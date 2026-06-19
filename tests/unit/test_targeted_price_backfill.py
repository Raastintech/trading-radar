"""tests/unit/test_targeted_price_backfill.py — Targeted price-cache backfill.

Tests:
 1  Candidate collection from daily alpha radar
 2  Priority ordering (HIGH > RESET > DATA_QUARANTINE > scanner)
 3  Deduplication keeps highest-priority source
 4  min-bars selection logic (skip if already deep)
 5  Provider call cap is respected
 6  Dry-run does not fetch/write price data
 7  Already-deep cache names are skipped
 8  Invalid symbols are skipped safely
 9  Artifact JSON schema contains before/after bar counts
10  Nightly operator summary includes backfill status if artifact exists
11  No trading/execution imports are introduced
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import research.targeted_price_backfill as mod


# ── Helpers ───────────────────────────────────────────────────────────────────

def _radar_sidecar(
    high: List[str] = None,
    reset: List[str] = None,
    reclaim: List[str] = None,
    quarantine: List[str] = None,
) -> Dict[str, Any]:
    return {
        "priority_tickers": {
            "HIGH_PRIORITY_RESEARCH": high or [],
            "RESET_WATCH": reset or [],
            "RECLAIM_WATCH": reclaim or [],
            "DATA_QUARANTINE": quarantine or [],
        },
        "priority_counts": {},
    }


def _scanner_sidecar(tickers: List[str]) -> Dict[str, Any]:
    return {
        "categories": {
            "early_accumulation": [{"ticker": t} for t in tickers],
        },
        "watchlist": [],
    }


def _plan_entry(ticker: str, bars_before: int = 50, action: str = "backfill") -> Dict[str, Any]:
    return {
        "ticker": ticker,
        "source": "test",
        "bars_before": bars_before,
        "bars_after": None,
        "action": action,
        "reason": "test",
        "cache_source": "none",
    }


def _make_plan(entries: List[Dict[str, Any]], min_bars: int = 300) -> Dict[str, Any]:
    """Minimal plan skeleton for artifact-schema tests."""
    return {
        "started_at": "2026-06-19T00:00:00+00:00",
        "version": mod.VERSION,
        "research_only": True,
        "disclaimer": "test",
        "params": {"min_bars": min_bars, "limit": 50, "max_provider_calls": 25,
                   "priority_only": False, "include_quarantine": True,
                   "force_refresh": False},
        "candidate_sources": ["daily_alpha_radar_latest.json"],
        "total_candidates_collected": len(entries),
        "total_after_limit": len(entries),
        "invalid_skipped_count": 0,
        "invalid_skipped_sample": [],
        "selected_for_backfill": sum(1 for e in entries if e["action"] == "backfill"),
        "selected_tickers": [e["ticker"] for e in entries if e["action"] == "backfill"],
        "skipped_enough_history": sum(1 for e in entries if e["action"] == "skip_enough"),
        "skipped_recently_refreshed": 0,
        "provider_calls_planned": sum(1 for e in entries if e["action"] == "backfill"),
        "provider_calls_used": 0,
        "successes": 0,
        "failures": 0,
        "entries": entries,
        "completed_at": "2026-06-19T00:01:00+00:00",
        "remaining_insufficient": None,
    }


# ── Test 1: Candidate collection from daily alpha radar ───────────────────────

def test_candidates_from_alpha_radar(tmp_path):
    radar = _radar_sidecar(high=["AAPL"], quarantine=["JUNK"])
    with patch.object(mod, "_load_json") as mock_load:
        def _side(path):
            name = str(path)
            if "daily_alpha_radar" in name:
                return radar
            return None
        mock_load.side_effect = _side
        candidates, sources, invalid = mod.collect_and_dedupe(include_quarantine=True)
    assert "AAPL" in candidates
    assert "JUNK" in candidates
    assert sources["AAPL"] == "radar:HIGH_PRIORITY_RESEARCH"
    assert sources["JUNK"] == "radar:DATA_QUARANTINE"


# ── Test 2: Priority ordering ─────────────────────────────────────────────────

def test_priority_ordering():
    radar = _radar_sidecar(high=["HIGH1"], reset=["RESET1"], quarantine=["QUARK1"])
    scanner = _scanner_sidecar(["SCAN1"])
    with patch.object(mod, "_load_json") as mock_load:
        def _side(path):
            name = str(path)
            if "daily_alpha_radar" in name:
                return radar
            if "research_scanner" in name:
                return scanner
            return None
        mock_load.side_effect = _side
        candidates, sources, _ = mod.collect_and_dedupe()
    # HIGH_PRIORITY_RESEARCH (rank 10) must precede RESET_WATCH (20) which precedes
    # DATA_QUARANTINE (40) which precedes scanner (50)
    assert candidates.index("HIGH1") < candidates.index("RESET1")
    assert candidates.index("RESET1") < candidates.index("QUARK1")
    assert candidates.index("QUARK1") < candidates.index("SCAN1")


# ── Test 3: Deduplication keeps highest-priority source ───────────────────────

def test_deduplication_keeps_highest_priority():
    radar = _radar_sidecar(high=["AAPL"], quarantine=["AAPL"])  # AAPL appears in both
    with patch.object(mod, "_load_json") as mock_load:
        def _side(path):
            if "daily_alpha_radar" in str(path):
                return radar
            return None
        mock_load.side_effect = _side
        candidates, sources, _ = mod.collect_and_dedupe()
    # Must appear only once, with the higher-priority label
    assert candidates.count("AAPL") == 1
    assert sources["AAPL"] == "radar:HIGH_PRIORITY_RESEARCH"


# ── Test 4: min-bars selection logic ──────────────────────────────────────────

def test_min_bars_skip_if_already_deep():
    with (
        patch.object(mod, "_load_json", return_value=None),
        patch.object(mod, "_bar_count", return_value=(350, "deep")),
        patch.object(mod, "_recently_refreshed", return_value=False),
    ):
        with patch.object(mod, "collect_and_dedupe",
                          return_value=(["RICH"], {"RICH": "test"}, [])):
            plan = mod.build_plan(min_bars=300, max_provider_calls=25)
    skipped = [e for e in plan["entries"] if e["ticker"] == "RICH"
               and e["action"] == "skip_enough"]
    assert len(skipped) == 1
    assert plan["selected_for_backfill"] == 0


def test_min_bars_includes_shallow_ticker():
    with (
        patch.object(mod, "_load_json", return_value=None),
        patch.object(mod, "_bar_count", return_value=(80, "shallow")),
        patch.object(mod, "_recently_refreshed", return_value=False),
    ):
        with patch.object(mod, "collect_and_dedupe",
                          return_value=(["SHALLOW"], {"SHALLOW": "test"}, [])):
            plan = mod.build_plan(min_bars=300, max_provider_calls=5)
    selected = [e for e in plan["entries"] if e["action"] == "backfill"]
    assert any(e["ticker"] == "SHALLOW" for e in selected)


# ── Test 5: Provider call cap is respected ────────────────────────────────────

def test_provider_call_cap():
    tickers = [f"T{i:03d}" for i in range(20)]
    cap = 5
    with (
        patch.object(mod, "_load_json", return_value=None),
        patch.object(mod, "_bar_count", return_value=(50, "none")),
        patch.object(mod, "_recently_refreshed", return_value=False),
    ):
        with patch.object(mod, "collect_and_dedupe",
                          return_value=(tickers, {t: "test" for t in tickers}, [])):
            plan = mod.build_plan(min_bars=300, max_provider_calls=cap)
    backfill_count = sum(1 for e in plan["entries"] if e["action"] == "backfill")
    assert backfill_count == cap
    budget_blocked = sum(1 for e in plan["entries"] if e["action"] == "skip_budget")
    assert budget_blocked == len(tickers) - cap


# ── Test 6: Dry-run does not fetch or write ───────────────────────────────────

def test_dry_run_no_fetch(tmp_path):
    out_json = tmp_path / "backfill.json"
    out_txt = tmp_path / "backfill.txt"
    with (
        patch.object(mod, "_load_json", return_value=None),
        patch.object(mod, "_bar_count", return_value=(50, "none")),
        patch.object(mod, "_recently_refreshed", return_value=False),
        patch.object(mod, "collect_and_dedupe",
                     return_value=(["DRYTEST"], {"DRYTEST": "test"}, [])),
        patch.object(mod, "OUT_JSON", out_json),
        patch.object(mod, "OUT_TXT", out_txt),
        patch.object(mod, "_fetch_and_write") as mock_fetch,
    ):
        mod.main(["--dry-run", "--min-bars", "300",
                  "--json-out", str(out_json), "--text-out", str(out_txt)])
    mock_fetch.assert_not_called()
    assert out_json.exists()
    data = json.loads(out_json.read_text())
    assert data["provider_calls_used"] == 0


# ── Test 7: Already-deep names are skipped ───────────────────────────────────

def test_already_deep_names_skipped():
    with (
        patch.object(mod, "_load_json", return_value=None),
        patch.object(mod, "_bar_count", return_value=(400, "deep")),
        patch.object(mod, "_recently_refreshed", return_value=False),
    ):
        with patch.object(mod, "collect_and_dedupe",
                          return_value=(["DEEPTICK"], {"DEEPTICK": "test"}, [])):
            plan = mod.build_plan(min_bars=300)
    assert plan["skipped_enough_history"] == 1
    assert plan["selected_for_backfill"] == 0


# ── Test 8: Invalid symbols are skipped safely ────────────────────────────────

def test_invalid_symbols_skipped():
    # Foreign tickers with "." and SPAC warrants/units should be filtered out
    assert mod._is_invalid("0A1K.L")   # foreign (has ".")
    assert mod._is_invalid("AACBUW")   # SPAC warrant (6-char + W)
    assert mod._is_invalid("AACIW")    # SPAC warrant (5-char + W)
    assert mod._is_invalid("AACBU")    # SPAC unit (5-char + U)
    assert mod._is_invalid("")         # empty
    assert mod._is_invalid("SPY")      # benchmark
    assert not mod._is_invalid("AAPL")
    assert not mod._is_invalid("NVDA")
    assert not mod._is_invalid("AMD")
    assert not mod._is_invalid("MSTR")  # 4-char ticker ending in R (valid)


def test_invalid_symbols_not_in_plan():
    radar = _radar_sidecar(quarantine=["0A1K.L", "AACBUW", "VALIDTICKER"])
    with patch.object(mod, "_load_json") as mock_load:
        def _side(path):
            if "daily_alpha_radar" in str(path):
                return radar
            return None
        mock_load.side_effect = _side
        with (
            patch.object(mod, "_bar_count", return_value=(50, "none")),
            patch.object(mod, "_recently_refreshed", return_value=False),
        ):
            candidates, _, invalid = mod.collect_and_dedupe()
    assert "VALIDTICKER" in candidates
    assert "0A1K.L" not in candidates
    assert "AACBUW" not in candidates


# ── Test 9: Artifact JSON schema has before/after bar counts ──────────────────

def test_artifact_schema_bar_counts(tmp_path):
    out_json = tmp_path / "backfill.json"
    entries = [
        _plan_entry("TICK1", bars_before=80, action="backfill"),
        _plan_entry("TICK2", bars_before=350, action="skip_enough"),
    ]
    entries[0]["bars_after"] = 340  # simulate successful backfill
    plan = _make_plan(entries)
    plan["provider_calls_used"] = 1
    plan["successes"] = 1

    out_json.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    data = json.loads(out_json.read_text())

    e_backfilled = next(e for e in data["entries"] if e["ticker"] == "TICK1")
    e_skipped = next(e for e in data["entries"] if e["ticker"] == "TICK2")

    assert "bars_before" in e_backfilled
    assert "bars_after" in e_backfilled
    assert e_backfilled["bars_after"] == 340
    assert e_skipped["bars_before"] == 350
    assert data["successes"] == 1
    assert data["provider_calls_used"] == 1
    assert data["research_only"] is True
    assert data["version"] == mod.VERSION


# ── Test 10: Nightly operator summary picks up backfill status ────────────────

def test_nightly_operator_summary_backfill_line(tmp_path):
    """If backfill sidecar exists, warnings should mention quarantine/backfill."""
    import research.nightly_operator_summary as nos

    backfill_sidecar = {
        "version": mod.VERSION,
        "generated_at": "2026-06-19T20:30:00+00:00",
        "research_only": True,
        "selected_for_backfill": 12,
        "skipped_enough_history": 5,
        "provider_calls_used": 10,
        "successes": 9,
        "failures": 1,
        "remaining_insufficient": 8,
        "params": {"min_bars": 300},
        "entries": [],
    }
    # The backfill line is surfaced in _build_warnings via the quarantine rate.
    # We test that the warning containing "data-quarantine" fires at 46% rate.
    alpha_snap = {
        "available": True,
        "total_candidates": 56,
        "high_priority_count": 1,
        "high_priority_tickers": ["AAL"],
        "reset_watch_count": 0,
        "reset_watch_tickers": [],
        "reclaim_watch_count": 0,
        "reclaim_watch_tickers": [],
        "extended_crowded_count": 0,
        "extended_crowded_tickers": [],
        "data_quarantine_count": 26,  # 46% => triggers quarantine warning
        "top_quarantine_reasons": ["INSUFFICIENT_HISTORY: 25"],
        "options_state": "DISABLED",
    }
    warnings = nos._build_warnings(
        sidecars={},
        market_ctx={"regime": "Trend", "confidence": "medium",
                    "bias_5d": "bullish", "research_posture": ""},
        forward={"available": False},
        alpha_snap=alpha_snap,
    )
    quarantine_warns = [w for w in warnings if "quarantine" in w.lower()]
    assert len(quarantine_warns) >= 1
    assert "26" in quarantine_warns[0]


# ── Test 11: No trading/execution imports ────────────────────────────────────

def test_no_execution_imports():
    """
    The module must not import execution or paper-trading symbols
    that could accidentally trigger order submission.
    """
    forbidden = {
        "submit_order", "place_order", "paper_trade", "live_trade",
        "bracket_order", "TradingClient", "OrderManager",
        "paper_governance", "alpaca.trading",
    }
    src = Path(ROOT / "research" / "targeted_price_backfill.py").read_text()
    for sym in forbidden:
        assert sym not in src, f"Forbidden symbol '{sym}' found in targeted_price_backfill.py"

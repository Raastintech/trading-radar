"""
tests/unit/test_nightly_operator_summary.py — Nightly Operator Summary tests.

Covers:
  - No forbidden strategy/trading language in any output
  - Research-only posture language
  - Correct section structure
  - Missing sidecar graceful degradation
  - JSON guardrails present
  - Markdown renders without errors
  - Dashboard Mode-1 strip no longer contains strategy abbreviation wording
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.nightly_operator_summary import (
    _build_alpha_snapshot,
    _build_best_names,
    _build_forward_evidence,
    _build_market_context,
    _build_next_actions,
    _build_overall_status,
    _build_warnings,
    _render_md,
    _research_posture,
    generate,
)

# ── Forbidden language constants ─────────────────────────────────────────────

FORBIDDEN_PATTERNS = [
    "Strat V",
    "V:favored",
    "S:allowed",
    "Sh:avoid",
    "SNIPER",
    "VOYAGER",
    "REMORA",
    "SHORT_A",
    "paper signal",
    "buy now",
    "sell now",
    "entry price",
    "stop loss",
    "position size",
    "place order",
    "submit order",
    "bracket order",
]


def _check_no_forbidden(text: str, label: str = "output") -> None:
    lower = text.lower()
    for pattern in FORBIDDEN_PATTERNS:
        assert pattern.lower() not in lower, (
            f"Forbidden pattern '{pattern}' found in {label}"
        )


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_forecast(regime: str = "Bull Continuation", conf: str = "medium") -> Dict[str, Any]:
    return {
        "built_at": "2026-06-17T05:00:00+00:00",
        "headline": {
            "current_regime": regime,
            "bias_5d": "constructive",
            "bias_10d": "constructive",
            "confidence": conf,
            "invalidation_breached": False,
        },
        "sector_rotation": {
            "rows": [
                {"sector": "XLK", "name": "Technology", "state": "Leading", "available": True,
                 "rs_5d_pct": 2.0, "rs_10d_pct": 3.0, "rs_20d_pct": 1.5, "above_ma50": True},
                {"sector": "XLE", "name": "Energy", "state": "Weakening", "available": True,
                 "rs_5d_pct": -1.0, "rs_10d_pct": -2.0, "rs_20d_pct": -0.5, "above_ma50": False},
            ],
            "leading": ["XLK"],
            "improving": [],
            "weakening": ["XLE"],
            "defensive": [],
        },
        "market_trend": {
            "SPY": {
                "return_20d_pct": 3.5,
                "above_ma50": True, "above_ma200": True,
            }
        },
        "strategy_favorability": {
            "VOYAGER": {"stance": "favored", "reason": "broad trend healthy"},
            "SNIPER_V6": {"stance": "allowed", "reason": "neutral context"},
            "SHORT_A": {"stance": "avoid", "reason": "broad bid hostile"},
            "ALPHA_DISCOVERY": {
                "stance": "selective",
                "reason": "avoid chasing extended names; favor pullback candidates",
            },
        },
    }


def _make_alpha_radar() -> Dict[str, Any]:
    return {
        "generated_at": "2026-06-17T05:08:00+00:00",
        "total_candidates": 54,
        "priority_counts": {
            "HIGH_PRIORITY_RESEARCH": 4,
            "RESET_WATCH": 3,
            "RECLAIM_WATCH": 1,
            "EXTENDED_CROWDED": 19,
            "DATA_QUARANTINE": 24,
        },
        # Authoritative per-label ticker lists (matching the counts exactly)
        "priority_tickers": {
            "HIGH_PRIORITY_RESEARCH": ["HP1", "HP2", "HP3", "HP4"],
            "RESET_WATCH": ["RW1", "RW2", "RW3"],
            "RECLAIM_WATCH": ["RC1"],
            "EXTENDED_CROWDED": ["EX" + str(i) for i in range(19)],
            "DATA_QUARANTINE": ["DQ" + str(i) for i in range(24)],
        },
        "quarantine_breakdown": {"INSUFFICIENT_HISTORY": 22, "DATA_QUARANTINE": 2},
        "options_coverage": {"state": "DISABLED"},
    }


def _make_scanner(n_high: int = 3) -> Dict[str, Any]:
    items = []
    for i in range(n_high):
        items.append({
            "ticker": f"TST{i}",
            "priority_label": "HIGH_PRIORITY_RESEARCH",
            "watchlist_label": "HIGH_PRIORITY_RESEARCH",
            "research_score": 90 - i,
            "sector": "Technology",
            "data_confidence": "HIGH",
            "trust_level": "HIGH",
            "why_appeared": "Rising RS and volume",
            "confirms_if": "RS sustains",
            "invalidates_if": "RS reverses",
            "no_trade_recommendation": True,
        })
    for i in range(2):
        items.append({
            "ticker": f"RST{i}",
            "priority_label": "RESET_WATCH",
            "watchlist_label": "RESET_WATCH",
            "research_score": 60 - i,
            "sector": "Healthcare",
            "data_confidence": "MEDIUM",
            "trust_level": "MEDIUM",
            "why_appeared": "Large drawdown with stabilization",
            "confirms_if": "Reclaims 50d MA",
            "invalidates_if": "New lows",
            "no_trade_recommendation": True,
        })
    return {"watchlist": items}


def _make_forward() -> Dict[str, Any]:
    return {
        "generated_at": "2026-06-17T05:08:00+00:00",
        "total_history_entries": 172,
        "new_entries_today": 5,
        "updated_entries_today": 2,
        "overall": {
            "matured_entries": 0,
            "sample_status": "TOO_EARLY",
            "verdict": "NEED_MORE_DATA",
            "n_with_spy_baseline": None,
            "n_with_qqq_baseline": None,
        },
        "benchmark_readiness": {
            "spy_available": True,
            "qqq_available": True,
            "entries_with_spy_10d": 0,
            "entries_with_qqq_10d": 0,
            "entries_with_sector_10d": 0,
            "entries_with_sector_etf": 126,
        },
    }


def _make_sidecars() -> Dict[str, Any]:
    return {
        "forecast": _make_forecast(),
        "alpha_radar": _make_alpha_radar(),
        "scanner": _make_scanner(),
        "forward": _make_forward(),
        "scanner_truth": {
            "winner_recall_pct": 1.9,
            "late_detection": 6,
            "blind_misses": 304,
            "main_failure": "UNIVERSE_MISS",
            "best_simple_baseline_recall_pct": 20.0,
        },
        "provider_audit": None,
        "social": None,
    }


# ── 1. Research posture translator ────────────────────────────────────────────

def test_posture_no_strategy_abbreviations():
    forecast = _make_forecast("Bull Continuation")
    posture = _research_posture(forecast)
    _check_no_forbidden(posture, "research_posture")
    assert "human review" in posture.lower()


def test_posture_bull_continuation_selective():
    fc = _make_forecast("Bull Continuation")
    posture = _research_posture(fc)
    assert "selective" in posture.lower() or "avoid extended" in posture.lower()


def test_posture_bull_pullback_constructive():
    fc = _make_forecast("Bull Pullback / Buy-the-Dip")
    # ALPHA_DISCOVERY stance for pullback = favored
    fc["strategy_favorability"]["ALPHA_DISCOVERY"]["stance"] = "favored"
    posture = _research_posture(fc)
    assert "constructive" in posture.lower()


def test_posture_risk_off_defensive():
    fc = _make_forecast("Risk-Off")
    fc["strategy_favorability"]["ALPHA_DISCOVERY"]["stance"] = "selective"
    fc["strategy_favorability"]["ALPHA_DISCOVERY"]["reason"] = "stalk only; do not promote ideas during stress"
    posture = _research_posture(fc)
    assert "stalking" in posture.lower() or "stalk" in posture.lower()


def test_posture_missing_forecast():
    posture = _research_posture(None)
    assert "unavailable" in posture.lower()
    _check_no_forbidden(posture, "posture_missing")


# ── 2. Section builders ───────────────────────────────────────────────────────

def test_market_context_no_forbidden_language():
    ctx = _build_market_context(_make_forecast())
    # Join all string values and check
    text = json.dumps(ctx)
    _check_no_forbidden(text, "market_context")


def test_market_context_has_required_fields():
    ctx = _build_market_context(_make_forecast())
    assert "regime" in ctx
    assert "confidence" in ctx
    assert "bias_5d" in ctx
    assert "bias_10d" in ctx
    assert "bias_30d" in ctx
    assert "leading_sectors" in ctx
    assert "weak_sectors" in ctx
    assert "research_posture" in ctx


def test_market_context_missing_forecast():
    ctx = _build_market_context(None)
    assert ctx["regime"] == "UNKNOWN"
    _check_no_forbidden(json.dumps(ctx), "market_context_missing")


def test_alpha_snapshot_no_forbidden_language():
    snap = _build_alpha_snapshot(_make_alpha_radar(), _make_scanner())
    _check_no_forbidden(json.dumps(snap), "alpha_snapshot")


def test_alpha_snapshot_counts():
    snap = _build_alpha_snapshot(_make_alpha_radar(), _make_scanner())
    assert snap["available"] is True
    assert snap["total_candidates"] == 54
    # HIGH_PRIORITY count from alpha_radar
    assert snap["high_priority_count"] == 4


def test_alpha_snapshot_missing():
    snap = _build_alpha_snapshot(None, None)
    assert snap["available"] is False


def test_best_names_no_forbidden_language():
    snap = _build_alpha_snapshot(_make_alpha_radar(), _make_scanner(5))
    names = _build_best_names(snap, _make_scanner(5))
    text = json.dumps(names)
    _check_no_forbidden(text, "best_names")


def test_best_names_max_10():
    snap = _build_alpha_snapshot(_make_alpha_radar(), _make_scanner(15))
    names = _build_best_names(snap, _make_scanner(15))
    assert len(names.get("primary", [])) <= 10


def test_best_names_missing_scanner():
    names = _build_best_names(None, None)
    assert names == {"primary": [], "secondary": []}


def test_forward_evidence_no_forbidden_language():
    fwd = _build_forward_evidence(_make_forward())
    _check_no_forbidden(json.dumps(fwd), "forward_evidence")


def test_forward_evidence_fields():
    fwd = _build_forward_evidence(_make_forward())
    assert fwd["available"] is True
    assert fwd["total_entries"] == 172
    assert fwd["verdict"] == "NEED_MORE_DATA"
    assert fwd["alpha_proven"] is False


def test_forward_evidence_missing():
    fwd = _build_forward_evidence(None)
    assert fwd["available"] is False


# ── 3. Warnings builder ───────────────────────────────────────────────────────

def test_warnings_scanner_recall_low():
    sidecars = _make_sidecars()
    ctx = _build_market_context(sidecars["forecast"])
    snap = _build_alpha_snapshot(sidecars["alpha_radar"], sidecars["scanner"])
    fwd = _build_forward_evidence(sidecars["forward"])
    warns = _build_warnings(sidecars, ctx, fwd, snap)
    recall_warn = [w for w in warns if "recall" in w.lower()]
    assert len(recall_warn) >= 1


def test_warnings_forward_immature():
    sidecars = _make_sidecars()
    ctx = _build_market_context(sidecars["forecast"])
    snap = _build_alpha_snapshot(sidecars["alpha_radar"], sidecars["scanner"])
    fwd = _build_forward_evidence(sidecars["forward"])
    warns = _build_warnings(sidecars, ctx, fwd, snap)
    mature_warn = [w for w in warns if "immature" in w.lower() or "forward" in w.lower()]
    assert len(mature_warn) >= 1


def test_warnings_capped_at_8():
    sidecars = _make_sidecars()
    ctx = _build_market_context(sidecars["forecast"])
    snap = _build_alpha_snapshot(sidecars["alpha_radar"], sidecars["scanner"])
    fwd = _build_forward_evidence(sidecars["forward"])
    warns = _build_warnings(sidecars, ctx, fwd, snap)
    assert len(warns) <= 8


def test_warnings_no_forbidden_language():
    sidecars = _make_sidecars()
    ctx = _build_market_context(sidecars["forecast"])
    snap = _build_alpha_snapshot(sidecars["alpha_radar"], sidecars["scanner"])
    fwd = _build_forward_evidence(sidecars["forward"])
    warns = _build_warnings(sidecars, ctx, fwd, snap)
    _check_no_forbidden(" ".join(warns), "warnings")


# ── 4. Markdown renderer ──────────────────────────────────────────────────────

def _render_with_fixtures() -> str:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    sidecars = _make_sidecars()
    overall = _build_overall_status(sidecars, now)
    ctx = _build_market_context(sidecars["forecast"])
    snap = _build_alpha_snapshot(sidecars["alpha_radar"], sidecars["scanner"])
    names = _build_best_names(snap, sidecars["scanner"])
    fwd = _build_forward_evidence(sidecars["forward"])
    warns = _build_warnings(sidecars, ctx, fwd, snap)
    actions = _build_next_actions(overall, ctx, fwd, warns, snap)
    return _render_md(now, overall, ctx, snap, names, fwd, warns, actions)


def test_markdown_no_forbidden_language():
    md = _render_with_fixtures()
    _check_no_forbidden(md, "markdown_output")


def test_markdown_has_required_sections():
    md = _render_with_fixtures()
    assert "## 1. Overall Status" in md
    assert "## 2. Market Context" in md
    assert "## 3. Alpha Radar Snapshot" in md
    assert "## 4. Best Research Names to Review" in md
    assert "## 5. Forward Evidence" in md
    assert "## 6. Biggest Warnings" in md
    assert "## 7. Next Operator Actions" in md


def test_markdown_research_only_banner():
    md = _render_with_fixtures()
    assert "RESEARCH_ONLY_MODE" in md
    assert "NO AUTO TRADING" in md


def test_markdown_has_no_trade_statement():
    md = _render_with_fixtures()
    # Must include a statement that no trade approval is made
    assert any(phrase in md.lower() for phrase in [
        "no trade approval",
        "no auto trading",
        "human review",
    ])


def test_markdown_forward_verdict_present():
    md = _render_with_fixtures()
    assert "NEED_MORE_DATA" in md


# ── 5. JSON payload structure ─────────────────────────────────────────────────

def test_json_guardrails():
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    sidecars = _make_sidecars()
    overall = _build_overall_status(sidecars, now)
    ctx = _build_market_context(sidecars["forecast"])
    snap = _build_alpha_snapshot(sidecars["alpha_radar"], sidecars["scanner"])
    names = _build_best_names(snap, sidecars["scanner"])
    fwd = _build_forward_evidence(sidecars["forward"])
    warns = _build_warnings(sidecars, ctx, fwd, snap)
    actions = _build_next_actions(overall, ctx, fwd, warns, snap)

    payload = {
        "version": "NIGHTLY_OPERATOR_SUMMARY_V1",
        "system_mode": "RESEARCH_ONLY",
        "research_only": True,
        "guardrails": {
            "no_trade_recommendation": True,
            "no_buy_sell": True,
            "no_entry_stop_target": True,
            "no_paper_signal": True,
            "no_strategy_abbreviations": True,
            "no_alpaca_interaction": True,
        },
        "overall_status": overall,
        "market_context": ctx,
        "alpha_snapshot": snap,
        "best_research_names": names,
        "forward_evidence": fwd,
        "warnings": warns,
        "next_actions": actions,
    }

    assert payload["guardrails"]["no_trade_recommendation"] is True
    assert payload["guardrails"]["no_strategy_abbreviations"] is True
    assert payload["system_mode"] == "RESEARCH_ONLY"
    assert payload["research_only"] is True

    # Full JSON dump should contain no forbidden language
    _check_no_forbidden(json.dumps(payload), "json_payload")


# ── 6. Dashboard Mode-1 strip no longer contains strategy abbreviations ───────

def test_dashboard_mode1_strip_no_strategy_abbreviations():
    """Verify market_forecast_strip removed V:/S:/Sh: pattern."""
    import ast

    dashboard_path = ROOT / "dashboards" / "gem_trader_hq.py"
    source = dashboard_path.read_text()

    # The function market_forecast_strip should NOT contain the old pattern
    # Find the function boundaries
    fn_start = source.find("def market_forecast_strip(")
    assert fn_start != -1, "market_forecast_strip not found"

    # Find next top-level def after it
    fn_end = source.find("\n    @staticmethod\n", fn_start + 1)
    if fn_end == -1:
        fn_end = fn_start + 5000

    strip_source = source[fn_start:fn_end]

    # Must NOT contain the old strategy abbreviation pattern
    assert "V:{v_st}" not in strip_source, "Old V:{v_st} pattern still present"
    assert "S:{s_st}" not in strip_source, "Old S:{s_st} pattern still present"
    assert "Sh:{sh_st}" not in strip_source, "Old Sh:{sh_st} pattern still present"
    assert '"Strat "' not in strip_source, "Old 'Strat ' label still present"

    # Must contain research posture language
    assert "Research posture" in strip_source or "research posture" in strip_source.lower(), (
        "Research posture label not found in market_forecast_strip"
    )


# ── 7. Overall status builder ─────────────────────────────────────────────────

def test_overall_status_pass_when_all_present():
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    sidecars = _make_sidecars()
    overall = _build_overall_status(sidecars, now)
    assert overall["overall"] == "PASS"
    assert overall["research_only_safety"] == "ACTIVE"


def test_overall_status_warn_when_missing():
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    sidecars = _make_sidecars()
    sidecars["forecast"] = None
    sidecars["alpha_radar"] = None
    overall = _build_overall_status(sidecars, now)
    assert overall["overall"] == "WARN"
    assert len(overall["components_missing"]) >= 1


# ── 8. Next actions builder ───────────────────────────────────────────────────

def test_next_actions_no_forbidden_language():
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    sidecars = _make_sidecars()
    overall = _build_overall_status(sidecars, now)
    ctx = _build_market_context(sidecars["forecast"])
    snap = _build_alpha_snapshot(sidecars["alpha_radar"], sidecars["scanner"])
    fwd = _build_forward_evidence(sidecars["forward"])
    warns = _build_warnings(sidecars, ctx, fwd, snap)
    actions = _build_next_actions(overall, ctx, fwd, warns, snap)
    _check_no_forbidden(" ".join(actions), "next_actions")


def test_next_actions_immature_recommendation():
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    sidecars = _make_sidecars()
    overall = _build_overall_status(sidecars, now)
    ctx = _build_market_context(sidecars["forecast"])
    snap = _build_alpha_snapshot(sidecars["alpha_radar"], sidecars["scanner"])
    fwd = _build_forward_evidence(sidecars["forward"])
    warns = _build_warnings(sidecars, ctx, fwd, snap)
    actions = _build_next_actions(overall, ctx, fwd, warns, snap)
    # Should recommend not changing scoring while immature
    scoring_rec = [a for a in actions if "scoring" in a.lower() or "forward" in a.lower()]
    assert len(scoring_rec) >= 1


def test_next_actions_capped():
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    sidecars = _make_sidecars()
    overall = _build_overall_status(sidecars, now)
    ctx = _build_market_context(sidecars["forecast"])
    snap = _build_alpha_snapshot(sidecars["alpha_radar"], sidecars["scanner"])
    fwd = _build_forward_evidence(sidecars["forward"])
    warns = _build_warnings(sidecars, ctx, fwd, snap)
    actions = _build_next_actions(overall, ctx, fwd, warns, snap)
    assert len(actions) <= 5


# ── 9. Consistency tests (requirements §1–4) ──────────────────────────────────

def test_alpha_snapshot_hp_count_matches_tickers():
    """HIGH_PRIORITY_RESEARCH count must equal the number of tickers in hp_tickers."""
    snap = _build_alpha_snapshot(_make_alpha_radar(), _make_scanner())
    assert snap["high_priority_count"] == 4
    assert len(snap["high_priority_tickers"]) == 4, (
        f"hp_tickers has {len(snap['high_priority_tickers'])} items but count=4"
    )


def test_alpha_snapshot_no_cross_bucket_tickers():
    """Each priority bucket's ticker list must be disjoint from all other buckets."""
    snap = _build_alpha_snapshot(_make_alpha_radar(), _make_scanner())
    hp = set(snap["high_priority_tickers"])
    rw = set(snap["reset_watch_tickers"])
    rc = set(snap["reclaim_watch_tickers"])
    ex = set(snap["extended_crowded_tickers"])
    assert hp.isdisjoint(rw), f"HP ∩ RESET: {hp & rw}"
    assert hp.isdisjoint(ex), f"HP ∩ EXTENDED: {hp & ex}"
    assert rw.isdisjoint(ex), f"RESET ∩ EXTENDED: {rw & ex}"
    assert hp.isdisjoint(rc), f"HP ∩ RECLAIM: {hp & rc}"


def test_next_actions_review_count_matches_listed_tickers():
    """'Review N high-priority...' must never list more tickers than N."""
    import re
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    sidecars = _make_sidecars()
    overall = _build_overall_status(sidecars, now)
    ctx = _build_market_context(sidecars["forecast"])
    snap = _build_alpha_snapshot(sidecars["alpha_radar"], sidecars["scanner"])
    fwd = _build_forward_evidence(sidecars["forward"])
    warns = _build_warnings(sidecars, ctx, fwd, snap)
    actions = _build_next_actions(overall, ctx, fwd, warns, snap)

    review_actions = [a for a in actions if "high-priority research name" in a.lower()]
    assert review_actions, "Expected a 'high-priority research name' action"
    action = review_actions[0]

    m = re.search(r"Review (\d+) high-priority", action)
    assert m, f"Unexpected action format: {action}"
    stated_count = int(m.group(1))

    ticker_part = action.split("manually: ", 1)[1] if "manually: " in action else ""
    # Count listed tickers (those NOT starting with "+")
    listed_tickers = [
        t.strip() for t in ticker_part.split(",")
        if t.strip() and not t.strip().startswith("+") and "more" not in t.strip()
    ]
    assert len(listed_tickers) <= stated_count, (
        f"Listed {len(listed_tickers)} tickers but stated count={stated_count}: {action}"
    )


def test_benchmark_wording_loaded_not_broken():
    """When benchmark parquets are loaded but no entries have matured, wording is LOADED, not PARTIAL(0,0)."""
    fwd_data = _make_forward()  # spy_available=True, qqq_available=True, spy_ready=0, qqq_ready=0
    fwd = _build_forward_evidence(fwd_data)

    assert fwd["benchmark_readiness"] == "LOADED", (
        f"Expected LOADED when benchmarks loaded but no matured entries, got: {fwd['benchmark_readiness']}"
    )
    detail = fwd.get("benchmark_detail", "")
    assert "pending maturity" in detail.lower() or "loaded" in detail.lower(), (
        f"benchmark_detail should indicate pending maturity, got: {detail!r}"
    )

    md = _render_with_fixtures()
    assert "PARTIAL (SPY: 0" not in md, "Markdown falsely implies partial coverage with 0 entries"
    assert "benchmark series not yet available" not in md, (
        "Markdown wrongly says benchmark series unavailable when they are loaded"
    )

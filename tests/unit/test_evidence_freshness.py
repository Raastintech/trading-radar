"""tests/unit/test_evidence_freshness.py — Mode-3 Evidence Freshness sourcing.

Covers the decision-grade source mapping:
  - daily bars resolve from the price cache (current / stale / missing) with a
    reason on every non-current state — never a bare 'unknown'
  - universe age resolves from the snapshot (age + count); missing → 'missing'
    with the expected path, never '?m'
  - the legacy daemon scan field is honestly labelled 'legacy scanner'
  - the panel + helpers are cache-only (no providers, no DB writes, no
    execution/governance imports)
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from rich.console import Console

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import core.evidence_freshness as ef  # noqa: E402
from dashboards.gem_trader_hq import PB  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
SAT = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)   # weekend → expected = Fri 06-05


def _write_spy(dirp: Path, last_day: str):
    dirp.mkdir(parents=True, exist_ok=True)
    idx = pd.to_datetime([f"2026-06-0{d}" for d in (1, 2, 3, 4, 5)])
    # truncate so the last bar is `last_day`
    idx = idx[idx <= pd.Timestamp(last_day)]
    df = pd.DataFrame({"close": [100.0] * len(idx)}, index=idx)
    df.to_parquet(dirp / "SPY.parquet")


# ── daily bars ───────────────────────────────────────────────────────────────

def test_daily_bars_current_resolves_from_cache(tmp_path):
    _write_spy(tmp_path / "prices", "2026-06-05")
    r = ef.price_cache_bar_status(tmp_path / "prices", tmp_path / "deep", now=SAT)
    assert r["status"] == "current"
    assert r["latest_bar"] == "2026-06-05"
    assert r["expected"] == "2026-06-05"


def test_daily_bars_stale_includes_reason(tmp_path):
    _write_spy(tmp_path / "prices", "2026-06-03")
    r = ef.price_cache_bar_status(tmp_path / "prices", tmp_path / "deep", now=SAT)
    assert r["status"] == "stale"
    assert r["trading_days_behind"] == 2          # Thu + Fri behind
    assert r["reason"] and "2026-06-03" in r["reason"]


def test_daily_bars_missing_includes_reason(tmp_path):
    r = ef.price_cache_bar_status(tmp_path / "prices", tmp_path / "deep", now=SAT)
    assert r["status"] == "missing"
    assert r["reason"] and "SPY" in r["reason"]
    assert r["source"].endswith("SPY.parquet")


# ── universe ─────────────────────────────────────────────────────────────────

def test_universe_age_resolves_from_artifact(tmp_path):
    snap = tmp_path / "universe_snapshot_latest.json"
    snap.write_text(json.dumps({
        "generated_at": "2026-06-05T19:34:05+00:00",
        "summary": {"base_universe_size": 1000, "fallback_used": False},
    }))
    r = ef.universe_artifact_meta(snap, now=SAT)
    assert r["status"] == "current"          # built Fri == latest session
    assert r["count"] == 1000
    assert r["age_seconds"] is not None and r["age_seconds"] > 0
    assert r["exists"] is True


def test_universe_missing_shows_missing_not_question_mark(tmp_path):
    r = ef.universe_artifact_meta(tmp_path / "nope.json", now=SAT)
    assert r["status"] == "missing"
    assert r["exists"] is False
    assert r["count"] is None
    assert "universe_snapshot_latest.json" in r["source"]
    assert r["reason"] and "not found" in r["reason"]


def test_universe_stale_when_older_than_latest_session(tmp_path):
    snap = tmp_path / "u.json"
    snap.write_text(json.dumps({"generated_at": "2026-06-01T19:00:00+00:00",
                                "summary": {"base_universe_size": 900}}))
    r = ef.universe_artifact_meta(snap, now=SAT)
    assert r["status"] == "stale"
    assert r["reason"] and "2026-06-05" in r["reason"]


# ── trading-day helper ───────────────────────────────────────────────────────

def test_latest_completed_trading_day_weekend():
    assert ef.latest_completed_trading_day(SAT).isoformat() == "2026-06-05"  # Friday


# ── panel rendering ──────────────────────────────────────────────────────────

class _Stub:
    def __init__(self, **v: Any):
        self._v = v

    def get(self, k, d=None):
        return self._v.get(k, d)


def _render(panel) -> str:
    c = Console(record=True, width=80, force_terminal=False, color_system=None)
    c.print(panel)
    return c.export_text()


def test_panel_resolves_bars_and_labels_legacy_scanner():
    data = _Stub(
        price_cache_meta={"status": "current", "latest_bar": "2026-06-05",
                          "expected": "2026-06-05", "source": "cache/prices/SPY.parquet"},
        universe_meta={"status": "current", "age_seconds": 119000, "count": 1000,
                       "fallback_used": False, "source": "cache/universe/universe_snapshot_latest.json"},
        scan_results={"last_cycle_ts": "2026-05-29T20:14:00+00:00"},
        alpha_discovery={"_age_short": "2h"},
        scanner_truth_summary={"_missing": False, "_age_seconds": 9000, "winner_recall_pct": 1.1},
        recall_repair_shadow_forward={"_missing": False, "_age_seconds": 1500, "verdict": "NEED_MORE_DATA"},
        market_forecast={"_age_short": "2h", "_missing": False, "_stale": False},
        evidence_status={"last_success_at": "2026-06-06T22:00:00+00:00",
                         "scoreboard_mtime": "2026-06-06T22:00:00+00:00", "ok": True},
        universe_snap={},
    )
    out = _render(PB.evidence_freshness(data))
    assert "daily bars    current · latest 2026-06-05" in out
    assert "1000 tickers" in out
    assert "legacy scanner" in out and "stale" in out
    assert "?m" not in out                       # no unexplained universe age
    assert "daily bars current  unknown" not in out  # old bug string gone
    assert "scanner last" not in out             # relabelled


def test_panel_daily_bars_unknown_has_reason():
    data = _Stub(price_cache_meta={"status": "unknown", "reason": "probe error: ValueError"},
                 universe_meta={"status": "missing",
                                "source": "cache/universe/universe_snapshot_latest.json",
                                "reason": "expected artifact not found"},
                 scan_results={}, evidence_status={})
    out = _render(PB.evidence_freshness(data))
    assert "daily bars    unknown · probe error: ValueError" in out
    assert "universe      missing · cache/universe/universe_snapshot_latest.json" in out


# ── cache-only / no forbidden imports ────────────────────────────────────────

def test_helpers_and_audit_are_cache_only():
    for rel in ("core/evidence_freshness.py", "research/evidence_freshness_audit.py"):
        src = (REPO / rel).read_text(encoding="utf-8")
        for token in ("get_fmp", "get_alpaca", "requests.", "httpx",
                      "execution.order_manager", "execution.paper_governance",
                      "submit_market_order", "submit_limit_order",
                      "INSERT", "UPDATE ", "DELETE ", "import core.config"):
            assert token not in src, f"{rel} must stay cache-only/read-only: found {token!r}"
        # read-only DB access only (immutable connection).
        if "sqlite3" in src:
            assert "mode=ro" in src, f"{rel} must open the DB read-only"

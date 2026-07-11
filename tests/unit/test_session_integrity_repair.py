"""
tests/unit/test_session_integrity_repair.py

P0/P0-B proofs for the 2026-07-10 pipeline freshness repair:

  1.  Market-session date (exchange calendar) is the validity rule — not
      calendar stale-days.
  2.  Stock and benchmark bar dates must match before RS math sees a frame.
  3.  A remaining mismatch is excluded and reported as SESSION_MISMATCH.
  4.  A mixed-session scan cannot claim full freshness (BLOCKED/DEGRADED).
  5.  Frozen-snapshot consumers fail closed with UNAVAILABLE_STALE_SOURCE.
  10. Candidate-selection thresholds and score formulas are unchanged.
  11. Tactical/Swing/Long-Term routing is unchanged.
  12. Research-only invariants hold for every new/modified module.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List
from unittest.mock import patch
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import os
os.environ.setdefault("ALPACA_API_KEY", "offline")
os.environ.setdefault("ALPACA_SECRET_KEY", "offline")
os.environ.setdefault("FMP_API_KEY", "offline")
os.environ.setdefault("ALPACA_PAPER", "true")
os.environ.setdefault("PAPER_TRADING", "true")
os.environ.setdefault("FMP_BASE_URL", "https://financialmodelingprep.com/stable")

from core.market_session import (
    required_market_session, sessions_behind, source_staleness,
)

ET = ZoneInfo("America/New_York")


def _mk_parquet(tmp: Path, sym: str, end: date, bars: int = 260,
                price: float = 50.0, vol: float = 1_000_000.0) -> Path:
    idx = pd.date_range(end=pd.Timestamp(end), periods=bars, freq="B")
    prices = np.full(bars, price)
    df = pd.DataFrame({
        "open": prices, "high": prices * 1.01, "low": prices * 0.99,
        "close": prices, "volume": np.full(bars, vol),
    }, index=idx)
    path = tmp / f"{sym}.parquet"
    df.to_parquet(path)
    return path


# ── Proof 1: exchange-calendar session rule, not calendar age ────────────────


def test_required_session_uses_exchange_calendar_not_calendar_days():
    # Friday 2026-07-10 premarket: required session is Thursday 7/09.
    premkt = datetime(2026, 7, 10, 8, 0, tzinfo=ET)
    assert required_market_session(premkt) == date(2026, 7, 9)
    # Monday 2026-07-06 premarket (Jul 3 observed holiday): Thursday 7/02 —
    # a pure calendar-days rule can never produce this.
    holiday_mon = datetime(2026, 7, 6, 8, 0, tzinfo=ET)
    assert required_market_session(holiday_mon) == date(2026, 7, 2)
    # After the close + publish buffer, today qualifies.
    nightly = datetime(2026, 7, 10, 20, 30, tzinfo=ET)
    assert required_market_session(nightly) == date(2026, 7, 10)


def test_one_session_behind_is_stale_even_if_calendar_fresh():
    """The old --stale-days 2 rule called a Wednesday bar 'fresh' on Friday
    premarket.  The session rule counts it one session behind."""
    friday_premkt = datetime(2026, 7, 10, 8, 0, tzinfo=ET)
    assert sessions_behind(date(2026, 7, 8), friday_premkt) == 1  # stale
    assert sessions_behind(date(2026, 7, 9), friday_premkt) == 0  # aligned
    assert sessions_behind(None, friday_premkt) is None


def test_refresher_plans_by_session_not_calendar_age(tmp_path):
    import research.refresh_universe_prices as rup

    required = "2026-07-09"
    # ALIGNED ends at the required session; LAGGED one session earlier —
    # only 1 calendar day apart, so a calendar rule would treat them alike.
    _mk_parquet(tmp_path, "ALIGNED", end=date(2026, 7, 9))
    _mk_parquet(tmp_path, "LAGGED", end=date(2026, 7, 8))
    for b in ("SPY", "QQQ", "IWM"):
        _mk_parquet(tmp_path, b, end=date(2026, 7, 9))
    build = {"universe_full": [{"ticker": "ALIGNED"}, {"ticker": "LAGGED"}]}
    build_json = tmp_path / "build.json"
    build_json.write_text(json.dumps(build))

    with (
        patch.object(rup, "PRICE_DIR", tmp_path),
        patch.object(rup, "UNIVERSE_BUILD_JSON", build_json),
        patch.object(rup, "OUT_JSON", tmp_path / "out.json"),
        patch.object(rup, "OUT_TXT", tmp_path / "out.txt"),
        patch.object(rup, "confirmed_dead", lambda: set()),
    ):
        payload = rup.run(max_calls=100, execute=False, session_override=required)

    assert payload["required_market_session"] == required
    stale_tickers = {m["ticker"] for m in payload["session_mismatch_sample"]}
    assert "LAGGED" in stale_tickers
    assert "ALIGNED" not in stale_tickers
    assert payload["benchmark_session"]["SPY"] == required
    assert payload["benchmarks_aligned"] is True
    # spec metric names present
    for key in ("same_session_ready", "refresh_attempted", "refresh_succeeded",
                "session_mismatch_excluded", "benchmark_session"):
        assert key in payload


def test_refresher_budget_shortfall_is_explicit_not_silent(tmp_path):
    import research.refresh_universe_prices as rup

    import research.refresh_universe_prices as rup_mod
    for i in range(6):
        _mk_parquet(tmp_path, f"T{i}", end=date(2026, 7, 8))  # all lagged
    for b in rup_mod.ALWAYS_INCLUDE:  # benchmarks + sector ETFs aligned
        _mk_parquet(tmp_path, b, end=date(2026, 7, 9))
    build_json = tmp_path / "build.json"
    build_json.write_text(json.dumps(
        {"universe_full": [{"ticker": f"T{i}"} for i in range(6)]}))

    with (
        patch.object(rup, "PRICE_DIR", tmp_path),
        patch.object(rup, "UNIVERSE_BUILD_JSON", build_json),
        patch.object(rup, "OUT_JSON", tmp_path / "out.json"),
        patch.object(rup, "OUT_TXT", tmp_path / "out.txt"),
        patch.object(rup, "confirmed_dead", lambda: set()),
    ):
        payload = rup.run(max_calls=3, execute=False, session_override="2026-07-09")

    assert payload["truncated_by_budget"] == 3
    assert payload["coverage_shortfall"] is True
    # the un-refreshed remainder is reported, not silently called fresh
    assert payload["session_mismatch_excluded"] >= 3


def test_refresher_surfaces_unconfirmed_fmp_budget(tmp_path):
    import research.refresh_universe_prices as rup
    with (
        patch.object(rup, "PRICE_DIR", tmp_path),
        patch.object(rup, "UNIVERSE_BUILD_JSON", tmp_path / "missing.json"),
        patch.object(rup, "OUT_JSON", tmp_path / "out.json"),
        patch.object(rup, "OUT_TXT", tmp_path / "out.txt"),
        patch.object(rup, "confirmed_dead", lambda: set()),
    ):
        payload = rup.run(max_calls=10, execute=False, session_override="2026-07-09")
    status = payload["fmp_budget"]["budget_cap_status"]
    assert "UNCONFIRMED" in status or "configured" == status


# ── Proofs 2+3: stock/benchmark alignment gate + SESSION_MISMATCH ────────────


def test_scanner_frame_gate_blocks_lagged_ticker_before_rs(tmp_path):
    import research.research_scanner as rs

    _mk_parquet(tmp_path, "LAGGED", end=date(2026, 7, 8))
    _mk_parquet(tmp_path, "ALIGNED", end=date(2026, 7, 9))
    with (
        patch.object(rs, "PRICE_DIR", tmp_path),
        patch.object(rs, "DEEP_PRICE_DIR", tmp_path / "_deep"),
        patch.object(rs, "_REQUIRED_SESSION", date(2026, 7, 9)),
    ):
        rs._SESSION_MISMATCH_SKIPPED.clear()
        assert rs._load_cached_frame("LAGGED") is None
        assert rs._load_cached_frame("ALIGNED") is not None
        assert "LAGGED" in rs._SESSION_MISMATCH_SKIPPED
    rs._SESSION_MISMATCH_SKIPPED.clear()


def test_scan_integrity_reports_session_mismatch_exclusions(tmp_path):
    import research.research_scanner as rs

    for b in ("SPY", "QQQ", "IWM"):
        _mk_parquet(tmp_path, b, end=date(2026, 7, 9))
    with (
        patch.object(rs, "PRICE_DIR", tmp_path),
        patch.object(rs, "DEEP_PRICE_DIR", tmp_path / "_deep"),
        patch.object(rs, "_REQUIRED_SESSION", date(2026, 7, 9)),
    ):
        rs._SESSION_MISMATCH_SKIPPED.clear()
        rs._SESSION_MISMATCH_SKIPPED.update({"LAGGED1", "LAGGED2"})
        integrity = rs._scan_integrity(universe_size=100)
    rs._SESSION_MISMATCH_SKIPPED.clear()

    assert integrity["readiness"] == "DEGRADED"
    assert integrity["session_mismatch_excluded"] == 2
    assert integrity["same_session_coverage_pct"] == 98.0
    assert integrity["benchmarks_aligned"] is True


# ── Proof 4: mixed-session scan cannot claim full freshness ──────────────────


def test_benchmark_mismatch_blocks_scan(tmp_path):
    import research.research_scanner as rs

    _mk_parquet(tmp_path, "SPY", end=date(2026, 7, 8))   # benchmark lagged!
    _mk_parquet(tmp_path, "QQQ", end=date(2026, 7, 9))
    _mk_parquet(tmp_path, "IWM", end=date(2026, 7, 9))
    with (
        patch.object(rs, "PRICE_DIR", tmp_path),
        patch.object(rs, "DEEP_PRICE_DIR", tmp_path / "_deep"),
        patch.object(rs, "_REQUIRED_SESSION", date(2026, 7, 9)),
    ):
        integrity = rs._scan_integrity(universe_size=100)
    assert integrity["readiness"] == "BLOCKED"
    assert integrity["benchmarks_aligned"] is False


# ── Proof 5: frozen-snapshot consumers fail closed ───────────────────────────


def _stale_snapshot(tmp: Path) -> Path:
    p = tmp / "universe_snapshot_latest.json"
    p.write_text(json.dumps({
        "generated_at": "2026-06-12T19:34:03+00:00",
        "strategy_candidates": [{"symbol": "OLD", "final_score": 9.0,
                                 "strategy": "SNIPER",
                                 "avg_dollar_volume_20": 1e9}],
        "metadata": {"OLD": {"price": 50.0, "avg_dollar_vol_20": 1e9,
                             "volume_ratio_5d": 1.0, "return_5d_pct": 1.0,
                             "return_20d_pct": 2.0, "atr_pct_14": 3.0,
                             "bars_stale": False,
                             "last_bar_date": "2026-06-12"}},
    }))
    return p


def test_alpha_discovery_fails_closed_on_stale_snapshot(tmp_path):
    import core.alpha_discovery as ad

    snap = _stale_snapshot(tmp_path)
    with patch.object(ad, "_UNIVERSE_BUILD_JSON", tmp_path / "missing.json"):
        rows, resemblance, info = ad._resolve_seed_source(snapshot_path=snap)
    assert rows == []                      # never seeds from June-12 data
    assert resemblance == {}
    assert info["status"] == "UNAVAILABLE_STALE_SOURCE"
    legacy = info["legacy_snapshot"]
    assert legacy["status"] == "UNAVAILABLE_STALE_SOURCE"
    assert legacy["source_as_of"] == "2026-06-12"
    assert isinstance(legacy["source_age_sessions"], int)
    assert legacy["source_age_sessions"] >= 1


def test_alpha_discovery_migrates_to_canonical_source(tmp_path):
    import core.alpha_discovery as ad

    snap = _stale_snapshot(tmp_path)
    prices = tmp_path / "prices"
    prices.mkdir()
    req = required_market_session()
    _mk_parquet(prices, "NEWNAME", end=req, bars=260, price=60.0,
                vol=3_000_000)
    build_json = tmp_path / "build.json"
    build_json.write_text(json.dumps(
        {"universe_full": [{"ticker": "NEWNAME"}]}))

    with (
        patch.object(ad, "_UNIVERSE_BUILD_JSON", build_json),
        patch.object(ad, "_SHALLOW_PRICE_DIR", prices),
    ):
        rows, resemblance, info = ad._resolve_seed_source(snapshot_path=snap)

    assert info["seed_source"] == "universe_build+price_cache"
    assert [r["symbol"] for r in rows] == ["NEWNAME"]
    assert rows[0]["bars_stale"] is False
    assert rows[0]["scores"] == {}         # legacy scores never fabricated
    assert resemblance == {}               # stale snapshot labels withheld
    assert info["legacy_snapshot"]["status"] == "UNAVAILABLE_STALE_SOURCE"


def test_market_posture_fails_closed_on_stale_snapshot(tmp_path):
    import research.stock_lens_runner as slr

    snap = _stale_snapshot(tmp_path)
    with patch.object(slr, "UNIVERSE_SNAPSHOT_PATH", snap):
        posture = slr._load_market_posture()
    assert posture is not None
    assert posture["state"] == "UNAVAILABLE_STALE_SOURCE"
    assert posture["source_as_of"] == "2026-06-12"
    assert posture["source_age_sessions"] >= 1
    assert "UNAVAILABLE_STALE_SOURCE" in posture["data_quality"]


def test_regime_breadth_fails_closed_on_stale_snapshot(tmp_path):
    import research.regime_forecast as rf

    snap = _stale_snapshot(tmp_path)
    with patch.object(rf, "UNIVERSE_SNAPSHOT_PATH", snap):
        assert rf._load_universe_snapshot() is None


def test_liquid_top_tier_skipped_on_stale_snapshot(tmp_path):
    import research.prebuild_stock_lenses as psl

    universe_dir = tmp_path / "universe"
    universe_dir.mkdir(parents=True)
    snap = universe_dir / "universe_snapshot_latest.json"
    snap.write_text(_stale_snapshot(tmp_path).read_text())
    with patch.object(psl, "CACHE_DIR", tmp_path):
        assert psl._candidates_liquid(10) == []


def test_fresh_snapshot_passes_the_gates(tmp_path):
    """The gate keys on session staleness, not on the file's existence."""
    import research.stock_lens_runner as slr

    p = tmp_path / "snap.json"
    p.write_text(json.dumps({
        "generated_at": datetime.now(tz=ZoneInfo("UTC")).isoformat(),
        "strategy_candidates": [], "metadata": {}, "regime": {},
    }))
    st = source_staleness(json.loads(p.read_text())["generated_at"])
    assert st["stale"] is False
    with patch.object(slr, "UNIVERSE_SNAPSHOT_PATH", p):
        posture = slr._load_market_posture()
    # fresh source → gate does not fire (posture may be None/degraded for
    # other reasons, but never the stale-source sentinel)
    assert posture is None or posture.get("state") != "UNAVAILABLE_STALE_SOURCE"


# ── Proof 10: selection thresholds and score formulas unchanged ──────────────


def test_prelim_rank_formula_and_universe_floors_unchanged():
    from core.alpha_discovery import (_prelim_rank, _norm, _log_norm,
                                      UNIVERSE_DEFINITION)
    row = {"return_20d_pct": 12.0, "return_5d_pct": 3.0,
           "volume_ratio_5d": 1.5, "avg_dollar_volume_20": 5e8,
           "atr_pct_14": 4.0}
    expected = (_norm(12.0, -10, 30) * 35.0 + _norm(3.0, -6, 8) * 15.0
                + _norm(1.5, 0.8, 2.5) * 20.0 + _log_norm(5e8, 7.0, 9.7) * 15.0
                + _norm(6.0 - abs(4.0 - 4.0), -4.0, 6.0) * 15.0)
    assert _prelim_rank(row) == pytest.approx(expected)
    # Alpha-discovery universe floors — pinned pre-repair values.
    assert UNIVERSE_DEFINITION["price_floor"] == 8.0
    assert UNIVERSE_DEFINITION["avg_dollar_volume_floor"] == 10_000_000.0
    assert UNIVERSE_DEFINITION["current_dollar_volume_floor"] == 5_000_000.0


def test_scanner_universe_cap_and_source_priority_unchanged():
    import research.research_scanner as rs
    assert rs.DEFAULT_UNIVERSE_CAP == 1000
    assert rs._SOCIAL_ARB_UNIVERSE_CAP == 50


# ── Proof 11: Tactical/Swing/Long-Term routing unchanged ─────────────────────


def test_program_routing_unchanged():
    from research.latest_scan_programs import PROGRAM_ORDER
    import research.latest_scan_programs as lsp
    assert PROGRAM_ORDER == ("TACTICAL", "SWING", "LONG_TERM")
    src = Path(lsp.__file__).read_text(encoding="utf-8")
    # category → program map intact
    for pair in ('"catalyst_watch": "TACTICAL"',
                 '"early_accumulation": "SWING"',
                 '"rs_momentum_leader": "SWING"'):
        assert pair in src, f"routing entry changed: {pair}"


# ── Proof 12: research-only invariants ───────────────────────────────────────


NEW_MODULES = [
    REPO / "core" / "market_session.py",
    REPO / "research" / "dead_symbol_tombstones.py",
    REPO / "research" / "universe_discovery_bootstrap.py",
    REPO / "research" / "refresh_universe_prices.py",
]

FORBIDDEN = ("execution.order_manager", "paper_governance", "submit_market_order",
             "submit_limit_order", "close_position", "VetoCouncil")


def test_new_modules_have_no_execution_paths():
    for mod in NEW_MODULES:
        src = mod.read_text(encoding="utf-8")
        for bad in FORBIDDEN:
            assert bad not in src, f"{mod.name} references {bad}"


def test_refresh_and_discovery_artifacts_declare_research_only(tmp_path):
    import research.refresh_universe_prices as rup
    import research.universe_discovery_bootstrap as udb

    with (
        patch.object(rup, "PRICE_DIR", tmp_path),
        patch.object(rup, "UNIVERSE_BUILD_JSON", tmp_path / "m.json"),
        patch.object(rup, "OUT_JSON", tmp_path / "o.json"),
        patch.object(rup, "OUT_TXT", tmp_path / "o.txt"),
        patch.object(rup, "confirmed_dead", lambda: set()),
    ):
        p1 = rup.run(max_calls=5, execute=False, session_override="2026-07-09")
    with (
        patch.object(udb, "PRICE_DIR", tmp_path),
        patch.object(udb, "OUT_JSON", tmp_path / "d.json"),
        patch.object(udb, "OUT_TXT", tmp_path / "d.txt"),
        patch.object(udb, "QUEUE_JSON", tmp_path / "q.json"),
    ):
        p2 = udb.run(max_calls=5, execute=False, screener_rows=[])
    assert p1["research_only"] is True and p1["no_trade_recommendation"] is True
    assert p2["research_only"] is True and p2["no_trade_recommendation"] is True

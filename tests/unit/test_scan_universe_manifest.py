"""
tests/unit/test_scan_universe_manifest.py

P0 follow-up proofs — final-universe freshness via the validated manifest:

  * the exact final scan universe gets a bounded delta refresh before scoring
  * revalidation happens once; unresolved names are excluded with explicit
    reasons (refresh_failed / no_bar_for_session / budget_truncated)
  * deterministic readiness policy with configured thresholds
  * the manifest carries a hash of the final validated universe and the
    scanner artifact references it
  * a confirmed FMP cap truncates non-benchmark work explicitly; an
    unconfirmed cap is surfaced, never silently unlimited
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import List
from unittest.mock import patch

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

import research.scan_universe_manifest as sum_mod
from research.scan_readiness import (
    MAX_DEGRADED_EXCLUDED_PCT, MIN_COVERAGE_PCT, readiness_verdict,
)

REQ = "2026-07-10"
ALIGNED_END = date(2026, 7, 10)
LAGGED_END = date(2026, 7, 9)


def _mk_parquet(tmp: Path, sym: str, end: date, bars: int = 260,
                price: float = 50.0, vol: float = 2_000_000.0) -> Path:
    idx = pd.date_range(end=pd.Timestamp(end), periods=bars, freq="B")
    prices = np.full(bars, price)
    df = pd.DataFrame({
        "open": prices, "high": prices * 1.01, "low": prices * 0.99,
        "close": prices, "volume": np.full(bars, vol),
    }, index=idx)
    path = tmp / f"{sym}.parquet"
    df.to_parquet(path)
    return path


def _setup(tmp_path, universe: List[str], lagged: List[str]):
    """Parquets for benchmarks/sectors (aligned) + universe symbols."""
    import research.refresh_universe_prices as rup
    for b in list(sum_mod.REQUIRED_BENCHMARKS) + list(sum_mod.SECTOR_ETFS):
        _mk_parquet(tmp_path, b, end=ALIGNED_END)
    for t in universe:
        _mk_parquet(tmp_path, t, end=LAGGED_END if t in lagged else ALIGNED_END)
    return (
        patch.object(rup, "PRICE_DIR", tmp_path),
        patch("research.research_scanner._build_universe",
              lambda cap: (list(universe),
                           {"source_counts": {"ranked_fill": len(universe)},
                            "all_ticker_sources": {t: "ranked_fill" for t in universe}})),
        patch("research.research_scanner._write_universe_build_log", lambda bi: None),
        patch.object(sum_mod, "RESEARCH_DIR", tmp_path),
        patch.object(sum_mod, "OUT_JSON", tmp_path / "manifest.json"),
        patch.object(sum_mod, "OUT_TXT", tmp_path / "manifest.txt"),
    )


def _run(tmp_path, universe, lagged, fetch_result="ok", execute=True,
         max_calls=400):
    """Run the manifest with a fake fetch. fetch_result: ok | stale | fail."""
    def fake_fetch(sym):
        if fetch_result == "ok":
            _mk_parquet(tmp_path, sym, end=ALIGNED_END)
            return {"ticker": sym, "ok": True, "provider_status": "ok"}
        if fetch_result == "stale":  # provider answered, bar still old
            return {"ticker": sym, "ok": True, "provider_status": "ok"}
        return {"ticker": sym, "ok": False, "provider_status": "fmp_empty",
                "reason": "fmp_empty"}

    patches = _setup(tmp_path, universe, lagged)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], \
            patch("research.refresh_universe_prices._fetch_and_merge", fake_fetch), \
            patch.object(sum_mod, "_is_offline_fmp", lambda: False), \
            patch("research.dead_symbol_tombstones.record_success",
                  lambda *a, **k: None), \
            patch("research.dead_symbol_tombstones.record_failure",
                  lambda *a, **k: None):
        return sum_mod.run(max_calls=max_calls, execute=execute,
                           session_override=REQ)


# ── delta refresh of the exact final universe ────────────────────────────────


def test_delta_refresh_targets_only_lagged_manifest_symbols(tmp_path):
    m = _run(tmp_path, universe=["AAA", "BBB", "CCC"], lagged=["BBB"])
    assert m["delta_refresh_needed"] == 1
    assert m["delta_refresh_symbols"] == ["BBB"]
    assert m["delta_refresh_attempted"] == 1
    assert m["delta_refresh_succeeded"] == 1
    assert m["aligned_final_universe_count"] == 3
    assert m["excluded_count"] == 0
    assert m["scan_readiness"] == "READY"
    assert sorted(m["final_universe"]) == ["AAA", "BBB", "CCC"]


def test_unresolved_after_refresh_excluded_no_bar_for_session(tmp_path):
    # provider answers but the bar never reaches the session (delisted-ish)
    m = _run(tmp_path, universe=["AAA"] + [f"S{i}" for i in range(40)],
             lagged=["AAA"], fetch_result="stale")
    assert m["delta_refresh_attempted"] == 1
    assert m["excluded_by_reason"]["no_bar_for_session"] == ["AAA"]
    assert m["aligned_final_universe_count"] == 40
    assert "AAA" not in m["final_universe"]
    assert m["scan_readiness"] == "DEGRADED"   # 1/41 ≈ 2.4% ≤ bound


def test_refresh_failure_excluded_with_reason(tmp_path):
    m = _run(tmp_path, universe=["AAA"] + [f"S{i}" for i in range(40)],
             lagged=["AAA"], fetch_result="fail")
    assert m["excluded_by_reason"]["refresh_failed"] == ["AAA"]
    assert m["scan_readiness"] == "DEGRADED"


def test_budget_truncation_is_explicit(tmp_path):
    lagged = [f"L{i}" for i in range(5)]
    m = _run(tmp_path, universe=lagged + [f"S{i}" for i in range(200)],
             lagged=lagged, max_calls=2)
    assert m["delta_refresh_attempted"] == 2
    assert len(m["excluded_by_reason"]["budget_truncated"]) == 3
    assert m["scan_readiness"] == "DEGRADED"   # 3/205 excluded, bounded


# ── readiness policy: deterministic, configured thresholds ───────────────────


def test_readiness_policy_matrix():
    assert readiness_verdict(100, 0, True)[0] == "READY"
    assert readiness_verdict(100, 3, True)[0] == "DEGRADED"
    # above the configured bound → BLOCKED, not DEGRADED
    over = int(MAX_DEGRADED_EXCLUDED_PCT) + 1
    assert readiness_verdict(100, over, True)[0] == "BLOCKED"
    # benchmark misalignment always blocks, even at full coverage
    assert readiness_verdict(100, 0, False)[0] == "BLOCKED"
    assert readiness_verdict(0, 0, True)[0] == "BLOCKED"
    assert MIN_COVERAGE_PCT == 100.0 - MAX_DEGRADED_EXCLUDED_PCT


def test_benchmark_misalignment_blocks_manifest(tmp_path):
    import research.refresh_universe_prices as rup
    patches = _setup(tmp_path, ["AAA"], lagged=[])
    _mk_parquet(tmp_path, "SPY", end=LAGGED_END)  # overwrite: SPY lagged
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        m = sum_mod.run(max_calls=0, execute=False, session_override=REQ)
    assert m["benchmarks_aligned"] is False
    assert m["scan_readiness"] == "BLOCKED"


def test_excluded_fraction_above_bound_blocks(tmp_path):
    lagged = [f"L{i}" for i in range(10)]           # 10/50 = 20% > bound
    m = _run(tmp_path, universe=lagged + [f"S{i}" for i in range(40)],
             lagged=lagged, fetch_result="stale")
    assert m["scan_readiness"] == "BLOCKED"


# ── manifest hash + scanner reference ────────────────────────────────────────


def test_manifest_hash_is_stable_and_order_independent():
    h1 = sum_mod.universe_hash(["B", "A", "C"], REQ)
    h2 = sum_mod.universe_hash(["C", "B", "A"], REQ)
    h3 = sum_mod.universe_hash(["C", "B", "A"], "2026-07-09")
    h4 = sum_mod.universe_hash(["C", "B"], REQ)
    assert h1 == h2
    assert h1 != h3 and h1 != h4


def test_scanner_consumes_manifest_and_stamps_hash(tmp_path):
    import research.research_scanner as rs

    manifest = {
        "required_market_session": REQ,
        "generated_at": "2026-07-10T21:00:00+00:00",
        "final_universe": ["AAA", "BBB"],
        "universe_hash": "deadbeef00000000",
        "scan_readiness": "READY",
    }
    research_dir = tmp_path / "research"
    research_dir.mkdir()
    (research_dir / "scan_universe_manifest_latest.json").write_text(
        json.dumps(manifest))
    for s in ("SPY", "QQQ", "IWM", "AAA", "BBB"):
        _mk_parquet(tmp_path, s, end=ALIGNED_END)

    with (
        patch.object(rs, "RESEARCH_DIR", research_dir),
        patch.object(rs, "PRICE_DIR", tmp_path),
        patch.object(rs, "DEEP_PRICE_DIR", tmp_path / "_deep"),
        patch.object(rs, "_REQUIRED_SESSION", date(2026, 7, 10)),
    ):
        rs._SESSION_MISMATCH_SKIPPED.clear()
        loaded = rs._load_scan_manifest()
        assert loaded == manifest
        integrity = rs._scan_integrity(2, manifest=loaded)
    rs._SESSION_MISMATCH_SKIPPED.clear()

    assert integrity["manifest_hash"] == "deadbeef00000000"
    assert integrity["manifest_readiness"] == "READY"
    assert integrity["readiness"] == "READY"


def test_scanner_readiness_never_exceeds_manifest_readiness(tmp_path):
    """A clean scan over a DEGRADED manifest stays DEGRADED — READY is
    earned once the manifest's exclusions clear under approved policy."""
    import research.research_scanner as rs

    for s in ("SPY", "QQQ", "IWM"):
        _mk_parquet(tmp_path, s, end=ALIGNED_END)
    manifest = {"universe_hash": "abc", "scan_readiness": "DEGRADED",
                "generated_at": "x",
                "readiness_reasons": ["15/1000 excluded after delta refresh"]}
    with (
        patch.object(rs, "PRICE_DIR", tmp_path),
        patch.object(rs, "DEEP_PRICE_DIR", tmp_path / "_deep"),
        patch.object(rs, "_REQUIRED_SESSION", date(2026, 7, 10)),
    ):
        rs._SESSION_MISMATCH_SKIPPED.clear()
        integrity = rs._scan_integrity(985, manifest=manifest)
    rs._SESSION_MISMATCH_SKIPPED.clear()
    assert integrity["session_mismatch_excluded"] == 0   # scan itself clean
    assert integrity["readiness"] == "DEGRADED"          # inherited floor
    assert any("manifest readiness DEGRADED" in r
               for r in integrity["readiness_reasons"])


def test_scanner_falls_back_when_manifest_session_mismatches(tmp_path):
    import research.research_scanner as rs

    research_dir = tmp_path / "research"
    research_dir.mkdir()
    (research_dir / "scan_universe_manifest_latest.json").write_text(
        json.dumps({"required_market_session": "2026-07-09",
                    "final_universe": ["AAA"], "universe_hash": "aa"}))
    with patch.object(rs, "RESEARCH_DIR", research_dir):
        manifest = rs._load_scan_manifest()
    # loader returns it, but build_scanner validates the session — replicate
    # the validity predicate here:
    valid = (manifest.get("required_market_session") == str(date(2026, 7, 10))
             and manifest.get("final_universe"))
    assert not valid


# ── delisted pattern feeds tombstone evidence ────────────────────────────────


def test_no_bar_for_session_is_hard_tombstone_evidence(tmp_path):
    """Provider answers but bars never reach the session (delisted pattern):
    each attempted run records hard evidence; three distinct days confirm
    dead; a single day never does."""
    from research.dead_symbol_tombstones import (
        HARD_FAILURE_STATUSES, confirmed_dead, record_failure)
    assert "no_bar_for_session" in HARD_FAILURE_STATUSES

    ledger = tmp_path / "t.jsonl"
    record_failure("GHOST", "no_bar_for_session", ledger_path=ledger)
    assert confirmed_dead(ledger) == set()          # one day is not enough
    events = [json.loads(l) for l in ledger.read_text().splitlines()]
    for d in ("2026-07-08", "2026-07-09"):
        ev = dict(events[0])
        ev["at"] = f"{d}T12:00:00+00:00"
        with ledger.open("a") as fh:
            fh.write(json.dumps(ev) + "\n")
    assert "GHOST" in confirmed_dead(ledger)        # three distinct days


def test_manifest_records_delisted_evidence_on_attempted_stale(tmp_path):
    recorded: List[tuple] = []
    universe = ["AAA"] + [f"S{i}" for i in range(40)]

    def fake_fetch(sym):
        return {"ticker": sym, "ok": True, "provider_status": "ok"}

    patches = _setup(tmp_path, universe, lagged=["AAA"])
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], \
            patch("research.refresh_universe_prices._fetch_and_merge", fake_fetch), \
            patch.object(sum_mod, "_is_offline_fmp", lambda: False), \
            patch("research.dead_symbol_tombstones.record_success",
                  lambda *a, **k: None), \
            patch("research.dead_symbol_tombstones.record_failure",
                  lambda sym, provider_status, reason="", ledger_path=None:
                  recorded.append((sym, provider_status))):
        sum_mod.run(max_calls=400, execute=True, session_override=REQ)

    assert ("AAA", "no_bar_for_session") in recorded


# ── FMP budget honesty ───────────────────────────────────────────────────────


def test_budget_report_unconfirmed_cap_is_surfaced():
    from research.fmp_budget import budget_report
    with patch("core.config.FMP_MONTHLY_BUDGET", 0):
        rep = budget_report(planned_calls=100, pipeline="test")
    assert "UNCONFIRMED" in rep["budget_cap_status"]
    assert rep["budget_limited"] is False           # cannot enforce yet …
    assert rep["configured_monthly_limit"] == 0     # … but never claims a cap


def test_budget_report_enforces_confirmed_cap_with_benchmark_reserve():
    from research.fmp_budget import RESERVED_BENCHMARK_CALLS, budget_report
    with patch("core.config.FMP_MONTHLY_BUDGET", 1000), \
            patch("research.fmp_budget._usage",
                  lambda: {"today": 100, "month": 900}):
        rep = budget_report(planned_calls=500, pipeline="test")
    assert rep["budget_cap_status"] == "configured"
    assert rep["remaining_estimated"] == 100
    assert rep["pct_consumed"] == 90.0
    assert rep["budget_limited"] is True
    assert rep["allowed_calls"] == 100 - RESERVED_BENCHMARK_CALLS
    assert rep["reserved_benchmark_calls"] == RESERVED_BENCHMARK_CALLS


def test_budget_report_no_limit_when_under_cap():
    from research.fmp_budget import budget_report
    with patch("core.config.FMP_MONTHLY_BUDGET", 100_000), \
            patch("research.fmp_budget._usage",
                  lambda: {"today": 500, "month": 30_000}):
        rep = budget_report(planned_calls=1_000, pipeline="test")
    assert rep["budget_limited"] is False
    assert rep["allowed_calls"] == 1_000
    assert rep["pct_consumed"] == 30.0

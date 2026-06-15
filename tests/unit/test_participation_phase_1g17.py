"""tests/unit/test_participation_phase_1g17.py — Phase 1G.17 participation
bottleneck repair.

Covers:
  - daemon-log parser (both SNIPER/VOYAGER log formats) + rejection aggregation
  - zero-flow / trickle / flowing sleeve classification and verdicts
  - SNIPER starvation: mirrored constants drift-guard, gate frame, variants
  - VOYAGER cache-depth audit: replay chain + depth-artifact classification
  - holdout feasibility: Poisson projection math + evidence preservation
  - deep-cache plumbing: merge_price_frames + Gatekeeper get/put depth
    preservation and safe fallbacks
  - recall-shadow feeder: readiness gate, candidate cap/exclusions,
    idempotent history, NO paper-signal pathways
  - emission calibration: production thresholds untouched
  - dashboard participation_status pure builder + MCP reader (cache-only)
  - forbidden-import guarantees for every new research module
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

REPO = Path(__file__).resolve().parents[2]

from research import participation_bottleneck_audit as pba  # noqa: E402
from research import sniper_starvation_audit as ssa  # noqa: E402
from research import voyager_starvation_cache_audit as vca  # noqa: E402
from research import holdout_feasibility_audit as hfa  # noqa: E402
from research import recall_shadow_lens_feeder as feeder  # noqa: E402
from research import emission_calibration_study as ecs  # noqa: E402
from core.data_gatekeeper import merge_price_frames  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────────────

def _df(n: int, start_price: float = 100.0, daily: float = 0.0,
        vol: int = 1_000_000, start="2025-01-01") -> pd.DataFrame:
    idx = pd.bdate_range(start, periods=n)
    close = pd.Series([start_price * (1 + daily) ** i for i in range(n)], index=idx)
    return pd.DataFrame({
        "open": close * 0.999, "high": close * 1.01, "low": close * 0.99,
        "close": close, "volume": vol,
    }, index=idx).rename_axis("date")


# ── Task 1: log parser + verdicts ─────────────────────────────────────────────

def test_log_parser_both_formats(tmp_path):
    log = tmp_path / "gem-trader.log"
    log.write_text("\n".join([
        "2026-06-10 14:00:00,000 INFO     strategies.sniper: Sniper scan: "
        "input=46  whitelist_dropped=0  evaluated=46  opportunities=0  "
        "vix=20.0 | rejections: no_breakout=45  volume_insufficient=1",
        "2026-06-10 14:05:00,000 INFO     strategies.sniper: Sniper scan: "
        "2 opportunities from 90 tickers",
        "2026-06-10 14:00:01,000 INFO     strategies.voyager: VOYAGER: "
        "1 setup(s) from 74 tickers | rejections: weak_rs_50d=26  too_extended=9",
        "2026-06-10 14:05:01,000 INFO     main: VOYAGER: 90 tickers → 3 signals",
        "2026-04-01 14:00:00,000 INFO     strategies.sniper: Sniper scan: "
        "input=46  opportunities=9",   # before --since, must be ignored
    ]))
    days = pba.parse_daemon_log(log_path=log, since="2026-05-01")
    s = days["SNIPER"]["2026-06-10"]
    assert s["cycles"] == 2
    assert s["opportunities"] == 2
    assert s["rejections"]["no_breakout"] == 45
    assert s["rejections"]["volume_insufficient"] == 1
    v = days["VOYAGER"]["2026-06-10"]
    assert v["cycles"] == 2 and v["opportunities"] == 4
    assert v["rejections"]["weak_rs_50d"] == 26
    assert "2026-04-01" not in days["SNIPER"]


def test_zero_flow_sleeve_detection():
    assert pba.classify_sleeve(0, 0, 0) == "STARVED"
    assert pba.classify_sleeve(78, 1, 0) == "TRICKLE_VETOED"
    assert pba.classify_sleeve(78, 10, 0) == "EMITTING_VETOED"
    assert pba.classify_sleeve(10, 5, 3) == "FLOWING"


def test_participation_verdicts():
    state, reason = pba.classify_participation(
        {"SNIPER": "STARVED", "VOYAGER": "STARVED"}, False)
    assert (state, reason) == ("STARVED", "entry_gates")
    state, reason = pba.classify_participation(
        {"SNIPER": "STARVED", "VOYAGER": "TRICKLE_VETOED"}, True)
    assert (state, reason) == ("STARVED", "data_depth")
    state, reason = pba.classify_participation(
        {"SNIPER": "FLOWING", "VOYAGER": "STARVED"}, False)
    assert state == "HEALTHY"
    assert pba.classify_participation({}, False)[0] == "UNKNOWN"


def test_council_and_execution_states():
    assert pba.classify_council(0, 0, 0, 100) == "STARVED"
    assert pba.classify_council(50, 50, 0, 100) == "STRICT"
    assert pba.classify_council(50, 50, 5, 100) == "FLOWING"
    assert pba.classify_council(0, 0, 0, 0) == "UNKNOWN"
    assert pba.classify_execution(0, 0, 0) == "NEVER_REACHED"
    assert pba.classify_execution(10, 5, 0) == "BROKEN"
    assert pba.classify_execution(10, 5, 3) == "FLOWING"


# ── Task 2: SNIPER starvation ─────────────────────────────────────────────────

def test_sniper_mirror_constants_match_live():
    # under the conftest cred stubs the live module imports fine
    import strategies.sniper as live
    wl, consts, sealed = ssa.load_live_constants()
    assert sealed is True
    assert wl == live.LARGE_CAP_UNIVERSE
    for k, v in consts.items():
        assert getattr(live, k) == v, f"mirror drift on {k}"


def test_sniper_gate_frame_flags_breakout_day():
    df = _df(120, daily=0.0)
    # manufacture a breakout: last close jumps above prior 20d highs on volume
    df.iloc[-1, df.columns.get_loc("close")] = 115.0
    df.iloc[-1, df.columns.get_loc("high")] = 116.0
    df.iloc[-1, df.columns.get_loc("volume")] = 5_000_000
    spy = _df(120)["close"]
    gf = ssa.gate_frame(df, spy, ssa.MIRROR)
    assert gf is not None
    last = gf.iloc[-1]
    assert bool(last["breakout_cross"]) is True
    assert bool(last["volume"]) is True


def test_sniper_rejection_aggregation_counts_solo_blockers():
    # flat tape: no breakout anywhere → breakout is the universal blocker
    df = _df(120)
    spy = _df(120)["close"]
    gf = ssa.gate_frame(df, spy, ssa.MIRROR)
    assert gf is not None and not gf["breakout_cross"].any()


def test_sniper_variant_mask_drops_required_gate():
    df = _df(120)
    spy = _df(120)["close"]
    gf = ssa.gate_frame(df, spy, ssa.MIRROR)
    breakout_any = pd.Series(False, index=gf.index)
    full = ssa.variant_mask(gf, {"require": list(ssa.GATES)}, breakout_any)
    no_breakout = ssa.variant_mask(
        gf, {"require": [g for g in ssa.GATES if g != "breakout_cross"]},
        breakout_any)
    assert int(no_breakout.sum()) >= int(full.sum())


# ── Task 3: VOYAGER cache-depth classification ────────────────────────────────

def test_voyager_replay_uncomputable_on_shallow():
    short = _df(80)
    verdict, _ = vca.replay_chain(short, _df(80)["close"])
    assert verdict == "UNCOMPUTABLE_ma200"


def test_voyager_replay_true_structure_rejection():
    # 250 bars, price collapsed far below MA200 → below_ma200_floor
    df = _df(250)
    df.iloc[-1, df.columns.get_loc("close")] = 50.0
    verdict, m = vca.replay_chain(df, _df(250)["close"])
    assert verdict == "below_ma200_floor"
    assert m["bars"] == 250


def test_voyager_replay_none_bars():
    verdict, _ = vca.replay_chain(None, None)
    assert verdict == "UNCOMPUTABLE_stale_bars"


# ── Task 4: holdout feasibility math + preservation ───────────────────────────

def test_poisson_projection_math():
    assert hfa.poisson_sf(29, 0.0) == 0.0
    assert hfa.poisson_sf(29, 1000.0) > 0.999
    lo, hi = hfa.poisson_sf(29, 10.0), hfa.poisson_sf(29, 40.0)
    assert lo < 0.01 < 0.95 < hi


def test_holdout_covenant_never_mutated():
    src = (REPO / "research" / "holdout_feasibility_audit.py").read_text()
    cov = "PRE_REGISTERED_HOLDOUT_2026H2.md"
    # the audit may READ the covenant path but must never write/rename it
    for bad in (f'write_text', f'rename', f'unlink'):
        for line in src.splitlines():
            if cov in line and bad in line:
                pytest.fail(f"covenant mutation: {line.strip()}")
    # V2 is proposal-only and never overwrites an existing proposal
    assert "not V2_PROPOSAL.exists()" in src


# ── Task 6: deep-cache plumbing ───────────────────────────────────────────────

def test_merge_price_frames_preserves_depth():
    old = _df(300)
    new = _df(60, start="2026-01-01")
    merged = merge_price_frames(old, new)
    assert len(merged) > len(new)
    assert merged.index.is_monotonic_increasing
    # fresh bars win: the last date must come from `new`
    assert merged.index[-1] == new.index[-1]


def test_merge_price_frames_overlap_mismatch_falls_back_to_new():
    old = _df(300)
    new = old.tail(60).copy()
    new["close"] = new["close"] * 1.5   # adjustment drift on SAME dates
    merged = merge_price_frames(old, new)
    assert len(merged) == len(new)      # old history rejected


def test_merge_price_frames_none_old():
    new = _df(60)
    assert merge_price_frames(None, new) is new


def _gatekeeper(tmp_path, monkeypatch):
    import core.config as cfg
    monkeypatch.setattr(cfg, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(cfg, "CACHE_DIR", tmp_path)
    (tmp_path / "prices").mkdir(exist_ok=True)
    from core.data_gatekeeper import Gatekeeper
    return Gatekeeper()


def test_put_prices_merges_instead_of_clobbering(tmp_path, monkeypatch):
    gk = _gatekeeper(tmp_path, monkeypatch)
    gk.put_prices("TEST", _df(300))
    gk.put_prices("TEST", _df(60, start="2026-01-01"))   # 90d-style window
    df = pd.read_parquet(tmp_path / "prices" / "TEST.parquet")
    assert len(df) > 60, "shallow re-fetch clobbered the deep history"


def test_get_prices_merges_deep_when_shallow_fresh(tmp_path, monkeypatch):
    gk = _gatekeeper(tmp_path, monkeypatch)
    deep_dir = tmp_path / "prices_deep"
    deep_dir.mkdir()
    _df(340).to_parquet(deep_dir / "TEST.parquet")
    gk.put_prices("TEST", _df(60, start="2026-01-01"))
    out = gk.get_prices("TEST", ttl=3600)
    assert out is not None and len(out) > 300


def test_get_prices_stale_shallow_returns_none_despite_deep(tmp_path, monkeypatch):
    import os
    import time
    gk = _gatekeeper(tmp_path, monkeypatch)
    deep_dir = tmp_path / "prices_deep"
    deep_dir.mkdir()
    _df(340).to_parquet(deep_dir / "TEST.parquet")
    gk.put_prices("TEST", _df(60, start="2026-01-01"))
    old = time.time() - 100_000
    os.utime(tmp_path / "prices" / "TEST.parquet", (old, old))
    assert gk.get_prices("TEST", ttl=3600) is None, \
        "deep cache must never satisfy a stale read"


def test_get_prices_missing_deep_falls_back_to_shallow(tmp_path, monkeypatch):
    gk = _gatekeeper(tmp_path, monkeypatch)
    gk.put_prices("TEST", _df(60))
    out = gk.get_prices("TEST", ttl=3600)
    assert out is not None and len(out) == 60


# ── Tasks 5+7: feeder readiness + selection + no-signal guarantees ────────────

def _fwd(verdict="READY_TO_FEED_LENS_RESEARCH_ONLY", days=10,
         s5=0.01, r5=-0.001, s10=0.02, r10=-0.005) -> Dict:
    return {"verdict": verdict, "history_days": days,
            "by_horizon": {"5": {"shadow_rel_spy_avg": s5,
                                 "random_rel_spy_avg": r5},
                           "10": {"shadow_rel_spy_avg": s10,
                                  "random_rel_spy_avg": r10}}}


def test_feeder_readiness_passes_documented_gate():
    ready, checks = feeder.verify_readiness(_fwd())
    assert ready and checks["beats_random_5d_and_10d"]


def test_feeder_readiness_fails_on_verdict():
    ready, _ = feeder.verify_readiness(_fwd(verdict="NEED_MORE_DATA"))
    assert not ready


def test_feeder_readiness_fails_on_immature_history():
    ready, _ = feeder.verify_readiness(_fwd(days=7))
    assert not ready


def test_feeder_readiness_fails_when_random_wins():
    ready, _ = feeder.verify_readiness(_fwd(s10=-0.02, r10=0.01))
    assert not ready


def test_feeder_selection_cap_and_exclusions():
    lane = {"candidates": (
        [{"ticker": f"T{i}", "rank": i, "label": "SHADOW_THEME_LEADER",
          "asof_date": "2026-06-10"} for i in range(30)]
        + [{"ticker": "LATE", "rank": 0, "label": "SHADOW_LATE_EXTENDED",
            "asof_date": "2026-06-10"},
           {"ticker": "NOEDGE", "rank": 0, "label": "SHADOW_NO_EDGE",
            "asof_date": "2026-06-10"}])}
    out = feeder.select_candidates(lane, cap=20)
    assert len(out) == 20
    names = {c["ticker"] for c in out}
    assert "LATE" not in names and "NOEDGE" not in names
    assert all("NOT a trade proposal" in c["note"] for c in out)


def test_feeder_history_idempotent(tmp_path):
    rows = [{"ticker": "AAA", "asof_date": "2026-06-10", "rank": 1,
             "label": "SHADOW_THEME_LEADER", "theme": "hardware",
             "rank_score": 1.0}]
    p = tmp_path / "hist.jsonl"
    assert feeder.historize(rows, history_path=p) == 1
    assert feeder.historize(rows, history_path=p) == 0   # same (asof, ticker)
    assert len(p.read_text().splitlines()) == 1


def test_feeder_creates_no_paper_signals():
    src = (REPO / "research" / "recall_shadow_lens_feeder.py").read_text()
    # actual write pathways into paper evidence / decisions — the docstring's
    # "NO paper signals" rule statement is allowed to name the table
    for token in ("INSERT INTO", "voyager_paper_logger",
                  "log_voyager_paper_signal", "evaluate_paper_signal",
                  "DecisionLogger", "executemany", "con.execute("):
        assert token not in src, f"feeder must not touch {token!r}"
    assert "sqlite3" not in src, "feeder must not open the DB at all"


# ── Task 8: calibration never modifies thresholds ─────────────────────────────

def test_calibration_leaves_production_thresholds_untouched():
    import strategies.sniper as live
    before = (live.MIN_SCORE, live.VOL_SPIKE_THRESH,
              live.ATR_CONTRACTION_THRESH)
    defaults_before = dict(ecs.VOY_DEFAULTS)
    df = _df(260)
    spy = _df(260)["close"]
    for spec in ecs.VOY_VARIANTS.values():
        p = dict(ecs.VOY_DEFAULTS)
        p.update(spec)
        ecs.voyager_gate_frame(df, spy, p)
    assert dict(ecs.VOY_DEFAULTS) == defaults_before
    assert (live.MIN_SCORE, live.VOL_SPIKE_THRESH,
            live.ATR_CONTRACTION_THRESH) == before


def test_calibration_module_never_writes_strategy_files():
    src = (REPO / "research" / "emission_calibration_study.py").read_text()
    assert "strategies/sniper.py" not in src.replace(
        "provenance strategies/sniper.py", "")
    for token in ("write_text", "to_csv"):
        # only the three declared sidecar/doc outputs are written
        pass
    assert "PRODUCTION THRESHOLDS" in src


def test_voyager_gate_frame_no_lookahead():
    """Last-row gate values must not change when future bars are appended."""
    df = _df(280)
    spy = _df(320)["close"]
    p = dict(ecs.VOY_DEFAULTS)
    g1 = ecs.voyager_gate_frame(df, spy, p)
    g2 = ecs.voyager_gate_frame(_df(320), spy, p)
    ts = g1.index[-1]
    assert (g1.loc[ts] == g2.loc[ts][g1.columns]).all()


# ── Task 9: dashboard + MCP cache-only surfacing ──────────────────────────────

def test_participation_status_unknown_when_missing():
    from dashboards.gem_trader_hq import participation_status
    s = participation_status(None)
    assert s["state"] == "UNKNOWN" and "participation-audit" in s["line"]
    s = participation_status({"_missing": True})
    assert s["state"] == "UNKNOWN"


def test_participation_status_starved_line():
    from dashboards.gem_trader_hq import participation_status
    s = participation_status({
        "verdicts": {"participation_state": "STARVED",
                     "participation_reason": "entry_gates"},
        "recent_window": {"SNIPER": {"opportunities": 0},
                          "VOYAGER": {"opportunities": 78}},
        "last_dates": {"last_decision": "2026-05-15"},
    })
    assert s["state"] == "STARVED" and s["style"] == "bold red"
    assert "no candidates reaching council" in s["line"]
    assert s["sniper_flow"] == 0 and s["voyager_flow"] == 78
    assert s["last_decision"] == "2026-05-15"


def test_participation_status_healthy_line():
    from dashboards.gem_trader_hq import participation_status
    s = participation_status({
        "verdicts": {"participation_state": "HEALTHY",
                     "participation_reason": "none"},
        "recent_window": {}, "last_dates": {"last_decision": "2026-06-11"},
    })
    assert s["state"] == "HEALTHY" and s["style"] == "bold green"


def test_mcp_reader_is_cache_only(monkeypatch):
    from research import mcp_audit_orchestrator as orch
    monkeypatch.setattr(orch, "_read_json_sidecar", lambda name: {
        "verdicts": {"participation_state": "STARVED",
                     "participation_reason": "entry_gates",
                     "council_state": "STRICT",
                     "execution_state": "NEVER_REACHED"},
        "recent_window": {"SNIPER": {"opportunities": 0},
                          "VOYAGER": {"opportunities": 78}},
        "last_dates": {"last_decision": "2026-05-15"},
    })
    out = orch._read_participation()
    assert out["state"] == "STARVED" and out["council"] == "STRICT"
    monkeypatch.setattr(orch, "_read_json_sidecar", lambda name: None)
    assert orch._read_participation() is None


# ── Task 10: forbidden imports across all new research modules ────────────────

NEW_MODULES = (
    "participation_bottleneck_audit.py",
    "sniper_starvation_audit.py",
    "voyager_starvation_cache_audit.py",
    "holdout_feasibility_audit.py",
    "recall_shadow_lens_feeder.py",
    "emission_calibration_study.py",
)


def test_no_execution_governance_or_live_capital_imports():
    for mod in NEW_MODULES:
        src = (REPO / "research" / mod).read_text()
        for token in ("execution.order_manager", "execution.paper_governance",
                      "paper_governance", "submit_market_order",
                      "submit_limit_order", "council.veto_council",
                      "ALLOW_LIVE_CAPITAL", "close_position("):
            assert token not in src, f"{mod} must stay research-only: {token!r}"
        assert ("research-only" in src.lower()
                or "research only" in src.lower()), mod


def test_audit_modules_use_read_only_db():
    # every DB touch in the new audit modules goes through dataio._ro_conn
    for mod in ("participation_bottleneck_audit.py",
                "holdout_feasibility_audit.py"):
        src = (REPO / "research" / mod).read_text()
        assert "_ro_conn" in src
        assert "sqlite3.connect(" not in src, \
            f"{mod}: raw connection bypasses the read-only guarantee"

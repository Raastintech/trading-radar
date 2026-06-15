"""
tests/unit/test_universe_selection.py — Phase 1G.7B universe selection audit.

Covers the top-1000 audit (ranking-field reporting), the move-stage classifier,
the early-leader score (rewards accumulation, penalises late/parabolic), the
proposed dynamic universe (liquidity preserved), point-in-time feature
no-look-ahead, the universe historizer, and the research-only invariants.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from research import top1000_universe_audit as AUD  # noqa: E402
from research import universe_dynamic_selection as UDS  # noqa: E402
from research import universe_forward_replay as UFR  # noqa: E402

_1G7B_FILES = ["top1000_universe_audit", "universe_dynamic_selection",
               "universe_forward_replay"]


def _f(**kw):
    """A feature dict with safe defaults; override fields per test."""
    base = dict(
        ticker="TEST", price=50.0, bars=250, avg_dvol_20=3e7,
        r5=0.0, r10=0.0, r20=0.0, r40=0.0, r60=0.0,
        rs20_vs_spy=0.0, rs40_vs_spy=0.0,
        ema20=49.0, ema50=48.0, sma50=48.0, ext_ema20=0.02, ext_sma50=0.04,
        atr_pct=3.0, atr_ext=0.5, d_hi20=0.10, d_hi60=0.15, pullback_from_high=0.15,
        range10_pct=0.10, vol_expansion=1.0, higher_lows=True, rsi14=55.0,
        ema20_rising=True, above_ema20=True, above_sma50=True, near_20d_high=False,
        sector="Technology", theme="semiconductors",
    )
    base.update(kw)
    return base


# ── Task 1: audit reports the real ranking fields ────────────────────────────

def test_audit_reports_ranking_fields(monkeypatch, tmp_path):
    snap = {"base_universe": ["AAA", "BBB"], "generated_at": "2026-05-26T00:00:00+00:00",
            "fallback_used": False, "source": "alpaca_dynamic_snapshot",
            "metadata": {"AAA": {"return_20d_pct": 80.0, "atr_pct_14": 12.0, "return_5d_pct": 5.0},
                         "BBB": {"return_20d_pct": 2.0, "atr_pct_14": 3.0, "return_5d_pct": 0.0}},
            "summary": {"excluded_for_filters": 100, "excluded_for_data": 10}}
    p = tmp_path / "snap.json"
    p.write_text(json.dumps(snap))
    monkeypatch.setattr(AUD, "SNAPSHOT", p)
    res = AUD.build()
    sl = res["selection_logic"]
    # the audit must report the real liquidity-dominated, RS/theme-blind ranking
    assert sl["weights"]["liquidity"] == 0.45
    assert sl["relative_strength_component"] is False
    assert sl["theme_component"] is False
    assert sl["sector_theme_diversity_enforced"] is False
    assert "DYNAMIC" in res["static_or_dynamic"]
    # AAA is +80%/20d ⇒ counted as late/extended
    assert res["late_bias"]["n_up_ge_50pct_20d"] == 1


# ── Task 2: stage classifier ─────────────────────────────────────────────────

def test_stage_classifier_marks_parabolic_and_late():
    assert UDS.classify_stage(_f(r5=0.30)) == "PARABOLIC"
    assert UDS.classify_stage(_f(r5=0.05, r10=0.10, r20=0.40, ext_ema20=0.25,
                                 ext_sma50=0.40)) == "LATE_EXTENDED"
    assert UDS.classify_stage(_f(above_sma50=False, r60=-0.20, r20=-0.15)) == "BROKEN"


def test_stage_classifier_marks_early_and_emerging():
    early = _f(vol_expansion=1.5, range10_pct=0.10, ext_ema20=0.03, r20=0.08,
               higher_lows=True, above_ema20=True, ema20_rising=True, rs20_vs_spy=0.01,
               near_20d_high=False, above_sma50=True, r60=0.05)
    assert UDS.classify_stage(early) == "EARLY_ACCUMULATION"
    emerging = _f(rs20_vs_spy=0.05, r20=0.08, above_ema20=True, ema20_rising=True,
                  ext_ema20=0.05, near_20d_high=False, r60=0.06)
    assert UDS.classify_stage(emerging) == "EMERGING_MOMENTUM"


# ── Task 3: early-leader score ───────────────────────────────────────────────

def test_early_leader_score_rewards_accumulation_no_extension():
    f = _f(vol_expansion=1.5, ext_ema20=0.04, ext_sma50=0.06, above_ema20=True,
           ema20_rising=True, higher_lows=True, range10_pct=0.10,
           rs20_vs_spy=0.09, rs40_vs_spy=0.05, bars=250, avg_dvol_20=5e7, rsi14=58)
    f["_stage"] = "EARLY_ACCUMULATION"
    out = UDS.early_leader_score(f, theme_state="LEADING")
    assert out["early_leader_score"] >= 55
    assert out["late_extension_score"] <= 25
    assert "VOL_EXP_NO_EXTENSION" in out["reason_codes"]
    assert "LEADING_THEME" in out["reason_codes"]


def test_early_leader_score_penalizes_late_parabolic():
    f = _f(r5=0.30, ext_ema20=0.40, ext_sma50=0.55, rsi14=88, rs20_vs_spy=0.05)
    f["_stage"] = "PARABOLIC"
    out = UDS.early_leader_score(f, theme_state="LEADING")
    assert out["late_extension_score"] >= 80
    assert "PARABOLIC_5D" in out["reason_codes"]
    # the late penalty must drag the early score well below the accumulation case
    clean = _f(vol_expansion=1.5, ext_ema20=0.04, above_ema20=True, ema20_rising=True,
               higher_lows=True, range10_pct=0.10, rs20_vs_spy=0.09, rs40_vs_spy=0.05)
    clean["_stage"] = "EARLY_ACCUMULATION"
    assert out["early_leader_score"] < UDS.early_leader_score(clean, "LEADING")["early_leader_score"]


# ── Task 4: dynamic universe preserves the liquidity filter ──────────────────

def test_features_enforces_liquidity_filter(monkeypatch):
    from research.scanner_truth import dataio
    cal = pd.date_range("2025-01-01", periods=120, freq="B")
    # illiquid: tiny volume ⇒ features() must reject (returns None)
    illiquid = pd.DataFrame({"open": [50.0] * 120, "high": [50.5] * 120,
                             "low": [49.5] * 120, "close": [50.0] * 120,
                             "volume": [100] * 120}, index=cal)
    monkeypatch.setattr(dataio, "load_prices", lambda t, prefer_deep=True: illiquid)
    spy = pd.Series([100.0] * 120, index=cal)
    assert UDS.features("ILQ", cal, 119, spy, spy, {}) is None


def test_features_no_lookahead(monkeypatch):
    from research.scanner_truth import dataio
    cal = pd.date_range("2025-01-01", periods=160, freq="B")
    series = [50.0] * 100 + list(np.linspace(50, 150, 60))     # flat then ramp
    df = pd.DataFrame({"open": series, "high": np.array(series) * 1.01,
                       "low": np.array(series) * 0.99, "close": series,
                       "volume": [3_000_000] * 160}, index=cal)
    monkeypatch.setattr(dataio, "load_prices", lambda t, prefer_deep=True: df)
    spy = pd.Series(np.linspace(100, 103, 160), index=cal)
    f = UDS.features("WIN", cal, 99, spy, spy, {})       # as-of BEFORE the ramp
    assert f is not None
    assert abs(f["r20"]) < 0.01 and abs(f["r60"]) < 0.01  # no future leakage


def test_propose_universe_preserves_liquidity_and_caps_size():
    # synthetic scan: all entries already passed features() liquidity, so the
    # proposed set is a subset of scan and never exceeds BASE_LIMIT.
    scan = {}
    for i in range(50):
        f = _f(ticker=f"T{i}", avg_dvol_20=1e8 - i * 1e5, rs20_vs_spy=0.2 - i * 0.001,
               early_leader_score=90 - i, stage_label="EMERGING_MOMENTUM",
               theme="semiconductors", sector="Technology")
        f["early_leader_score"] = 90 - i
        f["stage_label"] = "EMERGING_MOMENTUM"
        scan[f"T{i}"] = f
    out = UDS._propose_universe(scan, {"semiconductors": "LEADING"})
    assert set(out["selected"]) <= set(scan)          # liquidity preserved (subset)
    assert len(out["selected"]) <= UDS.BASE_LIMIT
    assert out["bucket_counts"]["core_liquid"] >= 1


# ── Task 7: historizer ───────────────────────────────────────────────────────

def _scan2():
    return {
        "AAA": _f(ticker="AAA", early_leader_score=70, late_extension_score=10,
                  accumulation_score=18, relative_strength_score=20,
                  stage_label="EMERGING_MOMENTUM", reason_codes=["RS20_POS"],
                  data_quality_score=12),
        "BBB": _f(ticker="BBB", early_leader_score=20, late_extension_score=80,
                  accumulation_score=2, relative_strength_score=5,
                  stage_label="PARABOLIC", reason_codes=["PARABOLIC_5D"],
                  data_quality_score=8),
    }


def test_universe_historizer_writes_both_versions_and_is_idempotent(monkeypatch, tmp_path):
    from research.scanner_truth import dataio
    hist = tmp_path / "uni_hist.jsonl"
    monkeypatch.setattr(UDS, "HISTORY_PATH", hist)
    scan = _scan2()
    proposed = {"selected": {"AAA": "rs_leaders"}}        # proposed includes AAA only
    production_ordered = ["BBB", "AAA"]                    # production includes both (BBB rank 1)
    out = UDS.historize(scan, proposed, production_ordered, "2026-05-26")
    rows = dataio.read_jsonl(hist)
    # union {AAA,BBB} × 2 versions = 4 rows
    assert out["rows_written"] == len(rows) == 4
    prod = {r["ticker"]: r for r in rows if r["universe_version"] == UDS.PRODUCTION_VERSION}
    prop = {r["ticker"]: r for r in rows if r["universe_version"] == UDS.PROPOSED_VERSION}
    # production includes both; BBB is production rank 1
    assert prod["AAA"]["included"] and prod["BBB"]["included"]
    assert prod["BBB"]["rank"] == 1
    # proposed includes AAA only
    assert prop["AAA"]["included"] is True and prop["AAA"]["selection_bucket"] == "rs_leaders"
    assert prop["BBB"]["included"] is False
    # the dual-version ledger carries the spec fields
    for r in rows:
        assert set(r) >= {"universe_version", "included", "rank", "selection_bucket",
                          "early_leader_score", "relative_strength_score",
                          "accumulation_score", "late_extension_score", "data_quality_score"}
    # idempotent per (date, version)
    assert UDS.historize(scan, proposed, production_ordered, "2026-05-26")["already_present"] is True


def test_production_and_proposed_ledgers_separate(monkeypatch, tmp_path):
    from research.scanner_truth import dataio
    hist = tmp_path / "uni_hist.jsonl"
    monkeypatch.setattr(UDS, "HISTORY_PATH", hist)
    UDS.historize(_scan2(), {"selected": {"AAA": "rs_leaders"}}, ["BBB", "AAA"], "2026-05-26")
    rows = dataio.read_jsonl(hist)
    versions = {r["universe_version"] for r in rows}
    assert versions == {UDS.PRODUCTION_VERSION, UDS.PROPOSED_VERSION}
    # each (version, ticker) appears exactly once
    keys = [(r["universe_version"], r["ticker"]) for r in rows]
    assert len(keys) == len(set(keys))


# ── Phase 1G.8: forward A/B replay ───────────────────────────────────────────

def _seed_ledger(path, asof, prod_incl, prop_incl):
    import json as _j
    rows = []
    union = list(dict.fromkeys(prod_incl + prop_incl))
    for t in union:
        rows.append({"asof_date": asof, "universe_version": UDS.PRODUCTION_VERSION,
                     "ticker": t, "included": t in prod_incl, "rank": 1,
                     "stage_label": "BREAKOUT_CONFIRMED", "sector": "Technology",
                     "theme": "semiconductors"})
        rows.append({"asof_date": asof, "universe_version": UDS.PROPOSED_VERSION,
                     "ticker": t, "included": t in prop_incl, "rank": 1,
                     "stage_label": "EMERGING_MOMENTUM", "sector": "Technology",
                     "theme": "semiconductors"})
    path.write_text("\n".join(_j.dumps(r) for r in rows) + "\n")


def test_forward_ab_excludes_immature_and_needs_more_data(monkeypatch, tmp_path):
    from research.scanner_truth import dataio
    cal = pd.date_range("2025-01-01", periods=140, freq="B")
    df = pd.DataFrame({"open": [50.0] * 140, "high": [50.0] * 140, "low": [50.0] * 140,
                       "close": [50.0] * 140, "volume": [3e6] * 140}, index=cal)
    monkeypatch.setattr(dataio, "load_prices", lambda t, prefer_deep=True: df)
    monkeypatch.setattr(dataio, "benchmark_calendar", lambda: cal)
    monkeypatch.setattr(dataio, "all_price_tickers", lambda: ["SPY", "QQQ", "WIN"])
    hist = tmp_path / "led.jsonl"
    _seed_ledger(hist, str(cal[-1])[:10], ["WIN"], ["WIN"])    # as-of today ⇒ immature
    monkeypatch.setattr(UFR, "HISTORY_PATH", hist)
    monkeypatch.setattr(UFR, "WINNER_UNI", tmp_path / "missing.json")
    monkeypatch.setattr(UFR, "DOCS_DIR", tmp_path)
    res = UFR.build()
    assert all(d["matured_dates"] == 0 for d in res["forward_ab"].values())
    assert res["verdict"] == "NEED_MORE_DATA"


def test_proposed_cannot_promote_without_sample_gates():
    # tiny mature sample ⇒ gates must report NEED_MORE_DATA, never auto-promote
    ab = {"matured_days": 2, "ticker_days": {"production": 100, "proposed_dynamic": 100},
          "by_horizon": {"20d": {"production": {}, "proposed_dynamic": {}}}}
    recall = {"production": {}, "proposed_dynamic": {}}
    quality = {"production": {"leading_theme_members": 5}, "proposed": {"leading_theme_members": 6}}
    g = UFR._gates(ab, recall, quality)
    assert g["status"] == "NEED_MORE_DATA"
    assert g["all_pass"] is False
    assert UFR._verdict(ab, recall, g) == "NEED_MORE_DATA"


def test_score_gate_audit_identifies_killed_early_leaders(monkeypatch, tmp_path):
    from research.scanner_truth import dataio
    cal = pd.date_range("2025-01-01", periods=120, freq="B")
    # short flat history ⇒ voyager needs 260 bars (fails), sniper finds no breakout
    df = pd.DataFrame({"open": [50.0] * 120, "high": [50.2] * 120, "low": [49.8] * 120,
                       "close": [50.0] * 120, "volume": [3e6] * 120}, index=cal)
    monkeypatch.setattr(dataio, "load_prices", lambda t, prefer_deep=True: df)
    monkeypatch.setattr(dataio, "benchmark_calendar", lambda: cal)
    ledger = {"2026-05-26": {UDS.PROPOSED_VERSION: [
        {"ticker": "WIN", "stage_label": "EMERGING_MOMENTUM"},
        {"ticker": "WIN2", "stage_label": "EARLY_ACCUMULATION"}]}}
    out = UFR._score_gate_audit(ledger, cal)
    assert out["status"] == "ok"
    assert out["n_proposed_early_leaders_evaluated"] == 2
    # flat short series ⇒ all killed by both structural gates
    assert out["killed_by_both_gates"] == 2 and out["killed_pct"] == 100.0
    assert "recommendation" in out and "BYPASS" in out["recommendation"].upper()


# ── research-only invariants ─────────────────────────────────────────────────

def test_no_execution_governance_or_signal_imports():
    forbidden = ("from execution", "import execution", "from council", "import council",
                 "paper_governance", "order_manager", "submit_order", "strategy_registry",
                 "from strategies", "import strategies", "decision_logger",
                 "ALLOW_LIVE_CAPITAL", "PAPER_TRADING", "ALPACA_PAPER")
    for n in _1G7B_FILES:
        src = (REPO / "research" / f"{n}.py").read_text()
        for tok in forbidden:
            assert tok not in src, f"{n}.py must not reference {tok!r}"


def test_no_db_mutation():
    for n in _1G7B_FILES:
        src = (REPO / "research" / f"{n}.py").read_text().upper()
        for tok in ("INSERT INTO", "DELETE FROM", "DROP TABLE", "CREATE TABLE",
                    "ALTER TABLE", "UPDATE ", "INSERT OR REPLACE"):
            assert tok not in src, f"{n}.py must not mutate the DB ({tok})"

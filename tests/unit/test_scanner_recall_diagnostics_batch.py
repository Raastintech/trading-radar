"""
tests/unit/test_scanner_recall_diagnostics_batch.py — multi-date batch
aggregate of the scanner recall diagnostics.

Covers: multi-date scan equivalence with the single-run world, cohort
scoreboard answers, filter-stability trust gating (min rejects/date, min
evidence dates), named-combo superset matching, winsorized outlier control,
single-vs-combination block classification, sector split, the rule-based
decision section + evidence-strength cap, artifact writing, and the
research-only source invariants.
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

from research import scanner_recall_diagnostics as SRD  # noqa: E402
from research import scanner_recall_diagnostics_batch as BATCH  # noqa: E402
from tests.unit.test_scanner_recall_diagnostics import (  # noqa: E402
    LOOKBACK, N_BARS, _mk, _world)

GRID = [21, 26]      # two matured as-of dates within the synthetic calendar


def test_batch_runs_grid_and_matches_single_run(monkeypatch, tmp_path):
    _world(monkeypatch, tmp_path)
    res = BATCH.build(grid=GRID)
    assert res["n_dates"] == 2
    assert [d["lookback_td"] for d in res["dates"]] == GRID
    # the lb=21 date must equal the single-run world's cohort membership
    single = SRD.build(lookback_td=LOOKBACK)
    batch_d = res["dates"][0]
    assert batch_d["asof_date"] == single["asof_date"]
    for c in ("strict_scanner", "rs_baseline", "loose_scanner"):
        assert (batch_d["cohorts"][c]["members_by_fwd20"]
                == single["cohorts"][c]["members_by_fwd20"])


def test_grid_lookbacks_too_deep_are_skipped(monkeypatch, tmp_path):
    _world(monkeypatch, tmp_path)
    res = BATCH.build(grid=[21, 5000])
    assert res["params"]["grid_skipped"] == [5000]
    assert res["n_dates"] == 1


def test_cohort_scoreboard_answers_shape(monkeypatch, tmp_path):
    _world(monkeypatch, tmp_path)
    res = BATCH.build(grid=GRID)
    ans = res["cohort_scoreboard"]["answers"]
    for q in ("q1_strict_beats_random", "q2_strict_beats_simple_rs",
              "q3_loose_improves_recall_without_destroying_edge"):
        assert isinstance(ans[q]["answer"], bool)
    assert ans["n_dates"] == 2


def test_filter_stability_trust_gating(monkeypatch, tmp_path):
    """With a tiny universe every filter has < MIN_REJECTS_PER_DATE rejects,
    so nothing is trusted and everything lands in insufficient_evidence."""
    _world(monkeypatch, tmp_path)
    res = BATCH.build(grid=GRID)
    assert all(not f["trusted"] for f in res["filter_stability"])
    dec = res["decision"]
    assert dec["filters_to_keep_strict"] == []
    assert dec["filters_to_test_in_loose_experiment"] == []
    assert set(dec["insufficient_evidence"]) == {
        f"{f['lane']}:{f['filter']}" for f in res["filter_stability"]}
    # 2 dates < MIN_EVIDENCE_DATES ⇒ capped INSUFFICIENT
    assert dec["evidence_strength"] == "INSUFFICIENT"
    assert "no ticker promoted" in dec["guardrails"].replace("is ", "")


def test_named_combos_superset_match(monkeypatch, tmp_path):
    _world(monkeypatch, tmp_path)
    res = BATCH.build(grid=GRID)
    combos = {c["combo"]: c for c in res["filter_combos"]}
    assert set(combos) == {"+".join(c) for c in BATCH.NAMED_COMBOS}
    # REJWIN fails too_extended AND no_breakout on both dates ⇒ pooled n ≥ 2.
    te_nb = combos["too_extended+no_breakout"]
    assert te_nb["pooled_n"] >= 2
    assert te_nb["winners_missed_total"] >= 2
    # REJWIN is Technology ⇒ ex-healthcare pool includes it.
    assert te_nb["ex_healthcare_n"] >= 2


def test_winsor_mean_tames_outliers():
    vals = [0.01] * 18 + [0.02, 5.0]           # one +500% outlier
    w = SRD._winsor_mean(vals)
    assert w < float(np.mean(vals))             # winsorized < raw mean
    assert w > 0
    assert SRD._winsor_mean([]) is None


def test_mae20_is_worst_forward_excursion():
    cal = pd.date_range("2025-01-01", periods=40, freq="B")
    c = pd.Series([100.0] * 10 + [90.0] + [120.0] * 29, index=cal)
    assert SRD._mae20(c, 9) == pytest.approx(-0.10)   # dip to 90 then rip


def test_block_structure_classification():
    dates = [{
        "asof_date": "2026-01-01",
        "top_rejected": [
            {"ticker": "ONE", "fwd20_pct": 30.0, "sector": "Technology",
             "theme": "other",
             "reject_reasons": ["production_structural:too_extended", "production_breakout:no_breakout",
                                "production_breakout:volume_insufficient"]},   # voy lane = 1 fail
            {"ticker": "MANY", "fwd20_pct": 25.0, "sector": "Healthcare",
             "theme": "biotech_healthcare",
             "reject_reasons": ["production_structural:too_extended", "production_structural:dvol_fading",
                                "production_breakout:no_breakout", "production_breakout:ma50_not_rising"]},
            {"ticker": "LOSER", "fwd20_pct": -5.0, "sector": "Energy",
             "theme": "other", "reject_reasons": ["production_structural:too_extended"]},
        ],
    }]
    rw = BATCH._rejected_winner_analysis(dates, 0.10)
    assert rw["block_structure"]["single_filter_blocks"] == 1    # ONE
    assert rw["block_structure"]["combination_blocks"] == 1      # MANY
    assert rw["distinct_top_rejected_winners"] == 2              # LOSER below thresh
    assert rw["sector_split"] == {"healthcare_biotech": 1, "non_healthcare": 1,
                                  "healthcare_frac": 0.5}


def test_repeat_winner_tracking():
    entry = {"ticker": "REP", "fwd20_pct": 40.0, "sector": "Technology",
             "theme": "other", "reject_reasons": ["production_structural:too_extended",
                                                  "production_breakout:no_breakout"]}
    dates = [{"asof_date": "2026-01-01", "top_rejected": [entry]},
             {"asof_date": "2026-02-01", "top_rejected": [dict(entry)]}]
    rw = BATCH._rejected_winner_analysis(dates, 0.10)
    assert rw["repeat_winners"]["REP"]["appearances"] == 2
    assert rw["repeat_winners"]["REP"]["filters"] == ["no_breakout", "too_extended"]


def test_decision_rules_buckets():
    def frow(name, lane, ev, frac, med, x_ev=0, x_frac=None, x_med=None):
        return {"filter": name, "lane": lane, "evidence_dates": ev,
                "trusted": ev >= BATCH.MIN_EVIDENCE_DATES,
                "pooled_median_fwd20_pct": med, "beats_spy_frac": frac,
                "ex_healthcare": {"evidence_dates": x_ev, "beats_spy_frac": x_frac,
                                  "pooled_median_fwd20_pct": x_med}}
    stability = [
        frow("below_ma200_floor", "production_structural", 8, 0.8, 5.0, 8, 0.7, 4.0),  # test_loose
        frow("dvol_fading", "production_structural", 8, 0.8, 5.0, 8, 0.2, -1.0),       # sector flip
        frow("ma50_not_rising", "production_breakout", 8, 0.6, 2.0),                  # investigate
        frow("too_extended", "production_structural", 8, 0.2, -2.0),                   # keep + not safe
        frow("insufficient_history_75", "production_breakout", 1, 1.0, 9.0),          # untrusted
    ]
    dec = BATCH._decide(stability, n_dates=12)
    assert dec["filters_to_test_in_loose_experiment"] == ["production_structural:below_ma200_floor"]
    assert any(x.startswith("production_structural:dvol_fading") for x in dec["filters_to_investigate"])
    assert "production_breakout:ma50_not_rising" in dec["filters_to_investigate"]
    assert "production_structural:too_extended" in dec["filters_to_keep_strict"]
    assert "production_structural:too_extended" in dec["filters_not_safe_to_loosen"]
    assert dec["insufficient_evidence"] == ["production_breakout:insufficient_history_75"]
    assert dec["sector_flip_filters"] == ["production_structural:dvol_fading"]
    assert dec["evidence_strength"] == "MIXED"                # flip ⇒ capped


def test_evidence_strength_ladder():
    assert BATCH._decide([], n_dates=2)["evidence_strength"] == "INSUFFICIENT"
    assert BATCH._decide([], n_dates=6)["evidence_strength"] == "WEAK"
    assert BATCH._decide([], n_dates=12)["evidence_strength"] == "STRONG"


def test_main_writes_batch_artifacts(monkeypatch, tmp_path, capsys):
    _world(monkeypatch, tmp_path)
    assert BATCH.main(["--grid", "21,26"]) == 0
    j = json.loads(
        (tmp_path / "scanner_recall_diagnostics_batch_latest.json").read_text())
    assert j["artifact_version"] == BATCH.ARTIFACT_VERSION
    assert j["verdict"] == "DIAGNOSTIC_ONLY"
    assert j["n_dates"] == 2
    txt = (tmp_path / "scanner_recall_diagnostics_batch_latest.txt").read_text()
    for marker in ("ANSWERS:", "FILTER STABILITY", "FILTER COMBOS",
                   "DECISION", "DIAGNOSTIC ONLY"):
        assert marker in txt


def test_short_calendar_errors_cleanly(monkeypatch, tmp_path):
    from research.scanner_truth import dataio
    _world(monkeypatch, tmp_path)
    monkeypatch.setattr(dataio, "benchmark_calendar",
                        lambda: pd.date_range("2025-01-01", periods=40, freq="B"))
    res = BATCH.build(grid=GRID)
    assert "error" in res
    assert BATCH._render_txt(res)[0].endswith("==")


def test_source_has_no_forbidden_imports():
    src = (REPO / "research" / "scanner_recall_diagnostics_batch.py").read_text()
    for banned in ("core.order", "execution", "paper_governance",
                   "strategy_registry", "alpaca", "requests", "sqlite3"):
        assert banned not in src, f"forbidden reference: {banned}"

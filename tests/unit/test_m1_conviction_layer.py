"""Guards for the M1 Conviction Layer (research/backtests).

This study asks whether ranking inside an already-working pool improves the
per-name experience. Three things must hold mechanically:

1. M1's definition is FROZEN — imported from the tournament, never restated,
   so the source pool cannot drift between studies.
2. Overlay discovery never touches the holdout. 2025 was already spent
   confirming M1, so the walk-forward steps carry the verdict and the
   discovery input must be train-only.
3. The per-name (ticker-clustered) bar is HARD for a conviction verdict. The
   whole study exists because M1's edge is a basket property; an overlay that
   improves the basket and leaves the typical name alone has not answered it.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import research.backtests.m1_conviction_layer as C
from research.backtests.alpha_tournament import FAMILIES
from research.backtests.common import (
    CONVICTION_VERDICTS,
    FORBIDDEN_LIVE_VERDICTS,
    ReplayWriteViolation,
    assert_conviction_verdict,
    assert_replay_write_path,
    is_replay_path,
)


# ── containment and ladder ──────────────────────────────────────────────────

def test_paths_are_inside_the_replay_namespace():
    for rel in (C.POOL_REL, C.INSIDE_REL, C.OVERLAYS_REL, C.LATEST_REL,
                C.REPORT_TXT_REL):
        assert is_replay_path(rel), rel


@pytest.mark.parametrize("live", [
    "cache/research/research_scanner_latest.json",
    "cache/research/alpha_focus_latest.json", "db/trading.db",
])
def test_cannot_write_live_artifacts(live):
    with pytest.raises(ReplayWriteViolation):
        assert_replay_write_path(live)


def test_conviction_ladder_is_disjoint_from_the_live_ladder():
    assert not (CONVICTION_VERDICTS & FORBIDDEN_LIVE_VERDICTS)
    with pytest.raises(ValueError):
        assert_conviction_verdict("VALIDATED_EDGE")


# ── the frozen pool ─────────────────────────────────────────────────────────

def test_m1_is_imported_not_restated():
    """A second copy of the rule would let the two studies drift apart."""
    tournament_m1 = next(f for f in FAMILIES if f["name"] == "momentum_12_1_raw")
    assert C.M1 is tournament_m1


def test_m1_rule_is_the_documented_one():
    df = pd.DataFrame({
        "scan_date": [pd.Timestamp("2022-01-07")] * 10,
        "mom_12_1": list(range(10)),
        "price": [10.0] * 10,
    })
    kept = df[C.M1["mask"](df).fillna(False)]
    # rank(pct=True) >= 0.80 includes the name sitting exactly on the boundary,
    # so ten names yield three rather than two. Immaterial at the ~600 names a
    # real scan date carries, but pinned here so the boundary is documented.
    assert len(kept) == 3
    assert set(kept["mom_12_1"]) == {7, 8, 9}


def test_metadata_join_must_not_change_pool_size():
    """An inner join would silently shrink M1 to the fundamentals-covered
    subset and quietly redefine what the pool means."""
    base = pd.DataFrame({
        "ticker": ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J"],
        "scan_date": [pd.Timestamp("2022-01-07")] * 10,
        "mom_12_1": list(range(10)), "price": [10.0] * 10,
    })
    # Fundamentals cover only two of the three names the rule keeps.
    fund = pd.DataFrame({"ticker": ["I", "J"],
                         "scan_date": [pd.Timestamp("2022-01-07")] * 2,
                         "roe_pct": [12.0, 15.0]})
    pool = C.m1_pool({"full": base, "fund": fund, "event": pd.DataFrame()})
    assert len(pool) == 3                      # the top quintile, not the join
    assert "roe_pct" in pool.columns
    assert pool["roe_pct"].notna().sum() == 2  # the third keeps a null, not a drop


def test_metadata_join_keeps_uncovered_names_with_nulls():
    base = pd.DataFrame({
        "ticker": [f"T{i}" for i in range(10)],
        "scan_date": [pd.Timestamp("2022-01-07")] * 10,
        "mom_12_1": list(range(10)), "price": [10.0] * 10,
    })
    fund = pd.DataFrame({"ticker": ["T9"], "scan_date": [pd.Timestamp("2022-01-07")],
                         "roe_pct": [12.0]})
    pool = C.m1_pool({"full": base, "fund": fund, "event": pd.DataFrame()})
    assert len(pool) == 3 and pool["roe_pct"].isna().sum() == 2


# ── train-only discipline ───────────────────────────────────────────────────

def test_discovery_refuses_holdout_rows():
    df = pd.DataFrame({"year": [2022, 2023, 2025]})
    with pytest.raises(C.TrainOnlyViolation):
        C._assert_train_only(df)


def test_discovery_accepts_train_rows():
    C._assert_train_only(pd.DataFrame({"year": [2022, 2023, 2024]}))


def test_walk_forward_steps_stay_inside_the_train_window():
    for train_y, test_y in C.WALK_FORWARD_STEPS:
        assert train_y in C.TRAIN_YEARS and test_y in C.TRAIN_YEARS
        assert test_y == train_y + 1


# ── within-pool labelling ───────────────────────────────────────────────────

def test_pool_labels_rank_within_the_pool_not_the_universe():
    """A name can be a poor performer universe-wide and still be a top-decile
    performer among its momentum peers; the pool labels must say the latter."""
    pool = pd.DataFrame({
        "scan_date": [pd.Timestamp("2022-01-07")] * 20,
        "ticker": [f"T{i}" for i in range(20)],
        **{f"fwd_{h}d": np.linspace(-30, -5, 20) for h in C.HORIZONS},
    })
    out = C.label_within_pool(pool)
    winners = out[out[f"pool_top10pct_{C.PRIMARY_H}d"]]
    assert len(winners) == 2
    assert (winners[f"fwd_{C.PRIMARY_H}d"] < 0).all(), "all names lost; some still won"


def test_catastrophic_and_flat_labels():
    pool = pd.DataFrame({
        "scan_date": [pd.Timestamp("2022-01-07")] * 3,
        "ticker": list("ABC"),
        **{f"fwd_{h}d": [-60.0, 0.5, 40.0] for h in C.HORIZONS},
    })
    out = C.label_within_pool(pool)
    assert list(out["pool_catastrophic"]) == [True, False, False]
    assert list(out["pool_flat"]) == [False, True, False]


# ── the verdict ladder ──────────────────────────────────────────────────────

def _res(**kw):
    base = {
        "pool_reference": {"episodes": 10000, "pct_down_25": 10.0, "pct_up_50": 4.0},
        "train": {
            "top50": {"episodes": 5000, "median_names_per_date": 50,
                      "selection_vs_pool": {"selection_mean": 2.0,
                                            "date_cluster_ci_mean": [1.0, 3.0],
                                            "ticker_cluster_ci": [0.5, 3.0]}},
            "vs_random_top50": {"mean_difference": 2.0, "mean_ci": [1.0, 3.0]},
            "filtered_pool": {"episodes": 6000, "pct_down_25": 8.0, "pct_up_50": 3.6},
        },
        "holdout": {"vs_random_top50": {"mean_difference": 1.5}},
        "walk_forward": [{"mean_difference_vs_random": 1.0},
                         {"mean_difference_vs_random": 1.2}],
    }
    for k, v in kw.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            base[k] = {**base[k], **v}
        else:
            base[k] = v
    return base


RANKER = {"name": "r", "kind": "ranker"}
FILTER = {"name": "f", "kind": "filter"}


def test_a_clean_sweep_earns_the_conviction_verdict():
    v, why = C.verdict_for_overlay(RANKER, _res())
    assert v == "PROMISING_CONVICTION_OVERLAY"
    assert "per-name" in why[0]


def test_per_name_failure_blocks_the_conviction_verdict():
    """The finding of the whole study: overlays improve the basket and leave
    the typical name alone. That must not read as a promotion."""
    r = _res()
    r["train"]["top50"]["selection_vs_pool"]["ticker_cluster_ci"] = [-4.0, 0.5]
    v, why = C.verdict_for_overlay(RANKER, r)
    assert v != "PROMISING_CONVICTION_OVERLAY"
    assert any("ticker-clustered" in x for x in why)


def test_a_filter_that_cuts_the_left_tail_harder_is_an_avoid_filter():
    r = _res()
    r["train"]["top50"]["selection_vs_pool"]["ticker_cluster_ci"] = [-4.0, 0.5]
    r["train"]["filtered_pool"] = {"episodes": 6000, "pct_down_25": 6.0,
                                   "pct_up_50": 3.6}   # -40% left, -10% right
    v, why = C.verdict_for_overlay(FILTER, r)
    assert v == "USEFUL_AVOID_FILTER" and "cuts the -25% rate" in why[0]


def test_a_filter_that_trims_both_tails_equally_is_not_useful():
    """The actual result for every volatility filter tested."""
    r = _res()
    r["train"]["top50"]["selection_vs_pool"]["ticker_cluster_ci"] = [-4.0, 0.5]
    r["train"]["filtered_pool"] = {"episodes": 6000, "pct_down_25": 6.4,
                                   "pct_up_50": 2.5}   # -36% left, -38% right
    v, _ = C.verdict_for_overlay(FILTER, r)
    assert v != "USEFUL_AVOID_FILTER"
    assert r["avoid_profile"]["tails_cut_proportionally"] is True


def test_the_avoid_profile_is_reported_even_when_the_verdict_is_rejection():
    """A filter can fail as a ranker and still owe the reader its tail numbers."""
    r = _res(holdout={"vs_random_top50": {"mean_difference": -2.0}})
    C.verdict_for_overlay(FILTER, r)
    assert "avoid_profile" in r
    assert set(r["avoid_profile"]) >= {"left_tail_cut_pct", "right_tail_cost_pct"}


def test_train_only_effect_is_overfit():
    r = _res(holdout={"vs_random_top50": {"mean_difference": -1.0}})
    r["train"]["filtered_pool"] = {"episodes": 6000, "pct_down_25": 10.0,
                                   "pct_up_50": 4.0}   # no tail reshaping
    v, _ = C.verdict_for_overlay(FILTER, r)
    assert v == "REJECTED_OVERFIT"


def test_random_control_draws_from_the_pool_itself():
    """The control that isolates an overlay: same pool, same dates, same size."""
    pool = pd.DataFrame({
        "scan_date": [pd.Timestamp("2022-01-07")] * 100 + [pd.Timestamp("2022-01-14")] * 100,
        "ticker": [f"T{i}" for i in range(200)],
    })
    picks = C.random_control(pool, 25, seed=1)
    assert len(picks) == 50
    assert set(picks.groupby("scan_date").size()) == {25}
    assert set(picks["ticker"]).issubset(set(pool["ticker"]))


# ── artifacts ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("rel", [
    "cache/research/m1_conviction_pool.json",
    "cache/research/m1_conviction_inside_study.json",
    "cache/research/m1_conviction_overlays.json",
    "cache/research/m1_conviction_latest.json",
])
def test_artifacts_carry_provenance_and_no_live_verdicts(rel):
    p = Path(__file__).resolve().parents[2] / rel
    if not p.exists():
        pytest.skip(f"{rel} not generated in this working tree")
    d = json.loads(p.read_text())
    for flag in ("research_only", "not_live_evidence",
                 "not_backtest_evidence_for_phase4b", "no_recommendation"):
        assert d.get(flag) is True, f"{rel} missing {flag}"
    blob = json.dumps(d)
    for bad in FORBIDDEN_LIVE_VERDICTS:
        assert bad not in blob, f"{rel} contains {bad}"


def test_overlay_artifact_records_the_contamination_ledger():
    p = Path(__file__).resolve().parents[2] / "cache/research/m1_conviction_overlays.json"
    if not p.exists():
        pytest.skip("overlays artifact not generated")
    led = json.loads(p.read_text()).get("contamination_ledger") or {}
    assert "m1_base_pool" in led and "2025" in led["m1_base_pool"]
    assert "walk_forward" in led

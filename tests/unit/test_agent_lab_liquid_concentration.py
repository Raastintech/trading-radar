"""Guards for the Autonomous Alpha Lab (sandbox research package).

These pin the parts of `research/agent_lab/` that are reusable and easy to get
silently wrong: the look-ahead deny-list, the two clustered bootstraps, the
within-date AUC, the top-N selector, and the style-cell leader pool. They read
no cached artifacts and make no provider calls.

The lab is research-only and is imported by no production module; these tests
exist so a future edit cannot quietly reintroduce leakage or a broken CI.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research.agent_lab import lab_stats as S


# ------------------------------------------------------------- deny-list ----

@pytest.mark.parametrize("name", [
    "fwd_60d", "fwd_20d_vs_spy", "mfe_60d", "mae_20d", "top1pct_60d",
    "bot5pct_45d", "top50_60d", "win_up50_45d", "lose_down25_20d",
    "path_clean_trend", "trap_fake_breakout", "excess_vs_vol_decile",
    "fwd_60d_matured",
])
def test_forward_columns_are_rejected(name):
    assert S.is_forward_column(name)


@pytest.mark.parametrize("name", [
    "mom_12_1", "atr14_pct", "dd_from_high_pct", "above_ma200", "price",
    "dvol_med_20", "ma200_slope_20d_pct", "combined_rs", "new_63d_high",
])
def test_as_of_features_are_allowed(name):
    assert not S.is_forward_column(name)


def test_assert_no_lookahead_raises_on_a_leaked_feature():
    with pytest.raises(ValueError, match="look-ahead"):
        S.assert_no_lookahead(["mom_12_1", "fwd_60d"])


def test_assert_no_lookahead_passes_a_clean_set():
    S.assert_no_lookahead(["mom_12_1", "atr14_pct"])  # must not raise


# ------------------------------------------------------------ bootstraps ----

def test_date_clustered_ci_brackets_a_known_mean():
    vals = [1.0] * 50 + [3.0] * 50
    ci = S.date_clustered_ci(vals, reps=500)
    assert ci["point"] == pytest.approx(2.0)
    assert ci["lo"] < 2.0 < ci["hi"]
    assert ci["n_clusters"] == 100


def test_date_clustered_ci_on_a_clearly_positive_sample_excludes_zero():
    ci = S.date_clustered_ci([5.0] * 40, reps=500)
    assert S.ci_positive(ci) and S.ci_excludes_zero(ci)


def test_date_clustered_ci_handles_too_few_clusters():
    ci = S.date_clustered_ci([1.0])
    assert not S.ci_excludes_zero(ci)


def test_ticker_clustered_ci_weights_tickers_not_rows():
    # One ticker contributes 100 strongly positive rows, nine contribute one
    # negative row each. Row-weighted the mean is positive; ticker-weighted the
    # uncertainty must reach well below zero.
    values = [10.0] * 100 + [-1.0] * 9
    tickers = ["HOT"] * 100 + [f"T{i}" for i in range(9)]
    ci = S.ticker_clustered_ci(values, tickers, reps=1000)
    assert ci["point"] > 0
    assert ci["n_clusters"] == 10
    assert ci["lo"] < 0 < ci["hi"]


def test_ticker_clustered_ci_matches_date_ci_when_every_row_is_its_own_ticker():
    vals = list(np.linspace(-1.0, 5.0, 60))
    tick = [f"T{i}" for i in range(60)]
    a = S.ticker_clustered_ci(vals, tick, reps=800, seed=7)
    b = S.date_clustered_ci(vals, reps=800, seed=7)
    assert a["point"] == pytest.approx(b["point"])
    assert a["lo"] == pytest.approx(b["lo"], abs=0.35)


def test_ci_positive_requires_the_whole_interval_above_zero():
    assert S.ci_positive({"lo": 0.1, "hi": 2.0})
    assert not S.ci_positive({"lo": -0.1, "hi": 2.0})
    assert not S.ci_positive({"lo": float("nan"), "hi": float("nan")})


# ------------------------------------------------------------------ AUC -----

def _auc_frame() -> pd.DataFrame:
    rows = []
    for d in pd.date_range("2024-01-05", periods=8, freq="7D"):
        for i in range(10):
            rows.append({"scan_date": d, "feat": float(i), "hit": i >= 8})
    return pd.DataFrame(rows)


def test_rank_auc_detects_a_perfect_separator():
    assert S.rank_auc_within_date(_auc_frame(), "feat", "hit") == pytest.approx(1.0)


def test_rank_auc_is_half_for_a_useless_feature():
    f = _auc_frame()
    rng = np.random.default_rng(0)
    f["noise"] = rng.normal(size=len(f))
    assert S.rank_auc_within_date(f, "noise", "hit") == pytest.approx(0.5, abs=0.2)


def test_rank_auc_skips_dates_with_no_positive_label():
    f = _auc_frame()
    f.loc[f["scan_date"] == f["scan_date"].min(), "hit"] = False
    assert S.rank_auc_within_date(f, "feat", "hit") == pytest.approx(1.0)


# ------------------------------------------------------- cohort mechanics ---

def _panel() -> pd.DataFrame:
    rows = []
    for d in pd.date_range("2024-01-05", periods=4, freq="7D"):
        for i in range(20):
            rows.append({
                "scan_date": d,
                "ticker": f"T{i:02d}",
                "mom_12_1": float(i),
                "style_cell": i % 4,
                "fwd_60d": float(i),
            })
    return pd.DataFrame(rows)


def test_select_top_n_takes_n_per_date_by_descending_score():
    from research.agent_lab.liquid_concentration_lab import _select_top_n
    p = _panel()
    got = _select_top_n(p, p["mom_12_1"], pd.Series(True, index=p.index), 3)
    assert len(got) == 12
    assert got.groupby("scan_date").size().unique().tolist() == [3]
    assert sorted(got["ticker"].unique()) == ["T17", "T18", "T19"]


def test_select_top_n_respects_the_eligibility_mask():
    from research.agent_lab.liquid_concentration_lab import _select_top_n
    p = _panel()
    elig = p["ticker"].isin({"T00", "T01", "T02"})
    got = _select_top_n(p, p["mom_12_1"], elig, 5)
    assert set(got["ticker"]) == {"T00", "T01", "T02"}


def test_style_cell_leader_pool_returns_one_name_per_cell_per_date():
    from research.agent_lab.tiebreak_robustness import cell_leader_pool
    p = _panel()
    pool = cell_leader_pool(p)
    per = pool.groupby(["scan_date", "style_cell"]).size()
    assert per.unique().tolist() == [1]
    # the leader of each cell is its highest-momentum member
    assert sorted(pool["ticker"].unique()) == ["T16", "T17", "T18", "T19"]


def test_preregistration_ladder_matches_the_brief():
    from research.agent_lab import preregistration as PRE
    assert PRE.LADDER == (
        "REJECTED", "OVERFIT", "INCONCLUSIVE", "PROMISING_BUT_UNPROVEN",
        "PROVISIONAL_RESEARCH_CANDIDATE", "VALIDATED_EDGE_CANDIDATE",
    )
    assert set(PRE.VERDICT_RULES) <= set(PRE.LADDER)
    assert PRE.PRIMARY["method"] and PRE.PRIMARY["universe"]


def test_score_bar_awards_the_top_verdict_only_when_every_check_passes():
    from research.agent_lab.holdout_stage import score_bar
    good_ci = {"lo": 1.0, "hi": 3.0}
    strong = {
        "selection_vs_universe_mean_pp": 2.0, "mean_return_pct": 5.0, "median_return_pct": 2.0,
        "portfolio_edge_ci": good_ci, "typical_name_edge_ci": good_ci,
        "controls": {"vs_random_same_date": good_ci, "vs_matched_random": good_ci},
        "conc_largest_sector_share_pct": 20.0, "dates": 53, "unique_tickers": 400,
    }
    train = dict(strong, unique_tickers=900)
    nonov = {"selection_vs_universe_mean_pp": 1.5}
    month = {"still_positive_without_worst_month": True}
    tick = {"selection_without_top_tickers_pp": 1.2}
    out = score_bar({}, train, strong, nonov, nonov, month, month, tick, tick)
    assert out["verdict"] == "VALIDATED_EDGE_CANDIDATE"
    assert out["failed_checks"] == []

    # a single fragility failure must demote it, never silently pass
    bad_tick = {"selection_without_top_tickers_pp": -0.5}
    out2 = score_bar({}, train, strong, nonov, nonov, month, month, bad_tick, bad_tick)
    assert out2["verdict"] != "VALIDATED_EDGE_CANDIDATE"
    assert "not_one_ticker" in out2["failed_checks"]


def test_score_bar_calls_a_train_only_result_overfit():
    from research.agent_lab.holdout_stage import score_bar
    zero_ci = {"lo": -1.0, "hi": 1.0}
    train = {"selection_vs_universe_mean_pp": 5.0, "unique_tickers": 500}
    hold = {
        "selection_vs_universe_mean_pp": -0.5, "mean_return_pct": 1.0, "median_return_pct": -1.0,
        "portfolio_edge_ci": zero_ci, "typical_name_edge_ci": zero_ci,
        "controls": {"vs_random_same_date": zero_ci, "vs_matched_random": zero_ci},
        "conc_largest_sector_share_pct": 20.0, "dates": 53, "unique_tickers": 100,
    }
    nonov = {"selection_vs_universe_mean_pp": -1.0}
    month = {"still_positive_without_worst_month": False}
    tick = {"selection_without_top_tickers_pp": -1.0}
    out = score_bar({}, train, hold, nonov, nonov, month, month, tick, tick)
    assert out["verdict"] == "OVERFIT"

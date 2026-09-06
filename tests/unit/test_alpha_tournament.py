"""Guards for the Alpha Discovery Tournament (research/backtests).

The tournament is an open search across fourteen strategy families, so the
things that can go wrong are different from the earlier studies': a family can
win by being a sector bet, by being one year, by holding five names a week, or
by being scored on the statistic that flatters it. Each of those has a test
here, because each of them actually happened during the build.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import research.backtests.alpha_tournament as T
from research.backtests.common import (
    FORBIDDEN_LIVE_VERDICTS,
    TOURNAMENT_VERDICTS,
    ReplayWriteViolation,
    assert_replay_write_path,
    assert_tournament_verdict,
    is_replay_path,
)


# ── write containment and the verdict ladder ────────────────────────────────

def test_tournament_paths_are_inside_the_replay_namespace():
    for rel in (T.PANEL_REL, T.MAP_REL, T.STRATEGIES_REL, T.LISTS_REL,
                T.LATEST_REL, T.REPORT_TXT_REL):
        assert is_replay_path(rel), rel


@pytest.mark.parametrize("live", [
    "cache/research/research_scanner_latest.json",
    "cache/research/alpha_focus_latest.json",
    "cache/prices/NVDA.parquet", "db/trading.db", "data/research/journal.jsonl",
])
def test_tournament_cannot_write_live_artifacts(live):
    with pytest.raises(ReplayWriteViolation):
        assert_replay_write_path(live)


def test_verdict_ladder_is_disjoint_from_the_live_ladder():
    assert not (TOURNAMENT_VERDICTS & FORBIDDEN_LIVE_VERDICTS)
    with pytest.raises(ValueError):
        assert_tournament_verdict("VALIDATED_EDGE")


def test_every_family_declares_its_rule_and_universe():
    seen = set()
    for f in T.FAMILIES:
        assert f["letter"] not in seen, f"duplicate letter {f['letter']}"
        seen.add(f["letter"])
        assert f["universe"] in ("full", "fund", "event"), f["name"]
        assert f["intent"] in ("alpha", "avoid", "baseline"), f["name"]
        assert f["description"] and f["requires"] is not None
        assert callable(f["mask"]) and callable(f["rank"])


def test_untestable_inputs_are_declared_not_proxied_silently():
    names = {u["name"] for u in T.UNTESTABLE}
    for expected in ("analyst_estimate_revisions",
                     "news_topic_shock_and_social_attention",
                     "options_flow_and_implied_volatility"):
        assert expected in names
    for u in T.UNTESTABLE:
        assert len(u["why"]) > 60


# ── the asymmetry metric, which this study turns on ─────────────────────────

def test_asymmetry_separates_mean_from_median():
    """A right-tail cohort: mostly small losses, occasionally a large gain.

    The previous study would have scored this negative on the median. An
    equal-weight list holding it earns the mean, which is positive.
    """
    a = np.array([-5.0] * 90 + [200.0] * 10)
    out = T.asymmetry(a)
    assert out["median"] < 0 < out["mean"]
    assert out["tail_ratio"] > 1
    assert out["pct_up_100"] == 10.0


def test_asymmetry_flags_a_left_tail_cohort():
    a = np.array([5.0] * 90 + [-90.0] * 10)
    out = T.asymmetry(a)
    assert out["median"] > 0 > out["mean"]
    assert out["tail_ratio"] < 1
    assert out["pct_down_50"] == 10.0


def test_asymmetry_needs_a_real_sample():
    assert T.asymmetry(np.arange(5.0)) is None


# ── tradability screens ─────────────────────────────────────────────────────

def test_fake_liquidity_ratio_catches_block_traded_shells():
    """One 5-million-share print on a 1,000-share-a-day name."""
    n = 80
    vol = np.full(n, 1_000.0)
    vol[60] = 5_000_000.0
    close = np.full(n, 50.0)
    p = {"open": close, "high": close, "low": close, "close": close, "volume": vol,
         "idx": pd.date_range("2022-01-03", periods=n, freq="B").values.astype("datetime64[ns]")}
    f = T.series_features(p)
    assert f["fake_liquidity_ratio"][-1] >= T.FAKE_LIQUIDITY_RATIO


def test_fake_liquidity_ratio_is_near_one_for_a_real_book():
    rng = np.random.default_rng(2)
    n = 80
    vol = rng.uniform(9e5, 1.1e6, n)
    close = np.full(n, 50.0)
    p = {"open": close, "high": close, "low": close, "close": close, "volume": vol,
         "idx": pd.date_range("2022-01-03", periods=n, freq="B").values.astype("datetime64[ns]")}
    assert T.series_features(p)["fake_liquidity_ratio"][-1] < 1.5


def test_security_type_filter_excludes_etps_and_derivatives(tmp_path):
    out = T.security_type_exclusions(tmp_path, ["NVDA", "SPY", "ABCDW", "ABCDU", "BRK.B", "MOG-A"])
    assert "NVDA" not in out
    for sym in ("SPY", "ABCDW", "ABCDU", "BRK.B", "MOG-A"):
        assert sym in out, sym


# ── as-of discipline ────────────────────────────────────────────────────────

def _series(n=400, seed=5):
    rng = np.random.default_rng(seed)
    close = 10.0 * np.exp(np.cumsum(rng.normal(0.001, 0.02, n)))
    return {"idx": pd.date_range("2022-01-03", periods=n, freq="B").values.astype("datetime64[ns]"),
            "open": close * 0.995, "high": close * 1.01, "low": close * 0.99,
            "close": close, "volume": rng.uniform(1e5, 5e5, n)}


def test_every_feature_is_backward_looking():
    p = _series()
    cut = 300
    full = T.series_features(p)
    trunc = T.series_features({k: v[: cut + 1] for k, v in p.items()})
    for col in T.FEATURE_COLS:
        a, b = float(full[col][cut]), float(trunc[col][-1])
        assert (math.isnan(a) and math.isnan(b)) or math.isclose(a, b, rel_tol=1e-9), col


def test_forward_measurement_truncates_at_the_last_bar():
    c = np.array([10.0, 12.0, 6.0])
    ret, matured = T._fwd_point(c, np.array([0]), 60)
    assert not matured[0] and math.isclose(ret[0], -40.0)


def test_no_label_column_is_used_as_a_feature():
    """A label may look at the future; a rule may not. This pins the boundary."""
    label_like = ("fwd_", "mfe_", "mae_", "top", "bot", "win_", "lose_", "path_", "trap_")
    for col in T.FEATURE_COLS:
        assert not col.startswith(label_like), f"{col} looks like a label"


# ── the eight-verdict ladder ────────────────────────────────────────────────

def _res(**kw):
    base = {
        "train": {"episodes": 5000, "dates_covered": 150, "median_names_per_date": 40,
                  "fwd_60d": {"mean": 2.0, "tail_ratio": 1.4},
                  "selection_60d": {"selection_mean": 2.0,
                                    "date_cluster_ci_mean": [1.0, 3.0]},
                  "capture": {"top10pct_60d": {"lift": 1.4},
                              "bot10pct_60d": {"lift": 1.0}},
                  "trap_exposure_pct": 0.1},
        "holdout": {"episodes": 900,
                    "selection_60d": {"selection_mean": 1.5,
                                      "date_cluster_ci_mean": [0.2, 2.8]}},
        "by_regime": {k: {"selection_60d": {"selection_mean": 1.0}} for k in T.REGIMES},
        "by_year": {str(y): {"selection_mean": 1.0} for y in (2022, 2023, 2024, 2025)},
        "matched_twin": {"holdout": {"mean_difference": 1.0}},
        "vs_old_scanner_mean_60d": 2.0,
        "concentration": {"largest_known_sector_share_pct": 30.0,
                          "top2_known_sector_share_pct": 50.0,
                          "top10_ticker_share_pct": 10.0,
                          "years_with_positive_mean_selection": 4, "years_tested": 4},
    }
    for k, v in kw.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            base[k] = {**base[k], **v}
        else:
            base[k] = v
    return base


ALPHA = {"intent": "alpha"}


def test_a_clean_sweep_earns_the_pool_verdict():
    v, why = T.verdict_for(ALPHA, _res())
    assert v == "TRUE_ALPHA_CANDIDATE_POOL" and why == ["clears every pre-declared bar"]


def test_a_sector_bet_cannot_be_a_pool():
    v, why = T.verdict_for(ALPHA, _res(concentration={
        "largest_known_sector_share_pct": 75.0}))
    assert v != "TRUE_ALPHA_CANDIDATE_POOL"
    assert any("one sector" in r for r in why)


def test_two_adjacent_sectors_are_also_a_sector_bet():
    """The commodity family split 57/43 across two sectors and slipped the
    single-sector check on the first run."""
    v, why = T.verdict_for(ALPHA, _res(concentration={
        "largest_known_sector_share_pct": 57.5, "top2_known_sector_share_pct": 100.0}))
    assert v != "TRUE_ALPHA_CANDIDATE_POOL"
    assert any("two sectors" in r for r in why)


def test_one_good_year_cannot_be_a_pool():
    v, why = T.verdict_for(ALPHA, _res(concentration={
        "years_with_positive_mean_selection": 1}))
    assert v != "TRUE_ALPHA_CANDIDATE_POOL"
    assert any("of 4 years" in r for r in why)


def test_a_handful_of_names_is_a_special_situation_not_a_pool():
    """Capacity routes before the clean-sweep check: a pool must fill a list."""
    v, why = T.verdict_for(ALPHA, _res(train={
        **_res()["train"], "median_names_per_date": 4}))
    assert v == "SPECIAL_SITUATION_ONLY"
    assert any("too sparse" in r for r in why)


def test_train_only_effect_is_overfit():
    v, _ = T.verdict_for(ALPHA, _res(holdout={
        "episodes": 900, "selection_60d": {"selection_mean": -1.0,
                                           "date_cluster_ci_mean": [-3.0, 1.0]}}))
    assert v == "REJECTED_OVERFIT"


def test_significant_negative_selection_is_rejected():
    v, _ = T.verdict_for(ALPHA, _res(train={
        **_res()["train"],
        "selection_60d": {"selection_mean": -2.0, "date_cluster_ci_mean": [-3.0, -1.0]}}))
    assert v == "REJECTED_NEGATIVE_SELECTION"


def test_an_avoid_lane_that_reliably_loses_is_an_avoid_trap():
    r = _res(train={**_res()["train"],
                    "selection_60d": {"selection_mean": -4.0,
                                      "date_cluster_ci_mean": [-6.0, -2.0]},
                    "capture": {"top10pct_60d": {"lift": 0.8},
                                "bot10pct_60d": {"lift": 2.0}}},
             holdout={"episodes": 900,
                      "selection_60d": {"selection_mean": -3.0,
                                        "date_cluster_ci_mean": [-5.0, -1.0]}})
    v, _ = T.verdict_for({"intent": "avoid"}, r)
    assert v == "AVOID_TRAP"


def test_an_avoid_lane_that_stops_losing_is_not_an_avoid_list():
    """The trap cohort turned positive in 2025; an avoid list built on it would
    have been a regime bet, and the ladder must say so."""
    r = _res(holdout={"episodes": 900,
                      "selection_60d": {"selection_mean": 5.0,
                                        "date_cluster_ci_mean": [1.0, 9.0]}})
    v, why = T.verdict_for({"intent": "avoid"}, r)
    assert v == "INCONCLUSIVE" and any("did not reliably lose" in x for x in why)


def test_the_baseline_is_never_promoted():
    v, _ = T.verdict_for({"intent": "baseline"}, _res())
    assert v == "INCONCLUSIVE"


# ── published artifacts ─────────────────────────────────────────────────────

@pytest.mark.parametrize("rel", [
    "cache/research/alpha_tournament_opportunity_map.json",
    "cache/research/alpha_tournament_strategies.json",
    "cache/research/alpha_tournament_research_lists.json",
    "cache/research/alpha_tournament_latest.json",
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


# ── the canonical factors the first roster was missing ──────────────────────

def test_momentum_12_1_excludes_the_most_recent_month():
    """12-1 must skip the last ~21 sessions, where short-horizon reversal lives.

    A series that rises for eleven months then falls hard in the last month
    should still score positively on 12-1 while its raw 252-day return sags.
    """
    n = 400
    close = np.concatenate([
        10.0 * (1.004 ** np.arange(n - 21)),          # eleven months up
        10.0 * (1.004 ** (n - 22)) * (0.985 ** np.arange(1, 22)),  # last month down
    ])
    p = {"idx": pd.date_range("2021-06-01", periods=n, freq="B").values.astype("datetime64[ns]"),
         "open": close, "high": close, "low": close, "close": close,
         "volume": np.full(n, 1e6)}
    f = T.series_features(p)
    assert f["mom_12_1"][-1] > f["ret_252d"][-1], "12-1 must exclude the recent drop"
    assert f["mom_12_1"][-1] > 0


def test_momentum_12_1_matches_its_definition():
    n = 400
    rng = np.random.default_rng(11)
    close = 20.0 * np.exp(np.cumsum(rng.normal(0.0005, 0.015, n)))
    p = {"idx": pd.date_range("2021-06-01", periods=n, freq="B").values.astype("datetime64[ns]"),
         "open": close, "high": close, "low": close, "close": close,
         "volume": np.full(n, 1e6)}
    f = T.series_features(p)
    # 12-1 is the return from t-253 to t-22: twelve months, last month removed.
    expected = close[-22] / close[-253] - 1.0
    assert math.isclose(f["mom_12_1"][-1], expected * 100, rel_tol=1e-9)


def test_up_down_volume_reflects_where_the_volume_traded():
    """Rising on heavy volume and falling on light volume is accumulation."""
    n = 120
    close = np.empty(n)
    vol = np.empty(n)
    price = 10.0
    for i in range(n):
        up = i % 2 == 0
        price *= 1.02 if up else 0.99
        close[i] = price
        vol[i] = 3e6 if up else 1e6
    p = {"idx": pd.date_range("2022-01-03", periods=n, freq="B").values.astype("datetime64[ns]"),
         "open": close, "high": close, "low": close, "close": close, "volume": vol}
    assert T.series_features(p)["up_down_volume_63"][-1] > 2.0


def test_quality_and_valuation_features_are_computed_from_statements():
    rows = [{"revenue": 100e6, "grossProfit": 60e6, "operatingIncome": 20e6,
             "netIncome": 15e6, "freeCashFlow": 12e6, "epsDiluted": 1.5,
             "totalAssets": 200e6, "totalStockholdersEquity": 100e6,
             "totalDebt": 50e6, "cashAndShortTermInvestments": 30e6,
             "totalCurrentAssets": 80e6, "totalCurrentLiabilities": 40e6,
             "commonStockRepurchased": -5e6, "commonDividendsPaid": -3e6}] * 8
    out = T._fund2_features(rows, mcap=1e9)
    assert math.isclose(out["roe_pct"], 60.0, rel_tol=1e-6)        # 60m NI / 100m equity
    assert math.isclose(out["gross_profit_to_assets"], 1.2, rel_tol=1e-6)
    assert math.isclose(out["debt_to_equity"], 0.5, rel_tol=1e-6)
    assert math.isclose(out["fcf_margin_pct"], 12.0, rel_tol=1e-6)
    assert math.isclose(out["fcf_yield_pct"], 4.8, rel_tol=1e-6)   # 48m FCF / 1bn cap
    assert out["ev_to_sales"] > 0 and out["shareholder_yield_pct"] > 0


def test_growth_on_a_negative_earnings_base_is_not_quoted():
    """A swing from -$1 to +$0.10 is not '110% growth'."""
    rows = [{"epsDiluted": 0.10}] * 4 + [{"epsDiluted": -1.0}] * 4
    assert T._fund2_features(rows, mcap=1e9)["eps_growth_yoy_pct"] is None


def test_peer_confirmation_is_computed_within_date_and_sector():
    df = pd.DataFrame({
        "scan_date": [pd.Timestamp("2022-01-07")] * 6,
        "sector_current_meta": ["Tech"] * 3 + ["Energy"] * 3,
        "rs_63d": [10.0, 5.0, -5.0, -1.0, -2.0, -3.0],
    })
    out = T.add_peer_confirmation(df)
    tech = out[out.sector_current_meta == "Tech"]["sector_peer_breadth_pct"]
    energy = out[out.sector_current_meta == "Energy"]["sector_peer_breadth_pct"]
    assert set(np.round(tech, 1)) == {round(200 / 3, 1)}
    assert set(energy) == {0.0}


def test_unknown_sector_gets_no_peer_confirmation():
    df = pd.DataFrame({"scan_date": [pd.Timestamp("2022-01-07")] * 2,
                       "sector_current_meta": ["UNKNOWN", "Tech"],
                       "rs_63d": [5.0, 5.0]})
    out = T.add_peer_confirmation(df)
    assert math.isnan(out.loc[0, "sector_peer_breadth_pct"])


# ── roster coverage and the portfolio-vs-name diagnostic ────────────────────

def test_the_roster_covers_every_declared_family_group():
    """The brief lists twelve groups; a roster that quietly drops one would
    make the tournament's ranking meaningless."""
    archetypes = {f["archetype"] for f in T.FAMILIES}
    for expected in ("Earnings Re-Rating", "Fundamental Acceleration",
                     "Quality Compounder", "Price Momentum", "Breakout / Base",
                     "Institutional Accumulation", "Value + Quality", "Turnaround",
                     "Sector / Theme Leadership", "Event / Catalyst",
                     "Risk / Failure Avoidance"):
        assert expected in archetypes, f"no family covers {expected}"


def test_no_single_archetype_dominates_the_roster():
    """The first roster over-weighted volatility; the ranking must not be an
    artifact of how many families one theme was given."""
    from collections import Counter
    counts = Counter(f["archetype"] for f in T.FAMILIES
                     if f["intent"] == "alpha")
    assert max(counts.values()) <= 4, counts


def test_momentum_is_represented_by_more_than_short_horizon_rs():
    names = {f["name"] for f in T.FAMILIES}
    assert "momentum_12_1_raw" in names and "momentum_12_1_confirmed" in names

"""Guards for the Alpha Reconstruction Lab (research/backtests).

The lab reverse-engineers what historical winners looked like before they won.
Three things about it must hold mechanically rather than by author discipline,
because every one of them is a way to manufacture a result that is not real:

1. It writes only into its own namespace and never touches a live artifact.
2. Explanatory features are as-of. A single forward-looking column would make
   the whole study a description of the answer.
3. It cannot emit a live verdict token, and its own verdicts are earned by a
   pre-declared rule rather than by narration.

The data-integrity guards get tests too: the study's first honest run was
dominated by reverse-split artifacts and by a shell that passed a liquidity
floor on one block trade, and both defects produced fake winners.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import research.backtests.alpha_reconstruction_lab as LAB
from research.backtests.common import (
    FINGERPRINT_VERDICTS,
    FORBIDDEN_LIVE_VERDICTS,
    ReplayWriteViolation,
    assert_fingerprint_verdict,
    assert_replay_write_path,
    is_replay_path,
)


# ── 1. write containment ────────────────────────────────────────────────────

def test_lab_artifact_paths_are_all_inside_the_replay_namespace():
    for rel in (LAB.PANEL_REL, LAB.WINNERS_REL, LAB.AUTOPSY_REL,
                LAB.FINGERPRINTS_REL, LAB.LATEST_REL, LAB.REPORT_TXT_REL):
        assert is_replay_path(rel), f"{rel} is outside the replay namespace"


@pytest.mark.parametrize("live", [
    "cache/research/research_scanner_latest.json",
    "cache/research/high_conviction_alpha_latest.json",
    "cache/research/alpha_focus_latest.json",
    "cache/prices/NVDA.parquet",
    "data/research/journal.jsonl",
    "db/trading.db",
])
def test_lab_cannot_write_live_artifacts(live):
    with pytest.raises(ReplayWriteViolation):
        assert_replay_write_path(live)


def test_a_sibling_directory_sharing_the_prefix_is_not_admitted():
    assert not is_replay_path("cache/research/alpha_reconstruction_intermediatesEVIL/x.json")


# ── 2. verdict ladder ───────────────────────────────────────────────────────

def test_fingerprint_verdicts_are_disjoint_from_the_live_ladder():
    assert not (FINGERPRINT_VERDICTS & FORBIDDEN_LIVE_VERDICTS)


@pytest.mark.parametrize("bad", ["VALIDATED_EDGE", "READY_TO_GATE", "NEED_MORE_DATA",
                                 "PROMISING_RESEARCH_SURFACE", "CORE_ENGINE_CANDIDATE"])
def test_live_verdicts_cannot_be_emitted(bad):
    with pytest.raises(ValueError):
        assert_fingerprint_verdict(bad)


def test_every_registered_fingerprint_declares_its_hypothesis_and_universe():
    for fp in LAB.FINGERPRINTS + [LAB.COMPOSITE]:
        assert fp["hypothesis"], fp["name"]
        assert fp["universe"] in ("full", "fundamentals_covered"), fp["name"]
        assert callable(fp["mask"]) and callable(fp["rank"]), fp["name"]


# ── 3. as-of features ───────────────────────────────────────────────────────

def _series(n=400, start=10.0, drift=0.001, seed=5):
    rng = np.random.default_rng(seed)
    r = rng.normal(drift, 0.02, n)
    close = start * np.exp(np.cumsum(r))
    return {
        "idx": pd.date_range("2022-01-03", periods=n, freq="B").values.astype("datetime64[ns]"),
        "open": close * 0.995,
        "high": close * 1.01,
        "low": close * 0.99,
        "close": close,
        "volume": rng.uniform(1e5, 5e5, n),
    }


def test_features_at_position_ignore_everything_after_it():
    """The property the whole study rests on."""
    p = _series()
    cut = 250
    full = LAB.series_features(p)
    truncated = LAB.series_features({k: v[: cut + 1] for k, v in p.items()})
    for col in LAB.FEATURE_COLS:
        a, b = float(full[col][cut]), float(truncated[col][-1])
        assert (math.isnan(a) and math.isnan(b)) or math.isclose(a, b, rel_tol=1e-9), col


def test_a_future_leak_would_be_caught_by_the_same_comparison():
    """The guard above only means something if it can fail."""
    p = _series()
    cut = 250
    leaked = LAB.series_features(p)
    # A feature that peeks one bar ahead — what a bug would look like.
    leaked["price"] = np.roll(leaked["price"], -1)
    truncated = LAB.series_features({k: v[: cut + 1] for k, v in p.items()})
    assert not math.isclose(float(leaked["price"][cut]), float(truncated["price"][-1]))


def test_known_feature_values_on_a_constant_growth_series():
    n = 300
    close = 10.0 * (1.01 ** np.arange(n))       # exactly +1% a day
    p = {"idx": pd.date_range("2022-01-03", periods=n, freq="B").values.astype("datetime64[ns]"),
         "open": close, "high": close, "low": close, "close": close,
         "volume": np.full(n, 1e6)}
    f = LAB.series_features(p)
    assert math.isclose(f["ret_20d"][-1], (1.01 ** 20 - 1) * 100, rel_tol=1e-6)
    assert f["dist_ma50_pct"][-1] > 0                      # rising series sits above its MA
    assert math.isclose(f["dd_from_high_pct"][-1], 0.0, abs_tol=1e-9)   # at its high
    assert f["days_since_252d_high"][-1] == 0


# ── 4. data-integrity quarantine ────────────────────────────────────────────

def test_reverse_split_artifact_is_quarantined():
    c = np.concatenate([np.full(50, 1000.0), np.full(50, 1.0)])   # 1000x overnight
    integ = LAB._series_integrity(c)
    assert integ["range_ratio"] >= LAB.SPLIT_ARTIFACT_RANGE_RATIO


def test_a_single_impossible_session_is_quarantined():
    c = np.concatenate([np.full(50, 1.0), np.full(50, 6.0)])      # +500% in a session
    assert LAB._series_integrity(c)["max_1d"] >= LAB.SPLIT_ARTIFACT_MAX_1D_RETURN


def test_a_real_multibagger_is_not_quarantined():
    """A genuine 40x over four years must survive: it is the thing being studied."""
    c = 10.0 * (1.0037 ** np.arange(1000))
    integ = LAB._series_integrity(c)
    assert integ["max_1d"] < LAB.SPLIT_ARTIFACT_MAX_1D_RETURN
    assert integ["range_ratio"] < LAB.SPLIT_ARTIFACT_RANGE_RATIO


def test_a_real_catastrophic_single_day_loss_is_kept():
    """A -90% day is a failed trial, not a data defect; deleting those would
    quietly remove real disasters from the control pool."""
    c = np.concatenate([np.full(50, 100.0), np.full(50, 8.0)])
    integ = LAB._series_integrity(c)
    assert integ["min_1d"] < -0.85
    assert integ["max_1d"] < LAB.SPLIT_ARTIFACT_MAX_1D_RETURN
    assert integ["range_ratio"] < LAB.SPLIT_ARTIFACT_RANGE_RATIO


def test_liquidity_floor_uses_the_median_not_the_mean():
    """A shell trading 1,000 shares a day with one 5M-share block is not
    investable, and it was polluting the winner cohort until this changed."""
    vol = np.full(60, 1_000.0)
    vol[40] = 5_000_000.0
    close = np.full(60, 50.0)
    p = {"idx": pd.date_range("2022-01-03", periods=60, freq="B").values.astype("datetime64[ns]"),
         "open": close, "high": close, "low": close, "close": close, "volume": vol}
    f = LAB.series_features(p)
    assert f["dvol_20"][-1] > 1_000_000        # the mean is fooled by the block
    assert f["dvol_med_20"][-1] < 100_000      # the median is not


# ── 5. forward returns are labels, and honest about delisting ───────────────

def test_forward_return_truncates_at_the_final_bar_instead_of_dropping_the_row():
    c = np.array([10.0, 11.0, 12.0, 6.0])      # dies after 4 bars
    ret, matured = LAB._forward_point(c, np.array([0]), 60)
    assert not matured[0], "a 60d horizon cannot have matured on a 4-bar series"
    assert math.isclose(ret[0], -40.0)          # measured to the last traded price


def test_mfe_measures_the_path_not_the_endpoint():
    c = np.array([10.0, 20.0, 10.0])
    assert math.isclose(LAB._forward_mfe(c, np.array([0]), 2)[0], 100.0)
    assert math.isclose(LAB._forward_point(c, np.array([0]), 2)[0][0], 0.0)


# ── 6. winner labels are cross-sectional, per date ──────────────────────────

def test_percentile_winner_labels_are_computed_within_each_scan_date():
    """A rising market must not manufacture winners, nor a falling one erase them."""
    rows = []
    # A crash week where every name loses, and a boom week where every name
    # gains. Within-date ranking must find winners in the first and losers in
    # the second.
    for d, base in (("2022-01-07", -60.0), ("2022-01-14", 30.0)):
        for i in range(200):
            v = base + i * 0.15
            rows.append({"scan_date": pd.Timestamp(d), "ticker": f"T{i}",
                         **{f"fwd_{h}d": v for h in LAB.HORIZONS},
                         **{f"fwd_{h}d_vs_{b}": v
                            for h in (20, 60, 126) for b in ("spy", "qqq", "iwm")}})
    out = LAB.label_winners(pd.DataFrame(rows))
    per_date = out.groupby("scan_date")["top1pct_60d"].sum()
    assert set(per_date) == {2}, "each date contributes its own top 1%"
    # The crash date's winners are still winners despite negative returns.
    crash = out[(out.scan_date == pd.Timestamp("2022-01-07")) & out["top1pct_60d"]]
    assert len(crash) == 2 and (crash["fwd_60d"] < 0).all()


# ── 7. the promotion ladder ─────────────────────────────────────────────────

def _res(train_sel, train_dci, train_tci, hold_sel, hold_dci=(None, None), **kw):
    base = {
        "train": {"episodes": 5000, "dates_covered": 150,
                  "selection_60d": {"selection": train_sel,
                                    "date_cluster_ci": list(train_dci),
                                    "ticker_cluster_ci": list(train_tci)},
                  "trap_exposure": {"cohort_in_score_100_zone_pct": 0.1,
                                    "universe_in_score_100_zone_pct": 1.3}},
        "holdout": {"episodes": 1000,
                    "selection_60d": {"selection": hold_sel,
                                      "date_cluster_ci": list(hold_dci)}},
        "vs_old_scanner_board_60d": {"difference": 2.0},
        "vs_liquid_lowvol_control_60d": {"difference": 0.5},
        "by_regime": {k: {"selection_60d": {"selection": 1.0}} for k in LAB.REGIMES},
    }
    base.update(kw)
    return base


def test_significantly_negative_selection_is_rejected_outright():
    v, _ = LAB.verdict_for(_res(-5.0, (-7.0, -3.0), (-6.0, -3.0), -1.0))
    assert v == "REJECTED_NEGATIVE_SELECTION"


def test_train_only_effect_is_called_overfit():
    v, why = LAB.verdict_for(_res(2.0, (1.0, 3.0), (0.5, 3.5), -0.5))
    assert v == "REJECTED_OVERFIT"
    assert any("holdout" in r for r in why)


def test_one_clustering_alone_is_not_enough():
    """A date-clustered win with a ticker-clustered miss is a composition
    effect, and must not be promoted."""
    v, why = LAB.verdict_for(_res(2.0, (1.0, 3.0), (-1.0, 3.0), 1.0))
    assert v == "INCONCLUSIVE"
    assert any("clustered CIs" in r for r in why)


def test_a_small_sample_cannot_be_promoted():
    r = _res(2.0, (1.0, 3.0), (0.5, 3.5), 1.0)
    r["train"]["episodes"] = 50
    v, why = LAB.verdict_for(r)
    assert v == "INCONCLUSIVE" and any("below" in x for x in why)


def test_carrying_more_trap_exposure_than_the_universe_blocks_promotion():
    r = _res(2.0, (1.0, 3.0), (0.5, 3.5), 1.0)
    r["train"]["trap_exposure"] = {"cohort_in_score_100_zone_pct": 9.0,
                                   "universe_in_score_100_zone_pct": 1.3}
    v, why = LAB.verdict_for(r)
    assert v == "INCONCLUSIVE" and any("score=100" in x for x in why)


def test_a_clean_sweep_earns_the_only_promotable_verdict():
    v, why = LAB.verdict_for(_res(2.0, (1.0, 3.0), (0.5, 3.5), 1.0))
    assert v == "PROMISING_RESEARCH_FINGERPRINT"
    assert why == ["clears every pre-declared promotion rule"]


# ── 8. train / holdout discipline ───────────────────────────────────────────

def test_train_and_holdout_years_are_disjoint_and_cover_the_window():
    assert not set(LAB.TRAIN_YEARS) & set(LAB.HOLDOUT_YEARS)
    assert set(LAB.TRAIN_YEARS) | set(LAB.HOLDOUT_YEARS) == {2022, 2023, 2024, 2025}


def test_regimes_partition_the_same_years():
    covered = [y for yrs in LAB.REGIMES.values() for y in yrs]
    assert sorted(covered) == sorted(set(LAB.TRAIN_YEARS) | set(LAB.HOLDOUT_YEARS))
    assert len(covered) == len(set(covered)), "a year must not sit in two regimes"


def test_untestable_hypotheses_are_declared_rather_than_approximated():
    """Approximating these with today's sector labels would be lookahead."""
    names = {u["name"] for u in LAB.UNTESTABLE_FINGERPRINTS}
    assert "second_order_theme_beneficiary" in names
    for u in LAB.UNTESTABLE_FINGERPRINTS:
        assert len(u["why_untestable"]) > 80, "an untestable claim needs its reason"
    registered = {f["name"] for f in LAB.FINGERPRINTS}
    assert not (names & registered), "an untestable hypothesis must not also be scored"


# ── 9. published artifacts keep their provenance ────────────────────────────

@pytest.mark.parametrize("rel", [
    "cache/research/alpha_reconstruction_winner_universe.json",
    "cache/research/alpha_reconstruction_winner_autopsy.json",
    "cache/research/alpha_reconstruction_fingerprints.json",
    "cache/research/alpha_reconstruction_latest.json",
])
def test_written_artifacts_carry_replay_provenance(rel):
    p = Path(__file__).resolve().parents[2] / rel
    if not p.exists():
        pytest.skip(f"{rel} not generated in this working tree")
    d = json.loads(p.read_text())
    for flag in ("research_only", "not_live_evidence",
                 "not_backtest_evidence_for_phase4b", "no_recommendation"):
        assert d.get(flag) is True, f"{rel} missing {flag}"
    blob = json.dumps(d)
    for bad in FORBIDDEN_LIVE_VERDICTS:
        assert bad not in blob, f"{rel} contains live verdict token {bad}"

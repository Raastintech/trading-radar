"""Guards for the Missing Conviction Data Audit (research/backtests).

The audit's job is to decide what data to buy next, so its failure modes are
about honesty rather than arithmetic: an oracle that is not actually an oracle,
a walk-forward that was really in-sample, a recommendation that quietly assumes
provider access it was never granted. Each has a test.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import research.backtests.m1_missing_data_audit as M
from research.backtests.common import (
    FORBIDDEN_LIVE_VERDICTS,
    ReplayWriteViolation,
    assert_replay_write_path,
    is_replay_path,
)


class _Args:
    bootstrap = 200
    seed = 1
    ridge_alpha = 50.0


def _pool(n_dates=8, n_names=60, seed=3):
    rng = np.random.default_rng(seed)
    rows = []
    dates = pd.date_range("2022-01-07", periods=n_dates, freq="7D")
    for d in dates:
        for i in range(n_names):
            rows.append({
                "scan_date": d, "ticker": f"T{i}", "year": d.year,
                "fwd_60d": float(rng.normal(2, 25)),
                "dvol_med_20": float(rng.uniform(1e6, 1e8)),
                "fwd_max_1d_up_60d": float(abs(rng.normal(8, 6))),
                "fwd_max_1d_down_60d": -float(abs(rng.normal(8, 6))),
                "fwd_max_gap_up_60d": float(rng.normal(2, 5)),
                "sector_current_meta": "Tech",
            })
    df = pd.DataFrame(rows)
    up = df.groupby("scan_date")["fwd_60d"].rank(pct=True, ascending=False) * 100
    dn = df.groupby("scan_date")["fwd_60d"].rank(pct=True) * 100
    df["pool_top10pct_60d"] = up <= 10
    df["pool_bot10pct_60d"] = dn <= 10
    return df


# ── containment ─────────────────────────────────────────────────────────────

def test_audit_paths_are_inside_the_replay_namespace():
    for rel in (M.HEADROOM_REL, M.INVENTORY_REL, M.LATEST_REL, M.REPORT_TXT_REL):
        assert is_replay_path(rel), rel


@pytest.mark.parametrize("live", ["cache/research/research_scanner_latest.json",
                                  "db/trading.db", "cache/prices/NVDA.parquet"])
def test_audit_cannot_write_live_artifacts(live):
    with pytest.raises(ReplayWriteViolation):
        assert_replay_write_path(live)


# ── the oracle really is an oracle ──────────────────────────────────────────

def test_oracle_picks_the_actual_best_names():
    """If the 'perfect ranker' is not perfect, the headroom number is a lie."""
    pool = _pool()
    out = M.oracle_headroom(pool, _Args())
    node = out["train"]["top25"]
    assert node["oracle_mean"] > node["random_mean"] > node["worst_case_mean"]
    # A perfect top-25 out of 60 names must beat every alternative ranking.
    best = pool.groupby("scan_date")["fwd_60d"].apply(
        lambda s: s.nlargest(25).mean()).mean()
    assert math.isclose(node["oracle_mean"], best, abs_tol=1e-3)  # artifact rounds


def test_oracle_headroom_shrinks_as_the_list_widens():
    out = M.oracle_headroom(_pool(n_names=300), _Args())["train"]
    assert out["top10"]["headroom_vs_random"] > out["top100"]["headroom_vs_random"]


# ── walk-forward must not be in-sample ──────────────────────────────────────

def test_walkforward_splits_are_labelled_out_of_sample():
    """The first version of this measurement scored 2023 and 2024 with a model
    fit on all train years and called it walk-forward. It was not."""
    pool = _pool(n_dates=6)
    frames = []
    for y in (2022, 2023, 2024, 2025):
        f = pool.copy()
        f["scan_date"] = f["scan_date"] + pd.DateOffset(years=y - 2022)
        f["year"] = y
        frames.append(f)
    big = pd.concat(frames, ignore_index=True)
    for col in M.ALL_FEATURES[:6]:
        big[col] = np.random.default_rng(5).normal(size=len(big))
    out = M.ceiling_model(big, _Args())
    for name, node in out["by_split"].items():
        if name.startswith("walkforward") or name.startswith("holdout"):
            assert node["in_sample"] is False, name
        if name == "train_in_sample":
            assert node["in_sample"] is True


def test_ridge_fit_shrinks_slopes_but_not_the_intercept():
    """Heavy regularisation must flatten the feature weights and still centre
    the fit on the mean; penalising the intercept would bias every score."""
    rng = np.random.default_rng(0)
    feat = rng.normal(size=200)
    X = np.column_stack([feat, np.ones(200)])
    y = np.full(200, 7.0) + 0.01 * feat
    w = M._ridge_fit(X, y, alpha=1e6)
    assert abs(w[0]) < 1e-3, "slope should be crushed"
    assert math.isclose(w[-1], 7.0, abs_tol=1e-3), "intercept should survive"


def test_rank_matrix_is_scale_free_and_fills_gaps_at_the_middle():
    df = pd.DataFrame({
        "scan_date": [pd.Timestamp("2022-01-07")] * 4,
        "f": [1.0, 2.0, np.nan, 4.0],
    })
    X = M._rank_matrix(df, ("f",), ["f"])
    assert X.shape == (4, 3)                 # rank, squared, intercept
    assert math.isclose(X[2, 0], 0.5)        # the missing value sits mid-cross-section
    scaled = df.assign(f=df["f"] * 1000)
    assert np.allclose(X, M._rank_matrix(scaled, ("f",), ["f"]), equal_nan=True)


# ── move anatomy ────────────────────────────────────────────────────────────

def test_move_anatomy_separates_event_winners_from_drift_winners():
    pool = _pool(n_dates=4, n_names=40)
    # Force the top decile to be event-shaped.
    pool.loc[pool["pool_top10pct_60d"], "fwd_max_1d_up_60d"] = 40.0
    pool.loc[pool["pool_top10pct_60d"], "fwd_60d"] = 60.0
    out = M.move_anatomy(pool)
    w = out["pool_top_decile_winners"]
    assert w["share_with_a_25pct_up_day"] == 100.0
    mc = out["winner_move_concentration"]
    assert math.isclose(mc["median_single_day_share_of_total_move"], 40.0 / 60.0,
                        abs_tol=1e-3)  # artifact rounds to three places


def test_move_anatomy_reports_drift_when_there_are_no_big_days():
    pool = _pool(n_dates=4, n_names=40)
    pool["fwd_max_1d_up_60d"] = 3.0
    out = M.move_anatomy(pool)
    assert out["pool_top_decile_winners"]["share_no_day_above_10pct"] == 100.0
    assert out["pool_top_decile_winners"]["share_with_a_25pct_up_day"] == 0.0


# ── the inventory and recommendation ────────────────────────────────────────

def test_every_missing_data_class_states_cost_pit_safety_and_a_verdict():
    for c in M.MISSING_DATA_CLASSES:
        for key in ("provider_status", "point_in_time", "estimated_calls",
                    "historical_coverage", "separates_tails", "worth_testing"):
            assert c.get(key), f"{c['class']} missing {key}"
        assert len(c["provider_status"]) > 40, c["class"]


def test_unavailable_data_is_marked_unavailable_not_estimated():
    """Options history does not exist for this window; an audit that quotes a
    call count for it would be inventing an option."""
    opts = next(c for c in M.MISSING_DATA_CLASSES if "options" in c["class"])
    assert "NOT AVAILABLE" in opts["provider_status"]
    assert opts["worth_testing"].startswith("NO")
    assert "0%" in opts["historical_coverage"]


def test_recommendation_declares_calls_paths_and_both_criteria():
    r = M.RECOMMENDATION
    assert r["estimated_provider_calls"]["primary"]
    assert all(p.startswith(("cache/", "logs/")) for p in r["destination_paths"])
    assert r["success_criteria"] and r["failure_criteria"]
    assert r["what_this_does_not_promise"], "a recommendation must state its limits"


def test_recommendation_destinations_are_clean_paths_not_prose():
    """A destination that carries an explanatory aside cannot be checked, and
    was silently unverifiable until this test."""
    for path in M.RECOMMENDATION["destination_paths"]:
        assert " " not in path, f"prose in a path: {path!r}"
        assert path.startswith(("cache/", "logs/")), path


def test_unregistered_destinations_are_declared_as_needing_registration():
    """The write guard refuses paths it does not know. If the recommended
    experiment writes somewhere new, the recommendation has to say so rather
    than discover it at runtime."""
    unregistered = [p for p in M.RECOMMENDATION["destination_paths"]
                    if not is_replay_path(p)]
    if unregistered:
        note = M.RECOMMENDATION.get("namespace_registration_required", "")
        assert "common.py" in note, (
            f"{unregistered} are not writable and the recommendation does not say "
            "the namespace must be registered first")


# ── artifacts ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("rel", [
    "cache/research/m1_missing_data_headroom.json",
    "cache/research/m1_missing_data_inventory.json",
    "cache/research/m1_missing_data_latest.json",
])
def test_artifacts_carry_provenance_and_no_live_verdicts(rel):
    p = Path(__file__).resolve().parents[2] / rel
    if not p.exists():
        pytest.skip(f"{rel} not generated")
    d = json.loads(p.read_text())
    for flag in ("research_only", "not_live_evidence", "no_recommendation"):
        assert d.get(flag) is True, f"{rel} missing {flag}"
    blob = json.dumps(d)
    for bad in FORBIDDEN_LIVE_VERDICTS:
        assert bad not in blob, f"{rel} contains {bad}"


def test_latest_artifact_records_that_no_provider_calls_were_made():
    p = Path(__file__).resolve().parents[2] / "cache/research/m1_missing_data_latest.json"
    if not p.exists():
        pytest.skip("latest artifact not generated")
    d = json.loads(p.read_text())
    assert d["provider_calls_made"] == 0
    assert d["requires_approval"] is True


# ── the surprise experiment's namespace (registered on approval) ────────────

@pytest.mark.parametrize("path", [
    "cache/research/m1_surprise_intermediates/earnings_surprise.parquet",
    "cache/research/m1_surprise_overlay_latest.json",
    "cache/research/m1_surprise_panel.json",
    "logs/m1_surprise_overlay_latest.txt",
])
def test_surprise_experiment_paths_are_registered_and_writable(path):
    """The audit recommended these destinations; approval registered them.

    Without registration the write guard refuses the path at runtime, which is
    correct but would surface only after the provider calls had been spent.
    """
    assert is_replay_path(path), path


@pytest.mark.parametrize("live", [
    "cache/research/research_scanner_latest.json",
    "cache/research/high_conviction_alpha_latest.json",
    "cache/prices/NVDA.parquet",
    "db/trading.db",
])
def test_registering_the_surprise_namespace_did_not_widen_the_guard(live):
    """Adding a prefix must not accidentally admit a live artifact."""
    with pytest.raises(ReplayWriteViolation):
        assert_replay_write_path(live)


def test_surprise_prefix_does_not_admit_a_sibling_directory():
    assert not is_replay_path("cache/research/m1_surprise_intermediatesEVIL/x.json")

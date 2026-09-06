"""Guards for the M1 Surprise Overlay (research/backtests).

This is the first study in the programme that spends provider calls, so the
tests cover two families of risk: the fetch behaving itself (gated, capped,
contained) and the earnings data being used point-in-time.
"""

from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import research.backtests.m1_surprise_overlay as S
from research.backtests.common import (
    CONVICTION_VERDICTS,
    FORBIDDEN_LIVE_VERDICTS,
    FetchNotAuthorised,
    ReplayWriteViolation,
    assert_conviction_verdict,
    assert_replay_write_path,
    is_replay_path,
)


class _Args:
    root = "."
    fetch_start = "2022-01-01"
    fetch_end = "2022-03-31"
    execute_fetch = False
    max_calls = None
    bootstrap = 200
    seed = 1


# ── the fetch is gated, capped and contained ────────────────────────────────

def test_fetch_refuses_without_explicit_authorisation():
    with pytest.raises(FetchNotAuthorised):
        from research.backtests.common import require_execute_fetch
        require_execute_fetch(_Args(), planned_calls=52, what="test")


def test_plan_stage_makes_no_calls_and_states_so(capsys):
    rc = S.plan_stage(_Args())
    assert rc == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["calls_made_by_this_stage"] == 0
    assert plan["planned_calls"] <= plan["hard_cap"]


def test_month_chunks_cover_the_range_without_gaps_or_overlap():
    chunks = S.month_chunks("2022-01-01", "2022-03-31")
    assert chunks[0][0] == "2022-01-01" and chunks[-1][1] == "2022-03-31"
    assert len(chunks) == 3
    for (a1, b1), (a2, _b2) in zip(chunks, chunks[1:]):
        assert date.fromisoformat(b1) < date.fromisoformat(a2)
        assert (date.fromisoformat(a2) - date.fromisoformat(b1)).days == 1


def test_planned_calls_stay_under_the_hard_cap_for_the_real_range():
    chunks = S.month_chunks(S.FETCH_START, S.FETCH_END)
    assert len(chunks) <= S.HARD_CALL_CAP
    assert S.HARD_CALL_CAP == 500


def test_cap_is_enforced_before_the_first_call():
    from research.backtests.common import enforce_call_cap
    with pytest.raises(FetchNotAuthorised):
        enforce_call_cap(600, S.HARD_CALL_CAP, what="test")


@pytest.mark.parametrize("rel", [S.RAW_REL, S.PANEL_REL, S.OVERLAY_REL,
                                 S.LATEST_REL, S.REPORT_TXT_REL])
def test_all_destinations_are_replay_owned(rel):
    assert is_replay_path(rel), rel


@pytest.mark.parametrize("live", ["cache/research/research_scanner_latest.json",
                                  "db/trading.db", "cache/prices/NVDA.parquet"])
def test_cannot_write_live_artifacts(live):
    with pytest.raises(ReplayWriteViolation):
        assert_replay_write_path(live)


# ── point-in-time handling ──────────────────────────────────────────────────

def test_an_event_is_not_usable_on_its_own_report_date():
    """No time-of-day field exists, so a report dated D must be unknown on D."""
    sessions = pd.date_range("2022-01-03", periods=10, freq="B")
    cal = pd.DataFrame({"symbol": ["AAA"], "date": [pd.Timestamp("2022-01-05")],
                        "epsActual": [1.0], "epsEstimated": [0.9],
                        "revenueActual": [100.0], "revenueEstimated": [90.0]})
    sess = np.array(sessions, dtype="datetime64[ns]")
    idx = np.searchsorted(sess, cal["date"].to_numpy("datetime64[ns]"), side="right")
    usable = sess[idx][0]
    assert usable > np.datetime64("2022-01-05")
    assert usable == np.datetime64("2022-01-06")


def test_surprise_uses_the_latest_event_at_or_before_the_scan_date():
    """merge_asof backward is the mechanism; this pins the semantics."""
    cal = pd.DataFrame({
        "ticker": ["AAA"] * 3,
        "usable_date": pd.to_datetime(["2022-01-10", "2022-04-11", "2022-07-11"]),
        "eps_surprise_pct": [5.0, -3.0, 9.0],
    })
    left = pd.DataFrame({"ticker": ["AAA", "AAA"],
                         "scan_date": pd.to_datetime(["2022-03-01", "2022-06-01"])})
    out = pd.merge_asof(left.sort_values("scan_date"), cal.sort_values("usable_date"),
                        left_on="scan_date", right_on="usable_date", by="ticker",
                        direction="backward")
    assert list(out["eps_surprise_pct"]) == [5.0, -3.0]     # never 9.0


def test_a_scan_date_before_any_event_gets_no_surprise():
    cal = pd.DataFrame({"ticker": ["AAA"],
                        "usable_date": pd.to_datetime(["2022-06-10"]),
                        "eps_surprise_pct": [5.0]})
    left = pd.DataFrame({"ticker": ["AAA"],
                         "scan_date": pd.to_datetime(["2022-01-05"])})
    out = pd.merge_asof(left, cal, left_on="scan_date", right_on="usable_date",
                        by="ticker", direction="backward")
    assert pd.isna(out["eps_surprise_pct"].iloc[0])


def test_surprise_on_a_near_zero_estimate_is_clipped_not_infinite():
    """The raw field reaches 1e14% on cent-level estimates."""
    a, e = 0.30, 0.0001
    raw = (a - e) / max(abs(e), S.EPS_DENOM_FLOOR) * 100.0
    assert abs(min(max(raw, -S.SURPRISE_CLIP_PCT), S.SURPRISE_CLIP_PCT)) <= S.SURPRISE_CLIP_PCT


# ── the verdict ladder ──────────────────────────────────────────────────────

def _res(**kw):
    base = {
        "train": {"top50": {"episodes": 4000,
                            "selection_vs_pool": {"selection_mean": 1.0,
                                                  "date_cluster_ci_mean": [0.5, 1.5],
                                                  "ticker_cluster_ci": [0.4, 2.0]}},
                  "vs_random_top50": {"mean_difference": 1.5, "mean_ci": [0.6, 2.4]}},
        "holdout": {"vs_random_top50": {"mean_difference": 1.2}},
        "walk_forward": [{"mean_difference_vs_random": 1.0},
                         {"mean_difference_vs_random": 0.8}],
        "tail_profile": {"right_tail_change_pct": 5.0, "left_tail_change_pct": -30.0,
                         "amplifies_both_tails": False},
        "subset_vs_pool": {"mean_difference": 1.0, "mean_ci": [0.5, 1.5]},
    }
    for k, v in kw.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            base[k] = {**base[k], **v}
        else:
            base[k] = v
    return base


ALPHA = {"name": "x", "kind": "filter"}


def test_a_clean_sweep_is_a_conviction_overlay():
    v, _ = S.verdict_for(ALPHA, _res(), coverage_pct=40.0)
    assert v == "PROMISING_CONVICTION_OVERLAY"


def test_low_coverage_is_refused_as_data_unsafe():
    """The fresh-event overlays covered 4-8% of the pool; a result on that is a
    statement about a sliver, not about M1."""
    v, why = S.verdict_for(ALPHA, _res(), coverage_pct=6.0)
    assert v == "REJECTED_DATA_UNSAFE" and "6.0%" in why[0]


def test_amplifying_both_tails_blocks_conviction():
    """The failure mode the audit predicted: finding live names, not good ones."""
    r = _res(tail_profile={"right_tail_change_pct": 40.0,
                           "left_tail_change_pct": 30.0,
                           "amplifies_both_tails": True})
    v, why = S.verdict_for(ALPHA, r, coverage_pct=40.0)
    assert v != "PROMISING_CONVICTION_OVERLAY"
    assert any("liveness" in x for x in why)


def test_a_subset_that_beats_the_pool_but_cannot_rank_is_an_annotation():
    r = _res()
    r["train"]["vs_random_top50"] = {"mean_difference": -0.2, "mean_ci": [-1.0, 0.6]}
    v, why = S.verdict_for(ALPHA, r, coverage_pct=40.0)
    assert v == "USEFUL_EVENT_ANNOTATION"
    assert "cannot order the list" in why[0]


def test_an_avoid_filter_must_cut_the_left_tail_harder():
    r = _res(tail_profile={"right_tail_change_pct": -5.0,
                           "left_tail_change_pct": -40.0,
                           "amplifies_both_tails": False})
    v, _ = S.verdict_for({"name": "a", "kind": "filter", "intent": "avoid"}, r,
                         coverage_pct=70.0)
    assert v == "USEFUL_AVOID_FILTER"


def test_an_avoid_filter_that_removes_more_winners_fails():
    """What dropping EPS-miss names actually did: -44% right, -24% left."""
    r = _res(tail_profile={"right_tail_change_pct": -44.0,
                           "left_tail_change_pct": -24.0,
                           "amplifies_both_tails": False})
    v, _ = S.verdict_for({"name": "a", "kind": "filter", "intent": "avoid"}, r,
                         coverage_pct=70.0)
    assert v != "USEFUL_AVOID_FILTER"


def test_train_only_effect_is_overfit():
    v, _ = S.verdict_for(ALPHA, _res(holdout={"vs_random_top50":
                                              {"mean_difference": -1.0}}),
                         coverage_pct=40.0)
    assert v == "REJECTED_OVERFIT"


def test_annotation_verdict_is_in_the_ladder_and_not_a_live_token():
    assert "USEFUL_EVENT_ANNOTATION" in CONVICTION_VERDICTS
    assert not (CONVICTION_VERDICTS & FORBIDDEN_LIVE_VERDICTS)
    assert_conviction_verdict("USEFUL_EVENT_ANNOTATION")


# ── artifacts ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("rel", [
    "cache/research/m1_surprise_overlay_latest.json",
    "cache/research/m1_surprise_latest.json",
    "cache/research/m1_surprise_panel_meta.json",
])
def test_artifacts_carry_provenance_and_no_live_verdicts(rel):
    p = Path(__file__).resolve().parents[2] / rel
    if not p.exists():
        pytest.skip(f"{rel} not generated")
    d = json.loads(p.read_text())
    for flag in ("research_only", "not_live_evidence", "no_recommendation"):
        assert d.get(flag) is True, f"{rel} missing {flag}"
    for bad in FORBIDDEN_LIVE_VERDICTS:
        assert bad not in json.dumps(d), f"{rel} contains {bad}"


def test_fetch_meta_records_the_call_count_within_the_cap():
    p = Path(__file__).resolve().parents[2] / S.FETCH_META_REL
    if not p.exists():
        pytest.skip("fetch meta not generated")
    d = json.loads(p.read_text())
    assert d["calls_made"] <= d["hard_cap"] <= 500
    assert d["calls_made"] == d["chunks"], "one call per chunk, no retries hidden"


def test_panel_meta_discloses_the_estimate_provenance_risk():
    p = Path(__file__).resolve().parents[2] / "cache/research/m1_surprise_panel_meta.json"
    if not p.exists():
        pytest.skip("panel meta not generated")
    d = json.loads(p.read_text())
    warn = d.get("estimate_provenance_warning", "")
    assert "cannot be verified" in warn and "reassurance, not proof" in warn

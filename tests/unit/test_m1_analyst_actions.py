"""Containment and point-in-time guards for the M1 analyst-action study.

The study is research-only and touches a provider, so the properties that have
to hold are mechanical, not editorial:

* it may write ONLY into ``cache/research/m1_analyst_actions_*`` and
  ``logs/m1_analyst_actions_*`` — every live path is refused;
* it may call ONLY ``/stable/grades``; ``grades-historical``,
  ``price-target-news``, ``analyst-estimates`` and options endpoints are
  refused before a call is made;
* no fetch happens without ``--execute-fetch``, and none above the 3,800 cap;
* an analyst action is invisible until the first session STRICTLY AFTER its
  event date, and no window feature can see past the scan date;
* upgrades are derived from the rating PAIR, so the maintain/reiterate noise
  that dominates the feed cannot be counted as conviction;
* no live verdict token (``VALIDATED_EDGE`` and friends) can leave the module,
  and no rule reads a forward return.
"""

from __future__ import annotations

import argparse
import inspect
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import research.backtests.m1_analyst_actions as M
from research.backtests.common import (
    CONVICTION_VERDICTS,
    FORBIDDEN_LIVE_VERDICTS,
    FetchNotAuthorised,
    ReplayWriteViolation,
    assert_replay_write_path,
    is_replay_path,
)


# ── write containment ───────────────────────────────────────────────────────

ARTIFACTS = [M.TICKERS_REL, M.RAW_REL, M.FETCH_META_REL, M.PANEL_REL,
             M.UNIVERSE_BASE_REL, M.PANEL_META_REL, M.OVERLAY_REL, M.LATEST_REL,
             M.REPORT_TXT_REL]


@pytest.mark.parametrize("rel", ARTIFACTS)
def test_every_artifact_is_inside_a_replay_namespace(rel):
    assert is_replay_path(rel), f"{rel} is not covered by a replay write prefix"
    assert assert_replay_write_path(rel)


@pytest.mark.parametrize("rel", ARTIFACTS)
def test_artifacts_use_the_approved_prefixes(rel):
    assert (rel.startswith("cache/research/m1_analyst_actions_")
            or rel.startswith("logs/m1_analyst_actions_")), rel


@pytest.mark.parametrize("rel", [
    "cache/prices/AAPL.parquet",
    "cache/prices_deep/AAPL.parquet",
    "cache/state/broker_positions_snapshot.json",
    "data/research/journal.jsonl",
    "db/trading.db",
    "cache/research/high_conviction_alpha_latest.json",
    "cache/research/alpha_focus_latest.json",
    "logs/research_scanner_latest.txt",
])
def test_live_paths_are_refused(rel):
    with pytest.raises(ReplayWriteViolation):
        assert_replay_write_path(rel)


def test_report_path_is_the_one_the_approval_named():
    assert M.REPORT_TXT_REL == "logs/m1_analyst_actions_latest.txt"


# ── provider containment ────────────────────────────────────────────────────


def test_only_grades_is_approved():
    assert M.APPROVED_ENDPOINT == "/grades"
    assert M.assert_endpoint_allowed("/grades") == "/grades"


@pytest.mark.parametrize("path", [
    "/grades-historical", "/price-target-news", "/price-target-summary",
    "/analyst-estimates", "/options-chain", "/earnings-calendar", "/quote",
])
def test_forbidden_endpoints_are_refused(path):
    with pytest.raises(M.ForbiddenEndpoint):
        M.assert_endpoint_allowed(path)


def test_the_endpoints_the_approval_named_are_all_listed():
    for path in ("/grades-historical", "/price-target-news", "/analyst-estimates"):
        assert path in M.FORBIDDEN_ENDPOINTS


def test_hard_cap_is_the_approved_one():
    assert M.HARD_CALL_CAP == 3800


def _args(root: Path, **kw) -> argparse.Namespace:
    base = dict(root=str(root), execute_fetch=False, allow_large_run=False,
                max_calls=None, limit=0,
                workers=1, retries=0, backoff=0.0, only_failed=False,
                rebuild_tickers=False, bootstrap=50, seed=31,
                start="2022-01-01", end="2025-12-31")
    base.update(kw)
    return argparse.Namespace(**base)


def _write_tickers(root: Path, n: int) -> None:
    p = root / M.TICKERS_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"tickers": [f"T{i}" for i in range(n)],
                             "n_tickers": n, "pool_rows": 10, "scan_dates": 2}))


def test_fetch_refuses_without_execute_fetch(tmp_path, monkeypatch):
    _write_tickers(tmp_path, 5)
    monkeypatch.setattr(
        "core.fmp_client.get_fmp",
        lambda: pytest.fail("the client must not be built without --execute-fetch"),
        raising=False)
    with pytest.raises(FetchNotAuthorised):
        M.fetch_stage(_args(tmp_path))


def test_fetch_refuses_over_the_cap(tmp_path):
    _write_tickers(tmp_path, 40)
    with pytest.raises(FetchNotAuthorised):
        M.fetch_stage(_args(tmp_path, execute_fetch=True, max_calls=10))


def test_fetch_cannot_exceed_the_hard_cap_even_if_asked(tmp_path):
    _write_tickers(tmp_path, M.HARD_CALL_CAP + 1)
    with pytest.raises(FetchNotAuthorised):
        # allow_large_run clears the size gate, so this still exercises the
        # hard cap itself rather than refusing one gate earlier.
        M.fetch_stage(_args(tmp_path, execute_fetch=True, allow_large_run=True,
                            max_calls=10_000))


def test_a_large_fetch_refuses_until_its_size_is_named(tmp_path):
    _write_tickers(tmp_path, 500)
    with pytest.raises(FetchNotAuthorised) as exc:
        M.fetch_stage(_args(tmp_path, execute_fetch=True))
    assert "--allow-large-run" in str(exc.value)


def test_run_cli_turns_a_refused_fetch_into_a_clean_exit(tmp_path):
    from research.backtests.common import run_cli

    _write_tickers(tmp_path, 5)
    assert run_cli(M.fetch_stage, _args(tmp_path)) == 3


# ── the grade vocabulary ────────────────────────────────────────────────────


@pytest.mark.parametrize("raw,expected", [
    ("Strong Buy", 5), ("Buy", 4), ("Outperform", 4), ("Overweight", 4),
    ("Neutral", 3), ("Hold", 3), ("Market Perform", 3), ("Equal-Weight", 3),
    ("Underperform", 2), ("Underweight", 2), ("Sell", 1), ("Strong Sell", 1),
    ("Buy (initiation)", 4), ("  buy  ", 4),
    ("", None), (None, None), ("Wildly Bullish Vibes", None),
])
def test_grade_ordinals(raw, expected):
    assert M.grade_ordinal(raw) == expected


@pytest.mark.parametrize("prev,new,expected", [
    ("Hold", "Buy", "upgrade"),
    ("Sector Perform", "Outperform", "upgrade"),
    ("Buy", "Hold", "downgrade"),
    ("Buy", "Buy", "maintain"),
    ("Outperform", "Overweight", "maintain"),
    (None, "Buy", "initiation"),
    ("Gibberish", "More Gibberish", "unknown"),
])
def test_action_classification_comes_from_the_rating_pair(prev, new, expected):
    assert M.classify_action(M.grade_ordinal(prev), M.grade_ordinal(new),
                             "whatever") == expected


def test_a_maintain_is_never_an_upgrade_whatever_the_provider_label_says():
    # The provider's own `action` string is not trusted: only the pair is.
    assert M.classify_action(M.grade_ordinal("Buy"), M.grade_ordinal("Buy"),
                             "upgrade") == "maintain"


def test_an_equal_rated_reiteration_is_not_new_bullish_coverage():
    assert M.BULLISH_ORDINAL_FLOOR == 4
    assert M.grade_ordinal("Hold") < M.BULLISH_ORDINAL_FLOOR
    assert M.grade_ordinal("Buy") >= M.BULLISH_ORDINAL_FLOOR


# ── point-in-time ───────────────────────────────────────────────────────────


SESSIONS = pd.DatetimeIndex(pd.bdate_range("2024-01-01", periods=200))


def _acts(rows: list[tuple[str, str, str, str, str]]):
    """(symbol, date, firm, prev, new) -> the frame load_raw_actions produces."""
    df = pd.DataFrame(rows, columns=["symbol", "date", "firm", "prev", "new"])
    df["date"] = pd.to_datetime(df["date"])
    df["action"] = "provider-label"
    df["prev_ord"] = df["prev"].map(M.grade_ordinal)
    df["new_ord"] = df["new"].map(M.grade_ordinal)
    df["klass"] = [M.classify_action(p, n, a)
                   for p, n, a in zip(df["prev_ord"], df["new_ord"], df["action"])]
    return df.sort_values(["symbol", "date"]).reset_index(drop=True)


def _pool(rows: list[tuple[str, str]]):
    df = pd.DataFrame(rows, columns=["ticker", "scan_date"])
    df["scan_date"] = pd.to_datetime(df["scan_date"])
    df["price"] = 10.0
    return df


def test_an_action_is_invisible_on_its_own_event_date():
    scan = SESSIONS[50]
    pool = _pool([("AAA", scan)])
    acts = _acts([("AAA", scan, "Firm A", "Hold", "Buy")])
    out, _ = M.compute_action_features(pool, acts, SESSIONS)
    assert out["act_up_63"].iloc[0] == 0.0, "same-day action must not be usable"


def test_an_action_becomes_usable_the_next_session():
    scan = SESSIONS[50]
    pool = _pool([("AAA", scan)])
    acts = _acts([("AAA", SESSIONS[49], "Firm A", "Hold", "Buy")])
    out, _ = M.compute_action_features(pool, acts, SESSIONS)
    assert out["act_up_63"].iloc[0] == 1.0
    assert out["act_up_7"].iloc[0] == 1.0


def test_a_future_action_never_enters_a_feature():
    scan = SESSIONS[50]
    pool = _pool([("AAA", scan)])
    acts = _acts([("AAA", SESSIONS[60], "Firm A", "Hold", "Buy"),
                  ("AAA", SESSIONS[120], "Firm B", "Hold", "Strong Buy")])
    out, _ = M.compute_action_features(pool, acts, SESSIONS)
    assert out["act_up_126"].iloc[0] == 0.0
    assert out["act_prior_count"].iloc[0] == 0.0


def test_window_edges_are_counted_in_sessions():
    scan_i = 130
    pool = _pool([("AAA", SESSIONS[scan_i])])
    inside = SESSIONS[scan_i - 63]      # usable at scan_i-62 → inside the 63 window
    outside = SESSIONS[scan_i - 130]
    acts = _acts([("AAA", inside, "Firm A", "Hold", "Buy"),
                  ("AAA", outside, "Firm B", "Hold", "Buy")])
    out, _ = M.compute_action_features(pool, acts, SESSIONS)
    assert out["act_up_63"].iloc[0] == 1.0
    assert out["act_up_126"].iloc[0] == 1.0
    assert out["act_prior_count"].iloc[0] == 2.0


def test_breadth_counts_houses_not_actions():
    scan = SESSIONS[100]
    pool = _pool([("AAA", scan)])
    acts = _acts([("AAA", SESSIONS[90], "Firm A", "Hold", "Buy"),
                  ("AAA", SESSIONS[95], "Firm A", "Buy", "Strong Buy"),
                  ("AAA", SESSIONS[97], "Firm B", "Hold", "Buy")])
    out, _ = M.compute_action_features(pool, acts, SESSIONS)
    assert out["act_up_63"].iloc[0] == 3.0
    assert out["act_up_firms_63"].iloc[0] == 2.0


def test_maintains_are_separated_from_changes():
    scan = SESSIONS[100]
    pool = _pool([("AAA", scan)])
    acts = _acts([("AAA", SESSIONS[90], "Firm A", "Buy", "Buy"),
                  ("AAA", SESSIONS[95], "Firm B", "Buy", "Buy")])
    out, _ = M.compute_action_features(pool, acts, SESSIONS)
    assert out["act_up_63"].iloc[0] == 0.0
    assert out["act_maintain_63"].iloc[0] == 2.0
    assert bool(out["act_maintain_only_63"].iloc[0]) is True
    assert bool(out["act_none_126"].iloc[0]) is False


def test_uncovered_names_are_zero_not_null():
    scan = SESSIONS[100]
    pool = _pool([("AAA", scan), ("BBB", scan)])
    acts = _acts([("AAA", SESSIONS[90], "Firm A", "Hold", "Buy")])
    out, _ = M.compute_action_features(pool, acts, SESSIONS)
    bbb = out[out.ticker == "BBB"].iloc[0]
    assert bbb["act_up_63"] == 0.0 and bbb["act_down_63"] == 0.0
    assert bool(bbb["act_none_126"]) is True
    assert bool(bbb["act_any_prior"]) is False


def test_downgrade_pressure_is_null_without_a_rating_change():
    scan = SESSIONS[100]
    pool = _pool([("AAA", scan)])
    acts = _acts([("AAA", SESSIONS[90], "Firm A", "Buy", "Buy")])
    out, _ = M.compute_action_features(pool, acts, SESSIONS)
    assert pd.isna(out["act_downgrade_pressure_63"].iloc[0])


def test_the_lookahead_audit_catches_a_tampered_count():
    scan = SESSIONS[100]
    pool = _pool([("AAA", scan)])
    acts = _acts([("AAA", SESSIONS[90], "Firm A", "Hold", "Buy")])
    out, usable = M.compute_action_features(pool, acts, SESSIONS)
    assert M._assert_no_future_actions(out, usable)["rows_checked"] == 1
    out.loc[out.index[0], "act_up_63"] = 7.0
    with pytest.raises(AssertionError):
        M._assert_no_future_actions(out, usable)


def test_the_panel_never_changes_the_pool_size():
    scan = SESSIONS[100]
    pool = _pool([("AAA", scan), ("AAA", SESSIONS[110]), ("BBB", scan)])
    acts = _acts([("AAA", SESSIONS[90], "Firm A", "Hold", "Buy"),
                  ("AAA", SESSIONS[90], "Firm A", "Hold", "Buy")])  # duplicate feed row
    out, _ = M.compute_action_features(pool, acts, SESSIONS)
    assert len(out) == 3


# ── verdict containment ─────────────────────────────────────────────────────


def test_no_live_verdict_token_appears_in_the_module():
    src = Path(M.__file__).read_text()
    for token in FORBIDDEN_LIVE_VERDICTS:
        assert token not in src, f"{token} must never be emittable from this module"


def test_every_verdict_the_module_can_emit_is_a_conviction_verdict():
    res = {"train": {"top50": {"episodes": 0}}}
    v, _ = M.verdict_for({"name": "x"}, res, 50.0)
    assert v in CONVICTION_VERDICTS


def test_a_control_row_can_never_earn_a_promotion():
    res = {"train": {"top50": {"episodes": 100},
                     "vs_random_top50": {"mean_ci": [1.0, 2.0], "mean_difference": 1.5},
                     "vs_random_top25": {"mean_ci": [1.0, 2.0], "mean_difference": 1.5}},
           "holdout": {"vs_random_top50": {"mean_difference": 1.0}},
           "walk_forward": [{"mean_difference_vs_random": 1.0},
                            {"mean_difference_vs_random": 1.0}]}
    v, why = M.verdict_for({"name": "c", "intent": "control"}, res, 50.0)
    assert v == "INCONCLUSIVE"
    assert "control" in why[0]


def test_a_sparse_feature_is_refused_as_data_unsafe():
    v, _ = M.verdict_for({"name": "x"}, {"train": {"top50": {"episodes": 10}}}, 4.0)
    assert v == "REJECTED_DATA_UNSAFE"


def test_a_conviction_verdict_needs_train_holdout_per_name_and_walk_forward():
    good = {
        "coverage": {"median_names_per_date": 60},
        "train": {"top50": {"episodes": 500,
                            "selection_vs_pool": {"ticker_cluster_ci": [0.5, 3.0]}},
                  "vs_random_top50": {"mean_ci": [0.4, 2.0], "mean_difference": 1.2},
                  "vs_random_top25": {"mean_ci": [-1.0, 2.0], "mean_difference": 0.5}},
        "holdout": {"vs_random_top50": {"mean_difference": 0.8},
                    "vs_random_top25": {"mean_difference": 0.2}},
        "walk_forward": [{"mean_difference_vs_random": 0.5},
                         {"mean_difference_vs_random": 0.9}],
        "tail_profile": {"right_tail_change_pct": 20.0, "left_tail_change_pct": -5.0,
                         "amplifies_both_tails": False},
    }
    assert M.verdict_for({"name": "x"}, good, 40.0)[0] == "PROMISING_CONVICTION_OVERLAY"

    # Same numbers, but the per-name CI straddles zero: the basket improved and
    # the typical name did not. That is the failure this study exists to catch.
    import copy

    thin = copy.deepcopy(good)
    thin["train"]["top50"]["selection_vs_pool"]["ticker_cluster_ci"] = [-2.0, 3.0]
    assert M.verdict_for({"name": "x"}, thin, 40.0)[0] != "PROMISING_CONVICTION_OVERLAY"

    both = copy.deepcopy(good)
    both["tail_profile"]["amplifies_both_tails"] = True
    assert M.verdict_for({"name": "x"}, both, 40.0)[0] != "PROMISING_CONVICTION_OVERLAY"

    no_ho = copy.deepcopy(good)
    no_ho["holdout"] = {"vs_random_top50": {"mean_difference": -0.4},
                        "vs_random_top25": {"mean_difference": -0.2}}
    assert M.verdict_for({"name": "x"}, no_ho, 40.0)[0] == "REJECTED_OVERFIT"


def test_an_avoid_filter_is_judged_on_its_tails():
    res = {"coverage": {"median_names_per_date": 300},
           "train": {"top50": {"episodes": 500}},
           "tail_profile": {"left_tail_change_pct": -30.0,
                            "right_tail_change_pct": -5.0},
           "walk_forward": []}
    assert M.verdict_for({"name": "a", "intent": "avoid"}, res,
                         90.0)[0] == "USEFUL_AVOID_FILTER"
    res["tail_profile"] = {"left_tail_change_pct": -30.0,
                           "right_tail_change_pct": -40.0}
    assert M.verdict_for({"name": "a", "intent": "avoid"}, res, 90.0)[0] == "INCONCLUSIVE"


# ── rule hygiene ────────────────────────────────────────────────────────────


def test_no_overlay_rule_reads_a_forward_return_or_a_label():
    src = inspect.getsource(M)
    start = src.index("ACTION_OVERLAYS: list[dict] = [")
    block = src[start:src.index("\n]\n", start)]
    for banned in ("fwd_", "pool_top", "pool_bot", "pool_catastrophic"):
        assert banned not in block, f"an overlay rule references {banned}"


def test_the_module_imports_nothing_from_production_runtime():
    src = Path(M.__file__).read_text()
    for banned in ("from execution", "import execution", "from strategies",
                   "import strategies", "from council", "import council",
                   "from dashboards", "import dashboards",
                   "core.decision_logger", "core.order", "paper_governance"):
        assert banned not in src, f"research module must not touch {banned}"


def test_pre_registered_thresholds_are_the_ones_the_earlier_studies_used():
    assert M.PROMOTION["min_coverage_pct"] == 10.0
    assert M.PROMOTION["min_names_per_date_for_list"] == 25
    assert M.PROMOTION["min_walk_forward_positive_steps"] == 2
    assert M.PROMOTION["needs_ticker_clustered_ci_above_zero"] is True


def test_list_sizes_and_horizons_match_the_approval():
    assert M.LIST_SIZES == (10, 25, 50, 100)
    assert tuple(M.HORIZONS) == (20, 45, 60, 90, 126)


def test_artifacts_carry_replay_provenance():
    from research.backtests.common import assert_provenance, provenance_flags

    assert_provenance(provenance_flags())


# ── column coercion (a silent all-False mask is a wrong answer, not a crash) ──


def test_flag_survives_every_shape_the_parquet_round_trip_produces():
    frames = {
        "float": pd.Series([1.0, 0.0, np.nan]),
        "nullable_bool_with_na": pd.array([True, False, None], dtype="boolean"),
        "nullable_bool_without_na": pd.array([True, False, False], dtype="boolean"),
        "object": pd.Series([True, False, None], dtype=object),
        "plain_bool": pd.Series([True, False, False]),
    }
    for name, col in frames.items():
        d = pd.DataFrame({"x": col})
        got = M._flag(d, "x")
        assert got.tolist() == [True, False, False], f"{name} coerced wrongly"
        assert got.dtype == bool


def test_flag_is_false_for_a_column_that_is_not_there():
    assert M._flag(pd.DataFrame({"a": [1, 2]}), "missing").tolist() == [False, False]


def test_missing_data_never_collapses_into_false():
    # NA means "no data", not "no upgrade". Collapsing the two would recruit
    # unfetched tickers into the no-coverage control.
    out = M._to_nullable_bool(pd.Series([1.0, 0.0, np.nan]))
    assert out.tolist()[:2] == [True, False]
    assert out.isna().tolist() == [False, False, True]


def test_restriction_drops_unfetched_rows_and_reports_the_cost():
    import argparse

    pool = pd.DataFrame({
        "ticker": ["A", "B", "C", "D"],
        "scan_date": pd.to_datetime(["2024-01-05"] * 4),
        "act_data_missing": [False, True, False, True],
        "fwd_60d": [1.0, 2.0, 3.0, 4.0],
    })
    kept, rep = M._restrict_to_fetched(pool, None, argparse.Namespace(
        bootstrap=10, seed=1))
    assert len(kept) == 2 and set(kept.ticker) == {"A", "C"}
    assert rep["rows_dropped"] == 2 and rep["rows_dropped_pct"] == 50.0
    assert "not a name without" in rep["note"]


# ── completion pass (the second approved provider pass) ─────────────────────


def _outcomes(root: Path, ok: list[str], failed: list[str]) -> None:
    p = root / M.OUTCOMES_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"ok_tickers": ok, "failed_tickers": failed,
                             "n_ok": len(ok), "n_failed": len(failed), "passes": 1}))


def test_a_completion_pass_targets_only_the_failed_tickers(tmp_path, monkeypatch):
    _write_tickers(tmp_path, 5)
    _outcomes(tmp_path, ok=["T0", "T1", "T2"], failed=["T3", "T4"])
    seen = {}

    def _fake_require(args, *, planned_calls, what):
        seen["planned"] = planned_calls
        raise FetchNotAuthorised("stop here — the plan is what is under test")

    monkeypatch.setattr(M, "require_execute_fetch", _fake_require)
    with pytest.raises(FetchNotAuthorised):
        M.fetch_stage(_args(tmp_path, only_failed=True))
    assert seen["planned"] == 2, "a completion pass must not re-fetch what returned"


def test_a_completion_pass_refuses_to_exceed_its_own_cap(tmp_path):
    _write_tickers(tmp_path, 50)
    _outcomes(tmp_path, ok=[], failed=[f"T{i}" for i in range(50)])
    with pytest.raises(FetchNotAuthorised):
        M.fetch_stage(_args(tmp_path, only_failed=True, execute_fetch=True,
                            max_calls=10))


def test_a_completion_pass_appends_and_never_truncates_the_feed(tmp_path, monkeypatch):
    _write_tickers(tmp_path, 3)
    _outcomes(tmp_path, ok=["T0"], failed=["T1", "T2"])
    raw = tmp_path / M.RAW_REL
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text(json.dumps({"symbol": "T0", "date": "2024-01-02", "firm": "F",
                               "prev": "Hold", "new": "Buy", "action": "upgrade"}) + "\n")

    class _Client:
        def _get(self, path, params=None):
            assert path == M.APPROVED_ENDPOINT
            return [{"symbol": params["symbol"], "date": "2024-02-02",
                     "gradingCompany": "F", "previousGrade": "Hold",
                     "newGrade": "Buy", "action": "upgrade"}]

    monkeypatch.setattr("core.fmp_client.get_fmp", lambda: _Client(), raising=False)
    monkeypatch.setattr(M, "load_dotenv", lambda *a, **k: True, raising=False)
    rc = M.fetch_stage(_args(tmp_path, only_failed=True, execute_fetch=True,
                             max_calls=1700))
    assert rc == 0
    symbols = [json.loads(l)["symbol"] for l in raw.read_text().splitlines() if l.strip()]
    assert symbols == ["T0", "T1", "T2"], "pass-1 rows must survive a completion pass"

    ledger = json.loads((tmp_path / M.OUTCOMES_REL).read_text())
    assert ledger["n_failed"] == 0 and ledger["n_ok"] == 3
    assert ledger["passes"] == 2
    assert ledger["this_pass"]["only_failed"] is True


def test_an_outcome_ledger_without_ok_tickers_is_reconstructed(tmp_path):
    _write_tickers(tmp_path, 4)
    p = tmp_path / M.OUTCOMES_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"failed_tickers": ["T2", "T3"]}))
    out = M._load_outcomes(tmp_path)
    assert out["ok_tickers"] == ["T0", "T1"]

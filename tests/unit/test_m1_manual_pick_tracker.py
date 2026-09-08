"""Guards for the M1 manual-pick forward tracker (research-only).

The tracker exists to find out whether a human picking ten names out of the
M1 shadow pool was worth anything. Its value depends entirely on it being able
to come back "no", so these tests pin the properties that keep it honest:

* the cohort is write-once — it cannot be edited once outcomes exist;
* an unelapsed horizon is null, never zero;
* the control arm is the not-picked names, and beating SPY/QQQ alone never
  earns a positive verdict;
* the default verdict is NO_EDGE, and NOT_MATURE blocks any claim;
* it makes no provider call and writes only into the replay namespace.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import research.backtests.m1_manual_pick_tracker as T
from research.backtests.common import ReplayWriteViolation, assert_replay_write_path

REF = "2026-09-04"


def _frame(n: int, start: float = 100.0, step: float = 0.0) -> pd.DataFrame:
    idx = pd.bdate_range(end=pd.Timestamp(REF), periods=n)
    close = np.array([start + step * i for i in range(n)], dtype=float)
    return pd.DataFrame({"open": close, "high": close, "low": close,
                         "close": close, "volume": 1e6}, index=idx)


def _future_frame(n_before: int, n_after: int, *, ret_pct: float) -> pd.DataFrame:
    """A series whose bar at REF is followed by `n_after` bars ending ret_pct up."""
    before = _frame(n_before)
    idx = pd.bdate_range(start=pd.Timestamp(REF), periods=n_after + 1)[1:]
    end = 100.0 * (1 + ret_pct / 100.0)
    close = np.linspace(100.0, end, n_after + 1)[1:]
    after = pd.DataFrame({"open": close, "high": close, "low": close,
                          "close": close, "volume": 1e6}, index=idx)
    return pd.concat([before, after])


def sandbox(tmp_path: Path, frames: dict[str, pd.DataFrame], *,
            picks: list[str], control: list[str]) -> Path:
    d = tmp_path / T.PRICES_REL
    d.mkdir(parents=True)
    for sym, df in frames.items():
        df.to_parquet(d / f"{sym}.parquet")

    rows = [{"ticker": t, "reference_close": 100.0, "reference_bar": REF}
            for t in picks + control]
    (tmp_path / "cache/research").mkdir(parents=True, exist_ok=True)
    (tmp_path / T.POOL_ROWS_REL).write_text(
        "\n".join(json.dumps(r) for r in rows))
    return tmp_path


def seed_input(root: Path, picks: list[str]) -> None:
    """The human pick file the freeze stage reads."""
    spec = {"session": REF, "prior": "p", "selection_rule": {}, "hypothesis": "h",
            "falsification": "f", "known_weaknesses": [],
            "picks": [{"ticker": t, "sector": "X", "thesis": "t",
                       "invalidation": "i"} for t in picks]}
    (root / T.INPUT_REL).parent.mkdir(parents=True, exist_ok=True)
    (root / T.INPUT_REL).write_text(json.dumps(spec))


def cohort_for(tmp_path: Path, picks: list[str], control: list[str]) -> dict:
    return {
        "kind": "M1_MANUAL_PICK_COHORT", "session": REF,
        "horizons_sessions": [20, 45], "benchmarks": ["SPY", "QQQ"],
        "decisive_horizons": [45],
        "hypothesis": "h", "falsification": "f", "known_weaknesses": [],
        "picks": [{"ticker": t, "reference_close": 100.0, "reference_bar": REF,
                   "sector": "X"} for t in picks],
        "control_not_picked": [{"ticker": t, "reference_close": 100.0,
                                "reference_bar": REF} for t in control],
        "n_picks": len(picks), "n_control": len(control),
    }


# ── 1. the cohort is a pre-registration, not a running edit ─────────────────


def test_a_frozen_cohort_cannot_be_silently_overwritten(tmp_path, monkeypatch):
    root = sandbox(tmp_path, {"AAA": _frame(300)}, picks=["AAA"], control=[])
    seed_input(root, ["AAA"])
    dest = root / T.COHORT_REL_FMT.format(session=REF)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps({"session": REF}))

    args = T.build_parser().parse_args(["freeze", "--root", str(root)])
    with pytest.raises(T.CohortExists):
        T.freeze_stage(args)


def test_the_refusal_is_a_clean_exit_not_a_traceback(tmp_path):
    root = sandbox(tmp_path, {"AAA": _frame(300)}, picks=["AAA"], control=[])
    seed_input(root, ["AAA"])
    dest = root / T.COHORT_REL_FMT.format(session=REF)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps({"session": REF}))

    assert T.main(["freeze", "--root", str(root)]) == 3


def test_a_pick_outside_the_published_pool_is_refused(tmp_path, monkeypatch):
    """The test must measure selection FROM the pool, not a free-form list."""
    root = sandbox(tmp_path, {"AAA": _frame(300)}, picks=["AAA"], control=[])
    spec = {"session": REF, "prior": "p", "selection_rule": {}, "hypothesis": "h",
            "falsification": "f", "known_weaknesses": [],
            "picks": [{"ticker": "NOTINPOOL", "sector": "X", "thesis": "t",
                       "invalidation": "i"}]}
    (root / T.INPUT_REL).parent.mkdir(parents=True, exist_ok=True)
    (root / T.INPUT_REL).write_text(json.dumps(spec))

    with pytest.raises(ValueError, match="absent from the published pool"):
        T.build_cohort(root)


# ── 2. an unelapsed horizon is null, never zero ─────────────────────────────


def test_an_unelapsed_horizon_is_null_not_zero(tmp_path):
    """A zero here would fake a flat result into a measurable outcome."""
    root = sandbox(tmp_path, {"AAA": _frame(300)}, picks=["AAA"], control=[])
    assert T.forward_return_pct(root, "AAA", REF, 20) is None


def test_a_matured_horizon_is_computed_from_the_cache(tmp_path):
    root = sandbox(tmp_path, {"AAA": _future_frame(300, 25, ret_pct=10.0)},
                   picks=["AAA"], control=[])
    got = T.forward_return_pct(root, "AAA", REF, 20)
    assert got is not None and 7.0 < got < 11.0


def test_a_missing_series_resolves_to_null(tmp_path):
    root = sandbox(tmp_path, {}, picks=[], control=[])
    assert T.forward_return_pct(root, "GONE", REF, 20) is None


# ── 3. the verdict ladder defaults to NO EDGE ───────────────────────────────


def test_an_immature_cohort_blocks_every_claim(tmp_path):
    root = sandbox(tmp_path, {"AAA": _frame(300), "BBB": _frame(300),
                              "SPY": _frame(300), "QQQ": _frame(300)},
                   picks=["AAA"], control=["BBB"])
    c = cohort_for(root, ["AAA"], ["BBB"])
    v = T.verdict(c, T.resolve_cohort(root, c))
    assert v["verdict"] == "NOT_MATURE"
    assert v["may_conclude"] is False


def test_picks_losing_to_the_control_is_recorded_as_no_edge(tmp_path):
    root = sandbox(tmp_path, {
        "AAA": _future_frame(300, 50, ret_pct=2.0),      # pick, +2%
        "BBB": _future_frame(300, 50, ret_pct=20.0),     # control, +20%
        "SPY": _future_frame(300, 50, ret_pct=1.0),
        "QQQ": _future_frame(300, 50, ret_pct=1.0),
    }, picks=["AAA"], control=["BBB"])
    c = cohort_for(root, ["AAA"], ["BBB"])
    v = T.verdict(c, T.resolve_cohort(root, c))
    assert v["verdict"] == "NO_EDGE_FROM_SELECTION"


def test_beating_only_the_benchmarks_never_earns_a_positive_verdict(tmp_path):
    """The pool beats the tape by construction; that is not selection skill."""
    root = sandbox(tmp_path, {
        "AAA": _future_frame(300, 50, ret_pct=15.0),     # pick beats SPY...
        "BBB": _future_frame(300, 50, ret_pct=25.0),     # ...but loses to control
        "SPY": _future_frame(300, 50, ret_pct=1.0),
        "QQQ": _future_frame(300, 50, ret_pct=1.0),
    }, picks=["AAA"], control=["BBB"])
    c = cohort_for(root, ["AAA"], ["BBB"])
    resolved = T.resolve_cohort(root, c)
    assert resolved["horizons"]["45d"]["benchmarks"]["SPY"] < 15.0
    assert T.verdict(c, resolved)["verdict"] == "NO_EDGE_FROM_SELECTION"


def test_a_win_is_provisional_and_promotes_nothing(tmp_path):
    root = sandbox(tmp_path, {
        "AAA": _future_frame(300, 50, ret_pct=30.0),
        "BBB": _future_frame(300, 50, ret_pct=3.0),
        "SPY": _future_frame(300, 50, ret_pct=1.0),
        "QQQ": _future_frame(300, 50, ret_pct=1.0),
    }, picks=["AAA"], control=["BBB"])
    c = cohort_for(root, ["AAA"], ["BBB"])
    v = T.verdict(c, T.resolve_cohort(root, c))
    assert v["verdict"] == "SELECTION_ADDS_VALUE_PROVISIONAL"
    assert v["may_conclude"] is False          # a win still concludes nothing


def test_a_mean_only_win_is_not_enough(tmp_path):
    """Mean AND median must both favour the picks — one outlier cannot carry it."""
    root = sandbox(tmp_path, {
        "AAA": _future_frame(300, 50, ret_pct=120.0),    # one huge winner
        "AAB": _future_frame(300, 50, ret_pct=-10.0),
        "AAC": _future_frame(300, 50, ret_pct=-10.0),
        "BBB": _future_frame(300, 50, ret_pct=5.0),
        "BBC": _future_frame(300, 50, ret_pct=5.0),
        "SPY": _future_frame(300, 50, ret_pct=1.0),
        "QQQ": _future_frame(300, 50, ret_pct=1.0),
    }, picks=["AAA", "AAB", "AAC"], control=["BBB", "BBC"])
    c = cohort_for(root, ["AAA", "AAB", "AAC"], ["BBB", "BBC"])
    resolved = T.resolve_cohort(root, c)
    assert resolved["horizons"]["45d"]["picks_beat_control_mean"] is True
    assert resolved["horizons"]["45d"]["picks_beat_control_median"] is False
    assert T.verdict(c, resolved)["verdict"] == "NO_EDGE_FROM_SELECTION"


# ── 4. containment ──────────────────────────────────────────────────────────


def test_both_artifacts_live_in_the_replay_namespace():
    assert_replay_write_path(T.RESOLUTION_REL)
    assert_replay_write_path(T.REPORT_TXT_REL)
    assert_replay_write_path(T.COHORT_REL_FMT.format(session=REF))


@pytest.mark.parametrize("forbidden", [
    "cache/prices/AAA.parquet",
    "cache/research/research_scanner_latest.json",
    "cache/research/high_conviction_alpha_latest.json",
    "data/research/journal.jsonl",
    "db/trading.db",
])
def test_the_tracker_cannot_write_a_live_artifact(forbidden):
    with pytest.raises(ReplayWriteViolation):
        assert_replay_write_path(forbidden)


def test_neither_stage_makes_a_provider_call(tmp_path, monkeypatch):
    import socket

    monkeypatch.setattr(socket.socket, "connect",
                        lambda *a, **k: pytest.fail("tracker attempted a network call"))
    root = sandbox(tmp_path, {"AAA": _frame(300), "BBB": _frame(300),
                              "SPY": _frame(300), "QQQ": _frame(300)},
                   picks=["AAA"], control=["BBB"])
    c = cohort_for(root, ["AAA"], ["BBB"])
    T.resolve_cohort(root, c)


def test_the_module_emits_no_signal_ranking_or_verdict_token(tmp_path):
    """It publishes a research verdict, never a trade instruction."""
    text = Path("research/backtests/m1_manual_pick_tracker.py").read_text().lower()
    for banned in ("buy_signal", "entry_price", "position_size", "stop_loss",
                   "target_price", "submit_order"):
        assert banned not in text


def test_the_published_cohort_matches_the_reviewed_pool():
    """The real cohort on disk: ten picks, sixty-five controls, same session."""
    path = Path(T.COHORT_REL_FMT.format(session="2026-09-04"))
    if not path.exists():
        pytest.skip("cohort not frozen in this checkout")
    c = json.loads(path.read_text())
    assert c["n_picks"] == 10
    assert c["n_picks"] + c["n_control"] == 75
    assert all("thesis" in p and "invalidation" in p for p in c["picks"])

"""Guards for the M1 deep price-history refresh (research-only).

The refresh exists to repair what the data guard refuses on. These tests pin
the three properties that make it safe to point at ``cache/replay_prices`` —
the cache four completed M1 studies read:

* it never fetches without explicit authorisation, and never exceeds its cap;
* it is append-forward-only, so no historical bar the studies measured can be
  rewritten, and a provider disagreement is surfaced rather than absorbed;
* it writes bars only into the replay namespace, never into a live cache.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import research.backtests.m1_price_refresh as R
from research.backtests.common import (
    FetchNotAuthorised, ReplayWriteViolation, assert_replay_write_path,
)
from tests.unit.test_m1_data_guard import REQ, make_frame


def sandbox(tmp_path: Path, frames: dict[str, pd.DataFrame]) -> Path:
    d = tmp_path / R.PRICES_REL
    d.mkdir(parents=True)
    for sym, df in frames.items():
        df.to_parquet(d / f"{sym}.parquet")
    return tmp_path


def args_for(root: Path, stage: str = "plan", **kw):
    argv = [stage, "--root", str(root)]
    for k, v in kw.items():
        flag = "--" + k.replace("_", "-")
        argv += [flag] if v is True else [flag, str(v)]
    return R.build_parser().parse_args(argv)


# ── 1. planning is free and honest ──────────────────────────────────────────


def test_plan_makes_no_provider_call_and_reports_the_estimate(tmp_path, monkeypatch):
    import socket

    monkeypatch.setattr(socket.socket, "connect",
                        lambda *a, **k: pytest.fail("plan attempted a network call"))
    root = sandbox(tmp_path, {
        "DEEP": make_frame(400),                              # fine
        "SHAL": make_frame(150),                              # needs history
        "STAL": make_frame(400, last=REQ - timedelta(days=5)),  # needs a top-up
    })
    rc = R.plan_stage(args_for(root))
    assert rc == 0

    payload = json.loads((root / R.LATEST_REL).read_text())
    assert payload["provider_calls"] == 0
    assert payload["stage"] == "plan"
    plan = payload["plan"]
    assert plan["n_shallow"] == 1 and plan["n_stale"] == 1
    assert plan["planned_calls"] == 2 == plan["n_tickers"]
    assert plan["calls_per_ticker"] == 1
    assert plan["destination_path"] == "cache/replay_prices"
    assert "append-forward-only" in plan["write_mode"]


def test_plan_reports_every_field_the_brief_requires(tmp_path):
    root = sandbox(tmp_path, {"SHAL": make_frame(150)})
    R.plan_stage(args_for(root, tickers="NEWSYM"))
    payload = json.loads((root / R.LATEST_REL).read_text())
    assert payload["plan"]["n_tickers"] >= 1          # number of tickers
    assert payload["plan"]["planned_calls"] >= 1      # expected calls
    assert payload["plan"]["destination_path"]        # destination path
    assert payload["budget"]["budget_accounting"]     # budget impact
    assert set(payload["failure_behaviour"]) >= {     # failure behaviour
        "no_execute_fetch", "over_cap", "http_or_transport_error",
        "history_drift", "partial_run", "live_artifacts"}


def test_plan_does_not_read_the_monthly_budget_from_credentials(tmp_path):
    root = sandbox(tmp_path, {"SHAL": make_frame(150)})
    R.plan_stage(args_for(root))
    payload = json.loads((root / R.LATEST_REL).read_text())
    assert payload["budget"]["monthly_budget_read_here"] is False


def test_delisted_names_are_never_planned_for_a_refetch(tmp_path):
    """A call spent on a dead symbol cannot produce a bar."""
    root = sandbox(tmp_path, {"DEAD": make_frame(400, last=REQ - timedelta(days=400))})
    plan = R.plan_refresh(root)
    assert plan["n_tickers"] == 0
    assert "DEAD" not in plan["todo"]


def test_illiquid_names_are_not_planned(tmp_path):
    root = sandbox(tmp_path, {"PENNY": make_frame(150, price=0.5)})
    assert R.plan_refresh(root)["n_tickers"] == 0


def test_missing_tickers_are_planned_as_fetches(tmp_path):
    root = sandbox(tmp_path, {"DEEP": make_frame(400)})
    plan = R.plan_refresh(root, extra_tickers=["NEWSYM", "DEEP"])
    assert plan["tickers_missing"] == ["NEWSYM"]
    assert plan["n_missing"] == 1


# ── 2. the fetch gates ──────────────────────────────────────────────────────


def test_fetch_refuses_without_execute_fetch(tmp_path):
    root = sandbox(tmp_path, {"SHAL": make_frame(150)})
    with pytest.raises(FetchNotAuthorised) as e:
        R.fetch_stage(args_for(root, "fetch"))
    assert "--execute-fetch" in str(e.value)
    assert "No calls were made" in str(e.value)


def test_fetch_refuses_over_the_call_cap(tmp_path):
    root = sandbox(tmp_path, {f"S{i}": make_frame(150) for i in range(5)})
    with pytest.raises(FetchNotAuthorised) as e:
        R.fetch_stage(args_for(root, "fetch", execute_fetch=True, max_calls=2))
    assert "exceed" in str(e.value)


def test_main_turns_a_refusal_into_a_clean_exit_not_a_traceback(tmp_path):
    root = sandbox(tmp_path, {"SHAL": make_frame(150)})
    assert R.main(["fetch", "--root", str(root)]) == 3


def test_nothing_to_do_makes_zero_calls_even_when_authorised(tmp_path):
    root = sandbox(tmp_path, {"DEEP": make_frame(400)})
    assert R.fetch_stage(args_for(root, "fetch", execute_fetch=True)) == 0
    payload = json.loads((root / R.LATEST_REL).read_text())
    assert payload["provider_calls"] == 0
    assert payload["result"]["calls_made"] == 0


# ── 3. append-forward-only ──────────────────────────────────────────────────


def test_merge_appends_only_bars_after_the_stored_series():
    existing = make_frame(300, last=REQ - timedelta(days=7))
    incoming = make_frame(310, last=REQ)
    merged, status, added = R.merge_forward(existing, incoming)
    assert status == "APPENDED"
    assert added == len(merged) - len(existing)
    # every stored bar survives byte-identical
    pd.testing.assert_series_equal(
        merged.loc[existing.index, "close"], existing["close"])
    assert merged.index.max() == incoming.index.max()


def test_merge_refuses_when_the_provider_disagrees_with_stored_history():
    """A split re-adjustment must never be absorbed silently."""
    existing = make_frame(300, last=REQ - timedelta(days=7))
    incoming = make_frame(310, last=REQ)
    incoming["close"] = incoming["close"] * 0.25       # 4:1 re-adjustment
    merged, status, added = R.merge_forward(existing, incoming)
    assert status == "HISTORY_DRIFT"
    assert merged is None and added == 0


def test_history_rewrite_is_possible_only_when_explicitly_allowed():
    existing = make_frame(300, last=REQ - timedelta(days=7))
    incoming = make_frame(310, last=REQ)
    incoming["close"] = incoming["close"] * 0.25
    merged, status, added = R.merge_forward(existing, incoming, allow_rewrite=True)
    assert status == "REWRITTEN"
    assert merged is not None
    assert merged.loc[existing.index[-1], "close"] == pytest.approx(
        incoming.loc[existing.index[-1], "close"])


def test_float_noise_is_not_mistaken_for_drift():
    existing = make_frame(300, last=REQ - timedelta(days=7))
    incoming = make_frame(310, last=REQ)
    incoming["close"] = incoming["close"] * (1 + R.DRIFT_TOLERANCE / 10)
    _, status, _ = R.merge_forward(existing, incoming)
    assert status == "APPENDED"


def test_no_new_bars_is_not_a_write():
    existing = make_frame(300, last=REQ)
    merged, status, added = R.merge_forward(existing, make_frame(300, last=REQ))
    assert status == "NO_NEW_BARS"
    assert merged is None and added == 0


def test_new_series_is_created_whole():
    merged, status, added = R.merge_forward(None, make_frame(300, last=REQ))
    assert status == "CREATED" and added == 300


# ── 4. drift feeds the guard's refusal ──────────────────────────────────────


def test_recorded_drift_blocks_the_next_guard_run(tmp_path):
    import research.backtests.m1_data_guard as G
    from tests.unit.test_m1_data_guard import depths_for

    (tmp_path / "cache" / "research").mkdir(parents=True)
    (tmp_path / G.REFRESH_ARTIFACT_REL).write_text(json.dumps(
        {"failed_refresh_count": 0, "missing_ticker_count": 0,
         "history_drift_count": 2}))
    audit = G.evaluate(depths_for(3000, n_bars=400), required_session=REQ,
                       refresh=G.load_refresh_outcome(tmp_path))
    assert audit.may_run is False
    assert G.M1RefusalReason.HISTORY_DRIFT_DETECTED in audit.refusals


# ── 5. write containment ────────────────────────────────────────────────────


def test_refresh_writes_only_into_the_replay_namespace():
    for rel in (R.LATEST_REL, R.REPORT_TXT_REL, R.PROGRESS_REL,
                f"{R.PRICES_REL}/AAPL.parquet"):
        assert assert_replay_write_path(rel)


@pytest.mark.parametrize("forbidden", [
    "cache/prices/AAPL.parquet",
    "cache/prices_deep/AAPL.parquet",
    "cache/backtest_prices/AAPL.parquet",
    "cache/research/research_scanner_latest.json",
    "data/research/research_watchlist_history.jsonl",
    "db/trading.db",
])
def test_refresh_cannot_write_a_live_price_cache_or_ledger(forbidden):
    with pytest.raises(ReplayWriteViolation):
        assert_replay_write_path(forbidden)


def test_plan_touches_no_live_artifact_and_creates_no_live_dirs(tmp_path):
    root = sandbox(tmp_path, {"SHAL": make_frame(150), "DEEP": make_frame(400)})
    R.plan_stage(args_for(root))
    assert not (root / "cache" / "prices").exists()
    assert not (root / "cache" / "prices_deep").exists()
    assert not (root / "data").exists()
    assert not (root / "db").exists()
    written = {p.relative_to(root).as_posix()
               for p in root.rglob("*") if p.is_file()}
    assert written == {
        "cache/replay_prices/SHAL.parquet",
        "cache/replay_prices/DEEP.parquet",
        R.LATEST_REL, R.REPORT_TXT_REL,
    }


def test_refresh_emits_no_signal_ranking_or_verdict(tmp_path):
    root = sandbox(tmp_path, {"SHAL": make_frame(150)})
    R.plan_stage(args_for(root))
    payload = json.loads((root / R.LATEST_REL).read_text())
    assert payload["emits_signal"] is False
    assert payload["emits_ranking"] is False
    assert payload["research_only"] is True
    assert "verdict" not in payload
    blob = json.dumps(payload).lower()
    assert "validated_edge" not in blob


# ── 4. the write path itself, not just the helper ───────────────────────────
#
# The two tests above prove that `assert_replay_write_path` refuses live paths.
# They do NOT prove that the refresh CALLS it — and for one revision of this
# module it did not: `merged.to_parquet(path)` was the only bar-write in the M1
# foundation and it trusted its symbol. These tests drive the write path.


@pytest.mark.parametrize("hostile", [
    "../../cache/prices/SPY",
    "../prices_deep/AAPL",
    "/etc/passwd",
    "AAPL/../../db/trading",
    "AA PL",
    "",
])
def test_a_hostile_ticker_never_enters_the_plan(hostile):
    """A symbol becomes a path component, so it is validated at the boundary."""
    if hostile == "":
        assert R._extra(R.build_parser().parse_args(
            ["plan", "--tickers", ","])) == []
        return
    with pytest.raises(SystemExit) as e:
        R._extra(R.build_parser().parse_args(["plan", "--tickers", hostile]))
    assert "not a ticker" in str(e.value)


@pytest.mark.parametrize("good", ["AAPL", "BRK.B", "RDS-A", "F", "brk.b"])
def test_real_symbols_survive_validation(good):
    assert R.valid_ticker(good)
    assert R._extra(R.build_parser().parse_args(
        ["plan", "--tickers", good])) == [good.upper()]


def _fetch_args(root: Path):
    return args_for(root, "fetch", execute_fetch=True, max_calls=10)


def _stub_provider(monkeypatch, rows):
    """A 200 response carrying `rows`, and a gatekeeper that touches no DB."""
    import requests

    import core.data_gatekeeper as dg

    class _Resp:
        status_code = 200
        content = b"x"

        @staticmethod
        def json():
            return rows

    class _Gate:
        def budget_consume(self, n=1):
            return True

        def log_endpoint(self, *a, **k):
            pass

    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp())
    monkeypatch.setattr(dg, "get_gatekeeper", lambda: _Gate())


def _bars(*dates):
    return [{"date": d, "open": 10.0, "high": 10.0, "low": 10.0,
             "close": 10.0, "volume": 1_000_000} for d in dates]


def test_the_bar_write_is_refused_outside_the_replay_namespace(tmp_path, monkeypatch):
    """Defence in depth: even a symbol that bypasses the plan cannot be written.

    The plan is stubbed to smuggle a traversal symbol into the queue — the
    shape an input-validation bug would produce — and the write must still be
    refused before anything reaches disk.
    """
    root = sandbox(tmp_path, {"DEEP": make_frame(400)})
    hostile = "../../cache/prices/SPY"
    monkeypatch.setattr(R, "plan_refresh", lambda root_, **kw: {
        "todo": [hostile], "required_session": REQ.isoformat(),
        "cache_present": True, "series_scanned": 1, "quarantined_excluded": 0,
        "tickers_stale": [], "tickers_shallow": [], "tickers_missing": [hostile],
        "n_stale": 0, "n_shallow": 0, "n_missing": 1, "n_tickers": 1,
        "planned_calls": 1, "calls_per_ticker": 1, "endpoint": R.ENDPOINT,
        "destination_path": R.PRICES_REL,
        "write_mode": "append-forward-only (merge on write; no historical rewrite)",
    })
    _stub_provider(monkeypatch, _bars(REQ.isoformat()))

    with pytest.raises(ReplayWriteViolation) as e:
        R.fetch_stage(_fetch_args(root))
    assert "cache/prices" in str(e.value)
    assert not (root / "cache" / "prices").exists(), "no live cache may be created"
    assert not (root / "cache" / "prices_deep").exists()


def test_run_cli_turns_a_write_violation_into_exit_5(tmp_path, monkeypatch):
    hostile = "../../db/trading"
    root = sandbox(tmp_path, {"DEEP": make_frame(400)})
    monkeypatch.setattr(R, "plan_refresh", lambda root_, **kw: {
        "todo": [hostile], "required_session": REQ.isoformat(),
        "cache_present": True, "series_scanned": 1, "quarantined_excluded": 0,
        "tickers_stale": [], "tickers_shallow": [], "tickers_missing": [hostile],
        "n_stale": 0, "n_shallow": 0, "n_missing": 1, "n_tickers": 1,
        "planned_calls": 1, "calls_per_ticker": 1, "endpoint": R.ENDPOINT,
        "destination_path": R.PRICES_REL, "write_mode": "append-forward-only",
    })
    _stub_provider(monkeypatch, _bars(REQ.isoformat()))
    from research.backtests.common import run_cli

    assert run_cli(R.fetch_stage, _fetch_args(root)) == 5
    assert not (root / "db").exists()


def test_a_legitimate_symbol_still_writes_into_the_replay_cache(tmp_path, monkeypatch):
    """The guard must not break the thing it protects.

    A stale-but-agreeing series: the provider returns the stored bars unchanged
    plus the sessions that were missed, so the merge appends rather than
    tripping the drift check.
    """
    df = make_frame(300, last=REQ - timedelta(days=7))
    root = sandbox(tmp_path, {"STAL": df})

    def _row(d, close):
        return {"date": pd.Timestamp(d).date().isoformat(), "open": close,
                "high": close, "low": close, "close": close, "volume": 1_000_000}

    rows = [_row(d, float(c)) for d, c in zip(df.index, df["close"])]
    tail = float(df["close"].iloc[-1])
    new_dates = pd.bdate_range(start=df.index[-1] + pd.Timedelta(days=1),
                               end=pd.Timestamp(REQ))
    rows += [_row(d, tail) for d in new_dates]
    assert len(new_dates) > 0, "fixture must actually have sessions to add"
    _stub_provider(monkeypatch, rows)

    rc = R.fetch_stage(_fetch_args(root))
    assert rc == 0
    out = root / R.PRICES_REL / "STAL.parquet"
    assert out.exists(), "a valid ticker must still be written"
    merged = pd.read_parquet(out)
    assert len(merged) == len(df) + len(new_dates), "only the new bars were added"
    assert str(pd.DatetimeIndex(merged.index)[-1].date()) == REQ.isoformat()
    payload = json.loads((root / R.LATEST_REL).read_text())
    assert payload["result"]["updated_count"] == 1
    assert payload["result"]["history_drift_count"] == 0
    assert payload["result"]["bars_added"] == len(new_dates)

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


@pytest.fixture(autouse=True)
def pin_required_session(monkeypatch):
    """Pin the session these fixtures are built around.

    ``make_frame`` ends its bars on a fixed date and ``REQ`` is that same fixed
    date, but ``plan_refresh`` asks ``required_market_session()`` for today when
    no clock is passed. Every staleness, young-listing and provider-lag
    assertion in this file therefore drifted as the real calendar advanced: a
    frame ending on REQ became "stale", a 120-bar listing aged past the
    400-calendar-day young-listing window, and the recheck dates moved.

    Pinning the clock is the whole fix — no threshold, rule or assertion is
    relaxed, and the module under test is unchanged. ``_required_session`` is
    the single date source the plan consults: ``scan_cache`` reads bar dates
    from the parquet files, and ``is_stale`` / ``is_active`` /
    ``_is_young_listing`` / ``build_provider_lag_entry`` all take the date
    explicitly from it.
    """
    monkeypatch.setattr(R, "_required_session", lambda now=None: REQ)


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
        "SHAL": make_frame(150, stride=8),                    # long-listed but gappy
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
    root = sandbox(tmp_path, {"SHAL": make_frame(150, stride=8)})
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
    root = sandbox(tmp_path, {"SHAL": make_frame(150, stride=8)})
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
    root = sandbox(tmp_path, {"SHAL": make_frame(150, stride=8)})
    with pytest.raises(FetchNotAuthorised) as e:
        R.fetch_stage(args_for(root, "fetch"))
    assert "--execute-fetch" in str(e.value)
    assert "No calls were made" in str(e.value)


def test_fetch_refuses_over_the_call_cap(tmp_path):
    root = sandbox(tmp_path, {f"S{i}": make_frame(150, stride=8) for i in range(5)})
    with pytest.raises(FetchNotAuthorised) as e:
        R.fetch_stage(args_for(root, "fetch", execute_fetch=True, max_calls=2))
    assert "exceed" in str(e.value)


def test_main_turns_a_refusal_into_a_clean_exit_not_a_traceback(tmp_path):
    root = sandbox(tmp_path, {"SHAL": make_frame(150, stride=8)})
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
    root = sandbox(tmp_path, {"SHAL": make_frame(150, stride=8), "DEEP": make_frame(400)})
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
    root = sandbox(tmp_path, {"SHAL": make_frame(150, stride=8)})
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
    """A 200 response carrying `rows`, and a gatekeeper that touches no DB.

    Also installs a non-sentinel key: these tests exercise the WRITE path, and
    the credential gate would otherwise refuse before reaching it (the unit
    suite's own stub key is a sentinel, by design).
    """
    import requests

    import core.data_gatekeeper as dg

    monkeypatch.setenv("FMP_API_KEY", "unittestkey1234567890")
    monkeypatch.delenv("SNIPER_ENV_PATH", raising=False)

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


# ── 5. the credential path ──────────────────────────────────────────────────
#
# The failure this pins actually happened, on 2026-09-07: `offline_env()` runs
# at import and sets FMP_API_KEY to the sentinel "offline", the fetch loaded the
# credential file with override=False so the sentinel survived, and all 108
# approved calls came back 401. The budget was spent discovering something that
# was knowable before the first call.


def test_offline_sentinel_is_refused_before_any_call(tmp_path, monkeypatch):
    import requests

    root = sandbox(tmp_path, {"SHAL": make_frame(150, stride=8)})
    monkeypatch.setenv("FMP_API_KEY", "offline")
    monkeypatch.delenv("SNIPER_ENV_PATH", raising=False)
    monkeypatch.setattr(requests, "get",
                        lambda *a, **k: pytest.fail("a call was made with a sentinel key"))
    with pytest.raises(FetchNotAuthorised) as e:
        R.fetch_stage(args_for(root, "fetch", execute_fetch=True, max_calls=10))
    assert "offline sentinel" in str(e.value)
    assert "No calls were made" in str(e.value)


def test_an_empty_credential_is_refused_before_any_call(tmp_path, monkeypatch):
    import requests

    root = sandbox(tmp_path, {"SHAL": make_frame(150, stride=8)})
    monkeypatch.setenv("FMP_API_KEY", "")
    monkeypatch.delenv("SNIPER_ENV_PATH", raising=False)
    monkeypatch.setattr(requests, "get",
                        lambda *a, **k: pytest.fail("a call was made with no key"))
    with pytest.raises(FetchNotAuthorised):
        R.fetch_stage(args_for(root, "fetch", execute_fetch=True, max_calls=10))


def test_the_credential_file_overrides_the_import_time_sentinel(tmp_path, monkeypatch):
    """override=False was the bug: the sentinel outranked the real key."""
    cred = tmp_path / "creds.env"
    cred.write_text("FMP_API_KEY=realkey123456\n")
    monkeypatch.setenv("FMP_API_KEY", "offline")
    monkeypatch.setenv("SNIPER_ENV_PATH", str(cred))
    seen = {}

    class _Resp:
        status_code = 200
        content = b"[]"

        @staticmethod
        def json():
            return []

    def _get(url, params=None, timeout=None):
        seen["key"] = (params or {}).get("apikey")
        return _Resp()

    import requests

    import core.data_gatekeeper as dg

    class _Gate:
        def budget_consume(self, n=1):
            return True

        def log_endpoint(self, *a, **k):
            pass

    monkeypatch.setattr(requests, "get", _get)
    monkeypatch.setattr(dg, "get_gatekeeper", lambda: _Gate())
    root = sandbox(tmp_path, {"SHAL": make_frame(150, stride=8)})
    R.fetch_stage(args_for(root, "fetch", execute_fetch=True, max_calls=10))
    assert seen["key"] == "realkey123456", "the credential file must win"


def test_a_conftest_stub_key_is_also_refused(tmp_path, monkeypatch):
    """The unit suite's stub key authenticates as nobody; refuse it too."""
    import requests

    root = sandbox(tmp_path, {"SHAL": make_frame(150, stride=8)})
    monkeypatch.setenv("FMP_API_KEY", "test_fmp_key")
    monkeypatch.delenv("SNIPER_ENV_PATH", raising=False)
    monkeypatch.setattr(requests, "get",
                        lambda *a, **k: pytest.fail("a call was made with a stub key"))
    with pytest.raises(FetchNotAuthorised):
        R.fetch_stage(args_for(root, "fetch", execute_fetch=True, max_calls=10))


# ── 6. the queue only holds names a refetch can change ──────────────────────
#
# Measured 2026-09-08: the queue held 108 names. 89 were young listings that
# cannot reach the depth floor because the bars do not exist yet, and the other
# 19 were behind the required session at the PROVIDER, which a refetch cannot
# fix either. The plan now says so instead of quoting 108.


def test_a_young_listing_is_not_queued(tmp_path):
    """Shallow because it is new, not because bars are missing."""
    root = sandbox(tmp_path, {"NEWCO": make_frame(120)})   # ~120 business days
    plan = R.plan_refresh(root)
    assert plan["n_shallow"] == 0
    assert plan["n_young_listing_excluded"] == 1
    assert "NEWCO" in plan["tickers_young_listing_excluded"]
    assert plan["todo"] == []


def test_a_genuinely_gappy_series_is_still_queued(tmp_path):
    """Long-listed but thin on bars: a refetch might actually deepen it."""
    root = sandbox(tmp_path, {"GAPPY": make_frame(150, stride=8)})
    plan = R.plan_refresh(root)
    assert "GAPPY" in plan["tickers_shallow"]
    assert plan["n_young_listing_excluded"] == 0
    assert plan["todo"] == ["GAPPY"]


def test_only_stale_restricts_the_queue(tmp_path):
    root = sandbox(tmp_path, {
        "STAL": make_frame(400, last=REQ - timedelta(days=5)),
        "GAPPY": make_frame(150, stride=8),
    })
    full = R.plan_refresh(root)
    assert set(full["todo"]) == {"STAL", "GAPPY"}
    only = R.plan_refresh(root, only_stale=True)
    assert only["todo"] == ["STAL"]
    assert only["only_stale"] is True


def test_the_young_listing_rule_is_published_in_the_plan(tmp_path):
    root = sandbox(tmp_path, {"NEWCO": make_frame(120)})
    plan = R.plan_refresh(root)
    assert str(R.YOUNG_LISTING_CALENDAR_DAYS) in plan["young_listing_rule"]


# ── 9. a provider that is itself behind must not be re-billed every run ─────
#
# The 2026-09-08 fetch spent 19 calls and added 0 bars: every one of the stale
# names came back NO_NEW_BARS, and a direct probe showed the provider holding
# no bar past the one already cached. "Stale" had been read as "our cache is
# behind" when it actually meant "the provider is behind". Unfixed, that is
# ~400 wasted calls a month to re-learn the same fact.
#
# The memo records what the PROVIDER had, and defers the name — without ever
# hiding it from the staleness count, which is the cache's real state.


def _lag_memo(root: Path) -> dict:
    return json.loads((root / R.PROVIDER_LAG_REL).read_text())


def _seed_memo(root: Path, sym: str, *, provider_last: str, cache_last: str,
               observed_on: date) -> None:
    R.save_provider_lag(root, {sym: R.build_provider_lag_entry(
        provider_last_bar=provider_last, cache_last_bar=cache_last,
        observed_on=observed_on)})


def _bars_at(price: float, *dates):
    """Bars that AGREE with a stored close, so the drift guard stays quiet.

    The shared ``_bars`` helper prices at 10.0; ``make_frame`` stores 50.0, and
    an overlapping bar that disagrees is a HISTORY_DRIFT by design. These tests
    are about the lag memo, not about drift.
    """
    return [{"date": d, "open": price, "high": price, "low": price,
             "close": price, "volume": 1_000_000} for d in dates]


def _stale_sandbox(tmp_path: Path) -> tuple[Path, str]:
    """A cache holding one stale name, plus its ACTUAL last bar.

    ``make_frame`` snaps to business days, so the last bar is not simply the
    date handed in — and the memo is keyed to the stored bar, not to intent.
    """
    frame = make_frame(400, last=REQ - timedelta(days=5))
    return sandbox(tmp_path, {"STAL": frame}), str(frame.index.max())[:10]


def test_a_no_new_bars_fetch_records_what_the_provider_had(tmp_path, monkeypatch):
    """The call is spent once; its answer is kept."""
    root, last = _stale_sandbox(tmp_path)
    # The provider returns history that stops exactly where the cache does.
    _stub_provider(monkeypatch, _bars_at(50.0, last))
    rc = R.fetch_stage(_fetch_args(root))
    assert rc == 0

    memo = _lag_memo(root)
    entry = memo["entries"]["STAL"]
    assert entry["provider_last_bar"] == last
    assert entry["cache_last_bar"] == last
    assert entry["confirmations"] == 1
    assert entry["recheck_on"] == str(REQ + timedelta(days=R.PROVIDER_LAG_RECHECK_DAYS))


def test_a_provider_lagged_name_is_not_re_queued_the_next_run(tmp_path):
    """The whole point: the second run costs nothing."""
    root, last = _stale_sandbox(tmp_path)
    _seed_memo(root, "STAL", provider_last=last, cache_last=last,
               observed_on=REQ)

    plan = R.plan_refresh(root)
    assert plan["todo"] == []
    assert plan["planned_calls"] == 0
    assert plan["tickers_provider_lag_deferred"] == ["STAL"]


def test_deferring_never_hides_the_staleness_itself(tmp_path):
    """The queue shrinks; the cache's real state is still reported in full."""
    root, last = _stale_sandbox(tmp_path)
    _seed_memo(root, "STAL", provider_last=last, cache_last=last,
               observed_on=REQ)

    plan = R.plan_refresh(root)
    assert plan["n_stale"] == 1                  # still stale, and still said so
    assert "STAL" in plan["tickers_stale"]
    assert plan["n_stale_queued"] == 0
    assert plan["n_provider_lag_deferred"] == 1
    assert "provider-lagged deferred 1" in R.render_report(
        plan, R.budget_block(0, 10), None)


def test_the_deferral_expires_and_the_name_returns_to_the_queue(tmp_path):
    """A provider that starts publishing again must be picked up."""
    root, last = _stale_sandbox(tmp_path)
    _seed_memo(root, "STAL", provider_last=last, cache_last=last,
               observed_on=REQ - timedelta(days=R.PROVIDER_LAG_RECHECK_DAYS))

    assert R.plan_refresh(root)["todo"] == ["STAL"]


def test_a_cache_that_moved_on_invalidates_the_memo(tmp_path):
    """The memo describes a series that no longer exists, so it says nothing."""
    root, _ = _stale_sandbox(tmp_path)
    _seed_memo(root, "STAL", provider_last="2026-01-02", cache_last="2026-01-02",
               observed_on=REQ)

    assert R.plan_refresh(root)["todo"] == ["STAL"]


def test_recheck_provider_lag_ignores_the_memo(tmp_path):
    root, last = _stale_sandbox(tmp_path)
    _seed_memo(root, "STAL", provider_last=last, cache_last=last,
               observed_on=REQ)

    plan = R.plan_refresh(root, recheck_provider_lag=True)
    assert plan["todo"] == ["STAL"]
    assert plan["recheck_provider_lag"] is True
    assert "IGNORED" in R.render_report(plan, R.budget_block(1, 10), None)


def test_a_corrupt_memo_fails_open(tmp_path):
    """Unreadable memo costs calls, never data."""
    root, _ = _stale_sandbox(tmp_path)
    path = root / R.PROVIDER_LAG_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json at all")

    assert R.load_provider_lag(root) == {}
    assert R.plan_refresh(root)["todo"] == ["STAL"]


def test_a_successful_append_clears_the_memo(tmp_path, monkeypatch):
    """The provider moved, so the reason to skip it is gone."""
    root, last = _stale_sandbox(tmp_path)
    _seed_memo(root, "STAL", provider_last=last, cache_last=last,
               observed_on=REQ - timedelta(days=R.PROVIDER_LAG_RECHECK_DAYS))
    _stub_provider(monkeypatch, _bars_at(50.0, last, str(REQ)))

    assert R.fetch_stage(_fetch_args(root)) == 0
    assert _lag_memo(root)["entries"] == {}


def test_a_repeat_confirmation_increments_rather_than_resets(tmp_path, monkeypatch):
    """How long a name has been provider-lagged stays visible."""
    root, last = _stale_sandbox(tmp_path)
    _stub_provider(monkeypatch, _bars_at(50.0, last))

    R.fetch_stage(_fetch_args(root))
    R.fetch_stage(args_for(root, "fetch", execute_fetch=True, max_calls=10,
                           recheck_provider_lag=True))
    assert _lag_memo(root)["entries"]["STAL"]["confirmations"] == 2


def test_the_memo_lives_in_the_replay_namespace(tmp_path):
    assert_replay_write_path(R.PROVIDER_LAG_REL)


def test_the_memo_is_never_written_by_the_plan_stage(tmp_path):
    """Planning stays a pure read: it may not create state a fetch is due to."""
    root = sandbox(tmp_path, {"STAL": make_frame(400, last=REQ - timedelta(days=5))})
    R.plan_stage(args_for(root))
    assert not (root / R.PROVIDER_LAG_REL).exists()


# ── 10. a tracked cohort stays current without paying for the universe ──────
#
# The shadow pool needs the whole replay universe inside the guard's 2-session
# cache-lag limit, which is a 3,269-call refresh once the required session
# moves on. A frozen cohort needs only its own 75 names. --only-tickers is what
# makes the second possible without the first.


def test_only_tickers_restricts_the_queue(tmp_path):
    root = sandbox(tmp_path, {
        "AAA": make_frame(400, last=REQ - timedelta(days=5)),
        "BBB": make_frame(400, last=REQ - timedelta(days=5)),
        "CCC": make_frame(400, last=REQ - timedelta(days=5)),
    })
    assert set(R.plan_refresh(root)["todo"]) == {"AAA", "BBB", "CCC"}

    plan = R.plan_refresh(root, only_tickers=["AAA", "CCC"])
    assert plan["todo"] == ["AAA", "CCC"]
    assert plan["n_only_tickers"] == 2
    assert "queue restricted to 2 named tickers" in R.render_report(
        plan, R.budget_block(2, 10), None)


def test_only_tickers_narrows_but_never_widens(tmp_path):
    """Naming a ticker cannot resurrect one another rule already excluded."""
    root = sandbox(tmp_path, {
        "STAL": make_frame(400, last=REQ - timedelta(days=5)),
        "NEWCO": make_frame(120),                      # young listing: excluded
    })
    plan = R.plan_refresh(root, only_tickers=["STAL", "NEWCO"])
    assert plan["todo"] == ["STAL"]


def test_only_tickers_still_honours_the_provider_lag_memo(tmp_path):
    root, last = _stale_sandbox(tmp_path)
    _seed_memo(root, "STAL", provider_last=last, cache_last=last, observed_on=REQ)
    assert R.plan_refresh(root, only_tickers=["STAL"])["todo"] == []


@pytest.mark.parametrize("hostile", ["../../etc/passwd", "A/../B", "A B"])
def test_a_hostile_only_tickers_value_is_refused(hostile):
    args = args_for(Path("/tmp"), "plan", only_tickers=hostile)
    with pytest.raises(SystemExit):
        R._only_tickers(args)

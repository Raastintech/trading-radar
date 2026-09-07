"""Guards for the M1 price-depth audit and staleness gate (research-only).

These tests prove the properties the M1 decision memo made a precondition for
building a shadow report at all:

* the report REFUSES on a shallow cache (the live-cache failure mode: median
  113 bars against the 253 that 12-1 needs);
* it PASSES on sufficient deep history;
* staleness, coverage, contamination, drift and pool-size guards each fire on
  their own condition and only on it;
* nothing writes a live artifact, and nothing calls a provider.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import research.backtests.m1_data_guard as G
from research.backtests.common import ReplayWriteViolation, assert_replay_write_path


# ── synthetic series ────────────────────────────────────────────────────────


def make_frame(n_bars: int, *, last: date = date(2026, 9, 4), price: float = 50.0,
               volume: float = 1_000_000.0, stride: int = 1,
               spike_at: int | None = None, spike_mult: float = 1.0,
               tail_mult: float | None = None, trend: float = 0.0) -> pd.DataFrame:
    """A well-formed daily frame ending on `last`.

    Bars sit on BUSINESS days, because the guard's calendar-span band is
    calibrated to real trading calendars (~252 sessions per 366 days). A
    `stride` above 1 keeps every Nth business day, which is how a gappy series
    is simulated: enough rows, but spanning far too much calendar time.
    """
    idx = pd.bdate_range(end=pd.Timestamp(last), periods=n_bars * stride)
    idx = idx[::-1][::stride][::-1]          # anchor the stride at the last bar
    close = np.array([price * (1.0 + trend) ** i for i in range(len(idx))],
                     dtype=float)
    if spike_at is not None:                 # one-session shock, price recovers
        close[spike_at] *= spike_mult
    if tail_mult is not None:                # permanent level break (split artifact)
        close[spike_at or len(idx) // 2:] *= tail_mult
    return pd.DataFrame(
        {"close": close, "volume": np.full(len(idx), volume, dtype=float)},
        index=idx,
    )


def depths_for(n: int, **kw) -> list[G.TickerDepth]:
    """N identical measured series.

    The frame is built and measured ONCE and then cloned under fresh tickers:
    `measure_series` is pure, so this is the same result as measuring N copies,
    and it keeps a 3,000-name universe fixture to milliseconds.
    """
    import dataclasses

    base = G.measure_series("T0000", make_frame(**kw))
    return [dataclasses.replace(base, ticker=f"T{i:04d}") for i in range(n)]


REQ = date(2026, 9, 4)


# ── 1. bar requirements are derived from the M1 definition ──────────────────


def test_bar_requirement_matches_the_lag_used_by_mom_12_1():
    """253 bars is not a preference: _lag_ratio(c, 252) is NaN below it."""
    assert G.M1_MIN_BARS_STRICT == G.M1_LOOKBACK_SESSIONS + 1 == 253
    short = np.arange(1.0, 1.0 + G.M1_LOOKBACK_SESSIONS)  # 252 bars
    assert np.isnan(G._lag_ratio(short, G.M1_LOOKBACK_SESSIONS)[-1])
    exact = np.arange(1.0, 1.0 + G.M1_MIN_BARS_STRICT)    # 253 bars
    assert np.isfinite(G._lag_ratio(exact, G.M1_LOOKBACK_SESSIONS)[-1])


def test_operating_floor_adds_the_excluded_month_as_buffer():
    assert G.M1_MIN_BARS_REQUIRED == G.M1_LOOKBACK_SESSIONS + G.M1_SKIP_SESSIONS == 273
    assert G.M1_MIN_BARS_REQUIRED > G.M1_MIN_BARS_STRICT


def test_mom_12_1_matches_the_tournament_formula():
    """The guard must not redefine M1; it recomputes the imported formula."""
    df = make_frame(400, trend=0.002)
    d = G.measure_series("X", df)
    c = np.asarray(df["close"], dtype=float)
    r252 = G._lag_ratio(c, 252)[-1]
    r21 = G._lag_ratio(c, 21)[-1]
    expected = ((1 + r252 / 100.0) / (1 + r21 / 100.0) - 1.0) * 100.0
    assert d.mom_12_1 == pytest.approx(expected)


# ── 2. shallow cache refuses; deep cache passes ─────────────────────────────


def test_refuses_on_shallow_cache_the_live_cache_failure_mode():
    """cache/prices' real shape: ~113 bars. 12-1 is not computable."""
    audit = G.evaluate(depths_for(3000, n_bars=113), required_session=REQ)
    assert audit.status is G.M1DataStatus.M1_DATA_BLOCKED
    assert audit.may_run is False
    assert G.M1RefusalReason.SHALLOW_HISTORY in audit.refusals
    assert audit.header["names_with_enough_bars"] == 0
    assert audit.header["coverage_pct"] == 0.0
    assert audit.header["shallow_pct"] == 100.0
    assert audit.header["refusal_reason"] == "SHALLOW_HISTORY"
    # the shallow names stay IN the candidate universe so the failure is
    # visible, rather than being hidden by the tournament's 120-bar floor
    assert audit.header["universe_size"] == 3000


def test_passes_on_sufficient_deep_history():
    audit = G.evaluate(depths_for(3000, n_bars=400), required_session=REQ)
    assert audit.status is G.M1DataStatus.M1_DATA_READY
    assert audit.may_run is True
    assert audit.refusals == []
    assert audit.header["coverage_pct"] == 100.0
    assert audit.header["m1_pool_size_if_run"] == 600


def test_bar_count_just_below_the_floor_is_refused():
    """The floor is a real edge, not a rounding suggestion."""
    below = G.evaluate(depths_for(3000, n_bars=G.M1_MIN_BARS_REQUIRED - 1),
                       required_session=REQ)
    at = G.evaluate(depths_for(3000, n_bars=G.M1_MIN_BARS_REQUIRED),
                    required_session=REQ)
    assert below.may_run is False
    assert G.M1RefusalReason.SHALLOW_HISTORY in below.refusals
    assert at.may_run is True
    assert at.header["shallow_count"] == 0


# ── 3. non-trading days and missing bars ────────────────────────────────────


def test_gappy_series_with_enough_rows_is_excluded_from_coverage():
    """253 rows spread over three years is not a twelve-month lookback."""
    d = G.measure_series("GAP", make_frame(300, stride=4))
    assert d.bars >= G.M1_MIN_BARS_REQUIRED
    assert d.depth_ok is True
    assert d.span_ok is False          # span far exceeds the calendar band
    assert d.has_m1 is False


def test_normal_weekday_spacing_is_inside_the_calendar_band():
    """~252 sessions of real spacing must NOT be flagged as gappy."""
    idx = pd.bdate_range(end="2026-09-04", periods=300)
    df = pd.DataFrame({"close": np.linspace(10, 20, 300),
                       "volume": np.full(300, 2e6)}, index=idx)
    d = G.measure_series("BD", df)
    assert d.span_ok is True
    assert d.has_m1 is True
    assert G.M1_SPAN_MIN_CALENDAR_DAYS <= d.span_252_calendar_days <= G.M1_SPAN_MAX_CALENDAR_DAYS


def test_span_broken_names_warn_rather_than_pass_silently():
    good = depths_for(2900, n_bars=400)
    gappy = depths_for(100, n_bars=300, stride=4)
    audit = G.evaluate(good + gappy, required_session=REQ)
    assert audit.header["span_broken_count"] == 100
    assert any("span" in w for w in audit.warnings)


# ── 4. staleness ────────────────────────────────────────────────────────────


def test_stale_share_above_threshold_refuses():
    fresh = depths_for(2000, n_bars=400, last=REQ)
    stale = depths_for(1000, n_bars=400, last=REQ - timedelta(days=3))
    audit = G.evaluate(fresh + stale, required_session=REQ)
    assert G.M1RefusalReason.STALE_CANDIDATES_ABOVE_THRESHOLD in audit.refusals
    assert audit.may_run is False
    assert audit.header["stale_count"] == 1000


def test_small_stale_share_does_not_refuse():
    fresh = depths_for(2950, n_bars=400, last=REQ)
    stale = depths_for(50, n_bars=400, last=REQ - timedelta(days=3))
    audit = G.evaluate(fresh + stale, required_session=REQ)
    assert G.M1RefusalReason.STALE_CANDIDATES_ABOVE_THRESHOLD not in audit.refusals
    assert audit.header["stale_count"] == 50


def test_whole_cache_frozen_refuses_the_2026_07_02_failure_mode():
    """Everything frozen together — the exact incident the guard exists for."""
    frozen = depths_for(3000, n_bars=400, last=REQ - timedelta(days=30))
    audit = G.evaluate(frozen, required_session=REQ)
    assert audit.may_run is False
    assert G.M1RefusalReason.CACHE_STALE in audit.refusals


def test_delisted_names_are_inactive_not_stale():
    """The cache carries delisted names to a final bar by design.

    Counting them as stale would block every run forever — the bug this test
    pins. They leave the candidate universe and are reported separately.
    """
    live = depths_for(3000, n_bars=400, last=REQ)
    dead = depths_for(1800, n_bars=400, last=REQ - timedelta(days=400))
    audit = G.evaluate(live + dead, required_session=REQ)
    assert audit.header["universe_size"] == 3000
    assert audit.header["inactive_excluded"] == 1800
    assert audit.header["stale_count"] == 0
    assert audit.may_run is True


def test_staleness_uses_the_required_session_not_a_calendar_threshold():
    """Per the 2026-07-10 pipeline review (P0)."""
    d = G.measure_series("S", make_frame(400, last=REQ - timedelta(days=1)))
    assert d.is_stale(REQ) is True         # one session behind → stale
    assert d.is_active(REQ) is True        # but still trading
    assert d.is_stale(REQ - timedelta(days=1)) is False


# ── 5. universe coverage ────────────────────────────────────────────────────


def test_coverage_below_threshold_refuses():
    deep = depths_for(2100, n_bars=400)
    thin = depths_for(900, n_bars=150)
    audit = G.evaluate(deep + thin, required_session=REQ)
    assert audit.header["coverage_pct"] == pytest.approx(70.0, abs=0.1)
    assert G.M1RefusalReason.UNIVERSE_COVERAGE_BELOW_THRESHOLD in audit.refusals


def test_pool_too_small_refuses_even_when_coverage_is_perfect():
    """100% coverage of a tiny universe still cannot fill a wide list."""
    audit = G.evaluate(depths_for(400, n_bars=400), required_session=REQ)
    assert audit.header["coverage_pct"] == 100.0
    assert G.M1RefusalReason.POOL_TOO_SMALL in audit.refusals
    assert G.M1RefusalReason.INSUFFICIENT_POOL_INPUT in audit.refusals
    # depth is fine here — the universe is simply too small. The guard must
    # not blame the bars for a size problem.
    assert G.M1RefusalReason.SHALLOW_HISTORY not in audit.refusals


# ── 6. contamination and drift ──────────────────────────────────────────────


def test_single_session_split_artifact_is_detected():
    """A +900% session: physically implausible for an adjusted series."""
    d = G.measure_series("SPK", make_frame(400, spike_at=200, spike_mult=10.0))
    assert d.contaminated is True
    assert d.contamination_reason == "split_artifact_single_day"
    assert d.has_m1 is False


def test_reverse_split_range_ratio_is_detected():
    """PPCB's shape: 37,500,000 -> 1.35 across the series."""
    d = G.measure_series("PPCB", make_frame(400, price=4e6, tail_mult=1e-6))
    assert d.contaminated is True
    assert d.contamination_reason in {"split_artifact_single_day",
                                      "split_artifact_range_ratio"}
    assert d.has_m1 is False


def test_a_real_moonshot_is_not_quarantined():
    """The rules must not fire on genuine winners (NVDA/CVNA were verified)."""
    d = G.measure_series("REAL", make_frame(400, price=10.0, trend=0.01))
    assert d.contaminated is False
    assert d.has_m1 is True


def test_contamination_above_threshold_refuses():
    clean = depths_for(2900, n_bars=400)
    bad = depths_for(200, n_bars=400, spike_at=200, spike_mult=10.0)
    bad = [dataclasses.replace(d, ticker=f"B{i}") for i, d in enumerate(bad)]
    audit = G.evaluate(clean + bad, required_session=REQ)
    assert audit.header["contamination_pct"] > G.MAX_NEW_CONTAMINATION_PCT
    assert G.M1RefusalReason.CONTAMINATION_ABOVE_THRESHOLD in audit.refusals


def test_contamination_at_the_observed_baseline_does_not_refuse():
    """1.55% is the measured standing rate of this cache, not a regression."""
    clean = depths_for(2948, n_bars=400)
    bad = depths_for(52, n_bars=400, spike_at=200, spike_mult=10.0)
    bad = [dataclasses.replace(d, ticker=f"B{i}") for i, d in enumerate(bad)]
    audit = G.evaluate(clean + bad, required_session=REQ)
    assert audit.header["contamination_pct"] <= G.MAX_NEW_CONTAMINATION_PCT
    assert G.M1RefusalReason.CONTAMINATION_ABOVE_THRESHOLD not in audit.refusals


def test_any_history_drift_refuses():
    audit = G.evaluate(depths_for(3000, n_bars=400), required_session=REQ,
                       refresh={"history_drift_count": 1})
    assert G.M1RefusalReason.HISTORY_DRIFT_DETECTED in audit.refusals
    assert audit.may_run is False


def test_missing_deep_cache_refuses():
    audit = G.evaluate([], required_session=REQ, cache_present=False)
    assert audit.status is G.M1DataStatus.M1_DATA_BLOCKED
    assert audit.refusals == [G.M1RefusalReason.DEEP_CACHE_MISSING]
    assert audit.header["refusal_reason"] == "DEEP_CACHE_MISSING"


# ── 7. the data-integrity header ────────────────────────────────────────────


REQUIRED_HEADER_FIELDS = (
    "universe_size", "names_with_enough_bars", "coverage_pct", "median_bars",
    "stale_count", "failed_refresh_count", "missing_ticker_count",
    "refusal_reason",
)


def test_header_carries_every_required_field_when_passing():
    audit = G.evaluate(depths_for(3000, n_bars=400), required_session=REQ)
    for f in REQUIRED_HEADER_FIELDS:
        assert f in audit.header, f
    assert audit.header["refusal_reason"] is None
    assert audit.header["median_bars"] == 400.0


def test_header_carries_every_required_field_when_refusing():
    audit = G.evaluate(depths_for(3000, n_bars=113), required_session=REQ)
    for f in REQUIRED_HEADER_FIELDS:
        assert f in audit.header, f
    assert audit.header["refusal_reason"] == audit.refusals[0].value


def test_header_reports_refresh_counts_as_none_when_no_refresh_has_run(tmp_path):
    """A missing refresh artifact must never be reported as a zero."""
    out = G.load_refresh_outcome(tmp_path)
    assert out["failed_refresh_count"] is None
    assert out["missing_ticker_count"] is None
    assert out["source"] == "unavailable_no_refresh_artifact"


def test_header_picks_up_refresh_counts_when_present(tmp_path):
    (tmp_path / "cache" / "research").mkdir(parents=True)
    (tmp_path / G.REFRESH_ARTIFACT_REL).write_text(json.dumps(
        {"failed_refresh_count": 4, "missing_ticker_count": 7,
         "history_drift_count": 0, "generated_at": "2026-09-06T00:00:00Z"}))
    out = G.load_refresh_outcome(tmp_path)
    assert (out["failed_refresh_count"], out["missing_ticker_count"]) == (4, 7)
    audit = G.evaluate(depths_for(3000, n_bars=400), required_session=REQ,
                       refresh=out)
    assert audit.header["failed_refresh_count"] == 4
    assert audit.header["missing_ticker_count"] == 7
    assert any("failed" in w for w in audit.warnings)


# ── 8. the gate the shadow report calls ─────────────────────────────────────


def test_require_m1_data_ready_raises_on_a_shallow_cache():
    audit = G.evaluate(depths_for(3000, n_bars=113), required_session=REQ)
    with pytest.raises(G.M1DataRefusal) as e:
        G.require_m1_data_ready(audit=audit)
    assert "SHALLOW_HISTORY" in str(e.value)


def test_require_m1_data_ready_returns_the_audit_when_deep():
    audit = G.evaluate(depths_for(3000, n_bars=400), required_session=REQ)
    assert G.require_m1_data_ready(audit=audit) is audit


def test_require_clean_rejects_degraded():
    good = depths_for(2900, n_bars=400)
    gappy = depths_for(100, n_bars=300, stride=4)
    audit = G.evaluate(good + gappy, required_session=REQ)
    assert audit.status is G.M1DataStatus.M1_DATA_DEGRADED
    assert G.require_m1_data_ready(audit=audit) is audit      # runnable by default
    with pytest.raises(G.M1DataRefusal):
        G.require_m1_data_ready(audit=audit, require_clean=True)


# ── 9. ladder isolation and no-ranking ──────────────────────────────────────


def test_status_ladder_is_disjoint_from_every_live_ladder():
    from research.backtests.common import (
        ConvictionVerdict, FingerprintVerdict, ReplayVerdict, TournamentVerdict,
    )

    mine = {s.value for s in G.M1DataStatus}
    for other in (ReplayVerdict, TournamentVerdict, ConvictionVerdict,
                  FingerprintVerdict):
        assert mine.isdisjoint({v.value for v in other})
    assert "VALIDATED_EDGE" not in mine


def test_validated_edge_cannot_be_emitted():
    with pytest.raises(ValueError):
        G.assert_m1_data_status("VALIDATED_EDGE")
    with pytest.raises(ValueError):
        G.assert_m1_data_status("PROMISING_RESEARCH_SOURCE_POOL")


def test_guard_never_emits_a_ranked_list_or_pool_members():
    """The memo forbids any ordering inside M1. Pin it structurally.

    Checked on the KEYS and the shape of the emitted values, not on prose:
    the guard is allowed to say the word "ordering" while explaining that it
    does not do it.
    """
    audit = G.evaluate(depths_for(3000, n_bars=400, trend=0.001),
                       required_session=REQ)
    keys = set(audit.header) | set(audit.detail)
    for banned in ("rank", "score", "shortlist", "conviction", "top",
                   "picks", "members", "pool_tickers", "mom_12_1"):
        assert not any(banned in k.lower() for k in keys), banned

    # the pool is a COUNT, never a collection
    assert isinstance(audit.header["m1_pool_size_if_run"], int)

    # every emitted list is a refusal diagnostic: capped, sorted, and empty on
    # a clean universe — never a candidate list
    for k in ("shallow_sample", "stale_sample", "contaminated_sample",
              "span_broken_sample"):
        v = audit.detail[k]
        assert isinstance(v, list) and len(v) <= 25
        assert v == sorted(v)
        assert v == []


def test_diagnostic_samples_never_grow_into_a_candidate_list():
    """Even with thousands of bad names, samples stay a capped diagnostic."""
    audit = G.evaluate(depths_for(3000, n_bars=113), required_session=REQ)
    assert len(audit.detail["shallow_sample"]) == 25
    assert audit.header["shallow_count"] == 3000


# ── 10. write containment and provider isolation ────────────────────────────


def test_guard_writes_only_into_its_own_namespace():
    for rel in (G.LATEST_REL, G.REPORT_TXT_REL):
        assert assert_replay_write_path(rel)


@pytest.mark.parametrize("forbidden", [
    "cache/prices/AAPL.parquet",
    "cache/prices_deep/AAPL.parquet",
    "cache/research/research_scanner_latest.json",
    "cache/research/high_conviction_alpha_latest.json",
    "cache/research/alpha_focus_latest.json",
    "data/research/journal.jsonl",
    "db/trading.db",
])
def test_live_artifact_writes_are_structurally_impossible(forbidden):
    with pytest.raises(ReplayWriteViolation):
        assert_replay_write_path(forbidden)


def test_audit_stage_makes_no_provider_call_and_touches_no_live_artifact(
        tmp_path, monkeypatch):
    """End-to-end on a sandbox root: any socket use fails the test."""
    import socket

    def _no_network(*a, **k):  # pragma: no cover - must never run
        raise AssertionError("the guard attempted a network call")

    monkeypatch.setattr(socket.socket, "connect", _no_network)
    monkeypatch.setattr(socket.socket, "connect_ex", _no_network)

    prices = tmp_path / G.PRICES_REL
    prices.mkdir(parents=True)
    for i in range(20):
        make_frame(400).to_parquet(prices / f"S{i:03d}.parquet")

    args = G.build_parser().parse_args(["audit", "--root", str(tmp_path)])
    rc = G.audit_stage(args)

    assert (tmp_path / G.LATEST_REL).exists()
    assert (tmp_path / G.REPORT_TXT_REL).exists()
    payload = json.loads((tmp_path / G.LATEST_REL).read_text())
    assert payload["provider_calls"] == 0
    assert payload["emits_ranking"] is False
    assert payload["research_only"] is True
    # 20 names cannot fill a pool, so this sandbox is correctly refused
    assert rc == 1
    assert payload["status"] == "M1_DATA_BLOCKED"
    assert not (tmp_path / "cache" / "prices").exists()
    assert not (tmp_path / "data").exists()


def test_guard_imports_the_m1_definition_rather_than_restating_it():
    """A guard that re-derived M1 could pass on a pool that is not M1."""
    import research.backtests.alpha_reconstruction_lab as LAB
    import research.backtests.alpha_tournament as T

    assert G._lag_ratio is LAB._lag_ratio
    assert G._series_integrity is LAB._series_integrity
    assert G.BASE_FLOOR is T.BASE_FLOOR
    assert G.SPLIT_ARTIFACT_MAX_1D_RETURN is LAB.SPLIT_ARTIFACT_MAX_1D_RETURN


# ── 11. production isolation ────────────────────────────────────────────────

PRODUCTION_ROOTS = ("core", "council", "execution", "strategies", "dashboards")
NEW_MODULES = ("m1_data_guard", "m1_price_refresh")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_no_production_module_imports_the_m1_guard_or_refresh():
    """Hard separation rule: production must never import from research/."""
    root = _repo_root()
    offenders = []
    for pkg in PRODUCTION_ROOTS:
        for f in (root / pkg).rglob("*.py"):
            text = f.read_text(errors="ignore")
            for mod in NEW_MODULES:
                if mod in text:
                    offenders.append(f"{f.relative_to(root)}:{mod}")
    assert offenders == [], offenders


def test_the_live_research_scanner_is_untouched_by_this_work():
    """The scanner's ranking axis and score formula must be byte-identical.

    The M1 memo recommends sunsetting `combined_rs` as an ordering — that is a
    separate, reviewed decision. This work must not have started it early.
    """
    src = (_repo_root() / "research" / "research_scanner.py").read_text()
    assert "combined_rs = rs_63 * 0.6 + rs_20 * 0.4" in src
    assert "score = min(100.0, max(0.0, 50.0 + combined_rs * 0.8))" in src
    for mod in NEW_MODULES:
        assert mod not in src


def test_guard_and_refresh_are_not_wired_into_any_runner_or_timer():
    """Nothing here runs on a schedule until it is reviewed and wired."""
    root = _repo_root()
    for rel in ("scripts/run_research_cycle.sh",):
        p = root / rel
        if not p.exists():
            continue
        text = p.read_text(errors="ignore")
        for mod in NEW_MODULES:
            assert mod not in text, f"{rel} references {mod}"


def test_guard_import_does_not_pull_in_the_production_scanner():
    """Importing the gate must not drag the live scanner into the process."""
    import subprocess
    import sys

    code = (
        "import sys; import research.backtests.m1_data_guard;"
        "print('research.research_scanner' in sys.modules)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=str(_repo_root()),
        env={"GEM_TRADER_SKIP_DOTENV": "true", "PATH": "/usr/bin:/bin",
             "HOME": "/tmp"},
        capture_output=True, text=True,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "False"


def test_guard_never_writes_a_db_row():
    """Read-only by construction: no sqlite write verb anywhere in the module."""
    src = (_repo_root() / "research" / "backtests" / "m1_data_guard.py").read_text()
    for verb in ("INSERT", "UPDATE ", "DELETE", "DROP", "CREATE TABLE",
                 "commit()", "DecisionLogger"):
        assert verb not in src, verb

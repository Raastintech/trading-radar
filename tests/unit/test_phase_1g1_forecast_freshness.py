"""
tests/unit/test_phase_1g1_forecast_freshness.py — Phase 1G.1 forecast
freshness-guard tests.

The audit on 2026-05-18 found a Monday-morning forecast that anchored on
Friday's close and was tagged ``data_freshness_status=behind``. That
verdict came from a raw calendar-age check (>= 2 days) — which on any
Monday before close trivially fires. The new logic compares the anchor
to the most recent *completed* NYSE trading day; on a normal Monday
premarket, Friday is the expected anchor, so the state should be
``FRAGILE_STALE`` (informational), not ``STALE``.

Tests pin the four canonical situations.
"""
from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import importlib  # noqa: E402

_ET = ZoneInfo("America/New_York")


def _import_rf():
    return importlib.import_module("research.regime_forecast")


class TestExpectedAnchorDate:
    def test_trading_day_post_close_today_is_anchor(self):
        rf = _import_rf()
        now = datetime(2026, 5, 18, 17, 0, tzinfo=_ET)  # Mon 5pm ET, after close
        expected, basis = rf._expected_anchor_date(now)
        assert expected == date(2026, 5, 18)
        assert basis == "post_close_today"

    def test_trading_day_premarket_uses_prior_close(self):
        rf = _import_rf()
        now = datetime(2026, 5, 18, 8, 17, tzinfo=_ET)  # Mon 8:17am ET
        expected, basis = rf._expected_anchor_date(now)
        # Friday 2026-05-15 is the previous trading day.
        assert expected == date(2026, 5, 15)
        assert basis == "intraday_prior_close"

    def test_weekend_uses_friday(self):
        rf = _import_rf()
        now = datetime(2026, 5, 16, 12, 0, tzinfo=_ET)  # Saturday noon
        expected, basis = rf._expected_anchor_date(now)
        assert expected == date(2026, 5, 15)
        assert basis == "non_trading_day_prior_close"

    def test_holiday_uses_prior_trading_day(self):
        # Memorial Day 2026 = 2026-05-25 (Monday).
        rf = _import_rf()
        now = datetime(2026, 5, 25, 12, 0, tzinfo=_ET)
        expected, basis = rf._expected_anchor_date(now)
        # Prior trading day is Friday 2026-05-22.
        assert expected == date(2026, 5, 22)
        assert basis == "non_trading_day_prior_close"


class TestClassifyFreshness:
    def test_match_is_fresh(self):
        rf = _import_rf()
        status, state, warn = rf._classify_freshness(
            anchor=date(2026, 5, 18),
            expected=date(2026, 5, 18),
            basis="post_close_today",
        )
        assert status == "fresh" and state == "FRESH"
        assert warn is None

    def test_monday_premarket_friday_anchor_is_fresh(self):
        rf = _import_rf()
        status, state, warn = rf._classify_freshness(
            anchor=date(2026, 5, 15),
            expected=date(2026, 5, 15),
            basis="intraday_prior_close",
        )
        # Anchor == expected (Friday) → FRESH on a Monday premarket.
        assert status == "fresh" and state == "FRESH"

    def test_post_close_with_prior_close_anchor_is_fragile_stale(self):
        rf = _import_rf()
        status, state, warn = rf._classify_freshness(
            anchor=date(2026, 5, 15),                # Friday
            expected=date(2026, 5, 18),              # Monday (today's close done)
            basis="post_close_today",
        )
        # Today's close exists in the world but cache still has Friday →
        # one-trading-day lag, after close → FRAGILE_STALE.
        assert status == "behind" and state == "FRAGILE_STALE"
        assert warn is not None and "not yet ingested" in warn

    def test_intraday_with_prior_close_missing_is_stale(self):
        rf = _import_rf()
        # Tuesday 2026-05-19 premarket; expected anchor = Mon 2026-05-18;
        # anchor = Fri 2026-05-15 → STALE: we expected Monday's close
        # (already in the world by Tue 8am) and don't have it.
        status, state, warn = rf._classify_freshness(
            anchor=date(2026, 5, 15),
            expected=date(2026, 5, 18),
            basis="intraday_prior_close",
        )
        assert status == "stale" and state == "STALE"

    def test_intraday_with_prior_trading_day_anchor_is_fresh(self):
        rf = _import_rf()
        # Tuesday 2026-05-19 premarket; expected = Mon 2026-05-18,
        # anchor = Mon 2026-05-18 → FRESH (we have what we expect).
        status, state, warn = rf._classify_freshness(
            anchor=date(2026, 5, 18),
            expected=date(2026, 5, 18),
            basis="intraday_prior_close",
        )
        assert status == "fresh" and state == "FRESH"

    def test_post_close_with_two_day_gap_is_stale(self):
        rf = _import_rf()
        # Monday after close; expected = Monday; anchor = previous
        # Thursday (2026-05-14) — both Fri 5/15 and Mon 5/18 missing → STALE.
        status, state, warn = rf._classify_freshness(
            anchor=date(2026, 5, 14),
            expected=date(2026, 5, 18),
            basis="post_close_today",
        )
        assert status == "stale" and state == "STALE"

    def test_missing_anchor(self):
        rf = _import_rf()
        status, state, warn = rf._classify_freshness(
            anchor=None,
            expected=date(2026, 5, 18),
            basis="post_close_today",
        )
        assert status == "missing" and state == "MISSING"
        assert warn is not None


class TestAnchorMetadataIntegration:
    """Stale cache should not look fresh just because file mtime is new.
    The metadata function only looks at frame contents, so this is
    enforced by construction."""

    def test_stale_data_with_recent_call_time(self):
        import pandas as pd
        rf = _import_rf()
        # Build a SPY frame whose last bar is 8 trading days old.
        idx = pd.to_datetime([
            "2026-05-04", "2026-05-05", "2026-05-06", "2026-05-07", "2026-05-08",
        ])
        spy = pd.DataFrame({"close": [700, 702, 704, 706, 708]}, index=idx)
        frames = {"SPY": spy}
        # Pretend it's Monday 2026-05-18 after close → expected anchor 2026-05-18.
        now = datetime(2026, 5, 18, 17, 0, tzinfo=_ET)
        meta = rf._anchor_metadata(frames, now_et=now)
        assert meta["data_freshness_status"] == "stale"
        assert meta["forecast_state"] == "STALE"
        assert meta["anchor_date"] == "2026-05-08"
        assert meta["expected_anchor_date"] == "2026-05-18"

    def test_friday_anchor_on_monday_premarket_is_fresh(self):
        import pandas as pd
        rf = _import_rf()
        idx = pd.to_datetime(["2026-05-15"])
        spy = pd.DataFrame({"close": [739.17]}, index=idx)
        frames = {"SPY": spy}
        now = datetime(2026, 5, 18, 8, 17, tzinfo=_ET)
        meta = rf._anchor_metadata(frames, now_et=now)
        # Expected = Friday (prior trading day, intraday basis); anchor = Friday → FRESH.
        assert meta["data_freshness_status"] == "fresh"
        assert meta["forecast_state"] == "FRESH"
        assert meta["expected_anchor_basis"] == "intraday_prior_close"

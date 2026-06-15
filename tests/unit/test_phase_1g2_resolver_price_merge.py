"""
tests/unit/test_phase_1g2_resolver_price_merge.py — Phase 1G.2 T4

Tests for the merge fix to ``core.forecast_forward_tracker._load_price_frame``.

Root cause this exercises: ``cache/research/regime_validation_prices/``
froze at 2026-04-24 while ``cache/prices/`` carries the fresh tail. The
old first-match behaviour read the stale long-history parquet and
never saw forward bars. The fix merges both, keeping the fresher row
on duplicate indices.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

pd = pytest.importorskip("pandas")

from core import forecast_forward_tracker as fft


def _write_parquet(dirpath: Path, symbol: str, dates: list[date], close: list[float]) -> None:
    dirpath.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(
        {
            "open": close,
            "high": [c * 1.01 for c in close],
            "low": [c * 0.99 for c in close],
            "close": close,
            "volume": [1_000_000] * len(close),
        },
        index=pd.to_datetime(dates),
    )
    df.to_parquet(dirpath / f"{symbol}.parquet")


def test_merge_picks_fresh_tail_from_second_dir(tmp_path: Path):
    long_history = tmp_path / "regime_validation_prices"
    fresh_tail = tmp_path / "prices"
    # long_history: 2026-04-20 to 2026-04-24
    long_dates = [date(2026, 4, 20), date(2026, 4, 21), date(2026, 4, 22),
                  date(2026, 4, 23), date(2026, 4, 24)]
    _write_parquet(long_history, "SPY", long_dates, [700, 705, 707, 708, 710])
    # fresh_tail: 2026-04-23 to 2026-05-01 — overlaps at 23/24
    fresh_dates = [date(2026, 4, 23), date(2026, 4, 24), date(2026, 4, 25),
                   date(2026, 4, 28), date(2026, 4, 29), date(2026, 4, 30),
                   date(2026, 5, 1)]
    _write_parquet(fresh_tail, "SPY", fresh_dates, [708.5, 710.5, 715, 720, 722, 724, 726])

    df = fft._load_price_frame("SPY", price_dirs=(long_history, fresh_tail))
    assert df is not None
    # Should contain full history (oldest from long_history)
    assert df.index[0] == pd.Timestamp("2026-04-20")
    # Tail should be the fresh 2026-05-01
    assert df.index[-1] == pd.Timestamp("2026-05-01")
    # Overlap rows must be the fresher values (later dir wins on duplicates)
    assert df.loc["2026-04-23", "close"] == 708.5
    assert df.loc["2026-04-24", "close"] == 710.5


def test_forward_return_resolves_after_merge(tmp_path: Path):
    long_history = tmp_path / "regime_validation_prices"
    fresh_tail = tmp_path / "prices"
    # Long history with anchor available
    anchor = date(2026, 4, 24)
    long_dates = [anchor - timedelta(days=4), anchor - timedelta(days=3),
                  anchor - timedelta(days=2), anchor - timedelta(days=1), anchor]
    _write_parquet(long_history, "SPY", long_dates, [700, 702, 705, 707, 710])
    # Fresh tail provides forward bars after the anchor
    fresh_dates = [anchor + timedelta(days=1), anchor + timedelta(days=4),
                   anchor + timedelta(days=5), anchor + timedelta(days=6),
                   anchor + timedelta(days=7), anchor + timedelta(days=8)]
    _write_parquet(fresh_tail, "SPY", fresh_dates,
                   [712, 720, 722, 724, 726, 728])

    df = fft._load_price_frame("SPY", price_dirs=(long_history, fresh_tail))
    r5 = fft._forward_return_pct(df, anchor, 5)
    assert r5 is not None
    # 5 trading days after anchor → close=726 vs base=710 → ~+2.25%
    assert r5 == pytest.approx(((726 / 710) - 1.0) * 100.0, abs=0.01)


def test_load_returns_none_when_no_parquets(tmp_path: Path):
    df = fft._load_price_frame("ABSENT", price_dirs=(tmp_path / "a", tmp_path / "b"))
    assert df is None


def test_single_dir_path_still_works(tmp_path: Path):
    dirpath = tmp_path / "only_dir"
    dates = [date(2026, 5, 1), date(2026, 5, 2), date(2026, 5, 3)]
    _write_parquet(dirpath, "SPY", dates, [700, 705, 710])
    df = fft._load_price_frame("SPY", price_dirs=(dirpath,))
    assert df is not None
    assert len(df) == 3
    assert df.index[-1] == pd.Timestamp("2026-05-03")

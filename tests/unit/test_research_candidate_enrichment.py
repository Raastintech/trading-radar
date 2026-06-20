"""
tests/unit/test_research_candidate_enrichment.py

Tests for research_candidate_enrichment.py — specifically the date-merge fix
that combines deep (historical) and shallow (recent) price caches so that RS
and momentum calculations use the most recent data available.
"""
from __future__ import annotations

import sys
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

os.environ.setdefault("ALPACA_API_KEY", "offline")
os.environ.setdefault("ALPACA_SECRET_KEY", "offline")
os.environ.setdefault("FMP_API_KEY", "offline")
os.environ.setdefault("ALPACA_PAPER", "true")
os.environ.setdefault("PAPER_TRADING", "true")
os.environ.setdefault("DB_PATH", str(REPO / "db" / "trading.db"))
os.environ.setdefault("CACHE_DIR", str(REPO / "cache"))
os.environ.setdefault("LOG_DIR", str(REPO / "logs"))
os.environ.setdefault("FMP_BASE_URL", "https://financialmodelingprep.com/stable")

from research.research_candidate_enrichment import (
    _load_frame,
    _merge_frames,
    _last_bar_date,
    _closes,
    _volumes,
    enrich_research_candidate,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_parquet(tmp: Path, ticker: str, start: str, bars: int,
                  base_price: float = 100.0, drift: float = 0.001) -> Path:
    """Write a synthetic daily price parquet starting from `start`."""
    idx = pd.date_range(start, periods=bars, freq="B")
    prices = base_price * (1 + drift) ** np.arange(bars)
    df = pd.DataFrame({
        "open": prices * 0.99,
        "high": prices * 1.02,
        "low": prices * 0.98,
        "close": prices,
        "volume": np.full(bars, 1_000_000.0),
    }, index=idx)
    path = tmp / f"{ticker}.parquet"
    df.to_parquet(path)
    return path


# ── _merge_frames ─────────────────────────────────────────────────────────────


def test_merge_frames_both_none():
    assert _merge_frames(None, None) is None


def test_merge_frames_only_deep(tmp_path):
    deep_dir = tmp_path / "deep"
    deep_dir.mkdir()
    _make_parquet(deep_dir, "A", "2025-01-01", 300)
    df_deep = _load_frame("A", deep_dir)
    result = _merge_frames(df_deep, None)
    assert len(result) == len(df_deep)


def test_merge_frames_only_shallow(tmp_path):
    shallow_dir = tmp_path / "shallow"
    shallow_dir.mkdir()
    _make_parquet(shallow_dir, "A", "2026-01-01", 100)
    df_reg = _load_frame("A", shallow_dir)
    result = _merge_frames(None, df_reg)
    assert len(result) == len(df_reg)


def test_merge_frames_combines_non_overlapping(tmp_path):
    """Deep ends before shallow starts — merged result should have combined bars."""
    deep_dir = tmp_path / "deep"
    shallow_dir = tmp_path / "shallow"
    deep_dir.mkdir()
    shallow_dir.mkdir()

    _make_parquet(deep_dir, "X", "2025-01-01", 200)   # ends ~Sep 2025
    _make_parquet(shallow_dir, "X", "2025-10-01", 80)  # starts Oct 2025

    df_deep = _load_frame("X", deep_dir)
    df_reg = _load_frame("X", shallow_dir)
    result = _merge_frames(df_deep, df_reg)

    # Should have nearly 280 bars (200 + 80 with no overlap)
    assert len(result) > max(len(df_deep), len(df_reg))
    # Last bar should be from shallow (more recent)
    assert result.index[-1] > df_deep.index[-1]


def test_merge_frames_shallow_wins_on_overlap(tmp_path):
    """When deep and shallow overlap, shallow's prices take precedence."""
    deep_dir = tmp_path / "deep"
    shallow_dir = tmp_path / "shallow"
    deep_dir.mkdir()
    shallow_dir.mkdir()

    # Both cover same period but with different prices
    idx = pd.bdate_range("2026-01-01", periods=50)
    df_deep = pd.DataFrame({"close": [50.0] * 50, "volume": [1e6] * 50}, index=idx)
    df_reg = pd.DataFrame({"close": [99.0] * 50, "volume": [2e6] * 50}, index=idx)

    deep_path = deep_dir / "T.parquet"
    shallow_path = shallow_dir / "T.parquet"
    df_deep.to_parquet(deep_path)
    df_reg.to_parquet(shallow_path)

    df_d = _load_frame("T", deep_dir)
    df_s = _load_frame("T", shallow_dir)
    result = _merge_frames(df_d, df_s)

    closes = _closes(result)
    assert all(c == 99.0 for c in closes), "Shallow prices should override deep on overlap"


def test_merge_frames_longer_result_ends_at_shallow_date(tmp_path):
    """
    Core regression: deep cache (343 bars ending May 26) merged with shallow
    (113 bars ending June 12) should produce a result ending June 12, not May 26.
    """
    deep_dir = tmp_path / "deep"
    shallow_dir = tmp_path / "shallow"
    deep_dir.mkdir()
    shallow_dir.mkdir()

    # Deep: long history ending "earlier"
    _make_parquet(deep_dir, "Z", "2024-06-01", 343)   # ends ~May 2025 (synthetic)
    # Shallow: recent, shorter, but ends LATER
    deep_df = _load_frame("Z", deep_dir)
    last_deep = deep_df.index[-1]
    # Shallow starts a few bars before deep ends (overlap) and extends further
    shallow_start = last_deep - pd.Timedelta(days=20)
    _make_parquet(shallow_dir, "Z", str(shallow_start.date()), 100)

    df_d = _load_frame("Z", deep_dir)
    df_s = _load_frame("Z", shallow_dir)
    result = _merge_frames(df_d, df_s)

    assert result.index[-1] > last_deep, (
        f"Merged result should end AFTER deep cache's last date {last_deep.date()}; "
        f"got {result.index[-1].date()}"
    )


# ── _last_bar_date ─────────────────────────────────────────────────────────────


def test_last_bar_date_returns_iso_string(tmp_path):
    path = tmp_path / "deep"
    path.mkdir()
    _make_parquet(path, "A", "2026-01-01", 50)
    df = _load_frame("A", path)
    date_str = _last_bar_date(df)
    assert date_str is not None
    assert "2026" in date_str


def test_last_bar_date_none_for_none():
    assert _last_bar_date(None) is None


# ── enrich_research_candidate: merge integration ──────────────────────────────


def test_enrich_uses_merged_series_last_date(tmp_path):
    """
    When deep cache ends earlier than shallow, enrich should report the
    shallow's last date (after merge) not the deep cache's last date.
    """
    deep_dir = tmp_path / "deep"
    shallow_dir = tmp_path / "shallow"
    deep_dir.mkdir()
    shallow_dir.mkdir()

    # Deep: 300 bars
    _make_parquet(deep_dir, "T", "2025-01-01", 300)
    df_deep = _load_frame("T", deep_dir)
    deep_last = df_deep.index[-1]

    # Shallow: 100 bars starting 30 bars before deep ends → extends past deep
    shallow_start = deep_last - pd.Timedelta(days=45)
    _make_parquet(shallow_dir, "T", str(shallow_start.date()), 100)
    df_shallow = _load_frame("T", shallow_dir)
    shallow_last = df_shallow.index[-1]

    assert shallow_last > deep_last, "Shallow should end later than deep"

    # Build spy closes (must be long enough for RS)
    spy_closes = [100.0 * (1.001 ** i) for i in range(400)]

    item = {"ticker": "T", "category": "test"}
    result = enrich_research_candidate("T", item, shallow_dir, spy_closes, deep_price_dir=deep_dir)

    last_date = result.get("latest_price_date", "")
    assert str(shallow_last.date()) in str(last_date), (
        f"latest_price_date should reflect shallow cache's last date {shallow_last.date()}, "
        f"got {last_date}"
    )


def test_enrich_merged_bars_exceed_deep_alone(tmp_path):
    """Merged result should have more bars than either cache alone when non-overlapping."""
    deep_dir = tmp_path / "deep"
    shallow_dir = tmp_path / "shallow"
    deep_dir.mkdir()
    shallow_dir.mkdir()

    _make_parquet(deep_dir, "M", "2024-01-01", 250)
    df_deep = _load_frame("M", deep_dir)
    deep_last = df_deep.index[-1]

    # Shallow starts just after deep ends
    shallow_start = deep_last + pd.Timedelta(days=3)
    _make_parquet(shallow_dir, "M", str(shallow_start.date()), 80)

    spy_closes = [100.0 * (1.001 ** i) for i in range(450)]
    item = {"ticker": "M", "category": "test"}
    result = enrich_research_candidate("M", item, shallow_dir, spy_closes, deep_price_dir=deep_dir)

    assert result.get("bars_available", 0) > 250, (
        "Merged result should have more bars than deep cache alone (250)"
    )


def test_enrich_rs_uses_recent_data_not_stale_deep(tmp_path):
    """
    RS should be computed using data that ends at the RECENT close, not at the
    stale deep-cache end date. We verify that merging gives a different (correct)
    RS than using deep-only.
    """
    deep_dir = tmp_path / "deep"
    shallow_dir = tmp_path / "shallow"
    deep_dir.mkdir()
    shallow_dir.mkdir()

    # Deep: flat at 100 for 200 bars
    _make_parquet(deep_dir, "RS", "2025-01-01", 200, base_price=100.0, drift=0.0)

    # Shallow: starts overlapping deep and rises sharply (simulating recent outperformance)
    df_deep = _load_frame("RS", deep_dir)
    overlap_start = df_deep.index[-30]
    _make_parquet(
        shallow_dir, "RS", str(overlap_start.date()), 80,
        base_price=100.0, drift=0.01,  # big positive drift in shallow
    )

    # SPY: flat
    spy_closes = [100.0] * 400

    item = {"ticker": "RS", "category": "test"}
    # Old behavior (only deep, which is flat): RS ≈ 0
    # New behavior (merged with rising shallow): RS > 0
    result = enrich_research_candidate("RS", item, shallow_dir, spy_closes, deep_price_dir=deep_dir)
    rs_63 = result.get("rs_63d_vs_spy")

    assert rs_63 is not None, "RS should be computable with merged series"
    assert rs_63 > 0, (
        f"Merged series includes rising shallow bars → RS should be positive, got {rs_63}"
    )

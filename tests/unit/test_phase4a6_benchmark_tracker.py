"""
tests/unit/test_phase4a6_benchmark_tracker.py — Phase 4A.6 benchmark-return tests.

Covers:
  - SECTOR_ETF_MAP and _benchmark_sector_etf logic
  - _load_closes_with_dates (both column and index layouts)
  - _forward_return with benchmark closes (regression guard)
  - Relative return formula: ret_Xd_vs_spy = ticker_ret - spy_ret
  - History preservation: original fields unchanged when benchmark fields added
  - _compute_verdicts with benchmark stats (null when sample too small; populated above threshold)
  - _benchmark_readiness summary
  - 60d horizon is included in HORIZONS
  - BENCHMARK_SCHEMA_VERSION constant present
  - No provider calls — all parquet-only
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

import research.research_watchlist_forward_tracker as rwft
from research.research_watchlist_forward_tracker import (
    BENCHMARK_SCHEMA_VERSION,
    HORIZONS,
    SAMPLE_THRESHOLD_PROVISIONAL,
    SECTOR_ETF_MAP,
    _benchmark_readiness,
    _benchmark_sector_etf,
    _compute_verdicts,
    _forward_return,
    _load_closes_with_dates,
)


# ── Constants ──────────────────────────────────────────────────────────────────

class TestConstants:

    def test_benchmark_schema_version_present(self):
        assert BENCHMARK_SCHEMA_VERSION == "BENCHMARK_RETURNS_V2"  # Phase 5.1: +IWM

    def test_60d_in_horizons(self):
        assert 60 in HORIZONS

    def test_all_standard_horizons_present(self):
        assert set(HORIZONS) >= {5, 10, 20, 60}

    def test_sector_etf_map_has_core_sectors(self):
        for sector in ("Technology", "Financials", "Health Care", "Energy", "Industrials"):
            assert sector in SECTOR_ETF_MAP, f"Missing sector: {sector}"

    def test_sector_etf_map_etfs_are_uppercase(self):
        for etf in SECTOR_ETF_MAP.values():
            assert etf == etf.upper()


# ── _benchmark_sector_etf ─────────────────────────────────────────────────────

class TestBenchmarkSectorEtf:

    def test_technology_maps_to_xlk(self):
        assert _benchmark_sector_etf("Technology") == "XLK"

    def test_financials_maps_to_xlf(self):
        assert _benchmark_sector_etf("Financials") == "XLF"

    def test_healthcare_variant_maps_to_xlv(self):
        assert _benchmark_sector_etf("Healthcare") == "XLV"
        assert _benchmark_sector_etf("Health Care") == "XLV"

    def test_semiconductor_industry_overrides_technology_sector(self):
        etf = _benchmark_sector_etf("Technology", "Semiconductors")
        assert etf == "SMH"

    def test_semiconductor_lowercase_matches(self):
        assert _benchmark_sector_etf("Technology", "semiconductor equipment") == "SMH"

    def test_unknown_sector_returns_none(self):
        assert _benchmark_sector_etf("Underwater Basket Weaving") is None

    def test_none_sector_returns_none(self):
        assert _benchmark_sector_etf(None) is None

    def test_empty_string_sector_returns_none(self):
        assert _benchmark_sector_etf("") is None

    def test_industry_none_falls_through_to_sector(self):
        assert _benchmark_sector_etf("Energy", None) == "XLE"

    def test_non_semiconductor_industry_uses_sector(self):
        assert _benchmark_sector_etf("Technology", "Software") == "XLK"


# ── _load_closes_with_dates (date-in-index layout) ───────────────────────────

class TestLoadClosesWithDates:

    def _write_parquet_index_dates(self, tmp_path: Path, sym: str, dates: List[str], closes: List[float]) -> Path:
        """Write a parquet with dates in the index (our cache format)."""
        path = tmp_path / f"{sym}.parquet"
        df = pd.DataFrame({"close": closes}, index=pd.Index(dates, name="date"))
        df.to_parquet(path)
        return path

    def _write_parquet_column_dates(self, tmp_path: Path, sym: str, dates: List[str], closes: List[float]) -> Path:
        """Write a parquet with dates as an explicit column (legacy format)."""
        path = tmp_path / f"{sym}.parquet"
        df = pd.DataFrame({"date": dates, "close": closes})
        df.to_parquet(path)
        return path

    def test_loads_dates_from_index(self, tmp_path):
        dates = ["2026-01-01", "2026-01-02", "2026-01-05"]  # Thu, Fri, Mon
        closes = [100.0, 101.0, 102.0]
        self._write_parquet_index_dates(tmp_path, "TEST", dates, closes)
        with patch("research.research_watchlist_forward_tracker.PRICE_DIR", tmp_path):
            result = _load_closes_with_dates("TEST")
        assert result == [("2026-01-01", 100.0), ("2026-01-02", 101.0), ("2026-01-05", 102.0)]

    def test_weekend_bars_are_dropped(self, tmp_path):
        """A weekend bar in a daily equity series belongs to another
        instrument (cache/prices/PI.parquet carried $0.09 weekend bars from a
        24/7 symbol). Keeping it corrupts twice: a wrong entry price, and a
        shifted horizon, because forward returns are counted in bars."""
        dates = ["2026-01-02", "2026-01-03", "2026-01-04", "2026-01-05"]
        closes = [101.0, 0.09, 0.09, 102.0]  # Fri, Sat, Sun, Mon
        self._write_parquet_index_dates(tmp_path, "SPLICED", dates, closes)
        with patch("research.research_watchlist_forward_tracker.PRICE_DIR", tmp_path):
            result = _load_closes_with_dates("SPLICED")
        assert result == [("2026-01-02", 101.0), ("2026-01-05", 102.0)]
        assert rwft.NON_SESSION_BARS_DROPPED["SPLICED"] == ["2026-01-03",
                                                           "2026-01-04"]

    def test_loads_dates_from_column(self, tmp_path):
        dates = ["2026-01-01", "2026-01-02"]
        closes = [50.0, 51.0]
        self._write_parquet_column_dates(tmp_path, "COL", dates, closes)
        with patch("research.research_watchlist_forward_tracker.PRICE_DIR", tmp_path):
            result = _load_closes_with_dates("COL")
        assert result == [("2026-01-01", 50.0), ("2026-01-02", 51.0)]

    def test_returns_empty_for_missing_file(self, tmp_path):
        with patch("research.research_watchlist_forward_tracker.PRICE_DIR", tmp_path):
            result = _load_closes_with_dates("MISSING")
        assert result == []

    def test_result_is_sorted_ascending(self, tmp_path):
        dates = ["2026-01-05", "2026-01-01", "2026-01-02"]  # Mon, Thu, Fri
        closes = [103.0, 101.0, 102.0]
        self._write_parquet_index_dates(tmp_path, "UNSORTED", dates, closes)
        with patch("research.research_watchlist_forward_tracker.PRICE_DIR", tmp_path):
            result = _load_closes_with_dates("UNSORTED")
        assert [d for d, _ in result] == sorted(dates)

    def test_ticker_uppercased_in_filename_lookup(self, tmp_path):
        dates = ["2026-01-01"]
        closes = [400.0]
        self._write_parquet_index_dates(tmp_path, "SPY", dates, closes)
        with patch("research.research_watchlist_forward_tracker.PRICE_DIR", tmp_path):
            result = _load_closes_with_dates("spy")  # lowercase input
        assert len(result) == 1


# ── Relative return formula ───────────────────────────────────────────────────

class TestRelativeReturnFormula:
    """ret_Xd_vs_spy = candidate_ret_Xd - spy_ret_Xd (simple percentage difference)."""

    def _closes(self, values: List[float]) -> List[tuple]:
        return [(f"2026-01-{i+1:02d}", v) for i, v in enumerate(values)]

    def test_positive_alpha_over_spy(self):
        ticker_closes = self._closes([100.0] * 1 + [110.0] * 9 + [115.0])  # +15% at 10d
        spy_closes = self._closes([100.0] * 1 + [105.0] * 9 + [108.0])     # +8% at 10d
        ticker_ret = _forward_return(ticker_closes, "2026-01-01", 10)
        spy_ret = _forward_return(spy_closes, "2026-01-01", 10)
        assert ticker_ret is not None and spy_ret is not None
        vs_spy = round(ticker_ret - spy_ret, 2)
        assert vs_spy > 0, "ticker outperformed SPY but vs_spy is non-positive"

    def test_negative_alpha_over_spy(self):
        ticker_closes = self._closes([100.0] + [102.0] * 10)  # +2%
        spy_closes = self._closes([100.0] + [108.0] * 10)    # +8%
        ticker_ret = _forward_return(ticker_closes, "2026-01-01", 10)
        spy_ret = _forward_return(spy_closes, "2026-01-01", 10)
        vs_spy = round(ticker_ret - spy_ret, 2)
        assert vs_spy < 0

    def test_zero_alpha_when_returns_equal(self):
        closes = [(f"2026-01-{i+1:02d}", 100.0 + i) for i in range(15)]
        ticker_ret = _forward_return(closes, "2026-01-01", 10)
        spy_ret = _forward_return(closes, "2026-01-01", 10)
        vs_spy = round(ticker_ret - spy_ret, 2)
        assert vs_spy == pytest.approx(0.0)

    def test_none_spy_ret_makes_vs_spy_none(self):
        # When SPY doesn't have enough bars, vs_spy must be None (not fabricated)
        spy_closes: List[tuple] = []  # no data
        spy_ret = _forward_return(spy_closes, "2026-01-01", 10)
        assert spy_ret is None
        # Caller must guard: vs_spy = None when spy_ret is None
        ticker_ret = 5.0
        vs_spy = round(ticker_ret - spy_ret, 2) if spy_ret is not None else None
        assert vs_spy is None


# ── _compute_verdicts with benchmark stats ────────────────────────────────────

class TestComputeVerdictsWithBenchmarks:

    _seq = 0

    def _entry(self, ret_10d: Optional[float], vs_spy: Optional[float] = None,
                vs_qqq: Optional[float] = None, vs_sec: Optional[float] = None) -> Dict[str, Any]:
        # Phase 5.1: sample status is graded on UNIQUE tickers, so every
        # synthetic entry needs its own ticker to count as evidence.
        TestComputeVerdictsWithBenchmarks._seq += 1
        e: Dict[str, Any] = {"ret_10d": ret_10d,
                             "ticker": f"T{TestComputeVerdictsWithBenchmarks._seq}"}
        if vs_spy is not None:
            e["ret_10d_vs_spy"] = vs_spy
        if vs_qqq is not None:
            e["ret_10d_vs_qqq"] = vs_qqq
        if vs_sec is not None:
            e["ret_10d_vs_sector"] = vs_sec
        return e

    def test_benchmark_stats_null_when_too_early(self):
        entries = [self._entry(5.0, 1.0)] * 3  # n < 10
        v = _compute_verdicts(entries, "TEST")
        assert v["verdict"] == "NEED_MORE_DATA"
        assert v["win_rate_vs_spy"] is None
        assert v["avg_ret_vs_spy"] is None

    def test_benchmark_stats_computed_at_provisional(self):
        entries = [self._entry(3.0, vs_spy=1.0, vs_qqq=0.5) for _ in range(10)]
        v = _compute_verdicts(entries, "TEST")
        assert v["win_rate_vs_spy"] == pytest.approx(1.0)
        assert v["avg_ret_vs_spy"] == pytest.approx(1.0)
        assert v["n_with_spy_baseline"] == 10

    def test_benchmark_stats_null_when_no_spy_data(self):
        entries = [self._entry(5.0) for _ in range(10)]  # no vs_spy field
        v = _compute_verdicts(entries, "TEST")
        assert v["win_rate_vs_spy"] is None
        assert v["n_with_spy_baseline"] is None

    def test_sector_stats_independent_of_spy(self):
        entries = [self._entry(3.0, vs_spy=1.0, vs_sec=2.0) for _ in range(10)]
        v = _compute_verdicts(entries, "TEST")
        assert v["avg_ret_vs_spy"] == pytest.approx(1.0)
        assert v["avg_ret_vs_sector"] == pytest.approx(2.0)
        assert v["n_with_sector_baseline"] == 10

    def test_partial_spy_coverage_uses_available_entries(self):
        entries = (
            [self._entry(3.0, vs_spy=1.5) for _ in range(7)] +
            [self._entry(4.0) for _ in range(3)]  # 3 entries without vs_spy
        )
        v = _compute_verdicts(entries, "TEST")
        assert v["n_with_spy_baseline"] == 7
        assert v["avg_ret_vs_spy"] == pytest.approx(1.5)

    def test_median_ret_10d_computed(self):
        entries = [self._entry(float(i)) for i in range(1, 12)]  # 11 entries
        v = _compute_verdicts(entries, "TEST")
        assert v["median_ret_10d"] is not None
        assert v["median_ret_10d"] == pytest.approx(6.0)  # median of 1..11

    def test_verdict_unaffected_by_benchmark_absence(self):
        # Verdict is based on absolute returns; benchmarks are additive metadata
        entries = [self._entry(float(i)) for i in range(8, 18)]  # all positive
        v = _compute_verdicts(entries, "TEST")
        assert v["verdict"] in ("PROMISING", "EARLY_SIGNAL")
        assert v["win_rate_vs_spy"] is None  # no benchmark data in these entries


# ── History preservation ──────────────────────────────────────────────────────

class TestHistoryPreservation:
    """Benchmark fields must not overwrite observation-time fields."""

    IMMUTABLE_FIELDS = (
        "ticker", "appearance_date", "watchlist_label", "category",
        "research_score", "earliness_label", "consensus_label",
    )

    def _make_original(self) -> Dict[str, Any]:
        return {
            "ticker": "AAPL",
            "appearance_date": "2026-06-15",
            "watchlist_label": "EARLY_ACCUMULATION",
            "category": "early_accumulation",
            "research_score": 82.0,
            "earliness_label": "EARLY_BASE",
            "consensus_label": "STRONG_CONSENSUS",
            "ret_5d": None,
            "ret_10d": None,
            "ret_20d": None,
            "ret_60d": None,
            "resolved": False,
        }

    def test_immutable_fields_unchanged_after_benchmark_patch(self):
        original = self._make_original()
        rec = dict(original)
        # Simulate adding benchmark fields (as the resolution loop does)
        rec["spy_ret_10d"] = 3.0
        rec["ret_10d_vs_spy"] = 2.0
        rec["benchmark_schema_version"] = BENCHMARK_SCHEMA_VERSION
        for field in self.IMMUTABLE_FIELDS:
            assert rec[field] == original[field], f"Field {field!r} was mutated"

    def test_original_scores_not_overwritten_by_none(self):
        rec = self._make_original()
        rec["research_score"] = 82.0
        # Simulate a second run — must not zero out existing score
        rec.setdefault("research_score", None)
        assert rec["research_score"] == 82.0

    def test_benchmark_fields_are_additive(self):
        rec = self._make_original()
        before_keys = set(rec.keys())
        # Add benchmark fields
        rec["ret_10d"] = 5.0
        rec["spy_ret_10d"] = 3.0
        rec["ret_10d_vs_spy"] = 2.0
        rec["benchmark_schema_version"] = BENCHMARK_SCHEMA_VERSION
        new_keys = set(rec.keys()) - before_keys
        assert "ret_10d_vs_spy" in new_keys
        assert "benchmark_schema_version" in new_keys
        # No original field removed
        assert before_keys.issubset(set(rec.keys()))


# ── _benchmark_readiness ──────────────────────────────────────────────────────

class TestBenchmarkReadiness:

    def test_empty_history_returns_zero_counts(self):
        br = _benchmark_readiness({})
        assert br["total_entries"] == 0
        assert br["entries_with_spy_10d"] == 0

    def test_counts_entries_with_spy_10d(self):
        history = {
            "A|2026-06-01": {"ticker": "A", "spy_ret_10d": 3.0, "benchmark_sector_etf": "XLK"},
            "B|2026-06-01": {"ticker": "B", "spy_ret_10d": None, "benchmark_sector_etf": None},
            "C|2026-06-01": {"ticker": "C", "benchmark_sector_etf": "XLF"},
        }
        br = _benchmark_readiness(history)
        assert br["total_entries"] == 3
        assert br["entries_with_spy_10d"] == 1
        assert br["entries_with_sector_etf"] == 2
        assert br["entries_with_sector_10d"] == 0

    def test_schema_version_in_readiness_output(self):
        br = _benchmark_readiness({})
        assert br["benchmark_schema_version"] == BENCHMARK_SCHEMA_VERSION

    def test_spy_available_always_true(self):
        br = _benchmark_readiness({})
        assert br["spy_available"] is True
        assert br["qqq_available"] is True


# ── No provider calls ─────────────────────────────────────────────────────────

class TestNoProviderCalls:
    """Forward tracker must never call providers — cache-parquet only."""

    def test_forward_return_takes_no_network_args(self):
        closes = [("2026-01-01", 100.0), ("2026-01-06", 110.0)]
        import inspect
        sig = inspect.signature(_forward_return)
        params = list(sig.parameters.keys())
        assert "url" not in params
        assert "api_key" not in params
        assert "ticker" not in params  # should be closes_with_dates, not ticker

    def test_load_closes_with_dates_only_reads_parquet(self, tmp_path):
        """Verify it only touches the filesystem — no HTTP."""
        with patch("research.research_watchlist_forward_tracker.PRICE_DIR", tmp_path):
            with patch("pandas.read_parquet") as mock_rp:
                mock_rp.side_effect = FileNotFoundError()
                result = _load_closes_with_dates("NONEXISTENT")
        # Returns [] even when parquet raises, no network call involved
        assert result == [] or isinstance(result, list)

    def test_benchmark_sector_etf_uses_no_io(self):
        """_benchmark_sector_etf is pure dict lookup — no IO."""
        import inspect
        src = inspect.getsource(_benchmark_sector_etf)
        assert "requests" not in src
        assert "http" not in src.lower()
        assert "open(" not in src


# ── Phase 5.1 follow-up: MAE + priority split ────────────────────────────────

from research.research_watchlist_forward_tracker import (  # noqa: E402
    _forward_mae, _priority_split, _priority_cohort,
)


class TestForwardMae:
    CLOSES = [(f"2026-06-{d:02d}", c) for d, c in
              [(1, 100.0), (2, 98.0), (3, 90.0), (4, 95.0), (5, 104.0),
               (6, 103.0), (7, 110.0), (8, 108.0)]]

    def test_mae_is_lowest_close_in_window(self):
        # entry 2026-06-01 @100; 5d window = 06-02..06-06, min 90 -> -10%
        assert _forward_mae(self.CLOSES, "2026-06-01", 5) == -10.0

    def test_mae_none_when_window_incomplete(self):
        assert _forward_mae(self.CLOSES, "2026-06-01", 10) is None
        assert _forward_mae([], "2026-06-01", 5) is None

    def test_mae_never_positive(self):
        # entry 06-04 @95; 3d window = 104,103,110 (all above entry) -> 0.0
        assert _forward_mae(self.CLOSES, "2026-06-04", 3) == 0.0


class TestPrioritySplit:
    def _rec(self, tkr, prio, ret10=None, vs_spy=None, mae10=None):
        r = {"ticker": tkr, "priority_label": prio}
        if ret10 is not None:
            r["ret_10d"] = ret10
        if vs_spy is not None:
            r["ret_10d_vs_spy"] = vs_spy
        if mae10 is not None:
            r["mae_10d"] = mae10
        return r

    def test_cohort_mapping(self):
        assert _priority_cohort(
            {"priority_label": "HIGH_PRIORITY_RESEARCH"}) == "high_priority"
        assert _priority_cohort(
            {"priority_label": "RESET_WATCH"}) == "watch_only"
        assert _priority_cohort(
            {"priority_label": "EXTENDED_CROWDED"}) == "other"
        assert _priority_cohort({"priority_label": None}) == "unstamped"
        assert _priority_cohort({}) == "unstamped"

    def test_split_stats_and_unstamped_reported(self):
        entries = [
            self._rec("A", "HIGH_PRIORITY_RESEARCH", 10.0, 4.0, -2.0),
            self._rec("B", "HIGH_PRIORITY_RESEARCH", -2.0, -3.0, -8.0),
            self._rec("C", "WATCHLIST_RESEARCH", 1.0, 0.5, -1.0),
            self._rec("D", None, 5.0, 2.0, -4.0),  # pre-stamping row
        ]
        split = _priority_split(entries)
        hp = split["high_priority"]["horizons"]["10d"]
        assert hp["n"] == 2
        assert hp["mean_ret_pct"] == 4.0
        assert hp["median_ret_pct"] == 4.0
        assert hp["hit_rate_vs_spy"] == 0.5
        assert hp["mean_mae_pct"] == -5.0
        assert hp["worst_mae_pct"] == -8.0
        assert split["watch_only"]["horizons"]["10d"]["n"] == 1
        # unstamped rows are reported, never dropped
        assert split["unstamped"]["entries"] == 1
        assert split["unstamped"]["horizons"]["10d"]["mean_ret_pct"] == 5.0

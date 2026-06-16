"""
tests/unit/test_phase4a3_field_coverage.py — Phase 4A.3 field coverage tests.

Covers:
  - Central enrichment function (research_candidate_enrichment.py)
  - above_ma200 / above_ma50 / above_ma20 computation
  - Insufficient history handling
  - Liquidity and ticker validity
  - RS vs SPY computation
  - Quarantine subtype classification
  - Scanner integration: all items have required fields after enrichment
  - Priority gating: enriched candidates can escape DATA_QUARANTINE
  - Report purity: no trade language
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("ALPACA_API_KEY", "test")
os.environ.setdefault("ALPACA_SECRET_KEY", "test")
os.environ.setdefault("FMP_API_KEY", "test")
os.environ.setdefault("FMP_BASE_URL", "https://financialmodelingprep.com/stable")
os.environ.setdefault("ALPACA_PAPER", "true")
os.environ.setdefault("PAPER_TRADING", "true")
os.environ.setdefault("DB_PATH", str(ROOT / "db" / "trading.db"))
os.environ.setdefault("CACHE_DIR", str(ROOT / "cache"))
os.environ.setdefault("LOG_DIR", str(ROOT / "logs"))

from research.research_candidate_enrichment import (
    enrich_research_candidate,
    classify_quarantine_subtype,
    _compute_missing_fields,
    _extension_state,
    _market_cap_bucket,
    QUARANTINE_INVALID,
    QUARANTINE_INSUFFICIENT_HISTORY,
    QUARANTINE_LOW_LIQUIDITY,
    QUARANTINE_DATA_INCOMPLETE,
    QUARANTINE_DATA_QUARANTINE,
    LIQUIDITY_MIN_DOLLAR_VOLUME,
    MIN_BARS_MA200,
    MIN_BARS_MA50,
    MIN_BARS_MA20,
)


# ── Test helpers ──────────────────────────────────────────────────────────────

def _make_parquet(tmp_dir: Path, sym: str, n_bars: int, price: float = 100.0,
                  volume: float = 1_000_000.0) -> None:
    """Write a simple price parquet for testing."""
    import numpy as np
    prices = [price * (1 + 0.001 * i) for i in range(n_bars)]
    vols = [volume] * n_bars
    df = pd.DataFrame({"close": prices, "volume": vols})
    df.to_parquet(tmp_dir / f"{sym.upper()}.parquet")


def _spy_closes(n: int = 300, price: float = 500.0) -> List[float]:
    return [price * (1 + 0.0005 * i) for i in range(n)]


def _base_item(ticker: str = "AAPL", category: str = "early_accumulation") -> Dict[str, Any]:
    return {
        "ticker": ticker,
        "category": category,
        "watchlist_label": "EARLY_ACCUMULATION",
        "research_score": 60.0,
        "why_appeared": "test",
        "confirms_if": "test",
        "invalidates_if": "test",
        "no_trade_recommendation": True,
    }


# ── Core enrichment tests ─────────────────────────────────────────────────────

class TestEnrichmentBasic(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.spy = _spy_closes(300)

    def test_enriches_above_ma200_when_enough_bars(self):
        _make_parquet(self.tmp, "AAPL", 250, price=100.0)
        result = enrich_research_candidate("AAPL", _base_item(), self.tmp, self.spy)
        self.assertIsNotNone(result["above_ma200"], "above_ma200 should be set with 250 bars")
        self.assertIsInstance(result["above_ma200"], bool)

    def test_no_ma200_when_insufficient_bars(self):
        _make_parquet(self.tmp, "AAPL", 100, price=100.0)
        result = enrich_research_candidate("AAPL", _base_item(), self.tmp, self.spy)
        self.assertIsNone(result["above_ma200"], "above_ma200 should be None with <200 bars")
        self.assertTrue(result["insufficient_history_for_ma200"])
        self.assertEqual(result["bars_available"], 100)

    def test_marks_insufficient_history_flag(self):
        _make_parquet(self.tmp, "AAPL", 150, price=100.0)
        result = enrich_research_candidate("AAPL", _base_item(), self.tmp, self.spy)
        self.assertTrue(result.get("insufficient_history_for_ma200"))

    def test_no_insufficient_flag_when_enough_bars(self):
        _make_parquet(self.tmp, "AAPL", 250, price=100.0)
        result = enrich_research_candidate("AAPL", _base_item(), self.tmp, self.spy)
        self.assertFalse(result.get("insufficient_history_for_ma200"))

    def test_enriches_above_ma50(self):
        _make_parquet(self.tmp, "AAPL", 100, price=100.0)
        result = enrich_research_candidate("AAPL", _base_item(), self.tmp, self.spy)
        self.assertIsNotNone(result["above_ma50"])
        self.assertIsInstance(result["above_ma50"], bool)

    def test_no_above_ma50_with_too_few_bars(self):
        _make_parquet(self.tmp, "AAPL", 30, price=100.0)
        result = enrich_research_candidate("AAPL", _base_item(), self.tmp, self.spy)
        self.assertIsNone(result.get("above_ma50"))

    def test_enriches_above_ma20(self):
        _make_parquet(self.tmp, "AAPL", 50, price=100.0)
        result = enrich_research_candidate("AAPL", _base_item(), self.tmp, self.spy)
        self.assertIsNotNone(result["above_ma20"])

    def test_enriches_dd_from_high_pct(self):
        _make_parquet(self.tmp, "AAPL", 50, price=100.0)
        result = enrich_research_candidate("AAPL", _base_item(), self.tmp, self.spy)
        self.assertIsNotNone(result["dd_from_high_pct"])

    def test_enriches_vol_trend_ratio(self):
        _make_parquet(self.tmp, "AAPL", 50, price=100.0, volume=1_000_000)
        result = enrich_research_candidate("AAPL", _base_item(), self.tmp, self.spy)
        self.assertIsNotNone(result["vol_trend_ratio"])

    def test_enriches_rs_20d_vs_spy(self):
        _make_parquet(self.tmp, "AAPL", 50, price=100.0)
        result = enrich_research_candidate("AAPL", _base_item(), self.tmp, self.spy)
        self.assertIsNotNone(result["rs_20d_vs_spy"])

    def test_enriches_rs_63d_vs_spy_when_enough_bars(self):
        _make_parquet(self.tmp, "AAPL", 100, price=100.0)
        result = enrich_research_candidate("AAPL", _base_item(), self.tmp, self.spy)
        self.assertIsNotNone(result["rs_63d_vs_spy"])

    def test_no_rs_63d_when_insufficient_bars(self):
        _make_parquet(self.tmp, "AAPL", 30, price=100.0)
        result = enrich_research_candidate("AAPL", _base_item(), self.tmp, self.spy)
        self.assertIsNone(result["rs_63d_vs_spy"])

    def test_emits_missing_fields(self):
        _make_parquet(self.tmp, "AAPL", 50, price=100.0)
        result = enrich_research_candidate("AAPL", _base_item(), self.tmp, self.spy)
        self.assertIn("missing_fields", result)
        self.assertIsInstance(result["missing_fields"], list)

    def test_missing_fields_includes_ma200_when_insufficient(self):
        _make_parquet(self.tmp, "AAPL", 100, price=100.0)
        result = enrich_research_candidate("AAPL", _base_item(), self.tmp, self.spy)
        self.assertIn("above_ma200", result["missing_fields"])

    def test_missing_fields_empty_when_all_computable(self):
        _make_parquet(self.tmp, "AAPL", 300, price=100.0, volume=5_000_000)
        result = enrich_research_candidate("AAPL", _base_item(), self.tmp, self.spy)
        # With 300 bars + good volume, no fields should be missing
        self.assertEqual(result["missing_fields"], [], f"Unexpected missing: {result['missing_fields']}")

    def test_does_not_fabricate_fields(self):
        """No fabrication: if parquet missing, ticker_valid=False, not invented data."""
        result = enrich_research_candidate("FAKEXYZ", _base_item("FAKEXYZ"), self.tmp, self.spy)
        self.assertFalse(result.get("ticker_valid"))

    def test_ticker_valid_false_when_no_parquet(self):
        result = enrich_research_candidate("FAKEXYZ", _base_item("FAKEXYZ"), self.tmp, self.spy)
        self.assertIs(result["ticker_valid"], False)

    def test_ticker_valid_true_with_enough_bars(self):
        _make_parquet(self.tmp, "AAPL", 30, price=100.0)
        result = enrich_research_candidate("AAPL", _base_item(), self.tmp, self.spy)
        self.assertIs(result["ticker_valid"], True)

    def test_data_confidence_high_when_enough_bars(self):
        _make_parquet(self.tmp, "AAPL", 250, price=100.0)
        result = enrich_research_candidate("AAPL", _base_item(), self.tmp, self.spy)
        self.assertEqual(result["data_confidence"], "HIGH")

    def test_data_confidence_medium_for_50_99_bars(self):
        _make_parquet(self.tmp, "AAPL", 80, price=100.0)
        result = enrich_research_candidate("AAPL", _base_item(), self.tmp, self.spy)
        self.assertEqual(result["data_confidence"], "MEDIUM")

    def test_data_confidence_low_for_10_49_bars(self):
        _make_parquet(self.tmp, "AAPL", 20, price=100.0)
        result = enrich_research_candidate("AAPL", _base_item(), self.tmp, self.spy)
        self.assertEqual(result["data_confidence"], "LOW")


class TestEnrichmentLiquidity(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.spy = _spy_closes(300)

    def test_liquidity_ok_true_for_high_volume(self):
        # 10M shares * $100 = $1B/day
        _make_parquet(self.tmp, "AAPL", 50, price=100.0, volume=10_000_000)
        result = enrich_research_candidate("AAPL", _base_item(), self.tmp, self.spy)
        self.assertTrue(result["liquidity_ok"])

    def test_liquidity_ok_false_for_low_volume(self):
        # 100 shares * $1 = $100/day — well below threshold
        _make_parquet(self.tmp, "AAPL", 50, price=1.0, volume=100)
        result = enrich_research_candidate("AAPL", _base_item(), self.tmp, self.spy)
        self.assertFalse(result["liquidity_ok"])

    def test_avg_dollar_volume_populated(self):
        _make_parquet(self.tmp, "AAPL", 50, price=100.0, volume=500_000)
        result = enrich_research_candidate("AAPL", _base_item(), self.tmp, self.spy)
        self.assertIsNotNone(result.get("avg_dollar_volume"))
        self.assertGreater(result["avg_dollar_volume"], 0)


class TestEnrichmentProfile(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.spy = _spy_closes(300)

    def test_sector_from_profile(self):
        _make_parquet(self.tmp, "AAPL", 50, price=100.0)
        profile = {"companyName": "Apple Inc", "sector": "Technology", "industry": "Consumer Electronics"}
        result = enrich_research_candidate("AAPL", _base_item(), self.tmp, self.spy, profile=profile)
        self.assertEqual(result["sector"], "Technology")
        self.assertEqual(result["industry"], "Consumer Electronics")
        self.assertEqual(result["company_name"], "Apple Inc")

    def test_no_sector_when_profile_none(self):
        _make_parquet(self.tmp, "AAPL", 50, price=100.0)
        result = enrich_research_candidate("AAPL", _base_item(), self.tmp, self.spy, profile=None)
        self.assertIsNone(result.get("sector"))

    def test_market_cap_bucket_populated(self):
        _make_parquet(self.tmp, "AAPL", 50, price=100.0)
        profile = {"companyName": "Apple", "sector": "Tech", "mktCap": 3_000_000_000_000}
        result = enrich_research_candidate("AAPL", _base_item(), self.tmp, self.spy, profile=profile)
        self.assertEqual(result["market_cap_bucket"], "MEGA")

    def test_market_cap_bucket_small(self):
        _make_parquet(self.tmp, "AAPL", 50, price=100.0)
        profile = {"companyName": "Small Co", "sector": "Tech", "mktCap": 500_000_000}
        result = enrich_research_candidate("AAPL", _base_item(), self.tmp, self.spy, profile=profile)
        self.assertEqual(result["market_cap_bucket"], "SMALL")


class TestEnrichmentDeepCache(unittest.TestCase):

    def setUp(self):
        self.reg = Path(tempfile.mkdtemp())
        self.deep = Path(tempfile.mkdtemp())
        self.spy = _spy_closes(300)

    def test_deep_cache_preferred_for_ma200(self):
        # Regular: 100 bars (no MA200); Deep: 250 bars (has MA200)
        _make_parquet(self.reg, "XYZ", 100, price=100.0)
        _make_parquet(self.deep, "XYZ", 250, price=100.0)
        result = enrich_research_candidate("XYZ", _base_item("XYZ"), self.reg, self.spy,
                                           deep_price_dir=self.deep)
        self.assertIsNotNone(result["above_ma200"])
        self.assertFalse(result.get("insufficient_history_for_ma200"))

    def test_regular_cache_used_when_no_deep(self):
        _make_parquet(self.reg, "XYZ", 100, price=100.0)
        result = enrich_research_candidate("XYZ", _base_item("XYZ"), self.reg, self.spy,
                                           deep_price_dir=None)
        self.assertIsNone(result["above_ma200"])
        self.assertTrue(result["insufficient_history_for_ma200"])


class TestEnrichmentIdempotent(unittest.TestCase):
    """Fields already set in base_item should NOT be overwritten."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.spy = _spy_closes(300)

    def test_existing_above_ma200_not_overwritten(self):
        _make_parquet(self.tmp, "AAPL", 250, price=100.0)
        base = _base_item()
        base["above_ma200"] = True   # pre-set
        result = enrich_research_candidate("AAPL", base, self.tmp, self.spy)
        self.assertTrue(result["above_ma200"])  # unchanged

    def test_existing_rs_63d_not_overwritten(self):
        _make_parquet(self.tmp, "AAPL", 100, price=100.0)
        base = _base_item()
        base["rs_63d_vs_spy"] = 99.9
        result = enrich_research_candidate("AAPL", base, self.tmp, self.spy)
        self.assertAlmostEqual(result["rs_63d_vs_spy"], 99.9)

    def test_scanner_set_none_above_ma200_overwritten_by_deep_cache(self):
        """Scanner emitted above_ma200=None (regular cache had <200 bars);
        deep cache has 250 bars → enrichment must overwrite the None slot."""
        reg = Path(tempfile.mkdtemp())
        deep = Path(tempfile.mkdtemp())
        _make_parquet(reg, "AAPL", 100, price=100.0)    # too few for MA200
        _make_parquet(deep, "AAPL", 250, price=100.0)   # enough for MA200
        base = _base_item()
        base["above_ma200"] = None   # scanner set explicitly to None
        result = enrich_research_candidate("AAPL", base, reg, self.spy, deep_price_dir=deep)
        self.assertIsNotNone(result["above_ma200"], "above_ma200 should be filled from deep cache")
        self.assertIsInstance(result["above_ma200"], bool)

    def test_scanner_set_none_above_ma50_overwritten_when_bars_available(self):
        """Scanner emitted above_ma50=None; regular cache has 100 bars →
        enrichment must overwrite the None with the computed bool."""
        _make_parquet(self.tmp, "AAPL", 100, price=100.0)
        base = _base_item()
        base["above_ma50"] = None   # scanner set explicitly to None
        result = enrich_research_candidate("AAPL", base, self.tmp, self.spy)
        self.assertIsNotNone(result["above_ma50"])
        self.assertIsInstance(result["above_ma50"], bool)


# ── Quarantine subtype tests ──────────────────────────────────────────────────

class TestQuarantineSubtype(unittest.TestCase):

    def test_invalid_when_no_ticker_valid(self):
        item = {"ticker_valid": False, "data_confidence": "INVALID", "bars_available": 5, "missing_fields": []}
        self.assertEqual(classify_quarantine_subtype(item), QUARANTINE_INVALID)

    def test_insufficient_history_when_bars_low(self):
        item = {
            "ticker_valid": True,
            "data_confidence": "MEDIUM",
            "bars_available": 100,
            "insufficient_history_for_ma200": True,
            "liquidity_ok": True,
            "avg_dollar_volume": 10_000_000,
            "missing_fields": ["above_ma200"],
        }
        self.assertEqual(classify_quarantine_subtype(item), QUARANTINE_INSUFFICIENT_HISTORY)

    def test_low_liquidity_when_dollar_vol_below_threshold(self):
        item = {
            "ticker_valid": True,
            "data_confidence": "HIGH",
            "bars_available": 300,
            "insufficient_history_for_ma200": False,
            "liquidity_ok": False,
            "avg_dollar_volume": 100_000,   # below $1M
            "missing_fields": [],
        }
        self.assertEqual(classify_quarantine_subtype(item), QUARANTINE_LOW_LIQUIDITY)

    def test_data_incomplete_when_some_fields_missing(self):
        item = {
            "ticker_valid": True,
            "data_confidence": "MEDIUM",
            "bars_available": 80,
            "insufficient_history_for_ma200": True,
            "liquidity_ok": True,
            "avg_dollar_volume": 5_000_000,
            "missing_fields": ["above_ma200"],
        }
        # bars >= MA50 but < MA200: INSUFFICIENT_HISTORY trumps DATA_INCOMPLETE
        self.assertEqual(classify_quarantine_subtype(item), QUARANTINE_INSUFFICIENT_HISTORY)

    def test_data_quarantine_when_no_specific_reason(self):
        item = {
            "ticker_valid": True,
            "data_confidence": "HIGH",
            "bars_available": 300,
            "insufficient_history_for_ma200": False,
            "liquidity_ok": True,
            "avg_dollar_volume": 10_000_000,
            "missing_fields": [],
        }
        self.assertEqual(classify_quarantine_subtype(item), QUARANTINE_DATA_QUARANTINE)


# ── Extension state tests ─────────────────────────────────────────────────────

class TestExtensionState(unittest.TestCase):

    def test_normal_when_none(self):
        from research.research_scoring import EXT_NORMAL
        self.assertEqual(_extension_state(None), EXT_NORMAL)

    def test_parabolic_above_20(self):
        from research.research_scoring import EXT_PARABOLIC
        self.assertEqual(_extension_state(25.0), EXT_PARABOLIC)

    def test_extended_15_to_20(self):
        from research.research_scoring import EXT_EXTENDED
        self.assertEqual(_extension_state(17.0), EXT_EXTENDED)

    def test_stretched_8_to_15(self):
        from research.research_scoring import EXT_STRETCHED
        self.assertEqual(_extension_state(10.0), EXT_STRETCHED)

    def test_normal_below_8(self):
        from research.research_scoring import EXT_NORMAL
        self.assertEqual(_extension_state(5.0), EXT_NORMAL)


# ── Market cap bucket tests ───────────────────────────────────────────────────

class TestMarketCapBucket(unittest.TestCase):

    def test_mega(self):
        self.assertEqual(_market_cap_bucket(200_000_000_001), "MEGA")

    def test_large(self):
        self.assertEqual(_market_cap_bucket(50_000_000_000), "LARGE")

    def test_mid(self):
        self.assertEqual(_market_cap_bucket(5_000_000_000), "MID")

    def test_small(self):
        self.assertEqual(_market_cap_bucket(500_000_000), "SMALL")

    def test_micro(self):
        self.assertEqual(_market_cap_bucket(50_000_000), "MICRO")

    def test_none_returns_none(self):
        self.assertIsNone(_market_cap_bucket(None))


# ── Priority gating integration tests ────────────────────────────────────────

class TestPriorityGatingWithEnrichment(unittest.TestCase):
    """Verify enriched items correctly escape DATA_QUARANTINE when all fields present."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.spy = _spy_closes(300)

    def _enrich_and_gate(self, ticker: str, n_bars: int, volume: float,
                          category: str = "sector_theme_leader") -> str:
        """Helper: enrich and run priority_label on result."""
        _make_parquet(self.tmp, ticker, n_bars, price=100.0, volume=volume)
        base = _base_item(ticker, category)
        base["rs_20d_vs_spy"] = 5.0
        base["rs_63d_vs_spy"] = 8.0
        base["all_categories"] = [category, "early_accumulation"]  # multi-category
        item = enrich_research_candidate(ticker, base, self.tmp, self.spy)

        from research.research_scoring import priority_label, earliness_detail, quality_adjusted_consensus
        ed = earliness_detail(
            rs_63=item.get("rs_63d_vs_spy"),
            rs_20=item.get("rs_20d_vs_spy"),
            above_ma50=item.get("above_ma50"),
            above_ma200=item.get("above_ma200"),
            vol_trend_ratio=item.get("vol_trend_ratio"),
        )
        qac = quality_adjusted_consensus(
            categories=item.get("all_categories", [category]),
            data_confidence=item.get("data_confidence"),
            earliness=ed["label"],
            extension_state=ed["extension_state"],
        )
        pri, _ = priority_label(
            data_confidence=item.get("data_confidence"),
            ticker_valid=item.get("ticker_valid"),
            liquidity_ok=item.get("liquidity_ok"),
            earliness=ed["label"],
            consensus=qac["consensus_label"],
            missing_fields=item.get("missing_fields") or None,
        )
        return pri

    def test_high_priority_candidate_escapes_quarantine_with_all_fields(self):
        from research.research_scoring import HIGH_PRIORITY_RESEARCH, DATA_QUARANTINE
        pri = self._enrich_and_gate("MSFT", n_bars=300, volume=10_000_000)
        # Should not be DATA_QUARANTINE with full enrichment
        self.assertNotEqual(pri, DATA_QUARANTINE,
                            "Fully enriched candidate should NOT be in DATA_QUARANTINE")

    def test_low_liquidity_cannot_be_high_priority(self):
        from research.research_scoring import HIGH_PRIORITY_RESEARCH, TOP_RESEARCH, DATA_QUARANTINE
        pri = self._enrich_and_gate("ILLIQ", n_bars=300, volume=10)  # very low volume
        # Low liquidity blocks high priority
        self.assertNotIn(pri, (HIGH_PRIORITY_RESEARCH, TOP_RESEARCH))

    def test_insufficient_history_stays_quarantined(self):
        from research.research_scoring import DATA_QUARANTINE
        pri = self._enrich_and_gate("NEWCO", n_bars=50, volume=10_000_000)
        # Without above_ma200, earliness=UNKNOWN → quarantine
        self.assertEqual(pri, DATA_QUARANTINE)

    def test_invalid_ticker_is_invalid_priority(self):
        from research.research_scoring import INVALID_PRIORITY
        # No parquet for FAKEXYZ
        base = _base_item("FAKEXYZ")
        result = enrich_research_candidate("FAKEXYZ", base, self.tmp, self.spy)
        from research.research_scoring import priority_label, earliness_detail, quality_adjusted_consensus
        ed = earliness_detail(above_ma50=None, above_ma200=None)
        qac = quality_adjusted_consensus(categories=["early_accumulation"],
                                          earliness=ed["label"])
        pri, _ = priority_label(
            ticker_valid=result.get("ticker_valid"),
            data_confidence=result.get("data_confidence"),
            earliness=ed["label"],
            consensus=qac["consensus_label"],
        )
        self.assertEqual(pri, INVALID_PRIORITY)


# ── Scanner integration tests ─────────────────────────────────────────────────

class TestScannerEnrichmentIntegration(unittest.TestCase):
    """Verify build_scanner() output has all required fields after Phase 4A.3."""

    def setUp(self):
        # Patch price directory to use temp directory
        self.tmp = Path(tempfile.mkdtemp())
        self.spy_closes = _spy_closes(300)
        # Create SPY parquet
        _make_parquet(self.tmp, "SPY", 300, price=500.0, volume=50_000_000)

    def _run_scanner_offline(self):
        import research.research_scanner as rs
        orig_price_dir = rs.PRICE_DIR
        orig_deep_dir = rs.DEEP_PRICE_DIR
        try:
            rs.PRICE_DIR = self.tmp
            rs.DEEP_PRICE_DIR = self.tmp
            result = rs.build_scanner(offline=True, universe_cap=5)
            return result
        finally:
            rs.PRICE_DIR = orig_price_dir
            rs.DEEP_PRICE_DIR = orig_deep_dir

    def _populate_universe(self, tickers, n_bars=250):
        for sym in tickers:
            _make_parquet(self.tmp, sym, n_bars, price=100.0, volume=5_000_000)

    def test_all_items_have_ticker_valid(self):
        self._populate_universe(["AAPL", "MSFT", "GOOG"])
        result = self._run_scanner_offline()
        for item in result.get("watchlist", []):
            self.assertIn("ticker_valid", item,
                          f"{item['ticker']} missing ticker_valid")

    def test_all_items_have_missing_fields(self):
        self._populate_universe(["AAPL", "MSFT", "GOOG"])
        result = self._run_scanner_offline()
        for item in result.get("watchlist", []):
            self.assertIn("missing_fields", item,
                          f"{item['ticker']} missing missing_fields list")
            self.assertIsInstance(item["missing_fields"], list)

    def test_all_items_have_data_confidence(self):
        self._populate_universe(["AAPL", "MSFT", "GOOG"])
        result = self._run_scanner_offline()
        for item in result.get("watchlist", []):
            self.assertIn("data_confidence", item,
                          f"{item['ticker']} missing data_confidence")

    def test_all_items_have_earliness_label(self):
        self._populate_universe(["AAPL", "MSFT", "GOOG"])
        result = self._run_scanner_offline()
        for item in result.get("watchlist", []):
            self.assertIn("earliness_label", item,
                          f"{item['ticker']} missing earliness_label")

    def test_all_items_have_priority_or_consensus_label(self):
        self._populate_universe(["AAPL", "MSFT", "GOOG"])
        result = self._run_scanner_offline()
        for item in result.get("watchlist", []):
            self.assertIn("consensus_label", item,
                          f"{item['ticker']} missing consensus_label")

    def test_items_with_200_bars_have_above_ma200(self):
        self._populate_universe(["AAPL"], n_bars=300)
        result = self._run_scanner_offline()
        for item in result.get("watchlist", []):
            if item["ticker"] == "AAPL" and item.get("bars_available", 0) >= MIN_BARS_MA200:
                self.assertIsNotNone(item.get("above_ma200"),
                                     "AAPL with 300 bars should have above_ma200")

    def test_items_with_50_bars_have_above_ma50(self):
        self._populate_universe(["AAPL"], n_bars=100)
        result = self._run_scanner_offline()
        for item in result.get("watchlist", []):
            if item["ticker"] == "AAPL" and item.get("bars_available", 0) >= MIN_BARS_MA50:
                self.assertIsNotNone(item.get("above_ma50"),
                                     "AAPL with 100 bars should have above_ma50")


# ── Report purity tests ───────────────────────────────────────────────────────

class TestReportPurityWithEnrichment(unittest.TestCase):
    """Verify the daily radar report has no trade language after Phase 4A.3."""

    TRADE_TERMS = [
        "buy now", "sell now", "entry price", "stop loss", "position size",
        "trade recommendation", "paper signal", "live signal", "auto trade",
    ]
    LEGACY_TERMS = [
        "VOYAGER", "SNIPER", "REMORA", "SHORT_A", "LRR",
        "holdout scoreboard", "strategy tournament", "paper loop",
        "READY_FOR_DEEPER_BACKTEST",
    ]

    def _load_report(self) -> str:
        report_path = ROOT / "docs" / "research" / "DAILY_ALPHA_RADAR_REPORT.md"
        if not report_path.exists():
            return ""
        return report_path.read_text(encoding="utf-8")

    def test_no_trade_terms_in_report(self):
        report = self._load_report()
        if not report:
            self.skipTest("Report not generated yet")
        for term in self.TRADE_TERMS:
            self.assertNotIn(term.lower(), report.lower(),
                             f"Trade term '{term}' found in report")

    def test_no_legacy_strategy_terms_in_report(self):
        report = self._load_report()
        if not report:
            self.skipTest("Report not generated yet")
        for term in self.LEGACY_TERMS:
            self.assertNotIn(term, report,
                             f"Legacy strategy term '{term}' found in report")

    def test_report_has_scanner_field_coverage_section(self):
        """Phase 4A.3 must add a Scanner Field Coverage section."""
        report = self._load_report()
        if not report:
            self.skipTest("Report not generated yet")
        self.assertIn("Scanner Field Coverage", report)

    def test_report_has_quarantine_breakdown(self):
        report = self._load_report()
        if not report:
            self.skipTest("Report not generated yet")
        # Either "Quarantine breakdown" header or one of the sub-types
        has_breakdown = (
            "Quarantine breakdown" in report or
            "INSUFFICIENT_HISTORY" in report or
            "DATA_INCOMPLETE" in report
        )
        self.assertTrue(has_breakdown, "Report should show quarantine breakdown")

    def test_report_has_research_only_mode(self):
        report = self._load_report()
        if not report:
            self.skipTest("Report not generated yet")
        self.assertIn("RESEARCH_ONLY_MODE", report)


# ── Quarantine constants tests ────────────────────────────────────────────────

class TestQuarantineConstants(unittest.TestCase):

    def test_all_subtypes_defined(self):
        from research.research_candidate_enrichment import QUARANTINE_SUBTYPES
        self.assertIn(QUARANTINE_INVALID, QUARANTINE_SUBTYPES)
        self.assertIn(QUARANTINE_INSUFFICIENT_HISTORY, QUARANTINE_SUBTYPES)
        self.assertIn(QUARANTINE_LOW_LIQUIDITY, QUARANTINE_SUBTYPES)
        self.assertIn(QUARANTINE_DATA_INCOMPLETE, QUARANTINE_SUBTYPES)
        self.assertIn(QUARANTINE_DATA_QUARANTINE, QUARANTINE_SUBTYPES)

    def test_liquidity_threshold_is_one_million(self):
        self.assertEqual(LIQUIDITY_MIN_DOLLAR_VOLUME, 1_000_000)


if __name__ == "__main__":
    unittest.main()

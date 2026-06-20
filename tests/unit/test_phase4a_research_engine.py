"""tests/unit/test_phase4a_research_engine.py — Phase 4A Alpha Radar tests.

Covers:
  - research_scoring.earliness_label (all 7 buckets + UNKNOWN)
  - research_scoring.consensus_label (all 4 levels)
  - research_coverage_audit.build_coverage_audit (offline smoke)
  - research_change_detector.detect_changes (first-run + delta cases)
  - research_watchlist_forward_tracker.run_forward_tracker (offline smoke)
  - ten_x_candidate_radar.scan_ten_x (offline smoke, label guardrails)
  - Forbidden output guardrails: no trade recommendations in any sidecar
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest

os.environ.setdefault("GEM_TRADER_SKIP_DOTENV", "true")
os.environ.setdefault("ALPACA_API_KEY", "offline")
os.environ.setdefault("ALPACA_SECRET_KEY", "offline")
os.environ.setdefault("FMP_API_KEY", "offline")
os.environ.setdefault("PAPER_TRADING", "true")
os.environ.setdefault("ALPACA_PAPER", "true")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# ── research_scoring ─────────────────────────────────────────────────────────

from research.research_scoring import (  # noqa: E402
    earliness_label,
    consensus_label,
    EARLY, DEVELOPING, RECLAIM_WATCH, RESET_WATCH, EXTENDED, LATE, INVALIDATED, UNKNOWN_EARLINESS,
    SINGLE_SIGNAL, DOUBLE_CONFIRMATION, MULTI_CONFIRMATION, HIGH_PRIORITY_RESEARCH,
    EARLINESS_LABELS, CONSENSUS_LABELS,
)


class TestEarlinessLabel:

    def test_early_above_ma50_below_ma200_rs_positive_vol_rising(self):
        assert earliness_label(
            rs_63=8.0, above_ma50=True, above_ma200=False, vol_trend_ratio=1.1
        ) == EARLY

    def test_early_rs_positive_no_vol(self):
        # RS improving alone is enough for EARLY classification
        assert earliness_label(
            rs_63=5.0, above_ma50=True, above_ma200=False
        ) == EARLY

    def test_reclaim_watch_below_ma50_below_ma200_but_close(self):
        # Below MA200, above_ma50=False, no RS signal
        assert earliness_label(
            rs_63=-2.0, above_ma50=False, above_ma200=False
        ) == RECLAIM_WATCH

    def test_developing_above_both_mas_rs_ok(self):
        assert earliness_label(
            rs_20=3.0, above_ma50=True, above_ma200=True
        ) == DEVELOPING

    def test_developing_above_both_mas_no_rs_data(self):
        assert earliness_label(above_ma50=True, above_ma200=True) == DEVELOPING

    def test_reset_watch_above_ma200_below_ma50(self):
        assert earliness_label(above_ma50=False, above_ma200=True) == RESET_WATCH

    def test_extended_above_15pct_ma200(self):
        assert earliness_label(
            above_ma50=True, above_ma200=True, extension_vs_ma200_pct=18.0
        ) == EXTENDED

    def test_late_above_20pct_and_high_rs63(self):
        assert earliness_label(
            rs_63=25.0, above_ma50=True, above_ma200=True, extension_vs_ma200_pct=22.0
        ) == LATE

    def test_invalidated_below_ma200_deeply_negative_rs(self):
        assert earliness_label(
            rs_63=-15.0, above_ma50=False, above_ma200=False
        ) == INVALIDATED

    def test_unknown_when_no_ma_data(self):
        assert earliness_label() == UNKNOWN_EARLINESS

    def test_unknown_when_only_one_ma(self):
        assert earliness_label(above_ma50=True) == UNKNOWN_EARLINESS

    def test_all_labels_are_valid(self):
        for label in EARLINESS_LABELS:
            assert isinstance(label, str) and len(label) > 0

    def test_extended_overrides_developing(self):
        # Even if above both MAs, extended label should win when ext > 15
        result = earliness_label(
            rs_63=5.0, above_ma50=True, above_ma200=True, extension_vs_ma200_pct=16.0
        )
        assert result == EXTENDED

    def test_late_overrides_extended(self):
        # LATE requires > 20% extension + rs_63 > 20
        result = earliness_label(
            rs_63=21.0, above_ma50=True, above_ma200=True, extension_vs_ma200_pct=21.0
        )
        assert result == LATE

    def test_not_late_if_rs63_not_high_enough(self):
        # extension > 20 but rs_63 = 15 → EXTENDED not LATE
        result = earliness_label(
            rs_63=15.0, above_ma50=True, above_ma200=True, extension_vs_ma200_pct=21.0
        )
        assert result == EXTENDED


class TestConsensusLabel:

    def test_single_signal_one_category(self):
        assert consensus_label(["early_accumulation"]) == SINGLE_SIGNAL

    def test_single_signal_empty(self):
        assert consensus_label([]) == SINGLE_SIGNAL

    def test_double_confirmation_two_categories(self):
        assert consensus_label(["early_accumulation", "catalyst_watch"]) == DOUBLE_CONFIRMATION

    def test_double_confirmation_deduplicates(self):
        # Same category twice should still be SINGLE_SIGNAL
        assert consensus_label(["early_accumulation", "early_accumulation"]) == SINGLE_SIGNAL

    def test_multi_confirmation_three_categories(self):
        result = consensus_label(["early_accumulation", "catalyst_watch", "sector_theme_leader"])
        assert result == MULTI_CONFIRMATION

    def test_high_priority_three_categories_high_score(self):
        result = consensus_label(
            ["early_accumulation", "catalyst_watch", "sector_theme_leader"],
            research_score=75.0,
        )
        assert result == HIGH_PRIORITY_RESEARCH

    def test_multi_not_high_priority_when_score_below_70(self):
        result = consensus_label(
            ["early_accumulation", "catalyst_watch", "sector_theme_leader"],
            research_score=65.0,
        )
        assert result == MULTI_CONFIRMATION

    def test_all_labels_are_valid(self):
        for label in CONSENSUS_LABELS:
            assert isinstance(label, str) and len(label) > 0


# ── research_coverage_audit ───────────────────────────────────────────────────

from research.research_coverage_audit import (  # noqa: E402
    _confidence_level, build_coverage_audit,
)


class TestConfidenceLevel:

    def test_invalid_below_20_bars(self):
        assert _confidence_level(bars=10, age_days=1.0, fmp_cached=True) == "INVALID"

    def test_low_below_60_bars(self):
        assert _confidence_level(bars=50, age_days=1.0, fmp_cached=True) == "LOW"

    def test_low_stale_above_7_days(self):
        assert _confidence_level(bars=90, age_days=8.0, fmp_cached=True) == "LOW"

    def test_medium_enough_bars_not_stale_no_fmp(self):
        assert _confidence_level(bars=65, age_days=2.0, fmp_cached=False) == "MEDIUM"

    def test_high_90_bars_fresh_fmp_cached(self):
        assert _confidence_level(bars=95, age_days=1.0, fmp_cached=True) == "HIGH"

    def test_medium_90_bars_fresh_no_fmp(self):
        # FMP missing drops from HIGH to MEDIUM
        assert _confidence_level(bars=95, age_days=1.0, fmp_cached=False) == "MEDIUM"


class TestBuildCoverageAudit:

    def test_smoke_offline_empty_price_dir(self, tmp_path):
        # Patch PRICE_DIR to an empty temp dir and DB_PATH to nonexistent
        with patch("research.research_coverage_audit.PRICE_DIR", tmp_path), \
             patch("research.research_coverage_audit.cfg") as mock_cfg:
            mock_cfg.DB_PATH = tmp_path / "trading.db"
            result = build_coverage_audit()
        assert result["research_only"] is True
        assert result["total_tickers"] == 0
        assert result["guardrails"]["no_trade_recommendation"] is True

    def test_confidence_counts_in_output(self, tmp_path):
        with patch("research.research_coverage_audit.PRICE_DIR", tmp_path), \
             patch("research.research_coverage_audit.cfg") as mock_cfg:
            mock_cfg.DB_PATH = tmp_path / "trading.db"
            result = build_coverage_audit()
        assert "confidence_counts" in result
        for key in ("HIGH", "MEDIUM", "LOW", "INVALID"):
            assert key in result["confidence_counts"]

    def test_no_trade_recommendation_guardrail(self, tmp_path):
        with patch("research.research_coverage_audit.PRICE_DIR", tmp_path), \
             patch("research.research_coverage_audit.cfg") as mock_cfg:
            mock_cfg.DB_PATH = tmp_path / "trading.db"
            result = build_coverage_audit()
        assert result["guardrails"]["no_trade_recommendation"] is True
        text = json.dumps(result)
        for forbidden in ("buy now", "sell now", "entry price", "stop loss", "position size"):
            assert forbidden not in text.lower()


# ── research_change_detector ─────────────────────────────────────────────────

from research.research_change_detector import detect_changes  # noqa: E402


def _make_scanner(tickers_scores: List[tuple]) -> Dict[str, Any]:
    """Build a minimal scanner dict for testing."""
    watchlist = [
        {"ticker": t, "research_score": s, "watchlist_label": f"LABEL_{t}", "category": "test"}
        for t, s in tickers_scores
    ]
    return {"generated_at": "2026-06-15T00:00:00+00:00", "watchlist": watchlist}


class TestDetectChanges:

    def test_first_run_no_previous(self):
        current = _make_scanner([("AAPL", 70.0), ("NVDA", 65.0)])
        result = detect_changes(current, previous=None)
        assert result["first_run"] is True
        assert result["changes"] == []
        assert result["research_only"] is True

    def test_new_entry_detected(self):
        prev = _make_scanner([("AAPL", 70.0)])
        curr = _make_scanner([("AAPL", 70.0), ("NVDA", 65.0)])
        result = detect_changes(curr, prev)
        new = [c for c in result["changes"] if c["change_type"] == "NEW_ENTRY"]
        assert len(new) == 1
        assert new[0]["ticker"] == "NVDA"

    def test_dropped_detected(self):
        prev = _make_scanner([("AAPL", 70.0), ("MSFT", 60.0)])
        curr = _make_scanner([("AAPL", 70.0)])
        result = detect_changes(curr, prev)
        dropped = [c for c in result["changes"] if c["change_type"] == "DROPPED"]
        assert len(dropped) == 1
        assert dropped[0]["ticker"] == "MSFT"

    def test_score_up_detected(self):
        prev = _make_scanner([("AAPL", 60.0)])
        curr = _make_scanner([("AAPL", 68.0)])
        result = detect_changes(curr, prev)
        up = [c for c in result["changes"] if c["change_type"] == "SCORE_UP"]
        assert len(up) == 1
        assert up[0]["ticker"] == "AAPL"
        assert up[0]["score_delta"] == pytest.approx(8.0)

    def test_score_down_detected(self):
        prev = _make_scanner([("AAPL", 70.0)])
        curr = _make_scanner([("AAPL", 60.0)])
        result = detect_changes(curr, prev)
        down = [c for c in result["changes"] if c["change_type"] == "SCORE_DOWN"]
        assert len(down) == 1

    def test_small_score_change_not_reported(self):
        # Delta < 5 should not trigger SCORE_UP/DOWN
        prev = _make_scanner([("AAPL", 70.0)])
        curr = _make_scanner([("AAPL", 72.0)])
        result = detect_changes(curr, prev)
        assert not any(c["change_type"] in ("SCORE_UP", "SCORE_DOWN") for c in result["changes"])

    def test_label_change_detected(self):
        prev = _make_scanner([("AAPL", 70.0)])
        curr_watchlist = [{"ticker": "AAPL", "research_score": 70.0, "watchlist_label": "NEW_LABEL", "category": "test"}]
        curr = {"generated_at": "2026-06-15T00:00:00+00:00", "watchlist": curr_watchlist}
        result = detect_changes(curr, prev)
        relabeled = [c for c in result["changes"] if c["change_type"] == "LABEL_CHANGE"]
        assert len(relabeled) == 1
        assert relabeled[0]["ticker"] == "AAPL"

    def test_no_changes_summary(self):
        prev = _make_scanner([("AAPL", 70.0)])
        curr = _make_scanner([("AAPL", 70.0)])
        result = detect_changes(curr, prev)
        assert result["summary"] == "no significant changes"

    def test_no_trade_recommendation_guardrail(self):
        curr = _make_scanner([("AAPL", 70.0)])
        result = detect_changes(curr, None)
        assert result["research_only"] is True
        text = json.dumps(result)
        for forbidden in ("buy now", "sell now", "entry price", "stop loss", "position size"):
            assert forbidden not in text.lower()


# ── ten_x_candidate_radar ────────────────────────────────────────────────────

from research.ten_x_candidate_radar import scan_ten_x  # noqa: E402


class TestTenXCandidateRadar:

    def test_smoke_empty_price_dir(self, tmp_path):
        with patch("research.ten_x_candidate_radar.PRICE_DIR", tmp_path):
            result = scan_ten_x(offline=True)
        assert result["research_only"] is True
        assert result["candidate_count"] == 0
        assert result["guardrails"]["no_trade_recommendation"] is True

    def test_guardrails_always_present(self, tmp_path):
        with patch("research.ten_x_candidate_radar.PRICE_DIR", tmp_path):
            result = scan_ten_x(offline=True)
        g = result["guardrails"]
        assert g["no_trade_recommendation"] is True
        assert g["no_buy_sell"] is True
        assert g["no_entry_stop_target"] is True
        assert g["speculative_research_only"] is True

    def test_no_forbidden_output_in_sidecar(self, tmp_path):
        with patch("research.ten_x_candidate_radar.PRICE_DIR", tmp_path):
            result = scan_ten_x(offline=True)
        text = json.dumps(result).lower()
        for forbidden in ("buy now", "sell now", "entry price", "stop loss", "position size", "trade signal"):
            assert forbidden not in text, f"Forbidden term found: {forbidden!r}"

    def test_label_field_always_present_in_candidates(self, tmp_path):
        # Create a mock parquet with enough bars to pass the MIN_BARS check
        import pandas as pd
        import numpy as np
        pf = tmp_path / "MOCK.parquet"
        dates = pd.date_range("2025-01-01", periods=100, freq="B")
        # Simulate a drawdown of >40%: peak = 100, current = 50
        closes = list(np.linspace(100, 50, 100))
        volumes = [1_000_000.0] * 100
        df = pd.DataFrame({"date": dates, "close": closes, "volume": volumes})
        df.to_parquet(pf)

        spy_pf = tmp_path / "SPY.parquet"
        spy_closes = [400.0] * 100
        spy_df = pd.DataFrame({"date": dates, "close": spy_closes, "volume": [50_000_000.0] * 100})
        spy_df.to_parquet(spy_pf)

        with patch("research.ten_x_candidate_radar.PRICE_DIR", tmp_path):
            result = scan_ten_x(offline=True)

        for c in result.get("candidates", []):
            assert "label" in c
            from research.ten_x_candidate_radar import TEN_X_LABELS
            assert c["label"] in TEN_X_LABELS, f"Unexpected label: {c['label']!r}"
            assert "no_trade_recommendation" in c
            assert c["no_trade_recommendation"] is True


# ── research_watchlist_forward_tracker ───────────────────────────────────────

from research.research_watchlist_forward_tracker import _forward_return, _compute_verdicts  # noqa: E402


class TestForwardReturn:

    def _closes(self, values: List[float]) -> List[tuple]:
        import string
        return [(f"2026-01-{i+1:02d}", v) for i, v in enumerate(values)]

    def test_5d_return_computed(self):
        closes = self._closes([100.0, 101, 102, 103, 104, 110.0])
        ret = _forward_return(closes, "2026-01-01", horizon=5)
        assert ret == pytest.approx(10.0)

    def test_returns_none_when_not_enough_bars(self):
        closes = self._closes([100.0, 105.0])
        assert _forward_return(closes, "2026-01-01", horizon=5) is None

    def test_returns_none_for_future_date(self):
        closes = self._closes([100.0] * 30)
        assert _forward_return(closes, "2030-01-01", horizon=5) is None


class TestComputeVerdicts:

    def test_need_more_data_when_few_entries(self):
        entries = [{"ret_10d": 5.0}] * 3
        v = _compute_verdicts(entries, "TEST")
        assert v["verdict"] == "NEED_MORE_DATA"

    def test_promising_high_win_rate(self):
        entries = [{"ret_10d": float(i)} for i in range(8, 18)]  # all positive
        v = _compute_verdicts(entries, "TEST")
        assert v["verdict"] in ("PROMISING", "EARLY_SIGNAL")

    def test_no_forward_edge_mostly_negative(self):
        entries = [{"ret_10d": -5.0}] * 8 + [{"ret_10d": 3.0}] * 2
        v = _compute_verdicts(entries, "TEST")
        assert v["verdict"] == "NO_FORWARD_EDGE"

    def test_matured_count_excludes_nulls(self):
        entries = [{"ret_10d": None}] * 3 + [{"ret_10d": 5.0}] * 7
        v = _compute_verdicts(entries, "TEST")
        assert v["matured_entries"] == 7


# ── Integration: research_scanner imports scoring without error ──────────────

class TestResearchScannerIntegration:

    def test_scanner_imports_scoring_without_error(self):
        import research.research_scanner as scanner
        assert hasattr(scanner, "_earliness_label")
        assert hasattr(scanner, "_consensus_label")

    def test_build_scanner_offline_smoke(self, tmp_path):
        """build_scanner completes with an empty price dir (offline)."""
        import research.research_scanner as scanner
        with patch.object(scanner, "PRICE_DIR", tmp_path), \
             patch.object(scanner, "_is_offline_fmp", return_value=True), \
             patch.object(scanner, "_load_social_data", return_value={}):
            result = scanner.build_scanner(offline=True, universe_cap=0)
        assert result["research_only"] is True
        assert result["guardrails"]["no_trade_recommendation"] is True

    def test_watchlist_items_have_earliness_and_consensus(self, tmp_path):
        """When the scanner produces results, each item has earliness/consensus fields."""
        import pandas as pd
        import numpy as np
        import research.research_scanner as scanner

        # Create a minimal price parquet for SPY + one ticker
        dates = pd.date_range("2025-01-01", periods=100, freq="B")
        closes = list(np.linspace(300, 350, 100))
        volumes = [1_000_000.0] * 100
        for sym in ("SPY", "AAPL"):
            df = pd.DataFrame({"date": dates, "close": closes, "volume": volumes})
            df.to_parquet(tmp_path / f"{sym}.parquet")

        with patch.object(scanner, "PRICE_DIR", tmp_path), \
             patch.object(scanner, "DEEP_PRICE_DIR", tmp_path / "deep"), \
             patch.object(scanner, "RESEARCH_DIR", tmp_path), \
             patch.object(scanner, "_is_offline_fmp", return_value=True), \
             patch.object(scanner, "_load_social_data", return_value={}), \
             patch.object(scanner, "_batch_fmp_profiles", return_value={}):
            result = scanner.build_scanner(offline=True, universe_cap=50)

        for item in result.get("watchlist", []):
            assert "earliness_label" in item, f"Missing earliness_label on {item['ticker']}"
            assert "consensus_label" in item, f"Missing consensus_label on {item['ticker']}"
            assert "all_categories" in item

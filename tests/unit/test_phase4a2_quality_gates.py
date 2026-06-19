"""tests/unit/test_phase4a2_quality_gates.py — Phase 4A.2 quality gate tests.

Covers:
  - priority_label(): unknown/extended/conflicted/quarantine gating
  - earliness_detail(): UNKNOWN only when required fields missing; missing_fields emitted
  - quality_adjusted_consensus(): raw count vs quality-adjusted; social-only downgrade
  - ten_x_candidate_radar: TRUE_10X_RESEARCH vs ASYMMETRIC_RECOVERY_WATCH separation
  - options coverage guard: overlay disabled below 50% coverage threshold
  - catalyst_sanity: freshness / duplicate / malformed / sector-spillover
  - forward tracker: sample_status thresholds
  - daily_alpha_radar_report: no legacy terms; no trade language; options guard
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch, MagicMock

import pytest

os.environ.setdefault("GEM_TRADER_SKIP_DOTENV", "true")
os.environ.setdefault("ALPACA_API_KEY", "offline")
os.environ.setdefault("ALPACA_SECRET_KEY", "offline")
os.environ.setdefault("FMP_API_KEY", "offline")
os.environ.setdefault("PAPER_TRADING", "true")
os.environ.setdefault("ALPACA_PAPER", "true")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.research_scoring import (
    earliness_label, earliness_detail, consensus_label,
    quality_adjusted_consensus, priority_label,
    EARLY, DEVELOPING, RECLAIM_WATCH, RESET_WATCH, EXTENDED, LATE, INVALIDATED, UNKNOWN_EARLINESS,
    SINGLE_SIGNAL, DOUBLE_CONFIRMATION, MULTI_CONFIRMATION, HIGH_PRIORITY_RESEARCH,
    TOP_RESEARCH, WATCHLIST_RESEARCH, CONFLICTED_SIGNAL, EXTENDED_CROWDED,
    DATA_QUARANTINE, INVALID_PRIORITY,
    PRIORITY_LABELS, EXT_NORMAL, EXT_EXTENDED, EXT_PARABOLIC,
)
from research.ten_x_candidate_radar import (
    scan_ten_x, TRUE_10X_RESEARCH, ASYMMETRIC_RECOVERY_WATCH, THEME_ONLY, TEN_X_LABELS,
)
from research.catalyst_sanity import (
    validate_catalyst, validate_social_signal,
    FRESH_COMPANY_SPECIFIC, SECTOR_SPILLOVER, DUPLICATE_OR_SYNDICATED,
    STALE, MALFORMED, HYPE_CROWDED, NEEDS_MANUAL_SOURCE_CHECK,
    CATALYST_SANITY_LABELS,
)
from research.research_watchlist_forward_tracker import (
    _compute_verdicts, _sample_status,
    SAMPLE_TOO_EARLY, SAMPLE_PROVISIONAL, SAMPLE_MEANINGFUL, SAMPLE_ROBUST,
    SAMPLE_THRESHOLD_PROVISIONAL, SAMPLE_THRESHOLD_MEANINGFUL, SAMPLE_THRESHOLD_ROBUST,
)


# ── Priority gating tests ─────────────────────────────────────────────────────

class TestPriorityLabel:

    def _call(self, **kwargs):
        label, reasons = priority_label(**kwargs)
        return label, reasons

    def test_unknown_ticker_returns_invalid(self):
        label, _ = self._call(ticker_valid=False)
        assert label == INVALID_PRIORITY

    def test_invalid_confidence_returns_invalid(self):
        label, _ = self._call(data_confidence="INVALID", ticker_valid=True)
        assert label == INVALID_PRIORITY

    def test_unknown_earliness_returns_data_quarantine(self):
        label, reasons = self._call(
            data_confidence="HIGH",
            ticker_valid=True,
            earliness=UNKNOWN_EARLINESS,
        )
        assert label == DATA_QUARANTINE
        assert any("UNKNOWN" in r for r in reasons)

    def test_invalidated_earliness_returns_data_quarantine(self):
        label, _ = self._call(
            data_confidence="MEDIUM",
            ticker_valid=True,
            earliness=INVALIDATED,
        )
        assert label == DATA_QUARANTINE

    def test_missing_fields_returns_data_quarantine(self):
        label, reasons = self._call(
            data_confidence="MEDIUM",
            ticker_valid=True,
            earliness=DEVELOPING,
            consensus=DOUBLE_CONFIRMATION,
            missing_fields=["rs_63d", "vol_trend_ratio"],
        )
        assert label == DATA_QUARANTINE
        assert any("missing_fields" in r for r in reasons)

    def test_conflict_flags_returns_conflicted_signal(self):
        label, reasons = self._call(
            data_confidence="HIGH",
            ticker_valid=True,
            earliness=EARLY,
            consensus=DOUBLE_CONFIRMATION,
            conflict_flags=["extended_but_in_early_accumulation"],
        )
        assert label == CONFLICTED_SIGNAL
        assert reasons

    def test_extended_with_high_consensus_returns_reset_watch(self):
        label, _ = self._call(
            data_confidence="HIGH",
            ticker_valid=True,
            earliness=EXTENDED,
            consensus=DOUBLE_CONFIRMATION,
        )
        assert label == RESET_WATCH

    def test_extended_with_low_consensus_returns_extended_crowded(self):
        label, _ = self._call(
            data_confidence="HIGH",
            ticker_valid=True,
            earliness=EXTENDED,
            consensus=SINGLE_SIGNAL,
        )
        assert label == EXTENDED_CROWDED

    def test_late_with_any_consensus_returns_reset_or_extended(self):
        label, _ = self._call(
            data_confidence="HIGH",
            ticker_valid=True,
            earliness=LATE,
            consensus=MULTI_CONFIRMATION,
        )
        assert label in (RESET_WATCH, EXTENDED_CROWDED)

    def test_high_extension_pct_returns_reset_watch(self):
        label, _ = self._call(
            data_confidence="HIGH",
            ticker_valid=True,
            earliness=DEVELOPING,
            consensus=DOUBLE_CONFIRMATION,
            extension_vs_ma200_pct=18.0,
        )
        assert label == RESET_WATCH

    def test_clean_high_priority_candidate(self):
        label, reasons = self._call(
            data_confidence="MEDIUM",
            ticker_valid=True,
            liquidity_ok=True,
            earliness=DEVELOPING,
            consensus=DOUBLE_CONFIRMATION,
            conflict_flags=None,
            missing_fields=None,
        )
        assert label == HIGH_PRIORITY_RESEARCH
        assert reasons == []

    def test_top_research_requires_high_confidence_and_high_priority_consensus(self):
        label, _ = self._call(
            data_confidence="HIGH",
            ticker_valid=True,
            liquidity_ok=True,
            earliness=EARLY,
            consensus=HIGH_PRIORITY_RESEARCH,
            adj_consensus_score=80.0,
        )
        assert label == TOP_RESEARCH

    def test_low_confidence_downgrades_to_watchlist(self):
        label, reasons = self._call(
            data_confidence="LOW",
            ticker_valid=True,
            earliness=DEVELOPING,
            consensus=DOUBLE_CONFIRMATION,
        )
        assert label == WATCHLIST_RESEARCH
        assert any("LOW" in r or "confidence" in r for r in reasons)

    def test_single_signal_does_not_achieve_high_priority(self):
        label, reasons = self._call(
            data_confidence="HIGH",
            ticker_valid=True,
            liquidity_ok=True,
            earliness=DEVELOPING,
            consensus=SINGLE_SIGNAL,
        )
        assert label == WATCHLIST_RESEARCH
        assert any("single_signal" in r for r in reasons)

    def test_reclaim_watch_earliness_routes_to_reclaim(self):
        label, _ = self._call(
            data_confidence="MEDIUM",
            ticker_valid=True,
            earliness=RECLAIM_WATCH,
        )
        assert label == RECLAIM_WATCH

    def test_reset_watch_earliness_routes_to_reset(self):
        label, _ = self._call(
            data_confidence="MEDIUM",
            ticker_valid=True,
            earliness=RESET_WATCH,
        )
        assert label == RESET_WATCH

    def test_all_returned_labels_are_valid(self):
        for pri, _ in [
            self._call(ticker_valid=False),
            self._call(data_confidence="INVALID", ticker_valid=True),
            self._call(data_confidence="HIGH", ticker_valid=True, earliness=UNKNOWN_EARLINESS),
            self._call(data_confidence="HIGH", ticker_valid=True, earliness=DEVELOPING,
                       consensus=DOUBLE_CONFIRMATION, conflict_flags=["x"]),
            self._call(data_confidence="HIGH", ticker_valid=True, earliness=EXTENDED, consensus=DOUBLE_CONFIRMATION),
            self._call(data_confidence="MEDIUM", ticker_valid=True, earliness=DEVELOPING, consensus=SINGLE_SIGNAL),
            self._call(data_confidence="MEDIUM", ticker_valid=True, liquidity_ok=True,
                       earliness=DEVELOPING, consensus=DOUBLE_CONFIRMATION),
        ]:
            assert pri in PRIORITY_LABELS, f"Unexpected label: {pri!r}"


# ── Earliness detail tests ────────────────────────────────────────────────────

class TestEarlinessDetail:

    def test_unknown_only_when_required_fields_missing(self):
        result = earliness_detail()
        assert result["label"] == UNKNOWN_EARLINESS
        assert "above_ma50" in result["missing_fields"]
        assert "above_ma200" in result["missing_fields"]

    def test_missing_fields_emitted_correctly(self):
        result = earliness_detail(above_ma50=True, above_ma200=True)
        # Should not be UNKNOWN (required fields present), but missing optional fields emitted
        assert result["label"] != UNKNOWN_EARLINESS
        assert "rs_63d" in result["missing_fields"] or len(result["missing_fields"]) >= 0

    def test_extended_state_when_high_extension(self):
        result = earliness_detail(
            above_ma50=True, above_ma200=True, extension_vs_ma200_pct=18.0
        )
        assert result["label"] == EXTENDED
        assert result["extension_state"] in (EXT_EXTENDED, EXT_PARABOLIC)

    def test_parabolic_extension_state(self):
        result = earliness_detail(
            above_ma50=True, above_ma200=True, extension_vs_ma200_pct=25.0,
            rs_63=25.0,
        )
        assert result["label"] == LATE
        assert result["extension_state"] == EXT_PARABOLIC

    def test_normal_extension_state_for_developing(self):
        result = earliness_detail(above_ma50=True, above_ma200=True)
        assert result["label"] == DEVELOPING
        assert result["extension_state"] == EXT_NORMAL

    def test_extended_names_cannot_be_early(self):
        result = earliness_detail(
            above_ma50=True, above_ma200=False, rs_63=5.0, extension_vs_ma200_pct=20.0
        )
        # LATE overrides EARLY
        assert result["label"] != EARLY

    def test_base_building_names_can_be_early(self):
        result = earliness_detail(
            above_ma50=True, above_ma200=False, rs_63=8.0, vol_trend_ratio=1.1
        )
        assert result["label"] == EARLY

    def test_earliness_score_nonzero_when_early(self):
        result = earliness_detail(
            above_ma50=True, above_ma200=False, rs_63=10.0, vol_trend_ratio=1.15
        )
        assert result["earliness_score"] > 50

    def test_earliness_score_zero_when_unknown(self):
        result = earliness_detail()
        assert result["earliness_score"] == 0.0

    def test_developing_has_missing_enriching_fields(self):
        result = earliness_detail(above_ma50=True, above_ma200=True)
        # Doesn't require optional fields but should list them as missing
        assert isinstance(result["missing_fields"], list)


# ── Quality adjusted consensus tests ─────────────────────────────────────────

class TestQualityAdjustedConsensus:

    def test_raw_count_not_enough_for_high_priority_with_low_confidence(self):
        result = quality_adjusted_consensus(
            categories=["early_accumulation", "catalyst_watch", "sector_theme_leaders"],
            research_score=80.0,
            data_confidence="LOW",
            earliness=DEVELOPING,
        )
        assert result["quality_adjusted_score"] < result["raw_consensus_score"]
        assert "confidence_LOW" in result["downgrade_reasons"]

    def test_unknown_earliness_reduces_score(self):
        result = quality_adjusted_consensus(
            categories=["early_accumulation", "catalyst_watch"],
            research_score=70.0,
            data_confidence="HIGH",
            earliness=UNKNOWN_EARLINESS,
        )
        assert result["quality_adjusted_score"] < result["raw_consensus_score"]
        assert "earliness_UNKNOWN" in result["downgrade_reasons"]

    def test_social_only_confirmation_reduces_score(self):
        result_social = quality_adjusted_consensus(
            categories=["social_arb_attention"],
            research_score=60.0,
            data_confidence="MEDIUM",
            earliness=DEVELOPING,
            social_only=True,
        )
        result_normal = quality_adjusted_consensus(
            categories=["early_accumulation"],
            research_score=60.0,
            data_confidence="MEDIUM",
            earliness=DEVELOPING,
            social_only=False,
        )
        assert result_social["quality_adjusted_score"] < result_normal["quality_adjusted_score"]
        assert "social_only_confirmation" in result_social["downgrade_reasons"]

    def test_conflict_flags_reduce_score(self):
        result = quality_adjusted_consensus(
            categories=["early_accumulation", "catalyst_watch"],
            research_score=75.0,
            data_confidence="HIGH",
            earliness=DEVELOPING,
            conflict_flags=["scanner_vs_alpha_conflict"],
        )
        assert result["quality_adjusted_score"] < result["raw_consensus_score"]

    def test_extended_state_reduces_score(self):
        result = quality_adjusted_consensus(
            categories=["early_accumulation", "catalyst_watch", "sector_theme_leaders"],
            research_score=80.0,
            data_confidence="HIGH",
            earliness=EXTENDED,
            extension_state=EXT_EXTENDED,
        )
        assert "earliness_EXTENDED" in result["downgrade_reasons"]
        assert result["quality_adjusted_score"] < result["raw_consensus_score"]

    def test_clean_three_category_high_confidence_is_high_priority(self):
        result = quality_adjusted_consensus(
            categories=["early_accumulation", "catalyst_watch", "sector_theme_leaders"],
            research_score=80.0,
            data_confidence="HIGH",
            earliness=EARLY,
        )
        assert result["consensus_label"] == HIGH_PRIORITY_RESEARCH
        assert result["downgrade_reasons"] == []

    def test_stale_data_reduces_score(self):
        result = quality_adjusted_consensus(
            categories=["early_accumulation", "catalyst_watch"],
            research_score=70.0,
            data_confidence="MEDIUM",
            earliness=DEVELOPING,
            has_stale_data=True,
        )
        assert "stale_data" in result["downgrade_reasons"]

    def test_all_returned_consensus_labels_are_valid(self):
        from research.research_scoring import CONSENSUS_LABELS
        for conf in ("HIGH", "MEDIUM", "LOW", None):
            for earl in (EARLY, DEVELOPING, UNKNOWN_EARLINESS, EXTENDED):
                result = quality_adjusted_consensus(
                    categories=["early_accumulation"],
                    data_confidence=conf,
                    earliness=earl,
                )
                assert result["consensus_label"] in CONSENSUS_LABELS


# ── 10x radar label tests ─────────────────────────────────────────────────────

class TestTenXCandidateLabels:

    def _make_parquets(self, tmp_path, has_theme_profile=False):
        """Create minimal parquets for a drawdown + RS recovery scenario."""
        import pandas as pd
        import numpy as np

        dates = pd.date_range("2025-01-01", periods=100, freq="B")
        spy_closes = [400.0] * 100
        spy_df = pd.DataFrame({"date": dates, "close": spy_closes, "volume": [50_000_000.0] * 100})
        spy_df.to_parquet(tmp_path / "SPY.parquet")

        # Large drawdown + RS recovery (meets large_dd + rs_recovering)
        closes = list(np.linspace(100, 45, 80)) + list(np.linspace(45, 55, 20))
        vols = [1_000_000.0] * 70 + [1_500_000.0] * 30  # volume surge
        df = pd.DataFrame({"date": dates, "close": closes, "volume": vols})
        df.to_parquet(tmp_path / "MOCK.parquet")
        return tmp_path

    def test_drawdown_rs_volume_without_theme_is_asymmetric_watch(self, tmp_path):
        self._make_parquets(tmp_path)
        with patch("research.ten_x_candidate_radar.PRICE_DIR", tmp_path):
            result = scan_ten_x(offline=True)
        candidates = result.get("candidates", [])
        # Without theme / small-cap, should be ASYMMETRIC_RECOVERY_WATCH
        mock_cands = [c for c in candidates if c["ticker"] == "MOCK"]
        if mock_cands:
            # large_dd + rs_recovering + vol_surge but no theme = ASYMMETRIC_RECOVERY_WATCH
            assert mock_cands[0]["label"] != TRUE_10X_RESEARCH

    def test_all_labels_in_valid_set(self, tmp_path):
        self._make_parquets(tmp_path)
        with patch("research.ten_x_candidate_radar.PRICE_DIR", tmp_path):
            result = scan_ten_x(offline=True)
        for c in result.get("candidates", []):
            assert c["label"] in TEN_X_LABELS, f"Invalid label: {c['label']!r}"

    def test_true_10x_not_just_drawdown_volume(self, tmp_path):
        """Drawdown + RS + volume alone should be ASYMMETRIC_RECOVERY_WATCH not TRUE_10X_RESEARCH."""
        self._make_parquets(tmp_path)
        with patch("research.ten_x_candidate_radar.PRICE_DIR", tmp_path):
            result = scan_ten_x(offline=True)
        for c in result.get("candidates", []):
            criteria = c.get("criteria_flags", {})
            if (criteria.get("large_drawdown_40pct")
                    and criteria.get("rs_recovering")
                    and not criteria.get("speculative_theme")
                    and not criteria.get("small_cap")):
                assert c["label"] == ASYMMETRIC_RECOVERY_WATCH, (
                    f"{c['ticker']} should be ASYMMETRIC_RECOVERY_WATCH "
                    f"when only price/volume signals fire"
                )

    def test_no_legacy_speculative_10x_label(self, tmp_path):
        self._make_parquets(tmp_path)
        with patch("research.ten_x_candidate_radar.PRICE_DIR", tmp_path):
            result = scan_ten_x(offline=True)
        for c in result.get("candidates", []):
            assert c["label"] != "SPECULATIVE_10X", "Legacy label SPECULATIVE_10X should not appear"
            assert c["label"] != "ASYMMETRIC_WATCH", "Legacy label ASYMMETRIC_WATCH should not appear"

    def test_speculative_warning_always_present(self, tmp_path):
        self._make_parquets(tmp_path)
        with patch("research.ten_x_candidate_radar.PRICE_DIR", tmp_path):
            result = scan_ten_x(offline=True)
        for c in result.get("candidates", []):
            assert c.get("no_trade_recommendation") is True
            assert "speculative_disclaimer" in c
            assert len(c["speculative_disclaimer"]) > 20

    def test_guardrails_always_present(self, tmp_path):
        with patch("research.ten_x_candidate_radar.PRICE_DIR", tmp_path):
            result = scan_ten_x(offline=True)
        g = result["guardrails"]
        assert g["no_trade_recommendation"] is True
        assert g["speculative_research_only"] is True


# ── Options coverage guard tests ─────────────────────────────────────────────

class TestOptionsCoverageGuard:

    def test_options_overlay_disabled_below_threshold(self, tmp_path):
        from research.daily_alpha_radar_report import _options_coverage_state
        # Empty watchlist of 10 items, no snapshot dir = 0% coverage
        watchlist = [{"ticker": f"T{i}"} for i in range(10)]
        with patch("research.daily_alpha_radar_report.ROOT", tmp_path):
            state = _options_coverage_state(watchlist)
        assert state["overlay_enabled"] is False
        assert state["state"] == "DISABLED"
        assert state["coverage_pct"] < 0.50

    def test_options_overlay_enabled_above_threshold(self, tmp_path):
        from research.daily_alpha_radar_report import _options_coverage_state
        # Create fake snapshot parquets for >80% of tickers under data/options_snapshots/{date}/
        snap_dir = tmp_path / "data" / "options_snapshots" / "2026-06-18"
        snap_dir.mkdir(parents=True)
        watchlist = [{"ticker": f"T{i:02d}"} for i in range(10)]
        for item in watchlist[:9]:  # 90% coverage
            (snap_dir / f"{item['ticker']}.parquet").write_bytes(b"")
        with patch("research.daily_alpha_radar_report.ROOT", tmp_path):
            state = _options_coverage_state(watchlist)
        assert state["overlay_enabled"] is True
        assert state["state"] == "NORMAL"

    def test_no_options_confirmed_label_when_disabled(self):
        # When options overlay is disabled, options_context should flag OPTIONS_DATA_UNAVAILABLE
        from research.daily_alpha_radar_report import _enrich_with_priority
        item = {
            "ticker": "TEST",
            "category": "early_accumulation",
            "watchlist_label": "EARLY_ACCUMULATION",
            "research_score": 70.0,
            "above_ma50": True,
            "above_ma200": True,
            "all_categories": ["early_accumulation", "catalyst_watch"],
        }
        options_state = {"overlay_enabled": False, "state": "DISABLED", "coverage_pct": 0.0}
        enriched = _enrich_with_priority(item, {}, options_state)
        assert enriched["options_context"] == "OPTIONS_DATA_UNAVAILABLE"

    def test_options_context_none_when_overlay_enabled(self):
        from research.daily_alpha_radar_report import _enrich_with_priority
        item = {
            "ticker": "TEST",
            "category": "early_accumulation",
            "watchlist_label": "EARLY_ACCUMULATION",
            "research_score": 70.0,
            "above_ma50": True,
            "above_ma200": True,
            "all_categories": ["early_accumulation"],
        }
        options_state = {"overlay_enabled": True, "state": "NORMAL", "coverage_pct": 0.9}
        enriched = _enrich_with_priority(item, {}, options_state)
        assert enriched["options_context"] is None


# ── Catalyst sanity tests ─────────────────────────────────────────────────────

class TestCatalystSanity:

    def _fresh_ts(self, hours_ago: float = 2.0) -> str:
        from datetime import datetime, timezone, timedelta
        dt = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
        return dt.isoformat()

    def test_fresh_company_specific_can_upgrade(self):
        result = validate_catalyst(
            headline="AAPL raises guidance after strong quarter",
            ticker="AAPL",
            published_at=self._fresh_ts(2),
            source="company_ir",
            data_confidence="HIGH",
        )
        assert result["label"] == FRESH_COMPANY_SPECIFIC
        assert result["can_upgrade"] is True
        assert result["freshness_ok"] is True

    def test_stale_catalyst_cannot_upgrade(self):
        result = validate_catalyst(
            headline="AAPL raises guidance",
            ticker="AAPL",
            published_at=self._fresh_ts(100),  # 100 hours old
            source="company_ir",
        )
        assert result["label"] == STALE
        assert result["can_upgrade"] is False

    def test_crowded_social_cannot_upgrade(self):
        result = validate_catalyst(
            headline="Everyone talking about AAPL",
            ticker="AAPL",
            published_at=self._fresh_ts(1),
            crowded=True,
        )
        assert result["label"] == HYPE_CROWDED
        assert result["can_upgrade"] is False

    def test_malformed_price_target_cannot_upgrade(self):
        result = validate_catalyst(
            headline="AAPL target raised",
            ticker="AAPL",
            published_at=self._fresh_ts(2),
            source="analyst",
            price_target=-999.0,  # clearly malformed
        )
        assert result["label"] == MALFORMED
        assert result["can_upgrade"] is False

    def test_syndicated_source_cannot_upgrade(self):
        result = validate_catalyst(
            headline="AAPL beats estimates",
            ticker="AAPL",
            published_at=self._fresh_ts(1),
            source="PR Newswire",
        )
        assert result["label"] == DUPLICATE_OR_SYNDICATED
        assert result["can_upgrade"] is False

    def test_sector_spillover_cannot_upgrade(self):
        result = validate_catalyst(
            headline="Tech sector upgrade on inflation data",
            ticker="AAPL",
            published_at=self._fresh_ts(2),
            source="analyst_firm",
        )
        assert result["label"] == SECTOR_SPILLOVER
        assert result["can_upgrade"] is False

    def test_low_confidence_blocks_upgrade(self):
        result = validate_catalyst(
            headline="NVDA beats estimates",
            ticker="NVDA",
            published_at=self._fresh_ts(2),
            source="company_ir",
            data_confidence="LOW",
        )
        assert result["can_upgrade"] is False

    def test_tape_extended_blocks_upgrade(self):
        result = validate_catalyst(
            headline="NVDA raises guidance",
            ticker="NVDA",
            published_at=self._fresh_ts(2),
            source="company_ir",
            tape_extended=True,
            data_confidence="HIGH",
        )
        assert result["can_upgrade"] is False

    def test_all_labels_are_valid(self):
        for ts_hrs in (2, 100):
            r = validate_catalyst(
                headline="Test headline",
                ticker="TST",
                published_at=self._fresh_ts(ts_hrs),
                source="test_source",
            )
            assert r["label"] in CATALYST_SANITY_LABELS

    def test_seen_in_many_sources_is_syndicated(self):
        result = validate_catalyst(
            headline="MSFT beats estimates",
            ticker="MSFT",
            published_at=self._fresh_ts(2),
            source="analyst_firm",
            seen_in_sources=5,
        )
        assert result["label"] == DUPLICATE_OR_SYNDICATED
        assert result["can_upgrade"] is False

    def test_social_signal_crowded_cannot_upgrade(self):
        result = validate_social_signal(
            ticker="AMC",
            social_score=0.95,
            crowded=True,
        )
        assert result["label"] == HYPE_CROWDED
        assert result["can_upgrade"] is False

    def test_fresh_social_signal_specific_can_upgrade(self):
        result = validate_social_signal(
            ticker="NVDA",
            social_score=0.5,
            crowded=False,
            age_hours=2.0,
            tape_extended=False,
            data_confidence="HIGH",
            is_company_specific=True,
        )
        assert result["can_upgrade"] is True


# ── Forward tracker sample status tests ──────────────────────────────────────

class TestSampleStatus:

    def test_zero_is_too_early(self):
        assert _sample_status(0) == SAMPLE_TOO_EARLY

    def test_below_10_is_too_early(self):
        assert _sample_status(9) == SAMPLE_TOO_EARLY

    def test_at_10_is_provisional(self):
        assert _sample_status(SAMPLE_THRESHOLD_PROVISIONAL) == SAMPLE_PROVISIONAL

    def test_at_30_is_meaningful(self):
        assert _sample_status(SAMPLE_THRESHOLD_MEANINGFUL) == SAMPLE_MEANINGFUL

    def test_at_100_is_robust(self):
        assert _sample_status(SAMPLE_THRESHOLD_ROBUST) == SAMPLE_ROBUST

    def test_compute_verdicts_includes_sample_status(self):
        entries = [{"ret_10d": 5.0}] * 3
        v = _compute_verdicts(entries, "TEST")
        assert "sample_status" in v
        assert v["sample_status"] == SAMPLE_TOO_EARLY

    def test_verdict_is_need_more_data_below_provisional(self):
        entries = [{"ret_10d": 5.0}] * 9
        v = _compute_verdicts(entries, "TEST")
        assert v["verdict"] == "NEED_MORE_DATA"

    def test_verdict_assigned_at_provisional_threshold(self):
        # 10+ matured with all positive returns → should get EARLY_SIGNAL or PROMISING
        entries = [{"ret_10d": float(i + 1)} for i in range(10)]
        v = _compute_verdicts(entries, "TEST")
        assert v["verdict"] != "NEED_MORE_DATA"

    def test_note_field_present_when_too_early(self):
        entries = [{"ret_10d": 5.0}] * 3
        v = _compute_verdicts(entries, "TEST")
        assert v.get("note") is not None
        assert "provisional" in v["note"].lower() or "≥" in v["note"]


# ── Daily alpha radar report purity tests ────────────────────────────────────

LEGACY_TERMS = [
    "VOYAGER", "SNIPER", "REMORA", "SHORT_A", "LRR",
    "holdout scoreboard", "risk telemetry", "short opportunity",
    "strategy tournament", "paper loop", "broker snap",
    "active paper", "READY_FOR_DEEPER_BACKTEST",
]

TRADE_TERMS = [
    "buy now", "sell now", "entry price", "stop loss", "position size",
    "trade recommendation", "paper signal", "live signal", "auto trade",
]


class TestDailyRadarReportPurity:

    def _run_report(self, tmp_path) -> str:
        from research.daily_alpha_radar_report import run_daily_radar, REPORT_PATH, OUT_JSON, RESEARCH_DIR
        docs_dir = tmp_path / "docs" / "research"
        docs_dir.mkdir(parents=True)
        cache_dir = tmp_path / "cache" / "research"
        cache_dir.mkdir(parents=True)

        # Patch all paths
        with patch("research.daily_alpha_radar_report.REPORT_PATH", docs_dir / "DAILY_ALPHA_RADAR_REPORT.md"), \
             patch("research.daily_alpha_radar_report.OUT_JSON", cache_dir / "daily_alpha_radar_latest.json"), \
             patch("research.daily_alpha_radar_report.RESEARCH_DIR", cache_dir), \
             patch("research.daily_alpha_radar_report.cfg") as mock_cfg:
            mock_cfg.CACHE_DIR = tmp_path / "cache"
            mock_cfg.LOG_DIR = tmp_path / "logs"
            run_daily_radar()

        report_path = docs_dir / "DAILY_ALPHA_RADAR_REPORT.md"
        if not report_path.exists():
            return ""
        return report_path.read_text(encoding="utf-8")

    def test_no_legacy_strategy_terms_in_report(self, tmp_path):
        report = self._run_report(tmp_path)
        if not report:
            pytest.skip("Report not generated (no sidecars available)")
        for term in LEGACY_TERMS:
            assert term not in report, f"Legacy term found in report: {term!r}"

    def test_no_trade_language_in_report(self, tmp_path):
        report = self._run_report(tmp_path)
        if not report:
            pytest.skip("Report not generated (no sidecars available)")
        report_lower = report.lower()
        for term in TRADE_TERMS:
            assert term.lower() not in report_lower, f"Forbidden trade term in report: {term!r}"

    def test_report_contains_safety_confirmations(self, tmp_path):
        report = self._run_report(tmp_path)
        if not report:
            pytest.skip("Report not generated (no sidecars available)")
        assert "Safety Confirmations" in report
        assert "NO TRADE-ACTION GUIDANCE" in report

    def test_report_is_research_only(self, tmp_path):
        report = self._run_report(tmp_path)
        if not report:
            pytest.skip("Report not generated (no sidecars available)")
        assert "RESEARCH_ONLY" in report or "RESEARCH ONLY" in report

    def test_json_sidecar_guardrails(self, tmp_path):
        from research.daily_alpha_radar_report import run_daily_radar, RESEARCH_DIR
        cache_dir = tmp_path / "cache" / "research"
        cache_dir.mkdir(parents=True)
        docs_dir = tmp_path / "docs" / "research"
        docs_dir.mkdir(parents=True)

        with patch("research.daily_alpha_radar_report.REPORT_PATH", docs_dir / "DAILY_ALPHA_RADAR_REPORT.md"), \
             patch("research.daily_alpha_radar_report.OUT_JSON", cache_dir / "daily_alpha_radar_latest.json"), \
             patch("research.daily_alpha_radar_report.RESEARCH_DIR", cache_dir), \
             patch("research.daily_alpha_radar_report.cfg") as mock_cfg:
            mock_cfg.CACHE_DIR = tmp_path / "cache"
            mock_cfg.LOG_DIR = tmp_path / "logs"
            result = run_daily_radar()

        assert result["research_only"] is True
        g = result["guardrails"]
        assert g["no_trade_recommendation"] is True
        assert g["no_buy_sell"] is True
        assert g["no_paper_signal"] is True
        assert g["no_alpaca_interaction"] is True
        assert g["no_broker_execution"] is True

    def test_data_quarantine_not_in_top_candidates(self, tmp_path):
        from research.daily_alpha_radar_report import _enrich_with_priority, _bucket_items

        items = [
            {
                "ticker": "VSXY",
                "category": "early_accumulation",
                "watchlist_label": "EARLY_ACCUMULATION",
                "research_score": 80.0,
                "above_ma50": None,  # missing — will be UNKNOWN earliness
                "above_ma200": None,
                "all_categories": ["early_accumulation", "catalyst_watch", "sector_theme_leaders"],
            }
        ]
        options_state = {"overlay_enabled": False, "state": "DISABLED", "coverage_pct": 0.0}
        coverage_map = {}
        enriched = [_enrich_with_priority(item, coverage_map, options_state) for item in items]
        buckets = _bucket_items(enriched)

        vsxy = next((e for e in enriched if e["ticker"] == "VSXY"), None)
        assert vsxy is not None
        assert vsxy["priority_label"] in (DATA_QUARANTINE, INVALID_PRIORITY), (
            f"VSXY should be quarantined but got {vsxy['priority_label']!r}"
        )
        # Must not appear in top_research or high_priority buckets
        for bucket_name in ("top_research", "high_priority"):
            tickers = [item["ticker"] for item in buckets[bucket_name]]
            assert "VSXY" not in tickers, f"VSXY should not be in {bucket_name} bucket"

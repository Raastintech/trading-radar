"""
tests/unit/test_phase4a4_catalyst_sanity.py — Phase 4A.4 catalyst/social sanity tests.

Covers:
  - _apply_catalyst_sanity() for social_arb_attention items
  - _apply_catalyst_sanity() for catalyst_watch items (earnings / analyst / no-event)
  - Extension and low-confidence gates in catalyst sanity
  - consensus filtering in _enrich_with_priority() for failed catalyst/social
  - conflict flag generation for purely social/catalyst items that fail sanity
  - Legacy diagnostics NOT in cmd_risk_telemetry (script content checks)
  - legacy-diagnostics and legacy-decision-policy subcommands present in dispatcher
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch, MagicMock

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os_env_patch = {"GEM_TRADER_SKIP_DOTENV": "true"}


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_parquet(price_dir: Path, ticker: str, n: int, price: float = 100.0, volume: float = 1_000_000) -> None:
    import numpy as np
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    prices = [price] * n
    vols = [volume] * n
    df = pd.DataFrame({"close": prices, "volume": vols}, index=dates)
    price_dir.mkdir(parents=True, exist_ok=True)
    df.to_parquet(price_dir / f"{ticker}.parquet")


def _spy_closes(n: int, price: float = 500.0) -> List[float]:
    return [price] * n


def _social_item(crowded: bool = False, score: float = 0.7,
                 ext_state: str = "NORMAL", confidence: str = "MEDIUM") -> Dict[str, Any]:
    return {
        "ticker": "TEST",
        "category": "social_arb_attention",
        "watchlist_label": "SOCIAL_ARB",
        "research_score": 70.0,
        "social_score": score,
        "crowded": crowded,
        "social_source": "stocktwits",
        "extension_state": ext_state,
        "data_confidence": confidence,
        "all_categories": ["social_arb_attention"],
    }


def _catalyst_item(days: int = 5, extended: bool = False,
                   has_analyst: bool = True, ext_state: str = "NORMAL",
                   confidence: str = "MEDIUM") -> Dict[str, Any]:
    return {
        "ticker": "TEST",
        "category": "catalyst_watch",
        "watchlist_label": "RISKY" if extended else "CATALYST",
        "research_score": 70.0,
        "days_to_earnings": days,
        "has_analyst_upgrade": has_analyst,
        "analyst_action": "Buy" if has_analyst else None,
        "extended_into_earnings": extended,
        "extension_state": ext_state,
        "data_confidence": confidence,
        "all_categories": ["catalyst_watch"],
    }


# ── Import the scanner function under test ────────────────────────────────────

import os
with patch.dict(os.environ, os_env_patch):
    from research.research_scanner import _apply_catalyst_sanity
    from research.catalyst_sanity import (
        FRESH_COMPANY_SPECIFIC,
        HYPE_CROWDED,
        NEEDS_MANUAL_SOURCE_CHECK,
        STALE,
    )


# ── Social item tests ─────────────────────────────────────────────────────────

class TestSocialCatalystSanity(unittest.TestCase):
    """_apply_catalyst_sanity for social_arb_attention items."""

    def test_clean_social_signal_can_upgrade(self):
        item = _social_item(crowded=False, score=0.7, ext_state="NORMAL", confidence="MEDIUM")
        result = _apply_catalyst_sanity(item)
        self.assertIn("catalyst_sanity_label", result)
        self.assertIn("catalyst_can_upgrade", result)
        # Non-crowded, non-extended, reasonable confidence → can_upgrade
        self.assertTrue(result["catalyst_can_upgrade"])
        self.assertEqual(result["catalyst_sanity_label"], FRESH_COMPANY_SPECIFIC)

    def test_crowded_social_signal_blocked(self):
        item = _social_item(crowded=True, score=0.7)
        result = _apply_catalyst_sanity(item)
        self.assertFalse(result["catalyst_can_upgrade"])
        self.assertEqual(result["catalyst_sanity_label"], HYPE_CROWDED)

    def test_high_social_score_crowded_blocks(self):
        item = _social_item(crowded=False, score=0.9)  # > 0.85 threshold
        result = _apply_catalyst_sanity(item)
        self.assertFalse(result["catalyst_can_upgrade"])
        self.assertEqual(result["catalyst_sanity_label"], HYPE_CROWDED)

    def test_extended_tape_blocks_social(self):
        item = _social_item(crowded=False, score=0.5, ext_state="PARABOLIC")
        result = _apply_catalyst_sanity(item)
        self.assertFalse(result["catalyst_can_upgrade"])

    def test_low_confidence_blocks_social(self):
        item = _social_item(crowded=False, score=0.5, confidence="LOW")
        result = _apply_catalyst_sanity(item)
        self.assertFalse(result["catalyst_can_upgrade"])

    def test_invalid_confidence_blocks_social(self):
        item = _social_item(crowded=False, score=0.5, confidence="INVALID")
        result = _apply_catalyst_sanity(item)
        self.assertFalse(result["catalyst_can_upgrade"])

    def test_sanity_issues_list_populated(self):
        item = _social_item(crowded=True, score=0.9)
        result = _apply_catalyst_sanity(item)
        self.assertIsInstance(result["catalyst_sanity_issues"], list)
        self.assertTrue(len(result["catalyst_sanity_issues"]) > 0)

    def test_non_social_non_catalyst_item_unchanged(self):
        item = {
            "ticker": "TEST",
            "category": "early_accumulation",
            "all_categories": ["early_accumulation"],
        }
        result = _apply_catalyst_sanity(item)
        self.assertNotIn("catalyst_sanity_label", result)
        self.assertNotIn("catalyst_can_upgrade", result)


# ── Catalyst item tests ───────────────────────────────────────────────────────

class TestCatalystWatchSanity(unittest.TestCase):
    """_apply_catalyst_sanity for catalyst_watch items."""

    def test_imminent_earnings_can_upgrade(self):
        item = _catalyst_item(days=3, extended=False, ext_state="NORMAL", confidence="MEDIUM")
        result = _apply_catalyst_sanity(item)
        self.assertTrue(result["catalyst_can_upgrade"])
        self.assertEqual(result["catalyst_sanity_label"], FRESH_COMPANY_SPECIFIC)

    def test_earnings_same_day_can_upgrade(self):
        item = _catalyst_item(days=0, extended=False)
        result = _apply_catalyst_sanity(item)
        self.assertTrue(result["catalyst_can_upgrade"])
        self.assertEqual(result["catalyst_sanity_label"], FRESH_COMPANY_SPECIFIC)

    def test_earnings_21_days_out_can_upgrade(self):
        item = _catalyst_item(days=21, extended=False)
        result = _apply_catalyst_sanity(item)
        self.assertTrue(result["catalyst_can_upgrade"])

    def test_earnings_22_days_out_needs_manual(self):
        item = _catalyst_item(days=22, extended=False)
        result = _apply_catalyst_sanity(item)
        self.assertFalse(result["catalyst_can_upgrade"])
        self.assertEqual(result["catalyst_sanity_label"], NEEDS_MANUAL_SOURCE_CHECK)

    def test_extended_into_earnings_blocked(self):
        item = _catalyst_item(days=3, extended=True)
        result = _apply_catalyst_sanity(item)
        self.assertFalse(result["catalyst_can_upgrade"])
        self.assertEqual(result["catalyst_sanity_label"], HYPE_CROWDED)
        self.assertIn("extended_into_earnings", result["catalyst_sanity_issues"])

    def test_analyst_only_no_earnings_needs_manual(self):
        item = _catalyst_item(days=None, extended=False, has_analyst=True)
        result = _apply_catalyst_sanity(item)
        self.assertFalse(result["catalyst_can_upgrade"])
        self.assertEqual(result["catalyst_sanity_label"], NEEDS_MANUAL_SOURCE_CHECK)
        self.assertIn("no_source_date_for_analyst_action", result["catalyst_sanity_issues"])

    def test_parabolic_tape_blocks_catalyst(self):
        item = _catalyst_item(days=5, extended=False, ext_state="PARABOLIC")
        result = _apply_catalyst_sanity(item)
        self.assertFalse(result["catalyst_can_upgrade"])
        self.assertIn("tape_extended", result["catalyst_sanity_issues"])

    def test_low_confidence_blocks_catalyst(self):
        item = _catalyst_item(days=5, extended=False, confidence="LOW")
        result = _apply_catalyst_sanity(item)
        self.assertFalse(result["catalyst_can_upgrade"])

    def test_no_event_at_all_needs_manual(self):
        item = {
            "ticker": "TEST",
            "category": "catalyst_watch",
            "days_to_earnings": None,
            "has_analyst_upgrade": False,
            "extended_into_earnings": False,
            "extension_state": "NORMAL",
            "data_confidence": "HIGH",
        }
        result = _apply_catalyst_sanity(item)
        self.assertFalse(result["catalyst_can_upgrade"])
        self.assertEqual(result["catalyst_sanity_label"], NEEDS_MANUAL_SOURCE_CHECK)
        self.assertIn("no_imminent_catalyst_event", result["catalyst_sanity_issues"])


# ── Priority consensus filtering tests ───────────────────────────────────────

class TestCatalystSanityInPriority(unittest.TestCase):
    """
    Tests that _enrich_with_priority() correctly filters unvalidated categories
    from consensus count and adds conflict flag for purely social/catalyst items.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        _make_parquet(self.tmp, "SPY", 250, price=500.0)
        _make_parquet(self.tmp, "TEST", 250, price=100.0)
        self.spy = _spy_closes(250)

    def _call_enrich(self, item: Dict[str, Any]) -> Dict[str, Any]:
        with patch.dict(os.environ, os_env_patch):
            from research.daily_alpha_radar_report import _enrich_with_priority
            # Provide a minimal options_state and coverage_map
            return _enrich_with_priority(item, coverage_map={}, options_state={})

    def _enriched_item(self, categories: List[str], catalyst_can_upgrade: bool,
                       primary_category: str) -> Dict[str, Any]:
        """Build a minimally enriched item with needed fields."""
        from research.research_candidate_enrichment import enrich_research_candidate
        base = {
            "ticker": "TEST",
            "category": primary_category,
            "watchlist_label": "SOCIAL_ARB",
            "research_score": 75.0,
            "all_categories": categories,
            "trust_level": "high",
            "data_source": "test",
            "why_appeared": "test",
            "confirms_if": "test",
            "invalidates_if": "test",
            "no_trade_recommendation": True,
            "catalyst_can_upgrade": catalyst_can_upgrade,
            "catalyst_sanity_label": HYPE_CROWDED if not catalyst_can_upgrade else FRESH_COMPANY_SPECIFIC,
            "catalyst_sanity_issues": ["test_issue"] if not catalyst_can_upgrade else [],
        }
        with patch.dict(os.environ, os_env_patch):
            return enrich_research_candidate(
                "TEST", base, self.tmp, self.spy
            )

    def test_failed_social_removed_from_effective_categories(self):
        """A social item that fails sanity should not count toward DOUBLE_CONFIRMATION."""
        # Two categories: sector_theme_leader + social_arb_attention, but social fails
        item = self._enriched_item(
            categories=["sector_theme_leader", "social_arb_attention"],
            catalyst_can_upgrade=False,
            primary_category="social_arb_attention",
        )
        enriched = self._call_enrich(item)
        # Social category filtered out → only sector_theme_leader remains → SINGLE_SIGNAL
        # (or at most quality-gated down from any double)
        # The consensus should NOT be DOUBLE_CONFIRMATION when social is invalid
        consensus = enriched.get("consensus_label", "")
        self.assertNotEqual(consensus, "DOUBLE_CONFIRMATION",
                            "Failed social signal should not inflate to DOUBLE_CONFIRMATION")

    def test_passing_social_counted_in_consensus(self):
        """A social item that passes sanity should contribute to consensus normally."""
        item = self._enriched_item(
            categories=["sector_theme_leader", "social_arb_attention"],
            catalyst_can_upgrade=True,
            primary_category="social_arb_attention",
        )
        enriched = self._call_enrich(item)
        # Both categories valid → should get at least DOUBLE_CONFIRMATION if scores allow
        consensus = enriched.get("consensus_label", "")
        qscore = enriched.get("quality_adjusted_consensus_score", 0)
        # With two categories and catalyst_can_upgrade=True, qscore should be higher
        self.assertGreater(qscore, 40,
                           "Passing social signal should keep quality-adjusted consensus score elevated")

    def test_conflict_flag_added_for_failed_primary_social(self):
        """Purely-social item that fails sanity should get catalyst_not_validated conflict flag."""
        item = self._enriched_item(
            categories=["social_arb_attention"],
            catalyst_can_upgrade=False,
            primary_category="social_arb_attention",
        )
        enriched = self._call_enrich(item)
        conflicts = enriched.get("conflict_flags", [])
        self.assertIn("catalyst_not_validated", conflicts,
                      "Primary social item that fails sanity must have catalyst_not_validated flag")

    def test_no_conflict_flag_when_catalyst_not_checked(self):
        """Items without catalyst fields (category not social/catalyst) should not get the flag."""
        item = self._enriched_item(
            categories=["early_accumulation"],
            catalyst_can_upgrade=True,  # doesn't matter — category doesn't trigger sanity
            primary_category="early_accumulation",
        )
        item.pop("catalyst_can_upgrade", None)  # simulate no sanity check run
        item.pop("catalyst_sanity_label", None)
        enriched = self._call_enrich(item)
        conflicts = enriched.get("conflict_flags", [])
        self.assertNotIn("catalyst_not_validated", conflicts)

    def test_catalyst_sanity_fields_surfaced_in_enriched_output(self):
        """catalyst_sanity_label and catalyst_can_upgrade must appear in enriched dict."""
        item = self._enriched_item(
            categories=["social_arb_attention"],
            catalyst_can_upgrade=False,
            primary_category="social_arb_attention",
        )
        enriched = self._call_enrich(item)
        self.assertIn("catalyst_sanity_label", enriched)
        self.assertIn("catalyst_can_upgrade", enriched)
        self.assertIn("catalyst_sanity_issues", enriched)


# ── Nightly script content checks ────────────────────────────────────────────

class TestLegacyDiagnosticsRemovedFromNightly(unittest.TestCase):
    """Verify the bash script no longer runs legacy diagnostics in cmd_risk_telemetry."""

    @classmethod
    def _read_script(cls) -> str:
        return (ROOT / "scripts" / "run_research_cycle.sh").read_text()

    def _extract_function_body(self, script: str, fn_name: str) -> str:
        """Naively extract the body of a bash function by name."""
        lines = script.splitlines()
        in_fn = False
        depth = 0
        body_lines: List[str] = []
        for line in lines:
            if f"{fn_name}()" in line and line.strip().endswith("{") or \
               (f"{fn_name}()" in line):
                # Check if the line has the opening brace on same or next line
                if f"{fn_name}()" in line:
                    in_fn = True
            if in_fn:
                body_lines.append(line)
                depth += line.count("{") - line.count("}")
                if depth == 0 and len(body_lines) > 1:
                    break
        return "\n".join(body_lines)

    def test_legacy_decision_policy_not_in_risk_telemetry(self):
        script = self._read_script()
        body = self._extract_function_body(script, "cmd_risk_telemetry")
        self.assertNotIn("legacy_decision_policy_report", body,
                         "legacy_decision_policy_report.py must not run in cmd_risk_telemetry")

    def test_short_opportunity_radar_not_in_risk_telemetry(self):
        script = self._read_script()
        body = self._extract_function_body(script, "cmd_risk_telemetry")
        self.assertNotIn("short_opportunity_radar", body,
                         "short_opportunity_radar.py must not run in cmd_risk_telemetry")

    def test_leader_reset_event_study_not_in_risk_telemetry(self):
        script = self._read_script()
        body = self._extract_function_body(script, "cmd_risk_telemetry")
        self.assertNotIn("leader_reset_event_study", body)

    def test_voyager_conversion_audit_not_in_risk_telemetry(self):
        script = self._read_script()
        body = self._extract_function_body(script, "cmd_risk_telemetry")
        self.assertNotIn("voyager_conversion_audit", body)

    def test_strategy_tournament_not_in_risk_telemetry(self):
        script = self._read_script()
        body = self._extract_function_body(script, "cmd_risk_telemetry")
        self.assertNotIn("strategy_tournament", body)

    def test_short_detection_audit_not_in_risk_telemetry(self):
        script = self._read_script()
        body = self._extract_function_body(script, "cmd_risk_telemetry")
        self.assertNotIn("short_detection_truth_audit", body)

    def test_forward_resolution_health_still_in_risk_telemetry(self):
        """forward_resolution_health is kept — it monitors all sleeves, not just shorts."""
        script = self._read_script()
        body = self._extract_function_body(script, "cmd_risk_telemetry")
        self.assertIn("forward_resolution_health", body,
                      "forward_resolution_health.py must remain in cmd_risk_telemetry")

    def test_legacy_diagnostics_command_exists(self):
        script = self._read_script()
        self.assertIn("cmd_legacy_diagnostics()", script,
                      "cmd_legacy_diagnostics function must be defined")
        self.assertIn("legacy-diagnostics)", script,
                      "legacy-diagnostics must be in the case dispatcher")

    def test_legacy_decision_policy_command_exists(self):
        script = self._read_script()
        self.assertIn("cmd_legacy_decision_policy()", script)
        self.assertIn("legacy-decision-policy)", script)

    def test_legacy_diagnostics_contains_removed_scripts(self):
        """The new cmd_legacy_diagnostics must contain all the scripts we removed."""
        script = self._read_script()
        body = self._extract_function_body(script, "cmd_legacy_diagnostics")
        for name in [
            "legacy_decision_policy_report",
            "short_opportunity_radar",
            "leader_reset_event_study",
            "voyager_conversion_audit",
            "strategy_tournament",
        ]:
            self.assertIn(name, body,
                          f"{name} must be in cmd_legacy_diagnostics")

    def test_four_operational_reports_still_in_risk_telemetry(self):
        """The 4 operational telemetry reports must remain in cmd_risk_telemetry."""
        script = self._read_script()
        body = self._extract_function_body(script, "cmd_risk_telemetry")
        for name in [
            "slippage_telemetry_report",
            "portfolio_concentration_report",
            "shadow_sizing_report",
            "paper_state_hygiene_report",
        ]:
            self.assertIn(name, body, f"{name} must remain in cmd_risk_telemetry")


# ── No forbidden trade language ───────────────────────────────────────────────

class TestPhase4A4Purity(unittest.TestCase):

    def _read(self, path: str) -> str:
        return (ROOT / path).read_text()

    def test_no_trade_language_in_scanner_new_function(self):
        src = self._read("research/research_scanner.py")
        # Find the _apply_catalyst_sanity function block
        start = src.find("def _apply_catalyst_sanity")
        end = src.find("\ndef ", start + 1)
        block = src[start:end] if end > start else src[start:]
        for term in ("buy now", "sell now", "entry price", "stop loss", "position size"):
            self.assertNotIn(term, block.lower())

    def test_catalyst_sanity_module_has_no_trade_language(self):
        src = self._read("research/catalyst_sanity.py")
        for term in ("buy now", "sell now", "entry price", "stop loss"):
            self.assertNotIn(term, src.lower())

    def test_apply_catalyst_sets_no_trade_recommendation_field(self):
        """_apply_catalyst_sanity must not add trade recommendation fields."""
        item = _social_item()
        with patch.dict(os.environ, os_env_patch):
            from research.research_scanner import _apply_catalyst_sanity as fn
            result = fn(item)
        for forbidden in ("entry", "stop_loss", "target_price", "position_size"):
            self.assertNotIn(forbidden, result)

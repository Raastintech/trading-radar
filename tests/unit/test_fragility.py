"""
tests/unit/test_fragility.py

Pins the Phase 1F fragility overlay contract: cross-artifact research
state derivation and label discipline. Display-only — these tests must
never touch DB, broker, or scoring code.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from core import fragility as fr  # noqa: E402


# ── Helpers to build canned cache shapes ────────────────────────────────────

def _forecast(
    *,
    regime: str = "Bull Continuation",
    confidence: str = "medium",
    breached: bool = False,
    breach_reasons=None,
    risk_off_p: float = 0.10,
    bull_p: float = 0.50,
):
    return {
        "headline": {
            "current_regime": regime,
            "confidence": confidence,
            "invalidation_breached": breached,
            "invalidation_breach_reasons": breach_reasons or [],
        },
        "regime_probabilities": [
            {"regime": regime, "probability": bull_p},
            {"regime": "Risk-Off", "probability": risk_off_p},
        ],
    }


def _lens(
    *,
    label: str = "Bullish",
    confidence: str = "medium",
    entry: str = "ok",
    options: str = "neutral",
):
    return {
        "label": label,
        "confidence": confidence,
        "layers": {
            "entry":   {"view": entry},
            "options": {"view": options},
            "sector":  {"view": "Bullish"},
        },
    }


class _PostureBullishLow:
    bias = "bullish"
    confidence = "low"


class _PostureBullishMed:
    bias = "bullish"
    confidence = "medium"


# ── Fragility-state matrix ──────────────────────────────────────────────────

class TestFragilityStates:

    def test_normal_when_all_aligned(self):
        r = fr.evaluate_fragility(
            forecast=_forecast(confidence="medium", breached=False),
            posture=_PostureBullishMed(),
            lens=_lens(entry="ok", options="neutral"),
        )
        assert r.status == "NORMAL"
        assert "standard playbook" in r.action_hint

    def test_unknown_when_forecast_missing(self):
        r = fr.evaluate_fragility(forecast=None)
        assert r.status == "UNKNOWN"
        assert "research artifact missing" in r.action_hint

    def test_conflicted_with_single_signal(self):
        """LOW confidence alone, no breach, no posture conflict → CONFLICTED."""
        r = fr.evaluate_fragility(
            forecast=_forecast(confidence="low", breached=False),
            posture=None,
            lens=None,
        )
        assert r.status == "CONFLICTED"
        assert any("LOW" in s for s in r.reasons)

    def test_fragile_when_two_or_more_signals(self):
        """Audit scenario: breached + LOW + posture-bullish-but-not-confirmed."""
        r = fr.evaluate_fragility(
            forecast=_forecast(
                confidence="low",
                breached=True,
                breach_reasons=["leading sectors 0 < 2"],
            ),
            posture=_PostureBullishLow(),
            lens=_lens(entry="too extended", options="bearish hedge"),
        )
        assert r.status == "FRAGILE", r.reasons
        # The headline reason set should mention the breach AND the
        # posture/regime disagreement at minimum.
        joined = " | ".join(r.reasons).lower()
        assert "breached" in joined
        assert "posture bullish but regime not confirmed" in joined

    def test_stress_when_vix_panic(self):
        r = fr.evaluate_fragility(
            forecast=_forecast(confidence="medium"),
            vix=29.5,
        )
        assert r.status == "STRESS"
        assert "no new entries" in r.action_hint

    def test_stress_when_breach_plus_risk_off_dominant(self):
        r = fr.evaluate_fragility(
            forecast=_forecast(
                confidence="medium", breached=True,
                risk_off_p=0.50, bull_p=0.30,
            ),
        )
        assert r.status == "STRESS"

    def test_options_bearish_hedge_counts(self):
        r = fr.evaluate_fragility(
            forecast=_forecast(confidence="medium", breached=False),
            lens=_lens(options="bearish hedge"),
        )
        # One signal → CONFLICTED.
        assert r.status == "CONFLICTED"
        assert any("bearish hedge" in s for s in r.reasons)


# ── Entry-label discipline (Task 5) ─────────────────────────────────────────

class TestEntryDiscipline:

    def test_actionable_entry_passes_through(self):
        assert fr.is_entry_actionable("ok") is True
        assert fr.is_entry_actionable("Bullish") is True
        assert fr.is_entry_actionable(None) is True

    def test_non_actionable_entry_blocks(self):
        for v in ("Too Extended", "EXTENDED", "Broken", "avoid"):
            assert fr.is_entry_actionable(v) is False, v

    def test_neutralize_buy_candidate(self):
        out = fr.neutralize_research_label("BUY candidate")
        assert out == "Research-aligned candidate"

    def test_buy_wording_demoted_when_entry_too_extended(self):
        """Audit-screenshot scenario: do NOT show 'Research-aligned candidate'
        next to an entry layer marked Too Extended."""
        out = fr.neutralize_research_label(
            "BUY candidate", entry_label="Too Extended"
        )
        assert "Research Only" in out
        assert "BUY" not in out.upper().split()  # no bare "BUY"

    def test_pass_through_unknown_label(self):
        out = fr.neutralize_research_label("WATCH")
        assert out == "WATCH"

    def test_pass_through_with_actionable_entry(self):
        out = fr.neutralize_research_label("WATCH", entry_label="ok")
        assert out == "WATCH"


# ── Audit-screenshot end-to-end scenario ────────────────────────────────────

def test_audit_screenshot_2026_05_15_evaluates_to_fragile_or_stress():
    """The exact configuration in the 2026-05-15 weekly audit screenshot:
    Bull Continuation, conf LOW, breached, posture BULLISH, SPY lens
    bearish-but-oversold, entry Too Extended, options bearish hedge."""
    forecast = _forecast(
        regime="Bull Continuation",
        confidence="low",
        breached=True,
        breach_reasons=["leading sectors 0 < 2"],
        bull_p=0.40,
        risk_off_p=0.25,
    )
    lens = _lens(
        label="bearish but oversold",
        confidence="low",
        entry="too extended",
        options="bearish hedge",
    )
    r = fr.evaluate_fragility(
        forecast=forecast,
        posture=_PostureBullishLow(),
        lens=lens,
        vix=17.9,
    )
    # The audit case should never read as NORMAL.
    assert r.status in {"FRAGILE", "STRESS"}, (r.status, r.reasons)
    joined = " | ".join(r.reasons).lower()
    assert "breached" in joined
    assert "bearish hedge" in joined

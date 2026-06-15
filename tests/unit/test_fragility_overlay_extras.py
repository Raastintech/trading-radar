"""
tests/unit/test_fragility_overlay_extras.py

Pins the Phase 1F+ display-only follow-ups:
  - Stock Lens panel adds a per-ticker TICKER STATE line whose verdict
    comes from core.fragility.evaluate_fragility against the lens.
  - Market Forecast panel demotes the "Strategies: ..." line styling
    when the panel is FRAGILE/CONFLICTED so the green "allowed"
    stances don't visually approve a regime that isn't confirmed.

Both checks render the relevant panel through Rich with color_system
disabled and assert on the plain text payload.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict

import pytest

os.environ.setdefault("GEM_TRADER_SKIP_DOTENV", "true")
os.environ.setdefault("ALPACA_API_KEY", "offline")
os.environ.setdefault("ALPACA_SECRET_KEY", "offline")
os.environ.setdefault("FMP_API_KEY", "offline")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from rich.console import Console  # noqa: E402

import dashboards.gem_trader_hq as hq  # noqa: E402


# ── Test fixtures: a hand-built DataLayer-like dict & State stub ────────────

class _FakeData:
    """Minimal DataLayer stand-in. Stores a dict the dashboard reads via
    .get() and a per-ticker lens accessor."""

    def __init__(self, blob: Dict[str, Any], lens_by_ticker: Dict[str, Any]):
        self._blob = blob
        self._lens = lens_by_ticker

    def get(self, key, default=None):
        return self._blob.get(key, default)

    def get_stock_lens(self, ticker: str):
        return self._lens.get(ticker.upper())

    def get_executive_gatekeeper(self, ticker: str):
        return {"_missing": True}


class _FakeState:
    def __init__(self, ticker=None):
        self.ticker = ticker
        self.lens_pending_ticker = None
        self.lens_last_error = None


def _render(panel) -> str:
    console = Console(width=200, record=True,
                      color_system=None, force_terminal=False)
    console.print(panel)
    return console.export_text()


# ── Audit-screenshot forecast artifact ──────────────────────────────────────

def _audit_forecast() -> Dict[str, Any]:
    return {
        "headline": {
            "current_regime": "Bull Continuation",
            "bias_5d": "mixed", "bias_10d": "mixed",
            "confidence": "low",
            "invalidation_breached": True,
            "invalidation_breach_reasons": ["leading sectors 0 < 2"],
            "main_invalidation": "leading sectors 0 < 2",
        },
        "regime_probabilities": [
            {"regime": "Bull Continuation", "probability": 0.40},
            {"regime": "Risk-Off",          "probability": 0.25},
            {"regime": "Chop / Range",      "probability": 0.15},
        ],
        "sector_rotation": {
            "leading": [], "improving": ["XLK"],
            "weakening": ["XLB", "XLY"], "defensive": ["XLP"],
        },
        "strategy_favorability": {
            "VOYAGER":         {"stance": "allowed"},
            "SNIPER_V6":       {"stance": "allowed"},
            "SHORT_A":         {"stance": "selective"},
            "ALPHA_DISCOVERY": {"stance": "selective"},
        },
        "_age_short": "5h",
        "_stale": False,
        "anchor_date": "2026-05-14",
        "data_freshness_status": "fresh",
    }


def _amzn_lens(entry_view: str = "Watch Reclaim",
               options_view: str = "Bullish confirming",
               label: str = "Bullish but not buyable yet") -> Dict[str, Any]:
    return {
        "ticker": "AMZN",
        "label": label,
        "confidence": "low",
        "_age_short": "1m",
        "_stale": False,
        "conclusion": "AMZN: constructive but not buyable now.",
        "invalidation": ["loses EMA20 (261.45) on close"],
        "horizon_view": {"5d": label, "10d": label, "20d": "Bullish"},
        "layers": {
            "market_regime":   {"view": "Bullish",      "available": True},
            "sector":          {"view": "Weakening",    "available": True, "etf": "XLY"},
            "technicals":      {"view": "Bullish",      "available": True},
            "entry_validator": {"view": entry_view,     "available": True},
            "alpha":           {"view": "Early Discovery", "available": True},
            "posture":         {"view": "Not in Posture focus", "available": True},
            "options":         {"view": options_view,   "available": True},
            "social":          {"view": "No useful signal", "available": True},
        },
    }


# ── Tests ───────────────────────────────────────────────────────────────────

class TestTickerStateInLensHeader:

    def test_audit_scenario_shows_fragile(self):
        data = _FakeData(
            blob={
                "market_forecast": _audit_forecast(),
                "alpha_discovery": {"_missing": True},
                "universe_snap": {},
                "regime": {"regime": "BULL"},
                "vix": 18.0,
            },
            lens_by_ticker={"AMZN": _amzn_lens()},
        )
        state = _FakeState(ticker="AMZN")
        out = _render(hq.PB.stock_lens_panel(data, state))
        assert "TICKER STATE:" in out, out
        assert "FRAGILE" in out, out
        assert "stalk only" in out, out

    def test_normal_when_forecast_unbreached_and_entry_actionable(self):
        forecast = _audit_forecast()
        forecast["headline"]["invalidation_breached"] = False
        forecast["headline"]["confidence"] = "medium"
        data = _FakeData(
            blob={
                "market_forecast": forecast,
                "alpha_discovery": {"_missing": True},
                "universe_snap": {},
                "regime": {"regime": "BULL"},
                "vix": 17.0,
            },
            lens_by_ticker={"AMZN": _amzn_lens(entry_view="ok",
                                               options_view="neutral",
                                               label="Bullish")},
        )
        state = _FakeState(ticker="AMZN")
        out = _render(hq.PB.stock_lens_panel(data, state))
        assert "TICKER STATE:" in out
        assert "NORMAL" in out


class TestStrategiesDemotionUnderFragile:

    def test_strategies_label_advisory_when_breached(self):
        data = _FakeData(
            blob={
                "market_forecast": _audit_forecast(),
                "vix": 18.0,
            },
            lens_by_ticker={},
        )
        out = _render(hq.PB.market_forecast_context(data))
        # Label rewrites + footer suffix appear only in the demoted path.
        assert "Research board (advisory):" in out, out
        assert "regime not confirmed" in out, out

    def test_strategies_label_unchanged_when_normal(self):
        forecast = _audit_forecast()
        forecast["headline"]["invalidation_breached"] = False
        forecast["headline"]["confidence"] = "medium"
        data = _FakeData(blob={"market_forecast": forecast, "vix": 17.0},
                         lens_by_ticker={})
        out = _render(hq.PB.market_forecast_context(data))
        assert "Research board:" in out, out
        assert "Research board (advisory)" not in out, out
        assert "regime not confirmed" not in out, out

    def test_demoted_when_only_low_conf_without_breach(self):
        """LOW conf alone (no breach) should still demote — that's the
        CONFLICTED path."""
        forecast = _audit_forecast()
        forecast["headline"]["invalidation_breached"] = False
        forecast["headline"]["confidence"] = "low"
        data = _FakeData(blob={"market_forecast": forecast, "vix": 17.0},
                         lens_by_ticker={})
        out = _render(hq.PB.market_forecast_context(data))
        assert "Research board (advisory):" in out

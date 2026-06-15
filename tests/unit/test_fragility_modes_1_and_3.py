"""
tests/unit/test_fragility_modes_1_and_3.py

Phase 1F+ propagation to Mode 1 (Monitor) and Mode 3 (Risk).

Pins:
  - Mode 1 market_bias_strip swaps BULLISH for FRAGILE/CONFLICTED when
    the forecast is breached/LOW; 30d appears next to 5d/10d.
  - Mode 1 market_forecast_strip replaces the small ⚠BREACH chip with
    the consolidated FRAGILE/CONFLICTED badge.
  - Mode 3 market_forecast_detailed: header reads FRAGILE/CONFLICTED,
    30d column present, "Strategy favorability (advisory)" rewrite
    fires when breached/LOW.
  - Mode 3 forecast_forward_status carries a fragility notice when the
    current forecast is breached/LOW.
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List

import pytest

os.environ.setdefault("GEM_TRADER_SKIP_DOTENV", "true")
os.environ.setdefault("ALPACA_API_KEY", "offline")
os.environ.setdefault("ALPACA_SECRET_KEY", "offline")
os.environ.setdefault("FMP_API_KEY", "offline")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from rich.console import Console  # noqa: E402

import dashboards.gem_trader_hq as hq  # noqa: E402


class _FakeData:
    def __init__(self, blob: Dict[str, Any]):
        self._blob = blob

    def get(self, key, default=None):
        return self._blob.get(key, default)

    def get_stock_lens(self, ticker):
        return self._blob.get(f"stock_lens::{ticker.upper()}")

    def get_executive_gatekeeper(self, ticker):
        return {"_missing": True}

    def get_research_note(self, ticker):
        return self._blob.get(f"research_note::{ticker.upper()}",
                              {"_missing": True})


def _render(panel) -> str:
    console = Console(width=220, record=True,
                      color_system=None, force_terminal=False)
    console.print(panel)
    return console.export_text()


def _spy_uptrend(days: int = 35, start: float = 700.0,
                  step: float = 1.0) -> List[Dict[str, Any]]:
    return [{"date": f"2026-04-{i+1:02d}",
             "open": start + step * i, "high": start + step * i,
             "low":  start + step * i, "close": start + step * i,
             "volume": 1}
            for i in range(days)]


def _forecast(*, breached: bool, confidence: str = "medium"):
    return {
        "headline": {
            "current_regime": "Bull Continuation",
            "bias_5d": "mixed", "bias_10d": "mixed",
            "confidence": confidence,
            "invalidation_breached": breached,
            "invalidation_breach_reasons": ["leading sectors 0 < 2"] if breached else [],
            "main_invalidation": "leading sectors 0 < 2",
        },
        "regime_probabilities": [
            {"regime": "Bull Continuation", "probability": 0.40},
            {"regime": "Risk-Off",          "probability": 0.25},
        ],
        "sector_rotation": {
            "leading": [], "improving": ["XLK"],
            "weakening": ["XLB"], "defensive": ["XLP"],
        },
        "strategy_favorability": {
            "VOYAGER":         {"stance": "allowed", "reason": "trend supportive"},
            "SNIPER_V6":       {"stance": "allowed", "reason": "vol regime ok"},
            "SHORT_A":         {"stance": "selective"},
            "ALPHA_DISCOVERY": {"stance": "selective"},
        },
        "factor_contributions": {"bullish": ["trend"], "bearish": []},
        "volatility": {"vix": 18.0, "vix_avg_20": 17.0},
        "breadth": {"sector_breadth_pct_above_ma20": 0.5,
                    "sector_breadth_pct_above_ma50": 0.5},
        "data_quality": {"spy_bars": 60},
        "_age_short": "5h", "_stale": False,
        "anchor_date": "2026-05-14",
        "data_freshness_status": "fresh",
    }


# ── Mode 1: market_bias_strip ───────────────────────────────────────────────

class TestModeOneBias:

    def test_fragile_badge_when_breached_and_low(self):
        data = _FakeData({
            "market_forecast": _forecast(breached=True, confidence="low"),
            "spy_bars": _spy_uptrend(),
        })
        out = _render(hq.PB.market_bias_strip(data))
        assert "FRAGILE" in out
        assert "BULLISH" not in out
        # 30d column flowed in.
        assert " · 30d " in out

    def test_conflicted_badge_when_only_breached(self):
        data = _FakeData({
            "market_forecast": _forecast(breached=True, confidence="medium"),
            "spy_bars": _spy_uptrend(),
        })
        out = _render(hq.PB.market_bias_strip(data))
        assert "CONFLICTED" in out
        assert "FRAGILE" not in out

    def test_bullish_badge_preserved_when_clean(self):
        forecast = _forecast(breached=False, confidence="medium")
        forecast["headline"]["bias_5d"] = "bullish"
        data = _FakeData({"market_forecast": forecast,
                          "spy_bars": _spy_uptrend()})
        out = _render(hq.PB.market_bias_strip(data))
        assert "BULLISH" in out
        assert "FRAGILE" not in out and "CONFLICTED" not in out

    def test_30d_hidden_when_bars_missing(self):
        data = _FakeData({"market_forecast": _forecast(breached=False),
                          "spy_bars": []})
        out = _render(hq.PB.market_bias_strip(data))
        assert " · 30d " not in out


# ── Mode 1: market_forecast_strip ──────────────────────────────────────────

class TestModeOneForecastStrip:

    def test_fragile_in_strip_when_breached_and_low(self):
        data = _FakeData({
            "market_forecast": _forecast(breached=True, confidence="low"),
            "spy_bars": _spy_uptrend(),
        })
        out = _render(hq.PB.market_forecast_strip(data))
        assert "FRAGILE" in out
        # The legacy chip is replaced.
        assert "⚠BREACH" not in out
        # 30d still rendered.
        assert " · 30d " in out

    def test_conflicted_when_only_low_conf(self):
        data = _FakeData({
            "market_forecast": _forecast(breached=False, confidence="low"),
            "spy_bars": _spy_uptrend(),
        })
        out = _render(hq.PB.market_forecast_strip(data))
        assert "CONFLICTED" in out

    def test_no_badge_when_normal(self):
        data = _FakeData({
            "market_forecast": _forecast(breached=False, confidence="medium"),
            "spy_bars": _spy_uptrend(),
        })
        out = _render(hq.PB.market_forecast_strip(data))
        assert "FRAGILE" not in out
        assert "CONFLICTED" not in out


# ── Mode 3: market_forecast_detailed ───────────────────────────────────────

class TestModeThreeForecastDetailed:

    def test_header_leads_with_fragile(self):
        data = _FakeData({
            "market_forecast": _forecast(breached=True, confidence="low"),
            "spy_bars": _spy_uptrend(),
            "vix": 18.0,
        })
        out = _render(hq.PB.market_forecast_detailed(data))
        # Header sequence: FRAGILE first, then dim "regime Bull Continuation"
        assert "FRAGILE" in out
        first_fragile = out.index("FRAGILE")
        first_regime  = out.index("Bull Continuation")
        assert first_fragile < first_regime, out
        # 30d horizon in header.
        assert "30d" in out
        # Research posture block demoted.
        assert "Research posture (advisory)" in out
        assert "regime not confirmed — advisory only" in out

    def test_normal_path_keeps_existing_labels(self):
        data = _FakeData({
            "market_forecast": _forecast(breached=False, confidence="medium"),
            "spy_bars": _spy_uptrend(),
            "vix": 18.0,
        })
        out = _render(hq.PB.market_forecast_detailed(data))
        assert "FRAGILE" not in out
        assert "CONFLICTED" not in out
        # The plain header still shows the regime as the lead.
        assert "Bull Continuation" in out
        # NOTE: Rich grid wraps the right column, so a literal newline
        # check isn't portable. The presence test + absence of advisory
        # marker is what we actually care about.
        assert "Research posture" in out
        assert "(advisory)" not in out
        assert "regime not confirmed" not in out


# ── Mode 3: forecast_forward_status ────────────────────────────────────────

class TestModeThreeForwardTracking:

    def _forward_payload(self) -> Dict[str, Any]:
        return {
            "snapshots_total": 12,
            "snapshots_matured": 7,
            "snapshots_open": 5,
            "matured_aggregate": {
                "spy_5d_hit_rate": 0.57,
                "spy_10d_hit_rate": 0.50,
            },
            "_age_short": "2h",
            "_stale": False,
            "_missing": False,
        }

    def test_notice_shown_when_current_forecast_breached(self):
        data = _FakeData({
            "market_forecast": _forecast(breached=True, confidence="low"),
            "forecast_forward_summary": self._forward_payload(),
            "stock_lens_forward_summary": {"_missing": True},
        })
        out = _render(hq.PB.forecast_forward_status(data))
        assert "FRAGILE" in out
        assert "hit rates below reflect prior calls, not today" in out

    def test_notice_absent_when_current_forecast_normal(self):
        data = _FakeData({
            "market_forecast": _forecast(breached=False, confidence="medium"),
            "forecast_forward_summary": self._forward_payload(),
            "stock_lens_forward_summary": {"_missing": True},
        })
        out = _render(hq.PB.forecast_forward_status(data))
        assert "FRAGILE" not in out
        assert "CONFLICTED" not in out
        assert "hit rates below reflect prior calls" not in out

    def test_stale_flag_marked_loud(self):
        payload = self._forward_payload()
        payload["_stale"] = True
        data = _FakeData({
            "market_forecast": _forecast(breached=False, confidence="medium"),
            "forecast_forward_summary": payload,
            "stock_lens_forward_summary": {"_missing": True},
        })
        out = _render(hq.PB.forecast_forward_status(data))
        assert "STALE" in out

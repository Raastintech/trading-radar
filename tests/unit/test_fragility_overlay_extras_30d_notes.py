"""
tests/unit/test_fragility_overlay_extras_30d_notes.py

Pins two display-only follow-ups to Phase 1F+:

  - Dashboard-side 30d bias computed from cached SPY bars: rendered
    next to the persisted 5d / 10d biases in the Market Forecast
    panels (Mode 1 strip and Mode 2 detail).
  - Severely stale research notes (overdue > 7d and not reviewed, or
    older than 30d with no review date) auto-fall back to the
    [auto] panel so the operator isn't staring at a 16-day-old
    conclusion.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import pytest

os.environ.setdefault("GEM_TRADER_SKIP_DOTENV", "true")
os.environ.setdefault("ALPACA_API_KEY", "offline")
os.environ.setdefault("ALPACA_SECRET_KEY", "offline")
os.environ.setdefault("FMP_API_KEY", "offline")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from rich.console import Console  # noqa: E402

import dashboards.gem_trader_hq as hq  # noqa: E402


# ── Fixtures ────────────────────────────────────────────────────────────────

def _spy_bars_uptrend(days: int = 35, start: float = 700.0,
                       step: float = 1.2) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for i in range(days):
        c = start + step * i
        out.append({"date": f"2026-04-{i+1:02d}", "open": c - 0.5,
                    "high": c + 0.6, "low": c - 0.6, "close": c, "volume": 1})
    return out


def _spy_bars_flat(days: int = 35, c: float = 700.0) -> List[Dict[str, Any]]:
    return [{"date": f"2026-04-{i+1:02d}", "open": c, "high": c,
             "low": c, "close": c, "volume": 1} for i in range(days)]


def _spy_bars_downtrend(days: int = 35, start: float = 800.0,
                         step: float = -1.5) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for i in range(days):
        c = start + step * i
        out.append({"date": f"2026-04-{i+1:02d}", "open": c, "high": c,
                    "low": c, "close": c, "volume": 1})
    return out


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
    console = Console(width=200, record=True,
                      color_system=None, force_terminal=False)
    console.print(panel)
    return console.export_text()


# ── 30d bias compute ────────────────────────────────────────────────────────

class TestThirtyDayBiasCompute:

    def test_returns_dash_when_insufficient_bars(self):
        assert hq.PB._compute_30d_bias([]) == ("—", None)
        assert hq.PB._compute_30d_bias([{"close": 1.0}] * 20)[0] == "—"

    def test_uptrend_reads_bullish(self):
        # +6% over 30 bars, closes above SMA30.
        label, ret = hq.PB._compute_30d_bias(_spy_bars_uptrend())
        assert label == "bullish", (label, ret)
        assert ret > 3.0

    def test_flat_reads_neutral(self):
        label, ret = hq.PB._compute_30d_bias(_spy_bars_flat())
        assert label == "neutral"
        assert ret == 0.0 or abs(ret) <= 0.1

    def test_downtrend_reads_bearish(self):
        label, ret = hq.PB._compute_30d_bias(_spy_bars_downtrend())
        assert label == "bearish", (label, ret)
        assert ret < -3.0


# ── 30d in the rendered panel ───────────────────────────────────────────────

def _forecast_blob() -> Dict[str, Any]:
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
        ],
        "strategy_favorability": {
            "VOYAGER": {"stance": "allowed"},
            "SNIPER_V6": {"stance": "allowed"},
        },
        "_age_short": "5h", "_stale": False,
        "anchor_date": "2026-05-14",
    }


class TestThirtyDayInForecastPanel:

    def test_mode2_panel_shows_30d_when_bars_present(self):
        data = _FakeData({
            "market_forecast": _forecast_blob(),
            "spy_bars": _spy_bars_uptrend(),
            "vix": 18.0,
        })
        out = _render(hq.PB.market_forecast_context(data))
        assert "30d" in out
        assert "bullish" in out

    def test_mode2_panel_hides_30d_when_bars_missing(self):
        data = _FakeData({
            "market_forecast": _forecast_blob(),
            "spy_bars": [],
            "vix": 18.0,
        })
        out = _render(hq.PB.market_forecast_context(data))
        # No 30d column when we don't have enough bars.
        assert " · 30d " not in out


# ── Stale-note auto-fallback ────────────────────────────────────────────────

def _iso_days_ago(d: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=d)).isoformat()


class _StaleNotesState:
    def __init__(self, ticker: str):
        self.ticker = ticker
        self.lens_pending_ticker = None
        self.lens_last_error = None


class TestStaleNoteAutoFallback:

    def test_fresh_note_renders_normally(self):
        note = {
            "_missing": False,
            "ticker": "MSFT",
            "status": "watch",
            "conclusion": "Watch reclaim above EMA50",
            "timestamp": _iso_days_ago(2),
            "_age_short": "2d",
            "_age_days": 2.0,
            "_days_until_review": 8,
            "review_date": "2099-12-31",
            "reviewed_at": None,
            "note_id": "rn_fresh",
        }
        data = _FakeData({
            "research_note::MSFT": note,
            "stock_lens::MSFT": {"_missing": True},
        })
        state = _StaleNotesState("MSFT")
        out = _render(hq.PB.research_note_panel(data, state))
        assert "rn_fresh" in out
        assert "Watch reclaim above EMA50" in out
        # The [auto] fallback marker should NOT appear for a fresh note.
        assert "[auto]" not in out

    def test_severely_stale_note_falls_back_to_auto(self):
        """The MSFT-screenshot scenario: 16d old, review overdue by 10d."""
        note = {
            "_missing": False,
            "ticker": "MSFT",
            "status": "watch",
            "conclusion": "Watch reclaim above EMA50",
            "timestamp": _iso_days_ago(16),
            "_age_short": "16d",
            "_age_days": 16.0,
            "_days_until_review": -10,  # 10 days overdue
            "review_date": "2026-04-30",
            "reviewed_at": None,
            "note_id": "rn_stale",
        }
        data = _FakeData({
            "research_note::MSFT": note,
            "stock_lens::MSFT": {
                "_missing": False,
                "label": "Avoid / no edge",
                "conclusion": "MSFT: no clean research edge.",
                "_age_short": "1m",
                "_stale": False,
                "layers": {},
            },
        })
        state = _StaleNotesState("MSFT")
        out = _render(hq.PB.research_note_panel(data, state))
        # Fallback fingerprint: [auto] badge + stale footer.
        assert "[auto]" in out
        assert "prior manual note" in out
        assert "overdue 10d" in out

    def test_aged_30d_without_review_date_also_falls_back(self):
        note = {
            "_missing": False,
            "ticker": "AAPL",
            "status": "watch",
            "conclusion": "Wait for breakout",
            "timestamp": _iso_days_ago(35),
            "_age_short": "35d",
            "_age_days": 35.0,
            "_days_until_review": None,
            "review_date": None,
            "reviewed_at": None,
            "note_id": "rn_undated",
        }
        data = _FakeData({
            "research_note::AAPL": note,
            "stock_lens::AAPL": {"_missing": True},
        })
        state = _StaleNotesState("AAPL")
        out = _render(hq.PB.research_note_panel(data, state))
        assert "[auto]" in out
        assert "prior manual note" in out

    def test_reviewed_overdue_does_not_fall_back(self):
        """If the operator already marked the note reviewed, keep it."""
        note = {
            "_missing": False,
            "ticker": "MSFT",
            "status": "watch",
            "conclusion": "Watch reclaim above EMA50",
            "timestamp": _iso_days_ago(16),
            "_age_short": "16d",
            "_age_days": 16.0,
            "_days_until_review": -10,
            "review_date": "2026-04-30",
            "reviewed_at": _iso_days_ago(3),
            "note_id": "rn_reviewed",
        }
        data = _FakeData({
            "research_note::MSFT": note,
            "stock_lens::MSFT": {"_missing": True},
        })
        state = _StaleNotesState("MSFT")
        out = _render(hq.PB.research_note_panel(data, state))
        # The reviewed flag short-circuits the stale check.
        assert "rn_reviewed" in out
        assert "[auto]" not in out

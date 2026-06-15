"""tests/unit/test_regime_panel.py — SPY/REGIME panel headline coherence.

The Mode 1 SPY/REGIME panel must show the same regime label that the
TRADE READINESS banner and MARKET FORECAST panel surface.  When the
cached regime_forecast artifact carries a headline.current_regime,
the panel must render that string verbatim — not the generic
"BULL"/"BEAR" substring from core.market_regime.
"""
from __future__ import annotations

from rich.console import Console

from dashboards.gem_trader_hq import PB


class _StubDataLayer:
    def __init__(self, values):
        self._v = values

    def get(self, key, default=None):
        return self._v.get(key, default)


def _spy_bars(n=30, start=600.0, step=0.5):
    out = []
    px = start
    for _ in range(n):
        out.append({"close": px, "high": px + 0.5, "low": px - 0.5})
        px += step
    return out


def _render(panel) -> str:
    c = Console(record=True, width=120, force_terminal=False, color_system=None)
    c.print(panel)
    return c.export_text()


def test_regime_panel_uses_forecast_headline_verbatim():
    data = _StubDataLayer({
        "spy_bars": _spy_bars(),
        "regime": {"regime": "BULL", "trend_strength": "75"},
        "market_forecast": {
            "_missing": False,
            "headline": {
                "current_regime": "Bull Pullback / Buy-the-Dip",
                "confidence": "medium",
            },
            "regime_probabilities": [],
        },
    })
    text = _render(PB.regime(data))
    assert "Bull Pullback / Buy-the-Dip" in text
    # The trend_strength from the legacy regime payload still trails as dim.
    assert "75" in text
    # The legacy generic upper-case "BULL" line must not appear standalone.
    assert " BULL\n" not in text and " BULL " not in text


def test_regime_panel_falls_back_to_legacy_regime_when_no_forecast():
    data = _StubDataLayer({
        "spy_bars": _spy_bars(),
        "regime": {"regime": "BULL", "trend_strength": "75"},
        "market_forecast": None,
    })
    text = _render(PB.regime(data))
    assert "BULL" in text
    assert "75" in text


def test_regime_panel_handles_missing_forecast_artifact():
    """When the forecast artifact is marked _missing, fall back to legacy regime."""
    data = _StubDataLayer({
        "spy_bars": _spy_bars(),
        "regime": {"regime": "BEAR", "trend_strength": "62"},
        "market_forecast": {"_missing": True},
    })
    text = _render(PB.regime(data))
    assert "BEAR" in text
    assert "62" in text


def test_regime_panel_risk_off_headline_renders_red():
    """A Risk-Off headline still needs the verbatim name even though it has no BULL/BEAR substring."""
    data = _StubDataLayer({
        "spy_bars": _spy_bars(),
        "regime": None,
        "market_forecast": {
            "_missing": False,
            "headline": {"current_regime": "Risk-Off", "confidence": "low"},
        },
    })
    text = _render(PB.regime(data))
    assert "Risk-Off" in text
    # Confidence hint trails the label.
    assert "LOW" in text

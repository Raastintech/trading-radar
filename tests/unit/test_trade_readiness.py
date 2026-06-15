"""tests/unit/test_trade_readiness.py — Trade Readiness banner scoring.

Phase: dashboards-only patch. Verifies that the TRADE READINESS banner:
 - uses forecast.headline.current_regime verbatim,
 - applies forecast + fragility + intraday-pulse penalties with the
   conflict-penalty cap (−35 non-STRESS, no cap for STRESS),
 - emits an intraday chip only during REGULAR session,
 - hard-floors RISK-ON / SELECTIVE when VIX ≥ 25,
 - and never lets a breached/red tape display "RISK-ON".
"""
from __future__ import annotations

import pytest

from dashboards.gem_trader_hq import compute_trade_readiness


def _forecast(
    *,
    current_regime: str = "Bull Continuation",
    breached: bool = False,
    confidence: str = "medium",
    risk_off_p: float = 0.0,
) -> dict:
    """Build a minimal forecast artifact matching the cached schema."""
    return {
        "headline": {
            "current_regime": current_regime,
            "confidence": confidence,
            "invalidation_breached": breached,
            "invalidation_breach_reasons": [],
        },
        "regime_probabilities": [
            {"regime": "Bull Continuation", "probability": 0.30},
            {"regime": "Bull Pullback / Buy-the-Dip", "probability": 0.45},
            {"regime": "Chop / Range", "probability": 0.25 - risk_off_p if risk_off_p < 0.25 else 0.0},
            {"regime": "Risk-Off", "probability": risk_off_p},
            {"regime": "Volatility Expansion / Stress", "probability": 0.0},
            {"regime": "Bear Rally / Unstable Rebound", "probability": 0.0},
        ],
        "market_trend": {},
        "volatility": {},
        "sector_rotation": {},
    }


def _spy_bars(prev_close: float, last_close: float):
    return [
        {"close": prev_close, "high": prev_close, "low": prev_close},
        {"close": last_close, "high": last_close, "low": last_close},
    ]


# ─────────────────────────────────────────────────────────────────────────────
# 1. Bull quiet day → RISK-ON, no chip.
# ─────────────────────────────────────────────────────────────────────────────
def test_bull_quiet_day_is_risk_on_no_chip():
    forecast = _forecast(current_regime="Bull Continuation", breached=False)
    status, _style, bullets, chip, reasons = compute_trade_readiness(
        vix=14.0,
        regime=None,
        econ_cal=[],
        mkt_status="OPEN",
        forecast=forecast,
        etf_quotes={"QQQ": {"change_pct": 0.5}, "VXX": {"change_pct": -1.0},
                    "XLP": {"change_pct": 0.2}, "XLU": {"change_pct": 0.1}},
        spy_bars=_spy_bars(600.0, 602.0),  # +0.33%
    )
    assert status == "RISK-ON"
    assert chip is None
    assert reasons["spy_red"] == 0
    assert reasons["defensive_flip"] == 0
    assert reasons["conflict_applied"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# 2. Red intraday + defensive flip → SELECTIVE with chip, FRAGILE fragility.
# ─────────────────────────────────────────────────────────────────────────────
def test_red_intraday_with_defensive_flip_is_selective_with_chip():
    forecast = _forecast(
        current_regime="Bull Pullback / Buy-the-Dip",
        breached=True,
        confidence="low",
        risk_off_p=0.0,
    )
    status, _style, _bullets, chip, reasons = compute_trade_readiness(
        vix=18.2,
        regime=None,
        econ_cal=[],
        mkt_status="OPEN",
        forecast=forecast,
        etf_quotes={"QQQ": {"change_pct": -1.1}, "VXX": {"change_pct": 2.0},
                    "XLP": {"change_pct": 0.4}, "XLU": {"change_pct": 0.6}},
        spy_bars=_spy_bars(600.0, 595.15),  # -0.81%
    )
    assert status == "SELECTIVE"
    assert chip is not None
    assert "SPY" in chip and "QQQ" in chip and "defensive rotation" in chip
    assert reasons["fragility_status"] == "FRAGILE"
    assert reasons["forecast_invalidation"] == -20
    assert reasons["spy_red"] == -10
    assert reasons["defensive_flip"] == -15
    # Cap is applied because raw conflict > 35
    assert reasons["conflict_cap_applied"] is True
    assert reasons["conflict_applied"] == -35


# ─────────────────────────────────────────────────────────────────────────────
# 3. Forecast breached + red intraday triggers cap and does not over-penalize.
# ─────────────────────────────────────────────────────────────────────────────
def test_breached_plus_red_intraday_uses_cap():
    forecast = _forecast(
        current_regime="Bull Pullback / Buy-the-Dip",
        breached=True,
        confidence="low",
        risk_off_p=0.30,
    )
    status, _style, _bullets, chip, reasons = compute_trade_readiness(
        vix=14.0,
        regime=None,
        econ_cal=[],
        mkt_status="OPEN",
        forecast=forecast,
        etf_quotes={"QQQ": {"change_pct": -1.5}, "VXX": {"change_pct": 6.0},
                    "XLP": {"change_pct": 0.5}, "XLU": {"change_pct": 0.4}},
        spy_bars=_spy_bars(600.0, 591.0),  # -1.5%
    )
    # Raw conflict = 20 (breach) + 10 (risk_off) + 15 (FRAGILE) + 20 (SPY≤-1)
    #              + 15 (def_flip) + 10 (VXX≥+5) = 90. Capped at 35.
    assert reasons["fragility_status"] == "FRAGILE"
    assert reasons["conflict_raw"] == -90
    assert reasons["conflict_applied"] == -35
    assert reasons["conflict_cap_applied"] is True
    # 100 - 0 (VIX<18) - 35 (capped) = 65 → SELECTIVE, not STANDBY.
    assert status == "SELECTIVE"
    assert chip is not None


# ─────────────────────────────────────────────────────────────────────────────
# 4. VIX 26 → STANDBY regardless of other signals.
# ─────────────────────────────────────────────────────────────────────────────
def test_vix_26_floors_to_standby():
    # No forecast / no intraday — would otherwise score 80 → RISK-ON.
    status, _style, _bullets, _chip, reasons = compute_trade_readiness(
        vix=26.0,
        regime={"regime": "BULL"},
        econ_cal=[],
        mkt_status="OPEN",
        forecast=None,
        etf_quotes={"QQQ": {"change_pct": 0.5}, "VXX": {"change_pct": -1.0}},
        spy_bars=_spy_bars(600.0, 603.0),
    )
    assert status == "STANDBY"
    assert reasons["vix"] == -20
    assert reasons["vix_floor_applied"] is True


# ─────────────────────────────────────────────────────────────────────────────
# 5. STRESS fragility can force STANDBY (no conflict cap).
# ─────────────────────────────────────────────────────────────────────────────
def test_stress_fragility_forces_standby():
    forecast = _forecast(
        current_regime="Risk-Off",
        breached=True,
        confidence="low",
        risk_off_p=0.50,  # ≥ 0.40 → STRESS together with breach
    )
    status, _style, _bullets, _chip, reasons = compute_trade_readiness(
        vix=18.2,
        regime=None,
        econ_cal=[],
        mkt_status="OPEN",
        forecast=forecast,
        etf_quotes={},
        spy_bars=_spy_bars(600.0, 600.0),
    )
    # Raw conflict = 20 (breach) + 10 (risk_off≥0.25) + 30 (STRESS) = 60.
    # STRESS is uncapped → applied = 60.
    assert reasons["fragility_status"] == "STRESS"
    assert reasons["conflict_raw"] == -60
    assert reasons["conflict_applied"] == -60
    assert reasons["conflict_cap_applied"] is False
    # 100 - 5 (VIX 18-22) - 60 = 35 → STANDBY tier.
    assert status == "STANDBY"


# ─────────────────────────────────────────────────────────────────────────────
# 6a. CLOSED → no intraday penalties, no chip.
# ─────────────────────────────────────────────────────────────────────────────
def test_closed_session_no_intraday_no_chip():
    forecast = _forecast(breached=True, confidence="low", risk_off_p=0.50)
    status, _style, _bullets, chip, reasons = compute_trade_readiness(
        vix=18.0,
        regime=None,
        econ_cal=[],
        mkt_status="CLOSED",
        forecast=forecast,
        etf_quotes={"QQQ": {"change_pct": -2.0}, "VXX": {"change_pct": 10.0},
                    "XLP": {"change_pct": 0.5}, "XLU": {"change_pct": 0.5}},
        spy_bars=_spy_bars(600.0, 580.0),
    )
    assert chip is None
    assert reasons["spy_red"] == 0
    assert reasons["defensive_flip"] == 0
    assert reasons["vxx_stress"] == 0
    assert reasons["conflict_applied"] == 0
    # CLOSED always returns STANDBY in this banner.
    assert status == "STANDBY"


# ─────────────────────────────────────────────────────────────────────────────
# 6b. PRE-MARKET → no intraday penalties, no chip.
# ─────────────────────────────────────────────────────────────────────────────
def test_premarket_session_no_intraday_no_chip():
    forecast = _forecast(breached=True, confidence="low")
    _status, _style, _bullets, chip, reasons = compute_trade_readiness(
        vix=18.0,
        regime=None,
        econ_cal=[],
        mkt_status="PRE-MARKET",
        forecast=forecast,
        etf_quotes={"QQQ": {"change_pct": -1.5}, "VXX": {"change_pct": 7.0},
                    "XLP": {"change_pct": 0.5}, "XLU": {"change_pct": 0.5}},
        spy_bars=_spy_bars(600.0, 590.0),
    )
    assert chip is None
    assert reasons["conflict_applied"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# 7. Forecast headline name renders verbatim — not collapsed to "BULL".
# ─────────────────────────────────────────────────────────────────────────────
def test_forecast_headline_name_used_verbatim():
    forecast = _forecast(current_regime="Bull Pullback / Buy-the-Dip")
    _status, _style, bullets, _chip, reasons = compute_trade_readiness(
        vix=15.0,
        regime={"regime": "BULL"},   # generic regime present; should not override headline name
        econ_cal=[],
        mkt_status="OPEN",
        forecast=forecast,
        etf_quotes={},
        spy_bars=_spy_bars(600.0, 601.0),
    )
    assert reasons["regime_label"] == "Bull Pullback / Buy-the-Dip"
    assert any("Bull Pullback / Buy-the-Dip" in b for b in bullets)
    # Must not present a generic "Regime BULL" line when headline is available.
    assert not any(b == "Regime BULL — trend supportive" for b in bullets)


# ─────────────────────────────────────────────────────────────────────────────
# 8. Sanity: VXX-only stress fires the chip but stays under the cap.
# ─────────────────────────────────────────────────────────────────────────────
def test_vxx_stress_chip_only_when_threshold_crossed():
    forecast = _forecast(breached=False)
    status, _style, _bullets, chip, reasons = compute_trade_readiness(
        vix=17.0,
        regime=None,
        econ_cal=[],
        mkt_status="OPEN",
        forecast=forecast,
        etf_quotes={"QQQ": {"change_pct": 0.2}, "VXX": {"change_pct": 6.5},
                    "XLP": {"change_pct": 0.1}, "XLU": {"change_pct": 0.0}},
        spy_bars=_spy_bars(600.0, 600.5),
    )
    assert reasons["vxx_stress"] == -10
    assert chip is not None and "VXX" in chip
    # SPY isn't red, so no SPY chip element.
    assert "SPY" not in chip
    # Chip-presence floor: tape stress chip must demote a would-be RISK-ON.
    assert status == "SELECTIVE"
    assert reasons["chip_floor_applied"] is True


# ─────────────────────────────────────────────────────────────────────────────
# 9. Chip-presence floor: defensive rotation alone with otherwise clean tape
#    must not show RISK-ON, even if the score sits at the 80 boundary.
# ─────────────────────────────────────────────────────────────────────────────
def test_defensive_rotation_alone_demotes_risk_on_to_selective():
    forecast = _forecast(current_regime="Bull Pullback / Buy-the-Dip",
                         breached=False, confidence="medium")
    status, _style, _bullets, chip, reasons = compute_trade_readiness(
        vix=18.1,
        regime=None,
        econ_cal=[],
        mkt_status="OPEN",
        forecast=forecast,
        etf_quotes={"QQQ": {"change_pct": -0.3}, "VXX": {"change_pct": 1.0},
                    "XLP": {"change_pct": 0.5}, "XLU": {"change_pct": 0.6}},
        spy_bars=_spy_bars(600.0, 597.1),  # -0.48% (below SPY trigger)
    )
    # SPY just above -0.5% threshold → no spy_red penalty.
    assert reasons["spy_red"] == 0
    # Defensive flip alone — only 15 point hit; raw score would land at 80.
    assert reasons["defensive_flip"] == -15
    assert chip is not None and "defensive rotation" in chip
    assert status == "SELECTIVE"
    assert reasons["chip_floor_applied"] is True

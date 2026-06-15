"""
tests/unit/test_phase_1g2_short_regime_gate.py — Phase 1G.2 T3

Tests for the SHORT_A documented regime gate in
``core.signal_hygiene.compute_short_regime_verdict``.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from core import signal_hygiene as sh


def _bull_forecast(vix: float = 16.7):
    return {
        "headline": {
            "current_regime": "Bull Continuation",
            "bias_5d": "constructive",
            "bias_10d": "constructive",
        },
        "market_trend": {"SPY": {"above_ma50": True, "above_ma200": True}},
        "volatility": {"vix": vix},
    }


def _risk_off_forecast():
    return {
        "headline": {
            "current_regime": "Risk-Off",
            "bias_5d": "defensive",
            "bias_10d": "defensive",
        },
        "market_trend": {"SPY": {"above_ma50": False}},
        "volatility": {"vix": 28.0},
    }


def test_non_short_strategy_always_passes():
    v = sh.compute_short_regime_verdict(
        strategy="SNIPER", payload={"regime_context": {}}, opp={},
        forecast_snapshot=_bull_forecast(),
    )
    assert v.ok is True


def test_bull_tape_suppresses_structural_short():
    payload = {"regime_context": {"regime_cluster": "structural_short"}}
    opp = {"score": 85.0}  # no event_date / event_type
    v = sh.compute_short_regime_verdict(
        strategy="SHORT", payload=payload, opp=opp,
        forecast_snapshot=_bull_forecast(),
    )
    assert v.ok is False
    assert "bull tape" in v.reason
    assert v.spy_above_ma50 is True
    assert v.vix == pytest.approx(16.7)
    assert v.current_regime == "Bull Continuation"


def test_event_driven_short_passes_in_bull_tape():
    payload = {"regime_context": {"regime_cluster": "earnings_event_short"}}
    opp = {"event_date": "2026-05-22", "event_type": "earnings"}
    v = sh.compute_short_regime_verdict(
        strategy="SHORT", payload=payload, opp=opp,
        forecast_snapshot=_bull_forecast(),
    )
    assert v.ok is True
    assert v.event_exception is True


def test_event_cluster_without_event_date_does_not_bypass():
    # cluster name suggests event but no explicit event metadata → not a
    # safe exception, gate still applies in a bull tape.
    payload = {"regime_context": {"regime_cluster": "earnings_event_short"}}
    opp = {}
    v = sh.compute_short_regime_verdict(
        strategy="SHORT", payload=payload, opp=opp,
        forecast_snapshot=_bull_forecast(),
    )
    assert v.ok is False
    assert v.event_exception is False


def test_risk_off_does_not_suppress():
    payload = {"regime_context": {"regime_cluster": "structural_short"}}
    v = sh.compute_short_regime_verdict(
        strategy="SHORT", payload=payload, opp={"score": 85.0},
        forecast_snapshot=_risk_off_forecast(),
    )
    assert v.ok is True
    assert "defensive regime" in v.reason


def test_high_vix_does_not_suppress_even_with_bull_regime():
    # Edge: bull regime label but VIX > 20 — the gate should not apply.
    fc = _bull_forecast(vix=22.0)
    payload = {"regime_context": {"regime_cluster": "structural_short"}}
    v = sh.compute_short_regime_verdict(
        strategy="SHORT", payload=payload, opp={"score": 85.0},
        forecast_snapshot=fc,
    )
    assert v.ok is True


def test_missing_forecast_suppresses_conservatively():
    v = sh.compute_short_regime_verdict(
        strategy="SHORT",
        payload={"regime_context": {"regime_cluster": "structural_short"}},
        opp={"score": 85.0}, forecast_snapshot=None,
    )
    assert v.ok is False
    assert "regime forecast unavailable" in v.reason


def test_missing_inputs_suppress_conservatively():
    fc = {
        "headline": {"current_regime": "Bull Continuation"},
        "market_trend": {"SPY": {}},  # above_ma50 missing
        "volatility": {},  # vix missing
    }
    v = sh.compute_short_regime_verdict(
        strategy="SHORT",
        payload={"regime_context": {"regime_cluster": "structural_short"}},
        opp={"score": 85.0}, forecast_snapshot=fc,
    )
    assert v.ok is False
    assert "regime inputs incomplete" in v.reason


def test_spy_below_ma50_does_not_suppress_even_if_vix_low():
    fc = _bull_forecast()
    fc["market_trend"]["SPY"]["above_ma50"] = False
    v = sh.compute_short_regime_verdict(
        strategy="SHORT",
        payload={"regime_context": {"regime_cluster": "structural_short"}},
        opp={"score": 85.0}, forecast_snapshot=fc,
    )
    assert v.ok is True
    assert "gate not breached" in v.reason


def test_short_regime_counters_record_reason():
    counters = sh.HygieneCounters()
    payload = {"regime_context": {"regime_cluster": "structural_short"}}
    v = sh.compute_short_regime_verdict(
        strategy="SHORT", payload=payload, opp={"score": 85.0},
        forecast_snapshot=_bull_forecast(),
    )
    counters.record_short_regime("INTU", v)
    snap = counters.snapshot()
    assert snap["short_regime_suppressed"]["count"] == 1
    assert snap["short_regime_suppressed"]["by_ticker"]["INTU"] == 1
    assert "bull tape" in snap["short_regime_suppressed"]["latest_reason"]

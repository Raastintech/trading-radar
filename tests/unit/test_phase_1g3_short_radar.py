"""
tests/unit/test_phase_1g3_short_radar.py — Phase 1G.3 T2

Short Opportunity Radar (research-only):
  - bull tape -> SHORTS_OFF (suppressed)
  - risk-off tape -> RESEARCH_ACTIVE or SHORT_SLEEVE_TEST_CANDIDATE
  - failed-leader and overcrowded-unwind archetypes classify
  - no forbidden trade language appears anywhere in the output
  - candidates in bull tape carry the 'Avoid Shorting In Bull Tape' label
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from research import short_opportunity_radar as sor

FORBIDDEN = ["short now", "sell now", "execute", "trade approved"]


def _bull_forecast():
    return {
        "headline": {"current_regime": "Bull Continuation", "bias_5d": "constructive"},
        "market_trend": {"SPY": {"above_ma50": True, "above_ma200": True}},
        "volatility": {"vix": 16.7},
        "sector_rotation": {"rows": []},
    }


def _risk_off_forecast():
    return {
        "headline": {"current_regime": "Risk-Off", "bias_5d": "defensive"},
        "market_trend": {"SPY": {"above_ma50": False, "above_ma200": False}},
        "volatility": {"vix": 28.0},
        "sector_rotation": {"rows": [
            {"sector": "XLU", "is_defensive": True, "state": "Leading", "above_ma50": True},
            {"sector": "XLP", "is_defensive": True, "state": "Improving", "above_ma50": True},
            {"sector": "XLK", "is_defensive": False, "state": "Lagging", "above_ma50": False, "rs_20d_pct": -5.0},
            {"sector": "XLY", "is_defensive": False, "state": "Lagging", "above_ma50": False, "rs_20d_pct": -4.0},
            {"sector": "XLF", "is_defensive": False, "state": "Lagging", "above_ma50": True, "rs_20d_pct": -2.0},
        ]},
    }


def test_bull_tape_is_shorts_off():
    r = sor.compute_short_regime_score(_bull_forecast())
    assert r["state"] == sor.STATE_SHORTS_OFF
    assert r["suppressed_bull_tape"] is True
    assert r["score"] <= 30


def test_risk_off_tape_is_research_active_or_higher():
    r = sor.compute_short_regime_score(_risk_off_forecast())
    assert r["score"] >= 51
    assert r["state"] in (sor.STATE_RESEARCH_ACTIVE, sor.STATE_TEST_CANDIDATE)
    assert r["suppressed_bull_tape"] is False


def test_missing_forecast_is_shorts_off():
    r = sor.compute_short_regime_score(None)
    assert r["state"] == sor.STATE_SHORTS_OFF
    assert r["score"] == 0


def test_failed_leader_and_overcrowded_classification():
    alpha = {"items": [
        {"ticker": "AAA", "bucket": "Too Late / Crowded", "options_quality": "BEARISH_HEDGE",
         "return_5d_pct": -2.0, "sector": "Tech"},
        {"ticker": "BBB", "bucket": "Too Late / Crowded", "options_quality": "MIXED_OPTIONS",
         "return_5d_pct": -3.0, "sector": "Tech"},
    ]}
    out = sor.build_candidates(alpha, _risk_off_forecast(), sor.STATE_RESEARCH_ACTIVE)
    assert out["counts"]["OVERCROWDED_UNWIND"] == 1   # AAA (bearish hedge)
    assert out["counts"]["FAILED_LEADER"] == 1        # BBB (crowded + negative 5d)


def test_bull_tape_labels_are_avoid():
    alpha = {"items": [
        {"ticker": "AAA", "bucket": "Too Late / Crowded", "options_quality": "BEARISH_HEDGE",
         "return_5d_pct": -2.0, "sector": "Tech"},
    ]}
    out = sor.build_candidates(alpha, _bull_forecast(), sor.STATE_SHORTS_OFF)
    for rows in out["by_archetype"].values():
        for c in rows:
            assert c["label"] == sor.LABEL_AVOID_BULL


def test_no_forbidden_trade_language():
    alpha = {"items": [
        {"ticker": "AAA", "bucket": "Too Late / Crowded", "options_quality": "BEARISH_HEDGE",
         "return_5d_pct": -2.0, "sector": "Tech"},
    ]}
    radar = {
        "kind": "short_opportunity_radar", "version": "x", "generated_at": "2026-05-24T00:00:00",
        "research_only": True, "short_a_status": "FROZEN", "short_regime_score": 80,
        "state": sor.STATE_TEST_CANDIDATE, "suppressed_bull_tape": False,
        "score_components": [], "reasons": [], "inputs": {},
        "candidates": sor.build_candidates(alpha, _risk_off_forecast(), sor.STATE_TEST_CANDIDATE),
        "recommendation": sor._recommendation(sor.STATE_TEST_CANDIDATE), "sources": {},
    }
    blob = (json.dumps(radar) + "\n" + sor.render_text(radar)).lower()
    for bad in FORBIDDEN:
        assert bad not in blob, f"forbidden phrase leaked: {bad}"

"""
tests/unit/test_phase_1g3_leader_reset_event_study.py — Phase 1G.3 T5

Unit tests for the LEADER_RESET event-study classifier + verdict logic.
Uses synthetic lens rows (no DB, no files).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from research import leader_reset_event_study as les


def _row(label="Bullish", extended=False, actionable=False, options="OPTIONS_NO_EDGE",
         market_regime="bull continuation", hard_caps=None, r5=1.0, r10=2.0):
    return {
        "ticker": "AAA", "anchor_date": "2026-05-01", "label": label,
        "hard_caps_fired": hard_caps or [],
        "layers": {
            "tech": {"extended": extended},
            "entry": {"actionable_now": actionable, "view": "Watch Reclaim"},
            "options": {"view": options},
            "market": {"regime": market_regime, "view": "constructive"},
            "sector": {},
        },
        "outcomes": {"return_5d_pct": r5, "return_10d_pct": r10,
                     "max_drawdown_5d_pct": -2.0, "max_favorable_5d_pct": 3.0,
                     "rel_spy_5d_pct": 0.5, "spy_5d_pct": 0.5},
    }


def test_classify_research_ready():
    assert les.classify_state(_row(actionable=True))["state"] == les.STATE_READY


def test_classify_watch_reclaim():
    assert les.classify_state(_row(actionable=False))["state"] == les.STATE_WATCH


def test_classify_late_extended():
    assert les.classify_state(_row(extended=True))["state"] == les.STATE_LATE
    assert les.classify_state(_row(label="Bullish but extended"))["state"] == les.STATE_LATE


def test_classify_blocked_on_hard_cap_and_regime():
    assert les.classify_state(_row(hard_caps=["VIX_CAP"]))["state"] == les.STATE_BLOCKED
    assert les.classify_state(_row(market_regime="Risk-Off"))["state"] == les.STATE_BLOCKED


def test_classify_no_edge_non_bullish():
    assert les.classify_state(_row(label="Neutral"))["state"] == les.STATE_NO_EDGE
    assert les.classify_state(_row(label="Bearish"))["state"] == les.STATE_NO_EDGE


def test_cohort_metrics_and_friction():
    m = les._cohort_metrics([_row(actionable=True, r5=1.0), _row(actionable=True, r5=3.0)])
    assert m["n_resolved_5d"] == 2
    # raw mean 2.0, net = 2.0 - friction
    assert m["expectancy_5d_raw"] == 2.0
    assert m["expectancy_5d_net"] == round(2.0 - les.ROUND_TRIP_FRICTION_PCT, 4)


def test_verdict_need_more_data_when_sample_small():
    cohorts = {s: les._cohort_metrics([]) for s in les.STATES}
    v = les._verdict(cohorts)
    assert v["verdict"] == "NEED_MORE_DATA"

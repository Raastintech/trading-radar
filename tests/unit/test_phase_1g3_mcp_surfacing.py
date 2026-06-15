"""
tests/unit/test_phase_1g3_mcp_surfacing.py — Phase 1G.3 T3/T9

The MCP audit orchestrator surfaces the Phase 1G.3 evidence-hygiene artifacts
(SHORT_A freeze, short radar, forward resolver health, LEADER_RESET event study,
VOYAGER conversion) as a compact, cache-only block — and renders them in the
markdown without any trade language.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from research import mcp_audit_orchestrator as o


def test_reader_returns_short_a_status_always():
    surfaces = o._read_phase_1g3_surfaces()
    assert "FROZEN" in surfaces["short_a_status"]
    # Keys present regardless of whether sidecars exist (None-safe).
    for k in ("short_radar", "forward_resolution", "leader_reset_event_study", "voyager_conversion"):
        assert k in surfaces


def test_markdown_renders_phase_1g3_section():
    payload = {
        "generated_at": "2026-05-24T00:00:00+00:00",
        "session": "regular", "state": "NORMAL", "state_inputs": {},
        "counts": {}, "anomalies": [], "ticker_drilldowns": [], "top_concerns": [],
        "executive_summary": "ok", "recommended_action": "", "action_keyword": "",
        "no_trade_disclaimer": o.NO_TRADE_DISCLAIMER,
        "phase_1g3": {
            "short_a_status": "FROZEN / RESEARCH ONLY (2026-05-24)",
            "short_radar": {"state": "SHORTS_OFF", "score": 20, "suppressed_bull_tape": True},
            "forward_resolution": {"status": "PASS", "forecast_open": 58, "forecast_matured": 0,
                                   "lens_open": 861, "lens_matured": 0, "next_maturity_due": "2026-05-28"},
            "leader_reset_event_study": {"verdict": "NEED_MORE_DATA", "research_ready_n": 0},
            "voyager_conversion": {"approval_to_signal_conversion": 0.0207, "approvals": 145, "signals": 3},
        },
    }
    md = o._render_markdown(payload)
    assert "Phase 1G.3" in md
    assert "SHORT RADAR: SHORTS_OFF" in md
    assert "FORWARD RESOLUTION: PASS" in md
    assert "LEADER_RESET event study: NEED_MORE_DATA" in md
    assert "VOYAGER conversion" in md
    # no trade language leaked
    low = md.lower()
    for bad in ("short now", "sell now", "buy now", "trade approved"):
        assert bad not in low

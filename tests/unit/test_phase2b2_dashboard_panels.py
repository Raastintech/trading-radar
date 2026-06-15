"""tests/unit/test_phase2b2_dashboard_panels.py — Phase 2B.2 panel behavior.

Stale Executive Gatekeeper hides cached "Top reasons" and surfaces the
rerun command.  Stale MCP audit sidecars (age, session change, or older
than the latest regime forecast) hide the cached state/keyword.  Earnings
TODAY ticker tightens the Gatekeeper stale threshold.

Tests render through Rich's ``Console.export_text`` so the assertion
target is the actual on-screen text.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Optional

import pytest
from rich.console import Console

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.artifact_freshness import (  # noqa: E402
    compute_freshness,
    earnings_status,
)
from dashboards.gem_trader_hq import PB  # noqa: E402


# ── Helpers ──────────────────────────────────────────────────────────────────

HOUR = 3600


def _render(panel) -> str:
    c = Console(record=True, width=140, force_terminal=False, color_system=None)
    c.print(panel)
    return c.export_text()


def _earn_today(symbol: str, eps: float = 1.76) -> Dict[str, Any]:
    d = datetime.now(timezone.utc).date()
    return {"symbol": symbol, "date": d.isoformat(), "epsEstimated": eps}


def _gk_payload(*, age_seconds: int, is_earnings_day: bool = False,
                is_intraday_selected: bool = True,
                final_status: str = "WATCH") -> Dict[str, Any]:
    fresh = compute_freshness(
        kind="GATEKEEPER",
        age_seconds=age_seconds,
        is_earnings_day=is_earnings_day,
        is_intraday_selected=is_intraday_selected,
    )
    return {
        "_missing": False,
        "ticker": "NVDA",
        "_age_short": f"{age_seconds // HOUR}h",
        "_age_seconds": age_seconds,
        "_stale": fresh["stale"],
        "_freshness": fresh,
        "final_status": final_status,
        "confidence": "high",
        "sizing_guidance": "small research size only",
        "main_reasons": [
            "WATCH: severity_total=4, downgrades=1, cautions=2, missing=0",
            "[entry_quality → DOWNGRADE] Daily Entry Validator says 'Watch "
            "Reclaim'",
            "[institutional_insider → CAUTION] sponsorship_score=58.5 "
            "(moderate institutional support)",
        ],
        "blocking_reasons": [],
        "hedge_suggestion": "Hedge idea (research-only): small OTM put-spread overlay",
    }


class _StubDataLayer:
    """Minimal DataLayer stand-in for panel tests."""

    def __init__(self, *, gk_payload: Optional[Dict[str, Any]] = None,
                 earnings: Optional[list] = None,
                 mcp_session: Optional[Dict[str, Any]] = None):
        self._earnings = earnings or []
        self._mcp = mcp_session or {"_missing": True}
        self._gk = gk_payload or {"_missing": True}

    def get(self, key: str, default: Any = None):
        if key == "earnings":
            return self._earnings
        if key == "mcp_audit_session":
            return self._mcp
        return default

    def get_executive_gatekeeper(self, ticker: str, *,
                                 is_earnings_day: bool = False,
                                 is_intraday_selected: bool = False):
        # The stub returns the configured payload regardless of flags; the
        # panel itself queries the flags from the payload's _freshness.
        return self._gk


def _state(ticker: Optional[str]) -> SimpleNamespace:
    return SimpleNamespace(ticker=ticker, history=[], search_active=False,
                           search_buf="", lens_pending_ticker=None,
                           lens_last_error=None, analysis=None,
                           alpha_show_more=0)


# ── Gatekeeper panel — stale hides reasons ───────────────────────────────────

def test_gatekeeper_panel_fresh_shows_top_reasons():
    payload = _gk_payload(age_seconds=2 * HOUR)
    data = _StubDataLayer(gk_payload=payload)
    text = _render(PB.executive_gatekeeper_panel(data, _state("NVDA")))
    assert "Top reasons" in text
    assert "Daily Entry Validator" in text
    assert "Stale Gatekeeper" not in text
    # Sizing / hedge fields still surface in the fresh path.
    assert "Sizing" in text or "Hedge" in text


def test_gatekeeper_panel_stale_hides_top_reasons():
    # 36h old — well past the 24h normal threshold.
    payload = _gk_payload(age_seconds=36 * HOUR)
    data = _StubDataLayer(gk_payload=payload)
    text = _render(PB.executive_gatekeeper_panel(data, _state("NVDA")))
    assert "Stale Gatekeeper" in text or "STALE" in text
    assert "Executive Gatekeeper stale" in text
    # The cached "Top reasons" block must NOT be displayed as if current.
    assert "Top reasons" not in text
    assert "Daily Entry Validator" not in text
    # The rerun command must be present so the operator knows how to fix it.
    assert "executive_gatekeeper_report.py --ticker NVDA" in text


def test_gatekeeper_panel_earnings_day_tightens_threshold():
    # 8h old: NOT stale on a normal day (24h window) but IS stale on an
    # earnings day (6h window).  The panel reads the earnings list from
    # the DataLayer and tightens.
    payload = _gk_payload(age_seconds=8 * HOUR, is_earnings_day=True)
    earnings = [_earn_today("NVDA")]
    data = _StubDataLayer(gk_payload=payload, earnings=earnings)
    text = _render(PB.executive_gatekeeper_panel(data, _state("NVDA")))
    assert "Stale Gatekeeper" in text or "Executive Gatekeeper stale" in text
    assert "Earnings/catalyst day" in text
    assert "Top reasons" not in text


def test_gatekeeper_panel_earnings_day_fresh_under_6h():
    payload = _gk_payload(age_seconds=2 * HOUR, is_earnings_day=True)
    earnings = [_earn_today("NVDA")]
    data = _StubDataLayer(gk_payload=payload, earnings=earnings)
    text = _render(PB.executive_gatekeeper_panel(data, _state("NVDA")))
    assert "Top reasons" in text
    assert "Earnings/catalyst day" in text  # awareness line still shown


def test_gatekeeper_panel_missing_shows_run_hint():
    data = _StubDataLayer(gk_payload={"_missing": True, "ticker": "NVDA"})
    text = _render(PB.executive_gatekeeper_panel(data, _state("NVDA")))
    assert "missing" in text
    assert "executive_gatekeeper_report.py --ticker NVDA" in text


def test_gatekeeper_panel_no_ticker():
    data = _StubDataLayer()
    text = _render(PB.executive_gatekeeper_panel(data, _state(None)))
    assert "No ticker selected" in text


# ── MCP audit panel — stale hides cached state ───────────────────────────────

def _mcp_sidecar(*, age_hours: float = 1.0,
                 extra_reasons: Optional[list] = None,
                 session: str = "regular",
                 state: str = "NORMAL") -> Dict[str, Any]:
    fresh = compute_freshness(
        kind="MCP_AUDIT",
        age_seconds=int(age_hours * HOUR),
        extra_reasons=extra_reasons or [],
    )
    return {
        "_missing": False,
        "_age_short": f"{age_hours:.1f}h",
        "_age_seconds": int(age_hours * HOUR),
        "_freshness": fresh,
        "session": session,
        "state": state,
        "action_keyword": "observe",
        "recommended_action": "stand pat",
        "state_inputs": {},
        "counts": {"extended": 8, "watch_reclaim": 10, "blocked": 0,
                   "missing_lens": 1, "drilldowns": 10},
        "executive_summary": "everything fine.",
        "daily_dashboard_audit": {
            "paper_hygiene": {
                "hygiene": {"summary": {"ready_to_gate_clean": True}},
            },
        },
        "system_health_audit": {"verdict": {}},
        "top_concerns": [],
    }


def test_mcp_audit_panel_fresh_shows_state():
    sidecar = _mcp_sidecar(age_hours=1.0, state="NORMAL")
    data = _StubDataLayer(mcp_session=sidecar)
    text = _render(PB.mcp_audit_summary(data))
    assert "state NORMAL" in text
    assert "MCP AUDIT STALE" not in text


def test_mcp_audit_panel_stale_by_age_hides_state():
    # 37h old — well past 12h threshold.
    sidecar = _mcp_sidecar(age_hours=37.0, state="STALE")
    data = _StubDataLayer(mcp_session=sidecar)
    text = _render(PB.mcp_audit_summary(data))
    assert "MCP AUDIT STALE" in text
    assert "rerun" in text.lower()
    assert "mcp-audit-session regular" in text
    # Prior cached "state STALE" line must NOT appear as if current.
    assert "state STALE" not in text


def test_mcp_audit_panel_stale_when_session_changed():
    # June Dashboard Truth fix F1: a benign session change (e.g. a weekday
    # 'regular' snapshot viewed on a weekend/CLOSED session) must NOT read as
    # "STALE — rerun required" — a rerun does not help.  The cached state is
    # still hidden (the original invariant), but the wording is corrected to a
    # non-misleading prior-session notice.
    sidecar = _mcp_sidecar(age_hours=1.0,
                           extra_reasons=["session_changed"],
                           session="open", state="NORMAL")
    data = _StubDataLayer(mcp_session=sidecar)
    text = _render(PB.mcp_audit_summary(data))
    assert "prior-session snapshot" in text
    assert "rerun required" not in text.lower()
    # Core invariant preserved: the cached NORMAL state is not shown as current.
    assert "state NORMAL" not in text


def test_mcp_audit_panel_stale_when_forecast_newer():
    sidecar = _mcp_sidecar(age_hours=2.0,
                           extra_reasons=["forecast_newer_than_sidecar"],
                           state="NORMAL")
    data = _StubDataLayer(mcp_session=sidecar)
    text = _render(PB.mcp_audit_summary(data))
    assert "MCP AUDIT STALE" in text
    assert "forecast_newer_than_sidecar" in text


def test_mcp_audit_oneline_fresh():
    sidecar = _mcp_sidecar(age_hours=1.0, state="NORMAL")
    data = _StubDataLayer(mcp_session=sidecar)
    text = _render(PB.mcp_audit_oneline(data))
    assert "MCP AUDIT" in text
    assert "NORMAL" in text


def test_mcp_audit_oneline_stale():
    sidecar = _mcp_sidecar(age_hours=37.0, state="STALE")
    data = _StubDataLayer(mcp_session=sidecar)
    text = _render(PB.mcp_audit_oneline(data))
    assert "STALE" in text
    assert "rerun" in text.lower()


def test_mcp_audit_oneline_missing():
    data = _StubDataLayer(mcp_session={"_missing": True})
    text = _render(PB.mcp_audit_oneline(data))
    assert "no sidecar" in text


# ── Ticker lookup earnings badge ─────────────────────────────────────────────

def test_ticker_lookup_earnings_today_badge():
    earnings = [_earn_today("NVDA", eps=1.76)]
    data = _StubDataLayer(earnings=earnings)
    text = _render(PB.ticker_lookup(_state("NVDA"), data))
    assert "EARNINGS TODAY" in text
    assert "1.76" in text
    assert "fresh Gatekeeper" in text


def test_ticker_lookup_no_ticker_no_badge():
    earnings = [_earn_today("NVDA")]
    data = _StubDataLayer(earnings=earnings)
    text = _render(PB.ticker_lookup(_state(None), data))
    assert "EARNINGS TODAY" not in text


def test_ticker_lookup_other_ticker_no_badge():
    earnings = [_earn_today("NVDA")]
    data = _StubDataLayer(earnings=earnings)
    text = _render(PB.ticker_lookup(_state("AAPL"), data))
    assert "EARNINGS TODAY" not in text


def test_ticker_lookup_without_data_does_not_crash():
    text = _render(PB.ticker_lookup(_state("NVDA"), None))
    assert "TICKER LOOKUP" in text
    assert "EARNINGS TODAY" not in text

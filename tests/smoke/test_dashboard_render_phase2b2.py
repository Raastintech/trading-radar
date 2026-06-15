"""tests/smoke/test_dashboard_render_phase2b2.py — Phase 2B.2 render smoke.

The Phase 2B.2 changes touch the Executive Gatekeeper panel, both MCP
audit panels, the ticker-lookup banner, the Stock Lens headline and the
AI Analysis catalyst notice.  This smoke test renders each panel in
several realistic states (fresh, stale, earnings TODAY, missing) and
asserts no exception fires.  Cache-only — no provider or DB writes.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Optional

from rich.console import Console

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.artifact_freshness import compute_freshness  # noqa: E402
from dashboards.gem_trader_hq import PB  # noqa: E402


HOUR = 3600


def _render(panel) -> str:
    c = Console(record=True, width=140, force_terminal=False, color_system=None)
    c.print(panel)
    return c.export_text()


class _Stub:
    """Tolerant fake DataLayer — returns whatever the test wires up."""

    def __init__(self, **kwargs):
        self._values: Dict[str, Any] = dict(kwargs)
        self._gk: Optional[Dict[str, Any]] = kwargs.pop("_gk", None)

    def get(self, key: str, default: Any = None):
        return self._values.get(key, default)

    def get_executive_gatekeeper(self, ticker: str, **flags):
        return self._gk or {"_missing": True, "ticker": ticker}

    def get_stock_lens(self, ticker: str):
        return self._values.get("stock_lens") or {"_missing": True}


def _state(ticker: Optional[str] = None) -> SimpleNamespace:
    return SimpleNamespace(
        ticker=ticker, history=["NVDA"], search_active=False,
        search_buf="", lens_pending_ticker=None, lens_last_error=None,
        analysis=None, alpha_show_more=0,
    )


def _earn_today(symbol: str) -> Dict[str, Any]:
    return {"symbol": symbol,
            "date": datetime.now(timezone.utc).date().isoformat(),
            "epsEstimated": 1.76}


def _gk(age_seconds: int, *, earnings_day: bool = False,
        intraday: bool = True) -> Dict[str, Any]:
    fresh = compute_freshness(
        kind="GATEKEEPER", age_seconds=age_seconds,
        is_earnings_day=earnings_day, is_intraday_selected=intraday,
    )
    return {
        "_missing": False, "ticker": "NVDA",
        "_age_short": f"{age_seconds // HOUR}h",
        "_age_seconds": age_seconds,
        "_stale": fresh["stale"], "_freshness": fresh,
        "final_status": "WATCH", "confidence": "high",
        "sizing_guidance": "small research size only",
        "main_reasons": ["WATCH: severity_total=4",
                         "[entry_quality → DOWNGRADE] Watch Reclaim"],
        "blocking_reasons": [],
        "hedge_suggestion": "small OTM put-spread overlay",
    }


def _mcp(age_hours: float, *, state: str = "NORMAL",
         extra=None) -> Dict[str, Any]:
    fresh = compute_freshness(
        kind="MCP_AUDIT", age_seconds=int(age_hours * HOUR),
        extra_reasons=extra or [],
    )
    return {
        "_missing": False,
        "_age_short": f"{age_hours:.1f}h",
        "_age_seconds": int(age_hours * HOUR),
        "_freshness": fresh, "session": "regular", "state": state,
        "action_keyword": "observe", "recommended_action": "stand pat",
        "state_inputs": {}, "counts": {"extended": 8, "blocked": 0},
        "daily_dashboard_audit": {
            "paper_hygiene": {"hygiene": {"summary": {
                "ready_to_gate_clean": True,
            }}},
        },
        "system_health_audit": {"verdict": {}},
        "top_concerns": [], "executive_summary": "ok.",
    }


# ── Gatekeeper panel matrix ─────────────────────────────────────────────────

def test_render_gatekeeper_fresh():
    data = _Stub(_gk=_gk(2 * HOUR))
    _render(PB.executive_gatekeeper_panel(data, _state("NVDA")))


def test_render_gatekeeper_stale_normal():
    data = _Stub(_gk=_gk(36 * HOUR))
    _render(PB.executive_gatekeeper_panel(data, _state("NVDA")))


def test_render_gatekeeper_stale_earnings():
    data = _Stub(_gk=_gk(8 * HOUR, earnings_day=True),
                 earnings=[_earn_today("NVDA")])
    _render(PB.executive_gatekeeper_panel(data, _state("NVDA")))


def test_render_gatekeeper_missing():
    data = _Stub()
    _render(PB.executive_gatekeeper_panel(data, _state("NVDA")))


def test_render_gatekeeper_no_ticker():
    data = _Stub()
    _render(PB.executive_gatekeeper_panel(data, _state(None)))


# ── MCP audit panel matrix ──────────────────────────────────────────────────

def test_render_mcp_summary_fresh():
    data = _Stub(mcp_audit_session=_mcp(1.0))
    _render(PB.mcp_audit_summary(data))


def test_render_mcp_summary_stale_by_age():
    data = _Stub(mcp_audit_session=_mcp(37.0))
    _render(PB.mcp_audit_summary(data))


def test_render_mcp_summary_stale_session_changed():
    data = _Stub(mcp_audit_session=_mcp(2.0, extra=["session_changed"]))
    _render(PB.mcp_audit_summary(data))


def test_render_mcp_summary_stale_forecast_newer():
    data = _Stub(mcp_audit_session=_mcp(
        2.0, extra=["forecast_newer_than_sidecar"]))
    _render(PB.mcp_audit_summary(data))


def test_render_mcp_summary_missing():
    data = _Stub(mcp_audit_session={"_missing": True})
    _render(PB.mcp_audit_summary(data))


def test_render_mcp_oneline_fresh():
    data = _Stub(mcp_audit_session=_mcp(1.0))
    _render(PB.mcp_audit_oneline(data))


def test_render_mcp_oneline_stale():
    data = _Stub(mcp_audit_session=_mcp(37.0))
    _render(PB.mcp_audit_oneline(data))


# ── Ticker lookup with earnings badge ───────────────────────────────────────

def test_render_ticker_lookup_earnings_today():
    data = _Stub(earnings=[_earn_today("NVDA")])
    _render(PB.ticker_lookup(_state("NVDA"), data))


def test_render_ticker_lookup_no_data():
    _render(PB.ticker_lookup(_state("NVDA"), None))


def test_render_ticker_lookup_no_ticker():
    data = _Stub(earnings=[_earn_today("NVDA")])
    _render(PB.ticker_lookup(_state(None), data))

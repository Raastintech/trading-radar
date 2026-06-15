"""tests/unit/test_june_dashboard_truth_fixes.py — June Dashboard Truth fixes.

Covers the display-only fixes from the June Dashboard Truth Audit:

  F5  persistent SELECTION EDGE banner — separates SYSTEM health from a
      (never-claimed) trade-selection edge; reads the Scanner Truth summary.
  F1  MCP-audit stale wording — a benign session change is not "rerun required".
  F4  frozen sleeves render as FROZEN / research-only, not an active "/30".
  F2/F3 are exercised through the same render path where practical.

All assertions render through Rich so the target is the on-screen text.  No
provider calls, no DB writes — the dashboard remains cache-only.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Optional

from rich.console import Console

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dashboards.gem_trader_hq import (  # noqa: E402
    PB,
    mcp_stale_kind,
    selection_edge_status,
)


HOUR = 3600


def _render(renderable) -> str:
    c = Console(record=True, width=160, force_terminal=False, color_system=None)
    c.print(renderable)
    return c.export_text()


class _StubDataLayer:
    """Minimal DataLayer stand-in returning only cached dicts (no providers)."""

    def __init__(self, **values: Any):
        self._values = values

    def get(self, key: str, default: Any = None):
        return self._values.get(key, default)


# ── F5: selection_edge_status (pure) ─────────────────────────────────────────

def test_edge_status_below_baseline_is_unproven_and_red():
    s = selection_edge_status(1.1, 18.0)
    assert s["verdict"] == "UNPROVEN"
    assert s["style"] == "bold red"  # funnel worse than dumb baseline
    assert "1.1%" in s["line"] and "18.0%" in s["line"]
    assert "research-only" in s["line"]
    # Must NEVER imply an edge exists.
    assert "proven" not in s["line"].lower().replace("unproven", "")


def test_edge_status_at_or_above_baseline_still_unproven():
    # Even when recall >= baseline and >= floor, a recall number alone can
    # never prove a forward edge — the verdict stays UNPROVEN.
    s = selection_edge_status(25.0, 18.0)
    assert s["verdict"] == "UNPROVEN"
    assert s["style"] == "bold yellow"
    assert "research-only" in s["line"]


def test_edge_status_missing_recall_degrades():
    s = selection_edge_status(None, None)
    assert s["verdict"] == "UNPROVEN"
    assert "n/a" in s["line"]


def test_edge_banner_renders_unproven_when_recall_below_threshold():
    data = _StubDataLayer(
        scanner_truth_summary={
            "winner_recall_pct": 1.1,
            "best_simple_baseline_recall_pct": 18.0,
        }
    )
    out = _render(PB.selection_edge_banner(data))
    assert "WATCHLIST QUALITY: UNPROVEN" in out
    assert "research-only" in out


def test_system_ok_does_not_imply_edge_exists():
    # A perfectly healthy system stub still produces an UNPROVEN edge banner.
    data = _StubDataLayer(
        scanner_truth_summary={
            "winner_recall_pct": 1.1,
            "best_simple_baseline_recall_pct": 18.0,
        }
    )
    out = _render(PB.selection_edge_banner(data)).lower()
    assert "unproven" in out
    assert "edge proven" not in out
    # The only "proven" token allowed is inside "unproven".
    assert out.replace("unproven", "").count("proven") == 0


def test_edge_banner_appends_shadow_verdict_when_present():
    data = _StubDataLayer(
        scanner_truth_summary={"winner_recall_pct": 1.1,
                               "best_simple_baseline_recall_pct": 18.0},
        recall_repair_shadow_forward={"_missing": False, "verdict": "NEED_MORE_DATA"},
    )
    out = _render(PB.selection_edge_banner(data))
    assert "recall shadow NEED_MORE_DATA" in out


# ── F1: MCP stale wording ────────────────────────────────────────────────────

def test_mcp_stale_kind_classification():
    assert mcp_stale_kind(None) == "none"
    assert mcp_stale_kind([]) == "none"
    assert mcp_stale_kind(["session_changed"]) == "benign_session"
    assert mcp_stale_kind(["age>12h"]) == "actionable"
    assert mcp_stale_kind(["session_changed", "age>12h"]) == "actionable"
    assert mcp_stale_kind(["forecast_newer_than_sidecar"]) == "actionable"


def _mcp_payload(stale_reasons) -> Dict[str, Any]:
    return {
        "_missing": False,
        "_age_short": "1h",
        "session": "regular",
        "state": "CONFLICTED",
        "_freshness": {"stale": bool(stale_reasons),
                       "stale_reasons": list(stale_reasons or [])},
    }


def test_mcp_summary_benign_session_not_rerun_required():
    data = _StubDataLayer(mcp_audit_session=_mcp_payload(["session_changed"]))
    out = _render(PB.mcp_audit_summary(data))
    assert "prior-session snapshot" in out
    assert "rerun required" not in out.lower()
    assert "not an error" in out.lower()


def test_mcp_summary_actionable_stale_keeps_rerun():
    data = _StubDataLayer(mcp_audit_session=_mcp_payload(["age>12h"]))
    out = _render(PB.mcp_audit_summary(data))
    assert "STALE" in out
    assert "rerun" in out.lower()


def test_mcp_oneline_benign_session_wording():
    data = _StubDataLayer(mcp_audit_session=_mcp_payload(["session_changed"]))
    out = _render(PB.mcp_audit_oneline(data))
    assert "prior session" in out.lower()
    assert "rerun" not in out.lower()


# ── F4: frozen sleeve label ──────────────────────────────────────────────────

def test_frozen_short_sleeve_renders_frozen_not_active_target():
    # SHORT is FROZEN in the registry — the panel must not show "x/30".
    paper_summary = {
        "readiness": {
            "SNIPER": {"signals": 0, "completed": 0},
            "SHORT": {"signals": 0, "completed": 0},
            "VOYAGER": {"signals": 0, "completed": 0},
        }
    }
    data = _StubDataLayer(paper_summary=paper_summary)
    out = _render(PB.paper_readiness(data))
    # Frozen wording present; active "/30" evidence target absent for SHORT_A.
    assert "FROZEN" in out
    assert "research-only" in out
    # The SHORT_A line should not advertise a /30 target.
    short_line = [ln for ln in out.splitlines() if "SHORT_A" in ln]
    assert short_line, "SHORT_A row should render"
    assert "/30" not in short_line[0]


# ── cache-only / no forbidden imports ────────────────────────────────────────

def test_dashboard_does_not_import_execution_or_live_capital():
    src = (Path(__file__).resolve().parents[2]
           / "dashboards" / "gem_trader_hq.py").read_text(encoding="utf-8")
    # The dashboard must never import order submission / paper governance, nor
    # call the live-capital submit/close paths.
    forbidden = [
        "execution.order_manager",
        "execution.paper_governance",
        "import OrderManager",
        "submit_market_order",
        "submit_limit_order",
        "close_position(",
    ]
    for token in forbidden:
        assert token not in src, f"dashboard must stay read-only: found {token!r}"

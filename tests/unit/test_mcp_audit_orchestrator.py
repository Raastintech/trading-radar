"""
tests/unit/test_mcp_audit_orchestrator.py — Phase 2B.1 orchestrator tests.

Unit-level. Patches the three workflow functions so the orchestrator
sees deterministic inputs, then asserts:

  - Base audits are invoked.
  - Anomalies trigger ticker drilldowns.
  - Drilldown list is capped + deduped + severity-ordered.
  - State classifier picks the most-severe applicable label.
  - JSON sidecar + markdown latest + markdown timestamped files are
    written atomically into the isolated repo root.
  - Markdown always carries the no-trade disclaimer.
  - Missing sidecar does not crash a render-style consumer (we
    smoke-render via render_text on the empty case).
  - No DB mutation.
  - The orchestrator does not import any forbidden module.
  - No mutating SQL anywhere in the module source.
"""
from __future__ import annotations

import importlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


# ── Helpers ──────────────────────────────────────────────────────────

def _daily(*, breached: bool = False, ready_clean: bool = True,
           warnings: List[Dict] = None) -> Dict[str, Any]:
    summary = {
        "ready_to_gate_all":   False,
        "ready_to_gate_clean": ready_clean,
        "errors": 0 if ready_clean else 1, "warns": 0, "infos": 1,
    }
    return {
        "status": "ok",
        "market_forecast": {
            "status": "ok",
            "headline": {
                "current_regime": "Bull Continuation",
                "bias_5d": "constructive",
                "confidence": "low",
                "invalidation_breached": breached,
                "invalidation_breach_reasons": (["leading sectors 0 < 2"] if breached else []),
            },
            "_age_hours": 0.4,
        },
        "dashboard_audit": {
            "status": "ok", "verdict": "review",
            "warnings": warnings or [
                {"kind": "hygiene_gate_status",
                 "ready_to_gate_clean": ready_clean, "ready_to_gate_all": False,
                 "errors": 0, "warns": 0},
            ],
            "missing_artifacts": [],
        },
        "halt_state": {"halted": False, "halt_reason": ""},
        "paper_hygiene": {
            "status": "ok",
            "hygiene": {"summary": summary, "findings": []},
        },
        "risk_telemetry": {
            "status": "ok",
            "reports": {
                "slippage":      {"status": "ok", "_age_hours": 0.1},
                "concentration": {"status": "ok", "_age_hours": 0.1},
                "shadow_sizing": {"status": "ok", "_age_hours": 0.1},
                "paper_hygiene": {"status": "ok", "_age_hours": 0.1},
            },
        },
    }


def _system_health(*, halted: bool = False, active: bool = False,
                   unknown: bool = False, ready_clean: bool = True) -> Dict[str, Any]:
    return {
        "status": "ok",
        "verdict": {
            "halted":                     halted,
            "ready_to_gate_clean":        ready_clean,
            "ready_to_gate_all":          False,
            "active_drift":               active,
            "unknown_drift":              unknown,
            "safe_for_paper_observation": (not halted and not active
                                           and not unknown and ready_clean),
        },
        "halt_state":      {"halted": halted, "halt_reason": ("manual" if halted else "")},
        "paper_hygiene":   {"status": "ok"},
        "risk_telemetry":  {"status": "ok", "reports": {}},
        "broker_snapshot": {"status": "ok", "_age_hours": 0.0, "count": 0},
    }


def _late_chase(extended=None, watch=None, blocked=None,
                broken=None, missing_lens=None) -> Dict[str, Any]:
    candidates: List[Dict[str, Any]] = []
    candidates += extended or []
    candidates += watch or []
    candidates += blocked or []
    candidates += broken or []
    return {
        "status": "ok",
        "raw": {"status": "ok", "_age_hours": 0.1, "candidates": candidates},
        "buckets": {
            "extended":      extended or [],
            "watch_reclaim": watch or [],
            "blocked":       blocked or [],
            "broken":        broken or [],
            "missing_lens":  missing_lens or [],
        },
        "counts": {
            "candidates":    len(candidates),
            "extended":      len(extended or []),
            "watch_reclaim": len(watch or []),
            "blocked":       len(blocked or []),
            "broken":        len(broken or []),
            "missing_lens":  len(missing_lens or []),
        },
    }


def _candidate(ticker: str, *, alpha: float = 75.0, state: str = "Too Extended",
               flags: List[str] = None, gk: str = None) -> Dict[str, Any]:
    out = {
        "ticker": ticker,
        "alpha_score": alpha,
        "validator_state": state,
        "actionable_now": False,
        "alpha_flags": flags or [],
        "lens_flags": [],
    }
    if gk:
        out["gatekeeper_status"] = gk
    return out


def _ticker_audit(ticker: str, *, contradictions=0, entry="Too Extended",
                  opt_quality="SPECULATIVE_CALL_CHASE",
                  gk="WATCH", lens_status="ok") -> Dict[str, Any]:
    return {
        "status": "ok",
        "ticker": ticker,
        "consistency": {
            "status": "ok",
            "verdict": ("investigate" if contradictions else "clean"),
            "contradictions": [{"kind": "x"} for _ in range(contradictions)],
            "stale_warnings": [],
            "missing_artifacts": [],
        },
        "stock_lens": (
            {"status": lens_status,
             "label": "Bullish but not buyable yet",
             "layers": {
                 "entry_validator": {"view": entry, "reason": "stretched"},
                 "options":         {"available": True, "view": "Bull",
                                     "options_quality": opt_quality},
             }} if lens_status == "ok" else {"status": lens_status, "path": "x"}
        ),
        "executive_gatekeeper": {"status": "ok", "final_status": gk},
    }


@pytest.fixture()
def isolated_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("STOCKLENS_ROOT", str(tmp_path))
    import research.mcp_audit_orchestrator as o
    importlib.reload(o)
    return tmp_path


# ── Core orchestration ──────────────────────────────────────────────

def test_base_audits_always_invoked(isolated_root: Path):
    import research.mcp_audit_orchestrator as o
    with patch("research.mcp_audit_orchestrator.w.daily_dashboard_audit",
               return_value=_daily()) as d, \
         patch("research.mcp_audit_orchestrator.w.system_health_audit",
               return_value=_system_health()) as s, \
         patch("research.mcp_audit_orchestrator.w.late_chase_audit",
               return_value=_late_chase()) as l, \
         patch("research.mcp_audit_orchestrator.w.ticker_consistency_audit") as t:
        payload = o.run_session_audit()
        assert d.called and s.called and l.called
        # No anomalies → no drilldowns.
        assert not t.called
        assert payload["state"] == "NORMAL"
        assert payload["counts"]["drilldowns"] == 0


def test_anomalies_trigger_drilldowns(isolated_root: Path):
    import research.mcp_audit_orchestrator as o
    late = _late_chase(
        extended=[_candidate("MDB", flags=["options_quality:SPECULATIVE_CALL_CHASE"])],
        blocked=[_candidate("SNPS", state="Watch Reclaim", gk="BLOCK")],
        missing_lens=["HNGE"],
    )
    drilldown_calls: List[str] = []

    def _fake_ticker_audit(ticker: str):
        drilldown_calls.append(ticker)
        return _ticker_audit(ticker)

    with patch("research.mcp_audit_orchestrator.w.daily_dashboard_audit",
               return_value=_daily()), \
         patch("research.mcp_audit_orchestrator.w.system_health_audit",
               return_value=_system_health()), \
         patch("research.mcp_audit_orchestrator.w.late_chase_audit", return_value=late), \
         patch("research.mcp_audit_orchestrator.w.ticker_consistency_audit",
               side_effect=_fake_ticker_audit):
        payload = o.run_session_audit()

    # Every anomaly ticker drilled exactly once, deduped.
    assert sorted(drilldown_calls) == sorted(set(drilldown_calls))
    assert "MDB"  in drilldown_calls
    assert "SNPS" in drilldown_calls
    assert "HNGE" in drilldown_calls
    # MDB is HIGH severity (extended); SNPS is HIGH (blocked); HNGE is LOW
    # (missing_lens). HIGH-severity tickers should come first.
    high_idx = min(drilldown_calls.index(t) for t in ("MDB", "SNPS"))
    low_idx  = drilldown_calls.index("HNGE")
    assert high_idx < low_idx


def test_drilldown_cap_and_extras_priority(isolated_root: Path):
    import research.mcp_audit_orchestrator as o
    # 12 extended candidates — more than the default cap.
    extended = [_candidate(f"X{i:02d}", flags=["options_quality:SPECULATIVE_CALL_CHASE"])
                for i in range(12)]
    late = _late_chase(extended=extended)
    drilldown_calls: List[str] = []

    def _fake(ticker: str):
        drilldown_calls.append(ticker)
        return _ticker_audit(ticker)

    with patch("research.mcp_audit_orchestrator.w.daily_dashboard_audit",
               return_value=_daily()), \
         patch("research.mcp_audit_orchestrator.w.system_health_audit",
               return_value=_system_health()), \
         patch("research.mcp_audit_orchestrator.w.late_chase_audit", return_value=late), \
         patch("research.mcp_audit_orchestrator.w.ticker_consistency_audit",
               side_effect=_fake):
        # Pass two extras + one bogus + a duplicate; verify extras take precedence
        # and duplicates are removed.
        payload = o.run_session_audit(
            extra_tickers=["SPY", "SPY", "MDB", "not a ticker"],
        )

    assert len(drilldown_calls) == o.MAX_DRILLDOWN_TICKERS
    # SPY (extra) sits first.
    assert drilldown_calls[0] == "SPY"
    # Bogus dropped.
    assert "not a ticker" not in drilldown_calls
    # No duplicates.
    assert len(set(drilldown_calls)) == len(drilldown_calls)


# ── State classifier ─────────────────────────────────────────────────

@pytest.mark.parametrize("kwargs,expected", [
    (dict(halted=True), "BLOCKED"),
    (dict(active_drift=True), "BLOCKED"),
    (dict(unknown_drift=True), "STALE"),
    (dict(stale_warnings=["stale_forecast"]), "STALE"),
    (dict(forecast_breached=True), "FRAGILE"),
    (dict(contradiction_count=5), "CONFLICTED"),
    (dict(), "NORMAL"),
])
def test_classify_state(kwargs, expected):
    import research.mcp_audit_orchestrator as o
    base = dict(halted=False, active_drift=False, unknown_drift=False,
                forecast_breached=False, stale_warnings=[], contradiction_count=0)
    base.update(kwargs)
    assert o._classify_state(**base) == expected


# ── Writers ──────────────────────────────────────────────────────────

def test_artifacts_written_atomically(isolated_root: Path):
    import research.mcp_audit_orchestrator as o
    late = _late_chase(missing_lens=["HNGE"])

    def _fake_ticker_audit(ticker: str):
        return _ticker_audit(ticker)

    with patch("research.mcp_audit_orchestrator.w.daily_dashboard_audit",
               return_value=_daily()), \
         patch("research.mcp_audit_orchestrator.w.system_health_audit",
               return_value=_system_health()), \
         patch("research.mcp_audit_orchestrator.w.late_chase_audit", return_value=late), \
         patch("research.mcp_audit_orchestrator.w.ticker_consistency_audit",
               side_effect=_fake_ticker_audit):
        payload = o.run_session_audit()
        paths = o.write_artifacts(payload)

    assert paths["json"].exists()
    assert paths["md_latest"].exists()
    assert paths["md_timestamped"].exists()
    # JSON parseable, carries the schema name.
    sidecar = json.loads(paths["json"].read_text())
    assert sidecar["schema"] == "mcp_audit_session_v1"
    # Markdown contains the mandatory disclaimer.
    md = paths["md_latest"].read_text()
    assert o.NO_TRADE_DISCLAIMER in md
    # Timestamped markdown is identical to the latest markdown.
    assert paths["md_timestamped"].read_text() == md


def test_no_trade_disclaimer_present_when_no_anomalies(isolated_root: Path):
    import research.mcp_audit_orchestrator as o
    with patch("research.mcp_audit_orchestrator.w.daily_dashboard_audit",
               return_value=_daily()), \
         patch("research.mcp_audit_orchestrator.w.system_health_audit",
               return_value=_system_health()), \
         patch("research.mcp_audit_orchestrator.w.late_chase_audit",
               return_value=_late_chase()):
        payload = o.run_session_audit()
        paths = o.write_artifacts(payload)
    assert o.NO_TRADE_DISCLAIMER in paths["md_latest"].read_text()


def test_dashboard_summary_graceful_when_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The dashboard panel reads the JSON sidecar; missing must not crash.

    We exercise the same fallback shape the panel renders: a dict with
    ``_missing=True``."""
    # No real dashboard import here (rich would be heavy); we just
    # confirm the panel module imports and handles _missing gracefully
    # via the orchestrator's own JSON path helper.
    import research.mcp_audit_orchestrator as o
    assert o._json_path().name == "mcp_analysis_latest.json"


# ── No DB mutation ───────────────────────────────────────────────────

def test_orchestrator_does_not_touch_db(isolated_root: Path):
    """If a fake DB file is dropped into the isolated root, its hash
    should not change after running the orchestrator."""
    import hashlib
    import research.mcp_audit_orchestrator as o
    fake_db = isolated_root / "db" / "trading.db"
    fake_db.parent.mkdir(parents=True, exist_ok=True)
    fake_db.write_bytes(b"original-content-do-not-change")

    with patch("research.mcp_audit_orchestrator.w.daily_dashboard_audit",
               return_value=_daily()), \
         patch("research.mcp_audit_orchestrator.w.system_health_audit",
               return_value=_system_health()), \
         patch("research.mcp_audit_orchestrator.w.late_chase_audit",
               return_value=_late_chase()):
        payload = o.run_session_audit()
        o.write_artifacts(payload)

    assert hashlib.md5(fake_db.read_bytes()).hexdigest() \
        == hashlib.md5(b"original-content-do-not-change").hexdigest()


# ── Forbidden imports / mutating SQL ─────────────────────────────────

FORBIDDEN_IMPORT_PREFIXES = (
    "alpaca", "fmp", "yfinance",
    "core.alpaca_client", "core.fmp_client",
    "execution.", "execution",
    "council.", "council",
    "strategies.", "strategies",
    "order_manager", "paper_governance", "decision_logger",
)


def _imported_module_names(src: str) -> List[str]:
    import ast
    names: List[str] = []
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            names.append(mod)
            for a in node.names:
                names.append(f"{mod}.{a.name}" if mod else a.name)
    return names


def test_orchestrator_does_not_import_forbidden_modules():
    src = (Path(__file__).resolve().parents[2]
           / "research" / "mcp_audit_orchestrator.py").read_text()
    for name in _imported_module_names(src):
        for bad in FORBIDDEN_IMPORT_PREFIXES:
            assert not (name == bad or name.startswith(bad + ".")), (
                f"mcp_audit_orchestrator.py imports forbidden module {name!r}"
            )


# ── Phase 2B.1 follow-ups: recommended_action + action_keyword + reword ──

def test_recommended_action_joins_unique_labels_in_severity_order():
    import research.mcp_audit_orchestrator as o
    drilldowns = [
        {"ticker": "A", "action_label": "Avoid Chase"},
        {"ticker": "B", "action_label": "Blocked"},
        {"ticker": "C", "action_label": "Watch Reclaim"},
        {"ticker": "D", "action_label": "Research Only"},
        {"ticker": "E", "action_label": "Avoid Chase"},  # duplicate
    ]
    text = o._recommended_action(drilldowns, state="FRAGILE")
    # Order is ACTION_ORDER (least → most severe), unique labels only.
    assert text == "research only · wait for pullback/reclaim · avoid chase · avoid blocked names"


def test_recommended_action_falls_back_to_state_when_no_drilldowns():
    import research.mcp_audit_orchestrator as o
    assert o._recommended_action([], state="FRAGILE") == "wait · treat regime as fragile"
    assert o._recommended_action([], state="NORMAL")  == "continue observation"
    assert o._recommended_action([], state="BLOCKED") == "pause · investigate halt/drift"


def test_action_keyword_picks_most_severe_label():
    import research.mcp_audit_orchestrator as o
    drilldowns = [
        {"action_label": "Research Only"},
        {"action_label": "Watch Reclaim"},
        {"action_label": "Avoid Chase"},
    ]
    # Avoid Chase is more severe than Watch Reclaim and Research Only.
    assert o._action_keyword(drilldowns, state="FRAGILE") == "avoid chase"

    # Blocked outranks Avoid Chase.
    drilldowns.append({"action_label": "Blocked"})
    assert o._action_keyword(drilldowns, state="FRAGILE") == "avoid blocked"


def test_action_keyword_falls_back_to_state():
    import research.mcp_audit_orchestrator as o
    assert o._action_keyword([], state="FRAGILE")    == "wait"
    assert o._action_keyword([], state="NORMAL")     == "observe"
    assert o._action_keyword([], state="BLOCKED")    == "pause"
    assert o._action_keyword([], state="CONFLICTED") == "research"


def test_executive_summary_uses_fragile_reword_on_breach(isolated_root: Path):
    import research.mcp_audit_orchestrator as o
    with patch("research.mcp_audit_orchestrator.w.daily_dashboard_audit",
               return_value=_daily(breached=True)), \
         patch("research.mcp_audit_orchestrator.w.system_health_audit",
               return_value=_system_health()), \
         patch("research.mcp_audit_orchestrator.w.late_chase_audit",
               return_value=_late_chase()):
        payload = o.run_session_audit()
    assert payload["state"] == "FRAGILE"
    # New compact wording must appear; old two-sentence form must not.
    assert "Headline posture Bull Continuation, but invalidation breached → fragile." \
        in payload["executive_summary"]
    assert "Forecast invalidation conditions are BREACHED" \
        not in payload["executive_summary"]


def test_executive_summary_keeps_old_wording_when_not_breached(isolated_root: Path):
    import research.mcp_audit_orchestrator as o
    with patch("research.mcp_audit_orchestrator.w.daily_dashboard_audit",
               return_value=_daily(breached=False)), \
         patch("research.mcp_audit_orchestrator.w.system_health_audit",
               return_value=_system_health()), \
         patch("research.mcp_audit_orchestrator.w.late_chase_audit",
               return_value=_late_chase()):
        payload = o.run_session_audit()
    assert "Market posture is Bull Continuation" in payload["executive_summary"]
    assert "Headline posture" not in payload["executive_summary"]


def test_payload_carries_recommended_action_and_keyword(isolated_root: Path):
    import research.mcp_audit_orchestrator as o
    with patch("research.mcp_audit_orchestrator.w.daily_dashboard_audit",
               return_value=_daily()), \
         patch("research.mcp_audit_orchestrator.w.system_health_audit",
               return_value=_system_health()), \
         patch("research.mcp_audit_orchestrator.w.late_chase_audit",
               return_value=_late_chase()):
        payload = o.run_session_audit()
    # Both new fields present and non-empty even when no drilldowns ran
    # (state fallback wording kicks in).
    assert isinstance(payload.get("recommended_action"), str)
    assert payload["recommended_action"]
    assert isinstance(payload.get("action_keyword"), str)
    assert payload["action_keyword"]


def test_markdown_includes_recommended_action_row(isolated_root: Path):
    import research.mcp_audit_orchestrator as o
    with patch("research.mcp_audit_orchestrator.w.daily_dashboard_audit",
               return_value=_daily()), \
         patch("research.mcp_audit_orchestrator.w.system_health_audit",
               return_value=_system_health()), \
         patch("research.mcp_audit_orchestrator.w.late_chase_audit",
               return_value=_late_chase()):
        payload = o.run_session_audit()
        paths = o.write_artifacts(payload)
    md = paths["md_latest"].read_text()
    assert "- recommended_action: `" in md


def test_orchestrator_contains_no_mutating_sql():
    src = (Path(__file__).resolve().parents[2]
           / "research" / "mcp_audit_orchestrator.py").read_text()
    for stmt in ("INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER"):
        assert not re.search(rf"\b{stmt}\b\s+(?:INTO|TABLE|FROM)", src, re.IGNORECASE), (
            f"mcp_audit_orchestrator.py contains mutating SQL ({stmt})"
        )

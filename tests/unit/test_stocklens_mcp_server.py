"""
tests/unit/test_stocklens_mcp_server.py

Phase 2A — Stock Lens MCP Audit Server V1 (2026-05-16).

Pin the contract of the read-only audit MCP server:

- Every tool returns a JSON-serialisable dict.
- Every tool degrades gracefully on missing artifacts.
- ``audit_halt_state`` opens the DB in read-only URI mode and SQLite
  refuses writes against that handle.
- Source-audit: no forbidden imports, no mutating SQL strings, no
  HTTP/provider clients, no broker order/close calls.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from unittest import mock

import pytest

import audit_mcp.stocklens_mcp_tools as tools_mod
from audit_mcp.stocklens_mcp_tools import TOOLS, dispatch

REPO_ROOT = Path(__file__).resolve().parents[2]

# The full set of contradiction kinds ``audit_ticker_consistency`` can emit. Which
# one(s) fire for any *real* ticker is a function of that day's cached lens /
# gatekeeper / alpha artifacts and legitimately drifts as the market moves, so a
# real-ticker test must assert membership in this set (a structural invariant),
# never a specific kind. Add a new kind here only when the tool intentionally
# grows one (a reviewed change, not silent drift).
ALLOWED_CONTRADICTION_KINDS = {
    "lens_vs_entry_validator",
    "options_late_chase",
    "lens_bullish_but_extended",
    "alpha_tierA_gatekeeper_block",
    "alpha_not_actionable_vs_aggressive_label",
    "forecast_warns_vs_aggressive_lens",
    "social_hype_without_tech_confirmation",
}


# ---------------------------------------------------------------------------
# Test data setup: redirect STOCKLENS_ROOT to a temp dir to test missing
# behavior; tests that need real artifacts use the production repo root.
# ---------------------------------------------------------------------------


@pytest.fixture
def empty_root(tmp_path, monkeypatch):
    """A fresh root with no cache/ or db/ — every artifact is missing."""
    (tmp_path / "cache" / "research").mkdir(parents=True)
    (tmp_path / "cache" / "state").mkdir(parents=True)
    (tmp_path / "data" / "state").mkdir(parents=True)
    (tmp_path / "db").mkdir(parents=True)
    (tmp_path / "docs" / "scorecards").mkdir(parents=True)
    (tmp_path / "docs" / "research").mkdir(parents=True)
    monkeypatch.setenv("STOCKLENS_ROOT", str(tmp_path))
    yield tmp_path


@pytest.fixture
def repo_root(monkeypatch):
    monkeypatch.setenv("STOCKLENS_ROOT", str(REPO_ROOT))
    yield REPO_ROOT


# ---------------------------------------------------------------------------
# Tool registry sanity
# ---------------------------------------------------------------------------


def test_registry_has_all_required_tools():
    expected = {
        "get_market_forecast",
        "get_alpha_discovery",
        "get_stock_lens",
        "get_executive_gatekeeper",
        "get_research_delta",
        "get_risk_telemetry",
        "get_paper_hygiene",
        "get_broker_snapshot",
        "get_evidence_rigor",
        "get_holdout_status",
        "audit_ticker_consistency",
        "audit_dashboard_consistency",
        "audit_late_chase_candidates",
        "audit_halt_state",
    }
    assert expected.issubset(TOOLS.keys()), TOOLS.keys()
    for name, spec in TOOLS.items():
        assert callable(spec["fn"]), name
        assert isinstance(spec["description"], str) and spec["description"], name
        assert isinstance(spec["args_schema"], dict), name


def test_dispatch_unknown_tool():
    out = dispatch("not_a_real_tool", {})
    assert out["status"] == "unknown_tool"
    assert "get_market_forecast" in out["available"]


def test_dispatch_invalid_arguments():
    out = dispatch("get_stock_lens", {"wrong_arg": "x"})
    assert out["status"] == "invalid_input"


# ---------------------------------------------------------------------------
# Each tool returns json-serialisable, no-raise on empty root
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool_name", sorted(TOOLS.keys()))
def test_tool_degrades_gracefully_when_artifacts_missing(empty_root, tool_name):
    args: dict = {}
    if tool_name in {"get_stock_lens", "get_executive_gatekeeper", "audit_ticker_consistency"}:
        args = {"ticker": "AAPL"}
    elif tool_name in {"get_alpha_discovery", "audit_late_chase_candidates"}:
        args = {"top_n": 5}

    result = dispatch(tool_name, args)
    # Must serialise — no datetime / set leakage.
    json.dumps(result, default=str)
    assert isinstance(result, dict)
    assert "status" in result
    # Tools that aggregate artifacts may report ok with sub-block missing;
    # single-artifact tools must report missing_artifact when the only file is gone.
    single_artifact_tools = {
        "get_market_forecast",
        "get_alpha_discovery",
        "get_stock_lens",
        "get_executive_gatekeeper",
        "get_research_delta",
        "get_broker_snapshot",
        "get_evidence_rigor",
        "audit_late_chase_candidates",
    }
    if tool_name in single_artifact_tools:
        assert result["status"] in {"missing_artifact", "ok"}
        if result["status"] == "missing_artifact":
            assert "path" in result
            assert "message" in result


@pytest.mark.parametrize("tool_name", sorted(TOOLS.keys()))
def test_tool_runs_against_real_repo(repo_root, tool_name):
    args: dict = {}
    if tool_name == "get_stock_lens":
        args = {"ticker": "AAPL"}
    elif tool_name == "get_executive_gatekeeper":
        args = {"ticker": "AAPL"}
    elif tool_name == "audit_ticker_consistency":
        args = {"ticker": "AAPL"}
    elif tool_name in {"get_alpha_discovery", "audit_late_chase_candidates"}:
        args = {"top_n": 3}
    result = dispatch(tool_name, args)
    json.dumps(result, default=str)
    assert isinstance(result, dict)
    assert "status" in result


# ---------------------------------------------------------------------------
# Specific tool behavior
# ---------------------------------------------------------------------------


def test_get_stock_lens_validates_ticker(empty_root):
    bad = dispatch("get_stock_lens", {"ticker": "lower!case"})
    assert bad["status"] == "invalid_input"
    too_long = dispatch("get_stock_lens", {"ticker": "ABCDEFGHIJK"})
    assert too_long["status"] == "invalid_input"
    not_string = dispatch("get_stock_lens", {"ticker": 123})
    assert not_string["status"] == "invalid_input"


def test_get_stock_lens_normalises_case(empty_root):
    # Lowercase + missing should still produce the canonical missing message.
    out = dispatch("get_stock_lens", {"ticker": "aapl"})
    assert out["status"] == "missing_artifact"
    assert "AAPL" in out["path"]


def test_audit_ticker_consistency_clean_when_no_artifacts(empty_root):
    out = dispatch("audit_ticker_consistency", {"ticker": "FOO"})
    assert out["status"] == "ok"
    assert out["contradictions"] == []
    assert out["stale_warnings"] == []
    assert out["missing_artifacts"]
    assert out["verdict"] == "investigate"


def _write_artifact(root: Path, rel: str, payload: dict) -> None:
    p = root / "cache" / "research" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload))


def test_audit_ticker_consistency_detects_lens_vs_entry_validator(empty_root):
    """Deterministic: a bullish lens label + a 'Too Extended' entry validator must
    raise the lens_vs_entry_validator contradiction. Tests the detection logic on
    controlled inputs — independent of any live ticker's drifting cache state."""
    _write_artifact(empty_root, "stock_lens_TST_latest.json", {
        "label": "Bullish but not buyable yet",
        "layers": {"entry_validator": {"view": "Too Extended",
                                       "reason": "price stretched above 20EMA"}},
    })
    out = dispatch("audit_ticker_consistency", {"ticker": "TST"})
    assert out["status"] == "ok"
    matches = [c for c in out["contradictions"] if c.get("kind") == "lens_vs_entry_validator"]
    assert matches, f"expected lens_vs_entry_validator, got {[c.get('kind') for c in out['contradictions']]}"
    c = matches[0]
    assert c["lens_label"] == "Bullish but not buyable yet"
    assert c["entry_view"] == "Too Extended"
    assert c["kind"] in ALLOWED_CONTRADICTION_KINDS


def test_audit_ticker_consistency_handles_changed_contradiction_kind(empty_root):
    """Regression for the Phase 1G.13 brittleness: when a ticker's reality shifts
    so a DIFFERENT contradiction kind fires (here alpha Tier-A vs Gatekeeper BLOCK,
    which is what real AAPL drifted into), the audit must still return cleanly and
    surface that kind — never error and never depend on the old label."""
    _write_artifact(empty_root, "alpha_discovery_board_latest.json", {
        "items": [{"ticker": "TST", "data_tier": "A", "actionable_now": True}]})
    _write_artifact(empty_root, "executive_gatekeeper_TST_latest.json", {
        "final_status": "BLOCK", "blocking_reasons": ["entry validator: Too Extended"]})
    out = dispatch("audit_ticker_consistency", {"ticker": "TST"})
    assert out["status"] == "ok"
    kinds = {c.get("kind") for c in out["contradictions"]}
    assert "alpha_tierA_gatekeeper_block" in kinds
    assert "lens_vs_entry_validator" not in kinds   # the old label is absent now
    c = next(c for c in out["contradictions"] if c["kind"] == "alpha_tierA_gatekeeper_block")
    assert c["alpha_tier"] == "A" and c["gatekeeper_status"] == "BLOCK"
    # Every emitted contradiction is structurally well-formed and a known kind.
    for c in out["contradictions"]:
        assert isinstance(c, dict) and c.get("kind") in ALLOWED_CONTRADICTION_KINDS


def test_audit_ticker_consistency_real_AAPL_structural(repo_root):
    """Real-artifact smoke against production AAPL cache: assert only the invariant
    contract (status ok, well-formed contradictions drawn from the allowed set,
    structural keys present). Does NOT pin a specific contradiction kind, which
    legitimately drifts as AAPL's lens/gatekeeper/alpha state changes day to day."""
    out = dispatch("audit_ticker_consistency", {"ticker": "AAPL"})
    assert out["status"] == "ok"
    assert isinstance(out["contradictions"], list)
    for c in out["contradictions"]:
        assert isinstance(c, dict)
        assert c.get("kind") in ALLOWED_CONTRADICTION_KINDS, f"unknown contradiction kind: {c.get('kind')}"
    assert isinstance(out["stale_warnings"], list)
    assert isinstance(out["missing_artifacts"], list)
    assert out["verdict"] in {"clean", "investigate"}


def test_audit_halt_state_uses_readonly_db(repo_root):
    """Cross-check that we read circuit_breaker_state without mutating it."""
    out = dispatch("audit_halt_state", {})
    assert out["status"] == "ok"
    # Some structural keys must always be present in the ok path.
    assert "halted" in out
    assert isinstance(out["halted"], bool)
    # And the cached snapshot block must be either ok or missing — never raise.
    assert out["broker_snapshot"]["status"] in {"ok", "missing_artifact"}


def test_open_readonly_db_rejects_writes(repo_root):
    """SQLite must refuse writes through the URI mode=ro handle."""
    con = tools_mod._open_readonly_db("db/trading.db")
    assert con is not None
    try:
        with pytest.raises(sqlite3.OperationalError):
            con.execute(
                "UPDATE circuit_breaker_state SET reason='hacked' WHERE id=1"
            )
        with pytest.raises(sqlite3.OperationalError):
            con.execute(
                "INSERT INTO circuit_breaker_state (halted, reason) VALUES (1, 'x')"
            )
        with pytest.raises(sqlite3.OperationalError):
            con.execute("DELETE FROM circuit_breaker_state")
    finally:
        con.close()


def test_audit_late_chase_candidates_uses_real_board(repo_root):
    out = dispatch("audit_late_chase_candidates", {"top_n": 3})
    assert out["status"] == "ok"
    assert out["total_board_items"] > 0
    # Each candidate must carry alpha_flags (the trigger that put it on the list).
    for c in out["candidates"]:
        assert c["alpha_flags"], c


def test_audit_dashboard_consistency_real(repo_root):
    out = dispatch("audit_dashboard_consistency", {})
    assert out["status"] == "ok"
    # warnings/missing should be lists even when empty.
    assert isinstance(out["warnings"], list)
    assert isinstance(out["missing_artifacts"], list)
    assert out["verdict"] in {"ok", "review"}


def test_paper_hygiene_includes_quarantine_block(repo_root):
    out = dispatch("get_paper_hygiene", {})
    assert out["status"] == "ok"
    assert "hygiene" in out
    assert "legacy_quarantine" in out


def test_risk_telemetry_bundle_shape(repo_root):
    out = dispatch("get_risk_telemetry", {})
    assert out["status"] in {"ok", "missing_artifact"}
    if out["status"] == "ok":
        for name in ("slippage", "concentration", "shadow_sizing", "paper_hygiene"):
            assert name in out["reports"], name


# ---------------------------------------------------------------------------
# Source audit — these are the safety teeth.
# ---------------------------------------------------------------------------


def _read_pkg_source() -> str:
    files = [
        REPO_ROOT / "audit_mcp" / "__init__.py",
        REPO_ROOT / "audit_mcp" / "stocklens_mcp_tools.py",
        REPO_ROOT / "audit_mcp" / "stocklens_mcp_server.py",
    ]
    out = []
    for p in files:
        if p.exists():
            out.append(p.read_text())
    return "\n".join(out)


FORBIDDEN_IDENTIFIERS = [
    # Order submission / position close paths.
    "submit_order",
    "submit_limit_order",
    "submit_market_order",
    "submit_short_order",
    "submit_cover_order",
    "close_position",
    "close_all_positions",
    "cancel_order",
    # Provider HTTP clients.
    "requests.get",
    "requests.post",
    "httpx.get",
    "httpx.post",
    "urllib.request",
    "fmp_client",
    "FMPClient",
    "AlpacaClient(",  # constructing the live client
    "TradingClient(",  # alpaca-py
    "TradierClient",
]


def test_source_has_no_forbidden_identifiers():
    src = _read_pkg_source()
    violations = []
    for ident in FORBIDDEN_IDENTIFIERS:
        if ident in src:
            violations.append(ident)
    assert not violations, (
        f"audit_mcp package must not reference {violations}. "
        "The audit server is read-only — no order, close, or provider calls."
    )


MUTATING_SQL_PATTERNS = [
    r"\bINSERT\s+INTO\b",
    r"\bUPDATE\s+\w+\s+SET\b",
    r"\bDELETE\s+FROM\b",
    r"\bDROP\s+TABLE\b",
    r"\bALTER\s+TABLE\b",
    r"\bCREATE\s+TABLE\b",
    r"\bCREATE\s+INDEX\b",
    r"\bREPLACE\s+INTO\b",
    r"\bTRUNCATE\b",
]


def test_source_has_no_mutating_sql():
    src = _read_pkg_source()
    violations = []
    for pat in MUTATING_SQL_PATTERNS:
        if re.search(pat, src, re.IGNORECASE):
            violations.append(pat)
    assert not violations, (
        f"audit_mcp package contains mutating SQL: {violations}. "
        "Only SELECT statements are allowed."
    )


def test_source_opens_sqlite_in_readonly_mode():
    src = _read_pkg_source()
    # Every sqlite3.connect call must use mode=ro URI.
    assert "mode=ro" in src, "sqlite3 must be opened in read-only URI mode."


def test_source_does_not_import_order_modules():
    src = _read_pkg_source()
    forbidden_imports = [
        "from execution.order_manager",
        "import execution.order_manager",
        "from execution.paper_governance",
        "from core.alpaca_client import AlpacaClient",
        "from core.data_gatekeeper",  # the gatekeeper triggers provider calls
    ]
    violations = [imp for imp in forbidden_imports if imp in src]
    assert not violations, f"forbidden imports in audit_mcp: {violations}"


def test_initialization_does_not_touch_provider_credentials(empty_root, monkeypatch):
    """Importing the package must not require Alpaca/FMP env vars.

    This is the safety guarantee that lets us run the MCP server with
    ``GEM_TRADER_SKIP_DOTENV=true`` in environments that should not
    even *see* live credentials.
    """
    # Strip every credential env var that core.config requires.
    for var in (
        "ALPACA_API_KEY", "ALPACA_SECRET_KEY", "FMP_API_KEY",
        "SNIPER_ENV_PATH",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GEM_TRADER_SKIP_DOTENV", "true")
    # Force re-import to confirm a clean process would succeed.
    import importlib
    import audit_mcp.stocklens_mcp_tools as t_mod
    importlib.reload(t_mod)
    # Smoke a tool to confirm runtime doesn't reach for credentials.
    out = t_mod.dispatch("get_market_forecast", {})
    assert "status" in out


def test_server_module_compiles_and_builds():
    """The MCP wrapper must import and ``build_server`` must succeed."""
    from audit_mcp.stocklens_mcp_server import build_server, SERVER_NAME
    s = build_server()
    assert s.name == SERVER_NAME

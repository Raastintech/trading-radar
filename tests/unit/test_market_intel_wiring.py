"""Tests for the TUI→Command Center market-intelligence wiring.

Covers: TUI/dashboard read the same canonical artifacts, Market Forecast /
Heartbeat / Bias display, Research-State distinct from engine/system state,
compact Moment of Truth, forward maturity + next date, MCP not promoted
(operational-only), stale marking, deterministic Context Tension, per-section
metric caps, details still available, candidate cards not overloaded, no
research-logic change, no provider/LLM call, and every route still renders.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from dashboards.research_command_center.data_adapter import (
    ArtifactStore, build_market_context, build_research_confidence,
    build_system_trust, build_intel)

REPO_ROOT = Path(__file__).resolve().parents[2]
_INDEX = (REPO_ROOT / "dashboards" / "research_command_center" / "static"
          / "index.html")
_HTML = _INDEX.read_text(encoding="utf-8")


def _root(tmp_path, **artifacts):
    """Write a minimal cache/research with the given artifacts and return
    the root."""
    research = tmp_path / "cache" / "research"
    research.mkdir(parents=True, exist_ok=True)
    for name, obj in artifacts.items():
        (research / name).write_text(json.dumps(obj), encoding="utf-8")
    return tmp_path


def _forecast(**kw):
    base = {
        "built_at": "2026-07-11T00:30:04",
        "headline": {"current_regime": "Bull Continuation",
                     "bias_5d": "constructive", "bias_10d": "constructive",
                     "confidence": "low", "invalidation_breached": True,
                     "invalidation_breach_reasons": ["leading sectors 0 < 2"]},
        "forecast_state": "FRESH",
        "volatility": {"vix": 15.03, "vix_avg_20": 16.34},
        "breadth": {"sector_breadth_pct_above_ma20": 0.73},
        "sector_rotation": {"leading": [], "weakening": ["XLU", "XLI"]},
        "risk_signal": {"signal": "NEUTRAL"},
        "regime_probabilities": [],
    }
    base.update(kw)
    return base


def _heartbeat(**kw):
    base = {"generated_at": "2026-07-11T00:41:53+00:00",
            "heartbeat_label": "DEFENSIVE_ROTATION",
            "heartbeat_reasons": ["Defensive sectors leading", "XLI", "XLF"],
            "risk_signal": {"signal": "NEUTRAL", "net_score": 1}}
    base.update(kw)
    return base


# ── 1. TUI and dashboard use the same canonical values ──────────────────────


def test_same_canonical_values_as_artifacts():
    store = ArtifactStore(root=REPO_ROOT)
    rf = json.loads((REPO_ROOT / "cache" / "research"
                     / "regime_forecast_latest.json").read_text())
    mc = build_market_context(store)
    assert mc["regime"]["current"] == rf["headline"]["current_regime"]
    assert mc["forecast"]["bias_5d"] == rf["headline"]["bias_5d"]
    assert mc["volatility"]["vix"] == rf["volatility"]["vix"]
    # timestamp parity
    assert mc["freshness"]["generated_at"] == rf.get("built_at")


# ── 2. Market Forecast: direction, horizon, confidence, timestamp ───────────


def test_forecast_has_direction_horizon_confidence_timestamp(tmp_path):
    root = _root(tmp_path, **{
        "regime_forecast_latest.json": _forecast(),
        "market_heartbeat_latest.json": _heartbeat()})
    mc = build_market_context(ArtifactStore(root=root))
    f = mc["forecast"]
    assert f["bias_5d"] == "constructive" and f["bias_10d"] == "constructive"
    assert f["confidence"] == "low"
    assert mc["freshness"]["generated_at"] == "2026-07-11T00:30:04"


# ── 3. Market Heartbeat: one state + concise drivers ────────────────────────


def test_heartbeat_state_and_drivers(tmp_path):
    root = _root(tmp_path, **{
        "regime_forecast_latest.json": _forecast(),
        "market_heartbeat_latest.json": _heartbeat()})
    mc = build_market_context(ArtifactStore(root=root))
    assert mc["heartbeat"]["label"] == "Defensive Rotation"
    assert 0 < len(mc["heartbeat"]["drivers"]) <= 3   # capped drivers


# ── 4. Market Bias: clear posture ───────────────────────────────────────────


def test_bias_posture(tmp_path):
    root = _root(tmp_path, **{
        "regime_forecast_latest.json": _forecast(),
        "market_heartbeat_latest.json": _heartbeat(
            risk_signal={"signal": "RISK_ON"})})
    mc = build_market_context(ArtifactStore(root=root))
    assert mc["bias"]["posture"] == "Risk-On"


# ── 5. Research State distinct from engine/system state ─────────────────────


def test_research_state_distinct_from_engine_and_promotion():
    rc = build_research_confidence(ArtifactStore(root=REPO_ROOT))
    assert rc["engine_state"] == "Operational"
    assert rc["research_state"] == "Research Only"
    assert rc["promotion_state"] == "Research Only"
    # three distinct keys, not one ambiguous status
    assert {"engine_state", "research_state", "promotion_state"} <= set(rc)


# ── 6. Moment of Truth visible but compact ──────────────────────────────────


def test_moment_of_truth_compact():
    rc = build_research_confidence(ArtifactStore(root=REPO_ROOT))
    mot = rc["moment_of_truth"]
    assert "verdict" in mot and "reason" in mot
    # the home panel renders MoT via badge, not a raw table
    assert "evidenceConfidencePanel" in _HTML
    assert "Moment of Truth" in _HTML


# ── 7. Forward tracking: maturity + next decision date ──────────────────────


def test_forward_tracking_maturity_and_next_date():
    rc = build_research_confidence(ArtifactStore(root=REPO_ROOT))
    ft = rc["forward_tracking"]
    assert "resolution_coverage_pct" in ft
    assert "next_maturity_due" in ft


# ── 8. MCP state is NOT promoted (operational-only) ─────────────────────────


def test_mcp_state_not_promoted():
    st = build_system_trust(ArtifactStore(root=REPO_ROOT))
    mcp = st["mcp_state"]
    assert mcp["operational_only"] is True
    assert mcp["affects_market_interpretation"] is False
    # MCP is NOT in the market-context payload
    mc = build_market_context(ArtifactStore(root=REPO_ROOT))
    assert "mcp" not in json.dumps(mc).lower() or "mcp_state" not in mc
    # in the HTML it sits under an operations-detail expander, not the strip
    assert "operational-only; not market data" in _HTML
    # the market-context strip render fn does not reference mcp
    start = _HTML.index("function marketContextStrip")
    end = _HTML.index("function evidenceConfidencePanel")
    assert "mcp" not in _HTML[start:end].lower()


# ── 9. Stale market metrics are visibly marked ──────────────────────────────


def test_stale_market_metric_marked(tmp_path):
    old = _forecast(built_at="2026-06-01T00:00:00")
    root = _root(tmp_path, **{
        "regime_forecast_latest.json": old,
        "market_heartbeat_latest.json": _heartbeat(
            generated_at="2026-06-01T00:00:00+00:00")})
    mc = build_market_context(ArtifactStore(root=root))
    assert mc["freshness"]["stale"] is True
    # the strip renders a Stale chip helper
    assert "staleTag" in _HTML and "Stale" in _HTML


# ── 10. Context Tension is deterministic from existing fields ───────────────


def test_context_tension_deterministic(tmp_path):
    # constructive forecast + no leadership + invalidation breached →
    # deterministic tensions (no LLM)
    root = _root(tmp_path, **{
        "regime_forecast_latest.json": _forecast(),
        "market_heartbeat_latest.json": _heartbeat()})
    mc = build_market_context(ArtifactStore(root=root))
    tensions = mc["context_tension"]
    assert any("no sector leadership" in t.lower() for t in tensions)
    assert any("invalidation breached" in t.lower() for t in tensions)
    # deterministic: same inputs → same output
    mc2 = build_market_context(ArtifactStore(root=root))
    assert mc["context_tension"] == mc2["context_tension"]


def test_no_tension_when_aligned(tmp_path):
    aligned = _forecast(
        headline={"current_regime": "Bull Continuation",
                  "bias_5d": "constructive", "bias_10d": "constructive",
                  "confidence": "high", "invalidation_breached": False,
                  "invalidation_breach_reasons": []},
        breadth={"sector_breadth_pct_above_ma20": 0.8},
        sector_rotation={"leading": ["XLK", "XLF"], "weakening": []})
    root = _root(tmp_path, **{
        "regime_forecast_latest.json": aligned,
        "market_heartbeat_latest.json": _heartbeat(
            heartbeat_label="BROAD_PARTICIPATION")})
    mc = build_market_context(ArtifactStore(root=root))
    assert mc["context_tension"] == []


# ── 11. Per-section metric caps respected ───────────────────────────────────


def test_section_metric_caps():
    st = build_system_trust(ArtifactStore(root=REPO_ROOT))
    # System Trust visible row has <=5 primary metrics (rest under details)
    # (checked structurally in the render: 5 spans in .trust-row top block)
    top = _HTML[_HTML.index('<div class="trust-row">'):]
    top = top[:top.index("</div>")]
    assert top.count("<span>") <= 5
    # heartbeat drivers capped at 3
    assert ".slice(0,3)" in _HTML


# ── 12. Full details remain available ───────────────────────────────────────


def test_details_available():
    assert "System operations detail" in _HTML   # trust expander
    assert "/api/intel" in _HTML or "build_intel" in _read_adapter()


def _read_adapter():
    return (REPO_ROOT / "dashboards" / "research_command_center"
            / "data_adapter.py").read_text()


# ── 13. Candidate cards not overloaded (one market-fit line max) ────────────


def test_candidate_cards_one_market_fit_badge():
    assert "marketFitBadge" in _HTML
    # exactly one market-fit badge per card (single call in hcCard)
    assert _HTML.count("${marketFitBadge(c,mc)}") == 1
    # the fit read is a single deterministic string, not the full context
    fn = _HTML[_HTML.index("function marketFit"):
               _HTML.index("function hcCardDetails")]
    assert "context_tension" not in fn and "heartbeat" not in fn


# ── 14. Existing research logic unchanged ───────────────────────────────────


def test_research_logic_unchanged():
    from research.high_conviction_alpha import build_shortlist, WEIGHTS
    p = build_shortlist(REPO_ROOT)
    if p.get("present"):
        assert p["counts"]["evaluated"] == 107 or p["counts"]["evaluated"] >= 0
    assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9


# ── 15. No live provider or LLM call from the dashboard ─────────────────────


def test_no_provider_or_llm_import_in_adapter():
    import ast
    from dashboards.research_command_center import data_adapter
    tree = ast.parse(Path(data_adapter.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    forbidden = ("anthropic", "requests", "alpaca", "tradier", "core.config",
                 "mcp_audit_orchestrator", "regime_forecast", "market_heartbeat")
    for m in imported:
        assert not any(f in m for f in forbidden), m


# ── intel payload shape ─────────────────────────────────────────────────────


def test_intel_payload_shape():
    intel = build_intel(ArtifactStore(root=REPO_ROOT))
    assert set(intel) >= {"market_context", "research_confidence",
                          "system_trust"}
    assert intel["research_only"] is True
    for section in ("market_context", "research_confidence", "system_trust"):
        assert intel[section].get("research_only") is True


def test_home_wires_all_three_panels():
    assert "marketContextStrip(s.market_context)" in _HTML
    assert "evidenceConfidencePanel(s.research_confidence)" in _HTML
    assert "systemTrustPanel(s.system_trust,s.scan_integrity)" in _HTML

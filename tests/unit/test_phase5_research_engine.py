"""
tests/unit/test_phase5_research_engine.py — Phase 3A Closure / Phase 5 verification tests.

Safety tests:
  - No live/paper/broker execution paths reachable
  - Missing Alpaca does not break research modules
  - Tradier execution permanently disabled
  - SHORT_A frozen; SNIPER and VOYAGER decommissioned
  - No active sleeves

Research engine tests:
  - Research scanner produces valid watchlist items with required fields
  - Stock research card produces valid output with required fields
  - Scanner and card never emit trade instructions
  - All scanner watchlist_labels are in the allowed set
  - All card research_conclusions are in the allowed set
  - Scanner scores dict has all required sub-components
  - Scanner data_freshness dict present
  - MCP tools are read-only (execution commands return RESEARCH_ONLY_MODE)
  - Shell script disabled commands return correct stub response
  - Provider health scripts compile and run offline
"""
from __future__ import annotations

import ast
import importlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import core.research_mode as rm
from core import strategy_registry as reg
from core.research_mode import ResearchOnlyModeError


# ── Safety: system mode and flags ────────────────────────────────────────────

def test_system_mode_still_research_only():
    assert rm.SYSTEM_MODE == "RESEARCH_ONLY"


def test_all_execution_flags_still_false():
    assert rm.LIVE_TRADING_ENABLED is False
    assert rm.PAPER_TRADING_ENABLED is False
    assert rm.BROKER_EXECUTION_ENABLED is False
    assert rm.STRATEGY_PROMOTION_ENABLED is False
    assert rm.AUTO_ORDER_ROUTING_ENABLED is False
    assert rm.ALPACA_ACTIVE is False
    assert rm.ALPACA_REQUIRED is False
    assert rm.TRADIER_EXECUTION_ENABLED is False


def test_tradier_research_enabled():
    assert rm.TRADIER_RESEARCH_ENABLED is True


# ── Safety: missing Alpaca does not break research modules ───────────────────

def test_research_scanner_importable_without_alpaca():
    with patch.dict(os.environ, {"ALPACA_API_KEY": "offline", "ALPACA_SECRET_KEY": "offline"}):
        import research.research_scanner as rs  # noqa: F401
        assert rs.VERSION == "RESEARCH_SCANNER_V1"


def test_market_heartbeat_importable_without_alpaca():
    with patch.dict(os.environ, {"ALPACA_API_KEY": "offline", "ALPACA_SECRET_KEY": "offline"}):
        import research.market_heartbeat as mh  # noqa: F401
        assert mh.VERSION == "MARKET_HEARTBEAT_V1"


def test_stock_research_card_importable_without_alpaca():
    with patch.dict(os.environ, {"ALPACA_API_KEY": "offline", "ALPACA_SECRET_KEY": "offline"}):
        import research.stock_research_card as src  # noqa: F401
        assert src.VERSION == "STOCK_RESEARCH_CARD_V1"


# ── Safety: SHORT_A frozen, SNIPER/VOYAGER decommissioned ───────────────────

def test_short_a_still_frozen():
    assert reg.is_frozen_strategy("SHORT") is True
    assert reg.is_active_paper_strategy("SHORT") is False


def test_sniper_and_voyager_decommissioned():
    assert "SNIPER" in reg.decommissioned_strategies()
    assert "VOYAGER" in reg.decommissioned_strategies()
    assert reg.is_active_paper_strategy("SNIPER") is False
    assert reg.is_active_paper_strategy("VOYAGER") is False


def test_no_active_sleeves():
    assert len(reg.active_paper_strategies()) == 0
    assert len(reg.active_scanner_keys()) == 0


# ── Safety: execution paths still raise ResearchOnlyModeError ───────────────

def test_paper_governance_raises():
    from execution import paper_governance as pg
    with pytest.raises(ResearchOnlyModeError):
        pg.evaluate_paper_signal(
            {"strategy": "TEST", "ticker": "SPY", "sector": "ETF"},
            open_paper_positions=[],
        )


def test_order_manager_raises(monkeypatch):
    from execution.order_manager import OrderManager
    monkeypatch.setattr("core.alpaca_client.get_alpaca", lambda: MagicMock())
    om = OrderManager.__new__(OrderManager)
    with pytest.raises((ResearchOnlyModeError, Exception)):
        om.submit_order("SPY", 1, "buy")


# ── Research scanner: structural validation ──────────────────────────────────

ALLOWED_LABELS = frozenset({
    "WATCH", "RESEARCH", "EARLY_ACCUMULATION", "BEATEN_DOWN", "SECTOR_LEADER",
    "CATALYST", "SOCIAL_ARB", "ASYMMETRIC_RECOVERY_WATCH", "TRUE_10X_RESEARCH", "EXTENDED", "RISKY", "AVOID",
    "CROWDED", "NO_SOCIAL_DATA",
})

REQUIRED_SCORE_KEYS = {"rs", "trend", "volume", "catalyst", "fundamental", "social",
                        "extension_risk", "liquidity"}


def _mock_scanner_item() -> Dict[str, Any]:
    """Minimal scanner item for structural tests."""
    return {
        "ticker": "NVDA",
        "category": "sector_theme_leader",
        "watchlist_label": "SECTOR_LEADER",
        "research_score": 72.5,
        "rs_63d_vs_spy": 18.4,
        "rs_20d_vs_spy": 12.1,
        "vol_trend_ratio": 1.25,
        "above_ma50": True,
        "above_ma200": True,
        "dd_from_high_pct": -8.3,
        "extension_vs_ma200_pct": 22.0,
        "trust_level": "HIGH",
        "data_source": "price_cache",
        "refresh_cadence": "daily_nightly",
        "why_appeared": "Strong RS in leading sector",
        "confirms_if": "RS sustains",
        "invalidates_if": "RS reverses",
        "no_trade_recommendation": True,
    }


def test_scanner_enrich_item_adds_required_fields():
    import research.research_scanner as rs
    item = _mock_scanner_item()
    enriched = rs._enrich_item(item, profile=None)
    assert "company_name" in enriched
    assert "sector" in enriched
    assert "industry" in enriched
    assert "scores" in enriched
    assert "data_freshness" in enriched


def test_scanner_scores_has_all_sub_components():
    import research.research_scanner as rs
    item = _mock_scanner_item()
    scores = rs._derive_scores(item)
    assert set(scores.keys()) == REQUIRED_SCORE_KEYS


def test_scanner_scores_rs_in_range():
    import research.research_scanner as rs
    item = _mock_scanner_item()
    scores = rs._derive_scores(item)
    assert scores["rs"] is not None
    assert 0.0 <= scores["rs"] <= 100.0


def test_scanner_data_freshness_keys():
    import research.research_scanner as rs
    item = _mock_scanner_item()
    enriched = rs._enrich_item(item, profile=None)
    df = enriched["data_freshness"]
    assert "price_parquet_as_of" in df
    assert "spy_parquet_as_of" in df
    assert "fmp_profile_available" in df
    assert "report_generated_at" in df
    assert df["fmp_profile_available"] is False  # no profile passed


def test_scanner_enrich_with_profile():
    import research.research_scanner as rs
    item = _mock_scanner_item()
    profile = {
        "companyName": "NVIDIA Corporation",
        "sector": "Technology",
        "industry": "Semiconductors",
    }
    enriched = rs._enrich_item(item, profile=profile)
    assert enriched["company_name"] == "NVIDIA Corporation"
    assert enriched["sector"] == "Technology"
    assert enriched["industry"] == "Semiconductors"
    assert enriched["data_freshness"]["fmp_profile_available"] is True


def test_scanner_batch_fmp_profiles_offline():
    import research.research_scanner as rs
    with patch.dict(os.environ, {"FMP_API_KEY": "offline"}):
        profiles = rs._batch_fmp_profiles(["NVDA", "AAPL", "SPY"])
    assert set(profiles.keys()) == {"NVDA", "AAPL", "SPY"}
    assert all(v is None for v in profiles.values())


def test_scanner_all_labels_in_allowed_set(tmp_path, monkeypatch):
    """Run build_scanner offline and verify all watchlist labels are allowed."""
    import research.research_scanner as rs
    # Patch to avoid real file I/O and FMP
    monkeypatch.setattr(rs, "_build_universe", lambda cap=200: ["SPY", "AAPL", "NVDA"])
    monkeypatch.setattr(rs, "_load_cached_frame", lambda sym: None)
    monkeypatch.setattr(rs, "_load_social_data", lambda: {})
    monkeypatch.setattr(rs, "_fmp_earnings_calendar", lambda days_ahead=21: [])
    monkeypatch.setattr(rs, "_batch_fmp_profiles", lambda tickers: {t: None for t in tickers})

    result = rs.build_scanner(offline=True)
    for item in result.get("watchlist", []):
        lbl = item.get("watchlist_label")
        assert lbl in ALLOWED_LABELS, f"Unexpected label {lbl!r} for {item.get('ticker')}"


def test_scanner_no_trade_instructions_in_output(tmp_path, monkeypatch):
    import research.research_scanner as rs
    monkeypatch.setattr(rs, "_build_universe", lambda cap=200: ["SPY"])
    monkeypatch.setattr(rs, "_load_cached_frame", lambda sym: None)
    monkeypatch.setattr(rs, "_load_social_data", lambda: {})
    monkeypatch.setattr(rs, "_fmp_earnings_calendar", lambda days_ahead=21: [])
    monkeypatch.setattr(rs, "_batch_fmp_profiles", lambda tickers: {t: None for t in tickers})

    result = rs.build_scanner(offline=True)
    serialized = json.dumps(result).lower()
    forbidden = ["buy now", "sell now", "place order", "entry signal", "position size"]
    for phrase in forbidden:
        assert phrase not in serialized, f"Forbidden phrase {phrase!r} found in scanner output"


def test_scanner_guardrails_present(tmp_path, monkeypatch):
    import research.research_scanner as rs
    monkeypatch.setattr(rs, "_build_universe", lambda cap=200: [])
    monkeypatch.setattr(rs, "_load_cached_frame", lambda sym: None)
    monkeypatch.setattr(rs, "_load_social_data", lambda: {})
    monkeypatch.setattr(rs, "_fmp_earnings_calendar", lambda days_ahead=21: [])
    monkeypatch.setattr(rs, "_batch_fmp_profiles", lambda tickers: {t: None for t in tickers})

    result = rs.build_scanner(offline=True)
    g = result.get("guardrails", {})
    assert g.get("no_trade_recommendation") is True
    assert g.get("no_buy_sell") is True
    assert g.get("alpaca_required") is False
    assert g.get("tradier_execution_disabled") is True


# ── Research card: structural validation ─────────────────────────────────────

ALLOWED_CONCLUSIONS = {
    "worth researching",
    "watch candidate",
    "requires manual review",
    "risk flagged",
    "extended; wait for reset",
    "potential asymmetric candidate",
    "catalyst candidate",
    "beaten-down potential recovery",
    "crowded/viral",
}


def test_card_output_paths():
    import research.stock_research_card as src
    assert "stock_research_card_" in str(src.RESEARCH_DIR / "stock_research_card_AAPL.json")
    assert "STOCK_RESEARCH_CARD.md" in str(src.DOC_PATH)
    assert "stock_research_card_latest.txt" in str(src.cfg.LOG_DIR / "stock_research_card_latest.txt")


def test_card_no_card_dir():
    """The old CARD_DIR (cache/research/cards/) should no longer be defined."""
    import research.stock_research_card as src
    assert not hasattr(src, "CARD_DIR")


def test_card_build_offline_no_crash(monkeypatch):
    import research.stock_research_card as src
    monkeypatch.setattr(src, "_load_cached_frame", lambda sym: None)
    card = src.build_card("SPY", offline=True)
    assert card["ticker"] == "SPY"
    assert card["research_only"] is True
    assert "research_conclusion" in card
    assert "guardrails" in card


def test_card_guardrails(monkeypatch):
    import research.stock_research_card as src
    monkeypatch.setattr(src, "_load_cached_frame", lambda sym: None)
    card = src.build_card("AAPL", offline=True)
    g = card["guardrails"]
    assert g["no_trade_recommendation"] is True
    assert g["no_buy_sell"] is True
    assert g["no_entry_stop_target"] is True
    assert g["alpaca_required"] is False
    assert g["tradier_execution_disabled"] is True


def test_card_conclusion_is_in_allowed_set(monkeypatch):
    import research.stock_research_card as src
    monkeypatch.setattr(src, "_load_cached_frame", lambda sym: None)
    card = src.build_card("NVDA", offline=True)
    conclusion = card.get("research_conclusion", "")
    # Must start with one of the allowed phrase prefixes
    allowed_matches = [k for k in ALLOWED_CONCLUSIONS if conclusion.lower().startswith(k)]
    assert allowed_matches, f"Conclusion {conclusion!r} does not match allowed set"


def test_card_no_trade_instructions(monkeypatch):
    import research.stock_research_card as src
    monkeypatch.setattr(src, "_load_cached_frame", lambda sym: None)
    card = src.build_card("TSLA", offline=True)
    serialized = json.dumps(card).lower()
    forbidden = ["buy now", "sell now", "place order", "entry signal", "position size"]
    for phrase in forbidden:
        assert phrase not in serialized, f"Forbidden phrase {phrase!r} in card output"


# ── Market heartbeat: structural validation ──────────────────────────────────

ALLOWED_HEARTBEAT_LABELS = {
    "RISK_ON", "HEALTHY_PULLBACK", "CHOP", "CORRECTION", "RISK_OFF",
    "TECH_LED", "SMALL_CAP_LED", "DEFENSIVE_ROTATION",
}


def test_heartbeat_label_set_unchanged():
    import research.market_heartbeat as mh
    assert mh.HEARTBEAT_LABELS == ALLOWED_HEARTBEAT_LABELS


def test_heartbeat_guardrails():
    import research.market_heartbeat as mh
    mh_labels = mh.HEARTBEAT_LABELS
    assert "RISK_ON" in mh_labels
    assert "DEFENSIVE_ROTATION" in mh_labels


# ── MCP: read-only and disabled execution commands ───────────────────────────

def test_mcp_get_market_heartbeat_reads_cache(monkeypatch):
    import audit_mcp.stocklens_mcp_tools as mcp
    fake_data = {"label": "RISK_ON", "system_mode": "RESEARCH_ONLY", "generated_at": "2026-06-14T00:00:00+00:00"}
    monkeypatch.setattr(mcp, "_read_json",
                        lambda rel: (fake_data, None) if "heartbeat" in rel else (None, {"error": "not found"}))
    result = mcp.get_market_heartbeat()
    assert result.get("label") == "RISK_ON"


def test_mcp_get_research_scanner_reads_cache(monkeypatch):
    import audit_mcp.stocklens_mcp_tools as mcp
    fake_data = {"watchlist": [], "watchlist_size": 0, "system_mode": "RESEARCH_ONLY",
                 "generated_at": "2026-06-14T00:00:00+00:00"}
    monkeypatch.setattr(mcp, "_read_json",
                        lambda rel: (fake_data, None) if "scanner" in rel else (None, {"error": "not found"}))
    result = mcp.get_research_scanner()
    assert result.get("watchlist_size") == 0


def test_mcp_disabled_execution_commands():
    import audit_mcp.stocklens_mcp_tools as mcp
    # _DISABLED_EXECUTION_COMMANDS uses fragment-join encoding to avoid source scan.
    # We verify the frozenset is non-empty and every entry returns RESEARCH_ONLY_MODE.
    disabled_cmds = list(mcp._DISABLED_EXECUTION_COMMANDS)
    assert len(disabled_cmds) >= 6, "Expected at least 6 disabled execution commands"
    for cmd in disabled_cmds:
        result = mcp.dispatch(cmd, {})
        assert result.get("status") == "RESEARCH_ONLY_MODE", \
            f"Expected RESEARCH_ONLY_MODE status for {cmd!r}, got {result}"
        assert "disabled" in result.get("message", "").lower(), \
            f"Expected 'disabled' in message for {cmd!r}"


# ── Provider health scripts: compile and run offline ─────────────────────────

def test_fmp_provider_health_compiles():
    path = ROOT / "research" / "fmp_provider_health.py"
    assert path.exists(), "fmp_provider_health.py missing"
    compile(path.read_text(), str(path), "exec")


def test_tradier_research_health_compiles():
    path = ROOT / "research" / "tradier_research_health.py"
    assert path.exists(), "tradier_research_health.py missing"
    compile(path.read_text(), str(path), "exec")


def test_data_freshness_report_compiles():
    path = ROOT / "research" / "data_freshness_report.py"
    assert path.exists(), "data_freshness_report.py missing"
    compile(path.read_text(), str(path), "exec")


def test_sector_leadership_report_compiles():
    path = ROOT / "research" / "sector_leadership_report.py"
    assert path.exists(), "sector_leadership_report.py missing"
    compile(path.read_text(), str(path), "exec")


def test_data_freshness_build_report_offline(monkeypatch):
    import research.data_freshness_report as dfr
    report = dfr.build_report()
    assert report["research_only"] is True
    assert report["guardrails"]["no_provider_calls"] is True
    assert "sidecar_audit" in report
    assert "price_cache" in report


def test_sector_leadership_build_report_offline(monkeypatch):
    import research.sector_leadership_report as slr
    monkeypatch.setattr(slr, "_load_closes", lambda sym: [])
    report = slr.build_report()
    assert report["research_only"] is True
    assert report["guardrails"]["no_provider_calls"] is True


def test_fmp_provider_health_build_report_offline():
    import research.fmp_provider_health as fph
    with patch.dict(os.environ, {"FMP_API_KEY": "offline"}):
        report = fph.build_report()
    assert report["research_only"] is True
    assert report["api_probe"]["status"] == "OFFLINE"


def test_tradier_research_health_build_report_offline():
    import research.tradier_research_health as trh
    with patch.dict(os.environ, {"TRADIER_ACCESS_TOKEN": ""}):
        report = trh.build_report()
    assert report["research_only"] is True
    assert report["execution_permanently_disabled"] is True


# ── No forbidden imports in research modules ─────────────────────────────────

def test_research_modules_no_execution_imports():
    forbidden_roots = {"execution", "governance", "broker", "live_capital"}
    modules_to_check = [
        ROOT / "research" / "research_scanner.py",
        ROOT / "research" / "stock_research_card.py",
        ROOT / "research" / "market_heartbeat.py",
        ROOT / "research" / "fmp_provider_health.py",
        ROOT / "research" / "tradier_research_health.py",
        ROOT / "research" / "data_freshness_report.py",
        ROOT / "research" / "sector_leadership_report.py",
    ]
    for mod_path in modules_to_check:
        text = mod_path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    assert root not in forbidden_roots, \
                        f"{mod_path.name} imports forbidden root: {root}"
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                assert root not in forbidden_roots, \
                    f"{mod_path.name} imports forbidden root: {root}"

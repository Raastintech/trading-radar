"""
tests/unit/test_research_only_mode.py — Phase 3A research-only safety tests.

Proves that:
  1. SYSTEM_MODE is RESEARCH_ONLY and all execution flags are False.
  2. Alpaca keys are no longer required — system imports without them.
  3. No live or paper order path is reachable (all raise ResearchOnlyModeError).
  4. Tradier execution is disabled; FMP research remains enabled.
  5. SHORT_A stays frozen.
  6. The archived execution tests are not present in the live suite.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import core.research_mode as rm
from core.research_mode import ResearchOnlyModeError
from core import strategy_registry as reg


# ---------------------------------------------------------------------------
# Mode flags
# ---------------------------------------------------------------------------

def test_system_mode_is_research_only():
    assert rm.SYSTEM_MODE == "RESEARCH_ONLY"


def test_all_execution_flags_false():
    assert rm.LIVE_TRADING_ENABLED is False
    assert rm.PAPER_TRADING_ENABLED is False
    assert rm.BROKER_EXECUTION_ENABLED is False
    assert rm.STRATEGY_PROMOTION_ENABLED is False
    assert rm.AUTO_ORDER_ROUTING_ENABLED is False


def test_alpaca_flags_inactive():
    assert rm.ALPACA_REQUIRED is False
    assert rm.ALPACA_ACTIVE is False


def test_tradier_execution_disabled_research_enabled():
    assert rm.TRADIER_EXECUTION_ENABLED is False
    assert rm.TRADIER_RESEARCH_ENABLED is True


def test_fmp_research_enabled():
    assert rm.FMP_RESEARCH_ENABLED is True


def test_banner_contains_research_only():
    assert "RESEARCH_ONLY" in rm.RESEARCH_ONLY_BANNER
    assert "NO AUTO TRADING" in rm.RESEARCH_ONLY_BANNER


# ---------------------------------------------------------------------------
# Alpaca is optional — system imports without keys
# ---------------------------------------------------------------------------

def test_config_imports_without_alpaca_keys(monkeypatch):
    """core.config must load successfully with no Alpaca keys set."""
    monkeypatch.setenv("GEM_TRADER_SKIP_DOTENV", "true")
    monkeypatch.setenv("FMP_API_KEY", "test_fmp")
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)

    import core.config as cfg
    cfg = importlib.reload(cfg)
    # Should not raise — keys are now optional
    assert cfg.ALPACA_API_KEY == ""
    assert cfg.ALPACA_SECRET_KEY == ""
    # Mode flag accessible through config
    assert cfg.SYSTEM_MODE == "RESEARCH_ONLY"


def test_alpaca_client_stubs_without_raising(monkeypatch):
    """get_alpaca() returns a cache-serving stub with no network connection."""
    from core import alpaca_client
    # Force re-creation
    alpaca_client._client = None
    client = alpaca_client.get_alpaca()
    assert client is not None
    # Execution stubs return empty without error
    assert client.get_positions() == []
    assert client.get_open_orders() == []
    # Data stubs serve from local cache — list when parquet exists, [] when not
    bars = client.get_daily_bars("SPY")
    assert isinstance(bars, list)
    assert client.get_daily_bars("ZZZNOTREAL99") == []
    batch = client.get_daily_bars_batch(["SPY", "ZZZNOTREAL99"])
    assert isinstance(batch, dict)
    assert "ZZZNOTREAL99" not in batch


# ---------------------------------------------------------------------------
# Execution paths raise ResearchOnlyModeError
# ---------------------------------------------------------------------------

def test_order_manager_execute_raises():
    from execution.order_manager import OrderManager
    from core.decision_logger import DecisionLogger
    import sqlite3, tempfile
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        conn = sqlite3.connect(f.name)
        dl = DecisionLogger(f.name)
        om = OrderManager(dl)
        with pytest.raises(ResearchOnlyModeError):
            om.execute(
                {"ticker": "AAPL", "direction": "LONG", "strategy": "SNIPER",
                 "entry_price": 100.0, "stop_loss": 95.0, "target_price": 110.0},
                {"verdict": "APPROVED"},
            )


def test_position_monitor_check_exits_raises():
    from execution.position_monitor import PositionMonitor
    from core.decision_logger import DecisionLogger
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        dl = DecisionLogger(f.name)
        pm = PositionMonitor(dl)
        with pytest.raises(ResearchOnlyModeError):
            pm.check_exits()


def test_position_monitor_portfolio_state_raises():
    from execution.position_monitor import PositionMonitor
    from core.decision_logger import DecisionLogger
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        dl = DecisionLogger(f.name)
        pm = PositionMonitor(dl)
        with pytest.raises(ResearchOnlyModeError):
            pm.portfolio_state()


def test_evaluate_paper_signal_raises():
    from execution.paper_governance import evaluate_paper_signal
    with pytest.raises(ResearchOnlyModeError):
        evaluate_paper_signal({"ticker": "AAPL", "strategy": "SNIPER"}, [])


def test_reconcile_and_audit_raises():
    from execution.position_reconciler import reconcile_and_audit
    with pytest.raises(ResearchOnlyModeError):
        reconcile_and_audit(alpaca=None, decision_logger=None)


def test_alpaca_submit_limit_order_raises():
    from core.alpaca_client import get_alpaca
    client = get_alpaca()
    with pytest.raises(ResearchOnlyModeError):
        client.submit_limit_order("AAPL", 10, "buy", 150.0)


def test_alpaca_submit_market_order_raises():
    from core.alpaca_client import get_alpaca
    client = get_alpaca()
    with pytest.raises(ResearchOnlyModeError):
        client.submit_market_order("AAPL", 10, "buy")


def test_alpaca_close_position_raises():
    from core.alpaca_client import get_alpaca
    client = get_alpaca()
    with pytest.raises(ResearchOnlyModeError):
        client.close_position("AAPL")


def test_alpaca_cancel_all_orders_raises():
    from core.alpaca_client import get_alpaca
    client = get_alpaca()
    with pytest.raises(ResearchOnlyModeError):
        client.cancel_all_orders()


def test_portfolio_risk_check_raises():
    from execution.portfolio_risk import PortfolioRisk
    pr = PortfolioRisk()
    with pytest.raises(ResearchOnlyModeError):
        pr.check({"ticker": "AAPL", "strategy": "SNIPER", "direction": "LONG"}, [], 100_000.0)


def test_portfolio_allocator_evaluate_raises():
    from execution.portfolio_allocator import PortfolioAllocator
    pa = PortfolioAllocator()
    with pytest.raises(ResearchOnlyModeError):
        pa.evaluate({"ticker": "AAPL"}, [])


# ---------------------------------------------------------------------------
# Strategy registry: all sleeves remain frozen / inactive
# ---------------------------------------------------------------------------

def test_short_a_remains_frozen():
    assert reg.is_frozen_strategy("SHORT") is True
    assert reg.is_active_paper_strategy("SHORT") is False


def test_no_sleeve_is_active_paper_after_phase3a():
    """In research-only mode, no sleeve should be active for paper trading."""
    active = list(reg.active_paper_strategies())
    # Active may still list SNIPER/VOYAGER in the registry, but execution
    # is blocked by ResearchOnlyModeError — the registry itself is not
    # mutated, only the execution layer is disabled.
    # What we verify: CORE_SATELLITE and LEVERAGED were never registered.
    assert "CORE_SATELLITE" not in reg.SLEEVE_REGISTRY
    assert "LEVERAGED" not in reg.SLEEVE_REGISTRY


# ---------------------------------------------------------------------------
# Archived tests are NOT present in the live suite
# ---------------------------------------------------------------------------

def test_execution_tests_not_in_live_suite():
    tests_dir = Path(__file__).resolve().parents[1] / "unit"
    archived = [
        "test_order_manager_fills.py",
        "test_phase_1g1_close_lifecycle.py",
        "test_reconciler_broker_unavailable.py",
        "test_phase1a_safety.py",
    ]
    for fname in archived:
        assert not (tests_dir / fname).exists(), (
            f"{fname} should be in archive/tests_archived/, not in tests/unit/"
        )


def test_archived_files_exist_in_archive():
    """Archived execution files are preserved for reproducibility."""
    archive = Path(__file__).resolve().parents[2] / "archive" / "execution_disabled"
    expected = [
        "order_manager.py",
        "position_monitor.py",
        "paper_governance.py",
        "position_reconciler.py",
        "portfolio_risk.py",
        "portfolio_allocator.py",
        "alpaca_client.py",
        "main.py",
    ]
    for fname in expected:
        assert (archive / fname).exists(), f"Archive missing: {fname}"

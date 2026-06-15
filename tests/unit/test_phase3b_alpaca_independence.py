"""
tests/unit/test_phase3b_alpaca_independence.py

Phase 3B acceptance tests — Alpaca independence, paper/holdout archival,
FMP fundamentals mapping, Tradier token fix, and no-execution proof.

All tests are offline/unit-level. No provider calls, no DB writes.
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

# ── Safety: execution paths disabled ─────────────────────────────────────────

def test_research_mode_flags():
    """All execution mode flags must be False in RESEARCH_ONLY_MODE."""
    from core.research_mode import (
        LIVE_TRADING_ENABLED,
        PAPER_TRADING_ENABLED,
        BROKER_EXECUTION_ENABLED,
        STRATEGY_PROMOTION_ENABLED,
        AUTO_ORDER_ROUTING_ENABLED,
        ALPACA_REQUIRED,
        ALPACA_ACTIVE,
        TRADIER_EXECUTION_ENABLED,
    )
    assert LIVE_TRADING_ENABLED is False
    assert PAPER_TRADING_ENABLED is False
    assert BROKER_EXECUTION_ENABLED is False
    assert STRATEGY_PROMOTION_ENABLED is False
    assert AUTO_ORDER_ROUTING_ENABLED is False
    assert ALPACA_REQUIRED is False
    assert ALPACA_ACTIVE is False
    assert TRADIER_EXECUTION_ENABLED is False


def test_alpaca_execution_raises():
    """AlpacaClient execution methods must raise ResearchOnlyModeError."""
    from core.alpaca_client import get_alpaca
    from core.research_mode import ResearchOnlyModeError
    alp = get_alpaca()
    with pytest.raises(ResearchOnlyModeError):
        alp.submit_market_order("SPY", 1, "buy")
    with pytest.raises(ResearchOnlyModeError):
        alp.submit_limit_order("SPY", 1, "buy", 700.0)
    with pytest.raises(ResearchOnlyModeError):
        alp.close_position("SPY")
    with pytest.raises(ResearchOnlyModeError):
        alp.cancel_all_orders()


def test_alpaca_read_methods_return_empty_gracefully():
    """AlpacaClient data stubs return safe empty values without network calls."""
    from core.alpaca_client import get_alpaca
    alp = get_alpaca()
    # Positions: [] when no cache
    assert isinstance(alp.get_positions(), list)
    # Account: dict with zeros
    acct = alp.get_account()
    assert isinstance(acct, dict)
    assert acct.get("equity") == 0
    # Open orders: []
    assert alp.get_open_orders() == []


def test_alpaca_stub_reads_parquet_not_network(tmp_path):
    """AlpacaClient.get_daily_bars reads from parquet cache, not the network."""
    import pandas as pd
    from core import alpaca_client as ac_mod

    # Patch the cache directory to our tmp path
    parquet_dir = tmp_path / "prices"
    parquet_dir.mkdir()
    df = pd.DataFrame([
        {"date": "2026-06-10", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1000},
        {"date": "2026-06-11", "open": 100.5, "high": 102.0, "low": 100.0, "close": 101.0, "volume": 1100},
    ])
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")
    df.to_parquet(parquet_dir / "TESTX.parquet")

    original = ac_mod._PRICE_CACHE
    ac_mod._PRICE_CACHE = parquet_dir
    try:
        alp = ac_mod.AlpacaClient()
        bars = alp.get_daily_bars("TESTX", days=10)
        assert len(bars) == 2
        assert bars[-1]["close"] == pytest.approx(101.0)
    finally:
        ac_mod._PRICE_CACHE = original


# ── Paper / holdout archival ──────────────────────────────────────────────────

def test_sleeves_are_decommissioned():
    """VOYAGER and SNIPER must be DECOMMISSIONED (not ACTIVE_PAPER)."""
    from core.strategy_registry import SLEEVE_REGISTRY, DECOMMISSIONED, ACTIVE_PAPER
    voyager = SLEEVE_REGISTRY["VOYAGER"]
    sniper = SLEEVE_REGISTRY["SNIPER"]
    assert voyager.status == DECOMMISSIONED, f"VOYAGER status={voyager.status}"
    assert sniper.status == DECOMMISSIONED, f"SNIPER status={sniper.status}"
    # Must not be ACTIVE_PAPER
    assert voyager.status != ACTIVE_PAPER
    assert sniper.status != ACTIVE_PAPER


def test_no_active_paper_sleeves():
    """No sleeve should have ACTIVE_PAPER status in RESEARCH_ONLY_MODE."""
    from core.strategy_registry import SLEEVE_REGISTRY, ACTIVE_PAPER
    active = [k for k, v in SLEEVE_REGISTRY.items() if v.status == ACTIVE_PAPER]
    assert active == [], f"Found ACTIVE_PAPER sleeves: {active}"


def test_short_a_remains_frozen():
    """SHORT_A must stay FROZEN (not ACTIVE_PAPER, not DECOMMISSIONED)."""
    from core.strategy_registry import SLEEVE_REGISTRY, FROZEN
    short = SLEEVE_REGISTRY["SHORT"]
    assert short.status == FROZEN, f"SHORT status={short.status}"


def test_paper_evidence_status_has_archived_marker():
    """run_paper_evidence status dict must include phase=ARCHIVED_FOR_HISTORY_ONLY."""
    # Verify the source code sets the marker without actually running the job
    import importlib.util
    from pathlib import Path
    src = Path(__file__).resolve().parents[2] / "scripts" / "run_paper_evidence.py"
    text = src.read_text()
    assert "ARCHIVED_FOR_HISTORY_ONLY" in text, "run_paper_evidence.py must mark phase as ARCHIVED_FOR_HISTORY_ONLY"
    assert "new_signals_possible" in text
    assert "alpaca_required" in text


# ── Tradier token key fix ─────────────────────────────────────────────────────

def test_tradier_health_uses_api_token_key():
    """tradier_research_health.py must read TRADIER_API_TOKEN, not TRADIER_ACCESS_TOKEN."""
    from pathlib import Path
    src = Path(__file__).resolve().parents[2] / "research" / "tradier_research_health.py"
    text = src.read_text()
    assert "TRADIER_API_TOKEN" in text, "tradier_research_health.py must use TRADIER_API_TOKEN"
    assert "TRADIER_ACCESS_TOKEN" not in text, "tradier_research_health.py must not use TRADIER_ACCESS_TOKEN"


def test_stock_research_card_uses_api_token_key():
    """stock_research_card.py must read TRADIER_API_TOKEN, not TRADIER_ACCESS_TOKEN."""
    from pathlib import Path
    src = Path(__file__).resolve().parents[2] / "research" / "stock_research_card.py"
    text = src.read_text()
    assert "TRADIER_API_TOKEN" in text, "stock_research_card.py must use TRADIER_API_TOKEN"
    assert "TRADIER_ACCESS_TOKEN" not in text, "stock_research_card.py must not use TRADIER_ACCESS_TOKEN"


def test_tradier_health_offline_when_no_token():
    """tradier_research_health._is_offline() must return True when TRADIER_API_TOKEN is absent."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("TRADIER_API_TOKEN", None)
        # Import fresh to avoid cached module state
        import importlib
        import research.tradier_research_health as th
        importlib.reload(th)
        assert th._is_offline() is True


def test_tradier_health_online_when_token_set():
    """tradier_research_health._is_offline() must return False when TRADIER_API_TOKEN is a real value."""
    with patch.dict(os.environ, {"TRADIER_API_TOKEN": "test_token_abc"}):
        import importlib
        import research.tradier_research_health as th
        importlib.reload(th)
        assert th._is_offline() is False


def test_tradier_execution_disabled_in_health_report():
    """tradier_research_health build_report must always report execution_permanently_disabled=True."""
    import importlib
    import research.tradier_research_health as th
    importlib.reload(th)
    with patch.object(th, "_probe_tradier", return_value={"status": "OFFLINE", "reason": "no token"}):
        report = th.build_report()
    assert report["execution_permanently_disabled"] is True
    assert report["tradier_execution_enabled"] is False


# ── FMP fundamentals mapping ──────────────────────────────────────────────────

def test_flatten_fmp_fundamentals_none_input():
    """_flatten_fmp_fundamentals(None) returns None."""
    from research.stock_research_card import _flatten_fmp_fundamentals
    assert _flatten_fmp_fundamentals(None) is None


def test_flatten_fmp_fundamentals_empty_lists():
    """_flatten_fmp_fundamentals with all empty lists returns None."""
    from research.stock_research_card import _flatten_fmp_fundamentals
    assert _flatten_fmp_fundamentals({"income": [], "balance": [], "cashflow": []}) is None


def test_flatten_fmp_fundamentals_revenue_ttm():
    """Revenue TTM is the sum of 4 quarterly revenue values."""
    from research.stock_research_card import _flatten_fmp_fundamentals
    income = [
        {"revenue": 100_000, "netIncome": 10_000, "grossProfit": 50_000, "grossProfitRatio": 0.50,
         "operatingIncome": 20_000, "operatingIncomeRatio": 0.20, "netIncomeRatio": 0.10,
         "eps": 1.0, "epsdiluted": 0.95},
        {"revenue": 90_000, "netIncome": 9_000, "grossProfit": 45_000, "grossProfitRatio": 0.50,
         "operatingIncome": 18_000, "operatingIncomeRatio": 0.20, "netIncomeRatio": 0.10,
         "eps": 0.9, "epsdiluted": 0.85},
        {"revenue": 80_000, "netIncome": 8_000, "grossProfit": 40_000, "grossProfitRatio": 0.50,
         "operatingIncome": 16_000, "operatingIncomeRatio": 0.20, "netIncomeRatio": 0.10,
         "eps": 0.8},
        {"revenue": 70_000, "netIncome": 7_000, "grossProfit": 35_000, "grossProfitRatio": 0.50,
         "operatingIncome": 14_000, "operatingIncomeRatio": 0.20, "netIncomeRatio": 0.10,
         "eps": 0.7},
    ]
    raw = {"income": income, "balance": [], "cashflow": []}
    flat = _flatten_fmp_fundamentals(raw)
    assert flat is not None
    assert flat["revenue"] == pytest.approx(340_000)
    assert flat["netIncome"] == pytest.approx(34_000)
    # Ratios from most recent quarter (income[0])
    assert flat["grossProfitRatio"] == pytest.approx(0.50)
    assert flat["netIncomeRatio"] == pytest.approx(0.10)
    assert flat["eps"] == pytest.approx(1.0)


def test_flatten_fmp_fundamentals_balance_sheet():
    """Balance sheet values come from the most recent quarter."""
    from research.stock_research_card import _flatten_fmp_fundamentals
    balance = [
        {"totalDebt": 500_000, "cashAndCashEquivalents": 200_000, "totalStockholdersEquity": 300_000},
        {"totalDebt": 480_000, "cashAndCashEquivalents": 180_000, "totalStockholdersEquity": 290_000},
    ]
    raw = {"income": [], "balance": balance, "cashflow": []}
    flat = _flatten_fmp_fundamentals(raw)
    assert flat is not None
    assert flat["totalDebt"] == 500_000
    assert flat["cashAndCashEquivalents"] == 200_000


def test_flatten_fmp_fundamentals_cashflow_ttm():
    """freeCashFlow is the TTM sum of cashflow rows."""
    from research.stock_research_card import _flatten_fmp_fundamentals
    cashflow = [
        {"operatingCashFlow": 25_000, "freeCashFlow": 20_000},
        {"operatingCashFlow": 22_000, "freeCashFlow": 18_000},
        {"operatingCashFlow": 20_000, "freeCashFlow": 15_000},
        {"operatingCashFlow": 18_000, "freeCashFlow": 13_000},
    ]
    raw = {"income": [], "balance": [], "cashflow": cashflow}
    flat = _flatten_fmp_fundamentals(raw)
    assert flat is not None
    assert flat["freeCashFlow"] == pytest.approx(66_000)
    assert flat["operatingCashFlow"] == pytest.approx(85_000)


def test_research_card_fundamentals_populated(monkeypatch):
    """build_card fundamentals block should have non-null revenue_ttm when FMP data available."""
    from research import stock_research_card as src

    # Provide a fake FMP response matching get_fundamentals() structure
    fake_raw = {
        "ticker": "FAKE",
        "income": [{"revenue": 200_000, "netIncome": 20_000, "grossProfit": 100_000,
                     "grossProfitRatio": 0.50, "operatingIncome": 40_000,
                     "operatingIncomeRatio": 0.20, "netIncomeRatio": 0.10,
                     "eps": 2.0, "epsdiluted": 1.95}],
        "balance": [{"totalDebt": 50_000, "cashAndCashEquivalents": 30_000,
                      "totalStockholdersEquity": 100_000}],
        "cashflow": [{"operatingCashFlow": 25_000, "freeCashFlow": 18_000}],
    }
    fake_profile = {
        "companyName": "Fake Corp",
        "sector": "Technology",
        "industry": "Software",
        "marketCap": 2_000_000,
    }

    monkeypatch.setattr(src, "_fmp_fundamentals", lambda ticker: fake_raw)
    monkeypatch.setattr(src, "_fmp_profile", lambda ticker: fake_profile)
    monkeypatch.setattr(src, "_fmp_next_earnings", lambda ticker: None)
    monkeypatch.setattr(src, "_fmp_news_summary", lambda ticker: [])
    monkeypatch.setattr(src, "_fmp_analyst_grades", lambda ticker: [])
    monkeypatch.setattr(src, "_fmp_insider", lambda ticker: [])

    card = src.build_card("FAKE")
    fund = card["fundamentals"]

    assert fund["revenue_ttm"] == pytest.approx(200_000), f"revenue_ttm={fund['revenue_ttm']}"
    assert fund["net_income"] == pytest.approx(20_000)
    assert fund["gross_margin_pct"] == pytest.approx(50.0, abs=0.1)
    assert fund["operating_margin_pct"] == pytest.approx(20.0, abs=0.1)
    assert fund["net_margin_pct"] == pytest.approx(10.0, abs=0.1)
    assert fund["free_cash_flow"] == pytest.approx(18_000)
    assert fund["data_available"] is True
    # Derived PE: market_cap / net_income = 2_000_000 / 20_000 = 100
    assert fund["pe_ratio"] == pytest.approx(100.0, abs=0.5)


def test_research_card_fundamentals_null_when_fmp_offline(monkeypatch):
    """build_card fundamentals should all be None when FMP is offline."""
    from research import stock_research_card as src
    monkeypatch.setattr(src, "_fmp_fundamentals", lambda ticker: None)
    monkeypatch.setattr(src, "_fmp_profile", lambda ticker: None)
    monkeypatch.setattr(src, "_fmp_next_earnings", lambda ticker: None)
    monkeypatch.setattr(src, "_fmp_news_summary", lambda ticker: [])
    monkeypatch.setattr(src, "_fmp_analyst_grades", lambda ticker: [])
    monkeypatch.setattr(src, "_fmp_insider", lambda ticker: [])
    card = src.build_card("FAKE")
    fund = card["fundamentals"]
    assert fund["data_available"] is False
    assert fund["revenue_ttm"] is None


# ── Research card: no trade recommendations ───────────────────────────────────

def test_research_card_guardrails(monkeypatch):
    """Research card must carry all no-trade guardrail flags."""
    from research import stock_research_card as src
    monkeypatch.setattr(src, "_fmp_fundamentals", lambda t: None)
    monkeypatch.setattr(src, "_fmp_profile", lambda t: None)
    monkeypatch.setattr(src, "_fmp_next_earnings", lambda t: None)
    monkeypatch.setattr(src, "_fmp_news_summary", lambda t: [])
    monkeypatch.setattr(src, "_fmp_analyst_grades", lambda t: [])
    monkeypatch.setattr(src, "_fmp_insider", lambda t: [])
    card = src.build_card("FAKE")
    gr = card["guardrails"]
    assert gr["no_trade_recommendation"] is True
    assert gr["no_buy_sell"] is True
    assert gr["no_paper_signal"] is True
    assert gr["alpaca_required"] is False
    assert gr["tradier_execution_disabled"] is True
    assert card["research_only"] is True


# ── Dashboard no-Alpaca proof ─────────────────────────────────────────────────

def test_dashboard_offline_script_exists_and_imports():
    """verify_dashboard_modes_offline.py must exist and be importable in skeleton."""
    from pathlib import Path
    p = Path(__file__).resolve().parents[2] / "scripts" / "verify_dashboard_modes_offline.py"
    assert p.exists()
    text = p.read_text()
    assert "RENDER_OK" in text
    assert 'os.environ.setdefault("ALPACA_API_KEY", "offline")' in text


def test_research_mode_alpaca_not_required():
    """ALPACA_REQUIRED must be False — Alpaca is safe to cancel."""
    from core.research_mode import ALPACA_REQUIRED, ALPACA_ACTIVE
    assert ALPACA_REQUIRED is False
    assert ALPACA_ACTIVE is False


# ── Price bar migration: FMP is the refresh provider ─────────────────────────

def test_nightly_refresh_uses_fmp_not_alpaca():
    """nightly_refresh.py must use FMP (get_ticker_bars) and not import AlpacaClient directly."""
    from pathlib import Path
    src = Path(__file__).resolve().parents[2] / "scripts" / "nightly_refresh.py"
    text = src.read_text()
    # Must use FMP
    assert "get_ticker_bars" in text or "fmp" in text.lower()
    # Must not import AlpacaClient directly
    assert "from core.alpaca_client import AlpacaClient" not in text
    assert "from core.alpaca_client import get_alpaca" not in text


def test_deepen_cache_uses_fmp_not_alpaca():
    """deepen_price_cache.py must use FMP and not import live AlpacaClient."""
    from pathlib import Path
    src = Path(__file__).resolve().parents[2] / "scripts" / "deepen_price_cache.py"
    text = src.read_text()
    assert "fmp" in text.lower() or "get_ticker_bars" in text
    assert "from core.alpaca_client import AlpacaClient" not in text
    assert "from core.alpaca_client import get_alpaca" not in text


# ── Options: Tradier-only, no Alpaca ─────────────────────────────────────────

def test_options_feed_factory_no_alpaca():
    """options_feed_factory.py must not reference AlpacaClient."""
    from pathlib import Path
    src = Path(__file__).resolve().parents[2] / "core" / "options_feed_factory.py"
    text = src.read_text()
    assert "AlpacaClient" not in text
    assert "alpaca_options" not in text.lower()


def test_options_feed_factory_tradier_only():
    """options_feed_factory.py must reference Tradier as the options provider."""
    from pathlib import Path
    src = Path(__file__).resolve().parents[2] / "core" / "options_feed_factory.py"
    text = src.read_text()
    assert "tradier" in text.lower()


# ── Scanner and heartbeat: no Alpaca imports ─────────────────────────────────

def test_research_scanner_no_alpaca_import():
    """research_scanner.py must not import AlpacaClient."""
    from pathlib import Path
    src = Path(__file__).resolve().parents[2] / "research" / "research_scanner.py"
    text = src.read_text()
    assert "from core.alpaca_client import" not in text


def test_market_heartbeat_no_alpaca_import():
    """market_heartbeat.py must not import AlpacaClient."""
    from pathlib import Path
    src = Path(__file__).resolve().parents[2] / "research" / "market_heartbeat.py"
    text = src.read_text()
    assert "from core.alpaca_client import" not in text

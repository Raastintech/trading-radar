"""Phase 1H.2 Strategy Lab portfolio-construction guardrail tests."""
from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core import strategy_registry as reg  # noqa: E402
from research import strategy_lab_drawdown_decomp as decomp_cli  # noqa: E402
from research import strategy_lab_method_audit as method_cli  # noqa: E402
from research import strategy_lab_portfolio as portfolio  # noqa: E402
from research import strategy_lab_portfolio_sim as sim_cli  # noqa: E402


def _patch_calendar(monkeypatch):
    monkeypatch.setattr(
        portfolio.lab.d,
        "trading_dates_between",
        lambda start, end: list(pd.bdate_range(start, end)),
    )
    monkeypatch.setattr(portfolio, "_close_asof", lambda ticker, date: 100.0)


def _trade(
    ticker: str,
    *,
    signal_date: str = "2026-01-02",
    entry_date: str = "2026-01-05",
    exit_date: str = "2026-01-09",
    net_return: float = 0.10,
    sector: str = "Technology",
    side: str = "long",
) -> dict:
    entry = 100.0
    exit_price = entry * (1.0 + net_return if side == "long" else 1.0 - net_return)
    return {
        "variant": "TEST",
        "ticker": ticker,
        "side": side,
        "signal_date": signal_date,
        "entry_date": entry_date,
        "exit_date": exit_date,
        "entry_price": entry,
        "exit_price": exit_price,
        "hold_days": 5,
        "raw_return": net_return,
        "net_return": net_return,
        "cost_return": 0.0,
        "exit_reason": "max_hold",
        "stop_hit": False,
        "target_hit": False,
        "mfe": net_return,
        "mae": 0.0,
        "score": 90.0,
        "sector": sector,
        "theme": "test",
        "data_reliability": {"metadata": "CURRENT_METADATA_APPROXIMATION"},
    }


def test_portfolio_simulator_respects_max_positions(monkeypatch):
    _patch_calendar(monkeypatch)
    trades = [_trade(f"T{i}", sector=f"S{i}") for i in range(6)]

    res = portfolio.realistic_portfolio_metrics(
        trades,
        start="2026-01-02",
        end="2026-01-12",
        config=portfolio.PortfolioConfig(max_open_positions=5, max_sector_pct=1.0),
    )

    assert res["accepted_trade_count"] == 5
    assert res["max_concurrent_positions"] <= 5
    assert "max_positions" in res["reject_reasons"] or "gross_exposure_cap" in res["reject_reasons"]


def test_duplicate_ticker_is_skipped_while_open(monkeypatch):
    _patch_calendar(monkeypatch)
    trades = [
        _trade("AAA", signal_date="2026-01-02", entry_date="2026-01-05", exit_date="2026-01-09"),
        _trade("AAA", signal_date="2026-01-05", entry_date="2026-01-06", exit_date="2026-01-12"),
    ]

    res = portfolio.realistic_portfolio_metrics(
        trades,
        start="2026-01-02",
        end="2026-01-12",
        config=portfolio.PortfolioConfig(max_sector_pct=1.0),
    )

    assert res["accepted_trade_count"] == 1
    assert res["reject_reasons"]["duplicate_ticker_open"] == 1


def test_exposure_cap_works(monkeypatch):
    _patch_calendar(monkeypatch)
    trades = [_trade(f"T{i}", sector=f"S{i}") for i in range(4)]

    res = portfolio.realistic_portfolio_metrics(
        trades,
        start="2026-01-02",
        end="2026-01-12",
        config=portfolio.PortfolioConfig(
            max_open_positions=10,
            max_position_pct=0.20,
            max_gross_exposure_pct=0.30,
            max_sector_pct=1.0,
        ),
    )

    assert res["accepted_trade_count"] == 2
    assert res["max_concurrent_positions"] <= 2
    assert res["reject_reasons"]["gross_exposure_cap"] >= 1


def test_sector_cap_works(monkeypatch):
    _patch_calendar(monkeypatch)
    trades = [_trade(f"T{i}", sector="Technology") for i in range(3)]

    res = portfolio.realistic_portfolio_metrics(
        trades,
        start="2026-01-02",
        end="2026-01-12",
        config=portfolio.PortfolioConfig(
            max_open_positions=10,
            max_position_pct=0.10,
            max_sector_pct=0.20,
            max_gross_exposure_pct=1.0,
        ),
    )

    assert res["accepted_trade_count"] == 2
    assert res["reject_reasons"]["sector_cap"] == 1


def test_cash_drag_included(monkeypatch):
    _patch_calendar(monkeypatch)
    trade = _trade("AAA", net_return=0.10)

    res = portfolio.realistic_portfolio_metrics(
        [trade],
        start="2026-01-02",
        end="2026-01-12",
        config=portfolio.PortfolioConfig(max_position_pct=0.10, max_sector_pct=1.0),
    )

    assert 0.009 <= res["total_return"] <= 0.011
    assert res["total_return"] < trade["net_return"]


def test_benchmark_window_alignment(monkeypatch):
    frame = pd.DataFrame(
        {
            "open": [100.0, 101.0, 102.0],
            "high": [101.0, 102.0, 103.0],
            "low": [99.0, 100.0, 101.0],
            "close": [100.0, 102.0, 104.0],
            "volume": [1_000_000] * 3,
        },
        index=pd.bdate_range("2026-01-02", periods=3),
    )
    monkeypatch.setattr(portfolio.lab.d, "get_forward_window", lambda symbol, start, horizon: frame.copy())

    res = portfolio.benchmark_metrics("SPY", start="2026-01-01", end="2026-01-07")

    assert res["start"] == "2026-01-02"
    assert res["end"] == "2026-01-06"
    assert res["daily_rows"][0]["date"] == "2026-01-02"


def test_drawdown_computed_correctly():
    dd = portfolio._max_drawdown_from_equity([1.0, 1.2, 0.9, 1.1])

    assert round(dd, 6) == -0.25


def test_independent_trade_vs_portfolio_mode_differ(monkeypatch):
    _patch_calendar(monkeypatch)
    trades = [_trade(f"T{i}", sector=f"S{i}") for i in range(10)]

    independent = portfolio.independent_trade_metrics(trades, start="2026-01-02", end="2026-01-12")
    realistic = portfolio.realistic_portfolio_metrics(
        trades,
        start="2026-01-02",
        end="2026-01-12",
        config=portfolio.PortfolioConfig(max_sector_pct=1.0),
    )

    assert independent["total_return"] > realistic["total_return"]
    assert realistic["accepted_trade_count"] <= 5


def test_no_paper_signals_trade_proposals_or_live_imports():
    forbidden_import_roots = {"execution", "governance", "broker", "live_capital"}
    forbidden_calls = (
        "create_paper_signal(",
        "emit_paper_signal(",
        "insert_paper_signal(",
        "create_trade_proposal(",
        "emit_trade_proposal(",
        "submit_buy_order",
        "submit_sell_order",
        "strategy_registry.register",
    )
    for module in (portfolio, method_cli, sim_cli, decomp_cli):
        text = Path(module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".")[0] for alias in node.names}
                assert roots.isdisjoint(forbidden_import_roots), module.__name__
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                assert root not in forbidden_import_roots, module.__name__
        for needle in forbidden_calls:
            assert needle not in text, f"{module.__name__}: {needle}"


def test_production_thresholds_unchanged_and_short_a_frozen():
    filters = importlib.import_module("research.scanner_truth.filters")

    assert filters.SNI_ATR_CONTRACTION_THRESH == 0.85
    assert filters.SNI_VOL_SPIKE_THRESH == 1.4
    assert filters.VOY_MAX_EXTENSION_MA50 == 0.12
    assert filters.VOY_MA200_FLOOR == 0.92
    assert reg.is_frozen_strategy("SHORT") is True
    assert reg.is_active_paper_strategy("SHORT") is False

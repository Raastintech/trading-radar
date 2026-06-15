"""Phase 1H.3 correction-regime and correction-strategy guardrail tests."""
from __future__ import annotations

import ast
import importlib
import math
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core import strategy_registry as reg  # noqa: E402
from research import strategy_lab_correction_strategy as correction_report  # noqa: E402
from research import strategy_lab_regime as regime  # noqa: E402
from research import strategy_research_lab as lab  # noqa: E402


def test_march_2026_classified_as_correction_without_manual_override():
    res = regime.build_regime_labels(start="2026-03-01", end="2026-03-31")
    counts = res["march_2026"]["label_counts"]

    assert res["march_2026"]["manual_override_used"] is False
    assert counts.get(regime.MARKET_CORRECTION, 0) + counts.get(regime.TECH_LED_CORRECTION, 0) > 0
    assert counts.get(regime.RISK_OFF, 0) + counts.get(regime.MARKET_CORRECTION, 0) + counts.get(regime.TECH_LED_CORRECTION, 0) > 0


def test_ema_reclaim_logic_uses_only_asof_bars(monkeypatch):
    dates = pd.bdate_range("2026-01-01", periods=30)
    close = [100.0] * 28 + [95.0, 101.0]
    frame = pd.DataFrame(
        {
            "open": close,
            "high": [x + 1 for x in close],
            "low": [x - 1 for x in close],
            "close": close,
            "volume": [1_000_000] * len(close),
        },
        index=dates,
    )
    asof = str(dates[-1].date())
    seen = {}

    def fake_load_price_frame_asof(ticker, requested_asof):
        out = frame[frame.index <= pd.Timestamp(requested_asof)].copy()
        seen["max_date"] = out.index.max()
        return out

    monkeypatch.setattr(lab.d, "load_price_frame_asof", fake_load_price_frame_asof)

    state = lab._ema_reclaim_state("TEST", asof, 10)

    assert seen["max_date"] <= pd.Timestamp(asof)
    assert state is not None
    assert state["reclaim"] is True
    assert state["ema_span"] == 10


def test_relative_strength_lookbacks_work():
    f = {
        "rs20_spy": 0.03,
        "rs20_qqq": 0.02,
        "sector_rs20": 0.01,
        "rs60_spy": 0.04,
        "rs60_qqq": 0.05,
        "r40": 0.07,
    }
    context = {"SPY": {"r40": 0.02}}

    assert lab._correction_rs(f, context, 20) == 0.03
    assert lab._correction_rs(f, context, 60) == 0.05
    assert round(lab._correction_rs(f, context, 40), 6) == 0.05


def test_correction_leader_reclaim_signal_contract(monkeypatch):
    monkeypatch.setattr(
        lab,
        "_ema_reclaim_state",
        lambda ticker, asof, span: {
            "ema_span": span,
            "ema": 99.0,
            "prior_ema": 100.0,
            "reclaim": True,
            "reclaim_low": 96.0,
            "last_close": 101.0,
            "prior_close": 98.0,
        },
    )
    f = {
        "ticker": "TEST",
        "asof": "2026-03-10",
        "bars": 120,
        "price": 101.0,
        "avg_dvol20": 20_000_000.0,
        "drawdown_from_high20": -0.04,
        "drawdown_from_high60": -0.08,
        "low20": 92.0,
        "ma200": 96.0,
        "ma50": 98.0,
        "avg_vol20": 1_000_000.0,
        "volume": 1_250_000.0,
        "vol_expansion": 1.20,
        "ext_ema20": 0.02,
        "r10": 0.03,
        "rs20_spy": 0.05,
        "rs20_qqq": 0.04,
        "sector_rs20": 0.03,
        "atr_pct": 0.02,
        "sector": "Technology",
        "theme": "semiconductors",
    }
    context = {
        "REGIME": {"label": regime.TECH_LED_CORRECTION, "flags": {"correction": True}},
        "SPY": {"drawdown_from_high60": -0.09, "drawdown_from_high20": -0.07, "r40": -0.03},
        "QQQ": {"drawdown_from_high60": -0.10, "drawdown_from_high20": -0.08},
    }

    sig = lab.signal_correction_leader_reclaim(f, lab.StrategyParams(), context)

    assert sig is not None
    assert sig.variant == "CORRECTION_LEADER_RECLAIM"
    assert sig.side == "long"
    assert sig.features["market_regime"] == regime.TECH_LED_CORRECTION
    assert sig.features["clr_stop_loss_pct"] >= 0.04
    assert "earnings_calendar_not_retained_no_future_filter" in sig.reasons


def test_same_exposure_benchmark_alignment(monkeypatch):
    def fake_benchmark_metrics(symbol, *, start, end, cost_model):
        return {
            "daily_rows": [
                {"date": "2026-01-02", "daily_return": 0.02, "equity": 1.02},
                {"date": "2026-01-05", "daily_return": -0.01, "equity": 1.0098},
                {"date": "2026-01-06", "daily_return": 0.04, "equity": 1.050192},
            ]
        }

    monkeypatch.setattr(correction_report.portfolio, "benchmark_metrics", fake_benchmark_metrics)
    strategy_rows = [
        {"date": "2026-01-02", "gross_exposure": 0.5},
        {"date": "2026-01-05", "gross_exposure": 0.0},
        {"date": "2026-01-06", "gross_exposure": 1.0},
    ]

    res = correction_report._same_exposure_benchmark("SPY", strategy_rows, start="2026-01-02", end="2026-01-06")

    expected = (1.0 + 0.02 * 0.5) * (1.0 - 0.01 * 0.0) * (1.0 + 0.04 * 1.0) - 1.0
    assert math.isclose(res["total_return"], round(expected, 6), abs_tol=1e-6)
    assert res["exposure_pct"] == 0.5


def _eligibility_inputs(wf_verdict, overfit_risk=None):
    return dict(
        trades=[],
        independent={"trade_count": 50, "expectancy": 0.01, "max_drawdown": -0.05},
        realistic={
            "accepted_trade_count": 30,
            "total_return": 0.10,
            "cagr": 0.05,
            "max_drawdown": -0.02,
            "sharpe": 2.0,
        },
        same_spy={"total_return": 0.01, "sharpe": 0.5},
        same_qqq={"total_return": 0.01, "sharpe": 0.5},
        correction={"CORRECTION_FAMILY": {"trade_count": 5, "expectancy": 0.01}},
        concentration={"top_ticker_pct": 0.10, "top_sector_pct": 0.30},
        month_check={"passes": True},
        walk_forward={"rows": {"X": {"verdict": wf_verdict, "overfit_risk": overfit_risk}}},
        threshold={"rows": {}},
    )


def test_walk_forward_need_more_data_blocks_paper_shadow_candidate():
    out = correction_report._eligibility(
        "X", **_eligibility_inputs(lab.VERDICT_NEED_MORE, "HIGH_LOW_TEST_SAMPLE")
    )

    assert out["label"] == correction_report.NEED_MORE
    assert any("walk-forward" in r for r in out["reasons"])


def test_walk_forward_overfit_verdict_blocks_paper_shadow_candidate():
    out = correction_report._eligibility("X", **_eligibility_inputs(lab.VERDICT_OVERFIT_RISK))

    assert out["label"] == correction_report.OVERFIT


def test_walk_forward_edge_verdict_allows_paper_shadow_candidate():
    out = correction_report._eligibility("X", **_eligibility_inputs(lab.VERDICT_EDGE))

    assert out["label"] == correction_report.CANDIDATE_LABEL


def test_no_future_or_stock_lens_gatekeeper_sources_used():
    text = Path(lab.__file__).read_text(encoding="utf-8")
    clr_start = text.index("def signal_correction_leader_reclaim")
    clr_end = text.index("def signal_simple_sector_rs")
    clr_text = text[clr_start:clr_end]

    assert "Stock Lens" not in clr_text
    assert "stock_lens" not in clr_text
    assert "gatekeeper" not in clr_text.lower()
    assert "get_forward_window" not in clr_text
    assert "future_label" not in clr_text.lower()


def test_no_paper_trade_or_execution_governance_imports_for_correction_modules():
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
    for module in (lab, regime, correction_report):
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


def test_production_thresholds_unchanged_and_short_a_frozen_after_phase_1h3():
    filters = importlib.import_module("research.scanner_truth.filters")

    assert filters.SNI_ATR_CONTRACTION_THRESH == 0.85
    assert filters.SNI_VOL_SPIKE_THRESH == 1.4
    assert filters.VOY_MAX_EXTENSION_MA50 == 0.12
    assert filters.VOY_MA200_FLOOR == 0.92
    assert reg.is_frozen_strategy("SHORT") is True
    assert reg.is_active_paper_strategy("SHORT") is False

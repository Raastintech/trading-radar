"""Phase 2A core-satellite portfolio engine guardrail tests."""
from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core import strategy_registry as reg  # noqa: E402
from research import core_satellite_portfolio as cs  # noqa: E402
from research import strategy_lab_regime as regime  # noqa: E402

DATES = [f"2026-03-{d:02d}" for d in (2, 3, 4, 5, 6)]


def _returns(spy=0.01, qqq=0.02):
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in DATES])
    return {
        "SPY": pd.Series([spy] * len(DATES), index=idx),
        "QQQ": pd.Series([qqq] * len(DATES), index=idx),
    }


def _regimes(labels):
    return {d: {"label": lab, "qqq_vs_spy_20": 0.01} for d, lab in zip(DATES, labels)}


# ---------------------------------------------------------------------------
# As-of regime usage / no lookahead
# ---------------------------------------------------------------------------

def test_exposure_uses_prior_close_regime_only():
    """Day i's weight must come from day i-1's label; day 0 is cash."""
    spec = cs.VariantSpec("T", "throttled", asset="QQQ")
    labels = [regime.RISK_OFF, regime.BULL_TREND, regime.RISK_OFF, regime.BULL_TREND, regime.BULL_TREND]
    rows = cs.simulate_variant(spec, dates=DATES, returns=_returns(), regimes=_regimes(labels), satellite_returns={})

    weights = [r["core_weight"] for r in rows]
    # day0 cash; day1 <- RISK_OFF(0.10); day2 <- BULL(1.0); day3 <- RISK_OFF; day4 <- BULL
    assert weights == [0.0, 0.10, 1.0, 0.10, 1.0]


def test_no_future_bars_are_read():
    """The simulator consumes only the provided return series; a date missing
    from the series contributes zero — it never reaches for future frames."""
    spec = cs.VariantSpec("T", "throttled", asset="QQQ")
    returns = _returns()
    returns["QQQ"] = returns["QQQ"].iloc[:3]  # truncate future days
    rows = cs.simulate_variant(spec, dates=DATES, returns=returns,
                               regimes=_regimes([regime.BULL_TREND] * 5), satellite_returns={})
    assert rows[-1]["daily_return"] == 0.0


# ---------------------------------------------------------------------------
# Weights, cash handling, satellite caps
# ---------------------------------------------------------------------------

def test_allocation_weights_sum_to_one():
    spec = cs.VariantSpec("T", "throttled", asset="QQQ", satellites=((cs.SAT_SNIPER, 0.075), (cs.SAT_LRR, 0.075)))
    sat = {cs.SAT_SNIPER: {d: 0.001 for d in DATES}, cs.SAT_LRR: {d: 0.0 for d in DATES}}
    rows = cs.simulate_variant(spec, dates=DATES, returns=_returns(),
                               regimes=_regimes([regime.BULL_TREND] * 5), satellite_returns=sat)
    for r in rows:
        assert r["core_weight"] + r["satellite_weight"] + r["cash_weight"] == pytest.approx(1.0)
        assert r["satellite_weight"] == pytest.approx(0.15)
        # Core is scaled by (1 - satellite capital): full bull = 0.85.
        assert r["core_weight"] <= 0.85 + 1e-9


def test_satellite_caps_respected_and_stream_applied():
    spec = cs.VariantSpec("T", "throttled", asset="QQQ", satellites=((cs.SAT_SNIPER, 0.10),))
    sat = {cs.SAT_SNIPER: {DATES[1]: 0.05}}
    rows = cs.simulate_variant(spec, dates=DATES, returns=_returns(qqq=0.0),
                               regimes=_regimes([regime.RISK_OFF] * 5), satellite_returns=sat)
    # Day 1: core 0.10*0.9=0.09 weight x 0 return + 0.10 x 5% = +0.5%.
    assert rows[1]["daily_return"] == pytest.approx(0.005)
    assert all(r["satellite_weight"] == pytest.approx(0.10) for r in rows)


def test_static_cash_handling():
    spec = cs.VariantSpec("S", "static", asset="QQQ", static_weight=0.60)
    rows = cs.simulate_variant(spec, dates=DATES, returns=_returns(qqq=0.02),
                               regimes={}, satellite_returns={})
    assert rows[0]["daily_return"] == pytest.approx(0.012)  # 60% of 2%
    expected = (1 + 0.012) ** len(DATES) - 1
    assert rows[-1]["equity"] - 1.0 == pytest.approx(expected, rel=1e-6)


# ---------------------------------------------------------------------------
# Metrics: monthly returns, drawdown, benchmark alignment
# ---------------------------------------------------------------------------

def test_month_returns_and_drawdown_math():
    rows = [
        {"date": "2026-01-15", "daily_return": 0.10, "equity": 1.10, "gross_exposure": 1.0},
        {"date": "2026-01-16", "daily_return": -0.20, "equity": 0.88, "gross_exposure": 1.0},
        {"date": "2026-02-02", "daily_return": 0.05, "equity": 0.924, "gross_exposure": 1.0},
    ]
    months = cs.month_returns(rows)
    assert months["2026-01"] == pytest.approx(1.10 * 0.80 - 1.0)
    assert months["2026-02"] == pytest.approx(0.05)

    m = cs.metrics_from_rows(rows, start="2026-01-15", end="2026-02-02")
    assert m["max_drawdown"] == pytest.approx(0.88 / 1.10 - 1.0)
    assert m["positive_months"] == 1
    assert m["exposure_pct"] == pytest.approx(1.0)


def test_benchmark_alignment_uses_common_calendar(monkeypatch):
    """SPY/QQQ series with different calendars must intersect, not misalign."""
    spy_idx = pd.DatetimeIndex([pd.Timestamp(d) for d in DATES])
    qqq_idx = spy_idx[:-1]  # QQQ missing the last day

    def fake_load(symbol, start, end):
        if symbol == "SPY":
            return pd.Series([0.01] * len(spy_idx), index=spy_idx)
        return pd.Series([0.02] * len(qqq_idx), index=qqq_idx)

    monkeypatch.setattr(cs, "load_daily_returns", fake_load)
    monkeypatch.setattr(cs, "build_regime_series", lambda dates: _regimes([regime.BULL_TREND] * len(dates)))

    res = cs.build_report(start=DATES[0], end=DATES[-1], skip_satellites=True)

    assert res["signal_window"]["trading_days"] == len(qqq_idx)


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------

def _m(total=0.50, dd=-0.10, sharpe=1.5, calmar=2.0, pos_months=10, best_share=0.2, change=0.10, turn=10.0):
    return {
        "total_return": total, "cagr": total / 2, "max_drawdown": dd, "sharpe": sharpe, "calmar": calmar,
        "positive_months": pos_months, "month_count": 20, "best_month_share_of_total": best_share,
        "exposure_change_days_pct": change, "turnover_per_year": turn,
    }


QQQ_M = {"total_return": 0.80, "max_drawdown": -0.22, "sharpe": 1.29, "calmar": 1.2}
SPY_M = {"total_return": 0.60, "max_drawdown": -0.18, "sharpe": 1.31, "calmar": 1.1}


def test_core_gates_pass_on_strong_low_dd_variant():
    gates = cs.core_gates(_m(total=0.50, dd=-0.10), {"2026": {"total_return": 0.05}}, QQQ_M, SPY_M)
    assert gates["all_passed"] is True


def test_core_gates_fail_on_high_dd_and_low_return():
    gates = cs.core_gates(_m(total=0.50, dd=-0.20), {"2026": {"total_return": 0.05}}, QQQ_M, SPY_M)
    assert gates["risk_or_return_vs_qqq"]["passed"] is False
    assert gates["all_passed"] is False


def test_core_gates_fail_on_churn_and_ytd():
    gates = cs.core_gates(_m(change=0.50), {"2026": {"total_return": -0.02}}, QQQ_M, SPY_M)
    assert gates["churn_acceptable"]["passed"] is False
    assert gates["ytd_2026_positive"]["passed"] is False


def test_satellite_assessment_requires_risk_adjusted_improvement():
    core = _m(sharpe=1.5)
    better = dict(core, sharpe=1.6, cagr=core["cagr"] + 0.01)
    worse = dict(core, sharpe=1.4)
    assert cs.satellite_assessment(better, core)["verdict"] == cs.SATELLITE_VALUE_ADD
    assert cs.satellite_assessment(worse, core)["verdict"] == cs.OBSERVATION_ONLY


def test_proposal_doc_written_only_when_candidate(tmp_path, monkeypatch):
    for name in ("OUT_JSON", "OUT_TXT", "OUT_DOC", "SCENARIOS_DOC", "PROPOSAL_DOC"):
        monkeypatch.setattr(cs, name, tmp_path / f"{name}.out")
    monkeypatch.setattr(cs, "render_text", lambda res: ["x"])
    monkeypatch.setattr(cs, "render_doc", lambda res: "x")
    monkeypatch.setattr(cs, "render_scenarios_doc", lambda: "scenarios")
    monkeypatch.setattr(cs, "render_proposal_doc", lambda res: "proposal")

    cs.write_outputs({"paper_shadow": {"proposal_created": False}})
    assert not (tmp_path / "PROPOSAL_DOC.out").exists()
    assert (tmp_path / "SCENARIOS_DOC.out").read_text() == "scenarios\n"

    cs.write_outputs({"paper_shadow": {"proposal_created": True}})
    assert (tmp_path / "PROPOSAL_DOC.out").read_text() == "proposal\n"


# ---------------------------------------------------------------------------
# Safety guardrails
# ---------------------------------------------------------------------------

def test_exposure_ladder_matches_spec_doc():
    assert cs.REGIME_EXPOSURE[regime.BULL_TREND] == 1.0
    assert cs.REGIME_EXPOSURE[regime.RECOVERY_RECLAIM] == 1.0
    assert cs.REGIME_EXPOSURE[regime.CHOP] == 0.6
    assert cs.REGIME_EXPOSURE[regime.HIGH_VOLATILITY] == 0.6
    assert cs.REGIME_EXPOSURE[regime.MARKET_CORRECTION] == 0.4
    assert cs.REGIME_EXPOSURE[regime.TECH_LED_CORRECTION] == 0.4
    assert cs.REGIME_EXPOSURE[regime.RISK_OFF] == 0.1
    assert cs.target_exposure("SOMETHING_UNKNOWN") == 0.0
    assert cs.target_exposure(None) == 0.0


def test_no_paper_trade_or_execution_governance_imports_for_phase_2a():
    forbidden_import_roots = {"execution", "governance", "broker", "live_capital", "council"}
    forbidden_calls = (
        "create_paper_signal(",
        "emit_paper_signal(",
        "insert_paper_signal(",
        "create_trade_proposal(",
        "emit_trade_proposal(",
        "submit_buy_order",
        "submit_sell_order",
        "submit_order",
        "strategy_registry.register",
    )
    text = Path(cs.__file__).read_text(encoding="utf-8")
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots = {alias.name.split(".")[0] for alias in node.names}
            assert roots.isdisjoint(forbidden_import_roots)
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            assert root not in forbidden_import_roots
    for needle in forbidden_calls:
        assert needle not in text, needle


def test_production_thresholds_unchanged_and_short_a_frozen_after_phase_2a():
    filters = importlib.import_module("research.scanner_truth.filters")

    assert filters.SNI_ATR_CONTRACTION_THRESH == 0.85
    assert filters.SNI_VOL_SPIKE_THRESH == 1.4
    assert filters.VOY_MAX_EXTENSION_MA50 == 0.12
    assert filters.VOY_MA200_FLOOR == 0.92
    assert reg.is_frozen_strategy("SHORT") is True
    assert reg.is_active_paper_strategy("SHORT") is False
    assert "CORE_SATELLITE" not in reg.SLEEVE_REGISTRY

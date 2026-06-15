"""Phase 2A.1 leveraged variant guardrail tests."""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core import strategy_registry as reg  # noqa: E402
from research import core_satellite_leveraged as lev  # noqa: E402
from research import core_satellite_portfolio as cs  # noqa: E402
from research import strategy_lab_regime as regime  # noqa: E402

DATES = [f"2026-03-{d:02d}" for d in (2, 3, 4, 5, 6, 9, 10)]
QQQ_RETURN = 0.02   # synthetic 2% daily QQQ return
SPY_RETURN = 0.01


def _returns():
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in DATES])
    return {
        "SPY": pd.Series([SPY_RETURN] * len(DATES), index=idx),
        "QQQ": pd.Series([QQQ_RETURN] * len(DATES), index=idx),
    }


def _regimes(labels):
    return {d: {"label": lab, "qqq_vs_spy_20": 0.01} for d, lab in zip(DATES, labels)}


# ---------------------------------------------------------------------------
# Borrow cost mechanics
# ---------------------------------------------------------------------------

def test_borrow_cost_zero_when_below_1x_exposure():
    """In CHOP (60%) × 1.25x = 75% gross — no borrowing needed."""
    spec = lev.LeveredSpec("T", "throttled", "QQQ", leverage=1.25)
    labels = [regime.CHOP] * len(DATES)
    rows = lev.simulate_levered(
        spec, dates=DATES, returns=_returns(),
        regimes=_regimes(labels),
    )
    # Day 0 is cash; day 1 onward uses CHOP → 0.6 × 1.25 = 0.75 gross, no borrow.
    for r in rows[1:]:
        assert r["borrow_cost_daily"] == 0.0, f"Expected 0 borrow cost, got {r['borrow_cost_daily']}"


def test_borrow_cost_positive_in_bull_at_125x():
    """In BULL (100%) × 1.25x = 125% gross — borrow 25% at stated rate."""
    spec = lev.LeveredSpec("T", "throttled", "QQQ", leverage=1.25,
                           borrow_rate_annual=lev.BORROW_RATE_ANNUAL)
    labels = [regime.BULL_TREND] * len(DATES)
    rows = lev.simulate_levered(
        spec, dates=DATES, returns=_returns(),
        regimes=_regimes(labels),
    )
    expected_borrow = round(0.25 * lev.BORROW_RATE_ANNUAL / 252, 8)
    # Day 1 onward uses BULL from prior close: core_w = 1.25, borrowed = 0.25.
    for r in rows[1:]:
        assert r["borrow_cost_daily"] == pytest.approx(expected_borrow, abs=1e-9)


def test_borrow_cost_zero_at_1x_leverage():
    """1.0x leverage: no borrowing regardless of regime."""
    spec = lev.LeveredSpec("T", "throttled", "QQQ", leverage=1.0)
    labels = [regime.BULL_TREND] * len(DATES)
    rows = lev.simulate_levered(
        spec, dates=DATES, returns=_returns(),
        regimes=_regimes(labels),
    )
    for r in rows:
        assert r["borrow_cost_daily"] == 0.0


def test_15x_bull_borrow_correct():
    """BULL × 1.5x = 150% gross → borrow 50% × rate/252."""
    spec = lev.LeveredSpec("T", "throttled", "QQQ", leverage=1.5,
                           borrow_rate_annual=lev.BORROW_RATE_ANNUAL)
    labels = [regime.BULL_TREND] * len(DATES)
    rows = lev.simulate_levered(
        spec, dates=DATES, returns=_returns(),
        regimes=_regimes(labels),
    )
    expected_borrow = round(0.5 * lev.BORROW_RATE_ANNUAL / 252, 8)
    for r in rows[1:]:
        assert r["borrow_cost_daily"] == pytest.approx(expected_borrow, abs=1e-9)


# ---------------------------------------------------------------------------
# No-lookahead
# ---------------------------------------------------------------------------

def test_no_lookahead_day0_is_cash():
    """Day 0 uses no prior regime → exposure 0 (cash), no borrow."""
    spec = lev.LeveredSpec("T", "throttled", "QQQ", leverage=1.5)
    labels = [regime.BULL_TREND] * len(DATES)
    rows = lev.simulate_levered(
        spec, dates=DATES, returns=_returns(),
        regimes=_regimes(labels),
    )
    assert rows[0]["gross_exposure"] == 0.0
    assert rows[0]["borrow_cost_daily"] == 0.0


def test_no_lookahead_weight_from_prior_close():
    """Day i gross_exposure = target_exposure(day i-1 label) × leverage."""
    spec = lev.LeveredSpec("T", "throttled", "QQQ", leverage=1.25)
    labels = [regime.RISK_OFF, regime.BULL_TREND, regime.RISK_OFF,
              regime.BULL_TREND, regime.BULL_TREND, regime.CHOP, regime.CHOP]
    rows = lev.simulate_levered(
        spec, dates=DATES, returns=_returns(),
        regimes=_regimes(labels),
    )
    # day0 → cash; day1 → RISK_OFF(0.1)×1.25=0.125; day2 → BULL(1.0)×1.25=1.25
    assert rows[0]["gross_exposure"] == 0.0
    assert rows[1]["gross_exposure"] == pytest.approx(0.125)
    assert rows[2]["gross_exposure"] == pytest.approx(1.25)


# ---------------------------------------------------------------------------
# Buy-hold benchmark: no borrow at 1.0x
# ---------------------------------------------------------------------------

def test_buy_hold_10x_no_borrow_cost():
    spec = lev.LeveredSpec("BH", "buy_hold", "QQQ", leverage=1.0)
    rows = lev.simulate_levered(
        spec, dates=DATES, returns=_returns(), regimes={},
    )
    for r in rows:
        assert r["borrow_cost_daily"] == 0.0
        assert r["gross_exposure"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Gap risk analysis
# ---------------------------------------------------------------------------

def test_gap_risk_quantile_table_keys():
    """All pre-registered quantile levels appear in the table."""
    daily = [-0.03, -0.01, 0.01, 0.02, 0.03] * 40
    result = lev.gap_risk_analysis(daily, leverage_levels=(1.0, 1.25, 1.5))
    qt = result["quantile_table"]
    for q in lev.GAP_RISK_QUANTILES:
        key = f"p{q * 100:.1f}"
        assert key in qt, f"Missing quantile key: {key}"


def test_gap_risk_loss_scales_monotonically_with_leverage():
    """Higher leverage → worse (more negative) tail-risk outcome."""
    daily = list(range(-10, 10))  # synthetic symmetric
    # Construct a clearly negative tail
    daily = [-0.10, -0.08, -0.05, -0.03, -0.01] + [0.01, 0.02, 0.03, 0.04] * 5
    result = lev.gap_risk_analysis(daily, leverage_levels=(1.0, 1.25, 1.5))
    qt = result["quantile_table"]
    for qdata in qt.values():
        lo = qdata["leveraged_loss"]
        if lo["1.00x"] < 0:  # only care about negative tail
            assert lo["1.25x"] <= lo["1.00x"], "1.25x should lose more than 1.00x"
            assert lo["1.50x"] <= lo["1.25x"], "1.50x should lose more than 1.25x"


def test_gap_risk_empty_series():
    result = lev.gap_risk_analysis([], (1.0,))
    assert result.get("error") == "no_data"


def test_gap_risk_mc_produces_expected_keys():
    daily = [0.01, -0.01, 0.02, -0.02] * 100
    result = lev.gap_risk_analysis(daily, (1.0, 1.25), n_mc_sims=200)
    mc = result["monte_carlo_1week"]["results"]
    for lev_tag in ("1.00x", "1.25x"):
        assert lev_tag in mc
        assert "p5_1week" in mc[lev_tag]
        assert "p1_1week" in mc[lev_tag]


# ---------------------------------------------------------------------------
# Tax estimate
# ---------------------------------------------------------------------------

def test_tax_estimate_worst_case_formula():
    """Worst case = pre_tax × (1 - STCG_RATE)."""
    result = lev.tax_estimate(0.20, 19.5)
    assert result["worst_case_after_tax_cagr"] == pytest.approx(0.20 * (1 - lev.STCG_RATE))


def test_tax_estimate_mid_case_formula():
    """Mid case = pre_tax × (1 - 0.5 × STCG_RATE)."""
    result = lev.tax_estimate(0.20, 19.5)
    assert result["mid_case_after_tax_cagr"] == pytest.approx(0.20 * (1 - 0.5 * lev.STCG_RATE))


def test_tax_estimate_stcg_rate_matches_constant():
    result = lev.tax_estimate(0.10, 10.0)
    assert result["stcg_rate_assumed"] == lev.STCG_RATE
    assert result["ltcg_rate_assumed"] == lev.LTCG_RATE


# ---------------------------------------------------------------------------
# Pre-registered gates
# ---------------------------------------------------------------------------

def _make_metrics(
    cagr=0.30, dd=-0.15, sharpe=1.35, calmar=2.0,
    pos_months=12, best_share=0.20, month_count=29,
):
    return {
        "cagr": cagr, "max_drawdown": dd, "sharpe": sharpe, "calmar": calmar,
        "positive_months": pos_months, "best_month_share_of_total": best_share,
        "month_count": month_count,
    }


def _make_years(y2024=0.18, y2025=0.15, ytd=0.10):
    return {
        "2024": {"total_return": y2024},
        "2025": {"total_return": y2025},
        "2026": {"total_return": ytd},
    }


def test_levered_gates_all_pass_on_strong_variant():
    gates = lev.levered_gates(_make_metrics(), _make_years())
    assert gates["all_passed"] is True


def test_levered_gates_fail_cagr_at_or_below_qqq():
    """CAGR must strictly exceed QQQ benchmark."""
    gates = lev.levered_gates(_make_metrics(cagr=lev.QQQ_CAGR_BENCHMARK), _make_years())
    assert gates["cagr_beats_qqq_after_cost"]["passed"] is False
    assert gates["all_passed"] is False


def test_levered_gates_fail_dd_exceeds_qqq_absolute():
    """maxDD may not exceed QQQ absolute floor, even if better than unlevered base."""
    gates = lev.levered_gates(_make_metrics(dd=-0.25), _make_years())
    assert gates["maxdd_lte_qqq_absolute"]["passed"] is False
    assert gates["all_passed"] is False


def test_levered_gates_fail_sharpe_at_or_below_qqq():
    gates = lev.levered_gates(_make_metrics(sharpe=lev.QQQ_SHARPE_BENCHMARK), _make_years())
    assert gates["sharpe_beats_qqq"]["passed"] is False
    assert gates["all_passed"] is False


def test_levered_gates_fail_calmar_at_or_below_qqq():
    gates = lev.levered_gates(_make_metrics(calmar=lev.QQQ_CALMAR_BENCHMARK), _make_years())
    assert gates["calmar_beats_qqq"]["passed"] is False
    assert gates["all_passed"] is False


def test_levered_gates_fail_negative_year():
    years = _make_years(y2025=-0.01)
    gates = lev.levered_gates(_make_metrics(), years)
    assert gates["all_years_positive"]["passed"] is False
    assert gates["all_passed"] is False


# ---------------------------------------------------------------------------
# Variant list completeness
# ---------------------------------------------------------------------------

def test_variant_list_covers_all_leverage_levels():
    """1.0x, 1.25x, 1.5x × QQQ + blend = 6 throttled + 2 benchmarks = 8 total."""
    assert len(lev.VARIANTS) == 8
    throttled = [s for s in lev.VARIANTS if s.base_kind in ("throttled", "throttled_blend")]
    assert len(throttled) == 6
    levs_seen = sorted({s.leverage for s in throttled})
    assert levs_seen == sorted(lev.LEVERAGE_LEVELS)


def test_benchmark_constants_match_phase2a():
    """Gate constants must equal the Phase 2A measured values (no re-tuning)."""
    assert lev.QQQ_CAGR_BENCHMARK == pytest.approx(0.2734)
    assert lev.QQQ_MAXDD_BENCHMARK == pytest.approx(-0.2277)
    assert lev.QQQ_SHARPE_BENCHMARK == pytest.approx(1.2738)
    assert lev.QQQ_CALMAR_BENCHMARK == pytest.approx(1.2004)
    assert lev.BORROW_RATE_ANNUAL == pytest.approx(0.065)


# ---------------------------------------------------------------------------
# Safety guardrails
# ---------------------------------------------------------------------------

def test_no_forbidden_imports():
    forbidden_roots = {"execution", "governance", "broker", "live_capital", "council"}
    forbidden_calls = (
        "create_paper_signal(", "emit_paper_signal(", "insert_paper_signal(",
        "create_trade_proposal(", "submit_buy_order", "submit_sell_order", "submit_order",
        "strategy_registry.register",
    )
    text = Path(lev.__file__).read_text(encoding="utf-8")
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in forbidden_roots
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in forbidden_roots
    for needle in forbidden_calls:
        assert needle not in text, f"Forbidden call found: {needle}"


def test_short_a_frozen_after_phase_2a1():
    assert reg.is_frozen_strategy("SHORT") is True
    assert reg.is_active_paper_strategy("SHORT") is False
    assert "CORE_SATELLITE" not in reg.SLEEVE_REGISTRY
    assert "LEVERAGED" not in reg.SLEEVE_REGISTRY

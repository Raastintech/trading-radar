"""Phase 1I LEADER_RESET_RECLAIM guardrail tests."""
from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core import strategy_registry as reg  # noqa: E402
from research import leader_reset_reclaim_strategy as lrr  # noqa: E402
from research import strategy_failure_reason_miner as miner  # noqa: E402
from research import strategy_research_lab as lab  # noqa: E402
from tests.unit.test_strategy_failure_miner import BULL_CONTEXT, base_features  # noqa: E402

PARAMS = lab.StrategyParams()

GOOD_RECLAIM = {
    "ema_span": 10,
    "ema": 99.0,
    "prior_ema": 100.0,
    "reclaim": True,
    "reclaim_low": 96.0,
    "last_close": 101.0,
    "prior_close": 98.0,
}


def lrr_features(**overrides):
    f = base_features(
        rs60_spy=0.10,
        rs60_qqq=0.08,
        r60=0.25,
        drawdown_from_high20=-0.08,
        above_ma200=True,
        r10=0.02,
        r20=0.10,
        atr_pct=0.02,
    )
    f.update(overrides)
    return f


# ---------------------------------------------------------------------------
# Signal contract
# ---------------------------------------------------------------------------

def test_signal_contract_accepts_leader_reset_reclaim(monkeypatch):
    monkeypatch.setattr(lab, "_ema_reclaim_state", lambda t, a, s: dict(GOOD_RECLAIM))

    sig = lab.signal_leader_reset_reclaim(lrr_features(), PARAMS)

    assert sig is not None
    assert sig.variant == "LEADER_RESET_RECLAIM"
    assert sig.side == "long"
    assert sig.features["lrr_reclaim_span"] == 10
    assert 0.04 <= sig.features["lrr_stop_loss_pct"] <= 0.10
    assert "prior_leadership_rs60" in sig.reasons
    assert "controlled_reset" in sig.reasons
    assert "ema10_reclaim_close_strength" in sig.reasons
    assert "earnings_calendar_not_retained_no_future_filter" in sig.reasons


@pytest.mark.parametrize("mutation", [
    {"rs60_spy": 0.01, "rs60_qqq": 0.01},          # no prior leadership
    {"r60": 0.05},                                   # weak prior momentum
    {"drawdown_from_high20": -0.02},                 # no reset yet
    {"drawdown_from_high20": -0.25},                 # trend break, not a reset
    {"above_ma200": False},                          # full trend break
    {"ma50": 120.0},                                 # below MA50 support band
    {"r10": -0.25},                                  # climax failure / crash
    {"r20": 0.70},                                   # parabolic run-up
])
def test_signal_rejects_non_qualifying_setups(monkeypatch, mutation):
    monkeypatch.setattr(lab, "_ema_reclaim_state", lambda t, a, s: dict(GOOD_RECLAIM))

    assert lab.signal_leader_reset_reclaim(lrr_features(**mutation), PARAMS) is None


def test_signal_requires_reclaim_with_close_strength(monkeypatch):
    monkeypatch.setattr(lab, "_ema_reclaim_state", lambda t, a, s: {**GOOD_RECLAIM, "reclaim": False})
    assert lab.signal_leader_reset_reclaim(lrr_features(), PARAMS) is None

    # Reclaim true on both spans but without an up close -> rejected.
    flat = {**GOOD_RECLAIM, "last_close": 98.0, "prior_close": 98.0}
    monkeypatch.setattr(lab, "_ema_reclaim_state", lambda t, a, s: dict(flat))
    assert lab.signal_leader_reset_reclaim(lrr_features(), PARAMS) is None


def test_signal_falls_back_to_ema20_reclaim(monkeypatch):
    def fake_reclaim(ticker, asof, span):
        if span == 10:
            return {**GOOD_RECLAIM, "reclaim": False}
        return dict(GOOD_RECLAIM)

    monkeypatch.setattr(lab, "_ema_reclaim_state", fake_reclaim)

    sig = lab.signal_leader_reset_reclaim(lrr_features(), PARAMS)

    assert sig is not None
    assert sig.features["lrr_reclaim_span"] == 20


# ---------------------------------------------------------------------------
# Risk: stop below reclaim low or ATR stop, clamped
# ---------------------------------------------------------------------------

def test_stop_uses_wider_of_atr_and_reclaim_low(monkeypatch):
    monkeypatch.setattr(lab, "_ema_reclaim_state", lambda t, a, s: {**GOOD_RECLAIM, "reclaim_low": 96.0})
    sig = lab.signal_leader_reset_reclaim(lrr_features(atr_pct=0.01), PARAMS)
    # reclaim stop = (100-96)/100 + 1% = 5% > atr stop 2%.
    assert sig.features["lrr_stop_loss_pct"] == pytest.approx(0.05)

    sig = lab.signal_leader_reset_reclaim(lrr_features(atr_pct=0.04), PARAMS)
    # atr stop = 8% > reclaim stop 5%.
    assert sig.features["lrr_stop_loss_pct"] == pytest.approx(0.08)

    monkeypatch.setattr(lab, "_ema_reclaim_state", lambda t, a, s: {**GOOD_RECLAIM, "reclaim_low": 70.0})
    sig = lab.signal_leader_reset_reclaim(lrr_features(atr_pct=0.01), PARAMS)
    assert sig.features["lrr_stop_loss_pct"] == pytest.approx(0.10)  # clamped


def test_simulate_trade_honors_lrr_stop(monkeypatch):
    seen = {}

    def fake_path(ticker, asof, side, max_hold, stop, target, trailing, timing):
        seen.update({"stop": stop, "target": target, "max_hold": max_hold})
        return (100.0, 105.0, "2026-03-20", "2026-03-11", 5, 0.05, "max_hold", 0.06, -0.01)

    monkeypatch.setattr(lab, "_simulate_path_custom", fake_path)
    monkeypatch.setattr(lab, "_benchmark_return", lambda *a: 0.0)
    signal = {
        "variant": "LEADER_RESET_RECLAIM", "ticker": "TST", "asof": "2026-03-10", "side": "long",
        "score": 80.0, "reasons": [], "features": {"lrr_stop_loss_pct": 0.07, "lrr_profit_target_pct": 0.12},
    }

    trade = lab.simulate_trade(signal, params=PARAMS)

    assert trade is not None
    assert seen["stop"] == pytest.approx(0.07)
    assert seen["target"] == pytest.approx(0.12)
    assert seen["max_hold"] == PARAMS.max_hold_days


# ---------------------------------------------------------------------------
# Miner trace fidelity for the new variant
# ---------------------------------------------------------------------------

LRR_MUTATIONS = [
    {},
    {"rs60_spy": 0.01, "rs60_qqq": 0.01},
    {"r60": 0.05},
    {"drawdown_from_high20": -0.02},
    {"drawdown_from_high20": -0.25},
    {"above_ma200": False},
    {"above_ma200": None},
    {"ma50": 120.0},
    {"ma50": None},
    {"r10": -0.25},
    {"r20": 0.70},
    {"bars": 60},
    {"price": 4.0, "avg_dvol20": 1_000_000.0},
]


@pytest.mark.parametrize("mutation", LRR_MUTATIONS)
def test_miner_trace_fidelity_for_leader_reset_reclaim(monkeypatch, mutation):
    monkeypatch.setattr(lab, "_ema_reclaim_state", lambda t, a, s: dict(GOOD_RECLAIM))

    trace = miner.trace_candidate("LEADER_RESET_RECLAIM", lrr_features(**mutation), PARAMS, BULL_CONTEXT)

    assert trace["fidelity_ok"], mutation


def test_miner_trace_fidelity_when_reclaim_fails(monkeypatch):
    monkeypatch.setattr(lab, "_ema_reclaim_state", lambda t, a, s: {**GOOD_RECLAIM, "reclaim": False})

    trace = miner.trace_candidate("LEADER_RESET_RECLAIM", lrr_features(), PARAMS, BULL_CONTEXT)

    assert trace["fidelity_ok"]
    assert trace["sole_blocker"] == "ema_reclaim_close_strength"


# ---------------------------------------------------------------------------
# Eligibility ladder
# ---------------------------------------------------------------------------

def _good_inputs():
    return dict(
        independent={"trade_count": 80, "expectancy": 0.01, "max_drawdown": -0.12},
        realistic={"accepted_trade_count": 50, "total_return": 0.25, "cagr": 0.10,
                   "max_drawdown": -0.06, "sharpe": 1.5},
        same_spy={"total_return": 0.10, "sharpe": 1.0},
        same_qqq={"total_return": 0.12, "sharpe": 1.1},
        exact={"trade_count": 80, "expectancy": 0.01},
        splits={
            "train": {"trade_count": 30, "expectancy": 0.01},
            "validation": {"trade_count": 25, "expectancy": 0.008},
            "test": {"trade_count": 25, "expectancy": 0.012, "rel_spy": 0.004, "rel_qqq": 0.003},
        },
        month_check={"passes": True},
        concentration={"top_ticker_pct": 0.10, "top_sector_pct": 0.40},
    )


def test_eligibility_yes_when_all_gates_pass():
    out = lrr._eligibility(**_good_inputs())
    assert out["answer"] == lrr.YES
    assert out["blockers"] == []


def test_eligibility_no_when_same_exposure_not_beaten():
    inputs = _good_inputs()
    inputs["same_qqq"] = {"total_return": 0.30, "sharpe": 2.0}
    out = lrr._eligibility(**inputs)
    assert out["answer"] == lrr.NO
    assert any("same-exposure" in b for b in out["blockers"])


def test_eligibility_no_when_independent_drawdown_excessive():
    inputs = _good_inputs()
    inputs["independent"] = {"trade_count": 80, "expectancy": 0.01, "max_drawdown": -0.50}
    out = lrr._eligibility(**inputs)
    assert out["answer"] == lrr.NO
    assert any("independent-trade maxDD" in b for b in out["blockers"])


def test_eligibility_maybe_when_only_flow_is_short():
    inputs = _good_inputs()
    inputs["independent"] = {"trade_count": 25, "expectancy": 0.01, "max_drawdown": -0.12}
    inputs["splits"]["test"] = {"trade_count": 10, "expectancy": 0.012, "rel_spy": 0.004}
    out = lrr._eligibility(**inputs)
    assert out["answer"] == lrr.MAYBE


def test_eligibility_no_when_month_dependent():
    inputs = _good_inputs()
    inputs["month_check"] = {"passes": False}
    out = lrr._eligibility(**inputs)
    assert out["answer"] == lrr.NO


# ---------------------------------------------------------------------------
# Safety guardrails
# ---------------------------------------------------------------------------

def test_proposal_doc_written_only_on_yes(tmp_path, monkeypatch):
    for name in ("OUT_JSON", "OUT_TXT", "OUT_DOC", "PROPOSAL_DOC"):
        monkeypatch.setattr(lrr, name, tmp_path / f"{name}.out")
    monkeypatch.setattr(lrr, "render_text", lambda res: ["x"])
    monkeypatch.setattr(lrr, "render_doc", lambda res: "x")
    monkeypatch.setattr(lrr, "render_proposal_doc", lambda res: "proposal")

    lrr.write_outputs({"paper_shadow": {"proposal_created": False}})
    assert not (tmp_path / "PROPOSAL_DOC.out").exists()

    lrr.write_outputs({"paper_shadow": {"proposal_created": True}})
    assert (tmp_path / "PROPOSAL_DOC.out").read_text() == "proposal\n"


def test_variant_registered_research_only_and_not_in_lab_defaults():
    assert "LEADER_RESET_RECLAIM" in lab.VARIANT_FUNCS
    assert "LEADER_RESET_RECLAIM" not in lab.DEFAULT_VARIANTS
    assert "LEADER_RESET_RECLAIM" in miner.GATE_SPECS
    assert "LEADER_RESET_RECLAIM" not in miner.EVAL_VARIANTS


def test_no_paper_trade_or_execution_governance_imports_for_phase_1i():
    forbidden_import_roots = {"execution", "governance", "broker", "live_capital", "council"}
    forbidden_calls = (
        "create_paper_signal(",
        "emit_paper_signal(",
        "insert_paper_signal(",
        "create_trade_proposal(",
        "emit_trade_proposal(",
        "submit_buy_order",
        "submit_sell_order",
        "close_position(",
        "strategy_registry.register",
    )
    text = Path(lrr.__file__).read_text(encoding="utf-8")
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


def test_production_thresholds_unchanged_and_short_a_frozen_after_phase_1i():
    filters = importlib.import_module("research.scanner_truth.filters")

    assert filters.SNI_ATR_CONTRACTION_THRESH == 0.85
    assert filters.SNI_VOL_SPIKE_THRESH == 1.4
    assert filters.VOY_MAX_EXTENSION_MA50 == 0.12
    assert filters.VOY_MA200_FLOOR == 0.92
    assert reg.is_frozen_strategy("SHORT") is True
    assert reg.is_active_paper_strategy("SHORT") is False

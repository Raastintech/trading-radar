"""Phase 1I.1 LRR_REGIME_GATED guardrail tests."""
from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core import strategy_registry as reg  # noqa: E402
from research import lrr_regime_gated_strategy as gated  # noqa: E402
from research import strategy_failure_reason_miner as miner  # noqa: E402
from research import strategy_lab_regime as regime  # noqa: E402
from research import strategy_research_lab as lab  # noqa: E402
from tests.unit.test_leader_reset_reclaim import GOOD_RECLAIM, lrr_features  # noqa: E402
from tests.unit.test_strategy_failure_miner import BULL_CONTEXT  # noqa: E402

PARAMS = lab.StrategyParams()


def _context(label):
    ctx = dict(BULL_CONTEXT)
    ctx["REGIME"] = {"label": label, "flags": {}}
    return ctx


# ---------------------------------------------------------------------------
# Allowed regimes work; blocked regimes block entries
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label", sorted(lab.LRR_ALLOWED_REGIMES))
def test_allowed_regimes_emit_signals(monkeypatch, label):
    monkeypatch.setattr(lab, "_ema_reclaim_state", lambda t, a, s: dict(GOOD_RECLAIM))

    sig = lab.signal_lrr_regime_gated(lrr_features(), PARAMS, _context(label))

    assert sig is not None
    assert sig.variant == "LRR_REGIME_GATED"
    assert sig.features["lrr_regime_label"] == label
    assert sig.reasons[0] == f"regime_gate:{label}"


@pytest.mark.parametrize("label", [
    regime.RISK_OFF,
    regime.MARKET_CORRECTION,
    regime.BULL_TREND,
    regime.CHOP,
    "UNKNOWN",
    None,
])
def test_blocked_regimes_block_entries(monkeypatch, label):
    monkeypatch.setattr(lab, "_ema_reclaim_state", lambda t, a, s: dict(GOOD_RECLAIM))

    assert lab.signal_lrr_regime_gated(lrr_features(), PARAMS, _context(label)) is None


def test_allowed_set_is_exactly_the_a_priori_spec():
    assert lab.LRR_ALLOWED_REGIMES == frozenset(
        {regime.TECH_LED_CORRECTION, regime.RECOVERY_RECLAIM, regime.HIGH_VOLATILITY}
    )
    spec = gated.gate_spec()
    assert sorted(spec["allowed_regimes"]) == sorted(lab.LRR_ALLOWED_REGIMES)
    assert spec["fixed_a_priori"] is True


def test_gated_variant_applies_same_base_rules(monkeypatch):
    monkeypatch.setattr(lab, "_ema_reclaim_state", lambda t, a, s: dict(GOOD_RECLAIM))
    ctx = _context(regime.TECH_LED_CORRECTION)

    # A setup rejected by base LRR rules must also be rejected when gated.
    weak = lrr_features(rs60_spy=0.01, rs60_qqq=0.01)
    assert lab.signal_leader_reset_reclaim(weak, PARAMS) is None
    assert lab.signal_lrr_regime_gated(weak, PARAMS, ctx) is None

    # Score and stop metadata are inherited unchanged from the base signal.
    base = lab.signal_leader_reset_reclaim(lrr_features(), PARAMS)
    wrapped = lab.signal_lrr_regime_gated(lrr_features(), PARAMS, ctx)
    assert wrapped.score == base.score
    assert wrapped.features["lrr_stop_loss_pct"] == base.features["lrr_stop_loss_pct"]


# ---------------------------------------------------------------------------
# Regime gate uses only as-of data; no future labels
# ---------------------------------------------------------------------------

def test_regime_gate_and_signal_never_touch_forward_data(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("regime gate / signal must not read forward data")

    monkeypatch.setattr(lab.d, "get_forward_window", boom)
    monkeypatch.setattr(lab, "_ema_reclaim_state", lambda t, a, s: dict(GOOD_RECLAIM))

    sig = lab.signal_lrr_regime_gated(lrr_features(), PARAMS, _context(regime.HIGH_VOLATILITY))
    assert sig is not None


def test_classifier_inputs_are_strictly_as_of(monkeypatch):
    """classify_regime must consume compute_features_asof only — the fake
    records every (symbol, asof) request and supplies no future fields."""
    seen = []

    def fake_features(symbol, asof):
        seen.append((symbol, str(asof)[:10]))
        return {"drawdown_from_high20": -0.06, "drawdown_from_high60": -0.08,
                "r5": 0.03, "r10": 0.01, "r20": -0.03, "above_ema20": True,
                "above_ma50": True, "above_ma200": True, "atr_pct": 0.01}

    monkeypatch.setattr(regime.d, "compute_features_asof", fake_features)

    out = regime.classify_regime("2026-03-10")

    assert out["label"] in {
        regime.BULL_TREND, regime.CHOP, regime.MARKET_CORRECTION, regime.TECH_LED_CORRECTION,
        regime.RISK_OFF, regime.RECOVERY_RECLAIM, regime.HIGH_VOLATILITY,
    }
    assert seen and all(asof == "2026-03-10" for _, asof in seen)
    assert out["data_reliability"]["manual_override"] is False


def test_miner_trace_fidelity_for_gated_variant(monkeypatch):
    monkeypatch.setattr(lab, "_ema_reclaim_state", lambda t, a, s: dict(GOOD_RECLAIM))
    for label in [regime.TECH_LED_CORRECTION, regime.BULL_TREND, regime.RISK_OFF, regime.HIGH_VOLATILITY]:
        for mutation in ({}, {"rs60_spy": 0.01, "rs60_qqq": 0.01}, {"drawdown_from_high20": -0.25}):
            trace = miner.trace_candidate("LRR_REGIME_GATED", lrr_features(**mutation), PARAMS, _context(label))
            assert trace["fidelity_ok"], (label, mutation)


def test_blocked_regime_is_recorded_as_the_gate_that_fired(monkeypatch):
    monkeypatch.setattr(lab, "_ema_reclaim_state", lambda t, a, s: dict(GOOD_RECLAIM))

    trace = miner.trace_candidate("LRR_REGIME_GATED", lrr_features(), PARAMS, _context(regime.RISK_OFF))

    assert not trace["accepted"]
    assert trace["sole_blocker"] == "regime_allowed_lrr"


# ---------------------------------------------------------------------------
# Decision ladder
# ---------------------------------------------------------------------------

def _block(exp=0.012, real_ret=0.20, real_dd=-0.06, sharpe=2.0, ind_dd=-0.15, n=80, accepted=50,
           test_n=25, test_exp=0.01):
    return {
        "exact": {"trade_count": n, "expectancy": exp},
        "independent": {"trade_count": n, "expectancy": exp, "max_drawdown": ind_dd},
        "realistic": {"accepted_trade_count": accepted, "total_return": real_ret, "cagr": 0.08,
                      "max_drawdown": real_dd, "sharpe": sharpe},
        "same_exposure_spy": {"total_return": 0.08, "sharpe": 1.0},
        "same_exposure_qqq": {"total_return": 0.09, "sharpe": 1.1},
        "walk_forward": {
            "train": {"trade_count": 30, "expectancy": 0.01},
            "validation": {"trade_count": 25, "expectancy": 0.01},
            "test": {"trade_count": test_n, "expectancy": test_exp},
        },
    }


def _passing_checks():
    return {k: {"passed": True, "detail": ""} for k in (
        "improves_sharpe_vs_same_exposure", "improves_maxdd_vs_unrestricted_lrr",
        "avoids_nov_2024_concentration", "improves_2026_ytd_decay",
        "walk_forward_test_positive", "walk_forward_majority_positive",
        "works_in_multiple_months", "works_across_tickers_and_themes",
        "beats_random_after_costs", "beats_same_exposure_return_after_costs",
    )} | {"all_passed": True}


def test_ladder_candidate_when_everything_passes():
    out = gated.decision_ladder(gated=_block(), checks=_passing_checks(),
                                concentration={"top_ticker_pct": 0.10})
    assert out["label"] == gated.CANDIDATE
    assert out["answer"] == gated.YES


def test_ladder_portfolio_risk_on_ruinous_independent_stream():
    out = gated.decision_ladder(gated=_block(ind_dd=-0.60), checks=_passing_checks(),
                                concentration={"top_ticker_pct": 0.10})
    assert out["label"] == gated.PORTFOLIO_RISK
    assert out["answer"] == gated.NO


def test_ladder_overfit_when_anti_overfit_checks_fail():
    checks = _passing_checks()
    checks["works_in_multiple_months"] = {"passed": False, "detail": ""}
    out = gated.decision_ladder(gated=_block(), checks=checks,
                                concentration={"top_ticker_pct": 0.10})
    assert out["label"] == gated.OVERFIT
    assert out["answer"] == gated.NO


def test_ladder_reject_when_test_split_negative():
    out = gated.decision_ladder(gated=_block(test_exp=-0.002), checks=_passing_checks(),
                                concentration={"top_ticker_pct": 0.10})
    assert out["label"] == gated.REJECT
    assert out["answer"] == gated.NO


def test_ladder_maybe_on_low_flow_or_thin_test():
    out = gated.decision_ladder(gated=_block(n=25), checks=_passing_checks(),
                                concentration={"top_ticker_pct": 0.10})
    assert out["label"] == gated.LOW_FLOW
    assert out["answer"] == gated.MAYBE

    out = gated.decision_ladder(gated=_block(test_n=8), checks=_passing_checks(),
                                concentration={"top_ticker_pct": 0.10})
    assert out["label"] == gated.NEED_MORE
    assert out["answer"] == gated.MAYBE


# ---------------------------------------------------------------------------
# Safety guardrails
# ---------------------------------------------------------------------------

def test_proposal_doc_written_only_on_yes(tmp_path, monkeypatch):
    for name in ("OUT_JSON", "OUT_TXT", "OUT_DOC", "GATE_SPEC_JSON", "GATE_SPEC_DOC", "PROPOSAL_DOC"):
        monkeypatch.setattr(gated, name, tmp_path / f"{name}.out")
    monkeypatch.setattr(gated, "render_text", lambda res: ["x"])
    monkeypatch.setattr(gated, "render_doc", lambda res: "x")
    monkeypatch.setattr(gated, "render_gate_spec_doc", lambda spec: "spec")
    monkeypatch.setattr(gated, "render_proposal_doc", lambda res: "proposal")
    base = {"generated_at": "t", "regime_gate_spec": {}}

    gated.write_outputs({**base, "paper_shadow": {"proposal_created": False}})
    assert not (tmp_path / "PROPOSAL_DOC.out").exists()

    gated.write_outputs({**base, "paper_shadow": {"proposal_created": True}})
    assert (tmp_path / "PROPOSAL_DOC.out").read_text() == "proposal\n"


def test_gated_variant_not_in_production_registry_or_lab_defaults():
    assert "LRR_REGIME_GATED" in lab.VARIANT_FUNCS
    assert "LRR_REGIME_GATED" not in lab.DEFAULT_VARIANTS
    assert "LRR_REGIME_GATED" in miner.GATE_SPECS
    assert "LRR_REGIME_GATED" not in reg.SLEEVE_REGISTRY
    assert "LEADER_RESET_RECLAIM" not in reg.SLEEVE_REGISTRY


def test_no_paper_trade_or_execution_governance_imports_for_phase_1i1():
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
    text = Path(gated.__file__).read_text(encoding="utf-8")
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


def test_production_thresholds_unchanged_and_short_a_frozen_after_phase_1i1():
    filters = importlib.import_module("research.scanner_truth.filters")

    assert filters.SNI_ATR_CONTRACTION_THRESH == 0.85
    assert filters.SNI_VOL_SPIKE_THRESH == 1.4
    assert filters.VOY_MAX_EXTENSION_MA50 == 0.12
    assert filters.VOY_MA200_FLOOR == 0.92
    assert reg.is_frozen_strategy("SHORT") is True
    assert reg.is_active_paper_strategy("SHORT") is False

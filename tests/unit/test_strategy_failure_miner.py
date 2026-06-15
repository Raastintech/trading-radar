"""Phase 1H.4 failure-reason miner and filter-replacement guardrail tests."""
from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core import strategy_registry as reg  # noqa: E402
from research import filter_replacement_counterfactual as cf  # noqa: E402
from research import strategy_failure_reason_miner as miner  # noqa: E402
from research import strategy_lab_data as d  # noqa: E402
from research import strategy_lab_regime as regime  # noqa: E402
from research import strategy_research_lab as lab  # noqa: E402

PARAMS = lab.StrategyParams()
SNIPER_TICKER = sorted(d.SNIPER_LARGE_CAP_UNIVERSE)[0]
POWER_THEME = sorted(d.POWER_THEMES)[0]

BULL_CONTEXT = {
    "REGIME": {"label": regime.BULL_TREND, "flags": {}},
    "SPY": {"drawdown_from_high60": -0.01, "drawdown_from_high20": -0.01, "r40": 0.02, "r10": 0.01},
    "QQQ": {"drawdown_from_high60": -0.01, "drawdown_from_high20": -0.01, "r10": 0.01, "above_ema20": True},
    "SMH": {"r10": 0.01},
    "XLK": {"r10": 0.01},
}


def base_features(**overrides):
    f = {
        "ticker": SNIPER_TICKER,
        "asof": "2026-03-10",
        "bars": 300,
        "price": 100.0,
        "open": 99.0,
        "high": 101.0,
        "low": 98.0,
        "volume": 2_000_000.0,
        "avg_vol20": 1_000_000.0,
        "avg_dvol20": 50_000_000.0,
        "ma20": 97.0,
        "ma50": 96.0,
        "ma200": 90.0,
        "ema20": 97.0,
        "ema50": 95.0,
        "ma50_rising": True,
        "atr_pct": 0.02,
        "atr_contraction": 0.70,
        "first_breakout": True,
        "breakout": True,
        "above_ma50": True,
        "above_ma200": True,
        "above_ema20": True,
        "spy_above_ma200": True,
        "rs10_spy": 0.05,
        "rs20_spy": 0.10,
        "rs50_spy": 0.10,
        "rs60_spy": 0.10,
        "rs20_qqq": 0.08,
        "rs60_qqq": 0.10,
        "sector_rs20": 0.06,
        "r5": 0.02,
        "r10": 0.05,
        "r20": 0.10,
        "r40": 0.12,
        "r60": 0.20,
        "dvol_ratio": 1.20,
        "up_vol_ratio": 1.20,
        "vol_expansion": 1.50,
        "range10_pct": 0.05,
        "ext_ema20": 0.031,
        "high20": 100.5,
        "low20": 90.0,
        "drawdown_from_high20": -0.06,
        "drawdown_from_high60": -0.10,
        "sector": "Technology",
        "theme": POWER_THEME,
        "market_regime": regime.BULL_TREND,
    }
    f.update(overrides)
    return f


# ---------------------------------------------------------------------------
# Task 10.1 — accepted/rejected winner/loser labeling
# ---------------------------------------------------------------------------

def test_classification_labels_cover_all_quadrants():
    win = {"matured": True, "signed_fwd_10d": 0.05}
    loss = {"matured": True, "signed_fwd_10d": -0.05}
    flat = {"matured": True, "signed_fwd_10d": 0.001}
    young = {"matured": False}

    assert miner.classify_case(True, win) == miner.ACCEPTED_WINNER
    assert miner.classify_case(True, loss) == miner.ACCEPTED_LOSER
    assert miner.classify_case(True, flat) == miner.ACCEPTED_NEUTRAL
    assert miner.classify_case(False, win) == miner.REJECTED_WINNER
    assert miner.classify_case(False, loss) == miner.REJECTED_LOSER
    assert miner.classify_case(False, flat) == miner.REJECTED_NEUTRAL
    assert miner.classify_case(True, young) == miner.UNKNOWN_NOT_MATURED
    assert miner.classify_case(False, young) == miner.UNKNOWN_NOT_MATURED


def test_short_side_outcome_is_sign_inverted(monkeypatch):
    dates = pd.bdate_range("2026-03-11", periods=21)
    frame = pd.DataFrame(
        {
            "open": [100.0] * 21,
            "high": [101.0] * 21,
            "low": [89.0] * 21,
            "close": [90.0] * 21,
            "volume": [1e6] * 21,
        },
        index=dates,
    )
    monkeypatch.setattr(miner.d, "get_forward_window", lambda t, asof, h: frame)
    miner.forward_outcome.cache_clear()

    out = miner.outcome_for({"ticker": "TST", "asof": "2026-03-10", "price": 100.0}, "short")

    assert out["matured"] is True
    assert out["fwd_10d"] == pytest.approx(-0.10)
    assert out["signed_fwd_10d"] == pytest.approx(0.10)
    assert miner.classify_case(True, out) == miner.ACCEPTED_WINNER
    miner.forward_outcome.cache_clear()


# ---------------------------------------------------------------------------
# Task 10.2 — forward data only after the signal date
# ---------------------------------------------------------------------------

def test_forward_outcome_uses_only_bars_after_signal_date(monkeypatch):
    seen = {}

    def fake_forward(ticker, asof, horizon):
        seen["asof"] = asof
        dates = pd.bdate_range(pd.Timestamp(asof) + pd.Timedelta(days=1), periods=21)
        closes = [100.0 + i for i in range(21)]
        return pd.DataFrame(
            {"open": closes, "high": [c + 1 for c in closes], "low": [c - 1 for c in closes],
             "close": closes, "volume": [1e6] * 21},
            index=dates,
        )

    monkeypatch.setattr(miner.d, "get_forward_window", fake_forward)
    miner.forward_outcome.cache_clear()

    out = miner.outcome_for({"ticker": "TST", "asof": "2026-03-10", "price": 99.0}, "long")

    assert seen["asof"] == "2026-03-10"
    # entry = first forward open (100); fwd10 = close of 10th forward bar (109).
    assert out["fwd_10d"] == pytest.approx(0.09)
    assert out["fwd_5d"] == pytest.approx(0.04)
    miner.forward_outcome.cache_clear()


def test_gate_tracing_never_touches_forward_data(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("gate tracing must not read forward data")

    monkeypatch.setattr(miner.d, "get_forward_window", boom)
    monkeypatch.setattr(lab, "_ema_reclaim_state", lambda t, a, s: {"reclaim": True, "reclaim_low": 95.0})

    for variant in miner.EVAL_VARIANTS:
        trace = miner.trace_candidate(variant, base_features(), PARAMS, BULL_CONTEXT)
        assert trace["fidelity_ok"], variant


# ---------------------------------------------------------------------------
# Task 10.3 — no-lookahead gate values / trace fidelity vs original functions
# ---------------------------------------------------------------------------

FIDELITY_MUTATIONS = [
    {},
    {"first_breakout": False},
    {"atr_contraction": 0.95},
    {"atr_contraction": None},
    {"volume": 1_000_000.0},
    {"above_ma50": False},
    {"ma50_rising": False},
    {"rs10_spy": -0.01},
    {"spy_above_ma200": False},
    {"spy_above_ma200": None},
    {"bars": 60},
    {"bars": 100},
    {"price": 4.0, "avg_dvol20": 1_000_000.0},
    {"ma200": None},
    {"ma200": 120.0},
    {"ext_ema20": 0.30},
    {"ext_ema20": 0.18},
    {"r20": 0.01},
    {"r60": 0.05},
    {"r5": -0.05},
    {"r5": 0.40},
    {"r10": 0.60},
    {"rs20_spy": -0.05, "sector_rs20": -0.05},
    {"above_ema20": False},
    {"above_ema20": None},
    {"vol_expansion": 0.80},
    {"dvol_ratio": 0.50},
    {"up_vol_ratio": 0.50},
    {"sector": "Utilities", "theme": "boring"},
    {"drawdown_from_high20": -0.25},
    {"drawdown_from_high60": -0.30},
    {"low20": 99.5},
    {"r60": 0.15, "above_ema20": False, "r10": -0.05, "rs20_qqq": -0.05},
]


@pytest.mark.parametrize("variant", list(miner.EVAL_VARIANTS))
def test_traced_acceptance_matches_original_signal_functions(monkeypatch, variant):
    monkeypatch.setattr(lab, "_ema_reclaim_state", lambda t, a, s: {"reclaim": True, "reclaim_low": 95.0})
    contexts = [BULL_CONTEXT, {**BULL_CONTEXT, "REGIME": {"label": regime.MARKET_CORRECTION, "flags": {}}}]
    weak_qqq = dict(BULL_CONTEXT)
    weak_qqq["QQQ"] = {"r10": -0.03, "above_ema20": False, "drawdown_from_high60": -0.08, "drawdown_from_high20": -0.06}
    contexts.append(weak_qqq)
    for context in contexts:
        for mutation in FIDELITY_MUTATIONS:
            f = base_features(**mutation)
            trace = miner.trace_candidate(variant, f, PARAMS, context)
            assert trace["fidelity_ok"], f"{variant} mutation={mutation} regime={context['REGIME']['label']}"


# ---------------------------------------------------------------------------
# Task 10.4 — gate scoring math
# ---------------------------------------------------------------------------

def _sole(matured, avg_fwd, win_rate, loser_rate):
    return {"matured": matured, "avg_fwd_10d": avg_fwd, "winner_rate": win_rate, "loser_rate": loser_rate}


def test_gate_verdict_thresholds():
    assert miner.gate_verdict(_sole(5, 0.05, 0.9, 0.0))[0] == miner.NEED_MORE_DATA
    assert miner.gate_verdict(_sole(50, -0.02, 0.10, 0.40))[0] == miner.KEEP_HARD_BLOCK
    assert miner.gate_verdict(_sole(50, 0.03, 0.50, 0.20))[0] == miner.OVERBLOCKS_WINNERS
    assert miner.gate_verdict(_sole(50, 0.001, 0.25, 0.25))[0] == miner.NO_SIGNAL
    assert miner.gate_verdict(_sole(50, 0.007, 0.30, 0.28))[0] == miner.KEEP_SOFT_WARNING


def test_outcome_accumulator_math():
    acc = miner._new_outcome_acc()
    win = {"matured": True, "signed_fwd_10d": 0.05, "rel_spy_10d": 0.02, "rel_qqq_10d": 0.01, "mfe_10d": 0.06, "mae_10d": -0.01}
    loss = {"matured": True, "signed_fwd_10d": -0.04, "rel_spy_10d": -0.05, "rel_qqq_10d": -0.06, "mfe_10d": 0.01, "mae_10d": -0.05}
    miner._acc_outcome(acc, win, miner.REJECTED_WINNER)
    miner._acc_outcome(acc, loss, miner.REJECTED_LOSER)
    miner._acc_outcome(acc, {"matured": False}, miner.UNKNOWN_NOT_MATURED)
    out = miner._final_outcome(acc)

    assert out["count"] == 3
    assert out["matured"] == 2
    assert out["winners"] == 1 and out["losers"] == 1
    assert out["winner_rate"] == 0.5 and out["loser_rate"] == 0.5
    assert out["avg_fwd_10d"] == pytest.approx(0.005)
    assert out["opportunity_cost_sum"] == pytest.approx(0.05)
    assert out["avoided_loss_sum"] == pytest.approx(0.04)
    assert out["net_value_sum"] == pytest.approx(-0.01)


# ---------------------------------------------------------------------------
# Task 10.5/10.6 — replacement scope and counterfactual integrity
# ---------------------------------------------------------------------------

def _trace_stub(ticker, accepted, sole_blocker=None, score=80.0, variant="PROD_SNIPER_CURRENT"):
    signal = lab.Signal(variant, ticker, "2026-03-10", "long", score, ["t"], {"ticker": ticker}) if accepted else None
    return {
        "variant": variant,
        "ticker": ticker,
        "asof": "2026-03-10",
        "side": "long",
        "accepted": accepted,
        "sole_blocker": sole_blocker,
        "failed_gates": [] if accepted else [sole_blocker or "other_gate"],
        "score": score,
        "signal": signal,
        "state": {},
        "selected": accepted,
        "fidelity_ok": True,
        "gates": [],
    }


def _fake_iter(traces):
    def fake(start, end, *, variants, params, config, max_dates=None):
        features = [base_features(ticker=t["ticker"]) for t in traces]
        yield 0, pd.Timestamp("2026-03-10"), features, BULL_CONTEXT, {"PROD_SNIPER_CURRENT": traces}
    return fake


def _spec(mode, condition, target="atr_contraction_lt_0_85"):
    return cf.ReplacementSpec("TEST_SPEC", "PROD_SNIPER_CURRENT", target, mode, "old", "new", "test", condition)


def test_soften_admits_only_where_target_gate_sole_fired(monkeypatch):
    traces = [
        _trace_stub("AAA", accepted=True),
        _trace_stub("BBB", accepted=False, sole_blocker="atr_contraction_lt_0_85", score=75.0),
        _trace_stub("CCC", accepted=False, sole_blocker="first_breakout", score=90.0),
        _trace_stub("DDD", accepted=False, sole_blocker=None, score=95.0),
    ]
    monkeypatch.setattr(miner, "iter_evaluation", _fake_iter(traces))
    spec = _spec("SOFTEN", lambda f, p, c, s: True)

    out = cf.collect_counterfactual_signals("2026-03-01", "2026-03-31", specs=[spec])

    assert [s.ticker for s in out["baseline"]["PROD_SNIPER_CURRENT"]] == ["AAA"]
    assert sorted(s.ticker for s in out["modified"]["TEST_SPEC"]) == ["AAA", "BBB"]
    assert out["changed_keys"]["TEST_SPEC"] == [("BBB", "2026-03-10")]
    admitted = [s for s in out["modified"]["TEST_SPEC"] if s.ticker == "BBB"]
    assert admitted[0].reasons == ["replacement:TEST_SPEC"]


def test_counterfactual_keeps_other_logic_unchanged_when_condition_never_fires(monkeypatch):
    traces = [
        _trace_stub("AAA", accepted=True),
        _trace_stub("BBB", accepted=False, sole_blocker="atr_contraction_lt_0_85"),
    ]
    monkeypatch.setattr(miner, "iter_evaluation", _fake_iter(traces))
    spec = _spec("SOFTEN", lambda f, p, c, s: False)

    out = cf.collect_counterfactual_signals("2026-03-01", "2026-03-31", specs=[spec])

    assert [s.ticker for s in out["modified"]["TEST_SPEC"]] == [s.ticker for s in out["baseline"]["PROD_SNIPER_CURRENT"]]
    assert out["changed_keys"].get("TEST_SPEC", []) == []


def test_tighten_removes_only_blocked_accepted_candidates(monkeypatch):
    traces = [
        _trace_stub("AAA", accepted=True, score=90.0),
        _trace_stub("BBB", accepted=True, score=80.0),
        _trace_stub("CCC", accepted=False, sole_blocker="first_breakout"),
    ]
    monkeypatch.setattr(miner, "iter_evaluation", _fake_iter(traces))
    spec = _spec("TIGHTEN", lambda f, p, c, s: f["ticker"] == "BBB", target="protection_new")

    out = cf.collect_counterfactual_signals("2026-03-01", "2026-03-31", specs=[spec])

    assert [s.ticker for s in out["baseline"]["PROD_SNIPER_CURRENT"]] == ["AAA", "BBB"]
    assert [s.ticker for s in out["modified"]["TEST_SPEC"]] == ["AAA"]
    assert out["changed_keys"]["TEST_SPEC"] == [("BBB", "2026-03-10")]


# ---------------------------------------------------------------------------
# Task 10.7/10.8 — too_extended and ATR replacement conditions
# ---------------------------------------------------------------------------

def test_too_extended_replacement_admits_power_leader_only():
    power = base_features(theme=POWER_THEME, rs20_spy=0.15, sector_rs20=0.15, drawdown_from_high20=-0.03)
    assert cf._c_rsmom_power_leader(power, PARAMS, BULL_CONTEXT, {}) is True

    non_power = base_features(theme="boring", rs20_spy=0.15, sector_rs20=0.15, drawdown_from_high20=-0.03)
    assert cf._c_rsmom_power_leader(non_power, PARAMS, BULL_CONTEXT, {}) is False

    deep_pullback = base_features(theme=POWER_THEME, rs20_spy=0.15, sector_rs20=0.15, drawdown_from_high20=-0.10)
    assert cf._c_rsmom_power_leader(deep_pullback, PARAMS, BULL_CONTEXT, {}) is False


def test_atr_contraction_replacement_conditions():
    strong_volume = base_features(volume=2_000_000.0, avg_vol20=1_000_000.0)
    assert cf._c_sniper_vol_expansion(strong_volume, PARAMS, BULL_CONTEXT, {}) is True
    weak_volume = base_features(volume=1_500_000.0, avg_vol20=1_000_000.0)
    assert cf._c_sniper_vol_expansion(weak_volume, PARAMS, BULL_CONTEXT, {}) is False

    accel = base_features(rs10_spy=0.03, r5=0.01)
    assert cf._c_sniper_rs_accel(accel, PARAMS, BULL_CONTEXT, {}) is True
    no_accel = base_features(rs10_spy=0.01, r5=0.01)
    assert cf._c_sniper_rs_accel(no_accel, PARAMS, BULL_CONTEXT, {}) is False


# ---------------------------------------------------------------------------
# Task 10.9-10.13 — safety guardrails
# ---------------------------------------------------------------------------

def test_proposal_doc_written_only_when_a_replacement_is_ready(tmp_path, monkeypatch):
    for name in ("OUT_JSON", "OUT_TXT", "OUT_DOC", "PROPOSAL_DOC"):
        monkeypatch.setattr(cf, name, tmp_path / f"{name}.out")
    monkeypatch.setattr(miner, "OUT_JSON", tmp_path / "no_miner.json")
    monkeypatch.setattr(cf, "render_text", lambda res: ["x"])
    monkeypatch.setattr(cf, "render_doc", lambda res: "x")
    monkeypatch.setattr(cf, "render_proposal_doc", lambda res: "proposal")

    res = {"paper_shadow": {"proposal_created": False}}
    cf.write_outputs(res)
    assert not (tmp_path / "PROPOSAL_DOC.out").exists()

    res = {"paper_shadow": {"proposal_created": True}}
    cf.write_outputs(res)
    assert (tmp_path / "PROPOSAL_DOC.out").read_text() == "proposal\n"


def test_no_paper_trade_or_execution_governance_imports_for_1h4_modules():
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
    for module in (miner, cf):
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


def test_production_thresholds_unchanged_and_short_a_frozen_after_phase_1h4():
    filters = importlib.import_module("research.scanner_truth.filters")

    assert filters.SNI_ATR_CONTRACTION_THRESH == 0.85
    assert filters.SNI_VOL_SPIKE_THRESH == 1.4
    assert filters.VOY_MAX_EXTENSION_MA50 == 0.12
    assert filters.VOY_MA200_FLOOR == 0.92
    assert reg.is_frozen_strategy("SHORT") is True
    assert reg.is_active_paper_strategy("SHORT") is False

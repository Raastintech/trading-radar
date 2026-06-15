"""Phase 1G.16 — Short Detection Truth Audit tests (research-only).

Covers the QQQ-aware short-state classifier, the simple baseline detectors, the
failed-leader classification, and the hard guardrails: the audit must never
import execution / governance / live-capital / Veto-Council code, never emit
paper signals or trade proposals, and never unfreeze SHORT_A.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import research.short_detection_truth_audit as sda  # noqa: E402
import research.short_detection_forward_validation as sdf  # noqa: E402


# ── Task 6: QQQ-aware state classifier ───────────────────────────────────────
def _healthy_bull():
    return dict(
        spy_above_ma50=True, spy_above_ma200=True, vix=15.0,
        qqq_ret_5d=1.5, qqq_rel_spy_5d=0.5, qqq_below_ema20=False,
        tech_rel_spy_5d=0.8, tech_breadth_frac_above_ema20=0.85,
        vxx_ret_3d=-3.0, n_failed_leaders=0,
    )


def test_bull_tape_suppression_still_works_in_broad_bull():
    r = sda.classify_short_state(**_healthy_bull())
    assert r["state"] == sda.STATE_SHORTS_OFF
    assert r["flags"]["bull_tape"] is True


def test_qqq_tactical_breakdown_detected_even_if_spy_above_200d():
    args = _healthy_bull()
    # SPY still structurally bull, but QQQ/tech break down hard.
    args.update(
        qqq_ret_5d=-5.1, qqq_rel_spy_5d=-2.2, qqq_below_ema20=True,
        tech_rel_spy_5d=-5.8, tech_breadth_frac_above_ema20=0.3,
        vxx_ret_3d=7.1,
    )
    r = sda.classify_short_state(**args)
    assert r["flags"]["bull_tape"] is True          # SPY still bull
    assert r["state"] != sda.STATE_SHORTS_OFF        # but NOT suppressed
    assert r["state"] == sda.STATE_TACTICAL_SHORT_RESEARCH


def test_vxx_rising_adds_stress_warning():
    args = _healthy_bull()
    args.update(vxx_ret_3d=8.0, qqq_ret_5d=-1.0)  # only vol stress + mild QQQ
    r = sda.classify_short_state(**args)
    assert r["flags"]["vxx_stress"] is True
    assert r["state"] in (sda.STATE_HEDGE_WATCH, sda.STATE_TACTICAL_SHORT_RESEARCH)


def test_classifier_never_auto_activates_short_regime():
    # Even the most bearish synthetic inputs cap at TACTICAL_SHORT_RESEARCH.
    args = dict(
        spy_above_ma50=False, spy_above_ma200=False, vix=35.0,
        qqq_ret_5d=-12.0, qqq_rel_spy_5d=-6.0, qqq_below_ema20=True,
        tech_rel_spy_5d=-9.0, tech_breadth_frac_above_ema20=0.05,
        vxx_ret_3d=40.0, n_failed_leaders=10,
    )
    r = sda.classify_short_state(**args)
    assert r["state"] != sda.STATE_SHORT_REGIME_ACTIVE


# ── Phase 1G.16 fix: SMH tech/semis proxy coverage ──────────────────────────
def test_tech_proxy_prefers_smh_when_present():
    assert sda.pick_tech_proxy({"SMH": True, "XLK": True}) == "SMH"


def test_tech_proxy_falls_back_to_xlk_when_smh_missing():
    assert sda.pick_tech_proxy({"SMH": False, "XLK": True}) == "XLK"
    assert sda.pick_tech_proxy({"XLK": True}) == "XLK"   # SMH key absent
    assert sda.pick_tech_proxy({}) == "XLK"               # nothing available


def test_reconstruct_tape_uses_smh_when_cached(monkeypatch):
    """End-to-end: when SMH has a frame, the tape's tech proxy is SMH; when SMH
    is absent the tape falls back to XLK. Drives load_prices via monkeypatch so
    the test does not depend on the live cache."""
    import pandas as pd
    cal = pd.date_range("2026-01-01", periods=60, freq="B")

    def _frame(start=100.0):
        return pd.DataFrame({"close": [start - i * 0.1 for i in range(len(cal))],
                             "high": [start] * len(cal), "low": [start - 5] * len(cal),
                             "open": [start] * len(cal), "volume": [1_000_000] * len(cal)},
                            index=cal)

    present = {"SPY", "QQQ", "IWM", "SMH", "XLK", "VXX"}

    def fake_load(t, prefer_deep=True):
        return _frame() if t.upper() in present else None

    monkeypatch.setattr(sda.dataio, "load_prices", fake_load)
    monkeypatch.setattr(sda.dataio, "benchmark_calendar", lambda: cal)
    tape = sda.reconstruct_tape(cal)
    assert tape["tech_proxy_symbol"] == "SMH"

    present.discard("SMH")            # SMH no longer cached → fall back
    tape2 = sda.reconstruct_tape(cal)
    assert tape2["tech_proxy_symbol"] == "XLK"


# ── Task 4: failed-leader classification ─────────────────────────────────────
def test_failed_leader_classification():
    arch = sda._classify_candidate(
        ticker="NVDA", r5=-6.0, below20=True, below50=False,
        alpha_item={"bucket": "Too Late / Crowded", "return_20d_pct": 25.0,
                    "options_quality": ""},
        in_power=False, index_like=False,
    )
    assert arch == sda.ARCH_FAILED_LEADER


def test_power_trend_unwind_classification():
    arch = sda._classify_candidate(
        ticker="SMCI", r5=-9.0, below20=True, below50=True,
        alpha_item={}, in_power=True, index_like=False,
    )
    assert arch == sda.ARCH_POWER_TREND_UNWIND


def test_index_hedge_only_classification():
    # Move tracks the index closely → better expressed as an index hedge.
    arch = sda._classify_candidate(
        ticker="AAPL", r5=-5.2, below20=True, below50=False,
        alpha_item={}, in_power=False, index_like=True,
    )
    assert arch == sda.ARCH_INDEX_HEDGE


# ── Task 3: simple baseline detectors ────────────────────────────────────────
def _synthetic_tape():
    return {
        "etf_returns": {"QQQ": {"ret_5d_pct": -5.1, "ret_3d_pct": -4.4}},
        "qqq_vs_spy": {"rel_5d_pct": -2.2},
        "tech_vs_spy": {"rel_5d_pct": -5.8},
        "vxx_3d_pct": 7.1,
    }


def test_simple_baselines_fire_on_tech_breakdown():
    breadth = {"frac_above_ema20": 0.3, "n_failed_leaders": 1}
    res = sda.baseline_detectors(_synthetic_tape(), breadth)
    d = res["detectors"]
    assert d["A"]["triggered"] is True   # QQQ downside velocity
    assert d["B"]["triggered"] is True   # QQQ rel weakness
    assert d["C"]["triggered"] is True   # tech breakdown
    assert d["E"]["triggered"] is True   # VXX stress
    assert d["F"]["triggered"] is True   # breadth
    assert res["n_triggered"] >= 4


def test_simple_baselines_quiet_on_healthy_tape():
    tape = {
        "etf_returns": {"QQQ": {"ret_5d_pct": 1.2, "ret_3d_pct": 0.8}},
        "qqq_vs_spy": {"rel_5d_pct": 0.5},
        "tech_vs_spy": {"rel_5d_pct": 0.9},
        "vxx_3d_pct": -2.0,
    }
    res = sda.baseline_detectors(tape, {"frac_above_ema20": 0.85, "n_failed_leaders": 0})
    assert res["n_triggered"] == 0
    assert res["any_triggered"] is False


# ── Task 7: forward verdict ladder ───────────────────────────────────────────
def test_forward_verdict_need_more_data_when_young(tmp_path):
    p = tmp_path / "hist.jsonl"
    p.write_text("")  # empty spine
    r = sdf.build(history_path=p)
    assert r["verdict"] == "NEED_MORE_DATA"


# ── guardrails: research-only, cache-only, SHORT_A stays frozen ──────────────
_FORBIDDEN_IMPORTS = (
    "execution.order_manager", "order_manager", "paper_governance",
    "core.config", "veto_council", "alpaca", "tradier",
)


def _src(modpath: str) -> str:
    return open(os.path.join(os.path.dirname(__file__), "..", "..", modpath)).read()


def test_no_forbidden_imports_in_audit_module():
    src = _src("research/short_detection_truth_audit.py")
    for bad in _FORBIDDEN_IMPORTS:
        assert f"import {bad}" not in src and f"from {bad}" not in src, bad


def test_no_forbidden_imports_in_forward_module():
    src = _src("research/short_detection_forward_validation.py")
    for bad in _FORBIDDEN_IMPORTS:
        assert f"import {bad}" not in src and f"from {bad}" not in src, bad


def test_no_paper_signal_or_trade_proposal_writes():
    for modpath in ("research/short_detection_truth_audit.py",
                    "research/short_detection_forward_validation.py"):
        src = _src(modpath).lower()
        assert "insert into paper_signals" not in src
        assert "submit_order" not in src
        assert "trade_proposal" not in src


def test_short_a_remains_frozen_in_registry():
    # The audit must not touch the sleeve registry; SHORT stays FROZEN.
    from core import strategy_registry as reg
    assert reg.SLEEVE_REGISTRY["SHORT"].status == reg.FROZEN


def test_audit_declares_short_a_frozen_and_research_only():
    # Build a tiny audit payload offline and check the scope-guard fields.
    a = sda.build_audit()
    assert "FROZEN" in a["short_a_status"]
    assert a["research_only"] is True
    # never emits an ACTIVE short regime as the proposed state
    assert a["proposed_state"]["state"] != sda.STATE_SHORT_REGIME_ACTIVE

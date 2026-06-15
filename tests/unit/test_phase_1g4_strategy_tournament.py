"""
tests/unit/test_phase_1g4_strategy_tournament.py — Phase 1G.4

Strategy Tournament (research-only) invariants:
  - no paper_signals / execution / governance / live-capital imports
  - no DB mutation (module never opens the DB)
  - immature outcomes are excluded from pass/fail
  - random controls are generated deterministically WITHOUT lookahead
  - a verdict requires the minimum mature sample
  - options expression rejects on wide spread
  - CSP/Wheel is rejected when assignment / buying power is not acceptable
  - short families stay research-only and carry no trade language
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from research import strategy_tournament as st

SRC = (Path(__file__).resolve().parents[2] / "research" / "strategy_tournament.py").read_text()


# ── source-level safety (no forbidden surfaces) ──────────────────────
def test_no_forbidden_imports_or_writes():
    forbidden = [
        "from execution", "import execution",
        "from council", "import council",
        "core.config", "paper_governance", "order_manager",
        "submit_order", "place_order", "create_paper_signal",
        "INSERT INTO", "UPDATE ", "DELETE FROM",
        "ALLOW_LIVE_CAPITAL", "PAPER_TRADING", "ALPACA_PAPER",
    ]
    low = SRC
    for bad in forbidden:
        assert bad not in low, f"forbidden surface in strategy_tournament.py: {bad}"


def test_module_marks_research_only():
    s = st.build_tournament()
    assert s["research_only"] is True
    assert s["kind"] == "strategy_tournament"


def test_no_trade_language_in_output():
    s = st.build_tournament()
    blob = (st.render_text(s) + "\n" + st.render_doc(s)).lower()
    for bad in ("buy now", "sell now", "short now", "trade approved", "execute order"):
        assert bad not in blob, f"forbidden phrase leaked: {bad}"


# ── maturity / sample gating ─────────────────────────────────────────
def _ev(side="long", r5=None, r10=None, mae=-2.0, mfe=3.0, rel5=0.5, clean=True):
    return {
        "side": side, "in_clean_epoch": clean,
        "label": st.LBL_RESEARCH,
        "outcomes": {
            "return_5d_pct": r5, "return_10d_pct": r10,
            "max_drawdown_5d_pct": mae, "max_favorable_5d_pct": mfe,
            "rel_spy_5d_pct": rel5,
        },
    }


def test_immature_events_excluded_from_metrics():
    events = [_ev(r5=2.0), _ev(r5=None), _ev(r5=None)]  # 1 mature, 2 immature
    m = st.cohort_metrics(events)
    assert m["n_events"] == 3
    assert m["n_mature"] == 1
    assert m["n_immature"] == 2
    assert m["n_resolved_5d"] == 1
    # expectancy uses only the mature event
    assert m["expectancy_5d_raw"] == 2.0


def test_short_event_missing_rel_spy_does_not_crash():
    # Regression: a MATURE short event with a missing rel_spy_5d_pct must not hit
    # `-None` inside cohort_metrics. The rel value is excluded; return still flips.
    m = st.cohort_metrics([_ev(side="short", r5=-5.0, rel5=None)])
    assert m["n_resolved_5d"] == 1
    assert m["mean_rel_spy_5d"] is None          # only mature rel was None → excluded
    assert m["expectancy_5d_raw"] == 5.0         # short flip of -5.0


def test_partial_missing_rel_spy_uses_only_present_values():
    # One long with rel5=2.0, one short with rel5 missing → mean uses only the long.
    m = st.cohort_metrics([_ev(side="long", r5=1.0, rel5=2.0),
                           _ev(side="short", r5=-1.0, rel5=None)])
    assert m["n_mature"] == 2
    assert m["mean_rel_spy_5d"] == 2.0


def test_all_missing_rel_spy_grades_without_crashing():
    # A populated cohort whose rel_spy is entirely missing must grade to an explicit
    # verdict (no edge provable), never raise.
    cohort = st.cohort_metrics([_ev(side="short", r5=-2.0, r10=-2.0, rel5=None)
                                for _ in range(40)])
    rnd = st.cohort_metrics([_ev(r5=0.1) for _ in range(40)])
    assert cohort["mean_rel_spy_5d"] is None
    v = st.grade_family(st.F_FAILED_SHORT, "short", cohort, rnd)
    # beats_spy cannot pass when rel-SPY is unavailable → no promotion.
    assert v["verdict"] not in (st.V_PAPER, st.V_DEEPER)
    assert "beats_spy" in v["blockers"]


def test_empty_cohort_metrics_are_none_not_crash():
    m = st.cohort_metrics([])
    assert m["n_events"] == 0 and m["n_mature"] == 0 and m["n_resolved_5d"] == 0
    assert m["expectancy_5d_net"] is None and m["mean_rel_spy_5d"] is None


def test_verdict_needs_minimum_sample():
    small = st.cohort_metrics([_ev(r5=5.0)])          # 1 mature << MIN
    rnd = st.cohort_metrics([_ev(r5=0.0)])
    v = st.grade_family(st.F_LEADER_RESET, "long", small, rnd)
    assert v["verdict"] == st.V_NEED_MORE


def test_adequate_sample_positive_edge_does_not_need_more_data():
    big = st.cohort_metrics([_ev(r5=5.0, r10=6.0, rel5=4.0) for _ in range(40)])
    rnd = st.cohort_metrics([_ev(r5=0.1) for _ in range(40)])
    v = st.grade_family(st.F_LEADER_RESET, "long", big, rnd)
    assert v["verdict"] != st.V_NEED_MORE
    # single-regime sample never jumps straight to paper
    assert v["verdict"] in (st.V_DEEPER, st.V_WATCHLIST)


# ── random control: deterministic + no lookahead ─────────────────────
def _rows(n=50):
    return [{"snapshot_id": f"s{i:03d}", "anchor_date": "2026-05-10",
             "layers": {"market": {"regime": "Bull Continuation"}, "sector": {}},
             "outcomes": {"return_5d_pct": float(i)}} for i in range(n)]


def test_random_control_is_deterministic():
    rows = _rows()
    a = st.random_control(rows, seed=123, n=10)
    b = st.random_control(rows, seed=123, n=10)
    assert [e["snapshot_id"] for e in a] == [e["snapshot_id"] for e in b]


def test_random_control_has_no_lookahead():
    rows = _rows()
    base = {e["snapshot_id"] for e in st.random_control(rows, seed=7, n=10)}
    # mutate every outcome — selection must not change (keys only on snapshot_id)
    for r in rows:
        r["outcomes"]["return_5d_pct"] = -999.0
    after = {e["snapshot_id"] for e in st.random_control(rows, seed=7, n=10)}
    assert base == after


# ── options expression (Task 7) ──────────────────────────────────────
def test_options_rejected_on_wide_spread():
    out = st.evaluate_options_expression(
        setup_valid=True, extended=False, options_view="Bullish confirming",
        spread_pct=2.5)  # > WIDE_SPREAD_PCT
    assert out["label"] == st.OPT_NO_EDGE
    assert any("spread too wide" in r for r in out["reject_codes"])


def test_options_requires_valid_setup():
    out = st.evaluate_options_expression(
        setup_valid=False, extended=False, options_view="Bullish confirming")
    assert out["label"] == st.OPT_NO_EDGE
    assert "no valid stock setup" in out["reject_codes"]


def test_csp_rejected_without_assignment_or_buying_power():
    # Quality tier A bullish setup, but assignment/buying power not acceptable:
    # must NOT choose CSP_WHEEL.
    no_assign = st.evaluate_options_expression(
        setup_valid=True, extended=False, options_view="Bullish positioning",
        quality_tier="A", assignment_acceptable=False, buying_power_ok=True)
    assert no_assign["label"] != st.OPT_CSP_WHEEL

    no_bp = st.evaluate_options_expression(
        setup_valid=True, extended=False, options_view="Bullish positioning",
        quality_tier="A", assignment_acceptable=True, buying_power_ok=False)
    assert no_bp["label"] != st.OPT_CSP_WHEEL

    # all conditions met -> CSP allowed
    ok = st.evaluate_options_expression(
        setup_valid=True, extended=False, options_view="Bullish positioning",
        quality_tier="A", assignment_acceptable=True, buying_power_ok=True)
    assert ok["label"] == st.OPT_CSP_WHEEL


def test_options_pl_unavailable_without_chain():
    out = st.evaluate_options_expression(
        setup_valid=True, extended=False, options_view="Bullish confirming")
    assert out["max_loss"] == "unavailable"
    assert out["risk_reward"] == "unavailable"


def test_options_pl_computed_with_strikes():
    out = st.evaluate_options_expression(
        setup_valid=True, extended=False, options_view="Bullish confirming",
        strikes={"long": 100.0, "short": 105.0}, premium=2.0)
    assert out["label"] == st.OPT_CALL_DEBIT
    assert out["max_loss"] == 2.0
    assert out["max_profit"] == 3.0
    assert out["breakeven"] == 102.0


# ── short families stay research-only ────────────────────────────────
def test_short_families_research_only():
    s = st.build_tournament()
    for fam in (st.F_FAILED_SHORT, st.F_RISKOFF_SHORT):
        g = s["graded"][fam]
        assert g["research_only"] is True
        # never promoted past research this phase
        assert g["verdict"] not in (st.V_PAPER, st.V_DEEPER)


def test_short_never_promotes_even_with_strong_in_sample_edge():
    strong = st.cohort_metrics([_ev(side="short", r5=-5.0, r10=-6.0, rel5=-4.0,
                                     mae=-2.0, mfe=3.0) for _ in range(40)])
    rnd = st.cohort_metrics([_ev(r5=0.1) for _ in range(40)])
    v = st.grade_family(st.F_FAILED_SHORT, "short", strong, rnd)
    assert v["research_only"] is True
    assert v["verdict"] not in (st.V_PAPER, st.V_DEEPER)


# ── cash baseline ────────────────────────────────────────────────────
def test_cash_baseline_is_flat():
    m = st.cash_baseline_metrics()
    assert m["expectancy_5d_net"] == 0.0
    assert m["mean_mae_5d"] == 0.0


# ── MCP surfacing (Task 11) ──────────────────────────────────────────
def test_mcp_orchestrator_renders_tournament_block():
    from research import mcp_audit_orchestrator as o
    payload = {
        "generated_at": "2026-05-26T00:00:00+00:00",
        "session": "regular", "state": "NORMAL", "state_inputs": {},
        "counts": {}, "anomalies": [], "ticker_drilldowns": [], "top_concerns": [],
        "executive_summary": "ok", "recommended_action": "", "action_keyword": "",
        "no_trade_disclaimer": o.NO_TRADE_DISCLAIMER,
        "strategy_tournament": {
            "best_candidate": None, "verdict": "NEED_MORE_DATA",
            "short_side": "OFF", "options_expression": "RESEARCH_ONLY",
            "no_trade_recommendation": "No strategy ready. Stay research-only.",
        },
    }
    md = o._render_markdown(payload)
    assert "Phase 1G.4" in md
    assert "STRATEGY TOURNAMENT: best=None verdict=NEED_MORE_DATA" in md
    assert "short_side=OFF" in md
    low = md.lower()
    for bad in ("buy now", "sell now", "short now", "trade approved"):
        assert bad not in low


def test_mcp_reader_is_none_safe(tmp_path, monkeypatch):
    from research import mcp_audit_orchestrator as o
    # Point the sidecar reader at an empty dir -> reader returns None, no raise.
    monkeypatch.setattr(o, "_repo_root", lambda: tmp_path)
    assert o._read_strategy_tournament() is None

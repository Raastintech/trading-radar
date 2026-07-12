"""Tests for the V1 filter audit + Emerging Outlier Watch lane.

Covers the twenty required behaviors: integrity failures stay hard,
single ordinary weaknesses do not create HIGH deterioration risk,
multi-signal does, momentum/social alone can't enter the outlier lane, a
credible pre-profit name can, high deterioration is excluded everywhere,
the outlier lane is separate from the shortlist, one-rule-away identifies
the exact gate, the four missingness states are distinct, sector-
inappropriate metrics aren't scored as weakness, V1 weights/gates are
frozen, everything is research-only, zero is valid, the LLM can't promote a
reject, the outlier lane is a distinct forward hypothesis, and stress tests
never mutate production config.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from research import high_conviction_alpha as hca
from research import emerging_outlier_watch as eow
from research import high_conviction_filter_audit as audit


# ── builders (clean, profitable defaults; override per test) ─────────────────


def _item(ticker="AAA", **kw):
    base = {
        "ticker": ticker, "rs_20d_vs_spy": 5.0, "rs_63d_vs_spy": 12.0,
        "rs_252d_vs_spy": 8.0, "above_ma50": True, "above_ma200": True,
        "extension_state": "NORMAL", "earliness_label": "DEVELOPING",
        "avg_dollar_volume": 50_000_000.0, "liquidity_ok": True,
        "same_session": True, "data_confidence": "HIGH", "ticker_valid": True,
        "survivability_ok": True, "bar_as_of_date": "2026-07-10",
        "sector": "Technology", "industry": "Software",
        "company_sector_etf": "XLK", "company_in_leading_sector": True,
        "dd_12m_pct": -10.0, "market_cap_bucket": "MID",
        "market_cap": 5e9, "research_score": 80.0,
    }
    base.update(kw)
    return base


def _fund(**kw):
    base = {
        "fallback": None, "quarters_available": 4, "ttm_revenue": 1000.0,
        "ttm_net_income": 200.0, "ttm_fcf": 150.0, "operating_margin_pct":
        25.0, "gross_margin_pct": 60.0, "net_margin_pct": 20.0,
        "net_debt": -100.0, "cash_and_st_investments": 500.0,
        "dilution_3q_pct": 1.0, "rev_growth_3q_pct": 20.0,
        "rev_growth_qoq_pct": 8.0, "pe_ttm": 22.0, "p_fcf_ttm": 18.0,
        "ps_ttm": 4.0, "quality_label": "PROFITABLE_CASHGEN",
    }
    base.update(kw)
    return base


def _routed(ticker="AAA", program="SWING", **kw):
    base = {"ticker": ticker, "program": program, "scan_status": "REPEAT",
            "priority": "WATCHLIST_RESEARCH", "same_session": True,
            "research_score": 80.0}
    base.update(kw)
    return base


def _eval(item=None, fund=None, routed=None):
    return hca.evaluate_candidate(routed or _routed(), item or _item(),
                                  fund or _fund())


# ── 1. data-integrity failures remain hard exclusions ───────────────────────


@pytest.mark.parametrize("item_kw,routed_kw,reason", [
    ({"same_session": False}, {"same_session": False}, "session mismatch"),
    ({}, {"priority": "DATA_QUARANTINE"}, "quarantine"),
    ({"ticker_valid": False}, {}, "invalid"),
    ({"liquidity_ok": False}, {}, "liquidity"),
    ({"data_confidence": "LOW"}, {}, "confidence"),
])
def test_integrity_failures_stay_hard(item_kw, routed_kw, reason):
    c = _eval(item=_item(**item_kw), routed=_routed(**routed_kw))
    assert c["classification"] == "REJECTED"
    assert any(reason in e for e in c["exclusions"])


# ── 2-4. single ordinary weaknesses do NOT create HIGH deterioration ────────


def test_ordinary_unprofitability_alone_not_high():
    fund = _fund(ttm_net_income=-10.0, ttm_fcf=5.0, operating_margin_pct=-5.0,
                 quality_label="UNPROFITABLE_FUNDED")
    risk, _ = eow.business_deterioration_risk(_item(), fund)
    assert risk != "HIGH"


def test_below_ma200_alone_not_high():
    item = _item(above_ma200=False, dd_12m_pct=-20.0, rs_252d_vs_spy=5.0)
    risk, _ = eow.business_deterioration_risk(item, _fund())
    assert risk != "HIGH"


def test_moderate_dilution_alone_not_high():
    for dil in (10.0, 15.0):
        risk, _ = eow.business_deterioration_risk(
            _item(), _fund(dilution_3q_pct=dil))
        assert risk != "HIGH", dil


# ── 5. multiple independent signals CAN create HIGH ─────────────────────────


def test_multiple_signals_create_high():
    fund = _fund(rev_growth_3q_pct=-20.0, dilution_3q_pct=18.0,
                 ttm_fcf=-50.0, operating_margin_pct=-15.0,
                 ttm_net_income=-30.0, quality_label="UNPROFITABLE_STRESSED")
    item = _item(rs_252d_vs_spy=-30.0)
    risk, signals = eow.business_deterioration_risk(item, fund)
    assert risk == "HIGH"
    assert len(signals) >= 2


# ── 6-7. momentum / social alone cannot enter the outlier lane ──────────────


def test_momentum_alone_cannot_enter_outlier():
    # blazing RS, unprofitable, but NO fundamental/balance evidence:
    # flat revenue, weak margins, heavy dilution, negligible runway
    item = _item(rs_63d_vs_spy=400, company_in_leading_sector=True)
    fund = _fund(ttm_net_income=-50, ttm_fcf=-40, rev_growth_3q_pct=0.0,
                 rev_growth_qoq_pct=0.0, gross_margin_pct=20.0,
                 dilution_3q_pct=12.0, net_margin_pct=-30.0,
                 cash_and_st_investments=20.0)  # runway ~2q, not "long"
    c = _eval(item=item, fund=fund)
    rec = eow.classify_emerging(c, item, fund)
    assert rec is None


def test_social_alone_cannot_enter_outlier():
    item = _item(social_score=99, rs_63d_vs_spy=-5,
                 company_in_leading_sector=False)
    fund = _fund(ttm_net_income=-50, ttm_fcf=-40, rev_growth_3q_pct=1.0,
                 rev_growth_qoq_pct=1.0, gross_margin_pct=10.0,
                 dilution_3q_pct=12.0, net_margin_pct=-40.0)
    c = _eval(item=item, fund=fund)
    assert eow.classify_emerging(c, item, fund) is None


# ── 8. credible pre-profit company MAY enter ────────────────────────────────


def test_pre_profit_credible_enters_outlier():
    # unprofitable but accelerating growth + strong unit economics +
    # long runway + controlled dilution → multiple substantive dimensions
    # negative operating margin keeps it out of the shortlist (below the
    # PROMISING score) while the growth/margin/runway evidence is credible
    item = _item(rs_63d_vs_spy=-5, company_in_leading_sector=True)
    fund = _fund(ttm_net_income=-20.0, ttm_fcf=-10.0, net_margin_pct=-20.0,
                 operating_margin_pct=-15.0, rev_growth_3q_pct=40.0,
                 rev_growth_qoq_pct=20.0, gross_margin_pct=70.0,
                 dilution_3q_pct=2.0, cash_and_st_investments=400.0,
                 pe_ttm=None, p_fcf_ttm=None,
                 quality_label="UNPROFITABLE_FUNDED")
    c = _eval(item=item, fund=fund)
    assert c["classification"] not in hca.SHORTLIST_CLASSES  # not shortlist
    rec = eow.classify_emerging(c, item, fund)
    assert rec is not None
    assert rec["lane"] == "EMERGING_OUTLIER_WATCH"
    assert rec["n_substantive_dimensions"] >= 2
    assert rec["business_deterioration_risk"] != "HIGH"
    assert rec["research_only"] is True


# ── 9. HIGH deterioration cannot enter either output ────────────────────────


def test_high_deterioration_excluded_everywhere():
    fund = _fund(rev_growth_3q_pct=-20.0, dilution_3q_pct=18.0, ttm_fcf=-50.0,
                 operating_margin_pct=-15.0, ttm_net_income=-30.0,
                 gross_margin_pct=30.0, quality_label="UNPROFITABLE_STRESSED")
    item = _item(rs_252d_vs_spy=-30.0)
    c = _eval(item=item, fund=fund)
    # not in shortlist
    assert c["classification"] not in hca.SHORTLIST_CLASSES
    # not in outlier lane
    assert eow.classify_emerging(c, item, fund) is None


# ── 10. outlier names separate from the shortlist ───────────────────────────


def test_outlier_separate_from_shortlist(tmp_path):
    root = _scan_root(tmp_path, tickers=[
        ("GOODCO", {}, {}, {}),
        ("EMRG", dict(_EMERGING_ITEM), dict(_EMERGING_FUND), {})])
    hc = hca.build_shortlist(root)
    eo = eow.build_watch(root)
    shortlist_tickers = {c["ticker"] for c in hc["shortlist"]}
    outlier_tickers = {w["ticker"] for w in eo["watch"]}
    assert "EMRG" in outlier_tickers          # emerging name surfaced
    assert "EMRG" not in shortlist_tickers     # but not in the shortlist
    assert shortlist_tickers.isdisjoint(outlier_tickers)


# ── 11. one-rule-away identifies the exact gate ─────────────────────────────


def test_one_rule_away_identifies_gate(tmp_path):
    # one clean-but-quarantined name (single integrity gate)
    root = _scan_root(tmp_path, tickers=[
        ("QUAR", {}, {}, {"priority": "DATA_QUARANTINE"}),
        ("GOODCO", {}, {}, {}),
    ])
    a = audit.build_audit(root)
    oa = a["one_rule_away"]
    quar = [c for c in oa["candidates"] if c["ticker"] == "QUAR"]
    assert quar and quar[0]["gate"] == "data_quarantine_or_invalid"
    assert a["gate_decomposition_matches_v1"] is True


# ── 12. missingness states are distinct ─────────────────────────────────────


def test_missingness_states_distinct():
    # PRESENT
    assert audit.factor_state("gross_margin_pct", _item(), _fund()) \
        == "PRESENT"
    # PROVIDER_MISSING (no fundamentals)
    assert audit.factor_state("gross_margin_pct", _item(),
                              {"fallback": "NO_DATA",
                               "quarters_available": 0}) == "PROVIDER_MISSING"
    # NOT_APPLICABLE (bank gross margin)
    assert audit.factor_state(
        "gross_margin_pct", _item(sector="Financials",
                                  industry="Banks - Diversified"),
        _fund()) == "NOT_APPLICABLE"
    # GENUINELY_UNAVAILABLE (no earnings → no P/E)
    assert audit.factor_state("pe_ttm", _item(),
                              _fund(pe_ttm=None)) == "GENUINELY_UNAVAILABLE"
    # DATA_QUALITY_FAIL
    assert audit.factor_state(
        "operating_margin_pct", _item(data_confidence="SUSPECT"),
        _fund(operating_margin_pct=None)) == "DATA_QUALITY_FAIL"


# ── 13. sector-inappropriate metrics not scored as weakness ─────────────────


def test_sector_inappropriate_metrics_flagged():
    bank = _item(sector="Financials", industry="Banks - Diversified")
    na = audit.sector_not_applicable_factors(bank, _fund())
    assert "net_debt" in na and "free_cash_flow" in na
    # biotech pre-revenue
    bio = _item(sector="Healthcare", industry="Biotechnology")
    na_bio = audit.sector_not_applicable_factors(bio, _fund(ttm_revenue=0.0))
    assert "gross_margin" in na_bio and "profitability" in na_bio


# ── 14. V1 weights and gates unchanged ──────────────────────────────────────


def test_v1_weights_and_thresholds_frozen():
    assert hca.WEIGHTS == {
        "quality": 0.25, "growth": 0.20, "fundamental_momentum": 0.15,
        "price_momentum": 0.15, "value": 0.10, "catalyst": 0.05,
        "sector_regime": 0.05, "risk_integrity": 0.05}
    assert hca.MIN_COVERAGE_FOR_FULL_SCORE == 0.60
    assert hca.MAX_SINGLE_COMPONENT_SHARE == 0.55
    assert hca.EXTREME_DILUTION_3Q_PCT == 25.0
    assert hca.SCORE_HIGH_CONVICTION == 78.0
    assert hca.VERSION == "HIGH_CONVICTION_ALPHA_V1"


# ── 15. no scanner / routing logic imported by the new modules ──────────────


def test_new_modules_import_no_engine_logic():
    import ast
    for mod in (eow, audit):
        tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        forbidden = ("research_scanner", "research_scoring", "alpaca",
                     "tradier", "execution", "broker", "core.config",
                     "order", "strategies", "council")
        for m in imported:
            assert not any(f in m for f in forbidden), (mod.__name__, m)


# ── 16. all outputs research-only ───────────────────────────────────────────


def test_outputs_research_only(tmp_path):
    root = _scan_root(tmp_path)
    eo = eow.build_watch(root)
    assert eo["research_only"] is True
    for w in eo["watch"]:
        assert w["research_only"] is True
    a = audit.build_audit(root)
    assert a["research_only"] is True


# ── 17. zero emerging outliers is valid ─────────────────────────────────────


def test_zero_emerging_outliers_valid(tmp_path):
    # only a profitable mature name → no outliers
    root = _scan_root(tmp_path, tickers=[("PROFCO", {}, {}, {})])
    eo = eow.build_watch(root)
    assert eo["watch_status"] == "NO_EMERGING_OUTLIERS"
    assert eo["watch"] == []
    assert "NO_EMERGING_OUTLIERS" in eow.render_terminal(eo)


# ── 18. LLM cannot promote a hard-rejected candidate ────────────────────────


def test_llm_cannot_promote_rejected():
    from research.journal_audit_reviewer import (
        build_emerging_outlier_flaws, audit_daily_digest)
    digest = (
        "## 4b2. Emerging Outlier Watch\n"
        "  - ZZZ (SWING): currently unprofitable — why watched: momentum "
        "| business-deterioration HIGH\n"
        "## 5. Fundamental Overlay\n")
    flaws = build_emerging_outlier_flaws(digest)
    assert any("HIGH business-deterioration" in f["issue"] for f in flaws)
    a = audit_daily_digest(digest, use_llm=False)
    assert a["promote_to_signal"] is False
    assert any(f["area"] == "high_conviction_selection"
               for f in a["flaws_detected"])


# ── 19. forward tracking is a distinct hypothesis ───────────────────────────


def test_emerging_forward_is_distinct_hypothesis(tmp_path):
    root = _scan_root(tmp_path, tickers=[
        ("EMRG", dict(_EMERGING_ITEM), dict(_EMERGING_FUND), {})])
    eo = eow.build_watch(root)
    assert eo["watch"]                    # fixture yields an emerging name
    n = eow.historize_watch(eo, root=root)
    assert n >= 1
    ledger = root / eow.HISTORY_REL
    assert ledger.exists()
    assert ledger.name == "emerging_outlier_history.jsonl"
    # distinct from the high-conviction and program ledgers
    assert ledger.name != "high_conviction_history.jsonl"
    assert ledger.name != "research_watchlist_history.jsonl"
    assert eow.historize_watch(eo, root=root) == 0  # idempotent
    fwd = eow.build_forward(root)
    assert fwd["separate_hypothesis"] is True
    assert fwd["verdict"] == eow.V_NEED_MORE_DATA


# ── 20. stress tests never mutate production config ─────────────────────────


def test_stress_tests_do_not_mutate_config(tmp_path):
    root = _scan_root(tmp_path)
    before = (hca.MIN_COVERAGE_FOR_FULL_SCORE, hca.MAX_SINGLE_COMPONENT_SHARE,
              hca.EXTREME_DILUTION_3Q_PCT, dict(hca.WEIGHTS))
    a = audit.build_audit(root)
    assert "shadow_stress_tests" in a
    after = (hca.MIN_COVERAGE_FOR_FULL_SCORE, hca.MAX_SINGLE_COMPONENT_SHARE,
             hca.EXTREME_DILUTION_3Q_PCT, dict(hca.WEIGHTS))
    assert before == after


def test_audit_recommendations_never_auto_remove(tmp_path):
    root = _scan_root(tmp_path)
    a = audit.build_audit(root)
    for g in a["gate_review_matrix"]:
        assert g["recommended_status"] != "REMOVE"
        assert g["recommended_status"] in (
            "KEEP_HARD", "KEEP_SOFT", "INVESTIGATE", "MOVE_TO_OUTLIER_LANE",
            "REMOVE_ONLY_IN_FUTURE_VERSION")


# ── shared fixture ──────────────────────────────────────────────────────────


def _scan_root(tmp_path, tickers=None):
    research = tmp_path / "cache" / "research"
    research.mkdir(parents=True, exist_ok=True)
    fund_dir = tmp_path / "cache" / "fundamentals"
    fund_dir.mkdir(parents=True, exist_ok=True)
    if tickers is None:
        tickers = [("GOODCO", {}, {}, {})]

    watchlist, routed_cands = [], []
    for tk, item_kw, fund_kw, routed_kw in tickers:
        it = _item(tk, **item_kw)
        watchlist.append(it)
        routed_cands.append(_routed(tk, program="SWING",
                                    research_score=it["research_score"],
                                    **routed_kw))
        _write_fund_file(fund_dir / f"{tk}.json", _fund(**fund_kw))

    (research / "research_scanner_latest.json").write_text(
        json.dumps({"generated_at": "2026-07-11T05:00:00+00:00",
                    "watchlist": watchlist}), encoding="utf-8")
    (research / "latest_scan_programs_latest.json").write_text(
        json.dumps({"kind": "latest_scan_programs", "present": True,
                    "market_as_of_date": "2026-07-10",
                    "market_session_match": True, "scan_readiness": "READY",
                    "integrity_warnings": [],
                    "programs": {"TACTICAL": {"candidates": []},
                                 "SWING": {"candidates": routed_cands},
                                 "LONG_TERM": {"candidates": []}}}),
        encoding="utf-8")
    return tmp_path


def _write_fund_file(path, f):
    """Reverse a fundamentals overlay into raw quarters.  Revenue is
    staggered (geometric ramp, newest highest) so the adapter recomputes the
    intended 3-quarter growth; margins are held constant per quarter."""
    q = f.get("quarters_available", 4)
    g = (f.get("rev_growth_3q_pct") or 0.0) / 100.0
    ttm_rev = f.get("ttm_revenue") or 1000.0
    # newest = R, oldest = R/(1+g); index 0 newest … q-1 oldest
    ratios = [(1.0 + g) ** ((q - 1 - i) / max(q - 1, 1)) for i in range(q)]
    scale = ttm_rev / sum(ratios) if sum(ratios) else 0.0
    revenues = [r * scale for r in ratios]
    ni = (f.get("ttm_net_income") or 0) / max(q, 1)
    fcf = (f.get("ttm_fcf") or 0) / max(q, 1)
    gm, om = f.get("gross_margin_pct"), f.get("operating_margin_pct")
    dil = f.get("dilution_3q_pct") or 0.0
    s_now, s_old = 100.0 * (1 + dil / 100.0), 100.0
    income = [{"date": f"2026-{12 - i:02d}-01",  # newest first
               "revenue": revenues[i],
               "grossProfit": revenues[i] * (gm / 100.0) if gm is not None
               else revenues[i] * 0.5,
               "operatingIncome": revenues[i] * (om / 100.0)
               if om is not None else ni,
               "netIncome": ni,
               "weightedAverageShsOutDil": s_now if i == 0 else s_old}
              for i in range(q)]
    balance = [{"date": "2026-12-01", "cashAndShortTermInvestments":
                f.get("cash_and_st_investments"), "totalDebt":
                f.get("total_debt"), "netDebt": f.get("net_debt")}]
    cashflow = [{"date": f"2026-{12 - i:02d}-01", "freeCashFlow": fcf,
                 "operatingCashFlow": fcf} for i in range(q)]
    path.write_text(json.dumps({"income": income, "balance": balance,
                                "cashflow": cashflow}), encoding="utf-8")


# a credible pre-profit emerging profile for file-based fixtures
_EMERGING_FUND = {"ttm_net_income": -20.0, "ttm_fcf": -10.0,
                  "net_margin_pct": -20.0, "operating_margin_pct": -15.0,
                  "rev_growth_3q_pct": 40.0, "gross_margin_pct": 70.0,
                  "dilution_3q_pct": 2.0, "cash_and_st_investments": 400.0,
                  "quality_label": "UNPROFITABLE_FUNDED"}
_EMERGING_ITEM = {"rs_63d_vs_spy": -5.0}

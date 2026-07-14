"""Tests for the High-Conviction Alpha Shortlist qualification layer.

Covers the sixteen required behaviors: momentum alone can't qualify, a
quality/growth/value/RS name can, dilution/negative-gross-margin/quarantine/
dead-horse exclusions, extended→QUALITY_BUT_EXTENDED, explicit missing
valuation, no neutral/perfect scores for missing fundamentals, one component
can't force 100, program routing untouched, scanner thresholds untouched,
zero-candidate output permitted, research-only invariant, LLM can't promote
a rejected name, and forward tracking as a separate hypothesis.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from research import high_conviction_alpha as hca


# ── builders ────────────────────────────────────────────────────────────────


def _item(ticker="AAA", **kw):
    """A raw scanner watchlist item with sensible clean defaults."""
    base = {
        "ticker": ticker, "rs_20d_vs_spy": 5.0, "rs_63d_vs_spy": 12.0,
        "rs_252d_vs_spy": 8.0, "above_ma50": True, "above_ma200": True,
        "extension_state": "NORMAL", "earliness_label": "DEVELOPING",
        "avg_dollar_volume": 50_000_000.0, "liquidity_ok": True,
        "same_session": True, "data_confidence": "HIGH",
        "ticker_valid": True, "survivability_ok": True,
        "bar_as_of_date": "2026-07-10", "sector": "Technology",
        "industry": "Software", "company_sector_etf": "XLK",
        "company_in_leading_sector": True, "dd_12m_pct": -10.0,
        "research_score": 80.0,
    }
    base.update(kw)
    return base


def _fund(**kw):
    """A cached fundamentals overlay with clean profitable defaults."""
    base = {
        "fallback": None, "quarters_available": 4,
        "ttm_revenue": 1000.0, "ttm_net_income": 200.0, "ttm_fcf": 150.0,
        "operating_margin_pct": 25.0, "gross_margin_pct": 60.0,
        "net_margin_pct": 20.0, "net_debt": -100.0,
        "cash_and_st_investments": 500.0, "dilution_3q_pct": 1.0,
        "rev_growth_3q_pct": 20.0, "rev_growth_qoq_pct": 8.0,
        "pe_ttm": 22.0, "p_fcf_ttm": 18.0, "ps_ttm": 4.0,
        "quality_label": "PROFITABLE_CASHGEN",
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


# ── 1. momentum alone cannot qualify ────────────────────────────────────────


def test_momentum_alone_cannot_qualify():
    # Blazing RS, but NO fundamentals at all (overlay missing).
    item = _item(rs_20d_vs_spy=90, rs_63d_vs_spy=400,
                 extension_state="PARABOLIC")
    fund = {"fallback": "NO_DATA", "quarters_available": 0}
    c = _eval(item=item, fund=fund)
    assert c["classification"] not in hca.SHORTLIST_CLASSES
    # low coverage cap prevents a high score from RS alone
    assert c["high_conviction_score"] <= hca.LOW_COVERAGE_SCORE_CAP


def test_pure_rs_score_100_name_is_not_high_conviction():
    # mirrors the real score-100 unprofitable RS names
    item = _item(rs_63d_vs_spy=490, extension_state="PARABOLIC",
                 research_score=100.0)
    fund = _fund(ttm_net_income=-50.0, ttm_fcf=-40.0,
                 operating_margin_pct=-20.0, gross_margin_pct=30.0,
                 quality_label="UNPROFITABLE_FUNDED", pe_ttm=None,
                 p_fcf_ttm=None)
    c = _eval(item=item, fund=fund, routed=_routed(program="SWING"))
    assert c["classification"] != hca.CLS_HIGH_CONVICTION


# ── 2. quality+growth+value+RS name CAN qualify ─────────────────────────────


def test_quality_growth_value_momentum_name_qualifies():
    c = _eval()
    assert c["classification"] in hca.SHORTLIST_CLASSES
    assert c["high_conviction_score"] >= hca.SCORE_PROMISING
    assert c["dead_horse_risk"] == "LOW"


def test_strong_name_can_reach_high_conviction():
    item = _item(rs_63d_vs_spy=20, rs_20d_vs_spy=8, earliness_label="EARLY")
    fund = _fund(operating_margin_pct=40, gross_margin_pct=80,
                 rev_growth_3q_pct=30, pe_ttm=18, p_fcf_ttm=15)
    c = _eval(item=item, fund=fund)
    assert c["classification"] == hca.CLS_HIGH_CONVICTION


# ── 3. severe dilution excludes / heavily penalizes ─────────────────────────


def test_severe_dilution_excludes():
    c = _eval(fund=_fund(dilution_3q_pct=40.0))
    assert c["classification"] == "REJECTED"
    assert any("dilution" in e for e in c["exclusions"])


# ── 4. negative gross margin prevents high conviction ───────────────────────


def test_negative_gross_margin_prevents_high_conviction():
    c = _eval(fund=_fund(gross_margin_pct=-5.0))
    assert c["classification"] == "REJECTED"
    assert "negative gross margin" in c["exclusions"]


# ── 5. data-quarantined names cannot qualify ────────────────────────────────


def test_data_quarantine_cannot_qualify():
    c = _eval(routed=_routed(priority="DATA_QUARANTINE"))
    assert c["classification"] == "REJECTED"
    assert any("quarantine" in e for e in c["exclusions"])


def test_session_mismatch_cannot_qualify():
    c = _eval(item=_item(same_session=False),
              routed=_routed(same_session=False))
    assert c["classification"] == "REJECTED"
    assert "session mismatch" in c["exclusions"]


# ── 6. high dead-horse risk cannot qualify ──────────────────────────────────


def test_high_dead_horse_risk_cannot_qualify():
    # declining revenue + negative FCF + dilution + long-term underperf
    item = _item(rs_252d_vs_spy=-30, above_ma200=False, dd_12m_pct=-70)
    fund = _fund(rev_growth_3q_pct=-20, ttm_fcf=-50, dilution_3q_pct=18,
                 operating_margin_pct=-15, ttm_net_income=-30,
                 gross_margin_pct=20, quality_label="UNPROFITABLE_STRESSED")
    risk, _ = hca.dead_horse_risk(item, fund)
    assert risk == "HIGH"
    c = _eval(item=item, fund=fund)
    assert c["classification"] == "REJECTED"
    assert c["dead_horse_risk"] == "HIGH"


def test_dead_horse_low_for_clean_name():
    risk, _ = hca.dead_horse_risk(_item(), _fund())
    assert risk == "LOW"


# ── 7. extended names route to QUALITY_BUT_EXTENDED ─────────────────────────


def test_extended_quality_name_routes_to_quality_but_extended():
    item = _item(extension_state="EXTENDED")
    fund = _fund(operating_margin_pct=40, gross_margin_pct=80,
                 rev_growth_3q_pct=30)
    c = _eval(item=item, fund=fund)
    assert c["classification"] == hca.CLS_QUALITY_BUT_EXTENDED


# ── 8. missing valuation reported explicitly ────────────────────────────────


def test_missing_valuation_reported_explicitly():
    c = _eval(fund=_fund(pe_ttm=None, p_fcf_ttm=None, ps_ttm=None))
    assert c["valuation_status"] == hca.VALUATION_UNAVAILABLE
    assert c["component_scores"]["value_score"] is None


# ── 9. missing fundamentals do NOT get neutral/perfect scores ───────────────


def test_missing_fundamentals_are_none_not_zero_or_full():
    fund = {"fallback": "NO_DATA", "quarters_available": 0}
    c = _eval(fund=fund)
    cs = c["component_scores"]
    assert cs["quality_score"] is None
    assert cs["growth_score"] is None
    # value with no multiples is None (not 0, not 100)
    assert cs["value_score"] is None
    # nothing silently imputed to 0 or 100
    for k in ("quality_score", "growth_score", "value_score"):
        assert cs[k] not in (0, 0.0, 100, 100.0)


# ── 10. one component cannot force a total score of 100 ──────────────────────


def test_single_component_cannot_force_100():
    # only price momentum measurable, pinned as high as possible
    item = _item(rs_20d_vs_spy=99, rs_63d_vs_spy=99, earliness_label="EARLY",
                 reclaiming_ma50=True)
    fund = {"fallback": "NO_DATA", "quarters_available": 0}
    c = _eval(item=item, fund=fund)
    assert c["high_conviction_score"] < 100
    assert c["high_conviction_score"] <= hca.LOW_COVERAGE_SCORE_CAP
    assert c["single_component_capped"] is True


def test_composite_excludes_missing_never_imputes():
    score, coverage, capped = hca.composite_score(
        {"quality": 90.0, "growth": None, "price_momentum": 80.0,
         "value": None, "fundamental_momentum": None, "catalyst": None,
         "sector_regime": None, "risk_integrity": None})
    # only quality+momentum measurable → coverage 0.40, capped
    assert coverage == pytest.approx(0.40, abs=0.001)
    assert capped is True
    assert score <= hca.LOW_COVERAGE_SCORE_CAP


# ── 11. program routing unchanged ───────────────────────────────────────────


def test_program_routing_preserved_from_input():
    for prog in ("TACTICAL", "SWING", "LONG_TERM"):
        c = _eval(routed=_routed(program=prog))
        assert c["program"] == prog
        assert c["holding_period"] == hca.HOLDING_PERIODS[prog]


def test_module_imports_no_scanner_or_scoring_logic():
    source = Path(hca.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    forbidden = ("research_scanner", "research_scoring", "alpaca", "tradier",
                 "execution", "broker", "core.config", "order", "strategies",
                 "council", "requests", "anthropic")
    for mod in imported:
        assert not any(f in mod for f in forbidden), mod


# ── 12. selection thresholds in existing scanners unchanged ─────────────────


def test_build_is_read_only(tmp_path):
    root = _scan_root(tmp_path)
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    hca.build_shortlist(root)
    after = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert before == after


# ── 13. zero candidates is permitted ────────────────────────────────────────


def test_zero_candidates_permitted(tmp_path):
    # a universe of only unprofitable / diluting names → empty shortlist
    root = _scan_root(tmp_path, tickers=[
        ("BADX", {"rs_63d_vs_spy": 400, "extension_state": "PARABOLIC"},
         {"ttm_net_income": -50, "ttm_fcf": -40, "gross_margin_pct": -5,
          "quarters_available": 4, "quality_label": "UNPROFITABLE_STRESSED"}),
    ])
    payload = hca.build_shortlist(root)
    assert payload["shortlist_status"] == "NO_HIGH_CONVICTION_CANDIDATES"
    assert payload["shortlist"] == []
    assert "NO_HIGH_CONVICTION_CANDIDATES" in hca.render_terminal(payload)


# ── 14. every record is research-only ───────────────────────────────────────


def test_every_record_research_only(tmp_path):
    root = _scan_root(tmp_path)
    payload = hca.build_shortlist(root)
    assert payload["research_only"] is True
    assert payload["guardrails"]["no_trade_recommendation"] is True
    for key in ("shortlist", "quality_but_extended", "improving_but_unproven",
                "rejected"):
        for c in payload.get(key) or []:
            assert c["research_only"] is True


# ── 15. LLM cannot promote a rejected name ──────────────────────────────────


def test_llm_cannot_promote_rejected_name():
    # the deterministic HC flaws are enforced on the LLM path too, and
    # promote_to_signal is always false; a rejected name never becomes a
    # shortlist member via the audit.
    from research.journal_audit_reviewer import (
        build_high_conviction_flaws, audit_daily_digest)
    bad_digest = (
        "## 4b1. High-Conviction Alpha Shortlist\n"
        "  1. ZZZ [HIGH_CONVICTION] SWING score 88.0 (quality None, "
        "growth None, momentum 95.0, value VALUATION_UNAVAILABLE) — RS "
        "| Business Deterioration Risk: High\n"
        "## 5. Fundamental Overlay\n")
    flaws = build_high_conviction_flaws(bad_digest)
    assert any(f["area"] == "high_conviction_selection"
               and "deterioration" in f["issue"].lower() for f in flaws)
    # no user-facing 'dead-horse' wording survives in the flaw text
    assert all("dead-horse" not in f["issue"].lower() for f in flaws)
    audit = audit_daily_digest(bad_digest, use_llm=False)
    assert audit["promote_to_signal"] is False
    assert any(f["area"] == "high_conviction_selection"
               for f in audit["flaws_detected"])


# ── 16. forward tracking is a separate hypothesis ───────────────────────────


def test_forward_tracking_is_separate_hypothesis(tmp_path):
    from research import high_conviction_forward as hcf
    root = _scan_root(tmp_path)
    payload = hca.build_shortlist(root)
    n = hcf.historize_shortlist(payload, root=root)
    # its ledger is a DISTINCT file, never the program-candidate ledger
    hist = root / hcf.HISTORY_REL
    assert hist.exists()
    assert hist.name == "high_conviction_history.jsonl"
    assert hist != root / "data" / "research" / \
        "research_watchlist_history.jsonl"
    # idempotent: re-historizing the same day adds nothing
    assert hcf.historize_shortlist(payload, root=root) == 0
    fwd = hcf.build_forward(root=root)
    assert fwd["separate_hypothesis"] is True
    assert fwd["version"] == hca.VERSION
    # immature → honest NEED_MORE_DATA, never a fabricated edge
    assert fwd["verdict"] == hcf.V_NEED_MORE_DATA


# ── shared fixtures ─────────────────────────────────────────────────────────


def _scan_root(tmp_path, tickers=None):
    """Build a minimal repo root with the routed sidecar + scanner artifact
    + cached fundamentals so build_shortlist runs end-to-end."""
    research = tmp_path / "cache" / "research"
    research.mkdir(parents=True)
    fund_dir = tmp_path / "cache" / "fundamentals"
    fund_dir.mkdir(parents=True)

    if tickers is None:
        tickers = [("GOODCO", {}, {})]

    watchlist = []
    routed_cands = []
    for tk, item_kw, fund_kw in tickers:
        it = _item(tk, **item_kw)
        watchlist.append(it)
        routed_cands.append(_routed(tk, program="SWING",
                                    research_score=it["research_score"]))
        # write a cached fundamentals file the adapter's build_fundamentals
        # can read (income/balance/cashflow quarters)
        f = _fund(**fund_kw)
        _write_fund_file(fund_dir / f"{tk}.json", f)

    (research / "research_scanner_latest.json").write_text(
        json.dumps({"generated_at": "2026-07-11T05:00:00+00:00",
                    "watchlist": watchlist}), encoding="utf-8")
    (research / "latest_scan_programs_latest.json").write_text(
        json.dumps({"kind": "latest_scan_programs", "present": True,
                    "market_as_of_date": "2026-07-10",
                    "market_session_match": True, "scan_readiness": "READY",
                    "integrity_warnings": [],
                    "programs": {
                        "TACTICAL": {"candidates": []},
                        "SWING": {"candidates": routed_cands},
                        "LONG_TERM": {"candidates": []}}}),
        encoding="utf-8")
    return tmp_path


def _write_fund_file(path, f):
    """Reverse build_fundamentals into a raw income/balance/cashflow file so
    the adapter recomputes the same overlay we intend."""
    q = f.get("quarters_available", 4)
    rev = (f.get("ttm_revenue") or 0) / max(q, 1)
    ni = (f.get("ttm_net_income") or 0) / max(q, 1)
    fcf = (f.get("ttm_fcf") or 0) / max(q, 1)
    gm = f.get("gross_margin_pct")
    om = f.get("operating_margin_pct")
    gross = rev * (gm / 100.0) if gm is not None else rev * 0.5
    opinc = rev * (om / 100.0) if om is not None else ni
    income = [{"date": f"2026-0{i+1}-01", "revenue": rev,
               "grossProfit": gross, "operatingIncome": opinc,
               "netIncome": ni, "weightedAverageShsOutDil": 100.0}
              for i in range(q)]
    balance = [{"date": "2026-04-01",
                "cashAndShortTermInvestments":
                    f.get("cash_and_st_investments"),
                "totalDebt": f.get("total_debt"),
                "netDebt": f.get("net_debt")}]
    cashflow = [{"date": f"2026-0{i+1}-01", "freeCashFlow": fcf,
                 "operatingCashFlow": fcf}
                for i in range(q)]
    path.write_text(json.dumps({"income": income, "balance": balance,
                                "cashflow": cashflow}), encoding="utf-8")


# ── ratio-sanity guard (display honesty; V1 scoring frozen) ─────────────────


def test_om_above_revenue_is_flagged_not_praised():
    """OM > 100% (non-operating gains booked in operating income) must
    surface as a caveat, never as a 'strong operating margin' strength."""
    pts, reasons, risks = hca.score_quality(_fund(operating_margin_pct=1445.0))
    assert not any("strong operating margin" in r for r in reasons)
    assert any("exceeds revenue" in r and "unreliable" in r for r in risks)
    # V1 scoring frozen: same points as the strong-OM branch
    ref_pts, _, _ = hca.score_quality(_fund(operating_margin_pct=45.0))
    assert pts == ref_pts


def test_gm_100_is_flagged_not_praised():
    pts, reasons, risks = hca.score_quality(_fund(gross_margin_pct=100.0))
    assert not any("healthy gross margin" in r for r in reasons)
    assert any("no cost of revenue" in r for r in risks)
    ref_pts, _, _ = hca.score_quality(_fund(gross_margin_pct=60.0))
    assert pts == ref_pts


def test_normal_margins_still_praised():
    _, reasons, risks = hca.score_quality(_fund(operating_margin_pct=45.0,
                                                gross_margin_pct=82.0))
    assert any("strong operating margin 45%" in r for r in reasons)
    assert any("healthy gross margin 82%" in r for r in reasons)
    assert not risks

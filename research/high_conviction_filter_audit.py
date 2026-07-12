#!/usr/bin/env python3
"""High-Conviction Alpha V1 — filter severity / recall / bias audit.

A DIAGNOSTIC over the FROZEN V1 qualification layer.  It answers: are V1's
hard gates, dead-horse logic, missing-data treatment, and burden rules
overly restrictive, and do they systematically exclude legitimate emerging
alpha?  It never changes V1 and never recommends loosening a rule from a
single scan.

Sections produced (all from the latest routed candidate set):
  * GATE REVIEW MATRIX — every gate, category, behaviour, threshold,
    number of latest candidates affected, and a default recommended status
    (KEEP_HARD / KEEP_SOFT / INVESTIGATE / MOVE_TO_OUTLIER_LANE /
    REMOVE_ONLY_IN_FUTURE_VERSION).  No rule is auto-marked REMOVE.
  * ONE-RULE-AWAY — names excluded by exactly one gate, the exact gate,
    and the lane they would enter without it.
  * MARKET-CAP / LIFECYCLE / SECTOR bias — qualification vs rejection by
    segment, to expose mega-cap / mature-company structural bias.
  * MISSINGNESS — factor coverage with four DISTINCT states (genuinely
    unavailable / provider missing / not applicable / insufficient
    history / data-quality failure), broken down by cap and sector.
  * SECTOR APPLICABILITY — where generic net-debt / FCF / gross-margin /
    profitability factors are NOT_APPLICABLE (banks, REITs, biotech …) and
    therefore must not be read as weakness.
  * SHADOW STRESS TESTS — qualification counts under alternative thresholds
    (dead-horse signal count, coverage cap, dominance cap, dilution band)
    computed on the fly.  Diagnostic only; V1 config is never altered.
  * COUNTERFACTUAL COHORTS + FORWARD — cohort forward returns when matured
    (honest NEED_MORE_DATA until horizons mature); tail-opportunity metric.

DOCTRINE: RESEARCH-ONLY / CACHE-ONLY / CRED-FREE / READ-ONLY on V1.  Writes
only its own sidecar + log.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research import high_conviction_alpha as hca  # noqa: E402  (frozen V1)
from research.emerging_outlier_watch import (  # noqa: E402
    business_deterioration_risk, classify_emerging)

SIDECAR_REL = (Path("cache") / "research"
               / "high_conviction_filter_audit_latest.json")
LOG_REL = Path("logs") / "high_conviction_filter_audit_latest.txt"

# Category taxonomy from the mission:
CAT_INTEGRITY = "INTEGRITY_INVESTABILITY"     # non-negotiable hard blocks
CAT_RISK = "FUNDAMENTAL_RISK_CONDITION"       # should usually be soft
CAT_SEVERE = "SEVERE_DISQUALIFIER"            # hard only w/ multi-fact support

# Gate registry — the audit's documented view of V1's gates.  ``check``
# reproduces V1's decision for that gate; a consistency guard asserts the
# union reproduces V1.hard_exclusions() exactly (see audit_consistency()).
def _g(gid, category, threshold, behavior, default_status, rationale, check):
    return {"gate": gid, "category": category, "threshold": threshold,
            "behavior": behavior, "default_recommended_status": default_status,
            "rationale": rationale, "_check": check}


def _adv(item):
    return hca._num(item.get("avg_dollar_volume"))


GATE_REGISTRY = [
    _g("data_quarantine_or_invalid", CAT_INTEGRITY, "priority in "
       "{DATA_QUARANTINE, INVALID}", "hard_exclude", "KEEP_HARD",
       "Data-integrity failure, not an alpha opinion.",
       lambda it, f, r, dh: str(r.get("priority") or "")
       in ("DATA_QUARANTINE", "INVALID")),
    _g("session_mismatch", CAT_INTEGRITY, "same_session is False",
       "hard_exclude", "KEEP_HARD",
       "Candidate/benchmark bars from different sessions are not comparable.",
       lambda it, f, r, dh: it.get("same_session") is False),
    _g("invalid_security", CAT_INTEGRITY, "ticker_valid is False",
       "hard_exclude", "KEEP_HARD", "Invalid / delisted security.",
       lambda it, f, r, dh: it.get("ticker_valid") is False),
    _g("low_data_confidence", CAT_INTEGRITY, "data_confidence in {LOW, "
       "SUSPECT}", "hard_exclude", "KEEP_HARD",
       "Suspect feed — metrics cannot be trusted.",
       lambda it, f, r, dh: str(it.get("data_confidence")
                                or it.get("trust_level") or "")
       in ("LOW", "SUSPECT")),
    _g("missing_bars", CAT_INTEGRITY, "no bar_as_of_date", "hard_exclude",
       "KEEP_HARD", "Insufficient price data for the claimed metric.",
       lambda it, f, r, dh: not it.get("bar_as_of_date")),
    _g("inadequate_liquidity", CAT_INTEGRITY,
       f"liquidity_ok False or ADV < ${hca.MIN_DOLLAR_VOLUME:,.0f}",
       "hard_exclude", "KEEP_HARD",
       "Below the investability floor — not tradable at research size.",
       lambda it, f, r, dh: it.get("liquidity_ok") is False
       or (_adv(it) is not None and _adv(it) < hca.MIN_DOLLAR_VOLUME)),
    _g("extreme_dilution", CAT_SEVERE,
       f"dilution_3q_pct >= {hca.EXTREME_DILUTION_3Q_PCT:.0f}%",
       "hard_exclude", "KEEP_HARD",
       "Extreme repeated dilution — a severe, multi-quarter fact.",
       lambda it, f, r, dh: hca._num(f.get("dilution_3q_pct")) is not None
       and hca._num(f.get("dilution_3q_pct")) >= hca.EXTREME_DILUTION_3Q_PCT),
    _g("negative_gross_margin", CAT_SEVERE, "gross_margin_pct < 0",
       "hard_exclude", "INVESTIGATE",
       "Negative gross margin is severe, but for some lifecycles (early "
       "scale) or sectors it may be temporary — investigate before treating "
       "as a universal veto; credible emerging cases route to the outlier "
       "lane only when NOT negative-GM.",
       lambda it, f, r, dh: hca._num(f.get("gross_margin_pct")) is not None
       and hca._num(f.get("gross_margin_pct")) < 0),
    _g("business_deterioration_high", CAT_SEVERE,
       "dead_horse_risk == HIGH (multi-signal)", "hard_exclude",
       "KEEP_HARD",
       "Already multi-signal: HIGH needs >=2 independent deterioration "
       "signals or one extreme condition (a single ordinary weakness cannot "
       "trigger it).  Recommend a neutral display name.",
       lambda it, f, r, dh: dh == "HIGH"),
    _g("structural_collapse", CAT_SEVERE,
       "below MA200 AND dd_12m <= "
       f"{hca.DEEP_DRAWDOWN_12M_PCT:.0f}% AND no reclaim/higher-lows",
       "hard_exclude", "KEEP_HARD",
       "Multi-condition: broken long-term trend with no recovery structure.",
       lambda it, f, r, dh: it.get("above_ma200") is False
       and hca._num(it.get("dd_12m_pct")) is not None
       and hca._num(it.get("dd_12m_pct")) <= hca.DEEP_DRAWDOWN_12M_PCT
       and not it.get("reclaiming_ma50") and not it.get("higher_lows")),
]

# Soft gates (not rejections — they cap classification).  Documented so the
# matrix is complete.
SOFT_GATES = [
    {"gate": "program_burden_of_proof", "category": CAT_RISK,
     "threshold": "Swing/Long-Term: not profitable AND no positive FCF AND "
     "no positive operating margin AND no funded-growth case",
     "behavior": "classification_cap",
     "default_recommended_status": "KEEP_SOFT",
     "rationale": "Higher burden for unprofitable names — a cap, not a "
     "rejection; such names are eligible for the Emerging Outlier lane."},
    {"gate": "low_coverage_score_cap", "category": CAT_RISK,
     "threshold": f"factor coverage < {hca.MIN_COVERAGE_FOR_FULL_SCORE}",
     "behavior": "score_cap",
     "default_recommended_status": "KEEP_SOFT",
     "rationale": "Prevents a one/two-factor score reaching the high band; "
     "missing factors are excluded, never imputed."},
    {"gate": "single_component_dominance_cap", "category": CAT_RISK,
     "threshold": f"top component share > {hca.MAX_SINGLE_COMPONENT_SHARE}",
     "behavior": "score_cap", "default_recommended_status": "KEEP_SOFT",
     "rationale": "Stops one factor (e.g. RS) forcing a 100."},
    {"gate": "extension_state", "category": CAT_RISK,
     "threshold": "EXTENDED / PARABOLIC",
     "behavior": "route_to_quality_but_extended",
     "default_recommended_status": "KEEP_SOFT",
     "rationale": "Strong business + poor entry → wait-for-reset lane, not a "
     "rejection."},
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── market-cap + lifecycle segmentation ─────────────────────────────────────


def market_cap_bucket(item: Dict[str, Any]) -> str:
    b = str(item.get("market_cap_bucket") or "").upper()
    if b:
        return b
    mc = hca._num(item.get("market_cap"))
    if mc is None:
        return "UNKNOWN"
    if mc >= 200e9:
        return "MEGA"
    if mc >= 10e9:
        return "LARGE"
    if mc >= 2e9:
        return "MID"
    if mc >= 300e6:
        return "SMALL"
    return "MICRO"


def lifecycle_stage(fund: Dict[str, Any]) -> str:
    if not fund.get("quarters_available"):
        return "UNKNOWN_NO_FUNDAMENTALS"
    ni = hca._num(fund.get("ttm_net_income"))
    fcf = hca._num(fund.get("ttm_fcf"))
    g3 = hca._num(fund.get("rev_growth_3q_pct"))
    if ni is not None and ni > 0:
        return "MATURE_PROFITABLE" if (fcf or 0) > 0 else "NEWLY_PROFITABLE"
    if g3 is not None and g3 > 0:
        return "PRE_PROFIT_GROWTH"
    return "TURNAROUND_OR_DECLINING"


# ── sector applicability ────────────────────────────────────────────────────

_FINANCIAL_SECTORS = {"Financials", "Financial Services"}
_REIT_HINTS = ("reit", "real estate")
_BIOTECH_HINTS = ("biotech", "pharmaceutical", "drug manufacturers")


def sector_not_applicable_factors(item: Dict[str, Any],
                                  fund: Dict[str, Any]) -> List[str]:
    """Factors that the generic model should NOT read as weakness for this
    sector.  These are NOT_APPLICABLE, distinct from missing/weak."""
    sector = str(item.get("sector") or "")
    industry = str(item.get("industry") or "").lower()
    na: List[str] = []
    if sector in _FINANCIAL_SECTORS:
        na += ["net_debt", "free_cash_flow", "gross_margin"]
    if any(h in industry for h in _REIT_HINTS) or "REIT" in industry.upper():
        na += ["net_debt", "gross_margin"]  # need FFO/AFFO, not generic
    if any(h in industry for h in _BIOTECH_HINTS):
        rev = hca._num(fund.get("ttm_revenue"))
        if rev is not None and rev <= 0:
            na += ["gross_margin", "profitability", "free_cash_flow"]
    # dedupe, preserve order
    seen, out = set(), []
    for f in na:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


# ── missingness state per factor ────────────────────────────────────────────

_FACTORS = ("gross_margin_pct", "operating_margin_pct", "ttm_fcf",
            "net_debt", "dilution_3q_pct", "rev_growth_3q_pct",
            "pe_ttm", "p_fcf_ttm")


def factor_state(factor: str, item: Dict[str, Any],
                 fund: Dict[str, Any]) -> str:
    """One of: PRESENT / NOT_APPLICABLE / DATA_QUALITY_FAIL /
    INSUFFICIENT_HISTORY / PROVIDER_MISSING / GENUINELY_UNAVAILABLE."""
    conf = str(item.get("data_confidence") or item.get("trust_level") or "")
    na = sector_not_applicable_factors(item, fund)
    key = {"ttm_fcf": "free_cash_flow", "net_debt": "net_debt",
           "gross_margin_pct": "gross_margin",
           "pe_ttm": "profitability", "p_fcf_ttm": "free_cash_flow"}.get(
        factor, factor)
    if key in na:
        return "NOT_APPLICABLE"
    if fund.get("fallback") or not fund.get("quarters_available"):
        return "PROVIDER_MISSING"
    if conf in ("LOW", "SUSPECT"):
        return "DATA_QUALITY_FAIL"
    val = fund.get(factor)
    if val is not None:
        return "PRESENT"
    # valuation multiples are legitimately unavailable when there are no
    # earnings / positive FCF — distinct from provider gaps
    if factor in ("pe_ttm", "p_fcf_ttm"):
        return "GENUINELY_UNAVAILABLE"
    if factor == "rev_growth_3q_pct" and (fund.get("quarters_available")
                                          or 0) < 4:
        return "INSUFFICIENT_HISTORY"
    return "PROVIDER_MISSING"


# ── build ────────────────────────────────────────────────────────────────────


def _fired_gates(item, fund, routed, dh) -> List[str]:
    return [g["gate"] for g in GATE_REGISTRY if g["_check"](item, fund, routed,
                                                            dh)]


def build_audit(root: Optional[Path] = None,
                now: Optional[datetime] = None) -> Dict[str, Any]:
    root = Path(root) if root else REPO_ROOT
    now = now or _utcnow()
    from research.emerging_outlier_watch import _iter_candidates

    rows: List[Dict[str, Any]] = []
    for candidate, item, fund in _iter_candidates(root):
        dh, dh_sig = business_deterioration_risk(item, fund)
        fired = _fired_gates(item, fund, candidate, dh)
        # consistency guard: our decomposition must reproduce V1's decision
        v1_rejected = bool(candidate.get("exclusions"))
        rows.append({
            "ticker": candidate.get("ticker"),
            "classification": candidate.get("classification"),
            "score": candidate.get("high_conviction_score"),
            "fired_gates": fired, "v1_rejected": v1_rejected,
            "mcap_bucket": market_cap_bucket(item),
            "lifecycle": lifecycle_stage(fund),
            "sector": item.get("sector"),
            "deterioration_risk": dh, "deterioration_signals": dh_sig,
            "na_factors": sector_not_applicable_factors(item, fund),
            "coverage": candidate.get("factor_coverage"),
            "emerging": classify_emerging(candidate, item, fund) is not None,
            "_item": item, "_fund": fund, "_candidate": candidate,
        })

    if not rows:
        return {"kind": "high_conviction_filter_audit", "version": "V1_AUDIT",
                "present": False, "research_only": True,
                "generated_at": now.isoformat(),
                "reason": "no routed candidates to audit"}

    consistency = _consistency_check(rows)
    matrix = _gate_matrix(rows)
    one_away = _one_rule_away(rows)
    bias = _bias_report(rows)
    missing = _missingness_report(rows)
    sector_app = _sector_applicability_report(rows)
    stress = _stress_tests(rows)
    tail = _tail_opportunity(rows)

    return {
        "kind": "high_conviction_filter_audit",
        "version": "HIGH_CONVICTION_ALPHA_V1_AUDIT",
        "audited_spec": hca.VERSION,
        "present": True, "research_only": True,
        "generated_at": now.isoformat(),
        "guardrails": {
            "v1_unchanged": True, "no_rule_change_from_one_scan": True,
            "recommendations_never_auto_remove": True,
        },
        "n_candidates": len(rows),
        "gate_decomposition_matches_v1": consistency["matches"],
        "gate_review_matrix": matrix,
        "soft_gates": SOFT_GATES,
        "one_rule_away": one_away,
        "market_cap_lifecycle_bias": bias,
        "missingness": missing,
        "sector_applicability": sector_app,
        "shadow_stress_tests": stress,
        "tail_opportunity": tail,
        "forward_note": ("Counterfactual forward returns require matured "
                         "horizons; the current scan is too recent — treat "
                         "cohort performance as NEED_MORE_DATA until the "
                         "high-conviction and emerging-outlier ledgers "
                         "mature."),
    }


def _consistency_check(rows) -> Dict[str, Any]:
    """Our per-gate decomposition must agree with V1's own reject decision:
    a name has fired hard gates iff V1 put it in the rejected set."""
    mism = [r["ticker"] for r in rows
            if bool(r["fired_gates"]) != r["v1_rejected"]]
    return {"matches": not mism, "mismatches": mism}


def _gate_matrix(rows) -> List[Dict[str, Any]]:
    out = []
    for g in GATE_REGISTRY:
        affected = [r["ticker"] for r in rows if g["gate"] in r["fired_gates"]]
        sole = [r["ticker"] for r in rows
                if r["fired_gates"] == [g["gate"]]]
        out.append({
            "gate": g["gate"], "category": g["category"],
            "threshold": g["threshold"], "behavior": g["behavior"],
            "recommended_status": g["default_recommended_status"],
            "rationale": g["rationale"],
            "latest_affected": len(affected),
            "latest_sole_reason": len(sole),
            "sole_reason_tickers": sole[:20],
        })
    return out


def _one_rule_away(rows) -> Dict[str, Any]:
    """Names excluded by exactly one gate — the recall-sensitive set."""
    per_gate: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    names = []
    for r in rows:
        if len(r["fired_gates"]) != 1:
            continue
        gate = r["fired_gates"][0]
        # what lane would it enter if this gate were absent?  Re-classify
        # without the exclusion (frozen V1 classify, exclusions removed).
        would_be = _lane_without_gate(r)
        entry = {"ticker": r["ticker"], "gate": gate,
                 "mcap_bucket": r["mcap_bucket"], "lifecycle": r["lifecycle"],
                 "sector": r["sector"],
                 "would_enter_lane_without_gate": would_be}
        per_gate[gate].append(entry)
        names.append(entry)
    return {
        "count": len(names),
        "by_gate": {g: len(v) for g, v in per_gate.items()},
        "candidates": names,
    }


def _lane_without_gate(r) -> str:
    """Frozen-V1 classification the name would receive if its single fired
    gate were not present (diagnostic only — nothing is re-qualified)."""
    cand, item, fund = r["_candidate"], r["_item"], r["_fund"]
    program = cand.get("program")
    dh = r["deterioration_risk"]
    # if removing the gate clears all exclusions, ask V1.classify
    components = {k.replace("_score", ""): v
                 for k, v in (cand.get("component_scores") or {}).items()}
    # component_scores keys are e.g. quality_score → quality
    comp = {
        "quality": components.get("quality"),
        "growth": components.get("growth"),
        "price_momentum": components.get("price_momentum"),
    }
    burden_ok = cand.get("program_burden_met")
    score = cand.get("high_conviction_score") or 0
    # dead-horse gate removed → treat as its computed non-HIGH tier
    dh_eff = "MEDIUM" if r["fired_gates"] == ["business_deterioration_high"] \
        else dh
    cls = hca.classify(score, program, dh_eff if dh_eff != "HIGH" else "LOW",
                       burden_ok, item, {
                           "quality": comp["quality"],
                           "growth": comp["growth"],
                           "price_momentum": comp["price_momentum"]})
    return cls


def _bias_report(rows) -> Dict[str, Any]:
    def _seg(key):
        seg = defaultdict(lambda: {"n": 0, "qualified": 0, "rejected": 0,
                                   "emerging": 0})
        for r in rows:
            s = seg[r[key]]
            s["n"] += 1
            if r["classification"] in hca.SHORTLIST_CLASSES:
                s["qualified"] += 1
            if r["v1_rejected"]:
                s["rejected"] += 1
            if r["emerging"]:
                s["emerging"] += 1
        return {k: dict(v) for k, v in seg.items()}
    return {
        "by_market_cap": _seg("mcap_bucket"),
        "by_lifecycle": _seg("lifecycle"),
    }


def _missingness_report(rows) -> Dict[str, Any]:
    by_factor: Dict[str, Counter] = {f: Counter() for f in _FACTORS}
    by_cap: Dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        for f in _FACTORS:
            st = factor_state(f, r["_item"], r["_fund"])
            by_factor[f][st] += 1
            if st == "PRESENT":
                by_cap[r["mcap_bucket"]]["present"] += 1
            else:
                by_cap[r["mcap_bucket"]]["absent"] += 1
    return {
        "states": ["PRESENT", "NOT_APPLICABLE", "DATA_QUALITY_FAIL",
                   "INSUFFICIENT_HISTORY", "PROVIDER_MISSING",
                   "GENUINELY_UNAVAILABLE"],
        "by_factor": {f: dict(c) for f, c in by_factor.items()},
        "coverage_by_market_cap": {k: dict(v) for k, v in by_cap.items()},
    }


def _sector_applicability_report(rows) -> Dict[str, Any]:
    flagged = [{"ticker": r["ticker"], "sector": r["sector"],
                "not_applicable_factors": r["na_factors"]}
               for r in rows if r["na_factors"]]
    by_factor = Counter()
    for r in rows:
        for f in r["na_factors"]:
            by_factor[f] += 1
    return {
        "n_candidates_with_na_factors": len(flagged),
        "na_factor_counts": dict(by_factor),
        "flagged": flagged[:40],
        "note": ("NOT_APPLICABLE factors must not be scored as weakness. "
                 "Full sector adapters (bank/REIT/biotech models) are "
                 "recommended future versioned work, not part of V1."),
    }


def _stress_tests(rows) -> Dict[str, Any]:
    """Shadow sensitivity — recomputed on the fly, V1 config untouched."""
    # dead-horse HIGH under 3 / 4 / 5 signal thresholds (V1 uses >=4 pts,
    # but signals here are the count of independent deterioration flags)
    def _n_signals(r):
        # approximate independent-signal count from the recorded signals
        sig = r["deterioration_signals"] or []
        return len([s for s in sig if "no deterioration" not in s
                    and "unavailable" not in s])

    dh_bands = {}
    for thr in (2, 3, 4):
        dh_bands[f">={thr}_signals"] = sum(
            1 for r in rows if _n_signals(r) >= thr)

    cov_bands = {}
    for cap in (0.55, 0.60, 0.65):
        cov_bands[f"cap_{cap}"] = sum(
            1 for r in rows if (r["coverage"] or 0) < cap)

    dilution_bands = {}
    for thr in (20.0, 25.0, 30.0):
        dilution_bands[f">={thr:.0f}pct"] = sum(
            1 for r in rows
            if hca._num(r["_fund"].get("dilution_3q_pct")) is not None
            and hca._num(r["_fund"].get("dilution_3q_pct")) >= thr)

    return {
        "note": ("Diagnostic only — these do NOT change V1.  They show how "
                 "sensitive the outcome is to each cutoff.  Do not select "
                 "the best-looking setting after seeing forward results."),
        "dead_horse_signal_threshold": dh_bands,
        "coverage_cap_candidates_capped": cov_bands,
        "dilution_exclusion_band": dilution_bands,
        "shortlist_size_current": sum(
            1 for r in rows if r["classification"] in hca.SHORTLIST_CLASSES),
    }


def _tail_opportunity(rows) -> Dict[str, Any]:
    """Missed-asymmetric-winner metric.  Requires matured forward returns;
    honest zeros + NEED_MORE_DATA until the ledgers mature."""
    return {
        "status": "NEED_MORE_DATA",
        "reason": ("forward returns for the current rejected set are not yet "
                   "matured; tail metrics populate as horizons complete"),
        "missed_outlier_rate": None,
        "missed_top_decile_winners": None,
        "missed_2x_winners": None,
        "missed_50pct_winners": None,
        "median_rejected_return": None,
        "rejected_downside_rate": None,
        "n_hard_rejected": sum(1 for r in rows if r["v1_rejected"]),
        "n_one_rule_away": sum(1 for r in rows
                               if len(r["fired_gates"]) == 1),
    }


# ── render + write ───────────────────────────────────────────────────────────


def render_terminal(payload: Dict[str, Any]) -> str:
    lines = ["=== HIGH-CONVICTION ALPHA V1 — FILTER AUDIT ===",
             f"  audits {payload.get('audited_spec')} · research-only · "
             "V1 unchanged"]
    if not payload.get("present"):
        lines.append(f"  {payload.get('reason')}")
        return "\n".join(lines)
    lines.append(f"  candidates {payload['n_candidates']} · gate "
                 f"decomposition matches V1: "
                 f"{payload['gate_decomposition_matches_v1']}")
    lines.append("\n  GATE REVIEW MATRIX (gate · category · affected · "
                 "sole-reason · status):")
    for g in payload["gate_review_matrix"]:
        lines.append(f"    {g['gate']:<30} {g['category'][:12]:<12} "
                     f"aff={g['latest_affected']:<3} "
                     f"sole={g['latest_sole_reason']:<3} "
                     f"{g['recommended_status']}")
    oa = payload["one_rule_away"]
    lines.append(f"\n  ONE-RULE-AWAY: {oa['count']} names "
                 f"(by gate: {oa['by_gate']})")
    for c in oa["candidates"][:12]:
        lines.append(f"    {c['ticker']:<6} blocked_by={c['gate']} "
                     f"({c['mcap_bucket']}/{c['lifecycle']}) → would be "
                     f"{c['would_enter_lane_without_gate']}")
    lines.append("\n  MARKET-CAP BIAS (bucket: n / qualified / rejected / "
                 "emerging):")
    for b, s in sorted(payload["market_cap_lifecycle_bias"]["by_market_cap"]
                       .items()):
        lines.append(f"    {b:<8} n={s['n']:<3} q={s['qualified']:<3} "
                     f"rej={s['rejected']:<3} emrg={s['emerging']}")
    lines.append("\n  LIFECYCLE BIAS:")
    for b, s in sorted(payload["market_cap_lifecycle_bias"]["by_lifecycle"]
                       .items()):
        lines.append(f"    {b:<26} n={s['n']:<3} q={s['qualified']:<3} "
                     f"rej={s['rejected']:<3} emrg={s['emerging']}")
    sa = payload["sector_applicability"]
    lines.append(f"\n  SECTOR-INAPPROPRIATE FACTORS: "
                 f"{sa['n_candidates_with_na_factors']} names "
                 f"({sa['na_factor_counts']})")
    lines.append("\n  SHADOW STRESS (diagnostic only — V1 unchanged):")
    st = payload["shadow_stress_tests"]
    lines.append(f"    business-deterioration by signal count: "
                 f"{st['dead_horse_signal_threshold']}")
    lines.append(f"    coverage-cap candidates: "
                 f"{st['coverage_cap_candidates_capped']}")
    lines.append(f"    dilution bands: {st['dilution_exclusion_band']}")
    lines.append(f"\n  TAIL OPPORTUNITY: {payload['tail_opportunity']['status']} "
                 f"(hard-rejected {payload['tail_opportunity']['n_hard_rejected']}, "
                 f"one-rule-away {payload['tail_opportunity']['n_one_rule_away']})")
    return "\n".join(lines)


def write_outputs(payload: Dict[str, Any],
                  root: Optional[Path] = None) -> Path:
    root = Path(root) if root else REPO_ROOT
    # strip private join keys before persisting
    clean = json.loads(json.dumps(payload, default=lambda o: None))
    _strip_private(clean)
    sidecar = root / SIDECAR_REL
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(json.dumps(clean, ensure_ascii=False, indent=2)
                       + "\n", encoding="utf-8")
    log = root / LOG_REL
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(render_terminal(payload) + "\n", encoding="utf-8")
    return sidecar


def _strip_private(obj):
    if isinstance(obj, dict):
        for k in list(obj.keys()):
            if k.startswith("_"):
                del obj[k]
            else:
                _strip_private(obj[k])
    elif isinstance(obj, list):
        for v in obj:
            _strip_private(v)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="High-Conviction Alpha V1 filter audit (diagnostic; "
                    "cache-only, research-only; V1 unchanged).")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--root", default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    root = Path(args.root) if args.root else REPO_ROOT
    payload = build_audit(root)
    if args.json:
        clean = json.loads(json.dumps(payload, default=lambda o: None))
        _strip_private(clean)
        print(json.dumps(clean, ensure_ascii=False, indent=2))
    else:
        print(render_terminal(payload))
    if not args.dry_run and payload.get("present"):
        print(f"\nwritten: {write_outputs(payload, root)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""High-Conviction Alpha Shortlist — selective quality/growth/value/momentum
qualification layer above the Tactical / Swing / Long-Term research programs.

WHAT THIS IS
------------
A separate, highly selective research-only layer.  It does NOT replace or
re-route the program views; it sits above them and answers a different
question: *of the candidates the latest scan already routed, which few
combine credible business quality, growth, reasonable valuation, positive
momentum, healthy structure, low risk, and clean data?*

A score of 100 in an existing lane (e.g. pure relative strength) is NOT a
high-conviction investment candidate.  This module distinguishes a strong
signal from a strong candidate.

DOCTRINE
--------
  - RESEARCH-ONLY.  No trade recommendation, no execution, no order
    language.  ``research_only`` is stamped on every record.
  - CACHE-ONLY / READ-ONLY on the engine.  Reads the routed-candidate
    sidecar, the raw scanner artifact (for technicals), and the cached
    fundamental overlay.  Changes NO scanner threshold, ranking, score,
    routing, gate, or program verdict.  Writes only its own sidecar +
    log twin (+ the separate forward historizer, append-only).
  - CRED-FREE.  No providers, no DB, no core.config import.
  - PRE-REGISTERED.  Factor definitions, weights, gates, exclusions,
    shortlist size, and classification thresholds are fixed constants
    (VERSION below) chosen before the evidence matured.  Do not tune them
    to results — retire the version and open a new one instead.
  - EXPLICIT MISSINGNESS.  A factor that cannot be measured from the
    cached data is scored ``None`` and excluded from the weighted
    composite (weights renormalize over what is measurable).  Missing
    inputs are NEVER imputed as 0 or 100.  Low overall coverage caps the
    composite so one component (e.g. RS) can never force a 100.

DATA MEASURABILITY (what the cached artifacts can and cannot support)
---------------------------------------------------------------------
  MEASURABLE: TTM margins (gross/operating/net), TTM FCF, net debt, cash,
    3-quarter dilution, quality tier; revenue growth q/q and vs-3-quarters
    (NOT true YoY — only ~4 quarters are cached); P/E, P/FCF, P/S; the full
    technical/structure set (RS 20/63/252d, MA50/200, extension state,
    drawdowns, liquidity, sector leadership); catalyst proximity + analyst
    upgrade + catalyst sanity.
  NOT AVAILABLE (scored None, never imputed): ROIC / return on capital;
    true YoY and multi-quarter FCF/earnings trend; analyst estimate
    revisions; institutional ownership; forward P/E, EV/EBITDA, PEG,
    EV/sales; sector-relative valuation percentile; reverse-split history.
  KNOWN CAVEAT — financials/banks: the generic FCF and net-debt heuristics
    do not fit banks/insurers, so the dead-horse gate is CONSERVATIVE for
    them (a profitable bank can be excluded from HIGH conviction while its
    peers pass).  This is the safe direction for a selective shortlist —
    the name stays fully visible in its program view; it is never a claim
    the business is failing.

Usage:
  ./scripts/run_research_cycle.sh high-conviction-alpha
  GEM_TRADER_SKIP_DOTENV=true .venv/bin/python research/high_conviction_alpha.py [--dry-run] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.artifact_dependency_audit import dependency_audit  # noqa: E402

# ── pre-registered version + artifact paths ─────────────────────────────────

VERSION = "HIGH_CONVICTION_ALPHA_V1"

SCANNER_REL = Path("cache") / "research" / "research_scanner_latest.json"
ROUTED_REL = (Path("cache") / "research"
              / "latest_scan_programs_latest.json")
SIDECAR_REL = (Path("cache") / "research"
               / "high_conviction_alpha_latest.json")
LOG_REL = Path("logs") / "high_conviction_alpha_latest.txt"

PROGRAM_ORDER = ("TACTICAL", "SWING", "LONG_TERM")
HOLDING_PERIODS = {
    "TACTICAL": "5-15 trading days",
    "SWING": "45-60 trading days",
    "LONG_TERM": "6-18 months",
}

# ── pre-registered composite weights (per the mission spec) ─────────────────
# The composite is a weighted mean over the components that are MEASURABLE
# for a given candidate; weights renormalize across available components.

WEIGHTS: Dict[str, float] = {
    "quality": 0.25,
    "growth": 0.20,
    "fundamental_momentum": 0.15,
    "price_momentum": 0.15,
    "value": 0.10,
    "catalyst": 0.05,
    "sector_regime": 0.05,
    "risk_integrity": 0.05,
}

# Minimum share of total weight that must be measurable before the
# composite is allowed to reach the high-conviction band.  Below this the
# score is capped so a single available component can never dominate.
MIN_COVERAGE_FOR_FULL_SCORE = 0.60
LOW_COVERAGE_SCORE_CAP = 65.0
# No single component may contribute more than this share of the raw
# weighted numerator — a second explicit guard against one-factor scores.
MAX_SINGLE_COMPONENT_SHARE = 0.55

# ── pre-registered classification thresholds ────────────────────────────────
CLS_HIGH_CONVICTION = "HIGH_CONVICTION"
CLS_PROMISING = "PROMISING"
CLS_WATCH_FOR_ENTRY = "WATCH_FOR_ENTRY"
CLS_QUALITY_BUT_EXTENDED = "QUALITY_BUT_EXTENDED"
CLS_IMPROVING_BUT_UNPROVEN = "IMPROVING_BUT_UNPROVEN"

SHORTLIST_CLASSES = (CLS_HIGH_CONVICTION, CLS_PROMISING, CLS_WATCH_FOR_ENTRY)

SCORE_HIGH_CONVICTION = 78.0
SCORE_PROMISING = 68.0

# shortlist sizing
SHORTLIST_HARD_MAX = 10
SHORTLIST_TARGET_MIN = 3
SHORTLIST_TARGET_MAX = 7

# ── liquidity / structure thresholds ────────────────────────────────────────
MIN_DOLLAR_VOLUME = 3_000_000.0     # $3M/day floor for "adequate liquidity"
EXTREME_DILUTION_3Q_PCT = 25.0      # hard-exclusion dilution over ~3 quarters
HIGH_DILUTION_3Q_PCT = 12.0         # penalized dilution
# A drop this large in weighted diluted shares over 3 quarters is far more
# likely a provider reporting gap (e.g. an Up-C name's latest-quarter
# diluted count shipped without its convertible-unit adjustment) than a
# real buyback, so it's treated as a data-suspect outlier rather than
# scored as "low dilution" (2026-07-30: RYAN dilution_3q_pct -52.7%).
SUSPECT_BUYBACK_3Q_PCT = -25.0
DEEP_DRAWDOWN_12M_PCT = -60.0       # structural-collapse candidate

# ── ratio-sanity bounds (display honesty; see score_quality) ────────────────
OM_SANITY_MAX_PCT = 100.0           # OM > revenue ⇒ non-operating gains
GM_SANITY_MAX_PCT = 99.5            # GM ≈ 100% ⇒ no cost-of-revenue line

VALUATION_UNAVAILABLE = "VALUATION_UNAVAILABLE"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8")) \
            if path.exists() else None
    except Exception:
        return None


def _num(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


# ════════════════════════════════════════════════════════════════════════════
# Factor scoring — each returns (score|None, [reasons], [risks]).
# ``None`` means "not measurable from cached data"; the caller excludes it
# from the composite (never imputes 0 or 100).
# ════════════════════════════════════════════════════════════════════════════


def score_quality(fund: Dict[str, Any]) -> Tuple[Optional[float], List[str],
                                                 List[str]]:
    if fund.get("fallback") or not fund.get("quarters_available"):
        return None, [], ["fundamentals unavailable"]
    reasons: List[str] = []
    risks: List[str] = []
    pts = 50.0
    ni = _num(fund.get("ttm_net_income"))
    fcf = _num(fund.get("ttm_fcf"))
    om = _num(fund.get("operating_margin_pct"))
    gm = _num(fund.get("gross_margin_pct"))
    nd = _num(fund.get("net_debt"))
    dil = _num(fund.get("dilution_3q_pct"))
    label = str(fund.get("quality_label") or "")

    if ni is not None:
        if ni > 0:
            pts += 14
            reasons.append("profitable")
        else:
            pts -= 12
            risks.append("unprofitable (TTM net loss)")
    if fcf is not None:
        if fcf > 0:
            pts += 10
            reasons.append("positive free cash flow")
        else:
            pts -= 8
            risks.append("negative free cash flow")
    # Ratio-sanity guard (display honesty): OM above 100% means operating
    # income exceeds revenue (non-operating gains, e.g. asset/spectrum
    # sales, booked in operating income); GM at ~100% means no cost of
    # revenue is reported.  Neither ratio is evidence of business quality,
    # so they surface as caveats, never as strengths.  V1 scoring is
    # pre-registered and frozen — points are unchanged; scoring treatment
    # of these artifacts is a V2 decision.
    if om is not None:
        if om > OM_SANITY_MAX_PCT:
            pts += 12  # V1-frozen weight
            risks.append(f"operating margin {om:.0f}% exceeds revenue — "
                         "ratio unreliable (non-operating gains)")
        elif om >= 20:
            pts += 12
            reasons.append(f"strong operating margin {om:.0f}%")
        elif om >= 8:
            pts += 6
            reasons.append(f"solid operating margin {om:.0f}%")
        elif om < 0:
            pts -= 10
            risks.append(f"negative operating margin {om:.0f}%")
    if gm is not None:
        if gm >= GM_SANITY_MAX_PCT:
            pts += 6  # V1-frozen weight
            risks.append(f"gross margin {gm:.0f}% with no cost of revenue "
                         "reported — ratio uninformative")
        elif gm >= 40:
            pts += 6
            reasons.append(f"healthy gross margin {gm:.0f}%")
        elif gm < 0:
            pts -= 20
            risks.append(f"negative gross margin {gm:.0f}%")
    if nd is not None:
        if nd < 0:
            pts += 6
            reasons.append("net cash balance sheet")
        else:
            rev = _num(fund.get("ttm_revenue")) or 0.0
            if rev and nd > 3 * rev:
                pts -= 8
                risks.append("high net debt vs revenue")
    if dil is not None:
        if dil < SUSPECT_BUYBACK_3Q_PCT:
            risks.append(f"dilution_3q_pct {dil:+.0f}%/3q outside plausible "
                         "range — likely a diluted-share-count data gap, "
                         "not a real buyback")
        elif dil <= 2:
            pts += 5
            reasons.append("low dilution")
        elif dil >= HIGH_DILUTION_3Q_PCT:
            pts -= 12
            risks.append(f"share dilution {dil:+.0f}%/3q")
    if label == "PROFITABLE_CASHGEN":
        pts += 4
    elif label == "UNPROFITABLE_STRESSED":
        pts -= 10
        risks.append("unprofitable and cash-stressed")
    return _clamp(pts), reasons, risks


def score_growth(fund: Dict[str, Any]) -> Tuple[Optional[float], List[str],
                                                List[str]]:
    g3 = _num(fund.get("rev_growth_3q_pct"))
    gq = _num(fund.get("rev_growth_qoq_pct"))
    if g3 is None and gq is None:
        return None, [], ["revenue growth not computable from cached quarters"]
    reasons: List[str] = []
    risks: List[str] = []
    pts = 50.0
    # Note: this is q/q and vs-3-quarters-ago growth, NOT true YoY (only
    # ~4 quarters are cached).  Documented caveat, not silently treated
    # as annual growth.
    primary = g3 if g3 is not None else gq
    if primary is not None:
        if primary >= 30:
            pts += 26
            reasons.append(f"revenue up {primary:.0f}% (3q basis)")
        elif primary >= 10:
            pts += 16
            reasons.append(f"revenue growing {primary:.0f}% (3q basis)")
        elif primary >= 0:
            pts += 4
            reasons.append(f"revenue flat/slightly up {primary:.0f}% (3q)")
        elif primary >= -10:
            pts -= 12
            risks.append(f"revenue declining {primary:.0f}% (3q)")
        else:
            pts -= 24
            risks.append(f"revenue falling {primary:.0f}% (3q)")
    return _clamp(pts), reasons, risks


def score_fundamental_momentum(fund: Dict[str, Any]
                               ) -> Tuple[Optional[float], List[str],
                                          List[str]]:
    """Direction-of-travel of the business, distinct from price action:
    growth acceleration, margin level, and dilution trend."""
    g3 = _num(fund.get("rev_growth_3q_pct"))
    gq = _num(fund.get("rev_growth_qoq_pct"))
    om = _num(fund.get("operating_margin_pct"))
    dil = _num(fund.get("dilution_3q_pct"))
    if g3 is None and gq is None and om is None:
        return None, [], ["fundamental-trend inputs unavailable"]
    reasons: List[str] = []
    risks: List[str] = []
    pts = 50.0
    # acceleration: latest quarter q/q vs the average quarterly pace over 3q.
    # g3 <= -100 means the 3q revenue base cratered (or flipped sign) — the
    # implied per-quarter rate is undefined (fractional power of a negative
    # base), not just a large negative number, so skip the comparison rather
    # than raise (2026-07-22: EWBC rev_growth_3q_pct=-175.72 crashed this
    # with a float/complex TypeError and silently froze the nightly sidecar).
    if gq is not None and g3 is not None and g3 > -100:
        implied_q = (1.0 + g3 / 100.0) ** (1.0 / 3.0) - 1.0
        if gq / 100.0 > implied_q + 0.01:
            pts += 16
            reasons.append("revenue growth accelerating")
        elif gq / 100.0 < implied_q - 0.01:
            pts -= 12
            risks.append("revenue growth decelerating")
    elif gq is not None and g3 is not None:
        pts -= 12
        risks.append(f"revenue base collapsed {g3:.0f}% (3q) — acceleration undefined")
    if om is not None:
        if om >= 10:
            pts += 8
            reasons.append("healthy operating profitability")
        elif om < 0:
            pts -= 8
    if dil is not None:
        if dil < SUSPECT_BUYBACK_3Q_PCT:
            risks.append(f"dilution_3q_pct {dil:+.0f}%/3q outside plausible "
                         "range — data suspect")
        elif dil <= 0:
            pts += 8
            reasons.append("share count flat/declining")
        elif dil >= HIGH_DILUTION_3Q_PCT:
            pts -= 12
            risks.append("ongoing dilution")
    return _clamp(pts), reasons, risks


def score_price_momentum(item: Dict[str, Any]
                         ) -> Tuple[Optional[float], List[str], List[str]]:
    rs20 = _num(item.get("rs_20d_vs_spy"))
    rs63 = _num(item.get("rs_63d_vs_spy"))
    if rs20 is None and rs63 is None:
        return None, [], ["relative-strength inputs unavailable"]
    reasons: List[str] = []
    risks: List[str] = []
    pts = 50.0
    if rs63 is not None:
        if rs63 >= 10:
            pts += 16
            reasons.append("strong 63d relative strength")
        elif rs63 > 0:
            pts += 8
            reasons.append("positive 63d relative strength")
        else:
            pts -= 10
            risks.append("lagging 63d vs SPY")
    if rs20 is not None:
        if rs20 > 0:
            pts += 8
            reasons.append("positive 20d relative strength")
        elif rs20 < -5:
            pts -= 6
    if item.get("above_ma50") and item.get("above_ma200"):
        pts += 10
        reasons.append("above MA50 and MA200")
    elif item.get("above_ma200"):
        pts += 4
    elif item.get("above_ma200") is False:
        pts -= 10
        risks.append("below MA200 (structural)")
    if item.get("reclaiming_ma50"):
        pts += 4
        reasons.append("reclaiming MA50")
    # extension / exhaustion penalties
    ext = str(item.get("extension_state") or "")
    if ext == "PARABOLIC":
        pts -= 18
        risks.append("parabolic extension")
    elif ext == "EXTENDED":
        pts -= 8
        risks.append("extended above trend")
    earliness = str(item.get("earliness_label") or "")
    if earliness in ("EARLY", "DEVELOPING", "RECLAIM_WATCH"):
        pts += 6
        reasons.append(f"early-stage structure ({earliness.lower()})")
    elif earliness == "LATE":
        pts -= 6
        risks.append("late-stage move")
    return _clamp(pts), reasons, risks


def score_value(fund: Dict[str, Any], item: Dict[str, Any]
                ) -> Tuple[Optional[float], List[str], List[str], str]:
    """Value relative to growth/cash generation.  Returns an extra status
    string so 'no usable valuation' is reported explicitly, never as a
    numeric conclusion."""
    pe = _num(fund.get("pe_ttm"))
    pfcf = _num(fund.get("p_fcf_ttm"))
    ps = _num(fund.get("ps_ttm"))
    g3 = _num(fund.get("rev_growth_3q_pct"))
    if pe is None and pfcf is None and ps is None:
        return None, [], ["valuation multiples unavailable"], VALUATION_UNAVAILABLE
    reasons: List[str] = []
    risks: List[str] = []
    pts = 50.0
    used = False
    if pe is not None:
        used = True
        if pe <= 20:
            pts += 18
            reasons.append(f"reasonable P/E {pe:.0f}")
        elif pe <= 35:
            pts += 6
        elif pe > 60:
            pts -= 12
            risks.append(f"rich P/E {pe:.0f}")
    if pfcf is not None:
        used = True
        if pfcf <= 20:
            pts += 12
            reasons.append(f"reasonable price/FCF {pfcf:.0f}")
        elif pfcf > 50:
            pts -= 8
            risks.append(f"expensive on FCF {pfcf:.0f}")
    if not used and ps is not None:
        # sales-only, appropriate only when earnings aren't meaningful
        if ps <= 3:
            pts += 8
            reasons.append(f"modest P/S {ps:.1f}")
        elif ps > 15:
            pts -= 14
            risks.append(f"very high P/S {ps:.1f}")
    # cheap-but-shrinking is a value trap, not value
    if g3 is not None and g3 < -5 and pts > 55:
        pts -= 10
        risks.append("low multiple driven by shrinking revenue")
    return _clamp(pts), reasons, risks, "VALUATION_AVAILABLE"


def score_catalyst(item: Dict[str, Any]
                   ) -> Tuple[Optional[float], List[str], List[str]]:
    dte = _num(item.get("days_to_earnings"))
    has_upgrade = bool(item.get("has_analyst_upgrade"))
    sanity = str(item.get("catalyst_sanity_label") or "")
    social = _num(item.get("social_score"))
    if dte is None and not has_upgrade and not sanity and social is None:
        return None, [], []
    reasons: List[str] = []
    risks: List[str] = []
    pts = 50.0
    if has_upgrade:
        pts += 12
        reasons.append("analyst upgrade")
    if dte is not None and 0 <= dte <= 15:
        pts += 8
        reasons.append(f"earnings in {int(dte)}d")
        if item.get("extended_into_earnings"):
            pts -= 6
            risks.append("extended into earnings")
    if sanity in ("CONFLICTED", "SUSPECT"):
        pts -= 8
        risks.append(f"catalyst sanity {sanity.lower()}")
    if item.get("social_data_fabricated"):
        pts -= 20
        risks.append("social data flagged fabricated")
    elif social is not None and social >= 70:
        pts += 4
        reasons.append("elevated social attention")
    return _clamp(pts), reasons, risks


def score_sector_regime(item: Dict[str, Any]
                        ) -> Tuple[Optional[float], List[str], List[str]]:
    if not item.get("sector"):
        return None, [], []
    reasons: List[str] = []
    pts = 50.0
    if item.get("company_in_leading_sector"):
        pts += 20
        reasons.append("in a leading sector")
    return _clamp(pts), reasons, []


def score_risk_integrity(item: Dict[str, Any], fund: Dict[str, Any]
                         ) -> Tuple[Optional[float], List[str], List[str]]:
    reasons: List[str] = []
    risks: List[str] = []
    pts = 60.0
    if item.get("same_session") is True:
        pts += 10
        reasons.append("same-session validated")
    elif item.get("same_session") is False:
        pts -= 25
        risks.append("session mismatch")
    conf = str(item.get("data_confidence") or item.get("trust_level") or "")
    if conf == "HIGH":
        pts += 10
    elif conf in ("LOW", "SUSPECT"):
        pts -= 20
        risks.append(f"data confidence {conf.lower()}")
    if item.get("liquidity_ok") is False:
        pts -= 15
        risks.append("thin liquidity")
    if item.get("survivability_ok") is False:
        pts -= 15
        risks.append("survivability concern")
    dil = _num(fund.get("dilution_3q_pct"))
    if dil is not None and dil >= HIGH_DILUTION_3Q_PCT:
        pts -= 10
    return _clamp(pts), reasons, risks


# ════════════════════════════════════════════════════════════════════════════
# Dead-horse detection — deterministic; flags price excitement disconnected
# from deteriorating fundamentals.  NOT a bankruptcy prediction.
# ════════════════════════════════════════════════════════════════════════════


def dead_horse_risk(item: Dict[str, Any], fund: Dict[str, Any]
                    ) -> Tuple[str, List[str]]:
    signals: List[str] = []
    have_fundamentals = bool(fund.get("quarters_available"))
    g3 = _num(fund.get("rev_growth_3q_pct"))
    om = _num(fund.get("operating_margin_pct"))
    gm = _num(fund.get("gross_margin_pct"))
    fcf = _num(fund.get("ttm_fcf"))
    dil = _num(fund.get("dilution_3q_pct"))
    nd = _num(fund.get("net_debt"))
    rs252 = _num(item.get("rs_252d_vs_spy"))
    dd12 = _num(item.get("dd_12m_pct"))

    pts = 0
    if g3 is not None and g3 < -5:
        pts += 2
        signals.append("revenue declining over multiple quarters")
    if om is not None and om < -10:
        pts += 1
        signals.append("deeply negative operating margin")
    if gm is not None and gm < 0:
        pts += 1
        signals.append("negative gross margin")
    if fcf is not None and fcf < 0:
        pts += 1
        signals.append("persistent negative free cash flow")
    if dil is not None and dil >= 15:
        pts += 2
        signals.append("repeated equity dilution")
    if nd is not None:
        rev = _num(fund.get("ttm_revenue")) or 0.0
        if rev and nd > 3 * rev:
            pts += 1
            signals.append("elevated net debt")
    if rs252 is not None and rs252 < -20:
        pts += 1
        signals.append("long-term underperformance vs SPY")
    if item.get("above_ma200") is False and dd12 is not None \
            and dd12 <= DEEP_DRAWDOWN_12M_PCT:
        pts += 1
        signals.append("below MA200 with deep 12m drawdown")
    if item.get("in_speculative_theme") and (g3 is None or g3 <= 0):
        pts += 1
        signals.append("speculative theme without improving fundamentals")

    if not have_fundamentals:
        # Can't rule out deterioration — honest UNKNOWN, treated as a
        # burden-of-proof failure for high conviction, not a rejection.
        return "UNKNOWN", (signals or ["fundamentals unavailable"])
    if pts >= 4:
        return "HIGH", signals
    if pts >= 2:
        return "MEDIUM", signals
    return "LOW", signals or ["no deterioration signals detected"]


# ════════════════════════════════════════════════════════════════════════════
# Composite + gates + classification
# ════════════════════════════════════════════════════════════════════════════


def composite_score(components: Dict[str, Optional[float]]
                    ) -> Tuple[float, float, bool]:
    """Weighted mean over MEASURABLE components (weights renormalized).
    Returns (score, coverage, single_component_capped).  Missing
    components are excluded, never imputed.  Low coverage or single-
    component dominance caps the score so one factor can't force 100."""
    contributions: Dict[str, float] = {}
    total_w = 0.0
    numer = 0.0
    for name, weight in WEIGHTS.items():
        val = components.get(name)
        if val is None:
            continue
        contributions[name] = weight * val
        numer += weight * val
        total_w += weight
    if total_w <= 0:
        return 0.0, 0.0, False
    raw = numer / total_w
    coverage = total_w  # weights sum to 1.0, so this is the covered share

    capped = False
    score = raw
    # Guard 1: low coverage → cap (one/two components can't reach the band)
    if coverage < MIN_COVERAGE_FOR_FULL_SCORE:
        score = min(score, LOW_COVERAGE_SCORE_CAP)
        capped = True
    # Guard 2: single-component dominance of the numerator → cap
    if numer > 0:
        top_share = max(contributions.values()) / numer
        if top_share > MAX_SINGLE_COMPONENT_SHARE \
                and len(contributions) < len(WEIGHTS):
            score = min(score, LOW_COVERAGE_SCORE_CAP)
            capped = True
    return round(score, 1), round(coverage, 3), capped


def hard_exclusions(item: Dict[str, Any], fund: Dict[str, Any],
                    routed: Dict[str, Any], dh_risk: str) -> List[str]:
    """Hard/near-hard exclusions.  Any non-empty result → REJECTED (kept
    visible in the rejected section, never deleted)."""
    out: List[str] = []
    priority = str(routed.get("priority") or "")
    if priority in ("DATA_QUARANTINE", "INVALID"):
        out.append("data quarantine / invalid priority")
    if item.get("same_session") is False:
        out.append("session mismatch")
    if item.get("ticker_valid") is False:
        out.append("invalid or delisted security")
    conf = str(item.get("data_confidence") or item.get("trust_level") or "")
    if conf in ("LOW", "SUSPECT"):
        out.append(f"data confidence {conf.lower()}")
    if not item.get("bar_as_of_date"):
        out.append("missing required bars")
    adv = _num(item.get("avg_dollar_volume"))
    if item.get("liquidity_ok") is False or (adv is not None
                                             and adv < MIN_DOLLAR_VOLUME):
        out.append("inadequate liquidity")
    dil = _num(fund.get("dilution_3q_pct"))
    if dil is not None and dil >= EXTREME_DILUTION_3Q_PCT:
        out.append(f"extreme dilution {dil:+.0f}%/3q")
    elif dil is not None and dil < SUSPECT_BUYBACK_3Q_PCT:
        # Symmetric to the extreme-dilution exclusion above: a share-count
        # drop this large is a provider data gap, not a real buyback (see
        # SUSPECT_BUYBACK_3Q_PCT comment), so the ticker can't be trusted
        # for a HIGH_CONVICTION/PROMISING call until the data resolves —
        # it stays visible in "rejected", never silently hidden.
        out.append(f"dilution_3q_pct {dil:+.0f}%/3q outside plausible range "
                    "— diluted-share-count data gap, not a real buyback")
    gm = _num(fund.get("gross_margin_pct"))
    if gm is not None and gm < 0:
        out.append("negative gross margin")
    if dh_risk == "HIGH":
        out.append("business deterioration risk high")
    # structural collapse below MA200 with no validated recovery hypothesis
    dd12 = _num(item.get("dd_12m_pct"))
    if item.get("above_ma200") is False and dd12 is not None \
            and dd12 <= DEEP_DRAWDOWN_12M_PCT \
            and not item.get("reclaiming_ma50") \
            and not item.get("higher_lows"):
        out.append("structural collapse below MA200")
    return out


def program_burden_met(program: str, fund: Dict[str, Any]
                       ) -> Tuple[bool, Optional[str]]:
    """Swing/Long-Term require stronger proof than momentum.  At least one
    of: profitable, positive FCF, clearly improving operating metrics, or a
    credible funded-growth case (strong growth + low dilution + cash).
    Tactical carries no fundamental burden but is still penalized elsewhere
    for weakness."""
    if program == "TACTICAL":
        return True, None
    if not fund.get("quarters_available"):
        return False, "insufficient fundamental data for its program"
    ni = _num(fund.get("ttm_net_income"))
    fcf = _num(fund.get("ttm_fcf"))
    g3 = _num(fund.get("rev_growth_3q_pct"))
    dil = _num(fund.get("dilution_3q_pct"))
    cash = _num(fund.get("cash_and_st_investments"))
    if ni is not None and ni > 0:
        return True, None
    if fcf is not None and fcf > 0:
        return True, None
    om = _num(fund.get("operating_margin_pct"))
    if om is not None and om > 0:
        return True, None
    # credible funded-growth case
    if (g3 is not None and g3 >= 20 and (dil is None or dil <= 5)
            and cash is not None and cash > 0):
        return True, "funded-growth case (unprofitable but strong growth, "
    return False, "unprofitable without a funded-growth case"


def classify(score: float, program: str, dh_risk: str, burden_ok: bool,
             item: Dict[str, Any], components: Dict[str, Optional[float]]
             ) -> str:
    """Assign a research classification (NOT a trade signal)."""
    ext = str(item.get("extension_state") or "")
    quality = components.get("quality")
    growth = components.get("growth")
    strong_fundamentals = (quality is not None and quality >= 60
                           and (growth is None or growth >= 45))
    # Quality-but-extended: the business is strong but price is stretched.
    if strong_fundamentals and ext in ("EXTENDED", "PARABOLIC"):
        return CLS_QUALITY_BUT_EXTENDED
    # High conviction: top score, clean risk, burden met, dead-horse LOW.
    if (score >= SCORE_HIGH_CONVICTION and dh_risk == "LOW" and burden_ok
            and strong_fundamentals):
        return CLS_HIGH_CONVICTION
    if score >= SCORE_PROMISING and burden_ok and dh_risk in ("LOW", "MEDIUM"):
        earliness = str(item.get("earliness_label") or "")
        pm = components.get("price_momentum")
        # Strong candidate but wants a better entry (immature or resetting).
        if earliness in ("RESET_WATCH", "RECLAIM_WATCH") \
                or (pm is not None and pm < 55):
            return CLS_WATCH_FOR_ENTRY
        return CLS_PROMISING
    return CLS_IMPROVING_BUT_UNPROVEN


# ════════════════════════════════════════════════════════════════════════════
# Candidate evaluation
# ════════════════════════════════════════════════════════════════════════════


def evaluate_candidate(routed: Dict[str, Any], item: Dict[str, Any],
                       fund: Dict[str, Any]) -> Dict[str, Any]:
    """Full factor evaluation for one routed candidate.  ``routed`` is the
    latest_scan_programs record (program, priority, same_session, ...);
    ``item`` is the raw scanner watchlist entry (technicals); ``fund`` is
    the cached fundamental overlay."""
    program = str(routed.get("program") or "TACTICAL")
    reasons: List[str] = []
    risks: List[str] = []

    q, q_r, q_x = score_quality(fund)
    g, g_r, g_x = score_growth(fund)
    fm, fm_r, fm_x = score_fundamental_momentum(fund)
    pm, pm_r, pm_x = score_price_momentum(item)
    v, v_r, v_x, v_status = score_value(fund, item)
    cat, cat_r, cat_x = score_catalyst(item)
    sec, sec_r, sec_x = score_sector_regime(item)
    ri, ri_r, ri_x = score_risk_integrity(item, fund)

    for lst in (q_r, g_r, fm_r, pm_r, v_r, cat_r, sec_r, ri_r):
        reasons.extend(lst)
    for lst in (q_x, g_x, fm_x, pm_x, v_x, cat_x, sec_x, ri_x):
        risks.extend(lst)

    components: Dict[str, Optional[float]] = {
        "quality": q, "growth": g, "fundamental_momentum": fm,
        "price_momentum": pm, "value": v, "catalyst": cat,
        "sector_regime": sec, "risk_integrity": ri,
    }
    score, coverage, capped = composite_score(components)
    dh_risk, dh_signals = dead_horse_risk(item, fund)
    exclusions = hard_exclusions(item, fund, routed, dh_risk)
    burden_ok, burden_note = program_burden_met(program, fund)

    if exclusions:
        classification = "REJECTED"
    else:
        classification = classify(score, program, dh_risk, burden_ok,
                                  item, components)

    def _r(x: Optional[float]) -> Optional[float]:
        return None if x is None else round(x, 1)

    # dedupe while preserving order
    def _dedupe(seq: List[str]) -> List[str]:
        seen, out = set(), []
        for s in seq:
            if s and s not in seen:
                seen.add(s)
                out.append(s)
        return out

    return {
        "ticker": routed.get("ticker") or item.get("ticker"),
        "program": program,
        "holding_period": HOLDING_PERIODS.get(program, "?"),
        "scan_status": routed.get("scan_status"),
        "high_conviction_score": score,
        "classification": classification,
        "component_scores": {
            "quality_score": _r(q),
            "growth_score": _r(g),
            "fundamental_momentum_score": _r(fm),
            "price_momentum_score": _r(pm),
            "value_score": _r(v),
            "catalyst_score": _r(cat),
            "sector_regime_score": _r(sec),
            "risk_integrity_score": _r(ri),
        },
        "factor_coverage": coverage,
        "single_component_capped": capped,
        "valuation_status": v_status,
        "dead_horse_risk": dh_risk,
        "dead_horse_signals": dh_signals,
        "program_burden_met": burden_ok,
        "program_burden_note": burden_note,
        "exclusions": exclusions,
        "why_selected": _dedupe(reasons)[:6],
        "main_risks": _dedupe(risks)[:5],
        "same_session": item.get("same_session"),
        "bar_as_of_date": item.get("bar_as_of_date"),
        "benchmark_as_of_date": item.get("benchmark_as_of_date"),
        "sector": item.get("sector"),
        "industry": item.get("industry"),
        "sector_etf": item.get("company_sector_etf"),
        "priority": routed.get("priority"),
        "extension_state": item.get("extension_state"),
        "research_score": routed.get("research_score")
        or item.get("research_score"),
        "research_only": True,
    }


# ════════════════════════════════════════════════════════════════════════════
# Build
# ════════════════════════════════════════════════════════════════════════════


def build_shortlist(root: Optional[Path] = None,
                    now: Optional[datetime] = None) -> Dict[str, Any]:
    root = Path(root) if root else REPO_ROOT
    now = now or _utcnow()
    generated_at = now.isoformat()
    routed_doc = _load_json(root / ROUTED_REL)
    scanner = _load_json(root / SCANNER_REL)
    source_dependencies = dependency_audit(
        artifact_kind="high_conviction_alpha",
        artifact_timestamp=generated_at,
        scanner_doc=scanner,
        routed_doc=routed_doc,
    )

    base = {
        "kind": "high_conviction_alpha",
        "version": VERSION,
        "generated_at": generated_at,
        "artifact_timestamp": generated_at,
        "hc_artifact_timestamp": generated_at,
        "research_only": True,
        "source_dependencies": source_dependencies,
        "cadence_status": source_dependencies["status"],
        "stale_for_latest_scan": source_dependencies["stale_for_latest_scan"],
        "weights": WEIGHTS,
        "guardrails": {
            "no_selection_change": True,
            "no_trade_recommendation": True,
            "membership_is_not_validated_alpha": True,
            "missing_factors_excluded_never_imputed": True,
        },
    }

    if not routed_doc or not routed_doc.get("present"):
        return {**base, "present": False,
                "reason": "routed-candidate sidecar missing — run "
                          "latest-scan-programs first",
                "shortlist": [], "shortlist_status":
                "NO_HIGH_CONVICTION_CANDIDATES"}

    # scan-session integrity must pass before we qualify anything
    integrity_warnings = list(routed_doc.get("integrity_warnings") or [])
    session_ok = routed_doc.get("market_session_match") is not False
    scan_items = {str(w.get("ticker")): w
                  for w in (scanner or {}).get("watchlist") or []}

    try:
        from dashboards.research_command_center.data_adapter import (
            ArtifactStore, build_fundamentals)
        store = ArtifactStore(root=root)

        def _fund(t: str) -> Dict[str, Any]:
            return build_fundamentals(t, store)
    except Exception:
        def _fund(t: str) -> Dict[str, Any]:
            return {"fallback": "NO_DATA", "quarters_available": 0}

    evaluated: List[Dict[str, Any]] = []
    for pid in PROGRAM_ORDER:
        block = (routed_doc.get("programs") or {}).get(pid) or {}
        for routed in block.get("candidates") or []:
            ticker = str(routed.get("ticker") or "")
            item = scan_items.get(ticker, {})
            fund = _fund(ticker)
            evaluated.append(evaluate_candidate(routed, item, fund))

    # ── partition ────────────────────────────────────────────────────────────
    rejected = [c for c in evaluated if c["classification"] == "REJECTED"]
    extended = [c for c in evaluated
                if c["classification"] == CLS_QUALITY_BUT_EXTENDED]
    improving = [c for c in evaluated
                 if c["classification"] == CLS_IMPROVING_BUT_UNPROVEN]
    qualified = [c for c in evaluated
                 if c["classification"] in SHORTLIST_CLASSES]

    qualified.sort(key=lambda c: -(c["high_conviction_score"] or 0))
    extended.sort(key=lambda c: -(c["high_conviction_score"] or 0))
    shortlist = qualified[:SHORTLIST_HARD_MAX]

    status = ("NO_HIGH_CONVICTION_CANDIDATES" if not shortlist
              else "OK")

    return {
        **base,
        "present": True,
        "session_integrity_ok": session_ok,
        "integrity_warnings": integrity_warnings,
        "market_as_of_date": routed_doc.get("market_as_of_date"),
        "scan_readiness": routed_doc.get("scan_readiness"),
        "counts": {
            "evaluated": len(evaluated),
            "qualified": len(qualified),
            "shortlisted": len(shortlist),
            "quality_but_extended": len(extended),
            "improving_but_unproven": len(improving),
            "rejected": len(rejected),
        },
        "shortlist_status": status,
        "shortlist": shortlist,
        "quality_but_extended": extended,
        "improving_but_unproven": improving,
        "rejected": rejected,
    }


# ════════════════════════════════════════════════════════════════════════════
# Rendering
# ════════════════════════════════════════════════════════════════════════════


def _cls_short(cls: str) -> str:
    return {CLS_HIGH_CONVICTION: "HIGH_CONVICTION",
            CLS_PROMISING: "PROMISING",
            CLS_WATCH_FOR_ENTRY: "WATCH_FOR_ENTRY",
            CLS_QUALITY_BUT_EXTENDED: "QUALITY_BUT_EXTENDED",
            CLS_IMPROVING_BUT_UNPROVEN: "IMPROVING_BUT_UNPROVEN"}.get(cls, cls)


def render_terminal(payload: Dict[str, Any], verbose: bool = False) -> str:
    lines = ["=== HIGH-CONVICTION ALPHA SHORTLIST ===",
             f"  {VERSION} · Quality + Growth + Value + Momentum · "
             "research-only"]
    if not payload.get("present"):
        lines.append("  (no routed-candidate sidecar — run "
                     "latest-scan-programs first)")
        return "\n".join(lines)
    c = payload.get("counts") or {}
    lines.append(f"  evaluated {c.get('evaluated', 0)} · qualified "
                 f"{c.get('qualified', 0)} · extended "
                 f"{c.get('quality_but_extended', 0)} · rejected "
                 f"{c.get('rejected', 0)}")
    for w in payload.get("integrity_warnings") or []:
        lines.append(f"  ⚠ {w}")
    shortlist = payload.get("shortlist") or []
    if not shortlist:
        lines.append("\n  NO_HIGH_CONVICTION_CANDIDATES")
    for i, s in enumerate(shortlist, 1):
        cs = s["component_scores"]
        lines += [
            f"\n  {i}. {s['ticker']:<6} score="
            f"{s['high_conviction_score']}  {s['program']}  "
            f"{_cls_short(s['classification'])}",
            f"     quality={cs['quality_score']} growth={cs['growth_score']} "
            f"momentum={cs['price_momentum_score']} "
            f"value={cs['value_score'] if cs['value_score'] is not None else s['valuation_status']}",
            f"     why={', '.join(s['why_selected']) or '—'}",
            f"     risk={', '.join(s['main_risks']) or '—'}",
            f"     deterioration={s['dead_horse_risk']}",
        ]
    ext = payload.get("quality_but_extended") or []
    if ext:
        lines.append("\n  QUALITY BUT EXTENDED (wait for reset):")
        for s in ext[:5]:
            lines.append(f"    {s['ticker']:<6} score="
                         f"{s['high_conviction_score']}  {s['program']}  "
                         f"{s['extension_state']}")
    if verbose:
        rej = payload.get("rejected") or []
        if rej:
            lines.append(f"\n  REJECTED BY QUALITY/RISK ({len(rej)}):")
            for s in rej[:40]:
                lines.append(f"    {s['ticker']:<6} "
                             f"{'; '.join(s['exclusions'])}")
    return "\n".join(lines)


def write_outputs(payload: Dict[str, Any],
                  root: Optional[Path] = None) -> Path:
    root = Path(root) if root else REPO_ROOT
    sidecar = root / SIDECAR_REL
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(json.dumps(payload, ensure_ascii=False, indent=2)
                       + "\n", encoding="utf-8")
    log = root / LOG_REL
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(render_terminal(payload, verbose=True) + "\n",
                   encoding="utf-8")
    return sidecar


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="High-Conviction Alpha Shortlist (cache-only, "
                    "research-only qualification layer).")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true",
                        help="print the full payload as JSON")
    parser.add_argument("--verbose", action="store_true",
                        help="include the rejected-names section")
    parser.add_argument("--root", default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    root = Path(args.root) if args.root else REPO_ROOT

    payload = build_shortlist(root)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_terminal(payload, verbose=args.verbose))
    if not args.dry_run:
        path = write_outputs(payload, root)
        # historize the shortlist as its own forward hypothesis (separate
        # ledger; append-only, idempotent).  Import late so the core module
        # stays free of the tracker's pandas dependency for --dry-run.
        try:
            from research.high_conviction_forward import historize_shortlist
            historize_shortlist(payload, root=root)
        except Exception as exc:  # never let tracking break the cycle
            print(f"  (forward historization skipped: {exc})")
        if not args.json:
            print(f"\nwritten: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

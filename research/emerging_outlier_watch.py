#!/usr/bin/env python3
"""Emerging Outlier Watch — a SEPARATE research lane for credible early-stage,
turnaround, or pre-profit companies that fail the mature-company standards of
the High-Conviction Alpha shortlist but show multi-factor emergence evidence.

WHY THIS EXISTS
---------------
High-Conviction Alpha V1 is deliberately strict — it demands profitability /
funded-growth, clean structure, and full data coverage.  That precision is
correct for the default shortlist, but it makes legitimate emerging outliers
(pre-profit high-growth, funded turnarounds) mathematically hard to surface.
This lane preserves *controlled recall* for those names WITHOUT diluting the
shortlist: it is a distinct, higher-risk, clearly-labelled research list.

ADMISSION RULES (all must hold)
-------------------------------
  1. The name FAILS one or more conventional quality/profitability standards
     (i.e. it is not already HIGH_CONVICTION / PROMISING / WATCH_FOR_ENTRY).
  2. It passes EVERY data-integrity and minimum-liquidity gate — this lane
     never overrides an integrity or investability failure.
  3. It shows credible evidence across MULTIPLE INDEPENDENT dimensions, at
     least one of which is fundamental or balance-sheet (not just price or
     catalyst).  Price momentum alone, social attention alone, or a
     speculative narrative alone are NEVER sufficient.
  4. Its business-deterioration risk is not HIGH (multi-signal severe
     deterioration stays out — see business_deterioration_risk()).
  5. There is an explicit reason why the conventional weakness may be
     temporary or appropriate for the company's lifecycle.

DOCTRINE: RESEARCH-ONLY / CACHE-ONLY / CRED-FREE.  Reuses the FROZEN V1
evaluation read-only (never mutates or re-tunes it).  Writes only its own
sidecar + log + separate forward-hypothesis ledger.  Membership is not
validated alpha and never a trade recommendation.  Zero candidates is valid.
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

from research.high_conviction_alpha import (  # noqa: E402  (frozen V1 reuse)
    VERSION as HCA_VERSION, HOLDING_PERIODS, SHORTLIST_CLASSES,
    CLS_QUALITY_BUT_EXTENDED, PROGRAM_ORDER, ROUTED_REL, SCANNER_REL,
    dead_horse_risk, evaluate_candidate, _num, _load_json)

VERSION = "EMERGING_OUTLIER_WATCH_V1"
LANE = "EMERGING_OUTLIER_WATCH"

SIDECAR_REL = (Path("cache") / "research"
               / "emerging_outlier_watch_latest.json")
LOG_REL = Path("logs") / "emerging_outlier_watch_latest.txt"
HISTORY_REL = (Path("data") / "research"
               / "emerging_outlier_history.jsonl")

# Neutral, user-facing name for the internal V1 "dead_horse_risk" shorthand.
# The label is a research risk state, never a factual claim a business will
# fail.  The underlying computation is V1's (multi-signal; HIGH needs ≥2
# independent deterioration signals or one extreme condition).
DETERIORATION_RISK_LABEL = "business_deterioration_risk"

# Emergence-evidence thresholds (pre-registered for this lane's V1).
STRONG_GROWTH_3Q_PCT = 20.0
UNIT_ECONOMICS_GM_PCT = 50.0
LONG_RUNWAY_QUARTERS = 6.0          # ~18 months of funded burn
CONTROLLED_DILUTION_3Q_PCT = 5.0
NEAR_BREAKEVEN_NET_MARGIN_PCT = -10.0
MIN_INDEPENDENT_DIMENSIONS = 2       # of which ≥1 must be substantive


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── neutral business-deterioration wrapper over the frozen V1 logic ─────────


def business_deterioration_risk(item: Dict[str, Any], fund: Dict[str, Any]
                                ) -> Tuple[str, List[str]]:
    """Neutral-named view of V1's multi-signal deterioration risk.  Returns
    (LOW|MEDIUM|HIGH|UNKNOWN, signals).  Identical computation to V1 — the
    only change is the vocabulary used to present it.  HIGH already requires
    multiple independent signals or one extreme condition; a single ordinary
    weakness (unprofitable, below MA200, moderate dilution) cannot reach
    HIGH on its own."""
    return dead_horse_risk(item, fund)


# ── emergence evidence (multiple independent dimensions) ────────────────────

# dimension -> class.  "substantive" = FUNDAMENTAL or BALANCE (real business
# evidence); MOMENTUM / CATALYST are supporting but never sufficient alone.
_DIMENSION_CLASS = {
    "STRONG_REVENUE_GROWTH": "FUNDAMENTAL",
    "REVENUE_ACCELERATION": "FUNDAMENTAL",
    "STRONG_UNIT_ECONOMICS": "FUNDAMENTAL",
    "NEAR_BREAKEVEN_TRANSITION": "FUNDAMENTAL",
    "LONG_CASH_RUNWAY": "BALANCE",
    "CONTROLLED_DILUTION": "BALANCE",
    "STRONG_SECTOR_RELATIVE_MOMENTUM": "MOMENTUM",
    "CREDIBLE_CATALYST": "CATALYST",
}
_SUBSTANTIVE_CLASSES = {"FUNDAMENTAL", "BALANCE"}


def cash_runway_quarters(fund: Dict[str, Any]) -> Optional[float]:
    """Quarters of cash at the current burn rate.  None when not applicable
    (self-funding / profitable) or not computable."""
    fcf = _num(fund.get("ttm_fcf"))
    cash = _num(fund.get("cash_and_st_investments"))
    if fcf is None or cash is None:
        return None
    if fcf >= 0:
        return None  # self-funding — runway is not the relevant lens
    quarterly_burn = abs(fcf) / 4.0
    if quarterly_burn <= 0:
        return None
    return round(cash / quarterly_burn, 1)


def emergence_evidence(item: Dict[str, Any], fund: Dict[str, Any]
                       ) -> Dict[str, Any]:
    """Independent emergence dimensions with concrete, data-backed reasons.
    Never counts price momentum or social attention as substantive."""
    dims: List[Dict[str, str]] = []

    def _add(name: str, detail: str) -> None:
        dims.append({"dimension": name,
                     "class": _DIMENSION_CLASS[name], "detail": detail})

    g3 = _num(fund.get("rev_growth_3q_pct"))
    gq = _num(fund.get("rev_growth_qoq_pct"))
    gm = _num(fund.get("gross_margin_pct"))
    nm = _num(fund.get("net_margin_pct"))
    dil = _num(fund.get("dilution_3q_pct"))

    if g3 is not None and g3 >= STRONG_GROWTH_3Q_PCT:
        _add("STRONG_REVENUE_GROWTH", f"revenue +{g3:.0f}% (3q basis)")
    if gq is not None and g3 is not None:
        implied_q = (1.0 + g3 / 100.0) ** (1.0 / 3.0) - 1.0
        if gq / 100.0 > implied_q + 0.01:
            _add("REVENUE_ACCELERATION", "latest-quarter growth accelerating")
    if gm is not None and gm >= UNIT_ECONOMICS_GM_PCT:
        _add("STRONG_UNIT_ECONOMICS", f"gross margin {gm:.0f}%")
    if nm is not None and NEAR_BREAKEVEN_NET_MARGIN_PCT <= nm < 0:
        _add("NEAR_BREAKEVEN_TRANSITION",
             f"net margin {nm:.0f}% (approaching profitability)")
    runway = cash_runway_quarters(fund)
    if runway is not None and runway >= LONG_RUNWAY_QUARTERS:
        _add("LONG_CASH_RUNWAY", f"~{runway:.0f} quarters of cash runway")
    if dil is not None and dil <= CONTROLLED_DILUTION_3Q_PCT:
        _add("CONTROLLED_DILUTION", f"dilution {dil:+.0f}%/3q (controlled)")
    rs63 = _num(item.get("rs_63d_vs_spy"))
    if item.get("company_in_leading_sector") and rs63 is not None and rs63 > 0:
        _add("STRONG_SECTOR_RELATIVE_MOMENTUM",
             "leading sector with positive 63d RS")
    if item.get("has_analyst_upgrade") or (
            _num(item.get("days_to_earnings")) is not None
            and 0 <= _num(item.get("days_to_earnings")) <= 15):
        _add("CREDIBLE_CATALYST", "analyst upgrade or near-term earnings")

    classes = {d["class"] for d in dims}
    substantive = classes & _SUBSTANTIVE_CLASSES
    return {
        "dimensions": dims,
        "n_dimensions": len(dims),
        "n_substantive": sum(1 for d in dims
                             if d["class"] in _SUBSTANTIVE_CLASSES),
        "has_substantive": bool(substantive),
        "cash_runway_quarters": runway,
    }


def _lifecycle_reason(evidence: Dict[str, Any], fund: Dict[str, Any]) -> str:
    names = {d["dimension"] for d in evidence["dimensions"]}
    runway = evidence.get("cash_runway_quarters")
    ni = _num(fund.get("ttm_net_income"))
    if "NEAR_BREAKEVEN_TRANSITION" in names:
        return ("Approaching profitability with supporting growth — early "
                "loss may be a scaling artifact, not deterioration.")
    if "STRONG_REVENUE_GROWTH" in names or "REVENUE_ACCELERATION" in names:
        base = "Pre-profit high-growth: losses may reflect reinvestment"
        if runway is not None:
            base += f" and the balance sheet funds ~{runway:.0f} quarters"
        return base + "."
    if (ni is not None and ni <= 0) and "LONG_CASH_RUNWAY" in names:
        return ("Unprofitable but well-funded — runway supports the "
                "turnaround thesis before dilution pressure.")
    return ("Conventional weakness may be lifecycle-appropriate given the "
            "multi-factor emergence evidence.")


def classify_emerging(candidate: Dict[str, Any], item: Dict[str, Any],
                      fund: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return an Emerging Outlier record if the candidate qualifies for the
    lane, else None.  Pure function of the frozen V1 evaluation + the item /
    fund it was scored from."""
    cls = candidate.get("classification")
    # rule 1: must NOT already be a shortlist or quality-but-extended name
    if cls in SHORTLIST_CLASSES or cls == CLS_QUALITY_BUT_EXTENDED:
        return None
    # rule 2: integrity + liquidity must be clean.  In V1, ANY hard exclusion
    # (integrity, severe dilution, negative gross margin, deterioration HIGH,
    # structural collapse) makes a name REJECTED — none of those may enter
    # this lane, so we only admit names V1 did not hard-reject.
    if candidate.get("exclusions"):
        return None
    # rule 4: business-deterioration risk must not be HIGH
    drisk, dsignals = business_deterioration_risk(item, fund)
    if drisk == "HIGH":
        return None
    # rule 1 (defining condition): the lane is for PRE-PROFIT / loss-making
    # companies.  A profitable name is not an "emerging outlier" — it either
    # qualifies for the shortlist or is an ordinary mid-quality name.  This
    # is the conventional profitability standard the candidate must fail.
    ni = _num(fund.get("ttm_net_income"))
    if ni is None or ni > 0:
        return None
    # rule 3: multiple independent dimensions, ≥1 substantive
    evidence = emergence_evidence(item, fund)
    if evidence["n_dimensions"] < MIN_INDEPENDENT_DIMENSIONS \
            or not evidence["has_substantive"]:
        return None

    main_weakness = _primary_weakness(candidate, fund)
    return {
        "ticker": candidate.get("ticker"),
        "lane": LANE,
        "program": candidate.get("program"),
        "holding_period": candidate.get("holding_period"),
        "v1_classification": cls,
        "why_watched": [d["detail"] for d in evidence["dimensions"]],
        "emergence_dimensions": evidence["dimensions"],
        "n_independent_dimensions": evidence["n_dimensions"],
        "n_substantive_dimensions": evidence["n_substantive"],
        "why_not_high_conviction": main_weakness,
        DETERIORATION_RISK_LABEL: drisk,
        "deterioration_signals": dsignals,
        "cash_runway_quarters": evidence["cash_runway_quarters"],
        "dilution_3q_pct": _num(fund.get("dilution_3q_pct")),
        "lifecycle_reason": _lifecycle_reason(evidence, fund),
        "valuation_status": candidate.get("valuation_status"),
        "data_coverage": candidate.get("factor_coverage"),
        "high_conviction_score": candidate.get("high_conviction_score"),
        "sector": item.get("sector"),
        "same_session": item.get("same_session"),
        "research_only": True,
        "evidence_status": "UNVALIDATED_EMERGING",
    }


def _primary_weakness(candidate: Dict[str, Any], fund: Dict[str, Any]) -> str:
    ni = _num(fund.get("ttm_net_income"))
    if ni is not None and ni <= 0:
        return "currently unprofitable"
    if candidate.get("valuation_status") == "VALUATION_UNAVAILABLE":
        return "valuation unavailable"
    if not candidate.get("program_burden_met"):
        return candidate.get("program_burden_note") or "fails program burden"
    return "does not meet mature-company standards"


# ── build ────────────────────────────────────────────────────────────────────


def _iter_candidates(root: Path):
    """Yield (candidate, item, fund) for every routed candidate, re-running
    the FROZEN V1 evaluation.  Read-only reuse — no V1 mutation."""
    routed_doc = _load_json(root / ROUTED_REL)
    scanner = _load_json(root / SCANNER_REL)
    if not routed_doc or not routed_doc.get("present"):
        return
    scan_items = {str(w.get("ticker")): w
                  for w in (scanner or {}).get("watchlist") or []}
    try:
        from dashboards.research_command_center.data_adapter import (
            ArtifactStore, build_fundamentals)
        store = ArtifactStore(root=root)

        def _fund(t):
            return build_fundamentals(t, store)
    except Exception:
        def _fund(t):
            return {"fallback": "NO_DATA", "quarters_available": 0}

    for pid in PROGRAM_ORDER:
        block = (routed_doc.get("programs") or {}).get(pid) or {}
        for routed in block.get("candidates") or []:
            ticker = str(routed.get("ticker") or "")
            item = scan_items.get(ticker, {})
            fund = _fund(ticker)
            yield evaluate_candidate(routed, item, fund), item, fund


def build_watch(root: Optional[Path] = None,
                now: Optional[datetime] = None) -> Dict[str, Any]:
    root = Path(root) if root else REPO_ROOT
    now = now or _utcnow()
    routed_doc = _load_json(root / ROUTED_REL)
    base = {
        "kind": "emerging_outlier_watch", "version": VERSION,
        "generated_at": now.isoformat(), "research_only": True,
        "based_on_hca_version": HCA_VERSION,
        "guardrails": {
            "separate_from_high_conviction": True,
            "never_validated_alpha": True,
            "momentum_or_social_alone_insufficient": True,
            "no_v1_change": True,
        },
    }
    if not routed_doc or not routed_doc.get("present"):
        return {**base, "present": False, "watch": [],
                "reason": "routed-candidate sidecar missing"}

    watch: List[Dict[str, Any]] = []
    n_eval = 0
    for candidate, item, fund in _iter_candidates(root):
        n_eval += 1
        rec = classify_emerging(candidate, item, fund)
        if rec is not None:
            watch.append(rec)
    watch.sort(key=lambda r: (-(r.get("n_substantive_dimensions") or 0),
                              -(r.get("high_conviction_score") or 0)))

    return {
        **base, "present": True,
        "market_as_of_date": routed_doc.get("market_as_of_date"),
        "integrity_warnings": routed_doc.get("integrity_warnings") or [],
        "counts": {"evaluated": n_eval, "watch": len(watch)},
        "watch_status": "NO_EMERGING_OUTLIERS" if not watch else "OK",
        "watch": watch,
    }


# ── rendering ────────────────────────────────────────────────────────────────


def render_terminal(payload: Dict[str, Any]) -> str:
    lines = ["=== EMERGING OUTLIER WATCH ===",
             f"  {VERSION} · research-only · higher-risk emerging lane · "
             "separate from high conviction"]
    if not payload.get("present"):
        lines.append("  (no routed-candidate sidecar)")
        return "\n".join(lines)
    watch = payload.get("watch") or []
    lines.append(f"  evaluated {payload['counts']['evaluated']} · "
                 f"watch {payload['counts']['watch']}")
    if not watch:
        lines.append("\n  NO_EMERGING_OUTLIERS")
        return "\n".join(lines)
    for i, w in enumerate(watch, 1):
        dims = ", ".join(d["dimension"].lower() for d in
                         w["emergence_dimensions"])
        risk_bits = []
        if w.get("cash_runway_quarters") is not None:
            risk_bits.append(f"~{w['cash_runway_quarters']:.0f}q runway")
        if w.get("dilution_3q_pct") is not None:
            risk_bits.append(f"dilution {w['dilution_3q_pct']:+.0f}%/3q")
        lines += [
            f"\n  {i}. {w['ticker']:<6} {w['program']}  "
            f"{w['why_not_high_conviction']}",
            f"     why={dims}",
            f"     lifecycle={w['lifecycle_reason']}",
            f"     risk={', '.join(risk_bits) or 'unprofitable'} · "
            f"{DETERIORATION_RISK_LABEL}={w[DETERIORATION_RISK_LABEL]}",
        ]
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
    log.write_text(render_terminal(payload) + "\n", encoding="utf-8")
    return sidecar


def historize_watch(payload: Dict[str, Any], root: Optional[Path] = None,
                    date_override: Optional[str] = None) -> int:
    """Append today's watch members to the SEPARATE emerging-outlier
    hypothesis ledger (idempotent by ticker+date+version)."""
    root = Path(root) if root else REPO_ROOT
    if not payload or not payload.get("present"):
        return 0
    date = date_override or str(payload.get("generated_at")
                                or _utcnow().isoformat())[:10]
    path = root / HISTORY_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                existing.add((r.get("ticker"), r.get("appearance_date"),
                              r.get("version")))
            except Exception:
                continue
    rows = []
    for w in payload.get("watch") or []:
        key = (w.get("ticker"), date, VERSION)
        if key in existing:
            continue
        rows.append({
            "ticker": w.get("ticker"), "appearance_date": date,
            "version": VERSION, "lane": LANE, "program": w.get("program"),
            "sector": w.get("sector"),
            "n_substantive_dimensions": w.get("n_substantive_dimensions"),
            "research_only": True,
        })
    if rows:
        with path.open("a", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(rows)


# ── forward validation (distinct hypothesis; own ledger) ────────────────────

FORWARD_SIDECAR_REL = (Path("cache") / "research"
                       / "emerging_outlier_forward_latest.json")
FORWARD_LOG_REL = Path("logs") / "emerging_outlier_forward_latest.txt"
V_NEED_MORE_DATA = "NEED_MORE_DATA"
MIN_MATURED_FOR_VERDICT = 10


def build_forward(root: Optional[Path] = None,
                  now: Optional[datetime] = None) -> Dict[str, Any]:
    """Forward validation for the Emerging Outlier lane as its OWN
    hypothesis — never merged with the shortlist or program cohorts.
    Reuses the high-conviction forward machinery on the emerging ledger."""
    root = Path(root) if root else REPO_ROOT
    now = now or _utcnow()
    from research.high_conviction_forward import (
        _load_closes, _resolve_entry, _cohort_summary, HORIZONS, BENCHMARKS)
    path = root / HISTORY_REL
    base = {
        "kind": "emerging_outlier_forward", "version": VERSION,
        "generated_at": now.isoformat(), "research_only": True,
        "separate_hypothesis": True, "horizons_td": list(HORIZONS),
        "benchmarks": list(BENCHMARKS) + ["SECTOR_ETF"],
        "note": "Emerging Outlier lane tracked as its own hypothesis — "
                "never merged with the high-conviction shortlist or the "
                "program-candidate ledger.",
    }
    if not path.exists():
        return {**base, "present": False, "verdict": V_NEED_MORE_DATA,
                "verdict_reason": "no emerging-outlier history recorded yet"}
    history = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                history.append(json.loads(line))
            except Exception:
                continue
    if not history:
        return {**base, "present": False, "verdict": V_NEED_MORE_DATA,
                "verdict_reason": "empty emerging-outlier history"}
    bench_cache: Dict[str, Any] = {}
    resolved = [_resolve_entry(e, root, bench_cache) for e in history]
    cohort = _cohort_summary(resolved)
    n_matured = cohort["10d"]["vs_spy"]["n"]
    verdict = V_NEED_MORE_DATA
    reason = (f"only {n_matured} matured 10d episodes "
              f"(need >={MIN_MATURED_FOR_VERDICT})")
    return {**base, "present": True, "n_history_rows": len(history),
            "cohort": cohort, "verdict": verdict, "verdict_reason": reason}


def write_forward(payload: Dict[str, Any],
                  root: Optional[Path] = None) -> Path:
    root = Path(root) if root else REPO_ROOT
    sidecar = root / FORWARD_SIDECAR_REL
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(json.dumps(payload, ensure_ascii=False, indent=2)
                       + "\n", encoding="utf-8")
    log = root / FORWARD_LOG_REL
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        f"EMERGING OUTLIER FORWARD — {payload.get('verdict')}: "
        f"{payload.get('verdict_reason')}\n", encoding="utf-8")
    return sidecar


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Emerging Outlier Watch (separate research lane; "
                    "cache-only, research-only; V1 unchanged).")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--root", default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    root = Path(args.root) if args.root else REPO_ROOT

    payload = build_watch(root)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_terminal(payload))
    if not args.dry_run:
        path = write_outputs(payload, root)
        n = historize_watch(payload, root=root)
        fwd = build_forward(root)
        write_forward(fwd, root)
        if not args.json:
            print(f"\nwritten: {path} (+{n} history rows)")
            print(f"forward: {fwd.get('verdict')} — {fwd.get('verdict_reason')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

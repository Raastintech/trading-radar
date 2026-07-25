#!/usr/bin/env python3
"""Alpha Focus — a same-day manual research prioritization layer.

NOT a new engine. Alpha Focus does not delete candidates, change scores,
change rankings, change scanner logic, or create trade signals. It is a
same-day, ticker-level presentation filter over candidates that are already
scanned, scored, and forward-tracked by the existing pipeline
(`research_watchlist_history.jsonl`). It computes no new price returns,
writes no new forward-evidence ledger, and defines no new score — it reuses
`research.cohort_attribution._enrich_rows` for enrichment, each row's
existing `research_score` for ordering, and the existing High-Conviction /
Emerging Outlier artifacts' own fields (`why_selected`, `why_watched`,
`business_deterioration_risk`) for read-only context.

A ticker only lands in the operator's "worth manual research today" buckets
if it has at least one POSITIVE research reason (HC-and-not-extended,
EO-and-not-high-risk, reset/watch-for-entry, profitable quality, mid/known-
cap with supporting quality-or-reset evidence, or a clear non-extended
catalyst reason). Everything else is deprioritized, with the specific
reason cited (extension, repeat-decay for broad-scanner-only names, weakest
signal family, or simply no positive reason found).

Buckets:
  review_now            — positive reason, not EO-only-risk, not extended
  higher_risk_eo_review — positive reason via Emerging Outlier membership
  wait_for_reset         — extended AND (HC/EO/profitable) — a strong profile
                           that should be revisited after a pullback, not
                           dropped
  deprioritized_by_focus_rule — no qualifying positive reason today

Deprioritization is same-day and reason-based, never permanent: HC/EO
membership is untouched (this module never reads HC/EO's own selection
logic, only their already-published output), so a deprioritized HC/EO name
still appears, unchanged, in its own HC/EO section. Repeat-candidate decay
is a CAUTION flag for HC/EO names (kept visible), not an automatic
exclusion — only broad-scanner-only repeats (not also HC/EO) are
deprioritized for that reason, per the root-cause audit's cohort-level
finding.

This module never changes scanner logic, scores, rankings, routing, gates,
thresholds, factor weights, High-Conviction rules, Emerging Outlier rules,
program verdict thresholds, or candidate selection. It relabels presentation
of already-selected candidates only. research_only=true, promote_to_signal=false.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_ROOT_FOR_IMPORTS = Path(__file__).resolve().parents[1]
if str(_ROOT_FOR_IMPORTS) not in sys.path:
    sys.path.insert(0, str(_ROOT_FOR_IMPORTS))

from research.cohort_attribution import (
    _enrich_rows,
    _load_json,
    _load_jsonl,
)

VERSION = "ALPHA_FOCUS_V1"
REPO_ROOT = Path(__file__).resolve().parents[1]

HISTORY_REL = Path("data/research/research_watchlist_history.jsonl")
COHORT_ATTRIBUTION_REL = Path("cache/research/cohort_attribution_latest.json")
ROOT_CAUSE_REL = Path("cache/research/alpha_failure_root_cause_latest.json")
HIGH_CONVICTION_REL = Path("cache/research/high_conviction_alpha_latest.json")
EMERGING_OUTLIER_REL = Path("cache/research/emerging_outlier_watch_latest.json")
OUT_JSON_REL = Path("cache/research/alpha_focus_latest.json")
OUT_TXT_REL = Path("logs/alpha_focus_latest.txt")
DOC_REL = Path("docs/research/ALPHA_FOCUS.md")

RS_MOMENTUM_CATEGORIES = {"rs_momentum_leader"}
CATALYST_CATEGORIES = {"catalyst_watch"}
HIGH_RISK_LEVELS = {"HIGH"}

# ── deprioritization reasons (same-day, not permanent) ──────────────────────

REASON_EXTENDED = "extended_top_ablation_drag"
REASON_REPEAT_DECAY = "repeat_candidate_decay"
REASON_RS_MOMENTUM = "rs_momentum_weakest_signal_family"
REASON_NO_POSITIVE_REASON = "no_positive_research_reason"

REASON_DESCRIPTIONS: Dict[str, str] = {
    REASON_EXTENDED: (
        "Deprioritized because extension was the top ablation-identified drag "
        "in the root-cause audit."
    ),
    REASON_REPEAT_DECAY: (
        "Deprioritized because repeat-candidate decay was confirmed in the "
        "root-cause audit. This does not mean every repeat candidate is "
        "permanently bad — it flags this name for extra manual scrutiny today. "
        "(Only applied to broad-scanner-only repeats; HC/EO repeats are kept "
        "visible with a caution flag instead.)"
    ),
    REASON_RS_MOMENTUM: (
        "Deprioritized because rs_momentum_leader was the weakest signal "
        "family in the root-cause audit."
    ),
    REASON_NO_POSITIVE_REASON: (
        "Deprioritized because no positive research reason (High-Conviction "
        "or Emerging Outlier membership, reset/watch-for-entry, profitable "
        "quality, mid/known-cap with supporting evidence, or a clear catalyst "
        "reason) was found for this name today."
    ),
}

REPEAT_CAUTION_TEXT = (
    "Repeat-candidate caution: root-cause audit showed repeat decay; manual "
    "review required."
)

WAIT_FOR_RESET_TEXT = "Strong profile, wait for reset."

PURPOSE_STATEMENT = (
    "Alpha Focus is a same-day manual research prioritization layer. It does "
    "not delete candidates, change scores, change rankings, change scanner "
    "logic, or create trade signals."
)

# ── positive research reasons (a ticker needs >=1 to reach a review bucket) ─

REASON_HC_NOT_EXTENDED = "high_conviction_not_extended"
REASON_EO_NOT_HIGH_RISK = "emerging_outlier_not_high_risk"
REASON_RESET_WATCH = "reset_or_watch_for_entry"
REASON_PROFITABLE_QUALITY = "profitable_quality"
REASON_MID_CAP_QUALITY_OR_RESET = "mid_or_known_cap_with_quality_or_reset"
REASON_NON_EXTENDED_CATALYST = "non_extended_catalyst"

POSITIVE_REASON_TEXT: Dict[str, str] = {
    REASON_HC_NOT_EXTENDED: "High-Conviction Alpha name, not extended.",
    REASON_EO_NOT_HIGH_RISK: "Emerging Outlier Watch name without a HIGH business-deterioration-risk flag.",
    REASON_RESET_WATCH: (
        "Reset/watch-for-entry candidate — the only cohort with a positive, "
        "high-win-rate forward read in the root-cause audit."
    ),
    REASON_PROFITABLE_QUALITY: "Profitable-quality candidate.",
    REASON_MID_CAP_QUALITY_OR_RESET: (
        "Mid/known-cap candidate with supporting profitable-quality or "
        "reset/watch-for-entry evidence."
    ),
    REASON_NON_EXTENDED_CATALYST: "Non-extended candidate with a clear catalyst reason.",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _latest_appearance_date(rows: List[Dict[str, Any]]) -> Optional[str]:
    dates = sorted({str(r.get("appearance_date") or "")[:10] for r in rows if r.get("appearance_date")})
    return dates[-1] if dates else None


def _load_hc_eo_context(root: Path) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """Read-only lookup of today's HC shortlist / EO watch entries.

    Passthrough only — never reads/writes anything else in either artifact,
    never changes HC/EO selection. Used solely so Alpha Focus can (a) flag
    when a deprioritized or review-bucket name is also an HC/EO name, and
    (b) surface each artifact's own `why_selected`/`why_watched`/
    `business_deterioration_risk` fields verbatim. HC/EO's own sections
    remain fully intact and unaffected regardless of what Alpha Focus does."""
    hc = _load_json(root / HIGH_CONVICTION_REL) or {}
    eo = _load_json(root / EMERGING_OUTLIER_REL) or {}
    hc_by_ticker = {
        str(t.get("ticker") or "").upper(): t
        for t in (hc.get("shortlist") or []) if t.get("ticker")
    }
    eo_by_ticker = {
        str(t.get("ticker") or "").upper(): t
        for t in (eo.get("watch") or []) if t.get("ticker")
    }
    return hc_by_ticker, eo_by_ticker


def _positive_reasons(row: Dict[str, Any], is_hc: bool, is_eo: bool,
                       eo_risk: Optional[str]) -> List[str]:
    """Every positive research reason code this candidate qualifies for.

    An empty list means this candidate has no positive research thesis
    today and belongs in the deprioritized bucket (unless it's an extended
    strong profile, handled separately as wait_for_reset)."""
    is_extended = row.get("extension_bucket") == "EXTENDED"
    is_reset_watch = row.get("extension_bucket") == "RESET_OR_WATCH_FOR_ENTRY"
    is_profitable = row.get("profitability_bucket") == "PROFITABLE"
    is_mid_or_known_cap = row.get("market_cap_bucket") in {"MID_CAP", "LARGE_CAP"}
    is_catalyst = str(row.get("category") or "") in CATALYST_CATEGORIES

    reasons: List[str] = []
    if is_hc and not is_extended:
        reasons.append(REASON_HC_NOT_EXTENDED)
    if is_eo and str(eo_risk or "").upper() not in HIGH_RISK_LEVELS:
        reasons.append(REASON_EO_NOT_HIGH_RISK)
    if is_reset_watch:
        reasons.append(REASON_RESET_WATCH)
    if is_profitable and not is_extended:
        reasons.append(REASON_PROFITABLE_QUALITY)
    if is_mid_or_known_cap and not is_extended and (is_profitable or is_reset_watch):
        reasons.append(REASON_MID_CAP_QUALITY_OR_RESET)
    if is_catalyst and not is_extended:
        reasons.append(REASON_NON_EXTENDED_CATALYST)
    return reasons


def _focus_tags(row: Dict[str, Any]) -> Dict[str, bool]:
    """Informational labels only — no score, no ranking formula."""
    return {
        "is_reset_or_watch_for_entry": row.get("extension_bucket") == "RESET_OR_WATCH_FOR_ENTRY",
        "is_profitable": row.get("profitability_bucket") == "PROFITABLE",
        "is_mid_or_known_cap": row.get("market_cap_bucket") in {"MID_CAP", "LARGE_CAP"},
    }


def _candidate_view(row: Dict[str, Any], hc_by_ticker: Dict[str, Dict[str, Any]],
                     eo_by_ticker: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    ticker = str(row.get("ticker") or "").upper()
    hc_entry = hc_by_ticker.get(ticker)
    eo_entry = eo_by_ticker.get(ticker)
    view = {
        "ticker": row.get("ticker"),
        "appearance_date": row.get("appearance_date"),
        "research_score": row.get("research_score"),
        "sector": row.get("sector"),
        "category": row.get("category"),
        "extension_bucket": row.get("extension_bucket"),
        "profitability_bucket": row.get("profitability_bucket"),
        "market_cap_bucket": row.get("market_cap_bucket"),
        "candidate_sequence": row.get("candidate_sequence"),
        "is_high_conviction": hc_entry is not None,
        "is_emerging_outlier": eo_entry is not None,
    }
    view.update(_focus_tags(row))
    if hc_entry is not None:
        view["source_reasons"] = hc_entry.get("why_selected") or []
    elif eo_entry is not None:
        view["source_reasons"] = eo_entry.get("why_watched") or []
        view["business_deterioration_risk"] = eo_entry.get("business_deterioration_risk")
    return view


def _evidence_citations(cohort_report: Optional[Dict[str, Any]],
                         root_cause: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Read-only excerpt of already-computed evidence. No recompute here."""
    cohorts = {str(c.get("id")): c for c in (cohort_report or {}).get("cohorts") or [] if c.get("id")}
    broad = cohorts.get("broad_scanner") or {}
    broad_h = (broad.get("horizons") or {}).get("10d") or {}
    rc_dash = (root_cause or {}).get("dashboard_summary") or {}
    rc_baseline = (root_cause or {}).get("baseline_comparison") or {}
    rand = (rc_baseline.get("random_same_universe_control") or {})
    pool = (rc_baseline.get("candidate_pool_vs_market_benchmarks") or {})
    random_beats_pool = (
        rand.get("mean_return_pct") is not None and pool.get("excess_vs_spy_pct") is not None
        and rand.get("mean_return_pct") > (pool.get("excess_vs_spy_pct") or 0)
    )
    return {
        "broad_scanner_verdict": broad.get("primary_result"),
        "broad_scanner_mean_10d_pct": broad_h.get("mean_return_pct"),
        "broad_scanner_sample_status": broad_h.get("sample_status"),
        "top_ablation_cause": rc_dash.get("top_likely_failure_cause"),
        "random_control_beats_candidate_pool": random_beats_pool,
        "source_artifacts": {
            "cohort_attribution": str(COHORT_ATTRIBUTION_REL),
            "alpha_failure_root_cause_audit": str(ROOT_CAUSE_REL),
        },
    }


def _assign_bucket(row: Dict[str, Any], view: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Returns (bucket_name, updated_view). bucket_name is one of:
    'wait_for_reset', 'review_now', 'higher_risk_eo_review', 'deprioritized'."""
    is_hc = view["is_high_conviction"]
    is_eo = view["is_emerging_outlier"]
    is_extended = row.get("extension_bucket") == "EXTENDED"
    is_repeat = row.get("candidate_sequence") == "REPEAT"
    is_profitable = row.get("profitability_bucket") == "PROFITABLE"
    is_rs_momentum = str(row.get("category") or "") in RS_MOMENTUM_CATEGORIES
    eo_risk = view.get("business_deterioration_risk")

    if is_extended:
        has_strong_profile = is_hc or is_eo or is_profitable
        if has_strong_profile:
            view["wait_for_reset_reason"] = WAIT_FOR_RESET_TEXT
            return "wait_for_reset", view
        view["deprioritized_by_focus_rule"] = REASON_EXTENDED
        view["reason_description"] = REASON_DESCRIPTIONS[REASON_EXTENDED]
        return "deprioritized", view

    if is_repeat and not (is_hc or is_eo):
        view["deprioritized_by_focus_rule"] = REASON_REPEAT_DECAY
        view["reason_description"] = REASON_DESCRIPTIONS[REASON_REPEAT_DECAY]
        return "deprioritized", view

    reasons = _positive_reasons(row, is_hc, is_eo, eo_risk)
    if not reasons:
        reason = REASON_RS_MOMENTUM if is_rs_momentum else REASON_NO_POSITIVE_REASON
        view["deprioritized_by_focus_rule"] = reason
        view["reason_description"] = REASON_DESCRIPTIONS[reason]
        return "deprioritized", view

    view["focus_reasons"] = reasons
    view["focus_reasons_text"] = [POSITIVE_REASON_TEXT[r] for r in reasons]
    if is_repeat and (is_hc or is_eo):
        view["repeat_caution"] = REPEAT_CAUTION_TEXT
    return ("higher_risk_eo_review" if is_eo else "review_now"), view


def build_report(root: Optional[Path] = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    root = Path(root) if root else REPO_ROOT
    now = now or _utcnow()

    raw_history = _load_jsonl(root / HISTORY_REL)
    if not raw_history:
        return {
            "kind": "alpha_focus",
            "version": VERSION,
            "generated_at": now.isoformat(),
            "research_only": True,
            "promote_to_signal": False,
            "fallback": "MISSING_ARTIFACT",
            "missing_artifact": str(HISTORY_REL),
            "guardrails": _guardrails(),
        }

    history = _enrich_rows(root, raw_history, lane="BROAD_SCANNER")
    market_as_of_date = _latest_appearance_date(history)
    today_rows = [r for r in history if str(r.get("appearance_date") or "")[:10] == market_as_of_date]
    hc_by_ticker, eo_by_ticker = _load_hc_eo_context(root)

    review_now: List[Dict[str, Any]] = []
    higher_risk_eo_review: List[Dict[str, Any]] = []
    wait_for_reset: List[Dict[str, Any]] = []
    deprioritized: List[Dict[str, Any]] = []

    for row in today_rows:
        view = _candidate_view(row, hc_by_ticker, eo_by_ticker)
        bucket, view = _assign_bucket(row, view)
        if bucket == "review_now":
            review_now.append(view)
        elif bucket == "higher_risk_eo_review":
            higher_risk_eo_review.append(view)
        elif bucket == "wait_for_reset":
            wait_for_reset.append(view)
        else:
            deprioritized.append(view)

    def _by_score(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return sorted(rows, key=lambda r: (r.get("research_score") is None, -(r.get("research_score") or 0)))

    review_now = _by_score(review_now)
    higher_risk_eo_review = _by_score(higher_risk_eo_review)
    wait_for_reset = _by_score(wait_for_reset)

    by_reason: Dict[str, List[Dict[str, Any]]] = {}
    for r in deprioritized:
        by_reason.setdefault(r["deprioritized_by_focus_rule"], []).append(r)
    deprioritized_by_reason = {
        reason: {
            "count": len(items),
            "tickers": [i["ticker"] for i in items],
            "description": REASON_DESCRIPTIONS.get(reason),
        }
        for reason, items in sorted(by_reason.items())
    }

    all_rows = review_now + higher_risk_eo_review + wait_for_reset + deprioritized
    cohort_report = _load_json(root / COHORT_ATTRIBUTION_REL)
    root_cause = _load_json(root / ROOT_CAUSE_REL)

    return {
        "kind": "alpha_focus",
        "version": VERSION,
        "generated_at": now.isoformat(),
        "research_only": True,
        "promote_to_signal": False,
        "purpose_statement": PURPOSE_STATEMENT,
        "market_as_of_date": market_as_of_date,
        "source_artifacts": {
            "watchlist_history": str(HISTORY_REL),
            "cohort_attribution": str(COHORT_ATTRIBUTION_REL),
            "alpha_failure_root_cause_audit": str(ROOT_CAUSE_REL),
            "high_conviction_alpha": str(HIGH_CONVICTION_REL),
            "emerging_outlier_watch": str(EMERGING_OUTLIER_REL),
        },
        "positive_reasons": POSITIVE_REASON_TEXT,
        "deprioritization_reasons": REASON_DESCRIPTIONS,
        "counts": {
            "total_today": len(today_rows),
            "review_now": len(review_now),
            "higher_risk_eo_review": len(higher_risk_eo_review),
            "wait_for_reset": len(wait_for_reset),
            "deprioritized": len(deprioritized),
            "high_conviction_overlap": sum(1 for r in all_rows if r.get("is_high_conviction")),
            "emerging_outlier_overlap": sum(1 for r in all_rows if r.get("is_emerging_outlier")),
        },
        "review_now": review_now,
        "higher_risk_eo_review": higher_risk_eo_review,
        "wait_for_reset": wait_for_reset,
        "deprioritized_by_focus_rule": deprioritized,
        "deprioritized_by_reason_summary": deprioritized_by_reason,
        "evidence_citations": _evidence_citations(cohort_report, root_cause),
        "guardrails": _guardrails(),
        "caveats": [
            PURPOSE_STATEMENT,
            "review_now / higher_risk_eo_review entries each carry focus_reasons_text answering "
            "\"why is this worth manual research today\"; higher_risk_eo_review is kept separate "
            "from review_now because Emerging Outlier is, by construction, an unprofitable/early "
            "lane and inherently higher risk than High-Conviction or profitable-quality names.",
            "wait_for_reset holds strong profiles (HC/EO/profitable) that are currently extended — "
            "they are not dropped, just flagged to revisit after a pullback.",
            "Deprioritized names are not rejected from research and are not excluded forever — they "
            "still appear, unaffected, in their original High-Conviction / Emerging Outlier sections "
            "(this module never reads or writes those artifacts' selection logic) and are listed "
            "here only for same-day manual-research prioritization.",
            "Repeat-candidate decay is a caution flag for HC/EO names (they stay visible with "
            "repeat_caution set), not an automatic exclusion; only broad-scanner-only repeats are "
            "deprioritized for that reason.",
            "This is a same-day presentation filter over candidates the existing scanner already "
            "selected and scored — it changes no scanner logic, score, ranking, routing, gate, "
            "threshold, factor weight, High-Conviction rule, Emerging Outlier rule, or program "
            "verdict, and it does not affect what the existing forward tracker measures.",
            "Bucket membership is not an alpha claim; no forward-evidence maturity floor has been "
            "applied to this list specifically (rely on the broad tracker / cohort attribution / "
            "root-cause audit for maturity-gated verdicts).",
        ],
    }


def _guardrails() -> Dict[str, Any]:
    return {
        "no_scanner_logic_change": True,
        "no_score_or_ranking_change": True,
        "no_routing_change": True,
        "no_gate_or_threshold_change": True,
        "no_factor_weight_change": True,
        "no_high_conviction_rule_change": True,
        "no_emerging_outlier_rule_change": True,
        "no_program_verdict_threshold_change": True,
        "no_candidate_selection_change": True,
        "research_only": True,
        "cache_only_inputs": True,
        "no_trade_recommendations": True,
        "promote_to_signal": False,
    }


def _fmt_pct(value: Any) -> str:
    return "n/a" if value is None else f"{float(value):.2f}%"


def render_text(report: Dict[str, Any]) -> str:
    if report.get("fallback"):
        return f"ALPHA FOCUS - fallback: {report.get('missing_artifact')}\n"
    counts = report.get("counts") or {}
    ev = report.get("evidence_citations") or {}
    lines = [
        f"ALPHA FOCUS - {report.get('generated_at')}",
        report.get("purpose_statement") or PURPOSE_STATEMENT,
        "",
        f"Research only: {report.get('research_only')}  ·  Promote to signal: {report.get('promote_to_signal')}",
        f"Market as-of date: {report.get('market_as_of_date')}",
        f"Total today: {counts.get('total_today')}  ·  Review Now: {counts.get('review_now')}  ·  "
        f"Higher-Risk EO Review: {counts.get('higher_risk_eo_review')}  ·  "
        f"Wait for Reset: {counts.get('wait_for_reset')}  ·  Deprioritized: {counts.get('deprioritized')}",
        f"HC overlap: {counts.get('high_conviction_overlap')}  ·  EO overlap: {counts.get('emerging_outlier_overlap')}",
        "",
        f"Broad scanner verdict: {ev.get('broad_scanner_verdict')} "
        f"(mean_10d={_fmt_pct(ev.get('broad_scanner_mean_10d_pct'))}, status={ev.get('broad_scanner_sample_status')})",
        f"Top ablation cause: {ev.get('top_ablation_cause')}",
        f"Random control beats candidate pool: {ev.get('random_control_beats_candidate_pool')}",
        "",
        "Review Now (top by existing research_score):",
    ]
    for row in report.get("review_now") or []:
        caution = f" | {row['repeat_caution']}" if row.get("repeat_caution") else ""
        lines.append(
            f"- {row.get('ticker')}: score={row.get('research_score')} "
            f"why={'; '.join(row.get('focus_reasons_text') or [])}{caution}"
        )
    lines.append("")
    lines.append("Higher-Risk EO Review (top by existing research_score):")
    for row in report.get("higher_risk_eo_review") or []:
        caution = f" | {row['repeat_caution']}" if row.get("repeat_caution") else ""
        lines.append(
            f"- {row.get('ticker')}: score={row.get('research_score')} "
            f"risk={row.get('business_deterioration_risk')} "
            f"why={'; '.join(row.get('focus_reasons_text') or [])}{caution}"
        )
    lines.append("")
    lines.append("Wait for Reset (strong profile, currently extended):")
    for row in report.get("wait_for_reset") or []:
        lines.append(f"- {row.get('ticker')}: {row.get('wait_for_reset_reason')}")
    lines.append("")
    lines.append("Deprioritized / Broad Noise by rule (still visible in their own HC/EO sections if applicable):")
    for reason, summary in sorted((report.get("deprioritized_by_reason_summary") or {}).items()):
        lines.append(f"- {reason}: {summary.get('count')} ({', '.join(summary.get('tickers') or [])})")
        lines.append(f"    {summary.get('description')}")
    lines.append("")
    lines.append("Caveats:")
    lines.extend(f"- {c}" for c in report.get("caveats") or [])
    return "\n".join(lines) + "\n"


def render_markdown(report: Dict[str, Any]) -> str:
    counts = report.get("counts") or {}
    return "\n".join([
        "# Alpha Focus",
        "",
        f"Generated: `{report.get('generated_at')}`",
        "",
        report.get("purpose_statement") or PURPOSE_STATEMENT,
        "",
        "Same-day, ticker-level presentation layer over already-scanned/scored candidates. Not a "
        "new engine: no new score, no new forward-evidence ledger, no scanner/gate/threshold/"
        "HC/EO/program-verdict change. Deprioritized names are not rejected from research — they "
        "remain visible, unaffected, in their original High-Conviction / Emerging Outlier sections.",
        "",
        "## Summary",
        f"- Market as-of date: `{report.get('market_as_of_date')}`",
        f"- Total today: {counts.get('total_today')} · Review Now: {counts.get('review_now')} · "
        f"Higher-Risk EO Review: {counts.get('higher_risk_eo_review')} · "
        f"Wait for Reset: {counts.get('wait_for_reset')} · Deprioritized: {counts.get('deprioritized')}",
        f"- HC overlap: {counts.get('high_conviction_overlap')} · EO overlap: {counts.get('emerging_outlier_overlap')}",
        f"- research_only: `{report.get('research_only')}` · promote_to_signal: `{report.get('promote_to_signal')}`",
        "",
        "## Buckets",
        "",
        "- **Review Now** — has >=1 positive research reason, not extended, not EO-only.",
        "- **Higher-Risk EO Review** — has >=1 positive research reason via Emerging Outlier "
        "membership (inherently unprofitable/early, kept separate from Review Now).",
        "- **Wait for Reset** — strong profile (HC/EO/profitable) but currently extended.",
        "- **Deprioritized / Broad Noise** — no qualifying positive reason today.",
        "",
        "## Rules",
        "",
        "1. Positive reasons required to enter Review Now / Higher-Risk EO Review: "
        "HC-and-not-extended, EO-and-not-high-risk, reset/watch-for-entry, profitable quality, "
        "mid/known-cap with quality-or-reset evidence, or a clear non-extended catalyst reason.",
        "2. Repeat-candidate decay is a caution flag (not exclusion) for HC/EO names; "
        "broad-scanner-only repeats are deprioritized.",
        "3. Extended HC/EO/profitable names go to Wait for Reset, not deprioritized.",
        "",
    ]) + "\n"


def write_artifacts(report: Dict[str, Any], root: Optional[Path] = None, *, docs: bool = False) -> Dict[str, str]:
    root = Path(root) if root else REPO_ROOT
    json_path = root / OUT_JSON_REL
    txt_path = root / OUT_TXT_REL
    _write_json(json_path, report)
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.write_text(render_text(report), encoding="utf-8")
    paths = {"json": str(json_path), "text": str(txt_path)}
    if docs:
        doc_path = root / DOC_REL
        doc_path.parent.mkdir(parents=True, exist_ok=True)
        doc_path.write_text(render_markdown(report), encoding="utf-8")
        paths["docs"] = str(doc_path)
    return paths


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build the Alpha Focus sidecar (research-only, cache-only).")
    parser.add_argument("--root", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--docs", action="store_true", help="Also write docs/research/ALPHA_FOCUS.md")
    parser.add_argument("--json", action="store_true", help="Print the report JSON to stdout")
    args = parser.parse_args(argv)
    root = Path(args.root) if args.root else REPO_ROOT
    report = build_report(root)
    paths = write_artifacts(report, root, docs=args.docs)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"wrote {paths['json']}")
        print(f"wrote {paths['text']}")
        if "docs" in paths:
            print(f"wrote {paths['docs']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

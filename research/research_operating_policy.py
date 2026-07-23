#!/usr/bin/env python3
"""Evidence-weighted research operating policy.

Non-mutating operator guidance only: reads the cohort-attribution sidecar,
writes a policy artifact, and never changes scanner, scoring, routing,
selection, gates, thresholds, factor weights, HC/EO rules, or safeguards.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

VERSION = "RESEARCH_OPERATING_POLICY_V1"
MIN_MATURED_TO_EVALUATE = 10
LONG_TERM_PRIMARY_HORIZONS = ("45d", "60d")

REPO_ROOT = Path(__file__).resolve().parents[1]
COHORT_ATTRIBUTION_REL = Path("cache/research/cohort_attribution_latest.json")
OUT_JSON_REL = Path("cache/research/research_operating_policy_latest.json")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except Exception:
        return None


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _cohort_index(report: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        str(c.get("id")): c
        for c in report.get("cohorts") or []
        if isinstance(c, dict) and c.get("id")
    }


def _find(cohorts: Dict[str, Dict[str, Any]], *ids: str) -> Optional[Dict[str, Any]]:
    for cid in ids:
        if cohorts.get(cid):
            return cohorts[cid]
    return None


def _horizon(cohort: Optional[Dict[str, Any]], horizon: str) -> Dict[str, Any]:
    return ((cohort or {}).get("horizons") or {}).get(horizon) or {}


def _metric(cohort: Optional[Dict[str, Any]], horizon: str, key: str) -> Any:
    return _horizon(cohort, horizon).get(key)


def _mean(cohort: Optional[Dict[str, Any]], horizon: str) -> Optional[float]:
    value = _metric(cohort, horizon, "mean_return_pct")
    return float(value) if value is not None else None


def _matured(cohort: Optional[Dict[str, Any]], horizon: str) -> int:
    value = _metric(cohort, horizon, "matured_count")
    try:
        return int(value or 0)
    except Exception:
        return 0


def _status(cohort: Optional[Dict[str, Any]], horizon: str) -> str:
    return str(_metric(cohort, horizon, "sample_status")
               or (cohort or {}).get("sample_status") or "UNKNOWN")


def _result(cohort: Optional[Dict[str, Any]]) -> str:
    return str((cohort or {}).get("primary_result") or "unknown").lower()


def _confidence(sample_status: str, *, positive_claim: bool = False) -> str:
    status = str(sample_status or "").upper()
    if status == "ROBUST":
        return "MEDIUM" if positive_claim else "HIGH"
    if status == "MEANINGFUL":
        return "LOW" if positive_claim else "MEDIUM"
    if status == "PROVISIONAL":
        return "LOW"
    return "VERY_LOW"


def _basis(cohort: Optional[Dict[str, Any]], horizon: str, source: str,
           extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    h = _horizon(cohort, horizon)
    basis = {
        "source": source,
        "primary_horizon": horizon,
        "cohort_id": (cohort or {}).get("id"),
        "cohort_label": (cohort or {}).get("label"),
        "matured_count": h.get("matured_count"),
        "resolution_pct": h.get("resolution_pct"),
        "mean_return_pct": h.get("mean_return_pct"),
        "median_return_pct": h.get("median_return_pct"),
        "winsorized_mean_return_pct": h.get("winsorized_mean_return_pct"),
        "win_rate": h.get("win_rate"),
        "excess_vs_spy_pct": h.get("excess_vs_spy_pct"),
        "negative_return_sum_pct_points": h.get("negative_return_sum_pct_points"),
        "sample_status": h.get("sample_status"),
        "primary_result": (cohort or {}).get("primary_result"),
    }
    if extra:
        basis.update(extra)
    return basis


def _item(label: str, rationale: str, cohort: Optional[Dict[str, Any]],
          horizon: str, treatment: str, confidence: str, *,
          source: str, extra_basis: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "label": label,
        "rationale": rationale,
        "evidence_basis": _basis(cohort, horizon, source, extra_basis),
        "maturity_status": _status(cohort, horizon),
        "confidence": confidence,
        "affected_cohort": (cohort or {}).get("id"),
        "suggested_dashboard_treatment": treatment,
        "research_only": True,
        "promote_to_signal": False,
    }


def _is_weak(cohort: Optional[Dict[str, Any]], horizon: str) -> bool:
    result = _result(cohort)
    mean = _mean(cohort, horizon)
    return result in {"weak", "no edge"} or (mean is not None and mean < 0)


def _is_promising(cohort: Optional[Dict[str, Any]], horizon: str) -> bool:
    return _result(cohort) == "promising" and _matured(cohort, horizon) >= MIN_MATURED_TO_EVALUATE


def _append_if(items: List[Dict[str, Any]], item: Optional[Dict[str, Any]]) -> None:
    if item:
        items.append(item)


def _not_focus_ids(items: Iterable[Dict[str, Any]]) -> set[str]:
    return {str(i.get("affected_cohort")) for i in items if i.get("affected_cohort")}


def build_policy(cohort_report: Dict[str, Any],
                 now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or _utcnow()
    horizon = str(cohort_report.get("primary_horizon") or "10d")
    cohorts = _cohort_index(cohort_report)
    source = str(COHORT_ATTRIBUTION_REL)

    broad = _find(cohorts, "broad_scanner")
    reset = _find(cohorts, "extension_reset_or_watch_for_entry")
    profitable = _find(cohorts, "profitability_profitable")
    unprofitable = _find(cohorts, "profitability_unprofitable")
    mid_cap = _find(cohorts, "market_cap_mid", "market_cap_bucket_mid_cap")
    extended = _find(cohorts, "extension_extended")
    rs_momentum = _find(cohorts, "rs_momentum_leaders")
    technology = _find(cohorts, "sector_technology")
    small_micro = _find(cohorts, "market_cap_small_micro")
    swing = _find(cohorts, "program_swing")
    repeat = _find(cohorts, "sequence_repeat")
    non_common = _find(cohorts, "security_non_common")
    degraded_source = _find(cohorts, "source_degraded_source_excluded_ticker")
    hc = _find(cohorts, "high_conviction_alpha")
    eo = _find(cohorts, "emerging_outlier_watch")
    long_term = _find(cohorts, "program_long_term")

    focus_now: List[Dict[str, Any]] = []
    if _is_promising(reset, horizon):
        focus_now.append(_item(
            "Reset/watch-for-entry candidates",
            "This cohort is currently better than extended entries, so it deserves primary operator attention without changing selection rules.",
            reset, horizon, "prioritize_review", _confidence(_status(reset, horizon), positive_claim=True),
            source=source))
    if _is_promising(mid_cap, horizon):
        focus_now.append(_item(
            "Mid-cap / known-cap candidates",
            "The mid-cap cohort is currently promising, but it is still evidence for focus only, not alpha proof.",
            mid_cap, horizon, "prioritize_review", _confidence(_status(mid_cap, horizon), positive_claim=True),
            source=source))
    prof_mean = _mean(profitable, horizon)
    unprof_mean = _mean(unprofitable, horizon)
    if profitable and prof_mean is not None and unprof_mean is not None and prof_mean > unprof_mean:
        focus_now.append(_item(
            "Profitable quality candidates",
            "Profitable names are less negative than unprofitable names; emphasize quality review while keeping alpha_proven=false.",
            profitable, horizon, "prioritize_quality_review",
            _confidence(_status(profitable, horizon), positive_claim=True),
            source=source,
            extra_basis={"comparison_cohort_id": "profitability_unprofitable",
                         "comparison_mean_return_pct": unprof_mean}))

    use_caution: List[Dict[str, Any]] = []
    for cohort, label, rationale, treatment in (
        (extended, "Extended names",
         "Extended entries are a major weak-return cohort and should require stronger manual review.",
         "de_emphasize_and_require_manual_review"),
        (rs_momentum, "Extreme RS momentum",
         "RS momentum leaders are weak in the repaired forward evidence and appear vulnerable to reversal.",
         "de_emphasize_and_require_manual_review"),
        (unprofitable, "Unprofitable / low-quality names",
         "Unprofitable names are substantially weaker than profitable names in current attribution.",
         "de_emphasize_and_require_manual_review"),
        (technology, "Technology cohort",
         "Technology is one of the largest sector drags in the current attribution.",
         "de_emphasize_if_drag_persists"),
        (small_micro, "Small/micro-cap names",
         "Small/micro caps are weak and more fragile in the repaired forward evidence.",
         "de_emphasize_and_require_manual_review"),
        (swing, "Broad Swing candidates",
         "Swing Research broad candidates are a major contributor to current weakness.",
         "de_emphasize_broad_repeats"),
    ):
        if cohort and _is_weak(cohort, horizon):
            use_caution.append(_item(
                label, rationale, cohort, horizon, treatment,
                _confidence(_status(cohort, horizon)), source=source))

    discovery_only: List[Dict[str, Any]] = []
    if broad and _is_weak(broad, horizon):
        discovery_only.append(_item(
            "Broad scanner",
            "Broad scanner evidence remains weak/no edge; keep it as discovery inventory, not operator priority.",
            broad, horizon, "monitor_only_discovery",
            _confidence(_status(broad, horizon)), source=source))
    for cohort, label, rationale in (
        (repeat, "Broad scanner repeat candidates",
         "Repeat candidates are the largest explanatory drag and should be treated as monitoring inventory unless separately qualified."),
        (non_common, "Non-common securities",
         "Warrants, units, rights, and other non-common formats should remain visible for audit but outside standard priority review."),
        (degraded_source, "Stale or degraded-source candidates",
         "Degraded-source candidates are useful for diagnostics but should not be elevated by presentation."),
    ):
        if cohort:
            discovery_only.append(_item(
                label, rationale, cohort, horizon, "monitor_only_discovery",
                _confidence(_status(cohort, horizon)), source=source))

    do_not_conclude: List[Dict[str, Any]] = []
    for cohort, label in ((hc, "High-Conviction Alpha"), (eo, "Emerging Outlier Watch")):
        if _matured(cohort, horizon) < MIN_MATURED_TO_EVALUATE:
            do_not_conclude.append(_item(
                label,
                f"{label} has fewer than {MIN_MATURED_TO_EVALUATE} matured {horizon} primary samples; too early to evaluate.",
                cohort, horizon, "show_immature_badge",
                "VERY_LOW", source=source))

    if long_term:
        long_term_counts = {
            h: _matured(long_term, h)
            for h in LONG_TERM_PRIMARY_HORIZONS
        }
        if max(long_term_counts.values() or [0]) < MIN_MATURED_TO_EVALUATE:
            long_term_item = _item(
                "Long-Term Research",
                "Long-Term primary horizons are not mature enough; short-horizon diagnostics are not a Long-Term conclusion.",
                long_term, horizon, "show_immature_long_horizon_badge",
                "VERY_LOW", source=source,
                extra_basis={
                    "long_term_primary_horizon_counts": long_term_counts,
                    "long_horizon_sample_status": "TOO_EARLY",
                })
            long_term_item["maturity_status"] = "LONG_HORIZON_TOO_EARLY"
            do_not_conclude.append(long_term_item)

    focus_ids = _not_focus_ids(focus_now)
    broad_in_focus = bool(broad and str(broad.get("id")) in focus_ids)

    dashboard_summary = {
        "focus_now": focus_now[:3],
        "use_caution": use_caution[:3],
        "discovery_only": discovery_only[:3],
        "do_not_conclude_yet": do_not_conclude[:3],
        "immature_reminder": (
            "; ".join(str(i["rationale"]).rstrip(".")
                      for i in do_not_conclude[:2])
            + ("." if do_not_conclude[:2] else "")
        ) or None,
        "policy_note": "Presentation priority only; no candidates are removed, rescored, reranked, or promoted to signals.",
    }
    return {
        "kind": "research_operating_policy",
        "version": VERSION,
        "generated_at": now.isoformat(),
        "research_only": True,
        "promote_to_signal": False,
        "alpha_proven": cohort_report.get("alpha_proven"),
        "primary_horizon": horizon,
        "source_artifacts": {
            "cohort_attribution": str(COHORT_ATTRIBUTION_REL),
        },
        "source_timestamps": {
            "cohort_attribution_generated_at": cohort_report.get("generated_at"),
        },
        "policy_groups": {
            "focus_now": focus_now,
            "use_caution": use_caution,
            "discovery_only": discovery_only,
            "do_not_conclude_yet": do_not_conclude,
        },
        "dashboard_summary": dashboard_summary,
        "non_mutation_statement": (
            "This policy changes only presentation guidance and operator focus. "
            "It does not remove candidates, alter ranking, change scores, or change scanner/routing/filter behavior."
        ),
        "guardrails": {
            "no_scanner_logic_change": True,
            "no_selection_change": True,
            "no_score_or_ranking_change": True,
            "no_gate_threshold_or_factor_weight_change": True,
            "no_high_conviction_rule_change": True,
            "no_emerging_outlier_rule_change": True,
            "no_program_verdict_threshold_change": True,
            "research_only": True,
            "cache_only_inputs": True,
            "promote_to_signal": False,
            "broad_scanner_not_focus_now": not broad_in_focus,
        },
        "caveats": [
            "Policy groups are presentation guidance, not scanner tuning.",
            "Positive focus labels are not alpha claims while alpha_proven=false.",
            "Immature HC/EO cohorts must not be judged until their matured samples clear the evidence floor.",
        ],
    }


def build_report(root: Optional[Path] = None,
                 now: Optional[datetime] = None) -> Dict[str, Any]:
    root = Path(root) if root else REPO_ROOT
    cohort_report = _load_json(root / COHORT_ATTRIBUTION_REL)
    if cohort_report is None:
        return {
            "kind": "research_operating_policy",
            "version": VERSION,
            "generated_at": (now or _utcnow()).isoformat(),
            "research_only": True,
            "promote_to_signal": False,
            "fallback": "MISSING_ARTIFACT",
            "missing_artifact": str(COHORT_ATTRIBUTION_REL),
            "policy_groups": {
                "focus_now": [],
                "use_caution": [],
                "discovery_only": [],
                "do_not_conclude_yet": [],
            },
            "guardrails": {
                "no_scanner_logic_change": True,
                "no_selection_change": True,
                "no_score_or_ranking_change": True,
                "no_gate_threshold_or_factor_weight_change": True,
                "no_high_conviction_rule_change": True,
                "no_emerging_outlier_rule_change": True,
                "no_program_verdict_threshold_change": True,
                "research_only": True,
                "cache_only_inputs": True,
                "promote_to_signal": False,
                "broad_scanner_not_focus_now": True,
            },
        }
    return build_policy(cohort_report, now=now)


def write_artifacts(report: Dict[str, Any], root: Optional[Path] = None) -> Dict[str, str]:
    root = Path(root) if root else REPO_ROOT
    path = root / OUT_JSON_REL
    _write_json(path, report)
    return {"json": str(path)}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build research operating policy sidecar.")
    parser.add_argument("--root", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--json", action="store_true", help="Print the policy JSON to stdout")
    args = parser.parse_args(argv)
    root = Path(args.root) if args.root else REPO_ROOT
    report = build_report(root)
    paths = write_artifacts(report, root)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"wrote {paths['json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

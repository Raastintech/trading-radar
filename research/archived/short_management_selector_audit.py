from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from breakout_timing_engine import DEFAULT_DB_PATH, DEFAULT_HORIZON_DAYS
from short_exit_management_experiment import (
    BASELINE_RECORDED,
    DEFAULT_POLICIES,
    PARTIAL_050R_BE,
    PARTIAL_050R_BE_TIME5D,
    ShortExitCandidate,
    ShortExitManagementExperiment,
)

MODEL_VERSION = "short_management_selector_audit_v1"
DEFAULT_SELECTOR_STATES = ("IMMINENT",)
DEFAULT_SELECTOR_COHORTS = ("NEAR_MISS",)
DEFAULT_SELECTOR_REGIME_STATUSES = ("BULL",)
DEFAULT_SELECTOR_VOLATILITIES = ("NORMAL",)
DEFAULT_SELECTOR_VIX_BUCKETS = ("NORMAL",)
DEFAULT_SHADOW_POLICY_NAME = PARTIAL_050R_BE_TIME5D.name
DEFAULT_POLICY_CANDIDATES = (
    PARTIAL_050R_BE.name,
    PARTIAL_050R_BE_TIME5D.name,
)


class ShortManagementSelectorAudit:
    """Read-only selector audit for narrow short management slices."""

    def __init__(
        self,
        db_path: str = DEFAULT_DB_PATH,
        *,
        experiment: Optional[ShortExitManagementExperiment] = None,
        data_feed: Optional[Any] = None,
    ) -> None:
        self.db_path = db_path
        self.experiment = experiment or ShortExitManagementExperiment(db_path=db_path, data_feed=data_feed)

    def run_audit(
        self,
        *,
        horizon_days: int = DEFAULT_HORIZON_DAYS,
        decision_reason: str = "risk_reward_too_low",
        states: Sequence[str] = DEFAULT_SELECTOR_STATES,
        selector_cohorts: Sequence[str] = DEFAULT_SELECTOR_COHORTS,
        selector_regime_statuses: Sequence[str] = DEFAULT_SELECTOR_REGIME_STATUSES,
        selector_volatilities: Sequence[str] = DEFAULT_SELECTOR_VOLATILITIES,
        selector_vix_buckets: Sequence[str] = DEFAULT_SELECTOR_VIX_BUCKETS,
        policy_names: Sequence[str] = DEFAULT_POLICY_CANDIDATES,
        min_target_n: int = 4,
        min_target_delta: float = 0.15,
        min_edge_gap: float = 0.15,
        example_limit: int = 10,
    ) -> Dict[str, Any]:
        state_filter = [str(v or "").upper().strip() for v in states if str(v or "").strip()]
        selector = {
            "cohorts": [str(v or "").upper().strip() for v in selector_cohorts if str(v or "").strip()],
            "regime_statuses": [str(v or "").upper().strip() for v in selector_regime_statuses if str(v or "").strip()],
            "volatilities": [str(v or "").upper().strip() for v in selector_volatilities if str(v or "").strip()],
            "vix_buckets": [str(v or "").upper().strip() for v in selector_vix_buckets if str(v or "").strip()],
        }
        candidate_policies = [name for name in policy_names if name in DEFAULT_POLICIES and name != BASELINE_RECORDED.name]
        if not candidate_policies:
            raise ValueError("No valid candidate policies selected")

        all_candidates = self.experiment.load_candidates(
            horizon_days=int(horizon_days),
            decision_reason=str(decision_reason or "").lower(),
            states=state_filter,
            cohorts=(),
            regime_statuses=(),
            volatilities=(),
            vix_buckets=(),
            limit=None,
        )

        matched_candidates = [candidate for candidate in all_candidates if self._matches_selector(candidate, selector)]
        complement_candidates = [
            candidate for candidate in all_candidates if not self._matches_selector(candidate, selector)
        ]

        policy_set = [BASELINE_RECORDED.name, *candidate_policies]
        target_report = self._run_subset(
            candidates=matched_candidates,
            horizon_days=int(horizon_days),
            policy_names=policy_set,
        )
        complement_report = self._run_subset(
            candidates=complement_candidates,
            horizon_days=int(horizon_days),
            policy_names=policy_set,
        )

        policy_comparison = []
        best_policy = None
        best_score = None
        for policy_name in candidate_policies:
            target_metrics = target_report["policies"].get(policy_name)
            complement_metrics = complement_report["policies"].get(policy_name)
            target_delta = self._safe_float((target_metrics or {}).get("delta_vs_baseline_avg_r"))
            complement_delta = self._safe_float((complement_metrics or {}).get("delta_vs_baseline_avg_r"))
            target_avg = self._safe_float((target_metrics or {}).get("avg_realized_r"))
            complement_avg = self._safe_float((complement_metrics or {}).get("avg_realized_r"))
            edge_gap = (
                round(target_delta - complement_delta, 4)
                if target_delta is not None and complement_delta is not None else None
            )
            qualifies = (
                len(matched_candidates) >= int(min_target_n)
                and target_delta is not None
                and edge_gap is not None
                and target_delta >= float(min_target_delta)
                and edge_gap >= float(min_edge_gap)
            )
            payload = {
                "policy_name": policy_name,
                "policy_label": DEFAULT_POLICIES[policy_name].label,
                "target_n": len(matched_candidates),
                "target_avg_r": target_avg,
                "target_delta_vs_baseline": target_delta,
                "complement_n": len(complement_candidates),
                "complement_avg_r": complement_avg,
                "complement_delta_vs_baseline": complement_delta,
                "edge_gap": edge_gap,
                "qualifies_for_shadow": qualifies,
            }
            policy_comparison.append(payload)
            score = edge_gap if qualifies and edge_gap is not None else None
            if score is not None and (best_score is None or score > best_score):
                best_score = score
                best_policy = payload

        if best_policy is not None:
            shadow_candidate = {
                "ready": True,
                "reason": (
                    f"target_delta={self._fmt_num(best_policy['target_delta_vs_baseline'])} "
                    f"edge_gap={self._fmt_num(best_policy['edge_gap'])} "
                    f"target_n={best_policy['target_n']}"
                ),
                "policy_name": best_policy["policy_name"],
                "policy_label": best_policy["policy_label"],
                "selector": selector,
                "shadow_rule_preview": {
                    "strategy": "SHORT",
                    "candidate_state": state_filter,
                    "decision_cohort": selector["cohorts"],
                    "regime_status": selector["regime_statuses"],
                    "regime_volatility": selector["volatilities"],
                    "regime_vix": selector["vix_buckets"],
                    "management_policy": best_policy["policy_name"],
                    "mode": "shadow_only",
                },
            }
        else:
            shadow_candidate = {
                "ready": False,
                "reason": (
                    f"no policy met selector thresholds "
                    f"(min_target_n={int(min_target_n)} min_target_delta={float(min_target_delta)} "
                    f"min_edge_gap={float(min_edge_gap)})"
                ),
                "policy_name": None,
                "policy_label": None,
                "selector": selector,
                "shadow_rule_preview": None,
            }

        return {
            "model_version": MODEL_VERSION,
            "horizon_days": int(horizon_days),
            "decision_reason": str(decision_reason or "").lower(),
            "states": state_filter,
            "selector": selector,
            "thresholds": {
                "min_target_n": int(min_target_n),
                "min_target_delta": float(min_target_delta),
                "min_edge_gap": float(min_edge_gap),
            },
            "total_n": len(all_candidates),
            "target_slice_n": len(matched_candidates),
            "complement_n": len(complement_candidates),
            "target_report": target_report,
            "complement_report": complement_report,
            "policy_comparison": policy_comparison,
            "shadow_candidate": shadow_candidate,
            "examples": {
                "target_candidates": [asdict(candidate) for candidate in matched_candidates[: max(0, int(example_limit))]],
                "complement_head": [asdict(candidate) for candidate in complement_candidates[: max(0, int(example_limit))]],
            },
        }

    def save_report(self, report: Dict[str, Any]) -> Dict[str, str]:
        os.makedirs("logs", exist_ok=True)
        date_tag = datetime.now(timezone.utc).date().isoformat()
        horizon = int(report.get("horizon_days") or DEFAULT_HORIZON_DAYS)
        state_part = "-".join(report.get("states") or ["ALL"]).lower()
        selector = report.get("selector") or {}
        cohort_part = "-".join(selector.get("cohorts") or ["ALL"]).lower()
        regime_part = "-".join(selector.get("regime_statuses") or ["ALL"]).lower()
        vol_part = "-".join(selector.get("volatilities") or ["ALL"]).lower()
        vix_part = "-".join(selector.get("vix_buckets") or ["ALL"]).lower()
        stem = (
            f"short_management_selector_audit_{date_tag}_{state_part}_{cohort_part}_"
            f"{regime_part}_{vol_part}_{vix_part}_{horizon}d"
        )
        text_path = os.path.join("logs", f"{stem}.txt")
        json_path = os.path.join("logs", f"{stem}.json")
        with open(text_path, "w", encoding="utf-8") as handle:
            handle.write(self.format_report(report))
            handle.write("\n")
        with open(json_path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, sort_keys=True, default=str)
        return {"text": text_path, "json": json_path}

    def format_report(self, report: Dict[str, Any]) -> str:
        selector = report.get("selector") or {}
        shadow = report.get("shadow_candidate") or {}
        lines = [
            "SHORT MANAGEMENT SELECTOR AUDIT",
            f"  model_version: {report.get('model_version')}",
            f"  horizon_days: {report.get('horizon_days')}",
            f"  decision_reason: {report.get('decision_reason')}",
            f"  states: {', '.join(report.get('states') or ['ALL'])}",
            f"  selector cohorts: {', '.join(selector.get('cohorts') or ['ALL'])}",
            f"  selector regime_statuses: {', '.join(selector.get('regime_statuses') or ['ALL'])}",
            f"  selector volatilities: {', '.join(selector.get('volatilities') or ['ALL'])}",
            f"  selector vix_buckets: {', '.join(selector.get('vix_buckets') or ['ALL'])}",
            f"  total_n: {report.get('total_n')}  target_slice_n: {report.get('target_slice_n')}  complement_n: {report.get('complement_n')}",
            "",
            f"{'Policy':<24} {'TargetΔ':>8} {'TargetR':>8} {'CompΔ':>8} {'CompR':>8} {'Gap':>8} {'Shadow':>8}",
        ]
        for row in report.get("policy_comparison") or []:
            lines.append(
                f"{row['policy_label']:<24} "
                f"{self._fmt_num(row.get('target_delta_vs_baseline')):>8} "
                f"{self._fmt_num(row.get('target_avg_r')):>8} "
                f"{self._fmt_num(row.get('complement_delta_vs_baseline')):>8} "
                f"{self._fmt_num(row.get('complement_avg_r')):>8} "
                f"{self._fmt_num(row.get('edge_gap')):>8} "
                f"{'YES' if row.get('qualifies_for_shadow') else 'NO':>8}"
            )

        lines.extend(
            [
                "",
                f"  shadow_ready: {bool(shadow.get('ready'))}",
                f"  shadow_reason: {shadow.get('reason')}",
            ]
        )
        if shadow.get("shadow_rule_preview"):
            rule = shadow["shadow_rule_preview"]
            lines.append(
                f"  shadow_rule_preview: strategy={rule.get('strategy')} "
                f"state={','.join(rule.get('candidate_state') or [])} "
                f"cohort={','.join(rule.get('decision_cohort') or [])} "
                f"regime={','.join(rule.get('regime_status') or [])} "
                f"vol={','.join(rule.get('regime_volatility') or [])} "
                f"vix={','.join(rule.get('regime_vix') or [])} "
                f"policy={rule.get('management_policy')} mode={rule.get('mode')}"
            )
        return "\n".join(lines)

    def _run_subset(
        self,
        *,
        candidates: Sequence[ShortExitCandidate],
        horizon_days: int,
        policy_names: Sequence[str],
    ) -> Dict[str, Any]:
        bars_cache: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        results_by_policy: Dict[str, List[Any]] = {name: [] for name in policy_names}
        for candidate in candidates:
            bars = self.experiment._fetch_forward_bars(
                ticker=candidate.ticker,
                candidate_date=candidate.candidate_date,
                horizon_days=int(horizon_days),
                cache=bars_cache,
            )
            for policy_name in policy_names:
                policy = DEFAULT_POLICIES[policy_name]
                results_by_policy[policy_name].append(
                    self.experiment._simulate_policy(
                        candidate=candidate,
                        policy=policy,
                        bars=bars[: int(horizon_days)],
                    )
                )

        baseline_rows = results_by_policy.get(BASELINE_RECORDED.name, [])
        baseline_by_candidate = {row.candidate_id: row for row in baseline_rows}
        policies = {
            policy_name: self.experiment._summarize_policy(
                policy=DEFAULT_POLICIES[policy_name],
                rows=results_by_policy[policy_name],
                baseline_by_candidate=baseline_by_candidate,
            )
            for policy_name in policy_names
        }
        return {
            "n": len(candidates),
            "policies": policies,
        }

    @staticmethod
    def _matches_selector(candidate: ShortExitCandidate, selector: Dict[str, List[str]]) -> bool:
        checks = (
            ("decision_cohort", selector.get("cohorts") or []),
            ("regime_status", selector.get("regime_statuses") or []),
            ("regime_volatility", selector.get("volatilities") or []),
            ("regime_vix", selector.get("vix_buckets") or []),
        )
        for attr, allowed in checks:
            if allowed and str(getattr(candidate, attr) or "UNKNOWN").upper() not in allowed:
                return False
        return True

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        try:
            if value is None:
                return None
            return float(value)
        except Exception:
            return None

    @staticmethod
    def _fmt_num(value: Any) -> str:
        if value is None:
            return "—"
        try:
            return f"{float(value):.3f}"
        except Exception:
            return str(value)


def _parse_csv(raw: str) -> List[str]:
    return [part.strip() for part in str(raw or "").split(",") if part.strip()]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only short selector audit for shadow management research.")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    parser.add_argument("--horizon-days", type=int, default=DEFAULT_HORIZON_DAYS)
    parser.add_argument("--reason", default="risk_reward_too_low")
    parser.add_argument("--states", default="IMMINENT")
    parser.add_argument("--selector-cohorts", default="NEAR_MISS")
    parser.add_argument("--selector-regime-statuses", default="BULL")
    parser.add_argument("--selector-volatilities", default="NORMAL")
    parser.add_argument("--selector-vix-buckets", default="NORMAL")
    parser.add_argument(
        "--policies",
        default=",".join(DEFAULT_POLICY_CANDIDATES),
        help=f"Comma-separated policies: {', '.join(DEFAULT_POLICY_CANDIDATES)}",
    )
    parser.add_argument("--min-target-n", type=int, default=4)
    parser.add_argument("--min-target-delta", type=float, default=0.15)
    parser.add_argument("--min-edge-gap", type=float, default=0.15)
    parser.add_argument("--examples", type=int, default=10)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--save", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    audit = ShortManagementSelectorAudit(db_path=args.db_path)
    report = audit.run_audit(
        horizon_days=int(args.horizon_days),
        decision_reason=str(args.reason or "").lower(),
        states=_parse_csv(args.states),
        selector_cohorts=_parse_csv(args.selector_cohorts),
        selector_regime_statuses=_parse_csv(args.selector_regime_statuses),
        selector_volatilities=_parse_csv(args.selector_volatilities),
        selector_vix_buckets=_parse_csv(args.selector_vix_buckets),
        policy_names=_parse_csv(args.policies),
        min_target_n=int(args.min_target_n),
        min_target_delta=float(args.min_target_delta),
        min_edge_gap=float(args.min_edge_gap),
        example_limit=int(args.examples),
    )
    if args.save:
        paths = audit.save_report(report)
        print(f"Saved: {paths['text']}  {paths['json']}")
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    else:
        print(audit.format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

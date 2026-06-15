from __future__ import annotations

import argparse
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from statistics import median
from typing import Any, Dict, List, Optional, Sequence, Tuple

from alpaca_data import AlpacaDataFeed
from breakout_timing_engine import DEFAULT_DB_PATH, DEFAULT_HORIZON_DAYS

MODEL_VERSION = "short_exit_management_experiment_v1"
DEFAULT_REASON = "risk_reward_too_low"
DEFAULT_STATES = ("IMMINENT",)


@dataclass(frozen=True)
class ShortExitCandidate:
    candidate_id: int
    decision_id: int
    candidate_date: str
    ticker: str
    candidate_state: str
    decision_cohort: str
    decision_reason: str
    scanner_rr: float
    entry_price: float
    stop_loss: float
    target_price: float
    regime_status: str
    regime_volatility: str
    regime_vix: str


@dataclass(frozen=True)
class ExitPolicy:
    name: str
    label: str
    partial_r: Optional[float] = None
    partial_fraction: float = 0.0
    move_stop_to_entry_after_partial: bool = False
    time_stop_days: Optional[int] = None


BASELINE_RECORDED = ExitPolicy(
    name="baseline_recorded",
    label="Baseline Recorded",
)

TIME_STOP_5D = ExitPolicy(
    name="time_stop_5d",
    label="Time Stop 5D",
    time_stop_days=5,
)

PARTIAL_050R_BE = ExitPolicy(
    name="partial_050r_be",
    label="Partial 0.5R + BE",
    partial_r=0.5,
    partial_fraction=0.5,
    move_stop_to_entry_after_partial=True,
)

PARTIAL_075R_BE = ExitPolicy(
    name="partial_075r_be",
    label="Partial 0.75R + BE",
    partial_r=0.75,
    partial_fraction=0.5,
    move_stop_to_entry_after_partial=True,
)

PARTIAL_100R_BE = ExitPolicy(
    name="partial_100r_be",
    label="Partial 1.0R + BE",
    partial_r=1.0,
    partial_fraction=0.5,
    move_stop_to_entry_after_partial=True,
)

PARTIAL_050R_BE_TIME5D = ExitPolicy(
    name="partial_050r_be_time5d",
    label="Partial 0.5R + BE + T5",
    partial_r=0.5,
    partial_fraction=0.5,
    move_stop_to_entry_after_partial=True,
    time_stop_days=5,
)

PARTIAL_075R_BE_TIME5D = ExitPolicy(
    name="partial_075r_be_time5d",
    label="Partial 0.75R + BE + T5",
    partial_r=0.75,
    partial_fraction=0.5,
    move_stop_to_entry_after_partial=True,
    time_stop_days=5,
)

PARTIAL_100R_BE_TIME5D = ExitPolicy(
    name="partial_100r_be_time5d",
    label="Partial 1.0R + BE + T5",
    partial_r=1.0,
    partial_fraction=0.5,
    move_stop_to_entry_after_partial=True,
    time_stop_days=5,
)

DEFAULT_POLICIES: Dict[str, ExitPolicy] = {
    BASELINE_RECORDED.name: BASELINE_RECORDED,
    TIME_STOP_5D.name: TIME_STOP_5D,
    PARTIAL_050R_BE.name: PARTIAL_050R_BE,
    PARTIAL_075R_BE.name: PARTIAL_075R_BE,
    PARTIAL_100R_BE.name: PARTIAL_100R_BE,
    PARTIAL_050R_BE_TIME5D.name: PARTIAL_050R_BE_TIME5D,
    PARTIAL_075R_BE_TIME5D.name: PARTIAL_075R_BE_TIME5D,
    PARTIAL_100R_BE_TIME5D.name: PARTIAL_100R_BE_TIME5D,
}


@dataclass
class ExitRunResult:
    policy_name: str
    candidate_id: int
    ticker: str
    candidate_date: str
    candidate_state: str
    decision_cohort: str
    regime_status: str
    regime_volatility: str
    regime_vix: str
    realized_r: float
    target_hit: bool
    stop_hit: bool
    breakeven_stop_hit: bool
    partial_taken: bool
    time_stop_exit: bool
    horizon_exit: bool
    days_to_exit: Optional[int]
    days_to_partial: Optional[int]
    realized_r_from_partials: float
    realized_r_from_runner: float
    mfe_r: float
    mae_r: float


class ShortExitManagementExperiment:
    """Read-only short exit management research on labeled RR rejects."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH, *, data_feed: Optional[Any] = None) -> None:
        self.db_path = db_path
        self.data_feed = data_feed or AlpacaDataFeed()

    def load_candidates(
        self,
        *,
        horizon_days: int,
        decision_reason: str,
        states: Sequence[str],
        cohorts: Sequence[str],
        regime_statuses: Sequence[str],
        volatilities: Sequence[str],
        vix_buckets: Sequence[str],
        limit: Optional[int] = None,
    ) -> List[ShortExitCandidate]:
        state_filter = [str(v or "").upper().strip() for v in states if str(v or "").strip()]
        cohort_filter = [str(v or "").upper().strip() for v in cohorts if str(v or "").strip()]
        regime_filter = [str(v or "").upper().strip() for v in regime_statuses if str(v or "").strip()]
        volatility_filter = [str(v or "").upper().strip() for v in volatilities if str(v or "").strip()]
        vix_filter = [str(v or "").upper().strip() for v in vix_buckets if str(v or "").strip()]

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        clauses = [
            "o.horizon_days = ?",
            "o.data_available = 1",
            "LOWER(COALESCE(c.decision_reason, '')) = ?",
            "d.entry_price IS NOT NULL",
            "d.stop_loss IS NOT NULL",
            "d.target_price IS NOT NULL",
            "d.stop_loss > d.entry_price",
        ]
        params: List[Any] = [int(horizon_days), str(decision_reason or DEFAULT_REASON).lower()]

        for values, expr in (
            (state_filter, "UPPER(COALESCE(c.candidate_state, 'UNKNOWN'))"),
            (cohort_filter, "UPPER(COALESCE(c.decision_cohort, 'UNSEEDED'))"),
            (regime_filter, "UPPER(COALESCE(c.regime_status, 'UNKNOWN'))"),
            (volatility_filter, "UPPER(COALESCE(c.regime_volatility, 'UNKNOWN'))"),
            (vix_filter, "UPPER(COALESCE(c.regime_vix, 'UNKNOWN'))"),
        ):
            if values:
                placeholders = ",".join("?" for _ in values)
                clauses.append(f"{expr} IN ({placeholders})")
                params.extend(values)

        sql = f"""
            SELECT
                c.id AS candidate_id,
                c.decision_id,
                c.candidate_date,
                c.ticker,
                c.candidate_state,
                c.decision_cohort,
                c.decision_reason,
                c.scanner_rr,
                d.entry_price,
                d.stop_loss,
                d.target_price,
                c.regime_status,
                c.regime_volatility,
                c.regime_vix
            FROM bte_short_candidates c
            JOIN bte_short_outcomes o
              ON o.candidate_id = c.id
            JOIN decisions d
              ON d.id = c.decision_id
            WHERE {" AND ".join(clauses)}
            ORDER BY c.candidate_date ASC, c.id ASC
        """
        if limit is not None and int(limit) > 0:
            sql += f" LIMIT {int(limit)}"

        cur.execute(sql, params)
        rows = cur.fetchall()
        conn.close()

        return [
            ShortExitCandidate(
                candidate_id=int(row["candidate_id"]),
                decision_id=int(row["decision_id"]),
                candidate_date=str(row["candidate_date"] or ""),
                ticker=str(row["ticker"] or "").upper(),
                candidate_state=str(row["candidate_state"] or "UNKNOWN").upper(),
                decision_cohort=str(row["decision_cohort"] or "UNSEEDED").upper(),
                decision_reason=str(row["decision_reason"] or "").lower(),
                scanner_rr=float(row["scanner_rr"] or 0.0),
                entry_price=float(row["entry_price"]),
                stop_loss=float(row["stop_loss"]),
                target_price=float(row["target_price"]),
                regime_status=str(row["regime_status"] or "UNKNOWN").upper(),
                regime_volatility=str(row["regime_volatility"] or "UNKNOWN").upper(),
                regime_vix=str(row["regime_vix"] or "UNKNOWN").upper(),
            )
            for row in rows
        ]

    def run_experiment(
        self,
        *,
        horizon_days: int = DEFAULT_HORIZON_DAYS,
        decision_reason: str = DEFAULT_REASON,
        states: Sequence[str] = DEFAULT_STATES,
        cohorts: Sequence[str] = (),
        regime_statuses: Sequence[str] = (),
        volatilities: Sequence[str] = (),
        vix_buckets: Sequence[str] = (),
        policy_names: Sequence[str] = (),
        limit: Optional[int] = None,
        example_limit: int = 10,
    ) -> Dict[str, Any]:
        candidates = self.load_candidates(
            horizon_days=int(horizon_days),
            decision_reason=str(decision_reason or DEFAULT_REASON).lower(),
            states=states,
            cohorts=cohorts,
            regime_statuses=regime_statuses,
            volatilities=volatilities,
            vix_buckets=vix_buckets,
            limit=limit,
        )
        policies = [
            DEFAULT_POLICIES[name]
            for name in (policy_names or DEFAULT_POLICIES.keys())
            if name in DEFAULT_POLICIES
        ]
        if not policies:
            raise ValueError("No valid exit policies selected")

        bars_cache: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        results_by_policy: Dict[str, List[ExitRunResult]] = {policy.name: [] for policy in policies}

        for candidate in candidates:
            bars = self._fetch_forward_bars(
                ticker=candidate.ticker,
                candidate_date=candidate.candidate_date,
                horizon_days=int(horizon_days),
                cache=bars_cache,
            )
            for policy in policies:
                results_by_policy[policy.name].append(
                    self._simulate_policy(candidate=candidate, policy=policy, bars=bars[: int(horizon_days)])
                )

        baseline_rows = results_by_policy.get(BASELINE_RECORDED.name, [])
        baseline_by_candidate = {row.candidate_id: row for row in baseline_rows}
        summaries = [
            self._summarize_policy(
                policy=policy,
                rows=results_by_policy[policy.name],
                baseline_by_candidate=baseline_by_candidate,
            )
            for policy in policies
        ]
        return {
            "model_version": MODEL_VERSION,
            "horizon_days": int(horizon_days),
            "decision_reason": str(decision_reason or DEFAULT_REASON).lower(),
            "states": [str(v or "").upper() for v in states if str(v or "").strip()],
            "cohorts": [str(v or "").upper() for v in cohorts if str(v or "").strip()],
            "regime_statuses": [str(v or "").upper() for v in regime_statuses if str(v or "").strip()],
            "volatilities": [str(v or "").upper() for v in volatilities if str(v or "").strip()],
            "vix_buckets": [str(v or "").upper() for v in vix_buckets if str(v or "").strip()],
            "candidate_count": len(candidates),
            "policies": summaries,
            "by_decision_cohort": self._segment_summary(
                rows_by_policy=results_by_policy,
                baseline_by_candidate=baseline_by_candidate,
                key="decision_cohort",
                ordering=["NEAR_MISS", "REJECT", "APPROVED", "UNSEEDED"],
            ),
            "by_regime_status": self._segment_summary(
                rows_by_policy=results_by_policy,
                baseline_by_candidate=baseline_by_candidate,
                key="regime_status",
                ordering=["BULL", "SIDEWAYS", "BEAR", "CORRECTION", "UNKNOWN"],
            ),
            "by_volatility": self._segment_summary(
                rows_by_policy=results_by_policy,
                baseline_by_candidate=baseline_by_candidate,
                key="regime_volatility",
                ordering=["NORMAL", "ELEVATED", "HIGH", "UNKNOWN"],
            ),
            "examples": self._build_examples(
                baseline_by_candidate=baseline_by_candidate,
                policy_results=results_by_policy,
                example_limit=int(example_limit),
            ),
        }

    def save_report(self, report: Dict[str, Any]) -> Dict[str, str]:
        os.makedirs("logs", exist_ok=True)
        date_tag = datetime.now(timezone.utc).date().isoformat()
        horizon = int(report.get("horizon_days") or DEFAULT_HORIZON_DAYS)
        state_part = "-".join(report.get("states") or ["ALL"]).lower()
        cohort_part = "-".join(report.get("cohorts") or ["ALL"]).lower()
        regime_part = "-".join(report.get("regime_statuses") or ["ALL"]).lower()
        vol_part = "-".join(report.get("volatilities") or ["ALL"]).lower()
        vix_part = "-".join(report.get("vix_buckets") or ["ALL"]).lower()
        stem = (
            f"short_exit_management_experiment_{date_tag}_{state_part}_{cohort_part}_"
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
        lines = [
            "SHORT EXIT MANAGEMENT EXPERIMENT",
            f"  model_version: {report.get('model_version')}",
            f"  horizon_days: {report.get('horizon_days')}",
            f"  decision_reason: {report.get('decision_reason')}",
            f"  states: {', '.join(report.get('states') or ['ALL'])}",
            f"  cohorts: {', '.join(report.get('cohorts') or ['ALL'])}",
            f"  regime_statuses: {', '.join(report.get('regime_statuses') or ['ALL'])}",
            f"  volatilities: {', '.join(report.get('volatilities') or ['ALL'])}",
            f"  vix_buckets: {', '.join(report.get('vix_buckets') or ['ALL'])}",
            f"  candidate_count: {report.get('candidate_count')}",
            "",
            f"{'Policy':<28} {'N':>4} {'Part%':>7} {'Tgt%':>7} {'Stop%':>7} {'BE%':>6} {'TStop%':>8} {'AvgR':>7} {'ΔR':>7}",
        ]
        for row in report.get("policies", []):
            lines.append(
                f"{row['label']:<28} {row['n']:>4} "
                f"{self._fmt_pct(row.get('partial_taken_rate')):>7} "
                f"{self._fmt_pct(row.get('target_hit_rate')):>7} "
                f"{self._fmt_pct(row.get('stop_hit_rate')):>7} "
                f"{self._fmt_pct(row.get('breakeven_stop_rate')):>6} "
                f"{self._fmt_pct(row.get('time_stop_exit_rate')):>8} "
                f"{self._fmt_num(row.get('avg_realized_r')):>7} "
                f"{self._fmt_num(row.get('delta_vs_baseline_avg_r')):>7}"
            )

        for title, key in (
            ("By decision cohort", "by_decision_cohort"),
            ("By regime status", "by_regime_status"),
            ("By regime volatility", "by_volatility"),
        ):
            lines.append("")
            lines.append(title)
            segments = report.get(key) or []
            if not segments:
                lines.append("  none")
                continue
            for segment in segments:
                lines.append(f"  {segment['label']:<12} n={segment['n']}")
                for policy_name, policy_label in (
                    (BASELINE_RECORDED.name, BASELINE_RECORDED.label),
                    (TIME_STOP_5D.name, TIME_STOP_5D.label),
                    (PARTIAL_050R_BE.name, PARTIAL_050R_BE.label),
                    (PARTIAL_075R_BE.name, PARTIAL_075R_BE.label),
                    (PARTIAL_050R_BE_TIME5D.name, PARTIAL_050R_BE_TIME5D.label),
                    (PARTIAL_075R_BE_TIME5D.name, PARTIAL_075R_BE_TIME5D.label),
                ):
                    metrics = (segment.get("policies") or {}).get(policy_name)
                    if not metrics:
                        continue
                    lines.append(
                        f"    {policy_label:<24} "
                        f"AvgR={self._fmt_num(metrics.get('avg_realized_r'))} "
                        f"ΔR={self._fmt_num(metrics.get('delta_vs_baseline_avg_r'))} "
                        f"Part={self._fmt_pct(metrics.get('partial_taken_rate'))} "
                        f"TStop={self._fmt_pct(metrics.get('time_stop_exit_rate'))}"
                    )

        examples = report.get("examples", {})
        for title, key in (
            ("Top management improvements", "top_improvements"),
            ("Losers improved materially", "losses_improved"),
            ("Worse early exits", "worse_exits"),
        ):
            lines.append("")
            lines.append(title)
            rows = examples.get(key) or []
            if not rows:
                lines.append("  none")
                continue
            for row in rows:
                lines.append(
                    f"  {row['policy']:<24} {row['ticker']:<6} {row['candidate_date']} "
                    f"state={row['candidate_state']:<12} cohort={row['decision_cohort']:<10} "
                    f"baseR={self._fmt_num(row.get('baseline_realized_r'))} "
                    f"newR={self._fmt_num(row.get('policy_realized_r'))} "
                    f"ΔR={self._fmt_num(row.get('delta_realized_r'))} "
                    f"exit={row.get('exit_reason')}"
                )
        return "\n".join(lines)

    def _simulate_policy(
        self,
        *,
        candidate: ShortExitCandidate,
        policy: ExitPolicy,
        bars: Sequence[Dict[str, Any]],
    ) -> ExitRunResult:
        entry = float(candidate.entry_price)
        stop = float(candidate.stop_loss)
        target = float(candidate.target_price)
        risk = stop - entry
        if risk <= 0:
            return ExitRunResult(
                policy_name=policy.name,
                candidate_id=int(candidate.candidate_id),
                ticker=candidate.ticker,
                candidate_date=candidate.candidate_date,
                candidate_state=candidate.candidate_state,
                decision_cohort=candidate.decision_cohort,
                regime_status=candidate.regime_status,
                regime_volatility=candidate.regime_volatility,
                regime_vix=candidate.regime_vix,
                realized_r=0.0,
                target_hit=False,
                stop_hit=False,
                breakeven_stop_hit=False,
                partial_taken=False,
                time_stop_exit=False,
                horizon_exit=True,
                days_to_exit=None,
                days_to_partial=None,
                realized_r_from_partials=0.0,
                realized_r_from_runner=0.0,
                mfe_r=0.0,
                mae_r=0.0,
            )

        partial_price = None
        if policy.partial_r is not None and policy.partial_fraction > 0:
            partial_price = entry - (policy.partial_r * risk)

        remaining_fraction = 1.0
        realized_from_partials = 0.0
        realized_from_runner = 0.0
        active_stop = stop
        partial_taken = False
        days_to_partial = None
        target_hit = False
        stop_hit = False
        breakeven_stop_hit = False
        time_stop_exit = False
        horizon_exit = False
        days_to_exit = None
        mfe_r = 0.0
        mae_r = 0.0

        last_close = entry
        for idx, bar in enumerate(bars, start=1):
            high = float(bar["high"])
            low = float(bar["low"])
            close = float(bar["close"])
            last_close = close
            favorable_r = (entry - low) / risk
            adverse_r = (high - entry) / risk
            mfe_r = max(mfe_r, favorable_r)
            mae_r = max(mae_r, adverse_r)

            if high >= active_stop:
                stop_r = 0.0 if partial_taken and active_stop <= entry else -1.0
                realized_from_runner += remaining_fraction * stop_r
                stop_hit = not (partial_taken and active_stop <= entry)
                breakeven_stop_hit = partial_taken and active_stop <= entry
                days_to_exit = idx
                break

            if low <= target:
                if partial_price is not None and not partial_taken and low <= partial_price:
                    realized_from_partials += policy.partial_fraction * float(policy.partial_r)
                    remaining_fraction -= policy.partial_fraction
                    partial_taken = True
                    days_to_partial = idx
                target_r = (entry - target) / risk
                realized_from_runner += remaining_fraction * target_r
                target_hit = True
                days_to_exit = idx
                remaining_fraction = 0.0
                break

            if partial_price is not None and not partial_taken and low <= partial_price:
                realized_from_partials += policy.partial_fraction * float(policy.partial_r)
                remaining_fraction -= policy.partial_fraction
                partial_taken = True
                days_to_partial = idx
                if policy.move_stop_to_entry_after_partial:
                    active_stop = min(active_stop, entry)

            if policy.time_stop_days is not None and idx >= int(policy.time_stop_days):
                realized_from_runner += remaining_fraction * ((entry - close) / risk)
                time_stop_exit = True
                days_to_exit = idx
                remaining_fraction = 0.0
                break

        if days_to_exit is None:
            realized_from_runner += remaining_fraction * ((entry - last_close) / risk)
            horizon_exit = True

        return ExitRunResult(
            policy_name=policy.name,
            candidate_id=int(candidate.candidate_id),
            ticker=candidate.ticker,
            candidate_date=candidate.candidate_date,
            candidate_state=candidate.candidate_state,
            decision_cohort=candidate.decision_cohort,
            regime_status=candidate.regime_status,
            regime_volatility=candidate.regime_volatility,
            regime_vix=candidate.regime_vix,
            realized_r=round(realized_from_partials + realized_from_runner, 4),
            target_hit=bool(target_hit),
            stop_hit=bool(stop_hit),
            breakeven_stop_hit=bool(breakeven_stop_hit),
            partial_taken=bool(partial_taken),
            time_stop_exit=bool(time_stop_exit),
            horizon_exit=bool(horizon_exit),
            days_to_exit=days_to_exit,
            days_to_partial=days_to_partial,
            realized_r_from_partials=round(realized_from_partials, 4),
            realized_r_from_runner=round(realized_from_runner, 4),
            mfe_r=round(mfe_r, 4),
            mae_r=round(mae_r, 4),
        )

    def _summarize_policy(
        self,
        *,
        policy: ExitPolicy,
        rows: Sequence[ExitRunResult],
        baseline_by_candidate: Dict[int, ExitRunResult],
    ) -> Dict[str, Any]:
        n = len(rows)
        avg_realized_r = self._avg([row.realized_r for row in rows])
        baseline_avg = self._avg(
            [baseline_by_candidate[row.candidate_id].realized_r for row in rows if row.candidate_id in baseline_by_candidate]
        )
        return {
            "name": policy.name,
            "label": policy.label,
            "n": n,
            "partial_taken_rate": self._rate(sum(1 for row in rows if row.partial_taken), n),
            "target_hit_rate": self._rate(sum(1 for row in rows if row.target_hit), n),
            "stop_hit_rate": self._rate(sum(1 for row in rows if row.stop_hit), n),
            "breakeven_stop_rate": self._rate(sum(1 for row in rows if row.breakeven_stop_hit), n),
            "time_stop_exit_rate": self._rate(sum(1 for row in rows if row.time_stop_exit), n),
            "horizon_exit_rate": self._rate(sum(1 for row in rows if row.horizon_exit), n),
            "avg_realized_r": avg_realized_r,
            "median_realized_r": self._median([row.realized_r for row in rows]),
            "avg_days_to_exit": self._avg([float(row.days_to_exit) for row in rows if row.days_to_exit is not None]),
            "avg_days_to_partial": self._avg([float(row.days_to_partial) for row in rows if row.days_to_partial is not None]),
            "avg_mfe_r": self._avg([row.mfe_r for row in rows]),
            "avg_mae_r": self._avg([row.mae_r for row in rows]),
            "delta_vs_baseline_avg_r": (
                round(avg_realized_r - baseline_avg, 4)
                if avg_realized_r is not None and baseline_avg is not None else None
            ),
        }

    def _segment_summary(
        self,
        *,
        rows_by_policy: Dict[str, List[ExitRunResult]],
        baseline_by_candidate: Dict[int, ExitRunResult],
        key: str,
        ordering: Sequence[str],
    ) -> List[Dict[str, Any]]:
        values = set()
        for rows in rows_by_policy.values():
            for row in rows:
                values.add(str(getattr(row, key) or "UNKNOWN").upper())
        ordered_values = [value for value in ordering if value in values]
        remaining = sorted(values.difference(ordered_values))
        labels = ordered_values + remaining

        segments: List[Dict[str, Any]] = []
        for label in labels:
            policy_metrics: Dict[str, Dict[str, Any]] = {}
            segment_n = 0
            for policy_name, rows in rows_by_policy.items():
                subset = [row for row in rows if str(getattr(row, key) or "UNKNOWN").upper() == label]
                if not subset:
                    continue
                segment_n = max(segment_n, len(subset))
                policy_metrics[policy_name] = self._summarize_policy(
                    policy=DEFAULT_POLICIES[policy_name],
                    rows=subset,
                    baseline_by_candidate=baseline_by_candidate,
                )
            if policy_metrics:
                segments.append(
                    {
                        "label": label,
                        "n": segment_n,
                        "policies": policy_metrics,
                    }
                )
        return segments

    def _build_examples(
        self,
        *,
        baseline_by_candidate: Dict[int, ExitRunResult],
        policy_results: Dict[str, List[ExitRunResult]],
        example_limit: int,
    ) -> Dict[str, List[Dict[str, Any]]]:
        improvements: List[Dict[str, Any]] = []
        losses_improved: List[Dict[str, Any]] = []
        worse_exits: List[Dict[str, Any]] = []

        for policy_name, rows in policy_results.items():
            if policy_name == BASELINE_RECORDED.name:
                continue
            for row in rows:
                baseline = baseline_by_candidate.get(row.candidate_id)
                if baseline is None:
                    continue
                delta = row.realized_r - baseline.realized_r
                payload = {
                    "policy": policy_name,
                    "ticker": row.ticker,
                    "candidate_date": row.candidate_date,
                    "candidate_state": row.candidate_state,
                    "decision_cohort": row.decision_cohort,
                    "regime_status": row.regime_status,
                    "baseline_realized_r": baseline.realized_r,
                    "policy_realized_r": row.realized_r,
                    "delta_realized_r": round(delta, 4),
                    "exit_reason": self._exit_reason(row),
                }
                improvements.append(payload)
                if baseline.realized_r <= -0.5 and delta > 0:
                    losses_improved.append(payload)
                if delta < 0:
                    worse_exits.append(payload)

        improvements.sort(key=lambda item: item["delta_realized_r"], reverse=True)
        losses_improved.sort(key=lambda item: item["delta_realized_r"], reverse=True)
        worse_exits.sort(key=lambda item: item["delta_realized_r"])
        return {
            "top_improvements": improvements[: max(0, int(example_limit))],
            "losses_improved": losses_improved[: max(0, int(example_limit))],
            "worse_exits": worse_exits[: max(0, int(example_limit))],
        }

    def _fetch_forward_bars(
        self,
        *,
        ticker: str,
        candidate_date: str,
        horizon_days: int,
        cache: Dict[Tuple[str, str], List[Dict[str, Any]]],
    ) -> List[Dict[str, Any]]:
        key = (ticker, candidate_date)
        cached = cache.get(key)
        if cached is not None:
            return cached
        candidate_dt = date.fromisoformat(str(candidate_date or "")[:10])
        age_days = max((datetime.now(timezone.utc).date() - candidate_dt).days, 0)
        lookback = max(90, int(age_days) + int(horizon_days) + 20)
        bars = self.data_feed.get_daily_bars(ticker, days_back=lookback, adjustment="all") or []
        output: List[Dict[str, Any]] = []
        for bar in bars:
            bar_date = self._bar_date(bar)
            if bar_date and bar_date > candidate_dt:
                output.append(bar)
        cache[key] = output
        return output

    @staticmethod
    def _bar_date(bar: Dict[str, Any]) -> Optional[date]:
        ts = bar.get("timestamp")
        if hasattr(ts, "date"):
            return ts.date()
        try:
            return datetime.fromisoformat(str(ts)).date()
        except Exception:
            return None

    @staticmethod
    def _exit_reason(row: ExitRunResult) -> str:
        if row.target_hit:
            return "TARGET_HIT"
        if row.stop_hit:
            return "STOP_HIT"
        if row.breakeven_stop_hit:
            return "BREAKEVEN_STOP"
        if row.time_stop_exit:
            return "TIME_STOP"
        if row.horizon_exit:
            return "HORIZON_EXIT"
        return "UNKNOWN"

    @staticmethod
    def _avg(values: Sequence[float]) -> Optional[float]:
        cleaned = [float(value) for value in values if value is not None]
        if not cleaned:
            return None
        return round(sum(cleaned) / len(cleaned), 4)

    @staticmethod
    def _median(values: Sequence[float]) -> Optional[float]:
        cleaned = [float(value) for value in values if value is not None]
        if not cleaned:
            return None
        return round(float(median(cleaned)), 4)

    @staticmethod
    def _rate(count: int, total: int) -> Optional[float]:
        if total <= 0:
            return None
        return round(count / total, 4)

    @staticmethod
    def _fmt_num(value: Any) -> str:
        if value is None:
            return "—"
        try:
            return f"{float(value):.3f}"
        except Exception:
            return str(value)

    @staticmethod
    def _fmt_pct(value: Any) -> str:
        if value is None:
            return "—"
        try:
            return f"{float(value) * 100:.1f}%"
        except Exception:
            return str(value)


def _parse_csv(raw: str) -> List[str]:
    return [part.strip() for part in str(raw or "").split(",") if part.strip()]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only short exit management experiment.")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    parser.add_argument("--horizon-days", type=int, default=DEFAULT_HORIZON_DAYS)
    parser.add_argument("--reason", default=DEFAULT_REASON)
    parser.add_argument("--states", default=",".join(DEFAULT_STATES))
    parser.add_argument("--cohorts", default="")
    parser.add_argument("--regime-statuses", default="")
    parser.add_argument("--volatilities", default="")
    parser.add_argument("--vix-buckets", default="")
    parser.add_argument(
        "--policies",
        default=",".join(DEFAULT_POLICIES.keys()),
        help=f"Comma-separated policies: {', '.join(DEFAULT_POLICIES.keys())}",
    )
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--examples", type=int, default=10)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--save", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    experiment = ShortExitManagementExperiment(db_path=args.db_path)
    report = experiment.run_experiment(
        horizon_days=int(args.horizon_days),
        decision_reason=str(args.reason or DEFAULT_REASON).lower(),
        states=_parse_csv(args.states),
        cohorts=_parse_csv(args.cohorts),
        regime_statuses=_parse_csv(args.regime_statuses),
        volatilities=_parse_csv(args.volatilities),
        vix_buckets=_parse_csv(args.vix_buckets),
        policy_names=_parse_csv(args.policies),
        limit=(int(args.limit) if int(args.limit) > 0 else None),
        example_limit=int(args.examples),
    )
    if args.save:
        paths = experiment.save_report(report)
        print(f"Saved: {paths['text']}  {paths['json']}")
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    else:
        print(experiment.format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
Cross-strategy readiness report grounded in doctrine and actual telemetry.

This report is intentionally evidence-first:
- strategy role and edge claim come from doctrine
- operating status comes from explicit policy gates
- promotion readiness comes from actual closed-trade evidence
- shadow outcomes are supplemental, not a substitute for live proof
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from io import StringIO
from datetime import datetime, timedelta
from statistics import mean
from typing import Any, Dict, List, Optional, Sequence, Tuple

from secure_env import load_runtime_env
from terminal_policy_snapshot import load_terminal_policy_snapshot

load_runtime_env("strategy_readiness_report")


DISPLAY_STRATEGIES: Tuple[str, ...] = ("VOYAGER", "SNIPER", "REMORA", "SHORT", "CONTRARIAN")
ACTIONABLE_DECISIONS = {
    "ENTRY_SUBMITTED",
    "PORTFOLIO_DENIED",
    "RISK_OVERLAY_DENIED",
    "EXECUTION_DENIED",
    "EXECUTE",
}
MIN_CLOSED_TRADES_FOR_LIVE = 15
MIN_WIN_RATE_PCT_FOR_LIVE = 45.0
MIN_SHADOW_OUTCOMES_CONTEXT = 25

STRATEGY_DOCTRINE: Dict[str, Dict[str, str]] = {
    "VOYAGER": {
        "mandate": "long-duration accumulation before the major run is widely recognized",
        "edge_claim": "align with real institutional accumulation while using retail agility for earlier, more precise entries",
        "failure_mode": "late entry into already-extended leaders",
        "horizon": "swing / position",
    },
    "SNIPER": {
        "mandate": "tactical breakout and continuation when timing quality is high",
        "edge_claim": "act faster than slower capital when clean momentum becomes tradeable",
        "failure_mode": "chasing weak breakouts with bad geometry",
        "horizon": "tactical swing",
    },
    "REMORA": {
        "mandate": "exploit short-lived catalyst and dislocation windows",
        "edge_claim": "capture fast post-catalyst mispricing before the market fully normalizes",
        "failure_mode": "treating random volatility as catalyst edge",
        "horizon": "event-driven tactical",
    },
    "SHORT": {
        "mandate": "capture deterioration before full downside repricing is complete",
        "edge_claim": "find early fundamental/technical breaks while avoiding crowded or exhausted shorts",
        "failure_mode": "shorting already-washed-out names or marginal weakness in strong tape",
        "horizon": "tactical swing",
    },
    "CONTRARIAN": {
        "mandate": "exploit panic, forced selling, and volatility overshoots",
        "edge_claim": "step in selectively where larger players are forced or constrained",
        "failure_mode": "fading normal weakness before real panic and stabilization",
        "horizon": "opportunistic swing",
    },
}


def _safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _pct(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round((numerator / denominator) * 100.0, 1)


def _strategy_aliases(strategy: str) -> Tuple[str, ...]:
    if strategy == "CONTRARIAN":
        return ("CONTRARIAN", "REAPER")
    return (strategy,)


def _format_metric(value: Optional[float], digits: int = 2, suffix: str = "") -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}{suffix}"


class StrategyReadinessReport:
    def __init__(self, db_path: str = "trading_performance.db"):
        self.db_path = db_path
        self.policy_snapshot = load_terminal_policy_snapshot(db_path)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _table_columns(self, table: str) -> set[str]:
        if not os.path.exists(self.db_path):
            return set()
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute(f"PRAGMA table_info({table})")
            return {str(row["name"]) for row in cur.fetchall()}
        finally:
            conn.close()

    @staticmethod
    def _cutoff(days: int) -> str:
        return (datetime.now() - timedelta(days=max(1, int(days)))).isoformat()

    def _fetch_closed_trades(self, strategy: str) -> List[sqlite3.Row]:
        cols = self._table_columns("trades")
        if not cols:
            return []

        select_cols = [
            "strategy", "direction", "status", "exit_reason", "entry_price", "exit_price",
            "stop_loss", "initial_stop_loss", "risk_amount", "actual_rr", "pnl_usd",
            "pnl", "pnl_pct", "pnl_percent", "entry_time", "exit_time", "exit_date",
        ]
        select_cols = [col for col in select_cols if col in cols]
        ordering_terms = [col for col in ("exit_time", "exit_date", "entry_time") if col in cols]
        if not ordering_terms:
            ordering_expr = "id"
        elif len(ordering_terms) == 1:
            ordering_expr = ordering_terms[0]
        else:
            ordering_expr = f"COALESCE({', '.join(ordering_terms)})"

        closed_clause = "UPPER(COALESCE(status, '')) = 'CLOSED'" if "status" in cols else "exit_time IS NOT NULL"
        excluded_clause = ""
        if "analytics_excluded" in cols:
            excluded_clause = " AND COALESCE(analytics_excluded, 0) = 0"

        aliases = _strategy_aliases(strategy)
        placeholders = ",".join("?" for _ in aliases)

        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT {', '.join(select_cols)}
                FROM trades
                WHERE UPPER(COALESCE(strategy, '')) IN ({placeholders})
                  AND ({closed_clause})
                  {excluded_clause}
                ORDER BY {ordering_expr} ASC, id ASC
                """,
                aliases,
            )
            return cur.fetchall()
        finally:
            conn.close()

    @staticmethod
    def _trade_pnl_usd(row: sqlite3.Row) -> Optional[float]:
        pnl_usd = _safe_float(row["pnl_usd"]) if "pnl_usd" in row.keys() else None
        if pnl_usd is not None:
            return pnl_usd
        pnl = _safe_float(row["pnl"]) if "pnl" in row.keys() else None
        return pnl

    @staticmethod
    def _trade_return_pct(row: sqlite3.Row) -> Optional[float]:
        if "pnl_pct" in row.keys():
            value = _safe_float(row["pnl_pct"])
            if value is not None:
                return value
        if "pnl_percent" in row.keys():
            return _safe_float(row["pnl_percent"])
        return None

    @staticmethod
    def _trade_r_multiple(row: sqlite3.Row) -> Optional[float]:
        risk_amount = _safe_float(row["risk_amount"]) if "risk_amount" in row.keys() else None
        pnl_usd = StrategyReadinessReport._trade_pnl_usd(row)
        if pnl_usd is not None and risk_amount not in (None, 0.0):
            return round(pnl_usd / abs(risk_amount), 4)

        actual_rr = _safe_float(row["actual_rr"]) if "actual_rr" in row.keys() else None
        if actual_rr is not None:
            return actual_rr

        entry = _safe_float(row["entry_price"]) if "entry_price" in row.keys() else None
        exit_price = _safe_float(row["exit_price"]) if "exit_price" in row.keys() else None
        stop = _safe_float(row["stop_loss"]) if "stop_loss" in row.keys() else None
        if stop is None and "initial_stop_loss" in row.keys():
            stop = _safe_float(row["initial_stop_loss"])
        direction = str(row["direction"] or "LONG").upper() if "direction" in row.keys() else "LONG"
        if None in (entry, exit_price, stop):
            return None
        risk_per_share = abs(entry - stop)
        if risk_per_share <= 0:
            return None
        if direction == "SHORT":
            return round((entry - exit_price) / risk_per_share, 4)
        return round((exit_price - entry) / risk_per_share, 4)

    def _trade_metrics(self, strategy: str) -> Dict[str, Any]:
        rows = self._fetch_closed_trades(strategy)
        if not rows:
            return {
                "closed_trades": 0,
                "win_rate_pct": None,
                "expectancy_usd": None,
                "avg_return_pct": None,
                "avg_r_multiple": None,
                "max_drawdown_usd": None,
            }

        pnl_values: List[float] = []
        return_values: List[float] = []
        r_values: List[float] = []
        wins = 0

        peak = 0.0
        cumulative = 0.0
        max_drawdown = 0.0

        for row in rows:
            pnl_usd = self._trade_pnl_usd(row)
            if pnl_usd is not None:
                pnl_values.append(pnl_usd)
                cumulative += pnl_usd
                peak = max(peak, cumulative)
                max_drawdown = min(max_drawdown, cumulative - peak)
            ret = self._trade_return_pct(row)
            if ret is not None:
                return_values.append(ret)
            r_mult = self._trade_r_multiple(row)
            if r_mult is not None:
                r_values.append(r_mult)

            if r_mult is not None:
                if r_mult > 0:
                    wins += 1
            elif pnl_usd is not None and pnl_usd > 0:
                wins += 1

        n = len(rows)
        return {
            "closed_trades": n,
            "win_rate_pct": round((wins / n) * 100.0, 1),
            "expectancy_usd": round(mean(pnl_values), 2) if pnl_values else None,
            "avg_return_pct": round(mean(return_values), 3) if return_values else None,
            "avg_r_multiple": round(mean(r_values), 3) if r_values else None,
            "max_drawdown_usd": round(abs(max_drawdown), 2) if pnl_values else None,
        }

    def _slippage_metrics(self, strategy: str) -> Dict[str, Any]:
        rows = self._fetch_closed_trades(strategy)
        slip_r_values: List[float] = []
        stop_exit_count = 0

        for row in rows:
            exit_reason = str(row["exit_reason"] or "").upper() if "exit_reason" in row.keys() else ""
            if "STOP" not in exit_reason:
                continue
            entry = _safe_float(row["entry_price"]) if "entry_price" in row.keys() else None
            exit_price = _safe_float(row["exit_price"]) if "exit_price" in row.keys() else None
            stop = _safe_float(row["stop_loss"]) if "stop_loss" in row.keys() else None
            if stop is None and "initial_stop_loss" in row.keys():
                stop = _safe_float(row["initial_stop_loss"])
            direction = str(row["direction"] or "LONG").upper() if "direction" in row.keys() else "LONG"
            if None in (entry, exit_price, stop):
                continue
            risk_per_share = abs(entry - stop)
            if risk_per_share <= 0:
                continue

            if direction == "SHORT":
                adverse_slip = max(0.0, exit_price - stop)
            else:
                adverse_slip = max(0.0, stop - exit_price)

            stop_exit_count += 1
            slip_r_values.append(adverse_slip / risk_per_share)

        if not slip_r_values:
            return {
                "stop_exit_count": stop_exit_count,
                "avg_slip_r": None,
                "max_slip_r": None,
                "quality": "N/A",
            }

        avg_slip_r = round(mean(slip_r_values), 3)
        max_slip_r = round(max(slip_r_values), 3)
        if avg_slip_r <= 0.10 and max_slip_r <= 0.25:
            quality = "CLEAN"
        elif avg_slip_r <= 0.25 and max_slip_r <= 0.50:
            quality = "ACCEPTABLE"
        else:
            quality = "POOR"

        return {
            "stop_exit_count": stop_exit_count,
            "avg_slip_r": avg_slip_r,
            "max_slip_r": max_slip_r,
            "quality": quality,
        }

    def _shadow_metrics(self, strategy: str, horizon_days: int) -> Dict[str, Any]:
        if not os.path.exists(self.db_path):
            return {"n": 0, "win_rate_pct": None, "avg_return_pct": None, "target_hit_pct": None, "stop_hit_pct": None}

        aliases = _strategy_aliases(strategy)
        placeholders = ",".join("?" for _ in aliases)
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT so.end_return_pct, so.hit_target, so.hit_stop
                FROM shadow_outcomes so
                JOIN shadow_candidates sc ON sc.id = so.candidate_id
                WHERE UPPER(COALESCE(sc.strategy, '')) IN ({placeholders})
                  AND sc.horizon_days = ?
                  AND so.horizon_days = ?
                  AND COALESCE(so.data_available, 1) = 1
                """,
                (*aliases, int(horizon_days), int(horizon_days)),
            )
            rows = cur.fetchall()
        finally:
            conn.close()

        if not rows:
            return {
                "n": 0,
                "win_rate_pct": None,
                "avg_return_pct": None,
                "target_hit_pct": None,
                "stop_hit_pct": None,
            }

        returns = [_safe_float(row["end_return_pct"], 0.0) or 0.0 for row in rows]
        wins = sum(1 for value in returns if value > 0)
        n = len(rows)
        return {
            "n": n,
            "win_rate_pct": round((wins / n) * 100.0, 1),
            "avg_return_pct": round(mean(returns), 3),
            "target_hit_pct": round(sum(int(row["hit_target"] or 0) for row in rows) / n * 100.0, 1),
            "stop_hit_pct": round(sum(int(row["hit_stop"] or 0) for row in rows) / n * 100.0, 1),
        }

    def _decision_metrics(self, strategy: str, days: int) -> Dict[str, Any]:
        cols = self._table_columns("decisions")
        if not cols:
            return {
                "decision_total": 0,
                "actionable_count": 0,
                "actionable_rate_pct": 0.0,
                "recent_tape_fit": "UNKNOWN",
                "top_failure_reasons": [],
                "latest_regime_status": None,
                "latest_regime_volatility": None,
                "latest_regime_timestamp": None,
            }

        aliases = _strategy_aliases(strategy)
        placeholders = ",".join("?" for _ in aliases)
        cutoff = self._cutoff(days)

        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT council_decision, COUNT(*) AS cnt
                FROM decisions
                WHERE UPPER(COALESCE(strategy, '')) IN ({placeholders})
                  AND timestamp >= ?
                GROUP BY council_decision
                """,
                (*aliases, cutoff),
            )
            decision_counts = {str(row["council_decision"] or "UNKNOWN"): int(row["cnt"] or 0) for row in cur.fetchall()}

            cur.execute(
                f"""
                SELECT council_reason, COUNT(*) AS cnt
                FROM decisions
                WHERE UPPER(COALESCE(strategy, '')) IN ({placeholders})
                  AND timestamp >= ?
                  AND council_decision = 'SCANNER_REJECT'
                  AND council_reason IS NOT NULL
                  AND TRIM(council_reason) != ''
                GROUP BY council_reason
                ORDER BY cnt DESC, council_reason ASC
                LIMIT 3
                """,
                (*aliases, cutoff),
            )
            top_failure_reasons = [
                {"reason": str(row["council_reason"]), "count": int(row["cnt"] or 0)}
                for row in cur.fetchall()
            ]

            if not top_failure_reasons:
                cur.execute(
                    f"""
                    SELECT COALESCE(NULLIF(execution_deny_reason, ''), council_reason, council_decision) AS reason,
                           COUNT(*) AS cnt
                    FROM decisions
                    WHERE UPPER(COALESCE(strategy, '')) IN ({placeholders})
                      AND timestamp >= ?
                      AND council_decision != 'ENTRY_SUBMITTED'
                    GROUP BY reason
                    ORDER BY cnt DESC, reason ASC
                    LIMIT 3
                    """,
                    (*aliases, cutoff),
                )
                top_failure_reasons = [
                    {"reason": str(row["reason"] or "UNKNOWN"), "count": int(row["cnt"] or 0)}
                    for row in cur.fetchall()
                ]

            cur.execute(
                f"""
                SELECT timestamp, regime_status, regime_volatility
                FROM decisions
                WHERE UPPER(COALESCE(strategy, '')) IN ({placeholders})
                  AND regime_status IS NOT NULL
                  AND regime_volatility IS NOT NULL
                ORDER BY timestamp DESC
                LIMIT 1
                """,
                aliases,
            )
            latest_regime = cur.fetchone()
        finally:
            conn.close()

        decision_total = sum(decision_counts.values())
        actionable_count = sum(decision_counts.get(decision, 0) for decision in ACTIONABLE_DECISIONS)
        actionable_rate_pct = _pct(actionable_count, decision_total)

        if decision_total == 0:
            recent_tape_fit = "UNKNOWN"
        elif actionable_count == 0:
            recent_tape_fit = "HOSTILE"
        elif actionable_rate_pct < 0.5:
            recent_tape_fit = "POOR"
        elif actionable_rate_pct < 2.0:
            recent_tape_fit = "MIXED"
        else:
            recent_tape_fit = "FAVORED"

        return {
            "decision_total": decision_total,
            "actionable_count": actionable_count,
            "actionable_rate_pct": actionable_rate_pct,
            "recent_tape_fit": recent_tape_fit,
            "top_failure_reasons": top_failure_reasons,
            "latest_regime_status": str(latest_regime["regime_status"]) if latest_regime else None,
            "latest_regime_volatility": str(latest_regime["regime_volatility"]) if latest_regime else None,
            "latest_regime_timestamp": str(latest_regime["timestamp"]) if latest_regime else None,
        }

    def _operating_status(
        self,
        strategy: str,
        trade_metrics: Dict[str, Any],
        shadow_metrics: Dict[str, Any],
        decision_metrics: Dict[str, Any],
    ) -> Tuple[str, bool, str]:
        if strategy == "SHORT" and not bool(self.policy_snapshot.get("short_live_enabled", True)):
            return "SHADOW", False, "SHORT_LIVE_ENABLED=0"

        if (
            trade_metrics["closed_trades"] == 0
            and shadow_metrics["n"] == 0
            and decision_metrics["decision_total"] == 0
        ):
            return "REBUILD", False, "no recent decisions or outcome evidence"

        avg_r = trade_metrics.get("avg_r_multiple")
        win_rate = trade_metrics.get("win_rate_pct")
        closed_trades = int(trade_metrics.get("closed_trades") or 0)

        promotion_ready = (
            closed_trades >= MIN_CLOSED_TRADES_FOR_LIVE
            and avg_r is not None
            and avg_r > 0
            and win_rate is not None
            and win_rate >= MIN_WIN_RATE_PCT_FOR_LIVE
        )
        if promotion_ready:
            return "LIVE", True, "closed-trade evidence meets minimum live bar"

        if closed_trades < MIN_CLOSED_TRADES_FOR_LIVE:
            return "REBUILD", False, f"closed_sample<{MIN_CLOSED_TRADES_FOR_LIVE}"
        if avg_r is None:
            return "REBUILD", False, "avg_r_unavailable"
        if avg_r <= 0:
            return "REBUILD", False, "avg_r<=0"
        if win_rate is None or win_rate < MIN_WIN_RATE_PCT_FOR_LIVE:
            return "REBUILD", False, f"win_rate<{MIN_WIN_RATE_PCT_FOR_LIVE:.0f}%"

        return "REBUILD", False, "promotion conditions not met"

    def build_report(self, *, days: int = 30, shadow_horizon_days: int = 5) -> Dict[str, Any]:
        strategies: List[Dict[str, Any]] = []
        for strategy in DISPLAY_STRATEGIES:
            doctrine = STRATEGY_DOCTRINE[strategy]
            trade_metrics = self._trade_metrics(strategy)
            slippage_metrics = self._slippage_metrics(strategy)
            shadow_metrics = self._shadow_metrics(strategy, shadow_horizon_days)
            decision_metrics = self._decision_metrics(strategy, days)
            status, promotion_ready, status_reason = self._operating_status(
                strategy,
                trade_metrics,
                shadow_metrics,
                decision_metrics,
            )

            strategies.append(
                {
                    "strategy": strategy,
                    "status": status,
                    "promotion_ready": promotion_ready,
                    "status_reason": status_reason,
                    "mandate": doctrine["mandate"],
                    "edge_claim": doctrine["edge_claim"],
                    "failure_mode": doctrine["failure_mode"],
                    "horizon": doctrine["horizon"],
                    "trade_metrics": trade_metrics,
                    "slippage_metrics": slippage_metrics,
                    "shadow_metrics": shadow_metrics,
                    "decision_metrics": decision_metrics,
                    "forecast_prior_active": strategy in self.policy_snapshot.get("forecast_active_strategies", []),
                }
            )

        return {
            "generated_at": datetime.now().isoformat(),
            "db_path": self.db_path,
            "window_days": int(days),
            "shadow_horizon_days": int(shadow_horizon_days),
            "policy_snapshot": self.policy_snapshot,
            "thresholds": {
                "min_closed_trades_for_live": MIN_CLOSED_TRADES_FOR_LIVE,
                "min_win_rate_pct_for_live": MIN_WIN_RATE_PCT_FOR_LIVE,
                "min_shadow_outcomes_context": MIN_SHADOW_OUTCOMES_CONTEXT,
            },
            "strategies": strategies,
        }

    @staticmethod
    def render_report_text(report: Dict[str, Any]) -> str:
        policy = report.get("policy_snapshot", {})
        out = StringIO()
        out.write("═" * 88 + "\n")
        out.write("STRATEGY READINESS REPORT\n")
        out.write("═" * 88 + "\n")
        out.write(
            f"Window: {report.get('window_days')}d | Shadow horizon: {report.get('shadow_horizon_days')}d | "
            f"SHORT mode: {policy.get('short_execution_mode')} | "
            f"VOY breadth: {policy.get('voyager_breadth_mode')} "
            f"({_format_metric(_safe_float(policy.get('voyager_breadth_pct')), 1, '%')})\n"
        )
        out.write(
            f"Forecast prior threshold: {policy.get('forecast_sample_threshold')} closed trades | "
            f"Forecast-active: {', '.join(policy.get('forecast_active_strategies') or []) or 'none'}\n"
        )
        out.write("─" * 88 + "\n")
        out.write(
            f"{'Strategy':<12} {'Status':<9} {'Tape':<8} {'Closed':>6} {'Win%':>6} "
            f"{'Exp$':>8} {'AvgR':>7} {'MaxDD$':>9} {'Slip':<10} {'Top Blocker':<18}\n"
        )
        out.write("─" * 88 + "\n")

        for row in report.get("strategies", []):
            trade = row["trade_metrics"]
            decision = row["decision_metrics"]
            slip = row["slippage_metrics"]
            blockers = row["decision_metrics"].get("top_failure_reasons") or []
            blocker = blockers[0]["reason"] if blockers else "—"
            out.write(
                f"{row['strategy']:<12} {row['status']:<9} {decision['recent_tape_fit']:<8} "
                f"{int(trade['closed_trades'] or 0):>6} "
                f"{_format_metric(trade['win_rate_pct'], 1, '%'):>6} "
                f"{_format_metric(trade['expectancy_usd'], 2):>8} "
                f"{_format_metric(trade['avg_r_multiple'], 2):>7} "
                f"{_format_metric(trade['max_drawdown_usd'], 2):>9} "
                f"{slip['quality']:<10} "
                f"{blocker[:18]:<18}\n"
            )

        out.write("═" * 88 + "\n")

        for row in report.get("strategies", []):
            trade = row["trade_metrics"]
            decision = row["decision_metrics"]
            shadow = row["shadow_metrics"]
            slip = row["slippage_metrics"]
            blockers = ", ".join(
                f"{item['reason']} ({item['count']})" for item in decision.get("top_failure_reasons", [])
            ) or "none"
            latest_regime = " / ".join(
                part for part in (
                    decision.get("latest_regime_status"),
                    decision.get("latest_regime_volatility"),
                ) if part
            ) or "unknown"
            out.write(f"{row['strategy']}  status={row['status']}  promotion_ready={row['promotion_ready']}\n")
            out.write(f"  Reason: {row['status_reason']}\n")
            out.write(f"  Mandate: {row['mandate']}\n")
            out.write(f"  Edge: {row['edge_claim']}\n")
            out.write(f"  Failure mode: {row['failure_mode']}\n")
            out.write(f"  Horizon: {row['horizon']}\n")
            out.write(
                f"  Recent tape fit: {decision['recent_tape_fit']}  "
                f"actionable={decision['actionable_count']}/{decision['decision_total']} "
                f"({_format_metric(decision['actionable_rate_pct'], 1, '%')})\n"
            )
            out.write(f"  Latest regime observed: {latest_regime}\n")
            out.write(
                f"  Closed trades: {int(trade['closed_trades'] or 0)}  "
                f"Win%={_format_metric(trade['win_rate_pct'], 1, '%')}  "
                f"Expectancy={_format_metric(trade['expectancy_usd'], 2)} USD  "
                f"AvgR={_format_metric(trade['avg_r_multiple'], 3)}  "
                f"MaxDD={_format_metric(trade['max_drawdown_usd'], 2)} USD\n"
            )
            out.write(
                f"  Slippage: {slip['quality']}  "
                f"stop_exits={int(slip['stop_exit_count'] or 0)}  "
                f"avg_slip_r={_format_metric(slip['avg_slip_r'], 3)}  "
                f"max_slip_r={_format_metric(slip['max_slip_r'], 3)}\n"
            )
            out.write(
                f"  Shadow {report.get('shadow_horizon_days')}d: "
                f"n={int(shadow['n'] or 0)}  "
                f"Win%={_format_metric(shadow['win_rate_pct'], 1, '%')}  "
                f"AvgRet%={_format_metric(shadow['avg_return_pct'], 3)}  "
                f"Tgt%={_format_metric(shadow['target_hit_pct'], 1, '%')}  "
                f"Stop%={_format_metric(shadow['stop_hit_pct'], 1, '%')}\n"
            )
            out.write(f"  Top blockers: {blockers}\n")
            out.write(
                f"  Forecast prior active: {'yes' if row['forecast_prior_active'] else 'no'}\n"
            )
            out.write("─" * 88 + "\n")
        return out.getvalue().rstrip() + "\n"

    @staticmethod
    def print_report(report: Dict[str, Any]) -> None:
        print(StrategyReadinessReport.render_report_text(report), end="")

    @staticmethod
    def save_report_artifacts(report: Dict[str, Any], logs_dir: str = "logs") -> Dict[str, str]:
        os.makedirs(logs_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d")
        json_path = os.path.join(logs_dir, f"strategy_readiness_{stamp}.json")
        txt_path = os.path.join(logs_dir, f"strategy_readiness_{stamp}.txt")
        with open(json_path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, sort_keys=True)
            handle.write("\n")
        with open(txt_path, "w", encoding="utf-8") as handle:
            handle.write(StrategyReadinessReport.render_report_text(report))
        return {"json_path": json_path, "text_path": txt_path}


def main() -> None:
    parser = argparse.ArgumentParser(description="Cross-strategy readiness report")
    parser.add_argument("--db", default="trading_performance.db", help="SQLite DB path")
    parser.add_argument("--days", type=int, default=30, help="Decision lookback window in days")
    parser.add_argument("--shadow-horizon-days", type=int, default=5, help="Shadow-outcome horizon")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text")
    parser.add_argument("--save", action="store_true", help="Write text + JSON artifacts to logs/")
    args = parser.parse_args()

    report = StrategyReadinessReport(db_path=args.db).build_report(
        days=args.days,
        shadow_horizon_days=args.shadow_horizon_days,
    )
    artifact_paths = None
    if args.save:
        artifact_paths = StrategyReadinessReport.save_report_artifacts(report)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        StrategyReadinessReport.print_report(report)
    if artifact_paths:
        print(f"Saved: {artifact_paths['text_path']}  {artifact_paths['json_path']}")


if __name__ == "__main__":
    main()

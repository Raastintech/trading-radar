from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

from breakout_timing_engine import BreakoutTimingTracker, DEFAULT_DB_PATH
from short_exit_management_experiment import DEFAULT_POLICIES
from short_management_selector_audit import (
    DEFAULT_SELECTOR_COHORTS,
    DEFAULT_SELECTOR_REGIME_STATUSES,
    DEFAULT_SELECTOR_STATES,
    DEFAULT_SELECTOR_VIX_BUCKETS,
    DEFAULT_SELECTOR_VOLATILITIES,
    DEFAULT_SHADOW_POLICY_NAME,
)

MODEL_VERSION = "short_management_shadow_monitor_v1"
SELECTOR_VERSION = "short_shadow_selector_v1"


def _norm_token(value: Any, default: str = "UNKNOWN") -> str:
    text = str(value or "").strip().upper()
    return text or default


class ShortManagementShadowMonitor:
    """Telemetry-only logger for the current short management shadow slice."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        self._ensure_table()

    def preview_rule(self) -> Dict[str, Any]:
        return {
            "selector_version": SELECTOR_VERSION,
            "strategy": "SHORT",
            "candidate_state": list(DEFAULT_SELECTOR_STATES),
            "decision_cohort": list(DEFAULT_SELECTOR_COHORTS),
            "regime_status": list(DEFAULT_SELECTOR_REGIME_STATUSES),
            "regime_volatility": list(DEFAULT_SELECTOR_VOLATILITIES),
            "regime_vix": list(DEFAULT_SELECTOR_VIX_BUCKETS),
            "management_policy": DEFAULT_SHADOW_POLICY_NAME,
            "management_policy_label": DEFAULT_POLICIES[DEFAULT_SHADOW_POLICY_NAME].label,
            "mode": "shadow_only",
        }

    def evaluate_decision(
        self,
        *,
        council_decision: Any,
        council_reason: Any,
        rr: Any,
        avg_score: Any,
        candidate_state: Any,
        regime_status: Any,
        regime_volatility: Any,
        regime_vix: Any,
    ) -> Dict[str, Any]:
        decision_cohort = BreakoutTimingTracker._short_decision_cohort(
            council_decision=council_decision,
            council_reason=council_reason,
            rr=rr,
            avg_score=avg_score,
        )
        state = _norm_token(candidate_state)
        status = _norm_token(regime_status)
        volatility = _norm_token(regime_volatility)
        vix_bucket = _norm_token(regime_vix)
        matched = (
            state in DEFAULT_SELECTOR_STATES
            and decision_cohort in DEFAULT_SELECTOR_COHORTS
            and status in DEFAULT_SELECTOR_REGIME_STATUSES
            and volatility in DEFAULT_SELECTOR_VOLATILITIES
            and vix_bucket in DEFAULT_SELECTOR_VIX_BUCKETS
        )
        preview = self.preview_rule()
        return {
            "decision_cohort": decision_cohort,
            "candidate_state": state,
            "regime_status": status,
            "regime_volatility": volatility,
            "regime_vix": vix_bucket,
            "selector_matched": bool(matched),
            "selector_version": preview["selector_version"],
            "shadow_policy_name": preview["management_policy"] if matched else None,
            "shadow_policy_label": preview["management_policy_label"] if matched else None,
            "shadow_rule_preview": preview,
        }

    def log_decision(
        self,
        *,
        decision_id: Optional[int],
        run_id: Any,
        ticker: Any,
        council_decision: Any,
        council_reason: Any,
        rr: Any,
        avg_score: Any,
        candidate_state: Any,
        regime_status: Any,
        regime_volatility: Any,
        regime_vix: Any,
        bte_short_breakdown_probability: Any = None,
        bte_short_source_dimension: Any = None,
        bte_short_source_segment: Any = None,
        decision_timestamp: Any = None,
        notes: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        if decision_id is None:
            return None

        evaluation = self.evaluate_decision(
            council_decision=council_decision,
            council_reason=council_reason,
            rr=rr,
            avg_score=avg_score,
            candidate_state=candidate_state,
            regime_status=regime_status,
            regime_volatility=regime_volatility,
            regime_vix=regime_vix,
        )

        payload = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "decision_id": int(decision_id),
            "run_id": str(run_id or ""),
            "decision_timestamp": str(decision_timestamp or datetime.now(timezone.utc).isoformat()),
            "ticker": str(ticker or "").upper(),
            "strategy": "SHORT",
            "council_decision": str(council_decision or "").upper(),
            "council_reason": str(council_reason or ""),
            "decision_cohort": evaluation["decision_cohort"],
            "candidate_state": evaluation["candidate_state"],
            "regime_status": evaluation["regime_status"],
            "regime_volatility": evaluation["regime_volatility"],
            "regime_vix": evaluation["regime_vix"],
            "avg_score": float(avg_score) if avg_score is not None else None,
            "rr": float(rr) if rr is not None else None,
            "bte_short_breakdown_probability": (
                float(bte_short_breakdown_probability)
                if bte_short_breakdown_probability is not None else None
            ),
            "bte_short_source_dimension": (
                str(bte_short_source_dimension) if bte_short_source_dimension else None
            ),
            "bte_short_source_segment": (
                str(bte_short_source_segment) if bte_short_source_segment else None
            ),
            "selector_version": evaluation["selector_version"],
            "selector_matched": 1 if evaluation["selector_matched"] else 0,
            "shadow_policy_name": evaluation["shadow_policy_name"],
            "shadow_policy_label": evaluation["shadow_policy_label"],
            "notes_json": json.dumps(notes or {}, default=str) if notes is not None else None,
        }

        conn = sqlite3.connect(self.db_path)
        try:
            cols = [key for key, value in payload.items() if value is not None]
            vals = [payload[key] for key in cols]
            placeholders = ", ".join("?" for _ in cols)
            conn.execute(
                f"""
                INSERT OR REPLACE INTO short_management_shadow_matches ({", ".join(cols)})
                VALUES ({placeholders})
                """,
                vals,
            )
            conn.commit()
        finally:
            conn.close()
        return evaluation

    def get_summary(
        self,
        *,
        days: int = 7,
        run_ids: Optional[Sequence[str]] = None,
        recent_limit: int = 5,
    ) -> Dict[str, Any]:
        preview = self.preview_rule()
        filters, params = self._build_filters(days=days, run_ids=run_ids)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            total_logged = self._scalar(
                conn,
                f"SELECT COUNT(*) FROM short_management_shadow_matches {filters}",
                params,
            )
            matched_count = self._scalar(
                conn,
                f"""
                SELECT COUNT(*) FROM short_management_shadow_matches
                {filters}{' AND' if filters else ' WHERE'} selector_matched = 1
                """,
                params,
            )
            matched_rate = (
                round(float(matched_count) / float(total_logged), 4)
                if total_logged else None
            )
            matched_cohorts = self._group_counts(
                conn,
                field="decision_cohort",
                filters=filters,
                params=params,
            )
            matched_states = self._group_counts(
                conn,
                field="candidate_state",
                filters=filters,
                params=params,
            )
            matched_decisions = self._group_counts(
                conn,
                field="council_decision",
                filters=filters,
                params=params,
            )
            recent_matches = [
                dict(row)
                for row in conn.execute(
                    f"""
                    SELECT ticker, decision_timestamp, council_decision, council_reason,
                           decision_cohort, candidate_state, regime_status, regime_volatility,
                           regime_vix, rr, avg_score, shadow_policy_name
                    FROM short_management_shadow_matches
                    {filters}{' AND' if filters else ' WHERE'} selector_matched = 1
                    ORDER BY decision_timestamp DESC, id DESC
                    LIMIT ?
                    """,
                    [*params, max(0, int(recent_limit))],
                ).fetchall()
            ]
        finally:
            conn.close()

        return {
            "model_version": MODEL_VERSION,
            "selector_version": preview["selector_version"],
            "window_days": int(days),
            "shadow_policy_name": preview["management_policy"],
            "shadow_policy_label": preview["management_policy_label"],
            "selector": {
                "candidate_state": preview["candidate_state"],
                "decision_cohort": preview["decision_cohort"],
                "regime_status": preview["regime_status"],
                "regime_volatility": preview["regime_volatility"],
                "regime_vix": preview["regime_vix"],
            },
            "total_logged": int(total_logged or 0),
            "matched_count": int(matched_count or 0),
            "matched_rate": matched_rate,
            "matched_cohorts": matched_cohorts,
            "matched_states": matched_states,
            "matched_decisions": matched_decisions,
            "recent_matches": recent_matches,
        }

    def _ensure_table(self) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS short_management_shadow_matches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    decision_id INTEGER UNIQUE,
                    run_id TEXT,
                    decision_timestamp TEXT,
                    ticker TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    council_decision TEXT,
                    council_reason TEXT,
                    decision_cohort TEXT,
                    candidate_state TEXT,
                    regime_status TEXT,
                    regime_volatility TEXT,
                    regime_vix TEXT,
                    avg_score REAL,
                    rr REAL,
                    bte_short_breakdown_probability REAL,
                    bte_short_source_dimension TEXT,
                    bte_short_source_segment TEXT,
                    selector_version TEXT,
                    selector_matched INTEGER DEFAULT 0,
                    shadow_policy_name TEXT,
                    shadow_policy_label TEXT,
                    notes_json TEXT
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _scalar(conn: sqlite3.Connection, sql: str, params: Sequence[Any]) -> int:
        row = conn.execute(sql, list(params)).fetchone()
        return int(row[0] or 0) if row else 0

    @staticmethod
    def _group_counts(
        conn: sqlite3.Connection,
        *,
        field: str,
        filters: str,
        params: Sequence[Any],
    ) -> List[Dict[str, Any]]:
        rows = conn.execute(
            f"""
            SELECT COALESCE({field}, 'UNKNOWN') AS label, COUNT(*) AS n
            FROM short_management_shadow_matches
            {filters}{' AND' if filters else ' WHERE'} selector_matched = 1
            GROUP BY COALESCE({field}, 'UNKNOWN')
            ORDER BY n DESC, label ASC
            """,
            list(params),
        ).fetchall()
        return [{"label": str(row["label"]), "n": int(row["n"] or 0)} for row in rows]

    @staticmethod
    def _build_filters(
        *,
        days: int,
        run_ids: Optional[Sequence[str]],
    ) -> tuple[str, List[Any]]:
        if run_ids:
            placeholders = ", ".join("?" for _ in run_ids)
            return f"WHERE run_id IN ({placeholders})", list(run_ids)
        window_start = (datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))).isoformat()
        return "WHERE created_at >= ?", [window_start]

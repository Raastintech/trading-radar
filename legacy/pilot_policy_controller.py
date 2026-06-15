#!/usr/bin/env python3
"""
pilot_policy_controller.py — Phase 4.3 Controlled Pilot Framework

Manages RR-threshold candidate observation (paper-only, OFF by default).

Safety invariants (non-negotiable):
  1. PILOT_ENABLE=0 (default) → zero behavior change anywhere
  2. Pilot runs ONLY in PAPER execution mode — hard block in LIVE
  3. Exposure caps enforced: max 1 new per scan, max 2 concurrent, max 0.25% equity risk
  4. One global instant-disable: PILOT_ENABLE=0

All schema changes are additive and idempotent.
"""

import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Policy definitions ─────────────────────────────────────────────────────────
# These are PAPER pilot shadow thresholds / monitoring policies.
# Current live thresholds: SNIPER=2.5, SHORT=2.5, VOYAGER=2.0, REMORA=2.0

PILOT_POLICIES: Dict[str, Dict] = {
    "SHORT_2_5": {
        "strategy": "SHORT",
        "shadow_threshold": 2.5,
        "live_threshold": 3.0,
        "direction": "SHORT",
        "priority": 1,
        "min_score_env": "SHORT_SCORE_FLOOR",
        "min_score_default": 55.0,
    },
    "SHORT_3_0": {
        "strategy": "SHORT",
        "shadow_threshold": 3.0,
        "live_threshold": 3.0,
        "direction": "SHORT",
        "priority": 2,
        "note": "Live-aligned SHORT monitoring policy",
        "min_score_env": "SHORT_SCORE_FLOOR",
        "min_score_default": 55.0,
    },
    "SNIPER_2_2": {
        "strategy": "SNIPER",
        "shadow_threshold": 2.2,
        "live_threshold": 2.5,
        "direction": "LONG",
        "priority": 3,
    },
    # NEW: SHORT with tighter stop geometry (paper calibration only).
    # Requires SHORT_STOP_MULTIPLIER=1.04 — used to compare tighter-stop branches.
    "SHORT_TIGHT_4_0": {
        "strategy": "SHORT",
        "shadow_threshold": 4.0,
        "live_threshold": 3.0,
        "direction": "SHORT",
        "priority": 4,
        "min_score_env": "SHORT_SCORE_FLOOR",
        "min_score_default": 55.0,
        "requires_env": {
            "SHORT_STOP_MULTIPLIER": "1.04",   # only activates if env matches exactly
        },
        "note": "SHORT at 4.0R with tighter stop geometry - stricter than live",
    },
    "SHORT_TIGHT_3_0": {
        "strategy": "SHORT",
        "shadow_threshold": 3.0,
        "live_threshold": 3.0,
        "direction": "SHORT",
        "priority": 5,
        "min_score_env": "SHORT_SCORE_FLOOR",
        "min_score_default": 55.0,
        "requires_env": {
            "SHORT_STOP_MULTIPLIER": "1.04",
        },
        "note": "SHORT at live 3.0R with tighter stop geometry - monitoring only",
    },
    # Future (placeholder only, intentionally inactive for Phase 5):
    # "CONTRARIAN_1_5": {
    #     "strategy": "CONTRARIAN",
    #     "shadow_threshold": 1.5,
    #     "live_threshold": 2.0,
    #     "direction": "LONG",
    #     "priority": 3,
    # },
}

# Reason codes for eligibility check
REASON_ELIGIBLE = "ELIGIBLE"
REASON_LIVE_MODE_BLOCK = "LIVE_MODE_BLOCK"
REASON_PILOT_DISABLED = "PILOT_DISABLED"
REASON_POLICY_NOT_ACTIVE = "POLICY_NOT_ACTIVE"
REASON_RR_BELOW_THRESHOLD = "RR_BELOW_THRESHOLD"
REASON_WRONG_STRATEGY = "WRONG_STRATEGY"
REASON_EXPOSURE_CAP_REACHED = "EXPOSURE_CAP_REACHED"
REASON_ENV_CONDITION_NOT_MET = "ENV_CONDITION_NOT_MET"
REASON_SCORE_BELOW_THRESHOLD = "SCORE_BELOW_THRESHOLD"


# ── DB schema migration ────────────────────────────────────────────────────────

def _ensure_pilot_schema(db_path: str) -> None:
    """
    Additive, idempotent DB schema migration for pilot tables/columns.

    Each ALTER TABLE is wrapped in try/except so re-running is safe
    (SQLite raises OperationalError 'duplicate column name' on re-run).
    """
    ddl_decisions_cols = [
        "ALTER TABLE decisions ADD COLUMN is_pilot INTEGER DEFAULT 0",
        "ALTER TABLE decisions ADD COLUMN pilot_policy TEXT",
        "ALTER TABLE decisions ADD COLUMN pilot_threshold REAL",
        "ALTER TABLE decisions ADD COLUMN pilot_decision_reason TEXT",
    ]

    ddl_trades_cols = [
        "ALTER TABLE trades ADD COLUMN is_pilot INTEGER DEFAULT 0",
        "ALTER TABLE trades ADD COLUMN pilot_policy TEXT",
        "ALTER TABLE trades ADD COLUMN pilot_threshold REAL",
        "ALTER TABLE trades ADD COLUMN analytics_excluded INTEGER DEFAULT 0",
        "ALTER TABLE trades ADD COLUMN analytics_exclude_reason TEXT",
        "ALTER TABLE trades ADD COLUMN analytics_excluded_at TEXT",
    ]

    ddl_create_pilot_events = """
    CREATE TABLE IF NOT EXISTS pilot_events (
        event_id       INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at     TEXT DEFAULT (datetime('now')),
        run_id         TEXT,
        ticker         TEXT,
        policy_name    TEXT,
        event_type     TEXT,
        reason_code    TEXT,
        rr             REAL,
        threshold      REAL,
        execution_mode TEXT DEFAULT 'PAPER',
        notes          TEXT
    )
    """

    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()

        # Decisions columns
        for stmt in ddl_decisions_cols:
            try:
                cur.execute(stmt)
            except sqlite3.OperationalError:
                pass  # Column already exists — idempotent

        # Trades columns (only if trades table exists)
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='trades'")
        if cur.fetchone():
            for stmt in ddl_trades_cols:
                try:
                    cur.execute(stmt)
                except sqlite3.OperationalError:
                    pass  # Column already exists

        # pilot_events table
        cur.execute(ddl_create_pilot_events)

        conn.commit()
        conn.close()
        logger.debug("pilot_policy_controller: schema migration complete")
    except Exception as exc:
        logger.warning(f"pilot_policy_controller: schema migration failed: {exc}")


# ── Controller ────────────────────────────────────────────────────────────────

class PilotPolicyController:
    """
    Controls pilot candidate observation for RR-threshold experiments.

    Default state: everything disabled (PILOT_ENABLE=0).
    Zero behavior change unless operator explicitly sets PILOT_ENABLE=1.
    """

    def __init__(
        self,
        execution_mode: str = "PAPER",
        db_path: Optional[str] = None,
    ):
        self.execution_mode = str(execution_mode or "PAPER").upper().strip()
        self.db_path = db_path or os.getenv("TRADING_DB_PATH", "trading_performance.db")
        self._config = self._load_config()

    # ── Config ────────────────────────────────────────────────────────────────

    def _load_config(self) -> Dict:
        """Read pilot config from environment with safe defaults."""
        raw_enable = os.getenv("PILOT_ENABLE", "0").strip()
        enabled = raw_enable in ("1", "true", "yes", "on")

        # Include SHORT_TIGHT policies in default allowed list when SHORT_STOP_MULTIPLIER=1.04.
        _default_policies = "SHORT_2_5,SNIPER_2_2"
        if os.getenv("SHORT_STOP_MULTIPLIER", "").strip() == "1.04":
            _default_policies = "SHORT_2_5,SNIPER_2_2,SHORT_TIGHT_4_0,SHORT_TIGHT_3_0"
        raw_policies = os.getenv("PILOT_ALLOWED_POLICIES", _default_policies).strip()
        allowed_policies = [p.strip() for p in raw_policies.split(",") if p.strip()]

        raw_execute = os.getenv("PILOT_EXECUTE_PAPER", "0").strip()
        execute_paper = raw_execute in ("1", "true", "yes", "on")

        try:
            max_new_per_scan = int(os.getenv("PILOT_MAX_NEW_PER_SCAN", "1"))
        except (ValueError, TypeError):
            max_new_per_scan = 1

        try:
            max_concurrent = int(os.getenv("PILOT_MAX_CONCURRENT", "2"))
        except (ValueError, TypeError):
            max_concurrent = 2

        try:
            max_risk_pct = float(os.getenv("PILOT_MAX_RISK_PCT", "0.25"))
        except (ValueError, TypeError):
            max_risk_pct = 0.25

        try:
            pilot_score_floor = float(os.getenv("PILOT_SCORE_FLOOR", "40.0"))
        except (ValueError, TypeError):
            pilot_score_floor = 40.0

        return {
            "pilot_enable": enabled,
            "allowed_policies": allowed_policies,
            "execute_paper": execute_paper,
            "max_new_per_scan": max_new_per_scan,
            "max_concurrent": max_concurrent,
            "max_risk_pct": max_risk_pct,
            "pilot_score_floor": pilot_score_floor,
        }

    def get_pilot_config(self) -> Dict:
        """Return all pilot config values (for diagnostics/logging)."""
        return dict(self._config)

    def _resolve_score_threshold(self, policy: Dict) -> float:
        """
        Resolve the effective score floor for a policy.

        Uses the higher of:
        - global pilot score floor
        - policy-specific linked floor (for example SHORT_SCORE_FLOOR)
        """
        try:
            base_floor = float(self._config.get("pilot_score_floor", 40.0) or 40.0)
        except (TypeError, ValueError):
            base_floor = 40.0

        env_key = str(policy.get("min_score_env") or "").strip()
        default_floor = policy.get("min_score_default")
        if not env_key:
            return base_floor

        raw_env = os.getenv(env_key)
        try:
            linked_floor = float(raw_env) if raw_env not in (None, "") else float(default_floor)
        except (TypeError, ValueError):
            linked_floor = float(default_floor or base_floor)
        return max(base_floor, linked_floor)

    # ── Core predicates ───────────────────────────────────────────────────────

    def is_pilot_enabled(self) -> bool:
        """
        Returns True only if PILOT_ENABLE=1 AND execution_mode is NOT LIVE.
        Hard block in LIVE mode regardless of env var.
        """
        if not self._config.get("pilot_enable", False):
            return False
        if self.is_live_mode_block(self.execution_mode):
            return False
        return True

    def is_live_mode_block(self, execution_mode: str) -> bool:
        """Returns True if execution_mode is LIVE (pilot is unconditionally blocked)."""
        return str(execution_mode or "").upper().strip() == "LIVE"

    # ── Eligibility ───────────────────────────────────────────────────────────

    def check_eligibility(
        self,
        reject_row: Dict,
        policy: Dict,
        existing_pilot_count: int = 0,
        per_scan_count: int = 0,
    ) -> Tuple[bool, str]:
        """
        Check whether a single reject_row is eligible under a given policy.

        Returns (eligible: bool, reason_code: str).

        Reason codes:
          LIVE_MODE_BLOCK         — execution_mode is LIVE
          PILOT_DISABLED          — PILOT_ENABLE=0
          POLICY_NOT_ACTIVE       — policy_name not in allowed_policies
          RR_BELOW_THRESHOLD      — rr < policy shadow_threshold
          SCORE_BELOW_THRESHOLD   — reject score below pilot score floor
          WRONG_STRATEGY          — reject strategy does not match policy strategy
          EXPOSURE_CAP_REACHED    — concurrent or per-scan cap exceeded
          ELIGIBLE                — passed all checks
        """
        # Hard block: LIVE mode
        if self.is_live_mode_block(self.execution_mode):
            return False, REASON_LIVE_MODE_BLOCK

        # Pilot must be enabled
        if not self._config.get("pilot_enable", False):
            return False, REASON_PILOT_DISABLED

        # Check requires_env conditions first — before allowed-list check.
        # A policy with an unmet env condition is fundamentally ineligible,
        # regardless of whether it appears in the allowed list.
        requires_env = policy.get("requires_env") or {}
        if requires_env:
            for env_key, expected_val in requires_env.items():
                actual_val = os.getenv(env_key, "").strip()
                if actual_val != str(expected_val).strip():
                    return False, REASON_ENV_CONDITION_NOT_MET

        # Policy must be in allowed list
        policy_name = policy.get("name", "")
        if policy_name and policy_name not in self._config.get("allowed_policies", []):
            return False, REASON_POLICY_NOT_ACTIVE

        # Strategy must match
        reject_strategy = str(reject_row.get("strategy") or "").upper().strip()
        policy_strategy = str(policy.get("strategy") or "").upper().strip()
        if reject_strategy and policy_strategy and reject_strategy != policy_strategy:
            return False, REASON_WRONG_STRATEGY

        # RR must meet shadow threshold
        rr_raw = reject_row.get("rr") or reject_row.get("risk_reward") or 0.0
        try:
            rr = float(rr_raw)
        except (TypeError, ValueError):
            rr = 0.0
        shadow_threshold = float(policy.get("shadow_threshold", 999.0))
        if rr < shadow_threshold:
            return False, REASON_RR_BELOW_THRESHOLD

        score_threshold = self._resolve_score_threshold(policy)
        score_raw = reject_row.get("score")
        try:
            score = float(score_raw)
        except (TypeError, ValueError):
            score = 0.0
        if score < score_threshold:
            return False, REASON_SCORE_BELOW_THRESHOLD

        # Exposure caps
        max_concurrent = self._config.get("max_concurrent", 2)
        max_new_per_scan = self._config.get("max_new_per_scan", 1)
        if existing_pilot_count >= max_concurrent:
            return False, REASON_EXPOSURE_CAP_REACHED
        if per_scan_count >= max_new_per_scan:
            return False, REASON_EXPOSURE_CAP_REACHED

        return True, REASON_ELIGIBLE

    # ── Candidate scanning ────────────────────────────────────────────────────

    def get_pilot_candidates(
        self,
        reject_rows: List[Dict],
        existing_pilot_count: int = 0,
        run_id: Optional[str] = None,
    ) -> List[Dict]:
        """
        From a list of scanner reject rows, identify candidates that would be
        eligible under any active pilot policy.

        Returns list of dicts: {ticker, policy_name, rr, reason}

        Hard guards:
          - Returns [] if pilot is disabled
          - Returns [] if in LIVE mode
          - Respects exposure caps (max_concurrent, max_new_per_scan)
        """
        if not self.is_pilot_enabled():
            return []

        candidates = []
        max_new = self._config.get("max_new_per_scan", 1)
        max_concurrent = self._config.get("max_concurrent", 2)
        available_slots = min(max_new, max(0, max_concurrent - existing_pilot_count))
        if available_slots <= 0:
            return []

        # Sort policies by priority so highest-priority policy wins first
        sorted_policies = sorted(
            PILOT_POLICIES.items(),
            key=lambda kv: kv[1].get("priority", 99),
        )

        for row in (reject_rows or []):
            ticker = str(row.get("ticker") or "?")
            row_strategy = str(row.get("strategy") or "").upper().strip()

            for policy_name, policy in sorted_policies:
                # Skip policy if not in allowed list
                if policy_name not in self._config.get("allowed_policies", []):
                    continue

                pol_with_name = dict(policy)
                pol_with_name["name"] = policy_name
                pol_strategy = str(policy.get("strategy") or "").upper().strip()

                # Quick strategy pre-filter to avoid slow check_eligibility on mismatches
                if row_strategy and pol_strategy and row_strategy != pol_strategy:
                    continue

                eligible, reason = self.check_eligibility(
                    reject_row=row,
                    policy=pol_with_name,
                    existing_pilot_count=existing_pilot_count,
                    per_scan_count=0,
                )

                rr_raw = row.get("rr") or row.get("risk_reward") or 0.0
                try:
                    rr = float(rr_raw)
                except (TypeError, ValueError):
                    rr = 0.0

                # Record each policy evaluation in pilot_events for diagnostics.
                self.log_pilot_event(
                    run_id=run_id,
                    ticker=ticker,
                    policy_name=policy_name,
                    event_type="CANDIDATE",
                    reason_code=reason,
                    rr=rr,
                    threshold=float(policy.get("shadow_threshold", 0.0) or 0.0),
                    notes=f"strategy={row_strategy}",
                )

                if eligible:
                    candidates.append({
                        "ticker": ticker,
                        "policy_name": policy_name,
                        "rr": rr,
                        "reason": reason,
                        "strategy": row_strategy,
                        "direction": policy.get("direction", ""),
                        "shadow_threshold": policy.get("shadow_threshold"),
                        "live_threshold": policy.get("live_threshold"),
                        # Carry execution-shape fields from reject row so
                        # Phase 4.4 can submit controlled paper pilot entries.
                        "entry_price": row.get("entry_price", row.get("entry")),
                        "stop_loss": row.get("stop_loss", row.get("stop")),
                        "target_price": row.get("target_price", row.get("target")),
                        "score": row.get("score"),
                        "grade": row.get("grade"),
                        "primary_pathway": row.get("primary_pathway"),
                        "pathways_failed": row.get("pathways_failed", []),
                        "_priority": int(policy.get("priority", 99) or 99),
                    })
                    self.log_pilot_event(
                        run_id=run_id,
                        ticker=ticker,
                        policy_name=policy_name,
                        event_type="ELIGIBLE",
                        reason_code=REASON_ELIGIBLE,
                        rr=rr,
                        threshold=float(policy.get("shadow_threshold", 0.0) or 0.0),
                        notes="candidate_accepted",
                    )
                    break  # One policy match per ticker per scan pass
                else:
                    self.log_pilot_event(
                        run_id=run_id,
                        ticker=ticker,
                        policy_name=policy_name,
                        event_type="BLOCKED",
                        reason_code=reason,
                        rr=rr,
                        threshold=float(policy.get("shadow_threshold", 0.0) or 0.0),
                        notes="candidate_blocked",
                    )
                    break  # evaluated matching policy path; stop on first result

        candidates.sort(
            key=lambda c: (
                int(c.get("_priority", 99)),
                -float(c.get("rr", 0.0) or 0.0),
                str(c.get("ticker") or ""),
            )
        )

        selected: List[Dict] = []
        seen_tickers = set()
        for cand in candidates:
            ticker = str(cand.get("ticker") or "").upper().strip()
            if ticker in seen_tickers:
                continue
            seen_tickers.add(ticker)
            selected.append({k: v for k, v in cand.items() if not str(k).startswith("_")})
            if len(selected) >= available_slots:
                break

        return selected

    # ── Reporting ─────────────────────────────────────────────────────────────

    def get_observe_summary(self, candidates: List[Dict]) -> str:
        """
        Format a compact summary string suitable for log output.

        Example:
            "2 candidates observed: [AAPL SHORT_2_5 rr=2.8, MSFT SNIPER_2_2 rr=2.3]"
        """
        if not candidates:
            return "0 pilot candidates this scan"
        parts = [
            f"{c['ticker']} {c['policy_name']} rr={c.get('rr', 0):.2f}"
            for c in candidates
        ]
        return f"{len(candidates)} pilot candidate(s) this scan: [{', '.join(parts)}]"

    def log_pilot_event(
        self,
        run_id: Optional[str],
        ticker: str,
        policy_name: str,
        event_type: str,
        reason_code: str,
        rr: float,
        threshold: float,
        notes: Optional[str] = None,
    ) -> None:
        """
        Write a record to the pilot_events table.
        event_type: 'CANDIDATE' | 'ELIGIBLE' | 'BLOCKED' | 'EXECUTED' | 'CLOSED'
        Silent on failure — never raises.
        """
        # Require run-scoped telemetry to prevent synthetic/test pollution.
        if not str(run_id or "").strip():
            return
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO pilot_events
                    (run_id, ticker, policy_name, event_type, reason_code,
                     rr, threshold, execution_mode, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    ticker,
                    policy_name,
                    event_type,
                    reason_code,
                    rr,
                    threshold,
                    self.execution_mode,
                    notes,
                ),
            )
            conn.commit()
            conn.close()
        except Exception as exc:
            logger.debug(f"pilot log_pilot_event failed: {exc}")

    def get_today_summary(self) -> Dict:
        """
        Query pilot_events for today's counts.
        Returns: {considered, eligible, blocked, executed, open, closed, win_pct}
        Silent on failure.
        """
        default = {
            "considered": 0,
            "eligible": 0,
            "blocked": 0,
            "executed": 0,
            "open": 0,
            "closed": 0,
            "win_pct": None,
        }
        try:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            # Check pilot_events table exists
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='pilot_events'"
            )
            if not cur.fetchone():
                conn.close()
                return default

            cur.execute(
                """
                SELECT event_type, COUNT(*) as n
                FROM pilot_events
                WHERE DATE(created_at) = ?
                  AND TRIM(COALESCE(run_id, '')) <> ''
                GROUP BY event_type
                """,
                (today,),
            )
            counts = {row["event_type"]: row["n"] for row in cur.fetchall()}

            considered = counts.get("CANDIDATE", 0)
            eligible = counts.get("ELIGIBLE", 0)
            blocked = counts.get("BLOCKED", 0)
            executed = counts.get("EXECUTED", 0)
            closed = counts.get("CLOSED", 0)
            win_pct = None

            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='trades'")
            has_trades = bool(cur.fetchone())
            if has_trades:
                cur.execute("PRAGMA table_info(trades)")
                tcols = {row["name"] for row in cur.fetchall()}
                entry_col = "entry_time" if "entry_time" in tcols else ("entry_date" if "entry_date" in tcols else None)
                if entry_col and "is_pilot" in tcols:
                    analytics_filter = "COALESCE(analytics_excluded, 0) = 0" if "analytics_excluded" in tcols else "1=1"
                    exit_checks = []
                    if "status" in tcols:
                        exit_checks.append("UPPER(COALESCE(status, '')) = 'CLOSED'")
                    if "exit_time" in tcols:
                        exit_checks.append("exit_time IS NOT NULL")
                    if "exit_date" in tcols:
                        exit_checks.append("exit_date IS NOT NULL")
                    closed_expr = " OR ".join(exit_checks) if exit_checks else "0"

                    win_expr = None
                    if "win" in tcols:
                        win_expr = "COALESCE(win, 0) = 1"
                    elif "pnl" in tcols:
                        win_expr = "COALESCE(pnl, 0) > 0"
                    elif "pnl_pct" in tcols:
                        win_expr = "COALESCE(pnl_pct, 0) > 0"
                    elif "pnl_percent" in tcols:
                        win_expr = "COALESCE(pnl_percent, 0) > 0"

                    cur.execute(
                        f"""
                        SELECT
                            COUNT(*) AS executed,
                            SUM(CASE WHEN ({closed_expr}) THEN 1 ELSE 0 END) AS closed,
                            SUM(CASE WHEN ({closed_expr}) AND ({win_expr or '0'}) THEN 1 ELSE 0 END) AS wins
                        FROM trades
                        WHERE COALESCE(is_pilot, 0) = 1
                          AND {analytics_filter}
                          AND DATE({entry_col}) = ?
                        """,
                        (today,),
                    )
                    trade_rollup = cur.fetchone()
                    if trade_rollup is not None:
                        executed = int(trade_rollup["executed"] or 0)
                        closed = int(trade_rollup["closed"] or 0)
                        wins = int(trade_rollup["wins"] or 0)
                        win_pct = round(wins / closed * 100, 1) if closed else None

            conn.close()

            return {
                "considered": considered,
                "eligible": eligible,
                "blocked": blocked,
                "executed": executed,
                "open": max(0, executed - closed),
                "closed": closed,
                "win_pct": win_pct,
            }
        except Exception as exc:
            logger.debug(f"get_today_summary failed: {exc}")
            return default

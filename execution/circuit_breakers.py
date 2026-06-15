"""
execution/circuit_breakers.py — Kill switches and daily loss circuit breakers.

Provides:
  • Global halt based on daily P&L drawdown
  • Per-strategy kill switches (operator-controlled)
  • Checked at the start of every scan cycle in main.py

Phase 0 hardening (2026-05):
  • Global halt state is now PERSISTED to ``circuit_breaker_state`` in
    trading.db.  A process restart no longer silently clears a halt.
  • Halts NO LONGER auto-clear when daily P&L recovers above the
    threshold.  The audit found this produced a "rally trap" — trip,
    bounce, un-trip, re-pile-in.  An operator must call ``clear_halt``
    (CLI / dashboard) to resume.  This is intentional: by the time a
    breaker has tripped we want a human to confirm conditions before
    risking capital again.
  • Per-strategy kill switches remain in-memory (lower stakes; operator
    re-applies on restart if needed).
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Set, Tuple

import core.config as cfg

logger = logging.getLogger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────
DAILY_LOSS_HALT_PCT   = -0.05   # halt all new orders if daily PnL <= -5% of equity
STRATEGY_LOSS_HALT_PCT = -0.03  # halt specific strategy if its open P&L <= -3% of equity


_DDL = """
CREATE TABLE IF NOT EXISTS circuit_breaker_state (
    -- single-row table: id is always 1.
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    halted          INTEGER NOT NULL DEFAULT 0,
    reason          TEXT    NOT NULL DEFAULT '',
    tripped_at      TEXT,           -- ISO8601 UTC, NULL when not halted
    cleared_at      TEXT,           -- ISO8601 UTC of last manual clear
    cleared_by      TEXT            -- operator label, free-text
);
INSERT OR IGNORE INTO circuit_breaker_state (id, halted, reason)
VALUES (1, 0, '');
"""


class CircuitBreakers:
    """
    Runtime kill-switch and circuit-breaker controller.

    Instantiate once at startup and pass into the main loop.
    Thread-safe reads (set operations are GIL-protected in CPython).
    Global halt state is persisted to ``trading.db`` so it survives
    restarts; per-strategy kills stay in memory.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._killed_strategies: Set[str] = set()
        self._global_halt: bool = False
        self._global_halt_reason: str = ""
        # Lazily resolve DB path so unit tests can pass an override.
        self._db_path = str(db_path or cfg.DB_PATH)
        self._init_state_table()
        self._load_state()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _connect(self):
        """Open a hardened SQLite connection.  Imported here to avoid a
        circular import at module load time (core.db imports config)."""
        from core.db import connect as _hardened_connect
        return _hardened_connect(self._db_path, check_same_thread=False)

    def _init_state_table(self) -> None:
        try:
            with self._connect() as con:
                con.executescript(_DDL)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("circuit_breaker_state init failed: %s", exc)

    def _load_state(self) -> None:
        """Read persisted halt from DB into memory at startup so a halt
        from a previous process incarnation is honored before any order
        is evaluated."""
        try:
            with self._connect() as con:
                row = con.execute(
                    "SELECT halted, reason FROM circuit_breaker_state WHERE id=1"
                ).fetchone()
            if row:
                self._global_halt = bool(row[0])
                self._global_halt_reason = str(row[1] or "")
                if self._global_halt:
                    logger.warning(
                        "CIRCUIT BREAKER persisted halt loaded: %s",
                        self._global_halt_reason,
                    )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("circuit_breaker_state load failed: %s", exc)

    def _persist_trip(self, reason: str) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        try:
            with self._connect() as con:
                con.execute(
                    "UPDATE circuit_breaker_state "
                    "SET halted=1, reason=?, tripped_at=? WHERE id=1",
                    (reason, ts),
                )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("circuit_breaker_state trip persist failed: %s", exc)

    def _persist_clear(self, cleared_by: str) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        try:
            with self._connect() as con:
                con.execute(
                    "UPDATE circuit_breaker_state "
                    "SET halted=0, reason='', tripped_at=NULL, "
                    "    cleared_at=?, cleared_by=? WHERE id=1",
                    (ts, cleared_by),
                )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("circuit_breaker_state clear persist failed: %s", exc)

    # ── Global halt ───────────────────────────────────────────────────────────

    def check_daily_loss(self, portfolio_state: Dict) -> Tuple[bool, str]:
        """
        Evaluates daily P&L against the halt threshold.

        Args:
            portfolio_state: dict from PositionMonitor.portfolio_state().

        Returns:
            (halted: bool, reason: str)

        Phase 0 change: this function NEVER clears a halt.  Once tripped
        the breaker stays tripped until ``clear_halt`` is called by an
        operator, even if intraday P&L recovers above the threshold.
        Reason: avoid the rally-trap whipsaw documented in the audit.
        """
        daily_pnl_pct = portfolio_state.get("daily_pnl_pct", 0.0)

        if daily_pnl_pct <= DAILY_LOSS_HALT_PCT:
            reason = (
                f"Daily loss circuit breaker: P&L = {daily_pnl_pct:.1%} "
                f"(threshold: {DAILY_LOSS_HALT_PCT:.1%})"
            )
            if not self._global_halt:
                logger.warning("CIRCUIT BREAKER TRIGGERED: %s", reason)
                self._global_halt = True
                self._global_halt_reason = reason
                self._persist_trip(reason)
            return True, reason

        # If we are already halted, stay halted regardless of recovery.
        if self._global_halt:
            return True, self._global_halt_reason

        return False, ""

    def is_globally_halted(self) -> Tuple[bool, str]:
        """Returns (halted, reason). Use before entering a new trade."""
        return self._global_halt, self._global_halt_reason

    def force_halt(self, reason: str = "operator halt") -> None:
        """Manually halt all new orders."""
        logger.warning("MANUAL HALT: %s", reason)
        self._global_halt = True
        self._global_halt_reason = reason
        self._persist_trip(reason)

    def clear_halt(self, cleared_by: str = "operator") -> None:
        """Manually clear a halt (operator must confirm conditions).

        This is the only path that clears the breaker — the daily-loss
        check no longer auto-clears on recovery."""
        logger.info("Manual halt cleared by %s.", cleared_by)
        self._global_halt = False
        self._global_halt_reason = ""
        self._persist_clear(cleared_by)

    # ── Per-strategy kill switches ────────────────────────────────────────────

    def kill_strategy(self, strategy_name: str, reason: str = "") -> None:
        """
        Disable a specific strategy. New signals from it will be blocked.
        Open positions are not touched — they follow their normal exit rules.
        """
        name = strategy_name.upper()
        self._killed_strategies.add(name)
        logger.warning("Strategy KILL: %s  reason=%s", name, reason or "unspecified")

    def revive_strategy(self, strategy_name: str) -> None:
        """Re-enable a killed strategy."""
        name = strategy_name.upper()
        self._killed_strategies.discard(name)
        logger.info("Strategy REVIVED: %s", name)

    def is_strategy_active(self, strategy_name: str) -> bool:
        """Returns True if the strategy is allowed to place new orders."""
        return strategy_name.upper() not in self._killed_strategies

    def killed_strategies(self) -> Set[str]:
        """Returns the set of currently killed strategies."""
        return set(self._killed_strategies)

    # ── Convenience: single gate check ────────────────────────────────────────

    def gate(self, strategy_name: str, portfolio_state: Dict) -> Tuple[bool, str]:
        """
        Single call that checks both global halt and per-strategy kill switch.

        Returns (allowed: bool, reason: str).
        Use in the main loop before evaluating any signal.
        """
        # Check daily loss circuit breaker
        halted, reason = self.check_daily_loss(portfolio_state)
        if halted:
            return False, reason

        # Check global manual halt
        halted, reason = self.is_globally_halted()
        if halted:
            return False, reason

        # Check per-strategy kill
        if not self.is_strategy_active(strategy_name):
            return False, f"{strategy_name.upper()} is killed"

        return True, ""

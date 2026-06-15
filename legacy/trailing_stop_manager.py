"""
trailing_stop_manager.py — R-multiple trailing stop logic for breakout positions.

Pure math, no side effects, no DB, no API calls.

Rules:
  TRAIL_ACTIVATE_R (default 2.0): When position gains >= 2R, raise stop to breakeven.
  TRAIL_LOCK_R     (default 3.0): When position gains >= 3R, lock in 1R profit.
  Stop never moves backwards (only ratchets in favor of the position).
  LONG : stop only moves UP
  SHORT: stop only moves DOWN

Env vars:
  TRAIL_STOP_ENABLE  = 1   (default ON; set to 0 to disable)
  TRAIL_ACTIVATE_R   = 2.0 (R multiple that triggers breakeven stop)
  TRAIL_LOCK_R       = 3.0 (R multiple that locks 1R profit)
"""

import os
from typing import Optional, Tuple

# ── availability flag (import-guard) ─────────────────────────────────────────
_TRAIL_STOP_AVAILABLE = True


def _env_float(name: str, default: float) -> float:
    """Read env var as float; return default on missing or invalid."""
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _trail_enabled() -> bool:
    """Return True unless TRAIL_STOP_ENABLE explicitly set to 0/false/no/off."""
    raw = os.getenv("TRAIL_STOP_ENABLE", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


class TrailingStopManager:
    """
    Manages trailing stop activation for positions that have moved in our favor.

    Rules:
    - If position gains >= TRAIL_ACTIVATE_R (default 2.0R):
        * Raise stop to breakeven (entry price)
        * Log reason_code TRAIL_ACTIVATED
    - If position gains >= TRAIL_LOCK_R (default 3.0R):
        * Lock in 1R profit: stop = entry + 1 * risk_per_share (LONG)
                              stop = entry - 1 * risk_per_share (SHORT)
        * Log reason_code TRAIL_LOCKED_1R
    - Stop only ratchets in favour — never moves backwards.
    - LONG  positions: stop only moves UP.
    - SHORT positions: stop only moves DOWN.
    """

    REASON_ACTIVATED = "TRAIL_ACTIVATED"
    REASON_LOCKED_1R = "TRAIL_LOCKED_1R"

    def compute_new_stop(
        self,
        ticker: str,
        direction: str,           # "LONG" or "SHORT"
        entry_price: float,
        current_stop: float,
        current_price: float,
        original_stop: float,     # stop at entry — used to compute risk_per_share
        target_price: float,
    ) -> Tuple[float, Optional[str]]:
        """
        Compute the updated stop level according to R-multiple trailing rules.

        Returns:
            (new_stop, reason_code)
            new_stop == current_stop  means no change.
            reason_code: "TRAIL_ACTIVATED" | "TRAIL_LOCKED_1R" | None
        """
        # Disabled globally
        if not _trail_enabled():
            return current_stop, None

        # Sanitise direction
        is_long = str(direction or "LONG").upper().strip() != "SHORT"

        # Risk per share (always positive)
        if is_long:
            risk_per_share = entry_price - original_stop
        else:
            risk_per_share = original_stop - entry_price

        # Guard: degenerate risk means we can't compute R multiples
        if risk_per_share <= 0:
            return current_stop, None

        # Current gain in R multiples
        if is_long:
            gain_r = (current_price - entry_price) / risk_per_share
        else:
            gain_r = (entry_price - current_price) / risk_per_share

        activate_r = _env_float("TRAIL_ACTIVATE_R", 2.0)
        lock_r = _env_float("TRAIL_LOCK_R", 3.0)

        # --- Evaluate best applicable rule (highest R first) ---

        if gain_r >= lock_r:
            # Lock in 1R profit
            if is_long:
                candidate = entry_price + risk_per_share   # stop = entry + 1R
                # Only raise stop (never lower)
                if candidate > current_stop:
                    return candidate, self.REASON_LOCKED_1R
            else:
                candidate = entry_price - risk_per_share   # stop = entry - 1R
                # Only lower stop for SHORT (move in favour)
                if candidate < current_stop:
                    return candidate, self.REASON_LOCKED_1R
            return current_stop, None

        if gain_r >= activate_r:
            # Move stop to breakeven
            if is_long:
                candidate = entry_price
                if candidate > current_stop:
                    return candidate, self.REASON_ACTIVATED
            else:
                candidate = entry_price
                if candidate < current_stop:
                    return candidate, self.REASON_ACTIVATED
            return current_stop, None

        # Below activation threshold — no change
        return current_stop, None


# ── DB schema migration helper (additive, idempotent) ────────────────────────

def ensure_trail_schema(db_path: str) -> None:
    """
    Add trail-stop columns to trades table and create trail_events table.
    Safe to call multiple times (idempotent via try/except on ALTER TABLE).
    """
    try:
        import sqlite3
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()

        for stmt in [
            "ALTER TABLE trades ADD COLUMN trail_stop_activated INTEGER DEFAULT 0",
            "ALTER TABLE trades ADD COLUMN trail_locked_at_r REAL",
        ]:
            try:
                cur.execute(stmt)
            except Exception:
                pass  # column already exists — idempotent

        cur.execute("""
            CREATE TABLE IF NOT EXISTS trail_events (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at     TEXT DEFAULT (datetime('now')),
                ticker         TEXT NOT NULL,
                direction      TEXT,
                reason_code    TEXT,
                entry_price    REAL,
                old_stop       REAL,
                new_stop       REAL,
                current_price  REAL,
                gain_r         REAL,
                execution_mode TEXT DEFAULT 'PAPER',
                run_id         TEXT
            )
        """)

        conn.commit()
        conn.close()
    except Exception:
        pass  # never raise — schema migration is best-effort


def log_trail_event(
    db_path: str,
    ticker: str,
    direction: str,
    reason_code: str,
    entry_price: float,
    old_stop: float,
    new_stop: float,
    current_price: float,
    gain_r: float,
    execution_mode: str = "PAPER",
    run_id: Optional[str] = None,
) -> None:
    """
    Write a row to the trail_events table. Silent on failure.
    Only called from the position monitor — never from compute_new_stop().
    """
    try:
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            INSERT INTO trail_events
                (ticker, direction, reason_code, entry_price, old_stop,
                 new_stop, current_price, gain_r, execution_mode, run_id)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (ticker, direction, reason_code, entry_price, old_stop,
             new_stop, current_price, gain_r, execution_mode, run_id),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

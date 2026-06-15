"""
core/decision_logger.py — Writes trade decisions and outcomes to SQLite.

Schema matches the existing trading_performance.db decisions table so
existing analytics tools continue to work unchanged.
"""
from __future__ import annotations
import json
import logging
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Optional
import uuid

import core.config as cfg

logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS decisions (
    id              TEXT PRIMARY KEY,
    run_id          TEXT,
    ts              TEXT NOT NULL,
    ticker          TEXT NOT NULL,
    strategy        TEXT NOT NULL,
    direction       TEXT NOT NULL,
    signal_score    REAL,
    shares          REAL,
    entry_price     REAL,
    stop_loss       REAL,
    target_price    REAL,
    risk_reward     REAL,
    order_id        TEXT,
    position_opened INTEGER DEFAULT 0,
    position_closed INTEGER DEFAULT 0,
    exit_price      REAL,
    pnl             REAL,
    pnl_pct         REAL,
    veto_votes      TEXT,
    notes           TEXT,
    -- Fill correctness (Phase 0): never assume signal entry == filled entry.
    fill_price      REAL,            -- broker filled_avg_price for the entry
    fill_qty        REAL,            -- broker filled_qty (may be < shares on partial)
    slippage_bps    REAL,            -- (fill_price - entry_price)/entry_price * 1e4
    fill_status     TEXT,            -- broker terminal status: filled|partially_filled|...
    exit_fill_price REAL             -- actual exit fill from broker close response
    -- UNITS NOTE (Phase 1F): pnl_pct on this table is stored as a FRACTION
    -- (e.g. 0.1419 means +14.19%). Contrast paper_signal_outcomes.return_pct
    -- which stores PERCENT (14.19). Any cross-table join MUST normalize.
);

CREATE TABLE IF NOT EXISTS veto_log (
    id          TEXT PRIMARY KEY,
    ts          TEXT NOT NULL,
    ticker      TEXT NOT NULL,
    strategy    TEXT NOT NULL,
    verdict     TEXT NOT NULL,   -- APPROVED | VETOED
    agent       TEXT,
    reason      TEXT,
    run_id      TEXT
);

CREATE TABLE IF NOT EXISTS macro_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_time_utc  TEXT,
    event_name      TEXT,
    country         TEXT,
    impact          TEXT,
    actual          TEXT,
    forecast        TEXT,
    previous        TEXT,
    source          TEXT DEFAULT 'fmp'
);
"""


class DecisionLogger:
    def __init__(self, run_id: Optional[str] = None):
        self._db = cfg.DB_PATH
        self._run_id = run_id or str(uuid.uuid4())[:8]
        from core.db import connect as _hardened_connect
        self._conn = _hardened_connect(str(self._db), check_same_thread=False)
        self._conn.executescript(_DDL)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """Phase 0: add fill-correctness columns to existing DBs that
        already had the decisions table.  ALTER TABLE … ADD COLUMN is a
        cheap O(1) metadata change in SQLite and does not rewrite rows.

        Phase 1C: add nullable suspect-marking columns so the paper-state
        audit can flag dubious rows without deleting or rewriting them.
        Production write paths do not populate these — only the audit
        script with --mark-suspects does.
        """
        existing = {row[1] for row in self._conn.execute(
            "PRAGMA table_info(decisions)"
        )}
        for col, ddl in (
            ("fill_price",      "REAL"),
            ("fill_qty",        "REAL"),
            ("slippage_bps",    "REAL"),
            ("fill_status",     "TEXT"),
            ("exit_fill_price", "REAL"),
            ("suspect_state",   "TEXT"),
            ("suspect_reason",  "TEXT"),
            ("reconciled_at",   "TEXT"),
        ):
            if col not in existing:
                self._conn.execute(
                    f"ALTER TABLE decisions ADD COLUMN {col} {ddl}"
                )

        # Macro standby scoping: add a nullable `country` column so the
        # MacroAgent can blackout on U.S. high-impact events only (not every
        # country's calendar). Additive, O(1) metadata change; existing rows
        # backfill to NULL and are repopulated on the next macro refresh.
        macro_cols = {row[1] for row in self._conn.execute(
            "PRAGMA table_info(macro_events)"
        )}
        if "country" not in macro_cols:
            self._conn.execute(
                "ALTER TABLE macro_events ADD COLUMN country TEXT"
            )

    def log_decision(
        self,
        ticker: str,
        strategy: str,
        direction: str,
        signal_score: float,
        shares: float,
        entry_price: float,
        stop_loss: float,
        target_price: float,
        order_id: Optional[str] = None,
        position_opened: bool = False,
        veto_votes: Optional[Dict] = None,
        notes: str = "",
        fill_price: Optional[float] = None,
        fill_qty: Optional[float] = None,
        slippage_bps: Optional[float] = None,
        fill_status: Optional[str] = None,
    ) -> str:
        """Persist a decision row.

        ``entry_price`` is the *intended* signal entry; ``fill_price`` is
        what the broker actually filled at.  P&L should be computed off
        ``fill_price`` (with ``entry_price`` as fallback for legacy rows
        that pre-date Phase 0)."""
        dec_id = str(uuid.uuid4())
        rr = round((target_price - entry_price) / (entry_price - stop_loss), 2) if (entry_price - stop_loss) != 0 else 0
        with self._conn:
            self._conn.execute(
                """INSERT INTO decisions
                   (id, run_id, ts, ticker, strategy, direction, signal_score,
                    shares, entry_price, stop_loss, target_price, risk_reward,
                    order_id, position_opened, veto_votes, notes,
                    fill_price, fill_qty, slippage_bps, fill_status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    dec_id, self._run_id,
                    datetime.now(timezone.utc).isoformat(),
                    ticker.upper(), strategy.upper(), direction.upper(),
                    signal_score, shares, entry_price, stop_loss, target_price, rr,
                    order_id, int(position_opened),
                    json.dumps(veto_votes or {}),
                    notes,
                    fill_price, fill_qty, slippage_bps, fill_status,
                ),
            )
        logger.info("Decision logged: %s %s %s id=%s", strategy, direction, ticker, dec_id)
        return dec_id

    def log_exit(
        self,
        decision_id: str,
        exit_price: float,
        pnl: float,
        pnl_pct: float,
        exit_fill_price: Optional[float] = None,
    ) -> None:
        """Persist an exit.  ``exit_price`` is the price at which the
        exit signal fired (e.g. last quoted close); ``exit_fill_price``
        is the broker-reported actual fill, when available.

        Phase 1G.1: closing this row also clears any prior
        ``suspect_state='closing_in_progress'`` marker (a partial fill
        from an earlier cycle that has now fully filled). We do not
        touch other suspect_state values — those belong to the hygiene
        audit, not the exit path.
        """
        with self._conn:
            self._conn.execute(
                """UPDATE decisions
                   SET position_closed=1, exit_price=?, pnl=?, pnl_pct=?,
                       exit_fill_price=COALESCE(?, exit_fill_price),
                       suspect_state=CASE
                           WHEN suspect_state='closing_in_progress' THEN NULL
                           ELSE suspect_state
                       END,
                       suspect_reason=CASE
                           WHEN suspect_state='closing_in_progress' THEN NULL
                           ELSE suspect_reason
                       END
                   WHERE id=?""",
                (exit_price, pnl, round(pnl_pct, 4),
                 exit_fill_price, decision_id),
            )

    def mark_suspect(
        self,
        decision_id: str,
        suspect_state: str,
        suspect_reason: str = "",
    ) -> None:
        """Phase 1G.1: tag an open decision row as suspect without
        closing it. Used by the position monitor when ``close_position``
        returns with a partial fill or a non-terminal status — the row
        stays open so the reconciler keeps it in scope, but the
        suspect_state makes it visible that we know the row is
        mid-flight.

        Only writes when ``suspect_state`` actually changes; idempotent
        otherwise.
        """
        with self._conn:
            self._conn.execute(
                """UPDATE decisions
                   SET suspect_state=?,
                       suspect_reason=?
                   WHERE id=?
                     AND (suspect_state IS NULL OR suspect_state != ?
                          OR suspect_reason IS NULL
                          OR suspect_reason != ?)""",
                (suspect_state, suspect_reason, decision_id,
                 suspect_state, suspect_reason),
            )

    def log_veto(
        self,
        ticker: str,
        strategy: str,
        verdict: str,
        agent: str = "",
        reason: str = "",
    ) -> None:
        with self._conn:
            self._conn.execute(
                """INSERT INTO veto_log (id, ts, ticker, strategy, verdict, agent, reason, run_id)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    str(uuid.uuid4()),
                    datetime.now(timezone.utc).isoformat(),
                    ticker.upper(), strategy.upper(),
                    verdict.upper(), agent, reason, self._run_id,
                ),
            )

    def refresh_macro_events(self, events: list[Dict]) -> None:
        """Overwrite macro_events with the latest FMP economic calendar data."""
        with self._conn:
            self._conn.execute("DELETE FROM macro_events")
            self._conn.executemany(
                """INSERT INTO macro_events
                   (event_time_utc, event_name, country, impact, actual, forecast, previous, source)
                   VALUES (?,?,?,?,?,?,?,?)""",
                [
                    (
                        ev.get("date"),
                        ev.get("event"),
                        ev.get("country"),
                        ev.get("impact"),
                        str(ev.get("actual", "")),
                        str(ev.get("estimate", "")),
                        str(ev.get("previous", "")),
                        "fmp",
                    )
                    for ev in events
                ],
            )
        logger.info("macro_events refreshed: %d events", len(events))

    def has_open_decision(self, ticker: str, strategy: Optional[str] = None) -> bool:
        """Phase-1-F idempotency check: do we already hold an open
        decision row for this ticker (optionally per-strategy)?

        Treats ``position_opened=1 AND position_closed=0`` as the
        canonical "open" state, ignoring `suspect_state` so that
        reconciled-open rows still block new orders. A single-source
        truth (broker `get_positions`) is not enough — a transient
        provider hiccup can return an empty list and bypass dedup.
        Cross-checking the DB closes that race window."""
        q = ("SELECT 1 FROM decisions "
             "WHERE position_opened=1 AND position_closed=0 "
             "AND ticker=? ")
        params: list = [ticker.upper()]
        if strategy:
            q += "AND strategy=? "
            params.append(strategy.upper())
        q += "LIMIT 1"
        return self._conn.execute(q, params).fetchone() is not None

    def has_recent_unfilled_decision(
        self,
        ticker: str,
        strategy: Optional[str] = None,
        within_minutes: int = 390,
    ) -> bool:
        """Idempotency check for *resting* orders, complementing
        ``has_open_decision`` (which only sees filled positions).

        Returns True when a recent decision row submitted an order that
        has not filled (``position_opened=0 AND position_closed=0`` with a
        non-empty ``order_id``) for this ticker within ``within_minutes``.
        Such a row marks a DAY limit order left resting on a prior scan
        cycle; re-submitting against it piles up duplicate resting orders.

        This is the DB-side backstop for the broker ``get_open_orders``
        check — a transient broker failure returns ``[]`` (fail-open), so
        the book cross-check closes that race the same way ``has_open_decision``
        backs up ``get_positions``.  The 390-minute default spans one
        trading session; DAY orders expire at the close, so a same-ticker
        signal the next session is not blocked."""
        cutoff = (
            datetime.now(timezone.utc) - timedelta(minutes=within_minutes)
        ).isoformat()
        q = ("SELECT 1 FROM decisions "
             "WHERE position_opened=0 AND position_closed=0 "
             "AND order_id IS NOT NULL AND order_id != '' "
             "AND ticker=? AND ts >= ? ")
        params: list = [ticker.upper(), cutoff]
        if strategy:
            q += "AND strategy=? "
            params.append(strategy.upper())
        q += "LIMIT 1"
        return self._conn.execute(q, params).fetchone() is not None

    def get_open_decisions(self) -> list[Dict]:
        rows = self._conn.execute(
            "SELECT * FROM decisions WHERE position_opened=1 AND position_closed=0"
        ).fetchall()
        cols = [d[0] for d in self._conn.execute("SELECT * FROM decisions LIMIT 0").description]
        return [dict(zip(cols, r)) for r in rows]

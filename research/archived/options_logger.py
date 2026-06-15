from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class OptionsLogger:
    """Persistent storage for the isolated options engine."""

    def __init__(self, db_path: str = "trading_performance.db"):
        self.db_path = db_path
        self._ensure_tables()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_tables(self) -> None:
        conn = self._connect()
        cur = conn.cursor()

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS option_positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                underlying_strategy TEXT NOT NULL,
                structure_type TEXT NOT NULL,
                underlying_direction TEXT NOT NULL,
                state TEXT NOT NULL,
                broker TEXT NOT NULL,
                paper_mode INTEGER NOT NULL DEFAULT 1,
                run_id TEXT,
                source_type TEXT,
                opened_at TEXT NOT NULL,
                closed_at TEXT,
                entry_expiry TEXT,
                entry_dte INTEGER,
                contracts INTEGER NOT NULL DEFAULT 1,
                entry_net_price REAL NOT NULL,
                entry_is_credit INTEGER NOT NULL DEFAULT 0,
                profit_target_mark REAL,
                stop_mark REAL,
                max_profit_usd REAL,
                max_risk_usd REAL,
                current_mark REAL,
                total_pnl_usd REAL,
                total_pnl_pct REAL,
                underlying_entry_price REAL,
                underlying_exit_price REAL,
                iv_regime TEXT,
                iv_rank_30d REAL,
                iv_rank_252d REAL,
                options_pcr REAL,
                options_gamma TEXT,
                notes TEXT,
                exit_reason TEXT,
                candidate_id INTEGER
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_option_positions_state ON option_positions(state)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_option_positions_ticker ON option_positions(ticker)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_option_positions_strategy ON option_positions(underlying_strategy)"
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS option_legs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                position_id INTEGER NOT NULL,
                leg_role TEXT NOT NULL,
                contract_symbol TEXT NOT NULL,
                option_type TEXT NOT NULL,
                strike REAL NOT NULL,
                expiry TEXT NOT NULL,
                side_open TEXT NOT NULL,
                side_close TEXT,
                quantity INTEGER NOT NULL DEFAULT 1,
                delta_at_entry REAL,
                entry_price REAL NOT NULL,
                exit_price REAL,
                opened_at TEXT NOT NULL,
                closed_at TEXT,
                state TEXT NOT NULL DEFAULT 'OPEN',
                notes TEXT,
                FOREIGN KEY(position_id) REFERENCES option_positions(id)
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_option_legs_position ON option_legs(position_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_option_legs_contract ON option_legs(contract_symbol)"
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS option_iv_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                date TEXT NOT NULL,
                atm_iv REAL NOT NULL,
                iv_rank_30d REAL,
                iv_rank_252d REAL,
                iv_pct_252d REAL,
                source TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(ticker, date)
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_option_iv_history_ticker_date ON option_iv_history(ticker, date)"
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS option_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT,
                timestamp TEXT NOT NULL,
                ticker TEXT NOT NULL,
                underlying_strategy TEXT NOT NULL,
                underlying_direction TEXT NOT NULL,
                source_type TEXT NOT NULL,
                structure_type TEXT,
                status TEXT NOT NULL,
                reason TEXT,
                entry_price REAL,
                stop_loss REAL,
                target_price REAL,
                equity_rr REAL,
                underlying_score REAL,
                options_pcr REAL,
                options_gamma TEXT,
                iv_rank_30d REAL,
                iv_rank_252d REAL,
                iv_regime TEXT,
                expiry TEXT,
                dte INTEGER,
                short_contract TEXT,
                long_contract TEXT,
                net_price REAL,
                width REAL,
                max_risk REAL,
                score REAL,
                broker_order_id TEXT,
                notes TEXT
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_option_candidates_time ON option_candidates(timestamp)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_option_candidates_ticker ON option_candidates(ticker)"
        )

        conn.commit()
        conn.close()

    def log_candidate(self, payload: Dict) -> int:
        now = payload.get("timestamp") or _utc_now_iso()
        conn = self._connect()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO option_candidates (
                run_id, timestamp, ticker, underlying_strategy, underlying_direction,
                source_type, structure_type, status, reason, entry_price, stop_loss,
                target_price, equity_rr, underlying_score, options_pcr, options_gamma,
                iv_rank_30d, iv_rank_252d, iv_regime, expiry, dte, short_contract,
                long_contract, net_price, width, max_risk, score, broker_order_id, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.get("run_id"),
                now,
                payload.get("ticker"),
                payload.get("underlying_strategy"),
                payload.get("underlying_direction"),
                payload.get("source_type"),
                payload.get("structure_type"),
                payload.get("status"),
                payload.get("reason"),
                payload.get("entry_price"),
                payload.get("stop_loss"),
                payload.get("target_price"),
                payload.get("equity_rr"),
                payload.get("underlying_score"),
                payload.get("options_pcr"),
                payload.get("options_gamma"),
                payload.get("iv_rank_30d"),
                payload.get("iv_rank_252d"),
                payload.get("iv_regime"),
                payload.get("expiry"),
                payload.get("dte"),
                payload.get("short_contract"),
                payload.get("long_contract"),
                payload.get("net_price"),
                payload.get("width"),
                payload.get("max_risk"),
                payload.get("score"),
                payload.get("broker_order_id"),
                json.dumps(payload.get("notes"), default=str) if isinstance(payload.get("notes"), (dict, list)) else payload.get("notes"),
            ),
        )
        candidate_id = int(cur.lastrowid)
        conn.commit()
        conn.close()
        return candidate_id

    def update_candidate(self, candidate_id: int, *, status: str, broker_order_id: Optional[str] = None, reason: Optional[str] = None) -> None:
        conn = self._connect()
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE option_candidates
            SET status = ?,
                broker_order_id = COALESCE(?, broker_order_id),
                reason = COALESCE(?, reason)
            WHERE id = ?
            """,
            (status, broker_order_id, reason, int(candidate_id)),
        )
        conn.commit()
        conn.close()

    def open_position(self, position_payload: Dict, legs: Iterable[Dict]) -> int:
        opened_at = position_payload.get("opened_at") or _utc_now_iso()
        conn = self._connect()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO option_positions (
                ticker, underlying_strategy, structure_type, underlying_direction,
                state, broker, paper_mode, run_id, source_type, opened_at,
                entry_expiry, entry_dte, contracts, entry_net_price, entry_is_credit,
                profit_target_mark, stop_mark, max_profit_usd, max_risk_usd,
                current_mark, total_pnl_usd, total_pnl_pct, underlying_entry_price,
                iv_regime, iv_rank_30d, iv_rank_252d, options_pcr, options_gamma,
                notes, candidate_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                position_payload.get("ticker"),
                position_payload.get("underlying_strategy"),
                position_payload.get("structure_type"),
                position_payload.get("underlying_direction"),
                position_payload.get("state", "OPEN"),
                position_payload.get("broker", "PAPER"),
                1 if position_payload.get("paper_mode", True) else 0,
                position_payload.get("run_id"),
                position_payload.get("source_type"),
                opened_at,
                position_payload.get("entry_expiry"),
                position_payload.get("entry_dte"),
                int(position_payload.get("contracts", 1) or 1),
                float(position_payload.get("entry_net_price") or 0.0),
                1 if position_payload.get("entry_is_credit", False) else 0,
                position_payload.get("profit_target_mark"),
                position_payload.get("stop_mark"),
                position_payload.get("max_profit_usd"),
                position_payload.get("max_risk_usd"),
                position_payload.get("current_mark", position_payload.get("entry_net_price")),
                position_payload.get("total_pnl_usd", 0.0),
                position_payload.get("total_pnl_pct", 0.0),
                position_payload.get("underlying_entry_price"),
                position_payload.get("iv_regime"),
                position_payload.get("iv_rank_30d"),
                position_payload.get("iv_rank_252d"),
                position_payload.get("options_pcr"),
                position_payload.get("options_gamma"),
                json.dumps(position_payload.get("notes"), default=str) if isinstance(position_payload.get("notes"), (dict, list)) else position_payload.get("notes"),
                position_payload.get("candidate_id"),
            ),
        )
        position_id = int(cur.lastrowid)
        for leg in legs:
            cur.execute(
                """
                INSERT INTO option_legs (
                    position_id, leg_role, contract_symbol, option_type, strike, expiry,
                    side_open, side_close, quantity, delta_at_entry, entry_price,
                    opened_at, state, notes
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?)
                """,
                (
                    position_id,
                    leg.get("leg_role"),
                    leg.get("contract_symbol"),
                    leg.get("option_type"),
                    leg.get("strike"),
                    leg.get("expiry"),
                    leg.get("side_open"),
                    leg.get("side_close"),
                    int(leg.get("quantity", 1) or 1),
                    leg.get("delta_at_entry"),
                    leg.get("entry_price"),
                    opened_at,
                    json.dumps(leg.get("notes"), default=str) if isinstance(leg.get("notes"), (dict, list)) else leg.get("notes"),
                ),
            )
        conn.commit()
        conn.close()
        return position_id

    def load_open_positions(self) -> List[sqlite3.Row]:
        conn = self._connect()
        rows = conn.execute(
            """
            SELECT * FROM option_positions
            WHERE UPPER(COALESCE(state, 'OPEN')) = 'OPEN'
            ORDER BY opened_at ASC, id ASC
            """
        ).fetchall()
        conn.close()
        return list(rows)

    def load_legs(self, position_id: int) -> List[sqlite3.Row]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM option_legs WHERE position_id = ? ORDER BY id ASC",
            (int(position_id),),
        ).fetchall()
        conn.close()
        return list(rows)

    def update_position_mark(
        self,
        position_id: int,
        *,
        current_mark: float,
        total_pnl_usd: float,
        total_pnl_pct: float,
        notes: Optional[Dict] = None,
    ) -> None:
        conn = self._connect()
        cur = conn.cursor()
        cur.execute("SELECT notes FROM option_positions WHERE id = ?", (int(position_id),))
        row = cur.fetchone()
        merged_notes = self._merge_notes(row["notes"] if row else None, notes)
        cur.execute(
            """
            UPDATE option_positions
            SET current_mark = ?, total_pnl_usd = ?, total_pnl_pct = ?,
                notes = COALESCE(?, notes)
            WHERE id = ?
            """,
            (
                float(current_mark),
                float(total_pnl_usd),
                float(total_pnl_pct),
                merged_notes,
                int(position_id),
            ),
        )
        conn.commit()
        conn.close()

    def close_position(
        self,
        position_id: int,
        *,
        close_reason: str,
        current_mark: float,
        total_pnl_usd: float,
        total_pnl_pct: float,
        underlying_exit_price: Optional[float] = None,
        notes: Optional[Dict] = None,
        leg_exit_prices: Optional[Dict[str, float]] = None,
        closed_at: Optional[str] = None,
    ) -> None:
        closed_at = closed_at or _utc_now_iso()
        conn = self._connect()
        cur = conn.cursor()
        cur.execute("SELECT notes FROM option_positions WHERE id = ?", (int(position_id),))
        row = cur.fetchone()
        merged_notes = self._merge_notes(row["notes"] if row else None, notes)
        cur.execute(
            """
            UPDATE option_positions
            SET state = 'CLOSED',
                closed_at = ?,
                exit_reason = ?,
                current_mark = ?,
                total_pnl_usd = ?,
                total_pnl_pct = ?,
                underlying_exit_price = COALESCE(?, underlying_exit_price),
                notes = COALESCE(?, notes)
            WHERE id = ?
            """,
            (
                closed_at,
                close_reason,
                float(current_mark),
                float(total_pnl_usd),
                float(total_pnl_pct),
                underlying_exit_price,
                merged_notes,
                int(position_id),
            ),
        )
        for leg in self.load_legs(position_id):
            exit_price = None
            if leg_exit_prices:
                exit_price = leg_exit_prices.get(str(leg["contract_symbol"]))
            cur.execute(
                """
                UPDATE option_legs
                SET closed_at = ?, exit_price = COALESCE(?, exit_price), state = 'CLOSED'
                WHERE id = ?
                """,
                (closed_at, exit_price, int(leg["id"])),
            )
        conn.commit()
        conn.close()

    @staticmethod
    def _merge_notes(existing, incoming) -> Optional[str]:
        if incoming is None:
            return existing
        if not isinstance(incoming, dict):
            return incoming
        payload = {}
        if existing:
            if isinstance(existing, dict):
                payload.update(existing)
            else:
                try:
                    parsed = json.loads(existing)
                    if isinstance(parsed, dict):
                        payload.update(parsed)
                except Exception:
                    pass
        payload.update(incoming)
        return json.dumps(payload, default=str)

    def upsert_iv_snapshot(
        self,
        *,
        ticker: str,
        date_str: str,
        atm_iv: float,
        iv_rank_30d: Optional[float],
        iv_rank_252d: Optional[float],
        iv_pct_252d: Optional[float],
        source: str,
    ) -> None:
        conn = self._connect()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO option_iv_history (
                ticker, date, atm_iv, iv_rank_30d, iv_rank_252d, iv_pct_252d,
                source, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker, date) DO UPDATE SET
                atm_iv = excluded.atm_iv,
                iv_rank_30d = excluded.iv_rank_30d,
                iv_rank_252d = excluded.iv_rank_252d,
                iv_pct_252d = excluded.iv_pct_252d,
                source = excluded.source,
                created_at = excluded.created_at
            """,
            (
                ticker.upper(),
                date_str,
                float(atm_iv),
                iv_rank_30d,
                iv_rank_252d,
                iv_pct_252d,
                source,
                _utc_now_iso(),
            ),
        )
        conn.commit()
        conn.close()

    def load_iv_series(self, ticker: str, limit: int = 252) -> List[sqlite3.Row]:
        conn = self._connect()
        rows = conn.execute(
            """
            SELECT * FROM option_iv_history
            WHERE ticker = ?
            ORDER BY date DESC
            LIMIT ?
            """,
            (ticker.upper(), int(limit)),
        ).fetchall()
        conn.close()
        return list(rows)

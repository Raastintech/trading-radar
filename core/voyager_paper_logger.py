"""
core/voyager_paper_logger.py — Voyager paper-validation signal logger.

Writes every Voyager signal that passes all gates (during the paper cycle)
to the voyager_paper_signals table in db/trading.db.

Outcome columns (outcome_30d, outcome_90d, outcome_180d) are NULL at signal
time. Run research/paper_trades/voyager_paper_report.py --update-outcomes
to fill them in once the measurement windows have elapsed.

Usage (auto-wired in strategies/voyager.py when VOYAGER_PAPER_LOG=true):
    from core.voyager_paper_logger import log_voyager_paper_signal
    signal_id = log_voyager_paper_signal(signal, vix, spy_above_ma50, spy_above_ma200)

Doctrine reminder: VOYAGER = LONG only. Every row in voyager_paper_signals
has direction='LONG'. SHORT logic belongs in strategies/short_sleeve.py.
"""
from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Dict, Optional

import core.config as cfg

logger = logging.getLogger(__name__)

# ── DDL (matches db/schema.sql — kept in sync here for auto-creation) ─────────
_DDL = """
CREATE TABLE IF NOT EXISTS voyager_paper_signals (
    id                       TEXT PRIMARY KEY,
    logged_at                TEXT NOT NULL,
    ticker                   TEXT NOT NULL,
    direction                TEXT NOT NULL DEFAULT 'LONG',
    archetype                TEXT NOT NULL,
    base_score               INTEGER NOT NULL,
    thirteen_f_pts           INTEGER NOT NULL DEFAULT 0,
    final_score              INTEGER NOT NULL,
    thirteen_f_flow          TEXT,
    thirteen_f_confidence    TEXT,
    thirteen_f_buying        INTEGER,
    thirteen_f_selling       INTEGER,
    thirteen_f_quarter       TEXT,
    size_bucket              TEXT,
    market_cap               REAL,
    entry_price              REAL NOT NULL,
    stop_loss                REAL NOT NULL,
    target_price             REAL NOT NULL,
    risk_reward              REAL NOT NULL,
    ma50                     REAL,
    ma200                    REAL,
    rs_50d                   REAL,
    rs_130d                  REAL,
    dvol_ratio               REAL,
    up_vol_ratio             REAL,
    extension_ma50           REAL,
    fund_score               INTEGER,
    fund_note                TEXT,
    vix_at_entry             REAL,
    spy_above_ma50           INTEGER,
    spy_above_ma200          INTEGER,
    outcome_30d              REAL,
    outcome_90d              REAL,
    outcome_180d             REAL,
    outcome_30d_date         TEXT,
    outcome_90d_date         TEXT,
    outcome_180d_date        TEXT,
    above_ma200_at_30d       INTEGER,
    signal_status            TEXT DEFAULT 'open',
    exit_price               REAL,
    exit_date                TEXT,
    exit_reason              TEXT,
    notes                    TEXT
);
CREATE INDEX IF NOT EXISTS idx_vps_ticker    ON voyager_paper_signals(ticker);
CREATE INDEX IF NOT EXISTS idx_vps_archetype ON voyager_paper_signals(archetype);
CREATE INDEX IF NOT EXISTS idx_vps_status    ON voyager_paper_signals(signal_status);
CREATE INDEX IF NOT EXISTS idx_vps_logged_at ON voyager_paper_signals(logged_at);
CREATE INDEX IF NOT EXISTS idx_vps_open_ticker ON voyager_paper_signals(ticker, signal_status);
"""

# ── Size-bucket classification ────────────────────────────────────────────────
# Thresholds applied to FMP profile.marketCap (current, not historical).
# For live paper signals this is the most actionable proxy available.
_LARGE_CAP_THRESHOLD    = 50_000_000_000   # >$50B
_MID_CAP_THRESHOLD      =  5_000_000_000   # >$5B
_EMERGING_CAP_THRESHOLD =    500_000_000   # >$500M


def _classify_size(market_cap: Optional[float]) -> str:
    if market_cap is None or market_cap <= 0:
        return "unknown"
    if market_cap >= _LARGE_CAP_THRESHOLD:
        return "large"
    if market_cap >= _MID_CAP_THRESHOLD:
        return "mid"
    if market_cap >= _EMERGING_CAP_THRESHOLD:
        return "emerging"
    return "micro"    # below $500M — will typically fail dollar-vol gate anyway


def _get_size_bucket(ticker: str) -> tuple[Optional[float], str]:
    """
    Fetches market cap from FMP profile and returns (market_cap, size_bucket).
    Silently returns (None, 'unknown') on any error.
    """
    try:
        from core.fmp_client import get_fmp
        profile = get_fmp().get_company_profile(ticker)
        if profile:
            mktcap = profile.get("marketCap") or 0
            return mktcap, _classify_size(mktcap)
    except Exception as exc:
        logger.debug("Size bucket fetch failed %s: %s", ticker, exc)
    return None, "unknown"


# ── DB connection (module-level singleton) ────────────────────────────────────
_conn: Optional[sqlite3.Connection] = None


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        from core.db import connect as _hardened_connect
        _conn = _hardened_connect(str(cfg.DB_PATH), check_same_thread=False)
        _conn.executescript(_DDL)
        _conn.commit()
    return _conn


def _existing_open_signal_id(conn: sqlite3.Connection, ticker: str) -> Optional[str]:
    """
    Return the newest open Voyager paper signal for ticker, if one exists.

    Paper validation measures one active thesis per ticker. Re-logging the same
    open ticker every scan inflates the sample and violates paper-governance
    concentration rules, so duplicates are skipped.
    """
    row = conn.execute(
        """
        SELECT id
          FROM voyager_paper_signals
         WHERE ticker=? AND signal_status='open'
         ORDER BY logged_at DESC
         LIMIT 1
        """,
        (ticker,),
    ).fetchone()
    return str(row[0]) if row else None


# ── Public API ────────────────────────────────────────────────────────────────

def log_voyager_paper_signal(
    signal: Dict,
    vix: Optional[float] = None,
    spy_above_ma50: Optional[bool] = None,
    spy_above_ma200: Optional[bool] = None,
    notes: str = "",
) -> str:
    """
    Log a Voyager paper signal to voyager_paper_signals.

    Args:
        signal:         Full signal dict from VoyagerScanner._evaluate().
        vix:            VIX level at time of signal (from FMP get_vix()).
        spy_above_ma50: True if SPY close > SPY MA50 at scan time.
        spy_above_ma200:True if SPY close > SPY MA200 at scan time.
        notes:          Optional free-text annotation.

    Returns:
        signal_id (UUID string) — store this if you need to update outcomes later.
    """
    ticker = signal.get("ticker", "").upper()
    conn = _get_conn()

    existing_id = _existing_open_signal_id(conn, ticker)
    if existing_id:
        logger.info(
            "VOYAGER paper signal skipped: %s already has open paper signal id=%s",
            ticker,
            existing_id[:8],
        )
        return existing_id

    market_cap, size_bucket = _get_size_bucket(ticker)
    signal_id = str(uuid.uuid4())
    logged_at = datetime.now(timezone.utc).isoformat()

    with conn:
        conn.execute(
            """
            INSERT INTO voyager_paper_signals (
                id, logged_at, ticker, direction, archetype,
                base_score, thirteen_f_pts, final_score,
                thirteen_f_flow, thirteen_f_confidence,
                thirteen_f_buying, thirteen_f_selling, thirteen_f_quarter,
                size_bucket, market_cap,
                entry_price, stop_loss, target_price, risk_reward,
                ma50, ma200,
                rs_50d, rs_130d, dvol_ratio, up_vol_ratio, extension_ma50,
                fund_score, fund_note,
                vix_at_entry, spy_above_ma50, spy_above_ma200,
                signal_status, notes
            ) VALUES (
                ?,?,?,?,?,  ?,?,?,  ?,?,  ?,?,?,  ?,?,
                ?,?,?,?,  ?,?,  ?,?,?,?,?,  ?,?,  ?,?,?,  ?,?
            )
            """,
            (
                signal_id, logged_at,
                ticker,
                "LONG",                                             # always LONG for Voyager
                signal.get("archetype", ""),
                signal.get("base_score", 0),
                signal.get("thirteen_f_pts", 0),
                signal.get("score", 0),
                signal.get("thirteen_f_flow"),
                signal.get("thirteen_f_confidence"),
                signal.get("thirteen_f_buying"),
                signal.get("thirteen_f_selling"),
                signal.get("thirteen_f_quarter"),
                size_bucket,
                market_cap,
                signal.get("entry_price", 0),
                signal.get("stop_loss", 0),
                signal.get("target_price", 0),
                signal.get("risk_reward", 0),
                signal.get("ma50"),
                signal.get("ma200"),
                signal.get("rs_50d"),
                signal.get("rs_130d"),
                signal.get("dvol_ratio"),
                signal.get("up_vol_ratio"),
                signal.get("extension_ma50"),
                signal.get("fund_score"),
                signal.get("fund_note"),
                vix,
                int(spy_above_ma50)  if spy_above_ma50  is not None else None,
                int(spy_above_ma200) if spy_above_ma200 is not None else None,
                "open",
                notes,
            ),
        )

    logger.info(
        "VOYAGER paper signal logged: %s  arch=%s  score=%d  13F=%+d  "
        "size=%s  entry=%.2f  stop=%.2f  id=%s",
        ticker,
        signal.get("archetype", "?"),
        signal.get("score", 0),
        signal.get("thirteen_f_pts", 0),
        size_bucket,
        signal.get("entry_price", 0),
        signal.get("stop_loss", 0),
        signal_id[:8],
    )
    return signal_id


def update_outcome(
    signal_id: str,
    horizon: str,                   # "30d" | "90d" | "180d"
    return_pct: float,
    above_ma200: Optional[bool] = None,
    measurement_date: Optional[str] = None,
) -> None:
    """
    Fill in an outcome column for a paper signal.

    Args:
        signal_id:        UUID from log_voyager_paper_signal().
        horizon:          "30d", "90d", or "180d".
        return_pct:       % return from entry price (e.g. +15.3 or -4.2).
        above_ma200:      True if ticker was above MA200 at measurement date.
        measurement_date: ISO date string (defaults to today UTC).
    """
    if horizon not in ("30d", "90d", "180d"):
        raise ValueError(f"horizon must be 30d/90d/180d, got {horizon!r}")

    mdate = measurement_date or datetime.now(timezone.utc).date().isoformat()
    col   = f"outcome_{horizon}"
    dcol  = f"outcome_{horizon}_date"

    conn = _get_conn()
    with conn:
        if horizon == "30d" and above_ma200 is not None:
            conn.execute(
                f"UPDATE voyager_paper_signals SET {col}=?, {dcol}=?, above_ma200_at_30d=? WHERE id=?",
                (round(return_pct, 2), mdate, int(above_ma200), signal_id),
            )
        else:
            conn.execute(
                f"UPDATE voyager_paper_signals SET {col}=?, {dcol}=? WHERE id=?",
                (round(return_pct, 2), mdate, signal_id),
            )
    logger.info("Outcome updated: %s  %s=%.2f%%", signal_id[:8], horizon, return_pct)


def mark_stopped_out(signal_id: str, exit_price: float, exit_date: Optional[str] = None) -> None:
    """Mark a paper signal as stopped out."""
    edate = exit_date or datetime.now(timezone.utc).date().isoformat()
    conn = _get_conn()
    with conn:
        conn.execute(
            """UPDATE voyager_paper_signals
               SET signal_status='stopped_out', exit_price=?, exit_date=?, exit_reason='stopped_out'
               WHERE id=?""",
            (exit_price, edate, signal_id),
        )
    logger.info("Paper signal stopped out: %s  exit=%.2f", signal_id[:8], exit_price)


def get_open_signals() -> list[Dict]:
    """Return all paper signals with status='open'."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM voyager_paper_signals WHERE signal_status='open' ORDER BY logged_at DESC"
    ).fetchall()
    cols = [d[1] for d in conn.execute("PRAGMA table_info(voyager_paper_signals)").fetchall()]
    return [dict(zip(cols, r)) for r in rows]


def get_all_signals() -> list[Dict]:
    """Return all paper signals, ordered newest-first."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM voyager_paper_signals ORDER BY logged_at DESC"
    ).fetchall()
    cols = [d[1] for d in conn.execute("PRAGMA table_info(voyager_paper_signals)").fetchall()]
    return [dict(zip(cols, r)) for r in rows]

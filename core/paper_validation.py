"""
core/paper_validation.py — Unified paper-validation signal ledger.

This module is intentionally strategy-agnostic. Strategy scanners can log paper
signals here after a signal qualifies for the current paper sleeve set. The
existing Voyager-specific logger remains valid; this table is the shared path
for SNIPER v6, SHORT Sleeve A, and any future active paper sleeve.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional

import core.config as cfg
from core.strategy_registry import active_paper_tags, frozen_strategies, is_active_paper_strategy, normalize_strategy

logger = logging.getLogger(__name__)

ACTIVE_PAPER_SLEEVES = active_paper_tags()
INACTIVE_RESEARCH_SLEEVES = {strategy: "RESEARCH_ONLY" for strategy in frozen_strategies()}

_DDL = """
CREATE TABLE IF NOT EXISTS paper_signals (
    id                    TEXT PRIMARY KEY,
    logged_at             TEXT NOT NULL,
    strategy              TEXT NOT NULL,
    sleeve                TEXT NOT NULL,
    ticker                TEXT NOT NULL,
    side                  TEXT NOT NULL,
    signal_version        TEXT NOT NULL,
    entry_price           REAL NOT NULL,
    stop_loss             REAL,
    target_price          REAL,
    risk_reward           REAL,
    score                 REAL,
    sector                TEXT,
    regime_context        TEXT,
    key_features          TEXT,
    allocation_bucket     TEXT,
    allocation_pct        REAL,
    qualified_reason      TEXT,
    notes                 TEXT,
    status                TEXT NOT NULL DEFAULT 'open',
    exit_price            REAL,
    exit_date             TEXT,
    exit_reason           TEXT
);

-- UNITS NOTE (Phase 1F): return_pct / adjusted_return_pct / mae_pct / mfe_pct
-- are stored as PERCENT (e.g. +14.19 means +14.19%), NOT as fractions.
-- This is intentionally inconsistent with decisions.pnl_pct which is stored
-- as a FRACTION (0.1419 == 14.19%). Any cross-table comparison MUST normalize.
CREATE TABLE IF NOT EXISTS paper_signal_outcomes (
    id                    TEXT PRIMARY KEY,
    signal_id             TEXT NOT NULL,
    horizon_days          INTEGER NOT NULL,
    outcome_date          TEXT,
    measured_at           TEXT NOT NULL,
    return_pct            REAL,
    adjusted_return_pct   REAL,
    stop_hit              INTEGER,
    target_hit            INTEGER,
    still_open            INTEGER,
    hold_complete         INTEGER,
    mae_pct               REAL,
    mfe_pct               REAL,
    path                  TEXT,
    notes                 TEXT,
    UNIQUE(signal_id, horizon_days),
    FOREIGN KEY(signal_id) REFERENCES paper_signals(id)
);

CREATE INDEX IF NOT EXISTS idx_ps_strategy ON paper_signals(strategy);
CREATE INDEX IF NOT EXISTS idx_ps_sleeve   ON paper_signals(sleeve);
CREATE INDEX IF NOT EXISTS idx_ps_ticker   ON paper_signals(ticker);
CREATE INDEX IF NOT EXISTS idx_ps_status   ON paper_signals(status);
CREATE INDEX IF NOT EXISTS idx_ps_logged   ON paper_signals(logged_at);
CREATE INDEX IF NOT EXISTS idx_pso_signal  ON paper_signal_outcomes(signal_id);
"""

_conn: Optional[sqlite3.Connection] = None

_OUTCOME_MIGRATIONS = {
    "outcome_date": "ALTER TABLE paper_signal_outcomes ADD COLUMN outcome_date TEXT",
    "still_open": "ALTER TABLE paper_signal_outcomes ADD COLUMN still_open INTEGER",
    "hold_complete": "ALTER TABLE paper_signal_outcomes ADD COLUMN hold_complete INTEGER",
}

# Phase 12B — additive instrumentation column for H3 forward OOS testing.
# JSON blob; nullable; readers tolerant of NULL won't break.
# Phase 1G.2 — additive identity column for scanner-side signal dedup.
# Short hex string; nullable; the hygiene layer in core/signal_hygiene.py
# computes the hash from the same fields stored on the row.
_PAPER_SIGNALS_MIGRATIONS = {
    "aux_h3": "ALTER TABLE paper_signals ADD COLUMN aux_h3 TEXT",
    "setup_state_hash": "ALTER TABLE paper_signals ADD COLUMN setup_state_hash TEXT",
}


def _json_blob(value: Optional[Dict[str, Any]]) -> str:
    return json.dumps(value or {}, sort_keys=True)


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        from core.db import connect as _hardened_connect
        _conn = _hardened_connect(str(cfg.DB_PATH), check_same_thread=False)
        _conn.executescript(_DDL)
        _migrate_schema(_conn)
        _conn.commit()
    return _conn


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Apply additive migrations for databases created before this module."""
    cols = _table_columns(conn, "paper_signal_outcomes")
    for col, ddl in _OUTCOME_MIGRATIONS.items():
        if col not in cols:
            conn.execute(ddl)
    sig_cols = _table_columns(conn, "paper_signals")
    for col, ddl in _PAPER_SIGNALS_MIGRATIONS.items():
        if col not in sig_cols:
            conn.execute(ddl)


# ──────────────────────────────────────────────────────────────────────────────
# Phase 12B — SNIPER H3 forward-OOS metadata helper.
#
# Scope: instrumentation only. compute_h3_metadata() never fails. If any input
# is missing, the corresponding bucket is "missing" and the relevant gate
# evaluates to False (the candidate-flag tolerates missing inputs by treating
# them as gate-fail, never as crash).
# ──────────────────────────────────────────────────────────────────────────────

# Sector canonicalisation for the H3 sector gate. Inputs come from disparate
# upstream sources (scanner sector strings, FMP profile, manual map). We only
# need to know whether the sector is in {Healthcare, Communications, Technology}.
_H3_SECTOR_ALIASES: Dict[str, str] = {
    "technology": "Technology",
    "tech": "Technology",
    "information technology": "Technology",
    "communications": "Communications",
    "communication services": "Communications",
    "communication": "Communications",
    "comm": "Communications",
    "comm services": "Communications",
    "healthcare": "Healthcare",
    "health care": "Healthcare",
    "health": "Healthcare",
}
H3_ALLOWED_SECTORS = {"Healthcare", "Communications", "Technology"}


def _h3_score_bucket(score: Optional[float]) -> str:
    if score is None:
        return "missing"
    try:
        s = float(score)
    except Exception:
        return "missing"
    if 80.0 <= s < 90.0:
        return "80-89"
    if s >= 90.0:
        return "90+"
    return "other"


def _h3_vix_bucket(vix: Optional[float]) -> str:
    if vix is None:
        return "missing"
    try:
        v = float(vix)
    except Exception:
        return "missing"
    if v < 15.0:
        return "low (<15)"
    if v < 20.0:
        return "normal (15-20)"
    if v < 30.0:
        return "elevated (20-30)"
    return "high (>=30)"


def _h3_vol_ratio_bucket(vr: Optional[float]) -> str:
    if vr is None:
        return "missing"
    try:
        x = float(vr)
    except Exception:
        return "missing"
    if x < 1.5:
        return "<1.5"
    if x < 2.0:
        return "1.5-2.0"
    return ">2.0"


def _h3_sector_canonical(sector: Optional[str]) -> Optional[str]:
    if not sector:
        return None
    key = str(sector).strip().lower()
    if key in _H3_SECTOR_ALIASES:
        return _H3_SECTOR_ALIASES[key]
    # Fall through: pass through the original value (possibly e.g. "Energy")
    # so the report can still aggregate by raw sector text.
    return str(sector).strip()


def _h3_sector_bucket(canonical_sector: Optional[str]) -> str:
    if canonical_sector is None:
        return "missing"
    if canonical_sector in H3_ALLOWED_SECTORS:
        return "h3_allowed"
    return "h3_disallowed"


def compute_h3_metadata(
    *,
    sniper_score: Optional[float] = None,
    vix_value: Optional[float] = None,
    volume_ratio: Optional[float] = None,
    sector: Optional[str] = None,
    ticker: Optional[str] = None,
    side: Optional[str] = None,
    entry_date: Optional[str] = None,
    baseline_tag: Optional[str] = None,
    daily_entry_validator_state: Optional[str] = None,
    market_forecast_regime: Optional[str] = None,
    market_forecast_bias_5d: Optional[str] = None,
    market_forecast_bias_10d: Optional[str] = None,
    market_posture_bias: Optional[str] = None,
    options_quality: Optional[str] = None,
    stock_extension_state: Optional[str] = None,
    alpha_discovery_state: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the Phase 12B H3 metadata dict for a SNIPER paper signal.

    Never raises. Missing inputs are recorded as None / "missing" buckets, and
    the corresponding H3 gate evaluates to False rather than crashing.

    Returns a JSON-serializable dict with:
      - the raw inputs (sniper_score, vix_value, …)
      - the H3 buckets (score_bucket, vix_bucket, vol_ratio_bucket, sector_bucket)
      - the auxiliary research-context state (DEV / Market Forecast / etc.)
      - h3_candidate (True iff all four gates pass)
      - h3_reason: per-gate pass/fail dict so the forward report can attribute
                   why a signal failed the cohort.
    """
    score_bucket = _h3_score_bucket(sniper_score)
    vix_bucket = _h3_vix_bucket(vix_value)
    vol_bucket = _h3_vol_ratio_bucket(volume_ratio)
    sector_canon = _h3_sector_canonical(sector)
    sector_bucket = _h3_sector_bucket(sector_canon)

    gate_score = score_bucket == "80-89"
    gate_vix = vix_bucket == "normal (15-20)"
    gate_vol = vol_bucket == "<1.5"
    gate_sector = sector_bucket == "h3_allowed"
    h3_candidate = bool(gate_score and gate_vix and gate_vol and gate_sector)

    h3_reason = {
        "score_80_89_pass": gate_score,
        "vix_15_20_pass": gate_vix,
        "vol_ratio_lt_1_5_pass": gate_vol,
        "sector_in_HC_COMM_TECH_pass": gate_sector,
    }

    return {
        "schema_version": "h3.v1",
        "phase_introduced": "12B",
        # Identity / context
        "ticker": (ticker or None),
        "side": (side or None),
        "entry_date": entry_date,
        "baseline_tag": baseline_tag,
        # Raw H3 inputs
        "sniper_score": sniper_score,
        "vix_value": vix_value,
        "volume_ratio": volume_ratio,
        "sector": sector,
        "sector_canonical": sector_canon,
        # Buckets (the report aggregates on these)
        "score_bucket": score_bucket,
        "vix_bucket": vix_bucket,
        "volume_ratio_bucket": vol_bucket,
        "sector_bucket": sector_bucket,
        # Auxiliary research-context state (best-effort; null when unavailable)
        "daily_entry_validator_state": daily_entry_validator_state,
        "market_forecast_regime": market_forecast_regime,
        "market_forecast_bias_5d": market_forecast_bias_5d,
        "market_forecast_bias_10d": market_forecast_bias_10d,
        "market_posture_bias": market_posture_bias,
        "options_quality": options_quality,
        "stock_extension_state": stock_extension_state,
        "alpha_discovery_state": alpha_discovery_state,
        # Cohort verdict
        "h3_candidate": h3_candidate,
        "h3_reason": h3_reason,
    }


def safe_compute_h3_metadata(**kwargs: Any) -> Optional[str]:
    """JSON-serialise compute_h3_metadata; return None on any failure.

    Used at the call site so a logging error never breaks signal emission.
    """
    try:
        meta = compute_h3_metadata(**kwargs)
        return json.dumps(meta, sort_keys=True, default=str)
    except Exception:
        logger.exception("compute_h3_metadata failed; persisting NULL aux_h3")
        return None


def _validate_immutable_baseline(strategy: str, sleeve: Optional[str], signal_version: Optional[str]) -> None:
    """
    Active paper baseline tags are immutable identifiers.

    If strategy logic changes later, callers should introduce a new tag instead
    of silently reusing the current baseline identifier.
    """
    expected = ACTIVE_PAPER_SLEEVES.get(strategy.upper())
    if not expected:
        return
    if signal_version is not None and signal_version != expected:
        raise ValueError(
            f"{strategy.upper()} paper baseline tag is immutable: expected {expected}, got {signal_version}"
        )
    if sleeve is not None and sleeve != expected:
        raise ValueError(
            f"{strategy.upper()} paper sleeve tag is immutable: expected {expected}, got {sleeve}"
        )


def ensure_schema() -> None:
    """Create paper validation tables if they do not already exist."""
    _get_conn()


def log_paper_signal(
    *,
    strategy: str,
    ticker: str,
    side: str,
    entry_price: float,
    sleeve: Optional[str] = None,
    signal_version: Optional[str] = None,
    stop_loss: Optional[float] = None,
    target_price: Optional[float] = None,
    risk_reward: Optional[float] = None,
    score: Optional[float] = None,
    sector: str = "",
    regime_context: Optional[Dict[str, Any]] = None,
    key_features: Optional[Dict[str, Any]] = None,
    allocation_bucket: str = "",
    allocation_pct: Optional[float] = None,
    qualified_reason: str = "",
    notes: str = "",
    status: str = "open",
    aux_h3: Optional[str] = None,
    setup_state_hash: Optional[str] = None,
) -> str:
    """
    Insert a paper signal and return its UUID.

    The function does not execute orders and does not approve a signal. It only
    records the state needed for later paper validation.

    Phase 12B: optional `aux_h3` JSON blob (built via `compute_h3_metadata` at
    the call site). Other strategies should leave it None. Persisted column is
    nullable; readers tolerant of NULL won't break.

    Phase 1G.2: optional `setup_state_hash` short identity string (built via
    `core.signal_hygiene.setup_state_hash`). Used by the scanner-side dedup
    layer to detect same-day re-emissions of the same setup. Nullable; legacy
    rows without it remain valid.
    """
    strat = normalize_strategy(strategy)
    if not is_active_paper_strategy(strat):
        raise ValueError(f"{strat or 'UNKNOWN'} is not active in the paper-validation phase")
    _validate_immutable_baseline(strat, sleeve, signal_version)
    sig_id = str(uuid.uuid4())
    conn = _get_conn()
    with conn:
        conn.execute(
            """
            INSERT INTO paper_signals (
                id, logged_at, strategy, sleeve, ticker, side, signal_version,
                entry_price, stop_loss, target_price, risk_reward, score, sector,
                regime_context, key_features, allocation_bucket, allocation_pct,
                qualified_reason, notes, status, aux_h3, setup_state_hash
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                sig_id,
                datetime.now(timezone.utc).isoformat(),
                strat,
                sleeve or ACTIVE_PAPER_SLEEVES.get(strat, strat),
                ticker.upper(),
                side.upper(),
                signal_version or ACTIVE_PAPER_SLEEVES.get(strat, "UNSPECIFIED"),
                float(entry_price),
                stop_loss,
                target_price,
                risk_reward,
                score,
                sector,
                _json_blob(regime_context),
                _json_blob(key_features),
                allocation_bucket,
                allocation_pct,
                qualified_reason,
                notes,
                status,
                aux_h3,
                setup_state_hash,
            ),
        )
    logger.info("Paper signal logged: %s %s %s status=%s id=%s", strat, side, ticker, status, sig_id[:8])
    return sig_id


def record_paper_outcome(
    signal_id: str,
    horizon_days: int,
    *,
    outcome_date: Optional[str] = None,
    return_pct: Optional[float] = None,
    adjusted_return_pct: Optional[float] = None,
    stop_hit: Optional[bool] = None,
    target_hit: Optional[bool] = None,
    still_open: Optional[bool] = None,
    hold_complete: Optional[bool] = None,
    mae_pct: Optional[float] = None,
    mfe_pct: Optional[float] = None,
    path: str = "",
    notes: str = "",
) -> None:
    """Upsert one measured paper outcome for a logged signal."""
    conn = _get_conn()
    with conn:
        conn.execute(
            """
            INSERT INTO paper_signal_outcomes (
                id, signal_id, horizon_days, outcome_date, measured_at,
                return_pct, adjusted_return_pct, stop_hit, target_hit,
                still_open, hold_complete, mae_pct, mfe_pct, path, notes
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(signal_id, horizon_days) DO UPDATE SET
                outcome_date=excluded.outcome_date,
                measured_at=excluded.measured_at,
                return_pct=excluded.return_pct,
                adjusted_return_pct=excluded.adjusted_return_pct,
                stop_hit=excluded.stop_hit,
                target_hit=excluded.target_hit,
                still_open=excluded.still_open,
                hold_complete=excluded.hold_complete,
                mae_pct=excluded.mae_pct,
                mfe_pct=excluded.mfe_pct,
                path=excluded.path,
                notes=excluded.notes
            """,
            (
                str(uuid.uuid4()),
                signal_id,
                int(horizon_days),
                outcome_date,
                datetime.now(timezone.utc).isoformat(),
                return_pct,
                adjusted_return_pct,
                int(stop_hit) if stop_hit is not None else None,
                int(target_hit) if target_hit is not None else None,
                int(still_open) if still_open is not None else None,
                int(hold_complete) if hold_complete is not None else None,
                mae_pct,
                mfe_pct,
                path,
                notes,
            ),
        )


def mark_paper_signal_closed(
    signal_id: str,
    *,
    exit_price: Optional[float] = None,
    exit_reason: str = "closed",
    exit_date: Optional[str] = None,
) -> None:
    """Mark a paper signal as closed/stopped/target/timeout/invalidated."""
    conn = _get_conn()
    with conn:
        conn.execute(
            """
            UPDATE paper_signals
               SET status=?, exit_price=?, exit_date=?, exit_reason=?
             WHERE id=?
            """,
            (
                exit_reason,
                exit_price,
                exit_date or datetime.now(timezone.utc).date().isoformat(),
                exit_reason,
                signal_id,
            ),
        )


def fetch_paper_signals(status: Optional[str] = None) -> list[Dict[str, Any]]:
    conn = _get_conn()
    if status:
        rows = conn.execute(
            "SELECT * FROM paper_signals WHERE status=? ORDER BY logged_at DESC",
            (status,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM paper_signals ORDER BY logged_at DESC").fetchall()
    cols = [d[1] for d in conn.execute("PRAGMA table_info(paper_signals)").fetchall()]
    return [dict(zip(cols, row)) for row in rows]


def fetch_paper_outcomes(signal_ids: Iterable[str]) -> list[Dict[str, Any]]:
    ids = [s for s in signal_ids if s]
    if not ids:
        return []
    conn = _get_conn()
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT * FROM paper_signal_outcomes WHERE signal_id IN ({placeholders})",
        ids,
    ).fetchall()
    cols = [d[1] for d in conn.execute("PRAGMA table_info(paper_signal_outcomes)").fetchall()]
    return [dict(zip(cols, row)) for row in rows]

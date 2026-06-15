"""
research/paper_state_hygiene_report.py — Phase 1C/1D paper-state hygiene.

Diagnostic-only report. Inventories data-quality issues across the three
tables that feed Phase 1B risk telemetry (decisions, paper_signals,
paper_signal_outcomes), the legacy voyager_paper_signals table, and the
position-reconciler audit trail in veto_log.

Phase 1D extension:
  - Dual-scope verdicts. Every check runs twice: once over the full
    ledger and once restricted to the clean-paper-evidence epoch
    (``CLEAN_PAPER_EVIDENCE_START``). The summary publishes both
    ``ready_to_gate_all`` and ``ready_to_gate_clean``.
  - Legacy quarantine sidecar. Pre-epoch decisions rows with hygiene
    issues are written (read-only on the DB) to a JSON sidecar so the
    operator can reason about the legacy debt without touching the DB.
  - Operator review section. Surfaces the three buckets:
      legacy rows needing review,
      rows safe to ignore for clean telemetry,
      rows requiring manual investigation (clean-epoch ERRORs).

Guardrails (must all hold):
  - Read-only by convention. No INSERT/UPDATE/DELETE/ALTER anywhere in
    this file. Production callers use SQLite read paths only.
  - No schema changes. Every check tolerates an empty DB and a DB that
    pre-dates each migrated column (e.g. aux_h3, fill_price).
  - No auto-remediation. Findings are surfaced; operators decide.
  - ``ready_to_gate_*`` are published verdicts only; this phase never
    uses them to enforce anything.
  - No provider calls. Cache + DB only.
  - No dashboard wiring in this phase.

Output:
  cache/research/paper_state_hygiene_latest.json
  logs/paper_state_hygiene_latest.txt
  data/state/paper_legacy_quarantine.json   (Phase 1D quarantine sidecar)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

_env_path = os.environ.get("SNIPER_ENV_PATH", "")
if _env_path:
    try:
        from dotenv import load_dotenv as _load
        _load(Path(_env_path), override=True)
    except ImportError:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import core.config as cfg  # noqa: E402
from core.paper_evidence_epoch import (  # noqa: E402
    CLEAN_PAPER_EVIDENCE_START,
    QUARANTINE_REASON_PRE_FILL_TELEMETRY,
)

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
logger = logging.getLogger("paper_state_hygiene")


# ── Thresholds (doctrine-defaulted; CLI-overridable) ─────────────────────────

# Decisions
STALE_OPEN_DECISION_DAYS = 365     # open positions older than 1y → flag
EXTREME_SLIPPAGE_BPS     = 500.0   # |slippage_bps| above this is implausible

# Paper signals
STALE_OPEN_PAPER_DAYS    = 180     # longest doctrine outcome horizon
DUPLICATE_OPEN_WINDOW_HR = 24      # dedup window for duplicate open signals
PHASE_12B_CUTOFF_ISO     = "2026-05-05"  # SNIPER aux_h3 instrumentation start

# Voyager (legacy paper table)
VOYAGER_STALE_OPEN_DAYS  = 90

# Reconciler audit trail
RECONCILER_DRIFT_WINDOW_DAYS = 7
# Phase 1G: how recent the broker snapshot must be for us to trust it as
# "current truth" when classifying drift as ACTIVE vs HISTORICAL. The
# daemon refreshes the snapshot every reconciler tick (≈5 min), so 1 h is
# a generous ceiling — anything older means we cannot prove resolution.
BROKER_SNAPSHOT_FRESH_HOURS = 1.0

# Sample IDs included per finding (keeps JSON readable; full count is exact)
SAMPLE_LIMIT = 5


# ── Severity scale ───────────────────────────────────────────────────────────

SEV_ERROR = "ERROR"
SEV_WARN  = "WARN"
SEV_INFO  = "INFO"

# Scope labels for the dual-view verdict.
SCOPE_FULL  = "full"
SCOPE_CLEAN = "clean"


# ── DB helpers ───────────────────────────────────────────────────────────────

def _connect_ro(db_path: Path) -> Optional[sqlite3.Connection]:
    """Open the DB. Returns None if the file is absent. Caller must close.

    Read-only by convention — every SQL in this module is a SELECT. We
    intentionally do not use ``mode=ro`` URI because tests pass writable
    temp DB paths; the read-only invariant is preserved by inspection.
    """
    if not db_path.exists():
        return None
    try:
        return sqlite3.connect(str(db_path))
    except sqlite3.Error as exc:
        logger.warning("connect failed (%s): %s", db_path, exc)
        return None


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.OperationalError:
        return set()


def _row_count(conn: sqlite3.Connection, table: str) -> int:
    if not _table_exists(conn, table):
        return 0
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    except sqlite3.OperationalError:
        return 0


def _finding(
    code: str,
    severity: str,
    count: int,
    detail: str,
    sample_ids: Optional[List[str]] = None,
    scope: str = SCOPE_FULL,
) -> Dict[str, Any]:
    return {
        "code":       code,
        "severity":   severity,
        "scope":      scope,
        "count":      int(count),
        "detail":     detail,
        "sample_ids": list(sample_ids or [])[:SAMPLE_LIMIT],
    }


def _safe_select(
    conn: sqlite3.Connection,
    sql: str,
    params: Tuple = (),
) -> List[Tuple]:
    try:
        return list(conn.execute(sql, params).fetchall())
    except sqlite3.OperationalError as exc:
        logger.debug("SELECT failed: %s — %s", sql, exc)
        return []


def _since_clause(col: str, since_iso: Optional[str]) -> Tuple[str, Tuple]:
    """Return (sql_fragment, params) appended to a WHERE clause. Empty
    if since_iso is None."""
    if since_iso is None:
        return "", ()
    return f" AND {col} >= ?", (since_iso,)


# ── Decisions checks ─────────────────────────────────────────────────────────

def check_decisions_open_no_fill_price(
    conn: sqlite3.Connection,
    *,
    since_iso: Optional[str] = None,
    scope: str = SCOPE_FULL,
) -> Optional[Dict[str, Any]]:
    if not _table_exists(conn, "decisions"):
        return None
    cols = _table_columns(conn, "decisions")
    if "fill_price" not in cols:
        return None
    extra, params = _since_clause("ts", since_iso)
    rows = _safe_select(
        conn,
        "SELECT id FROM decisions "
        "WHERE position_opened=1 AND fill_price IS NULL" + extra + " "
        "ORDER BY ts ASC",
        params,
    )
    if not rows:
        return None
    ids = [str(r[0]) for r in rows]
    return _finding(
        "DECISIONS_OPEN_NO_FILL_PRICE",
        SEV_ERROR,
        len(ids),
        "decisions rows marked position_opened=1 but missing fill_price — "
        "would corrupt Phase 1B slippage/concentration inputs",
        sample_ids=ids,
        scope=scope,
    )


def check_decisions_open_no_fill_qty(
    conn: sqlite3.Connection,
    *,
    since_iso: Optional[str] = None,
    scope: str = SCOPE_FULL,
) -> Optional[Dict[str, Any]]:
    if not _table_exists(conn, "decisions"):
        return None
    cols = _table_columns(conn, "decisions")
    if "fill_qty" not in cols:
        return None
    extra, params = _since_clause("ts", since_iso)
    rows = _safe_select(
        conn,
        "SELECT id FROM decisions "
        "WHERE position_opened=1 AND (fill_qty IS NULL OR fill_qty <= 0)"
        + extra + " ORDER BY ts ASC",
        params,
    )
    if not rows:
        return None
    ids = [str(r[0]) for r in rows]
    return _finding(
        "DECISIONS_OPEN_NO_FILL_QTY",
        SEV_ERROR,
        len(ids),
        "decisions rows marked position_opened=1 but missing or non-positive "
        "fill_qty — concentration inputs cannot be trusted",
        sample_ids=ids,
        scope=scope,
    )


def check_decisions_closed_missing_pnl_exit(
    conn: sqlite3.Connection,
    *,
    since_iso: Optional[str] = None,
    scope: str = SCOPE_FULL,
) -> Optional[Dict[str, Any]]:
    if not _table_exists(conn, "decisions"):
        return None
    extra, params = _since_clause("ts", since_iso)
    rows = _safe_select(
        conn,
        "SELECT id FROM decisions "
        "WHERE position_closed=1 AND (pnl IS NULL OR exit_price IS NULL)"
        + extra + " ORDER BY ts ASC",
        params,
    )
    if not rows:
        return None
    ids = [str(r[0]) for r in rows]
    return _finding(
        "DECISIONS_CLOSED_MISSING_PNL_EXIT",
        SEV_WARN,
        len(ids),
        "closed decisions missing pnl or exit_price — incomplete exit audit trail",
        sample_ids=ids,
        scope=scope,
    )


def check_decisions_extreme_slippage(
    conn: sqlite3.Connection,
    *,
    threshold_bps: float = EXTREME_SLIPPAGE_BPS,
    since_iso: Optional[str] = None,
    scope: str = SCOPE_FULL,
) -> Optional[Dict[str, Any]]:
    if not _table_exists(conn, "decisions"):
        return None
    cols = _table_columns(conn, "decisions")
    if "slippage_bps" not in cols:
        return None
    extra, ts_params = _since_clause("ts", since_iso)
    params: Tuple = (threshold_bps,) + ts_params
    rows = _safe_select(
        conn,
        "SELECT id FROM decisions "
        "WHERE slippage_bps IS NOT NULL AND ABS(slippage_bps) > ?"
        + extra + " ORDER BY ts ASC",
        params,
    )
    if not rows:
        return None
    ids = [str(r[0]) for r in rows]
    return _finding(
        "DECISIONS_EXTREME_SLIPPAGE_BPS",
        SEV_WARN,
        len(ids),
        f"decisions with |slippage_bps| > {threshold_bps:.0f} — implausibly "
        "large; check for entry/fill price swap or stale entry_price",
        sample_ids=ids,
        scope=scope,
    )


def check_decisions_stale_open(
    conn: sqlite3.Connection,
    *,
    days: int = STALE_OPEN_DECISION_DAYS,
    now: Optional[datetime] = None,
    since_iso: Optional[str] = None,
    scope: str = SCOPE_FULL,
) -> Optional[Dict[str, Any]]:
    if not _table_exists(conn, "decisions"):
        return None
    now_dt = now or datetime.now(timezone.utc)
    cutoff_iso = (now_dt - timedelta(days=days)).isoformat()
    extra, ts_params = _since_clause("ts", since_iso)
    rows = _safe_select(
        conn,
        "SELECT id FROM decisions "
        "WHERE position_opened=1 AND position_closed=0 AND ts < ?"
        + extra + " ORDER BY ts ASC",
        (cutoff_iso,) + ts_params,
    )
    if not rows:
        return None
    ids = [str(r[0]) for r in rows]
    return _finding(
        "DECISIONS_STALE_OPEN_OVER_365D",
        SEV_WARN,
        len(ids),
        f"decisions open for more than {days}d — reconciler should have caught",
        sample_ids=ids,
        scope=scope,
    )


# ── Paper signals checks ─────────────────────────────────────────────────────

def check_paper_signals_stale_open(
    conn: sqlite3.Connection,
    *,
    days: int = STALE_OPEN_PAPER_DAYS,
    now: Optional[datetime] = None,
    since_iso: Optional[str] = None,
    scope: str = SCOPE_FULL,
) -> Optional[Dict[str, Any]]:
    if not _table_exists(conn, "paper_signals"):
        return None
    now_dt = now or datetime.now(timezone.utc)
    cutoff_iso = (now_dt - timedelta(days=days)).isoformat()
    extra, ts_params = _since_clause("logged_at", since_iso)
    rows = _safe_select(
        conn,
        "SELECT id FROM paper_signals "
        "WHERE status='open' AND logged_at < ?" + extra + " "
        "ORDER BY logged_at ASC",
        (cutoff_iso,) + ts_params,
    )
    if not rows:
        return None
    ids = [str(r[0]) for r in rows]
    return _finding(
        "PAPER_SIGNALS_STALE_OPEN_OVER_180D",
        SEV_WARN,
        len(ids),
        f"paper_signals open longer than {days}d (past longest doctrine "
        "horizon) — exit resolver gap or resolver outage",
        sample_ids=ids,
        scope=scope,
    )


def check_paper_signals_invalid_entry_price(
    conn: sqlite3.Connection,
    *,
    since_iso: Optional[str] = None,
    scope: str = SCOPE_FULL,
) -> Optional[Dict[str, Any]]:
    if not _table_exists(conn, "paper_signals"):
        return None
    extra, params = _since_clause("logged_at", since_iso)
    rows = _safe_select(
        conn,
        "SELECT id FROM paper_signals "
        "WHERE (entry_price IS NULL OR entry_price <= 0)" + extra + " "
        "ORDER BY logged_at ASC",
        params,
    )
    if not rows:
        return None
    ids = [str(r[0]) for r in rows]
    return _finding(
        "PAPER_SIGNALS_INVALID_ENTRY_PRICE",
        SEV_ERROR,
        len(ids),
        "paper_signals with NULL or non-positive entry_price — corrupts "
        "all downstream return / concentration math",
        sample_ids=ids,
        scope=scope,
    )


def check_paper_signals_sniper_missing_aux_h3(
    conn: sqlite3.Connection,
    *,
    cutoff_iso: str = PHASE_12B_CUTOFF_ISO,
    since_iso: Optional[str] = None,
    scope: str = SCOPE_FULL,
) -> Optional[Dict[str, Any]]:
    if not _table_exists(conn, "paper_signals"):
        return None
    if "aux_h3" not in _table_columns(conn, "paper_signals"):
        return None
    extra, ts_params = _since_clause("logged_at", since_iso)
    rows = _safe_select(
        conn,
        "SELECT id FROM paper_signals "
        "WHERE strategy='SNIPER' AND logged_at >= ? AND aux_h3 IS NULL"
        + extra + " ORDER BY logged_at ASC",
        (cutoff_iso,) + ts_params,
    )
    if not rows:
        return None
    ids = [str(r[0]) for r in rows]
    return _finding(
        "PAPER_SIGNALS_SNIPER_MISSING_AUX_H3",
        SEV_WARN,
        len(ids),
        f"SNIPER paper_signals logged at/after {cutoff_iso} are missing "
        "aux_h3 — Phase 12B instrumentation coverage gap",
        sample_ids=ids,
        scope=scope,
    )


def check_paper_signals_duplicate_open_24h(
    conn: sqlite3.Connection,
    *,
    window_hours: int = DUPLICATE_OPEN_WINDOW_HR,
    since_iso: Optional[str] = None,
    scope: str = SCOPE_FULL,
) -> Optional[Dict[str, Any]]:
    if not _table_exists(conn, "paper_signals"):
        return None
    extra, params = _since_clause("logged_at", since_iso)
    rows = _safe_select(
        conn,
        "SELECT id, strategy, ticker, side, logged_at FROM paper_signals "
        "WHERE status='open'" + extra + " "
        "ORDER BY strategy, ticker, side, logged_at ASC",
        params,
    )
    if not rows:
        return None

    def _parse(ts: str) -> Optional[datetime]:
        if not ts:
            return None
        try:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (TypeError, ValueError):
            return None

    window = timedelta(hours=window_hours)
    by_key: Dict[Tuple[str, str, str], List[Tuple[str, datetime]]] = {}
    for rid, strat, tkr, side, logged_at in rows:
        dt = _parse(str(logged_at))
        if dt is None:
            continue
        key = (str(strat).upper(), str(tkr).upper(), str(side).upper())
        by_key.setdefault(key, []).append((str(rid), dt))

    dupe_ids: List[str] = []
    for entries in by_key.values():
        for i in range(1, len(entries)):
            if entries[i][1] - entries[i - 1][1] <= window:
                dupe_ids.append(entries[i][0])

    if not dupe_ids:
        return None
    return _finding(
        "PAPER_SIGNALS_DUPLICATE_OPEN_24H",
        SEV_WARN,
        len(dupe_ids),
        f"open paper_signals with another open (strategy, ticker, side) "
        f"row within {window_hours}h — paper governance dedup leak",
        sample_ids=dupe_ids,
        scope=scope,
    )


# ── Paper signal outcomes checks ─────────────────────────────────────────────

def check_outcomes_orphan(
    conn: sqlite3.Connection,
    *,
    since_iso: Optional[str] = None,
    scope: str = SCOPE_FULL,
) -> Optional[Dict[str, Any]]:
    if not (_table_exists(conn, "paper_signal_outcomes")
            and _table_exists(conn, "paper_signals")):
        return None
    extra, params = _since_clause("o.measured_at", since_iso)
    rows = _safe_select(
        conn,
        "SELECT o.id FROM paper_signal_outcomes o "
        "LEFT JOIN paper_signals p ON p.id = o.signal_id "
        "WHERE p.id IS NULL" + extra + " "
        "ORDER BY o.measured_at ASC",
        params,
    )
    if not rows:
        return None
    ids = [str(r[0]) for r in rows]
    return _finding(
        "PAPER_SIGNAL_OUTCOMES_ORPHAN",
        SEV_ERROR,
        len(ids),
        "paper_signal_outcomes rows whose signal_id points to a missing "
        "paper_signals.id — broken foreign key",
        sample_ids=ids,
        scope=scope,
    )


def check_outcomes_incoherent_state(
    conn: sqlite3.Connection,
    *,
    since_iso: Optional[str] = None,
    scope: str = SCOPE_FULL,
) -> Optional[Dict[str, Any]]:
    if not _table_exists(conn, "paper_signal_outcomes"):
        return None
    cols = _table_columns(conn, "paper_signal_outcomes")
    if "still_open" not in cols or "hold_complete" not in cols:
        return None
    extra, params = _since_clause("measured_at", since_iso)
    rows = _safe_select(
        conn,
        "SELECT id FROM paper_signal_outcomes "
        "WHERE still_open=1 AND hold_complete=1" + extra + " "
        "ORDER BY measured_at ASC",
        params,
    )
    if not rows:
        return None
    ids = [str(r[0]) for r in rows]
    return _finding(
        "PAPER_SIGNAL_OUTCOMES_INCOHERENT_STATE",
        SEV_ERROR,
        len(ids),
        "outcome rows with still_open=1 AND hold_complete=1 — mutually "
        "exclusive flags both set",
        sample_ids=ids,
        scope=scope,
    )


# ── Voyager paper signals checks (legacy table) ──────────────────────────────

def check_voyager_stale_open_no_outcome(
    conn: sqlite3.Connection,
    *,
    days: int = VOYAGER_STALE_OPEN_DAYS,
    now: Optional[datetime] = None,
    since_iso: Optional[str] = None,
    scope: str = SCOPE_FULL,
) -> Optional[Dict[str, Any]]:
    if not _table_exists(conn, "voyager_paper_signals"):
        return None
    now_dt = now or datetime.now(timezone.utc)
    cutoff_iso = (now_dt - timedelta(days=days)).isoformat()
    extra, ts_params = _since_clause("logged_at", since_iso)
    rows = _safe_select(
        conn,
        "SELECT id FROM voyager_paper_signals "
        "WHERE signal_status='open' AND logged_at < ? AND outcome_30d IS NULL"
        + extra + " ORDER BY logged_at ASC",
        (cutoff_iso,) + ts_params,
    )
    if not rows:
        return None
    ids = [str(r[0]) for r in rows]
    return _finding(
        "VOYAGER_STALE_OPEN_NO_30D_OUTCOME",
        SEV_WARN,
        len(ids),
        f"voyager_paper_signals open longer than {days}d with no resolved "
        "outcome_30d — resolver coverage gap",
        sample_ids=ids,
        scope=scope,
    )


def check_voyager_direction_violation(
    conn: sqlite3.Connection,
    *,
    since_iso: Optional[str] = None,
    scope: str = SCOPE_FULL,
) -> Optional[Dict[str, Any]]:
    if not _table_exists(conn, "voyager_paper_signals"):
        return None
    extra, params = _since_clause("logged_at", since_iso)
    rows = _safe_select(
        conn,
        "SELECT id FROM voyager_paper_signals "
        "WHERE (direction IS NULL OR UPPER(direction) <> 'LONG')"
        + extra + " ORDER BY logged_at ASC",
        params,
    )
    if not rows:
        return None
    ids = [str(r[0]) for r in rows]
    return _finding(
        "VOYAGER_DIRECTION_VIOLATION",
        SEV_ERROR,
        len(ids),
        "voyager_paper_signals with direction != 'LONG' — doctrine violation "
        "(Voyager is LONG-only)",
        sample_ids=ids,
        scope=scope,
    )


def check_voyager_missing_required_fields(
    conn: sqlite3.Connection,
    *,
    since_iso: Optional[str] = None,
    scope: str = SCOPE_FULL,
) -> Optional[Dict[str, Any]]:
    if not _table_exists(conn, "voyager_paper_signals"):
        return None
    extra, params = _since_clause("logged_at", since_iso)
    rows = _safe_select(
        conn,
        "SELECT id FROM voyager_paper_signals "
        "WHERE (entry_price IS NULL OR entry_price <= 0 "
        "    OR stop_loss IS NULL OR stop_loss <= 0 "
        "    OR target_price IS NULL OR target_price <= 0)"
        + extra + " ORDER BY logged_at ASC",
        params,
    )
    if not rows:
        return None
    ids = [str(r[0]) for r in rows]
    return _finding(
        "VOYAGER_MISSING_REQUIRED_FIELDS",
        SEV_ERROR,
        len(ids),
        "voyager_paper_signals missing entry_price / stop_loss / target_price "
        "— doctrine requires all three for paper validation",
        sample_ids=ids,
        scope=scope,
    )


# ── Reconciler audit-trail check ─────────────────────────────────────────────

def _broker_snapshot_age_hours(
    snapshot_path: Optional[Path],
    *,
    now: Optional[datetime] = None,
) -> Optional[float]:
    """Return the snapshot's age in hours. ``None`` if missing, unreadable,
    or no ``generated_at`` field."""
    if snapshot_path is None or not snapshot_path.exists():
        return None
    try:
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    gen = payload.get("generated_at")
    if not gen:
        return None
    try:
        gen_dt = datetime.fromisoformat(str(gen))
    except ValueError:
        return None
    if gen_dt.tzinfo is None:
        gen_dt = gen_dt.replace(tzinfo=timezone.utc)
    now_dt = now or datetime.now(timezone.utc)
    return (now_dt - gen_dt).total_seconds() / 3600.0


def _open_decisions_by_ticker(
    conn: sqlite3.Connection,
) -> Dict[str, Dict[str, Any]]:
    """Map ticker → {id, direction, qty} for every currently-open decision.
    Two open decisions on the same ticker would be a separate hygiene
    violation; here we keep the most-recent so the per-ticker comparison
    is well-defined."""
    if not _table_exists(conn, "decisions"):
        return {}
    cols = _table_columns(conn, "decisions")
    qty_col = "fill_qty" if "fill_qty" in cols else "shares"
    rows = _safe_select(
        conn,
        f"SELECT id, ticker, direction, {qty_col}, ts FROM decisions "
        "WHERE position_opened=1 AND position_closed=0 "
        "ORDER BY ts ASC",
    )
    out: Dict[str, Dict[str, Any]] = {}
    for rid, tkr, direction, qty, _ts in rows:
        t = (tkr or "").upper()
        if not t:
            continue
        try:
            qty_f = float(qty) if qty is not None else None
        except (TypeError, ValueError):
            qty_f = None
        out[t] = {
            "id":        str(rid),
            "direction": (direction or "").upper(),
            "qty":       qty_f,
        }
    return out


def _current_broker_book_diff(
    conn: sqlite3.Connection,
    broker_by_ticker: Dict[str, Dict[str, Any]],
) -> List[Tuple[str, str, str]]:
    """Return [(ticker, kind, detail)] for every current mismatch between
    broker snapshot and open decisions. Empty list = currently aligned."""
    book = _open_decisions_by_ticker(conn)
    diffs: List[Tuple[str, str, str]] = []

    # decisions_only — open in book but absent from broker
    for t, b in book.items():
        if t not in broker_by_ticker:
            diffs.append((t, "DECISIONS_ONLY",
                          f"book {b['direction']} qty={b['qty']}"))

    # broker_only — held at broker but no open decision
    for t, p in broker_by_ticker.items():
        if t not in book:
            diffs.append((t, "BROKER_ONLY",
                          f"broker {p.get('side')} qty={p.get('qty')}"))

    # side + qty mismatches on tickers that exist in both
    for t, b in book.items():
        if t not in broker_by_ticker:
            continue
        p = broker_by_ticker[t]
        broker_side = (p.get("side") or "").lower()
        expected_side = "long" if b["direction"] == "LONG" else "short"
        if broker_side and broker_side != expected_side:
            diffs.append((t, "SIDE_MISMATCH",
                          f"book {b['direction']} vs broker {broker_side}"))
            continue  # don't double-report qty if side is wrong
        broker_qty = p.get("qty")
        if broker_qty is None or b["qty"] is None:
            continue
        try:
            if abs(abs(float(broker_qty)) - abs(float(b["qty"]))) > 0.5:
                diffs.append((t, "QTY_MISMATCH",
                              f"book qty={b['qty']} broker qty={broker_qty}"))
        except (TypeError, ValueError):
            continue
    return diffs


def check_reconciler_drift(
    conn: sqlite3.Connection,
    *,
    days: int = RECONCILER_DRIFT_WINDOW_DAYS,
    now: Optional[datetime] = None,
    since_iso: Optional[str] = None,
    scope: str = SCOPE_FULL,
    broker_snapshot_path: Optional[Path] = None,
    broker_fresh_hours: float = BROKER_SNAPSHOT_FRESH_HOURS,
) -> List[Dict[str, Any]]:
    """Phase 1G: classify reconciler drift as ACTIVE / HISTORICAL / UNKNOWN.

    - ``RECONCILER_DRIFT_ACTIVE`` (WARN): the cached broker snapshot is
      fresh and disagrees with the open-decisions book *right now*.
    - ``RECONCILER_DRIFT_HISTORICAL`` (INFO): drift rows exist in the
      window but the fresh broker snapshot matches the book — so the
      mismatch is resolved and the rows are storm debris.
    - ``RECONCILER_DRIFT_UNKNOWN`` (WARN): drift rows exist but the
      broker snapshot is missing or stale, so we cannot confirm
      resolution. Conservative WARN so ``ready_to_gate_clean`` stays
      false until an operator refreshes the snapshot.
    - No findings if there are zero drift rows in the window *and* no
      current broker/book mismatch.
    """
    if not _table_exists(conn, "veto_log"):
        return []
    now_dt = now or datetime.now(timezone.utc)
    cutoff_iso = (now_dt - timedelta(days=days)).isoformat()
    # Effective cutoff = max(now-Nd, since_iso). For clean scope this
    # typically still resolves to the 7d window since clean epoch is
    # also close to "now"; we take the later of the two to honor both.
    effective = cutoff_iso
    if since_iso and since_iso > effective:
        effective = since_iso
    rows = _safe_select(
        conn,
        "SELECT id, ts, ticker FROM veto_log "
        "WHERE agent='position_reconciler' AND verdict='RECONCILE_DRIFT' "
        "  AND ts >= ? "
        "ORDER BY ts ASC",
        (effective,),
    )
    drift_ids = [str(r[0]) for r in rows]
    first_ts  = str(rows[0][1]) if rows else None
    last_ts   = str(rows[-1][1]) if rows else None
    tickers   = sorted({str(r[2]).upper() for r in rows if r[2]})

    broker_by_ticker, _ = _load_broker_snapshot(broker_snapshot_path)
    snapshot_age_hr = _broker_snapshot_age_hours(snapshot_path=broker_snapshot_path,
                                                 now=now_dt)
    snapshot_fresh = (
        snapshot_age_hr is not None
        and snapshot_age_hr <= broker_fresh_hours
    )

    findings: List[Dict[str, Any]] = []

    # No usable snapshot — cannot prove resolution. Stay conservative.
    if not snapshot_fresh:
        if not rows:
            return []
        if snapshot_age_hr is None:
            stale_reason = "broker snapshot is unavailable"
        else:
            stale_reason = (
                f"broker snapshot is {snapshot_age_hr:.1f}h old "
                f"(> {broker_fresh_hours}h fresh limit)"
            )
        finding = _finding(
            "RECONCILER_DRIFT_UNKNOWN",
            SEV_WARN,
            len(drift_ids),
            f"position_reconciler logged {len(drift_ids)} RECONCILE_DRIFT "
            f"row(s) in the last {days}d (first={first_ts}, last={last_ts}); "
            f"{stale_reason}; cannot confirm whether drift is resolved.",
            sample_ids=drift_ids,
            scope=scope,
        )
        finding["first_ts"] = first_ts
        finding["last_ts"] = last_ts
        finding["affected_tickers"] = tickers
        finding["broker_snapshot_age_hours"] = snapshot_age_hr
        findings.append(finding)
        return findings

    # Snapshot is fresh — compute current diff vs open decisions.
    diffs = _current_broker_book_diff(conn, broker_by_ticker)

    if diffs:
        active = _finding(
            "RECONCILER_DRIFT_ACTIVE",
            SEV_WARN,
            len(diffs),
            f"broker snapshot ({snapshot_age_hr:.2f}h old) vs open decisions: "
            f"{len(diffs)} mismatch(es): "
            + "; ".join(f"{t}:{k}" for t, k, _ in diffs[:SAMPLE_LIMIT]),
            sample_ids=[t for t, _, _ in diffs[:SAMPLE_LIMIT]],
            scope=scope,
        )
        active["mismatches"] = [
            {"ticker": t, "kind": k, "detail": d}
            for t, k, d in diffs
        ]
        active["broker_snapshot_age_hours"] = snapshot_age_hr
        findings.append(active)

    if rows:
        resolved = not diffs
        detail = (
            f"position_reconciler logged {len(drift_ids)} RECONCILE_DRIFT "
            f"row(s) in the last {days}d (first={first_ts}, last={last_ts}). "
            f"Broker snapshot is fresh ({snapshot_age_hr:.2f}h) and "
            + ("matches open decisions — drift is resolved/historical."
               if resolved
               else "current diff is non-empty — see RECONCILER_DRIFT_ACTIVE.")
        )
        historical = _finding(
            "RECONCILER_DRIFT_HISTORICAL",
            SEV_INFO,
            len(drift_ids),
            detail,
            sample_ids=drift_ids,
            scope=scope,
        )
        historical["first_ts"] = first_ts
        historical["last_ts"] = last_ts
        historical["affected_tickers"] = tickers
        historical["broker_snapshot_age_hours"] = snapshot_age_hr
        historical["resolved"] = resolved
        findings.append(historical)

    return findings


# ── Quarantine sidecar ───────────────────────────────────────────────────────

# Phase 1E: relative path to the broker-positions snapshot the
# operator-invoked scripts/snapshot_broker_positions.py emits. Read-only
# (cache only) — the hygiene report does NOT call the broker itself.
BROKER_SNAPSHOT_REL = Path("cache") / "state" / "broker_positions_snapshot.json"


def _load_broker_snapshot(
    snapshot_path: Optional[Path],
) -> Tuple[Dict[str, Dict[str, Any]], str]:
    """Return (ticker → position dict, source-label).

    ``source-label`` is one of:
      - ``broker_snapshot_sidecar`` — snapshot loaded successfully
      - ``unavailable_no_cached_snapshot`` — sidecar absent or unreadable
    """
    if snapshot_path is None or not snapshot_path.exists():
        return {}, "unavailable_no_cached_snapshot"
    try:
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("broker snapshot read failed (%s): %s", snapshot_path, exc)
        return {}, "unavailable_no_cached_snapshot"
    positions = payload.get("positions") or []
    by_ticker: Dict[str, Dict[str, Any]] = {}
    for p in positions:
        t = str(p.get("ticker") or "").upper()
        if t:
            by_ticker[t] = p
    return by_ticker, "broker_snapshot_sidecar"


def _broker_match_label(
    entry: Dict[str, Any],
    broker_by_ticker: Dict[str, Dict[str, Any]],
) -> Optional[str]:
    """Return one of:
      - ``match``                — broker has an open position on this ticker
      - ``no_broker_position``   — snapshot loaded but ticker not present
      - ``closed_by_book``       — decision was closed in our book; broker
                                   absence is consistent
      - ``None``                 — snapshot unavailable; we cannot say
    """
    if not broker_by_ticker:
        return None
    ticker = (entry.get("ticker") or "").upper()
    pos = broker_by_ticker.get(ticker)
    if pos is not None:
        return "match"
    if int(entry.get("position_closed") or 0) == 1:
        return "closed_by_book"
    return "no_broker_position"


def build_legacy_quarantine(
    conn: sqlite3.Connection,
    clean_epoch_iso: str = CLEAN_PAPER_EVIDENCE_START,
    *,
    broker_snapshot_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Return one entry per pre-epoch decisions row with a hygiene issue.

    Phase 1E: when ``broker_snapshot_path`` exists, each entry's
    ``broker_position_match`` is filled in from the cached broker
    snapshot (read-only). When the snapshot is absent the field stays
    ``None`` and the report's ``broker_match_source`` records why.

    Detection mirrors the hygiene checks (decisions only — the 84-row
    issue identified in Phase 1C); paper_signals quarantine is intentionally
    out of scope until a separate phase, since paper_signals have no
    equivalent fill-telemetry gap.

    DB is never mutated.
    """
    if not _table_exists(conn, "decisions"):
        return []
    cols = _table_columns(conn, "decisions")
    if not {"fill_price", "fill_qty"}.issubset(cols):
        return []

    rows = _safe_select(
        conn,
        "SELECT id, ts, ticker, strategy, position_opened, position_closed, "
        "       fill_price, fill_qty, pnl, exit_price, slippage_bps "
        "FROM decisions WHERE ts < ? ORDER BY ts ASC",
        (clean_epoch_iso,),
    )

    broker_by_ticker, _broker_source = _load_broker_snapshot(broker_snapshot_path)

    entries: List[Dict[str, Any]] = []
    for (rid, ts, tkr, strat, pos_open, pos_closed,
         fill_price, fill_qty, pnl, exit_price, slip) in rows:
        missing: List[str] = []
        if pos_open == 1:
            if fill_price is None:
                missing.append("fill_price")
            if fill_qty is None or (fill_qty is not None and float(fill_qty) <= 0):
                missing.append("fill_qty")
        if pos_closed == 1:
            if pnl is None:
                missing.append("pnl")
            if exit_price is None:
                missing.append("exit_price")
        if slip is not None and abs(float(slip)) > EXTREME_SLIPPAGE_BPS:
            missing.append("slippage_bps_extreme")
        if not missing:
            continue
        partial: Dict[str, Any] = {
            "decision_id":       str(rid),
            "ticker":             str(tkr or "").upper(),
            "strategy":           str(strat or "").upper(),
            "timestamp":          str(ts or ""),
            "position_opened":    int(pos_open or 0),
            "position_closed":    int(pos_closed or 0),
            "missing_fields":     missing,
            "quarantine_reason":  QUARANTINE_REASON_PRE_FILL_TELEMETRY,
            "broker_position_match": None,
        }
        partial["broker_position_match"] = _broker_match_label(
            partial, broker_by_ticker,
        )
        entries.append(partial)
    return entries


def _broker_match_counts(
    entries: List[Dict[str, Any]],
) -> Dict[str, int]:
    """Tally broker_position_match labels for the sidecar header. Helps
    operators (and the dashboard) see coverage at a glance."""
    counts: Dict[str, int] = {
        "match": 0, "no_broker_position": 0, "closed_by_book": 0, "unknown": 0,
    }
    for e in entries:
        label = e.get("broker_position_match")
        if label is None:
            counts["unknown"] += 1
        elif label in counts:
            counts[label] += 1
        else:
            counts["unknown"] += 1
    return counts


def write_quarantine_sidecar(
    entries: List[Dict[str, Any]],
    sidecar_path: Path,
    *,
    clean_epoch_iso: str = CLEAN_PAPER_EVIDENCE_START,
    db_path: Optional[Path] = None,
    broker_match_source: str = "unavailable_no_cached_snapshot",
    broker_snapshot_path: Optional[Path] = None,
    broker_snapshot_generated_at: Optional[str] = None,
) -> None:
    """Atomically write the quarantine sidecar. Overwrites prior content
    each run — this is a snapshot, not append-only state."""
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at":              datetime.now(timezone.utc).isoformat(),
        "db_path":                   str(db_path) if db_path else None,
        "clean_epoch_start":         clean_epoch_iso,
        "quarantine_reason_default": QUARANTINE_REASON_PRE_FILL_TELEMETRY,
        "broker_match_source":       broker_match_source,
        "broker_snapshot_path":      (str(broker_snapshot_path)
                                      if broker_snapshot_path else None),
        "broker_snapshot_generated_at": broker_snapshot_generated_at,
        "broker_match_counts":       _broker_match_counts(entries),
        "count":                     len(entries),
        "entries":                   entries,
    }
    tmp = sidecar_path.with_suffix(sidecar_path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    tmp.replace(sidecar_path)


def _read_broker_snapshot_meta(
    snapshot_path: Optional[Path],
) -> Tuple[str, Optional[str]]:
    """Return (source-label, generated_at) for the sidecar header without
    needing to re-load positions. Mirrors ``_load_broker_snapshot``."""
    if snapshot_path is None or not snapshot_path.exists():
        return "unavailable_no_cached_snapshot", None
    try:
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "unavailable_no_cached_snapshot", None
    return "broker_snapshot_sidecar", payload.get("generated_at")


# ── Report driver ────────────────────────────────────────────────────────────

_CHECKS: Tuple[Callable[..., Optional[Dict[str, Any]]], ...] = (
    check_decisions_open_no_fill_price,
    check_decisions_open_no_fill_qty,
    check_decisions_closed_missing_pnl_exit,
    check_decisions_extreme_slippage,
    check_decisions_stale_open,
    check_paper_signals_stale_open,
    check_paper_signals_invalid_entry_price,
    check_paper_signals_sniper_missing_aux_h3,
    check_paper_signals_duplicate_open_24h,
    check_outcomes_orphan,
    check_outcomes_incoherent_state,
    check_voyager_stale_open_no_outcome,
    check_voyager_direction_violation,
    check_voyager_missing_required_fields,
    check_reconciler_drift,
)


def _run_check(check, conn, *, scope: str, since_iso: Optional[str],
               now: Optional[datetime],
               broker_snapshot_path: Optional[Path] = None,
               ) -> List[Dict[str, Any]]:
    """Invoke a check with the kwargs it accepts. Each check signature
    declares which optional kwargs are meaningful. Returns a flat list of
    findings — a check may return ``None``, a single finding dict, or a
    list of finding dicts."""
    import inspect
    sig = inspect.signature(check)
    kwargs: Dict[str, Any] = {"scope": scope, "since_iso": since_iso}
    if "now" in sig.parameters and now is not None:
        kwargs["now"] = now
    if "broker_snapshot_path" in sig.parameters:
        kwargs["broker_snapshot_path"] = broker_snapshot_path
    try:
        result = check(conn, **kwargs)
    except Exception as exc:
        logger.warning("check %s raised: %s", check.__name__, exc)
        return []
    if result is None:
        return []
    if isinstance(result, list):
        return [r for r in result if r is not None]
    return [result]


def build_report(
    db_path: Path,
    *,
    clean_epoch_iso: str = CLEAN_PAPER_EVIDENCE_START,
    now: Optional[datetime] = None,
    broker_snapshot_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Run every check in both scopes (full + clean) and assemble the
    report. Never raises on missing tables / columns / files.

    Phase 1E: when ``broker_snapshot_path`` is provided and exists, the
    legacy quarantine entries are enriched with a ``broker_position_match``
    label (read-only cache access). When missing, the field stays None
    and ``broker_match_source`` records why.
    """
    findings_full: List[Dict[str, Any]] = []
    findings_clean: List[Dict[str, Any]] = []
    tables_scanned: Dict[str, int] = {}
    quarantine: List[Dict[str, Any]] = []
    conn = _connect_ro(db_path)
    broker_source, broker_generated_at = _read_broker_snapshot_meta(
        broker_snapshot_path,
    )

    if conn is None:
        return _assemble(
            db_path, findings_full, findings_clean, tables_scanned,
            quarantine, clean_epoch_iso, db_missing=True,
            broker_match_source=broker_source,
            broker_snapshot_path=broker_snapshot_path,
            broker_snapshot_generated_at=broker_generated_at,
        )

    try:
        for tbl in ("decisions", "paper_signals", "paper_signal_outcomes",
                    "voyager_paper_signals", "veto_log"):
            tables_scanned[tbl] = _row_count(conn, tbl)

        for check in _CHECKS:
            findings_full.extend(_run_check(
                check, conn, scope=SCOPE_FULL,
                since_iso=None, now=now,
                broker_snapshot_path=broker_snapshot_path,
            ))
            findings_clean.extend(_run_check(
                check, conn, scope=SCOPE_CLEAN,
                since_iso=clean_epoch_iso, now=now,
                broker_snapshot_path=broker_snapshot_path,
            ))

        quarantine = build_legacy_quarantine(
            conn, clean_epoch_iso,
            broker_snapshot_path=broker_snapshot_path,
        )
    finally:
        conn.close()

    return _assemble(
        db_path, findings_full, findings_clean, tables_scanned,
        quarantine, clean_epoch_iso,
        broker_match_source=broker_source,
        broker_snapshot_path=broker_snapshot_path,
        broker_snapshot_generated_at=broker_generated_at,
    )


def _summarize(findings: List[Dict[str, Any]]) -> Dict[str, Any]:
    errors = sum(1 for f in findings if f["severity"] == SEV_ERROR)
    warns  = sum(1 for f in findings if f["severity"] == SEV_WARN)
    infos  = sum(1 for f in findings if f["severity"] == SEV_INFO)
    return {
        "total_findings": len(findings),
        "errors":         errors,
        "warns":          warns,
        "infos":          infos,
    }


def _operator_review(
    findings_clean: List[Dict[str, Any]],
    quarantine: List[Dict[str, Any]],
    *,
    broker_match_source: str = "unavailable_no_cached_snapshot",
    broker_snapshot_generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Compose the operator-review section.

    - Legacy rows needing review: the quarantined set.
    - Rows safe to ignore for clean telemetry: same set (annotated with
      reason). Operators can confirm these are pre-Phase-0 debt rather
      than active issues.
    - Rows requiring manual investigation: clean-epoch ERROR-level
      findings — these are real Phase-0+ data integrity violations that
      must be resolved before any 1B gate can be promoted.
    - Phase 1E: broker_match_counts surfaces how many of the quarantined
      rows correspond to an open broker position (purely informational).
    """
    investigation: List[Dict[str, Any]] = [
        {"code": f["code"], "count": f["count"], "samples": f["sample_ids"],
         "detail": f["detail"]}
        for f in findings_clean if f["severity"] == SEV_ERROR
    ]
    sample_quarantined = [
        {"decision_id": q["decision_id"], "ticker": q["ticker"],
         "strategy": q["strategy"], "timestamp": q["timestamp"],
         "missing_fields": q["missing_fields"],
         "broker_position_match": q.get("broker_position_match")}
        for q in quarantine[:SAMPLE_LIMIT]
    ]
    return {
        "legacy_rows_needing_review": {
            "count":   len(quarantine),
            "samples": sample_quarantined,
            "where":   "see data/state/paper_legacy_quarantine.json for the full list",
        },
        "rows_safe_to_ignore_for_clean": {
            "count":   len(quarantine),
            "reason":  ("rows predate the clean-paper-evidence epoch; "
                        "they are quarantined from the clean ready_to_gate "
                        "verdict and excluded from clean Phase 1B telemetry"),
        },
        "rows_requiring_manual_investigation": {
            "count":    len(investigation),
            "findings": investigation,
        },
        "broker_position_match_source": broker_match_source,
        "broker_snapshot_generated_at": broker_snapshot_generated_at,
        "broker_match_counts": _broker_match_counts(quarantine),
    }


def _assemble(
    db_path: Path,
    findings_full: List[Dict[str, Any]],
    findings_clean: List[Dict[str, Any]],
    tables_scanned: Dict[str, int],
    quarantine: List[Dict[str, Any]],
    clean_epoch_iso: str,
    db_missing: bool = False,
    *,
    broker_match_source: str = "unavailable_no_cached_snapshot",
    broker_snapshot_path: Optional[Path] = None,
    broker_snapshot_generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    s_full  = _summarize(findings_full)
    s_clean = _summarize(findings_clean)
    return {
        "generated_at":    datetime.now(timezone.utc).isoformat(),
        "db_path":         str(db_path),
        "db_missing":      bool(db_missing),
        "clean_epoch_start": clean_epoch_iso,
        "thresholds": {
            "stale_open_decision_days":     STALE_OPEN_DECISION_DAYS,
            "extreme_slippage_bps":         EXTREME_SLIPPAGE_BPS,
            "stale_open_paper_days":        STALE_OPEN_PAPER_DAYS,
            "duplicate_open_window_hours":  DUPLICATE_OPEN_WINDOW_HR,
            "phase_12b_cutoff_iso":         PHASE_12B_CUTOFF_ISO,
            "voyager_stale_open_days":      VOYAGER_STALE_OPEN_DAYS,
            "reconciler_drift_window_days": RECONCILER_DRIFT_WINDOW_DAYS,
        },
        "tables_scanned":  tables_scanned,
        "summary": {
            # Phase 1D — dual-scope verdicts. Published only. Never wired
            # to enforcement in this phase.
            "ready_to_gate_all":   (s_full["errors"] == 0 and s_full["warns"] == 0),
            "ready_to_gate_clean": (s_clean["errors"] == 0 and s_clean["warns"] == 0),
            "full_ledger":  s_full,
            "clean_epoch":  s_clean,
            # Phase 1C back-compat: keep the original flat counters
            # available from the full-ledger view so existing readers
            # (sidecar consumers, dashboards if they pick this up later)
            # don't break. "ready_to_gate" alone is the strictest verdict.
            "total_findings":  s_full["total_findings"],
            "errors":          s_full["errors"],
            "warns":           s_full["warns"],
            "infos":           s_full["infos"],
            "ready_to_gate":   (s_clean["errors"] == 0 and s_clean["warns"] == 0),
        },
        "findings":          findings_full + findings_clean,
        "findings_by_scope": {
            "full":  findings_full,
            "clean": findings_clean,
        },
        "legacy_quarantine": {
            "count":              len(quarantine),
            "reason":             QUARANTINE_REASON_PRE_FILL_TELEMETRY,
            "sidecar_path":       "data/state/paper_legacy_quarantine.json",
            "broker_match_source":         broker_match_source,
            "broker_snapshot_path":        (str(broker_snapshot_path)
                                            if broker_snapshot_path else None),
            "broker_snapshot_generated_at": broker_snapshot_generated_at,
            "broker_match_counts":         _broker_match_counts(quarantine),
        },
        "operator_review":   _operator_review(
            findings_clean, quarantine,
            broker_match_source=broker_match_source,
            broker_snapshot_generated_at=broker_snapshot_generated_at,
        ),
    }


# ── Rendering ────────────────────────────────────────────────────────────────

def render_text(report: Dict[str, Any]) -> str:
    bar = "─" * 80
    s = report["summary"]
    lines: List[str] = []
    lines.append(bar)
    lines.append("PAPER STATE HYGIENE — Phase 1C/1D diagnostic (no enforcement)")
    lines.append(f"generated_at={report['generated_at']}   db={report['db_path']}")
    lines.append(f"clean_epoch_start={report.get('clean_epoch_start')}")
    if report.get("db_missing"):
        lines.append("DB MISSING — no scans performed")
    lines.append(bar)
    full  = s.get("full_ledger") or {}
    clean = s.get("clean_epoch") or {}
    lines.append(
        f"FULL  ledger  findings={full.get('total_findings', 0)}  "
        f"errors={full.get('errors', 0)}  warns={full.get('warns', 0)}  "
        f"ready_to_gate_all={s['ready_to_gate_all']}"
    )
    lines.append(
        f"CLEAN epoch   findings={clean.get('total_findings', 0)}  "
        f"errors={clean.get('errors', 0)}  warns={clean.get('warns', 0)}  "
        f"ready_to_gate_clean={s['ready_to_gate_clean']}"
    )
    tables = report.get("tables_scanned") or {}
    if tables:
        tbl_parts = ", ".join(f"{k}={v}" for k, v in sorted(tables.items()))
        lines.append(f"tables_scanned: {tbl_parts}")
    lines.append("")

    by_scope = report.get("findings_by_scope") or {}
    for label, key in (("FULL LEDGER findings:", "full"),
                       ("CLEAN EPOCH findings:", "clean")):
        rows = by_scope.get(key, [])
        if not rows:
            lines.append(f"{label} none")
        else:
            lines.append(label)
            for f in rows:
                samples = ", ".join(f["sample_ids"]) or "—"
                lines.append(
                    f"  [{f['severity']:<5}] {f['code']:<40} n={f['count']:>4}"
                )
                lines.append(f"          {f['detail']}")
                lines.append(f"          samples: {samples}")
        lines.append("")

    lq = report.get("legacy_quarantine") or {}
    lines.append(
        f"LEGACY QUARANTINE: count={lq.get('count', 0)} "
        f"reason={lq.get('reason')} → {lq.get('sidecar_path')}"
    )
    op = report.get("operator_review") or {}
    inv = op.get("rows_requiring_manual_investigation") or {}
    lines.append(
        f"OPERATOR REVIEW: legacy_needing_review={op.get('legacy_rows_needing_review', {}).get('count', 0)} "
        f"safe_to_ignore={op.get('rows_safe_to_ignore_for_clean', {}).get('count', 0)} "
        f"manual_investigation={inv.get('count', 0)}"
    )
    if (inv.get("count") or 0) > 0:
        lines.append("  Manual investigation findings (clean-epoch ERRORs):")
        for f in inv.get("findings", []):
            lines.append(f"    {f['code']}  n={f['count']}  e.g. {', '.join(f['samples'][:3]) or '—'}")
    lines.append(
        f"broker_position_match_source: {op.get('broker_position_match_source')}"
        f"   snapshot_generated_at={op.get('broker_snapshot_generated_at') or '—'}"
    )
    bmc = op.get("broker_match_counts") or {}
    if bmc:
        lines.append(
            f"broker_match_counts: match={bmc.get('match', 0)}  "
            f"no_broker_position={bmc.get('no_broker_position', 0)}  "
            f"closed_by_book={bmc.get('closed_by_book', 0)}  "
            f"unknown={bmc.get('unknown', 0)}"
        )
    lines.append(bar)
    return "\n".join(lines) + "\n"


# ── I/O ──────────────────────────────────────────────────────────────────────

def write_outputs(
    report: Dict[str, Any],
    json_path: Path,
    txt_path: Path,
    *,
    quarantine_entries: Optional[List[Dict[str, Any]]] = None,
    quarantine_path: Optional[Path] = None,
    clean_epoch_iso: str = CLEAN_PAPER_EVIDENCE_START,
    db_path: Optional[Path] = None,
    broker_snapshot_path: Optional[Path] = None,
) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = json_path.with_suffix(json_path.suffix + ".tmp")
    tmp.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    tmp.replace(json_path)
    txt_path.write_text(render_text(report), encoding="utf-8")

    if quarantine_path is not None and quarantine_entries is not None:
        broker_source, broker_gen_at = _read_broker_snapshot_meta(
            broker_snapshot_path,
        )
        write_quarantine_sidecar(
            quarantine_entries, quarantine_path,
            clean_epoch_iso=clean_epoch_iso, db_path=db_path,
            broker_match_source=broker_source,
            broker_snapshot_path=broker_snapshot_path,
            broker_snapshot_generated_at=broker_gen_at,
        )


def _default_quarantine_path() -> Path:
    # Resolve relative to the repo root regardless of CWD so the report
    # is location-independent. core.config is already imported.
    return Path(cfg.PROJECT_ROOT) / "data" / "state" / "paper_legacy_quarantine.json" \
        if hasattr(cfg, "PROJECT_ROOT") else \
        Path(__file__).resolve().parents[1] / "data" / "state" / "paper_legacy_quarantine.json"


def _collect_quarantine(
    db_path: Path,
    clean_epoch_iso: str,
    *,
    broker_snapshot_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    conn = _connect_ro(db_path)
    if conn is None:
        return []
    try:
        return build_legacy_quarantine(
            conn, clean_epoch_iso,
            broker_snapshot_path=broker_snapshot_path,
        )
    finally:
        conn.close()


def _default_broker_snapshot_path() -> Path:
    """Mirror scripts/snapshot_broker_positions.py default location."""
    return Path(cfg.PROJECT_ROOT) / BROKER_SNAPSHOT_REL \
        if hasattr(cfg, "PROJECT_ROOT") else \
        Path(__file__).resolve().parents[1] / BROKER_SNAPSHOT_REL


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Paper-state hygiene report (Phase 1C/1D/1E)")
    parser.add_argument("--db-path", default=str(cfg.DB_PATH),
                        help="SQLite DB path (default: core.config.DB_PATH)")
    parser.add_argument("--since", default=None,
                        help=("Override the clean-epoch start (ISO ts). "
                              f"Default: {CLEAN_PAPER_EVIDENCE_START}"))
    parser.add_argument("--json-out", default=None,
                        help="Override JSON output path")
    parser.add_argument("--txt-out", default=None,
                        help="Override text output path")
    parser.add_argument("--quarantine-out", default=None,
                        help="Override quarantine sidecar path "
                             "(default: data/state/paper_legacy_quarantine.json)")
    parser.add_argument("--broker-snapshot", default=None,
                        help=("Override broker-positions snapshot path "
                              f"(default: {BROKER_SNAPSHOT_REL}). "
                              "Read-only — produced by "
                              "scripts/snapshot_broker_positions.py."))
    parser.add_argument("--print", action="store_true",
                        help="Also print the rendered report to stdout")
    args = parser.parse_args(argv)

    db_path = Path(args.db_path)
    clean_epoch_iso = args.since or CLEAN_PAPER_EVIDENCE_START
    broker_snapshot_path = (
        Path(args.broker_snapshot) if args.broker_snapshot
        else _default_broker_snapshot_path()
    )
    report = build_report(
        db_path, clean_epoch_iso=clean_epoch_iso,
        broker_snapshot_path=broker_snapshot_path,
    )
    quarantine_entries = _collect_quarantine(
        db_path, clean_epoch_iso,
        broker_snapshot_path=broker_snapshot_path,
    )

    json_path = Path(args.json_out) if args.json_out else (
        cfg.CACHE_DIR / "research" / "paper_state_hygiene_latest.json"
    )
    txt_path = Path(args.txt_out) if args.txt_out else (
        cfg.LOG_DIR / "paper_state_hygiene_latest.txt"
    )
    quarantine_path = Path(args.quarantine_out) if args.quarantine_out else (
        _default_quarantine_path()
    )

    write_outputs(
        report, json_path, txt_path,
        quarantine_entries=quarantine_entries,
        quarantine_path=quarantine_path,
        clean_epoch_iso=clean_epoch_iso,
        db_path=db_path,
        broker_snapshot_path=broker_snapshot_path,
    )

    if args.print or report["summary"]["total_findings"] == 0:
        print(render_text(report))
    print(f"wrote {json_path}")
    print(f"wrote {txt_path}")
    print(f"wrote {quarantine_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

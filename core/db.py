"""
core/db.py — hardened SQLite connection helper.

Phase 0 hardening: every primary writer to ``cfg.DB_PATH`` should connect
through ``connect()`` here so the connection has consistent durability and
concurrency settings.  This module does NOT modify schema or data — it
only sets connection-level / database-level PRAGMAs.

PRAGMAs applied on every connect:
  - journal_mode=WAL       persistent on the DB file; idempotent.  WAL is
                           required for safe concurrent reader+writer use
                           (data_gatekeeper writes from the main loop while
                           decision_logger and paper_logger write from the
                           same process; nightly research scripts read from
                           a separate process).
  - synchronous=FULL       fsync after every commit.  Slightly slower than
                           NORMAL but rules out torn writes on power loss.
                           Required when the DB holds open-position state.
  - busy_timeout=5000      5s busy-wait on locked DB before raising.
                           Tames the rare collision between the main loop
                           and a nightly script.
  - foreign_keys=ON        defensive; the schema currently does not declare
                           FKs, but enabling here means future ones are
                           enforced from day one.

This module is read-only with respect to schema.  It will NOT create,
alter, drop, or migrate any table.  Callers retain full responsibility for
DDL — exactly the same surface they had before.

Usage:
    from core.db import connect
    conn = connect(db_path, check_same_thread=False)
    # … use conn as before …

Verification helper:
    from core.db import verify_hardening
    info = verify_hardening(db_path)   # dict of pragma values
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, Union

logger = logging.getLogger("core.db")


_BUSY_TIMEOUT_MS = 5000


def connect(
    db_path: Union[str, Path],
    *,
    check_same_thread: bool = True,
    timeout: float = 5.0,
    **kwargs: Any,
) -> sqlite3.Connection:
    """Open a hardened SQLite connection.

    Mirrors the ``sqlite3.connect`` signature.  Extra kwargs are forwarded
    so callers can keep their original arguments untouched.

    The PRAGMA writes are wrapped in a try/except: if the underlying DB is
    in a broken state we still return the open connection so the caller's
    own error path can surface it (rather than crashing inside this
    helper).  The PRAGMA failure is logged at WARNING level.
    """
    conn = sqlite3.connect(
        str(db_path),
        check_same_thread=check_same_thread,
        timeout=timeout,
        **kwargs,
    )
    try:
        # journal_mode is sticky on the DB file; calling it on every connect
        # is safe and idempotent.  ``synchronous`` is per-connection and
        # MUST be set every time.
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=FULL")
        cur.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()
    except sqlite3.Error as exc:  # pragma: no cover - defensive
        logger.warning("db hardening PRAGMAs failed for %s: %s", db_path, exc)
    return conn


def verify_hardening(db_path: Union[str, Path]) -> Dict[str, Any]:
    """Return a dict snapshot of the durability-relevant PRAGMA values for
    ``db_path``.  Used by tests and ops scripts to confirm the DB is in
    the expected state without performing any writes."""
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        out: Dict[str, Any] = {}
        for pragma in ("journal_mode", "synchronous", "busy_timeout",
                       "foreign_keys"):
            row = cur.execute(f"PRAGMA {pragma}").fetchone()
            out[pragma] = row[0] if row else None
        return out
    finally:
        conn.close()

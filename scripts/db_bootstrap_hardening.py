#!/usr/bin/env python3
"""
scripts/db_bootstrap_hardening.py — one-shot WAL + integrity bootstrap.

Run once after Phase 0 hardening lands.  Idempotent: safe to re-run.

Actions per DB file:
  1. ``PRAGMA integrity_check`` — abort with non-zero exit on corruption.
  2. ``PRAGMA journal_mode=WAL`` — flips the journal mode persistently
     in the DB header.  No-op if already WAL.
  3. Print before/after PRAGMA snapshot for the operator's visibility.

Does NOT migrate schema, does NOT touch row data, does NOT vacuum (vacuum
is incompatible with WAL mode in some configurations).  If integrity_check
fails, the script exits 1 BEFORE attempting the journal-mode change so a
suspect DB is never silently rewritten.

Usage:
    .venv/bin/python scripts/db_bootstrap_hardening.py
    .venv/bin/python scripts/db_bootstrap_hardening.py --paths db/trading.db
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parent.parent

# Live DBs that the production system writes through.  trading.db is the
# canonical one (cfg.DB_PATH).  trading_performance.db is legacy duplicate
# but still referenced by some research scripts; we harden both for safety.
DEFAULT_DBS: List[Path] = [
    ROOT / "db" / "trading.db",
    ROOT / "db" / "trading_performance.db",
]


def _snapshot(conn: sqlite3.Connection) -> dict:
    cur = conn.cursor()
    out: dict = {}
    for pragma in ("journal_mode", "synchronous", "busy_timeout"):
        row = cur.execute(f"PRAGMA {pragma}").fetchone()
        out[pragma] = row[0] if row else None
    return out


def _bootstrap_one(db_path: Path) -> int:
    if not db_path.exists():
        print(f"  SKIP   {db_path}  (does not exist)")
        return 0

    conn = sqlite3.connect(str(db_path))
    try:
        before = _snapshot(conn)
        # Step 1: integrity check.  Abort if not 'ok'.
        rows = conn.execute("PRAGMA integrity_check").fetchall()
        bad = [str(r[0]) for r in rows if str(r[0]).lower() != "ok"]
        if bad:
            print(f"  FAIL   {db_path}  integrity_check returned: {bad[:3]}")
            return 1
        # Step 2: enable WAL persistently.
        new_mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()
        # Synchronous=FULL is per-connection so we set it here too even
        # though application connect() will reset it on every open.
        conn.execute("PRAGMA synchronous=FULL")
        after = _snapshot(conn)
        print(f"  OK     {db_path}  "
              f"journal_mode {before['journal_mode']!r} → {after['journal_mode']!r}, "
              f"synchronous = {after['synchronous']!r}, "
              f"new_mode_returned={new_mode[0] if new_mode else None!r}")
        return 0
    finally:
        conn.close()


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bootstrap WAL + integrity check on live SQLite DBs.")
    parser.add_argument("--paths", nargs="*", default=None,
                        help=("explicit DB paths (overrides defaults). "
                              f"defaults: {', '.join(str(p) for p in DEFAULT_DBS)}"))
    args = parser.parse_args(argv)
    paths = [Path(p) for p in args.paths] if args.paths else DEFAULT_DBS

    print(f"DB hardening bootstrap — {len(paths)} target(s)")
    rc = 0
    for p in paths:
        rc |= _bootstrap_one(p)
    if rc:
        print("\nbootstrap FAILED on at least one DB.  Investigate before"
              " running the trader.")
    else:
        print("\nbootstrap complete.")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())

"""
scripts/retire_unfilled_decisions.py — one-time hygiene cleanup.

Retires the historical "stuck_unfilled" decision rows: orders that were
submitted on a scan cycle, never filled (``position_opened=0`` and
``position_closed=0`` with a non-empty ``order_id``), and have long since
expired (DAY orders from the 2026-04-23 → 2026-05-08 window — the PEGA /
CRS / BRO duplicate-scan bursts). The broker is flat and the
reconcile-drift investigator has confirmed none of them match a real
fill, so they are inert audit cruft that nonetheless keeps the
investigator's ``stuck_unfilled`` counter pinned at 69.

Resolution model (data-only, honest, audit-preserving):
  • Move the dead ``order_id`` into ``notes`` (the order is expired; the
    id no longer points at anything actionable) and blank ``order_id`` so
    the row leaves the investigator's stuck query
    (``position_opened=0 AND position_closed=0 AND order_id != ''``).
  • Stamp a terminal ``suspect_state`` — keep an existing
    ``duplicate_scan`` / ``never_filled`` label; assign ``never_filled``
    to any un-adjudicated (NULL) row.
  • Set ``reconciled_at``.

Deliberately NOT done:
  • No ``position_closed=1`` — that would trip the hygiene report's
    ``DECISIONS_CLOSED_MISSING_PNL_EXIT`` check (closed-but-no-pnl).
  • No fabricated ``pnl`` / ``exit_price`` — these orders never filled,
    so there is no realized trade to record.

The only active reader of ``decisions.order_id`` is this investigator's
own stuck display; production reads order ids from the live broker
result dict, not the table. Verified before writing.

Usage:
  SNIPER_ENV_PATH=/home/gem/secure/trading.env \\
    .venv/bin/python scripts/retire_unfilled_decisions.py            # dry-run
  SNIPER_ENV_PATH=/home/gem/secure/trading.env \\
    .venv/bin/python scripts/retire_unfilled_decisions.py --apply    # mutate

Default is dry-run (no writes). With ``--apply`` the script takes a fresh
WAL-safe DB backup, then applies the retirement inside a single
transaction.
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("retire_unfilled_decisions")

REPO = Path(__file__).resolve().parents[1]
DB_PATH = REPO / "db" / "trading.db"

# The investigator's stuck_unfilled signature.
STUCK_WHERE = (
    "position_opened=0 AND position_closed=0 "
    "AND order_id IS NOT NULL AND order_id != ''"
)


def fetch_stuck(con: sqlite3.Connection) -> list[sqlite3.Row]:
    con.row_factory = sqlite3.Row
    return con.execute(
        f"SELECT id, ts, ticker, strategy, order_id, fill_status, "
        f"suspect_state, notes FROM decisions WHERE {STUCK_WHERE} ORDER BY ts"
    ).fetchall()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="mutate the DB (default: dry-run)")
    args = ap.parse_args()

    con = sqlite3.connect(str(DB_PATH))
    rows = fetch_stuck(con)
    now = datetime.now(timezone.utc).isoformat()

    by_state: dict[str, int] = {}
    for r in rows:
        by_state[r["suspect_state"] or "(null→never_filled)"] = (
            by_state.get(r["suspect_state"] or "(null→never_filled)", 0) + 1
        )

    print(f"== retire unfilled decisions ({now}) ==")
    print(f"  candidates = {len(rows)}")
    for state, n in sorted(by_state.items()):
        print(f"    {state:24} {n}")
    # Per-ticker preview.
    tick: dict[str, int] = {}
    for r in rows:
        tick[r["ticker"]] = tick.get(r["ticker"], 0) + 1
    print("  by ticker:", ", ".join(f"{k}×{v}" for k, v in sorted(tick.items())))

    if not rows:
        print("  nothing to retire.")
        return 0

    if not args.apply:
        print("\n(dry-run — pass --apply to back up the DB and mutate)")
        return 0

    # Fresh WAL-safe backup before any write.
    backup = REPO / "scripts" / "backup_db.sh"
    if backup.exists():
        logger.info("taking DB backup via %s", backup)
        subprocess.run(["bash", str(backup)], check=True, cwd=str(REPO))
    else:  # pragma: no cover - backup script is part of the repo
        logger.warning("backup script not found at %s — proceeding without", backup)

    updated = 0
    with con:  # single transaction
        for r in rows:
            new_state = r["suspect_state"] or "never_filled"
            note_add = (
                f"retired_unfilled order_id={r['order_id']} "
                f"fill_status={r['fill_status'] or 'n/a'} @{now} "
                f"(expired never-filled scan order; broker flat)"
            )
            new_notes = (
                f"{r['notes']} | {note_add}" if r["notes"] else note_add
            )
            con.execute(
                "UPDATE decisions SET "
                "order_id='', "
                "suspect_state=?, "
                "suspect_reason=COALESCE(NULLIF(suspect_reason,''), ?), "
                "reconciled_at=?, "
                "notes=? "
                "WHERE id=?",
                (
                    new_state,
                    "expired never-filled scan order; broker confirmed flat",
                    now,
                    new_notes,
                    r["id"],
                ),
            )
            updated += 1

    remaining = len(fetch_stuck(con))
    logger.info("RETIRED %d row(s); stuck_unfilled now = %d", updated, remaining)
    print(f"\nRETIRED {updated}; stuck_unfilled now = {remaining}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

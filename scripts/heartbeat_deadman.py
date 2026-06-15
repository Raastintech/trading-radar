#!/usr/bin/env python3
"""
scripts/heartbeat_deadman.py — Phase 1A dead-man switch.

Reads the daemon's ``trader_heartbeat.json`` (written by ``main.write_heartbeat``)
and, if the ``last_heartbeat_ts`` is older than the configured threshold,
forces a global circuit-breaker halt.  The halt persists across process
restarts (Phase 0 ``circuit_breaker_state`` table) and DOES NOT
auto-clear — operator must run ``CircuitBreakers.clear_halt`` to resume.

Why a separate script (not in-process):
  - The thing we're guarding against is the in-process loop wedging,
    deadlocking, or otherwise failing to update the heartbeat.  An
    in-process timer can wedge with the loop.  An external cron-driven
    check is independent of the trader's runtime.

Usage (cron, every minute):
    * * * * * /home/gem/trading-production/.venv/bin/python \
        /home/gem/trading-production/scripts/heartbeat_deadman.py \
        --threshold-seconds 300

CLI options:
    --heartbeat PATH      override default LOG_DIR/trader_heartbeat.json
    --db-path PATH        override default cfg.DB_PATH (used by tests)
    --threshold-seconds N stale threshold; halt if heartbeat older (default 300)
    --dry-run             evaluate only; never call force_halt
    --reason TEXT         override the halt reason string

Exit codes:
    0  heartbeat fresh (no action needed) OR halt successfully tripped
    2  heartbeat missing or stale AND a halt was *attempted* but failed
    3  invalid CLI input

This script is paper- and live-safe.  It only writes to
``circuit_breaker_state`` and the journal.  It never closes positions,
cancels orders, or touches the broker.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Allow running outside the venv too — tests inject their own DB path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger("heartbeat_deadman")


def _parse_iso(ts: str) -> Optional[datetime]:
    try:
        # Tolerate trailing 'Z' for UTC.
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def evaluate(
    *,
    heartbeat_path: Path,
    threshold_seconds: int,
    now: Optional[datetime] = None,
) -> tuple[bool, str]:
    """Return (is_stale, reason).  Pure: no I/O beyond reading the
    heartbeat file."""
    now = now or datetime.now(timezone.utc)
    if not heartbeat_path.exists():
        return True, (
            f"heartbeat file missing at {heartbeat_path} "
            f"(expected within last {threshold_seconds}s)"
        )
    try:
        payload = json.loads(heartbeat_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return True, f"heartbeat file unreadable: {exc}"
    ts_raw = payload.get("last_heartbeat_ts")
    if not ts_raw:
        return True, "heartbeat payload missing last_heartbeat_ts"
    ts = _parse_iso(str(ts_raw))
    if ts is None:
        return True, f"heartbeat timestamp unparseable: {ts_raw!r}"
    age = (now - ts).total_seconds()
    if age > threshold_seconds:
        return True, (
            f"heartbeat stale: age={age:.0f}s threshold={threshold_seconds}s "
            f"last_ts={ts.isoformat()} stage={payload.get('heartbeat_stage')!r}"
        )
    return False, (
        f"heartbeat fresh: age={age:.0f}s threshold={threshold_seconds}s "
        f"stage={payload.get('heartbeat_stage')!r}"
    )


def trip_breaker(reason: str, db_path: Optional[str] = None) -> bool:
    """Force-halt via the persisted circuit_breaker_state table.
    Returns True on success.  Imports CircuitBreakers lazily so this
    script can run without the full venv when --dry-run."""
    try:
        from execution.circuit_breakers import CircuitBreakers
    except Exception as exc:
        logger.error("could not import CircuitBreakers: %s", exc)
        return False
    try:
        cb = CircuitBreakers(db_path=db_path) if db_path else CircuitBreakers()
        cb.force_halt(reason)
        return True
    except Exception as exc:
        logger.error("force_halt failed: %s", exc)
        return False


def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--heartbeat", type=str, default="",
                        help="path to trader_heartbeat.json "
                             "(default: <LOG_DIR>/trader_heartbeat.json)")
    parser.add_argument("--db-path", type=str, default="",
                        help="override SQLite DB path (test use)")
    parser.add_argument("--threshold-seconds", type=int, default=300,
                        help="stale threshold (default 300s = 5 min)")
    parser.add_argument("--dry-run", action="store_true",
                        help="evaluate only; never force_halt")
    parser.add_argument("--reason", type=str, default="",
                        help="override halt reason string")
    args = parser.parse_args(argv)

    if args.threshold_seconds <= 0:
        logger.error("--threshold-seconds must be positive")
        return 3

    # Resolve heartbeat path lazily so importing core.config never
    # happens unless we actually need it (lets the test pass an
    # explicit --heartbeat without env vars).
    if args.heartbeat:
        hb_path = Path(args.heartbeat)
    else:
        try:
            import core.config as cfg
        except Exception as exc:
            logger.error("could not load core.config to resolve heartbeat path: %s", exc)
            return 3
        hb_path = Path(cfg.LOG_DIR) / "trader_heartbeat.json"

    is_stale, reason = evaluate(
        heartbeat_path=hb_path,
        threshold_seconds=args.threshold_seconds,
    )
    if not is_stale:
        logger.info("OK %s", reason)
        return 0

    halt_reason = args.reason or f"heartbeat dead-man: {reason}"
    logger.warning("STALE %s", reason)
    if args.dry_run:
        logger.info("DRY-RUN — would force_halt(%r)", halt_reason)
        return 0
    ok = trip_breaker(halt_reason, db_path=args.db_path or None)
    if ok:
        logger.error("CIRCUIT BREAKER TRIPPED by heartbeat dead-man: %s",
                     halt_reason)
        return 0
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

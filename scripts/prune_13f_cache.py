"""
scripts/prune_13f_cache.py — rolling-window 13F cache hygiene.

13F filings drop quarterly with a 45-day lag. Once a new quarter
publishes, the prior quarter's snapshot is operationally useless —
the radar consumes whatever the institutions most recently reported.
Holding stale quarter rows in ``cache_meta`` only inflates the cache,
confuses operators, and risks the radar returning a mix of Q-over-Q
diffs computed off different vintages.

Policy (set by the 2026-05-15 operator note):
  - Keep only entries whose ``last_quarter`` equals the latest quarter
    present in cache (default), OR
  - Keep only entries whose ``last_quarter`` is on/after a configured
    threshold (e.g. 2026-01-01), OR
  - Keep only entries whose ``last_quarter`` matches an explicit
    quarter date (e.g. 2026-03-31).

Everything older is removed from ``cache_meta``. Entries with no
``last_quarter`` (UNKNOWN/transient SEC failure cache rows) are also
dropped so the next radar call retries with a clean slate.

This script is operator-invoked. ``scripts/nightly_refresh.py`` calls
the same prune logic so the rolling-window policy stays enforced
without manual intervention.

Usage:
  # See what would change without mutating:
  GEM_TRADER_SKIP_DOTENV=true .venv/bin/python scripts/prune_13f_cache.py

  # Drop everything older than today's known frontier (auto mode):
  GEM_TRADER_SKIP_DOTENV=true .venv/bin/python scripts/prune_13f_cache.py --apply --keep-latest

  # Drop anything strictly before 2026-01-01:
  GEM_TRADER_SKIP_DOTENV=true .venv/bin/python scripts/prune_13f_cache.py --apply --min-quarter 2026-01-01

  # Keep only entries pinned to a specific quarter:
  GEM_TRADER_SKIP_DOTENV=true .venv/bin/python scripts/prune_13f_cache.py --apply --keep-quarter 2026-03-31

Exit codes:
  0   prune completed (or dry-run rendered)
  2   config error / DB unavailable
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

# Env-load only when explicitly needed — this tool only touches the local
# SQLite cache and never calls a provider.
_env_path = os.environ.get("SNIPER_ENV_PATH", "")
if _env_path:
    try:
        from dotenv import load_dotenv as _load
        _load(Path(_env_path), override=True)
    except ImportError:
        pass

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("prune_13f_cache")


CACHE_KEY_PREFIX = "13f:"


def _resolve_db_path() -> Path:
    """Honor cfg.DB_PATH when creds are loaded; fall back to the repo
    default so the tool runs even with GEM_TRADER_SKIP_DOTENV=true."""
    try:
        import core.config as cfg  # noqa: E402
        return Path(cfg.DB_PATH)
    except Exception:
        return _ROOT / "db" / "trading.db"


# ── Core selection logic ────────────────────────────────────────────────────

def load_entries(con: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Return one dict per 13F cache row with the parsed last_quarter."""
    rows = con.execute(
        "SELECT key, fetched_at, payload FROM cache_meta WHERE key LIKE ?",
        (CACHE_KEY_PREFIX + "%",),
    ).fetchall()
    out: List[Dict[str, Any]] = []
    for key, fetched_at, payload in rows:
        last_q: Optional[str] = None
        try:
            blob = json.loads(payload) if payload else {}
            last_q = blob.get("last_quarter") if isinstance(blob, dict) else None
        except Exception:
            last_q = None
        out.append({
            "key": key,
            "fetched_at": float(fetched_at or 0),
            "last_quarter": last_q,
        })
    return out


def select_for_eviction(
    entries: List[Dict[str, Any]],
    *,
    mode: str,
    keep_quarter: Optional[str] = None,
    min_quarter: Optional[str] = None,
    keep_unknown: bool = False,
) -> Tuple[List[Dict[str, Any]], str]:
    """Partition rows into (to_evict, surviving). Returns (evict, summary).

    ``mode`` is one of:
      - 'latest'   — keep only entries whose last_quarter == max present
      - 'quarter'  — keep only entries whose last_quarter == keep_quarter
      - 'min'      — keep entries whose last_quarter >= min_quarter (ISO)

    UNKNOWN entries (last_quarter is None) are evicted unless
    ``keep_unknown`` is True. Holding UNKNOWN rows is almost always a
    transient-failure artifact; clearing them forces a fresh retry."""
    quarters = sorted({e["last_quarter"] for e in entries
                       if e["last_quarter"]})
    if mode == "latest":
        if not quarters:
            target = None
        else:
            target = quarters[-1]
        summary = f"keep_latest=true frontier={target or 'none'}"
        def _keep(e):
            if e["last_quarter"] is None:
                return keep_unknown
            return target is not None and e["last_quarter"] == target

    elif mode == "quarter":
        if not keep_quarter:
            raise ValueError("--keep-quarter requires a YYYY-MM-DD value")
        target = keep_quarter
        summary = f"keep_quarter={target}"
        def _keep(e):
            if e["last_quarter"] is None:
                return keep_unknown
            return e["last_quarter"] == target

    elif mode == "min":
        if not min_quarter:
            raise ValueError("--min-quarter requires a YYYY-MM-DD value")
        target = min_quarter
        summary = f"min_quarter={target} (keep if last_quarter >= target)"
        def _keep(e):
            if e["last_quarter"] is None:
                return keep_unknown
            return e["last_quarter"] >= target

    else:
        raise ValueError(f"unknown mode: {mode}")

    evict = [e for e in entries if not _keep(e)]
    return evict, summary


def apply_eviction(con: sqlite3.Connection, to_evict: List[Dict[str, Any]]) -> int:
    """Delete rows from cache_meta. Returns the count actually removed."""
    if not to_evict:
        return 0
    keys = [e["key"] for e in to_evict]
    with con:
        con.executemany(
            "DELETE FROM cache_meta WHERE key = ?",
            [(k,) for k in keys],
        )
    return len(keys)


# ── CLI ─────────────────────────────────────────────────────────────────────

def render_report(
    entries: List[Dict[str, Any]],
    to_evict: List[Dict[str, Any]],
    summary: str,
    *,
    applied: bool,
) -> str:
    by_q: Dict[str, int] = {}
    for e in entries:
        k = e["last_quarter"] or "UNKNOWN"
        by_q[k] = by_q.get(k, 0) + 1
    evict_by_q: Dict[str, int] = {}
    for e in to_evict:
        k = e["last_quarter"] or "UNKNOWN"
        evict_by_q[k] = evict_by_q.get(k, 0) + 1

    lines = []
    lines.append(f"== 13F cache prune ({summary}) ==")
    lines.append(f"total entries: {len(entries)}")
    lines.append("by last_quarter:")
    for q in sorted(by_q):
        flag = " (evict)" if evict_by_q.get(q, 0) == by_q[q] else ""
        partial = ""
        if 0 < evict_by_q.get(q, 0) < by_q[q]:
            partial = f"  ({evict_by_q[q]}/{by_q[q]} flagged for evict)"
        lines.append(f"  {q:<14} {by_q[q]:>4}{flag}{partial}")
    lines.append(f"would evict: {len(to_evict)} entries")
    lines.append(f"would survive: {len(entries) - len(to_evict)} entries")
    lines.append("status: " + ("APPLIED" if applied else "dry-run"))
    if not applied:
        lines.append("(pass --apply to mutate the DB)")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Prune stale 13F cache entries to a rolling window.")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--keep-latest", action="store_true",
                   help="Keep only entries whose last_quarter is the "
                        "max present in cache (default).")
    g.add_argument("--keep-quarter", metavar="YYYY-MM-DD",
                   help="Keep only entries whose last_quarter matches "
                        "this exact quarter end date.")
    g.add_argument("--min-quarter", metavar="YYYY-MM-DD",
                   help="Keep entries whose last_quarter >= this date.")
    p.add_argument("--keep-unknown", action="store_true",
                   help="Retain entries with no last_quarter (UNKNOWN). "
                        "By default these are evicted so the next radar "
                        "call retries with a clean slate.")
    p.add_argument("--apply", action="store_true",
                   help="Actually delete the selected rows. Default is "
                        "dry-run.")
    p.add_argument("--db", metavar="PATH", default=None,
                   help="Override the SQLite path (default: cfg.DB_PATH).")
    args = p.parse_args(argv)

    db_path = Path(args.db) if args.db else _resolve_db_path()
    if not db_path.exists():
        logger.error("trading DB not found: %s", db_path)
        return 2

    # Determine mode and target.
    if args.keep_quarter:
        mode = "quarter"
    elif args.min_quarter:
        mode = "min"
    else:
        mode = "latest"

    con = sqlite3.connect(str(db_path))
    try:
        entries = load_entries(con)
        to_evict, summary = select_for_eviction(
            entries,
            mode=mode,
            keep_quarter=args.keep_quarter,
            min_quarter=args.min_quarter,
            keep_unknown=args.keep_unknown,
        )
        applied = False
        if args.apply and to_evict:
            apply_eviction(con, to_evict)
            applied = True
        print(render_report(entries, to_evict, summary, applied=applied))
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

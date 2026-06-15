#!/usr/bin/env python3
"""
scripts/check_13f.py — operator-invoked 13F activity report for a ticker.

Reads ``core.whale_tracker.WhaleTracker.get_institutional_activity()`` and
prints a clean Q-over-Q view of which tracked institutions have
increased / decreased / opened / closed / held their position in the
named ticker over the most recent two 13F-HR filings on file at SEC
EDGAR.

This is a research / diagnostic tool.  It does NOT trigger any radar /
scanner / order path.  It is the canonical CLI for spot-checking the
same data the social-arb radar consumes per candidate.

Caching:
  Whale tracker caches results 24h (10 min if the sweep returned
  UNKNOWN due to a transient SEC failure).  Use ``--no-cache`` to evict
  the persistent cache for the named tickers and force a fresh sweep.

Usage:
  SNIPER_ENV_PATH=/home/gem/secure/trading.env \\
    .venv/bin/python scripts/check_13f.py AAPL
  SNIPER_ENV_PATH=/home/gem/secure/trading.env \\
    .venv/bin/python scripts/check_13f.py AAPL NVDA MSFT --no-cache
  SNIPER_ENV_PATH=/home/gem/secure/trading.env \\
    .venv/bin/python scripts/check_13f.py NVDA --json

Exit codes:
  0   report printed for at least one ticker
  1   whale tracker unavailable (edgartools missing or init failed)
  2   environment / config error
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

# Standard env-load pattern used by every credential-requiring tool.
_env_path = os.environ.get("SNIPER_ENV_PATH", "")
if _env_path:
    try:
        from dotenv import load_dotenv as _load
        _load(Path(_env_path), override=True)
    except ImportError:
        pass

# Allow `python scripts/check_13f.py` from repo root.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _evict_cache(tickers: Sequence[str]) -> int:
    """Delete the persistent Gatekeeper cache entries for the given tickers
    so the next sweep is forced fresh.  Returns count of rows deleted."""
    try:
        import core.config as cfg
    except Exception:
        return 0
    deleted = 0
    try:
        con = sqlite3.connect(str(cfg.DB_PATH), timeout=3)
        for t in tickers:
            cur = con.execute("DELETE FROM cache_meta WHERE key=?", (f"13f:v2:{t.upper()}",))
            deleted += cur.rowcount or 0
        con.commit()
        con.close()
    except sqlite3.Error:
        pass
    return deleted


def _fmt_shares(n: Optional[int]) -> str:
    if n is None:
        return "       -"
    n = int(n)
    sign = "-" if n < 0 else " "
    n = abs(n)
    if n >= 1_000_000:
        return f"{sign}{n/1_000_000:6.1f}M"
    if n >= 1_000:
        return f"{sign}{n/1_000:6.1f}K"
    return f"{sign}{n:6d} "


def _format_report(ticker: str, activity: Optional[Dict[str, Any]]) -> str:
    """Build a human-readable per-ticker section."""
    lines: List[str] = []
    bar = "─" * 64
    lines.append("")
    lines.append(bar)
    lines.append(f"  {ticker.upper()}")
    lines.append(bar)

    if activity is None:
        lines.append("  whale tracker returned None (edgartools missing / init failed)")
        return "\n".join(lines)

    net_flow = activity.get("net_flow") or "?"
    conf = activity.get("confidence") or "?"
    n_buy = int(activity.get("whales_buying") or 0)
    n_sell = int(activity.get("whales_selling") or 0)
    n_hold = int(activity.get("whales_holding") or 0)
    ic = int(activity.get("institutions_checked") or 0)
    period = activity.get("last_quarter") or "n/a"

    lines.append(
        f"  net_flow={net_flow:8}  confidence={conf:8}  "
        f"institutions_checked={ic:2}/{16}  period={period}"
    )
    lines.append(
        f"  whales: {n_buy:>2} buying · {n_sell:>2} selling · {n_hold:>2} holding"
    )

    if net_flow == "UNKNOWN":
        lines.append("")
        lines.append("  ⚠  UNKNOWN sweep — either SEC EDGAR transient failure or")
        lines.append("     ticker is not held by any of the 16 tracked institutions.")
        return "\n".join(lines)

    top_buyers = activity.get("top_buyers") or []
    top_sellers = activity.get("top_sellers") or []

    if top_buyers:
        lines.append("")
        lines.append("  top buyers:")
        for b in top_buyers:
            lines.append(
                f"    + {b.get('name',''):24}  "
                f"{_fmt_shares(b.get('shares_added'))}  "
                f"({float(b.get('change_pct') or 0):+.1f}%)"
            )

    if top_sellers:
        lines.append("")
        lines.append("  top sellers:")
        for s in top_sellers:
            lines.append(
                f"    - {s.get('name',''):24}  "
                f"{_fmt_shares(-int(s.get('shares_removed') or 0))}  "
                f"({-float(s.get('change_pct') or 0):+.1f}%)"
            )

    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Print recent 13F activity for one or more tickers.",
    )
    parser.add_argument(
        "tickers",
        nargs="+",
        help="Ticker(s) to check, e.g. AAPL NVDA MSFT",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Evict the Gatekeeper cache for the named tickers before fetching "
             "so the SEC sweep runs fresh.  Slower (5-30s per cold ticker).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON instead of the text report.",
    )
    args = parser.parse_args(argv)

    tickers = [t.upper().strip() for t in args.tickers if t.strip()]
    if not tickers:
        print("no tickers provided", file=sys.stderr)
        return 2

    try:
        from core.whale_tracker import get_whale_tracker
    except Exception as exc:
        print(f"failed to import whale_tracker: {exc}", file=sys.stderr)
        return 2

    tracker = get_whale_tracker()
    if tracker is None:
        print("whale tracker unavailable (edgartools not installed or init failed)",
              file=sys.stderr)
        return 1

    if args.no_cache:
        deleted = _evict_cache(tickers)
        if not args.json:
            print(f"[cache] evicted {deleted} entries (forced fresh sweep)")

    results: Dict[str, Optional[Dict[str, Any]]] = {}
    for t in tickers:
        t0 = time.time()
        activity = tracker.get_institutional_activity(t)
        elapsed = time.time() - t0
        results[t] = activity
        if not args.json:
            print(_format_report(t, activity))
            print(f"  ({elapsed:.1f}s)")

    if args.json:
        print(json.dumps(results, indent=2, default=str))

    return 0


if __name__ == "__main__":
    sys.exit(main())

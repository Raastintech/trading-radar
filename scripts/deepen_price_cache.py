#!/usr/bin/env python3
"""
scripts/deepen_price_cache.py — Phase 1G.7 Task 1 gated refresh tool.

Fetches deep daily history (default ≥300 trading bars) for a prioritised research
universe and writes it MERGE-ON-WRITE to cache/prices_deep/{TICKER}.parquet — a
SEPARATE cache from cache/prices, which the nightly FMP pre-warm overwrites with
~260-bar windows. Research dataio.load_prices prefers the deep cache when present,
so deepening durably improves the MA200/260-bar evidence without touching any
execution path.

Phase 3A (2026-06-13): Alpaca removed. Provider is now FMP (get_ticker_bars per
ticker). FMP budget impact: one API call per ticker per run.

SAFETY:
  * Default mode is DRY-RUN: prints the plan and makes ZERO provider calls.
  * --execute is REQUIRED to contact FMP.
  * Writes ONLY cache/prices_deep. Never the DB, governance, paper signals, or
    live-capital settings.

Usage
-----
    # Dry-run the prioritised plan (no provider calls):
    SNIPER_ENV_PATH=/home/gem/secure/trading.env \
        python scripts/deepen_price_cache.py --priority

    # Execute the prioritised refresh (FMP provider calls):
    SNIPER_ENV_PATH=/home/gem/secure/trading.env \
        python scripts/deepen_price_cache.py --priority --execute

    # One-off ticker list:
    SNIPER_ENV_PATH=/home/gem/secure/trading.env \
        python scripts/deepen_price_cache.py --tickers NVDA AMD MU --execute
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

_repo = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_repo))

_cred = os.environ.get("SNIPER_ENV_PATH", "/home/gem/secure/trading.env")
if Path(_cred).exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_cred, override=True)
    except ImportError:
        pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("deepen")

DEEP_DIR = _repo / "cache" / "prices_deep"


def _priority_tickers(target_bars: int) -> list:
    """Reuse the cache-only planner to assemble the prioritised, deduped list."""
    from research import price_cache_deepening_plan as P
    plan = P.build()
    out: list = []
    for b in plan["priority_batches"]:
        out += b["tickers_needing_deepen"]
    # dedupe preserve order
    return list(dict.fromkeys(out))


def _merge_write(ticker: str, rows: list) -> int:
    import pandas as pd
    if not rows:
        return 0
    df = pd.DataFrame(rows)
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
        df.set_index("date", inplace=True)
    df.sort_index(inplace=True)
    path = DEEP_DIR / f"{ticker.upper()}.parquet"
    if path.exists():
        try:
            old = pd.read_parquet(path)
            old.index = pd.to_datetime(old.index)
            df = pd.concat([old, df]).sort_index()
            df = df[~df.index.duplicated(keep="last")]
        except Exception:
            pass
    DEEP_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, compression="snappy")
    return int(df["close"].notna().sum()) if "close" in df.columns else len(df)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Deepen the research price cache (gated).")
    ap.add_argument("--priority", action="store_true",
                    help="use the prioritised plan (alpha/RS/theme/winners/positions)")
    ap.add_argument("--tickers", nargs="*", default=None, help="explicit ticker list")
    ap.add_argument("--target-bars", type=int, default=300)
    ap.add_argument("--max", type=int, default=0, help="cap number of tickers (0=all)")
    ap.add_argument("--execute", action="store_true",
                    help="REQUIRED to make provider calls; default is dry-run")
    args = ap.parse_args(argv)

    if args.tickers:
        tickers = [t.upper() for t in args.tickers]
    elif args.priority:
        tickers = _priority_tickers(args.target_bars)
    else:
        ap.error("specify --priority or --tickers")
        return 64
    if args.max and args.max > 0:
        tickers = tickers[:args.max]

    log.info("deepen target=%d bars · %d tickers · deep cache=%s",
             args.target_bars, len(tickers), DEEP_DIR)

    if not args.execute:
        log.info("DRY-RUN (no provider calls). Re-run with --execute to fetch.")
        preview = ", ".join(tickers[:25]) + (" …" if len(tickers) > 25 else "")
        log.info("would deepen: %s", preview or "(none — cache already deep)")
        return 0

    # --- provider path (only reached with --execute) ---
    # Phase 3A: FMP replaces Alpaca. One API call per ticker.
    from core.fmp_client import get_fmp
    fmp = get_fmp()
    total_written = 0
    for sym in tickers:
        try:
            bars = fmp.get_ticker_bars(sym, days=args.target_bars)
        except Exception as exc:
            log.warning("FMP fetch failed for %s: %s", sym, exc)
            bars = []
        n = _merge_write(sym, bars)
        total_written += 1 if n else 0
    log.info("done · deep parquets written/updated: %d → %s", total_written, DEEP_DIR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

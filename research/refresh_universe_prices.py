#!/usr/bin/env python3
"""
research/refresh_universe_prices.py — Shallow price-cache refresh for the scan universe.

Problem this solves (2026-07-02 staleness audit): nothing refreshes
cache/prices/*.parquet for the research-scanner universe since the trading
daemon's scan loop was decommissioned — 95% of parquets froze at 2026-06-12
while SPY stayed current, so every RS/momentum figure the scanner computed
was silently comparing 3-week-old ticker prices against today's SPY.

This script re-warms the SHALLOW cache (cache/prices/) that the research
scanner actually reads:

  1. Reads the current scan universe from
     cache/research/research_universe_build_latest.json (universe_full).
  2. Skips tickers whose parquet last bar is already within --stale-days
     of today (default 2 calendar days — fresh through the last session).
  3. Fetches daily bars from FMP (get_ticker_bars, one call per ticker) and
     merge-writes via DataGatekeeper.put_prices (merge-on-write preserves
     existing history; fresh bars win on overlap).
  4. Stops at --max-calls provider calls (default 1100) so a bad universe
     file cannot burn the FMP budget.

RESEARCH-ONLY / DATA-COLLECTION-ONLY: no signals, no DB row writes outside
the gatekeeper cache tables, no execution paths.

Outputs:
  cache/research/universe_price_refresh_latest.json
  logs/universe_price_refresh_latest.txt

Usage:
  # Dry-run (default): report what would be fetched, no provider calls
  SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python research/refresh_universe_prices.py

  # Actually fetch
  SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python research/refresh_universe_prices.py --execute
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None

if load_dotenv is not None and os.getenv("GEM_TRADER_SKIP_DOTENV", "").lower() not in {"1", "true", "yes"}:
    env_path = os.getenv("SNIPER_ENV_PATH", "").strip()
    if env_path:
        load_dotenv(env_path, override=False)
    load_dotenv(ROOT / ".env", override=False)

os.environ.setdefault("ALPACA_API_KEY", "offline")
os.environ.setdefault("ALPACA_SECRET_KEY", "offline")
os.environ.setdefault("FMP_API_KEY", "offline")
os.environ.setdefault("FMP_BASE_URL", "https://financialmodelingprep.com/stable")
os.environ.setdefault("ALPACA_PAPER", "true")

import pandas as pd

import core.config as cfg

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s %(message)s")
logger = logging.getLogger("refresh_universe_prices")

PRICE_DIR = cfg.CACHE_DIR / "prices"
RESEARCH_DIR = cfg.CACHE_DIR / "research"
UNIVERSE_BUILD_JSON = RESEARCH_DIR / "research_universe_build_latest.json"
OUT_JSON = RESEARCH_DIR / "universe_price_refresh_latest.json"
OUT_TXT = cfg.LOG_DIR / "universe_price_refresh_latest.txt"

DEFAULT_STALE_DAYS = 2       # parquet last bar within this many calendar days = fresh
DEFAULT_MAX_CALLS = 1100     # provider-call ceiling per run
FMP_BARS_DAYS = 320          # ~330 trading bars — covers MA200 / 252d lookbacks


def _is_offline_fmp() -> bool:
    return os.getenv("FMP_API_KEY", "").strip().lower() in {"", "offline", "stub"}


def _load_universe() -> List[str]:
    """Scan universe from the last universe build; SPY always included."""
    tickers: List[str] = ["SPY"]
    try:
        data = json.loads(UNIVERSE_BUILD_JSON.read_text())
        for entry in data.get("universe_full") or []:
            t = str(entry.get("ticker") or "").upper()
            if t and t not in tickers:
                tickers.append(t)
    except Exception as exc:
        logger.warning("universe build sidecar unreadable (%s) — SPY only", exc)
    return tickers


def _parquet_last_date(ticker: str) -> Optional[str]:
    path = PRICE_DIR / f"{ticker.upper()}.parquet"
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path)
        if df.empty:
            return None
        return str(pd.to_datetime(df.index).max().date())
    except Exception:
        return None


def _fetch_and_merge(ticker: str) -> Dict[str, Any]:
    """One FMP call → merge-write shallow parquet. Returns result entry."""
    from core.data_gatekeeper import get_gatekeeper
    from core.fmp_client import get_fmp

    entry: Dict[str, Any] = {"ticker": ticker, "ok": False, "bars_after": 0}
    try:
        rows = get_fmp().get_ticker_bars(ticker, days=FMP_BARS_DAYS)
        if not rows:
            entry["reason"] = "fmp_empty"
            return entry
        df = pd.DataFrame(rows)
        if "date" not in df.columns:
            entry["reason"] = "no_date_column"
            return entry
        df["date"] = pd.to_datetime(df["date"])
        df.set_index("date", inplace=True)
        df.sort_index(inplace=True)
        cols = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
        get_gatekeeper().put_prices(ticker, df[cols])
        entry["ok"] = True
        entry["bars_fetched"] = len(df)
        entry["bars_after"] = entry["bars_fetched"]
        entry["last_bar"] = str(df.index.max().date())
    except Exception as exc:
        entry["reason"] = f"error: {exc}"
    return entry


def run(stale_days: int, max_calls: int, execute: bool, limit: Optional[int] = None) -> Dict[str, Any]:
    universe = _load_universe()
    if limit:
        universe = universe[:limit]
    today = datetime.now(timezone.utc).date()
    cutoff = today - timedelta(days=stale_days)

    fresh: List[str] = []
    missing: List[str] = []
    stale: List[Dict[str, Any]] = []
    for t in universe:
        last = _parquet_last_date(t)
        if last is None:
            missing.append(t)
            stale.append({"ticker": t, "last_bar": None})
        elif datetime.strptime(last, "%Y-%m-%d").date() >= cutoff:
            fresh.append(t)
        else:
            stale.append({"ticker": t, "last_bar": last})

    to_fetch = stale[:max_calls]
    truncated = len(stale) - len(to_fetch)

    results: List[Dict[str, Any]] = []
    n_ok = n_fail = 0
    if execute and not _is_offline_fmp():
        for i, item in enumerate(to_fetch):
            r = _fetch_and_merge(item["ticker"])
            results.append(r)
            n_ok += 1 if r["ok"] else 0
            n_fail += 0 if r["ok"] else 1
            if (i + 1) % 100 == 0:
                logger.info("refreshed %d/%d (ok=%d fail=%d)", i + 1, len(to_fetch), n_ok, n_fail)
    elif execute:
        logger.warning("FMP offline — cannot execute; falling back to dry-run report")
        execute = False

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "execute" if execute else "dry_run",
        "params": {"stale_days": stale_days, "max_calls": max_calls, "fmp_bars_days": FMP_BARS_DAYS},
        "universe_size": len(universe),
        "already_fresh": len(fresh),
        "stale_or_missing": len(stale),
        "missing_parquet": len(missing),
        "planned_calls": len(to_fetch),
        "truncated_by_budget": truncated,
        "refreshed_ok": n_ok,
        "refresh_failed": n_fail,
        "failed": [r for r in results if not r["ok"]][:50],
        "research_only": True,
        "no_trade_recommendation": True,
    }
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, indent=2))

    lines = [
        "UNIVERSE PRICE REFRESH (shallow cache/prices/)",
        f"generated_at: {payload['generated_at']}   mode: {payload['mode']}",
        f"universe={len(universe)}  fresh={len(fresh)}  stale/missing={len(stale)} (missing parquet: {len(missing)})",
        f"planned_calls={len(to_fetch)}  truncated_by_budget={truncated}",
        f"refreshed_ok={n_ok}  refresh_failed={n_fail}",
    ]
    if payload["failed"]:
        lines.append("failures (first 10): " + ", ".join(f"{r['ticker']}({r.get('reason','?')})" for r in payload["failed"][:10]))
    OUT_TXT.parent.mkdir(parents=True, exist_ok=True)
    OUT_TXT.write_text("\n".join(lines) + "\n")
    for ln in lines:
        logger.info(ln)
    return payload


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Refresh shallow price cache for the scan universe (FMP)")
    ap.add_argument("--stale-days", type=int, default=DEFAULT_STALE_DAYS,
                    help=f"Parquet last bar within N calendar days = fresh (default {DEFAULT_STALE_DAYS})")
    ap.add_argument("--max-calls", type=int, default=DEFAULT_MAX_CALLS,
                    help=f"Provider-call ceiling per run (default {DEFAULT_MAX_CALLS})")
    ap.add_argument("--limit", type=int, default=None,
                    help="Only consider the first N universe tickers (testing)")
    ap.add_argument("--execute", action="store_true", default=False,
                    help="Actually fetch from FMP. Without this flag the run is a dry-run report.")
    args = ap.parse_args(argv)
    payload = run(stale_days=args.stale_days, max_calls=args.max_calls,
                  execute=args.execute, limit=args.limit)
    return 0 if payload["refresh_failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

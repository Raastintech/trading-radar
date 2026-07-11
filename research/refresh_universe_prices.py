#!/usr/bin/env python3
"""
research/refresh_universe_prices.py — same-session price refresh for the scan universe.

Problem history:
  * 2026-07-02 staleness audit: nothing refreshed cache/prices/*.parquet after
    the trading daemon was decommissioned — 95% of parquets froze while SPY
    stayed current, so scanner RS silently compared weeks-old ticker prices
    against a fresh benchmark.
  * 2026-07-10 pipeline review (P0): the original fix used a calendar-day
    stale threshold (--stale-days 2), which still accepted bars one completed
    session behind the benchmark on alternating premarket runs.

This version enforces SAME-SESSION freshness:

  1. Derives the required completed US trading session from the project's
     exchange calendar (core/market_session.py — holiday + early-close aware).
  2. Benchmarks SPY / QQQ / IWM are always in the universe and refreshed FIRST.
  3. Every eligible ticker whose parquet last bar is earlier than the required
     session is refreshed (one FMP get_ticker_bars call each, merge-on-write).
  4. After the refresh, every attempted ticker is RE-CHECKED; tickers still
     behind the required session are reported as SESSION_MISMATCH so the
     scanner can exclude them instead of comparing them against a fresher
     benchmark.
  5. Confirmed-dead symbols (research/dead_symbol_tombstones.py, repeated
     hard failures on distinct days) are excluded from planning; refresh
     outcomes feed the tombstone ledger (success revives, no-data failures
     accumulate evidence — transport errors never count toward death).
  6. Budget is explicit: the run stops at --max-calls; a truncated run is
     flagged coverage_shortfall=true and the artifact records the FMP budget
     configuration (FMP_MONTHLY_BUDGET=0 means the premium plan's cap is
     UNCONFIRMED — this is surfaced, never silently treated as unlimited).

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
from datetime import date, datetime, timezone
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
from core.market_session import required_market_session
from research.dead_symbol_tombstones import (
    HARD_FAILURE_STATUSES, confirmed_dead, record_failure, record_success,
    summary as tombstone_summary,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s %(message)s")
logger = logging.getLogger("refresh_universe_prices")

PRICE_DIR = cfg.CACHE_DIR / "prices"
RESEARCH_DIR = cfg.CACHE_DIR / "research"
UNIVERSE_BUILD_JSON = RESEARCH_DIR / "research_universe_build_latest.json"
OUT_JSON = RESEARCH_DIR / "universe_price_refresh_latest.json"
OUT_TXT = cfg.LOG_DIR / "universe_price_refresh_latest.txt"

BENCHMARKS = ("SPY", "QQQ", "IWM")
# Sector benchmarks used by scan_sector_leaders — must be on the same session
# as SPY or the sector-RS ranking silently loses misaligned sectors.
SECTOR_ETFS = ("XLB", "XLC", "XLE", "XLF", "XLI", "XLK",
               "XLP", "XLRE", "XLU", "XLV", "XLY")
ALWAYS_INCLUDE = BENCHMARKS + SECTOR_ETFS
DEFAULT_MAX_CALLS = 1100     # provider-call ceiling per run
FMP_BARS_DAYS = 320          # ~330 trading bars — covers MA200 / 252d lookbacks


def _is_offline_fmp() -> bool:
    return os.getenv("FMP_API_KEY", "").strip().lower() in {"", "offline", "stub"}


def _load_universe() -> List[str]:
    """Scan universe from the last universe build; benchmarks + sector ETFs
    always included (and refreshed first)."""
    tickers: List[str] = list(ALWAYS_INCLUDE)
    try:
        data = json.loads(UNIVERSE_BUILD_JSON.read_text())
        for entry in data.get("universe_full") or []:
            t = str(entry.get("ticker") or "").upper()
            if t and t not in tickers:
                tickers.append(t)
    except Exception as exc:
        logger.warning("universe build sidecar unreadable (%s) — benchmarks only", exc)
    return tickers


def _parquet_last_date(ticker: str) -> Optional[date]:
    path = PRICE_DIR / f"{ticker.upper()}.parquet"
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path)
        if df.empty:
            return None
        return pd.to_datetime(df.index).max().date()
    except Exception:
        return None


def _fetch_and_merge(ticker: str) -> Dict[str, Any]:
    """One FMP call → merge-write shallow parquet. Returns result entry with a
    stable provider_status slug (ok / fmp_empty / no_date_column / transport_error)."""
    from core.data_gatekeeper import get_gatekeeper
    from core.fmp_client import get_fmp

    entry: Dict[str, Any] = {"ticker": ticker, "ok": False, "provider_status": "transport_error"}
    try:
        rows = get_fmp().get_ticker_bars(ticker, days=FMP_BARS_DAYS)
        if not rows:
            entry["provider_status"] = "fmp_empty"
            entry["reason"] = "fmp_empty"
            return entry
        df = pd.DataFrame(rows)
        if "date" not in df.columns:
            entry["provider_status"] = "no_date_column"
            entry["reason"] = "no_date_column"
            return entry
        df["date"] = pd.to_datetime(df["date"])
        df.set_index("date", inplace=True)
        df.sort_index(inplace=True)
        cols = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
        get_gatekeeper().put_prices(ticker, df[cols])
        entry["ok"] = True
        entry["provider_status"] = "ok"
        entry["bars_fetched"] = len(df)
        entry["last_bar"] = str(df.index.max().date())
    except Exception as exc:
        entry["reason"] = f"error: {exc}"
    return entry


def run(max_calls: int, execute: bool, limit: Optional[int] = None,
        session_override: Optional[str] = None) -> Dict[str, Any]:
    required = (datetime.strptime(session_override, "%Y-%m-%d").date()
                if session_override else required_market_session())
    universe = _load_universe()
    if limit:
        universe = [t for t in ALWAYS_INCLUDE] + [
            t for t in universe if t not in ALWAYS_INCLUDE][: max(0, limit - len(ALWAYS_INCLUDE))]

    dead = confirmed_dead()
    excluded_dead = sorted(t for t in universe if t in dead and t not in ALWAYS_INCLUDE)
    eligible = [t for t in universe if t not in dead or t in ALWAYS_INCLUDE]

    fresh: List[str] = []
    missing: List[str] = []
    stale: List[Dict[str, Any]] = []
    for t in eligible:
        last = _parquet_last_date(t)
        if last is None:
            missing.append(t)
            stale.append({"ticker": t, "last_bar": None})
        elif last >= required:
            fresh.append(t)
        else:
            stale.append({"ticker": t, "last_bar": str(last)})

    # Benchmarks + sector ETFs refresh first — a stale benchmark poisons
    # every RS figure downstream.
    stale.sort(key=lambda r: (r["ticker"] not in ALWAYS_INCLUDE, r["ticker"]))

    # Shared budget policy: a confirmed monthly cap truncates NON-benchmark
    # work explicitly (benchmarks ride the reserve); an unconfirmed cap is
    # surfaced as a warning, never silently treated as unlimited.
    from research.fmp_budget import budget_report
    budget = budget_report(planned_calls=len(stale), pipeline="refresh_universe_prices")
    allowed = min(len(stale), max_calls,
                  budget["allowed_calls"] if budget["budget_limited"] else len(stale))
    to_fetch = stale[:allowed]
    truncated = len(stale) - len(to_fetch)

    results: List[Dict[str, Any]] = []
    n_ok = n_fail = 0
    if execute and not _is_offline_fmp():
        for i, item in enumerate(to_fetch):
            r = _fetch_and_merge(item["ticker"])
            results.append(r)
            if r["ok"]:
                n_ok += 1
                record_success(r["ticker"])
            else:
                n_fail += 1
                status = r.get("provider_status", "transport_error")
                record_failure(r["ticker"], provider_status=status,
                               reason=str(r.get("reason", "")))
            if (i + 1) % 100 == 0:
                logger.info("refreshed %d/%d (ok=%d fail=%d)", i + 1, len(to_fetch), n_ok, n_fail)
    elif execute:
        logger.warning("FMP offline — cannot execute; falling back to dry-run report")
        execute = False

    # ── Post-refresh RECHECK: who is still behind the required session? ──────
    session_mismatch: List[Dict[str, Any]] = []
    attempted = {r["ticker"] for r in results}
    succeeded_syms = {r["ticker"] for r in results if r["ok"]}
    for item in stale:
        t = item["ticker"]
        last = _parquet_last_date(t) if t in attempted else (
            datetime.strptime(item["last_bar"], "%Y-%m-%d").date()
            if item["last_bar"] else None)
        if last is None or last < required:
            session_mismatch.append({"ticker": t, "last_bar": str(last) if last else None,
                                     "attempted": t in attempted})
            # Delisted pattern: the provider answered (call succeeded) but
            # the latest bar stays behind the session.  Hard tombstone
            # evidence — CONFIRMED_DEAD after distinct-day repeats, never
            # from one occurrence.
            if t in succeeded_syms:
                record_failure(t, provider_status="no_bar_for_session",
                               reason=f"last_bar {last} < required {required}")

    benchmark_session = {b: (str(_parquet_last_date(b)) if _parquet_last_date(b) else None)
                         for b in BENCHMARKS}
    benchmarks_aligned = all(v == str(required) for v in benchmark_session.values())

    same_session_ready = len(fresh) + n_ok - sum(
        1 for m in session_mismatch if m["attempted"])

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "execute" if execute else "dry_run",
        "params": {"max_calls": max_calls, "fmp_bars_days": FMP_BARS_DAYS,
                   "freshness_rule": "same_completed_session (core/market_session.py)"},
        # ── P0 spec metrics ──────────────────────────────────────────────────
        "required_market_session": str(required),
        "same_session_ready": same_session_ready,
        "refresh_attempted": len(results),
        "refresh_succeeded": n_ok,
        "session_mismatch_excluded": len(session_mismatch),
        "benchmark_session": benchmark_session,
        "benchmarks_aligned": benchmarks_aligned,
        # ── coverage / budget honesty ────────────────────────────────────────
        "universe_size": len(universe),
        "eligible_size": len(eligible),
        "already_fresh": len(fresh),
        "stale_or_missing": len(stale),
        "missing_parquet": len(missing),
        "planned_calls": len(to_fetch),
        "truncated_by_budget": truncated,
        "coverage_shortfall": truncated > 0,
        "refresh_failed": n_fail,
        "session_mismatch_sample": session_mismatch[:50],
        "excluded_dead_symbols": excluded_dead,
        "excluded_dead_count": len(excluded_dead),
        "tombstones": tombstone_summary(),
        "fmp_budget": budget,
        "failed": [r for r in results if not r["ok"]][:50],
        "research_only": True,
        "no_trade_recommendation": True,
    }
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, indent=2))

    lines = [
        "UNIVERSE PRICE REFRESH (same-session rule, shallow cache/prices/)",
        f"generated_at: {payload['generated_at']}   mode: {payload['mode']}",
        f"required_market_session: {required}   benchmarks_aligned: {benchmarks_aligned} {benchmark_session}",
        f"universe={len(universe)} eligible={len(eligible)} fresh={len(fresh)} stale/missing={len(stale)} (missing parquet: {len(missing)})",
        f"planned_calls={len(to_fetch)}  truncated_by_budget={truncated}  coverage_shortfall={payload['coverage_shortfall']}",
        f"refresh_attempted={len(results)}  refresh_succeeded={n_ok}  refresh_failed={n_fail}",
        f"session_mismatch_excluded={len(session_mismatch)}  dead_excluded={len(excluded_dead)}",
        f"fmp_budget: {payload['fmp_budget']['budget_cap_status']} (month={payload['fmp_budget']['calls_used_month']})",
    ]
    if payload["failed"]:
        lines.append("failures (first 10): " + ", ".join(
            f"{r['ticker']}({r.get('reason','?')})" for r in payload["failed"][:10]))
    if not benchmarks_aligned and execute:
        lines.append("WARNING: benchmark session mismatch — downstream scan must be BLOCKED")
    OUT_TXT.parent.mkdir(parents=True, exist_ok=True)
    OUT_TXT.write_text("\n".join(lines) + "\n")
    for ln in lines:
        logger.info(ln)
    return payload


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Same-session refresh of the shallow price cache for the scan universe (FMP)")
    ap.add_argument("--max-calls", type=int, default=DEFAULT_MAX_CALLS,
                    help=f"Provider-call ceiling per run (default {DEFAULT_MAX_CALLS})")
    ap.add_argument("--limit", type=int, default=None,
                    help="Only consider the first N universe tickers (testing)")
    ap.add_argument("--session", type=str, default=None,
                    help="Override the required market session (YYYY-MM-DD, testing)")
    ap.add_argument("--execute", action="store_true", default=False,
                    help="Actually fetch from FMP. Without this flag the run is a dry-run report.")
    args = ap.parse_args(argv)
    payload = run(max_calls=args.max_calls, execute=args.execute,
                  limit=args.limit, session_override=args.session)
    return 0 if payload["refresh_failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

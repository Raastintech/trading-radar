#!/usr/bin/env python3
"""
research/universe_discovery_bootstrap.py — open candidate discovery safely (P1).

Problem (2026-07-10 pipeline review): the scanner's ranked-fill universe is
dynamically ranked but CLOSED — candidates must already have a parquet in
cache/prices/, and nothing has created new parquets since the trading daemon
was decommissioned (2026-06-13). New or newly-eligible stocks can only enter
via Alpha Board / Social Arb seeds.

Canonical discovery-coverage process:

    market security master (FMP company screener, cached 24h — 1 call/day)
      → eligibility filter   (symbol validity: no warrants/units/rights/
                              foreign-dot junk; exchange NYSE/NASDAQ/AMEX;
                              common stock, actively trading — screener-side)
      → liquidity prefilter  (price > $5, volume > 300k — mirrors
                              core/universe.py _MIN_* floors; screener-side)
      → existing-history check (cache/prices/{SYM}.parquet with usable bars)
      → bootstrap missing eligible names (budgeted, resumable queue)
      → dynamic ranking pool (the scanner's ranked fill picks up new parquets
                              automatically on its next run)

Honesty rules:
  * bootstraps ~320 calendar days of bars; symbols with shorter listed
    history keep whatever exists — MA200/long-horizon factors stay None
    downstream (never fabricated; the scanner's enrichment already labels
    insufficient history).
  * confirmed-dead symbols (research/dead_symbol_tombstones.py) are excluded.
  * a truncated run is resumable: the queue persists in
    data/research/discovery_bootstrap_queue.json and continues next run.
  * daily budget capped (--max-calls, default 150) so discovery can never
    starve the main refresh; it also never blocks the scan (separate step).

Outputs:
  cache/research/discovery_coverage_latest.json   (coverage report, P1 spec)
  logs/discovery_coverage_latest.txt
  data/research/discovery_bootstrap_queue.json    (resumable queue state)

RESEARCH-ONLY / DATA-COLLECTION-ONLY: no signals, no scoring change, no
threshold change — this only widens which symbols HAVE data to be ranked.

Usage:
  # Dry-run (default): report coverage + what would be bootstrapped
  SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python research/universe_discovery_bootstrap.py

  # Execute (provider calls: 1 screener + up to --max-calls bar fetches)
  SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python research/universe_discovery_bootstrap.py --execute
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

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
from research.dead_symbol_tombstones import confirmed_dead

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s %(message)s")
logger = logging.getLogger("universe_discovery_bootstrap")

PRICE_DIR = cfg.CACHE_DIR / "prices"
RESEARCH_DIR = cfg.CACHE_DIR / "research"
OUT_JSON = RESEARCH_DIR / "discovery_coverage_latest.json"
OUT_TXT = cfg.LOG_DIR / "discovery_coverage_latest.txt"
QUEUE_JSON = ROOT / "data" / "research" / "discovery_bootstrap_queue.json"

DEFAULT_MAX_CALLS = 150      # daily bootstrap budget (bar fetches)
FMP_BARS_DAYS = 320          # matches refresh_universe_prices — MA200-capable
MIN_USABLE_BARS = 20         # parquet with fewer bars = still "missing history"

# Same symbol-validity policy as the scanner (warrants/units/rights/foreign
# junk are never admitted).  Mirrored regexes — research_scanner is the
# canonical source; test_discovery_bootstrap pins them equal.
_DOTTED_RE = re.compile(r"\.")
_INVALID_SUFFIX_RE = re.compile(r"^[A-Z]{1,5}[WU]$|^[A-Z]+[-.](WS|WT|U|UN|R|RT)$")


def _is_valid_equity_symbol(sym: str) -> bool:
    if not sym or len(sym) > 6 or len(sym) < 1:
        return False
    if _DOTTED_RE.search(sym):
        return False
    if _INVALID_SUFFIX_RE.match(sym):
        return False
    return True


def _is_offline_fmp() -> bool:
    return os.getenv("FMP_API_KEY", "").strip().lower() in {"", "offline", "stub"}


def _usable_history(sym: str) -> bool:
    path = PRICE_DIR / f"{sym}.parquet"
    if not path.exists():
        return False
    try:
        idx = pd.read_parquet(path, columns=[]).index
        return len(idx) >= MIN_USABLE_BARS
    except Exception:
        return False


def _load_queue() -> Dict[str, Any]:
    if QUEUE_JSON.exists():
        try:
            return json.loads(QUEUE_JSON.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("bootstrap queue unreadable — starting fresh")
    return {"pending": [], "completed": {}, "failed": {}}


def _save_queue(queue: Dict[str, Any]) -> None:
    QUEUE_JSON.parent.mkdir(parents=True, exist_ok=True)
    QUEUE_JSON.write_text(json.dumps(queue, indent=2), encoding="utf-8")


def _fetch_bootstrap(sym: str) -> Dict[str, Any]:
    """One FMP bar fetch → merge-write parquet (same path the refresher uses)."""
    from research.refresh_universe_prices import _fetch_and_merge
    return _fetch_and_merge(sym)


def run(max_calls: int, execute: bool,
        screener_rows: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Build the coverage report and (optionally) run the budgeted bootstrap.

    ``screener_rows`` injects a synthetic security master for tests."""
    exclusions: Dict[str, int] = {}

    # ── 1. Security master ───────────────────────────────────────────────────
    if screener_rows is None:
        if _is_offline_fmp():
            logger.warning("FMP offline — coverage report only, empty security master")
            screener_rows = []
        else:
            from core.fmp_client import get_fmp
            screener_rows = get_fmp().get_company_screener()

    # ── 2/3. Eligibility + liquidity (screener params already enforce
    #         exchange/type/liquidity; symbol validity is enforced here) ─────
    dead = confirmed_dead()
    eligible: List[str] = []
    seen: Set[str] = set()
    for row in screener_rows:
        sym = str(row.get("symbol") or "").upper().strip()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        if not _is_valid_equity_symbol(sym):
            exclusions["invalid_symbol"] = exclusions.get("invalid_symbol", 0) + 1
            continue
        if sym in dead:
            exclusions["confirmed_dead"] = exclusions.get("confirmed_dead", 0) + 1
            continue
        eligible.append(sym)

    # ── 4. Existing-history check ────────────────────────────────────────────
    have_history = [s for s in eligible if _usable_history(s)]
    missing = [s for s in eligible if not _usable_history(s)]

    # ── 5. Resumable bootstrap queue ─────────────────────────────────────────
    queue = _load_queue()
    known = set(queue["pending"]) | set(queue["completed"]) | set(queue["failed"])
    for s in missing:
        if s not in known:
            queue["pending"].append(s)
    # Retry policy: a failed name re-enters the queue on later days (one
    # attempt per day) until either it succeeds or the tombstone ledger
    # confirms it dead (hard failures on distinct days) — so a single
    # provider failure never permanently drops a valid stock, and known-dead
    # names stop consuming refresh work.
    today = datetime.now(timezone.utc).date().isoformat()
    for s, entry in list(queue["failed"].items()):
        if (s in eligible and s not in dead and not _usable_history(s)
                and str(entry.get("at", ""))[:10] < today
                and s not in queue["pending"]):
            queue["pending"].append(s)
            del queue["failed"][s]
    # Drop queue entries that gained history through other paths, went dead,
    # or fell out of eligibility.
    eligible_set = set(eligible)
    queue["pending"] = [s for s in queue["pending"]
                        if s in eligible_set and not _usable_history(s) and s not in dead]

    executing = execute and not _is_offline_fmp()
    to_fetch = queue["pending"][:max_calls] if executing else []
    attempted = 0
    succeeded = 0
    newly_admitted: List[str] = []
    for sym in to_fetch:
        r = _fetch_bootstrap(sym)
        attempted += 1
        if r.get("ok") and _usable_history(sym):
            succeeded += 1
            newly_admitted.append(sym)
            queue["completed"][sym] = {"at": datetime.now(timezone.utc).isoformat(),
                                       "bars": r.get("bars_fetched")}
        else:
            reason = r.get("provider_status") or r.get("reason") or "unknown"
            entry = queue["failed"].get(sym) or {"count": 0}
            entry.update({"count": entry.get("count", 0) + 1,
                          "last_reason": str(reason),
                          "at": datetime.now(timezone.utc).isoformat()})
            queue["failed"][sym] = entry
            # No-data failures feed the tombstone ledger (repeated evidence
            # tombstones; one transient failure never does).
            try:
                from research.dead_symbol_tombstones import record_failure
                record_failure(sym, provider_status=str(r.get("provider_status") or "transport_error"),
                               reason=str(r.get("reason", "")))
            except Exception:
                pass
    # pending = previous pending minus anything now completed or failed
    done_now = set(queue["completed"]) | set(queue["failed"])
    queue["pending"] = [s for s in queue["pending"] if s not in done_now]
    _save_queue(queue)

    coverage_pct = (round(100.0 * (len(have_history) + len(newly_admitted)) / len(eligible), 1)
                    if eligible else 0.0)

    from research.fmp_budget import budget_report
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "execute" if executing else "dry_run",
        "params": {"max_calls": max_calls, "fmp_bars_days": FMP_BARS_DAYS,
                   "min_usable_bars": MIN_USABLE_BARS},
        "fmp_budget": budget_report(planned_calls=len(to_fetch),
                                    pipeline="universe_discovery_bootstrap"),
        # ── P1 spec metrics ─────────────────────────────────────────────────
        "eligible_security_master_count": len(eligible),
        "usable_price_history_count": len(have_history) + len(newly_admitted),
        "missing_history_count": max(0, len(missing) - len(newly_admitted)),
        "bootstrap_attempted": attempted,
        "bootstrap_succeeded": succeeded,
        "newly_admitted_today": newly_admitted,
        "coverage_pct": coverage_pct,
        "exclusions_by_reason": exclusions,
        # ── queue state ─────────────────────────────────────────────────────
        "queue_pending": len(queue["pending"]),
        "queue_completed": len(queue["completed"]),
        "queue_failed": len(queue["failed"]),
        "security_master_rows": len(screener_rows),
        "research_only": True,
        "no_trade_recommendation": True,
    }
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, indent=2))

    lines = [
        "DISCOVERY COVERAGE (security master → bootstrap queue)",
        f"generated_at: {payload['generated_at']}   mode: {payload['mode']}",
        f"security_master={len(screener_rows)}  eligible={len(eligible)}  "
        f"with_history={payload['usable_price_history_count']}  missing={payload['missing_history_count']}",
        f"coverage={coverage_pct}%  exclusions={exclusions}",
        f"bootstrap: attempted={attempted} succeeded={succeeded} newly_admitted={newly_admitted[:15]}",
        f"queue: pending={payload['queue_pending']} completed={payload['queue_completed']} failed={payload['queue_failed']}",
    ]
    OUT_TXT.parent.mkdir(parents=True, exist_ok=True)
    OUT_TXT.write_text("\n".join(lines) + "\n")
    for ln in lines:
        logger.info(ln)
    return payload


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Discovery coverage + budgeted bootstrap of missing eligible symbols (FMP)")
    ap.add_argument("--max-calls", type=int, default=DEFAULT_MAX_CALLS,
                    help=f"Daily bootstrap bar-fetch budget (default {DEFAULT_MAX_CALLS})")
    ap.add_argument("--execute", action="store_true", default=False,
                    help="Fetch from FMP. Without this flag the run is a coverage report only.")
    args = ap.parse_args(argv)
    run(max_calls=args.max_calls, execute=args.execute)
    return 0


if __name__ == "__main__":
    sys.exit(main())

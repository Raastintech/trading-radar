#!/usr/bin/env python3
"""
research/scan_universe_manifest.py — canonical validated scan-universe manifest.

Closes the final-universe freshness gap (2026-07-11 follow-up): the bulk
refresh runs against *yesterday's* universe, so names that newly rank into
today's universe (or were just bootstrapped) could enter scoring without a
bar for the required session — 24-29 SESSION_MISMATCH exclusions per cycle
that never self-heal within the cycle.

Single-manifest flow (runs AFTER refresh-universe-prices and
discovery-bootstrap, BEFORE the scanner):

  1. Determine the required completed market session (exchange calendar).
  2. Build the provisional scanner universe with the scanner's OWN builder —
     unchanged floors, source priorities, rank formula, 1,000-name cap
     (research_scanner._build_universe; confirmed-dead symbols are already
     removed there under the tombstone policy).
  3. Add required benchmarks (SPY/QQQ/IWM) + the 11 sector ETFs.
  4. Delta-refresh every manifest symbol missing the required session —
     bounded by --max-calls and the shared FMP budget policy (benchmarks
     first; a confirmed cap truncates NON-benchmark work explicitly).
  5. Revalidate once (no rebuild/refresh loop).
  6. Exclude unresolved names with explicit reasons
     (refresh_failed / no_bar_for_session / budget_truncated).
  7. Write the manifest with a sha256 hash of the final validated universe;
     the scanner consumes exactly this universe and stamps the hash into
     its artifact, proving which universe + session produced each scan.

Readiness comes from research/scan_readiness.py (deterministic, configured
thresholds).  RESEARCH-ONLY / DATA-COLLECTION-ONLY: no scoring, threshold,
routing, or label change — this only guarantees the data under them.

Outputs:
  cache/research/scan_universe_manifest_latest.json
  cache/research/research_universe_build_latest.json  (provisional build log,
      written via the scanner's own writer so downstream readers continue
      to work unchanged)
  logs/scan_universe_manifest_latest.txt

Usage:
  # Dry-run: build + validate, report the delta, no provider calls
  SNIPER_ENV_PATH=... .venv/bin/python research/scan_universe_manifest.py

  # Execute the bounded delta refresh
  SNIPER_ENV_PATH=... .venv/bin/python research/scan_universe_manifest.py --execute
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timezone
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

import core.config as cfg
from core.market_session import required_market_session
from research.fmp_budget import budget_report
from research.scan_readiness import (
    REQUIRED_BENCHMARKS, readiness_verdict,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s %(message)s")
logger = logging.getLogger("scan_universe_manifest")

RESEARCH_DIR = cfg.CACHE_DIR / "research"
OUT_JSON = RESEARCH_DIR / "scan_universe_manifest_latest.json"
OUT_TXT = cfg.LOG_DIR / "scan_universe_manifest_latest.txt"

SECTOR_ETFS = ("XLB", "XLC", "XLE", "XLF", "XLI", "XLK",
               "XLP", "XLRE", "XLU", "XLV", "XLY")
DEFAULT_MAX_CALLS = 400      # delta ceiling; steady state is a few dozen


def _is_offline_fmp() -> bool:
    return os.getenv("FMP_API_KEY", "").strip().lower() in {"", "offline", "stub"}


def universe_hash(final_universe: List[str], required_session: str) -> str:
    blob = ("|".join(sorted(final_universe)) + "@" + required_session).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def run(max_calls: int, execute: bool, cap: int = 1000,
        session_override: Optional[str] = None) -> Dict[str, Any]:
    from research.refresh_universe_prices import _fetch_and_merge, _parquet_last_date
    from research.dead_symbol_tombstones import record_failure, record_success
    import research.research_scanner as rs

    required = (session_override
                or str(required_market_session()))

    # ── provisional universe: the scanner's own builder, unchanged ──────────
    provisional, build_info = rs._build_universe(cap=cap)
    rs._write_universe_build_log(build_info)

    bootstrapped: List[str] = []
    try:
        cov = json.loads((RESEARCH_DIR / "discovery_coverage_latest.json").read_text())
        bootstrapped = list(cov.get("newly_admitted_today") or [])
    except Exception:
        pass

    manifest_symbols: List[str] = list(dict.fromkeys(
        list(REQUIRED_BENCHMARKS) + list(SECTOR_ETFS) + provisional))

    # ── delta detection: exact manifest vs required session ─────────────────
    delta = [s for s in manifest_symbols
             if (lambda d: d is None or str(d) < required)(_parquet_last_date(s))]
    # benchmarks + sector ETFs first — they hold the budget reserve
    protected = set(REQUIRED_BENCHMARKS) | set(SECTOR_ETFS)
    delta.sort(key=lambda s: (s not in protected, s))

    budget = budget_report(planned_calls=len(delta), pipeline="scan_universe_manifest")
    allowed = min(len(delta), max_calls, budget["allowed_calls"]
                  if budget["budget_limited"] else len(delta))
    to_fetch = delta[:allowed]
    budget_truncated = [s for s in delta[allowed:]]

    attempted = succeeded = failed = 0
    refresh_failed: List[str] = []
    if execute and not _is_offline_fmp():
        for sym in to_fetch:
            r = _fetch_and_merge(sym)
            attempted += 1
            if r.get("ok"):
                succeeded += 1
                record_success(sym)
            else:
                failed += 1
                refresh_failed.append(sym)
                record_failure(sym, provider_status=str(
                    r.get("provider_status") or "transport_error"),
                    reason=str(r.get("reason", "")))
    elif execute:
        logger.warning("FMP offline — dry-run report only")
        execute = False

    # ── revalidate ONCE ──────────────────────────────────────────────────────
    excluded_by_reason: Dict[str, List[str]] = {
        "refresh_failed": [], "no_bar_for_session": [], "budget_truncated": [],
    }
    attempted_set = set(to_fetch[:attempted]) if attempted else set()
    benchmark_session: Dict[str, Optional[str]] = {}
    aligned_universe: List[str] = []
    for sym in manifest_symbols:
        last = _parquet_last_date(sym)
        ok = last is not None and str(last) >= required
        if sym in REQUIRED_BENCHMARKS:
            benchmark_session[sym] = str(last) if last else None
        if sym in protected:
            continue  # benchmarks/sectors are not scan candidates
        if ok:
            aligned_universe.append(sym)
        elif sym in refresh_failed:
            excluded_by_reason["refresh_failed"].append(sym)
        elif sym in budget_truncated:
            excluded_by_reason["budget_truncated"].append(sym)
        else:
            excluded_by_reason["no_bar_for_session"].append(sym)
            # Hard tombstone evidence, but only when we actually asked the
            # provider this run: a successful fetch that still leaves the
            # bar behind the session is the delisted pattern.  Three
            # distinct days of this and the symbol is CONFIRMED_DEAD and
            # removed before future manifests (operator reinstate covers
            # halts that resume).
            if execute and sym in attempted_set:
                record_failure(sym, provider_status="no_bar_for_session",
                               reason=f"last_bar {last} < required {required}")

    benchmarks_aligned = all(v == required for v in benchmark_session.values())
    sector_session_ok = [s for s in SECTOR_ETFS
                         if str(_parquet_last_date(s) or "") >= required]
    excluded_count = sum(len(v) for v in excluded_by_reason.values())
    readiness, reasons = readiness_verdict(
        universe_size=len(provisional),
        excluded_count=excluded_count,
        benchmarks_aligned=benchmarks_aligned,
    )
    final_hash = universe_hash(aligned_universe, required)

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "execute" if execute else "dry_run",
        "required_market_session": required,
        "universe_size": len(provisional),
        "source_counts": build_info.get("source_counts"),
        "bootstrapped_symbols": bootstrapped,
        "benchmark_symbols": list(REQUIRED_BENCHMARKS),
        "sector_etf_symbols": list(SECTOR_ETFS),
        "benchmark_session": benchmark_session,
        "benchmarks_aligned": benchmarks_aligned,
        "sector_etfs_aligned_count": len(sector_session_ok),
        "delta_refresh_symbols": delta,
        "delta_refresh_needed": len(delta),
        "delta_refresh_attempted": attempted,
        "delta_refresh_succeeded": succeeded,
        "delta_refresh_failed": failed,
        "aligned_final_universe_count": len(aligned_universe),
        "excluded_count": excluded_count,
        "excluded_by_reason": {k: sorted(v) for k, v in excluded_by_reason.items()},
        "final_universe": aligned_universe,
        "final_source_map": {t: (build_info.get("all_ticker_sources") or {}).get(t)
                             for t in aligned_universe},
        "scan_readiness": readiness,
        "readiness_reasons": reasons,
        "universe_hash": final_hash,
        "fmp_budget": budget,
        "params": {"max_calls": max_calls, "cap": cap},
        "research_only": True,
        "no_trade_recommendation": True,
    }
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(manifest, indent=2))

    lines = [
        "SCAN UNIVERSE MANIFEST (validated same-session universe)",
        f"generated_at: {manifest['generated_at']}   mode: {manifest['mode']}",
        f"required_market_session: {required}   readiness: {readiness}",
        f"provisional={len(provisional)}  delta_needed={len(delta)}  "
        f"attempted={attempted} ok={succeeded} fail={failed}",
        f"aligned_final={len(aligned_universe)}  excluded={excluded_count} "
        + " ".join(f"{k}={len(v)}" for k, v in excluded_by_reason.items() if v),
        f"benchmarks: {benchmark_session} aligned={benchmarks_aligned}  "
        f"sector_etfs_aligned={len(sector_session_ok)}/{len(SECTOR_ETFS)}",
        f"universe_hash: {final_hash}",
        f"fmp_budget: {budget['budget_cap_status']} month={budget['calls_used_month']} "
        f"limited={budget['budget_limited']}",
    ]
    OUT_TXT.parent.mkdir(parents=True, exist_ok=True)
    OUT_TXT.write_text("\n".join(lines) + "\n")
    for ln in lines:
        logger.info(ln)
    return manifest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Build + validate the canonical scan-universe manifest (bounded delta refresh)")
    ap.add_argument("--max-calls", type=int, default=DEFAULT_MAX_CALLS,
                    help=f"Delta-refresh provider-call ceiling (default {DEFAULT_MAX_CALLS})")
    ap.add_argument("--cap", type=int, default=1000,
                    help="Universe size cap (default 1000, unchanged)")
    ap.add_argument("--session", type=str, default=None,
                    help="Override the required market session (YYYY-MM-DD, testing)")
    ap.add_argument("--execute", action="store_true", default=False,
                    help="Run the delta refresh. Without this flag: validate + report only.")
    args = ap.parse_args(argv)
    manifest = run(max_calls=args.max_calls, execute=args.execute,
                   cap=args.cap, session_override=args.session)
    return 0 if manifest["scan_readiness"] != "BLOCKED" else 1


if __name__ == "__main__":
    sys.exit(main())

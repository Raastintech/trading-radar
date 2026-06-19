"""
research/targeted_price_backfill.py — Phase 4A.7: Targeted research price-cache backfill.

Identifies high-interest research tickers whose local price cache is too shallow
(DATA_QUARANTINE / INSUFFICIENT_HISTORY), then backfills ONLY those names using
FMP (preferred provider).  Writes merge-on-write to cache/prices_deep/ so the
daemon's 90-day overwrite cannot clobber the deep history.

Design constraints:
  - RESEARCH-ONLY: no trade signals, no paper trading, no execution.
  - Cache-first candidate collection — reads existing research sidecars only.
  - FMP is the preferred provider for historical bars.
  - Do NOT backfill the full 5 000+ ticker universe.
  - Default mode is DRY-RUN: safe to run any time; provider calls require
    --execute (or a non-zero --max-provider-calls when used from tests).

Outputs:
  cache/research/targeted_price_backfill_latest.json
  logs/targeted_price_backfill_latest.txt

Usage:
  # Dry-run (no provider calls):
  GEM_TRADER_SKIP_DOTENV=true .venv/bin/python research/targeted_price_backfill.py --dry-run

  # Actual backfill (requires SNIPER_ENV_PATH):
  SNIPER_ENV_PATH=/home/gem/secure/trading.env \\
    .venv/bin/python research/targeted_price_backfill.py --limit 25 --min-bars 300

  # Via run_research_cycle:
  ./scripts/run_research_cycle.sh targeted-backfill --dry-run --limit 25 --min-bars 300
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
from typing import Any, Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None  # type: ignore[assignment]

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
os.environ.setdefault("PAPER_TRADING", "true")
os.environ.setdefault("DB_PATH", str(ROOT / "db" / "trading.db"))
os.environ.setdefault("CACHE_DIR", str(ROOT / "cache"))
os.environ.setdefault("LOG_DIR", str(ROOT / "logs"))

import core.config as cfg  # noqa: E402

VERSION = "TARGETED_PRICE_BACKFILL_V1"
RESEARCH_DIR = cfg.CACHE_DIR / "research"
LOG_DIR = cfg.LOG_DIR
DEEP_DIR = cfg.CACHE_DIR / "prices_deep"
SHALLOW_DIR = cfg.CACHE_DIR / "prices"
OUT_JSON = RESEARCH_DIR / "targeted_price_backfill_latest.json"
OUT_TXT = LOG_DIR / "targeted_price_backfill_latest.txt"

DEFAULT_MIN_BARS = 300
DEFAULT_MAX_PROVIDER_CALLS = 25
DEFAULT_LIMIT = 50
RECENT_REFRESH_DAYS = 7       # skip re-fetch if deep parquet is this fresh
FMP_DAYS_TO_REQUEST = 450     # request more than min_bars to ensure coverage

BENCHMARKS: Set[str] = {"SPY", "QQQ"}

# Matches a "." anywhere (foreign tickers, e.g. 0A1K.L)
_FOREIGN_RE = re.compile(r"\.")
# Full-string match for SPAC warrant/unit/rights patterns (4-6 base + suffix).
# US equity tickers are ≤5 chars; SPAC warrants are the common 5-6 char + W/U/R form.
_WARRANT_RE = re.compile(r"^[A-Z]{4,6}(?:WS|W|U)$")  # warrants + units

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("targeted_price_backfill")


# ── Sidecar loaders ───────────────────────────────────────────────────────────

def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


# ── Symbol filter ─────────────────────────────────────────────────────────────

def _is_invalid(ticker: str) -> bool:
    if not ticker or not isinstance(ticker, str):
        return True
    t = ticker.strip().upper()
    if not t or t in BENCHMARKS:
        return True
    if _FOREIGN_RE.search(t):
        return True
    if _WARRANT_RE.match(t):
        return True
    return False


# ── Candidate collection ──────────────────────────────────────────────────────

def _collect_candidates(
    priority_only: bool = False,
    include_quarantine: bool = True,
) -> List[Tuple[str, int, str]]:
    """
    Return ordered list of (ticker, priority_rank, source_label).
    Lower rank = higher priority.  Deduplication is handled by the caller.
    """
    entries: List[Tuple[str, int, str]] = []

    # ── Source 1: Daily Alpha Radar ───────────────────────────────────────────
    radar = _load_json(RESEARCH_DIR / "daily_alpha_radar_latest.json")
    if radar:
        pt = radar.get("priority_tickers") or {}
        for t in pt.get("HIGH_PRIORITY_RESEARCH", []):
            entries.append((t, 10, "radar:HIGH_PRIORITY_RESEARCH"))
        for t in pt.get("RESET_WATCH", []):
            entries.append((t, 20, "radar:RESET_WATCH"))
        for t in pt.get("RECLAIM_WATCH", []):
            entries.append((t, 30, "radar:RECLAIM_WATCH"))
        if include_quarantine:
            for t in pt.get("DATA_QUARANTINE", []):
                entries.append((t, 40, "radar:DATA_QUARANTINE"))

    if priority_only:
        return entries

    # ── Source 2: Research Scanner ────────────────────────────────────────────
    scanner = _load_json(RESEARCH_DIR / "research_scanner_latest.json")
    if scanner:
        cats = scanner.get("categories") or {}
        for cat_name, items in cats.items():
            if not isinstance(items, list):
                continue
            for item in items:
                t = (item.get("ticker") or item.get("symbol") or "").upper()
                if t:
                    entries.append((t, 50, f"scanner:{cat_name}"))
        # Also pick up watchlist (56 tickers in sample)
        for t in scanner.get("watchlist") or []:
            if isinstance(t, str):
                entries.append((t.upper(), 55, "scanner:watchlist"))

    # ── Source 3: RS/Theme Lens Triage ───────────────────────────────────────
    triage = _load_json(RESEARCH_DIR / "rs_theme_lens_triage_latest.json")
    if triage:
        for c in (triage.get("candidates") or []):
            t = (c.get("ticker") or "").upper()
            if not t:
                continue
            label = c.get("triage_label") or c.get("stage_label") or ""
            # Prefer actionable labels first
            if label in ("BREAKOUT_CONFIRMED", "EMERGING_MOMENTUM"):
                rank = 35
            elif label == "PULLBACK_RECLAIM":
                rank = 45
            else:
                rank = 60
            entries.append((t, rank, f"triage:{label}"))

    # ── Source 4: Recall-Repair Shadow Lane ───────────────────────────────────
    shadow = _load_json(RESEARCH_DIR / "recall_repair_shadow_lane_latest.json")
    if shadow:
        for c in (shadow.get("candidates") or []):
            t = (c.get("ticker") or "").upper()
            label = c.get("label") or ""
            if t:
                rank = 45 if label in ("SHADOW_THEME_LEADER", "SHADOW_RS_LEADER") else 65
                entries.append((t, rank, f"shadow:{label}"))

    # ── Source 5: Research Forward (optional enrichment) ──────────────────────
    forward = _load_json(RESEARCH_DIR / "research_forward_latest.json")
    if forward:
        for entry in (forward.get("entries") or []):
            t = (entry.get("ticker") or "").upper()
            if t:
                entries.append((t, 70, "forward_tracker"))

    return entries


def collect_and_dedupe(
    priority_only: bool = False,
    include_quarantine: bool = True,
) -> Tuple[List[str], Dict[str, str], List[str]]:
    """
    Returns (ordered_tickers, ticker_to_source, invalid_symbols).
    Deduplication keeps the first (highest-priority) source per ticker.
    """
    raw = _collect_candidates(priority_only=priority_only,
                               include_quarantine=include_quarantine)
    seen: Dict[str, Tuple[int, str]] = {}
    invalid: List[str] = []

    for ticker, rank, source in raw:
        t = ticker.strip().upper()
        if _is_invalid(t):
            if t and t not in invalid:
                invalid.append(t)
            continue
        if t not in seen or rank < seen[t][0]:
            seen[t] = (rank, source)

    # Sort by priority rank then alphabetically
    ordered = sorted(seen.keys(), key=lambda t: (seen[t][0], t))
    ticker_source = {t: seen[t][1] for t in ordered}
    return ordered, ticker_source, invalid


# ── Bar-depth helpers ─────────────────────────────────────────────────────────

def _bar_count(ticker: str) -> Tuple[int, str]:
    """
    Return (bar_count, source) where source is 'deep', 'shallow', or 'none'.
    Prefers deep cache (cache/prices_deep) over shallow (cache/prices).
    """
    import pandas as pd
    t = ticker.upper()

    deep_path = DEEP_DIR / f"{t}.parquet"
    if deep_path.exists():
        try:
            df = pd.read_parquet(deep_path)
            n = int(df["close"].notna().sum()) if "close" in df.columns else len(df)
            return n, "deep"
        except Exception:
            pass

    shallow_path = SHALLOW_DIR / f"{t}.parquet"
    if shallow_path.exists():
        try:
            df = pd.read_parquet(shallow_path)
            n = int(df["close"].notna().sum()) if "close" in df.columns else len(df)
            return n, "shallow"
        except Exception:
            pass

    return 0, "none"


def _deep_parquet_last_date(ticker: str) -> Optional[str]:
    """Return the last date in the deep parquet, or None."""
    import pandas as pd
    path = DEEP_DIR / f"{ticker.upper()}.parquet"
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path)
        if df.empty:
            return None
        idx = pd.to_datetime(df.index)
        return str(idx.max().date())
    except Exception:
        return None


def _recently_refreshed(ticker: str, recent_days: int = RECENT_REFRESH_DAYS) -> bool:
    """True if deep parquet last date is within recent_days of today."""
    from datetime import timedelta
    last = _deep_parquet_last_date(ticker)
    if not last:
        return False
    try:
        last_dt = datetime.strptime(last, "%Y-%m-%d").date()
        cutoff = (datetime.now(timezone.utc).date() - timedelta(days=recent_days))
        return last_dt >= cutoff
    except Exception:
        return False


# ── FMP backfill ──────────────────────────────────────────────────────────────

def _merge_write(ticker: str, rows: List[Dict]) -> int:
    """Merge-on-write rows into cache/prices_deep/{ticker}.parquet. Returns final bar count."""
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


def _fetch_and_write(ticker: str, min_bars: int) -> Tuple[bool, str, int]:
    """
    Fetch from FMP and merge-write.
    Returns (success, message, bars_after).
    """
    try:
        from core.fmp_client import get_fmp
        fmp = get_fmp()
        rows = fmp.get_ticker_bars(ticker, days=FMP_DAYS_TO_REQUEST)
        if not rows:
            return False, "FMP returned empty result", 0
        bars_after = _merge_write(ticker, rows)
        return True, f"FMP ok, {bars_after} bars written", bars_after
    except Exception as exc:
        return False, f"FMP fetch error: {exc}", 0


# ── Planning phase ────────────────────────────────────────────────────────────

def build_plan(
    min_bars: int = DEFAULT_MIN_BARS,
    limit: int = DEFAULT_LIMIT,
    max_provider_calls: int = DEFAULT_MAX_PROVIDER_CALLS,
    priority_only: bool = False,
    include_quarantine: bool = True,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    """
    Build a backfill plan without making provider calls.
    Returns a structured plan dict used by both dry-run and execute paths.
    """
    started_at = datetime.now(timezone.utc).isoformat()

    all_candidates, ticker_source, invalid = collect_and_dedupe(
        priority_only=priority_only,
        include_quarantine=include_quarantine,
    )

    # Apply candidate limit
    candidates = all_candidates[:limit] if limit > 0 else all_candidates

    plan_entries: List[Dict[str, Any]] = []
    selected: List[str] = []
    skipped_enough: List[str] = []
    skipped_recent: List[str] = []

    provider_calls_planned = 0

    for ticker in candidates:
        bars_before, cache_src = _bar_count(ticker)
        already_recent = _recently_refreshed(ticker) and not force_refresh

        entry: Dict[str, Any] = {
            "ticker": ticker,
            "source": ticker_source.get(ticker, "unknown"),
            "bars_before": bars_before,
            "cache_source": cache_src,
            "bars_after": None,
            "action": None,
            "reason": None,
        }

        if bars_before >= min_bars and not force_refresh:
            entry["action"] = "skip_enough"
            entry["reason"] = f"{bars_before} bars >= {min_bars} target"
            skipped_enough.append(ticker)
        elif already_recent:
            entry["action"] = "skip_recent"
            entry["reason"] = f"deep parquet refreshed within {RECENT_REFRESH_DAYS}d"
            skipped_recent.append(ticker)
        elif provider_calls_planned >= max_provider_calls:
            entry["action"] = "skip_budget"
            entry["reason"] = f"provider call cap ({max_provider_calls}) reached"
        else:
            entry["action"] = "backfill"
            entry["reason"] = f"{bars_before} bars < {min_bars} target"
            selected.append(ticker)
            provider_calls_planned += 1

        plan_entries.append(entry)

    return {
        "started_at": started_at,
        "version": VERSION,
        "research_only": True,
        "disclaimer": (
            "RESEARCH-ONLY price-cache backfill. "
            "Writes only to cache/prices_deep/. "
            "No trade signals, no paper trading, no execution, no DB writes."
        ),
        "params": {
            "min_bars": min_bars,
            "limit": limit,
            "max_provider_calls": max_provider_calls,
            "priority_only": priority_only,
            "include_quarantine": include_quarantine,
            "force_refresh": force_refresh,
        },
        "candidate_sources": [
            "daily_alpha_radar_latest.json",
            "research_scanner_latest.json",
            "rs_theme_lens_triage_latest.json",
            "recall_repair_shadow_lane_latest.json",
            "research_forward_latest.json",
        ],
        "total_candidates_collected": len(all_candidates),
        "total_after_limit": len(candidates),
        "invalid_skipped_count": len(invalid),
        "invalid_skipped_sample": invalid[:20],
        "selected_for_backfill": len(selected),
        "selected_tickers": selected,
        "skipped_enough_history": len(skipped_enough),
        "skipped_recently_refreshed": len(skipped_recent),
        "provider_calls_planned": provider_calls_planned,
        "provider_calls_used": 0,
        "successes": 0,
        "failures": 0,
        "entries": plan_entries,
        "completed_at": None,
        "remaining_insufficient": None,
    }


# ── Execution phase ────────────────────────────────────────────────────────────

def execute_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Execute the backfill plan (provider calls). Mutates and returns plan."""
    provider_calls = 0
    successes = 0
    failures = 0

    for entry in plan["entries"]:
        if entry["action"] != "backfill":
            continue

        ticker = entry["ticker"]
        logger.info("Backfilling %s (before: %d bars)", ticker, entry["bars_before"])
        ok, msg, bars_after = _fetch_and_write(ticker, plan["params"]["min_bars"])
        provider_calls += 1
        entry["bars_after"] = bars_after
        entry["fetch_result"] = msg

        if ok:
            successes += 1
            entry["action"] = "backfilled"
            logger.info("  %s: %s", ticker, msg)
        else:
            failures += 1
            entry["action"] = "failed"
            logger.warning("  %s failed: %s", ticker, msg)

    # Compute remaining insufficient
    min_bars = plan["params"]["min_bars"]
    still_insufficient = []
    for entry in plan["entries"]:
        bars = entry.get("bars_after") or entry.get("bars_before") or 0
        if bars < min_bars:
            still_insufficient.append(entry["ticker"])

    plan["provider_calls_used"] = provider_calls
    plan["successes"] = successes
    plan["failures"] = failures
    plan["completed_at"] = datetime.now(timezone.utc).isoformat()
    plan["remaining_insufficient"] = len(still_insufficient)
    plan["remaining_insufficient_tickers"] = still_insufficient[:30]
    return plan


# ── Report rendering ──────────────────────────────────────────────────────────

def _render_txt(plan: Dict[str, Any], dry_run: bool) -> List[str]:
    p = plan["params"]
    L = [
        f"== TARGETED PRICE BACKFILL ({plan['started_at']}) ==",
        plan["disclaimer"],
        f"dry_run={'YES' if dry_run else 'NO'}  ·  min_bars={p['min_bars']}  ·  "
        f"limit={p['limit']}  ·  max_provider_calls={p['max_provider_calls']}",
        f"priority_only={p['priority_only']}  ·  include_quarantine={p['include_quarantine']}",
        "",
        f"Candidate sources: {', '.join(plan['candidate_sources'])}",
        f"Total collected: {plan['total_candidates_collected']}  ·  "
        f"after limit: {plan['total_after_limit']}  ·  "
        f"invalid skipped: {plan['invalid_skipped_count']}",
        "",
        f"Selected for backfill:      {plan['selected_for_backfill']}",
        f"Skipped (enough history):   {plan['skipped_enough_history']}",
        f"Skipped (recent refresh):   {plan['skipped_recently_refreshed']}",
        f"Provider calls planned:     {plan['provider_calls_planned']}",
        f"Provider calls used:        {plan['provider_calls_used']}",
        f"Successes:                  {plan['successes']}",
        f"Failures:                   {plan['failures']}",
    ]
    if plan.get("remaining_insufficient") is not None:
        L.append(f"Still insufficient:         {plan['remaining_insufficient']}")
    L.append("")

    if dry_run:
        L.append("=== DRY-RUN PLAN (no provider calls made) ===")
    else:
        L.append("=== BACKFILL RESULTS ===")
    L.append("")
    L.append(f"{'ticker':<8} {'action':<16} {'before':>7} {'after':>7} {'source'}")
    L.append("-" * 65)
    for e in plan["entries"]:
        after = str(e.get("bars_after") or "") if e.get("bars_after") is not None else ""
        L.append(
            f"{e['ticker']:<8} {e['action']:<16} {e['bars_before']:>7} {after:>7}  {e['source']}"
        )
    if plan["invalid_skipped_count"] > 0:
        L += ["", f"Invalid/skipped symbols ({plan['invalid_skipped_count']}): "
              + ", ".join(plan["invalid_skipped_sample"])]
    if plan.get("completed_at"):
        L.append(f"\nCompleted at: {plan['completed_at']}")
    return L


def write_artifacts(plan: Dict[str, Any], dry_run: bool) -> None:
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = _render_txt(plan, dry_run)
    OUT_TXT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("Artifacts written: %s", OUT_JSON)
    logger.info("Text log written: %s", OUT_TXT)


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Targeted research price-cache backfill (RESEARCH-ONLY)."
    )
    ap.add_argument("--dry-run", action="store_true", default=False,
                    help="Plan only — no provider calls (default: False, but see --execute)")
    ap.add_argument("--execute", action="store_true", default=False,
                    help="Actually fetch from FMP. Without this flag, --dry-run is forced.")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                    help=f"Max candidates to inspect (default {DEFAULT_LIMIT})")
    ap.add_argument("--max-provider-calls", type=int, default=DEFAULT_MAX_PROVIDER_CALLS,
                    help=f"Hard cap on FMP calls (default {DEFAULT_MAX_PROVIDER_CALLS})")
    ap.add_argument("--min-bars", type=int, default=DEFAULT_MIN_BARS,
                    help=f"Minimum bars target (default {DEFAULT_MIN_BARS})")
    ap.add_argument("--priority-only", action="store_true", default=False,
                    help="Only include HIGH_PRIORITY/RESET/RECLAIM tickers")
    ap.add_argument("--include-quarantine", action="store_true", default=True,
                    help="Include DATA_QUARANTINE tickers (default True)")
    ap.add_argument("--no-quarantine", action="store_false", dest="include_quarantine",
                    help="Exclude DATA_QUARANTINE tickers")
    ap.add_argument("--force-refresh", action="store_true", default=False,
                    help="Re-fetch even if deep parquet is recent")
    ap.add_argument("--json-out", type=str, default=None,
                    help="Custom JSON output path (overrides default)")
    ap.add_argument("--text-out", type=str, default=None,
                    help="Custom text output path (overrides default)")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    global OUT_JSON, OUT_TXT
    args = _parse_args(argv)

    # Safety: --execute is required to actually call FMP.
    # If neither --dry-run nor --execute is given, default to dry-run.
    dry_run = args.dry_run or not args.execute

    if args.json_out:
        OUT_JSON = Path(args.json_out)
    if args.text_out:
        OUT_TXT = Path(args.text_out)

    if dry_run:
        logger.info("DRY-RUN mode — no provider calls will be made")
    else:
        logger.info("EXECUTE mode — FMP provider calls enabled (cap: %d)",
                    args.max_provider_calls)

    plan = build_plan(
        min_bars=args.min_bars,
        limit=args.limit,
        max_provider_calls=args.max_provider_calls,
        priority_only=args.priority_only,
        include_quarantine=args.include_quarantine,
        force_refresh=args.force_refresh,
    )

    if not dry_run:
        plan = execute_plan(plan)
    else:
        plan["completed_at"] = datetime.now(timezone.utc).isoformat()
        plan["remaining_insufficient"] = len([
            e for e in plan["entries"] if e["bars_before"] < args.min_bars
        ])

    write_artifacts(plan, dry_run)

    # Print summary
    lines = _render_txt(plan, dry_run)
    for line in lines[:30]:
        print(line)
    if len(lines) > 30:
        print(f"... ({len(lines) - 30} more lines in {OUT_TXT})")

    logger.info("targeted-backfill complete: selected=%d used=%d success=%d fail=%d",
                plan["selected_for_backfill"], plan["provider_calls_used"],
                plan["successes"], plan["failures"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

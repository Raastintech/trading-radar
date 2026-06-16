#!/usr/bin/env python3
"""
research/research_watchlist_forward_tracker.py — Forward outcome tracking for
the research scanner watchlist.

For each ticker that appears in research_scanner_latest.json, records the date
of first appearance, score, label, and category.  On subsequent runs, computes
forward price returns at 5d, 10d, 20d, and 60d using the cached price parquets,
alongside SPY, QQQ, and sector-ETF benchmark returns at the same horizons.

Verdict ladder (per bucket):
  NEED_MORE_DATA   — fewer than 10 matured entries
  EARLY_SIGNAL     — matured entries show ≥ 60% positive return at 10d
  MIXED            — matured entries show 40–60% positive at 10d
  NO_FORWARD_EDGE  — < 40% positive at 10d
  PROMISING        — ≥ 70% positive at 10d AND mean_10d > 2%

Outputs:
  data/research/research_watchlist_history.jsonl  (append-only, idempotent by ticker+date)
  cache/research/research_forward_latest.json     (summary by label bucket)
  logs/research_forward_latest.txt

Usage:
  GEM_TRADER_SKIP_DOTENV=true .venv/bin/python research/research_watchlist_forward_tracker.py
  SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python research/research_watchlist_forward_tracker.py
  ./scripts/run_research_cycle.sh research-forward-tracker
"""
from __future__ import annotations

import json
import logging
import os
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

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

import pandas as pd

import core.config as cfg
from core.research_mode import SYSTEM_MODE, RESEARCH_ONLY_BANNER

VERSION = "RESEARCH_FORWARD_TRACKER_V1"
BENCHMARK_SCHEMA_VERSION = "BENCHMARK_RETURNS_V1"
PRICE_DIR = cfg.CACHE_DIR / "prices"
RESEARCH_DIR = cfg.CACHE_DIR / "research"
DATA_DIR = ROOT / "data" / "research"
SCANNER_LATEST = RESEARCH_DIR / "research_scanner_latest.json"
HISTORY_JSONL = DATA_DIR / "research_watchlist_history.jsonl"
OUT_JSON = RESEARCH_DIR / "research_forward_latest.json"
OUT_TXT = cfg.LOG_DIR / "research_forward_latest.txt"

HORIZONS = [5, 10, 20, 60]

# Sector → benchmark ETF mapping for relative-return computation
SECTOR_ETF_MAP: Dict[str, str] = {
    "Technology": "XLK",
    "Communication Services": "XLC",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Health Care": "XLV",
    "Healthcare": "XLV",
    "Financials": "XLF",
    "Financial Services": "XLF",
    "Industrials": "XLI",
    "Materials": "XLB",
    "Energy": "XLE",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
}

# Sample status thresholds
SAMPLE_TOO_EARLY = "TOO_EARLY"        # < 10 matured observations
SAMPLE_PROVISIONAL = "PROVISIONAL"    # 10–29 matured observations
SAMPLE_MEANINGFUL = "MEANINGFUL"      # 30–99 matured observations
SAMPLE_ROBUST = "ROBUST"              # ≥ 100 matured observations

SAMPLE_THRESHOLD_PROVISIONAL = 10
SAMPLE_THRESHOLD_MEANINGFUL = 30
SAMPLE_THRESHOLD_ROBUST = 100


def _sample_status(n_matured: int) -> str:
    if n_matured < SAMPLE_THRESHOLD_PROVISIONAL:
        return SAMPLE_TOO_EARLY
    if n_matured < SAMPLE_THRESHOLD_MEANINGFUL:
        return SAMPLE_PROVISIONAL
    if n_matured < SAMPLE_THRESHOLD_ROBUST:
        return SAMPLE_MEANINGFUL
    return SAMPLE_ROBUST


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("research_watchlist_forward_tracker")


def _benchmark_sector_etf(sector: Optional[str], industry: Optional[str] = None) -> Optional[str]:
    """Return the most appropriate sector ETF ticker for a given sector/industry.

    Semiconductor industry overrides Technology sector → SMH for more precision.
    """
    if industry and "semiconductor" in industry.lower():
        return "SMH"
    return SECTOR_ETF_MAP.get(sector or "")


def _load_closes_with_dates(sym: str) -> List[tuple]:
    """Return list of (date_str, close) sorted ascending.

    Handles both column-stored dates and index-stored dates, since the price
    cache stores dates in the DataFrame index (not as an explicit column).
    """
    path = PRICE_DIR / f"{sym.upper()}.parquet"
    if not path.exists():
        return []
    try:
        df = pd.read_parquet(path)
        close_col = next((c for c in ("close", "Close") if c in df.columns), None)
        if not close_col:
            return []
        date_col = next((c for c in ("date", "Date", "timestamp", "Timestamp") if c in df.columns), None)
        if date_col:
            dates = df[date_col].astype(str).str[:10]
        else:
            # Price cache stores dates in the index — extract from there
            dates = df.index.astype(str).str[:10]
        pairs = list(zip(dates, df[close_col].astype(float)))
        return sorted(pairs, key=lambda x: x[0])
    except Exception:
        return []


def _forward_return(closes_with_dates: List[tuple], appearance_date: str, horizon: int) -> Optional[float]:
    """Return the % change from the close on/after appearance_date to horizon bars later."""
    if not closes_with_dates:
        return None
    idx = None
    for i, (d, _) in enumerate(closes_with_dates):
        if d >= appearance_date:
            idx = i
            break
    if idx is None or idx + horizon >= len(closes_with_dates):
        return None
    entry_close = closes_with_dates[idx][1]
    exit_close = closes_with_dates[idx + horizon][1]
    if not entry_close:
        return None
    return round((exit_close / entry_close - 1.0) * 100.0, 2)


def _load_history() -> Dict[str, Dict[str, Any]]:
    """Load JSONL history keyed by 'ticker|date'."""
    result: Dict[str, Dict[str, Any]] = {}
    if not HISTORY_JSONL.exists():
        return result
    for line in HISTORY_JSONL.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            key = f"{rec['ticker']}|{rec['appearance_date']}"
            result[key] = rec
        except Exception:
            pass
    return result


def _compute_verdicts(entries: List[Dict[str, Any]], bucket_name: str) -> Dict[str, Any]:
    matured = [e for e in entries if e.get("ret_10d") is not None]
    n = len(matured)
    status = _sample_status(n)

    # No bucket verdict until at least PROVISIONAL sample threshold (≥10 matured)
    if n < SAMPLE_THRESHOLD_PROVISIONAL:
        return {
            "bucket": bucket_name,
            "total_entries": len(entries),
            "matured_entries": n,
            "sample_status": status,
            "verdict": "NEED_MORE_DATA",
            "win_rate_10d": None,
            "mean_ret_10d": None,
            "median_ret_10d": None,
            "win_rate_vs_spy": None,
            "avg_ret_vs_spy": None,
            "median_ret_vs_spy": None,
            "n_with_spy_baseline": None,
            "win_rate_vs_qqq": None,
            "avg_ret_vs_qqq": None,
            "median_ret_vs_qqq": None,
            "n_with_qqq_baseline": None,
            "win_rate_vs_sector": None,
            "avg_ret_vs_sector": None,
            "median_ret_vs_sector": None,
            "n_with_sector_baseline": None,
            "note": (
                f"Need ≥{SAMPLE_THRESHOLD_PROVISIONAL} matured for provisional read, "
                f"≥{SAMPLE_THRESHOLD_MEANINGFUL} for meaningful, "
                f"≥{SAMPLE_THRESHOLD_ROBUST} for robust."
            ),
        }

    # Absolute return stats
    ret_10d_vals = [e["ret_10d"] for e in matured]
    pos = sum(1 for r in ret_10d_vals if r > 0)
    pos_rate = round(pos / n, 3)
    mean_10d = round(sum(ret_10d_vals) / n, 2)
    median_10d = round(statistics.median(ret_10d_vals), 2)

    # Primary verdict is based on absolute 10d returns (backward compatible)
    if pos_rate >= 0.70 and mean_10d > 2.0:
        verdict = "PROMISING"
    elif pos_rate >= 0.60:
        verdict = "EARLY_SIGNAL"
    elif pos_rate >= 0.40:
        verdict = "MIXED"
    else:
        verdict = "NO_FORWARD_EDGE"

    def _bench_stats(field: str):
        vals = [e[field] for e in matured if e.get(field) is not None]
        if not vals:
            return None, None, None, None
        avg = round(sum(vals) / len(vals), 2)
        med = round(statistics.median(vals), 2)
        wr = round(sum(1 for v in vals if v > 0) / len(vals), 3)
        return avg, med, wr, len(vals)

    avg_vs_spy, med_vs_spy, wr_vs_spy, n_spy = _bench_stats("ret_10d_vs_spy")
    avg_vs_qqq, med_vs_qqq, wr_vs_qqq, n_qqq = _bench_stats("ret_10d_vs_qqq")
    avg_vs_sec, med_vs_sec, wr_vs_sec, n_sec = _bench_stats("ret_10d_vs_sector")

    return {
        "bucket": bucket_name,
        "total_entries": len(entries),
        "matured_entries": n,
        "sample_status": status,
        "verdict": verdict,
        "win_rate_10d": pos_rate,
        "mean_ret_10d": mean_10d,
        "median_ret_10d": median_10d,
        "win_rate_vs_spy": wr_vs_spy,
        "avg_ret_vs_spy": avg_vs_spy,
        "median_ret_vs_spy": med_vs_spy,
        "n_with_spy_baseline": n_spy,
        "win_rate_vs_qqq": wr_vs_qqq,
        "avg_ret_vs_qqq": avg_vs_qqq,
        "median_ret_vs_qqq": med_vs_qqq,
        "n_with_qqq_baseline": n_qqq,
        "win_rate_vs_sector": wr_vs_sec,
        "avg_ret_vs_sector": avg_vs_sec,
        "median_ret_vs_sector": med_vs_sec,
        "n_with_sector_baseline": n_sec,
        "note": None,
    }


def _benchmark_readiness(history: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Summarise how much of the history has benchmark returns populated."""
    all_recs = list(history.values())
    total = len(all_recs)
    if total == 0:
        return {
            "spy_available": True,
            "qqq_available": True,
            "total_entries": 0,
            "entries_with_spy_10d": 0,
            "entries_with_qqq_10d": 0,
            "entries_with_sector_etf": 0,
            "entries_with_sector_10d": 0,
            "benchmark_schema_version": BENCHMARK_SCHEMA_VERSION,
        }
    n_spy = sum(1 for r in all_recs if r.get("spy_ret_10d") is not None)
    n_qqq = sum(1 for r in all_recs if r.get("qqq_ret_10d") is not None)
    n_with_etf = sum(1 for r in all_recs if r.get("benchmark_sector_etf"))
    n_sec = sum(1 for r in all_recs if r.get("sector_ret_10d") is not None)
    return {
        "spy_available": True,
        "qqq_available": True,
        "total_entries": total,
        "entries_with_spy_10d": n_spy,
        "entries_with_qqq_10d": n_qqq,
        "entries_with_sector_etf": n_with_etf,
        "entries_with_sector_10d": n_sec,
        "benchmark_schema_version": BENCHMARK_SCHEMA_VERSION,
    }


def run_forward_tracker() -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    today = now[:10]
    logger.info("Research Watchlist Forward Tracker %s starting", VERSION)

    if not SCANNER_LATEST.exists():
        logger.error("research_scanner_latest.json not found — run research-scanner first")
        return {
            "version": VERSION, "generated_at": now, "system_mode": SYSTEM_MODE,
            "research_only": True, "error": "scanner_missing",
            "verdicts": [], "new_entries_today": 0, "guardrails": {"no_trade_recommendation": True},
        }

    scanner = json.loads(SCANNER_LATEST.read_text(encoding="utf-8"))
    watchlist = scanner.get("watchlist", [])
    logger.info("Current watchlist: %d tickers", len(watchlist))

    # Build sector lookup from current scanner output for existing-entry backfill
    sector_lookup: Dict[str, Dict[str, Optional[str]]] = {
        item["ticker"]: {
            "sector": item.get("sector"),
            "industry": item.get("industry"),
        }
        for item in watchlist
    }

    history = _load_history()
    new_entries: List[Dict[str, Any]] = []

    for item in watchlist:
        ticker = item["ticker"]
        key = f"{ticker}|{today}"
        if key not in history:
            sector = item.get("sector")
            industry = item.get("industry")
            sector_etf = _benchmark_sector_etf(sector, industry)
            rec: Dict[str, Any] = {
                "ticker": ticker,
                "appearance_date": today,
                "sector": sector,
                "industry": industry,
                "benchmark_sector_etf": sector_etf,
                "watchlist_label": item.get("watchlist_label"),
                "category": item.get("category"),
                "research_score": item.get("research_score"),
                "earliness_label": item.get("earliness_label"),
                "consensus_label": item.get("consensus_label"),
                "ret_5d": None,
                "ret_10d": None,
                "ret_20d": None,
                "ret_60d": None,
                "resolved": False,
            }
            history[key] = rec
            new_entries.append(rec)

    # Backfill sector/benchmark_sector_etf for existing entries that predate this schema
    for rec in history.values():
        if "benchmark_sector_etf" not in rec:
            ticker = rec["ticker"]
            if ticker in sector_lookup:
                s = sector_lookup[ticker].get("sector")
                ind = sector_lookup[ticker].get("industry")
            else:
                s, ind = None, None
            rec.setdefault("sector", s)
            rec.setdefault("industry", ind)
            rec["benchmark_sector_etf"] = _benchmark_sector_etf(s, ind)

    # Preload benchmark closes — reused across all entries
    benchmark_closes: Dict[str, List[tuple]] = {}
    for bm in ("SPY", "QQQ"):
        benchmark_closes[bm] = _load_closes_with_dates(bm)
        logger.info("Benchmark %s: %d bars loaded", bm, len(benchmark_closes[bm]))

    # Collect unique sector ETFs needed and preload them
    sector_etfs_needed = {
        rec["benchmark_sector_etf"]
        for rec in history.values()
        if rec.get("benchmark_sector_etf")
    }
    for etf in sector_etfs_needed:
        if etf not in benchmark_closes:
            benchmark_closes[etf] = _load_closes_with_dates(etf)
            logger.info("Sector ETF %s: %d bars loaded", etf, len(benchmark_closes[etf]))

    spy_closes = benchmark_closes.get("SPY", [])
    qqq_closes = benchmark_closes.get("QQQ", [])

    # Resolve forward returns for unresolved entries
    resolved_count = 0
    for rec in history.values():
        ticker = rec["ticker"]
        date = rec["appearance_date"]

        # Skip only when ticker is fully resolved AND benchmark schema is current
        ticker_resolved = all(rec.get(f"ret_{h}d") is not None for h in HORIZONS)
        benchmarks_ok = rec.get("benchmark_schema_version") == BENCHMARK_SCHEMA_VERSION
        if ticker_resolved and benchmarks_ok:
            continue

        sector_etf = rec.get("benchmark_sector_etf")
        sector_closes = benchmark_closes.get(sector_etf, []) if sector_etf else []

        closes = None
        updated = False

        for h in HORIZONS:
            ret_field = f"ret_{h}d"

            # Compute ticker return if not yet available
            if rec.get(ret_field) is None:
                if closes is None:
                    closes = _load_closes_with_dates(ticker)
                ret = _forward_return(closes, date, h)
                if ret is not None:
                    rec[ret_field] = ret
                    updated = True

            # Fill benchmark fields whenever the ticker return for this horizon is ready
            ret_val = rec.get(ret_field)
            if ret_val is not None:
                spy_field = f"spy_ret_{h}d"
                if rec.get(spy_field) is None:
                    spy_ret = _forward_return(spy_closes, date, h)
                    if spy_ret is not None:
                        rec[spy_field] = spy_ret
                        rec[f"ret_{h}d_vs_spy"] = round(ret_val - spy_ret, 2)
                        updated = True

                qqq_field = f"qqq_ret_{h}d"
                if rec.get(qqq_field) is None:
                    qqq_ret = _forward_return(qqq_closes, date, h)
                    if qqq_ret is not None:
                        rec[qqq_field] = qqq_ret
                        rec[f"ret_{h}d_vs_qqq"] = round(ret_val - qqq_ret, 2)
                        updated = True

                if sector_closes:
                    sec_field = f"sector_ret_{h}d"
                    if rec.get(sec_field) is None:
                        sec_ret = _forward_return(sector_closes, date, h)
                        if sec_ret is not None:
                            rec[sec_field] = sec_ret
                            rec[f"ret_{h}d_vs_sector"] = round(ret_val - sec_ret, 2)
                            updated = True

        if updated:
            resolved_count += 1

        # Mark benchmark schema version once SPY/QQQ returns are populated for
        # every horizon that has a ticker return (sector ETF is optional)
        horizons_with_ticker = [h for h in HORIZONS if rec.get(f"ret_{h}d") is not None]
        if horizons_with_ticker:
            spy_complete = all(rec.get(f"spy_ret_{h}d") is not None for h in horizons_with_ticker)
            qqq_complete = all(rec.get(f"qqq_ret_{h}d") is not None for h in horizons_with_ticker)
            if spy_complete and qqq_complete:
                rec["benchmark_schema_version"] = BENCHMARK_SCHEMA_VERSION
                rec["benchmark_fields_updated_at"] = now
                reasons: List[str] = []
                if not sector_etf:
                    reasons.append("no_sector_etf")
                elif not sector_closes:
                    reasons.append("sector_etf_parquet_missing")
                rec["benchmark_missing_reasons"] = reasons if reasons else None

        # Update resolved flag — all 4 horizons must have ticker returns
        rec["resolved"] = all(rec.get(f"ret_{h}d") is not None for h in HORIZONS)

    # Write back JSONL (idempotent: rewrite full file)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(rec, separators=(",", ":")) for rec in history.values()]
    HISTORY_JSONL.write_text("\n".join(lines) + "\n" if lines else "", encoding="utf-8")
    logger.info(
        "History: %d entries (%d new today, %d updated/resolved)",
        len(history), len(new_entries), resolved_count,
    )

    # Compute verdicts by bucket (watchlist_label)
    all_entries = list(history.values())
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for rec in all_entries:
        lb = rec.get("watchlist_label") or "UNKNOWN"
        buckets.setdefault(lb, []).append(rec)

    verdicts = [_compute_verdicts(entries, bucket) for bucket, entries in sorted(buckets.items())]

    overall_verdict = _compute_verdicts(all_entries, "ALL")
    bench_readiness = _benchmark_readiness(history)

    out: Dict[str, Any] = {
        "version": VERSION,
        "generated_at": now,
        "system_mode": SYSTEM_MODE,
        "research_only": True,
        "total_history_entries": len(history),
        "new_entries_today": len(new_entries),
        "updated_entries_today": resolved_count,
        "overall": overall_verdict,
        "verdicts_by_label": verdicts,
        "benchmark_readiness": bench_readiness,
        "guardrails": {
            "no_trade_recommendation": True,
            "forward_returns_are_research_only": True,
            "past_performance_not_predictive": True,
        },
    }
    return out


def _format_text(result: Dict[str, Any]) -> str:
    lines = [
        RESEARCH_ONLY_BANNER,
        "",
        f"RESEARCH WATCHLIST FORWARD TRACKER  [{result['version']}]",
        f"Generated: {result['generated_at']}",
        f"Total history entries: {result.get('total_history_entries', 0)}",
        f"New entries today:     {result.get('new_entries_today', 0)}",
        "",
    ]
    overall = result.get("overall", {})
    lines.append(f"Overall verdict: {overall.get('verdict', 'N/A')}  "
                 f"(n={overall.get('matured_entries', 0)}, "
                 f"win%={overall.get('win_rate_10d') or 'n/a'}, "
                 f"mean10d={overall.get('mean_ret_10d') or 'n/a'})")

    # Benchmark readiness summary
    br = result.get("benchmark_readiness", {})
    if br:
        total = br.get("total_entries", 0)
        n_spy = br.get("entries_with_spy_10d", 0)
        n_sec = br.get("entries_with_sector_10d", 0)
        n_etf = br.get("entries_with_sector_etf", 0)
        lines += [
            "",
            "=== BENCHMARK READINESS ===",
            f"  SPY/QQQ baseline entries: {n_spy}/{total}",
            f"  Sector ETF assigned:       {n_etf}/{total}",
            f"  Sector ETF returns ready:  {n_sec}/{total}",
        ]

    lines += ["", "=== BY LABEL BUCKET ==="]
    for v in result.get("verdicts_by_label", []):
        abs_str = f"win%={v.get('win_rate_10d') or 'n/a'}  mean10d={v.get('mean_ret_10d') or 'n/a'}"
        vs_spy = v.get("avg_ret_vs_spy")
        spy_str = f"  vs_SPY={vs_spy:+.2f}%" if vs_spy is not None else ""
        vs_qqq = v.get("avg_ret_vs_qqq")
        qqq_str = f"  vs_QQQ={vs_qqq:+.2f}%" if vs_qqq is not None else ""
        lines.append(
            f"  {v['bucket']:<26}  n={v['matured_entries']:3d}/{v['total_entries']:3d}  "
            f"verdict={v['verdict']:<22}  {abs_str}{spy_str}{qqq_str}"
        )
    lines += [
        "",
        "NOTE: Forward returns are research-only and not predictive.",
        "--- RESEARCH ONLY — NO TRADE RECOMMENDATIONS ---",
    ]
    return "\n".join(lines)


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Research Watchlist Forward Tracker (research-only)")
    parser.parse_args()

    print(RESEARCH_ONLY_BANNER)
    result = run_forward_tracker()

    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    cfg.LOG_DIR.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(result, indent=2), encoding="utf-8")
    OUT_TXT.write_text(_format_text(result), encoding="utf-8")
    logger.info("wrote %s", OUT_JSON)

    print(f"\nForward tracker complete.")
    print(f"Total entries: {result.get('total_history_entries', 0)}")
    print(f"New today: {result.get('new_entries_today', 0)}")
    overall = result.get("overall", {})
    print(f"Overall verdict: {overall.get('verdict', 'N/A')} "
          f"(n_matured={overall.get('matured_entries', 0)})")
    br = result.get("benchmark_readiness", {})
    if br:
        print(f"Benchmark: SPY ready for {br.get('entries_with_spy_10d', 0)}/{br.get('total_entries', 0)} entries")
    print(f"Artifact: {OUT_JSON}")


if __name__ == "__main__":
    main()

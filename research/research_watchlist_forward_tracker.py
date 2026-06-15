#!/usr/bin/env python3
"""
research/research_watchlist_forward_tracker.py — Forward outcome tracking for
the research scanner watchlist.

For each ticker that appears in research_scanner_latest.json, records the date
of first appearance, score, label, and category.  On subsequent runs, computes
forward price returns at 5d, 10d, and 20d using the cached price parquets.

Verdict ladder (per bucket):
  NEED_MORE_DATA   — fewer than 5 matured entries
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
PRICE_DIR = cfg.CACHE_DIR / "prices"
RESEARCH_DIR = cfg.CACHE_DIR / "research"
DATA_DIR = ROOT / "data" / "research"
SCANNER_LATEST = RESEARCH_DIR / "research_scanner_latest.json"
HISTORY_JSONL = DATA_DIR / "research_watchlist_history.jsonl"
OUT_JSON = RESEARCH_DIR / "research_forward_latest.json"
OUT_TXT = cfg.LOG_DIR / "research_forward_latest.txt"

MIN_MATURED_FOR_VERDICT = 5
HORIZONS = [5, 10, 20]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("research_watchlist_forward_tracker")


def _load_closes_with_dates(sym: str) -> List[tuple]:
    """Return list of (date_str, close) sorted ascending."""
    path = PRICE_DIR / f"{sym.upper()}.parquet"
    if not path.exists():
        return []
    try:
        df = pd.read_parquet(path)
        close_col = next((c for c in ("close", "Close") if c in df.columns), None)
        date_col = next((c for c in ("date", "Date", "timestamp", "Timestamp") if c in df.columns), None)
        if not close_col or not date_col:
            return []
        pairs = list(zip(df[date_col].astype(str).str[:10], df[close_col].astype(float)))
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
    if n < MIN_MATURED_FOR_VERDICT:
        verdict = "NEED_MORE_DATA"
        pos_rate = None
        mean_10d = None
    else:
        pos = sum(1 for e in matured if (e.get("ret_10d") or 0) > 0)
        pos_rate = round(pos / n, 3)
        mean_10d = round(sum(e["ret_10d"] for e in matured) / n, 2)
        if pos_rate >= 0.70 and mean_10d > 2.0:
            verdict = "PROMISING"
        elif pos_rate >= 0.60:
            verdict = "EARLY_SIGNAL"
        elif pos_rate >= 0.40:
            verdict = "MIXED"
        else:
            verdict = "NO_FORWARD_EDGE"

    return {
        "bucket": bucket_name,
        "total_entries": len(entries),
        "matured_entries": n,
        "verdict": verdict,
        "win_rate_10d": pos_rate,
        "mean_ret_10d": mean_10d,
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

    history = _load_history()
    new_entries: List[Dict[str, Any]] = []

    for item in watchlist:
        ticker = item["ticker"]
        key = f"{ticker}|{today}"
        if key not in history:
            rec: Dict[str, Any] = {
                "ticker": ticker,
                "appearance_date": today,
                "watchlist_label": item.get("watchlist_label"),
                "category": item.get("category"),
                "research_score": item.get("research_score"),
                "earliness_label": item.get("earliness_label"),
                "consensus_label": item.get("consensus_label"),
                "ret_5d": None,
                "ret_10d": None,
                "ret_20d": None,
                "resolved": False,
            }
            history[key] = rec
            new_entries.append(rec)

    # Resolve forward returns for unresolved entries
    resolved_count = 0
    for rec in history.values():
        if rec.get("resolved"):
            continue
        ticker = rec["ticker"]
        date = rec["appearance_date"]
        closes = _load_closes_with_dates(ticker)
        updated = False
        for h in HORIZONS:
            field = f"ret_{h}d"
            if rec.get(field) is None:
                ret = _forward_return(closes, date, h)
                if ret is not None:
                    rec[field] = ret
                    updated = True
        # Mark resolved if all horizons computed
        if all(rec.get(f"ret_{h}d") is not None for h in HORIZONS):
            rec["resolved"] = True
            resolved_count += 1
        elif updated:
            resolved_count += 1  # partial update

    # Write back JSONL (idempotent: rewrite full file)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(rec, separators=(",", ":")) for rec in history.values()]
    HISTORY_JSONL.write_text("\n".join(lines) + "\n" if lines else "", encoding="utf-8")
    logger.info("History: %d entries (%d new today, %d updated)", len(history), len(new_entries), resolved_count)

    # Compute verdicts by bucket (watchlist_label)
    all_entries = list(history.values())
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for rec in all_entries:
        lb = rec.get("watchlist_label") or "UNKNOWN"
        buckets.setdefault(lb, []).append(rec)

    verdicts = [_compute_verdicts(entries, bucket) for bucket, entries in sorted(buckets.items())]

    # Overall verdict
    overall_matured = [e for e in all_entries if e.get("ret_10d") is not None]
    overall_verdict = _compute_verdicts(all_entries, "ALL")

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
    lines += ["", "=== BY LABEL BUCKET ==="]
    for v in result.get("verdicts_by_label", []):
        lines.append(
            f"  {v['bucket']:<22}  n={v['matured_entries']:3d}/{v['total_entries']:3d}  "
            f"verdict={v['verdict']:<22}  win%={v.get('win_rate_10d') or 'n/a'}"
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
    print(f"Artifact: {OUT_JSON}")


if __name__ == "__main__":
    main()

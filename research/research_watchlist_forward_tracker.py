#!/usr/bin/env python3
"""
research/research_watchlist_forward_tracker.py — Forward outcome tracking for
the research scanner watchlist.

For each ticker that appears in research_scanner_latest.json, records the date
of first appearance, score, label, and category.  On subsequent runs, computes
forward price returns at every configured research horizon using the cached
price parquets, alongside SPY, QQQ, IWM, and sector-ETF benchmark returns at
the same horizons.

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

import pandas as pd

import core.config as cfg
from core.research_mode import SYSTEM_MODE, RESEARCH_ONLY_BANNER
from research.research_programs import TRACKED_HORIZONS, classify_label

VERSION = "RESEARCH_FORWARD_TRACKER_V2"
BENCHMARK_SCHEMA_VERSION = "BENCHMARK_RETURNS_V2"
PRICE_DIR = cfg.CACHE_DIR / "prices"
PRICE_DEEP_DIR = cfg.CACHE_DIR / "prices_deep"
RESEARCH_DIR = cfg.CACHE_DIR / "research"
DATA_DIR = ROOT / "data" / "research"
SCANNER_LATEST = RESEARCH_DIR / "research_scanner_latest.json"
HISTORY_JSONL = DATA_DIR / "research_watchlist_history.jsonl"
OUT_JSON = RESEARCH_DIR / "research_forward_latest.json"
OUT_TXT = cfg.LOG_DIR / "research_forward_latest.txt"
OUT_UNRESOLVED_JSON = RESEARCH_DIR / "forward_unresolved_cohorts_latest.json"
OUT_UNRESOLVED_TXT = cfg.LOG_DIR / "forward_unresolved_cohorts_latest.txt"

# Canonical multi-program horizon set (Phase 5.1) — defined once in
# research/research_programs.py so architecture and measurement agree.
HORIZONS = list(TRACKED_HORIZONS)

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

# Below this, a matured horizon is not treated as clean evidence; the
# verdict ladder remains unchanged, but the artifact reports the attrition
# warning beside the stats.
MIN_RESOLUTION_COVERAGE_PCT = 90.0

REPAIRABLE = "REPAIRABLE"
NEEDS_MAPPING = "NEEDS_MAPPING"
NON_COMMON_SECURITY = "NON_COMMON_SECURITY"
PROVIDER_GAP = "PROVIDER_GAP"
INVALID_OR_DELISTED = "INVALID_OR_DELISTED"
UNKNOWN_REPAIRABILITY = "UNKNOWN"

REASON_MISSING_PRICE_BAR = "missing price bar"
REASON_STALE_PRICE_HISTORY = "stale price history"
REASON_TICKER_MAPPING = "ticker mapping issue"
REASON_NON_COMMON_SECURITY = "warrant / unit / non-common security"
REASON_DELISTED_CORPORATE_ACTION = "delisted / corporate action"
REASON_PROVIDER_GAP = "provider gap"
REASON_SYMBOL_CHANGED = "symbol changed"
REASON_INSUFFICIENT_HISTORY = "insufficient history"
REASON_INVALID_TICKER = "invalid ticker"
REASON_UNRESOLVED_UNKNOWN = "unresolved unknown"

NON_COMMON_SECURITY_TYPES = {
    "POSSIBLE_WARRANT",
    "POSSIBLE_UNIT_OR_RIGHT",
    "UNSUPPORTED_SHARE_CLASS_FORMAT",
}


def _sample_status(n_matured: int) -> str:
    if n_matured < SAMPLE_THRESHOLD_PROVISIONAL:
        return SAMPLE_TOO_EARLY
    if n_matured < SAMPLE_THRESHOLD_MEANINGFUL:
        return SAMPLE_PROVISIONAL
    if n_matured < SAMPLE_THRESHOLD_ROBUST:
        return SAMPLE_MEANINGFUL
    return SAMPLE_ROBUST


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
from research.forward_stat_safety import (  # noqa: E402
    drop_non_session_bars,
    per_entity_stats,
    robust_stats,
    split_price_artifacts,
)

#: Weekend bars dropped during this run, by ticker — reported, never silent.
NON_SESSION_BARS_DROPPED: Dict[str, List[str]] = {}


logger = logging.getLogger("research_watchlist_forward_tracker")


def _benchmark_sector_etf(sector: Optional[str], industry: Optional[str] = None) -> Optional[str]:
    """Return the most appropriate sector ETF ticker for a given sector/industry.

    Semiconductor industry overrides Technology sector → SMH for more precision.
    """
    if industry and "semiconductor" in industry.lower():
        return "SMH"
    return SECTOR_ETF_MAP.get(sector or "")


def _read_parquet_closes(path: Path) -> List[Tuple[str, float]]:
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
            dates = df.index.astype(str).str[:10]
        pairs = list(zip(dates, df[close_col].astype(float)))
        # A US equity daily series cannot have a weekend bar. When one is
        # present it belongs to another instrument (cache/prices/PI.parquet
        # carried four $0.09 weekend bars from a 24/7 symbol), and it
        # corrupts twice: it supplies a wrong entry price, and it shifts
        # every horizon, because forward returns are counted in bars.
        kept, dropped = drop_non_session_bars(sorted(pairs, key=lambda x: x[0]))
        if dropped:
            NON_SESSION_BARS_DROPPED[path.stem.upper()] = [d for d, _ in dropped]
        return kept
    except Exception:
        return []


def _merge_closes(*series: List[Tuple[str, float]]) -> List[Tuple[str, float]]:
    by_date: Dict[str, float] = {}
    for closes in series:
        for d, close in closes:
            by_date[str(d)[:10]] = float(close)
    return sorted(by_date.items(), key=lambda x: x[0])


def _load_price_cache(sym: str) -> Dict[str, Any]:
    """Load local cached bars for one symbol, merging deep + shallow parquet.

    This is still filesystem-only. Deep cache fills historical gaps; shallow
    cache supplies the latest session bars. Existing resolved history rows are
    not recomputed, so this only repairs previously missing forward returns.
    """
    t = sym.upper()
    shallow = _read_parquet_closes(PRICE_DIR / f"{t}.parquet")
    # Unit tests often patch only PRICE_DIR to a temp directory. Avoid mixing
    # those temp fixtures with the real repo's deep cache unless both cache dirs
    # share the same parent, as they do in production.
    deep = []
    if PRICE_DEEP_DIR.parent == PRICE_DIR.parent:
        deep = _read_parquet_closes(PRICE_DEEP_DIR / f"{t}.parquet")
    closes = _merge_closes(deep, shallow)
    if deep and shallow:
        source = "merged_deep_shallow"
    elif deep:
        source = "deep"
    elif shallow:
        source = "shallow"
    else:
        source = "none"
    return {
        "ticker": t,
        "closes": closes,
        "cache_source": source,
        "bar_count": len(closes),
        "first_date": closes[0][0] if closes else None,
        "last_date": closes[-1][0] if closes else None,
        "shallow_last_date": shallow[-1][0] if shallow else None,
        "deep_last_date": deep[-1][0] if deep else None,
        "shallow_bar_count": len(shallow),
        "deep_bar_count": len(deep),
    }


def _load_closes_with_dates(sym: str) -> List[tuple]:
    """Return list of (date_str, close) sorted ascending.

    Handles both column-stored dates and index-stored dates. Uses only local
    parquet caches, preferring a merge of deep historical bars and shallow
    same-session bars when both are present.
    """
    return _load_price_cache(sym).get("closes") or []


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


def _forward_mae(closes_with_dates: List[tuple], appearance_date: str,
                 horizon: int) -> Optional[float]:
    """Post-selection max adverse excursion: the lowest close inside the
    horizon window relative to the entry close, in % (<= 0).  Uses closes
    for consistency with the return methodology; only computed when the
    full window exists (same maturity rule as the forward return)."""
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
    if not entry_close:
        return None
    window = [c for _, c in closes_with_dates[idx + 1: idx + horizon + 1]]
    if not window:
        return None
    return round(min(0.0, (min(window) / entry_close - 1.0) * 100.0), 2)


# Priority cohorts (Phase 5.1 follow-up): the daily alpha radar's
# priority buckets collapsed into the split the forward evidence reports.
PRIORITY_COHORTS: Dict[str, tuple] = {
    "high_priority": ("HIGH_PRIORITY_RESEARCH", "TOP_RESEARCH"),
    "watch_only": ("WATCHLIST_RESEARCH", "RESET_WATCH"),
    "other": ("EXTENDED_CROWDED", "DATA_QUARANTINE"),
}
PRIORITY_SPLIT_HORIZONS = (5, 10, 20)


def _priority_cohort(rec: Dict[str, Any]) -> str:
    label = rec.get("priority_label")
    if label is None:
        return "unstamped"
    for name, buckets in PRIORITY_COHORTS.items():
        if label in buckets:
            return name
    return "other"


def _priority_split(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Forward evidence split by radar priority cohort: hit rate vs SPY,
    mean/median return and excess, and post-selection max adverse
    excursion at 5/10/20d.  'unstamped' rows predate priority stamping
    and are reported, never silently dropped."""
    split: Dict[str, Any] = {}
    for name in list(PRIORITY_COHORTS) + ["unstamped"]:
        cohort = [r for r in entries if _priority_cohort(r) == name]
        block: Dict[str, Any] = {
            "entries": len(cohort),
            "unique_tickers": len({r.get("ticker") for r in cohort}),
            "horizons": {},
        }
        for h in PRIORITY_SPLIT_HORIZONS:
            rets = [r[f"ret_{h}d"] for r in cohort
                    if r.get(f"ret_{h}d") is not None]
            if not rets:
                continue
            ex = [r[f"ret_{h}d_vs_spy"] for r in cohort
                  if r.get(f"ret_{h}d_vs_spy") is not None]
            maes = [r[f"mae_{h}d"] for r in cohort
                    if r.get(f"mae_{h}d") is not None]
            block["horizons"][f"{h}d"] = {
                "n": len(rets),
                "mean_ret_pct": round(sum(rets) / len(rets), 2),
                "median_ret_pct": round(statistics.median(rets), 2),
                "hit_rate_vs_spy":
                    round(sum(1 for v in ex if v > 0) / len(ex), 3)
                    if ex else None,
                "mean_excess_vs_spy_pct":
                    round(sum(ex) / len(ex), 2) if ex else None,
                "median_excess_vs_spy_pct":
                    round(statistics.median(ex), 2) if ex else None,
                "mean_mae_pct":
                    round(sum(maes) / len(maes), 2) if maes else None,
                "worst_mae_pct": round(min(maes), 2) if maes else None,
                "n_with_mae": len(maes),
            }
        split[name] = block
    return split


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
    # A return too large to be a price move is a broken bar, and a broken
    # entry price discredits the whole row, not one horizon of it. These are
    # quarantined from every statistic below and listed in the output.
    entries, price_artifacts = split_price_artifacts(entries, HORIZONS)
    matured = [e for e in entries if e.get("ret_10d") is not None]
    matured_5d = [e for e in entries if e.get("ret_5d") is not None]
    n = len(matured)
    # Phase 5.1 (A3): re-appearances of the same ticker are overlapping
    # observations, not independent evidence — sample status is graded on
    # UNIQUE matured tickers, with the raw row count kept for transparency.
    n_unique = len({e.get("ticker") for e in matured})
    n_dates = len({e.get("appearance_date") for e in matured})
    status = _sample_status(n_unique)
    matured_by_horizon = {
        f"{h}d": sum(1 for e in entries if e.get(f"ret_{h}d") is not None)
        for h in HORIZONS}

    # No bucket verdict until at least PROVISIONAL sample threshold of
    # UNIQUE matured tickers (Phase 5.1: was raw rows, which overstated
    # the sample by the duplication factor)
    if n_unique < SAMPLE_THRESHOLD_PROVISIONAL:
        return {
            "bucket": bucket_name,
            "total_entries": len(entries),
            "matured_5d_entries": len(matured_5d),
            "matured_entries": n,
            "matured_unique_tickers": n_unique,
            "matured_distinct_dates": n_dates,
            "matured_by_horizon": matured_by_horizon,
            "sample_basis": "unique_tickers",
            "sample_status": status,
            "price_artifacts_excluded": len(price_artifacts),
            "price_artifacts": price_artifacts[:10],
            "robust": {},
            "per_ticker": {},
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
    avg_vs_iwm, med_vs_iwm, wr_vs_iwm, n_iwm = _bench_stats("ret_10d_vs_iwm")

    # Published beside the row-weighted figures, never instead of them:
    # `robust` says what the typical row did once a skewed tail is pulled
    # in, `per_ticker` says what the typical *name* did, which is the
    # sample any claim about the surface actually rests on.
    robust = {
        "ret_10d": robust_stats([e["ret_10d"] for e in matured]),
        "vs_spy": robust_stats([e["ret_10d_vs_spy"] for e in matured
                                if e.get("ret_10d_vs_spy") is not None]),
    }
    per_ticker = {
        "ret_10d": per_entity_stats(matured, "ret_10d"),
        "vs_spy": per_entity_stats(matured, "ret_10d_vs_spy"),
    }

    return {
        "bucket": bucket_name,
        "total_entries": len(entries),
        "matured_5d_entries": len(matured_5d),
        "matured_entries": n,
        "matured_unique_tickers": n_unique,
        "matured_distinct_dates": n_dates,
        "matured_by_horizon": matured_by_horizon,
        "sample_basis": "unique_tickers",
        "sample_status": status,
        "price_artifacts_excluded": len(price_artifacts),
        "price_artifacts": price_artifacts[:10],
        "robust": robust,
        "per_ticker": per_ticker,
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
        "win_rate_vs_iwm": wr_vs_iwm,
        "avg_ret_vs_iwm": avg_vs_iwm,
        "median_ret_vs_iwm": med_vs_iwm,
        "n_with_iwm_baseline": n_iwm,
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
    n_iwm = sum(1 for r in all_recs if r.get("iwm_ret_10d") is not None)
    n_with_etf = sum(1 for r in all_recs if r.get("benchmark_sector_etf"))
    n_sec = sum(1 for r in all_recs if r.get("sector_ret_10d") is not None)
    return {
        "spy_available": True,
        "qqq_available": True,
        "iwm_available": True,
        "total_entries": total,
        "entries_with_spy_10d": n_spy,
        "entries_with_qqq_10d": n_qqq,
        "entries_with_iwm_10d": n_iwm,
        "entries_with_sector_etf": n_with_etf,
        "entries_with_sector_10d": n_sec,
        "benchmark_schema_version": BENCHMARK_SCHEMA_VERSION,
    }


def _market_cap_bucket(value: Any) -> str:
    try:
        mc = float(value)
    except Exception:
        return "UNKNOWN"
    if mc < 300_000_000:
        return "MICRO_CAP"
    if mc < 2_000_000_000:
        return "SMALL_CAP"
    if mc < 10_000_000_000:
        return "MID_CAP"
    if mc < 200_000_000_000:
        return "LARGE_CAP"
    return "MEGA_CAP"


def _security_type(ticker: str) -> str:
    t = str(ticker or "").upper()
    if t.endswith(("WS", "WT")) or (len(t) >= 5 and t.endswith("W")):
        return "POSSIBLE_WARRANT"
    if len(t) >= 5 and t.endswith(("U", "R")):
        return "POSSIBLE_UNIT_OR_RIGHT"
    if "." in t or "-" in t:
        return "UNSUPPORTED_SHARE_CLASS_FORMAT"
    return "COMMON_STOCK_OR_ADR"


def _is_invalid_ticker(ticker: str) -> bool:
    t = str(ticker or "").strip().upper()
    if not t or len(t) > 12:
        return True
    return not all(ch.isalnum() or ch in {".", "-"} for ch in t)


def _last_price_date(closes_with_dates: List[tuple]) -> Optional[str]:
    return closes_with_dates[-1][0] if closes_with_dates else None


def _expected_maturity_date(spy_dates: List[str], appearance_date: str,
                            horizon: int) -> Optional[str]:
    idx = None
    for i, d in enumerate(spy_dates):
        if d >= appearance_date:
            idx = i
            break
    if idx is None or idx + horizon >= len(spy_dates):
        return None
    return spy_dates[idx + horizon]


def _is_calendar_mature(spy_dates: List[str], appearance_date: str,
                        horizon: int) -> bool:
    return _expected_maturity_date(spy_dates, appearance_date, horizon) is not None


def _unresolved_reason(closes_with_dates: List[tuple], appearance_date: str,
                       horizon: int) -> str:
    if not appearance_date:
        return "appearance_date_missing"
    if not closes_with_dates:
        return "price_parquet_missing_or_empty"
    idx = None
    for i, (d, _) in enumerate(closes_with_dates):
        if d >= appearance_date:
            idx = i
            break
    if idx is None:
        return "price_history_ends_before_appearance"
    if idx + horizon >= len(closes_with_dates):
        return "missing_horizon_bar"
    return "resolver_not_filled_or_invalid_price"


def _classify_unresolved(
    ticker: str,
    raw_reason: str,
    price_cache: Dict[str, Any],
    appearance_date: str,
    expected_date: Optional[str],
    horizon: int,
    backfill: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str]:
    """Map resolver failure mechanics into operator-facing reason buckets."""
    t = str(ticker or "").upper()
    security_type = _security_type(t)
    backfill = backfill or {}
    backfill_text = " ".join(str(backfill.get(k) or "") for k in
                              ("action", "reason", "fetch_result")).lower()

    if _is_invalid_ticker(t):
        return REASON_INVALID_TICKER, INVALID_OR_DELISTED
    if "symbol" in backfill_text and "change" in backfill_text:
        return REASON_SYMBOL_CHANGED, NEEDS_MAPPING
    if security_type == "UNSUPPORTED_SHARE_CLASS_FORMAT":
        return REASON_TICKER_MAPPING, NEEDS_MAPPING
    if security_type in NON_COMMON_SECURITY_TYPES:
        return REASON_NON_COMMON_SECURITY, NON_COMMON_SECURITY
    if t.endswith("Q") or "delist" in backfill_text or "not found" in backfill_text:
        return REASON_DELISTED_CORPORATE_ACTION, INVALID_OR_DELISTED
    if backfill.get("action") == "failed":
        return REASON_PROVIDER_GAP, PROVIDER_GAP
    if backfill.get("action") == "skip_source_exhausted":
        return REASON_INSUFFICIENT_HISTORY, PROVIDER_GAP
    if backfill.get("action") == "backfilled":
        before = backfill.get("bars_before") or backfill.get("before") or 0
        after = backfill.get("bars_after") or backfill.get("after") or 0
        if after <= before:
            return REASON_PROVIDER_GAP, PROVIDER_GAP

    last_date = price_cache.get("last_date")
    if backfill.get("action") == "backfilled" and expected_date             and last_date and str(last_date) < str(expected_date):
        return REASON_PROVIDER_GAP, PROVIDER_GAP
    bar_count = int(price_cache.get("bar_count") or 0)
    if raw_reason == "price_parquet_missing_or_empty":
        return REASON_PROVIDER_GAP, PROVIDER_GAP
    if raw_reason == "price_history_ends_before_appearance":
        return REASON_STALE_PRICE_HISTORY, REPAIRABLE
    if raw_reason == "missing_horizon_bar":
        if last_date and expected_date and str(last_date) < str(expected_date):
            return REASON_STALE_PRICE_HISTORY, REPAIRABLE
        if bar_count <= horizon:
            return REASON_INSUFFICIENT_HISTORY, REPAIRABLE
        return REASON_MISSING_PRICE_BAR, REPAIRABLE
    return REASON_UNRESOLVED_UNKNOWN, UNKNOWN_REPAIRABILITY


def _date_bucket(value: Optional[str]) -> str:
    if not value:
        return "UNKNOWN"
    return str(value)[:7]


def _count(rows: List[Dict[str, Any]], field: str) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for row in rows:
        val = row.get(field) or "UNKNOWN"
        if isinstance(val, list):
            vals = val or ["UNKNOWN"]
        else:
            vals = [val]
        for v in vals:
            key = str(v or "UNKNOWN")
            out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items()))


def _load_json_lines(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def _load_lane_membership() -> Dict[str, List[str]]:
    memberships: Dict[str, List[str]] = {}
    for path, lane in (
        (DATA_DIR / "high_conviction_history.jsonl", "HIGH_CONVICTION"),
        (DATA_DIR / "emerging_outlier_history.jsonl", "EMERGING_OUTLIER"),
    ):
        for row in _load_json_lines(path):
            ticker = str(row.get("ticker") or "").upper()
            date = str(row.get("appearance_date") or "")[:10]
            if ticker and date:
                memberships.setdefault(f"{ticker}|{date}", []).append(lane)
    return memberships


def _load_backfill_by_ticker() -> Dict[str, Dict[str, Any]]:
    path = RESEARCH_DIR / "targeted_price_backfill_latest.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for entry in doc.get("entries") or doc.get("plan") or doc.get("results") or []:
        ticker = str(entry.get("ticker") or "").upper()
        if ticker:
            out[ticker] = entry
    return out


def _membership_label(ticker: str, appearance_date: str,
                      memberships: Optional[Dict[str, List[str]]]) -> str:
    lanes = (memberships or {}).get(f"{ticker}|{appearance_date}") or []
    if not lanes:
        return "BROAD_SCANNER"
    return "+".join(sorted(set(lanes)))


def _source_lane(rec: Dict[str, Any]) -> str:
    return (rec.get("priority_label") or rec.get("category")
            or rec.get("watchlist_label") or rec.get("universe_source")
            or rec.get("source") or "UNKNOWN")


def _resolution_coverage(
    history: Dict[str, Dict[str, Any]],
    spy_closes: List[tuple],
    lane_membership: Optional[Dict[str, List[str]]] = None,
    backfill_by_ticker: Optional[Dict[str, Dict[str, Any]]] = None,
    price_cache_loader=None,
) -> Dict[str, Any]:
    """Quantify silent resolution attrition by horizon.

    An entry whose appearance date plus the horizon fits inside the SPY
    trading calendar SHOULD have a resolved return. Unresolved mature rows
    are a survivorship hole, so each horizon carries full unresolved cohort
    detail and a warning when coverage falls below the configured floor.
    """
    spy_dates = [d for d, _ in spy_closes]
    out: Dict[str, Any] = {}
    cache_loader = price_cache_loader or _load_price_cache
    price_cache: Dict[str, Dict[str, Any]] = {}
    source_artifact = str(HISTORY_JSONL.relative_to(ROOT))
    lane_membership = lane_membership or {}
    backfill_by_ticker = backfill_by_ticker or {}

    for h in HORIZONS:
        expected = 0
        resolved = 0
        unresolved_details: List[Dict[str, Any]] = []

        for rec in history.values():
            date = rec.get("appearance_date") or ""
            expected_date = _expected_maturity_date(spy_dates, date, h)
            if expected_date is None:
                continue  # not calendar-mature yet
            expected += 1
            if rec.get(f"ret_{h}d") is not None:
                resolved += 1
                continue

            ticker = str(rec.get("ticker") or "").upper()
            if ticker not in price_cache:
                price_cache[ticker] = cache_loader(ticker)
            cache_info = price_cache[ticker]
            closes = cache_info.get("closes") or []
            raw_reason = _unresolved_reason(closes, date, h)
            reason_category, repairability = _classify_unresolved(
                ticker=ticker,
                raw_reason=raw_reason,
                price_cache=cache_info,
                appearance_date=date,
                expected_date=expected_date,
                horizon=h,
                backfill=backfill_by_ticker.get(ticker),
            )
            security_type = _security_type(ticker)
            market_cap_bucket = _market_cap_bucket(rec.get("market_cap"))
            sector = rec.get("sector") or "UNKNOWN"
            source_lane = _source_lane(rec)
            membership = _membership_label(ticker, date, lane_membership)

            unresolved_details.append({
                "ticker": ticker,
                "first_seen_date": date,
                "appearance_date": date,
                "expected_maturity_date": expected_date,
                "horizon": f"{h}d",
                "reason": raw_reason,
                "reason_category": reason_category,
                "repairability": repairability,
                "security_type": security_type,
                "market_cap_bucket": market_cap_bucket,
                "market_cap": rec.get("market_cap"),
                "sector": sector,
                "industry": rec.get("industry"),
                "watchlist_label": rec.get("watchlist_label"),
                "category": rec.get("category"),
                "research_program": rec.get("research_program") or "UNROUTED",
                "source_lane": source_lane,
                "membership_lane": membership,
                "date_bucket": _date_bucket(date),
                "source_artifact": source_artifact,
                "last_available_price_date": cache_info.get("last_date"),
                "price_last_date": cache_info.get("last_date"),
                "price_first_date": cache_info.get("first_date"),
                "price_cache_source": cache_info.get("cache_source"),
                "price_bar_count": cache_info.get("bar_count"),
                "shallow_last_date": cache_info.get("shallow_last_date"),
                "deep_last_date": cache_info.get("deep_last_date"),
                "backfill_action": (backfill_by_ticker.get(ticker) or {}).get("action"),
            })

        unresolved = expected - resolved
        coverage = round(resolved / expected * 100, 1) if expected else None
        unresolved_pct = round(unresolved / expected * 100, 1) if expected else None
        status = "NOT_MATURE"
        warning = None
        incomplete_warning = None
        if coverage is not None:
            status = "OK" if coverage >= MIN_RESOLUTION_COVERAGE_PCT else "WARN"
            if status == "WARN":
                warning = (
                    f"{h}d resolution coverage {coverage}% below "
                    f"{MIN_RESOLUTION_COVERAGE_PCT:.0f}% floor; evidence at "
                    "this horizon is not clean."
                )
                incomplete_warning = (
                    f"Forward evidence incomplete — {h}d resolution {coverage}%; "
                    "unresolved cohort may bias results."
                )
        out[f"{h}d"] = {
            "calendar_mature": expected,
            "expected_mature_count": expected,
            "resolved": resolved,
            "resolved_count": resolved,
            "unresolved": unresolved,
            "unresolved_count": unresolved,
            "unresolved_pct": unresolved_pct,
            "coverage_pct": coverage,
            "resolution_pct": coverage,
            "resolution_status": status,
            "warning": warning,
            "incomplete_evidence_warning": incomplete_warning,
            "min_resolution_coverage_pct": MIN_RESOLUTION_COVERAGE_PCT,
            "unresolved_tickers": [d["ticker"] for d in unresolved_details],
            "unresolved_tickers_sample": [d["ticker"] for d in unresolved_details[:10]],
            "unresolved_detail": unresolved_details,
            "reason_counts": _count(unresolved_details, "reason"),
            "reason_category_counts": _count(unresolved_details, "reason_category"),
            "repairability_counts": _count(unresolved_details, "repairability"),
            "security_type_counts": _count(unresolved_details, "security_type"),
            "market_cap_bucket_counts": _count(unresolved_details, "market_cap_bucket"),
            "source_counts": _count(unresolved_details, "source_lane"),
            "source_lane_counts": _count(unresolved_details, "source_lane"),
            "program_counts": _count(unresolved_details, "research_program"),
            "membership_lane_counts": _count(unresolved_details, "membership_lane"),
            "sector_counts": _count(unresolved_details, "sector"),
            "date_bucket_counts": _count(unresolved_details, "date_bucket"),
            "repair_recommendation": (
                "Run targeted price backfill for REPAIRABLE mature episodes, "
                "create mapping tickets for NEEDS_MAPPING rows, and keep "
                "non-common securities in the audit trail but out of clean "
                "common-equity evidence views."
                if unresolved else None
            ),
        }
    return out


def _incomplete_evidence_warnings(resolution_coverage: Dict[str, Any]) -> List[str]:
    warnings: List[str] = []
    for h, blk in resolution_coverage.items():
        msg = blk.get("incomplete_evidence_warning")
        if msg:
            warnings.append(msg)
    return warnings


def _bias_analysis(resolution_coverage: Dict[str, Any]) -> Dict[str, Any]:
    by_horizon: Dict[str, Any] = {}
    all_details: List[Dict[str, Any]] = []
    for h, blk in resolution_coverage.items():
        details = blk.get("unresolved_detail") or []
        all_details.extend(details)
        by_horizon[h] = {
            "unresolved_count": len(details),
            "by_market_cap_bucket": _count(details, "market_cap_bucket"),
            "by_sector": _count(details, "sector"),
            "by_security_type": _count(details, "security_type"),
            "by_source_lane": _count(details, "source_lane"),
            "by_program": _count(details, "research_program"),
            "by_membership_lane": _count(details, "membership_lane"),
            "by_date_bucket": _count(details, "date_bucket"),
        }
    return {
        "aggregate_unresolved_rows": len(all_details),
        "aggregate": {
            "by_market_cap_bucket": _count(all_details, "market_cap_bucket"),
            "by_sector": _count(all_details, "sector"),
            "by_security_type": _count(all_details, "security_type"),
            "by_source_lane": _count(all_details, "source_lane"),
            "by_program": _count(all_details, "research_program"),
            "by_membership_lane": _count(all_details, "membership_lane"),
            "by_date_bucket": _count(all_details, "date_bucket"),
            "by_reason_category": _count(all_details, "reason_category"),
            "by_repairability": _count(all_details, "repairability"),
        },
        "by_horizon": by_horizon,
    }


def _mapping_needed_report(resolution_coverage: Dict[str, Any]) -> Dict[str, Any]:
    rows: Dict[str, Dict[str, Any]] = {}
    for blk in resolution_coverage.values():
        for detail in blk.get("unresolved_detail") or []:
            if detail.get("repairability") != NEEDS_MAPPING:
                continue
            ticker = detail.get("ticker")
            rows.setdefault(ticker, {
                "ticker": ticker,
                "reason_category": detail.get("reason_category"),
                "security_type": detail.get("security_type"),
                "first_seen_dates": [],
                "horizons": [],
                "source_artifacts": [],
                "mapping_status": "NEEDS_MAPPING",
                "note": "No reliable existing mapping was applied; operator review required.",
            })
            row = rows[ticker]
            row["first_seen_dates"].append(detail.get("first_seen_date"))
            row["horizons"].append(detail.get("horizon"))
            row["source_artifacts"].append(detail.get("source_artifact"))
    for row in rows.values():
        row["first_seen_dates"] = sorted(set(row["first_seen_dates"]))
        row["horizons"] = sorted(set(row["horizons"]))
        row["source_artifacts"] = sorted(set(row["source_artifacts"]))
    return {"count": len(rows), "tickers": sorted(rows.values(), key=lambda r: r["ticker"] or "")}


def _horizon_evidence_stats(entries: List[Dict[str, Any]], spy_dates: List[str],
                            horizon: int) -> Dict[str, Any]:
    expected = [e for e in entries
                if _is_calendar_mature(spy_dates, str(e.get("appearance_date") or ""), horizon)]
    resolved = [e for e in expected if e.get(f"ret_{horizon}d") is not None]
    rets = [float(e[f"ret_{horizon}d"]) for e in resolved]
    wins = sum(1 for v in rets if v > 0)
    unresolved = len(expected) - len(resolved)
    coverage = round(len(resolved) / len(expected) * 100, 1) if expected else None
    return {
        "expected_mature_count": len(expected),
        "resolved_count": len(resolved),
        "unresolved_count": unresolved,
        "resolution_pct": coverage,
        "positive_count": wins,
        "win_rate": round(wins / len(resolved), 3) if resolved else None,
        "mean_return_pct": round(sum(rets) / len(rets), 2) if rets else None,
        "median_return_pct": round(statistics.median(rets), 2) if rets else None,
        "primary_evidence_basis": "resolved_price_bars_only",
        "unresolved_not_filled": True,
    }


def _sensitivity(stats: Dict[str, Any]) -> Dict[str, Any]:
    expected = int(stats.get("expected_mature_count") or 0)
    unresolved = int(stats.get("unresolved_count") or 0)
    wins = int(stats.get("positive_count") or 0)
    if expected <= 0:
        return {
            "expected_mature_count": 0,
            "unresolved_count": 0,
            "win_rate_range_if_unresolved_extreme": None,
            "mean_return_sensitivity": "not_estimated_without_prices",
        }
    return {
        "expected_mature_count": expected,
        "unresolved_count": unresolved,
        "observed_resolved_win_rate": stats.get("win_rate"),
        "win_rate_if_all_unresolved_losses": round(wins / expected, 3),
        "win_rate_if_all_unresolved_wins": round((wins + unresolved) / expected, 3),
        "mean_return_sensitivity": "not_estimated_without_prices",
        "note": "Unresolved returns are not fabricated or forward-filled.",
    }


def _evidence_views(entries: List[Dict[str, Any]], spy_closes: List[tuple]) -> Dict[str, Any]:
    spy_dates = [d for d, _ in spy_closes]
    common_entries = [e for e in entries
                      if _security_type(str(e.get("ticker") or "")) == "COMMON_STOCK_OR_ADR"]
    non_common_entries = [e for e in entries
                          if _security_type(str(e.get("ticker") or "")) != "COMMON_STOCK_OR_ADR"]
    raw = {f"{h}d": _horizon_evidence_stats(entries, spy_dates, h) for h in HORIZONS}
    common = {f"{h}d": _horizon_evidence_stats(common_entries, spy_dates, h) for h in HORIZONS}
    resolved_only = {}
    for h, stats in raw.items():
        resolved_only[h] = {
            "resolved_count": stats["resolved_count"],
            "win_rate": stats["win_rate"],
            "mean_return_pct": stats["mean_return_pct"],
            "median_return_pct": stats["median_return_pct"],
            "primary_evidence_basis": "resolved_price_bars_only",
            "warning": "Resolved-only stats omit unresolved mature rows; use raw sensitivity before judging alpha.",
        }
    return {
        "raw_evidence": {
            "description": "All mature research episodes; unresolved rows remain visible in denominator and warnings.",
            "horizons": raw,
        },
        "clean_resolved_only_evidence": {
            "description": "Stats computed only from rows with real resolved price bars. This is not proof of better alpha.",
            "horizons": resolved_only,
        },
        "non_common_security_adjusted_evidence": {
            "description": "Common-stock/ADR rows only; non-common securities are preserved in audit detail, not deleted.",
            "excluded_episode_count": len(non_common_entries),
            "excluded_unique_tickers": sorted({str(e.get("ticker") or "").upper() for e in non_common_entries}),
            "horizons": common,
        },
        "unresolved_impact_sensitivity": {
            h: _sensitivity(stats) for h, stats in raw.items()
        },
        "guardrails": {
            "no_fabricated_prices": True,
            "no_forward_fill_primary_evidence": True,
            "clean_views_are_diagnostic_not_alpha_claims": True,
        },
    }


def _resolution_repair_summary(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    by_horizon: Dict[str, Any] = {}
    for h in after:
        b = before.get(h) or {}
        a = after.get(h) or {}
        by_horizon[h] = {
            "before_resolved": b.get("resolved", 0),
            "after_resolved": a.get("resolved", 0),
            "resolved_delta": (a.get("resolved", 0) or 0) - (b.get("resolved", 0) or 0),
            "before_unresolved": b.get("unresolved", 0),
            "after_unresolved": a.get("unresolved", 0),
            "unresolved_delta": (a.get("unresolved", 0) or 0) - (b.get("unresolved", 0) or 0),
            "before_resolution_pct": b.get("coverage_pct"),
            "after_resolution_pct": a.get("coverage_pct"),
            "repair_source": "local_deep_plus_shallow_cache_merge",
        }
    return {"by_horizon": by_horizon}


def _repairable_tickers(resolution_coverage: Dict[str, Any]) -> List[str]:
    tickers: Set[str] = set()
    for blk in resolution_coverage.values():
        for detail in blk.get("unresolved_detail") or []:
            if detail.get("repairability") == REPAIRABLE:
                ticker = str(detail.get("ticker") or "").upper()
                if ticker:
                    tickers.add(ticker)
    return sorted(tickers)


def _build_forward_backfill_plan(
    resolution_coverage: Dict[str, Any],
    execute: bool = False,
    max_provider_calls: int = 0,
    min_bars: int = 300,
) -> Dict[str, Any]:
    tickers = _repairable_tickers(resolution_coverage)
    if not tickers:
        return {
            "present": True,
            "mode": "NONE_NEEDED",
            "research_only": True,
            "repairable_tickers": [],
            "selected_for_backfill": 0,
            "provider_calls_planned": 0,
            "provider_calls_used": 0,
            "successes": 0,
            "failures": 0,
            "entries": [],
        }
    from research import targeted_price_backfill as tpb

    call_cap = max_provider_calls if execute else max(len(tickers), 1)
    plan = tpb.build_plan(
        min_bars=min_bars,
        limit=len(tickers),
        max_provider_calls=call_cap,
        force_refresh=True,
        tickers=tickers,
    )
    if execute:
        plan = tpb.execute_plan(plan)
        try:
            tpb.write_artifacts(plan, dry_run=False)
        except Exception:
            pass
    return {
        "present": True,
        "mode": "EXECUTE" if execute else "DRY_RUN_PLAN",
        "research_only": True,
        "repairable_tickers": tickers,
        "selected_for_backfill": plan.get("selected_for_backfill", 0),
        "provider_calls_planned": plan.get("provider_calls_planned", 0),
        "provider_calls_used": plan.get("provider_calls_used", 0),
        "successes": plan.get("successes", 0),
        "failures": plan.get("failures", 0),
        "entries": plan.get("entries") or [],
        "rerun_required_after_execute": bool(execute and plan.get("provider_calls_used", 0)),
        "source": "research.targeted_price_backfill.build_plan",
    }


def run_forward_tracker(execute_repair_backfill: bool = False,
                        max_repair_provider_calls: int = 0) -> Dict[str, Any]:
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
            label = item.get("watchlist_label")
            route = classify_label(label or "")
            rec: Dict[str, Any] = {
                "ticker": ticker,
                "appearance_date": today,
                "sector": sector,
                "industry": industry,
                "market_cap": item.get("market_cap"),
                "benchmark_sector_etf": sector_etf,
                "watchlist_label": label,
                "category": item.get("category"),
                "research_score": item.get("research_score"),
                "earliness_label": item.get("earliness_label"),
                "consensus_label": item.get("consensus_label"),
                # Phase 5.1 routing: every candidate carries its program
                "research_program": route.get("program"),
                "program_holding_period_td":
                    route.get("expected_holding_period_td"),
                "program_confidence": route.get("confidence"),
                **{f"ret_{h}d": None for h in HORIZONS},
                "resolved": False,
            }
            history[key] = rec
            new_entries.append(rec)

    # Backfill program routing for entries that predate Phase 5.1 (same
    # label -> program mapping; additive, never overwrites)
    for rec in history.values():
        if not rec.get("research_program"):
            route = classify_label(rec.get("watchlist_label") or "")
            rec["research_program"] = route.get("program")
            rec["program_holding_period_td"] = \
                route.get("expected_holding_period_td")
            rec["program_confidence"] = route.get("confidence")

    # Priority stamping (next-run backfill): the alpha radar runs AFTER
    # the tracker each night, so entries recorded tonight get their
    # priority bucket stamped on the next run, when the radar sidecar's
    # date matches their appearance_date.  Older rows without a matching
    # radar snapshot stay unstamped and are reported as such.
    radar_map: Dict[str, str] = {}
    radar_date: Optional[str] = None
    radar_path = RESEARCH_DIR / "daily_alpha_radar_latest.json"
    if radar_path.exists():
        try:
            radar = json.loads(radar_path.read_text(encoding="utf-8"))
            radar_date = str(radar.get("generated_at") or "")[:10]
            for bucket, tickers in (radar.get("priority_tickers")
                                    or {}).items():
                for t in tickers or []:
                    radar_map.setdefault(t, bucket)
        except Exception:
            radar_map, radar_date = {}, None
    if radar_map and radar_date:
        for rec in history.values():
            if rec.get("priority_label") is None \
                    and rec.get("appearance_date") == radar_date \
                    and rec["ticker"] in radar_map:
                rec["priority_label"] = radar_map[rec["ticker"]]
                rec["priority_source"] = "daily_alpha_radar"

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

    # Preload benchmark closes — reused across all entries.  IWM added in
    # Phase 5.1 (A5): small/mid-cap picks were judged only against
    # large-cap indices, which flips the sign of measured excess.
    benchmark_closes: Dict[str, List[tuple]] = {}
    for bm in ("SPY", "QQQ", "IWM"):
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
    iwm_closes = benchmark_closes.get("IWM", [])

    lane_membership = _load_lane_membership()
    backfill_by_ticker = _load_backfill_by_ticker()
    resolution_before_repair = _resolution_coverage(
        history, spy_closes, lane_membership, backfill_by_ticker)

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

        price_info = None
        closes: List[tuple] = []
        updated = False

        for h in HORIZONS:
            ret_field = f"ret_{h}d"

            # Compute ticker return if not yet available. Existing resolved
            # returns are never recomputed; this only fills missing evidence
            # from real local cached bars.
            if rec.get(ret_field) is None:
                if price_info is None:
                    price_info = _load_price_cache(ticker)
                    closes = price_info.get("closes") or []
                ret = _forward_return(closes, date, h)
                if ret is not None:
                    rec[ret_field] = ret
                    rec[f"ret_{h}d_price_source"] = price_info.get("cache_source")
                    rec[f"ret_{h}d_price_last_date"] = price_info.get("last_date")
                    rec["local_cache_resolution_repaired_at"] = now
                    updated = True

            # Post-selection max adverse excursion (same window rule)
            mae_field = f"mae_{h}d"
            if rec.get(mae_field) is None                     and rec.get(ret_field) is not None:
                if price_info is None:
                    price_info = _load_price_cache(ticker)
                    closes = price_info.get("closes") or []
                mae = _forward_mae(closes, date, h)
                if mae is not None:
                    rec[mae_field] = mae
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

                iwm_field = f"iwm_ret_{h}d"
                if rec.get(iwm_field) is None:
                    iwm_ret = _forward_return(iwm_closes, date, h)
                    if iwm_ret is not None:
                        rec[iwm_field] = iwm_ret
                        rec[f"ret_{h}d_vs_iwm"] = round(ret_val - iwm_ret, 2)
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
            iwm_complete = all(rec.get(f"iwm_ret_{h}d") is not None for h in horizons_with_ticker)
            if spy_complete and qqq_complete and iwm_complete:
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
    resolution_coverage = _resolution_coverage(
        history, spy_closes, lane_membership, backfill_by_ticker)
    resolution_warnings = [
        blk["warning"] for blk in resolution_coverage.values()
        if blk.get("warning")
    ]
    incomplete_evidence_warnings = _incomplete_evidence_warnings(
        resolution_coverage)
    weak_blocks = [b for b in resolution_coverage.values()
                   if b.get("incomplete_evidence_warning")]
    weakest = min(weak_blocks, key=lambda b: b.get("coverage_pct") or 0)         if weak_blocks else None
    incomplete_evidence_warning = (
        weakest.get("incomplete_evidence_warning") if weakest else None)
    unresolved_repair_recommendation = (
        "Run targeted price backfill for REPAIRABLE mature episodes, then "
        "rerun research-forward-tracker; do not treat weak-resolution "
        "horizons as clean evidence until coverage recovers."
        if resolution_warnings else None
    )
    resolution_repair_summary = _resolution_repair_summary(
        resolution_before_repair, resolution_coverage)
    unresolved_bias_analysis = _bias_analysis(resolution_coverage)
    mapping_needed_report = _mapping_needed_report(resolution_coverage)
    evidence_views = _evidence_views(all_entries, spy_closes)
    forward_backfill_plan = _build_forward_backfill_plan(
        resolution_coverage,
        execute=execute_repair_backfill,
        max_provider_calls=max_repair_provider_calls,
    )

    # Per-program rollup (routing only — program VERDICTS come from the
    # pre-registered gates in research/research_programs.py, not here)
    program_counts: Dict[str, Dict[str, int]] = {}
    for rec in all_entries:
        prog = rec.get("research_program") or "UNROUTED"
        pc = program_counts.setdefault(
            prog, {"entries": 0, "unique_tickers": 0})
        pc["entries"] += 1
    for prog in program_counts:
        pc_tickers = {r["ticker"] for r in all_entries
                      if (r.get("research_program") or "UNROUTED") == prog}
        program_counts[prog]["unique_tickers"] = len(pc_tickers)

    out: Dict[str, Any] = {
        "version": VERSION,
        "generated_at": now,
        "system_mode": SYSTEM_MODE,
        "research_only": True,
        "total_history_entries": len(history),
        "price_cache_hygiene": {
            "non_session_bars_dropped": {
                t: len(dates) for t, dates in
                sorted(NON_SESSION_BARS_DROPPED.items())},
            "tickers_affected": len(NON_SESSION_BARS_DROPPED),
            "note": "Weekend bars in a daily equity series belong to another "
                    "instrument. They are ignored when resolving forward "
                    "returns; the parquet itself still needs a refetch.",
        },
        "new_entries_today": len(new_entries),
        "updated_entries_today": resolved_count,
        "overall": overall_verdict,
        "verdicts_by_label": verdicts,
        "benchmark_readiness": bench_readiness,
        "resolution_before_repair": resolution_before_repair,
        "resolution_coverage": resolution_coverage,
        "resolution_repair_summary": resolution_repair_summary,
        "resolution_warnings": resolution_warnings,
        "incomplete_evidence_warnings": incomplete_evidence_warnings,
        "incomplete_evidence_warning": incomplete_evidence_warning,
        "unresolved_repair_recommendation": unresolved_repair_recommendation,
        "unresolved_bias_analysis": unresolved_bias_analysis,
        "mapping_needed_report": mapping_needed_report,
        "evidence_views": evidence_views,
        "forward_backfill_repair_plan": forward_backfill_plan,
        "program_counts": program_counts,
        "priority_split": _priority_split(all_entries),
        "tracked_horizons_td": HORIZONS,
        "guardrails": {
            "no_trade_recommendation": True,
            "forward_returns_are_research_only": True,
            "past_performance_not_predictive": True,
            "no_fabricated_prices": True,
            "no_forward_fill_primary_evidence": True,
            "no_scanner_scoring_or_routing_change": True,
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
                 f"(matured_5d={overall.get('matured_5d_entries', 0)}, "
                 f"matured_10d={overall.get('matured_entries', 0)}, "
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

    rc = result.get("resolution_coverage") or {}
    if rc:
        lines += ["", "=== RESOLUTION COVERAGE (survivorship guard) ==="]
        for h, blk in rc.items():
            cov = blk.get("coverage_pct")
            lines.append(
                f"  {h:>4}: {blk.get('resolved', 0)}/"
                f"{blk.get('calendar_mature', 0)} resolved "
                f"({cov if cov is not None else 'n/a'}%) - "
                f"{blk.get('unresolved', 0)} unresolved "
                f"[{blk.get('resolution_status') or 'UNKNOWN'}]"
            )
            if blk.get("warning"):
                lines.append(f"       warning: {blk['warning']}")
            cats = blk.get("reason_category_counts") or {}
            if cats:
                lines.append(f"       reasons: {cats}")

    incomplete = result.get("incomplete_evidence_warnings") or []
    if incomplete:
        lines += ["", "=== INCOMPLETE EVIDENCE WARNINGS ==="]
        lines.extend(f"  {w}" for w in incomplete)

    repairs = (result.get("resolution_repair_summary") or {}).get("by_horizon") or {}
    if repairs:
        lines += ["", "=== LOCAL CACHE REPAIR DELTA ==="]
        for h, blk in repairs.items():
            if blk.get("resolved_delta") or blk.get("unresolved_delta"):
                lines.append(
                    f"  {h}: resolved_delta={blk.get('resolved_delta')} "
                    f"unresolved_delta={blk.get('unresolved_delta')} "
                    f"source={blk.get('repair_source')}"
                )

    pc = result.get("program_counts") or {}
    if pc:
        lines += ["", "=== RESEARCH PROGRAM ROUTING ==="]
        for prog, blk in sorted(pc.items()):
            lines.append(f"  {prog:<12} entries={blk['entries']:5d}  "
                         f"unique_tickers={blk['unique_tickers']}")

    ps = result.get("priority_split") or {}
    if ps:
        lines += ["", "=== PRIORITY SPLIT (hit-rate vs SPY | mean/med ret "
                      "| mean/worst MAE) ==="]
        for name, blk in ps.items():
            if not blk.get("horizons"):
                lines.append(f"  {name:<14} entries={blk.get('entries', 0)}"
                             "  (no matured evidence)")
                continue
            lines.append(f"  {name:<14} entries={blk['entries']}  "
                         f"unique={blk['unique_tickers']}")
            for h, st in blk["horizons"].items():
                lines.append(
                    f"    {h:>4}: n={st['n']:3d}  "
                    f"hit_vs_spy={st['hit_rate_vs_spy'] if st['hit_rate_vs_spy'] is not None else 'n/a'}  "
                    f"ret={st['mean_ret_pct']:+.2f}/{st['median_ret_pct']:+.2f}%  "
                    f"mae={st['mean_mae_pct'] if st['mean_mae_pct'] is not None else 'n/a'}"
                    f"/{st['worst_mae_pct'] if st['worst_mae_pct'] is not None else 'n/a'}%"
                    f" (mae n={st['n_with_mae']})")

    lines += ["", "=== BY LABEL BUCKET ==="]
    for v in result.get("verdicts_by_label", []):
        abs_str = f"win%={v.get('win_rate_10d') or 'n/a'}  mean10d={v.get('mean_ret_10d') or 'n/a'}"
        vs_spy = v.get("avg_ret_vs_spy")
        spy_str = f"  vs_SPY={vs_spy:+.2f}%" if vs_spy is not None else ""
        vs_qqq = v.get("avg_ret_vs_qqq")
        qqq_str = f"  vs_QQQ={vs_qqq:+.2f}%" if vs_qqq is not None else ""
        lines.append(
            f"  {v['bucket']:<26}  5d={v.get('matured_5d_entries',0):3d}  10d={v['matured_entries']:3d}/{v['total_entries']:3d}  "
            f"verdict={v['verdict']:<22}  {abs_str}{spy_str}{qqq_str}"
        )
    lines += [
        "",
        "NOTE: Forward returns are research-only and not predictive.",
        "--- RESEARCH ONLY — NO TRADE RECOMMENDATIONS ---",
    ]
    return "\n".join(lines)


def _unresolved_report(result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "kind": "forward_unresolved_cohorts",
        "version": result.get("version"),
        "generated_at": result.get("generated_at"),
        "research_only": True,
        "resolution_before_repair": result.get("resolution_before_repair") or {},
        "resolution_coverage": result.get("resolution_coverage") or {},
        "resolution_repair_summary": result.get("resolution_repair_summary") or {},
        "resolution_warnings": result.get("resolution_warnings") or [],
        "incomplete_evidence_warnings": result.get("incomplete_evidence_warnings") or [],
        "unresolved_repair_recommendation": result.get("unresolved_repair_recommendation"),
        "unresolved_bias_analysis": result.get("unresolved_bias_analysis") or {},
        "mapping_needed_report": result.get("mapping_needed_report") or {},
        "evidence_views": result.get("evidence_views") or {},
        "forward_backfill_repair_plan": result.get("forward_backfill_repair_plan") or {},
        "guardrails": result.get("guardrails") or {},
    }


def _format_unresolved_text(report: Dict[str, Any]) -> str:
    lines = [
        RESEARCH_ONLY_BANNER,
        "",
        "FORWARD UNRESOLVED COHORT REPORT",
        f"Generated: {report.get('generated_at')}",
        "",
    ]
    warnings = report.get("incomplete_evidence_warnings") or []
    if warnings:
        lines.append("Incomplete evidence warnings:")
        lines.extend(f"  - {w}" for w in warnings)
        lines.append("")
    rc = report.get("resolution_coverage") or {}
    for h, blk in rc.items():
        if not blk.get("expected_mature_count"):
            continue
        lines.append(
            f"{h}: expected={blk.get('expected_mature_count')} "
            f"resolved={blk.get('resolved_count')} "
            f"unresolved={blk.get('unresolved_count')} "
            f"resolution={blk.get('resolution_pct')}% "
            f"status={blk.get('resolution_status')}"
        )
        if blk.get("reason_category_counts"):
            lines.append(f"  reasons={blk.get('reason_category_counts')}")
        if blk.get("repairability_counts"):
            lines.append(f"  repairability={blk.get('repairability_counts')}")
        sample = blk.get("unresolved_detail") or []
        for row in sample[:12]:
            lines.append(
                f"  {row.get('ticker'):<8} {row.get('reason_category')} "
                f"repair={row.get('repairability')} "
                f"first={row.get('first_seen_date')} "
                f"mature={row.get('expected_maturity_date')} "
                f"last={row.get('last_available_price_date')} "
                f"type={row.get('security_type')}"
            )
        lines.append("")
    plan = report.get("forward_backfill_repair_plan") or {}
    lines.append(
        f"Backfill plan: mode={plan.get('mode')} "
        f"repairable={len(plan.get('repairable_tickers') or [])} "
        f"selected={plan.get('selected_for_backfill', 0)} "
        f"provider_calls_used={plan.get('provider_calls_used', 0)}"
    )
    lines += ["", "NOTE: Missing returns are never fabricated or forward-filled."]
    return "\n".join(lines)


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(
        description="Research Watchlist Forward Tracker (research-only)")
    parser.add_argument("--execute-repair-backfill", action="store_true",
                        default=False,
                        help=("Execute the existing targeted price-backfill "
                              "path for remaining REPAIRABLE unresolved "
                              "episodes. Default is dry-run planning only."))
    parser.add_argument("--max-repair-provider-calls", type=int, default=0,
                        help="Provider-call cap when --execute-repair-backfill is used.")
    args = parser.parse_args()

    print(RESEARCH_ONLY_BANNER)
    result = run_forward_tracker(
        execute_repair_backfill=args.execute_repair_backfill,
        max_repair_provider_calls=args.max_repair_provider_calls,
    )
    unresolved = _unresolved_report(result)

    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    cfg.LOG_DIR.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(result, indent=2), encoding="utf-8")
    OUT_TXT.write_text(_format_text(result), encoding="utf-8")
    OUT_UNRESOLVED_JSON.write_text(
        json.dumps(unresolved, indent=2), encoding="utf-8")
    OUT_UNRESOLVED_TXT.write_text(_format_unresolved_text(unresolved),
                                  encoding="utf-8")
    logger.info("wrote %s", OUT_JSON)
    logger.info("wrote %s", OUT_UNRESOLVED_JSON)

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
    print(f"Unresolved report: {OUT_UNRESOLVED_JSON}")


if __name__ == "__main__":
    main()

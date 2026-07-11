#!/usr/bin/env python3
"""
research/research_scanner.py — Research Scanner Engine (Phase 4B/4C).

Runs six research scanner categories against the cached price universe and
FMP data, scores each result with research-only watchlist labels, and
produces a daily research watchlist.

This module does NOT:
  - recommend trades, entries, stops, or targets
  - emit paper signals or trade proposals
  - require Alpaca or broker credentials
  - auto-route any result to execution

Scanner categories:
  1. Early Accumulation   — rising RS, volume ramp, base-building
  2. Beaten-Down Recovery — large drawdown, stabilization, reclaiming MAs
  3. Sector / Theme Leaders — top RS names inside leading sectors
  4. Catalyst Watch        — upcoming earnings, guidance, analyst revisions
  5. Social Arb / Attention Anomaly — early rising attention before price extends
  6. Long-Term Asymmetric Watch — small/mid-cap high-growth speculative candidates

Watchlist labels (4C):
  WATCH, RESEARCH, EARLY_ACCUMULATION, BEATEN_DOWN, SECTOR_LEADER,
  CATALYST, SOCIAL_ARB, ASYMMETRIC_RECOVERY_WATCH, TRUE_10X_RESEARCH, EXTENDED, RISKY, AVOID,
  CROWDED, NO_SOCIAL_DATA

Forbidden outputs:
  buy/sell/entry/stop loss/price target as trade instruction/position size

Outputs:
  cache/research/research_scanner_latest.json
  logs/research_scanner_latest.txt
  docs/research/RESEARCH_SCANNER_ENGINE.md   (written once)

Usage:
  SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python research/research_scanner.py
  GEM_TRADER_SKIP_DOTENV=true .venv/bin/python research/research_scanner.py --offline
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

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
os.environ.setdefault("PAPER_TRADING", "true")
os.environ.setdefault("DB_PATH", str(ROOT / "db" / "trading.db"))
os.environ.setdefault("CACHE_DIR", str(ROOT / "cache"))
os.environ.setdefault("LOG_DIR", str(ROOT / "logs"))

import pandas as pd

import core.config as cfg
from core.research_mode import SYSTEM_MODE, RESEARCH_ONLY_BANNER
from research.research_scoring import earliness_label as _earliness_label, consensus_label as _consensus_label
from research.research_candidate_enrichment import enrich_research_candidate
from research.catalyst_sanity import (
    validate_social_signal,
    FRESH_COMPANY_SPECIFIC,
    HYPE_CROWDED,
    NEEDS_MANUAL_SOURCE_CHECK,
    STALE,
)

VERSION = "RESEARCH_SCANNER_V1"
PRICE_DIR = cfg.CACHE_DIR / "prices"
DEEP_PRICE_DIR = cfg.CACHE_DIR / "prices_deep"
RESEARCH_DIR = cfg.CACHE_DIR / "research"
SCANNER_JSON = RESEARCH_DIR / "research_scanner_latest.json"
SCANNER_TXT = cfg.LOG_DIR / "research_scanner_latest.txt"
DOC_PATH = ROOT / "docs" / "research" / "RESEARCH_SCANNER_ENGINE.md"
UNIVERSE_BUILD_JSON = RESEARCH_DIR / "research_universe_build_latest.json"
UNIVERSE_BUILD_TXT = cfg.LOG_DIR / "research_universe_build_latest.txt"
UNIVERSE_MISS_JSON = RESEARCH_DIR / "universe_miss_diagnostic_latest.json"

# Scan universe cap: how many tickers to evaluate per category pass.
# Restores the original design: all ~5 500 tickers → ranked by RS/liquidity/bars
# → top 1 000 feed the 6 category scanners → ~70-100 unique candidates after dedup.
DEFAULT_UNIVERSE_CAP = 1000

# Max tickers from social-arb sidecar seeded into the universe.
# Social arb candidates are ordered by attention score in the sidecar; we take the
# top-N so social attention does not crowd out the RS-ranked fill.
_SOCIAL_ARB_UNIVERSE_CAP = 50

# Maps FMP sector name variants to GICS sector ETFs.
# FMP uses different naming than SECTOR_NAMES in regime_forecaster.
_FMP_SECTOR_TO_ETF: Dict[str, str] = {
    "technology": "XLK",
    "information technology": "XLK",
    "financial services": "XLF",
    "financials": "XLF",
    "finance": "XLF",
    "health care": "XLV",
    "healthcare": "XLV",
    "health": "XLV",
    "energy": "XLE",
    "consumer discretionary": "XLY",
    "consumer cyclical": "XLY",
    "industrials": "XLI",
    "industrial": "XLI",
    "consumer staples": "XLP",
    "consumer defensive": "XLP",
    "utilities": "XLU",
    "materials": "XLB",
    "basic materials": "XLB",
    "real estate": "XLRE",
    "communication services": "XLC",
    "communications": "XLC",
    "media": "XLC",
}

# Social crowd stages from social_attention_radar that indicate a name has peaked
_CROWDED_CROWD_STAGES = frozenset({"TRENDING", "VIRAL", "PEAK_ATTENTION", "POST_VIRAL"})

# Key tickers tracked in the miss diagnostic (not a hardcoded inclusion list)
_MISS_DIAGNOSTIC_TICKERS = [
    "NVDA", "AMD", "AVGO", "MRVL", "KLAC", "LRCX", "SMCI", "CRDO",
    "TSLA", "MSFT", "META", "PANW", "TSM", "AMAT", "ONTO",
]

import re as _re
_INVALID_SUFFIX_RE = _re.compile(r"^[A-Z]{2,6}(?:WS|WW|W|U)$")
_DOTTED_RE = _re.compile(r"[.\-]")

# ETFs, inverse ETFs, leveraged ETFs, commodity funds, and index funds that must
# never appear in operating-company scanner categories (early_accumulation,
# beaten_down_recovery, sector_theme_leader, catalyst_watch, long_term_asymmetric,
# social_arb_attention).  Macro/sector ETFs remain usable for regime + RS context.
# Keep alphabetical for easy audit.
_KNOWN_ETFS: frozenset = frozenset({
    # Volatility products
    "SVXY", "UVXY", "VIXY", "VXX",
    # Bond ETFs
    "AGG", "BIL", "BND", "HYG", "IEF", "LQD", "SHY", "TLT",
    # Cannabis
    "MSOS", "YOLO",
    # Commodity / Metals
    "DBTC", "DGP", "DJP", "GLD", "GLL", "PDBC", "SLV", "USO", "UNG", "ZSL",
    # Broad US market index
    "DIA", "IVV", "IWM", "MDY", "QQQ", "RSP", "SPY", "SPTM", "VTI", "VOO",
    # Sector SPDR + equivalents
    "XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY",
    # Tech / Semi sector ETFs
    "BOTZ", "CIBR", "HACK", "ROBO", "SMH", "SOXX", "WCLD",
    # Growth / Speculative
    "ARKF", "ARKG", "ARKK", "ARKQ", "ARKW", "FNGU", "FNGD",
    # Biotech
    "IBB", "LABD", "LABU", "XBI",
    # International
    "EEM", "EFA", "FXI", "IEMG", "IEFA", "EWJ", "VWO",
    # Leveraged / Inverse broad
    "SDOW", "SOXL", "SOXS", "SPXL", "SPXS", "SPXU", "SQQQ", "TECS", "TECL",
    "TQQQ", "UDOW", "UPRO",
    # Income / Dividend ETFs
    "DGRO", "JEPI", "JEPQ", "SCHD", "SPYD",
    # Other commonly seen in social/scanner feeds
    "BITX", "BITO", "FNGO", "GBTC", "IBIT",
})

# Watchlist label set
WATCHLIST_LABELS = frozenset({
    "WATCH",
    "RESEARCH",
    "EARLY_ACCUMULATION",
    "BEATEN_DOWN",
    "SECTOR_LEADER",
    "CATALYST",
    "SOCIAL_ARB",
    "ASYMMETRIC_RECOVERY_WATCH",
    "TRUE_10X_RESEARCH",
    "RS_MOMENTUM_LEADER",
    "EXTENDED",
    "RISKY",
    "AVOID",
    "CROWDED",
    "NO_SOCIAL_DATA",
})

# Data trust levels
TRUST_HIGH = "HIGH"
TRUST_MEDIUM = "MEDIUM"
TRUST_LOW = "LOW"
TRUST_SPECULATIVE = "SPECULATIVE"
TRUST_NO_DATA = "NO_DATA"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("research_scanner")


# ── Price utilities ──────────────────────────────────────────────────────────


# Max staleness (calendar days) for a ticker's last bar before the frame is
# treated as unusable for RS/momentum math.  None = no gate (unit-test default;
# tests build synthetic frames with arbitrary dates).  main() sets this for
# real runs so a 3-week-stale parquet can't be compared against a current SPY.
_MAX_STALE_DAYS: Optional[int] = None
_STALE_SKIPPED: Set[str] = set()

# P0 same-session gate (2026-07-10 pipeline review): the PRIMARY validity rule.
# When set (main() sets it to core.market_session.required_market_session()),
# any ticker whose last bar predates the required completed session is excluded
# with SESSION_MISMATCH instead of being compared against a fresher benchmark.
# None = no gate (unit-test default, same pattern as _MAX_STALE_DAYS).
_REQUIRED_SESSION: Optional[date] = None
_SESSION_MISMATCH_SKIPPED: Set[str] = set()

# Price-series sanity guard (2026-07-02): FMP EOD history for some micro-caps
# mixes adjusted/unadjusted prints — e.g. repeated same-week x10 up / x0.1 down
# flips, or isolated x25-x37 single-day jumps from unadjusted reverse splits.
# Those series produce four-digit fake RS values that crowd out real leaders.
# Any close-to-close ratio outside [LO, HI] within the RS lookback window marks
# the series suspect and the ticker is skipped (surfaced in the sidecar).
_SUSPECT_JUMP_HI = 2.5
_SUSPECT_JUMP_LO = 0.4
_SUSPECT_WINDOW = 64
_DATA_SUSPECT_SKIPPED: Set[str] = set()


def _price_series_suspect(closes: List[float]) -> bool:
    tail = closes[-_SUSPECT_WINDOW:]
    for a, b in zip(tail, tail[1:]):
        if a > 0 and (b / a > _SUSPECT_JUMP_HI or b / a < _SUSPECT_JUMP_LO):
            return True
    return False


def _load_cached_frame(symbol: str) -> Optional[pd.DataFrame]:
    """Merged price frame: deep backfill history + shallow recent bars.

    Mirrors the universe builder's merged read — the scan lanes previously
    read only the shallow cache, so MA200/252d windows were silently None
    for every ticker whose deep history lives in cache/prices_deep/.
    """
    def _read(path: Path) -> Optional[pd.DataFrame]:
        if not path.exists():
            return None
        try:
            df = pd.read_parquet(path)
            if df is None or df.empty:
                return None
            if "close" not in df.columns and "Close" in df.columns:
                df = df.rename(columns={"Close": "close"})
            return df
        except Exception:
            return None

    sym = symbol.upper()
    df_shallow = _read(PRICE_DIR / f"{sym}.parquet")
    df_deep = _read(DEEP_PRICE_DIR / f"{sym}.parquet") if DEEP_PRICE_DIR.exists() else None

    if df_shallow is None and df_deep is None:
        return None
    if df_shallow is None:
        df = df_deep
    elif df_deep is None:
        df = df_shallow
    else:
        try:
            combined = pd.concat([df_deep, df_shallow], axis=0)
            combined = combined[~combined.index.duplicated(keep="last")]
            df = combined.sort_index()
        except Exception:
            df = df_shallow if len(df_shallow) >= len(df_deep) else df_deep

    if _REQUIRED_SESSION is not None and df is not None:
        try:
            last = pd.to_datetime(df.index).max().date()
            if last < _REQUIRED_SESSION:
                _SESSION_MISMATCH_SKIPPED.add(sym)
                return None
        except Exception:
            pass

    if _MAX_STALE_DAYS is not None and df is not None:
        try:
            last = pd.to_datetime(df.index).max().date()
            age = (datetime.now(timezone.utc).date() - last).days
            if age > _MAX_STALE_DAYS:
                _STALE_SKIPPED.add(sym)
                return None
        except Exception:
            pass

    if df is not None and sym != "SPY":
        try:
            closes = [float(v) for v in df["close"].dropna().tolist()] if "close" in df.columns else []
            if closes and _price_series_suspect(closes):
                _DATA_SUSPECT_SKIPPED.add(sym)
                return None
        except Exception:
            pass
    return df


def _closes(df: Optional[pd.DataFrame]) -> List[float]:
    if df is None:
        return []
    col = "close" if "close" in df.columns else ("Close" if "Close" in df.columns else None)
    if col is None:
        return []
    return [float(v) for v in df[col].dropna().tolist()]


def _volumes(df: Optional[pd.DataFrame]) -> List[float]:
    if df is None:
        return []
    col = next((c for c in ["volume", "Volume"] if c in df.columns), None)
    if col is None:
        return []
    return [float(v) for v in df[col].dropna().tolist()]


def _last(s: List[float]) -> Optional[float]:
    return s[-1] if s else None


def _ma(s: List[float], w: int) -> Optional[float]:
    if len(s) < w:
        return None
    return sum(s[-w:]) / w


def _ret(s: List[float], lb: int) -> Optional[float]:
    if len(s) < lb + 1:
        return None
    return (s[-1] / s[-(lb + 1)] - 1.0) * 100.0


def _max_in_window(s: List[float], w: int) -> Optional[float]:
    if not s:
        return None
    return max(s[-w:]) if len(s) >= w else max(s)


def _vol_trend(vols: List[float], short: int = 10, long: int = 30) -> Optional[float]:
    """Ratio of short-window avg volume to long-window avg volume. >1 = rising."""
    if len(vols) < long:
        return None
    avg_short = sum(vols[-short:]) / short
    avg_long = sum(vols[-long:]) / long
    return avg_short / avg_long if avg_long > 0 else None


def _rs_vs_spy(closes: List[float], spy: List[float], lookback: int = 63) -> Optional[float]:
    """Relative strength vs SPY over lookback bars."""
    if len(closes) < lookback + 1 or len(spy) < lookback + 1:
        return None
    r_stock = closes[-1] / closes[-(lookback + 1)] - 1.0
    r_spy = spy[-1] / spy[-(lookback + 1)] - 1.0
    return (r_stock - r_spy) * 100.0


def _higher_lows(s: List[float], window: int = 20) -> bool:
    """Simple higher-lows check: each recent trough is higher than the prior."""
    if len(s) < window:
        return False
    chunk = s[-window:]
    troughs = [chunk[i] for i in range(1, len(chunk) - 1)
               if chunk[i] < chunk[i - 1] and chunk[i] < chunk[i + 1]]
    if len(troughs) < 2:
        return False
    return troughs[-1] > troughs[0]


def _drawdown_from_high(s: List[float], window: int = 252) -> Optional[float]:
    if not s or len(s) < 5:
        return None
    recent = s[-window:] if len(s) >= window else s
    hi = max(recent)
    return (s[-1] / hi - 1.0) * 100.0 if hi > 0 else None


# ── Universe building ────────────────────────────────────────────────────────


def _is_offline_fmp() -> bool:
    return os.getenv("FMP_API_KEY", "").strip().lower() in {"", "offline", "stub"}


def _is_valid_equity_symbol(sym: str) -> bool:
    """Reject warrants, units, rights, foreign dots, and obvious junk."""
    if not sym or len(sym) > 6 or len(sym) < 1:
        return False
    if _DOTTED_RE.search(sym):
        return False
    if _INVALID_SUFFIX_RE.match(sym):
        return False
    return True


def _alpha_board_tickers() -> List[str]:
    """Pull tickers from alpha discovery board sidecar (primary) and fallback files.

    Priority:
      1. alpha_discovery_board_latest.json  — uses 'items' key (current format)
      2. alpha_discovery_latest.json        — legacy format, 'candidates'/'board' key
      3. alpha_discovery_overlay_latest.json — secondary fallback
    """
    tickers: List[str] = []
    seen_set: Set[str] = set()

    def _add_from_list(items: List[Any]) -> None:
        for item in items:
            t = (item.get("ticker") or item.get("symbol") or "").upper().strip()
            if t and len(t) <= 6 and _is_valid_equity_symbol(t) and t not in seen_set:
                seen_set.add(t)
                tickers.append(t)

    # Primary: alpha_discovery_board_latest.json uses 'items' key
    board_file = RESEARCH_DIR / "alpha_discovery_board_latest.json"
    if board_file.exists():
        try:
            data = json.loads(board_file.read_text())
            _add_from_list(data.get("items", []))
        except Exception:
            pass

    # Fallback: older / overlay formats use 'candidates' or 'board' key
    for name in ["alpha_discovery_latest.json", "alpha_discovery_overlay_latest.json"]:
        path = RESEARCH_DIR / name
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text())
            _add_from_list(
                data.get("candidates", data.get("board", data.get("items", [])))
            )
        except Exception:
            pass

    if tickers:
        logger.info("alpha board: %d tickers", len(tickers))
    return tickers


def _daily_radar_tickers() -> List[str]:
    """Pull reset/reclaim/high-priority candidates from daily alpha radar sidecar."""
    tickers: List[str] = []
    radar_file = RESEARCH_DIR / "daily_alpha_radar_latest.json"
    if not radar_file.exists():
        return tickers
    try:
        data = json.loads(radar_file.read_text())
        for item in data.get("candidates", []):
            label = (item.get("priority_label") or item.get("label") or "").upper()
            if label in {"RESET_WATCH", "RECLAIM_WATCH", "HIGH_PRIORITY_RESEARCH", "WATCHLIST_RESEARCH"}:
                t = (item.get("ticker") or "").upper().strip()
                if t and len(t) <= 6 and _is_valid_equity_symbol(t) and t not in tickers:
                    tickers.append(t)
    except Exception:
        pass
    if tickers:
        logger.info("daily radar seeds: %d tickers", len(tickers))
    return tickers


def _social_arb_tickers() -> List[str]:
    """Pull tickers from social arb / social attention sidecars."""
    tickers: List[str] = []
    for name in [
        "social_arb_latest.json",
        "social_attention_latest.json",
        "social_attention_radar_latest.json",
    ]:
        path = RESEARCH_DIR / name
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text())
            for item in _social_items(data):
                t = (item.get("ticker") or item.get("symbol") or "").upper()
                if t and len(t) <= 6 and t not in tickers:
                    tickers.append(t)
        except Exception:
            pass
    return tickers


def _social_items(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Candidate rows from a social sidecar, tolerant of each artifact's key:
    social_attention_radar uses ``leads``, older sidecars used ``candidates``,
    and social_arb_radar stores its picks under ``items`` (which no reader
    matched before — the arb radar's tickers never reached the scanner)."""
    for key in ("candidates", "leads", "items"):
        rows = data.get(key)
        if isinstance(rows, list) and rows:
            return rows
    return []


def _social_score_from_item(item: Dict[str, Any]) -> float:
    """
    Derive a 0-1 social attention score from a radar item.

    Priority: legacy 'score'/'deterministic_score' fields (old sidecars)
    → attention_velocity_score + attention_novelty_score composite (new radar).
    Output is always in [0.0, 1.0] for use in the 50+score*40 formula.
    """
    # Explicit score field (social_arb sidecars).  Old sidecars published
    # 0-1; the current social_arb_radar publishes deterministic_score /
    # confidence_score on a 0-100 scale — normalize instead of clamping
    # (clamping mapped every arb item to 1.0 regardless of confidence).
    legacy = item.get("score") or item.get("deterministic_score")
    if legacy is not None:
        val = float(legacy)
        if val > 1.0:
            val /= 100.0
        return float(min(1.0, max(0.0, val)))

    # New social_attention_radar fields
    velocity = float(item.get("attention_velocity_score") or 0.0)  # 0-100
    novelty = float(item.get("attention_novelty_score") or 0.0)    # 0-100
    z_raw = float(item.get("mention_z_score") or 0.0)
    confidence = float(item.get("best_confidence") or 0.0)         # 0-1

    # Weighted composite on 0-100 scale
    raw = velocity * 0.5 + novelty * 0.3 + max(0.0, min(z_raw * 10.0, 20.0)) * 0.2

    # Discount low-confidence ticker mappings
    if confidence < 0.5:
        raw *= 0.5
    elif confidence < 0.7:
        raw *= 0.8

    return min(1.0, max(0.0, raw / 100.0))


def _load_social_data() -> Dict[str, Any]:
    """Load social attention data from available sidecars."""
    social: Dict[str, Any] = {}
    for name in [
        "social_arb_latest.json",
        "social_attention_latest.json",
        "social_attention_radar_latest.json",
    ]:
        path = RESEARCH_DIR / name
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text())
            for item in _social_items(data):
                t = (item.get("ticker") or item.get("symbol") or "").upper()
                if not t:
                    continue
                crowd_stage = (item.get("crowd_stage") or "").upper()
                crowded = (
                    bool(item.get("already_viral") or item.get("crowded"))
                    or crowd_stage in _CROWDED_CROWD_STAGES
                )
                score = _social_score_from_item(item)
                # A ticker can appear in more than one sidecar (arb radar +
                # attention radar); keep the strongest signal rather than
                # letting whichever file loads last win.
                prev = social.get(t)
                if prev is not None:
                    # CROWDED is a risk flag — once any sidecar says a name
                    # is already viral, no other entry may clear it,
                    # regardless of which one wins on score.
                    if prev["score"] >= score:
                        if crowded and not prev.get("crowded"):
                            prev["crowded"] = True
                            prev["crowd_stage"] = prev.get("crowd_stage") or (crowd_stage or None)
                        continue
                    crowded = crowded or bool(prev.get("crowded"))
                social[t] = {
                    "source": name.replace("_latest.json", ""),
                    "trust_level": TRUST_MEDIUM,
                    "refresh_cadence": "daily_post_close",
                    "crowded": crowded,
                    "score": score,
                    "crowd_stage": crowd_stage or None,
                    "label": item.get("label") or item.get("news_label") or "",
                }
        except Exception:
            pass
    return social


def _load_ranked_frame(sym: str) -> Optional[pd.DataFrame]:
    """
    Load the best available price data for ranked_fill scoring.
    Merges deep and shallow caches (shallow wins on overlap) so tickers
    present only in the deep cache (historical backfill) are discoverable
    and scored on the most recent data.
    """
    def _read(path: "Path") -> Optional[pd.DataFrame]:
        if not path.exists():
            return None
        try:
            df = pd.read_parquet(path)
            if df is None or df.empty:
                return None
            if "close" not in df.columns and "Close" in df.columns:
                df = df.rename(columns={"Close": "close"})
            return df
        except Exception:
            return None

    df_shallow = _read(PRICE_DIR / f"{sym}.parquet")
    df_deep = _read(DEEP_PRICE_DIR / f"{sym}.parquet") if DEEP_PRICE_DIR.exists() else None

    if df_shallow is None and df_deep is None:
        return None
    if df_shallow is None:
        return df_deep
    if df_deep is None:
        return df_shallow
    try:
        combined = pd.concat([df_deep, df_shallow], axis=0)
        combined = combined[~combined.index.duplicated(keep="last")]
        return combined.sort_index()
    except Exception:
        return df_shallow if len(df_shallow) >= len(df_deep) else df_deep


def _ranked_cache_fill(
    needed: int,
    seen: Set[str],
) -> Tuple[List[Tuple[str, str]], Dict[str, Any]]:
    """
    Select the best `needed` tickers from the price cache ranked by data quality,
    liquidity, and RS vs SPY.  Returns ([(ticker, 'ranked_fill'), ...], metadata).

    Scans BOTH cache/prices/ (shallow) and cache/prices_deep/ (deep backfill)
    so high-quality names like NVDA/AMD/AVGO that exist only in one cache are
    discoverable and ranked on merit.

    Scoring (additive, higher = better):
      +4   bar_count >= 200  (MA200 window available)
      +2   bar_count >= 100
      +1   bar_count >= 60
      +3   avg daily $ vol >= $50M
      +2   avg daily $ vol >= $10M
      +1   avg daily $ vol >= $1M
      +2.5 rs_63d vs SPY > 15pp
      +1.5 rs_63d vs SPY > 5pp
      +0.5 rs_63d vs SPY > 0pp
      -1.0 rs_63d vs SPY < -20pp
      +0.5 rs_20d vs SPY > 5pp
      -0.5 rs_20d vs SPY < -10pp
      +0.5 last_price >= $10
    Minimum viability: bar_count >= 60, last_price >= $2.
    Excludes known ETFs / inverse ETFs / leveraged products (_KNOWN_ETFS).
    """
    if needed <= 0:
        return [], {"candidates_considered": 0, "candidates_scored": 0, "selected": 0}

    spy_closes = _closes(_load_cached_frame("SPY"))

    # Collect all unique candidate symbols from BOTH caches
    candidate_syms: Set[str] = set()
    for pf in PRICE_DIR.glob("*.parquet"):
        sym = pf.stem.upper()
        if _is_valid_equity_symbol(sym) and sym not in seen and sym not in _KNOWN_ETFS:
            candidate_syms.add(sym)
    if DEEP_PRICE_DIR.exists():
        for pf in DEEP_PRICE_DIR.glob("*.parquet"):
            sym = pf.stem.upper()
            if _is_valid_equity_symbol(sym) and sym not in seen and sym not in _KNOWN_ETFS:
                candidate_syms.add(sym)

    # Load and score each candidate using merged (deep+shallow) data
    scored: List[Tuple[float, str]] = []
    all_scores: Dict[str, float] = {}
    for sym in candidate_syms:
        df = _load_ranked_frame(sym)
        if df is None or df.empty:
            continue
        col_c = "close" if "close" in df.columns else ("Close" if "Close" in df.columns else None)
        col_v = next((c for c in ["volume", "Volume"] if c in df.columns), None)
        if col_c is None:
            continue
        try:
            closes = [float(v) for v in df[col_c].dropna().tolist()]
            volumes = [float(v) for v in df[col_v].dropna().tolist()] if col_v else []
        except Exception:
            continue

        n_bars = len(closes)
        last = closes[-1] if closes else 0.0
        if n_bars < 60 or last < 2.0:
            continue

        sc = 0.0
        if n_bars >= 200:
            sc += 4.0
        elif n_bars >= 100:
            sc += 2.0
        else:
            sc += 1.0

        if volumes and len(volumes) >= 20:
            avg_dvol = (sum(volumes[-20:]) / 20) * last
            if avg_dvol >= 50_000_000:
                sc += 3.0
            elif avg_dvol >= 10_000_000:
                sc += 2.0
            elif avg_dvol >= 1_000_000:
                sc += 1.0

        rs_63 = _rs_vs_spy(closes, spy_closes, 63) if len(closes) >= 64 and spy_closes else None
        rs_20 = _rs_vs_spy(closes, spy_closes, 20) if len(closes) >= 21 and spy_closes else None
        if rs_63 is not None:
            if rs_63 > 15:
                sc += 2.5
            elif rs_63 > 5:
                sc += 1.5
            elif rs_63 > 0:
                sc += 0.5
            elif rs_63 < -20:
                sc -= 1.0
        if rs_20 is not None:
            if rs_20 > 5:
                sc += 0.5
            elif rs_20 < -10:
                sc -= 0.5
        if last >= 10.0:
            sc += 0.5

        all_scores[sym] = round(sc, 3)
        scored.append((sc, sym))

    scored.sort(key=lambda x: x[0], reverse=True)
    selected = [(sym, "ranked_fill") for _, sym in scored[:needed]]
    metadata = {
        "candidates_considered": len(candidate_syms),
        "candidates_scored": len(scored),
        "selected": len(selected),
        "all_scores": all_scores,
    }
    logger.info("ranked fill: considered=%d scored=%d selected=%d",
                len(candidate_syms), len(scored), len(selected))
    return selected, metadata


def _build_universe(
    cap: int = DEFAULT_UNIVERSE_CAP,
) -> Tuple[List[str], Dict[str, Any]]:
    """
    Assemble scan universe with source tracking.  Returns (universe_list, build_info).

    Source priority:
      1. alpha_board        — curated alpha discovery board
      2. daily_alpha_radar  — reset / reclaim / high-priority radar names
      3. social_arb         — social attention candidates
      4. ranked_fill        — full price cache ranked by data quality + RS + liquidity
      [emergency] alphabetical_fallback — only when no ranked data is available
    """
    seen: Set[str] = set()
    universe: List[str] = []
    source_map: Dict[str, str] = {}
    counts: Dict[str, int] = {
        "alpha_board": 0,
        "daily_alpha_radar": 0,
        "social_arb": 0,
        "ranked_fill": 0,
        "alphabetical_fallback": 0,
    }

    # P2 hygiene: confirmed-dead symbols (repeated hard refresh failures on
    # distinct days — research/dead_symbol_tombstones.py) never enter the
    # scan universe.  Excluded count is reported, never silent.
    try:
        from research.dead_symbol_tombstones import confirmed_dead
        dead_symbols: Set[str] = confirmed_dead()
    except Exception:
        dead_symbols = set()
    dead_excluded: List[str] = []

    def _add(t: str, source: str) -> bool:
        u = (t or "").upper().strip()
        if u and u in dead_symbols:
            if u not in dead_excluded:
                dead_excluded.append(u)
            return False
        if u and u not in seen and _is_valid_equity_symbol(u):
            seen.add(u)
            universe.append(u)
            source_map[u] = source
            counts[source] = counts.get(source, 0) + 1
            return True
        return False

    # P1: alpha discovery board
    for t in _alpha_board_tickers():
        _add(t, "alpha_board")

    # P2: daily alpha radar (reset / reclaim / high-priority)
    for t in _daily_radar_tickers():
        _add(t, "daily_alpha_radar")

    # P3: social arb / attention names (capped — sidecar is ordered by attention score,
    # so [:cap] takes the highest-signal names and leaves ranked_fill room to breathe).
    for t in _social_arb_tickers()[:_SOCIAL_ARB_UNIVERSE_CAP]:
        _add(t, "social_arb")

    # P4: ranked fill from full price cache
    needed = cap - len(universe)
    ranked_meta: Dict[str, Any] = {}
    if needed > 0:
        ranked, ranked_meta = _ranked_cache_fill(needed, seen)
        for sym, src in ranked:
            _add(sym, src)

    # Emergency fallback (should not trigger in normal operation)
    used_alphabetical = False
    if len(universe) < cap:
        needed_fb = cap - len(universe)
        for pf in sorted(PRICE_DIR.glob("*.parquet")):
            sym = pf.stem.upper()
            if _is_valid_equity_symbol(sym) and sym not in seen:
                _add(sym, "alphabetical_fallback")
                needed_fb -= 1
                used_alphabetical = True
                if needed_fb <= 0:
                    break
        if used_alphabetical:
            logger.warning(
                "universe: alphabetical fallback used (%d tickers) — ranked data may be unavailable",
                counts["alphabetical_fallback"],
            )

    final = universe[:cap]

    # Build diagnostics for miss diagnostic — checks BOTH caches
    all_scores = ranked_meta.get("all_scores", {})
    miss_diag: Dict[str, Any] = {}
    for sym in _MISS_DIAGNOSTIC_TICKERS:
        in_shallow = (PRICE_DIR / f"{sym}.parquet").exists()
        in_deep = DEEP_PRICE_DIR.exists() and (DEEP_PRICE_DIR / f"{sym}.parquet").exists()
        in_cache = in_shallow or in_deep
        if in_shallow and in_deep:
            cache_location = "in_merged_cache"
        elif in_shallow:
            cache_location = "in_shallow_cache_only"
        elif in_deep:
            cache_location = "in_deep_cache_only"
        else:
            cache_location = "not_in_any_price_cache"

        is_selected = sym in source_map
        score = all_scores.get(sym)
        reason: Optional[str]
        if not in_cache:
            reason = "not_in_any_price_cache"
        elif is_selected:
            reason = None   # selected — no absent reason
        elif sym in seen and not is_selected:
            reason = "already_in_universe_from_earlier_source"
        elif score is not None:
            reason = f"ranked_fill_not_selected (score={score})"
        elif in_cache:
            reason = "ranked_fill_not_scored (below_bar_count_or_price_floor)"
        else:
            reason = "unknown"
        miss_diag[sym] = {
            "cache_location": cache_location,
            "in_price_cache": in_cache,
            "in_universe": is_selected,
            "source": source_map.get(sym),
            "ranked_score": score,
            "absent_reason": reason,
        }

    build_info = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_universe_size": len(final),
        "source_counts": counts,
        "dead_symbol_excluded_count": len(dead_excluded),
        "dead_symbol_excluded": sorted(dead_excluded)[:50],
        "used_alphabetical_fallback": used_alphabetical,
        "ranked_fill_candidates_considered": ranked_meta.get("candidates_considered", 0),
        "ranked_fill_candidates_scored": ranked_meta.get("candidates_scored", 0),
        "ranked_fill_selected": ranked_meta.get("selected", 0),
        # Full universe list — JSON consumers should use this, not top_50
        "universe_full": [{"ticker": t, "source": source_map[t]} for t in final],
        # Backward-compat alias (first 50 only)
        "top_50": [{"ticker": t, "source": source_map[t]} for t in final[:50]],
        "all_ticker_sources": source_map,
        "miss_diagnostic": miss_diag,
    }

    logger.info(
        "universe: total=%d board=%d radar=%d social=%d ranked=%d fallback=%d",
        len(final),
        counts["alpha_board"], counts["daily_alpha_radar"],
        counts["social_arb"], counts["ranked_fill"], counts["alphabetical_fallback"],
    )
    return final, build_info


def _write_universe_build_log(build_info: Dict[str, Any]) -> None:
    """Write universe build sidecar JSON + human-readable text log.

    Paths come from the module-level UNIVERSE_BUILD_JSON / UNIVERSE_MISS_JSON /
    UNIVERSE_BUILD_TXT constants, read at call time so test patches are
    respected.  Tests are sandboxed centrally by an autouse conftest fixture —
    on 2026-07-02 a pytest run clobbered the real
    research_universe_build_latest.json with a fixture universe, which would
    have silently degraded the nightly refresh-universe-prices step (it reads
    this sidecar for its ticker list) to SPY-only.
    """
    if not build_info:
        return

    out_json = UNIVERSE_BUILD_JSON
    out_miss = UNIVERSE_MISS_JSON
    out_txt = UNIVERSE_BUILD_TXT

    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_txt.parent.mkdir(parents=True, exist_ok=True)

    # JSON sidecar — includes full universe list
    out_json.write_text(json.dumps(build_info, indent=2), encoding="utf-8")

    # Miss diagnostic JSON
    out_miss.write_text(
        json.dumps({
            "generated_at": build_info.get("generated_at"),
            "miss_diagnostic": build_info.get("miss_diagnostic", {}),
        }, indent=2),
        encoding="utf-8",
    )

    # Human-readable text
    total = build_info.get("total_universe_size", 0)
    counts = build_info.get("source_counts", {})
    universe_full = build_info.get("universe_full", build_info.get("top_50", []))
    display_n = min(50, len(universe_full))
    lines = [
        "RESEARCH UNIVERSE BUILD LOG",
        f"Generated: {build_info.get('generated_at', 'unknown')}",
        f"Total universe size (selected): {total}",
        "",
        "SOURCE COUNTS (selected tickers):",
        f"  alpha_board:          {counts.get('alpha_board', 0):4d}",
        f"  daily_alpha_radar:    {counts.get('daily_alpha_radar', 0):4d}",
        f"  social_arb:           {counts.get('social_arb', 0):4d}",
        f"  ranked_fill:          {counts.get('ranked_fill', 0):4d}",
        f"  alphabetical_fallback:{counts.get('alphabetical_fallback', 0):4d}",
        f"  TOTAL SELECTED:       {total:4d}",
        "",
        f"RANKED FILL: considered={build_info.get('ranked_fill_candidates_considered', 0)} "
        f"scored={build_info.get('ranked_fill_candidates_scored', 0)} "
        f"selected={build_info.get('ranked_fill_selected', 0)}",
        f"ALPHABETICAL FALLBACK USED: {build_info.get('used_alphabetical_fallback', False)}",
        "",
        f"TOP {display_n} DISPLAYED (of {total} selected) — full list in JSON universe_full:",
    ]
    for entry in universe_full[:display_n]:
        lines.append(f"  {entry['ticker']:8s}  [{entry['source']}]")

    lines += ["", "MISS DIAGNOSTIC (key names):"]
    for sym, d in build_info.get("miss_diagnostic", {}).items():
        status = "IN_UNIVERSE" if d["in_universe"] else "ABSENT"
        reason = f"  reason={d['absent_reason']}" if d.get("absent_reason") else ""
        score_str = f"  score={d['ranked_score']}" if d.get("ranked_score") is not None else ""
        source_str = f"  source={d['source']}" if d.get("source") else ""
        cache_str = f"  cache={d.get('cache_location', '?')}"
        lines.append(f"  {sym:8s}  {status}{source_str}{score_str}{cache_str}{reason}")

    out_txt.write_text("\n".join(lines), encoding="utf-8")
    logger.info("wrote %s", out_json)
    logger.info("wrote %s", out_miss)


# ── FMP helpers ──────────────────────────────────────────────────────────────


def _fmp_fundamentals(ticker: str) -> Optional[Dict[str, Any]]:
    if _is_offline_fmp():
        return None
    try:
        from core.fmp_client import get_fmp
        return get_fmp().get_fundamentals(ticker)
    except Exception:
        return None


def _fmp_earnings_calendar(days_ahead: int = 14) -> List[Dict[str, Any]]:
    if _is_offline_fmp():
        return []
    try:
        from core.fmp_client import get_fmp
        return get_fmp().get_earnings_calendar(days_ahead=days_ahead)
    except Exception:
        return []


def _fmp_analyst_grades(ticker: str) -> List[Dict[str, Any]]:
    if _is_offline_fmp():
        return []
    try:
        from core.fmp_client import get_fmp
        return get_fmp().get_analyst_grades(ticker, limit=10)
    except Exception:
        return []


def _fmp_news(ticker: str) -> List[Dict[str, Any]]:
    if _is_offline_fmp():
        return []
    try:
        from core.fmp_client import get_fmp
        return get_fmp().get_news(ticker=ticker, limit=5)
    except Exception:
        return []


# ── Enrichment helpers (scores / company metadata / data freshness) ──────────


def _parquet_mtime(sym: str) -> Optional[str]:
    path = PRICE_DIR / f"{sym.upper()}.parquet"
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def _derive_scores(item: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """Derive 0–100 sub-component research scores from available item fields."""
    rs_63 = item.get("rs_63d_vs_spy")
    rs_20 = item.get("rs_20d_vs_spy")
    vol_tr = item.get("vol_trend_ratio")
    above_ma50 = item.get("above_ma50")
    above_ma200 = item.get("above_ma200")
    dd = item.get("dd_from_high_pct")
    extension = item.get("extension_vs_ma200_pct")
    social_score_raw = item.get("social_score")
    has_catalyst = bool(item.get("earnings_date") or item.get("has_analyst_upgrade"))
    revenue_growth = item.get("revenue_growth_pct")
    survivability_ok = item.get("survivability_ok")
    category = item.get("category", "")

    rs_val: Optional[float] = None
    if rs_63 is not None or rs_20 is not None:
        combined = (rs_63 or 0) * 0.6 + (rs_20 or 0) * 0.4
        rs_val = max(0.0, min(100.0, 50.0 + combined * 2.0))

    trend_val: Optional[float] = None
    if above_ma50 is not None or above_ma200 is not None:
        trend_val = 50.0
        if above_ma200 is True:
            trend_val += 25
        elif above_ma200 is False:
            trend_val -= 25
        if above_ma50 is True:
            trend_val += 25
        elif above_ma50 is False:
            trend_val -= 15
        trend_val = max(0.0, min(100.0, trend_val))

    volume_val: Optional[float] = None
    if vol_tr is not None:
        volume_val = max(0.0, min(100.0, (vol_tr - 0.5) / 1.0 * 100.0))

    catalyst_val: Optional[float] = None
    if category == "catalyst_watch" or has_catalyst:
        catalyst_val = 70.0
        days_to = item.get("days_to_earnings")
        if days_to is not None and 0 <= int(days_to) <= 7:
            catalyst_val += 20
        elif days_to is not None and int(days_to) <= 14:
            catalyst_val += 10
        if item.get("extended_into_earnings"):
            catalyst_val -= 20
        catalyst_val = max(0.0, min(100.0, catalyst_val))

    fundamental_val: Optional[float] = None
    if survivability_ok is not None or revenue_growth is not None:
        fundamental_val = 50.0
        if survivability_ok is True:
            fundamental_val += 20
        elif survivability_ok is False:
            fundamental_val -= 25
        if revenue_growth is not None:
            if revenue_growth > 30:
                fundamental_val += 25
            elif revenue_growth > 15:
                fundamental_val += 15
            elif revenue_growth > 5:
                fundamental_val += 5
            elif revenue_growth < 0:
                fundamental_val -= 15
        fundamental_val = max(0.0, min(100.0, fundamental_val))

    social_val: Optional[float] = None
    if social_score_raw is not None:
        social_val = max(0.0, min(100.0, float(social_score_raw) * 100.0))
    elif category == "social_arb_attention":
        social_val = 0.0

    extension_risk_val: Optional[float] = None
    if extension is not None:
        extension_risk_val = max(0.0, min(100.0, extension * 2.0))
    elif dd is not None:
        extension_risk_val = max(0.0, min(100.0, max(0.0, 100.0 + dd)))

    liquidity_val: Optional[float] = None
    if vol_tr is not None:
        liquidity_val = max(0.0, min(100.0, vol_tr * 60.0))

    return {
        "rs": round(rs_val, 1) if rs_val is not None else None,
        "trend": round(trend_val, 1) if trend_val is not None else None,
        "volume": round(volume_val, 1) if volume_val is not None else None,
        "catalyst": round(catalyst_val, 1) if catalyst_val is not None else None,
        "fundamental": round(fundamental_val, 1) if fundamental_val is not None else None,
        "social": round(social_val, 1) if social_val is not None else None,
        "extension_risk": round(extension_risk_val, 1) if extension_risk_val is not None else None,
        "liquidity": round(liquidity_val, 1) if liquidity_val is not None else None,
    }


def _enrich_item(item: Dict[str, Any], profile: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Add company_name, sector, industry, scores, data_freshness to a scanner item."""
    sym = item.get("ticker", "")
    if profile:
        item["company_name"] = profile.get("companyName") or profile.get("name") or sym
        item["sector"] = profile.get("sector") or None
        item["industry"] = profile.get("industry") or None
    else:
        item.setdefault("company_name", None)
        item.setdefault("sector", None)
        item.setdefault("industry", None)

    # Per-company sector ETF attribution (uses FMP sector from profile)
    company_sector = (item.get("sector") or "").lower().strip()
    company_etf = _FMP_SECTOR_TO_ETF.get(company_sector) if company_sector else None
    item["company_sector_etf"] = company_etf
    # For sector_theme_leaders: check if company sector is one of the leading sectors
    market_leaders = set(item.get("leading_sector_etfs", []))
    if market_leaders:
        item["company_in_leading_sector"] = (
            company_etf in market_leaders if company_etf is not None else None
        )

    item["scores"] = _derive_scores(item)
    item["data_freshness"] = {
        "price_parquet_as_of": _parquet_mtime(sym),
        "spy_parquet_as_of": _parquet_mtime("SPY"),
        "fmp_profile_available": profile is not None,
        "report_generated_at": datetime.now(timezone.utc).isoformat(),
    }
    return item


def _batch_fmp_profiles(tickers: List[str]) -> Dict[str, Optional[Dict[str, Any]]]:
    """Fetch FMP company profiles for a batch of tickers (offline-safe)."""
    if _is_offline_fmp():
        return {t: None for t in tickers}
    profiles: Dict[str, Optional[Dict[str, Any]]] = {}
    for t in tickers:
        try:
            from core.fmp_client import get_fmp
            p = get_fmp().get_company_profile(t)
            profiles[t] = p if isinstance(p, dict) else None
        except Exception:
            profiles[t] = None
    return profiles


# ── Watchlist score ──────────────────────────────────────────────────────────


def _watchlist_score(
    rs_63: Optional[float],
    vol_trend: Optional[float],
    dd_from_high: Optional[float],
    above_ma50: Optional[bool],
    above_ma200: Optional[bool],
    fundamental_ok: Optional[bool],
    has_catalyst: bool,
    has_social: bool,
    crowded: bool,
    is_speculative: bool,
) -> Tuple[float, str]:
    """
    Compute a 0-100 research score and return (score, label).
    Score is NOT a trade signal — it ranks research priority only.
    """
    if crowded:
        return 20.0, "CROWDED"

    score = 50.0

    # RS contribution
    if rs_63 is not None:
        if rs_63 > 10:
            score += 15
        elif rs_63 > 3:
            score += 8
        elif rs_63 < -15:
            score -= 15
        elif rs_63 < -5:
            score -= 8

    # Volume trend
    if vol_trend is not None:
        if vol_trend > 1.3:
            score += 8
        elif vol_trend > 1.1:
            score += 4
        elif vol_trend < 0.7:
            score -= 5

    # Drawdown (beaten-down recovery signal)
    if dd_from_high is not None:
        if dd_from_high < -40:
            score -= 5  # deep hole; needs more evidence
        elif -35 < dd_from_high < -20 and above_ma50:
            score += 10  # potential recovery

    # MA position
    if above_ma200:
        score += 5
    elif above_ma200 is False:
        score -= 5
    if above_ma50:
        score += 5
    elif above_ma50 is False:
        score -= 3

    # Catalyst
    if has_catalyst:
        score += 8

    # Social attention
    if has_social and not crowded:
        score += 5

    # Fundamental quality
    if fundamental_ok is True:
        score += 5
    elif fundamental_ok is False:
        score -= 5

    score = max(0.0, min(100.0, score))

    # Map to label
    if is_speculative:
        # market_cap not available here; default to ASYMMETRIC_RECOVERY_WATCH
        label = "ASYMMETRIC_RECOVERY_WATCH"
    elif not has_social:
        if score >= 70:
            label = "RESEARCH"
        elif score >= 55:
            label = "WATCH"
        else:
            label = "AVOID" if score < 35 else "WATCH"
    else:
        label = "SOCIAL_ARB" if score >= 50 else "RISKY"

    return round(score, 1), label


# ── Scanner category 1: Early Accumulation ──────────────────────────────────


def scan_early_accumulation(
    universe: List[str],
    spy_closes: List[float],
    max_results: int = 15,
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for sym in universe:
        if sym in _KNOWN_ETFS:
            continue
        df = _load_cached_frame(sym)
        if df is None:
            continue
        s = _closes(df)
        v = _volumes(df)
        if len(s) < 60:
            continue

        rs_63 = _rs_vs_spy(s, spy_closes, 63)
        rs_20 = _rs_vs_spy(s, spy_closes, 20)
        vol_tr = _vol_trend(v)
        hl = _higher_lows(s, 30)
        last = _last(s)
        ma50 = _ma(s, 50)
        ma200 = _ma(s, 200)
        dd = _drawdown_from_high(s)

        # Early accumulation criteria:
        # - RS vs SPY improving (not deeply negative)
        # - Volume trending up
        # - Higher lows OR approaching key MA from below
        # - Not extremely extended (not >30% above 52w low)
        if rs_63 is None:
            continue
        if rs_63 < -20:  # deeply lagging — skip
            continue
        vol_rising = vol_tr is not None and vol_tr > 1.05
        if not (vol_rising or hl):
            continue
        if dd is not None and dd < -60:  # catastrophic loss — not accumulation
            continue

        above_ma50 = last > ma50 if (last and ma50) else None
        above_ma200 = last > ma200 if (last and ma200) else None

        score, label = _watchlist_score(
            rs_63=rs_63, vol_trend=vol_tr, dd_from_high=dd,
            above_ma50=above_ma50, above_ma200=above_ma200,
            fundamental_ok=None, has_catalyst=False,
            has_social=False, crowded=False, is_speculative=False,
        )
        label = "EARLY_ACCUMULATION"

        results.append({
            "ticker": sym,
            "category": "early_accumulation",
            "watchlist_label": label,
            "research_score": score,
            "rs_63d_vs_spy": round(rs_63, 2) if rs_63 is not None else None,
            "rs_20d_vs_spy": round(rs_20, 2) if rs_20 is not None else None,
            "vol_trend_ratio": round(vol_tr, 2) if vol_tr is not None else None,
            "higher_lows": hl,
            "above_ma50": above_ma50,
            "above_ma200": above_ma200,
            "dd_from_high_pct": round(dd, 2) if dd is not None else None,
            "trust_level": TRUST_MEDIUM,
            "data_source": "price_cache",
            "refresh_cadence": "daily_nightly",
            "why_appeared": "Rising volume + improving RS or higher lows; not extended",
            "confirms_if": "RS continues rising, volume expands on up-days, reclaims 50d MA",
            "invalidates_if": "Volume dries up, RS reverses, undercuts recent lows",
            "no_trade_recommendation": True,
        })

    results.sort(key=lambda x: x["research_score"], reverse=True)
    return results[:max_results]


# ── Scanner category 2: Beaten-Down Recovery ────────────────────────────────


def scan_beaten_down(
    universe: List[str],
    spy_closes: List[float],
    max_results: int = 15,
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for sym in universe:
        if sym in _KNOWN_ETFS:
            continue
        df = _load_cached_frame(sym)
        if df is None:
            continue
        s = _closes(df)
        v = _volumes(df)
        if len(s) < 60:
            continue

        # Large drawdown required
        dd_3m = _ret(s, 63)
        dd_6m = _ret(s, 126)
        dd_12m = _ret(s, 252) if len(s) >= 253 else None
        dd_from_high = _drawdown_from_high(s)

        if dd_3m is None:
            continue
        # Need at least one of: 3m<-20%, 6m<-25%, 12m<-30%
        deep_dd = (
            (dd_3m is not None and dd_3m < -20) or
            (dd_6m is not None and dd_6m < -25) or
            (dd_12m is not None and dd_12m < -30)
        )
        if not deep_dd:
            continue

        last = _last(s)
        ma50 = _ma(s, 50)
        ma200 = _ma(s, 200)
        vol_tr = _vol_trend(v)
        rs_20 = _rs_vs_spy(s, spy_closes, 20)
        rs_63 = _rs_vs_spy(s, spy_closes, 63)

        # Stabilization signal: recent 10d vol < recent 20d vol (narrowing range)
        recent_10d = s[-10:] if len(s) >= 10 else s
        recent_20d = s[-20:] if len(s) >= 20 else s
        range_10d = (max(recent_10d) - min(recent_10d)) / min(recent_10d) if min(recent_10d) > 0 else 0
        range_20d = (max(recent_20d) - min(recent_20d)) / min(recent_20d) if min(recent_20d) > 0 else 0
        stabilizing = range_10d < range_20d * 0.8

        # Reclaiming 50d MA = positive sign
        reclaiming_ma50 = last and ma50 and last > ma50 * 0.97

        above_ma50 = last > ma50 if (last and ma50) else None
        above_ma200 = last > ma200 if (last and ma200) else None

        # Skip deeply broken + no stabilization
        if not stabilizing and not reclaiming_ma50:
            continue

        score, _ = _watchlist_score(
            rs_63=rs_63, vol_trend=vol_tr, dd_from_high=dd_from_high,
            above_ma50=above_ma50, above_ma200=above_ma200,
            fundamental_ok=None, has_catalyst=False,
            has_social=False, crowded=False, is_speculative=False,
        )
        label = "BEATEN_DOWN"

        results.append({
            "ticker": sym,
            "category": "beaten_down_recovery",
            "watchlist_label": label,
            "research_score": score,
            "dd_3m_pct": round(dd_3m, 2) if dd_3m is not None else None,
            "dd_6m_pct": round(dd_6m, 2) if dd_6m is not None else None,
            "dd_12m_pct": round(dd_12m, 2) if dd_12m is not None else None,
            "dd_from_high_pct": round(dd_from_high, 2) if dd_from_high is not None else None,
            "stabilizing": stabilizing,
            "reclaiming_ma50": bool(reclaiming_ma50),
            "above_ma50": above_ma50,
            "above_ma200": above_ma200,
            "vol_trend_ratio": round(vol_tr, 2) if vol_tr is not None else None,
            "rs_20d_vs_spy": round(rs_20, 2) if rs_20 is not None else None,
            "trust_level": TRUST_MEDIUM,
            "data_source": "price_cache",
            "refresh_cadence": "daily_nightly",
            "why_appeared": f"Large drawdown ({dd_3m:.0f}%/3m) with stabilization pattern",
            "confirms_if": "Price reclaims 50d MA on volume, RS turns positive, catalytic news",
            "invalidates_if": "New lows, accelerating selling, fundamental deterioration",
            "no_trade_recommendation": True,
        })

    results.sort(key=lambda x: x["research_score"], reverse=True)
    return results[:max_results]


# ── Scanner category 3: Sector / Theme Leaders ──────────────────────────────


def scan_sector_leaders(
    universe: List[str],
    spy_closes: List[float],
    max_results: int = 20,
) -> List[Dict[str, Any]]:
    """Find top RS names within the leading sectors."""
    from core.regime_forecaster import SECTOR_ETFS, SECTOR_NAMES, DEFENSIVE_SECTORS

    # Find leading sectors (top 4 by 20d RS vs SPY)
    sector_ranks: List[Tuple[str, float]] = []
    for etf in SECTOR_ETFS:
        s = _closes(_load_cached_frame(etf))
        rs = _rs_vs_spy(s, spy_closes, 20)
        if rs is not None:
            sector_ranks.append((etf, rs))
    sector_ranks.sort(key=lambda x: x[1], reverse=True)
    leading_etfs = {etf for etf, rs in sector_ranks[:4] if rs > 0}

    if not leading_etfs:
        return []

    results: List[Dict[str, Any]] = []
    for sym in universe:
        if sym in _KNOWN_ETFS or sym in leading_etfs:
            continue
        df = _load_cached_frame(sym)
        if df is None:
            continue
        s = _closes(df)
        if len(s) < 63:
            continue

        rs_20 = _rs_vs_spy(s, spy_closes, 20)
        rs_63 = _rs_vs_spy(s, spy_closes, 63)
        if rs_20 is None or rs_20 < 2:
            continue

        last = _last(s)
        ma50 = _ma(s, 50)
        ma200 = _ma(s, 200)
        above_ma50 = last > ma50 if (last and ma50) else None
        above_ma200 = last > ma200 if (last and ma200) else None
        vol_tr = _vol_trend(_volumes(df))

        score, _ = _watchlist_score(
            rs_63=rs_63, vol_trend=vol_tr, dd_from_high=_drawdown_from_high(s),
            above_ma50=above_ma50, above_ma200=above_ma200,
            fundamental_ok=None, has_catalyst=False,
            has_social=False, crowded=False, is_speculative=False,
        )
        label = "SECTOR_LEADER"

        # Detect if extended (>20% above 200d MA)
        extension = None
        if last and ma200:
            extension = (last / ma200 - 1.0) * 100.0
        if extension is not None and extension > 20:
            label = "EXTENDED"

        results.append({
            "ticker": sym,
            "category": "sector_theme_leader",
            "watchlist_label": label,
            "research_score": score,
            "rs_20d_vs_spy": round(rs_20, 2),
            "rs_63d_vs_spy": round(rs_63, 2) if rs_63 is not None else None,
            "above_ma50": above_ma50,
            "above_ma200": above_ma200,
            "extension_vs_ma200_pct": round(extension, 1) if extension is not None else None,
            "vol_trend_ratio": round(vol_tr, 2) if vol_tr is not None else None,
            "leading_sector_etfs": sorted(leading_etfs),
            "trust_level": TRUST_HIGH,
            "data_source": "price_cache",
            "refresh_cadence": "daily_nightly",
            "why_appeared": (
                f"Outperforming SPY by +{rs_20:.1f}pp over 20d"
                + ("; above 50d MA" if above_ma50 else "; below 50d MA — pullback phase")
            ),
            "confirms_if": "RS sustains, sector ETF stays in leadership, volume confirms",
            "invalidates_if": "RS reverses, sector rotates out, loses leading sector membership",
            "no_trade_recommendation": True,
        })

    results.sort(key=lambda x: x["research_score"], reverse=True)
    return results[:max_results]


# ── Scanner category 4: Catalyst Watch ──────────────────────────────────────


def scan_catalyst_watch(
    universe: List[str],
    offline: bool = False,
    max_results: int = 20,
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    now_utc = datetime.now(timezone.utc)

    # Load earnings calendar once
    earnings_by_ticker: Dict[str, str] = {}
    if not offline and not _is_offline_fmp():
        calendar = _fmp_earnings_calendar(days_ahead=21)
        for item in calendar:
            t = (item.get("symbol") or "").upper()
            d = item.get("date") or item.get("reportDate") or ""
            if t and d:
                earnings_by_ticker[t] = d

    for sym in universe:
        if sym in _KNOWN_ETFS:
            continue
        df = _load_cached_frame(sym)
        s = _closes(df) if df is not None else []

        has_earnings = sym in earnings_by_ticker
        earnings_date = earnings_by_ticker.get(sym)

        # Earnings proximity
        days_to_earnings: Optional[int] = None
        if earnings_date:
            try:
                ed = datetime.fromisoformat(earnings_date).replace(tzinfo=timezone.utc)
                days_to_earnings = (ed - now_utc).days
            except Exception:
                pass

        has_analyst_upgrade = False
        analyst_action = None
        if not offline and not _is_offline_fmp() and has_earnings:
            grades = _fmp_analyst_grades(sym)
            for g in grades[:3]:
                action = (g.get("action") or g.get("newGrade") or "").lower()
                if "upgrade" in action or "buy" in action or "strong" in action:
                    has_analyst_upgrade = True
                    analyst_action = g.get("action") or g.get("newGrade")
                    break

        if not has_earnings and not has_analyst_upgrade:
            continue

        last = _last(s)
        rs_20 = None
        if len(s) >= 21:
            spy = _closes(_load_cached_frame("SPY"))
            rs_20 = _rs_vs_spy(s, spy, 20)

        # Recent pre-earnings run-up (risk)
        run_up = _ret(s, 10) if len(s) >= 11 else None
        extended_into_earnings = run_up is not None and run_up > 12

        score = 60.0
        if has_analyst_upgrade:
            score += 10
        if days_to_earnings is not None and 0 <= days_to_earnings <= 7:
            score += 8
        if extended_into_earnings:
            score -= 10

        label = "CATALYST"
        if extended_into_earnings:
            label = "RISKY"

        results.append({
            "ticker": sym,
            "category": "catalyst_watch",
            "watchlist_label": label,
            "research_score": round(min(100.0, score), 1),
            "earnings_date": earnings_date,
            "days_to_earnings": days_to_earnings,
            "has_analyst_upgrade": has_analyst_upgrade,
            "analyst_action": analyst_action,
            "run_up_10d_pct": round(run_up, 2) if run_up is not None else None,
            "extended_into_earnings": extended_into_earnings,
            "rs_20d_vs_spy": round(rs_20, 2) if rs_20 is not None else None,
            "trust_level": TRUST_HIGH if not _is_offline_fmp() else TRUST_LOW,
            "data_source": "fmp_earnings_calendar" if not _is_offline_fmp() else "offline_no_data",
            "refresh_cadence": "daily_premarket",
            "why_appeared": f"Upcoming earnings ({earnings_date})" + (
                f"; analyst {analyst_action}" if analyst_action else ""),
            "confirms_if": "Guidance raised, strong beat, volume expands post-earnings",
            "invalidates_if": "Miss + guide down, volume collapses, extended into print",
            "no_trade_recommendation": True,
        })

    results.sort(key=lambda x: (x["days_to_earnings"] or 99, -x["research_score"]))
    return results[:max_results]


# ── Scanner category 5: Social Arb / Attention Anomaly ──────────────────────


def scan_social_arb(
    universe: List[str],
    max_results: int = 15,
) -> List[Dict[str, Any]]:
    social_data = _load_social_data()
    results: List[Dict[str, Any]] = []
    seen_viral: List[str] = []

    for sym in universe:
        if sym in _KNOWN_ETFS:
            continue
        entry = social_data.get(sym)
        if entry is None:
            continue

        crowded = bool(entry.get("crowded"))
        score = float(entry.get("score") or 0)
        label_raw = (entry.get("label") or "").upper()
        source = entry.get("source", "unknown")

        if crowded:
            seen_viral.append(sym)
            label = "CROWDED"
        elif score > 0.6:
            label = "SOCIAL_ARB"
        elif score > 0.3:
            label = "WATCH"
        else:
            label = "RISKY"

        df = _load_cached_frame(sym)
        s = _closes(df) if df is not None else []
        spy = _closes(_load_cached_frame("SPY"))
        rs_20 = _rs_vs_spy(s, spy, 20) if len(s) >= 21 and spy else None
        vol_tr = _vol_trend(_volumes(df)) if df is not None else None

        research_score = min(100.0, 50.0 + score * 40 - (10 if crowded else 0))

        results.append({
            "ticker": sym,
            "category": "social_arb_attention",
            "watchlist_label": label,
            "research_score": round(research_score, 1),
            "social_score": round(score, 3),
            "social_source": source,
            "crowded": crowded,
            "already_viral": crowded,
            "rs_20d_vs_spy": round(rs_20, 2) if rs_20 is not None else None,
            "vol_trend_ratio": round(vol_tr, 2) if vol_tr is not None else None,
            "trust_level": TRUST_MEDIUM,
            "data_source": source,
            "refresh_cadence": "daily_post_close",
            "social_data_fabricated": False,
            "why_appeared": f"Social attention signal (source: {source})" + (
                " — ALREADY VIRAL/CROWDED" if crowded else ""),
            "confirms_if": "Early attention + price not yet extended + fundamental support",
            "invalidates_if": "Already widely discussed (CROWDED), price fully extended",
            "no_trade_recommendation": True,
        })

    # Add NO_SOCIAL_DATA entries for universe members with no social data
    no_data_count = 0
    for sym in universe[:20]:  # sample only first 20
        if sym in _KNOWN_ETFS:
            continue
        if sym not in social_data and no_data_count < 3:
            results.append({
                "ticker": sym,
                "category": "social_arb_attention",
                "watchlist_label": "NO_SOCIAL_DATA",
                "research_score": 0.0,
                "social_data_available": False,
                "social_data_fabricated": False,
                "trust_level": TRUST_NO_DATA,
                "data_source": "none",
                "refresh_cadence": "daily_post_close",
                "why_appeared": "Included in universe; no social data available",
                "confirms_if": "Social signal appears in next refresh",
                "invalidates_if": "N/A — no data",
                "no_trade_recommendation": True,
            })
            no_data_count += 1

    results.sort(key=lambda x: (0 if x["watchlist_label"] == "NO_SOCIAL_DATA" else 1,
                                 -x["research_score"]))
    return results[:max_results]


# ── Scanner category 6: Long-Term Asymmetric Watch ──────────────────────────

SPECULATIVE_THEMES = {
    "semiconductor", "ai", "artificial intelligence", "defense", "aerospace",
    "space", "energy", "biotech", "biotechnology", "software", "cloud",
    "cybersecurity", "electric vehicle", "autonomy", "robotics", "quantum",
}


def scan_asymmetric(
    universe: List[str],
    spy_closes: List[float],
    offline: bool = False,
    max_results: int = 15,
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []

    for sym in universe:
        if sym in _KNOWN_ETFS:
            continue
        df = _load_cached_frame(sym)
        if df is None:
            continue
        s = _closes(df)
        if len(s) < 60:
            continue

        last = _last(s)
        if last is None:
            continue

        # Compute price signals first — cheap, no provider calls.
        # This lets us exit early before FMP calls for deeply lagging names.
        rs_63 = _rs_vs_spy(s, spy_closes, 63)
        rs_252 = _rs_vs_spy(s, spy_closes, 252) if len(s) >= 253 else None
        dd = _drawdown_from_high(s)
        vol_tr = _vol_trend(_volumes(df))

        has_price_momentum = rs_63 is not None and rs_63 > 5

        # Skip deeply lagging names before any FMP call — no momentum and unlikely
        # to carry a speculative theme worth researching at scale.
        if rs_63 is not None and rs_63 < -30:
            continue

        # Fetch fundamentals (served from data_gatekeeper cache on repeat nightly calls).
        fundamentals: Optional[Dict[str, Any]] = None
        if not offline and not _is_offline_fmp():
            fundamentals = _fmp_fundamentals(sym)

        market_cap = None
        revenue_growth = None
        in_speculative_theme = False
        survivability_ok = None

        if fundamentals:
            market_cap = fundamentals.get("market_cap") or fundamentals.get("marketCap")
            # Revenue growth check
            rev = fundamentals.get("revenue")
            prev_rev = fundamentals.get("revenue_prior")
            if rev and prev_rev and prev_rev > 0:
                revenue_growth = (rev / prev_rev - 1.0) * 100.0
            # Theme check
            desc = ((fundamentals.get("description") or "") +
                    " " + (fundamentals.get("sector") or "") +
                    " " + (fundamentals.get("industry") or "")).lower()
            in_speculative_theme = any(t in desc for t in SPECULATIVE_THEMES)
            # Survivability: positive cash or small debt
            debt = fundamentals.get("total_debt") or 0
            cash = fundamentals.get("cash") or 0
            survivability_ok = cash > debt * 0.5 if (cash or debt) else None

        has_theme = in_speculative_theme
        if not has_price_momentum and not has_theme:
            continue

        score = 50.0
        if has_price_momentum:
            score += 15
        if has_theme:
            score += 10
        if revenue_growth is not None and revenue_growth > 20:
            score += 10
        if survivability_ok is True:
            score += 5
        if survivability_ok is False:
            score -= 10
        if dd is not None and dd < -50:
            score -= 5

        small_cap = market_cap is not None and market_cap < 5_000_000_000
        large_dd = dd is not None and dd < -40
        rs_recovering = rs_63 is not None and rs_63 > 0 and rs_63 < 40
        if has_theme and small_cap and large_dd and rs_recovering:
            label = "TRUE_10X_RESEARCH"
        else:
            label = "ASYMMETRIC_RECOVERY_WATCH"
        if score < 45:
            label = "RISKY"

        results.append({
            "ticker": sym,
            "category": "long_term_asymmetric",
            "watchlist_label": label,
            "research_score": round(min(100.0, max(0.0, score)), 1),
            "rs_63d_vs_spy": round(rs_63, 2) if rs_63 is not None else None,
            "rs_252d_vs_spy": round(rs_252, 2) if rs_252 is not None else None,
            "dd_from_high_pct": round(dd, 2) if dd is not None else None,
            "vol_trend_ratio": round(vol_tr, 2) if vol_tr is not None else None,
            "market_cap": market_cap,
            "revenue_growth_pct": round(revenue_growth, 1) if revenue_growth is not None else None,
            "in_speculative_theme": in_speculative_theme,
            "survivability_ok": survivability_ok,
            "trust_level": TRUST_SPECULATIVE,
            "data_source": "price_cache_and_fmp_fundamentals",
            "refresh_cadence": "weekly",
            "why_appeared": "Speculative growth theme + price momentum; requires manual research",
            "confirms_if": "Revenue growth accelerates, expanding gross margin, theme tailwind",
            "invalidates_if": "Revenue decelerates, balance sheet stress, theme fades",
            "speculative_disclaimer": "Clearly speculative; multi-year thesis required; high risk",
            "no_trade_recommendation": True,
        })

    results.sort(key=lambda x: x["research_score"], reverse=True)
    return results[:max_results]


# ── Scanner category 7: RS Momentum Leaders ─────────────────────────────────


def scan_rs_momentum_leaders(
    universe: List[str],
    spy_closes: List[float],
    max_results: int = 25,
) -> List[Dict[str, Any]]:
    """
    Find names with strong price momentum vs SPY — no MA position gate.

    This is the high-recall lane: Phase 1G.5 Scanner Truth Review found that a
    simple RS-rank catches 24.4% of forward winners vs 1.6% for the legacy
    pullback-entry funnel. The prior funnel gate (`above_ma50` required) was a
    Voyager entry condition — not a research filter. This lane removes it.

    Criteria (all must hold):
      - rs_20d > 5pp vs SPY  OR  rs_63d > 10pp vs SPY
      - last price >= $5
      - avg 30d daily dollar volume >= $1M
      - enough bars: at least 21 for rs_20d, or 64 for rs_63d

    No MA50 / MA200 gate.  MA position is noted in output for human context only.

    Sort: combined_rs = rs_63d * 0.6 + rs_20d * 0.4 (higher = stronger momentum).
    Label: RS_MOMENTUM_LEADER
    """
    results: List[Dict[str, Any]] = []

    for sym in universe:
        if sym in _KNOWN_ETFS:
            continue
        df = _load_cached_frame(sym)
        if df is None:
            continue
        s = _closes(df)
        v = _volumes(df)
        n_bars = len(s)

        # Need enough bars for at least one RS window
        if n_bars < 21:
            continue

        last = _last(s)
        if last is None or last < 5.0:
            continue

        # Liquidity floor: avg 30d daily $ vol >= $1M
        lookback_vol = min(30, n_bars)
        if v and len(v) >= lookback_vol:
            avg_dvol = (sum(v[-lookback_vol:]) / lookback_vol) * last
            if avg_dvol < 1_000_000:
                continue
        elif not v:
            continue

        rs_20 = _rs_vs_spy(s, spy_closes, 20) if n_bars >= 21 else None
        rs_63 = _rs_vs_spy(s, spy_closes, 63) if n_bars >= 64 else None

        # At least one RS window must meet the threshold
        strong_20 = rs_20 is not None and rs_20 > 5
        strong_63 = rs_63 is not None and rs_63 > 10
        if not (strong_20 or strong_63):
            continue

        # Combined RS for ranking
        combined_rs = 0.0
        if rs_63 is not None and rs_20 is not None:
            combined_rs = rs_63 * 0.6 + rs_20 * 0.4
        elif rs_63 is not None:
            combined_rs = rs_63
        else:
            combined_rs = rs_20 or 0.0

        ma50 = _ma(s, 50)
        ma200 = _ma(s, 200)
        above_ma50 = last > ma50 if (last and ma50) else None
        above_ma200 = last > ma200 if (last and ma200) else None
        vol_tr = _vol_trend(v)
        dd = _drawdown_from_high(s)

        ma_note = ""
        if above_ma200 is True and above_ma50 is True:
            ma_note = "above MA50 + MA200"
        elif above_ma200 is True and above_ma50 is False:
            ma_note = "above MA200, below MA50 (pullback)"
        elif above_ma200 is False and above_ma50 is True:
            ma_note = "above MA50, below MA200 (early recovery)"
        elif above_ma200 is False and above_ma50 is False:
            ma_note = "below MA50 + MA200"
        else:
            ma_note = "MA position unknown"

        score = min(100.0, max(0.0, 50.0 + combined_rs * 0.8))

        results.append({
            "ticker": sym,
            "category": "rs_momentum_leader",
            "watchlist_label": "RS_MOMENTUM_LEADER",
            "research_score": round(score, 1),
            "combined_rs": round(combined_rs, 2),
            "rs_20d_vs_spy": round(rs_20, 2) if rs_20 is not None else None,
            "rs_63d_vs_spy": round(rs_63, 2) if rs_63 is not None else None,
            "above_ma50": above_ma50,
            "above_ma200": above_ma200,
            "vol_trend_ratio": round(vol_tr, 2) if vol_tr is not None else None,
            "dd_from_high_pct": round(dd, 2) if dd is not None else None,
            "trust_level": TRUST_HIGH,
            "data_source": "price_cache",
            "refresh_cadence": "daily_nightly",
            "why_appeared": (
                f"Strong RS vs SPY: 20d={rs_20:+.1f}pp" if rs_20 is not None else ""
            ) + (
                f", 63d={rs_63:+.1f}pp" if rs_63 is not None else ""
            ) + f" | {ma_note}",
            "confirms_if": "RS continues expanding, volume confirms, price holds above recent pivot",
            "invalidates_if": "RS rolls over, volume dries on up-days, price undercuts pivot low",
            "no_trade_recommendation": True,
        })

    results.sort(key=lambda x: x["combined_rs"], reverse=True)
    return results[:max_results]


# ── Phase 4A.4: per-item catalyst / social sanity ────────────────────────────


def _apply_catalyst_sanity(item: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate catalyst and social signals before they can influence priority.

    Sets three fields on item (never overwrites if already present):
      catalyst_sanity_label  — one of CATALYST_SANITY_LABELS
      catalyst_can_upgrade   — bool; False blocks priority elevation
      catalyst_sanity_issues — list of detected problems

    Called once per item after the central enrichment pass in build_scanner().
    Research-only; never touches governance, orders, or DB.
    """
    category = item.get("category", "")
    ext_state = item.get("extension_state", "NORMAL")
    tape_extended = ext_state in ("EXTENDED", "PARABOLIC")
    confidence = item.get("data_confidence", "UNKNOWN")

    if category == "social_arb_attention":
        result = validate_social_signal(
            social_score=item.get("social_score"),
            crowded=item.get("crowded", False),
            source=item.get("social_source") or item.get("data_source"),
            tape_extended=tape_extended,
            data_confidence=confidence,
        )
        item["catalyst_sanity_label"] = result["label"]
        item["catalyst_can_upgrade"] = result["can_upgrade"]
        item["catalyst_sanity_issues"] = result.get("issues", [])

    elif category == "catalyst_watch":
        # The scanner's FMP data path does not expose headline or published_at,
        # so full freshness validation via validate_catalyst() is not possible.
        # Apply what we can determine from the calendar and tape state.
        extended = item.get("extended_into_earnings", False)
        days = item.get("days_to_earnings")
        has_analyst = item.get("has_analyst_upgrade", False)

        issues: List[str] = []
        if tape_extended:
            issues.append("tape_extended")
        if confidence in ("LOW", "INVALID"):
            issues.append(f"confidence_{confidence}")

        if extended:
            # Run-up into earnings is a disqualifier (RISKY label from scanner)
            label: str = HYPE_CROWDED
            can_upgrade = False
            issues.append("extended_into_earnings")
        elif days is not None and 0 <= days <= 21:
            # Imminent earnings event from live FMP calendar — treat as fresh catalyst
            label = FRESH_COMPANY_SPECIFIC
            can_upgrade = not tape_extended and confidence not in ("LOW", "INVALID")
        elif has_analyst and days is None:
            # Analyst action present but no upcoming earnings date to anchor freshness
            label = NEEDS_MANUAL_SOURCE_CHECK
            can_upgrade = False
            issues.append("no_source_date_for_analyst_action")
        else:
            label = NEEDS_MANUAL_SOURCE_CHECK
            can_upgrade = False
            issues.append("no_imminent_catalyst_event")

        item["catalyst_sanity_label"] = label
        item["catalyst_can_upgrade"] = can_upgrade
        item["catalyst_sanity_issues"] = issues

    # Items from other categories get no catalyst fields (not applicable)
    return item


# ── Main scanner ─────────────────────────────────────────────────────────────


def _ticker_last_bar_date(symbol: str) -> Optional[str]:
    """Last bar date (YYYY-MM-DD) from the merged deep+shallow read, ignoring
    the session/stale gates — used for as-of reporting, not validity."""
    path = PRICE_DIR / f"{symbol.upper()}.parquet"
    deep = DEEP_PRICE_DIR / f"{symbol.upper()}.parquet"
    best: Optional[date] = None
    for p in (path, deep):
        if not p.exists():
            continue
        try:
            idx = pd.read_parquet(p, columns=[]).index
            if len(idx):
                d = pd.to_datetime(idx).max().date()
                best = d if best is None or d > best else best
        except Exception:
            continue
    return str(best) if best else None


def _load_scan_manifest() -> Optional[Dict[str, Any]]:
    """Validated scan-universe manifest written by
    research/scan_universe_manifest.py (cache-only read)."""
    path = RESEARCH_DIR / "scan_universe_manifest_latest.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _scan_integrity(universe_size: int,
                    manifest: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """P0 scan-integrity verdict.  Separates market-data session freshness
    from artifact freshness: a scan is not FRESH merely because it just ran.
    Readiness policy (deterministic, configured thresholds) is shared with
    the manifest builder — research/scan_readiness.py:

      READY    — full same-session alignment (pre-approved dead/unsupported
                 removals excepted).
      DEGRADED — benchmarks aligned; a bounded fraction excluded.
      BLOCKED  — a required benchmark misaligned, or aligned coverage below
                 the configured floor.
      UNGATED  — no session gate set (unit tests / explicit override).
    """
    required = _REQUIRED_SESSION
    benchmark_session = {b: _ticker_last_bar_date(b) for b in ("SPY", "QQQ", "IWM")}
    manifest_fields = {
        "manifest_hash": (manifest or {}).get("universe_hash"),
        "manifest_readiness": (manifest or {}).get("scan_readiness"),
        "manifest_generated_at": (manifest or {}).get("generated_at"),
    }
    if required is None:
        return {"readiness": "UNGATED", "required_market_session": None,
                "benchmark_session": benchmark_session,
                "benchmarks_aligned": None, "session_mismatch_excluded": 0,
                "same_session_coverage_pct": None,
                "readiness_reasons": ["session gate disabled"],
                **manifest_fields}
    aligned = all(v == str(required) for v in benchmark_session.values())
    mism = len(_SESSION_MISMATCH_SKIPPED)
    coverage = (round(100.0 * (universe_size - mism) / universe_size, 1)
                if universe_size else 0.0)
    from research.scan_readiness import readiness_verdict
    readiness, reasons = readiness_verdict(
        universe_size=universe_size, excluded_count=mism,
        benchmarks_aligned=aligned)
    if not aligned:
        reasons = ["benchmark session mismatch: " + ", ".join(
            f"{b}={v}" for b, v in benchmark_session.items()
            if v != str(required))]
    # When the scan consumed a validated manifest, the combined verdict
    # never exceeds the manifest's: names the manifest excluded (e.g.
    # no_bar_for_session pending tombstone confirmation) keep the scan
    # DEGRADED until they are removed under the approved dead-symbol
    # policy — READY is earned, not inherited.
    rank = {"READY": 0, "DEGRADED": 1, "BLOCKED": 2}
    m_readiness = manifest_fields.get("manifest_readiness")
    if m_readiness in rank and rank[m_readiness] > rank.get(readiness, 0):
        readiness = m_readiness
        reasons = list(reasons) + [
            f"manifest readiness {m_readiness}: "
            + "; ".join((manifest or {}).get("readiness_reasons") or [])]
    return {"readiness": readiness,
            "required_market_session": str(required),
            "benchmark_session": benchmark_session,
            "benchmarks_aligned": aligned,
            "session_mismatch_excluded": mism,
            "same_session_coverage_pct": coverage,
            "readiness_reasons": reasons,
            **manifest_fields}


def build_scanner(offline: bool = False, universe_cap: int = DEFAULT_UNIVERSE_CAP) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    logger.info("Research Scanner Engine %s starting (offline=%s)", VERSION, offline)

    # Module-level skip registries are per-run state — reset so repeated
    # in-process builds (tests) don't accumulate.
    _STALE_SKIPPED.clear()
    _SESSION_MISMATCH_SKIPPED.clear()
    _DATA_SUSPECT_SKIPPED.clear()

    # P0 follow-up: consume the VALIDATED scan-universe manifest when one
    # exists for the required session — the exact universe the manifest
    # builder delta-refreshed and revalidated.  The manifest already wrote
    # the universe build log; the artifact stamps the manifest hash so every
    # scan is traceable to the universe + session that produced it.
    # Fallback (no/mismatched manifest, or ungated test runs): build the
    # universe in-process exactly as before.
    manifest = _load_scan_manifest()
    manifest_valid = bool(
        _REQUIRED_SESSION is not None and manifest
        and manifest.get("required_market_session") == str(_REQUIRED_SESSION)
        and manifest.get("final_universe"))
    if manifest_valid:
        universe = list(manifest["final_universe"])[:universe_cap]
        universe_build_info: Dict[str, Any] = {}
        logger.info("Universe: %d tickers (validated manifest %s)",
                    len(universe), manifest.get("universe_hash"))
    else:
        manifest = None
        universe, universe_build_info = _build_universe(cap=universe_cap)
        logger.info("Universe: %d tickers", len(universe))

    spy_df = _load_cached_frame("SPY")
    spy_closes = _closes(spy_df)
    if not spy_closes:
        logger.warning("SPY price data unavailable; RS comparisons will be skipped")

    # Run all 6 categories
    logger.info("Scanning category 1: Early Accumulation")
    early_acc = scan_early_accumulation(universe, spy_closes)

    logger.info("Scanning category 2: Beaten-Down Recovery")
    beaten = scan_beaten_down(universe, spy_closes)

    logger.info("Scanning category 3: Sector/Theme Leaders")
    leaders = scan_sector_leaders(universe, spy_closes)

    logger.info("Scanning category 4: Catalyst Watch")
    catalyst = scan_catalyst_watch(universe, offline=offline)

    logger.info("Scanning category 5: Social Arb / Attention Anomaly")
    social = scan_social_arb(universe)

    logger.info("Scanning category 6: Long-Term Asymmetric Watch")
    asymmetric = scan_asymmetric(universe, spy_closes, offline=offline)

    logger.info("Scanning category 7: RS Momentum Leaders")
    rs_momentum = scan_rs_momentum_leaders(universe, spy_closes)

    # Deduped master watchlist (highest score per ticker) + track all categories
    seen: Dict[str, Dict[str, Any]] = {}
    all_categories_by_ticker: Dict[str, List[str]] = {}
    all_results = early_acc + beaten + leaders + catalyst + social + asymmetric + rs_momentum
    for item in all_results:
        t = item["ticker"]
        all_categories_by_ticker.setdefault(t, []).append(item["category"])
        if t not in seen or item["research_score"] > seen[t]["research_score"]:
            seen[t] = item
    watchlist = sorted(seen.values(), key=lambda x: x["research_score"], reverse=True)

    # Phase 4A.3: Fetch profiles for all watchlist items (not just top 25)
    profile_cache = _batch_fmp_profiles([item["ticker"] for item in watchlist])

    # Phase 4A.3: Central enrichment pass — fills all required technical,
    # liquidity, and metadata fields across all scanner category paths.
    deep_dir = DEEP_PRICE_DIR if DEEP_PRICE_DIR.exists() else None
    for i, item in enumerate(watchlist):
        ticker = item["ticker"]
        item["all_categories"] = sorted(set(
            all_categories_by_ticker.get(ticker, [item.get("category", "")])
        ))
        watchlist[i] = enrich_research_candidate(
            ticker, item, PRICE_DIR, spy_closes,
            profile=profile_cache.get(ticker),
            deep_price_dir=deep_dir,
        )

    # Add sub-component scores and data freshness (legacy helper, now runs after enrichment)
    for i, item in enumerate(watchlist):
        item = _enrich_item(item, profile_cache.get(item["ticker"]))
        item["earliness_label"] = _earliness_label(
            rs_63=item.get("rs_63d_vs_spy"),
            rs_20=item.get("rs_20d_vs_spy"),
            above_ma50=item.get("above_ma50"),
            above_ma200=item.get("above_ma200"),
            dd_from_high_pct=item.get("dd_from_high_pct"),
            vol_trend_ratio=item.get("vol_trend_ratio"),
            extension_vs_ma200_pct=item.get("extension_vs_ma200_pct"),
        )
        item["consensus_label"] = _consensus_label(
            item["all_categories"],
            research_score=item.get("research_score"),
        )
        watchlist[i] = item

    # Phase 4A.4: validate catalyst / social signals before priority assignment
    for i, item in enumerate(watchlist):
        watchlist[i] = _apply_catalyst_sanity(item)

    # P0 per-candidate as-of transparency: every displayed candidate carries
    # its own bar date, the benchmark bar date, and the same-session verdict.
    spy_as_of = _ticker_last_bar_date("SPY")
    for item in watchlist:
        bar_as_of = _ticker_last_bar_date(item["ticker"])
        item["bar_as_of_date"] = bar_as_of
        item["benchmark_as_of_date"] = spy_as_of
        item["same_session"] = (
            bar_as_of is not None and bar_as_of == spy_as_of
            and (_REQUIRED_SESSION is None or bar_as_of == str(_REQUIRED_SESSION)))

    # Label summary
    label_counts: Dict[str, int] = {}
    for item in watchlist:
        lbl = item.get("watchlist_label", "UNKNOWN")
        label_counts[lbl] = label_counts.get(lbl, 0) + 1

    social_available = bool(_load_social_data())

    # Write universe build log before returning
    _write_universe_build_log(universe_build_info)

    out: Dict[str, Any] = {
        "version": VERSION,
        "generated_at": now,
        "system_mode": SYSTEM_MODE,
        "research_only": True,
        "universe_size": len(universe),
        # P0: one canonical market as-of date for the whole scan.
        "market_as_of_date": str(_REQUIRED_SESSION) if _REQUIRED_SESSION else None,
        "required_market_session": str(_REQUIRED_SESSION) if _REQUIRED_SESSION else None,
        "session_mismatch_excluded_count": len(_SESSION_MISMATCH_SKIPPED),
        "session_mismatch_excluded_sample": sorted(_SESSION_MISMATCH_SKIPPED)[:50],
        "scan_universe_manifest_hash": (manifest or {}).get("universe_hash"),
        "scan_integrity": _scan_integrity(len(universe), manifest=manifest),
        "stale_price_skip_days": _MAX_STALE_DAYS,
        "stale_price_skipped_count": len(_STALE_SKIPPED),
        "stale_price_skipped_sample": sorted(_STALE_SKIPPED)[:25],
        "data_suspect_skipped_count": len(_DATA_SUSPECT_SKIPPED),
        "data_suspect_skipped_sample": sorted(_DATA_SUSPECT_SKIPPED)[:25],
        "offline_mode": offline,
        "fmp_available": not _is_offline_fmp(),
        "social_data_available": social_available,
        "watchlist": watchlist,
        "watchlist_size": len(watchlist),
        "label_summary": label_counts,
        "categories": {
            "early_accumulation": early_acc,
            "beaten_down_recovery": beaten,
            "sector_theme_leaders": leaders,
            "catalyst_watch": catalyst,
            "social_arb_attention": social,
            "long_term_asymmetric": asymmetric,
            "rs_momentum_leader": rs_momentum,
        },
        "category_counts": {
            "early_accumulation": len(early_acc),
            "beaten_down_recovery": len(beaten),
            "sector_theme_leaders": len(leaders),
            "catalyst_watch": len(catalyst),
            "social_arb_attention": len(social),
            "long_term_asymmetric": len(asymmetric),
            "rs_momentum_leader": len(rs_momentum),
        },
        "guardrails": {
            "no_trade_recommendation": True,
            "no_buy_sell": True,
            "no_entry_stop_target": True,
            "no_paper_signal": True,
            "alpaca_required": False,
            "social_data_fabricated": False,
            "tradier_execution_disabled": True,
            "manual_review_required": True,
        },
        "allowed_outputs": [
            "worth researching",
            "watch candidate",
            "requires manual review",
            "risk flagged",
            "extended; wait for reset",
            "potential asymmetric candidate",
            "catalyst candidate",
            "speculative long-term watch",
        ],
    }
    return out


# ── Output writers ───────────────────────────────────────────────────────────


def _format_text(s: Dict[str, Any]) -> str:
    lines = [
        RESEARCH_ONLY_BANNER,
        "",
        f"RESEARCH SCANNER  [{s['version']}]",
        f"Generated: {s['generated_at']}",
        f"Universe: {s['universe_size']} tickers  |  FMP: {'yes' if s['fmp_available'] else 'offline'}",
        f"Social data: {'available' if s['social_data_available'] else 'not available'}",
        "",
        "Label Summary:",
    ]
    for lbl, cnt in sorted(s.get("label_summary", {}).items(), key=lambda x: -x[1]):
        lines.append(f"  {lbl:22s} {cnt:3d}")

    lines += ["", "=== TOP WATCHLIST ==="]
    for item in s.get("watchlist", [])[:30]:
        lines.append(
            f"  [{item['watchlist_label']:20s}]  {item['ticker']:6s}  "
            f"score={item['research_score']:5.1f}  cat={item['category']}"
        )

    # Latest-scan candidates by research program: candidates never
    # print as bare tickers — each carries program, holding period,
    # status vs the previous scan, detection reason, lifecycle, and the
    # pre-registered program verdict.  Display boundary only — no
    # scoring or ranking change.
    try:
        from research.latest_scan_programs import (
            build_latest_scan, render_terminal)
        lines += ["", render_terminal(build_latest_scan(scanner_doc=s))]
    except Exception:
        lines.append("  (latest-scan program view unavailable)")

    for cat_key, cat_name in [
        ("early_accumulation", "EARLY ACCUMULATION"),
        ("beaten_down_recovery", "BEATEN-DOWN RECOVERY"),
        ("sector_theme_leaders", "SECTOR / THEME LEADERS"),
        ("catalyst_watch", "CATALYST WATCH"),
        ("social_arb_attention", "SOCIAL ARB / ATTENTION ANOMALY"),
        ("long_term_asymmetric", "LONG-TERM ASYMMETRIC WATCH"),
        ("rs_momentum_leader", "RS MOMENTUM LEADERS"),
    ]:
        items = s["categories"].get(cat_key, [])
        lines += ["", f"=== {cat_name} ({len(items)}) ==="]
        for item in items[:10]:
            lines.append(
                f"  {item['ticker']:6s}  [{item['watchlist_label']:20s}]  "
                f"score={item['research_score']:5.1f}  {item.get('why_appeared','')[:60]}"
            )

    lines += [
        "",
        "--- RESEARCH ONLY — NO TRADE RECOMMENDATIONS ---",
        "--- Manual review required before any action ---",
    ]
    return "\n".join(lines)


def write_outputs(scanner: Dict[str, Any]) -> None:
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    cfg.LOG_DIR.mkdir(parents=True, exist_ok=True)
    SCANNER_JSON.write_text(json.dumps(scanner, indent=2), encoding="utf-8")
    logger.info("wrote %s", SCANNER_JSON)
    SCANNER_TXT.write_text(_format_text(scanner), encoding="utf-8")
    logger.info("wrote %s", SCANNER_TXT)


def write_doc() -> None:
    if DOC_PATH.exists():
        return
    DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    content = """\
# RESEARCH SCANNER ENGINE

**Module:** `research/research_scanner.py`
**Phase:** 4B / 4C
**Mode:** RESEARCH_ONLY — no trade recommendations.

## Purpose

The Research Scanner runs six evidence categories against the cached price
universe and FMP data each day. It surfaces names worth researching — not
names worth trading. The human operator decides whether any scanner result
deserves deeper research, and all subsequent steps are manual.

## Scanner Categories

| # | Category | Data Sources | Trust |
|---|---|---|---|
| 1 | Early Accumulation | Price cache (RS + volume trend + higher lows) | MEDIUM |
| 2 | Beaten-Down Recovery | Price cache (drawdown + stabilization) | MEDIUM |
| 3 | Sector / Theme Leaders | Price cache (RS rank within leading sectors) | HIGH |
| 4 | Catalyst Watch | FMP earnings calendar + analyst grades | HIGH |
| 5 | Social Arb / Attention Anomaly | Social arb/attention sidecars | MEDIUM |
| 6 | Long-Term Asymmetric Watch | Price cache + FMP fundamentals (optional) | SPECULATIVE |

## Watchlist Labels (4C)

| Label | Meaning |
|---|---|
| `WATCH` | Worth monitoring; research phase |
| `RESEARCH` | Higher priority; warrants deeper look |
| `EARLY_ACCUMULATION` | Volume + RS improving; base building |
| `BEATEN_DOWN` | Large drawdown + stabilization signal |
| `SECTOR_LEADER` | Outperforming sector ETF and SPY |
| `CATALYST` | Upcoming earnings or analyst upgrade |
| `SOCIAL_ARB` | Social attention anomaly; early-stage |
| `ASYMMETRIC_RECOVERY_WATCH` | Price/volume recovery signals; theme or fundamental thesis unconfirmed |
| `TRUE_10X_RESEARCH` | Theme + small-cap base + price recovery confirmed; highest speculative research priority |
| `EXTENDED` | Outperforming but stretched vs MA |
| `RISKY` | Signal present but risk flags elevated |
| `AVOID` | No positive signals; multiple flags |
| `CROWDED` | Already viral/widely discussed; risky |
| `NO_SOCIAL_DATA` | Social signal absent; data degraded |

## Guardrails

- No buy/sell/entry/stop/target output
- Social data is never fabricated; degrades to NO_SOCIAL_DATA
- Already-viral names are labeled CROWDED
- Tradier is research-only; no execution
- Alpaca is not required
- Manual review is required before any action

## Outputs

- `cache/research/research_scanner_latest.json`
- `logs/research_scanner_latest.txt`

## Usage

```bash
SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python research/research_scanner.py
GEM_TRADER_SKIP_DOTENV=true .venv/bin/python research/research_scanner.py --offline
./scripts/run_research_cycle.sh research-scanner
```
"""
    DOC_PATH.write_text(content, encoding="utf-8")
    logger.info("wrote %s", DOC_PATH)


# ── CLI ──────────────────────────────────────────────────────────────────────


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Research Scanner Engine (research-only)")
    parser.add_argument("--offline", action="store_true", help="Cache-only; no provider calls")
    parser.add_argument("--cap", type=int, default=DEFAULT_UNIVERSE_CAP,
                        help=f"Universe size cap (default {DEFAULT_UNIVERSE_CAP})")
    parser.add_argument("--print", dest="print_text", action="store_true")
    parser.add_argument("--max-stale-days", type=int, default=7,
                        help="Secondary coarse backstop: skip tickers whose last cached bar is "
                             "older than N calendar days (0 disables). Default 7. The PRIMARY "
                             "validity rule is the same-session gate.")
    parser.add_argument("--session", type=str, default=None,
                        help="Override the required market session (YYYY-MM-DD, testing/replay)")
    parser.add_argument("--no-session-gate", action="store_true", default=False,
                        help="Disable the same-session gate (readiness reports UNGATED)")
    args = parser.parse_args(argv)

    global _MAX_STALE_DAYS, _REQUIRED_SESSION
    _MAX_STALE_DAYS = args.max_stale_days if args.max_stale_days > 0 else None

    # P0 primary validity rule: same completed session (holiday-aware),
    # not calendar age.  --session overrides for testing/replay.
    if args.session:
        _REQUIRED_SESSION = datetime.strptime(args.session, "%Y-%m-%d").date()
    elif args.no_session_gate:
        _REQUIRED_SESSION = None
    else:
        from core.market_session import required_market_session
        _REQUIRED_SESSION = required_market_session()

    print(RESEARCH_ONLY_BANNER)

    scanner = build_scanner(offline=args.offline, universe_cap=args.cap)
    write_outputs(scanner)
    write_doc()

    print(f"\nResearch Scanner complete.")
    print(f"Universe: {scanner['universe_size']} tickers")
    print(f"Watchlist: {scanner['watchlist_size']} unique results")
    print("\nLabel summary:")
    for lbl, cnt in sorted(scanner["label_summary"].items(), key=lambda x: -x[1]):
        print(f"  {lbl:22s}  {cnt}")
    print(f"\nArtifacts: {SCANNER_JSON}")

    if args.print_text:
        print("\n" + _format_text(scanner))


if __name__ == "__main__":
    main()

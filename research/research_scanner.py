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
  CATALYST, SOCIAL_ARB, SPECULATIVE_10X, EXTENDED, RISKY, AVOID,
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
from datetime import datetime, timedelta, timezone
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

VERSION = "RESEARCH_SCANNER_V1"
PRICE_DIR = cfg.CACHE_DIR / "prices"
RESEARCH_DIR = cfg.CACHE_DIR / "research"
SCANNER_JSON = RESEARCH_DIR / "research_scanner_latest.json"
SCANNER_TXT = cfg.LOG_DIR / "research_scanner_latest.txt"
DOC_PATH = ROOT / "docs" / "research" / "RESEARCH_SCANNER_ENGINE.md"

# Scan universe cap: how many tickers to evaluate per category pass
DEFAULT_UNIVERSE_CAP = 200

# Watchlist label set
WATCHLIST_LABELS = frozenset({
    "WATCH",
    "RESEARCH",
    "EARLY_ACCUMULATION",
    "BEATEN_DOWN",
    "SECTOR_LEADER",
    "CATALYST",
    "SOCIAL_ARB",
    "SPECULATIVE_10X",
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


def _load_cached_frame(symbol: str) -> Optional[pd.DataFrame]:
    path = PRICE_DIR / f"{symbol.upper()}.parquet"
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


def _alpha_board_tickers() -> List[str]:
    """Pull tickers from the latest alpha discovery board sidecar."""
    for name in ["alpha_discovery_latest.json", "alpha_discovery_overlay_latest.json"]:
        path = RESEARCH_DIR / name
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text())
            tickers = []
            for item in data.get("candidates", data.get("board", [])):
                t = item.get("ticker") or item.get("symbol")
                if t and isinstance(t, str) and len(t) <= 6:
                    tickers.append(t.upper())
            if tickers:
                logger.info("alpha board: %d tickers", len(tickers))
                return tickers
        except Exception:
            pass
    return []


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
            for item in data.get("candidates", data.get("leads", [])):
                t = (item.get("ticker") or item.get("symbol") or "").upper()
                if t and len(t) <= 6 and t not in tickers:
                    tickers.append(t)
        except Exception:
            pass
    return tickers


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
            for item in data.get("candidates", data.get("leads", [])):
                t = (item.get("ticker") or item.get("symbol") or "").upper()
                if t:
                    social[t] = {
                        "source": name.replace("_latest.json", ""),
                        "trust_level": TRUST_MEDIUM,
                        "refresh_cadence": "daily_post_close",
                        "crowded": item.get("already_viral") or item.get("crowded") or False,
                        "score": item.get("score") or item.get("deterministic_score") or 0,
                        "label": item.get("label") or item.get("news_label") or "",
                    }
        except Exception:
            pass
    return social


def _build_universe(cap: int = DEFAULT_UNIVERSE_CAP) -> List[str]:
    """Assemble scan universe from alpha board + social sidecars + price cache."""
    seen: Set[str] = set()
    universe: List[str] = []

    def add(t: str) -> None:
        u = t.upper().strip()
        if u and u not in seen and len(u) <= 6:
            seen.add(u)
            universe.append(u)

    # Priority 1: alpha board
    for t in _alpha_board_tickers():
        add(t)

    # Priority 2: social arb / attention names
    for t in _social_arb_tickers():
        add(t)

    # Priority 3: price cache (by parquet file existence)
    # Load all parquets up to cap; prefer names with fresh data
    if len(universe) < cap:
        needed = cap - len(universe)
        price_files = sorted(PRICE_DIR.glob("*.parquet"))
        # Exclude ETFs (single-char, known sector/market ETFs)
        import re
        for pf in price_files:
            sym = pf.stem.upper()
            if re.match(r"^[A-Z]{1,5}$", sym) and sym not in seen:
                add(sym)
                needed -= 1
                if needed <= 0:
                    break

    return universe[:cap]


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
        label = "SPECULATIVE_10X"
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
        if sym in leading_etfs:
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

        if not above_ma50:
            continue

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
            "why_appeared": f"Outperforming SPY by +{rs_20:.1f}pp over 20d; above 50d MA",
            "confirms_if": "RS sustains, sector ETF stays in leadership, volume confirms",
            "invalidates_if": "RS reverses, sector rotates out, undercuts 50d MA",
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
        df = _load_cached_frame(sym)
        if df is None:
            continue
        s = _closes(df)
        if len(s) < 60:
            continue

        last = _last(s)
        if last is None:
            continue

        # Focus on small/mid-cap proxies (price < $200 as rough proxy without market cap data)
        # FMP profile has market cap but we avoid per-ticker provider calls in the scanner loop
        # We rely on FMP fundamentals cached data if available.
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

        # Without fundamentals: use price signals as proxy
        rs_63 = _rs_vs_spy(s, spy_closes, 63)
        rs_252 = _rs_vs_spy(s, spy_closes, 252) if len(s) >= 253 else None
        dd = _drawdown_from_high(s)
        vol_tr = _vol_trend(_volumes(df))

        # Skip if no evidence of growth narrative or price strength
        has_price_momentum = rs_63 is not None and rs_63 > 5
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

        label = "SPECULATIVE_10X"
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


# ── Main scanner ─────────────────────────────────────────────────────────────


def build_scanner(offline: bool = False, universe_cap: int = DEFAULT_UNIVERSE_CAP) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    logger.info("Research Scanner Engine %s starting (offline=%s)", VERSION, offline)

    universe = _build_universe(cap=universe_cap)
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

    # Deduped master watchlist (highest score per ticker)
    seen: Dict[str, Dict[str, Any]] = {}
    all_results = early_acc + beaten + leaders + catalyst + social + asymmetric
    for item in all_results:
        t = item["ticker"]
        if t not in seen or item["research_score"] > seen[t]["research_score"]:
            seen[t] = item
    watchlist = sorted(seen.values(), key=lambda x: x["research_score"], reverse=True)

    # Label summary
    label_counts: Dict[str, int] = {}
    for item in watchlist:
        lbl = item.get("watchlist_label", "UNKNOWN")
        label_counts[lbl] = label_counts.get(lbl, 0) + 1

    social_available = bool(_load_social_data())

    out: Dict[str, Any] = {
        "version": VERSION,
        "generated_at": now,
        "system_mode": SYSTEM_MODE,
        "research_only": True,
        "universe_size": len(universe),
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
        },
        "category_counts": {
            "early_accumulation": len(early_acc),
            "beaten_down_recovery": len(beaten),
            "sector_theme_leaders": len(leaders),
            "catalyst_watch": len(catalyst),
            "social_arb_attention": len(social),
            "long_term_asymmetric": len(asymmetric),
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

    for cat_key, cat_name in [
        ("early_accumulation", "EARLY ACCUMULATION"),
        ("beaten_down_recovery", "BEATEN-DOWN RECOVERY"),
        ("sector_theme_leaders", "SECTOR / THEME LEADERS"),
        ("catalyst_watch", "CATALYST WATCH"),
        ("social_arb_attention", "SOCIAL ARB / ATTENTION ANOMALY"),
        ("long_term_asymmetric", "LONG-TERM ASYMMETRIC WATCH"),
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
| `SPECULATIVE_10X` | Long-term asymmetric thesis; high risk |
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
    args = parser.parse_args(argv)

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

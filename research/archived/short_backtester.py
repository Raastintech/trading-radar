#!/usr/bin/env python3
"""
Standalone SHORT strategy backtester.

Uses the frozen SHORT scoring model plus current research-provider fundamentals
as a point-in-time approximation. This is not a point-in-time fundamental backtest.
It answers whether the current SHORT signal type shows useful behavior on
historical price action.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import hashlib
import os
import platform
import re
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import pandas as pd

try:
    import yfinance as yf
    _HAS_YFINANCE = True
except ImportError:
    yf = None  # type: ignore[assignment]
    _HAS_YFINANCE = False

from enhanced_strategy_scoring import calculate_strategy_score
from fundamental_data_fetcher import FundamentalDataFetcher
from research_data_provider import make_research_providers
from short_scanner_v1 import ShortScanner
from strategy_check_mapper import StrategyCheckMapper

try:
    from secure_env import load_runtime_env
    load_runtime_env()
except Exception:
    pass

try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import AssetClass, AssetStatus
    from alpaca.trading.requests import GetAssetsRequest
except Exception:  # pragma: no cover - import guard for test doubles
    TradingClient = None  # type: ignore
    AssetClass = None  # type: ignore
    AssetStatus = None  # type: ignore
    GetAssetsRequest = None  # type: ignore

try:
    from sector_resolver import resolve_sector_etf
    _HAS_SECTOR_RESOLVER = True
except Exception:  # pragma: no cover - optional dependency in tests
    resolve_sector_etf = None  # type: ignore
    _HAS_SECTOR_RESOLVER = False

FALLBACK_TICKERS: list[str] = [
    "NKE", "LULU", "ETSY", "W", "BBWI", "GPS", "PVH", "HBI", "CPRI", "RL",
    "DECK", "BOOT", "FL", "FIVE", "BYND", "PLUG",
    "INTC", "DELL", "HPQ", "STX", "WDC", "NTAP", "PSTG", "KEYS", "AMAT", "LRCX",
    "MSI", "JNPR", "CSCO", "IBM",
    "PARA", "WBD", "DIS", "FOXA", "IPG", "OMC", "NWSA",
    "BA", "GE", "MMM", "HON", "CAT", "SLB", "HAL", "MPC", "VLO", "OXY",
    "AFRM", "UPST", "LC", "SOFI", "OPEN", "COOP", "PFSI",
    "JNJ", "PFE", "ABBV", "BMY", "MRNA", "BNTX", "MDT", "BSX",
    "MPW", "NYCB", "WPG", "KRE", "OHI",
]
LOSING_SLICE_PATHWAYS: tuple[str, ...] = (
    "GUIDANCE + REVENUE",
    "MARGIN + REVENUE",
    "MARGIN + STRESS",
)
PATHWAYS_OF_INTEREST: tuple[str, ...] = (
    "GUIDANCE + REVENUE",
    "MARGIN + REVENUE",
    "REVENUE only",
    "GUIDANCE + MARGIN + REVENUE",
)
COMMON_STOCK_NAME_BLOCKERS: tuple[str, ...] = (
    " WARRANT",
    " RIGHTS",
    " RIGHTS ",
    " UNIT",
    " UNITS",
    " PFD",
    " PREFERRED",
    " PREFERENCE",
)

SHORT_SCORE_THRESHOLD = 60.0
SHORT_PATHWAY_THRESHOLD = 1
MIN_HISTORY_BARS = 60
DEFAULT_LOOKBACK = 504   # 2 trading years (current fundamentals are reliable ~2y back)
MAX_HOLD_DAYS = 45
DEFAULT_DISCOVERY_LIMIT = 200
DEFAULT_ROLLING_SEED_POOL_SIZE = 400
DEFAULT_REBALANCE_EVERY_BARS = 5
DISCOVERY_MAX_WORKERS = 10
DEFAULT_MIN_RR = 2.5
PRICE_CACHE_TTL_SECONDS = 12 * 60 * 60
FUNDAMENTALS_CACHE_TTL_SECONDS = 24 * 60 * 60
FUNDAMENTALS_CACHE_SCHEMA_VERSION = 2
FUNDAMENTALS_WARM_PAUSE_SECONDS = 0.75
FUNDAMENTALS_RATE_LIMIT_PAUSE_SECONDS = 3.0
SHORT_LOG_PATTERN = re.compile(r"📉 SHORT:\s+([A-Z0-9.\-_]+)")
_LAST_DISCOVERY_METADATA: Optional[Dict[str, Any]] = None
_LAST_ALPACA_METADATA: Dict[str, Any] = {}
_RESEARCH_PROVIDER_SET: Optional[Dict[str, Any]] = None
_ALPACA_SYMBOL_CACHE: dict[str, Any] = {
    "symbols": None,
    "expires_at": 0.0,
}
_SCORING_LOGGER = logging.getLogger("enhanced_strategy_scoring")
CACHE_ROOT = Path("logs") / "short_backtester_cache"


def _safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _research_providers() -> Dict[str, Any]:
    global _RESEARCH_PROVIDER_SET
    if _RESEARCH_PROVIDER_SET is None:
        _RESEARCH_PROVIDER_SET = make_research_providers()
    return _RESEARCH_PROVIDER_SET


def get_research_provider_coverage_report() -> Dict[str, Any]:
    providers = _research_providers()
    return {
        "price": providers["price_coverage"].summary(),
        "earnings": providers["earnings_coverage"].summary(),
        "fundamentals": providers["fundamentals_coverage"].summary(),
        "fmp_budget": providers.get("fmp_budget").summary() if providers.get("fmp_budget") is not None else {},
    }


def _mean(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    return float(sum(values) / len(values))


def _bucket_numeric(
    value: Optional[float],
    *,
    edges: Sequence[float],
    labels: Sequence[str],
    unknown_label: str = "UNKNOWN",
) -> str:
    if value is None:
        return unknown_label
    try:
        v = float(value)
    except (TypeError, ValueError):
        return unknown_label
    if not math.isfinite(v):
        return unknown_label
    for edge, label in zip(edges, labels):
        if v < float(edge):
            return str(label)
    return str(labels[-1]) if labels else unknown_label


def _bucket_market_cap(value: Optional[float]) -> str:
    edges = (2e9, 10e9, 50e9, 200e9, float("inf"))
    labels = ("<2B", "2-10B", "10-50B", "50-200B", "200B+")
    return _bucket_numeric(value, edges=edges, labels=labels)


def _bucket_price(value: Optional[float]) -> str:
    edges = (10.0, 30.0, 100.0, 300.0, float("inf"))
    labels = ("<10", "10-30", "30-100", "100-300", "300+")
    return _bucket_numeric(value, edges=edges, labels=labels)


def _bucket_dollar_volume(value: Optional[float]) -> str:
    edges = (20e6, 50e6, 200e6, 1e9, float("inf"))
    labels = ("<20M", "20-50M", "50-200M", "200M-1B", "1B+")
    return _bucket_numeric(value, edges=edges, labels=labels)


def _bucket_short_interest_pct(value: Optional[float]) -> str:
    edges = (2.0, 5.0, 10.0, 20.0, float("inf"))
    labels = ("<2%", "2-5%", "5-10%", "10-20%", "20%+")
    return _bucket_numeric(value, edges=edges, labels=labels)


def _bucket_borrow_cost_pct(value: Optional[float]) -> str:
    edges = (0.25, 0.5, 1.0, 2.0, float("inf"))
    labels = ("<0.25%", "0.25-0.5%", "0.5-1.0%", "1.0-2.0%", "2.0%+")
    return _bucket_numeric(value, edges=edges, labels=labels)


def _bucket_atr_pct(value: Optional[float]) -> str:
    edges = (2.0, 3.5, 5.0, 8.0, float("inf"))
    labels = ("<2%", "2-3.5%", "3.5-5%", "5-8%", "8%+")
    return _bucket_numeric(value, edges=edges, labels=labels)


def _bucket_gap_risk_pct(value: Optional[float]) -> str:
    edges = (1.0, 3.0, 5.0, 8.0, float("inf"))
    labels = ("<1%", "1-3%", "3-5%", "5-8%", "8%+")
    return _bucket_numeric(value, edges=edges, labels=labels)


def _bucket_earnings_proximity(days_to_earnings: Optional[float]) -> str:
    if days_to_earnings is None:
        return "UNKNOWN"
    try:
        d = int(round(float(days_to_earnings)))
    except (TypeError, ValueError):
        return "UNKNOWN"
    if d <= 7:
        return "0-7d"
    if d <= 21:
        return "8-21d"
    if d <= 45:
        return "22-45d"
    return "45d+"


def _bucket_hold_days(value: Optional[float]) -> str:
    if value is None:
        return "UNKNOWN"
    try:
        d = int(round(float(value)))
    except (TypeError, ValueError):
        return "UNKNOWN"
    if d <= 5:
        return "0-5d"
    if d <= 15:
        return "6-15d"
    if d <= 30:
        return "16-30d"
    if d <= 45:
        return "31-45d"
    return "45d+"


def _calc_atr_pct(frame: pd.DataFrame, *, period: int = 14) -> Optional[float]:
    f = _normalize_price_frame(frame)
    if f.empty or len(f) < period + 1:
        return None
    highs = f["high"].astype(float)
    lows = f["low"].astype(float)
    closes = f["close"].astype(float)
    prev_close = closes.shift(1)
    tr = pd.concat([(highs - lows).abs(), (highs - prev_close).abs(), (lows - prev_close).abs()], axis=1).max(axis=1)
    atr = tr.rolling(period).mean().iloc[-1]
    last_close = closes.iloc[-1]
    if pd.isna(atr) or last_close <= 0:
        return None
    return float(atr / last_close * 100.0)


def _calc_gap_risk_pct(frame: pd.DataFrame, *, lookback: int = 20) -> Optional[float]:
    f = _normalize_price_frame(frame)
    if f.empty or len(f) < 2:
        return None
    tail = f.tail(max(2, int(lookback) + 1))
    prev_close = tail["close"].shift(1)
    gaps = (tail["open"] / prev_close - 1.0) * 100.0
    gaps = gaps.replace([math.inf, -math.inf], pd.NA).dropna()
    if gaps.empty:
        return None
    return float(gaps.max())


def _calc_intraday_range_pct(frame: pd.DataFrame, *, lookback: int = 20) -> Optional[float]:
    f = _normalize_price_frame(frame)
    if f.empty:
        return None
    tail = f.tail(max(1, int(lookback)))
    close = tail["close"]
    rng = ((tail["high"] - tail["low"]) / close) * 100.0
    rng = rng.replace([math.inf, -math.inf], pd.NA).dropna()
    if rng.empty:
        return None
    return float(rng.max())


def _median(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    return float(statistics.median([float(value) for value in values]))


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _normalize_symbol(symbol: Any) -> str:
    return str(symbol).strip().upper().replace(".", "-")


def _score_bucket_label(score: float) -> str:
    if score < 65.0:
        return "60-65"
    if score < 70.0:
        return "65-70"
    if score < 75.0:
        return "70-75"
    return "75+"


def _vix_bucket_label(vix_level: float) -> str:
    if vix_level < 18.0:
        return "<18"
    if vix_level < 22.0:
        return "18-22"
    if vix_level < 28.0:
        return "22-28"
    return "28+"


def _market_regime_label(vix_level: float, benchmark_return_pct: float, benchmark_below_ma50: bool) -> str:
    if benchmark_return_pct <= -2.0 or benchmark_below_ma50:
        return "bearish"
    if benchmark_return_pct >= 2.0 and vix_level < 22.0:
        return "bullish"
    return "neutral"


def _trade_metric(trade: Dict[str, Any], field: str = "effective_return_pct") -> float:
    if field in trade:
        return float(trade.get(field) or 0.0)
    if field == "effective_return_pct":
        return float(trade.get("return_pct") or 0.0)
    return float(trade.get(field) or 0.0)


def _cache_key(value: Any) -> str:
    return re.sub(r"[^A-Z0-9_.-]+", "_", str(value).strip().upper()) or "UNKNOWN"


def _cache_is_fresh(path: Path, ttl_seconds: int) -> bool:
    if not path.exists():
        return False
    try:
        return (time.time() - path.stat().st_mtime) <= float(ttl_seconds)
    except OSError:
        return False


def _price_cache_path(symbol: str, period: str) -> Path:
    return CACHE_ROOT / "price" / _cache_key(period.lower()) / f"{_cache_key(symbol)}.pkl"


def _fundamentals_cache_path(ticker: str) -> Path:
    return CACHE_ROOT / "fundamentals" / f"{_cache_key(ticker)}.json"


def _read_cached_price_frame(symbol: str, period: str, *, allow_stale: bool = False) -> Optional[pd.DataFrame]:
    cache_path = _price_cache_path(symbol, period)
    if not cache_path.exists():
        return None
    if not allow_stale and not _cache_is_fresh(cache_path, PRICE_CACHE_TTL_SECONDS):
        return None
    try:
        return _normalize_price_frame(pd.read_pickle(cache_path))
    except Exception:
        return None


def _write_cached_price_frame(symbol: str, period: str, frame: pd.DataFrame) -> None:
    cache_path = _price_cache_path(symbol, period)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_pickle(cache_path)


def _read_cached_fundamentals(ticker: str, *, allow_stale: bool = False) -> Optional[Dict[str, Any]]:
    cache_path = _fundamentals_cache_path(ticker)
    if not cache_path.exists():
        return None
    if not allow_stale and not _cache_is_fresh(cache_path, FUNDAMENTALS_CACHE_TTL_SECONDS):
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None
        if int(payload.get("_cache_schema_version", 0) or 0) != FUNDAMENTALS_CACHE_SCHEMA_VERSION:
            return None
        normalized = dict(payload)
        normalized.pop("_cache_schema_version", None)
        return normalized
    except Exception:
        return None


def _write_cached_fundamentals(ticker: str, payload: Dict[str, Any]) -> None:
    cache_path = _fundamentals_cache_path(ticker)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    normalized = dict(payload)
    normalized["_cache_schema_version"] = FUNDAMENTALS_CACHE_SCHEMA_VERSION
    cache_path.write_text(json.dumps(normalized, sort_keys=True), encoding="utf-8")


def _dedupe_symbols(symbols: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in symbols:
        symbol = _normalize_symbol(raw)
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        ordered.append(symbol)
    return ordered


def _normalize_price_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    normalized = frame.copy()
    if isinstance(normalized.columns, pd.MultiIndex):
        if len(normalized.columns.levels[0]) == 1:
            normalized.columns = normalized.columns.get_level_values(-1)
        else:
            normalized.columns = normalized.columns.get_level_values(0)

    normalized.columns = [str(col).lower() for col in normalized.columns]
    rename_map = {
        "adj close": "adj_close",
    }
    normalized = normalized.rename(columns=rename_map)
    normalized = normalized.loc[:, ~normalized.columns.duplicated(keep="last")]

    required = ["open", "high", "low", "close", "volume"]
    for column in required:
        if column not in normalized.columns:
            normalized[column] = pd.NA

    normalized.index = pd.to_datetime(normalized.index)
    if getattr(normalized.index, "tz", None) is not None:
        normalized.index = normalized.index.tz_convert(None)
    # Normalize all timestamps to midnight so earnings date lookups match.
    # Alpaca daily bars arrive as "2024-01-15 05:00:00 UTC"; after tz-strip they
    # are "2024-01-15 05:00:00", which won't equal a normalized pd.Timestamp.
    normalized.index = normalized.index.normalize()
    normalized = normalized[~normalized.index.duplicated(keep="last")]
    normalized = normalized.sort_index()
    normalized = normalized[required].dropna(subset=["open", "high", "low", "close"])
    return normalized


def fetch_price_history(ticker: str, period: str = "2y") -> pd.DataFrame:
    cached = _read_cached_price_frame(ticker, period)
    if cached is not None:
        return cached

    providers = _research_providers()
    result = providers["price"].fetch(str(ticker), period=period)
    if result.success and result.value is not None:
        normalized = _normalize_price_frame(result.value)
        try:
            _write_cached_price_frame(ticker, period, normalized)
        except Exception:
            pass
        return normalized

    stale = _read_cached_price_frame(ticker, period, allow_stale=True)
    if stale is not None:
        return stale
    return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])


def fetch_price_history_batch(symbols: Sequence[str], period: str = "5d") -> Dict[str, pd.DataFrame]:
    normalized_symbols = _dedupe_symbols(symbols)
    if not normalized_symbols:
        return {}

    results: Dict[str, pd.DataFrame] = {}
    missing_symbols: list[str] = []
    for symbol in normalized_symbols:
        cached = _read_cached_price_frame(symbol, period)
        if cached is not None:
            results[symbol] = cached
        else:
            missing_symbols.append(symbol)

    if not missing_symbols:
        return results

    providers = _research_providers()
    batch_results = providers["price"].fetch_many(missing_symbols, period=period)
    for symbol in missing_symbols:
        provider_result = batch_results.get(symbol)
        if provider_result is not None and provider_result.success and provider_result.value is not None:
            normalized = _normalize_price_frame(provider_result.value)
            if not normalized.empty:
                results[symbol] = normalized
                try:
                    _write_cached_price_frame(symbol, period, normalized)
                except Exception:
                    pass
                continue
        stale = _read_cached_price_frame(symbol, period, allow_stale=True)
        if stale is not None:
            results[symbol] = stale
    return results


def fetch_vix_history(period: str = "2y") -> pd.DataFrame:
    return fetch_price_history("^VIX", period=period)


def fetch_spy_history(period: str = "2y") -> pd.DataFrame:
    return fetch_price_history("SPY", period=period)


def load_short_fundamentals(ticker: str) -> Dict[str, Any]:
    cached = _read_cached_fundamentals(ticker)
    if cached is not None:
        return cached

    providers = _research_providers()
    result = providers["fundamentals"].fetch(str(ticker))
    if result.success and isinstance(result.value, dict) and result.value:
        payload = dict(result.value)
        try:
            _write_cached_fundamentals(ticker, payload)
        except Exception:
            pass
        return payload

    stale = _read_cached_fundamentals(ticker, allow_stale=True)
    if stale is not None:
        return stale
    return {}


def warm_short_fundamentals_cache(
    tickers: Sequence[str],
    *,
    fundamentals_loader=load_short_fundamentals,
    pause_seconds: float = FUNDAMENTALS_WARM_PAUSE_SECONDS,
    rate_limit_pause_seconds: float = FUNDAMENTALS_RATE_LIMIT_PAUSE_SECONDS,
) -> Dict[str, Dict[str, Any]]:
    warmed: Dict[str, Dict[str, Any]] = {}
    ordered_tickers = _dedupe_symbols(tickers)
    cached_hits = 0
    fetched = 0
    empty_results = 0

    for index, ticker in enumerate(ordered_tickers):
        fresh_cached = _read_cached_fundamentals(ticker)
        if fresh_cached is not None:
            warmed[ticker] = fresh_cached
            cached_hits += 1
            continue

        payload = fundamentals_loader(ticker) or {}
        fetched += 1
        if payload:
            warmed[ticker] = payload
        else:
            stale_cached = _read_cached_fundamentals(ticker, allow_stale=True)
            if stale_cached is not None:
                warmed[ticker] = stale_cached
            empty_results += 1

        if index < len(ordered_tickers) - 1:
            sleep_for = float(rate_limit_pause_seconds if not payload else pause_seconds)
            if sleep_for > 0:
                time.sleep(sleep_for)

    print(
        f"[FUNDAMENTALS] Warmed cache for {len(ordered_tickers)} tickers "
        f"(cached={cached_hits}, fetched={fetched}, empty={empty_results})",
        flush=True,
    )
    return warmed


def evaluate_short_fundamental_fit(ticker: str, fundamentals: Dict[str, Any]) -> Dict[str, Any]:
    margin_guard = ShortScanner._margin_decay_confirmation(fundamentals)
    try:
        revenue_growth_yoy = float(margin_guard.get("revenue_growth_yoy")) if margin_guard.get("revenue_growth_yoy") is not None else None
    except (TypeError, ValueError):
        revenue_growth_yoy = None

    revenue_path = bool(fundamentals.get("revenue_deceleration"))
    debt_stress = bool(fundamentals.get("debt_stress"))
    fcf_negative = bool(fundamentals.get("fcf_negative"))
    guidance_signal = (
        fundamentals.get("guidance_trend") == "cutting"
        or fundamentals.get("estimate_revisions") == "cutting"
    )
    guidance_path = guidance_signal and (revenue_path or debt_stress)
    stress_path = debt_stress or (fcf_negative and ((revenue_growth_yoy is None or revenue_growth_yoy <= 0.15) or revenue_path))
    margin_path = (
        bool(margin_guard.get("margin_decay_confirmed"))
        and bool(fundamentals.get("margin_compression"))
        and bool(fundamentals.get("profit_margin_declining"))
    )
    high_growth_guard_blocked = False
    if margin_path and revenue_growth_yoy is not None and revenue_growth_yoy > 0.15 and not (revenue_path or debt_stress):
        margin_path = False
        high_growth_guard_blocked = True

    available_pathways = {
        "REVENUE": revenue_path,
        "MARGIN": margin_path,
        "GUIDANCE": guidance_path,
        "STRESS": stress_path,
    }
    fit_pathways = [name for name, passed in available_pathways.items() if passed]
    operating_company_like = fundamentals.get("market_cap") is not None and str(fundamentals.get("data_source") or "") != "error"
    fit_score = (
        (3 if revenue_path else 0)
        + (3 if margin_path else 0)
        + (2 if guidance_path else 0)
        + (2 if stress_path else 0)
        + (1 if bool(fundamentals.get("valuation_rich")) else 0)
        - (2 if high_growth_guard_blocked else 0)
    )

    return {
        "ticker": ticker,
        "fit_pathways": fit_pathways,
        "fit_pathway_count": len(fit_pathways),
        "fit_score": fit_score,
        "operating_company_like": operating_company_like,
        "high_growth_guard_blocked": high_growth_guard_blocked,
        "data_quality": fundamentals.get("data_quality"),
    }


def refine_short_candidate_pool(
    tickers: Sequence[str],
    fundamentals_map: Dict[str, Dict[str, Any]],
) -> tuple[list[str], Dict[str, Any]]:
    profiles = [evaluate_short_fundamental_fit(ticker, fundamentals_map.get(ticker) or {}) for ticker in tickers]
    ranked = sorted(
        profiles,
        key=lambda item: (
            -int(item["operating_company_like"]),
            -int(item["fit_pathway_count"]),
            -float(item["fit_score"]),
            tickers.index(item["ticker"]),
        ),
    )
    filtered = [item for item in ranked if item["operating_company_like"] and int(item["fit_pathway_count"]) >= 1]
    selected_profiles = filtered if filtered else ranked
    selected_tickers = [item["ticker"] for item in selected_profiles]
    meta = {
        "operating_company_like": sum(1 for item in profiles if item["operating_company_like"]),
        "fit_candidates": sum(1 for item in profiles if int(item["fit_pathway_count"]) >= 1),
        "selected_count": len(selected_tickers),
        "top_fit": [
            {
                "ticker": item["ticker"],
                "fit_pathways": list(item["fit_pathways"]),
                "fit_score": int(item["fit_score"]),
            }
            for item in selected_profiles[:10]
        ],
    }
    return selected_tickers, meta


def parse_short_log_ticker_counts(log_dir: Path | str = "logs") -> Counter[str]:
    counts: Counter[str] = Counter()
    for path in sorted(Path(log_dir).glob("trader_v3_*.log")):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        counts.update(_normalize_symbol(symbol) for symbol in SHORT_LOG_PATTERN.findall(text))
    return counts


def resolve_default_short_tickers(log_dir: Path | str = "logs", limit: int = 30) -> list[str]:
    try:
        counts = parse_short_log_ticker_counts(log_dir)
        if counts:
            return [ticker for ticker, _count in counts.most_common(limit)]
    except Exception:
        pass
    return list(FALLBACK_TICKERS[:limit])


def _is_symbol_allowed(symbol: str) -> bool:
    return symbol.isalpha() and 1 <= len(symbol) <= 5


def _is_obvious_fund_or_etf(asset: Any) -> bool:
    name = str(getattr(asset, "name", "") or "").upper()
    blockers = (
        " ETF",
        " ETN",
        " TRUST",
        " FUND",
        " SHARES",
        " INDEX",
        " BOND",
        " PROSHARES",
        " ISHARES",
        " DIREXION",
        " SPDR",
        " INVESCO",
        " VANGUARD",
        " ARK ",
        " GLOBAL X",
        " FIRST TRUST",
        " WISDOMTREE",
        " SCHWAB",
        " VANECK",
        " CBOE",
    )
    return any(token in name for token in blockers)


def _is_obvious_non_common_stock(asset: Any) -> bool:
    name = f" {str(getattr(asset, 'name', '') or '').upper()} "
    return any(token in name for token in COMMON_STOCK_NAME_BLOCKERS)


def _chunked(symbols: Sequence[str], chunk_size: int) -> list[list[str]]:
    return [list(symbols[idx : idx + chunk_size]) for idx in range(0, len(symbols), max(1, int(chunk_size)))]


def _log_norm(value: float, lo: float, hi: float) -> float:
    if value <= 0 or hi <= lo:
        return 0.0
    try:
        lv = math.log10(float(value))
    except Exception:
        return 0.0
    return _clip01((lv - lo) / (hi - lo))


def _passes_basic_filters(row: Dict[str, Any]) -> bool:
    price = float(row["price"])
    avg_volume = float(row["avg_volume_20"])
    avg_dollar_volume = float(row["avg_dollar_volume_20"])
    return (
        5.0 <= price <= 1000.0
        and avg_volume >= 300_000.0
        and avg_dollar_volume >= 5_000_000.0
    )


def _short_filter(row: Dict[str, Any]) -> bool:
    return (
        float(row["price"]) >= 8.0
        and float(row["return_20d_pct"]) <= -2.0
        and float(row["close_vs_ma20_pct"]) <= -1.0
        and float(row["dist_to_60d_low_pct"]) <= 12.0
        and float(row["close_position_10"]) <= 0.35
        and float(row["volume_ratio_5d"]) >= 0.8
    )


def _load_alpaca_symbols(
    *,
    log_dir: Path | str = "logs",
    now_ts: Optional[float] = None,
    cache_ttl_seconds: int = 1800,
) -> list[str]:
    global _LAST_ALPACA_METADATA
    current_ts = float(now_ts if now_ts is not None else time.time())
    cached_symbols = _ALPACA_SYMBOL_CACHE.get("symbols")
    expires_at = float(_ALPACA_SYMBOL_CACHE.get("expires_at") or 0.0)
    if cached_symbols and current_ts < expires_at:
        return list(cached_symbols)

    try:
        if TradingClient is None or GetAssetsRequest is None or AssetClass is None or AssetStatus is None:
            raise RuntimeError("Alpaca trading client unavailable")

        api_key = os.getenv("APCA_API_KEY_ID") or os.getenv("ALPACA_API_KEY")
        secret_key = os.getenv("APCA_API_SECRET_KEY") or os.getenv("ALPACA_SECRET_KEY")
        if not api_key or not secret_key:
            raise RuntimeError("Missing APCA_API_KEY_ID/APCA_API_SECRET_KEY or ALPACA_API_KEY/ALPACA_SECRET_KEY")

        credential_source = (
            "APCA_API_KEY_ID/APCA_API_SECRET_KEY"
            if os.getenv("APCA_API_KEY_ID") and os.getenv("APCA_API_SECRET_KEY")
            else "ALPACA_API_KEY/ALPACA_SECRET_KEY"
        )
        print(f"[DISCOVERY] Using Alpaca credentials from {credential_source}", flush=True)

        client = TradingClient(api_key, secret_key, paper=True)
        print("[DISCOVERY] Alpaca credentials loaded successfully", flush=True)
        request = GetAssetsRequest(status=AssetStatus.ACTIVE, asset_class=AssetClass.US_EQUITY)
        assets = client.get_all_assets(filter=request)
        symbols: list[str] = []
        filtered_non_common = 0
        for asset in assets or []:
            symbol = _normalize_symbol(getattr(asset, "symbol", ""))
            if not symbol:
                continue
            if not bool(getattr(asset, "tradable", False)):
                continue
            if not _is_symbol_allowed(symbol):
                continue
            if _is_obvious_fund_or_etf(asset):
                continue
            if _is_obvious_non_common_stock(asset):
                filtered_non_common += 1
                continue
            exchange = str(getattr(asset, "exchange", "") or "").upper()
            if "OTC" in exchange:
                continue
            symbols.append(symbol)

        symbols = sorted(set(symbols))
        _LAST_ALPACA_METADATA = {
            "asset_universe_size": len(list(assets or [])),
            "common_stock_symbols": len(symbols),
            "filtered_non_common": int(filtered_non_common),
        }
        _ALPACA_SYMBOL_CACHE["symbols"] = list(symbols)
        _ALPACA_SYMBOL_CACHE["expires_at"] = current_ts + float(cache_ttl_seconds)
        print(f"[DISCOVERY] Alpaca asset universe loaded ({len(symbols)} symbols)", flush=True)
        return symbols
    except Exception:
        fallback = resolve_default_short_tickers(log_dir=log_dir, limit=len(FALLBACK_TICKERS))
        _LAST_ALPACA_METADATA = {
            "asset_universe_size": 0,
            "common_stock_symbols": len(fallback),
            "filtered_non_common": 0,
        }
        print(f"[DISCOVERY] Alpaca asset fetch failed, using log-parsed fallback ({len(fallback)} symbols)", flush=True)
        return list(fallback)


def _load_market_screen_symbols(log_dir: Path | str = "logs") -> tuple[list[str], str]:
    alpaca_symbols = _load_alpaca_symbols(log_dir=log_dir)
    alpaca_symbol_set = set(alpaca_symbols)
    log_symbols: list[str] = []
    try:
        counts = parse_short_log_ticker_counts(log_dir)
        raw_log_symbols = [ticker for ticker, _count in counts.most_common()]
        if alpaca_symbol_set:
            log_symbols = [ticker for ticker in raw_log_symbols if ticker in alpaca_symbol_set]
        else:
            log_symbols = raw_log_symbols
    except Exception:
        log_symbols = []

    symbols = _dedupe_symbols([*log_symbols, *alpaca_symbols])
    if not symbols:
        symbols = list(FALLBACK_TICKERS)
    return symbols, "Full market screen (Alpaca + log-parsed)"


def _prefilter_liquid_symbols(
    symbols: Sequence[str],
    *,
    batch_price_loader=fetch_price_history_batch,
    limit: int = DEFAULT_ROLLING_SEED_POOL_SIZE,
) -> tuple[list[str], Dict[str, Any]]:
    ranked_rows: list[Dict[str, Any]] = []

    def _load_liquid_batch(chunk: Sequence[str]) -> list[Dict[str, Any]]:
        batch_frames = batch_price_loader(chunk, "5d")
        rows: list[Dict[str, Any]] = []
        for symbol in chunk:
            frame = _normalize_price_frame(batch_frames.get(symbol))
            if frame.empty:
                continue
            last_close = float(frame["close"].iloc[-1] or 0.0)
            avg_volume_5d = float(frame["volume"].tail(min(5, len(frame))).mean() or 0.0)
            avg_dollar_volume_5d = last_close * avg_volume_5d
            if 5.0 <= last_close <= 1000.0 and avg_volume_5d >= 300_000.0:
                rows.append(
                    {
                        "symbol": symbol,
                        "last_close": last_close,
                        "avg_volume_5d": avg_volume_5d,
                        "avg_dollar_volume_5d": avg_dollar_volume_5d,
                    }
                )
        return rows

    with ThreadPoolExecutor(max_workers=DISCOVERY_MAX_WORKERS) as executor:
        futures = [executor.submit(_load_liquid_batch, chunk) for chunk in _chunked(symbols, 100)]
        for future in as_completed(futures):
            try:
                ranked_rows.extend(future.result() or [])
            except Exception:
                continue

    ranked_rows.sort(
        key=lambda row: (
            -float(row["avg_dollar_volume_5d"]),
            -float(row["avg_volume_5d"]),
            row["symbol"],
        )
    )
    selected_rows = ranked_rows[: max(0, int(limit))]
    return [str(row["symbol"]) for row in selected_rows], {
        "liquid_prefilter_count": len(ranked_rows),
        "seed_pool_size": len(selected_rows),
        "top_seed_symbols": [str(row["symbol"]) for row in selected_rows[:10]],
    }


def _preload_price_histories(
    symbols: Sequence[str],
    *,
    period: str = "5y",
    price_loader=fetch_price_history,
) -> Dict[str, pd.DataFrame]:
    histories: Dict[str, pd.DataFrame] = {}

    def _load(symbol: str) -> tuple[str, pd.DataFrame]:
        return symbol, price_loader(symbol, period)

    with ThreadPoolExecutor(max_workers=DISCOVERY_MAX_WORKERS) as executor:
        futures = {executor.submit(_load, symbol): symbol for symbol in _dedupe_symbols(symbols)}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                loaded_symbol, frame = future.result()
            except Exception:
                loaded_symbol, frame = symbol, pd.DataFrame()
            histories[loaded_symbol] = _normalize_price_frame(frame)
    return histories


def _compute_rebalance_dates(
    reference_history: pd.DataFrame,
    *,
    lookback: int,
    step_bars: int = DEFAULT_REBALANCE_EVERY_BARS,
) -> list[pd.Timestamp]:
    frame = _normalize_price_frame(reference_history)
    if frame.empty or len(frame) <= MIN_HISTORY_BARS:
        return []
    start_idx = max(MIN_HISTORY_BARS - 1, len(frame) - int(max(lookback, 1)))
    end_idx = len(frame) - 1
    step = max(1, int(step_bars))
    dates = [pd.Timestamp(frame.index[idx]) for idx in range(start_idx, end_idx, step)]
    final_candidate = pd.Timestamp(frame.index[end_idx - 1])
    if dates and dates[-1] != final_candidate:
        dates.append(final_candidate)
    elif not dates:
        dates = [final_candidate]
    return dates


def _select_historical_short_candidates(
    price_histories: Dict[str, pd.DataFrame],
    as_of_date: pd.Timestamp,
    *,
    limit: int,
    fit_profiles: Optional[Dict[str, Dict[str, Any]]] = None,
) -> tuple[list[str], Dict[str, Any]]:
    rows: list[Dict[str, Any]] = []
    for symbol, history in price_histories.items():
        if history.empty:
            continue
        history_slice = history.loc[history.index <= as_of_date]
        row = _compute_discovery_features(symbol, history_slice)
        if row is None or not _passes_basic_filters(row):
            continue
        profile = (fit_profiles or {}).get(symbol) or {}
        if fit_profiles is not None:
            if not bool(profile.get("operating_company_like")) or int(profile.get("fit_pathway_count", 0)) < 1:
                continue
        row["fit_score"] = float(profile.get("fit_score", 0.0) or 0.0)
        rows.append(row)

    rows.sort(
        key=lambda row: (
            -float(row["base_score"]),
            -float(row["short_score"]),
            -float(row.get("fit_score", 0.0) or 0.0),
            row["symbol"],
        )
    )
    base_universe = rows[:1000]
    passed = [row for row in base_universe if _short_filter(row)]
    passed.sort(
        key=lambda row: (
            -float(row["short_score"]),
            -float(row.get("fit_score", 0.0) or 0.0),
            row["symbol"],
        )
    )
    selected_rows = passed[: max(0, int(limit))]
    return [str(row["symbol"]) for row in selected_rows], {
        "screened_candidates": len(passed),
        "selected_candidates": len(selected_rows),
    }


def _compute_discovery_features(symbol: str, price_history: pd.DataFrame) -> Optional[Dict[str, Any]]:
    frame = _normalize_price_frame(price_history)
    if frame.empty or len(frame) < 21:
        return None

    closes = frame["close"].astype(float)
    highs = frame["high"].astype(float)
    lows = frame["low"].astype(float)
    volumes = frame["volume"].fillna(0.0).astype(float)
    current = float(closes.iloc[-1] or 0.0)
    if current <= 0:
        return None

    lookback20 = min(20, len(frame))
    lookback14 = min(14, len(frame))
    lookback60 = min(60, len(frame))
    lookback10 = min(10, len(frame))

    avg_volume_20 = float(volumes.tail(20).mean() or 0.0)
    avg_dollar_volume_20 = float(current * avg_volume_20)
    return_20d_pct = ((current / float(closes.iloc[-21])) - 1.0) * 100.0 if len(closes) >= 21 and float(closes.iloc[-21]) > 0 else 0.0
    if len(closes) >= 61 and float(closes.iloc[-61]) > 0:
        return_60d_pct = ((current / float(closes.iloc[-61])) - 1.0) * 100.0
    else:
        return_60d_pct = return_20d_pct

    ma20 = float(closes.tail(20).mean() or 0.0)
    ma60_window = closes.tail(min(60, len(closes)))
    ma60 = float(ma60_window.mean() or 0.0)
    close_vs_ma20_pct = ((current / ma20) - 1.0) * 100.0 if ma20 > 0 else 0.0
    close_vs_ma60_pct = ((current / ma60) - 1.0) * 100.0 if ma60 > 0 else close_vs_ma20_pct

    low_60 = float(closes.tail(min(60, len(closes))).min() or 0.0)
    dist_to_60d_low_pct = ((current / low_60) - 1.0) * 100.0 if low_60 > 0 else 0.0
    high_10 = float(highs.tail(lookback10).max() or 0.0)
    low_10 = float(lows.tail(lookback10).min() or 0.0)
    close_position_10 = ((current - low_10) / (high_10 - low_10)) if high_10 > low_10 else 0.5

    recent_volume_5 = float(volumes.tail(min(5, len(volumes))).mean() or 0.0)
    volume_ratio_5d = (recent_volume_5 / avg_volume_20) if avg_volume_20 > 0 else 0.0

    avg_range_14 = float((highs.tail(lookback14) - lows.tail(lookback14)).mean() or 0.0)
    atr_pct_14 = (avg_range_14 / current) * 100.0 if current > 0 else 0.0

    liquidity_factor = _clip01(avg_dollar_volume_20 / 50_000_000.0)
    bearish_trend = _clip01((-return_20d_pct) / 20.0)
    bearish_trend_60 = _clip01((-return_60d_pct) / 30.0)
    below_ma20 = _clip01((-close_vs_ma20_pct) / 10.0)
    below_ma60 = _clip01((-close_vs_ma60_pct) / 15.0)
    activity = _clip01(volume_ratio_5d / 3.0)
    low_proximity_60 = _clip01(1.0 - (dist_to_60d_low_pct / 20.0))
    movement = _clip01((atr_pct_14 - 1.0) / 7.0)
    abs_trend = _clip01(abs(return_20d_pct) / 20.0)
    liquidity = _log_norm(avg_dollar_volume_20, 6.7, 9.7)
    base_score = (
        0.45 * liquidity
        + 0.25 * movement
        + 0.15 * activity
        + 0.15 * abs_trend
    )
    short_score = (
        0.22 * liquidity_factor
        + 0.20 * bearish_trend
        + 0.16 * bearish_trend_60
        + 0.14 * below_ma20
        + 0.10 * below_ma60
        + 0.10 * activity
        + 0.08 * low_proximity_60
    )

    return {
        "symbol": symbol,
        "price": current,
        "avg_volume_20": avg_volume_20,
        "avg_dollar_volume_20": avg_dollar_volume_20,
        "return_20d_pct": return_20d_pct,
        "return_60d_pct": return_60d_pct,
        "close_vs_ma20_pct": close_vs_ma20_pct,
        "close_vs_ma60_pct": close_vs_ma60_pct,
        "dist_to_60d_low_pct": dist_to_60d_low_pct,
        "close_position_10": close_position_10,
        "volume_ratio_5d": volume_ratio_5d,
        "base_score": base_score,
        "short_score": short_score,
    }


def discover_short_candidates(
    limit: int = DEFAULT_DISCOVERY_LIMIT,
    *,
    symbols: Optional[Sequence[str]] = None,
    price_loader=fetch_price_history,
    batch_price_loader=fetch_price_history_batch,
    log_dir: Path | str = "logs",
) -> list[str]:
    global _LAST_DISCOVERY_METADATA

    if symbols is None:
        symbol_list, source = _load_market_screen_symbols(log_dir=log_dir)
    else:
        symbol_list = _dedupe_symbols(symbols)
        source = "Manual discovery symbol set"

    liquid_symbols: list[str] = []

    def _load_liquid_batch(chunk: Sequence[str]) -> list[str]:
        batch_frames = batch_price_loader(chunk, "5d")
        results: list[str] = []
        for symbol in chunk:
            frame = _normalize_price_frame(batch_frames.get(symbol))
            if frame.empty:
                continue
            last_close = float(frame["close"].iloc[-1] or 0.0)
            avg_volume_5d = float(frame["volume"].tail(min(5, len(frame))).mean() or 0.0)
            if 5.0 <= last_close <= 1000.0 and avg_volume_5d >= 300_000.0:
                results.append(symbol)
        return results

    stage1_chunks = _chunked(symbol_list, 100)
    with ThreadPoolExecutor(max_workers=DISCOVERY_MAX_WORKERS) as executor:
        futures = [executor.submit(_load_liquid_batch, chunk) for chunk in stage1_chunks]
        for future in as_completed(futures):
            try:
                liquid_symbols.extend(future.result() or [])
            except Exception:
                continue
    liquid_symbols = _dedupe_symbols(liquid_symbols)

    def _load_symbol_features(symbol: str) -> Optional[Dict[str, Any]]:
        history = price_loader(symbol, "3mo")
        return _compute_discovery_features(symbol, history)

    rows: list[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=DISCOVERY_MAX_WORKERS) as executor:
        futures = {executor.submit(_load_symbol_features, symbol): symbol for symbol in liquid_symbols}
        for future in as_completed(futures):
            try:
                row = future.result()
            except Exception:
                row = None
            if row:
                rows.append(row)

    basic_filtered = [row for row in rows if _passes_basic_filters(row)]
    basic_filtered.sort(key=lambda row: (-float(row["base_score"]), -float(row["short_score"]), row["symbol"]))
    base_universe = basic_filtered[:1000]
    passed = [row for row in base_universe if _short_filter(row)]
    passed.sort(key=lambda row: (-float(row["short_score"]), row["symbol"]))
    selected = [row["symbol"] for row in passed[: max(0, int(limit))]]

    top_10 = [
        {"symbol": row["symbol"], "short_score": round(float(row["short_score"]), 4)}
        for row in passed[:10]
    ]
    _LAST_DISCOVERY_METADATA = {
        "used": True,
        "source": source,
        "symbols_scanned": len(symbol_list),
        "passed_screening": len(passed),
        "candidates_taken": len(selected),
        "top_candidates": top_10,
    }

    print(f"[DISCOVERY] Source: {source}", flush=True)
    print(f"[DISCOVERY] Symbols scanned: {len(symbol_list)}", flush=True)
    print(f"[DISCOVERY] Liquid prefilter: {len(liquid_symbols)}", flush=True)
    print(f"[DISCOVERY] Basic filter top-universe: {len(base_universe)}", flush=True)
    print(f"[DISCOVERY] Passed screening: {len(passed)}", flush=True)
    if top_10:
        formatted = ", ".join(f"{item['symbol']} ({item['short_score']:.3f})" for item in top_10)
        print(f"[DISCOVERY] Top candidates: {formatted}", flush=True)
    return selected


def _rolling_return_series(closes: Sequence[float], window: int) -> list[float]:
    if not closes or len(closes) <= window:
        return []
    series: list[float] = []
    for idx in range(window, len(closes)):
        start = _safe_float(closes[idx - window], 0.0) or 0.0
        end = _safe_float(closes[idx], 0.0) or 0.0
        if start <= 0:
            continue
        series.append(((end / start) - 1.0) * 100.0)
    return series


def compute_relative_weakness(
    ticker: str,
    price_history: pd.DataFrame,
    spy_history: pd.DataFrame,
    lookback: int = 20,
    benchmark_cache: Optional[Dict[str, pd.DataFrame]] = None,
) -> Dict[str, Any]:
    closes = [float(value) for value in price_history["close"].tolist()]
    current = price_history.iloc[-1]
    avg_volume_20 = float(price_history["volume"].tail(min(20, len(price_history))).mean() or 0.0)
    volume_on_decline = (
        float(current["close"]) < float(current["open"])
        and float(current["volume"] or 0.0) >= avg_volume_20 * 1.15
    )
    distribution_days_10 = int(
        (
            (price_history["close"].tail(min(10, len(price_history))) < price_history["open"].tail(min(10, len(price_history))))
            & (price_history["volume"].tail(min(10, len(price_history))) >= (avg_volume_20 * 1.05))
        ).sum()
    )

    benchmark_ticker = "SPY"
    if benchmark_cache is not None and _HAS_SECTOR_RESOLVER and resolve_sector_etf is not None:
        try:
            sector_payload = resolve_sector_etf(ticker) or {}
            benchmark_ticker = (
                sector_payload.get("sector_etf")
                or sector_payload.get("fallback_sector_etf")
                or "SPY"
            )
        except Exception:
            benchmark_ticker = "SPY"

    benchmark_history = spy_history
    if benchmark_ticker != "SPY":
        if benchmark_cache is not None and benchmark_ticker in benchmark_cache:
            benchmark_history = benchmark_cache[benchmark_ticker]
        else:
            benchmark_history = fetch_price_history(benchmark_ticker, "5y")
            if benchmark_cache is not None:
                benchmark_cache[benchmark_ticker] = benchmark_history
        if benchmark_history.empty or len(benchmark_history) < (lookback + 2):
            benchmark_ticker = "SPY"
            benchmark_history = spy_history

    benchmark_closes = [float(value) for value in benchmark_history["close"].tolist()]
    ticker_roll = _rolling_return_series(closes, lookback)
    benchmark_roll = _rolling_return_series(benchmark_closes, lookback)

    ticker_return = 0.0
    benchmark_return = 0.0
    current_excess = 0.0
    excess_zscore = 0.0
    if ticker_roll and benchmark_roll:
        aligned = min(len(ticker_roll), len(benchmark_roll))
        ticker_roll = ticker_roll[-aligned:]
        benchmark_roll = benchmark_roll[-aligned:]
        excess_roll = [t - b for t, b in zip(ticker_roll, benchmark_roll)]
        ticker_return = float(ticker_roll[-1])
        benchmark_return = float(benchmark_roll[-1])
        current_excess = float(excess_roll[-1])
        if len(excess_roll) >= 5:
            excess_series = pd.Series(excess_roll, dtype="float64")
            stdev = float(excess_series.std(ddof=0) or 0.0)
            if stdev > 0:
                excess_zscore = (current_excess - float(excess_series.mean())) / stdev

    benchmark_ma50 = float(benchmark_history["close"].tail(50).mean() or 0.0) if len(benchmark_history) >= 50 else 0.0
    benchmark_below_ma50 = bool(benchmark_ma50 and float(benchmark_history["close"].iloc[-1]) < benchmark_ma50)
    institutional_distribution = (
        distribution_days_10 >= 3
        or (
            current_excess <= -5.0
            and excess_zscore <= -1.5
            and volume_on_decline
        )
    )
    sector_weakness = benchmark_return <= -2.0 or benchmark_below_ma50

    return {
        "benchmark_ticker": benchmark_ticker,
        "ticker_return_pct": round(ticker_return, 2),
        "benchmark_return_pct": round(benchmark_return, 2),
        "relative_strength_pct": round(current_excess, 2),
        "excess_zscore": round(excess_zscore, 2),
        "distribution_days_10": distribution_days_10,
        "volume_on_decline": volume_on_decline,
        "institutional_distribution": institutional_distribution,
        "sector_weakness": sector_weakness,
        "benchmark_below_ma50": benchmark_below_ma50,
        "benchmark_ma50": round(benchmark_ma50, 2) if benchmark_ma50 else None,
    }


def compute_short_trade_levels(
    price_history: pd.DataFrame,
    entry_price: float,
    min_rr: float = DEFAULT_MIN_RR,
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    if len(price_history) < 20 or entry_price <= 0:
        return None, None, None

    frame = _normalize_price_frame(price_history)
    if frame.empty or len(frame) < 20:
        return None, None, None

    prior_20 = frame.iloc[-20:]

    # Structure stop: prior 20-day high + 1.5% buffer
    structure_stop = float(prior_20["high"].max() or 0.0) * 1.015

    # ATR stop: 1.5× ATR(14) above entry — ensures minimum stop separation
    # when the stock has been grinding down without any relief rally
    atr_window = frame.iloc[-14:] if len(frame) >= 14 else frame
    atr_14 = float((atr_window["high"] - atr_window["low"]).mean() or 0.0)
    atr_stop = entry_price + (1.5 * atr_14) if atr_14 > 0 else structure_stop

    # Use the wider stop so there's room for the target to breathe
    stop = max(structure_stop, atr_stop)

    # Cap at 10% above entry — never accept a massive stop on a short
    stop = min(stop, entry_price * 1.10)

    if stop <= entry_price:
        return None, None, None

    support_window = frame.iloc[-60:]["close"]
    support_60d = float(support_window.min() or 0.0) if not support_window.empty else 0.0
    target = support_60d if 0.0 < support_60d < entry_price else entry_price * 0.80
    min_target = entry_price - (float(min_rr) * (stop - entry_price))
    target = min(float(target), float(min_target))

    rr = (entry_price - target) / (stop - entry_price) if stop > entry_price else None
    if target >= entry_price or target <= 0:
        return None, None, None
    if rr is None or rr < float(min_rr) - 1e-9:
        return None, None, None
    return round(float(stop), 6), round(float(target), 6), round(float(rr), 6)


def _rr_bucket(rr: float) -> str:
    if rr < 1.0:
        return "below_1.0"
    if rr < 1.5:
        return "1.0-1.5"
    if rr < 2.0:
        return "1.5-2.0"
    if rr < 2.5:
        return "2.0-2.5"
    if rr < 3.0:
        return "2.5-3.0"
    return "3.0+"


def _extract_scored_rr(rejection_reason: str) -> Optional[float]:
    """Pull the actual R/R value out of a risk_reward_ratio fatal rejection string."""
    try:
        # e.g. "Failed fatal check: risk_reward_ratio (2.41 < 3.0)"
        if "risk_reward_ratio" in rejection_reason and "(" in rejection_reason:
            inner = rejection_reason.split("(", 1)[1].split("<", 1)[0].strip()
            return float(inner)
    except Exception:
        pass
    return None


def _classify_short_rejection(
    score_result: Dict[str, Any],
    *,
    score_threshold: float,
    min_pathways_required: int,
) -> tuple[str, str]:
    rejection_reason = str(score_result.get("rejection_reason") or "").strip()
    pathway_info = score_result.get("pathway_qualification") or {}
    pathways_passed = pathway_info.get("pathways_passed") or []

    if rejection_reason.startswith("Failed fatal check:"):
        check_name = rejection_reason.split(":", 1)[1].split("(", 1)[0].strip()
        return f"fatal_check:{check_name}", rejection_reason
    if "requires at least 2 SHORT pathways" in rejection_reason:
        if len(pathways_passed) >= 1:
            return "single_pathway_blocked_low_vix", rejection_reason
        return "zero_pathways_low_vix", rejection_reason
    if "No SHORT pathway qualified" in rejection_reason or "No qualification pathway passed" in rejection_reason:
        return "pathway_not_qualified", rejection_reason

    normalized_score = float(score_result.get("normalized_score", 0.0) or 0.0)
    if normalized_score < float(score_threshold):
        return "score_below_threshold", f"Score {normalized_score:.1f} below threshold {float(score_threshold):.1f}"
    if len(pathways_passed) < int(min_pathways_required):
        return "pathway_count_below_min", f"{len(pathways_passed)} pathways passed, need {int(min_pathways_required)}"
    if len(pathways_passed) < SHORT_PATHWAY_THRESHOLD or not bool(pathway_info.get("qualified", False)):
        return "pathway_not_qualified", rejection_reason or "Pathway qualification failed"
    return "unknown_rejection", rejection_reason or "Rejected after scoring"


def _extract_failed_check_name(entry: str) -> str:
    raw = str(entry or "").strip()
    if not raw:
        return "unknown"
    return raw.split(":", 1)[0].strip() or "unknown"


def build_short_signal(
    ticker: str,
    price_history: pd.DataFrame,
    spy_history: pd.DataFrame,
    fundamentals: Dict[str, Any],
    vix_level: float,
    score_threshold: float = SHORT_SCORE_THRESHOLD,
    single_pathway_ok: bool = True,
    min_rr: float = DEFAULT_MIN_RR,
    diagnostics: Optional[Dict[str, Any]] = None,
    benchmark_cache: Optional[Dict[str, pd.DataFrame]] = None,
) -> Optional[Dict[str, Any]]:
    diagnostics = diagnostics if diagnostics is not None else {}

    if len(price_history) < MIN_HISTORY_BARS or len(spy_history) < MIN_HISTORY_BARS:
        diagnostics.update({
            "rejection_code": "insufficient_history",
            "rejection_reason": "Insufficient price/SPY history",
        })
        return None

    entry_price = float(price_history["close"].iloc[-1])
    avg_volume_20 = float(price_history["volume"].tail(min(20, len(price_history))).mean() or 0.0)
    avg_dollar_volume_20 = float(entry_price * avg_volume_20)
    atr_pct_14 = _calc_atr_pct(price_history, period=14)
    gap_risk_max_up_20d_pct = _calc_gap_risk_pct(price_history.iloc[:-1], lookback=20)
    intraday_range_max_20d_pct = _calc_intraday_range_pct(price_history.iloc[:-1], lookback=20)
    stop_price, target_price, risk_reward = compute_short_trade_levels(
        price_history.iloc[:-1],
        entry_price,
        min_rr=min_rr,
    )
    if stop_price is None or target_price is None or risk_reward is None:
        diagnostics.update({
            "rejection_code": "rr_geometry_failed",
            "rejection_reason": f"Unable to compute trade geometry at min_rr {float(min_rr):.1f}",
            "computed_rr": None,
        })
        return None

    margin_guard = ShortScanner._margin_decay_confirmation(fundamentals)
    rs_ctx = compute_relative_weakness(
        ticker,
        price_history,
        spy_history,
        benchmark_cache=benchmark_cache,
    )
    fundamentals_deteriorating = any([
        bool(fundamentals.get("revenue_deceleration")),
        bool(margin_guard.get("margin_decay_confirmed")) and bool(fundamentals.get("margin_compression")),
        bool(margin_guard.get("margin_decay_confirmed")) and bool(fundamentals.get("profit_margin_declining")),
        bool(fundamentals.get("fcf_negative")),
        bool(fundamentals.get("debt_stress")),
        fundamentals.get("guidance_trend") == "cutting",
        fundamentals.get("estimate_revisions") == "cutting",
    ])

    scanner_data = {
        "risk_reward": risk_reward,
        "rs_spy": rs_ctx["relative_strength_pct"],
        "inst_selling": rs_ctx["institutional_distribution"],
        "fundamentals_weak": fundamentals_deteriorating,
        "revenue_growth_yoy": margin_guard.get("revenue_growth_yoy"),
        "revenue_deceleration": fundamentals.get("revenue_deceleration"),
        "margin_compression": fundamentals.get("margin_compression"),
        "profit_margin_declining": fundamentals.get("profit_margin_declining"),
        "fcf_negative": fundamentals.get("fcf_negative"),
        "guidance_trend": fundamentals.get("guidance_trend"),
        "debt_stress": fundamentals.get("debt_stress"),
        "valuation_rich": fundamentals.get("valuation_rich"),
        "short_interest_safe": fundamentals.get("short_interest_safe", False),
        "estimate_revisions": fundamentals.get("estimate_revisions"),
        "earnings_window_safe": fundamentals.get("earnings_window_safe", False),
        "short_interest_low": fundamentals.get("short_interest_low", False),
        "vix_elevated": float(vix_level or 0.0) > 20.0,
        "support_broken": entry_price < float(price_history["low"].iloc[-21:-1].min() or entry_price),
        "sector_weak": rs_ctx["sector_weakness"],
        "sector": str(rs_ctx.get("benchmark_ticker") or fundamentals.get("sector") or "SPY"),
        "inst_data_age_days": 30,
    }

    checks = StrategyCheckMapper.map_short_checks(scanner_data)
    metadata = StrategyCheckMapper.build_metadata(scanner_data, "SHORT")
    regime_vix = float(vix_level or 0.0)
    score_result = calculate_strategy_score(
        "SHORT",
        checks,
        metadata,
        regime_context={"vix": regime_vix},
    )

    pathway_info = score_result.get("pathway_qualification") or {}
    pathways_passed = pathway_info.get("pathways_passed") or []
    relaxed_single_pathway = False
    if (
        single_pathway_ok
        and regime_vix < 22.0
        and len(pathways_passed) >= SHORT_PATHWAY_THRESHOLD
        and not bool(pathway_info.get("qualified", False))
    ):
        # Backtest-only override: keep the live low-VIX dual-pathway rule intact,
        # but score the same candidate under single-path qualification so we can
        # measure whether the stricter live rule is actually improving outcomes.
        score_result = calculate_strategy_score(
            "SHORT",
            checks,
            metadata,
            regime_context={"vix": 22.0},
        )
        pathway_info = score_result.get("pathway_qualification") or {}
        pathways_passed = pathway_info.get("pathways_passed") or []
        relaxed_single_pathway = len(pathways_passed) == 1 and bool(pathway_info.get("qualified", False))

    min_pathways_required = 1 if single_pathway_ok else 2

    if float(score_result.get("normalized_score", 0.0) or 0.0) < float(score_threshold):
        rejection_code, rejection_reason = _classify_short_rejection(
            score_result,
            score_threshold=score_threshold,
            min_pathways_required=min_pathways_required,
        )
        scored_rr = _extract_scored_rr(rejection_reason)
        diagnostics.update({
            "rejection_code": rejection_code,
            "rejection_reason": rejection_reason,
            "score": float(score_result.get("normalized_score", 0.0) or 0.0),
            "pathways_passed": list(pathways_passed),
            "pathway_details": pathway_info.get("pathway_details") or {},
            "scored_rr": scored_rr,
        })
        return None
    if len(pathways_passed) < min_pathways_required:
        rejection_code, rejection_reason = _classify_short_rejection(
            score_result,
            score_threshold=score_threshold,
            min_pathways_required=min_pathways_required,
        )
        diagnostics.update({
            "rejection_code": rejection_code,
            "rejection_reason": rejection_reason,
            "score": float(score_result.get("normalized_score", 0.0) or 0.0),
            "pathways_passed": list(pathways_passed),
            "pathway_details": pathway_info.get("pathway_details") or {},
        })
        return None
    if len(pathways_passed) < SHORT_PATHWAY_THRESHOLD or not bool(pathway_info.get("qualified", False)):
        rejection_code, rejection_reason = _classify_short_rejection(
            score_result,
            score_threshold=score_threshold,
            min_pathways_required=min_pathways_required,
        )
        diagnostics.update({
            "rejection_code": rejection_code,
            "rejection_reason": rejection_reason,
            "score": float(score_result.get("normalized_score", 0.0) or 0.0),
            "pathways_passed": list(pathways_passed),
            "pathway_details": pathway_info.get("pathway_details") or {},
        })
        return None

    spy_close = float(spy_history["close"].iloc[-1]) if not spy_history.empty else None
    spy_ma200 = None
    if len(spy_history) >= 200:
        rolling_ma200 = spy_history["close"].rolling(200).mean()
        last_ma200 = rolling_ma200.iloc[-1]
        if not pd.isna(last_ma200):
            spy_ma200 = float(last_ma200)
    macro_gate_context = ShortScanner.evaluate_margin_revenue_macro_gate(
        pathways_passed,
        spy_close=spy_close,
        spy_ma200=spy_ma200,
        vix_level=regime_vix,
    )
    research_only = bool(macro_gate_context.get("applies") and not macro_gate_context.get("passed"))
    if research_only:
        diagnostics.update({
            "rejection_code": "research_only_margin_revenue_macro_gate",
            "rejection_reason": str(macro_gate_context.get("reason") or "margin_revenue_macro_gate_failed"),
            "score": float(score_result.get("normalized_score", 0.0) or 0.0),
            "pathways_passed": list(pathways_passed),
            "pathway_details": pathway_info.get("pathway_details") or {},
            "macro_gate_context": macro_gate_context,
        })

    pathway_label = format_pathway_label(pathways_passed)
    pathway_count = len(pathways_passed)
    score_value = float(score_result.get("normalized_score", 0.0) or 0.0)
    if pathway_count <= 1:
        pathway_count_bucket = "single_pathway"
    elif pathway_count == 2:
        pathway_count_bucket = "dual_pathway"
    else:
        pathway_count_bucket = "three_plus_pathways"

    size_multiplier = 1.0  # Uniform sizing — no pathway weighting in this model version

    benchmark_return_pct = float(rs_ctx.get("benchmark_return_pct") or 0.0)
    benchmark_below_ma50 = bool(rs_ctx.get("benchmark_below_ma50", False))
    market_regime = _market_regime_label(regime_vix, benchmark_return_pct, benchmark_below_ma50)
    vix_bucket = _vix_bucket_label(regime_vix)
    market_cap = _safe_float(fundamentals.get("market_cap"), None)
    short_interest_pct = _safe_float(fundamentals.get("short_interest_pct"), None)
    days_to_earnings = _safe_float(fundamentals.get("days_to_earnings"), None)

    return {
        "ticker": ticker,
        "entry_date": price_history.index[-1],
        "entry_price": entry_price,
        "price_bucket": _bucket_price(entry_price),
        "avg_volume_20": float(avg_volume_20),
        "avg_dollar_volume_20": float(avg_dollar_volume_20),
        "dollar_volume_bucket": _bucket_dollar_volume(avg_dollar_volume_20),
        "stop_price": stop_price,
        "target_price": target_price,
        "risk_reward": risk_reward,
        "score": score_value,
        "score_bucket": _score_bucket_label(score_value),
        "pathways": list(pathways_passed),
        "pathway_label": pathway_label,
        "pathway_count": pathway_count,
        "pathway_count_bucket": pathway_count_bucket,
        "single_pathway_relaxed": relaxed_single_pathway,
        "size_multiplier": size_multiplier,
        "sector": str(rs_ctx.get("benchmark_ticker") or fundamentals.get("sector") or "SPY"),
        "industry": str(fundamentals.get("industry") or "UNKNOWN"),
        "market_cap": market_cap,
        "market_cap_bucket": _bucket_market_cap(market_cap),
        "short_interest_pct": short_interest_pct,
        "short_interest_bucket": _bucket_short_interest_pct(short_interest_pct),
        "days_to_earnings": days_to_earnings,
        "earnings_proximity_bucket": _bucket_earnings_proximity(days_to_earnings),
        "earnings_window_safe": bool(fundamentals.get("earnings_window_safe", False)),
        "atr_pct_14": atr_pct_14,
        "atr_bucket": _bucket_atr_pct(atr_pct_14),
        "gap_risk_max_up_20d_pct": gap_risk_max_up_20d_pct,
        "gap_risk_bucket": _bucket_gap_risk_pct(gap_risk_max_up_20d_pct),
        "intraday_range_max_20d_pct": intraday_range_max_20d_pct,
        "vix_level": regime_vix,
        "vix_bucket": vix_bucket,
        "market_regime": market_regime,
        "relative_strength_pct": float(rs_ctx.get("relative_strength_pct") or 0.0),
        "benchmark_return_pct": benchmark_return_pct,
        "benchmark_below_ma50": benchmark_below_ma50,
        "sector_weakness": bool(rs_ctx.get("sector_weakness", False)),
        "research_only": research_only,
        "dormant": research_only,
        "trade_eligible": not research_only,
        "execution_block_reason": "margin_revenue_macro_gate_failed" if research_only else None,
        "macro_gate_context": macro_gate_context,
        "score_result": score_result,
    }


def simulate_short_trade(
    ticker: str,
    future_prices: pd.DataFrame,
    entry_date: pd.Timestamp,
    entry_price: float,
    stop_price: float,
    target_price: float,
    pathway_label: str,
) -> Dict[str, Any]:
    cutoff_date = pd.Timestamp(entry_date) + pd.Timedelta(days=MAX_HOLD_DAYS)
    window = future_prices.loc[(future_prices.index > entry_date) & (future_prices.index <= cutoff_date)]

    exit_reason = "TIMEOUT"
    exit_price = entry_price
    exit_date = pd.Timestamp(entry_date)

    max_high = float("nan")
    min_low = float("nan")
    if not window.empty:
        try:
            max_high = float(window["high"].max() or float("nan"))
            min_low = float(window["low"].min() or float("nan"))
        except Exception:
            max_high = float("nan")
            min_low = float("nan")

    if not window.empty:
        for idx, row in window.iterrows():
            day_high = float(row["high"])
            day_low = float(row["low"])
            if day_high >= stop_price and day_low <= target_price:
                exit_reason = "STOP"
                exit_price = stop_price
                exit_date = idx
                break
            if day_high >= stop_price:
                exit_reason = "STOP"
                exit_price = stop_price
                exit_date = idx
                break
            if day_low <= target_price:
                exit_reason = "TARGET"
                exit_price = target_price
                exit_date = idx
                break
        else:
            exit_date = window.index[-1]
            exit_price = float(window["close"].iloc[-1])
    return_pct = ((entry_price - exit_price) / entry_price) * 100.0 if entry_price > 0 else 0.0
    hold_days = max(0, int((pd.Timestamp(exit_date) - pd.Timestamp(entry_date)).days))

    # Excursions are measured only over post-entry bars (the same window used for stop/target scanning).
    mae_pct = 0.0
    mfe_pct = 0.0
    if entry_price > 0 and not (math.isnan(max_high) or math.isnan(min_low)):
        # For shorts: MAE is adverse move up from entry; MFE is favorable move down from entry.
        mae_pct = ((max_high / entry_price) - 1.0) * 100.0
        mfe_pct = ((entry_price - min_low) / entry_price) * 100.0
    return {
        "ticker": ticker,
        "entry_date": pd.Timestamp(entry_date),
        "entry_price": float(entry_price),
        "stop_price": float(stop_price),
        "target_price": float(target_price),
        "exit_date": pd.Timestamp(exit_date),
        "exit_price": float(exit_price),
        "exit_reason": exit_reason,
        "hold_days": hold_days,
        "gross_return_pct": float(return_pct),
        "return_pct": float(return_pct),
        "pathway": pathway_label,
        "mae_pct": float(mae_pct),
        "mfe_pct": float(mfe_pct),
    }


def _attach_signal_metadata_to_trade(trade: Dict[str, Any], signal: Dict[str, Any]) -> None:
    for key in (
        "pathways",
        "pathway_count",
        "pathway_count_bucket",
        "single_pathway_relaxed",
        "score",
        "score_bucket",
        "size_multiplier",
        "sector",
        "industry",
        "vix_level",
        "vix_bucket",
        "market_regime",
        "relative_strength_pct",
        "benchmark_return_pct",
        "benchmark_below_ma50",
        "avg_volume_20",
        "avg_dollar_volume_20",
        "dollar_volume_bucket",
        "price_bucket",
        "market_cap",
        "market_cap_bucket",
        "short_interest_pct",
        "short_interest_bucket",
        "days_to_earnings",
        "earnings_proximity_bucket",
        "earnings_window_safe",
        "atr_pct_14",
        "atr_bucket",
        "gap_risk_max_up_20d_pct",
        "gap_risk_bucket",
        "intraday_range_max_20d_pct",
    ):
        if key in signal:
            trade[key] = signal.get(key)


def _return_histogram_buckets(values: Sequence[float]) -> Dict[str, int]:
    buckets = {
        "<=-15%": 0,
        "-15 to -10": 0,
        "-10 to -5": 0,
        "-5 to 0": 0,
        "0 to 5": 0,
        "5 to 10": 0,
        "10 to 20": 0,
        "20%+": 0,
    }
    for value in values:
        v = float(value)
        if v <= -15.0:
            buckets["<=-15%"] += 1
        elif v <= -10.0:
            buckets["-15 to -10"] += 1
        elif v <= -5.0:
            buckets["-10 to -5"] += 1
        elif v <= 0.0:
            buckets["-5 to 0"] += 1
        elif v <= 5.0:
            buckets["0 to 5"] += 1
        elif v <= 10.0:
            buckets["5 to 10"] += 1
        elif v <= 20.0:
            buckets["10 to 20"] += 1
        else:
            buckets["20%+"] += 1
    return buckets


def build_tail_risk_diagnostics(
    trades: Sequence[Dict[str, Any]],
    *,
    return_field: str = "effective_return_pct",
) -> Dict[str, Any]:
    ordered = sorted(trades, key=lambda trade: _trade_metric(trade, return_field))
    worst_20 = ordered[:20]
    returns = [_trade_metric(trade, return_field) for trade in trades]
    total_pnl = float(sum(returns))
    total_loss = float(sum(value for value in returns if value < 0.0))
    worst_5 = ordered[:5]
    worst_10 = ordered[:10]
    worst_5_sum = float(sum(_trade_metric(trade, return_field) for trade in worst_5))
    worst_10_sum = float(sum(_trade_metric(trade, return_field) for trade in worst_10))

    def _share(part: float, whole: float) -> float:
        if whole == 0.0:
            return 0.0
        return float(part / whole * 100.0)

    squeeze_losses: list[float] = []
    for trade in trades:
        if str(trade.get("exit_reason") or "").upper() != "STOP":
            continue
        gap_risk = _safe_float(trade.get("gap_risk_max_up_20d_pct"), None) or 0.0
        intraday_risk = _safe_float(trade.get("intraday_range_max_20d_pct"), None) or 0.0
        short_interest = _safe_float(trade.get("short_interest_pct"), None) or 0.0
        if gap_risk >= 5.0 or intraday_risk >= 8.0 or short_interest >= 15.0:
            squeeze_losses.append(_trade_metric(trade, return_field))
    squeeze_loss_sum = float(sum(value for value in squeeze_losses if value < 0.0))

    return {
        "total_pnl": total_pnl,
        "total_loss": total_loss,
        "worst_20": worst_20,
        "worst_5_contribution_pct_of_total_pnl": _share(worst_5_sum, total_pnl),
        "worst_10_contribution_pct_of_total_pnl": _share(worst_10_sum, total_pnl),
        "worst_5_contribution_pct_of_total_loss": _share(worst_5_sum, total_loss),
        "worst_10_contribution_pct_of_total_loss": _share(worst_10_sum, total_loss),
        "return_histogram": _return_histogram_buckets(returns),
        "squeeze_stop_loss_pct_of_total_loss": _share(squeeze_loss_sum, total_loss),
    }


def build_pathway_dimension_breakdown(
    trades: Sequence[Dict[str, Any]],
    *,
    pathway: str,
    return_field: str,
) -> Dict[str, List[Dict[str, Any]]]:
    rows = [trade for trade in trades if str(trade.get("pathway") or "") == str(pathway)]
    return {
        "sector": _group_trade_performance(rows, key_fn=lambda trade: trade.get("sector"), return_field=return_field),
        "market_cap": _group_trade_performance(rows, key_fn=lambda trade: trade.get("market_cap_bucket"), return_field=return_field),
        "price": _group_trade_performance(rows, key_fn=lambda trade: trade.get("price_bucket"), return_field=return_field),
        "dollar_volume": _group_trade_performance(rows, key_fn=lambda trade: trade.get("dollar_volume_bucket"), return_field=return_field),
        "short_interest": _group_trade_performance(rows, key_fn=lambda trade: trade.get("short_interest_bucket"), return_field=return_field),
        "borrow_cost": _group_trade_performance(rows, key_fn=lambda trade: _bucket_borrow_cost_pct(_safe_float(trade.get("borrow_cost_pct"), None)), return_field=return_field),
        "atr_risk": _group_trade_performance(rows, key_fn=lambda trade: trade.get("atr_bucket"), return_field=return_field),
        "gap_risk": _group_trade_performance(rows, key_fn=lambda trade: trade.get("gap_risk_bucket"), return_field=return_field),
        "earnings_proximity": _group_trade_performance(rows, key_fn=lambda trade: trade.get("earnings_proximity_bucket"), return_field=return_field),
        "regime": _group_trade_performance(rows, key_fn=lambda trade: trade.get("market_regime"), return_field=return_field, label_order=("bullish", "neutral", "bearish")),
        "vix": _group_trade_performance(rows, key_fn=lambda trade: trade.get("vix_bucket"), return_field=return_field, label_order=("<18", "18-22", "22-28", "28+")),
        "hold_days": _group_trade_performance(rows, key_fn=lambda trade: _bucket_hold_days(_safe_float(trade.get("hold_days"), None)), return_field=return_field, label_order=("0-5d", "6-15d", "16-30d", "31-45d", "45d+", "UNKNOWN")),
        "score_bucket": _group_trade_performance(rows, key_fn=lambda trade: trade.get("score_bucket"), return_field=return_field, label_order=("60-65", "65-70", "70-75", "75+")),
    }


def build_failure_decomposition(
    trades: Sequence[Dict[str, Any]],
    *,
    pathways: Sequence[str] = PATHWAYS_OF_INTEREST,
    return_field: str = "effective_return_pct",
) -> Dict[str, Any]:
    output: Dict[str, Any] = {}
    for pathway in pathways:
        output[pathway] = {
            "summary": summarize_backtest(
                [trade for trade in trades if str(trade.get("pathway") or "") == str(pathway)],
                tickers_tested=0,
                return_field=return_field,
            ),
            "dimensions": build_pathway_dimension_breakdown(trades, pathway=pathway, return_field=return_field),
        }
    return output


def _index_of_timestamp(frame: pd.DataFrame, ts: pd.Timestamp) -> Optional[int]:
    try:
        idx = frame.index.get_loc(pd.Timestamp(ts))
        if isinstance(idx, slice):
            return int(idx.start)
        if isinstance(idx, (list, tuple)) and idx:
            return int(idx[0])
        return int(idx)
    except Exception:
        return None


def _entry_index_delay(signal_index: int, delay_days: int, frame_len: int) -> Optional[int]:
    idx = int(signal_index) + int(delay_days)
    if 0 <= idx < int(frame_len):
        return idx
    return None


def _entry_index_failed_bounce(signal_index: int, history: pd.DataFrame, *, lookahead: int = 10) -> Optional[int]:
    frame = _normalize_price_frame(history)
    if frame.empty:
        return None
    closes = frame["close"].astype(float)
    sma10 = closes.rolling(10).mean()
    start = int(signal_index) + 1
    end = min(len(frame) - 1, int(signal_index) + int(lookahead))
    for idx in range(start, end):
        if idx + 1 >= len(frame):
            break
        if pd.isna(sma10.iloc[idx]) or pd.isna(sma10.iloc[idx + 1]):
            continue
        bounced = closes.iloc[idx] > float(sma10.iloc[idx]) and closes.iloc[idx] > closes.iloc[idx - 1] * 1.01
        failed_reclaim = closes.iloc[idx + 1] < float(sma10.iloc[idx + 1])
        if bounced and failed_reclaim:
            return idx + 1
    return None


def _entry_index_confirm_below_trend(signal_index: int, history: pd.DataFrame, *, lookahead: int = 5) -> Optional[int]:
    frame = _normalize_price_frame(history)
    if frame.empty:
        return None
    closes = frame["close"].astype(float)
    sma10 = closes.rolling(10).mean()
    start = int(signal_index) + 1
    end = min(len(frame) - 1, int(signal_index) + int(lookahead))
    for idx in range(start, end + 1):
        if pd.isna(sma10.iloc[idx]):
            continue
        below_trend = closes.iloc[idx] < float(sma10.iloc[idx])
        window_start = max(0, idx - 2)
        confirms = closes.iloc[idx] <= float(closes.iloc[window_start: idx + 1].min())
        if below_trend and confirms:
            return idx
    return None


def _simulate_timing_variant(
    signal: Dict[str, Any],
    *,
    history: pd.DataFrame,
    min_rr: float,
    entry_index: int,
) -> Optional[Dict[str, Any]]:
    frame = _normalize_price_frame(history)
    if frame.empty or entry_index <= 0 or entry_index >= len(frame):
        return None
    entry_date = pd.Timestamp(frame.index[entry_index])
    entry_price = float(frame["close"].iloc[entry_index])
    prior_history = frame.iloc[:entry_index]
    stop_price, target_price, risk_reward = compute_short_trade_levels(prior_history, entry_price, min_rr=min_rr)
    if stop_price is None or target_price is None or risk_reward is None:
        return None
    trade = simulate_short_trade(
        ticker=str(signal.get("ticker") or "UNKNOWN"),
        future_prices=frame.iloc[entry_index + 1 :],
        entry_date=entry_date,
        entry_price=entry_price,
        stop_price=float(stop_price),
        target_price=float(target_price),
        pathway_label=str(signal.get("pathway_label") or signal.get("pathway") or "UNKNOWN"),
    )
    _attach_signal_metadata_to_trade(trade, signal)
    trade["timing_policy"] = str(signal.get("timing_policy") or "UNKNOWN")
    trade["signal_date"] = pd.Timestamp(signal.get("entry_date"))
    return trade


def run_timing_experiments(
    signals: Sequence[Dict[str, Any]],
    *,
    price_histories: Dict[str, pd.DataFrame],
    min_rr: float,
    borrow_fee_annual_pct: float,
    slippage_bps: float,
    spread_bps: float,
    halt_gap_penalty_pct: float,
) -> Dict[str, Any]:
    policies = [
        ("signal_day", lambda sig_idx, hist: sig_idx),
        ("delay_1d", lambda sig_idx, hist: _entry_index_delay(sig_idx, 1, len(hist))),
        ("delay_2d", lambda sig_idx, hist: _entry_index_delay(sig_idx, 2, len(hist))),
        ("failed_bounce", lambda sig_idx, hist: _entry_index_failed_bounce(sig_idx, hist)),
        ("confirm_below_trend", lambda sig_idx, hist: _entry_index_confirm_below_trend(sig_idx, hist)),
    ]
    results: Dict[str, Any] = {"policies": {}}
    for policy_name, idx_fn in policies:
        trades: list[Dict[str, Any]] = []
        skipped = 0
        for signal in signals:
            ticker = str(signal.get("ticker") or "").upper()
            history = price_histories.get(ticker)
            if history is None or history.empty:
                skipped += 1
                continue
            sig_idx = _index_of_timestamp(history, pd.Timestamp(signal.get("entry_date")))
            if sig_idx is None:
                skipped += 1
                continue
            entry_idx = idx_fn(sig_idx, history)
            if entry_idx is None:
                skipped += 1
                continue
            variant_signal = dict(signal)
            variant_signal["timing_policy"] = policy_name
            trade = _simulate_timing_variant(
                variant_signal,
                history=history.loc[history.index <= history.index[-1]],
                min_rr=min_rr,
                entry_index=int(entry_idx),
            )
            if trade is None:
                skipped += 1
                continue
            trades.append(trade)

        friction_trades = apply_trade_execution_assumptions(
            trades,
            use_size_multiplier=True,
            borrow_fee_annual_pct=borrow_fee_annual_pct,
            slippage_bps=slippage_bps,
            spread_bps=spread_bps,
            halt_gap_penalty_pct=halt_gap_penalty_pct,
        )
        summary = summarize_backtest(friction_trades, tickers_tested=0, return_field="effective_return_pct")
        results["policies"][policy_name] = {
            "summary": summary,
            "skipped_signals": int(skipped),
            "trades": friction_trades,
        }
    return results


def run_research_veto_experiments(
    trades: Sequence[Dict[str, Any]],
    *,
    tickers_tested: int,
    return_field: str,
) -> Dict[str, Any]:
    baseline = summarize_backtest(trades, tickers_tested=tickers_tested, return_field=return_field)
    scenarios = [
        ("exclude_low_price", "Exclude low-priced names (< $10)", lambda t: float(t.get("entry_price") or 0.0) >= 10.0),
        ("exclude_low_dollar_vol", "Exclude low dollar volume (< $20M avg 20d)", lambda t: float(t.get("avg_dollar_volume_20") or 0.0) >= 20e6),
        ("exclude_high_short_interest", "Exclude highest short interest (>= 20% float)", lambda t: float(t.get("short_interest_pct") or 0.0) < 20.0),
        (
            "exclude_biotech",
            "Exclude biotech/binary-event industry",
            lambda t: not any(
                token in str(t.get("industry") or "").upper()
                for token in ("BIOTECH", "BIOTECHNOLOGY", "PHARM", "PHARMACEUTICAL", "DRUG", "CLINICAL")
            ),
        ),
        ("exclude_halt_like_gap", "Exclude halt-like gap risk (gap>=8% or intraday>=12%)", lambda t: (float(t.get("gap_risk_max_up_20d_pct") or 0.0) < 8.0) and (float(t.get("intraday_range_max_20d_pct") or 0.0) < 12.0)),
    ]
    out_rows: list[Dict[str, Any]] = []
    for key, label, keep_fn in scenarios:
        filtered = [t for t in trades if keep_fn(t)]
        after = summarize_backtest(filtered, tickers_tested=tickers_tested, return_field=return_field)
        out_rows.append(
            {
                "key": key,
                "label": label,
                "removed": int(len(trades) - len(filtered)),
                "after": after,
                "delta_expectancy_pct": float(after.get("expectancy_per_trade_pct", 0.0)) - float(baseline.get("expectancy_per_trade_pct", 0.0)),
            }
        )
    out_rows.sort(key=lambda row: float(row.get("delta_expectancy_pct") or 0.0), reverse=True)
    return {
        "baseline": baseline,
        "scenarios": out_rows,
    }


def build_failure_decision_memo(
    *,
    failure_decomposition: Dict[str, Any],
    tail_risk: Dict[str, Any],
    timing_experiments: Dict[str, Any],
    veto_experiments: Dict[str, Any],
) -> Dict[str, Any]:
    retire: list[str] = []
    keep_research: list[str] = []
    for pathway in PATHWAYS_OF_INTEREST:
        payload = failure_decomposition.get(pathway) or {}
        s = payload.get("summary") or {}
        trades = int(s.get("trades_simulated", 0) or 0)
        win_rate = float(s.get("win_rate_pct", 0.0) or 0.0)
        exp = float(s.get("expectancy_per_trade_pct", 0.0) or 0.0)
        if trades >= 20 and exp < 0.0 and win_rate < 35.0:
            retire.append(pathway)
        elif trades >= 10 and exp > 0.0:
            keep_research.append(pathway)

    loss_concentration = {
        "worst_5_pct_total_loss": float(tail_risk.get("worst_5_contribution_pct_of_total_loss", 0.0) or 0.0),
        "worst_10_pct_total_loss": float(tail_risk.get("worst_10_contribution_pct_of_total_loss", 0.0) or 0.0),
        "squeeze_stop_loss_pct_total_loss": float(tail_risk.get("squeeze_stop_loss_pct_of_total_loss", 0.0) or 0.0),
        "concentrated": bool(float(tail_risk.get("worst_10_contribution_pct_of_total_loss", 0.0) or 0.0) >= 55.0),
    }

    timing_rows: list[tuple[str, float, int]] = []
    for policy, payload in (timing_experiments.get("policies") or {}).items():
        s = payload.get("summary") or {}
        timing_rows.append((policy, float(s.get("expectancy_per_trade_pct", 0.0) or 0.0), int(s.get("trades_simulated", 0) or 0)))
    timing_rows.sort(key=lambda row: row[1], reverse=True)
    best_timing = timing_rows[0] if timing_rows else ("none", 0.0, 0)
    baseline_timing = next((row for row in timing_rows if row[0] == "signal_day"), ("signal_day", 0.0, 0))

    veto_rows = veto_experiments.get("scenarios") or []
    best_veto = veto_rows[0] if veto_rows else None

    return {
        "retire_pathways": retire,
        "keep_research_pathways": keep_research,
        "loss_concentration": loss_concentration,
        "best_timing_policy": {
            "policy": best_timing[0],
            "expectancy_pct": best_timing[1],
            "trades": best_timing[2],
            "delta_vs_signal_day": float(best_timing[1] - baseline_timing[1]),
        },
        "best_veto": best_veto,
        "remain_rejected": True,
    }


def format_pathway_label(pathways: Sequence[str]) -> str:
    if not pathways:
        return "NONE"
    ordered = sorted(str(pathway).upper() for pathway in pathways)
    if len(ordered) == 1:
        return f"{ordered[0]} only"
    return " + ".join(ordered)


def apply_trade_execution_assumptions(
    trades: Sequence[Dict[str, Any]],
    *,
    use_size_multiplier: bool,
    borrow_fee_annual_pct: float = 0.0,
    slippage_bps: float = 0.0,
    spread_bps: float = 0.0,
    halt_gap_penalty_pct: float = 0.0,
) -> list[Dict[str, Any]]:
    adjusted: list[Dict[str, Any]] = []
    round_trip_slippage_pct = max(0.0, float(slippage_bps)) / 100.0 * 2.0
    round_trip_spread_pct = max(0.0, float(spread_bps)) / 100.0 * 2.0
    annual_borrow_pct = max(0.0, float(borrow_fee_annual_pct))
    halt_penalty_pct = max(0.0, float(halt_gap_penalty_pct))

    for trade in trades:
        sized = dict(trade)
        gross_return_pct = float(trade.get("gross_return_pct", trade.get("return_pct", 0.0)) or 0.0)
        hold_days = int(trade.get("hold_days") or 0)
        borrow_cost_pct = (annual_borrow_pct / 365.0) * max(0, hold_days)
        friction_cost_pct = borrow_cost_pct + round_trip_slippage_pct + round_trip_spread_pct
        if str(trade.get("exit_reason") or "").upper() == "STOP":
            friction_cost_pct += halt_penalty_pct
        position_weight = float(trade.get("size_multiplier") or 1.0) if use_size_multiplier else 1.0
        net_return_pct = gross_return_pct - friction_cost_pct
        effective_return_pct = net_return_pct * position_weight
        sized.update(
            {
                "position_weight": float(position_weight),
                "borrow_cost_pct": float(borrow_cost_pct),
                "friction_cost_pct": float(friction_cost_pct),
                "net_return_pct": float(net_return_pct),
                "effective_return_pct": float(effective_return_pct),
            }
        )
        adjusted.append(sized)
    return adjusted


def _compute_max_drawdown_pct(trades: Sequence[Dict[str, Any]], *, return_field: str) -> float:
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    ordered = sorted(trades, key=lambda trade: (pd.Timestamp(trade.get("exit_date")), pd.Timestamp(trade.get("entry_date"))))
    for trade in ordered:
        trade_return = _trade_metric(trade, return_field) / 100.0
        equity *= max(0.0, 1.0 + trade_return)
        peak = max(peak, equity)
        if peak > 0:
            drawdown = ((equity / peak) - 1.0) * 100.0
            max_drawdown = min(max_drawdown, drawdown)
    return float(max_drawdown)


def _group_trade_performance(
    trades: Sequence[Dict[str, Any]],
    *,
    key_fn,
    return_field: str,
    min_trades: int = 1,
    label_order: Optional[Sequence[str]] = None,
) -> list[Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        label = str(key_fn(trade) or "UNKNOWN")
        grouped[label].append(trade)

    rows: list[Dict[str, Any]] = []
    for label, rows_for_label in grouped.items():
        if len(rows_for_label) < int(min_trades):
            continue
        returns = [_trade_metric(row, return_field) for row in rows_for_label]
        winners = [value for value in returns if value > 0.0]
        rows.append(
            {
                "label": label,
                "trades": len(rows_for_label),
                "win_rate_pct": (len(winners) / len(rows_for_label) * 100.0) if rows_for_label else 0.0,
                "avg_return_pct": _mean(returns),
                "median_return_pct": _median(returns),
            }
        )

    if label_order:
        order_lookup = {label: idx for idx, label in enumerate(label_order)}
        rows.sort(key=lambda row: (order_lookup.get(row["label"], len(order_lookup)), -int(row["trades"]), row["label"]))
    else:
        rows.sort(key=lambda row: (-int(row["trades"]), float(row["avg_return_pct"]), row["label"]))
    return rows


def build_losing_slice_postmortem(
    trades: Sequence[Dict[str, Any]],
    *,
    pathways: Sequence[str] = LOSING_SLICE_PATHWAYS,
    return_field: str = "return_pct",
) -> Dict[str, Dict[str, Any]]:
    postmortem: Dict[str, Dict[str, Any]] = {}
    for pathway in pathways:
        rows = [trade for trade in trades if str(trade.get("pathway")) == str(pathway)]
        returns = [_trade_metric(trade, return_field) for trade in rows]
        winners = [value for value in returns if value > 0.0]
        exit_counts = Counter(str(trade.get("exit_reason") or "UNKNOWN") for trade in rows)
        repeated_losers: list[Dict[str, Any]] = []
        loser_grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for trade in rows:
            if _trade_metric(trade, return_field) <= 0.0:
                loser_grouped[str(trade.get("ticker") or "UNKNOWN")].append(trade)
        for ticker, loser_rows in loser_grouped.items():
            if len(loser_rows) < 2:
                continue
            loser_returns = [_trade_metric(row, return_field) for row in loser_rows]
            repeated_losers.append(
                {
                    "ticker": ticker,
                    "losses": len(loser_rows),
                    "avg_return_pct": _mean(loser_returns),
                    "sector": str(loser_rows[0].get("sector") or "UNKNOWN"),
                }
            )
        repeated_losers.sort(key=lambda row: (-int(row["losses"]), float(row["avg_return_pct"]), row["ticker"]))

        postmortem[pathway] = {
            "trades": len(rows),
            "win_rate_pct": (len(winners) / len(rows) * 100.0) if rows else 0.0,
            "avg_return_pct": _mean(returns),
            "median_return_pct": _median(returns),
            "avg_hold_days": _mean([float(trade.get("hold_days") or 0.0) for trade in rows]),
            "exit_counts": {
                "STOP": int(exit_counts.get("STOP", 0)),
                "TARGET": int(exit_counts.get("TARGET", 0)),
                "TIMEOUT": int(exit_counts.get("TIMEOUT", 0)),
            },
            "sector_breakdown": _group_trade_performance(rows, key_fn=lambda trade: trade.get("sector"), return_field=return_field, min_trades=1),
            "vix_breakdown": _group_trade_performance(rows, key_fn=lambda trade: trade.get("vix_bucket"), return_field=return_field, min_trades=1, label_order=("<18", "18-22", "22-28", "28+")),
            "score_bucket_breakdown": _group_trade_performance(rows, key_fn=lambda trade: trade.get("score_bucket"), return_field=return_field, min_trades=1, label_order=("60-65", "65-70", "70-75", "75+")),
            "market_regime_breakdown": _group_trade_performance(rows, key_fn=lambda trade: trade.get("market_regime"), return_field=return_field, min_trades=1, label_order=("bullish", "neutral", "bearish")),
            "repeated_loser_tickers": repeated_losers[:10],
        }
    return postmortem


def _identify_blacklists_for_losing_slices(
    trades: Sequence[Dict[str, Any]],
    *,
    pathways: Sequence[str] = LOSING_SLICE_PATHWAYS,
    return_field: str = "effective_return_pct",
) -> Dict[str, List[str]]:
    targeted = [trade for trade in trades if str(trade.get("pathway")) in set(pathways)]
    loser_tickers: list[str] = []
    loser_sectors: list[str] = []

    grouped_by_ticker: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for trade in targeted:
        grouped_by_ticker[str(trade.get("ticker") or "UNKNOWN")].append(trade)
    for ticker, rows in grouped_by_ticker.items():
        if len(rows) < 2:
            continue
        returns = [_trade_metric(row, return_field) for row in rows]
        if all(value <= 0.0 for value in returns):
            loser_tickers.append(ticker)

    grouped_by_sector: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for trade in targeted:
        grouped_by_sector[str(trade.get("sector") or "UNKNOWN")].append(trade)
    for sector, rows in grouped_by_sector.items():
        if len(rows) < 5:
            continue
        returns = [_trade_metric(row, return_field) for row in rows]
        win_rate = len([value for value in returns if value > 0.0]) / len(rows) * 100.0 if rows else 0.0
        if _mean(returns) < 0.0 and win_rate < 35.0:
            loser_sectors.append(sector)

    return {
        "tickers": sorted(set(loser_tickers)),
        "sectors": sorted(set(loser_sectors)),
    }


def _evaluate_cut_scenarios(
    trades: Sequence[Dict[str, Any]],
    *,
    tickers_tested: int,
    return_field: str = "effective_return_pct",
) -> Dict[str, Any]:
    baseline_summary = summarize_backtest(trades, tickers_tested=tickers_tested, return_field=return_field)
    blacklists = _identify_blacklists_for_losing_slices(trades, return_field=return_field)
    targeted_pathways = set(LOSING_SLICE_PATHWAYS)

    scenario_defs = [
        {
            "key": "remove_margin_stress",
            "label": "Remove MARGIN + STRESS",
            "detail": "Cut the worst isolated slice entirely",
            "keep": lambda trade: str(trade.get("pathway")) != "MARGIN + STRESS",
            "curve_fit_risk": "low",
        },
        {
            "key": "guidance_revenue_score70",
            "label": "GUIDANCE + REVENUE score >= 70",
            "detail": "Cut lower-score GUIDANCE + REVENUE trades",
            "keep": lambda trade: not (
                str(trade.get("pathway")) == "GUIDANCE + REVENUE"
                and float(trade.get("score") or 0.0) < 70.0
            ),
            "curve_fit_risk": "medium",
        },
        {
            "key": "margin_revenue_rs_minus5",
            "label": "MARGIN + REVENUE RS <= -5",
            "detail": "Require deeper relative weakness on MARGIN + REVENUE",
            "keep": lambda trade: not (
                str(trade.get("pathway")) == "MARGIN + REVENUE"
                and float(trade.get("relative_strength_pct") or 0.0) > -5.0
            ),
            "curve_fit_risk": "low",
        },
        {
            "key": "blacklist_repeated_losers",
            "label": "Blacklist repeated losing tickers / sectors",
            "detail": (
                "Exclude targeted-slice trades in "
                + (", ".join(blacklists["tickers"]) if blacklists["tickers"] else "no repeat-loser tickers")
                + " / "
                + (", ".join(blacklists["sectors"]) if blacklists["sectors"] else "no losing sectors")
            ),
            "keep": lambda trade: not (
                str(trade.get("pathway")) in targeted_pathways
                and (
                    str(trade.get("ticker") or "") in set(blacklists["tickers"])
                    or str(trade.get("sector") or "") in set(blacklists["sectors"])
                )
            ),
            "curve_fit_risk": "high",
        },
        {
            "key": "bearish_regime_confirmation",
            "label": "Require bearish regime on losing slices",
            "detail": "Keep targeted-slice trades only when market regime is bearish",
            "keep": lambda trade: not (
                str(trade.get("pathway")) in targeted_pathways
                and str(trade.get("market_regime") or "neutral") != "bearish"
            ),
            "curve_fit_risk": "low",
        },
    ]

    scenarios: list[Dict[str, Any]] = []
    improved_keys: list[str] = []
    for scenario in scenario_defs:
        filtered = [trade for trade in trades if scenario["keep"](trade)]
        after = summarize_backtest(filtered, tickers_tested=tickers_tested, return_field=return_field)
        row = {
            "key": scenario["key"],
            "label": scenario["label"],
            "detail": scenario["detail"],
            "curve_fit_risk": scenario["curve_fit_risk"],
            "trades_before": int(baseline_summary["trades_simulated"]),
            "trades_after": int(after["trades_simulated"]),
            "removed_trades": int(baseline_summary["trades_simulated"] - after["trades_simulated"]),
            "win_rate_before_pct": float(baseline_summary["win_rate_pct"]),
            "win_rate_after_pct": float(after["win_rate_pct"]),
            "expectancy_before_pct": float(baseline_summary["expectancy_per_trade_pct"]),
            "expectancy_after_pct": float(after["expectancy_per_trade_pct"]),
            "avg_winner_before_pct": float(baseline_summary["avg_winner_return_pct"]),
            "avg_winner_after_pct": float(after["avg_winner_return_pct"]),
            "avg_loser_before_pct": float(baseline_summary["avg_loser_return_pct"]),
            "avg_loser_after_pct": float(after["avg_loser_return_pct"]),
            "max_drawdown_before_pct": float(baseline_summary["max_drawdown_pct"]),
            "max_drawdown_after_pct": float(after["max_drawdown_pct"]),
            "after_passes": dict(after["pass_criteria"]),
        }
        row["delta_expectancy_pct"] = row["expectancy_after_pct"] - row["expectancy_before_pct"]
        row["delta_max_drawdown_pct"] = row["max_drawdown_after_pct"] - row["max_drawdown_before_pct"]
        scenarios.append(row)

        if (
            row["delta_expectancy_pct"] > 0.0
            and row["trades_after"] >= max(20, int(row["trades_before"] * 0.35))
            and scenario["curve_fit_risk"] != "high"
        ):
            improved_keys.append(str(scenario["key"]))

    recommended_keys = []
    for key in ("remove_margin_stress", "margin_revenue_rs_minus5", "guidance_revenue_score70", "bearish_regime_confirmation"):
        if key in improved_keys:
            recommended_keys.append(key)

    active_keepers = {scenario["key"]: scenario["keep"] for scenario in scenario_defs}
    if recommended_keys:
        combined = [
            trade
            for trade in trades
            if all(active_keepers[key](trade) for key in recommended_keys)
        ]
    else:
        combined = list(trades)
    combined_summary = summarize_backtest(combined, tickers_tested=tickers_tested, return_field=return_field)

    return {
        "baseline_summary": baseline_summary,
        "scenarios": scenarios,
        "blacklists": blacklists,
        "recommended_keys": recommended_keys,
        "recommended_summary": combined_summary,
    }


def build_decision_memo(
    *,
    summary: Dict[str, Any],
    scenario_analysis: Dict[str, Any],
    slice_postmortem: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    scenario_by_key = {row["key"]: row for row in scenario_analysis.get("scenarios") or []}
    keep: list[str] = []
    remove: list[str] = []
    cautions: list[str] = []

    if scenario_by_key.get("remove_margin_stress", {}).get("delta_expectancy_pct", 0.0) > 0.0:
        remove.append("MARGIN + STRESS")

    guidance_cut = scenario_by_key.get("guidance_revenue_score70")
    if guidance_cut and guidance_cut.get("delta_expectancy_pct", 0.0) > 0.0:
        keep.append("GUIDANCE + REVENUE only at score >= 70")
    elif slice_postmortem.get("GUIDANCE + REVENUE", {}).get("avg_return_pct", 0.0) >= 0.0:
        keep.append("GUIDANCE + REVENUE")
    else:
        cautions.append("GUIDANCE + REVENUE remains weak without a higher score floor")

    margin_cut = scenario_by_key.get("margin_revenue_rs_minus5")
    if margin_cut and margin_cut.get("delta_expectancy_pct", 0.0) > 0.0:
        keep.append("MARGIN + REVENUE only when relative_strength_pct <= -5")
    elif slice_postmortem.get("MARGIN + REVENUE", {}).get("avg_return_pct", 0.0) < 0.0:
        cautions.append("MARGIN + REVENUE is still negative without stronger relative weakness")

    if scenario_by_key.get("bearish_regime_confirmation", {}).get("delta_expectancy_pct", 0.0) > 0.0:
        keep.append("Targeted losing slices only when market regime is bearish")

    if scenario_by_key.get("blacklist_repeated_losers", {}).get("delta_expectancy_pct", 0.0) > 0.0:
        cautions.append("Ticker/sector blacklists help in-sample but are curve-fit prone; keep as audit output, not live policy")

    recommended_summary = scenario_analysis.get("recommended_summary") or summary
    tradeable_after_cuts = all(bool(value) for value in (recommended_summary.get("pass_criteria") or {}).values())
    smallest_change_set = scenario_analysis.get("recommended_keys") or ["no statistically justified cut set found"]
    return {
        "keep": keep,
        "remove": remove,
        "tradeable_after_cuts": tradeable_after_cuts,
        "smallest_change_set": smallest_change_set,
        "recommended_summary": recommended_summary,
        "cautions": cautions,
    }


def summarize_backtest(
    trades: Sequence[Dict[str, Any]],
    tickers_tested: int,
    *,
    return_field: str = "effective_return_pct",
) -> Dict[str, Any]:
    total = len(trades)
    trade_returns = [_trade_metric(trade, return_field) for trade in trades]
    winners = [trade for trade in trades if _trade_metric(trade, return_field) > 0.0]
    losers = [trade for trade in trades if _trade_metric(trade, return_field) <= 0.0]
    avg_winner = _mean([_trade_metric(trade, return_field) for trade in winners])
    avg_loser = _mean([_trade_metric(trade, return_field) for trade in losers])
    win_rate_pct = (len(winners) / total * 100.0) if total else 0.0
    loss_rate = (len(losers) / total) if total else 0.0
    win_rate = (len(winners) / total) if total else 0.0
    wl_ratio = (avg_winner / abs(avg_loser)) if losers and avg_loser != 0 else (math.inf if winners else 0.0)
    expectancy_pct = (win_rate * avg_winner) + (loss_rate * avg_loser)
    max_single_loss_pct = min(trade_returns, default=0.0)
    max_drawdown_pct = _compute_max_drawdown_pct(trades, return_field=return_field)

    pathway_rollup: dict[str, dict[str, float]] = {}
    pathway_count_rollup: dict[str, dict[str, float]] = {}
    score_band_rollup: dict[str, dict[str, float]] = {}
    grouped: dict[str, list[Dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        grouped[str(trade["pathway"])].append(trade)
    for pathway, rows in grouped.items():
        row_returns = [_trade_metric(row, return_field) for row in rows]
        wins = [row for row in rows if _trade_metric(row, return_field) > 0.0]
        pathway_rollup[pathway] = {
            "trades": len(rows),
            "win_rate_pct": (len(wins) / len(rows) * 100.0) if rows else 0.0,
            "avg_return_pct": _mean(row_returns),
        }

    count_grouped: dict[str, list[Dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        bucket = str(trade.get("pathway_count_bucket") or "single_pathway")
        count_grouped[bucket].append(trade)
    for bucket, rows in count_grouped.items():
        wins = [row for row in rows if _trade_metric(row, return_field) > 0.0]
        pathway_count_rollup[bucket] = {
            "trades": len(rows),
            "win_rate_pct": (len(wins) / len(rows) * 100.0) if rows else 0.0,
            "avg_return_pct": _mean([_trade_metric(row, return_field) for row in rows]),
        }

    score_bands = {
        "60-65": lambda score: 60.0 <= score < 65.0,
        "65-70": lambda score: 65.0 <= score < 70.0,
        "70-75": lambda score: 70.0 <= score < 75.0,
        "75+": lambda score: score >= 75.0,
    }
    for label, predicate in score_bands.items():
        rows = [trade for trade in trades if predicate(float(trade.get("score", 0.0) or 0.0))]
        wins = [row for row in rows if _trade_metric(row, return_field) > 0.0]
        score_band_rollup[label] = {
            "trades": len(rows),
            "win_rate_pct": (len(wins) / len(rows) * 100.0) if rows else 0.0,
            "avg_return_pct": _mean([_trade_metric(row, return_field) for row in rows]),
        }

    # SHORT strategy runs at lower hit rate than longs — winners are larger, compensating.
    # Threshold: 40% win rate (vs 55% for longs) with W/L >= 1.5 and positive expectancy.
    pass_criteria = {
        "win_rate": win_rate_pct >= 40.0,
        "avg_wl_ratio": wl_ratio >= 1.5,
        "expectancy": expectancy_pct > 0.0,
    }

    return {
        "tickers_tested": int(tickers_tested),
        "trades_simulated": int(total),
        "winners": int(len(winners)),
        "losers": int(len(losers)),
        "win_rate_pct": float(win_rate_pct),
        "avg_winner_return_pct": float(avg_winner),
        "avg_loser_return_pct": float(avg_loser),
        "win_loss_ratio": float(wl_ratio) if math.isfinite(wl_ratio) else math.inf,
        "expectancy_per_trade_pct": float(expectancy_pct),
        "max_single_loss_pct": float(max_single_loss_pct),
        "median_return_pct": float(_median(trade_returns)),
        "avg_hold_days": float(_mean([float(trade.get("hold_days") or 0.0) for trade in trades])),
        "max_drawdown_pct": float(max_drawdown_pct),
        "pathway_breakdown": pathway_rollup,
        "pathway_count_breakdown": pathway_count_rollup,
        "score_band_breakdown": score_band_rollup,
        "pass_criteria": pass_criteria,
        "interpretation": (
            "SHORT strategy has demonstrable edge. Proceed to live validation and then unlock next strategy."
            if all(pass_criteria.values())
            else "SHORT strategy edge not confirmed. Do not unlock other strategies. Review pathway breakdown for weakest pathway and recalibrate."
        ),
    }


def _append_table(lines: list[str], title: str, headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> None:
    lines.append(title)
    lines.append("────────────────────────────────────────────")
    rendered_rows = [[str(cell) for cell in row] for row in rows]
    widths = [len(str(header)) for header in headers]
    for row in rendered_rows:
        for idx, cell in enumerate(row):
            widths[idx] = max(widths[idx], len(cell))

    def _render_row(row: Sequence[str]) -> str:
        return " | ".join(cell.ljust(widths[idx]) for idx, cell in enumerate(row))

    lines.append(_render_row([str(header) for header in headers]))
    lines.append("-+-".join("-" * width for width in widths))
    if rendered_rows:
        for row in rendered_rows:
            lines.append(_render_row(row))
    else:
        lines.append("(none)")


def format_backtest_report(
    summary: Dict[str, Any],
    trades: Sequence[Dict[str, Any]],
    tickers: Sequence[str],
    lookback: int,
    *,
    discovery: Optional[Dict[str, Any]],
    score_threshold: float,
    single_pathway_ok: bool,
    min_rr: float = DEFAULT_MIN_RR,
    max_trades_per_ticker: int = 2,
    reject_breakdown: Optional[Dict[str, Dict[str, Any]]] = None,
    pathway_failure_breakdown: Optional[Dict[str, Dict[str, Any]]] = None,
    zero_pathway_ticker_breakdown: Optional[Dict[str, int]] = None,
    candidate_fit_meta: Optional[Dict[str, Any]] = None,
    rr_distribution: Optional[Dict[str, Any]] = None,
    slice_postmortem: Optional[Dict[str, Dict[str, Any]]] = None,
    scenario_analysis: Optional[Dict[str, Any]] = None,
    decision_memo: Optional[Dict[str, Any]] = None,
    failure_decomposition: Optional[Dict[str, Any]] = None,
    tail_risk: Optional[Dict[str, Any]] = None,
    timing_experiments: Optional[Dict[str, Any]] = None,
    veto_experiments: Optional[Dict[str, Any]] = None,
    friction_summary: Optional[Dict[str, Any]] = None,
    friction_config: Optional[Dict[str, float]] = None,
) -> str:
    lines: list[str] = []
    lines.append("SHORT BACKTESTER")
    lines.append("Point-in-time limitation: uses current research-provider fundamentals (FMP primary, Alpha Vantage fallback) as an approximation; historical fundamental snapshots are not reconstructed.")
    lines.append(f"Lookback requested: {lookback} trading days")
    lines.append(f"Backtest score threshold: {float(score_threshold):.1f}")
    lines.append(f"Backtest single-pathway override: {'ON' if single_pathway_ok else 'OFF'}")
    lines.append(f"Backtest minimum R/R: {float(min_rr):.1f}")
    lines.append(f"Backtest max trades per ticker: {int(max_trades_per_ticker)}")
    if friction_config:
        lines.append(
            "Friction mode: "
            f"borrow={float(friction_config.get('borrow_fee_annual_pct', 0.0)):.1f}%/yr, "
            f"slippage={float(friction_config.get('slippage_bps', 0.0)):.1f}bps, "
            f"spread={float(friction_config.get('spread_bps', 0.0)):.1f}bps, "
            f"halt_penalty={float(friction_config.get('halt_gap_penalty_pct', 0.0)):.2f}%"
        )
    lines.append("")
    lines.append("UNIVERSE DISCOVERY")
    lines.append("────────────────────────────────────────────")
    if discovery and discovery.get("used"):
        lines.append(f"Source:             {discovery.get('source', 'Full market screen (Alpaca + log-parsed)')}")
        lines.append(f"Symbols scanned:    {int(discovery.get('symbols_scanned', len(tickers)))}")
        if discovery.get("historical_mode"):
            lines.append(f"Liquid seed pool:   {int(discovery.get('liquid_prefilter_count', 0))}")
            lines.append(f"Rolling pool kept:  {int(discovery.get('seed_pool_size', len(tickers)))}")
            lines.append(f"Rebalance dates:    {int(discovery.get('rebalance_dates', 0))}")
            lines.append(f"Avg passed screen:  {float(discovery.get('avg_passed_screening', 0.0)):.1f} per rebalance")
            lines.append(f"Avg selected:       {float(discovery.get('avg_candidates_taken', 0.0)):.1f} per rebalance")
            if int(discovery.get("filtered_non_common", 0) or 0) > 0:
                lines.append(f"Filtered non-common:{int(discovery.get('filtered_non_common', 0))} warrants/rights/units removed")
        else:
            lines.append(f"Passed screening:   {int(discovery.get('passed_screening', len(tickers)))} (return_20d <= -2%, below MA20)")
            lines.append(f"Candidates taken:   {int(discovery.get('candidates_taken', len(tickers)))} (top by short_score)")
    else:
        lines.append(f"Source:             Manual ticker list ({len(tickers)} tickers)")
    lines.append("────────────────────────────────────────────")
    if candidate_fit_meta:
        lines.append("CANDIDATE FIT REFINEMENT")
        lines.append("────────────────────────────────────────────")
        lines.append(f"Operating-company candidates: {int(candidate_fit_meta.get('operating_company_like', 0))}")
        lines.append(f"Candidates with >=1 fit pathway: {int(candidate_fit_meta.get('fit_candidates', 0))}")
        lines.append(f"Candidates kept for backtest: {int(candidate_fit_meta.get('selected_count', len(tickers)))}")
        top_fit = candidate_fit_meta.get("top_fit") or []
        if top_fit:
            rendered = ", ".join(
                f"{item['ticker']} ({'/'.join(item.get('fit_pathways') or ['NONE'])}, score={item.get('fit_score', 0)})"
                for item in top_fit
            )
            lines.append(f"Top fit candidates:  {rendered}")
        lines.append("────────────────────────────────────────────")
        lines.append("")
    lines.append("")
    lines.append("BACKTEST SUMMARY")
    lines.append("────────────────────────────────────────────")
    lines.append(f"Tickers tested:        {summary['tickers_tested']}")
    lines.append(f"Trades simulated:      {summary['trades_simulated']}")
    lines.append(f"Winners:               {summary['winners']} (win rate {summary['win_rate_pct']:.1f}%)")
    lines.append(f"Losers:                {summary['losers']}")
    lines.append(f"Avg winner return:     {summary['avg_winner_return_pct']:.2f}%")
    lines.append(f"Avg loser return:      {summary['avg_loser_return_pct']:.2f}%")
    wl_ratio = summary["win_loss_ratio"]
    lines.append(
        "Win/Loss ratio:        "
        + ("inf" if not math.isfinite(wl_ratio) else f"{wl_ratio:.2f}:1")
    )
    lines.append(f"Expectancy per trade:  {summary['expectancy_per_trade_pct']:.2f}%")
    lines.append(f"Median return:         {summary['median_return_pct']:.2f}%")
    lines.append(f"Avg hold days:         {summary['avg_hold_days']:.1f}")
    lines.append(f"Max single loss:       {summary['max_single_loss_pct']:.2f}%")
    lines.append(f"Max drawdown:          {summary['max_drawdown_pct']:.2f}%")
    lines.append("Reject breakdown:")
    if reject_breakdown:
        for reason, payload in sorted(reject_breakdown.items(), key=lambda item: (-int(item[1].get("count", 0)), item[0])):
            examples = ", ".join(payload.get("examples") or [])
            example_suffix = f" | samples: {examples}" if examples else ""
            lines.append(f"  {reason:<20} {int(payload.get('count', 0))} rejects{example_suffix}")
    else:
        lines.append("  NONE                 0 rejects")
    lines.append("Pathway failure audit:")
    if pathway_failure_breakdown:
        for pathway, payload in sorted(pathway_failure_breakdown.items(), key=lambda item: (-int(item[1].get("count", 0)), item[0])):
            top_checks = payload.get("top_failed_checks") or {}
            rendered_checks = ", ".join(f"{name}={count}" for name, count in top_checks.items())
            check_suffix = f" | top failed: {rendered_checks}" if rendered_checks else ""
            examples = ", ".join(payload.get("examples") or [])
            example_suffix = f" | samples: {examples}" if examples else ""
            lines.append(
                f"  {pathway:<20} {int(payload.get('count', 0))} failed evals{check_suffix}{example_suffix}"
            )
    else:
        lines.append("  NONE                 0 failed evals")
    lines.append("Zero-pathway ticker concentration:")
    if zero_pathway_ticker_breakdown:
        for ticker, count in sorted(zero_pathway_ticker_breakdown.items(), key=lambda item: (-int(item[1]), item[0]))[:10]:
            lines.append(f"  {ticker:<20} {int(count)} zero-path rejects")
    else:
        lines.append("  NONE                 0 zero-path rejects")
    lines.append("Pathway breakdown:")
    if summary["pathway_breakdown"]:
        for pathway, stats in sorted(summary["pathway_breakdown"].items()):
            lines.append(
                f"  {pathway:<20} {int(stats['trades'])} trades, {float(stats['win_rate_pct']):.1f}% win rate, avg {float(stats.get('avg_return_pct', 0.0)):.2f}%"
            )
    else:
        lines.append("  NONE                 0 trades, 0.0% win rate")
    lines.append("Score band breakdown:")
    for band in ("60-65", "65-70", "70-75", "75+"):
        stats = summary["score_band_breakdown"].get(band, {"trades": 0, "win_rate_pct": 0.0})
        lines.append(
            f"  {band:<20} {int(stats['trades'])} trades, {float(stats['win_rate_pct']):.1f}% win rate, avg {float(stats.get('avg_return_pct', 0.0)):.2f}%"
        )
    lines.append("Pathway count breakdown:")
    count_labels = {
        "single_pathway": "Single pathway",
        "dual_pathway": "Dual pathway",
        "three_plus_pathways": "3+ pathways",
    }
    for bucket in ("single_pathway", "dual_pathway", "three_plus_pathways"):
        stats = summary["pathway_count_breakdown"].get(bucket, {"trades": 0, "win_rate_pct": 0.0})
        lines.append(
            f"  {count_labels[bucket]:<20} {int(stats['trades'])} trades, {float(stats['win_rate_pct']):.1f}% win rate, avg {float(stats.get('avg_return_pct', 0.0)):.2f}%"
        )
    if slice_postmortem:
        lines.append("")
        lines.append("LOSING SLICE POSTMORTEM")
        lines.append("────────────────────────────────────────────")
        for pathway in LOSING_SLICE_PATHWAYS:
            payload = slice_postmortem.get(pathway) or {}
            _append_table(
                lines,
                f"{pathway} summary",
                ("Trades", "Win %", "Avg %", "Median %", "STOP", "TARGET", "TIMEOUT", "Avg hold"),
                [(
                    int(payload.get("trades", 0)),
                    f"{float(payload.get('win_rate_pct', 0.0)):.1f}",
                    f"{float(payload.get('avg_return_pct', 0.0)):.2f}",
                    f"{float(payload.get('median_return_pct', 0.0)):.2f}",
                    int((payload.get("exit_counts") or {}).get("STOP", 0)),
                    int((payload.get("exit_counts") or {}).get("TARGET", 0)),
                    int((payload.get("exit_counts") or {}).get("TIMEOUT", 0)),
                    f"{float(payload.get('avg_hold_days', 0.0)):.1f}",
                )],
            )
    if failure_decomposition:
        lines.append("")
        lines.append("FAILURE DECOMPOSITION (BY PATHWAY)")
        lines.append("────────────────────────────────────────────")
        for pathway in PATHWAYS_OF_INTEREST:
            payload = failure_decomposition.get(pathway) or {}
            summary_row = payload.get("summary") or {}
            if not summary_row or int(summary_row.get("trades_simulated", 0)) == 0:
                continue
            lines.append("")
            lines.append(f"{pathway} breakdown")
            lines.append("────────────────────────────────────────────")
            lines.append(
                f"Trades={int(summary_row.get('trades_simulated', 0))} | "
                f"Win%={float(summary_row.get('win_rate_pct', 0.0)):.1f} | "
                f"Avg={float(summary_row.get('expectancy_per_trade_pct', 0.0)):.2f}% exp | "
                f"Median={float(summary_row.get('median_return_pct', 0.0)):.2f}% | "
                f"MaxDD={float(summary_row.get('max_drawdown_pct', 0.0)):.2f}%"
            )
            dims = (payload.get("dimensions") or {})
            dim_order = [
                ("sector", "Sector"),
                ("market_cap", "Market Cap"),
                ("price", "Price"),
                ("dollar_volume", "Dollar Volume"),
                ("short_interest", "Short Interest"),
                ("borrow_cost", "Borrow Cost"),
                ("atr_risk", "ATR Risk"),
                ("gap_risk", "Gap Risk"),
                ("earnings_proximity", "Earnings Proximity"),
                ("regime", "Regime"),
                ("vix", "VIX"),
                ("hold_days", "Holding Period"),
                ("score_bucket", "Score Bucket"),
            ]
            for key, label in dim_order:
                rows = dims.get(key) or []
                table_rows = [
                    (
                        row.get("label"),
                        int(row.get("trades", 0)),
                        f"{float(row.get('win_rate_pct', 0.0)):.1f}",
                        f"{float(row.get('avg_return_pct', 0.0)):.2f}",
                        f"{float(row.get('median_return_pct', 0.0)):.2f}",
                    )
                    for row in rows[:10]
                ]
                _append_table(
                    lines,
                    f"{pathway} {label}",
                    ("Bucket", "Trades", "Win %", "Avg %", "Median %"),
                    table_rows,
                )
    if tail_risk:
        lines.append("")
        lines.append("TAIL RISK DIAGNOSTICS")
        lines.append("────────────────────────────────────────────")
        lines.append(
            "Worst-5 contribution: "
            f"{float(tail_risk.get('worst_5_contribution_pct_of_total_loss', 0.0)):.1f}% of total loss; "
            "Worst-10: "
            f"{float(tail_risk.get('worst_10_contribution_pct_of_total_loss', 0.0)):.1f}% of total loss"
        )
        lines.append(
            f"Squeeze-like STOP loss share: {float(tail_risk.get('squeeze_stop_loss_pct_of_total_loss', 0.0)):.1f}% of total loss"
        )
        hist = tail_risk.get("return_histogram") or {}
        _append_table(
            lines,
            "Single-trade return histogram",
            ("Bucket", "Count"),
            [(k, int(v)) for k, v in hist.items()],
        )
        worst_20 = tail_risk.get("worst_20") or []
        _append_table(
            lines,
            "Top 20 worst trades",
            ("Ticker", "Entry", "Exit", "Reason", "Return%", "Pathway", "Score", "Hold", "SI%", "Gap%"),
            [
                (
                    str(t.get("ticker")),
                    str(pd.Timestamp(t.get("entry_date")).date()),
                    str(pd.Timestamp(t.get("exit_date")).date()),
                    str(t.get("exit_reason")),
                    f"{_trade_metric(t, 'effective_return_pct'):.2f}",
                    str(t.get("pathway")),
                    f"{float(t.get('score') or 0.0):.1f}",
                    int(t.get("hold_days") or 0),
                    f"{float(t.get('short_interest_pct') or 0.0):.1f}",
                    f"{float(t.get('gap_risk_max_up_20d_pct') or 0.0):.1f}",
                )
                for t in worst_20
            ],
        )
    if timing_experiments and timing_experiments.get("policies"):
        lines.append("")
        lines.append("TIMING EXPERIMENTS (RESEARCH ONLY)")
        lines.append("────────────────────────────────────────────")
        rows = []
        for policy, payload in (timing_experiments.get("policies") or {}).items():
            s = (payload.get("summary") or {})
            rows.append(
                (
                    policy,
                    int(s.get("trades_simulated", 0)),
                    f"{float(s.get('win_rate_pct', 0.0)):.1f}",
                    f"{float(s.get('expectancy_per_trade_pct', 0.0)):.2f}",
                    f"{float(s.get('max_drawdown_pct', 0.0)):.2f}",
                    int(payload.get("skipped_signals", 0)),
                )
            )
        _append_table(
            lines,
            "Timing policy comparison (friction-adjusted)",
            ("Policy", "Trades", "Win %", "Exp %", "Max DD", "Skipped"),
            rows,
        )
    if veto_experiments and veto_experiments.get("scenarios"):
        lines.append("")
        lines.append("RESEARCH-ONLY VETO EXPERIMENTS")
        lines.append("────────────────────────────────────────────")
        base = veto_experiments.get("baseline") or {}
        lines.append(
            f"Baseline: trades={int(base.get('trades_simulated', 0))}, exp={float(base.get('expectancy_per_trade_pct', 0.0)):.2f}%, maxDD={float(base.get('max_drawdown_pct', 0.0)):.2f}%"
        )
        _append_table(
            lines,
            "Veto scenario deltas (friction-adjusted)",
            ("Scenario", "Removed", "Trades", "Win %", "Exp %", "Δ Exp %"),
            [
                (
                    row.get("label"),
                    int(row.get("removed", 0)),
                    int((row.get("after") or {}).get("trades_simulated", 0)),
                    f"{float((row.get('after') or {}).get('win_rate_pct', 0.0)):.1f}",
                    f"{float((row.get('after') or {}).get('expectancy_per_trade_pct', 0.0)):.2f}",
                    f"{float(row.get('delta_expectancy_pct', 0.0)):+.2f}",
                )
                for row in (veto_experiments.get("scenarios") or [])
            ],
        )
    lines.append("R/R DISTRIBUTION (rejected entries)")
    lines.append("────────────────────────────────────────────")
    rr_dist = rr_distribution or {}
    geometry_none = int(rr_dist.get("geometry_none", 0))
    computed = rr_dist.get("computed") or {}
    scored = rr_dist.get("scored") or {}
    lines.append(f"  Geometry returned None (no structure): {geometry_none}")
    lines.append("  Computed R/R rejected (below backtest min_rr):")
    buckets = ("below_1.0", "1.0-1.5", "1.5-2.0", "2.0-2.5", "2.5-3.0")
    labels = ("below 1.0 (geometry broken)", "1.0–1.5  (weak)", "1.5–2.0  (marginal)", "2.0–2.5  (close to floor)", "2.5–3.0  (close to live gate)")
    for bucket, label in zip(buckets, labels):
        lines.append(f"    {label}: {int(computed.get(bucket, 0))}")
    lines.append("  Scored R/R rejected by live fatal gate (< 2.5):")
    for bucket, label in zip(buckets, labels):
        lines.append(f"    {label}: {int(scored.get(bucket, 0))}")
    # Verdict
    total_scored_rejects = sum(scored.values())
    borderline_scored = int(scored.get("2.5-3.0", 0))
    if total_scored_rejects > 0 and borderline_scored / total_scored_rejects > 0.30:
        verdict = ("Live R/R gate was lowered to 2.5. Setups in 2.5-3.0 band now qualify. "
                   f"({borderline_scored}/{total_scored_rejects} = "
                   f"{borderline_scored/total_scored_rejects*100:.0f}% previously rejected). "
                   "Gate is now aligned with backtest min_rr=2.5.")
    elif geometry_none > 100:
        verdict = ("Stop geometry is the bottleneck — too many entries have no structure "
                   f"({geometry_none} geometry-None rejects). "
                   "ATR floor should widen stops. Threshold is not the issue.")
    else:
        verdict = ("R/R rejects are spread across low buckets. "
                   "Stop geometry is the bottleneck, not the live threshold. Threshold is correct.")
    lines.append(f"  VERDICT: {verdict}")
    lines.append("────────────────────────────────────────────")
    if scenario_analysis:
        lines.append("")
        _append_table(
            lines,
            "CUT SCENARIO ANALYSIS",
            ("Scenario", "Removed", "Trades", "Win %", "Exp %", "Avg Win", "Avg Loss", "Max DD", "Risk"),
            [
                (
                    row["label"],
                    int(row["removed_trades"]),
                    int(row["trades_after"]),
                    f"{float(row['win_rate_after_pct']):.1f}",
                    f"{float(row['expectancy_after_pct']):.2f}",
                    f"{float(row['avg_winner_after_pct']):.2f}",
                    f"{float(row['avg_loser_after_pct']):.2f}",
                    f"{float(row['max_drawdown_after_pct']):.2f}",
                    str(row["curve_fit_risk"]).upper(),
                )
                for row in scenario_analysis.get("scenarios") or []
            ],
        )
        _append_table(
            lines,
            "CUT DELTAS VS BASELINE",
            ("Scenario", "Δ Exp %", "Δ Win %", "Δ Max DD", "Detail"),
            [
                (
                    row["label"],
                    f"{float(row['delta_expectancy_pct']):+.2f}",
                    f"{float(row['win_rate_after_pct'] - row['win_rate_before_pct']):+.1f}",
                    f"{float(row['delta_max_drawdown_pct']):+.2f}",
                    row["detail"],
                )
                for row in scenario_analysis.get("scenarios") or []
            ],
        )
    if friction_summary:
        lines.append("")
        _append_table(
            lines,
            "FRICTION DELTA",
            ("Mode", "Trades", "Win %", "Exp %", "Avg Win", "Avg Loss", "Max DD"),
            [
                (
                    "Sized no friction",
                    int(summary.get("trades_simulated", 0)),
                    f"{float(summary.get('win_rate_pct', 0.0)):.1f}",
                    f"{float(summary.get('expectancy_per_trade_pct', 0.0)):.2f}",
                    f"{float(summary.get('avg_winner_return_pct', 0.0)):.2f}",
                    f"{float(summary.get('avg_loser_return_pct', 0.0)):.2f}",
                    f"{float(summary.get('max_drawdown_pct', 0.0)):.2f}",
                ),
                (
                    "Sized with friction",
                    int(friction_summary.get("trades_simulated", 0)),
                    f"{float(friction_summary.get('win_rate_pct', 0.0)):.1f}",
                    f"{float(friction_summary.get('expectancy_per_trade_pct', 0.0)):.2f}",
                    f"{float(friction_summary.get('avg_winner_return_pct', 0.0)):.2f}",
                    f"{float(friction_summary.get('avg_loser_return_pct', 0.0)):.2f}",
                    f"{float(friction_summary.get('max_drawdown_pct', 0.0)):.2f}",
                ),
            ],
        )
        lines.append(
            "Friction delta: "
            f"expectancy {float(friction_summary.get('expectancy_per_trade_pct', 0.0) - summary.get('expectancy_per_trade_pct', 0.0)):+.2f}%, "
            f"win rate {float(friction_summary.get('win_rate_pct', 0.0) - summary.get('win_rate_pct', 0.0)):+.1f}%, "
            f"max drawdown {float(friction_summary.get('max_drawdown_pct', 0.0) - summary.get('max_drawdown_pct', 0.0)):+.2f}%"
        )
    lines.append("")
    lines.append("BACKTEST WEAKNESSES")
    lines.append("────────────────────────────────────────────")
    lines.append("1. Survivorship bias: the seed universe starts from today's active Alpaca equities, so delisted names are absent.")
    lines.append("2. Fundamental approximation: current research-provider fundamentals are reused through history, not point-in-time snapshots.")
    lines.append("3. Symbol contamination risk remains: common-stock cleanup now strips warrants/rights/units/preferreds by asset name, but odd listings can still survive.")
    if discovery and int(discovery.get("filtered_non_common", 0) or 0) > 0:
        lines.append(f"Implemented cleanup: filtered {int(discovery.get('filtered_non_common', 0))} obvious non-common symbols before the seed universe.")
    if decision_memo:
        lines.append("")
        lines.append("DECISION MEMO")
        lines.append("────────────────────────────────────────────")
        lines.append("Keep:")
        keep_items = decision_memo.get("keep") or ["No pathway earned a keep recommendation"]
        for item in keep_items:
            lines.append(f"  - {item}")
        lines.append("Remove:")
        remove_items = decision_memo.get("remove") or ["No pathway earned a hard removal"]
        for item in remove_items:
            lines.append(f"  - {item}")
        lines.append(
            "Tradeable after cuts: "
            + ("YES" if bool(decision_memo.get("tradeable_after_cuts", False)) else "NO")
        )
        lines.append(
            "Smallest change set: "
            + ", ".join(str(item) for item in (decision_memo.get("smallest_change_set") or []))
        )
        cautions = decision_memo.get("cautions") or []
        if cautions:
            lines.append("Cautions:")
            for item in cautions:
                lines.append(f"  - {item}")
        failure_memo = decision_memo.get("failure_memo") or {}
        if failure_memo:
            lines.append("Failure decomposition memo:")
            retire = failure_memo.get("retire_pathways") or []
            if retire:
                lines.append("  - Retire candidates: " + ", ".join(str(p) for p in retire))
            keep_research = failure_memo.get("keep_research_pathways") or []
            if keep_research:
                lines.append("  - Continue research: " + ", ".join(str(p) for p in keep_research))
            lc = failure_memo.get("loss_concentration") or {}
            lines.append(
                "  - Loss concentration: "
                f"worst10={float(lc.get('worst_10_pct_total_loss', 0.0)):.1f}% of loss; "
                f"squeeze_stops={float(lc.get('squeeze_stop_loss_pct_total_loss', 0.0)):.1f}%"
            )
            bt = failure_memo.get("best_timing_policy") or {}
            if bt:
                lines.append(
                    "  - Best timing policy: "
                    f"{bt.get('policy')} (exp={float(bt.get('expectancy_pct', 0.0)):.2f}%, "
                    f"Δ vs signal_day={float(bt.get('delta_vs_signal_day', 0.0)):+.2f}%, trades={int(bt.get('trades', 0))})"
                )
            bv = failure_memo.get("best_veto")
            if isinstance(bv, dict):
                lines.append(
                    "  - Best veto: "
                    f"{bv.get('label')} (Δ exp {float(bv.get('delta_expectancy_pct', 0.0)):+.2f}%)"
                )
            lines.append(
                "  - SHORT program status: "
                + ("REJECTED" if bool(failure_memo.get("remain_rejected", True)) else "CANDIDATE")
            )
    lines.append(f"PASS CRITERIA (required for strategy to be considered validated):")
    lines.append(f"  Win rate >= 40%:     {'PASS' if summary['pass_criteria']['win_rate'] else 'FAIL'}")
    lines.append(f"  Avg W/L ratio >= 1.5:1: {'PASS' if summary['pass_criteria']['avg_wl_ratio'] else 'FAIL'}")
    lines.append(f"  Expectancy > 0:      {'PASS' if summary['pass_criteria']['expectancy'] else 'FAIL'}")
    lines.append("────────────────────────────────────────────")
    lines.append("INDIVIDUAL TRADES:")
    lines.append("Ticker | Entry Date | Entry $ | Stop $ | Target $ | Exit $ | Exit Reason | Return% | Size | Pathway")
    for trade in trades:
        lines.append(
            f"{trade['ticker']} | {pd.Timestamp(trade['entry_date']).date()} | "
            f"{float(trade['entry_price']):.2f} | {float(trade['stop_price']):.2f} | "
            f"{float(trade['target_price']):.2f} | {float(trade['exit_price']):.2f} | "
            f"{trade['exit_reason']} | {_trade_metric(trade, 'effective_return_pct'):.2f}% | "
            f"{float(trade.get('position_weight', trade.get('size_multiplier', 1.0))):.2f} | {trade['pathway']}"
        )
    lines.append("")
    lines.append("INTERPRETATION:")
    lines.append(f"  {summary['interpretation']}")
    lines.append("")
    lines.append("Universe:")
    lines.append("  " + ", ".join(tickers))
    return "\n".join(lines)


def save_report(text: str, report_date: Optional[date] = None, logs_dir: Path | str = "logs") -> Path:
    report_date = report_date or date.today()
    logs_path = Path(logs_dir)
    logs_path.mkdir(parents=True, exist_ok=True)
    # Avoid clobbering real backtest artifacts when tests run.
    if os.getenv("PYTEST_CURRENT_TEST"):
        target_path = logs_path / f"short_backtest_pytest_{report_date.isoformat()}_{os.getpid()}.txt"
    else:
        target_path = logs_path / f"short_backtest_{report_date.isoformat()}.txt"
    target_path.write_text(text + "\n", encoding="utf-8")
    return target_path


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def export_research_trades_csv(trades: Sequence[Dict[str, Any]], out_path: Path | str) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    def _squeeze_like(trade: Dict[str, Any]) -> bool:
        if str(trade.get("exit_reason") or "").upper() != "STOP":
            return False
        gap_risk = _safe_float(trade.get("gap_risk_max_up_20d_pct"), None) or 0.0
        intraday_risk = _safe_float(trade.get("intraday_range_max_20d_pct"), None) or 0.0
        short_interest = _safe_float(trade.get("short_interest_pct"), None) or 0.0
        return bool(gap_risk >= 5.0 or intraday_risk >= 8.0 or short_interest >= 15.0)

    rows: list[Dict[str, Any]] = []
    for trade in trades:
        signal_date = trade.get("signal_date") or trade.get("entry_date")
        rows.append(
            {
                "ticker": str(trade.get("ticker") or "").upper(),
                "signal_date": pd.Timestamp(signal_date).date().isoformat() if signal_date else None,
                "entry_date": pd.Timestamp(trade.get("entry_date")).date().isoformat() if trade.get("entry_date") else None,
                "pathway": str(trade.get("pathway") or "UNKNOWN"),
                "timing_variant": str(trade.get("timing_policy") or "signal_day"),
                "sector": trade.get("sector"),
                "industry": trade.get("industry"),
                "market_cap": _safe_float(trade.get("market_cap"), None),
                "market_cap_bucket": trade.get("market_cap_bucket"),
                "price": _safe_float(trade.get("entry_price"), None),
                "price_bucket": trade.get("price_bucket"),
                "avg_dollar_volume_20": _safe_float(trade.get("avg_dollar_volume_20"), None),
                "dollar_volume_bucket": trade.get("dollar_volume_bucket"),
                "borrow_cost_pct": _safe_float(trade.get("borrow_cost_pct"), None),
                "gap_size_proxy_pct": _safe_float(trade.get("gap_risk_max_up_20d_pct"), None),
                "intraday_range_max_20d_pct": _safe_float(trade.get("intraday_range_max_20d_pct"), None),
                "stop_type": trade.get("stop_type") or "prior_20d_high_or_atr_cap",
                "squeeze_like_flag": _squeeze_like(trade),
                "days_to_earnings": _safe_float(trade.get("days_to_earnings"), None),
                "earnings_proximity_bucket": trade.get("earnings_proximity_bucket"),
                "market_regime": trade.get("market_regime"),
                "vix_level": _safe_float(trade.get("vix_level"), None),
                "holding_period_days": int(trade.get("hold_days") or 0),
                "exit_reason": trade.get("exit_reason"),
                "gross_return_pct": _safe_float(trade.get("gross_return_pct"), None),
                "net_return_pct": _safe_float(trade.get("net_return_pct"), None),
                "effective_return_pct": _safe_float(trade.get("effective_return_pct"), None),
                "mae_pct": _safe_float(trade.get("mae_pct"), None),
                "mfe_pct": _safe_float(trade.get("mfe_pct"), None),
                "stop_price": _safe_float(trade.get("stop_price"), None),
                "target_price": _safe_float(trade.get("target_price"), None),
                "exit_price": _safe_float(trade.get("exit_price"), None),
                "score": _safe_float(trade.get("score"), None),
                "score_bucket": trade.get("score_bucket"),
                "pathway_count": int(trade.get("pathway_count") or 0),
            }
        )

    pd.DataFrame(rows).to_csv(out_path, index=False)
    return out_path


def freeze_rejected_baseline_v1(
    *,
    tag: str,
    result: Dict[str, Any],
    artifacts_dir: Path | str = "reports/baselines",
) -> Path:
    """
    Persist a full run as a frozen rejected baseline.

    Does not change any live trading logic. This is a research artifact freeze.
    """
    base_dir = Path(artifacts_dir) / str(tag)
    base_dir.mkdir(parents=True, exist_ok=True)

    (base_dir / "report.txt").write_text(str(result.get("report_text") or "") + "\n", encoding="utf-8")
    try:
        (base_dir / "summary.json").write_text(
            json.dumps(result.get("summary") or {}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (base_dir / "friction_summary.json").write_text(
            json.dumps(result.get("friction_summary") or {}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except Exception:
        pass

    key_files = [
        Path("short_backtester.py"),
        Path("enhanced_strategy_scoring.py"),
        Path("fundamental_data_fetcher.py"),
        Path("short_scanner_v1.py"),
    ]
    file_hashes: Dict[str, Any] = {}
    for p in key_files:
        try:
            file_hashes[str(p)] = {"exists": True, "sha256": _sha256_file(p)}
        except Exception:
            file_hashes[str(p)] = {"exists": False, "sha256": None}

    manifest = {
        "tag": str(tag),
        "frozen_at_utc": pd.Timestamp.utcnow().isoformat(),
        "python": sys.version,
        "platform": platform.platform(),
        "cwd": str(Path.cwd()),
        "report_path": str(result.get("report_path") or ""),
        "lookback": int(result.get("lookback") or 0),
        "file_hashes": file_hashes,
    }
    (base_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    trades = result.get("friction_trades") or result.get("sized_trades") or []
    export_research_trades_csv(trades, base_dir / "trades.csv")
    return base_dir


def run_backtest(
    tickers: Optional[Sequence[str]] = None,
    lookback: int = DEFAULT_LOOKBACK,
    score_threshold: float = SHORT_SCORE_THRESHOLD,
    single_pathway_ok: bool = True,
    min_rr: float = DEFAULT_MIN_RR,
    universe_size: int = DEFAULT_DISCOVERY_LIMIT,
    max_trades_per_ticker: int = 2,
    borrow_fee_annual_pct: float = 0.0,
    slippage_bps: float = 0.0,
    spread_bps: float = 0.0,
    halt_gap_penalty_pct: float = 0.0,
    pathway_filter: Optional[frozenset[str]] = None,
    macro_gate_mode: str = "none",
    backtest_start: Optional[str] = None,
    backtest_end: Optional[str] = None,
    price_period: str = "5y",
    price_loader=fetch_price_history,
    batch_price_loader=fetch_price_history_batch,
    fundamentals_loader=load_short_fundamentals,
    spy_loader=fetch_spy_history,
    vix_loader=fetch_vix_history,
) -> Dict[str, Any]:
    # pathway_filter: if set, only accept signals whose exact pathway set matches one
    # of the entries. Each entry is a frozenset of pathway names, e.g.:
    #   frozenset({"REVENUE"})              → REVENUE-only trades
    #   frozenset({"MARGIN", "REVENUE"})    → MARGIN+REVENUE trades only
    # Pass None (default) to accept all qualifying signals.
    #
    # macro_gate_mode: controls an optional pre-trade macro environment check.
    # The signal scoring logic is unchanged regardless of this setting.
    # Allowed values:
    #   "none"   — no gate (default); all qualifying signals are taken
    #   "or"     — allow trade if SPY < 200-bar MA  OR  VIX >= 20
    #   "and"    — allow trade if SPY < 200-bar MA  AND VIX >= 20 (stricter)
    #   "signal" — allow trade only if signal["market_regime"] == "bearish"
    #              (uses the regime label already computed inside build_short_signal)
    # Rejected trades are counted in reject_counts["macro_gate_blocked"].
    #
    # backtest_start / backtest_end: ISO date strings ("YYYY-MM-DD") that clip
    # the rebalance_dates list to a specific calendar window for regime analysis.
    # price_period: yfinance period string passed to price/spy/vix loaders.
    global _LAST_DISCOVERY_METADATA

    discovery_meta: Optional[Dict[str, Any]] = None
    candidate_fit_meta: Optional[Dict[str, Any]] = None
    previous_scoring_level = _SCORING_LOGGER.level
    _SCORING_LOGGER.setLevel(logging.ERROR)

    def _record_reject(
        ticker: str,
        diagnostics: Dict[str, Any],
        *,
        reject_counts: Counter[str],
        reject_examples: Dict[str, List[str]],
        pathway_fail_counts: Counter[str],
        pathway_fail_examples: Dict[str, List[str]],
        pathway_fail_check_counts: Dict[str, Counter[str]],
        zero_pathway_ticker_counts: Counter[str],
        rr_computed_dist: Counter[str],
        rr_scored_dist: Counter[str],
        rr_geometry_none_box: List[int],
    ) -> None:
        reject_code = str(diagnostics.get("rejection_code") or "unknown_rejection")
        reject_counts[reject_code] += 1
        if ticker not in reject_examples[reject_code] and len(reject_examples[reject_code]) < 5:
            reject_examples[reject_code].append(ticker)
        if reject_code in {"zero_pathways_low_vix", "pathway_not_qualified"}:
            zero_pathway_ticker_counts[ticker] += 1
        if reject_code == "rr_geometry_failed":
            computed_rr = diagnostics.get("computed_rr")
            if computed_rr is None:
                rr_geometry_none_box[0] += 1
            else:
                rr_computed_dist[_rr_bucket(float(computed_rr))] += 1
        elif reject_code.startswith("fatal_check:risk_reward"):
            scored_rr = diagnostics.get("scored_rr")
            if scored_rr is not None:
                rr_scored_dist[_rr_bucket(float(scored_rr))] += 1
        pathway_details = diagnostics.get("pathway_details") or {}
        if isinstance(pathway_details, dict):
            for pathway, detail in pathway_details.items():
                if not isinstance(detail, dict) or bool(detail.get("qualified", False)):
                    continue
                pathway_fail_counts[str(pathway)] += 1
                if ticker not in pathway_fail_examples[str(pathway)] and len(pathway_fail_examples[str(pathway)]) < 5:
                    pathway_fail_examples[str(pathway)].append(ticker)
                for failed_entry in detail.get("failed_list") or []:
                    pathway_fail_check_counts[str(pathway)][_extract_failed_check_name(str(failed_entry))] += 1

    try:
        spy_history = spy_loader(price_period)
        benchmark_cache: dict[str, pd.DataFrame] = {"SPY": spy_history}
        vix_history = vix_loader(price_period)
        vix_close = vix_history["close"].rename("vix_close") if not vix_history.empty else pd.Series(dtype="float64")
        trades: list[Dict[str, Any]] = []
        signals_for_timing: list[Dict[str, Any]] = []
        timing_price_histories: Dict[str, pd.DataFrame] = {}
        trades_by_ticker: Counter[str] = Counter()
        reject_counts: Counter[str] = Counter()
        reject_examples: dict[str, list[str]] = defaultdict(list)
        pathway_fail_counts: Counter[str] = Counter()
        pathway_fail_examples: dict[str, list[str]] = defaultdict(list)
        pathway_fail_check_counts: dict[str, Counter[str]] = defaultdict(Counter)
        zero_pathway_ticker_counts: Counter[str] = Counter()
        rr_geometry_none_count_box: list[int] = [0]
        rr_computed_dist: Counter[str] = Counter()   # geometry-level rejects
        rr_scored_dist: Counter[str] = Counter()     # scorer fatal-gate rejects
        if tickers is None:
            market_symbols, source = _load_market_screen_symbols()
            seed_limit = max(DEFAULT_ROLLING_SEED_POOL_SIZE, int(universe_size) * 2)
            seed_symbols, liquid_meta = _prefilter_liquid_symbols(
                market_symbols,
                batch_price_loader=batch_price_loader,
                limit=seed_limit,
            )
            fundamentals_map = warm_short_fundamentals_cache(
                seed_symbols,
                fundamentals_loader=fundamentals_loader,
            )
            fit_profiles = {
                ticker: evaluate_short_fundamental_fit(ticker, fundamentals_map.get(ticker) or {})
                for ticker in seed_symbols
            }
            normalized_tickers, candidate_fit_meta = refine_short_candidate_pool(
                seed_symbols,
                fundamentals_map,
            )
            price_histories = _preload_price_histories(
                normalized_tickers,
                period=price_period,
                price_loader=price_loader,
            )
            timing_price_histories = dict(price_histories)
            normalized_tickers = [
                ticker
                for ticker in normalized_tickers
                if not price_histories.get(ticker, pd.DataFrame()).empty
                and len(price_histories.get(ticker, pd.DataFrame())) >= MIN_HISTORY_BARS
            ]
            if candidate_fit_meta is not None:
                candidate_fit_meta["selected_count"] = len(normalized_tickers)
            rebalance_dates = _compute_rebalance_dates(
                spy_history,
                lookback=lookback,
                step_bars=DEFAULT_REBALANCE_EVERY_BARS,
            )
            # Clip rebalance_dates to the requested regime window
            if backtest_start is not None:
                _bs = pd.Timestamp(backtest_start)
                rebalance_dates = [d for d in rebalance_dates if d >= _bs]
            if backtest_end is not None:
                _be = pd.Timestamp(backtest_end)
                rebalance_dates = [d for d in rebalance_dates if d <= _be]
            avg_screened_counts: list[int] = []
            avg_selected_counts: list[int] = []
            next_available_date: dict[str, pd.Timestamp] = {}

            print(
                f"Prepared rolling historical SHORT pool with {len(normalized_tickers)} fit candidates",
                flush=True,
            )

            for current_date in rebalance_dates:
                current_candidates, screen_meta = _select_historical_short_candidates(
                    price_histories,
                    current_date,
                    limit=universe_size,
                    fit_profiles=fit_profiles,
                )
                avg_screened_counts.append(int(screen_meta.get("screened_candidates", 0)))
                avg_selected_counts.append(int(screen_meta.get("selected_candidates", 0)))
                for ticker in current_candidates:
                    if trades_by_ticker[ticker] >= int(max_trades_per_ticker):
                        continue
                    blocked_until = next_available_date.get(ticker)
                    if blocked_until is not None and pd.Timestamp(current_date) <= pd.Timestamp(blocked_until):
                        continue
                    price_history = price_histories.get(ticker, pd.DataFrame())
                    current_history = price_history.loc[price_history.index <= current_date]
                    if current_history.empty or len(current_history) < MIN_HISTORY_BARS:
                        continue
                    aligned_spy = spy_history.loc[spy_history.index <= current_date]
                    if len(aligned_spy) < MIN_HISTORY_BARS:
                        continue
                    vix_level = 25.0
                    historical_vix = pd.Series(dtype="float64")
                    if not vix_close.empty:
                        historical_vix = vix_close.loc[vix_close.index <= current_date]
                    if not historical_vix.empty:
                        vix_level = float(historical_vix.iloc[-1])
                    # Macro gate (pre-signal): "or" and "and" modes check SPY/VIX
                    # before calling build_short_signal. Signal logic is unchanged.
                    if macro_gate_mode in ("or", "and"):
                        spy_close_series = aligned_spy["close"]
                        if len(spy_close_series) >= 200:
                            spy_ma200 = float(spy_close_series.rolling(200).mean().iloc[-1])
                            spy_below_ma200 = float(spy_close_series.iloc[-1]) < spy_ma200
                        else:
                            spy_below_ma200 = False  # insufficient history → conservative default
                        if macro_gate_mode == "or":
                            gate_pass = spy_below_ma200 or vix_level >= 20.0
                        else:  # "and"
                            gate_pass = spy_below_ma200 and vix_level >= 20.0
                        if not gate_pass:
                            reject_counts["macro_gate_blocked"] += 1
                            continue
                    diagnostics: Dict[str, Any] = {}
                    signal = build_short_signal(
                        ticker=ticker,
                        price_history=current_history,
                        spy_history=aligned_spy,
                        fundamentals=fundamentals_map.get(ticker) or {},
                        vix_level=vix_level,
                        score_threshold=score_threshold,
                        single_pathway_ok=single_pathway_ok,
                        min_rr=min_rr,
                        diagnostics=diagnostics,
                        benchmark_cache=benchmark_cache,
                    )
                    if signal is None:
                        _record_reject(
                            ticker,
                            diagnostics,
                            reject_counts=reject_counts,
                            reject_examples=reject_examples,
                            pathway_fail_counts=pathway_fail_counts,
                            pathway_fail_examples=pathway_fail_examples,
                            pathway_fail_check_counts=pathway_fail_check_counts,
                            zero_pathway_ticker_counts=zero_pathway_ticker_counts,
                            rr_computed_dist=rr_computed_dist,
                            rr_scored_dist=rr_scored_dist,
                            rr_geometry_none_box=rr_geometry_none_count_box,
                        )
                        continue
                    if not bool(signal.get("trade_eligible", True)):
                        _record_reject(
                            ticker,
                            diagnostics,
                            reject_counts=reject_counts,
                            reject_examples=reject_examples,
                            pathway_fail_counts=pathway_fail_counts,
                            pathway_fail_examples=pathway_fail_examples,
                            pathway_fail_check_counts=pathway_fail_check_counts,
                            zero_pathway_ticker_counts=zero_pathway_ticker_counts,
                            rr_computed_dist=rr_computed_dist,
                            rr_scored_dist=rr_scored_dist,
                            rr_geometry_none_box=rr_geometry_none_count_box,
                        )
                        continue
                    signals_for_timing.append(dict(signal))
                    # Pathway variant filter: skip signals whose pathway set does not
                    # match any of the allowed combinations in pathway_filter.
                    if pathway_filter is not None:
                        signal_pathways = frozenset(signal.get("pathways") or [])
                        if signal_pathways not in pathway_filter:
                            reject_counts["pathway_variant_filtered"] += 1
                            continue
                    # Signal-level regime gate: only allow trades when the signal
                    # itself is already in a "bearish" macro regime (as classified
                    # by build_short_signal using SPY vs MA50 and benchmark return).
                    if macro_gate_mode == "signal":
                        sig_regime = str(signal.get("market_regime") or "neutral")
                        if sig_regime != "bearish":
                            reject_counts["macro_gate_blocked"] += 1
                            continue
                    trade = simulate_short_trade(
                        ticker=ticker,
                        future_prices=price_history.loc[price_history.index > pd.Timestamp(signal["entry_date"])],
                        entry_date=signal["entry_date"],
                        entry_price=float(signal["entry_price"]),
                        stop_price=float(signal["stop_price"]),
                        target_price=float(signal["target_price"]),
                        pathway_label=str(signal["pathway_label"]),
                    )
                    _attach_signal_metadata_to_trade(trade, signal)
                    trade["timing_policy"] = "signal_day"
                    trade["signal_date"] = pd.Timestamp(signal["entry_date"])
                    trade.setdefault("stop_type", "prior_20d_high_or_atr_cap")
                    trades.append(trade)
                    trades_by_ticker[ticker] += 1
                    next_available_date[ticker] = pd.Timestamp(trade["exit_date"])

            discovery_meta = {
                "used": True,
                "historical_mode": True,
                "source": source,
                "symbols_scanned": len(market_symbols),
                "liquid_prefilter_count": int(liquid_meta.get("liquid_prefilter_count", 0)),
                "seed_pool_size": int(liquid_meta.get("seed_pool_size", len(seed_symbols))),
                "rebalance_dates": len(rebalance_dates),
                "avg_passed_screening": _mean(avg_screened_counts),
                "avg_candidates_taken": _mean(avg_selected_counts),
                "filtered_non_common": int(_LAST_ALPACA_METADATA.get("filtered_non_common", 0) or 0),
            }
        else:
            normalized_tickers = [_normalize_symbol(ticker) for ticker in tickers if str(ticker).strip()]
            discovery_meta = {"used": False, "source": f"Manual ticker list ({len(normalized_tickers)} tickers)"}
            fundamentals_map = warm_short_fundamentals_cache(
                normalized_tickers,
                fundamentals_loader=fundamentals_loader,
            )
            normalized_tickers, candidate_fit_meta = refine_short_candidate_pool(
                normalized_tickers,
                fundamentals_map,
            )

            for ticker in normalized_tickers:
                price_history = price_loader(ticker, "5y")
                if price_history.empty or len(price_history) < MIN_HISTORY_BARS:
                    continue
                timing_price_histories[str(ticker).upper()] = _normalize_price_frame(price_history)
                fundamentals = fundamentals_map.get(ticker) or {}
                start_idx = max(MIN_HISTORY_BARS - 1, len(price_history) - int(max(lookback, 1)))
                idx = start_idx
                while idx < len(price_history) - 1:
                    if trades_by_ticker[ticker] >= int(max_trades_per_ticker):
                        break
                    current_history = price_history.iloc[: idx + 1]
                    current_date = current_history.index[-1]
                    aligned_spy = spy_history.loc[spy_history.index <= current_date]
                    if len(aligned_spy) < MIN_HISTORY_BARS:
                        idx += 1
                        continue
                    vix_level = 25.0
                    historical_vix = pd.Series(dtype="float64")
                    if not vix_close.empty:
                        historical_vix = vix_close.loc[vix_close.index <= current_date]
                    if not historical_vix.empty:
                        vix_level = float(historical_vix.iloc[-1])
                    diagnostics: Dict[str, Any] = {}
                    signal = build_short_signal(
                        ticker=ticker,
                        price_history=current_history,
                        spy_history=aligned_spy,
                        fundamentals=fundamentals,
                        vix_level=vix_level,
                        score_threshold=score_threshold,
                        single_pathway_ok=single_pathway_ok,
                        min_rr=min_rr,
                        diagnostics=diagnostics,
                        benchmark_cache=benchmark_cache,
                    )
                    if signal is None:
                        _record_reject(
                            ticker,
                            diagnostics,
                            reject_counts=reject_counts,
                            reject_examples=reject_examples,
                            pathway_fail_counts=pathway_fail_counts,
                            pathway_fail_examples=pathway_fail_examples,
                            pathway_fail_check_counts=pathway_fail_check_counts,
                            zero_pathway_ticker_counts=zero_pathway_ticker_counts,
                            rr_computed_dist=rr_computed_dist,
                            rr_scored_dist=rr_scored_dist,
                            rr_geometry_none_box=rr_geometry_none_count_box,
                        )
                        idx += 1
                        continue
                    if not bool(signal.get("trade_eligible", True)):
                        _record_reject(
                            ticker,
                            diagnostics,
                            reject_counts=reject_counts,
                            reject_examples=reject_examples,
                            pathway_fail_counts=pathway_fail_counts,
                            pathway_fail_examples=pathway_fail_examples,
                            pathway_fail_check_counts=pathway_fail_check_counts,
                            zero_pathway_ticker_counts=zero_pathway_ticker_counts,
                            rr_computed_dist=rr_computed_dist,
                            rr_scored_dist=rr_scored_dist,
                            rr_geometry_none_box=rr_geometry_none_count_box,
                        )
                        idx += 1
                        continue
                    signals_for_timing.append(dict(signal))
                    trade = simulate_short_trade(
                        ticker=ticker,
                        future_prices=price_history.iloc[idx + 1 :],
                        entry_date=signal["entry_date"],
                        entry_price=float(signal["entry_price"]),
                        stop_price=float(signal["stop_price"]),
                        target_price=float(signal["target_price"]),
                        pathway_label=str(signal["pathway_label"]),
                    )
                    _attach_signal_metadata_to_trade(trade, signal)
                    trade["timing_policy"] = "signal_day"
                    trade["signal_date"] = pd.Timestamp(signal["entry_date"])
                    trade.setdefault("stop_type", "prior_20d_high_or_atr_cap")
                    trades.append(trade)
                    trades_by_ticker[ticker] += 1
                    future_window = price_history.loc[price_history.index <= trade["exit_date"]]
                    next_idx = len(future_window)
                    idx = max(idx + 1, next_idx)
    finally:
        _SCORING_LOGGER.setLevel(previous_scoring_level)

    sized_trades = apply_trade_execution_assumptions(
        trades,
        use_size_multiplier=True,
    )
    friction_trades = apply_trade_execution_assumptions(
        trades,
        use_size_multiplier=True,
        borrow_fee_annual_pct=borrow_fee_annual_pct,
        slippage_bps=slippage_bps,
        spread_bps=spread_bps,
        halt_gap_penalty_pct=halt_gap_penalty_pct,
    )
    summary = summarize_backtest(sized_trades, len(normalized_tickers), return_field="effective_return_pct")
    friction_summary = summarize_backtest(friction_trades, len(normalized_tickers), return_field="effective_return_pct")
    slice_postmortem = build_losing_slice_postmortem(trades, return_field="return_pct")
    scenario_analysis = _evaluate_cut_scenarios(
        sized_trades,
        tickers_tested=len(normalized_tickers),
        return_field="effective_return_pct",
    )
    decision_memo = build_decision_memo(
        summary=summary,
        scenario_analysis=scenario_analysis,
        slice_postmortem=slice_postmortem,
    )
    failure_decomposition = build_failure_decomposition(
        friction_trades,
        return_field="effective_return_pct",
    )
    tail_risk = build_tail_risk_diagnostics(
        friction_trades,
        return_field="effective_return_pct",
    )
    timing_experiments = run_timing_experiments(
        signals_for_timing,
        price_histories=timing_price_histories,
        min_rr=min_rr,
        borrow_fee_annual_pct=borrow_fee_annual_pct,
        slippage_bps=slippage_bps,
        spread_bps=spread_bps,
        halt_gap_penalty_pct=halt_gap_penalty_pct,
    )
    veto_experiments = run_research_veto_experiments(
        friction_trades,
        tickers_tested=len(normalized_tickers),
        return_field="effective_return_pct",
    )
    failure_memo = build_failure_decision_memo(
        failure_decomposition=failure_decomposition,
        tail_risk=tail_risk,
        timing_experiments=timing_experiments,
        veto_experiments=veto_experiments,
    )
    try:
        decision_memo = dict(decision_memo or {})
        decision_memo["failure_memo"] = failure_memo
    except Exception:
        pass
    reject_breakdown = {
        reason: {
            "count": int(count),
            "examples": list(reject_examples.get(reason) or []),
        }
        for reason, count in reject_counts.items()
    }
    pathway_failure_breakdown = {
        pathway: {
            "count": int(count),
            "examples": list(pathway_fail_examples.get(pathway) or []),
            "top_failed_checks": {
                name: int(check_count)
                for name, check_count in pathway_fail_check_counts.get(pathway, Counter()).most_common(3)
            },
        }
        for pathway, count in pathway_fail_counts.items()
    }
    rr_distribution = {
        "geometry_none": rr_geometry_none_count_box[0],
        "computed": dict(rr_computed_dist),
        "scored": dict(rr_scored_dist),
    }
    report_text = format_backtest_report(
        summary,
        friction_trades if any(
            float(value) > 0.0
            for value in (borrow_fee_annual_pct, slippage_bps, spread_bps, halt_gap_penalty_pct)
        ) else sized_trades,
        normalized_tickers,
        lookback,
        discovery=discovery_meta,
        score_threshold=score_threshold,
        single_pathway_ok=single_pathway_ok,
        min_rr=min_rr,
        max_trades_per_ticker=max_trades_per_ticker,
        reject_breakdown=reject_breakdown,
        pathway_failure_breakdown=pathway_failure_breakdown,
        zero_pathway_ticker_breakdown={ticker: int(count) for ticker, count in zero_pathway_ticker_counts.items()},
        candidate_fit_meta=candidate_fit_meta,
        rr_distribution=rr_distribution,
        slice_postmortem=slice_postmortem,
        scenario_analysis=scenario_analysis,
        decision_memo=decision_memo,
        failure_decomposition=failure_decomposition,
        tail_risk=tail_risk,
        timing_experiments=timing_experiments,
        veto_experiments=veto_experiments,
        friction_summary=friction_summary,
        friction_config={
            "borrow_fee_annual_pct": float(borrow_fee_annual_pct),
            "slippage_bps": float(slippage_bps),
            "spread_bps": float(spread_bps),
            "halt_gap_penalty_pct": float(halt_gap_penalty_pct),
        },
    )
    report_path = save_report(report_text)
    return {
        "summary": summary,
        "trades": trades,
        "sized_trades": sized_trades,
        "friction_trades": friction_trades,
        "reject_breakdown": reject_breakdown,
        "pathway_failure_breakdown": pathway_failure_breakdown,
        "zero_pathway_ticker_breakdown": {ticker: int(count) for ticker, count in zero_pathway_ticker_counts.items()},
        "slice_postmortem": slice_postmortem,
        "scenario_analysis": scenario_analysis,
        "decision_memo": decision_memo,
        "failure_decomposition": failure_decomposition,
        "tail_risk": tail_risk,
        "timing_experiments": timing_experiments,
        "veto_experiments": veto_experiments,
        "friction_summary": friction_summary,
        "report_text": report_text,
        "report_path": report_path,
        "tickers": normalized_tickers,
        "lookback": lookback,
        "discovery": discovery_meta,
        "candidate_fit_meta": candidate_fit_meta,
        "score_threshold": float(score_threshold),
        "single_pathway_ok": bool(single_pathway_ok),
        "min_rr": float(min_rr),
        "max_trades_per_ticker": int(max_trades_per_ticker),
        "borrow_fee_annual_pct": float(borrow_fee_annual_pct),
        "slippage_bps": float(slippage_bps),
        "spread_bps": float(spread_bps),
        "halt_gap_penalty_pct": float(halt_gap_penalty_pct),
    }


def _parse_bool_arg(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "f", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"Invalid boolean value: {value}")


def _parse_universe_size(value: Any) -> int:
    size = int(value)
    if not 50 <= size <= 300:
        raise argparse.ArgumentTypeError("universe_size must be between 50 and 300")
    return size


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Standalone SHORT strategy backtester")
    parser.add_argument("--lookback", type=int, default=DEFAULT_LOOKBACK, help="Trading-day lookback window")
    parser.add_argument("--tickers", nargs="*", default=None, help="Optional ticker override")
    parser.add_argument(
        "--pathway_filter",
        type=str,
        default=None,
        help=(
            "Restrict to a specific pathway variant. "
            "Options: 'REVENUE' (revenue-only trades), 'MARGIN_REVENUE' (margin+revenue trades), "
            "or omit for all pathways."
        ),
    )
    parser.add_argument(
        "--macro_gate_mode",
        type=str,
        default="none",
        choices=["none", "or", "and", "signal"],
        help=(
            "Macro gate mode: 'none' (default, no gate), "
            "'or' (SPY<200MA OR VIX>=20), "
            "'and' (SPY<200MA AND VIX>=20, stricter), "
            "'signal' (only when signal market_regime=='bearish'). "
            "Does not change signal scoring logic."
        ),
    )
    parser.add_argument(
        "--score_threshold",
        type=float,
        default=SHORT_SCORE_THRESHOLD,
        help="Minimum normalized SHORT score required to simulate a trade",
    )
    parser.add_argument(
        "--single_pathway_ok",
        type=_parse_bool_arg,
        default=True,
        help="Allow a single qualifying SHORT pathway in the backtest (true/false, default: true)",
    )
    parser.add_argument(
        "--universe_size",
        type=_parse_universe_size,
        default=DEFAULT_DISCOVERY_LIMIT,
        help="How many top screen candidates to backtest when --tickers is omitted (50-300)",
    )
    parser.add_argument(
        "--min_rr",
        type=float,
        default=DEFAULT_MIN_RR,
        help="Minimum risk/reward ratio required to simulate a trade",
    )
    parser.add_argument(
        "--max_trades_per_ticker",
        type=int,
        default=5,
        help="Maximum simulated trades allowed per ticker",
    )
    parser.add_argument(
        "--borrow_fee_annual_pct",
        type=float,
        default=0.0,
        help="Annualized borrow fee assumption in percent",
    )
    parser.add_argument(
        "--slippage_bps",
        type=float,
        default=0.0,
        help="Per-side slippage in basis points",
    )
    parser.add_argument(
        "--spread_bps",
        type=float,
        default=0.0,
        help="Per-side spread cost in basis points",
    )
    parser.add_argument(
        "--halt_gap_penalty_pct",
        type=float,
        default=0.0,
        help="Additional percent loss applied to STOP exits for short gap risk",
    )
    parser.add_argument(
        "--export_trades_csv",
        type=str,
        default=None,
        help="Optional path to export friction-adjusted trades to CSV",
    )
    parser.add_argument(
        "--freeze_baseline_tag",
        type=str,
        default=None,
        help="Freeze this run as a baseline under reports/baselines/{tag} (research only)",
    )
    return parser.parse_args(argv)


def _parse_pathway_filter(value: Optional[str]) -> Optional[frozenset[frozenset]]:
    """Convert --pathway_filter CLI string to a frozenset of frozensets for run_backtest."""
    if value is None:
        return None
    v = value.strip().upper()
    if v == "REVENUE":
        return frozenset({frozenset({"REVENUE"})})
    if v in ("MARGIN_REVENUE", "MARGIN+REVENUE"):
        return frozenset({frozenset({"MARGIN", "REVENUE"})})
    raise argparse.ArgumentTypeError(
        f"Unknown pathway_filter '{value}'. Use 'REVENUE' or 'MARGIN_REVENUE'."
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    pathway_filter = _parse_pathway_filter(getattr(args, "pathway_filter", None))
    result = run_backtest(
        tickers=args.tickers,
        lookback=int(args.lookback),
        score_threshold=float(args.score_threshold),
        single_pathway_ok=bool(args.single_pathway_ok),
        min_rr=float(args.min_rr),
        universe_size=int(args.universe_size),
        max_trades_per_ticker=int(args.max_trades_per_ticker),
        borrow_fee_annual_pct=float(args.borrow_fee_annual_pct),
        slippage_bps=float(args.slippage_bps),
        spread_bps=float(args.spread_bps),
        halt_gap_penalty_pct=float(args.halt_gap_penalty_pct),
        pathway_filter=pathway_filter,
        macro_gate_mode=str(args.macro_gate_mode),
    )
    if args.export_trades_csv:
        export_research_trades_csv(
            result.get("friction_trades") or result.get("sized_trades") or [],
            Path(str(args.export_trades_csv)),
        )
    if args.freeze_baseline_tag:
        frozen_dir = freeze_rejected_baseline_v1(tag=str(args.freeze_baseline_tag), result=result)
        print(f"[BASELINE] Frozen baseline saved: {frozen_dir}")
    print(result["report_text"])
    print("")
    print(f"Saved report: {result['report_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

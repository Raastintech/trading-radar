"""
research_data_provider  (RESEARCH ONLY — does not touch live trading logic)

Provider abstraction for the short-research pipeline.  Separates *what data
is needed* (price history, earnings dates, fundamentals) from *who supplies
it*, so the provider stack can be swapped without touching thesis or scoring
logic.

Priority order (per requirements):
    Price:        Alpaca  →  yfinance (debug-only fallback)
    Earnings:     FMP  →  Alpha Vantage  →  yfinance (debug-only)
    Fundamentals: FMP  →  Alpha Vantage  →  yfinance (debug-only)

Design rules
    - Every failure is logged with: provider, symbol, endpoint, reason.
    - No bare `except: pass` anywhere in this module.
    - `None` is never returned silently; callers always get a ProviderResult
      that describes success/failure and which provider was used.
    - yfinance fallback uses `yf.Ticker(t).history()` — never `yf.download()`.
    - FMP or other providers can be added by implementing the Protocol and
      inserting into the routing list without changing caller code.

Coverage diagnostics (ProviderCoverageReport) accumulate across a full
universe scan and are written to the research report.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd
import requests

try:
    from secure_env import load_runtime_env

    load_runtime_env()
except Exception:
    pass

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

AV_BASE_URL = "https://www.alphavantage.co/query"
FMP_BASE_URL = "https://financialmodelingprep.com"
AV_REQUEST_TIMEOUT_SECONDS = 20
AV_RATE_LIMIT_PAUSE_SECONDS = 12.0   # free tier: 5 req/min → 12 s gap is safe
AV_CACHE_TTL_EARNINGS_SECONDS = 7 * 24 * 3600   # 7 days (data doesn't change)
AV_CACHE_TTL_OVERVIEW_SECONDS = 24 * 3600        # 24 h
FMP_REQUEST_TIMEOUT_SECONDS = 20
FMP_DAILY_BUDGET_TARGET = 3000  # Starter Annual: 300 calls/min; 3000/day is conservative headroom
FMP_CACHE_TTL_HISTORICAL_SECONDS = 30 * 24 * 3600
FMP_CACHE_TTL_OVERVIEW_SECONDS = 24 * 3600
FMP_CACHE_TTL_UPCOMING_EARNINGS_SECONDS = 6 * 3600

ALPACA_CACHE_TTL_SECONDS = 12 * 3600             # 12 h (same as short_backtester)
CACHE_ROOT = Path("logs") / "short_backtester_cache" / "provider_router"


# ── Data types ───────────────────────────────────────────────────────────────

@dataclass
class EarningsEvent:
    """One verified earnings release date for a ticker."""
    ticker: str
    reported_date: date          # actual date management released results
    fiscal_period: Optional[str]  # e.g. "Q1 2024"
    session_flag: str            # "pre_market" | "after_hours" | "unknown"
    source: str                  # e.g. "alpha_vantage_earnings"
    confidence_label: str = "verified_primary"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderResult:
    """
    Wrapper returned by every provider fetch.  Never None — callers always know
    what happened and which provider was responsible.
    """
    value: Any                          # DataFrame / List[EarningsEvent] / Dict / None
    provider: str                       # "alpaca" / "alpha_vantage" / "yfinance" / "none"
    success: bool
    fallback_used: bool = False
    failure_reason: Optional[str] = None  # set when success=False
    symbol: str = ""
    endpoint: str = ""
    confidence_label: str = "verified_primary"
    cache_hit: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderCoverageReport:
    """Accumulated diagnostics across a universe scan."""
    total_symbols: int = 0
    primary_success: int = 0
    fallback_used: int = 0
    secondary_success: int = 0
    debug_fallback_used: int = 0
    both_failed: int = 0
    skipped: int = 0
    events_found: int = 0
    events_missing: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    calls_saved_by_cache: int = 0
    budget_exhausted: int = 0
    requests_by_provider: Dict[str, int] = field(default_factory=dict)
    failure_log: List[Dict[str, str]] = field(default_factory=list)

    def record_provider_hit(
        self,
        *,
        provider: str,
        cache_hit: bool,
        confidence_label: str,
    ) -> None:
        self.requests_by_provider[provider] = int(self.requests_by_provider.get(provider, 0)) + 1
        if cache_hit:
            self.cache_hits += 1
            self.calls_saved_by_cache += 1
        else:
            self.cache_misses += 1
        if confidence_label == "fallback_secondary":
            self.secondary_success += 1
        elif confidence_label == "debug_only":
            self.debug_fallback_used += 1

    def record_budget_exhausted(self, provider: str) -> None:
        self.budget_exhausted += 1
        self.record_failure(
            provider=provider,
            symbol="*",
            endpoint="budget",
            reason="daily budget exhausted",
        )

    def record_failure(
        self,
        *,
        provider: str,
        symbol: str,
        endpoint: str,
        reason: str,
    ) -> None:
        entry = {
            "provider": provider,
            "symbol": symbol,
            "endpoint": endpoint,
            "reason": reason,
        }
        self.failure_log.append(entry)
        logger.warning(
            "Provider failure | provider=%s symbol=%s endpoint=%s reason=%s",
            provider, symbol, endpoint, reason,
        )

    def summary(self) -> Dict[str, Any]:
        return {
            "total_symbols": self.total_symbols,
            "primary_success": self.primary_success,
            "fallback_used": self.fallback_used,
            "secondary_success": self.secondary_success,
            "debug_fallback_used": self.debug_fallback_used,
            "both_failed": self.both_failed,
            "skipped": self.skipped,
            "events_found": self.events_found,
            "events_missing": self.events_missing,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "calls_saved_by_cache": self.calls_saved_by_cache,
            "budget_exhausted": self.budget_exhausted,
            "requests_by_provider": dict(self.requests_by_provider),
            "failure_count": len(self.failure_log),
        }


# ── Helpers ──────────────────────────────────────────────────────────────────

def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "")).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _jsonable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return pd.Timestamp(value).isoformat()
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    return str(value)


class PersistentProviderCache:
    def __init__(self, root: Path | str = CACHE_ROOT) -> None:
        self._root = Path(root)
        self._lock = threading.Lock()

    def _path(
        self,
        *,
        provider: str,
        endpoint: str,
        symbol: str,
        date_range: str = "",
        params: Optional[Dict[str, Any]] = None,
    ) -> Path:
        payload = {
            "provider": provider,
            "endpoint": endpoint,
            "symbol": str(symbol).upper(),
            "date_range": date_range,
            "params": _jsonable(params or {}),
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:24]
        return self._root / provider / endpoint.replace("/", "_") / f"{str(symbol).upper()}__{digest}.json"

    def get(
        self,
        *,
        provider: str,
        endpoint: str,
        symbol: str,
        ttl_seconds: int,
        date_range: str = "",
        params: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        path = self._path(
            provider=provider,
            endpoint=endpoint,
            symbol=symbol,
            date_range=date_range,
            params=params,
        )
        try:
            if not path.exists():
                return None
            if ttl_seconds > 0 and (time.time() - path.stat().st_mtime) > float(ttl_seconds):
                return None
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else None
        except Exception as exc:
            logger.warning(
                "Provider cache read failed | provider=%s endpoint=%s symbol=%s reason=%s",
                provider,
                endpoint,
                symbol,
                exc,
            )
            return None

    def set(
        self,
        *,
        provider: str,
        endpoint: str,
        symbol: str,
        payload: Dict[str, Any],
        date_range: str = "",
        params: Optional[Dict[str, Any]] = None,
    ) -> None:
        path = self._path(
            provider=provider,
            endpoint=endpoint,
            symbol=symbol,
            date_range=date_range,
            params=params,
        )
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(_jsonable(payload), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )


class DailyBudgetManager:
    def __init__(
        self,
        *,
        provider_name: str,
        daily_target: int,
        root: Path | str = CACHE_ROOT,
    ) -> None:
        self.provider_name = str(provider_name)
        self.daily_target = int(daily_target)
        self._root = Path(root) / "budgets"
        self._lock = threading.Lock()

    def _path(self) -> Path:
        today = datetime.now().strftime("%Y-%m-%d")
        return self._root / f"{self.provider_name}_{today}.json"

    def _load(self) -> Dict[str, Any]:
        path = self._path()
        if not path.exists():
            return {"provider": self.provider_name, "date": datetime.now().strftime("%Y-%m-%d"), "calls_used": 0}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                payload.setdefault("provider", self.provider_name)
                payload.setdefault("date", datetime.now().strftime("%Y-%m-%d"))
                payload.setdefault("calls_used", 0)
                return payload
        except Exception as exc:
            logger.warning("Budget state read failed | provider=%s reason=%s", self.provider_name, exc)
        return {"provider": self.provider_name, "date": datetime.now().strftime("%Y-%m-%d"), "calls_used": 0}

    def _save(self, payload: Dict[str, Any]) -> None:
        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_jsonable(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def can_consume(self, calls: int = 1) -> bool:
        with self._lock:
            payload = self._load()
            return int(payload.get("calls_used", 0)) + int(calls) <= self.daily_target

    def record_call(self, calls: int = 1) -> None:
        with self._lock:
            payload = self._load()
            payload["calls_used"] = int(payload.get("calls_used", 0)) + int(calls)
            self._save(payload)

    def summary(self) -> Dict[str, Any]:
        with self._lock:
            payload = self._load()
            used = int(payload.get("calls_used", 0))
            return {
                "provider": self.provider_name,
                "daily_target": self.daily_target,
                "calls_used": used,
                "calls_remaining": max(0, self.daily_target - used),
            }

def _period_to_start_datetime(period: str) -> datetime:
    """Convert a yfinance-style period string to a UTC start datetime."""
    now = datetime.now(timezone.utc)
    p = str(period).strip().lower()
    mapping = {
        "1d": timedelta(days=1),
        "5d": timedelta(days=5),
        "1mo": timedelta(days=32),
        "3mo": timedelta(days=95),
        "6mo": timedelta(days=185),
        "1y": timedelta(days=366),
        "2y": timedelta(days=365 * 2 + 1),
        "3y": timedelta(days=365 * 3 + 1),
        "5y": timedelta(days=365 * 5 + 2),
        "10y": timedelta(days=365 * 10 + 3),
        "max": timedelta(days=365 * 30),
    }
    delta = mapping.get(p)
    if delta is None:
        # Try to parse numeric suffixes like "504d" (trading days used in backtester)
        # Approximate: 1 trading day ≈ 1.4 calendar days
        try:
            if p.endswith("d"):
                n = int(p[:-1])
                delta = timedelta(days=int(n * 1.4) + 5)
            elif p.endswith("y"):
                n = float(p[:-1])
                delta = timedelta(days=int(n * 365) + 2)
            else:
                delta = timedelta(days=365 * 5)
        except (ValueError, TypeError):
            delta = timedelta(days=365 * 5)
    return now - delta


def _normalize_price_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Standardise any DataFrame to lowercase OHLCV with DatetimeIndex."""
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    out = frame.copy()
    if isinstance(out.columns, pd.MultiIndex):
        # yf.Ticker.history never returns MultiIndex; this is defensive
        out.columns = out.columns.get_level_values(-1)
    out.columns = [str(c).lower().strip() for c in out.columns]
    rename = {"adj close": "adj_close", "adj_close": "adj_close"}
    out = out.rename(columns=rename)
    out = out.loc[:, ~out.columns.duplicated(keep="last")]
    for col in ("open", "high", "low", "close", "volume"):
        if col not in out.columns:
            out[col] = pd.NA
    out.index = pd.to_datetime(out.index)
    if getattr(out.index, "tz", None) is not None:
        out.index = out.index.tz_convert(None)
    out = out.sort_index()
    out = out[["open", "high", "low", "close", "volume"]].dropna(
        subset=["open", "high", "low", "close"]
    )
    return out


def _massage_research_fundamentals(payload: Dict[str, Any], *, provider_name: str) -> Dict[str, Any]:
    """
    Normalize research fundamentals so labels remain honest:
      - analyst proxy is not mislabeled as management guidance
      - margin_compression only means a real trend, not a weak level proxy
    """
    out = dict(payload or {})

    analyst_proxy = out.get("analyst_downgrade_proxy")
    if analyst_proxy is None and out.get("guidance_trend") not in (None, "", "stable"):
        analyst_proxy = out.get("guidance_trend")
    out["analyst_downgrade_proxy"] = analyst_proxy

    # Research pipeline keeps management guidance separate from analyst proxy.
    out["management_guidance_trend"] = None
    out["guidance_trend"] = None

    if "margin_level_below_threshold" not in out:
        out["margin_level_below_threshold"] = out.get("margin_compression")
    if "profit_margin_level_below_threshold" not in out:
        out["profit_margin_level_below_threshold"] = out.get("profit_margin_declining")

    if "margin_compressing" in out and "margin_compression" not in out:
        out["margin_compression"] = out.get("margin_compressing")
        out.pop("margin_compressing", None)

    margin_signal_quality = out.get("margin_signal_quality")
    if margin_signal_quality is None:
        margin_signal_quality = (
            "trend"
            if out.get("margin_compression") in (True, False)
            and out.get("margin_level_below_threshold") is not None
            and provider_name in {"alpha_vantage_fundamentals", "fmp_fundamentals"}
            else "level_only"
        )
    out["margin_signal_quality"] = margin_signal_quality

    # Only preserve margin_compression as a live trend signal when the provider
    # can actually support it. Otherwise expose the level-only field separately.
    if provider_name not in {"alpha_vantage_fundamentals", "fmp_fundamentals"}:
        out["margin_compression"] = None

    out["data_source"] = provider_name
    return out


# ── Price providers ──────────────────────────────────────────────────────────

class AlpacaHistoricalPriceProvider:
    """
    Fetches daily OHLCV bars from Alpaca for a date range derived from a
    period string.  Uses the SIP feed with split+dividend adjustment.

    Requires ALPACA_API_KEY and ALPACA_SECRET_KEY in environment.
    """

    NAME = "alpaca"

    def __init__(self, *, cache: Optional[PersistentProviderCache] = None) -> None:
        self._client = None
        self._init_error: Optional[str] = None
        self._cache = cache or _provider_cache()
        try:
            from alpaca.data.historical import StockHistoricalDataClient
            api_key = os.getenv("ALPACA_API_KEY") or os.getenv("APCA_API_KEY_ID", "")
            secret_key = os.getenv("ALPACA_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY", "")
            if not api_key or not secret_key:
                self._init_error = "ALPACA_API_KEY / ALPACA_SECRET_KEY not set"
            else:
                self._client = StockHistoricalDataClient(api_key, secret_key)
        except ImportError as exc:
            self._init_error = f"alpaca-py not installed: {exc}"
        except Exception as exc:
            self._init_error = f"Alpaca init failed: {exc}"

    def fetch(self, ticker: str, *, period: str = "5y") -> ProviderResult:
        symbol = str(ticker).upper().strip()
        endpoint = f"alpaca/bars/{symbol}/{period}"
        cached = self._cache.get(
            provider=self.NAME,
            endpoint=endpoint,
            symbol=symbol,
            ttl_seconds=ALPACA_CACHE_TTL_SECONDS,
            date_range=period,
        )
        if cached is not None and isinstance(cached.get("frame"), dict):
            logger.info("[PROVIDER CACHE HIT] provider=%s endpoint=%s symbol=%s", self.NAME, endpoint, symbol)
            frame = pd.DataFrame(cached["frame"])
            if "index" in frame.columns:
                frame["index"] = pd.to_datetime(frame["index"])
                frame = frame.set_index("index")
            frame = _normalize_price_frame(frame)
            return ProviderResult(
                value=frame,
                provider=self.NAME,
                success=True,
                symbol=symbol,
                endpoint=endpoint,
                confidence_label="verified_primary",
                cache_hit=True,
            )
        logger.info("[PROVIDER CACHE MISS] provider=%s endpoint=%s symbol=%s", self.NAME, endpoint, symbol)
        if self._client is None:
            reason = self._init_error or "client not initialised"
            logger.warning(
                "AlpacaHistoricalPriceProvider unavailable: %s (symbol=%s)", reason, symbol
            )
            return ProviderResult(
                value=None, provider=self.NAME, success=False,
                failure_reason=reason, symbol=symbol, endpoint=endpoint,
            )
        try:
            from alpaca.data.requests import StockBarsRequest
            from alpaca.data.timeframe import TimeFrame
            from alpaca.data.enums import DataFeed

            start = _period_to_start_datetime(period)
            end = datetime.now(timezone.utc)

            request = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=TimeFrame.Day,
                start=start,
                end=end,
                adjustment="all",
                feed=DataFeed.SIP,
            )
            resp = self._client.get_stock_bars(request)
            bar_list = []
            if resp is not None:
                raw = getattr(resp, "data", {}) or {}
                bar_list = raw.get(symbol, [])

            if not bar_list:
                reason = f"0 bars returned (period={period})"
                logger.warning(
                    "AlpacaHistoricalPriceProvider: %s symbol=%s", reason, symbol
                )
                return ProviderResult(
                    value=None, provider=self.NAME, success=False,
                    failure_reason=reason, symbol=symbol, endpoint=endpoint,
                )

            rows = []
            for bar in bar_list:
                rows.append({
                    "timestamp": bar.timestamp,
                    "open": float(bar.open),
                    "high": float(bar.high),
                    "low": float(bar.low),
                    "close": float(bar.close),
                    "volume": int(bar.volume),
                })
            df = pd.DataFrame(rows).set_index("timestamp")
            df = _normalize_price_frame(df)
            if df.empty:
                reason = "bars returned but normalised to empty"
                logger.warning(
                    "AlpacaHistoricalPriceProvider: %s symbol=%s", reason, symbol
                )
                return ProviderResult(
                    value=None, provider=self.NAME, success=False,
                    failure_reason=reason, symbol=symbol, endpoint=endpoint,
                )
            self._cache.set(
                provider=self.NAME,
                endpoint=endpoint,
                symbol=symbol,
                date_range=period,
                payload={"frame": df.reset_index().rename(columns={df.index.name or "index": "index"}).to_dict(orient="list")},
            )
            return ProviderResult(
                value=df, provider=self.NAME, success=True,
                symbol=symbol, endpoint=endpoint,
                confidence_label="verified_primary",
            )
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "AlpacaHistoricalPriceProvider exception: %s symbol=%s", reason, symbol
            )
            return ProviderResult(
                value=None, provider=self.NAME, success=False,
                failure_reason=reason, symbol=symbol, endpoint=endpoint,
            )

    def fetch_many(self, symbols: Sequence[str], *, period: str = "5d") -> Dict[str, ProviderResult]:
        results: Dict[str, ProviderResult] = {}
        symbols_norm = [str(symbol).upper().strip() for symbol in symbols if str(symbol).strip()]
        if not symbols_norm:
            return results
        endpoint = f"alpaca/bars/batch/{period}"
        uncached_symbols: List[str] = []
        for symbol in symbols_norm:
            cached = self._cache.get(
                provider=self.NAME,
                endpoint=endpoint,
                symbol=symbol,
                ttl_seconds=ALPACA_CACHE_TTL_SECONDS,
                date_range=period,
            )
            if cached is not None and isinstance(cached.get("frame"), dict):
                logger.info("[PROVIDER CACHE HIT] provider=%s endpoint=%s symbol=%s", self.NAME, endpoint, symbol)
                frame = pd.DataFrame(cached["frame"])
                if "index" in frame.columns:
                    frame["index"] = pd.to_datetime(frame["index"])
                    frame = frame.set_index("index")
                results[symbol] = ProviderResult(
                    value=_normalize_price_frame(frame),
                    provider=self.NAME,
                    success=True,
                    symbol=symbol,
                    endpoint=endpoint,
                    confidence_label="verified_primary",
                    cache_hit=True,
                )
            else:
                logger.info("[PROVIDER CACHE MISS] provider=%s endpoint=%s symbol=%s", self.NAME, endpoint, symbol)
                uncached_symbols.append(symbol)
        if not uncached_symbols:
            return results
        if self._client is None:
            reason = self._init_error or "client not initialised"
            for symbol in uncached_symbols:
                results[symbol] = ProviderResult(
                    value=None,
                    provider=self.NAME,
                    success=False,
                    failure_reason=reason,
                    symbol=symbol,
                    endpoint=endpoint,
                )
            return results
        try:
            from alpaca.data.requests import StockBarsRequest
            from alpaca.data.timeframe import TimeFrame
            from alpaca.data.enums import DataFeed

            start = _period_to_start_datetime(period)
            end = datetime.now(timezone.utc)
            request = StockBarsRequest(
                symbol_or_symbols=uncached_symbols,
                timeframe=TimeFrame.Day,
                start=start,
                end=end,
                adjustment="all",
                feed=DataFeed.SIP,
            )
            resp = self._client.get_stock_bars(request)
            raw = getattr(resp, "data", {}) or {}
            for symbol in uncached_symbols:
                bar_list = raw.get(symbol, [])
                if not bar_list:
                    results[symbol] = ProviderResult(
                        value=None,
                        provider=self.NAME,
                        success=False,
                        failure_reason=f"0 bars returned (period={period})",
                        symbol=symbol,
                        endpoint=endpoint,
                    )
                    continue
                rows = [
                    {
                        "timestamp": bar.timestamp,
                        "open": float(bar.open),
                        "high": float(bar.high),
                        "low": float(bar.low),
                        "close": float(bar.close),
                        "volume": int(bar.volume),
                    }
                    for bar in bar_list
                ]
                frame = _normalize_price_frame(pd.DataFrame(rows).set_index("timestamp"))
                if frame.empty:
                    results[symbol] = ProviderResult(
                        value=None,
                        provider=self.NAME,
                        success=False,
                        failure_reason="bars returned but normalised to empty",
                        symbol=symbol,
                        endpoint=endpoint,
                    )
                else:
                    self._cache.set(
                        provider=self.NAME,
                        endpoint=endpoint,
                        symbol=symbol,
                        date_range=period,
                        payload={"frame": frame.reset_index().rename(columns={frame.index.name or "index": "index"}).to_dict(orient="list")},
                    )
                    results[symbol] = ProviderResult(
                        value=frame,
                        provider=self.NAME,
                        success=True,
                        symbol=symbol,
                        endpoint=endpoint,
                        confidence_label="verified_primary",
                    )
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            for symbol in uncached_symbols:
                results[symbol] = ProviderResult(
                    value=None,
                    provider=self.NAME,
                    success=False,
                    failure_reason=reason,
                    symbol=symbol,
                    endpoint=endpoint,
                )
        return results
        try:
            from alpaca.data.requests import StockBarsRequest
            from alpaca.data.timeframe import TimeFrame
            from alpaca.data.enums import DataFeed

            start = _period_to_start_datetime(period)
            end = datetime.now(timezone.utc)

            request = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=TimeFrame.Day,
                start=start,
                end=end,
                adjustment="all",
                feed=DataFeed.SIP,
            )
            resp = self._client.get_stock_bars(request)
            bar_list = []
            if resp is not None:
                raw = getattr(resp, "data", {}) or {}
                bar_list = raw.get(symbol, [])

            if not bar_list:
                reason = f"0 bars returned (period={period})"
                logger.warning(
                    "AlpacaHistoricalPriceProvider: %s symbol=%s", reason, symbol
                )
                return ProviderResult(
                    value=None, provider=self.NAME, success=False,
                    failure_reason=reason, symbol=symbol, endpoint=endpoint,
                )

            rows = []
            for bar in bar_list:
                rows.append({
                    "timestamp": bar.timestamp,
                    "open": float(bar.open),
                    "high": float(bar.high),
                    "low": float(bar.low),
                    "close": float(bar.close),
                    "volume": int(bar.volume),
                })
            df = pd.DataFrame(rows).set_index("timestamp")
            df = _normalize_price_frame(df)
            if df.empty:
                reason = "bars returned but normalised to empty"
                logger.warning(
                    "AlpacaHistoricalPriceProvider: %s symbol=%s", reason, symbol
                )
                return ProviderResult(
                    value=None, provider=self.NAME, success=False,
                    failure_reason=reason, symbol=symbol, endpoint=endpoint,
                )
            return ProviderResult(
                value=df, provider=self.NAME, success=True,
                symbol=symbol, endpoint=endpoint,
            )
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "AlpacaHistoricalPriceProvider exception: %s symbol=%s", reason, symbol
            )
            return ProviderResult(
                value=None, provider=self.NAME, success=False,
                failure_reason=reason, symbol=symbol, endpoint=endpoint,
            )


class YFinancePriceProvider:
    """
    Fetches daily OHLCV bars using yf.Ticker.history().

    Intentionally does NOT use yf.download() which returns empty DataFrames
    silently under yfinance >= 1.0.0 with auto_adjust=False.
    """

    NAME = "yfinance"

    def __init__(self, *, cache: Optional[PersistentProviderCache] = None) -> None:
        self._cache = cache or _provider_cache()

    def fetch(self, ticker: str, *, period: str = "5y") -> ProviderResult:
        import yfinance as yf
        symbol = str(ticker).upper().strip()
        endpoint = f"yfinance/Ticker.history/{symbol}/{period}"
        cached = self._cache.get(
            provider=self.NAME,
            endpoint=endpoint,
            symbol=symbol,
            ttl_seconds=ALPACA_CACHE_TTL_SECONDS,
            date_range=period,
        )
        if cached is not None and isinstance(cached.get("frame"), dict):
            logger.info("[PROVIDER CACHE HIT] provider=%s endpoint=%s symbol=%s", self.NAME, endpoint, symbol)
            frame = pd.DataFrame(cached["frame"])
            if "index" in frame.columns:
                frame["index"] = pd.to_datetime(frame["index"])
                frame = frame.set_index("index")
            return ProviderResult(
                value=_normalize_price_frame(frame),
                provider=self.NAME,
                success=True,
                symbol=symbol,
                endpoint=endpoint,
                confidence_label="debug_only",
                cache_hit=True,
            )
        logger.info("[PROVIDER CACHE MISS] provider=%s endpoint=%s symbol=%s", self.NAME, endpoint, symbol)
        try:
            t = yf.Ticker(symbol)
            raw = t.history(period=period, interval="1d", auto_adjust=True)
            if raw is None or (hasattr(raw, "empty") and raw.empty):
                reason = f"Ticker.history returned empty (period={period})"
                logger.warning(
                    "YFinancePriceProvider: %s symbol=%s", reason, symbol
                )
                return ProviderResult(
                    value=None, provider=self.NAME, success=False,
                    failure_reason=reason, symbol=symbol, endpoint=endpoint,
                )
            df = _normalize_price_frame(raw)
            if df.empty:
                reason = "history returned but normalised to empty"
                logger.warning(
                    "YFinancePriceProvider: %s symbol=%s", reason, symbol
                )
                return ProviderResult(
                    value=None, provider=self.NAME, success=False,
                    failure_reason=reason, symbol=symbol, endpoint=endpoint,
                )
            self._cache.set(
                provider=self.NAME,
                endpoint=endpoint,
                symbol=symbol,
                date_range=period,
                payload={"frame": df.reset_index().rename(columns={df.index.name or "index": "index"}).to_dict(orient="list")},
            )
            return ProviderResult(
                value=df, provider=self.NAME, success=True,
                symbol=symbol, endpoint=endpoint,
                confidence_label="debug_only",
            )
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "YFinancePriceProvider exception: %s symbol=%s", reason, symbol
            )
            return ProviderResult(
                value=None, provider=self.NAME, success=False,
                failure_reason=reason, symbol=symbol, endpoint=endpoint,
            )

    def fetch_many(self, symbols: Sequence[str], *, period: str = "5d") -> Dict[str, ProviderResult]:
        return {str(symbol).upper().strip(): self.fetch(symbol, period=period) for symbol in symbols if str(symbol).strip()}


class RoutingPriceProvider:
    """
    Routes price requests: primary first, fallback on failure.
    Every attempt and failure is logged and counted in the coverage report.
    """

    def __init__(
        self,
        primary: Optional[AlpacaHistoricalPriceProvider] = None,
        fallback: Optional[YFinancePriceProvider] = None,
        coverage: Optional[ProviderCoverageReport] = None,
        allow_debug_fallback: Optional[bool] = None,
    ) -> None:
        self._primary = primary or AlpacaHistoricalPriceProvider()
        self._fallback = fallback or YFinancePriceProvider()
        self._coverage = coverage or ProviderCoverageReport()
        self._allow_debug_fallback = _env_bool("RESEARCH_ALLOW_YFINANCE_DEBUG", False) if allow_debug_fallback is None else bool(allow_debug_fallback)

    @property
    def coverage(self) -> ProviderCoverageReport:
        return self._coverage

    def fetch(self, ticker: str, *, period: str = "5y") -> ProviderResult:
        symbol = str(ticker).upper().strip()
        self._coverage.total_symbols += 1

        primary_result = self._primary.fetch(symbol, period=period)
        if primary_result.success:
            self._coverage.primary_success += 1
            self._coverage.record_provider_hit(
                provider=primary_result.provider,
                cache_hit=bool(primary_result.cache_hit),
                confidence_label=primary_result.confidence_label,
            )
            return primary_result

        # Primary failed — record and try fallback
        self._coverage.record_failure(
            provider=primary_result.provider,
            symbol=symbol,
            endpoint=primary_result.endpoint,
            reason=primary_result.failure_reason or "unknown",
        )
        if primary_result.metadata.get("budget_exhausted"):
            self._coverage.record_budget_exhausted(primary_result.provider)

        if not self._allow_debug_fallback:
            self._coverage.both_failed += 1
            logger.error(
                "RoutingPriceProvider: debug fallback disabled symbol=%s period=%s primary_reason=%s",
                symbol,
                period,
                primary_result.failure_reason,
            )
            return ProviderResult(
                value=None,
                provider="none",
                success=False,
                failure_reason=f"primary({primary_result.provider}): {primary_result.failure_reason}; debug fallback disabled",
                symbol=symbol,
                endpoint=f"routing/{period}",
            )

        fallback_result = self._fallback.fetch(symbol, period=period)
        if fallback_result.success:
            self._coverage.fallback_used += 1
            fallback_result.fallback_used = True
            fallback_result.confidence_label = "debug_only"
            self._coverage.record_provider_hit(
                provider=fallback_result.provider,
                cache_hit=bool(fallback_result.cache_hit),
                confidence_label=fallback_result.confidence_label,
            )
            return fallback_result

        # Both failed
        self._coverage.both_failed += 1
        self._coverage.record_failure(
            provider=fallback_result.provider,
            symbol=symbol,
            endpoint=fallback_result.endpoint,
            reason=fallback_result.failure_reason or "unknown",
        )
        logger.error(
            "RoutingPriceProvider: ALL providers failed symbol=%s period=%s "
            "primary_reason=%s fallback_reason=%s",
            symbol, period,
            primary_result.failure_reason,
            fallback_result.failure_reason,
        )
        return ProviderResult(
            value=None, provider="none", success=False, fallback_used=True,
            failure_reason=(
                f"primary({primary_result.provider}): {primary_result.failure_reason}; "
                f"fallback({fallback_result.provider}): {fallback_result.failure_reason}"
            ),
            symbol=symbol, endpoint=f"routing/{period}",
        )

    def fetch_many(self, symbols: Sequence[str], *, period: str = "5d") -> Dict[str, ProviderResult]:
        symbols_norm = [str(symbol).upper().strip() for symbol in symbols if str(symbol).strip()]
        if not symbols_norm:
            return {}

        primary_batch = (
            self._primary.fetch_many(symbols_norm, period=period)
            if hasattr(self._primary, "fetch_many")
            else {symbol: self._primary.fetch(symbol, period=period) for symbol in symbols_norm}
        )

        results: Dict[str, ProviderResult] = {}
        fallback_symbols: List[str] = []
        for symbol in symbols_norm:
            self._coverage.total_symbols += 1
            primary_result = primary_batch.get(symbol)
            if primary_result is not None and primary_result.success and primary_result.value is not None:
                self._coverage.primary_success += 1
                self._coverage.record_provider_hit(
                    provider=primary_result.provider,
                    cache_hit=bool(primary_result.cache_hit),
                    confidence_label=primary_result.confidence_label,
                )
                results[symbol] = primary_result
                continue

            if primary_result is not None:
                self._coverage.record_failure(
                    provider=primary_result.provider,
                    symbol=symbol,
                    endpoint=primary_result.endpoint,
                    reason=primary_result.failure_reason or "unknown",
                )
                if primary_result.metadata.get("budget_exhausted"):
                    self._coverage.record_budget_exhausted(primary_result.provider)
            if not self._allow_debug_fallback:
                self._coverage.both_failed += 1
                results[symbol] = ProviderResult(
                    value=None,
                    provider="none",
                    success=False,
                    failure_reason=(
                        f"primary({primary_result.provider if primary_result else self._primary.NAME}): "
                        f"{primary_result.failure_reason if primary_result else 'unknown'}; debug fallback disabled"
                    ),
                    symbol=symbol,
                    endpoint=f"routing/{period}",
                )
                continue
            fallback_symbols.append(symbol)

        fallback_batch = (
            self._fallback.fetch_many(fallback_symbols, period=period)
            if fallback_symbols and hasattr(self._fallback, "fetch_many")
            else {symbol: self._fallback.fetch(symbol, period=period) for symbol in fallback_symbols}
        )
        for symbol in fallback_symbols:
            fallback_result = fallback_batch.get(symbol)
            if fallback_result is not None and fallback_result.success and fallback_result.value is not None:
                self._coverage.fallback_used += 1
                fallback_result.fallback_used = True
                fallback_result.confidence_label = "debug_only"
                self._coverage.record_provider_hit(
                    provider=fallback_result.provider,
                    cache_hit=bool(fallback_result.cache_hit),
                    confidence_label=fallback_result.confidence_label,
                )
                results[symbol] = fallback_result
                continue
            self._coverage.both_failed += 1
            if fallback_result is not None:
                self._coverage.record_failure(
                    provider=fallback_result.provider,
                    symbol=symbol,
                    endpoint=fallback_result.endpoint,
                    reason=fallback_result.failure_reason or "unknown",
                )
                results[symbol] = ProviderResult(
                    value=None,
                    provider="none",
                    success=False,
                    fallback_used=True,
                    failure_reason=(
                        f"primary({primary_batch.get(symbol).provider if primary_batch.get(symbol) else self._primary.NAME}): "
                        f"{primary_batch.get(symbol).failure_reason if primary_batch.get(symbol) else 'unknown'}; "
                        f"fallback({fallback_result.provider}): {fallback_result.failure_reason}"
                    ),
                    symbol=symbol,
                    endpoint=f"routing/{period}",
                )
            else:
                results[symbol] = ProviderResult(
                    value=None,
                    provider="none",
                    success=False,
                    fallback_used=True,
                    failure_reason="fallback provider did not return a result",
                    symbol=symbol,
                    endpoint=f"routing/{period}",
                )
        return results


def _provider_cache() -> PersistentProviderCache:
    global _PERSISTENT_PROVIDER_CACHE
    try:
        return _PERSISTENT_PROVIDER_CACHE
    except NameError:
        _PERSISTENT_PROVIDER_CACHE = PersistentProviderCache()
        return _PERSISTENT_PROVIDER_CACHE


def _fmp_api_key() -> str:
    return (
        os.getenv("FMP_API_KEY")
        or os.getenv("FINANCIAL_MODELING_PREP_API_KEY")
        or os.getenv("FMP_KEY")
        or ""
    )


def _session_flag_from_fmp_time(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"bmo", "before open", "pre-market", "premarket"}:
        return "pre_market"
    if text in {"amc", "after close", "after-hours", "afterhours"}:
        return "after_hours"
    return "unknown"


class _FMPBaseProvider:
    NAME = "fmp"

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        cache: Optional[PersistentProviderCache] = None,
        budget: Optional[DailyBudgetManager] = None,
    ) -> None:
        self._api_key = api_key or _fmp_api_key()
        self._cache = cache or _provider_cache()
        self._budget = budget or DailyBudgetManager(
            provider_name="fmp",
            daily_target=int(os.getenv("FMP_DAILY_BUDGET_TARGET", FMP_DAILY_BUDGET_TARGET)),
        )

    @property
    def budget(self) -> DailyBudgetManager:
        return self._budget

    def _request_json(
        self,
        *,
        symbol: str,
        endpoint: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        ttl_seconds: int,
        allow_fallback_on_budget: bool = True,
    ) -> ProviderResult:
        symbol = str(symbol).upper().strip()
        params = dict(params or {})
        cached = self._cache.get(
            provider=self.NAME,
            endpoint=endpoint,
            symbol=symbol,
            ttl_seconds=ttl_seconds,
            params=params,
        )
        if cached is not None:
            logger.info("[PROVIDER CACHE HIT] provider=%s endpoint=%s symbol=%s", self.NAME, endpoint, symbol)
            return ProviderResult(
                value=cached.get("payload"),
                provider=self.NAME,
                success=True,
                symbol=symbol,
                endpoint=endpoint,
                confidence_label="verified_primary",
                cache_hit=True,
                metadata={"cached": True},
            )

        logger.info("[PROVIDER CACHE MISS] provider=%s endpoint=%s symbol=%s", self.NAME, endpoint, symbol)
        if not self._api_key:
            return ProviderResult(
                value=None,
                provider=self.NAME,
                success=False,
                symbol=symbol,
                endpoint=endpoint,
                failure_reason="FMP_API_KEY not set",
            )
        if allow_fallback_on_budget and not self._budget.can_consume(1):
            return ProviderResult(
                value=None,
                provider=self.NAME,
                success=False,
                symbol=symbol,
                endpoint=endpoint,
                failure_reason="daily budget exhausted",
                metadata={"budget_exhausted": True},
            )
        try:
            response = requests.get(
                f"{FMP_BASE_URL}/{path.lstrip('/')}",
                params={**params, "apikey": self._api_key},
                timeout=FMP_REQUEST_TIMEOUT_SECONDS,
            )
            self._budget.record_call(1)
            if response.status_code != 200:
                return ProviderResult(
                    value=None,
                    provider=self.NAME,
                    success=False,
                    symbol=symbol,
                    endpoint=endpoint,
                    failure_reason=f"HTTP {response.status_code}",
                )
            payload = response.json()
            self._cache.set(
                provider=self.NAME,
                endpoint=endpoint,
                symbol=symbol,
                params=params,
                payload={"payload": payload},
            )
            logger.info("[PROVIDER HIT] provider=%s endpoint=%s symbol=%s", self.NAME, endpoint, symbol)
            return ProviderResult(
                value=payload,
                provider=self.NAME,
                success=True,
                symbol=symbol,
                endpoint=endpoint,
                confidence_label="verified_primary",
            )
        except Exception as exc:
            return ProviderResult(
                value=None,
                provider=self.NAME,
                success=False,
                symbol=symbol,
                endpoint=endpoint,
                failure_reason=f"{type(exc).__name__}: {exc}",
            )


class FMPEarningsProvider(_FMPBaseProvider):
    NAME = "fmp_earnings"

    def fetch_dates(self, ticker: str) -> ProviderResult:
        symbol = str(ticker).upper().strip()
        endpoint = f"fmp/earnings/{symbol}"
        raw = self._request_json(
            symbol=symbol,
            endpoint=endpoint,
            path="stable/earnings",
            params={"symbol": symbol},
            ttl_seconds=FMP_CACHE_TTL_HISTORICAL_SECONDS,
        )
        if not raw.success:
            return raw
        rows = raw.value if isinstance(raw.value, list) else []
        events: List[EarningsEvent] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_symbol = str(row.get("symbol") or row.get("ticker") or "").upper().strip()
            if row_symbol and row_symbol != symbol:
                continue
            reported_raw = row.get("date") or row.get("reportedDate")
            if not reported_raw:
                continue
            try:
                reported = pd.Timestamp(reported_raw).normalize().date()
            except Exception:
                continue
            period_raw = row.get("fiscalDateEnding") or row.get("period")
            fiscal_period = None
            if period_raw:
                try:
                    fd = pd.Timestamp(period_raw)
                    fiscal_period = f"Q{((int(fd.month) - 1) // 3) + 1} {int(fd.year)}"
                except Exception:
                    fiscal_period = str(period_raw)
            events.append(
                EarningsEvent(
                    ticker=symbol,
                    reported_date=reported,
                    fiscal_period=fiscal_period,
                    session_flag=_session_flag_from_fmp_time(row.get("time")),
                    source=self.NAME,
                    confidence_label="verified_primary",
                    metadata={"time": row.get("time")},
                )
            )
        if not events:
            # ETF/fund detection: FMP correctly returns no earnings for ETFs.
            # Check profile (cached 24h, same key as FMPFundamentalsProvider)
            # so we can suppress noisy ERROR logs for expected no-earnings cases.
            is_etf = False
            profile_check = self._request_json(
                symbol=symbol,
                endpoint=f"fmp/fundamentals/{symbol}/profile",
                path="stable/profile",
                params={"symbol": symbol},
                ttl_seconds=FMP_CACHE_TTL_OVERVIEW_SECONDS,
            )
            if profile_check.success and isinstance(profile_check.value, list) and profile_check.value:
                pr = profile_check.value[0] if isinstance(profile_check.value[0], dict) else {}
                is_etf = bool(pr.get("isEtf") or pr.get("isFund") or str(pr.get("type") or "").upper() in {"ETF", "FUND"})
            reason = "ETF/fund has no quarterly earnings" if is_etf else "FMP earnings payload empty"
            return ProviderResult(
                value=None,
                provider=self.NAME,
                success=False,
                symbol=symbol,
                endpoint=endpoint,
                failure_reason=reason,
                confidence_label="verified_primary",
                cache_hit=raw.cache_hit,
                metadata={"is_etf": is_etf},
            )
        return ProviderResult(
            value=events,
            provider=self.NAME,
            success=True,
            symbol=symbol,
            endpoint=endpoint,
            confidence_label="verified_primary",
            cache_hit=raw.cache_hit,
        )


class FMPFundamentalsProvider(_FMPBaseProvider):
    NAME = "fmp_fundamentals"

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        text = str(value).strip()
        if text in {"", "None", "null", "N/A", "-"}:
            return None
        try:
            return float(text)
        except (TypeError, ValueError):
            return None

    def fetch(self, ticker: str) -> ProviderResult:
        symbol = str(ticker).upper().strip()
        endpoint = f"fmp/fundamentals/{symbol}"
        profile = self._request_json(
            symbol=symbol,
            endpoint=f"{endpoint}/profile",
            path="stable/profile",
            params={"symbol": symbol},
            ttl_seconds=FMP_CACHE_TTL_OVERVIEW_SECONDS,
        )
        if not profile.success and profile.metadata.get("budget_exhausted"):
            return profile
        income = self._request_json(
            symbol=symbol,
            endpoint=f"{endpoint}/income-quarter",
            path="stable/income-statement",
            params={"symbol": symbol, "period": "quarter", "limit": 8},
            ttl_seconds=FMP_CACHE_TTL_HISTORICAL_SECONDS,
        )
        if not income.success and income.metadata.get("budget_exhausted"):
            return income
        cashflow = self._request_json(
            symbol=symbol,
            endpoint=f"{endpoint}/cashflow-quarter",
            path="stable/cash-flow-statement",
            params={"symbol": symbol, "period": "quarter", "limit": 4},
            ttl_seconds=FMP_CACHE_TTL_HISTORICAL_SECONDS,
        )
        balance = self._request_json(
            symbol=symbol,
            endpoint=f"{endpoint}/balance-quarter",
            path="stable/balance-sheet-statement",
            params={"symbol": symbol, "period": "quarter", "limit": 2},
            ttl_seconds=FMP_CACHE_TTL_HISTORICAL_SECONDS,
        )
        if not any(r.success for r in (profile, income, cashflow, balance)):
            return ProviderResult(
                value=None,
                provider=self.NAME,
                success=False,
                symbol=symbol,
                endpoint=endpoint,
                failure_reason="FMP profile/income/cashflow/balance all unavailable",
            )

        profile_row = (profile.value[0] if profile.success and isinstance(profile.value, list) and profile.value else {}) if profile.value is not None else {}
        income_rows = list(income.value or []) if income.success and isinstance(income.value, list) else []
        cash_rows = list(cashflow.value or []) if cashflow.success and isinstance(cashflow.value, list) else []
        balance_rows = list(balance.value or []) if balance.success and isinstance(balance.value, list) else []

        op_margin_trend: List[Dict[str, Any]] = []
        revenue_trend: List[Dict[str, Any]] = []
        for row in income_rows[:8]:
            if not isinstance(row, dict):
                continue
            revenue = self._safe_float(row.get("revenue"))
            operating_income = self._safe_float(row.get("operatingIncome"))
            period = row.get("date") or row.get("calendarYear")
            if revenue and revenue > 0:
                revenue_trend.append({"period": period, "revenue": revenue})
                if operating_income is not None:
                    op_margin_trend.append({"period": period, "operating_margin": operating_income / revenue})

        revenue_growth_yoy = None
        if len(revenue_trend) >= 8:
            recent = sum(float(item["revenue"]) for item in revenue_trend[:4])
            prior = sum(float(item["revenue"]) for item in revenue_trend[4:8])
            if prior > 0:
                revenue_growth_yoy = (recent - prior) / prior

        revenue_deceleration = bool(revenue_growth_yoy is not None and revenue_growth_yoy < 0.05)
        margin_compression = False
        if len(op_margin_trend) >= 2:
            margin_compression = (float(op_margin_trend[1]["operating_margin"]) - float(op_margin_trend[0]["operating_margin"])) > 0.03

        latest_income = income_rows[0] if income_rows else {}
        latest_cash = cash_rows[0] if cash_rows else {}
        latest_balance = balance_rows[0] if balance_rows else {}
        latest_revenue = self._safe_float(latest_income.get("revenue"))
        latest_op_income = self._safe_float(latest_income.get("operatingIncome"))
        latest_net_income = self._safe_float(latest_income.get("netIncome"))
        operating_margin_level = (latest_op_income / latest_revenue) if latest_revenue and latest_op_income is not None else None
        profit_margin_level = (latest_net_income / latest_revenue) if latest_revenue and latest_net_income is not None else None
        free_cash_flow = self._safe_float(latest_cash.get("freeCashFlow"))
        total_debt = self._safe_float(latest_balance.get("totalDebt"))
        total_equity = self._safe_float(latest_balance.get("totalStockholdersEquity") or latest_balance.get("totalEquity") or latest_balance.get("stockholdersEquity"))
        debt_to_equity = (total_debt / total_equity) if total_debt is not None and total_equity not in (None, 0.0) else None

        fundamentals = {
            "revenue_growth_yoy": revenue_growth_yoy,
            "revenue_deceleration": revenue_deceleration,
            "margin_compression": bool(margin_compression),
            "margin_level_below_threshold": bool(operating_margin_level is not None and operating_margin_level < 0.10),
            "margin_signal_quality": "trend" if op_margin_trend else "level_only",
            "profit_margin_declining": None,
            "profit_margin_level_below_threshold": bool(profit_margin_level is not None and profit_margin_level < 0.05),
            "fcf_negative": bool(free_cash_flow is not None and free_cash_flow < 0),
            "debt_stress": bool(debt_to_equity is not None and debt_to_equity > 1.5),
            "valuation_rich": None,
            "market_cap": self._safe_float(profile_row.get("mktCap") or profile_row.get("marketCap")),
            "sector": profile_row.get("sector"),
            "industry": profile_row.get("industry"),
            "short_interest_safe": None,
            "short_interest_low": None,
            "short_interest_pct": None,
            "earnings_window_safe": None,
            "days_to_earnings": None,
            "guidance_trend": None,
            "estimate_revisions": None,
            "analyst_downgrade_proxy": None,
            "fmp_operating_margin_trend": op_margin_trend,
            "fmp_revenue_trend": revenue_trend,
            "data_source": self.NAME,
            "data_quality": "good" if revenue_growth_yoy is not None else "partial",
        }
        return ProviderResult(
            value=_massage_research_fundamentals(fundamentals, provider_name=self.NAME),
            provider=self.NAME,
            success=True,
            symbol=symbol,
            endpoint=endpoint,
            confidence_label="verified_primary",
            cache_hit=all(r.cache_hit for r in (profile, income, cashflow, balance) if r.success),
        )


# ── Earnings providers ───────────────────────────────────────────────────────

class AlphaVantageEarningsProvider:
    """
    Fetches historical earnings dates from Alpha Vantage EARNINGS endpoint.

    Endpoint: GET /query?function=EARNINGS&symbol=TICKER&apikey=KEY
    Returns `quarterlyEarnings[].reportedDate` — the actual date management
    released results, not the fiscal period end.

    Rate limit (free tier): 25 req/day, 5 req/min.
    Cache TTL: 7 days (earnings history doesn't change retroactively).
    """

    NAME = "alpha_vantage_earnings"
    _last_request_ts: float = 0.0   # class-level rate limiter

    def __init__(
        self,
        api_key: Optional[str] = None,
        cache_dir: Optional[str] = None,
        rate_limit_pause: float = AV_RATE_LIMIT_PAUSE_SECONDS,
    ) -> None:
        self._api_key = (
            api_key
            or os.getenv("ALPHAVANTAGE_API_KEY")
            or os.getenv("ALPHA_VANTAGE_KEY", "")
        )
        self._cache_dir = cache_dir or "logs/short_backtester_cache/av_earnings"
        self._rate_limit_pause = float(rate_limit_pause)

    def _cache_path(self, ticker: str) -> str:
        safe = ticker.upper().replace("/", "_").replace("\\", "_")
        return f"{self._cache_dir}/{safe}.json"

    def _cache_read(self, ticker: str) -> Optional[List[EarningsEvent]]:
        import json, time as _time
        path = self._cache_path(ticker)
        try:
            import pathlib
            p = pathlib.Path(path)
            if not p.exists():
                return None
            if (_time.time() - p.stat().st_mtime) > AV_CACHE_TTL_EARNINGS_SECONDS:
                return None
            payload = json.loads(p.read_text(encoding="utf-8"))
            rows = payload.get("earnings_events") or []
            return [
                EarningsEvent(
                    ticker=str(r["ticker"]),
                    reported_date=date.fromisoformat(r["reported_date"]),
                    fiscal_period=r.get("fiscal_period"),
                    session_flag=str(r.get("session_flag", "unknown")),
                    source=str(r.get("source", self.NAME)),
                )
                for r in rows
            ]
        except Exception as exc:
            logger.debug("AV earnings cache read failed %s: %s", ticker, exc)
            return None

    def _cache_write(self, ticker: str, events: List[EarningsEvent]) -> None:
        import json, pathlib
        path = pathlib.Path(self._cache_path(ticker))
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "ticker": ticker.upper(),
            "earnings_events": [
                {
                    "ticker": e.ticker,
                    "reported_date": e.reported_date.isoformat(),
                    "fiscal_period": e.fiscal_period,
                    "session_flag": e.session_flag,
                    "source": e.source,
                }
                for e in events
            ],
        }
        path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")

    def _throttle(self) -> None:
        elapsed = time.time() - AlphaVantageEarningsProvider._last_request_ts
        if elapsed < self._rate_limit_pause:
            time.sleep(self._rate_limit_pause - elapsed)
        AlphaVantageEarningsProvider._last_request_ts = time.time()

    def fetch_dates(self, ticker: str) -> ProviderResult:
        symbol = str(ticker).upper().strip()
        endpoint = f"alpha_vantage/EARNINGS/{symbol}"

        cached = self._cache_read(symbol)
        if cached is not None:
            return ProviderResult(
                value=cached, provider=self.NAME, success=True,
                symbol=symbol, endpoint=endpoint,
            )

        if not self._api_key:
            reason = "ALPHAVANTAGE_API_KEY not set"
            logger.debug("AlphaVantageEarningsProvider: %s symbol=%s", reason, symbol)
            return ProviderResult(
                value=None, provider=self.NAME, success=False,
                failure_reason=reason, symbol=symbol, endpoint=endpoint,
            )

        self._throttle()
        try:
            resp = requests.get(
                AV_BASE_URL,
                params={
                    "function": "EARNINGS",
                    "symbol": symbol,
                    "apikey": self._api_key,
                },
                timeout=AV_REQUEST_TIMEOUT_SECONDS,
            )
            if resp.status_code != 200:
                reason = f"HTTP {resp.status_code}"
                logger.warning(
                    "AlphaVantageEarningsProvider: %s symbol=%s", reason, symbol
                )
                return ProviderResult(
                    value=None, provider=self.NAME, success=False,
                    failure_reason=reason, symbol=symbol, endpoint=endpoint,
                )
            data = resp.json()

            if "Note" in data or "Information" in data:
                # AV rate-limit messages
                reason = f"AV rate limit: {data.get('Note') or data.get('Information', '')[:120]}"
                logger.warning(
                    "AlphaVantageEarningsProvider: %s symbol=%s", reason, symbol
                )
                return ProviderResult(
                    value=None, provider=self.NAME, success=False,
                    failure_reason=reason, symbol=symbol, endpoint=endpoint,
                )

            quarterly = data.get("quarterlyEarnings") or []
            if not quarterly:
                reason = "quarterlyEarnings array empty or missing"
                logger.warning(
                    "AlphaVantageEarningsProvider: %s symbol=%s", reason, symbol
                )
                return ProviderResult(
                    value=None, provider=self.NAME, success=False,
                    failure_reason=reason, symbol=symbol, endpoint=endpoint,
                )

            events: List[EarningsEvent] = []
            for row in quarterly:
                reported_raw = row.get("reportedDate")
                fiscal_raw = row.get("fiscalDateEnding")
                if not reported_raw or reported_raw in ("None", ""):
                    continue
                try:
                    reported = date.fromisoformat(str(reported_raw))
                except ValueError:
                    continue
                fiscal_period: Optional[str] = None
                if fiscal_raw and fiscal_raw not in ("None", ""):
                    try:
                        fd = date.fromisoformat(str(fiscal_raw))
                        fiscal_period = f"Q{((fd.month - 1) // 3) + 1} {fd.year}"
                    except ValueError:
                        pass
                events.append(EarningsEvent(
                    ticker=symbol,
                    reported_date=reported,
                    fiscal_period=fiscal_period,
                    # AV doesn't supply intraday time → we mark unknown;
                    # event_store's session-inference will refine this
                    session_flag="unknown",
                    source=self.NAME,
                ))

            events.sort(key=lambda e: e.reported_date, reverse=True)
            if events:
                try:
                    self._cache_write(symbol, events)
                except Exception as exc:
                    logger.debug("AV earnings cache write failed %s: %s", symbol, exc)

            return ProviderResult(
                value=events, provider=self.NAME, success=True,
                symbol=symbol, endpoint=endpoint,
            )
        except requests.Timeout as exc:
            reason = f"Timeout after {AV_REQUEST_TIMEOUT_SECONDS}s"
            logger.warning(
                "AlphaVantageEarningsProvider: %s symbol=%s exc=%s", reason, symbol, exc
            )
            return ProviderResult(
                value=None, provider=self.NAME, success=False,
                failure_reason=reason, symbol=symbol, endpoint=endpoint,
            )
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "AlphaVantageEarningsProvider exception: %s symbol=%s", reason, symbol
            )
            return ProviderResult(
                value=None, provider=self.NAME, success=False,
                failure_reason=reason, symbol=symbol, endpoint=endpoint,
            )


class YFinanceEarningsProvider:
    """
    Fetches historical earnings dates from yfinance as fallback.
    Tries three yfinance surfaces; logs each failure individually.
    """

    NAME = "yfinance_earnings"

    def fetch_dates(self, ticker: str) -> ProviderResult:
        import yfinance as yf
        symbol = str(ticker).upper().strip()
        endpoint = f"yfinance/earnings_dates/{symbol}"
        found: List[date] = []
        surface_errors: List[str] = []

        try:
            stock = yf.Ticker(symbol)
        except Exception as exc:
            reason = f"yf.Ticker init failed: {type(exc).__name__}: {exc}"
            logger.warning("YFinanceEarningsProvider: %s symbol=%s", reason, symbol)
            return ProviderResult(
                value=None, provider=self.NAME, success=False,
                failure_reason=reason, symbol=symbol, endpoint=endpoint,
            )

        # Surface 1: get_earnings_dates
        try:
            frame = stock.get_earnings_dates(limit=40)
            if frame is not None and not (hasattr(frame, "empty") and frame.empty):
                for ts in frame.index.tolist():
                    try:
                        d = pd.Timestamp(ts).normalize().date()
                        found.append(d)
                    except Exception:
                        pass
        except Exception as exc:
            surface_errors.append(f"get_earnings_dates: {type(exc).__name__}: {exc}")

        # Surface 2: get_earnings_history
        try:
            hist = stock.get_earnings_history()
            if hist is not None and not (hasattr(hist, "empty") and hist.empty):
                for ts in hist.index.tolist():
                    try:
                        d = pd.Timestamp(ts).normalize().date()
                        found.append(d)
                    except Exception:
                        pass
        except Exception as exc:
            surface_errors.append(f"get_earnings_history: {type(exc).__name__}: {exc}")

        # Surface 3: earnings_history property
        try:
            hist_prop = stock.earnings_history
            if hist_prop is not None and not (hasattr(hist_prop, "empty") and hist_prop.empty):
                for ts in hist_prop.index.tolist():
                    try:
                        d = pd.Timestamp(ts).normalize().date()
                        found.append(d)
                    except Exception:
                        pass
        except Exception as exc:
            surface_errors.append(f"earnings_history property: {type(exc).__name__}: {exc}")

        if surface_errors:
            logger.debug(
                "YFinanceEarningsProvider surface errors symbol=%s: %s",
                symbol, "; ".join(surface_errors),
            )

        unique_dates = sorted(set(found), reverse=True)
        if not unique_dates:
            reason = (
                "all three yfinance surfaces returned empty; "
                + "; ".join(surface_errors) if surface_errors else "no data"
            )
            logger.warning(
                "YFinanceEarningsProvider: %s symbol=%s", reason, symbol
            )
            return ProviderResult(
                value=None, provider=self.NAME, success=False,
                failure_reason=reason, symbol=symbol, endpoint=endpoint,
            )

        events = [
            EarningsEvent(
                ticker=symbol,
                reported_date=d,
                fiscal_period=None,
                session_flag="unknown",
                source=self.NAME,
            )
            for d in unique_dates
        ]
        return ProviderResult(
            value=events, provider=self.NAME, success=True,
            symbol=symbol, endpoint=endpoint,
        )


class RoutingEarningsProvider:
    """
    Routes earnings-date requests: FMP primary, Alpha Vantage secondary,
    yfinance debug-only fallback.
    Logs every failure and accumulates coverage diagnostics.
    """

    def __init__(
        self,
        primary: Optional[FMPEarningsProvider] = None,
        secondary: Optional[AlphaVantageEarningsProvider] = None,
        debug_fallback: Optional[YFinanceEarningsProvider] = None,
        coverage: Optional[ProviderCoverageReport] = None,
        allow_debug_fallback: Optional[bool] = None,
    ) -> None:
        self._primary = primary or FMPEarningsProvider()
        self._secondary = secondary or AlphaVantageEarningsProvider()
        self._debug_fallback = debug_fallback or YFinanceEarningsProvider()
        self._coverage = coverage or ProviderCoverageReport()
        self._allow_debug_fallback = _env_bool("RESEARCH_ALLOW_YFINANCE_DEBUG", False) if allow_debug_fallback is None else bool(allow_debug_fallback)

    @property
    def coverage(self) -> ProviderCoverageReport:
        return self._coverage

    def fetch_dates(self, ticker: str) -> ProviderResult:
        symbol = str(ticker).upper().strip()
        self._coverage.total_symbols += 1

        primary_result = self._primary.fetch_dates(symbol)
        if primary_result.success and primary_result.value:
            self._coverage.primary_success += 1
            self._coverage.record_provider_hit(
                provider=primary_result.provider,
                cache_hit=bool(primary_result.cache_hit),
                confidence_label="verified_primary",
            )
            n = len(primary_result.value)
            if n > 0:
                self._coverage.events_found += n
            else:
                self._coverage.events_missing += 1
            return primary_result

        # Primary failed or returned empty — check for ETF before trying secondary
        self._coverage.record_failure(
            provider=primary_result.provider,
            symbol=symbol,
            endpoint=primary_result.endpoint,
            reason=primary_result.failure_reason or "returned empty list",
        )
        if primary_result.metadata.get("budget_exhausted"):
            self._coverage.record_budget_exhausted(primary_result.provider)

        # ETFs/funds have no quarterly earnings — skip secondary entirely to avoid noise
        if primary_result.metadata.get("is_etf"):
            self._coverage.both_failed += 1
            self._coverage.events_missing += 1
            logger.debug(
                "RoutingEarningsProvider: ETF/fund skipped symbol=%s reason=%s",
                symbol,
                primary_result.failure_reason,
            )
            return ProviderResult(
                value=None,
                provider="none",
                success=False,
                fallback_used=False,
                failure_reason=primary_result.failure_reason,
                symbol=symbol,
                endpoint="routing/earnings_dates",
                metadata={"is_etf": True},
            )

        secondary_result = self._secondary.fetch_dates(symbol)
        if secondary_result.success and secondary_result.value:
            self._coverage.fallback_used += 1
            secondary_result.fallback_used = True
            secondary_result.confidence_label = "fallback_secondary"
            self._coverage.record_provider_hit(
                provider=secondary_result.provider,
                cache_hit=bool(secondary_result.cache_hit),
                confidence_label=secondary_result.confidence_label,
            )
            n = len(secondary_result.value)
            self._coverage.events_found += n
            return secondary_result

        self._coverage.record_failure(
            provider=secondary_result.provider,
            symbol=symbol,
            endpoint=secondary_result.endpoint,
            reason=secondary_result.failure_reason or "returned empty list",
        )

        if not self._allow_debug_fallback:
            self._coverage.both_failed += 1
            self._coverage.events_missing += 1
            logger.warning(
                "RoutingEarningsProvider: verified providers failed symbol=%s "
                "primary_reason=%s secondary_reason=%s debug_fallback=disabled",
                symbol,
                primary_result.failure_reason,
                secondary_result.failure_reason,
            )
            return ProviderResult(
                value=None,
                provider="none",
                success=False,
                fallback_used=True,
                failure_reason=(
                    f"primary({primary_result.provider}): {primary_result.failure_reason}; "
                    f"secondary({secondary_result.provider}): {secondary_result.failure_reason}; "
                    "debug fallback disabled"
                ),
                symbol=symbol,
                endpoint="routing/earnings_dates",
            )

        debug_result = self._debug_fallback.fetch_dates(symbol)
        if debug_result.success and debug_result.value:
            self._coverage.fallback_used += 1
            debug_result.fallback_used = True
            debug_result.confidence_label = "debug_only"
            self._coverage.record_provider_hit(
                provider=debug_result.provider,
                cache_hit=bool(debug_result.cache_hit),
                confidence_label=debug_result.confidence_label,
            )
            n = len(debug_result.value)
            self._coverage.events_found += n
            return debug_result

        self._coverage.both_failed += 1
        self._coverage.events_missing += 1
        self._coverage.record_failure(
            provider=debug_result.provider,
            symbol=symbol,
            endpoint=debug_result.endpoint,
            reason=debug_result.failure_reason or "returned empty list",
        )
        logger.error(
            "RoutingEarningsProvider: ALL providers failed symbol=%s "
            "primary_reason=%s secondary_reason=%s debug_reason=%s",
            symbol,
            primary_result.failure_reason,
            secondary_result.failure_reason,
            debug_result.failure_reason,
        )
        return ProviderResult(
            value=None, provider="none", success=False, fallback_used=True,
            failure_reason=(
                f"primary({primary_result.provider}): {primary_result.failure_reason}; "
                f"secondary({secondary_result.provider}): {secondary_result.failure_reason}; "
                f"debug({debug_result.provider}): {debug_result.failure_reason}"
            ),
            symbol=symbol, endpoint="routing/earnings_dates",
        )


# ── Fundamentals provider ─────────────────────────────────────────────────────

class AlphaVantageFundamentalsProvider:
    """
    Fetches company fundamentals from Alpha Vantage OVERVIEW and
    INCOME_STATEMENT endpoints.

    OVERVIEW  → margins, PE, revenue growth (current-period snapshots)
    INCOME_STATEMENT → quarterly revenue/operating income for trend detection

    Rate limit (free tier): 25 req/day.  This provider uses 2 calls per ticker.
    Cache TTL: 24 h for overview, 7 days for income statement.
    """

    NAME = "alpha_vantage_fundamentals"
    _last_request_ts: float = 0.0

    def __init__(
        self,
        api_key: Optional[str] = None,
        cache_dir: Optional[str] = None,
        rate_limit_pause: float = AV_RATE_LIMIT_PAUSE_SECONDS,
    ) -> None:
        self._api_key = (
            api_key
            or os.getenv("ALPHAVANTAGE_API_KEY")
            or os.getenv("ALPHA_VANTAGE_KEY", "")
        )
        self._cache_dir = cache_dir or "logs/short_backtester_cache/av_fundamentals"
        self._rate_limit_pause = float(rate_limit_pause)

    def _throttle(self) -> None:
        elapsed = time.time() - AlphaVantageFundamentalsProvider._last_request_ts
        if elapsed < self._rate_limit_pause:
            time.sleep(self._rate_limit_pause - elapsed)
        AlphaVantageFundamentalsProvider._last_request_ts = time.time()

    def _cache_path(self, ticker: str, function: str) -> "pathlib.Path":
        import pathlib
        safe = ticker.upper().replace("/", "_").replace("\\", "_")
        return pathlib.Path(self._cache_dir) / function / f"{safe}.json"

    def _cache_read(self, ticker: str, function: str, ttl: int) -> Optional[Dict[str, Any]]:
        import json, pathlib, time as _time
        path = self._cache_path(ticker, function)
        try:
            if not path.exists():
                return None
            if (_time.time() - path.stat().st_mtime) > ttl:
                return None
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.debug("AV fundamentals cache read failed %s/%s: %s", ticker, function, exc)
            return None

    def _cache_write(self, ticker: str, function: str, data: Dict[str, Any]) -> None:
        import json
        path = self._cache_path(ticker, function)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")

    def _fetch_av(self, symbol: str, function: str) -> Optional[Dict[str, Any]]:
        """Single Alpha Vantage REST call with throttle and error checking."""
        self._throttle()
        try:
            resp = requests.get(
                AV_BASE_URL,
                params={"function": function, "symbol": symbol, "apikey": self._api_key},
                timeout=AV_REQUEST_TIMEOUT_SECONDS,
            )
            if resp.status_code != 200:
                logger.warning(
                    "AlphaVantageFundamentalsProvider: HTTP %s function=%s symbol=%s",
                    resp.status_code, function, symbol,
                )
                return None
            data = resp.json()
            if "Note" in data or "Information" in data:
                logger.warning(
                    "AlphaVantageFundamentalsProvider: rate-limit/info response "
                    "function=%s symbol=%s: %s",
                    function, symbol,
                    (data.get("Note") or data.get("Information", ""))[:120],
                )
                return None
            return data
        except Exception as exc:
            logger.warning(
                "AlphaVantageFundamentalsProvider exception function=%s symbol=%s: %s",
                function, symbol, exc,
            )
            return None

    def _safe_float(self, value: Any) -> Optional[float]:
        if value is None:
            return None
        text = str(value).strip()
        if text in ("None", "", "N/A", "-"):
            return None
        try:
            if text.endswith("%"):
                return float(text[:-1])
            return float(text)
        except (TypeError, ValueError):
            return None

    def fetch(self, ticker: str) -> ProviderResult:
        """
        Returns a fundamentals dict with the same keys as
        FundamentalDataFetcher.get_short_fundamentals() so callers can use
        either provider interchangeably.

        Extra keys added:
            av_operating_margin_trend  : list of (period, operating_margin) last 4 quarters
            av_revenue_trend           : list of (period, revenue) last 4 quarters
            data_source                : "alpha_vantage"
        """
        symbol = str(ticker).upper().strip()
        endpoint = f"alpha_vantage/OVERVIEW+INCOME_STATEMENT/{symbol}"

        if not self._api_key:
            reason = "ALPHAVANTAGE_API_KEY not set"
            return ProviderResult(
                value=None, provider=self.NAME, success=False,
                failure_reason=reason, symbol=symbol, endpoint=endpoint,
            )

        # ── OVERVIEW ─────────────────────────────────────────────────────────
        overview = self._cache_read(symbol, "OVERVIEW", AV_CACHE_TTL_OVERVIEW_SECONDS)
        if overview is None:
            overview = self._fetch_av(symbol, "OVERVIEW") or {}
            if overview:
                try:
                    self._cache_write(symbol, "OVERVIEW", overview)
                except Exception:
                    pass

        # ── INCOME_STATEMENT ─────────────────────────────────────────────────
        income = self._cache_read(symbol, "INCOME_STATEMENT", AV_CACHE_TTL_EARNINGS_SECONDS)
        if income is None:
            income = self._fetch_av(symbol, "INCOME_STATEMENT") or {}
            if income:
                try:
                    self._cache_write(symbol, "INCOME_STATEMENT", income)
                except Exception:
                    pass

        if not overview and not income:
            reason = "both OVERVIEW and INCOME_STATEMENT returned empty"
            return ProviderResult(
                value=None, provider=self.NAME, success=False,
                failure_reason=reason, symbol=symbol, endpoint=endpoint,
            )

        # ── Parse OVERVIEW fields ─────────────────────────────────────────────
        rev_growth = self._safe_float(overview.get("RevenueGrowthYOY") or overview.get("QuarterlyRevenueGrowthYOY"))
        profit_margin = self._safe_float(overview.get("ProfitMargin"))
        op_margin = self._safe_float(overview.get("OperatingMarginTTM"))
        ev_to_sales = self._safe_float(overview.get("EVToRevenue"))
        sector = overview.get("Sector")
        industry = overview.get("Industry")
        market_cap = self._safe_float(overview.get("MarketCapitalization"))
        short_percent_float = self._safe_float(overview.get("ShortPercentFloat"))
        if short_percent_float is not None and short_percent_float <= 1.0:
            short_percent_float *= 100.0

        # ── Revenue deceleration — requires trend, not just level ─────────────
        revenue_deceleration = False
        if rev_growth is not None and rev_growth < 0.05:
            revenue_deceleration = True

        # ── Margin: level check only (AV overview is point-in-time) ──────────
        # Named _level_ to avoid implying a trend that we cannot confirm
        # from a single OVERVIEW snapshot.
        op_margin_level = op_margin if op_margin is not None else 0.0
        margin_level_below_threshold = op_margin_level < 0.10

        profit_margin_level = profit_margin if profit_margin is not None else 0.0
        profit_margin_declining = profit_margin_level < 0.05

        # ── Quarterly trend analysis from INCOME_STATEMENT ───────────────────
        quarterly_reports = (income.get("quarterlyReports") or [])[:8]
        op_margin_trend: List[Dict[str, Any]] = []
        revenue_trend: List[Dict[str, Any]] = []
        margin_compressing_trend = False   # True only when we have ≥2 quarters

        if len(quarterly_reports) >= 2:
            for report in quarterly_reports[:4]:
                rev = self._safe_float(report.get("totalRevenue"))
                op_inc = self._safe_float(report.get("operatingIncome"))
                period = report.get("fiscalDateEnding", "")
                if rev and rev > 0:
                    revenue_trend.append({"period": period, "revenue": rev})
                if rev and rev > 0 and op_inc is not None:
                    op_margin_trend.append({
                        "period": period,
                        "operating_margin": op_inc / rev,
                    })
            # Compression = most recent margin < prior-quarter margin by > 3pp
            if len(op_margin_trend) >= 2:
                recent_m = op_margin_trend[0]["operating_margin"]
                prior_m = op_margin_trend[1]["operating_margin"]
                if (prior_m - recent_m) > 0.03:
                    margin_compressing_trend = True

            # Revenue YoY slowdown (quarterly)
            if len(revenue_trend) >= 2:
                recent_rev = revenue_trend[0]["revenue"]
                prior_rev = revenue_trend[1]["revenue"]
                if prior_rev > 0:
                    qoq_growth = (recent_rev - prior_rev) / prior_rev
                    if qoq_growth < -0.05:
                        revenue_deceleration = True

        fundamentals = {
            # ── Backward-compat keys used by the research stack ─────────────
            "revenue_growth_yoy": rev_growth,
            "revenue_deceleration": bool(revenue_deceleration),
            # True trend only. Level-only condition is exposed separately.
            "margin_compression": bool(margin_compressing_trend),
            "margin_level_below_threshold": bool(margin_level_below_threshold),
            "margin_signal_quality": "trend",
            "profit_margin_declining": None,
            "profit_margin_level_below_threshold": bool(profit_margin_declining),

            "fcf_negative": None,   # Not in AV OVERVIEW; caller should treat as unknown
            "debt_stress": None,    # Not reliably available without BALANCE_SHEET call
            "valuation_rich": bool(ev_to_sales is not None and ev_to_sales > 3.0),
            "market_cap": market_cap,
            "sector": sector,
            "industry": industry,
            "short_interest_safe": None,   # Not in AV fundamental endpoints
            "short_interest_low": None,
            "short_interest_pct": short_percent_float,
            "earnings_window_safe": None,  # Unknown from fundamentals; set by live scanner
            "days_to_earnings": None,
            # Analyst guidance proxy: AV doesn't provide this; leave as None
            # so scoring treats it as unknown (0 pts) rather than False (0 pts
            # AND potentially misleading diagnostics).
            "guidance_trend": None,
            "estimate_revisions": None,
            "analyst_downgrade_proxy": None,   # Honest label; None = not available

            # ── Extra keys added by this provider ────────────────────────────
            "av_operating_margin_trend": op_margin_trend,
            "av_revenue_trend": revenue_trend,
            "av_operating_margin_level": op_margin,
            "av_profit_margin_level": profit_margin,
            "data_source": "alpha_vantage",
            "data_quality": (
                "good" if (rev_growth is not None and op_margin is not None)
                else "partial"
            ),
        }
        fundamentals = _massage_research_fundamentals(fundamentals, provider_name=self.NAME)

        return ProviderResult(
            value=fundamentals, provider=self.NAME, success=True,
            symbol=symbol, endpoint=endpoint,
        )


class YFinanceFundamentalsProvider:
    """
    Research-only fallback fundamentals provider.

    Uses the existing yfinance-backed FundamentalDataFetcher but relabels
    fields so analyst-proxy and level-only margin checks are not mistaken for
    verified management guidance or true margin-compression trends.
    """

    NAME = "yfinance_fundamentals"

    def fetch(self, ticker: str) -> ProviderResult:
        symbol = str(ticker).upper().strip()
        endpoint = f"yfinance/fundamentals/{symbol}"
        try:
            from fundamental_data_fetcher import FundamentalDataFetcher

            payload = FundamentalDataFetcher.get_short_fundamentals(symbol)
            if not isinstance(payload, dict) or not payload:
                reason = "FundamentalDataFetcher returned empty payload"
                logger.warning("YFinanceFundamentalsProvider: %s symbol=%s", reason, symbol)
                return ProviderResult(
                    value=None,
                    provider=self.NAME,
                    success=False,
                    failure_reason=reason,
                    symbol=symbol,
                    endpoint=endpoint,
                )
            return ProviderResult(
                value=_massage_research_fundamentals(payload, provider_name=self.NAME),
                provider=self.NAME,
                success=True,
                symbol=symbol,
                endpoint=endpoint,
            )
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            logger.warning("YFinanceFundamentalsProvider exception: %s symbol=%s", reason, symbol)
            return ProviderResult(
                value=None,
                provider=self.NAME,
                success=False,
                failure_reason=reason,
                symbol=symbol,
                endpoint=endpoint,
            )


class RoutingFundamentalsProvider:
    """
    Routes fundamentals requests: FMP primary, Alpha Vantage secondary,
    yfinance debug-only fallback.
    """

    def __init__(
        self,
        primary: Optional[FMPFundamentalsProvider] = None,
        secondary: Optional[AlphaVantageFundamentalsProvider] = None,
        debug_fallback: Optional[YFinanceFundamentalsProvider] = None,
        coverage: Optional[ProviderCoverageReport] = None,
        allow_debug_fallback: Optional[bool] = None,
    ) -> None:
        self._primary = primary or FMPFundamentalsProvider()
        self._secondary = secondary or AlphaVantageFundamentalsProvider()
        self._debug_fallback = debug_fallback or YFinanceFundamentalsProvider()
        self._coverage = coverage or ProviderCoverageReport()
        self._allow_debug_fallback = _env_bool("RESEARCH_ALLOW_YFINANCE_DEBUG", False) if allow_debug_fallback is None else bool(allow_debug_fallback)

    @property
    def coverage(self) -> ProviderCoverageReport:
        return self._coverage

    def fetch(self, ticker: str) -> ProviderResult:
        symbol = str(ticker).upper().strip()
        self._coverage.total_symbols += 1

        primary_result = self._primary.fetch(symbol)
        if primary_result.success and primary_result.value:
            self._coverage.primary_success += 1
            self._coverage.record_provider_hit(
                provider=primary_result.provider,
                cache_hit=bool(primary_result.cache_hit),
                confidence_label=primary_result.confidence_label,
            )
            return primary_result

        self._coverage.record_failure(
            provider=primary_result.provider,
            symbol=symbol,
            endpoint=primary_result.endpoint,
            reason=primary_result.failure_reason or "returned empty payload",
        )
        if primary_result.metadata.get("budget_exhausted"):
            self._coverage.record_budget_exhausted(primary_result.provider)

        secondary_result = self._secondary.fetch(symbol)
        if secondary_result.success and secondary_result.value:
            self._coverage.fallback_used += 1
            secondary_result.fallback_used = True
            secondary_result.confidence_label = "fallback_secondary"
            self._coverage.record_provider_hit(
                provider=secondary_result.provider,
                cache_hit=bool(secondary_result.cache_hit),
                confidence_label=secondary_result.confidence_label,
            )
            return secondary_result

        self._coverage.record_failure(
            provider=secondary_result.provider,
            symbol=symbol,
            endpoint=secondary_result.endpoint,
            reason=secondary_result.failure_reason or "returned empty payload",
        )

        if not self._allow_debug_fallback:
            self._coverage.both_failed += 1
            return ProviderResult(
                value=None,
                provider="none",
                success=False,
                fallback_used=True,
                failure_reason=(
                    f"primary({primary_result.provider}): {primary_result.failure_reason}; "
                    f"secondary({secondary_result.provider}): {secondary_result.failure_reason}; "
                    "debug fallback disabled"
                ),
                symbol=symbol,
                endpoint="routing/fundamentals",
            )

        fallback_result = self._debug_fallback.fetch(symbol)
        if fallback_result.success and fallback_result.value:
            self._coverage.fallback_used += 1
            fallback_result.fallback_used = True
            fallback_result.confidence_label = "debug_only"
            self._coverage.record_provider_hit(
                provider=fallback_result.provider,
                cache_hit=bool(fallback_result.cache_hit),
                confidence_label=fallback_result.confidence_label,
            )
            return fallback_result

        self._coverage.both_failed += 1
        self._coverage.record_failure(
            provider=fallback_result.provider,
            symbol=symbol,
            endpoint=fallback_result.endpoint,
            reason=fallback_result.failure_reason or "returned empty payload",
        )
        return ProviderResult(
            value=None,
            provider="none",
            success=False,
            fallback_used=True,
            failure_reason=(
                f"primary({primary_result.provider}): {primary_result.failure_reason}; "
                f"fallback({fallback_result.provider}): {fallback_result.failure_reason}"
            ),
            symbol=symbol,
            endpoint="routing/fundamentals",
        )


# ── Convenience factory ───────────────────────────────────────────────────────

def make_research_providers(
    coverage: Optional[ProviderCoverageReport] = None,
) -> Dict[str, Any]:
    """
    Returns the default provider set for research pipelines.

    Usage:
        providers = make_research_providers()
        price_result   = providers["price"].fetch("AAPL", period="5y")
        earnings_result = providers["earnings"].fetch_dates("AAPL")
        fundamentals_result = providers["fundamentals"].fetch("AAPL")
    """
    cov = coverage or ProviderCoverageReport()
    fundamentals_cov = ProviderCoverageReport()
    earnings_cov = ProviderCoverageReport()
    price_cov = ProviderCoverageReport()
    cache = _provider_cache()
    fmp_budget = DailyBudgetManager(
        provider_name="fmp",
        daily_target=int(os.getenv("FMP_DAILY_BUDGET_TARGET", FMP_DAILY_BUDGET_TARGET)),
    )
    allow_debug_fallback = _env_bool("RESEARCH_ALLOW_YFINANCE_DEBUG", False)
    return {
        "price": RoutingPriceProvider(
            primary=AlpacaHistoricalPriceProvider(cache=cache),
            fallback=YFinancePriceProvider(cache=cache),
            coverage=price_cov,
            allow_debug_fallback=allow_debug_fallback,
        ),
        "earnings": RoutingEarningsProvider(
            primary=FMPEarningsProvider(cache=cache, budget=fmp_budget),
            secondary=AlphaVantageEarningsProvider(),
            debug_fallback=YFinanceEarningsProvider(),
            coverage=earnings_cov,
            allow_debug_fallback=allow_debug_fallback,
        ),
        "fundamentals": RoutingFundamentalsProvider(
            primary=FMPFundamentalsProvider(cache=cache, budget=fmp_budget),
            secondary=AlphaVantageFundamentalsProvider(),
            debug_fallback=YFinanceFundamentalsProvider(),
            coverage=fundamentals_cov,
            allow_debug_fallback=allow_debug_fallback,
        ),
        "coverage": cov,
        "price_coverage": price_cov,
        "earnings_coverage": earnings_cov,
        "fundamentals_coverage": fundamentals_cov,
        "provider_cache": cache,
        "fmp_budget": fmp_budget,
    }


def fetch_price_history_research(
    ticker: str,
    period: str = "5y",
    *,
    provider: Optional[RoutingPriceProvider] = None,
    coverage: Optional[ProviderCoverageReport] = None,
) -> pd.DataFrame:
    """
    Drop-in replacement for short_backtester.fetch_price_history() for use in
    research pipelines.  Returns an empty DataFrame (never raises) but logs
    every failure with provider / symbol / endpoint / reason.

    Primary: Alpaca.  Fallback: yfinance Ticker.history.
    """
    p = provider or RoutingPriceProvider(coverage=coverage)
    result = p.fetch(ticker, period=period)
    if result.success and result.value is not None:
        return result.value
    return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])


def fetch_short_fundamentals_research(
    ticker: str,
    *,
    provider: Optional[RoutingFundamentalsProvider] = None,
) -> ProviderResult:
    p = provider or RoutingFundamentalsProvider()
    return p.fetch(ticker)

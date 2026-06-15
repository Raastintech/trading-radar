"""
research/backtests/backtest_data_loader.py — Cache-first price loader for all backtest scripts.

Architecture
------------
Backtests must NOT call Alpaca (or FMP) in tight per-ticker loops on every run.
This loader sits in front of Alpaca and provides a parquet cache at:

    cache/backtest_prices/{TICKER}.parquet

Separate from the production cache (cache/prices/) to avoid TTL conflicts —
production parquet has a 12-hour TTL and is overwritten with recent data on
each scanner run. Backtest parquet is write-once per date range: it is only
re-fetched if the file doesn't exist or doesn't cover the requested date range.

Usage
-----
Replaces the inline `fetch_bars` / `fetch_price_data` / `fetch_all_price_data`
functions in sniper_backtest.py, short_v1_backtest.py, and voyager_v2_backtest.py.

    loader = BacktestDataLoader()

    # Single ticker (replaces fetch_bars in sniper/short backtests)
    bars: List[Dict] = loader.get_bars("AAPL", date(2020, 1, 1), date(2024, 12, 31))

    # Multi-ticker batch (replaces fetch_all_price_data / fetch_price_data)
    data: Dict[str, pd.DataFrame] = loader.get_bars_batch(
        ["AAPL", "MSFT", "SPY"], date(2020, 1, 1), date(2024, 12, 31)
    )
"""
from __future__ import annotations

import logging
import os
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

# Allow running directly from the backtests directory
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

logger = logging.getLogger(__name__)

# Cache lives next to the production price cache, but in a separate directory.
_DEFAULT_CACHE_DIR = Path(__file__).parent.parent.parent / "cache" / "backtest_prices"


class BacktestDataLoader:
    """
    Cache-first OHLCV loader for backtest scripts.

    On first use for a given ticker + date range, data is fetched from Alpaca
    and written to cache/backtest_prices/{TICKER}.parquet.  Subsequent runs
    read from the parquet — zero network calls unless the cache doesn't cover
    the requested range.
    """

    def __init__(self, cache_dir: Optional[Path] = None):
        self._cache_dir = cache_dir or _DEFAULT_CACHE_DIR
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._client = None  # lazy-initialised on first cache miss
        self._cache_only = os.getenv("BACKTEST_CACHE_ONLY", "").lower() in ("1", "true", "yes")

    # ── Public API ────────────────────────────────────────────────────────────

    def get_bars(self, ticker: str, start: date, end: date) -> List[Dict]:
        """
        Return OHLCV bars for *ticker* covering [start, end], sorted oldest-first.
        Each element is a dict with keys: date (datetime.date), open, high, low,
        close, volume.

        Reads from cache; falls back to Alpaca on miss.
        """
        df = self._load_cached(ticker, start, end)
        if df is None:
            if self._cache_only:
                raise RuntimeError(f"BACKTEST_CACHE_ONLY=true and cache miss found: {ticker.upper()}")
            df = self._fetch_single(ticker, start, end)
        if df is None or df.empty:
            return []
        return self._df_to_bars(df, start, end)

    def get_bars_batch(
        self,
        tickers: List[str],
        start: date,
        end: date,
    ) -> Dict[str, pd.DataFrame]:
        """
        Return OHLCV DataFrames for all *tickers* covering [start, end].
        Result dict: ticker → DataFrame(date_index, open/high/low/close/volume).

        Loads cached tickers first; batches uncached tickers into a single
        Alpaca request.
        """
        result: Dict[str, pd.DataFrame] = {}
        need_fetch: List[str] = []

        for ticker in tickers:
            df = self._load_cached(ticker, start, end)
            if df is not None:
                filtered = self._filter_df(df, start, end)
                if not filtered.empty:
                    result[ticker] = filtered
                else:
                    need_fetch.append(ticker)
            else:
                need_fetch.append(ticker)

        if need_fetch:
            if self._cache_only:
                raise RuntimeError(
                    "BACKTEST_CACHE_ONLY=true and cache misses found: "
                    + ", ".join(sorted(set(need_fetch))[:20])
                )
            fetched = self._fetch_batch(need_fetch, start, end)
            for ticker, df in fetched.items():
                self._save_to_cache(ticker, df)
                filtered = self._filter_df(df, start, end)
                if not filtered.empty:
                    result[ticker] = filtered

        cached_count  = len(tickers) - len(need_fetch)
        fetched_count = len(need_fetch)
        logger.info(
            "BacktestDataLoader: %d from cache, %d from Alpaca (of %d requested)",
            cached_count, fetched_count, len(tickers),
        )
        return result

    # ── Cache I/O ─────────────────────────────────────────────────────────────

    def _cache_path(self, ticker: str) -> Path:
        return self._cache_dir / f"{ticker.upper()}.parquet"

    def _load_cached(self, ticker: str, start: date, end: date) -> Optional[pd.DataFrame]:
        """Return cached DataFrame if it covers [start, end]; else None."""
        path = self._cache_path(ticker)
        if not path.exists():
            return None
        try:
            df = pd.read_parquet(path)
            df.index = pd.to_datetime(df.index)
            # Check that the cache covers the full requested range.
            cache_start = df.index.min().date()
            cache_end   = df.index.max().date()
            if cache_start <= start and cache_end >= end:
                return df
            # IPO/new-listing tickers cannot cover dates before listing.  If
            # the cache reaches the requested end date, the backtest can still
            # use the available history and will naturally skip until enough
            # bars exist for each strategy gate.
            if cache_end >= end and os.getenv("BACKTEST_ALLOW_PARTIAL_START", "true").lower() in ("1", "true", "yes"):
                return df
            # Partial coverage: re-fetch to extend.
            logger.debug(
                "Cache partial for %s: cache=[%s,%s] requested=[%s,%s] — re-fetching",
                ticker, cache_start, cache_end, start, end,
            )
            return None
        except Exception as exc:
            logger.warning("Corrupt cache for %s: %s — re-fetching", ticker, exc)
            return None

    def _save_to_cache(self, ticker: str, df: pd.DataFrame) -> None:
        path = self._cache_path(ticker)
        try:
            # Merge with existing data if present, then deduplicate.
            if path.exists():
                existing = pd.read_parquet(path)
                existing.index = pd.to_datetime(existing.index)
                df = pd.concat([existing, df]).sort_index()
                df = df[~df.index.duplicated(keep="last")]
            df.to_parquet(path, compression="snappy")
        except Exception as exc:
            logger.error("Failed to write backtest cache for %s: %s", ticker, exc)

    # ── Alpaca fetching ───────────────────────────────────────────────────────

    def _get_client(self):
        if self._client is None:
            from alpaca.data.historical import StockHistoricalDataClient
            api_key    = os.environ.get("ALPACA_API_KEY", "")
            secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
            self._client = StockHistoricalDataClient(
                api_key=api_key, secret_key=secret_key,
            )
        return self._client

    def _fetch_single(self, ticker: str, start: date, end: date) -> Optional[pd.DataFrame]:
        """Fetch one ticker from Alpaca and write to cache."""
        fetched = self._fetch_batch([ticker], start, end)
        df = fetched.get(ticker.upper())
        if df is not None:
            self._save_to_cache(ticker, df)
        return df

    def _fetch_batch(
        self, tickers: List[str], start: date, end: date
    ) -> Dict[str, pd.DataFrame]:
        """Fetch a list of tickers from Alpaca in a single request."""
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        from alpaca.data.enums import DataFeed

        client = self._get_client()
        symbols = [t.upper() for t in tickers]
        req = StockBarsRequest(
            symbol_or_symbols=symbols,
            timeframe=TimeFrame.Day,
            start=datetime(start.year, start.month, start.day, tzinfo=timezone.utc),
            end=datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=timezone.utc),
            adjustment="all",
            feed=DataFeed.SIP,
        )
        logger.info(
            "BacktestDataLoader: fetching %d tickers from Alpaca [%s → %s]",
            len(symbols), start, end,
        )
        t0 = time.time()
        try:
            resp = client.get_stock_bars(req)
        except Exception as exc:
            logger.error("Alpaca batch fetch failed: %s", exc)
            return {}
        logger.info("  Done: %.1fs", time.time() - t0)

        result: Dict[str, pd.DataFrame] = {}
        for sym in symbols:
            raw = resp.data.get(sym, [])
            if not raw:
                logger.warning("  No data for %s", sym)
                continue
            rows = [
                {
                    "open":   float(b.open),
                    "high":   float(b.high),
                    "low":    float(b.low),
                    "close":  float(b.close),
                    "volume": int(b.volume),
                }
                for b in raw
            ]
            dates = [b.timestamp.date() for b in raw]
            df = pd.DataFrame(rows, index=pd.to_datetime(dates))
            df.index.name = "date"
            df.sort_index(inplace=True)
            result[sym] = df

        logger.info("  Loaded %d/%d tickers", len(result), len(symbols))
        return result

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _filter_df(df: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
        ts_start = pd.Timestamp(start)
        ts_end   = pd.Timestamp(end)
        return df[(df.index >= ts_start) & (df.index <= ts_end)]

    @staticmethod
    def _df_to_bars(df: pd.DataFrame, start: date, end: date) -> List[Dict]:
        filtered = BacktestDataLoader._filter_df(df, start, end)
        return [
            {
                "date":   row.Index.date(),
                "open":   float(row.open),
                "high":   float(row.high),
                "low":    float(row.low),
                "close":  float(row.close),
                "volume": int(row.volume),
            }
            for row in filtered.itertuples()
        ]

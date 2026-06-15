"""
Daily market breadth context based on the canonical Alpaca breadth universe.

Computes the percentage of the tracked breadth-universe symbols trading above
their 200-day moving average and stores one row per day in the daily_context
table.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import UTC, date, datetime
from typing import Any, Dict, List, Optional

from alpaca_data import AlpacaDataFeed
from breadth_utils import calendar_days_for_trading_bars
from market_breadth_monitor import MarketBreadthMonitor

DEFAULT_MIN_SYMBOLS_WITH_DATA = 60
REQUIRED_BREADTH_BARS = 200
PRICE_HISTORY_CALENDAR_DAYS = calendar_days_for_trading_bars(REQUIRED_BREADTH_BARS + 20)
STOCK_BREADTH_WEIGHT = MarketBreadthMonitor.STOCK_BREADTH_WEIGHT
SECTOR_BREADTH_WEIGHT = MarketBreadthMonitor.SECTOR_BREADTH_WEIGHT
SECTOR_ETFS = set(MarketBreadthMonitor.SECTOR_ETFS)
ALPACA_SYMBOL_ALIASES = {
    "BRK-B": "BRK.B",
    "BRK-A": "BRK.A",
    "BF-B": "BF.B",
    "BF-A": "BF.A",
}


def _normalize_symbol(symbol: Any) -> str:
    value = str(symbol or "").strip().upper()
    if not value:
        return ""
    return ALPACA_SYMBOL_ALIASES.get(value, value)


def has_sufficient_market_breadth_coverage(
    payload: Optional[Dict[str, Any]],
    *,
    min_symbols_with_data: int = DEFAULT_MIN_SYMBOLS_WITH_DATA,
) -> bool:
    if not payload:
        return False
    try:
        breadth_pct = payload.get("market_breadth_pct")
        symbols_with_data = int(payload.get("symbols_with_data") or 0)
    except Exception:
        return False
    if breadth_pct is None:
        return False
    return symbols_with_data >= int(min_symbols_with_data)


class AlpacaBreadthUniverseProvider:
    def __init__(
        self,
        universe: Optional[List[str]] = None,
        data_feed: Optional[AlpacaDataFeed] = None,
    ):
        base_universe = universe or list(getattr(MarketBreadthMonitor, "BREADTH_UNIVERSE", []))
        self.universe = self._dedupe_tickers(base_universe)
        self.universe_source = "alpaca_breadth_universe"
        self.data_feed = data_feed or AlpacaDataFeed()

    @staticmethod
    def _dedupe_tickers(tickers: List[str]) -> List[str]:
        deduped: List[str] = []
        seen = set()
        for raw in tickers or []:
            ticker = _normalize_symbol(raw)
            if not ticker or ticker in {"NAN", "NONE"} or ticker in seen:
                continue
            seen.add(ticker)
            deduped.append(ticker)
        return deduped

    def get_breadth_universe(self) -> List[str]:
        return list(self.universe)

    def get_price_history(self, tickers: List[str]) -> Dict[str, List[float]]:
        clean = self._dedupe_tickers(tickers)
        if not clean:
            return {}

        history: Dict[str, List[float]] = {}
        try:
            bars_by_symbol = self.data_feed.get_daily_bars_batch(
                clean,
                days_back=PRICE_HISTORY_CALENDAR_DAYS,
                adjustment="all",
                chunk_size=100,
            )
        except Exception:
            bars_by_symbol = {}

        missing_symbols: List[str] = []
        for ticker in clean:
            closes = self._extract_closes(bars_by_symbol.get(ticker, []) or [])
            if closes:
                history[ticker] = closes
            else:
                missing_symbols.append(ticker)

        for ticker in missing_symbols:
            try:
                invalidate = getattr(self.data_feed, "invalidate_daily_bars_cache", None)
                if callable(invalidate):
                    invalidate(ticker, days_back=PRICE_HISTORY_CALENDAR_DAYS)
                closes = self._extract_closes(
                    self.data_feed.get_daily_bars(
                        ticker,
                        days_back=PRICE_HISTORY_CALENDAR_DAYS,
                        adjustment="all",
                    ) or []
                )
            except Exception:
                closes = []
            if closes:
                history[ticker] = closes
        return history

    @staticmethod
    def _extract_closes(bars: List[Dict[str, Any]]) -> List[float]:
        closes: List[float] = []
        for bar in bars or []:
            try:
                closes.append(float(bar["close"]))
            except Exception:
                continue
        return closes


class MarketBreadthContextService:
    def __init__(
        self,
        db_path: str = "trading_performance.db",
        provider: Optional[Any] = None,
        min_symbols_with_data: Optional[int] = None,
    ):
        self.db_path = db_path
        self.provider = provider or AlpacaBreadthUniverseProvider()
        env_min_symbols = os.getenv("MARKET_BREADTH_MIN_SYMBOLS")
        if min_symbols_with_data is not None:
            self.min_symbols_with_data = int(min_symbols_with_data)
        elif env_min_symbols not in (None, ""):
            self.min_symbols_with_data = max(1, int(float(env_min_symbols)))
        else:
            self.min_symbols_with_data = DEFAULT_MIN_SYMBOLS_WITH_DATA
        self.ensure_table()

    def ensure_table(self) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_context (
                context_date TEXT PRIMARY KEY,
                market_breadth_pct REAL,
                constituent_count INTEGER,
                symbols_with_data INTEGER,
                above_200ma_count INTEGER,
                source TEXT,
                computed_at TEXT
            )
            """
        )
        existing_cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(daily_context)").fetchall()
        }
        optional_columns = {
            "stock_constituent_count": "INTEGER",
            "stock_symbols_with_data": "INTEGER",
            "stock_above_200ma_count": "INTEGER",
            "stock_breadth_pct": "REAL",
            "sector_constituent_count": "INTEGER",
            "sector_symbols_with_data": "INTEGER",
            "sector_above_200ma_count": "INTEGER",
            "sector_breadth_pct": "REAL",
            "breadth_model": "TEXT",
        }
        for column, col_type in optional_columns.items():
            if column not in existing_cols:
                conn.execute(f"ALTER TABLE daily_context ADD COLUMN {column} {col_type}")
        conn.commit()
        conn.close()

    def get_or_compute(
        self,
        context_date: Optional[str] = None,
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        target_date = context_date or date.today().isoformat()
        existing = self.load(target_date)
        existing_is_valid = False
        if existing and not force_refresh:
            existing_is_valid = has_sufficient_market_breadth_coverage(
                existing,
                min_symbols_with_data=self.min_symbols_with_data,
            )
            if existing_is_valid:
                return existing

        computed = self.compute(target_date)
        if computed:
            self.store(computed)
            return computed

        if existing and not existing_is_valid:
            invalid = dict(existing)
            invalid["market_breadth_pct"] = None
            source = str(invalid.get("source") or "unknown")
            if "insufficient_coverage" not in source:
                invalid["source"] = f"{source}+insufficient_coverage"
            return invalid

        return existing or {
            "context_date": target_date,
            "market_breadth_pct": None,
            "constituent_count": 0,
            "symbols_with_data": 0,
            "above_200ma_count": 0,
            "source": "unavailable",
            "computed_at": datetime.now(UTC).isoformat(),
        }

    def load(self, context_date: str) -> Optional[Dict[str, Any]]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM daily_context WHERE context_date = ?",
            (context_date,),
        )
        row = cur.fetchone()
        conn.close()
        return dict(row) if row else None

    def store(self, payload: Dict[str, Any]) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """
            INSERT INTO daily_context
                (context_date, market_breadth_pct, constituent_count, symbols_with_data,
                 above_200ma_count, source, computed_at,
                 stock_constituent_count, stock_symbols_with_data, stock_above_200ma_count, stock_breadth_pct,
                 sector_constituent_count, sector_symbols_with_data, sector_above_200ma_count, sector_breadth_pct,
                 breadth_model)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(context_date) DO UPDATE SET
                market_breadth_pct=excluded.market_breadth_pct,
                constituent_count=excluded.constituent_count,
                symbols_with_data=excluded.symbols_with_data,
                above_200ma_count=excluded.above_200ma_count,
                source=excluded.source,
                computed_at=excluded.computed_at,
                stock_constituent_count=excluded.stock_constituent_count,
                stock_symbols_with_data=excluded.stock_symbols_with_data,
                stock_above_200ma_count=excluded.stock_above_200ma_count,
                stock_breadth_pct=excluded.stock_breadth_pct,
                sector_constituent_count=excluded.sector_constituent_count,
                sector_symbols_with_data=excluded.sector_symbols_with_data,
                sector_above_200ma_count=excluded.sector_above_200ma_count,
                sector_breadth_pct=excluded.sector_breadth_pct,
                breadth_model=excluded.breadth_model
            """,
            (
                payload.get("context_date"),
                payload.get("market_breadth_pct"),
                payload.get("constituent_count"),
                payload.get("symbols_with_data"),
                payload.get("above_200ma_count"),
                payload.get("source"),
                payload.get("computed_at"),
                payload.get("stock_constituent_count"),
                payload.get("stock_symbols_with_data"),
                payload.get("stock_above_200ma_count"),
                payload.get("stock_breadth_pct"),
                payload.get("sector_constituent_count"),
                payload.get("sector_symbols_with_data"),
                payload.get("sector_above_200ma_count"),
                payload.get("sector_breadth_pct"),
                payload.get("breadth_model"),
            ),
        )
        conn.commit()
        conn.close()

    def _provider_universe(self) -> List[str]:
        if hasattr(self.provider, "get_breadth_universe"):
            return list(self.provider.get_breadth_universe() or [])
        if hasattr(self.provider, "get_spy_constituents"):
            return list(self.provider.get_spy_constituents() or [])
        return []

    def compute(self, context_date: str) -> Optional[Dict[str, Any]]:
        tickers = [_normalize_symbol(ticker) for ticker in self._provider_universe()]
        tickers = [ticker for ticker in tickers if ticker]
        if not tickers:
            return None

        history = self.provider.get_price_history(tickers)
        above_200ma = 0
        symbols_with_data = 0
        stock_tickers = [ticker for ticker in tickers if ticker not in SECTOR_ETFS]
        sector_tickers = [ticker for ticker in tickers if ticker in SECTOR_ETFS]
        stock_symbols_with_data = 0
        stock_above_200ma = 0
        sector_symbols_with_data = 0
        sector_above_200ma = 0
        universe_source = getattr(
            self.provider,
            "universe_source",
            getattr(self.provider, "constituent_source", "custom_provider"),
        )

        for ticker in tickers:
            closes = history.get(_normalize_symbol(ticker), [])
            if len(closes) < REQUIRED_BREADTH_BARS:
                continue
            ma_200 = sum(closes[-REQUIRED_BREADTH_BARS:]) / float(REQUIRED_BREADTH_BARS)
            current = closes[-1]
            symbols_with_data += 1
            if current > ma_200:
                above_200ma += 1
            if ticker in SECTOR_ETFS:
                sector_symbols_with_data += 1
                if current > ma_200:
                    sector_above_200ma += 1
            else:
                stock_symbols_with_data += 1
                if current > ma_200:
                    stock_above_200ma += 1

        stock_breadth_pct = (
            round((stock_above_200ma / stock_symbols_with_data) * 100.0, 2)
            if stock_symbols_with_data > 0 else None
        )
        sector_breadth_pct = (
            round((sector_above_200ma / sector_symbols_with_data) * 100.0, 2)
            if sector_symbols_with_data > 0 else None
        )

        breadth_pct = None
        if symbols_with_data >= self.min_symbols_with_data:
            breadth_pct = round(
                self._composite_breadth_pct(stock_breadth_pct, sector_breadth_pct),
                2,
            )
            source = f"{universe_source}+alpaca_prices"
        else:
            source = f"{universe_source}+insufficient_coverage"

        return {
            "context_date": context_date,
            "market_breadth_pct": breadth_pct,
            "constituent_count": len(tickers),
            "symbols_with_data": symbols_with_data,
            "above_200ma_count": above_200ma,
            "stock_constituent_count": len(stock_tickers),
            "stock_symbols_with_data": stock_symbols_with_data,
            "stock_above_200ma_count": stock_above_200ma,
            "stock_breadth_pct": stock_breadth_pct,
            "sector_constituent_count": len(sector_tickers),
            "sector_symbols_with_data": sector_symbols_with_data,
            "sector_above_200ma_count": sector_above_200ma,
            "sector_breadth_pct": sector_breadth_pct,
            "breadth_model": "stock_sector_composite_v1",
            "source": source,
            "computed_at": datetime.now(UTC).isoformat(),
        }

    @staticmethod
    def _composite_breadth_pct(stock_pct: Optional[float], sector_pct: Optional[float]) -> float:
        stock_ok = stock_pct is not None
        sector_ok = sector_pct is not None
        if stock_ok and sector_ok:
            return float(stock_pct) * STOCK_BREADTH_WEIGHT + float(sector_pct) * SECTOR_BREADTH_WEIGHT
        if stock_ok:
            return float(stock_pct)
        if sector_ok:
            return float(sector_pct)
        return 0.0

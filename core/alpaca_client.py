"""
core/alpaca_client.py — EXECUTION PERMANENTLY DISABLED (Phase 3A, 2026-06-13)

Archived for reproducibility only. Original preserved at:
  archive/execution_disabled/alpaca_client.py

In RESEARCH_ONLY mode:
  - Alpaca keys are optional (system starts without them).
  - All execution methods (submit_*_order, close_position, cancel_all_orders)
    raise ResearchOnlyModeError immediately.
  - Data/read methods serve from the local price cache (cache/prices/*.parquet)
    using the same list-of-dicts format the real client returned.
    Symbols not in the cache return [] / None / {} as appropriate.
  - get_alpaca() returns an AlpacaClient stub; no network connection is made.

Re-enabling Alpaca execution requires deliberate restoration from the archive.
"""
from __future__ import annotations
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from core.research_mode import ResearchOnlyModeError
import core.config as cfg

logger = logging.getLogger(__name__)

_EXEC_MSG = (
    "RESEARCH_ONLY_MODE: Alpaca execution is permanently disabled. "
    "See archive/execution_disabled/alpaca_client.py to restore."
)

_PRICE_CACHE = Path(__file__).resolve().parents[1] / "cache" / "prices"


def _read_price_cache(ticker: str, days: int) -> List[Dict]:
    """Read OHLCV bars from cache/prices/{TICKER}.parquet.

    Returns a list of dicts (oldest-first) in the same format the real
    AlpacaClient returned: {"date": "YYYY-MM-DD", "open": float, ...}.
    Returns [] when the parquet is absent or unreadable.
    """
    try:
        path = _PRICE_CACHE / f"{ticker.upper()}.parquet"
        if not path.exists():
            return []
        df = pd.read_parquet(path)
        if df.empty:
            return []
        df = df.tail(days).reset_index()
        df["date"] = df["date"].dt.strftime("%Y-%m-%d")
        cols = [c for c in ("date", "open", "high", "low", "close", "volume") if c in df.columns]
        return df[cols].to_dict("records")
    except Exception as exc:
        logger.debug("Price cache read failed for %s: %s", ticker, exc)
        return []


class AlpacaClient:
    """
    Stub AlpacaClient for RESEARCH_ONLY mode.

    All execution paths raise ResearchOnlyModeError.
    Data-read paths serve from the local price cache (cache/prices/*.parquet)
    in the same format the real client returned. No network connection is made.
    """

    def __init__(self) -> None:
        if cfg.ALPACA_API_KEY:
            logger.info(
                "AlpacaClient stub: Alpaca keys present but broker execution is "
                "disabled. Serving price data from local cache."
            )
        else:
            logger.info(
                "AlpacaClient stub: No Alpaca keys configured. "
                "Serving price data from local cache."
            )

    # ── Data / read-only stubs (serve from local cache) ──────────────────────

    def get_quote(self, ticker: str) -> Optional[Dict]:
        rows = _read_price_cache(ticker, 1)
        if rows:
            close = rows[-1].get("close")
            if close:
                c = float(close)
                return {"mid": c, "bid": c, "ask": c, "last": c}
        return None

    def get_daily_bars(
        self,
        ticker: str,
        days: int = 60,
        **_: Any,
    ) -> List[Dict]:
        """Return OHLCV bars from local cache, oldest-first."""
        return _read_price_cache(ticker, days)

    def get_daily_bars_batch(
        self,
        tickers: List[str],
        days: int = 60,
        **_: Any,
    ) -> Dict[str, List[Dict]]:
        """Return OHLCV bars from local cache for multiple symbols."""
        result: Dict[str, List[Dict]] = {}
        for t in tickers:
            rows = _read_price_cache(t, days)
            if rows:
                result[t] = rows
        return result

    def get_intraday_bars(
        self,
        ticker: str,
        minutes: int = 5,
        trading_days: int = 5,
    ) -> List[Dict]:
        # No intraday cache exists; return empty list (callers handle this).
        return []

    def get_positions(self) -> List[Dict]:
        return []

    def get_positions_with_status(self) -> Tuple[List[Dict], bool]:
        return [], False

    def get_account(self) -> Dict:
        return {"equity": 0, "buying_power": 0, "cash": 0}

    def get_order(self, order_id: str) -> Optional[Dict]:
        return None

    def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict]:
        return []

    def wait_for_fill(self, order_id: str, timeout_s: float = 5.0, poll_interval_s: float = 0.5) -> Optional[Dict]:
        return None

    # ── Execution methods — permanently disabled ──────────────────────────────

    def submit_market_order(self, ticker: str, qty: float, side: str) -> Optional[Dict]:
        raise ResearchOnlyModeError(_EXEC_MSG)

    def submit_limit_order(self, ticker: str, qty: float, side: str, limit_price: float) -> Optional[Dict]:
        raise ResearchOnlyModeError(_EXEC_MSG)

    def cancel_all_orders(self) -> None:
        raise ResearchOnlyModeError(_EXEC_MSG)

    def close_position(self, ticker: str) -> Optional[Dict]:
        raise ResearchOnlyModeError(_EXEC_MSG)


_client: Optional[AlpacaClient] = None


def get_alpaca() -> AlpacaClient:
    """Return the singleton AlpacaClient stub. No connection is made."""
    global _client
    if _client is None:
        _client = AlpacaClient()
    return _client

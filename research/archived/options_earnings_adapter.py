from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional, Set

logger = logging.getLogger(__name__)

try:
    import yfinance as yf
except Exception:  # pragma: no cover - import guard
    yf = None  # type: ignore


@dataclass
class EarningsContext:
    ticker: str
    next_earnings_date: Optional[date]
    known: bool


class OptionsEarningsAdapter:
    """Best-effort ticker earnings lookup for options blackout handling."""

    NON_EARNINGS_UNDERLYINGS = {
        "SPY", "QQQ", "IWM", "DIA",
        "XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY",
        "XBI", "XME", "XRT",
        "GLD", "SLV", "TLT", "IEF", "HYG", "LQD", "USO", "UNG",
        "VXX", "UVXY", "SVXY",
        "IBIT", "ETHA",
        "TQQQ", "SQQQ", "SPXL", "SPXS", "UPRO", "SOXL", "SOXS",
    }

    def __init__(self, provider=None):
        self.provider = provider

    @classmethod
    def _non_earnings_underlyings(cls) -> Set[str]:
        names = set(cls.NON_EARNINGS_UNDERLYINGS)
        raw = os.getenv("OPTIONS_NON_EARNINGS_UNDERLYINGS", "")
        for token in str(raw).split(","):
            ticker = token.strip().upper()
            if ticker:
                names.add(ticker)
        return names

    @classmethod
    def _skip_earnings_lookup(cls, ticker: str) -> bool:
        ticker = str(ticker or "").upper().strip()
        if not ticker:
            return False
        if ticker.startswith("^"):
            return True
        return ticker in cls._non_earnings_underlyings()

    def get_next_earnings_date(self, ticker: str) -> Optional[date]:
        ticker = str(ticker or "").upper().strip()
        if not ticker:
            return None
        if self._skip_earnings_lookup(ticker):
            return None

        if self.provider is not None:
            try:
                value = self.provider.get_next_earnings_date(ticker)
                if isinstance(value, datetime):
                    return value.date()
                if isinstance(value, date):
                    return value
            except Exception as exc:
                logger.debug("[OPTIONS] custom earnings provider failed for %s: %s", ticker, exc)

        if yf is None:
            return None

        try:
            tk = yf.Ticker(ticker)
            cal = getattr(tk, "calendar", None)
            if cal is not None:
                try:
                    if hasattr(cal, "index") and hasattr(cal, "loc"):
                        for key in ("Earnings Date", "Earnings Date(s)"):
                            if key in cal.index:
                                value = cal.loc[key]
                                if hasattr(value, "iloc"):
                                    value = value.iloc[0]
                                if isinstance(value, datetime):
                                    return value.date()
                                if isinstance(value, date):
                                    return value
                    elif isinstance(cal, dict):
                        for key in ("Earnings Date", "earningsDate"):
                            value = cal.get(key)
                            if isinstance(value, list):
                                value = value[0] if value else None
                            if isinstance(value, datetime):
                                return value.date()
                            if isinstance(value, date):
                                return value
                except Exception:
                    pass

            try:
                frame = tk.get_earnings_dates(limit=1)
                if frame is not None and len(frame.index) > 0:
                    idx = frame.index[0]
                    if hasattr(idx, "date"):
                        return idx.date()
            except Exception:
                pass
        except Exception as exc:
            logger.debug("[OPTIONS] earnings lookup failed for %s: %s", ticker, exc)
            return None

        return None

    def get_context(self, ticker: str) -> EarningsContext:
        next_date = self.get_next_earnings_date(ticker)
        return EarningsContext(
            ticker=str(ticker or "").upper().strip(),
            next_earnings_date=next_date,
            known=next_date is not None,
        )

    def days_to_earnings(self, ticker: str, as_of: Optional[date] = None) -> Optional[int]:
        next_date = self.get_next_earnings_date(ticker)
        if next_date is None:
            return None
        base = as_of or date.today()
        return (next_date - base).days

    def is_blackout(self, ticker: str, blackout_days: int = 7, as_of: Optional[date] = None) -> bool:
        days = self.days_to_earnings(ticker, as_of=as_of)
        return days is not None and days <= int(blackout_days)

    def should_block_new_trade(self, ticker: str, blackout_days: int = 7, strict_when_unknown: bool = False) -> bool:
        next_date = self.get_next_earnings_date(ticker)
        if next_date is None:
            return bool(strict_when_unknown)
        return self.is_blackout(ticker, blackout_days=blackout_days)

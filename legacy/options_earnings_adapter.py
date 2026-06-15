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

        # FMP earnings calendar (replaces yfinance)
        try:
            from core.fmp_client import get_fmp
            cal = get_fmp().get_earnings_calendar(days_ahead=60)
            match = next(
                (e for e in cal if e.get("symbol", "").upper() == ticker),
                None,
            )
            if match and match.get("date"):
                return datetime.strptime(match["date"], "%Y-%m-%d").date()
        except Exception as exc:
            logger.debug("[OPTIONS] FMP earnings lookup failed for %s: %s", ticker, exc)

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

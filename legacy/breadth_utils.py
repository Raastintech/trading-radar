"""
Helpers for market-breadth lookback sizing.

Breadth calculations are expressed in trading bars, but Alpaca historical
requests use calendar dates. Converting too literally under-fetches history
around weekends and holidays, which can silently zero-out breadth metrics.
"""

from __future__ import annotations

import math


def calendar_days_for_trading_bars(trading_bars: int, *, buffer_days: int = 30) -> int:
    """
    Convert a required number of trading bars into a safe calendar-day window.

    Uses a 5-trading-days-per-7-calendar-days approximation plus an explicit
    buffer for market holidays and partial weeks.
    """

    bars = max(1, int(trading_bars or 1))
    buffer = max(0, int(buffer_days or 0))
    implied_calendar_days = math.ceil(bars * 7 / 5)
    return max(bars + buffer, implied_calendar_days + buffer)

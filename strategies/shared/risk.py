"""
strategies/shared/risk.py — Shared risk and sizing utilities.

Used by all production strategy scanners.
Do not import anything outside of core/ or the standard library.
"""
from __future__ import annotations
import statistics
from typing import Dict, List


def calc_atr(bars: List[Dict], period: int = 14) -> float:
    """
    Average True Range over the last `period` bars.

    Args:
        bars:   List of OHLCV dicts with keys 'high', 'low', 'close'.
                Must be sorted oldest-first.
        period: Number of bars to average. Defaults to 14.

    Returns:
        ATR as a float, or 0.0 if insufficient data.
    """
    window = bars[-period:] if len(bars) >= period else bars
    trs: List[float] = []
    for i in range(1, len(window)):
        h  = window[i]["high"]
        l  = window[i]["low"]
        pc = window[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return statistics.mean(trs) if trs else 0.0


def size_shares(
    equity: float,
    entry: float,
    stop: float,
    multiplier: float = 1.0,
) -> int:
    """
    Position size in shares based on fixed fractional risk.

    Risk per trade = equity × MAX_POSITION_PCT × multiplier.
    Shares = risk / |entry - stop|.

    Args:
        equity:     Current account equity in dollars.
        entry:      Planned entry price.
        stop:       Stop-loss price (above or below entry for shorts/longs).
        multiplier: Scale factor on normal position size. Default 1.0.
                    Pass 0.5 for half-size (e.g. Contrarian fear-regime entries).

    Returns:
        Number of shares (minimum 1), or 0 if risk geometry is invalid.
    """
    from core.config import MAX_POSITION_PCT
    risk_per_share = abs(entry - stop)
    if risk_per_share <= 0:
        return 0
    dollar_risk = equity * MAX_POSITION_PCT * multiplier
    return max(1, int(dollar_risk / risk_per_share))

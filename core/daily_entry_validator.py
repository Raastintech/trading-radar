"""
Daily entry validator for Alpha Discovery.

Research-only module that sits on top of Alpha Discovery idea generation.
It does not route trades, score sleeves, or write paper evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence


@dataclass(frozen=True)
class DailyEntryValidation:
    state: str
    actionable_now: bool
    reason: str
    flags: List[str]
    metrics: Dict[str, float]


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _ema(values: Sequence[float], period: int) -> float:
    if not values:
        return 0.0
    if len(values) < period:
        return sum(values) / max(1, len(values))
    k = 2.0 / (period + 1.0)
    ema = sum(values[:period]) / period
    for value in values[period:]:
        ema = (value * k) + (ema * (1.0 - k))
    return ema


def _atr_pct(bars: Sequence[Dict[str, Any]], period: int = 14) -> float:
    if len(bars) < 2:
        return 0.0
    window = list(bars[-period:]) if len(bars) >= period else list(bars)
    prev_close = _f(window[0].get("close"))
    trs: List[float] = []
    for bar in window[1:]:
        high = _f(bar.get("high"))
        low = _f(bar.get("low"))
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
        prev_close = _f(bar.get("close"))
    close = _f(window[-1].get("close"))
    if not trs or close <= 0:
        return 0.0
    return (sum(trs) / len(trs)) / close * 100.0


def _up_days(closes: Sequence[float], lookback: int = 5) -> int:
    if len(closes) < 2:
        return 0
    recent = list(closes[-(lookback + 1):])
    return sum(1 for i in range(1, len(recent)) if recent[i] > recent[i - 1])


def _clip_flag(flags: List[str], text: str) -> None:
    if text not in flags and len(flags) < 3:
        flags.append(text)


def validate_daily_entry(bars: Sequence[Dict[str, Any]]) -> DailyEntryValidation:
    if len(bars) < 80:
        return DailyEntryValidation(
            state="Watch Only",
            actionable_now=False,
            reason="insufficient daily bars for structure validation",
            flags=["insufficient bars"],
            metrics={},
        )

    closes = [_f(bar.get("close")) for bar in bars]
    highs = [_f(bar.get("high")) for bar in bars]
    lows = [_f(bar.get("low")) for bar in bars]
    opens = [_f(bar.get("open")) for bar in bars]

    close = closes[-1]
    prev_close = closes[-2]
    ema20 = _ema(closes, 20)
    ema50 = _ema(closes, 50)
    ma200 = sum(closes[-200:]) / 200.0 if len(closes) >= 200 else 0.0
    atr_pct = _atr_pct(bars, 14)

    recent_peak_window = highs[-21:-1] if len(highs) >= 21 else highs[:-1]
    recent_peak = max(recent_peak_window) if recent_peak_window else close
    peak_idx = len(recent_peak_window) - 1 - list(reversed(recent_peak_window)).index(recent_peak) if recent_peak_window else 0
    bars_since_peak = max(0, len(recent_peak_window) - 1 - peak_idx)
    pullback_depth_pct = ((recent_peak - close) / recent_peak * 100.0) if recent_peak > 0 else 0.0

    recent_low5 = min(lows[-5:])
    prior_low5 = min(lows[-10:-5]) if len(lows) >= 10 else recent_low5
    higher_low_intact = recent_low5 >= (prior_low5 * 0.99)

    close_location_5 = 0.5
    range_high5 = max(highs[-5:])
    range_low5 = min(lows[-5:])
    if range_high5 > range_low5:
        close_location_5 = (close - range_low5) / (range_high5 - range_low5)

    dist_ema20_pct = ((close / ema20) - 1.0) * 100.0 if ema20 > 0 else 0.0
    dist_ema50_pct = ((close / ema50) - 1.0) * 100.0 if ema50 > 0 else 0.0
    dist_ma200_pct = ((close / ma200) - 1.0) * 100.0 if ma200 > 0 else 0.0

    trend_intact = close >= ema50 * 0.99 and ema20 >= ema50 * 0.985 and (ma200 <= 0 or close >= ma200 * 0.97)
    reclaim_support = close >= ema20 and prev_close <= ema20 * 1.01
    gap_pct = ((opens[-1] / prev_close) - 1.0) * 100.0 if prev_close > 0 else 0.0
    upside_to_peak_pct = ((recent_peak - close) / close * 100.0) if close > 0 else 0.0
    stop_anchor = min(recent_low5, ema20, ema50)
    risk_pct = ((close - stop_anchor) / close * 100.0) if close > stop_anchor > 0 else 0.0
    rr = upside_to_peak_pct / risk_pct if risk_pct > 0 else 0.0

    flags: List[str] = []
    if dist_ema20_pct > 0:
        _clip_flag(flags, f"{dist_ema20_pct:.1f}% vs EMA20")
    if bars_since_peak < 3:
        _clip_flag(flags, "pullback immature")
    if reclaim_support:
        _clip_flag(flags, "reclaim support")
    if higher_low_intact:
        _clip_flag(flags, "higher low intact")
    if rr and rr < 1.5:
        _clip_flag(flags, "reward/risk poor")

    too_extended = (
        dist_ema20_pct >= 6.0
        or (pullback_depth_pct < 1.5 and bars_since_peak < 3)
        or (_up_days(closes, 5) >= 5 and close_location_5 >= 0.85)
        or gap_pct >= 4.0
    )
    broken = (
        (
            close < ema50 * 0.94
            and ema20 <= ema50 * 0.985
            and not higher_low_intact
            and close_location_5 < 0.35
        )
        or (
            ma200 > 0
            and close < ma200 * 0.90
            and not higher_low_intact
        )
        or (
            pullback_depth_pct > 16.0
            and close < recent_low5 * 0.985
            and close_location_5 < 0.35
        )
    )

    metrics = {
        "ema20": round(ema20, 2),
        "ema50": round(ema50, 2),
        "ma200": round(ma200, 2) if ma200 > 0 else 0.0,
        "dist_ema20_pct": round(dist_ema20_pct, 2),
        "dist_ema50_pct": round(dist_ema50_pct, 2),
        "dist_ma200_pct": round(dist_ma200_pct, 2) if ma200 > 0 else 0.0,
        "pullback_depth_pct": round(pullback_depth_pct, 2),
        "bars_since_peak": float(bars_since_peak),
        "close_location_5": round(close_location_5, 2),
        "atr_pct_14": round(atr_pct, 2),
        "reward_risk": round(rr, 2),
    }

    if broken:
        return DailyEntryValidation(
            state="Broken / Avoid",
            actionable_now=False,
            reason="daily structure is damaged enough that this is not a valid fresh entry",
            flags=(flags + ["trend broken"])[:3],
            metrics=metrics,
        )

    if too_extended:
        return DailyEntryValidation(
            state="Too Extended",
            actionable_now=False,
            reason="strong name, but the daily entry is too stretched for a fresh buy",
            flags=(flags + ["late chase risk"])[:3],
            metrics=metrics,
        )

    if (
        trend_intact
        and 1.5 <= pullback_depth_pct <= 8.0
        and bars_since_peak >= 2
        and higher_low_intact
        and close >= ema20 * 0.995
        and close <= ema20 * 1.02
        and close_location_5 >= 0.45
        and rr >= 1.2
        and gap_pct < 2.0
    ):
        return DailyEntryValidation(
            state="Buyable Now",
            actionable_now=True,
            reason="trend is intact and the pullback has reset enough to offer a sane daily entry",
            flags=(flags + ["buy zone intact"])[:3],
            metrics=metrics,
        )

    if trend_intact and (close < ema20 or close_location_5 < 0.45 or not reclaim_support or dist_ema20_pct > 3.0):
        return DailyEntryValidation(
            state="Watch Reclaim",
            actionable_now=False,
            reason="setup could become buyable, but it still needs reclaim / support confirmation",
            flags=(flags + ["reclaim pending"])[:3],
            metrics=metrics,
        )

    if trend_intact and (bars_since_peak < 5 or pullback_depth_pct < 2.0):
        return DailyEntryValidation(
            state="Pullback Forming",
            actionable_now=False,
            reason="interesting name, but the daily reset is not mature enough yet",
            flags=(flags + ["too early in reset"])[:3],
            metrics=metrics,
        )

    return DailyEntryValidation(
        state="Watch Only",
        actionable_now=False,
        reason="interesting structure, but current location is not a clean daily entry",
        flags=flags[:3],
        metrics=metrics,
    )

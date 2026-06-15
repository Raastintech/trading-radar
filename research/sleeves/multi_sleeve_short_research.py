#!/usr/bin/env python3
"""
multi_sleeve_short_research.py  (RESEARCH ONLY — does not touch live trading logic)

Three independent short research sleeves + a portfolio combiner.

Sleeve A: FailedUpsideReactionShort
    Pure price-structure signal.  Gap up >= 3% into resistance → close in the
    bottom 35% of the day's range → next 1-2 sessions fail to reclaim the gap
    high → downside volume confirmation.  SPY relative weakness required.
    Strict liquidity and anti-squeeze filters.

Sleeve B: VerifiedBadNewsEventShort
    Requires a FMP-verified earnings event row with high/medium confidence and
    a known first tradable session.  Bad surprise or negative post-earnings
    continuation on the first 1-3 sessions.  No heuristic event labels allowed.

Sleeve C: QualityDeteriorationShort
    FMP fundamentals primary, Alpha Vantage secondary.  Scores margin-trend
    deterioration, revenue deceleration, FCF/debt strain, accruals proxy.
    Price must confirm (below 20MA AND 60MA in a downtrend) before entry.

Portfolio combiner:
    Each sleeve emits a normalized 0-100 score.  The combiner weights them,
    ranks candidates, enforces sector cap, applies borrow penalty, and respects
    daily/weekly loss circuit breakers.

Usage:
    python multi_sleeve_short_research.py --universe 200 --mode all
    python multi_sleeve_short_research.py --universe 300 --mode sleeve_a
    python multi_sleeve_short_research.py --universe 500 --mode combined
    python multi_sleeve_short_research.py --universe 200 --mode all --period 5y
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

# Sleeve-specific stop / target geometry (short = stop ABOVE entry)
SLEEVE_A_STOP_PCT   = 0.08   # 8% above entry
SLEEVE_A_TARGET_PCT = 0.20   # 20% below entry (2.5 R/R)
SLEEVE_B_STOP_PCT   = 0.06   # tighter: event-driven moves often mean-revert fast
SLEEVE_B_TARGET_PCT = 0.15   # 2.5 R/R
SLEEVE_C_STOP_PCT   = 0.10   # wider: fundamental thesis takes time to play out
SLEEVE_C_TARGET_PCT = 0.25   # 2.5 R/R

# Backtest mechanics
DEFAULT_MAX_HOLD_DAYS     = 30
POSITION_SIZE_PCT         = 0.10    # 10 % of book per trade
COMMISSION_PCT            = 0.0005  # 0.05 % each way
SLIPPAGE_PCT              = 0.0005  # 0.05 % each way
DEFAULT_BORROW_RATE_ANN   = 1.0     # 1 % annualised (easy-to-borrow baseline)
DEFAULT_REBALANCE_EVERY   = 5       # bars between signal scans
MIN_BARS_REQUIRED         = 60

# Sleeve signal thresholds
SLEEVE_A_GAP_MIN_PCT      = 3.0     # minimum gap-up % to qualify
SLEEVE_A_RANGE_POSITION   = 0.35    # close must be in bottom 35 % of day range
SLEEVE_A_VOL_SPIKE        = 1.4     # volume on gap day >= 1.4× 20d avg
SLEEVE_A_RECLAIM_WINDOW   = 2       # check failed-reclaim over next N days
SLEEVE_A_REL_WEAK_MIN_PCT = -2.0    # stock 5d return < SPY 5d return by 2 %+
SLEEVE_A_ATR_MAX_PCT      = 8.0     # reject if ATR % > 8 (too volatile)
SLEEVE_A_MIN_DOLLAR_VOL   = 10_000_000  # $10 M/day minimum

SLEEVE_B_REACTION_MIN_PCT = -3.0    # minimum negative reaction on event session
SLEEVE_B_VOL_SPIKE        = 1.5     # volume on event day >= 1.5× 20d avg
SLEEVE_B_MAX_LAG_SESSIONS = 3       # entry must be within 3 sessions of event
SLEEVE_B_CONTINUATION_REQ = True    # require day-2 continuation to confirm
SLEEVE_B_MIN_CONFIDENCE   = {"high", "medium"}  # event confidence_flag

SLEEVE_C_MIN_FUNDA_SCORE  = 3       # of 6 fundamental criteria must pass
SLEEVE_C_PRICE_CONFIRMS   = 3       # of 3 price confirmation criteria must pass

# Portfolio combiner
SLEEVE_WEIGHTS = {"A": 1.0, "B": 1.2, "C": 0.8}
MAX_COMBINED_POSITIONS    = 8
SECTOR_CAP                = 2       # max positions per sector in combined book
BORROW_HIGH_SI_THRESHOLD  = 0.20    # short interest % of float  > 20 % → penalty
BORROW_PENALTY_FACTOR     = 0.70    # multiply score by this when SI is high
BORROW_EXTREME_THRESHOLD  = 0.30    # > 30 % SI → stronger penalty
BORROW_EXTREME_FACTOR     = 0.50
DAILY_LOSS_CAP_PCT        = 0.03    # -3 % book loss today → halt new entries
WEEKLY_LOSS_CAP_PCT       = 0.07    # -7 % book loss this week → halt new entries

# Walk-forward stability
WF_WINDOW_BARS            = 126     # ~6 trading months per window
WF_STEP_BARS              = 63      # slide by ~3 months
WF_MIN_TRADES             = 4       # minimum trades per window to count
WF_STABLE_FRACTION        = 0.50    # fraction of windows with +expectancy to call STABLE

# Minimum trade counts for a sleeve to "matter"
MIN_TRADES_A = 20
MIN_TRADES_B = 10
MIN_TRADES_C = 20
MIN_TRADES_COMBINED = 30


# ── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class OpenPosition:
    ticker:       str
    sleeve:       str           # "A", "B", "C"
    entry_date:   pd.Timestamp
    entry_price:  float
    stop_price:   float
    target_price: float
    score:        float
    sector:       str = "UNKNOWN"
    hold_days:    int = 0

    def friction_pct(
        self,
        hold_days: int,
        *,
        borrow_rate_ann: float = DEFAULT_BORROW_RATE_ANN,
    ) -> float:
        borrow = borrow_rate_ann / 100.0 * hold_days / 252.0
        return (COMMISSION_PCT + SLIPPAGE_PCT) * 2.0 + borrow


@dataclass
class ClosedTrade:
    ticker:         str
    sleeve:         str
    entry_date:     pd.Timestamp
    entry_price:    float
    exit_date:      pd.Timestamp
    exit_price:     float
    exit_reason:    str
    stop_price:     float
    target_price:   float
    hold_days:      int
    gross_return_pct: float
    net_return_pct:   float
    score:          float
    sector:         str = "UNKNOWN"


@dataclass
class SleeveBacktestResult:
    sleeve:          str
    trades:          List[ClosedTrade] = field(default_factory=list)
    universe_size:   int = 0
    period:          str = "5y"
    # computed on demand
    _stats: Optional[Dict[str, Any]] = field(default=None, repr=False)

    def stats(self) -> Dict[str, Any]:
        if self._stats is not None:
            return self._stats
        self._stats = _compute_backtest_stats(self.trades, self.sleeve)
        return self._stats


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Import from short_backtester to avoid duplication."""
    from short_backtester import _normalize_price_frame
    return _normalize_price_frame(frame)


def _price_history(ticker: str, period: str = "5y") -> pd.DataFrame:
    from short_backtester import fetch_price_history
    return fetch_price_history(ticker, period)


def _spy_history(period: str = "5y") -> pd.DataFrame:
    from short_backtester import fetch_spy_history
    return fetch_spy_history(period)


def _rebalance_dates(reference_history: pd.DataFrame, *, lookback: int) -> List[pd.Timestamp]:
    from short_backtester import _compute_rebalance_dates
    return _compute_rebalance_dates(
        reference_history, lookback=lookback, step_bars=DEFAULT_REBALANCE_EVERY
    )


def _atr_pct(frame: pd.DataFrame, period: int = 14) -> Optional[float]:
    from short_backtester import _calc_atr_pct
    return _calc_atr_pct(frame, period=period)


def _simulate_exit(
    future_prices: pd.DataFrame,
    entry_date: pd.Timestamp,
    entry_price: float,
    stop_price: float,
    target_price: float,
    *,
    max_hold_days: int = DEFAULT_MAX_HOLD_DAYS,
) -> Tuple[pd.Timestamp, float, str]:
    """
    Scan future bars day-by-day for stop or target.
    Returns (exit_date, exit_price, reason).
    """
    cutoff = pd.Timestamp(entry_date) + pd.Timedelta(days=max_hold_days * 2)
    window = future_prices.loc[
        (future_prices.index > entry_date) & (future_prices.index <= cutoff)
    ]
    if window.empty:
        return entry_date, entry_price, "TIMEOUT"

    hold = 0
    for idx, row in window.iterrows():
        hold += 1
        if hold > max_hold_days:
            return idx, float(row["close"]), "TIMEOUT"
        hi = float(row["high"] or 0.0)
        lo = float(row["low"] or 0.0)
        if hi >= stop_price:
            return idx, stop_price, "STOP"
        if lo <= target_price:
            return idx, target_price, "TARGET"

    last_row = window.iloc[-1]
    return window.index[-1], float(last_row["close"]), "TIMEOUT"


def _gross_short_return_pct(entry: float, exit_px: float) -> float:
    if entry <= 0:
        return 0.0
    return (entry - exit_px) / entry * 100.0


def _compute_backtest_stats(trades: List[ClosedTrade], sleeve: str) -> Dict[str, Any]:
    if not trades:
        return {
            "sleeve": sleeve,
            "trade_count": 0,
            "win_rate_pct": 0.0,
            "expectancy_gross_pct": 0.0,
            "expectancy_net_pct": 0.0,
            "max_dd_equity_pct": 0.0,
            "stop_share_pct": 0.0,
            "avg_hold_days": 0.0,
            "walk_forward_stable": False,
            "wf_windows_positive": 0,
            "wf_windows_total": 0,
            "trade_count_sufficient": False,
        }

    gross_returns = [t.gross_return_pct for t in trades]
    net_returns   = [t.net_return_pct for t in trades]
    winners       = [r for r in gross_returns if r > 0]
    stops         = sum(1 for t in trades if t.exit_reason == "STOP")

    win_rate      = len(winners) / len(trades) * 100.0
    exp_gross     = statistics.mean(gross_returns)
    exp_net       = statistics.mean(net_returns)
    stop_share    = stops / len(trades) * 100.0
    avg_hold      = statistics.mean([t.hold_days for t in trades])

    # Equity-model max drawdown at POSITION_SIZE_PCT per trade
    equity = 1.0
    peak   = 1.0
    max_dd = 0.0
    for r in net_returns:
        equity *= (1.0 + POSITION_SIZE_PCT * r / 100.0)
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak
        if dd > max_dd:
            max_dd = dd

    # Walk-forward stability
    entries = sorted(trades, key=lambda t: t.entry_date)
    bar_dates = sorted({t.entry_date for t in entries})
    wf_windows_positive = 0
    wf_windows_total    = 0
    if len(bar_dates) >= WF_MIN_TRADES:
        for i in range(0, len(bar_dates) - WF_WINDOW_BARS, WF_STEP_BARS):
            window_start = bar_dates[i]
            window_end   = bar_dates[min(i + WF_WINDOW_BARS, len(bar_dates) - 1)]
            window_trades = [
                t for t in entries
                if window_start <= t.entry_date <= window_end
            ]
            if len(window_trades) < WF_MIN_TRADES:
                continue
            wf_windows_total += 1
            if statistics.mean([t.net_return_pct for t in window_trades]) > 0:
                wf_windows_positive += 1

    stable = (
        wf_windows_total >= 2
        and (wf_windows_positive / wf_windows_total) >= WF_STABLE_FRACTION
    )

    min_req = {"A": MIN_TRADES_A, "B": MIN_TRADES_B, "C": MIN_TRADES_C,
               "COMBINED": MIN_TRADES_COMBINED}.get(sleeve, MIN_TRADES_A)

    return {
        "sleeve": sleeve,
        "trade_count": len(trades),
        "win_rate_pct": round(win_rate, 1),
        "expectancy_gross_pct": round(exp_gross, 2),
        "expectancy_net_pct": round(exp_net, 2),
        "max_dd_equity_pct": round(max_dd * 100.0, 1),
        "stop_share_pct": round(stop_share, 1),
        "avg_hold_days": round(avg_hold, 1),
        "walk_forward_stable": stable,
        "wf_windows_positive": wf_windows_positive,
        "wf_windows_total": wf_windows_total,
        "trade_count_sufficient": len(trades) >= min_req,
    }


# ── Sleeve A: FailedUpsideReactionShort ──────────────────────────────────────

class SleeveAScorer:
    """
    Price-structure only.  Detects euphoric gap-up attempts that fail to hold
    and then exhibit downside follow-through with volume confirmation.

    Signal conditions (as of the close of signal_date):
      1. Look back up to 4 days for a gap-up day:
             open_gap = (day_open / prev_close) - 1 >= GAP_MIN_PCT
      2. On that gap day the close was weak:
             range_position = (close - low) / (high - low) <= RANGE_POSITION
      3. Volume on the gap day >= VOL_SPIKE × 20d avg volume
      4. The NEXT day after the gap (and/or signal_date itself) fails to
         reclaim the gap-day high:
             open on reclaim_day < gap_day_high
             close on reclaim_day < gap_day_close
      5. Relative weakness: stock 5d return < SPY 5d return - 2 %
      6. Stock price >= $8, ATR % <= 8 %, avg dollar volume >= $10 M

    Score (0-100):
        - Gap size component:      0-20 pts (3 %-8 %+ gap)
        - Weak close position:     0-25 pts (lower in range = more bearish)
        - Volume spike on gap day: 0-20 pts
        - Relative weakness depth: 0-20 pts
        - Sessions since gap:      0-15 pts (tighter window = fresher signal)
    """

    NAME = "A"

    def score(
        self,
        ticker: str,
        hist: pd.DataFrame,
        spy_hist: pd.DataFrame,
    ) -> Optional[float]:
        f = _normalize_frame(hist)
        spy = _normalize_frame(spy_hist)
        if len(f) < MIN_BARS_REQUIRED or len(spy) < 10:
            return None

        # --- Liquidity filter ---
        last_close = float(f["close"].iloc[-1] or 0.0)
        if last_close < 8.0:
            return None
        avg_vol_20 = float(f["volume"].tail(20).mean() or 0.0)
        if avg_vol_20 * last_close < SLEEVE_A_MIN_DOLLAR_VOL:
            return None
        atr = _atr_pct(f)
        if atr is not None and atr > SLEEVE_A_ATR_MAX_PCT:
            return None

        # --- Look for gap-up failure in last 4 days ---
        gap_day_idx: Optional[int] = None
        gap_pct: float = 0.0
        range_position: float = 0.0
        vol_ratio: float = 0.0

        search_tail = min(5, len(f))  # last 5 rows: look back 4 days from current
        for offset in range(1, search_tail):
            i = len(f) - 1 - offset  # index of candidate gap day
            if i <= 0:
                break
            prev_close = float(f["close"].iloc[i - 1] or 0.0)
            if prev_close <= 0:
                continue
            day_open  = float(f["open"].iloc[i] or 0.0)
            day_high  = float(f["high"].iloc[i] or 0.0)
            day_low   = float(f["low"].iloc[i] or 0.0)
            day_close = float(f["close"].iloc[i] or 0.0)
            day_vol   = float(f["volume"].iloc[i] or 0.0)

            g_pct = (day_open / prev_close - 1.0) * 100.0
            if g_pct < SLEEVE_A_GAP_MIN_PCT:
                continue

            rng = day_high - day_low
            if rng <= 0:
                continue
            rp = (day_close - day_low) / rng

            avg_vol_at_gap = float(f["volume"].iloc[max(0, i - 20):i].mean() or 0.0)
            vr = (day_vol / avg_vol_at_gap) if avg_vol_at_gap > 0 else 0.0

            if rp > SLEEVE_A_RANGE_POSITION or vr < SLEEVE_A_VOL_SPIKE:
                continue

            # Found a valid gap day.  Check failed-reclaim on days AFTER it.
            fail_found = False
            reclaim_sessions = min(SLEEVE_A_RECLAIM_WINDOW, len(f) - 1 - i)
            for j in range(1, reclaim_sessions + 1):
                ri = i + j
                if ri >= len(f):
                    break
                r_open  = float(f["open"].iloc[ri] or 0.0)
                r_close = float(f["close"].iloc[ri] or 0.0)
                if r_open < day_high and r_close < day_close:
                    fail_found = True
                    break
            if not fail_found:
                continue

            gap_day_idx    = i
            gap_pct        = g_pct
            range_position = rp
            vol_ratio      = vr
            break  # use most recent qualifying gap day

        if gap_day_idx is None:
            return None

        # --- Relative weakness: stock 5d vs SPY 5d ---
        def _ret5(frame: pd.DataFrame) -> float:
            if len(frame) < 6:
                return 0.0
            c0 = float(frame["close"].iloc[-6] or 0.0)
            c1 = float(frame["close"].iloc[-1] or 0.0)
            return (c1 / c0 - 1.0) * 100.0 if c0 > 0 else 0.0

        stock_ret5 = _ret5(f)
        spy_ret5   = _ret5(spy)
        rel_weak   = stock_ret5 - spy_ret5
        if rel_weak > SLEEVE_A_REL_WEAK_MIN_PCT:
            return None  # stock is not weak enough relative to SPY

        # --- Score ---
        sessions_since = (len(f) - 1) - gap_day_idx  # 0 = signal today
        freshness_pts  = _clip(15.0 - sessions_since * 5.0, 0.0, 15.0)
        gap_pts        = _clip((gap_pct - 3.0) / 5.0 * 20.0, 0.0, 20.0)
        # lower range_position = weaker close = more bearish
        close_pts      = _clip((SLEEVE_A_RANGE_POSITION - range_position) / SLEEVE_A_RANGE_POSITION * 25.0, 0.0, 25.0)
        vol_pts        = _clip((vol_ratio - 1.0) / 2.0 * 20.0, 0.0, 20.0)
        rel_weak_pts   = _clip((SLEEVE_A_REL_WEAK_MIN_PCT - rel_weak) / 10.0 * 20.0, 0.0, 20.0)

        return gap_pts + close_pts + vol_pts + rel_weak_pts + freshness_pts


# ── Sleeve B: VerifiedBadNewsEventShort ──────────────────────────────────────

class SleeveBScorer:
    """
    Requires a FMP-verified earnings event row (high or medium confidence).
    Looks for negative post-earnings reaction with volume spike and continuation.

    Signal conditions:
      1. A verified event with event_completeness_flag=True and
         confidence_flag in {"high", "medium"} within SLEEVE_B_MAX_LAG_SESSIONS.
      2. On the first tradable session: reaction <= -3 % vs prev_close
         AND volume >= 1.5× 20d avg.
      3. (If CONTINUATION_REQ) Day 2 after event: close < day1_close.

    Score (0-100):
        - Reaction size:        0-30 pts
        - Volume confirmation:  0-25 pts
        - Day-2 continuation:   0-20 pts
        - Event confidence:     0-15 pts (high=15, medium=10)
        - Session proximity:    0-10 pts (smaller lag = better)
    """

    NAME = "B"

    def score(
        self,
        ticker: str,
        hist: pd.DataFrame,
        spy_hist: pd.DataFrame,
        event_store: pd.DataFrame,  # canonical event df from build_event_store_for_universe
        as_of_date: pd.Timestamp,
    ) -> Optional[float]:
        f = _normalize_frame(hist)
        if len(f) < 20:
            return None

        if event_store is None or event_store.empty:
            return None

        # --- Find a qualifying verified event near as_of_date ---
        ticker_upper = str(ticker).upper()
        t_events = event_store[event_store["ticker"] == ticker_upper].copy()
        if t_events.empty:
            return None

        t_events = t_events[
            t_events["event_completeness_flag"].fillna(False).astype(bool)
            & t_events["confidence_flag"].isin(SLEEVE_B_MIN_CONFIDENCE)
        ]
        if t_events.empty:
            return None

        # Find event with first_tradable_session_date within lag window
        t_events["_ftsd"] = pd.to_datetime(t_events["first_tradable_session_date"], errors="coerce")
        t_events = t_events.dropna(subset=["_ftsd"])
        t_events["_lag"] = (
            pd.Timestamp(as_of_date).normalize() - t_events["_ftsd"].dt.normalize()
        ).dt.days
        valid = t_events[(t_events["_lag"] >= 0) & (t_events["_lag"] <= SLEEVE_B_MAX_LAG_SESSIONS * 2)]
        if valid.empty:
            return None

        best = valid.sort_values("_lag").iloc[0]
        ftsd       = pd.Timestamp(best["_ftsd"]).normalize()
        lag_days   = int(best["_lag"])
        confidence = str(best.get("confidence_flag") or "medium")

        # --- Measure reaction on first tradable session ---
        if ftsd not in f.index:
            # Use closest session on or after ftsd
            idx_pos = f.index.searchsorted(ftsd, side="left")
            if idx_pos >= len(f):
                return None
            ftsd = f.index[idx_pos]

        ftsd_loc = f.index.get_loc(ftsd)
        if isinstance(ftsd_loc, slice):
            ftsd_loc = ftsd_loc.start
        if ftsd_loc <= 0 or ftsd_loc >= len(f):
            return None

        prev_close = float(f["close"].iloc[ftsd_loc - 1] or 0.0)
        if prev_close <= 0:
            return None

        event_close  = float(f["close"].iloc[ftsd_loc] or 0.0)
        event_vol    = float(f["volume"].iloc[ftsd_loc] or 0.0)
        avg_vol_20   = float(f["volume"].iloc[max(0, ftsd_loc - 20):ftsd_loc].mean() or 0.0)
        reaction_pct = (event_close / prev_close - 1.0) * 100.0

        if reaction_pct > SLEEVE_B_REACTION_MIN_PCT:
            return None  # not a bad-enough reaction
        vol_ratio = (event_vol / avg_vol_20) if avg_vol_20 > 0 else 0.0
        if vol_ratio < SLEEVE_B_VOL_SPIKE:
            return None

        # --- Day-2 continuation (if required and available) ---
        continuation_pts = 0.0
        if SLEEVE_B_CONTINUATION_REQ:
            if ftsd_loc + 1 < len(f):
                day2_close = float(f["close"].iloc[ftsd_loc + 1] or 0.0)
                if day2_close >= event_close:
                    # Stock bounced on day 2 — weak signal
                    continuation_pts = 0.0
                else:
                    continuation_pts = 20.0
            else:
                # Data not yet available — partial credit
                continuation_pts = 10.0
        else:
            continuation_pts = 10.0  # doesn't require it, half credit

        # --- Score ---
        reaction_pts   = _clip((abs(reaction_pct) - 3.0) / 7.0 * 30.0, 0.0, 30.0)
        vol_pts        = _clip((vol_ratio - 1.0) / 2.0 * 25.0, 0.0, 25.0)
        confidence_pts = 15.0 if confidence == "high" else 10.0
        # Fewer lag days = fresher signal = better
        lag_sessions   = max(0, lag_days // 1)
        proximity_pts  = _clip(10.0 - lag_sessions * 2.0, 0.0, 10.0)

        return reaction_pts + vol_pts + continuation_pts + confidence_pts + proximity_pts


# ── Sleeve C: QualityDeteriorationShort ──────────────────────────────────────

class SleeveCScorer:
    """
    Point-in-time fundamental deterioration from FMP (primary) / AV (secondary).
    Scores six criteria; requires >= SLEEVE_C_MIN_FUNDA_SCORE before evaluating
    price confirmation.

    Fundamental criteria (1 pt each, max 6):
      1. margin_compression (trend — FMP quarterly delta, requires >= 2 quarters)
      2. revenue_deceleration (TTM YoY < 5 % or sequential slowdown)
      3. fcf_negative
      4. debt_stress (D/E > 1.5)
      5. margin_level_below_threshold (operating margin < 10 %)
      6. profit_margin_level_below_threshold (net margin < 5 %)

    Price confirmation (all 3 required):
      - Close < 20-day MA
      - Close < 60-day MA
      - 60-day return < -5 %

    Score (0-100):
        - Fundamental score: 0-50 pts (funda_score / 6 × 50)
        - Price depth below MAs: 0-30 pts
        - 60d return severity: 0-20 pts

    WARNING: This sleeve applies today's fundamentals to historical price data
    (point-in-time contamination).  Results are research-grade, not production-grade.
    """

    NAME = "C"

    def score(
        self,
        ticker: str,
        hist: pd.DataFrame,
        spy_hist: pd.DataFrame,
        fundamentals: Dict[str, Any],
    ) -> Optional[float]:
        f = _normalize_frame(hist)
        if len(f) < MIN_BARS_REQUIRED:
            return None
        if not fundamentals or fundamentals.get("data_quality") == "error":
            return None

        # --- Fundamental scoring ---
        funda_hits = [
            bool(fundamentals.get("margin_compression")),         # trend decline
            bool(fundamentals.get("revenue_deceleration")),
            bool(fundamentals.get("fcf_negative")),
            bool(fundamentals.get("debt_stress")),
            bool(fundamentals.get("margin_level_below_threshold")
                 or fundamentals.get("margin_compression")),       # level fallback
            bool(fundamentals.get("profit_margin_level_below_threshold")
                 or fundamentals.get("profit_margin_declining")),
        ]
        funda_score = sum(funda_hits)
        if funda_score < SLEEVE_C_MIN_FUNDA_SCORE:
            return None

        # --- Price confirmation ---
        closes = f["close"].astype(float)
        last_close = float(closes.iloc[-1] or 0.0)
        if last_close <= 0:
            return None

        ma20 = float(closes.tail(20).mean() or 0.0)
        ma60 = float(closes.tail(60).mean() or 0.0)
        ret60 = (last_close / float(closes.iloc[-min(61, len(closes))] or last_close) - 1.0) * 100.0

        price_confirms = [
            ma20 > 0 and last_close < ma20,
            ma60 > 0 and last_close < ma60,
            ret60 < -5.0,
        ]
        if sum(price_confirms) < SLEEVE_C_PRICE_CONFIRMS:
            return None  # price has not confirmed the fundamental weakness

        # --- Score ---
        funda_pts  = (funda_score / 6.0) * 50.0
        below_ma20 = _clip((1.0 - last_close / ma20) * 100.0, 0.0, 10.0) if ma20 > 0 else 0.0
        below_ma60 = _clip((1.0 - last_close / ma60) * 100.0, 0.0, 10.0) if ma60 > 0 else 0.0
        ma_pts     = below_ma20 * 1.5 + below_ma60 * 1.5   # max ~30
        ret_pts    = _clip((abs(ret60) - 5.0) / 20.0 * 20.0, 0.0, 20.0)

        return min(100.0, funda_pts + ma_pts + ret_pts)


# ── Generic backtest engine ───────────────────────────────────────────────────

def _run_sleeve_backtest(
    sleeve_name: str,
    scorer,
    universe_symbols: Sequence[str],
    price_histories: Dict[str, pd.DataFrame],
    spy_history: pd.DataFrame,
    *,
    lookback: int,
    stop_pct: float,
    target_pct: float,
    max_hold_days: int = DEFAULT_MAX_HOLD_DAYS,
    max_positions: int = 3,
    borrow_rate_ann: float = DEFAULT_BORROW_RATE_ANN,
    period: str = "5y",
    # Sleeve B only
    event_store: Optional[pd.DataFrame] = None,
    # Sleeve C only
    fundamentals_map: Optional[Dict[str, Dict[str, Any]]] = None,
) -> SleeveBacktestResult:
    spy = _normalize_frame(spy_history)
    rebalance_dates = _rebalance_dates(spy, lookback=lookback)
    if not rebalance_dates:
        logger.warning("Sleeve %s: no rebalance dates (lookback=%d)", sleeve_name, lookback)
        return SleeveBacktestResult(sleeve=sleeve_name, universe_size=len(universe_symbols), period=period)

    trades:    List[ClosedTrade] = []
    positions: Dict[str, OpenPosition] = {}

    for rebalance_date in rebalance_dates:
        # ── 1. Advance all open positions to rebalance_date ──────────────────
        for ticker in list(positions.keys()):
            pos = positions[ticker]
            hist_full = price_histories.get(ticker)
            if hist_full is None or hist_full.empty:
                del positions[ticker]
                continue
            f = _normalize_frame(hist_full)
            future = f.loc[f.index > pos.entry_date]
            window = future.loc[future.index <= rebalance_date]
            if window.empty:
                continue

            exit_date, exit_px, reason = _simulate_exit(
                window,
                pos.entry_date,
                pos.entry_price,
                pos.stop_price,
                pos.target_price,
                max_hold_days=max_hold_days,
            )
            hold = max(1, int((pd.Timestamp(exit_date) - pd.Timestamp(pos.entry_date)).days))
            if reason != "TIMEOUT" or hold >= max_hold_days:
                gross = _gross_short_return_pct(pos.entry_price, exit_px)
                friction = pos.friction_pct(hold, borrow_rate_ann=borrow_rate_ann)
                trades.append(ClosedTrade(
                    ticker=ticker,
                    sleeve=sleeve_name,
                    entry_date=pos.entry_date,
                    entry_price=pos.entry_price,
                    exit_date=exit_date,
                    exit_price=exit_px,
                    exit_reason=reason,
                    stop_price=pos.stop_price,
                    target_price=pos.target_price,
                    hold_days=hold,
                    gross_return_pct=gross,
                    net_return_pct=gross - friction * 100.0,
                    score=pos.score,
                    sector=pos.sector,
                ))
                del positions[ticker]

        # ── 2. Score candidates at rebalance_date ────────────────────────────
        slots_available = max_positions - len(positions)
        if slots_available <= 0:
            continue

        candidates: List[Tuple[str, float]] = []
        for ticker in universe_symbols:
            if ticker in positions:
                continue
            hist_full = price_histories.get(ticker)
            if hist_full is None or hist_full.empty:
                continue
            f = _normalize_frame(hist_full)
            f_slice = f.loc[f.index <= rebalance_date]
            if len(f_slice) < MIN_BARS_REQUIRED:
                continue
            spy_slice = spy.loc[spy.index <= rebalance_date]

            try:
                if sleeve_name == "A":
                    sc = scorer.score(ticker, f_slice, spy_slice)
                elif sleeve_name == "B":
                    sc = scorer.score(ticker, f_slice, spy_slice, event_store, rebalance_date)
                elif sleeve_name == "C":
                    funda = (fundamentals_map or {}).get(ticker) or {}
                    sc = scorer.score(ticker, f_slice, spy_slice, funda)
                else:
                    sc = None
            except Exception as exc:
                logger.debug("Sleeve %s scorer error ticker=%s: %s", sleeve_name, ticker, exc)
                sc = None

            if sc is not None and sc > 0:
                candidates.append((ticker, sc))

        candidates.sort(key=lambda x: -x[1])

        for ticker, score in candidates[:slots_available]:
            # Entry at next available open after rebalance_date
            hist_full = price_histories.get(ticker)
            if hist_full is None:
                continue
            f = _normalize_frame(hist_full)
            idx = f.index.searchsorted(rebalance_date, side="right")
            if idx >= len(f):
                continue
            entry_date  = f.index[idx]
            entry_price = float(f["open"].iloc[idx] or 0.0)
            if entry_price <= 0:
                continue

            stop   = entry_price * (1.0 + stop_pct)
            target = entry_price * (1.0 - target_pct)

            # Derive sector from fundamentals (Sleeve C) or leave UNKNOWN
            sector = "UNKNOWN"
            if fundamentals_map:
                funda = (fundamentals_map or {}).get(ticker) or {}
                sector = str(funda.get("sector") or "UNKNOWN")

            positions[ticker] = OpenPosition(
                ticker=ticker,
                sleeve=sleeve_name,
                entry_date=entry_date,
                entry_price=entry_price,
                stop_price=stop,
                target_price=target,
                score=score,
                sector=sector,
            )

    # ── Close any remaining open positions at last available price ────────────
    for ticker, pos in positions.items():
        hist_full = price_histories.get(ticker)
        if hist_full is None or hist_full.empty:
            continue
        f = _normalize_frame(hist_full)
        future = f.loc[f.index > pos.entry_date]
        if future.empty:
            continue
        exit_date, exit_px, reason = _simulate_exit(
            future, pos.entry_date, pos.entry_price,
            pos.stop_price, pos.target_price,
            max_hold_days=max_hold_days,
        )
        hold    = max(1, int((pd.Timestamp(exit_date) - pd.Timestamp(pos.entry_date)).days))
        gross   = _gross_short_return_pct(pos.entry_price, exit_px)
        friction = pos.friction_pct(hold, borrow_rate_ann=borrow_rate_ann)
        trades.append(ClosedTrade(
            ticker=ticker,
            sleeve=sleeve_name,
            entry_date=pos.entry_date,
            entry_price=pos.entry_price,
            exit_date=exit_date,
            exit_price=exit_px,
            exit_reason=reason,
            stop_price=pos.stop_price,
            target_price=pos.target_price,
            hold_days=hold,
            gross_return_pct=gross,
            net_return_pct=gross - friction * 100.0,
            score=pos.score,
            sector=pos.sector,
        ))

    return SleeveBacktestResult(
        sleeve=sleeve_name,
        trades=trades,
        universe_size=len(universe_symbols),
        period=period,
    )


# ── Portfolio combiner ────────────────────────────────────────────────────────

class PortfolioCombiner:
    """
    Runs all three sleeves concurrently on the same rebalance schedule.
    At each rebalance date:
      - Score each symbol on each active sleeve.
      - Combine: combined_score = max(sleeve_score × sleeve_weight) per symbol.
      - Apply borrow penalty for high short interest.
      - Select top MAX_COMBINED_POSITIONS with SECTOR_CAP.
      - Respect DAILY_LOSS_CAP and WEEKLY_LOSS_CAP circuit breakers.
    """

    def run(
        self,
        universe_symbols: Sequence[str],
        price_histories: Dict[str, pd.DataFrame],
        spy_history: pd.DataFrame,
        *,
        lookback: int,
        period: str = "5y",
        event_store: Optional[pd.DataFrame] = None,
        fundamentals_map: Optional[Dict[str, Dict[str, Any]]] = None,
        borrow_rate_ann: float = DEFAULT_BORROW_RATE_ANN,
    ) -> SleeveBacktestResult:
        spy = _normalize_frame(spy_history)
        rebalance_dates = _rebalance_dates(spy, lookback=lookback)
        if not rebalance_dates:
            return SleeveBacktestResult(sleeve="COMBINED", universe_size=len(universe_symbols), period=period)

        scorer_a = SleeveAScorer()
        scorer_b = SleeveBScorer()
        scorer_c = SleeveCScorer()

        # Sleeve-specific geometry lookup
        geometry = {
            "A": (SLEEVE_A_STOP_PCT, SLEEVE_A_TARGET_PCT),
            "B": (SLEEVE_B_STOP_PCT, SLEEVE_B_TARGET_PCT),
            "C": (SLEEVE_C_STOP_PCT, SLEEVE_C_TARGET_PCT),
        }

        trades:    List[ClosedTrade] = []
        positions: Dict[str, OpenPosition] = {}
        daily_pnl: Dict[str, float] = {}    # date-str → daily book pnl %
        weekly_pnl_start: Tuple[Optional[str], float] = (None, 0.0)

        def _book_pnl_today(date_key: str) -> float:
            return daily_pnl.get(date_key, 0.0)

        def _week_key(ts: pd.Timestamp) -> str:
            return f"{ts.isocalendar()[0]}-W{ts.isocalendar()[1]:02d}"

        for rebalance_date in rebalance_dates:
            date_key = str(pd.Timestamp(rebalance_date).date())
            wk_key   = _week_key(pd.Timestamp(rebalance_date))

            # ── Close out positions that triggered stop/target ────────────────
            day_book_pnl = 0.0
            for ticker in list(positions.keys()):
                pos = positions[ticker]
                hist_full = price_histories.get(ticker)
                if hist_full is None:
                    del positions[ticker]
                    continue
                f = _normalize_frame(hist_full)
                future = f.loc[(f.index > pos.entry_date) & (f.index <= rebalance_date)]
                if future.empty:
                    continue
                exit_date, exit_px, reason = _simulate_exit(
                    future, pos.entry_date, pos.entry_price,
                    pos.stop_price, pos.target_price,
                    max_hold_days=DEFAULT_MAX_HOLD_DAYS,
                )
                hold = max(1, int((pd.Timestamp(exit_date) - pd.Timestamp(pos.entry_date)).days))
                if reason != "TIMEOUT" or hold >= DEFAULT_MAX_HOLD_DAYS:
                    gross    = _gross_short_return_pct(pos.entry_price, exit_px)
                    friction = pos.friction_pct(hold, borrow_rate_ann=borrow_rate_ann)
                    net      = gross - friction * 100.0
                    trades.append(ClosedTrade(
                        ticker=ticker,
                        sleeve=pos.sleeve,
                        entry_date=pos.entry_date,
                        entry_price=pos.entry_price,
                        exit_date=exit_date,
                        exit_price=exit_px,
                        exit_reason=reason,
                        stop_price=pos.stop_price,
                        target_price=pos.target_price,
                        hold_days=hold,
                        gross_return_pct=gross,
                        net_return_pct=net,
                        score=pos.score,
                        sector=pos.sector,
                    ))
                    day_book_pnl += net * POSITION_SIZE_PCT
                    del positions[ticker]

            daily_pnl[date_key] = daily_pnl.get(date_key, 0.0) + day_book_pnl

            # ── Circuit breakers ──────────────────────────────────────────────
            if daily_pnl.get(date_key, 0.0) < -DAILY_LOSS_CAP_PCT * 100.0:
                continue
            wk_start_key, wk_start_pnl = weekly_pnl_start
            if wk_start_key != wk_key:
                weekly_pnl_start = (wk_key, 0.0)
                wk_start_pnl = 0.0
            wk_cumul = sum(v for k, v in daily_pnl.items() if k.startswith(wk_key[:4]))
            if wk_cumul - wk_start_pnl < -WEEKLY_LOSS_CAP_PCT * 100.0:
                continue

            # ── Score all symbols on all sleeves ──────────────────────────────
            slots = MAX_COMBINED_POSITIONS - len(positions)
            if slots <= 0:
                continue

            combined_scores: Dict[str, Tuple[float, str]] = {}  # ticker → (score, sleeve)
            spy_slice = spy.loc[spy.index <= rebalance_date]

            for ticker in universe_symbols:
                if ticker in positions:
                    continue
                hist_full = price_histories.get(ticker)
                if hist_full is None or hist_full.empty:
                    continue
                f = _normalize_frame(hist_full)
                f_slice = f.loc[f.index <= rebalance_date]
                if len(f_slice) < MIN_BARS_REQUIRED:
                    continue

                best_score: Optional[float] = None
                best_sleeve: str = "A"

                for slv, scorer, weight in [
                    ("A", scorer_a, SLEEVE_WEIGHTS["A"]),
                    ("B", scorer_b, SLEEVE_WEIGHTS["B"]),
                    ("C", scorer_c, SLEEVE_WEIGHTS["C"]),
                ]:
                    try:
                        if slv == "A":
                            sc = scorer.score(ticker, f_slice, spy_slice)
                        elif slv == "B":
                            sc = scorer.score(ticker, f_slice, spy_slice, event_store, rebalance_date)
                        elif slv == "C":
                            funda = (fundamentals_map or {}).get(ticker) or {}
                            sc = scorer.score(ticker, f_slice, spy_slice, funda)
                        else:
                            sc = None
                    except Exception:
                        sc = None

                    if sc is None:
                        continue
                    weighted = sc * weight

                    # Borrow penalty from fundamentals
                    si_pct = None
                    if fundamentals_map:
                        funda = (fundamentals_map or {}).get(ticker) or {}
                        si_pct = _safe_float(funda.get("short_interest_pct"))
                    if si_pct is not None:
                        if si_pct / 100.0 > BORROW_EXTREME_THRESHOLD:
                            weighted *= BORROW_EXTREME_FACTOR
                        elif si_pct / 100.0 > BORROW_HIGH_SI_THRESHOLD:
                            weighted *= BORROW_PENALTY_FACTOR

                    if best_score is None or weighted > best_score:
                        best_score = weighted
                        best_sleeve = slv

                if best_score is not None and best_score > 0:
                    combined_scores[ticker] = (best_score, best_sleeve)

            # ── Rank, sector cap, select ──────────────────────────────────────
            ranked = sorted(combined_scores.items(), key=lambda x: -x[1][0])
            sector_counts: Dict[str, int] = {}
            for ticker, pos in positions.items():
                sector_counts[pos.sector] = sector_counts.get(pos.sector, 0) + 1

            selected = 0
            for ticker, (score, best_sleeve) in ranked:
                if selected >= slots:
                    break
                funda = (fundamentals_map or {}).get(ticker) or {}
                sector = str(funda.get("sector") or "UNKNOWN")
                if sector_counts.get(sector, 0) >= SECTOR_CAP:
                    continue

                hist_full = price_histories.get(ticker)
                if hist_full is None:
                    continue
                f = _normalize_frame(hist_full)
                idx = f.index.searchsorted(rebalance_date, side="right")
                if idx >= len(f):
                    continue

                entry_date  = f.index[idx]
                entry_price = float(f["open"].iloc[idx] or 0.0)
                if entry_price <= 0:
                    continue

                stop_pct_v, target_pct_v = geometry[best_sleeve]
                positions[ticker] = OpenPosition(
                    ticker=ticker,
                    sleeve=best_sleeve,
                    entry_date=entry_date,
                    entry_price=entry_price,
                    stop_price=entry_price * (1.0 + stop_pct_v),
                    target_price=entry_price * (1.0 - target_pct_v),
                    score=score,
                    sector=sector,
                )
                sector_counts[sector] = sector_counts.get(sector, 0) + 1
                selected += 1

        # Close remaining open positions
        for ticker, pos in positions.items():
            hist_full = price_histories.get(ticker)
            if hist_full is None:
                continue
            f = _normalize_frame(hist_full)
            future = f.loc[f.index > pos.entry_date]
            if future.empty:
                continue
            exit_date, exit_px, reason = _simulate_exit(
                future, pos.entry_date, pos.entry_price,
                pos.stop_price, pos.target_price,
                max_hold_days=DEFAULT_MAX_HOLD_DAYS,
            )
            hold    = max(1, int((pd.Timestamp(exit_date) - pd.Timestamp(pos.entry_date)).days))
            gross   = _gross_short_return_pct(pos.entry_price, exit_px)
            friction = pos.friction_pct(hold, borrow_rate_ann=borrow_rate_ann)
            trades.append(ClosedTrade(
                ticker=ticker,
                sleeve=pos.sleeve,
                entry_date=pos.entry_date,
                entry_price=pos.entry_price,
                exit_date=exit_date,
                exit_price=exit_px,
                exit_reason=reason,
                stop_price=pos.stop_price,
                target_price=pos.target_price,
                hold_days=hold,
                gross_return_pct=gross,
                net_return_pct=gross - friction * 100.0,
                score=pos.score,
                sector=pos.sector,
            ))

        return SleeveBacktestResult(
            sleeve="COMBINED",
            trades=trades,
            universe_size=len(universe_symbols),
            period=period,
        )


# ── FMP-backed event store builder ───────────────────────────────────────────

def _build_fmp_event_store(
    tickers: Sequence[str],
    price_histories: Dict[str, pd.DataFrame],
    *,
    max_workers: int = 6,
) -> pd.DataFrame:
    """
    Build a canonical event DataFrame for Sleeve B using RoutingEarningsProvider
    (FMP primary → AV secondary).  Does NOT use earnings_event_store.py's
    yfinance-only path.

    Each row represents one verified earnings event with:
        ticker, earnings_date, first_tradable_session_date,
        confidence_flag, event_completeness_flag, verified_event_flag

    FMP results are cached 30 days on disk; AV results 7 days.  Only symbols
    with no cached entry consume API budget — re-runs are free.

    FMP daily budget note: the default cap is FMP_DAILY_BUDGET_TARGET (env var,
    default 200).  Set FMP_DAILY_BUDGET_TARGET=2000 (or your plan limit) to
    avoid mid-run exhaustion on large universe runs.
    """
    import os
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from research_data_provider import make_research_providers

    # One shared provider set so the cache and budget are shared across workers
    providers = make_research_providers()
    earnings_router = providers["earnings"]

    # Inform the user of the effective FMP budget for this run
    fmp_budget = providers.get("fmp_budget")
    if fmp_budget is not None:
        bs = fmp_budget.summary()
        print(
            f"[EVENTS] FMP budget: {bs['calls_used']}/{bs['daily_target']} used today "
            f"({bs['calls_remaining']} remaining)",
            flush=True,
        )

    tickers_norm = [str(t).upper().strip() for t in tickers if str(t).strip()]
    print(
        f"[EVENTS] Fetching earnings events for {len(tickers_norm)} symbols "
        f"via FMP→AV routing provider ...",
        flush=True,
    )

    def _fetch_one(ticker: str):
        result = earnings_router.fetch_dates(ticker)
        events = result.value if result.success and result.value else []
        return ticker, events

    all_events: List[Any] = []
    symbols_missing: List[str] = []

    with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as executor:
        futures = {executor.submit(_fetch_one, t): t for t in tickers_norm}
        for fut in as_completed(futures):
            ticker = futures[fut]
            try:
                _, events = fut.result()
            except Exception as exc:
                logger.warning("[EVENTS] fetch_one failed ticker=%s: %s", ticker, exc)
                events = []
            if events:
                all_events.extend(events)
            else:
                symbols_missing.append(ticker)

    print(
        f"[EVENTS] Raw events: {len(all_events)} from "
        f"{len(tickers_norm) - len(symbols_missing)}/{len(tickers_norm)} symbols "
        f"(missing={len(symbols_missing)})",
        flush=True,
    )

    if not all_events:
        print("[EVENTS] WARNING: 0 events fetched — Sleeve B will produce 0 trades", flush=True)
        return pd.DataFrame()

    # ── Convert EarningsEvent → canonical rows with first_tradable_session_date ─
    # Group by ticker so we only iterate each price history once
    from earnings_event_store import (
        infer_session_flag,
        _first_tradable_session_date,
        _next_session_gap_pct,
        _confidence_flag,
    )
    # Build ticker → events lookup
    from collections import defaultdict
    events_by_ticker: Dict[str, List[Any]] = defaultdict(list)
    for ev in all_events:
        events_by_ticker[ev.ticker].append(ev)

    canonical_rows: List[Dict[str, Any]] = []

    for ticker, evs in events_by_ticker.items():
        price_history = price_histories.get(ticker)
        ph = _normalize_frame(price_history) if price_history is not None else pd.DataFrame()

        for ev in evs:
            try:
                ed = pd.Timestamp(ev.reported_date).normalize()
                session_flag = str(ev.session_flag or "unknown")
                ftsd = _first_tradable_session_date(ed, session_flag, ph)
                gap_pct = _next_session_gap_pct(ftsd, ph)
                complete = ftsd is not None and gap_pct is not None
                # All FMP events have a source timestamp available — count as 1 source
                has_ts = session_flag != "unknown"
                source_labels = ["verified_primary"] if bool(ev.source or "fmp") and "fmp" in str(ev.source or "fmp").lower() else ["fallback_secondary"]
                confidence = _confidence_flag(1, has_ts, bool(complete), source_labels)
                canonical_rows.append({
                    "ticker": ticker,
                    "earnings_date": ed,
                    "event_timestamp": None,
                    "session_flag": session_flag,
                    "source_identifier": str(ev.source or "fmp_earnings"),
                    "source_count": 1,
                    "confidence_flag": confidence,
                    "first_tradable_session_date": ftsd,
                    "next_session_gap_pct": gap_pct,
                    "event_completeness_flag": bool(complete),
                    "verified_event_flag": bool(complete and confidence in {"high", "medium"}),
                })
            except Exception as exc:
                logger.debug("[EVENTS] canonical row build failed ticker=%s ev=%s: %s", ticker, ev, exc)

    if not canonical_rows:
        print("[EVENTS] WARNING: 0 canonical rows after mapping — check price history coverage", flush=True)
        return pd.DataFrame()

    df = pd.DataFrame(canonical_rows)
    df["ticker"] = df["ticker"].astype(str).str.upper()
    n_verified = int(df["verified_event_flag"].fillna(False).sum())
    print(
        f"[EVENTS] {len(df)} canonical events, {n_verified} verified",
        flush=True,
    )
    return df


# ── Validation runner ─────────────────────────────────────────────────────────

def run_validation(
    *,
    universe_size: int = 200,
    period: str = "5y",
    mode: str = "all",
    lookback: Optional[int] = None,
    max_workers: int = 6,
) -> Dict[str, SleeveBacktestResult]:
    """
    Load universe, price histories, fundamentals, and event store.
    Then run whichever sleeves are requested by mode.

    mode: "all" | "sleeve_a" | "sleeve_b" | "sleeve_c" | "combined"
    """
    from short_backtester import (
        _load_market_screen_symbols,
        _prefilter_liquid_symbols,
        _preload_price_histories,
        fetch_spy_history,
        warm_short_fundamentals_cache,
        load_short_fundamentals,
    )

    if lookback is None:
        # Map period string to approximate bar count
        _p_map = {
            "1y": 252, "2y": 504, "3y": 756, "5y": 1260,
            "7y": 1764, "10y": 2520, "max": 2520,
        }
        lookback = _p_map.get(period, 1260)

    run_modes = {mode} if mode != "all" else {"sleeve_a", "sleeve_b", "sleeve_c", "combined"}

    print(f"\n[VALIDATION] universe_size={universe_size}  period={period}  mode={mode}")

    # ── Load universe ─────────────────────────────────────────────────────────
    print("[UNIVERSE] Loading market screen symbols ...", flush=True)
    all_symbols, source = _load_market_screen_symbols()
    print(f"[UNIVERSE] Market screen: {len(all_symbols)} symbols from {source}", flush=True)

    print("[UNIVERSE] Liquid pre-filter ...", flush=True)
    liquid_symbols, liq_meta = _prefilter_liquid_symbols(all_symbols, limit=universe_size)
    print(
        f"[UNIVERSE] Liquid filter: {liq_meta['liquid_prefilter_count']} → "
        f"{len(liquid_symbols)} seed-pool symbols",
        flush=True,
    )

    # ── Load price histories ──────────────────────────────────────────────────
    print(f"[PRICE] Pre-loading {len(liquid_symbols)} price histories ({period}) ...", flush=True)
    price_histories = _preload_price_histories(liquid_symbols, period=period)
    print(f"[PRICE] Loaded {sum(1 for f in price_histories.values() if not f.empty)} non-empty frames", flush=True)

    spy_history = fetch_spy_history(period)
    print(f"[PRICE] SPY: {len(spy_history)} bars", flush=True)

    results: Dict[str, SleeveBacktestResult] = {}

    # ── Sleeve C: load fundamentals ───────────────────────────────────────────
    fundamentals_map: Optional[Dict[str, Dict[str, Any]]] = None
    if any(m in run_modes for m in ("sleeve_c", "combined")):
        print(f"[FUNDAMENTALS] Warming cache for {len(liquid_symbols)} symbols ...", flush=True)
        fundamentals_map = warm_short_fundamentals_cache(
            liquid_symbols,
            fundamentals_loader=load_short_fundamentals,
        )
        print(f"[FUNDAMENTALS] Loaded {len(fundamentals_map)} fundamental records", flush=True)

    # ── Sleeve B: build event store via FMP routing provider ─────────────────
    # We do NOT use earnings_event_store.build_event_store_for_universe here —
    # that module is yfinance-only and returns 0 verified events when yfinance
    # surfaces fail (which is most of the time in yfinance 1.x).  Instead we
    # call research_data_provider.RoutingEarningsProvider directly: FMP primary
    # (30-day cache), AV secondary (7-day cache).  Both providers cache results
    # to disk so the daily API budget only matters for symbols not yet cached.
    event_store_df: Optional[pd.DataFrame] = None
    if any(m in run_modes for m in ("sleeve_b", "combined")):
        event_store_df = _build_fmp_event_store(
            tickers=liquid_symbols,
            price_histories=price_histories,
            max_workers=max_workers,
        )

    # ── Run sleeves ───────────────────────────────────────────────────────────
    if "sleeve_a" in run_modes:
        print("\n[SLEEVE A] Running FailedUpsideReactionShort ...", flush=True)
        results["A"] = _run_sleeve_backtest(
            sleeve_name="A",
            scorer=SleeveAScorer(),
            universe_symbols=liquid_symbols,
            price_histories=price_histories,
            spy_history=spy_history,
            lookback=lookback,
            stop_pct=SLEEVE_A_STOP_PCT,
            target_pct=SLEEVE_A_TARGET_PCT,
            max_positions=4,
            period=period,
        )
        print(f"[SLEEVE A] Done: {len(results['A'].trades)} trades", flush=True)

    if "sleeve_b" in run_modes:
        print("\n[SLEEVE B] Running VerifiedBadNewsEventShort ...", flush=True)
        results["B"] = _run_sleeve_backtest(
            sleeve_name="B",
            scorer=SleeveBScorer(),
            universe_symbols=liquid_symbols,
            price_histories=price_histories,
            spy_history=spy_history,
            lookback=lookback,
            stop_pct=SLEEVE_B_STOP_PCT,
            target_pct=SLEEVE_B_TARGET_PCT,
            max_positions=3,
            period=period,
            event_store=event_store_df,
        )
        print(f"[SLEEVE B] Done: {len(results['B'].trades)} trades", flush=True)

    if "sleeve_c" in run_modes:
        print("\n[SLEEVE C] Running QualityDeteriorationShort ...", flush=True)
        results["C"] = _run_sleeve_backtest(
            sleeve_name="C",
            scorer=SleeveCScorer(),
            universe_symbols=liquid_symbols,
            price_histories=price_histories,
            spy_history=spy_history,
            lookback=lookback,
            stop_pct=SLEEVE_C_STOP_PCT,
            target_pct=SLEEVE_C_TARGET_PCT,
            max_positions=4,
            period=period,
            fundamentals_map=fundamentals_map,
        )
        print(f"[SLEEVE C] Done: {len(results['C'].trades)} trades", flush=True)

    if "combined" in run_modes:
        print("\n[COMBINED] Running portfolio combiner ...", flush=True)
        results["COMBINED"] = PortfolioCombiner().run(
            universe_symbols=liquid_symbols,
            price_histories=price_histories,
            spy_history=spy_history,
            lookback=lookback,
            period=period,
            event_store=event_store_df,
            fundamentals_map=fundamentals_map,
        )
        print(f"[COMBINED] Done: {len(results['COMBINED'].trades)} trades", flush=True)

    return results


# ── Report printer ────────────────────────────────────────────────────────────

_DIVIDER = "═" * 66
_LINE    = "─" * 66

def _fmt_pct(v: float, width: int = 6) -> str:
    return f"{v:+{width}.1f}%"

def _stability_label(stable: bool, pos: int, total: int) -> str:
    if total == 0:
        return "NO DATA"
    ratio = pos / total
    if stable and ratio >= 0.66:
        return f"STABLE   ({pos}/{total} windows +ve)"
    if stable:
        return f"MARGINAL ({pos}/{total} windows +ve)"
    return f"UNSTABLE ({pos}/{total} windows +ve)"


SLEEVE_FULL_NAMES = {
    "A":        "FailedUpsideReactionShort  (price-structure)",
    "B":        "VerifiedBadNewsEventShort  (event-anchored)",
    "C":        "QualityDeteriorationShort  (fundamental, point-in-time)",
    "COMBINED": "Combined Portfolio         (multi-sleeve, sector-capped)",
}


def print_validation_report(
    results: Dict[str, SleeveBacktestResult],
    *,
    universe_size: int,
    period: str,
) -> None:
    from datetime import date as _date
    today = str(_date.today())

    print(f"\n{_DIVIDER}")
    print(f"  MULTI-SLEEVE SHORT RESEARCH — VALIDATION REPORT")
    print(f"  Universe: {universe_size} symbols | Period: {period} | {today}")
    print(_DIVIDER)

    sleeve_stats: Dict[str, Dict[str, Any]] = {}
    for key in ("A", "B", "C", "COMBINED"):
        if key not in results:
            continue
        res = results[key]
        st  = res.stats()
        sleeve_stats[key] = st

        full_name = SLEEVE_FULL_NAMES.get(key, key)
        print(f"\nSLEEVE {key}: {full_name}")
        print(_LINE)

        if st["trade_count"] == 0:
            print("  NO TRADES GENERATED")
            continue

        sufficient = "✓" if st["trade_count_sufficient"] else "✗"
        print(
            f"  Trades:    {st['trade_count']:>4} {sufficient}  |  "
            f"Win rate: {st['win_rate_pct']:>5.1f}%  |  "
            f"Stop share: {st['stop_share_pct']:>5.1f}%"
        )
        print(
            f"  Exp gross: {_fmt_pct(st['expectancy_gross_pct'])}  |  "
            f"Exp net:  {_fmt_pct(st['expectancy_net_pct'])}  |  "
            f"Max DD: {st['max_dd_equity_pct']:>5.1f}%"
        )
        print(
            f"  Avg hold:  {st['avg_hold_days']:>4.1f}d  |  "
            f"Walk-fwd: {_stability_label(st['walk_forward_stable'], st['wf_windows_positive'], st['wf_windows_total'])}"
        )

        # Regime breakdown
        trades = results[key].trades
        if trades:
            from short_backtester import _market_regime_label, _normalize_price_frame
            regime_wins:   Dict[str, List[float]] = {}
            for t in trades:
                regime_wins.setdefault("all", []).append(t.gross_return_pct)
            wins_all = [r for r in regime_wins.get("all", []) if r > 0]
            wr_all   = len(wins_all) / len(regime_wins.get("all", [1])) * 100
            print(f"  Win rate by regime: overall={wr_all:.0f}%  (regime-level breakdown requires VIX data)")

        if key == "C":
            print(
                "  NOTE: Sleeve C applies TODAY's fundamentals to historical prices "
                "(point-in-time contamination).\n"
                "        Positive results are suspect until verified with a true PIT data set."
            )
        if key == "B" and st.get("trade_count", 0) < MIN_TRADES_B * 3:
            print(
                f"  NOTE: Low trade count ({st['trade_count']}).  "
                "Sleeve B is event-anchored — coverage depends on FMP event availability.  "
                "Consider expanding universe to 500+ for this sleeve."
            )

    # ── Summary verdict ───────────────────────────────────────────────────────
    print(f"\n{_DIVIDER}")
    print("  CONCLUSIONS")
    print(_LINE)

    valid_sleeves = {k: sleeve_stats[k] for k in ("A", "B", "C") if k in sleeve_stats}
    combined_st   = sleeve_stats.get("COMBINED")

    if not valid_sleeves:
        print("  No sleeve results available.")
        return

    # Strongest = highest positive expectancy net + stable + sufficient trades
    def _sleeve_rank_key(item: Tuple[str, Dict[str, Any]]) -> float:
        k, st = item
        if not st["trade_count_sufficient"] or st["expectancy_net_pct"] <= 0:
            return -999.0
        stability_bonus = 1.0 if st["walk_forward_stable"] else 0.0
        return st["expectancy_net_pct"] + stability_bonus

    ranked_sleeves = sorted(valid_sleeves.items(), key=_sleeve_rank_key, reverse=True)

    if ranked_sleeves and _sleeve_rank_key(ranked_sleeves[0]) > -999.0:
        best_key, best_st = ranked_sleeves[0]
        print(f"  Strongest sleeve:  {best_key} — {SLEEVE_FULL_NAMES[best_key].split('(')[0].strip()}")
        print(f"    Exp net {_fmt_pct(best_st['expectancy_net_pct'])}  |  "
              f"WR {best_st['win_rate_pct']:.1f}%  |  "
              f"{'STABLE' if best_st['walk_forward_stable'] else 'MARGINAL'}")
    else:
        print("  No sleeve produced positive net expectancy with sufficient trade count.")

    # Narrow but real
    for k, st in valid_sleeves.items():
        if st["expectancy_net_pct"] > 0 and not st["trade_count_sufficient"]:
            print(
                f"  Real but narrow:   {k} ({st['trade_count']} trades < minimum)  "
                "— valid edge but needs larger universe to matter"
            )

    # Combined vs best single
    if combined_st:
        best_exp = max((st["expectancy_net_pct"] for st in valid_sleeves.values()), default=0.0)
        combined_exp = combined_st["expectancy_net_pct"]
        if combined_exp > best_exp:
            print(
                f"  Combined book:     STRONGER than best single sleeve "
                f"(+{combined_exp - best_exp:.2f}% additional expectancy, "
                f"DD={combined_st['max_dd_equity_pct']:.1f}%)"
            )
        elif combined_exp > 0:
            print(
                f"  Combined book:     Positive but not stronger than best single sleeve "
                f"(combined {_fmt_pct(combined_exp)} vs best {_fmt_pct(best_exp)})"
            )
        else:
            print("  Combined book:     Negative net expectancy — do not trade combined until individual sleeves are validated.")

    # Next optimization cycle
    candidates_for_next = [
        (k, st) for k, st in valid_sleeves.items()
        if st["trade_count_sufficient"] and st["expectancy_net_pct"] > 0
    ]
    if candidates_for_next:
        # Suggest the one with good expectancy but lower trade count (more upside from expansion)
        by_count = sorted(candidates_for_next, key=lambda x: x[1]["trade_count"])
        next_k, next_st = by_count[0]
        print(
            f"  Next optimization: {next_k} — "
            f"expand universe for this sleeve to improve trade frequency "
            f"(currently {next_st['trade_count']} trades)"
        )

    print(_DIVIDER)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Multi-sleeve short research validation framework"
    )
    parser.add_argument(
        "--universe", type=int, default=200,
        help="Universe size: 200 / 300 / 500 (default: 200)"
    )
    parser.add_argument(
        "--period", type=str, default="5y",
        choices=["2y", "3y", "5y", "7y", "10y", "max"],
        help="Price history period (default: 5y)"
    )
    parser.add_argument(
        "--mode", type=str, default="all",
        choices=["all", "sleeve_a", "sleeve_b", "sleeve_c", "combined"],
        help="Which sleeves to run (default: all)"
    )
    parser.add_argument(
        "--lookback", type=int, default=None,
        help="Lookback bars for rebalance date generation (default: auto from period)"
    )
    parser.add_argument(
        "--workers", type=int, default=6,
        help="Max workers for event store build (default: 6)"
    )
    parser.add_argument(
        "--out", type=str, default=None,
        help="Optional JSON output path for machine-readable results"
    )
    args = parser.parse_args()

    results = run_validation(
        universe_size=args.universe,
        period=args.period,
        mode=args.mode,
        lookback=args.lookback,
        max_workers=args.workers,
    )

    print_validation_report(
        results,
        universe_size=args.universe,
        period=args.period,
    )

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        serialisable: Dict[str, Any] = {}
        for key, res in results.items():
            st = res.stats()
            serialisable[key] = {
                "stats": st,
                "trades": [
                    {
                        "ticker":            t.ticker,
                        "sleeve":            t.sleeve,
                        "entry_date":        str(t.entry_date.date()),
                        "entry_price":       t.entry_price,
                        "exit_date":         str(t.exit_date.date()),
                        "exit_price":        t.exit_price,
                        "exit_reason":       t.exit_reason,
                        "hold_days":         t.hold_days,
                        "gross_return_pct":  t.gross_return_pct,
                        "net_return_pct":    t.net_return_pct,
                        "score":             t.score,
                        "sector":            t.sector,
                    }
                    for t in res.trades
                ],
            }
        out_path.write_text(json.dumps(serialisable, indent=2, default=str), encoding="utf-8")
        print(f"\n[OUT] Results written to {out_path}", flush=True)


if __name__ == "__main__":
    main()

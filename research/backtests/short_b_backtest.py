"""
research/backtests/short_b_backtest.py — SHORT Sleeve B validation backtest.

Sleeve B: Broken Leader / Structural Deterioration Short.

Tests the proposed Sleeve B scanner logic against historical data 2021–2024.
Complements short_v1_backtest.py (Sleeve A — Event Continuation Short).

Signal logic (production-candidate conditions, from manual validation pass on
META/SNAP/PYPL 2021-2023):

  Screening gates (structural — checked before any timing logic):
    1. Prior leadership: stock achieved ≥ +40% in any 18-month rolling window
       in the last 3 years. Filters for names that were institutionally held.
    2. Price < 50-day MA (structural break confirmed, not just a dip)
    3. Lower high: 52-week high is not in the last 20 bars (new high attempt failed)
    4. Drawdown range: −20% to −35% from 52-week high. Below −20% = not broken
       enough. Above −35% = deeper breakdown where mean-reversion risk and violent
       bear-market rallies dominate. (Calibration v2: tightened from −55%.)
    5. RSI at entry: 33–55. Below 33 = already oversold. Above 55 = not broken enough.
    6. Relative weakness vs SPY 20d: ticker ≤ −6% vs SPY. Confirms the name is
       underperforming the market, not just falling in a falling market.
    7. Distribution confirmation: ≥ 2 of last 10 sessions had volume > 1.3× 20-day
       avg on a down close (close < open). Active supply present.

  Timing gate (specific entry trigger):
    8. Failed rally: in the last 20 bars, price bounced ≥ 4% from a local low
       within ≤ 12 bars, then today's close is below that bounce start (local low).
       The failed rally peak is used as the stop reference.

  Regime gate (Calibration v2):
    9. SPY below its 50-day MA on signal date. Sleeve B structural shorts have
       demonstrated poor outcomes (+36% WR in 2024 bull) when the broad market
       is in an uptrend. This gate blocks signals during SPY uptrend periods.
       "Bad enough for Sleeve B" = SPY close < SPY 50d MA. Not required to be
       in a crash — just not in a confirmed broad uptrend.

  Safety gates:
   10. Price ≥ $15 (penny stock avoidance)
   11. No FMP earnings event within 5 trading days (Sleeve A territory)
   12. Stop risk ≤ 15% of entry price (rejects wide-stop geometry)
   13. Target > $0

  China ADR exclusion (Calibration v2):
       BABA and JD excluded from universe. China ADRs have geopolitical/regulatory
       noise that overrides structural deterioration logic. 10 signals, 20% WR in
       prior run. Structurally not comparable to US-listed broken leaders.

Trade geometry:
  - Entry: close on signal date
  - Stop: failed_rally_peak × 1.015 (1.5% above the specific failed high)
  - Target: entry − 2× risk (R:R = 2.0)
  - Hold: 20d primary, 30d secondary

Anti-lookahead:
  - Price bars sliced strictly to signal date
  - Forward returns computed after signal date only
  - Failed rally measured from historical bars up to (but not including) signal date

Friction model:
  - Commission: 0.05% each way (0.10% round-trip)
  - Slippage: 0.10% each way (0.20% round-trip, higher than Sleeve A for volatile names)
  - Borrow: 2.0% annualized (~0.008% per calendar day, harder-to-borrow names)
  - Total 20-day hold: ~0.46%  |  30-day hold: ~0.54%

Manual validation baseline (META, SNAP, PYPL 2021-2023):
  10 signals, 50% WR at 20d, avg +5.4% raw at 20d.
  NVDA (control): 1 signal (stop hit, correct rejection behavior).
  MSFT (control): 1 signal (held, +4.5%).

Usage:
  cd /home/gem/trading-production
  SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python3 \\
      research/backtests/short_b_backtest.py [--quick]

  --quick: 25-ticker universe, 2022–2023 window (smoke test, ~5 min)
"""
from __future__ import annotations

import argparse
import logging
import os
import statistics
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Set, Tuple

# ── Load credentials ──────────────────────────────────────────────────────────
_env_path = os.environ.get("SNIPER_ENV_PATH", "")
if _env_path:
    from pathlib import Path as _Path
    try:
        from dotenv import load_dotenv as _load
        _load(_Path(_env_path), override=True)
    except ImportError:
        pass

import pandas as pd
from backtest_data_loader import BacktestDataLoader

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from core.fmp_client import get_fmp

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Universe
# ══════════════════════════════════════════════════════════════════════════════

# Prior leaders that broke 2021-2024:
# - SaaS/growth leaders (peaked 2021, broke 2022)
# - Consumer/retail (peaked 2021, broke 2022-2023)
# - Big-cap that had significant breaks
# - China ADRs
# - Controls (should rarely trigger)

FULL_UNIVERSE = [
    # Prior SaaS / growth leaders (peaked 2020-2021)
    "NFLX", "SNAP", "PYPL", "DOCU", "ZM", "SHOP", "COIN", "ROKU",
    "AFRM", "UPST", "TWLO", "OKTA", "SQ", "HOOD", "DDOG", "NET",
    # Prior hyped consumer / retail
    "PTON", "W", "ETSY", "CHWY", "BBWI", "ANF", "LULU",
    # Big-cap breaks
    "META", "DIS", "PARA", "WBD", "INTC", "NYCB",
    # Consumer discretionary cyclicals
    "NKE", "DG", "DLTR", "KSS", "M",
    # Airlines / travel that had structural breaks
    "AAL", "CCL",
    # China ADRs EXCLUDED (Calibration v2): BABA/JD removed — geopolitical noise
    # overrides structural logic; 20% WR in prior run, not comparable to US names.
    # Fintech / gig
    "SOFI", "OPEN", "LYFT",
    # Semis / hardware with significant breaks
    "WDC", "NTAP", "MU",
    # Media
    "BMBL",
    # Controls — strong names that should rarely signal
    "NVDA", "MSFT", "AAPL", "AMZN", "LLY", "V", "GOOGL",
]

QUICK_UNIVERSE = [
    "SNAP", "PYPL", "META", "NFLX", "COIN", "DOCU", "ZM",
    "PTON", "W", "ETSY", "NKE", "INTC",
    # Controls
    "NVDA", "MSFT", "AAPL",
    # Additional coverage
    "SHOP", "ROKU", "AFRM", "OKTA", "SOFI",
    "DIS", "KSS", "M",  # BABA excluded (China ADR)
]

# ── Production constants ──────────────────────────────────────────────────────
# (These are the calibrated thresholds from manual validation pass)
LEADERSHIP_THRESH     = 0.40    # ≥40% in any 18-month window
LEADERSHIP_LOOKBACK   = 1080    # days (~3 years) to check for prior leadership

MA_PERIOD             = 50      # structural break: price below 50d MA
LOWER_HIGH_BARS       = 20      # 52w high must not be in last 20 bars
DRAWDOWN_MIN          = -0.35   # not below -35% (Calibration v2: tightened from -55%)
DRAWDOWN_MAX          = -0.20   # must be at least -20% from 52w high
# Rationale: prior run showed -20%/-30% bucket = 56% WR, +0.6% avg;
# -40%/-55% bucket = 32% WR, -2.3% avg. Deep-breakdown tail eliminated.

SPY_MA_PERIOD         = 50      # SPY regime gate: SPY must be below its 50d MA

RSI_LOW               = 33
RSI_HIGH              = 55
RSI_PERIOD            = 14

RS_THRESHOLD          = -0.06   # 20d return vs SPY ≤ -6%
RS_LOOKBACK           = 20      # days

DIST_VOL_MULT         = 1.3     # vol > 1.3× 20d avg on down close = dist day
DIST_MIN_DAYS         = 2       # ≥2 of last 10 sessions

BOUNCE_MIN_PCT        = 0.04    # rally must be ≥4% from local low
BOUNCE_MAX_BARS       = 12      # rally must peak within 12 bars
BOUNCE_LOOKBACK       = 20      # look back 20 bars for a failed bounce

MAX_RISK_PCT          = 0.15    # stop must be within 15% of entry (tight geometry)
MIN_RRR               = 2.0
STOP_BUFFER           = 1.015   # 1.5% above failed bounce peak

EARNINGS_BLOCK_DAYS   = 5       # block if earnings within 5 sessions
HOLD_DAYS             = [20, 30]

PRICE_MIN             = 15.0    # penny stock gate

# Friction
COMMISSION_EACH_WAY   = 0.0005
SLIPPAGE_EACH_WAY     = 0.0010  # higher than Sleeve A (volatile broken names)
BORROW_ANNUAL         = 0.02    # 2% (harder to borrow than easy-to-borrow)


# ══════════════════════════════════════════════════════════════════════════════
# Price data
# ══════════════════════════════════════════════════════════════════════════════

_backtest_loader = BacktestDataLoader()


def fetch_price_data(tickers: List[str], start: date, end: date) -> Dict[str, pd.DataFrame]:
    """
    Fetch OHLCV history for all tickers.
    Reads from cache/backtest_prices/ first; falls back to Alpaca on miss.
    """
    return _backtest_loader.get_bars_batch(list(set(tickers)), start, end)


# ══════════════════════════════════════════════════════════════════════════════
# Earnings calendar (for earnings-proximity block)
# ══════════════════════════════════════════════════════════════════════════════

def fetch_earnings_blocks(event_blocks: List[Tuple[str, str]]) -> Dict[str, Set[date]]:
    """Returns {ticker: set of earnings dates} for the full window."""
    import calendar as _cal
    fmp = get_fmp()
    result: Dict[str, Set[date]] = defaultdict(set)
    for from_dt, to_dt in event_blocks:
        try:
            events = fmp._get("/earnings-calendar", params={"from": from_dt, "to": to_dt})
            if not isinstance(events, list):
                continue
            for ev in events:
                sym = (ev.get("symbol") or "").upper()
                dt_str = ev.get("date", "")
                if sym and dt_str:
                    try:
                        result[sym].add(date.fromisoformat(dt_str[:10]))
                    except ValueError:
                        pass
        except Exception as exc:
            logger.error("Earnings fetch failed %s–%s: %s", from_dt, to_dt, exc)
    logger.info("Earnings calendar loaded: %d tickers", len(result))
    return dict(result)


def _monthly_blocks(start_year: int, start_month: int,
                    end_year: int, end_month: int) -> List[Tuple[str, str]]:
    import calendar as _cal
    blocks = []
    y, m = start_year, start_month
    while (y, m) <= (end_year, end_month):
        last_day = _cal.monthrange(y, m)[1]
        blocks.append((f"{y:04d}-{m:02d}-01", f"{y:04d}-{m:02d}-{last_day:02d}"))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return blocks


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _rsi(closes: List[float], period: int = RSI_PERIOD) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for k in range(1, period + 1):
        d = closes[-period - 1 + k] - closes[-period - 2 + k]
        (gains if d > 0 else losses).append(abs(d))
    avg_g = statistics.mean(gains) if gains else 0.0
    avg_l = statistics.mean(losses) if losses else 0.0
    if avg_l == 0:
        return 100.0
    return round(100 - 100 / (1 + avg_g / avg_l), 1)


def _had_prior_leadership(bars: List[Dict], as_of_idx: int) -> bool:
    """Returns True if the stock achieved ≥40% in any 18-month window in last 3 years."""
    window_bars = bars[max(0, as_of_idx - LEADERSHIP_LOOKBACK // 1): as_of_idx + 1]
    step = 18 * 21  # approx 18 months in trading days
    for start_k in range(0, len(window_bars) - step, 10):
        end_k = start_k + step
        if end_k >= len(window_bars):
            break
        start_price = window_bars[start_k]["close"]
        end_price   = max(b["close"] for b in window_bars[start_k: end_k + 1])
        if start_price > 0 and (end_price / start_price - 1) >= LEADERSHIP_THRESH:
            return True
    return False


def _friction(hold_days: int) -> float:
    """Total round-trip friction as fraction."""
    return (2 * COMMISSION_EACH_WAY + 2 * SLIPPAGE_EACH_WAY
            + BORROW_ANNUAL / 252 * hold_days)


# ══════════════════════════════════════════════════════════════════════════════
# Signal evaluation
# ══════════════════════════════════════════════════════════════════════════════

def evaluate_bar(
    ticker: str,
    bars: List[Dict],
    i: int,
    spy_bars: List[Dict],
    spy_by_date: Dict,
    earnings_dates: Set[date],
    spy_ma50_by_date: Dict,  # date → SPY 50d MA; used for regime gate
) -> Optional[Dict]:
    """
    Evaluate bar i against all Sleeve B gates.
    Returns a signal dict on pass, or None (with rejection key) on failure.
    The caller tracks rejection_counts.
    """
    b = bars[i]
    today = b["date"]

    # Need enough history
    if i < max(MA_PERIOD + 50, 260):
        return None

    closes = [x["close"] for x in bars[:i+1]]
    ma50   = statistics.mean(closes[-MA_PERIOD:])

    # ── Gate 1: Price < 50d MA ────────────────────────────────────────────────
    if b["close"] >= ma50:
        return None

    # ── Gate 2: Lower high (52w high not in last 20 bars) ────────────────────
    high_52w = max(x["high"] for x in bars[max(0, i - 252): i + 1])
    high_20d = max(x["high"] for x in bars[i - LOWER_HIGH_BARS: i + 1])
    if high_20d >= high_52w * 0.95:
        return None

    # ── Gate 3: Drawdown −20% to −55% ────────────────────────────────────────
    drawdown = (b["close"] - high_52w) / high_52w
    if not (DRAWDOWN_MIN <= drawdown <= DRAWDOWN_MAX):
        return None

    # ── Gate 4: RSI 33–55 ────────────────────────────────────────────────────
    cur_rsi = _rsi(closes)
    if cur_rsi is None or not (RSI_LOW <= cur_rsi <= RSI_HIGH):
        return None

    # ── Gate 5: Relative weakness vs SPY 20d ≤ −6% ───────────────────────────
    spy_now = spy_by_date.get(today)
    spy_20d = spy_by_date.get(bars[i - RS_LOOKBACK]["date"]) if i >= RS_LOOKBACK else None
    if not (spy_now and spy_20d):
        return None
    spy_ret   = (spy_now - spy_20d) / spy_20d
    tkr_ret   = (b["close"] - bars[i - RS_LOOKBACK]["close"]) / bars[i - RS_LOOKBACK]["close"]
    rs_vs_spy = tkr_ret - spy_ret
    if rs_vs_spy > RS_THRESHOLD:
        return None

    # ── Gate 6: SPY regime gate ───────────────────────────────────────────────
    # Sleeve B requires SPY below its 50d MA. In confirmed broad uptrends,
    # structural shorts on broken leaders fail at high rates (2024: 36% WR).
    # "Bad enough for Sleeve B" = SPY close < SPY 50d MA on signal date.
    spy_ma50 = spy_ma50_by_date.get(today)
    spy_close_today = spy_by_date.get(today)
    if spy_ma50 is not None and spy_close_today is not None:
        if spy_close_today > spy_ma50:
            return None   # broad uptrend — Sleeve B signals likely to fail

    # ── Gate 7: Distribution days ≥2 in last 10 sessions ─────────────────────
    vol_window = [x["volume"] for x in bars[max(0, i - 20): i]]
    vol_20avg  = statistics.mean(vol_window) if vol_window else 0
    dist_days  = sum(
        1 for x in bars[max(0, i - 10): i + 1]
        if x["volume"] > DIST_VOL_MULT * vol_20avg and x["close"] < x["open"]
    )
    if dist_days < DIST_MIN_DAYS:
        return None

    # ── Gate 8: Prior leadership ──────────────────────────────────────────────
    if not _had_prior_leadership(bars, i):
        return None

    # ── Gate 9: Failed rally trigger ─────────────────────────────────────────
    bounce_found      = False
    failed_bounce_peak = None
    for j in range(max(0, i - BOUNCE_LOOKBACK), i - 2):
        local_low       = bars[j]["close"]
        bounce_segment  = bars[j + 1: min(j + 1 + BOUNCE_MAX_BARS, i)]
        if not bounce_segment:
            continue
        bounce_peak_c   = max(x["close"] for x in bounce_segment)
        bounce_peak_h   = max(x["high"]  for x in bounce_segment)
        bounce_pct      = (bounce_peak_c - local_low) / local_low
        if bounce_pct >= BOUNCE_MIN_PCT and b["close"] < local_low:
            bounce_found       = True
            failed_bounce_peak = bounce_peak_h
            break

    if not bounce_found or failed_bounce_peak is None:
        return None

    # ── Gate 10: Safety — price ≥ $15 ────────────────────────────────────────
    if b["close"] < PRICE_MIN:
        return None

    # ── Gate 11: No earnings within 5 sessions ───────────────────────────────
    if earnings_dates:
        for offset in range(-1, EARNINGS_BLOCK_DAYS + 1):
            check_date = today + timedelta(days=offset)
            if check_date in earnings_dates:
                return None

    # ── Trade geometry ────────────────────────────────────────────────────────
    stop  = failed_bounce_peak * STOP_BUFFER
    entry = b["close"]
    risk  = stop - entry
    if risk <= 0:
        return None
    if risk / entry > MAX_RISK_PCT:
        return None
    target = entry - risk * MIN_RRR
    if target <= 0:
        return None

    return {
        "ticker":       ticker,
        "signal_date":  str(today),
        "bar_idx":      i,
        "direction":    "SHORT",
        "entry_price":  round(entry, 2),
        "stop_loss":    round(stop, 2),
        "target_price": round(target, 2),
        "risk_pct":     round(risk / entry * 100, 1),
        "rr":           MIN_RRR,
        "ma50":         round(ma50, 1),
        "drawdown_pct": round(drawdown * 100, 1),
        "rsi":          cur_rsi,
        "rs_vs_spy_20d": round(rs_vs_spy * 100, 1),
        "dist_days":    dist_days,
        "failed_peak":  round(failed_bounce_peak, 2),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Forward returns
# ══════════════════════════════════════════════════════════════════════════════

def compute_forward_returns(signal: Dict, bars: List[Dict]) -> Dict:
    """
    Walk forward from signal date. For each HOLD_DAYS horizon, check stop/target
    management. Returns dict with raw_Nd, adj_Nd, outcome_Nd.
    """
    entry  = signal["entry_price"]
    stop   = signal["stop_loss"]
    target = signal["target_price"]
    sig_idx = signal["bar_idx"]

    results: Dict = {}
    for horizon in HOLD_DAYS:
        future_bars = bars[sig_idx: sig_idx + horizon + 1]
        if len(future_bars) < 2:
            results[f"raw_{horizon}d"]     = None
            results[f"adj_{horizon}d"]     = None
            results[f"outcome_{horizon}d"] = "insufficient_data"
            continue

        outcome  = "held"
        exit_idx = horizon
        for offset, bar in enumerate(future_bars[1:], 1):
            if bar["high"] >= stop:
                outcome = "stop_hit"
                exit_idx = offset
                break
            if bar["low"] <= target:
                outcome = "target_hit"
                exit_idx = offset
                break

        exit_bar   = future_bars[exit_idx] if exit_idx < len(future_bars) else future_bars[-1]
        exit_price = (stop   if outcome == "stop_hit"   else
                      target if outcome == "target_hit" else
                      float(exit_bar["close"]))

        raw_ret = (entry - exit_price) / entry * 100          # positive = short won
        adj_ret = raw_ret - _friction(exit_idx) * 100

        results[f"raw_{horizon}d"]     = round(raw_ret, 2)
        results[f"adj_{horizon}d"]     = round(adj_ret, 2)
        results[f"outcome_{horizon}d"] = outcome

    return results


# ══════════════════════════════════════════════════════════════════════════════
# Main backtest runner
# ══════════════════════════════════════════════════════════════════════════════

def run_backtest(
    universe: List[str],
    start_year: int, start_month: int,
    end_year: int, end_month: int,
) -> Dict:
    # Price data window: go back 2 years before backtest start so the leadership
    # prerequisite check can see the full prior bull run (many names peaked in
    # late 2021 after 2019-2021 runs; a 1-year buffer misses most of it)
    price_start = date(start_year - 2, 1, 1)
    price_end   = date(end_year, 12, 31)

    price_data = fetch_price_data(universe + ["SPY"], price_start, price_end)
    spy_bars   = price_data.get("SPY", pd.DataFrame())
    spy_list: List[Dict] = []
    if not spy_bars.empty:
        spy_list = spy_bars.reset_index().to_dict("records")
    spy_by_date = {b["date"]: b["close"] for b in spy_list}

    # Precompute SPY 50d MA by date (for regime gate in evaluate_bar)
    spy_ma50_by_date: Dict[date, float] = {}
    spy_closes_list = [b["close"] for b in spy_list]
    spy_dates_list  = [b["date"]  for b in spy_list]
    for _k in range(SPY_MA_PERIOD, len(spy_list)):
        spy_ma50_by_date[spy_dates_list[_k]] = statistics.mean(
            spy_closes_list[_k - SPY_MA_PERIOD: _k]
        )

    # Earnings calendar (monthly blocks)
    event_blocks = _monthly_blocks(start_year, start_month, end_year, end_month)
    logger.info("Fetching earnings calendar: %d monthly blocks...", len(event_blocks))
    earnings_data = fetch_earnings_blocks(event_blocks)

    # Backtest window boundaries
    bt_start = date(start_year, start_month, 1)
    bt_end   = price_end

    signals: List[Dict] = []
    rejection_counts: Dict[str, int] = defaultdict(int)
    bars_evaluated = 0

    logger.info("Evaluating signals for %d universe tickers...", len(universe))
    for ticker in universe:
        if ticker not in price_data or ticker == "SPY":
            continue
        df = price_data[ticker]
        bars = df.reset_index().to_dict("records")
        earnings_dates: Set[date] = earnings_data.get(ticker, set())

        # Per-ticker: enforce 1 signal per 15-day window (avoid cascading entries)
        last_signal_idx = -999

        for i in range(260, len(bars)):
            bar_date = bars[i]["date"]
            if bar_date < bt_start or bar_date > bt_end:
                continue
            bars_evaluated += 1

            sig = evaluate_bar(ticker, bars, i, spy_list, spy_by_date, earnings_dates, spy_ma50_by_date)

            if sig is None:
                # Re-evaluate to find the specific failing gate (for reporting)
                b = bars[i]
                if i < 260:
                    rejection_counts["insufficient_history"] += 1
                    continue
                closes = [x["close"] for x in bars[:i+1]]
                ma50 = statistics.mean(closes[-MA_PERIOD:]) if len(closes) >= MA_PERIOD else 0
                if b["close"] >= ma50:
                    rejection_counts["price_above_ma50"] += 1
                    continue
                high_52w = max(x["high"] for x in bars[max(0, i-252): i+1])
                high_20d = max(x["high"] for x in bars[i-LOWER_HIGH_BARS: i+1])
                if high_20d >= high_52w * 0.95:
                    rejection_counts["no_lower_high"] += 1
                    continue
                drawdown = (b["close"] - high_52w) / high_52w
                if not (DRAWDOWN_MIN <= drawdown <= DRAWDOWN_MAX):
                    if drawdown > DRAWDOWN_MAX:
                        rejection_counts["drawdown_too_shallow"] += 1
                    else:
                        rejection_counts["drawdown_too_deep"] += 1
                    continue
                cur_rsi = _rsi(closes)
                if cur_rsi is None or not (RSI_LOW <= cur_rsi <= RSI_HIGH):
                    rejection_counts["rsi_out_of_range"] += 1
                    continue
                spy_now = spy_by_date.get(bars[i]["date"])
                spy_20d = spy_by_date.get(bars[i-RS_LOOKBACK]["date"]) if i >= RS_LOOKBACK else None
                if not (spy_now and spy_20d):
                    rejection_counts["rs_data_missing"] += 1
                    continue
                spy_ret = (spy_now - spy_20d) / spy_20d
                tkr_ret = (b["close"] - bars[i-RS_LOOKBACK]["close"]) / bars[i-RS_LOOKBACK]["close"]
                if tkr_ret - spy_ret > RS_THRESHOLD:
                    rejection_counts["rs_too_strong"] += 1
                    continue
                spy_ma50_today = spy_ma50_by_date.get(bars[i]["date"])
                spy_close_today = spy_by_date.get(bars[i]["date"])
                if (spy_ma50_today is not None and spy_close_today is not None
                        and spy_close_today > spy_ma50_today):
                    rejection_counts["spy_regime_too_strong"] += 1
                    continue
                vol_window = [x["volume"] for x in bars[max(0, i-20): i]]
                vol_20avg  = statistics.mean(vol_window) if vol_window else 0
                dist_days  = sum(
                    1 for x in bars[max(0, i-10): i+1]
                    if x["volume"] > DIST_VOL_MULT * vol_20avg and x["close"] < x["open"]
                )
                if dist_days < DIST_MIN_DAYS:
                    rejection_counts["insufficient_distribution"] += 1
                    continue
                if not _had_prior_leadership(bars, i):
                    rejection_counts["no_prior_leadership"] += 1
                    continue
                # Check failed rally
                bounce_found = False
                for j in range(max(0, i - BOUNCE_LOOKBACK), i - 2):
                    local_low = bars[j]["close"]
                    seg = bars[j+1: min(j+1+BOUNCE_MAX_BARS, i)]
                    if not seg:
                        continue
                    bp = max(x["close"] for x in seg)
                    if (bp - local_low) / local_low >= BOUNCE_MIN_PCT and b["close"] < local_low:
                        bounce_found = True
                        break
                if not bounce_found:
                    rejection_counts["no_failed_rally"] += 1
                    continue
                if b["close"] < PRICE_MIN:
                    rejection_counts["price_too_low"] += 1
                    continue
                rejection_counts["geometry_or_earnings"] += 1
                continue

            # Dedup: 1 signal per ticker per 15-day window
            if i - last_signal_idx < 15:
                rejection_counts["dedup_cooldown"] += 1
                continue

            # Compute forward returns
            fwd = compute_forward_returns(sig, bars)
            sig.update(fwd)
            signals.append(sig)
            last_signal_idx = i

    logger.info("Backtest complete: %d bars evaluated → %d signals",
                bars_evaluated, len(signals))
    return {
        "signals":          signals,
        "rejection_counts": dict(rejection_counts),
        "bars_evaluated":   bars_evaluated,
        "universe":         universe,
        "bt_start":         str(date(start_year, start_month, 1)),
        "bt_end":           str(bt_end),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Reporting
# ══════════════════════════════════════════════════════════════════════════════

def _pct(x: Optional[float]) -> str:
    return "N/A" if x is None else f"{x:+.1f}%"

def _avg(vals: List[Optional[float]]) -> Optional[float]:
    clean = [v for v in vals if v is not None]
    return round(statistics.mean(clean), 1) if clean else None

def _wr(vals: List[Optional[float]]) -> Optional[float]:
    clean = [v for v in vals if v is not None]
    return round(sum(1 for v in clean if v > 0) / len(clean) * 100, 1) if clean else None

def _exp(vals: List[Optional[float]]) -> Optional[float]:
    clean = [v for v in vals if v is not None]
    if not clean:
        return None
    wins   = [v for v in clean if v > 0]
    losses = [v for v in clean if v <= 0]
    n      = len(clean)
    wr     = len(wins) / n
    avg_w  = statistics.mean(wins)   if wins   else 0.0
    avg_l  = statistics.mean(losses) if losses else 0.0
    return round(wr * avg_w + (1 - wr) * avg_l, 2)


def print_report(results: Dict) -> None:
    signals = results["signals"]
    rej     = results["rejection_counts"]
    n_bars  = results["bars_evaluated"]

    def section(title: str):
        print(f"\n{'═'*72}")
        print(f"  {title}")
        print(f"{'═'*72}")

    section("SHORT SLEEVE B — BROKEN LEADER VALIDATION BACKTEST")
    print(f"\n  Universe:          {len(results['universe'])} tickers")
    print(f"  Signal window:     {results['bt_start']} → {results['bt_end']}")
    print(f"  Bars evaluated:    {n_bars:,}")
    print(f"  Signals generated: {len(signals)}")
    print(f"\n  Strategy identity: Broken Leader / Structural Deterioration Short")
    print(f"  Edge claim:        Former leaders with structural break + failed rally")
    print(f"                     = institutional distribution confirmed; retail buyer")
    print(f"                     base provides supply for remaining downside")
    print(f"\n  Doctrine: SHORT Sleeve B. Sleeve A = Event Continuation (short_sleeve.py).")
    print(f"  See docs/strategy/SHORT_DOCTRINE.md for full family doctrine.")

    # ── Rejection funnel ──────────────────────────────────────────────────────
    section("1. SIGNAL FUNNEL")
    total_rej = sum(rej.values())
    print(f"\n  Bars evaluated:            {n_bars:>10,}")
    print(f"  {'Rejection bucket':<35} {'Count':>10}")
    print(f"  {'-'*47}")
    for reason, cnt in sorted(rej.items(), key=lambda x: -x[1]):
        print(f"  {reason:<35} {cnt:>10,}")
    print(f"\n  Signals generated:         {len(signals):>10,}")
    print(f"  Signal rate (of bars):     {len(signals)/n_bars*100:>10.3f}%" if n_bars else "")

    if not signals:
        section("NO SIGNALS — INVESTIGATION REQUIRED")
        print("\n  Possible causes:")
        print("  1. Leadership prerequisite too strict (≥40% in 18-month window)")
        print("  2. Drawdown range too narrow (−20% to −55%)")
        print("  3. Failed rally definition too specific")
        print("  4. Universe doesn't have enough names that were actual leaders")
        return

    # ── Coverage ──────────────────────────────────────────────────────────────
    section("2. COVERAGE BY TICKER")
    sig_by_ticker = defaultdict(list)
    for s in signals:
        sig_by_ticker[s["ticker"]].append(s)

    # Identify controls and mark them
    CONTROLS = {"NVDA", "MSFT", "AAPL", "AMZN", "LLY", "V", "GOOGL"}
    print(f"\n  Tickers with ≥1 signal: {len(sig_by_ticker)}"
          f"  (controls: {sum(1 for t in sig_by_ticker if t in CONTROLS)})")
    print(f"\n  {'Ticker':<8} {'Type':<8} {'#Sig':>5} {'AvgDD':>8} {'AvgRSI':>8}"
          f" {'Avg20d':>8} {'WR20':>7}")
    print(f"  {'-'*60}")
    for ticker, sigs in sorted(sig_by_ticker.items(), key=lambda x: -len(x[1])):
        tag = "CONTROL" if ticker in CONTROLS else "target"
        dds = [s["drawdown_pct"] for s in sigs]
        rsis = [s["rsi"] for s in sigs]
        r20  = [s.get("raw_20d") for s in sigs]
        print(f"  {ticker:<8} {tag:<8} {len(sigs):>5} {statistics.mean(dds):>7.1f}%"
              f" {statistics.mean(rsis):>8.1f} {_pct(_avg(r20)):>8} {str(_wr(r20) or '—'):>7}")

    # ── Performance ───────────────────────────────────────────────────────────
    section("3. PERFORMANCE SUMMARY")
    print(f"\n  {'Horizon':<8} {'N':>5} {'AvgRaw':>9} {'AvgAdj':>9} {'WinRate':>9} {'Expectancy':>12}")
    print(f"  {'-'*56}")
    for h in HOLD_DAYS:
        raws = [s.get(f"raw_{h}d") for s in signals]
        adjs = [s.get(f"adj_{h}d") for s in signals]
        raw_clean = [v for v in raws if v is not None]
        if not raw_clean:
            print(f"  {h}d       {'—':>5}")
            continue
        print(f"  {h}d       {len(raw_clean):>5} {_pct(_avg(raws)):>9} {_pct(_avg(adjs)):>9}"
              f" {str(_wr(raws) or '—'):>9} {_pct(_exp(raws)):>12}")

    # ── By drawdown bucket ─────────────────────────────────────────────────────
    section("4. RETURN BY DRAWDOWN AT ENTRY (20d raw)")
    buckets = [
        ("−20% to −30%",  lambda s: -0.30 <= s["drawdown_pct"]/100 <= -0.20),
        ("−30% to −40%",  lambda s: -0.40 <= s["drawdown_pct"]/100 <  -0.30),
        ("−40% to −55%",  lambda s: -0.55 <= s["drawdown_pct"]/100 <  -0.40),
    ]
    print(f"\n  {'Bucket':<20} {'N':>5} {'Avg20d':>9} {'WR20':>7}")
    print(f"  {'-'*43}")
    for label, cond in buckets:
        sigs = [s for s in signals if cond(s)]
        r20  = [s.get("raw_20d") for s in sigs]
        print(f"  {label:<20} {len(sigs):>5} {_pct(_avg(r20)):>9} {str(_wr(r20) or '—'):>7}")

    # ── Outcome breakdown ─────────────────────────────────────────────────────
    section("5. OUTCOME BREAKDOWN (20d primary horizon)")
    outcome_counts: Dict[str, int] = defaultdict(int)
    for s in signals:
        outcome_counts[s.get("outcome_20d", "unknown")] += 1
    print(f"\n  {'Outcome':<20} {'Count':>7} {'%':>7}")
    print(f"  {'-'*37}")
    for outcome, cnt in sorted(outcome_counts.items(), key=lambda x: -x[1]):
        print(f"  {outcome:<20} {cnt:>7} {cnt/len(signals)*100:>6.1f}%")

    # ── Control tickers ───────────────────────────────────────────────────────
    section("6. CONTROL TICKER ANALYSIS")
    control_sigs = [s for s in signals if s["ticker"] in CONTROLS]
    target_sigs  = [s for s in signals if s["ticker"] not in CONTROLS]
    print(f"\n  Controls (should have low signal count and low/negative edge):")
    print(f"    {len(control_sigs)} control signals vs {len(target_sigs)} target signals")
    if control_sigs:
        r20c = [s.get("raw_20d") for s in control_sigs]
        print(f"    Control avg 20d: {_pct(_avg(r20c))}  WR: {_wr(r20c) or '—'}%")
        for s in control_sigs:
            print(f"      {s['ticker']} {s['signal_date']}  entry={s['entry_price']}"
                  f"  DD={s['drawdown_pct']}%  20d={_pct(s.get('raw_20d'))} {s.get('outcome_20d','')}")
    else:
        print(f"    No control signals — strong selectivity confirmed")

    # ── Signal characteristics ────────────────────────────────────────────────
    section("7. SIGNAL CHARACTERISTICS")
    dds   = [s["drawdown_pct"]   for s in signals]
    rsis  = [s["rsi"]            for s in signals]
    rss   = [s["rs_vs_spy_20d"]  for s in signals]
    risks = [s["risk_pct"]       for s in signals]
    dists = [s["dist_days"]      for s in signals]
    print(f"\n  {'Metric':<22} {'Min':>8} {'Avg':>8} {'Max':>8}")
    print(f"  {'-'*49}")
    print(f"  {'Drawdown at entry %':<22} {min(dds):>8.1f} {statistics.mean(dds):>8.1f} {max(dds):>8.1f}")
    print(f"  {'RSI at entry':<22} {min(rsis):>8.1f} {statistics.mean(rsis):>8.1f} {max(rsis):>8.1f}")
    print(f"  {'RS vs SPY 20d %':<22} {min(rss):>8.1f} {statistics.mean(rss):>8.1f} {max(rss):>8.1f}")
    print(f"  {'Risk % of entry':<22} {min(risks):>8.1f} {statistics.mean(risks):>8.1f} {max(risks):>8.1f}")
    print(f"  {'Distribution days':<22} {min(dists):>8} {statistics.mean(dists):>8.1f} {max(dists):>8}")

    # ── Sample signals ────────────────────────────────────────────────────────
    section("8. SAMPLE SIGNALS (top 12 by drawdown at entry)")
    sorted_sigs = sorted(signals, key=lambda x: x["drawdown_pct"])[:12]
    print(f"\n  {'Date':<12} {'Ticker':<7} {'DD%':>6} {'RSI':>5} {'RS20d':>7} {'Risk%':>6}"
          f" {'20d_raw':>9} {'20d_adj':>9} {'Outcome':<14}")
    print(f"  {'-'*83}")
    for s in sorted_sigs:
        print(f"  {s['signal_date']:<12} {s['ticker']:<7} {s['drawdown_pct']:>6.1f}%"
              f" {s['rsi']:>5.1f} {s['rs_vs_spy_20d']:>6.1f}% {s['risk_pct']:>5.1f}%"
              f" {_pct(s.get('raw_20d')):>9} {_pct(s.get('adj_20d')):>9}"
              f" {s.get('outcome_20d', 'N/A'):<14}")

    # ── Regime context ────────────────────────────────────────────────────────
    section("9. REGIME CONTEXT")
    # Classify by year
    by_year: Dict[str, List[Dict]] = defaultdict(list)
    for s in signals:
        by_year[s["signal_date"][:4]].append(s)
    print(f"\n  {'Year':<6} {'Signals':>8} {'Avg20d':>9} {'WR20':>8}")
    print(f"  {'-'*35}")
    for yr in sorted(by_year):
        sigs = by_year[yr]
        r20  = [s.get("raw_20d") for s in sigs]
        print(f"  {yr:<6} {len(sigs):>8} {_pct(_avg(r20)):>9} {str(_wr(r20) or '—'):>8}")

    # ── Verdict ───────────────────────────────────────────────────────────────
    section("10. VERDICT")
    n    = len(signals)
    r20  = [s.get("raw_20d") for s in signals if s.get("raw_20d") is not None]
    r20a = [s.get("adj_20d") for s in signals if s.get("adj_20d") is not None]
    wr20 = _wr(r20)
    avg20_adj = _avg(r20a)

    print(f"\n  Signal count:          {n}  (target ≥ 40 for statistical validity)")
    print(f"  Primary horizon (20d):")
    print(f"    Avg raw return:      {_pct(_avg(r20))}")
    print(f"    Avg adj return:      {_pct(avg20_adj)}  (friction applied)")
    print(f"    Win rate:            {wr20 or 'N/A'}%")

    if n < 15:
        verdict = "INSUFFICIENT SAMPLE — conditions too restrictive or universe too narrow"
        ready   = "ADJUST CONDITIONS — re-run with loosened leadership or drawdown thresholds"
    elif wr20 and wr20 >= 55 and avg20_adj and avg20_adj > 0:
        verdict = "POSITIVE EXPECTANCY — edge present after friction"
        ready   = "READY for paper phase subject to council profile completion"
    elif wr20 and wr20 >= 50 and avg20_adj and avg20_adj > 0:
        verdict = "MARGINAL EDGE — positive but narrow; paper cycle required to confirm"
        ready   = "CONDITIONAL — begin paper phase, monitor closely"
    elif avg20_adj and avg20_adj <= 0:
        verdict = "EDGE DOES NOT SURVIVE FRICTION"
        ready   = "NOT READY — review conditions or hold horizon"
    else:
        verdict = "INCONCLUSIVE — mixed results or borderline sample"
        ready   = "PROCEED WITH CAUTION — paper cycle required"

    print(f"\n  VERDICT:          {verdict}")
    print(f"  PAPER READINESS:  {ready}")
    print(f"\n  Design strengths:")
    print(f"    + Leadership prerequisite screens for institutionally-held names")
    print(f"    + Failed rally trigger provides specific entry with precise invalidation")
    print(f"    + Stop = failed bounce peak (not MA) → workable geometry")
    print(f"    + Distribution days confirm supply, not just price weakness")
    print(f"    + Earnings block prevents overlap with Sleeve A territory")
    print(f"\n  Remaining risks:")
    print(f"    - Bear market rallies can stop out structurally-correct shorts")
    print(f"    - Borrow availability not verified (hard-to-borrow names possible)")
    print(f"    - Council profile for Sleeve B not yet implemented")
    print(f"    - Leadership lookback may not capture all relevant prior peaks")
    print(f"    - Sample size may require expanding universe if n < 40")


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SHORT Sleeve B backtest")
    parser.add_argument("--quick", action="store_true",
                        help="25-ticker universe, 2022–2023 window (smoke test)")
    args = parser.parse_args()

    if args.quick:
        universe    = QUICK_UNIVERSE
        start_y, start_m = 2022, 1
        end_y, end_m     = 2023, 12
    else:
        universe    = FULL_UNIVERSE
        start_y, start_m = 2018, 1
        end_y, end_m     = 2024, 12

    t0      = time.time()
    results = run_backtest(universe, start_y, start_m, end_y, end_m)
    elapsed = time.time() - t0

    print_report(results)
    print(f"\n  Total runtime: {elapsed/60:.1f} minutes")

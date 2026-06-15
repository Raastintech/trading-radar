"""
research/backtests/short_v1_backtest.py — SHORT strategy v1 validation backtest.

Tests the production ShortSleeveScanner logic against 3 years of historical data
(2022–2024). Mirrors strategies/short_sleeve.py exactly: same gap threshold, same
volume spike requirement, same continuation logic, same stop/target geometry.

Anti-lookahead:
  • Price bars: strictly sliced to bars available on the signal date
  • Event dates: FMP /stable/earnings-calendar with historical date ranges
  • No future price information used at signal time

Event timing note:
  Earnings events are anchored to FMP's `date` field. For BMO (before-market-open)
  reports, this is the announcement day and the gap appears on that bar's open —
  correct. For AMC (after-market-close) reports, FMP records the announcement day
  but the gap appears the NEXT morning. The production scanner measures the gap on
  the bar AT event_date, which is pre-announcement for AMC events. This causes
  false negatives for AMC reporters. This backtest uses the same logic, so results
  are consistent with what live production would produce. The AMC/BMO fix is a
  future improvement that would increase signal count.

Friction model:
  Commission:  0.05% each way (0.10% round-trip)
  Slippage:    0.05% each way (0.10% round-trip)
  Borrow cost: 1.0% annualized ≈ 0.004% per calendar day (easy-to-borrow names)
  Total 10-day hold:   ~0.24%  |  20-day hold: ~0.28%

Usage:
  cd /home/gem/trading-production
  SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python3 \
      research/backtests/short_v1_backtest.py [--quick]

  --quick   : 20-ticker universe, 1-year window (smoke test)
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
from typing import Dict, List, Optional, Tuple

# ── Load credentials (SNIPER_ENV_PATH pattern) ────────────────────────────────
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
# Universe — tickers with historically high earnings reaction volatility
# Includes both likely-signal names AND controls (big tech) for calibration
# ══════════════════════════════════════════════════════════════════════════════

FULL_UNIVERSE = [
    # High-beta growth — frequent earnings misses 2022-2024
    "NFLX", "SNAP", "ROKU", "PYPL", "SQ", "SHOP", "DOCU", "ZM", "COIN",
    "RIVN", "PLTR", "HOOD", "AFRM", "UPST",
    # Consumer / retail — cyclical earnings risk
    "LULU", "NKE", "ETSY", "W", "BBWI", "ANF", "M", "KSS", "PTON",
    "CHWY", "DG", "FIVE", "DLTR",
    # Semis / tech hardware
    "INTC", "DELL", "WDC", "NTAP",
    # Media / entertainment
    "PARA", "WBD", "DIS",
    # Biotech
    "MRNA", "BNTX",
    # Airlines / travel
    "AAL", "UAL", "CCL",
    # Fintech / gig economy
    "OPEN", "SOFI", "NYCB", "LYFT", "UBER", "DASH", "BMBL",
    # China ADRs
    "BABA", "JD",
    # Social / ad-tech
    "PINS", "TTD",
    # Controls — large-cap quality (expect few signals; validates rejection logic)
    "MSFT", "AAPL", "AMZN", "META", "GOOGL", "NVDA", "V", "JPM", "LLY",
    # Cybersecurity
    "OKTA", "DDOG", "NET",
    # Consumer mid-cap
    "DUOL", "CELH", "ELF",
]

QUICK_UNIVERSE = [
    "NFLX", "SNAP", "ROKU", "PYPL", "LULU", "NKE",
    "INTC", "PARA", "AAL", "PTON", "MSFT", "AAPL",
    "AFRM", "UPST", "DOCU", "ZM", "DDOG", "COIN",
    "OKTA", "DIS",
]

# ── Production constants (mirrored from strategies/short_sleeve.py) ───────────
REACTION_MIN_PCT  = -3.0    # gap must be ≤ -3%
VOL_SPIKE         = 1.5     # ≥ 1.5× avg volume
MAX_LAG_SESSIONS  = 3       # continuation within 3 sessions of event
MIN_SCORE         = 55
MIN_RRR           = 2.0
HOLD_DAYS         = [5, 10, 20]    # forward return measurement windows

# ── Friction model ────────────────────────────────────────────────────────────
COMMISSION_EACH_WAY = 0.0005   # 0.05%
SLIPPAGE_EACH_WAY   = 0.0005   # 0.05%
BORROW_ANNUAL       = 0.01     # 1% annualized easy-to-borrow rate

# ── Event data: MONTHLY blocks to avoid FMP 4000-event-per-call cap ───────────
# FMP /stable/earnings-calendar caps results at ~4000 per request.
# January 2024 alone has ~2630 events, so 6-month blocks overflow and
# drop many tickers. Monthly blocks stay safely under the cap.

def _monthly_blocks(start_year: int, start_month: int,
                    end_year: int, end_month: int) -> List[Tuple[str, str]]:
    """Generate (from_dt, to_dt) monthly blocks inclusive of last day."""
    import calendar
    blocks = []
    y, m = start_year, start_month
    while (y, m) <= (end_year, end_month):
        last_day = calendar.monthrange(y, m)[1]
        from_dt = f"{y:04d}-{m:02d}-01"
        to_dt   = f"{y:04d}-{m:02d}-{last_day:02d}"
        blocks.append((from_dt, to_dt))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return blocks

# Full backtest: 2022-01 → 2024-09  (33 monthly API calls)
FULL_EVENT_BLOCKS  = _monthly_blocks(2022, 1, 2024, 9)
# Quick smoke test: 2023-01 → 2024-06  (18 monthly API calls)
QUICK_EVENT_BLOCKS = _monthly_blocks(2023, 1, 2024, 6)


# ══════════════════════════════════════════════════════════════════════════════
# Price data (Alpaca)
# ══════════════════════════════════════════════════════════════════════════════

_backtest_loader = BacktestDataLoader()


def fetch_price_data(tickers: List[str], start: date, end: date) -> Dict[str, pd.DataFrame]:
    """
    Fetch OHLCV history for all tickers.
    Reads from cache/backtest_prices/ first; falls back to Alpaca on miss.
    """
    return _backtest_loader.get_bars_batch(list(set(tickers)), start, end)


# ══════════════════════════════════════════════════════════════════════════════
# Earnings events (FMP historical calendar)
# ══════════════════════════════════════════════════════════════════════════════

def fetch_earnings_events(event_blocks: List[Tuple[str, str]]) -> Dict[str, List[Dict]]:
    """
    Returns {ticker: [event, ...]} for all earnings events in the date blocks.
    Uses FMP /stable/earnings-calendar with historical date ranges.
    """
    fmp = get_fmp()
    all_events: Dict[str, List[Dict]] = defaultdict(list)
    for from_dt, to_dt in event_blocks:
        try:
            events = fmp._get("/earnings-calendar", params={"from": from_dt, "to": to_dt})
            if not isinstance(events, list):
                logger.warning("Unexpected response for %s–%s: %s", from_dt, to_dt, type(events))
                continue
            for ev in events:
                sym = (ev.get("symbol") or "").upper()
                if sym:
                    all_events[sym].append(ev)
            logger.info("  Event block %s → %s: %d events", from_dt, to_dt, len(events))
        except Exception as exc:
            logger.error("Event fetch failed %s–%s: %s", from_dt, to_dt, exc)
    logger.info("Total earnings events: %d tickers, %d events",
                len(all_events), sum(len(v) for v in all_events.values()))
    return dict(all_events)


# ══════════════════════════════════════════════════════════════════════════════
# Signal evaluation (mirrors strategies/short_sleeve.py exactly)
# ══════════════════════════════════════════════════════════════════════════════

def _atr(bars: List[Dict], period: int = 14) -> float:
    if len(bars) < 2:
        return 0.0
    trs = []
    for i in range(1, min(len(bars), period + 1)):
        h, l, pc = bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return statistics.mean(trs) if trs else bars[-1]["close"] * 0.02


def _score(gap_pct: float, vol_ratio: float, lag: int, continuation: bool) -> int:
    s = 30
    s += min(30, int(abs(gap_pct) * 4))
    s += min(20, int((vol_ratio - 1.5) * 20))
    s += max(0, (MAX_LAG_SESSIONS - lag) * 5)
    if continuation:
        s += 15
    return max(0, min(100, s))


def evaluate_event(
    ticker: str,
    event: Dict,
    price_df: pd.DataFrame,
) -> Optional[Dict]:
    """
    Evaluate a single (ticker, event) pair against the production SHORT gates.
    Returns a signal dict on pass, or None on rejection.

    Anti-lookahead: uses only bars available up to the signal date.
    """
    event_date = str(event.get("date", ""))[:10]
    if not event_date:
        return None

    # All bars available (oldest-first list)
    all_bars = price_df.reset_index().rename(columns={"date": "date"}).to_dict("records")
    if len(all_bars) < 25:
        return None

    # Find the reaction bar: first bar with date >= event_date
    ed_idx = None
    for i, b in enumerate(all_bars):
        if str(b["date"])[:10] >= event_date:
            ed_idx = i
            break
    if ed_idx is None or ed_idx == 0:
        return None

    event_bar = all_bars[ed_idx]
    prev_bar  = all_bars[ed_idx - 1]

    # ── Gate 1: Gap reaction ─────────────────────────────────────────────────
    gap_pct = (event_bar["open"] - prev_bar["close"]) / prev_bar["close"] * 100
    if gap_pct > REACTION_MIN_PCT:
        return None     # gap not negative enough

    # ── Gate 2: Volume confirmation ──────────────────────────────────────────
    vol_window = all_bars[max(0, ed_idx - 20): ed_idx]
    vol_20avg  = statistics.mean(b["volume"] for b in vol_window) if vol_window else 0
    vol_ratio  = event_bar["volume"] / vol_20avg if vol_20avg > 0 else 0
    if vol_ratio < VOL_SPIKE:
        return None

    # ── Gate 3: Continuation within lag window ───────────────────────────────
    # Scan bars ed_idx to ed_idx + MAX_LAG_SESSIONS for first with continuation.
    # Continuation = close < event_bar["low"] (still pressing below event bar low).
    signal_bar = None
    for lag_offset in range(0, MAX_LAG_SESSIONS + 1):
        today_idx = ed_idx + lag_offset
        if today_idx >= len(all_bars):
            break
        today_bar = all_bars[today_idx]
        # Bars used for this scan: only up to today_idx (anti-lookahead)
        if today_bar["close"] < event_bar["low"]:
            signal_bar = today_bar
            lag = lag_offset
            break

    if signal_bar is None:
        return None

    # ── Bars available at signal time (for ATR, score) ───────────────────────
    bars_at_signal = all_bars[: (ed_idx + lag + 1)]

    # ── Trade geometry ────────────────────────────────────────────────────────
    entry  = signal_bar["close"]
    atr    = _atr(bars_at_signal[-14:])
    stop   = entry + atr * 1.5        # SHORT: stop is ABOVE entry
    target = entry - (stop - entry) * MIN_RRR
    rr     = round((entry - target) / (stop - entry), 2)
    if rr < MIN_RRR or target <= 0:
        return None

    score = _score(gap_pct, vol_ratio, lag, True)
    if score < MIN_SCORE:
        return None

    signal_date = str(signal_bar["date"])[:10]
    return {
        "ticker":       ticker,
        "direction":    "SHORT",
        "score":        score,
        "signal_date":  signal_date,
        "event_date":   event_date,
        "lag_sessions": lag,
        "gap_pct":      round(gap_pct, 2),
        "vol_ratio":    round(vol_ratio, 2),
        "entry_price":  round(entry, 2),
        "stop_loss":    round(stop, 2),
        "target_price": round(target, 2),
        "risk_reward":  rr,
        "atr":          round(atr, 2),
        "eps_estimate": event.get("epsEstimated"),
        "eps_actual":   event.get("epsActual"),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Forward return calculation (with stop/target management)
# ══════════════════════════════════════════════════════════════════════════════

def _friction(hold_days: int) -> float:
    """Total round-trip friction as a fraction (not %)."""
    rt_commission = 2 * COMMISSION_EACH_WAY
    rt_slippage   = 2 * SLIPPAGE_EACH_WAY
    borrow_daily  = BORROW_ANNUAL / 252
    return rt_commission + rt_slippage + borrow_daily * hold_days


def compute_forward_returns(
    signal: Dict,
    price_df: pd.DataFrame,
) -> Dict:
    """
    Compute forward returns at each HOLD_DAYS horizon with stop/target management.

    Returns: dict with keys raw_Nd, adj_Nd (friction-adjusted), outcome_Nd
    outcome_Nd: 'stop_hit' | 'target_hit' | 'held' | 'insufficient_data'
    """
    signal_date = signal["signal_date"]
    entry       = signal["entry_price"]
    stop        = signal["stop_loss"]
    target      = signal["target_price"]

    all_bars = price_df.reset_index().to_dict("records")
    # Find index of signal_date
    sig_idx = None
    for i, b in enumerate(all_bars):
        if str(b["date"])[:10] >= signal_date:
            sig_idx = i
            break
    if sig_idx is None:
        return {f"raw_{n}d": None for n in HOLD_DAYS} | {f"adj_{n}d": None for n in HOLD_DAYS}

    results: Dict = {}
    for horizon in HOLD_DAYS:
        future_bars = all_bars[sig_idx: sig_idx + horizon + 1]
        if len(future_bars) < 2:
            results[f"raw_{horizon}d"]     = None
            results[f"adj_{horizon}d"]     = None
            results[f"outcome_{horizon}d"] = "insufficient_data"
            continue

        # Walk forward: check if stop or target hit intraday
        outcome = "held"
        exit_idx = horizon  # default: exit at horizon close
        for offset, bar in enumerate(future_bars[1:], 1):
            # For SHORT: stop hit if price goes UP past stop
            if bar["high"] >= stop:
                outcome  = "stop_hit"
                exit_idx = offset
                break
            # For SHORT: target hit if price goes DOWN past target
            if bar["low"] <= target:
                outcome  = "target_hit"
                exit_idx = offset
                break

        exit_bar    = future_bars[exit_idx] if exit_idx < len(future_bars) else future_bars[-1]
        exit_price  = stop    if outcome == "stop_hit"   else \
                      target  if outcome == "target_hit" else \
                      float(exit_bar["close"])

        # SHORT return: positive when price falls
        raw_ret = (entry - exit_price) / entry * 100
        adj_ret = raw_ret - _friction(exit_idx) * 100

        results[f"raw_{horizon}d"]     = round(raw_ret, 2)
        results[f"adj_{horizon}d"]     = round(adj_ret, 2)
        results[f"outcome_{horizon}d"] = outcome

    return results


# ══════════════════════════════════════════════════════════════════════════════
# Main backtest runner
# ══════════════════════════════════════════════════════════════════════════════

def run_backtest(universe: List[str], event_blocks: List[Tuple[str, str]]) -> Dict:
    # Date range: add 90-day buffer before/after for price data
    start_dt = date(2021, 10, 1)   # buffer before first event block
    end_dt   = date(2025, 1, 31)   # buffer after last event block

    price_data  = fetch_price_data(universe, start_dt, end_dt)
    event_data  = fetch_earnings_events(event_blocks)

    signals: List[Dict] = []
    rejection_counts: Dict[str, int] = defaultdict(int)
    events_evaluated = 0

    logger.info("Evaluating signals for %d universe tickers...", len(universe))
    for ticker in universe:
        if ticker not in price_data:
            continue
        ticker_events = event_data.get(ticker, [])
        if not ticker_events:
            rejection_counts["no_events_found"] += len(ticker_events) or 1
            continue

        price_df = price_data[ticker]
        for event in ticker_events:
            events_evaluated += 1
            sig = evaluate_event(ticker, event, price_df)
            if sig:
                fwd = compute_forward_returns(sig, price_df)
                sig.update(fwd)
                signals.append(sig)
            else:
                # Track rejection at the most specific gate
                event_date = str(event.get("date", ""))[:10]
                # Reconstruct rejection reason
                all_bars = price_df.reset_index().to_dict("records")
                ed_idx = next((i for i, b in enumerate(all_bars)
                               if str(b["date"])[:10] >= event_date), None)
                if ed_idx is None or ed_idx == 0:
                    rejection_counts["event_out_of_price_range"] += 1
                    continue
                event_bar = all_bars[ed_idx]
                prev_bar  = all_bars[ed_idx - 1]
                gap_pct = (event_bar["open"] - prev_bar["close"]) / prev_bar["close"] * 100
                if gap_pct > REACTION_MIN_PCT:
                    rejection_counts["gap_too_small"] += 1
                    continue
                vol_window = all_bars[max(0, ed_idx - 20): ed_idx]
                vol_20avg  = statistics.mean(b["volume"] for b in vol_window) if vol_window else 0
                vol_ratio  = event_bar["volume"] / vol_20avg if vol_20avg > 0 else 0
                if vol_ratio < VOL_SPIKE:
                    rejection_counts["low_volume"] += 1
                    continue
                rejection_counts["no_continuation"] += 1

    logger.info("Backtest complete: %d events evaluated → %d signals", events_evaluated, len(signals))
    return {
        "signals":            signals,
        "rejection_counts":   dict(rejection_counts),
        "events_evaluated":   events_evaluated,
        "universe":           universe,
        "event_blocks":       event_blocks,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Reporting
# ══════════════════════════════════════════════════════════════════════════════

def _pct(x: Optional[float]) -> str:
    return "N/A" if x is None else f"{x:+.1f}%"

def _avg(vals: List[Optional[float]]) -> Optional[float]:
    clean = [v for v in vals if v is not None]
    return round(statistics.mean(clean), 1) if clean else None

def _win_rate(vals: List[Optional[float]]) -> Optional[float]:
    clean = [v for v in vals if v is not None]
    return round(sum(1 for v in clean if v > 0) / len(clean) * 100, 1) if clean else None

def _expectancy(wins: List[float], losses: List[float]) -> Optional[float]:
    if not wins and not losses:
        return None
    n = len(wins) + len(losses)
    wr = len(wins) / n
    avg_w = statistics.mean(wins) if wins else 0
    avg_l = statistics.mean(losses) if losses else 0
    return round(wr * avg_w + (1 - wr) * avg_l, 2)


def print_report(results: Dict) -> None:
    signals = results["signals"]
    rej     = results["rejection_counts"]
    events_evaluated = results["events_evaluated"]

    def section(title: str):
        print(f"\n{'═'*72}")
        print(f"  {title}")
        print(f"{'═'*72}")

    section("SHORT STRATEGY v1 VALIDATION BACKTEST")
    print(f"\n  Universe:          {len(results['universe'])} tickers")
    n_blocks = len(results["event_blocks"])
    if results["event_blocks"]:
        print(f"  Event window:      {results['event_blocks'][0][0]} → {results['event_blocks'][-1][1]}"
              f"  ({n_blocks} quarterly blocks)")
    print(f"  Events evaluated:  {events_evaluated}")
    print(f"  Signals:           {len(signals)}  ({len(signals)/events_evaluated*100:.1f}% signal rate)"
          if events_evaluated else "  Signals: 0")
    print(f"\n  Strategy identity: event-anchored continuation short")
    print(f"  Edge claim:        post-earnings gap-down with institutional volume")
    print(f"                     + continuation confirmation = remaining downside")
    print(f"\n  Doctrine: SHORT is the sole short-direction strategy.")
    print(f"  Voyager and all other strategies are LONG-only.")

    # ── Rejection funnel ──────────────────────────────────────────────────────
    section("1. SIGNAL FUNNEL")
    total_rej = sum(rej.values())
    print(f"\n  Total events in universe:          {events_evaluated}")
    print(f"  {'Rejection reason':<35} {'Count':>7} {'%':>7}")
    print(f"  {'-'*52}")
    for reason, cnt in sorted(rej.items(), key=lambda x: -x[1]):
        print(f"  {reason:<35} {cnt:>7} {cnt/events_evaluated*100:>6.1f}%  " if events_evaluated
              else f"  {reason:<35} {cnt:>7}")
    print(f"\n  Signals generated: {len(signals)}")
    if events_evaluated:
        print(f"  Gap qualifiers (gap ≤ -3%): "
              f"{rej.get('low_volume', 0) + rej.get('no_continuation', 0) + len(signals)}"
              f"  ({(rej.get('low_volume', 0) + rej.get('no_continuation', 0) + len(signals))/events_evaluated*100:.1f}%)")

    if not signals:
        section("NO SIGNALS GENERATED — INVESTIGATION REQUIRED")
        print(f"\n  Possible causes:")
        print(f"  1. Universe tickers may not overlap with FMP event coverage")
        print(f"  2. AMC/BMO timing: gap measured on event day, misses many AMC events")
        print(f"  3. Event blocks may not cover the price data window")
        print(f"  4. Continuation gate eliminates most gap-down events (mean reversion)")
        print(f"\n  Recommendation: verify event coverage manually for 2-3 known")
        print(f"  earnings disasters (NFLX 2022-Q1, SNAP 2022-Q1, etc.)")
        return

    # ── Coverage ──────────────────────────────────────────────────────────────
    section("2. EVENT COVERAGE")
    tickers_with_events = {s["ticker"] for s in signals}
    tickers_with_no_events = set(results["universe"]) - set(
        t for t in results["universe"] if results["rejection_counts"].get("no_events_found", 0) > 0
    )
    print(f"\n  Tickers with ≥1 signal: {len(tickers_with_events)}")
    sig_by_ticker = defaultdict(list)
    for s in signals:
        sig_by_ticker[s["ticker"]].append(s)
    print(f"\n  {'Ticker':<8} {'Signals':>8} {'AvgGap':>9} {'AvgVol':>9} {'Avg10d_raw':>12}")
    print(f"  {'-'*50}")
    for ticker, sigs in sorted(sig_by_ticker.items(), key=lambda x: -len(x[1])):
        gaps  = [s["gap_pct"] for s in sigs]
        vols  = [s["vol_ratio"] for s in sigs]
        r10   = [s.get("raw_10d") for s in sigs]
        print(f"  {ticker:<8} {len(sigs):>8} {_pct(statistics.mean(gaps)):>9} "
              f"{statistics.mean(vols):>9.2f} {_pct(_avg(r10)):>12}")

    # ── Performance ───────────────────────────────────────────────────────────
    section("3. PERFORMANCE SUMMARY")
    print(f"\n  {'Horizon':<8} {'N':>5} {'AvgRaw':>9} {'AvgAdj':>9} {'WinRate':>9} {'Expectancy':>12}")
    print(f"  {'-'*56}")
    for n in HOLD_DAYS:
        raws = [s.get(f"raw_{n}d") for s in signals]
        adjs = [s.get(f"adj_{n}d") for s in signals]
        raw_clean = [v for v in raws if v is not None]
        adj_clean = [v for v in adjs if v is not None]
        if not raw_clean:
            print(f"  {n}d       {'—':>5}")
            continue
        wins_raw  = [v for v in raw_clean if v > 0]
        losses_raw = [v for v in raw_clean if v <= 0]
        exp       = _expectancy(wins_raw, losses_raw)
        print(f"  {n}d       {len(raw_clean):>5} {_pct(_avg(raws)):>9} {_pct(_avg(adjs)):>9} "
              f"{str(_win_rate(raws) or '—'):>9} {_pct(exp):>12}")

    # ── Outcome breakdown ─────────────────────────────────────────────────────
    section("4. OUTCOME BREAKDOWN (10-day primary horizon)")
    outcome_counts: Dict[str, int] = defaultdict(int)
    for s in signals:
        outcome_counts[s.get("outcome_10d", "unknown")] += 1
    print(f"\n  {'Outcome':<20} {'Count':>7} {'%':>7}")
    print(f"  {'-'*37}")
    for outcome, cnt in sorted(outcome_counts.items(), key=lambda x: -x[1]):
        print(f"  {outcome:<20} {cnt:>7} {cnt/len(signals)*100:>6.1f}%")

    # ── Signal characteristics ────────────────────────────────────────────────
    section("5. SIGNAL CHARACTERISTICS")
    gap_vals  = [s["gap_pct"]   for s in signals]
    vol_vals  = [s["vol_ratio"] for s in signals]
    lag_vals  = [s["lag_sessions"] for s in signals]
    score_vals = [s["score"] for s in signals]
    print(f"\n  {'Metric':<20} {'Min':>8} {'Avg':>8} {'Max':>8}")
    print(f"  {'-'*47}")
    print(f"  {'Gap %':<20} {min(gap_vals):>8.1f} {statistics.mean(gap_vals):>8.1f} {max(gap_vals):>8.1f}")
    print(f"  {'Volume ratio':<20} {min(vol_vals):>8.2f} {statistics.mean(vol_vals):>8.2f} {max(vol_vals):>8.2f}")
    print(f"  {'Lag sessions':<20} {min(lag_vals):>8} {statistics.mean(lag_vals):>8.1f} {max(lag_vals):>8}")
    print(f"  {'Score':<20} {min(score_vals):>8} {statistics.mean(score_vals):>8.1f} {max(score_vals):>8}")

    lag_dist: Dict[int, int] = defaultdict(int)
    for l in lag_vals:
        lag_dist[l] += 1
    print(f"\n  Lag distribution (session between event and continuation):")
    for l in sorted(lag_dist):
        print(f"    lag={l}: {lag_dist[l]} signals ({lag_dist[l]/len(signals)*100:.0f}%)")

    # ── Return by gap size ────────────────────────────────────────────────────
    section("6. RETURN BY GAP SEVERITY (10-day raw)")
    buckets = [("gap < -10%", lambda s: s["gap_pct"] < -10),
               ("-10% ≤ gap < -7%", lambda s: -10 <= s["gap_pct"] < -7),
               ("-7% ≤ gap < -5%", lambda s: -7 <= s["gap_pct"] < -5),
               ("-5% ≤ gap < -3%", lambda s: -5 <= s["gap_pct"] < -3)]
    print(f"\n  {'Gap bucket':<22} {'N':>5} {'Avg10d':>9} {'WR':>7}")
    print(f"  {'-'*45}")
    for label, cond in buckets:
        sigs = [s for s in signals if cond(s)]
        r10  = [s.get("raw_10d") for s in sigs]
        print(f"  {label:<22} {len(sigs):>5} {_pct(_avg(r10)):>9} {str(_win_rate(r10) or '—'):>7}")

    # ── Sample signals ────────────────────────────────────────────────────────
    section("7. SAMPLE SIGNALS")
    top_signals = sorted(signals, key=lambda x: -x["score"])[:10]
    print(f"\n  {'Date':<12} {'Ticker':<7} {'Score':>6} {'Gap':>8} {'Vol':>6} {'Lag':>4} "
          f"{'10d_raw':>9} {'10d_adj':>9} {'Outcome':<15}")
    print(f"  {'-'*84}")
    for s in top_signals:
        print(f"  {s['signal_date']:<12} {s['ticker']:<7} {s['score']:>6} "
              f"{_pct(s['gap_pct']):>8} {s['vol_ratio']:>6.2f} {s['lag_sessions']:>4} "
              f"{_pct(s.get('raw_10d')):>9} {_pct(s.get('adj_10d')):>9} "
              f"{s.get('outcome_10d', 'N/A'):<15}")

    # ── Council simulation (simplified) ──────────────────────────────────────
    section("8. COUNCIL SIMULATION (simplified)")
    print(f"\n  The full VetoCouncil requires live API calls and is not simulated here.")
    print(f"  Key known council interactions with SHORT signals:")
    print(f"\n  RegimeAgent:    Blocks if VIX > 40 (extreme panic mode).")
    print(f"    → In 2022 bear market (VIX often 25-35), most SHORT signals would pass.")
    print(f"    → In 2020-style crisis (VIX > 40), SHORT would be blocked.")
    print(f"\n  EarningsAgent:  Scores 80 if no upcoming earnings, 20 if earnings within 5d.")
    print(f"    → SHORT triggers on PAST earnings, so upcoming earnings are unlikely.")
    print(f"    → Most SHORT signals would score 80 here (helping Tier 2 score).")
    print(f"\n  MomentumAgent:  For SHORT, negative 20d momentum = higher score.")
    print(f"    → Post-gap-down tickers likely have negative 20d momentum → good score.")
    print(f"\n  FlowAgent:      For SHORT, decelerating intraday volume = higher score.")
    print(f"    → Post-event continuation day may show volume tailing off → neutral/good.")
    print(f"\n  Structural concern: no SHORT-specific council weights exist.")
    print(f"  The council uses generic weights (flow: 0.25, sector: 0.20, etc.)")
    print(f"  A SHORT-specific profile would weight MomentumAgent higher and")
    print(f"  FlowAgent lower (intraday noise is not meaningful for event shorts).")

    # ── Event timing integrity ────────────────────────────────────────────────
    section("9. EVENT TIMING INTEGRITY")
    print(f"""
  Event source: FMP /stable/earnings-calendar with historical date ranges.
  Event date field: `date` (the earnings announcement date per FMP).

  Gap measurement: event_bar.open vs prev_bar.close
    WHERE event_bar = first bar with date >= event_date

  What "recent event" means in code:
    Event occurred within last MAX_LAG_SESSIONS (3) trading bars.
    Lag = number of bars between event bar and current (signal) bar.

  What "continuation" means in code:
    Signal bar close < event bar low.
    Price continues to trade BELOW the entire event day's candle.
    This filters out one-day wonders / quick rebounds.

  What "gap" means in code:
    (event_bar.open - prev_bar.close) / prev_bar.close
    Measured at the OPEN of the event bar — no intraday lookahead.

  Allowed post-event timing window:
    0 to 3 sessions (MAX_LAG_SESSIONS).
    Lag=0: signal fires same day as gap (immediate continuation).
    Lag=1: one day after the gap (first continuation confirmation).
    Lag=2/3: up to 3 sessions for continuation to manifest.

  LOOKAHEAD RISK:
    NONE for BMO reporters. The gap on bar[event_date] is measured at
    open, before any forward prices are used.

  KNOWN LIMITATION (not lookahead, but false negatives):
    For AMC reporters, FMP records event_date as the announcement day.
    The gap appears on the NEXT morning. The current code measures the
    gap on bar[event_date] which is pre-announcement → near-zero gap →
    rejected at the gap gate. AMC reporters are systematically missed.
    This affects roughly 50% of earnings events. It is not a lookahead
    issue but a signal-count issue. Fix: detect AMC events from FMP
    data and advance the reaction bar by 1 day.
""")

    # ── Conclusions and verdict ───────────────────────────────────────────────
    section("10. CONCLUSIONS AND VERDICT")
    n = len(signals)
    r10_clean = [s.get("raw_10d") for s in signals if s.get("raw_10d") is not None]
    wr10 = _win_rate(r10_clean)
    avg10 = _avg(r10_clean)
    r10_adj_clean = [s.get("adj_10d") for s in signals if s.get("adj_10d") is not None]
    avg10_adj = _avg(r10_adj_clean)

    print(f"\n  Signal count:       {n}  (backtest target: ≥30 for statistical validity)")
    print(f"  10d raw avg return: {_pct(avg10)}")
    print(f"  10d adj avg return: {_pct(avg10_adj)}  (friction model applied)")
    print(f"  10d win rate:       {wr10 or 'N/A'}%")

    if n < 10:
        verdict = "INSUFFICIENT SAMPLE — cannot assess edge"
        ready   = "NOT READY — need more signals"
    elif wr10 and wr10 >= 55 and avg10_adj and avg10_adj > 0:
        verdict = "POSITIVE EXPECTANCY — edge present after friction"
        ready   = "READY for paper validation subject to doctrine notes"
    elif wr10 and wr10 >= 50 and avg10_adj and avg10_adj > 0:
        verdict = "MARGINAL EDGE — positive but narrow after friction"
        ready   = "CONDITIONAL — paper cycle needed to confirm live edge"
    elif avg10_adj and avg10_adj <= 0:
        verdict = "EDGE DOES NOT SURVIVE FRICTION at this horizon"
        ready   = "NOT READY — review stop distance and hold period"
    else:
        verdict = "INCONCLUSIVE — small sample or mixed results"
        ready   = "PROCEED WITH CAUTION — paper cycle required"

    print(f"\n  VERDICT: {verdict}")
    print(f"  PAPER READINESS: {ready}")
    print(f"\n  Key structural strengths:")
    print(f"    + FMP event sourcing now fixed (stable API endpoint)")
    print(f"    + AMC/BMO timing is transparent — no lookahead contamination")
    print(f"    + Continuation gate adds meaningful filter beyond gap alone")
    print(f"    + Stop/target geometry enforces RR ≥ 2.0 consistently")
    print(f"\n  Key remaining limitations before capital deployment:")
    print(f"    - AMC reporters systematically missed (fix = 2x signal count)")
    print(f"    - No SHORT-specific council weights")
    print(f"    - Fundamental quality not checked (not a screened short)")
    print(f"    - Borrow availability not verified (short squeeze risk)")
    print(f"    - Sample size may be borderline for statistical confidence")


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SHORT v1 backtest")
    parser.add_argument("--quick", action="store_true",
                        help="20-ticker universe, 18-month event window")
    args = parser.parse_args()

    universe     = QUICK_UNIVERSE    if args.quick else FULL_UNIVERSE
    event_blocks = QUICK_EVENT_BLOCKS if args.quick else FULL_EVENT_BLOCKS

    t0      = time.time()
    results = run_backtest(universe, event_blocks)
    elapsed = time.time() - t0

    print_report(results)
    print(f"\n  Total runtime: {elapsed/60:.1f} minutes")

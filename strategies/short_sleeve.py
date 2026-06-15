"""
strategies/short_sleeve.py — SHORT Sleeve A: Event Continuation Short.

MANDATE: SHORT is the sole short-direction strategy family in this platform.
All other strategies (Voyager, Sniper, Remora, Contrarian) are LONG-only.
See docs/strategy/SHORT_DOCTRINE.md for the full SHORT family doctrine.
See docs/strategy/STRATEGY_DOCTRINE.md §Direction Mandate for platform rules.

This is Sleeve A of the SHORT family. Sleeve B (Broken Leader / Structural
Deterioration Short) is in design phase — see docs/scorecards/short_sleeve_scorecard.md.

This is the Event Continuation Short, hardened for production from prior research:
  • Uses FMP earnings calendar for verified event dates (not heuristics)
  • Entry: session after earnings announcement if reaction gap ≤ -3%
  • Requires volume ≥ 1.5× average (institutional selling, not retail noise)
  • Requires continuation: recent close still pressing below event bar low
  • Earnings within 3 sessions (max lag)

Signal conditions:
  1. FMP earnings date confirmed via /stable/earnings-calendar
  2. Gap reaction ≤ -3.0% on earnings session open
  3. Volume ≥ 1.5× 20-day average
  4. Continuation: today's close < event bar's low
  5. R:R ≥ 2.0, Stop: 1.5× ATR above entry

AMC/BMO timing:
  For after-market-close (AMC) earnings, the gap appears the following morning,
  not on the FMP-reported announcement date. FMP /stable/earnings-calendar does
  not expose a time-of-day field on the Starter plan, so we detect AMC events
  by price action: if the bar at the reported event_date has a near-flat gap
  but the next trading day shows a qualifying reaction (≤ REACTION_MIN_PCT),
  the announcement was AMC and the event bar is shifted forward by one session.
"""
from __future__ import annotations
import logging
import statistics
from datetime import date, timedelta
from typing import Dict, List, Optional

from core.alpaca_client import get_alpaca
from core.fmp_client import get_fmp

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
REACTION_MIN_PCT     = -3.0    # gap must be ≤ -3%
VOL_SPIKE            = 1.5     # ≥ 1.5× avg volume
MAX_LAG_SESSIONS     = 3       # entry within 3 sessions of event
MIN_SCORE            = 55
MIN_RRR              = 2.0
BARS_NEEDED          = 30


class ShortSleeveScanner:
    """
    Event-anchored short scanner using FMP earnings calendar.
    Returns SHORT opportunities triggered by verified earnings disappointments.
    """

    def __init__(self, account_equity: float = 100_000):
        self._alpaca = get_alpaca()
        self._fmp    = get_fmp()
        self._equity = account_equity

    def scan(self, tickers: List[str]) -> List[Dict]:
        from core.config import ALLOW_SHORTS
        if not ALLOW_SHORTS:
            return []

        # Get earnings from last 14 days (catch recent events)
        earnings_past = self._recent_earnings(lookback_days=14)

        opportunities = []
        for ticker in tickers:
            ev = earnings_past.get(ticker.upper())
            if ev is None:
                continue
            try:
                opp = self._evaluate(ticker, ev)
                if opp:
                    opportunities.append(opp)
            except Exception as exc:
                logger.debug("ShortSleeve eval failed %s: %s", ticker, exc)

        opportunities.sort(key=lambda x: x["score"], reverse=True)
        logger.info("ShortSleeve scan: %d events found, %d setups", len(earnings_past), len(opportunities))
        return opportunities

    # ── Earnings lookup ───────────────────────────────────────────────────────

    def _recent_earnings(self, lookback_days: int = 14) -> Dict[str, Dict]:
        """
        Returns {ticker: event_dict} for earnings announced in last lookback_days.
        Uses FMP /stable/earnings-calendar with historical date range.
        """
        try:
            cal = self._fmp.get_past_earnings(lookback_days)
        except Exception as exc:
            logger.error("FMP past earnings failed: %s", exc)
            return {}

        result: Dict[str, Dict] = {}
        for ev in cal:
            sym = (ev.get("symbol") or "").upper()
            dt  = ev.get("date", "")
            if not sym or not dt:
                continue
            # Keep most recent event per ticker
            if sym not in result or dt > result[sym]["date"]:
                result[sym] = ev
        return result

    # ── Per-ticker evaluation ─────────────────────────────────────────────────

    def _evaluate(self, ticker: str, event: Dict) -> Optional[Dict]:
        bars = self._alpaca.get_daily_bars(ticker, days=BARS_NEEDED)
        if len(bars) < 5:
            return None

        event_date = event.get("date", "")
        if not event_date:
            return None

        # Find the earnings reaction bar
        dates  = [b["date"] for b in bars]
        try:
            ed_idx = next(i for i, d in enumerate(dates) if str(d)[:10] >= event_date)
        except StopIteration:
            return None

        if ed_idx == 0:
            return None

        # AMC detection: the FMP-reported event_date may be the announcement day
        # for after-market-close events, where the reaction prints the next
        # morning. If the reported day shows a near-flat gap but the next bar
        # shows a qualifying reaction, treat the next bar as the event bar.
        event_time = "bmo"
        reported_gap = self._gap_at(bars, ed_idx)
        if reported_gap is not None and reported_gap > REACTION_MIN_PCT and ed_idx + 1 < len(bars):
            next_gap = self._gap_at(bars, ed_idx + 1)
            if next_gap is not None and next_gap <= REACTION_MIN_PCT:
                ed_idx += 1
                event_time = "amc"

        # Lag check (after possible AMC adjustment)
        lag = len(bars) - 1 - ed_idx
        if lag > MAX_LAG_SESSIONS or lag < 0:
            return None

        event_bar  = bars[ed_idx]
        prev_bar   = bars[ed_idx - 1]
        today_bar  = bars[-1]

        gap_pct = (event_bar["open"] - prev_bar["close"]) / prev_bar["close"] * 100
        if gap_pct > REACTION_MIN_PCT:
            return None

        # Volume confirmation
        vol_20avg = statistics.mean(b["volume"] for b in bars[max(0, ed_idx - 20): ed_idx])
        vol_ratio = event_bar["volume"] / vol_20avg if vol_20avg > 0 else 0
        if vol_ratio < VOL_SPIKE:
            return None

        # Continuation: today's close still below event-bar low
        continuation = today_bar["close"] < event_bar["low"]
        if not continuation:
            return None

        # Trade geometry
        atr    = self._atr(bars[max(0, -14):])
        entry  = today_bar["close"]
        stop   = entry + atr * 1.5
        target = entry - (stop - entry) * MIN_RRR
        rr     = round((entry - target) / (stop - entry), 2)
        if rr < MIN_RRR or target <= 0:
            return None

        score = self._score(gap_pct, vol_ratio, lag, continuation)
        if score < MIN_SCORE:
            return None

        shares = self._size_shares(entry, stop)

        return {
            "strategy":      "SHORT",
            "ticker":        ticker,
            "direction":     "SHORT",
            "score":         score,
            "entry_price":   round(entry, 2),
            "stop_loss":     round(stop, 2),
            "target_price":  round(target, 2),
            "risk_reward":   rr,
            "shares":        shares,
            "gap_pct":       round(gap_pct, 2),
            "vol_ratio":     round(vol_ratio, 2),
            "lag_sessions":  lag,
            "event_date":    event_date,
            "event_time":    event_time,
            "eps_estimate":  event.get("epsEstimated"),
            "eps_actual":    event.get("epsActual"),    # stable API field (not "eps")
        }

    # ── Scoring ───────────────────────────────────────────────────────────────

    def _score(self, gap_pct: float, vol_ratio: float, lag: int, continuation: bool) -> int:
        s = 30
        s += min(30, int(abs(gap_pct) * 4))    # larger gap = stronger signal
        s += min(20, int((vol_ratio - 1.5) * 20))  # more vol = conviction
        s += max(0, (MAX_LAG_SESSIONS - lag) * 5)   # fresher = better
        if continuation:
            s += 15
        return max(0, min(100, s))

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _gap_at(self, bars: List[Dict], idx: int) -> Optional[float]:
        """Open-to-prior-close gap at bars[idx], in percent. None if out of range."""
        if idx <= 0 or idx >= len(bars):
            return None
        prev_close = bars[idx - 1]["close"]
        if prev_close <= 0:
            return None
        return (bars[idx]["open"] - prev_close) / prev_close * 100

    def _atr(self, bars: List[Dict]) -> float:
        trs = []
        for i in range(1, len(bars)):
            h, l, pc = bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        return statistics.mean(trs) if trs else 0.0

    def _size_shares(self, entry: float, stop: float) -> int:
        from core.config import MAX_POSITION_PCT
        risk = abs(stop - entry)
        if risk <= 0:
            return 0
        return max(1, int(self._equity * MAX_POSITION_PCT / risk))

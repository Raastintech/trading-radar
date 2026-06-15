"""
strategies/remora.py — Remora: quiet institutional accumulation detection.

Logic from dynamic_remora_scanner.py and remora_engine.py.

Remora is fundamentally different from Sniper:
  • Sniper chases BREAKOUTS (explosive, high-volume moves)
  • Remora finds STEALTH accumulation (subtle, low-footprint entry by big players)

Signal conditions:
  • Price change < 0.5% (stealth — no headline move)
  • Volume 20–60% above 20-day average (unusual but not explosive)
  • Dollar volume ≥ $25M (institutional-grade liquidity)
  • Price within 2% of 52-week high (accumulation near highs, not distressed buying)
  • Spread (bid-ask) < 0.15% (tight — dark pool eligible)
  • No earnings within 5 days

Direction: LONG (following institutional accumulation).
Sizing: 1.5× normal (institutional tail-wind adds confidence).
"""
from __future__ import annotations
import logging
import statistics
from typing import Dict, List, Optional, Tuple

from core.alpaca_client import get_alpaca
from core.fmp_client import get_fmp

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
STEALTH_PRICE_CHANGE = 0.005   # price move must be < 0.5%
VOL_FLOOR_RATIO      = 1.20    # ≥ 20% above avg vol
VOL_CEIL_RATIO       = 1.60    # < 60% above avg vol (not a breakout)
MIN_DOLLAR_VOL       = 25_000_000
PCT_FROM_52W_HIGH    = 0.02    # within 2% of 52-week high
MAX_SPREAD_PCT       = 0.0015  # < 0.15%
MIN_SCORE            = 55
EARNINGS_SAFE_DAYS   = 5
BARS_NEEDED          = 252     # 52 weeks


class RemoraScanner:
    """
    Stealth accumulation scanner. Returns LONG opportunities.
    """

    def __init__(self, account_equity: float = 100_000):
        self._alpaca = get_alpaca()
        self._fmp    = get_fmp()
        self._equity = account_equity

    def scan(self, tickers: List[str]) -> List[Dict]:
        earnings_cal = self._fmp.get_earnings_calendar(days_ahead=EARNINGS_SAFE_DAYS)
        earnings_soon = {e.get("symbol", "").upper() for e in earnings_cal}

        opportunities = []
        rejections: Dict[str, int] = {}
        for ticker in tickers:
            if ticker.upper() in earnings_soon:
                rejections["earnings_soon"] = rejections.get("earnings_soon", 0) + 1
                continue
            try:
                opp, reason = self._evaluate(ticker)
                if opp:
                    opportunities.append(opp)
                else:
                    rejections[reason] = rejections.get(reason, 0) + 1
            except Exception as exc:
                logger.debug("Remora eval failed %s: %s", ticker, exc)
                rejections["exception"] = rejections.get("exception", 0) + 1

        opportunities.sort(key=lambda x: x["score"], reverse=True)
        rej_str = "  ".join(
            f"{reason}={count}"
            for reason, count in sorted(rejections.items(), key=lambda item: -item[1])[:7]
        )
        logger.info(
            "Remora scan: %d stealth setups from %d tickers | rejections: %s",
            len(opportunities), len(tickers), rej_str or "none",
        )
        return opportunities

    def _evaluate(self, ticker: str) -> Tuple[Optional[Dict], str]:
        bars = self._alpaca.get_daily_bars(ticker, days=BARS_NEEDED)
        if len(bars) < BARS_NEEDED:
            return None, "stale_bars"

        closes  = [b["close"] for b in bars]
        volumes = [b["volume"] for b in bars]

        today_close   = closes[-1]
        prev_close    = closes[-2]
        today_vol     = volumes[-1]
        avg_vol       = statistics.mean(volumes[-20:])
        vol_ratio     = today_vol / avg_vol if avg_vol > 0 else 0
        price_chg     = abs(today_close - prev_close) / prev_close if prev_close > 0 else 1
        dollar_vol    = today_close * today_vol
        high_52w      = max(closes[-min(252, len(closes)):])
        pct_from_high = (high_52w - today_close) / high_52w if high_52w > 0 else 1

        # All stealth filters must pass
        if price_chg >= STEALTH_PRICE_CHANGE:
            return None, "price_moved"
        if not (VOL_FLOOR_RATIO <= vol_ratio <= VOL_CEIL_RATIO):
            return None, "vol_too_low" if vol_ratio < VOL_FLOOR_RATIO else "vol_too_high"
        if dollar_vol < MIN_DOLLAR_VOL:
            return None, "low_dollar_vol"
        if pct_from_high > PCT_FROM_52W_HIGH:
            return None, "not_near_52w_high"

        # Spread gate via live quote
        quote = self._alpaca.get_quote(ticker)
        if quote:
            mid = quote["mid"]
            spread_pct = (quote["ask"] - quote["bid"]) / mid if mid > 0 else 1
            if spread_pct > MAX_SPREAD_PCT:
                return None, "wide_spread"
        else:
            return None, "no_quote"

        # Build trade geometry
        atr    = self._atr(bars[-14:])
        stop   = today_close - atr * 1.2
        target = today_close + atr * 3.0
        rr     = round((target - today_close) / (today_close - stop), 2)
        if rr < 2.0 or stop <= 0:
            return None, "poor_geometry"

        score  = self._score(vol_ratio, pct_from_high, spread_pct, dollar_vol)
        if score < MIN_SCORE:
            return None, "low_score"

        shares = self._size_shares(today_close, stop, multiplier=1.5)

        return {
            "strategy":      "REMORA",
            "ticker":        ticker,
            "direction":     "LONG",
            "score":         score,
            "entry_price":   round(today_close, 2),
            "stop_loss":     round(stop, 2),
            "target_price":  round(target, 2),
            "risk_reward":   rr,
            "shares":        shares,
            "vol_ratio":     round(vol_ratio, 2),
            "dollar_vol":    dollar_vol,
            "spread_pct":    round(spread_pct * 100, 3),
            "pct_from_high": round(pct_from_high * 100, 2),
        }, ""

    def _score(self, vol_ratio: float, pct_from_high: float, spread_pct: float, dollar_vol: float) -> int:
        s = 40
        # Ideal vol ratio: 1.3–1.5 is stealth sweet spot
        s += max(0, 15 - abs(vol_ratio - 1.4) * 30)
        # Near 52w high
        s += max(0, 15 - pct_from_high * 500)
        # Tight spread
        if spread_pct < 0.0005:
            s += 15
        elif spread_pct < 0.001:
            s += 8
        # Dollar volume quality
        if dollar_vol > 50_000_000:
            s += 10
        elif dollar_vol > 25_000_000:
            s += 5
        return max(0, min(100, int(s)))

    def _atr(self, bars: List[Dict]) -> float:
        trs = []
        for i in range(1, len(bars)):
            h, l, pc = bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        return statistics.mean(trs) if trs else 0.0

    def _size_shares(self, entry: float, stop: float, multiplier: float = 1.0) -> int:
        from core.config import MAX_POSITION_PCT
        risk = entry - stop
        if risk <= 0:
            return 0
        return max(1, int(self._equity * MAX_POSITION_PCT * multiplier / risk))

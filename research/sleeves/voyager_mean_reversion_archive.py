"""
ARCHIVED — doctrine drift. NOT production code.

Original path: strategies/voyager.py
Archived to:   research/sleeves/voyager_mean_reversion_archive.py
Reason:        This file implemented VOYAGER as a mean-reversion SHORT
               (fade the extension, RSI overbought, SHORT direction). That
               directly contradicts the VOYAGER doctrine in STRATEGY_DOCTRINE.md,
               which defines VOYAGER as a LONG-only, 6–18 month institutional
               accumulation strategy. The production strategies/voyager.py has
               been fully rebuilt to match doctrine. Keep this file as reference
               only — do not re-import or promote it.

--- ORIGINAL DOCSTRING BELOW ---

strategies/voyager.py — Voyager: mean-reversion from extended top (SHORT bias).

Logic extracted from voyager_complete.py and voyager_production_v2.py.

Signal conditions:
  • Price ≥ +15% above 200-day MA (extended / overextended)
  • RSI(14) ≥ 70 (overbought) on daily
  • Volume declining over last 5 sessions (distribution, not breakout)
  • No earnings within 10 days (avoid event risk)
  • Cluster-selling detected: 3 of last 10 closes below their open

Direction: SHORT (mean-reversion back toward 200 MA).
R:R minimum: 2.0. Stop: 1.5× ATR above entry.
"""
from __future__ import annotations
import logging
import statistics
from datetime import date, timedelta
from typing import Dict, List, Optional

from core.alpaca_client import get_alpaca
from core.fmp_client import get_fmp
from strategies.shared.risk import calc_atr, size_shares

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
MA_EXTENSION_PCT     = 0.15    # price must be ≥ 15% above 200 MA
RSI_OVERBOUGHT       = 70
VOL_DECAY_SESSIONS   = 5       # volume must be declining
CLUSTER_SELL_NEEDED  = 3       # of last 10 sessions must be down-closes
MIN_SCORE            = 60
MIN_RRR              = 2.0
BARS_NEEDED          = 220     # 200 MA + headroom
EARNINGS_SAFE_DAYS   = 10      # skip if earnings < 10 days away


class VoyagerScanner:
    """
    Mean-reversion short scanner. Returns ranked SHORT opportunities.
    Call scan(tickers) after market open.
    """

    def __init__(self, account_equity: float = 100_000):
        self._alpaca = get_alpaca()
        self._fmp    = get_fmp()
        self._equity = account_equity

    def scan(self, tickers: List[str]) -> List[Dict]:
        from core.config import ALLOW_SHORTS
        if not ALLOW_SHORTS:
            logger.info("Voyager: shorts disabled via config")
            return []

        earnings_cal = self._fmp.get_earnings_calendar(days_ahead=EARNINGS_SAFE_DAYS)
        earnings_soon = {e.get("symbol", "").upper() for e in earnings_cal}

        rej: Dict[str, int] = {}
        opportunities = []
        for ticker in tickers:
            if ticker.upper() in earnings_soon:
                rej["earnings_soon"] = rej.get("earnings_soon", 0) + 1
                continue
            try:
                opp, reason = self._evaluate(ticker)
                if opp:
                    opportunities.append(opp)
                else:
                    rej[reason] = rej.get(reason, 0) + 1
            except Exception as exc:
                logger.debug("VOYAGER eval error %s: %s", ticker, exc)
                rej["exception"] = rej.get("exception", 0) + 1

        opportunities.sort(key=lambda x: x["score"], reverse=True)
        top_rej = sorted(rej.items(), key=lambda x: -x[1])[:6]
        rej_str = "  ".join(f"{r}={n}" for r, n in top_rej)
        logger.info(
            "VOYAGER: %d setup(s) from %d tickers | rejections: %s",
            len(opportunities), len(tickers), rej_str or "none",
        )
        return opportunities

    def _evaluate(self, ticker: str):
        """Returns (opp_dict, "") on pass or (None, reason_str) on reject."""
        bars = self._alpaca.get_daily_bars(ticker, days=BARS_NEEDED)
        if len(bars) < BARS_NEEDED:
            return None, "stale_bars"

        closes  = [b["close"] for b in bars]
        volumes = [b["volume"] for b in bars]
        opens   = [b["open"]  for b in bars]

        ma200          = statistics.mean(closes[-200:])
        today_close    = closes[-1]
        extension_pct  = (today_close - ma200) / ma200
        if extension_pct < MA_EXTENSION_PCT:
            return None, "not_extended"

        rsi = self._rsi(closes[-30:])
        if rsi < RSI_OVERBOUGHT:
            return None, "rsi_not_overbought"

        # Volume declining
        recent_vols = volumes[-VOL_DECAY_SESSIONS:]
        vol_declining = all(
            recent_vols[i] >= recent_vols[i + 1]
            for i in range(len(recent_vols) - 1)
        )

        # Cluster selling: down-closes in last 10 bars
        down_closes = sum(
            1 for i in range(-10, 0) if closes[i] < opens[i]
        )
        cluster_sell = down_closes >= CLUSTER_SELL_NEEDED

        if not vol_declining and not cluster_sell:
            return None, "no_distribution"

        atr    = calc_atr(bars)
        stop   = today_close + atr * 1.5
        target = today_close - (stop - today_close) * MIN_RRR
        rr     = round((today_close - target) / (stop - today_close), 2)
        if rr < MIN_RRR or target <= 0:
            return None, "poor_geometry"

        score = self._score(extension_pct, rsi, vol_declining, cluster_sell, down_closes)
        if score < MIN_SCORE:
            return None, "low_score"

        shares = size_shares(self._equity, today_close, stop)

        return {
            "strategy":      "VOYAGER",
            "ticker":        ticker,
            "direction":     "SHORT",
            "score":         score,
            "entry_price":   round(today_close, 2),
            "stop_loss":     round(stop, 2),
            "target_price":  round(target, 2),
            "risk_reward":   rr,
            "shares":        shares,
            "extension_pct": round(extension_pct * 100, 1),
            "rsi":           round(rsi, 1),
            "distance_from_ma": round(extension_pct * 100, 1),
            "cluster_sell":  cluster_sell,
        }, ""

    def _score(
        self,
        extension_pct: float,
        rsi: float,
        vol_declining: bool,
        cluster_sell: bool,
        down_close_count: int,
    ) -> int:
        s = 40
        s += min(25, int(extension_pct * 100))   # extension above 200 MA
        s += min(20, int(rsi - 70) * 2)          # overbought severity
        if vol_declining:
            s += 10
        s += min(10, down_close_count * 2)        # cluster selling
        return max(0, min(100, s))

    def _rsi(self, closes: List[float], period: int = 14) -> float:
        if len(closes) < period + 1:
            return 50.0
        gains, losses = [], []
        for i in range(1, len(closes)):
            d = closes[i] - closes[i - 1]
            gains.append(max(d, 0))
            losses.append(max(-d, 0))
        ag = statistics.mean(gains[-period:])
        al = statistics.mean(losses[-period:])
        if al == 0:
            return 100.0
        rs = ag / al
        return round(100 - (100 / (1 + rs)), 2)


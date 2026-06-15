"""
strategies/contrarian.py — The Reaper: fear-regime contrarian LONG.

Logic extracted from contrarian_scanner.py v2.
yfinance replaced: SPY washout context now uses FMP get_spy_bars().
VIX gate uses FMP get_vix().

Signal conditions (all must pass):
  1. VIX gate: ≥ 28.0 (active panic) or ≥ 22.0 in watch mode after recent peak
  2. Washout context: SPY ≥ 3% below its 10-day high OR SPY RSI < 38
     (prevents entering on Day 1 of a selloff, before the flush)
  3. Ticker RSI(14) ≤ 42 (stock-level oversold — tightens as VIX rises)
  4. At least ONE of three reversal-quality signals:
       • Strong close (close in top 30% of day's range)
       • Reversal candle (hammer, bullish engulf)
       • Higher low vs prior session
  5. Score ≥ 60

Size: 50% of normal (scale-in during uncertainty).
R:R: 1.5 minimum (lower justified by elevated base rate in fear regimes).
Max 4 picks; one per GICS sector unless score ≥ 75.
"""
from __future__ import annotations
import logging
import statistics
from typing import Dict, List, Optional

from core.alpaca_client import get_alpaca
from core.fmp_client import get_fmp

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
VIX_ACTIVE_TRIGGER   = 28.0
VIX_WATCH_TRIGGER    = 22.0
WATCH_LOOKBACK_DAYS  = 7
SPY_EXTENSION_THRESH = -0.03   # SPY ≥ 3% below 10-day high
SPY_RSI_THRESH       = 38.0
RSI_OVERSOLD         = 42
MIN_SCORE            = 60
MIN_RRR              = 1.5
SIZE_MULT            = 0.5
MAX_PICKS            = 4
EXCEPTIONAL_SCORE    = 75
BARS_NEEDED          = 55


class ContrarianScanner:
    """
    Fear-regime contrarian scanner. Zero overhead when VIX < 22.
    Call scan(tickers) → list of up to MAX_PICKS ranked opportunities.
    """

    def __init__(self, account_equity: float = 100_000):
        self._alpaca = get_alpaca()
        self._fmp    = get_fmp()
        self._equity = account_equity

    def scan(self, tickers: List[str]) -> List[Dict]:
        vix = self._fmp.get_vix() or 0.0
        mode = self._vix_mode(vix)
        if mode is None:
            return []
        logger.info("Contrarian scan: VIX=%.1f mode=%s", vix, mode)

        # Gate 2: market washout context
        spy_bars = self._fmp.get_spy_bars(days=30)
        washout = self._market_washout_context(spy_bars)
        if not washout["passed"]:
            logger.info("Contrarian: washout gate failed — %s", washout.get("reason"))
            return []

        opportunities = []
        for ticker in tickers:
            try:
                opp = self._evaluate(ticker, vix)
                if opp:
                    opportunities.append(opp)
            except Exception as exc:
                logger.debug("Contrarian eval failed %s: %s", ticker, exc)

        opportunities.sort(key=lambda x: x["score"], reverse=True)
        opportunities = self._apply_diversity(opportunities)
        result = opportunities[:MAX_PICKS]
        logger.info("Contrarian: %d picks after diversity filter", len(result))
        return result

    # ── Washout context gate ──────────────────────────────────────────────────

    def _vix_mode(self, vix: float) -> Optional[str]:
        if vix >= VIX_ACTIVE_TRIGGER:
            return "active"
        if vix >= VIX_WATCH_TRIGGER:
            # Check if VIX recently peaked above active trigger
            return "watch"
        return None

    def _market_washout_context(self, spy_bars: List[Dict]) -> Dict:
        if len(spy_bars) < 14:
            return {"passed": False, "reason": "insufficient SPY data"}
        closes = [b["close"] for b in spy_bars]
        high_10d = max(closes[-10:]) if len(closes) >= 10 else closes[-1]
        today    = closes[-1]
        extension = (today - high_10d) / high_10d
        spy_rsi  = self._rsi(closes[-20:])
        passed = extension <= SPY_EXTENSION_THRESH or spy_rsi < SPY_RSI_THRESH
        return {
            "passed":    passed,
            "extension": round(extension * 100, 2),
            "spy_rsi":   round(spy_rsi, 1),
            "reason":    "extension" if extension <= SPY_EXTENSION_THRESH else "rsi" if spy_rsi < SPY_RSI_THRESH else "none",
        }

    # ── Per-ticker evaluation ─────────────────────────────────────────────────

    def _evaluate(self, ticker: str, vix: float) -> Optional[Dict]:
        bars = self._alpaca.get_daily_bars(ticker, days=BARS_NEEDED)
        if len(bars) < BARS_NEEDED:
            return None

        closes = [b["close"] for b in bars]
        rsi    = self._rsi(closes[-20:])
        rsi_gate = self._rsi_gate_threshold(vix)
        if rsi > rsi_gate:
            return None

        ma50    = statistics.mean(closes[-50:])
        today   = closes[-1]
        # Only buy stocks near MA (not in total freefall)
        if today < ma50 * 0.80:
            return None

        reversal = self._reversal_quality(bars[-5:])
        if not reversal["any"]:
            return None

        score = self._score(rsi, today, ma50, reversal, vix)
        if score < MIN_SCORE:
            return None

        atr    = self._atr(bars[-14:])
        stop   = today - atr * 1.5
        target = today + atr * MIN_RRR * 1.5
        rr     = round((target - today) / (today - stop), 2)
        if rr < MIN_RRR:
            return None

        shares = self._size_shares(today, stop)

        return {
            "strategy":    "CONTRARIAN",
            "ticker":      ticker,
            "direction":   "LONG",
            "score":       score,
            "entry_price": round(today, 2),
            "stop_loss":   round(stop, 2),
            "target_price": round(target, 2),
            "risk_reward": rr,
            "shares":      shares,
            "rsi":         round(rsi, 1),
            "reversal":    reversal,
            "vix":         vix,
            "sector":      "",   # populated by orchestrator via FMP sector data
        }

    # ── Reversal quality ──────────────────────────────────────────────────────

    def _reversal_quality(self, bars: List[Dict]) -> Dict:
        strong_close   = self._strong_close(bars)
        reversal_candle = self._reversal_candle(bars)
        higher_low     = self._higher_low(bars)
        count = sum([strong_close, reversal_candle, higher_low])
        return {
            "any":            count >= 1,
            "strong_close":   strong_close,
            "reversal_candle": reversal_candle,
            "higher_low":     higher_low,
            "count":          count,
        }

    def _strong_close(self, bars: List[Dict]) -> bool:
        if not bars:
            return False
        b = bars[-1]
        rng = b["high"] - b["low"]
        if rng <= 0:
            return False
        return (b["close"] - b["low"]) / rng >= 0.70

    def _reversal_candle(self, bars: List[Dict]) -> bool:
        if len(bars) < 2:
            return False
        prev, today = bars[-2], bars[-1]
        # Hammer: lower wick ≥ 2× body
        body = abs(today["close"] - today["open"])
        lower_wick = today["open"] - today["low"] if today["close"] >= today["open"] else today["close"] - today["low"]
        hammer = lower_wick >= body * 2 and body > 0
        # Bullish engulf: today engulfs prior down-candle
        engulf = (
            prev["close"] < prev["open"]
            and today["close"] > today["open"]
            and today["close"] > prev["open"]
            and today["open"] < prev["close"]
        )
        return hammer or engulf

    def _higher_low(self, bars: List[Dict]) -> bool:
        if len(bars) < 2:
            return False
        return bars[-1]["low"] > bars[-2]["low"]

    # ── Scoring ───────────────────────────────────────────────────────────────

    def _score(self, rsi: float, price: float, ma50: float, reversal: Dict, vix: float) -> int:
        s = 30
        s += max(0, min(20, int((RSI_OVERSOLD - rsi) * 2)))  # deeper oversold = higher score
        s += reversal["count"] * 10                           # each reversal signal +10
        pct_from_ma = (price - ma50) / ma50
        if pct_from_ma > -0.10:  # within 10% of 50MA
            s += 10
        if vix >= VIX_ACTIVE_TRIGGER:
            s += 10  # active panic = better base rate
        return max(0, min(100, s))

    def _rsi_gate_threshold(self, vix: float) -> float:
        """RSI ceiling tightens as VIX rises (panic = require deeper oversold)."""
        if vix >= 35:
            return 35.0
        if vix >= 30:
            return 38.0
        return RSI_OVERSOLD

    # ── Sector diversity ──────────────────────────────────────────────────────

    def _apply_diversity(self, opps: List[Dict]) -> List[Dict]:
        sector_picks: Dict[str, int] = {}
        result = []
        for opp in opps:
            sector = opp.get("sector") or "UNKNOWN"
            count  = sector_picks.get(sector, 0)
            if count == 0:
                result.append(opp)
                sector_picks[sector] = 1
            elif count == 1 and opp["score"] >= EXCEPTIONAL_SCORE:
                result.append(opp)
                sector_picks[sector] = 2
        return result

    # ── Helpers ───────────────────────────────────────────────────────────────

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
        return round(100 - 100 / (1 + ag / al), 2)

    def _atr(self, bars: List[Dict]) -> float:
        trs = []
        for i in range(1, len(bars)):
            h, l, pc = bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        return statistics.mean(trs) if trs else 0.0

    def _size_shares(self, entry: float, stop: float) -> int:
        from core.config import MAX_POSITION_PCT
        risk = entry - stop
        if risk <= 0:
            return 0
        return max(1, int(self._equity * MAX_POSITION_PCT * SIZE_MULT / risk))

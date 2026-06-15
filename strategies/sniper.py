"""
strategies/sniper.py — Sniper: institutional breakout confirmation, LONG only.

Mandate: capture tactical breakout moves where volume confirms institutional
participation. Enter on the confirmed breakout bar. Hold 1–30 trading days.

Distinct from VOYAGER (enters accumulation base weeks before breakout) and
REMORA (detects quiet volume flow without a price breakout).

Signal conditions (v6 configuration — 2026-04-21):
  • Today's close > max high of prior 20 bars (EXCLUDING today) — breakout bar
  • Previous close ≤ that 20-day high — first bar above resistance, not continuation
  • Volume ≥ 1.4× 20-day average — institutional participation signature
  • ATR contraction: recent_5bar_atr / prior_15bar_atr < 0.85 — volatility
    compressed before the breakout (replaces old absolute range gate)
  • Above 50-day MA — in a confirmed uptrend, not a relief bounce
  • MA50 rising slope: ma50_now > ma50_20bars_ago — mid-trend breakout, not
    a bounce from a declining structure
  • RS vs SPY positive over 10 days — relative strength confirms leadership
  • SPY above 200d MA — no bear-market regime (bear = structural overhead supply
    even for quality large-cap names; VIX < 28 alone does not catch sustained bears)
  • VIX < 28 — no panic regime (CONTRARIAN territory above this)
  • Score ≥ 70, R:R ≥ 2.5

Entry: close on breakout bar.
Stop: entry − 1.5× ATR (wider stop confirmed by backtest; 1× ATR stop-hit rate 55%).
Target: entry + 3.75× ATR (R:R = 2.5, maintained with wider stop).

Universe: large-cap institutional quality only (LARGE_CAP_UNIVERSE constant below).
SaaS/high-beta cohort excluded — backtest attribution confirmed systematic false
breakouts from that cohort (v5 run, 2026-04-20).

Backtest provenance: v1–v6 run 2026-04-20. v6 configuration:
  WR 50.7%, avgAdj +0.57%, stop-hit 42.7%, n=75 over 5 years, 4/5 years positive.
"""
from __future__ import annotations
import logging
import statistics
from typing import Dict, List, Optional

from core.alpaca_client import get_alpaca, AlpacaClient
from core.data_gatekeeper import get_gatekeeper
from core.fmp_client import get_fmp

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
VOL_SPIKE_THRESH      = 1.4
MIN_SCORE             = 70
MIN_RRR               = 2.5
BARS_NEEDED           = 75        # 50 MA + 20 slope lookback + 5 headroom
MA50_SLOPE_BARS       = 20        # how far back to measure MA50 slope
ATR_CONTRACTION_THRESH = 0.85     # recent_5bar_atr / prior_15bar_atr must be < this
STOP_ATR_MULT         = 1.5       # stop = entry − 1.5× ATR (v2 finding)
TARGET_ATR_MULT       = 3.75      # target = entry + 3.75× ATR (R:R = 2.5)
VIX_REGIME_CEILING    = 28.0
SPY_BARS_NEEDED       = 220       # 200d MA + 20 headroom

# ── Large-cap institutional quality universe (v5b, 2026-04-20) ────────────────
# Excludes: high-beta SaaS cohort (PLTR, SNOW, MDB, NET, TWLO, DDOG, ZS, RBLX —
# systematic false breakout rate confirmed by backtest attribution), speculative
# names with shallow institutional ownership, and event-driven tickers (MRNA,
# OXY, SLB) that behave differently from franchise businesses.
LARGE_CAP_UNIVERSE = {
    # Mega-cap tech + semis
    "NVDA", "AMD", "META", "AAPL", "MSFT", "GOOGL", "AMZN", "NFLX", "AVGO",
    "QCOM", "AMAT", "LRCX", "MRVL",
    # Financials
    "JPM", "GS", "V", "MA",
    # Healthcare
    "LLY", "ABBV", "REGN",
    # Consumer quality
    "NKE", "LULU", "HD", "LOW",
    # Energy majors
    "XOM", "CVX",
    # Enterprise software
    "CRM", "ADBE", "PANW",
    # Marketplace leaders
    "SHOP", "MELI", "UBER",
    # Sector ETFs (regime confirmation / controls)
    "QQQ", "XLK", "XLF", "XLE", "XLY", "XLV", "XLI",
    "TLT", "GLD", "XLU", "XLP", "WMT", "COST", "PG",
}


class SniperScanner:
    """
    Breakout scanner (v6 configuration). Call scan(tickers) → list of ranked
    opportunities. Each opportunity dict is ready for VetoCouncil evaluation.

    Tickers not in LARGE_CAP_UNIVERSE are silently skipped — the backtest
    showed the SaaS / high-beta cohort produces systematic false breakouts
    that no structural filter can reliably separate before the outcome.
    """

    def __init__(self, account_equity: float = 100_000):
        self._alpaca: AlpacaClient = get_alpaca()
        self._fmp   = get_fmp()
        self._gate  = get_gatekeeper()
        self._equity = account_equity

    # ── Public ────────────────────────────────────────────────────────────────

    def scan(self, tickers: List[str]) -> List[Dict]:
        input_count = len(tickers)
        # ── Regime gates (evaluated once per scan cycle) ──────────────────────
        vix = self._fmp.get_vix() or 0.0
        if vix >= VIX_REGIME_CEILING:
            logger.info(
                "Sniper scan: input=%d  regime_suppressed=VIX(%.1f>=%.1f)  opportunities=0",
                input_count, vix, VIX_REGIME_CEILING,
            )
            return []

        # SPY: 10d RS benchmark + 200d MA bear-market gate.
        # Try the regime cache first (4h TTL, multi-year history).  On miss,
        # fetch from Alpaca with 220-bar lookback and write to the regime slot
        # so the next caller can read it without a network round-trip.
        spy_df = self._gate.get_spy_bars(min_bars=SPY_BARS_NEEDED)
        if spy_df is None:
            spy_bars = self._alpaca.get_daily_bars("SPY", days=SPY_BARS_NEEDED)
            if len(spy_bars) >= SPY_BARS_NEEDED:
                import pandas as pd
                df = pd.DataFrame(spy_bars)
                df["date"] = pd.to_datetime(df["date"])
                df.set_index("date", inplace=True)
                self._gate.put_spy_bars(df)
                spy_closes = [b["close"] for b in spy_bars]
            else:
                logger.warning("Sniper: insufficient SPY bars (%d); skipping 200d MA gate", len(spy_bars))
                spy_closes = [b["close"] for b in spy_bars]
        else:
            spy_closes = spy_df["close"].tolist()

        spy_return_10d = self._spy_return(spy_closes, 10)

        # SPY 200d MA: True = regime is clear; None = insufficient history (gate skipped)
        spy_above_200d: Optional[bool] = None
        if len(spy_closes) >= 200:
            spy_ma200 = statistics.mean(spy_closes[-200:])
            spy_above_200d = spy_closes[-1] > spy_ma200
            if not spy_above_200d:
                logger.info(
                    "Sniper scan: input=%d  regime_suppressed=SPY<MA200(%.2f<%.2f)  opportunities=0",
                    input_count, spy_closes[-1], spy_ma200,
                )
                return []

        # ── Universe filter ───────────────────────────────────────────────────
        # Defensive: input pool should already be the doctrinal whitelist
        # (see main.SNIPER_DOCTRINAL_POOL). This intersection stays as a
        # guard against future callers that bypass the routing layer.
        scan_tickers = [t for t in tickers if t in LARGE_CAP_UNIVERSE]
        whitelist_dropped = len(tickers) - len(scan_tickers)

        opportunities = []
        rejections: Dict[str, int] = {}
        for ticker in scan_tickers:
            try:
                opp, reject = self._evaluate(ticker, vix, spy_return_10d)
                if opp:
                    opportunities.append(opp)
                elif reject:
                    rejections[reject] = rejections.get(reject, 0) + 1
            except Exception as exc:
                logger.debug("Sniper eval failed %s: %s", ticker, exc)
                rejections["eval_error"] = rejections.get("eval_error", 0) + 1

        opportunities.sort(key=lambda x: x["score"], reverse=True)
        rej_str = "  ".join(f"{k}={v}" for k, v in sorted(rejections.items(), key=lambda x: -x[1]))
        logger.info(
            "Sniper scan: input=%d  whitelist_dropped=%d  evaluated=%d  opportunities=%d  vix=%.1f | rejections: %s",
            input_count, whitelist_dropped, len(scan_tickers),
            len(opportunities), vix, rej_str or "none",
        )
        return opportunities

    # ── Core evaluation ───────────────────────────────────────────────────────

    def _evaluate(self, ticker: str, vix: float, spy_10d: float):
        """Returns (opportunity_dict, None) or (None, rejection_reason_str)."""
        bars = self._alpaca.get_daily_bars(ticker, days=BARS_NEEDED)
        if len(bars) < BARS_NEEDED:
            return None, "stale_bars"

        closes  = [b["close"] for b in bars]
        volumes = [b["volume"] for b in bars]

        # ── Use PRIOR bars only (exclude today) for resistance check ──────────
        # On a genuine breakout day, today_high IS the new 20d high, making
        # close > max(highs[-20:]) = close > today_high mathematically impossible.
        prior_bars  = bars[-21:-1]          # 20 bars before today
        prior_highs = [b["high"] for b in prior_bars]
        recent_high = max(prior_highs) if prior_highs else 0.0

        today_close = closes[-1]
        prev_close  = closes[-2]

        # ── Gate 1: Breakout bar ──────────────────────────────────────────────
        breakout = today_close > recent_high and prev_close <= recent_high
        if not breakout:
            return None, "no_breakout"

        # ── Gate 2: Volume confirmation ───────────────────────────────────────
        avg_vol   = statistics.mean(volumes[-20:]) if len(volumes) >= 20 else 0
        today_vol = volumes[-1]
        if avg_vol <= 0 or today_vol / avg_vol < VOL_SPIKE_THRESH:
            return None, "volume_insufficient"

        atr = self._atr(bars[-14:])

        # ── Gate 3: ATR contraction (replaces old absolute range gate) ────────
        # Measures whether volatility compressed before the breakout.
        # recent_5bar_atr  = avg true range of the 5 bars before today
        # prior_15bar_atr  = avg true range of the 15 bars before that window
        # Ratio < 0.85 means volatility contracted into the breakout.
        recent_5_bars  = bars[-6:-1]    # 5 bars before today
        prior_15_bars  = bars[-21:-6]   # 15 bars before the 5-bar window
        recent_5bar_atr  = self._atr(recent_5_bars)
        prior_15bar_atr  = self._atr(prior_15_bars)
        if prior_15bar_atr <= 0:
            return None, "atr_contraction_unavailable"
        contraction_ratio = recent_5bar_atr / prior_15bar_atr
        if contraction_ratio >= ATR_CONTRACTION_THRESH:
            return None, "atr_contraction_fail"

        # ── Gate 4: Above 50-day MA ───────────────────────────────────────────
        ma50 = statistics.mean(closes[-50:])
        if today_close < ma50:
            return None, "below_ma50"

        # ── Gate 5: Rising MA50 slope ─────────────────────────────────────────
        # MA50 20 bars ago = mean of closes[-70:-20]; must be below current MA50.
        # Ensures the breakout comes from a mid-trend structure, not a declining one.
        if len(closes) >= 70:
            ma50_prev = statistics.mean(closes[-70:-20])
            if ma50 <= ma50_prev:
                return None, "ma50_slope_flat"
        else:
            return None, "stale_bars"

        # ── Gate 6: RS vs SPY ─────────────────────────────────────────────────
        ticker_10d  = (closes[-1] / closes[-11] - 1) if len(closes) >= 11 else 0
        rs_positive = ticker_10d > spy_10d

        # ── Scoring ───────────────────────────────────────────────────────────
        score = self._score(
            vol_ratio=today_vol / avg_vol,
            rs_positive=rs_positive,
            contraction_ratio=contraction_ratio,
            vix=vix,
        )
        if score < MIN_SCORE:
            return None, "score_too_low"

        # ── R:R geometry — 1.5× ATR stop ─────────────────────────────────────
        stop   = today_close - atr * STOP_ATR_MULT
        target = today_close + atr * TARGET_ATR_MULT
        rr     = round((target - today_close) / (today_close - stop), 2)
        if rr < MIN_RRR:
            return None, "rr_insufficient"

        shares = self._size_shares(today_close, stop)

        return {
            "strategy":          "SNIPER",
            "ticker":            ticker,
            "direction":         "LONG",
            "score":             score,
            "entry_price":       round(today_close, 2),
            "stop_loss":         round(stop, 2),
            "target_price":      round(target, 2),
            "risk_reward":       rr,
            "shares":            shares,
            "vol_ratio":         round(today_vol / avg_vol, 2),
            "atr_contraction":   round(contraction_ratio, 3),
            "atr":               round(atr, 2),
            "vix":               vix,
        }, None

    # ── Scoring ───────────────────────────────────────────────────────────────

    def _score(
        self,
        vol_ratio: float,
        rs_positive: bool,
        contraction_ratio: float,
        vix: float,
    ) -> int:
        s = 50  # base
        s += min(25, int((vol_ratio - 1.4) * 40))  # vol: up to +25
        if rs_positive:
            s += 15
        # ATR contraction quality: tighter = better
        if contraction_ratio < 0.65:
            s += 10
        elif contraction_ratio < 0.75:
            s += 5
        # VIX comfort zone
        if vix < 18:
            s += 5
        elif vix > 22:
            s -= 10
        return max(0, min(100, s))

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _atr(self, bars: List[Dict]) -> float:
        trs = []
        for i in range(1, len(bars)):
            h, l, pc = bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        return statistics.mean(trs) if trs else 0.0

    def _spy_return(self, closes: List[float], n: int) -> float:
        if len(closes) < n + 1:
            return 0.0
        return closes[-1] / closes[-(n + 1)] - 1

    def _size_shares(self, entry: float, stop: float) -> int:
        from core.config import MAX_POSITION_PCT
        risk_per_share = entry - stop
        if risk_per_share <= 0:
            return 0
        dollar_risk = self._equity * MAX_POSITION_PCT
        return max(1, int(dollar_risk / risk_per_share))

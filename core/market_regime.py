"""
core/market_regime.py — Market regime detection (legacy daemon module).

Determines current market environment:
- Regime: STRONG_BULL, BULL, LATE_BULL, DISTRIBUTION, BEAR, CAPITULATION, TRANSITIONAL
- Risk state: RISK_ON, RISK_OFF, TRANSITIONAL
- Phase: EXPANSION, PEAK, CONTRACTION, TROUGH, TRANSITIONAL

Phase 3A (2026-06-13): Alpaca broker connection is permanently disabled.
core.alpaca_client is a stub that serves data from cache/prices/*.parquet —
no network calls are made.

Primary VIX source: FMP (get_vix).
Primary price source: cache/prices/*.parquet via AlpacaClient stub.

Note: the research pipeline (research/market_heartbeat.py, research/regime_forecast.py)
uses core.regime_forecaster directly and is preferred over this module.
This file is retained for reference; the trading daemon (main.py) is decommissioned.
"""
from __future__ import annotations
import logging
from datetime import datetime
from typing import Dict, List, Optional

from core.alpaca_client import get_alpaca, AlpacaClient
from core.fmp_client import get_fmp

logger = logging.getLogger(__name__)


class MarketRegimeDetector:
    """
    Detect market regime using SPY moving averages, VIX level, and sector leadership.
    All data sourced through production clients only.
    """

    def __init__(self) -> None:
        self._alpaca: AlpacaClient = get_alpaca()
        self._fmp = get_fmp()

    def detect_regime(self) -> Dict:
        """
        Returns:
            {
                'regime': str,
                'risk_state': str,
                'phase': str,
                'trend_strength': int (0-100),
                'spy_vs_50ma': float,
                'spy_vs_200ma': float,
                'vix_level': float,
                'vix_regime': str,
                'sector_leadership': str,
                'recommendation': str,
                'signals': Dict,
            }
        """
        spy_data    = self._get_spy_analysis()
        vix_data    = self._get_vix_analysis()
        sector_data = self._get_sector_leadership()

        regime       = self._determine_regime(spy_data, vix_data, sector_data)
        risk_state   = self._determine_risk_state(sector_data, vix_data)
        phase        = self._determine_phase(spy_data, vix_data)
        trend_str    = self._calculate_trend_strength(spy_data)
        recommendation = self._generate_recommendation(regime, risk_state, phase)

        return {
            "regime":           regime,
            "risk_state":       risk_state,
            "phase":            phase,
            "trend_strength":   trend_str,
            "spy_vs_50ma":      spy_data["vs_50ma"],
            "spy_vs_200ma":     spy_data["vs_200ma"],
            "vix_level":        vix_data["level"],
            "vix_regime":       vix_data["regime"],
            "sector_leadership": sector_data["leadership"],
            "recommendation":   recommendation,
            "signals": {
                "spy":     spy_data,
                "vix":     vix_data,
                "sectors": sector_data,
            },
        }

    # ── Private helpers ───────────────────────────────────────────────────────

    def _get_spy_analysis(self) -> Dict:
        try:
            bars = self._alpaca.get_daily_bars("SPY", days=250)
            if not bars or len(bars) < 200:
                return self._default_spy_data()

            prices = [b["close"] for b in bars]
            current = prices[-1]
            ma_50   = sum(prices[-50:])  / 50
            ma_200  = sum(prices[-200:]) / 200
            vs_50ma  = (current - ma_50)  / ma_50  * 100
            vs_200ma = (current - ma_200) / ma_200 * 100
            momentum = (current - prices[-5]) / prices[-5] * 100 if len(prices) >= 5 else 0.0

            return {
                "price":    current,
                "ma_50":    ma_50,
                "ma_200":   ma_200,
                "vs_50ma":  vs_50ma,
                "vs_200ma": vs_200ma,
                "ma_trend": "BULLISH" if ma_50 > ma_200 else "BEARISH",
                "momentum": momentum,
                "above_50":  current > ma_50,
                "above_200": current > ma_200,
            }
        except Exception as exc:
            logger.warning("SPY analysis failed: %s", exc)
            return self._default_spy_data()

    def _classify_vix(self, level: float, bars: Optional[List[Dict]] = None) -> Dict:
        if level < 15:
            regime = "CALM"
        elif level < 20:
            regime = "NORMAL"
        elif level < 25:
            regime = "ELEVATED"
        elif level < 30:
            regime = "HIGH"
        else:
            regime = "EXTREME"

        trend = "NEUTRAL"
        if bars and len(bars) >= 5:
            week_ago = bars[-5]["close"] if isinstance(bars[-5], dict) else float(bars[-5])
            trend = "RISING" if level > week_ago else "FALLING"

        return {"level": level, "regime": regime, "trend": trend}

    def _get_vix_analysis(self) -> Dict:
        try:
            current = self._fmp.get_vix()
            if current and current > 0:
                spy_bars = self._fmp.get_spy_bars(days=5)
                closes   = [{"close": b["close"]} for b in spy_bars]
                return self._classify_vix(current, closes)
        except Exception as exc:
            logger.warning("FMP VIX failed: %s", exc)

        try:
            bars = self._alpaca.get_daily_bars("VXX", days=20)
            if bars:
                proxy    = bars[-1]["close"]
                vix_equiv = max(10.0, min(80.0, proxy * 0.85))
                result   = self._classify_vix(vix_equiv, bars)
                result["source"] = "vxx_proxy"
                return result
        except Exception as exc:
            logger.warning("VXX proxy failed: %s", exc)

        return {"level": 20.0, "regime": "NORMAL", "trend": "NEUTRAL"}

    def _get_sector_leadership(self) -> Dict:
        sectors = {
            "XLK": "Technology",
            "XLF": "Financials",
            "XLE": "Energy",
            "XLV": "Healthcare",
            "XLY": "Consumer Discretionary",
            "XLP": "Consumer Staples",
            "XLI": "Industrials",
            "XLU": "Utilities",
            "XLB": "Materials",
        }
        sector_performance: Dict = {}
        try:
            for ticker, name in sectors.items():
                try:
                    bars = self._alpaca.get_daily_bars(ticker, days=5)
                    if bars and len(bars) >= 2:
                        perf = (bars[-1]["close"] - bars[0]["close"]) / bars[0]["close"] * 100
                        sector_performance[ticker] = {"name": name, "performance": perf}
                except Exception:
                    continue

            if sector_performance:
                sorted_sectors = sorted(
                    sector_performance.items(),
                    key=lambda x: x[1]["performance"],
                    reverse=True,
                )
                top_tickers = [s[0] for s in sorted_sectors[:3]]
                if any(t in top_tickers for t in ["XLK", "XLY", "XLF"]):
                    leadership = "OFFENSIVE"
                elif any(t in top_tickers for t in ["XLP", "XLU", "XLV"]):
                    leadership = "DEFENSIVE"
                else:
                    leadership = "MIXED"

                return {
                    "leadership": leadership,
                    "top_3":    sorted_sectors[:3],
                    "bottom_3": sorted_sectors[-3:],
                    "all_sectors": sector_performance,
                }
        except Exception as exc:
            logger.warning("Sector leadership failed: %s", exc)

        return {"leadership": "UNKNOWN", "top_3": [], "bottom_3": [], "all_sectors": {}}

    def _determine_regime(self, spy: Dict, vix: Dict, sec: Dict) -> str:
        if spy["above_50"] and spy["above_200"] and vix["level"] < 15 and sec["leadership"] == "OFFENSIVE":
            return "STRONG_BULL"
        if spy["above_50"] and spy["above_200"] and vix["level"] < 20:
            return "BULL"
        if spy["above_50"] and spy["above_200"] and (vix["level"] >= 20 or sec["leadership"] == "DEFENSIVE"):
            return "LATE_BULL"
        if spy["above_200"] and not spy["above_50"]:
            return "DISTRIBUTION"
        if not spy["above_200"] or vix["level"] > 25:
            return "BEAR"
        if vix["level"] > 30:
            return "CAPITULATION"
        return "TRANSITIONAL"

    def _determine_risk_state(self, sec: Dict, vix: Dict) -> str:
        if sec["leadership"] == "DEFENSIVE" and vix["level"] > 20:
            return "RISK_OFF"
        if sec["leadership"] == "OFFENSIVE" and vix["level"] < 18:
            return "RISK_ON"
        return "TRANSITIONAL"

    def _determine_phase(self, spy: Dict, vix: Dict) -> str:
        if spy["above_50"] and spy["momentum"] > 0:
            return "EXPANSION"
        if spy["above_200"] and spy["momentum"] < 0:
            return "PEAK"
        if not spy["above_50"] and spy["momentum"] < 0:
            return "CONTRACTION"
        if not spy["above_200"] and spy["momentum"] > 0:
            return "TROUGH"
        return "TRANSITIONAL"

    def _calculate_trend_strength(self, spy: Dict) -> int:
        score = 0
        if spy["above_50"]:           score += 25
        if spy["above_200"]:          score += 25
        if spy["ma_trend"] == "BULLISH": score += 25
        if spy["momentum"] > 0:       score += 25
        return score

    def _generate_recommendation(self, regime: str, risk_state: str, phase: str) -> str:
        recommendations = {
            "STRONG_BULL":  "AGGRESSIVE — full risk exposure, offensive sectors",
            "BULL":         "BULLISH — standard risk, favor growth",
            "LATE_BULL":    "CAUTIOUS — reduce exposure, take profits",
            "DISTRIBUTION": "DEFENSIVE — raise cash, avoid new longs",
            "BEAR":         "RISK_OFF — minimal exposure, defensive only",
            "CAPITULATION": "OPPORTUNISTIC — prepare to buy extreme fear",
            "TRANSITIONAL": "NEUTRAL — wait for clarity",
        }
        return recommendations.get(regime, "NEUTRAL — monitor closely")

    @staticmethod
    def _default_spy_data() -> Dict:
        return {
            "price": 0.0, "ma_50": 0.0, "ma_200": 0.0,
            "vs_50ma": 0.0, "vs_200ma": 0.0,
            "ma_trend": "UNKNOWN", "momentum": 0.0,
            "above_50": False, "above_200": False,
        }

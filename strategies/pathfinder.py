"""
strategies/pathfinder.py - PATHFINDER: early sponsorship / emerging leader.

Future research sleeve. LONG only.

Doctrine:
  Exploit the interval where a smaller but liquid company is improving
  fundamentally and technically before broad institutional crowding is obvious.
  PATHFINDER is not a hype/meme scanner and not a breakout-confirmation sleeve.

Baseline identity:
  - smaller-cap but tradeable single stocks
  - business improvement required
  - improving relative strength from a low/mid base
  - constructive base / higher-low structure
  - not extended, not already obvious mega-cap sponsorship

Baseline tag: PATHFINDER_V1
"""
from __future__ import annotations

import logging
import statistics
from typing import Dict, List, Optional, Tuple

from core.alpaca_client import get_alpaca
from core.fmp_client import get_fmp
from strategies.shared.risk import calc_atr, size_shares

logger = logging.getLogger(__name__)

BASELINE_TAG = "PATHFINDER_V1"

# Universe/quality gates.
MIN_PRICE = 8.0
MIN_MARKET_CAP = 500_000_000
MAX_MARKET_CAP = 15_000_000_000
MIN_AVG_DOLLAR_VOL_20 = 5_000_000
MAX_AVG_DOLLAR_VOL_20 = 150_000_000

# Tape structure.
BARS_NEEDED = 220
SPY_BARS_NEEDED = 220
RS_20_MIN = 0.03
RS_60_MIN = 0.00
MAX_EXTENSION_20D_LOW = 0.25
MAX_EXTENSION_MA50 = 0.12
MIN_DVOL_RATIO = 1.05
MAX_VOLUME_SPIKE = 2.50
MAX_PRIOR_HIGH_DISTANCE = 0.08

# Fundamentals.
MIN_FUND_SCORE = 55

# Regime.
VIX_ACTIVE_CEILING = 28.0
VIX_SELECTIVE_CEILING = 35.0

# Geometry/scoring.
MIN_SCORE = 65
STOP_ATR_MULT = 1.8
TARGET_ATR_MULT = 4.5
MIN_RR = 2.5


class PathfinderScanner:
    """
    Early sponsorship / emerging leader scanner.

    PATHFINDER is not currently wired into the live paper/capital path. It is a
    future research sleeve with explicit doctrine and baseline logic.
    """

    def __init__(self, account_equity: float = 100_000):
        self._alpaca = get_alpaca()
        self._fmp = get_fmp()
        self._equity = account_equity

    def scan(self, tickers: List[str]) -> List[Dict]:
        vix = self._fmp.get_vix() or 0.0
        if vix >= VIX_SELECTIVE_CEILING:
            logger.info("PATHFINDER suppressed: VIX=%.1f >= %.1f", vix, VIX_SELECTIVE_CEILING)
            return []

        spy_bars = self._alpaca.get_daily_bars("SPY", days=SPY_BARS_NEEDED)
        if len(spy_bars) < 80:
            logger.warning("PATHFINDER suppressed: insufficient SPY bars")
            return []
        spy_closes = [float(b["close"]) for b in spy_bars]

        if len(spy_closes) >= 200:
            spy_ma200 = statistics.mean(spy_closes[-200:])
            if spy_closes[-1] < spy_ma200 and vix >= VIX_ACTIVE_CEILING:
                logger.info("PATHFINDER suppressed: SPY below MA200 and VIX=%.1f", vix)
                return []

        profile_map: Dict[str, Optional[Dict]] = {}
        fund_map: Dict[str, Optional[Dict]] = {}
        for ticker in tickers:
            sym = ticker.upper()
            try:
                profile_map[sym] = self._fmp.get_company_profile(sym)
            except Exception:
                profile_map[sym] = None
            try:
                fund_map[sym] = self._fmp.get_fundamentals(sym)
            except Exception:
                fund_map[sym] = None

        opportunities: List[Dict] = []
        rejections: Dict[str, int] = {}
        for ticker in tickers:
            sym = ticker.upper()
            try:
                opp, reason = self._evaluate(sym, spy_closes, vix, profile_map.get(sym), fund_map.get(sym))
                if opp:
                    opportunities.append(opp)
                else:
                    rejections[reason] = rejections.get(reason, 0) + 1
            except Exception as exc:
                logger.debug("PATHFINDER eval failed %s: %s", sym, exc)
                rejections["eval_error"] = rejections.get("eval_error", 0) + 1

        opportunities.sort(key=lambda x: x["score"], reverse=True)
        rej_str = "  ".join(f"{k}={v}" for k, v in sorted(rejections.items(), key=lambda x: -x[1])[:10])
        logger.info(
            "PATHFINDER: %d setup(s) from %d tickers | rejections: %s",
            len(opportunities),
            len(tickers),
            rej_str or "none",
        )
        return opportunities

    def _evaluate(
        self,
        ticker: str,
        spy_closes: List[float],
        vix: float,
        profile: Optional[Dict],
        fundamentals: Optional[Dict],
    ) -> Tuple[Optional[Dict], str]:
        bars = self._alpaca.get_daily_bars(ticker, days=BARS_NEEDED)
        if len(bars) < BARS_NEEDED:
            return None, "stale_bars"

        closes = [float(b["close"]) for b in bars]
        volumes = [int(b["volume"]) for b in bars]
        today = closes[-1]
        if today < MIN_PRICE:
            return None, "price_too_low"

        market_cap = float((profile or {}).get("marketCap") or 0)
        if market_cap < MIN_MARKET_CAP:
            return None, "market_cap_too_low"
        if market_cap > MAX_MARKET_CAP:
            return None, "market_cap_too_high"

        avg_dvol_20 = statistics.mean(closes[i] * volumes[i] for i in range(-20, 0))
        if avg_dvol_20 < MIN_AVG_DOLLAR_VOL_20:
            return None, "low_dollar_vol"
        if avg_dvol_20 > MAX_AVG_DOLLAR_VOL_20:
            return None, "too_crowded_dvol"

        fund_score, fund_reason = self._fundamental_inflection_score(fundamentals)
        if fund_score < MIN_FUND_SCORE:
            return None, fund_reason

        ma50 = statistics.mean(closes[-50:])
        ma150 = statistics.mean(closes[-150:])
        if today < ma50:
            return None, "below_ma50"
        if ma50 < ma150 * 0.97:
            return None, "ma_structure_declining"

        low_60 = min(closes[-60:])
        high_60_prior = max(closes[-61:-1])
        if (today - low_60) / low_60 > MAX_EXTENSION_20D_LOW:
            return None, "too_far_from_base_low"
        if (today - ma50) / ma50 > MAX_EXTENSION_MA50:
            return None, "too_extended_ma50"
        if (high_60_prior - today) / high_60_prior > MAX_PRIOR_HIGH_DISTANCE:
            return None, "not_near_base_high"

        higher_low = min(closes[-20:]) > min(closes[-60:-20])
        if not higher_low:
            return None, "no_higher_low"

        dvol_20 = statistics.mean(closes[i] * volumes[i] for i in range(-20, 0))
        dvol_60_prev = statistics.mean(closes[i] * volumes[i] for i in range(-80, -20))
        dvol_ratio = dvol_20 / dvol_60_prev if dvol_60_prev > 0 else 0.0
        if dvol_ratio < MIN_DVOL_RATIO:
            return None, "dvol_not_improving"

        avg_vol_20 = statistics.mean(volumes[-20:])
        vol_ratio = volumes[-1] / avg_vol_20 if avg_vol_20 > 0 else 0.0
        if vol_ratio > MAX_VOLUME_SPIKE:
            return None, "volume_spike_chase"

        rs_20 = self._rs(closes, spy_closes, 20)
        rs_60 = self._rs(closes, spy_closes, 60)
        if rs_20 is None or rs_20 < RS_20_MIN:
            return None, "rs_20_weak"
        if rs_60 is None or rs_60 < RS_60_MIN:
            return None, "rs_60_weak"

        trigger = today > max(closes[-11:-1]) and closes[-2] <= max(closes[-12:-2])
        if not trigger:
            return None, "no_trigger"

        atr = calc_atr(bars[-14:])
        if atr <= 0:
            return None, "atr_unavailable"
        stop = max(today - STOP_ATR_MULT * atr, ma50 * 0.96)
        if stop >= today:
            return None, "poor_geometry"
        target = today + TARGET_ATR_MULT * atr
        rr = (target - today) / (today - stop)
        if rr < MIN_RR:
            return None, "poor_geometry"

        score = self._score(
            fund_score=fund_score,
            rs_20=rs_20,
            rs_60=rs_60,
            dvol_ratio=dvol_ratio,
            extension_ma50=(today - ma50) / ma50,
            vix=vix,
        )
        if score < MIN_SCORE:
            return None, "score_too_low"

        return {
            "strategy": "PATHFINDER",
            "sleeve": BASELINE_TAG,
            "signal_version": BASELINE_TAG,
            "ticker": ticker,
            "direction": "LONG",
            "entry_price": round(today, 2),
            "stop_loss": round(stop, 2),
            "target_price": round(target, 2),
            "risk_reward": round(rr, 2),
            "score": score,
            "shares": size_shares(self._equity, today, stop, multiplier=0.5),
            "fund_score": fund_score,
            "market_cap": market_cap,
            "avg_dollar_vol_20": round(avg_dvol_20, 2),
            "dvol_ratio": round(dvol_ratio, 3),
            "rs_20": round(rs_20, 4),
            "rs_60": round(rs_60, 4),
            "extension_ma50": round((today - ma50) / ma50, 4),
            "regime_vix": vix,
            "sector": (profile or {}).get("sector", ""),
            "qualified_reason": "PATHFINDER_V1 early sponsorship trigger: business inflection + constructive higher-low base + RS turn",
        }, ""

    @staticmethod
    def _rs(closes: List[float], spy_closes: List[float], window: int) -> Optional[float]:
        if len(closes) <= window or len(spy_closes) <= window:
            return None
        stock_ret = closes[-1] / closes[-window - 1] - 1
        spy_ret = spy_closes[-1] / spy_closes[-window - 1] - 1
        return stock_ret - spy_ret

    @staticmethod
    def _fundamental_inflection_score(fundamentals: Optional[Dict]) -> Tuple[int, str]:
        if not fundamentals:
            return 0, "fundamentals_missing"
        income = fundamentals.get("income") or []
        cashflow = fundamentals.get("cashflow") or []
        if len(income) < 4:
            return 0, "fundamentals_incomplete"

        rev = [float(q.get("revenue") or 0) for q in income[:4]]
        op_income = [float(q.get("operatingIncome") or 0) for q in income[:4]]
        gp_ratio = [float(q.get("grossProfitRatio") or 0) for q in income[:4]]
        ocf = [float(q.get("operatingCashFlow") or 0) for q in cashflow[:4]] if len(cashflow) >= 4 else []

        if min(rev) <= 0:
            return 0, "revenue_missing"

        latest_growth = rev[0] / rev[1] - 1
        prior_growth = rev[1] / rev[2] - 1
        two_q_growth = rev[0] / rev[2] - 1
        margin_now = op_income[0] / rev[0]
        margin_prev = op_income[1] / rev[1]
        gross_margin_delta = gp_ratio[0] - gp_ratio[1]

        score = 0
        if latest_growth > 0.08:
            score += 25
        elif latest_growth > 0.03:
            score += 15
        if latest_growth > prior_growth + 0.03:
            score += 20
        if two_q_growth > 0.12:
            score += 15
        if margin_now > margin_prev:
            score += 15
        if gross_margin_delta > 0:
            score += 10
        if ocf and ocf[0] > 0:
            score += 10
        if op_income[0] > op_income[1]:
            score += 10

        return max(0, min(100, score)), "fundamental_score_too_low"

    @staticmethod
    def _score(
        *,
        fund_score: int,
        rs_20: float,
        rs_60: float,
        dvol_ratio: float,
        extension_ma50: float,
        vix: float,
    ) -> int:
        score = 25
        score += min(25, fund_score * 0.25)
        score += min(20, max(0, rs_20) * 250)
        score += min(10, max(0, rs_60) * 100)
        score += min(15, max(0, dvol_ratio - 1.0) * 75)
        if extension_ma50 <= 0.08:
            score += 10
        if vix < VIX_ACTIVE_CEILING:
            score += 5
        return max(0, min(100, int(score)))

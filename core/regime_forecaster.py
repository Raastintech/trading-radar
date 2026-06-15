"""
core/regime_forecaster.py — Market & Sector Regime Forecaster V1 (Phase 1).

Pure-Python feature builders + heuristic regime/sector/strategy mapping.

This module is research-only. It does not:
  - approve or block trades
  - emit paper evidence
  - touch governance, execution, sleeve logic, Alpha Discovery, Market Posture,
    or the Daily Entry Validator
  - call providers (it operates on already-loaded price frames + a VIX scalar)

The CLI runner ``research/regime_forecast.py`` is responsible for all I/O,
provider/cache fan-out, and artifact persistence.

Design notes:
  - Heuristic, transparent, no ML.
  - Inputs are pandas DataFrames keyed by symbol (date-indexed OHLCV).
  - Probabilities are coarse-rounded (5%-grid) to avoid the appearance of
    predictive precision.
  - Every output carries a confidence label and an explicit invalidation rule.
  - Missing data degrades the relevant feature family without faulting the
    whole report.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

import math


# ── Universe constants ──────────────────────────────────────────────────────

MARKET_ETFS: Tuple[str, ...] = ("SPY", "QQQ", "IWM", "DIA")

SECTOR_ETFS: Tuple[str, ...] = (
    "XLK", "XLF", "XLV", "XLE", "XLY",
    "XLI", "XLP", "XLU", "XLB", "XLRE", "XLC",
)

SECTOR_NAMES: Dict[str, str] = {
    "XLK": "Technology",
    "XLF": "Financials",
    "XLV": "Health Care",
    "XLE": "Energy",
    "XLY": "Consumer Discretionary",
    "XLI": "Industrials",
    "XLP": "Consumer Staples",
    "XLU": "Utilities",
    "XLB": "Materials",
    "XLRE": "Real Estate",
    "XLC": "Communication Services",
}

DEFENSIVE_SECTORS: frozenset = frozenset({"XLP", "XLU", "XLV", "XLRE"})

RISK_PROXIES: Tuple[str, ...] = ("TLT", "HYG", "LQD", "VXX")

ACTIVE_STRATEGIES: Tuple[str, ...] = (
    "VOYAGER",
    "SNIPER_V6",
    "SHORT_A",
    "ALPHA_DISCOVERY",
)

REGIME_CLASSES: Tuple[str, ...] = (
    "Bull Continuation",
    "Bull Pullback / Buy-the-Dip",
    "Chop / Range",
    "Risk-Off",
    "Volatility Expansion / Stress",
    "Bear Rally / Unstable Rebound",
)

VERSION = "REGIME_FORECASTER_V1"


# ── Numeric helpers ─────────────────────────────────────────────────────────


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (TypeError, ValueError):
        return default


def _safe_pct(curr: float, prior: float) -> Optional[float]:
    if prior == 0 or prior is None:
        return None
    return (curr / prior - 1.0) * 100.0


def _round5(p: float) -> float:
    """Round a probability to the nearest 5% to avoid fake precision."""
    if p <= 0:
        return 0.0
    return round(p * 20) / 20.0


def _normalize_probs(scores: Dict[str, float]) -> Dict[str, float]:
    """Normalize positive scores to probabilities, then 5%-snap, then re-fix."""
    pos = {k: max(0.0, float(v)) for k, v in scores.items()}
    total = sum(pos.values())
    if total <= 0:
        n = len(pos) or 1
        return {k: round(1.0 / n, 2) for k in pos}
    raw = {k: v / total for k, v in pos.items()}
    snapped = {k: _round5(v) for k, v in raw.items()}
    # Patch rounding drift onto the largest bucket so totals stay ~1.0 (snap
    # threshold is 5% but float arithmetic can make a clean 0.05 read as
    # 0.04999…, so the correction triggers from a half-step).
    drift = 1.0 - sum(snapped.values())
    if abs(drift) >= 0.025 and snapped:
        top = max(snapped, key=snapped.get)
        snapped[top] = max(0.0, round(snapped[top] + _round5(drift), 2))
    return snapped


# ── Frame helpers ───────────────────────────────────────────────────────────


def _close_series(frame: Any) -> Optional[List[float]]:
    """Return close prices as a plain list (oldest-first), or None."""
    if frame is None:
        return None
    try:
        if hasattr(frame, "columns"):  # pandas DataFrame
            if "close" in frame.columns:
                series = list(frame["close"].astype(float).values)
            elif "Close" in frame.columns:
                series = list(frame["Close"].astype(float).values)
            else:
                return None
        else:
            series = [float(x) for x in frame]
        series = [float(x) for x in series if x == x]  # drop NaN
        return series or None
    except Exception:
        return None


def _last(series: List[float]) -> Optional[float]:
    return series[-1] if series else None


def _ma(series: List[float], window: int) -> Optional[float]:
    if not series or len(series) < window:
        return None
    sub = series[-window:]
    return sum(sub) / len(sub)


def _ret(series: List[float], lookback: int) -> Optional[float]:
    if not series or len(series) <= lookback:
        return None
    prior = series[-(lookback + 1)]
    curr = series[-1]
    return _safe_pct(curr, prior)


def _max_close(series: List[float], window: int) -> Optional[float]:
    if not series:
        return None
    sub = series[-window:] if len(series) >= window else series
    return max(sub) if sub else None


def _min_close(series: List[float], window: int) -> Optional[float]:
    if not series:
        return None
    sub = series[-window:] if len(series) >= window else series
    return min(sub) if sub else None


def _realized_vol(series: List[float], window: int = 20) -> Optional[float]:
    """Annualized realized vol from log returns (percent)."""
    if not series or len(series) < window + 1:
        return None
    sub = series[-(window + 1):]
    rets = []
    for prev, curr in zip(sub[:-1], sub[1:]):
        if prev > 0 and curr > 0:
            rets.append(math.log(curr / prev))
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(252) * 100.0


# ── Feature: market trend ───────────────────────────────────────────────────


def market_trend_features(frames: Dict[str, Any]) -> Dict[str, Any]:
    """Compute trend features for SPY/QQQ/IWM/DIA."""
    out: Dict[str, Any] = {}
    for sym in MARKET_ETFS:
        series = _close_series(frames.get(sym))
        if not series:
            out[sym] = {"available": False}
            continue
        last = _last(series)
        ma20, ma50, ma200 = _ma(series, 20), _ma(series, 50), _ma(series, 200)
        hi_60 = _max_close(series, 60)
        lo_60 = _min_close(series, 60)
        out[sym] = {
            "available": True,
            "bars": len(series),
            "last": last,
            "return_5d_pct": _ret(series, 5),
            "return_10d_pct": _ret(series, 10),
            "return_20d_pct": _ret(series, 20),
            "ma20": ma20,
            "ma50": ma50,
            "ma200": ma200,
            "above_ma20": (last is not None and ma20 is not None and last > ma20),
            "above_ma50": (last is not None and ma50 is not None and last > ma50),
            "above_ma200": (last is not None and ma200 is not None and last > ma200),
            "pct_from_60d_high": (
                _safe_pct(last, hi_60) if (last is not None and hi_60) else None
            ),
            "pct_from_60d_low": (
                _safe_pct(last, lo_60) if (last is not None and lo_60) else None
            ),
        }

    spy_close = _close_series(frames.get("SPY"))
    qqq_close = _close_series(frames.get("QQQ"))
    iwm_close = _close_series(frames.get("IWM"))

    def _rs_trend(num: Optional[List[float]], den: Optional[List[float]]) -> Optional[float]:
        if not num or not den:
            return None
        n = min(len(num), len(den), 25)
        if n < 6:
            return None
        nums = num[-n:]
        dens = den[-n:]
        try:
            curr = nums[-1] / dens[-1]
            prior = nums[-5] / dens[-5]
            if prior <= 0:
                return None
            return (curr / prior - 1.0) * 100.0
        except Exception:
            return None

    out["relative_strength"] = {
        "qqq_vs_spy_5d_pct": _rs_trend(qqq_close, spy_close),
        "iwm_vs_spy_5d_pct": _rs_trend(iwm_close, spy_close),
    }
    return out


# ── Feature: sector rotation ────────────────────────────────────────────────


def sector_rotation_features(frames: Dict[str, Any]) -> Dict[str, Any]:
    """Compute relative-strength + state for each sector ETF (vs SPY)."""
    spy = _close_series(frames.get("SPY"))
    rows: List[Dict[str, Any]] = []
    for sym in SECTOR_ETFS:
        series = _close_series(frames.get(sym))
        if not series:
            rows.append({
                "sector": sym,
                "name": SECTOR_NAMES[sym],
                "available": False,
                "state": "Unknown",
            })
            continue

        def _rs(lb: int) -> Optional[float]:
            if not spy or len(spy) <= lb or len(series) <= lb:
                return None
            try:
                sec_ret = series[-1] / series[-(lb + 1)] - 1.0
                spy_ret = spy[-1] / spy[-(lb + 1)] - 1.0
                return (sec_ret - spy_ret) * 100.0
            except Exception:
                return None

        last = _last(series)
        ma20 = _ma(series, 20)
        ma50 = _ma(series, 50)
        rs5 = _rs(5)
        rs10 = _rs(10)
        rs20 = _rs(20)

        rows.append({
            "sector": sym,
            "name": SECTOR_NAMES[sym],
            "available": True,
            "bars": len(series),
            "last": last,
            "rs_5d_pct": rs5,
            "rs_10d_pct": rs10,
            "rs_20d_pct": rs20,
            "above_ma20": (last is not None and ma20 is not None and last > ma20),
            "above_ma50": (last is not None and ma50 is not None and last > ma50),
            "is_defensive": sym in DEFENSIVE_SECTORS,
        })

    # Rank by 10d RS (preferring 20d if 10d unavailable for a row).
    def _rank_key(r: Dict[str, Any]) -> float:
        if not r.get("available"):
            return -999.0
        v = r.get("rs_10d_pct")
        if v is None:
            v = r.get("rs_20d_pct")
        return _f(v, -999.0)

    ranked = sorted(rows, key=_rank_key, reverse=True)
    available_count = sum(1 for r in ranked if r.get("available"))
    for i, r in enumerate(ranked):
        r["rank"] = (i + 1) if r.get("available") else None
        r["of_total"] = available_count
        # Sector state classification.
        if not r.get("available"):
            continue
        rs5 = _f(r.get("rs_5d_pct"))
        rs10 = _f(r.get("rs_10d_pct"))
        rs20 = _f(r.get("rs_20d_pct"))
        above50 = bool(r.get("above_ma50"))
        improving = (rs5 - rs20) > 0.5
        weakening = (rs5 - rs20) < -0.5

        if rs10 >= 1.5 and above50 and not weakening:
            state = "Leading"
        elif rs10 >= 0.0 and improving:
            state = "Improving"
        elif rs10 <= -1.5 and not above50:
            state = "Defensive" if r.get("is_defensive") else "Weakening"
        elif rs10 <= -0.5 and weakening:
            state = "Weakening"
        else:
            state = "Neutral"
        r["state"] = state

    leading = [r["sector"] for r in ranked if r.get("state") == "Leading"]
    improving = [r["sector"] for r in ranked if r.get("state") == "Improving"]
    weakening = [r["sector"] for r in ranked if r.get("state") == "Weakening"]
    defensive = [r["sector"] for r in ranked if r.get("state") == "Defensive"]
    return {
        "rows": ranked,
        "leading": leading,
        "improving": improving,
        "weakening": weakening,
        "defensive": defensive,
        "available_count": available_count,
    }


# ── Feature: volatility / risk ──────────────────────────────────────────────


def volatility_features(
    frames: Dict[str, Any],
    vix: Optional[float],
    vix_history: Optional[List[float]] = None,
) -> Dict[str, Any]:
    """VIX level + change, plus realized vol on SPY, plus VXX trend."""
    spy = _close_series(frames.get("SPY"))
    rv20 = _realized_vol(spy or [], 20) if spy else None

    vix_avg_20 = None
    vix_chg_5 = None
    vix_chg_10 = None
    if vix_history and len(vix_history) >= 6:
        sub = [float(x) for x in vix_history if x is not None]
        if sub:
            tail = sub[-20:] if len(sub) >= 20 else sub
            vix_avg_20 = sum(tail) / len(tail)
            if len(sub) >= 6:
                vix_chg_5 = sub[-1] - sub[-6]
            if len(sub) >= 11:
                vix_chg_10 = sub[-1] - sub[-11]

    vxx = _close_series(frames.get("VXX"))
    vxx_5d = _ret(vxx or [], 5) if vxx else None
    vxx_above_ma20 = None
    if vxx:
        ma20 = _ma(vxx, 20)
        last = _last(vxx)
        if last is not None and ma20 is not None:
            vxx_above_ma20 = last > ma20

    return {
        "vix": _f(vix) if vix is not None else None,
        "vix_available": vix is not None,
        "vix_avg_20": vix_avg_20,
        "vix_change_5d": vix_chg_5,
        "vix_change_10d": vix_chg_10,
        "spy_realized_vol_20d_ann": rv20,
        "vxx_5d_pct": vxx_5d,
        "vxx_above_ma20": vxx_above_ma20,
    }


# ── Feature: credit / rates / risk-appetite proxies ─────────────────────────


def credit_rates_features(frames: Dict[str, Any]) -> Dict[str, Any]:
    """HYG vs LQD, TLT trend, IWM/SPY risk appetite."""
    out: Dict[str, Any] = {}

    for sym in ("TLT", "HYG", "LQD"):
        series = _close_series(frames.get(sym))
        if not series:
            out[sym] = {"available": False}
            continue
        last = _last(series)
        ma20 = _ma(series, 20)
        ma50 = _ma(series, 50)
        out[sym] = {
            "available": True,
            "last": last,
            "return_5d_pct": _ret(series, 5),
            "return_20d_pct": _ret(series, 20),
            "above_ma20": (last is not None and ma20 is not None and last > ma20),
            "above_ma50": (last is not None and ma50 is not None and last > ma50),
        }

    hyg = _close_series(frames.get("HYG"))
    lqd = _close_series(frames.get("LQD"))
    spy = _close_series(frames.get("SPY"))
    iwm = _close_series(frames.get("IWM"))

    def _ratio_trend(num: Optional[List[float]], den: Optional[List[float]], lb: int) -> Optional[float]:
        if not num or not den:
            return None
        if len(num) <= lb or len(den) <= lb:
            return None
        try:
            curr = num[-1] / den[-1]
            prior = num[-(lb + 1)] / den[-(lb + 1)]
            if prior <= 0:
                return None
            return (curr / prior - 1.0) * 100.0
        except Exception:
            return None

    out["hyg_lqd_ratio_5d_pct"] = _ratio_trend(hyg, lqd, 5)
    out["hyg_lqd_ratio_20d_pct"] = _ratio_trend(hyg, lqd, 20)
    out["iwm_spy_ratio_5d_pct"] = _ratio_trend(iwm, spy, 5)
    out["iwm_spy_ratio_20d_pct"] = _ratio_trend(iwm, spy, 20)

    appetite_score = 0
    appetite_signals: List[str] = []
    hyg_lqd_5 = out["hyg_lqd_ratio_5d_pct"]
    iwm_spy_5 = out["iwm_spy_ratio_5d_pct"]
    if hyg_lqd_5 is not None:
        if hyg_lqd_5 > 0.3:
            appetite_score += 1
            appetite_signals.append("credit risk-on (HYG>LQD)")
        elif hyg_lqd_5 < -0.3:
            appetite_score -= 1
            appetite_signals.append("credit risk-off (HYG<LQD)")
    if iwm_spy_5 is not None:
        if iwm_spy_5 > 0.5:
            appetite_score += 1
            appetite_signals.append("small-cap leadership")
        elif iwm_spy_5 < -0.5:
            appetite_score -= 1
            appetite_signals.append("small-cap lag")
    tlt_info = out.get("TLT") or {}
    if tlt_info.get("available"):
        tlt_ret_5 = tlt_info.get("return_5d_pct")
        if tlt_ret_5 is not None and tlt_ret_5 > 1.5:
            appetite_score -= 1
            appetite_signals.append("bond bid (TLT up)")

    if appetite_score >= 2:
        appetite_label = "risk-on"
    elif appetite_score <= -2:
        appetite_label = "risk-off"
    elif appetite_score == 1:
        appetite_label = "leaning risk-on"
    elif appetite_score == -1:
        appetite_label = "leaning risk-off"
    else:
        appetite_label = "neutral"
    out["risk_appetite"] = {
        "score": appetite_score,
        "label": appetite_label,
        "signals": appetite_signals,
    }
    return out


# ── Feature: breadth (optional, degrades gracefully) ────────────────────────


def breadth_features(
    frames: Dict[str, Any],
    universe_snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Two breadth lenses:
      1. Sector breadth  — % of sector ETFs above 20d/50d MA (always available
         when at least one sector frame is present).
      2. Universe breadth — % of strategy_candidates above 20d/50d/200d MA, if
         the dashboard's universe snapshot has been provided.  Degrades to
         ``available=False`` when the snapshot lacks the relevant fields.
    """
    sector_above_20 = 0
    sector_above_50 = 0
    sector_total = 0
    for sym in SECTOR_ETFS:
        series = _close_series(frames.get(sym))
        if not series:
            continue
        sector_total += 1
        last = _last(series)
        ma20 = _ma(series, 20)
        ma50 = _ma(series, 50)
        if last is not None and ma20 is not None and last > ma20:
            sector_above_20 += 1
        if last is not None and ma50 is not None and last > ma50:
            sector_above_50 += 1
    sector_breadth_20 = sector_above_20 / sector_total if sector_total else None
    sector_breadth_50 = sector_above_50 / sector_total if sector_total else None

    universe_breadth: Dict[str, Any] = {"available": False}
    snap = universe_snapshot or {}
    rows = snap.get("strategy_candidates") or []
    if rows:
        ok20 = ok50 = ok200 = 0
        seen20 = seen50 = seen200 = 0
        for row in rows:
            ma20_flag = row.get("above_ma20") if "above_ma20" in row else None
            ma50_flag = row.get("above_ma50") if "above_ma50" in row else None
            ma200_flag = row.get("above_ma200") if "above_ma200" in row else None
            if ma20_flag is not None:
                seen20 += 1
                if ma20_flag:
                    ok20 += 1
            if ma50_flag is not None:
                seen50 += 1
                if ma50_flag:
                    ok50 += 1
            if ma200_flag is not None:
                seen200 += 1
                if ma200_flag:
                    ok200 += 1
        if seen20 or seen50 or seen200:
            universe_breadth = {
                "available": True,
                "rows_seen": len(rows),
                "pct_above_ma20": (ok20 / seen20) if seen20 else None,
                "pct_above_ma50": (ok50 / seen50) if seen50 else None,
                "pct_above_ma200": (ok200 / seen200) if seen200 else None,
            }

    return {
        "sector_breadth_pct_above_ma20": sector_breadth_20,
        "sector_breadth_pct_above_ma50": sector_breadth_50,
        "sector_breadth_total": sector_total,
        "universe_breadth": universe_breadth,
    }


# ── Regime classification (heuristic) ───────────────────────────────────────


def _trend_score(market: Dict[str, Any]) -> float:
    spy = market.get("SPY") or {}
    if not spy.get("available"):
        return 0.0
    score = 0.0
    if spy.get("above_ma200"):
        score += 1.0
    if spy.get("above_ma50"):
        score += 1.0
    if spy.get("above_ma20"):
        score += 1.0
    r5 = spy.get("return_5d_pct")
    r20 = spy.get("return_20d_pct")
    if r5 is not None and r5 > 0:
        score += 0.5
    if r20 is not None and r20 > 0:
        score += 0.5
    return score


def classify_regime(
    market: Dict[str, Any],
    sectors: Dict[str, Any],
    vol: Dict[str, Any],
    credit: Dict[str, Any],
    breadth: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Score each regime using transparent feature aggregations, then convert to
    coarse-rounded probabilities.
    """
    spy = market.get("SPY") or {}
    available = bool(spy.get("available"))
    trend = _trend_score(market)  # 0..5
    vix = vol.get("vix")
    vix_chg5 = vol.get("vix_change_5d")
    rv20 = vol.get("spy_realized_vol_20d_ann")
    sector_breadth = breadth.get("sector_breadth_pct_above_ma50") or 0.0
    appetite = (credit.get("risk_appetite") or {}).get("score", 0)
    leading = len(sectors.get("leading") or [])
    weakening = len(sectors.get("weakening") or [])
    spy_r5 = spy.get("return_5d_pct")
    spy_r10 = spy.get("return_10d_pct")
    spy_r20 = spy.get("return_20d_pct")

    bullish_factors: List[str] = []
    bearish_factors: List[str] = []

    # Bullish factors
    if spy.get("above_ma200"):
        bullish_factors.append("SPY > 200d MA")
    if spy.get("above_ma50"):
        bullish_factors.append("SPY > 50d MA")
    if sector_breadth >= 0.6:
        bullish_factors.append(f"sector breadth {sector_breadth*100:.0f}% > 50d MA")
    if leading >= 3:
        bullish_factors.append(f"{leading} sectors leading")
    if appetite >= 1:
        bullish_factors.append((credit.get("risk_appetite") or {}).get("label", "risk-on"))
    if vix is not None and vix < 18:
        bullish_factors.append(f"VIX low ({vix:.1f})")
    if spy_r20 is not None and spy_r20 > 1.0:
        bullish_factors.append(f"SPY 20d {spy_r20:+.1f}%")

    # Bearish factors
    if spy.get("available") and spy.get("above_ma200") is False:
        bearish_factors.append("SPY < 200d MA")
    if spy.get("available") and spy.get("above_ma50") is False:
        bearish_factors.append("SPY < 50d MA")
    if sector_breadth and sector_breadth <= 0.4:
        bearish_factors.append(f"sector breadth {sector_breadth*100:.0f}% > 50d MA")
    if weakening >= 3:
        bearish_factors.append(f"{weakening} sectors weakening")
    if appetite <= -1:
        bearish_factors.append((credit.get("risk_appetite") or {}).get("label", "risk-off"))
    if vix is not None and vix >= 25:
        bearish_factors.append(f"VIX elevated ({vix:.1f})")
    if vix_chg5 is not None and vix_chg5 >= 4:
        bearish_factors.append(f"VIX +{vix_chg5:.1f} over 5d")
    if rv20 is not None and rv20 >= 25:
        bearish_factors.append(f"realized vol {rv20:.0f}%")
    if spy_r5 is not None and spy_r5 <= -2.5:
        bearish_factors.append(f"SPY 5d {spy_r5:+.1f}%")

    # Heuristic regime scores (0..1 scale before normalization).
    bull_cont = 0.0
    bull_pull = 0.0
    chop = 0.0
    risk_off = 0.0
    vol_exp = 0.0
    bear_rally = 0.0

    if available:
        # --- Bull Continuation ---
        bull_cont += 0.30 * (trend / 5.0)
        if sector_breadth:
            bull_cont += 0.20 * sector_breadth
        if appetite >= 0:
            bull_cont += 0.10 * (1 if appetite >= 0 else 0)
        if vix is not None and vix < 22:
            bull_cont += 0.10
        if spy_r20 is not None and spy_r20 > 0:
            bull_cont += 0.10
        if spy_r5 is not None and spy_r5 > 0 and spy_r10 is not None and spy_r10 > 0:
            bull_cont += 0.10
        if leading >= 3:
            bull_cont += 0.10

        # --- Bull Pullback / Buy-the-Dip ---
        if trend >= 3.0 and (spy_r5 is not None and -3.5 <= spy_r5 < 0):
            bull_pull += 0.35
        if spy.get("above_ma200") and spy.get("above_ma50") and spy_r5 is not None and spy_r5 < 0:
            bull_pull += 0.20
        if vix is not None and 15 <= vix < 25:
            bull_pull += 0.15
        if sector_breadth and sector_breadth >= 0.45:
            bull_pull += 0.10
        if appetite >= 0:
            bull_pull += 0.10

        # --- Chop / Range ---
        if 1.5 <= trend <= 3.5:
            chop += 0.25
        if spy_r20 is not None and -2.0 <= spy_r20 <= 2.0:
            chop += 0.20
        if spy_r5 is not None and -2.0 <= spy_r5 <= 2.0:
            chop += 0.10
        if vix is not None and 15 <= vix < 22:
            chop += 0.10
        if 0.4 <= (sector_breadth or 0.0) <= 0.6:
            chop += 0.15
        if -1 <= appetite <= 1:
            chop += 0.10

        # --- Risk-Off ---
        if trend <= 2.0:
            risk_off += 0.20
        if (sector_breadth or 0.0) <= 0.4:
            risk_off += 0.15
        if appetite <= -1:
            risk_off += 0.15
        if spy_r10 is not None and spy_r10 <= -2.0:
            risk_off += 0.15
        if weakening >= 3:
            risk_off += 0.10
        if vix is not None and vix >= 22:
            risk_off += 0.10
        if (credit.get("TLT") or {}).get("available") and (credit.get("TLT") or {}).get("return_5d_pct") and credit["TLT"]["return_5d_pct"] > 1.5:
            risk_off += 0.10

        # --- Volatility Expansion / Stress ---
        if vix is not None and vix >= 25:
            vol_exp += 0.30
        if vix_chg5 is not None and vix_chg5 >= 5:
            vol_exp += 0.25
        if rv20 is not None and rv20 >= 25:
            vol_exp += 0.15
        if vol.get("vxx_above_ma20"):
            vol_exp += 0.10
        if spy_r5 is not None and spy_r5 <= -3.5:
            vol_exp += 0.15
        if appetite <= -2:
            vol_exp += 0.05

        # --- Bear Rally / Unstable Rebound ---
        if spy.get("above_ma200") is False and spy_r5 is not None and spy_r5 > 1.5:
            bear_rally += 0.35
        if spy.get("above_ma200") is False and spy_r10 is not None and spy_r10 > 0:
            bear_rally += 0.15
        if vix is not None and vix >= 22:
            bear_rally += 0.10
        if (sector_breadth or 0.0) < 0.5 and spy_r5 is not None and spy_r5 > 0:
            bear_rally += 0.10
        if appetite <= 0 and spy_r5 is not None and spy_r5 > 0:
            bear_rally += 0.05
    else:
        # No SPY data — fall back to a mostly-uniform prior with a chop tilt.
        bull_cont = 0.10
        bull_pull = 0.10
        chop = 0.40
        risk_off = 0.15
        vol_exp = 0.15
        bear_rally = 0.10

    raw = {
        "Bull Continuation": bull_cont,
        "Bull Pullback / Buy-the-Dip": bull_pull,
        "Chop / Range": chop,
        "Risk-Off": risk_off,
        "Volatility Expansion / Stress": vol_exp,
        "Bear Rally / Unstable Rebound": bear_rally,
    }
    probs = _normalize_probs(raw)
    current = max(probs.items(), key=lambda kv: kv[1])[0] if probs else "Chop / Range"

    # Bias labels for 5d / 10d horizons — driven by the dominant probability
    # mass split into "constructive" / "defensive" / "neutral" buckets, not by
    # forward returns (we make no forward claim here, only a directional lean).
    constructive_mass = probs.get("Bull Continuation", 0) + probs.get("Bull Pullback / Buy-the-Dip", 0)
    defensive_mass = probs.get("Risk-Off", 0) + probs.get("Volatility Expansion / Stress", 0) + probs.get("Bear Rally / Unstable Rebound", 0)
    chop_mass = probs.get("Chop / Range", 0)

    def _bias(constructive: float, defensive: float, chop: float) -> str:
        if constructive >= defensive + 0.20 and constructive >= chop:
            return "constructive"
        if defensive >= constructive + 0.20:
            return "defensive"
        if chop >= max(constructive, defensive):
            return "neutral / chop"
        return "mixed"

    bias_5d = _bias(constructive_mass, defensive_mass, chop_mass)
    # 10d bias decays toward chop in higher-vol environments; otherwise mirrors 5d.
    if vix is not None and vix >= 25:
        bias_10d = "defensive" if defensive_mass > 0.30 else "neutral / chop"
    else:
        bias_10d = bias_5d

    # Confidence: based on top-probability margin and data availability.
    sorted_probs = sorted(probs.values(), reverse=True)
    margin = (sorted_probs[0] - sorted_probs[1]) if len(sorted_probs) >= 2 else 0.0
    if not available:
        confidence = "low"
    elif margin >= 0.20 and (sector_breadth is not None) and (vix is not None):
        confidence = "high"
    elif margin >= 0.10:
        confidence = "medium"
    else:
        confidence = "low"

    invalidation = _build_invalidation(current, market, vol, sectors)
    inv_status = _evaluate_invalidation(current, market, vol, sectors)
    # When the current regime's own invalidation conditions are already
    # breached on this snapshot, the headline is internally contradictory
    # (classifier still picks the regime by weighted vote, but its defining
    # rule has flipped).  Cap confidence at "low" so downstream consumers
    # don't read a "high" confidence breach as actionable.
    if inv_status["breached"] and confidence != "low":
        confidence = "low"

    return {
        "current_regime": current,
        "bias_5d": bias_5d,
        "bias_10d": bias_10d,
        "confidence": confidence,
        "main_invalidation": invalidation,
        "invalidation_breached": inv_status["breached"],
        "invalidation_breach_reasons": inv_status["breach_reasons"],
        "regime_probabilities": [
            {"regime": r, "probability": probs.get(r, 0.0)}
            for r in REGIME_CLASSES
        ],
        "trend_score": round(trend, 2),
        "constructive_mass": round(constructive_mass, 2),
        "defensive_mass": round(defensive_mass, 2),
        "factor_contributions": {
            "bullish": bullish_factors[:6],
            "bearish": bearish_factors[:6],
        },
    }


def _build_invalidation(
    regime: str,
    market: Dict[str, Any],
    vol: Dict[str, Any],
    sectors: Dict[str, Any],
) -> str:
    """Return one short, concrete invalidation rule per regime."""
    spy = market.get("SPY") or {}
    ma50 = spy.get("ma50")
    ma200 = spy.get("ma200")
    last = spy.get("last")
    ma50_str = f"{ma50:.2f}" if ma50 else "—"
    ma200_str = f"{ma200:.2f}" if ma200 else "—"
    last_str = f"{last:.2f}" if last else "—"
    vix = vol.get("vix")
    vix_str = f"{vix:.1f}" if vix is not None else "n/a"
    leading = len(sectors.get("leading") or [])

    if regime == "Bull Continuation":
        return (
            f"SPY {last_str} closes below 50d MA {ma50_str}, VIX > 22 (now {vix_str}), "
            f"or leading sectors fall below 2 (now {leading})."
        )
    if regime == "Bull Pullback / Buy-the-Dip":
        return (
            f"SPY closes below 200d MA {ma200_str} on closing basis, or VIX > 25 (now {vix_str})."
        )
    if regime == "Chop / Range":
        return (
            "SPY closes >2% above or below the 20d range with rising VIX, "
            "or sector breadth flips beyond 60% / 40%."
        )
    if regime == "Risk-Off":
        return (
            f"SPY recovers above 50d MA {ma50_str} with VIX < 20 (now {vix_str}), "
            f"or 3+ sectors flip back to Improving/Leading."
        )
    if regime == "Volatility Expansion / Stress":
        return (
            f"VIX < 20 for two consecutive closes (now {vix_str}) and SPY > 20d MA."
        )
    if regime == "Bear Rally / Unstable Rebound":
        return (
            f"SPY reclaims 200d MA {ma200_str} with sector breadth > 55% and VIX < 22."
        )
    return "regime-specific invalidation unavailable"


def _evaluate_invalidation(
    regime: str,
    market: Dict[str, Any],
    vol: Dict[str, Any],
    sectors: Dict[str, Any],
) -> Dict[str, Any]:
    """Check whether the current regime's invalidation conditions are already
    breached on the snapshot used to build the text rule.

    Returns ``{"breached": bool, "breach_reasons": [str]}``.  Only conditions
    that can be evaluated from a single snapshot are checked; temporal
    conditions like "two consecutive closes" are skipped, as are the soft
    range-style rules for Chop / Range.
    """
    spy = market.get("SPY") or {}
    last = spy.get("last")
    ma50 = spy.get("ma50")
    ma200 = spy.get("ma200")
    vix = vol.get("vix")
    leading = len(sectors.get("leading") or [])
    reasons: List[str] = []

    if regime == "Bull Continuation":
        if last is not None and ma50 is not None and last < ma50:
            reasons.append(f"SPY {last:.2f} below 50d MA {ma50:.2f}")
        if vix is not None and vix > 22:
            reasons.append(f"VIX {vix:.1f} > 22")
        if leading < 2:
            reasons.append(f"leading sectors {leading} < 2")
    elif regime == "Bull Pullback / Buy-the-Dip":
        if last is not None and ma200 is not None and last < ma200:
            reasons.append(f"SPY {last:.2f} below 200d MA {ma200:.2f}")
        if vix is not None and vix > 25:
            reasons.append(f"VIX {vix:.1f} > 25")
    elif regime == "Risk-Off":
        # Risk-Off invalidates when SPY reclaims 50d MA *and* VIX < 20.
        if (last is not None and ma50 is not None and last > ma50
                and vix is not None and vix < 20):
            reasons.append(
                f"SPY {last:.2f} above 50d MA {ma50:.2f} and VIX {vix:.1f} < 20"
            )
    elif regime == "Bear Rally / Unstable Rebound":
        # Bear Rally invalidates when SPY reclaims 200d MA *and* VIX < 22.
        # Breadth condition skipped — sector_breadth isn't passed in here.
        if (last is not None and ma200 is not None and last > ma200
                and vix is not None and vix < 22):
            reasons.append(
                f"SPY {last:.2f} above 200d MA {ma200:.2f} and VIX {vix:.1f} < 22"
            )
    # Chop / Range and Volatility Expansion / Stress: temporal / range
    # conditions, intentionally skipped.

    return {"breached": bool(reasons), "breach_reasons": reasons}


# ── Strategy favorability mapping ───────────────────────────────────────────


def map_strategy_favorability(
    regime: str,
    market: Dict[str, Any],
    sectors: Dict[str, Any],
    vol: Dict[str, Any],
    credit: Dict[str, Any],
    breadth: Dict[str, Any],
) -> Dict[str, Dict[str, str]]:
    spy = market.get("SPY") or {}
    sector_breadth = breadth.get("sector_breadth_pct_above_ma50") or 0.0
    vix = vol.get("vix")
    appetite_label = (credit.get("risk_appetite") or {}).get("label", "neutral")
    leading = len(sectors.get("leading") or [])
    weakening = len(sectors.get("weakening") or [])

    favorability: Dict[str, Dict[str, str]] = {}

    # ---- VOYAGER (trend / accumulation) ----
    if regime in {"Bull Continuation", "Bull Pullback / Buy-the-Dip"} and sector_breadth >= 0.5 and (vix or 0) < 22:
        v_stance = "favored"
        v_reason = "broad trend healthy, breadth supportive, volatility contained"
    elif regime in {"Chop / Range"} or (vix and vix >= 22):
        v_stance = "selective"
        v_reason = "trend mixed or volatility elevated; require confirmation"
    elif regime in {"Risk-Off", "Volatility Expansion / Stress"}:
        v_stance = "avoid"
        v_reason = "broad trend not supportive; accumulation likely premature"
    else:
        v_stance = "allowed"
        v_reason = "no strong tailwind, no veto"
    favorability["VOYAGER"] = {
        "stance": v_stance,
        "reason": v_reason,
        "invalidation": "VIX > 25, SPY < 50d MA, or sector breadth < 40%",
    }

    # ---- SNIPER_V6 (breakouts) ----
    if regime == "Bull Continuation" and (vix or 0) < 20 and leading >= 3:
        s_stance = "favored"
        s_reason = "breakouts supported by leading sectors and contained volatility"
    elif regime == "Bull Pullback / Buy-the-Dip":
        s_stance = "selective"
        s_reason = "wait for confirmation; pullbacks can absorb breakouts"
    elif regime == "Chop / Range":
        s_stance = "selective"
        s_reason = "breakouts often fail in range; require confirmation"
    elif regime in {"Risk-Off", "Volatility Expansion / Stress", "Bear Rally / Unstable Rebound"}:
        s_stance = "avoid"
        s_reason = "expansion-style entries face elevated failure risk"
    else:
        s_stance = "allowed"
        s_reason = "neutral context"
    favorability["SNIPER_V6"] = {
        "stance": s_stance,
        "reason": s_reason,
        "invalidation": "VIX > 22, leading sectors < 2, or 5d SPY < -2%",
    }

    # ---- SHORT_A (short-side) ----
    if regime in {"Risk-Off", "Volatility Expansion / Stress"}:
        sh_stance = "favored"
        sh_reason = "weakness, breadth contraction, and expanding volatility"
    elif regime == "Bear Rally / Unstable Rebound" and weakening >= 2:
        sh_stance = "selective"
        sh_reason = "rebounds inside a damaged tape can offer short re-entries"
    elif regime in {"Bull Continuation", "Bull Pullback / Buy-the-Dip"} and sector_breadth >= 0.55:
        sh_stance = "avoid"
        sh_reason = "broad bid is hostile to short setups"
    else:
        sh_stance = "selective"
        sh_reason = "context mixed; require name-level weakness"
    favorability["SHORT_A"] = {
        "stance": sh_stance,
        "reason": sh_reason,
        "invalidation": (
            "VIX < 18 for two consecutive closes, SPY back above 50d MA, "
            "or 3+ sectors flip to Leading"
        ),
    }

    # ---- ALPHA_DISCOVERY (research board, not a sleeve) ----
    if regime == "Bull Pullback / Buy-the-Dip":
        a_stance = "favored"
        a_reason = "pullback context favors discovering quality names; research only"
    elif regime == "Bull Continuation":
        a_stance = "selective"
        a_reason = "avoid chasing extended names; favor pullback candidates"
    elif regime == "Chop / Range":
        a_stance = "selective"
        a_reason = "research board still useful for stalking; don't expect breakouts"
    elif regime in {"Risk-Off", "Volatility Expansion / Stress"}:
        a_stance = "selective"
        a_reason = "stalk only; do not promote ideas during stress"
    elif regime == "Bear Rally / Unstable Rebound":
        a_stance = "avoid"
        a_reason = "research board signal-quality is poor in unstable rebounds"
    else:
        a_stance = "allowed"
        a_reason = "neutral context"
    favorability["ALPHA_DISCOVERY"] = {
        "stance": a_stance,
        "reason": a_reason,
        "invalidation": (
            "research stance changes only when the regime classification changes; "
            "this mapping does not affect Alpha Discovery scoring"
        ),
    }
    return favorability


# ── Top-level entrypoint ────────────────────────────────────────────────────


def build_forecast(
    *,
    frames: Dict[str, Any],
    vix: Optional[float] = None,
    vix_history: Optional[List[float]] = None,
    universe_snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Build the Phase 1 regime forecast from already-loaded inputs.

    Parameters
    ----------
    frames : dict[str, pandas.DataFrame]
        Date-indexed OHLCV frames keyed by symbol.  Missing or empty frames
        cause the relevant feature family to degrade gracefully.
    vix : float, optional
        Latest VIX close.  When omitted, the volatility family is partially
        degraded but the report still builds.
    vix_history : list[float], optional
        Trailing VIX closes (oldest-first), used for change/avg features.
    universe_snapshot : dict, optional
        Dashboard universe snapshot.  Used only as an optional breadth proxy.
    """
    market = market_trend_features(frames)
    sectors = sector_rotation_features(frames)
    vol = volatility_features(frames, vix, vix_history)
    credit = credit_rates_features(frames)
    breadth = breadth_features(frames, universe_snapshot)
    regime = classify_regime(market, sectors, vol, credit, breadth)
    favorability = map_strategy_favorability(
        regime["current_regime"], market, sectors, vol, credit, breadth
    )

    missing_layers: List[str] = []
    if not (market.get("SPY") or {}).get("available"):
        missing_layers.append("SPY price history")
    if not vol.get("vix_available"):
        missing_layers.append("VIX level")
    if not vol.get("vix_change_5d") and not vol.get("vix_change_10d"):
        missing_layers.append("VIX history")
    if (sectors.get("available_count") or 0) < len(SECTOR_ETFS):
        missing_layers.append(
            f"{len(SECTOR_ETFS) - (sectors.get('available_count') or 0)} sector ETF frames"
        )
    if not (breadth.get("universe_breadth") or {}).get("available"):
        missing_layers.append("universe-level breadth (snapshot)")

    return {
        "version": VERSION,
        "phase": 1,
        "headline": {
            "current_regime": regime["current_regime"],
            "bias_5d": regime["bias_5d"],
            "bias_10d": regime["bias_10d"],
            "confidence": regime["confidence"],
            "main_invalidation": regime["main_invalidation"],
            "invalidation_breached": regime.get("invalidation_breached", False),
            "invalidation_breach_reasons": regime.get("invalidation_breach_reasons", []),
        },
        "regime_probabilities": regime["regime_probabilities"],
        "trend_score": regime["trend_score"],
        "constructive_mass": regime["constructive_mass"],
        "defensive_mass": regime["defensive_mass"],
        "market_trend": market,
        "sector_rotation": sectors,
        "volatility": vol,
        "credit_rates": credit,
        "breadth": breadth,
        "strategy_favorability": favorability,
        "factor_contributions": regime["factor_contributions"],
        "data_quality": {
            "missing_layers": missing_layers,
            "spy_bars": (market.get("SPY") or {}).get("bars", 0),
            "sector_frames_available": sectors.get("available_count", 0),
            "vix_history_points": len(vix_history) if vix_history else 0,
        },
    }

"""
core/stock_research_lens.py — Single-stock research lens (Phase 3, V1).

Pure-Python feature builders + transparent layer scoring + label resolver.
Combines all available research layers (regime, sector, technicals, daily
entry validator, Alpha Discovery, Market Posture, options, social arb,
13F) into one honest 5d / 10d / 20d view per ticker.

This module is research-only.  It does NOT:
  - approve or block trades
  - emit paper evidence
  - touch governance, execution, sleeve logic, Alpha Discovery scoring,
    Market Posture logic, the Daily Entry Validator's logic, or Social Arb
    scoring
  - call providers (the caller is responsible for all I/O and provider
    fan-out; this module operates on already-loaded inputs)

Inputs are intentionally permissive: any layer that is not provided is
labelled ``available=False`` and excluded from the composite score (with a
reweighting so the remaining layers still sum to 1.0).  We never
hallucinate missing layers.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


VERSION = "STOCK_RESEARCH_LENS_V1"


# ── Sector mapping ──────────────────────────────────────────────────────────

# FMP sector / industry name → SPDR sector ETF.
SECTOR_TO_ETF: Dict[str, str] = {
    "technology": "XLK",
    "information technology": "XLK",
    "financial services": "XLF",
    "financials": "XLF",
    "healthcare": "XLV",
    "health care": "XLV",
    "energy": "XLE",
    "consumer cyclical": "XLY",
    "consumer discretionary": "XLY",
    "industrials": "XLI",
    "industrial goods": "XLI",
    "consumer defensive": "XLP",
    "consumer staples": "XLP",
    "utilities": "XLU",
    "basic materials": "XLB",
    "materials": "XLB",
    "real estate": "XLRE",
    "communication services": "XLC",
    "communications": "XLC",
}

ETF_TO_SECTOR_NAME: Dict[str, str] = {
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


def map_sector_to_etf(sector: Optional[str], industry: Optional[str] = None) -> Optional[str]:
    """Return the SPDR sector ETF for an FMP sector / industry string."""
    for value in (sector, industry):
        if not value:
            continue
        key = str(value).strip().lower()
        etf = SECTOR_TO_ETF.get(key)
        if etf:
            return etf
    return None


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
    if prior is None or prior == 0:
        return None
    return (curr / prior - 1.0) * 100.0


def _close_series(frame: Any) -> Optional[List[float]]:
    if frame is None:
        return None
    try:
        if hasattr(frame, "columns"):
            if "close" in frame.columns:
                series = list(frame["close"].astype(float).values)
            elif "Close" in frame.columns:
                series = list(frame["Close"].astype(float).values)
            else:
                return None
        else:
            series = [float(x) for x in frame]
        series = [float(x) for x in series if x == x]
        return series or None
    except Exception:
        return None


def _ohlcv_records(frame: Any, lookback: int = 260) -> List[Dict[str, Any]]:
    """Convert a date-indexed OHLCV DataFrame to the dict-bars list used by
    ``core.daily_entry_validator.validate_daily_entry``."""
    if frame is None:
        return []
    try:
        if not hasattr(frame, "columns"):
            return []
        sub = frame.tail(lookback)
        rows: List[Dict[str, Any]] = []
        for ts, row in sub.iterrows():
            rows.append({
                "date": ts.strftime("%Y-%m-%d") if hasattr(ts, "strftime") else str(ts),
                "open": _f(row.get("open")),
                "high": _f(row.get("high")),
                "low": _f(row.get("low")),
                "close": _f(row.get("close")),
                "volume": _f(row.get("volume")),
            })
        return rows
    except Exception:
        return []


def _ema(series: Sequence[float], period: int) -> Optional[float]:
    if not series or len(series) < max(2, period // 2):
        return None
    if len(series) < period:
        return sum(series) / len(series)
    k = 2.0 / (period + 1.0)
    ema = sum(series[:period]) / period
    for v in series[period:]:
        ema = (v * k) + (ema * (1.0 - k))
    return ema


def _ma(series: Sequence[float], period: int) -> Optional[float]:
    if not series or len(series) < period:
        return None
    sub = series[-period:]
    return sum(sub) / len(sub)


def _ret_pct(series: Sequence[float], lookback: int) -> Optional[float]:
    if not series or len(series) <= lookback:
        return None
    return _safe_pct(series[-1], series[-(lookback + 1)])


def _atr_pct(bars: Sequence[Dict[str, Any]], period: int = 14) -> Optional[float]:
    if not bars or len(bars) < 2:
        return None
    window = bars[-period:] if len(bars) >= period else bars
    prev_close = _f(window[0].get("close"))
    trs: List[float] = []
    for b in window[1:]:
        high = _f(b.get("high"))
        low = _f(b.get("low"))
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
        prev_close = _f(b.get("close"))
    last = _f(window[-1].get("close"))
    if not trs or last <= 0:
        return None
    return (sum(trs) / len(trs)) / last * 100.0


# ── Technical features ──────────────────────────────────────────────────────


def stock_technical_features(
    ticker: str,
    frame: Any,
    spy_frame: Any,
) -> Dict[str, Any]:
    series = _close_series(frame)
    if not series or len(series) < 30:
        return {"available": False, "reason": "insufficient bars"}
    last = series[-1]
    ema20 = _ema(series, 20)
    ema50 = _ema(series, 50)
    ma200 = _ma(series, 200)
    r5 = _ret_pct(series, 5)
    r10 = _ret_pct(series, 10)
    r20 = _ret_pct(series, 20)

    hi_60 = max(series[-60:]) if len(series) >= 60 else max(series)
    lo_60 = min(series[-60:]) if len(series) >= 60 else min(series)
    pct_from_high = _safe_pct(last, hi_60) if hi_60 else None
    pct_from_low = _safe_pct(last, lo_60) if lo_60 else None

    spy_series = _close_series(spy_frame)
    rs_5 = rs_10 = rs_20 = None
    if spy_series and len(spy_series) > 20:
        try:
            for lb, target in ((5, "rs_5"), (10, "rs_10"), (20, "rs_20")):
                if len(spy_series) > lb and len(series) > lb:
                    s_ret = series[-1] / series[-(lb + 1)] - 1.0
                    m_ret = spy_series[-1] / spy_series[-(lb + 1)] - 1.0
                    rs = (s_ret - m_ret) * 100.0
                    if target == "rs_5":
                        rs_5 = rs
                    elif target == "rs_10":
                        rs_10 = rs
                    else:
                        rs_20 = rs
        except Exception:
            pass

    bars = _ohlcv_records(frame, lookback=260)
    atr_pct = _atr_pct(bars, 14)

    # Volume participation: avg 5d / avg 20d (excluding the last 5).
    vol_ratio = None
    try:
        if frame is not None and hasattr(frame, "columns") and "volume" in frame.columns:
            vols = list(frame["volume"].astype(float).values)
            if len(vols) >= 25:
                last5 = sum(vols[-5:]) / 5.0
                trail20 = sum(vols[-25:-5]) / 20.0
                if trail20 > 0:
                    vol_ratio = last5 / trail20
    except Exception:
        vol_ratio = None

    extended = (ema20 is not None and last > ema20 * 1.06) or (r5 is not None and r5 >= 8)
    pullback = (ema20 is not None and last < ema20 * 0.99 and last >= (ema50 or 0) * 0.97)
    above_50 = (ema50 is not None and last > ema50)
    above_200 = (ma200 is not None and last > ma200)

    if extended:
        state = "extended"
    elif above_50 and above_200 and r20 is not None and r20 > 0:
        state = "trend up"
    elif above_50 and pullback:
        state = "pullback within trend"
    elif (not above_50) and r10 is not None and r10 < -3:
        state = "weakening"
    elif (not above_50) and r5 is not None and r5 > 1.5:
        state = "oversold bounce attempt"
    else:
        state = "neutral"

    return {
        "available": True,
        "bars": len(series),
        "last": last,
        "ema20": ema20,
        "ema50": ema50,
        "ma200": ma200,
        "above_ema20": (ema20 is not None and last > ema20),
        "above_ema50": (ema50 is not None and last > ema50),
        "above_ma200": (ma200 is not None and last > ma200),
        "return_5d_pct": r5,
        "return_10d_pct": r10,
        "return_20d_pct": r20,
        "rs_vs_spy_5d_pct": rs_5,
        "rs_vs_spy_10d_pct": rs_10,
        "rs_vs_spy_20d_pct": rs_20,
        "pct_from_60d_high": pct_from_high,
        "pct_from_60d_low": pct_from_low,
        "atr_pct_14": atr_pct,
        "volume_ratio_5_vs_20": vol_ratio,
        "state": state,
        "extended": extended,
    }


# ── Layer scoring ───────────────────────────────────────────────────────────


def _market_layer(forecast: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not forecast:
        return {"available": False, "view": "Unknown", "score": 0.0,
                "notes": "no current regime forecast artifact"}
    head = forecast.get("headline") or {}
    regime = head.get("current_regime") or "Unknown"
    bias_5d = head.get("bias_5d") or "—"
    confidence = head.get("confidence") or "low"
    probs = {p["regime"]: float(p.get("probability") or 0)
             for p in (forecast.get("regime_probabilities") or [])}
    constructive = probs.get("Bull Continuation", 0) + probs.get("Bull Pullback / Buy-the-Dip", 0)
    defensive = (probs.get("Risk-Off", 0)
                 + probs.get("Volatility Expansion / Stress", 0)
                 + probs.get("Bear Rally / Unstable Rebound", 0))
    score = float(constructive - defensive)  # in [-1, 1] approximately
    if regime in {"Bull Continuation", "Bull Pullback / Buy-the-Dip"}:
        view = "Bullish"
    elif regime in {"Risk-Off", "Volatility Expansion / Stress"}:
        view = "Defensive"
    elif regime == "Bear Rally / Unstable Rebound":
        view = "Unstable"
    else:
        view = "Chop"
    return {
        "available": True,
        "view": view,
        "regime": regime,
        "bias_5d": bias_5d,
        "bias_10d": head.get("bias_10d") or "—",
        "confidence": confidence,
        "score": max(-1.0, min(1.0, score)),
        "constructive_mass": round(constructive, 2),
        "defensive_mass": round(defensive, 2),
        "notes": (
            f"{regime} (conf {confidence})  "
            f"constructive={constructive:.0%} defensive={defensive:.0%}"
        ),
    }


def _sector_layer(
    sector_etf: Optional[str],
    sector_rotation: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    if not sector_etf:
        return {"available": False, "view": "Unknown", "score": 0.0,
                "notes": "sector mapping unavailable"}
    rows = (sector_rotation or {}).get("rows") or []
    target = next((r for r in rows if r.get("sector") == sector_etf), None)
    if not target or not target.get("available"):
        return {
            "available": False, "view": "Unknown", "score": 0.0,
            "etf": sector_etf,
            "notes": f"sector ETF {sector_etf} not in rotation snapshot",
        }
    state = target.get("state") or "Neutral"
    rs5 = _f(target.get("rs_5d_pct"))
    rs10 = _f(target.get("rs_10d_pct"))
    rs20 = _f(target.get("rs_20d_pct"))
    above_50 = bool(target.get("above_ma50"))

    state_score = {
        "Leading": 0.8,
        "Improving": 0.4,
        "Neutral": 0.0,
        "Weakening": -0.5,
        "Defensive": -0.2,
    }.get(state, 0.0)
    rs_component = max(-0.6, min(0.6, rs10 / 5.0))
    score = max(-1.0, min(1.0, state_score * 0.6 + rs_component * 0.4))

    return {
        "available": True,
        "view": state,
        "etf": sector_etf,
        "etf_name": ETF_TO_SECTOR_NAME.get(sector_etf, sector_etf),
        "rs_vs_spy_5d_pct": rs5,
        "rs_vs_spy_10d_pct": rs10,
        "rs_vs_spy_20d_pct": rs20,
        "above_ma50": above_50,
        "score": score,
        "notes": (
            f"{sector_etf} {state}  "
            f"rs10={rs10:+.2f}pp  ma50={'yes' if above_50 else 'no'}"
        ),
    }


def _technicals_layer(tech: Dict[str, Any]) -> Dict[str, Any]:
    if not tech.get("available"):
        return {"available": False, "view": "Unknown", "score": 0.0,
                "notes": tech.get("reason") or "no price history"}
    score = 0.0
    if tech.get("above_ma200"):
        score += 0.20
    if tech.get("above_ema50"):
        score += 0.20
    if tech.get("above_ema20"):
        score += 0.10
    r20 = _f(tech.get("return_20d_pct"))
    if r20 > 1:
        score += 0.10
    elif r20 < -3:
        score -= 0.10
    rs10 = _f(tech.get("rs_vs_spy_10d_pct"))
    if rs10 > 1:
        score += 0.10
    elif rs10 < -2:
        score -= 0.15
    if tech.get("extended"):
        score -= 0.20
    score = max(-1.0, min(1.0, score))

    state = tech.get("state") or "neutral"
    view_map = {
        "trend up": "Bullish",
        "extended": "Bullish but extended",
        "pullback within trend": "Constructive (pullback)",
        "neutral": "Neutral",
        "weakening": "Bearish",
        "oversold bounce attempt": "Bearish but oversold",
    }
    view = view_map.get(state, "Neutral")
    notes = (
        f"close vs ema20 {(tech.get('last') or 0):.2f}/{(tech.get('ema20') or 0):.2f}  "
        f"r20={r20:+.1f}%  rs10={rs10:+.1f}pp  state={state}"
    )
    return {
        "available": True,
        "view": view,
        "state": state,
        "score": score,
        "notes": notes,
        # Pass through raw flags the resolver / risk-score consult.
        "extended": bool(tech.get("extended")),
        "ema20": tech.get("ema20"),
        "ema50": tech.get("ema50"),
        "ma200": tech.get("ma200"),
        "rs_vs_spy_10d_pct": tech.get("rs_vs_spy_10d_pct"),
        "atr_pct_14": tech.get("atr_pct_14"),
        "last": tech.get("last"),
    }


def _entry_validator_layer(entry: Optional[Any]) -> Dict[str, Any]:
    if entry is None:
        return {"available": False, "view": "Unknown", "score": 0.0,
                "notes": "validator not run"}
    state = getattr(entry, "state", None) or (entry.get("state") if isinstance(entry, dict) else None) or "Watch Only"
    actionable = bool(getattr(entry, "actionable_now", False) or (entry.get("actionable_now") if isinstance(entry, dict) else False))
    reason = getattr(entry, "reason", None) or (entry.get("reason") if isinstance(entry, dict) else "") or ""
    score_map = {
        "Buyable Now": 0.9,
        "Watch Reclaim": 0.2,
        "Pullback Forming": 0.1,
        "Watch Only": -0.1,
        "Too Extended": -0.4,
        "Broken / Avoid": -0.9,
    }
    score = score_map.get(state, 0.0)
    return {
        "available": True,
        "view": state,
        "actionable_now": actionable,
        "reason": reason,
        "score": score,
        "notes": reason,
    }


def _alpha_layer(ticker: str, alpha_artifacts: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
    if not alpha_artifacts:
        return {"available": False, "view": "No data", "score": 0.0,
                "notes": "no Alpha Discovery artifact loaded"}
    sym = ticker.upper()
    found_row: Optional[Dict[str, Any]] = None
    found_in: Optional[str] = None
    for art in alpha_artifacts:
        if not art:
            continue
        for row in (art.get("items") or []):
            if str(row.get("ticker") or "").upper() == sym:
                found_row = row
                found_in = art.get("mode") or art.get("source") or "alpha_board"
                break
        if found_row is not None:
            break
    if found_row is None:
        return {"available": True, "view": "Not on Alpha board", "score": 0.0,
                "notes": "ticker is not in the latest Alpha Discovery board"}
    bucket = str(found_row.get("bucket") or found_row.get("overlay_bucket") or "—")
    track = str(found_row.get("track") or "—")
    tier = str(found_row.get("data_tier") or "—")
    score_v = _f(found_row.get("alpha_score"))
    actionable = bool(found_row.get("actionable_now"))
    s = 0.0
    if "Buyable Now" in bucket:
        s = 0.5
    elif "Buyable Pullback" in bucket or "Pullback Watch" in bucket:
        s = 0.3
    elif "Sponsor" in bucket:
        s = 0.1
    elif "Too Late" in bucket or "Broken" in bucket:
        s = -0.4
    elif "Early" in bucket:
        s = 0.1
    if actionable:
        s += 0.1
    s = max(-1.0, min(1.0, s))
    return {
        "available": True,
        "view": bucket,
        "track": track,
        "tier": tier,
        "alpha_score": score_v,
        "actionable_now": actionable,
        "source": found_in,
        "score": s,
        "notes": f"Alpha {bucket} · track {track} · tier {tier} · score {score_v:.1f}",
    }


def _posture_layer(ticker: str, posture: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not posture:
        return {"available": False, "view": "Unknown", "score": 0.0,
                "notes": "no Market Posture snapshot"}
    bias = str(posture.get("bias") or "—")
    confidence = str(posture.get("confidence") or "—")
    focus = posture.get("focus_names") or []
    sym = ticker.upper()
    target = next((r for r in focus if str(r.get("symbol") or "").upper() == sym), None)
    if target is None:
        view = "Not in Posture focus"
        score = 0.0
        if bias == "bullish":
            score = 0.05
        elif bias == "defensive":
            score = -0.05
        notes = f"Posture bias {bias} (conf {confidence}); ticker not in focus list"
    else:
        compliance = str(target.get("compliance_tag") or "—")
        actionable = str(target.get("actionable_now") or "—")
        view = f"In Posture focus ({compliance})"
        score = 0.0
        if actionable.lower() == "yes" and compliance == "aligned now":
            score = 0.4
        elif compliance in {"pullback watch", "early setup"}:
            score = 0.15
        elif compliance in {"extended", "wait for confirmation"}:
            score = -0.05
        elif compliance == "not actionable yet":
            score = -0.1
        notes = (
            f"Posture bias {bias} (conf {confidence}); focus tag '{compliance}'"
        )
    return {"available": True, "view": view, "score": score, "notes": notes}


def _social_layer(ticker: str, social: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not social:
        return {"available": False, "view": "No data", "score": 0.0,
                "notes": "no Social Arb artifact"}
    sym = ticker.upper()
    items = social.get("items") or []
    target = next((r for r in items if str(r.get("ticker") or "").upper() == sym), None)
    if target is None:
        return {"available": True, "view": "No useful signal", "score": 0.0,
                "notes": "ticker not in latest Social Arb leads"}
    bucket = str(target.get("bucket") or "—")
    confidence = str(target.get("confidence") or "—")
    noise = str(target.get("noise_risk") or "—")
    s = 0.0
    if "Cross-Confirmed" in bucket:
        s = 0.25
    elif "Tape Confirmed" in bucket or "Options/Tape" in bucket:
        s = 0.20
    elif "News Catalyst" in bucket:
        s = 0.10
    elif "Emerging" in bucket:
        s = 0.05
    if noise.upper().startswith("HIGH"):
        s -= 0.15
    s = max(-1.0, min(1.0, s))
    return {
        "available": True,
        "view": bucket,
        "confidence": confidence,
        "noise_risk": noise,
        "score": s,
        "notes": f"social bucket {bucket} · conf {confidence} · noise {noise}",
    }


_OPTIONS_QUALITY_LABELS = (
    "BULLISH_CONFIRMING",
    "BULLISH_BUT_LATE",
    "SPECULATIVE_CALL_CHASE",
    "MIXED_OPTIONS",
    "BEARISH_HEDGE",
    "OPTIONS_NO_EDGE",
    "OPTIONS_MISSING",
)


def _tilt_to_score(tilt: float) -> float:
    """Map a positive ratio around 1.0 to a -1..+1 score (log2 scaled, clipped)."""
    if tilt is None or tilt <= 0:
        return 0.0
    import math as _m
    return max(-1.0, min(1.0, _m.log(tilt) / _m.log(2.0)))


_PATTERN_TO_CONFIRMATION = {
    "broad_confirmation":      "broad confirmation across expiries",
    "back_month_confirmation": "swing/back-month confirmation",
    "front_only_chase":        "front-only chase",
    "front_only_coverage":     "front-only coverage",
    "front_only_bearish":      "front-only bearish",
    "bearish_across_expiries": "bearish across expiries",
    "neutral_across_expiries": "neutral across expiries",
    "uncertain":               "uncertain",
}


def _extension_context(tech: Optional[Dict[str, Any]]) -> Dict[str, float]:
    """Compute the underlying-extension flags used by the options classifier."""
    tech = tech or {}
    last = _f(tech.get("last"))
    ema20 = _f(tech.get("ema20"))
    above_ema20_pct = ((last - ema20) / ema20 * 100.0) if (last and ema20) else 0.0
    extended = bool(tech.get("extended")) or _f(tech.get("return_5d_pct")) >= 8.0 \
        or _f(tech.get("return_20d_pct")) >= 25.0 or above_ema20_pct >= 6.0
    return {
        "extended": bool(extended),
        "r5": _f(tech.get("return_5d_pct")),
        "r20": _f(tech.get("return_20d_pct")),
        "above_ema20_pct": above_ema20_pct,
    }


def _options_layer(options: Optional[Dict[str, Any]], tech: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Translate Tradier features + underlying technicals into a research-only
    options view.  Dispatches between three payload shapes:

      - V2 (multi-expiry):  ``options.schema == "options_v2"`` — full
        feature vector with per-expiry tilts, spread quality, multi-expiry
        pattern, and IV-rank context.
      - V1 (single-expiry raw features):  has ``oi_tilt`` /
        ``liquidity_grade`` keys; uses the original single-bucket logic.
      - Legacy ({view, score, notes}):  pre-classifier payloads from older
        runners; surfaced as OPTIONS_NO_EDGE so the dashboard signals
        "this isn't the new classifier".

    The layer never overrides the entry validator or turns an extended
    stock into a buyable one — its only job is to label options flow
    honestly: late-stage / chase / mixed / bearish hedge / etc.

    Quality labels:
        BULLISH_CONFIRMING       bullish flow, underlying not extended
        BULLISH_BUT_LATE         bullish flow, underlying already extended
        SPECULATIVE_CALL_CHASE   front-only chase or vol >> OI on extended tape
        MIXED_OPTIONS            OI and volume tilts disagree
        BEARISH_HEDGE            put-heavy flow or put IV skew elevated
        OPTIONS_NO_EDGE          spread/liquidity too thin / signal too weak
        OPTIONS_MISSING          Tradier unavailable / chain absent
    """
    if not options:
        return _options_missing_payload()

    if options.get("schema") == "options_v2" or "per_expiry" in options:
        return _options_layer_v2(options, tech)

    legacy_only = (
        ("oi_tilt" not in options)
        and ("liquidity_grade" not in options)
        and ("view" in options or "score" in options)
    )
    if legacy_only:
        return {
            "available": True,
            "view": str(options.get("view") or "Neutral"),
            "score": _f(options.get("score"), 0.0),
            "notes": str(options.get("notes") or ""),
            "state": "legacy",
            "quality": "OPTIONS_NO_EDGE",
            "options_quality": "OPTIONS_NO_EDGE",
            "reason": "legacy options payload; quality classification unavailable",
            "warning": None,
            "options_warning": None,
        }

    return _options_layer_v1(options, tech)


def _options_missing_payload() -> Dict[str, Any]:
    return {
        "available": False,
        "view": "No data",
        "score": 0.0,
        "notes": "options layer not implemented in V1; future Tradier integration",
        "state": "missing",
        "quality": "OPTIONS_MISSING",
        "options_quality": "OPTIONS_MISSING",
        "reason": "Tradier options data unavailable",
        "warning": None,
        "options_warning": None,
    }


def _options_layer_v1(options: Dict[str, Any], tech: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """V1 single-expiry raw-features path (kept for back-compat)."""
    expiry = str(options.get("expiry") or "")
    liq = str(options.get("liquidity_grade") or "ok")
    oi_tilt = _f(options.get("oi_tilt"), 1.0)
    vol_tilt = _f(options.get("vol_tilt"), 1.0)
    iv_skew = _f(options.get("iv_skew"), 0.0)
    total_oi = _f(options.get("total_oi"), 0.0)

    oi_score = _tilt_to_score(oi_tilt)
    vol_score = _tilt_to_score(vol_tilt)
    base_score = round(0.6 * oi_score + 0.4 * vol_score, 3)

    ext = _extension_context(tech)
    extended = ext["extended"]
    r5 = ext["r5"]
    above_ema20_pct = ext["above_ema20_pct"]

    notes_core = (
        f"exp {expiry} · OI tilt {oi_tilt:.2f} · vol tilt {vol_tilt:.2f}"
        + (f" · skew {iv_skew:.2f}" if iv_skew else "")
        + f" · liq {liq} (OI {int(total_oi)})"
    )

    base_layer = {
        "available": True,
        "expiry": expiry,
        "oi_tilt": oi_tilt,
        "vol_tilt": vol_tilt,
        "iv_skew": iv_skew or None,
    }

    def _result(view, score, state, quality, reason, warning):
        out = {
            **base_layer,
            "view": view,
            "score": score,
            "notes": notes_core,
            "state": state,
            "quality": quality,
            "options_quality": quality,
            "reason": reason,
            "warning": warning,
            "options_warning": warning,
        }
        return out

    if liq == "thin":
        return _result("No edge", 0.0, "no_edge", "OPTIONS_NO_EDGE",
                       "option chain too thin to interpret", None)

    conflict = (oi_score >= 0.4 and vol_score <= -0.4) or (oi_score <= -0.4 and vol_score >= 0.4)
    if conflict:
        return _result("Mixed", 0.0, "mixed", "MIXED_OPTIONS",
                       f"OI tilt {oi_tilt:.2f} vs volume tilt {vol_tilt:.2f} disagree",
                       "do not treat as confirmation")

    bullish = base_score >= 0.25
    bearish = base_score <= -0.25
    put_skew_strong = bool(iv_skew) and iv_skew >= 1.10

    if bearish or put_skew_strong:
        return _result(
            "Bearish hedge",
            base_score if bearish else round(-0.30, 3),
            "bearish", "BEARISH_HEDGE",
            (f"put-heavy flow (OI {oi_tilt:.2f}, vol {vol_tilt:.2f})" if bearish
             else f"IV skew elevated {iv_skew:.2f} (puts richer than calls)"),
            None,
        )

    if not bullish:
        return _result("No edge", 0.0, "neutral", "OPTIONS_NO_EDGE",
                       "option flow neither bullish nor bearish at meaningful magnitude",
                       None)

    vol_dominates = vol_score >= max(oi_score + 0.4, 0.6)
    low_liq = liq == "low"
    if extended and (vol_dominates or low_liq):
        return _result(
            "Speculative chase", 0.0, "speculative", "SPECULATIVE_CALL_CHASE",
            (f"call volume hot (vol tilt {vol_tilt:.2f}) on extended tape "
             f"(r5 {r5:+.1f}%, above ema20 {above_ema20_pct:+.1f}%)"
             + (" with thin OI" if low_liq else "")),
            "do not treat as confirmation; likely late chase",
        )
    if extended:
        return _result(
            "Bullish (late)", round(base_score * 0.3, 3), "bullish_late", "BULLISH_BUT_LATE",
            (f"bullish OI/vol tilt on extended underlying "
             f"(r5 {r5:+.1f}%, above ema20 {above_ema20_pct:+.1f}%)"),
            "calls active but timing late",
        )
    return _result("Bullish confirming", base_score, "bullish", "BULLISH_CONFIRMING",
                   "bullish OI/vol tilt with underlying not extended", None)


def _options_layer_v2(options: Dict[str, Any], tech: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """V2 multi-expiry path: spread quality, pattern detection, IV rank."""
    canonical_bucket = options.get("canonical_bucket") or "swing"
    canonical = (options.get("per_expiry") or {}).get(canonical_bucket) or {}

    expiry = str(canonical.get("expiry") or "")
    liq = str(canonical.get("liquidity_grade") or "ok")
    spread_grade_chain = str(options.get("spread_grade_chain") or "unknown")
    oi_tilt = _f(options.get("canonical_oi_tilt") or canonical.get("oi_tilt"), 1.0)
    vol_tilt = _f(options.get("canonical_vol_tilt") or canonical.get("vol_tilt"), 1.0)
    max_iv_skew = _f(options.get("max_iv_skew"), 0.0)
    canonical_iv_skew = _f(canonical.get("iv_skew"), 0.0)
    pattern = str(options.get("pattern") or "uncertain")
    expiries_used = list(options.get("expiries_used") or [])
    event_iv_spike = bool(options.get("event_iv_spike"))

    iv_rank = options.get("iv_rank")
    iv_percentile = options.get("iv_percentile")
    iv_history_count = int(options.get("iv_history_count") or 0)
    iv_history_status = str(options.get("iv_history_status") or "insufficient")

    atm_call_iv = canonical.get("atm_call_iv")
    atm_put_iv = canonical.get("atm_put_iv")
    iv_skew_out = canonical.get("iv_skew") or (round(max_iv_skew, 3) if max_iv_skew else None)

    oi_score = _tilt_to_score(oi_tilt)
    vol_score = _tilt_to_score(vol_tilt)
    base_score = round(0.6 * oi_score + 0.4 * vol_score, 3)

    ext = _extension_context(tech)
    extended = ext["extended"]
    r5 = ext["r5"]
    above_ema20_pct = ext["above_ema20_pct"]

    expiry_confirmation = _PATTERN_TO_CONFIRMATION.get(pattern, pattern)

    notes_core = (
        f"canon {canonical_bucket} {expiry} · OI {oi_tilt:.2f} · vol {vol_tilt:.2f}"
        + (f" · skew {canonical_iv_skew:.2f}" if canonical_iv_skew else "")
        + f" · spread {spread_grade_chain} · liq {liq}"
        + f" · expiries {','.join(expiries_used) or '—'}"
    )

    base_layer = {
        "available": True,
        "expiry": expiry,
        "oi_tilt": oi_tilt,
        "vol_tilt": vol_tilt,
        "atm_call_iv": atm_call_iv,
        "atm_put_iv": atm_put_iv,
        "iv_skew": iv_skew_out,
        "iv_rank": iv_rank,
        "iv_percentile": iv_percentile,
        "iv_history_count": iv_history_count,
        "spread_quality": spread_grade_chain,
        "expiry_confirmation": expiry_confirmation,
        "expiries_used": expiries_used,
        "pattern": pattern,
    }

    def _result(view, score, state, quality, reason, warning):
        return {
            **base_layer,
            "view": view,
            "score": score,
            "notes": notes_core,
            "state": state,
            "quality": quality,
            "options_quality": quality,
            "reason": reason,
            "warning": warning,
            "options_warning": warning,
        }

    # 1) Spread gate — unusable spreads cannot produce a label.
    if spread_grade_chain == "unusable":
        return _result(
            "No edge", 0.0, "no_edge", "OPTIONS_NO_EDGE",
            "bid/ask spreads unusable across expiries",
            "do not treat options flow as confirmation",
        )

    # 2) Liquidity gate.
    if liq == "thin":
        return _result(
            "No edge", 0.0, "no_edge", "OPTIONS_NO_EDGE",
            "canonical expiry chain too thin to interpret",
            None,
        )

    # 3) Conflict between OI and volume tilts.
    conflict = (oi_score >= 0.4 and vol_score <= -0.4) or (oi_score <= -0.4 and vol_score >= 0.4)
    if conflict:
        return _result(
            "Mixed", 0.0, "mixed", "MIXED_OPTIONS",
            f"OI tilt {oi_tilt:.2f} vs volume tilt {vol_tilt:.2f} disagree",
            "do not treat as confirmation",
        )

    bullish = base_score >= 0.25
    bearish = base_score <= -0.25
    put_skew_strong = bool(canonical_iv_skew and canonical_iv_skew >= 1.10) \
        or bool(max_iv_skew and max_iv_skew >= 1.10)

    # Front-only chase pattern is speculative by construction — front bucket
    # is bullish but the back months are neutral or bearish.  Classify here
    # before the bullish/bearish gate so it fires even when the canonical
    # (swing) tilt itself is neutral.  Respect spread/liquidity/conflict
    # gates above; respect explicit bearish/put-skew evidence below.
    if pattern == "front_only_chase" and not (bearish or put_skew_strong):
        return _result(
            "Speculative chase", 0.0, "speculative", "SPECULATIVE_CALL_CHASE",
            "front-week call flow without swing/back-month confirmation",
            "do not treat as confirmation; likely event/single-week chase",
        )

    if bearish or put_skew_strong or pattern == "bearish_across_expiries":
        reason_bits = []
        if bearish:
            reason_bits.append(f"put-heavy flow (OI {oi_tilt:.2f}, vol {vol_tilt:.2f})")
        if pattern == "bearish_across_expiries":
            reason_bits.append("bearish across expiries")
        if put_skew_strong and not bearish:
            reason_bits.append(f"IV skew elevated {max_iv_skew:.2f} (puts richer)")
        score = base_score if bearish else round(-0.30, 3)
        return _result(
            "Bearish hedge", score, "bearish", "BEARISH_HEDGE",
            "; ".join(reason_bits) or "bearish positioning",
            None,
        )

    if not bullish:
        return _result(
            "No edge", 0.0, "neutral", "OPTIONS_NO_EDGE",
            "option flow neither bullish nor bearish at meaningful magnitude",
            None,
        )

    # 4) Bullish flow — disambiguate using multi-expiry pattern + extension.
    vol_dominates = vol_score >= max(oi_score + 0.4, 0.6)
    low_liq = liq == "low"

    if extended and (vol_dominates or low_liq):
        return _result(
            "Speculative chase", 0.0, "speculative", "SPECULATIVE_CALL_CHASE",
            (f"call volume hot (vol tilt {vol_tilt:.2f}) on extended tape "
             f"(r5 {r5:+.1f}%, above ema20 {above_ema20_pct:+.1f}%)"
             + (" with thin OI" if low_liq else "")),
            "do not treat as confirmation; likely late chase",
        )

    if extended:
        warning = "calls active but timing late"
        if event_iv_spike:
            warning += "; front-week IV spike (event risk)"
        if iv_rank is not None and iv_rank >= 80.0:
            warning += "; ATM IV in upper 20% of recent range"
        return _result(
            "Bullish (late)", round(base_score * 0.3, 3), "bullish_late", "BULLISH_BUT_LATE",
            (f"bullish OI/vol tilt on extended underlying "
             f"(r5 {r5:+.1f}%, above ema20 {above_ema20_pct:+.1f}%)"),
            warning,
        )

    # Confirming branches — surface multi-expiry corroboration in the reason.
    if pattern == "broad_confirmation":
        reason = "bullish OI/vol tilt confirmed across all covered expiries"
    elif pattern == "back_month_confirmation":
        reason = "bullish swing/back-month confirmation; underlying not extended"
    else:
        reason = "bullish OI/vol tilt with underlying not extended"

    warning = None
    if event_iv_spike:
        warning = "front-week IV spike (event risk)"
    if iv_rank is not None and iv_rank >= 80.0:
        msg = "ATM IV in upper 20% of recent range; calls already expensive"
        warning = msg if warning is None else f"{warning}; {msg}"
    elif iv_rank is not None and iv_rank <= 20.0:
        # Cheap-IV note doesn't escalate to warning; surface in reason.
        reason += "; ATM IV in lower 20% of recent range"

    # Confirmation does NOT raise the score above its base — composite
    # weight is fixed at 0.10 by design.  Surfacing the pattern in
    # `expiry_confirmation` is what the user reads.
    return _result(
        "Bullish confirming", base_score, "bullish", "BULLISH_CONFIRMING",
        reason, warning,
    )


def _institutional_layer(inst: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not inst:
        return {"available": False, "view": "No data", "score": 0.0,
                "notes": "13F layer not used in V1 (background only)"}
    return {
        "available": True,
        "view": str(inst.get("view") or "Neutral"),
        "score": _f(inst.get("score"), 0.0),
        "notes": str(inst.get("notes") or ""),
    }


# ── Composite + label resolver ──────────────────────────────────────────────


WEIGHTS: Dict[str, float] = {
    "market_regime": 0.15,
    "sector": 0.20,
    "technicals": 0.25,
    "entry_validator": 0.20,
    "options": 0.10,
    "alpha_posture_overlap": 0.07,
    "social": 0.03,
}


def _composite(layers: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """
    Weighted composite of available layers.  ``alpha_posture_overlap`` is the
    average of the Alpha Discovery and Market Posture layers (where each is
    available).  Missing layers are dropped and remaining weights are
    renormalized so they still sum to 1.0.
    """
    parts: List[Tuple[str, float, float]] = []  # (name, weight, score)

    for key in ("market_regime", "sector", "technicals", "entry_validator", "options", "social"):
        layer = layers.get(key) or {}
        if layer.get("available"):
            parts.append((key, WEIGHTS[key], _f(layer.get("score"))))

    alpha = layers.get("alpha") or {}
    posture = layers.get("posture") or {}
    overlap_components = [_f(layer.get("score")) for layer in (alpha, posture) if layer.get("available")]
    if overlap_components:
        parts.append((
            "alpha_posture_overlap",
            WEIGHTS["alpha_posture_overlap"],
            sum(overlap_components) / len(overlap_components),
        ))

    total_w = sum(w for _, w, _ in parts) or 1.0
    weighted = sum(w * s for _, w, s in parts)
    composite_score = weighted / total_w  # in [-1, 1]

    bullish_score = max(0.0, composite_score)
    bearish_score = max(0.0, -composite_score)
    return {
        "composite_score": round(composite_score, 2),
        "bullish_score": round(bullish_score * 100, 0),
        "bearish_score": round(bearish_score * 100, 0),
        "layers_used": [name for name, _, _ in parts],
        "layers_skipped": [
            name for name in ("market_regime", "sector", "technicals", "entry_validator",
                              "options", "social", "alpha_posture_overlap")
            if name not in {n for n, _, _ in parts}
        ],
    }


def _entry_quality_score(entry_layer: Dict[str, Any], tech_layer: Dict[str, Any]) -> int:
    """0–100 index of *entry timing* quality, separate from bullish/bearish."""
    if not entry_layer.get("available"):
        return 0
    base = {
        "Buyable Now": 90,
        "Watch Reclaim": 55,
        "Pullback Forming": 50,
        "Watch Only": 35,
        "Too Extended": 15,
        "Broken / Avoid": 5,
    }.get(str(entry_layer.get("view")), 30)
    if tech_layer.get("available") and tech_layer.get("extended"):
        base = min(base, 30)
    return int(base)


def _risk_score(
    market_layer: Dict[str, Any],
    sector_layer: Dict[str, Any],
    tech_layer: Dict[str, Any],
    options_layer: Dict[str, Any],
) -> int:
    """0–100 index of *risk* (higher = more risk)."""
    risk = 30
    if market_layer.get("available"):
        if str(market_layer.get("view")) == "Defensive":
            risk += 25
        elif str(market_layer.get("view")) == "Unstable":
            risk += 15
    if sector_layer.get("available"):
        sv = str(sector_layer.get("view"))
        if sv in {"Weakening"}:
            risk += 15
        elif sv == "Defensive":
            risk += 5
    if tech_layer.get("available"):
        if tech_layer.get("extended"):
            risk += 15
        atr = _f(tech_layer.get("atr_pct_14"))
        if atr >= 5:
            risk += 10
    if options_layer.get("available"):
        risk += int(max(0.0, -_f(options_layer.get("score"))) * 20)
    return int(max(0, min(100, risk)))


def _confidence(
    layers: Dict[str, Dict[str, Any]],
    market_layer: Dict[str, Any],
) -> str:
    available_count = sum(
        1 for k in ("market_regime", "sector", "technicals", "entry_validator")
        if (layers.get(k) or {}).get("available")
    )
    market_conf = str(market_layer.get("confidence") or "low") if market_layer.get("available") else "low"
    if available_count < 2 or market_conf == "low":
        return "low"
    if available_count >= 4 and market_conf == "high":
        return "high"
    return "medium"


# Hard caps order matters: the first cap that fires wins for "label".
def _resolve_label(
    composite: Dict[str, Any],
    market_layer: Dict[str, Any],
    sector_layer: Dict[str, Any],
    tech_layer: Dict[str, Any],
    entry_layer: Dict[str, Any],
) -> Tuple[str, List[str]]:
    """
    Apply hard caps and resolve the final label vocabulary.

    Allowed labels:
      Bullish, Bullish but extended, Bullish but not buyable yet,
      Neutral, Bearish, Bearish but oversold, Avoid / no edge.
    """
    caps_fired: List[str] = []
    score = float(composite.get("composite_score") or 0.0)

    entry_view = str(entry_layer.get("view")) if entry_layer.get("available") else None
    market_view = str(market_layer.get("view")) if market_layer.get("available") else None
    sector_view = str(sector_layer.get("view")) if sector_layer.get("available") else None
    tech_extended = bool(tech_layer.get("available") and tech_layer.get("extended"))
    rs10 = _f((tech_layer or {}).get("rs_vs_spy_10d_pct"))

    # Default mapping from composite score.
    if score >= 0.30:
        label = "Bullish"
    elif score >= 0.10:
        label = "Bullish but not buyable yet"
    elif score >= -0.10:
        label = "Neutral"
    elif score >= -0.30:
        label = "Bearish but oversold"
    else:
        label = "Bearish"

    # Hard cap 1: Broken / Avoid → cap at Neutral / Bearish / Avoid.
    if entry_view == "Broken / Avoid":
        if score < -0.10:
            label = "Bearish"
        else:
            label = "Avoid / no edge"
        caps_fired.append("entry_validator=Broken/Avoid")

    # Hard cap 2: Too Extended → cannot be Buyable / Bullish.  Use
    # "Bullish but extended" if upstream says bullish.
    if tech_extended or entry_view == "Too Extended":
        if label.startswith("Bullish") and label != "Bullish but not buyable yet":
            label = "Bullish but extended"
            caps_fired.append("technicals/entry=extended")

    # Hard cap 3: Sector weakening + stock not strongly outperforming.
    if sector_view in {"Weakening"} and rs10 < 1.5 and label.startswith("Bullish"):
        if label == "Bullish":
            label = "Bullish but not buyable yet"
        caps_fired.append("sector_weakening + rs10<1.5pp")

    # Hard cap 4: Defensive market regime → cautious longs.
    if market_view == "Defensive" and label == "Bullish":
        label = "Bullish but not buyable yet"
        caps_fired.append("market_regime=Defensive")

    # Hard cap 5: explicit Buyable Now requires entry_validator agreement.
    # We don't emit "Buyable Now" as a label, but if the technicals look
    # bullish and the entry validator is NOT Buyable Now, the bullish-bias
    # label should explicitly say so.
    if (label == "Bullish"
            and entry_layer.get("available")
            and entry_view not in {"Buyable Now"}):
        label = "Bullish but not buyable yet"
        caps_fired.append("entry_validator!=Buyable Now")

    return label, caps_fired


def _build_invalidation(
    market_layer: Dict[str, Any],
    sector_layer: Dict[str, Any],
    tech_layer: Dict[str, Any],
) -> List[str]:
    out: List[str] = []
    if tech_layer.get("available"):
        ema20 = tech_layer.get("ema20")
        ema50 = tech_layer.get("ema50")
        if ema20:
            out.append(f"loses EMA20 ({ema20:.2f}) on close")
        if ema50:
            out.append(f"loses EMA50 ({ema50:.2f}) on close")
    if sector_layer.get("available"):
        out.append(f"sector {sector_layer.get('etf','?')} relative-strength rolls over (rs10 turns negative)")
    if market_layer.get("available"):
        out.append("VIX expands or market regime flips to Defensive / Stress")
    out.append("failed reclaim or lower-low on 5d bars")
    return out[:5]


def _next_manual_checks(
    ticker: str,
    sector_etf: Optional[str],
    options_available: bool,
) -> List[str]:
    out = [
        f"check {ticker} daily chart for clean reclaim / pullback structure",
        "check upcoming earnings date and macro calendar for the horizon",
        "check option liquidity and put/call activity (not yet wired in V1)" if not options_available
        else "review options layer notes for confirmation / warning",
    ]
    if sector_etf:
        out.append(f"verify sector ETF {sector_etf} behavior over the next 1–3 sessions")
    out.append("verify any news catalyst quality before acting (do not rely on social leads alone)")
    return out


def _horizon_view(label: str, horizon_days: int) -> str:
    """Decay the headline label slightly across horizons.  V1 keeps it simple
    — the same label applies to 5d / 10d / 20d unless the label is a
    short-term qualifier."""
    if horizon_days <= 5:
        return label
    if horizon_days <= 10:
        return label
    # 20d view: drop "but not buyable yet" since the entry timing window may
    # have changed by then.
    if label == "Bullish but not buyable yet":
        return "Bullish (subject to fresh entry trigger)"
    return label


def build_stock_lens(
    *,
    ticker: str,
    stock_frame: Any,
    spy_frame: Any,
    market_forecast: Optional[Dict[str, Any]] = None,
    sector_etf: Optional[str] = None,
    sector_rotation: Optional[Dict[str, Any]] = None,
    company_profile: Optional[Dict[str, Any]] = None,
    entry_validation: Optional[Any] = None,
    alpha_artifacts: Optional[List[Dict[str, Any]]] = None,
    posture_output: Optional[Dict[str, Any]] = None,
    social_artifact: Optional[Dict[str, Any]] = None,
    options_layer: Optional[Dict[str, Any]] = None,
    institutional_layer: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Build the single-stock research lens for one ticker from already-loaded
    inputs.  All provider calls / cache I/O are the caller's responsibility.

    See module docstring for the inputs vocabulary and guardrails.
    """
    sym = (ticker or "").upper()

    tech = stock_technical_features(sym, stock_frame, spy_frame)

    layers = {
        "market_regime": _market_layer(market_forecast),
        "sector": _sector_layer(sector_etf, sector_rotation),
        "technicals": _technicals_layer(tech),
        "entry_validator": _entry_validator_layer(entry_validation),
        "alpha": _alpha_layer(sym, alpha_artifacts),
        "posture": _posture_layer(sym, posture_output),
        "social": _social_layer(sym, social_artifact),
        "options": _options_layer(options_layer, tech),
        "institutional": _institutional_layer(institutional_layer),
    }
    composite = _composite(layers)
    label, caps_fired = _resolve_label(
        composite, layers["market_regime"], layers["sector"],
        layers["technicals"], layers["entry_validator"],
    )

    entry_quality = _entry_quality_score(layers["entry_validator"], layers["technicals"])
    risk = _risk_score(layers["market_regime"], layers["sector"], layers["technicals"], layers["options"])
    confidence = _confidence(layers, layers["market_regime"])

    horizon_views = {
        "5d": _horizon_view(label, 5),
        "10d": _horizon_view(label, 10),
        "20d": _horizon_view(label, 20),
    }

    invalidation = _build_invalidation(layers["market_regime"], layers["sector"], layers["technicals"])
    next_checks = _next_manual_checks(sym, sector_etf, options_layer is not None)

    # Headline: short, single-sentence summary.
    sector_part = ""
    if layers["sector"].get("available"):
        sector_part = f"sector {layers['sector'].get('etf')} {layers['sector'].get('view')}"
    market_part = ""
    if layers["market_regime"].get("available"):
        market_part = f"market {layers['market_regime'].get('view')}"
    entry_part = ""
    if layers["entry_validator"].get("available"):
        entry_part = f"entry {layers['entry_validator'].get('view')}"
    parts = [p for p in (sector_part, market_part, entry_part) if p]
    headline = (
        f"{sym} — 10–20d view: {label}, confidence {confidence}."
        + (f"  Reason: {' · '.join(parts)}." if parts else "")
    )

    conclusion = _build_conclusion(
        sym, label, layers["entry_validator"], layers["sector"], layers["technicals"],
    )

    data_quality_notes: List[str] = []
    for k, layer in layers.items():
        if not layer.get("available"):
            data_quality_notes.append(f"{k}: {layer.get('notes') or 'not available'}")

    return {
        "version": VERSION,
        "ticker": sym,
        "company": (company_profile or {}).get("companyName") if company_profile else None,
        "sector_name": (company_profile or {}).get("sector") if company_profile else None,
        "industry": (company_profile or {}).get("industry") if company_profile else None,
        "sector_etf": sector_etf,
        "headline": headline,
        "label": label,
        "confidence": confidence,
        "horizon_view": horizon_views,
        "scores": {
            "composite": composite.get("composite_score"),
            "bullish_score": composite.get("bullish_score"),
            "bearish_score": composite.get("bearish_score"),
            "entry_quality_score": entry_quality,
            "risk_score": risk,
        },
        "layers": {k: v for k, v in layers.items()},
        "weights": dict(WEIGHTS),
        "hard_caps_fired": caps_fired,
        "invalidation": invalidation,
        "next_manual_checks": next_checks,
        "conclusion": conclusion,
        "technicals_raw": tech,
        "data_quality_notes": data_quality_notes,
        "guardrails": [
            "research-only / not trade approval / not paper evidence",
            "no execution, no governance, no sleeve mutation",
            "no Alpha Discovery scoring change, no Market Posture change, "
            "no Daily Entry Validator change, no Social Arb change",
            "cache-first; degrades gracefully when a layer is missing",
            "no news/social as core forecasting input — used only as weak context",
            "no 13F as timing input",
            "no fake precision; scores are coarse and rounded",
        ],
        "validation_plan": [
            "Phase 4 (not built): forward 5d / 10d / 20d return tests by label",
            "hit rate by confidence bucket",
            "whether 'Bullish but extended' avoids worse forward draws than 'Bullish'",
            "whether sector-aligned bullish picks beat sector-conflicted bullish picks",
            "whether Entry-Validator Buyable Now improves expectancy vs Watch states",
        ],
    }


def _build_conclusion(
    ticker: str,
    label: str,
    entry_layer: Dict[str, Any],
    sector_layer: Dict[str, Any],
    tech_layer: Dict[str, Any],
) -> str:
    """One-sentence honest manual-action recommendation; never says 'buy now'."""
    ev = str(entry_layer.get("view")) if entry_layer.get("available") else None
    if label == "Avoid / no edge":
        return f"{ticker}: no clean research edge in either direction. Skip until structure changes."
    if label == "Bearish":
        return (
            f"{ticker}: bearish bias. Long entries are not supported by current "
            f"layers. Watch for capitulation + sector reset before reconsidering."
        )
    if label == "Bearish but oversold":
        return (
            f"{ticker}: bias is bearish but tape is oversold. Bounce attempts can "
            f"happen; treat as counter-trend only and demand confirmation."
        )
    if label == "Bullish but extended":
        ema20 = (tech_layer or {}).get("ema20")
        anchor = f"EMA20 {ema20:.2f}" if ema20 else "EMA20"
        return (
            f"{ticker}: trend is healthy but extended. Best treatment: wait for a "
            f"pullback toward {anchor} or a clean reset before adding exposure. "
            f"Do not chase."
        )
    if label == "Bullish but not buyable yet":
        if ev == "Watch Reclaim":
            return (
                f"{ticker}: constructive but not buyable now. Watch for a clean "
                f"reclaim of EMA20 with confirmation volume; otherwise skip."
            )
        if ev == "Pullback Forming":
            return (
                f"{ticker}: constructive but the pullback hasn't matured. Let it "
                f"develop another 1–3 sessions and re-check structure."
            )
        return (
            f"{ticker}: constructive context, but the entry location is not clean. "
            f"Wait for either a reclaim trigger or a deeper pullback before acting."
        )
    if label == "Bullish":
        return (
            f"{ticker}: bullish across layers. Even so, treat as research context — "
            f"verify the daily structure, sector ETF behavior, and any catalyst "
            f"window before sizing."
        )
    return f"{ticker}: {label}. See layer table and invalidations before acting."

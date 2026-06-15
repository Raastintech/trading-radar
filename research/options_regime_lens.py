#!/usr/bin/env python3
"""
research/options_regime_lens.py — Phase 1G.12

Research-only Options Regime Lens. Measures market-level options conditions —
a gamma-exposure PROXY, put/call skew by delta, IV rank/percentile, term
structure, and a consolidated regime label — so the dashboard / MCP / forecast
can read *market* options conditions (today the only options surface is the
per-ticker Stock Lens).

THIS IS NOT A TRADING STRATEGY AND NOT A VETO.
  - It emits no paper signals, registers no strategy, builds no trade proposals.
  - It never imports execution / council / governance / the decision logger.
  - It never touches the live-capital gate and mutates no DB rows.
  - It surfaces diagnostics only; nothing here approves or blocks any candidate.

Data sources (same chain every other options consumer uses):
  - core.options_feed_factory.load_options_feed()  → Alpaca primary, Tradier
    enrichment (IV/greeks) when configured. Returns None when unconfigured, in
    which case every symbol degrades to NOT_ENOUGH_DATA.
  - core.alpaca_client.get_alpaca()  → spot (cache-first daily close, then quote
    mid).
Provider calls happen ONLY in this module (and only when run from the provider
research cycles). The dashboard and MCP orchestrator read the JSON sidecar only.

Writes:
  - cache/research/options_regime_lens_latest.json
  - logs/options_regime_lens_latest.txt
  - data/research/options_regime_lens_history.jsonl   (one row per symbol/day,
    appended idempotently — re-running the same day does not duplicate)

GEX_PROXY caveat: the gamma exposure number is a NAIVE proxy that assumes the
common "dealers long calls / short puts" sign convention. It does NOT claim to
know actual dealer positioning. Treat the sign/regime as a hint, not a fact.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Load credentials the same way the other provider research scripts do: prefer
# SNIPER_ENV_PATH, then a root .env. GEM_TRADER_SKIP_DOTENV=true skips both for
# offline tooling/tests. Placeholders below let the module import core.config
# without real creds (real provider calls still fail-soft on placeholders).
try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None

if load_dotenv is not None and os.getenv("GEM_TRADER_SKIP_DOTENV", "").lower() not in {"1", "true", "yes"}:
    _env_path = os.getenv("SNIPER_ENV_PATH", "").strip()
    if _env_path:
        load_dotenv(_env_path, override=False)
    load_dotenv(ROOT / ".env", override=False)

os.environ.setdefault("ALPACA_API_KEY", "offline")
os.environ.setdefault("ALPACA_SECRET_KEY", "offline")
os.environ.setdefault("FMP_API_KEY", "offline")
os.environ.setdefault("FMP_BASE_URL", "https://financialmodelingprep.com/stable")
os.environ.setdefault("DB_PATH", str(ROOT / "db" / "trading.db"))
os.environ.setdefault("CACHE_DIR", str(ROOT / "cache"))
os.environ.setdefault("LOG_DIR", str(ROOT / "logs"))
# NOTE: paper/live flags are intentionally NOT set here — core.config defaults
# them to the safe (paper) value, and this module never submits orders.

import pandas as pd  # noqa: E402

CACHE = ROOT / "cache" / "research"
JSON_OUT = CACHE / "options_regime_lens_latest.json"
TXT_OUT = ROOT / "logs" / "options_regime_lens_latest.txt"
HISTORY_PATH = ROOT / "data" / "research" / "options_regime_lens_history.jsonl"

SCHEMA = "options_regime_lens_v1"

# Index/ETF first; the lens is a *market* read, not a single-name read.
DEFAULT_SYMBOLS: Tuple[str, ...] = ("SPY", "QQQ", "IWM", "VXX")

# ── tunable thresholds (kept in-module; not promoted to core.config) ──────────
# Expirations are bucketed by DTE so term structure compares like-for-like.
_FRONT_DTE_MAX = 14          # "near-term" expiry ceiling for term structure
_TARGET_DTE = (30, 45, 60)   # interpolation targets for IV rank / term structure
_MAX_EXPIRIES = 6            # cap chains pulled per symbol (provider-call budget)
_MIN_ATM_STRIKES = 3         # ATM IV averages this many strikes nearest spot

# Skew (expressed in IV *points*, e.g. 0.05 = 5 vol points = put richer by 5).
_SKEW_PUT_HEAVY = 0.05       # >= → PUT_HEDGE_DEMAND
_SKEW_CALL_RICH = 0.00       # <  → CALL_CHASE (calls richer than equidistant puts)

# IV rank bands (0–100). Require this many prior obs before ranking.
_IV_HISTORY_MIN_OBS = 20
_IV_RANK_LOW = 20.0
_IV_RANK_HIGH = 60.0
_IV_RANK_EXTREME = 85.0

# Term structure (front IV minus 30d IV, in IV points).
_TS_CONTANGO = -0.005        # front below 30d by this → NORMAL (healthy contango)
_TS_FRONT_PREMIUM = 0.03     # front above 30d up to this → FRONT_PREMIUM
_TS_EVENT_PREMIUM = 0.06     # front above 30d beyond this → EVENT_PREMIUM / panic

# Gamma confidence: fraction of OI that sits on strikes with a usable gamma.
_GAMMA_MIN_COVERAGE = 0.30
_GAMMA_WALL_BAND_PCT = 0.10  # only strikes within ±10% of spot count as "walls"

# ── regime labels (string constants — keep deterministic) ─────────────────────
REGIME_CALM_RANGE = "CALM_RANGE"
REGIME_BULLISH_STABLE = "BULLISH_STABLE"
REGIME_FRAGILE_HEDGING = "FRAGILE_HEDGING"
REGIME_CALL_CHASE_RISK = "CALL_CHASE_RISK"
REGIME_HIGH_VOL_STRESS = "HIGH_VOL_STRESS"
REGIME_MIXED = "MIXED"
REGIME_NOT_ENOUGH_DATA = "NOT_ENOUGH_DATA"

NOT_ENOUGH_DATA = "NOT_ENOUGH_DATA"


# ══════════════════════════════════════════════════════════════════════════════
# Provider access (lazy — keeps the module import-safe without creds)
# ══════════════════════════════════════════════════════════════════════════════

def _load_options_feed():
    """Return the shared Alpaca-first / Tradier-enrich chain, or None."""
    try:
        from core.options_feed_factory import load_options_feed
        return load_options_feed()
    except Exception:
        return None


def _get_spot(sym: str) -> Optional[float]:
    """Cache-first daily close, then live quote mid. None on failure."""
    try:
        from core.alpaca_client import get_alpaca
        client = get_alpaca()
    except Exception:
        return None
    try:
        bars = client.get_daily_bars(sym, days=1)
        if bars:
            c = bars[-1].get("close")
            if c and float(c) > 0:
                return float(c)
    except Exception:
        pass
    try:
        q = client.get_quote(sym)
        if q and q.get("mid"):
            return float(q["mid"])
    except Exception:
        pass
    return None


# ══════════════════════════════════════════════════════════════════════════════
# small numeric helpers
# ══════════════════════════════════════════════════════════════════════════════

def _f(v: Any) -> Optional[float]:
    try:
        x = float(v)
        if x != x:  # NaN
            return None
        return x
    except (TypeError, ValueError):
        return None


def _dte(expiry: str, *, today: Optional[date] = None) -> Optional[int]:
    try:
        d = datetime.strptime(expiry[:10], "%Y-%m-%d").date()
        return (d - (today or date.today())).days
    except Exception:
        return None


def _col(df, name: str):
    """Return a float Series for column ``name`` or None if absent/empty."""
    if df is None or getattr(df, "empty", True):
        return None
    if name not in getattr(df, "columns", []):
        return None
    try:
        return df[name].astype(float)
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════════════════
# Task 5 primitive — ATM IV from a chain side (3 strikes nearest spot)
# ══════════════════════════════════════════════════════════════════════════════

def _atm_iv(df, *, spot: Optional[float]) -> Optional[float]:
    if df is None or getattr(df, "empty", True) or not spot or spot <= 0:
        return None
    try:
        strike = df["strike"].astype(float)
        iv = df["impliedVolatility"].astype(float)
        diffs = (strike - float(spot)).abs()
        idx = diffs.nsmallest(_MIN_ATM_STRIKES).index
        vals = [v for v in iv.loc[idx].tolist() if v and v > 0]
        if not vals:
            return None
        return sum(vals) / len(vals)
    except Exception:
        return None


def _blend_atm_iv(calls, puts, *, spot: Optional[float]) -> Optional[float]:
    c = _atm_iv(calls, spot=spot)
    p = _atm_iv(puts, spot=spot)
    vals = [v for v in (c, p) if v is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


# ══════════════════════════════════════════════════════════════════════════════
# Task 3 — gamma / GEX proxy
# ══════════════════════════════════════════════════════════════════════════════

def gamma_proxy(calls, puts, *, spot: Optional[float]) -> Dict[str, Any]:
    """Naive dealer-gamma PROXY from per-strike gamma·OI.

    Convention (explicitly a proxy, NOT real dealer positioning): calls
    contribute positive gamma, puts negative. Positive total ⇒ candidate
    pinning/range regime; negative ⇒ candidate acceleration/fragility.
    Returns NOT_ENOUGH_DATA state when gamma/OI coverage is too sparse.
    """
    out: Dict[str, Any] = {
        "total_gamma_proxy": None,
        "gamma_by_strike": [],
        "largest_gamma_strikes": [],
        "nearest_gamma_wall": None,
        "zero_gamma_estimate": None,
        "gamma_regime": NOT_ENOUGH_DATA,
        "gamma_confidence": "none",
        "gamma_source": "gex_proxy",
        "note": "GEX_PROXY — assumes dealers long calls / short puts; not exact "
                "dealer gamma.",
    }
    if not spot or spot <= 0:
        return out

    def _side_contrib(df, sign: float):
        strike = _col(df, "strike")
        gamma = _col(df, "gamma")
        oi = _col(df, "openInterest")
        if strike is None or gamma is None or oi is None:
            return {}, 0.0, 0.0
        per: Dict[float, float] = {}
        covered_oi = 0.0
        total_oi = 0.0
        for k, g, o in zip(strike.tolist(), gamma.tolist(), oi.tolist()):
            o = max(0.0, float(o or 0.0))
            total_oi += o
            if not g or g <= 0 or o <= 0:
                continue
            covered_oi += o
            # gamma·OI·100·spot^2·0.01 → $ gamma per 1% move (scaled).
            contrib = sign * float(g) * o * 100.0 * (spot ** 2) * 0.01
            per[round(float(k), 4)] = per.get(round(float(k), 4), 0.0) + contrib
        return per, covered_oi, total_oi

    call_per, c_cov, c_tot = _side_contrib(calls, +1.0)
    put_per, p_cov, p_tot = _side_contrib(puts, -1.0)

    total_oi = c_tot + p_tot
    covered_oi = c_cov + p_cov
    if total_oi <= 0 or covered_oi <= 0:
        return out  # no gamma anywhere → NOT_ENOUGH_DATA

    coverage = covered_oi / total_oi if total_oi else 0.0

    # net gamma per strike (calls + signed puts already)
    net: Dict[float, float] = {}
    for k, v in call_per.items():
        net[k] = net.get(k, 0.0) + v
    for k, v in put_per.items():
        net[k] = net.get(k, 0.0) + v

    if not net:
        return out

    total = sum(net.values())
    # report in $MM units for readability
    by_strike = sorted(
        ({"strike": k, "net_gamma_mm": round(v / 1e6, 3)} for k, v in net.items()),
        key=lambda r: r["strike"],
    )
    largest = sorted(net.items(), key=lambda kv: abs(kv[1]), reverse=True)[:5]
    largest_out = [
        {"strike": k, "net_gamma_mm": round(v / 1e6, 3),
         "dist_pct": round((k - spot) / spot * 100.0, 2)}
        for k, v in largest
    ]

    # nearest gamma "wall": strongest *positive* net-gamma strike within band.
    band = spot * _GAMMA_WALL_BAND_PCT
    walls = [(k, v) for k, v in net.items()
             if v > 0 and abs(k - spot) <= band]
    nearest_wall = None
    if walls:
        wk, wv = max(walls, key=lambda kv: kv[1])
        nearest_wall = {
            "strike": wk,
            "net_gamma_mm": round(wv / 1e6, 3),
            "dist_pct": round((wk - spot) / spot * 100.0, 2),
        }

    # zero-gamma (gamma flip): strike where cumulative net gamma crosses 0.
    zero_gamma = _zero_gamma_estimate(net)

    if coverage >= 0.6:
        conf = "high"
    elif coverage >= _GAMMA_MIN_COVERAGE:
        conf = "medium"
    else:
        conf = "low"

    # Regime: sign of total proxy, but only commit when coverage is usable.
    if coverage < _GAMMA_MIN_COVERAGE:
        regime = NOT_ENOUGH_DATA
    elif total > 0:
        regime = "positive_gamma_regime"
    elif total < 0:
        regime = "negative_gamma_regime"
    else:
        regime = "neutral"

    out.update({
        "total_gamma_proxy": round(total / 1e6, 3),       # $MM per 1% move
        "gamma_by_strike": by_strike,
        "largest_gamma_strikes": largest_out,
        "nearest_gamma_wall": nearest_wall,
        "zero_gamma_estimate": zero_gamma,
        "gamma_regime": regime,
        "gamma_confidence": conf,
        "gamma_oi_coverage": round(coverage, 3),
    })
    return out


def _zero_gamma_estimate(net: Dict[float, float]) -> Optional[float]:
    """Strike where cumulative net gamma (ascending strikes) crosses zero."""
    if not net:
        return None
    items = sorted(net.items(), key=lambda kv: kv[0])
    cum = 0.0
    prev_k = None
    prev_cum = None
    for k, v in items:
        cum += v
        if prev_cum is not None and (prev_cum < 0 <= cum or prev_cum > 0 >= cum):
            # linear interpolation between the two strikes
            if cum != prev_cum:
                frac = -prev_cum / (cum - prev_cum)
                return round(prev_k + frac * (k - prev_k), 2)
            return round(k, 2)
        prev_k, prev_cum = k, cum
    return None


# ══════════════════════════════════════════════════════════════════════════════
# Task 4 — skew by delta (fallback: moneyness)
# ══════════════════════════════════════════════════════════════════════════════

def _iv_at_delta(df, target_delta: float, *, is_put: bool):
    """IV of the contract whose delta is nearest ``target_delta``.

    target_delta is magnitude (0.25 / 0.10). Puts have negative delta;
    we match on absolute delta. Returns None if deltas are unusable.
    """
    delta = _col(df, "delta")
    iv = _col(df, "impliedVolatility")
    if delta is None or iv is None:
        return None
    try:
        ad = delta.abs()
        mask = (ad > 0) & (iv > 0)
        if not mask.any():
            return None
        sub_ad = ad[mask]
        sub_iv = iv[mask]
        idx = (sub_ad - target_delta).abs().idxmin()
        # only trust the match if it is reasonably close to the target delta
        if abs(float(sub_ad.loc[idx]) - target_delta) > 0.15:
            return None
        return float(sub_iv.loc[idx])
    except Exception:
        return None


def _iv_at_moneyness(df, *, spot: float, target_pct: float, is_put: bool):
    """Fallback skew input: IV at a strike ~target_pct away from spot.

    Puts use strikes below spot, calls above, at a comparable |distance|.
    """
    strike = _col(df, "strike")
    iv = _col(df, "impliedVolatility")
    if strike is None or iv is None or not spot:
        return None
    try:
        target = spot * (1.0 - target_pct) if is_put else spot * (1.0 + target_pct)
        diffs = (strike - target).abs()
        valid = iv > 0
        if not valid.any():
            return None
        idx = diffs[valid].idxmin()
        return float(iv.loc[idx])
    except Exception:
        return None


def skew_diagnostics(calls, puts, *, spot: Optional[float]) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "put_call_skew_25d": None,
        "put_call_skew_10d": None,
        "skew_basis": None,
        "skew_state": NOT_ENOUGH_DATA,
        "skew_confidence": "none",
    }
    if not spot or spot <= 0:
        return out

    # Preferred: by delta.
    p25 = _iv_at_delta(puts, 0.25, is_put=True)
    c25 = _iv_at_delta(calls, 0.25, is_put=False)
    p10 = _iv_at_delta(puts, 0.10, is_put=True)
    c10 = _iv_at_delta(calls, 0.10, is_put=False)

    basis = "delta"
    skew_25 = (p25 - c25) if (p25 is not None and c25 is not None) else None
    skew_10 = (p10 - c10) if (p10 is not None and c10 is not None) else None

    # Fallback: moneyness (≈ 5% OTM for "25d-like", 10% for "10d-like").
    if skew_25 is None:
        pp = _iv_at_moneyness(puts, spot=spot, target_pct=0.05, is_put=True)
        cc = _iv_at_moneyness(calls, spot=spot, target_pct=0.05, is_put=False)
        if pp is not None and cc is not None:
            skew_25 = pp - cc
            basis = "moneyness"
    if skew_10 is None:
        pp = _iv_at_moneyness(puts, spot=spot, target_pct=0.10, is_put=True)
        cc = _iv_at_moneyness(calls, spot=spot, target_pct=0.10, is_put=False)
        if pp is not None and cc is not None:
            skew_10 = pp - cc
            if basis is None:
                basis = "moneyness"

    primary = skew_25 if skew_25 is not None else skew_10
    if primary is None:
        return out

    if primary >= _SKEW_PUT_HEAVY:
        state = "PUT_HEDGE_DEMAND"
    elif primary < _SKEW_CALL_RICH:
        state = "CALL_CHASE"
    else:
        state = "BALANCED"

    out.update({
        "put_call_skew_25d": round(skew_25, 4) if skew_25 is not None else None,
        "put_call_skew_10d": round(skew_10, 4) if skew_10 is not None else None,
        "skew_basis": basis,
        "skew_state": state,
        "skew_confidence": "ok" if basis == "delta" else "low",
    })
    return out


# ══════════════════════════════════════════════════════════════════════════════
# Task 6 — term structure + interpolated IV
# ══════════════════════════════════════════════════════════════════════════════

def _interp_iv(points: List[Tuple[int, float]], target_dte: int) -> Optional[float]:
    """Linear-interpolate ATM IV to ``target_dte`` from (dte, iv) points."""
    pts = sorted((d, v) for d, v in points if d is not None and v is not None and v > 0)
    if not pts:
        return None
    if len(pts) == 1:
        return pts[0][1]
    # exact / bracketed
    for i in range(len(pts) - 1):
        d0, v0 = pts[i]
        d1, v1 = pts[i + 1]
        if d0 <= target_dte <= d1:
            if d1 == d0:
                return v0
            frac = (target_dte - d0) / (d1 - d0)
            return v0 + frac * (v1 - v0)
    # extrapolate flat to nearest endpoint
    if target_dte < pts[0][0]:
        return pts[0][1]
    return pts[-1][1]


def term_structure(iv_points: List[Tuple[int, float]]) -> Dict[str, Any]:
    """Classify front-vs-30d IV. iv_points = [(dte, atm_iv), ...]."""
    out = {
        "term_structure_state": NOT_ENOUGH_DATA,
        "front_iv": None,
        "iv_30d_estimate": None,
        "front_minus_30d": None,
    }
    usable = [(d, v) for d, v in iv_points if d is not None and v and v > 0]
    if len(usable) < 2:
        # still expose 30d estimate if a single point exists
        iv30 = _interp_iv(usable, 30) if usable else None
        out["iv_30d_estimate"] = round(iv30, 4) if iv30 else None
        return out

    front_pts = sorted(d for d, _ in usable)
    front_dte = front_pts[0]
    front_iv = _interp_iv(usable, front_dte)
    iv30 = _interp_iv(usable, 30)
    if front_iv is None or iv30 is None:
        return out

    diff = front_iv - iv30
    if diff <= _TS_CONTANGO:
        state = "NORMAL"
    elif diff < _TS_FRONT_PREMIUM:
        # only "FRONT_PREMIUM" once it clears flat; otherwise still NORMAL-ish
        state = "FRONT_PREMIUM" if diff > 0 else "NORMAL"
    elif diff < _TS_EVENT_PREMIUM:
        state = "FRONT_PREMIUM"
    else:
        # very steep front premium → event/panic or backwardation-like
        state = "EVENT_PREMIUM"
    # explicit backwardation flag when the whole curve inverts hard
    if diff >= _TS_EVENT_PREMIUM and front_dte <= _FRONT_DTE_MAX:
        # distinguish a single-event hump from a sustained inversion
        back_pts = [v for d, v in usable if d >= 30]
        if back_pts and front_iv - max(back_pts) >= _TS_EVENT_PREMIUM:
            state = "BACKWARDATION_LIKE"

    out.update({
        "term_structure_state": state,
        "front_iv": round(front_iv, 4),
        "iv_30d_estimate": round(iv30, 4),
        "front_minus_30d": round(diff, 4),
    })
    return out


# ══════════════════════════════════════════════════════════════════════════════
# Task 5 — IV rank / percentile from history
# ══════════════════════════════════════════════════════════════════════════════

def iv_rank_percentile(series: List[float], today_iv: Optional[float]) -> Dict[str, Any]:
    """IV rank + percentile of today_iv within ``series`` (history excl today)."""
    vals = [float(v) for v in series if v is not None and float(v) > 0]
    n = len(vals)
    if today_iv is None or n < _IV_HISTORY_MIN_OBS:
        return {"iv_rank": None, "iv_percentile": None, "n": n}
    lo, hi = min(vals), max(vals)
    rank = ((today_iv - lo) / (hi - lo) * 100.0) if hi > lo else None
    if rank is not None:
        rank = max(0.0, min(100.0, rank))
    pct = sum(1 for v in vals if v < today_iv) / n * 100.0
    return {
        "iv_rank": round(rank, 1) if rank is not None else None,
        "iv_percentile": round(pct, 1),
        "n": n,
    }


def _iv_state_from_rank(rank: Optional[float], n: int) -> str:
    if rank is None or n < _IV_HISTORY_MIN_OBS:
        return "NOT_ENOUGH_HISTORY"
    if rank >= _IV_RANK_EXTREME:
        return "EXTREME_IV"
    if rank >= _IV_RANK_HIGH:
        return "HIGH_IV"
    if rank >= _IV_RANK_LOW:
        return "NORMAL_IV"
    return "LOW_IV"


# ══════════════════════════════════════════════════════════════════════════════
# Task 7 — regime summary
# ══════════════════════════════════════════════════════════════════════════════

def classify_regime(*, gamma_regime: str, skew_state: str, iv_state: str,
                     term_state: str) -> Dict[str, Any]:
    """Deterministic composite label from the four sub-diagnostics."""
    reasons: List[str] = []
    gamma_pos = gamma_regime == "positive_gamma_regime"
    gamma_neg = gamma_regime == "negative_gamma_regime"
    high_iv = iv_state in ("HIGH_IV", "EXTREME_IV")
    extreme_iv = iv_state == "EXTREME_IV"
    low_iv = iv_state == "LOW_IV"
    put_skew = skew_state == "PUT_HEDGE_DEMAND"
    call_skew = skew_state == "CALL_CHASE"
    stress_term = term_state in ("EVENT_PREMIUM", "BACKWARDATION_LIKE")

    have_any = any(s not in (NOT_ENOUGH_DATA, "NOT_ENOUGH_HISTORY", None)
                   for s in (gamma_regime, skew_state, iv_state, term_state))
    if not have_any:
        return {
            "options_regime": REGIME_NOT_ENOUGH_DATA,
            "risk_warning_level": "LOW",
            "preferred_expression_context": [],
            "reason_codes": ["insufficient_options_data"],
        }

    regime = REGIME_MIXED

    if (extreme_iv or stress_term) and (gamma_neg or put_skew):
        regime = REGIME_HIGH_VOL_STRESS
        reasons += ["extreme_iv_or_stress_term", "negative_gamma_or_put_hedging"]
    elif put_skew and gamma_neg and high_iv:
        regime = REGIME_FRAGILE_HEDGING
        reasons += ["put_hedge_demand", "negative_gamma_proxy", "elevated_iv"]
    elif put_skew and (gamma_neg or high_iv):
        regime = REGIME_FRAGILE_HEDGING
        reasons += ["put_hedge_demand", "gamma_or_iv_warning"]
    elif call_skew and not gamma_pos:
        regime = REGIME_CALL_CHASE_RISK
        reasons += ["call_chase_skew", "no_positive_gamma_cushion"]
    elif gamma_pos and low_iv and not put_skew:
        regime = REGIME_BULLISH_STABLE
        reasons += ["positive_gamma_proxy", "low_iv", "no_put_hedge_demand"]
    elif gamma_pos and not high_iv and not put_skew:
        regime = REGIME_CALM_RANGE
        reasons += ["positive_gamma_proxy", "contained_iv"]
    else:
        reasons += ["mixed_or_conflicting_signals"]

    # risk warning ladder
    if regime in (REGIME_HIGH_VOL_STRESS,):
        risk = "HIGH"
    elif regime in (REGIME_FRAGILE_HEDGING, REGIME_CALL_CHASE_RISK):
        risk = "MEDIUM"
    else:
        risk = "LOW"

    # preferred expression context (research framing only — not an order)
    ctx: List[str] = []
    if regime == REGIME_CALM_RANGE:
        ctx = ["credit_spreads_possible"]
    elif regime == REGIME_BULLISH_STABLE:
        ctx = ["debit_spreads_favored"]
    elif regime == REGIME_FRAGILE_HEDGING:
        ctx = ["reduce_size", "avoid_new_options"]
    elif regime == REGIME_CALL_CHASE_RISK:
        ctx = ["reduce_size", "wait_for_reset"]
    elif regime == REGIME_HIGH_VOL_STRESS:
        ctx = ["avoid_new_options", "wait_for_reset"]
    else:
        ctx = ["reduce_size"]

    return {
        "options_regime": regime,
        "risk_warning_level": risk,
        "preferred_expression_context": ctx,
        "reason_codes": reasons,
    }


# ══════════════════════════════════════════════════════════════════════════════
# per-symbol analysis (pure: given a feed + spot)
# ══════════════════════════════════════════════════════════════════════════════

def _pick_expiries(expirations: List[str], *, today: Optional[date] = None) -> List[str]:
    """Pick a spread of expirations: nearest, plus ones bracketing 30/45/60 DTE."""
    dated = [(e, _dte(e, today=today)) for e in expirations]
    dated = [(e, d) for e, d in dated if d is not None and d >= 0]
    dated.sort(key=lambda ed: ed[1])
    if not dated:
        return []
    picks: List[str] = []
    # nearest
    picks.append(dated[0][0])
    # closest to each target DTE
    for tgt in _TARGET_DTE:
        best = min(dated, key=lambda ed: abs(ed[1] - tgt))
        if best[0] not in picks:
            picks.append(best[0])
    return picks[:_MAX_EXPIRIES]


def analyze_symbol(
    sym: str,
    *,
    feed: Any,
    spot: Optional[float],
    history_series: Optional[List[float]] = None,
    today: Optional[date] = None,
) -> Dict[str, Any]:
    """Compute all sub-diagnostics for one symbol from its options chain.

    ``feed`` must expose get_expirations(sym) and get_chain(sym, expiry).
    ``history_series`` is the symbol's prior 30d-IV observations (excl today)
    for IV rank; pass None/empty to force NOT_ENOUGH_HISTORY.
    """
    today = today or date.today()
    res: Dict[str, Any] = {
        "symbol": sym,
        "spot": spot,
        "data_quality": "ok",
    }
    if feed is None or spot is None or spot <= 0:
        res["data_quality"] = NOT_ENOUGH_DATA
        res.update(_empty_symbol_blocks())
        return res

    try:
        expirations = list(feed.get_expirations(sym))
    except Exception:
        expirations = []
    picks = _pick_expiries(expirations, today=today)
    if not picks:
        res["data_quality"] = NOT_ENOUGH_DATA
        res.update(_empty_symbol_blocks())
        return res

    iv_points: List[Tuple[int, float]] = []
    front_calls = front_puts = None
    front_dte = None
    for expiry in picks:
        try:
            chain = feed.get_chain(sym, expiry)
        except Exception:
            chain = None
        if not chain:
            continue
        calls = chain.get("calls")
        puts = chain.get("puts")
        d = _dte(expiry, today=today)
        atm = _blend_atm_iv(calls, puts, spot=spot)
        if atm is not None and d is not None:
            iv_points.append((d, atm))
        if front_dte is None or (d is not None and d < front_dte):
            front_dte, front_calls, front_puts = d, calls, puts

    if front_calls is None and front_puts is None:
        res["data_quality"] = NOT_ENOUGH_DATA
        res.update(_empty_symbol_blocks())
        return res

    gam = gamma_proxy(front_calls, front_puts, spot=spot)
    skew = skew_diagnostics(front_calls, front_puts, spot=spot)
    ts = term_structure(iv_points)

    iv30 = ts.get("iv_30d_estimate")
    ivrp = iv_rank_percentile(history_series or [], iv30)
    iv_state = _iv_state_from_rank(ivrp.get("iv_rank"), ivrp.get("n", 0))

    regime = classify_regime(
        gamma_regime=gam.get("gamma_regime", NOT_ENOUGH_DATA),
        skew_state=skew.get("skew_state", NOT_ENOUGH_DATA),
        iv_state=iv_state,
        term_state=ts.get("term_structure_state", NOT_ENOUGH_DATA),
    )

    res.update({
        "atm_iv_30d": iv30,
        "iv_rank_30d": ivrp.get("iv_rank"),
        "iv_percentile_30d": ivrp.get("iv_percentile"),
        "iv_history_count": ivrp.get("n", 0),
        "iv_state": iv_state,
        "gamma": gam,
        "skew": skew,
        "term_structure": ts,
        **regime,
    })
    return res


def _empty_symbol_blocks() -> Dict[str, Any]:
    return {
        "atm_iv_30d": None,
        "iv_rank_30d": None,
        "iv_percentile_30d": None,
        "iv_history_count": 0,
        "iv_state": "NOT_ENOUGH_HISTORY",
        "gamma": {"gamma_regime": NOT_ENOUGH_DATA, "gamma_confidence": "none"},
        "skew": {"skew_state": NOT_ENOUGH_DATA, "skew_confidence": "none"},
        "term_structure": {"term_structure_state": NOT_ENOUGH_DATA},
        "options_regime": REGIME_NOT_ENOUGH_DATA,
        "risk_warning_level": "LOW",
        "preferred_expression_context": [],
        "reason_codes": ["insufficient_options_data"],
    }


# ══════════════════════════════════════════════════════════════════════════════
# Task 5 — history persistence (idempotent per symbol/day)
# ══════════════════════════════════════════════════════════════════════════════

def read_history(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    p = path or HISTORY_PATH
    rows: List[Dict[str, Any]] = []
    if not p.exists():
        return rows
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    except Exception:
        pass
    return rows


def history_series_for(rows: List[Dict[str, Any]], sym: str,
                       *, exclude_date: Optional[str] = None) -> List[float]:
    """30d-IV observations for ``sym`` (latest row per date), oldest first."""
    by_date: Dict[str, float] = {}
    for r in rows:
        if r.get("symbol") != sym:
            continue
        d = r.get("generated_at_date") or (r.get("generated_at") or "")[:10]
        if not d or d == exclude_date:
            continue
        iv = r.get("iv_30d_estimate")
        if iv is None:
            continue
        by_date[d] = float(iv)  # latest row for the date wins
    return [by_date[d] for d in sorted(by_date)]


def append_history(snapshots: List[Dict[str, Any]], *, path: Optional[Path] = None,
                   today_iso: Optional[str] = None) -> int:
    """Append one row per symbol for today, skipping (symbol, date) already
    present so re-running the same day is idempotent. Returns rows written."""
    p = path or HISTORY_PATH
    today_iso = today_iso or date.today().isoformat()
    existing = read_history(p)
    have = {
        (r.get("symbol"),
         r.get("generated_at_date") or (r.get("generated_at") or "")[:10])
        for r in existing
    }
    written = 0
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            for snap in snapshots:
                sym = snap.get("symbol")
                if (sym, today_iso) in have:
                    continue
                # Skip degraded snapshots (no usable 30d IV) so a feed-
                # unconfigured / no-chain run does not poison the day's slot
                # and block a later good run from recording the real series.
                if snap.get("atm_iv_30d") is None:
                    continue
                row = {
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "generated_at_date": today_iso,
                    "symbol": sym,
                    "atm_iv": snap.get("atm_iv_30d"),
                    "iv_30d_estimate": snap.get("atm_iv_30d"),
                    "skew": (snap.get("skew") or {}).get("put_call_skew_25d"),
                    "gamma_proxy": (snap.get("gamma") or {}).get("total_gamma_proxy"),
                    "term_structure": (snap.get("term_structure") or {}).get(
                        "term_structure_state"),
                    "source_quality": snap.get("data_quality"),
                }
                f.write(json.dumps(row, default=str) + "\n")
                have.add((sym, today_iso))
                written += 1
    except Exception:
        pass
    return written


# ══════════════════════════════════════════════════════════════════════════════
# top-level run
# ══════════════════════════════════════════════════════════════════════════════

def _market_rollup(symbols: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Market-level read, SPY-anchored, with QQQ/IWM as confirmation."""
    by_sym = {s["symbol"]: s for s in symbols}
    anchor = None
    for pref in ("SPY", "QQQ", "IWM"):
        if pref in by_sym and by_sym[pref].get("options_regime") != REGIME_NOT_ENOUGH_DATA:
            anchor = by_sym[pref]
            break
    if anchor is None:
        # first symbol with data, else not enough data
        for s in symbols:
            if s.get("options_regime") != REGIME_NOT_ENOUGH_DATA:
                anchor = s
                break
    if anchor is None:
        return {
            "market_options_regime": REGIME_NOT_ENOUGH_DATA,
            "risk_warning_level": "LOW",
            "anchor_symbol": None,
            "reason_codes": ["insufficient_options_data"],
        }
    return {
        "market_options_regime": anchor.get("options_regime"),
        "risk_warning_level": anchor.get("risk_warning_level", "LOW"),
        "anchor_symbol": anchor.get("symbol"),
        "skew_state": (anchor.get("skew") or {}).get("skew_state"),
        "iv_state": anchor.get("iv_state"),
        "gamma_regime": (anchor.get("gamma") or {}).get("gamma_regime"),
        "term_structure_state": (anchor.get("term_structure") or {}).get(
            "term_structure_state"),
        "preferred_expression_context": anchor.get("preferred_expression_context", []),
        "reason_codes": anchor.get("reason_codes", []),
    }


def run(
    symbols: Optional[List[str]] = None,
    *,
    feed: Any = "auto",
    history_path: Optional[Path] = None,
    write: bool = True,
    today: Optional[date] = None,
) -> Dict[str, Any]:
    """Run the lens over ``symbols`` and (optionally) persist artifacts.

    ``feed="auto"`` loads the shared options chain; pass an explicit feed
    (or None) for tests. Provider calls happen here only.
    """
    syms = [s.upper() for s in (symbols or DEFAULT_SYMBOLS)]
    today = today or date.today()
    today_iso = today.isoformat()
    if feed == "auto":
        feed = _load_options_feed()

    hist_rows = read_history(history_path)

    per_symbol: List[Dict[str, Any]] = []
    for sym in syms:
        spot = _get_spot(sym) if feed is not None else None
        series = history_series_for(hist_rows, sym, exclude_date=today_iso)
        res = analyze_symbol(sym, feed=feed, spot=spot,
                             history_series=series, today=today)
        per_symbol.append(res)

    # persist today's snapshot (idempotent), then rollup
    if write:
        append_history(per_symbol, path=history_path, today_iso=today_iso)

    payload = {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_at_date": today_iso,
        "feed_configured": feed is not None,
        "feed_status": (feed.status() if hasattr(feed, "status") else None),
        "symbols_requested": syms,
        "market": _market_rollup(per_symbol),
        "per_symbol": per_symbol,
        "disclaimer": (
            "Research-only options regime diagnostics. GEX is a PROXY (assumes "
            "dealers long calls / short puts) and does not claim exact dealer "
            "positioning. Not a trade signal, not a veto, not an approval."
        ),
    }

    if write:
        _write_artifacts(payload)
    return payload


def _write_artifacts(payload: Dict[str, Any]) -> None:
    try:
        JSON_OUT.parent.mkdir(parents=True, exist_ok=True)
        tmp = JSON_OUT.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        tmp.replace(JSON_OUT)
    except Exception:
        pass
    try:
        TXT_OUT.parent.mkdir(parents=True, exist_ok=True)
        TXT_OUT.write_text(render_text(payload), encoding="utf-8")
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# rendering
# ══════════════════════════════════════════════════════════════════════════════

def render_text(payload: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append(f"OPTIONS REGIME LENS (research-only) — {payload.get('generated_at')}")
    mk = payload.get("market") or {}
    lines.append(
        f"MARKET: {mk.get('market_options_regime')} "
        f"(anchor {mk.get('anchor_symbol')}) "
        f"risk={mk.get('risk_warning_level')}  "
        f"skew={mk.get('skew_state')}  iv={mk.get('iv_state')}  "
        f"gamma={mk.get('gamma_regime')}  term={mk.get('term_structure_state')}"
    )
    if not payload.get("feed_configured"):
        lines.append("  (options feed not configured — symbols degrade to NOT_ENOUGH_DATA)")
    lines.append("")
    for s in payload.get("per_symbol", []):
        g = s.get("gamma") or {}
        sk = s.get("skew") or {}
        ts = s.get("term_structure") or {}
        lines.append(
            f"{s.get('symbol'):5} regime={s.get('options_regime'):17} "
            f"risk={s.get('risk_warning_level'):6} "
            f"gamma={g.get('gamma_regime')}({g.get('gamma_confidence')}) "
            f"skew={sk.get('skew_state')} iv={s.get('iv_state')}"
            f"(rank={s.get('iv_rank_30d')}) term={ts.get('term_structure_state')}"
        )
        wall = g.get("nearest_gamma_wall")
        if wall:
            lines.append(
                f"      gamma_wall@{wall.get('strike')} "
                f"({wall.get('dist_pct')}% from spot)  "
                f"zero_gamma={g.get('zero_gamma_estimate')}  "
                f"total_gex_proxy={g.get('total_gamma_proxy')}MM/1%"
            )
        ctx = s.get("preferred_expression_context") or []
        if ctx:
            lines.append(f"      context: {', '.join(ctx)}")
    lines.append("")
    lines.append(payload.get("disclaimer", ""))
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Options Regime Lens (research-only)")
    p.add_argument("symbols", nargs="*", help="Symbols (default: SPY QQQ IWM VXX)")
    p.add_argument("--print", dest="do_print", action="store_true",
                   help="Print the text report to stdout")
    p.add_argument("--no-write", dest="no_write", action="store_true",
                   help="Do not write sidecars / history (dry run)")
    args = p.parse_args(argv)

    payload = run(args.symbols or None, write=not args.no_write)
    if args.do_print or args.no_write:
        print(render_text(payload))
    else:
        mk = payload.get("market") or {}
        print(f"options regime: {mk.get('market_options_regime')} "
              f"(anchor {mk.get('anchor_symbol')}) → {JSON_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

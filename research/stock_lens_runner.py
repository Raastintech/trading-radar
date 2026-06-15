"""
research/stock_lens_runner.py — runner for the single-stock research lens.

Invoked from ``research/regime_forecast.py`` when ``--ticker`` is supplied.

Loads each research layer (cache-first) and hands them to
``core.stock_research_lens.build_stock_lens``.  Writes JSON + text artifacts.

Layer sources (each is optional and degrades gracefully):
  - Stock OHLCV: cache → Alpaca → FMP → yfinance (reuses regime_forecast loader)
  - SPY OHLCV: same chain (already cached for the regime forecaster)
  - Market regime: cache/research/regime_forecast_latest.json (cache-only;
    if --refresh, recompute via core.regime_forecaster.build_forecast)
  - Sector mapping: FMP get_company_profile (24h Gatekeeper cache)
  - Daily Entry Validator: core.daily_entry_validator.validate_daily_entry
  - Alpha Discovery: cache/research/alpha_discovery_board_latest.json,
                     cache/research/alpha_discovery_overlay_latest.json
  - Market Posture: rebuild via core.research_assist_bte.build_research_bte
                    against cache/universe/universe_snapshot_latest.json
  - Social Arb: cache/research/social_arb_latest.json
  - Options / 13F: not wired in V1 (placeholder hooks for future work)

Guardrails:
  - research-only / not trade approval / not paper evidence
  - no execution, no governance, no sleeve mutation
  - no Alpha Discovery / Market Posture / Daily Entry Validator / Social
    Arb logic changes
  - cache-first; --cache-only / --offline is honoured
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

import core.config as cfg
from core.stock_research_lens import (
    VERSION as LENS_VERSION,
    map_sector_to_etf,
    build_stock_lens,
)
from core.daily_entry_validator import validate_daily_entry
from core.research_assist_bte import build_research_bte


logger = logging.getLogger("stock_lens_runner")


RESEARCH_DIR = cfg.CACHE_DIR / "research"
UNIVERSE_SNAPSHOT_PATH = cfg.CACHE_DIR / "universe" / "universe_snapshot_latest.json"
ALPHA_BOARD_PATH = RESEARCH_DIR / "alpha_discovery_board_latest.json"
ALPHA_OVERLAY_PATH = RESEARCH_DIR / "alpha_discovery_overlay_latest.json"
SOCIAL_ARB_PATH = RESEARCH_DIR / "social_arb_latest.json"
MARKET_FORECAST_PATH = RESEARCH_DIR / "regime_forecast_latest.json"


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception as exc:
        logger.info("json read failed for %s: %s", path, exc)
        return None


# ── Ticker price loading: cache → Alpaca → FMP → yfinance ──────────────────


def _load_ticker_frame(ticker: str, *, offline: bool, no_fmp: bool) -> Optional[pd.DataFrame]:
    """
    Load deep daily history for a single ticker.  Reuses the regime_forecast
    loader's helpers so the cache/Alpaca/FMP/yfinance order is identical and
    each fetch persists back to the local parquet cache.
    """
    from research import regime_forecast as rf

    sym = ticker.upper()
    df = rf._load_cached_frame(sym)
    fresh = df is not None and rf._frame_is_fresh(df, stale_threshold_h=24.0)

    if fresh or offline:
        return df

    # Alpaca
    from_alpaca = rf._ensure_alpaca_frames([sym])
    if sym in from_alpaca:
        return from_alpaca[sym]

    # FMP
    if not no_fmp:
        from_fmp = rf._ensure_fmp_frames([sym])
        if sym in from_fmp:
            return from_fmp[sym]

    # yfinance
    from_yf = rf._ensure_yfinance_frames([sym])
    if sym in from_yf:
        rf._persist_frame_to_local_cache(sym, from_yf[sym])
        return from_yf[sym]

    return df  # last-ditch: stale cache is better than nothing


def _load_spy_frame(*, offline: bool, no_fmp: bool) -> Optional[pd.DataFrame]:
    """SPY is the relative-strength benchmark and the regime anchor."""
    return _load_ticker_frame("SPY", offline=offline, no_fmp=no_fmp)


# ── Market regime: cache or recompute ──────────────────────────────────────


def _load_or_recompute_market_forecast(args: argparse.Namespace) -> Optional[Dict[str, Any]]:
    if not args.refresh:
        cached = _load_json(MARKET_FORECAST_PATH)
        if cached:
            return cached
    # Recompute only if explicitly requested or cache missing.
    try:
        from research import regime_forecast as rf
        return rf.build_artifact(args)
    except Exception as exc:
        logger.info("market regime recompute failed: %s", exc)
        return None


# ── Sector profile ─────────────────────────────────────────────────────────


def _load_sector_profile(ticker: str, *, offline: bool) -> Optional[Dict[str, Any]]:
    if offline:
        return None
    try:
        from core.fmp_client import get_fmp
        return get_fmp().get_company_profile(ticker)
    except Exception as exc:
        logger.info("FMP profile lookup failed for %s: %s", ticker, exc)
        return None


# ── Universe snapshot → Market Posture ─────────────────────────────────────


def _load_market_posture() -> Optional[Dict[str, Any]]:
    snapshot = _load_json(UNIVERSE_SNAPSHOT_PATH)
    if not snapshot:
        return None
    try:
        out = build_research_bte(
            universe_snapshot=snapshot,
            regime=snapshot.get("regime") or {},
            vix=None,
        )
        # Convert dataclass → dict for the lens consumer.
        return {
            "state": out.state,
            "bias": out.bias,
            "confidence": out.confidence,
            "focus_names": out.focus_names,
            "ready_long_names": out.ready_long_names,
            "data_quality": out.data_quality,
        }
    except Exception as exc:
        logger.info("Market Posture rebuild failed: %s", exc)
        return None


# ── Alpha Discovery ────────────────────────────────────────────────────────


def _load_alpha_artifacts() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for path in (ALPHA_BOARD_PATH, ALPHA_OVERLAY_PATH):
        data = _load_json(path)
        if data:
            data.setdefault("source", path.name)
            out.append(data)
    return out


# ── Social Arb ─────────────────────────────────────────────────────────────


def _load_social_arb() -> Optional[Dict[str, Any]]:
    return _load_json(SOCIAL_ARB_PATH)


# ── Options (Alpaca primary; Tradier fallback) ─────────────────────────────
#
# The actual feed loader lives in core/options_feed_factory.py so alpha
# discovery, social arb radar, and this module all use one shared chain.

def _load_options_feed():
    try:
        from core.options_feed_factory import load_options_feed
        return load_options_feed()
    except Exception as exc:
        logger.info("Options feed factory import failed: %s", exc)
        return None


# ── Options Quality V2: multi-expiry, spot-based ATM IV, spread quality,
#    and IV history persistence.  All research-only; the Stock Lens
#    classifier (core.stock_research_lens._options_layer) interprets the
#    raw features produced here.
IV_HISTORY_PATH = RESEARCH_DIR / "options_iv_history.jsonl"
IV_HISTORY_MIN_OBS = 30

_DTE_BUCKETS = (
    ("front",    7,  21),
    ("swing",   21,  45),
    ("position",45,  90),
)


def _filter_liquid_strikes(df):
    try:
        oi = df["openInterest"].fillna(0).astype(float)
        vol = df["volume"].fillna(0).astype(float)
        return df[(oi >= 50) | (vol >= 25)]
    except Exception:
        return df


def _atm_iv_from_chain(df, *, spot: Optional[float], side: str) -> Optional[float]:
    """
    ATM IV averaged over the 3 strikes nearest spot.  If spot is missing,
    fall back to Tradier's ``inTheMoney`` boundary (V1 behaviour).
    """
    if df is None or df.empty:
        return None
    try:
        if spot is not None and spot > 0:
            anchor = float(spot)
        else:
            itm = df[df["inTheMoney"] == True]
            if not itm.empty:
                anchor = float(itm["strike"].max() if side == "call" else itm["strike"].min())
            else:
                otm = df[df["inTheMoney"] == False]
                if otm.empty:
                    return None
                anchor = float(otm["strike"].min() if side == "call" else otm["strike"].max())
        if anchor is None or anchor != anchor:
            return None
        diffs = (df["strike"].astype(float) - anchor).abs()
        idx = diffs.nsmallest(3).index
        ivs = [float(v) for v in df.loc[idx, "impliedVolatility"].values if v and v > 0]
        if not ivs:
            return None
        return sum(ivs) / len(ivs)
    except Exception:
        return None


def _spread_stats(df) -> Dict[str, Any]:
    """
    Per-chain bid/ask spread quality.  Returns median spread % across
    contracts that have usable bid+ask, plus a chain-level grade.
    """
    out = {"median_spread_pct": None, "n_usable": 0, "grade": "unknown"}
    try:
        usable = df[(df["bid"].fillna(0) > 0) & (df["ask"].fillna(0) > 0)]
        usable = usable[usable["ask"] > usable["bid"]]
        if usable.empty:
            return out
        mid = (usable["ask"].astype(float) + usable["bid"].astype(float)) / 2.0
        spread = (usable["ask"].astype(float) - usable["bid"].astype(float)) / mid
        spread = spread[spread <= 2.0]  # drop pathological outliers
        if spread.empty:
            return out
        med = float(spread.median())
        out["median_spread_pct"] = round(med, 4)
        out["n_usable"] = int(len(usable))
        if med <= 0.05:
            out["grade"] = "good"
        elif med <= 0.15:
            out["grade"] = "ok"
        elif med <= 0.30:
            out["grade"] = "poor"
        else:
            out["grade"] = "unusable"
        return out
    except Exception:
        return out


def _per_expiry_features(
    sym: str,
    expiry: str,
    chain: Dict[str, Any],
    *,
    spot: Optional[float],
) -> Optional[Dict[str, Any]]:
    """Compute the raw, liquidity-filtered features for a single expiry."""
    calls_df = chain.get("calls")
    puts_df = chain.get("puts")
    if calls_df is None or puts_df is None or calls_df.empty or puts_df.empty:
        return None

    calls_l = _filter_liquid_strikes(calls_df)
    puts_l = _filter_liquid_strikes(puts_df)

    call_oi = float(calls_l["openInterest"].sum() or 0)
    put_oi = float(puts_l["openInterest"].sum() or 0)
    call_vol = float(calls_l["volume"].sum() or 0)
    put_vol = float(puts_l["volume"].sum() or 0)

    oi_tilt = (call_oi + 1.0) / (put_oi + 1.0)
    vol_tilt = (call_vol + 1.0) / (put_vol + 1.0)

    atm_call_iv = _atm_iv_from_chain(calls_l, spot=spot, side="call")
    atm_put_iv = _atm_iv_from_chain(puts_l, spot=spot, side="put")
    iv_skew = None
    if atm_call_iv and atm_put_iv and atm_call_iv > 0:
        iv_skew = atm_put_iv / atm_call_iv
    iv_blend = None
    if atm_call_iv and atm_put_iv:
        iv_blend = (atm_call_iv + atm_put_iv) / 2.0

    # Spread quality — combine call + put medians by row-stacking.
    try:
        combined = pd.concat([calls_l, puts_l], ignore_index=True)
    except Exception:
        combined = calls_l
    spread = _spread_stats(combined)

    total_oi = call_oi + put_oi
    total_vol = call_vol + put_vol
    n_strikes = int(len(calls_l) + len(puts_l))
    if n_strikes < 4 or total_oi < 200:
        liquidity = "thin"
    elif total_oi < 1000 or total_vol < 100:
        liquidity = "low"
    else:
        liquidity = "ok"

    return {
        "expiry": expiry,
        "dte": _dte_for(expiry),
        "call_oi": call_oi,
        "put_oi": put_oi,
        "call_vol": call_vol,
        "put_vol": put_vol,
        "oi_tilt": round(oi_tilt, 3),
        "vol_tilt": round(vol_tilt, 3),
        "atm_call_iv": atm_call_iv,
        "atm_put_iv": atm_put_iv,
        "atm_iv_blend": iv_blend,
        "iv_skew": round(iv_skew, 3) if iv_skew else None,
        "n_call_strikes": int(len(calls_l)),
        "n_put_strikes": int(len(puts_l)),
        "total_oi": total_oi,
        "total_vol": total_vol,
        "liquidity_grade": liquidity,
        "spread_grade": spread["grade"],
        "spread_median_pct": spread["median_spread_pct"],
        "spread_n_usable": spread["n_usable"],
    }


def _dte_for(expiry: str, *, today: Optional[date] = None) -> Optional[int]:
    try:
        d = datetime.strptime(str(expiry)[:10], "%Y-%m-%d").date()
        return (d - (today or date.today())).days
    except Exception:
        return None


def _pick_expiries_by_bucket(expirations: List[str]) -> Dict[str, str]:
    """
    Choose at most one expiry per DTE bucket.  Within a bucket, prefer the
    expiry closest to the bucket midpoint so we avoid the volatile
    front-week (e.g., earnings the next day) when a calmer alternative
    exists.
    """
    today = date.today()
    candidates = []
    for exp in expirations:
        d = _dte_for(exp, today=today)
        if d is None or d < 0:
            continue
        candidates.append((d, exp))
    candidates.sort()
    chosen: Dict[str, str] = {}
    for label, lo, hi in _DTE_BUCKETS:
        in_band = [(d, e) for d, e in candidates if lo <= d <= hi]
        if not in_band:
            continue
        midpoint = (lo + hi) / 2.0
        in_band.sort(key=lambda t: abs(t[0] - midpoint))
        chosen[label] = in_band[0][1]
    # Fallback: if no front but there is a near-dated expiry, surface it as
    # "front" so the lens isn't blind on event-week names.
    if "front" not in chosen and candidates:
        chosen["front"] = candidates[0][1]
    return chosen


def _aggregate_expiries(per_expiry: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Combine per-expiry features into chain-level signals."""
    if not per_expiry:
        return {}

    import math as _m

    def _tilt_log2(t: float) -> float:
        if t is None or t <= 0:
            return 0.0
        return max(-1.0, min(1.0, _m.log(t) / _m.log(2.0)))

    def _sign(d: Dict[str, Any]) -> str:
        s = 0.6 * _tilt_log2(d.get("oi_tilt", 1.0)) + 0.4 * _tilt_log2(d.get("vol_tilt", 1.0))
        if s >= 0.25:
            return "bull"
        if s <= -0.25:
            return "bear"
        return "neutral"

    signs = {k: _sign(v) for k, v in per_expiry.items() if v}
    available_buckets = list(per_expiry.keys())
    bullish_buckets = [k for k, s in signs.items() if s == "bull"]
    bearish_buckets = [k for k, s in signs.items() if s == "bear"]

    front = per_expiry.get("front") or {}
    swing = per_expiry.get("swing") or {}
    position = per_expiry.get("position") or {}

    front_iv = front.get("atm_iv_blend")
    swing_iv = swing.get("atm_iv_blend")
    pos_iv = position.get("atm_iv_blend")

    # Event-style front IV spike: front clearly elevated vs swing/position.
    event_iv_spike = False
    reference_iv = swing_iv or pos_iv
    if front_iv and reference_iv and reference_iv > 0:
        event_iv_spike = front_iv > reference_iv * 1.3

    # Pattern resolution.  Front-only chase requires an explicit non-bullish
    # signal at the back so we don't mislabel single-bucket coverage.
    pattern = "uncertain"
    back_signs = [signs.get(k) for k in ("swing", "position") if k in signs]
    front_sign = signs.get("front")
    if len(signs) >= 2 and bullish_buckets and len(bullish_buckets) == len(signs):
        pattern = "broad_confirmation"
    elif (signs.get("swing") == "bull" or signs.get("position") == "bull") and front_sign != "bear":
        pattern = "back_month_confirmation"
    elif (
        front_sign == "bull"
        and back_signs
        and all(s in {"neutral", "bear"} for s in back_signs)
    ):
        pattern = "front_only_chase"
    elif bearish_buckets:
        pattern = "bearish_across_expiries"
    elif all(s == "neutral" for s in signs.values()):
        pattern = "neutral_across_expiries"
    elif len(signs) == 1 and front_sign == "bull":
        pattern = "front_only_coverage"
    elif len(signs) == 1 and front_sign == "bear":
        pattern = "front_only_bearish"

    # Put-skew warning surfaces independently of direction; pick the worst
    # observed skew across covered expiries (≥1.10 means puts are richer).
    skews = [v.get("iv_skew") for v in per_expiry.values() if v and v.get("iv_skew")]
    max_skew = max(skews) if skews else None
    put_skew_warning = bool(max_skew and max_skew >= 1.10)

    # Spread quality at the chain level: take the worst grade across
    # covered expiries — if any expiry is unusable, the chain should be
    # treated as suspect.
    grade_rank = {"good": 3, "ok": 2, "poor": 1, "unusable": 0, "unknown": 0}
    grades = [v.get("spread_grade", "unknown") for v in per_expiry.values() if v]
    if grades:
        worst = min(grades, key=lambda g: grade_rank.get(g, 0))
    else:
        worst = "unknown"

    # Canonical bucket: swing > front > position.  Used as the primary
    # source for direction tilts so the front week alone cannot dominate.
    canonical_label = "swing" if swing else ("front" if front else ("position" if position else None))
    canonical = per_expiry.get(canonical_label) if canonical_label else {}

    return {
        "expiries_used": available_buckets,
        "expiry_signs": signs,
        "pattern": pattern,
        "event_iv_spike": event_iv_spike,
        "max_iv_skew": max_skew,
        "put_skew_warning": put_skew_warning,
        "spread_grade_chain": worst,
        "canonical_bucket": canonical_label,
        "canonical_oi_tilt": canonical.get("oi_tilt") if canonical else None,
        "canonical_vol_tilt": canonical.get("vol_tilt") if canonical else None,
        "canonical_liquidity_grade": canonical.get("liquidity_grade") if canonical else None,
        "canonical_total_oi": canonical.get("total_oi") if canonical else None,
        "canonical_total_vol": canonical.get("total_vol") if canonical else None,
        "front_iv": front_iv,
        "swing_iv": swing_iv,
        "position_iv": pos_iv,
    }


def _record_iv_history(
    ticker: str,
    today_iso: str,
    per_expiry: Dict[str, Dict[str, Any]],
    history_path: Optional[Path] = None,
) -> None:
    """
    Append today's IV snapshot to the JSONL store.  One row per
    (ticker, date, bucket).  We don't deduplicate at write time — readers
    keep the latest row per (date, bucket).
    """
    path = history_path or IV_HISTORY_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            for bucket, feats in per_expiry.items():
                if not feats:
                    continue
                row = {
                    "ticker": ticker,
                    "date": today_iso,
                    "bucket": bucket,
                    "expiry": feats.get("expiry"),
                    "atm_call_iv": feats.get("atm_call_iv"),
                    "atm_put_iv": feats.get("atm_put_iv"),
                    "atm_iv_blend": feats.get("atm_iv_blend"),
                    "iv_skew": feats.get("iv_skew"),
                }
                f.write(json.dumps(row) + "\n")
    except Exception as exc:
        logger.info("IV history write failed for %s: %s", ticker, exc)


def _read_iv_history(
    ticker: str,
    history_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Read history rows for the ticker; latest row per (date, bucket) wins."""
    path = history_path or IV_HISTORY_PATH
    if not path.exists():
        return []
    by_key: Dict[tuple, Dict[str, Any]] = {}
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("ticker") != ticker:
                    continue
                key = (r.get("date"), r.get("bucket"))
                by_key[key] = r  # later rows replace earlier
    except Exception as exc:
        logger.info("IV history read failed for %s: %s", ticker, exc)
        return []
    return list(by_key.values())


def _iv_rank_percentile(
    history: List[Dict[str, Any]],
    today_iv: Optional[float],
    *,
    bucket: str = "swing",
) -> Dict[str, Any]:
    """
    Compute IV rank and IV percentile against the ticker's own history for
    the canonical bucket.  Requires at least IV_HISTORY_MIN_OBS prior daily
    observations (today's row is excluded so we don't anchor on ourselves).
    """
    rows = [
        r for r in history
        if r.get("bucket") == bucket and r.get("atm_iv_blend")
    ]
    rows.sort(key=lambda r: r.get("date", ""))
    series = [float(r["atm_iv_blend"]) for r in rows if r["atm_iv_blend"] is not None]
    n = len(series)
    if n < IV_HISTORY_MIN_OBS or today_iv is None:
        return {
            "iv_rank": None,
            "iv_percentile": None,
            "iv_history_count": n,
            "iv_history_status": "insufficient",
        }
    lo = min(series)
    hi = max(series)
    rank = ((today_iv - lo) / (hi - lo) * 100.0) if hi > lo else None
    if rank is not None:
        rank = max(0.0, min(100.0, rank))
    pct = (sum(1 for v in series if v < today_iv) / n) * 100.0
    return {
        "iv_rank": round(rank, 1) if rank is not None else None,
        "iv_percentile": round(pct, 1),
        "iv_history_count": n,
        "iv_history_status": "ok",
    }


def _load_options_layer(
    ticker: str,
    *,
    offline: bool,
    last_price: Optional[float] = None,
    history_path: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """
    Pull up to three Tradier expiries (front / swing / position), compute
    raw liquidity-filtered features per expiry, aggregate multi-expiry
    signals, persist a daily IV snapshot to the history store, and compute
    IV rank/percentile when the history series is long enough.

    The lens classifier in core.stock_research_lens._options_layer is the
    *only* place that translates these features into a quality label —
    this function never decides bullish/bearish.

    Returns ``None`` when the feed is offline / unconfigured / no chain is
    available so the lens cleanly falls back to OPTIONS_MISSING.
    """
    if offline:
        return None
    feed = _load_options_feed()
    if feed is None:
        return None
    sym = ticker.upper()
    try:
        expirations = list(feed.get_expirations(sym))
        if not expirations:
            return None
        picks = _pick_expiries_by_bucket(expirations)
        if not picks:
            return None
        per_expiry: Dict[str, Dict[str, Any]] = {}
        for bucket, expiry in picks.items():
            chain = feed.get_chain(sym, expiry)
            if not chain:
                continue
            feats = _per_expiry_features(sym, expiry, chain, spot=last_price)
            if feats:
                per_expiry[bucket] = feats
        if not per_expiry:
            return None

        agg = _aggregate_expiries(per_expiry)
        today_iso = date.today().isoformat()
        _record_iv_history(sym, today_iso, per_expiry, history_path=history_path)

        # IV rank uses the canonical bucket's blended ATM IV against the
        # ticker's own past observations from the same bucket.  Today's row
        # was just appended; the read path keys on (date, bucket) so we
        # naturally exclude it via the bucket filter when no swing row was
        # written yet, and the ranker simply ignores the just-written row
        # by using the historical series excluding today.
        canonical_bucket = agg.get("canonical_bucket") or "swing"
        history = _read_iv_history(sym, history_path=history_path)
        history_excl_today = [r for r in history if r.get("date") != today_iso]
        canonical_iv = (per_expiry.get(canonical_bucket) or {}).get("atm_iv_blend")
        iv_stats = _iv_rank_percentile(
            history_excl_today,
            canonical_iv,
            bucket=canonical_bucket,
        )

        return {
            "schema": "options_v2",
            "ticker": sym,
            "spot_used": last_price,
            "spot_source": "stock_frame.close" if last_price else "inTheMoney boundary fallback",
            "per_expiry": per_expiry,
            **agg,
            **iv_stats,
        }
    except Exception as exc:
        logger.info("Tradier options layer failed for %s: %s", sym, exc)
        return None


# ── Rendering ──────────────────────────────────────────────────────────────


def _f2(v: Any, suffix: str = "") -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.2f}{suffix}"
    except Exception:
        return "—"


def _pct(v: Any) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):+.2f}%"
    except Exception:
        return "—"


def _render_text(art: Dict[str, Any]) -> str:
    sym = art.get("ticker") or "?"
    lines: List[str] = []
    lines.append("=" * 78)
    lines.append(f"STOCK RESEARCH LENS V1 — {sym}")
    lines.append("=" * 78)
    lines.append(f"built_at        : {art.get('built_at','—')}")
    lines.append(f"company         : {art.get('company') or '—'}")
    lines.append(f"sector / ETF    : {art.get('sector_name') or '—'} / {art.get('sector_etf') or '—'}")
    lines.append(f"industry        : {art.get('industry') or '—'}")
    lines.append("")
    lines.append("HEADLINE")
    lines.append("-" * 78)
    lines.append(art.get("headline") or "—")
    lines.append("")

    lines.append("HORIZON VIEW")
    lines.append("-" * 78)
    hv = art.get("horizon_view") or {}
    for h in ("5d", "10d", "20d"):
        lines.append(f"  {h:4s} : {hv.get(h,'—')}")
    lines.append("")

    lines.append("LAYER AGREEMENT")
    lines.append("-" * 78)
    lines.append(f"  {'layer':<22s} {'view':<28s} notes")
    layers = art.get("layers") or {}
    for key, label in (
        ("market_regime", "Market regime"),
        ("sector", "Sector"),
        ("technicals", "Stock technicals"),
        ("entry_validator", "Entry validator"),
        ("alpha", "Alpha Discovery"),
        ("posture", "Market Posture"),
        ("options", "Options"),
        ("social", "Social Arb"),
        ("institutional", "13F / institutional"),
    ):
        layer = layers.get(key) or {}
        view = str(layer.get("view") or "—")
        notes = str(layer.get("notes") or "")
        suffix = "" if layer.get("available") else "  (n/a)"
        lines.append(f"  {label:<22s} {view[:28]:<28s} {notes}{suffix}")
    lines.append("")

    lines.append("COMPOSITE SCORES")
    lines.append("-" * 78)
    sc = art.get("scores") or {}
    lines.append(f"  bullish_score        : {sc.get('bullish_score','—')}")
    lines.append(f"  bearish_score        : {sc.get('bearish_score','—')}")
    lines.append(f"  entry_quality_score  : {sc.get('entry_quality_score','—')}")
    lines.append(f"  risk_score           : {sc.get('risk_score','—')}")
    lines.append(f"  composite_score      : {sc.get('composite','—')} (range -1..+1)")
    lines.append(f"  confidence           : {art.get('confidence','—')}")
    lines.append("")

    if art.get("hard_caps_fired"):
        lines.append("HARD CAPS APPLIED")
        lines.append("-" * 78)
        for c in art.get("hard_caps_fired") or []:
            lines.append(f"  ! {c}")
        lines.append("")

    lines.append("CONCLUSION")
    lines.append("-" * 78)
    lines.append(art.get("conclusion") or "—")
    lines.append("")

    lines.append("INVALIDATION")
    lines.append("-" * 78)
    for inv in art.get("invalidation") or []:
        lines.append(f"  - {inv}")
    lines.append("")

    lines.append("NEXT MANUAL CHECKS")
    lines.append("-" * 78)
    for chk in art.get("next_manual_checks") or []:
        lines.append(f"  - {chk}")
    lines.append("")

    lines.append("TECHNICALS RAW")
    lines.append("-" * 78)
    tr = art.get("technicals_raw") or {}
    if tr.get("available"):
        lines.append(f"  bars                  : {tr.get('bars')}")
        lines.append(f"  last close            : {_f2(tr.get('last'))}")
        lines.append(f"  ema20 / ema50 / ma200 : {_f2(tr.get('ema20'))} / {_f2(tr.get('ema50'))} / {_f2(tr.get('ma200'))}")
        lines.append(f"  return 5d / 10d / 20d : {_pct(tr.get('return_5d_pct'))} / "
                     f"{_pct(tr.get('return_10d_pct'))} / {_pct(tr.get('return_20d_pct'))}")
        lines.append(f"  rs vs SPY 5/10/20d    : {_pct(tr.get('rs_vs_spy_5d_pct'))} / "
                     f"{_pct(tr.get('rs_vs_spy_10d_pct'))} / {_pct(tr.get('rs_vs_spy_20d_pct'))}")
        lines.append(f"  pct from 60d high/low : {_pct(tr.get('pct_from_60d_high'))} / "
                     f"{_pct(tr.get('pct_from_60d_low'))}")
        lines.append(f"  ATR%(14)              : {_f2(tr.get('atr_pct_14'),'%')}")
        lines.append(f"  vol ratio 5/20        : {_f2(tr.get('volume_ratio_5_vs_20'))}")
        lines.append(f"  state                 : {tr.get('state')}")
    else:
        lines.append(f"  unavailable: {tr.get('reason','no price history')}")
    lines.append("")

    lines.append("WEIGHTS (transparent composite)")
    lines.append("-" * 78)
    for k, v in (art.get("weights") or {}).items():
        lines.append(f"  {k:<26s} {v:.2f}")
    lines.append("")

    lines.append("DATA QUALITY NOTES")
    lines.append("-" * 78)
    notes = art.get("data_quality_notes") or []
    if not notes:
        lines.append("  all layers available")
    else:
        for n in notes:
            lines.append(f"  - {n}")
    lines.append("")

    lines.append("VALIDATION PLAN (Phase 4 — not built)")
    lines.append("-" * 78)
    for v in art.get("validation_plan") or []:
        lines.append(f"  - {v}")
    lines.append("")

    lines.append("GUARDRAILS")
    lines.append("-" * 78)
    for g in art.get("guardrails") or []:
        lines.append(f"  - {g}")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ── Top-level runner ───────────────────────────────────────────────────────


def run(args: argparse.Namespace) -> int:
    sym = (args.ticker or "").strip().upper()
    if not sym:
        print("stock_lens: --ticker is required")
        return 2

    offline = bool(args.offline or args.cache_only)
    no_fmp = bool(args.no_fmp)

    logger.info("stock lens for %s (offline=%s, no_fmp=%s, refresh=%s)",
                sym, offline, no_fmp, bool(args.refresh))

    stock_frame = _load_ticker_frame(sym, offline=offline, no_fmp=no_fmp)
    spy_frame = _load_spy_frame(offline=offline, no_fmp=no_fmp)

    market_forecast = _load_or_recompute_market_forecast(args)
    profile = _load_sector_profile(sym, offline=offline)
    sector_etf = map_sector_to_etf(
        (profile or {}).get("sector"), (profile or {}).get("industry")
    )

    sector_rotation = (market_forecast or {}).get("sector_rotation")

    # Daily Entry Validator: needs >= 80 bars; the validator handles the
    # short-history case itself by returning a Watch Only state.
    entry_validation = None
    if stock_frame is not None and not stock_frame.empty:
        try:
            from core.stock_research_lens import _ohlcv_records as _to_records
            entry_validation = validate_daily_entry(_to_records(stock_frame))
        except Exception as exc:
            logger.info("entry validator failed for %s: %s", sym, exc)
            entry_validation = None

    alpha_artifacts = _load_alpha_artifacts()
    posture = _load_market_posture()
    social = _load_social_arb()
    last_price = None
    try:
        if stock_frame is not None and "close" in stock_frame.columns and not stock_frame.empty:
            last_price = float(stock_frame["close"].iloc[-1])
    except Exception:
        last_price = None
    options = _load_options_layer(sym, offline=offline, last_price=last_price)

    art = build_stock_lens(
        ticker=sym,
        stock_frame=stock_frame,
        spy_frame=spy_frame,
        market_forecast=market_forecast,
        sector_etf=sector_etf,
        sector_rotation=sector_rotation,
        company_profile=profile,
        entry_validation=entry_validation,
        alpha_artifacts=alpha_artifacts,
        posture_output=posture,
        social_artifact=social,
        options_layer=options,
        institutional_layer=None,   # not wired in V1
    )
    art["built_at"] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    art["mode"] = "stock_lens"
    art["horizon_hint_days"] = int(args.horizon)
    art["sources_used"] = {
        "stock_bars": "cache → Alpaca → FMP → yfinance" if not offline else "cache only (offline)",
        "spy_bars":   "cache → Alpaca → FMP → yfinance" if not offline else "cache only (offline)",
        "market_forecast": "cache/research/regime_forecast_latest.json"
                           + (" (refreshed)" if args.refresh else " (cached)"),
        "sector_profile": "FMP get_company_profile" if not offline else "skipped (offline)",
        "entry_validator": "core.daily_entry_validator (cache-only inputs)",
        "alpha_discovery": "cache/research/alpha_discovery_*",
        "market_posture": "rebuilt via core.research_assist_bte from universe snapshot",
        "social_arb": "cache/research/social_arb_latest.json",
        "options": (
            "Tradier nearest-expiry chain (call/put OI + volume tilt)"
            if options is not None
            else "Tradier unavailable / unconfigured"
        ),
        "institutional_13f": "not wired in V1",
    }

    json_path = RESEARCH_DIR / f"stock_lens_{sym}_latest.json"
    text_path = cfg.LOG_DIR / f"stock_lens_{sym}_latest.txt"
    art["artifacts"] = {"json": str(json_path), "text": str(text_path)}

    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    cfg.LOG_DIR.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(art, indent=2, default=str), encoding="utf-8")
    text_path.write_text(_render_text(art), encoding="utf-8")

    # Phase 5 forward-tracking ledger for stock lens calls.  Append-only;
    # failures here must not break the lens run.
    try:
        from core.forecast_forward_tracker import append_stock_lens_snapshot
        append_stock_lens_snapshot(art)
    except Exception as exc:  # pragma: no cover
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "stock lens forward-tracking append failed: %s", exc
        )

    print(
        f"stock_lens {sym}: {art.get('label','—')}  conf {art.get('confidence','—')}  "
        f"composite {art.get('scores',{}).get('composite','—')}  "
        f"→ {json_path}"
    )
    if getattr(args, "print_json", False):
        print(json.dumps(art, indent=2, default=str))
    return 0

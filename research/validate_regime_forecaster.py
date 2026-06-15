#!/usr/bin/env python3
"""
research/validate_regime_forecaster.py — Phase 2 walk-forward validation.

Honest historical validation of the Market & Sector Regime Forecaster V1.
Asks five questions:

  1. Did predicted bullish regimes lead to better forward SPY/QQQ returns?
  2. Did predicted risk-off / stress regimes show weaker forward returns or
     higher realized volatility?
  3. Did predicted sector leaders outperform predicted laggards?
  4. Are forecast probabilities even roughly calibrated?
  5. Is the forecaster useful enough to keep, recalibrate, or downgrade?

The validator does NOT touch live sleeves, paper evidence, governance,
execution, Alpha Discovery, Market Posture, the Daily Entry Validator, the
Social Arb radar, or the dashboard.  It only consumes price history and
re-uses ``core.regime_forecaster.build_forecast`` against per-date truncated
frames.

Walk-forward rules:
  - For each evaluation date t, only bars with date <= t are visible.
  - Forward outcomes (t+5, t+10) come from the full historical frame but are
    used only to score the forecast generated at t.
  - VIX history is truncated to <= t before being passed to the forecaster.
  - No future sector ranks, no future VIX, no future returns leak into the
    forecast computation.

Default mode is cache-only.  Provider backfill (yfinance) is opt-in with
``--backfill`` and writes to a separate validation-only parquet store at
``cache/research/regime_validation_prices/`` so the production
``cache/prices/`` directory used by the live scanner is never touched.

Usage:
  SNIPER_ENV_PATH=/home/gem/secure/trading.env \
    .venv/bin/python research/validate_regime_forecaster.py \
      --start 2020-01-01 --end 2025-12-31 --backfill

Smoke (cache-only, fast):
  GEM_TRADER_SKIP_DOTENV=true \
    .venv/bin/python research/validate_regime_forecaster.py \
      --start 2025-04-01 --end 2026-04-24 --max-rows 30
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import os
import sys
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta, timezone, date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None

if load_dotenv is not None and os.getenv("GEM_TRADER_SKIP_DOTENV", "").lower() not in {"1", "true", "yes"}:
    env_path = os.getenv("SNIPER_ENV_PATH", "").strip()
    if env_path:
        load_dotenv(env_path, override=False)
    load_dotenv(ROOT / ".env", override=False)

os.environ.setdefault("ALPACA_API_KEY", "offline")
os.environ.setdefault("ALPACA_SECRET_KEY", "offline")
os.environ.setdefault("FMP_API_KEY", "offline")
os.environ.setdefault("FMP_BASE_URL", "https://financialmodelingprep.com/stable")
os.environ.setdefault("ALPACA_PAPER", "true")
os.environ.setdefault("PAPER_TRADING", "true")
os.environ.setdefault("DB_PATH", str(ROOT / "db" / "trading.db"))
os.environ.setdefault("CACHE_DIR", str(ROOT / "cache"))
os.environ.setdefault("LOG_DIR", str(ROOT / "logs"))


import pandas as pd

import core.config as cfg
from core.regime_forecaster import (
    MARKET_ETFS,
    SECTOR_ETFS,
    RISK_PROXIES,
    SECTOR_NAMES,
    REGIME_CLASSES,
    VERSION as FORECASTER_VERSION,
    build_forecast,
)


VERSION = "REGIME_FORECASTER_VALIDATOR_V1"

logger = logging.getLogger("validate_regime_forecaster")
logging.basicConfig(
    level=os.getenv("REGIME_FORECAST_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


RESEARCH_DIR = cfg.CACHE_DIR / "research"
PRODUCTION_PRICE_DIR = cfg.CACHE_DIR / "prices"
VALIDATION_PRICE_DIR = RESEARCH_DIR / "regime_validation_prices"
VALIDATION_VIX_PATH = RESEARCH_DIR / "regime_validation_vix.parquet"
ARTIFACT_JSON = RESEARCH_DIR / "regime_forecast_validation_latest.json"
ARTIFACT_TEXT = cfg.LOG_DIR / "regime_forecast_validation_latest.txt"
ARTIFACT_CSV = RESEARCH_DIR / "regime_forecast_walkforward_rows.csv"

ALL_SYMBOLS: Tuple[str, ...] = tuple(sorted(set(MARKET_ETFS) + tuple(SECTOR_ETFS) + tuple(RISK_PROXIES) + ("LQD",))) \
    if False else tuple(sorted(set(list(MARKET_ETFS) + list(SECTOR_ETFS) + list(RISK_PROXIES) + ["LQD"])))


# Realized-label thresholds (intentionally simple and explicit).
LABEL_THRESHOLDS = {
    # bullish: SPY 5d return >= +1.0% OR SPY 10d return >= +1.5%
    "bullish_5d_pct": 1.0,
    "bullish_10d_pct": 1.5,
    # risk-off: SPY 5d return <= -1.0% OR realized vol expansion >= +30% (relative)
    "riskoff_5d_pct": -1.0,
    "riskoff_realized_vol_expansion_rel_pct": 30.0,
    # stress: SPY 5d drawdown <= -3.0% OR VIX 5d expansion >= +30% (relative)
    "stress_5d_drawdown_pct": -3.0,
    "stress_vix_expansion_rel_pct": 30.0,
    # chop: |SPY 5d return| <= 1.0% AND realized vol change within +/- 20%
    "chop_5d_abs_pct": 1.0,
    "chop_vol_change_band_rel_pct": 20.0,
    # high-confidence threshold for false-confidence audit
    "high_confidence_label": "high",
}

# Coarse-class mapping for the confusion matrix.
COARSE_CLASS_OF_REGIME = {
    "Bull Continuation": "bullish",
    "Bull Pullback / Buy-the-Dip": "bullish",
    "Chop / Range": "chop",
    "Risk-Off": "risk-off / stress",
    "Volatility Expansion / Stress": "risk-off / stress",
    "Bear Rally / Unstable Rebound": "chop",
}


# ── Data loading ────────────────────────────────────────────────────────────


def _read_parquet(path: Path) -> Optional[pd.DataFrame]:
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path)
        if df is None or df.empty:
            return None
        if "close" not in df.columns and "Close" in df.columns:
            df = df.rename(columns={"Close": "close"})
        df.index = pd.to_datetime(df.index)
        df.sort_index(inplace=True)
        return df
    except Exception as exc:
        logger.warning("parquet read failed for %s: %s", path, exc)
        return None


def _write_parquet(path: Path, df: pd.DataFrame) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        cols = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
        out = df[cols] if cols else df
        out.to_parquet(path, compression="snappy")
    except Exception as exc:
        logger.warning("parquet write failed for %s: %s", path, exc)


def _yf_download(tickers: Sequence[str], start: date, end: date) -> Dict[str, pd.DataFrame]:
    """Single batched yfinance call.  Quiet on failure."""
    out: Dict[str, pd.DataFrame] = {}
    if not tickers:
        return out
    try:
        import yfinance as yf
    except Exception as exc:
        logger.warning("yfinance unavailable: %s", exc)
        return out
    try:
        data = yf.download(
            tickers=" ".join(tickers),
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),
            interval="1d",
            auto_adjust=False,
            progress=False,
            group_by="ticker",
            threads=False,
        )
    except Exception as exc:
        logger.warning("yfinance download failed: %s", exc)
        return out
    if data is None or len(data) == 0:
        return out

    def _norm(df: pd.DataFrame) -> Optional[pd.DataFrame]:
        if df is None or df.empty:
            return None
        df = df.rename(columns={c: c.lower() for c in df.columns})
        if "close" not in df.columns:
            return None
        df = df.dropna(subset=["close"])
        if df.empty:
            return None
        df.index = pd.to_datetime(df.index)
        keep = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
        return df[keep] if keep else df

    if hasattr(data, "columns") and isinstance(data.columns, pd.MultiIndex):
        for sym in tickers:
            if sym not in data.columns.get_level_values(0):
                continue
            sub = _norm(data[sym].copy())
            if sub is not None:
                out[sym] = sub
    else:
        if len(tickers) == 1:
            sub = _norm(data.copy())
            if sub is not None:
                out[tickers[0]] = sub
    return out


def _load_symbol_history(
    symbol: str,
    start: date,
    end: date,
    *,
    cache_only: bool,
    backfill: bool,
) -> Optional[pd.DataFrame]:
    """
    Load deep history for a symbol.  Order:
      1) validation-only parquet (cache/research/regime_validation_prices)
      2) production parquet (cache/prices) — read-only
      3) yfinance backfill, if --backfill and not --cache-only

    Backfilled frames are written ONLY to the validation-only directory; the
    production live cache is never modified by this script.
    """
    val_path = VALIDATION_PRICE_DIR / f"{symbol}.parquet"
    df_val = _read_parquet(val_path)
    df_prod = _read_parquet(PRODUCTION_PRICE_DIR / f"{symbol}.parquet")

    candidates = [df for df in (df_val, df_prod) if df is not None]
    df: Optional[pd.DataFrame] = None
    if candidates:
        df = pd.concat(candidates).sort_index()
        df = df[~df.index.duplicated(keep="last")]

    needs_backfill = (
        df is None
        or df.index.min() > pd.Timestamp(start)
        or df.index.max() < pd.Timestamp(end)
    )

    if needs_backfill and backfill and not cache_only:
        fetched = _yf_download([symbol], start, end)
        sub = fetched.get(symbol)
        if sub is not None and not sub.empty:
            if df is None:
                df = sub
            else:
                df = pd.concat([df, sub]).sort_index()
                df = df[~df.index.duplicated(keep="last")]
            _write_parquet(val_path, df)

    if df is None:
        return None
    return df.loc[(df.index >= pd.Timestamp(start) - pd.Timedelta(days=350)) & (df.index <= pd.Timestamp(end))]


def _load_vix_history(
    start: date,
    end: date,
    *,
    cache_only: bool,
    backfill: bool,
) -> Optional[pd.DataFrame]:
    """Load ^VIX daily closes for the validation window (yfinance backfill)."""
    df = _read_parquet(VALIDATION_VIX_PATH)
    needs_backfill = (
        df is None
        or df.index.min() > pd.Timestamp(start)
        or df.index.max() < pd.Timestamp(end) - pd.Timedelta(days=2)
    )
    if needs_backfill and backfill and not cache_only:
        try:
            import yfinance as yf
            data = yf.download(
                tickers="^VIX",
                start=start.isoformat(),
                end=(end + timedelta(days=1)).isoformat(),
                interval="1d",
                auto_adjust=False,
                progress=False,
                threads=False,
            )
            if data is not None and len(data) > 0:
                data = data.rename(columns={c: c.lower() for c in data.columns}) \
                    if not isinstance(data.columns, pd.MultiIndex) else data
                if isinstance(data.columns, pd.MultiIndex):
                    # Newer yfinance returns MultiIndex even for single ticker
                    data.columns = [str(c[0]).lower() for c in data.columns]
                if "close" in data.columns:
                    sub = data[["close"]].dropna()
                    sub.index = pd.to_datetime(sub.index)
                    if df is None:
                        df = sub
                    else:
                        df = pd.concat([df, sub]).sort_index()
                        df = df[~df.index.duplicated(keep="last")]
                    _write_parquet(VALIDATION_VIX_PATH, df)
        except Exception as exc:
            logger.warning("VIX backfill failed: %s", exc)
    if df is None:
        return None
    return df.loc[(df.index >= pd.Timestamp(start) - pd.Timedelta(days=350)) & (df.index <= pd.Timestamp(end))]


# ── Walk-forward engine ─────────────────────────────────────────────────────


def _trading_dates(spy: pd.DataFrame, start: date, end: date) -> List[pd.Timestamp]:
    idx = spy.index
    mask = (idx >= pd.Timestamp(start)) & (idx <= pd.Timestamp(end))
    return list(idx[mask])


def _truncate_frames(frames: Dict[str, pd.DataFrame], t: pd.Timestamp) -> Dict[str, pd.DataFrame]:
    out: Dict[str, pd.DataFrame] = {}
    for sym, df in frames.items():
        if df is None or df.empty:
            continue
        sub = df.loc[df.index <= t]
        if not sub.empty:
            out[sym] = sub
    return out


def _truncate_vix(vix_df: Optional[pd.DataFrame], t: pd.Timestamp) -> Tuple[Optional[float], List[float]]:
    if vix_df is None or vix_df.empty:
        return None, []
    sub = vix_df.loc[vix_df.index <= t, "close"].dropna()
    if sub.empty:
        return None, []
    series = [float(x) for x in sub.tolist()]
    return series[-1], series[-60:]


def _forward_return_pct(series: pd.Series, t: pd.Timestamp, horizon: int) -> Optional[float]:
    """Return the percentage return from t to t+horizon trading days."""
    try:
        idx = series.index.get_loc(t)
    except KeyError:
        return None
    if isinstance(idx, slice):
        return None
    end_idx = idx + horizon
    if end_idx >= len(series):
        return None
    p0 = float(series.iloc[idx])
    p1 = float(series.iloc[end_idx])
    if p0 <= 0:
        return None
    return (p1 / p0 - 1.0) * 100.0


def _forward_drawdown_pct(series: pd.Series, t: pd.Timestamp, horizon: int) -> Optional[float]:
    """Min close / start_close - 1 over (t, t+horizon]."""
    try:
        idx = series.index.get_loc(t)
    except KeyError:
        return None
    if isinstance(idx, slice):
        return None
    end_idx = idx + horizon
    if end_idx >= len(series):
        return None
    p0 = float(series.iloc[idx])
    if p0 <= 0:
        return None
    window = series.iloc[idx + 1: end_idx + 1]
    if window.empty:
        return None
    return (float(window.min()) / p0 - 1.0) * 100.0


def _realized_vol_pct(series: pd.Series, anchor: pd.Timestamp, lookback: int) -> Optional[float]:
    """
    Annualized realized vol from log returns over [anchor-lookback, anchor].
    Used to compare pre-vs-post realized vol around an evaluation date.
    """
    try:
        idx = series.index.get_loc(anchor)
    except KeyError:
        return None
    if isinstance(idx, slice):
        return None
    start = max(0, idx - lookback)
    sub = series.iloc[start: idx + 1].astype(float)
    if len(sub) < 4:
        return None
    rets = []
    prev = None
    for v in sub:
        if prev is not None and prev > 0 and v > 0:
            rets.append(math.log(v / prev))
        prev = v
    if len(rets) < 3:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(252) * 100.0


def _vix_change_rel(vix_df: Optional[pd.DataFrame], t: pd.Timestamp, horizon: int) -> Optional[float]:
    if vix_df is None or vix_df.empty:
        return None
    sub = vix_df["close"].dropna()
    try:
        idx = sub.index.get_loc(t)
    except KeyError:
        # fall back to nearest prior trading date
        idx_arr = sub.index.searchsorted(t, side="right") - 1
        if idx_arr < 0 or idx_arr >= len(sub):
            return None
        idx = int(idx_arr)
    if isinstance(idx, slice):
        return None
    end_idx = idx + horizon
    if end_idx >= len(sub):
        return None
    p0 = float(sub.iloc[idx])
    p1 = float(sub.iloc[end_idx])
    if p0 <= 0:
        return None
    return (p1 / p0 - 1.0) * 100.0


def _coarse_class(regime: str) -> str:
    return COARSE_CLASS_OF_REGIME.get(regime, "chop")


def _realized_coarse_class(
    spy_5d: Optional[float],
    spy_10d: Optional[float],
    spy_dd_5d: Optional[float],
    rv_pre: Optional[float],
    rv_post: Optional[float],
    vix_chg_5d: Optional[float],
) -> str:
    """Map realized forward outcomes to a coarse class for the confusion matrix."""
    th = LABEL_THRESHOLDS

    # Stress wins ties (it's the worst case).
    if spy_dd_5d is not None and spy_dd_5d <= th["stress_5d_drawdown_pct"]:
        return "risk-off / stress"
    if vix_chg_5d is not None and vix_chg_5d >= th["stress_vix_expansion_rel_pct"]:
        return "risk-off / stress"
    if spy_5d is not None and spy_5d <= th["riskoff_5d_pct"]:
        return "risk-off / stress"
    if rv_pre is not None and rv_post is not None and rv_pre > 0:
        rv_change = (rv_post / rv_pre - 1.0) * 100.0
        if rv_change >= th["riskoff_realized_vol_expansion_rel_pct"]:
            return "risk-off / stress"

    bull_5d = spy_5d is not None and spy_5d >= th["bullish_5d_pct"]
    bull_10d = spy_10d is not None and spy_10d >= th["bullish_10d_pct"]
    if bull_5d or bull_10d:
        return "bullish"

    return "chop"


# ── Per-date evaluation ────────────────────────────────────────────────────


@dataclass
class WalkForwardRow:
    date: str
    current_regime: str
    confidence: str
    bias_5d: str
    p_bull_continuation: float
    p_bull_pullback: float
    p_chop: float
    p_riskoff: float
    p_vol_expansion: float
    p_bear_rally: float
    coarse_predicted: str
    spy_fwd_5d_pct: Optional[float]
    spy_fwd_10d_pct: Optional[float]
    qqq_fwd_5d_pct: Optional[float]
    qqq_fwd_10d_pct: Optional[float]
    spy_fwd_dd_5d_pct: Optional[float]
    rv_pre_20d: Optional[float]
    rv_post_10d: Optional[float]
    vix_chg_5d_rel_pct: Optional[float]
    vix_chg_10d_rel_pct: Optional[float]
    coarse_realized: str
    top_sectors: str  # "XLK,XLY,XLI"
    bottom_sectors: str
    top_basket_fwd_5d_pct: Optional[float]
    bottom_basket_fwd_5d_pct: Optional[float]
    top_minus_bottom_5d_pct: Optional[float]
    top_basket_fwd_10d_pct: Optional[float]
    bottom_basket_fwd_10d_pct: Optional[float]
    top_minus_bottom_10d_pct: Optional[float]


def _basket_fwd(symbols: Sequence[str], frames: Dict[str, pd.DataFrame], t: pd.Timestamp, horizon: int) -> Optional[float]:
    if not symbols:
        return None
    parts: List[float] = []
    for s in symbols:
        df = frames.get(s)
        if df is None or "close" not in df.columns:
            continue
        r = _forward_return_pct(df["close"], t, horizon)
        if r is not None:
            parts.append(r)
    if not parts:
        return None
    return sum(parts) / len(parts)


def evaluate_one(
    t: pd.Timestamp,
    full_frames: Dict[str, pd.DataFrame],
    full_vix: Optional[pd.DataFrame],
    horizons: Tuple[int, int],
) -> Optional[WalkForwardRow]:
    h5, h10 = horizons
    # 1) Walk-forward forecast: use only data <= t.
    truncated = _truncate_frames(full_frames, t)
    spy_df = truncated.get("SPY")
    if spy_df is None or len(spy_df) < 60:
        return None
    vix_latest, vix_history = _truncate_vix(full_vix, t)
    forecast = build_forecast(
        frames=truncated,
        vix=vix_latest,
        vix_history=vix_history,
        universe_snapshot=None,
    )
    head = forecast["headline"]
    probs = {p["regime"]: float(p["probability"]) for p in forecast["regime_probabilities"]}
    coarse_pred = _coarse_class(head["current_regime"])
    sector_rows = (forecast["sector_rotation"]["rows"] or [])
    available_sectors = [r for r in sector_rows if r.get("available")]
    top_syms = [r["sector"] for r in available_sectors[:3]]
    bot_syms = [r["sector"] for r in available_sectors[-3:]] if len(available_sectors) >= 3 else []

    # 2) Forward outcomes computed from FULL frames (post-t bars).
    full_spy = full_frames.get("SPY")
    full_qqq = full_frames.get("QQQ")
    spy_close = full_spy["close"] if full_spy is not None and "close" in full_spy.columns else None
    qqq_close = full_qqq["close"] if full_qqq is not None and "close" in full_qqq.columns else None

    spy_5d = _forward_return_pct(spy_close, t, h5) if spy_close is not None else None
    spy_10d = _forward_return_pct(spy_close, t, h10) if spy_close is not None else None
    qqq_5d = _forward_return_pct(qqq_close, t, h5) if qqq_close is not None else None
    qqq_10d = _forward_return_pct(qqq_close, t, h10) if qqq_close is not None else None
    spy_dd5 = _forward_drawdown_pct(spy_close, t, h5) if spy_close is not None else None
    rv_pre = _realized_vol_pct(spy_close, t, 20) if spy_close is not None else None
    rv_post = _realized_vol_pct(spy_close, t + pd.Timedelta(days=int(h10 * 1.6)), h10) if spy_close is not None else None
    vix_chg_5d = _vix_change_rel(full_vix, t, h5)
    vix_chg_10d = _vix_change_rel(full_vix, t, h10)
    coarse_realized = _realized_coarse_class(spy_5d, spy_10d, spy_dd5, rv_pre, rv_post, vix_chg_5d)

    spy_fwd_5d = spy_5d
    top5 = _basket_fwd(top_syms, full_frames, t, h5)
    bot5 = _basket_fwd(bot_syms, full_frames, t, h5)
    top10 = _basket_fwd(top_syms, full_frames, t, h10)
    bot10 = _basket_fwd(bot_syms, full_frames, t, h10)
    spread5 = (top5 - bot5) if (top5 is not None and bot5 is not None) else None
    spread10 = (top10 - bot10) if (top10 is not None and bot10 is not None) else None

    return WalkForwardRow(
        date=t.strftime("%Y-%m-%d"),
        current_regime=head["current_regime"],
        confidence=head["confidence"],
        bias_5d=head["bias_5d"],
        p_bull_continuation=probs.get("Bull Continuation", 0.0),
        p_bull_pullback=probs.get("Bull Pullback / Buy-the-Dip", 0.0),
        p_chop=probs.get("Chop / Range", 0.0),
        p_riskoff=probs.get("Risk-Off", 0.0),
        p_vol_expansion=probs.get("Volatility Expansion / Stress", 0.0),
        p_bear_rally=probs.get("Bear Rally / Unstable Rebound", 0.0),
        coarse_predicted=coarse_pred,
        spy_fwd_5d_pct=spy_fwd_5d,
        spy_fwd_10d_pct=spy_10d,
        qqq_fwd_5d_pct=qqq_5d,
        qqq_fwd_10d_pct=qqq_10d,
        spy_fwd_dd_5d_pct=spy_dd5,
        rv_pre_20d=rv_pre,
        rv_post_10d=rv_post,
        vix_chg_5d_rel_pct=vix_chg_5d,
        vix_chg_10d_rel_pct=vix_chg_10d,
        coarse_realized=coarse_realized,
        top_sectors=",".join(top_syms),
        bottom_sectors=",".join(bot_syms),
        top_basket_fwd_5d_pct=top5,
        bottom_basket_fwd_5d_pct=bot5,
        top_minus_bottom_5d_pct=spread5,
        top_basket_fwd_10d_pct=top10,
        bottom_basket_fwd_10d_pct=bot10,
        top_minus_bottom_10d_pct=spread10,
    )


# ── Aggregation ─────────────────────────────────────────────────────────────


def _mean(values: Iterable[float]) -> Optional[float]:
    vs = [float(v) for v in values if v is not None]
    if not vs:
        return None
    return sum(vs) / len(vs)


def _winrate(values: Iterable[float], threshold: float = 0.0) -> Optional[float]:
    vs = [float(v) for v in values if v is not None]
    if not vs:
        return None
    wins = sum(1 for v in vs if v > threshold)
    return wins / len(vs)


def _stdev(values: Iterable[float]) -> Optional[float]:
    vs = [float(v) for v in values if v is not None]
    if len(vs) < 2:
        return None
    m = sum(vs) / len(vs)
    return math.sqrt(sum((v - m) ** 2 for v in vs) / (len(vs) - 1))


def regime_return_table(rows: List[WalkForwardRow]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for regime in REGIME_CLASSES:
        sub = [r for r in rows if r.current_regime == regime]
        n = len(sub)
        if n == 0:
            out.append({"regime": regime, "n": 0})
            continue
        out.append({
            "regime": regime,
            "n": n,
            "avg_spy_5d_pct": _mean(r.spy_fwd_5d_pct for r in sub),
            "avg_spy_10d_pct": _mean(r.spy_fwd_10d_pct for r in sub),
            "win_rate_spy_5d": _winrate(r.spy_fwd_5d_pct for r in sub),
            "win_rate_spy_10d": _winrate(r.spy_fwd_10d_pct for r in sub),
            "avg_spy_5d_drawdown_pct": _mean(r.spy_fwd_dd_5d_pct for r in sub),
            "avg_realized_vol_post_10d": _mean(r.rv_post_10d for r in sub),
            "avg_vix_chg_5d_rel_pct": _mean(r.vix_chg_5d_rel_pct for r in sub),
        })
    return out


def sector_validation(rows: List[WalkForwardRow]) -> Dict[str, Any]:
    spreads_5d = [r.top_minus_bottom_5d_pct for r in rows if r.top_minus_bottom_5d_pct is not None]
    spreads_10d = [r.top_minus_bottom_10d_pct for r in rows if r.top_minus_bottom_10d_pct is not None]
    return {
        "n_with_spread": len(spreads_5d),
        "avg_top_minus_bottom_5d_pct": _mean(spreads_5d),
        "avg_top_minus_bottom_10d_pct": _mean(spreads_10d),
        "win_rate_top_gt_bottom_5d": _winrate(spreads_5d, threshold=0.0),
        "win_rate_top_gt_bottom_10d": _winrate(spreads_10d, threshold=0.0),
        "stdev_top_minus_bottom_5d": _stdev(spreads_5d),
        "stdev_top_minus_bottom_10d": _stdev(spreads_10d),
    }


def confusion_matrix(rows: List[WalkForwardRow]) -> Dict[str, Dict[str, int]]:
    classes = ["bullish", "chop", "risk-off / stress"]
    mat = {p: {a: 0 for a in classes} for p in classes}
    for r in rows:
        if r.coarse_predicted in mat and r.coarse_realized in mat[r.coarse_predicted]:
            mat[r.coarse_predicted][r.coarse_realized] += 1
    return mat


def _calibration_buckets(values: Iterable[Tuple[float, int]]) -> List[Dict[str, Any]]:
    """Given (p, outcome) pairs, return per-bucket frequency."""
    buckets = [
        ("0-30%", 0.0, 0.30),
        ("30-50%", 0.30, 0.50),
        ("50-70%", 0.50, 0.70),
        ("70%+", 0.70, 1.01),
    ]
    out: List[Dict[str, Any]] = []
    pairs = list(values)
    for label, lo, hi in buckets:
        sub = [(p, o) for p, o in pairs if lo <= p < hi]
        n = len(sub)
        out.append({
            "bucket": label,
            "n": n,
            "avg_predicted_p": (sum(p for p, _ in sub) / n) if n else None,
            "actual_frequency": (sum(o for _, o in sub) / n) if n else None,
        })
    return out


def calibration(rows: List[WalkForwardRow]) -> Dict[str, Any]:
    bull_pairs: List[Tuple[float, int]] = []
    riskoff_pairs: List[Tuple[float, int]] = []
    th = LABEL_THRESHOLDS
    for r in rows:
        if r.spy_fwd_5d_pct is None and r.spy_fwd_10d_pct is None:
            continue
        bull_p = float(r.p_bull_continuation + r.p_bull_pullback)
        risk_p = float(r.p_riskoff + r.p_vol_expansion)
        bull_realized = 1 if (
            (r.spy_fwd_5d_pct is not None and r.spy_fwd_5d_pct >= th["bullish_5d_pct"])
            or (r.spy_fwd_10d_pct is not None and r.spy_fwd_10d_pct >= th["bullish_10d_pct"])
        ) else 0
        risk_realized = 1 if (
            (r.spy_fwd_5d_pct is not None and r.spy_fwd_5d_pct <= th["riskoff_5d_pct"])
            or (r.spy_fwd_dd_5d_pct is not None and r.spy_fwd_dd_5d_pct <= th["stress_5d_drawdown_pct"])
            or (r.vix_chg_5d_rel_pct is not None and r.vix_chg_5d_rel_pct >= th["stress_vix_expansion_rel_pct"])
        ) else 0
        bull_pairs.append((bull_p, bull_realized))
        riskoff_pairs.append((risk_p, risk_realized))

    def _brier(pairs: List[Tuple[float, int]]) -> Optional[float]:
        if not pairs:
            return None
        return sum((p - o) ** 2 for p, o in pairs) / len(pairs)

    return {
        "n_pairs": len(bull_pairs),
        "brier_bullish": _brier(bull_pairs),
        "brier_riskoff": _brier(riskoff_pairs),
        "base_rate_bullish": (sum(o for _, o in bull_pairs) / len(bull_pairs)) if bull_pairs else None,
        "base_rate_riskoff": (sum(o for _, o in riskoff_pairs) / len(riskoff_pairs)) if riskoff_pairs else None,
        "avg_p_bullish": (sum(p for p, _ in bull_pairs) / len(bull_pairs)) if bull_pairs else None,
        "avg_p_riskoff": (sum(p for p, _ in riskoff_pairs) / len(riskoff_pairs)) if riskoff_pairs else None,
        "buckets_bullish": _calibration_buckets(bull_pairs),
        "buckets_riskoff": _calibration_buckets(riskoff_pairs),
    }


def false_confidence_examples(rows: List[WalkForwardRow], limit: int = 8) -> List[Dict[str, Any]]:
    """High-confidence cases where the predicted coarse class missed reality."""
    th_label = LABEL_THRESHOLDS["high_confidence_label"]
    misses = []
    for r in rows:
        if r.confidence != th_label:
            continue
        if r.coarse_predicted == r.coarse_realized:
            continue
        # Score the miss by how far the realized SPY return ran against the
        # predicted bias (used only for sorting).
        adverse = 0.0
        if r.coarse_predicted == "bullish" and r.spy_fwd_5d_pct is not None:
            adverse = -float(r.spy_fwd_5d_pct)  # bigger = worse miss
        elif r.coarse_predicted in ("risk-off / stress",) and r.spy_fwd_5d_pct is not None:
            adverse = float(r.spy_fwd_5d_pct)
        misses.append((adverse, r))
    misses.sort(key=lambda x: x[0], reverse=True)
    out: List[Dict[str, Any]] = []
    for _, r in misses[:limit]:
        out.append({
            "date": r.date,
            "predicted_regime": r.current_regime,
            "predicted_coarse": r.coarse_predicted,
            "confidence": r.confidence,
            "realized_coarse": r.coarse_realized,
            "spy_5d_pct": r.spy_fwd_5d_pct,
            "spy_10d_pct": r.spy_fwd_10d_pct,
            "spy_dd_5d_pct": r.spy_fwd_dd_5d_pct,
            "vix_chg_5d_rel_pct": r.vix_chg_5d_rel_pct,
        })
    return out


def derive_verdict(
    n_rows: int,
    regime_table: List[Dict[str, Any]],
    sector: Dict[str, Any],
    cal: Dict[str, Any],
    confusion: Dict[str, Dict[str, int]],
) -> Dict[str, Any]:
    """
    Honest, mechanical verdict.  No model tuning happens here — we only
    summarize what the metrics show.
    """
    bullets: List[str] = []
    flags: List[str] = []

    # Signal check #1: do bullish-coarse predictions beat risk-off-coarse
    # predictions on average forward SPY return?
    avg_by_coarse: Dict[str, float] = {}
    counts: Dict[str, int] = {}
    bullish = [row for row in regime_table if COARSE_CLASS_OF_REGIME.get(row["regime"]) == "bullish" and row.get("n")]
    riskoff = [row for row in regime_table if COARSE_CLASS_OF_REGIME.get(row["regime"]) == "risk-off / stress" and row.get("n")]

    def _weighted_avg(rs: List[Dict[str, Any]], key: str) -> Optional[float]:
        items = [(r["n"], r.get(key)) for r in rs if r.get(key) is not None and r.get("n")]
        total_n = sum(n for n, _ in items)
        if total_n == 0:
            return None
        return sum(n * v for n, v in items) / total_n

    bull_5d = _weighted_avg(bullish, "avg_spy_5d_pct")
    risk_5d = _weighted_avg(riskoff, "avg_spy_5d_pct")
    spread_5d_signal: Optional[float] = None
    if bull_5d is not None and risk_5d is not None:
        spread_5d_signal = bull_5d - risk_5d
        bullets.append(
            f"5d SPY return spread (bullish predictions − risk-off/stress predictions): "
            f"{spread_5d_signal:+.2f}pp  (bull avg {bull_5d:+.2f}%, risk-off avg {risk_5d:+.2f}%)"
        )
    else:
        bullets.append("5d SPY return spread: insufficient class coverage to compare.")

    # Signal check #2: top-vs-bottom sector basket
    spread_5d = sector.get("avg_top_minus_bottom_5d_pct")
    wr_5d = sector.get("win_rate_top_gt_bottom_5d")
    if spread_5d is not None:
        bullets.append(
            f"Top-3 vs bottom-3 sector basket forward spread: "
            f"5d {spread_5d:+.2f}pp (win rate {wr_5d*100:.0f}% over n={sector.get('n_with_spread')})"
        )
    else:
        bullets.append("Sector spread: insufficient sector coverage.")

    # Signal check #3: calibration vs base rate
    brier_bull = cal.get("brier_bullish")
    base_bull = cal.get("base_rate_bullish")
    avg_p_bull = cal.get("avg_p_bullish")
    if brier_bull is not None and base_bull is not None:
        baseline = base_bull * (1 - base_bull)  # Brier of always predicting base rate
        bullets.append(
            f"Brier (bullish): {brier_bull:.3f}; baseline {baseline:.3f}; "
            f"avg predicted bullish p={avg_p_bull:.2f}, base rate {base_bull:.2f}"
        )
        if brier_bull > baseline + 0.01:
            flags.append("Bullish probabilities are worse than the always-predict-base-rate baseline.")
        if avg_p_bull is not None and avg_p_bull > base_bull + 0.10:
            flags.append("Bullish probabilities run materially above the realized base rate (overconfident bullish).")

    brier_risk = cal.get("brier_riskoff")
    base_risk = cal.get("base_rate_riskoff")
    avg_p_risk = cal.get("avg_p_riskoff")
    if brier_risk is not None and base_risk is not None:
        baseline = base_risk * (1 - base_risk)
        bullets.append(
            f"Brier (risk-off): {brier_risk:.3f}; baseline {baseline:.3f}; "
            f"avg predicted risk-off p={avg_p_risk:.2f}, base rate {base_risk:.2f}"
        )
        if brier_risk > baseline + 0.01:
            flags.append("Risk-off probabilities are worse than the base-rate baseline.")

    # Verdict logic — deliberately conservative.
    verdict = "descriptive only for now"
    rationale: List[str] = []
    has_signal_market = (spread_5d_signal is not None and spread_5d_signal >= 0.30)
    has_signal_sector = (spread_5d is not None and spread_5d >= 0.20 and (wr_5d or 0) >= 0.52)
    calibration_ok = (
        (brier_bull is None or base_bull is None or brier_bull <= base_bull * (1 - base_bull) + 0.005)
        and (brier_risk is None or base_risk is None or brier_risk <= base_risk * (1 - base_risk) + 0.005)
    )

    if n_rows < 60:
        verdict = "insufficient sample (need more history before judging)"
        rationale.append(f"only {n_rows} walk-forward dates available; results are indicative at best")
    elif has_signal_market and has_signal_sector and calibration_ok:
        verdict = "useful as strategic lens"
        rationale.append("market-direction spread positive AND sector spread positive AND calibration not worse than base rate")
    elif has_signal_market or has_signal_sector:
        verdict = "needs calibration"
        rationale.append("partial signal: at least one of market-direction spread or sector spread is positive, but the other is weak or calibration is off")
    else:
        verdict = "descriptive only for now"
        rationale.append("no consistent forward edge in either market direction or sector ranking")

    return {
        "verdict": verdict,
        "rationale": rationale,
        "bullets": bullets,
        "flags": flags,
    }


# ── Rendering ───────────────────────────────────────────────────────────────


def _render_text(artifact: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("=" * 78)
    lines.append("MARKET & SECTOR REGIME FORECASTER — VALIDATION REPORT (Phase 2)")
    lines.append("=" * 78)
    meta = artifact.get("meta") or {}
    lines.append(f"forecaster version : {meta.get('forecaster_version','—')}")
    lines.append(f"validator version  : {meta.get('validator_version','—')}")
    lines.append(f"built_at           : {meta.get('built_at','—')}")
    lines.append(f"sample period      : {meta.get('start','—')} → {meta.get('end','—')}")
    lines.append(f"forecast dates (n) : {meta.get('n_dates',0)}")
    lines.append(f"horizons (5d, 10d) : {meta.get('horizons')}")
    lines.append("")

    cov = artifact.get("data_coverage") or {}
    lines.append("DATA COVERAGE")
    lines.append("-" * 78)
    lines.append(f"  symbols with frames : {cov.get('symbols_with_frames',0)} of {cov.get('symbols_total',0)}")
    miss = cov.get("symbols_missing") or []
    lines.append(f"  symbols missing     : {(', '.join(miss)) if miss else 'none'}")
    lines.append(f"  vix history bars    : {cov.get('vix_history_bars',0)}")
    lines.append("")

    lines.append("REGIME RETURN TABLE (forward outcomes by predicted dominant regime)")
    lines.append("-" * 78)
    lines.append(f"  {'regime':30s} {'n':>4s} {'avg5d%':>8s} {'avg10d%':>8s} {'win5d':>6s} {'win10d':>6s} {'fwdDD%':>7s} {'rvPost':>7s}")
    for r in artifact.get("regime_table") or []:
        n = r.get("n", 0)
        if not n:
            lines.append(f"  {r.get('regime',''):30s} {n:>4d}   (no samples)")
            continue
        lines.append(
            f"  {r.get('regime',''):30s} {n:>4d} "
            f"{(r.get('avg_spy_5d_pct') or 0):>+8.2f} "
            f"{(r.get('avg_spy_10d_pct') or 0):>+8.2f} "
            f"{(100*(r.get('win_rate_spy_5d') or 0)):>5.0f}% "
            f"{(100*(r.get('win_rate_spy_10d') or 0)):>5.0f}% "
            f"{(r.get('avg_spy_5d_drawdown_pct') or 0):>+7.2f} "
            f"{(r.get('avg_realized_vol_post_10d') or 0):>7.1f}"
        )
    lines.append("")

    lines.append("SECTOR VALIDATION (top-3 minus bottom-3 predicted sectors, forward returns)")
    lines.append("-" * 78)
    sec = artifact.get("sector_validation") or {}
    lines.append(f"  samples with both top & bottom baskets : {sec.get('n_with_spread', 0)}")
    def _f2(v: Any) -> str:
        try:
            return f"{float(v):.2f}" if v is not None else "—"
        except Exception:
            return "—"
    lines.append(f"  avg top-minus-bottom 5d  : {(sec.get('avg_top_minus_bottom_5d_pct') or 0):+.2f}pp"
                 f"   (stdev {_f2(sec.get('stdev_top_minus_bottom_5d'))})")
    lines.append(f"  avg top-minus-bottom 10d : {(sec.get('avg_top_minus_bottom_10d_pct') or 0):+.2f}pp"
                 f"   (stdev {_f2(sec.get('stdev_top_minus_bottom_10d'))})")
    lines.append(f"  win rate (top > bottom) 5d/10d : "
                 f"{(100*(sec.get('win_rate_top_gt_bottom_5d') or 0)):.0f}% / "
                 f"{(100*(sec.get('win_rate_top_gt_bottom_10d') or 0)):.0f}%")
    lines.append("")

    def _f3(v: Any) -> str:
        try:
            return f"{float(v):.3f}" if v is not None else "—"
        except Exception:
            return "—"

    def _pct(v: Any) -> str:
        try:
            return f"{100.0*float(v):.1f}%" if v is not None else "—"
        except Exception:
            return "—"

    lines.append("PROBABILITY CALIBRATION")
    lines.append("-" * 78)
    cal = artifact.get("calibration") or {}
    lines.append(f"  pairs n            : {cal.get('n_pairs',0)}")
    lines.append(f"  base rate bullish  : {_pct(cal.get('base_rate_bullish'))}")
    lines.append(f"  base rate riskoff  : {_pct(cal.get('base_rate_riskoff'))}")
    lines.append(f"  avg p(bullish)     : {_pct(cal.get('avg_p_bullish'))}")
    lines.append(f"  avg p(riskoff)     : {_pct(cal.get('avg_p_riskoff'))}")
    lines.append(f"  Brier (bullish)    : {_f3(cal.get('brier_bullish'))}")
    lines.append(f"  Brier (riskoff)    : {_f3(cal.get('brier_riskoff'))}")
    lines.append("  bullish buckets    :")
    for b in cal.get("buckets_bullish") or []:
        lines.append(
            f"    {b.get('bucket'):<7s}  n={b.get('n',0):>4d}  "
            f"avg_p={_pct(b.get('avg_predicted_p'))}  actual={_pct(b.get('actual_frequency'))}"
        )
    lines.append("  risk-off buckets   :")
    for b in cal.get("buckets_riskoff") or []:
        lines.append(
            f"    {b.get('bucket'):<7s}  n={b.get('n',0):>4d}  "
            f"avg_p={_pct(b.get('avg_predicted_p'))}  actual={_pct(b.get('actual_frequency'))}"
        )
    lines.append("")

    lines.append("CONFUSION MATRIX (predicted coarse class vs realized coarse class)")
    lines.append("-" * 78)
    conf = artifact.get("confusion_matrix") or {}
    classes = ["bullish", "chop", "risk-off / stress"]
    header = "  " + " " * 22 + "".join(f"{c:>20s}" for c in classes)
    lines.append(header)
    for p in classes:
        row = "  predicted " + f"{p:<12s}" + "".join(f"{conf.get(p,{}).get(a,0):>20d}" for a in classes)
        lines.append(row)
    lines.append("")

    lines.append("FALSE-CONFIDENCE EXAMPLES (high-confidence misses)")
    lines.append("-" * 78)
    fc = artifact.get("false_confidence") or []
    if not fc:
        lines.append("  (none — no high-confidence misclassifications in the sample)")
    else:
        def _f2x(v: Any, suffix: str = "%") -> str:
            try:
                return f"{float(v):+.2f}{suffix}" if v is not None else "—"
            except Exception:
                return "—"
        for ex in fc:
            lines.append(
                f"  {ex.get('date')}  pred={ex.get('predicted_coarse')}/{ex.get('predicted_regime')}  "
                f"realized={ex.get('realized_coarse')}  "
                f"spy_5d={_f2x(ex.get('spy_5d_pct'))}  "
                f"spy_dd_5d={_f2x(ex.get('spy_dd_5d_pct'))}  "
                f"vix_5d={_f2x(ex.get('vix_chg_5d_rel_pct'))}"
            )
    lines.append("")

    lines.append("INTERPRETATION & VERDICT")
    lines.append("-" * 78)
    verdict = artifact.get("verdict") or {}
    lines.append(f"  verdict  : {verdict.get('verdict','—')}")
    for r in verdict.get("rationale") or []:
        lines.append(f"  rationale: {r}")
    for b in verdict.get("bullets") or []:
        lines.append(f"  • {b}")
    for f in verdict.get("flags") or []:
        lines.append(f"  ! {f}")
    lines.append("")

    lines.append("LABEL THRESHOLDS (realized class definitions)")
    lines.append("-" * 78)
    for k, v in (artifact.get("label_thresholds") or {}).items():
        lines.append(f"  {k:<40s}: {v}")
    lines.append("")

    lines.append("CALIBRATION GUIDANCE")
    lines.append("-" * 78)
    for g in artifact.get("calibration_guidance") or []:
        lines.append(f"  - {g}")
    lines.append("")

    lines.append("GUARDRAILS")
    lines.append("-" * 78)
    for g in artifact.get("guardrails") or []:
        lines.append(f"  - {g}")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _write_csv_rows(rows: List[WalkForwardRow]) -> Optional[str]:
    if not rows:
        return None
    try:
        ARTIFACT_CSV.parent.mkdir(parents=True, exist_ok=True)
        with ARTIFACT_CSV.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(asdict(rows[0]).keys()))
            w.writeheader()
            for r in rows:
                w.writerow(asdict(r))
        return str(ARTIFACT_CSV)
    except Exception as exc:
        logger.warning("CSV write failed: %s", exc)
        return None


# ── CLI ─────────────────────────────────────────────────────────────────────


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Walk-forward validator for the Regime Forecaster V1")
    parser.add_argument("--start", type=str, default="2020-01-01",
                        help="validation window start (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, default=date.today().isoformat(),
                        help="validation window end (YYYY-MM-DD)")
    parser.add_argument("--horizons", type=str, default="5,10",
                        help="comma-separated forward horizons in trading days (default 5,10)")
    parser.add_argument("--sample-step", type=str, default="1d",
                        help="evaluate every Nd trading days; '1d' = every day, '5d' = every 5 days")
    parser.add_argument("--max-rows", type=int, default=0,
                        help="cap on evaluation dates (smoke testing); 0 = unlimited")
    parser.add_argument("--cache-only", action="store_true",
                        help="never call providers; use cached parquets only")
    parser.add_argument("--backfill", action="store_true",
                        help="if range exceeds cache, fetch missing history via yfinance "
                             "(written to cache/research/regime_validation_prices/, NOT to cache/prices)")
    parser.add_argument("--no-csv", action="store_true",
                        help="skip writing the per-date CSV artifact")
    parser.add_argument("--verbose", action="store_true",
                        help="DEBUG-level logging")
    return parser.parse_args(argv)


def _resolve_step(step: str) -> int:
    s = step.strip().lower()
    if s.endswith("d"):
        s = s[:-1]
    try:
        n = int(s)
        return max(1, n)
    except Exception:
        return 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.verbose:
        logger.setLevel(logging.DEBUG)
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    h_parts = [int(x) for x in str(args.horizons).split(",") if x.strip()]
    if len(h_parts) < 2:
        h_parts = [5, 10]
    horizons = (h_parts[0], h_parts[1])
    step = _resolve_step(args.sample_step)

    logger.info("loading deep history for %d symbols (cache_only=%s, backfill=%s) %s..%s",
                len(ALL_SYMBOLS), args.cache_only, args.backfill, start, end)
    full_frames: Dict[str, pd.DataFrame] = {}
    missing: List[str] = []
    for sym in ALL_SYMBOLS:
        df = _load_symbol_history(sym, start, end, cache_only=args.cache_only, backfill=args.backfill)
        if df is None or df.empty:
            missing.append(sym)
        else:
            full_frames[sym] = df

    full_vix = _load_vix_history(start, end, cache_only=args.cache_only, backfill=args.backfill)

    spy = full_frames.get("SPY")
    if spy is None or spy.empty:
        logger.error("SPY history is required and was not available — aborting")
        return 2

    dates = _trading_dates(spy, start, end)
    # Walk-forward minimum lookback: the forecaster's 50d MA + a small buffer.
    # Drop any leading dates where SPY does not yet have enough history; this
    # is what otherwise produced silent "0 valid rows" smoke runs.
    min_bars = 60
    full_spy_close = spy["close"]
    usable_dates: List[pd.Timestamp] = []
    for d in dates:
        try:
            idx = full_spy_close.index.get_loc(d)
        except KeyError:
            continue
        if isinstance(idx, slice):
            continue
        if idx + 1 >= min_bars:
            usable_dates.append(d)
    skipped = len(dates) - len(usable_dates)
    if skipped > 0:
        logger.info("skipping %d leading dates without %d-bar lookback", skipped, min_bars)
    dates = usable_dates
    if step > 1:
        dates = dates[::step]
    if args.max_rows and args.max_rows > 0:
        dates = dates[: args.max_rows]
    logger.info("evaluating %d walk-forward dates (step=%dd, horizons=%s)",
                len(dates), step, horizons)

    rows: List[WalkForwardRow] = []
    for t in dates:
        row = evaluate_one(t, full_frames, full_vix, horizons)
        if row is not None:
            rows.append(row)

    logger.info("evaluated %d valid rows", len(rows))

    regime_table = regime_return_table(rows)
    sector = sector_validation(rows)
    cal = calibration(rows)
    confusion = confusion_matrix(rows)
    fc = false_confidence_examples(rows, limit=8)
    verdict = derive_verdict(len(rows), regime_table, sector, cal, confusion)

    artifact: Dict[str, Any] = {
        "meta": {
            "validator_version": VERSION,
            "forecaster_version": FORECASTER_VERSION,
            "built_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "horizons": list(horizons),
            "sample_step_days": step,
            "n_dates": len(rows),
            "cache_only": bool(args.cache_only),
            "backfill": bool(args.backfill),
        },
        "data_coverage": {
            "symbols_total": len(ALL_SYMBOLS),
            "symbols_with_frames": len(full_frames),
            "symbols_missing": missing,
            "vix_history_bars": int(len(full_vix)) if full_vix is not None else 0,
        },
        "label_thresholds": LABEL_THRESHOLDS,
        "regime_table": regime_table,
        "sector_validation": sector,
        "calibration": cal,
        "confusion_matrix": confusion,
        "false_confidence": fc,
        "verdict": verdict,
        "calibration_guidance": [
            "V1 probabilities are heuristic; do not interpret a 60% as a calibrated 60%.",
            "If the bullish bucket consistently over-predicts vs realized frequency, "
            "consider shrinking bullish weights or trimming the constructive_mass tilt.",
            "If high-confidence labels show too many false positives, raise the margin "
            "threshold required for `confidence='high'`.",
            "Do not retune weights inside this phase; surface findings, then propose a "
            "single calibration pass in a follow-up.",
        ],
        "guardrails": [
            "research-only / not trade approval",
            "no live sleeve, paper, governance, execution, dashboard, Alpha Discovery, "
            "Market Posture, Daily Entry Validator, or Social Arb changes",
            "no lookahead — forecasts use only data <= each evaluation date",
            "VIX history is truncated to <= each evaluation date before being passed in",
            "forward outcomes are computed from full data but only used for scoring",
            "no ML, no overfitting / weight tuning in this phase",
            "no news/social as a core forecasting input",
            "validation-only price backfill is written to "
            "cache/research/regime_validation_prices/, never to cache/prices",
        ],
    }

    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    cfg.LOG_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = None if args.no_csv else _write_csv_rows(rows)
    artifact["artifacts"] = {
        "json": str(ARTIFACT_JSON),
        "text": str(ARTIFACT_TEXT),
        "csv": csv_path,
    }
    ARTIFACT_JSON.write_text(json.dumps(artifact, indent=2, default=str), encoding="utf-8")
    ARTIFACT_TEXT.write_text(_render_text(artifact), encoding="utf-8")

    print(
        f"validate_regime_forecaster: n={len(rows)} verdict={verdict.get('verdict','—')} "
        f"→ {ARTIFACT_JSON}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

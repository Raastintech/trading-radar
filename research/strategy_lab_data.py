"""
research/strategy_lab_data.py - Strategy Research Lab no-lookahead data layer.

Research-only and cache-only. This module reads local parquet/JSON artifacts,
computes price-derived features using bars at-or-before an as-of date, and
labels every non-price approximation explicitly.

Phase 1H.1: features are computed once per ticker as vectorized rolling
columns (the "feature table") and looked up per as-of date, instead of being
recomputed per (ticker, date). The original per-date implementation is kept
as `compute_features_asof_legacy` and used automatically for any ticker whose
price frame contains non-finite values, so semantics are preserved; an
equivalence test pins fast == legacy on a fixture. Set FAST_FEATURES = False
(and clear caches) to force the legacy path everywhere.
"""
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from statistics import median
from typing import Any, Dict, Iterable, List, Optional, Sequence

import numpy as np
import pandas as pd

from research.scanner_truth import dataio
from research.scanner_truth.filters import atr, rsi, sma

TRUE_POINT_IN_TIME = "TRUE_POINT_IN_TIME"
RECONSTRUCTED_FROM_PRICE_ONLY = "RECONSTRUCTED_FROM_PRICE_ONLY"
CURRENT_METADATA_APPROXIMATION = "CURRENT_METADATA_APPROXIMATION"
NOT_RETAINED = "NOT_RETAINED"

ROOT = Path(__file__).resolve().parents[1]
BACKTEST_PRICES_DIR = ROOT / "cache" / "backtest_prices"
DEEP_PRICES_DIR = ROOT / "cache" / "prices_deep"
SHALLOW_PRICES_DIR = ROOT / "cache" / "prices"
UNIVERSE_SNAPSHOT = ROOT / "cache" / "universe" / "universe_snapshot_latest.json"

BENCHMARKS = ("SPY", "QQQ", "SMH", "XLK")

DEFAULT_UNIVERSE_CAP = 160
DEFAULT_CANDIDATE_POOL_CAP = 450

# Phase 1H.1 in-memory performance caches. Cache-only, per-process, never
# persisted; cleared via clear_caches_for_tests() / compute_features_asof.cache_clear().
FAST_FEATURES = True
_TABLE_CACHE: Dict[str, Any] = {}
_FEATURE_MEMO: Dict[Any, Optional[Dict[str, Any]]] = {}
_FEATURE_MEMO_MAX = 400_000

# SNIPER v6 large-cap universe, mirrored to avoid importing provider-backed
# strategy modules.
SNIPER_LARGE_CAP_UNIVERSE = {
    "NVDA", "AMD", "META", "AAPL", "MSFT", "GOOGL", "AMZN", "NFLX", "AVGO",
    "QCOM", "AMAT", "LRCX", "MRVL", "JPM", "GS", "V", "MA", "LLY", "ABBV",
    "REGN", "NKE", "LULU", "HD", "LOW", "XOM", "CVX", "CRM", "ADBE", "PANW",
    "SHOP", "MELI", "UBER", "QQQ", "XLK", "XLF", "XLE", "XLY", "XLV", "XLI",
    "TLT", "GLD", "XLU", "XLP", "WMT", "COST", "PG",
}

POWER_THEMES = {
    "semiconductors", "hardware", "memory_storage", "space_aerospace",
    "nuclear_energy", "quantum", "crypto_blockchain",
}


def _as_ts(value: Any) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is not None:
        ts = ts.tz_convert(None)
    return ts.normalize()


def _norm_ticker(ticker: Any) -> str:
    return str(ticker or "").strip().upper()


def _price_paths(ticker: str) -> List[tuple[str, Path]]:
    t = _norm_ticker(ticker)
    return [
        ("BACKTEST_CACHE", BACKTEST_PRICES_DIR / f"{t}.parquet"),
        ("DEEP_CACHE", DEEP_PRICES_DIR / f"{t}.parquet"),
        ("SHALLOW_CACHE", SHALLOW_PRICES_DIR / f"{t}.parquet"),
    ]


def _read_price_path(path: Path) -> Optional[pd.DataFrame]:
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path)
    except Exception:
        return None
    if df is None or df.empty or "close" not in df.columns:
        return None
    df = df.copy()
    df.index = pd.to_datetime(df.index)
    if getattr(df.index, "tz", None) is not None:
        df.index = df.index.tz_convert(None)
    df.index = df.index.normalize()
    needed = ["open", "high", "low", "close", "volume"]
    for col in needed:
        if col not in df.columns:
            if col == "open":
                df[col] = df["close"]
            elif col in ("high", "low"):
                df[col] = df["close"]
            else:
                df[col] = 0
    df = df[needed].sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df


@lru_cache(maxsize=8192)
def _load_merged_prices(ticker: str) -> Optional[pd.DataFrame]:
    """Merge all retained local price caches for one ticker.

    The merge is cache-only. Duplicate dates keep the later source in this
    order: backtest, deep, shallow. That preserves older backtest history and
    lets the current shallow cache provide the newest bars when deep/backtest
    stop earlier.
    """
    frames: List[pd.DataFrame] = []
    sources: List[str] = []
    for source, path in _price_paths(ticker):
        df = _read_price_path(path)
        if df is None:
            continue
        piece = df.copy()
        piece["_source"] = source
        frames.append(piece)
        sources.append(source)
    if not frames:
        return None
    merged = pd.concat(frames).sort_index()
    merged = merged[~merged.index.duplicated(keep="last")]
    merged.attrs["sources"] = sorted(set(sources))
    return merged


@lru_cache(maxsize=1)
def _all_price_tickers() -> tuple[str, ...]:
    tickers = set()
    for directory in (BACKTEST_PRICES_DIR, DEEP_PRICES_DIR, SHALLOW_PRICES_DIR):
        if directory.exists():
            tickers.update(p.stem.upper() for p in directory.glob("*.parquet"))
    return tuple(sorted(tickers))


@lru_cache(maxsize=1)
def _current_universe_tickers() -> tuple[str, ...]:
    if not UNIVERSE_SNAPSHOT.exists():
        return ()
    try:
        data = json.loads(UNIVERSE_SNAPSHOT.read_text(encoding="utf-8"))
    except Exception:
        return ()
    out: List[str] = []
    for key in ("base_universe", "tickers", "symbols"):
        vals = data.get(key) or []
        if isinstance(vals, list):
            out.extend(_norm_ticker(v) for v in vals)
    for row in data.get("strategy_candidates") or []:
        if isinstance(row, dict):
            out.append(_norm_ticker(row.get("ticker") or row.get("symbol")))
    return tuple(t for t in dict.fromkeys(out) if t)


@lru_cache(maxsize=1)
def load_profiles_current() -> Dict[str, Dict[str, Any]]:
    try:
        return dataio.load_profiles()
    except Exception:
        return {}


def metadata_for_ticker(ticker: str) -> Dict[str, Any]:
    profiles = load_profiles_current()
    profile = profiles.get(_norm_ticker(ticker)) or {}
    return {
        "sector": profile.get("sector") or "UNKNOWN",
        "industry": profile.get("industry") or "UNKNOWN",
        "market_cap": profile.get("market_cap"),
        "company_name": profile.get("company_name"),
        "theme": dataio.classify_theme(profile),
        "metadata_reliability": CURRENT_METADATA_APPROXIMATION if profile else NOT_RETAINED,
    }


def available_price_summary(ticker: str) -> Dict[str, Any]:
    df = _load_merged_prices(_norm_ticker(ticker))
    if df is None or df.empty:
        return {
            "ticker": _norm_ticker(ticker),
            "available": False,
            "sources": [],
            "min_date": None,
            "max_date": None,
            "rows": 0,
        }
    return {
        "ticker": _norm_ticker(ticker),
        "available": True,
        "sources": list(df.attrs.get("sources") or []),
        "min_date": str(df.index.min().date()),
        "max_date": str(df.index.max().date()),
        "rows": int(len(df)),
    }


def load_price_frame_asof(ticker: str, asof: Any) -> Optional[pd.DataFrame]:
    """Return OHLCV bars with index <= asof.

    The returned DataFrame never contains future bars. Attributes carry source
    and reliability labels for downstream reports/tests.
    """
    t = _norm_ticker(ticker)
    df = _load_merged_prices(t)
    if df is None:
        return None
    ts = _as_ts(asof)
    out = df[df.index <= ts].copy()
    if out.empty:
        return None
    out.attrs["ticker"] = t
    out.attrs["asof"] = str(ts.date())
    out.attrs["sources"] = list(df.attrs.get("sources") or [])
    out.attrs["price_reliability"] = TRUE_POINT_IN_TIME
    out.attrs["feature_reliability"] = RECONSTRUCTED_FROM_PRICE_ONLY
    return out


def _full_frame(ticker: str) -> Optional[pd.DataFrame]:
    """Full merged frame for read-only slicing. Callers must not mutate it;
    get_forward_window copies the slice it returns."""
    return _load_merged_prices(_norm_ticker(ticker))


def _ret_from_series(series: pd.Series, bars_back: int) -> Optional[float]:
    s = series.dropna()
    if len(s) <= bars_back:
        return None
    a = float(s.iloc[-bars_back - 1])
    b = float(s.iloc[-1])
    if a <= 0 or not math.isfinite(a) or not math.isfinite(b):
        return None
    return b / a - 1.0


def _rolling_value(series: pd.Series, n: int) -> Optional[float]:
    if len(series.dropna()) < n:
        return None
    value = series.rolling(n, min_periods=n).mean().iloc[-1]
    return None if pd.isna(value) else float(value)


def _ema_value(series: pd.Series, n: int) -> Optional[float]:
    s = series.dropna()
    if len(s) < max(3, min(n, 10)):
        return None
    return float(s.ewm(span=n, min_periods=min(n, len(s))).mean().iloc[-1])


def _bench_ret(symbol: str, asof: Any, bars_back: int) -> Optional[float]:
    b = load_price_frame_asof(symbol, asof)
    if b is None:
        return None
    return _ret_from_series(b["close"], bars_back)


def _dvol_ratio(df: pd.DataFrame) -> Optional[float]:
    if len(df) < 60:
        return None
    dvol = df["close"] * df["volume"]
    recent = float(dvol.tail(20).mean())
    prior = float(dvol.iloc[-60:-20].mean())
    if prior <= 0:
        return None
    return recent / prior


def _up_volume_ratio(df: pd.DataFrame) -> Optional[float]:
    if len(df) < 20:
        return None
    d = df.tail(20)
    up = d[d["close"] >= d["open"]]["volume"]
    down = d[d["close"] < d["open"]]["volume"]
    up_avg = float(up.mean()) if len(up) else 0.0
    down_avg = float(down.mean()) if len(down) else 1.0
    return up_avg / down_avg if down_avg > 0 else None


def _atr_contraction(df: pd.DataFrame) -> Optional[float]:
    if len(df) < 25:
        return None
    a = atr(df, 14)
    recent = float(a.iloc[-6:-1].mean())
    prior = float(a.iloc[-21:-6].mean())
    if prior <= 0 or pd.isna(prior) or pd.isna(recent):
        return None
    return recent / prior


def _sector_rs(rows: Sequence[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    by_sector: Dict[str, List[float]] = {}
    for row in rows:
        r20 = row.get("r20")
        if isinstance(r20, (int, float)) and math.isfinite(r20):
            by_sector.setdefault(row.get("sector") or "UNKNOWN", []).append(float(r20))
    med = {s: median(v) for s, v in by_sector.items() if v}
    out: Dict[str, Optional[float]] = {}
    for row in rows:
        r20 = row.get("r20")
        sector = row.get("sector") or "UNKNOWN"
        out[row["ticker"]] = (
            float(r20) - med[sector]
            if isinstance(r20, (int, float)) and sector in med
            else None
        )
    return out


def compute_features_asof_legacy(ticker: str, asof: Any) -> Optional[Dict[str, Any]]:
    """Original per-(ticker, date) feature computation. Reference semantics for
    the fast table path; also the fallback for non-finite price frames."""
    t = _norm_ticker(ticker)
    df = load_price_frame_asof(t, asof)
    if df is None or len(df) < 20:
        return None
    validate_no_future_bars(df, asof)
    close = df["close"]
    price = float(close.iloc[-1])
    if not math.isfinite(price) or price <= 0:
        return None
    volume = df["volume"]
    avg_vol20 = float(volume.tail(20).mean()) if len(df) >= 20 else None
    avg_dvol20 = float((close * volume).tail(20).mean()) if len(df) >= 20 else None
    ma20 = _rolling_value(close, 20)
    ma50 = _rolling_value(close, 50)
    ma200 = _rolling_value(close, 200)
    ema20 = _ema_value(close, 20)
    ema50 = _ema_value(close, 50)
    ma50_prev = None
    if len(df) >= 70:
        ma50_prev = float(close.iloc[-70:-20].mean())
    ma50_rising = bool(ma50 is not None and ma50_prev is not None and ma50 > ma50_prev)

    a14 = atr(df, 14)
    atr14 = None if a14.empty or pd.isna(a14.iloc[-1]) else float(a14.iloc[-1])
    rsi14 = rsi(close, 14)
    rsi_now = None if rsi14.empty or pd.isna(rsi14.iloc[-1]) else float(rsi14.iloc[-1])

    prior_20_high = float(df["high"].iloc[-21:-1].max()) if len(df) >= 21 else None
    high20 = float(df["high"].tail(20).max())
    high50 = float(df["high"].tail(min(50, len(df))).max())
    high60 = float(df["high"].tail(min(60, len(df))).max())
    low20 = float(df["low"].tail(20).min())
    prev_close = float(close.iloc[-2]) if len(close) >= 2 else None
    breakout = bool(prior_20_high is not None and price > prior_20_high)
    first_breakout = bool(
        breakout and prev_close is not None and prior_20_high is not None
        and prev_close <= prior_20_high
    )

    def ret(n: int) -> Optional[float]:
        return _ret_from_series(close, n)

    r5, r10, r20, r40, r50, r60, r130 = (
        ret(5), ret(10), ret(20), ret(40), ret(50), ret(60), ret(130)
    )
    spy10, spy20, spy50, spy60, spy130 = (
        _bench_ret("SPY", asof, 10),
        _bench_ret("SPY", asof, 20),
        _bench_ret("SPY", asof, 50),
        _bench_ret("SPY", asof, 60),
        _bench_ret("SPY", asof, 130),
    )
    qqq10, qqq20, qqq60 = (
        _bench_ret("QQQ", asof, 10),
        _bench_ret("QQQ", asof, 20),
        _bench_ret("QQQ", asof, 60),
    )

    spy_df = load_price_frame_asof("SPY", asof)
    spy_above_ma200 = None
    if spy_df is not None and len(spy_df) >= 200:
        spy_ma200 = float(spy_df["close"].rolling(200, min_periods=200).mean().iloc[-1])
        spy_above_ma200 = bool(float(spy_df["close"].iloc[-1]) > spy_ma200)

    meta = metadata_for_ticker(t)
    ext_ema20 = (price - ema20) / ema20 if ema20 else None
    ext_ma50 = (price - ma50) / ma50 if ma50 else None
    ext_ma200 = (price - ma200) / ma200 if ma200 else None
    vol_expansion = None
    if len(volume) >= 40:
        prior_vol = float(volume.iloc[-40:-20].mean())
        vol_expansion = float(avg_vol20 / prior_vol) if prior_vol > 0 and avg_vol20 else None
    range10 = None
    if len(df) >= 10:
        d10 = df.tail(10)
        range10 = float((d10["high"].max() - d10["low"].min()) / price)

    return {
        "ticker": t,
        "asof": str(_as_ts(asof).date()),
        "bar_date": str(df.index[-1].date()),
        "bars": int(len(df)),
        "sources": list(df.attrs.get("sources") or []),
        "data_reliability": {
            "price": TRUE_POINT_IN_TIME,
            "features": RECONSTRUCTED_FROM_PRICE_ONLY,
            "metadata": meta["metadata_reliability"],
            "fundamentals": NOT_RETAINED,
            "stock_lens": NOT_RETAINED,
            "gatekeeper": NOT_RETAINED,
        },
        "price": price,
        "open": float(df["open"].iloc[-1]),
        "high": float(df["high"].iloc[-1]),
        "low": float(df["low"].iloc[-1]),
        "volume": float(df["volume"].iloc[-1]),
        "avg_vol20": avg_vol20,
        "avg_dvol20": avg_dvol20,
        "ma20": ma20,
        "ma50": ma50,
        "ma200": ma200,
        "ema20": ema20,
        "ema50": ema50,
        "ma50_rising": ma50_rising,
        "atr14": atr14,
        "atr_pct": (atr14 / price) if atr14 and price else None,
        "atr_contraction": _atr_contraction(df),
        "rsi14": rsi_now,
        "r5": r5,
        "r10": r10,
        "r20": r20,
        "r40": r40,
        "r50": r50,
        "r60": r60,
        "r130": r130,
        "rs10_spy": (r10 - spy10) if r10 is not None and spy10 is not None else None,
        "rs20_spy": (r20 - spy20) if r20 is not None and spy20 is not None else None,
        "rs50_spy": (r50 - spy50) if r50 is not None and spy50 is not None else None,
        "rs60_spy": (r60 - spy60) if r60 is not None and spy60 is not None else None,
        "rs130_spy": (r130 - spy130) if r130 is not None and spy130 is not None else None,
        "rs10_qqq": (r10 - qqq10) if r10 is not None and qqq10 is not None else None,
        "rs20_qqq": (r20 - qqq20) if r20 is not None and qqq20 is not None else None,
        "rs60_qqq": (r60 - qqq60) if r60 is not None and qqq60 is not None else None,
        "spy_above_ma200": spy_above_ma200,
        "dvol_ratio": _dvol_ratio(df),
        "up_vol_ratio": _up_volume_ratio(df),
        "vol_expansion": vol_expansion,
        "prior_20_high": prior_20_high,
        "high20": high20,
        "high50": high50,
        "high60": high60,
        "low20": low20,
        "near_20d_high": price >= high20 * 0.97 if high20 else False,
        "near_50d_high": price >= high50 * 0.97 if high50 else False,
        "breakout": breakout,
        "first_breakout": first_breakout,
        "ext_ema20": ext_ema20,
        "ext_ma50": ext_ma50,
        "ext_ma200": ext_ma200,
        "drawdown_from_high20": (price / high20 - 1.0) if high20 else None,
        "drawdown_from_high60": (price / high60 - 1.0) if high60 else None,
        "range10_pct": range10,
        "above_ema20": price >= ema20 if ema20 else None,
        "above_ma50": price >= ma50 if ma50 else None,
        "above_ma200": price >= ma200 if ma200 else None,
        "sector": meta["sector"],
        "industry": meta["industry"],
        "theme": meta["theme"],
        "market_cap": meta["market_cap"],
    }


# ── Phase 1H.1 vectorized feature tables ─────────────────────────────────────

_RET_WINDOWS = (5, 10, 20, 40, 50, 60, 130)


def _build_feature_table(df: pd.DataFrame) -> Optional[pd.DataFrame]:
    """Vectorize every rolling feature once over the merged frame.

    Each row of the result equals compute_features_asof_legacy evaluated with
    that row's bar as the last available bar. Returns None when the frame has
    non-finite OHLCV values so the caller falls back to the legacy path, whose
    dropna/NaN semantics are the reference.
    """
    ohlcv = df[["open", "high", "low", "close", "volume"]].astype(float)
    if not np.isfinite(ohlcv.to_numpy()).all():
        return None
    o, h, l, c, v = (ohlcv[k] for k in ("open", "high", "low", "close", "volume"))
    n = len(df)
    cnt = pd.Series(np.arange(1, n + 1, dtype=float), index=df.index)

    t = pd.DataFrame(index=df.index)
    t["count"] = cnt
    t["price"] = c
    t["open"] = o
    t["high"] = h
    t["low"] = l
    t["volume"] = v
    dvol = c * v
    t["avg_vol20"] = v.rolling(20, min_periods=1).mean().where(cnt >= 20)
    t["avg_dvol20"] = dvol.rolling(20, min_periods=1).mean().where(cnt >= 20)
    t["ma20"] = c.rolling(20, min_periods=20).mean()
    t["ma50"] = c.rolling(50, min_periods=50).mean()
    t["ma200"] = c.rolling(200, min_periods=200).mean()
    # _ema_value guard: len >= max(3, min(n, 10)) == 10 for spans 20/50.
    t["ema20"] = c.ewm(span=20, min_periods=1).mean().where(cnt >= 10)
    t["ema50"] = c.ewm(span=50, min_periods=1).mean().where(cnt >= 10)
    ma50_prev = c.rolling(50, min_periods=1).mean().shift(20).where(cnt >= 70)
    t["ma50_rising"] = (t["ma50"].notna() & ma50_prev.notna() & (t["ma50"] > ma50_prev))

    a14 = atr(df, 14)
    t["atr14"] = a14
    atr_recent = a14.rolling(5, min_periods=1).mean().shift(1)
    atr_prior = a14.rolling(15, min_periods=1).mean().shift(6)
    t["atr_contraction"] = (atr_recent / atr_prior).where(
        (cnt >= 25) & atr_recent.notna() & atr_prior.notna() & (atr_prior > 0)
    )
    t["rsi14"] = rsi(c, 14)

    for nb in _RET_WINDOWS:
        prev = c.shift(nb)
        t[f"r{nb}"] = (c / prev - 1.0).where(prev.notna() & (prev > 0))

    t["prior_20_high"] = h.rolling(20, min_periods=1).max().shift(1).where(cnt >= 21)
    t["high20"] = h.rolling(20, min_periods=1).max()
    t["high50"] = h.rolling(50, min_periods=1).max()
    t["high60"] = h.rolling(60, min_periods=1).max()
    t["low20"] = l.rolling(20, min_periods=1).min()
    prev_close = c.shift(1)
    breakout = t["prior_20_high"].notna() & (c > t["prior_20_high"])
    t["breakout"] = breakout
    t["first_breakout"] = breakout & prev_close.notna() & (prev_close <= t["prior_20_high"])

    dvol_recent = dvol.rolling(20, min_periods=1).mean()
    dvol_prior = dvol.rolling(40, min_periods=1).mean().shift(20)
    t["dvol_ratio"] = (dvol_recent / dvol_prior).where(
        (cnt >= 60) & dvol_prior.notna() & (dvol_prior > 0)
    )
    up_mask = c >= o
    t["up_vol20_mean"] = v.where(up_mask).rolling(20, min_periods=1).mean()
    t["down_vol20_mean"] = v.where(~up_mask).rolling(20, min_periods=1).mean()
    vol_prior = v.rolling(20, min_periods=1).mean().shift(20).where(cnt >= 40)
    t["vol_expansion"] = (t["avg_vol20"] / vol_prior).where(
        vol_prior.notna() & (vol_prior > 0) & t["avg_vol20"].notna() & (t["avg_vol20"] != 0)
    )
    t["range10_pct"] = (
        (h.rolling(10, min_periods=1).max() - l.rolling(10, min_periods=1).min()) / c
    ).where(cnt >= 10)
    # SPY-only regime column, harmless for other tickers.
    t["above_ma200_strict"] = (c > t["ma200"]).where(t["ma200"].notna())
    return t


def _ticker_feature_table(ticker: str) -> Optional[pd.DataFrame]:
    t = _norm_ticker(ticker)
    if t in _TABLE_CACHE:
        return _TABLE_CACHE[t]
    df = _load_merged_prices(t)
    table: Optional[pd.DataFrame] = None
    if df is not None and not df.empty:
        try:
            table = _build_feature_table(df)
        except Exception:
            table = None
        if table is not None:
            table.attrs["sources"] = list(df.attrs.get("sources") or [])
    _TABLE_CACHE[t] = table
    return table


def _table_pos_asof(table: pd.DataFrame, ts: pd.Timestamp) -> Optional[int]:
    pos = int(table.index.searchsorted(ts, side="right")) - 1
    return pos if pos >= 0 else None


def _opt_float(value: Any) -> Optional[float]:
    return None if value is None or (isinstance(value, float) and math.isnan(value)) or pd.isna(value) else float(value)


def _opt_bool(value: Any) -> Optional[bool]:
    return None if value is None or pd.isna(value) else bool(value)


def _bench_snapshot(asof: Any) -> Dict[str, Optional[float]]:
    """Shared SPY/QQQ benchmark return + regime values for one as-of date."""
    ts = _as_ts(asof)
    key = ("__bench__", ts)
    if key in _FEATURE_MEMO:
        return _FEATURE_MEMO[key]  # type: ignore[return-value]
    out: Dict[str, Optional[float]] = {}
    spy_table = _ticker_feature_table("SPY")
    if spy_table is not None:
        pos = _table_pos_asof(spy_table, ts)
        if pos is not None:
            row = spy_table.iloc[pos]
            for nb in (10, 20, 50, 60, 130):
                out[f"spy{nb}"] = _opt_float(row[f"r{nb}"])
            if row["count"] >= 200 and not pd.isna(row["ma200"]):
                out["spy_above_ma200"] = bool(row["price"] > row["ma200"])
            else:
                out["spy_above_ma200"] = None
    else:
        for nb in (10, 20, 50, 60, 130):
            out[f"spy{nb}"] = _bench_ret("SPY", ts, nb)
        spy_df = load_price_frame_asof("SPY", ts)
        out["spy_above_ma200"] = None
        if spy_df is not None and len(spy_df) >= 200:
            spy_ma200 = float(spy_df["close"].rolling(200, min_periods=200).mean().iloc[-1])
            out["spy_above_ma200"] = bool(float(spy_df["close"].iloc[-1]) > spy_ma200)
    qqq_table = _ticker_feature_table("QQQ")
    if qqq_table is not None:
        pos = _table_pos_asof(qqq_table, ts)
        if pos is not None:
            row = qqq_table.iloc[pos]
            for nb in (10, 20, 60):
                out[f"qqq{nb}"] = _opt_float(row[f"r{nb}"])
    else:
        for nb in (10, 20, 60):
            out[f"qqq{nb}"] = _bench_ret("QQQ", ts, nb)
    for k in ("spy10", "spy20", "spy50", "spy60", "spy130", "qqq10", "qqq20", "qqq60", "spy_above_ma200"):
        out.setdefault(k, None)
    _FEATURE_MEMO[key] = out  # type: ignore[assignment]
    return out


def _compute_features_asof_fast(ticker: str, asof: Any) -> Optional[Dict[str, Any]]:
    t = _norm_ticker(ticker)
    table = _ticker_feature_table(t)
    if table is None:
        return compute_features_asof_legacy(t, asof)
    ts = _as_ts(asof)
    pos = _table_pos_asof(table, ts)
    if pos is None:
        return None
    row = table.iloc[pos]
    bars = int(row["count"])
    if bars < 20:
        return None
    price = float(row["price"])
    if not math.isfinite(price) or price <= 0:
        return None
    meta = metadata_for_ticker(t)
    bench = _bench_snapshot(ts)

    ma20 = _opt_float(row["ma20"])
    ma50 = _opt_float(row["ma50"])
    ma200 = _opt_float(row["ma200"])
    ema20 = _opt_float(row["ema20"])
    ema50 = _opt_float(row["ema50"])
    atr14 = _opt_float(row["atr14"])
    rets = {nb: _opt_float(row[f"r{nb}"]) for nb in _RET_WINDOWS}

    def rel(r: Optional[float], b: Optional[float]) -> Optional[float]:
        return (r - b) if r is not None and b is not None else None

    prior_20_high = _opt_float(row["prior_20_high"])
    high20 = float(row["high20"])
    high50 = float(row["high50"])
    high60 = float(row["high60"])
    up_avg = _opt_float(row["up_vol20_mean"])
    down_avg = _opt_float(row["down_vol20_mean"])
    up_avg = 0.0 if up_avg is None else up_avg
    down_avg = 1.0 if down_avg is None else down_avg
    avg_vol20 = _opt_float(row["avg_vol20"])
    avg_dvol20 = _opt_float(row["avg_dvol20"])
    return {
        "ticker": t,
        "asof": str(ts.date()),
        "bar_date": str(table.index[pos].date()),
        "bars": bars,
        "sources": list(table.attrs.get("sources") or []),
        "data_reliability": {
            "price": TRUE_POINT_IN_TIME,
            "features": RECONSTRUCTED_FROM_PRICE_ONLY,
            "metadata": meta["metadata_reliability"],
            "fundamentals": NOT_RETAINED,
            "stock_lens": NOT_RETAINED,
            "gatekeeper": NOT_RETAINED,
        },
        "price": price,
        "open": float(row["open"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "volume": float(row["volume"]),
        "avg_vol20": avg_vol20,
        "avg_dvol20": avg_dvol20,
        "ma20": ma20,
        "ma50": ma50,
        "ma200": ma200,
        "ema20": ema20,
        "ema50": ema50,
        "ma50_rising": bool(row["ma50_rising"]),
        "atr14": atr14,
        "atr_pct": (atr14 / price) if atr14 and price else None,
        "atr_contraction": _opt_float(row["atr_contraction"]),
        "rsi14": _opt_float(row["rsi14"]),
        "r5": rets[5],
        "r10": rets[10],
        "r20": rets[20],
        "r40": rets[40],
        "r50": rets[50],
        "r60": rets[60],
        "r130": rets[130],
        "rs10_spy": rel(rets[10], bench["spy10"]),
        "rs20_spy": rel(rets[20], bench["spy20"]),
        "rs50_spy": rel(rets[50], bench["spy50"]),
        "rs60_spy": rel(rets[60], bench["spy60"]),
        "rs130_spy": rel(rets[130], bench["spy130"]),
        "rs10_qqq": rel(rets[10], bench["qqq10"]),
        "rs20_qqq": rel(rets[20], bench["qqq20"]),
        "rs60_qqq": rel(rets[60], bench["qqq60"]),
        "spy_above_ma200": bench["spy_above_ma200"],
        "dvol_ratio": _opt_float(row["dvol_ratio"]),
        "up_vol_ratio": up_avg / down_avg if down_avg > 0 else None,
        "vol_expansion": _opt_float(row["vol_expansion"]),
        "prior_20_high": prior_20_high,
        "high20": high20,
        "high50": high50,
        "high60": high60,
        "low20": float(row["low20"]),
        "near_20d_high": bool(price >= high20 * 0.97) if high20 else False,
        "near_50d_high": bool(price >= high50 * 0.97) if high50 else False,
        "breakout": bool(row["breakout"]),
        "first_breakout": bool(row["first_breakout"]),
        "ext_ema20": (price - ema20) / ema20 if ema20 else None,
        "ext_ma50": (price - ma50) / ma50 if ma50 else None,
        "ext_ma200": (price - ma200) / ma200 if ma200 else None,
        "drawdown_from_high20": (price / high20 - 1.0) if high20 else None,
        "drawdown_from_high60": (price / high60 - 1.0) if high60 else None,
        "range10_pct": _opt_float(row["range10_pct"]),
        "above_ema20": bool(price >= ema20) if ema20 else None,
        "above_ma50": bool(price >= ma50) if ma50 else None,
        "above_ma200": bool(price >= ma200) if ma200 else None,
        "sector": meta["sector"],
        "industry": meta["industry"],
        "theme": meta["theme"],
        "market_cap": meta["market_cap"],
    }


def compute_features_asof(ticker: str, asof: Any) -> Optional[Dict[str, Any]]:
    """Compute no-lookahead price features for one ticker as-of a date.

    Memoized per (ticker, asof). Uses the vectorized table path when
    FAST_FEATURES is on, the legacy reference path otherwise (or when the
    ticker frame has non-finite values).
    """
    key = (_norm_ticker(ticker), str(_as_ts(asof).date()))
    if key in _FEATURE_MEMO:
        return _FEATURE_MEMO[key]
    if FAST_FEATURES:
        out = _compute_features_asof_fast(ticker, asof)
    else:
        out = compute_features_asof_legacy(ticker, asof)
    if len(_FEATURE_MEMO) >= _FEATURE_MEMO_MAX:
        _FEATURE_MEMO.clear()
    _FEATURE_MEMO[key] = out
    return out


def _clear_feature_caches() -> None:
    _FEATURE_MEMO.clear()
    _TABLE_CACHE.clear()


# Back-compat with the lru_cache interface used by existing tests.
compute_features_asof.cache_clear = _clear_feature_caches  # type: ignore[attr-defined]


@lru_cache(maxsize=256)
def _candidate_pool(mode: str) -> tuple[str, ...]:
    mode = str(mode or "research_core").lower()
    if mode == "sniper_current":
        return tuple(sorted(SNIPER_LARGE_CAP_UNIVERSE | set(BENCHMARKS)))
    if mode == "backtest_cache":
        return tuple(sorted(p.stem.upper() for p in BACKTEST_PRICES_DIR.glob("*.parquet")))
    if mode == "deep_cache":
        return tuple(sorted(p.stem.upper() for p in DEEP_PRICES_DIR.glob("*.parquet")))
    if mode == "current_universe":
        return tuple(_current_universe_tickers())
    if mode == "all_price_cache":
        return _all_price_tickers()

    # Default research core: broad enough to test current research surfaces,
    # bounded enough for fast local iteration.
    pool: List[str] = []
    pool.extend(sorted(SNIPER_LARGE_CAP_UNIVERSE))
    pool.extend(sorted(p.stem.upper() for p in BACKTEST_PRICES_DIR.glob("*.parquet")))
    pool.extend(sorted(p.stem.upper() for p in DEEP_PRICES_DIR.glob("*.parquet")))
    pool.extend(list(_current_universe_tickers())[:DEFAULT_CANDIDATE_POOL_CAP])
    pool.extend(BENCHMARKS)
    return tuple(t for t in dict.fromkeys(pool) if t)


def _universe_stats_fast(ticker: str, ts: pd.Timestamp) -> Optional[tuple[float, float, int]]:
    """(price, avg_dvol20, bars) from the feature table without building the
    full feature dict. Returns None exactly when compute_features_asof would."""
    table = _ticker_feature_table(ticker)
    if table is None:
        f = compute_features_asof_legacy(ticker, ts)
        if not f:
            return None
        return float(f.get("price") or 0.0), float(f.get("avg_dvol20") or 0.0), int(f["bars"])
    pos = _table_pos_asof(table, ts)
    if pos is None:
        return None
    bars = int(table["count"].values[pos])
    if bars < 20:
        return None
    price = float(table["price"].values[pos])
    if not math.isfinite(price) or price <= 0:
        return None
    avg_dvol = table["avg_dvol20"].values[pos]
    avg_dvol = 0.0 if pd.isna(avg_dvol) else float(avg_dvol)
    return price, avg_dvol, bars


def build_universe_asof(
    asof: Any,
    mode: str = "research_core",
    *,
    cap: int = DEFAULT_UNIVERSE_CAP,
    min_price: float = 5.0,
    min_avg_dvol: float = 5_000_000.0,
    min_bars: int = 75,
) -> List[Dict[str, Any]]:
    """Build a bounded liquid universe using only bars available as-of date.

    The candidate pool is determined by retained cache membership. If mode uses
    the current universe snapshot, the universe reliability is explicitly marked
    as CURRENT_METADATA_APPROXIMATION.
    """
    rows: List[Dict[str, Any]] = []
    pool = _candidate_pool(mode)
    reliability = (
        CURRENT_METADATA_APPROXIMATION
        if mode in {"current_universe", "research_core"}
        else RECONSTRUCTED_FROM_PRICE_ONLY
    )
    ts = _as_ts(asof)
    asof_str = str(ts.date())
    for t in pool:
        if not t or t in {"BRK.B", "BF.B"}:
            continue
        if FAST_FEATURES:
            stats = _universe_stats_fast(t, ts)
            if stats is None:
                continue
            price, avg_dvol, bars = stats
            if bars < min_bars:
                continue
            if price < min_price or avg_dvol < min_avg_dvol:
                continue
            meta = metadata_for_ticker(t)
            sector, theme = meta["sector"], meta["theme"]
        else:
            f = compute_features_asof(t, asof)
            if not f:
                continue
            if f["bars"] < min_bars:
                continue
            avg_dvol = f.get("avg_dvol20") or 0.0
            price = f.get("price") or 0.0
            bars = f["bars"]
            if price < min_price or avg_dvol < min_avg_dvol:
                continue
            sector, theme = f.get("sector"), f.get("theme")
        rows.append({
            "ticker": t,
            "asof": asof_str,
            "price": price,
            "avg_dvol20": avg_dvol,
            "bars": bars,
            "sector": sector,
            "theme": theme,
            "universe_mode": mode,
            "universe_reliability": reliability,
        })
    rows.sort(key=lambda r: (r["avg_dvol20"], r["bars"]), reverse=True)
    return rows[:max(1, int(cap))]


def compute_universe_features_asof(
    asof: Any,
    mode: str = "research_core",
    *,
    cap: int = DEFAULT_UNIVERSE_CAP,
    min_bars: int = 75,
    min_avg_dvol: float = 5_000_000.0,
) -> List[Dict[str, Any]]:
    universe = build_universe_asof(
        asof, mode, cap=cap, min_bars=min_bars, min_avg_dvol=min_avg_dvol
    )
    rows = [compute_features_asof(row["ticker"], asof) for row in universe]
    features = [row for row in rows if row is not None]
    srs = _sector_rs(features)
    for row in features:
        row["sector_rs20"] = srs.get(row["ticker"])
    return features


def get_forward_window(ticker: str, asof: Any, horizon: int) -> pd.DataFrame:
    """Return bars strictly after the signal as-of bar for outcome simulation."""
    t = _norm_ticker(ticker)
    full = _full_frame(t)
    if full is None:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    ts = _as_ts(asof)
    past = full[full.index <= ts]
    if past.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    anchor = past.index[-1]
    future = full[full.index > anchor].head(max(0, int(horizon)) + 1).copy()
    future.attrs["ticker"] = t
    future.attrs["signal_asof"] = str(ts.date())
    future.attrs["anchor_bar"] = str(anchor.date())
    future.attrs["price_reliability"] = TRUE_POINT_IN_TIME
    return future


def validate_no_future_bars(frame: Optional[pd.DataFrame] = None, asof: Any = None) -> Dict[str, Any]:
    """Validate that a feature frame contains no bars after asof.

    When called without a frame, performs a small cache smoke check against SPY.
    """
    if frame is None:
        spy = load_price_frame_asof("SPY", pd.Timestamp.utcnow().date())
        if spy is None:
            return {"ok": False, "reason": "SPY price cache missing"}
        return {"ok": True, "checked": "SPY", "max_bar": str(spy.index.max().date())}
    if asof is None:
        raise ValueError("asof is required when frame is provided")
    if frame.empty:
        return {"ok": True, "max_bar": None, "asof": str(_as_ts(asof).date())}
    max_bar = frame.index.max()
    ok = bool(max_bar <= _as_ts(asof))
    if not ok:
        raise AssertionError(f"future bar detected: max_bar={max_bar.date()} asof={_as_ts(asof).date()}")
    return {"ok": True, "max_bar": str(max_bar.date()), "asof": str(_as_ts(asof).date())}


def benchmark_calendar() -> pd.DatetimeIndex:
    """Return a merged SPY calendar from retained local bars."""
    spy = _full_frame("SPY")
    if spy is None or spy.empty:
        # Fallback to scanner_truth if the merged cache is absent.
        return dataio.benchmark_calendar()
    return pd.DatetimeIndex(spy.index.sort_values().unique())


def trading_dates_between(start: Any, end: Any) -> List[pd.Timestamp]:
    cal = benchmark_calendar()
    s = _as_ts(start)
    e = _as_ts(end)
    return [pd.Timestamp(x).normalize() for x in cal[(cal >= s) & (cal <= e)]]


def cache_coverage() -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for label, directory in (
        ("cache/prices", SHALLOW_PRICES_DIR),
        ("cache/prices_deep", DEEP_PRICES_DIR),
        ("cache/backtest_prices", BACKTEST_PRICES_DIR),
    ):
        files = list(directory.glob("*.parquet")) if directory.exists() else []
        mins: List[pd.Timestamp] = []
        maxs: List[pd.Timestamp] = []
        rows: List[int] = []
        for path in files:
            df = _read_price_path(path)
            if df is None or df.empty:
                continue
            mins.append(df.index.min())
            maxs.append(df.index.max())
            rows.append(len(df))
        out[label] = {
            "files": len(files),
            "min_date": str(min(mins).date()) if mins else None,
            "max_date": str(max(maxs).date()) if maxs else None,
            "median_rows": int(np.median(rows)) if rows else 0,
            "max_rows": max(rows) if rows else 0,
        }
    return out


def clear_caches_for_tests() -> None:
    _load_merged_prices.cache_clear()
    _all_price_tickers.cache_clear()
    _current_universe_tickers.cache_clear()
    _candidate_pool.cache_clear()
    _clear_feature_caches()
    load_profiles_current.cache_clear()

#!/usr/bin/env python3
"""
research/research_candidate_enrichment.py — Central enrichment for research scanner candidates.

Phase 4A.3: Ensures every candidate from every scanner category has all
required technical, liquidity, and scoring fields populated before quality
gating. Works as a post-deduplication pass in build_scanner().

Rules:
  - Never fabricates unavailable data
  - Uses deep price cache (cache/prices_deep/) for MA200+ when available
  - Falls back to regular cache (cache/prices/)
  - Marks missing_fields accurately
  - Never weakens quality gates
  - Research-only; no trade recommendations

Quarantine sub-types (for display/breakdown; do not change priority_label cascade):
  INVALID_PRIORITY       — bad ticker or no price data at all
  INSUFFICIENT_HISTORY   — valid ticker, not enough bars for full scoring
  LOW_LIQUIDITY          — avg dollar volume below minimum threshold
  DATA_INCOMPLETE        — has some required fields but missing others
  DATA_QUARANTINE        — critical scoring fields missing (fallthrough)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("research_candidate_enrichment")

# ── Quarantine sub-type constants ─────────────────────────────────────────────
QUARANTINE_INVALID = "INVALID_PRIORITY"
QUARANTINE_INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
QUARANTINE_LOW_LIQUIDITY = "LOW_LIQUIDITY"
QUARANTINE_DATA_INCOMPLETE = "DATA_INCOMPLETE"
QUARANTINE_DATA_QUARANTINE = "DATA_QUARANTINE"

QUARANTINE_SUBTYPES = (
    QUARANTINE_INVALID,
    QUARANTINE_INSUFFICIENT_HISTORY,
    QUARANTINE_LOW_LIQUIDITY,
    QUARANTINE_DATA_INCOMPLETE,
    QUARANTINE_DATA_QUARANTINE,
)

# ── Thresholds ────────────────────────────────────────────────────────────────
LIQUIDITY_MIN_DOLLAR_VOLUME = 1_000_000   # $1M/day avg

MIN_BARS_VALID = 10
MIN_BARS_MA20 = 20
MIN_BARS_MA50 = 50
MIN_BARS_MA200 = 200
MIN_BARS_RS20 = 21
MIN_BARS_RS63 = 64
MIN_BARS_VOL_TREND = 30
MIN_BARS_60D_HIGH = 60

# ── Price utilities (self-contained; mirrors research_scanner.py helpers) ─────


def _load_frame(ticker: str, price_dir: Path):
    """Load parquet; return DataFrame or None."""
    path = price_dir / f"{ticker.upper()}.parquet"
    if not path.exists():
        return None
    try:
        import pandas as pd
        df = pd.read_parquet(path)
        if df is None or df.empty:
            return None
        if "close" not in df.columns and "Close" in df.columns:
            df = df.rename(columns={"Close": "close"})
        return df
    except Exception:
        return None


def _merge_frames(df_deep, df_reg):
    """
    Date-merge deep (historical) and shallow (recent) price DataFrames.

    Deep cache has more bars but may end weeks before the shallow cache.
    Shallow cache has fewer bars but is refreshed more recently.
    Merging gives the full historical depth PLUS the most recent bars,
    so RS/momentum calculations use the latest available data.

    On overlapping dates, shallow wins (more recently fetched from provider).
    Falls back to the longer series if merge fails.
    """
    import pandas as pd
    if df_deep is None and df_reg is None:
        return None
    if df_deep is None:
        return df_reg
    if df_reg is None:
        return df_deep
    try:
        combined = pd.concat([df_deep, df_reg], axis=0)
        # Keep shallow on overlap (appended last → keep='last')
        combined = combined[~combined.index.duplicated(keep="last")]
        return combined.sort_index()
    except Exception:
        return df_deep if len(df_deep) >= len(df_reg) else df_reg


def _last_bar_date(df) -> Optional[str]:
    """Return the last index entry as an ISO string, or None."""
    if df is None:
        return None
    try:
        last = df.index[-1]
        return last.isoformat() if hasattr(last, "isoformat") else str(last)
    except Exception:
        return None


def _closes(df) -> List[float]:
    if df is None:
        return []
    col = "close" if "close" in df.columns else ("Close" if "Close" in df.columns else None)
    if col is None:
        return []
    return [float(v) for v in df[col].dropna().tolist()]


def _volumes(df) -> List[float]:
    if df is None:
        return []
    col = next((c for c in ["volume", "Volume"] if c in df.columns), None)
    if col is None:
        return []
    return [float(v) for v in df[col].dropna().tolist()]


def _ma(s: List[float], w: int) -> Optional[float]:
    if len(s) < w:
        return None
    return sum(s[-w:]) / w


def _ret(s: List[float], lb: int) -> Optional[float]:
    if len(s) < lb + 1:
        return None
    return round((s[-1] / s[-(lb + 1)] - 1.0) * 100.0, 2)


def _fill_if_none(d: Dict[str, Any], key: str, value: Any) -> None:
    """Set d[key]=value only when the key is absent or its current value is None.

    Differs from setdefault: setdefault only fires when the key is missing entirely.
    Scanners may emit key=None meaning "couldn't compute with available bars"; this
    helper allows the central enrichment to overwrite those None slots when the deep
    price cache provides enough bars, while still preserving any real computed value
    (True/False/float) that a scanner already set.
    """
    if d.get(key) is None:
        d[key] = value


def _rs_vs_spy(closes: List[float], spy: List[float], lb: int) -> Optional[float]:
    if len(closes) < lb + 1 or len(spy) < lb + 1:
        return None
    r_stock = closes[-1] / closes[-(lb + 1)] - 1.0
    r_spy = spy[-1] / spy[-(lb + 1)] - 1.0
    return round((r_stock - r_spy) * 100.0, 2)


def _vol_trend(vols: List[float], short: int = 10, long: int = 30) -> Optional[float]:
    if len(vols) < long:
        return None
    avg_s = sum(vols[-short:]) / short
    avg_l = sum(vols[-long:]) / long
    if avg_l <= 0:
        return None
    return round(avg_s / avg_l, 3)


def _drawdown_from_high(s: List[float], window: int = 252) -> Optional[float]:
    if not s or len(s) < 5:
        return None
    recent = s[-window:] if len(s) >= window else s
    hi = max(recent)
    if hi <= 0:
        return None
    return round((s[-1] / hi - 1.0) * 100.0, 2)


def _parquet_mtime(ticker: str, price_dir: Path) -> Optional[str]:
    path = price_dir / f"{ticker.upper()}.parquet"
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def _extension_state(ext_pct: Optional[float]) -> str:
    from research.research_scoring import EXT_NORMAL, EXT_STRETCHED, EXT_EXTENDED, EXT_PARABOLIC
    if ext_pct is None:
        return EXT_NORMAL
    if ext_pct > 20:
        return EXT_PARABOLIC
    if ext_pct > 15:
        return EXT_EXTENDED
    if ext_pct > 8:
        return EXT_STRETCHED
    return EXT_NORMAL


def _market_cap_bucket(mc) -> Optional[str]:
    if mc is None:
        return None
    mc = float(mc)
    if mc >= 200_000_000_000:
        return "MEGA"
    if mc >= 10_000_000_000:
        return "LARGE"
    if mc >= 2_000_000_000:
        return "MID"
    if mc >= 300_000_000:
        return "SMALL"
    return "MICRO"


# ── Central enrichment ────────────────────────────────────────────────────────


def enrich_research_candidate(
    ticker: str,
    base_item: Dict[str, Any],
    price_dir: Path,
    spy_closes: List[float],
    profile: Optional[Dict[str, Any]] = None,
    deep_price_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Populate all required technical, liquidity, and metadata fields for a
    research scanner candidate.

    Preference order for price data:
      1. Deep cache (deep_price_dir) — for MA200 and long-lookback fields
      2. Regular cache (price_dir)

    Never fabricates missing data. Sets missing_fields to explain gaps.
    Returns a copy of base_item with all computable fields filled in.
    """
    item = dict(base_item)
    sym = ticker.upper()

    # ── Load price data ────────────────────────────────────────────────────────
    df_deep = _load_frame(sym, deep_price_dir) if deep_price_dir else None
    df_reg = _load_frame(sym, price_dir)

    # Date-merge: deep gives historical depth, shallow gives recent bars.
    # Taking the longer series (old behavior) causes RS misalignment when
    # deep cache ends weeks before the shallow cache that nightly refreshes.
    df_merged = _merge_frames(df_deep, df_reg)
    closes = _closes(df_merged)
    vols = _volumes(df_merged)
    n_bars = len(closes)

    item["bars_available"] = n_bars

    # ── Validity gate ─────────────────────────────────────────────────────────
    if n_bars < MIN_BARS_VALID:
        item["ticker_valid"] = False
        item["missing_fields"] = ["price_history"]
        item["data_confidence"] = "INVALID"
        return item

    item.setdefault("ticker_valid", True)
    last = closes[-1]
    item["latest_close"] = round(last, 4)
    # Use the actual last bar date from the merged series (more accurate than file mtime)
    item["latest_price_date"] = (
        _last_bar_date(df_merged)
        or _parquet_mtime(sym, price_dir if df_reg is not None else (deep_price_dir or price_dir))
    )

    # ── MA20 / above_ma20 / extension_vs_ma20 ────────────────────────────────
    ma20 = _ma(closes, MIN_BARS_MA20)
    _fill_if_none(item, "ma20", round(ma20, 4) if ma20 else None)
    _fill_if_none(item, "above_ma20", bool(last > ma20) if ma20 else None)
    ext_ma20 = round((last / ma20 - 1.0) * 100.0, 2) if (ma20 and ma20 > 0) else None
    _fill_if_none(item, "extension_vs_ma20_pct", ext_ma20)

    # ── MA50 / above_ma50 ─────────────────────────────────────────────────────
    ma50 = _ma(closes, MIN_BARS_MA50)
    _fill_if_none(item, "ma50", round(ma50, 4) if ma50 else None)
    _fill_if_none(item, "above_ma50", bool(last > ma50) if ma50 else None)

    # ── MA200 / above_ma200 / extension_vs_ma200 ─────────────────────────────
    # Use _fill_if_none (not setdefault) so scanner-set None values are overridden
    # when the deep price cache has enough bars to compute a real value.
    if n_bars >= MIN_BARS_MA200:
        ma200 = _ma(closes, MIN_BARS_MA200)
        _fill_if_none(item, "ma200", round(ma200, 4) if ma200 else None)
        _fill_if_none(item, "above_ma200", bool(last > ma200) if ma200 else None)
        ext_ma200 = round((last / ma200 - 1.0) * 100.0, 2) if (ma200 and ma200 > 0) else None
        _fill_if_none(item, "extension_vs_ma200_pct", ext_ma200)
        item["insufficient_history_for_ma200"] = False
    else:
        item.setdefault("ma200", None)
        item.setdefault("above_ma200", None)
        item.setdefault("extension_vs_ma200_pct", None)
        item["insufficient_history_for_ma200"] = True

    # ── Extension state ───────────────────────────────────────────────────────
    ext_pct = item.get("extension_vs_ma200_pct") or item.get("extension_vs_ma20_pct")
    item["extension_state"] = _extension_state(ext_pct)

    # ── Returns ───────────────────────────────────────────────────────────────
    _fill_if_none(item, "return_5d", _ret(closes, 5))
    _fill_if_none(item, "return_20d", _ret(closes, 20))

    # ── 60d high distance ─────────────────────────────────────────────────────
    if n_bars >= MIN_BARS_60D_HIGH:
        high_60d = max(closes[-MIN_BARS_60D_HIGH:])
        dist = round((last / high_60d - 1.0) * 100.0, 2) if high_60d > 0 else None
        item.setdefault("distance_from_60d_high_pct", dist)

    # ── Drawdown from high ────────────────────────────────────────────────────
    _fill_if_none(item, "dd_from_high_pct", _drawdown_from_high(closes))

    # ── Volume metrics ────────────────────────────────────────────────────────
    _fill_if_none(item, "vol_trend_ratio", _vol_trend(vols))
    if len(vols) >= MIN_BARS_MA20:
        avg_vol_20d = sum(vols[-MIN_BARS_MA20:]) / MIN_BARS_MA20
        item.setdefault("volume_avg_20d", round(avg_vol_20d, 0))
        avg_dv = round(avg_vol_20d * last, 0)
        item.setdefault("avg_dollar_volume", avg_dv)
        item.setdefault("liquidity_ok", avg_dv >= LIQUIDITY_MIN_DOLLAR_VOLUME)
    else:
        item.setdefault("liquidity_ok", None)

    # ── RS vs SPY ─────────────────────────────────────────────────────────────
    _fill_if_none(item, "rs_20d_vs_spy", _rs_vs_spy(closes, spy_closes, 20))
    _fill_if_none(item, "rs_63d_vs_spy", _rs_vs_spy(closes, spy_closes, 63))

    # ── Profile metadata ──────────────────────────────────────────────────────
    if profile:
        item.setdefault("company_name", profile.get("companyName") or profile.get("name"))
        item.setdefault("sector", profile.get("sector"))
        item.setdefault("industry", profile.get("industry"))
        mc = profile.get("mktCap") or profile.get("marketCap")
        if mc:
            item["market_cap"] = mc
            item["market_cap_bucket"] = _market_cap_bucket(mc)
    else:
        item.setdefault("company_name", None)
        item.setdefault("sector", None)
        item.setdefault("industry", None)

    # ── Data confidence (based on bar count) ──────────────────────────────────
    if "data_confidence" not in item or item.get("data_confidence") is None:
        if n_bars >= MIN_BARS_MA200:
            conf = "HIGH"
        elif n_bars >= MIN_BARS_MA50:
            conf = "MEDIUM"
        elif n_bars >= MIN_BARS_VALID:
            conf = "LOW"
        else:
            conf = "INVALID"
        item["data_confidence"] = conf

    # ── Missing fields list ───────────────────────────────────────────────────
    item["missing_fields"] = _compute_missing_fields(item, n_bars)

    return item


def _compute_missing_fields(item: Dict[str, Any], n_bars: int) -> List[str]:
    """List fields that are genuinely unavailable (not just un-computed)."""
    missing: List[str] = []

    # Critical for earliness_detail:
    if item.get("above_ma50") is None:
        if n_bars >= MIN_BARS_MA50:
            missing.append("above_ma50_gap")   # should have been computed
        else:
            missing.append("above_ma50")        # genuine: too few bars
    if item.get("above_ma200") is None:
        if n_bars >= MIN_BARS_MA200:
            missing.append("above_ma200_gap")
        else:
            missing.append("above_ma200")
    if item.get("rs_63d_vs_spy") is None:
        if n_bars >= MIN_BARS_RS63:
            missing.append("rs_63d_no_spy")   # could compute if SPY available
        else:
            missing.append("rs_63d_vs_spy")
    if item.get("rs_20d_vs_spy") is None:
        if n_bars >= MIN_BARS_RS20:
            missing.append("rs_20d_no_spy")
        else:
            missing.append("rs_20d_vs_spy")
    if item.get("vol_trend_ratio") is None:
        missing.append("vol_trend_ratio")
    if item.get("dd_from_high_pct") is None:
        missing.append("dd_from_high_pct")
    if item.get("extension_vs_ma200_pct") is None and n_bars >= MIN_BARS_MA200:
        missing.append("extension_vs_ma200_gap")

    return missing


def classify_quarantine_subtype(item: Dict[str, Any]) -> str:
    """
    Classify WHY a candidate is in DATA_QUARANTINE.

    Returns one of the QUARANTINE_* constants.
    Used for display/breakdown only; does not change the priority_label cascade.
    """
    if item.get("ticker_valid") is False or item.get("data_confidence") == "INVALID":
        return QUARANTINE_INVALID

    n_bars = item.get("bars_available", 0)
    if n_bars < MIN_BARS_MA200 and item.get("insufficient_history_for_ma200", False):
        return QUARANTINE_INSUFFICIENT_HISTORY

    liq = item.get("liquidity_ok")
    if liq is False:
        avg_dv = item.get("avg_dollar_volume", 0)
        if avg_dv is not None and avg_dv < LIQUIDITY_MIN_DOLLAR_VOLUME:
            return QUARANTINE_LOW_LIQUIDITY

    # Has some fields but not all required for high-priority scoring
    missing = item.get("missing_fields", [])
    if missing and n_bars >= MIN_BARS_MA50:
        return QUARANTINE_DATA_INCOMPLETE

    return QUARANTINE_DATA_QUARANTINE

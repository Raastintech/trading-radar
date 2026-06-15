"""
research/scanner_truth/filters.py — faithful point-in-time re-computation of
the funnel's price-derived gates.

These constants MIRROR the live scanner/universe/Alpha thresholds. They are
copied here (rather than imported) so the autopsy runs cache-only with no
creds, since the live modules import ``core.config`` which requires broker
keys at import time. Provenance is cited per constant; a drift-guard test
(tests/unit/test_scanner_truth.py::test_mirrored_constants_match_live) imports
the real modules under the test stubs and asserts these stay in sync.

Every gate here is a PURE function of bars at-or-before the as-of index — no
look-ahead. A winner that fails ``voyager_structural`` as-of its move-start
date was genuinely un-buyable by Voyager's rules at that time; we never use
post-date bars to justify an earlier pass/fail.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

# ── Mirrored constants (provenance in comments) ──────────────────────────────

# core/universe.py — Stage 4 base liquidity gate
UNIV_MIN_PRICE = 5.0          # _MIN_PRICE
UNIV_MAX_PRICE = 1000.0       # _MAX_PRICE
UNIV_MIN_AVG_VOL = 300_000.0  # _MIN_AVG_VOLUME
UNIV_MIN_AVG_DVOL = 5_000_000.0  # _MIN_AVG_DOLLAR_VOLUME
UNIV_BASE_LIMIT = 1000        # _BASE_LIMIT (top-N by liquidity seed)

# strategies/voyager.py
VOY_MIN_PRICE = 5.0           # MIN_PRICE
VOY_MIN_AVG_DVOL = 5_000_000.0  # MIN_AVG_DOLLAR_VOL
VOY_MAX_EXTENSION_MA50 = 0.12   # MAX_EXTENSION_MA50 — price must not be >12% above MA50
VOY_MA200_FLOOR = 0.92          # MA200_FLOOR — price must not be <8% below MA200
VOY_RS_50_WINDOW = 50           # RS_50_WINDOW
VOY_RS_130_WINDOW = 130         # RS_130_WINDOW
VOY_DVOL_TREND_RATIO = 0.85     # DVOL_TREND_RATIO
VOY_BARS_NEEDED = 260           # BARS_NEEDED

# strategies/sniper.py
SNI_VOL_SPIKE_THRESH = 1.4      # VOL_SPIKE_THRESH
SNI_BARS_NEEDED = 75            # BARS_NEEDED
SNI_MA50_SLOPE_BARS = 20        # MA50_SLOPE_BARS
SNI_ATR_CONTRACTION_THRESH = 0.85  # ATR_CONTRACTION_THRESH
SNI_BREAKOUT_LOOKBACK = 20      # close > max high of prior 20 bars (excl today)
SNI_VIX_REGIME_CEILING = 28.0   # VIX_REGIME_CEILING

# core/alpha_discovery.py — UNIVERSE_DEFINITION
ALPHA_MCAP_FLOOR = 300_000_000.0     # market_cap_floor
ALPHA_MCAP_CEILING = 80_000_000_000.0  # market_cap_ceiling
ALPHA_BOARD_CAP = 25                 # candidate_band[:25] / rotation_top[:25] enrichment+board cap


# ── Pure technical primitives (no look-ahead by construction) ────────────────

def sma(close: pd.Series, n: int) -> pd.Series:
    return close.rolling(n, min_periods=n).mean()


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    prev_c = c.shift(1)
    tr = pd.concat([(h - l), (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0).rolling(n, min_periods=n).mean()
    down = (-delta.clip(upper=0)).rolling(n, min_periods=n).mean()
    rs = up / down.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def relative_strength(ticker_close: pd.Series, bench_close: pd.Series, window: int) -> Optional[float]:
    """Ratio of ticker return to benchmark return over the trailing window,
    as-of the last point of both series (already aligned/sliced by caller).
    Returns ticker_ret - bench_ret (excess return); positive ⇒ outperforming."""
    if len(ticker_close) <= window or len(bench_close) <= window:
        return None
    t0, t1 = ticker_close.iloc[-window - 1], ticker_close.iloc[-1]
    b0, b1 = bench_close.iloc[-window - 1], bench_close.iloc[-1]
    if not (t0 and b0) or t0 <= 0 or b0 <= 0:
        return None
    return float((t1 / t0 - 1.0) - (b1 / b0 - 1.0))


# ── Gate results ─────────────────────────────────────────────────────────────

@dataclass
class GateResult:
    passed: bool
    reasons: List[str] = field(default_factory=list)   # failing-gate codes
    metrics: Dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> Dict:
        return {"passed": self.passed, "reasons": self.reasons, "metrics": self.metrics}


def _window_to(df: pd.DataFrame, asof: pd.Timestamp) -> pd.DataFrame:
    """Bars at-or-before ``asof`` — the only data a point-in-time scan could use."""
    return df[df.index <= asof]


def liquidity_gate(df: pd.DataFrame, asof: pd.Timestamp) -> GateResult:
    """Mirror of core/universe.py Stage-4 base liquidity gate, as-of ``asof``."""
    d = _window_to(df, asof)
    if len(d) < 20:
        return GateResult(False, ["insufficient_bars"], {"bars": float(len(d))})
    price = float(d["close"].iloc[-1])
    avg_vol = float(d["volume"].tail(20).mean())
    avg_dvol = float((d["close"] * d["volume"]).tail(20).mean())
    reasons: List[str] = []
    if price < UNIV_MIN_PRICE:
        reasons.append("price_below_min")
    if price > UNIV_MAX_PRICE:
        reasons.append("price_above_max")
    if avg_vol < UNIV_MIN_AVG_VOL:
        reasons.append("avg_volume_below_min")
    if avg_dvol < UNIV_MIN_AVG_DVOL:
        reasons.append("avg_dvol_below_min")
    return GateResult(
        not reasons, reasons,
        {"price": price, "avg_vol_20": avg_vol, "avg_dvol_20": avg_dvol},
    )


def voyager_structural(df: pd.DataFrame, asof: pd.Timestamp) -> GateResult:
    """Mirror of strategies/voyager.py structural price gates, as-of ``asof``.
    Does NOT reproduce fundamental-score or earnings gates (those need
    point-in-time fundamentals/earnings that are not historized) — those are
    reported separately as NOT_RETAINED. Captures the price-structure gates
    that are pure functions of bars: history depth, MA200 floor, MA50
    over-extension, dollar-vol trend."""
    d = _window_to(df, asof)
    reasons: List[str] = []
    metrics: Dict[str, float] = {"bars": float(len(d))}
    if len(d) < VOY_BARS_NEEDED:
        return GateResult(False, ["insufficient_history_260"], metrics)
    close = d["close"]
    price = float(close.iloc[-1])
    ma50 = float(sma(close, 50).iloc[-1])
    ma200 = float(sma(close, 200).iloc[-1])
    ext_ma50 = (price - ma50) / ma50 if ma50 else np.nan
    dist_ma200 = price / ma200 if ma200 else np.nan
    dvol = (d["close"] * d["volume"])
    dvol20 = float(dvol.tail(20).mean())
    dvol60 = float(dvol.tail(60).mean())
    dvol_ratio = dvol20 / dvol60 if dvol60 else np.nan
    metrics.update({
        "price": price, "ma50": ma50, "ma200": ma200,
        "ext_above_ma50": float(ext_ma50), "dist_ma200_ratio": float(dist_ma200),
        "dvol_trend_ratio": float(dvol_ratio),
    })
    if price < VOY_MIN_PRICE:
        reasons.append("price_below_min")
    if dist_ma200 < VOY_MA200_FLOOR:
        reasons.append("below_ma200_floor")
    if ext_ma50 > VOY_MAX_EXTENSION_MA50:
        reasons.append("too_extended")           # >12% above MA50
    if dvol_ratio < VOY_DVOL_TREND_RATIO:
        reasons.append("dvol_fading")
    return GateResult(not reasons, reasons, metrics)


def sniper_breakout(df: pd.DataFrame, asof: pd.Timestamp) -> GateResult:
    """Mirror of strategies/sniper.py breakout structure, as-of ``asof``:
    breakout bar (close > max high of prior 20, excl today), volume ≥ 1.4×
    20d avg, prior ATR contraction < 0.85, MA50 rising over 20 bars."""
    d = _window_to(df, asof)
    reasons: List[str] = []
    metrics: Dict[str, float] = {"bars": float(len(d))}
    if len(d) < SNI_BARS_NEEDED:
        return GateResult(False, ["insufficient_history_75"], metrics)
    close, high, vol = d["close"], d["high"], d["volume"]
    prior_high = float(high.iloc[-(SNI_BREAKOUT_LOOKBACK + 1):-1].max())
    today_close = float(close.iloc[-1])
    vol_ratio = float(vol.iloc[-1] / vol.tail(20).mean()) if vol.tail(20).mean() else np.nan
    a = atr(d, 14)
    recent5 = float(a.tail(5).mean())
    prior15 = float(a.iloc[-20:-5].mean()) if len(a) >= 20 else np.nan
    atr_contraction = recent5 / prior15 if prior15 else np.nan
    ma50_now = float(sma(close, 50).iloc[-1])
    ma50_prev = float(sma(close, 50).iloc[-(SNI_MA50_SLOPE_BARS + 1)])
    metrics.update({
        "today_close": today_close, "prior_20d_high": prior_high,
        "vol_ratio": vol_ratio, "atr_contraction": float(atr_contraction),
        "ma50_now": ma50_now, "ma50_20ago": ma50_prev,
    })
    if today_close <= prior_high:
        reasons.append("no_breakout")
    if not (vol_ratio >= SNI_VOL_SPIKE_THRESH):
        reasons.append("volume_insufficient")
    if not (atr_contraction < SNI_ATR_CONTRACTION_THRESH):
        reasons.append("no_atr_contraction")
    if not (ma50_now > ma50_prev):
        reasons.append("ma50_not_rising")
    return GateResult(not reasons, reasons, metrics)


def alpha_market_cap_eligible(market_cap: Optional[float]) -> GateResult:
    """Mirror of core/alpha_discovery.py UNIVERSE_DEFINITION cap band."""
    if market_cap is None or market_cap <= 0:
        return GateResult(False, ["market_cap_unknown"], {})
    reasons: List[str] = []
    if market_cap < ALPHA_MCAP_FLOOR:
        reasons.append("below_mcap_floor_300M")
    if market_cap > ALPHA_MCAP_CEILING:
        reasons.append("above_mcap_ceiling_80B")
    return GateResult(not reasons, reasons, {"market_cap": float(market_cap)})

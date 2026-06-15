#!/usr/bin/env python3
"""
research/strategy_research_lab.py - Phase 1H Strategy Research Lab.

Research-only, cache-only strategy backtest and comparison engine. It never
imports execution, broker, paper-signal, governance, or live-capital modules.
All variants are pure research functions over no-lookahead price features.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
import sys
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from research import strategy_lab_data as d
from research import strategy_lab_regime as regime

ROOT = Path(__file__).resolve().parents[1]
OUT_JSON = ROOT / "cache" / "research" / "strategy_research_lab_latest.json"
OUT_TXT = ROOT / "logs" / "strategy_research_lab_latest.txt"
OUT_DOC = ROOT / "docs" / "research" / "STRATEGY_RESEARCH_LAB_RESULTS.md"

# Phase 1H.1 exact-mode artifact copies (the canonical latest is always written too).
MODE_OUTPUTS: Dict[str, Dict[str, Optional[Path]]] = {
    "exact_recent_60": {
        "json": ROOT / "cache" / "research" / "strategy_lab_exact_recent60_latest.json",
        "txt": ROOT / "logs" / "strategy_lab_exact_recent60_latest.txt",
        "doc": None,
    },
    "exact_ytd": {
        "json": ROOT / "cache" / "research" / "strategy_lab_exact_2026ytd_latest.json",
        "txt": ROOT / "logs" / "strategy_lab_exact_2026ytd_latest.txt",
        "doc": ROOT / "docs" / "research" / "STRATEGY_LAB_2026YTD_RESULTS.md",
    },
    "exact_full": {
        "json": ROOT / "cache" / "research" / "strategy_lab_full_windows_latest.json",
        "txt": ROOT / "logs" / "strategy_lab_full_windows_latest.txt",
        "doc": ROOT / "docs" / "research" / "STRATEGY_LAB_FULL_WINDOW_RESULTS.md",
    },
}

RUN_SCOPES = {
    "quick": "QUICK_RECENT_60_SAMPLED",
    "exact_recent_60": "EXACT_RECENT_60",
    "exact_ytd": "EXACT_2026_YTD",
    "exact_full": "EXACT_FULL_WINDOWS",
    "default": "DEFAULT_WINDOWS",
}

VERSION = "STRATEGY_RESEARCH_LAB_V2"
RESEARCH_DISCLAIMER = (
    "RESEARCH_ONLY: no broker orders, no paper signals, no trade proposals, "
    "no registry/governance/execution/live-capital changes."
)

VERDICT_REJECT = "REJECT"
VERDICT_NEED_MORE = "NEED_MORE_DATA"
VERDICT_OVERFIT_RISK = "PROMISING_BUT_OVERFIT_RISK"
VERDICT_EDGE = "BACKTEST_EDGE_DETECTED"
VERDICT_READY = "READY_FOR_PAPER_SHADOW_PROPOSAL"

DEFAULT_VARIANTS = (
    "PROD_SNIPER_CURRENT",
    "SNIPER_NO_ATR_CONTRACTION",
    "PROD_VOYAGER_CURRENT",
    "CORRECTION_LEADER_RECLAIM",
    "RECALL_SHADOW_RS_MOMENTUM",
    "RECALL_SHADOW_PULLBACK",
    "POWER_TREND_EXTENSION",
    "QQQ_TECH_TACTICAL_SHORT",
    "SIMPLE_SECTOR_RS",
    "SIMPLE_MOM_20_60",
    "RANDOM_LIQUID",
)

BENCHMARK_VARIANTS = ("SPY_BUY_HOLD", "QQQ_BUY_HOLD", "CASH")

NO_COST = {"name": "no_cost", "slippage_bps": 0.0, "spread_bps": 0.0, "commission_bps": 0.0, "short_borrow_annual_pct": 0.0}
BASE_COST = {"name": "base_cost", "slippage_bps": 10.0, "spread_bps": 5.0, "commission_bps": 0.0, "short_borrow_annual_pct": 2.0}
HIGH_COST = {"name": "high_cost", "slippage_bps": 25.0, "spread_bps": 15.0, "commission_bps": 0.0, "short_borrow_annual_pct": 6.0}
COST_MODELS = (NO_COST, BASE_COST, HIGH_COST)


@dataclass(frozen=True)
class StrategyParams:
    sector_rs_threshold: float = 0.08
    momentum_20_threshold: float = 0.08
    momentum_60_threshold: float = 0.15
    extension_cap: float = 0.25
    pullback_depth: float = 0.12
    atr_contraction_enabled: bool = True
    volume_expansion_threshold: float = 1.0
    stop_loss_pct: float = 0.06
    profit_target_pct: float = 0.10
    max_hold_days: int = 10
    min_avg_dvol: float = 5_000_000.0
    trailing_stop_pct: Optional[float] = None
    correction_rs_lookback: int = 20
    correction_max_pullback: float = 0.25
    correction_ema_reclaim: int = 20
    correction_volume_dryup_threshold: float = 0.90
    correction_volume_expansion_threshold: float = 1.05
    correction_atr_stop_multiple: float = 2.0
    correction_market_dd_threshold: float = 0.05

    def as_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class BacktestConfig:
    universe_mode: str = "research_core"
    universe_cap: int = 140
    max_signals_per_variant_day: int = 5
    entry_timing: str = "next_open"
    min_bars: int = 75
    random_seed: int = 20260612
    date_stride: int = 1

    def as_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class Signal:
    variant: str
    ticker: str
    asof: str
    side: str
    score: float
    reasons: List[str] = field(default_factory=list)
    features: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "variant": self.variant,
            "ticker": self.ticker,
            "asof": self.asof,
            "side": self.side,
            "score": round(float(self.score), 6),
            "reasons": list(self.reasons),
            "features": {
                "sector": self.features.get("sector"),
                "theme": self.features.get("theme"),
                "price": self.features.get("price"),
                "r20": self.features.get("r20"),
                "r60": self.features.get("r60"),
                "rs20_spy": self.features.get("rs20_spy"),
                "sector_rs20": self.features.get("sector_rs20"),
                "ext_ema20": self.features.get("ext_ema20"),
                "avg_dvol20": self.features.get("avg_dvol20"),
                "data_reliability": self.features.get("data_reliability"),
            },
        }


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        value = float(value)
        return value if math.isfinite(value) else default
    except Exception:
        return default


def _opt(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        value = float(value)
        return value if math.isfinite(value) else None
    except Exception:
        return None


def _pct(value: Any) -> str:
    return "n/a" if value is None else f"{float(value) * 100:+.2f}%"


def _is_power_theme(f: Dict[str, Any]) -> bool:
    return str(f.get("theme") or "") in d.POWER_THEMES


def _liquid(f: Dict[str, Any], params: StrategyParams) -> bool:
    return _f(f.get("avg_dvol20")) >= params.min_avg_dvol and _f(f.get("price")) >= 5.0


def _sniper_score(f: Dict[str, Any]) -> float:
    vol_ratio = _f(f.get("volume")) / max(_f(f.get("avg_vol20"), 1.0), 1.0)
    rs_positive = _f(f.get("rs10_spy")) > 0
    contraction = _f(f.get("atr_contraction"), 1.0)
    score = 50.0
    score += min(25.0, max(0.0, (vol_ratio - 1.4) * 40.0))
    if rs_positive:
        score += 15.0
    if contraction < 0.75:
        score += 10.0
    return max(0.0, min(100.0, score))


def signal_prod_sniper_current(f: Dict[str, Any], params: StrategyParams) -> Optional[Signal]:
    if f["ticker"] not in d.SNIPER_LARGE_CAP_UNIVERSE:
        return None
    if not _liquid(f, params):
        return None
    if f.get("bars", 0) < 75:
        return None
    reasons: List[str] = []
    if not f.get("first_breakout"):
        return None
    reasons.append("first_breakout")
    vol_ratio = _f(f.get("volume")) / max(_f(f.get("avg_vol20"), 1.0), 1.0)
    if vol_ratio < 1.4:
        return None
    reasons.append("volume_confirmed")
    atr_c = _opt(f.get("atr_contraction"))
    if atr_c is None or atr_c >= 0.85:
        return None
    reasons.append("atr_contraction")
    if f.get("above_ma50") is not True or f.get("ma50_rising") is not True:
        return None
    if _f(f.get("rs10_spy")) <= 0:
        return None
    if f.get("spy_above_ma200") is False:
        return None
    score = _sniper_score(f)
    if score < 70:
        return None
    return Signal("PROD_SNIPER_CURRENT", f["ticker"], f["asof"], "long", score, reasons, f)


def signal_sniper_no_atr(f: Dict[str, Any], params: StrategyParams) -> Optional[Signal]:
    if f["ticker"] not in d.SNIPER_LARGE_CAP_UNIVERSE:
        return None
    if not _liquid(f, params) or f.get("bars", 0) < 75:
        return None
    if not f.get("first_breakout"):
        return None
    vol_ratio = _f(f.get("volume")) / max(_f(f.get("avg_vol20"), 1.0), 1.0)
    if vol_ratio < 1.4:
        return None
    if f.get("above_ma50") is not True or f.get("ma50_rising") is not True:
        return None
    if _f(f.get("rs10_spy")) <= 0:
        return None
    if f.get("spy_above_ma200") is False:
        return None
    score = max(70.0, _sniper_score(f) + 5.0)
    return Signal("SNIPER_NO_ATR_CONTRACTION", f["ticker"], f["asof"], "long", score, ["first_breakout", "atr_gate_removed"], f)


def _voyager_archetype(f: Dict[str, Any]) -> Optional[str]:
    price = _f(f.get("price"))
    ma50 = _opt(f.get("ma50"))
    ma200 = _opt(f.get("ma200"))
    dvol_ratio = _f(f.get("dvol_ratio"))
    if not ma50 or not ma200 or price <= 0:
        return None
    dist_ma50 = (price - ma50) / ma50
    golden = ma50 > ma200
    if golden:
        if abs(dist_ma50) <= 0.05 and _f(f.get("range10_pct"), 1.0) <= 0.06:
            return "BASE_ACCUMULATION"
        if -0.10 <= dist_ma50 <= -0.02 and f.get("ma50_rising"):
            return "TREND_PULLBACK"
    else:
        ma_gap = (ma200 - ma50) / ma200
        if ma_gap <= 0.03 and f.get("ma50_rising") and dvol_ratio >= 1.15:
            return "EARLY_ACCUMULATION"
    return None


def _voyager_score(f: Dict[str, Any], archetype: str) -> float:
    score = 45.0
    score += min(20.0, max(0.0, _f(f.get("rs50_spy")) * 120.0))
    score += min(15.0, max(0.0, (_f(f.get("dvol_ratio")) - 0.85) * 30.0))
    score += min(10.0, max(0.0, (_f(f.get("up_vol_ratio")) - 0.8) * 20.0))
    if archetype == "BASE_ACCUMULATION":
        score += 10.0
    elif archetype == "TREND_PULLBACK":
        score += 8.0
    elif archetype == "EARLY_ACCUMULATION":
        score += 5.0
    return max(0.0, min(100.0, score))


def signal_prod_voyager_current(f: Dict[str, Any], params: StrategyParams) -> Optional[Signal]:
    if not _liquid(f, params) or f.get("bars", 0) < 260:
        return None
    if _f(f.get("price")) < 5.0:
        return None
    ma200 = _opt(f.get("ma200"))
    ma50 = _opt(f.get("ma50"))
    price = _f(f.get("price"))
    if not ma200 or not ma50:
        return None
    if price < ma200 * 0.92:
        return None
    if (price - ma50) / ma50 > 0.12:
        return None
    if _f(f.get("rs50_spy")) <= 0:
        return None
    if _f(f.get("dvol_ratio")) < 0.85:
        return None
    archetype = _voyager_archetype(f)
    if archetype is None:
        return None
    upv = _f(f.get("up_vol_ratio"), 1.0)
    if archetype == "TREND_PULLBACK":
        if upv < 0.8:
            return None
    elif upv < 1.0:
        return None
    # Fundamental and earnings gates are not retained point-in-time. Use a
    # neutral placeholder and label the approximation in the signal reasons.
    score = _voyager_score(f, archetype)
    if score < 65:
        return None
    return Signal("PROD_VOYAGER_CURRENT", f["ticker"], f["asof"], "long", score, [archetype, "fundamentals_not_retained_neutral_proxy"], f)


def signal_recall_shadow_rs_momentum(f: Dict[str, Any], params: StrategyParams) -> Optional[Signal]:
    if not _liquid(f, params) or f.get("bars", 0) < 75:
        return None
    rs = max(_f(f.get("rs20_spy"), -9.0), _f(f.get("sector_rs20"), -9.0))
    r20 = _f(f.get("r20"))
    r60 = _f(f.get("r60"))
    ext = _f(f.get("ext_ema20"), 9.0)
    if rs < params.sector_rs_threshold:
        return None
    if r20 < params.momentum_20_threshold or r60 < params.momentum_60_threshold:
        return None
    if ext > params.extension_cap:
        return None
    if f.get("above_ema20") is False:
        return None
    vol = _f(f.get("vol_expansion"), 1.0)
    score = 60 + rs * 160 + r20 * 40 + r60 * 20 + min(10, max(0, (vol - 1.0) * 10))
    if _is_power_theme(f):
        score += 8
    return Signal("RECALL_SHADOW_RS_MOMENTUM", f["ticker"], f["asof"], "long", score, ["sector_rs_momentum"], f)


def signal_recall_shadow_pullback(f: Dict[str, Any], params: StrategyParams) -> Optional[Signal]:
    if not _liquid(f, params) or f.get("bars", 0) < 75:
        return None
    rs = max(_f(f.get("rs20_spy"), -9.0), _f(f.get("sector_rs20"), -9.0))
    ext = _opt(f.get("ext_ema20"))
    if ext is None:
        return None
    if _f(f.get("r60")) < params.momentum_60_threshold:
        return None
    if rs < max(0.03, params.sector_rs_threshold * 0.6):
        return None
    if not (-params.pullback_depth <= ext <= 0.04):
        return None
    if _f(f.get("r5")) < -0.02:
        return None
    if f.get("above_ema20") is not True:
        return None
    score = 58 + rs * 140 + _f(f.get("r60")) * 25 - abs(ext) * 30
    if _is_power_theme(f):
        score += 6
    return Signal("RECALL_SHADOW_PULLBACK", f["ticker"], f["asof"], "long", score, ["pullback_reclaim"], f)


def signal_power_trend_extension(f: Dict[str, Any], params: StrategyParams) -> Optional[Signal]:
    if not _liquid(f, params) or f.get("bars", 0) < 75:
        return None
    ext = _f(f.get("ext_ema20"))
    rs = max(_f(f.get("rs20_spy"), -9.0), _f(f.get("sector_rs20"), -9.0))
    vol = _f(f.get("vol_expansion"), 1.0)
    if ext < 0.15 or ext > max(params.extension_cap, 0.35):
        return None
    if rs < params.sector_rs_threshold:
        return None
    if vol < params.volume_expansion_threshold:
        return None
    if not (_is_power_theme(f) or str(f.get("sector")) in {"Technology", "Industrials", "Energy"}):
        return None
    if _f(f.get("r5")) > 0.30 or _f(f.get("r10")) > 0.55:
        return None
    score = 55 + rs * 170 + min(15, (vol - 1.0) * 12) + ext * 20
    if _is_power_theme(f):
        score += 10
    return Signal("POWER_TREND_EXTENSION", f["ticker"], f["asof"], "long", score, ["supported_extension"], f)


def signal_qqq_tech_tactical_short(f: Dict[str, Any], params: StrategyParams, context: Dict[str, Any]) -> Optional[Signal]:
    if not _liquid(f, params) or f.get("bars", 0) < 75:
        return None
    sector = str(f.get("sector") or "")
    theme = str(f.get("theme") or "")
    if sector != "Technology" and theme not in d.POWER_THEMES and f["ticker"] not in {"QQQ", "SMH", "XLK"}:
        return None
    qqq = context.get("QQQ") or {}
    smh = context.get("SMH") or {}
    xlk = context.get("XLK") or {}
    risk_weak = (
        _f(qqq.get("r10")) < -0.01
        or _f(smh.get("r10")) < -0.02
        or _f(xlk.get("r10")) < -0.01
        or qqq.get("above_ema20") is False
    )
    if not risk_weak:
        return None
    failed_leader = _f(f.get("r60")) > 0.10 and (f.get("above_ema20") is False or _f(f.get("r10")) < -0.04)
    tech_weak = _f(f.get("rs20_qqq")) < -0.03 or _f(f.get("r20")) < -0.08
    if not (failed_leader and tech_weak):
        return None
    score = 55 + abs(_f(f.get("rs20_qqq"))) * 150 + abs(min(0.0, _f(f.get("r10")))) * 100
    return Signal("QQQ_TECH_TACTICAL_SHORT", f["ticker"], f["asof"], "short", score, ["failed_leader", "tech_weakness"], f)


CORRECTION_RECLAIM_REGIMES = {
    regime.MARKET_CORRECTION,
    regime.TECH_LED_CORRECTION,
    regime.CHOP,
    regime.RECOVERY_RECLAIM,
}


def _ema_reclaim_state(ticker: str, asof: Any, span: int) -> Optional[Dict[str, Any]]:
    df = d.load_price_frame_asof(ticker, asof)
    if df is None or len(df) < max(25, span + 3):
        return None
    close = df["close"].astype(float)
    ema = close.ewm(span=span, min_periods=min(span, len(close))).mean()
    if len(ema.dropna()) < 2:
        return None
    last_close = float(close.iloc[-1])
    prior_close = float(close.iloc[-2])
    last_ema = float(ema.iloc[-1])
    prior_ema = float(ema.iloc[-2])
    reclaim = bool(last_close > last_ema and (prior_close <= prior_ema or last_close > prior_close))
    lows = df["low"].tail(3).astype(float)
    return {
        "ema_span": int(span),
        "ema": last_ema,
        "prior_ema": prior_ema,
        "reclaim": reclaim,
        "reclaim_low": float(lows.min()),
        "last_close": last_close,
        "prior_close": prior_close,
    }


def _correction_rs(f: Dict[str, Any], context: Dict[str, Any], lookback: int) -> float:
    if lookback <= 20:
        return max(_f(f.get("rs20_spy"), -9.0), _f(f.get("rs20_qqq"), -9.0), _f(f.get("sector_rs20"), -9.0))
    if lookback >= 60:
        return max(_f(f.get("rs60_spy"), -9.0), _f(f.get("rs60_qqq"), -9.0))
    spy = context.get("SPY") or {}
    own = _f(f.get("r40"), -9.0)
    return max(own - _f(spy.get("r40"), 0.0), _f(f.get("sector_rs20"), -9.0))


def signal_correction_leader_reclaim(f: Dict[str, Any], params: StrategyParams, context: Dict[str, Any]) -> Optional[Signal]:
    regime_row = context.get("REGIME") or {}
    label = regime_row.get("label")
    if label not in CORRECTION_RECLAIM_REGIMES:
        return None
    if not _liquid(f, params) or f.get("bars", 0) < 75:
        return None

    spy = context.get("SPY") or {}
    qqq = context.get("QQQ") or {}
    market_dd = min(
        _f(spy.get("drawdown_from_high60")),
        _f(qqq.get("drawdown_from_high60")),
        _f(spy.get("drawdown_from_high20")),
        _f(qqq.get("drawdown_from_high20")),
    )
    rs = _correction_rs(f, context, int(params.correction_rs_lookback))
    if rs < 0.01:
        return None

    price = _f(f.get("price"))
    if price <= 0:
        return None
    dd20 = _f(f.get("drawdown_from_high20"))
    dd60 = _f(f.get("drawdown_from_high60"))
    low20 = _f(f.get("low20"))
    if dd60 < -abs(params.correction_max_pullback) or dd20 < -0.20:
        return None
    if market_dd < 0 and dd60 < market_dd - 0.02:
        return None
    if low20 > 0 and price <= low20 * 1.02:
        return None

    reclaim = _ema_reclaim_state(f["ticker"], f["asof"], int(params.correction_ema_reclaim))
    if not reclaim or not reclaim["reclaim"]:
        return None
    ma200 = _opt(f.get("ma200"))
    ma50 = _opt(f.get("ma50"))
    if ma200 and price < ma200 * 0.96:
        return None
    if ma50 and price < ma50 * 0.92:
        return None

    avg_vol = max(_f(f.get("avg_vol20"), 1.0), 1.0)
    volume_ratio = _f(f.get("volume")) / avg_vol
    vol_expansion = _f(f.get("vol_expansion"), 1.0)
    dryup = vol_expansion <= params.correction_volume_dryup_threshold
    volume_confirm = volume_ratio >= params.correction_volume_expansion_threshold or dryup
    if not volume_confirm:
        return None

    ext = _f(f.get("ext_ema20"))
    if ext > 0.12 and dd20 > -0.05:
        return None
    if _f(f.get("r10")) > 0.30 and dd20 > -0.08:
        return None

    atr_stop = max(0.0, _f(f.get("atr_pct")) * params.correction_atr_stop_multiple)
    reclaim_stop = max(0.0, (price - _f(reclaim.get("reclaim_low"), price)) / price + 0.01)
    stop_pct = min(0.10, max(0.04, atr_stop, reclaim_stop))
    feat = dict(f)
    feat.update({
        "market_regime": label,
        "market_regime_flags": regime_row.get("flags"),
        "correction_rs": rs,
        "correction_market_drawdown": market_dd,
        "correction_ema_reclaim_span": int(params.correction_ema_reclaim),
        "correction_volume_ratio": volume_ratio,
        "correction_volume_dryup": dryup,
        "clr_stop_loss_pct": stop_pct,
        "clr_profit_target_pct": params.profit_target_pct,
    })
    score = 55.0
    score += min(25.0, max(0.0, rs * 180.0))
    score += min(10.0, max(0.0, (dd60 - market_dd) * 80.0)) if market_dd < 0 else 0.0
    score += 5.0 if label == regime.RECOVERY_RECLAIM else 0.0
    score += 5.0 if volume_ratio >= params.correction_volume_expansion_threshold else 0.0
    score += 3.0 if dryup else 0.0
    reasons = [
        "correction_regime",
        "positive_relative_strength",
        f"ema{int(params.correction_ema_reclaim)}_reclaim",
        "controlled_pullback",
        "volume_confirm_or_dryup",
        "earnings_calendar_not_retained_no_future_filter",
    ]
    return Signal("CORRECTION_LEADER_RECLAIM", f["ticker"], f["asof"], "long", score, reasons, feat)


def signal_simple_sector_rs(f: Dict[str, Any], params: StrategyParams) -> Optional[Signal]:
    if not _liquid(f, params) or f.get("bars", 0) < 60:
        return None
    srs = _opt(f.get("sector_rs20"))
    if srs is None or srs < params.sector_rs_threshold:
        return None
    return Signal("SIMPLE_SECTOR_RS", f["ticker"], f["asof"], "long", 50 + srs * 200, ["sector_rs_only"], f)


def signal_simple_mom_20_60(f: Dict[str, Any], params: StrategyParams) -> Optional[Signal]:
    if not _liquid(f, params) or f.get("bars", 0) < 60:
        return None
    if _f(f.get("r20")) < max(0.10, params.momentum_20_threshold):
        return None
    if _f(f.get("r60")) < max(0.20, params.momentum_60_threshold):
        return None
    return Signal("SIMPLE_MOM_20_60", f["ticker"], f["asof"], "long", 50 + _f(f.get("r20")) * 100 + _f(f.get("r60")) * 50, ["mom_20_60_only"], f)


# --- Phase 1I: LEADER_RESET_RECLAIM (research-only standalone alpha test) ---
# Fixed a priori; deliberately NOT exposed to the walk-forward param sweep so
# the walk-forward split stays a decay diagnostic, not a fitting surface.
LRR_RS60_MIN = 0.05          # prior leadership: 60d RS vs SPY/QQQ
LRR_R60_MIN = 0.15           # prior leadership: strong 60d momentum
LRR_RESET_MIN = 0.05         # reset: minimum pullback from 20d high
LRR_RESET_MAX = 0.18         # reset: beyond this is a trend break, not a reset
LRR_CRASH_R10 = -0.18        # reset: 10d crash = climax failure, not a reset
LRR_PARABOLIC_R20 = 0.60     # parabolic climax run-up is excluded
LRR_ATR_STOP_MULT = 2.0


def _lrr_reclaim(ticker: str, asof: Any) -> Optional[Dict[str, Any]]:
    """Reclaim of the 10 EMA preferred, 20 EMA fallback, with an up close."""
    for span in (10, 20):
        rec = _ema_reclaim_state(ticker, asof, span)
        if rec and rec.get("reclaim") and _f(rec.get("last_close")) > _f(rec.get("prior_close")):
            rec["span"] = span
            return rec
    return None


def signal_leader_reset_reclaim(f: Dict[str, Any], params: StrategyParams) -> Optional[Signal]:
    if not _liquid(f, params) or f.get("bars", 0) < 75:
        return None
    # 1. Prior leadership (as-of: 60d RS/momentum predates a 1-3 week reset).
    rs60 = max(_f(f.get("rs60_spy"), -9.0), _f(f.get("rs60_qqq"), -9.0))
    if rs60 < LRR_RS60_MIN:
        return None
    if _f(f.get("r60")) < LRR_R60_MIN:
        return None
    # 2. Controlled reset: pulled back from the 20d high but trend intact.
    dd20 = _f(f.get("drawdown_from_high20"))
    if not (-LRR_RESET_MAX <= dd20 <= -LRR_RESET_MIN):
        return None
    if f.get("above_ma200") is False:
        return None
    ma50 = _opt(f.get("ma50"))
    price = _f(f.get("price"))
    if ma50 and price < ma50 * 0.92:
        return None
    if _f(f.get("r10")) < LRR_CRASH_R10:
        return None
    if _f(f.get("r20")) > LRR_PARABOLIC_R20:
        return None
    # 3. Reclaim trigger with close strength. Entry is next open (lab default).
    reclaim = _lrr_reclaim(f["ticker"], f["asof"])
    if reclaim is None:
        return None
    # 4. Risk: stop below reclaim low or ATR stop, clamped to [4%, 10%].
    atr_stop = max(0.0, _f(f.get("atr_pct")) * LRR_ATR_STOP_MULT)
    reclaim_stop = max(0.0, (price - _f(reclaim.get("reclaim_low"), price)) / price + 0.01) if price > 0 else 0.0
    stop_pct = min(0.10, max(0.04, atr_stop, reclaim_stop))

    volume_ratio = _f(f.get("volume")) / max(_f(f.get("avg_vol20"), 1.0), 1.0)
    dryup = _f(f.get("vol_expansion"), 1.0) <= 0.95
    compressed = _opt(f.get("atr_contraction")) is not None and _f(f.get("atr_contraction")) < 0.90

    score = 55.0
    score += min(20.0, rs60 * 120.0)
    score += min(10.0, _f(f.get("r60")) * 20.0)
    score += 5.0 if volume_ratio >= 1.1 else 0.0
    score += 4.0 if dryup else 0.0
    score += 3.0 if compressed else 0.0
    score += 4.0 if (_is_power_theme(f) or _f(f.get("sector_rs20")) > 0) else 0.0
    score = min(100.0, score)

    feat = dict(f)
    feat.update({
        "lrr_reclaim_span": reclaim["span"],
        "lrr_reclaim_low": reclaim.get("reclaim_low"),
        "lrr_stop_loss_pct": stop_pct,
        "lrr_profit_target_pct": params.profit_target_pct,
        "lrr_volume_ratio": volume_ratio,
        "lrr_volume_dryup": dryup,
        "lrr_atr_compressed": compressed,
    })
    reasons = [
        "prior_leadership_rs60",
        "controlled_reset",
        f"ema{reclaim['span']}_reclaim_close_strength",
        "volume_confirm" if volume_ratio >= 1.1 else "no_volume_confirm",
        "earnings_calendar_not_retained_no_future_filter",
    ]
    return Signal("LEADER_RESET_RECLAIM", f["ticker"], f["asof"], "long", score, reasons, feat)


# --- Phase 1I.1: LRR_REGIME_GATED (research-only, strict a-priori test) ---
# The allowed set was FIXED before this variant was ever run, from the Phase 1I
# regime breakdown of unrestricted LRR. The regime label comes from
# regime.classify_regime, which uses only as-of SPY/QQQ/SMH/XLK/VXX features.
# BULL_TREND is blocked: the classifier emits RECOVERY_RECLAIM as its own
# label, so "bull unless recovery condition is present" reduces to this set.
# CHOP, RISK_OFF, MARKET_CORRECTION, and unknown labels are all blocked.
LRR_ALLOWED_REGIMES = frozenset({
    regime.TECH_LED_CORRECTION,
    regime.RECOVERY_RECLAIM,
    regime.HIGH_VOLATILITY,
})


def signal_lrr_regime_gated(f: Dict[str, Any], params: StrategyParams, context: Dict[str, Any]) -> Optional[Signal]:
    label = (context.get("REGIME") or {}).get("label")
    if label not in LRR_ALLOWED_REGIMES:
        return None
    base = signal_leader_reset_reclaim(f, params)
    if base is None:
        return None
    feat = dict(base.features)
    feat["lrr_regime_label"] = label
    return Signal(
        "LRR_REGIME_GATED", base.ticker, base.asof, base.side, base.score,
        [f"regime_gate:{label}"] + list(base.reasons), feat,
    )


VARIANT_FUNCS: Dict[str, Callable[..., Optional[Signal]]] = {
    "LEADER_RESET_RECLAIM": signal_leader_reset_reclaim,
    "LRR_REGIME_GATED": signal_lrr_regime_gated,
    "PROD_SNIPER_CURRENT": signal_prod_sniper_current,
    "SNIPER_NO_ATR_CONTRACTION": signal_sniper_no_atr,
    "PROD_VOYAGER_CURRENT": signal_prod_voyager_current,
    "CORRECTION_LEADER_RECLAIM": signal_correction_leader_reclaim,
    "RECALL_SHADOW_RS_MOMENTUM": signal_recall_shadow_rs_momentum,
    "RECALL_SHADOW_PULLBACK": signal_recall_shadow_pullback,
    "POWER_TREND_EXTENSION": signal_power_trend_extension,
    "QQQ_TECH_TACTICAL_SHORT": signal_qqq_tech_tactical_short,
    "SIMPLE_SECTOR_RS": signal_simple_sector_rs,
    "SIMPLE_MOM_20_60": signal_simple_mom_20_60,
}


def deterministic_seed(*parts: Any) -> int:
    raw = "|".join(str(p) for p in parts)
    return int(hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12], 16)


def select_random_liquid(features: Sequence[Dict[str, Any]], asof: Any, n: int, seed: int) -> List[Signal]:
    rng = random.Random(deterministic_seed(seed, str(asof)[:10], "RANDOM_LIQUID"))
    candidates = sorted(features, key=lambda f: f["ticker"])
    picks = rng.sample(candidates, min(max(0, n), len(candidates))) if candidates else []
    out = []
    for f in picks:
        out.append(Signal("RANDOM_LIQUID", f["ticker"], f["asof"], "long", 1.0, ["deterministic_random_liquid"], f))
    return out


def round_trip_cost_fraction(cost_model: Dict[str, Any], side: str, hold_days: int) -> float:
    slip = _f(cost_model.get("slippage_bps")) * 2.0
    spread = _f(cost_model.get("spread_bps"))
    comm = _f(cost_model.get("commission_bps")) * 2.0
    borrow = 0.0
    if side == "short":
        borrow = _f(cost_model.get("short_borrow_annual_pct")) / 100.0 * max(0, hold_days) / 252.0 * 10000.0
    return (slip + spread + comm + borrow) / 10000.0


def _entry_price(row: pd.Series, entry_timing: str) -> float:
    if entry_timing == "next_close":
        return float(row.get("close"))
    value = row.get("open")
    if value is None or pd.isna(value) or float(value) <= 0:
        return float(row.get("close"))
    return float(value)


@lru_cache(maxsize=300_000)
def _benchmark_return(symbol: str, asof: str, exit_date: str, side: str, entry_timing: str) -> Optional[float]:
    future = d.get_forward_window(symbol, asof, 30)
    if future.empty:
        return None
    future = future[future.index <= pd.Timestamp(exit_date)]
    if future.empty:
        return None
    entry = _entry_price(future.iloc[0], entry_timing)
    exit_price = float(future.iloc[-1]["close"])
    if entry <= 0:
        return None
    ret = exit_price / entry - 1.0
    return -ret if side == "short" else ret


@lru_cache(maxsize=500_000)
def _simulate_path(
    ticker: str,
    asof: str,
    side: str,
    params: StrategyParams,
    entry_timing: str,
) -> Optional[Tuple[float, float, str, str, int, float, str, float, float]]:
    """Cost-independent trade path for one (ticker, asof, side, params).

    Shared across the three cost models so the identical price path is only
    simulated once. Returns (entry, exit_price, exit_date, entry_date,
    hold_days, raw_return, exit_reason, mfe, mae) or None.
    """
    return _simulate_path_custom(
        ticker,
        asof,
        side,
        int(params.max_hold_days),
        float(params.stop_loss_pct),
        float(params.profit_target_pct),
        params.trailing_stop_pct,
        entry_timing,
    )


@lru_cache(maxsize=500_000)
def _simulate_path_custom(
    ticker: str,
    asof: str,
    side: str,
    max_hold_days: int,
    stop_loss_pct: float,
    profit_target_pct: float,
    trailing_stop_pct: Optional[float],
    entry_timing: str,
) -> Optional[Tuple[float, float, str, str, int, float, str, float, float]]:
    """Cost-independent path with explicit research-only exit settings."""
    future = d.get_forward_window(ticker, asof, max_hold_days)
    if len(future) < max_hold_days:
        return None
    rows = future.head(max_hold_days)
    entry = _entry_price(rows.iloc[0], entry_timing)
    if entry <= 0:
        return None

    stop = max(0.0, float(stop_loss_pct))
    target = max(0.0, float(profit_target_pct))
    trailing = trailing_stop_pct
    exit_price = float(rows.iloc[-1]["close"])
    exit_date = rows.index[-1]
    exit_reason = "max_hold"
    exit_i = len(rows) - 1
    high_water = entry
    low_water = entry

    for i, (idx, row) in enumerate(rows.iterrows()):
        high = float(row["high"])
        low = float(row["low"])
        high_water = max(high_water, high)
        low_water = min(low_water, low)
        if side == "long":
            stop_px = entry * (1.0 - stop)
            if trailing is not None:
                stop_px = max(stop_px, high_water * (1.0 - trailing))
            target_px = entry * (1.0 + target)
            if low <= stop_px:
                exit_price, exit_date, exit_reason, exit_i = stop_px, idx, "stop", i
                break
            if high >= target_px:
                exit_price, exit_date, exit_reason, exit_i = target_px, idx, "target", i
                break
        else:
            stop_px = entry * (1.0 + stop)
            if trailing is not None:
                stop_px = min(stop_px, low_water * (1.0 + trailing))
            target_px = entry * (1.0 - target)
            if high >= stop_px:
                exit_price, exit_date, exit_reason, exit_i = stop_px, idx, "stop", i
                break
            if low <= target_px:
                exit_price, exit_date, exit_reason, exit_i = target_px, idx, "target", i
                break

    held = rows.iloc[: exit_i + 1]
    raw = (exit_price / entry - 1.0) if side == "long" else ((entry - exit_price) / entry)
    if side == "long":
        mfe = float(held["high"].max() / entry - 1.0)
        mae = float(held["low"].min() / entry - 1.0)
    else:
        mfe = float((entry - held["low"].min()) / entry)
        mae = float((entry - held["high"].max()) / entry)
    return (
        entry,
        float(exit_price),
        str(pd.Timestamp(exit_date).date()),
        str(rows.index[0].date()),
        int(exit_i + 1),
        raw,
        exit_reason,
        mfe,
        mae,
    )


def clear_caches_for_tests() -> None:
    _simulate_path.cache_clear()
    _simulate_path_custom.cache_clear()
    _benchmark_return.cache_clear()
    d.clear_caches_for_tests()


def simulate_trade(
    signal: Signal | Dict[str, Any],
    *,
    params: StrategyParams,
    cost_model: Dict[str, Any] = BASE_COST,
    entry_timing: str = "next_open",
) -> Optional[Dict[str, Any]]:
    if isinstance(signal, dict):
        sig = Signal(
            signal["variant"], signal["ticker"], signal["asof"], signal["side"],
            float(signal.get("score", 0.0)), list(signal.get("reasons") or []),
            dict(signal.get("features") or {}),
        )
    else:
        sig = signal
    side = sig.side
    if sig.variant == "CORRECTION_LEADER_RECLAIM":
        path = _simulate_path_custom(
            sig.ticker,
            str(sig.asof),
            side,
            int(params.max_hold_days),
            float(sig.features.get("clr_stop_loss_pct") or params.stop_loss_pct),
            float(sig.features.get("clr_profit_target_pct") or params.profit_target_pct),
            params.trailing_stop_pct,
            entry_timing,
        )
    elif sig.variant in {"LEADER_RESET_RECLAIM", "LRR_REGIME_GATED"}:
        path = _simulate_path_custom(
            sig.ticker,
            str(sig.asof),
            side,
            int(params.max_hold_days),
            float(sig.features.get("lrr_stop_loss_pct") or params.stop_loss_pct),
            float(sig.features.get("lrr_profit_target_pct") or params.profit_target_pct),
            params.trailing_stop_pct,
            entry_timing,
        )
    else:
        path = _simulate_path(sig.ticker, str(sig.asof), side, params, entry_timing)
    if path is None:
        return None
    entry, exit_price, exit_date, entry_date, hold_days, raw, exit_reason, mfe, mae = path
    cost = round_trip_cost_fraction(cost_model, side, hold_days)
    net = raw - cost
    spy_ret = _benchmark_return("SPY", str(sig.asof), exit_date, side, entry_timing)
    qqq_ret = _benchmark_return("QQQ", str(sig.asof), exit_date, side, entry_timing)
    return {
        "variant": sig.variant,
        "ticker": sig.ticker,
        "side": side,
        "signal_date": sig.asof,
        "entry_date": entry_date,
        "exit_date": exit_date,
        "entry_price": round(entry, 4),
        "exit_price": round(exit_price, 4),
        "hold_days": hold_days,
        "raw_return": round(raw, 6),
        "net_return": round(net, 6),
        "cost_return": round(cost, 6),
        "exit_reason": exit_reason,
        "stop_hit": exit_reason == "stop",
        "target_hit": exit_reason == "target",
        "mfe": round(mfe, 6),
        "mae": round(mae, 6),
        "rel_spy": round(net - spy_ret, 6) if spy_ret is not None else None,
        "rel_qqq": round(net - qqq_ret, 6) if qqq_ret is not None else None,
        "score": round(float(sig.score), 4),
        "sector": sig.features.get("sector"),
        "theme": sig.features.get("theme"),
        "market_regime": sig.features.get("market_regime"),
        "market_regime_flags": sig.features.get("market_regime_flags"),
        "correction_rs": sig.features.get("correction_rs"),
        "correction_market_drawdown": sig.features.get("correction_market_drawdown"),
        "correction_ema_reclaim_span": sig.features.get("correction_ema_reclaim_span"),
        "correction_volume_ratio": sig.features.get("correction_volume_ratio"),
        "correction_volume_dryup": sig.features.get("correction_volume_dryup"),
        "reasons": list(sig.reasons),
        "data_reliability": sig.features.get("data_reliability"),
        "spy_above_ma200_at_signal": sig.features.get("spy_above_ma200"),
        "cost_model": cost_model.get("name"),
    }


def _max_drawdown(returns: Sequence[float]) -> float:
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in returns:
        equity *= 1.0 + float(r)
        peak = max(peak, equity)
        if peak:
            max_dd = min(max_dd, equity / peak - 1.0)
    return max_dd


def summarize_trades(trades: Sequence[Dict[str, Any]], *, start: str, end: str) -> Dict[str, Any]:
    returns = [float(t["net_return"]) for t in trades]
    raw_returns = [float(t["raw_return"]) for t in trades]
    rel_spy = [float(t["rel_spy"]) for t in trades if t.get("rel_spy") is not None]
    rel_qqq = [float(t["rel_qqq"]) for t in trades if t.get("rel_qqq") is not None]
    mfe = [float(t["mfe"]) for t in trades if t.get("mfe") is not None]
    mae = [float(t["mae"]) for t in trades if t.get("mae") is not None]
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r < 0]
    days = max(1, (pd.Timestamp(end) - pd.Timestamp(start)).days)
    weeks = max(1.0, days / 7.0)
    monthly: Dict[str, List[float]] = defaultdict(list)
    by_ticker = Counter()
    by_sector = Counter()
    by_theme = Counter()
    regime: Dict[str, List[float]] = defaultdict(list)
    for t in trades:
        monthly[str(t["exit_date"])[:7]].append(float(t["net_return"]))
        by_ticker[t["ticker"]] += 1
        by_sector[t.get("sector") or "UNKNOWN"] += 1
        by_theme[t.get("theme") or "unknown"] += 1
        flag = t.get("spy_above_ma200_at_signal")
        regime_key = "spy_above_ma200" if flag is True else ("spy_below_ma200" if flag is False else "regime_unknown")
        regime[regime_key].append(float(t["net_return"]))
    avg = statistics.mean(returns) if returns else None
    downside = [r for r in returns if r < 0]
    stdev = statistics.pstdev(returns) if len(returns) > 1 else None
    down_stdev = statistics.pstdev(downside) if len(downside) > 1 else None
    return {
        "trade_count": len(trades),
        "trades_per_week": round(len(trades) / weeks, 3),
        "win_rate": round(len(wins) / len(returns), 4) if returns else None,
        "average_return": round(avg, 6) if avg is not None else None,
        "median_return": round(statistics.median(returns), 6) if returns else None,
        "expectancy": round(avg, 6) if avg is not None else None,
        "average_raw_return": round(statistics.mean(raw_returns), 6) if raw_returns else None,
        "rel_spy": round(statistics.mean(rel_spy), 6) if rel_spy else None,
        "rel_qqq": round(statistics.mean(rel_qqq), 6) if rel_qqq else None,
        "max_drawdown": round(_max_drawdown(returns), 6),
        "mfe": round(statistics.mean(mfe), 6) if mfe else None,
        "mae": round(statistics.mean(mae), 6) if mae else None,
        "profit_factor": round(sum(wins) / abs(sum(losses)), 4) if losses else (None if not wins else 999.0),
        "sharpe_per_trade": round((avg / stdev), 4) if avg is not None and stdev and stdev > 0 else None,
        "sortino_per_trade": round((avg / down_stdev), 4) if avg is not None and down_stdev and down_stdev > 0 else None,
        "average_hold_days": round(statistics.mean([t["hold_days"] for t in trades]), 3) if trades else None,
        "stop_hit_rate": round(sum(1 for t in trades if t["stop_hit"]) / len(trades), 4) if trades else None,
        "target_hit_rate": round(sum(1 for t in trades if t["target_hit"]) / len(trades), 4) if trades else None,
        "worst_10_trades": sorted(trades, key=lambda t: t["net_return"])[:10],
        "best_10_trades": sorted(trades, key=lambda t: t["net_return"], reverse=True)[:10],
        "sector_concentration": by_sector.most_common(10),
        "theme_concentration": by_theme.most_common(10),
        "ticker_concentration": by_ticker.most_common(10),
        "max_ticker_concentration_pct": round((max(by_ticker.values()) / len(trades)), 4) if trades else None,
        "monthly_return_distribution": {
            month: round(statistics.mean(vals), 6) for month, vals in sorted(monthly.items())
        },
        "monthly_trade_counts": {month: len(vals) for month, vals in sorted(monthly.items())},
        "regime_breakdown": {
            key: {"trade_count": len(vals), "average_return": round(statistics.mean(vals), 6)}
            for key, vals in sorted(regime.items())
        },
        "liquidity_spread_warning": "spread modeled from configured bps; no point-in-time quote spread retained",
    }


def _baseline_buy_hold(symbol: str, start: str, end: str, cost_model: Dict[str, Any]) -> Dict[str, Any]:
    df = d.get_forward_window(symbol, start, 3000)
    if df.empty:
        return summarize_trades([], start=start, end=end)
    df = df[df.index <= pd.Timestamp(end)]
    if df.empty:
        return summarize_trades([], start=start, end=end)
    entry = float(df.iloc[0]["open"] if df.iloc[0].get("open") else df.iloc[0]["close"])
    exit_price = float(df.iloc[-1]["close"])
    raw = exit_price / entry - 1.0 if entry > 0 else 0.0
    cost = round_trip_cost_fraction(cost_model, "long", len(df))
    trade = {
        "variant": f"{symbol}_BUY_HOLD",
        "ticker": symbol,
        "side": "long",
        "signal_date": start,
        "entry_date": str(df.index[0].date()),
        "exit_date": str(df.index[-1].date()),
        "entry_price": entry,
        "exit_price": exit_price,
        "hold_days": len(df),
        "raw_return": raw,
        "net_return": raw - cost,
        "cost_return": cost,
        "exit_reason": "buy_hold",
        "stop_hit": False,
        "target_hit": False,
        "mfe": float(df["high"].max() / entry - 1.0) if entry else None,
        "mae": float(df["low"].min() / entry - 1.0) if entry else None,
        "rel_spy": None,
        "rel_qqq": None,
        "score": 0,
        "sector": "ETF",
        "theme": "benchmark",
        "reasons": ["buy_hold_baseline"],
        "cost_model": cost_model.get("name"),
    }
    return summarize_trades([trade], start=start, end=end)


def _cash_baseline(start: str, end: str) -> Dict[str, Any]:
    out = summarize_trades([], start=start, end=end)
    out.update({
        "trade_count": 0,
        "average_return": 0.0,
        "median_return": 0.0,
        "expectancy": 0.0,
        "rel_spy": 0.0,
        "rel_qqq": 0.0,
        "max_drawdown": 0.0,
        "profit_factor": None,
        "note": "cash/no-trade baseline",
    })
    return out


def generate_signals_for_date(
    asof: Any,
    *,
    variants: Sequence[str],
    params: StrategyParams,
    config: BacktestConfig,
    features: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, List[Signal]]:
    if features is None:
        features = d.compute_universe_features_asof(
            asof,
            mode=config.universe_mode,
            cap=config.universe_cap,
            min_bars=config.min_bars,
            min_avg_dvol=params.min_avg_dvol,
        )
    context = {
        sym: d.compute_features_asof(sym, asof)
        for sym in ("SPY", "QQQ", "SMH", "XLK")
    }
    context["REGIME"] = regime.classify_regime(
        asof,
        correction_threshold=params.correction_market_dd_threshold,
    )
    regime_label = (context.get("REGIME") or {}).get("label")
    regime_flags = (context.get("REGIME") or {}).get("flags")
    for f in features:
        f.setdefault("market_regime", regime_label)
        f.setdefault("market_regime_flags", regime_flags)
    out: Dict[str, List[Signal]] = {v: [] for v in variants if v not in BENCHMARK_VARIANTS}
    for variant in variants:
        if variant in BENCHMARK_VARIANTS:
            continue
        if variant == "RANDOM_LIQUID":
            out[variant] = select_random_liquid(
                features, asof, config.max_signals_per_variant_day, config.random_seed
            )
            continue
        fn = VARIANT_FUNCS.get(variant)
        if fn is None:
            continue
        signals: List[Signal] = []
        for f in features:
            try:
                if variant in {"QQQ_TECH_TACTICAL_SHORT", "CORRECTION_LEADER_RECLAIM", "LRR_REGIME_GATED"}:
                    sig = fn(f, params, context)
                else:
                    sig = fn(f, params)
            except Exception:
                sig = None
            if sig is not None:
                signals.append(sig)
        signals.sort(key=lambda s: s.score, reverse=True)
        out[variant] = signals[: config.max_signals_per_variant_day]
    return out


def run_backtest_window(
    name: str,
    start: str,
    end: str,
    *,
    variants: Sequence[str] = DEFAULT_VARIANTS,
    params: StrategyParams = StrategyParams(),
    config: BacktestConfig = BacktestConfig(),
    cost_models: Sequence[Dict[str, Any]] = COST_MODELS,
) -> Dict[str, Any]:
    all_dates = d.trading_dates_between(start, end)
    dates = all_dates
    if config.date_stride > 1:
        dates = all_dates[:: config.date_stride]
    sampled = config.date_stride > 1
    signals_by_variant: Dict[str, List[Signal]] = {v: [] for v in variants}
    ticker_days = 0
    skipped_dates: List[Dict[str, str]] = []
    for asof in dates:
        features = d.compute_universe_features_asof(
            asof,
            mode=config.universe_mode,
            cap=config.universe_cap,
            min_bars=config.min_bars,
            min_avg_dvol=params.min_avg_dvol,
        )
        ticker_days += len(features)
        if not features:
            skipped_dates.append({
                "date": str(pd.Timestamp(asof).date()),
                "reason": "no_retained_ticker_meets_min_bars_and_liquidity_on_this_date",
            })
        daily = generate_signals_for_date(
            asof, variants=variants, params=params, config=config, features=features
        )
        for variant, signals in daily.items():
            signals_by_variant.setdefault(variant, []).extend(signals)

    by_cost: Dict[str, Dict[str, Any]] = {}
    for cost in cost_models:
        c_name = str(cost.get("name") or "cost")
        by_variant: Dict[str, Any] = {}
        for variant in variants:
            trades: List[Dict[str, Any]] = []
            for sig in signals_by_variant.get(variant, []):
                trade = simulate_trade(sig, params=params, cost_model=cost, entry_timing=config.entry_timing)
                if trade is not None:
                    trades.append(trade)
            by_variant[variant] = summarize_trades(trades, start=start, end=end)
            by_variant[variant]["signals_generated"] = len(signals_by_variant.get(variant, []))
            by_variant[variant]["cost_model"] = c_name
        by_variant["SPY_BUY_HOLD"] = _baseline_buy_hold("SPY", start, end, cost)
        by_variant["QQQ_BUY_HOLD"] = _baseline_buy_hold("QQQ", start, end, cost)
        by_variant["CASH"] = _cash_baseline(start, end)
        by_cost[c_name] = by_variant

    return {
        "name": name,
        "start": start,
        "end": end,
        "trading_dates": len(dates),
        "dates_total_in_window": len(all_dates),
        "dates_evaluated": len(dates),
        "dates_skipped_by_stride": len(all_dates) - len(dates),
        "sampled": sampled,
        "ticker_days_evaluated": ticker_days,
        "skipped_dates": skipped_dates[:50],
        "skipped_date_count": len(skipped_dates),
        "params": params.as_dict(),
        "config": config.as_dict(),
        "signals_generated": {k: len(v) for k, v in signals_by_variant.items()},
        "by_cost": by_cost,
    }


def _metric_score(m: Dict[str, Any]) -> float:
    if not m or not m.get("trade_count"):
        return -999.0
    exp = _f(m.get("expectancy"))
    rel_spy = _f(m.get("rel_spy"))
    rel_qqq = _f(m.get("rel_qqq"))
    dd = abs(_f(m.get("max_drawdown")))
    pf = min(_f(m.get("profit_factor"), 0.0), 5.0)
    n = min(_f(m.get("trade_count")), 100.0)
    conc = _f(m.get("max_ticker_concentration_pct"), 1.0)
    return exp * 100.0 + rel_spy * 30.0 + rel_qqq * 20.0 + pf * 0.1 + n * 0.005 - dd * 2.0 - max(0.0, conc - 0.25)


def verdict_for_variant(
    variant: str,
    windows: Dict[str, Any],
    *,
    cost_name: str = "base_cost",
    sampled: bool = False,
) -> Dict[str, Any]:
    metrics = [(wname, w["by_cost"][cost_name].get(variant, {})) for wname, w in windows.items()]
    trade_count = sum(int(m.get("trade_count") or 0) for _, m in metrics)
    positive = [w for w, m in metrics if _f(m.get("expectancy")) > 0]
    rel_positive = [w for w, m in metrics if _f(m.get("rel_spy")) > 0 and _f(m.get("rel_qqq")) > 0]
    destroyed_high_cost = False
    for wname, w in windows.items():
        hi = (w["by_cost"].get("high_cost") or {}).get(variant, {})
        base = (w["by_cost"].get("base_cost") or {}).get(variant, {})
        if base.get("trade_count") and _f(base.get("expectancy")) > 0 and _f(hi.get("expectancy")) <= 0:
            destroyed_high_cost = True
    blockers: List[str] = []
    if trade_count < 40:
        blockers.append("minimum_trade_count")
    if len(positive) < 2:
        blockers.append("not_stable_across_two_windows")
    if len(rel_positive) < 2:
        blockers.append("does_not_beat_spy_qqq_across_two_windows")
    if destroyed_high_cost:
        blockers.append("destroyed_by_high_cost")
    max_conc = max((_f(m.get("max_ticker_concentration_pct")) for _, m in metrics), default=0.0)
    if max_conc > 0.35:
        blockers.append("ticker_concentration")
    worst_dd = min((_f(m.get("max_drawdown")) for _, m in metrics), default=0.0)
    if worst_dd < -0.20:
        blockers.append("drawdown_too_high")

    if trade_count == 0:
        verdict = VERDICT_REJECT
    elif trade_count < 40:
        verdict = VERDICT_NEED_MORE
    elif blockers:
        verdict = VERDICT_OVERFIT_RISK if len(positive) >= 2 else VERDICT_REJECT
    else:
        verdict = VERDICT_EDGE
    # Sampled (stride>1) runs are for debugging only: they can never assert an
    # edge or feed a promotion decision.
    if sampled:
        blockers.append("sampled_run_not_decision_grade")
        if verdict == VERDICT_EDGE:
            verdict = VERDICT_OVERFIT_RISK
    # This lab intentionally does not mark READY without independent
    # walk-forward confirmation; strategy_walk_forward.py is the next gate.
    return {
        "variant": variant,
        "verdict": verdict,
        "blockers": blockers,
        "trade_count": trade_count,
        "positive_windows": positive,
        "rel_positive_windows": rel_positive,
        "worst_drawdown": round(worst_dd, 6),
        "max_ticker_concentration_pct": round(max_conc, 4),
        "score": round(sum(_metric_score(m) for _, m in metrics), 6),
        "sampled": sampled,
        "promotable_from_this_run": bool(not sampled and verdict == VERDICT_EDGE),
    }


def default_windows(today: Optional[str] = None) -> List[Dict[str, str]]:
    cal = d.benchmark_calendar()
    latest = pd.Timestamp(today).normalize() if today else pd.Timestamp(cal.max()).normalize()
    latest = min(latest, pd.Timestamp(cal.max()).normalize())
    out: List[Dict[str, str]] = []

    def add(name: str, start: str, end: str) -> None:
        dates = d.trading_dates_between(start, end)
        if len(dates) >= 20:
            out.append({"name": name, "start": str(pd.Timestamp(dates[0]).date()), "end": str(pd.Timestamp(dates[-1]).date())})

    add("2024_available", "2024-01-01", "2024-12-31")
    add("2025_available", "2025-01-01", "2025-12-31")
    add("2026_ytd", "2026-01-01", str(latest.date()))
    recent = cal[cal <= latest]
    if len(recent) >= 60:
        add("recent_60_trading_days", str(pd.Timestamp(recent[-60]).date()), str(pd.Timestamp(recent[-1]).date()))

    # Recent rolling 3-month blocks, limited to keep the default run fast.
    if len(recent) >= 126:
        starts = list(range(max(0, len(recent) - 252), len(recent) - 63, 63))
        for idx, start_i in enumerate(starts[-4:], start=1):
            block = recent[start_i:start_i + 63]
            if len(block) >= 40:
                add(f"rolling_3m_{idx}", str(pd.Timestamp(block[0]).date()), str(pd.Timestamp(block[-1]).date()))
    # Deduplicate identical windows.
    seen = set()
    uniq = []
    for w in out:
        key = (w["name"], w["start"], w["end"])
        if key not in seen:
            seen.add(key)
            uniq.append(w)
    return uniq


def full_windows(today: Optional[str] = None) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    """All historical windows for exact_full mode plus an availability report.

    Returns (windows, unavailable) where unavailable states exactly which
    requested windows the retained caches cannot support — nothing is
    fabricated or backfilled.
    """
    cal = d.benchmark_calendar()
    latest = pd.Timestamp(today).normalize() if today else pd.Timestamp(cal.max()).normalize()
    latest = min(latest, pd.Timestamp(cal.max()).normalize())
    windows: List[Dict[str, str]] = []
    unavailable: List[Dict[str, str]] = []

    def add(name: str, start: str, end: str) -> None:
        dates = d.trading_dates_between(start, end)
        if len(dates) >= 20:
            windows.append({
                "name": name,
                "start": str(pd.Timestamp(dates[0]).date()),
                "end": str(pd.Timestamp(dates[-1]).date()),
            })
        else:
            unavailable.append({
                "name": name,
                "requested": f"{start}..{end}",
                "reason": f"only {len(dates)} benchmark trading dates retained in local price caches",
            })

    add("2024_available", "2024-01-01", "2024-12-31")
    add("2025_available", "2025-01-01", "2025-12-31")
    add("2026_ytd", "2026-01-01", str(latest.date()))
    recent = cal[cal <= latest]
    if len(recent) >= 60:
        add("recent_60_trading_days", str(pd.Timestamp(recent[-60]).date()), str(pd.Timestamp(recent[-1]).date()))

    # Consecutive (non-overlapping) 3-month blocks across the full retained span.
    span = [pd.Timestamp(x).normalize() for x in cal[(cal >= pd.Timestamp("2024-01-01")) & (cal <= latest)]]
    for start_i in range(0, len(span), 63):
        block = span[start_i:start_i + 63]
        if len(block) >= 40:
            name = f"rolling_3m_{str(block[0].date())[:7]}"
            add(name, str(block[0].date()), str(block[-1].date()))
    seen = set()
    uniq = []
    for w in windows:
        key = (w["start"], w["end"])
        if key not in seen:
            seen.add(key)
            uniq.append(w)
    return uniq, unavailable


def build_lab_result(
    *,
    windows: Optional[Sequence[Dict[str, str]]] = None,
    params: StrategyParams = StrategyParams(),
    config: BacktestConfig = BacktestConfig(),
    variants: Sequence[str] = DEFAULT_VARIANTS,
    run_mode: str = "default",
    window_availability: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    selected_windows = list(windows or default_windows())
    window_results: Dict[str, Any] = {}
    for w in selected_windows:
        window_results[w["name"]] = run_backtest_window(
            w["name"], w["start"], w["end"],
            variants=variants, params=params, config=config, cost_models=COST_MODELS,
        )
    sampled = config.date_stride > 1
    verdicts = {v: verdict_for_variant(v, window_results, sampled=sampled) for v in variants}
    ranked = sorted(verdicts.values(), key=lambda x: x["score"], reverse=True)
    best = ranked[0] if ranked else None
    comparison = comparison_answers(window_results, verdicts)
    total_dates = sum(int(w.get("dates_evaluated") or 0) for w in window_results.values())
    ticker_days = sum(int(w.get("ticker_days_evaluated") or 0) for w in window_results.values())
    skipped = [
        {"window": wname, **row}
        for wname, w in window_results.items()
        for row in (w.get("skipped_dates") or [])
    ]
    paper_reason = (
        "Backtest alone is insufficient; walk-forward gate must pass before any proposal document."
    )
    if sampled:
        paper_reason = (
            "SAMPLED RUN (date_stride > 1): debugging only — sampled results can never "
            "produce a paper-shadow proposal. " + paper_reason
        )
    return {
        "kind": "strategy_research_lab",
        "version": VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "research_only": True,
        "status": "RESEARCH_ONLY",
        "mode": run_mode,
        "sampled": sampled,
        "date_count": total_dates,
        "ticker_days_evaluated": ticker_days,
        "skipped_dates": skipped[:100],
        "skipped_date_count": len(skipped),
        "window_availability": window_availability or [],
        "disclaimer": RESEARCH_DISCLAIMER,
        "data_reliability": {
            "price": d.TRUE_POINT_IN_TIME,
            "features": d.RECONSTRUCTED_FROM_PRICE_ONLY,
            "metadata": d.CURRENT_METADATA_APPROXIMATION,
            "fundamentals": d.NOT_RETAINED,
            "stock_lens_gatekeeper_for_backtest": d.NOT_RETAINED,
            "limitations": [
                "2024 coverage is limited to retained backtest cache tickers.",
                "Current sector/theme/profile metadata is an approximation for old dates.",
                "Fundamental, earnings, 13F, options, social, and Gatekeeper labels are not used as historical decision inputs.",
            ],
        },
        "cache_coverage": d.cache_coverage(),
        "params": params.as_dict(),
        "config": config.as_dict(),
        "windows": window_results,
        "variant_verdicts": verdicts,
        "ranked_variants": ranked,
        "best_variant": None if best is None else best["variant"],
        "best_variant_verdict": None if best is None else best["verdict"],
        "comparison_answers": comparison,
        "paper_shadow": {
            "proposal_created": False,
            "status": "NO_VARIANT_READY_FOR_PAPER_SHADOW",
            "sampled": sampled,
            "reason": paper_reason,
        },
        "run_scope": RUN_SCOPES.get(run_mode, "DEFAULT_WINDOWS"),
        "runtime_limitations": [],
        "safety": safety_confirmations(),
    }


def _base_metric(windows: Dict[str, Any], window_name: str, variant: str) -> Dict[str, Any]:
    return ((windows.get(window_name) or {}).get("by_cost") or {}).get("base_cost", {}).get(variant, {})


def _aggregate_expectancy(windows: Dict[str, Any], variant: str) -> Optional[float]:
    vals = []
    weights = []
    for w in windows.values():
        m = w["by_cost"]["base_cost"].get(variant, {})
        n = int(m.get("trade_count") or 0)
        if n and m.get("expectancy") is not None:
            vals.append(float(m["expectancy"]) * n)
            weights.append(n)
    return round(sum(vals) / sum(weights), 6) if weights else None


def comparison_answers(windows: Dict[str, Any], verdicts: Dict[str, Any]) -> Dict[str, Any]:
    exp = {v: _aggregate_expectancy(windows, v) for v in list(DEFAULT_VARIANTS) + list(BENCHMARK_VARIANTS)}
    sniper = exp.get("PROD_SNIPER_CURRENT")
    no_atr = exp.get("SNIPER_NO_ATR_CONTRACTION")
    sector = exp.get("SIMPLE_SECTOR_RS")
    mom = exp.get("SIMPLE_MOM_20_60")
    rand = exp.get("RANDOM_LIQUID")
    recall = exp.get("RECALL_SHADOW_RS_MOMENTUM")
    pullback = exp.get("RECALL_SHADOW_PULLBACK")
    power = exp.get("POWER_TREND_EXTENSION")
    short = exp.get("QQQ_TECH_TACTICAL_SHORT")
    voyager = exp.get("PROD_VOYAGER_CURRENT")
    spy = exp.get("SPY_BUY_HOLD")
    qqq = exp.get("QQQ_BUY_HOLD")

    def yes_no_unknown(cond: Optional[bool]) -> str:
        return "UNKNOWN" if cond is None else ("YES" if cond else "NO")

    return {
        "aggregate_base_cost_expectancy": exp,
        "is_production_sniper_worse_than_simple_baselines": yes_no_unknown(
            None if sniper is None or (sector is None and mom is None) else sniper < max(x for x in (sector, mom) if x is not None)
        ),
        "does_sniper_no_atr_improve_flow_and_returns": yes_no_unknown(
            None if sniper is None or no_atr is None else (
                verdicts.get("SNIPER_NO_ATR_CONTRACTION", {}).get("trade_count", 0)
                > verdicts.get("PROD_SNIPER_CURRENT", {}).get("trade_count", 0)
                and no_atr > sniper
            )
        ),
        "does_recall_shadow_have_backtested_edge": yes_no_unknown(
            None if recall is None or rand is None or spy is None or qqq is None else recall > rand and recall > 0
        ),
        "does_pullback_improve_recall_shadow_entry_quality": yes_no_unknown(
            None if pullback is None or recall is None else pullback > recall
        ),
        "does_power_trend_extension_work_beyond_recent_regime": yes_no_unknown(
            None if power is None else verdicts.get("POWER_TREND_EXTENSION", {}).get("verdict") in {VERDICT_EDGE, VERDICT_READY}
        ),
        "does_qqq_tactical_short_produce_usable_edge": yes_no_unknown(
            None if short is None or rand is None else short > 0 and short > rand
        ),
        "is_voyager_worth_preserving_unchanged": yes_no_unknown(
            None if voyager is None else voyager >= 0 or verdicts.get("PROD_VOYAGER_CURRENT", {}).get("trade_count", 0) < 40
        ),
        "variant_deserving_paper_shadow_proposal": "NONE_BACKTEST_ONLY_REQUIRES_WALK_FORWARD",
    }


def safety_confirmations() -> Dict[str, bool]:
    return {
        "no_live_trading": True,
        "no_broker_orders": True,
        "no_paper_signals": True,
        "no_trade_proposals": True,
        "no_execution_imports": True,
        "no_governance_imports": True,
        "no_live_capital_changes": True,
        "no_production_threshold_changes": True,
        "no_gatekeeper_changes": True,
        "no_veto_council_changes": True,
        "no_historical_evidence_mutation": True,
        "short_a_remains_frozen": True,
    }


def render_text(result: Dict[str, Any]) -> List[str]:
    lines = [
        f"STRATEGY RESEARCH LAB - {result['generated_at']}",
        RESEARCH_DISCLAIMER,
        "",
        f"best_variant={result.get('best_variant')} verdict={result.get('best_variant_verdict')}",
        f"paper_shadow={result['paper_shadow']['status']}",
        f"run_scope={result.get('run_scope', 'DEFAULT_WINDOWS')} mode={result.get('mode', 'default')} sampled={result.get('sampled', False)}",
        f"date_count={result.get('date_count')} ticker_days_evaluated={result.get('ticker_days_evaluated')} skipped_dates={result.get('skipped_date_count', 0)}",
        "",
        "VARIANT SUMMARY (base cost, aggregate):",
        f"{'variant':34s} {'verdict':30s} {'trades':>7s} {'score':>10s}",
    ]
    for row in result.get("ranked_variants") or []:
        lines.append(f"{row['variant']:34s} {row['verdict']:30s} {row['trade_count']:7d} {row['score']:10.4f}")
    lines += ["", "WINDOWS:"]
    for wname, w in result.get("windows", {}).items():
        lines.append(f"  {wname}: {w['start']} -> {w['end']} ({w['trading_dates']} signal dates)")
    lines += ["", "COMPARISON ANSWERS:"]
    for k, v in (result.get("comparison_answers") or {}).items():
        if k == "aggregate_base_cost_expectancy":
            continue
        lines.append(f"  {k}: {v}")
    return lines


def _summary_table(result: Dict[str, Any]) -> List[str]:
    rows = [
        "| Variant | Verdict | Trades | Avg Exp | Rel SPY | Rel QQQ | Max DD |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    windows = result.get("windows") or {}
    for row in result.get("ranked_variants") or []:
        variant = row["variant"]
        vals = []
        for w in windows.values():
            m = w["by_cost"]["base_cost"].get(variant, {})
            if m.get("trade_count"):
                vals.append(m)
        n = sum(int(m.get("trade_count") or 0) for m in vals)
        avg = _aggregate_expectancy(windows, variant)
        rel_spy = statistics.mean([float(m["rel_spy"]) for m in vals if m.get("rel_spy") is not None]) if vals else None
        rel_qqq = statistics.mean([float(m["rel_qqq"]) for m in vals if m.get("rel_qqq") is not None]) if vals else None
        max_dd = min([float(m.get("max_drawdown") or 0.0) for m in vals], default=0.0)
        rows.append(
            f"| {variant} | {row['verdict']} | {n} | {_pct(avg)} | "
            f"{_pct(rel_spy)} | {_pct(rel_qqq)} | {_pct(max_dd)} |"
        )
    return rows


def render_doc(result: Dict[str, Any]) -> str:
    lines = [
        "# Strategy Research Lab Results",
        "",
        f"Generated: {result['generated_at']}",
        "",
        RESEARCH_DISCLAIMER,
        "",
        f"Run scope: {result.get('run_scope', 'DEFAULT_WINDOWS')} · mode: `{result.get('mode', 'default')}` · "
        f"sampled: `{result.get('sampled', False)}` · dates: {result.get('date_count')} · "
        f"ticker-days: {result.get('ticker_days_evaluated')} · skipped dates: {result.get('skipped_date_count', 0)}",
        "",
        "## Data Reliability",
        "",
        "- Price bars: TRUE_POINT_IN_TIME when sliced to the as-of date.",
        "- Features: RECONSTRUCTED_FROM_PRICE_ONLY.",
        "- Sector/theme/profile metadata: CURRENT_METADATA_APPROXIMATION for old dates.",
        "- Stock Lens, Gatekeeper, Alpha board, fundamentals, earnings, 13F, options, social, and short-interest labels are not used as historical decision inputs unless dated history exists.",
        "",
        "## Results Table",
        "",
        *_summary_table(result),
        "",
        "## Window Details",
        "",
    ]
    for wname, w in result.get("windows", {}).items():
        lines.append(f"### {wname}")
        lines.append("")
        lines.append(f"- Dates: {w['start']} to {w['end']}")
        lines.append(f"- Signal dates: {w['trading_dates']} (sampled: {w.get('sampled', False)}, "
                     f"ticker-days: {w.get('ticker_days_evaluated', 'n/a')}, "
                     f"skipped: {w.get('skipped_date_count', 0)})")
        lines.append("")
        lines.append("| Variant | Trades | Win Rate | Avg | Rel SPY | Rel QQQ | Max DD | Stop | Target | Exp no-cost | Exp high-cost |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for variant in list(DEFAULT_VARIANTS) + list(BENCHMARK_VARIANTS):
            m = w["by_cost"]["base_cost"].get(variant, {})
            m_no = (w["by_cost"].get("no_cost") or {}).get(variant, {})
            m_hi = (w["by_cost"].get("high_cost") or {}).get(variant, {})
            lines.append(
                f"| {variant} | {m.get('trade_count', 0)} | {_pct(m.get('win_rate'))} | "
                f"{_pct(m.get('average_return'))} | {_pct(m.get('rel_spy'))} | "
                f"{_pct(m.get('rel_qqq'))} | {_pct(m.get('max_drawdown'))} | "
                f"{_pct(m.get('stop_hit_rate'))} | {_pct(m.get('target_hit_rate'))} | "
                f"{_pct(m_no.get('expectancy'))} | {_pct(m_hi.get('expectancy'))} |"
            )
        lines.append("")
        active = [
            (variant, w["by_cost"]["base_cost"].get(variant, {}))
            for variant in DEFAULT_VARIANTS
            if (w["by_cost"]["base_cost"].get(variant) or {}).get("trade_count")
        ]
        if active:
            lines.append("Trade counts by month / regime / theme (base cost):")
            lines.append("")
            for variant, m in active:
                months = ", ".join(f"{k}:{v}" for k, v in (m.get("monthly_trade_counts") or {}).items())
                regimes = ", ".join(
                    f"{k}={v['trade_count']}@{_pct(v['average_return'])}"
                    for k, v in (m.get("regime_breakdown") or {}).items()
                )
                themes = ", ".join(f"{k}:{v}" for k, v in (m.get("theme_concentration") or [])[:5])
                lines.append(f"- {variant}: months [{months}] · regimes [{regimes}] · themes [{themes}]")
            lines.append("")
    if result.get("window_availability"):
        lines += ["## Window Availability", ""]
        for row in result["window_availability"]:
            lines.append(f"- {row['name']} ({row['requested']}): UNAVAILABLE — {row['reason']}")
        lines.append("")
    if result.get("runtime_limitations"):
        lines += ["## Runtime Limitations", ""]
        lines.extend(f"- {x}" for x in result.get("runtime_limitations") or [])
        lines.append("")
    lines += [
        "## Comparison Answers",
        "",
    ]
    for key, value in (result.get("comparison_answers") or {}).items():
        if key == "aggregate_base_cost_expectancy":
            continue
        lines.append(f"- {key}: {value}")
    comp = result.get("sampled_vs_exact_comparison")
    if comp:
        lines += [
            "",
            "## Sampled vs Exact Comparison",
            "",
            f"Prior sampled artifact: {comp.get('prior_generated_at')} ({comp.get('prior_run_scope')})",
            f"Sampled run misleading: **{comp.get('sampled_run_misleading')}**",
            "",
        ]
        lines.extend(f"- {x}" for x in comp.get("misleading_reasons") or [])
        lines += [
            "",
            "| Variant | Sampled trades | Exact trades | Sampled exp | Exact exp | Sampled verdict | Exact verdict |",
            "|---|---:|---:|---:|---:|---|---|",
        ]
        for v, row in (comp.get("per_variant") or {}).items():
            lines.append(
                f"| {v} | {row.get('sampled_trades')} | {row.get('exact_trades')} | "
                f"{_pct(row.get('sampled_expectancy'))} | {_pct(row.get('exact_expectancy'))} | "
                f"{row.get('sampled_verdict')} | {row.get('exact_verdict')} |"
            )
    lines += [
        "",
        "## Paper-Shadow Decision",
        "",
        result["paper_shadow"]["status"],
        "",
        "No paper signals, trade proposals, strategy registry edits, execution edits, Gatekeeper edits, Veto Council edits, live-capital edits, or historical evidence mutation were made.",
    ]
    return "\n".join(lines) + "\n"


def compare_to_prior_sampled(result: Dict[str, Any], prior: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Task 4: exact-vs-sampled comparison against the previous lab artifact.

    Only meaningful when the prior artifact was sampled (stride > 1) or a
    quick run. Differences in universe/cap setup are reported alongside the
    sampling difference rather than hidden."""
    prior_cfg = prior.get("config") or {}
    prior_sampled = int(prior_cfg.get("date_stride") or 1) > 1 or str(prior.get("run_scope", "")).startswith("QUICK")
    if not prior_sampled:
        return None
    prior_windows = prior.get("windows") or {}
    exact_windows = result.get("windows") or {}
    per_variant: Dict[str, Any] = {}
    for v in list(DEFAULT_VARIANTS):
        s_verdict = (prior.get("variant_verdicts") or {}).get(v) or {}
        e_verdict = (result.get("variant_verdicts") or {}).get(v) or {}
        per_variant[v] = {
            "sampled_trades": s_verdict.get("trade_count"),
            "exact_trades": e_verdict.get("trade_count"),
            "sampled_expectancy": _aggregate_expectancy(prior_windows, v) if prior_windows else None,
            "exact_expectancy": _aggregate_expectancy(exact_windows, v),
            "sampled_verdict": s_verdict.get("verdict"),
            "exact_verdict": e_verdict.get("verdict"),
        }
    s_rank = [r["variant"] for r in (prior.get("ranked_variants") or [])]
    e_rank = [r["variant"] for r in (result.get("ranked_variants") or [])]
    misleading_reasons: List[str] = []
    if s_rank and e_rank and s_rank[0] != e_rank[0]:
        misleading_reasons.append(f"top-ranked variant changed: sampled={s_rank[0]} exact={e_rank[0]}")
    for v, row in per_variant.items():
        se, ee = row["sampled_expectancy"], row["exact_expectancy"]
        if se is not None and ee is not None and (se > 0) != (ee > 0):
            misleading_reasons.append(f"{v}: expectancy sign flipped sampled={se:+.4f} exact={ee:+.4f}")
        st, et = row["sampled_trades"], row["exact_trades"]
        if st is not None and et is not None and st and et and (et / max(1, st)) >= 3:
            misleading_reasons.append(f"{v}: sampled run missed most trades ({st} vs {et})")
    return {
        "prior_generated_at": prior.get("generated_at"),
        "prior_run_scope": prior.get("run_scope"),
        "setup_differences": {
            "date_stride": {"sampled": prior_cfg.get("date_stride"), "exact": (result.get("config") or {}).get("date_stride")},
            "universe_mode": {"sampled": prior_cfg.get("universe_mode"), "exact": (result.get("config") or {}).get("universe_mode")},
            "universe_cap": {"sampled": prior_cfg.get("universe_cap"), "exact": (result.get("config") or {}).get("universe_cap")},
            "max_signals_per_variant_day": {"sampled": prior_cfg.get("max_signals_per_variant_day"), "exact": (result.get("config") or {}).get("max_signals_per_variant_day")},
        },
        "per_variant": per_variant,
        "ranking_sampled": s_rank,
        "ranking_exact": e_rank,
        "sampled_run_misleading": bool(misleading_reasons),
        "misleading_reasons": misleading_reasons,
    }


def write_outputs(result: Dict[str, Any], *, run_mode: str = "default") -> None:
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_TXT.parent.mkdir(parents=True, exist_ok=True)
    OUT_DOC.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(result, indent=2, default=str)
    text = "\n".join(render_text(result)) + "\n"
    OUT_JSON.write_text(payload, encoding="utf-8")
    OUT_TXT.write_text(text, encoding="utf-8")
    OUT_DOC.write_text(render_doc(result), encoding="utf-8")
    extra = MODE_OUTPUTS.get(run_mode)
    if extra:
        if extra.get("json"):
            extra["json"].write_text(payload, encoding="utf-8")
        if extra.get("txt"):
            extra["txt"].write_text(text, encoding="utf-8")
        if extra.get("doc"):
            extra["doc"].write_text(render_doc(result), encoding="utf-8")


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Strategy Research Lab backtest engine (research-only)")
    ap.add_argument("--universe-cap", type=int, default=140)
    ap.add_argument("--universe-mode", default="research_core")
    ap.add_argument("--max-signals-per-day", type=int, default=5)
    ap.add_argument("--date-stride", type=int, default=1)
    ap.add_argument(
        "--mode",
        choices=["quick", "exact_recent_60", "exact_ytd", "exact_full", "default"],
        default="default",
        help="quick = stride-sampled debugging only; exact_* = every trading date, decision-grade",
    )
    ap.add_argument("--quick", action="store_true", help="alias for --mode quick")
    args = ap.parse_args(argv)

    run_mode = "quick" if args.quick else args.mode
    stride = max(1, int(args.date_stride))
    if run_mode == "quick":
        stride = max(3, stride)
    elif run_mode.startswith("exact"):
        stride = 1

    config = BacktestConfig(
        universe_mode=args.universe_mode,
        universe_cap=args.universe_cap,
        max_signals_per_variant_day=args.max_signals_per_day,
        date_stride=stride,
    )
    params = StrategyParams()
    windows = None
    window_availability: Optional[List[Dict[str, str]]] = None
    if run_mode in ("quick", "exact_recent_60"):
        windows = [w for w in default_windows() if w["name"] == "recent_60_trading_days"]
    elif run_mode == "exact_ytd":
        windows = [w for w in default_windows() if w["name"] == "2026_ytd"]
    elif run_mode == "exact_full":
        windows, window_availability = full_windows()

    prior: Optional[Dict[str, Any]] = None
    if run_mode == "exact_recent_60" and OUT_JSON.exists():
        try:
            prior = json.loads(OUT_JSON.read_text(encoding="utf-8"))
        except Exception:
            prior = None

    result = build_lab_result(
        windows=windows, params=params, config=config,
        run_mode=run_mode, window_availability=window_availability,
    )
    if run_mode == "quick":
        result.setdefault("runtime_limitations", []).append(
            "QUICK mode is stride-sampled and for debugging only. Do not treat it as decision-grade evidence; rerun with --mode exact_full."
        )
    if prior is not None:
        comparison = compare_to_prior_sampled(result, prior)
        if comparison is not None:
            result["sampled_vs_exact_comparison"] = comparison
    write_outputs(result, run_mode=run_mode)
    print("\n".join(render_text(result)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

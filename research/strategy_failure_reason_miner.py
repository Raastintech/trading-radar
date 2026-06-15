#!/usr/bin/env python3
"""
research/strategy_failure_reason_miner.py - Phase 1H.4 failure-reason mining.

Research-only gate-level autopsy of the Strategy Lab variants. For every
(variant, ticker, as-of date) candidate it traces each gate of the variant's
signal function, classifies the outcome into accepted/rejected x winner/loser
using exit-free forward returns, and scores every gate by what it actually
blocked (avoided loss vs opportunity cost, sole-blocker counterfactuals).

It writes cache/log/doc artifacts only and never imports broker, execution,
governance, paper-signal, or live-capital modules. Forward returns are used
ONLY to label outcomes after the fact; every decision field is as-of.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from research import strategy_lab_data as d  # noqa: E402
from research import strategy_lab_portfolio as portfolio  # noqa: E402
from research import strategy_lab_regime as regime  # noqa: E402
from research import strategy_research_lab as lab  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "cache" / "research"
LOGS = ROOT / "logs"
DOCS = ROOT / "docs" / "research"

OUT_JSON = CACHE / "strategy_failure_reason_miner_latest.json"
OUT_TXT = LOGS / "strategy_failure_reason_miner_latest.txt"
OUT_DOC = DOCS / "STRATEGY_FAILURE_REASON_MINER.md"
OUT_LOSER_JSON = CACHE / "accepted_loser_pattern_latest.json"
OUT_LOSER_DOC = DOCS / "ACCEPTED_LOSER_PATTERN_REPORT.md"
OUT_WINNER_JSON = CACHE / "rejected_winner_pattern_latest.json"
OUT_WINNER_DOC = DOCS / "REJECTED_WINNER_PATTERN_REPORT.md"
OUT_KILL_REPAIR_DOC = DOCS / "STRATEGY_KILL_AND_REPAIR_LIST.md"
COUNTERFACTUAL_JSON = CACHE / "filter_replacement_counterfactual_latest.json"
CORRECTION_JSON = CACHE / "strategy_lab_correction_strategy_latest.json"

VERSION = "STRATEGY_FAILURE_REASON_MINER_V1"

EVAL_VARIANTS = (
    "PROD_SNIPER_CURRENT",
    "SNIPER_NO_ATR_CONTRACTION",
    "PROD_VOYAGER_CURRENT",
    "RECALL_SHADOW_RS_MOMENTUM",
    "RECALL_SHADOW_PULLBACK",
    "POWER_TREND_EXTENSION",
    "CORRECTION_LEADER_RECLAIM",
    "QQQ_TECH_TACTICAL_SHORT",
)
SHORT_VARIANTS = {"QQQ_TECH_TACTICAL_SHORT"}

# Outcome labeling (exit-free forward returns; strategy-perspective signed).
LABEL_HORIZON = 10
WIN_THRESHOLD = 0.03
LOSS_THRESHOLD = -0.03

ACCEPTED_WINNER = "ACCEPTED_WINNER"
ACCEPTED_LOSER = "ACCEPTED_LOSER"
ACCEPTED_NEUTRAL = "ACCEPTED_NEUTRAL"
REJECTED_WINNER = "REJECTED_WINNER"
REJECTED_LOSER = "REJECTED_LOSER"
REJECTED_NEUTRAL = "REJECTED_NEUTRAL"
UNKNOWN_NOT_MATURED = "UNKNOWN_NOT_MATURED"

# Gate verdicts.
KEEP_HARD_BLOCK = "KEEP_HARD_BLOCK"
KEEP_SOFT_WARNING = "KEEP_SOFT_WARNING"
OVERBLOCKS_WINNERS = "OVERBLOCKS_WINNERS"
MISSING_PROTECTION = "MISSING_PROTECTION"
NO_SIGNAL = "NO_SIGNAL"
NEED_MORE_DATA = "NEED_MORE_DATA"

GATE_MIN_MATURED = 25
SAMPLES_PER_CLASS = 20

RISK_REGIMES = {
    regime.RISK_OFF,
    regime.MARKET_CORRECTION,
    regime.TECH_LED_CORRECTION,
    regime.HIGH_VOLATILITY,
}

NOT_MEASURABLE_PATTERNS = {
    "earnings_soon": "earnings calendar history is not retained point-in-time (NOT_RETAINED)",
    "high_spread_low_liquidity_quote": "no point-in-time quote spread retained; only avg dollar-volume proxies exist",
    "gatekeeper_blocked_but_price_advanced": "Gatekeeper verdicts are not retained point-in-time inside the lab; see Phase 1G.13 for the production-snapshot autopsy",
    "lacked_lens_but_price_behaved": "Stock Lens artifacts are not retained point-in-time inside the lab",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


_f = lab._f
_opt = lab._opt


def _pct(value: Any) -> str:
    return "n/a" if value is None else f"{float(value) * 100:+.2f}%"


def _mean(vals: Sequence[float]) -> Optional[float]:
    return round(statistics.mean(vals), 6) if vals else None


def _vol_ratio(f: Dict[str, Any]) -> float:
    return _f(f.get("volume")) / max(_f(f.get("avg_vol20"), 1.0), 1.0)


# ---------------------------------------------------------------------------
# Gate tracing
# ---------------------------------------------------------------------------
# Each gate fn returns (value, passed, evaluated). Gates are evaluated on as-of
# features only; trace_candidate never touches forward data. `state` carries
# derived values (archetype, score, correction rs) between gates of a variant.

GateFn = Callable[[Dict[str, Any], lab.StrategyParams, Dict[str, Any], Dict[str, Any]], Tuple[Any, bool, bool]]


@dataclass(frozen=True)
class Gate:
    name: str
    fn: GateFn
    expensive: bool = False


def _g_liquidity(f, p, c, s):
    return (_f(f.get("avg_dvol20")), bool(lab._liquid(f, p)), True)


def _g_bars(n: int) -> GateFn:
    def fn(f, p, c, s):
        return (f.get("bars", 0), bool(f.get("bars", 0) >= n), True)
    return fn


def _g_sniper_universe(f, p, c, s):
    return (f["ticker"], f["ticker"] in d.SNIPER_LARGE_CAP_UNIVERSE, True)


def _g_first_breakout(f, p, c, s):
    return (bool(f.get("first_breakout")), bool(f.get("first_breakout")), True)


def _g_volume_confirm(f, p, c, s):
    vr = _vol_ratio(f)
    return (round(vr, 4), vr >= 1.4, True)


def _g_atr_contraction(f, p, c, s):
    atr_c = _opt(f.get("atr_contraction"))
    return (atr_c, atr_c is not None and atr_c < 0.85, True)


def _g_trend_ma50(f, p, c, s):
    ok = f.get("above_ma50") is True and f.get("ma50_rising") is True
    return ({"above_ma50": f.get("above_ma50"), "ma50_rising": f.get("ma50_rising")}, ok, True)


def _g_rs10_positive(f, p, c, s):
    return (f.get("rs10_spy"), _f(f.get("rs10_spy")) > 0, True)


def _g_spy_regime(f, p, c, s):
    return (f.get("spy_above_ma200"), f.get("spy_above_ma200") is not False, True)


def _g_sniper_score(f, p, c, s):
    score = lab._sniper_score(f)
    s["score"] = score
    return (round(score, 2), score >= 70, True)


def _g_price_min(f, p, c, s):
    return (f.get("price"), _f(f.get("price")) >= 5.0, True)


def _g_voy_ma_available(f, p, c, s):
    ma200 = _opt(f.get("ma200"))
    ma50 = _opt(f.get("ma50"))
    return ({"ma50": ma50, "ma200": ma200}, bool(ma200) and bool(ma50), True)


def _g_voy_ma200_floor(f, p, c, s):
    ma200 = _opt(f.get("ma200"))
    if not ma200:
        return (None, False, False)
    ratio = _f(f.get("price")) / ma200
    return (round(ratio, 4), ratio >= 0.92, True)


def _g_voy_ext_ma50(f, p, c, s):
    ma50 = _opt(f.get("ma50"))
    if not ma50:
        return (None, False, False)
    ext = (_f(f.get("price")) - ma50) / ma50
    return (round(ext, 4), ext <= 0.12, True)


def _g_voy_rs50(f, p, c, s):
    return (f.get("rs50_spy"), _f(f.get("rs50_spy")) > 0, True)


def _g_voy_dvol_ratio(f, p, c, s):
    return (f.get("dvol_ratio"), _f(f.get("dvol_ratio")) >= 0.85, True)


def _g_voy_archetype(f, p, c, s):
    archetype = lab._voyager_archetype(f)
    s["archetype"] = archetype
    return (archetype, archetype is not None, True)


def _g_voy_up_vol(f, p, c, s):
    archetype = s.get("archetype")
    if archetype is None:
        return (None, False, False)
    upv = _f(f.get("up_vol_ratio"), 1.0)
    ok = upv >= 0.8 if archetype == "TREND_PULLBACK" else upv >= 1.0
    return (round(upv, 4), ok, True)


def _g_voy_score(f, p, c, s):
    archetype = s.get("archetype")
    if archetype is None:
        return (None, False, False)
    score = lab._voyager_score(f, archetype)
    s["score"] = score
    return (round(score, 2), score >= 65, True)


def _rs_max(f) -> float:
    return max(_f(f.get("rs20_spy"), -9.0), _f(f.get("sector_rs20"), -9.0))


def _g_rsmom_rs_floor(f, p, c, s):
    rs = _rs_max(f)
    s["rs"] = rs
    return (round(rs, 4), rs >= p.sector_rs_threshold, True)


def _g_rsmom_mom20(f, p, c, s):
    return (f.get("r20"), _f(f.get("r20")) >= p.momentum_20_threshold, True)


def _g_rsmom_mom60(f, p, c, s):
    return (f.get("r60"), _f(f.get("r60")) >= p.momentum_60_threshold, True)


def _g_rsmom_ext_cap(f, p, c, s):
    ext = _f(f.get("ext_ema20"), 9.0)
    return (round(ext, 4), ext <= p.extension_cap, True)


def _g_above_ema20_not_false(f, p, c, s):
    return (f.get("above_ema20"), f.get("above_ema20") is not False, True)


def _g_above_ema20_true(f, p, c, s):
    return (f.get("above_ema20"), f.get("above_ema20") is True, True)


def _g_pull_ext_available(f, p, c, s):
    ext = _opt(f.get("ext_ema20"))
    s["ext"] = ext
    return (ext, ext is not None, True)


def _g_pull_rs_floor(f, p, c, s):
    rs = _rs_max(f)
    s["rs"] = rs
    return (round(rs, 4), rs >= max(0.03, p.sector_rs_threshold * 0.6), True)


def _g_pull_band(f, p, c, s):
    ext = _opt(f.get("ext_ema20"))
    if ext is None:
        return (None, False, False)
    return (round(ext, 4), -p.pullback_depth <= ext <= 0.04, True)


def _g_pull_r5(f, p, c, s):
    return (f.get("r5"), _f(f.get("r5")) >= -0.02, True)


def _g_pwr_ext_band(f, p, c, s):
    ext = _f(f.get("ext_ema20"))
    return (round(ext, 4), 0.15 <= ext <= max(p.extension_cap, 0.35), True)


def _g_pwr_rs_floor(f, p, c, s):
    rs = _rs_max(f)
    s["rs"] = rs
    return (round(rs, 4), rs >= p.sector_rs_threshold, True)


def _g_pwr_vol_floor(f, p, c, s):
    vol = _f(f.get("vol_expansion"), 1.0)
    return (round(vol, 4), vol >= p.volume_expansion_threshold, True)


def _g_pwr_membership(f, p, c, s):
    ok = lab._is_power_theme(f) or str(f.get("sector")) in {"Technology", "Industrials", "Energy"}
    return ({"theme": f.get("theme"), "sector": f.get("sector")}, ok, True)


def _g_pwr_not_parabolic(f, p, c, s):
    ok = not (_f(f.get("r5")) > 0.30 or _f(f.get("r10")) > 0.55)
    return ({"r5": f.get("r5"), "r10": f.get("r10")}, ok, True)


def _g_clr_regime(f, p, c, s):
    label = (c.get("REGIME") or {}).get("label")
    s["regime_label"] = label
    return (label, label in lab.CORRECTION_RECLAIM_REGIMES, True)


def _g_clr_rs(f, p, c, s):
    rs = lab._correction_rs(f, c, int(p.correction_rs_lookback))
    s["rs"] = rs
    return (round(rs, 4), rs >= 0.01, True)


def _g_clr_price(f, p, c, s):
    return (f.get("price"), _f(f.get("price")) > 0, True)


def _g_clr_pullback(f, p, c, s):
    dd20 = _f(f.get("drawdown_from_high20"))
    dd60 = _f(f.get("drawdown_from_high60"))
    ok = not (dd60 < -abs(p.correction_max_pullback) or dd20 < -0.20)
    return ({"dd20": round(dd20, 4), "dd60": round(dd60, 4)}, ok, True)


def _market_dd(c: Dict[str, Any]) -> float:
    spy = c.get("SPY") or {}
    qqq = c.get("QQQ") or {}
    return min(
        _f(spy.get("drawdown_from_high60")),
        _f(qqq.get("drawdown_from_high60")),
        _f(spy.get("drawdown_from_high20")),
        _f(qqq.get("drawdown_from_high20")),
    )


def _g_clr_vs_market(f, p, c, s):
    market_dd = _market_dd(c)
    s["market_dd"] = market_dd
    dd60 = _f(f.get("drawdown_from_high60"))
    ok = not (market_dd < 0 and dd60 < market_dd - 0.02)
    return ({"dd60": round(dd60, 4), "market_dd": round(market_dd, 4)}, ok, True)


def _g_clr_not_new_low(f, p, c, s):
    price = _f(f.get("price"))
    low20 = _f(f.get("low20"))
    ok = not (low20 > 0 and price <= low20 * 1.02)
    return ({"price": price, "low20": low20}, ok, True)


def _g_clr_reclaim(f, p, c, s):
    rec = lab._ema_reclaim_state(f["ticker"], f["asof"], int(p.correction_ema_reclaim))
    s["reclaim"] = rec
    return (bool(rec and rec.get("reclaim")), bool(rec and rec.get("reclaim")), True)


def _g_clr_ma200(f, p, c, s):
    ma200 = _opt(f.get("ma200"))
    price = _f(f.get("price"))
    ok = not (ma200 and price < ma200 * 0.96)
    return ({"price": price, "ma200": ma200}, ok, True)


def _g_clr_ma50(f, p, c, s):
    ma50 = _opt(f.get("ma50"))
    price = _f(f.get("price"))
    ok = not (ma50 and price < ma50 * 0.92)
    return ({"price": price, "ma50": ma50}, ok, True)


def _g_clr_volume(f, p, c, s):
    volume_ratio = _vol_ratio(f)
    vol_expansion = _f(f.get("vol_expansion"), 1.0)
    dryup = vol_expansion <= p.correction_volume_dryup_threshold
    s["volume_ratio"] = volume_ratio
    s["dryup"] = dryup
    ok = volume_ratio >= p.correction_volume_expansion_threshold or dryup
    return ({"volume_ratio": round(volume_ratio, 4), "dryup": dryup}, ok, True)


def _g_clr_not_extended(f, p, c, s):
    ext = _f(f.get("ext_ema20"))
    dd20 = _f(f.get("drawdown_from_high20"))
    ok = not (ext > 0.12 and dd20 > -0.05)
    return ({"ext": round(ext, 4), "dd20": round(dd20, 4)}, ok, True)


def _g_clr_not_parabolic(f, p, c, s):
    r10 = _f(f.get("r10"))
    dd20 = _f(f.get("drawdown_from_high20"))
    ok = not (r10 > 0.30 and dd20 > -0.08)
    return ({"r10": r10, "dd20": round(dd20, 4)}, ok, True)


def _g_short_membership(f, p, c, s):
    sector = str(f.get("sector") or "")
    theme = str(f.get("theme") or "")
    ok = sector == "Technology" or theme in d.POWER_THEMES or f["ticker"] in {"QQQ", "SMH", "XLK"}
    return ({"sector": sector, "theme": theme}, ok, True)


def _g_short_market_weak(f, p, c, s):
    qqq = c.get("QQQ") or {}
    smh = c.get("SMH") or {}
    xlk = c.get("XLK") or {}
    ok = (
        _f(qqq.get("r10")) < -0.01
        or _f(smh.get("r10")) < -0.02
        or _f(xlk.get("r10")) < -0.01
        or qqq.get("above_ema20") is False
    )
    return ({"qqq_r10": qqq.get("r10"), "smh_r10": smh.get("r10")}, ok, True)


def _g_short_failed_leader(f, p, c, s):
    ok = _f(f.get("r60")) > 0.10 and (f.get("above_ema20") is False or _f(f.get("r10")) < -0.04)
    return ({"r60": f.get("r60"), "r10": f.get("r10")}, ok, True)


def _g_short_tech_weak(f, p, c, s):
    ok = _f(f.get("rs20_qqq")) < -0.03 or _f(f.get("r20")) < -0.08
    return ({"rs20_qqq": f.get("rs20_qqq"), "r20": f.get("r20")}, ok, True)


def _g_lrr_leadership_rs60(f, p, c, s):
    rs60 = max(_f(f.get("rs60_spy"), -9.0), _f(f.get("rs60_qqq"), -9.0))
    s["rs60"] = rs60
    return (round(rs60, 4), rs60 >= lab.LRR_RS60_MIN, True)


def _g_lrr_momentum_r60(f, p, c, s):
    return (f.get("r60"), _f(f.get("r60")) >= lab.LRR_R60_MIN, True)


def _g_lrr_reset_band(f, p, c, s):
    dd20 = _f(f.get("drawdown_from_high20"))
    return (round(dd20, 4), -lab.LRR_RESET_MAX <= dd20 <= -lab.LRR_RESET_MIN, True)


def _g_lrr_trend_ma200(f, p, c, s):
    return (f.get("above_ma200"), f.get("above_ma200") is not False, True)


def _g_lrr_trend_ma50(f, p, c, s):
    ma50 = _opt(f.get("ma50"))
    price = _f(f.get("price"))
    return ({"price": price, "ma50": ma50}, not (ma50 and price < ma50 * 0.92), True)


def _g_lrr_no_crash(f, p, c, s):
    return (f.get("r10"), _f(f.get("r10")) >= lab.LRR_CRASH_R10, True)


def _g_lrr_no_parabolic(f, p, c, s):
    return (f.get("r20"), _f(f.get("r20")) <= lab.LRR_PARABOLIC_R20, True)


def _g_lrr_reclaim(f, p, c, s):
    rec = lab._lrr_reclaim(f["ticker"], f["asof"])
    s["lrr_reclaim"] = rec
    return (rec.get("span") if rec else None, rec is not None, True)


def _g_lrr_regime_allowed(f, p, c, s):
    label = (c.get("REGIME") or {}).get("label")
    s["regime_label"] = label
    return (label, label in lab.LRR_ALLOWED_REGIMES, True)


_LRR_BASE_GATES = (
    Gate("liquidity_floor", _g_liquidity),
    Gate("min_bars_75", _g_bars(75)),
    Gate("prior_leadership_rs60", _g_lrr_leadership_rs60),
    Gate("prior_momentum_r60", _g_lrr_momentum_r60),
    Gate("reset_band_5_18", _g_lrr_reset_band),
    Gate("trend_intact_ma200", _g_lrr_trend_ma200),
    Gate("trend_intact_ma50_92", _g_lrr_trend_ma50),
    Gate("no_crash_r10", _g_lrr_no_crash),
    Gate("no_parabolic_r20", _g_lrr_no_parabolic),
    Gate("ema_reclaim_close_strength", _g_lrr_reclaim, expensive=True),
)


GATE_SPECS: Dict[str, Tuple[Gate, ...]] = {
    "LRR_REGIME_GATED": (Gate("regime_allowed_lrr", _g_lrr_regime_allowed),) + _LRR_BASE_GATES,
    "LEADER_RESET_RECLAIM": _LRR_BASE_GATES,
    "PROD_SNIPER_CURRENT": (
        Gate("sniper_universe", _g_sniper_universe),
        Gate("liquidity_floor", _g_liquidity),
        Gate("min_bars_75", _g_bars(75)),
        Gate("first_breakout", _g_first_breakout),
        Gate("volume_confirm_1_4x", _g_volume_confirm),
        Gate("atr_contraction_lt_0_85", _g_atr_contraction),
        Gate("trend_ma50_rising", _g_trend_ma50),
        Gate("rs10_spy_positive", _g_rs10_positive),
        Gate("spy_above_ma200_regime", _g_spy_regime),
        Gate("sniper_score_70", _g_sniper_score),
    ),
    "SNIPER_NO_ATR_CONTRACTION": (
        Gate("sniper_universe", _g_sniper_universe),
        Gate("liquidity_floor", _g_liquidity),
        Gate("min_bars_75", _g_bars(75)),
        Gate("first_breakout", _g_first_breakout),
        Gate("volume_confirm_1_4x", _g_volume_confirm),
        Gate("trend_ma50_rising", _g_trend_ma50),
        Gate("rs10_spy_positive", _g_rs10_positive),
        Gate("spy_above_ma200_regime", _g_spy_regime),
    ),
    "PROD_VOYAGER_CURRENT": (
        Gate("liquidity_floor", _g_liquidity),
        Gate("min_bars_260", _g_bars(260)),
        Gate("price_min_5", _g_price_min),
        Gate("ma_available", _g_voy_ma_available),
        Gate("ma200_floor_0_92", _g_voy_ma200_floor),
        Gate("ma50_extension_cap_12", _g_voy_ext_ma50),
        Gate("rs50_spy_positive", _g_voy_rs50),
        Gate("dvol_ratio_0_85", _g_voy_dvol_ratio),
        Gate("archetype_match", _g_voy_archetype),
        Gate("up_vol_ratio_floor", _g_voy_up_vol),
        Gate("voyager_score_65", _g_voy_score),
    ),
    "RECALL_SHADOW_RS_MOMENTUM": (
        Gate("liquidity_floor", _g_liquidity),
        Gate("min_bars_75", _g_bars(75)),
        Gate("rs_floor", _g_rsmom_rs_floor),
        Gate("momentum_20", _g_rsmom_mom20),
        Gate("momentum_60", _g_rsmom_mom60),
        Gate("extension_cap", _g_rsmom_ext_cap),
        Gate("above_ema20_not_false", _g_above_ema20_not_false),
    ),
    "RECALL_SHADOW_PULLBACK": (
        Gate("liquidity_floor", _g_liquidity),
        Gate("min_bars_75", _g_bars(75)),
        Gate("ext_available", _g_pull_ext_available),
        Gate("momentum_60", _g_rsmom_mom60),
        Gate("rs_floor_pullback", _g_pull_rs_floor),
        Gate("pullback_band", _g_pull_band),
        Gate("r5_floor", _g_pull_r5),
        Gate("above_ema20_true", _g_above_ema20_true),
    ),
    "POWER_TREND_EXTENSION": (
        Gate("liquidity_floor", _g_liquidity),
        Gate("min_bars_75", _g_bars(75)),
        Gate("extension_band_15_35", _g_pwr_ext_band),
        Gate("rs_floor", _g_pwr_rs_floor),
        Gate("volume_expansion_floor", _g_pwr_vol_floor),
        Gate("theme_sector_membership", _g_pwr_membership),
        Gate("not_parabolic", _g_pwr_not_parabolic),
    ),
    "CORRECTION_LEADER_RECLAIM": (
        Gate("regime_allowed", _g_clr_regime),
        Gate("liquidity_floor", _g_liquidity),
        Gate("min_bars_75", _g_bars(75)),
        Gate("correction_rs_floor", _g_clr_rs),
        Gate("price_positive", _g_clr_price),
        Gate("controlled_pullback", _g_clr_pullback),
        Gate("not_weaker_than_market", _g_clr_vs_market),
        Gate("not_at_new_low", _g_clr_not_new_low),
        Gate("ema_reclaim", _g_clr_reclaim, expensive=True),
        Gate("ma200_support", _g_clr_ma200),
        Gate("ma50_support", _g_clr_ma50),
        Gate("volume_confirm_or_dryup", _g_clr_volume),
        Gate("not_extended_without_pullback", _g_clr_not_extended),
        Gate("not_parabolic_without_pullback", _g_clr_not_parabolic),
    ),
    "QQQ_TECH_TACTICAL_SHORT": (
        Gate("liquidity_floor", _g_liquidity),
        Gate("min_bars_75", _g_bars(75)),
        Gate("tech_membership", _g_short_membership),
        Gate("market_risk_weak", _g_short_market_weak),
        Gate("failed_leader", _g_short_failed_leader),
        Gate("tech_weakness", _g_short_tech_weak),
    ),
}


def score_for(variant: str, f: Dict[str, Any], state: Dict[str, Any]) -> Optional[float]:
    """Score a candidate the way the variant's signal function would.

    Used to rank replacement-admitted candidates inside the daily top-N cap.
    """
    try:
        if variant == "PROD_SNIPER_CURRENT":
            return lab._sniper_score(f)
        if variant == "SNIPER_NO_ATR_CONTRACTION":
            return max(70.0, lab._sniper_score(f) + 5.0)
        if variant == "PROD_VOYAGER_CURRENT":
            archetype = state.get("archetype") or lab._voyager_archetype(f)
            return lab._voyager_score(f, archetype) if archetype else None
        if variant == "RECALL_SHADOW_RS_MOMENTUM":
            rs = _rs_max(f)
            vol = _f(f.get("vol_expansion"), 1.0)
            score = 60 + rs * 160 + _f(f.get("r20")) * 40 + _f(f.get("r60")) * 20
            score += min(10, max(0, (vol - 1.0) * 10))
            return score + (8 if lab._is_power_theme(f) else 0)
        if variant == "RECALL_SHADOW_PULLBACK":
            rs = _rs_max(f)
            ext = _f(f.get("ext_ema20"))
            score = 58 + rs * 140 + _f(f.get("r60")) * 25 - abs(ext) * 30
            return score + (6 if lab._is_power_theme(f) else 0)
        if variant == "POWER_TREND_EXTENSION":
            rs = _rs_max(f)
            vol = _f(f.get("vol_expansion"), 1.0)
            score = 55 + rs * 170 + min(15, (vol - 1.0) * 12) + _f(f.get("ext_ema20")) * 20
            return score + (10 if lab._is_power_theme(f) else 0)
        if variant == "QQQ_TECH_TACTICAL_SHORT":
            return 55 + abs(_f(f.get("rs20_qqq"))) * 150 + abs(min(0.0, _f(f.get("r10")))) * 100
        if variant in {"LEADER_RESET_RECLAIM", "LRR_REGIME_GATED"}:
            sig = lab.signal_leader_reset_reclaim(f, lab.StrategyParams())
            return float(sig.score) if sig is not None else None
    except Exception:
        return None
    return None


def trace_candidate(
    variant: str,
    f: Dict[str, Any],
    params: lab.StrategyParams,
    context: Dict[str, Any],
) -> Dict[str, Any]:
    """Trace every gate of a variant for one candidate. As-of data only.

    Expensive gates are only evaluated while the candidate could still be
    accepted or sole-blocked (<=1 prior failure); otherwise they are marked
    not-evaluated. The original signal function is called once as a fidelity
    check: traced acceptance must equal `signal is not None`.
    """
    state: Dict[str, Any] = {}
    gates: List[Dict[str, Any]] = []
    failed: List[str] = []
    for gate in GATE_SPECS[variant]:
        if gate.expensive and len(failed) > 1:
            gates.append({"gate": gate.name, "evaluated": False, "passed": None, "value": None})
            continue
        try:
            value, passed, evaluated = gate.fn(f, params, context, state)
        except Exception:
            value, passed, evaluated = None, False, True
        gates.append({"gate": gate.name, "evaluated": evaluated, "passed": bool(passed) if evaluated else None, "value": value})
        if not passed:
            failed.append(gate.name)

    accepted = not failed
    all_evaluated = all(g["evaluated"] for g in gates)
    sole_blocker = failed[0] if len(failed) == 1 and all_evaluated else None

    fn = lab.VARIANT_FUNCS[variant]
    try:
        if variant in {"QQQ_TECH_TACTICAL_SHORT", "CORRECTION_LEADER_RECLAIM", "LRR_REGIME_GATED"}:
            signal = fn(f, params, context)
        else:
            signal = fn(f, params)
    except Exception:
        signal = None
    fidelity_ok = accepted == (signal is not None)

    return {
        "variant": variant,
        "ticker": f["ticker"],
        "asof": str(f.get("asof")),
        "side": "short" if variant in SHORT_VARIANTS else "long",
        "gates": gates,
        "failed_gates": failed,
        "first_failed_gate": failed[0] if failed else None,
        "sole_blocker": sole_blocker,
        "accepted": accepted,
        "score": float(signal.score) if signal is not None else score_for(variant, f, state),
        "signal": signal,
        "state": state,
        "fidelity_ok": fidelity_ok,
    }


# ---------------------------------------------------------------------------
# Forward outcomes (labeling only — never used in gate decisions)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=400_000)
def forward_outcome(ticker: str, asof: str) -> Optional[Tuple]:
    """Exit-free forward returns from the next bar's open. Label-only."""
    fw = d.get_forward_window(ticker, asof, 21)
    if fw.empty:
        return None
    first = fw.iloc[0]
    entry = first.get("open")
    if entry is None or pd.isna(entry) or float(entry) <= 0:
        entry = first.get("close")
    entry = float(entry)
    if entry <= 0:
        return None
    closes = fw["close"].astype(float)

    def fwd(k: int) -> Optional[float]:
        if len(closes) < k:
            return None
        return round(float(closes.iloc[k - 1]) / entry - 1.0, 6)

    horizon = fw.head(LABEL_HORIZON)
    mfe = round(float(horizon["high"].astype(float).max()) / entry - 1.0, 6) if len(horizon) else None
    mae = round(float(horizon["low"].astype(float).min()) / entry - 1.0, 6) if len(horizon) else None
    return (fwd(5), fwd(10), fwd(20), mfe, mae, round(entry, 4))


def outcome_for(f: Dict[str, Any], side: str) -> Dict[str, Any]:
    raw = forward_outcome(f["ticker"], str(f.get("asof")))
    sign = -1.0 if side == "short" else 1.0
    if raw is None:
        return {"matured": False}
    fwd5, fwd10, fwd20, mfe, mae, entry = raw
    spy = forward_outcome("SPY", str(f.get("asof")))
    qqq = forward_outcome("QQQ", str(f.get("asof")))
    spy10 = spy[1] if spy else None
    qqq10 = qqq[1] if qqq else None
    signed10 = sign * fwd10 if fwd10 is not None else None
    price = _f(f.get("price"))
    return {
        "matured": fwd10 is not None,
        "fwd_5d": fwd5,
        "fwd_10d": fwd10,
        "fwd_20d": fwd20,
        "signed_fwd_5d": sign * fwd5 if fwd5 is not None else None,
        "signed_fwd_10d": signed10,
        "signed_fwd_20d": sign * fwd20 if fwd20 is not None else None,
        "mfe_10d": mfe if side == "long" else (-mae if mae is not None else None),
        "mae_10d": mae if side == "long" else (-mfe if mfe is not None else None),
        "rel_spy_10d": round(signed10 - sign * spy10, 6) if signed10 is not None and spy10 is not None else None,
        "rel_qqq_10d": round(signed10 - sign * qqq10, 6) if signed10 is not None and qqq10 is not None else None,
        "entry_gap": round(entry / price - 1.0, 6) if price > 0 else None,
    }


def classify_case(accepted: bool, outcome: Dict[str, Any]) -> str:
    if not outcome.get("matured"):
        return UNKNOWN_NOT_MATURED
    signed10 = outcome.get("signed_fwd_10d")
    if signed10 is None:
        return UNKNOWN_NOT_MATURED
    if signed10 >= WIN_THRESHOLD:
        return ACCEPTED_WINNER if accepted else REJECTED_WINNER
    if signed10 <= LOSS_THRESHOLD:
        return ACCEPTED_LOSER if accepted else REJECTED_LOSER
    return ACCEPTED_NEUTRAL if accepted else REJECTED_NEUTRAL


# ---------------------------------------------------------------------------
# Pattern flags
# ---------------------------------------------------------------------------

def accepted_loser_flags(f: Dict[str, Any], context: Dict[str, Any], outcome: Dict[str, Any], repeated: bool) -> Dict[str, bool]:
    label = (context.get("REGIME") or {}).get("label")
    ext = _f(f.get("ext_ema20"))
    return {
        "weak_rs": _f(f.get("rs20_spy")) < 0,
        "sector_weakness": _f(f.get("sector_rs20")) < 0,
        "volume_exhaustion": _opt(f.get("vol_expansion")) is not None and _f(f.get("vol_expansion")) < 0.9,
        "too_extended_without_power_trend": ext > 0.15 and not lab._is_power_theme(f),
        "below_ma50": f.get("above_ma50") is False,
        "no_trend_support_below_ma200": f.get("above_ma200") is False,
        "risk_off_or_correction_regime": label in RISK_REGIMES,
        "parabolic_climax_r10_gt_30": _f(f.get("r10")) > 0.30,
        "gap_up_entry_gt_3pct": _f(outcome.get("entry_gap")) > 0.03,
        "repeated_ticker_exposure_5d": repeated,
        "low_liquidity_tail_dvol_lt_10m": _f(f.get("avg_dvol20")) < 10_000_000,
        "failed_reclaim_below_ema20": f.get("above_ema20") is False,
    }


EXTENSION_GATES = {"extension_cap", "extension_band_15_35", "not_parabolic", "not_extended_without_pullback", "not_parabolic_without_pullback", "ma50_extension_cap_12"}
SCORE_GATES = {"sniper_score_70", "voyager_score_65"}


def rejected_winner_flags(trace: Dict[str, Any], f: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, bool]:
    sole = trace.get("sole_blocker")
    label = (context.get("REGIME") or {}).get("label")
    return {
        "too_extended_but_kept_running": sole in EXTENSION_GATES,
        "atr_contraction_failed_but_breakout_worked": sole == "atr_contraction_lt_0_85",
        "no_breakout_but_reclaim_worked": sole == "first_breakout" and f.get("above_ema20") is True,
        "weak_archetype_but_strong_rs_theme": sole == "archetype_match" and (_f(f.get("rs50_spy")) > 0.03 or lab._is_power_theme(f)),
        "below_ma200_floor_but_recovering": sole == "ma200_floor_0_92" and _f(f.get("r20")) > 0.05,
        "score_gate_blocked_winner": sole in SCORE_GATES,
        "correction_regime_survivor": label in regime.CORRECTION_FAMILY,
        "high_rs_leader_during_market_weakness": _f(f.get("rs20_spy")) > 0.05 and f.get("spy_above_ma200") is False,
    }


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def _new_outcome_acc() -> Dict[str, Any]:
    return {"n": 0, "matured": 0, "winners": 0, "losers": 0, "neutral": 0,
            "fwd10": [], "rel_spy": [], "rel_qqq": [], "mfe": [], "mae": [],
            "opportunity_cost": 0.0, "avoided_loss": 0.0}


def _acc_outcome(acc: Dict[str, Any], outcome: Dict[str, Any], label: str) -> None:
    acc["n"] += 1
    if label == UNKNOWN_NOT_MATURED:
        return
    acc["matured"] += 1
    signed10 = outcome.get("signed_fwd_10d")
    if label in (ACCEPTED_WINNER, REJECTED_WINNER):
        acc["winners"] += 1
    elif label in (ACCEPTED_LOSER, REJECTED_LOSER):
        acc["losers"] += 1
    else:
        acc["neutral"] += 1
    if signed10 is not None:
        acc["fwd10"].append(signed10)
        if signed10 > 0:
            acc["opportunity_cost"] += signed10
        else:
            acc["avoided_loss"] += -signed10
    for key, field_name in (("rel_spy", "rel_spy_10d"), ("rel_qqq", "rel_qqq_10d"), ("mfe", "mfe_10d"), ("mae", "mae_10d")):
        value = outcome.get(field_name)
        if value is not None:
            acc[key].append(value)


def _final_outcome(acc: Dict[str, Any]) -> Dict[str, Any]:
    matured = acc["matured"]
    return {
        "count": acc["n"],
        "matured": matured,
        "winners": acc["winners"],
        "losers": acc["losers"],
        "neutral": acc["neutral"],
        "winner_rate": round(acc["winners"] / matured, 4) if matured else None,
        "loser_rate": round(acc["losers"] / matured, 4) if matured else None,
        "avg_fwd_10d": _mean(acc["fwd10"]),
        "avg_rel_spy_10d": _mean(acc["rel_spy"]),
        "avg_rel_qqq_10d": _mean(acc["rel_qqq"]),
        "avg_mfe_10d": _mean(acc["mfe"]),
        "avg_mae_10d": _mean(acc["mae"]),
        "opportunity_cost_sum": round(acc["opportunity_cost"], 4),
        "avoided_loss_sum": round(acc["avoided_loss"], 4),
        "net_value_sum": round(acc["avoided_loss"] - acc["opportunity_cost"], 4),
    }


def gate_verdict(sole: Dict[str, Any]) -> Tuple[str, List[str]]:
    """Verdict for one gate from its sole-blocker counterfactual stats.

    A gate is bad if it blocks winners more than it prevents losers; useful if
    it rejects losers without overblocking winners.
    """
    matured = int(sole.get("matured") or 0)
    if matured < GATE_MIN_MATURED:
        return NEED_MORE_DATA, [f"only {matured} matured sole-blocked cases (<{GATE_MIN_MATURED})"]
    mean_fwd = _f(sole.get("avg_fwd_10d"))
    win_rate = _f(sole.get("winner_rate"))
    loss_rate = _f(sole.get("loser_rate"))
    reasons = [
        f"sole-blocked n={matured}, mean fwd10 {mean_fwd * 100:+.2f}%",
        f"blocked winner rate {win_rate:.0%} vs blocked loser rate {loss_rate:.0%}",
    ]
    if mean_fwd <= -0.005 and loss_rate >= win_rate:
        return KEEP_HARD_BLOCK, reasons + ["blocked flow loses money; gate prevents losers"]
    if mean_fwd >= 0.01 and win_rate > loss_rate * 1.25:
        return OVERBLOCKS_WINNERS, reasons + ["blocked flow wins; the gate costs more than it protects"]
    if abs(mean_fwd) < 0.005 and (loss_rate == 0 or 0.8 <= (win_rate / loss_rate if loss_rate else 99) <= 1.25):
        return NO_SIGNAL, reasons + ["blocked flow is indistinguishable from noise"]
    return KEEP_SOFT_WARNING, reasons + ["mixed evidence; gate has some protective value but blocks some winners"]


@dataclass
class VariantAgg:
    variant: str
    class_counts: Counter = field(default_factory=Counter)
    gate_eval: Counter = field(default_factory=Counter)
    gate_fired: Counter = field(default_factory=Counter)
    gate_sole: Dict[str, Dict[str, Any]] = field(default_factory=lambda: defaultdict(_new_outcome_acc))
    gate_fired_out: Dict[str, Dict[str, Any]] = field(default_factory=lambda: defaultdict(_new_outcome_acc))
    accepted_out: Dict[str, Any] = field(default_factory=_new_outcome_acc)
    rejected_out: Dict[str, Any] = field(default_factory=_new_outcome_acc)
    loser_patterns: Dict[str, Dict[str, Any]] = field(default_factory=lambda: defaultdict(lambda: {"loser": 0, "winner": 0, "neutral": 0, "fwd_with": [], "fwd_without": []}))
    winner_patterns: Dict[str, Dict[str, Any]] = field(default_factory=lambda: defaultdict(lambda: {"count": 0, "fwd": [], "rel_spy": []}))
    rejected_winner_total: int = 0
    samples: Dict[str, List[Dict[str, Any]]] = field(default_factory=lambda: defaultdict(list))
    fidelity_mismatches: int = 0
    accepted_total: int = 0
    selected_total: int = 0
    capped_out: int = 0
    last_accept_idx: Dict[str, int] = field(default_factory=dict)


def _sample_row(trace: Dict[str, Any], f: Dict[str, Any], outcome: Dict[str, Any], label: str) -> Dict[str, Any]:
    return {
        "ticker": trace["ticker"],
        "asof": trace["asof"],
        "label": label,
        "sole_blocker": trace.get("sole_blocker"),
        "failed_gates": list(trace.get("failed_gates") or [])[:4],
        "signed_fwd_10d": outcome.get("signed_fwd_10d"),
        "rel_spy_10d": outcome.get("rel_spy_10d"),
        "score": round(_f(trace.get("score")), 2) if trace.get("score") is not None else None,
        "sector": f.get("sector"),
        "theme": f.get("theme"),
        "market_regime": f.get("market_regime"),
        "ext_ema20": f.get("ext_ema20"),
        "rs20_spy": f.get("rs20_spy"),
    }


def iter_evaluation(
    start: str,
    end: str,
    *,
    variants: Sequence[str] = EVAL_VARIANTS,
    params: lab.StrategyParams = lab.StrategyParams(),
    config: lab.BacktestConfig = lab.BacktestConfig(universe_cap=140, date_stride=1),
    max_dates: Optional[int] = None,
):
    """Yield (date_index, asof, features, context, traces_by_variant).

    Shared by the miner and the counterfactual engine so both see identical
    evaluation. `selected` marks the daily top-N accepted candidates exactly
    like lab.generate_signals_for_date's cap.
    """
    dates = d.trading_dates_between(start, end)
    if config.date_stride > 1:
        dates = dates[:: config.date_stride]
    if max_dates is not None:
        dates = dates[:max_dates]
    for idx, asof in enumerate(dates):
        features = d.compute_universe_features_asof(
            asof,
            mode=config.universe_mode,
            cap=config.universe_cap,
            min_bars=config.min_bars,
            min_avg_dvol=params.min_avg_dvol,
        )
        context = {sym: d.compute_features_asof(sym, asof) for sym in ("SPY", "QQQ", "SMH", "XLK")}
        context["REGIME"] = regime.classify_regime(asof, correction_threshold=params.correction_market_dd_threshold)
        regime_label = (context.get("REGIME") or {}).get("label")
        for f in features:
            f.setdefault("market_regime", regime_label)
        traces_by_variant: Dict[str, List[Dict[str, Any]]] = {}
        for variant in variants:
            traces = [trace_candidate(variant, f, params, context) for f in features]
            accepted = sorted(
                (t for t in traces if t["accepted"]),
                key=lambda t: _f(t.get("score")),
                reverse=True,
            )
            selected_keys = {(t["ticker"], t["asof"]) for t in accepted[: config.max_signals_per_variant_day]}
            for t in traces:
                t["selected"] = t["accepted"] and (t["ticker"], t["asof"]) in selected_keys
            traces_by_variant[variant] = traces
        yield idx, asof, features, context, traces_by_variant


def mine(
    start: str,
    end: str,
    *,
    variants: Sequence[str] = EVAL_VARIANTS,
    params: lab.StrategyParams = lab.StrategyParams(),
    config: lab.BacktestConfig = lab.BacktestConfig(universe_cap=140, date_stride=1),
    max_dates: Optional[int] = None,
) -> Dict[str, Any]:
    aggs: Dict[str, VariantAgg] = {v: VariantAgg(v) for v in variants}
    dates_evaluated = 0
    ticker_days = 0
    feature_index: Dict[Tuple[str, str], Dict[str, Any]] = {}

    for idx, asof, features, context, traces_by_variant in iter_evaluation(
        start, end, variants=variants, params=params, config=config, max_dates=max_dates
    ):
        dates_evaluated += 1
        ticker_days += len(features)
        fmap = {f["ticker"]: f for f in features}
        for variant, traces in traces_by_variant.items():
            agg = aggs[variant]
            for trace in traces:
                f = fmap[trace["ticker"]]
                if not trace["fidelity_ok"]:
                    agg.fidelity_mismatches += 1
                outcome = outcome_for(f, trace["side"])
                label = classify_case(trace["accepted"], outcome)
                agg.class_counts[label] += 1

                if trace["accepted"]:
                    agg.accepted_total += 1
                    if trace["selected"]:
                        agg.selected_total += 1
                    else:
                        agg.capped_out += 1
                    repeated = (idx - agg.last_accept_idx.get(trace["ticker"], -99)) <= 5
                    agg.last_accept_idx[trace["ticker"]] = idx
                    _acc_outcome(agg.accepted_out, outcome, label)
                    if label in (ACCEPTED_LOSER, ACCEPTED_WINNER, ACCEPTED_NEUTRAL):
                        flags = accepted_loser_flags(f, context, outcome, repeated)
                        bucket = "loser" if label == ACCEPTED_LOSER else ("winner" if label == ACCEPTED_WINNER else "neutral")
                        for name, hit in flags.items():
                            pat = agg.loser_patterns[name]
                            if hit:
                                pat[bucket] += 1
                                if outcome.get("signed_fwd_10d") is not None:
                                    pat["fwd_with"].append(outcome["signed_fwd_10d"])
                            elif outcome.get("signed_fwd_10d") is not None:
                                pat["fwd_without"].append(outcome["signed_fwd_10d"])
                else:
                    _acc_outcome(agg.rejected_out, outcome, label)
                    for gate_row in trace["gates"]:
                        if not gate_row["evaluated"]:
                            continue
                        agg.gate_eval[gate_row["gate"]] += 1
                        if gate_row["passed"] is False:
                            agg.gate_fired[gate_row["gate"]] += 1
                            _acc_outcome(agg.gate_fired_out[gate_row["gate"]], outcome, label)
                    if trace["sole_blocker"]:
                        _acc_outcome(agg.gate_sole[trace["sole_blocker"]], outcome, label)
                    if label == REJECTED_WINNER:
                        agg.rejected_winner_total += 1
                        for name, hit in rejected_winner_flags(trace, f, context).items():
                            if hit:
                                pat = agg.winner_patterns[name]
                                pat["count"] += 1
                                if outcome.get("signed_fwd_10d") is not None:
                                    pat["fwd"].append(outcome["signed_fwd_10d"])
                                if outcome.get("rel_spy_10d") is not None:
                                    pat["rel_spy"].append(outcome["rel_spy_10d"])

                if len(agg.samples[label]) < SAMPLES_PER_CLASS:
                    agg.samples[label].append(_sample_row(trace, f, outcome, label))
        feature_index.clear()

    return {
        "start": start,
        "end": end,
        "dates_evaluated": dates_evaluated,
        "ticker_days_evaluated": ticker_days,
        "aggs": aggs,
    }


# ---------------------------------------------------------------------------
# Report building
# ---------------------------------------------------------------------------

def _gate_table(agg: VariantAgg) -> Dict[str, Any]:
    out = {}
    accepted = _final_outcome(agg.accepted_out)
    for gate in (g.name for g in GATE_SPECS[agg.variant]):
        sole = _final_outcome(agg.gate_sole[gate])
        fired_all = _final_outcome(agg.gate_fired_out[gate])
        verdict, reasons = gate_verdict(sole)
        matured = sole["matured"]
        out[gate] = {
            "evaluated_count": int(agg.gate_eval.get(gate, 0)) + agg.accepted_total,
            "fired_count": int(agg.gate_fired.get(gate, 0)),
            "sole_blocker": sole,
            "fired_any": fired_all,
            "accepted_reference": {
                "winner_rate": accepted.get("winner_rate"),
                "loser_rate": accepted.get("loser_rate"),
                "avg_fwd_10d": accepted.get("avg_fwd_10d"),
            },
            "rejected_winner_rate": sole.get("winner_rate"),
            "rejected_loser_rate": sole.get("loser_rate"),
            "false_positive_reduction": sole.get("loser_rate"),
            "opportunity_cost": sole.get("opportunity_cost_sum"),
            "avoided_loss": sole.get("avoided_loss_sum"),
            "net_value": sole.get("net_value_sum"),
            "net_value_per_blocked": round(_f(sole.get("net_value_sum")) / matured, 6) if matured else None,
            "verdict": verdict,
            "verdict_reasons": reasons,
        }
    return out


def _loser_pattern_table(agg: VariantAgg) -> Dict[str, Any]:
    out = {}
    losers = agg.class_counts[ACCEPTED_LOSER]
    winners = agg.class_counts[ACCEPTED_WINNER]
    for name, pat in sorted(agg.loser_patterns.items()):
        n_with = pat["loser"] + pat["winner"] + pat["neutral"]
        loser_prev = pat["loser"] / losers if losers else None
        winner_prev = pat["winner"] / winners if winners else None
        lift = (loser_prev / winner_prev) if loser_prev and winner_prev else None
        mean_with = _mean(pat["fwd_with"])
        mean_without = _mean(pat["fwd_without"])
        protective = bool(
            lift is not None and lift >= 1.3
            and loser_prev is not None and loser_prev >= 0.10
            and n_with >= 30
            and mean_with is not None and mean_without is not None
            and mean_with < mean_without - 0.005
        )
        out[name] = {
            "hits": n_with,
            "loser_hits": pat["loser"],
            "winner_hits": pat["winner"],
            "loser_prevalence": round(loser_prev, 4) if loser_prev is not None else None,
            "winner_prevalence": round(winner_prev, 4) if winner_prev is not None else None,
            "lift": round(lift, 3) if lift is not None else None,
            "avg_fwd_10d_with_pattern": mean_with,
            "avg_fwd_10d_without_pattern": mean_without,
            "verdict": MISSING_PROTECTION if protective else (NEED_MORE_DATA if n_with < 30 else NO_SIGNAL),
        }
    for name, why in NOT_MEASURABLE_PATTERNS.items():
        if name in ("earnings_soon", "high_spread_low_liquidity_quote"):
            out[name] = {"verdict": "NOT_MEASURABLE", "reason": why}
    return out


def _winner_pattern_table(agg: VariantAgg) -> Dict[str, Any]:
    out = {}
    total = agg.rejected_winner_total
    for name, pat in sorted(agg.winner_patterns.items()):
        out[name] = {
            "count": pat["count"],
            "share_of_rejected_winners": round(pat["count"] / total, 4) if total else None,
            "avg_fwd_10d": _mean(pat["fwd"]),
            "avg_rel_spy_10d": _mean(pat["rel_spy"]),
        }
    for name in ("gatekeeper_blocked_but_price_advanced", "lacked_lens_but_price_behaved"):
        out[name] = {"verdict": "NOT_MEASURABLE", "reason": NOT_MEASURABLE_PATTERNS[name]}
    return out


def _strategy_recommendation(
    variant: str,
    gate_table: Dict[str, Any],
    accepted: Dict[str, Any],
    loser_patterns: Dict[str, Any],
    correction_verdicts: Dict[str, Any],
) -> Dict[str, Any]:
    overblockers = sorted(
        ((g, row) for g, row in gate_table.items() if row["verdict"] == OVERBLOCKS_WINNERS),
        key=lambda kv: -_f(kv[1].get("opportunity_cost")),
    )
    protectors = sorted(
        ((g, row) for g, row in gate_table.items() if row["verdict"] == KEEP_HARD_BLOCK),
        key=lambda kv: -_f(kv[1].get("net_value")),
    )
    missing = [name for name, row in loser_patterns.items() if row.get("verdict") == MISSING_PROTECTION]
    matured = int(accepted.get("matured") or 0)
    mean_fwd = accepted.get("avg_fwd_10d")
    corr = (correction_verdicts.get(variant) or {})

    if variant == "QQQ_TECH_TACTICAL_SHORT":
        stance = "KILL"
        why = "Confirmed harmful baseline: negative expectancy, -100% independent drawdown in 1H.1/1H.3, and no gate fix addresses being short a bull tape."
    elif matured >= 30 and mean_fwd is not None and mean_fwd <= 0:
        stance = "KILL"
        why = "Accepted flow loses money on raw forward returns; gate repair cannot fix negative selection."
    elif overblockers or missing:
        stance = "REPAIR"
        why = "Accepted flow has positive forward returns and specific gates/protections are identified for replacement testing."
    elif matured < 30:
        stance = "NEED_MORE_DATA"
        why = f"Only {matured} matured accepted cases; cannot judge selection quality yet."
    else:
        stance = "PRESERVE"
        why = "Accepted flow is positive and no gate shows overblocking; do not touch what works."

    return {
        "stance": stance,
        "why": why,
        "accepted_matured": matured,
        "accepted_avg_fwd_10d": mean_fwd,
        "worst_overblocking_gates": [{"gate": g, "opportunity_cost": row["opportunity_cost"], "verdict_reasons": row["verdict_reasons"]} for g, row in overblockers[:3]],
        "best_protective_gates": [{"gate": g, "avoided_loss": row["avoided_loss"], "net_value": row["net_value"]} for g, row in protectors[:3]],
        "missing_protections": missing,
        "correction_report_verdict": corr.get("label"),
    }


def build_report(
    *,
    start: Optional[str] = None,
    end: Optional[str] = None,
    variants: Sequence[str] = EVAL_VARIANTS,
    stride: int = 1,
    max_dates: Optional[int] = None,
) -> Dict[str, Any]:
    if start is None or end is None:
        span_start, span_end, _, _ = portfolio._primary_window_span()
        start = start or span_start
        end = end or span_end
    params = lab.StrategyParams()
    config = lab.BacktestConfig(universe_cap=140, date_stride=stride)

    mined = mine(start, end, variants=variants, params=params, config=config, max_dates=max_dates)
    aggs: Dict[str, VariantAgg] = mined["aggs"]

    correction = {}
    if CORRECTION_JSON.exists():
        try:
            correction = (json.loads(CORRECTION_JSON.read_text()).get("updated_variant_verdicts") or {})
        except Exception:
            correction = {}

    variants_out: Dict[str, Any] = {}
    all_gates_flat: List[Tuple[str, str, Dict[str, Any]]] = []
    for variant in variants:
        agg = aggs[variant]
        gate_table = _gate_table(agg)
        accepted = _final_outcome(agg.accepted_out)
        loser_patterns = _loser_pattern_table(agg)
        winner_patterns = _winner_pattern_table(agg)
        variants_out[variant] = {
            "class_counts": dict(agg.class_counts),
            "accepted_total": agg.accepted_total,
            "selected_within_daily_cap": agg.selected_total,
            "accepted_but_capped_out": agg.capped_out,
            "accepted_outcomes": accepted,
            "rejected_outcomes": _final_outcome(agg.rejected_out),
            "gate_table": gate_table,
            "accepted_loser_patterns": loser_patterns,
            "rejected_winner_patterns": winner_patterns,
            "fidelity_mismatches": agg.fidelity_mismatches,
            "samples": {k: v for k, v in agg.samples.items()},
            "recommendation": _strategy_recommendation(variant, gate_table, accepted, loser_patterns, correction),
        }
        for gate, row in gate_table.items():
            all_gates_flat.append((variant, gate, row))

    top_failed = sorted(all_gates_flat, key=lambda x: -x[2]["fired_count"])[:15]
    worst_overblocking = sorted(
        (x for x in all_gates_flat if x[2]["verdict"] == OVERBLOCKS_WINNERS),
        key=lambda x: -_f(x[2].get("opportunity_cost")),
    )[:10]
    best_protective = sorted(
        (x for x in all_gates_flat if x[2]["verdict"] == KEEP_HARD_BLOCK),
        key=lambda x: -_f(x[2].get("net_value")),
    )[:10]

    total_mismatch = sum(a.fidelity_mismatches for a in aggs.values())

    return {
        "kind": "strategy_failure_reason_miner",
        "version": VERSION,
        "generated_at": _utc_now(),
        "research_only": True,
        "status": "RESEARCH_ONLY",
        "signal_window": {"start": start, "end": end},
        "dates_evaluated": mined["dates_evaluated"],
        "ticker_days_evaluated": mined["ticker_days_evaluated"],
        "label_rules": {
            "horizon_days": LABEL_HORIZON,
            "winner_threshold": WIN_THRESHOLD,
            "loser_threshold": LOSS_THRESHOLD,
            "note": "Exit-free forward returns from next-bar open, side-adjusted for shorts. Forward data is used only for labeling after the decision; gate values are strictly as-of.",
        },
        "params": params.as_dict(),
        "config": config.as_dict(),
        "fidelity": {
            "total_mismatches": total_mismatch,
            "note": "traced gate acceptance is asserted against the original VARIANT_FUNCS signal on every candidate",
        },
        "fairness_caveats": [
            "The candidate pool is already pre-filtered (price>=5, avg dollar-volume>=5M, bars>=75, universe cap 140), so liquidity-gate statistics are conditional on that pool.",
            "Earnings proximity, point-in-time quote spread, Gatekeeper verdicts, and Stock Lens artifacts are NOT retained historically; affected patterns are reported NOT_MEASURABLE instead of guessed.",
            "Winner/loser labels use exit-free 10d forward returns, not the strategy's stop/target exits; strategy P&L is measured separately in the counterfactual backtest.",
            "Sole-blocker counterfactuals assume the rest of the gates stay unchanged; daily top-5 capacity effects are handled in the counterfactual backtest, not here.",
        ],
        "variants": variants_out,
        "top_failed_reasons": [
            {"variant": v, "gate": g, "fired_count": row["fired_count"], "verdict": row["verdict"]}
            for v, g, row in top_failed
        ],
        "worst_overblocking_filters": [
            {"variant": v, "gate": g, "opportunity_cost": row["opportunity_cost"], "sole_matured": row["sole_blocker"]["matured"], "avg_fwd_10d": row["sole_blocker"]["avg_fwd_10d"]}
            for v, g, row in worst_overblocking
        ],
        "best_protective_filters": [
            {"variant": v, "gate": g, "avoided_loss": row["avoided_loss"], "net_value": row["net_value"], "sole_matured": row["sole_blocker"]["matured"]}
            for v, g, row in best_protective
        ],
        "safety": lab.safety_confirmations(),
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_text(res: Dict[str, Any]) -> List[str]:
    lines = [
        f"STRATEGY FAILURE REASON MINER - {res['generated_at']}",
        lab.RESEARCH_DISCLAIMER,
        f"window={res['signal_window']['start']}..{res['signal_window']['end']} dates={res['dates_evaluated']} ticker_days={res['ticker_days_evaluated']}",
        f"fidelity_mismatches={res['fidelity']['total_mismatches']}",
        "",
        f"{'variant':28s} {'acc':>5s} {'accW':>5s} {'accL':>5s} {'rejW':>6s} {'rejL':>7s} {'stance':>15s}",
    ]
    for variant, row in res["variants"].items():
        cc = row["class_counts"]
        lines.append(
            f"{variant:28s} {row['accepted_total']:>5d} {cc.get(ACCEPTED_WINNER, 0):>5d} {cc.get(ACCEPTED_LOSER, 0):>5d} "
            f"{cc.get(REJECTED_WINNER, 0):>6d} {cc.get(REJECTED_LOSER, 0):>7d} {row['recommendation']['stance']:>15s}"
        )
    lines.append("")
    lines.append("WORST OVERBLOCKING FILTERS (by opportunity cost):")
    for row in res["worst_overblocking_filters"][:5]:
        lines.append(f"  {row['variant']}.{row['gate']}: opp_cost={row['opportunity_cost']} avg_fwd10={_pct(row['avg_fwd_10d'])} n={row['sole_matured']}")
    lines.append("BEST PROTECTIVE FILTERS (by net avoided loss):")
    for row in res["best_protective_filters"][:5]:
        lines.append(f"  {row['variant']}.{row['gate']}: avoided={row['avoided_loss']} net={row['net_value']} n={row['sole_matured']}")
    return lines


def render_doc(res: Dict[str, Any]) -> str:
    lines = [
        "# Strategy Failure Reason Miner (Phase 1H.4)",
        "",
        f"Generated: {res['generated_at']}",
        "",
        lab.RESEARCH_DISCLAIMER,
        "",
        f"Signal window: `{res['signal_window']['start']}` to `{res['signal_window']['end']}` "
        f"({res['dates_evaluated']} dates, {res['ticker_days_evaluated']} ticker-days). "
        f"Fidelity mismatches vs original signal functions: **{res['fidelity']['total_mismatches']}**.",
        "",
        "Labels: winner = 10d exit-free forward return >= +3%, loser <= -3% (side-adjusted). "
        "Forward data is used only for labeling; decision fields are strictly as-of.",
        "",
        "## Case Classification",
        "",
        "| Variant | Accepted | Acc Winners | Acc Losers | Rej Winners | Rej Losers | Not Matured |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for variant, row in res["variants"].items():
        cc = row["class_counts"]
        lines.append(
            f"| {variant} | {row['accepted_total']} | {cc.get(ACCEPTED_WINNER, 0)} | {cc.get(ACCEPTED_LOSER, 0)} | "
            f"{cc.get(REJECTED_WINNER, 0)} | {cc.get(REJECTED_LOSER, 0)} | {cc.get(UNKNOWN_NOT_MATURED, 0)} |"
        )
    lines += ["", "## Gate Verdicts", ""]
    for variant, row in res["variants"].items():
        lines += [f"### {variant}", "", "| Gate | Fired | Sole-blocked (matured) | Blocked avg fwd10 | W rate | L rate | Opp Cost | Avoided | Verdict |", "|---|---:|---:|---:|---:|---:|---:|---:|---|"]
        for gate, g in row["gate_table"].items():
            sole = g["sole_blocker"]
            lines.append(
                f"| {gate} | {g['fired_count']} | {sole['matured']} | {_pct(sole['avg_fwd_10d'])} | "
                f"{sole['winner_rate'] if sole['winner_rate'] is not None else 'n/a'} | {sole['loser_rate'] if sole['loser_rate'] is not None else 'n/a'} | "
                f"{g['opportunity_cost']} | {g['avoided_loss']} | {g['verdict']} |"
            )
        rec = row["recommendation"]
        lines += ["", f"**Stance: {rec['stance']}** — {rec['why']}", ""]
    lines += [
        "## Fairness Caveats",
        "",
        *[f"- {c}" for c in res["fairness_caveats"]],
        "",
        "## Safety",
        "",
        "No paper signals, broker orders, trade proposals, production thresholds, Gatekeeper/Veto logic, execution/governance/live-capital modules, historical evidence, or SHORT_A frozen status were changed.",
        "",
    ]
    return "\n".join(lines)


def render_loser_doc(res: Dict[str, Any]) -> str:
    lines = [
        "# Accepted-Loser Pattern Report (Phase 1H.4)",
        "",
        f"Generated: {res['generated_at']}",
        "",
        lab.RESEARCH_DISCLAIMER,
        "",
        "Purpose: find protections that should be added. A pattern is flagged MISSING_PROTECTION when it is at least 1.3x "
        "more prevalent among accepted losers than accepted winners, covers >=10% of losers, has >=30 hits, and degrades "
        "forward returns by >=0.5%.",
        "",
    ]
    for variant, row in res["variants"].items():
        lines += [f"## {variant}", "", "| Pattern | Hits | Loser prev | Winner prev | Lift | Fwd10 with | Fwd10 without | Verdict |", "|---|---:|---:|---:|---:|---:|---:|---|"]
        for name, pat in row["accepted_loser_patterns"].items():
            if pat.get("verdict") == "NOT_MEASURABLE":
                lines.append(f"| {name} | - | - | - | - | - | - | NOT_MEASURABLE ({pat['reason']}) |")
                continue
            lines.append(
                f"| {name} | {pat['hits']} | {pat['loser_prevalence']} | {pat['winner_prevalence']} | {pat['lift']} | "
                f"{_pct(pat['avg_fwd_10d_with_pattern'])} | {_pct(pat['avg_fwd_10d_without_pattern'])} | {pat['verdict']} |"
            )
        lines.append("")
    return "\n".join(lines)


def render_winner_doc(res: Dict[str, Any]) -> str:
    lines = [
        "# Rejected-Winner Pattern Report (Phase 1H.4)",
        "",
        f"Generated: {res['generated_at']}",
        "",
        lab.RESEARCH_DISCLAIMER,
        "",
        "Purpose: find gates that should be softened or replaced. Counts are over matured REJECTED_WINNER cases "
        "(10d forward return >= +3% despite rejection).",
        "",
    ]
    for variant, row in res["variants"].items():
        total = row["class_counts"].get(REJECTED_WINNER, 0)
        lines += [f"## {variant} ({total} rejected winners)", "", "| Pattern | Count | Share | Avg fwd10 | Avg rel-SPY |", "|---|---:|---:|---:|---:|"]
        for name, pat in row["rejected_winner_patterns"].items():
            if pat.get("verdict") == "NOT_MEASURABLE":
                lines.append(f"| {name} | - | - | - | NOT_MEASURABLE |")
                continue
            lines.append(f"| {name} | {pat['count']} | {pat['share_of_rejected_winners']} | {_pct(pat['avg_fwd_10d'])} | {_pct(pat['avg_rel_spy_10d'])} |")
        lines.append("")
    return "\n".join(lines)


def render_kill_repair_doc(res: Dict[str, Any], counterfactual: Optional[Dict[str, Any]] = None) -> str:
    """Task 8: plain and decisive kill/repair list."""
    kills, repairs, preserves, need_data = [], [], [], []
    for variant, row in res["variants"].items():
        stance = row["recommendation"]["stance"]
        entry = (variant, row["recommendation"]["why"])
        {"KILL": kills, "REPAIR": repairs, "PRESERVE": preserves, "NEED_MORE_DATA": need_data}[stance].append(entry)

    keep, soften, replace, more_data = [], [], [], []
    for variant, row in res["variants"].items():
        for gate, g in row["gate_table"].items():
            tag = f"{variant}.{gate}"
            if g["verdict"] == KEEP_HARD_BLOCK:
                keep.append((tag, g["net_value"]))
            elif g["verdict"] == KEEP_SOFT_WARNING:
                soften.append((tag, g["net_value"]))
            elif g["verdict"] == OVERBLOCKS_WINNERS:
                replace.append((tag, g["opportunity_cost"]))
            elif g["verdict"] == NEED_MORE_DATA:
                more_data.append((tag, g["sole_blocker"]["matured"]))

    worst = res["worst_overblocking_filters"][:1]
    best = res["best_protective_filters"][:1]

    cf_lines: List[str] = []
    if counterfactual:
        status = counterfactual.get("paper_shadow", {}).get("status") or counterfactual.get("status")
        best_repl = counterfactual.get("best_replacement")
        cf_lines = [
            "## Replacement Test Outcome (from counterfactual backtest)",
            "",
            f"- Status: **{status}**",
            f"- Best replacement candidate: **{best_repl.get('spec_id') if best_repl else 'none'}**"
            + (f" ({best_repl.get('verdict')}, expectancy delta {_pct(best_repl.get('delta_expectancy'))})" if best_repl else ""),
            "",
        ]

    lines = [
        "# Strategy Kill and Repair List (Phase 1H.4)",
        "",
        f"Generated: {res['generated_at']}",
        "",
        lab.RESEARCH_DISCLAIMER,
        "",
        "## Strategies to KILL",
        "",
        *([f"- **{v}** — {why}" for v, why in kills] or ["- none"]),
        "",
        "## Strategies to REPAIR",
        "",
        *([f"- **{v}** — {why}" for v, why in repairs] or ["- none"]),
        "",
        "## Strategies to PRESERVE as-is",
        "",
        *([f"- **{v}** — {why}" for v, why in preserves] or ["- none"]),
        "",
        "## Strategies needing more data",
        "",
        *([f"- **{v}** — {why}" for v, why in need_data] or ["- none"]),
        "",
        "## Filters to KEEP (hard blocks that prevent losses)",
        "",
        *([f"- `{t}` (net avoided loss {n})" for t, n in sorted(keep, key=lambda x: -_f(x[1]))[:10]] or ["- none proven"]),
        "",
        "## Filters to SOFTEN (mixed evidence)",
        "",
        *([f"- `{t}`" for t, _ in soften[:10]] or ["- none"]),
        "",
        "## Filters to REPLACE (overblock winners)",
        "",
        *([f"- `{t}` (opportunity cost {n})" for t, n in sorted(replace, key=lambda x: -_f(x[1]))[:10]] or ["- none proven"]),
        "",
        "## Filters needing more data",
        "",
        *([f"- `{t}` (matured sole-blocked n={n})" for t, n in more_data[:15]] or ["- none"]),
        "",
        "## Worst filter by opportunity cost",
        "",
        *([f"- `{w['variant']}.{w['gate']}` — opportunity cost {w['opportunity_cost']} across {w['sole_matured']} matured sole-blocked cases" for w in worst] or ["- none qualifies (insufficient matured sole-blocked samples)"]),
        "",
        "## Best protective filter by avoided loss",
        "",
        *([f"- `{b['variant']}.{b['gate']}` — avoided loss {b['avoided_loss']}, net value {b['net_value']}" for b in best] or ["- none qualifies (insufficient matured sole-blocked samples)"]),
        "",
        *cf_lines,
        "## Safety",
        "",
        "Research-only. Nothing here changes production thresholds, gates, execution, governance, paper evidence, or SHORT_A's frozen status.",
        "",
    ]
    return "\n".join(lines)


def write_outputs(res: Dict[str, Any]) -> None:
    for p in (OUT_JSON, OUT_TXT, OUT_DOC):
        p.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    OUT_TXT.write_text("\n".join(render_text(res)) + "\n", encoding="utf-8")
    OUT_DOC.write_text(render_doc(res) + "\n", encoding="utf-8")

    loser_payload = {
        "kind": "accepted_loser_patterns",
        "generated_at": res["generated_at"],
        "research_only": True,
        "signal_window": res["signal_window"],
        "variants": {v: row["accepted_loser_patterns"] for v, row in res["variants"].items()},
    }
    OUT_LOSER_JSON.write_text(json.dumps(loser_payload, indent=2, default=str), encoding="utf-8")
    OUT_LOSER_DOC.write_text(render_loser_doc(res) + "\n", encoding="utf-8")

    winner_payload = {
        "kind": "rejected_winner_patterns",
        "generated_at": res["generated_at"],
        "research_only": True,
        "signal_window": res["signal_window"],
        "variants": {v: row["rejected_winner_patterns"] for v, row in res["variants"].items()},
    }
    OUT_WINNER_JSON.write_text(json.dumps(winner_payload, indent=2, default=str), encoding="utf-8")
    OUT_WINNER_DOC.write_text(render_winner_doc(res) + "\n", encoding="utf-8")

    counterfactual = None
    if COUNTERFACTUAL_JSON.exists():
        try:
            counterfactual = json.loads(COUNTERFACTUAL_JSON.read_text())
        except Exception:
            counterfactual = None
    OUT_KILL_REPAIR_DOC.write_text(render_kill_repair_doc(res, counterfactual) + "\n", encoding="utf-8")


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Phase 1H.4 failure-reason miner (research-only)")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--stride", type=int, default=1)
    ap.add_argument("--max-dates", type=int, default=None)
    args = ap.parse_args(argv)
    res = build_report(start=args.start, end=args.end, stride=args.stride, max_dates=args.max_dates)
    write_outputs(res)
    print("\n".join(render_text(res)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

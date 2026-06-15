"""
Alpha Discovery Board V2.

Research-only long idea discovery layer for manual/discretionary review.

This module is explicitly not:
  - an execution engine
  - a paper-evidence engine
  - a sleeve approval system
  - a replacement for VOYAGER / SNIPER / SHORT_A

It is a cache-first ranking layer that surfaces early long-side research ideas
and classifies them into four buckets:
  - Early Discovery
  - Buyable Pullback
  - Sponsor Confirmation
  - Too Late / Crowded

Operational flow:
  - nightly board build = canonical discovery artifact
  - premarket overlay = light actionability refinement on surfaced names only
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
import importlib.util
import json
import math
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import core.config as cfg
from core.alpaca_client import get_alpaca
from core.daily_entry_validator import DailyEntryValidation, validate_daily_entry
from core.fmp_client import get_fmp


_KNOWN_INSTRUMENTS = {
    "SPY", "QQQ", "IWM", "GLD", "TLT", "HYG", "VXX", "SQQQ", "QID", "TQQQ",
    "QLD", "SOXL", "SOXS", "SPXU", "UPRO", "SDS", "SH", "DOG", "DXD", "TZA",
    "TNA", "USO", "SLV", "XLF", "XLE", "XLI", "XLK", "XBI", "ARKK",
}

ALPHA_DISCOVERY_VERSION = "ALPHA_DISCOVERY_V2.1"
UNIVERSE_DEFINITION = {
    "price_floor": 8.0,
    "avg_dollar_volume_floor": 10_000_000.0,
    "current_dollar_volume_floor": 5_000_000.0,
    "market_cap_floor": 300_000_000.0,
    "market_cap_ceiling": 80_000_000_000.0,
    "exclude_etfs": True,
    "exclude_microcaps": True,
}
_ALPHA_RESEARCH_DIR = cfg.CACHE_DIR / "research"
_ALPHA_JSON_PATH = _ALPHA_RESEARCH_DIR / "alpha_discovery_board_latest.json"
_ALPHA_OVERLAY_JSON_PATH = _ALPHA_RESEARCH_DIR / "alpha_discovery_overlay_latest.json"
_ALPHA_ENRICHMENT_JSON_PATH = _ALPHA_RESEARCH_DIR / "alpha_discovery_enrichment_latest.json"
_ALPHA_ENRICHMENT_ROTATION_PATH = cfg.CACHE_DIR / "state" / "alpha_enrichment_rotation.json"

# ── Stock Lens annotation constants ──────────────────────────────────────────
# Fresh-lens threshold matches the prebuild script and dashboard convention.
LENS_FRESH_HOURS_DEFAULT = 24.0

# Stricter actionability labels sort higher.  When a fresh lens reports a
# stricter validator state than the board, the board's action_label is
# downgraded to the lens label and `board_lens_conflict=True` is set.
# The board's original_action_label / original_alpha_score are preserved so
# the audit trail of the conflict is not lost.
_STRICTNESS_ORDER: Dict[str, int] = {
    "Buyable Now":            0,
    "Buyable Pullback":       0,
    "Pullback Forming":       1,
    "Pullback Watch":         1,
    "Sponsor Confirmation":   1,
    "Watch Reclaim":          2,
    "Watch Only":             3,
    "Too Extended":           4,
    "Too Late / Crowded":     4,
    "Broken/Avoid":           5,
}

# Map lens options_quality → board-level alpha_flags.  These are warnings,
# not trade signals.  They are surfaced on the board so a reader does not
# need to drill into the lens artifact to see a contradiction.
_OPTIONS_QUALITY_TO_FLAG: Dict[str, str] = {
    "BEARISH_HEDGE":            "OPTIONS_BEARISH_HEDGE",
    "SPECULATIVE_CALL_CHASE":   "OPTIONS_SPECULATIVE_CALL_CHASE",
    "BULLISH_BUT_LATE":         "OPTIONS_BULLISH_BUT_LATE",
    "BEARISH_CALL_CHASE":       "OPTIONS_BEARISH_CALL_CHASE",
    "OPTIONS_NO_EDGE":          "OPTIONS_NO_EDGE",
    "OPTIONS_MISSING":          "OPTIONS_MISSING",
}

# Labels that force actionable_now=False when adopted as the action_label.
_NON_ACTIONABLE_LABELS = {
    "Too Extended",
    "Too Late / Crowded",
    "Watch Only",
    "Broken/Avoid",
}


@dataclass(frozen=True)
class ScoreBlock:
    score: Optional[float]
    detail: str


@dataclass(frozen=True)
class AlphaDiscoveryCandidate:
    ticker: str
    track: str
    bucket: str
    alpha_score: float
    business_inflection_score: Optional[float]
    sponsorship_score: float
    entry_quality_score: float
    crowd_penalty: float
    liquidity_score: float
    validator_state: str
    validator_reason: str
    validator_flags: List[str]
    entry_state: str
    action_label: str
    data_tier: str
    data_layers: List[str]
    why_now: str
    main_risk: str
    sleeve_resemblance: str
    actionable_now: bool
    why_not: str
    market_cap: Optional[float]
    sector: str
    industry: str
    price: float
    avg_dollar_volume_20: float
    current_dollar_volume: float
    return_5d_pct: float
    return_20d_pct: float
    volume_ratio_5d: float
    overlay_13f: Optional[float]
    overlay_tradier: Optional[float]
    block_details: Dict[str, str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _clip100(value: float) -> float:
    return max(0.0, min(100.0, value))


def _norm(value: float, lo: float, hi: float) -> float:
    if hi <= lo:
        return 0.0
    return _clip01((value - lo) / (hi - lo))


def _log_norm(value: float, lo: float, hi: float) -> float:
    if value <= 0 or hi <= lo:
        return 0.0
    try:
        lv = math.log10(value)
    except ValueError:
        return 0.0
    return _clip01((lv - lo) / (hi - lo))


def _score_from_components(values: Sequence[float]) -> Optional[float]:
    usable = [v for v in values if v is not None]
    if not usable:
        return None
    return round(sum(usable) / len(usable) * 100.0, 1)


def _bucket_label(
    *,
    overall: float,
    sponsorship: float,
    entry: float,
    crowd_penalty: float,
    entry_state: str,
) -> str:
    if crowd_penalty >= 55 or entry_state == "too extended":
        return "Too Late / Crowded"
    if entry_state == "buyable now" and entry >= 72 and sponsorship >= 55 and crowd_penalty < 45:
        return "Buyable Pullback"
    if sponsorship >= 68 and overall >= 60 and entry_state in {"watch reclaim", "pullback forming"}:
        return "Sponsor Confirmation"
    return "Early Discovery"


def _track_label(row: Dict[str, Any], market_cap: Optional[float]) -> str:
    avg_dvol = _f(row.get("avg_dollar_volume_20"))
    current_dvol = _f(row.get("current_dollar_volume"))
    mcap = market_cap or 0.0
    if mcap >= 20_000_000_000 or avg_dvol >= 750_000_000 or current_dvol >= 1_000_000_000:
        return "Liquid Leadership Reset"
    return "Emerging Opportunity"


def _actionability(bucket: str, entry: float, crowd_penalty: float, entry_state: str) -> Tuple[bool, str, str]:
    if bucket == "Buyable Pullback" and entry_state == "buyable now" and entry >= 72 and crowd_penalty < 45:
        return True, "", "Buyable Now"
    if bucket == "Too Late / Crowded":
        return False, "too extended / crowded for fresh daily entry", "Too Late / Crowded"
    if entry_state == "too extended":
        return False, "too extended for a fresh daily-chart entry", "Too Extended"
    if entry_state == "watch reclaim":
        return False, "needs reclaim / stabilization before daily entry is buyable", "Watch Reclaim"
    if entry_state == "pullback forming":
        return False, "pullback is still forming on the daily chart", "Pullback Forming"
    if bucket == "Sponsor Confirmation":
        return False, "confirmation improving but daily entry is not yet buyable", "Watch Only"
    return False, "daily entry still needs better structure", "Watch Only"


def _obvious_leader_penalty(row: Dict[str, Any], market_cap: Optional[float]) -> float:
    avg_dvol = _f(row.get("avg_dollar_volume_20"))
    current_dvol = _f(row.get("current_dollar_volume"))
    mcap = market_cap or 0.0
    penalty = (
        _log_norm(max(mcap, 1.0), 10.3, 12.6) * 45.0
        + _log_norm(max(avg_dvol, 1.0), 8.5, 10.5) * 35.0
        + _log_norm(max(current_dvol, 1.0), 8.8, 10.7) * 20.0
    )
    return round(_clip100(penalty), 1)


def _emerging_participation_bonus(row: Dict[str, Any], market_cap: Optional[float]) -> float:
    avg_dvol = _f(row.get("avg_dollar_volume_20"))
    mcap = market_cap or 0.0
    return round(
        _clip100(
            _norm(row["return_20d_pct"], 3.0, 24.0) * 35.0
            + _norm(row["volume_ratio_5d"], 1.0, 2.5) * 35.0
            + (1.0 - _log_norm(max(avg_dvol, 1.0), 8.0, 10.2)) * 15.0
            + (1.0 - _log_norm(max(mcap, 1.0), 9.0, 11.3)) * 15.0
        ),
        1,
    )


def _leader_reset_bonus(row: Dict[str, Any]) -> float:
    return round(
        _clip100(
            _norm(-abs(row["return_5d_pct"] + 1.0), -6.0, 0.0) * 40.0
            + _norm(row["return_20d_pct"], 6.0, 25.0) * 35.0
            + (1.0 - _norm(row["volume_ratio_5d"], 1.6, 3.0)) * 25.0
        ),
        1,
    )


def _sleeve_resemblance(value: Any) -> str:
    raw = str(value or "").strip().upper()
    if raw == "SNIPER":
        return "Sniper v6 resemblance"
    if raw == "VOYAGER":
        return "Voyager resemblance"
    if raw == "SHORT":
        return "Short A resemblance"
    if raw == "REMORA":
        return "Remora resemblance (research-only)"
    if raw == "CONTRARIAN":
        return "Contrarian resemblance (research-only)"
    if raw == "PATHFINDER":
        return "Pathfinder resemblance (research-only)"
    return "No active sleeve resemblance"


def _quote_now(symbols: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    if not symbols:
        return {}
    try:
        return get_fmp().get_quotes_batch(list(symbols))
    except Exception:
        return {}


def _dominant_sectors(items: Sequence[Dict[str, Any]], limit: int = 3) -> List[str]:
    counts: Dict[str, int] = {}
    for item in items:
        sector = str(item.get("sector") or "Unknown").strip() or "Unknown"
        counts[sector] = counts.get(sector, 0) + 1
    return [name for name, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]]


def _bucket_for_overlay(action_state: str, base_bucket: str) -> str:
    state = str(action_state or "").lower()
    if state in {"still buyable"}:
        return "Buyable Now"
    if state in {"wait for pullback", "watch reclaim", "stalk only", "stronger than expected"}:
        return "Pullback Watch"
    if state in {"gapped too far", "too crowded now"}:
        return "Too Late / Crowded"
    if base_bucket == "Buyable Pullback":
        return "Pullback Watch"
    return base_bucket


def _load_snapshot(path: Optional[Path] = None) -> Dict[str, Any]:
    snap_path = path or (cfg.CACHE_DIR / "universe" / "universe_snapshot_latest.json")
    if not snap_path.exists():
        return {}
    try:
        return json.loads(snap_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _best_resemblance_map(snapshot: Dict[str, Any]) -> Dict[str, str]:
    by_symbol: Dict[str, Tuple[float, str]] = {}
    for row in snapshot.get("strategy_candidates") or []:
        sym = str(row.get("symbol") or "").upper()
        if not sym:
            continue
        score = _f(row.get("final_score"))
        strat = str(row.get("strategy") or "")
        prev = by_symbol.get(sym)
        if prev is None or score > prev[0]:
            by_symbol[sym] = (score, strat)
    return {sym: _sleeve_resemblance(strat) for sym, (_, strat) in by_symbol.items()}


def _baseline_seed_rows(snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    metadata = snapshot.get("metadata") or {}
    if not isinstance(metadata, dict):
        return []
    rows: List[Dict[str, Any]] = []
    for symbol, raw in metadata.items():
        if not isinstance(raw, dict):
            continue
        symbol = str(symbol or "").upper().strip()
        if not symbol or symbol in _KNOWN_INSTRUMENTS:
            continue
        price = _f(raw.get("price"))
        avg_dvol = _f(raw.get("avg_dollar_vol_20"))
        current_dvol = avg_dvol * max(_f(raw.get("volume_ratio_5d"), 1.0), 0.1)
        row = {
            "symbol": symbol,
            "price": price,
            "avg_dollar_volume_20": avg_dvol,
            "current_dollar_volume": current_dvol,
            "return_5d_pct": _f(raw.get("return_5d_pct")),
            "return_20d_pct": _f(raw.get("return_20d_pct")),
            "volume_ratio_5d": _f(raw.get("volume_ratio_5d"), 1.0),
            "atr_pct_14": _f(raw.get("atr_pct_14")),
            "bars_stale": bool(raw.get("bars_stale")),
            "last_bar_date": raw.get("last_bar_date"),
            "scores": raw.get("scores") or {},
        }
        if price < UNIVERSE_DEFINITION["price_floor"]:
            continue
        if avg_dvol < UNIVERSE_DEFINITION["avg_dollar_volume_floor"]:
            continue
        if current_dvol < UNIVERSE_DEFINITION["current_dollar_volume_floor"]:
            continue
        if row["bars_stale"]:
            continue
        rows.append(row)
    return rows


def _prelim_rank(row: Dict[str, Any]) -> float:
    return (
        _norm(row["return_20d_pct"], -10, 30) * 35.0
        + _norm(row["return_5d_pct"], -6, 8) * 15.0
        + _norm(row["volume_ratio_5d"], 0.8, 2.5) * 20.0
        + _log_norm(row["avg_dollar_volume_20"], 7.0, 9.7) * 15.0
        + _norm(6.0 - abs(row["atr_pct_14"] - 4.0), -4.0, 6.0) * 15.0
    )


def _profile_filter(profile: Optional[Dict[str, Any]], row: Dict[str, Any]) -> bool:
    if not profile:
        return row["avg_dollar_volume_20"] >= 25_000_000
    market_cap = _f(profile.get("marketCap"))
    if market_cap <= 0:
        return row["avg_dollar_volume_20"] >= 25_000_000
    return (
        market_cap >= UNIVERSE_DEFINITION["market_cap_floor"]
        and market_cap <= UNIVERSE_DEFINITION["market_cap_ceiling"]
    )


def _latest_quarters(fundamentals: Optional[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    if not fundamentals:
        return None, None, None, None
    income = fundamentals.get("income") or []
    balance = fundamentals.get("balance") or []
    cashflow = fundamentals.get("cashflow") or []
    q0 = income[0] if len(income) > 0 else None
    q1 = income[1] if len(income) > 1 else None
    b0 = balance[0] if len(balance) > 0 else None
    c0 = cashflow[0] if len(cashflow) > 0 else None
    return q0, q1, b0, c0


def _business_inflection_score(fundamentals: Optional[Dict[str, Any]]) -> ScoreBlock:
    q0, q1, b0, c0 = _latest_quarters(fundamentals)
    if not q0:
        return ScoreBlock(None, "fundamentals unavailable")

    revenue0 = _f(q0.get("revenue"))
    revenue1 = _f(q1.get("revenue")) if q1 else 0.0
    net0 = _f(q0.get("netIncome"))
    net1 = _f(q1.get("netIncome")) if q1 else 0.0
    op0 = _f(q0.get("operatingIncome"))
    op1 = _f(q1.get("operatingIncome")) if q1 else 0.0
    gp0 = _f(q0.get("grossProfitRatio"))
    gp1 = _f(q1.get("grossProfitRatio")) if q1 else 0.0
    ocf0 = _f(c0.get("operatingCashFlow")) if c0 else 0.0
    debt = _f(b0.get("totalDebt")) if b0 else 0.0
    equity = _f(b0.get("totalStockholdersEquity")) if b0 else 0.0

    rev_growth = _norm((revenue0 / revenue1 - 1.0) if revenue1 > 0 else 0.0, -0.05, 0.20)
    margin_improve = _norm((op0 / revenue0 - op1 / revenue1) if revenue0 > 0 and revenue1 > 0 else 0.0, -0.03, 0.05)
    profit_inflect = _norm(net0 - net1, -100_000_000, 150_000_000)
    ocf_strength = _norm(ocf0, -50_000_000, 250_000_000)
    balance_quality = _norm((equity / debt) if debt > 0 else 2.0, 0.2, 2.0)
    gross_margin = _norm(gp0 - gp1, -0.03, 0.04)

    score = _score_from_components([rev_growth, margin_improve, profit_inflect, ocf_strength, balance_quality, gross_margin])
    detail = (
        f"rev trend {rev_growth*100:.0f} | margin {margin_improve*100:.0f} | "
        f"ocf {ocf_strength*100:.0f}"
    )
    return ScoreBlock(score, detail)


def _sponsorship_score(row: Dict[str, Any]) -> ScoreBlock:
    score = _score_from_components([
        _norm(row["return_20d_pct"], -5.0, 25.0),
        _norm(row["return_5d_pct"], -3.0, 7.0),
        _norm(row["volume_ratio_5d"], 0.9, 2.2),
        _log_norm(row["current_dollar_volume"], 7.0, 10.0),
        _log_norm(row["avg_dollar_volume_20"], 7.0, 10.0),
    ])
    detail = (
        f"20d {row['return_20d_pct']:+.1f}% | 5d {row['return_5d_pct']:+.1f}% | "
        f"vol {row['volume_ratio_5d']:.1f}x"
    )
    return ScoreBlock(score or 0.0, detail)


def _daily_entry_state(row: Dict[str, Any]) -> Tuple[str, str]:
    ret5 = row["return_5d_pct"]
    ret20 = row["return_20d_pct"]
    atr = row["atr_pct_14"]
    vol = row["volume_ratio_5d"]
    if ret5 >= 4.0 or (ret20 >= 24.0 and ret5 >= 1.5) or vol >= 2.2:
        return "too extended", "too extended"
    if ret20 >= 5.0 and -3.5 <= ret5 <= 1.2 and atr <= 6.5 and vol <= 1.8:
        return "buyable now", "buyable now"
    if ret20 >= 5.0 and 1.2 < ret5 <= 4.0 and atr <= 7.0:
        return "watch reclaim", "watch reclaim"
    if ret20 >= 4.0 and -6.0 <= ret5 < -1.2:
        return "pullback forming", "pullback forming"
    return "watch only", "watch only"


def _entry_quality_score(row: Dict[str, Any]) -> ScoreBlock:
    ret5 = row["return_5d_pct"]
    ret20 = row["return_20d_pct"]
    atr = row["atr_pct_14"]
    vol = row["volume_ratio_5d"]
    pullback_zone = 1.0 - min(abs(ret5 + 2.0) / 4.5, 1.0)
    trend_health = _norm(ret20, 5.0, 22.0)
    not_broken = _norm(ret5, -6.0, 1.0)
    volatility_fit = 1.0 - min(abs(atr - 4.0) / 4.5, 1.0)
    not_late = 1.0 - _norm(ret5, 2.5, 9.0)
    vol_fit = 1.0 - _norm(vol, 1.8, 3.0)
    state, detail = _daily_entry_state(row)
    score = _score_from_components([pullback_zone, trend_health, not_broken, volatility_fit, not_late])
    if score is None:
        return ScoreBlock(0.0, detail)
    state_bonus = {
        "buyable now": 8.0,
        "watch reclaim": -2.0,
        "pullback forming": -6.0,
        "watch only": -12.0,
        "too extended": -18.0,
    }.get(state, 0.0)
    score = round(_clip100(score + state_bonus + (vol_fit * 6.0)), 1)
    return ScoreBlock(score or 0.0, detail)


def _crowd_penalty(row: Dict[str, Any]) -> ScoreBlock:
    ret5 = row["return_5d_pct"]
    ret20 = row["return_20d_pct"]
    vol = row["volume_ratio_5d"]
    atr = row["atr_pct_14"]
    penalty = (
        _norm(ret5, 7.0, 18.0) * 40.0
        + _norm(ret20, 22.0, 45.0) * 30.0
        + _norm(vol, 1.8, 3.5) * 15.0
        + _norm(atr, 6.0, 12.0) * 15.0
    )
    detail = (
        "vertical / crowded"
        if penalty >= 70
        else "warming up"
        if penalty >= 45
        else "crowd manageable"
    )
    return ScoreBlock(round(_clip100(penalty), 1), detail)


def _liquidity_score(row: Dict[str, Any], market_cap: Optional[float]) -> ScoreBlock:
    price_score = _norm(row["price"], 8.0, 80.0)
    avg_dvol_score = _log_norm(row["avg_dollar_volume_20"], 7.0, 10.3)
    cur_dvol_score = _log_norm(row["current_dollar_volume"], 6.8, 10.3)
    cap_score = _log_norm(market_cap, 8.5, 10.9) if market_cap else None
    score = _score_from_components([price_score, avg_dvol_score, cur_dvol_score, cap_score] if cap_score is not None else [price_score, avg_dvol_score, cur_dvol_score])
    detail = f"avg ${row['avg_dollar_volume_20']/1_000_000:.0f}M | cur ${row['current_dollar_volume']/1_000_000:.0f}M"
    return ScoreBlock(score or 0.0, detail)


def _safe_import(path: Path, name: str):
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None


def _thirteen_f_overlay(symbol: str, tracker: Any) -> ScoreBlock:
    if tracker is None:
        return ScoreBlock(None, "13F unavailable")
    try:
        activity = tracker.get_institutional_activity(symbol)
    except Exception:
        activity = None
    if not activity:
        return ScoreBlock(None, "13F unavailable")
    flow = str(activity.get("net_flow") or "UNKNOWN").upper()
    confidence = str(activity.get("confidence") or "UNKNOWN").upper()
    mapping = {
        ("BUYING", "HIGH"): 90.0,
        ("BUYING", "MODERATE"): 78.0,
        ("BUYING", "LOW"): 65.0,
        ("MIXED", "HIGH"): 58.0,
        ("MIXED", "MODERATE"): 55.0,
        ("NEUTRAL", "UNKNOWN"): 50.0,
        ("SELLING", "HIGH"): 20.0,
        ("SELLING", "MODERATE"): 30.0,
        ("SELLING", "LOW"): 40.0,
    }
    score = mapping.get((flow, confidence), 50.0 if flow in {"NEUTRAL", "UNKNOWN"} else 45.0)
    detail = f"{flow.lower()} / {confidence.lower()}"
    return ScoreBlock(score, detail)


def _tradier_overlay(symbol: str, feed: Any) -> ScoreBlock:
    if not getattr(feed, "is_configured", lambda: False)():
        return ScoreBlock(None, "Tradier unavailable")
    try:
        expirations = list(feed.get_expirations(symbol))
        if not expirations:
            return ScoreBlock(None, "Tradier chain unavailable")
        chain = feed.get_chain(symbol, expirations[0])
        if not chain:
            return ScoreBlock(None, "Tradier chain unavailable")
        calls = chain.get("calls")
        puts = chain.get("puts")
        if calls is None or puts is None or calls.empty or puts.empty:
            return ScoreBlock(None, "Tradier chain unavailable")
        call_oi = float(calls["openInterest"].sum() or 0)
        put_oi = float(puts["openInterest"].sum() or 0)
        call_vol = float(calls["volume"].sum() or 0)
        put_vol = float(puts["volume"].sum() or 0)
        oi_tilt = (call_oi + 1.0) / (put_oi + 1.0)
        vol_tilt = (call_vol + 1.0) / (put_vol + 1.0)
        score = _clip100((_norm(oi_tilt, 0.8, 1.8) * 60.0) + (_norm(vol_tilt, 0.8, 1.8) * 40.0))
        detail = f"call/put oi {oi_tilt:.2f} | vol {vol_tilt:.2f}"
        return ScoreBlock(round(score, 1), detail)
    except Exception:
        return ScoreBlock(None, "Tradier unavailable")


def _tier_for_layers(layers: List[str]) -> str:
    layer_set = set(layers)
    if {"Alpaca", "FMP", "13F", "Tradier"}.issubset(layer_set):
        return "A"
    if {"Alpaca", "FMP"}.issubset(layer_set):
        return "B"
    return "C"


def _why_now(track: str, bucket: str, business: ScoreBlock, sponsorship: ScoreBlock, entry: ScoreBlock) -> str:
    if track == "Liquid Leadership Reset" and bucket == "Buyable Pullback":
        return f"known leader in reset | {entry.detail}"
    if track == "Emerging Opportunity" and bucket in {"Early Discovery", "Sponsor Confirmation"}:
        if business.score and business.score >= 65:
            return f"emerging sponsor + business inflection | {business.detail}"
        return f"emerging participation from lower base | {sponsorship.detail}"
    if bucket == "Buyable Pullback":
        return f"buyable reset | {entry.detail}"
    if bucket == "Sponsor Confirmation":
        return f"sponsorship confirming | {sponsorship.detail}"
    if bucket == "Too Late / Crowded":
        return "leader but entry too stretched"
    if business.score and business.score >= 65:
        return f"business improving | {business.detail}"
    return f"tape improvement early | {sponsorship.detail}"


def _main_risk(track: str, bucket: str, business: ScoreBlock, sponsorship: ScoreBlock, entry: ScoreBlock, crowd: ScoreBlock) -> str:
    if bucket == "Too Late / Crowded":
        return "extension / crowding makes current risk-reward poor"
    if track == "Emerging Opportunity" and business.score is None:
        return "emerging tape is interesting, but business layer is still missing"
    if business.score is None:
        return "business layer missing; tape-led idea only"
    if business.score < 45:
        return "business improvement evidence is still thin"
    if entry.score is not None and entry.score < 50:
        return "entry quality still needs pullback or reclaim"
    if crowd.score is not None and crowd.score >= 55:
        return "name is heating up too quickly"
    return "sponsorship could fade without further confirmation"


# ── Stock Lens annotation ────────────────────────────────────────────────────
#
# These helpers decorate Alpha board items with the fresh Stock Lens state
# so the board surface mirrors lens-level evidence without a second lookup.
# Pure read-only — they never call providers, never mutate the DB, and never
# alter execution / governance.  See docs/ in CLAUDE.md "Hard separation
# rule".


def _strictness(label: Optional[str]) -> int:
    """Higher = more conservative.  Unknown labels rank lowest (no downgrade)."""
    if not label:
        return -1
    return _STRICTNESS_ORDER.get(str(label).strip(), -1)


def _lens_artifact_path(ticker: str) -> Path:
    return _ALPHA_RESEARCH_DIR / f"stock_lens_{ticker.upper()}_latest.json"


def _gatekeeper_artifact_path(ticker: str) -> Path:
    return _ALPHA_RESEARCH_DIR / f"executive_gatekeeper_{ticker.upper()}_latest.json"


def _read_lens_from_disk(ticker: str) -> Optional[Dict[str, Any]]:
    p = _lens_artifact_path(ticker)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    data["_mtime"] = p.stat().st_mtime
    return data


def _read_gatekeeper_from_disk(ticker: str) -> Optional[Dict[str, Any]]:
    p = _gatekeeper_artifact_path(ticker)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return data


def annotate_alpha_items_with_lens(
    items: Sequence[Dict[str, Any]],
    *,
    fresh_hours: float = LENS_FRESH_HOURS_DEFAULT,
    now_ts: Optional[float] = None,
    read_lens: Callable[[str], Optional[Dict[str, Any]]] = _read_lens_from_disk,
    read_gatekeeper: Optional[Callable[[str], Optional[Dict[str, Any]]]] = _read_gatekeeper_from_disk,
) -> List[Dict[str, Any]]:
    """Decorate Alpha board items with Stock Lens evidence + warning flags.

    For each item this function:

    * Reads the per-ticker lens artifact (cache-only; missing → ``lens_missing=True``).
    * Computes ``lens_age_hours`` and ``lens_stale`` (default threshold 24 h).
    * Copies ``lens_label``, ``entry_validator_state``, ``options_quality``
      onto the item.
    * Optionally copies ``gatekeeper_status`` from the per-ticker Gatekeeper
      artifact.
    * Appends an ``OPTIONS_*`` warning to ``alpha_flags`` when the fresh
      lens flags a contradicting / weak options quality.
    * Sets ``board_lens_conflict=True`` and downgrades ``action_label`` to
      the stricter lens label when a fresh lens disagrees with the board.
    * Preserves the original board labels in ``original_action_label`` /
      ``original_alpha_score`` so the audit chain stays intact.
    * **Never** trusts a stale lens as the current truth — stale lenses are
      surfaced with ``LENS_STALE`` flag but do not drive downgrades or
      options warnings.

    Returns a new list of dict items; the input is not mutated.
    """
    if now_ts is None:
        now_ts = time.time()
    fresh_hours = float(fresh_hours)
    # Flags this function owns and re-derives on every call.  Anything else
    # in alpha_flags (e.g. flags set by other systems) is preserved.
    managed_flags = (
        {"LENS_STALE", "BOARD_LENS_CONFLICT"}
        | set(_OPTIONS_QUALITY_TO_FLAG.values())
    )
    annotated: List[Dict[str, Any]] = []
    for raw in items:
        if not isinstance(raw, dict):
            annotated.append(raw)  # type: ignore[arg-type]
            continue
        new_it: Dict[str, Any] = dict(raw)
        ticker = str(new_it.get("ticker") or "").strip().upper()
        # Strip stale managed flags before re-deriving so re-annotation is
        # idempotent (the lens may have moved from stale → fresh, conflict
        # → no-conflict, options BEARISH_HEDGE → BULLISH_CONFIRMING).
        flags: List[str] = [
            f for f in (new_it.get("alpha_flags") or []) if f not in managed_flags
        ]
        new_it.setdefault("original_action_label", new_it.get("action_label"))
        new_it.setdefault("original_alpha_score", new_it.get("alpha_score"))

        lens = read_lens(ticker) if ticker else None
        if not isinstance(lens, dict):
            new_it["lens_label"] = None
            new_it["lens_age_hours"] = None
            new_it["lens_stale"] = None
            new_it["lens_missing"] = True
            new_it["entry_validator_state"] = None
            new_it["options_quality"] = None
            new_it["board_lens_conflict"] = False
            new_it["alpha_flags"] = flags
            annotated.append(new_it)
            continue

        mtime = lens.get("_mtime")
        age_h: Optional[float] = None
        if mtime is not None:
            try:
                age_h = max(0.0, (float(now_ts) - float(mtime)) / 3600.0)
            except (TypeError, ValueError):
                age_h = None
        stale = bool(age_h is not None and age_h > fresh_hours)
        new_it["lens_age_hours"] = round(age_h, 2) if age_h is not None else None
        new_it["lens_stale"] = stale
        new_it["lens_missing"] = False
        new_it["lens_label"] = lens.get("label")

        layers = lens.get("layers") or {}
        ev = layers.get("entry_validator") or {}
        validator_view = ev.get("view") or ev.get("state")
        new_it["entry_validator_state"] = validator_view

        opt = layers.get("options") or {}
        options_quality = opt.get("options_quality") or opt.get("quality") or None
        if options_quality:
            options_quality = str(options_quality).strip().upper()
        new_it["options_quality"] = options_quality

        if read_gatekeeper is not None and ticker:
            gk = read_gatekeeper(ticker)
            if isinstance(gk, dict):
                status = str(gk.get("final_status") or "").strip().upper() or None
                if status:
                    new_it["gatekeeper_status"] = status

        # Only act on a FRESH lens.  A stale lens is surfaced but never
        # treated as the current state.
        conflict = False
        if not stale:
            mapped = _OPTIONS_QUALITY_TO_FLAG.get(str(options_quality or ""))
            if mapped and mapped not in flags:
                flags.append(mapped)

            if validator_view:
                board_label = str(new_it.get("action_label") or "")
                lens_label = str(validator_view).strip()
                if _strictness(lens_label) > _strictness(board_label):
                    new_it["original_action_label"] = (
                        new_it.get("original_action_label") or board_label
                    )
                    new_it["action_label"] = lens_label
                    if lens_label in _NON_ACTIONABLE_LABELS:
                        new_it["actionable_now"] = False
                    conflict = True
        new_it["board_lens_conflict"] = conflict
        if conflict and "BOARD_LENS_CONFLICT" not in flags:
            flags.append("BOARD_LENS_CONFLICT")

        if stale and "LENS_STALE" not in flags:
            flags.append("LENS_STALE")

        new_it["alpha_flags"] = flags
        annotated.append(new_it)
    return annotated


def annotate_alpha_board_on_disk(
    *,
    board_path: Optional[Path] = None,
    overlay_path: Optional[Path] = None,
    fresh_hours: float = LENS_FRESH_HOURS_DEFAULT,
) -> Dict[str, Any]:
    """Read board (and optional overlay), annotate items in place, save back.

    Cache-only: never invokes a provider, never mutates DB / paper evidence.
    Returns a summary dict with counts so callers can log a one-line status.
    """
    out: Dict[str, Any] = {
        "board": {"updated": False, "items": 0, "annotated": 0,
                  "with_lens": 0, "stale_lens": 0, "conflicts": 0},
        "overlay": {"updated": False, "items": 0, "annotated": 0,
                    "with_lens": 0, "stale_lens": 0, "conflicts": 0},
        "fresh_hours": float(fresh_hours),
        "annotated_at": datetime.utcnow().isoformat(),
    }
    for kind, path, save_fn in (
        ("board", board_path or _ALPHA_JSON_PATH, save_alpha_discovery_board),
        ("overlay", overlay_path or _ALPHA_OVERLAY_JSON_PATH, save_alpha_discovery_overlay),
    ):
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        items = data.get("items") or []
        if not items:
            data["items"] = []
            save_fn(data)
            continue
        annotated = annotate_alpha_items_with_lens(items, fresh_hours=fresh_hours)
        data["items"] = annotated
        data.setdefault("lens_annotation", {})
        data["lens_annotation"].update({
            "annotated_at": out["annotated_at"],
            "fresh_hours": float(fresh_hours),
        })
        save_fn(data)
        out[kind] = {
            "updated": True,
            "items": len(annotated),
            "annotated": sum(1 for it in annotated if not it.get("lens_missing")),
            "with_lens": sum(1 for it in annotated if not it.get("lens_missing")),
            "stale_lens": sum(1 for it in annotated if it.get("lens_stale")),
            "conflicts": sum(1 for it in annotated if it.get("board_lens_conflict")),
        }
    return out


# ── Enrichment cap transparency + rotation queue ─────────────────────────────


def _load_enrichment_rotation_state() -> Dict[str, Any]:
    p = _ALPHA_ENRICHMENT_ROTATION_PATH
    if not p.exists():
        return {"last_enriched_at": {}, "version": 1}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"last_enriched_at": {}, "version": 1}
        data.setdefault("last_enriched_at", {})
        return data
    except Exception:
        return {"last_enriched_at": {}, "version": 1}


def _save_enrichment_rotation_state(state: Dict[str, Any]) -> None:
    p = _ALPHA_ENRICHMENT_ROTATION_PATH
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    except Exception:
        pass


def _build_enrichment_transparency(
    *,
    candidate_band_symbols: Sequence[str],
    profiles: Dict[str, Optional[Dict[str, Any]]],
    fundamentals: Dict[str, Optional[Dict[str, Any]]],
    overlay_13f: Dict[str, "ScoreBlock"],
    overlay_tradier: Dict[str, "ScoreBlock"],
    overlay_symbols: Iterable[str],
    profile_target: int,
    fundamentals_target: int,
) -> Dict[str, Any]:
    """Compute the transparency block — what got enriched, what didn't, why,
    and which tickers should rotate to the front of the queue next run.

    Does not raise the enrichment caps.  This is observability only.
    """
    overlay_set = set(overlay_symbols)
    candidate_set: List[str] = [str(s).upper() for s in candidate_band_symbols if s]
    # An item is "enriched" if it has any provider-derived layer beyond
    # Alpaca: profile, fundamentals, 13F overlay, or Tradier overlay.
    enriched: List[str] = []
    not_enriched: List[Dict[str, str]] = []
    for sym in candidate_set:
        has_profile = bool(profiles.get(sym))
        has_fund = bool(fundamentals.get(sym))
        thf = overlay_13f.get(sym)
        trad = overlay_tradier.get(sym)
        has_13f = thf is not None and thf.score is not None
        has_trad = trad is not None and trad.score is not None
        if has_profile or has_fund or has_13f or has_trad:
            enriched.append(sym)
        else:
            # Reason: was the cap reached at the profile stage?
            try:
                idx = candidate_set.index(sym)
            except ValueError:
                idx = -1
            if idx >= 0 and idx >= max(0, profile_target):
                reason = "cap_bound"
            elif sym in overlay_set:
                reason = "missing_data"
            else:
                reason = "low_priority"
            not_enriched.append({"ticker": sym, "reason": reason})

    # Rotation: anything not enriched this run is a candidate.  Persistent
    # queue prioritises tickers that have been missing the longest.
    state = _load_enrichment_rotation_state()
    last_seen = state.get("last_enriched_at") or {}
    now_iso = datetime.utcnow().isoformat()
    for sym in enriched:
        last_seen[sym] = now_iso
    state["last_enriched_at"] = last_seen
    _save_enrichment_rotation_state(state)

    # Sort not-enriched by "longest since last enriched" — never enriched
    # ranks ahead of anything else.
    def _rotation_key(entry: Dict[str, str]) -> Tuple[int, str]:
        ts = last_seen.get(entry["ticker"])
        return (0, "") if ts is None else (1, ts)

    not_enriched_sorted = sorted(not_enriched, key=_rotation_key)
    rotation_top: List[str] = [e["ticker"] for e in not_enriched_sorted[:25]]
    for entry in not_enriched_sorted:
        entry["enrichment_rotation_candidate"] = entry["ticker"] in rotation_top

    return {
        "candidate_band_size": len(candidate_set),
        "profile_target": int(profile_target),
        "fundamentals_target": int(fundamentals_target),
        "enriched_count": len(enriched),
        "not_enriched_count": len(not_enriched),
        "not_enriched_tickers": [e["ticker"] for e in not_enriched_sorted],
        "not_enriched_details": not_enriched_sorted,
        "rotation_queue_top": rotation_top,
    }


def prewarm_alpha_discovery_enrichment(
    *,
    snapshot_path: Optional[Path] = None,
    seed_limit: int = 320,
    profile_limit: int = 240,
    fundamentals_limit: int = 160,
) -> Dict[str, Any]:
    snapshot = _load_snapshot(snapshot_path)
    if not snapshot:
        return {
            "built_at": datetime.utcnow().isoformat(),
            "error": "universe snapshot unavailable",
            "seed_rows": 0,
            "candidate_band": 0,
            "profile_target": 0,
            "fundamentals_target": 0,
            "profile_rows": 0,
            "fundamental_rows": 0,
            "symbols": [],
        }

    seed_rows = _baseline_seed_rows(snapshot)
    seed_rows.sort(key=_prelim_rank, reverse=True)
    candidate_band = seed_rows[: max(0, seed_limit)]

    fmp = get_fmp()
    profiles: Dict[str, Optional[Dict[str, Any]]] = {}
    fundamentals: Dict[str, Optional[Dict[str, Any]]] = {}

    for row in candidate_band[: max(0, profile_limit)]:
        sym = row["symbol"]
        try:
            profiles[sym] = fmp.get_company_profile(sym)
        except Exception:
            profiles[sym] = None

    filtered = [r for r in candidate_band if _profile_filter(profiles.get(r["symbol"]), r)]

    for row in filtered[: max(0, fundamentals_limit)]:
        sym = row["symbol"]
        try:
            fundamentals[sym] = fmp.get_fundamentals(sym)
        except Exception:
            fundamentals[sym] = None

    summary = {
        "built_at": datetime.utcnow().isoformat(),
        "seed_rows": len(seed_rows),
        "candidate_band": len(candidate_band),
        "profile_target": min(len(candidate_band), max(0, profile_limit)),
        "fundamentals_target": min(len(filtered), max(0, fundamentals_limit)),
        "profile_rows": sum(1 for v in profiles.values() if v),
        "fundamental_rows": sum(1 for v in fundamentals.values() if v),
        "symbols": [row["symbol"] for row in candidate_band[:25]],
    }

    _ALPHA_RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    _ALPHA_ENRICHMENT_JSON_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def load_alpha_discovery_enrichment(path: Optional[Path] = None) -> Dict[str, Any]:
    target = path or _ALPHA_ENRICHMENT_JSON_PATH
    if not target.exists():
        return {}
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return {}


def build_alpha_discovery_board(
    *,
    limit: int = 20,
    profile_limit: int = 120,
    fundamentals_limit: int = 80,
    overlay_limit: int = 25,
    use_fmp: bool = True,
    use_13f: bool = True,
    use_tradier: bool = True,
    snapshot_path: Optional[Path] = None,
) -> Dict[str, Any]:
    snapshot = _load_snapshot(snapshot_path)
    if not snapshot:
        return {"version": ALPHA_DISCOVERY_VERSION, "error": "universe snapshot unavailable", "items": []}

    enrichment_cache = load_alpha_discovery_enrichment()
    effective_profile_limit = max(profile_limit, int(enrichment_cache.get("profile_target") or 0))
    effective_fundamentals_limit = max(fundamentals_limit, int(enrichment_cache.get("fundamentals_target") or 0))

    resemblance_map = _best_resemblance_map(snapshot)
    seed_rows = _baseline_seed_rows(snapshot)
    seed_rows.sort(key=_prelim_rank, reverse=True)

    profiles: Dict[str, Optional[Dict[str, Any]]] = {}
    fundamentals: Dict[str, Optional[Dict[str, Any]]] = {}
    if use_fmp:
        fmp = get_fmp()
        for row in seed_rows[:effective_profile_limit]:
            sym = row["symbol"]
            try:
                profiles[sym] = fmp.get_company_profile(sym)
            except Exception:
                profiles[sym] = None
        filtered = [r for r in seed_rows if _profile_filter(profiles.get(r["symbol"]), r)]
        for row in filtered[:effective_fundamentals_limit]:
            sym = row["symbol"]
            try:
                fundamentals[sym] = fmp.get_fundamentals(sym)
            except Exception:
                fundamentals[sym] = None
    else:
        filtered = seed_rows

    provisional: List[Tuple[float, Dict[str, Any]]] = []
    for row in filtered:
        sym = row["symbol"]
        profile = profiles.get(sym)
        market_cap = _f((profile or {}).get("marketCap")) or None
        track = _track_label(row, market_cap)

        business = _business_inflection_score(fundamentals.get(sym))
        sponsorship = _sponsorship_score(row)
        entry = _entry_quality_score(row)
        entry_state, _ = _daily_entry_state(row)
        crowd = _crowd_penalty(row)
        liquidity = _liquidity_score(row, market_cap)

        positive_blocks = {
            "business": business.score,
            "sponsorship": sponsorship.score,
            "entry": entry.score,
            "liquidity": liquidity.score,
        }
        weights = {"business": 0.30, "sponsorship": 0.30, "entry": 0.24, "liquidity": 0.16}
        usable_weight = sum(weights[k] for k, v in positive_blocks.items() if v is not None)
        total = 0.0
        if usable_weight <= 0:
            continue
        for key, score in positive_blocks.items():
            if score is None:
                continue
            total += score * (weights[key] / usable_weight)
        total -= (crowd.score or 0.0) * 0.18
        if track == "Emerging Opportunity":
            total += _emerging_participation_bonus(row, market_cap) * 0.16
            total -= _obvious_leader_penalty(row, market_cap) * 0.20
        else:
            total += _leader_reset_bonus(row) * 0.10
        provisional.append((round(_clip100(total), 1), {**row, "_track": track, "_market_cap": market_cap}))

    provisional.sort(key=lambda item: item[0], reverse=True)

    leader_rows = [row for _, row in provisional if str(row.get("_track")) == "Liquid Leadership Reset"]
    emerging_rows = [row for _, row in provisional if str(row.get("_track")) == "Emerging Opportunity"]
    leader_cap = max(1, limit // 2)
    emerging_cap = max(1, limit - leader_cap)
    finalist_rows = leader_rows[:leader_cap] + emerging_rows[:emerging_cap]
    if len(finalist_rows) < limit:
        taken = {row["symbol"] for row in finalist_rows}
        remainder_rows = [row for _, row in provisional if row["symbol"] not in taken]
        finalist_rows.extend(remainder_rows[: max(0, limit - len(finalist_rows))])

    if use_fmp:
        fmp = get_fmp()
        for row in finalist_rows:
            sym = row["symbol"]
            if not profiles.get(sym):
                try:
                    profiles[sym] = fmp.get_company_profile(sym)
                except Exception:
                    profiles[sym] = None
            if fundamentals.get(sym) is None:
                try:
                    fundamentals[sym] = fmp.get_fundamentals(sym)
                except Exception:
                    fundamentals[sym] = None

    overlay_symbols = {row["symbol"] for _, row in provisional[:overlay_limit]}
    validation_symbols = [row["symbol"] for _, row in provisional[: max(limit * 4, 40)]]
    bars_map: Dict[str, List[Dict[str, Any]]] = {}
    try:
        bars_map = get_alpaca().get_daily_bars_batch(validation_symbols, days=260)
    except Exception:
        bars_map = {}

    overlay_13f: Dict[str, ScoreBlock] = {}
    overlay_tradier: Dict[str, ScoreBlock] = {}
    tracker = None
    if use_13f:
        try:
            from core.whale_tracker import get_whale_tracker
            tracker = get_whale_tracker()
        except Exception:
            tracker = None
    tradier_feed = None
    if use_tradier:
        # Use the shared Alpaca-first / Tradier-fallback chain. The local
        # variable name is kept (``tradier_feed``) so downstream call
        # sites remain unchanged; the chain wrapper exposes the same
        # is_configured / get_expirations / get_chain contract.
        try:
            from core.options_feed_factory import load_options_feed
            tradier_feed = load_options_feed()
        except Exception:
            tradier_feed = None
    if use_13f:
        for sym in overlay_symbols:
            overlay_13f[sym] = _thirteen_f_overlay(sym, tracker)
    if use_tradier:
        for sym in overlay_symbols:
            overlay_tradier[sym] = _tradier_overlay(sym, tradier_feed)

    items: List[AlphaDiscoveryCandidate] = []
    for _, row in provisional:
        sym = row["symbol"]
        profile = profiles.get(sym) or {}
        market_cap = row.get("_market_cap") or _f(profile.get("marketCap")) or None
        track = str(row.get("_track") or _track_label(row, market_cap))
        business = _business_inflection_score(fundamentals.get(sym))
        sponsorship = _sponsorship_score(row)
        entry = _entry_quality_score(row)
        validation = validate_daily_entry(bars_map.get(sym) or [])
        crowd = _crowd_penalty(row)
        liquidity = _liquidity_score(row, market_cap)
        thf = overlay_13f.get(sym, ScoreBlock(None, "13F skipped"))
        trad = overlay_tradier.get(sym, ScoreBlock(None, "Tradier skipped"))

        positive_blocks = {
            "business": business.score,
            "sponsorship": sponsorship.score,
            "entry": entry.score,
            "liquidity": liquidity.score,
            "13f": thf.score,
            "tradier": trad.score,
        }
        weights = {
            "business": 0.28,
            "sponsorship": 0.29,
            "entry": 0.23,
            "liquidity": 0.15,
            "13f": 0.03,
            "tradier": 0.02,
        }
        usable_weight = sum(weights[k] for k, v in positive_blocks.items() if v is not None)
        if usable_weight <= 0:
            continue
        total = 0.0
        for key, score in positive_blocks.items():
            if score is None:
                continue
            total += score * (weights[key] / usable_weight)
        total -= (crowd.score or 0.0) * 0.18
        if track == "Emerging Opportunity":
            total += _emerging_participation_bonus(row, market_cap) * 0.16
            total -= _obvious_leader_penalty(row, market_cap) * 0.20
        else:
            total += _leader_reset_bonus(row) * 0.10
        overall = round(_clip100(total), 1)

        bucket = _bucket_label(
            overall=overall,
            sponsorship=sponsorship.score or 0.0,
            entry=entry.score or 0.0,
            crowd_penalty=crowd.score or 0.0,
            entry_state=str(validation.state).lower(),
        )
        if track == "Emerging Opportunity" and bucket == "Buyable Pullback" and _obvious_leader_penalty(row, market_cap) >= 55:
            bucket = "Sponsor Confirmation"
        actionable = bool(validation.actionable_now)
        why_not = "" if actionable else validation.reason
        action_label = validation.state

        layers = ["Alpaca"]
        if profile or fundamentals.get(sym):
            layers.append("FMP")
        if thf.score is not None:
            layers.append("13F")
        if trad.score is not None:
            layers.append("Tradier")

        items.append(
            AlphaDiscoveryCandidate(
                ticker=sym,
                track=track,
                bucket=bucket,
                alpha_score=overall,
                business_inflection_score=business.score,
                sponsorship_score=sponsorship.score or 0.0,
                entry_quality_score=entry.score or 0.0,
                crowd_penalty=crowd.score or 0.0,
                liquidity_score=liquidity.score or 0.0,
                validator_state=validation.state,
                validator_reason=validation.reason,
                validator_flags=list(validation.flags),
                entry_state=str(validation.state).lower(),
                action_label=action_label,
                data_tier=_tier_for_layers(layers),
                data_layers=layers,
                why_now=_why_now(track, bucket, business, sponsorship, entry),
                main_risk=_main_risk(track, bucket, business, sponsorship, entry, crowd),
                sleeve_resemblance=resemblance_map.get(sym, "No active sleeve resemblance"),
                actionable_now=actionable,
                why_not=why_not,
                market_cap=market_cap,
                sector=str(profile.get("sector") or ""),
                industry=str(profile.get("industry") or ""),
                price=row["price"],
                avg_dollar_volume_20=row["avg_dollar_volume_20"],
                current_dollar_volume=row["current_dollar_volume"],
                return_5d_pct=row["return_5d_pct"],
                return_20d_pct=row["return_20d_pct"],
                volume_ratio_5d=row["volume_ratio_5d"],
                overlay_13f=thf.score,
                overlay_tradier=trad.score,
                block_details={
                    "business": business.detail,
                    "sponsorship": sponsorship.detail,
                    "entry": entry.detail,
                    "validator": validation.reason,
                    "crowd": crowd.detail,
                    "liquidity": liquidity.detail,
                    "track_bias": (
                        f"emerging bonus {_emerging_participation_bonus(row, market_cap):.1f} | "
                        f"obvious penalty {_obvious_leader_penalty(row, market_cap):.1f}"
                        if track == "Emerging Opportunity"
                        else f"leader reset bonus {_leader_reset_bonus(row):.1f}"
                    ),
                    "13f": thf.detail,
                    "tradier": trad.detail,
                },
            )
        )

    leader_items = sorted(
        [item for item in items if item.track == "Liquid Leadership Reset"],
        key=lambda item: (-item.alpha_score, item.ticker),
    )
    emerging_items = sorted(
        [item for item in items if item.track == "Emerging Opportunity"],
        key=lambda item: (-item.alpha_score, item.ticker),
    )
    items = leader_items[:leader_cap] + emerging_items[:emerging_cap]
    if len(items) < limit:
        remainder = leader_items[leader_cap:] + emerging_items[emerging_cap:]
        remainder.sort(key=lambda item: (-item.alpha_score, item.ticker))
        items.extend(remainder[: max(0, limit - len(items))])
    items.sort(key=lambda item: (item.track, -item.alpha_score, item.ticker))

    bucket_counts: Dict[str, int] = {}
    track_counts: Dict[str, int] = {}
    tier_counts: Dict[str, int] = {"A": 0, "B": 0, "C": 0}
    for item in items:
        bucket_counts[item.bucket] = bucket_counts.get(item.bucket, 0) + 1
        track_counts[item.track] = track_counts.get(item.track, 0) + 1
        if item.data_tier in tier_counts:
            tier_counts[item.data_tier] += 1

    candidate_band_symbols = [row["symbol"] for _, row in provisional]
    enrichment_transparency = _build_enrichment_transparency(
        candidate_band_symbols=candidate_band_symbols,
        profiles=profiles,
        fundamentals=fundamentals,
        overlay_13f=overlay_13f,
        overlay_tradier=overlay_tradier,
        overlay_symbols=overlay_symbols,
        profile_target=effective_profile_limit,
        fundamentals_target=effective_fundamentals_limit,
    )

    item_dicts = [item.to_dict() for item in items]
    item_dicts = annotate_alpha_items_with_lens(item_dicts)

    output = {
        "version": ALPHA_DISCOVERY_VERSION,
        "mode": "nightly",
        "built_at": datetime.utcnow().isoformat(),
        "subtitle": "Early Opportunity / Buyable Pullback / Sponsor Confirmation",
        "universe_definition": UNIVERSE_DEFINITION,
        "methodology": {
            "positive_blocks": [
                "business_inflection",
                "sponsorship_participation",
                "entry_quality",
                "liquidity_practicality",
            ],
            "penalty_blocks": ["crowd_penalty"],
            "optional_overlays": ["13F", "Tradier"],
            "tracks": ["Liquid Leadership Reset", "Emerging Opportunity"],
            "separation": "research-only; not sleeve approval; not paper evidence; not auto-tradable",
        },
        "coverage": {
            "seed_rows": len(seed_rows),
            "filtered_rows": len(filtered),
            "profile_rows": sum(1 for v in profiles.values() if v),
            "fundamental_rows": sum(1 for v in fundamentals.values() if v),
            "thirteen_f_rows": sum(1 for v in overlay_13f.values() if v.score is not None),
            "tradier_rows": sum(1 for v in overlay_tradier.values() if v.score is not None),
        },
        "enrichment_cache": enrichment_cache,
        "enrichment_transparency": enrichment_transparency,
        "diagnostics": {
            "v1_dominance_causes": [
                "single top-N ranking let high-liquidity leader resets crowd out earlier names",
                "liquidity/practicality rewarded mega-cap consensus leaders without distinguishing useful liquidity from obvious crowding",
                "crowd penalty measured extension but not consensus size / dominant-liquidity obviousness",
                "bucket logic labeled many leader pullbacks as Buyable Pullback before separating them from emerging opportunity",
            ],
            "calibration_change": "V2 keeps one shared discovery universe but splits output into two internal tracks with track-aware scoring and balanced caps.",
        },
        "track_counts": track_counts,
        "bucket_counts": bucket_counts,
        "tier_counts": tier_counts,
        "dominant_sectors": _dominant_sectors(item_dicts),
        "items": item_dicts,
    }
    return output


def save_alpha_discovery_board(board: Dict[str, Any]) -> Dict[str, str]:
    cache_dir = _ALPHA_RESEARCH_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    log_dir = cfg.LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)

    json_path = _ALPHA_JSON_PATH
    txt_path = log_dir / "alpha_discovery_board_latest.txt"
    json_path.write_text(json.dumps(board, indent=2), encoding="utf-8")
    return {"json": str(json_path), "text": str(txt_path)}


def load_alpha_discovery_board(path: Optional[Path] = None) -> Dict[str, Any]:
    target = path or _ALPHA_JSON_PATH
    if not target.exists():
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        data["_mtime_iso"] = datetime.fromtimestamp(target.stat().st_mtime).isoformat()
        return data
    except Exception:
        return {}


def load_alpha_discovery_overlay(path: Optional[Path] = None) -> Dict[str, Any]:
    target = path or _ALPHA_OVERLAY_JSON_PATH
    if not target.exists():
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        data["_mtime_iso"] = datetime.fromtimestamp(target.stat().st_mtime).isoformat()
        return data
    except Exception:
        return {}


def build_alpha_discovery_overlay(*, board: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    source = board or load_alpha_discovery_board()
    items = list(source.get("items") or [])
    if not items:
        return {"version": ALPHA_DISCOVERY_VERSION, "mode": "premarket_overlay", "items": [], "error": "nightly board unavailable"}

    symbols = [str(item.get("ticker") or "").upper() for item in items if item.get("ticker")]
    quotes = _quote_now(symbols)
    overlay_items: List[Dict[str, Any]] = []
    for item in items:
        sym = str(item.get("ticker") or "").upper()
        quote = quotes.get(sym) or {}
        ref_price = _f(item.get("price"))
        current_price = _f(quote.get("price"))
        prev_close = _f(quote.get("prev_close")) or ref_price
        change_pct = _f(quote.get("change_pct"))
        effective_gap = ((current_price / prev_close) - 1.0) * 100.0 if current_price > 0 and prev_close > 0 else change_pct
        base_bucket = str(item.get("bucket") or "")
        if current_price <= 0:
            action_state = "no live overlay"
            action_reason = "current quote unavailable"
        elif effective_gap >= 5.0:
            action_state = "gapped too far"
            action_reason = "overnight move stretched the entry"
        elif effective_gap <= -4.0:
            action_state = "lost setup"
            action_reason = "gap down damaged the entry profile"
        elif str(item.get("track") or "") == "Liquid Leadership Reset" and effective_gap >= 2.5:
            action_state = "too crowded now"
            action_reason = "leader reset got too hot into the open"
        elif base_bucket == "Buyable Pullback" and _f(item.get("crowd_penalty")) >= 55 and effective_gap >= 1.8:
            action_state = "too crowded now"
            action_reason = "setup stayed strong but current entry is now too stretched"
        elif base_bucket == "Buyable Pullback" and bool(item.get("actionable_now")) and -1.5 <= effective_gap <= 1.8:
            action_state = "still buyable"
            action_reason = "overnight move kept the setup in range"
        elif base_bucket in {"Buyable Pullback", "Early Discovery", "Sponsor Confirmation"} and 1.5 < effective_gap < 4.5:
            action_state = "stronger than expected"
            action_reason = "participation improved, but entry needs review"
        elif base_bucket == "Too Late / Crowded":
            action_state = "too crowded now"
            action_reason = "already a bad chase; overnight action did not fix it"
        else:
            action_state = "watch reclaim"
            action_reason = "setup remains valid for stalking, not immediate entry"

        overlay_items.append({
            **item,
            "overlay_status": action_state,
            "overlay_reason": action_reason,
            "overlay_bucket": _bucket_for_overlay(action_state, base_bucket),
            "overlay_current_price": current_price or None,
            "overlay_gap_pct": round(effective_gap, 2) if current_price > 0 or change_pct else None,
            "overlay_quote_available": bool(current_price > 0),
        })

    overlay_items = annotate_alpha_items_with_lens(overlay_items)

    track_counts: Dict[str, int] = {}
    bucket_counts: Dict[str, int] = {}
    tier_counts: Dict[str, int] = {"A": 0, "B": 0, "C": 0}
    for item in overlay_items:
        track = str(item.get("track") or "Unknown")
        track_counts[track] = track_counts.get(track, 0) + 1
        bucket = str(item.get("overlay_bucket") or item.get("bucket") or "Unknown")
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
        tier = str(item.get("data_tier") or "C")
        if tier in tier_counts:
            tier_counts[tier] += 1

    return {
        "version": ALPHA_DISCOVERY_VERSION,
        "mode": "premarket_overlay",
        "built_at": datetime.utcnow().isoformat(),
        "source_board_mtime": source.get("_mtime_iso"),
        "tier_counts": tier_counts,
        "track_counts": track_counts,
        "bucket_counts": bucket_counts,
        "dominant_sectors": _dominant_sectors(overlay_items),
        "items": overlay_items,
    }


def save_alpha_discovery_overlay(overlay: Dict[str, Any]) -> Dict[str, str]:
    cache_dir = _ALPHA_RESEARCH_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    log_dir = cfg.LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    json_path = _ALPHA_OVERLAY_JSON_PATH
    txt_path = log_dir / "alpha_discovery_overlay_latest.txt"
    json_path.write_text(json.dumps(overlay, indent=2), encoding="utf-8")
    return {"json": str(json_path), "text": str(txt_path)}

#!/usr/bin/env python3
"""
research/market_heartbeat.py — Market Heartbeat Engine (Phase 4A).

Research-only daily market pulse: trend, breadth, sector leadership,
risk-on/risk-off, and a single market regime label.

This module does NOT:
  - recommend trades, entries, stops, or targets
  - emit paper signals or trade proposals
  - require Alpaca or broker credentials
  - touch governance, execution, or strategy sleeves

Data sources (in priority order):
  1. Cached parquets in cache/prices/  (always tried first)
  2. FMP historical bars               (if FMP key configured)
  3. yfinance debug fallback           (if available, RESEARCH_ALLOW_YFINANCE_DEBUG=true)

VIX: FMP get_vix() → cached DB key → parquet fallback → None (degrades gracefully).

Outputs:
  cache/research/market_heartbeat_latest.json
  logs/market_heartbeat_latest.txt
  docs/research/MARKET_HEARTBEAT_ENGINE.md  (written once; not overwritten on each run)

Heartbeat labels:
  RISK_ON            — broad strength, low vol, positive breadth
  HEALTHY_PULLBACK   — short-term weakness inside uptrend
  CHOP               — no clear direction, mixed signals
  CORRECTION         — meaningful decline (10–20%), elevated vol
  RISK_OFF           — defensive rotation, credit stress, high vol
  TECH_LED           — tech/semis outperforming; market carried by QQQ/SMH
  SMALL_CAP_LED      — IWM leading; risk-appetite broadening
  DEFENSIVE_ROTATION — staples/utilities/health-care leading; risk shedding

Usage:
  SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python research/market_heartbeat.py
  GEM_TRADER_SKIP_DOTENV=true .venv/bin/python research/market_heartbeat.py --offline
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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
from core.research_mode import SYSTEM_MODE, RESEARCH_ONLY_BANNER, ALPACA_ACTIVE
from core.regime_forecaster import (
    MARKET_ETFS,
    SECTOR_ETFS,
    SECTOR_NAMES,
    DEFENSIVE_SECTORS,
    RISK_PROXIES,
    market_trend_features,
    sector_rotation_features,
    volatility_features,
    breadth_features,
)

VERSION = "MARKET_HEARTBEAT_V1"
PRICE_DIR = cfg.CACHE_DIR / "prices"
RESEARCH_DIR = cfg.CACHE_DIR / "research"
HEARTBEAT_JSON = RESEARCH_DIR / "market_heartbeat_latest.json"
HEARTBEAT_TXT = cfg.LOG_DIR / "market_heartbeat_latest.txt"
DOC_PATH = ROOT / "docs" / "research" / "MARKET_HEARTBEAT_ENGINE.md"

# Symbols we track specifically for the heartbeat (superset of regime_forecaster)
HEARTBEAT_SYMBOLS: Tuple[str, ...] = (
    "SPY", "QQQ", "IWM", "SMH",  # core market ETFs
    "VXX",                        # vol proxy
    "XLK", "XLF", "XLV", "XLE", "XLY",
    "XLI", "XLP", "XLU", "XLB", "XLRE", "XLC",  # sectors
    "TLT", "HYG",                 # credit / risk
)

HEARTBEAT_LABELS = frozenset({
    "RISK_ON",
    "HEALTHY_PULLBACK",
    "CHOP",
    "CORRECTION",
    "RISK_OFF",
    "TECH_LED",
    "SMALL_CAP_LED",
    "DEFENSIVE_ROTATION",
})

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("market_heartbeat")


# ── Price loading (cache-first, no Alpaca required) ─────────────────────────


def _load_cached_frame(symbol: str) -> Optional[pd.DataFrame]:
    path = PRICE_DIR / f"{symbol.upper()}.parquet"
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path)
        if df is None or df.empty:
            return None
        if "close" not in df.columns and "Close" in df.columns:
            df = df.rename(columns={"Close": "close"})
        return df
    except Exception as exc:
        logger.warning("cached parquet read failed for %s: %s", symbol, exc)
        return None


def _is_offline_fmp() -> bool:
    return os.getenv("FMP_API_KEY", "").strip().lower() in {"", "offline", "stub"}


def _fetch_fmp_frame(symbol: str) -> Optional[pd.DataFrame]:
    if _is_offline_fmp():
        return None
    try:
        from core.fmp_client import get_fmp
        fmp = get_fmp()
        rows = fmp.get_ticker_bars(symbol, days=260)
        if not rows:
            return None
        df = pd.DataFrame(rows)
        if "date" not in df.columns or df.empty:
            return None
        df["date"] = pd.to_datetime(df["date"])
        df.set_index("date", inplace=True)
        df.sort_index(inplace=True)
        return df
    except Exception as exc:
        logger.info("FMP frame fetch failed for %s: %s", symbol, exc)
        return None


def _fetch_yf_frame(symbol: str) -> Optional[pd.DataFrame]:
    if os.getenv("RESEARCH_ALLOW_YFINANCE_DEBUG", "false").lower() not in {"1", "true", "yes"}:
        return None
    try:
        import yfinance as yf
        from datetime import timedelta, date
        end = date.today()
        start = end - timedelta(days=400)
        df = yf.download(symbol, start=start.isoformat(), end=end.isoformat(),
                         interval="1d", auto_adjust=False, progress=False, threads=False)
        if df is None or df.empty:
            return None
        df.columns = [c.lower() for c in df.columns]
        return df
    except Exception as exc:
        logger.info("yfinance frame fetch failed for %s: %s", symbol, exc)
        return None


def load_frames(symbols: Tuple[str, ...], offline: bool = False) -> Dict[str, Any]:
    """Return {symbol: DataFrame} — cache-first, FMP/yfinance fallback."""
    frames: Dict[str, Any] = {}
    missing: List[str] = []

    for sym in symbols:
        df = _load_cached_frame(sym)
        if df is not None and len(df) >= 10:
            frames[sym] = df
        else:
            missing.append(sym)

    if missing and not offline:
        for sym in list(missing):
            df = _fetch_fmp_frame(sym)
            if df is not None and len(df) >= 10:
                frames[sym] = df
                missing.remove(sym)

        for sym in list(missing):
            df = _fetch_yf_frame(sym)
            if df is not None and len(df) >= 10:
                frames[sym] = df
                missing.remove(sym)

    if missing:
        logger.info("symbols without price data: %s", missing)

    return frames


def _get_vix(offline: bool = False) -> Optional[float]:
    """FMP VIX → None on failure. Never blocks the run."""
    if offline or _is_offline_fmp():
        return None
    try:
        from core.fmp_client import get_fmp
        return get_fmp().get_vix()
    except Exception as exc:
        logger.info("VIX fetch failed: %s", exc)
        return None


# ── Close series helpers ─────────────────────────────────────────────────────


def _closes(df: Any) -> List[float]:
    if df is None or not hasattr(df, "iloc"):
        return []
    col = "close" if "close" in df.columns else ("Close" if "Close" in df.columns else None)
    if col is None:
        return []
    return [float(v) for v in df[col].dropna().tolist()]


def _last(series: List[float]) -> Optional[float]:
    return series[-1] if series else None


def _ma(series: List[float], window: int) -> Optional[float]:
    if len(series) < window:
        return None
    return sum(series[-window:]) / window


def _ret(series: List[float], lookback: int) -> Optional[float]:
    if len(series) < lookback + 1:
        return None
    return (series[-1] / series[-(lookback + 1)] - 1.0) * 100.0


def _pct_above_ma(frames: Dict[str, Any], symbols: Tuple[str, ...], window: int) -> Optional[float]:
    """Fraction of symbols whose last close is above their {window}-day MA."""
    above = 0
    total = 0
    for sym in symbols:
        s = _closes(frames.get(sym))
        ma = _ma(s, window)
        last = _last(s)
        if ma is not None and last is not None:
            total += 1
            if last > ma:
                above += 1
    return above / total if total > 0 else None


# ── Per-ETF trend label ──────────────────────────────────────────────────────


def _etf_trend(series: List[float]) -> str:
    if len(series) < 20:
        return "INSUFFICIENT_DATA"
    last = series[-1]
    ma20 = _ma(series, 20)
    ma50 = _ma(series, 50)
    r5 = _ret(series, 5)
    r20 = _ret(series, 20)
    if ma20 is None:
        return "INSUFFICIENT_DATA"
    above_20 = last > ma20
    above_50 = last > ma50 if ma50 else None
    if above_50 and above_20 and r20 is not None and r20 > 3:
        return "UPTREND"
    if above_50 and above_20:
        return "UPTREND_MILD"
    if above_50 and not above_20:
        return "PULLBACK"
    if not above_50 and above_20:
        return "RECOVERING"
    if r20 is not None and r20 < -15:
        return "DOWNTREND_SEVERE"
    if r20 is not None and r20 < -5:
        return "DOWNTREND"
    return "CHOP"


# ── Sector leadership ────────────────────────────────────────────────────────


def _sector_leadership(frames: Dict[str, Any]) -> Dict[str, Any]:
    """Rank sectors by 20d and 60d return vs SPY."""
    spy = _closes(frames.get("SPY"))
    spy_r20 = _ret(spy, 20) or 0.0
    spy_r60 = _ret(spy, 60) or 0.0

    rankings: List[Dict[str, Any]] = []
    for sym in SECTOR_ETFS:
        s = _closes(frames.get(sym))
        if not s:
            continue
        r20 = _ret(s, 20)
        r60 = _ret(s, 60)
        rel20 = (r20 - spy_r20) if r20 is not None else None
        rel60 = (r60 - spy_r60) if r60 is not None else None
        last = _last(s)
        ma50 = _ma(s, 50)
        above_ma50 = last > ma50 if (last and ma50) else None
        rankings.append({
            "symbol": sym,
            "name": SECTOR_NAMES.get(sym, sym),
            "r20_pct": round(r20, 2) if r20 is not None else None,
            "r60_pct": round(r60, 2) if r60 is not None else None,
            "rel_spy_20d": round(rel20, 2) if rel20 is not None else None,
            "rel_spy_60d": round(rel60, 2) if rel60 is not None else None,
            "above_ma50": above_ma50,
            "is_defensive": sym in DEFENSIVE_SECTORS,
        })

    rankings.sort(key=lambda x: x["rel_spy_20d"] or -99, reverse=True)
    leaders = [r["symbol"] for r in rankings[:3] if (r["rel_spy_20d"] or 0) > 0]
    laggards = [r["symbol"] for r in rankings[-3:] if (r["rel_spy_20d"] or 0) < 0]
    defensive_leading = any(
        r["symbol"] in DEFENSIVE_SECTORS and (r["rel_spy_20d"] or 0) > 1
        for r in rankings[:4]
    )

    return {
        "rankings": rankings,
        "top_leaders": leaders,
        "top_laggards": laggards,
        "defensive_rotation_signal": defensive_leading,
    }


# ── Risk-on / Risk-off ───────────────────────────────────────────────────────


def _risk_signal(frames: Dict[str, Any], vix: Optional[float]) -> Dict[str, Any]:
    hyg = _closes(frames.get("HYG"))
    tlt = _closes(frames.get("TLT"))
    iwm = _closes(frames.get("IWM"))
    spy = _closes(frames.get("SPY"))
    vxx = _closes(frames.get("VXX"))

    hyg_r10 = _ret(hyg, 10)
    tlt_r10 = _ret(tlt, 10)
    iwm_spy_rel = None
    if iwm and spy:
        r_iwm = _ret(iwm, 20)
        r_spy = _ret(spy, 20)
        if r_iwm is not None and r_spy is not None:
            iwm_spy_rel = r_iwm - r_spy

    vxx_trend = _etf_trend(_closes(frames.get("VXX"))) if "VXX" in frames else None

    risk_on_signals: List[str] = []
    risk_off_signals: List[str] = []

    if hyg_r10 is not None:
        if hyg_r10 > 0.5:
            risk_on_signals.append(f"HYG credit bid +{hyg_r10:.1f}%/10d")
        elif hyg_r10 < -1.0:
            risk_off_signals.append(f"HYG credit offered {hyg_r10:.1f}%/10d")

    if tlt_r10 is not None:
        if tlt_r10 > 2.0:
            risk_off_signals.append(f"TLT flight-to-safety +{tlt_r10:.1f}%/10d")
        elif tlt_r10 < -1.5:
            risk_on_signals.append(f"TLT selling (yields rising) {tlt_r10:.1f}%/10d")

    if iwm_spy_rel is not None:
        if iwm_spy_rel > 2:
            risk_on_signals.append(f"IWM outpacing SPY +{iwm_spy_rel:.1f}pp/20d (breadth broadening)")
        elif iwm_spy_rel < -3:
            risk_off_signals.append(f"IWM lagging SPY {iwm_spy_rel:.1f}pp/20d (narrow market)")

    if vix is not None:
        if vix < 15:
            risk_on_signals.append(f"VIX low ({vix:.1f})")
        elif vix > 25:
            risk_off_signals.append(f"VIX elevated ({vix:.1f})")
        elif vix > 20:
            risk_off_signals.append(f"VIX above normal ({vix:.1f})")

    if vxx_trend in {"UPTREND", "UPTREND_MILD"}:
        risk_off_signals.append(f"VXX in {vxx_trend}")

    score = len(risk_on_signals) - len(risk_off_signals)
    signal = "RISK_ON" if score >= 2 else ("RISK_OFF" if score <= -2 else "NEUTRAL")

    return {
        "signal": signal,
        "risk_on_signals": risk_on_signals,
        "risk_off_signals": risk_off_signals,
        "net_score": score,
        "vix": vix,
        "vxx_trend": vxx_trend,
        "hyg_r10_pct": round(hyg_r10, 2) if hyg_r10 is not None else None,
        "tlt_r10_pct": round(tlt_r10, 2) if tlt_r10 is not None else None,
        "iwm_spy_rel_20d": round(iwm_spy_rel, 2) if iwm_spy_rel is not None else None,
    }


# ── Correction warning ───────────────────────────────────────────────────────


def _correction_check(frames: Dict[str, Any], vix: Optional[float]) -> Dict[str, Any]:
    spy = _closes(frames.get("SPY"))
    qqq = _closes(frames.get("QQQ"))
    iwm = _closes(frames.get("IWM"))

    spy_r20 = _ret(spy, 20)
    spy_r60 = _ret(spy, 60)
    qqq_r20 = _ret(qqq, 20)
    iwm_r20 = _ret(iwm, 20)

    # Distance from 52-week high
    def _dd_from_high(s: List[float], window: int = 252) -> Optional[float]:
        if not s or len(s) < 5:
            return None
        recent = s[-window:] if len(s) >= window else s
        hi = max(recent)
        curr = s[-1]
        return (curr / hi - 1.0) * 100.0 if hi > 0 else None

    spy_dd = _dd_from_high(spy)
    breadth_20 = _pct_above_ma(frames, SECTOR_ETFS, 20)
    breadth_50 = _pct_above_ma(frames, SECTOR_ETFS, 50)

    warnings: List[str] = []
    if spy_r20 is not None and spy_r20 < -7:
        warnings.append(f"SPY -7%+ over 20d ({spy_r20:.1f}%)")
    if spy_r60 is not None and spy_r60 < -10:
        warnings.append(f"SPY -10%+ over 60d ({spy_r60:.1f}%)")
    if spy_dd is not None and spy_dd < -10:
        warnings.append(f"SPY off {spy_dd:.1f}% from 52wk high")
    if vix is not None and vix > 30:
        warnings.append(f"VIX stress zone ({vix:.1f})")
    if breadth_50 is not None and breadth_50 < 0.40:
        warnings.append(f"Sector breadth below 50d MA: {breadth_50*100:.0f}% of sectors")

    is_correction = len(warnings) >= 2 or (spy_dd is not None and spy_dd < -15)

    return {
        "is_correction": is_correction,
        "warnings": warnings,
        "spy_r20_pct": round(spy_r20, 2) if spy_r20 is not None else None,
        "spy_r60_pct": round(spy_r60, 2) if spy_r60 is not None else None,
        "spy_dd_from_high_pct": round(spy_dd, 2) if spy_dd is not None else None,
        "qqq_r20_pct": round(qqq_r20, 2) if qqq_r20 is not None else None,
        "iwm_r20_pct": round(iwm_r20, 2) if iwm_r20 is not None else None,
        "sector_breadth_above_ma20": round(breadth_20, 2) if breadth_20 is not None else None,
        "sector_breadth_above_ma50": round(breadth_50, 2) if breadth_50 is not None else None,
    }


# ── Heartbeat label ──────────────────────────────────────────────────────────


def _classify_heartbeat(
    spy_trend: str,
    qqq_trend: str,
    iwm_trend: str,
    smh_trend: str,
    sector: Dict[str, Any],
    risk: Dict[str, Any],
    correction: Dict[str, Any],
    breadth_above_50: Optional[float],
) -> Tuple[str, List[str]]:
    reasons: List[str] = []

    # RISK_OFF / CORRECTION first — hard negatives override positive signals
    if correction["is_correction"]:
        reasons.extend(correction["warnings"])
        spy_r20 = correction.get("spy_r20_pct") or 0
        if spy_r20 < -15 or (correction.get("spy_dd_from_high_pct") or 0) < -20:
            reasons.append("Severe drawdown detected")
            return "RISK_OFF", reasons
        return "CORRECTION", reasons

    if risk["signal"] == "RISK_OFF" and spy_trend in {"DOWNTREND", "DOWNTREND_SEVERE", "CHOP"}:
        reasons.extend(risk["risk_off_signals"])
        return "RISK_OFF", reasons

    # DEFENSIVE_ROTATION — sectors moving but risk proxies defensive
    if sector.get("defensive_rotation_signal") and risk["signal"] != "RISK_ON":
        reasons.append("Defensive sectors leading (staples/utilities/health)")
        reasons.extend(sector.get("top_leaders", []))
        return "DEFENSIVE_ROTATION", reasons

    # TECH_LED — QQQ/SMH outperforming; IWM lagging
    if qqq_trend in {"UPTREND", "UPTREND_MILD"} and smh_trend in {"UPTREND", "UPTREND_MILD"}:
        iwm_spy_rel = risk.get("iwm_spy_rel_20d") or 0
        if iwm_spy_rel < 0 and "XLK" in sector.get("top_leaders", []):
            reasons.append(f"QQQ {qqq_trend}, SMH {smh_trend}")
            reasons.append("Tech/semis leading; small-caps lagging")
            return "TECH_LED", reasons

    # SMALL_CAP_LED — IWM outperforming
    iwm_spy_rel = risk.get("iwm_spy_rel_20d") or 0
    if iwm_spy_rel > 2 and iwm_trend in {"UPTREND", "UPTREND_MILD"}:
        reasons.append(f"IWM outpacing SPY by +{iwm_spy_rel:.1f}pp/20d")
        return "SMALL_CAP_LED", reasons

    # RISK_ON — broad strength
    if (spy_trend in {"UPTREND", "UPTREND_MILD"} and
            risk["signal"] == "RISK_ON" and
            (breadth_above_50 or 0) > 0.55):
        reasons.append(f"SPY {spy_trend}")
        reasons.extend(risk["risk_on_signals"])
        reasons.append(f"Sector breadth: {(breadth_above_50 or 0)*100:.0f}% above 50d MA")
        return "RISK_ON", reasons

    # HEALTHY_PULLBACK — uptrend intact, short-term weakness
    if spy_trend in {"UPTREND", "UPTREND_MILD", "PULLBACK"}:
        spy_r20 = correction.get("spy_r20_pct") or 0
        if -8 < spy_r20 < 0:
            reasons.append(f"SPY {spy_trend} — short-term pullback ({spy_r20:.1f}%/20d)")
            return "HEALTHY_PULLBACK", reasons
        if spy_trend in {"UPTREND", "UPTREND_MILD"}:
            reasons.append(f"SPY {spy_trend}")
            return "RISK_ON", reasons

    # CHOP — default
    reasons.append("Mixed signals — no clear regime")
    return "CHOP", reasons


# ── Main heartbeat builder ───────────────────────────────────────────────────


def build_heartbeat(offline: bool = False) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    logger.info("Market Heartbeat Engine %s starting (offline=%s)", VERSION, offline)

    frames = load_frames(HEARTBEAT_SYMBOLS, offline=offline)
    vix = _get_vix(offline=offline)

    # Per-ETF trends
    etf_trends = {}
    for sym in ("SPY", "QQQ", "IWM", "SMH", "VXX"):
        s = _closes(frames.get(sym))
        etf_trends[sym] = {
            "trend": _etf_trend(s),
            "last": round(_last(s), 2) if _last(s) else None,
            "r5_pct": round(_ret(s, 5), 2) if _ret(s, 5) is not None else None,
            "r20_pct": round(_ret(s, 20), 2) if _ret(s, 20) is not None else None,
            "r60_pct": round(_ret(s, 60), 2) if _ret(s, 60) is not None else None,
            "above_ma20": (_last(s) > _ma(s, 20)) if (_last(s) and _ma(s, 20)) else None,
            "above_ma50": (_last(s) > _ma(s, 50)) if (_last(s) and _ma(s, 50)) else None,
            "above_ma200": (_last(s) > _ma(s, 200)) if (_last(s) and _ma(s, 200)) else None,
        }

    breadth_20 = _pct_above_ma(frames, SECTOR_ETFS, 20)
    breadth_50 = _pct_above_ma(frames, SECTOR_ETFS, 50)
    breadth = {
        "sector_pct_above_ma20": round(breadth_20, 2) if breadth_20 is not None else None,
        "sector_pct_above_ma50": round(breadth_50, 2) if breadth_50 is not None else None,
        "proxy_source": "sector_etfs",
        "note": "% of 11 sector ETFs above 20d/50d MA — higher = broader market participation",
    }

    sector = _sector_leadership(frames)
    risk = _risk_signal(frames, vix)
    correction = _correction_check(frames, vix)

    label, reasons = _classify_heartbeat(
        spy_trend=etf_trends["SPY"]["trend"],
        qqq_trend=etf_trends["QQQ"]["trend"],
        iwm_trend=etf_trends["IWM"]["trend"],
        smh_trend=etf_trends["SMH"]["trend"] if "SMH" in etf_trends else "INSUFFICIENT_DATA",
        sector=sector,
        risk=risk,
        correction=correction,
        breadth_above_50=breadth_50,
    )

    symbols_loaded = sorted(frames.keys())
    symbols_missing = [s for s in HEARTBEAT_SYMBOLS if s not in frames]

    out: Dict[str, Any] = {
        "version": VERSION,
        "generated_at": now,
        "system_mode": SYSTEM_MODE,
        "research_only": True,
        "heartbeat_label": label,
        "heartbeat_reasons": reasons,
        "vix": vix,
        "etf_trends": etf_trends,
        "breadth": breadth,
        "sector_leadership": sector,
        "risk_signal": risk,
        "correction_check": correction,
        "data_coverage": {
            "symbols_loaded": symbols_loaded,
            "symbols_missing": symbols_missing,
            "offline_mode": offline,
        },
        "guardrails": {
            "no_trade_recommendation": True,
            "no_buy_sell": True,
            "no_entry_stop_target": True,
            "no_paper_signal": True,
            "alpaca_required": False,
        },
    }

    return out


# ── Output writers ───────────────────────────────────────────────────────────


def _format_text(h: Dict[str, Any]) -> str:
    lines = [
        RESEARCH_ONLY_BANNER,
        "",
        f"MARKET HEARTBEAT  [{h['heartbeat_label']}]",
        f"Generated: {h['generated_at']}",
        f"VIX: {h['vix'] or 'N/A'}",
        "",
        "Why:",
    ]
    for r in h.get("heartbeat_reasons", []):
        lines.append(f"  • {r}")

    lines += ["", "ETF Trends:"]
    for sym, t in h.get("etf_trends", {}).items():
        lines.append(
            f"  {sym:6s}  {t['trend']:20s}  last={t['last'] or 'N/A':>8}  "
            f"r5={t['r5_pct'] or 'N/A':>6}%  r20={t['r20_pct'] or 'N/A':>6}%"
        )

    b = h.get("breadth", {})
    lines += [
        "",
        "Breadth (sector ETFs):",
        f"  Above 20d MA: {(b.get('sector_pct_above_ma20') or 0)*100:.0f}%",
        f"  Above 50d MA: {(b.get('sector_pct_above_ma50') or 0)*100:.0f}%",
        "",
        "Sector Leaders / Laggards:",
    ]
    sl = h.get("sector_leadership", {})
    for r in sl.get("rankings", [])[:5]:
        rel = r.get("rel_spy_20d")
        lines.append(
            f"  {r['symbol']:6s}  {r['name']:30s}  rel_spy_20d={rel or 'N/A':>+6}"
            + ("  [DEFENSIVE]" if r.get("is_defensive") else "")
        )

    rs = h.get("risk_signal", {})
    lines += [
        "",
        f"Risk Signal: {rs.get('signal', 'N/A')}  (net={rs.get('net_score', 0):+d})",
    ]
    for s in rs.get("risk_on_signals", []):
        lines.append(f"  + {s}")
    for s in rs.get("risk_off_signals", []):
        lines.append(f"  - {s}")

    corr = h.get("correction_check", {})
    if corr.get("warnings"):
        lines += ["", "Correction Warnings:"]
        for w in corr["warnings"]:
            lines.append(f"  ⚠ {w}")

    lines += [
        "",
        "--- RESEARCH ONLY — NO TRADE RECOMMENDATIONS ---",
    ]
    return "\n".join(lines)


def write_outputs(h: Dict[str, Any]) -> None:
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    cfg.LOG_DIR.mkdir(parents=True, exist_ok=True)

    HEARTBEAT_JSON.write_text(json.dumps(h, indent=2), encoding="utf-8")
    logger.info("wrote %s", HEARTBEAT_JSON)

    HEARTBEAT_TXT.write_text(_format_text(h), encoding="utf-8")
    logger.info("wrote %s", HEARTBEAT_TXT)


def write_doc() -> None:
    if DOC_PATH.exists():
        return
    DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    content = """\
# MARKET HEARTBEAT ENGINE

**Module:** `research/market_heartbeat.py`
**Phase:** 4A
**Mode:** RESEARCH_ONLY — no trade recommendations.

## Purpose

The Market Heartbeat provides a daily read on the broad market environment.
It is not a trade signal. It does not recommend entries, exits, stops, or
position sizes. Its purpose is to give the human operator a clear, concise
snapshot of what the market is doing so manual research is conducted in the
right context.

## Data Sources

1. `cache/prices/*.parquet` — always tried first (no provider call)
2. FMP historical bars — fallback if parquet missing or stale
3. yfinance — debug fallback only (`RESEARCH_ALLOW_YFINANCE_DEBUG=true`)

VIX: FMP `get_vix()` → None on failure. Degrades gracefully.

**Alpaca is not required.**

## Heartbeat Labels

| Label | Meaning |
|---|---|
| `RISK_ON` | Broad strength, positive breadth, low vol |
| `HEALTHY_PULLBACK` | Short-term weakness inside intact uptrend |
| `CHOP` | No clear direction, mixed signals |
| `CORRECTION` | Meaningful decline (≥7%), elevated vol |
| `RISK_OFF` | Defensive rotation, credit stress, high vol |
| `TECH_LED` | Tech/semis outperforming; narrow leadership |
| `SMALL_CAP_LED` | IWM leading; risk appetite broadening |
| `DEFENSIVE_ROTATION` | Staples/utilities/health-care leading |

## Symbols Tracked

- Market: SPY, QQQ, IWM, SMH
- Vol proxy: VXX
- Sectors: XLK XLF XLV XLE XLY XLI XLP XLU XLB XLRE XLC
- Credit/risk: TLT, HYG

## Outputs

- `cache/research/market_heartbeat_latest.json`
- `logs/market_heartbeat_latest.txt`

## Usage

```bash
# With FMP credentials
SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python research/market_heartbeat.py

# Cache-only (no provider calls)
GEM_TRADER_SKIP_DOTENV=true .venv/bin/python research/market_heartbeat.py --offline

# Via research cycle
./scripts/run_research_cycle.sh market-heartbeat
```

## Guardrails

- No buy/sell/entry/stop/target output
- No paper signals, no governance, no execution
- No Alpaca required
- Tradier not used
- Degrades gracefully when data is missing
"""
    DOC_PATH.write_text(content, encoding="utf-8")
    logger.info("wrote %s", DOC_PATH)


# ── CLI ──────────────────────────────────────────────────────────────────────


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Market Heartbeat Engine (research-only)")
    parser.add_argument("--offline", action="store_true",
                        help="Skip all provider calls; use only cached parquets")
    parser.add_argument("--print", dest="print_text", action="store_true",
                        help="Also print the text summary to stdout")
    args = parser.parse_args(argv)

    print(RESEARCH_ONLY_BANNER)

    heartbeat = build_heartbeat(offline=args.offline)
    write_outputs(heartbeat)
    write_doc()

    label = heartbeat["heartbeat_label"]
    reasons = heartbeat.get("heartbeat_reasons", [])
    vix = heartbeat.get("vix")

    print(f"\nMarket Heartbeat: [{label}]")
    print(f"VIX: {vix or 'N/A'}")
    for r in reasons:
        print(f"  • {r}")

    loaded = heartbeat["data_coverage"]["symbols_loaded"]
    missing = heartbeat["data_coverage"]["symbols_missing"]
    print(f"\nLoaded {len(loaded)} symbols; missing: {missing or 'none'}")
    print(f"Artifacts: {HEARTBEAT_JSON}")

    if args.print_text:
        print("\n" + _format_text(heartbeat))


if __name__ == "__main__":
    main()

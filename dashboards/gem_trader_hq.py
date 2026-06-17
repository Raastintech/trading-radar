#!/usr/bin/env python3
"""
dashboards/gem_trader_hq.py — GEM Trader HQ v2

Retro terminal trading desk.  Alpaca + FMP + Claude Haiku.

Modes (press key):
  1 = Monitor   2 = Research   3 = Risk   4 = Scanner
  / = Search    r = Refresh    q = Quit

Usage:
  SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python dashboards/gem_trader_hq.py
"""
from __future__ import annotations

import json, os, re, select, shutil, sqlite3, sys, termios, threading, time, tty
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

# ── env load before core imports ──────────────────────────────────────────────
_CRED = os.environ.get("SNIPER_ENV_PATH")
_SKIP_CRED_LOAD = os.environ.get("GEM_TRADER_SKIP_DOTENV", "").lower() in ("1", "true", "yes")
if _CRED and not _SKIP_CRED_LOAD and Path(_CRED).exists():
    try:
        from dotenv import load_dotenv; load_dotenv(_CRED, override=True)
    except ImportError:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core.config as cfg
from core.alpaca_client import get_alpaca
from core.alpha_discovery import load_alpha_discovery_board, load_alpha_discovery_overlay
from core.artifact_freshness import (
    FRESHNESS_THRESHOLDS as _FRESHNESS_THRESHOLDS,
    compute_freshness as _compute_freshness,
    earnings_status as _earnings_status,
    is_earnings_day as _is_earnings_day,
    mcp_extra_reasons as _mcp_extra_reasons,
)
from core.evidence_freshness import (
    artifact_meta as _ef_artifact_meta,
    fmt_age_short as _ef_fmt_age,
    price_cache_bar_status as _ef_price_status,
    universe_artifact_meta as _ef_universe_meta,
)
from core.fmp_client import get_fmp
from core.research_assist_bte import build_research_bte
from core.session import SessionState, get_session_state, next_session_change as _next_session_change
from core.strategy_registry import (
    active_paper_strategies,
    active_scanner_keys,
    frozen_strategies,
    is_active_paper_strategy,
    is_frozen_strategy,
    normalize_strategy,
    registry_rows,
)

try:
    from rich.columns import Columns
    from rich.console import Console, Group
    from rich.layout import Layout
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich import box
except ImportError:
    print("pip install rich"); sys.exit(1)

try:
    import anthropic as _ant
    _CLAUDE_OK = bool(os.environ.get("ANTHROPIC_API_KEY"))
except Exception:
    _ant = None; _CLAUDE_OK = False

def _get_claude_client():
    """Return a fresh Anthropic client using the current key from the environment.

    Reading the key on each call (rather than caching it at import time) means
    a key rotation in trading.env takes effect on the next analysis request
    without requiring a dashboard restart.
    """
    if _ant is None:
        return None
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return None
    return _ant.Anthropic(api_key=key)

try:
    from zoneinfo import ZoneInfo; _ET = ZoneInfo("America/New_York")
except Exception:
    _ET = None

# ── modes ─────────────────────────────────────────────────────────────────────
M_MONITOR = 1; M_RESEARCH = 2; M_RISK = 3; M_SCANNER = 4
MODE_NAMES = {M_MONITOR: "MARKET", M_RESEARCH: "WATCHLIST", M_RISK: "INTEL", M_SCANNER: "RESEARCH"}

# ── fallback ticker universe for standalone scans (daemon not running) ────────
# Emergency fallback only — kept for cases when the dynamic universe builder
# is unreachable. Phase 10 hygiene rule: no delisted/acquired/re-tickered names.
_SCANNER_FALLBACK: List[str] = [
    "AAPL","MSFT","NVDA","GOOGL","AMZN","META","TSLA","AMD","CRM","INTC",
    "JPM","BAC","GS","MS","V","MA","COIN",
    "XOM","CVX","OXY","HAL","COP",
    "JNJ","PFE","ABBV","UNH","BMY",
    "BA","CAT","DE","HON","RTX",
    "TGT","WMT","COST","HD","MCD",
    "DIS","NFLX","SPOT","ROKU","TTD",
    "SHOP","MELI","PLTR","SOFI","UPST",
    "SPY","QQQ","IWM","GLD","TLT","HYG",
]

_INSTRUMENT_SYMBOLS = {
    "SPY","QQQ","IWM","VXX","GLD","TLT","HYG","SQQQ","QID","TQQQ","QLD",
    "SOXS","SOXL","SPXU","UPRO","PSQ","SDS","SH","DOG","DXD","TZA","TNA",
}

# ── catalyst keywords (for news filtering) ────────────────────────────────────
_CATALYST_KW = {
    "fed","fomc","cpi","inflation","gdp","payroll","jobs","rate","recession",
    "tariff","yield","downgrade","upgrade","miss","beat","warning","guidance",
    "bankruptcy","merger","acquisition","crash","plunge","surge","rally",
    "selloff","circuit breaker","halt","reserve","treasury","powell",
}

# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

_SPARK = "▁▂▃▄▅▆▇█"


def _active_strategy_row(row: Dict[str, Any]) -> bool:
    return is_active_paper_strategy(normalize_strategy(row.get("strategy")))


def _side_key(value: Any) -> str:
    side = str(value or "").strip().upper()
    if side.startswith("L"):
        return "LONG"
    if side.startswith("S"):
        return "SHORT"
    return side


def _instrument_type_label(symbol: Any) -> str:
    return "ETF" if _is_instrument_symbol(symbol) else "EQ"


def _active_sleeve_display(code: Any) -> str:
    raw = normalize_strategy(code)
    if raw == "SNIPER":
        return "SNIPER_V6"
    if raw == "VOYAGER":
        return "VOYAGER"
    if raw == "SHORT":
        return "SHORT_A"
    return str(code or "—").strip().upper() or "—"


def _position_owner_label(position: Dict[str, Any]) -> str:
    strat = normalize_strategy(position.get("strategy"))
    src = str(position.get("sl_source") or "").upper()
    if is_active_paper_strategy(strat):
        return _active_sleeve_display(strat)
    if src == "MANUAL":
        return "MANUAL"
    if strat and strat not in {"—", "", "NONE"}:
        return "LEGACY"
    return "UNKNOWN"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso_utc(value: Any) -> datetime:
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _age_from_iso_short(value: Any) -> str:
    if not value or str(value) == "—":
        return "N/A"
    try:
        return _fmt_dur(_utc_now() - _parse_iso_utc(value))
    except Exception:
        return "N/A"


def _strategy_fit_label(code: str) -> str:
    raw = str(code or "").strip().upper()
    if raw == "SNP":
        return "Sniper v6"
    if raw == "VOY":
        return "Voyager"
    if raw == "SHA":
        return "Short A"
    if raw == "SNIPER":
        return "Sniper v6"
    if raw == "VOYAGER":
        return "Voyager"
    if raw == "SHORT":
        return "Short A"
    if raw == "MANUAL":
        return "Manual research only"
    if raw == "NONE" or raw == "—":
        return "No active sleeve fit"
    if raw in {"REM", "REMORA"}:
        return "Remora resemblance (research-only)"
    if raw in {"CON", "CONTRARIAN"}:
        return "Contrarian resemblance (research-only)"
    if raw in {"PATH", "PATHFINDER"}:
        return "Pathfinder resemblance (research-only)"
    if raw in {"SHORT_B", "SHB"}:
        return "Short B resemblance (research-only)"
    return raw.title()


def _quality_tier(value: Any) -> str:
    try:
        q = int(value)
    except (TypeError, ValueError):
        return "—"
    if q >= 75:
        return "High"
    if q >= 55:
        return "Medium"
    return "Low"


def _action_status_label(action: str, why: str, strat_label: str) -> str:
    """Phase 1F Task 3: research panels avoid "BUY candidate" framing.
    The action value passed in is still ENTER / WATCH / AVOID and any
    downstream sleeve / governance check that reads ``action`` is
    unaffected — this only renames the *display* label."""
    action_u = str(action or "").upper()
    why_l = str(why or "").lower()
    if "no active sleeve fit" in strat_label.lower():
        return "No active sleeve fit"
    if action_u == "ENTER":
        return "Research-aligned candidate"
    if "extended" in why_l or "overheated" in why_l:
        return "Late / Extended"
    if "pullback" in why_l:
        return "Watch Pullback"
    if action_u == "WATCH":
        return "WATCH"
    if action_u == "AVOID":
        return "Avoid"
    return "WATCH"

def sparkline(vals: List[float], w: int = 24) -> str:
    if not vals: return "─" * w
    d = vals[-w:]; mn, mx = min(d), max(d); rng = mx - mn or 1.0
    return "".join(_SPARK[int((v - mn) / rng * 7)] for v in d)

def calc_rsi(closes: List[float], p: int = 14) -> float:
    if len(closes) < p + 1: return 50.0
    dlts = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    g = sum(max(d, 0) for d in dlts[-p:]) / p
    l = sum(max(-d,0) for d in dlts[-p:]) / p
    return round(100 - 100/(1 + g/l), 1) if l else 100.0

def calc_ema(vals: List[float], p: int) -> float:
    if not vals: return 0.0
    if len(vals) < p: return sum(vals)/len(vals)
    k = 2/(p+1); e = sum(vals[:p])/p
    for v in vals[p:]: e = v*k + e*(1-k)
    return round(e, 4)

def calc_macd(closes: List[float]) -> Tuple[float, str]:
    if len(closes) < 27: return 0.0, "flat"
    m  = calc_ema(closes, 12) - calc_ema(closes, 26)
    mp = calc_ema(closes[:-1], 12) - calc_ema(closes[:-1], 26) if len(closes)>27 else m
    return round(m, 3), ("bull" if m > mp else "bear")

def calc_atr(bars: List[Dict], p: int = 14) -> float:
    # True Range references the PREVIOUS bar's close within the same window.
    # (Earlier bug indexed bars[i] — the head of the full history — making TR =
    # recent high minus an ancient close, e.g. INTC ATR ~$77 instead of ~$9.)
    w = bars[-(p + 1):] if len(bars) >= p + 1 else bars
    trs = [max(float(w[i]["high"]) - float(w[i]["low"]),
               abs(float(w[i]["high"]) - float(w[i - 1]["close"])),
               abs(float(w[i]["low"]) - float(w[i - 1]["close"])))
           for i in range(1, len(w))]
    return round(sum(trs) / len(trs), 3) if trs else 0.0

def _now_et() -> datetime:
    return datetime.now(_ET) if _ET else datetime.now()

def _market_status() -> str:
    """
    Returns a display label for the current market session.
    Delegates to core.session.get_session_state() for NYSE-aware logic.
    """
    state = get_session_state()
    return {
        SessionState.PREMARKET:  "PRE-MARKET",
        SessionState.REGULAR:    "OPEN",
        SessionState.POSTMARKET: "AFTER-HOURS",
        SessionState.CLOSED:     "CLOSED",
    }.get(state, "CLOSED")

def _vix_label(v: float) -> Tuple[str, str]:
    if v < 15: return "CALM",     "bold green"
    if v < 20: return "LOW",      "green"
    if v < 25: return "MODERATE", "yellow"
    if v < 30: return "ELEVATED", "bold yellow"
    if v < 40: return "HIGH",     "bold red"
    return "EXTREME", "bold red"

def _vix_meter(v: float, w: int = 16) -> str:
    f = min(int(v/50*w), w); return "█"*f + "░"*(w-f)

def _pnl_style(v: float) -> str:
    return "bold green" if v > 0 else ("bold red" if v < 0 else "white")

def _clip(s: str, n: int) -> str:
    return s[:n-1]+"…" if len(s) > n else s


def _is_instrument_symbol(symbol: str) -> bool:
    return str(symbol or "").upper() in _INSTRUMENT_SYMBOLS

def _fmt_dur(td: timedelta) -> str:
    s = int(td.total_seconds())
    if s < 0: return "—"
    if s < 3600: return f"{s//60}m"
    if s < 86400: return f"{s//3600}h{(s%3600)//60:02d}m"
    return f"{s//86400}d{(s%86400)//3600:02d}h"

# Non-VIX conflict-penalty cap, applied to the sum of forecast + fragility
# + intraday pulse penalties when fragility is not STRESS.  Prevents related
# symptoms (invalidation + red tape + defensive rotation) from triple-counting
# the same underlying risk.
_READINESS_CONFLICT_CAP = 35


def _readiness_spy_intraday_pct(spy_bars) -> Optional[float]:
    """Return today's intraday % change vs prior close from spy_bars, or None.

    spy_bars are daily Alpaca bars that update intraday; the last bar is
    today's running bar during REGULAR session.
    """
    try:
        if not spy_bars or len(spy_bars) < 2:
            return None
        c = float(spy_bars[-1].get("close") or 0.0)
        p = float(spy_bars[-2].get("close") or 0.0)
        if p <= 0:
            return None
        return (c - p) / p * 100.0
    except Exception:
        return None


def _readiness_quote_pct(etf_quotes, sym) -> Optional[float]:
    if not etf_quotes:
        return None
    q = etf_quotes.get(sym)
    if not q:
        return None
    try:
        return float(q.get("change_pct"))
    except Exception:
        return None


def compute_trade_readiness(
    vix, regime, econ_cal, mkt_status,
    scan_results=None, universe_snap=None,
    *,
    forecast=None, etf_quotes=None, spy_bars=None,
):
    """Return (status, style, bullets, chip, reasons).

    status   — RISK-ON / SELECTIVE / STANDBY / RISK-OFF
    style    — Rich style string
    bullets  — up to 3 short bullets for the panel
    chip     — short descriptive intraday chip (e.g. "SPY -0.8% · QQQ -1.1% ·
               defensive rotation") or None.  Only emitted in REGULAR session.
    reasons  — dict of score deductions, exposed for tests / debugging.
    """
    reasons: Dict[str, Any] = {
        "vix": 0,
        "regime": 0,
        "macro": 0,
        "forecast_invalidation": 0,
        "forecast_risk_off": 0,
        "fragility": 0,
        "fragility_status": None,
        "spy_red": 0,
        "defensive_flip": 0,
        "vxx_stress": 0,
        "conflict_raw": 0,
        "conflict_applied": 0,
        "conflict_cap_applied": False,
        "vix_floor_applied": False,
        "chip_parts": [],
        "regime_label": None,
    }

    # ── Closed / pre-market / post-market: no intraday penalties, no chip ──
    if mkt_status in ("CLOSED", "WEEKEND", "AFTER-HOURS", "PRE-MARKET"):
        bullets: List[str] = []
        if vix is not None:
            lbl, _ = _vix_label(vix)
            bullets.append(f"VIX {vix:.1f} — {lbl} heading into next session")
        try:
            ns, nt = _next_session_change()
            import time as _time
            mins = max(0, int((nt.timestamp() - _time.time()) / 60))
            if mins < 60:
                next_s = f"{ns.value} in {mins}m (at {nt.strftime('%H:%M')} ET)"
            elif mins < 1440:
                next_s = f"{ns.value} at {nt.strftime('%H:%M')} ET ({mins//60}h{mins%60:02d}m)"
            else:
                next_s = f"{ns.value} {nt.strftime('%a %H:%M')} ET"
        except Exception:
            next_s = "09:30 ET"
        bullets.append(f"Next: {next_s}")
        now = _now_et()
        for e in sorted(econ_cal or [], key=lambda x: x.get("date","")):
            if str(e.get("impact","")).lower() != "high":
                continue
            try:
                raw = str(e.get("date","")).replace("Z","")
                edt = datetime.fromisoformat(raw)
                if edt.tzinfo is None:
                    edt = edt.replace(tzinfo=timezone.utc)
                hrs = (edt - now).total_seconds() / 3600
                if hrs > 0:
                    bullets.append(
                        f"Next macro: {_clip(str(e.get('event','')),24)} in {int(hrs)}h"
                    )
                    break
            except Exception:
                continue
        # Headline regime name from forecast, if present
        head_closed = (forecast or {}).get("headline") or {}
        if head_closed.get("current_regime"):
            reasons["regime_label"] = head_closed["current_regime"]
        if universe_snap:
            candidates = universe_snap.get("strategy_candidates") or []
            n_ready = sum(1 for c in candidates if c.get("readiness") == "READY_NOW")
            n_dev   = sum(1 for c in candidates if c.get("readiness") == "DEVELOPING")
            n_long  = sum(1 for c in candidates if c.get("readiness") == "READY_NOW" and c.get("direction") == "LONG")
            n_short = sum(1 for c in candidates if c.get("readiness") == "READY_NOW" and c.get("direction") == "SHORT")
            fallback = (universe_snap.get("summary") or {}).get("fallback_used", False)
            if fallback:
                bullets.append("Universe: FALLBACK MODE — structural quality unknown")
            elif n_ready > 0:
                bias = f"L:{n_long} S:{n_short}"
                bullets.append(f"Structural pool: {n_ready} qualified ({bias})  +{n_dev} developing")
            else:
                bullets.append(f"Structural pool: 0 qualified  +{n_dev} developing")
        elif scan_results:
            n  = len(scan_results.get("opportunities") or [])
            lt = scan_results.get("last_cycle_ts")
            if lt:
                try:
                    odt = _parse_iso_utc(lt)
                    age_m = int((_utc_now() - odt).total_seconds() / 60)
                    bullets.append(f"Last scan: {n} setup{'s' if n!=1 else ''} found ({age_m}m ago)")
                except Exception:
                    bullets.append(f"Last scan: {n} setup{'s' if n!=1 else ''} found")
        return "STANDBY", "dim", bullets[:3], None, reasons

    # ── REGULAR session — full scoring ─────────────────────────────────────
    score = 100
    bullets: List[str] = []

    # VIX (independent of conflict cap)
    if vix is None:
        score -= 15; reasons["vix"] = -15
        bullets.append("VIX unavailable")
    elif vix >= 35:
        score -= 55; reasons["vix"] = -55
        bullets.append(f"VIX {vix:.1f} — extreme fear, stand down")
    elif vix >= 28:
        score -= 35; reasons["vix"] = -35
        bullets.append(f"VIX {vix:.1f} — high vol, size down")
    elif vix >= 22:
        score -= 20; reasons["vix"] = -20
        bullets.append(f"VIX {vix:.1f} — elevated, be selective")
    elif vix >= 18:
        score -= 5; reasons["vix"] = -5
        bullets.append(f"VIX {vix:.1f} — moderate, normal caution")
    else:
        bullets.append(f"VIX {vix:.1f} — calm, supportive")

    # Regime label — prefer forecast headline name verbatim.
    head = (forecast or {}).get("headline") or {}
    forecast_regime_name = head.get("current_regime")
    if forecast_regime_name:
        eff_regime = str(forecast_regime_name).upper()
        bullets.append(f"Regime: {forecast_regime_name}")
        reasons["regime_label"] = forecast_regime_name
    elif regime:
        eff_regime = str(regime.get("regime","")).upper()
        if "BEAR" in eff_regime:
            bullets.append("Regime BEAR — longs restricted")
        elif "BULL" in eff_regime:
            bullets.append("Regime BULL — trend supportive")
        else:
            bullets.append("Regime NEUTRAL — mixed")
        reasons["regime_label"] = eff_regime
    else:
        eff_regime = ""

    if "BEAR" in eff_regime:
        score -= 20; reasons["regime"] = -20
    elif "BULL" in eff_regime:
        pass
    elif eff_regime:
        score -= 5; reasons["regime"] = -5
    elif mkt_status == "OPEN":
        score -= 5; reasons["regime"] = -5

    # Upcoming HIGH-impact macro (existing behavior)
    now = _now_et()
    for e in sorted(econ_cal or [], key=lambda x: x.get("date","")):
        if str(e.get("impact","")).lower() != "high":
            continue
        try:
            raw = str(e.get("date","")).replace("Z","")
            edt = datetime.fromisoformat(raw)
            if edt.tzinfo is None:
                edt = edt.replace(tzinfo=timezone.utc)
            mins = (edt - now).total_seconds() / 60
            if -15 <= mins <= 0:
                score -= 35; reasons["macro"] = -35
                bullets.append(f"⚠ {_clip(str(e.get('event','')),28)} — LIVE NOW"); break
            elif 0 < mins <= 45:
                score -= 35; reasons["macro"] = -35
                bullets.append(f"⚠ {_clip(str(e.get('event','')),28)} in {int(mins)}m"); break
            elif 45 < mins <= 120:
                score -= 12; reasons["macro"] = -12
                bullets.append(f"{_clip(str(e.get('event','')),28)} in {int(mins/60)}h{int(mins%60):02d}m"); break
        except Exception:
            continue

    # ── Forecast + fragility + intraday pulse (subject to conflict cap) ────
    conflict_raw = 0
    chip_parts: List[str] = []

    if bool(head.get("invalidation_breached")):
        conflict_raw += 20; reasons["forecast_invalidation"] = -20

    probs = (forecast or {}).get("regime_probabilities") or []
    risk_off_p = 0.0
    for r in probs:
        if str(r.get("regime") or "").strip().lower() == "risk-off":
            try:
                risk_off_p = float(r.get("probability") or 0.0)
            except Exception:
                risk_off_p = 0.0
            break
    if risk_off_p >= 0.25:
        conflict_raw += 10; reasons["forecast_risk_off"] = -10

    fragility_status = None
    if forecast and not forecast.get("_missing"):
        try:
            from core.fragility import evaluate_fragility
            fr = evaluate_fragility(forecast=forecast, vix=vix)
            fragility_status = (fr.status or "").upper()
        except Exception:
            fragility_status = None
    reasons["fragility_status"] = fragility_status
    is_stress = fragility_status == "STRESS"
    if fragility_status == "STRESS":
        conflict_raw += 30; reasons["fragility"] = -30
    elif fragility_status == "FRAGILE":
        conflict_raw += 15; reasons["fragility"] = -15
    elif fragility_status == "CONFLICTED":
        conflict_raw += 10; reasons["fragility"] = -10

    # Intraday pulse — REGULAR only (this branch is REGULAR by definition).
    spy_chg = _readiness_spy_intraday_pct(spy_bars)
    qqq_chg = _readiness_quote_pct(etf_quotes, "QQQ")
    xlp_chg = _readiness_quote_pct(etf_quotes, "XLP")
    xlu_chg = _readiness_quote_pct(etf_quotes, "XLU")
    vxx_chg = _readiness_quote_pct(etf_quotes, "VXX")

    if spy_chg is not None and spy_chg <= -1.0:
        conflict_raw += 20; reasons["spy_red"] = -20
        chip_parts.append(f"SPY {spy_chg:+.1f}%")
    elif spy_chg is not None and spy_chg <= -0.5:
        conflict_raw += 10; reasons["spy_red"] = -10
        chip_parts.append(f"SPY {spy_chg:+.1f}%")
    if chip_parts and qqq_chg is not None and qqq_chg < 0:
        chip_parts.append(f"QQQ {qqq_chg:+.1f}%")

    defensive_green = (
        (xlp_chg is not None and xlp_chg > 0) or
        (xlu_chg is not None and xlu_chg > 0)
    )
    broad_red = (
        spy_chg is not None and spy_chg < 0
        and qqq_chg is not None and qqq_chg < 0
    )
    if broad_red and defensive_green:
        conflict_raw += 15; reasons["defensive_flip"] = -15
        chip_parts.append("defensive rotation")

    if vxx_chg is not None and vxx_chg >= 5.0:
        conflict_raw += 10; reasons["vxx_stress"] = -10
        chip_parts.append(f"VXX {vxx_chg:+.0f}%")

    applied_conflict = conflict_raw if is_stress else min(conflict_raw, _READINESS_CONFLICT_CAP)
    if (not is_stress) and conflict_raw > _READINESS_CONFLICT_CAP:
        reasons["conflict_cap_applied"] = True
    reasons["conflict_raw"] = -conflict_raw
    reasons["conflict_applied"] = -applied_conflict
    reasons["chip_parts"] = list(chip_parts)
    score -= applied_conflict

    fr_msgs = []
    if reasons["forecast_invalidation"]:
        fr_msgs.append("forecast invalidation breached")
    if fragility_status in ("FRAGILE", "CONFLICTED", "STRESS"):
        fr_msgs.append(f"research fragility {fragility_status.lower()}")
    if fr_msgs:
        bullets.append(" · ".join(fr_msgs))

    chip = " · ".join(chip_parts) if chip_parts else None

    if score >= 80:
        status, style = "RISK-ON",   "bold green"
    elif score >= 55:
        status, style = "SELECTIVE", "bold yellow"
    elif score >= 30:
        status, style = "STANDBY",   "bold red"
    else:
        status, style = "RISK-OFF",  "bold red on black"

    # Chip-presence floor: if the intraday-pulse chip is non-empty the tape is
    # showing at least one fragility signal (SPY red, defensive rotation, VXX
    # stress); the banner cannot read RISK-ON while the chip says otherwise.
    if chip and status == "RISK-ON":
        status, style = "SELECTIVE", "bold yellow"
        reasons["chip_floor_applied"] = True
    else:
        reasons["chip_floor_applied"] = False

    # Hard VIX floor: VIX >= 25 cannot show RISK-ON or SELECTIVE — the elevated
    # vol regime alone is enough to demote the banner to STANDBY.
    if vix is not None and vix >= 25 and status in ("RISK-ON", "SELECTIVE"):
        status, style = "STANDBY", "bold red"
        reasons["vix_floor_applied"] = True

    return status, style, bullets[:3], chip, reasons

def _filter_catalyst_news(items: List[Dict]) -> List[Dict]:
    out = []
    for item in items:
        title = (item.get("title") or "").lower()
        if any(kw in title for kw in _CATALYST_KW):
            out.append(item)
    return out[:5]

# ══════════════════════════════════════════════════════════════════════════════
# DATA LAYER
# ══════════════════════════════════════════════════════════════════════════════

_TTL = dict(positions=10, account=15, vix=60, spy_bars=300, etf_quotes=60,
            treasury=600, econ_cal=300, earnings=600, sector_pe=1800,
            earnings_cache_age=300,
            db_decisions=15, scan_results=20, news_market=120, regime=60,
            universe_snap=600, universe_meta=600, price_cache_meta=600,
            paper_summary=60, evidence_status=60,
            alpha_discovery=300, alpha_discovery_overlay=120, social_arb=300,
            market_forecast=300, market_forecast_validation=900,
            stock_lens=300, executive_gatekeeper=300,
            forecast_forward_summary=600, stock_lens_forward_summary=600,
            research_delta=120, research_note=60, weekly_review=600,
            mcp_audit_session=120, options_regime=300)

# Phase 2B.4 — dashboard surfaces "EARNINGS DATA STALE" when the
# underlying fmp:earnings_cal:7 cache row is older than this threshold.
# Set just past the bumped 13h TTL so a benign nightly→premarket gap
# does not trip the marker, but a missed cycle does.
_EARNINGS_CACHE_STALE_S = 14 * 3600

# Freshness thresholds (seconds) for forecast research artifacts.
_FORECAST_FRESH_S      = 24 * 3600
_VALIDATION_FRESH_S    = 14 * 24 * 3600
_STOCK_LENS_FRESH_S    = 24 * 3600
_GATEKEEPER_FRESH_S    = 24 * 3600

# ── Phase: June Dashboard Truth fixes (F5/F2/F3/F1/F4) — pure helpers ────────
# These are display-only.  They never touch providers, governance, execution,
# the live-capital gate, or any strategy/universe/gate logic.  The dashboard
# remains cache-only.

# Recall floor (percent) below which the scanner funnel has no demonstrated
# selection edge.  Doctrine: even at/above this floor the banner says
# UNPROVEN until a *forward* gate proves edge — the dashboard must never imply
# an edge exists from a recall number alone.
_EDGE_RECALL_FLOOR_PCT = 10.0

# Age (hours) past which an Alpha board/overlay source is flagged STALE in the
# panel header so a multi-day-old watchlist is never shown as if current.
_ALPHA_STALE_BANNER_H = 24.0


def selection_edge_status(
    recall_pct: Optional[float],
    baseline_pct: Optional[float],
    *,
    floor: float = _EDGE_RECALL_FLOOR_PCT,
) -> Dict[str, Any]:
    """Pure verdict builder for the persistent SELECTION EDGE banner.

    The verdict is ALWAYS research-only — operational health (SYSTEM OK) is
    orthogonal to a proven trade-selection edge, and a recall number alone can
    never prove edge (that needs a forward gate).  This helper only decides the
    descriptive detail + colour, never a "PROVEN" claim.

    Returns a dict: verdict, detail, recall_pct, baseline_pct, style, line.
    """
    r = None if recall_pct is None else float(recall_pct)
    b = None if baseline_pct is None else float(baseline_pct)

    if r is None:
        detail = "scanner recall n/a"
        style = "bold yellow"
    else:
        if b is not None:
            detail = f"scanner recall {r:.1f}% vs baseline {b:.1f}%"
        else:
            detail = f"scanner recall {r:.1f}%"
        below_floor = r < floor
        below_baseline = b is not None and r < b
        if below_baseline or below_floor:
            # Funnel worse than a dumb baseline and/or below the recall floor.
            style = "bold red"
        else:
            # Recall >= baseline and >= floor — still UNPROVEN (no forward gate).
            style = "bold yellow"

    return {
        "verdict": "UNPROVEN",
        "detail": detail,
        "recall_pct": r,
        "baseline_pct": b,
        "style": style,
        "line": f"WATCHLIST QUALITY: UNPROVEN · {detail} · research-only",
    }


def participation_status(audit: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Pure verdict builder for the PARTICIPATION line (Phase 1G.17).

    Input is the cache-only participation_bottleneck_audit sidecar (or None /
    a ``{"_missing": True}`` shell). Like selection_edge_status, this only
    formats — it never measures, never calls providers, and never claims
    health it cannot see.

    Returns: state (STARVED|HEALTHY|UNKNOWN), reason, last_decision,
    sniper_flow, voyager_flow, style, line.
    """
    if not audit or audit.get("_missing"):
        return {
            "state": "UNKNOWN", "reason": "no_audit_sidecar",
            "last_decision": None, "sniper_flow": None, "voyager_flow": None,
            "style": "dim",
            "line": ("PARTICIPATION: UNKNOWN · run "
                     "./scripts/run_research_cycle.sh participation-audit"),
        }
    v = audit.get("verdicts") or {}
    recent = audit.get("recent_window") or {}
    state = str(v.get("participation_state") or "UNKNOWN")
    reason = str(v.get("participation_reason") or "none")
    last_dec = (audit.get("last_dates") or {}).get("last_decision")
    sniper_flow = (recent.get("SNIPER") or {}).get("opportunities")
    voyager_flow = (recent.get("VOYAGER") or {}).get("opportunities")
    if state == "STARVED":
        style = "bold red"
        line = (f"PARTICIPATION EDGE: STARVED — no candidates reaching "
                f"council · last decision {last_dec or 'n/a'} · "
                f"reason={reason}")
    elif state == "HEALTHY":
        style = "bold green"
        line = f"PARTICIPATION: HEALTHY · last decision {last_dec or 'n/a'}"
    else:
        style = "bold yellow"
        line = f"PARTICIPATION: {state} · reason={reason}"
    return {
        "state": state, "reason": reason, "last_decision": last_dec,
        "sniper_flow": sniper_flow, "voyager_flow": voyager_flow,
        "style": style, "line": line,
    }


def mcp_stale_kind(stale_reasons: Optional[Iterable[str]]) -> str:
    """Classify MCP-audit staleness so the panel wording is not misleading.

    Returns:
      "none"           — not stale
      "benign_session" — the ONLY reason is a session change (e.g. a weekday
                         'regular' snapshot viewed on a weekend/CLOSED session).
                         A rerun does not help; this is not an error.
      "actionable"     — aged out or superseded by a newer forecast; a rerun
                         genuinely refreshes the state.
    """
    reasons = {str(r).strip() for r in (stale_reasons or []) if str(r).strip()}
    if not reasons:
        return "none"
    if reasons <= {"session_changed"}:
        return "benign_session"
    return "actionable"


class DataLayer:
    def __init__(self):
        self._lock = threading.Lock()
        self._data: Dict[str,Any] = {}
        self._ts:   Dict[str,float] = {}
        self._running = True
        self._alpaca_ok = False; self._fmp_ok = False
        # scanner state
        self._scanner_running  = False
        self._scanner_status   = "idle — press S to scan"
        self._scanner_last_run: Optional[float] = None
        self._ensure_scan_table()
        threading.Thread(target=self._loop, daemon=True).start()

    def _stale(self, k): return (time.time()-self._ts.get(k,0)) > _TTL.get(k,60)

    def invalidate_all(self) -> None:
        """Mark every cached key as stale so the next background tick
        re-reads sidecars and re-queries providers. Wired to the `r`
        hotkey for operator-initiated refresh; the existing 5s loop
        does the actual work, so the keypress itself is non-blocking."""
        with self._lock:
            self._ts.clear()
    def _set(self, k, v):
        with self._lock: self._data[k]=v; self._ts[k]=time.time()
    def get(self, k, default=None):
        with self._lock: return self._data.get(k, default)
    def provider_status(self): return self._alpaca_ok, self._fmp_ok
    def cache_age(self, k) -> int: return int(time.time()-self._ts.get(k,0))

    def system_health(self) -> Tuple[str, str, str]:
        """
        Returns (status, rich_style, reason).

        Phase 3A: Alpaca removed from health assessment; FMP is the critical
        research provider. Alpaca stub always succeeds so we ignore it here.

        SYSTEM OK       — FMP responding
        SYSTEM DEGRADED — FMP unavailable; research data may be stale
        """
        _, f_ok = self._alpaca_ok, self._fmp_ok
        if not f_ok:
            return "DEGRADED", "bold yellow", "FMP unavailable — research data may be stale"
        return "OK", "bold green", ""

    def _loop(self):
        while self._running:
            try: self._refresh()
            except Exception: pass
            time.sleep(5)

    def _refresh(self):
        if self._stale("vix"):
            v = self._fetch_vix(); self._set("vix", v); self._fmp_ok = v is not None
        if self._stale("positions"):
            p = self._fetch_positions(); self._set("positions", p); self._alpaca_ok = p is not None
        if self._stale("account"):      self._set("account",      self._fetch_account())
        if self._stale("spy_bars"):     self._set("spy_bars",     self._fetch_spy_bars())
        if self._stale("etf_quotes"):   self._set("etf_quotes",   self._fetch_etf_quotes())
        if self._stale("treasury"):     self._set("treasury",     self._fetch_treasury())
        if self._stale("econ_cal"):     self._set("econ_cal",     self._fetch_econ_cal())
        if self._stale("earnings"):     self._set("earnings",     self._fetch_earnings())
        if self._stale("earnings_cache_age"):
            self._set("earnings_cache_age", self._fetch_earnings_cache_age())
        if self._stale("sector_pe"):    self._set("sector_pe",    self._fetch_sector_pe())
        if self._stale("db_decisions"): self._set("db_decisions", self._fetch_db_decisions())
        if self._stale("scan_results"): self._set("scan_results", self._fetch_scan_results())
        if self._stale("news_market"):  self._set("news_market",  self._fetch_news_market())
        if self._stale("regime"):       self._set("regime",       self._fetch_regime())
        if self._stale("universe_snap"): self._set("universe_snap", self._fetch_universe_snap())
        if self._stale("price_cache_meta"): self._set("price_cache_meta", self._fetch_price_cache_meta())
        if self._stale("universe_meta"): self._set("universe_meta", self._fetch_universe_meta())
        if self._stale("paper_summary"): self._set("paper_summary", self._fetch_paper_summary())
        if self._stale("evidence_status"): self._set("evidence_status", self._fetch_evidence_status())
        if self._stale("alpha_discovery"): self._set("alpha_discovery", self._fetch_alpha_discovery())
        if self._stale("alpha_discovery_overlay"): self._set("alpha_discovery_overlay", self._fetch_alpha_discovery_overlay())
        if self._stale("social_arb"): self._set("social_arb", self._fetch_social_arb())
        if self._stale("market_forecast"):
            self._set("market_forecast", self._fetch_market_forecast())
        if self._stale("market_forecast_validation"):
            self._set("market_forecast_validation", self._fetch_market_forecast_validation())
        if self._stale("forecast_forward_summary"):
            self._set("forecast_forward_summary", self._fetch_forecast_forward_summary())
        if self._stale("stock_lens_forward_summary"):
            self._set("stock_lens_forward_summary", self._fetch_stock_lens_forward_summary())
        if self._stale("research_delta"):
            self._set("research_delta", self._fetch_research_delta())
        if self._stale("slippage_telemetry"):
            self._set("slippage_telemetry", self._fetch_risk_sidecar("slippage_telemetry_latest.json"))
        if self._stale("portfolio_concentration"):
            self._set("portfolio_concentration", self._fetch_risk_sidecar("portfolio_concentration_latest.json"))
        if self._stale("shadow_sizing"):
            self._set("shadow_sizing", self._fetch_risk_sidecar("shadow_sizing_latest.json"))
        # Phase 1D/1E — clean-epoch verdicts + broker-snapshot diagnostics.
        # Cache-only reads; the dashboard never invokes the hygiene report
        # nor the broker-snapshot CLI.
        if self._stale("paper_state_hygiene"):
            self._set("paper_state_hygiene",
                      self._fetch_risk_sidecar("paper_state_hygiene_latest.json"))
        if self._stale("broker_snapshot"):
            self._set("broker_snapshot", self._fetch_broker_snapshot())
        # Phase 1G.5 — cache-only read of the Scanner Truth Review summary.
        # The dashboard never runs the autopsy; it only reads the sidecar.
        if self._stale("scanner_truth_summary"):
            self._set("scanner_truth_summary",
                      self._fetch_risk_sidecar("scanner_truth_summary_latest.json"))
        # Phase 1G.12 — cache-only read of the Options Regime Lens sidecar.
        # The dashboard never runs the lens nor calls any options provider;
        # it only reads cache/research/options_regime_lens_latest.json.
        if self._stale("options_regime"):
            self._set("options_regime",
                      self._fetch_risk_sidecar("options_regime_lens_latest.json"))
        # Phase 2B.1 — cache-only read of the MCP audit session sidecar.
        # The dashboard never invokes the orchestrator nor any MCP tool.
        # Phase 2B.2 — enrich with a freshness verdict that compares the
        # sidecar against the latest regime forecast and the current
        # session so the panel can suppress a stale state.
        if self._stale("mcp_audit_session"):
            self._set("mcp_audit_session",
                      self._fetch_mcp_audit_sidecar())
        # Recall-Repair Shadow Lane (research-only) — cache-only read of the
        # forward-validation sidecar so the persistent edge banner + Mode-3
        # strip can surface its verdict.  The dashboard never runs the lane.
        if self._stale("recall_repair_shadow_forward"):
            self._set("recall_repair_shadow_forward",
                      self._fetch_risk_sidecar("recall_repair_shadow_forward_latest.json"))
        # Phase 1G.17 — cache-only read of the participation-bottleneck audit
        # sidecar (scan→council→decision flow verdicts). The dashboard never
        # runs the audit.
        if self._stale("participation_audit"):
            self._set("participation_audit",
                      self._fetch_risk_sidecar("participation_bottleneck_audit_latest.json"))
        # Phase 4 — Research Engine sidecars (cache-only; dashboard never runs these)
        if self._stale("market_heartbeat"):
            self._set("market_heartbeat",
                      self._fetch_risk_sidecar("market_heartbeat_latest.json"))
        if self._stale("research_scanner"):
            self._set("research_scanner",
                      self._fetch_risk_sidecar("research_scanner_latest.json"))

    # ── fetchers ──────────────────────────────────────────────────────────────

    def _fetch_vix(self):
        try: return get_fmp().get_vix()
        except Exception: return None

    def _fetch_positions(self):
        try:
            pos = get_alpaca().get_positions()
            stops = self._db_stops()
            entry_times = self._db_entry_times()
            for p in pos:
                t = p.get("ticker","")
                side_key = _side_key(p.get("side"))
                stop_data = stops.get((t, side_key), {}) or stops.get((t, ""), {}) or {}
                p["stop_loss"]    = stop_data.get("stop_loss")
                p["target_price"] = stop_data.get("target_price")
                p["strategy"]     = stop_data.get("strategy","—")
                p["entry_ts"]     = entry_times.get(t)
                # Source label for honest N/A display.
                # ``stops`` is keyed by (ticker, side_key) tuples; a bare
                # ``t not in stops`` check always evaluated true, so every
                # tracked position got mislabeled ``MANUAL`` and rendered
                # the wrong owner column. Match the same tuple keys we
                # used to look up ``stop_data`` above.
                if (t, side_key) not in stops and (t, "") not in stops:
                    p["sl_source"] = "MANUAL"     # no decision record found (manual/legacy position)
                elif not stop_data.get("stop_loss"):
                    p["sl_source"] = "NOT_SET"    # decision logged but stop not recorded
                else:
                    p["sl_source"] = "TRACKED"    # fully tracked by system
                qty   = abs(float(p.get("qty",1) or 1))
                entry = float(p.get("entry_price",0) or 0)
                pnl   = float(p.get("unrealized_pnl",0) or 0)
                cost  = entry * qty
                p["pnl_pct"] = round(pnl/cost*100, 2) if cost else 0.0
                # R-multiple
                stop = p["stop_loss"]
                if stop and entry:
                    curr  = float(p.get("current_price",0) or 0)
                    r_sz  = abs(entry - float(stop))
                    if p.get("side","long") == "long":
                        p["r_mult"] = round((curr-entry)/r_sz, 2) if r_sz else None
                    else:
                        p["r_mult"] = round((entry-curr)/r_sz, 2) if r_sz else None
                else:
                    p["r_mult"] = None
            return pos
        except Exception: return []

    def _db_stops(self) -> Dict:
        out = {}
        try:
            con = sqlite3.connect(str(cfg.DB_PATH), timeout=3)
            # Primary: open positions tracked by system
            for ticker,strat,direction,sl,tp in con.execute(
                "SELECT ticker,strategy,direction,stop_loss,target_price FROM decisions "
                "WHERE position_opened=1 AND position_closed=0 "
                "ORDER BY ts DESC LIMIT 50"
            ).fetchall():
                key = (ticker, _side_key(direction))
                if ticker and key not in out:
                    out[key]={"strategy":strat,"stop_loss":float(sl) if sl else None,
                              "target_price":float(tp) if tp else None}
            # Fallback: any recent decision for this ticker (catches legacy/manual positions)
            for ticker,strat,direction,sl,tp in con.execute(
                "SELECT ticker,strategy,direction,stop_loss,target_price FROM decisions "
                "WHERE ts > datetime('now', '-60 days') "
                "ORDER BY ts DESC LIMIT 100"
            ).fetchall():
                key = (ticker, _side_key(direction))
                if ticker and key not in out and (sl or tp):
                    out[key]={"strategy":strat,"stop_loss":float(sl) if sl else None,
                              "target_price":float(tp) if tp else None}
            # Final fallback: active paper rows with explicit stop/target, direction-safe.
            for ticker,strategy,side,sl,tp in con.execute(
                "SELECT ticker,strategy,side,stop_loss,target_price FROM paper_signals "
                "WHERE status IN ('open','governance_blocked','observe_only') "
                "ORDER BY logged_at DESC LIMIT 100"
            ).fetchall():
                key = (ticker, _side_key(side))
                if ticker and key not in out and (sl or tp):
                    out[key]={"strategy":strategy,"stop_loss":float(sl) if sl else None,
                              "target_price":float(tp) if tp else None}
            for ticker,direction,sl,tp in con.execute(
                "SELECT ticker,direction,stop_loss,target_price FROM voyager_paper_signals "
                "WHERE signal_status='open' ORDER BY logged_at DESC LIMIT 50"
            ).fetchall():
                key = (ticker, _side_key(direction))
                if ticker and key not in out and (sl or tp):
                    out[key]={"strategy":"VOYAGER","stop_loss":float(sl) if sl else None,
                              "target_price":float(tp) if tp else None}
            con.close()
        except Exception: pass
        return out

    def _db_entry_times(self) -> Dict:
        out = {}
        try:
            con = sqlite3.connect(str(cfg.DB_PATH), timeout=3)
            for ticker,ts in con.execute(
                "SELECT ticker,ts FROM decisions WHERE position_opened=1 ORDER BY ts DESC LIMIT 30"
            ).fetchall():
                if ticker and ticker not in out: out[ticker]=ts
            con.close()
        except Exception: pass
        return out

    def _fetch_account(self) -> Dict:
        try:
            a = get_alpaca().get_account()
            try:
                con = sqlite3.connect(str(cfg.DB_PATH), timeout=3)
                row = con.execute(
                    "SELECT COALESCE(SUM(pnl),0) FROM decisions "
                    "WHERE DATE(ts)=DATE('now') AND position_closed=1"
                ).fetchone()
                con.close()
                a["daily_pnl"] = float(row[0]) if row else 0.0
            except Exception: a["daily_pnl"] = 0.0
            return a
        except Exception: return {}

    def _fetch_spy_bars(self) -> List[Dict]:
        try: return get_fmp().get_spy_bars(days=60)
        except Exception: return []

    def _fetch_etf_quotes(self) -> Dict:
        # One batched FMP call for all three ETFs (vs. 3 individual calls before).
        # Results are cached per-ticker at TTL_QUOTE (20s) / TTL_QUOTE_AH (5min).
        try:
            batch = get_fmp().get_quotes_batch(["QQQ", "IWM", "VXX", "XLP", "XLU"])
            out = {}
            for sym, q in batch.items():
                out[sym] = {"price":      q.get("price",      0),
                            "change_pct": q.get("change_pct", 0),
                            "prev_close": q.get("prev_close", 0)}
            return out
        except Exception:
            return {}

    def _fetch_treasury(self):
        try: return get_fmp().get_treasury_rates()
        except Exception: return None

    def _fetch_econ_cal(self) -> List[Dict]:
        try: return get_fmp().get_economic_calendar(days_ahead=14)
        except Exception: return []

    def _fetch_earnings(self) -> List[Dict]:
        try: return get_fmp().get_earnings_calendar(days_ahead=7)
        except Exception: return []

    def _fetch_earnings_cache_age(self) -> Optional[int]:
        """Phase 2B.4: read cache_meta directly for the days_ahead=7
        earnings calendar entry the dashboard relies on.  Returns the
        underlying FMP-cache age in seconds, or None when no row exists.

        This is a stale-marker probe — never invokes FMP and never
        invalidates the cache.  The dashboard's earnings panel + ticker
        lookup compare against a doctrine threshold (14 h, one hour
        past the bumped TTL) to surface ``EARNINGS DATA STALE``.
        """
        try:
            con = sqlite3.connect(str(cfg.DB_PATH), timeout=2)
            try:
                row = con.execute(
                    "SELECT fetched_at FROM cache_meta WHERE key=?",
                    ("fmp:earnings_cal:7",),
                ).fetchone()
            finally:
                con.close()
            if not row:
                return None
            return int(time.time() - float(row[0]))
        except Exception:
            return None

    def _fetch_sector_pe(self) -> List[Dict]:
        try: return get_fmp().get_sector_pe()
        except Exception: return []

    def _fetch_db_decisions(self) -> List[Dict]:
        rows = []
        try:
            con = sqlite3.connect(str(cfg.DB_PATH), timeout=3)
            grouped: Dict[Tuple[str, str, str, str, str], Dict[str, Any]] = {}
            for ts,tick,strat,dirn,status,reason in con.execute(
                "SELECT ts,ticker,strategy,direction,status,veto_reason "
                "FROM scan_results WHERE ts > datetime('now', '-24 hours') "
                "ORDER BY ts DESC LIMIT 80"
            ).fetchall():
                key = (tick or "—", strat or "—", dirn or "—", status or "—", reason or "")
                if key not in grouped:
                    grouped[key] = {
                        "ts": ts, "ticker": tick or "—", "strategy": strat or "—",
                        "direction": dirn or "—", "status": status or "—",
                        "reason": reason or "", "count": 0,
                    }
                grouped[key]["count"] += 1
            con.close()

            for row in grouped.values():
                status = str(row["status"]).upper()
                if status == "READY_NOW":
                    verdict = "live-confirmed"
                    notes = "execution-confirmed live position"
                elif status == "SCAN_APPROVED":
                    try:
                        odt = _parse_iso_utc(row["ts"])
                        age_m = int((_utc_now() - odt).total_seconds() // 60)
                    except Exception:
                        age_m = 0
                    if age_m > 240:
                        verdict = "carry-forward"
                        notes = "approved earlier; review freshness before next session"
                    elif get_session_state() != SessionState.REGULAR:
                        verdict = "post-market review"
                        notes = "scanner-approved after session; review next session"
                    else:
                        verdict = "scan-approved"
                        notes = "dry-run only"
                elif status == "EXECUTION_FAILED":
                    verdict = "execution-failed"
                    notes = "scanner approved but execution failed"
                elif status == "ALLOCATION_BLOCKED":
                    verdict = "allocator-blocked"
                    notes = row["reason"] or "allocator blocked"
                elif status == "GATED":
                    verdict = "council-gated"
                    notes = row["reason"] or "council soft-veto"
                elif status == "REJECTED":
                    verdict = "rejected"
                    notes = row["reason"] or "scanner rejected"
                else:
                    verdict = status.lower() if status else "unknown"
                    notes = row["reason"] or "state recorded"
                if row["count"] > 1:
                    notes = f"{notes} · x{row['count']}"
                rows.append({
                    "ts": row["ts"],
                    "ticker": row["ticker"],
                    "strategy": row["strategy"],
                    "direction": row["direction"],
                    "verdict": verdict,
                    "notes": notes[:80],
                })
            rows.sort(key=lambda r: r["ts"], reverse=True)
        except Exception: pass
        return rows

    def _fetch_scan_results(self) -> Dict:
        """
        Read last 24h scan cycle results from the scan_results table.

        Pipeline state vocabulary (scan_results.status):
          READY_NOW          — order placed → live position  (daemon only)
          SCAN_APPROVED      — council approved in dry-run/manual scan; no allocator or execution attempted
          EXECUTION_FAILED   — council + allocator approved; order submitted but did not fill/succeed  (daemon only)
          ALLOCATION_BLOCKED — council approved; portfolio allocator blocked it  (daemon only)
          GATED              — council soft-vetoed (borderline score); scanner-confirmed
          REJECTED           — council hard-vetoed (low score); scanner-confirmed

        Legacy aliases (rows written by pre-v2 pipeline runs):
          APPROVED           → display as SCAN-APPROVED (ambiguous legacy)
          WATCH              → display as SCAN-APPROVED (pre-v2 daemon)

        Mapping to pipeline stages:
          READY_NOW          ↔ execution-confirmed / live position
          SCAN_APPROVED      ↔ scanner-confirmed + council-approved (no further steps taken)
          EXECUTION_FAILED   ↔ council-approved + allocated + execution rejected
          ALLOCATION_BLOCKED ↔ council-approved but allocator blocked
          GATED              ↔ scanner-confirmed, council soft-blocked
          REJECTED           ↔ scanner-confirmed, council hard-blocked

        Universe structural candidates (DEVELOPING etc.) come from universe snapshot
        — separate data source, see _fetch_universe_snap().
        """
        opportunities: List[Dict] = []
        vetoed:        List[Dict] = []  # GATED + ALLOCATION_BLOCKED — near-misses worth watching
        last_cycle_ts: Optional[str] = None
        _COLS = ("ticker","strategy","direction","score","entry_price","stop_loss",
                 "target_price","risk_reward","veto_verdict","veto_agent","veto_reason",
                 "soft_score","status","ts")
        try:
            con = sqlite3.connect(str(cfg.DB_PATH), timeout=3)
            rows = con.execute(
                f"SELECT {','.join(_COLS)} FROM scan_results "
                "WHERE ts > datetime('now', '-24 hours') "
                "ORDER BY score DESC LIMIT 100"
            ).fetchall()
            row = con.execute("SELECT MAX(ts) FROM scan_results").fetchone()
            last_cycle_ts = row[0] if row else None
            con.close()
            # Deduplicate by ticker (highest score wins, ORDER BY score DESC ensures this)
            seen: set = set()
            for r in rows:
                d = dict(zip(_COLS, r))
                t = d["ticker"]
                if t in seen: continue
                seen.add(t)
                status = str(d.get("status") or "").upper()
                if status in ("READY_NOW",
                             "SCAN_APPROVED", "EXECUTION_FAILED",
                             "APPROVED", "WATCH"):       # APPROVED/WATCH = legacy compat
                    opportunities.append(d)
                elif status in ("GATED", "ALLOCATION_BLOCKED"):
                    # Near-misses: scanner + council ran, something blocked late
                    vetoed.append(d)
                # REJECTED excluded — hard fails add noise to the panel
        except Exception:
            pass
        return {
            "opportunities": opportunities[:10],
            "vetoed":        vetoed[:8],
            "developing":    vetoed[:8],   # backward compat alias
            "last_cycle_ts": last_cycle_ts,
        }

    def _fetch_news_market(self) -> List[Dict]:
        try: return get_fmp().get_news(ticker=None, limit=20)
        except Exception: return []

    def _fetch_regime(self):
        try:
            from core.market_regime import MarketRegimeDetector
            return MarketRegimeDetector().detect_regime()
        except Exception: return None

    def _fetch_universe_snap(self) -> Dict:
        """Read the latest universe snapshot JSON from disk (written by universe builder)."""
        try:
            snap_path = cfg.CACHE_DIR / "universe" / "universe_snapshot_latest.json"
            if not snap_path.exists():
                return {}
            age_s = time.time() - snap_path.stat().st_mtime
            if age_s > 7200:          # ignore if older than 2 hours
                return {}
            data = json.loads(snap_path.read_text(encoding="utf-8"))
            data["_file_age_seconds"] = int(age_s)
            return data
        except Exception:
            return {}

    def _fetch_price_cache_meta(self) -> Dict:
        """Evidence-Freshness probe: resolve 'daily bars current' from the actual
        price cache (cache/prices, deep fallback).  Cache-only — stat + parquet
        index read; no providers.  Never raises; degrades to an explicit
        unknown+reason so the panel never shows a bare 'unknown'."""
        try:
            return _ef_price_status(
                cfg.CACHE_DIR / "prices", cfg.CACHE_DIR / "prices_deep",
                benchmark="SPY")
        except Exception as e:
            return {"field": "daily bars", "status": "unknown",
                    "latest_bar": None, "source": "cache/prices/SPY.parquet",
                    "reason": f"probe error: {e.__class__.__name__}"}

    def _fetch_universe_meta(self) -> Dict:
        """Evidence-Freshness probe: resolve 'universe age' from the universe
        snapshot mtime/generated_at + count WITHOUT the 2h discard used by
        _fetch_universe_snap (which exists to freshness-gate the structural pool
        for trade-readiness, not to report age).  Cache-only."""
        try:
            return _ef_universe_meta(
                cfg.CACHE_DIR / "universe" / "universe_snapshot_latest.json")
        except Exception as e:
            return {"field": "universe", "status": "unknown", "exists": False,
                    "age_seconds": None, "count": None,
                    "source": "cache/universe/universe_snapshot_latest.json",
                    "reason": f"probe error: {e.__class__.__name__}"}

    def _fetch_evidence_status(self) -> Dict:
        try:
            path = cfg.LOG_DIR / "paper_evidence_status.json"
            if not path.exists():
                return {}
            data = json.loads(path.read_text(encoding="utf-8"))
            scoreboard = cfg.LOG_DIR / "paper_scoreboard_latest.txt"
            if scoreboard.exists():
                data["scoreboard_mtime"] = datetime.fromtimestamp(scoreboard.stat().st_mtime).isoformat()
            return data
        except Exception:
            return {}

    def _fetch_alpha_discovery(self) -> Dict:
        try:
            data = load_alpha_discovery_board()
            if data:
                built_at = data.get("built_at") or data.get("_mtime_iso")
                data["_age_short"] = _age_from_iso_short(built_at)
            return data
        except Exception:
            return {}

    def _fetch_alpha_discovery_overlay(self) -> Dict:
        try:
            data = load_alpha_discovery_overlay()
            if data:
                built_at = data.get("built_at") or data.get("_mtime_iso")
                data["_age_short"] = _age_from_iso_short(built_at)
            return data
        except Exception:
            return {}

    def _fetch_social_arb(self) -> Dict:
        """Read the latest Social Arb artifact from disk. No provider calls."""
        try:
            path = cfg.CACHE_DIR / "research" / "social_arb_latest.json"
            if not path.exists():
                return {}
            data = json.loads(path.read_text(encoding="utf-8"))
            data["_mtime_iso"] = datetime.fromtimestamp(path.stat().st_mtime).isoformat()
            built_at = data.get("built_at") or data.get("_mtime_iso")
            data["_age_short"] = _age_from_iso_short(built_at)
            return data
        except Exception:
            return {}

    # ── regime forecaster v1 (research-only, cache-only) ───────────────────────
    def _fetch_market_forecast(self) -> Dict:
        """Read cache/research/regime_forecast_latest.json. No provider calls."""
        try:
            path = cfg.CACHE_DIR / "research" / "regime_forecast_latest.json"
            if not path.exists():
                return {"_missing": True, "_path": str(path)}
            mtime = path.stat().st_mtime
            data = json.loads(path.read_text(encoding="utf-8"))
            data["_mtime_iso"] = datetime.fromtimestamp(mtime).isoformat()
            built_at = data.get("built_at") or data["_mtime_iso"]
            data["_age_short"] = _age_from_iso_short(built_at)
            data["_age_seconds"] = int(time.time() - mtime)
            data["_stale"] = data["_age_seconds"] > _FORECAST_FRESH_S
            data["_missing"] = False
            # Back-fill breach state for older artifacts written before the
            # invalidation evaluator landed.  Pure function applied to the
            # already-cached snapshot — no provider calls.
            head = data.get("headline") or {}
            if "invalidation_breached" not in head:
                try:
                    from core.regime_forecaster import _evaluate_invalidation
                    inv = _evaluate_invalidation(
                        head.get("current_regime") or "",
                        data.get("market_trend") or {},
                        data.get("volatility") or {},
                        data.get("sector_rotation") or {},
                    )
                    head["invalidation_breached"] = inv["breached"]
                    head["invalidation_breach_reasons"] = inv["breach_reasons"]
                    if inv["breached"] and (head.get("confidence") or "").lower() != "low":
                        head["confidence"] = "low"
                    data["headline"] = head
                except Exception:
                    head.setdefault("invalidation_breached", False)
                    head.setdefault("invalidation_breach_reasons", [])
                    data["headline"] = head
            return data
        except Exception as exc:
            return {"_missing": True, "_error": str(exc)[:120]}

    def _fetch_market_forecast_validation(self) -> Dict:
        """Read cache/research/regime_forecast_validation_latest.json. Optional."""
        try:
            path = cfg.CACHE_DIR / "research" / "regime_forecast_validation_latest.json"
            if not path.exists():
                return {"_missing": True}
            mtime = path.stat().st_mtime
            data = json.loads(path.read_text(encoding="utf-8"))
            data["_mtime_iso"] = datetime.fromtimestamp(mtime).isoformat()
            built_at = (data.get("meta") or {}).get("built_at") or data["_mtime_iso"]
            data["_age_short"] = _age_from_iso_short(built_at)
            data["_age_seconds"] = int(time.time() - mtime)
            data["_stale"] = data["_age_seconds"] > _VALIDATION_FRESH_S
            data["_missing"] = False
            return data
        except Exception as exc:
            return {"_missing": True, "_error": str(exc)[:120]}

    def _fetch_forecast_forward_summary(self) -> Dict:
        """Phase 5: read cache/research/forecast_forward_summary_latest.json."""
        try:
            path = cfg.CACHE_DIR / "research" / "forecast_forward_summary_latest.json"
            if not path.exists():
                return {"_missing": True}
            mtime = path.stat().st_mtime
            data = json.loads(path.read_text(encoding="utf-8"))
            data["_mtime_iso"] = datetime.fromtimestamp(mtime).isoformat()
            built_at = data.get("generated_at") or data["_mtime_iso"]
            data["_age_short"] = _age_from_iso_short(built_at)
            data["_age_seconds"] = int(time.time() - mtime)
            data["_missing"] = False
            return data
        except Exception as exc:
            return {"_missing": True, "_error": str(exc)[:120]}

    def _fetch_stock_lens_forward_summary(self) -> Dict:
        """Phase 5: read cache/research/stock_lens_forward_summary_latest.json."""
        try:
            path = cfg.CACHE_DIR / "research" / "stock_lens_forward_summary_latest.json"
            if not path.exists():
                return {"_missing": True}
            mtime = path.stat().st_mtime
            data = json.loads(path.read_text(encoding="utf-8"))
            data["_mtime_iso"] = datetime.fromtimestamp(mtime).isoformat()
            built_at = data.get("generated_at") or data["_mtime_iso"]
            data["_age_short"] = _age_from_iso_short(built_at)
            data["_age_seconds"] = int(time.time() - mtime)
            data["_missing"] = False
            return data
        except Exception as exc:
            return {"_missing": True, "_error": str(exc)[:120]}

    def _fetch_research_delta(self) -> Dict:
        """Phase 7: read cache/research/research_delta_latest.json.

        Cache-only — never invokes research_delta.py.  The delta is
        produced by scripts/run_research_cycle.sh (nightly + premarket)
        and the dashboard only reads what's on disk.
        """
        try:
            path = cfg.CACHE_DIR / "research" / "research_delta_latest.json"
            if not path.exists():
                return {"_missing": True}
            mtime = path.stat().st_mtime
            data = json.loads(path.read_text(encoding="utf-8"))
            data["_mtime_iso"] = datetime.fromtimestamp(mtime).isoformat()
            built_at = data.get("built_at") or data["_mtime_iso"]
            data["_age_short"] = _age_from_iso_short(built_at)
            data["_age_seconds"] = int(time.time() - mtime)
            data["_missing"] = False
            return data
        except Exception as exc:
            return {"_missing": True, "_error": str(exc)[:120]}

    def _fetch_risk_sidecar(self, filename: str) -> Dict:
        """Phase 1B: read cache/research/{slippage,concentration,shadow}_latest.json.

        Cache-only — the dashboard never runs the report.  Sidecars are
        produced by ``scripts/run_research_cycle.sh risk-telemetry``.
        """
        try:
            path = cfg.CACHE_DIR / "research" / filename
            if not path.exists():
                return {"_missing": True}
            mtime = path.stat().st_mtime
            data = json.loads(path.read_text(encoding="utf-8"))
            data["_mtime_iso"] = datetime.fromtimestamp(mtime).isoformat()
            built_at = data.get("generated_at") or data["_mtime_iso"]
            data["_age_short"] = _age_from_iso_short(built_at)
            data["_age_seconds"] = int(time.time() - mtime)
            data["_missing"] = False
            return data
        except Exception as exc:
            return {"_missing": True, "_error": str(exc)[:120]}

    def _fetch_mcp_audit_sidecar(self) -> Dict:
        """Phase 2B.2: read cache/research/mcp_analysis_latest.json + attach
        a freshness verdict that knows about session changes and a newer
        regime_forecast_latest.json.  Cache-only; never invokes the
        orchestrator or any MCP tool.
        """
        data = self._fetch_risk_sidecar("mcp_analysis_latest.json")
        if data.get("_missing"):
            data["_freshness"] = _compute_freshness(
                kind="MCP_AUDIT", age_seconds=None,
            )
            return data

        try:
            cur_session = get_session_state().value
        except Exception:
            cur_session = None
        sidecar_session = data.get("session")
        sidecar_generated_at = data.get("generated_at")

        # Read the forecast mtime + built_at from disk so the comparison
        # works regardless of refresh order in this scheduler tick.  No
        # provider call — pure stat() / json parse.
        forecast_built_at: Optional[str] = None
        try:
            fpath = cfg.CACHE_DIR / "research" / "regime_forecast_latest.json"
            if fpath.exists():
                fmtime = fpath.stat().st_mtime
                try:
                    fdata = json.loads(fpath.read_text(encoding="utf-8"))
                    forecast_built_at = (
                        fdata.get("built_at")
                        or datetime.fromtimestamp(fmtime).isoformat()
                    )
                except Exception:
                    forecast_built_at = datetime.fromtimestamp(fmtime).isoformat()
        except Exception:
            forecast_built_at = None

        extra = _mcp_extra_reasons(
            sidecar_session=sidecar_session,
            current_session=cur_session,
            sidecar_generated_at=sidecar_generated_at,
            forecast_built_at=forecast_built_at,
        )
        data["_freshness"] = _compute_freshness(
            kind="MCP_AUDIT",
            age_seconds=data.get("_age_seconds"),
            generated_at=sidecar_generated_at,
            extra_reasons=extra,
        )
        return data

    def _fetch_broker_snapshot(self) -> Dict:
        """Phase 1E: read cache/state/broker_positions_snapshot.json.

        Cache-only — operator runs ``scripts/snapshot_broker_positions.py``
        on demand. Missing file degrades gracefully; the dashboard does
        not call Alpaca itself for this.
        """
        try:
            path = Path(cfg.PROJECT_ROOT) / "cache" / "state" / "broker_positions_snapshot.json" \
                if hasattr(cfg, "PROJECT_ROOT") else \
                Path(__file__).resolve().parents[1] / "cache" / "state" / "broker_positions_snapshot.json"
            if not path.exists():
                return {"_missing": True}
            mtime = path.stat().st_mtime
            data = json.loads(path.read_text(encoding="utf-8"))
            data["_mtime_iso"] = datetime.fromtimestamp(mtime).isoformat()
            built_at = data.get("generated_at") or data["_mtime_iso"]
            data["_age_short"] = _age_from_iso_short(built_at)
            data["_age_seconds"] = int(time.time() - mtime)
            data["_missing"] = False
            return data
        except Exception as exc:
            return {"_missing": True, "_error": str(exc)[:120]}

    def get_stock_lens(self, ticker: str) -> Dict:
        """
        Read cache/research/stock_lens_<TICKER>_latest.json on demand.
        Cached per-ticker with a short TTL. No provider calls; never auto-runs.
        Returns {"_missing": True} if the artifact is not on disk.
        """
        if not ticker:
            return {"_missing": True}
        ticker = ticker.upper()
        cache_key = f"stock_lens::{ticker}"
        # Short in-memory TTL to avoid disk thrash on each render tick.
        ttl_s = _TTL.get("stock_lens", 300)
        if (time.time() - self._ts.get(cache_key, 0)) <= ttl_s:
            cached = self.get(cache_key)
            if cached is not None:
                return cached
        try:
            path = cfg.CACHE_DIR / "research" / f"stock_lens_{ticker}_latest.json"
            if not path.exists():
                payload = {"_missing": True, "ticker": ticker, "_path": str(path)}
            else:
                mtime = path.stat().st_mtime
                payload = json.loads(path.read_text(encoding="utf-8"))
                payload["_mtime_iso"] = datetime.fromtimestamp(mtime).isoformat()
                built_at = payload.get("built_at") or payload["_mtime_iso"]
                payload["_age_short"] = _age_from_iso_short(built_at)
                payload["_age_seconds"] = int(time.time() - mtime)
                payload["_stale"] = payload["_age_seconds"] > _STOCK_LENS_FRESH_S
                payload["_missing"] = False
        except Exception as exc:
            payload = {"_missing": True, "ticker": ticker, "_error": str(exc)[:120]}
        # Override TTL specifically for stock_lens entries.
        with self._lock:
            self._data[cache_key] = payload
            self._ts[cache_key] = time.time()
        return payload

    def get_executive_gatekeeper(self, ticker: str, *,
                                 is_earnings_day: bool = False,
                                 is_intraday_selected: bool = False) -> Dict:
        """
        Read cache/research/executive_gatekeeper_<TICKER>_latest.json on demand.
        Cache-only, never auto-runs the gatekeeper, never calls a provider.
        Returns ``{"_missing": True}`` when the artifact is not on disk.

        Phase 2B.2: a ``_freshness`` verdict is attached to every non-missing
        payload (kind=GATEKEEPER) so the panel can suppress stale "Top
        reasons" instead of displaying old verdicts as current.  The legacy
        ``_stale`` boolean is preserved for back-compat.
        """
        if not ticker:
            return {"_missing": True}
        ticker = ticker.upper()
        # Phase 2B.2: cache key includes the earnings/intraday flags so a
        # ticker that flips to earnings-day status mid-session sees the
        # tighter threshold immediately rather than reusing the prior
        # verdict from the 5-minute in-memory TTL.
        flag_key = f"{int(bool(is_earnings_day))}{int(bool(is_intraday_selected))}"
        cache_key = f"executive_gatekeeper::{ticker}::{flag_key}"
        ttl_s = _TTL.get("executive_gatekeeper", 300)
        if (time.time() - self._ts.get(cache_key, 0)) <= ttl_s:
            cached = self.get(cache_key)
            if cached is not None:
                return cached
        try:
            path = cfg.CACHE_DIR / "research" / f"executive_gatekeeper_{ticker}_latest.json"
            if not path.exists():
                payload = {"_missing": True, "ticker": ticker, "_path": str(path)}
                payload["_freshness"] = _compute_freshness(
                    kind="GATEKEEPER",
                    age_seconds=None,
                    is_earnings_day=is_earnings_day,
                    is_intraday_selected=is_intraday_selected,
                )
            else:
                mtime = path.stat().st_mtime
                payload = json.loads(path.read_text(encoding="utf-8"))
                payload["_mtime_iso"] = datetime.fromtimestamp(mtime).isoformat()
                built_at = payload.get("generated_at") or payload["_mtime_iso"]
                payload["_age_short"] = _age_from_iso_short(built_at)
                payload["_age_seconds"] = int(time.time() - mtime)
                fresh = _compute_freshness(
                    kind="GATEKEEPER",
                    age_seconds=payload["_age_seconds"],
                    generated_at=built_at,
                    is_earnings_day=is_earnings_day,
                    is_intraday_selected=is_intraday_selected,
                )
                payload["_freshness"] = fresh
                # Legacy field kept for back-compat with any other readers.
                payload["_stale"] = bool(fresh.get("stale"))
                payload["_missing"] = False
        except Exception as exc:
            payload = {"_missing": True, "ticker": ticker, "_error": str(exc)[:120]}
            payload["_freshness"] = _compute_freshness(
                kind="GATEKEEPER",
                age_seconds=None,
                is_earnings_day=is_earnings_day,
                is_intraday_selected=is_intraday_selected,
            )
        with self._lock:
            self._data[cache_key] = payload
            self._ts[cache_key] = time.time()
        return payload

    def get_weekly_review(self) -> Dict:
        """
        Read cache/research/weekly_review_latest.json.  Cache-only — never
        invokes review_misses.py.  Returns ``{"_missing": True}`` when the
        artifact has not been built yet.
        """
        cache_key = "weekly_review"
        ttl_s = _TTL.get("weekly_review", 600)
        if (time.time() - self._ts.get(cache_key, 0)) <= ttl_s:
            cached = self.get(cache_key)
            if cached is not None:
                return cached
        try:
            path = cfg.CACHE_DIR / "research" / "weekly_review_latest.json"
            if not path.exists():
                payload: Dict[str, Any] = {"_missing": True, "_path": str(path)}
            else:
                mtime = path.stat().st_mtime
                payload = json.loads(path.read_text(encoding="utf-8"))
                payload["_mtime_iso"] = datetime.fromtimestamp(mtime).isoformat()
                payload["_age_short"] = _age_from_iso_short(payload.get("generated_at"))
                payload["_age_seconds"] = int(time.time() - mtime)
                payload["_missing"] = False
        except Exception as exc:
            payload = {"_missing": True, "_error": str(exc)[:120]}
        with self._lock:
            self._data[cache_key] = payload
            self._ts[cache_key] = time.time()
        return payload

    def get_research_note(self, ticker: str) -> Dict:
        """
        Read the latest research journal note for ``ticker`` from the
        cache-only JSONL store at data/state/research_notes.jsonl.

        Returns ``{"_missing": True}`` when no note exists for the ticker
        (or the store itself is missing).  Cache-only — never calls a
        provider.  Cached per-ticker with a short TTL so the panel does
        not re-read the file on every render tick, but a freshly-added
        note still shows up within ~60s.
        """
        if not ticker:
            return {"_missing": True}
        sym = ticker.upper()
        cache_key = f"research_note::{sym}"
        ttl_s = _TTL.get("research_note", 60)
        if (time.time() - self._ts.get(cache_key, 0)) <= ttl_s:
            cached = self.get(cache_key)
            if cached is not None:
                return cached
        try:
            from research import research_journal as rj
            note = rj.latest_note(sym)
            payload: Dict[str, Any] = note or {"_missing": True, "ticker": sym}
            if note:
                payload["_age_short"] = _age_from_iso_short(note.get("timestamp"))
                # Compute days_until_review for the panel without re-parsing.
                rd = note.get("review_date")
                if rd:
                    try:
                        dr = date.fromisoformat(str(rd)[:10])
                        payload["_days_until_review"] = (dr - date.today()).days
                    except Exception:
                        pass
                # Phase 1F+ extra: also surface absolute age in days so
                # the panel can auto-archive notes that have aged out
                # without ever being reviewed (no review_date case).
                try:
                    ts = _parse_iso_utc(note.get("timestamp"))
                    payload["_age_days"] = (_utc_now() - ts).total_seconds() / 86400.0
                except Exception:
                    pass
                payload["_missing"] = False
        except Exception as exc:
            payload = {"_missing": True, "ticker": sym, "_error": str(exc)[:120]}
        with self._lock:
            self._data[cache_key] = payload
            self._ts[cache_key] = time.time()
        return payload

    def _fetch_paper_summary(self) -> Dict:
        """
        Compact paper-validation summary for dashboard display.

        This mirrors the scoreboard's active/effective distinction but keeps the
        data shape small enough for live terminal panels.
        """
        active = {row.key for row in registry_rows(active_paper_strategies())}
        summary = {
            "sleeves": {
                "VOYAGER": {"raw": 0, "effective": 0, "open": 0, "closed": 0, "blocked": 0, "observe": 0},
                "SNIPER": {"raw": 0, "effective": 0, "open": 0, "closed": 0, "blocked": 0, "observe": 0},
                "SHORT": {"raw": 0, "effective": 0, "open": 0, "closed": 0, "blocked": 0, "observe": 0},
            },
            "readiness": {},
            "governance": {
                "same_ticker": 0, "sector": 0, "regime": 0, "max_position": 0,
                "duplicate": 0, "frozen": 0, "other": 0,
            },
        }
        try:
            con = sqlite3.connect(str(cfg.DB_PATH), timeout=3)
            con.row_factory = sqlite3.Row
            paper_rows = [dict(r) for r in con.execute("SELECT * FROM paper_signals").fetchall()]
            outcome_rows = [dict(r) for r in con.execute("SELECT * FROM paper_signal_outcomes").fetchall()]
            voyager_rows = [dict(r) for r in con.execute("SELECT * FROM voyager_paper_signals").fetchall()]
            con.close()
        except Exception:
            return summary

        converted = []
        for row in paper_rows:
            strat = normalize_strategy(row.get("strategy"))
            converted.append({
                **row,
                "strategy": strat,
                "side": row.get("side"),
                "status": row.get("status") or "open",
            })
        for row in voyager_rows:
            converted.append({
                "id": f"voyager:{row.get('id')}",
                "strategy": "VOYAGER",
                "ticker": row.get("ticker"),
                "side": row.get("direction", "LONG"),
                "status": row.get("signal_status") or "open",
                "outcome_30d": row.get("outcome_30d"),
                "outcome_90d": row.get("outcome_90d"),
                "above_ma200_at_30d": row.get("above_ma200_at_30d"),
            })

        seen_open: set[tuple[str, str, str]] = set()
        effective = []
        for row in sorted(converted, key=lambda r: str(r.get("logged_at") or ""), reverse=True):
            strat = normalize_strategy(row.get("strategy"))
            if strat not in active:
                continue
            status = str(row.get("status") or "open").lower()
            key = (strat, str(row.get("ticker") or "").upper(), str(row.get("side") or "").upper())
            raw_bucket = summary["sleeves"].setdefault(strat, {"raw": 0, "effective": 0, "open": 0, "closed": 0, "blocked": 0, "observe": 0})
            raw_bucket["raw"] += 1
            if status == "open":
                if key in seen_open:
                    raw_bucket["observe"] += 1
                    continue
                seen_open.add(key)
            effective.append(row)

        for row in effective:
            strat = normalize_strategy(row.get("strategy"))
            bucket = summary["sleeves"][strat]
            bucket["effective"] += 1
            status = str(row.get("status") or "open").lower()
            if status == "open":
                bucket["open"] += 1
            elif status == "governance_blocked":
                bucket["blocked"] += 1
            elif status in ("observe_only", "duplicate"):
                bucket["observe"] += 1
            else:
                bucket["closed"] += 1

        by_signal: Dict[str, list[Dict]] = {}
        for row in outcome_rows:
            by_signal.setdefault(str(row.get("signal_id")), []).append(row)

        def tactical_metrics(strategy: str, horizon: int) -> Dict:
            rows = [r for r in paper_rows if normalize_strategy(r.get("strategy")) == strategy and str(r.get("status") or "").lower() != "governance_blocked"]
            outcomes = []
            stops = []
            for sig in rows:
                for out in by_signal.get(str(sig.get("id")), []):
                    if int(out.get("horizon_days") or 0) == horizon and not bool(out.get("still_open")):
                        outcomes.append(out)
                        if out.get("stop_hit") is not None:
                            stops.append(bool(out.get("stop_hit")))
            adjusted = [float(o.get("adjusted_return_pct")) for o in outcomes if o.get("adjusted_return_pct") is not None]
            wins = [x for x in adjusted if x > 0]
            return {
                "signals": len(rows),
                "completed": len(outcomes),
                "wr": (len(wins) / len(adjusted) * 100) if adjusted else None,
                "avg_adj": (sum(adjusted) / len(adjusted)) if adjusted else None,
                "stop": (sum(stops) / len(stops) * 100) if stops else None,
            }

        summary["readiness"]["SNIPER"] = tactical_metrics("SNIPER", 10)
        summary["readiness"]["SHORT"] = tactical_metrics("SHORT", 3)

        v_completed = [r for r in voyager_rows if r.get("outcome_30d") is not None]
        v_ret = [float(r.get("outcome_30d")) for r in v_completed if r.get("outcome_30d") is not None]
        ma200 = [bool(r.get("above_ma200_at_30d")) for r in v_completed if r.get("above_ma200_at_30d") is not None]
        wr90_rows = [r for r in voyager_rows if r.get("outcome_90d") is not None]
        wr90 = [float(r.get("outcome_90d")) for r in wr90_rows if r.get("outcome_90d") is not None]
        summary["readiness"]["VOYAGER"] = {
            "signals": len(voyager_rows),
            "completed": len(v_completed),
            "avg_30d": (sum(v_ret) / len(v_ret)) if v_ret else None,
            "ma200_hold": (sum(ma200) / len(ma200) * 100) if ma200 else None,
            "wr90": (sum(1 for x in wr90 if x > 0) / len(wr90) * 100) if wr90 else None,
        }

        for row in paper_rows:
            notes = str(row.get("notes") or "").lower()
            strat = normalize_strategy(row.get("strategy"))
            if not is_active_paper_strategy(strat):
                summary["governance"]["frozen"] += 1
            if str(row.get("status") or "").lower() != "governance_blocked":
                continue
            if "already has active paper exposure" in notes:
                summary["governance"]["same_ticker"] += 1
                summary["governance"]["duplicate"] += 1
            elif "sector exposure" in notes:
                summary["governance"]["sector"] += 1
            elif "regime cluster" in notes:
                summary["governance"]["regime"] += 1
            elif "max paper positions" in notes or "max positions" in notes:
                summary["governance"]["max_position"] += 1
            else:
                summary["governance"]["other"] += 1

        return summary

    # ── scan table bootstrap ──────────────────────────────────────────────────

    def _ensure_scan_table(self):
        """Create scan_results table if it doesn't exist (needed before daemon first run)."""
        try:
            con = sqlite3.connect(str(cfg.DB_PATH), timeout=3)
            con.executescript("""
CREATE TABLE IF NOT EXISTS scan_results (
    id           TEXT PRIMARY KEY,
    run_id       TEXT,
    ts           TEXT NOT NULL,
    ticker       TEXT NOT NULL,
    strategy     TEXT NOT NULL,
    direction    TEXT NOT NULL,
    score        REAL,
    entry_price  REAL,
    stop_loss    REAL,
    target_price REAL,
    risk_reward  REAL,
    veto_verdict TEXT,
    veto_agent   TEXT,
    veto_reason  TEXT,
    soft_score   REAL,
    status       TEXT
);
""")
            con.commit()
            con.close()
        except Exception:
            pass

    # ── standalone scanner ────────────────────────────────────────────────────

    def scanner_status(self) -> Dict:
        return {
            "running":  self._scanner_running,
            "status":   self._scanner_status,
            "last_run": self._scanner_last_run,
        }

    def trigger_scan(self) -> bool:
        """Start a background scan without order execution. Returns False if already running."""
        if self._scanner_running:
            return False
        threading.Thread(target=self._background_scan, daemon=True).start()
        return True

    def _background_scan(self):
        """Run active paper scanners + veto council. Writes to scan_results. No orders."""
        import uuid as _uuid
        run_id = str(_uuid.uuid4())[:8]
        self._scanner_running = True
        self._scanner_status  = "starting…"
        try:
            # Lazy strategy imports — avoid loading heavy modules at dashboard startup
            from strategies.sniper       import SniperScanner
            from strategies.voyager      import VoyagerScanner
            from strategies.short_sleeve import ShortSleeveScanner
            from council.veto_council    import VetoCouncil

            # Account equity (best-effort — default to $100k)
            try:
                acct   = get_alpaca().get_account()
                equity = float(acct.get("equity", 100_000) or 100_000)
            except Exception:
                equity = 100_000

            # Use universe snapshot pools if available, else fallback
            snap = self.get("universe_snap") or {}
            strategy_universes = {
                "sniper":     snap.get("sniper_universe")     or _SCANNER_FALLBACK,
                "voyager":    snap.get("voyager_universe")    or _SCANNER_FALLBACK,
                "short":      snap.get("short_universe")      or _SCANNER_FALLBACK,
            }
            using_fallback = not bool(snap.get("sniper_universe"))

            scanner_factories = {
                "sniper":     SniperScanner(account_equity=equity),
                "voyager":    VoyagerScanner(account_equity=equity),
                "short":      ShortSleeveScanner(account_equity=equity),
            }
            scanners = {k: scanner_factories[k] for k in active_scanner_keys() if k in scanner_factories}
            council = VetoCouncil()

            # Mock portfolio — no real positions for standalone scan
            mock_portfolio = {
                "open_positions":   0,
                "max_positions":    10,
                "gross_long_pct":   0.0,
                "gross_short_pct":  0.0,
                "daily_pnl_pct":    0.0,
            }

            # Run scanners
            all_opps: List[Dict] = []
            n_strats = len(scanners)
            for i, (name, scanner) in enumerate(scanners.items(), 1):
                self._scanner_status = f"scanning {name.upper()} ({i}/{n_strats})…"
                tickers = strategy_universes.get(name, _SCANNER_FALLBACK)
                try:
                    opps = scanner.scan(tickers)
                    all_opps.extend(opps)
                except Exception:
                    pass

            # Deduplicate + rank
            self._scanner_status = "running veto council…"
            seen: set = set()
            ranked: List[Dict] = []
            for opp in sorted(all_opps, key=lambda x: x.get("score", 0), reverse=True):
                if opp["ticker"] not in seen:
                    seen.add(opp["ticker"])
                    ranked.append(opp)

            # Veto council evaluation (no circuit breaker — standalone mode)
            now_ts = _utc_now().replace(tzinfo=None).isoformat()
            rows = []
            for opp in ranked[:40]:
                try:
                    result  = council.evaluate(opp, mock_portfolio)
                    verdict = result.get("verdict", "VETOED")
                    soft    = result.get("soft_score")
                    if verdict == "APPROVED":
                        status = "SCAN_APPROVED"   # dry-run: no allocator/execution attempted
                    elif soft is not None and soft >= 50:
                        status = "GATED"
                    else:
                        status = "REJECTED"
                    rows.append((
                        str(_uuid.uuid4()), run_id, now_ts,
                        opp["ticker"].upper(), (opp.get("strategy") or "?").upper(),
                        (opp.get("direction") or "LONG").upper(),
                        opp.get("score"), opp.get("entry_price"),
                        opp.get("stop_loss"), opp.get("target_price"),
                        opp.get("risk_reward"),
                        verdict.upper(), result.get("agent","") or "",
                        result.get("reason","") or "",
                        soft, status,
                    ))
                except Exception:
                    pass

            # Persist results
            if rows:
                con = sqlite3.connect(str(cfg.DB_PATH), timeout=5)
                con.executemany(
                    """INSERT INTO scan_results
                       (id, run_id, ts, ticker, strategy, direction, score,
                        entry_price, stop_loss, target_price, risk_reward,
                        veto_verdict, veto_agent, veto_reason, soft_score, status)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    rows,
                )
                con.commit()
                con.close()

            n_approved = sum(1 for r in rows if r[-1] == "SCAN_APPROVED")
            n_gated    = sum(1 for r in rows if r[-1] == "GATED")
            fallback_note = " [fallback universe]" if using_fallback else ""
            self._scanner_status = (
                f"{n_approved} approved  {n_gated} gated  "
                f"({len(rows)} evaluated){fallback_note}"
            )
            self._scanner_last_run = time.time()
            # Force scan_results cache refresh on next poll
            self._ts["scan_results"] = 0

        except Exception as exc:
            self._scanner_status = f"error: {str(exc)[:70]}"
        finally:
            self._scanner_running = False

    def stop(self): self._running = False


# ══════════════════════════════════════════════════════════════════════════════
# CLAUDE ANALYZER — structured, quant-aware
# ══════════════════════════════════════════════════════════════════════════════

class ClaudeAnalyzer:
    MAX   = int(os.environ.get("CLAUDE_DAILY_BUDGET", "20"))
    TTL   = 1800

    def __init__(self):
        self._cache: Dict[str,Dict] = {}
        self._calls = 0; self._day = date.today().isoformat()
        self._lock = threading.Lock()

    def _reset(self):
        today = date.today().isoformat()
        if today != self._day: self._day = today; self._calls = 0

    def calls(self) -> int: self._reset(); return self._calls
    def budget(self) -> int: return self.MAX

    def cached(self, ticker: str) -> Optional[Dict]:
        with self._lock:
            e = self._cache.get(ticker.upper())
            if e and (time.time()-e["_ts"]) < self.TTL: return e
        return None

    def age_str(self, ticker: str) -> str:
        with self._lock:
            e = self._cache.get(ticker.upper())
            if not e: return "—"
        s = int(time.time()-e["_ts"]); return f"{s//60}m{s%60:02d}s ago"

    def analyze(self, ticker: str, bars: List[Dict], sentiment: float,
                vix: Optional[float], regime: Optional[Dict],
                econ_cal: List[Dict], earnings: List[Dict]) -> Dict:
        ticker = ticker.upper()
        cached = self.cached(ticker)
        if cached: return cached
        self._reset()
        if self._calls >= self.MAX:
            return self._stub(ticker, f"Daily budget ({self.MAX}) reached")
        client = _get_claude_client()
        if not client:
            return self._stub(ticker, "ANTHROPIC_API_KEY not set")
        if len(bars) < 20:
            return self._stub(ticker, "Insufficient price data (<20 bars)")

        closes  = [float(b["close"])  for b in bars]
        highs   = [float(b["high"])   for b in bars]
        lows    = [float(b["low"])    for b in bars]
        vols    = [int(b.get("volume",0)) for b in bars]
        close   = closes[-1]
        chg     = (close-closes[-2])/closes[-2]*100 if len(closes)>1 else 0.0
        rsi     = calc_rsi(closes)
        ema20   = calc_ema(closes, 20)
        ema50   = calc_ema(closes, 50)
        macd_v, macd_t = calc_macd(closes)
        atr     = calc_atr(bars)
        avg_vol = sum(vols[-20:])/20 if len(vols)>=20 else sum(vols)/len(vols) if vols else 1
        vol_r   = round(vols[-1]/avg_vol, 2) if avg_vol else 1.0
        wk_hi   = max(highs[-252:]) if len(highs)>=50 else max(highs)
        wk_lo   = min(lows[-252:])  if len(lows)>=50  else min(lows)
        pos_pct = int((close-wk_lo)/(wk_hi-wk_lo)*100) if wk_hi!=wk_lo else 50
        rel_spy = ""
        spy_bars = get_fmp().get_spy_bars(days=15)
        if spy_bars and len(bars) >= 10:
            spy_ret = (spy_bars[-1]["close"]-spy_bars[-10]["close"])/spy_bars[-10]["close"]*100
            stk_ret = (close-closes[-10])/closes[-10]*100
            rel_spy = f"{stk_ret-spy_ret:+.1f}% vs SPY last 10d"

        # earnings context
        earn_str = "None identified"
        for e in sorted(earnings, key=lambda x: x.get("date","")):
            if e.get("symbol","").upper() == ticker:
                d = e.get("date","")[:10]
                try:
                    days_away = (date.fromisoformat(d)-date.today()).days
                    earn_str = f"Earnings {d} ({days_away}d)"
                except Exception:
                    earn_str = f"Earnings {d}"
                break

        # macro context
        now = _now_et()
        macro_str = "No HIGH-impact event in next 48h"
        for ev in sorted(econ_cal, key=lambda x: x.get("date","")):
            if str(ev.get("impact","")).lower()!="high": continue
            try:
                edt = datetime.fromisoformat(str(ev.get("date","")).replace("Z",""))
                # FMP economic-calendar timestamps are UTC.
                if edt.tzinfo is None: edt = edt.replace(tzinfo=timezone.utc)
                hrs = (edt-now).total_seconds()/3600
                if 0 < hrs <= 48:
                    macro_str = f"{_clip(str(ev.get('event','')),35)} in {int(hrs)}h"
                    break
            except Exception: continue

        reg_str  = str(regime.get("regime","unknown")).upper() if regime else "unknown"
        vix_str  = f"{vix:.1f}" if vix else "unknown"
        sent_lbl = "bullish" if sentiment>0.6 else ("bearish" if sentiment<0.4 else "neutral")

        prompt = f"""You are a quantitative analyst on a multi-strategy equity trading desk.
Assess the following stock for intraday/swing trading opportunity.

MARKET CONTEXT
  Regime: {reg_str}
  VIX: {vix_str}
  Market: {_market_status()}

TICKER: {ticker}
  Price: ${close:.2f} ({chg:+.1f}% today)
  ATR(14): ${atr:.2f} ({atr/close*100:.1f}%)
  52w position: {pos_pct}th percentile (${wk_lo:.2f}–${wk_hi:.2f})

STRUCTURE
  EMA20 ${ema20:.2f} → price {'ABOVE' if close>ema20 else 'BELOW'} by {abs(close-ema20)/ema20*100:.1f}%
  EMA50 ${ema50:.2f} → price {'ABOVE' if close>ema50 else 'BELOW'} by {abs(close-ema50)/ema50*100:.1f}%
  RSI(14): {rsi}
  MACD: {macd_t.upper()} ({macd_v:+.3f})
  Volume: {vol_r:.1f}x 20d average
  Relative strength: {rel_spy or 'unavailable'}

EVENTS
  {earn_str}
  Macro risk: {macro_str}
  News sentiment: {sentiment:.2f} ({sent_lbl})

STRATEGIES (match to best fit):
  SNP: ACTIVE PAPER — SNIPER_V6 momentum breakout LONG
  VOY: ACTIVE PAPER — VOYAGER long-horizon institutional accumulation LONG
  MANUAL: discretionary/manual research only, not paper evidence
  NONE: no edge or conflicting signals

Frozen this phase, NOT tradable suggestions: SHORT_A (research-only — short-side
awareness via Short Opportunity Radar), REMORA, CONTRARIAN, SHORT_B, PATHFINDER.

Respond in EXACTLY this format — no preamble, no markdown:
BIAS: BULLISH|BEARISH|NEUTRAL
SLEEVE_RESEMBLANCE: SNP|VOY|SHA|MANUAL|NONE
ACTIONABLE_NOW: YES|NO
TIMEFRAME: intraday|swing|event|position
REGIME_FIT: supportive|mixed|hostile
QUALITY: 0-100
WHY: [single concise gating reason in one sentence]
EVIDENCE:
• [specific evidence 1]
• [specific evidence 2]
• [specific evidence 3, 4 max]
INVALIDATION: [specific price or condition]
NEXT_SESSION_PLAN: [Concrete triggers for the next 1-2 sessions with explicit price levels. Format: "If reclaims $X on volume → action; if loses $Y on close → action; otherwise: action". Must include at least one upside trigger AND one downside invalidation level, even when ACTIONABLE_NOW=NO. No vague phrases like "wait for confirmation" without a price.]
EVENT_RISK: {earn_str if 'Earnings' in earn_str else 'none'}
ACTION: Enter|Watch|Wait|Avoid
INPUTS: price|volume|regime|events|news

Rules: High RSI in a strong uptrend is NOT automatically overbought — judge within trend.
Volume alone without price confirmation is weak. Avoid generic TA clichés. Score quality
0-100 for setup clarity × confirmation × regime fit. If data is weak, say so. The
NEXT_SESSION_PLAN field is required even when ACTIONABLE_NOW=NO — operators rely on it
to know what level would change the picture. Use concrete numbers from the STRUCTURE
block (EMA20/EMA50/ATR/52w levels), not generic phrases."""

        try:
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=420,
                messages=[{"role":"user","content":prompt}]
            )
            raw = resp.content[0].text.strip()
        except Exception as exc:
            return self._stub(ticker, f"API error: {type(exc).__name__}")

        result = self._parse(raw, ticker)
        result["_spark"] = sparkline(closes, 40)
        result["_rsi"]   = rsi
        result["_ema20"] = ema20
        result["_ema50"] = ema50
        result["_macd"]  = macd_t
        result["_atr"]   = atr
        result["_vol_r"] = vol_r
        result["_close"] = close
        result["_chg"]   = chg
        result["_ts"]    = time.time()

        with self._lock:
            self._calls += 1
            self._cache[ticker] = result
        return result

    def _parse(self, text: str, ticker: str) -> Dict:
        d: Dict[str,Any] = {"ticker": ticker, "_raw": text}
        for k in ("BIAS","SLEEVE_RESEMBLANCE","ACTIONABLE_NOW","TIMEFRAME","REGIME_FIT","ACTION","INPUTS","INVALIDATION","NEXT_SESSION_PLAN","EVENT_RISK","WHY"):
            m = re.search(rf"^{k}:\s*(.+)$", text, re.M|re.I)
            d[k.lower()] = m.group(1).strip() if m else "—"
        m = re.search(r"^QUALITY:\s*(\d+)", text, re.M|re.I)
        d["quality"] = int(m.group(1)) if m else None
        d["evidence"] = [l.lstrip("•·- ").strip() for l in re.findall(r"^[•·\-]\s*(.+)", text, re.M)][:4]
        if "strategy_fit" not in d:
            d["strategy_fit"] = d.get("sleeve_resemblance", "—")
        return d

    @staticmethod
    def _stub(ticker: str, reason: str) -> Dict:
        return {"ticker":ticker,"bias":"—","strategy_fit":"—","sleeve_resemblance":"—","actionable_now":"—",
                "timeframe":"—","regime_fit":"—","quality":None,"why":reason,"evidence":[reason],"invalidation":"—",
                "next_session_plan":"—","event_risk":"—",
                "action":"—","inputs":"—","_ts":time.time(),"_spark":"","_rsi":None,
                "_ema20":None,"_ema50":None,"_macd":"—","_atr":None,"_vol_r":None,
                "_close":None,"_chg":None}


# ══════════════════════════════════════════════════════════════════════════════
# DASHBOARD STATE
# ══════════════════════════════════════════════════════════════════════════════

class State:
    def __init__(self, init_ticker=None):
        self.mode          = M_MONITOR
        self.search_active = False
        self.search_buf    = ""
        self.history: List[str] = []
        self.ticker        = (init_ticker or "").upper() or None
        self.analysis: Optional[Dict] = None
        self.ana_bars: List[Dict] = []
        self.dirty         = True
        self._lock         = threading.Lock()
        # Mode-2 (Research) UI state.
        # research_focus = None means "auto" (alpha when no ticker, analysis when ticker set).
        self.research_focus: Optional[str] = None
        self.alpha_cursor: int = 0
        self._alpha_visible_count: int = 0  # written by Alpha panel each render
        # Show-more level for Alpha Discovery (0=default, 1=more, 2=max).
        # Multiplies the per-section visible cap; bucket/scoring logic unchanged.
        self.alpha_show_more: int = 0
        self.ALPHA_SHOW_MORE_MAX: int = 2
        # Stock Lens on-demand build (Mode 2, "L" key).
        # Holds the ticker currently being built; None when idle.  Display
        # logic in stock_lens_panel checks this so the missing-strip flips
        # to a "building…" line while the daemon thread runs.
        self.lens_pending_ticker: Optional[str] = None
        self.lens_last_error: Optional[str] = None

    def push_history(self, ticker: str):
        t = ticker.upper()
        if t in self.history: self.history.remove(t)
        self.history.insert(0, t)
        self.history = self.history[:6]


# ══════════════════════════════════════════════════════════════════════════════
# PANEL BUILDERS
# ══════════════════════════════════════════════════════════════════════════════

class PB:  # PanelBuilder — all static
    # ── persistent SELECTION EDGE banner (F5) ──────────────────────────────────
    @staticmethod
    def selection_edge_banner(data: DataLayer):
        """One-line, always-on banner separating SYSTEM health from trade EDGE.

        Reads the cache-only Scanner Truth summary (winner_recall_pct vs
        best_simple_baseline_recall_pct) and, when present, the Recall-Repair
        Shadow forward verdict.  It NEVER claims an edge exists — operationally
        clean (SYSTEM OK) must never be misread as a proven selection edge.

        Returns a single-line Rich ``Text`` (no border) so it costs one row in
        every mode's layout.  Cache-only; no provider calls.
        """
        truth = data.get("scanner_truth_summary") or {}
        recall = truth.get("winner_recall_pct")
        baseline = truth.get("best_simple_baseline_recall_pct")
        status = selection_edge_status(recall, baseline)

        t = Text(justify="center")
        t.append(status["line"], style=status["style"])

        # Optional: append the research-only shadow-lane verdict when its
        # forward sidecar exists (Step 2 Task D).  Degrades silently.
        shadow = data.get("recall_repair_shadow_forward") or {}
        if shadow and not shadow.get("_missing"):
            sv = shadow.get("verdict")
            if sv:
                t.append(f"  ·  recall shadow {sv}", style="dim")

        return t

    # ── header bar ────────────────────────────────────────────────────────────
    @staticmethod
    def header(state: State, data: DataLayer, claude: ClaudeAnalyzer) -> Panel:
        session = get_session_state()
        mkt     = _market_status()
        mc      = "bold green" if mkt=="OPEN" else ("yellow" if "PRE" in mkt else "dim")
        mode    = MODE_NAMES[state.mode]
        _, f_ok = data.provider_status()
        fstr  = "[green]●[/]" if f_ok else "[red]●[/]"
        calls = claude.calls(); budget = claude.budget()
        ai_c  = "green" if calls < budget*0.7 else "yellow"
        health, hstyle, hreason = data.system_health()

        # Session badge: colour-coded by execution permission
        if session == SessionState.REGULAR:
            sess_style, sess_lbl = "bold green",  "REGULAR"
        elif session == SessionState.PREMARKET:
            sess_style, sess_lbl = "bold yellow", "PRE-MARKET"
        elif session == SessionState.POSTMARKET:
            sess_style, sess_lbl = "yellow",      "POST-MARKET"
        else:
            sess_style, sess_lbl = "dim",         "CLOSED"

        # Phase 3A: always RESEARCH ONLY — no session-based execution gating needed
        readonly_tag = "  [bold magenta]RESEARCH ONLY[/]"

        srch = ""
        if state.search_active:
            srch = f"  [bold cyan]SEARCH:[/] {state.search_buf}▌"

        t = Text(justify="left")
        t.append("GEM RESEARCH TERMINAL", style="bold cyan")
        t.append(f"  {_now_et().strftime('%H:%M:%S')} ET", style="white")
        # Session state badge — primary execution indicator
        t.append(f"  [{sess_style}][{sess_lbl}][/]", style="")
        t.append(readonly_tag)
        t.append(f"  [{mode}]", style="bold white")
        # System health badge
        t.append(f"  [{hstyle}][SYSTEM {health}][/]", style="")
        if hreason:
            t.append(f" [dim]{hreason}[/]", style="")
        # Provider status (FMP only — Alpaca is a local cache stub)
        t.append(f"  {fstr}FMP", style="")
        t.append(f"  AI:[{ai_c}]{calls}/{budget}[/]", style="")
        t.append(srch)
        t.append("  [dim]1·2·3·4 mode  / search  q quit[/]")
        return Panel(t, box=box.HEAVY, padding=(0,1))

    # ── trade readiness ───────────────────────────────────────────────────────
    @staticmethod
    def trade_readiness(data: DataLayer) -> Panel:
        vix      = data.get("vix")
        regime   = data.get("regime")
        econ     = data.get("econ_cal") or []
        sr       = data.get("scan_results")
        usnap    = data.get("universe_snap")
        forecast = data.get("market_forecast")
        etf      = data.get("etf_quotes")
        spy_b    = data.get("spy_bars")
        mkt      = _market_status()
        status, style, bullets, chip, _reasons = compute_trade_readiness(
            vix, regime, econ, mkt,
            scan_results=sr, universe_snap=usnap,
            forecast=forecast, etf_quotes=etf, spy_bars=spy_b,
        )

        t = Text()
        t.append(f"  {status}  ", style=f"bold reverse {style}")
        for b in bullets:
            t.append(f"  ·  {b}", style="white")
        if chip:
            t.append(f"  [ {chip} ]", style="bold red")
        return Panel(t, title="[bold]TRADE READINESS[/]",
                     border_style=style.replace("bold ","").replace(" on black",""),
                     padding=(0,1))

    # ── regime + sparkline ────────────────────────────────────────────────────
    @staticmethod
    def regime(data: DataLayer) -> Panel:
        bars     = data.get("spy_bars") or []
        regime   = data.get("regime")
        forecast = data.get("market_forecast") or {}
        head     = forecast.get("headline") or {}
        t        = Text()
        if bars:
            closes = [b["close"] for b in bars]
            c, p   = closes[-1], closes[-2] if len(closes)>1 else closes[-1]
            chg    = (c-p)/p*100
            cc     = "green" if chg>=0 else "red"
            t.append(f"SPY ${c:,.2f}  ", style="bold white")
            t.append(f"{chg:+.2f}%\n", style=cc)
            t.append(sparkline(closes)+"\n", style="cyan")
        else:
            t.append("SPY loading…\n", style="dim")

        # Prefer the forecast headline name verbatim so this panel agrees with
        # the TRADE READINESS banner and the MARKET FORECAST strip.  Risk-Off
        # renders red even though its label contains no BULL/BEAR substring.
        head_name = head.get("current_regime")
        if head_name and not forecast.get("_missing"):
            up = str(head_name).upper()
            if "BULL" in up:
                rc = "green"
            elif "BEAR" in up or "RISK-OFF" in up or "STRESS" in up:
                rc = "red"
            else:
                rc = "yellow"
            t.append(str(head_name), style=f"bold {rc}")
            conf = str(head.get("confidence") or "").lower()
            if conf:
                t.append(f"  conf {conf.upper()}", style="dim")
            ts = (regime or {}).get("trend_strength", "")
            if ts:
                t.append(f"  {ts}", style="dim")
        elif regime:
            reg = str(regime.get("regime","—")).upper()
            rc  = "green" if "BULL" in reg else ("red" if "BEAR" in reg else "yellow")
            t.append(reg, style=f"bold {rc}")
            ts  = regime.get("trend_strength","")
            if ts: t.append(f"  {ts}", style="dim")
        elif bars and len(bars)>=20:
            cls = [b["close"] for b in bars]
            e20 = calc_ema(cls,20)
            lbl = "BULL" if cls[-1]>e20 else "BEAR"
            t.append(lbl+" vs EMA20", style="bold green" if lbl=="BULL" else "bold red")
        return Panel(t, title="[bold]SPY / REGIME[/]", border_style="cyan", padding=(0,1))

    # ── market internals ──────────────────────────────────────────────────────
    @staticmethod
    def internals(data: DataLayer) -> Panel:
        etf    = data.get("etf_quotes") or {}
        spy_b  = data.get("spy_bars") or []
        t      = Text()

        # SPY/QQQ/IWM/VXX relative
        for sym, label in [("QQQ","QQQ"),("IWM","IWM"),("VXX","VXX")]:
            q = etf.get(sym)
            if q:
                chg = float(q.get("change_pct",0))
                cc  = "green" if chg>=0 else "red"
                t.append(f"{label} ", style="dim")
                t.append(f"{chg:+.2f}%  ", style=cc)
        t.append("\n")

        # SPY position vs EMAs
        if len(spy_b)>=50:
            cls  = [b["close"] for b in spy_b]
            c    = cls[-1]
            e20  = calc_ema(cls,20); e50 = calc_ema(cls,50)
            a20  = "↑" if c>e20 else "↓"; a50 = "↑" if c>e50 else "↓"
            s20  = "green" if c>e20 else "red"; s50 = "green" if c>e50 else "red"
            t.append("EMA20 ", style="dim"); t.append(f"${e20:,.2f}{a20}  ", style=s20)
            t.append("EMA50 ", style="dim"); t.append(f"${e50:,.2f}{a50}\n", style=s50)

            # Breadth proxy: how many ETFs above their EMA20
            above = sum(1 for q in etf.values() if q)
            t.append(f"ETFs tracked: {len(etf)}  ", style="dim")

        # VXX direction = fear
        vxx = etf.get("VXX")
        if vxx:
            chg = float(vxx.get("change_pct",0))
            lbl = "fear ↑" if chg>1 else ("fear ↓" if chg<-1 else "fear flat")
            cc  = "red" if chg>1 else ("green" if chg<-1 else "dim")
            t.append(lbl, style=cc)

        return Panel(t, title="[bold]MARKET INTERNALS[/]", border_style="blue", padding=(0,1))

    # ── vix + strategy gates ──────────────────────────────────────────────────
    @staticmethod
    def vix_gates(data: DataLayer) -> Panel:
        vix = data.get("vix")
        t   = Text()
        if vix is not None:
            label, lc = _vix_label(vix)
            t.append(f"VIX {vix:.2f}  {_vix_meter(vix)}  ", style="white")
            t.append(f"{label}\n\n", style=lc)
        else:
            t.append("VIX loading…\n\n", style="dim")

        # VIX market stress context — research framing only
        if vix is not None:
            if vix < 15:
                t.append("  complacency zone — options pricing cheap\n", style="green")
            elif vix < 20:
                t.append("  low vol — constructive research environment\n", style="green")
            elif vix < 25:
                t.append("  moderate vol — elevated watchlist caution\n", style="yellow")
            elif vix < 30:
                t.append("  high vol — risk-off, defensive rotation\n", style="red")
            else:
                t.append("  extreme vol — stress regime active\n", style="bold red")
        return Panel(t, title="[bold]VIX & MARKET STRESS[/] [dim]research context[/]", border_style="magenta", padding=(0,1))

    # ── open positions ────────────────────────────────────────────────────────
    @staticmethod
    def positions(data: DataLayer, detailed: bool = False) -> Panel:
        pos = data.get("positions") or []
        tbl = Table(box=box.SIMPLE, show_header=True, header_style="bold dim",
                    expand=True, padding=(0,1))
        cols = ["TICKER","STRAT","SIDE","QTY","ENTRY","NOW","P&L$","P&L%"]
        if detailed:
            cols += ["STOP","TARGET","R","TIME"]
        for c in cols:
            tbl.add_column(c, justify="right" if c not in ("TICKER","STRAT","SIDE") else "left",
                           style="white", width=7 if c in ("P&L$","P&L%","ENTRY","NOW") else None)

        if not pos:
            tbl.add_row("[dim]No open positions[/]", *[""]*(len(cols)-1))
        else:
            for p in pos:
                pnl   = float(p.get("unrealized_pnl",0) or 0)
                pnlp  = float(p.get("pnl_pct",0) or 0)
                pc    = _pnl_style(pnl)
                side  = str(p.get("side","long")).upper()
                sc    = "green" if side=="LONG" else "red"
                r     = p.get("r_mult")
                r_str = f"{r:+.1f}R" if r is not None else "N/A"
                r_col = "green" if r and r>0 else ("red" if r and r<0 else "dim")
                et    = p.get("entry_ts")
                try:
                    elapsed = _utc_now() - _parse_iso_utc(et) if et else None
                    t_str = _fmt_dur(elapsed) if elapsed else "—"
                except Exception:
                    t_str = "—"
                row = [
                    p.get("ticker","—"),
                    _clip(_position_owner_label(p), 10),
                    Text(side[:4], style=sc),
                    f"{abs(float(p.get('qty',0))):.0f}",
                    f"${float(p.get('entry_price',0)):.2f}",
                    f"${float(p.get('current_price',0)):.2f}",
                    Text(f"${pnl:+.0f}", style=pc),
                    Text(f"{pnlp:+.1f}%", style=pc),
                ]
                if detailed:
                    stop    = p.get("stop_loss")
                    tgt     = p.get("target_price")
                    src     = p.get("sl_source", "")
                    na_lbl  = src if src in ("MANUAL", "NOT_SET") else "N/A"
                    row += [
                        f"${float(stop):.2f}" if stop else Text(na_lbl, style="dim"),
                        f"${float(tgt):.2f}"  if tgt  else Text(na_lbl, style="dim"),
                        Text(r_str, style=r_col),
                        t_str,
                    ]
                tbl.add_row(*row)
        return Panel(tbl, title="[bold]OPEN POSITIONS[/] [dim]owner shown per row[/]", border_style="green", padding=(0,0))

    # ── account ───────────────────────────────────────────────────────────────
    @staticmethod
    def account(data: DataLayer) -> Panel:
        a    = data.get("account") or {}
        pos  = data.get("positions") or []
        ps   = data.get("paper_summary") or {}
        t    = Text()
        if not a:
            t.append("Loading…", style="dim")
        else:
            eq   = float(a.get("equity",0) or 0)
            csh  = float(a.get("cash",0) or 0)
            bp   = float(a.get("buying_power",0) or 0)
            dpnl = float(a.get("daily_pnl",0) or 0)
            opnl = sum(float(p.get("unrealized_pnl",0) or 0) for p in pos)
            sleeves = ps.get("sleeves") or {}
            gov = ps.get("governance") or {}
            paper_open = sum(int((sleeves.get(s) or {}).get("open", 0) or 0) for s in ("VOYAGER", "SNIPER", "SHORT"))
            active_live = sum(1 for s in ("VOYAGER", "SNIPER", "SHORT") if int((sleeves.get(s) or {}).get("open", 0) or 0) > 0)
            gov_total = sum(int(gov.get(k, 0) or 0) for k in ("same_ticker", "sector", "regime", "max_position", "duplicate", "frozen", "other"))
            pressure = "high" if gov_total >= 10 else ("moderate" if gov_total >= 3 else "low")
            rows = [("Equity", f"${eq:>12,.2f}", "bold white"),
                    ("Open P&L",f"${opnl:>+12,.0f}", _pnl_style(opnl)),
                    ("Realized",f"${dpnl:>+12,.0f}", _pnl_style(dpnl)),
                    ("Cash",    f"${csh:>12,.2f}",  "white"),
                    ("Buy Pwr", f"${bp:>12,.2f}",   "white"),
                    ("Pos",     f"{len(pos):>12}",   "white"),
                    ("Paper open", f"{paper_open:>10}", "white"),
                    ("Sleeves live", f"{active_live:>9}/3", "white"),
                    ("Gov pressure", f"{pressure:>9}", "yellow" if pressure != "low" else "green")]
            for label, val, style in rows:
                t.append(f"{label:<10}", style="dim")
                t.append(f"{val}\n",    style=style)
        return Panel(t, title="[bold]ACCOUNT[/] [bold magenta]RESEARCH ONLY[/]",
                     border_style="green", padding=(0,1))

    # ── paper validation evidence ────────────────────────────────────────────
    @staticmethod
    def paper_evidence(data: DataLayer) -> Panel:
        ps = data.get("paper_summary") or {}
        ev = data.get("evidence_status") or {}
        sleeves = ps.get("sleeves") or {}

        tbl = Table(box=box.SIMPLE, show_header=True, header_style="bold dim",
                    expand=True, padding=(0,1))
        for c in ("SLEEVE", "RAW", "EFF", "OPEN", "CLSD", "GOV", "OBS", "STATUS"):
            tbl.add_column(c, justify="right" if c != "SLEEVE" else "left")
        labels = {"VOYAGER": "VOYAGER", "SNIPER": "SNIPER_V6", "SHORT": "SHORT_A"}
        for strat in active_paper_strategies():
            row = sleeves.get(strat) or {}
            raw = int(row.get("raw", 0) or 0)
            eff = int(row.get("effective", 0) or 0)
            blocked = int(row.get("blocked", 0) or 0)
            observe = int(row.get("observe", 0) or 0)
            usable = int(row.get("open", 0) or 0) + int(row.get("closed", 0) or 0)
            block_rate = int(round((blocked / raw) * 100)) if raw else 0
            if raw == 0:
                status = "no flow"
            elif usable == 0 and blocked > 0:
                status = f"blocked {block_rate}%"
            elif eff <= 2:
                status = "early evidence"
            elif blocked >= usable:
                status = f"pressure {block_rate}%"
            else:
                status = f"usable {usable}/{raw}"
            tbl.add_row(
                labels.get(strat, strat),
                str(raw),
                str(eff),
                str(row.get("open", 0)),
                str(row.get("closed", 0)),
                str(blocked),
                str(observe),
                status,
            )

        last_ok = ev.get("last_success_at") or ev.get("finished_at") or "—"
        score_ts = ev.get("scoreboard_mtime") or "—"
        subtitle = f"[dim]resolver {_age_from_iso_short(last_ok)} ago · scoreboard {_age_from_iso_short(score_ts)} ago[/]"
        return Panel(tbl, title="[bold]PAPER EVIDENCE[/] [dim]active sleeves only[/]",
                     subtitle=subtitle, border_style="green", padding=(0,0))

    @staticmethod
    def paper_readiness(data: DataLayer) -> Panel:
        ps = data.get("paper_summary") or {}
        readiness = ps.get("readiness") or {}
        t = Text()

        # Decisive status phrasing per sleeve — the count tells the user
        # whether evidence is accumulating, sufficient, or absent at a glance.
        sn = readiness.get("SNIPER") or {}
        sn_n = int(sn.get("signals") or 0)
        sn_done = int(sn.get("completed") or 0)
        t.append("SNIPER_V6  ", style="bold white")
        t.append(f"{sn_n}/30  ", style="green" if sn_n >= 30 else "yellow")
        if sn_n <= 0:
            t.append("no flow\n", style="dim")
        elif sn_done <= 0:
            t.append("evidence accumulating · awaiting closes\n", style="dim")
        else:
            t.append(
                f"10d WR {sn.get('wr', 0):.1f}%  avg {sn.get('avg_adj', 0):+.2f}%  stop {sn.get('stop', 0):.1f}%\n",
                style="white",
            )

        sh = readiness.get("SHORT") or {}
        sh_n = int(sh.get("signals") or 0)
        sh_done = int(sh.get("completed") or 0)
        t.append("SHORT_A    ", style="bold white")
        # F4: a frozen sleeve must never render an active "/30" evidence target
        # that implies it should still be producing flow.  Read the registry as
        # the single source of truth (display-only; no logic change).
        if is_frozen_strategy("SHORT"):
            t.append("FROZEN", style="bold dim")
            t.append(" research-only (no new signals)\n", style="dim")
        else:
            t.append(f"{sh_n}/30  ", style="green" if sh_n >= 30 else "yellow")
            if sh_n <= 0:
                t.append("no flow\n", style="dim")
            elif sh_done <= 0:
                t.append("evidence accumulating · awaiting closes\n", style="dim")
            else:
                t.append(
                    f"3d avg {sh.get('avg_adj', 0):+.2f}%  stop {sh.get('stop', 0):.1f}%\n",
                    style="white",
                )

        vo = readiness.get("VOYAGER") or {}
        vo_done = int(vo.get("completed") or 0)
        vo_n    = int(vo.get("signals") or 0)
        t.append("VOYAGER    ", style="bold white")
        t.append(f"{vo_done}/30  ", style="green" if vo_done >= 30 else "yellow")
        if vo_n <= 0:
            t.append("no windows logged\n", style="dim")
        elif vo_done <= 0:
            t.append("waiting for 30d windows\n", style="dim")
        else:
            ma = vo.get("ma200_hold")
            ma_s = f"{ma:.1f}%" if ma is not None else "N/A"
            t.append(f"30d avg {vo.get('avg_30d', 0):+.2f}%  MA200 {ma_s}\n", style="white")

        return Panel(t, title="[bold]PAPER READINESS[/] [dim]promotion progress[/]",
                     border_style="green", padding=(0,1))

    @staticmethod
    def governance_blocks(data: DataLayer) -> Panel:
        ps = data.get("paper_summary") or {}
        gov = ps.get("governance") or {}
        rows = [
            ("same ticker", gov.get("same_ticker", 0)),
            ("sector", gov.get("sector", 0)),
            ("regime cluster", gov.get("regime", 0)),
            ("max position", gov.get("max_position", 0)),
            ("duplicate/open", gov.get("duplicate", 0)),
            ("frozen sleeve", gov.get("frozen", 0)),
            ("other", gov.get("other", 0)),
        ]
        t = Text()
        total = sum(int(count or 0) for _, count in rows)
        dominant_label, dominant_count = max(rows, key=lambda item: int(item[1] or 0))
        pressure = "high" if total >= 10 else ("moderate" if total >= 3 else "low")
        # Conclusion (action hint) is purely interpretive — it does not
        # change governance behavior, only summarises the existing counts.
        if total == 0:
            action = "no issue"
            action_style = "green"
        elif dominant_label in {"max position", "duplicate/open"}:
            action = "hold limits"
            action_style = "yellow"
        elif dominant_label in {"sector", "regime cluster"}:
            action = "investigate concentration"
            action_style = "yellow"
        elif dominant_label == "frozen sleeve":
            action = "investigate freeze"
            action_style = "yellow"
        elif pressure == "high":
            action = "investigate"
            action_style = "yellow"
        else:
            action = "hold limits"
            action_style = "dim"

        t.append("pressure        ", style="dim")
        t.append(f"{pressure}\n", style="yellow" if pressure != "low" else "green")
        if dominant_count:
            t.append("main issue      ", style="dim")
            t.append(f"{dominant_label} ({int(dominant_count)})\n", style="yellow")
        else:
            t.append("main issue      ", style="dim")
            t.append("none\n", style="dim")
        t.append("action          ", style="dim")
        t.append(f"{action}\n", style=action_style)
        t.append("\n")
        for label, count in rows:
            style = "yellow" if int(count or 0) else "dim"
            t.append(f"{label:<16}", style="dim")
            t.append(f"{int(count or 0):>3}\n", style=style)
        return Panel(t, title="[bold]GOVERNANCE BLOCKS[/]",
                     border_style="yellow", padding=(0,1))

    # ── Phase 4 research engine panels ────────────────────────────────────────

    @staticmethod
    def market_heartbeat(data: DataLayer) -> Panel:
        """Phase 4A — Market Heartbeat panel. Cache-only read of heartbeat sidecar."""
        hb = data.get("market_heartbeat") or {}
        t = Text()
        if hb.get("_missing"):
            t.append("No heartbeat artifact.\n", style="dim")
            t.append("Run: ./scripts/run_research_cycle.sh market-heartbeat\n", style="dim")
            return Panel(t, title="[bold]MARKET HEARTBEAT[/] [dim]RESEARCH ONLY[/]",
                         border_style="magenta", padding=(0, 1))

        label = str(hb.get("heartbeat_label") or "UNKNOWN")
        label_style = {
            "RISK_ON": "bold green",
            "TECH_LED": "bold green",
            "SMALL_CAP_LED": "bold green",
            "HEALTHY_PULLBACK": "bold yellow",
            "CHOP": "yellow",
            "CORRECTION": "bold red",
            "RISK_OFF": "bold red",
            "DEFENSIVE_ROTATION": "bold yellow",
        }.get(label, "white")

        t.append(f"{label}\n", style=label_style)
        for reason in (hb.get("heartbeat_reasons") or [])[:3]:
            t.append(f"  · {reason}\n", style="dim")

        vix = hb.get("vix")
        t.append(f"\nVIX  ", style="dim")
        t.append(f"{vix:.1f}\n" if vix is not None else "N/A\n", style="white")

        etfs = hb.get("etf_trends") or {}
        for sym in ("SPY", "QQQ", "IWM", "SMH"):
            info = etfs.get(sym) or {}
            trend = str(info.get("trend") or "—")
            r20 = info.get("r20_pct")
            r20_str = f"{r20:+.1f}%" if r20 is not None else "—"
            t_style = "green" if "UPTREND" in trend else ("red" if "DOWNTREND" in trend else "yellow")
            t.append(f"{sym:<5}", style="dim")
            t.append(f"{trend:<18}", style=t_style)
            t.append(f" {r20_str:>7}/20d\n", style="dim")

        rs = hb.get("risk_signal") or {}
        sig = str(rs.get("signal") or "—")
        sig_style = "green" if sig == "RISK_ON" else ("red" if sig == "RISK_OFF" else "yellow")
        t.append(f"\nRisk  ", style="dim")
        t.append(f"{sig}\n", style=sig_style)

        age = hb.get("_age_short") or "?"
        return Panel(t, title="[bold]MARKET HEARTBEAT[/] [dim]RESEARCH ONLY[/]",
                     subtitle=f"[dim]{age} ago[/]",
                     border_style="magenta", padding=(0, 1))

    @staticmethod
    def research_watchlist(data: DataLayer) -> Panel:
        """Phase 4B/4C — Research Watchlist panel. Cache-only read of scanner sidecar."""
        sc = data.get("research_scanner") or {}
        t = Text()
        if sc.get("_missing"):
            t.append("No scanner artifact.\n", style="dim")
            t.append("Run: ./scripts/run_research_cycle.sh research-scanner\n", style="dim")
            return Panel(t, title="[bold]DAILY RESEARCH WATCHLIST[/] [dim]RESEARCH ONLY[/]",
                         border_style="magenta", padding=(0, 1))

        label_style_map = {
            "EARLY_ACCUMULATION": "cyan",
            "BEATEN_DOWN": "yellow",
            "SECTOR_LEADER": "green",
            "CATALYST": "bold yellow",
            "SOCIAL_ARB": "magenta",
            "ASYMMETRIC_RECOVERY_WATCH": "bold magenta",
            "TRUE_10X_RESEARCH": "bold magenta",
            "EXTENDED": "dim yellow",
            "RISKY": "red",
            "CROWDED": "bold red",
            "WATCH": "white",
            "RESEARCH": "bold white",
            "AVOID": "dim",
            "NO_SOCIAL_DATA": "dim",
        }
        t.append(f"{'TICKER':<7}{'LABEL':<22}{'SCORE':>5}  CATEGORY\n", style="bold dim")
        for item in (sc.get("watchlist") or [])[:12]:
            lbl = str(item.get("watchlist_label") or "WATCH")
            ticker = str(item.get("ticker") or "?")
            score = item.get("research_score") or 0
            cat = str(item.get("category") or "")[:16]
            lbl_s = label_style_map.get(lbl, "white")
            t.append(f"{ticker:<7}", style="bold white")
            t.append(f"{lbl:<22}", style=lbl_s)
            t.append(f"{score:>5.1f}  {cat}\n", style="dim")

        summary = sc.get("label_summary") or {}
        top_labels = sorted(summary.items(), key=lambda x: -x[1])[:4]
        t.append("\n")
        for lbl, cnt in top_labels:
            lbl_s = label_style_map.get(lbl, "white")
            t.append(f"{lbl:<22}", style=lbl_s)
            t.append(f"{cnt:>3}\n", style="dim")

        age = sc.get("_age_short") or "?"
        size = sc.get("watchlist_size") or 0
        return Panel(t, title=f"[bold]DAILY RESEARCH WATCHLIST[/] [dim]({size} results)[/]",
                     subtitle=f"[dim]{age} ago  ·  RESEARCH ONLY — no trade recommendations[/]",
                     border_style="magenta", padding=(0, 1))

    @staticmethod
    def evidence_freshness(data: DataLayer) -> Panel:
        ev = data.get("evidence_status") or {}
        snap = data.get("universe_snap") or {}
        sr = data.get("scan_results") or {}
        t = Text()

        def age_from_iso(value: str) -> str:
            if not value:
                return "N/A"
            try:
                dt = _parse_iso_utc(value)
                return _fmt_dur(_utc_now() - dt)
            except Exception:
                return "N/A"

        # ── daily bars (price cache) — resolved from the actual SPY parquet,
        #    not the freshness-gated universe snapshot.  Explicit status+reason.
        pcm = data.get("price_cache_meta") or {}
        pstatus = pcm.get("status") or "unknown"
        if pstatus == "current":
            bars_txt, bars_style = f"current · latest {pcm.get('latest_bar')}", "white"
        elif pstatus == "stale":
            bars_txt = (f"stale {pcm.get('trading_days_behind', '?')}d · latest "
                        f"{pcm.get('latest_bar')} · expected {pcm.get('expected')}")
            bars_style = "yellow"
        elif pstatus == "missing":
            bars_txt, bars_style = f"missing · {pcm.get('source')}", "yellow"
        else:
            bars_txt = f"unknown · {pcm.get('reason') or 'no metadata'}"
            bars_style = "yellow"
        t.append(f"daily bars    {bars_txt}\n", style=bars_style)

        # ── universe age — from the snapshot mtime/generated_at + count, NO 2h
        #    discard; a valid weekend snapshot resolves with an explicit age.
        um = data.get("universe_meta") or {}
        ustatus = um.get("status") or "unknown"
        if ustatus == "missing":
            t.append(f"universe      missing · {um.get('source')}\n", style="yellow")
        elif ustatus == "unknown":
            t.append(f"universe      unknown · {um.get('reason') or 'no metadata'}\n",
                     style="yellow")
        else:
            cnt = um.get("count")
            cnt_s = f"{cnt} tickers" if cnt is not None else "count n/a"
            fb = " · FALLBACK" if um.get("fallback_used") else ""
            tail = "  (stale)" if ustatus == "stale" else ""
            t.append(f"universe      {_ef_fmt_age(um.get('age_seconds'))} · {cnt_s} · "
                     f"snapshot{fb}{tail}\n",
                     style="white" if ustatus == "current" else "yellow")

        # ── research artifacts (decision-useful, current) — explicitly sourced.
        al = data.get("alpha_discovery") or {}
        st = data.get("scanner_truth_summary") or {}
        rs = data.get("recall_repair_shadow_forward") or {}
        al_age = al.get("_age_short") or "N/A"
        st_age = _ef_fmt_age(st.get("_age_seconds")) if not st.get("_missing") else "N/A"
        recall = st.get("winner_recall_pct")
        t.append(f"alpha board   {al_age}    scanner truth {st_age}"
                 f"{f' · recall {recall}%' if recall is not None else ''}\n", style="white")
        if rs and not rs.get("_missing"):
            t.append(f"recall shadow {_ef_fmt_age(rs.get('_age_seconds'))} · "
                     f"{rs.get('verdict') or '—'}\n", style="white")

        # ── legacy daemon scan loop — honestly labelled; the daemon scan_results
        #    cycle is NOT the research pipeline above and is often days stale.
        scan_ts = str(sr.get("last_cycle_ts") or "")
        scan_age = age_from_iso(scan_ts)
        scan_stale = False
        try:
            if scan_ts:
                scan_stale = (_utc_now() - _parse_iso_utc(scan_ts)).total_seconds() > 36 * 3600
        except Exception:
            pass
        t.append(f"legacy scanner {scan_age}{' stale' if scan_stale else ''}\n",
                 style="yellow" if (scan_stale or scan_age == 'N/A') else "white",)

        # ── premarket research cycle (forecast + alpha) ──
        fc = data.get("market_forecast") or {}
        fc_age = fc.get("_age_short") or "N/A"
        pm_ok = (not fc.get("_missing") and not fc.get("_stale")
                 and fc_age != "N/A" and al_age != "N/A")
        t.append(f"premarket     fc {fc_age}  alpha {al_age}\n",
                 style="white" if pm_ok else "yellow")

        return Panel(t, title="[bold]RESEARCH DATA FRESHNESS[/]",
                     border_style="cyan", padding=(0,1))

    # ── Phase 2B.1 MCP audit summary strip ────────────────────────────────────
    @staticmethod
    def mcp_audit_summary(data: DataLayer) -> Panel:
        """Compact cache-only summary of the latest MCP audit session.

        Reads cache/research/mcp_analysis_latest.json (written by
        research/mcp_audit_orchestrator.py). The dashboard never calls
        MCP tools, providers, or Claude — degrades cleanly when the
        sidecar is missing.
        """
        sess = data.get("mcp_audit_session") or {}
        t = Text()
        if sess.get("_missing"):
            t.append("no sidecar yet — run `./scripts/run_research_cycle.sh mcp-audit-session`",
                     style="dim")
            return Panel(t, title="[bold]MCP AUDIT SUMMARY[/]",
                         border_style="magenta", padding=(0, 1))

        fresh = sess.get("_freshness") or {}
        stale_sidecar = bool(fresh.get("stale"))
        stale_reasons = list(fresh.get("stale_reasons") or [])
        age = sess.get("_age_short") or "?"

        # Phase 2B.2: if the sidecar itself is stale (age, session change,
        # or older than the latest regime forecast), do NOT display the
        # cached state/keyword/concerns as if current — they would mislead
        # the operator. Surface the rerun command instead.
        if stale_sidecar:
            # F1: distinguish a benign session change (e.g. a weekday 'regular'
            # snapshot viewed on a CLOSED weekend session — a rerun does NOT
            # help) from a genuinely actionable stale (aged out / superseded by
            # a newer forecast).  Misleading "rerun required" wording was the
            # audit finding.
            kind = mcp_stale_kind(stale_reasons)
            if kind == "benign_session":
                t.append("MCP AUDIT — prior-session snapshot (state hidden)\n",
                         style="bold dim")
                t.append("Not an error: ", style="dim")
                t.append(
                    f"snapshot was taken in session '{sess.get('session', '—')}'; "
                    f"current session differs (e.g. weekend/CLOSED). Rerun only when "
                    f"a live session resumes.\n",
                    style="dim")
            else:
                t.append("MCP AUDIT STALE — rerun required\n", style="bold yellow")
                if stale_reasons:
                    t.append("Why stale: ", style="dim")
                    t.append(_clip(", ".join(stale_reasons), 120), style="white")
                    t.append("\n")
                t.append("Rerun: ", style="bold dim")
                t.append("./scripts/run_research_cycle.sh mcp-audit-session regular",
                         style="white")
                t.append("\n", style="dim")
            t.append(f"sidecar age {age} · session={sess.get('session', '—')} "
                     f"(prior state hidden until refresh)",
                     style="dim")
            return Panel(t, title="[bold]MCP AUDIT SUMMARY[/]",
                         border_style=("magenta" if kind == "benign_session" else "yellow"),
                         padding=(0, 1))

        state   = str(sess.get("state") or "UNKNOWN")
        inputs  = sess.get("state_inputs") or {}
        counts  = sess.get("counts") or {}
        verdict = ((sess.get("system_health_audit") or {}).get("verdict") or {})
        hyg = (((sess.get("daily_dashboard_audit") or {}).get("paper_hygiene") or {})
               .get("hygiene") or {})
        ready_clean = (hyg.get("summary") or {}).get("ready_to_gate_clean")
        ready_clean_s = "—" if ready_clean is None else ("READY" if ready_clean else "NOT READY")

        active = "yes" if (verdict.get("active_drift") or inputs.get("active_drift")) else "no"
        unknown = "yes" if (verdict.get("unknown_drift") or inputs.get("unknown_drift")) else "no"

        # Header line: state + clean + drift + late + missing-lens counts.
        state_style = {
            "BLOCKED":    "red",
            "STALE":      "yellow",
            "FRAGILE":    "yellow",
            "CONFLICTED": "yellow",
            "NORMAL":     "green",
        }.get(state, "white")
        t.append(f"state {state}", style=state_style)
        t.append("  ", style="dim")
        t.append(
            f"clean_epoch {ready_clean_s}  drift {active}  unknown_drift {unknown}\n",
            style=("green" if ready_clean and active == "no" else "yellow"),
        )

        # Second line: bucket counts.
        t.append(
            f"late_chase ext={counts.get('extended', 0)}  "
            f"watch={counts.get('watch_reclaim', 0)}  "
            f"blocked={counts.get('blocked', 0)}  "
            f"missing_lens={counts.get('missing_lens', 0)}  "
            f"drilldowns={counts.get('drilldowns', 0)}\n",
            style="white",
        )

        # Action line — deterministic verb phrase produced by the
        # orchestrator from the drilldown action_labels. Field is absent
        # in pre-Phase-2B.1-follow-up sidecars, so degrade gracefully.
        action = sess.get("recommended_action")
        if action:
            t.append(f"Action: {_clip(str(action), 100)}\n", style="bold")

        # Up to two concerns — quoted verbatim from the summary so the
        # dashboard never invents wording.
        concerns = sess.get("top_concerns") or []
        for c in concerns[:2]:
            t.append(f"• {_clip(str(c), 90)}\n", style="white")

        # Single-line operator brief — strips the long executive summary
        # to the first sentence to keep the panel compact.
        brief = str(sess.get("executive_summary") or "").split(". ")[0]
        if brief:
            if not brief.endswith("."):
                brief += "."
            t.append(f"{_clip(brief, 100)}\n", style="dim")

        # Phase 1G.9: one-line RS/Theme → Lens/Gatekeeper triage strip (cache-only
        # read of the orchestrator payload; degrades silently when absent).
        rtt = sess.get("rs_theme_triage")
        if rtt:
            t.append(
                f"RS/Theme Triage: watch={rtt.get('research_watch', 0)} · "
                f"needsLens={rtt.get('needs_lens', 0)} · "
                f"extended={rtt.get('too_extended', 0)} ({rtt.get('verdict', '—')})\n",
                style="dim")

        # Phase 1G.11: one-line RS/Theme forward-validation strip (cache-only read
        # of the orchestrator payload; degrades silently when absent).
        rtf = sess.get("rs_theme_forward")
        if rtf:
            t.append(
                f"RS/Theme Fwd: matured={rtf.get('matured', 0)} · "
                f"verdict={rtf.get('verdict', '—')}\n",
                style="dim")

        # Phase 1G.15: one-line Social Attention Radar strip (cache-only read of
        # the orchestrator payload; research-only, degrades silently when absent).
        sa = sess.get("social_attention")
        if sa:
            t.append(
                f"Social Attention: social-led={sa.get('social_led', 0)} · "
                f"early={sa.get('early', 0)} · crowded={sa.get('crowded', 0)} "
                f"({sa.get('forward_verdict', '—')})\n",
                style="dim")

        # Phase 1G.16: one-line Short Detection strip (cache-only read of the
        # orchestrator payload; research-only, degrades silently when absent).
        # Distinct from the frozen SHORT_A sleeve — this is downside *detection*.
        sdet = (sess.get("phase_1g3") or {}).get("short_detection")
        if sdet and sdet.get("proposed_state"):
            st = sdet.get("proposed_state")
            qd = sdet.get("qqq_drawdown_10d_pct")
            qd_s = f"{qd:+.1f}%" if isinstance(qd, (int, float)) else "—"
            t.append(
                f"Short Detection: {st} · QQQ {qd_s} · "
                f"{'missed breakdown' if sdet.get('missed_breakdown') else 'no miss'} · "
                f"no active short sleeve\n",
                style="dim")

        slab = sess.get("strategy_lab")
        if slab:
            paper = "no paper active" if not slab.get("paper_active") else "paper active"
            mode = slab.get("mode", "-")
            exact = "no(sampled)" if slab.get("sampled") else mode
            t.append(
                f"Strategy Lab: exact={exact} · best={slab.get('best_variant', '-')} · "
                f"verdict={slab.get('verdict', '-')} · {paper}",
                style="dim")
            t.append("\n", style="dim")

        t.append(f"sidecar age {age}  · session={sess.get('session', 'regular')}",
                 style="dim")
        return Panel(t, title="[bold]MCP AUDIT SUMMARY[/]",
                     border_style="magenta", padding=(0, 1))

    # ── Phase 2B.1 follow-up: Mode-2 one-line MCP audit strip ─────────────────
    @staticmethod
    def mcp_audit_oneline(data: DataLayer) -> Panel:
        """One-line Mode-2 Research strip mirroring the Risk-mode summary.

        Format:
          MCP AUDIT: <STATE> · clean <READY|NOT READY> · late chase <N>
                     · blocked <N> · <action_keyword>

        Cache-only read of cache/research/mcp_analysis_latest.json. Same
        contract as the Risk panel: never invokes MCP / providers /
        Claude; degrades to a hint line when the sidecar is missing.
        """
        sess = data.get("mcp_audit_session") or {}
        t = Text()
        if sess.get("_missing"):
            t.append(
                "MCP AUDIT: no sidecar  · run `./scripts/run_research_cycle.sh mcp-audit-session`",
                style="dim",
            )
            return Panel(t, box=box.SIMPLE, padding=(0, 1))

        # Phase 2B.2: if the sidecar is stale (age / session change /
        # superseded by a newer regime forecast), do not display the
        # cached state / keyword as if it were current.
        fresh = sess.get("_freshness") or {}
        if fresh.get("stale"):
            kind = mcp_stale_kind(fresh.get("stale_reasons"))
            t.append("MCP AUDIT: ", style="bold")
            if kind == "benign_session":
                # Benign weekend/session mismatch — not an error, no rerun fix.
                t.append("prior session", style="bold dim")
                t.append(" · snapshot from a different session (state hidden)",
                         style="dim")
            else:
                t.append("STALE", style="bold yellow")
                t.append(
                    " · rerun `./scripts/run_research_cycle.sh mcp-audit-session regular`",
                    style="dim",
                )
            return Panel(t, box=box.SIMPLE, padding=(0, 1))

        state = str(sess.get("state") or "UNKNOWN")
        counts = sess.get("counts") or {}
        hyg = (((sess.get("daily_dashboard_audit") or {}).get("paper_hygiene") or {})
               .get("hygiene") or {})
        ready_clean = (hyg.get("summary") or {}).get("ready_to_gate_clean")
        ready_clean_s = "—" if ready_clean is None else ("READY" if ready_clean else "NOT READY")
        keyword = sess.get("action_keyword") or "observe"

        state_style = {
            "BLOCKED":    "red",
            "STALE":      "yellow",
            "FRAGILE":    "yellow",
            "CONFLICTED": "yellow",
            "NORMAL":     "green",
        }.get(state, "white")

        t.append("MCP AUDIT: ", style="bold")
        t.append(state, style=state_style)
        t.append(
            f" · clean {ready_clean_s}"
            f" · late chase {counts.get('extended', 0)}"
            f" · blocked {counts.get('blocked', 0)}"
            f" · {keyword}",
            style="white",
        )
        return Panel(t, box=box.SIMPLE, padding=(0, 1))

    # ── Phase 1B/1D/1E risk telemetry strip ───────────────────────────────────
    @staticmethod
    def risk_telemetry(data: DataLayer) -> Panel:
        """Compact one-panel summary of the Phase 1B sidecars + Phase
        1D/1E clean-epoch & broker-snapshot visibility.

        Reads cache/research/{slippage_telemetry, portfolio_concentration,
        shadow_sizing, paper_state_hygiene}_latest.json and
        cache/state/broker_positions_snapshot.json. Cache-only — never
        runs the reports nor calls Alpaca. Missing sidecars degrade to
        ``no data`` lines so the panel is safe on a fresh DB.
        """
        slip  = data.get("slippage_telemetry") or {}
        conc  = data.get("portfolio_concentration") or {}
        shad  = data.get("shadow_sizing") or {}
        hyg   = data.get("paper_state_hygiene") or {}
        brok  = data.get("broker_snapshot") or {}
        t = Text()

        def _bps(v):
            return "n/a" if v is None else f"{v:+.1f}bps"

        # ── slippage row ──
        if slip.get("_missing"):
            t.append("slippage     no sidecar yet\n", style="dim")
        else:
            ov = slip.get("overall_adverse_bps") or {}
            s = slip.get("summary") or {}
            warns = len(slip.get("warnings") or [])
            n_filled = ov.get("n") or 0
            line = (
                f"slippage     n={n_filled}  med={_bps(ov.get('median_bps'))}  "
                f"p90={_bps(ov.get('p90_bps'))}"
            )
            style = "yellow" if warns else "white"
            t.append(line + "\n", style=style)
            fr = s.get("fill_rate")
            fr_s = "n/a" if fr is None else f"{fr*100:.1f}%"
            t.append(
                f"             fill_rate={fr_s}  partial={s.get('partial_fills',0)}  "
                f"warn={warns}\n",
                style="dim",
            )

        # ── concentration row ──
        if conc.get("_missing"):
            t.append("concentr     no sidecar yet\n", style="dim")
        else:
            cs = conc.get("summary") or {}
            warns = len(conc.get("warnings") or [])
            top1 = cs.get("top1_gross_pct")
            top3 = cs.get("top3_gross_pct")
            line = (
                f"concentr     pos={cs.get('n_positions',0)}  "
                f"top1={'n/a' if top1 is None else f'{top1:.1f}%'}  "
                f"top3={'n/a' if top3 is None else f'{top3:.1f}%'}"
            )
            style = "yellow" if warns else "white"
            t.append(line + "\n", style=style)
            corr = conc.get("correlation") or {}
            n_clusters = len(corr.get("clusters") or [])
            t.append(
                f"             gross%eq={cs.get('gross_pct_equity') or 'n/a'}  "
                f"clusters={n_clusters}  warn={warns}\n",
                style="dim",
            )

        # ── shadow sizing row ──
        if shad.get("_missing"):
            t.append("shadow       no sidecar yet\n", style="dim")
        else:
            ss = shad.get("summary") or {}
            warns = len(shad.get("warnings") or [])
            line = (
                f"shadow       ≥2x={ss.get('open_size_ge_2x_shadow',0)}  "
                f"≤0.4x={ss.get('open_size_le_04x_shadow',0)}  "
                f"no_atr={ss.get('open_no_atr_cache',0)}"
            )
            style = "yellow" if warns else "white"
            t.append(line + "\n", style=style)
            drag = ss.get("open_short_borrow_drag_dollar") or 0
            adj = ss.get("closed_short_median_borrow_adjusted_pct")
            t.append(
                f"             borrow_drag=${drag:,.2f}  "
                f"closed_adj_med={'n/a' if adj is None else f'{adj:+.2f}%'}  "
                f"warn={warns}\n",
                style="dim",
            )

        # ── Phase 1D clean-epoch row ──
        # Compact one-liner: clean-epoch verdict is the operationally
        # important bit; full counts live in the JSON sidecar.
        if hyg.get("_missing"):
            t.append("clean epoch  no sidecar yet\n", style="dim")
        else:
            h_sum = hyg.get("summary") or {}
            r_all   = h_sum.get("ready_to_gate_all")
            r_clean = h_sum.get("ready_to_gate_clean")
            lq = hyg.get("legacy_quarantine") or {}
            r_all_s = "—" if r_all is None else ("Y" if r_all else "N")
            r_clean_s = "—" if r_clean is None else ("Y" if r_clean else "N")
            t.append(
                f"clean epoch  all={r_all_s}  clean={r_clean_s}  "
                f"qrt={lq.get('count', 0)}\n",
                style=("green" if r_clean else "yellow"),
            )

        # ── Phase 1E broker-snapshot row ──
        if brok.get("_missing"):
            t.append("broker snap  no snapshot yet", style="dim")
        else:
            bcount = brok.get("count")
            age = brok.get("_age_short") or "?"
            bmc = ((hyg.get("legacy_quarantine") or {})
                   .get("broker_match_counts") or {})
            if bmc:
                t.append(
                    f"broker snap  pos={bcount} {age}  "
                    f"m={bmc.get('match', 0)}/"
                    f"n={bmc.get('no_broker_position', 0)}/"
                    f"c={bmc.get('closed_by_book', 0)}/"
                    f"u={bmc.get('unknown', 0)}",
                    style="white",
                )
            else:
                t.append(
                    f"broker snap  pos={bcount}  age={age}",
                    style="white",
                )

        # ── Phase 1G.5 Scanner Truth one-liner (cache-only) ──
        # Refreshed nightly (run_research_cycle.sh nightly → scanner_truth_review).
        # The sidecar is a frozen autopsy, not a live read, so surface its age and
        # flag STALE when the nightly refresh has not landed (>72h tolerates the
        # weekend gap) — otherwise a 10-day-old number reads as current telemetry.
        st = data.get("scanner_truth_summary") or {}
        if st and not st.get("_missing"):
            recall = st.get("winner_recall_pct")
            base = st.get("best_simple_baseline_recall_pct")
            age_short = st.get("_age_short") or "?"
            stale = (st.get("_age_seconds") or 0) > 72 * 3600
            tag = ", STALE" if stale else ""
            if stale:
                st_style = "yellow"
            elif recall is not None and recall < 10:
                st_style = "red"
            else:
                st_style = "white"
            t.append(
                f"\nscanner truth  recall={recall}%  miss={st.get('main_failure', '?')}"
                f"  simpleRS={base}%  ({age_short}{tag})",
                style=st_style,
            )

        # ── Phase 1G.12 Options Regime one-liner (cache-only, diagnostic) ──
        org = data.get("options_regime") or {}
        if org and not org.get("_missing"):
            mk = org.get("market") or {}
            regime = mk.get("market_options_regime") or "NOT_ENOUGH_DATA"
            if not org.get("feed_configured"):
                t.append("\nOptions Regime: feed not configured", style="dim")
            else:
                age = org.get("_age_short") or "?"
                skew_s = (mk.get("skew_state") or "—")
                # compact skew/iv/gamma tags for the example format
                skew_tag = ("put" if skew_s == "PUT_HEDGE_DEMAND"
                            else "call" if skew_s == "CALL_CHASE"
                            else "bal" if skew_s == "BALANCED" else "—")
                iv_tag = {"LOW_IV": "low", "NORMAL_IV": "normal",
                          "HIGH_IV": "high", "EXTREME_IV": "extreme",
                          "NOT_ENOUGH_HISTORY": "n/h"}.get(mk.get("iv_state"), "—")
                gm = mk.get("gamma_regime") or ""
                gamma_tag = ("pos" if gm == "positive_gamma_regime"
                             else "weak/neg" if gm == "negative_gamma_regime"
                             else "neutral" if gm == "neutral" else "—")
                risk = mk.get("risk_warning_level") or "LOW"
                style = ("red" if risk == "HIGH"
                         else "yellow" if risk == "MEDIUM" else "white")
                t.append(
                    f"\nOptions Regime: {regime} · skew={skew_tag} · "
                    f"IV={iv_tag} · gamma={gamma_tag}  ({age})",
                    style=style,
                )

        # ── Phase 1G.17 PARTICIPATION one-liner (cache-only) ──
        part = participation_status(data.get("participation_audit"))
        t.append(
            f"\nPARTICIPATION: state={part['state']}  "
            f"last_decision={part['last_decision'] or 'n/a'}  "
            f"sniper_flow={part['sniper_flow'] if part['sniper_flow'] is not None else '?'}  "
            f"voyager_flow={part['voyager_flow'] if part['voyager_flow'] is not None else '?'}  "
            f"reason={part['reason']}",
            style=part["style"],
        )

        return Panel(t, title="[bold]RISK TELEMETRY (Phase 1B / 1D / 1E)[/]",
                     border_style="yellow", padding=(0,1))

    # ── macro countdown ───────────────────────────────────────────────────────
    @staticmethod
    def macro(data: DataLayer) -> Panel:
        econ = data.get("econ_cal") or []
        t    = Text()
        now  = _now_et()
        hi   = [e for e in econ if str(e.get("impact","")).lower()=="high"]
        hi.sort(key=lambda x: x.get("date",""))
        shown = 0
        for e in hi[:8]:
            raw = str(e.get("date","")).replace("Z","")
            try:
                edt = datetime.fromisoformat(raw)
                # FMP economic-calendar timestamps are UTC (verified against
                # FOMC Minutes which FMP publishes as 18:00:00 = 14:00 ET).
                # Previously stamped as ET, which shifted every event by
                # 4-5 hours and made already-fired events look future-bound.
                if edt.tzinfo is None: edt = edt.replace(tzinfo=timezone.utc)
                mins = (edt-now).total_seconds()/60
                if mins < -60: continue  # old event
                # Render in ET so the date/time matches the operator's clock.
                edt_local = edt.astimezone(now.tzinfo) if now.tzinfo else edt
                if edt_local.date() == now.date():
                    when_str = f"TODAY {edt_local.strftime('%H:%M')} ET"
                else:
                    when_str = edt_local.strftime('%a %m-%d %H:%M ET')
                if mins <= 45:
                    countdown_str   = f"{when_str}  in {int(max(mins,0))}m ⚠"
                    countdown_style = "bold red"
                    bullet_style    = "bold red"
                elif mins <= 120:
                    countdown_str   = f"{when_str}  in {int(mins/60)}h{int(mins%60):02d}m"
                    countdown_style = "yellow"
                    bullet_style    = "yellow"
                else:
                    countdown_str   = when_str
                    countdown_style = "dim"
                    bullet_style    = "red"
                # Country chip prevents the "same event name, different
                # country" confusion (e.g. Canada's CPI today vs UK's CPI
                # tomorrow both render as "Inflation Rate YoY (Apr)").
                # US rows render full-intensity; non-US rows are dimmed so
                # the operator's eye lands on the events that actually
                # drive SPY without losing global context entirely.
                ctry = str(e.get("country") or "??").upper()[:2]
                is_us = (ctry == "US")
                if is_us:
                    ctry_style    = "bold cyan"
                    event_style   = "white"
                    row_bullet    = bullet_style
                    row_countdown = countdown_style
                else:
                    ctry_style    = "dim cyan"
                    event_style   = "dim"
                    row_bullet    = "dim"
                    # Preserve the urgency tier (in-XXm ⚠ / yellow) only
                    # when the event is imminent — otherwise dim the
                    # whole row including the countdown.
                    row_countdown = countdown_style if mins <= 120 else "dim"
                t.append("● ", style=row_bullet)
                t.append(f"{ctry:<2} ", style=ctry_style)
                t.append(f"{_clip(str(e.get('event','')),31):<31}  ", style=event_style)
                t.append(countdown_str + "\n", style=row_countdown)
                shown += 1
            except Exception:
                continue
        if not shown:
            t.append("No HIGH-impact macro events in next 14d", style="dim")
        return Panel(t, title="[bold]MACRO COUNTDOWN[/]", border_style="red", padding=(0,1))

    # ── catalyst news ─────────────────────────────────────────────────────────
    @staticmethod
    def catalyst_news(data: DataLayer, ticker: Optional[str]=None) -> Panel:
        raw   = data.get("news_market") or []
        items = _filter_catalyst_news(raw)
        pos   = data.get("positions") or []
        pos_tickers = {p["ticker"] for p in pos}
        t     = Text()
        if not items:
            t.append("No catalyst news detected", style="dim")
        else:
            for item in items[:5]:
                title  = _clip(str(item.get("title","")), 72)
                src    = _clip(str(item.get("publisher") or item.get("site","")), 14)
                sym    = str(item.get("symbol",""))
                flag   = " ⚠" if sym in pos_tickers else ""
                t.append(f"[dim]{src:<14}[/]  {title}{flag}\n", style="")
        return Panel(t, title="[bold]CATALYST NEWS[/]", border_style="white", padding=(0,1))

    # ── discretionary research assist ────────────────────────────────────────
    @staticmethod
    def research_assist(
        data: DataLayer,
        bte: Optional[Any] = None,
        alpha_symbols: Optional[set] = None,
    ) -> Panel:
        """
        Manual research aid only. These rows are not approved paper signals and
        do not feed paper evidence.

        Rendering is compact:
          - bias / why-now / playbook / risk / freshness header (5 lines)
          - Manual Focus Now (3 buckets, max 2 rows each)
          - Top Liquid (3 rows, anchors)
          - Movers (5 rows, LIQ vs SPC tag preserved)

        Optional cross-reference: rows whose symbol also appears on the Alpha
        Discovery board are tagged with " *A".
        """
        snap = data.get("universe_snap") or {}
        cands = snap.get("strategy_candidates") or []
        by_symbol: Dict[str, Dict[str, Any]] = {}
        for c in cands:
            sym = str(c.get("symbol") or "").upper()
            if not sym:
                continue
            prev = by_symbol.get(sym)
            if prev is None or float(c.get("base_score") or 0) > float(prev.get("base_score") or 0):
                by_symbol[sym] = c

        rows = list(by_symbol.values())
        liquid = sorted(
            rows,
            key=lambda c: (
                -float(c.get("avg_dollar_volume_20") or 0),
                -float(c.get("current_dollar_volume") or 0),
                str(c.get("symbol") or ""),
            ),
        )[:5]
        trending = sorted(
            rows,
            key=lambda c: (
                -(
                    abs(float(c.get("return_5d_pct") or 0)) * 0.6
                    + abs(float(c.get("return_20d_pct") or 0)) * 0.4
                ) * max(float(c.get("volume_ratio_5d") or 0), 0.1),
                -float(c.get("avg_dollar_volume_20") or 0),
                str(c.get("symbol") or ""),
            ),
        )[:10]

        a_set: set = alpha_symbols or set()

        def _xref_mark(sym: str) -> str:
            return " *A" if sym.upper() in a_set else "   "

        vix = data.get("vix")
        regime = data.get("regime") or {}
        t = Text()
        t.append("DISCRETIONARY / MANUAL RESEARCH ONLY\n", style="bold yellow")

        if bte is None:
            bte = build_research_bte(universe_snapshot=snap, regime=regime, vix=vix)
        bte_style = {
            "bullish": "green",
            "defensive": "yellow",
            "mixed": "yellow",
        }.get(bte.bias, "yellow")
        # Posture "spread" is the long/short pressure imbalance in the current
        # candidate snapshot — it is *not* the regime forecaster's confidence
        # (which measures probability margin between top-2 regimes).  The
        # label is explicit so the operator doesn't conflate the two.
        t.append(
            f"Market Posture  bias {bte.bias.upper()} · spread {bte.confidence.upper()}\n",
            style=bte_style,
        )
        # Surface divergence when posture spread reads strong but the
        # forecaster's regime confidence is low — same direction can be
        # high-spread / low-conviction.  Cache-only read.
        try:
            mf = data.get("market_forecast") or {}
            head = mf.get("headline") or {}
            fc_conf = str(head.get("confidence") or "").lower()
            posture_conf = str(bte.confidence or "").lower()
            breached = bool(head.get("invalidation_breached"))
            posture_bias = str(bte.bias or "").lower()
            _rank = {"low": 0, "low-medium": 1, "medium": 2, "high": 3}

            # Phase 1F Task 2: hard REGIME CONFLICT badge when posture
            # reads bullish but the forecast is breached or LOW-conf,
            # or visibly tilts risk-off. Stronger than the previous
            # subtler "spread is candidate-pressure only" hint — this
            # one explicitly tells the operator the regime is not
            # confirmed, so candidate language must not imply approval.
            probs = mf.get("regime_probabilities") or []
            risk_off_p = 0.0
            for r in probs:
                if str(r.get("regime") or "").strip().lower() == "risk-off":
                    risk_off_p = float(r.get("probability") or 0)
                    break
            posture_bullish_unconfirmed = (
                posture_bias == "bullish"
                and not mf.get("_missing")
                and (breached or fc_conf == "low" or risk_off_p >= 0.40)
            )
            if posture_bullish_unconfirmed:
                t.append(
                    "  ⚠ REGIME CONFLICT — bullish tape pressure, but "
                    "regime not confirmed\n",
                    style="bold white on red",
                )
            elif (not mf.get("_missing")
                    and fc_conf in _rank and posture_conf in _rank
                    and _rank[posture_conf] - _rank[fc_conf] >= 2):
                t.append(
                    f"  ⚠ regime conf {fc_conf.upper()} · posture spread is candidate-pressure only\n",
                    style="bold yellow",
                )
        except Exception:
            pass
        if bte.factors:
            t.append("Why now   ", style="bold dim")
            t.append(" | ".join(bte.factors[:4]) + "\n", style="dim")
        if bte.playbook:
            t.append("Playbook  ", style="bold dim")
            t.append(" | ".join(bte.playbook[:3]) + "\n", style="white")
        risk_style = "yellow" if (bte.risk_flag and bte.risk_flag != "none") else "dim"
        t.append("Risk      ", style="bold dim")
        t.append(
            f"{bte.risk_flag or 'none'}   "
            f"snap {int(snap.get('_file_age_seconds') or 0)//60}m old\n",
            style=risk_style,
        )
        t.append("advisory only · research context\n", style="dim")
        if a_set:
            t.append("*A = also on Alpha Discovery board\n\n", style="dim")
        else:
            t.append("\n", style="dim")

        focus = bte.focus_names[:5]
        # Phase 1F: tag renamed from "aligned now" → "research-aligned".
        # Accept both so cached BTE snapshots from prior builds still
        # bucket correctly.
        _aligned_tags = {"research-aligned", "aligned now"}
        ready_now = [
            r for r in focus
            if str(r.get("actionable_now") or "").upper() == "YES"
            and str(r.get("compliance_tag") or "") in _aligned_tags
        ]
        pullback_watch = [
            r for r in focus
            if str(r.get("status") or "").lower() in {
                "pullback watch", "watch pullback", "watch"
            }
            or str(r.get("compliance_tag") or "") in {
                "pullback watch", "early setup", "wait for confirmation",
                "not actionable yet",
            }
        ]
        extended_leaders = [
            r for r in focus
            if str(r.get("status") or "").lower() in {"extended", "late / extended"}
            or str(r.get("compliance_tag") or "") == "extended"
        ]
        any_focus = bool(ready_now or pullback_watch or extended_leaders)

        # Movers — combined trending list, LIQ vs SPC tag preserved.
        movers: List[Dict[str, Any]] = []
        for r in trending:
            adv = float(r.get("avg_dollar_volume_20") or 0)
            r["_mover_type"] = "LIQ" if adv >= 100_000_000 else "SPC"
            movers.append(r)
        movers = movers[:5]

        # Compact mode: when Market Posture has no focus, no liquid anchors, and no movers,
        # collapse to a single advisory line so Alpha gets the vertical space.
        if not any_focus and not liquid and not movers:
            t.append("Market Posture summary only · no candidate names from current snapshot\n", style="dim")
            return Panel(t, title="[bold]RESEARCH ASSIST[/]",
                         border_style="blue", padding=(0, 1))

        # Phase 1F Tasks 3+5: status is now run through
        # neutralize_research_label so:
        #   - "BUY candidate" / legacy strings are translated, and
        #   - any candidate label is demoted to "Research Only · entry
        #     not actionable" when the lens entry layer is Too Extended /
        #     Broken / Avoid (the operator must not see green "candidate"
        #     wording against a non-actionable setup).
        from core.fragility import neutralize_research_label  # local import
        def _entry_label(row: Dict[str, Any]) -> str:
            # Prefer an explicit entry_layer value if the BTE row carries
            # one; fall back to the lens cache for the same ticker.
            ev = (row.get("entry_layer") or row.get("entry_view") or
                  row.get("entry") or "")
            if ev:
                return str(ev)
            sym = str(row.get("symbol") or "").upper()
            if not sym:
                return ""
            try:
                ld = (data.get(f"stock_lens:{sym}") or {})
                return str(((ld.get("layers") or {}).get("entry") or {}).get("view") or "")
            except Exception:
                return ""

        def _render_focus_bucket(title: str, rows: List[Dict[str, Any]]) -> None:
            if not rows:
                return  # collapse empty buckets entirely
            t.append(f"{title}\n", style="bold dim")
            for row in rows[:2]:
                sym = str(row.get('symbol') or '—')
                raw_status = str(row.get("status") or "WATCH")
                entry_view = _entry_label(row)
                rendered_status = neutralize_research_label(
                    raw_status, entry_label=entry_view,
                )
                tag = str(row.get("compliance_tag") or "watch only")
                t.append(
                    f"  {sym:<5}{_xref_mark(sym)} "
                    f"{str(row.get('sleeve_resemblance') or '—')[:14]:<14} "
                    f"{rendered_status[:26]:<26} "
                    f"{tag[:16]}\n",
                    style="white",
                )

        if any_focus:
            t.append("Research Focus\n", style="bold dim")
            _render_focus_bucket("Research-aligned (not approval)", ready_now)
            _render_focus_bucket("Watch Pullback", pullback_watch)
            _render_focus_bucket("Late / Extended", extended_leaders)

        # Top Liquid — context anchors only, render only what exists.
        if liquid:
            t.append("\nTop Liquid", style="bold dim")
            t.append(" (20d avg $vol)\n", style="dim")
            for i, l in enumerate(liquid[:3], 1):
                l_sym = str(l.get("symbol") or "—")[:5]
                l_val = float(l.get("avg_dollar_volume_20") or 0) / 1_000_000
                t.append(
                    f" {i}. {l_sym:<5}{_xref_mark(l_sym)} ${l_val:>6.0f}M\n",
                    style="white",
                )

        if not movers:
            return Panel(t, title="[bold]RESEARCH ASSIST[/]",
                         border_style="blue", padding=(0, 1))

        t.append("\nMovers", style="bold dim")
        t.append(" (LIQ ≥$100M ADV · SPC research-only)\n", style="dim")
        for i, g in enumerate(movers, 1):
            g_sym = str(g.get("symbol") or "—")[:5]
            g_r5 = float(g.get("return_5d_pct") or 0)
            g_r20 = float(g.get("return_20d_pct") or 0)
            g_rv = float(g.get("volume_ratio_5d") or 0)
            tag_color = "white" if g.get("_mover_type") == "LIQ" else "cyan"
            t.append(
                f" {i}. {g_sym:<5}{_xref_mark(g_sym)} "
                f"{g_r5:+4.1f}/{g_r20:+4.1f}  {g_rv:>3.1f}x  {g.get('_mover_type')}\n",
                style=tag_color,
            )

        return Panel(t, title="[bold]RESEARCH ASSIST[/]",
                     border_style="blue", padding=(0,1))

    @staticmethod
    def social_arb_radar(
        data: DataLayer,
        alpha_symbols: Optional[set] = None,
        bte_focus_symbols: Optional[set] = None,
        scanner_symbols: Optional[set] = None,
        compact: bool = False,
        max_lines: int = 3,
    ) -> Panel:
        board = data.get("social_arb") or {}
        items = list(board.get("items") or [])
        a_set = alpha_symbols or set()
        b_set = bte_focus_symbols or set()
        s_set = scanner_symbols or set()

        def _bucket_code(value: Any) -> str:
            raw = str(value or "")
            return {
                "Cross-Confirmed Lead": "XCONF",
                "News Catalyst": "NEWS",
                "Emerging Theme": "THEME",
                "Options/Tape Confirmed": "TAPE",
                "Watch Only / Needs Verification": "WATCH",
            }.get(raw, raw[:5].upper() or "WATCH")

        def _markers(row: Dict[str, Any]) -> str:
            sym = str(row.get("ticker") or "").upper()
            marks = set(str(x) for x in (row.get("cross_refs") or []) if x)
            if sym in a_set:
                marks.add("ALPHA+")
            if sym in b_set:
                marks.add("POSTURE+")
            if sym in s_set:
                marks.add("SCANNER+")
            ordered = [m for m in ("ALPHA+", "POSTURE+", "SCANNER+", "TRENDS+") if m in marks]
            return " ".join(ordered) if ordered else "—"

        t = Text()
        if compact:
            age = board.get("_age_short") or _age_from_iso_short(board.get("built_at") or board.get("_mtime_iso"))
            dropped = (board.get("dropped_noise") or {}).get("total", 0)
            status_tags = []
            if board.get("sample_data"):
                status_tags.append("SAMPLE")
            if board.get("source_errors"):
                status_tags.append("FALLBACK")
            suffix = f" [{' '.join(status_tags)}]" if status_tags else ""
            line_cap = max(1, min(5, int(max_lines or 1)))
            # Prefix width: sym(6)+sp+bucket(5)+sp+conf(4)+/+noise(3)+sp+marker(30)+sp = 53
            # Marker bumped to 30 to fit "ALPHA+ POSTURE+ SCANNER+ TRENDS+" (31).
            # Reserve a bit for panel borders/padding + possible "+N" suffix.
            term_cols = shutil.get_terminal_size((140, 24)).columns
            label_width = max(58, term_cols - 53 - 10)
            if not board:
                t.append("No Social Arb artifact · run after market close", style="dim")
            elif not items:
                t.append(
                    f"No high-quality social/news arb leads this run · age {age} · dropped {dropped}{suffix}",
                    style="dim",
                )
            else:
                for idx, row in enumerate(items[:line_cap], 1):
                    sym = str(row.get("ticker") or "—").upper()[:6]
                    bucket = _bucket_code(row.get("bucket"))
                    conf = str(row.get("confidence") or "—")[:4]
                    noise = str(row.get("noise_risk") or "—")[:3]
                    marker = _markers(row)
                    label = _clip(str(row.get("news_label") or row.get("theme") or "—"), label_width)
                    more = f" +{len(items) - line_cap}" if idx == line_cap and len(items) > line_cap else ""
                    style = "green" if bucket in {"XCONF", "TAPE"} else "white"
                    if noise.upper().startswith("HIG"):
                        style = "yellow"
                    t.append(
                        f"{sym:<6} {bucket:<5} {conf:<4}/{noise:<3} {marker:<30} {label}{more}{suffix}",
                        style=style,
                    )
                    if idx < min(line_cap, len(items)):
                        t.append("\n")
            return Panel(
                t,
                title="[bold]SOCIAL ARB RADAR[/] [dim]research-only · cache only[/]",
                border_style="cyan",
                padding=(0, 1),
            )

        t.append("research-only · twice-weekly\n", style="bold yellow")
        if not board:
            t.append("\nNo Social Arb artifact loaded\n", style="dim")
            t.append("Run research/social_arb_radar.py after market close.\n", style="dim")
            return Panel(
                t,
                title="[bold]SOCIAL ARB RADAR[/] [dim]cache only[/]",
                border_style="cyan",
                padding=(0, 1),
            )

        age = board.get("_age_short") or _age_from_iso_short(board.get("built_at") or board.get("_mtime_iso"))
        dropped = (board.get("dropped_noise") or {}).get("total", 0)
        raw_count = board.get("raw_item_count", 0)
        t.append(f"artifact age {age}  raw {raw_count}  dropped/noise {dropped}\n", style="dim")
        if board.get("sample_data"):
            t.append("offline sample artifact · rerun real twice-weekly radar for live research\n", style="yellow")
        if board.get("source_errors"):
            t.append("missing-source fallback active\n", style="yellow")

        if not items:
            t.append("\nNo high-quality social/news arb leads this run.\n", style="dim")
            return Panel(
                t,
                title="[bold]SOCIAL ARB RADAR[/] [dim]cache only[/]",
                border_style="cyan",
                padding=(0, 1),
            )

        for row in items[:8]:
            sym = str(row.get("ticker") or "—").upper()[:6]
            bucket = _bucket_code(row.get("bucket"))
            conf = str(row.get("confidence") or "—")[:6]
            noise = str(row.get("noise_risk") or "—")[:6]
            source_type = str(row.get("source_type") or "—")[:18]
            marker = _markers(row)
            style = "green" if bucket in {"XCONF", "TAPE"} else "white"
            if noise.upper().startswith("HIGH"):
                style = "yellow"
            t.append(
                f"{sym:<6} {bucket:<5} conf {conf:<6} noise {noise:<6} {marker}\n",
                style=style,
            )
            label = _clip(str(row.get("news_label") or row.get("theme") or "—"), 74)
            why = _clip(str(row.get("why_it_matters") or "—"), 54)
            check = _clip(str(row.get("manual_check_needed") or "—"), 58)
            t.append(f"  src {source_type:<18} {label}\n", style="white")
            t.append(f"  why {why}  check {check}\n", style="dim")

        return Panel(
            t,
            title="[bold]SOCIAL ARB RADAR[/] [dim]cache only[/]",
            border_style="cyan",
            padding=(0, 1),
        )

    @staticmethod
    def alpha_discovery(
        data: DataLayer,
        state: Optional["State"] = None,
        expanded: bool = False,
        bte_focus_symbols: Optional[set] = None,
        show_more_level: int = 0,
        compact_detail: bool = False,
    ) -> Panel:
        overlay = data.get("alpha_discovery_overlay") or {}
        board = data.get("alpha_discovery") or {}
        b_set: set = bte_focus_symbols or set()
        show_more_level = max(0, int(show_more_level or 0))
        session = get_session_state()
        nightly_items = list(board.get("items") or [])

        def _dt_from_any(value: Any) -> Optional[datetime]:
            if not value or str(value) == "—":
                return None
            try:
                return _parse_iso_utc(value)
            except Exception:
                return None

        def _age_hours(value: Any) -> Optional[float]:
            dt = _dt_from_any(value)
            if dt is None:
                return None
            return max(0.0, (_utc_now() - dt).total_seconds() / 3600.0)

        nightly_stamp = board.get("built_at") or board.get("_mtime_iso")
        overlay_stamp = overlay.get("built_at") or overlay.get("_mtime_iso")
        nightly_age_h = _age_hours(nightly_stamp)
        overlay_age_h = _age_hours(overlay_stamp)
        nightly_ok = bool(board.get("items"))
        overlay_ok = bool(overlay.get("items"))
        nightly_fresh = nightly_ok and (nightly_age_h is not None and nightly_age_h <= 36.0)
        overlay_fresh = overlay_ok and (overlay_age_h is not None and overlay_age_h <= 10.0)

        source = board
        source_label = "NIGHTLY"
        source_desc = "canonical nightly"
        fallback_note = ""
        if session == SessionState.PREMARKET:
            if overlay_fresh:
                source = overlay
                source_label = "PREMARKET"
                source_desc = "morning overlay"
            elif nightly_ok:
                source = board
                source_label = "PREMARKET"
                source_desc = "fallback nightly"
                fallback_note = "premarket overlay missing/stale; falling back to nightly"
        elif session == SessionState.REGULAR:
            if overlay_fresh:
                source = overlay
                source_label = "INTRADAY REFERENCE"
                source_desc = "latest premarket overlay"
            elif nightly_fresh:
                source = board
                source_label = "INTRADAY REFERENCE"
                source_desc = "today's nightly research"
                fallback_note = "premarket overlay missing/stale; using fresh nightly research"
            elif overlay_ok:
                source = overlay
                source_label = "INTRADAY REFERENCE"
                source_desc = "stale premarket overlay"
                fallback_note = "intraday reference only; overlay is stale for current session"
            elif nightly_ok:
                source = board
                source_label = "INTRADAY REFERENCE"
                source_desc = "stale nightly"
                fallback_note = "no fresh research artifact available; nightly is stale"
        else:
            if nightly_ok:
                source = board
                source_label = "NIGHTLY"
                source_desc = "canonical nightly"
                if not nightly_fresh:
                    fallback_note = "nightly board is stale; review freshness before relying on it"
            elif overlay_ok:
                source = overlay
                source_label = "NIGHTLY"
                source_desc = "fallback overlay"
                fallback_note = "nightly board missing; using last available overlay"

        items = list(source.get("items") or [])
        mode = str(source.get("mode") or "nightly")

        # F2: surface an explicit STALE banner when the *chosen* source is more
        # than a day old, so a multi-day-old watchlist (e.g. a weekend-served
        # premarket overlay) is never presented as a current opportunity set.
        source_age_h = overlay_age_h if source is overlay else nightly_age_h
        source_is_stale = source_age_h is not None and source_age_h > _ALPHA_STALE_BANNER_H

        t = Text()
        if compact_detail:
            t.append("manual research board\n", style="dim")
        else:
            t.append("RESEARCH-ONLY ALPHA BOARD\n", style="bold yellow")
            t.append("pre-sleeve discovery · manual research only\n", style="dim")
        if source_is_stale:
            t.append(
                f"⚠ STALE BOARD — {source_desc} built {source_age_h/24:.1f}d ago; "
                f"names may be outdated, do not treat as current\n",
                style="bold red",
            )
        if not items:
            t.append("\nNo Alpha Discovery artifact loaded\n", style="dim")
            t.append("Run nightly build first; premarket overlay is optional.\n", style="dim")
            return Panel(
                t,
                title="[bold]ALPHA DISCOVERY[/] [dim]research candidates[/]",
                border_style="magenta",
                padding=(0, 1),
            )

        tiers = source.get("tier_counts") or {}
        tracks = source.get("track_counts") or {}
        buckets = source.get("bucket_counts") or {}
        age = source.get("_age_short") or _age_from_iso_short(source.get("built_at") or source.get("_mtime_iso"))
        sectors = ", ".join((source.get("dominant_sectors") or [])[:3]) or "Unknown"
        caution = "tier-C heavy" if int(tiers.get("C", 0)) >= max(3, int(len(items) * 0.5)) else "mixed quality"
        if compact_detail:
            t.append(
                f"{source_label}  A:{tiers.get('A',0)} B:{tiers.get('B',0)} C:{tiers.get('C',0)}  "
                f"LLR:{tracks.get('Liquid Leadership Reset',0)} EMG:{tracks.get('Emerging Opportunity',0)}  "
                f"age {age}  sectors {sectors}\n",
                style="white",
            )
            if fallback_note:
                t.append(f"{fallback_note}\n", style="yellow")
            elif session == SessionState.REGULAR:
                t.append("intraday reference only · not refreshed for execution use\n", style="yellow")
        else:
            t.append(
                f"{source_label}  "
                f"{source_desc}  "
                f"tiers A:{tiers.get('A',0)} B:{tiers.get('B',0)} C:{tiers.get('C',0)}  "
                f"age {age}\n",
                style="white",
            )
            t.append(
                f"tracks LLR:{tracks.get('Liquid Leadership Reset',0)} "
                f"EMG:{tracks.get('Emerging Opportunity',0)}  "
                f"sectors {sectors}\n",
                style="dim",
            )
            if source.get("built_at") or source.get("_mtime_iso"):
                t.append(
                    f"source {source_desc}  built {str(source.get('built_at') or source.get('_mtime_iso'))[:19]}\n",
                    style="dim",
                )
            if session == SessionState.REGULAR:
                t.append("research reference only · intraday not refreshed for execution use\n", style="yellow")
            if fallback_note:
                t.append(f"{fallback_note}\n", style="yellow")
            t.append(f"caution {caution}\n", style="yellow" if caution != "mixed quality" else "dim")
        more_tag = (
            f"  ·  show:{show_more_level}+ (m/+ more, - less)"
            if show_more_level > 0
            else "  ·  m/+ show more"
        )
        # Cross-reference overlap count: alpha rows whose ticker is also in Market Posture focus.
        item_symbols = {str(row.get("ticker") or "").upper() for row in items if row.get("ticker")}
        overlap_count = len(b_set & item_symbols) if b_set else 0
        if b_set:
            overlap_tag = (
                f"  ·  POSTURE+ALPHA agree on {overlap_count}"
                if overlap_count else "  ·  no Posture/Alpha overlap"
            )
            if compact_detail:
                t.append(f"*P = Posture focus{overlap_tag}  ·  j/k row{more_tag}\n", style="dim")
            else:
                t.append(f"*P = also in Posture focus{overlap_tag}  ·  j/k move cursor{more_tag}\n", style="dim")
        else:
            t.append(f"j/k row  ·  detail follows selection{more_tag}\n", style="dim")
        if not compact_detail:
            t.append(
                "codes: BN buyable · WR watch reclaim · PF pullback · "
                "WO watch · EXT extended · LATE late · BRK broken\n",
                style="dim",
            )

        bucket_key = "overlay_bucket" if mode == "premarket_overlay" else "bucket"
        status_key = "overlay_status" if mode == "premarket_overlay" else None
        reason_key = "overlay_reason" if mode == "premarket_overlay" else None

        def _track_tag(name: str) -> str:
            return "LLR" if name == "Liquid Leadership Reset" else "EMG" if name == "Emerging Opportunity" else name[:3].upper()

        tier_rank = {"A": 0, "B": 1, "C": 2}
        bucket_rank = {
            "Buyable Now": 0,
            "Buyable Pullback": 0,
            "Sponsor Confirmation": 1,
            "Pullback Watch": 1,
            "Early Discovery": 2,
            "Too Late / Crowded": 3,
        }

        def _display_bucket(row: Dict[str, Any]) -> str:
            raw_bucket = str(row.get(bucket_key) or "")
            if mode == "premarket_overlay":
                return raw_bucket
            if str(raw_bucket) == "Buyable Pullback" and bool(row.get("actionable_now")):
                return "Top Discovery Now"
            if raw_bucket in {"Buyable Pullback", "Sponsor Confirmation", "Early Discovery"}:
                return "Watch / Stalk"
            return "Too Late / Crowded"

        def _display_actionable(row: Dict[str, Any]) -> bool:
            if mode == "premarket_overlay":
                return str(row.get("overlay_bucket") or "") == "Buyable Now"
            return bool(row.get("actionable_now"))

        def _rank_tuple(row: Dict[str, Any]) -> Tuple[int, int, float, int, str]:
            return (
                0 if _display_actionable(row) else 1,
                bucket_rank.get(str(row.get(bucket_key) or ""), 9),
                tier_rank.get(str(row.get("data_tier") or "C"), 3),
                -float(row.get("alpha_score") or 0.0),
                str(row.get("ticker") or ""),
            )

        ranked_items = sorted(items, key=_rank_tuple)
        nightly_by_ticker = {
            str(row.get("ticker") or "").upper(): row
            for row in nightly_items
            if row.get("ticker")
        }

        def _balanced_select(rows: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
            if len(rows) <= limit:
                return rows
            by_track: Dict[str, List[Dict[str, Any]]] = {"Liquid Leadership Reset": [], "Emerging Opportunity": []}
            other: List[Dict[str, Any]] = []
            for row in rows:
                track = str(row.get("track") or "")
                if track in by_track:
                    by_track[track].append(row)
                else:
                    other.append(row)
            selected: List[Dict[str, Any]] = []
            while len(selected) < limit and any(by_track.values()):
                for track in ("Emerging Opportunity", "Liquid Leadership Reset"):
                    if by_track[track] and len(selected) < limit:
                        selected.append(by_track[track].pop(0))
            remainder = by_track["Emerging Opportunity"] + by_track["Liquid Leadership Reset"] + other
            if len(selected) < limit:
                selected.extend(remainder[: max(0, limit - len(selected))])
            return selected[:limit]

        # Per-section visible caps.  show_more_level expands the caps without
        # changing scoring, ranking, or bucket assignment — strictly a UI
        # affordance for inspecting more rows.  Defaults sized so the natural
        # NOW/WATCH/LATE total reaches the 10-row board ceiling enforced below.
        base_limit  = (8 if expanded else 7) if compact_detail else (8 if expanded else 7)
        extra       = show_more_level * (2 if compact_detail else 4)
        late_limit  = (2 if compact_detail else 2) + show_more_level * (1 if compact_detail else 2)
        now_limit   = base_limit + extra
        watch_limit = base_limit + (2 if expanded else 0) + extra
        # Global ceiling — board renders at most 10 ranked rows by default
        # (most-buyable NOW first, then WATCH, then LATE), expanded by
        # show_more_level.  Pre-cap section limits prevent any single bucket
        # from monopolising the board before global truncation.
        total_visible_cap = 10 + show_more_level * 6

        now_rows = [row for row in ranked_items if _display_bucket(row) in {"Buyable Now", "Top Discovery Now"}]
        watch_rows = [row for row in ranked_items if _display_bucket(row) in {"Pullback Watch", "Watch / Stalk", "Early Discovery"}]
        crowded_rows = [row for row in ranked_items if _display_bucket(row) == "Too Late / Crowded"]

        # Empty Top Discovery Now banner: NOW section has zero buyable rows,
        # so the table will only carry watch/stalk and late candidates.  This
        # is honest framing — not a strategy claim.
        if not now_rows and not compact_detail:
            t.append(
                "No true daily buyable setups now — "
                "showing best watch/stalk candidates.\n",
                style="yellow",
            )

        now_rows = _balanced_select(now_rows, now_limit)
        watch_rows = _balanced_select(watch_rows, watch_limit)
        crowded_rows = _balanced_select(crowded_rows, late_limit)

        renderables: List[Any] = [t]

        def _transition_text(row: Dict[str, Any]) -> str:
            tier = str(row.get("data_tier") or "C")
            nightly_row = nightly_by_ticker.get(str(row.get("ticker") or "").upper(), {})
            nightly_tier = str(nightly_row.get("data_tier") or tier)
            nightly_bucket = str(nightly_row.get("bucket") or "—")
            current_bucket = str(row.get("overlay_bucket") or row.get("bucket") or "—")
            if mode == "premarket_overlay":
                if current_bucket == "Buyable Now":
                    move = "held" if nightly_bucket == "Buyable Pullback" else "improved"
                elif current_bucket in {"Pullback Watch", "Too Late / Crowded"}:
                    move = "deteriorated"
                else:
                    move = "watch"
            else:
                move = "nightly"
            return f"{nightly_tier} -> {current_bucket} | {move}"

        def _risk_text(row: Dict[str, Any]) -> str:
            if status_key and row.get(status_key):
                return _clip(str(row.get(reason_key) or row.get(status_key) or "—"), 110 if expanded else 96)
            return _clip(str(row.get("main_risk") or "—"), 110 if expanded else 96)

        sections = [
            ("NOW", now_rows, buckets.get("Buyable Now", buckets.get("Buyable Pullback", len(now_rows)))),
            ("WATCH", watch_rows, buckets.get("Pullback Watch", 0) + buckets.get("Sponsor Confirmation", 0) + buckets.get("Early Discovery", 0)),
            ("LATE", crowded_rows, buckets.get("Too Late / Crowded", len(crowded_rows))),
        ]

        visible_rows: List[Tuple[str, Dict[str, Any]]] = []
        for label, rows, _count in sections:
            for row in rows:
                visible_rows.append((label, row))
        # Apply the global most-buyable-first ceiling.  Sections were appended
        # in NOW → WATCH → LATE order, so a head-slice keeps highest-ranked
        # buyable rows first and trims the tail (typically LATE/crowded).
        visible_rows = visible_rows[:total_visible_cap]

        # Cursor-driven selection.
        cursor_idx = 0
        if state is not None:
            state._alpha_visible_count = len(visible_rows)
            if visible_rows:
                cursor_idx = max(0, min(state.alpha_cursor, len(visible_rows) - 1))
                state.alpha_cursor = cursor_idx
            else:
                state.alpha_cursor = 0

        selected_section = visible_rows[cursor_idx][0] if visible_rows else "NOW"
        selected_row = visible_rows[cursor_idx][1] if visible_rows else None

        def _short_state_code(label: str) -> str:
            s = (label or "").upper().strip()
            mapping = {
                "BUYABLE NOW": "BN",
                "BUYABLE PULLBACK": "BN",
                "WATCH RECLAIM": "WR",
                "PULLBACK FORMING": "PF",
                "PULLBACK WATCH": "PF",
                "WATCH ONLY": "WO",
                "WATCH": "WO",
                "TOO EXTENDED": "EXT",
                "EXTENDED": "EXT",
                "TOO LATE": "LATE",
                "TOO LATE / CROWDED": "LATE",
                "BROKEN / AVOID": "BRK",
                "BROKEN": "BRK",
            }
            return mapping.get(s, s[:4] if s else "—")

        left = Table(
            box=box.SIMPLE_HEAVY,
            expand=True,
            show_header=True,
            header_style="bold",
            pad_edge=False,
            collapse_padding=True,
        )
        left.add_column("", width=1, no_wrap=True)  # cursor caret column
        left.add_column("GRP", width=5, no_wrap=True)
        left.add_column("TK", width=8, no_wrap=True)
        left.add_column("TRK", width=3, no_wrap=True)
        left.add_column("T", width=1, no_wrap=True)
        left.add_column("S", width=5, justify="right", no_wrap=True)
        left.add_column("ST", width=4, no_wrap=True)
        # Thesis column: ellipsis (single-line) keeps rows 1 line tall so the
        # board stays scannable.  Full thesis is shown in the detail strip
        # below the table when the user moves the cursor onto a row.
        left.add_column("THESIS", ratio=1, overflow="ellipsis", no_wrap=True)

        # Tighter thesis clip than before — the table column can render the
        # tail with "…" via overflow="ellipsis" if the terminal is narrow,
        # but we still pre-clip to keep the cell short and avoid wide
        # rebalancing of the other ratio columns.
        thesis_clip = 56 if expanded else 44

        if visible_rows:
            for idx, (section_label, row) in enumerate(visible_rows):
                if section_label == "NOW":
                    state_label = "BUYABLE NOW" if _display_actionable(row) else str(row.get("validator_state") or row.get("action_label") or "WATCH").upper()
                elif section_label == "LATE":
                    state_label = str(row.get("validator_state") or "TOO LATE").upper()
                else:
                    state_label = str(row.get("validator_state") or row.get("action_label") or "WATCH").upper()
                tk_raw = str(row.get("ticker") or "—")[:6]
                tk_disp = f"{tk_raw}*P" if tk_raw.upper() in b_set else tk_raw
                caret = "►" if idx == cursor_idx else " "
                row_style = "reverse bold" if idx == cursor_idx else None
                left.add_row(
                    caret,
                    section_label,
                    tk_disp,
                    _track_tag(str(row.get("track") or "")),
                    str(row.get("data_tier") or "C"),
                    f"{float(row.get('alpha_score') or 0.0):.1f}",
                    _short_state_code(state_label),
                    _clip(str(row.get("why_now") or "—"), thesis_clip),
                    style=row_style,
                )
        else:
            left.add_row(" ", "—", "none", "—", "—", "—", "—", "No visible Alpha Discovery rows")

        selected_sym_upper = (
            str(selected_row.get("ticker") or "").upper() if selected_row else ""
        )
        agree_tag = " · [bold green]POSTURE+ALPHA AGREE[/]" if selected_sym_upper and selected_sym_upper in b_set else ""
        detail_title = f"Watch Detail · {selected_section}{agree_tag}"

        if compact_detail:
            # Inline detail strip — no inner Panel borders.  Wrapping the
            # Watch Detail in its own Panel cost ~3 lines (top border, title,
            # bottom border) and the Layout slot's residual height showed as
            # dead space below the strip.  Render the table and the detail
            # strip directly inside the outer ALPHA DISCOVERY panel so the
            # strip uses only the lines its content actually needs.
            detail = Text()
            detail.append(f"\n{detail_title}\n", style="bold dim")
            if selected_row is not None:
                ticker_disp = str(selected_row.get("ticker") or "—")
                state_label = str(selected_row.get("validator_state") or selected_row.get("action_label") or "Watch Only")
                score = float(selected_row.get("alpha_score") or 0.0)
                cursor_pos = f"{cursor_idx + 1}/{len(visible_rows)}" if visible_rows else "0/0"
                why = _clip(str(selected_row.get("why_now") or "—"), 110)
                risk = _clip(_risk_text(selected_row), 96)
                detail.append(
                    f"{ticker_disp:<6} | {selected_section:<5} | {state_label[:22]:<22} | "
                    f"score {score:>4.1f} | [{cursor_pos}]",
                    style="white",
                )
                if selected_sym_upper in b_set:
                    detail.append("  *P", style="bold green")
                detail.append("\n")
                detail.append(f"why  {why}\n", style="dim")
                detail.append(f"risk {risk}", style="yellow")
            else:
                detail.append("no selected row", style="dim")

            renderables.append(left)
            renderables.append(detail)
            return Panel(
                Group(*renderables),
                title="[bold]ALPHA DISCOVERY[/] [dim]research candidates[/]",
                border_style="magenta",
                padding=(0, 1),
            )

        detail = Text()
        if selected_row is not None:
            ticker_disp = str(selected_row.get('ticker') or '—')
            sym_upper = ticker_disp.upper()
            cursor_pos = f"{cursor_idx + 1}/{len(visible_rows)}" if visible_rows else "0/0"
            detail.append(f"SELECTED  {ticker_disp}", style="bold yellow")
            if sym_upper in b_set:
                detail.append("  *P in Posture focus", style="bold green")
            detail.append(f"   [{cursor_pos}]\n", style="dim")
            detail.append(
                f"Track {_track_tag(str(selected_row.get('track') or ''))} | "
                f"Tier {str(selected_row.get('data_tier') or 'C')} | "
                f"Score {float(selected_row.get('alpha_score') or 0.0):.1f}\n",
                style="white",
            )
            detail.append(
                f"State: {str(selected_row.get('validator_state') or selected_row.get('action_label') or 'Watch Only')}\n",
                style="white",
            )
            sleeve = str(selected_row.get('sleeve_resemblance') or '').strip()
            if sleeve:
                detail.append(f"Sleeve: {sleeve}\n", style="dim")
            detail.append("Why now:\n", style="bold dim")
            detail.append(f"- {_clip(str(selected_row.get('why_now') or '—'), 110 if expanded else 78)}\n", style="white")
            business = str((selected_row.get("block_details") or {}).get("business") or "")
            sponsorship = str((selected_row.get("block_details") or {}).get("sponsorship") or "")
            if business and "unavailable" not in business.lower():
                detail.append(f"- biz { _clip(business, 96 if expanded else 70) }\n", style="dim")
            if sponsorship:
                detail.append(f"- tape { _clip(sponsorship, 96 if expanded else 70) }\n", style="dim")
            detail.append("Risk:\n", style="bold dim")
            detail.append(f"- {_clip(_risk_text(selected_row), 110 if expanded else 78)}\n", style="yellow")
            detail.append("Transition:\n", style="bold dim")
            detail.append(f"- nightly {_clip(_transition_text(selected_row), 96 if expanded else 70)}\n", style="dim")
            flags = list(selected_row.get("validator_flags") or [])
            if flags:
                detail.append("Flags:\n", style="bold dim")
                for flag in flags[:3]:
                    detail.append(f"- {flag}\n", style="dim")
        else:
            detail.append("No visible Alpha Discovery rows", style="dim")

        detail_title = f"Detail · {selected_section}{agree_tag}"

        split = Table.grid(expand=True)
        split.add_column(ratio=6)
        split.add_column(ratio=4)
        split.add_row(
            Panel(left, title="Ranked Board", border_style="magenta", padding=(0, 1)),
            Panel(detail, title=detail_title, border_style="magenta", padding=(0, 1)),
        )
        renderables.append(split)

        if mode == "premarket_overlay" and nightly_items:
            visible = {str(row.get("ticker") or "").upper() for row in (now_rows + watch_rows + crowded_rows)}
            nightly_strong = [
                row for row in nightly_items
                if str(row.get("data_tier") or "C") in {"A", "B"}
            ]
            shifted = []
            for row in nightly_strong:
                sym = str(row.get("ticker") or "").upper()
                if not sym or sym in visible:
                    continue
                shifted.append(
                    (
                        tier_rank.get(str(row.get("data_tier") or "C"), 3),
                        -float(row.get("alpha_score") or 0.0),
                        sym,
                        row,
                    )
                )
            shifted.sort()
            if shifted:
                foot = Text("Nightly strong not in top view\n", style="bold dim")
                for _, _, sym, row in shifted[:4]:
                    overlay_row = next((r for r in items if str(r.get("ticker") or "").upper() == sym), None)
                    if overlay_row is not None:
                        dest = str(overlay_row.get("overlay_bucket") or overlay_row.get("bucket") or "—")
                        why = _clip(str(overlay_row.get("overlay_reason") or overlay_row.get("why_not") or "not surfaced"), 34)
                        foot.append(f"  {sym:<6} -> {dest:<17} {why}\n", style="dim")
                    else:
                        foot.append(f"  {sym:<6} -> not surfaced        no premarket overlay row\n", style="dim")
                renderables.append(foot)
        elif source is board and overlay_ok and nightly_items:
            overlay_by_ticker = {
                str(row.get("ticker") or "").upper(): row
                for row in (overlay.get("items") or [])
                if row.get("ticker")
            }
            changed = []
            for row in nightly_items:
                sym = str(row.get("ticker") or "").upper()
                ov = overlay_by_ticker.get(sym)
                if ov is None:
                    continue
                changed.append((sym, row, ov))
            if changed:
                foot = Text("Nightly -> premarket preview\n", style="bold dim")
                for sym, row, ov in changed[:4]:
                    dest = str(ov.get("overlay_bucket") or ov.get("bucket") or "—")
                    why = _clip(str(ov.get("overlay_reason") or ov.get("why_not") or "overlay updated"), 34)
                    foot.append(
                        f"  {sym:<6} nightly {str(row.get('data_tier') or '—')} -> {dest:<17} {why}\n",
                        style="dim",
                    )
                renderables.append(foot)
        renderables.append(Text("manual research board", style="dim"))
        return Panel(
            Group(*renderables),
            title="[bold]ALPHA DISCOVERY[/] [dim]research candidates[/]",
            border_style="magenta",
            padding=(0, 1),
        )

    # ── ticker lookup ─────────────────────────────────────────────────────────
    @staticmethod
    def ticker_lookup(state: State, data: "DataLayer" = None) -> Panel:
        cursor = "▌" if state.search_active else ""
        hist   = "  ".join(state.history[:6])
        t      = Text()
        if state.search_active:
            t.append(f"  > ", style="bold cyan")
            t.append(f"{state.search_buf}{cursor}", style="bold white")
        else:
            t.append(f"  / to focus  |  enter ticker + Enter  |  Esc to cancel", style="dim")
        t.append(f"\n  Recent: {hist or '—'}", style="dim")

        # Phase 2B.2 catalyst badge — surfaced inline so the operator sees
        # "EARNINGS TODAY" before reading any per-ticker panel.  Cache-only:
        # reads the earnings calendar already loaded by DataLayer.
        ticker = (getattr(state, "ticker", None) or "").upper()
        if ticker and data is not None:
            try:
                earn_rows = data.get("earnings") or []
            except Exception:
                earn_rows = []
            status = _earnings_status(ticker, earn_rows)
            if status:
                est_eps = None
                for row in earn_rows:
                    if str((row or {}).get("symbol") or "").upper() != ticker:
                        continue
                    if str((row or {}).get("date") or "")[:10] == \
                            datetime.now(timezone.utc).date().isoformat():
                        est_eps = (row or {}).get("epsEstimated")
                        break
                badge_style = (
                    "bold yellow on red" if status == "EARNINGS TODAY"
                    else "bold yellow" if status in ("EARNINGS TOMORROW", "EARNINGS THIS WEEK")
                    else "bold cyan"
                )
                t.append(f"\n  {status}", style=badge_style)
                if est_eps is not None:
                    try:
                        t.append(f"  (est EPS ${float(est_eps):.2f})", style="dim")
                    except (TypeError, ValueError):
                        pass
                if status == "EARNINGS TODAY":
                    t.append("  · require fresh Gatekeeper", style="dim")
                # Phase 2B.4: if the underlying FMP earnings cache row is
                # older than the doctrine threshold, do not let the badge
                # silently imply it is current.  Cache-only check.
                try:
                    cache_age = data.get("earnings_cache_age")
                except Exception:
                    cache_age = None
                if isinstance(cache_age, (int, float)) and cache_age > _EARNINGS_CACHE_STALE_S:
                    t.append(
                        f"  · EARNINGS DATA STALE ({int(cache_age // 3600)}h)",
                        style="bold yellow",
                    )
        return Panel(t, title="[bold]TICKER LOOKUP[/]", border_style="cyan", padding=(0,1))

    # ── research / AI analysis ────────────────────────────────────────────────
    @staticmethod
    def research(state: State, claude: ClaudeAnalyzer, data: "DataLayer" = None) -> Panel:
        ticker = state.ticker
        ana    = state.analysis
        t      = Text()

        if not ticker:
            t.append("Enter a ticker to begin analysis", style="dim")
        elif ana is None:
            t.append(f"Analyzing {ticker}…  (Claude Haiku)", style="dim italic")
        else:
            bias   = str(ana.get("bias","—")).upper()
            sleeve = str(ana.get("sleeve_resemblance", ana.get("strategy_fit","—"))).upper()
            actionable = str(ana.get("actionable_now","—")).upper()
            strat_label = _strategy_fit_label(sleeve)
            actionable_label = "Yes" if actionable == "YES" else ("No" if actionable == "NO" else "—")

            # Lens deference: when the cached Stock Lens has already vetoed
            # entry ("Too Extended" / actionable_now False) or is STALE, an
            # AI "Yes" is structurally weaker than the lens conclusion and
            # is downgraded to a conflict-aware label so the operator can't
            # eyeball the AI verdict in isolation.
            lens_conflict_reasons: List[str] = []
            lens_stale_flag = False
            try:
                lens_obj = (data.get_stock_lens(ticker.upper())
                            if (data is not None and ticker) else {}) or {}
            except Exception:
                lens_obj = {}
            if lens_obj and not lens_obj.get("_missing"):
                ev = ((lens_obj.get("layers") or {}).get("entry_validator") or {})
                if ev.get("available") and ev.get("actionable_now") is False:
                    lens_conflict_reasons.append(
                        f"Lens entry: {str(ev.get('view') or 'not actionable')}"
                    )
                lens_label = str(lens_obj.get("label") or "").strip().lower()
                if lens_label in {"bearish", "avoid"}:
                    lens_conflict_reasons.append(f"Lens label: {lens_obj.get('label')}")
                if lens_obj.get("_stale"):
                    lens_stale_flag = True
                    lens_conflict_reasons.append(
                        f"Lens STALE (age {lens_obj.get('_age_short') or '?'})"
                    )
            if actionable_label == "Yes" and lens_conflict_reasons:
                actionable_label = "Yes (Lens conflict)"
            tf     = str(ana.get("timeframe","—"))
            reg    = str(ana.get("regime_fit","—"))
            action = str(ana.get("action","—")).upper()
            quality= ana.get("quality")
            why    = str(ana.get("why","—"))
            inval  = str(ana.get("invalidation","—"))
            plan   = str(ana.get("next_session_plan","—"))
            evrisk = str(ana.get("event_risk","—"))
            inputs = str(ana.get("inputs","—"))
            evid   = ana.get("evidence") or []

            # Compact mode: when there is no edge (sleeve=NONE AND actionable!=YES),
            # the full structured verdict adds little above the Lens. Collapse to
            # sparkline + indicators + bias one-liner + WHY + NEXT_SESSION_PLAN +
            # INVALIDATION. The forward-looking plan is the only field that
            # adds independent value vs the Lens in this state.
            no_edge_compact = (
                str(sleeve).upper() in ("NONE", "—")
                and str(actionable).upper() != "YES"
            )

            bc = "bold green" if "BULL" in bias else ("bold red" if "BEAR" in bias else "yellow")
            ac = "bold green" if action=="ENTER" else \
                 ("yellow" if action=="WATCH" else ("dim" if action=="WAIT" else "bold red"))
            qc = "green" if quality and quality>=70 else \
                 ("yellow" if quality and quality>=50 else "red")

            # Phase 2B.2: catalyst-day notice — AI analysis is short-horizon
            # and can read stale on an event day, so flag explicitly.
            try:
                _earn_rows = data.get("earnings") if data is not None else []
            except Exception:
                _earn_rows = []
            if _is_earnings_day(ticker, _earn_rows or []):
                t.append("Earnings/catalyst day — require fresh Gatekeeper "
                         "and avoid stale reasons.\n", style="bold yellow")

            # sparkline
            spark = ana.get("_spark","")
            if spark: t.append(f"{spark}\n", style="cyan")

            # indicators row
            rsi  = ana.get("_rsi"); e20  = ana.get("_ema20"); e50 = ana.get("_ema50")
            macd = ana.get("_macd","—"); atr = ana.get("_atr"); vr  = ana.get("_vol_r")
            close= ana.get("_close"); chg = ana.get("_chg")
            if close:
                t.append(f"${close:.2f} ", style="bold white")
                t.append(f"({chg:+.1f}%)  " if chg else "", style="white")
            if rsi:  t.append(f"RSI {rsi}  ", style="white")
            if e20:  t.append(f"EMA20 ${e20:.2f}  ", style="white")
            if e50:  t.append(f"EMA50 ${e50:.2f}  ", style="white")
            mc   = "green" if str(macd)=="bull" else "red"
            t.append(f"MACD {str(macd).upper()}  ", style=mc)
            if atr:  t.append(f"ATR ${atr:.2f}  ", style="dim")
            if vr:   t.append(f"Vol {vr:.1f}x avg\n\n", style="dim")
            else:    t.append("\n\n")

            # structured verdict
            def row(label, val, val_style="white"):
                t.append(f"  {label:<18}", style="dim")
                t.append(f"{val}\n",       style=val_style)

            if action == "ENTER":
                why_prefix = "Why BUY"
            elif action == "WATCH":
                why_prefix = "Why WATCH"
            elif action == "AVOID":
                why_prefix = "Why AVOID"
            else:
                why_prefix = "Why not BUY yet"
            status_label = _action_status_label(action, why, strat_label)
            # Phase 1F Task 5: when the cached lens entry layer says
            # the setup is non-actionable, demote any candidate label
            # so no AI panel reads "ready now" against an extended /
            # broken / avoid entry.
            try:
                _lens_entry_view = (
                    ((lens_obj.get("layers") or {}).get("entry_validator")
                     or (lens_obj.get("layers") or {}).get("entry") or {})
                    .get("view")
                ) if lens_obj else None
                from core.fragility import neutralize_research_label
                status_label = neutralize_research_label(
                    status_label, entry_label=_lens_entry_view,
                )
            except Exception:
                pass

            if no_edge_compact:
                # Compact: bias one-liner, WHY, NEXT_SESSION_PLAN (the
                # only forward-looking field), INVALIDATION. Skips the
                # Quality/Confidence/Timeframe/Inputs/Evidence rows that
                # duplicate the Lens panel above.
                t.append("  ", style="")
                t.append(f"{bias}", style=bc)
                t.append("  ·  ", style="dim")
                t.append("No active sleeve fit", style="yellow")
                t.append("  ·  stand aside\n", style="dim")
                row(f"{why_prefix}:",      _clip(why, 68), "white")
                row("Next session plan:",  _clip(plan, 200), "bold white")
                row("Invalidation:",       _clip(inval, 60))
                if lens_conflict_reasons:
                    row("Lens deference:", "; ".join(lens_conflict_reasons)[:80], "dim")
            else:
                row("Bias:",         bias,   bc)
                row("Sleeve resemblance:", strat_label,  "bold white")
                if actionable_label == "Yes":
                    actionable_style = "bold white"
                elif actionable_label.startswith("Yes ("):
                    actionable_style = "bold yellow"
                else:
                    actionable_style = "yellow"
                row("Actionable now:", actionable_label, actionable_style)
                if lens_conflict_reasons:
                    row("Lens deference:", "; ".join(lens_conflict_reasons)[:80], "bold red")
                # Phase 1F: candidate-friendly statuses include the new
                # neutral label set; entry-driven non-actionable demotions
                # ("Research Only · entry not actionable", "Late / Extended")
                # render in muted style.
                _candidate_styles = {
                    "Research-aligned candidate", "WATCH", "BUY candidate",
                }
                row("Status:",       status_label,
                    ac if status_label in _candidate_styles else "yellow")
                row("Timeframe:",    tf)
                row("Regime fit:",   reg)
                row("Quality:",      f"{quality}/100" if quality else "—", qc)
                row("Confidence:",   _quality_tier(quality), qc)
                row(f"{why_prefix}:", _clip(why, 68), "white")
                t.append("\n  Evidence:\n", style="dim")
                for e in evid:
                    t.append(f"    • {_clip(e,78)}\n", style="white")
                t.append("\n")
                row("Invalidation:",      _clip(inval, 60))
                row("Next session plan:", _clip(plan, 200), "bold white")
                row("Event risk:",        _clip(evrisk, 60))
                row("Inputs used:",       _clip(inputs, 60), "dim")

            age = claude.age_str(ticker)
            t.append(f"\n  [dim]cached {age}  ·  claude-haiku-4-5  ·  "
                     f"{claude.calls()}/{claude.budget()} calls today[/]")

        title = f"[bold]AI ANALYSIS[/]" + (f" — [bold white]{ticker}[/]" if ticker else "")
        return Panel(t, title=title, border_style="magenta", padding=(0,1))

    # ── ranked opportunities (from scan_results table — scanner+veto pipeline) ──
    @staticmethod
    def top_opportunities(data: DataLayer) -> Panel:
        sr    = data.get("scan_results") or {}
        opps  = [opp for opp in (sr.get("opportunities") or []) if _active_strategy_row(opp)]
        lt    = sr.get("last_cycle_ts")
        scan  = data.scanner_status()

        # ── scan status / footer text ─────────────────────────────────────────
        footer = ""
        if lt:
            try:
                odt   = _parse_iso_utc(lt)
                age_m = int((_utc_now() - odt).total_seconds() // 60)
                footer = f"  [dim]last scan {age_m}m ago[/]"
            except Exception:
                pass
        if scan["running"]:
            scan_line = f"  [bold yellow]⟳ {scan['status']}[/]"
        elif scan["status"] and scan["status"] != "idle — press S to scan":
            scan_line = f"  [dim]{_clip(scan['status'], 60)}[/]"
        else:
            scan_line = "  [dim]S = run manual scan[/]"
        legend = f"  [dim]LIVE-CONFIRMED · SCAN-APPROVED · PM-REVIEW/CARRY-FWD · EXEC-FAILED[/]{scan_line}"

        # ── empty state — compact strip (3 rows total: borders + 1 line) ─
        # Fixed-size slot in build_scanner reclaims the surplus rows for the
        # developing/gated panel which still has rendering content.
        if not opps:
            t = Text()
            if scan["running"]:
                t.append("SCANNER SIGNALS: ", style="bold yellow")
                t.append(f"⟳ {scan['status']}", style="yellow")
            elif lt:
                t.append("SCANNER SIGNALS: ", style="bold dim")
                t.append("none in last 24h ", style="dim")
                t.append("· press s to rescan", style="yellow")
            else:
                t.append("SCANNER SIGNALS: ", style="bold dim")
                t.append("no scan results yet ", style="dim")
                t.append("· press s to run a manual scan", style="yellow")
            return Panel(t, box=box.SIMPLE, padding=(0, 1))

        # ── results table ─────────────────────────────────────────────────────
        tbl = Table(box=box.SIMPLE, show_header=True, header_style="bold dim",
                    expand=True, padding=(0,1))
        tbl.add_column("TICKER",   style="bold white", width=7)
        tbl.add_column("STRAT",    style="dim",        width=8)
        tbl.add_column("DIR",      width=5)
        tbl.add_column("SCORE",    justify="right",    width=6)
        tbl.add_column("ENTRY",    justify="right",    width=8)
        tbl.add_column("STOP",     justify="right",    width=8)
        tbl.add_column("TGT",      justify="right",    width=8)
        tbl.add_column("R:R",      justify="right",    width=5)
        tbl.add_column("STATE",    width=12)
        tbl.add_column("AGE",      justify="right",    width=5)

        def _layer_display(status: str, ts: str):
            s = status.upper()
            try:
                odt   = _parse_iso_utc(ts)
                age_m = int((_utc_now() - odt).total_seconds() // 60)
            except Exception:
                age_m = 0
            post_market = get_session_state() != SessionState.REGULAR
            if s == "READY_NOW":         return Text("LIVE-CONFIRMED",  style="bold green")
            if s == "SCAN_APPROVED":
                if age_m > 240:
                    return Text("CARRY-FWD", style="yellow")
                if post_market:
                    return Text("PM-REVIEW", style="yellow")
                return Text("SCAN-APPROVED",   style="yellow")
            if s == "EXECUTION_FAILED":  return Text("EXEC-FAILED",     style="bold red")
            if s == "APPROVED":          return Text("SCAN-APPROVED",   style="dim yellow")  # legacy
            if s == "WATCH":             return Text("CARRY-FWD",       style="dim yellow")  # legacy
            return Text(s[:14], style="dim")

        now_utc = _utc_now()
        for opp in opps[:8]:
            status = str(opp.get("status") or "—").upper()
            dirn   = str(opp.get("direction") or "—").upper()
            dc     = "green" if dirn in ("LONG","BUY") else "red"
            rr     = opp.get("risk_reward")
            rr_s   = f"{float(rr):.1f}R" if rr else "—"
            try:
                odt   = _parse_iso_utc(opp.get("ts", ""))
                age_s = f"{int((now_utc-odt).total_seconds()//60)}m"
            except Exception:
                age_s = "—"
            _p = lambda f: f"${float(opp.get(f,0)):.2f}" if opp.get(f) else "—"
            tbl.add_row(
                str(opp.get("ticker","—")),
                str(opp.get("strategy","—"))[:8],
                Text(dirn[:4], style=dc),
                f"{float(opp.get('score',0)):.0f}",
                _p("entry_price"), _p("stop_loss"), _p("target_price"),
                rr_s,
                _layer_display(status, str(opp.get("ts",""))),
                age_s,
            )

        return Panel(tbl,
                     title=f"[bold]SCANNER SIGNALS[/]{footer}",
                     subtitle=legend,
                     border_style="yellow", padding=(0,0))

    # ── vetoed scans (council GATED) + structural developing (universe) ──────────
    @staticmethod
    def developing_soon(data: DataLayer) -> Panel:
        """
        Two data sources displayed together:

        COUNCIL GATED — scanner-confirmed signals that passed structural checks
          but were blocked by the veto council (soft score < 50 or regime/event gate).
          Source: scan_results table, status=GATED.

        STRUCTURALLY DEVELOPING — universe candidates scoring just below threshold.
          Passed daily-bar structural filters; not yet scanner-confirmed.
          Source: universe snapshot, readiness=DEVELOPING.
        """
        sr   = data.get("scan_results") or {}
        snap = data.get("universe_snap") or {}
        vetoed   = [row for row in (sr.get("vetoed") or sr.get("developing") or []) if _active_strategy_row(row)]
        dev_cands = [
            c for c in (snap.get("strategy_candidates") or [])
            if c.get("readiness") == "DEVELOPING" and _active_strategy_row(c)
        ]
        metadata = snap.get("metadata") or {}
        stale_symbols = sorted(
            [sym for sym, row in metadata.items() if isinstance(row, dict) and row.get("bars_stale")]
        )[:6] if isinstance(metadata, dict) else []
        scan_rows = (sr.get("opportunities") or []) + vetoed
        block_counts = {
            "council": sum(1 for r in scan_rows if str(r.get("status") or "").upper() == "GATED"),
            "allocator": sum(1 for r in scan_rows if str(r.get("status") or "").upper() == "ALLOCATION_BLOCKED"),
            "execution": sum(1 for r in scan_rows if str(r.get("status") or "").upper() == "EXECUTION_FAILED"),
            "duplicate": sum(
                1 for r in scan_rows
                if "duplicate" in str(r.get("veto_reason") or "").lower()
                or "open exposure" in str(r.get("veto_reason") or "").lower()
            ),
            "frozen": sum(
                1 for r in scan_rows
                if "frozen" in str(r.get("veto_reason") or "").lower()
            ),
            "missing": sum(
                1 for r in scan_rows
                if any(
                    kw in str(r.get("veto_reason") or "").lower()
                    for kw in ("missing", "quote", "data")
                )
            ),
            "stale": len(stale_symbols),
        }
        # Deduplicate dev candidates by symbol
        seen_dev: set = set()
        unique_dev: List[Dict] = []
        for c in sorted(dev_cands, key=lambda x: -float(x.get("final_score", 0))):
            sym = c.get("symbol", "")
            if sym not in seen_dev:
                seen_dev.add(sym)
                unique_dev.append(c)
        unique_dev = unique_dev[:4]

        t = Text()

        # Section 1: blocked summary + council-gated scanner signals
        t.append("  FILTER SUMMARY  ", style="bold dim")
        t.append(
            f"score_gate={block_counts['council']}  alloc={block_counts['allocator']}  "
            f"data_err={block_counts['execution']}  dup={block_counts['duplicate']}  "
            f"inactive={block_counts['frozen']}  data={block_counts['missing']}  "
            f"stale={block_counts['stale']}\n",
            style="dim",
        )
        if stale_symbols:
            t.append(f"  stale bars: {', '.join(stale_symbols)}\n", style="yellow")
        else:
            t.append("  stale bars: none\n", style="dim")

        if vetoed:
            t.append("\n  FILTERED / LOW-SCORE  ", style="bold dim")
            t.append("scanner-confirmed · filtered after scan stage\n", style="dim")
            for d in vetoed[:4]:
                dirn   = str(d.get("direction") or "").upper()[:4]
                dc     = "green" if dirn in ("LONG","BUY") else "red"
                status = str(d.get("status") or "GATED").upper()
                # Distinguish the filter stage clearly
                if status == "ALLOCATION_BLOCKED":
                    stage  = Text("ALLOC-FILT", style="bold red")
                    reason = _clip(str(d.get("veto_reason") or "allocator filtered"), 34)
                else:
                    stage  = Text(str(d.get("veto_agent") or "score_gate")[:10], style="yellow")
                    reason = _clip(str(d.get("veto_reason") or "—"), 34)
                score  = float(d.get("score") or 0)
                t.append(f"  {str(d.get('ticker','—')):<7}", style="bold white")
                t.append(f"{str(d.get('strategy','—'))[:8]:<8}  ", style="dim")
                t.append(f"{dirn:<5}", style=dc)
                t.append(f" {score:.0f}/100  ", style="white")
                t.append(stage); t.append("  ", style="")
                t.append(f"{reason}\n", style="dim")
        else:
            t.append("\n  No filtered scanner rows in last 24h\n", style="dim")

        # Section 2: Universe structural developing candidates
        # Header now mirrors the column layout below: ticker | sleeve | dir |
        # score | gap-to-trigger.  Gap is computed from the per-strategy
        # structural threshold pulled from the snapshot summary; this is the
        # same threshold the scanner uses, so the value is directly meaningful.
        t.append("\n  STRUCTURALLY DEVELOPING  ", style="bold dim")
        t.append("daily-bar only · not scanner-confirmed\n", style="dim")
        thresholds = (snap.get("summary") or {}).get("score_thresholds") or {}
        if unique_dev:
            t.append(
                f"  {'TK':<7}{'SLEEVE':<10}{'DIR':<5}{'SCORE':>6}  {'GAP':>7}  REASON\n",
                style="bold dim",
            )
            for c in unique_dev:
                dirn  = str(c.get("direction") or "?")
                dc    = "green" if dirn == "LONG" else "red"
                score = float(c.get("final_score", 0))
                # Threshold is keyed by scanner_key; strategy registry exposes
                # the mapping.  Default to the strategy code when no mapping.
                strat_lbl = str(c.get("strategy") or "—")
                strat_key = strat_lbl
                try:
                    strat_key = (
                        normalize_strategy(strat_lbl)
                        or strat_lbl
                    )
                except Exception:
                    pass
                # Try a few likely keys; if none found, gap is unknown.
                threshold = (
                    thresholds.get(strat_key)
                    or thresholds.get(strat_lbl)
                    or thresholds.get(strat_lbl.lower())
                )
                if isinstance(threshold, (int, float)):
                    gap = float(threshold) - score
                    gap_str = f"{gap:+.3f}"
                    gap_style = "yellow" if gap > 0 else "green"
                else:
                    gap_str = "—"
                    gap_style = "dim"
                rsn = _clip(c.get("key_reason", "—"), 32)
                t.append(f"  {str(c.get('symbol','—'))[:6]:<7}", style="bold white")
                t.append(f"{strat_lbl[:8]:<10}", style="dim")
                t.append(f"{dirn[:4]:<5}", style=dc)
                t.append(f"{score:>6.3f}", style="white")
                t.append(f"  {gap_str:>7}", style=gap_style)
                t.append(f"  {rsn}\n", style="dim")
        else:
            no_snap = not snap
            t.append("  " + (
                "Universe snapshot not available" if no_snap else
                "No near-threshold candidates — market below average quality"
            ) + "\n", style="dim")

        return Panel(t, title="[bold]RESEARCH FILTER FRICTION[/]",
                     subtitle="[dim]filter breakdown + stale-bar visibility + near-threshold structural names[/]",
                     border_style="dim", padding=(0,1))

    # ── universe readiness summary (per-strategy counts from snapshot) ───────────
    @staticmethod
    def universe_readiness_summary(data: DataLayer) -> Panel:
        """
        Per-strategy candidate counts from the universe snapshot.
        These are STRUCTURAL (daily-bar) readiness labels — not scanner-confirmed.

        QUALIFIED  = score ≥ structural threshold + fresh data
        STALE      = score ≥ structural threshold + stale-data caveat
        DEVELOPING = score near structural threshold
        """
        snap    = data.get("universe_snap") or {}
        summary = snap.get("summary") or {}
        cands   = snap.get("strategy_candidates") or []

        # Count per strategy per readiness
        from collections import defaultdict
        counts: Dict = defaultdict(lambda: defaultdict(int))
        for c in cands:
            strat = str(c.get("strategy") or "?").upper()
            if not is_active_paper_strategy(strat):
                continue
            rdns  = str(c.get("readiness") or "?")
            counts[strat][rdns] += 1

        t         = Text()
        thresholds = summary.get("score_thresholds") or {}
        strats     = [(row.key, row.scanner_key) for row in registry_rows(active_paper_strategies())]

        t.append(f"  {'STRAT':<11} {'QUAL':>4} {'STALE':>5} {'DEV':>4}  STRUCT THR\n",
                 style="bold dim")
        any_ready = False
        for label, key in strats:
            sc     = counts.get(label, {})
            ready  = sc.get("READY_NOW", 0)
            watch  = sc.get("WATCH", 0)
            devl   = sc.get("DEVELOPING", 0)
            thresh = thresholds.get(key, "—")
            thresh_s = f"{thresh:.2f}" if isinstance(thresh, float) else str(thresh)
            if ready > 0: any_ready = True
            rc = "bold green" if ready > 0 else "dim"
            wc = "yellow" if watch > 0 else "dim"
            dc = "yellow" if devl > 0 else "dim"
            t.append(f"  {label:<11}", style="white")
            t.append(f"{ready:>4}", style=rc)
            t.append(f"{watch:>5}", style=wc)
            t.append(f"{devl:>4}", style=dc)
            t.append(f"  {thresh_s}\n", style="dim")

        # Universe metadata
        if not snap:
            t.append("\n  [dim]Universe snapshot not loaded[/]")
        else:
            built = summary.get("built_at", "")
            fallback = summary.get("fallback_used", False)
            ver = summary.get("pipeline_version", "")
            age_str = ""
            if built:
                try:
                    dt    = _parse_iso_utc(built)
                    age_m = int((_utc_now() - dt).total_seconds() / 60)
                    age_str = f"{age_m}m"
                except Exception:
                    pass
            file_age = snap.get("_file_age_seconds", 0)
            fa_str   = f"{file_age//60}m" if file_age else "?"
            t.append(f"\n  built {age_str} ago · file {fa_str} old", style="dim")
            if ver: t.append(f" · v{ver}", style="dim")
            if fallback: t.append("\n  ⚠ FALLBACK UNIVERSE", style="bold red")
            warns = summary.get("warnings") or []
            if warns:
                t.append(f"\n  ⚠ {_clip(warns[0], 44)}", style="yellow")
            # Threshold calibration telemetry
            strat_sz = summary.get("strategy_sizes") or {}
            if strat_sz:
                total_q = sum(strat_sz.values())
                if total_q == 0 and not fallback:
                    t.append("\n  0 structural qualifiers across active sleeves",
                             style="bold yellow")
            stale_symbols = sorted(
                [sym for sym, row in (snap.get("metadata") or {}).items() if isinstance(row, dict) and row.get("bars_stale")]
            )[:6] if isinstance(snap.get("metadata"), dict) else []
            if stale_symbols:
                t.append(f"\n  stale bars: {', '.join(stale_symbols)}", style="yellow")

        return Panel(t, title="[bold]UNIVERSE READINESS[/] [dim]structural · daily-bar[/]",
                     border_style="cyan", padding=(0,1))

    # ── universe structural candidates (from snapshot, not scanner-confirmed) ──
    @staticmethod
    def universe_candidates(data: DataLayer) -> Panel:
        """
        Top structural candidates from the universe snapshot.

        These are CANDIDATE-layer only:
          • passed daily-bar structural filters
          • score ≥ per-strategy threshold
          • NOT yet evaluated by intraday scanners
          • NOT council-vetoed or risk-checked

        Do NOT treat these as trade signals — they are research candidates.
        """
        snap  = data.get("universe_snap") or {}
        cands = snap.get("strategy_candidates") or []

        # READY_NOW + WATCH, deduplicated by symbol (highest score wins)
        ready = [
            c for c in cands
            if c.get("readiness") in ("READY_NOW","WATCH") and _active_strategy_row(c)
        ]
        ready.sort(key=lambda c: (-float(c.get("final_score",0)), c.get("symbol","")))
        seen: set = set()
        unique: List[Dict] = []
        for c in ready:
            sym = c.get("symbol","")
            if sym not in seen:
                seen.add(sym)
                unique.append(c)

        tbl = Table(box=box.SIMPLE, show_header=True, header_style="bold dim",
                    expand=True, padding=(0,1))
        tbl.add_column("TICKER",   style="bold white", width=7)
        tbl.add_column("TYPE",     style="dim",        width=4)
        tbl.add_column("STRAT",    style="dim",        width=8)
        tbl.add_column("DIR",      width=5)
        tbl.add_column("SCORE",    justify="right",    width=6)
        tbl.add_column("FRESH",    justify="right",    width=11)
        tbl.add_column("REASON",   style="dim")

        if not unique:
            if not snap:
                msg = "Universe snapshot not loaded — is trader daemon running?"
            else:
                msg = "No structural candidates above threshold"
            tbl.add_row(f"[dim]{msg}[/]", *[""]*6)
        else:
            # Group rows by direction/type so the user can scan LONG vs
            # SHORT vs ETF/inverse-ETF without parsing per-row tags.
            #   LONG  = equity LONG candidates
            #   SHORT = equity SHORT candidates
            #   ETF   = instrument-symbol candidates (SPY/QQQ/SOXL/SQQQ/...)
            buckets: Dict[str, List[Dict]] = {"LONG": [], "SHORT": [], "ETF": []}
            for c in unique:
                sym = str(c.get("symbol") or "")
                dirn = str(c.get("direction") or "—").upper()
                if _is_instrument_symbol(sym):
                    buckets["ETF"].append(c)
                elif dirn == "LONG":
                    buckets["LONG"].append(c)
                elif dirn == "SHORT":
                    buckets["SHORT"].append(c)
                else:
                    buckets["LONG"].append(c)  # treat unknown as long for display

            # Per-section caps so no single bucket monopolises the table.
            section_cap = max(2, 8 // max(1, sum(1 for v in buckets.values() if v)))

            def _add_row(c: Dict[str, Any]) -> None:
                rdns  = str(c.get("readiness","—"))
                rc    = "bold green" if rdns == "READY_NOW" else "yellow"
                dirn  = str(c.get("direction","—"))
                dc    = "green" if dirn == "LONG" else "red"
                score = float(c.get("final_score", 0))
                fresh = str(c.get("freshness_ts") or "—")
                rsn   = _clip(str(c.get("key_reason","—")), 28)
                curated = " ★" if c.get("is_curated") else ""
                tbl.add_row(
                    str(c.get("symbol","—")) + curated,
                    _instrument_type_label(c.get("symbol")),
                    str(c.get("strategy","—"))[:8],
                    Text(dirn[:5], style=dc),
                    Text(f"{score:.3f}", style=rc),
                    fresh,
                    rsn,
                )

            for label in ("LONG", "SHORT", "ETF"):
                rows = buckets[label]
                if not rows:
                    continue
                # Section header row — placed in REASON (widest column) so
                # the dash separators don't wrap inside the narrow TICKER
                # cell.  Short label + small dash run survives most terminal
                # widths; wider terminals just leave trailing space.
                header_color = (
                    "bold green"  if label == "LONG"
                    else "bold red" if label == "SHORT"
                    else "bold cyan"
                )
                tbl.add_row(
                    "", "", "", "", "", "",
                    Text(f"── {label}", style=header_color),
                )
                for c in rows[:section_cap]:
                    _add_row(c)

        built = (snap.get("summary") or {}).get("built_at","")
        age_note = ""
        if built:
            try:
                dt    = _parse_iso_utc(built)
                age_m = int((_utc_now() - dt).total_seconds() / 60)
                age_note = f" [dim]· {age_m}m old[/]"
            except Exception:
                pass

        return Panel(
            tbl,
            title=f"[bold]STRUCTURAL CANDIDATES[/] [dim]structural pool · not scanner-confirmed[/]{age_note}",
            subtitle="[dim]TYPE=EQ/ETF  ·  ★ = curated watchlist  ·  FRESH = last completed daily bar date[/]",
            border_style="dim",
            padding=(0,0),
        )

    # ── compact top-3 strip (for Monitor mode) ────────────────────────────────
    @staticmethod
    def top3_strip(data: DataLayer) -> Panel:
        sr    = data.get("scan_results") or {}
        snap  = data.get("universe_snap") or {}
        opps  = [opp for opp in (sr.get("opportunities") or []) if _active_strategy_row(opp)]
        t     = Text()

        if opps:
            # Show scanner-confirmed signals (authoritative — scanner + veto pipeline)
            t.append("SIGNALS  ", style="bold dim")
            for opp in opps[:3]:
                dirn   = str(opp.get("direction") or "").upper()[:4]
                dc     = "green" if dirn in ("LONG","BUY") else "red"
                status = str(opp.get("status") or "").upper()
                if status == "READY_NOW":
                    sc, lbl = "bold green", "EXEC"
                elif status == "EXECUTION_FAILED":
                    sc, lbl = "bold red",   "FAIL"
                else:
                    sc, lbl = "yellow",     "APR"
                side_lbl = "long" if dirn in ("LONG","BUY") else "short"
                t.append(f"  {opp.get('ticker','—')}", style="bold white")
                t.append(f" {side_lbl}", style=dc)
                t.append(f" {float(opp.get('score',0)):.0f}", style="white")
                t.append(f" [{lbl}]  ", style=sc)
        else:
            # No scanner results — show universe structural candidates as fallback info
            cands = [c for c in (snap.get("strategy_candidates") or [])
                     if c.get("readiness") == "READY_NOW" and _active_strategy_row(c)]
            cands.sort(key=lambda c: -float(c.get("final_score",0)))
            seen: set = set()
            uniq = []
            for c in cands:
                s = c.get("symbol","")
                if s not in seen: seen.add(s); uniq.append(c)
            if uniq:
                t.append("CANDIDATES  ", style="bold dim")
                t.append("[dim](structural · not scanner-confirmed)[/]  ")
                for c in uniq[:3]:
                    dirn = str(c.get("direction","?"))
                    dc   = "green" if dirn == "LONG" else "red"
                    side_lbl = "long" if dirn == "LONG" else "short"
                    t.append(f"  {c.get('symbol','—')}", style="bold white")
                    t.append(f" {side_lbl}", style=dc)
                    t.append(f" {float(c.get('final_score',0)):.3f}  ", style="dim")
            else:
                t.append("SETUPS  ", style="bold dim")
                t.append("no scanner signals · no structural candidates above threshold",
                         style="dim")

        return Panel(t, title=None, box=box.SIMPLE, padding=(0,1))

    # ── recent decisions ──────────────────────────────────────────────────────
    @staticmethod
    def decisions(data: DataLayer) -> Panel:
        dec = data.get("db_decisions") or []
        # Empty state: compact strip — paired with size=3 in build_scanner
        # so the saved rows go to neighboring panels.
        if not dec:
            t = Text()
            t.append("RECENT DECISIONS: ", style="bold dim")
            t.append("none yet ", style="dim")
            t.append("· awaiting trader daemon decisions", style="dim")
            return Panel(t, box=box.SIMPLE, padding=(0, 1))

        tbl = Table(box=box.SIMPLE, show_header=True, header_style="bold dim",
                    expand=True, padding=(0,1))
        tbl.add_column("TIME",       style="dim",   width=6)
        tbl.add_column("TICKER",     style="white", width=7)
        tbl.add_column("STRAT",      style="dim",   width=8)
        tbl.add_column("DIR",        style="white", width=5)
        tbl.add_column("STATE",      width=17)
        tbl.add_column("WHY / NOW",  style="dim")
        for d in dec[:8]:
            ts    = str(d.get("ts",""))[11:16]
            v     = d.get("verdict","—")
            vu    = str(v).upper()
            vc    = (
                "bold green" if any(k in vu for k in ("LIVE-CONFIRMED", "SCAN-APPROVED"))
                else "yellow" if any(k in vu for k in ("CARRY", "POST", "UNCHANGED"))
                else "bold red" if any(k in vu for k in ("BLOCK", "FAIL", "REJECT", "GATED"))
                else "white"
            )
            tbl.add_row(ts, d.get("ticker","—"), (d.get("strategy") or "—")[:8],
                        (d.get("direction") or "—")[:5].upper(),
                        Text(v, style=vc), _clip(d.get("notes",""),52))
        return Panel(tbl, title="[bold]RECENT DECISIONS[/]", border_style="dim", padding=(0,0))

    # ── portfolio risk ────────────────────────────────────────────────────────
    @staticmethod
    def portfolio_risk(data: DataLayer) -> Panel:
        pos  = data.get("positions") or []
        acct = data.get("account")   or {}
        eq   = float(acct.get("equity",0) or 0) or 1.0
        t    = Text()

        long_val  = sum(float(p.get("market_value",0) or 0) for p in pos if p.get("side","long")=="long")
        short_val = sum(abs(float(p.get("market_value",0) or 0)) for p in pos if p.get("side")=="short")
        gross_l   = long_val  / eq * 100
        gross_s   = short_val / eq * 100
        net       = (long_val-short_val) / eq * 100

        def bar(pct, cap, w=20):
            f = min(int(pct/cap*w), w); color = "red" if pct>cap*0.85 else "green"
            return f"[{color}]{'█'*f}[/]{'░'*(w-f)}"

        t.append(f"  Gross Long   {gross_l:5.1f}%  {bar(gross_l,100)}  cap 100%\n")
        t.append(f"  Gross Short  {gross_s:5.1f}%  {bar(gross_s,30)}  cap 30%\n")
        t.append(f"  Net Long     {net:5.1f}%  {bar(max(net,0),80)}  cap 80%\n")

        # daily loss limit
        dpnl   = float(acct.get("daily_pnl",0) or 0)
        opnl   = sum(float(p.get("unrealized_pnl",0) or 0) for p in pos)
        total  = dpnl + opnl
        limit  = -eq * cfg.MAX_DAILY_LOSS_PCT
        loss_p = total/eq*100
        t.append(f"\n  P&L today    ${total:+,.0f}  limit ${limit:,.0f}  ({loss_p:+.1f}%)")
        lc = "bold red" if total < limit*0.8 else ("yellow" if total < limit*0.5 else "green")
        t.append(f"  [bold]{'⚠ NEAR LIMIT' if total < limit*0.5 else 'NORMAL'}[/]", style=lc)

        return Panel(t, title="[bold]PORTFOLIO RISK[/]", border_style="red", padding=(0,1))

    # ── earnings wall ─────────────────────────────────────────────────────────
    @staticmethod
    def earnings(data: DataLayer) -> Panel:
        earn = data.get("earnings") or []
        pos  = data.get("positions") or []
        snap = data.get("universe_snap") or {}
        held = {p["ticker"] for p in pos}
        md   = snap.get("metadata") or {}
        active_syms = {
            str(r.get("symbol") or "").upper()
            for r in (snap.get("strategy_candidates") or [])
            if str(r.get("readiness") or "").upper() in {"READY_NOW", "WATCH", "DEVELOPING"}
        }
        by_date: Dict[str,list] = {}
        today = date.today().isoformat()

        def _is_us_listed(sym: str) -> bool:
            # Dashboard cache hygiene only (no provider call): the FMP earnings
            # calendar mixes in foreign listings (e.g. "RELIANCE.NS",
            # "0700.HK") that pollute the wall.  US tickers are 1-5 plain
            # A-Z letters with no exchange suffix.  This filter never affects
            # trading logic — it only cleans what the panel displays.
            return bool(re.fullmatch(r"[A-Z]{1,5}", sym))

        for e in earn:
            sym = str(e.get("symbol","")).upper()
            if not _is_us_listed(sym):
                continue
            row = md.get(sym) or {}
            avg_dvol = float(row.get("avg_dollar_vol_20") or 0.0)
            important = 0
            if sym in held:
                important += 100
            if sym in active_syms:
                important += 40
            if avg_dvol >= 500_000_000:
                important += 20
            elif avg_dvol >= 100_000_000:
                important += 12
            elif avg_dvol >= 25_000_000:
                important += 6
            event = dict(e)
            event["_importance"] = important
            event["_avg_dvol"] = avg_dvol
            d = str(e.get("date",""))[:10]
            by_date.setdefault(d,[]).append(event)

        def _reason_tag(sym: str, avg_dvol: float) -> Tuple[str, str]:
            """Single highest-priority reason tag for an earnings row.

            Mirrors the importance bonuses so the user can see *why* a name
            made the wall (held > active candidate > liquidity tier).
            """
            if sym in held:
                return "HELD", "bold red"
            if sym in active_syms:
                return "CAND", "bold yellow"
            if avg_dvol >= 500_000_000:
                return "MEGA", "bold cyan"
            if avg_dvol >= 100_000_000:
                return "LIQ",  "cyan"
            if avg_dvol >= 25_000_000:
                return "MID",  "dim cyan"
            return "", ""

        t = Text()
        # Phase 2B.4: surface a stale-cache banner when the underlying
        # fmp:earnings_cal:7 row is older than the doctrine threshold so
        # the panel never silently implies the dates are current.
        try:
            cache_age = data.get("earnings_cache_age")
        except Exception:
            cache_age = None
        if isinstance(cache_age, (int, float)) and cache_age > _EARNINGS_CACHE_STALE_S:
            t.append(
                f"EARNINGS DATA STALE — cache age {int(cache_age // 3600)}h "
                f"(> {_EARNINGS_CACHE_STALE_S // 3600}h doctrine).\n",
                style="bold yellow",
            )
        # Legend so the inline tags are self-explanatory at a glance.
        t.append("HELD=position · CAND=active candidate · MEGA/LIQ/MID=$vol tier\n",
                 style="dim")
        for d in sorted(by_date)[:5]:
            evts = sorted(
                by_date[d],
                key=lambda e: (
                    -int(e.get("_importance") or 0),
                    -float(e.get("_avg_dvol") or 0.0),
                    str(e.get("symbol") or ""),
                ),
            )
            evts = [e for e in evts if int(e.get("_importance") or 0) > 0] or evts[:5]
            lbl  = "TODAY  " if d==today else f"{d[5:]}  "
            t.append(lbl, style="bold yellow" if d==today else "dim")
            for e in evts[:4]:
                sym  = str(e.get("symbol",""))
                eps  = e.get("epsEstimated")
                avg_dvol = float(e.get("_avg_dvol") or 0.0)
                tag, tag_style = _reason_tag(sym, avg_dvol)
                hi = int(e.get("_importance") or 0) >= 40
                sym_style = "bold red" if sym in held else ("bold white" if hi else "white")
                t.append(f"{sym}", style=sym_style)
                if tag:
                    t.append(f" {tag}", style=tag_style)
                if eps:
                    t.append(f"(${float(eps):.2f})", style="dim")
                t.append("  ")
            extra = len(evts) - 4
            if extra > 0:
                t.append(f"+{extra} more  ", style="dim")
            t.append("\n")
        # warn on held tickers
        for p in pos:
            tk = p.get("ticker","")
            for e in earn:
                if e.get("symbol","").upper()==tk:
                    d = str(e.get("date",""))[:10]
                    try:
                        da = (date.fromisoformat(d)-date.today()).days
                        if da<=3: t.append(f"\n⚠ {tk} earns {d} ({da}d) — POSITION AT RISK", style="bold red")
                    except Exception: pass
                    break
        return Panel(t, title="[bold]EARNINGS WALL — 7d[/]", border_style="yellow", padding=(0,1))

    # ══════════════════════════════════════════════════════════════════════════
    # MARKET & SECTOR REGIME FORECASTER V1 — research-only dashboard panels.
    # All panels read cached JSON via DataLayer; no provider calls.
    # No paper / governance / execution side-effects.
    # ══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _forecast_run_hint() -> str:
        return "./scripts/run_research_cycle.sh nightly"

    @staticmethod
    def _stock_lens_run_hint(ticker: str) -> str:
        return f"./scripts/run_research_cycle.sh nightly  # then: research/stock_lens.py --ticker {ticker}"

    @staticmethod
    def _confidence_style(conf: str) -> str:
        c = (conf or "").lower()
        if c == "high":   return "bold green"
        if c == "medium" or c == "med": return "yellow"
        if c == "low":    return "dim yellow"
        return "white"

    @staticmethod
    def _stance_style(stance: str) -> str:
        s = (stance or "").lower()
        if s == "favored":    return "bold green"
        if s == "allowed":    return "green"
        if s == "selective":  return "yellow"
        if s == "avoid":      return "red"
        return "white"

    @staticmethod
    def _bias_style(bias: str) -> str:
        b = (bias or "").lower()
        if "construct" in b or "bull" in b: return "green"
        if "defens"    in b or "risk-off" in b or "bear" in b: return "red"
        if "neutral"   in b or "chop" in b: return "yellow"
        return "white"

    @staticmethod
    def _compute_30d_bias(spy_bars: List[Dict]) -> Tuple[str, Optional[float]]:
        """Phase 1F+ extra: 30d trend bias for the Market Forecast panel.

        Read-only compute from cached SPY daily bars (DataLayer already
        pulls 60d for other Mode-1 panels). Pairs 30-bar return with the
        position vs the 30-bar SMA so a sharp spike against a downtrend
        still reads as "mixed" rather than a fresh bullish print.

        Returns (label, ret_30_pct). Label is one of "bullish",
        "bearish", "mixed", "neutral", "—" (insufficient bars). The
        label vocabulary deliberately matches bias_5d/bias_10d so the
        existing PB._bias_style colors apply unchanged."""
        if not spy_bars or len(spy_bars) < 30:
            return "—", None
        try:
            closes = [float(b.get("close") or 0) for b in spy_bars[-30:]]
        except (TypeError, ValueError):
            return "—", None
        if len(closes) < 30 or closes[0] <= 0:
            return "—", None
        ret_30_pct = (closes[-1] - closes[0]) / closes[0] * 100.0
        sma_30 = sum(closes) / len(closes)
        above_sma = closes[-1] > sma_30

        # Thresholds: 3% chosen to match the rough magnitude that
        # bias_5d/bias_10d treat as a directional swing. Inside ±0.7%
        # the tape is too flat to call.
        if ret_30_pct >= 3.0 and above_sma:
            label = "bullish"
        elif ret_30_pct <= -3.0 and not above_sma:
            label = "bearish"
        elif abs(ret_30_pct) <= 0.7:
            label = "neutral"
        else:
            label = "mixed"
        return label, round(ret_30_pct, 1)

    @staticmethod
    def market_bias_strip(data: DataLayer) -> Panel:
        """
        At-a-glance MARKET BIAS badge for Mode 1.  Reverse-colored single
        word (BULLISH / BEARISH / NEUTRAL) derived from the 5d bias in the
        cached market_forecast artifact, with 10d + confidence + age trailing
        in dim.  Reuses the same cache as market_forecast_strip — no I/O.
        """
        f = data.get("market_forecast") or {}
        t = Text(justify="center")
        if not f or f.get("_missing"):
            t.append("MARKET BIAS  ", style="bold cyan")
            t.append("— forecast artifact missing", style="dim")
            return Panel(t, box=box.SIMPLE, padding=(0, 1))

        head = f.get("headline") or {}
        bias5  = str(head.get("bias_5d")  or "—")
        bias10 = str(head.get("bias_10d") or "—")
        conf   = str(head.get("confidence") or "—")
        age    = f.get("_age_short") or "?"
        stale  = f.get("_stale")
        breached = bool(head.get("invalidation_breached"))
        low_conf = conf.lower() == "low"

        # Phase 1F+: when the forecast is breached or LOW confidence, the
        # primary badge changes from BULLISH to the consolidated conflict
        # state so the operator can't visually anchor on a green word
        # while the underlying regime is invalidated.
        bl = bias5.lower()
        if breached and low_conf:
            word, badge_style = "FRAGILE", "bold white on red"
        elif breached or low_conf:
            word, badge_style = "CONFLICTED", "bold black on yellow"
        elif "bull" in bl or "construct" in bl:
            word, badge_style = "BULLISH", "bold white on green"
        elif "bear" in bl or "defens" in bl or "risk-off" in bl:
            word, badge_style = "BEARISH", "bold white on red"
        elif "neutral" in bl or "chop" in bl:
            word, badge_style = "NEUTRAL", "bold black on yellow"
        else:
            word, badge_style = "—", "bold white"

        t.append("MARKET BIAS  ", style="bold cyan")
        t.append(f"  {word}  ", style=badge_style)
        t.append("    5d ", style="dim")
        t.append(bias5, style=PB._bias_style(bias5))
        t.append("  · 10d ", style="dim")
        t.append(bias10, style=PB._bias_style(bias10))
        # Phase 1F+ extra: 30d bias computed from cached SPY bars so the
        # Mode-1 badge sits next to the same three horizons as Mode 2.
        bias30, _ = PB._compute_30d_bias(data.get("spy_bars") or [])
        if bias30 != "—":
            t.append("  · 30d ", style="dim")
            t.append(bias30, style=PB._bias_style(bias30))
        t.append("  · conf ", style="dim")
        t.append(conf.upper(), style=PB._confidence_style(conf))
        t.append(f"    age {age}", style="yellow" if stale else "dim")
        if stale:
            t.append("  STALE", style="bold yellow")
        return Panel(t, box=box.SIMPLE, padding=(0, 1))

    @staticmethod
    def market_forecast_strip(data: DataLayer) -> Panel:
        """
        Compact one-line MARKET FORECAST strip for Mode 1 / Monitor.
        Cache-only; safe with missing/stale artifacts.
        """
        f = data.get("market_forecast") or {}
        t = Text()
        if not f or f.get("_missing"):
            t.append("MARKET FORECAST ", style="bold cyan")
            t.append("research-only  ", style="dim")
            t.append("artifact missing — run: ", style="yellow")
            t.append(PB._forecast_run_hint(), style="dim")
            return Panel(t, box=box.SIMPLE, padding=(0,1))

        head = f.get("headline") or {}
        regime = head.get("current_regime") or "—"
        bias5  = head.get("bias_5d") or "—"
        bias10 = head.get("bias_10d") or "—"
        conf   = head.get("confidence") or "—"
        sec    = f.get("sector_rotation") or {}
        leaders   = list(sec.get("leading") or [])
        improving = list(sec.get("improving") or [])
        weak      = list(sec.get("weakening") or [])
        defens    = list(sec.get("defensive") or [])
        # If no Leading sectors, fall back to Improving — and relabel as such
        # so the strip doesn't contradict the "leading sectors 0" invalidation.
        ld_show = (leaders or improving)[:2]
        ld_label = "Leaders" if leaders else "Improving"
        wk_show = (weak or defens)[:2]
        sf = f.get("strategy_favorability") or {}
        # Research posture from ALPHA_DISCOVERY (neutral language; no strategy abbreviations)
        ad = sf.get("ALPHA_DISCOVERY") or {}
        ad_stance = ad.get("stance", "")

        age = f.get("_age_short") or "?"
        stale = f.get("_stale")
        anchor = f.get("anchor_date") or "—"
        freshness = f.get("data_freshness_status") or ""
        anchor_warn = f.get("anchor_warning")

        breached = bool(head.get("invalidation_breached"))
        low_conf = str(conf or "").lower() == "low"
        t.append("MARKET FORECAST ", style="bold cyan")
        t.append("research-only ", style="dim")
        t.append(f"· anchor {anchor}", style="yellow" if anchor_warn else "dim")
        t.append(f" · age {age}", style="yellow" if stale else "dim")
        if stale:
            t.append(" STALE", style="bold yellow")
        if anchor_warn:
            t.append(" ⚠ANCHOR", style="bold yellow")
        elif freshness in ("stale", "behind", "missing"):
            t.append(f" ⚠{freshness}", style="bold yellow")
        # Phase 1F+: replace the small ⚠BREACH chip with the consolidated
        # FRAGILE / CONFLICTED badge so Mode 1 reads the same headline
        # state as Mode 2.
        if breached and low_conf:
            t.append(" FRAGILE", style="bold white on red")
        elif breached or low_conf:
            t.append(" CONFLICTED", style="bold black on yellow")
        t.append("  ")
        t.append(f"5d: {regime} / {bias5}", style=PB._bias_style(bias5))
        t.append(" · 10d ", style="dim")
        t.append(str(bias10), style=PB._bias_style(bias10))
        # Phase 1F+ extra: dashboard-side 30d bias from cached SPY bars.
        bias30, ret30 = PB._compute_30d_bias(data.get("spy_bars") or [])
        if bias30 != "—":
            t.append(" · 30d ", style="dim")
            t.append(str(bias30), style=PB._bias_style(bias30))
        t.append(" · conf ", style="dim")
        t.append(str(conf).upper(), style=PB._confidence_style(conf))
        if ld_show or wk_show:
            t.append("  | ", style="dim")
            if ld_show:
                t.append(f"{ld_label} ", style="dim")
                t.append(",".join(ld_show), style="green")
            if wk_show:
                t.append("  Weak ", style="dim")
                t.append(",".join(wk_show), style="red")
        # Research posture — neutral language, no strategy abbreviations
        if ad_stance:
            t.append("  | Research posture: ", style="dim")
            t.append(ad_stance, style=PB._stance_style(ad_stance))
        return Panel(t, box=box.SIMPLE, padding=(0,1))

    @staticmethod
    def market_forecast_context(data: DataLayer) -> Panel:
        """
        Compact research-context panel for Mode 2 / Research.
        Shows regime probabilities, leaders/laggards, strategy favorability,
        invalidation. Cache-only.
        """
        f = data.get("market_forecast") or {}
        t = Text()
        if not f or f.get("_missing"):
            t.append("Market forecast artifact missing.\n", style="yellow")
            t.append("Run: ", style="dim")
            t.append(PB._forecast_run_hint(), style="dim")
            return Panel(t, title="[bold]MARKET FORECAST[/] [dim]research-only[/]",
                         border_style="cyan", padding=(0,1))

        head = f.get("headline") or {}
        regime = head.get("current_regime") or "—"
        bias5  = head.get("bias_5d") or "—"
        bias10 = head.get("bias_10d") or "—"
        conf   = head.get("confidence") or "—"
        invalid = head.get("main_invalidation") or "—"
        breached = bool(head.get("invalidation_breached"))
        breach_reasons = list(head.get("invalidation_breach_reasons") or [])
        age = f.get("_age_short") or "?"
        stale = f.get("_stale")
        anchor = f.get("anchor_date") or "—"
        freshness = f.get("data_freshness_status") or ""
        anchor_warn = f.get("anchor_warning")

        # Phase 1F Task 1: when the headline is breached or confidence
        # is LOW, lead with the conflict state. "Bull Continuation" with
        # a small breach badge to the right was visually misleading on
        # 2026-05-15 — the dashboard read bullish before the operator's
        # eye reached the warning. Headline now says CONFLICTED/FRAGILE
        # first; the regime name is preserved alongside in dim style.
        _low_conf = str(conf or "").lower() == "low"
        if breached or _low_conf:
            badge = "FRAGILE" if (breached and _low_conf) else "CONFLICTED"
            t.append(badge, style="bold white on red")
            t.append(f"  · regime ", style="dim")
            t.append(str(regime), style="dim")
        else:
            t.append(f"{regime}", style=PB._bias_style(regime))
        if breached:
            t.append("  ⚠BREACHED", style="bold white on red")
        t.append("  · 5d ", style="dim"); t.append(bias5, style=PB._bias_style(bias5))
        t.append("  · 10d ", style="dim"); t.append(bias10, style=PB._bias_style(bias10))
        # Phase 1F+ extra: 30d bias computed on the fly from cached SPY
        # bars. Displayed only when we have enough bars (≥30) so the
        # absence of the column never misleads.
        bias30, ret30 = PB._compute_30d_bias(data.get("spy_bars") or [])
        if bias30 != "—":
            t.append("  · 30d ", style="dim")
            label = (f"{bias30} ({ret30:+.1f}%)"
                     if ret30 is not None else bias30)
            t.append(label, style=PB._bias_style(bias30))
        t.append("  · conf ", style="dim"); t.append(str(conf).upper(),
                                                     style=PB._confidence_style(conf))
        t.append(f"\nanchor {anchor}", style="yellow" if anchor_warn else "dim")
        if freshness:
            fs_style = "yellow" if freshness in ("stale", "behind", "missing") else "dim"
            t.append(f" · status {freshness}", style=fs_style)
        t.append(f" · age {age}\n", style="yellow" if stale else "dim")
        if anchor_warn:
            t.append(f"⚠ anchor: {_clip(str(anchor_warn), 160)}\n", style="bold yellow")
        if stale:
            t.append("[stale — re-run forecast]\n", style="bold yellow")
        if breached:
            t.append("Headline invalidation already breached: ", style="bold red")
            t.append(_clip("; ".join(breach_reasons) or "—", 180), style="red")
            t.append("\n")

        # Top regime probabilities (top 3) — but always surface the
        # remaining mass so the displayed numbers reconcile to 100%.
        probs = list(f.get("regime_probabilities") or [])
        probs_sorted = sorted(probs, key=lambda r: float(r.get("probability") or 0), reverse=True)
        if probs_sorted:
            top = probs_sorted[:3]
            rest_mass = sum(
                float(r.get("probability") or 0) for r in probs_sorted[3:]
            ) * 100
            t.append("Probability tilt: ", style="dim")
            for i, row in enumerate(top):
                name = row.get("regime", "—")
                p = float(row.get("probability") or 0) * 100
                t.append(f"{name} {p:.0f}%", style=PB._bias_style(name))
                if i < len(top) - 1:
                    t.append(" · ", style="dim")
            if rest_mass >= 0.5:
                t.append(f" · other {rest_mass:.0f}%", style="dim")
            t.append("\n")

        sec = f.get("sector_rotation") or {}
        leaders   = list(sec.get("leading") or [])
        improving = list(sec.get("improving") or [])
        weak      = list(sec.get("weakening") or [])
        defens    = list(sec.get("defensive") or [])
        if leaders:
            t.append("Leaders: ", style="dim"); t.append(", ".join(leaders), style="green"); t.append("  ")
        elif improving:
            t.append("Improving: ", style="dim"); t.append(", ".join(improving), style="green"); t.append("  ")
        if weak:
            t.append("Weak: ", style="dim"); t.append(", ".join(weak), style="red"); t.append("  ")
        if defens:
            t.append("Defensive: ", style="dim"); t.append(", ".join(defens), style="yellow")
        if leaders or improving or weak or defens:
            t.append("\n")

        sf = f.get("strategy_favorability") or {}
        if sf:
            _demote_stances = bool(breached or _low_conf)
            # Show only ALPHA_DISCOVERY (research board context); strategy-named
            # rows are omitted — no active strategies in research terminal.
            ad = sf.get("ALPHA_DISCOVERY") or {}
            if ad:
                label = "Research board (advisory)" if _demote_stances else "Research board"
                stance = ad.get("stance", "—")
                t.append(f"{label}: ", style="dim")
                style = PB._stance_style(stance)
                if _demote_stances and "green" in style:
                    style = "yellow"
                t.append(f"{stance}", style=style)
                if _demote_stances:
                    t.append("  · regime not confirmed", style="dim yellow")
                t.append("\n")

        if invalid and invalid != "—":
            # Substitute the cached-at-build VIX value with the live one so
            # the Trade Readiness panel and this invalidation line agree on
            # a single VIX snapshot per render.  The only float-formatted
            # "(now X.X)" in the invalidation strings is the VIX value;
            # integer "(now N)" (e.g., leading sectors) is left alone.
            live_vix = data.get("vix")
            if live_vix is not None:
                invalid = re.sub(
                    r"\(now \d+\.\d+\)",
                    f"(now {float(live_vix):.1f})",
                    str(invalid),
                )
            t.append("Invalidation: ", style="dim")
            t.append(_clip(str(invalid), 200), style="white")

        return Panel(t, title="[bold]MARKET FORECAST[/] [dim]research-only · probabilistic[/]",
                     border_style="cyan", padding=(0,1))

    @staticmethod
    def _journal_status_style(status: str) -> str:
        s = (status or "").lower()
        if s == "bullish":  return "bold green"
        if s == "bearish":  return "bold red"
        if s == "avoid":    return "bold red"
        if s == "watch":    return "bold yellow"
        if s == "neutral":  return "yellow"
        return "white"

    @staticmethod
    def _auto_research_note_panel(data: "DataLayer", ticker: str) -> Panel:
        """
        Cache-only fallback shown when no manual journal entry exists for
        ``ticker``.  Stitches a one-screen summary from the Stock Lens
        label/conclusion, Alpha Discovery tier+bucket, and Executive
        Gatekeeper status.  Read-only: never appends to research_notes.jsonl.
        Always badged ``[auto]`` so it cannot be mistaken for a deliberate
        human conclusion.
        """
        sym = ticker.upper()

        lens = data.get_stock_lens(sym) or {}
        lens_present = bool(lens) and not lens.get("_missing")
        lens_label   = str(lens.get("label") or "").strip() if lens_present else ""
        lens_concl   = str(lens.get("conclusion") or "").strip() if lens_present else ""
        lens_age     = str(lens.get("_age_short") or "").strip() if lens_present else ""
        lens_stale   = bool(lens.get("_stale")) if lens_present else False

        gk = data.get_executive_gatekeeper(sym) or {}
        gk_present  = bool(gk) and not gk.get("_missing")
        gk_status   = str(gk.get("final_status") or "").strip() if gk_present else ""

        # Alpha Discovery tier — read directly from cached board so we don't
        # need a per-ticker provider call.  Overlay (premarket) preferred,
        # nightly board as fallback.
        alpha_tier = ""
        alpha_bucket = ""
        try:
            import json as _json
            from pathlib import Path as _Path
            for p in (cfg.CACHE_DIR / "research" / "alpha_discovery_overlay_latest.json",
                      cfg.CACHE_DIR / "research" / "alpha_discovery_board_latest.json"):
                if not _Path(p).exists():
                    continue
                art = _json.loads(_Path(p).read_text(encoding="utf-8")) or {}
                for it in (art.get("items") or []):
                    if str(it.get("ticker") or "").upper() == sym:
                        alpha_tier = str(it.get("data_tier") or "").strip()
                        alpha_bucket = str(it.get("bucket") or "").strip()
                        break
                if alpha_tier:
                    break
        except Exception:
            alpha_tier = ""
            alpha_bucket = ""

        t = Text()
        # Badge — always visible, always says [auto] so the user can never
        # confuse this with a manual conclusion.
        t.append("  [auto]  ", style="reverse dim")
        t.append("  derived from cache", style="dim")
        if lens_age:
            t.append(f"  · lens age {lens_age}",
                     style="yellow" if lens_stale else "dim")
        t.append("\n")

        # Build the body line-by-line so empty sources collapse cleanly.
        any_body = False
        if lens_present and lens_label:
            t.append("LENS: ", style="bold dim")
            t.append(_clip(lens_label, 80), style=PB._bias_style(lens_label))
            if lens_concl and lens_concl.lower() != lens_label.lower():
                t.append("  ·  ", style="dim")
                t.append(_clip(lens_concl, 120), style="white")
            t.append("\n")
            any_body = True

        line_parts: List = []
        if alpha_tier:
            line_parts.append(("ALPHA", f"tier {alpha_tier}"
                               + (f" · {alpha_bucket}" if alpha_bucket else "")))
        if gk_present and gk_status:
            line_parts.append(("GATE", gk_status))
        if line_parts:
            for i, (lbl, val) in enumerate(line_parts):
                if i:
                    t.append("    ", style="dim")
                t.append(f"{lbl}: ", style="bold dim")
                style = "white"
                if lbl == "GATE":
                    style = PB._gatekeeper_status_style(val)
                t.append(_clip(str(val), 60), style=style)
            t.append("\n")
            any_body = True

        if not any_body:
            t.append(f"No cached research artefacts yet for {sym}.", style="dim")
            t.append("\n")
            t.append("Add a manual note: ", style="dim")
            t.append(f"research/research_journal.py add --ticker {sym} "
                     "--conclusion '...' --status watch", style="dim")

        return Panel(t,
                     title=f"[bold]LAST RESEARCH NOTE — {sym}[/] "
                           "[dim]research-only · [auto] · no manual entry[/]",
                     border_style="cyan", padding=(0, 1))

    @staticmethod
    def research_note_panel(data: DataLayer, state: "State") -> Panel:
        """
        Compact LAST RESEARCH NOTE panel for Mode 2 — surfaces the most
        recent journal entry for the selected ticker so prior conclusions
        aren't lost between sessions.  Cache-only (data/state JSONL); no
        provider calls.
        """
        ticker = (getattr(state, "ticker", None) or "").upper()
        if not ticker:
            t = Text()
            t.append("No ticker selected · journal note appears here", style="dim")
            return Panel(t, title="[bold]LAST RESEARCH NOTE[/] [dim]research-only[/]",
                         border_style="cyan", padding=(0, 1))

        note = data.get_research_note(ticker) or {}
        if note.get("_missing"):
            # No manual journal entry — render a compact [auto] summary
            # derived from cached Stock Lens / Alpha Discovery / Executive
            # Gatekeeper artefacts so the slot still carries something
            # useful per ticker.  This is read-only: nothing is appended to
            # research_notes.jsonl.  Manual notes always take precedence and
            # would have short-circuited above.
            return PB._auto_research_note_panel(data, ticker)

        # Phase 1F+ extra: severely stale manual notes (overdue > 7d and
        # never re-reviewed) get auto-archived from the headline slot —
        # the [auto] panel renders instead so the operator sees fresh
        # cache-derived context. The stale note isn't deleted; a dim
        # footer surfaces that a manual conclusion exists so the user
        # can decide to re-stamp it via the journal command.
        STALE_DAYS = 7  # how far past review_date counts as "severely stale"
        _days_until = note.get("_days_until_review")
        _reviewed_at = note.get("reviewed_at")
        _age_days = note.get("_age_days") or note.get("age_days")
        try:
            _age_days_f = float(_age_days) if _age_days is not None else None
        except (TypeError, ValueError):
            _age_days_f = None
        severely_stale = (
            _days_until is not None
            and _reviewed_at is None
            and (-_days_until) >= STALE_DAYS
        )
        # Also treat a note older than 30 days with no explicit review
        # date as stale — the conclusion has had a full month to decay.
        if (not severely_stale and _age_days_f is not None
                and _age_days_f >= 30 and not _reviewed_at):
            severely_stale = True

        if severely_stale:
            auto_panel = PB._auto_research_note_panel(data, ticker)
            # Suffix a footer noting the prior manual note exists. We
            # rebuild the panel so the title can carry the [stale] tag.
            footer = Text()
            footer.append("\nprior manual note: ", style="dim")
            footer.append(str(note.get("status") or "—").upper(), style="dim yellow")
            footer.append("  · age ", style="dim")
            footer.append(str(note.get("_age_short") or "?"), style="dim yellow")
            if _days_until is not None and _days_until < 0:
                footer.append(f" · overdue {-int(_days_until)}d", style="dim yellow")
            footer.append("  · re-stamp: ", style="dim")
            footer.append(
                f"research/research_journal.py add --ticker {ticker} "
                "--conclusion '...' --status watch",
                style="dim",
            )
            try:
                auto_panel.renderable.append(footer)
                # Tag the title so it's obvious why the [auto] view took over.
                auto_panel.title = (
                    f"[bold]LAST RESEARCH NOTE — {ticker}[/] "
                    "[dim]research-only · [auto] · prior manual note STALE[/]"
                )
            except Exception:
                pass
            return auto_panel

        status = str(note.get("status") or "—")
        conclusion = str(note.get("conclusion") or "—")
        next_action = note.get("next_action")
        ts_short = note.get("_age_short") or "?"
        review_date = note.get("review_date")
        days_until = note.get("_days_until_review")
        reviewed_at = note.get("reviewed_at")
        nid = note.get("note_id") or "?"

        t = Text()
        # Headline: status badge + age + note id
        t.append(f"  {status.upper()}  ", style=f"reverse {PB._journal_status_style(status)}")
        t.append(f"  age {ts_short}", style="dim")
        t.append(f" · {nid}", style="dim")
        if review_date:
            if days_until is not None and days_until < 0 and not reviewed_at:
                t.append(f"  · review OVERDUE ({-int(days_until)}d)", style="bold yellow")
            elif days_until is not None and days_until == 0 and not reviewed_at:
                t.append("  · review DUE today", style="bold yellow")
            elif days_until is not None and days_until > 0 and not reviewed_at:
                t.append(f"  · review in {int(days_until)}d", style="dim")
            elif reviewed_at:
                t.append("  · reviewed", style="dim green")
        t.append("\n")
        t.append(_clip(conclusion, 200), style="white")
        if next_action:
            t.append("\n")
            t.append("→ ", style="bold cyan")
            t.append(_clip(str(next_action), 180), style="white")
        return Panel(t, title=f"[bold]LAST RESEARCH NOTE — {ticker}[/] [dim]research-only[/]",
                     border_style="cyan", padding=(0, 1))

    @staticmethod
    def stock_lens_panel(data: DataLayer, state: "State") -> Panel:
        """
        Single-Stock Research Lens summary for Mode 2 / Research.
        Reads cache/research/stock_lens_<TICKER>_latest.json on demand.
        Never auto-runs the lens; if missing, shows the run command.
        """
        ticker = (getattr(state, "ticker", None) or "").upper()
        if not ticker:
            t = Text()
            t.append("No ticker selected · ", style="dim")
            t.append("/ to search · Stock Lens appears here", style="dim")
            return Panel(t, title="[bold]STOCK LENS[/] [dim]research-only[/]",
                         border_style="cyan", padding=(0,0))

        # Pending build (L key fired): show a building strip until the daemon
        # finishes and invalidates the per-ticker cache.  The next render
        # picks up the fresh artifact automatically.
        pending = getattr(state, "lens_pending_ticker", None)
        if pending and pending == ticker:
            t = Text()
            t.append(f"building for {ticker}…", style="bold yellow")
            t.append("  ·  ", style="dim")
            t.append("calls FMP/Alpaca · appends forward-tracking ledger",
                     style="dim")
            return Panel(t, title=f"[bold]STOCK LENS — {ticker}[/] [dim]research-only · build in progress[/]",
                         border_style="yellow", padding=(0, 0))

        lens = data.get_stock_lens(ticker) or {}
        if lens.get("_missing"):
            # Compact strip: a missing artifact never deserves a full panel —
            # the build_research layout sizes this slot at 4 rows total
            # (border + content + hint + border) and reclaims the rest for
            # AI Analysis / Alpha Discovery.  L triggers an in-app build.
            t = Text()
            t.append(f"missing for {ticker}", style="yellow")
            t.append("  ·  press ", style="dim")
            t.append("L", style="bold cyan")
            t.append(" to build · or run: ", style="dim")
            t.append(PB._stock_lens_run_hint(ticker), style="dim")
            err = getattr(state, "lens_last_error", None)
            if err:
                t.append(f"  ·  last error: {err}", style="red")
            return Panel(t, title=f"[bold]STOCK LENS — {ticker}[/] [dim]research-only[/]",
                         border_style="cyan", padding=(0,0))

        label   = lens.get("label") or "—"
        conf    = lens.get("confidence") or "—"
        horizon = lens.get("horizon_view") or {}
        view5   = horizon.get("5d")  or "—"
        view10  = horizon.get("10d") or "—"
        view20  = horizon.get("20d") or "—"
        layers  = lens.get("layers") or {}
        invals  = lens.get("invalidation") or []
        concl   = lens.get("conclusion") or "—"
        age     = lens.get("_age_short") or "?"
        stale   = lens.get("_stale")

        t = Text()
        # Headline.
        t.append(f"{ticker}", style="bold cyan")
        t.append("  ", style="dim")
        t.append(f"{label}", style=PB._bias_style(label))
        t.append("  · conf ", style="dim")
        t.append(str(conf).upper(), style=PB._confidence_style(conf))
        t.append(f"   age {age}", style="yellow" if stale else "dim")
        if stale:
            t.append(" STALE", style="bold yellow")
        # Phase 2B.2: catalyst-day awareness line — Lens stays research-only,
        # but flags that the read should not be acted on without a fresh
        # Gatekeeper.
        try:
            earn_rows = data.get("earnings") or []
        except Exception:
            earn_rows = []
        if _is_earnings_day(ticker, earn_rows):
            t.append("  · EARNINGS TODAY", style="bold yellow")
        t.append("\n")
        if _is_earnings_day(ticker, earn_rows):
            t.append("Earnings/catalyst day — require fresh Gatekeeper and "
                     "avoid stale reasons.\n", style="bold yellow")

        # Phase 1F+ #1: per-ticker fragility verdict. Mirrors the global
        # RESEARCH STATE strip but evaluated against this ticker's
        # specific lens (entry/options layers) instead of the
        # market-wide view. Cache-only — same evaluator the Mode 2 strip
        # uses, just with the lens populated.
        try:
            from core.fragility import evaluate_fragility
            forecast_artifact = data.get("market_forecast") or {}
            if forecast_artifact.get("_missing"):
                forecast_artifact = None
            alpha_artifact = data.get("alpha_discovery") or {}
            if alpha_artifact.get("_missing"):
                alpha_artifact = None
            try:
                posture_obj = build_research_bte(
                    universe_snapshot=data.get("universe_snap") or {},
                    regime=data.get("regime") or {},
                    vix=data.get("vix"),
                )
            except Exception:
                posture_obj = None
            tr_state = evaluate_fragility(
                forecast=forecast_artifact,
                posture=posture_obj,
                lens=lens,
                alpha=alpha_artifact,
                vix=data.get("vix"),
            )
            ts_styles = {
                "NORMAL":     "bold green",
                "CONFLICTED": "bold yellow",
                "FRAGILE":    "bold white on red",
                "STRESS":     "bold white on red",
                "UNKNOWN":    "bold dim",
            }
            t.append("TICKER STATE: ", style="bold dim")
            t.append(f"{tr_state.status} ",
                     style=ts_styles.get(tr_state.status, "bold yellow"))
            reason_preview = " · ".join(tr_state.reasons[:2])
            if reason_preview:
                t.append(f"· {_clip(reason_preview, 110)} ", style="white")
            t.append(" | ", style="dim")
            t.append(_clip(tr_state.action_hint, 80), style="dim")
            t.append("\n")
        except Exception:
            # Display-only: never break the lens panel because the
            # fragility overlay couldn't compute.
            pass

        # Compressed horizon line — only show horizons that diverge from the
        # headline label, packed onto a single line.  Avoids three near-
        # duplicate rows that pushed Posture/Options/Social out of view.
        diverging = [(span, v) for span, v in (("5d", view5), ("10d", view10), ("20d", view20))
                     if v and v != "—" and v != label]
        if diverging:
            for i, (span, v) in enumerate(diverging):
                if i:
                    t.append("  ·  ", style="dim")
                t.append(f"{span}: ", style="dim")
                t.append(str(v), style=PB._bias_style(v))
            t.append("\n")

        # Layer agreement — paired 2-per-row so all 8 layers stay on screen
        # within the lens panel's vertical budget.
        order = [
            ("market_regime",   "Market"),
            ("sector",          "Sector"),
            ("technicals",      "Tech"),
            ("entry_validator", "Entry"),
            ("alpha",           "Alpha"),
            ("posture",         "Posture"),
            ("options",         "Options"),
            ("social",          "Social"),
        ]
        t.append("Layers:\n", style="bold dim")

        def _render_layer_slot(key: str, lbl: str) -> int:
            """Render one layer slot inline; return rendered plain-text length
            so the second slot can be padded to a tabular column."""
            row = layers.get(key) or {}
            view = row.get("view") or "—"
            available = row.get("available", True)
            # :<8 keeps a clean column even for 7-char labels (Posture/Options).
            prefix = f"  {lbl:<8}"
            t.append(prefix, style="dim")
            if not available:
                t.append("n/a", style="dim")
                return len(prefix) + 3
            view_clipped = _clip(str(view), 20)
            t.append(view_clipped, style=PB._bias_style(view))
            out_len = len(prefix) + len(view_clipped)
            etf = row.get("etf")
            if etf:
                etf_part = f" ({etf})"
                t.append(etf_part, style="dim")
                out_len += len(etf_part)
            return out_len

        slot_col = 32  # nominal first-column width before the gutter
        pairs = [(order[i], order[i + 1]) for i in range(0, len(order), 2)]
        for left, right in pairs:
            left_len = _render_layer_slot(*left)
            pad = max(2, slot_col - left_len)
            t.append(" " * pad)
            _render_layer_slot(*right)
            t.append("\n")
        t.append("\n")

        # Conclusion.
        t.append("Conclusion: ", style="bold dim")
        t.append(_clip(str(concl), 220), style="white")
        t.append("\n")

        # Invalidation (top 1–2).
        if invals:
            t.append("Invalidation: ", style="bold dim")
            top = invals[0] if isinstance(invals, list) else invals
            t.append(_clip(str(top), 200), style="white")

        title = f"[bold]STOCK LENS — {ticker}[/] [dim]research-only[/]"
        return Panel(t, title=title, border_style="cyan", padding=(0,1))

    @staticmethod
    def _gatekeeper_run_hint(ticker: str) -> str:
        return (".venv/bin/python research/executive_gatekeeper_report.py "
                f"--ticker {ticker}")

    @staticmethod
    def _gatekeeper_status_style(status: str) -> str:
        s = (status or "").upper()
        if s == "PASS_RESEARCH":     return "bold green"
        if s == "WATCH":             return "bold yellow"
        if s == "BLOCK":             return "bold red"
        if s == "INSUFFICIENT_DATA": return "dim yellow"
        return "white"

    @staticmethod
    def _gatekeeper_status_label(status: str) -> str:
        s = (status or "").upper()
        if s == "PASS_RESEARCH":
            return "Research pass — still requires chart/entry review"
        if s == "WATCH":
            return "Watch only"
        if s == "BLOCK":
            return "Blocked by gate"
        if s == "INSUFFICIENT_DATA":
            return "Insufficient data"
        return "—"

    @staticmethod
    def executive_gatekeeper_panel(data: DataLayer, state: "State") -> Panel:
        """
        Compact Executive Gatekeeper V1 summary for Mode 2 / Research.

        Reads cache/research/executive_gatekeeper_<TICKER>_latest.json on
        demand.  Cache-only — never invokes the gatekeeper or any provider.
        If the artifact is missing, shows the run hint.  Always research-only;
        never trade approval — manual review still required.

        Phase 2B.2: stale Gatekeeper artifacts (older than the kind/earnings
        threshold) suppress the cached "Top reasons" / "Sizing" / "Hedge"
        block and surface the rerun command instead — May 5 reasons must
        never be presented as current on an earnings day.
        """
        ticker = (getattr(state, "ticker", None) or "").upper()
        if not ticker:
            t = Text()
            t.append("No ticker selected · ", style="dim")
            t.append("Executive Gatekeeper appears here", style="dim")
            return Panel(t, title="[bold]EXECUTIVE GATEKEEPER[/] [dim]research-only[/]",
                         border_style="cyan", padding=(0, 0))

        # Earnings calendar already lives on the DataLayer (Mode 3 earnings
        # wall reads the same field).  No new provider call.
        earn_rows = data.get("earnings") or []
        earnings_today = _is_earnings_day(ticker, earn_rows)
        gk = data.get_executive_gatekeeper(
            ticker,
            is_earnings_day=earnings_today,
            is_intraday_selected=True,
        ) or {}
        if gk.get("_missing"):
            t = Text()
            t.append(f"Executive Gatekeeper missing for {ticker}", style="yellow")
            t.append("  ·  run: ", style="dim")
            t.append(PB._gatekeeper_run_hint(ticker), style="dim")
            return Panel(t, title=f"[bold]EXECUTIVE GATEKEEPER — {ticker}[/] [dim]research-only[/]",
                         border_style="cyan", padding=(0, 0))

        status   = str(gk.get("final_status") or "—")
        conf     = str(gk.get("confidence") or "—")
        sizing   = str(gk.get("sizing_guidance") or "")
        reasons  = list(gk.get("main_reasons") or [])
        blocking = list(gk.get("blocking_reasons") or [])
        hedge    = gk.get("hedge_suggestion")
        age      = gk.get("_age_short") or "?"
        fresh    = gk.get("_freshness") or {}
        stale    = bool(fresh.get("stale") or gk.get("_stale"))
        warn     = bool(fresh.get("warn"))
        thresh_label = str(fresh.get("threshold_label") or "normal")
        stale_reasons = list(fresh.get("stale_reasons") or [])

        t = Text()
        # Headline: status badge + confidence + age.
        if stale:
            badge_status = "STALE"
            badge_style  = "bold yellow"
            badge_label  = "Stale Gatekeeper — rerun required"
        else:
            badge_status = status
            badge_style  = PB._gatekeeper_status_style(status)
            badge_label  = PB._gatekeeper_status_label(status)
        t.append(f"  {badge_status}  ", style=f"reverse {badge_style}")
        t.append("  ", style="dim")
        t.append(badge_label, style=badge_style)
        t.append("\n")
        t.append("conf ", style="dim")
        t.append(conf.upper(), style=PB._confidence_style(conf))
        age_style = "bold yellow" if stale else ("yellow" if warn else "dim")
        t.append(f"   age {age}", style=age_style)
        if stale:
            t.append(" STALE", style="bold yellow")
        elif warn:
            t.append(" WARN", style="yellow")
        if earnings_today:
            t.append("  · earnings-day threshold (6h)", style="bold yellow")
        elif thresh_label == "intraday":
            t.append("  · intraday warn 4h", style="dim")
        t.append("   manual review required", style="dim")
        t.append("\n")

        if stale:
            # Suppress the cached verdict + reasons block.  Surfacing
            # 15-day-old "Top reasons" as if they were current is exactly
            # what Phase 2B.2 exists to prevent.
            t.append("Executive Gatekeeper stale — rerun required\n",
                     style="bold yellow")
            if earnings_today:
                t.append("Earnings/catalyst day — require fresh Gatekeeper "
                         "and avoid stale reasons.\n", style="bold yellow")
            if stale_reasons:
                t.append("Why stale: ", style="dim")
                t.append(_clip(", ".join(stale_reasons), 160), style="white")
                t.append("\n")
            t.append("Rerun: ", style="bold dim")
            t.append(PB._gatekeeper_run_hint(ticker), style="white")
            t.append("\n", style="dim")
        else:
            if blocking:
                t.append("Blocking:\n", style="bold red")
                for r in blocking[:3]:
                    t.append("  · ", style="red")
                    t.append(_clip(str(r), 180), style="white")
                    t.append("\n")

            if reasons:
                t.append("Top reasons:\n", style="bold dim")
                for r in reasons[:3]:
                    t.append("  · ", style="dim")
                    t.append(_clip(str(r), 180), style="white")
                    t.append("\n")

            if sizing:
                t.append("Sizing: ", style="bold dim")
                t.append(_clip(sizing, 200), style="white")
                t.append("\n")

            if hedge:
                t.append("Hedge/tail-risk (research-only): ", style="bold dim")
                t.append(_clip(str(hedge), 220), style="white")

            if earnings_today:
                t.append("\nEarnings/catalyst day — verify the Gatekeeper "
                         "reflects today's setup before acting.",
                         style="bold yellow")
            elif warn:
                t.append("\nIntraday warn — consider a midday rerun: ", style="dim")
                t.append(PB._gatekeeper_run_hint(ticker), style="dim")

        title = (f"[bold]EXECUTIVE GATEKEEPER — {ticker}[/] "
                 f"[dim]research-only · manual review required[/]")
        return Panel(t, title=title, border_style="cyan", padding=(0, 1))

    @staticmethod
    def market_forecast_detailed(data: DataLayer) -> Panel:
        """
        Detailed Market Forecast diagnostics for Mode 3 / Risk.

        Two-column layout:
          LEFT  — regime probabilities, factor contributions
          RIGHT — sector leadership, strategy favorability, validation

        Cache-only; no provider calls.
        """
        f = data.get("market_forecast") or {}
        if not f or f.get("_missing"):
            t = Text("Market forecast artifact missing.\nRun: ", style="yellow")
            t.append(PB._forecast_run_hint(), style="dim")
            return Panel(t, title="[bold]MARKET FORECAST — DETAIL[/] [dim]research-only[/]",
                         border_style="cyan", padding=(0,1))

        head = f.get("headline") or {}
        probs = list(f.get("regime_probabilities") or [])
        probs_sorted = sorted(probs, key=lambda r: float(r.get("probability") or 0), reverse=True)
        sec = f.get("sector_rotation") or {}
        vol = f.get("volatility") or {}
        breadth = f.get("breadth") or {}
        factors = f.get("factor_contributions") or {}
        dq = f.get("data_quality") or {}
        sf = f.get("strategy_favorability") or {}
        val = data.get("market_forecast_validation") or {}
        age = f.get("_age_short") or "?"
        stale = f.get("_stale")

        # ── header (spans full width) ────────────────────────────────────
        header = Text()
        regime = head.get("current_regime") or "—"
        bias5  = head.get("bias_5d") or "—"
        bias10 = head.get("bias_10d") or "—"
        conf   = head.get("confidence") or "—"
        invalid = head.get("main_invalidation") or ""
        anchor = f.get("anchor_date") or "—"
        anchor_warn = f.get("anchor_warning")
        freshness = f.get("data_freshness_status") or ""
        breached = bool(head.get("invalidation_breached"))
        low_conf = str(conf or "").lower() == "low"

        # Phase 1F+: header reads the consolidated FRAGILE / CONFLICTED
        # state first; the regime name is preserved in dim style on the
        # right. Matches the Mode 2 panel so all three modes share one
        # vocabulary.
        if breached and low_conf:
            header.append("FRAGILE", style="bold white on red")
            header.append("   regime ", style="dim"); header.append(str(regime), style="dim")
        elif breached or low_conf:
            header.append("CONFLICTED", style="bold black on yellow")
            header.append("   regime ", style="dim"); header.append(str(regime), style="dim")
        else:
            header.append(f"{regime}", style=PB._bias_style(regime))
        header.append("   5d ", style="dim"); header.append(bias5, style=PB._bias_style(bias5))
        header.append("  10d ", style="dim"); header.append(bias10, style=PB._bias_style(bias10))
        # 30d computed from cached SPY bars; suppressed when bars are
        # missing so the header doesn't lie.
        bias30, ret30 = PB._compute_30d_bias(data.get("spy_bars") or [])
        if bias30 != "—":
            header.append("  30d ", style="dim")
            label = (f"{bias30} ({ret30:+.1f}%)"
                     if ret30 is not None else bias30)
            header.append(label, style=PB._bias_style(bias30))
        header.append("  conf ", style="dim")
        header.append(str(conf).upper(), style=PB._confidence_style(conf))
        header.append(f"   age {age}", style="yellow" if stale else "dim")
        if stale:
            header.append(" STALE", style="bold yellow")
        header.append("\n")
        header.append(f"anchor {anchor}", style="yellow" if anchor_warn else "dim")
        if freshness:
            fs_style = "yellow" if freshness in ("stale", "behind", "missing") else "dim"
            header.append(f"  status {freshness}", style=fs_style)
        if anchor_warn:
            header.append(f"\n⚠ {_clip(str(anchor_warn), 200)}", style="bold yellow")
        if breached:
            breach_reasons = list(head.get("invalidation_breach_reasons") or [])
            header.append("\n", style="dim")
            header.append("⚠ Headline invalidation breached: ", style="bold red")
            header.append(_clip("; ".join(breach_reasons) or "—", 180), style="red")

        # ── LEFT column: probabilities + factor contributions ────────────
        left = Text()
        left.append("Regime probabilities\n", style="bold dim")
        for row in probs_sorted:
            name = row.get("regime", "—")
            p = float(row.get("probability") or 0) * 100
            bar_w = 12
            f_w = max(0, min(int(p / 100 * bar_w), bar_w))
            color = PB._bias_style(name) or "white"
            left.append(f"  {str(name)[:24]:<24}", style="white")
            left.append(f"{p:5.1f}%  ", style="white")
            left.append("█" * f_w, style=color)
            left.append("░" * (bar_w - f_w) + "\n", style="dim")

        bull = list(factors.get("bullish") or [])
        bear = list(factors.get("bearish") or [])
        if bull or bear:
            left.append("\nFactor contributions\n", style="bold dim")
            if bull:
                left.append("  bullish: ", style="dim")
                left.append(_clip("; ".join(bull[:5]), 200), style="green")
                left.append("\n")
            if bear:
                left.append("  bearish: ", style="dim")
                left.append(_clip("; ".join(bear[:5]), 200), style="red")
                left.append("\n")

        # Volatility / breadth (also belongs to "factors" side).
        vix = vol.get("vix")
        vix_avg20 = vol.get("vix_avg_20")
        rv = vol.get("spy_realized_vol_20d_ann")
        sb20 = breadth.get("sector_breadth_pct_above_ma20")
        sb50 = breadth.get("sector_breadth_pct_above_ma50")
        if any(v is not None for v in (vix, rv, sb20, sb50)):
            left.append("\nVolatility / breadth\n", style="bold dim")
        if vix is not None:
            warn = ""
            if isinstance(vix, (int, float)) and vix >= 22:
                warn = "  ⚠ VIX expansion"
            elif isinstance(vix, (int, float)) and vix_avg20 and vix > vix_avg20 * 1.15:
                warn = "  ⚠ VIX above 20d avg"
            left.append(f"  VIX {vix}", style="white")
            if vix_avg20 is not None:
                left.append(f"  20d avg {float(vix_avg20):.2f}", style="dim")
            if warn:
                left.append(warn, style="bold yellow")
            left.append("\n")
        if rv is not None:
            left.append(f"  realized vol 20d ann {float(rv):.2f}%\n", style="dim")
        if sb20 is not None or sb50 is not None:
            left.append("  breadth ", style="dim")
            if sb20 is not None:
                left.append(f">ma20 {float(sb20)*100:.0f}%", style="dim")
            if sb50 is not None:
                if sb20 is not None:
                    left.append("  ", style="dim")
                left.append(f">ma50 {float(sb50)*100:.0f}%", style="dim")
            left.append("\n")

        # ── RIGHT column: sector leadership + strategy + validation ──────
        right = Text()
        leaders = list(sec.get("leading") or [])
        improving = list(sec.get("improving") or [])
        weak = list(sec.get("weakening") or [])
        defens = list(sec.get("defensive") or [])
        right.append("Sector leadership\n", style="bold dim")
        right.append("  Leading:   ", style="dim")
        right.append(", ".join(leaders) or "—", style="green"); right.append("\n")
        right.append("  Improving: ", style="dim")
        right.append(", ".join(improving) or "—", style="green"); right.append("\n")
        right.append("  Weakening: ", style="dim")
        right.append(", ".join(weak) or "—", style="red"); right.append("\n")
        right.append("  Defensive: ", style="dim")
        right.append(", ".join(defens) or "—", style="yellow"); right.append("\n")

        if sf:
            _demote = bool(breached or low_conf)
            sf_header = ("Research posture (advisory)" if _demote else "Research posture")
            right.append(f"\n{sf_header}\n", style="bold dim")
            # Show only ALPHA_DISCOVERY research context; strategy-named rows
            # (VOYAGER/SNIPER_V6/SHORT_A) are omitted — no active strategies.
            ad_row = sf.get("ALPHA_DISCOVERY") or {}
            if ad_row:
                stance = ad_row.get("stance", "—")
                reason = ad_row.get("reason", "")
                right.append("  Research board    ", style="dim")
                style = PB._stance_style(stance)
                if _demote and "green" in style:
                    style = "yellow"
                right.append(f"{stance:<10}", style=style)
                if reason:
                    right.append(_clip(reason, 60), style="dim")
                right.append("\n")
            if _demote:
                right.append("  regime not confirmed — advisory only\n",
                             style="dim yellow")

        if invalid and invalid != "—":
            # Pin VIX "(now X.X)" to the live snapshot — matches the single
            # VIX value the rest of the render uses.
            live_vix = data.get("vix")
            if live_vix is not None:
                invalid = re.sub(
                    r"\(now \d+\.\d+\)",
                    f"(now {float(live_vix):.1f})",
                    str(invalid),
                )
            right.append("\nInvalidation\n", style="bold dim")
            right.append(f"  {_clip(str(invalid), 200)}\n", style="white")

        # Validation block (forward tracking note when available).
        right.append("\nValidation\n", style="bold dim")
        if val and not val.get("_missing"):
            verdict = (val.get("verdict") or {}).get("verdict") or "—"
            v_age = val.get("_age_short") or "?"
            right.append("  ", style="dim")
            right.append(str(verdict), style="yellow")
            right.append(f"   age {v_age}\n", style="dim")
            flags = (val.get("verdict") or {}).get("flags") or []
            for fl in flags[:2]:
                right.append("  ⚠ ", style="yellow")
                right.append(_clip(str(fl), 80) + "\n", style="dim")
        else:
            v_status = f.get("validation_status") or "not validated"
            right.append(f"  {v_status}\n", style="dim")

        # Data quality footnote (kept on RIGHT side as a compact line).
        miss = list(dq.get("missing_layers") or [])
        spy_bars = dq.get("spy_bars")
        sec_avail = dq.get("sector_frames_available")
        right.append("\nData quality\n", style="bold dim")
        right.append(f"  spy bars {spy_bars}  sector frames {sec_avail}\n", style="dim")
        if miss:
            right.append(f"  missing: {', '.join(miss)}\n", style="yellow")

        # ── compose: header on top, two columns below ─────────────────────
        grid = Table.grid(expand=True)
        grid.add_column(ratio=1)
        grid.add_column(ratio=1)
        grid.add_row(left, right)

        body = Group(header, Text(""), grid)
        return Panel(
            body,
            title="[bold]MARKET FORECAST — DETAIL[/] [dim]research-only · probabilistic[/]",
            border_style="cyan", padding=(0, 1),
        )

    @staticmethod
    def forecast_forward_status(data: DataLayer) -> Panel:
        """
        Phase 5: compact read-only forward-tracking status.

        Reads cache-only summaries written by run_research_cycle.sh (or the
        forward-tracking report scripts).  Never runs the resolver itself.
        """
        fc = data.get("forecast_forward_summary") or {}
        sl = data.get("stock_lens_forward_summary") or {}
        t = Text()
        if (not fc or fc.get("_missing")) and (not sl or sl.get("_missing")):
            t.append("Forward tracking summary missing.\n", style="yellow")
            t.append("Run: ", style="dim")
            t.append("./scripts/run_research_cycle.sh resolve", style="dim")
            return Panel(t, title="[bold]FORWARD TRACKING[/] [dim]research-only[/]",
                         border_style="cyan", padding=(0,1))

        # Phase 1F+: when the *current* forecast is breached or LOW
        # confidence, the forward-tracking hit rates measure historical
        # discipline, not present validity. Surface that explicitly so
        # the operator doesn't read "spy 5d hit 60%" as confirmation that
        # today's call should be trusted.
        try:
            mf = data.get("market_forecast") or {}
            mf_head = mf.get("headline") or {}
            mf_breached = bool(mf_head.get("invalidation_breached"))
            mf_low = str(mf_head.get("confidence") or "").lower() == "low"
            if not mf.get("_missing") and (mf_breached or mf_low):
                badge = ("FRAGILE" if (mf_breached and mf_low)
                         else "CONFLICTED")
                t.append(f"  {badge}  ", style="bold white on red"
                         if badge == "FRAGILE" else "bold black on yellow")
                t.append(" current forecast is ", style="dim")
                t.append("breached" if mf_breached else "LOW confidence",
                         style="bold yellow")
                t.append(" — hit rates below reflect prior calls, not today\n",
                         style="dim")
        except Exception:
            pass

        # Forecast block.
        t.append("Forecast: ", style="bold dim")
        if fc and not fc.get("_missing"):
            agg = fc.get("matured_aggregate") or {}
            n_total = fc.get("snapshots_total", 0)
            n_mat = fc.get("snapshots_matured", 0)
            n_open = fc.get("snapshots_open", 0)
            age = fc.get("_age_short") or "?"
            hr5 = agg.get("spy_5d_hit_rate")
            hr10 = agg.get("spy_10d_hit_rate")
            t.append(f"{n_total} snapshots · matured {n_mat} · open {n_open}", style="white")
            # Phase 1F+: flag stale summaries so an old hit-rate doesn't
            # look fresh. _stale is set by DataLayer; if absent fall back
            # to a 48h heuristic on the timestamp string.
            fc_stale = bool(fc.get("_stale"))
            t.append(f"   age {age}",
                     style="bold yellow" if fc_stale else "dim")
            if fc_stale:
                t.append(" STALE", style="bold yellow")
            t.append("\n")
            def _hr(v):
                if v is None: return "—"
                return f"{v*100:.0f}%"
            def _hr_style(v):
                if v is None: return "dim"
                return "green" if v >= 0.55 else ("yellow" if v >= 0.45 else "red")
            t.append("  spy 5d hit ", style="dim")
            t.append(_hr(hr5), style=_hr_style(hr5))
            t.append("   10d hit ", style="dim")
            t.append(_hr(hr10), style=_hr_style(hr10))
            sb = fc.get("sector_basket") or {}
            if sb.get("n_with_basket"):
                spread = sb.get("avg_top_minus_bottom_5d_pct")
                if spread is not None:
                    t.append(f"   sector 5d spread {spread:+.2f}pp", style="dim")
            t.append("\n")
        else:
            t.append("no summary on disk\n", style="yellow")

        # Stock lens block.
        t.append("Stock Lens: ", style="bold dim")
        if sl and not sl.get("_missing"):
            n_total = sl.get("snapshots_total", 0)
            n_mat = sl.get("snapshots_matured", 0)
            n_open = sl.get("snapshots_open", 0)
            age = sl.get("_age_short") or "?"
            t.append(f"{n_total} calls · matured {n_mat} · open {n_open}", style="white")
            t.append(f"   age {age}\n", style="dim")
            by_conf = sl.get("by_confidence") or {}
            high = by_conf.get("high") or {}
            if high.get("n"):
                hr5 = high.get("hit_rate_5d")
                rel5 = high.get("avg_rel_spy_5d_pct")
                def _hr(v):
                    if v is None: return "—"
                    return f"{v*100:.0f}%"
                t.append(f"  high-conf n={high.get('n')}  ", style="dim")
                if hr5 is not None:
                    t.append(f"5d hit {_hr(hr5)}  ", style="dim")
                if rel5 is not None:
                    t.append(f"rel-spy {rel5:+.2f}pp", style="dim")
                t.append("\n")
        else:
            t.append("no summary on disk\n", style="yellow")

        falses = list((fc or {}).get("false_confidence_examples") or [])
        if falses:
            t.append(f"  false-confidence (high-conf misses): {len(falses)} on file",
                     style="dim")

        return Panel(t, title="[bold]FORWARD TRACKING[/] [dim]research-only · honest summary[/]",
                     border_style="cyan", padding=(0,1))

    # ── WEEKLY REVIEW strip (Phase 8B — cache-only) ──────────────────────────
    @staticmethod
    def weekly_review_strip(data: DataLayer) -> Panel:
        """
        One-line WEEKLY REVIEW strip for Mode 3.  Reads
        cache/research/weekly_review_latest.json (built by
        ``./scripts/run_research_cycle.sh weekly-review``).  Cache-only —
        never runs the review itself and never calls a provider.
        """
        wr = data.get_weekly_review() or {}
        t = Text()
        t.append("WEEKLY REVIEW ", style="bold cyan")
        t.append("research-only  ", style="dim")
        if wr.get("_missing"):
            t.append("artifact missing — run: ", style="yellow")
            t.append("./scripts/run_research_cycle.sh weekly-review", style="dim")
            return Panel(t, box=box.SIMPLE, padding=(0, 1))

        counts = wr.get("counts") or {}
        hcm = int(counts.get("high_conf_misses", 0))
        nd  = int(counts.get("notes_due", 0))
        wc  = int(counts.get("worst_calls", 0))
        bc  = int(counts.get("best_calls", 0))
        lm  = int(counts.get("lens_matured", 0))
        fm  = int(counts.get("forecast_matured", 0))
        as_of = wr.get("as_of") or "—"
        age   = wr.get("_age_short") or "?"

        t.append(f"as_of {as_of}", style="dim")
        t.append(f" · age {age}  ", style="dim")
        t.append(f"{hcm} high-conf misses",
                 style=("bold red" if hcm > 0 else "green"))
        t.append("  · ", style="dim")
        t.append(f"{nd} notes due",
                 style=("bold yellow" if nd > 0 else "dim"))
        t.append("  · ", style="dim")
        t.append(f"matured lens {lm} fc {fm}", style="dim")
        t.append("  · ", style="dim")
        t.append(f"best {bc} / worst {wc}", style="dim")
        t.append("   run: ", style="dim")
        t.append("./scripts/run_research_cycle.sh weekly-review", style="dim")
        return Panel(t, box=box.SIMPLE, padding=(0, 1))

    # ── WHAT CHANGED strip (Phase 7 — cache-only research delta) ─────────────
    @staticmethod
    def what_changed_strip(data: DataLayer) -> Panel:
        """
        Compact "WHAT CHANGED" strip — reads cache/research/research_delta_
        latest.json (produced by research/research_delta.py).  Cache-only:
        never invokes the delta script and never makes provider calls.

        Empty/missing/baseline states all collapse to a one-line summary so
        the strip stays at its allocated 3 rows.
        """
        delta = data.get("research_delta") or {}
        t = Text()
        if not delta or delta.get("_missing"):
            t.append("WHAT CHANGED: ", style="bold cyan")
            t.append("research delta artifact missing  ", style="dim")
            t.append("· run: ./scripts/run_research_cycle.sh delta", style="dim")
            return Panel(t, box=box.SIMPLE, padding=(0, 1))

        if delta.get("baseline"):
            t.append("WHAT CHANGED: ", style="bold cyan")
            t.append("baseline created — no prior comparison ", style="dim")
            age = delta.get("_age_short") or "?"
            t.append(f"· age {age}", style="dim")
            return Panel(t, box=box.SIMPLE, padding=(0, 1))

        # Compose the headline summary.  We respect the script's pre-built
        # delta["headline"] when populated; otherwise fall back to per-section
        # counts so the line still says something useful.
        head = list(delta.get("headline") or [])
        ad = delta.get("alpha_discovery") or {}
        ao = delta.get("alpha_overlay") or {}
        st = delta.get("structural") or {}
        sa = delta.get("social_arb") or {}
        new_alpha   = len(ad.get("new") or []) + len(ao.get("new") or [])
        rm_alpha    = len(ad.get("removed") or []) + len(ao.get("removed") or [])
        up_alpha    = len(ad.get("upgrades") or []) + len(ao.get("upgrades") or [])
        dn_alpha    = len(ad.get("downgrades") or []) + len(ao.get("downgrades") or [])
        newly_ready = len(st.get("newly_ready") or [])
        social_new  = len(sa.get("new_leads") or [])

        if not head and not any((new_alpha, rm_alpha, up_alpha, dn_alpha,
                                 newly_ready, social_new)):
            t.append("WHAT CHANGED: ", style="bold cyan")
            age = delta.get("_age_short") or "?"
            t.append(
                f"No major research changes since last run · age {age}",
                style="dim",
            )
            return Panel(t, box=box.SIMPLE, padding=(0, 1))

        t.append("WHAT CHANGED  ", style="bold cyan")
        age = delta.get("_age_short") or "?"
        t.append(f"age {age} · ", style="dim")
        # Forecast bias/regime moves first — most consequential change.
        f = delta.get("market_forecast") or {}
        bits: List[str] = []
        if f.get("regime"):
            bits.append(f"regime {f['regime']['from']}→{f['regime']['to']}")
        if f.get("bias_5d"):
            bits.append(f"5d {f['bias_5d']['from']}→{f['bias_5d']['to']}")
        if f.get("strategy_favorability"):
            bits.append(f"strat×{len(f['strategy_favorability'])}")
        # Then alpha + structural counts.
        if new_alpha:   bits.append(f"alpha new+{new_alpha}")
        if up_alpha:    bits.append(f"up{up_alpha}")
        if dn_alpha:    bits.append(f"down{dn_alpha}")
        if rm_alpha:    bits.append(f"removed{rm_alpha}")
        if newly_ready: bits.append(f"struct READY+{newly_ready}")
        if social_new:  bits.append(f"social+{social_new}")
        # Needs-action count (e.g. lens missing for new alpha).
        needs = delta.get("needs_action") or []
        if needs:
            bits.append(f"needs:{len(needs)}")
        t.append(" · ".join(bits) if bits else "(no notable changes)", style="white")

        return Panel(t, box=box.SIMPLE, padding=(0, 1))

    # ── RESEARCH STATE strip (Phase 1F — cross-artifact fragility) ───────────
    @staticmethod
    def research_state_strip(state: "State", data: DataLayer) -> Panel:
        """
        Compact, single-line RESEARCH STATE: NORMAL / CONFLICTED / FRAGILE
        / STRESS / UNKNOWN. Reads cache-only artifacts (Market Forecast,
        Stock Lens for the selected ticker, Alpha Discovery, cached VIX)
        via core.fragility.evaluate_fragility. Display only — never
        affects scoring, governance, or execution.
        """
        from core.fragility import evaluate_fragility  # local import

        forecast = data.get("market_forecast") or {}
        if forecast.get("_missing"):
            forecast = None
        ticker = (state.ticker or "").upper() if state is not None else ""
        lens_obj: Optional[Dict[str, Any]] = None
        if ticker:
            try:
                lens_obj = data.get_stock_lens(ticker) or None
                if lens_obj and lens_obj.get("_missing"):
                    lens_obj = None
            except Exception:
                lens_obj = None

        alpha = data.get("alpha_discovery") or None
        if alpha and alpha.get("_missing"):
            alpha = None

        try:
            posture = build_research_bte(
                universe_snapshot=data.get("universe_snap") or {},
                regime=data.get("regime") or {},
                vix=data.get("vix"),
            )
        except Exception:
            posture = None

        result = evaluate_fragility(
            forecast=forecast,
            posture=posture,
            lens=lens_obj,
            alpha=alpha,
            vix=data.get("vix"),
        )

        status_styles = {
            "NORMAL":     "bold green",
            "CONFLICTED": "bold yellow",
            "FRAGILE":    "bold white on red",
            "STRESS":     "bold white on red",
            "UNKNOWN":    "bold dim",
        }
        style = status_styles.get(result.status, "bold yellow")
        t = Text()
        t.append("RESEARCH STATE: ", style="bold cyan")
        t.append(f"{result.status} ", style=style)
        # First two reasons fit on the one-line strip; the rest live in
        # the JSON output the operator can dump from the module.
        reasons_preview = " · ".join(result.reasons[:2])
        if reasons_preview:
            t.append(f"· {reasons_preview} ", style="white")
        t.append(" | ", style="dim")
        t.append("Action: ", style="dim")
        # F3: the fragility action hint (e.g. "entries permitted within sleeve
        # rules" for NORMAL) reads like a live go-signal.  Two corrections,
        # display-only — fragility logic is untouched:
        #   1. When the market is not in a REGULAR session, no entries are
        #      possible — say so instead of "entries permitted".
        #   2. Otherwise, this remains a paper-validation system (no live
        #      capital) — append that qualifier so the strip never implies a
        #      live entry is authorised.
        action_hint = result.action_hint
        try:
            _live_session = get_session_state() == SessionState.REGULAR
        except Exception:
            _live_session = False
        if not _live_session:
            action_hint = "market CLOSED — research only, no entries this session"
            hint_style = "dim"
        else:
            if "entries permitted" in action_hint.lower():
                action_hint = f"{action_hint} (paper-validation — no live entries)"
            hint_style = "white"
        t.append(action_hint, style=hint_style)
        return Panel(t, box=box.SIMPLE, padding=(0, 1))

    # ── NEXT ACTION strip (compact, derived from displayed state only) ───────
    @staticmethod
    def next_action(state: "State", data: DataLayer) -> Panel:
        """
        Compact NEXT ACTION line per mode.

        Derived from already-displayed state (paper readiness, alpha board,
        forecast, scanner results).  Never produces buy/sell signals — phrased
        as research/process actions only.
        """
        mode = getattr(state, "mode", M_MONITOR)
        t = Text()
        t.append("NEXT ACTION: ", style="bold cyan")

        if mode == M_MONITOR:
            hb = data.get("market_heartbeat") or {}
            sc = data.get("research_scanner") or {}
            hb_label = str(hb.get("heartbeat_label") or "")
            wl_size = int(sc.get("watchlist_size") or 0)
            if hb.get("_missing") and sc.get("_missing"):
                t.append(
                    "run market-heartbeat + research-scanner to populate panels",
                    style="dim",
                )
            elif hb_label in {"CORRECTION", "RISK_OFF"}:
                t.append(
                    f"MARKET HEARTBEAT: {hb_label} — defensive posture · review watchlist risk flags",
                    style="bold red",
                )
            elif wl_size > 0:
                t.append(
                    f"{wl_size} research watchlist results · review via stock-research-card · manual analysis only",
                    style="white",
                )
            else:
                t.append(
                    "RESEARCH_ONLY_MODE — run research-scanner for daily watchlist",
                    style="dim",
                )

        elif mode == M_RESEARCH:
            ticker = (getattr(state, "ticker", None) or "").upper()
            pending = getattr(state, "lens_pending_ticker", None)
            if pending and pending == ticker:
                t.append(
                    f"building Stock Lens for {ticker}… · panel updates when ready",
                    style="white",
                )
            elif ticker:
                lens = data.get_stock_lens(ticker) or {}
                if lens.get("_missing"):
                    t.append(
                        f"chart {ticker} · press L to build Stock Lens",
                        style="white",
                    )
                else:
                    t.append(
                        f"review {ticker} via Stock Lens · cross-check Alpha watch list",
                        style="white",
                    )
            else:
                t.append(
                    "chart Alpha watch names · / to select a ticker · L to build its Stock Lens",
                    style="white",
                )

        elif mode == M_RISK:
            f = data.get("market_forecast") or {}
            head = f.get("headline") or {}
            bias5 = str(head.get("bias_5d") or "").lower()
            breadth = f.get("breadth") or {}
            sb20 = breadth.get("sector_breadth_pct_above_ma20")
            try:
                sb20_v = float(sb20) if sb20 is not None else None
            except Exception:
                sb20_v = None
            if not f or f.get("_missing"):
                t.append("review intel · forecast artifact missing — run nightly research cycle", style="white")
            elif "construct" in bias5 or "bull" in bias5:
                if sb20_v is not None and sb20_v < 0.5:
                    t.append(
                        "review intel · forecast constructive but breadth weak",
                        style="white",
                    )
                else:
                    t.append("review intel · forecast constructive", style="white")
            elif "bear" in bias5 or "defens" in bias5:
                t.append("review intel · forecast defensive — check MCP audit + earnings wall", style="white")
            else:
                t.append("review intel · forecast mixed", style="white")

        elif mode == M_SCANNER:
            sc = data.get("research_scanner") or {}
            wl_size = int(sc.get("watchlist_size") or 0)
            alpha = data.get("alpha_discovery") or {}
            alpha_count = len(alpha.get("items") or [])
            if wl_size > 0:
                t.append(
                    f"{wl_size} research watchlist names · run stock-research-card for deep dive",
                    style="white",
                )
            elif alpha_count > 0:
                t.append(
                    f"no scanner results · {alpha_count} alpha candidates visible",
                    style="white",
                )
            else:
                t.append(
                    "run research-scanner to populate watchlist",
                    style="dim",
                )

        return Panel(t, box=box.SIMPLE, padding=(0, 1))


# ══════════════════════════════════════════════════════════════════════════════
# LAYOUT BUILDERS — one per mode
# ══════════════════════════════════════════════════════════════════════════════

def _layout_root(header, readiness, body, status_bar, next_action=None,
                 edge_banner=None) -> Layout:
    root = Layout()
    rows = [Layout(header, name="header", size=3)]
    if edge_banner is not None:
        rows.append(Layout(edge_banner, name="edge_banner", size=1))
    if readiness is not None:
        rows.append(Layout(readiness, name="readiness", size=3))
    rows.append(Layout(body, name="body"))
    if next_action is not None:
        rows.append(Layout(next_action, name="next_action", size=3))
    rows.append(Layout(status_bar, name="footer", size=2))
    root.split_column(*rows)
    return root

def _status_bar(state: Optional["State"] = None) -> Panel:
    t = Text(justify="center")
    base = ("[dim]1[/] Market  [dim]2[/] Watchlist  [dim]3[/] Intel  [dim]4[/] Research"
            "  [dim]s[/] Scan  [dim]/[/] Search  [dim]r[/] Refresh  [dim]q[/] Quit")
    if state is not None and state.mode == M_RESEARCH and not state.search_active:
        focus = state.research_focus or "auto"
        more  = state.alpha_show_more
        # "L" surfaces only when a ticker is selected, so the prompt matches
        # what the keystroke can actually do at that moment.
        lens_hint = "  [dim]L[/] build lens" if state.ticker else ""
        extra = (
            f"   ·   [dim]f[/] focus({focus})  "
            f"[dim]j/k[/] alpha row  "
            f"[dim]g/G[/] top/bottom  "
            f"[dim]m/-[/] show({more})  "
            f"[dim]0[/] reset"
            f"{lens_hint}"
        )
        t.append(base + extra, style="dim")
    else:
        t.append(base, style="dim")
    return Panel(t, box=box.SIMPLE, padding=(0,0))

def build_monitor(state,data,claude):
    alpha_symbols: set = set()
    for src in (data.get("alpha_discovery") or {}, data.get("alpha_discovery_overlay") or {}):
        for row in (src.get("items") or []):
            sym = str(row.get("ticker") or "").upper()
            if sym:
                alpha_symbols.add(sym)
    scanner_symbols: set = set()
    scan_results = data.get("scan_results") or {}
    for src_key in ("opportunities", "vetoed", "developing"):
        for row in (scan_results.get(src_key) or []):
            sym = str(row.get("ticker") or "").upper()
            if sym:
                scanner_symbols.add(sym)

    body = Layout()
    body.split_column(
        Layout(name="top",       size=11),
        Layout(name="mid",       size=10),
        Layout(name="bias",      size=3),
        Layout(name="forecast",  size=3),
        Layout(name="changed",   size=3),
        Layout(name="strip",     size=3),
        Layout(name="bot"),
    )
    body["top"].split_row(
        Layout(PB.regime(data),     name="regime",    ratio=3),
        Layout(PB.internals(data),  name="internals", ratio=4),
        Layout(PB.vix_gates(data),  name="vix",       ratio=3),
    )
    body["mid"].split_row(
        Layout(PB.market_heartbeat(data),    name="heartbeat", ratio=5),
        Layout(PB.research_watchlist(data),  name="watchlist", ratio=8),
    )
    body["bias"].update(PB.market_bias_strip(data))
    body["forecast"].update(PB.market_forecast_strip(data))
    body["changed"].update(PB.what_changed_strip(data))
    body["strip"].update(PB.top3_strip(data))
    body["bot"].update(PB.social_arb_radar(
        data,
        alpha_symbols=alpha_symbols,
        scanner_symbols=scanner_symbols,
        compact=True,
        max_lines=5,
    ))
    return _layout_root(PB.header(state,data,claude),
                        None,
                        body,
                        _status_bar(),
                        next_action=PB.next_action(state, data),
                        edge_banner=PB.selection_edge_banner(data))

def build_research(state, data, claude):
    """
    Mode 2 (Research) layout — stable ratios + explicit focus.

    Base ratio keeps BTE above Alpha in the sidebar.  Focus boosts one panel
    without starving the others.  Layout no longer flips silently when a
    ticker is selected; ticker selection only changes the *default* focus
    when state.research_focus is None.  (Social Arb radar now lives in
    Mode 1 / Monitor, alongside positions + account.)
    """
    focus = state.research_focus
    explicit_focus = focus is not None
    if focus is None and not state.ticker:
        # No ticker → default to "alpha" so the alpha board renders expanded.
        # When a ticker IS set we leave focus as None and let the base 4:6
        # ratios apply; the AI Analysis pane is text-clipped at ~80 chars and
        # gains nothing from auto-boosting to a larger share of the width.
        focus = "alpha"

    # Detect "analysis loaded but effectively empty" — Claude returned a
    # placeholder (insufficient bars, missing fundamentals).  Treat this the
    # same as no-ticker for layout purposes so the two sidebar panels
    # (BTE + Alpha) aren't crushed by a mostly-blank analysis.
    ana = state.analysis or {}
    analysis_sparse = (
        state.analysis is not None
        and ana.get("quality") in (None, 0)
        and str(ana.get("bias", "—")) in ("—", "", "None")
    )

    # Base ratios — sidebar-favored by default.  The AI Analysis content is
    # text with fixed clip widths (~80 chars per line in PB.research), so any
    # outer width beyond that is wasted whitespace.  Two sidebar panels
    # (BTE + Alpha) genuinely use the extra columns.
    a_ratio, s_ratio = 4, 6
    bte_ratio, alpha_ratio = 4, 6
    if focus == "analysis":
        # Explicit analysis focus boosts the analysis pane.  Capped at 6:4
        # because text clips already prevent fuller use of more width.
        a_ratio, s_ratio = 6, 4
        bte_ratio, alpha_ratio = 3, 7
    elif focus == "posture":
        a_ratio, s_ratio = 3, 7
        bte_ratio, alpha_ratio = 5, 5
    elif focus == "alpha":
        a_ratio, s_ratio = 3, 7
        bte_ratio, alpha_ratio = 2, 10

    # No-ticker OR sparse-analysis auto state: AI Analysis pane has nothing
    # useful to show, so reclaim that space for the sidebar.  Skipped only
    # when the user has *explicitly* chosen analysis focus (they accept the
    # blank pane to keep room for incoming data).
    if (not state.ticker or analysis_sparse) and not (explicit_focus and focus == "analysis"):
        a_ratio, s_ratio = 2, 8

    # Build BTE once so research_assist + alpha_discovery can cross-reference.
    snap   = data.get("universe_snap") or {}
    regime = data.get("regime") or {}
    vix    = data.get("vix")
    bte_out = build_research_bte(universe_snapshot=snap, regime=regime, vix=vix)
    bte_focus_symbols = {
        str(r.get("symbol") or "").upper()
        for r in (bte_out.focus_names or [])
        if r.get("symbol")
    }
    alpha_symbols: set = set()
    for src in (data.get("alpha_discovery") or {}, data.get("alpha_discovery_overlay") or {}):
        for row in (src.get("items") or []):
            sym = str(row.get("ticker") or "").upper()
            if sym:
                alpha_symbols.add(sym)
    scanner_symbols: set = set()
    scan_results = data.get("scan_results") or {}
    for src_key in ("opportunities", "vetoed", "developing"):
        for row in (scan_results.get(src_key) or []):
            sym = str(row.get("ticker") or "").upper()
            if sym:
                scanner_symbols.add(sym)

    # Predicate: will the research_assist panel collapse to its compact mode?
    # Mirrors the inline check in PB.research_assist (no_focus + no_liquid +
    # no_movers).  When true, the assist slot only needs ~9 lines (header +
    # bias/why/playbook/risk + footer note); the surplus is reclaimed for the
    # alpha board which actually has rows to render.
    posture_compact = (
        not bool(bte_out.focus_names or [])
        and not bool(snap.get("strategy_candidates") or [])
    )

    # Compact research-only Market Forecast strip — sized for the current
    # cache content.  Higher when content is rich, lower when missing/sparse,
    # so it never starves the main work area.
    forecast_obj = data.get("market_forecast") or {}
    forecast_size = 8 if forecast_obj and not forecast_obj.get("_missing") else 4

    # Stock Lens missing detection — drives the analysis-column collapse so
    # a 2-line "missing" notice does not consume half the analysis pane.
    # Pending builds (L key) are treated like missing for layout purposes so
    # the in-progress strip stays compact and AI Analysis keeps its space.
    lens_missing = False
    if state.ticker:
        lens_obj = data.get_stock_lens(state.ticker.upper()) or {}
        lens_missing = bool(lens_obj.get("_missing"))
    if state.ticker and state.lens_pending_ticker == state.ticker.upper():
        lens_missing = True

    # Executive Gatekeeper missing detection — when the artifact is missing
    # the panel collapses to a single-line "missing" hint so it does not
    # consume the analysis column.  Cache-only — never auto-runs.
    gk_missing = False
    if state.ticker:
        gk_obj = data.get_executive_gatekeeper(state.ticker.upper()) or {}
        gk_missing = bool(gk_obj.get("_missing"))

    # If Stock Lens is missing, push more room to the alpha board (which has
    # actionable rows) rather than letting the AI Analysis pane absorb the
    # extra lines (it clips at ~80 chars per line).
    effective_a_ratio = a_ratio
    effective_s_ratio = s_ratio
    if lens_missing and not (explicit_focus and focus == "analysis"):
        effective_a_ratio = max(3, a_ratio - 1)
        effective_s_ratio = s_ratio + 1

    body = Layout()
    body.split_column(
        Layout(PB.ticker_lookup(state, data), name="search",   size=5),
        Layout(PB.market_forecast_context(data),
                                              name="forecast", size=forecast_size),
        # Phase 1F Task 6: a single-line RESEARCH STATE strip that
        # combines Market Forecast, Posture, Lens, Alpha, and VIX into
        # a NORMAL / CONFLICTED / FRAGILE / STRESS / UNKNOWN verdict
        # plus an Action hint. Reads cache only.
        Layout(PB.research_state_strip(state, data),
                                              name="rstate",   size=3),
        # Phase 2B.1 follow-up: compact MCP audit one-liner — same
        # contract as the Risk-mode panel; cache-only read of
        # cache/research/mcp_analysis_latest.json.
        Layout(PB.mcp_audit_oneline(data),    name="mcp_strip", size=3),
        Layout(PB.what_changed_strip(data),   name="changed",  size=3),
        Layout(name="main"),
    )
    # Analysis column: when a ticker is selected, surface the cached Stock Lens
    # summary above the AI analysis so the user can see at a glance whether
    # the broader regime / lens supports the idea.  No ticker → AI analysis
    # only (the forecast context strip above already covers research context).
    if state.ticker:
        analysis_layout = Layout(name="analysis", ratio=effective_a_ratio)
        # Phase 8A: compact "LAST RESEARCH NOTE" strip pinned at the top of
        # the analysis column so prior manual conclusions surface as soon as
        # a ticker is selected — independent of whether a Lens is built.
        gk_size = 3 if gk_missing else 10
        if lens_missing:
            # Compact strip for the missing lens — fixed 4 rows (border + line
            # + run-hint + border).  AI Analysis takes the rest.
            analysis_layout.split_column(
                Layout(PB.research_note_panel(data, state),         name="note",  size=5),
                Layout(PB.stock_lens_panel(data, state),            name="lens",  size=4),
                Layout(PB.executive_gatekeeper_panel(data, state),  name="gate",  size=gk_size),
                Layout(PB.research(state, claude, data),                  name="ai_analysis"),
            )
        else:
            analysis_layout.split_column(
                Layout(PB.research_note_panel(data, state),         name="note",  size=5),
                Layout(PB.stock_lens_panel(data, state),            name="lens",  ratio=7),
                Layout(PB.executive_gatekeeper_panel(data, state),  name="gate",  size=gk_size),
                Layout(PB.research(state, claude, data),                  name="ai_analysis", ratio=5),
            )
    else:
        analysis_layout = Layout(PB.research(state, claude, data),
                                 name="analysis", ratio=effective_a_ratio)

    body["main"].split_row(
        analysis_layout,
        Layout(name="sidebar", ratio=effective_s_ratio),
    )

    # Assist slot sizing: compact posture (no focus/no candidates) gets a
    # fixed-height slot so the surplus rows go to the alpha board.
    if posture_compact:
        assist_layout = Layout(
            PB.research_assist(data, bte=bte_out, alpha_symbols=alpha_symbols),
            name="assist", size=9,
        )
    else:
        assist_layout = Layout(
            PB.research_assist(data, bte=bte_out, alpha_symbols=alpha_symbols),
            name="assist", ratio=bte_ratio,
        )

    body["main"]["sidebar"].split_column(
        assist_layout,
        Layout(
            PB.alpha_discovery(
                data,
                state=state,
                expanded=(focus == "alpha"),
                bte_focus_symbols=bte_focus_symbols,
                show_more_level=state.alpha_show_more,
                compact_detail=True,
            ),
            name="alpha", ratio=alpha_ratio,
        ),
    )
    return _layout_root(PB.header(state, data, claude),
                        None,
                        body,
                        _status_bar(state),
                        next_action=PB.next_action(state, data),
                        edge_banner=PB.selection_edge_banner(data))

def build_risk(state,data,claude):
    body = Layout()
    body.split_column(
        Layout(name="top",     size=10),
        Layout(name="forecast"),
        Layout(name="forward", size=7),
        Layout(name="weekly",  size=3),
        Layout(PB.mcp_audit_summary(data), name="mcp_audit", size=9),
        Layout(name="earn_row", size=9),
    )
    body["top"].update(PB.evidence_freshness(data))
    body["forecast"].update(PB.market_forecast_detailed(data))
    body["forward"].update(PB.forecast_forward_status(data))
    body["weekly"].update(PB.weekly_review_strip(data))
    body["earn_row"].split_row(
        Layout(PB.earnings(data), name="earn",  ratio=6),
        Layout(PB.macro(data),    name="macro", ratio=4),
    )
    return _layout_root(PB.header(state,data,claude),
                        None,
                        body,
                        _status_bar(),
                        next_action=PB.next_action(state, data),
                        edge_banner=PB.selection_edge_banner(data))

def build_scanner(state,data,claude):
    """
    Mode 4 (Research) — research pipeline view.

    Left: daily research watchlist + developing universe candidates.
    Right: alpha discovery board + universe readiness summary.
    """
    body = Layout()
    body.split_row(
        Layout(name="left",  ratio=11),
        Layout(name="right", ratio=9),
    )
    body["left"].split_column(
        Layout(PB.research_watchlist(data), name="watchlist"),
        Layout(PB.developing_soon(data),    name="devs",      size=12),
    )
    body["right"].split_column(
        Layout(
            PB.alpha_discovery(
                data,
                state=state,
                expanded=False,
                bte_focus_symbols=set(),
                show_more_level=state.alpha_show_more,
                compact_detail=True,
            ),
            name="alpha",
        ),
        Layout(PB.universe_readiness_summary(data), name="uni_sum", size=10),
    )
    return _layout_root(PB.header(state,data,claude),
                        None,
                        body,
                        _status_bar(),
                        next_action=PB.next_action(state, data),
                        edge_banner=PB.selection_edge_banner(data))

_BUILDERS = {M_MONITOR:build_monitor, M_RESEARCH:build_research,
             M_RISK:build_risk, M_SCANNER:build_scanner}


# ══════════════════════════════════════════════════════════════════════════════
# KEYBOARD HANDLER
# ══════════════════════════════════════════════════════════════════════════════

class KB:
    def __init__(self):
        self._fd  = sys.stdin.fileno()
        self._old = None
        try:
            self._old = termios.tcgetattr(self._fd)
            tty.cbreak(self._fd)
        except Exception:
            pass

    def read(self, timeout=0.05) -> Optional[str]:
        if self._old is None: return None
        try:
            if select.select([sys.stdin],[],[],timeout)[0]:
                return os.read(self._fd,1).decode("utf-8","ignore")
        except Exception: pass
        return None

    def restore(self):
        if self._old:
            try: termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old)
            except Exception: pass


# ══════════════════════════════════════════════════════════════════════════════
# INPUT HANDLER
# ══════════════════════════════════════════════════════════════════════════════

def handle_key(ch: str, state: State, data: DataLayer,
               claude: ClaudeAnalyzer) -> bool:
    """Return True if quit requested."""
    if state.search_active:
        if ch in ("\r","\n"):
            ticker = state.search_buf.strip().upper()
            # Reject obvious non-tickers up-front: must match length+charset,
            # and must not be a single repeated letter run of 4+ chars
            # (QQQQ, QQQQQ, AAAAA …).  QQQ at 3 chars is a real ETF (Invesco
            # QQQ Trust) and must be allowed.
            valid_shape = bool(re.match(r"^[A-Z]{1,5}$", ticker))
            if valid_shape and len(set(ticker)) == 1 and len(ticker) >= 4:
                valid_shape = False
            if valid_shape:
                state.ticker  = ticker
                state.analysis= None
                # Defer push_history until _run_analysis confirms the symbol
                # has real bars — keeps typos / non-existent tickers out of
                # the Recent list.
                state.mode    = M_RESEARCH
                threading.Thread(target=_run_analysis,
                                 args=(ticker,state,data,claude), daemon=True).start()
            state.search_active = False
            state.search_buf    = ""
        elif ch in ("\x7f","\x08"):
            state.search_buf = state.search_buf[:-1]
        elif ch == "\x1b":
            state.search_active = False; state.search_buf = ""
        elif ch.isalpha():
            state.search_buf = (state.search_buf+ch.upper())[:6]
        state.dirty = True
        return False

    # global hotkeys
    if ch == "q": return True
    if ch == "/": state.search_active=True; state.search_buf=""; state.dirty=True
    elif ch == "r":
        # Force-refresh: invalidate every cached key so the background
        # loop re-fetches on its next tick (≤5s), and mark dirty so the
        # screen redraws immediately afterward.
        data.invalidate_all()
        state.dirty = True
    elif ch in ("1","2","3","4"):
        new_mode = int(ch)
        if new_mode != state.mode:
            # Reset Mode-2 cursor + show-more on mode change so re-entry starts clean.
            if new_mode == M_RESEARCH:
                state.alpha_cursor = 0
                state.alpha_show_more = 0
        state.mode = new_mode
        state.dirty = True
    elif ch in ("s","S") and state.mode == M_SCANNER:
        data.trigger_scan(); state.dirty=True
    # ── Mode 2 (Research) controls ─────────────────────────────────────────
    elif state.mode == M_RESEARCH:
        if ch == "f":
            # Cycle: auto (None) -> analysis -> posture -> alpha -> auto
            order = [None, "analysis", "posture", "alpha"]
            try:
                idx = order.index(state.research_focus)
            except ValueError:
                idx = 0
            state.research_focus = order[(idx + 1) % len(order)]
            state.dirty = True
        elif ch in ("j", "J"):
            cap = max(0, state._alpha_visible_count - 1)
            state.alpha_cursor = min(state.alpha_cursor + 1, cap)
            state.dirty = True
        elif ch in ("k", "K"):
            state.alpha_cursor = max(0, state.alpha_cursor - 1)
            state.dirty = True
        elif ch == "g":
            state.alpha_cursor = 0
            state.dirty = True
        elif ch == "G":
            state.alpha_cursor = max(0, state._alpha_visible_count - 1)
            state.dirty = True
        elif ch in ("m", "+"):
            state.alpha_show_more = min(state.alpha_show_more + 1, state.ALPHA_SHOW_MORE_MAX)
            state.dirty = True
        elif ch == "-":
            state.alpha_show_more = max(0, state.alpha_show_more - 1)
            state.dirty = True
        elif ch == "0":
            state.alpha_cursor = 0
            state.alpha_show_more = 0
            state.research_focus = None  # back to auto
            state.dirty = True
        elif ch in ("l", "L"):
            # Manual Stock Lens build for the active ticker.  No-op if no
            # ticker is selected or a build for that ticker is already in
            # flight; refires fine for a different ticker.
            tk = (state.ticker or "").upper().strip()
            if tk and state.lens_pending_ticker != tk:
                state.lens_pending_ticker = tk
                state.lens_last_error = None
                threading.Thread(
                    target=_run_stock_lens,
                    args=(tk, state, data),
                    daemon=True,
                ).start()
                state.dirty = True
    return False


def _run_analysis(ticker: str, state: State, data: DataLayer, claude: ClaudeAnalyzer):
    try:
        # Use get_ticker_bars() — goes through cache (12h TTL) rather than bypassing it.
        # Previously called _get() directly, which bypassed the Gatekeeper entirely.
        bars  = get_fmp().get_ticker_bars(ticker, days=60)
        sent  = get_fmp().get_sentiment_score(ticker)
        vix   = data.get("vix")
        reg   = data.get("regime")
        econ  = data.get("econ_cal") or []
        earn  = data.get("earnings") or []
        state.ana_bars  = bars
        state.analysis  = claude.analyze(ticker, bars, sent, vix, reg, econ, earn)
        # Only record the lookup in Recent once we have real price history
        # for the symbol — keeps typos like "QQQQQ" out of the history.
        if bars and len(bars) >= 10:
            state.push_history(ticker)
    except Exception as exc:
        state.analysis = ClaudeAnalyzer._stub(ticker, str(exc)[:80])
    state.dirty = True


def _run_stock_lens(ticker: str, state: State, data: DataLayer):
    """
    Manual on-demand Stock Lens build for the currently-selected Mode 2 ticker.

    Triggered by the "L" key.  Calls research.stock_lens_runner.run() in a
    daemon thread; on completion the new artifact lands at
    cache/research/stock_lens_<TICKER>_latest.json and we invalidate the
    DataLayer's per-ticker cache key so the next render reads the fresh
    artifact.  This DOES make provider calls (FMP/Alpaca via the runner) and
    DOES append to the stock-lens forward-tracking ledger — same behavior as
    invoking the runner from the CLI.  Use sparingly for tickers you're
    actually researching.
    """
    import argparse as _ap
    ticker = (ticker or "").upper().strip()
    if not ticker:
        return
    try:
        # Synthesize the same Namespace shape research/regime_forecast.py
        # parses; runner reads attributes directly off it.
        args = _ap.Namespace(
            mode="daily",
            ticker=ticker,
            horizon=20,
            refresh=False,
            offline=False,
            cache_only=False,
            no_fmp=False,
            no_snapshot=False,
            stale_hours=24.0,
            print_json=False,
        )
        from research import stock_lens_runner
        stock_lens_runner.run(args)
        state.lens_last_error = None
    except Exception as exc:
        state.lens_last_error = str(exc)[:120]
    finally:
        # Invalidate the DataLayer's per-ticker stock-lens cache so the next
        # get_stock_lens(ticker) call re-reads the file we just wrote (or
        # surfaces the error path for a missing artifact).
        cache_key = f"stock_lens::{ticker}"
        try:
            with data._lock:
                data._ts[cache_key] = 0
        except Exception:
            pass
        # Clear pending only if the ticker we just built matches the active
        # one — protects against the user switching tickers mid-build.
        if state.lens_pending_ticker == ticker:
            state.lens_pending_ticker = None
        state.dirty = True


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    import argparse
    p = argparse.ArgumentParser(description="GEM Trader HQ v2")
    p.add_argument("--ticker",   default=None, help="Pre-load analysis ticker")
    p.add_argument("--mode",     default=1,    type=int, help="Start mode 1-4")
    p.add_argument("--refresh",  default=5,    type=int, help="Refresh interval (s)")
    args = p.parse_args()

    console  = Console()
    console.print("[bold cyan]GEM Trader HQ v2[/] — initializing…")

    data   = DataLayer()
    claude = ClaudeAnalyzer()
    state  = State(init_ticker=args.ticker)
    state.mode = max(1, min(4, args.mode))
    time.sleep(3)  # let data layer populate

    if args.ticker:
        state.mode = M_RESEARCH
        threading.Thread(target=_run_analysis,
                         args=(args.ticker.upper(),state,data,claude), daemon=True).start()

    kb = KB()
    try:
        with Live(console=console, screen=True, auto_refresh=False) as live:
            last_render = 0.0
            running     = True
            while running:
                ch = kb.read(0.05)
                if ch:
                    running = not handle_key(ch, state, data, claude)

                now = time.time()
                if now - last_render >= args.refresh or state.dirty:
                    try:
                        layout = _BUILDERS[state.mode](state, data, claude)
                        live.update(layout, refresh=True)
                    except Exception:
                        pass
                    last_render = now
                    state.dirty = False
    except KeyboardInterrupt:
        pass
    finally:
        kb.restore()
        data.stop()
        console.print("\n[bold cyan]GEM Trader HQ[/] stopped.")

if __name__ == "__main__":
    main()

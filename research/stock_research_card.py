#!/usr/bin/env python3
"""
research/stock_research_card.py — Stock Research Card Engine (Phase 4D).

Generates a per-ticker research card: trend snapshot, RS, volume, catalyst
summary, earnings date, FMP fundamentals, options snapshot (Tradier,
research-only), social attention (if available), risk flags, and
invalidation conditions.

This module does NOT:
  - recommend entries, stops, targets, or position sizes
  - emit paper signals or trade proposals
  - require Alpaca or broker credentials
  - route any result to execution

Tradier is used ONLY for options research (open interest, IV, put/call ratio).
Execution via Tradier is disabled.

Usage:
  SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python research/stock_research_card.py AAPL NVDA MSFT
  GEM_TRADER_SKIP_DOTENV=true .venv/bin/python research/stock_research_card.py AAPL --offline
  ./scripts/run_research_cycle.sh stock-research-card AAPL NVDA
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
except Exception:
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
from core.research_mode import SYSTEM_MODE, RESEARCH_ONLY_BANNER, TRADIER_RESEARCH_ENABLED, TRADIER_EXECUTION_ENABLED

VERSION = "STOCK_RESEARCH_CARD_V1"
PRICE_DIR = cfg.CACHE_DIR / "prices"
RESEARCH_DIR = cfg.CACHE_DIR / "research"
CARD_DIR = RESEARCH_DIR / "cards"
DOC_PATH = ROOT / "docs" / "research" / "STOCK_RESEARCH_CARD_ENGINE.md"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("stock_research_card")


# ── Helpers ──────────────────────────────────────────────────────────────────


def _is_offline_fmp() -> bool:
    return os.getenv("FMP_API_KEY", "").strip().lower() in {"", "offline", "stub"}


def _is_offline_tradier() -> bool:
    tok = os.getenv("TRADIER_ACCESS_TOKEN", "").strip()
    return not tok or tok.lower() in {"", "offline", "stub"}


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
    except Exception:
        return None


def _closes(df: Optional[pd.DataFrame]) -> List[float]:
    if df is None:
        return []
    col = "close" if "close" in df.columns else ("Close" if "Close" in df.columns else None)
    if col is None:
        return []
    return [float(v) for v in df[col].dropna().tolist()]


def _volumes(df: Optional[pd.DataFrame]) -> List[float]:
    if df is None:
        return []
    col = next((c for c in ["volume", "Volume"] if c in df.columns), None)
    if col is None:
        return []
    return [float(v) for v in df[col].dropna().tolist()]


def _last(s: List[float]) -> Optional[float]:
    return s[-1] if s else None


def _ma(s: List[float], w: int) -> Optional[float]:
    if len(s) < w:
        return None
    return sum(s[-w:]) / w


def _ret(s: List[float], lb: int) -> Optional[float]:
    if len(s) < lb + 1:
        return None
    return (s[-1] / s[-(lb + 1)] - 1.0) * 100.0


def _rs_vs_spy(closes: List[float], spy: List[float], lookback: int = 63) -> Optional[float]:
    if len(closes) < lookback + 1 or len(spy) < lookback + 1:
        return None
    r_stock = closes[-1] / closes[-(lookback + 1)] - 1.0
    r_spy = spy[-1] / spy[-(lookback + 1)] - 1.0
    return (r_stock - r_spy) * 100.0


def _drawdown_from_high(s: List[float], window: int = 252) -> Optional[float]:
    if not s or len(s) < 5:
        return None
    recent = s[-window:] if len(s) >= window else s
    hi = max(recent)
    return (s[-1] / hi - 1.0) * 100.0 if hi > 0 else None


def _atr_pct(df: Optional[pd.DataFrame], window: int = 14) -> Optional[float]:
    if df is None or len(df) < window + 1:
        return None
    hi = df.get("high", df.get("High"))
    lo = df.get("low", df.get("Low"))
    cl = df.get("close", df.get("Close"))
    if hi is None or lo is None or cl is None:
        return None
    tr_vals: List[float] = []
    for i in range(-window, 0):
        try:
            h, l, pc = float(hi.iloc[i]), float(lo.iloc[i]), float(cl.iloc[i - 1])
            tr_vals.append(max(h - l, abs(h - pc), abs(l - pc)))
        except Exception:
            pass
    if not tr_vals:
        return None
    atr = sum(tr_vals) / len(tr_vals)
    last_cl = _last(_closes(df))
    return (atr / last_cl * 100.0) if last_cl else None


def _vol_trend(vols: List[float], short: int = 10, long: int = 30) -> Optional[float]:
    if len(vols) < long:
        return None
    avg_s = sum(vols[-short:]) / short
    avg_l = sum(vols[-long:]) / long
    return avg_s / avg_l if avg_l > 0 else None


def _up_down_vol(df: Optional[pd.DataFrame], window: int = 20) -> Optional[Dict[str, float]]:
    if df is None or len(df) < window + 1:
        return None
    cl_col = "close" if "close" in df.columns else ("Close" if "Close" in df.columns else None)
    vol_col = next((c for c in ["volume", "Volume"] if c in df.columns), None)
    if not cl_col or not vol_col:
        return None
    chunk = df.iloc[-window:]
    up_vol = 0.0
    dn_vol = 0.0
    for i in range(1, len(chunk)):
        vol = float(chunk[vol_col].iloc[i])
        if float(chunk[cl_col].iloc[i]) > float(chunk[cl_col].iloc[i - 1]):
            up_vol += vol
        else:
            dn_vol += vol
    total = up_vol + dn_vol
    if total == 0:
        return None
    return {"up_vol_pct": round(up_vol / total * 100, 1), "dn_vol_pct": round(dn_vol / total * 100, 1)}


# ── FMP data pulls ────────────────────────────────────────────────────────────


def _fmp_profile(ticker: str) -> Optional[Dict[str, Any]]:
    if _is_offline_fmp():
        return None
    try:
        from core.fmp_client import get_fmp
        profile = get_fmp().get_company_profile(ticker)
        return profile if isinstance(profile, dict) else None
    except Exception:
        return None


def _fmp_fundamentals(ticker: str) -> Optional[Dict[str, Any]]:
    if _is_offline_fmp():
        return None
    try:
        from core.fmp_client import get_fmp
        return get_fmp().get_fundamentals(ticker)
    except Exception:
        return None


def _fmp_next_earnings(ticker: str) -> Optional[str]:
    if _is_offline_fmp():
        return None
    try:
        from core.fmp_client import get_fmp
        cal = get_fmp().get_earnings_calendar(days_ahead=60)
        for item in cal:
            if (item.get("symbol") or "").upper() == ticker.upper():
                return item.get("date") or item.get("reportDate")
        return None
    except Exception:
        return None


def _fmp_news_summary(ticker: str) -> List[str]:
    if _is_offline_fmp():
        return []
    try:
        from core.fmp_client import get_fmp
        news = get_fmp().get_news(ticker=ticker, limit=5)
        return [n.get("title") or n.get("headline") or "" for n in news if n.get("title") or n.get("headline")][:5]
    except Exception:
        return []


def _fmp_analyst_grades(ticker: str) -> List[Dict[str, Any]]:
    if _is_offline_fmp():
        return []
    try:
        from core.fmp_client import get_fmp
        return get_fmp().get_analyst_grades(ticker, limit=5)
    except Exception:
        return []


def _fmp_insider(ticker: str) -> List[Dict[str, Any]]:
    if _is_offline_fmp():
        return []
    try:
        from core.fmp_client import get_fmp
        return get_fmp().get_insider_trading(ticker, limit=5)
    except Exception:
        return []


# ── Tradier options snapshot (research-only) ─────────────────────────────────


def _tradier_options_snapshot(ticker: str) -> Optional[Dict[str, Any]]:
    if not TRADIER_RESEARCH_ENABLED or _is_offline_tradier():
        return None
    if TRADIER_EXECUTION_ENABLED:
        logger.error("TRADIER_EXECUTION_ENABLED=True — this must never be True in RESEARCH_ONLY_MODE")
        return None
    try:
        import requests
        token = os.getenv("TRADIER_ACCESS_TOKEN", "")
        base = os.getenv("TRADIER_BASE_URL", "https://api.tradier.com/v1")
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        # Nearest expiry chain
        r_exp = requests.get(
            f"{base}/markets/options/expirations",
            params={"symbol": ticker, "includeAllRoots": "true"},
            headers=headers, timeout=8,
        )
        if r_exp.status_code != 200:
            return None
        exps = r_exp.json().get("expirations", {}).get("date") or []
        if not exps:
            return None
        nearest = exps[0] if isinstance(exps, list) else str(exps)
        r_chain = requests.get(
            f"{base}/markets/options/chains",
            params={"symbol": ticker, "expiration": nearest, "greeks": "false"},
            headers=headers, timeout=12,
        )
        if r_chain.status_code != 200:
            return None
        options = r_chain.json().get("options", {}).get("option") or []
        if not options:
            return None
        call_oi = sum(o.get("open_interest") or 0 for o in options if o.get("option_type") == "call")
        put_oi = sum(o.get("open_interest") or 0 for o in options if o.get("option_type") == "put")
        total_oi = call_oi + put_oi
        pc_ratio = round(put_oi / call_oi, 3) if call_oi > 0 else None
        ivs = [float(o["greeks"]["smv_vol"]) for o in options
               if isinstance(o.get("greeks"), dict) and o["greeks"].get("smv_vol")]
        avg_iv = round(sum(ivs) / len(ivs) * 100, 1) if ivs else None
        return {
            "expiry_sampled": nearest,
            "call_open_interest": call_oi,
            "put_open_interest": put_oi,
            "total_open_interest": total_oi,
            "put_call_ratio": pc_ratio,
            "avg_iv_pct": avg_iv,
            "research_only": True,
            "execution_disabled": True,
        }
    except Exception as exc:
        logger.debug("Tradier options snapshot failed for %s: %s", ticker, exc)
        return None


# ── Social data ───────────────────────────────────────────────────────────────


def _social_lookup(ticker: str) -> Optional[Dict[str, Any]]:
    for name in [
        "social_arb_latest.json",
        "social_attention_latest.json",
        "social_attention_radar_latest.json",
    ]:
        path = RESEARCH_DIR / name
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text())
            for item in data.get("candidates", data.get("leads", [])):
                t = (item.get("ticker") or item.get("symbol") or "").upper()
                if t == ticker.upper():
                    return {
                        "available": True,
                        "source": name.replace("_latest.json", ""),
                        "score": item.get("score") or item.get("deterministic_score") or 0,
                        "crowded": item.get("already_viral") or item.get("crowded") or False,
                        "label": item.get("label") or item.get("news_label") or "",
                        "fabricated": False,
                    }
        except Exception:
            pass
    return None


# ── Scanner context ───────────────────────────────────────────────────────────


def _scanner_context(ticker: str) -> Dict[str, Any]:
    """Pull this ticker's entry from the latest scanner output if available."""
    path = RESEARCH_DIR / "research_scanner_latest.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        for item in data.get("watchlist", []):
            if (item.get("ticker") or "").upper() == ticker.upper():
                return {
                    "watchlist_label": item.get("watchlist_label"),
                    "research_score": item.get("research_score"),
                    "category": item.get("category"),
                    "why_appeared": item.get("why_appeared"),
                    "confirms_if": item.get("confirms_if"),
                    "invalidates_if": item.get("invalidates_if"),
                }
    except Exception:
        pass
    return {}


# ── Research card builder ─────────────────────────────────────────────────────


def build_card(ticker: str, offline: bool = False) -> Dict[str, Any]:
    sym = ticker.upper().strip()
    now = datetime.now(timezone.utc).isoformat()
    logger.info("Building research card for %s (offline=%s)", sym, offline)

    df = _load_cached_frame(sym)
    s = _closes(df)
    v = _volumes(df)
    spy = _closes(_load_cached_frame("SPY"))

    # Price trend section
    last_price = _last(s)
    ma20 = _ma(s, 20)
    ma50 = _ma(s, 50)
    ma200 = _ma(s, 200)
    r5 = _ret(s, 5)
    r20 = _ret(s, 20)
    r63 = _ret(s, 63)
    r252 = _ret(s, 252) if len(s) >= 253 else None
    dd = _drawdown_from_high(s)
    atr_p = _atr_pct(df)

    price_trend = {
        "last_price": round(last_price, 4) if last_price is not None else None,
        "ma20": round(ma20, 4) if ma20 is not None else None,
        "ma50": round(ma50, 4) if ma50 is not None else None,
        "ma200": round(ma200, 4) if ma200 is not None else None,
        "above_ma20": bool(last_price > ma20) if (last_price and ma20) else None,
        "above_ma50": bool(last_price > ma50) if (last_price and ma50) else None,
        "above_ma200": bool(last_price > ma200) if (last_price and ma200) else None,
        "r5d_pct": round(r5, 2) if r5 is not None else None,
        "r20d_pct": round(r20, 2) if r20 is not None else None,
        "r63d_pct": round(r63, 2) if r63 is not None else None,
        "r252d_pct": round(r252, 2) if r252 is not None else None,
        "dd_from_52w_high_pct": round(dd, 2) if dd is not None else None,
        "atr_pct": round(atr_p, 2) if atr_p is not None else None,
        "data_source": "price_cache_parquet" if df is not None else "no_data",
    }

    # RS score
    rs_20 = _rs_vs_spy(s, spy, 20)
    rs_63 = _rs_vs_spy(s, spy, 63)
    rs_252 = _rs_vs_spy(s, spy, 252) if (len(s) >= 253 and len(spy) >= 253) else None
    rs_score = {
        "rs_20d_vs_spy": round(rs_20, 2) if rs_20 is not None else None,
        "rs_63d_vs_spy": round(rs_63, 2) if rs_63 is not None else None,
        "rs_252d_vs_spy": round(rs_252, 2) if rs_252 is not None else None,
        "rs_label": (
            "STRONG" if (rs_63 is not None and rs_63 > 10) else
            "ABOVE_AVG" if (rs_63 is not None and rs_63 > 3) else
            "AVERAGE" if (rs_63 is not None and rs_63 > -3) else
            "WEAK" if (rs_63 is not None and rs_63 > -10) else
            "LAGGING" if rs_63 is not None else "UNKNOWN"
        ),
        "data_source": "price_cache_parquet",
    }

    # Volume and accumulation
    vol_tr = _vol_trend(v)
    updn = _up_down_vol(df, 20)
    avg_vol_20 = sum(v[-20:]) / 20 if len(v) >= 20 else None
    last_vol = v[-1] if v else None
    vol_section = {
        "last_volume": int(last_vol) if last_vol is not None else None,
        "avg_volume_20d": int(avg_vol_20) if avg_vol_20 is not None else None,
        "vol_trend_ratio": round(vol_tr, 3) if vol_tr is not None else None,
        "vol_trend_label": (
            "RISING" if (vol_tr and vol_tr > 1.1) else
            "FLAT" if (vol_tr and vol_tr > 0.9) else
            "DECLINING" if vol_tr is not None else "UNKNOWN"
        ),
        "up_down_vol_20d": updn,
        "data_source": "price_cache_parquet",
    }

    # FMP company profile
    profile: Optional[Dict[str, Any]] = None if offline else _fmp_profile(sym)
    company_name = (profile or {}).get("companyName") or (profile or {}).get("name") or sym
    sector = (profile or {}).get("sector") or "Unknown"
    industry = (profile or {}).get("industry") or "Unknown"
    market_cap = (profile or {}).get("mktCap") or (profile or {}).get("market_cap")
    description = (profile or {}).get("description") or ""
    exchange = (profile or {}).get("exchange") or (profile or {}).get("exchangeShortName") or ""

    # FMP fundamentals
    fundamentals: Optional[Dict[str, Any]] = None if offline else _fmp_fundamentals(sym)

    def _f(key: str, *alt_keys: str) -> Any:
        for k in (key, *alt_keys):
            v = (fundamentals or {}).get(k)
            if v is not None:
                return v
        return None

    revenue = _f("revenue", "totalRevenue")
    prev_revenue = _f("revenue_prior", "revenuePrior")
    revenue_growth = None
    if revenue and prev_revenue and float(prev_revenue) > 0:
        revenue_growth = round((float(revenue) / float(prev_revenue) - 1.0) * 100.0, 1)

    net_income = _f("netIncome", "net_income")
    gross_margin = _f("grossProfitRatio", "gross_margin", "grossMarginTTM")
    operating_margin = _f("operatingIncomeRatio", "operating_margin", "operatingMarginTTM")
    total_debt = _f("totalDebt", "total_debt")
    cash = _f("cashAndCashEquivalents", "cash", "cashAndShortTermInvestments")
    pe_ratio = _f("peRatio", "pe_ratio", "priceEarningsRatio")
    ps_ratio = _f("priceToSalesRatioTTM", "ps_ratio")
    eps_ttm = _f("eps", "epsTTM", "epsBasic")
    fcf = _f("freeCashFlow", "free_cash_flow")
    roe = _f("returnOnEquity", "roe")

    fundamental_section = {
        "company_name": company_name,
        "sector": sector,
        "industry": industry,
        "exchange": exchange,
        "market_cap": market_cap,
        "description_snippet": description[:200] if description else None,
        "revenue_ttm": revenue,
        "revenue_growth_pct": revenue_growth,
        "gross_margin_pct": round(float(gross_margin) * 100, 1) if gross_margin is not None and float(gross_margin) < 5 else (round(float(gross_margin), 1) if gross_margin is not None else None),
        "operating_margin_pct": round(float(operating_margin) * 100, 1) if operating_margin is not None and float(operating_margin) < 5 else (round(float(operating_margin), 1) if operating_margin is not None else None),
        "net_income": net_income,
        "free_cash_flow": fcf,
        "total_debt": total_debt,
        "cash": cash,
        "debt_to_cash": round(float(total_debt) / float(cash), 2) if (total_debt and cash and float(cash) > 0) else None,
        "pe_ratio": round(float(pe_ratio), 1) if pe_ratio is not None else None,
        "ps_ratio": round(float(ps_ratio), 1) if ps_ratio is not None else None,
        "eps_ttm": eps_ttm,
        "roe_pct": round(float(roe) * 100, 1) if roe is not None and float(roe) < 5 else (round(float(roe), 1) if roe is not None else None),
        "data_source": "fmp" if not _is_offline_fmp() else "offline_no_data",
        "data_available": fundamentals is not None,
    }

    # Catalyst summary
    earnings_date: Optional[str] = None if offline else _fmp_next_earnings(sym)
    news_headlines = [] if offline else _fmp_news_summary(sym)
    analyst_grades = [] if offline else _fmp_analyst_grades(sym)
    insider_trades = [] if offline else _fmp_insider(sym)

    analyst_summary: List[str] = []
    for g in analyst_grades[:3]:
        action = g.get("action") or g.get("newGrade") or ""
        firm = g.get("analystCompany") or g.get("firm") or ""
        if action:
            analyst_summary.append(f"{firm}: {action}" if firm else action)

    insider_summary: List[str] = []
    for it in insider_trades[:3]:
        name = it.get("reportingName") or it.get("insider") or "Insider"
        trans = it.get("transactionType") or it.get("type") or ""
        shares = it.get("securitiesTransacted") or 0
        if trans:
            insider_summary.append(f"{name} {trans} {shares:,} shares" if shares else f"{name} {trans}")

    catalyst_section = {
        "next_earnings_date": earnings_date,
        "news_headlines": news_headlines,
        "analyst_grades_recent": analyst_summary,
        "insider_transactions_recent": insider_summary,
        "data_source": "fmp" if not _is_offline_fmp() else "offline_no_data",
    }

    # Options snapshot (Tradier, research-only)
    options_section: Optional[Dict[str, Any]] = None
    if TRADIER_RESEARCH_ENABLED and not offline and not _is_offline_tradier():
        options_section = _tradier_options_snapshot(sym)
    if options_section:
        options_section["note"] = "Research-only; Tradier execution is disabled."
    else:
        options_section = {
            "available": False,
            "reason": "Tradier unavailable or offline mode" if (_is_offline_tradier() or offline) else "No data returned",
            "research_only": True,
            "execution_disabled": True,
        }

    # Social attention
    social = _social_lookup(sym)
    social_section = social if social else {
        "available": False,
        "label": "NO_SOCIAL_DATA",
        "fabricated": False,
        "note": "No social attention data available for this ticker.",
    }

    # Scanner context
    scanner_ctx = _scanner_context(sym)

    # Valuation snapshot (human-readable interpretation)
    valuation_note = "No FMP data available."
    if fundamentals:
        pe = fundamental_section.get("pe_ratio")
        ps = fundamental_section.get("ps_ratio")
        rev_g = fundamental_section.get("revenue_growth_pct")
        notes = []
        if pe is not None:
            notes.append(f"P/E {pe:.1f}" + (" (elevated)" if pe > 35 else " (moderate)" if pe > 18 else " (low)"))
        if ps is not None:
            notes.append(f"P/S {ps:.1f}" + (" (growth premium)" if ps > 10 else " (moderate)"))
        if rev_g is not None:
            notes.append(f"Revenue growth {rev_g:.1f}%" + (" (strong)" if rev_g > 20 else " (moderate)" if rev_g > 5 else " (slow)"))
        valuation_note = "; ".join(notes) if notes else "No valuation data."

    # Risk flags
    risk_flags: List[str] = []
    if last_price and ma200 and last_price < ma200:
        risk_flags.append("Below 200d MA — long-term downtrend context")
    if last_price and ma50 and last_price < ma50:
        risk_flags.append("Below 50d MA — medium-term downtrend context")
    if dd is not None and dd < -30:
        risk_flags.append(f"Significant drawdown from high: {dd:.1f}%")
    if vol_tr is not None and vol_tr < 0.7:
        risk_flags.append("Volume declining — institutional interest may be waning")
    if rs_63 is not None and rs_63 < -15:
        risk_flags.append(f"Deeply lagging SPY by {rs_63:.1f}pp over 63d")
    if atr_p is not None and atr_p > 5:
        risk_flags.append(f"High volatility: ATR = {atr_p:.1f}% of price")
    if social_section.get("crowded"):
        risk_flags.append("Already viral/crowded — late-mover risk")
    if fundamental_section.get("debt_to_cash") is not None:
        dtc = fundamental_section["debt_to_cash"]
        if dtc and float(dtc) > 5:
            risk_flags.append(f"High debt-to-cash ratio: {dtc:.1f}x")

    # Extension risk
    extension_pct = None
    if last_price and ma200:
        extension_pct = round((last_price / ma200 - 1.0) * 100.0, 1)
    if extension_pct is not None and extension_pct > 25:
        risk_flags.append(f"Extended {extension_pct:.1f}% above 200d MA — mean reversion risk")

    # Allowed research conclusions
    research_conclusion = _derive_conclusion(
        rs_63=rs_63,
        vol_tr=vol_tr,
        dd=dd,
        above_ma50=price_trend.get("above_ma50"),
        has_earnings=earnings_date is not None,
        risk_flags=risk_flags,
        scanner_label=scanner_ctx.get("watchlist_label"),
    )

    card: Dict[str, Any] = {
        "version": VERSION,
        "ticker": sym,
        "generated_at": now,
        "system_mode": SYSTEM_MODE,
        "research_only": True,
        "company_name": company_name,
        "sector": sector,
        "industry": industry,
        "market_cap": market_cap,
        "price_trend": price_trend,
        "rs_score": rs_score,
        "volume_accumulation": vol_section,
        "catalyst_summary": catalyst_section,
        "fundamentals": fundamental_section,
        "valuation_snapshot": valuation_note,
        "options_snapshot": options_section,
        "social_attention": social_section,
        "risk_flags": risk_flags,
        "extension_pct_above_ma200": extension_pct,
        "scanner_context": scanner_ctx if scanner_ctx else None,
        "research_conclusion": research_conclusion,
        "why_appeared_in_scanner": scanner_ctx.get("why_appeared") or "Not in latest scanner run",
        "what_confirms_improvement": scanner_ctx.get("confirms_if") or "RS improves, volume expands, catalyst confirms",
        "what_invalidates_thesis": scanner_ctx.get("invalidates_if") or "RS reverses, new lows, fundamental deterioration",
        "guardrails": {
            "no_trade_recommendation": True,
            "no_buy_sell": True,
            "no_entry_stop_target": True,
            "no_paper_signal": True,
            "alpaca_required": False,
            "tradier_research_only": True,
            "tradier_execution_disabled": True,
            "manual_review_required": True,
        },
    }
    return card


def _derive_conclusion(
    rs_63: Optional[float],
    vol_tr: Optional[float],
    dd: Optional[float],
    above_ma50: Optional[bool],
    has_earnings: bool,
    risk_flags: List[str],
    scanner_label: Optional[str],
) -> str:
    if len(risk_flags) >= 4:
        return "risk flagged — multiple concerns; requires manual review before any action"
    if scanner_label in {"CROWDED"}:
        return "crowded/viral — late-mover risk; watch candidate only after consolidation"
    if scanner_label in {"EXTENDED"}:
        return "extended; wait for reset — worth watching for better entry context"
    if scanner_label in {"SPECULATIVE_10X"}:
        return "potential asymmetric candidate — speculative long-term watch; high risk"
    if has_earnings and dd is not None and dd > -5:
        return "catalyst candidate — upcoming earnings; requires manual review"
    if rs_63 is not None and rs_63 > 8 and above_ma50:
        return "watch candidate — relative strength and trend positive"
    if dd is not None and dd < -25 and above_ma50:
        return "beaten-down potential recovery — worth researching further"
    if vol_tr is not None and vol_tr > 1.2:
        return "worth researching — volume accumulation signal; manual review recommended"
    return "requires manual review — mixed signals; no clear research conclusion"


# ── Output writers ────────────────────────────────────────────────────────────


def _format_text(card: Dict[str, Any]) -> str:
    def _s(v: Any, suffix: str = "") -> str:
        if v is None:
            return "N/A"
        return f"{v}{suffix}"

    lines = [
        RESEARCH_ONLY_BANNER,
        "",
        f"STOCK RESEARCH CARD [{card['version']}]",
        f"Ticker:  {card['ticker']}  —  {card['company_name']}",
        f"Sector:  {card['sector']} / {card['industry']}",
        f"Market Cap: {_s(card['market_cap'])}",
        f"Generated: {card['generated_at']}",
        "",
        "=== PRICE TREND ===",
    ]
    pt = card.get("price_trend", {})
    lines += [
        f"  Last:     {_s(pt.get('last_price'))}",
        f"  MA20:     {_s(pt.get('ma20'))}  |  MA50: {_s(pt.get('ma50'))}  |  MA200: {_s(pt.get('ma200'))}",
        f"  Above MA50: {_s(pt.get('above_ma50'))}  |  Above MA200: {_s(pt.get('above_ma200'))}",
        f"  5d: {_s(pt.get('r5d_pct'), '%')}  |  20d: {_s(pt.get('r20d_pct'), '%')}  |  63d: {_s(pt.get('r63d_pct'), '%')}  |  252d: {_s(pt.get('r252d_pct'), '%')}",
        f"  DD from high: {_s(pt.get('dd_from_52w_high_pct'), '%')}  |  ATR%: {_s(pt.get('atr_pct'), '%')}",
        "",
        "=== RELATIVE STRENGTH ===",
    ]
    rs = card.get("rs_score", {})
    lines += [
        f"  RS vs SPY 20d: {_s(rs.get('rs_20d_vs_spy'), 'pp')}",
        f"  RS vs SPY 63d: {_s(rs.get('rs_63d_vs_spy'), 'pp')}",
        f"  RS vs SPY 252d: {_s(rs.get('rs_252d_vs_spy'), 'pp')}",
        f"  RS Label: {_s(rs.get('rs_label'))}",
        "",
        "=== VOLUME / ACCUMULATION ===",
    ]
    vol = card.get("volume_accumulation", {})
    updn = vol.get("up_down_vol_20d") or {}
    lines += [
        f"  Vol trend ratio (10d/30d): {_s(vol.get('vol_trend_ratio'))}  [{_s(vol.get('vol_trend_label'))}]",
        f"  Up-vol%: {_s(updn.get('up_vol_pct'), '%')}  |  Dn-vol%: {_s(updn.get('dn_vol_pct'), '%')}",
        "",
        "=== CATALYST SUMMARY ===",
    ]
    cat = card.get("catalyst_summary", {})
    lines += [
        f"  Next earnings: {_s(cat.get('next_earnings_date'))}",
        "  Headlines:",
    ]
    for h in cat.get("news_headlines", []):
        lines.append(f"    - {h}")
    lines.append("  Analyst grades:")
    for g in cat.get("analyst_grades_recent", []):
        lines.append(f"    - {g}")
    lines.append("  Insider transactions:")
    for it in cat.get("insider_transactions_recent", []):
        lines.append(f"    - {it}")

    lines += ["", "=== FUNDAMENTALS ==="]
    f = card.get("fundamentals", {})
    lines += [
        f"  Revenue TTM: {_s(f.get('revenue_ttm'))}  |  Growth: {_s(f.get('revenue_growth_pct'), '%')}",
        f"  Gross margin: {_s(f.get('gross_margin_pct'), '%')}  |  Operating margin: {_s(f.get('operating_margin_pct'), '%')}",
        f"  Net income: {_s(f.get('net_income'))}  |  FCF: {_s(f.get('free_cash_flow'))}",
        f"  Debt: {_s(f.get('total_debt'))}  |  Cash: {_s(f.get('cash'))}  |  D/C: {_s(f.get('debt_to_cash'))}",
        f"  P/E: {_s(f.get('pe_ratio'))}  |  P/S: {_s(f.get('ps_ratio'))}  |  EPS TTM: {_s(f.get('eps_ttm'))}",
        f"  ROE: {_s(f.get('roe_pct'), '%')}",
        "",
        "=== VALUATION SNAPSHOT ===",
        f"  {card.get('valuation_snapshot')}",
        "",
        "=== OPTIONS SNAPSHOT (Research-only, Tradier) ===",
    ]
    opt = card.get("options_snapshot") or {}
    if opt.get("available") is False:
        lines.append(f"  Unavailable: {opt.get('reason', '')}")
    else:
        lines += [
            f"  Expiry sampled: {_s(opt.get('expiry_sampled'))}",
            f"  Call OI: {_s(opt.get('call_open_interest'))}  |  Put OI: {_s(opt.get('put_open_interest'))}",
            f"  Put/Call ratio: {_s(opt.get('put_call_ratio'))}  |  Avg IV: {_s(opt.get('avg_iv_pct'), '%')}",
            f"  NOTE: {opt.get('note', 'Research-only — execution disabled.')}",
        ]

    lines += ["", "=== SOCIAL ATTENTION ==="]
    soc = card.get("social_attention") or {}
    if soc.get("available"):
        lines += [
            f"  Source: {soc.get('source')}  |  Score: {soc.get('score')}  |  Crowded: {soc.get('crowded')}",
            f"  Label: {soc.get('label')}",
        ]
    else:
        lines.append(f"  NO_SOCIAL_DATA — {soc.get('note', '')}")

    lines += ["", "=== RISK FLAGS ==="]
    for rf in card.get("risk_flags", []):
        lines.append(f"  !! {rf}")
    if not card.get("risk_flags"):
        lines.append("  No major risk flags identified from available data.")

    lines += [
        "",
        "=== RESEARCH CONCLUSION ===",
        f"  {card.get('research_conclusion', '')}",
        "",
        "=== SCANNER CONTEXT ===",
        f"  Why appeared: {card.get('why_appeared_in_scanner', 'N/A')}",
        f"  Confirms if: {card.get('what_confirms_improvement', 'N/A')}",
        f"  Invalidates if: {card.get('what_invalidates_thesis', 'N/A')}",
        "",
        "--- RESEARCH ONLY — NO TRADE RECOMMENDATION ---",
        "--- Manual review required before any action ---",
    ]
    return "\n".join(lines)


def write_card(card: Dict[str, Any]) -> Path:
    CARD_DIR.mkdir(parents=True, exist_ok=True)
    sym = card["ticker"]
    out_json = CARD_DIR / f"{sym}_research_card.json"
    out_txt = CARD_DIR / f"{sym}_research_card.txt"
    out_json.write_text(json.dumps(card, indent=2), encoding="utf-8")
    out_txt.write_text(_format_text(card), encoding="utf-8")
    logger.info("wrote %s  %s", out_json.name, out_txt.name)
    return out_json


def write_doc() -> None:
    if DOC_PATH.exists():
        return
    DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    content = """\
# STOCK RESEARCH CARD ENGINE

**Module:** `research/stock_research_card.py`
**Phase:** 4D
**Mode:** RESEARCH_ONLY — no trade recommendations.

## Purpose

Generates a per-ticker research card for any name surfaced by the scanner,
the heartbeat, or the human operator. Cards compile trend, RS, volume,
catalyst, fundamentals, options (research-only), social attention, risk flags,
and invalidation conditions into a single human-readable artifact.

## Card Sections

| Section | Data Source | Trust |
|---|---|---|
| Price Trend | Price cache (parquet) | HIGH |
| RS Score | Price cache vs SPY | HIGH |
| Volume / Accumulation | Price cache | HIGH |
| Catalyst Summary | FMP earnings calendar + news + analyst grades | HIGH (when FMP available) |
| Fundamentals | FMP income / balance sheet | HIGH (when FMP available) |
| Valuation Snapshot | FMP ratios | MEDIUM |
| Options Snapshot | Tradier (research-only; no execution) | MEDIUM |
| Social Attention | Social arb sidecars (degraded to NO_SOCIAL_DATA if absent) | MEDIUM |
| Risk Flags | Derived from all above | HIGH |
| Research Conclusion | Rule-based summary; one of allowed outputs only | HIGH |
| Scanner Context | research_scanner_latest.json | HIGH (if scanner ran) |

## Allowed Outputs

- "worth researching"
- "watch candidate"
- "requires manual review"
- "risk flagged — multiple concerns; requires manual review before any action"
- "extended; wait for reset"
- "potential asymmetric candidate — speculative long-term watch; high risk"
- "catalyst candidate — upcoming earnings; requires manual review"
- "beaten-down potential recovery — worth researching further"
- "crowded/viral — late-mover risk; watch candidate only after consolidation"

## Guardrails

- No buy/sell/entry/stop/target outputs
- Social data is never fabricated; degrades to NO_SOCIAL_DATA
- Tradier options are research-only; execution is disabled
- Alpaca is not required
- Manual review is required before any action

## Outputs

- `cache/research/cards/<TICKER>_research_card.json`
- `cache/research/cards/<TICKER>_research_card.txt`

## Usage

```bash
SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python research/stock_research_card.py AAPL NVDA
GEM_TRADER_SKIP_DOTENV=true .venv/bin/python research/stock_research_card.py AAPL --offline
./scripts/run_research_cycle.sh stock-research-card AAPL NVDA
```
"""
    DOC_PATH.write_text(content, encoding="utf-8")
    logger.info("wrote %s", DOC_PATH)


# ── CLI ───────────────────────────────────────────────────────────────────────


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Stock Research Card Engine (research-only)")
    parser.add_argument("tickers", nargs="+", help="Ticker symbols (e.g. AAPL NVDA MSFT)")
    parser.add_argument("--offline", action="store_true", help="Cache-only; no provider calls")
    parser.add_argument("--print", dest="print_text", action="store_true", help="Print text card to stdout")
    args = parser.parse_args(argv)

    print(RESEARCH_ONLY_BANNER)

    write_doc()

    for raw_ticker in args.tickers:
        ticker = raw_ticker.upper().strip()
        if not ticker:
            continue
        card = build_card(ticker, offline=args.offline)
        out = write_card(card)

        print(f"\n  {ticker}: {card['research_conclusion']}")
        print(f"  Artifacts: {out}")

        if args.print_text:
            print("\n" + _format_text(card))

    print("\n--- RESEARCH ONLY — NO TRADE RECOMMENDATIONS ---")


if __name__ == "__main__":
    main()

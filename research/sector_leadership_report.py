#!/usr/bin/env python3
"""
research/sector_leadership_report.py — Sector leadership ranking (research-only).

Ranks sector ETFs by relative strength vs SPY using cached price data.
Cache-only; never calls providers.

Outputs:
  cache/research/sector_leadership_latest.json
  logs/sector_leadership_latest.txt

Usage:
  GEM_TRADER_SKIP_DOTENV=true .venv/bin/python research/sector_leadership_report.py
  SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python research/sector_leadership_report.py
"""
from __future__ import annotations

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
from core.research_mode import SYSTEM_MODE, RESEARCH_ONLY_BANNER

VERSION = "SECTOR_LEADERSHIP_V1"
PRICE_DIR = cfg.CACHE_DIR / "prices"
RESEARCH_DIR = cfg.CACHE_DIR / "research"
OUT_JSON = RESEARCH_DIR / "sector_leadership_latest.json"
OUT_TXT = cfg.LOG_DIR / "sector_leadership_latest.txt"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("sector_leadership_report")

# Sector ETFs and names
SECTOR_ETFS: List[Tuple[str, str]] = [
    ("XLK", "Technology"),
    ("XLF", "Financials"),
    ("XLV", "Health Care"),
    ("XLY", "Consumer Discretionary"),
    ("XLP", "Consumer Staples"),
    ("XLE", "Energy"),
    ("XLI", "Industrials"),
    ("XLB", "Materials"),
    ("XLU", "Utilities"),
    ("XLRE", "Real Estate"),
    ("XLC", "Communication Services"),
    ("SMH", "Semiconductors"),
    ("IBB", "Biotech"),
    ("ITB", "Homebuilders"),
    ("XBI", "Biotech Small-Cap"),
    ("IYT", "Transportation"),
    ("GDX", "Gold Miners"),
    ("KRE", "Regional Banks"),
]


def _load_closes(sym: str) -> List[float]:
    path = PRICE_DIR / f"{sym.upper()}.parquet"
    if not path.exists():
        return []
    try:
        df = pd.read_parquet(path)
        col = "close" if "close" in df.columns else ("Close" if "Close" in df.columns else None)
        if col is None:
            return []
        return [float(v) for v in df[col].dropna().tolist()]
    except Exception:
        return []


def _rs(closes: List[float], spy: List[float], lb: int) -> Optional[float]:
    if len(closes) < lb + 1 or len(spy) < lb + 1:
        return None
    r_etf = closes[-1] / closes[-(lb + 1)] - 1.0
    r_spy = spy[-1] / spy[-(lb + 1)] - 1.0
    return round((r_etf - r_spy) * 100.0, 2)


def _ret(closes: List[float], lb: int) -> Optional[float]:
    if len(closes) < lb + 1:
        return None
    return round((closes[-1] / closes[-(lb + 1)] - 1.0) * 100.0, 2)


def build_report() -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    logger.info("Sector leadership report %s starting", VERSION)

    spy = _load_closes("SPY")
    spy_available = len(spy) >= 252

    rankings: List[Dict[str, Any]] = []
    for etf, name in SECTOR_ETFS:
        closes = _load_closes(etf)
        if len(closes) < 21:
            rankings.append({
                "etf": etf,
                "name": name,
                "data_available": False,
                "rs_20d_vs_spy": None,
                "rs_63d_vs_spy": None,
                "rs_252d_vs_spy": None,
                "ret_20d_pct": None,
                "ret_63d_pct": None,
                "rank_20d": None,
            })
            continue
        rs_20 = _rs(closes, spy, 20) if spy_available else None
        rs_63 = _rs(closes, spy, 63) if spy_available else None
        rs_252 = _rs(closes, spy, 252) if spy_available and len(closes) >= 253 else None
        ret_20 = _ret(closes, 20)
        ret_63 = _ret(closes, 63)
        rankings.append({
            "etf": etf,
            "name": name,
            "data_available": True,
            "rs_20d_vs_spy": rs_20,
            "rs_63d_vs_spy": rs_63,
            "rs_252d_vs_spy": rs_252,
            "ret_20d_pct": ret_20,
            "ret_63d_pct": ret_63,
        })

    # Sort by 20d RS
    ranked = sorted(
        [r for r in rankings if r.get("data_available")],
        key=lambda x: x.get("rs_20d_vs_spy") or -999,
        reverse=True,
    )
    for i, r in enumerate(ranked):
        r["rank_20d"] = i + 1

    # Identify leaders / laggards
    leaders = [r["etf"] for r in ranked[:3]]
    laggards = [r["etf"] for r in ranked[-3:] if r.get("data_available")]

    return {
        "version": VERSION,
        "generated_at": now,
        "system_mode": SYSTEM_MODE,
        "research_only": True,
        "spy_data_available": spy_available,
        "sectors_ranked": ranked,
        "sectors_no_data": [r for r in rankings if not r.get("data_available")],
        "leading_sectors_20d": leaders,
        "lagging_sectors_20d": laggards,
        "guardrails": {
            "no_trade_recommendation": True,
            "cache_only": True,
            "no_provider_calls": True,
            "alpaca_required": False,
        },
    }


def _format_text(r: Dict[str, Any]) -> str:
    lines = [
        RESEARCH_ONLY_BANNER,
        "",
        f"SECTOR LEADERSHIP REPORT  [{r['version']}]",
        f"Generated: {r['generated_at']}",
        f"SPY data available: {r['spy_data_available']}",
        "",
        "=== SECTOR RANKINGS (20d RS vs SPY) ===",
        f"  {'Rank':4s}  {'ETF':5s}  {'Name':30s}  {'RS 20d':8s}  {'RS 63d':8s}  {'Ret 20d':8s}",
        f"  {'----':4s}  {'---':5s}  {'----':30s}  {'------':8s}  {'------':8s}  {'-------':8s}",
    ]
    for sec in r.get("sectors_ranked", []):
        rank = sec.get("rank_20d", "?")
        rs20 = f"{sec['rs_20d_vs_spy']:+.1f}pp" if sec.get("rs_20d_vs_spy") is not None else "  N/A  "
        rs63 = f"{sec['rs_63d_vs_spy']:+.1f}pp" if sec.get("rs_63d_vs_spy") is not None else "  N/A  "
        ret20 = f"{sec['ret_20d_pct']:+.1f}%" if sec.get("ret_20d_pct") is not None else "  N/A  "
        lines.append(f"  {rank:4}  {sec['etf']:5s}  {sec['name']:30s}  {rs20:8s}  {rs63:8s}  {ret20:8s}")

    lines += [
        "",
        f"Leading sectors (20d): {', '.join(r.get('leading_sectors_20d', []))}",
        f"Lagging sectors (20d): {', '.join(r.get('lagging_sectors_20d', []))}",
        "",
        "--- RESEARCH ONLY — CACHE ONLY — NO TRADE RECOMMENDATIONS ---",
    ]
    return "\n".join(lines)


def main() -> None:
    print(RESEARCH_ONLY_BANNER)
    report = build_report()
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    cfg.LOG_DIR.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(report, indent=2), encoding="utf-8")
    OUT_TXT.write_text(_format_text(report), encoding="utf-8")
    logger.info("wrote %s  %s", OUT_JSON, OUT_TXT)
    print(f"\nSector leadership: {len(report['sectors_ranked'])} sectors ranked")
    print(f"Leaders (20d RS): {', '.join(report['leading_sectors_20d'])}")
    print(f"Laggards (20d RS): {', '.join(report['lagging_sectors_20d'])}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
research/data_freshness_report.py — Cache data freshness report (research-only).

Audits the staleness of price parquets and key research sidecars.
Cache-only; never calls providers.

Outputs:
  cache/research/data_freshness_latest.json
  logs/data_freshness_latest.txt

Usage:
  GEM_TRADER_SKIP_DOTENV=true .venv/bin/python research/data_freshness_report.py
  SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python research/data_freshness_report.py
"""
from __future__ import annotations

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

import core.config as cfg
from core.research_mode import SYSTEM_MODE, RESEARCH_ONLY_BANNER

VERSION = "DATA_FRESHNESS_V1"
RESEARCH_DIR = cfg.CACHE_DIR / "research"
PRICE_DIR = cfg.CACHE_DIR / "prices"
OUT_JSON = RESEARCH_DIR / "data_freshness_latest.json"
OUT_TXT = cfg.LOG_DIR / "data_freshness_latest.txt"

# Research sidecars to audit
SIDECARS: List[str] = [
    "market_heartbeat_latest.json",
    "research_scanner_latest.json",
    "regime_forecast_latest.json",
    "alpha_discovery_latest.json",
    "social_arb_latest.json",
    "social_attention_latest.json",
    "mcp_analysis_latest.json",
    "risk_telemetry_latest.json",
    "slippage_telemetry_latest.json",
    "portfolio_concentration_latest.json",
    "fmp_provider_health_latest.json",
    "tradier_research_health_latest.json",
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("data_freshness_report")


def _mtime_info(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"exists": False, "mtime": None, "age_hours": None, "status": "MISSING"}
    mtime = path.stat().st_mtime
    now_ts = datetime.now(timezone.utc).timestamp()
    age_h = (now_ts - mtime) / 3600.0
    mtime_iso = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
    status = "FRESH" if age_h < 26 else ("STALE" if age_h < 72 else "VERY_STALE")
    return {
        "exists": True,
        "mtime": mtime_iso,
        "age_hours": round(age_h, 1),
        "status": status,
    }


def _price_cache_audit() -> Dict[str, Any]:
    if not PRICE_DIR.exists():
        return {"status": "PRICE_DIR_MISSING", "files": 0}
    parquets = list(PRICE_DIR.glob("*.parquet"))
    now_ts = datetime.now(timezone.utc).timestamp()
    buckets: Dict[str, List[str]] = {"fresh_24h": [], "fresh_48h": [], "stale": [], "very_stale": []}
    for p in parquets:
        age_h = (now_ts - p.stat().st_mtime) / 3600.0
        if age_h < 24:
            buckets["fresh_24h"].append(p.stem)
        elif age_h < 48:
            buckets["fresh_48h"].append(p.stem)
        elif age_h < 168:
            buckets["stale"].append(p.stem)
        else:
            buckets["very_stale"].append(p.stem)
    spy_info = _mtime_info(PRICE_DIR / "SPY.parquet")
    return {
        "total_files": len(parquets),
        "fresh_24h": len(buckets["fresh_24h"]),
        "fresh_48h": len(buckets["fresh_48h"]),
        "stale_2_7d": len(buckets["stale"]),
        "very_stale_over_7d": len(buckets["very_stale"]),
        "spy_parquet": spy_info,
        "very_stale_tickers": sorted(buckets["very_stale"])[:10],
    }


def build_report() -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    logger.info("Data freshness report %s starting", VERSION)

    sidecar_audit: Dict[str, Any] = {}
    missing: List[str] = []
    stale: List[str] = []
    for name in SIDECARS:
        info = _mtime_info(RESEARCH_DIR / name)
        sidecar_audit[name] = info
        if not info["exists"]:
            missing.append(name)
        elif info["status"] in {"STALE", "VERY_STALE"}:
            stale.append(name)

    price_audit = _price_cache_audit()

    overall = "FRESH"
    if missing or stale:
        overall = "DEGRADED"
    if len(missing) > 5 or price_audit.get("very_stale_over_7d", 0) > 20:
        overall = "STALE"

    return {
        "version": VERSION,
        "generated_at": now,
        "system_mode": SYSTEM_MODE,
        "research_only": True,
        "overall_status": overall,
        "sidecar_audit": sidecar_audit,
        "missing_sidecars": missing,
        "stale_sidecars": stale,
        "price_cache": price_audit,
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
        f"DATA FRESHNESS REPORT  [{r['version']}]",
        f"Generated: {r['generated_at']}",
        f"Overall status: {r['overall_status']}",
        "",
        "=== RESEARCH SIDECARS ===",
    ]
    for name, info in r.get("sidecar_audit", {}).items():
        status = info.get("status", "MISSING")
        age = f"{info['age_hours']}h" if info.get("age_hours") is not None else "N/A"
        exists = info.get("exists", False)
        lines.append(f"  [{status:10s}]  {name}  {'age=' + age if exists else 'MISSING'}")

    if r.get("missing_sidecars"):
        lines += ["", f"Missing ({len(r['missing_sidecars'])}): " + ", ".join(r["missing_sidecars"])]
    if r.get("stale_sidecars"):
        lines += [f"Stale ({len(r['stale_sidecars'])}): " + ", ".join(r["stale_sidecars"])]

    pc = r.get("price_cache", {})
    lines += [
        "",
        "=== PRICE CACHE ===",
        f"  Total parquets: {pc.get('total_files', 'N/A')}",
        f"  Fresh (<24h): {pc.get('fresh_24h', 0)}  |  Fresh (<48h): {pc.get('fresh_48h', 0)}",
        f"  Stale (2-7d): {pc.get('stale_2_7d', 0)}  |  Very stale (>7d): {pc.get('very_stale_over_7d', 0)}",
    ]
    spy = pc.get("spy_parquet", {})
    lines.append(f"  SPY parquet: {spy.get('status', 'N/A')}  |  mtime={spy.get('mtime', 'N/A')}")

    lines += ["", "--- RESEARCH ONLY — CACHE ONLY — NO PROVIDER CALLS ---"]
    return "\n".join(lines)


def main() -> None:
    print(RESEARCH_ONLY_BANNER)
    report = build_report()
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    cfg.LOG_DIR.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(report, indent=2), encoding="utf-8")
    OUT_TXT.write_text(_format_text(report), encoding="utf-8")
    logger.info("wrote %s  %s", OUT_JSON, OUT_TXT)
    print(f"\nData freshness: {report['overall_status']}")
    print(f"Missing sidecars: {len(report['missing_sidecars'])}  |  Stale: {len(report['stale_sidecars'])}")
    print(f"Price cache: {report['price_cache'].get('total_files', 0)} parquets, "
          f"{report['price_cache'].get('fresh_24h', 0)} fresh <24h")


if __name__ == "__main__":
    main()

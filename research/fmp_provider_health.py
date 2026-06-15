#!/usr/bin/env python3
"""
research/fmp_provider_health.py — FMP API provider health check (research-only).

Reports FMP availability, cache freshness, and budget utilization.
Never calls providers in live-trading path; this is a standalone diagnostic.

Outputs:
  cache/research/fmp_provider_health_latest.json
  logs/fmp_provider_health_latest.txt

Usage:
  SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python research/fmp_provider_health.py
  GEM_TRADER_SKIP_DOTENV=true .venv/bin/python research/fmp_provider_health.py
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

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

VERSION = "FMP_PROVIDER_HEALTH_V1"
RESEARCH_DIR = cfg.CACHE_DIR / "research"
OUT_JSON = RESEARCH_DIR / "fmp_provider_health_latest.json"
OUT_TXT = cfg.LOG_DIR / "fmp_provider_health_latest.txt"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("fmp_provider_health")


def _fmp_key() -> str:
    return os.getenv("FMP_API_KEY", "").strip()


def _is_offline() -> bool:
    return _fmp_key().lower() in {"", "offline", "stub"}


def _check_cache_meta() -> Dict[str, Any]:
    """Inspect the cache_meta table for FMP endpoint staleness."""
    try:
        import sqlite3
        db_path = cfg.DB_PATH
        if not Path(db_path).exists():
            return {"status": "DB_UNAVAILABLE", "message": "trading.db not found"}
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute(
            "SELECT endpoint, cached_at, expires_at FROM cache_meta "
            "WHERE endpoint LIKE 'fmp:%' ORDER BY cached_at DESC LIMIT 20"
        )
        rows = [dict(r) for r in cur.fetchall()]
        con.close()
        now_iso = datetime.now(timezone.utc).isoformat()
        stale = [r for r in rows if r.get("expires_at") and r["expires_at"] < now_iso]
        return {
            "fmp_cache_entries": len(rows),
            "stale_entries": len(stale),
            "most_recent_cached_at": rows[0]["cached_at"] if rows else None,
            "sample_endpoints": [r["endpoint"] for r in rows[:5]],
        }
    except Exception as exc:
        return {"status": "CACHE_META_ERROR", "message": str(exc)}


def _check_price_cache() -> Dict[str, Any]:
    """Count parquet files and find the most/least recently updated."""
    price_dir = cfg.CACHE_DIR / "prices"
    if not price_dir.exists():
        return {"status": "PRICE_DIR_MISSING"}
    parquets = list(price_dir.glob("*.parquet"))
    if not parquets:
        return {"status": "NO_PARQUET_FILES"}
    mtimes = [(p.stem, p.stat().st_mtime) for p in parquets]
    mtimes.sort(key=lambda x: x[1], reverse=True)
    now_ts = datetime.now(timezone.utc).timestamp()
    fresh = [s for s, t in mtimes if now_ts - t < 86400 * 2]
    stale = [s for s, t in mtimes if now_ts - t >= 86400 * 2]
    return {
        "total_parquet_files": len(parquets),
        "fresh_last_48h": len(fresh),
        "stale_over_48h": len(stale),
        "newest_ticker": mtimes[0][0] if mtimes else None,
        "newest_mtime": datetime.fromtimestamp(mtimes[0][1], tz=timezone.utc).isoformat() if mtimes else None,
        "oldest_ticker": mtimes[-1][0] if mtimes else None,
        "oldest_mtime": datetime.fromtimestamp(mtimes[-1][1], tz=timezone.utc).isoformat() if mtimes else None,
    }


def _probe_fmp() -> Dict[str, Any]:
    """Attempt a lightweight FMP call to verify API connectivity."""
    if _is_offline():
        return {"status": "OFFLINE", "reason": "FMP_API_KEY not set or stub"}
    try:
        from core.fmp_client import get_fmp
        profile = get_fmp().get_company_profile("SPY")
        if isinstance(profile, dict) and profile:
            return {"status": "OK", "probe": "get_company_profile(SPY)", "company": profile.get("companyName")}
        return {"status": "DEGRADED", "reason": "Empty profile returned", "probe": "get_company_profile(SPY)"}
    except Exception as exc:
        return {"status": "UNAVAILABLE", "reason": str(exc), "probe": "get_company_profile(SPY)"}


def build_report() -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    logger.info("FMP provider health check %s starting", VERSION)

    fmp_key_present = bool(_fmp_key()) and not _is_offline()
    probe = _probe_fmp()
    cache_meta = _check_cache_meta()
    price_cache = _check_price_cache()

    overall = "OK"
    if probe.get("status") == "UNAVAILABLE":
        overall = "UNAVAILABLE"
    elif probe.get("status") == "DEGRADED" or probe.get("status") == "OFFLINE":
        overall = "DEGRADED"

    report: Dict[str, Any] = {
        "version": VERSION,
        "generated_at": now,
        "system_mode": SYSTEM_MODE,
        "research_only": True,
        "overall_status": overall,
        "fmp_key_configured": fmp_key_present,
        "api_probe": probe,
        "cache_meta": cache_meta,
        "price_cache": price_cache,
        "guardrails": {
            "no_trade_recommendation": True,
            "alpaca_required": False,
        },
    }
    return report


def _format_text(r: Dict[str, Any]) -> str:
    lines = [
        RESEARCH_ONLY_BANNER,
        "",
        f"FMP PROVIDER HEALTH  [{r['version']}]",
        f"Generated: {r['generated_at']}",
        f"Overall status: {r['overall_status']}",
        f"FMP key configured: {r['fmp_key_configured']}",
        "",
        "=== API PROBE ===",
    ]
    probe = r.get("api_probe", {})
    lines.append(f"  Status: {probe.get('status')}  |  Probe: {probe.get('probe', 'N/A')}")
    if probe.get("reason"):
        lines.append(f"  Reason: {probe['reason']}")
    if probe.get("company"):
        lines.append(f"  Response: {probe['company']}")

    lines += ["", "=== CACHE META ==="]
    cm = r.get("cache_meta", {})
    lines.append(f"  FMP cache entries: {cm.get('fmp_cache_entries', 'N/A')}  |  Stale: {cm.get('stale_entries', 'N/A')}")
    lines.append(f"  Most recent cached_at: {cm.get('most_recent_cached_at', 'N/A')}")

    lines += ["", "=== PRICE CACHE ==="]
    pc = r.get("price_cache", {})
    lines.append(f"  Parquet files: {pc.get('total_parquet_files', 'N/A')}  |  Fresh (<48h): {pc.get('fresh_last_48h', 'N/A')}  |  Stale: {pc.get('stale_over_48h', 'N/A')}")
    lines.append(f"  Newest: {pc.get('newest_ticker', 'N/A')} @ {pc.get('newest_mtime', 'N/A')}")
    lines.append(f"  Oldest: {pc.get('oldest_ticker', 'N/A')} @ {pc.get('oldest_mtime', 'N/A')}")

    lines += ["", "--- RESEARCH ONLY ---"]
    return "\n".join(lines)


def main() -> None:
    print(RESEARCH_ONLY_BANNER)
    report = build_report()
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    cfg.LOG_DIR.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(report, indent=2), encoding="utf-8")
    OUT_TXT.write_text(_format_text(report), encoding="utf-8")
    logger.info("wrote %s  %s", OUT_JSON, OUT_TXT)
    print(f"\nFMP provider health: {report['overall_status']}")
    print(f"Price cache: {report['price_cache'].get('total_parquet_files', 'N/A')} parquets, "
          f"{report['price_cache'].get('fresh_last_48h', 'N/A')} fresh")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
research/tradier_research_health.py — Tradier research-only provider health check.

Verifies Tradier token configuration, account access, and that execution is
permanently disabled. Does NOT place orders or fetch live execution state.

Outputs:
  cache/research/tradier_research_health_latest.json
  logs/tradier_research_health_latest.txt

Usage:
  SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python research/tradier_research_health.py
  GEM_TRADER_SKIP_DOTENV=true .venv/bin/python research/tradier_research_health.py
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
from core.research_mode import (
    SYSTEM_MODE,
    RESEARCH_ONLY_BANNER,
    TRADIER_RESEARCH_ENABLED,
    TRADIER_EXECUTION_ENABLED,
)

VERSION = "TRADIER_RESEARCH_HEALTH_V1"
RESEARCH_DIR = cfg.CACHE_DIR / "research"
OUT_JSON = RESEARCH_DIR / "tradier_research_health_latest.json"
OUT_TXT = cfg.LOG_DIR / "tradier_research_health_latest.txt"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("tradier_research_health")


def _tradier_token() -> str:
    return os.getenv("TRADIER_API_TOKEN", "").strip()


def _is_offline() -> bool:
    tok = _tradier_token()
    return not tok or tok.lower() in {"offline", "stub"}


def _probe_tradier() -> Dict[str, Any]:
    """Attempt a lightweight Tradier call (market clock) to verify connectivity."""
    if not TRADIER_RESEARCH_ENABLED:
        return {"status": "DISABLED", "reason": "TRADIER_RESEARCH_ENABLED=False in research_mode.py"}
    if TRADIER_EXECUTION_ENABLED:
        return {"status": "CONFIG_ERROR", "reason": "TRADIER_EXECUTION_ENABLED must be False in RESEARCH_ONLY_MODE"}
    if _is_offline():
        return {"status": "OFFLINE", "reason": "TRADIER_API_TOKEN not set or stub"}
    try:
        import requests
        token = _tradier_token()
        base = os.getenv("TRADIER_BASE_URL", "https://api.tradier.com/v1")
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        r = requests.get(f"{base}/markets/clock", headers=headers, timeout=8)
        if r.status_code == 200:
            data = r.json().get("clock") or {}
            return {
                "status": "OK",
                "probe": "GET /markets/clock",
                "market_state": data.get("state"),
                "description": data.get("description"),
            }
        return {
            "status": "DEGRADED",
            "probe": "GET /markets/clock",
            "http_status": r.status_code,
        }
    except Exception as exc:
        return {"status": "UNAVAILABLE", "reason": str(exc), "probe": "GET /markets/clock"}


def build_report() -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    logger.info("Tradier research health check %s starting", VERSION)

    token_present = not _is_offline()
    probe = _probe_tradier()

    overall = "OK"
    if probe.get("status") in {"UNAVAILABLE", "CONFIG_ERROR"}:
        overall = "UNAVAILABLE"
    elif probe.get("status") in {"DEGRADED", "OFFLINE", "DISABLED"}:
        overall = "DEGRADED"

    report: Dict[str, Any] = {
        "version": VERSION,
        "generated_at": now,
        "system_mode": SYSTEM_MODE,
        "research_only": True,
        "overall_status": overall,
        "tradier_token_configured": token_present,
        "tradier_research_enabled": TRADIER_RESEARCH_ENABLED,
        "tradier_execution_enabled": TRADIER_EXECUTION_ENABLED,
        "execution_permanently_disabled": not TRADIER_EXECUTION_ENABLED,
        "api_probe": probe,
        "guardrails": {
            "no_trade_recommendation": True,
            "no_order_placement": True,
            "options_research_only": True,
            "alpaca_required": False,
        },
    }
    return report


def _format_text(r: Dict[str, Any]) -> str:
    lines = [
        RESEARCH_ONLY_BANNER,
        "",
        f"TRADIER RESEARCH HEALTH  [{r['version']}]",
        f"Generated: {r['generated_at']}",
        f"Overall status: {r['overall_status']}",
        f"Token configured: {r['tradier_token_configured']}",
        f"Research enabled: {r['tradier_research_enabled']}  |  Execution disabled: {r['execution_permanently_disabled']}",
        "",
        "=== API PROBE ===",
    ]
    probe = r.get("api_probe", {})
    lines.append(f"  Status: {probe.get('status')}  |  Probe: {probe.get('probe', 'N/A')}")
    if probe.get("reason"):
        lines.append(f"  Reason: {probe['reason']}")
    if probe.get("market_state"):
        lines.append(f"  Market state: {probe['market_state']}  ({probe.get('description', '')})")

    lines += [
        "",
        "NOTE: Tradier is used ONLY for options research (OI, put/call ratio, IV).",
        "      Execution via Tradier is permanently disabled in RESEARCH_ONLY_MODE.",
        "",
        "--- RESEARCH ONLY ---",
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
    print(f"\nTradier research health: {report['overall_status']}")
    print(f"Execution permanently disabled: {report['execution_permanently_disabled']}")


if __name__ == "__main__":
    main()

"""
vix_snapshot.py — Centralized VIX snapshot. FMP primary, Alpaca VXX proxy fallback.

yfinance REMOVED. Source order:
  1. FMP /v3/quote/%5EVIX  (cached 5 min via Gatekeeper)
  2. Alpaca VXX proxy       (VXX close × 0.85)
  3. unavailable
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 30.0
_CACHE: Dict[str, tuple] = {}


def _cache_get(key: str) -> Optional[Dict[str, Any]]:
    record = _CACHE.get(key)
    if not record:
        return None
    ts, payload = record
    if (time.time() - ts) <= _CACHE_TTL_SECONDS:
        return dict(payload)
    return None


def _cache_set(key: str, payload: Dict[str, Any]) -> None:
    _CACHE[key] = (time.time(), dict(payload))


def _snapshot(level, source, confidence, reason, available) -> Dict[str, Any]:
    return {
        "vix_level": round(float(level), 2) if isinstance(level, (int, float)) else None,
        "source": source,
        "confidence": confidence,
        "available": bool(available),
        "reason": reason,
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


def get_vix_snapshot(data_feed=None) -> Dict[str, Any]:
    cached = _cache_get("vix_snapshot")
    if cached is not None:
        return cached

    # ── Source 1: FMP ─────────────────────────────────────────────────────────
    try:
        from core.fmp_client import get_fmp
        level = get_fmp().get_vix()
        if level and 5.0 < level < 100.0:
            payload = _snapshot(level, "fmp", "HIGH", "live ^VIX via FMP", True)
            _cache_set("vix_snapshot", payload)
            return payload
    except Exception as exc:
        logger.debug("FMP VIX failed: %s", exc)

    # ── Source 2: Alpaca VXX proxy ────────────────────────────────────────────
    try:
        from alpaca_data import AlpacaDataFeed
        feed = data_feed or AlpacaDataFeed()
        bars = feed.get_daily_bars("VXX", days_back=5, adjustment="all")
        if bars:
            vxx_price = float(bars[-1]["close"])
            proxy = max(10.0, min(80.0, vxx_price * 0.85))
            payload = _snapshot(proxy, "alpaca_vxx_proxy", "MEDIUM", f"VXX proxy {vxx_price:.2f}", True)
            _cache_set("vix_snapshot", payload)
            return payload
    except Exception as exc:
        logger.debug("VXX proxy failed: %s", exc)

    payload = _snapshot(None, "unavailable", "LOW", "no_vix_source_available", False)
    _cache_set("vix_snapshot", payload)
    return payload

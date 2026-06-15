#!/usr/bin/env python3
"""
scripts/nightly_refresh.py — Nightly cache cleanup and data pre-warm.

Called by gem-trader-nightly.service at 03:30 ET Mon–Fri.
Runs in about 10–20 s; exits when done (Type=oneshot service).
"""
import os
import sys
from pathlib import Path

# Load credentials (same pattern as the rest of the system)
cred = os.environ.get("SNIPER_ENV_PATH", "/home/gem/secure/trading.env")
if Path(cred).exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(cred, override=True)
    except ImportError:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("nightly")

import core.config as cfg

# ── Cache cleanup ──────────────────────────────────────────────────────────────
try:
    from core.data_gatekeeper import get_gatekeeper
    gate = get_gatekeeper()
    if hasattr(gate, "expire_stale"):
        removed = gate.expire_stale(max_age_hours=24)
        logger.info("Cache cleanup: %d stale entries removed", removed)
    else:
        logger.info("Cache cleanup: expire_stale() not available — skipping")
except Exception as exc:
    logger.warning("Cache cleanup failed: %s", exc)

# ── Pre-warm FMP data ──────────────────────────────────────────────────────────
try:
    from core.fmp_client import get_fmp
    fmp = get_fmp()

    spy = fmp.get_spy_bars(days=90)
    logger.info("Pre-warmed: SPY %d bars", len(spy))

    vix = fmp.get_vix()
    logger.info("Pre-warmed: VIX=%.1f", vix or 0)

    rates = fmp.get_treasury_rates()
    if rates:
        y10 = float(rates.get("year10", rates.get("tenYear", 0)) or 0)
        logger.info("Pre-warmed: 10y treasury=%.2f%%", y10)

except Exception as exc:
    logger.warning("FMP pre-warm failed: %s", exc)

# ── Pre-warm regime-forecast price cache via FMP ───────────────────────────────
# Phase 3A: Alpaca SIP removed. regime_forecast.py now refreshes cache/prices/
# parquets via FMP when it detects stale data (provider chain: Alpaca-stub →
# FMP → yfinance). This block gives the nightly timer a head start so the
# 20:30 ET research run doesn't spend FMP budget on all symbols at once.
try:
    from research.regime_forecast import ALL_SYMBOLS as _RF_SYMBOLS
    from core.fmp_client import get_fmp
    from pathlib import Path as _Path
    import pandas as _pd

    _PRICE_DIR = _Path(__file__).resolve().parent.parent / "cache" / "prices"
    _fmp = get_fmp()
    fresh = 0
    for _sym in _RF_SYMBOLS:
        try:
            bars = _fmp.get_ticker_bars(_sym, days=260)
            if not bars:
                continue
            df = _pd.DataFrame(bars)
            if "date" in df.columns:
                df["date"] = _pd.to_datetime(df["date"])
                df.set_index("date", inplace=True)
                df.sort_index(inplace=True)
                cols = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
                _PRICE_DIR.mkdir(parents=True, exist_ok=True)
                df[cols].to_parquet(_PRICE_DIR / f"{_sym.upper()}.parquet", compression="snappy")
                fresh += 1
        except Exception:
            pass
    logger.info("Pre-warmed regime parquets via FMP: %d/%d symbols", fresh, len(_RF_SYMBOLS))
except Exception as exc:
    logger.warning("Regime parquet FMP pre-warm failed: %s", exc)

logger.info("Nightly refresh complete.")

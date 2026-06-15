#!/usr/bin/env python3
"""
research/research_coverage_audit.py — Data confidence audit for the research engine.

Scans the price cache and cross-references FMP profile availability, options
snapshot depth, and social data to produce a per-ticker DATA_CONFIDENCE score.

Confidence levels:
  HIGH     — price ≥ 90 bars, parquet age ≤ 3 days, FMP profile cached
  MEDIUM   — price ≥ 60 bars OR parquet age ≤ 7 days (FMP profile missing OK)
  LOW      — price ≥ 20 bars but stale (>7 days) OR bars < 60
  INVALID  — price < 20 bars OR no parquet file

Outputs:
  cache/research/research_coverage_latest.json
  logs/research_coverage_latest.txt
  docs/research/RESEARCH_COVERAGE_AUDIT.md  (on first run)

Usage:
  GEM_TRADER_SKIP_DOTENV=true .venv/bin/python research/research_coverage_audit.py
  SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python research/research_coverage_audit.py
  ./scripts/run_research_cycle.sh research-coverage
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
    load_dotenv = None  # type: ignore[assignment]

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

VERSION = "RESEARCH_COVERAGE_V1"
PRICE_DIR = cfg.CACHE_DIR / "prices"
RESEARCH_DIR = cfg.CACHE_DIR / "research"
OPTIONS_DIR = cfg.CACHE_DIR / "options_chains"
SOCIAL_DIR = cfg.CACHE_DIR / "research"
OUT_JSON = RESEARCH_DIR / "research_coverage_latest.json"
OUT_TXT = cfg.LOG_DIR / "research_coverage_latest.txt"
DOC_PATH = ROOT / "docs" / "research" / "RESEARCH_COVERAGE_AUDIT.md"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("research_coverage_audit")

# ── Confidence thresholds ─────────────────────────────────────────────────────
MIN_BARS_INVALID = 20
MIN_BARS_LOW = 60
MIN_BARS_HIGH = 90
STALE_LOW_DAYS = 7
STALE_HIGH_DAYS = 3


def _parquet_stats(path: Path) -> Dict[str, Any]:
    """Return bar count, age in days, and last close date for a parquet file."""
    if not path.exists():
        return {"bars": 0, "age_days": None, "last_date": None}
    try:
        stat = path.stat()
        age_days = (datetime.now(timezone.utc).timestamp() - stat.st_mtime) / 86400.0
        df = pd.read_parquet(path)
        col = next((c for c in ("close", "Close") if c in df.columns), None)
        bars = int(df[col].dropna().shape[0]) if col else 0
        last_date = None
        date_col = next((c for c in ("date", "Date", "timestamp", "Timestamp") if c in df.columns), None)
        if date_col and bars > 0:
            try:
                last_date = str(df[date_col].dropna().iloc[-1])[:10]
            except Exception:
                pass
        return {"bars": bars, "age_days": round(age_days, 2), "last_date": last_date}
    except Exception:
        return {"bars": 0, "age_days": None, "last_date": None}


def _fmp_profile_cached(ticker: str) -> bool:
    """Check whether an FMP profile exists in the cache_meta DB."""
    try:
        import sqlite3
        db_path = cfg.DB_PATH
        if not Path(str(db_path)).exists():
            return False
        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute(
                "SELECT 1 FROM cache_meta WHERE key = ? LIMIT 1",
                (f"fmp:profile:{ticker.upper()}",),
            ).fetchone()
            return row is not None
    except Exception:
        return False


def _options_snapshot_available(ticker: str) -> bool:
    """Check whether a recent options chain snapshot exists for this ticker."""
    if not OPTIONS_DIR.exists():
        return False
    # Options collector stores chains at cache/options_chains/<TICKER>_*.parquet or .json
    patterns = [f"{ticker.upper()}_*.parquet", f"{ticker.upper()}_*.json", f"{ticker.upper()}.json"]
    return any(bool(list(OPTIONS_DIR.glob(p))) for p in patterns)


def _social_data_available() -> bool:
    """Check whether a social attention sidecar is present."""
    candidates = [
        SOCIAL_DIR / "social_arb_latest.json",
        SOCIAL_DIR / "social_attention_latest.json",
    ]
    return any(p.exists() for p in candidates)


def _confidence_level(bars: int, age_days: Optional[float], fmp_cached: bool) -> str:
    if bars < MIN_BARS_INVALID:
        return "INVALID"
    if bars < MIN_BARS_LOW:
        return "LOW"
    # bars >= 60
    if age_days is not None and age_days > STALE_LOW_DAYS:
        return "LOW"
    if bars >= MIN_BARS_HIGH and (age_days is None or age_days <= STALE_HIGH_DAYS) and fmp_cached:
        return "HIGH"
    return "MEDIUM"


def build_coverage_audit() -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    logger.info("Research Coverage Audit %s starting", VERSION)

    price_files = sorted(PRICE_DIR.glob("*.parquet"))
    logger.info("Found %d parquet files in %s", len(price_files), PRICE_DIR)

    social_available = _social_data_available()

    tickers: List[Dict[str, Any]] = []
    counts: Dict[str, int] = {"HIGH": 0, "MEDIUM": 0, "LOW": 0, "INVALID": 0}

    for pf in price_files:
        sym = pf.stem.upper()
        stats = _parquet_stats(pf)
        fmp = _fmp_profile_cached(sym)
        opts = _options_snapshot_available(sym)
        conf = _confidence_level(stats["bars"], stats["age_days"], fmp)
        counts[conf] = counts.get(conf, 0) + 1
        tickers.append({
            "ticker": sym,
            "confidence": conf,
            "price_bars": stats["bars"],
            "price_age_days": stats["age_days"],
            "price_last_date": stats["last_date"],
            "fmp_profile_cached": fmp,
            "options_snapshot_available": opts,
        })

    tickers.sort(key=lambda x: ({"HIGH": 0, "MEDIUM": 1, "LOW": 2, "INVALID": 3}[x["confidence"]], x["ticker"]))

    total = len(tickers)
    high_pct = round(counts["HIGH"] / total * 100, 1) if total else 0
    actionable_pct = round((counts["HIGH"] + counts["MEDIUM"]) / total * 100, 1) if total else 0

    out: Dict[str, Any] = {
        "version": VERSION,
        "generated_at": now,
        "system_mode": SYSTEM_MODE,
        "research_only": True,
        "total_tickers": total,
        "confidence_counts": counts,
        "high_confidence_pct": high_pct,
        "actionable_pct": actionable_pct,
        "social_data_available": social_available,
        "tickers": tickers,
        "guardrails": {
            "no_trade_recommendation": True,
            "diagnostic_only": True,
        },
    }
    logger.info(
        "Coverage audit complete: %d total, HIGH=%d MEDIUM=%d LOW=%d INVALID=%d",
        total, counts["HIGH"], counts["MEDIUM"], counts["LOW"], counts["INVALID"],
    )
    return out


def _format_text(audit: Dict[str, Any]) -> str:
    lines = [
        RESEARCH_ONLY_BANNER,
        "",
        f"RESEARCH COVERAGE AUDIT  [{audit['version']}]",
        f"Generated: {audit['generated_at']}",
        f"Total tickers: {audit['total_tickers']}",
        "",
        "Confidence distribution:",
    ]
    counts = audit.get("confidence_counts", {})
    total = audit["total_tickers"] or 1
    for lvl in ("HIGH", "MEDIUM", "LOW", "INVALID"):
        n = counts.get(lvl, 0)
        pct = round(n / total * 100, 1)
        lines.append(f"  {lvl:<8}  {n:4d}  ({pct:.1f}%)")
    lines += [
        "",
        f"Actionable (HIGH+MEDIUM): {audit['actionable_pct']:.1f}%",
        f"Social data available:    {'yes' if audit['social_data_available'] else 'no'}",
        "",
        "=== HIGH CONFIDENCE TICKERS ===",
    ]
    for item in [t for t in audit["tickers"] if t["confidence"] == "HIGH"][:30]:
        fmp = "FMP✓" if item["fmp_profile_cached"] else "FMP✗"
        opts = "OPT✓" if item["options_snapshot_available"] else ""
        lines.append(
            f"  {item['ticker']:<6}  bars={item['price_bars']:4d}  "
            f"age={item['price_age_days'] or '?':>5}d  {fmp}  {opts}"
        )
    lines += [
        "",
        "=== LOW / INVALID TICKERS ===",
    ]
    for item in [t for t in audit["tickers"] if t["confidence"] in ("LOW", "INVALID")][:20]:
        lines.append(
            f"  {item['ticker']:<6}  [{item['confidence']:<7}]  bars={item['price_bars']:4d}  "
            f"age={item['price_age_days'] or '?':>5}d"
        )
    lines += ["", "--- RESEARCH ONLY — DIAGNOSTIC DATA ONLY ---"]
    return "\n".join(lines)


def _write_doc() -> None:
    if DOC_PATH.exists():
        return
    DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    DOC_PATH.write_text("""\
# RESEARCH COVERAGE AUDIT

**Module:** `research/research_coverage_audit.py`
**Phase:** 4A — Task 1
**Mode:** RESEARCH_ONLY — diagnostic only, no trade recommendations.

## Purpose

Audits data availability and freshness across the price cache universe.
Assigns a DATA_CONFIDENCE level per ticker so downstream research scripts
can weight their findings appropriately.

## Confidence Levels

| Level   | Criteria |
|---------|----------|
| HIGH    | ≥ 90 price bars, parquet age ≤ 3 days, FMP profile in cache |
| MEDIUM  | ≥ 60 bars, age ≤ 7 days (FMP optional) |
| LOW     | ≥ 20 bars but stale (> 7 days) OR bars < 60 |
| INVALID | < 20 bars or no parquet file |

## Outputs

- `cache/research/research_coverage_latest.json`
- `logs/research_coverage_latest.txt`

## Usage

```bash
GEM_TRADER_SKIP_DOTENV=true .venv/bin/python research/research_coverage_audit.py
./scripts/run_research_cycle.sh research-coverage
```
""", encoding="utf-8")
    logger.info("wrote %s", DOC_PATH)


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Research Coverage Audit (research-only)")
    parser.parse_args()

    print(RESEARCH_ONLY_BANNER)
    audit = build_coverage_audit()

    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    cfg.LOG_DIR.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(audit, indent=2), encoding="utf-8")
    OUT_TXT.write_text(_format_text(audit), encoding="utf-8")
    _write_doc()

    print(f"\nCoverage audit complete.")
    print(f"Total: {audit['total_tickers']} tickers")
    counts = audit["confidence_counts"]
    print(f"HIGH={counts['HIGH']}  MEDIUM={counts['MEDIUM']}  LOW={counts['LOW']}  INVALID={counts['INVALID']}")
    print(f"Actionable: {audit['actionable_pct']:.1f}%")
    print(f"Artifact: {OUT_JSON}")


if __name__ == "__main__":
    main()

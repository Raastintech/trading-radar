#!/usr/bin/env python3
"""
research/ten_x_candidate_radar.py — 10x speculative research candidate radar.

A focused scan for names that could deliver outsized multi-year returns based
on price structure, drawdown recovery potential, theme exposure, and (when
available) fundamental signals.  This is NOT a short-term trading scanner.

Criteria (any two or more required to appear):
  - Large ATH drawdown (>40%) with price momentum turning positive
  - In a speculative growth theme (AI, biotech, space, EV, semis, etc.)
  - RS vs SPY recovering: rs_63 > 0 after being negative
  - Volume surge: vol_trend_ratio > 1.2
  - Market cap < $5B (small-cap upside leverage)

Outputs labels (research framing only):
  SPECULATIVE_10X   — meets 3+ criteria; highest research priority
  ASYMMETRIC_WATCH  — meets 2 criteria; worth monitoring
  THEME_ONLY        — theme exposure but no price confirmation

Outputs:
  cache/research/ten_x_candidates_latest.json
  logs/ten_x_candidates_latest.txt

Usage:
  GEM_TRADER_SKIP_DOTENV=true .venv/bin/python research/ten_x_candidate_radar.py
  SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python research/ten_x_candidate_radar.py --offline
  ./scripts/run_research_cycle.sh ten-x-candidates
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

VERSION = "TEN_X_RADAR_V1"
PRICE_DIR = cfg.CACHE_DIR / "prices"
RESEARCH_DIR = cfg.CACHE_DIR / "research"
OUT_JSON = RESEARCH_DIR / "ten_x_candidates_latest.json"
OUT_TXT = cfg.LOG_DIR / "ten_x_candidates_latest.txt"

DEFAULT_UNIVERSE_CAP = 300
MIN_BARS = 60

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("ten_x_candidate_radar")

# Speculative growth themes (keyword matching on FMP profile text / sector / industry)
SPECULATIVE_THEMES: List[Tuple[str, str]] = [
    ("artificial intelligence", "AI"),
    ("machine learning", "AI"),
    ("semiconductor", "SEMIS"),
    ("nvidia", "SEMIS"),
    ("quantum", "QUANTUM"),
    ("biotech", "BIOTECH"),
    ("genomics", "GENOMICS"),
    ("crispr", "GENOMICS"),
    ("space", "SPACE"),
    ("rocket", "SPACE"),
    ("satellite", "SPACE"),
    ("electric vehicle", "EV"),
    ("autonomous", "EV/AUTO"),
    ("renewable energy", "ENERGY_TRANSITION"),
    ("solar", "ENERGY_TRANSITION"),
    ("hydrogen", "ENERGY_TRANSITION"),
    ("nuclear", "ENERGY_TRANSITION"),
    ("cybersecurity", "CYBERSEC"),
    ("cloud", "CLOUD"),
    ("software as a service", "SAAS"),
    ("fintech", "FINTECH"),
    ("cryptocurrency", "CRYPTO"),
    ("blockchain", "CRYPTO"),
]

SMALL_CAP_THRESHOLD = 5_000_000_000  # $5B


def _load_cached_frame(sym: str) -> Optional[Any]:
    path = PRICE_DIR / f"{sym.upper()}.parquet"
    if not path.exists():
        return None
    try:
        return pd.read_parquet(path)
    except Exception:
        return None


def _closes(df: Optional[Any]) -> List[float]:
    if df is None:
        return []
    col = next((c for c in ("close", "Close") if c in df.columns), None)
    return [float(v) for v in df[col].dropna().tolist()] if col else []


def _volumes(df: Optional[Any]) -> List[float]:
    if df is None:
        return []
    col = next((c for c in ("volume", "Volume") if c in df.columns), None)
    return [float(v) for v in df[col].dropna().tolist()] if col else []


def _rs_vs_spy(closes: List[float], spy_closes: List[float], lookback: int) -> Optional[float]:
    if len(closes) < lookback or len(spy_closes) < lookback:
        return None
    try:
        tk_ret = (closes[-1] / closes[-lookback] - 1.0) * 100.0
        spy_ret = (spy_closes[-1] / spy_closes[-lookback] - 1.0) * 100.0
        return round(tk_ret - spy_ret, 2)
    except Exception:
        return None


def _drawdown_from_high(closes: List[float]) -> Optional[float]:
    if not closes:
        return None
    peak = max(closes)
    if peak <= 0:
        return None
    return round((closes[-1] / peak - 1.0) * 100.0, 2)


def _vol_trend(volumes: List[float], short: int = 10, long: int = 30) -> Optional[float]:
    if len(volumes) < long:
        return None
    try:
        avg_short = sum(volumes[-short:]) / short
        avg_long = sum(volumes[-long:]) / long
        return round(avg_short / avg_long, 3) if avg_long > 0 else None
    except Exception:
        return None


def _fmp_profile_from_cache(ticker: str) -> Optional[Dict[str, Any]]:
    """Read FMP profile from cache_meta DB (no provider call)."""
    try:
        import sqlite3
        import time
        db_path = cfg.DB_PATH
        if not Path(str(db_path)).exists():
            return None
        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute(
                "SELECT value FROM cache_meta WHERE key = ?",
                (f"fmp:profile:{ticker.upper()}",),
            ).fetchone()
            if row:
                return json.loads(row[0])
    except Exception:
        pass
    return None


def _detect_themes(profile: Optional[Dict[str, Any]], sym: str) -> List[str]:
    if not profile:
        return []
    text = " ".join([
        str(profile.get("description") or ""),
        str(profile.get("sector") or ""),
        str(profile.get("industry") or ""),
        sym.lower(),
    ]).lower()
    found = []
    seen_labels: set = set()
    for keyword, label in SPECULATIVE_THEMES:
        if keyword in text and label not in seen_labels:
            found.append(label)
            seen_labels.add(label)
    return found


def _build_universe(cap: int = DEFAULT_UNIVERSE_CAP) -> List[str]:
    universe: List[str] = []
    seen: set = set()
    for pf in sorted(PRICE_DIR.glob("*.parquet")):
        sym = pf.stem.upper()
        if sym not in seen:
            universe.append(sym)
            seen.add(sym)
        if len(universe) >= cap:
            break
    return universe


def scan_ten_x(
    offline: bool = False,
    universe_cap: int = DEFAULT_UNIVERSE_CAP,
    max_results: int = 30,
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    logger.info("10x Candidate Radar %s starting (offline=%s)", VERSION, offline)

    universe = _build_universe(universe_cap)
    spy_df = _load_cached_frame("SPY")
    spy_closes = _closes(spy_df)

    candidates: List[Dict[str, Any]] = []
    skipped = 0

    for sym in universe:
        df = _load_cached_frame(sym)
        closes = _closes(df)
        volumes = _volumes(df)

        if len(closes) < MIN_BARS:
            skipped += 1
            continue

        dd = _drawdown_from_high(closes)
        rs_63 = _rs_vs_spy(closes, spy_closes, 63)
        rs_20 = _rs_vs_spy(closes, spy_closes, 20)
        vol_tr = _vol_trend(volumes)

        # Check ATH drawdown criterion (>40% down from high)
        large_dd = dd is not None and dd < -40

        # Price momentum turning: rs_63 in [-5, +30] (not deeply negative, but recovering)
        rs_recovering = rs_63 is not None and rs_63 > 0 and rs_63 < 40

        # Volume surge
        vol_surge = vol_tr is not None and vol_tr > 1.2

        # FMP theme check (cache-only)
        profile = _fmp_profile_from_cache(sym) if not offline else None
        themes = _detect_themes(profile, sym)
        in_theme = len(themes) > 0

        # Market cap filter
        market_cap = None
        small_cap = False
        if profile:
            market_cap = profile.get("mktCap") or profile.get("marketCap") or profile.get("market_cap")
            if market_cap and market_cap < SMALL_CAP_THRESHOLD:
                small_cap = True

        # Criteria score: how many signals fire?
        criteria_met = sum([
            large_dd,
            rs_recovering,
            vol_surge,
            in_theme,
            small_cap,
        ])

        if criteria_met < 2:
            continue

        # Assign label
        if criteria_met >= 3:
            label = "SPECULATIVE_10X"
        elif in_theme and not rs_recovering:
            label = "THEME_ONLY"
        else:
            label = "ASYMMETRIC_WATCH"

        # Research score (not a trading signal)
        score = 40.0 + criteria_met * 12.0
        if large_dd and rs_recovering:
            score += 5  # core asymmetric setup
        if vol_surge:
            score += 5
        score = min(100.0, score)

        candidates.append({
            "ticker": sym,
            "label": label,
            "research_score": round(score, 1),
            "criteria_met": criteria_met,
            "criteria_flags": {
                "large_drawdown_40pct": bool(large_dd),
                "rs_recovering": bool(rs_recovering),
                "volume_surge": bool(vol_surge),
                "speculative_theme": bool(in_theme),
                "small_cap": bool(small_cap),
            },
            "dd_from_high_pct": dd,
            "rs_63d_vs_spy": rs_63,
            "rs_20d_vs_spy": rs_20,
            "vol_trend_ratio": vol_tr,
            "themes": themes,
            "market_cap": market_cap,
            "data_source": "price_cache_and_fmp_cache",
            "refresh_cadence": "weekly",
            "research_note": "Speculative — multi-year thesis required. Manual research only.",
            "no_trade_recommendation": True,
            "speculative_disclaimer": (
                "These are clearly speculative candidates requiring extensive manual research. "
                "High risk of permanent capital loss. No position sizing, entry, or stop implied."
            ),
        })

    candidates.sort(key=lambda x: x["research_score"], reverse=True)
    candidates = candidates[:max_results]

    label_counts: Dict[str, int] = {}
    for c in candidates:
        label_counts[c["label"]] = label_counts.get(c["label"], 0) + 1

    return {
        "version": VERSION,
        "generated_at": now,
        "system_mode": SYSTEM_MODE,
        "research_only": True,
        "universe_size": len(universe),
        "candidates": candidates,
        "candidate_count": len(candidates),
        "label_counts": label_counts,
        "skipped_insufficient_bars": skipped,
        "guardrails": {
            "no_trade_recommendation": True,
            "no_buy_sell": True,
            "no_entry_stop_target": True,
            "no_position_sizing": True,
            "speculative_research_only": True,
            "manual_review_required": True,
        },
    }


def _format_text(result: Dict[str, Any]) -> str:
    lines = [
        RESEARCH_ONLY_BANNER,
        "",
        f"10x CANDIDATE RADAR  [{result['version']}]",
        f"Generated: {result['generated_at']}",
        f"Universe: {result['universe_size']} tickers",
        f"Candidates: {result['candidate_count']}",
        "",
        "⚠  SPECULATIVE — HIGH RISK — MANUAL RESEARCH REQUIRED  ⚠",
        "   These are research ideas, not trade recommendations.",
        "",
        "Label summary:",
    ]
    for lbl, cnt in sorted(result.get("label_counts", {}).items(), key=lambda x: -x[1]):
        lines.append(f"  {lbl:<22}  {cnt:3d}")

    lines += ["", "=== TOP CANDIDATES ==="]
    for c in result.get("candidates", [])[:20]:
        themes = ",".join(c.get("themes", [])[:3]) or "—"
        lines.append(
            f"  [{c['label']:<18}]  {c['ticker']:<6}  "
            f"score={c['research_score']:5.1f}  dd={c.get('dd_from_high_pct') or '?':>7}%  "
            f"rs63={c.get('rs_63d_vs_spy') or '?':>6}  "
            f"themes=[{themes}]"
        )

    lines += [
        "",
        "⚠  Research only. No trade recommendations. Extensive due diligence required.",
        "--- RESEARCH ONLY — SPECULATIVE WATCH LIST ---",
    ]
    return "\n".join(lines)


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="10x Candidate Radar (research-only)")
    parser.add_argument("--offline", action="store_true", help="Skip FMP cache lookup")
    parser.add_argument("--cap", type=int, default=DEFAULT_UNIVERSE_CAP)
    parser.add_argument("--max", type=int, default=30)
    args = parser.parse_args()

    print(RESEARCH_ONLY_BANNER)
    result = scan_ten_x(offline=args.offline, universe_cap=args.cap, max_results=args.max)

    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    cfg.LOG_DIR.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(result, indent=2), encoding="utf-8")
    OUT_TXT.write_text(_format_text(result), encoding="utf-8")
    logger.info("wrote %s", OUT_JSON)

    print(f"\n10x Candidate Radar complete.")
    print(f"Universe: {result['universe_size']} tickers")
    print(f"Candidates: {result['candidate_count']}")
    for lbl, cnt in sorted(result.get("label_counts", {}).items(), key=lambda x: -x[1]):
        print(f"  {lbl:<22}  {cnt}")
    print(f"\nArtifact: {OUT_JSON}")
    print("\n⚠  Speculative research only. No trade recommendations.")


if __name__ == "__main__":
    main()

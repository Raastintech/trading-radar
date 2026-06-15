#!/usr/bin/env python3
"""
research/regime_forecast.py — Market & Sector Regime Forecaster V1 (Phase 1).

Research-only CLI runner.  Loads ETF / risk-proxy price history (parquet cache
first, then Alpaca, then yfinance fallback), pulls a recent VIX series, and
hands the inputs to ``core.regime_forecaster.build_forecast``.

Writes:
  - cache/research/regime_forecast_latest.json   — machine-readable artifact
  - logs/regime_forecast_latest.txt              — human-readable summary

This script is the only place provider calls are allowed.  The dashboard, when
later wired up in Phase 3, must read only the JSON artifact above.

Guardrails:
  - research-only / not trade approval
  - no paper evidence, no governance, no execution
  - no sleeve mutation; Alpha Discovery / Market Posture / Daily Entry
    Validator are unchanged
  - no news/social as a core forecasting input
  - no 13F as a timing input
  - cache-first; degrades gracefully when data layers are missing

Usage:
  SNIPER_ENV_PATH=/home/gem/secure/trading.env \
    .venv/bin/python research/regime_forecast.py --mode daily

Smoke (offline-friendly):
  GEM_TRADER_SKIP_DOTENV=true \
    .venv/bin/python research/regime_forecast.py --mode daily --offline
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None

if load_dotenv is not None and os.getenv("GEM_TRADER_SKIP_DOTENV", "").lower() not in {"1", "true", "yes"}:
    env_path = os.getenv("SNIPER_ENV_PATH", "").strip()
    if env_path:
        load_dotenv(env_path, override=False)
    load_dotenv(ROOT / ".env", override=False)

# Allow compile/smoke runs without real credentials.  Real provider calls still
# fail fast/quiet on these placeholders.
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
from core.regime_forecaster import (
    MARKET_ETFS,
    SECTOR_ETFS,
    RISK_PROXIES,
    SECTOR_NAMES,
    VERSION,
    build_forecast,
)

logger = logging.getLogger("regime_forecast")
logging.basicConfig(
    level=os.getenv("REGIME_FORECAST_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


RESEARCH_DIR = cfg.CACHE_DIR / "research"
ARTIFACT_JSON = RESEARCH_DIR / "regime_forecast_latest.json"
ARTIFACT_TEXT = cfg.LOG_DIR / "regime_forecast_latest.txt"
PRICE_DIR = cfg.CACHE_DIR / "prices"
VIX_HISTORY_PATH = RESEARCH_DIR / "regime_forecast_vix_history.json"

UNIVERSE_SNAPSHOT_PATH = cfg.CACHE_DIR / "universe" / "universe_snapshot_latest.json"

# Symbols required for the forecaster.
# SMH (semiconductors) is included for CACHE COVERAGE ONLY — semis are central to
# the tech tape, so short-detection / market-internals research needs SMH's
# parquet pre-warmed (Phase 1G.16 used XLK as a fallback proxy because SMH was
# never cached). It is intentionally NOT added to core.regime_forecaster.SECTOR_ETFS,
# so the forecaster's sector-leadership/rotation math and the Gatekeeper that
# reads it are unchanged — SMH is simply loaded and available alongside the rest.
ALL_SYMBOLS: tuple = tuple(MARKET_ETFS) + tuple(SECTOR_ETFS) + tuple(RISK_PROXIES) + ("LQD", "XLRE", "XLC", "SMH")
# (LQD / XLRE / XLC / SMH are explicitly added because not every cache slot may exist.)
ALL_SYMBOLS = tuple(sorted(set(ALL_SYMBOLS)))


# ── Cache / loader helpers ──────────────────────────────────────────────────


def _load_cached_frame(symbol: str) -> Optional[pd.DataFrame]:
    path = PRICE_DIR / f"{symbol.upper()}.parquet"
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path)
        if df is None or df.empty:
            return None
        # Normalize column names (some legacy parquets may use Title-case).
        cols_lower = {c.lower(): c for c in df.columns}
        if "close" not in df.columns and "Close" in df.columns:
            df = df.rename(columns={"Close": "close"})
        return df
    except Exception as exc:
        logger.warning("cached parquet read failed for %s: %s", symbol, exc)
        return None


def _is_offline_alpaca() -> bool:
    # Phase 3A: ALPACA_ACTIVE=False means Alpaca is research-only stub;
    # skip it so FMP becomes the primary price refresher.
    try:
        from core.research_mode import ALPACA_ACTIVE
        if not ALPACA_ACTIVE:
            return True
    except ImportError:
        pass
    return os.getenv("ALPACA_API_KEY", "").strip().lower() in {"", "offline", "stub"}


def _persist_frame_to_local_cache(symbol: str, df: pd.DataFrame) -> None:
    """
    Write a freshly fetched frame to the project's local parquet cache so that
    subsequent runs hit the cache and avoid burning provider API budget.

    The Alpaca client already writes back via Gatekeeper for symbols it
    fetches itself; this helper exists for the FMP + yfinance code paths.
    """
    try:
        PRICE_DIR.mkdir(parents=True, exist_ok=True)
        path = PRICE_DIR / f"{symbol.upper()}.parquet"
        # Defensive: write only the canonical OHLCV columns when available.
        cols = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
        out = df[cols] if cols else df
        out.to_parquet(path, compression="snappy")
    except Exception as exc:
        logger.info("local parquet write failed for %s: %s", symbol, exc)


def _ensure_alpaca_frames(missing: Sequence[str], days: int = 260) -> Dict[str, pd.DataFrame]:
    """
    Fetch missing/stale frames via Alpaca.  Returns whatever it can; quietly
    returns empty when credentials are placeholders.

    Uses ``AlpacaClient.get_daily_bars_batch`` which is cache-first per ticker
    and chunks the network request (default chunk size 200) so a 19-symbol
    universe fits in a single SIP request.  The client also persists each
    fetched frame to the Gatekeeper parquet cache automatically.
    """
    out: Dict[str, pd.DataFrame] = {}
    if not missing or _is_offline_alpaca():
        return out
    try:
        from core.alpaca_client import get_alpaca
        alp = get_alpaca()
    except Exception as exc:
        logger.info("Alpaca client init failed (continuing on FMP/yfinance): %s", exc)
        return out
    try:
        batch = alp.get_daily_bars_batch(list(missing), days=days, use_cache=True)
    except Exception as exc:
        logger.info("Alpaca batch fetch failed (%d symbols): %s", len(missing), exc)
        return out
    for sym, rows in (batch or {}).items():
        if not rows:
            continue
        try:
            df = pd.DataFrame(rows)
            df["date"] = pd.to_datetime(df["date"])
            df.set_index("date", inplace=True)
            df.sort_index(inplace=True)
            out[sym] = df
        except Exception as exc:
            logger.info("alpaca → frame conversion failed for %s: %s", sym, exc)
    return out


def _is_offline_fmp() -> bool:
    return os.getenv("FMP_API_KEY", "").strip().lower() in {"", "offline", "stub"}


def _ensure_fmp_frames(missing: Sequence[str], days: int = 260) -> Dict[str, pd.DataFrame]:
    """
    Per-ticker FMP fallback for symbols Alpaca couldn't supply.

    Each call goes through ``FMPClient.get_ticker_bars`` which:
      - reads the Gatekeeper key ``fmp:bars:{SYM}:{days}`` first (12h TTL),
      - on miss issues exactly one ``/historical-price-eod/full`` request,
      - is rate-limited by the global ``_TokenBucket(FMP_CALLS_PER_MINUTE)``.

    The result is ALSO persisted to the project's local parquet cache via
    ``_persist_frame_to_local_cache``, so the next run hits the parquet cache
    long before any FMP call is attempted.
    """
    out: Dict[str, pd.DataFrame] = {}
    if not missing or _is_offline_fmp():
        return out
    try:
        from core.fmp_client import get_fmp
        fmp = get_fmp()
    except Exception as exc:
        logger.info("FMP client init failed (continuing on yfinance): %s", exc)
        return out
    for sym in missing:
        try:
            rows = fmp.get_ticker_bars(sym, days=days)
        except Exception as exc:
            logger.info("FMP get_ticker_bars failed for %s: %s", sym, exc)
            continue
        if not rows:
            continue
        try:
            df = pd.DataFrame(rows)
            if "date" not in df.columns or df.empty:
                continue
            df["date"] = pd.to_datetime(df["date"])
            df.set_index("date", inplace=True)
            df.sort_index(inplace=True)
            out[sym] = df
            _persist_frame_to_local_cache(sym, df)
        except Exception as exc:
            logger.info("FMP → frame conversion failed for %s: %s", sym, exc)
    return out


def _ensure_yfinance_frames(missing: Sequence[str], days: int = 300) -> Dict[str, pd.DataFrame]:
    """yfinance fallback.  Quiet on failure; returns whatever it gets."""
    out: Dict[str, pd.DataFrame] = {}
    if not missing:
        return out
    try:
        import yfinance as yf
    except Exception as exc:
        logger.info("yfinance unavailable (skipping fallback): %s", exc)
        return out
    end = datetime.now(timezone.utc).replace(tzinfo=None).date() + timedelta(days=1)
    start = end - timedelta(days=int(days * 1.5))
    try:
        data = yf.download(
            tickers=" ".join(missing),
            start=start.isoformat(),
            end=end.isoformat(),
            interval="1d",
            auto_adjust=False,
            progress=False,
            group_by="ticker",
            threads=False,
        )
    except Exception as exc:
        logger.info("yfinance download failed: %s", exc)
        return out
    if data is None or len(data) == 0:
        return out

    # Multi-ticker: columns are MultiIndex (ticker, field).
    if hasattr(data, "columns") and isinstance(data.columns, pd.MultiIndex):
        for sym in missing:
            try:
                if sym not in data.columns.get_level_values(0):
                    continue
                df = data[sym].copy()
                df = df.rename(columns={c: c.lower() for c in df.columns})
                df = df.dropna(subset=["close"]) if "close" in df.columns else df
                if df.empty:
                    continue
                df.index = pd.to_datetime(df.index)
                out[sym] = df[["open", "high", "low", "close", "volume"]] if "volume" in df.columns else df
            except Exception as exc:
                logger.info("yfinance parse failed for %s: %s", sym, exc)
    else:  # single-ticker fallback
        try:
            df = data.copy()
            df = df.rename(columns={c: c.lower() for c in df.columns})
            df = df.dropna(subset=["close"]) if "close" in df.columns else df
            if not df.empty and len(missing) == 1:
                df.index = pd.to_datetime(df.index)
                out[missing[0]] = df
        except Exception as exc:
            logger.info("yfinance single-frame parse failed: %s", exc)
    return out


def _frame_age_hours(df: Optional[pd.DataFrame]) -> Optional[float]:
    if df is None or df.empty:
        return None
    try:
        last = df.index[-1]
        if not isinstance(last, (pd.Timestamp, datetime)):
            last = pd.to_datetime(last)
        last = last.replace(tzinfo=None) if hasattr(last, "tzinfo") and last.tzinfo else last
        return max(0.0, (datetime.now(timezone.utc).replace(tzinfo=None) - last).total_seconds() / 3600.0)
    except Exception:
        return None


def _last_completed_trading_day(now_utc: Optional[datetime] = None) -> "pd.Timestamp":
    """
    Return the date (UTC, midnight) of the most recently *completed* US-equities
    trading session, ignoring half-days.  Used to decide whether a cached frame
    is genuinely stale or just untouched because no new session has closed yet
    (e.g., a Sunday run reading Friday's bar).

    Heuristic — weekends roll back to Friday; pre-market on a weekday rolls
    back to the previous trading day.  Half-day handling and exchange-holiday
    awareness are intentionally out of scope for V1; both only cause an extra
    Gatekeeper-cached provider call, never duplicate provider charges.
    """
    now = now_utc or datetime.now(timezone.utc).replace(tzinfo=None)
    # US equities close at 16:00 ET = 20:00 UTC (standard) or 21:00 UTC (DST).
    # Use a conservative 22:00 UTC cutoff so a same-day post-close cache is
    # considered "current".
    candidate = now.date()
    if now.hour < 22:
        candidate = candidate - timedelta(days=1)
    # Walk back over weekends.
    while True:
        wd = candidate.weekday()  # Mon=0 .. Sun=6
        if wd <= 4:
            break
        candidate = candidate - timedelta(days=1)
    return pd.Timestamp(candidate)


def _frame_is_fresh(df: Optional[pd.DataFrame], stale_threshold_h: float) -> bool:
    """
    Trading-day-aware freshness check.  A parquet is fresh if either:
      - its last bar's date is >= the most recently completed trading day
        (so a Sunday run on Friday's bar does not look stale), OR
      - its last bar is younger than stale_threshold_h wall-clock hours.
    """
    if df is None or df.empty:
        return False
    try:
        last = df.index[-1]
        if not isinstance(last, (pd.Timestamp, datetime)):
            last = pd.to_datetime(last)
        last_ts = pd.Timestamp(last).normalize()
        if last_ts >= _last_completed_trading_day():
            return True
    except Exception:
        pass
    age = _frame_age_hours(df)
    return age is not None and age <= stale_threshold_h


def _load_all_frames(args: argparse.Namespace) -> Dict[str, pd.DataFrame]:
    """Cache-first loader; optionally fans out to Alpaca then yfinance."""
    frames: Dict[str, pd.DataFrame] = {}
    missing: List[str] = []
    stale_threshold_h = float(args.stale_hours)

    for sym in ALL_SYMBOLS:
        df = _load_cached_frame(sym)
        if df is None:
            missing.append(sym)
            continue
        if _frame_is_fresh(df, stale_threshold_h):
            frames[sym] = df
        else:
            # Frame is stale; keep it as a fallback but try to refresh.
            frames[sym] = df
            missing.append(sym)

    if missing and not args.offline:
        logger.info("cache miss/stale for %d symbols: %s", len(missing), ",".join(sorted(missing)))
        # Provider order: Alpaca → FMP → yfinance.  Each tier writes back to
        # the local parquet cache so the same symbol does not burn provider
        # budget on the next run (until the parquet ages past --stale-hours).

        # 1) Alpaca SIP batch (cache-first per ticker, single chunked request).
        from_alpaca = _ensure_alpaca_frames(missing)
        for sym, df in from_alpaca.items():
            frames[sym] = df

        # 2) FMP per-ticker (12h Gatekeeper cache + token-bucket rate limiter).
        still_missing = [s for s in missing if s not in from_alpaca]
        if still_missing and not args.no_fmp:
            from_fmp = _ensure_fmp_frames(still_missing)
            for sym, df in from_fmp.items():
                frames[sym] = df
            still_missing = [s for s in still_missing if s not in from_fmp]

        # 3) yfinance final fallback (single batched download).
        if still_missing:
            from_yf = _ensure_yfinance_frames(still_missing)
            for sym, df in from_yf.items():
                frames[sym] = df
                _persist_frame_to_local_cache(sym, df)

    return frames


# ── VIX loader (FMP first, then yfinance) ───────────────────────────────────


def _load_vix(args: argparse.Namespace) -> tuple:
    """Return (latest_vix, history_list_oldest_first)."""
    history = _load_vix_history()
    latest = history[-1] if history else None

    if args.offline:
        return latest, history

    # Try FMP for latest level.
    if not _is_offline_fmp():
        try:
            from core.fmp_client import get_fmp
            v = get_fmp().get_vix()
            if v is not None:
                latest = float(v)
                history = (history or []) + [latest]
                history = history[-60:]
                _save_vix_history(history)
        except Exception as exc:
            logger.info("FMP get_vix failed: %s", exc)

    # If we still don't have a useful history, try yfinance ^VIX bars.
    if (not history) or len(history) < 6:
        try:
            import yfinance as yf
            df = yf.download(
                tickers="^VIX",
                period="3mo",
                interval="1d",
                auto_adjust=False,
                progress=False,
                threads=False,
            )
            series = _flatten_close(df)
            if series:
                history = series[-60:]
                latest = history[-1]
                _save_vix_history(history)
        except Exception as exc:
            logger.info("yfinance VIX fallback failed: %s", exc)

    return latest, history


def _flatten_close(df: Optional[pd.DataFrame]) -> List[float]:
    """
    Extract a 1-D close series from a yfinance result, handling both Series
    and MultiIndex-column DataFrames.  Newer yfinance (>=0.2.x) returns a
    MultiIndex even for single-ticker downloads.
    """
    if df is None or len(df) == 0:
        return []
    try:
        # MultiIndex columns: pick the first level whose label matches Close.
        if hasattr(df, "columns") and isinstance(df.columns, pd.MultiIndex):
            candidates = [c for c in df.columns if str(c[0]).lower() == "close" or str(c[-1]).lower() == "close"]
            if not candidates:
                return []
            sub = df[candidates[0]]
        elif "Close" in df.columns:
            sub = df["Close"]
        elif "close" in df.columns:
            sub = df["close"]
        else:
            return []
        # `sub` may still be a DataFrame if multiple matching columns exist.
        if hasattr(sub, "columns"):
            sub = sub.iloc[:, 0]
        return [float(x) for x in sub.dropna().tolist()]
    except Exception:
        return []


def _load_vix_history() -> List[float]:
    if not VIX_HISTORY_PATH.exists():
        return []
    try:
        raw = json.loads(VIX_HISTORY_PATH.read_text())
        if isinstance(raw, dict):
            raw = raw.get("history") or []
        return [float(x) for x in raw if x is not None]
    except Exception:
        return []


def _save_vix_history(history: List[float]) -> None:
    try:
        RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
        VIX_HISTORY_PATH.write_text(json.dumps({
            "saved_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            "history": [float(x) for x in history if x is not None],
        }, indent=2))
    except Exception as exc:
        logger.info("VIX history save failed: %s", exc)


# ── Universe-snapshot breadth (optional) ────────────────────────────────────


def _load_universe_snapshot() -> Optional[Dict[str, Any]]:
    if not UNIVERSE_SNAPSHOT_PATH.exists():
        return None
    try:
        return json.loads(UNIVERSE_SNAPSHOT_PATH.read_text())
    except Exception:
        return None


# ── Rendering ───────────────────────────────────────────────────────────────


def _render_text(artifact: Dict[str, Any]) -> str:
    h = artifact.get("headline") or {}
    lines: List[str] = []
    lines.append("=" * 72)
    lines.append("MARKET & SECTOR REGIME FORECASTER V1 — Phase 1")
    lines.append("=" * 72)
    lines.append(f"built_at      {artifact.get('built_at','—')}")
    lines.append(f"mode          {artifact.get('mode','daily')}")
    anchor = artifact.get("anchor_date") or "—"
    spy_last = artifact.get("spy_last_bar_date") or "—"
    age_d = artifact.get("anchor_age_days")
    fresh = artifact.get("data_freshness_status") or "—"
    state = artifact.get("forecast_state") or "—"
    expected = artifact.get("expected_anchor_date") or "—"
    basis = artifact.get("expected_anchor_basis") or "—"
    lines.append(f"anchor_date   {anchor}  (spy_last={spy_last}, age={age_d}d, status={fresh})")
    lines.append(f"expected      {expected}  ({basis})  forecast_state={state}")
    warn = artifact.get("anchor_warning")
    if warn:
        lines.append(f"⚠ anchor      {warn}")
    lines.append("")
    lines.append("HEADLINE")
    lines.append("-" * 72)
    lines.append(f"current regime    : {h.get('current_regime','—')}")
    lines.append(f"5d bias           : {h.get('bias_5d','—')}")
    lines.append(f"10d bias          : {h.get('bias_10d','—')}")
    lines.append(f"confidence        : {h.get('confidence','—')}")
    lines.append(f"main invalidation : {h.get('main_invalidation','—')}")
    lines.append("")
    lines.append("REGIME PROBABILITIES")
    lines.append("-" * 72)
    for r in artifact.get("regime_probabilities") or []:
        bar_len = int(round(float(r.get("probability", 0)) * 40))
        bar = "█" * bar_len + "·" * (40 - bar_len)
        lines.append(f"  {r.get('regime',''):35s} {bar} {float(r.get('probability',0))*100:4.0f}%")
    lines.append("")
    lines.append("SECTOR LEADERSHIP")
    lines.append("-" * 72)
    lines.append(f"  {'sector':6s} {'name':24s} {'state':11s} {'rs5d%':>7s} {'rs10d%':>7s} {'rs20d%':>7s} ma50")
    for r in (artifact.get("sector_rotation") or {}).get("rows") or []:
        if not r.get("available"):
            lines.append(f"  {r.get('sector',''):6s} {r.get('name',''):24s} {'unavailable':11s}")
            continue
        rs5 = r.get("rs_5d_pct")
        rs10 = r.get("rs_10d_pct")
        rs20 = r.get("rs_20d_pct")
        ma50 = "yes" if r.get("above_ma50") else "no"
        lines.append(
            f"  {r.get('sector',''):6s} {r.get('name',''):24s} {r.get('state','—'):11s} "
            f"{(f'{rs5:+.2f}' if rs5 is not None else '   —'):>7s} "
            f"{(f'{rs10:+.2f}' if rs10 is not None else '   —'):>7s} "
            f"{(f'{rs20:+.2f}' if rs20 is not None else '   —'):>7s} {ma50}"
        )
    lines.append("")
    lines.append("STRATEGY FAVORABILITY (research only)")
    lines.append("-" * 72)
    for k, v in (artifact.get("strategy_favorability") or {}).items():
        lines.append(f"  {k:18s} {v.get('stance',''):9s}  {v.get('reason','')}")
        lines.append(f"    invalidation: {v.get('invalidation','—')}")
    lines.append("")
    lines.append("FACTOR CONTRIBUTIONS")
    lines.append("-" * 72)
    bf = (artifact.get("factor_contributions") or {}).get("bullish") or []
    rf = (artifact.get("factor_contributions") or {}).get("bearish") or []
    lines.append("  bullish:")
    if bf:
        for f in bf:
            lines.append(f"    + {f}")
    else:
        lines.append("    (none)")
    lines.append("  bearish:")
    if rf:
        for f in rf:
            lines.append(f"    - {f}")
    else:
        lines.append("    (none)")
    lines.append("")
    dq = artifact.get("data_quality") or {}
    lines.append("DATA QUALITY")
    lines.append("-" * 72)
    lines.append(f"  spy bars                : {dq.get('spy_bars', 0)}")
    lines.append(f"  sector frames available : {dq.get('sector_frames_available', 0)} of {len(SECTOR_ETFS)}")
    lines.append(f"  vix history points      : {dq.get('vix_history_points', 0)}")
    missing = dq.get("missing_layers") or []
    lines.append(f"  missing layers          : {(', '.join(missing)) if missing else 'none'}")
    lines.append("")
    lines.append("VOLATILITY / CREDIT SUMMARY")
    lines.append("-" * 72)
    vol = artifact.get("volatility") or {}
    credit = artifact.get("credit_rates") or {}
    vix = vol.get("vix")
    rv = vol.get("spy_realized_vol_20d_ann")
    appetite = (credit.get("risk_appetite") or {}).get("label", "—")
    appetite_signals = (credit.get("risk_appetite") or {}).get("signals") or []
    def _fmt_pct(value: Any, places: int = 2) -> str:
        if value is None:
            return "—"
        try:
            return f"{float(value):+.{places}f}%"
        except Exception:
            return "—"

    def _fmt_pts(value: Any, places: int = 2) -> str:
        if value is None:
            return "—"
        try:
            return f"{float(value):+.{places}f} pts"
        except Exception:
            return "—"

    vix_avg = vol.get("vix_avg_20")
    lines.append(f"  VIX               : {(f'{vix:.2f}' if vix is not None else '—')}")
    lines.append(f"  VIX change 5d     : {_fmt_pts(vol.get('vix_change_5d'))}")
    lines.append(f"  VIX change 10d    : {_fmt_pts(vol.get('vix_change_10d'))}")
    lines.append(f"  VIX 20d avg       : {(f'{vix_avg:.2f}' if vix_avg is not None else '—')}")
    lines.append(f"  SPY realized vol  : {(f'{rv:.1f}%' if rv is not None else '—')}")
    lines.append(f"  HYG/LQD 5d change : {_fmt_pct(credit.get('hyg_lqd_ratio_5d_pct'))}")
    lines.append(f"  IWM/SPY 5d change : {_fmt_pct(credit.get('iwm_spy_ratio_5d_pct'))}")
    lines.append(f"  risk appetite     : {appetite}")
    if appetite_signals:
        lines.append(f"    signals         : {', '.join(appetite_signals)}")
    lines.append("")
    lines.append("GUARDRAILS")
    lines.append("-" * 72)
    for g in artifact.get("guardrails") or []:
        lines.append(f"  - {g}")
    lines.append("")
    lines.append("INVALIDATION CONDITIONS (per regime)")
    lines.append("-" * 72)
    for r in artifact.get("regime_probabilities") or []:
        rg = r.get("regime")
        inval = (artifact.get("regime_invalidations") or {}).get(rg, "—")
        lines.append(f"  {rg}: {inval}")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ── Artifact assembly ───────────────────────────────────────────────────────


def _frames_summary(frames: Dict[str, pd.DataFrame]) -> Dict[str, Dict[str, Any]]:
    summary: Dict[str, Dict[str, Any]] = {}
    for sym in ALL_SYMBOLS:
        df = frames.get(sym)
        if df is None or df.empty:
            summary[sym] = {"available": False}
            continue
        try:
            last_dt = df.index[-1]
            if hasattr(last_dt, "isoformat"):
                last_str = last_dt.isoformat()
            else:
                last_str = str(last_dt)
        except Exception:
            last_str = "—"
        summary[sym] = {
            "available": True,
            "bars": int(len(df)),
            "last_bar": last_str,
            "last_close": float(df["close"].iloc[-1]) if "close" in df.columns and not df["close"].empty else None,
        }
    return summary


def _build_invalidation_index(forecast: Dict[str, Any]) -> Dict[str, str]:
    """
    Re-derive a per-regime invalidation map by reusing the forecaster's
    invalidation builder against the same observed market context.  This is a
    convenience for the text report; the canonical invalidation lives on
    forecast['headline']['main_invalidation'].
    """
    from core.regime_forecaster import _build_invalidation as _bi
    market = forecast.get("market_trend") or {}
    vol = forecast.get("volatility") or {}
    sectors = forecast.get("sector_rotation") or {}
    out: Dict[str, str] = {}
    for r in forecast.get("regime_probabilities") or []:
        rg = r.get("regime")
        if not rg:
            continue
        try:
            out[rg] = _bi(rg, market, vol, sectors)
        except Exception:
            out[rg] = "—"
    return out


def _expected_anchor_date(now_et: Optional[datetime] = None) -> Tuple[date, str]:
    """Phase 1G.1: return (expected_anchor_date, expectation_basis).

    The expected anchor is the most recent *completed* NYSE trading day
    as of ``now_et``. Doctrine:

      • Trading day, after regular close (16:00 ET, 13:00 ET on
        early-close days): today's bar is the expected anchor.
      • Trading day, before regular close: today's bar is not yet
        complete, so the prior trading day is the expected anchor.
      • Weekend / NYSE holiday: prior trading day.

    ``expectation_basis`` is a short token used by the freshness
    classifier and the dashboard:
        ``post_close_today`` | ``intraday_prior_close`` | ``non_trading_day_prior_close``
    """
    # Lazy imports keep regime_forecast importable without the session
    # module (e.g. in research-only environments).
    from core.session import (
        _is_trading_day, _regular_close_hour,
    )
    from zoneinfo import ZoneInfo as _ZoneInfo

    et_zone = _ZoneInfo("America/New_York")
    if now_et is None:
        now_et = datetime.now(et_zone)
    elif now_et.tzinfo is None:
        now_et = now_et.replace(tzinfo=et_zone)
    else:
        now_et = now_et.astimezone(et_zone)

    today_et = now_et.date()

    def _prev_trading(d: date) -> date:
        candidate = d - timedelta(days=1)
        # Cap at 14d back — _is_trading_day reads from a static
        # holiday set; an unbroken 2-week NYSE closure has never
        # happened, so this is a defensive ceiling, not policy.
        for _ in range(14):
            if _is_trading_day(candidate):
                return candidate
            candidate = candidate - timedelta(days=1)
        return candidate  # fall through — best effort

    if _is_trading_day(today_et):
        close_hr = _regular_close_hour(today_et)
        if now_et.hour > close_hr or (now_et.hour == close_hr and now_et.minute >= 0):
            # at/after the close — today is the expected anchor
            return today_et, "post_close_today"
        return _prev_trading(today_et), "intraday_prior_close"
    return _prev_trading(today_et), "non_trading_day_prior_close"


def _classify_freshness(
    anchor: Optional[date],
    expected: date,
    basis: str,
) -> Tuple[str, str, Optional[str]]:
    """Phase 1G.1: trading-day-aware freshness classification.

    Returns ``(data_freshness_status, forecast_state, anchor_warning)``.

    Statuses:
      ``fresh``  — anchor matches the expected trading day. On a
                   premarket / intraday cycle the expected anchor is
                   the prior completed close, so ``anchor == expected``
                   is the normal "everything is current" state.
      ``behind`` — anchor is one trading day off from expected and we
                   are *after today's close* (``basis=post_close_today``).
                   This is the narrow "today's bar hasn't been ingested
                   into the cache yet" window — the dashboard renders
                   this as ``FRAGILE_STALE`` so the operator knows the
                   snapshot is a single-bar lag rather than an outage.
      ``stale``  — anchor is older than the expected trading day by
                   either more than one trading day, or by exactly one
                   trading day while we are intraday — meaning we
                   expected to have the prior close and we don't. The
                   forecast inputs are NOT current; do not treat the
                   regime as load-bearing.
      ``missing``— no frames available.

    Forecast states:
      ``FRESH`` | ``FRAGILE_STALE`` | ``STALE`` | ``MISSING``
    """
    if anchor is None:
        return "missing", "MISSING", "no market frames available — anchor unknown"
    if anchor >= expected:
        return "fresh", "FRESH", None
    # Walk back from ``expected`` to find the trading day immediately before.
    from core.session import _is_trading_day
    candidate = expected - timedelta(days=1)
    for _ in range(14):
        if _is_trading_day(candidate):
            break
        candidate -= timedelta(days=1)
    if anchor == candidate and basis == "post_close_today":
        # Today's close already exists in the world, but our cache
        # still has the previous trading day. Common when the
        # post-close cache prewarm has not yet run. Surface as
        # FRAGILE_STALE so operators know it is a small lag, not a
        # multi-day gap.
        return (
            "behind",
            "FRAGILE_STALE",
            (f"market data anchored to {anchor.isoformat()} — today's close "
             f"({expected.isoformat()}) not yet ingested into cache"),
        )
    age = (expected - anchor).days
    return (
        "stale",
        "STALE",
        (f"market data {age}d behind expected anchor {expected.isoformat()} — "
         f"forecast inputs are not current; refresh cache before relying on the call"),
    )


def _anchor_metadata(
    frames: Dict[str, "pd.DataFrame"],
    now_et: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Phase 6: derive an explicit anchor date for forward tracking.

    The anchor is the most recent calendar trading day reflected in the
    market frames.  We prefer SPY; if unavailable, the max of QQQ/IWM/DIA.
    The result is the canonical date forward returns are measured *from*,
    independent of when the script happened to run.

    Phase 1G.1 (2026-05-18): freshness is now compared against the
    expected trading-day anchor (``_expected_anchor_date``), not raw
    calendar age. Previously the artifact would read "behind" all
    weekend and on Monday morning — useless noise. Now a Monday
    premarket forecast that anchors on Friday's close is
    ``FRAGILE_STALE`` (informational, expected), and a forecast that
    anchors on the prior week's close is ``STALE`` (operator action).
    """
    def _last_date(sym: str) -> Optional[date]:
        df = frames.get(sym)
        if df is None or len(df) == 0:
            return None
        try:
            ts = df.index[-1]
            d = ts.date() if hasattr(ts, "date") else date.fromisoformat(str(ts)[:10])
            return d
        except Exception:
            return None

    spy_last = _last_date("SPY")
    candidates = {
        "SPY": spy_last,
        "QQQ": _last_date("QQQ"),
        "IWM": _last_date("IWM"),
        "DIA": _last_date("DIA"),
    }
    market_last = max((d for d in candidates.values() if d is not None), default=None)
    anchor = spy_last or market_last  # prefer SPY, fall back to broadest

    expected, basis = _expected_anchor_date(now_et)
    freshness, forecast_state, warning = _classify_freshness(anchor, expected, basis)

    # Calendar-day age stays in the artifact for back-compat with any
    # consumer that previously used it. The freshness verdict above is
    # the new source of truth.
    age_days: Optional[int] = (
        (date.today() - anchor).days if anchor is not None else None
    )

    return {
        "anchor_date": anchor.isoformat() if anchor else None,
        "spy_last_bar_date": spy_last.isoformat() if spy_last else None,
        "market_data_last_bar_date": market_last.isoformat() if market_last else None,
        "anchor_age_days": age_days,
        "expected_anchor_date": expected.isoformat(),
        "expected_anchor_basis": basis,
        "data_freshness_status": freshness,
        "forecast_state": forecast_state,
        "anchor_warning": warning,
        "anchor_source": "spy_frame" if spy_last else ("market_frame" if market_last else "unknown"),
        "anchor_per_symbol": {
            sym: (d.isoformat() if d is not None else None)
            for sym, d in candidates.items()
        },
    }


def build_artifact(args: argparse.Namespace) -> Dict[str, Any]:
    frames = _load_all_frames(args)
    vix, vix_history = _load_vix(args)
    snapshot = _load_universe_snapshot() if not args.no_snapshot else None

    forecast = build_forecast(
        frames=frames,
        vix=vix,
        vix_history=vix_history,
        universe_snapshot=snapshot,
    )

    anchor_meta = _anchor_metadata(frames)
    artifact = dict(forecast)
    artifact.update({
        "version": VERSION,
        "phase": 1,
        "mode": args.mode,
        "built_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        "anchor_date":               anchor_meta["anchor_date"],
        "spy_last_bar_date":         anchor_meta["spy_last_bar_date"],
        "market_data_last_bar_date": anchor_meta["market_data_last_bar_date"],
        "anchor_age_days":           anchor_meta["anchor_age_days"],
        "expected_anchor_date":      anchor_meta["expected_anchor_date"],
        "expected_anchor_basis":     anchor_meta["expected_anchor_basis"],
        "data_freshness_status":     anchor_meta["data_freshness_status"],
        "forecast_state":            anchor_meta["forecast_state"],
        "anchor_warning":            anchor_meta["anchor_warning"],
        "anchor_source":             anchor_meta["anchor_source"],
        "anchor_per_symbol":         anchor_meta["anchor_per_symbol"],
        "guardrails": [
            "research-only",
            "not trade approval",
            "not paper evidence",
            "no execution",
            "no governance change",
            "no sleeve mutation",
            "no Alpha Discovery / Market Posture / Daily Entry Validator change",
            "no news/social as core forecast input",
            "no 13F as timing input",
            "cache-first",
            "graceful degradation on missing data",
        ],
        "data_sources": {
            "prices_order": [
                "local parquet cache (cache/prices)",
                "Alpaca SIP daily bars (batched, cache-first)" if not args.offline else "skipped (offline)",
                ("FMP /historical-price-eod/full (12h Gatekeeper cache, "
                 "token-bucket rate-limited)") if (not args.offline and not args.no_fmp) else "skipped",
                "yfinance daily bars (batched fallback)" if not args.offline else "skipped (offline)",
            ],
            "vix_order": [
                "cached rolling history (cache/research/regime_forecast_vix_history.json)",
                "FMP get_vix (5-min Gatekeeper cache)" if not args.offline else "skipped (offline)",
                "yfinance ^VIX (history backfill)" if not args.offline else "skipped (offline)",
            ],
            "breadth_optional": "dashboard universe snapshot (read-only)",
            "rate_limit_notes": (
                "Alpaca: SIP batch endpoint, 1 request per chunk (chunk_size=200, "
                "so the V1 universe of ~19 symbols is a single request). "
                "FMP: per-ticker /historical-price-eod/full, but cached 12h via "
                "Gatekeeper and rate-limited by FMP_CALLS_PER_MINUTE token bucket. "
                "yfinance: single batched download for any remaining symbols. "
                "Provider results are written back to the parquet cache so the next "
                "run hits the cache."
            ),
        },
        "frames_summary": _frames_summary(frames),
        "validation_status": "Phase 1: not validated yet (Phase 2)",
        "validation_plan": [
            "5d / 10d SPY forward returns by regime",
            "realized volatility expansion vs predicted Vol-Expansion regime",
            "leader-vs-laggard sector spread realization",
            "regime hit rate and Brier-score calibration (offline only, no lookahead)",
        ],
    })
    artifact["regime_invalidations"] = _build_invalidation_index(artifact)
    return artifact


def _save_artifacts(artifact: Dict[str, Any]) -> Dict[str, str]:
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    cfg.LOG_DIR.mkdir(parents=True, exist_ok=True)
    artifact["artifacts"] = {
        "json": str(ARTIFACT_JSON),
        "text": str(ARTIFACT_TEXT),
    }
    ARTIFACT_JSON.write_text(json.dumps(artifact, indent=2, default=str), encoding="utf-8")
    ARTIFACT_TEXT.write_text(_render_text(artifact), encoding="utf-8")

    # Phase 5 forward-tracking ledger.  Append-only and idempotent — does NOT
    # modify the forecast artifact, only records what the forecast said for
    # later outcome resolution.  Failures here must not break the forecast.
    try:
        from core.forecast_forward_tracker import append_forecast_snapshot
        append_forecast_snapshot(artifact)
    except Exception as exc:  # pragma: no cover
        logging.getLogger(__name__).warning(
            "forecast forward-tracking append failed: %s", exc
        )

    return artifact["artifacts"]


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Market & Sector Regime Forecaster V1 (Phase 1) + single-stock lens (Phase 3)")
    parser.add_argument("--mode", choices=["daily"], default="daily",
                        help="run cadence label (only 'daily' in V1)")
    parser.add_argument("--ticker", type=str, default=None,
                        help="run the single-stock research lens for this ticker instead of the daily forecast")
    parser.add_argument("--horizon", type=int, default=20,
                        help="lens horizon hint in trading days (default 20; lens always reports 5/10/20)")
    parser.add_argument("--refresh", action="store_true",
                        help="lens: ignore the cached regime artifact and recompute the market forecast")
    parser.add_argument("--offline", action="store_true",
                        help="skip all provider calls; use local parquet cache + cached VIX history only")
    parser.add_argument("--cache-only", action="store_true",
                        help="lens: alias for --offline (no provider calls for ticker bars or sector profile)")
    parser.add_argument("--no-fmp", action="store_true",
                        help="skip the FMP price-bars tier (use Alpaca then yfinance only)")
    parser.add_argument("--no-snapshot", action="store_true",
                        help="skip the optional universe-snapshot breadth proxy")
    parser.add_argument("--stale-hours", type=float, default=24.0,
                        help="frame age above which we attempt a refresh fetch (default 24h)")
    parser.add_argument("--print-json", action="store_true",
                        help="print the artifact JSON to stdout after writing")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.cache_only:
        args.offline = True
    if args.ticker:
        from research import stock_lens_runner
        return stock_lens_runner.run(args)
    artifact = build_artifact(args)
    paths = _save_artifacts(artifact)
    headline = artifact.get("headline") or {}
    print(
        f"regime_forecast: {headline.get('current_regime','—')} "
        f"(conf {headline.get('confidence','—')}, 5d {headline.get('bias_5d','—')}) "
        f"→ {paths.get('json','—')}"
    )
    if args.print_json:
        print(json.dumps(artifact, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

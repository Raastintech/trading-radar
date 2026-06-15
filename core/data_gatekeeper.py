"""
core/data_gatekeeper.py — Local cache layer sitting in front of all FMP calls.

Architecture:
  • SQLite  → metadata table (last_fetch timestamps, call budget tracking)
  • Parquet → historical OHLCV price files per ticker (1 file per ticker)

Gatekeeper.get(endpoint, ticker, ttl_seconds) returns cached data or None.
Gatekeeper.put(endpoint, ticker, data) writes to the appropriate store.

All FMP callers go through this — zero API calls for in-TTL data.

Budget model (2026-04-20):
  Plan limits: 750 RPM, 50,000 calls/month.
  Budget is tracked monthly (YYYY-MM key) against FMP_MONTHLY_BUDGET.
  The old daily table (fmp_budget) is preserved for audit history but is no
  longer the enforcement gate — fmp_budget_monthly is the live gate.
"""
from __future__ import annotations
import json
import logging
import sqlite3
import time
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

import core.config as cfg

logger = logging.getLogger(__name__)

# TTLs (seconds)
TTL_OHLCV        = 12 * 3600       # 12 h  — intraday stale-ok for strategy scans

# Phase 1G.17 — depth-preserving price cache.
# Overlap-validation tolerance: if cached history and a fresh fetch disagree
# on the SAME dates by more than this relative amount, the cached history is
# on a stale adjustment basis (split/dividend re-adjustment since it was
# written) and must be discarded rather than merged.
PRICE_OVERLAP_TOL = 0.005


def merge_price_frames(
    old: Optional[pd.DataFrame],
    new: pd.DataFrame,
    tol: float = PRICE_OVERLAP_TOL,
) -> pd.DataFrame:
    """Merge an older cached OHLCV frame into a fresh fetch, preserving depth.

    The fresh frame wins on overlapping dates, so the most-recent bars (and
    their adjustment basis) are always the newly fetched ones — no look-ahead
    and no staleness can enter through the merge. If overlapping closes
    disagree beyond ``tol`` the old history is on a different adjustment
    basis and the fresh frame is returned unmerged (the pre-1G.17 behavior).
    Any error degrades to returning ``new`` unchanged.
    """
    if old is None or old.empty:
        return new
    try:
        old = old.copy()
        old.index = pd.to_datetime(old.index)
        if "close" not in old.columns or "close" not in new.columns:
            return new
        overlap = old.index.intersection(new.index)
        if len(overlap):
            o = old.loc[overlap, "close"].astype(float)
            n = new.loc[overlap, "close"].astype(float)
            denom = n.where(n != 0)
            rel = ((o - n).abs() / denom).max()
            if pd.notna(rel) and float(rel) > tol:
                return new            # adjustment drift — trust fresh fetch only
        merged = pd.concat([old, new])
        merged = merged[~merged.index.duplicated(keep="last")].sort_index()
        merged.index.name = new.index.name or old.index.name or "date"
        return merged
    except Exception:
        return new
# Phase 2B.4: bumped 6h → 13h so a nightly fire at 20:30 ET still has a fresh
# cache entry when the next premarket fires at 08:00 ET (gap is ~11.5 h).
# Earnings calendar dates rarely move intraday; the dashboard surfaces an
# EARNINGS DATA STALE marker when the cache exceeds doctrine thresholds.
TTL_EARNINGS_CAL = 13 * 3600       # 13 h — covers nightly→premarket gap
TTL_ECONOMIC_CAL = 4  * 3600       # 4 h
TTL_TREASURY     = 4  * 3600       # 4 h
TTL_NEWS         = 1  * 3600       # 1 h
TTL_FUNDAMENTALS = 24 * 3600       # 24 h
TTL_VIX          = 5  * 60         # 5 min — near-real-time needed for regime gate
TTL_QUOTE        = 20              # 20 s  — intraday quote cache (batch quotes)
TTL_SPY_BARS     = 4  * 3600       # 4 h   — regime-gate SPY (200d MA, RS); same-day freshness


class Gatekeeper:
    """Thread-safe local cache with SQLite metadata + Parquet price files."""

    _DDL = """
    CREATE TABLE IF NOT EXISTS cache_meta (
        key        TEXT PRIMARY KEY,
        fetched_at REAL NOT NULL,
        payload    TEXT
    );
    CREATE TABLE IF NOT EXISTS fmp_budget (
        day        TEXT PRIMARY KEY,
        calls_used INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS fmp_budget_monthly (
        month      TEXT PRIMARY KEY,
        calls_used INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS fmp_endpoint_log (
        endpoint      TEXT NOT NULL,
        ts            REAL NOT NULL,
        saved         INTEGER NOT NULL DEFAULT 0,
        resp_bytes    INTEGER NOT NULL DEFAULT 0
    );
    """

    def __init__(self):
        self._db_path = cfg.DB_PATH
        self._cache_dir = cfg.CACHE_DIR
        self._price_dir = self._cache_dir / "prices"
        self._fund_dir  = self._cache_dir / "fundamentals"
        from core.db import connect as _hardened_connect
        self._conn = _hardened_connect(str(self._db_path), check_same_thread=False)
        self._conn.executescript(self._DDL)
        self._conn.commit()
        self._migrate()

    def _migrate(self) -> None:
        """Apply schema migrations that can't be handled by CREATE TABLE IF NOT EXISTS."""
        cols = {row[1] for row in self._conn.execute(
            "PRAGMA table_info(fmp_endpoint_log)"
        ).fetchall()}
        if "resp_bytes" not in cols:
            self._conn.execute(
                "ALTER TABLE fmp_endpoint_log ADD COLUMN resp_bytes INTEGER NOT NULL DEFAULT 0"
            )
            self._conn.commit()
            logger.debug("Migrated fmp_endpoint_log: added resp_bytes column")

    # ── Usage tracking (telemetry only — nothing here blocks a call) ─────────

    def budget_used_today(self) -> int:
        today = date.today().isoformat()
        row = self._conn.execute(
            "SELECT calls_used FROM fmp_budget WHERE day=?", (today,)
        ).fetchone()
        return row[0] if row else 0

    def budget_used_month(self) -> int:
        month = date.today().strftime("%Y-%m")
        row = self._conn.execute(
            "SELECT calls_used FROM fmp_budget_monthly WHERE month=?", (month,)
        ).fetchone()
        return row[0] if row else 0

    def budget_remaining(self) -> int:
        """
        Returns 0 — monthly cap is not confirmed for this plan.
        Kept for API compatibility; callers should use budget_used_month() instead.
        """
        return 0

    def budget_consume(self, n: int = 1) -> bool:
        """
        TELEMETRY ONLY — always returns True (never blocks).

        The only hard enforcement is the 750 RPM token bucket in FMPClient._get().
        No monthly or daily call cap has been confirmed from the plan page
        (plan shows 750 RPM + 50 GB bandwidth, not a call count ceiling).

        Increments both daily and monthly counters for visibility.
        """
        month = date.today().strftime("%Y-%m")
        today = date.today().isoformat()
        try:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO fmp_budget_monthly(month, calls_used) VALUES(?,?) "
                    "ON CONFLICT(month) DO UPDATE SET calls_used=calls_used+?",
                    (month, n, n),
                )
                self._conn.execute(
                    "INSERT INTO fmp_budget(day, calls_used) VALUES(?,?) "
                    "ON CONFLICT(day) DO UPDATE SET calls_used=calls_used+?",
                    (today, n, n),
                )
        except Exception as exc:
            logger.debug("budget_consume tracking error (non-blocking): %s", exc)
        return True   # always allow — rate bucket is the real gate

    def log_endpoint(self, endpoint: str, saved: int = 0, resp_bytes: int = 0) -> None:
        """
        Record one FMP call (saved=0) or one cache hit (saved=1) per endpoint.
        resp_bytes: Content-Length of the HTTP response if known (for bandwidth tracking).
        Fire-and-forget — never blocks callers.
        """
        try:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO fmp_endpoint_log(endpoint, ts, saved, resp_bytes) "
                    "VALUES(?,?,?,?)",
                    (endpoint, time.time(), saved, resp_bytes),
                )
        except Exception:
            pass

    def endpoint_summary(self, since_hours: float = 24.0) -> Dict[str, Dict[str, int]]:
        """
        Returns {endpoint: {calls, saved, bytes_mb}} for the last `since_hours`.
        bytes_mb is estimated bandwidth (sum of resp_bytes / 1048576).
        Used by status logging and debug views.
        """
        cutoff = time.time() - since_hours * 3600
        rows = self._conn.execute(
            "SELECT endpoint, saved, COUNT(*), SUM(resp_bytes) "
            "FROM fmp_endpoint_log WHERE ts >= ? GROUP BY endpoint, saved",
            (cutoff,),
        ).fetchall()
        result: Dict[str, Dict[str, int]] = defaultdict(
            lambda: {"calls": 0, "saved": 0, "bytes": 0}
        )
        for endpoint, saved, count, total_bytes in rows:
            key = "saved" if saved else "calls"
            result[endpoint][key] += count
            result[endpoint]["bytes"] += (total_bytes or 0)
        # Convert bytes to MB for display
        for ep in result:
            result[ep]["bytes_mb"] = round(result[ep].pop("bytes") / 1_048_576, 2)
        return dict(result)

    def bandwidth_used_month_mb(self) -> float:
        """Total estimated FMP response bandwidth this calendar month (MB)."""
        month_start = date.today().replace(day=1)
        cutoff = month_start.timetuple()
        import calendar as _cal
        cutoff_ts = time.mktime(cutoff)
        row = self._conn.execute(
            "SELECT SUM(resp_bytes) FROM fmp_endpoint_log WHERE ts >= ? AND saved=0",
            (cutoff_ts,),
        ).fetchone()
        return round((row[0] or 0) / 1_048_576, 2)

    # ── Generic JSON cache (earnings, economic cal, treasury, news, VIX) ──────

    def get(self, key: str, ttl: float) -> Optional[Any]:
        row = self._conn.execute(
            "SELECT fetched_at, payload FROM cache_meta WHERE key=?", (key,)
        ).fetchone()
        if row is None:
            return None
        age = time.time() - row[0]
        if age > ttl:
            return None
        try:
            return json.loads(row[1])
        except Exception:
            return None

    def put(self, key: str, data: Any) -> None:
        payload = json.dumps(data, default=str)
        with self._conn:
            self._conn.execute(
                "INSERT INTO cache_meta(key, fetched_at, payload) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET fetched_at=excluded.fetched_at, "
                "payload=excluded.payload",
                (key, time.time(), payload),
            )

    # ── Parquet price cache ───────────────────────────────────────────────────

    def get_prices(self, ticker: str, ttl: float = TTL_OHLCV) -> Optional[pd.DataFrame]:
        """Fresh cached OHLCV for ``ticker``, depth-extended when possible.

        Freshness is governed ONLY by the shallow parquet's mtime, exactly as
        before — a stale or missing shallow file still returns None, so the
        deep cache can never serve bars the live path hasn't refreshed.
        When the shallow file IS fresh and a deep-history parquet exists
        (cache/prices_deep — Phase 1G.7 research deepening), the deep history
        is merged UNDER the fresh bars (fresh bars win on overlap; overlap
        closes are validated against adjustment drift). Phase 1G.17 — fixes
        the cache-layer depth loss classified DATA_CORRECTNESS_BUG by
        research/voyager_starvation_cache_audit.py.
        """
        path = self._price_dir / f"{ticker.upper()}.parquet"
        if not path.exists():
            return None
        age = time.time() - path.stat().st_mtime
        if age > ttl:
            return None
        try:
            df = pd.read_parquet(path)
        except Exception as exc:
            logger.warning("Price cache corrupt for %s: %s", ticker, exc)
            return None
        deep_path = self._cache_dir / "prices_deep" / f"{ticker.upper()}.parquet"
        if deep_path.exists():
            try:
                deep = pd.read_parquet(deep_path)
                df = merge_price_frames(deep, df)
            except Exception as exc:
                logger.debug("Deep price merge skipped for %s: %s", ticker, exc)
        return df

    def put_prices(self, ticker: str, df: pd.DataFrame) -> None:
        """Write OHLCV parquet, merge-on-write (Phase 1G.17).

        Pre-1G.17 this OVERWROTE the parquet, so any ticker whose cache went
        stale (>12 h) was clobbered back to the next fetcher's window — the
        nightly universe builder fetches 90 calendar days, repeatedly erasing
        the 260-bar history the VOYAGER scanner had bought from the provider
        the same morning. Existing history is now merged under the new
        window (new bars win on overlap; overlap validated against
        adjustment drift, where the new fetch wins outright). Failures
        degrade to the old overwrite behavior.
        """
        path = self._price_dir / f"{ticker.upper()}.parquet"
        out = df
        if path.exists():
            try:
                old = pd.read_parquet(path)
                out = merge_price_frames(old, df)
            except Exception:
                out = df
        try:
            out.to_parquet(path, compression="snappy")
        except Exception as exc:
            logger.error("Failed to write price parquet %s: %s", ticker, exc)

    # ── SPY regime-cache (dedicated slot, merge-on-write) ────────────────────
    #
    # Separate from the regular production OHLCV parquet (cache/prices/SPY.parquet)
    # which is overwritten with only ~70 recent bars on each scanner run.  This
    # slot keeps a growing multi-year DataFrame so regime-gate computations
    # (200d MA, 10d RS) always have enough history without a cold Alpaca fetch.

    _SPY_REGIME_FILE = "SPY_regime.parquet"

    def get_spy_bars(self, min_bars: int = 250) -> Optional[pd.DataFrame]:
        """
        Return the SPY regime DataFrame if it is fresh and has ≥ min_bars rows.
        TTL: TTL_SPY_BARS (4 h).  Returns None on miss — caller must fetch and
        call put_spy_bars().
        """
        path = self._price_dir / self._SPY_REGIME_FILE
        if not path.exists():
            return None
        age = time.time() - path.stat().st_mtime
        if age > TTL_SPY_BARS:
            return None
        try:
            df = pd.read_parquet(path)
            df.index = pd.to_datetime(df.index)
            if len(df) < min_bars:
                return None
            return df
        except Exception as exc:
            logger.warning("SPY regime cache corrupt: %s", exc)
            return None

    def put_spy_bars(self, df: pd.DataFrame) -> None:
        """
        Merge *df* into the SPY regime parquet, keeping all history.
        Deduplicates on the date index so overlapping fetches are idempotent.
        """
        path = self._price_dir / self._SPY_REGIME_FILE
        try:
            if path.exists():
                existing = pd.read_parquet(path)
                existing.index = pd.to_datetime(existing.index)
                df = pd.concat([existing, df]).sort_index()
                df = df[~df.index.duplicated(keep="last")]
            df.to_parquet(path, compression="snappy")
            logger.debug("SPY regime cache updated: %d bars", len(df))
        except Exception as exc:
            logger.error("Failed to write SPY regime cache: %s", exc)

    # ── Parquet fundamentals cache ────────────────────────────────────────────

    def get_fundamentals(self, ticker: str, ttl: float = TTL_FUNDAMENTALS) -> Optional[dict]:
        path = self._fund_dir / f"{ticker.upper()}.json"
        if not path.exists():
            return None
        age = time.time() - path.stat().st_mtime
        if age > ttl:
            return None
        try:
            return json.loads(path.read_text())
        except Exception:
            return None

    def put_fundamentals(self, ticker: str, data: dict) -> None:
        path = self._fund_dir / f"{ticker.upper()}.json"
        try:
            path.write_text(json.dumps(data, default=str))
        except Exception as exc:
            logger.error("Failed to write fundamentals %s: %s", ticker, exc)


# Module-level singleton — all modules share one Gatekeeper instance.
_gate: Optional[Gatekeeper] = None


def get_gatekeeper() -> Gatekeeper:
    global _gate
    if _gate is None:
        _gate = Gatekeeper()
    return _gate

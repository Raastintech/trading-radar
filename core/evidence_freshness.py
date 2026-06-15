"""
core/evidence_freshness.py — cache-only freshness probes for the dashboard's
Mode-3 Evidence Freshness panel and the `freshness-audit` operator command.

Pure, dependency-light, **credential-free** (never imports core.config), and
**side-effect-free**: it only ``stat()``s files, reads a parquet index, and
parses JSON already on disk.  No providers, no DB writes, no governance /
execution / live-capital / universe / gate logic.  Both production (the
dashboard) and research (the audit script) may import it.

The decision-grade contract: every field resolves to an explicit
(status, age, source, reason) — a field is only ``unknown`` when the source
truly lacks metadata, and it then says *why*.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from core.session import _is_trading_day  # cred-free import

# Universe snapshot is "current" while its build date is the latest completed
# trading day; a hard hour-TTL would wrongly flag a valid Friday snapshot as
# stale over the weekend, which is exactly the bug this module fixes.


# ── time helpers ─────────────────────────────────────────────────────────────

def _utc_now(now: Optional[datetime] = None) -> datetime:
    return now or datetime.now(timezone.utc)


def latest_completed_trading_day(now: Optional[datetime] = None) -> date:
    """Most recent calendar date that is a completed NYSE trading day on/before
    today (UTC date).  Weekend/holiday-aware via core.session._is_trading_day.

    Note: intraday-vs-after-close is not modelled here (the price cache itself is
    the source of truth for whether *today's* bar exists); this only answers
    'what is the latest session that should have a daily bar by now'.  We treat
    today as completed only if it is a trading day and it is past ~21:00 UTC
    (after the US close); otherwise we step back to the prior trading day.
    """
    n = _utc_now(now)
    d = n.date()
    # If today is a trading day but the US cash close (~20:00 ET ≈ 00:00 UTC
    # next day) hasn't happened, the freshest *completed* session is yesterday-
    # or-earlier.  Use 21:00 UTC as a conservative "close has printed" cutoff.
    if _is_trading_day(d) and n.hour >= 21:
        return d
    d = d - timedelta(days=1)
    for _ in range(7):
        if _is_trading_day(d):
            return d
        d = d - timedelta(days=1)
    return d


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    s = str(value).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        try:
            dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _age_seconds(ts: Any, now: Optional[datetime] = None) -> Optional[int]:
    dt = _parse_iso(ts)
    if dt is None:
        return None
    return int((_utc_now(now) - dt).total_seconds())


# ── price cache (daily bars) ─────────────────────────────────────────────────

def _parquet_last_date(path: Path) -> Optional[date]:
    if not path.exists():
        return None
    try:
        import pandas as pd  # local import keeps module import cheap
        df = pd.read_parquet(path, columns=["close"])
        if df.empty:
            return None
        idx = pd.to_datetime(df.index)
        return idx.max().date()
    except Exception:
        return None


def price_cache_bar_status(prices_dir: Path, deep_dir: Optional[Path] = None,
                           *, benchmark: str = "SPY",
                           now: Optional[datetime] = None) -> Dict[str, Any]:
    """Resolve 'daily bars current' from the actual price cache (cache-only).

    status ∈ {current, stale, missing, unknown}.  Never returns a bare
    'unknown' without a ``reason``.
    """
    prices_dir = Path(prices_dir)
    cur = prices_dir / f"{benchmark}.parquet"
    latest = _parquet_last_date(cur)
    source = f"cache/prices/{benchmark}.parquet"
    if latest is None and deep_dir is not None:
        deep = Path(deep_dir) / f"{benchmark}.parquet"
        latest = _parquet_last_date(deep)
        if latest is not None:
            source = f"cache/prices_deep/{benchmark}.parquet"
    expected = latest_completed_trading_day(now)
    if latest is None:
        return {"field": "daily bars", "status": "missing", "latest_bar": None,
                "expected": expected.isoformat(), "source": source,
                "reason": f"no {benchmark} parquet in price cache"}
    if latest >= expected:
        return {"field": "daily bars", "status": "current",
                "latest_bar": latest.isoformat(), "expected": expected.isoformat(),
                "source": source, "reason": None}
    # stale — count trading days behind
    behind = 0
    d = expected
    while d > latest and behind < 30:
        if _is_trading_day(d):
            behind += 1
        d -= timedelta(days=1)
    return {"field": "daily bars", "status": "stale",
            "latest_bar": latest.isoformat(), "expected": expected.isoformat(),
            "source": source, "trading_days_behind": behind,
            "reason": f"latest {latest.isoformat()} < expected {expected.isoformat()}"}


# ── universe snapshot ────────────────────────────────────────────────────────

def universe_artifact_meta(snapshot_path: Path,
                           *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Resolve 'universe age' from the universe snapshot mtime/generated_at +
    count (cache-only, NO 2h discard — a valid weekend snapshot still resolves).

    A snapshot is ``stale`` only when its build date is older than the latest
    completed trading day; otherwise ``current``.  Missing → explicit path.
    """
    p = Path(snapshot_path)
    source = "cache/universe/universe_snapshot_latest.json"
    if not p.exists():
        return {"field": "universe", "status": "missing", "exists": False,
                "age_seconds": None, "count": None, "source": source,
                "reason": f"expected artifact not found: {source}"}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        return {"field": "universe", "status": "unknown", "exists": True,
                "age_seconds": None, "count": None, "source": source,
                "reason": f"unreadable snapshot: {e.__class__.__name__}"}
    gen = data.get("generated_at") or (data.get("summary") or {}).get("built_at")
    age = _age_seconds(gen, now)
    if age is None:
        age = int((_utc_now(now).timestamp() - p.stat().st_mtime))
        gen = datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat()
    # count
    count = (data.get("summary") or {}).get("base_universe_size")
    if count is None:
        for k in ("base_universe", "strategy_candidates"):
            v = data.get(k)
            if isinstance(v, list):
                count = len(v)
                break
    # staleness: build date vs latest completed trading day
    gen_dt = _parse_iso(gen)
    expected = latest_completed_trading_day(now)
    stale = bool(gen_dt and gen_dt.date() < expected)
    return {"field": "universe", "status": "stale" if stale else "current",
            "exists": True, "age_seconds": age, "generated_at": gen,
            "count": count, "fallback_used": bool((data.get("summary") or {}).get("fallback_used")
                                                   or data.get("fallback_used")),
            "source": source, "expected_session": expected.isoformat(),
            "reason": (f"snapshot built {gen_dt.date().isoformat()} < latest session "
                       f"{expected.isoformat()}" if stale else None)}


# ── generic artifact age ─────────────────────────────────────────────────────

def artifact_meta(path: Path, *, generated_field: str = "generated_at",
                  now: Optional[datetime] = None) -> Dict[str, Any]:
    """exists / age_seconds / mtime_iso / generated_at for any JSON/text artifact."""
    p = Path(path)
    if not p.exists():
        return {"exists": False, "age_seconds": None, "mtime_iso": None,
                "generated_at": None, "source": str(p)}
    mtime = p.stat().st_mtime
    gen = None
    if p.suffix == ".json":
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            gen = d.get(generated_field) or d.get("built_at") or d.get("generated_at")
        except Exception:
            gen = None
    age = _age_seconds(gen, now) if gen else int(_utc_now(now).timestamp() - mtime)
    return {"exists": True, "age_seconds": age,
            "mtime_iso": datetime.fromtimestamp(mtime, timezone.utc).isoformat(),
            "generated_at": gen, "source": str(p)}


def fmt_age_short(age_seconds: Optional[int]) -> str:
    if age_seconds is None:
        return "N/A"
    s = max(0, int(age_seconds))
    d, rem = divmod(s, 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    if d:
        return f"{d}d{h:02d}h"
    if h:
        return f"{h}h{m:02d}m"
    return f"{m}m"

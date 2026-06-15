#!/usr/bin/env python3
"""
research/provider_freshness_audit.py — Phase 2B.4 provider + pipeline audit.

Cache-only inspector.  Walks the on-disk caches (db/trading.db cache_meta,
fmp_endpoint_log, fmp_budget_monthly) and the per-artifact JSON sidecars
under cache/research/ to answer two questions:

  1. Which FMP-backed artifacts are stale (or fresh) right now, and
     therefore which endpoints would be hit by the next premarket /
     nightly / gatekeeper-refresh / mcp-audit-session fire?
  2. Are the dashboard / research pipeline artifacts fresh enough for
     the operator to trust the panels — and if not, what is the exact
     refresh command?

The audit NEVER calls FMP, Alpaca, Tradier, or any other provider.  It
NEVER mutates any DB row, governance state, or paper evidence.  It is
import-safe without ``SNIPER_ENV_PATH`` (no ``core.config``
dependency) so the operator can run it from a stripped environment.

Outputs:
  cache/research/provider_freshness_audit_latest.json
  logs/provider_freshness_audit_latest.txt
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# core.artifact_freshness is pure stdlib — safe to import without creds.
from core.artifact_freshness import compute_freshness  # noqa: E402

logger = logging.getLogger("provider_freshness_audit")

CACHE_DIR = REPO / "cache" / "research"
LOG_DIR = REPO / "logs"
DB_PATH = REPO / "db" / "trading.db"

HOUR = 3600
DAY = 24 * HOUR


# ── FMP endpoint catalog ────────────────────────────────────────────────────
#
# Mirrors the TTL constants in core/data_gatekeeper.py.  We DUPLICATE the
# values here on purpose so the audit can run without importing the FMP
# stack (which transitively requires credentials).  The test suite
# asserts the audit's TTL view stays in sync with the canonical
# constants; drift breaks the test.

FMP_TTL_TABLE: Dict[str, int] = {
    "fmp:vix":           5 * 60,          # TTL_VIX
    "fmp:quote":         20,              # TTL_QUOTE (intraday)
    "fmp:bars":          12 * HOUR,       # TTL_OHLCV (per-ticker bars)
    "fmp:spy_bars":      12 * HOUR,       # TTL_OHLCV
    "fmp:treasury":      4 * HOUR,        # TTL_TREASURY
    "fmp:econ_cal":      4 * HOUR,        # TTL_ECONOMIC_CAL
    "fmp:news":          1 * HOUR,        # TTL_NEWS
    "fmp:earnings_cal":  13 * HOUR,       # TTL_EARNINGS_CAL — Phase 2B.4 bump
    "fmp:past_earnings": 13 * HOUR,       # TTL_EARNINGS_CAL — Phase 2B.4 bump
    "fmp:profile":       24 * HOUR,       # TTL_FUNDAMENTALS
    "fmp:fundamentals":  24 * HOUR,       # TTL_FUNDAMENTALS
    "fmp:grades":        24 * HOUR,       # TTL_FUNDAMENTALS (graded as fundamentals)
    "fmp:insider":       24 * HOUR,       # TTL_FUNDAMENTALS
    "fmp:sector_pe":     24 * HOUR,       # 24h
    "fmp:dcf":           7 * DAY,         # not actively used, but enumerated
    "13f:v2":            14 * DAY,        # institutional — read on a weekly cadence
}

# Endpoints that the dashboard / research path is allowed to call FMP for.
# Anything not listed here MUST come from cache.  This is documentation
# only — enforcement lives in the doctrine doc, not the code.
FMP_ALLOWED_CALLERS = {
    "fmp:vix":           {"daemon", "dashboard"},
    "fmp:quote":         {"daemon", "dashboard"},
    "fmp:bars":          {"daemon", "research"},
    "fmp:spy_bars":      {"daemon", "dashboard"},
    "fmp:treasury":      {"daemon", "dashboard"},
    "fmp:econ_cal":      {"daemon", "dashboard"},
    "fmp:news":          {"daemon", "dashboard"},
    "fmp:earnings_cal":  {"daemon", "dashboard", "gatekeeper_refresh"},
    "fmp:past_earnings": {"strategies"},
    "fmp:profile":       {"daemon", "research"},
    "fmp:fundamentals":  {"daemon", "research", "gatekeeper"},
    "fmp:grades":        {"daemon", "research"},
    "fmp:insider":       {"daemon", "research"},
    "fmp:sector_pe":     {"daemon", "dashboard"},
    "fmp:dcf":           {"research"},
    "13f:v2":            {"research"},
}

# Cadence run shapes — how many distinct cache_meta keys each cadence
# typically touches.  Used to estimate next-fire provider load.
RUN_SHAPES: Dict[str, Dict[str, int]] = {
    "premarket": {
        # Provider-tagged steps that fire under cmd_premarket.
        "fmp:earnings_cal:5":  1,   # gatekeeper-refresh tail
        # Most of the heavy provider load (regime forecast, alpha board,
        # overlay) is captured under the per-ticker fmp:bars / fmp:profile
        # families which we account for via the endpoint log totals.
    },
    "nightly": {
        "fmp:earnings_cal:5":  1,
        # Alpha + lenses fire many provider calls; magnitude is logged
        # in fmp_endpoint_log so we report observed rather than synthetic.
    },
    "gatekeeper_refresh": {
        "fmp:earnings_cal:5":  1,
    },
    "mcp_audit_session": {
        # Cache-only — no provider calls expected.
    },
}


# ── Helpers ─────────────────────────────────────────────────────────────────

def _classify_key(key: str) -> str:
    """Reduce a cache_meta key (e.g. ``fmp:profile:NVDA``) to its TTL family."""
    if not key:
        return ""
    parts = key.split(":", 2)
    if len(parts) >= 2:
        return ":".join(parts[:2])
    return key


def _ttl_for(prefix: str) -> Optional[int]:
    return FMP_TTL_TABLE.get(prefix)


def _now() -> float:
    return time.time()


def _fmt_age(sec: float) -> str:
    if sec < 60:
        return f"{int(sec)}s"
    if sec < 3600:
        return f"{int(sec/60)}m"
    if sec < 86400:
        return f"{sec/3600:.1f}h"
    return f"{sec/86400:.1f}d"


def _read_only_conn(path: Path) -> Optional[sqlite3.Connection]:
    if not path.exists():
        return None
    try:
        uri = f"file:{path}?mode=ro"
        return sqlite3.connect(uri, uri=True, timeout=5.0)
    except sqlite3.Error as exc:
        logger.warning("read-only DB open failed: %s", exc)
        return None


# ── Provider cache scan ─────────────────────────────────────────────────────

@dataclass
class FMPCacheRow:
    prefix: str
    count: int
    oldest_age_s: Optional[int]
    newest_age_s: Optional[int]
    ttl_s: Optional[int]
    stale_count: int
    fresh_count: int
    next_call_predicted: bool
    allowed_callers: List[str]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "prefix":              self.prefix,
            "count":               self.count,
            "oldest_age_s":        self.oldest_age_s,
            "newest_age_s":        self.newest_age_s,
            "ttl_s":               self.ttl_s,
            "stale_count":         self.stale_count,
            "fresh_count":         self.fresh_count,
            "next_call_predicted": self.next_call_predicted,
            "allowed_callers":     self.allowed_callers,
        }


def _read_cache_meta(conn: sqlite3.Connection) -> List[Tuple[str, float]]:
    try:
        return [(str(k), float(t)) for k, t in conn.execute(
            "SELECT key, fetched_at FROM cache_meta"
        ).fetchall()]
    except sqlite3.Error:
        return []


def cache_meta_by_key(conn: Optional[sqlite3.Connection],
                      now: Optional[float] = None) -> Dict[str, int]:
    """Return ``{cache_key: age_seconds}`` — used by the specific-key
    next-call predictor.  Empty dict when the DB is missing.
    """
    if conn is None:
        return {}
    now_ts = now if now is not None else _now()
    return {k: int(now_ts - t) for k, t in _read_cache_meta(conn)}


def scan_cache_meta(conn: Optional[sqlite3.Connection],
                    now: Optional[float] = None) -> List[FMPCacheRow]:
    """Group cache_meta rows by TTL family and report aggregate freshness."""
    if conn is None:
        return []
    now_ts = now if now is not None else _now()
    rows = _read_cache_meta(conn)

    by_prefix: Dict[str, List[float]] = {}
    for key, fetched_at in rows:
        pref = _classify_key(key)
        if not pref:
            continue
        by_prefix.setdefault(pref, []).append(float(fetched_at))

    out: List[FMPCacheRow] = []
    for pref in sorted(by_prefix):
        timestamps = by_prefix[pref]
        if not timestamps:
            continue
        ttl = _ttl_for(pref)
        ages = sorted(int(now_ts - ts) for ts in timestamps)
        oldest = ages[-1]
        newest = ages[0]
        if ttl is None:
            stale_n = 0
            fresh_n = len(ages)
        else:
            stale_n = sum(1 for a in ages if a > ttl)
            fresh_n = len(ages) - stale_n
        # If TTL is known: next caller for THIS prefix triggers FMP only if
        # the oldest stale slot is what they query.  We use a conservative
        # rule — predict a call if any entry is stale, OR if the newest
        # is past TTL (single-key endpoints like the earnings calendar).
        if ttl is None:
            predicted = False
        elif len(ages) == 1:
            predicted = newest > ttl
        else:
            predicted = stale_n > 0
        allowed = sorted(FMP_ALLOWED_CALLERS.get(pref, []))
        out.append(FMPCacheRow(
            prefix=pref,
            count=len(ages),
            oldest_age_s=oldest,
            newest_age_s=newest,
            ttl_s=ttl,
            stale_count=stale_n,
            fresh_count=fresh_n,
            next_call_predicted=predicted,
            allowed_callers=allowed,
        ))
    return out


# ── Endpoint log + budget ───────────────────────────────────────────────────

def scan_endpoint_log(conn: Optional[sqlite3.Connection],
                      since_hours: float = 168.0) -> Dict[str, Dict[str, int]]:
    """Per-endpoint counts of provider calls and cache hits."""
    if conn is None:
        return {}
    cutoff = _now() - float(since_hours) * HOUR
    try:
        rows = conn.execute(
            "SELECT endpoint, saved, COUNT(*) FROM fmp_endpoint_log "
            "WHERE ts >= ? GROUP BY endpoint, saved",
            (cutoff,),
        ).fetchall()
    except sqlite3.Error:
        return {}
    out: Dict[str, Dict[str, int]] = {}
    for endpoint, saved, count in rows:
        node = out.setdefault(endpoint, {"calls": 0, "cache_hits": 0})
        if saved:
            node["cache_hits"] += int(count)
        else:
            node["calls"] += int(count)
    # Add a cache_hit_rate per row for convenience.
    for node in out.values():
        total = node["calls"] + node["cache_hits"]
        node["total"] = total
        node["cache_hit_rate"] = (
            round(node["cache_hits"] / total, 3) if total else 0.0
        )
    return out


def scan_budget(conn: Optional[sqlite3.Connection]) -> Dict[str, Any]:
    """Latest 3 months from fmp_budget_monthly + today's daily total."""
    if conn is None:
        return {}
    out: Dict[str, Any] = {"monthly": [], "today": 0}
    try:
        out["monthly"] = [
            {"month": m, "calls_used": int(c)}
            for m, c in conn.execute(
                "SELECT month, calls_used FROM fmp_budget_monthly "
                "ORDER BY month DESC LIMIT 3"
            ).fetchall()
        ]
    except sqlite3.Error:
        pass
    try:
        today_iso = date.today().isoformat()
        row = conn.execute(
            "SELECT calls_used FROM fmp_budget WHERE day=?", (today_iso,)
        ).fetchone()
        out["today"] = int(row[0]) if row else 0
    except sqlite3.Error:
        pass
    return out


def scan_bandwidth(conn: Optional[sqlite3.Connection]) -> float:
    """Bandwidth MB used this calendar month."""
    if conn is None:
        return 0.0
    month_start = date.today().replace(day=1)
    cutoff_ts = time.mktime(month_start.timetuple())
    try:
        row = conn.execute(
            "SELECT SUM(resp_bytes) FROM fmp_endpoint_log "
            "WHERE ts >= ? AND saved=0",
            (cutoff_ts,),
        ).fetchone()
    except sqlite3.Error:
        return 0.0
    return round((row[0] or 0) / 1_048_576, 2)


# ── Pipeline freshness ──────────────────────────────────────────────────────

@dataclass
class PipelineRow:
    name: str
    path: Path
    age_s: Optional[int]
    threshold_s: int
    verdict: str            # PASS | WARN | FAIL
    cause: str
    refresh_command: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name":            self.name,
            "path":            str(self.path),
            "age_s":           self.age_s,
            "threshold_s":     self.threshold_s,
            "verdict":         self.verdict,
            "cause":           self.cause,
            "refresh_command": self.refresh_command,
        }


# (name, relative path under repo root, stale-threshold seconds, refresh command)
PIPELINE_SPEC: List[Tuple[str, str, int, str]] = [
    ("regime_forecast",
     "cache/research/regime_forecast_latest.json",
     24 * HOUR,
     "./scripts/run_research_cycle.sh forecast"),
    ("alpha_discovery_board",
     "cache/research/alpha_discovery_board_latest.json",
     36 * HOUR,
     "./scripts/run_research_cycle.sh alpha"),
    ("alpha_discovery_overlay",
     "cache/research/alpha_discovery_overlay_latest.json",
     12 * HOUR,
     "./scripts/run_research_cycle.sh alpha-overlay"),
    ("research_delta",
     "cache/research/research_delta_latest.json",
     12 * HOUR,
     "./scripts/run_research_cycle.sh delta"),
    ("mcp_analysis",
     "cache/research/mcp_analysis_latest.json",
     12 * HOUR,
     "./scripts/run_research_cycle.sh mcp-audit-session regular"),
    ("paper_state_hygiene",
     "cache/research/paper_state_hygiene_latest.json",
     36 * HOUR,
     "./scripts/run_research_cycle.sh risk-telemetry"),
    ("slippage_telemetry",
     "cache/research/slippage_telemetry_latest.json",
     36 * HOUR,
     "./scripts/run_research_cycle.sh risk-telemetry"),
    ("portfolio_concentration",
     "cache/research/portfolio_concentration_latest.json",
     36 * HOUR,
     "./scripts/run_research_cycle.sh risk-telemetry"),
    ("shadow_sizing",
     "cache/research/shadow_sizing_latest.json",
     36 * HOUR,
     "./scripts/run_research_cycle.sh risk-telemetry"),
    ("broker_positions_snapshot",
     "cache/state/broker_positions_snapshot.json",
     8 * HOUR,
     ".venv/bin/python scripts/snapshot_broker_positions.py"),
    ("holdout_scoreboard",
     "cache/research/holdout_2026h2_scoreboard_latest.json",
     36 * HOUR,
     "./scripts/run_research_cycle.sh holdout"),
    # Phase 1G.12 — Options Regime Lens sidecar.  Refreshed in premarket +
    # nightly (provider cadence); 36h stale threshold tolerates a single
    # missed cycle.  Provider calls are Alpaca/Tradier (options chains), NOT
    # FMP — see the dedicated options_regime budget block below.
    ("options_regime_lens",
     "cache/research/options_regime_lens_latest.json",
     36 * HOUR,
     "./scripts/run_research_cycle.sh options-regime"),
]


def _pipeline_row(name: str, path: Path, threshold_s: int, refresh_cmd: str,
                  now_ts: float) -> PipelineRow:
    if not path.exists():
        return PipelineRow(
            name=name, path=path,
            age_s=None,
            threshold_s=threshold_s,
            verdict="FAIL",
            cause="artifact_missing",
            refresh_command=refresh_cmd,
        )
    try:
        mtime = path.stat().st_mtime
    except OSError as exc:
        return PipelineRow(
            name=name, path=path,
            age_s=None,
            threshold_s=threshold_s,
            verdict="FAIL",
            cause=f"stat_error:{exc.__class__.__name__}",
            refresh_command=refresh_cmd,
        )
    age = int(now_ts - mtime)
    # WARN at 75% of stale threshold; FAIL above threshold.
    warn_cut = int(threshold_s * 0.75)
    if age > threshold_s:
        verdict, cause = "FAIL", f"age>{threshold_s // HOUR}h"
    elif age > warn_cut:
        verdict, cause = "WARN", f"age>{warn_cut // HOUR}h"
    else:
        verdict, cause = "PASS", "fresh"
    return PipelineRow(
        name=name, path=path,
        age_s=age, threshold_s=threshold_s,
        verdict=verdict, cause=cause,
        refresh_command=refresh_cmd,
    )


def scan_pipelines(repo: Path = REPO, now: Optional[float] = None) -> List[PipelineRow]:
    now_ts = now if now is not None else _now()
    out: List[PipelineRow] = []
    for name, rel, threshold, cmd in PIPELINE_SPEC:
        path = repo / rel
        out.append(_pipeline_row(name, path, threshold, cmd, now_ts))
    return out


# ── Next-run prediction ─────────────────────────────────────────────────────

def predict_next_fmp_calls(by_key_age: Dict[str, int]) -> Dict[str, Dict[str, Any]]:
    """For each scheduled cadence (premarket / nightly / etc.), report
    whether the next fire is likely to call FMP for the listed keys.

    Looks up the EXACT cache_meta key (e.g. ``fmp:earnings_cal:5``) — a
    different days_ahead variant of the earnings calendar being stale
    does not trigger a prediction here, because the next caller will use
    its own variant.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for cadence, shape in RUN_SHAPES.items():
        slots = []
        likely_calls = 0
        for key, expected_calls in shape.items():
            pref = _classify_key(key)
            ttl = _ttl_for(pref)
            age = by_key_age.get(key)
            if age is None:
                predicted = True            # missing key → next call WILL fire
            elif ttl is None:
                predicted = False           # unknown TTL → don't synthesize
            else:
                predicted = age > ttl
            slot = {
                "key":             key,
                "prefix":          pref,
                "ttl_s":           ttl,
                "newest_age_s":    age,
                "call_predicted":  predicted,
                "expected_calls":  int(expected_calls) if predicted else 0,
            }
            if predicted:
                likely_calls += int(expected_calls)
            slots.append(slot)
        out[cadence] = {
            "slots":         slots,
            "likely_calls":  likely_calls,
            "note": (
                "specific-key prediction; the daemon / alpha board / "
                "lens prebuild can independently fire many more calls "
                "that this audit does not synthesize."
            ),
        }
    return out


# ── Selected-ticker audit ───────────────────────────────────────────────────

def audit_selected_ticker(ticker: str, now: Optional[float] = None) -> Dict[str, Any]:
    """Per-ticker freshness verdict for the Stock Lens + Executive Gatekeeper
    artifacts the dashboard reads when a ticker is selected.
    """
    tk = ticker.strip().upper()
    if not tk:
        return {"ticker": "", "error": "empty_ticker"}
    now_ts = now if now is not None else _now()
    out: Dict[str, Any] = {"ticker": tk}
    for name, rel, threshold, cmd in [
        ("stock_lens",
         f"cache/research/stock_lens_{tk}_latest.json",
         24 * HOUR,
         f"./scripts/run_research_cycle.sh lens {tk}"),
        ("executive_gatekeeper",
         f"cache/research/executive_gatekeeper_{tk}_latest.json",
         24 * HOUR,
         f"./scripts/run_research_cycle.sh gatekeeper {tk}"),
    ]:
        out[name] = _pipeline_row(name, REPO / rel, threshold, cmd, now_ts).as_dict()
    return out


# ── Top-level audit assembly ────────────────────────────────────────────────

def run_audit(*, repo: Path = REPO, ticker: Optional[str] = None,
              now: Optional[float] = None) -> Dict[str, Any]:
    """Top-level audit entry — pure read of caches; no provider calls."""
    now_ts = now if now is not None else _now()
    conn = _read_only_conn(repo / "db" / "trading.db")
    try:
        cache_rows = scan_cache_meta(conn, now=now_ts)
        by_key_age = cache_meta_by_key(conn, now=now_ts)
        endpoint_usage = scan_endpoint_log(conn, since_hours=24.0)
        endpoint_usage_7d = scan_endpoint_log(conn, since_hours=168.0)
        budget = scan_budget(conn)
        bandwidth_mb = scan_bandwidth(conn)
    finally:
        if conn is not None:
            conn.close()

    pipelines = scan_pipelines(repo=repo, now=now_ts)
    next_runs = predict_next_fmp_calls(by_key_age)

    # Phase 2B.3 gatekeeper-refresh cap warning — surfaces here if the
    # latest gatekeeper_refresh wrote a `cap_hit` summary to its sidecar.
    gk_refresh_summary = _read_gatekeeper_refresh_summary(repo)

    verdict_counts = _verdict_counts(pipelines)
    selected_ticker_audit = None
    if ticker:
        selected_ticker_audit = audit_selected_ticker(ticker, now=now_ts)

    return {
        "schema_version":  "provider_freshness_audit.v1",
        "generated_at":    datetime.fromtimestamp(now_ts, timezone.utc)
                                   .isoformat(timespec="seconds"),
        "guardrails": [
            "cache-only — no provider calls",
            "no DB mutation",
            "no governance / execution / strategy / paper-evidence changes",
            "dashboard remains cache-only",
        ],
        "fmp": {
            "cache_summary":            [r.as_dict() for r in cache_rows],
            "endpoint_usage_24h":       endpoint_usage,
            "endpoint_usage_7d":        endpoint_usage_7d,
            "budget":                   budget,
            "bandwidth_mb_month":       bandwidth_mb,
            "next_run_predictions":     next_runs,
        },
        "pipelines": {
            "rows":             [r.as_dict() for r in pipelines],
            "verdict_counts":   verdict_counts,
        },
        "gatekeeper_refresh_summary": gk_refresh_summary,
        "selected_ticker":            selected_ticker_audit,
        # Phase 1G.12 — diagnostic-only options-chain provider-call estimate.
        # Alpaca/Tradier, NOT FMP; surfaced separately so the FMP budget view
        # stays clean. Cache-only read of the lens sidecar's feed_status.
        "options_regime":             _read_options_regime_budget(repo),
    }


def _verdict_counts(rows: List[PipelineRow]) -> Dict[str, int]:
    counts = {"PASS": 0, "WARN": 0, "FAIL": 0}
    for r in rows:
        counts[r.verdict] = counts.get(r.verdict, 0) + 1
    return counts


def _read_gatekeeper_refresh_summary(repo: Path) -> Optional[Dict[str, Any]]:
    path = repo / "cache" / "research" / "gatekeeper_refresh_latest.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# Phase 1G.12 — options-regime is wired into premarket + nightly.
_OPTIONS_REGIME_RUNS_PER_DAY = 2
# In-process feed caches (core/alpaca_options_client.py): chain 60s, expiries
# 300s.  Kept as documentation here so the audit need not import the client
# (which would pull core.config and break the cache-only/credential-free path).
_OPTIONS_CHAIN_TTL_S = 60
_OPTIONS_EXPIRY_TTL_S = 300


def _read_options_regime_budget(repo: Path) -> Dict[str, Any]:
    """Phase 1G.12 — DIAGNOSTIC-ONLY options-chain provider-call estimate.

    Reads the Options Regime Lens sidecar's recorded ``feed_status`` (actual
    serve / enrich counts from the last run) and projects a per-day estimate.
    Options chains are served by Alpaca (primary) + Tradier (IV/greeks
    enrichment) — they do NOT consume the FMP monthly budget, so this is
    surfaced separately from the FMP scans. Cache-only; never calls a provider,
    never mutates the budget tables. Returns a sidecar_present=False stub when
    the lens has not run yet."""
    path = repo / "cache" / "research" / "options_regime_lens_latest.json"
    if not path.exists():
        return {
            "sidecar_present": False,
            "fmp_budget_impact": 0,
            "note": ("Options Regime Lens has not run yet. When it does, chains "
                     "are served by Alpaca + Tradier (NOT FMP) — zero FMP budget "
                     "impact. Diagnostic only; not a gate."),
        }
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"sidecar_present": False, "fmp_budget_impact": 0,
                "error": f"{exc.__class__.__name__}"}

    status = doc.get("feed_status") or {}
    served = status.get("served_counts") or {}
    enrich = status.get("enrich_counts") or {}
    symbols = doc.get("symbols_requested") or []
    alpaca_serves = int(served.get("alpaca", 0))
    tradier_serves = int(served.get("tradier", 0))
    tradier_enrich = int(enrich.get("tradier", 0))

    # Each Alpaca serve is get_expirations (≈1 HTTP) or get_chain (≈2 HTTP:
    # snapshot + contracts-metadata). Use a ×2 upper-bound so the estimate is
    # conservative for budget-risk purposes; enrichment is ≈1 HTTP each.
    est_http_per_run = alpaca_serves * 2 + tradier_serves * 2 + tradier_enrich
    return {
        "sidecar_present": True,
        "generated_at": doc.get("generated_at"),
        "symbols_requested": symbols,
        "n_symbols": len(symbols),
        "feed_configured": bool(doc.get("feed_configured")),
        "feed_order": status.get("feed_order"),
        "provider_family": "alpaca(primary)+tradier(enrich)",
        "fmp_budget_impact": 0,
        "last_run_chain_serves": {
            "alpaca": alpaca_serves,
            "tradier": tradier_serves,
            "tradier_iv_enrich": tradier_enrich,
        },
        "est_provider_http_per_run": est_http_per_run,
        "runs_per_day": _OPTIONS_REGIME_RUNS_PER_DAY,
        "est_provider_http_per_day": est_http_per_run * _OPTIONS_REGIME_RUNS_PER_DAY,
        "cache_ttls_s": {
            "chain": _OPTIONS_CHAIN_TTL_S,
            "expirations": _OPTIONS_EXPIRY_TTL_S,
            "scope": "in-process per run (not shared across invocations)",
        },
        "note": ("Options chains use Alpaca + Tradier, NOT FMP — zero impact on "
                 "the FMP monthly budget. Counts are the last run's actuals from "
                 "feed_status; HTTP-per-run is a conservative ×2 upper bound. "
                 "Diagnostic only; not a gate."),
    }


# ── Text rendering ──────────────────────────────────────────────────────────

def _fmt_text(audit: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("Provider freshness + pipeline audit (Phase 2B.4) — cache-only")
    lines.append(f"Generated: {audit.get('generated_at')}")
    lines.append("")

    fmp = audit.get("fmp") or {}
    budget = fmp.get("budget") or {}
    monthly = budget.get("monthly") or []
    today = budget.get("today")
    bandwidth = fmp.get("bandwidth_mb_month")
    lines.append("FMP budget")
    if monthly:
        for row in monthly:
            lines.append(f"  · {row.get('month')}  calls_used={row.get('calls_used'):,}")
    else:
        lines.append("  · no monthly budget rows on disk")
    lines.append(f"  · today  calls_used={today}")
    lines.append(f"  · bandwidth_mb_month={bandwidth}")
    lines.append("")

    lines.append("FMP cache summary (by prefix)")
    lines.append(f"  {'prefix':<20} {'n':>5} {'ttl':>8} {'newest':>8} "
                 f"{'oldest':>8} {'stale':>6} {'next_call':>10}")
    for row in fmp.get("cache_summary") or []:
        ttl = row.get("ttl_s")
        ttl_s = _fmt_age(ttl) if ttl else "—"
        newest = _fmt_age(row["newest_age_s"]) if row["newest_age_s"] is not None else "—"
        oldest = _fmt_age(row["oldest_age_s"]) if row["oldest_age_s"] is not None else "—"
        lines.append(
            f"  {row['prefix']:<20} {row['count']:>5} {ttl_s:>8} "
            f"{newest:>8} {oldest:>8} {row['stale_count']:>6} "
            f"{'yes' if row['next_call_predicted'] else 'no':>10}"
        )
    lines.append("")

    lines.append("Endpoint usage (24h: calls / cache_hits / hit_rate)")
    for ep, node in sorted((fmp.get("endpoint_usage_24h") or {}).items()):
        lines.append(
            f"  {ep:<32} calls={node.get('calls', 0):>5} "
            f"hits={node.get('cache_hits', 0):>5} "
            f"rate={node.get('cache_hit_rate', 0.0):.2f}"
        )
    lines.append("")

    lines.append("Next-run predictions")
    for cadence, payload in (fmp.get("next_run_predictions") or {}).items():
        lines.append(f"  · {cadence:<18} likely_calls={payload.get('likely_calls', 0)}")
        for slot in payload.get("slots") or []:
            pred = "yes" if slot.get("call_predicted") else "no"
            lines.append(
                f"      {slot['key']:<28} ttl={_fmt_age(slot['ttl_s']) if slot['ttl_s'] else '—'} "
                f"newest={_fmt_age(slot['newest_age_s']) if slot['newest_age_s'] is not None else '—'} "
                f"call_predicted={pred}"
            )
    lines.append("")

    lines.append("Pipeline freshness")
    pipelines = (audit.get("pipelines") or {}).get("rows") or []
    counts = (audit.get("pipelines") or {}).get("verdict_counts") or {}
    lines.append(
        f"  verdict counts: PASS={counts.get('PASS', 0)}  "
        f"WARN={counts.get('WARN', 0)}  FAIL={counts.get('FAIL', 0)}"
    )
    for row in pipelines:
        age_str = _fmt_age(row["age_s"]) if row["age_s"] is not None else "missing"
        lines.append(
            f"  {row['verdict']:<5}  {row['name']:<28}  age={age_str:<6}  "
            f"({row['cause']})"
        )
        if row["verdict"] != "PASS":
            lines.append(f"        refresh: {row['refresh_command']}")
    lines.append("")

    gk = audit.get("gatekeeper_refresh_summary")
    if gk:
        lines.append("Last gatekeeper-refresh summary")
        summary = gk.get("summary") or {}
        cap_hit = gk.get("cap_hit")
        lines.append(
            f"  planned={summary.get('planned', '?')}  "
            f"ok={summary.get('ok', '?')}  "
            f"error={summary.get('error', '?')}  "
            f"dropped={summary.get('dropped', '?')}  "
            f"cap_hit={cap_hit}"
        )
        if cap_hit:
            lines.append("  WARNING: gatekeeper-refresh cap hit; some "
                         "candidates were not refreshed.  See "
                         "gatekeeper_refresh_latest.json for the dropped list.")
        lines.append("")

    if audit.get("selected_ticker"):
        t = audit["selected_ticker"]
        lines.append(f"Selected ticker audit — {t.get('ticker', '?')}")
        for k in ("stock_lens", "executive_gatekeeper"):
            row = t.get(k) or {}
            age = row.get("age_s")
            age_str = _fmt_age(age) if age is not None else "missing"
            lines.append(
                f"  {row.get('verdict', '—'):<5}  {k:<22}  age={age_str:<7}  "
                f"({row.get('cause', '—')})"
            )
            if row.get("verdict") != "PASS":
                lines.append(f"        refresh: {row.get('refresh_command')}")
        lines.append("")

    org = audit.get("options_regime") or {}
    lines.append("Options Regime Lens — provider calls (Phase 1G.12, diagnostic only)")
    if not org.get("sidecar_present"):
        lines.append("  · sidecar not present yet — 0 FMP impact when it runs "
                     "(Alpaca/Tradier chains, not FMP)")
    else:
        lr = org.get("last_run_chain_serves") or {}
        lines.append(
            f"  · provider={org.get('provider_family')}  "
            f"FMP_budget_impact={org.get('fmp_budget_impact')}  "
            f"symbols={org.get('n_symbols')} {org.get('symbols_requested')}")
        lines.append(
            f"  · last-run serves: alpaca={lr.get('alpaca')} "
            f"tradier={lr.get('tradier')} tradier_iv_enrich={lr.get('tradier_iv_enrich')}")
        lines.append(
            f"  · est_http_per_run≈{org.get('est_provider_http_per_run')} "
            f"× {org.get('runs_per_day')} runs/day "
            f"≈ {org.get('est_provider_http_per_day')}/day (conservative upper bound)")
        ttls = org.get("cache_ttls_s") or {}
        lines.append(
            f"  · feed cache TTLs: chain={ttls.get('chain')}s "
            f"expirations={ttls.get('expirations')}s ({ttls.get('scope')})")
    lines.append("")

    lines.append("Guardrails: " + " | ".join(audit.get("guardrails") or []))
    return "\n".join(lines)


# ── Atomic write ────────────────────────────────────────────────────────────

def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


# ── CLI ─────────────────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Phase 2B.4 cache-only provider freshness + pipeline audit",
    )
    ap.add_argument("--ticker", default=None,
                    help="Optional: include a per-ticker Stock Lens + Gatekeeper "
                         "freshness check.")
    ap.add_argument("--print", action="store_true",
                    help="Also print the prose summary to stdout.")
    ap.add_argument("--json", action="store_true",
                    help="Print the machine-readable JSON to stdout "
                         "(in addition to the sidecar write).")
    ap.add_argument("--no-write", action="store_true",
                    help="Do not write the sidecar/log files (useful for tests).")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s %(message)s",
    )

    audit = run_audit(ticker=args.ticker)
    text = _fmt_text(audit)

    if not args.no_write:
        _atomic_write(
            CACHE_DIR / "provider_freshness_audit_latest.json",
            json.dumps(audit, indent=2, default=str),
        )
        _atomic_write(
            LOG_DIR / "provider_freshness_audit_latest.txt",
            text,
        )
        print(f"wrote {CACHE_DIR / 'provider_freshness_audit_latest.json'}",
              file=sys.stderr)
        print(f"wrote {LOG_DIR / 'provider_freshness_audit_latest.txt'}",
              file=sys.stderr)

    if args.print:
        print(text)
    if args.json:
        print(json.dumps(audit, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

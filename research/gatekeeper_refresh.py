#!/usr/bin/env python3
"""
research/gatekeeper_refresh.py — Phase 2B.2 batched Executive Gatekeeper refresh.

Builds a short list of tickers that most need a fresh Executive Gatekeeper
verdict and runs ``core.executive_gatekeeper.run_executive_gatekeeper`` for
each.  Designed to be invoked from the operator CLI / cron, never from the
dashboard.

Selection sources (cache-only or local DB):

  1. Open positions in ``db/trading.db`` (``decisions`` table with
     ``position_opened=1 AND position_closed=0``)
  2. Top Alpha Discovery candidates from
     ``cache/research/alpha_discovery_board_latest.json`` (A-tier first,
     then B/C; sorted by ``alpha_score``)
  3. Tickers with earnings in the next N days (FMP earnings-calendar
     endpoint; cached 6 h in the FMP gatekeeper, so this is effectively
     a cache read on a normal day)
  4. Tickers with missing or stale Gatekeeper artifacts that already
     have a Stock Lens on disk (Stock Lens is the canonical "we care
     about this ticker" signal in the curated short-list flow)
  5. Optional explicit watchlist tickers passed via ``--watch TICKER…``

Each ticker carries a (priority, reason) pair so the cap selects the most
urgent refreshes first.  After de-dupe and ranking, the run is capped at
``--max`` (default 25) tickers.

Guardrails (mirrors executive_gatekeeper_report.py):
  - cache-first; the Gatekeeper itself does not place new orders, does
    not mutate paper evidence, does not change governance.
  - never invoked from the dashboard.
  - safe to run idempotently — overwrites the per-ticker latest artifact.

Usage:
  cd /home/gem/trading-production
  .venv/bin/python research/gatekeeper_refresh.py
  .venv/bin/python research/gatekeeper_refresh.py --max 10
  .venv/bin/python research/gatekeeper_refresh.py --dry-run --max 25
  .venv/bin/python research/gatekeeper_refresh.py --watch NVDA TSLA AAPL
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# Load creds the same way the other provider research scripts do (e.g.
# research/options_regime_lens.py): prefer SNIPER_ENV_PATH, then a root .env.
# core.config itself only reads a root .env and ignores SNIPER_ENV_PATH, so
# without this the lazy FMP import in select_earnings() raises "ALPACA_API_KEY
# not set" and the earnings-calendar selection source is silently dropped.
# GEM_TRADER_SKIP_DOTENV=true skips both for offline tooling/tests.
try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None
if load_dotenv is not None and os.getenv("GEM_TRADER_SKIP_DOTENV", "").lower() not in {"1", "true", "yes"}:
    _env_path = os.getenv("SNIPER_ENV_PATH", "").strip()
    if _env_path:
        load_dotenv(_env_path, override=False)
    load_dotenv(REPO / ".env", override=False)

# Import after sys.path so research/ can call into core/.
from core.artifact_freshness import (  # noqa: E402
    FRESHNESS_THRESHOLDS,
    compute_freshness,
    gatekeeper_artifact_path,
)

logger = logging.getLogger("gatekeeper_refresh")

CACHE_DIR = REPO / "cache" / "research"
LOG_DIR = REPO / "logs"
DB_PATH = REPO / "db" / "trading.db"

# Priority weights — lower number = higher urgency.  Used only for
# selection ordering, not for the gatekeeper itself.
PRIORITY = {
    "open_position":     10,
    "earnings_today":    15,
    "earnings_tomorrow": 20,
    "explicit_watch":    25,
    "earnings_week":     30,
    "missing_artifact":  35,
    "stale_artifact":    40,
    "alpha_top":         50,
}


@dataclass
class Candidate:
    ticker: str
    priority: int = 99
    reasons: List[str] = field(default_factory=list)

    def merge(self, other: "Candidate") -> "Candidate":
        # Lower priority wins; reasons union-ed.
        merged_pri = min(self.priority, other.priority)
        merged_reasons = list(dict.fromkeys(self.reasons + other.reasons))
        return Candidate(ticker=self.ticker, priority=merged_pri,
                         reasons=merged_reasons)


# ── Selection helpers ───────────────────────────────────────────────────────

def _normalize(t: str) -> str:
    return (t or "").strip().upper()


def _is_us_listed(symbol: str) -> bool:
    """FMP's earnings calendar is global; foreign listings carry an exchange
    suffix after a dot (e.g. PCTN.L London, SVB.TO Toronto, KLM.V TSXV,
    EWG.CN).  US class shares use a hyphen on FMP (BRK-B, not BRK.B), so a '.'
    reliably marks a non-US listing.  Filtering these keeps the gatekeeper
    refresh focused on tradeable US-universe names instead of spending its cap
    on symbols we will never trade — without that filter ~34% of the earnings
    calendar was foreign noise that outranks alpha/stale candidates."""
    return bool(symbol) and "." not in symbol


def select_open_positions(db_path: Path) -> List[Candidate]:
    if not db_path.exists():
        return []
    out: List[Candidate] = []
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "SELECT DISTINCT ticker FROM decisions "
                "WHERE position_opened=1 AND position_closed=0"
            )
            for row in cur.fetchall():
                tk = _normalize(row["ticker"])
                if tk:
                    out.append(Candidate(
                        ticker=tk,
                        priority=PRIORITY["open_position"],
                        reasons=["open_position"],
                    ))
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.warning("open-positions query failed: %s", exc)
    return out


def select_alpha_top(board_path: Path, cap: int) -> List[Candidate]:
    if not board_path.exists() or cap <= 0:
        return []
    try:
        data = json.loads(board_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("alpha board read failed: %s", exc)
        return []
    items = data.get("items") or data.get("candidates") or data.get("rows") or []
    rows: List[Tuple[float, str, str]] = []
    for row in items:
        tk = _normalize((row or {}).get("ticker"))
        if not tk:
            continue
        tier = str((row or {}).get("data_tier") or "").upper()
        score = (row or {}).get("alpha_score")
        try:
            score_f = float(score) if score is not None else 0.0
        except (TypeError, ValueError):
            score_f = 0.0
        rows.append((score_f, tier, tk))
    # A-tier first, then B/C, sorted by alpha_score desc within each.
    rows.sort(key=lambda r: (0 if r[1] == "A" else (1 if r[1] == "B" else 2),
                             -r[0]))
    out: List[Candidate] = []
    for score_f, tier, tk in rows[:cap]:
        out.append(Candidate(
            ticker=tk,
            priority=PRIORITY["alpha_top"],
            reasons=[f"alpha_top:{tier}:{score_f:.1f}"],
        ))
    return out


# Below this many names from the universe snapshot we treat the snapshot as
# unavailable and DISABLE the earnings↔universe intersection (fail open), so a
# missing/corrupt snapshot can never silently drop every earnings candidate down
# to the tiny positions/alpha/lens set.  A healthy snapshot carries ~1000 names.
_MIN_UNIVERSE_FOR_INTERSECT = 50


def _load_known_universe(cache_dir: Path, db_path: Path) -> set:
    """Tradeable/known set the earnings source is intersected against: the
    dynamic universe snapshot (``base_universe``, ~1000 names) ∪ open positions
    ∪ alpha board ∪ on-disk Stock Lens names.  FMP's earnings calendar is global
    with no exchange field, so restricting it to names we actually track keeps
    the gatekeeper cap off OTC/foreign noise while still surfacing imminent-
    earnings names already in our universe.  Returns an empty set when no healthy
    snapshot is available, which DISABLES the intersection (fail open)."""
    snapshot_names: set = set()
    snap_path = REPO / "cache" / "universe" / "universe_snapshot_latest.json"
    try:
        snap = json.loads(snap_path.read_text(encoding="utf-8"))
        for tk in snap.get("base_universe") or []:
            n = _normalize(tk if isinstance(tk, str) else (tk or {}).get("symbol"))
            if n:
                snapshot_names.add(n)
    except (OSError, json.JSONDecodeError, TypeError, AttributeError) as exc:
        logger.warning("universe snapshot read failed (earnings intersection disabled): %s", exc)
    if len(snapshot_names) < _MIN_UNIVERSE_FOR_INTERSECT:
        return set()
    known = set(snapshot_names)
    known.update(c.ticker for c in select_open_positions(db_path))
    known.update(c.ticker for c in select_alpha_top(
        cache_dir / "alpha_discovery_board_latest.json", cap=10_000))
    for path in cache_dir.glob("stock_lens_*_latest.json"):
        name = path.stem
        if name.startswith("stock_lens_") and name.endswith("_latest"):
            n = _normalize(name[len("stock_lens_"):-len("_latest")])
            if n:
                known.add(n)
    return known


def select_earnings(days_ahead: int, known_universe: Optional[set] = None) -> List[Candidate]:
    """Pull cached or fresh FMP earnings calendar.  The FMP client caches
    the calendar for 6 h, so on a normal day this is effectively a cache
    read.  If FMP is not reachable (e.g. dry-run, offline test) the call
    returns []; the workflow degrades gracefully.

    Two filters keep the cap focused on tradeable US names:
      1. foreign exchange-suffixed symbols are dropped (``_is_us_listed``);
      2. when ``known_universe`` is non-empty, only names in that set survive
         (snapshot ∪ positions ∪ alpha ∪ lens).  An empty/None set disables (2)
         so we fail open rather than dropping everything.
    """
    try:
        from core.fmp_client import get_fmp
        cal = get_fmp().get_earnings_calendar(days_ahead=max(1, int(days_ahead)))
    except Exception as exc:  # network, auth, transient
        logger.warning("earnings calendar fetch skipped: %s", exc)
        return []
    today = datetime.now(timezone.utc).date()
    out: List[Candidate] = []
    skipped_foreign = 0
    skipped_off_universe = 0
    for row in cal or []:
        tk = _normalize((row or {}).get("symbol"))
        if not tk:
            continue
        if not _is_us_listed(tk):
            skipped_foreign += 1
            continue
        if known_universe and tk not in known_universe:
            skipped_off_universe += 1
            continue
        date_text = str((row or {}).get("date") or "")[:10]
        try:
            row_date = datetime.fromisoformat(date_text).date()
        except ValueError:
            continue
        delta = (row_date - today).days
        if delta < 0 or delta > days_ahead:
            continue
        if delta == 0:
            pri = PRIORITY["earnings_today"]; tag = "earnings_today"
        elif delta == 1:
            pri = PRIORITY["earnings_tomorrow"]; tag = "earnings_tomorrow"
        else:
            pri = PRIORITY["earnings_week"]; tag = f"earnings_week+{delta}d"
        out.append(Candidate(ticker=tk, priority=pri, reasons=[tag]))
    if skipped_foreign:
        logger.info("earnings calendar: skipped %d non-US (exchange-suffixed) symbols",
                    skipped_foreign)
    if skipped_off_universe:
        logger.info("earnings calendar: skipped %d off-universe symbols (not in tradeable set)",
                    skipped_off_universe)
    return out


def select_missing_or_stale(stock_lens_dir: Path, cap: int) -> List[Candidate]:
    """Walk per-ticker Stock Lens artifacts and flag tickers whose
    Gatekeeper is missing or stale.  Bounds the size of the curated
    short-list so a deep cache does not blow the cap.
    """
    if not stock_lens_dir.exists() or cap <= 0:
        return []
    out: List[Candidate] = []
    # Stock lens files: stock_lens_<TICKER>_latest.json
    for path in stock_lens_dir.glob("stock_lens_*_latest.json"):
        name = path.stem  # stock_lens_NVDA_latest
        if not name.startswith("stock_lens_") or not name.endswith("_latest"):
            continue
        ticker = name[len("stock_lens_"):-len("_latest")].upper()
        if not ticker:
            continue
        gk_path = gatekeeper_artifact_path(ticker)
        if not gk_path.exists():
            out.append(Candidate(
                ticker=ticker,
                priority=PRIORITY["missing_artifact"],
                reasons=["missing_gatekeeper"],
            ))
            continue
        try:
            mtime = gk_path.stat().st_mtime
        except OSError:
            out.append(Candidate(
                ticker=ticker,
                priority=PRIORITY["missing_artifact"],
                reasons=["missing_gatekeeper"],
            ))
            continue
        age = int(time.time() - mtime)
        verdict = compute_freshness(kind="GATEKEEPER", age_seconds=age)
        if verdict.get("stale"):
            out.append(Candidate(
                ticker=ticker,
                priority=PRIORITY["stale_artifact"],
                reasons=[f"stale:{age // 3600}h"],
            ))
    out.sort(key=lambda c: c.priority)
    return out[:cap]


def select_explicit(tickers: Iterable[str]) -> List[Candidate]:
    out: List[Candidate] = []
    for t in tickers or []:
        tk = _normalize(t)
        if not tk:
            continue
        out.append(Candidate(
            ticker=tk,
            priority=PRIORITY["explicit_watch"],
            reasons=["explicit_watch"],
        ))
    return out


def merge_candidates(*groups: List[Candidate]) -> List[Candidate]:
    by_ticker: Dict[str, Candidate] = {}
    for group in groups:
        for cand in group:
            if cand.ticker in by_ticker:
                by_ticker[cand.ticker] = by_ticker[cand.ticker].merge(cand)
            else:
                by_ticker[cand.ticker] = cand
    out = list(by_ticker.values())
    out.sort(key=lambda c: (c.priority, c.ticker))
    return out


def summarize_sources(candidates: List[Candidate]) -> Dict[str, int]:
    """Count how many tickers in the selected plan came from each source.

    A ticker counts once per source it was tagged with — open_position and
    alpha_top both count for AMZN if it appears on both lists.  Used by
    the CLI to log a `selection by source` breakdown so the operator can
    see why each ticker is in the plan and whether the cap is biting
    against an unexpected source.
    """
    counts: Dict[str, int] = {}
    for cand in candidates:
        seen_sources: set = set()
        for reason in cand.reasons:
            # Source key = prefix before ":" (e.g. "alpha_top:A:77.1" → "alpha_top").
            source = str(reason).split(":", 1)[0]
            seen_sources.add(source)
        for source in seen_sources:
            counts[source] = counts.get(source, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


@dataclass
class PlanResult:
    """Phase 2B.4: cap-aware plan.

    ``selected`` is the (possibly cap-trimmed) list the runner will
    process.  ``dropped`` carries the tail that the cap excluded so the
    operator can see what would have been refreshed at a higher cap.
    """
    selected: List[Candidate] = field(default_factory=list)
    dropped: List[Candidate] = field(default_factory=list)
    max_tickers: int = 0

    @property
    def cap_hit(self) -> bool:
        return len(self.dropped) > 0

    def dropped_by_source(self) -> Dict[str, int]:
        return summarize_sources(self.dropped)


def build_plan(
    *,
    max_tickers: int,
    earnings_days_ahead: int,
    explicit_watch: Iterable[str],
    db_path: Path = DB_PATH,
    cache_dir: Path = CACHE_DIR,
    skip_earnings: bool = False,
    alpha_cap: int = 20,
    missing_cap: int = 50,
) -> List[Candidate]:
    """Top-level selector — back-compat shim.

    Returns the cap-trimmed selected list (same as Phase 2B.3 callers
    expected).  New code should call :func:`build_plan_detailed` to also
    receive the dropped tail and the cap-hit signal.
    """
    return build_plan_detailed(
        max_tickers=max_tickers,
        earnings_days_ahead=earnings_days_ahead,
        explicit_watch=explicit_watch,
        db_path=db_path,
        cache_dir=cache_dir,
        skip_earnings=skip_earnings,
        alpha_cap=alpha_cap,
        missing_cap=missing_cap,
    ).selected


def build_plan_detailed(
    *,
    max_tickers: int,
    earnings_days_ahead: int,
    explicit_watch: Iterable[str],
    db_path: Path = DB_PATH,
    cache_dir: Path = CACHE_DIR,
    skip_earnings: bool = False,
    alpha_cap: int = 20,
    missing_cap: int = 50,
) -> PlanResult:
    """Phase 2B.4 cap-aware selector — returns selected + dropped.

    Pure (no side effects).  ``selected`` and ``dropped`` are sorted by
    (priority asc, ticker asc) so the highest-urgency candidates land
    in ``selected`` first; the cap-bound tail flows to ``dropped``.
    """
    groups: List[List[Candidate]] = []
    groups.append(select_open_positions(db_path))
    if not skip_earnings:
        known_universe = _load_known_universe(cache_dir, db_path)
        groups.append(select_earnings(earnings_days_ahead, known_universe=known_universe))
    groups.append(select_explicit(explicit_watch))
    groups.append(select_alpha_top(
        cache_dir / "alpha_discovery_board_latest.json", alpha_cap,
    ))
    groups.append(select_missing_or_stale(cache_dir, missing_cap))
    merged = merge_candidates(*groups)
    cap = max(0, int(max_tickers))
    selected = merged[:cap]
    dropped = merged[cap:]
    return PlanResult(selected=selected, dropped=dropped, max_tickers=cap)


# ── Runner ──────────────────────────────────────────────────────────────────

def _write_artifacts(result) -> Tuple[Path, Path]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    json_path = CACHE_DIR / f"executive_gatekeeper_{result.ticker}_latest.json"
    txt_path = LOG_DIR / f"executive_gatekeeper_{result.ticker}_latest.txt"
    json_path.write_text(json.dumps(result.to_dict(), indent=2, default=str))
    txt_path.write_text(result.llm_summary or "")
    return json_path, txt_path


def run_refresh(
    candidates: List[Candidate],
    *,
    dry_run: bool = False,
    with_llm_summary: bool = False,
) -> Dict[str, Dict[str, str]]:
    """Run the Gatekeeper for each candidate; collect outcomes."""
    results: Dict[str, Dict[str, str]] = {}
    if dry_run:
        for cand in candidates:
            results[cand.ticker] = {
                "status": "DRY_RUN",
                "reasons": ", ".join(cand.reasons),
                "priority": str(cand.priority),
            }
        return results

    # Late import — the gatekeeper itself imports heavier modules (FMP,
    # alpaca client) which require credentials.  Dry-run never imports.
    from core.executive_gatekeeper import run_executive_gatekeeper

    for cand in candidates:
        t0 = time.time()
        try:
            res = run_executive_gatekeeper(
                cand.ticker, with_llm_summary=with_llm_summary,
            )
            _write_artifacts(res)
            results[cand.ticker] = {
                "status":   "OK",
                "verdict":  res.final_status,
                "elapsed_s": f"{time.time() - t0:.2f}",
                "reasons":  ", ".join(cand.reasons),
            }
        except Exception as exc:
            logger.exception("gatekeeper failed for %s", cand.ticker)
            results[cand.ticker] = {
                "status":   "ERROR",
                "error":    f"{type(exc).__name__}: {exc}",
                "elapsed_s": f"{time.time() - t0:.2f}",
                "reasons":  ", ".join(cand.reasons),
            }
    return results


# ── CLI ─────────────────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Batched Executive Gatekeeper refresh "
                    "(open positions, alpha top, earnings, missing/stale).",
    )
    ap.add_argument("--max", type=int, default=25,
                    help="Cap on number of tickers to refresh (default 25).")
    ap.add_argument("--earnings-days", type=int, default=5,
                    help="Earnings horizon in days (default 5).")
    ap.add_argument("--alpha-cap", type=int, default=20,
                    help="Cap on Alpha-board candidates considered (default 20).")
    ap.add_argument("--missing-cap", type=int, default=50,
                    help="Cap on missing/stale tickers considered (default 50).")
    ap.add_argument("--watch", nargs="*", default=[],
                    help="Explicit tickers to add to the refresh plan.")
    ap.add_argument("--skip-earnings", action="store_true",
                    help="Skip the earnings-calendar source (offline/dry-run).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the plan; do not run the Gatekeeper.")
    ap.add_argument("--with-llm-summary", action="store_true",
                    help="Forward to run_executive_gatekeeper.")
    ap.add_argument("--json", action="store_true",
                    help="Emit machine-readable JSON summary.")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s %(message)s",
    )

    plan_result = build_plan_detailed(
        max_tickers=args.max,
        earnings_days_ahead=args.earnings_days,
        explicit_watch=args.watch,
        skip_earnings=args.skip_earnings,
        alpha_cap=args.alpha_cap,
        missing_cap=args.missing_cap,
        # Pass module-level paths explicitly so monkeypatched values in
        # tests take effect — otherwise Python captures the defaults at
        # function-definition time and ignores the override.
        db_path=DB_PATH,
        cache_dir=CACHE_DIR,
    )
    plan = plan_result.selected
    dropped = plan_result.dropped

    if not plan and not dropped:
        msg = "no tickers selected — nothing to refresh"
        print(msg, file=sys.stderr)
        if args.json:
            print(json.dumps({"plan": [], "results": {}, "note": msg}))
        return 0

    if args.dry_run:
        print(f"[DRY RUN] would refresh {len(plan)} ticker(s) "
              f"(cap={args.max}):", file=sys.stderr)
    else:
        print(f"refreshing {len(plan)} ticker(s) "
              f"(cap={args.max})…", file=sys.stderr)

    # Phase 2B.3: per-source selection breakdown so the operator can see
    # why each ticker is in the plan and whether the cap is biting on the
    # wrong source.  Cache-only — does not change selection logic.
    source_counts = summarize_sources(plan)
    if source_counts:
        breakdown = "  ".join(f"{k}={v}" for k, v in source_counts.items())
        print(f"  selection by source: {breakdown}", file=sys.stderr)

    for cand in plan:
        print(f"  · {cand.ticker:<8} pri={cand.priority:<3} "
              f"reasons={', '.join(cand.reasons)}", file=sys.stderr)

    # Phase 2B.4: cap-hit warning + dropped tail visibility.
    dropped_by_source = plan_result.dropped_by_source()
    if plan_result.cap_hit:
        breakdown = "  ".join(f"{k}={v}" for k, v in dropped_by_source.items())
        print(
            f"  WARNING: cap hit — {len(dropped)} candidate(s) not refreshed "
            f"(cap={args.max}). dropped by source: {breakdown}",
            file=sys.stderr,
        )
        for cand in dropped[:8]:
            print(
                f"    · DROPPED {cand.ticker:<8} pri={cand.priority:<3} "
                f"reasons={', '.join(cand.reasons)}",
                file=sys.stderr,
            )
        extra = len(dropped) - 8
        if extra > 0:
            print(f"    · DROPPED +{extra} more (see JSON sidecar)",
                  file=sys.stderr)

    results = run_refresh(
        plan,
        dry_run=args.dry_run,
        with_llm_summary=args.with_llm_summary,
    )

    print("", file=sys.stderr)
    ok_count = sum(1 for v in results.values() if v.get("status") == "OK")
    err_count = sum(1 for v in results.values() if v.get("status") == "ERROR")
    dry_count = sum(1 for v in results.values() if v.get("status") == "DRY_RUN")
    summary_line = (
        f"refresh summary: planned={len(plan)} ok={ok_count} "
        f"error={err_count} dry_run={dry_count} "
        f"dropped={len(dropped)} cap_hit={plan_result.cap_hit}"
    )
    print(summary_line, file=sys.stderr)

    # Phase 2B.4 — write a tiny summary sidecar so the provider-freshness
    # audit and the dashboard cap-hit warning can read structured state
    # without re-running the planner.  Cache-only.
    summary_payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "max":          args.max,
        "selected": [
            {"ticker": c.ticker, "priority": c.priority, "reasons": c.reasons}
            for c in plan
        ],
        "dropped": [
            {"ticker": c.ticker, "priority": c.priority, "reasons": c.reasons}
            for c in dropped
        ],
        "selected_by_source": source_counts,
        "dropped_by_source":  dropped_by_source,
        "cap_hit": plan_result.cap_hit,
        "summary": {
            "planned":  len(plan),
            "ok":       ok_count,
            "error":    err_count,
            "dry_run":  dry_count,
            "dropped":  len(dropped),
            "cap_hit":  plan_result.cap_hit,
        },
        "dry_run": bool(args.dry_run),
        "skip_earnings": bool(args.skip_earnings),
    }
    try:
        # Use the module-level CACHE_DIR via a fresh lookup so tests that
        # monkeypatch the constant land their sidecar in the tmp dir.
        sidecar_dir = sys.modules[__name__].CACHE_DIR
        sidecar_dir.mkdir(parents=True, exist_ok=True)
        sidecar_path = sidecar_dir / "gatekeeper_refresh_latest.json"
        tmp = sidecar_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(summary_payload, indent=2, default=str))
        tmp.replace(sidecar_path)
    except OSError as exc:
        logger.warning("could not write summary sidecar: %s", exc)

    if args.json:
        print(json.dumps({
            "plan": [{"ticker": c.ticker, "priority": c.priority,
                      "reasons": c.reasons} for c in plan],
            "dropped": [{"ticker": c.ticker, "priority": c.priority,
                         "reasons": c.reasons} for c in dropped],
            "results": results,
            "selection_by_source": source_counts,
            "dropped_by_source":   dropped_by_source,
            "cap_hit": plan_result.cap_hit,
            "summary": summary_payload["summary"],
        }, indent=2))
    return 0 if err_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

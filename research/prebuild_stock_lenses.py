#!/usr/bin/env python3
"""
research/prebuild_stock_lenses.py — nightly Stock Lens prebuild for an
auto-curated short list of important tickers.

Why this exists:
    The Stock Lens is the most informative per-ticker artifact in the
    research stack (Market regime / Sector / Tech / Entry / Alpha / Posture
    / Options / Social).  Running it on demand from the dashboard works for
    one-offs but leaves the named research candidates with no lens until a
    user manually triggers each one.  This script pre-builds lenses
    overnight for the names already surfaced by other research artifacts so
    Mode 2 has a populated lens panel for the names you most likely look at
    next session.

What it does:
    1. Assembles a candidate list from cache-only sources (with one optional
       Alpaca call for open positions; failure is non-fatal).
    2. Deduplicates, applies a hard cap (default 25 tickers).
    3. For each ticker:
         - if a fresh lens artifact (<24h) exists and --force is not set,
           SKIP and log it.
         - otherwise call research.stock_lens_runner.run() with a synthesized
           argparse Namespace, capturing exceptions per-ticker so one bad
           ticker does not abort the whole batch.
    4. Writes a summary to cache/research/lens_prebuild_latest.json and a
       human-readable log to logs/lens_prebuild_latest.txt.

Cost controls:
    --force         rebuild even when fresh
    --max=N         override the per-run ticker cap
    --dry-run       print the plan; do not call the runner
    --no-positions  skip the Alpaca positions source (purely cache-only)
    --skip-source S skip a candidate source (alpha|posture|structural|social|positions)

This script does NOT change strategy / sleeve / paper / governance /
execution logic.  It only invokes the existing stock_lens_runner.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# ── env load before core imports ──────────────────────────────────────────────
_CRED = os.environ.get("SNIPER_ENV_PATH")
if _CRED and Path(_CRED).exists():
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv(_CRED, override=True)
    except ImportError:
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logger = logging.getLogger("prebuild_stock_lenses")

# Soft import of cfg for cache/log dirs.
try:
    import core.config as cfg  # type: ignore
    CACHE_DIR = Path(cfg.CACHE_DIR) if hasattr(cfg, "CACHE_DIR") else (ROOT / "cache")
    LOG_DIR   = Path(cfg.LOG_DIR)   if hasattr(cfg, "LOG_DIR")   else (ROOT / "logs")
except Exception:
    CACHE_DIR = ROOT / "cache"
    LOG_DIR   = ROOT / "logs"

RESEARCH_DIR = CACHE_DIR / "research"

DEFAULT_CAP    = 25
FRESH_LENS_S   = 24 * 3600   # match the dashboard's _STOCK_LENS_FRESH_S
SUMMARY_JSON   = RESEARCH_DIR / "lens_prebuild_latest.json"
SUMMARY_TXT    = LOG_DIR     / "lens_prebuild_latest.txt"

VALID_SOURCES = {"alpha", "posture", "structural", "social", "positions", "liquid", "reference"}

# Default size for the liquid coverage tier when --liquid-top is set without
# a value.  Picked to give a useful coverage band without blowing the FMP
# budget when combined with the curated sources.
DEFAULT_LIQUID_TOP = 100

# Always-relevant macro reference tickers.  These are index/sector ETFs the
# operator looks at to anchor every research session and don't appear in
# strategy_candidates (which is stock-only) — so without an explicit seed
# they never enter any auto-prebuild cycle.  Keeping the list short avoids
# burning FMP budget on names that aren't actually referenced.
REFERENCE_TICKERS: List[str] = ["SPY", "QQQ", "IWM", "DIA"]


# ══════════════════════════════════════════════════════════════════════════════
# CANDIDATE COLLECTION — each source returns a list of (ticker, reason) tuples
# ══════════════════════════════════════════════════════════════════════════════

def _read_json(p: Path) -> Dict[str, Any]:
    try:
        if not p.exists():
            return {}
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("read failed for %s: %s", p, exc)
        return {}


def _candidates_alpha() -> List[Dict[str, str]]:
    """Tier A + Tier B from the Alpha Discovery board.  Overlay (premarket)
    is preferred when it exists and is fresher than the nightly board, but
    we union both to catch names that drop off the overlay but were on the
    nightly."""
    out: List[Dict[str, str]] = []
    seen: Set[str] = set()
    for path, label in (
        (RESEARCH_DIR / "alpha_discovery_overlay_latest.json", "alpha-overlay"),
        (RESEARCH_DIR / "alpha_discovery_board_latest.json",   "alpha-nightly"),
    ):
        art = _read_json(path)
        for it in (art.get("items") or []):
            sym = str(it.get("ticker") or "").upper()
            tier = str(it.get("data_tier") or "")
            if not sym or sym in seen:
                continue
            if tier in {"A", "B"}:
                seen.add(sym)
                out.append({"ticker": sym, "reason": f"{label} tier {tier}"})
    return out


def _candidates_structural() -> List[Dict[str, str]]:
    """READY_NOW + WATCH from the universe snapshot."""
    snap = _read_json(CACHE_DIR / "universe_snapshot.json")
    out: List[Dict[str, str]] = []
    seen: Set[str] = set()
    for c in (snap.get("strategy_candidates") or []):
        sym = str(c.get("symbol") or "").upper()
        rdns = str(c.get("readiness") or "")
        if not sym or sym in seen:
            continue
        if rdns in {"READY_NOW", "WATCH"}:
            seen.add(sym)
            out.append({"ticker": sym,
                        "reason": f"structural {rdns} ({c.get('strategy','—')})"})
    return out


def _candidates_posture() -> List[Dict[str, str]]:
    """Market Posture focus names — derived via research_assist_bte.  Cache-
    only inputs (universe snapshot)."""
    snap = _read_json(CACHE_DIR / "universe_snapshot.json")
    try:
        from core.research_assist_bte import build_research_bte  # type: ignore
        out_obj = build_research_bte(universe_snapshot=snap or {}, regime=None, vix=None)
        names: List[Dict[str, str]] = []
        seen: Set[str] = set()
        for r in (out_obj.focus_names or [])[:8]:
            sym = str(r.get("symbol") or "").upper()
            if not sym or sym in seen:
                continue
            seen.add(sym)
            names.append({"ticker": sym,
                          "reason": f"posture-focus {r.get('compliance_tag','')}"})
        return names
    except Exception as exc:
        logger.info("posture candidates unavailable: %s", exc)
        return []


def _candidates_social() -> List[Dict[str, str]]:
    """High-quality Social Arb leads (Cross-Confirmed Lead / Options-Tape /
    confidence=high)."""
    art = _read_json(RESEARCH_DIR / "social_arb_latest.json")
    out: List[Dict[str, str]] = []
    seen: Set[str] = set()
    for it in (art.get("items") or []):
        sym = str(it.get("ticker") or "").upper()
        if not sym or sym in seen:
            continue
        bucket = str(it.get("bucket") or "")
        conf = str(it.get("confidence") or "").lower()
        if bucket in {"Cross-Confirmed Lead", "Options/Tape Confirmed"} or conf == "high":
            seen.add(sym)
            out.append({"ticker": sym, "reason": f"social-arb {bucket or 'high-conf'}"})
    return out


def _candidates_liquid(top_n: int) -> List[Dict[str, str]]:
    """Top-N most liquid US stocks by 20d average dollar volume.

    Reads cache/universe/universe_snapshot_latest.json (already maintained
    by the universe builder) and ranks ``strategy_candidates`` by
    ``avg_dollar_volume_20`` descending.  Cache-only — no provider calls.

    Used as a *coverage* tier alongside the existing signal-driven sources
    (alpha / posture / structural / social / positions) so highly-traded
    names that aren't currently flagged still get a fresh lens artifact.
    """
    if top_n <= 0:
        return []
    snap_path = CACHE_DIR / "universe" / "universe_snapshot_latest.json"
    snap = _read_json(snap_path)
    if not snap:
        # Fall back to legacy path if the universe builder ever writes flat.
        legacy = CACHE_DIR / "universe_snapshot.json"
        snap = _read_json(legacy)
    cands = snap.get("strategy_candidates") or []
    # Dedupe by symbol while keeping the row with the largest ADV (some
    # symbols appear once per strategy).
    best: Dict[str, float] = {}
    for c in cands:
        sym = str(c.get("symbol") or "").upper()
        if not sym:
            continue
        adv = c.get("avg_dollar_volume_20") or 0
        try:
            adv_f = float(adv)
        except (TypeError, ValueError):
            continue
        if adv_f <= 0:
            continue
        if adv_f > best.get(sym, 0.0):
            best[sym] = adv_f
    ranked = sorted(best.items(), key=lambda kv: kv[1], reverse=True)
    out: List[Dict[str, str]] = []
    for sym, adv in ranked[: int(top_n)]:
        out.append({
            "ticker": sym,
            "reason": f"liquid top — adv20 ${adv/1e9:.2f}B" if adv >= 1e9
                       else f"liquid top — adv20 ${adv/1e6:.0f}M",
        })
    return out


def _candidates_reference() -> List[Dict[str, str]]:
    """Macro reference ETFs (SPY/QQQ/IWM/DIA).  Always relevant for any
    research session; cache-only seed (no provider call here — the
    per-ticker run happens in stock_lens_runner)."""
    return [
        {"ticker": sym, "reason": "macro reference ETF"}
        for sym in REFERENCE_TICKERS
    ]


def _candidates_positions() -> List[Dict[str, str]]:
    """Open positions via Alpaca.  Best-effort: returns empty list on any
    error (network unreachable, credentials missing, …)."""
    try:
        from core.alpaca_client import get_alpaca  # type: ignore
        client = get_alpaca()
        pos = client.get_positions() or []
        out: List[Dict[str, str]] = []
        seen: Set[str] = set()
        for p in pos:
            sym = str(p.get("ticker") or "").upper()
            if not sym or sym in seen:
                continue
            seen.add(sym)
            out.append({"ticker": sym, "reason": "open position"})
        return out
    except Exception as exc:
        logger.info("positions unavailable: %s", exc)
        return []


def assemble_candidates(
    skip_sources: Set[str],
    no_positions: bool,
    liquid_top: int = 0,
) -> List[Dict[str, str]]:
    """Walk each source in priority order; first reason for a ticker wins.

    Open positions come first, then the small macro-reference ETF seed
    (SPY/QQQ/IWM/DIA) so those always get a fresh lens regardless of what
    surfaces from the signal-driven sources.  Signal-driven sources
    (alpha / posture / structural / social) run next, preserving their
    existing precedence in the union.  Liquid coverage is appended last so
    it only adds names that were not already surfaced — this preserves the
    curated default behaviour when ``liquid_top == 0``.
    """
    sources = []
    if not no_positions and "positions" not in skip_sources:
        sources.append(("positions",  _candidates_positions))
    if "reference" not in skip_sources: sources.append(("reference", _candidates_reference))
    if "alpha"      not in skip_sources: sources.append(("alpha",      _candidates_alpha))
    if "posture"    not in skip_sources: sources.append(("posture",    _candidates_posture))
    if "structural" not in skip_sources: sources.append(("structural", _candidates_structural))
    if "social"     not in skip_sources: sources.append(("social",     _candidates_social))
    if liquid_top > 0 and "liquid" not in skip_sources:
        sources.append(("liquid",     lambda: _candidates_liquid(liquid_top)))

    seen: Set[str] = set()
    out: List[Dict[str, str]] = []
    for source_name, fn in sources:
        try:
            rows = fn() or []
        except Exception as exc:
            logger.warning("source %s failed: %s", source_name, exc)
            rows = []
        for r in rows:
            sym = (r.get("ticker") or "").upper()
            if not sym or sym in seen:
                continue
            seen.add(sym)
            out.append({"ticker": sym, "source": source_name, "reason": r.get("reason", "")})
    return out


# ══════════════════════════════════════════════════════════════════════════════
# FRESHNESS + RUN
# ══════════════════════════════════════════════════════════════════════════════

def _lens_artifact_path(ticker: str) -> Path:
    return RESEARCH_DIR / f"stock_lens_{ticker.upper()}_latest.json"


def _is_fresh(ticker: str, fresh_s: int = FRESH_LENS_S) -> bool:
    p = _lens_artifact_path(ticker)
    if not p.exists():
        return False
    age = time.time() - p.stat().st_mtime
    return age < fresh_s


def _run_one(ticker: str) -> Dict[str, Any]:
    """Invoke the runner for a single ticker.  Returns a per-ticker result
    dict so one failure cannot abort the batch."""
    started_at = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")
    try:
        ns = argparse.Namespace(
            mode="daily",
            ticker=ticker,
            horizon=20,
            refresh=False,
            offline=False,
            cache_only=False,
            no_fmp=False,
            no_snapshot=False,
            stale_hours=24.0,
            print_json=False,
        )
        from research import stock_lens_runner  # type: ignore
        rc = stock_lens_runner.run(ns)
        ok = (rc == 0)
        # Lightweight follow-up: confirm the artifact landed; if not, mark partial.
        artifact_present = _lens_artifact_path(ticker).exists()
        return {
            "ticker": ticker,
            "status": "built" if (ok and artifact_present) else ("partial" if ok else "failed"),
            "rc":     rc,
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds"),
        }
    except Exception as exc:
        return {
            "ticker": ticker,
            "status": "failed",
            "rc":     None,
            "error":  str(exc)[:240],
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds"),
        }


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Nightly Stock Lens prebuild for important tickers.")
    parser.add_argument("--max", type=int, default=DEFAULT_CAP,
                        help=f"max tickers per run (default {DEFAULT_CAP})")
    parser.add_argument("--force", action="store_true",
                        help="rebuild even when a fresh (<24h) lens already exists")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the plan only; do not call the runner")
    parser.add_argument("--no-positions", action="store_true",
                        help="skip the Alpaca-positions source (purely cache-only)")
    parser.add_argument("--skip-source", action="append", default=[],
                        help=f"skip a candidate source ({'|'.join(sorted(VALID_SOURCES))})")
    parser.add_argument("--quiet", action="store_true",
                        help="suppress per-ticker stdout chatter")
    parser.add_argument("--fresh-hours", type=float, default=24.0,
                        help="freshness window in hours (default 24)")
    parser.add_argument("--liquid-top", type=int, nargs="?", const=DEFAULT_LIQUID_TOP,
                        default=0,
                        help=("include the top-N most liquid US stocks (by 20d "
                              "avg dollar volume from the universe snapshot) "
                              "as a coverage tier alongside curated sources. "
                              f"bare flag uses N={DEFAULT_LIQUID_TOP}; 0 disables. "
                              "Pair with a matching --max to actually build them."))
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    )

    skip: Set[str] = set()
    for s in (args.skip_source or []):
        s_lower = s.strip().lower()
        if s_lower not in VALID_SOURCES:
            logger.warning("ignoring unknown --skip-source %r", s)
            continue
        skip.add(s_lower)

    fresh_s = int(args.fresh_hours * 3600)

    candidates = assemble_candidates(skip, no_positions=args.no_positions,
                                     liquid_top=int(args.liquid_top or 0))
    if not candidates:
        logger.warning("no candidates assembled — nothing to prebuild")
    capped = candidates[: max(0, int(args.max))]
    over_cap = candidates[max(0, int(args.max)):]

    plan: List[Dict[str, Any]] = []
    for c in capped:
        sym = c["ticker"]
        fresh = _is_fresh(sym, fresh_s=fresh_s)
        plan.append({
            "ticker": sym,
            "source": c.get("source"),
            "reason": c.get("reason"),
            "fresh":  fresh,
            "action": "skip-fresh" if (fresh and not args.force) else "build",
        })

    started_at = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")
    results: List[Dict[str, Any]] = []
    if args.dry_run:
        logger.info("DRY-RUN — %d candidates (%d capped, %d over cap)",
                    len(candidates), len(capped), len(over_cap))
        for row in plan:
            logger.info("  %-7s %-12s %s — %s", row["action"], row["source"] or "—",
                        row["ticker"], row["reason"] or "")
    else:
        for row in plan:
            sym = row["ticker"]
            if row["action"] == "skip-fresh":
                results.append({"ticker": sym, "status": "skipped-fresh",
                                "source": row["source"], "reason": row["reason"]})
                if not args.quiet:
                    logger.info("skip-fresh  %s  (%s)", sym, row["reason"])
                continue
            if not args.quiet:
                logger.info("build       %s  (%s)", sym, row["reason"])
            r = _run_one(sym)
            r["source"] = row["source"]
            r["reason"] = row["reason"]
            results.append(r)

    finished_at = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")
    summary = {
        "built_at":    finished_at,
        "started_at":  started_at,
        "dry_run":     bool(args.dry_run),
        "force":       bool(args.force),
        "fresh_hours": float(args.fresh_hours),
        "cap":         int(args.max),
        "liquid_top":  int(args.liquid_top or 0),
        "candidates_assembled": len(candidates),
        "over_cap":    [c["ticker"] for c in over_cap],
        "skipped_sources": sorted(skip),
        "no_positions": bool(args.no_positions),
        "plan":        plan,
        "results":     results,
        "counts": {
            "built":         sum(1 for r in results if r.get("status") == "built"),
            "partial":       sum(1 for r in results if r.get("status") == "partial"),
            "failed":        sum(1 for r in results if r.get("status") == "failed"),
            "skipped_fresh": sum(1 for r in results if r.get("status") == "skipped-fresh"),
        },
        "research_only": True,
    }

    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    SUMMARY_JSON.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")

    text = _render_summary_text(summary)
    SUMMARY_TXT.write_text(text, encoding="utf-8")

    print(f"lens_prebuild: {summary['counts']}  → {SUMMARY_JSON}")
    if not args.quiet:
        print(text)
    return 0


def _render_summary_text(summary: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("=" * 72)
    lines.append("STOCK LENS PREBUILD — research-only · nightly batch")
    lines.append("=" * 72)
    lines.append(f"started_at:  {summary.get('started_at','—')}")
    lines.append(f"built_at:    {summary.get('built_at','—')}")
    lines.append(f"dry_run:     {summary.get('dry_run')}")
    lines.append(f"cap:         {summary.get('cap')}    "
                 f"force={summary.get('force')}  "
                 f"fresh_hours={summary.get('fresh_hours')}  "
                 f"liquid_top={summary.get('liquid_top', 0)}")
    lines.append(f"candidates:  {summary.get('candidates_assembled')}  "
                 f"(over cap: {len(summary.get('over_cap') or [])})")
    if summary.get("skipped_sources"):
        lines.append(f"skipped:     sources={','.join(summary['skipped_sources'])}")
    counts = summary.get("counts") or {}
    lines.append(f"counts:      built={counts.get('built',0)}  "
                 f"partial={counts.get('partial',0)}  "
                 f"failed={counts.get('failed',0)}  "
                 f"skipped-fresh={counts.get('skipped_fresh',0)}")
    lines.append("")
    lines.append("-- PLAN --")
    for row in (summary.get("plan") or []):
        lines.append(f"  {row.get('action','—'):<11} {row.get('source','—'):<11} "
                     f"{row.get('ticker','—'):<6}  {row.get('reason','') or ''}")
    if summary.get("results"):
        lines.append("")
        lines.append("-- RESULTS --")
        for r in summary["results"]:
            extra = ""
            if r.get("error"):
                extra = f"  err={r['error']}"
            lines.append(f"  {r.get('status','—'):<13} {r.get('ticker','—'):<6}"
                         f"  rc={r.get('rc','—')}{extra}")
    if summary.get("over_cap"):
        lines.append("")
        lines.append(f"-- OVER-CAP (not built this run): {', '.join(summary['over_cap'][:30])}")
    lines.append("")
    lines.append("research-only · this batch does not approve any trade.")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())

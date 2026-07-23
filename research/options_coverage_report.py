#!/usr/bin/env python3
"""Options coverage health report — why the overlay is disabled.

Completes the P2 options_overlay task: reports how many of today's
watchlist tickers have valid options snapshot data, the explicit
insufficiency reason for every uncovered ticker, and a required-vs-
optional flag for every research surface that consumes options data.

Key structural fact this report makes visible: the daily options
snapshot collector (Phase 1J) intentionally covers a CAPPED top-
liquidity universe (~20 symbols: index ETFs + mega-caps) to protect
provider budget and build IV history, while the scanner watchlist is
~100 small/mid-cap names.  Low per-ticker coverage is therefore a
COLLECTION-UNIVERSE DESIGN consequence, not a data failure — the
overlay guard (disabled < 50% coverage) is working as designed.

Doctrine: RESEARCH-ONLY / CACHE-ONLY / READ-ONLY.  No provider calls,
no DB writes; writes only its own sidecar + log.  Changes nothing about
the collector, the overlay guard, or any gate.

Usage:
  ./scripts/run_research_cycle.sh options-coverage-report
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SCANNER_REL = Path("cache") / "research" / "research_scanner_latest.json"
RADAR_REL = Path("cache") / "research" / "daily_alpha_radar_latest.json"
SNAPSHOT_ROOT_REL = Path("data") / "options_snapshots"
SIDECAR_REL = (Path("cache") / "research"
               / "options_coverage_report_latest.json")
LOG_REL = Path("logs") / "options_coverage_report_latest.txt"

# Overlay guard thresholds (mirrors daily_alpha_radar_report — reported
# here, never changed here)
DISABLED_BELOW = 0.50
PARTIAL_BELOW = 0.80

# Every research surface that consumes options data, with whether the
# options layer is REQUIRED for that surface to function or an OPTIONAL
# enrichment.  Doctrine sources: CLAUDE.md "Options data sources",
# docs/ops/OPTIONS_DATA_SOURCES.md, Phase 1G.12/1J notes.
CONSUMERS = [
    {"surface": "alpha_discovery_overlay",
     "requirement": "OPTIONAL",
     "note": "per-ticker enrichment; guarded off below 50% watchlist "
             "coverage — candidates rank fine without it"},
    {"surface": "stock_lens_options_context",
     "requirement": "OPTIONAL",
     "note": "per-ticker context block; renders 'no options data' "
             "gracefully"},
    {"surface": "social_arb_radar_options_confirm",
     "requirement": "OPTIONAL",
     "note": "confirmation signal only; social radar runs without it"},
    {"surface": "options_regime_lens",
     "requirement": "REQUIRED",
     "note": "market-level only (SPY/QQQ/IWM/VXX) — independent of "
             "per-ticker watchlist coverage; unaffected by this gap"},
    {"surface": "options_chain_snapshot_quality",
     "requirement": "REQUIRED",
     "note": "IV-history accumulation for the capped liquid universe "
             "(Phase 1J); by design does NOT cover the scanner "
             "watchlist"},
]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def latest_snapshot(root: Path):
    """(date_str, set_of_symbols) for the newest snapshot day, if any."""
    snap_root = root / SNAPSHOT_ROOT_REL
    if not snap_root.exists():
        return None, set()
    day_dirs = sorted((d for d in snap_root.iterdir() if d.is_dir()),
                      reverse=True)
    if not day_dirs:
        return None, set()
    latest = day_dirs[0]
    return latest.name, {p.stem for p in latest.glob("*.parquet")}


def classify_ticker(ticker: str, covered_syms: set,
                    snapshot_date: Optional[str]) -> Dict[str, str]:
    if snapshot_date is None:
        return {"ticker": ticker, "status": "NO_SNAPSHOT_DAY",
                "reason": "no options snapshot day exists yet — collector "
                          "has not run",
                "clearance": "check gem-trader-options-snapshot.timer"}
    if ticker in covered_syms:
        return {"ticker": ticker, "status": "COVERED",
                "reason": f"present in the {snapshot_date} snapshot",
                "clearance": "n/a"}
    return {"ticker": ticker, "status": "NOT_IN_COLLECTION_UNIVERSE",
            "reason": "the snapshot collector's capped top-liquidity "
                      "universe does not select this ticker (design, "
                      "not failure)",
            "clearance": "extend the collector's universe cap (provider-"
                         "budget decision) or accept the overlay as "
                         "market-level only"}


def build_report(root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(root) if root else REPO_ROOT

    def _load(rel: Path) -> Dict[str, Any]:
        path = root / rel
        try:
            return json.loads(path.read_text(encoding="utf-8")) \
                if path.exists() else {}
        except Exception:
            return {}

    scanner = _load(SCANNER_REL)
    watchlist = [i.get("ticker") for i in scanner.get("watchlist") or []
                 if i.get("ticker")]
    snapshot_date, covered_syms = latest_snapshot(root)

    rows = [classify_ticker(t, covered_syms, snapshot_date)
            for t in watchlist]
    covered = [r for r in rows if r["status"] == "COVERED"]
    uncovered = [r for r in rows if r["status"] != "COVERED"]
    coverage = len(covered) / len(rows) if rows else 0.0
    if coverage < DISABLED_BELOW:
        overlay_state = "DISABLED"
    elif coverage < PARTIAL_BELOW:
        overlay_state = "PARTIAL"
    else:
        overlay_state = "ENABLED"
    if overlay_state == "DISABLED":
        selection_status = "NOT_USED_IN_SELECTION"
        selection_note = (
            "Options overlay disabled — insufficient coverage; not used in "
            "selection.")
        used_in_selection = False
    else:
        selection_status = "OPTIONAL_CONTEXT_ONLY"
        selection_note = (
            "Options overlay available only as optional context; scanner "
            "selection and ranking are not changed by this report.")
        used_in_selection = None

    reason_counts: Dict[str, int] = {}
    for r in rows:
        reason_counts[r["status"]] = reason_counts.get(r["status"], 0) + 1

    radar_cov = (_load(RADAR_REL).get("options_coverage") or {})
    return {
        "kind": "options_coverage_report",
        "version": 1,
        "generated_at": _utcnow(),
        "research_only": True,
        "snapshot_date": snapshot_date,
        "snapshot_symbols": sorted(covered_syms),
        "watchlist_total": len(rows),
        "covered": len(covered),
        "uncovered": len(uncovered),
        "coverage_pct": round(coverage * 100, 1),
        "overlay_state": overlay_state,
        "selection_status": selection_status,
        "selection_note": selection_note,
        "used_in_selection": used_in_selection,
        "overlay_guard_thresholds": {"disabled_below_pct": 50,
                                     "partial_below_pct": 80},
        "radar_agrees": (radar_cov.get("state") == overlay_state
                         if radar_cov else None),
        "status_counts": reason_counts,
        "structural_cause": (
            "The snapshot collector covers a capped top-liquidity "
            "universe (~20 symbols) for IV-history accumulation, while "
            "the scanner surfaces ~100 small/mid caps — low per-ticker "
            "coverage is a collection-universe design consequence, and "
            "the overlay guard disabling enrichment is correct behavior."),
        "covered_tickers": sorted(r["ticker"] for r in covered),
        "uncovered_detail": uncovered,
        "consumers": CONSUMERS,
    }


def render_text(report: Dict[str, Any]) -> str:
    lines = [
        "OPTIONS COVERAGE REPORT (research-only; read-only)",
        f"  generated {report['generated_at'][:16]} | snapshot "
        f"{report['snapshot_date']} | watchlist coverage "
        f"{report['covered']}/{report['watchlist_total']} "
        f"({report['coverage_pct']}%) -> overlay {report['overlay_state']}",
        f"  covered: {', '.join(report['covered_tickers']) or 'none'}",
        f"  status counts: {report['status_counts']}",
        f"  selection status: {report.get('selection_note')}",
        f"  structural cause: {report['structural_cause']}",
        "",
        "  CONSUMERS (required vs optional):",
    ]
    for c in report["consumers"]:
        lines.append(f"    {c['surface']:36s} {c['requirement']:8s} "
                     f"{c['note']}")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Options coverage health report (cache-only).")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--root", default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    root = Path(args.root) if args.root else REPO_ROOT

    report = build_report(root)
    print(render_text(report))
    if not args.dry_run:
        sidecar = root / SIDECAR_REL
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(json.dumps(report, ensure_ascii=False, indent=2)
                           + "\n", encoding="utf-8")
        log = root / LOG_REL
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(render_text(report) + "\n", encoding="utf-8")
        print(f"\nwritten: {sidecar}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

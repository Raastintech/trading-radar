#!/usr/bin/env python3
"""Quarantine cause report — explicit reason + clearance per ticker.

Completes the P1 data-quality task: every DATA_QUARANTINE ticker on the
alpha radar gets an explicit cause and a concrete clearance condition,
joined from the price cache (deep preferred) and the latest targeted
backfill results.  Cache-only, cred-free, read-only on everything —
writes only its own sidecar + log.

Causes:
  REPAIRED          — bar depth now >= floor; clears on the next nightly
                      scanner re-evaluation.
  YOUNG_LISTING     — backfill fetched everything the provider has and
                      the history is still short; clears as bars accrue.
  BACKFILL_PENDING  — short history, no exhaustive backfill attempt yet.
  STALE_FEED        — last bar is old; provider coverage / delisting
                      needs verification before trusting any depth fix.

Usage:
  ./scripts/run_research_cycle.sh quarantine-report
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

RADAR_REL = Path("cache") / "research" / "daily_alpha_radar_latest.json"
BACKFILL_REL = (Path("cache") / "research"
                / "targeted_price_backfill_latest.json")
SIDECAR_REL = (Path("cache") / "research"
               / "quarantine_cause_report_latest.json")
LOG_REL = Path("logs") / "quarantine_cause_report_latest.txt"

BAR_FLOOR = 300          # scanner depth floor (matches targeted backfill)
STALE_DAYS = 5           # last bar older than this ⇒ stale feed


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def classify(ticker: str, bars: Optional[int], last_bar: Optional[str],
             backfill: Optional[Dict[str, Any]],
             today: Optional[str] = None) -> Dict[str, Any]:
    """Pure classification: reason + clearance for one quarantined name."""
    today_d = datetime.strptime(today or _utcnow()[:10], "%Y-%m-%d").date()
    stale = False
    if last_bar:
        try:
            age = (today_d
                   - datetime.strptime(last_bar[:10], "%Y-%m-%d").date()).days
            stale = age > STALE_DAYS
        except Exception:
            stale = False

    rec: Dict[str, Any] = {"ticker": ticker, "bars": bars,
                           "last_bar": last_bar,
                           "backfill_action": (backfill or {}).get("action")}
    if bars is None:
        rec["cause"] = "STALE_FEED"
        rec["reason"] = "no usable price parquet in shallow or deep cache"
        rec["clearance"] = ("verify the ticker is real/listed, then run "
                            "targeted-backfill --execute")
        return rec
    if stale:
        rec["cause"] = "STALE_FEED"
        rec["reason"] = (f"last bar {last_bar} is stale (> {STALE_DAYS} "
                         "days old) — possible delisting or provider gap")
        rec["clearance"] = ("verify provider coverage / delisting; depth "
                            "fixes are meaningless while the feed is stale")
        return rec
    if bars >= BAR_FLOOR:
        rec["cause"] = "REPAIRED"
        rec["reason"] = (f"history repaired — {bars} bars available "
                         f"(floor {BAR_FLOOR})")
        rec["clearance"] = ("clears automatically on the next nightly "
                            "scanner re-evaluation")
        return rec
    # "backfilled" with no depth gain proves exhaustion; so does the
    # backfiller's own "skip_source_exhausted" verdict (it remembers a
    # prior exhaustive fetch and skips the provider call entirely).
    exhausted = bool(backfill and (
        backfill.get("action") == "skip_source_exhausted"
        or (backfill.get("action") == "backfilled"
            and (backfill.get("after") or 0) <= (bars or 0))))
    missing = BAR_FLOOR - bars
    eta = today_d + timedelta(days=round(missing * 7 / 5))
    if exhausted:
        rec["cause"] = "YOUNG_LISTING"
        rec["reason"] = (f"young listing / short trading history — the "
                         f"provider has only {bars} bars (backfill "
                         "exhausted the source)")
        rec["clearance"] = (f"auto-clears as bars accrue: ~{missing} "
                            f"trading days to the {BAR_FLOOR}-bar floor "
                            f"(≈ {eta.isoformat()})")
    else:
        rec["cause"] = "BACKFILL_PENDING"
        rec["reason"] = (f"insufficient history ({bars} bars < "
                         f"{BAR_FLOOR}) — no exhaustive backfill attempt "
                         "recorded")
        rec["clearance"] = ("run ./scripts/run_research_cycle.sh "
                            "targeted-backfill --execute (provider "
                            "budget permitting)")
    return rec


def _bars_and_last(root: Path, ticker: str):
    """Depth + last bar date, preferring the deep cache."""
    import pandas as pd
    best_bars, best_last = None, None
    for sub in ("prices_deep", "prices"):
        path = root / "cache" / sub / f"{ticker}.parquet"
        if not path.exists():
            continue
        try:
            df = pd.read_parquet(path)
        except Exception:
            continue
        if df is None or df.empty:
            continue
        last = str(df.index.max())[:10]
        if best_last is None or last > best_last \
                or (last == best_last and len(df) > (best_bars or 0)):
            best_bars, best_last = len(df), last
    return best_bars, best_last


def build_report(root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(root) if root else REPO_ROOT

    def _load(rel: Path) -> Dict[str, Any]:
        path = root / rel
        try:
            return json.loads(path.read_text(encoding="utf-8")) \
                if path.exists() else {}
        except Exception:
            return {}

    radar = _load(RADAR_REL)
    quarantined: List[str] = (radar.get("priority_tickers")
                              or {}).get("DATA_QUARANTINE") or []
    backfill_doc = _load(BACKFILL_REL)
    backfill_by_ticker: Dict[str, Dict[str, Any]] = {}
    for entry in (backfill_doc.get("plan") or backfill_doc.get("entries")
                  or backfill_doc.get("results") or []):
        if isinstance(entry, dict) and entry.get("ticker"):
            backfill_by_ticker[entry["ticker"]] = entry

    rows = []
    for ticker in quarantined:
        bars, last_bar = _bars_and_last(root, ticker)
        rows.append(classify(ticker, bars, last_bar,
                             backfill_by_ticker.get(ticker)))

    causes: Dict[str, int] = {}
    for r in rows:
        causes[r["cause"]] = causes.get(r["cause"], 0) + 1
    return {
        "kind": "quarantine_cause_report",
        "version": 1,
        "generated_at": _utcnow(),
        "research_only": True,
        "radar_generated_at": radar.get("generated_at"),
        "bar_floor": BAR_FLOOR,
        "n_quarantined": len(quarantined),
        "cause_counts": causes,
        "tickers": rows,
    }


def render_text(report: Dict[str, Any]) -> str:
    lines = ["QUARANTINE CAUSE REPORT (research-only; read-only)",
             f"  generated {report['generated_at'][:16]} | quarantined "
             f"{report['n_quarantined']} | causes {report['cause_counts']}"]
    for r in report["tickers"]:
        lines += [
            f"  {r['ticker']:6s} [{r['cause']}] bars={r['bars']} "
            f"last={r['last_bar']}",
            f"         reason: {r['reason']}",
            f"         clears: {r['clearance']}",
        ]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Per-ticker quarantine cause + clearance report "
                    "(cache-only).")
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

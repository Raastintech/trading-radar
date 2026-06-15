"""
research/funnel_historizer.py — Phase 1G.5 funnel artifact historizer.

RESEARCH-ONLY. CACHE-ONLY. The Scanner Truth Review found the funnel is only
PARTIALLY historized: ``research/research_delta.py`` already snapshots the
Alpha board + overlay + market context into ``cache/research/history/
snapshot_*.json`` on its premarket/nightly cadence (since ~2026-05-20). What
is NOT historized is the per-ticker downstream — Stock Lens and Executive
Gatekeeper are written ``*_latest.json`` only, so we cannot tell when a given
name first got a lens/gatekeeper read. This script closes that gap: it copies
the per-ticker lens + gatekeeper (and, for a self-contained dated bundle, the
board/overlay) into dated read-only snapshots so FUTURE autopsies can compute
recall-before-move and forward-precision per ticker. It does not change how
the live system writes its latest artifacts, makes no provider calls, and
never imports execution / governance.

It cannot help the PAST winners in the current audit — only future ones. Run
it on a daily cadence (operator or a future timer) to accrue history.

Snapshot layout:
  cache/research/history/<YYYY-MM-DD>/alpha_discovery_board.json
  cache/research/history/<YYYY-MM-DD>/alpha_discovery_overlay.json
  cache/research/history/<YYYY-MM-DD>/alpha_discovery_enrichment.json
  cache/research/history/<YYYY-MM-DD>/stock_lens/<TICKER>.json
  cache/research/history/<YYYY-MM-DD>/gatekeeper/<TICKER>.json
  cache/research/history/<YYYY-MM-DD>/manifest.json

Idempotent: re-running for the same date overwrites that date's snapshot only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

REPO = Path(__file__).resolve().parents[1]
RESEARCH = REPO / "cache" / "research"
HISTORY = RESEARCH / "history"

BOARD_FILES = {
    "alpha_discovery_board.json": "alpha_discovery_board_latest.json",
    "alpha_discovery_overlay.json": "alpha_discovery_overlay_latest.json",
    "alpha_discovery_enrichment.json": "alpha_discovery_enrichment_latest.json",
}


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def _snapshot_date() -> str:
    """Prefer the Alpha board's own built_at date so the snapshot is tagged by
    the data's as-of, not merely the clock. Falls back to today (UTC)."""
    bp = RESEARCH / "alpha_discovery_board_latest.json"
    if bp.exists():
        try:
            ba = json.loads(bp.read_text()).get("built_at")
            if ba:
                return ba[:10]
        except Exception:
            pass
    return datetime.now(timezone.utc).date().isoformat()


def snapshot(date: str | None = None) -> Dict:
    date = date or _snapshot_date()
    out = HISTORY / date
    (out / "stock_lens").mkdir(parents=True, exist_ok=True)
    (out / "gatekeeper").mkdir(parents=True, exist_ok=True)

    manifest: Dict = {
        "snapshot_date": date,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source": "cache/research/*_latest.json (read-only copy)",
        "files": {}, "stock_lens_count": 0, "gatekeeper_count": 0,
    }

    for dst, src in BOARD_FILES.items():
        sp = RESEARCH / src
        if sp.exists():
            shutil.copy2(sp, out / dst)
            manifest["files"][dst] = {"sha16": _sha(sp), "bytes": sp.stat().st_size}
        else:
            manifest["files"][dst] = {"status": "missing"}

    lens = 0
    for sp in RESEARCH.glob("stock_lens_*_latest.json"):
        ticker = sp.name[len("stock_lens_"):-len("_latest.json")]
        shutil.copy2(sp, out / "stock_lens" / f"{ticker}.json")
        lens += 1
    gk = 0
    for sp in RESEARCH.glob("executive_gatekeeper_*_latest.json"):
        ticker = sp.name[len("executive_gatekeeper_"):-len("_latest.json")]
        shutil.copy2(sp, out / "gatekeeper" / f"{ticker}.json")
        gk += 1
    manifest["stock_lens_count"] = lens
    manifest["gatekeeper_count"] = gk
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def list_snapshots() -> List[str]:
    if not HISTORY.exists():
        return []
    return sorted(p.name for p in HISTORY.iterdir() if p.is_dir())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="override snapshot date (default: board built_at)")
    ap.add_argument("--list", action="store_true", help="list existing snapshots")
    args = ap.parse_args()
    if args.list:
        for d in list_snapshots():
            print(d)
        return 0
    m = snapshot(args.date)
    print(f"funnel snapshot {m['snapshot_date']}: "
          f"{sum(1 for f in m['files'].values() if 'sha16' in f)} board files, "
          f"{m['stock_lens_count']} lens, {m['gatekeeper_count']} gatekeeper "
          f"→ cache/research/history/{m['snapshot_date']}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

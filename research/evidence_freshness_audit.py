"""
research/evidence_freshness_audit.py — operator audit/debug for the Mode-3
Evidence Freshness panel.

CACHE-ONLY / READ-ONLY.  For every field the dashboard's EVIDENCE FRESHNESS
panel shows, this resolves the *exact* current source artifact, its age /
generated_at, whether it exists, its status, and the dashboard field that reads
it — so a stale/unknown field can be diagnosed without guessing.

No providers, no DB writes (a single read-only SELECT for the legacy daemon
scan timestamp), no governance / execution / universe / gate side effects.

Outputs:
  cache/research/evidence_freshness_mapping_audit_latest.json
  logs/evidence_freshness_mapping_audit_latest.txt
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.evidence_freshness import (artifact_meta, fmt_age_short,
                                     price_cache_bar_status,
                                     universe_artifact_meta)

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "cache"
RESEARCH = CACHE / "research"
LOGS = REPO / "logs"
DB = REPO / "db" / "trading.db"

OUT_JSON = RESEARCH / "evidence_freshness_mapping_audit_latest.json"
OUT_TXT = LOGS / "evidence_freshness_mapping_audit_latest.txt"


def _legacy_scan_meta() -> Dict[str, Any]:
    """Read-only: last daemon scan cycle timestamp from scan_results.  This is
    the 'legacy scanner' field — the daemon scan loop, distinct from the research
    pipeline.  Read-only SELECT (immutable connection); never writes."""
    # Mirror the dashboard: last_cycle_ts is derived from MAX(ts) in scan_results.
    src = "db/trading.db:scan_results.MAX(ts)"
    if not DB.exists():
        return {"field": "legacy scanner", "exists": False, "source": src,
                "status": "missing", "reason": "trading.db not found"}
    try:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        try:
            row = con.execute("SELECT MAX(ts) FROM scan_results").fetchone()
        finally:
            con.close()
    except Exception as e:
        return {"field": "legacy scanner", "exists": True, "source": src,
                "status": "unknown", "reason": f"query error: {e.__class__.__name__}"}
    ts = row[0] if row else None
    if not ts:
        return {"field": "legacy scanner", "exists": True, "source": src,
                "status": "missing", "reason": "no scan_results rows with last_cycle_ts"}
    from core.evidence_freshness import _age_seconds  # local helper
    age = _age_seconds(ts)
    stale = age is not None and age > 36 * 3600
    return {"field": "legacy scanner", "exists": True, "source": src,
            "generated_at": ts, "age_seconds": age, "age": fmt_age_short(age),
            "status": "stale" if stale else "current",
            "legacy": True,
            "reason": ("daemon scan loop has not recorded a cycle recently — this is "
                       "the legacy production scanner, NOT the research pipeline"
                       if stale else None)}


def _json_field_meta(path: Path, field: str, dashboard: str,
                     value_key: str) -> Dict[str, Any]:
    m = artifact_meta(path)
    out = {"field": field, "dashboard_field": dashboard,
           "source": str(path.relative_to(REPO)) + f":{value_key}",
           "exists": m["exists"], "age_seconds": m["age_seconds"],
           "age": fmt_age_short(m["age_seconds"]) if m["exists"] else "N/A"}
    if not m["exists"]:
        out["status"] = "missing"
        out["reason"] = f"expected artifact not found: {path.relative_to(REPO)}"
        return out
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        out["value"] = d.get(value_key)
        out["status"] = "present" if d.get(value_key) is not None else "no_value"
        if d.get(value_key) is None:
            out["reason"] = f"key '{value_key}' absent/null in artifact"
    except Exception as e:
        out["status"] = "unknown"
        out["reason"] = f"unreadable: {e.__class__.__name__}"
    return out


def build() -> Dict[str, Any]:
    gen = datetime.now(timezone.utc).isoformat()
    fields: List[Dict[str, Any]] = []

    # 1. daily bars (price cache)
    pc = price_cache_bar_status(CACHE / "prices", CACHE / "prices_deep", benchmark="SPY")
    pc["dashboard_field"] = "daily bars"
    pc["age"] = None
    fields.append(pc)

    # 2. universe
    um = universe_artifact_meta(CACHE / "universe" / "universe_snapshot_latest.json")
    um["dashboard_field"] = "universe"
    um["age"] = fmt_age_short(um.get("age_seconds"))
    fields.append(um)

    # 3-6 research artifacts
    def _art(field, dash, rel):
        p = REPO / rel
        m = artifact_meta(p)
        return {"field": field, "dashboard_field": dash, "source": rel,
                "exists": m["exists"], "age_seconds": m["age_seconds"],
                "generated_at": m["generated_at"],
                "age": fmt_age_short(m["age_seconds"]) if m["exists"] else "N/A",
                "status": "present" if m["exists"] else "missing",
                "reason": None if m["exists"] else f"expected artifact not found: {rel}"}

    fields.append(_art("alpha board", "alpha board / premarket",
                       "cache/research/alpha_discovery_board_latest.json"))
    fields.append(_art("market forecast", "premarket",
                       "cache/research/regime_forecast_latest.json"))
    fields.append(_art("scanner truth", "scanner truth",
                       "cache/research/scanner_truth_summary_latest.json"))
    fields.append(_art("recall shadow", "recall shadow",
                       "cache/research/recall_repair_shadow_forward_latest.json"))

    # 7. legacy scanner (daemon scan loop)
    fields.append({**_legacy_scan_meta(), "dashboard_field": "legacy scanner"})

    # 8. resolver success / 10. paper loop (paper_evidence_status.json)
    pe = LOGS / "paper_evidence_status.json"
    fields.append(_json_field_meta(pe, "resolver success", "resolver", "last_success_at"))
    fields.append(_json_field_meta(pe, "paper loop", "paper loop", "ok"))

    # 9. scoreboard refresh (mtime of the txt)
    sb = LOGS / "paper_scoreboard_latest.txt"
    sm = artifact_meta(sb)
    fields.append({"field": "scoreboard", "dashboard_field": "scoreboard",
                   "source": "logs/paper_scoreboard_latest.txt",
                   "exists": sm["exists"], "age_seconds": sm["age_seconds"],
                   "age": fmt_age_short(sm["age_seconds"]) if sm["exists"] else "N/A",
                   "status": "present" if sm["exists"] else "missing",
                   "reason": None if sm["exists"] else "scoreboard txt not found"})

    return {
        "kind": "evidence_freshness_mapping_audit",
        "generated_at": gen,
        "research_only": True,
        "disclaimer": ("cache-only / read-only diagnostic of the Mode-3 Evidence "
                       "Freshness panel sources · no providers, no DB writes, no "
                       "execution/governance/universe/gate side effects"),
        "fields": fields,
    }


def _render_txt(res: Dict[str, Any]) -> List[str]:
    L = [f"== EVIDENCE FRESHNESS MAPPING AUDIT — {res['generated_at']} ==",
         res["disclaimer"], "",
         f"{'field':<16}{'status':<10}{'age':<9}{'source':<52}{'reason'}"]
    for f in res["fields"]:
        L.append(
            f"{str(f.get('field')):<16}{str(f.get('status')):<10}"
            f"{str(f.get('age') or fmt_age_short(f.get('age_seconds'))):<9}"
            f"{str(f.get('source'))[:51]:<52}{str(f.get('reason') or '')}")
    return L


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Evidence Freshness mapping audit (cache-only)")
    ap.add_argument("--print", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="print only; write no files")
    args = ap.parse_args(argv)
    res = build()
    lines = _render_txt(res)
    if args.dry_run:
        print("\n".join(lines))
        return 0
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(res, indent=2, default=str))
    OUT_TXT.parent.mkdir(parents=True, exist_ok=True)
    OUT_TXT.write_text("\n".join(lines) + "\n")
    if args.print or True:
        print("\n".join(lines))
    print(f"\nwrote {OUT_JSON.relative_to(REPO)} · {OUT_TXT.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

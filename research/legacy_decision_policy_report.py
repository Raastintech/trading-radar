"""
research/legacy_decision_policy_report.py — Phase 1G.2 T5

Read-only operator policy report for ``decisions`` rows that are marked
``position_opened=1`` but missing ``fill_price`` or ``fill_qty``. These
rows pre-date the Phase 0 fill telemetry and pollute the full-ledger
verdict in ``paper_state_hygiene_latest.json`` (``ready_to_gate_all``).

This script DOES NOT:
  - mutate the ``decisions`` table
  - delete legacy rows
  - backfill fill values from any source
  - touch ``paper_signals``, ``paper_signal_outcomes``, ``voyager_paper_signals``,
    or ``veto_log``
  - call providers
  - change governance or execution

It produces a recommendation per row plus a single header recommendation
the operator can apply with a separate, explicitly-approved tool. The
default policy is "keep quarantined" — same as the existing legacy
quarantine sidecar at ``data/state/paper_legacy_quarantine.json``.

Outputs:
  cache/research/legacy_decision_policy_latest.json
  logs/legacy_decision_policy_latest.txt
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_env_path = os.environ.get("SNIPER_ENV_PATH", "")
if _env_path:
    try:
        from dotenv import load_dotenv as _load
        _load(Path(_env_path), override=True)
    except ImportError:
        pass
os.environ.setdefault("GEM_TRADER_SKIP_DOTENV", "true")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import core.config as cfg  # noqa: E402
from core.paper_evidence_epoch import CLEAN_PAPER_EVIDENCE_START  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("legacy_decision_policy")


# ── Constants ────────────────────────────────────────────────────────────────

DEFAULT_OUT_JSON = cfg.CACHE_DIR / "research" / "legacy_decision_policy_latest.json"
DEFAULT_OUT_TEXT = cfg.LOG_DIR / "legacy_decision_policy_latest.txt"


# ── Data loaders ─────────────────────────────────────────────────────────────


def _load_legacy_rows(db_path: Path) -> List[Dict[str, Any]]:
    if not db_path.exists():
        return []
    with sqlite3.connect(str(db_path)) as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(decisions)").fetchall()]
        rows = conn.execute(
            "SELECT * FROM decisions "
            "WHERE position_opened=1 "
            "  AND (fill_price IS NULL OR fill_qty IS NULL OR fill_qty <= 0)"
        ).fetchall()
    return [dict(zip(cols, r)) for r in rows]


def _load_broker_snapshot() -> Optional[Dict[str, Any]]:
    path = Path("cache/state/broker_positions_snapshot.json")
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _broker_tickers(snapshot: Optional[Dict[str, Any]]) -> set[str]:
    if not snapshot:
        return set()
    out: set[str] = set()
    for p in snapshot.get("positions") or []:
        t = str(p.get("ticker") or "").upper()
        if t:
            out.add(t)
    return out


# ── Policy logic ─────────────────────────────────────────────────────────────


def _parse_clean_epoch_start() -> datetime:
    s = str(CLEAN_PAPER_EVIDENCE_START).replace("Z", "+00:00")
    return datetime.fromisoformat(s)


def _classify_row(
    row: Dict[str, Any],
    *,
    broker_tickers: set[str],
    clean_epoch_start: datetime,
) -> Dict[str, Any]:
    ts_raw = str(row.get("ts") or "")
    try:
        ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
    except Exception:
        ts = None

    pre_clean_epoch: Optional[bool]
    if ts is None:
        pre_clean_epoch = None
    else:
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        pre_clean_epoch = ts < clean_epoch_start

    ticker = str(row.get("ticker") or "").upper()
    broker_match: str
    if not broker_tickers and ticker:
        broker_match = "unavailable_no_cached_snapshot"
    elif ticker in broker_tickers:
        broker_match = "match"
    else:
        broker_match = "closed_by_book"

    closed = bool(row.get("position_closed"))
    has_fill_price = row.get("fill_price") is not None
    has_fill_qty = row.get("fill_qty") not in (None, 0)

    # Default policy: keep quarantined.
    recommendation = "keep_quarantined"
    rationale_parts: List[str] = []
    if pre_clean_epoch is True:
        rationale_parts.append("logged before clean-epoch start")
    elif pre_clean_epoch is False:
        rationale_parts.append("INSIDE clean epoch — investigate as a hygiene incident")
        recommendation = "investigate"
    if not closed:
        rationale_parts.append("row marked OPEN — reconcile before any annotation")
        recommendation = "investigate"
    if broker_match == "match":
        rationale_parts.append("broker still holds this ticker — DO NOT archive")
        recommendation = "investigate"
    if broker_match == "closed_by_book" and closed and pre_clean_epoch:
        rationale_parts.append(
            "closed-by-book and pre-clean-epoch — safe to quarantine"
        )
    if not rationale_parts:
        rationale_parts.append("default: insufficient evidence to act")

    return {
        "id": row.get("id"),
        "ts": ts_raw,
        "strategy": row.get("strategy"),
        "ticker": ticker,
        "direction": row.get("direction"),
        "entry_price": row.get("entry_price"),
        "fill_price": row.get("fill_price"),
        "fill_qty": row.get("fill_qty"),
        "fill_status": row.get("fill_status"),
        "position_opened": bool(row.get("position_opened")),
        "position_closed": closed,
        "has_fill_price": has_fill_price,
        "has_fill_qty": has_fill_qty,
        "pre_clean_epoch": pre_clean_epoch,
        "broker_position_match": broker_match,
        "recommendation": recommendation,
        "rationale": "; ".join(rationale_parts),
    }


def build_report(db_path: Path) -> Dict[str, Any]:
    rows = _load_legacy_rows(db_path)
    broker_snap = _load_broker_snapshot()
    broker_tk = _broker_tickers(broker_snap)
    epoch_start = _parse_clean_epoch_start()

    classified = [
        _classify_row(r, broker_tickers=broker_tk, clean_epoch_start=epoch_start)
        for r in rows
    ]

    counts = {
        "total": len(classified),
        "pre_clean_epoch": sum(1 for r in classified if r["pre_clean_epoch"] is True),
        "inside_clean_epoch": sum(1 for r in classified if r["pre_clean_epoch"] is False),
        "closed": sum(1 for r in classified if r["position_closed"]),
        "broker_closed_by_book": sum(1 for r in classified if r["broker_position_match"] == "closed_by_book"),
        "broker_match": sum(1 for r in classified if r["broker_position_match"] == "match"),
    }

    safe_to_quarantine = [r for r in classified if r["recommendation"] == "keep_quarantined"]
    needs_investigation = [r for r in classified if r["recommendation"] == "investigate"]

    if not classified:
        header = "no legacy decisions missing fill data — full-ledger hygiene already clean"
    elif not needs_investigation and counts["pre_clean_epoch"] == counts["total"]:
        header = (
            "all legacy rows are pre-clean-epoch, closed, and confirmed closed-by-book — "
            "default policy: keep quarantined; NO DB mutation without separate operator approval"
        )
    else:
        header = (
            f"{len(needs_investigation)} row(s) need investigation before any annotation; "
            f"the remaining {len(safe_to_quarantine)} are safe to keep quarantined"
        )

    return {
        "version": "LEGACY_DECISION_POLICY_V1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "db_path": str(db_path),
        "clean_epoch_start": CLEAN_PAPER_EVIDENCE_START,
        "broker_snapshot_present": broker_snap is not None,
        "counts": counts,
        "header_recommendation": header,
        "rows": classified,
        "policy": {
            "default": "keep_quarantined",
            "mutation_policy": (
                "this report NEVER mutates the decisions table; archival annotation "
                "(e.g. setting suspect_state) requires a separate, explicitly-approved tool"
            ),
            "scope": (
                "diagnostic-only; the active hygiene gate is `ready_to_gate_clean` from "
                "paper_state_hygiene_latest.json which is computed from the clean-epoch "
                "view and is already true while these legacy rows remain quarantined"
            ),
        },
    }


# ── Rendering ────────────────────────────────────────────────────────────────


def render_text(report: Dict[str, Any]) -> str:
    bar = "─" * 78
    out: List[str] = [bar, "LEGACY DECISION POLICY REPORT — read-only", bar]
    out.append(f"generated_at        {report['generated_at']}")
    out.append(f"db_path             {report['db_path']}")
    out.append(f"clean_epoch_start   {report['clean_epoch_start']}")
    out.append(f"broker_snapshot     {'available' if report['broker_snapshot_present'] else 'unavailable_no_cached_snapshot'}")
    out.append("")
    c = report["counts"]
    out.append(f"total_legacy_rows   {c['total']}")
    out.append(f"  pre_clean_epoch       {c['pre_clean_epoch']}")
    out.append(f"  inside_clean_epoch    {c['inside_clean_epoch']}")
    out.append(f"  position_closed       {c['closed']}")
    out.append(f"  broker_closed_by_book {c['broker_closed_by_book']}")
    out.append(f"  broker_match          {c['broker_match']}")
    out.append("")
    out.append(f"HEADER  {report['header_recommendation']}")
    out.append("")
    if report["rows"]:
        out.append("Per-row:")
        for r in report["rows"]:
            out.append(
                f"  {(r['id'] or '')[:8]} {str(r['ts'])[:19]} "
                f"{r['strategy']:8s} {r['ticker']:6s} {r['direction']:5s} "
                f"entry={r['entry_price']} pre_clean={r['pre_clean_epoch']} "
                f"broker={r['broker_position_match']} closed={r['position_closed']} "
                f"-> {r['recommendation']}"
            )
            out.append(f"    rationale: {r['rationale']}")
    out.append("")
    out.append("POLICY")
    out.append(f"  default: {report['policy']['default']}")
    out.append(f"  mutation_policy: {report['policy']['mutation_policy']}")
    out.append(f"  scope: {report['policy']['scope']}")
    out.append(bar)
    return "\n".join(out) + "\n"


# ── Writers ──────────────────────────────────────────────────────────────────


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", dir=str(path.parent), delete=False, suffix=".tmp", encoding="utf-8",
    ) as tmp:
        tmp.write(content)
        tmp.flush()
        os.fsync(tmp.fileno())
    os.replace(tmp.name, path)


# ── CLI ──────────────────────────────────────────────────────────────────────


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Phase 1G.2 T5 — read-only legacy decision policy report."
    )
    parser.add_argument("--db-path", default=None, help="Override path to trading.db")
    parser.add_argument("--out-json", default=str(DEFAULT_OUT_JSON))
    parser.add_argument("--out-text", default=str(DEFAULT_OUT_TEXT))
    parser.add_argument("--json", action="store_true", help="Print JSON to stdout instead of text")
    args = parser.parse_args(argv)

    db_path = Path(args.db_path) if args.db_path else Path(cfg.DB_PATH)
    if not db_path.exists():
        logger.error("db not found at %s", db_path)
        return 1

    report = build_report(db_path)
    text = render_text(report)

    _atomic_write(Path(args.out_json), json.dumps(report, indent=2, default=str) + "\n")
    _atomic_write(Path(args.out_text), text)

    if args.json:
        sys.stdout.write(json.dumps(report, indent=2, default=str) + "\n")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())

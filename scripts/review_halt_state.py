"""
scripts/review_halt_state.py — Phase 1G.1 operator halt-state review tool.

Read-only by default. Cross-checks the persisted circuit-breaker state
against the live broker snapshot, the open-decisions ledger, and the
paper-state hygiene sidecar, then prints a verdict on whether the halt
is still load-bearing.

Why it exists:
  After Phase 1A landed the reconcile-drift halt, a class of "stuck halt"
  appeared: a hard drift trips the breaker, the underlying drift later
  resolves (broker covers, operator closes, or position monitor catches
  up), but the persisted ``circuit_breaker_state.halted=1`` row stays
  set. The system sits flat while the world has moved on.

  This script is the safe operator workflow:
      .venv/bin/python scripts/review_halt_state.py
      → prints a verdict, never mutates anything
      .venv/bin/python scripts/review_halt_state.py --clear-reviewed \\
          --reason "operator-reviewed: CRK drift resolved (broker flat, reconciler OK)"
      → clears the breaker via CircuitBreakers.clear_halt with a typed
        reason, but ONLY if every safety precondition is met.

Safety rules (enforced):
  - Default mode is dry-run / read-only. No DB writes.
  - ``--clear-reviewed`` requires ALL of:
      • current broker/book reconciler match (no active drift)
      • fresh broker snapshot (age <= --max-snapshot-age-hours)
      • paper-state hygiene ``ready_to_gate_clean=true``
      • an explicit ``--reason`` string
  - Never raises a halt. Never modifies decisions / paper_signals / veto_log.
  - Live-capital gate is irrelevant here — this is a paper-or-live-safe
    operator console for an existing breaker; the broker is only read.

Exit codes:
  0   review complete (whether eligible to clear or not)
  1   environment / config error (DB missing, modules un-importable)
  2   ``--clear-reviewed`` requested but preconditions not met
  3   ``--clear-reviewed`` requested and clear failed at write time
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_env_path = os.environ.get("SNIPER_ENV_PATH", "")
if _env_path:
    try:
        from dotenv import load_dotenv as _load
        _load(Path(_env_path), override=True)
    except ImportError:
        pass
# Tooling that runs without provider creds (the common dry-run case)
# must not blow up on import — the script only needs the DB.
os.environ.setdefault("GEM_TRADER_SKIP_DOTENV", "true")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import core.config as cfg  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("review_halt_state")


# ── Configuration ──────────────────────────────────────────────────────────

DEFAULT_MAX_SNAPSHOT_AGE_HOURS = 1.0
DEFAULT_RECONCILER_LOOKBACK_MIN = 30


# ── Data loaders ───────────────────────────────────────────────────────────


def _load_circuit_breaker_state(db_path: Path) -> Optional[Dict[str, Any]]:
    if not db_path.exists():
        return None
    try:
        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute(
                "SELECT id, halted, reason, tripped_at, cleared_at, cleared_by "
                "FROM circuit_breaker_state WHERE id=1"
            ).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    return {
        "id":         row[0],
        "halted":     bool(row[1]),
        "reason":     row[2] or "",
        "tripped_at": row[3],
        "cleared_at": row[4],
        "cleared_by": row[5],
    }


def _load_broker_snapshot() -> Tuple[Optional[Dict[str, Any]], Optional[float]]:
    """Return (snapshot dict, age_hours) or (None, None) if missing."""
    path = Path("cache/state/broker_positions_snapshot.json")
    if not path.exists():
        return None, None
    try:
        snap = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None, None
    generated_at = snap.get("generated_at")
    age_hours: Optional[float] = None
    if generated_at:
        try:
            ts = datetime.fromisoformat(str(generated_at).replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age_hours = (now - ts).total_seconds() / 3600.0
        except Exception:
            pass
    return snap, age_hours


def _load_open_decisions(db_path: Path) -> List[Dict[str, Any]]:
    if not db_path.exists():
        return []
    try:
        with sqlite3.connect(str(db_path)) as conn:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(decisions)").fetchall()]
            if not cols:
                return []
            rows = conn.execute(
                "SELECT * FROM decisions "
                "WHERE position_opened=1 AND position_closed=0"
            ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(zip(cols, r)) for r in rows]


def _load_recent_reconciler_rows(
    db_path: Path, lookback_min: int = DEFAULT_RECONCILER_LOOKBACK_MIN,
) -> List[Dict[str, Any]]:
    """Pull the last reconciler verdict rows from veto_log.

    ``RECONCILE_DRIFT`` rows mean drift was observed at that timestamp.
    The reconciler does not emit a "RECONCILE_OK" row, but the absence
    of fresh drift rows + a fresh broker snapshot is the positive signal
    we rely on here.
    """
    if not db_path.exists():
        return []
    cutoff = datetime.now(timezone.utc).timestamp() - lookback_min * 60.0
    try:
        with sqlite3.connect(str(db_path)) as conn:
            rows = conn.execute(
                "SELECT ts, ticker, strategy, verdict, agent, reason "
                "FROM veto_log "
                "WHERE agent='position_reconciler' "
                "  AND verdict='RECONCILE_DRIFT' "
                "ORDER BY ts DESC LIMIT 200"
            ).fetchall()
    except sqlite3.OperationalError:
        return []
    out: List[Dict[str, Any]] = []
    for ts, ticker, strategy, verdict, agent, reason in rows:
        out.append({
            "ts":       ts,
            "ticker":   ticker,
            "strategy": strategy,
            "verdict":  verdict,
            "agent":    agent,
            "reason":   reason,
            "_recent":  _ts_after_cutoff(ts, cutoff),
        })
    return out


def _ts_after_cutoff(ts_iso: Optional[str], cutoff_epoch: float) -> bool:
    if not ts_iso:
        return False
    try:
        ts = datetime.fromisoformat(str(ts_iso).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.timestamp() >= cutoff_epoch
    except Exception:
        return False


def _load_paper_hygiene() -> Optional[Dict[str, Any]]:
    path = cfg.CACHE_DIR / "research" / "paper_state_hygiene_latest.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


# ── Reconciliation logic ───────────────────────────────────────────────────


def _broker_book_match(
    snapshot: Optional[Dict[str, Any]],
    open_decisions: List[Dict[str, Any]],
    qty_tolerance: float = 0.5,
) -> Dict[str, Any]:
    """Quick offline reconcile — does broker == book right now?"""
    if snapshot is None:
        return {
            "match":               False,
            "reason":              "no broker snapshot available",
            "broker_count":        0,
            "book_count":          len(open_decisions),
            "broker_only_tickers": [],
            "book_only_tickers":   [t.get("ticker") for t in open_decisions if t.get("ticker")],
            "qty_mismatch":        [],
        }
    broker = {}
    for p in snapshot.get("positions") or []:
        t = str(p.get("ticker") or "").upper()
        if t:
            try:
                broker[t] = float(p.get("qty") or 0.0)
            except (TypeError, ValueError):
                broker[t] = 0.0
    book: Dict[str, float] = {}
    for d in open_decisions:
        t = str(d.get("ticker") or "").upper()
        if not t:
            continue
        qty = d.get("fill_qty")
        try:
            q = float(qty) if qty is not None else None
        except (TypeError, ValueError):
            q = None
        if q is None or q <= 0:
            try:
                q = float(d.get("shares") or 0.0)
            except (TypeError, ValueError):
                q = 0.0
        book[t] = book.get(t, 0.0) + q

    broker_only = [t for t in broker if t not in book]
    book_only   = [t for t in book if t not in broker]
    qty_mismatch: List[Dict[str, Any]] = []
    for t in set(broker) & set(book):
        diff = abs(abs(broker[t]) - abs(book[t]))
        if diff > qty_tolerance:
            qty_mismatch.append({
                "ticker":     t,
                "broker_qty": broker[t],
                "book_qty":   book[t],
                "diff":       diff,
            })
    match = not broker_only and not book_only and not qty_mismatch
    return {
        "match":               match,
        "reason":              "" if match else
                               (f"broker_only={len(broker_only)} "
                                f"book_only={len(book_only)} "
                                f"qty_mismatch={len(qty_mismatch)}"),
        "broker_count":        len(broker),
        "book_count":          len(book),
        "broker_only_tickers": broker_only,
        "book_only_tickers":   book_only,
        "qty_mismatch":        qty_mismatch,
    }


def _classify_drift(
    reconciler_rows: List[Dict[str, Any]],
    bb: Dict[str, Any],
) -> Dict[str, Any]:
    """Active drift = recent drift rows AND broker/book mismatch.
    Historical drift = drift rows older than the recent window, with
    broker/book currently matching."""
    recent_drift = [r for r in reconciler_rows if r.get("_recent")]
    older_drift  = [r for r in reconciler_rows if not r.get("_recent")]
    active = bool(recent_drift) and not bb["match"]
    historical = bool(older_drift) and bb["match"]
    return {
        "active_drift":     active,
        "historical_drift": historical,
        "recent_count":     len(recent_drift),
        "older_count":      len(older_drift),
        "first_recent_ts":  recent_drift[-1]["ts"] if recent_drift else None,
        "last_recent_ts":   recent_drift[0]["ts"] if recent_drift else None,
        "last_drift_ts":    reconciler_rows[0]["ts"] if reconciler_rows else None,
    }


def _is_clear_eligible(
    cb: Optional[Dict[str, Any]],
    bb: Dict[str, Any],
    snap_age_hours: Optional[float],
    drift: Dict[str, Any],
    hygiene: Optional[Dict[str, Any]],
    max_snap_age_hours: float,
) -> Tuple[bool, List[str]]:
    """Return (eligible, blockers).  All checks must pass."""
    blockers: List[str] = []
    if cb is None or not cb.get("halted"):
        return False, ["not halted — nothing to clear"]
    if not bb["match"]:
        blockers.append(
            f"broker and book do not match: {bb['reason']}"
        )
    if snap_age_hours is None:
        blockers.append("no broker snapshot available — run scripts/snapshot_broker_positions.py first")
    elif snap_age_hours > max_snap_age_hours:
        blockers.append(
            f"broker snapshot is {snap_age_hours:.2f}h old "
            f"(max {max_snap_age_hours:.2f}h) — refresh it"
        )
    if drift["active_drift"]:
        blockers.append(
            f"active drift detected: {drift['recent_count']} "
            f"recent reconciler drift row(s)"
        )
    if hygiene is None:
        blockers.append("paper-state hygiene sidecar missing — run risk-telemetry first")
    else:
        h_summary = hygiene.get("summary") or (hygiene.get("hygiene") or {}).get("summary") or {}
        ready_clean = h_summary.get("ready_to_gate_clean")
        if not ready_clean:
            blockers.append(
                "paper-state hygiene ready_to_gate_clean is not true"
            )
    return (not blockers), blockers


def _recommendation(
    cb: Optional[Dict[str, Any]],
    eligible: bool,
    blockers: List[str],
    drift: Dict[str, Any],
    script_path: str = "scripts/review_halt_state.py",
    max_snap_age_hours: float = DEFAULT_MAX_SNAPSHOT_AGE_HOURS,
) -> str:
    """Operator-facing one-liner. When eligible, embeds the exact clear
    command so the operator can copy/paste with a typed reason instead
    of having to reconstruct flags. The text deliberately fits on one
    terminal line."""
    if cb is None:
        return "circuit_breaker_state row missing — bootstrap the table or check DB path"
    if not cb.get("halted"):
        return "no action: system is not halted"
    if eligible:
        return (
            "eligible to clear — run:\n"
            f"    .venv/bin/python {script_path} --clear-reviewed "
            f"--max-snapshot-age-hours {max_snap_age_hours} "
            "--reason \"operator-reviewed: <one-line explanation>\""
        )
    if drift["active_drift"]:
        return "do NOT clear — investigate the active drift first"
    return "do NOT clear — fix blockers, then re-run review"


def build_review_report(
    *,
    db_path: Path,
    max_snap_age_hours: float = DEFAULT_MAX_SNAPSHOT_AGE_HOURS,
    reconciler_lookback_min: int = DEFAULT_RECONCILER_LOOKBACK_MIN,
) -> Dict[str, Any]:
    cb = _load_circuit_breaker_state(db_path)
    snapshot, snap_age = _load_broker_snapshot()
    open_decs = _load_open_decisions(db_path)
    reconc_rows = _load_recent_reconciler_rows(db_path, reconciler_lookback_min)
    hygiene = _load_paper_hygiene()
    bb = _broker_book_match(snapshot, open_decs)
    drift = _classify_drift(reconc_rows, bb)
    eligible, blockers = _is_clear_eligible(
        cb, bb, snap_age, drift, hygiene, max_snap_age_hours
    )
    recommendation = _recommendation(
        cb, eligible, blockers, drift,
        max_snap_age_hours=max_snap_age_hours,
    )
    hygiene_summary: Optional[Dict[str, Any]] = None
    if hygiene is not None:
        # The raw sidecar puts the summary at the root; the MCP wrapper
        # nests it under "hygiene". Accept either shape.
        h = hygiene.get("summary") or (hygiene.get("hygiene") or {}).get("summary") or {}
        hygiene_summary = {
            "ready_to_gate_clean": h.get("ready_to_gate_clean"),
            "ready_to_gate_all":   h.get("ready_to_gate_all"),
            "errors_full":         (h.get("full_ledger") or {}).get("errors"),
            "errors_clean":        (h.get("clean_epoch")  or {}).get("errors"),
        }
    return {
        "generated_at":              datetime.now(timezone.utc).isoformat(),
        "db_path":                   str(db_path),
        "halted":                    bool(cb.get("halted")) if cb else False,
        "halt_reason":               cb.get("reason") if cb else None,
        "tripped_at":                cb.get("tripped_at") if cb else None,
        "cleared_at":                cb.get("cleared_at") if cb else None,
        "cleared_by":                cb.get("cleared_by") if cb else None,
        "broker_book_match":         bb,
        "broker_snapshot_age_hours": snap_age,
        "active_drift":              drift["active_drift"],
        "historical_drift":          drift["historical_drift"],
        "drift_summary":             drift,
        "hygiene_summary":           hygiene_summary,
        "clear_eligible":            eligible,
        "clear_blockers":            blockers,
        "recommendation":            recommendation,
        "limits": {
            "max_snapshot_age_hours":    max_snap_age_hours,
            "reconciler_lookback_min":   reconciler_lookback_min,
        },
    }


# ── Rendering ──────────────────────────────────────────────────────────────


def render_text(report: Dict[str, Any]) -> str:
    bar = "─" * 78
    lines: List[str] = [bar, "HALT-STATE REVIEW — read-only", bar]
    lines.append(f"generated_at        {report['generated_at']}")
    lines.append(f"db_path             {report['db_path']}")
    lines.append("")
    lines.append(f"halted              {report['halted']}")
    lines.append(f"halt_reason         {report['halt_reason'] or '—'}")
    lines.append(f"tripped_at          {report['tripped_at'] or '—'}")
    lines.append(f"last cleared_at     {report['cleared_at'] or '—'}")
    lines.append(f"last cleared_by     {report['cleared_by'] or '—'}")
    lines.append("")
    bb = report["broker_book_match"]
    lines.append(f"broker/book match   {bb['match']}  "
                 f"(broker={bb['broker_count']} book={bb['book_count']})")
    if bb["broker_only_tickers"]:
        lines.append(f"  broker_only       {', '.join(bb['broker_only_tickers'])}")
    if bb["book_only_tickers"]:
        lines.append(f"  book_only         {', '.join(bb['book_only_tickers'])}")
    if bb["qty_mismatch"]:
        lines.append(f"  qty_mismatch      {len(bb['qty_mismatch'])} ticker(s)")
    age = report["broker_snapshot_age_hours"]
    lines.append(f"snapshot age        {age:.2f}h" if age is not None else "snapshot age        n/a")
    lines.append("")
    d = report["drift_summary"]
    lines.append(f"active_drift        {report['active_drift']}  "
                 f"(recent={d['recent_count']} in last "
                 f"{report['limits']['reconciler_lookback_min']}m)")
    lines.append(f"historical_drift    {report['historical_drift']}  "
                 f"(older={d['older_count']})")
    if d.get("last_drift_ts"):
        lines.append(f"last drift row      {d['last_drift_ts']}")
    lines.append("")
    h = report["hygiene_summary"]
    if h is not None:
        lines.append(f"hygiene clean       ready_to_gate_clean={h['ready_to_gate_clean']}  "
                     f"all={h['ready_to_gate_all']}")
        lines.append(f"hygiene errors      full={h['errors_full']}  clean={h['errors_clean']}")
    else:
        lines.append("hygiene             sidecar missing")
    lines.append("")
    lines.append(f"clear_eligible      {report['clear_eligible']}")
    if report["clear_blockers"]:
        lines.append("blockers:")
        for b in report["clear_blockers"]:
            lines.append(f"  - {b}")
    lines.append("")
    lines.append(f"recommendation:  {report['recommendation']}")
    lines.append(bar)
    return "\n".join(lines) + "\n"


# ── Clear path ─────────────────────────────────────────────────────────────


def perform_clear(reason: str, db_path: Path) -> bool:
    """Call CircuitBreakers.clear_halt with a typed reason. Returns True
    on success, False otherwise. Never raises."""
    try:
        from execution.circuit_breakers import CircuitBreakers
        cb = CircuitBreakers(db_path=str(db_path))
        cb.clear_halt(cleared_by=reason)
        return True
    except Exception as exc:
        logger.error("clear_halt failed: %s", exc)
        return False


# ── CLI ────────────────────────────────────────────────────────────────────


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Phase 1G.1 halt-state review — read-only by default."
    )
    parser.add_argument(
        "--clear-reviewed", action="store_true",
        help="If preconditions pass, call CircuitBreakers.clear_halt. "
             "Requires --reason. NEVER auto-clears.",
    )
    parser.add_argument(
        "--dry-run-and-print-decision", action="store_true",
        help="Explicit alias for the default read-only mode. Forces the "
             "script to print its verdict and the exact clear command "
             "without mutating anything, even if --clear-reviewed is "
             "passed alongside (the --clear-reviewed write path is "
             "ignored in that combination).",
    )
    parser.add_argument(
        "--reason", default="",
        help="Operator-typed reason string. Required with --clear-reviewed.",
    )
    parser.add_argument(
        "--max-snapshot-age-hours", type=float,
        default=DEFAULT_MAX_SNAPSHOT_AGE_HOURS,
        help=f"Maximum broker snapshot age in hours "
             f"(default {DEFAULT_MAX_SNAPSHOT_AGE_HOURS}).",
    )
    parser.add_argument(
        "--reconciler-lookback-min", type=int,
        default=DEFAULT_RECONCILER_LOOKBACK_MIN,
        help=f"Lookback window for 'recent' reconciler drift rows "
             f"(default {DEFAULT_RECONCILER_LOOKBACK_MIN}m).",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Emit the report as JSON instead of text.",
    )
    args = parser.parse_args(argv)

    db_path = Path(cfg.DB_PATH)
    if not db_path.exists():
        logger.error("db not found at %s", db_path)
        return 1

    report = build_review_report(
        db_path=db_path,
        max_snap_age_hours=args.max_snapshot_age_hours,
        reconciler_lookback_min=args.reconciler_lookback_min,
    )
    output = json.dumps(report, indent=2, default=str) if args.json else render_text(report)
    sys.stdout.write(output)
    if not args.json:
        sys.stdout.write("\n")

    if not args.clear_reviewed:
        return 0

    # Phase 1G.2 T6 — explicit dry-run flag wins over --clear-reviewed.
    # This lets the operator paste the eligible command verbatim from the
    # report and add --dry-run-and-print-decision to confirm preconditions
    # without mutating state.
    if args.dry_run_and_print_decision:
        logger.info(
            "--dry-run-and-print-decision set; ignoring --clear-reviewed "
            "and exiting after the read-only report."
        )
        return 0

    # --clear-reviewed path. Every guard re-checked here so the safe
    # default never relies on the caller having read the text output.
    if not args.reason or not args.reason.strip():
        logger.error("--clear-reviewed requires --reason \"...\"")
        return 2
    if not report["clear_eligible"]:
        logger.error("clear blocked — preconditions not met:")
        for b in report["clear_blockers"]:
            logger.error("  - %s", b)
        return 2
    reason = f"operator-reviewed: {args.reason.strip()}"
    ok = perform_clear(reason, db_path)
    if not ok:
        return 3
    logger.info("circuit breaker cleared with reason=%r", reason)
    # Re-read state to confirm.
    after = _load_circuit_breaker_state(db_path)
    if after and after.get("halted"):
        logger.error("clear_halt returned ok but state still halted=true")
        return 3
    sys.stdout.write(f"\nCLEARED. new halted={after.get('halted') if after else 'unknown'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

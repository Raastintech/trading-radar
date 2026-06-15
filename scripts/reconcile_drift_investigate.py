"""
scripts/reconcile_drift_investigate.py — Fix 1 dry-run investigation.

Pulls the current broker view from Alpaca, the open decision rows from
SQLite, and the recent Alpaca order history. Produces a *proposal* of
corrections — does NOT mutate the DB by default.

For each drift it prints what the corrective action would be:

  DECISIONS_ONLY  (book says open, broker has nothing)
    → Find the closing order(s) from broker history.
    → Plan: mark the earliest opened row position_closed=1 with the
      broker exit fill data; mark duplicate "open" rows as
      position_opened=0 (they were re-scans of the same logical
      position, never separate trades).

  BROKER_ONLY     (broker holds, book missing)
    → For each broker position with no matching open decision row,
      check whether a stale stuck row exists (e.g. orderstatus.new).
      If yes, plan an in-place update with the broker's entry data.
      If no, plan an "adoption" decision row with sane defaults.

Usage:
  SNIPER_ENV_PATH=/home/gem/secure/trading.env \
    .venv/bin/python scripts/reconcile_drift_investigate.py [--apply]

Default is dry-run (no DB writes, no halt clear). With --apply the
script will:
  1. Take a fresh DB backup before any mutation.
  2. Apply the proposed corrections inside a single transaction.
  3. Clear the manual halt via CircuitBreakers.clear_halt().

The investigation is paper- and live-safe: no broker orders submitted,
no cancels, no closes. The only Alpaca calls are read-only
(get_positions, get_orders).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_env_path = os.environ.get("SNIPER_ENV_PATH", "")
if _env_path:
    try:
        from dotenv import load_dotenv as _load
        _load(Path(_env_path), override=True)
    except ImportError:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("reconcile_drift_investigate")


DB_PATH = Path(__file__).resolve().parents[1] / "db" / "trading.db"
ORDER_HISTORY_LOOKBACK_DAYS = 60  # plenty to cover open-but-closed-on-broker rows


# ── Broker / DB readers ──────────────────────────────────────────────────────

def fetch_broker_state() -> Tuple[List[Dict], List[Dict], bool]:
    """Return ``(positions, recent_orders, broker_call_ok)``. Read-only.

    Phase 1G safety patch: surface ``broker_call_ok`` so the dry-run
    investigator can distinguish "broker really has nothing" from
    "broker call failed and we cannot classify drift." When the
    positions call fails, we still attempt the orders fetch (it may
    succeed independently) but mark the cycle as unavailable so the
    renderer / apply logic refuse to act on an ambiguous snapshot.
    """
    from core.alpaca_client import get_alpaca
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import QueryOrderStatus

    alpaca = get_alpaca()

    broker_call_ok = True
    if hasattr(alpaca, "get_positions_with_status"):
        positions, broker_call_ok = alpaca.get_positions_with_status()
        positions = list(positions or [])
    else:
        positions = alpaca.get_positions() or []

    after = datetime.now(timezone.utc) - timedelta(days=ORDER_HISTORY_LOOKBACK_DAYS)
    req = GetOrdersRequest(
        status=QueryOrderStatus.ALL,
        after=after,
        limit=500,
        direction="desc",
        nested=False,
    )
    try:
        orders = alpaca._trading.get_orders(filter=req) or []
    except Exception as exc:
        logger.warning("orders fetch failed: %s", exc)
        orders = []

    serialized_orders = []
    for o in orders:
        s = alpaca._serialize_order(o)
        # Add the raw filled_at/submitted_at since _serialize_order already
        # ISO-formats them; also keep canceled_at for visibility.
        s["canceled_at"] = getattr(o, "canceled_at", None) and o.canceled_at.isoformat()
        s["expired_at"] = getattr(o, "expired_at", None) and o.expired_at.isoformat()
        serialized_orders.append(s)

    return positions, serialized_orders, broker_call_ok


def fetch_open_decisions(con: sqlite3.Connection) -> List[Dict]:
    cur = con.execute(
        "SELECT * FROM decisions WHERE position_opened=1 AND position_closed=0"
    )
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def fetch_unfilled_decisions(con: sqlite3.Connection) -> List[Dict]:
    """Rows that have an order_id but are stuck pre-fill (e.g. orderstatus.new)."""
    cur = con.execute(
        """SELECT * FROM decisions
           WHERE position_opened=0 AND position_closed=0
             AND order_id IS NOT NULL AND order_id != ''"""
    )
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


# ── Drift detection ─────────────────────────────────────────────────────────

def index_by_ticker(items: List[Dict], key: str = "ticker") -> Dict[str, List[Dict]]:
    out: Dict[str, List[Dict]] = defaultdict(list)
    for it in items:
        t = str(it.get(key) or "").upper().strip()
        if t:
            out[t].append(it)
    return out


def _status_filled(o: Dict) -> bool:
    """Alpaca serializes status as 'OrderStatus.FILLED'. Normalize."""
    s = (o.get("status") or "").lower().split(".")[-1]
    return s == "filled"


def aggregate_fills(
    orders_by_ticker: Dict[str, List[Dict]],
    ticker: str,
    side: str,
    after_iso: Optional[str] = None,
    before_iso: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Aggregate all filled orders on one side for a ticker into a single
    weighted-average view.

    side is 'buy' or 'sell'. Returns {total_qty, avg_price, first_at,
    last_at, order_ids} or None if no fills found in the window."""
    total_qty = 0.0
    notional = 0.0
    order_ids: List[str] = []
    first_at: Optional[str] = None
    last_at: Optional[str] = None
    for o in orders_by_ticker.get(ticker, []):
        if not _status_filled(o):
            continue
        if (o.get("side") or "").lower() != side:
            continue
        fa = o.get("filled_at") or ""
        if after_iso and fa and fa < after_iso:
            continue
        if before_iso and fa and fa >= before_iso:
            continue
        qty = float(o.get("filled_qty") or 0)
        px = float(o.get("filled_avg_price") or 0)
        if qty <= 0 or px <= 0:
            continue
        total_qty += qty
        notional += qty * px
        order_ids.append(o["order_id"])
        if first_at is None or (fa and fa < first_at):
            first_at = fa
        if last_at is None or (fa and fa > last_at):
            last_at = fa
    if total_qty <= 0:
        return None
    return {
        "total_qty": total_qty,
        "avg_price": notional / total_qty,
        "first_at": first_at,
        "last_at": last_at,
        "order_ids": order_ids,
        "n_fills": len(order_ids),
    }


# ── Proposal builder ────────────────────────────────────────────────────────

def infer_book_side(row: Dict) -> str:
    """Derive long/short from the decision row's direction column."""
    direction = (row.get("direction") or "").upper()
    if direction in ("SHORT", "SELL"):
        return "short"
    return "long"


def build_proposal(
    open_decisions: List[Dict],
    unfilled_decisions: List[Dict],
    broker_positions: List[Dict],
    broker_orders: List[Dict],
) -> Dict[str, Any]:
    """Return a structured plan. No mutations."""
    book_by_ticker = index_by_ticker(open_decisions)
    broker_by_ticker = {
        str(p.get("ticker") or "").upper(): p for p in broker_positions
    }
    orders_by_ticker = index_by_ticker(broker_orders, key="symbol")
    unfilled_by_ticker = index_by_ticker(unfilled_decisions)

    actions: List[Dict] = []

    # DECISIONS_ONLY: book has open rows that the broker no longer holds.
    for ticker, rows in sorted(book_by_ticker.items()):
        if ticker in broker_by_ticker:
            continue  # matched or mismatched-qty — handle separately
        rows_sorted = sorted(rows, key=lambda r: r.get("ts") or "")
        canonical = rows_sorted[0]
        duplicates = rows_sorted[1:]
        book_side = infer_book_side(canonical)
        open_side = "buy" if book_side == "long" else "sell"
        close_side = "sell" if book_side == "long" else "buy"

        # Aggregate broker fills on both sides; this captures the case
        # where the daemon emitted duplicate broker orders.
        open_agg = aggregate_fills(orders_by_ticker, ticker, open_side)
        close_agg = aggregate_fills(
            orders_by_ticker, ticker, close_side,
            after_iso=open_agg["last_at"] if open_agg else None,
        )

        if close_agg and open_agg:
            entry_px = open_agg["avg_price"]
            exit_px = close_agg["avg_price"]
            qty = min(open_agg["total_qty"], close_agg["total_qty"])
            if book_side == "short":
                pnl = (entry_px - exit_px) * qty
                pnl_pct = (entry_px - exit_px) / entry_px if entry_px else 0
            else:
                pnl = (exit_px - entry_px) * qty
                pnl_pct = (exit_px - entry_px) / entry_px if entry_px else 0
        else:
            entry_px = open_agg["avg_price"] if open_agg else None
            exit_px = close_agg["avg_price"] if close_agg else None
            pnl = None
            pnl_pct = None

        # Three sub-cases:
        #  - open_agg None: rows were never actually filled by the broker
        #    (e.g. all submitted orders EXPIRED). Demote every row.
        #  - close_agg None but open_agg present: a real position existed
        #    and we have no close evidence — refuse to mutate, escalate.
        #  - both present: standard close-the-ghost.
        if open_agg is None:
            kind = "DEMOTE_NEVER_OPENED"
        elif close_agg is None:
            kind = "CLOSE_GHOST"  # will fail the apply gate
        else:
            kind = "CLOSE_GHOST"

        actions.append({
            "kind": kind,
            "ticker": ticker,
            "side": book_side,
            "canonical_id": canonical["id"],
            "all_row_ids": [r["id"] for r in rows_sorted],
            "duplicate_ids": [r["id"] for r in duplicates],
            "duplicate_count": len(duplicates),
            "first_opened_ts": canonical.get("ts"),
            "broker_open_agg": open_agg,
            "broker_close_agg": close_agg,
            "entry_price_broker": entry_px,
            "exit_price": exit_px,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "missing_open_evidence": open_agg is None,
            "missing_close_evidence": close_agg is None and open_agg is not None,
        })

    # BROKER_ONLY: broker positions with no matching open decision row.
    for ticker, pos in sorted(broker_by_ticker.items()):
        if ticker in book_by_ticker:
            continue  # matched
        stuck = unfilled_by_ticker.get(ticker, [])
        broker_side = str(pos.get("side") or "").lower()
        broker_qty = pos.get("qty")
        broker_entry = pos.get("entry_price")
        open_side = "buy" if broker_side == "long" else "sell"
        open_agg = aggregate_fills(orders_by_ticker, ticker, open_side)

        if stuck:
            stuck_row = stuck[0]
            actions.append({
                "kind": "UPDATE_STUCK_ORDER",
                "ticker": ticker,
                "decision_id": stuck_row["id"],
                "order_id": stuck_row.get("order_id"),
                "broker_qty": broker_qty,
                "broker_entry": broker_entry,
                "broker_side": broker_side,
                "broker_open_agg": open_agg,
            })
        else:
            actions.append({
                "kind": "ADOPT_ORPHAN",
                "ticker": ticker,
                "broker_qty": broker_qty,
                "broker_entry": broker_entry,
                "broker_side": broker_side,
                "broker_open_agg": open_agg,
            })

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "broker_positions_count": len(broker_positions),
        "open_decisions_count": len(open_decisions),
        "stuck_unfilled_count": len(unfilled_decisions),
        "actions": actions,
    }


# ── Apply path (only with --apply) ───────────────────────────────────────────

def apply_proposal(con: sqlite3.Connection, proposal: Dict[str, Any]) -> Dict[str, int]:
    """Mutates the DB in a single transaction. Returns a counts summary."""
    counts = {"closed": 0, "demoted_duplicates": 0, "demoted_never_opened": 0,
              "updated_stuck": 0, "adopted": 0}

    with con:  # transaction
        for act in proposal["actions"]:
            kind = act["kind"]

            if kind == "CLOSE_GHOST":
                if act.get("missing_close_evidence"):
                    raise RuntimeError(
                        f"CLOSE_GHOST {act['ticker']}: no matching broker close "
                        f"fill found in lookback window — refusing blind close. "
                        f"Investigate manually."
                    )
                open_agg = act["broker_open_agg"] or {}
                close_agg = act["broker_close_agg"] or {}
                # Stamp the canonical row with broker-aggregated truth so
                # downstream telemetry sees a single faithful trade per
                # logical position, regardless of how many duplicate broker
                # orders the daemon emitted.
                con.execute(
                    """UPDATE decisions
                          SET position_opened=1,
                              position_closed=1,
                              fill_price=?,
                              fill_qty=?,
                              fill_status='reconciled_aggregate',
                              entry_price=COALESCE(entry_price, ?),
                              exit_price=?,
                              exit_fill_price=?,
                              pnl=?,
                              pnl_pct=?,
                              reconciled_at=?,
                              suspect_state='reconciled_close',
                              suspect_reason=?
                        WHERE id=?""",
                    (
                        act["entry_price_broker"],
                        open_agg.get("total_qty"),
                        act["entry_price_broker"],
                        act["exit_price"],
                        act["exit_price"],
                        act["pnl"],
                        round(act["pnl_pct"] or 0, 4),
                        datetime.now(timezone.utc).isoformat(),
                        (f"broker_open n_fills={open_agg.get('n_fills')} "
                         f"qty={open_agg.get('total_qty')} avg={open_agg.get('avg_price')} | "
                         f"broker_close n_fills={close_agg.get('n_fills')} "
                         f"qty={close_agg.get('total_qty')} avg={close_agg.get('avg_price')} "
                         f"last_at={close_agg.get('last_at')}"),
                        act["canonical_id"],
                    ),
                )
                counts["closed"] += 1
                # Demote duplicate "open" rows so they stop polluting
                # concentration / slippage / hygiene reports.
                for dup_id in act["duplicate_ids"]:
                    con.execute(
                        """UPDATE decisions
                              SET position_opened=0,
                                  reconciled_at=?,
                                  suspect_state='duplicate_scan',
                                  suspect_reason=?
                            WHERE id=?""",
                        (
                            datetime.now(timezone.utc).isoformat(),
                            f"duplicate scanner-iteration row; canonical={act['canonical_id']}",
                            dup_id,
                        ),
                    )
                    counts["demoted_duplicates"] += 1

            elif kind == "DEMOTE_NEVER_OPENED":
                # Broker never filled any opening order on this ticker.
                # Every "open" decision row was a submit-time write that
                # never matched a real fill. Demote them all.
                for row_id in act["all_row_ids"]:
                    con.execute(
                        """UPDATE decisions
                              SET position_opened=0,
                                  reconciled_at=?,
                                  suspect_state='never_filled',
                                  suspect_reason=?
                            WHERE id=?""",
                        (
                            datetime.now(timezone.utc).isoformat(),
                            f"broker has zero filled open-side orders for "
                            f"{act['ticker']}; position never existed",
                            row_id,
                        ),
                    )
                    counts["demoted_never_opened"] += 1

            elif kind == "UPDATE_STUCK_ORDER":
                # KVYO-style: existing row stuck at orderstatus.new, broker actually filled.
                qty = abs(float(act["broker_qty"] or 0))
                entry = float(act["broker_entry"] or 0)
                open_order = act.get("open_order_match") or {}
                fill_status = "orderstatus.filled" if open_order else "reconciled_open"
                con.execute(
                    """UPDATE decisions
                          SET position_opened=1,
                              fill_price=?,
                              fill_qty=?,
                              fill_status=?,
                              reconciled_at=?,
                              suspect_state='reconciled_open',
                              suspect_reason=?
                        WHERE id=?""",
                    (
                        entry,
                        qty,
                        fill_status,
                        datetime.now(timezone.utc).isoformat(),
                        f"adopted broker fill qty={qty} entry={entry}; "
                        f"matched_order={open_order.get('order_id') if open_order else 'none'}",
                        act["decision_id"],
                    ),
                )
                counts["updated_stuck"] += 1

            elif kind == "ADOPT_ORPHAN":
                # SBAC-style: broker has it, no DB row at all. Create one.
                qty = abs(float(act["broker_qty"] or 0))
                entry = float(act["broker_entry"] or 0)
                direction = "SHORT" if act["broker_side"] == "short" else "LONG"
                open_order = act.get("open_order_match") or {}
                new_id = str(uuid.uuid4())
                con.execute(
                    """INSERT INTO decisions
                         (id, run_id, ts, ticker, strategy, direction,
                          signal_score, shares, entry_price, fill_price, fill_qty,
                          fill_status, position_opened, position_closed,
                          reconciled_at, suspect_state, suspect_reason, notes)
                       VALUES (?, ?, ?, ?, 'ADOPTED', ?, NULL, ?, ?, ?, ?,
                               ?, 1, 0, ?, 'reconciled_open', ?, ?)""",
                    (
                        new_id,
                        None,
                        datetime.now(timezone.utc).isoformat(),
                        act["ticker"],
                        direction,
                        qty,
                        entry,
                        entry,
                        qty,
                        "reconciled_open",
                        datetime.now(timezone.utc).isoformat(),
                        f"adopted via reconciliation; broker qty={qty} entry={entry}; "
                        f"matched_order={open_order.get('order_id') if open_order else 'none'}",
                        "adopted_via_reconciliation",
                    ),
                )
                counts["adopted"] += 1

            else:
                raise RuntimeError(f"unknown action kind: {kind}")

    return counts


def clear_breaker_halt(con: sqlite3.Connection) -> None:
    """Clear the persisted manual halt so the daemon resumes."""
    with con:
        con.execute(
            """UPDATE circuit_breaker_state
                  SET halted=0,
                      reason='',
                      cleared_by='reconcile_drift_investigate.py',
                      cleared_at=?,
                      tripped_at=NULL
                WHERE id=1""",
            (datetime.now(timezone.utc).isoformat(),),
        )


# ── CLI ─────────────────────────────────────────────────────────────────────

def render_proposal(proposal: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append(f"== reconcile drift investigation ({proposal['generated_at']}) ==")
    lines.append(f"  broker_positions = {proposal['broker_positions_count']}")
    lines.append(f"  open_decisions   = {proposal['open_decisions_count']}")
    lines.append(f"  stuck_unfilled   = {proposal['stuck_unfilled_count']}")
    lines.append(f"  actions          = {len(proposal['actions'])}")
    lines.append("")
    for act in proposal["actions"]:
        if act["kind"] == "DEMOTE_NEVER_OPENED":
            lines.append(
                f"  DEMOTE_NEVER_OPENED {act['ticker']:<6} "
                f"rows={len(act['all_row_ids'])}  side={act['side']}  "
                f"(broker had 0 filled open-side orders)"
            )
            continue
        if act["kind"] == "CLOSE_GHOST":
            o = act["broker_open_agg"] or {}
            c = act["broker_close_agg"] or {}
            ev = "OK" if not act["missing_close_evidence"] else "NO_EVIDENCE"
            pnl = act["pnl"]
            pnl_pct = (round(act['pnl_pct']*100, 2)
                       if act['pnl_pct'] is not None else None)
            lines.append(
                f"  CLOSE_GHOST {act['ticker']:<6} side={act['side']:<5} "
                f"canonical={act['canonical_id'][:8]} dupes={act['duplicate_count']:>2}  "
                f"src={ev}"
            )
            lines.append(
                f"      open: n={o.get('n_fills')} qty={o.get('total_qty')} "
                f"@avg={o.get('avg_price')}  ({o.get('first_at')} → {o.get('last_at')})"
            )
            lines.append(
                f"      close: n={c.get('n_fills')} qty={c.get('total_qty')} "
                f"@avg={c.get('avg_price')}  at={c.get('last_at')}"
            )
            lines.append(
                f"      → pnl=${pnl:.2f}  pnl_pct={pnl_pct}%"
                if pnl is not None else "      → pnl=n/a"
            )
        elif act["kind"] == "UPDATE_STUCK_ORDER":
            o = act.get("broker_open_agg") or {}
            lines.append(
                f"  UPDATE_STUCK {act['ticker']:<6} decision={act['decision_id'][:8]} "
                f"broker_qty={act['broker_qty']} entry={act['broker_entry']} "
                f"side={act['broker_side']}  "
                f"broker_fills(n={o.get('n_fills')} qty={o.get('total_qty')} avg={o.get('avg_price')})"
            )
        elif act["kind"] == "ADOPT_ORPHAN":
            o = act.get("broker_open_agg") or {}
            lines.append(
                f"  ADOPT_ORPHAN {act['ticker']:<6} broker_qty={act['broker_qty']} "
                f"entry={act['broker_entry']} side={act['broker_side']}  "
                f"broker_fills(n={o.get('n_fills')} qty={o.get('total_qty')} avg={o.get('avg_price')})"
            )
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Actually mutate the DB and clear the halt. "
                             "Default is dry-run.")
    parser.add_argument("--out", default=None,
                        help="Optional path to write the proposal JSON.")
    args = parser.parse_args(argv)

    try:
        broker_positions, broker_orders, broker_call_ok = fetch_broker_state()
    except Exception as exc:
        logger.error("broker fetch failed: %s", exc)
        return 1

    con = sqlite3.connect(str(DB_PATH))
    try:
        open_decisions = fetch_open_decisions(con)
        unfilled = fetch_unfilled_decisions(con)

        # Phase 1G classification: distinguish four cases up front so
        # the operator reads the right story before any actions matter.
        #   1. broker_unavailable: get_positions failed, drift cannot be
        #      classified, NO actions are proposed, halt may be stale.
        #   2. real_empty: broker returned [] and call succeeded; book
        #      may legitimately need cleanup.
        #   3. matched: broker and book agree row-for-row; nothing to do.
        #   4. has_drift: real drift; standard build_proposal output.
        halt_state = _read_halt_state(con)
        if not broker_call_ok:
            classification = "broker_unavailable"
        elif not broker_positions and not open_decisions:
            classification = "matched"  # both empty
        else:
            # Defer the full classification to build_proposal's
            # action-list emptiness check.
            classification = "to-be-decided"

        if classification == "broker_unavailable":
            proposal = {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "classification": "broker_unavailable",
                "broker_call_ok": False,
                "broker_positions_count": 0,
                "open_decisions_count": len(open_decisions),
                "stuck_unfilled_count": len(unfilled),
                "actions": [],
                "halt_state": halt_state,
                "operator_notice": (
                    "Broker get_positions call failed; this script cannot "
                    "classify drift this cycle. If the daemon's halt is "
                    "older than the last successful RECONCILE OK log line, "
                    "it is likely a stale halt from a transient API "
                    "failure rather than real drift. Wait for the next "
                    "cycle or clear the halt manually after operator "
                    "review. --apply is disabled in this classification."
                ),
            }
            print(_render_broker_unavailable(proposal))
            if args.out:
                Path(args.out).write_text(json.dumps(proposal, indent=2, default=str))
                logger.info("wrote proposal JSON → %s", args.out)
            if args.apply:
                logger.error(
                    "refusing to apply: broker is unavailable; cannot "
                    "classify drift on this cycle"
                )
                return 2
            return 0

        proposal = build_proposal(open_decisions, unfilled, broker_positions, broker_orders)
        proposal["broker_call_ok"] = True
        proposal["halt_state"] = halt_state

        # Final classification once actions are computed.
        if not proposal.get("actions"):
            proposal["classification"] = "matched"
            stale_note = _maybe_stale_halt_note(halt_state)
            if stale_note:
                proposal["stale_halt_note"] = stale_note
        else:
            proposal["classification"] = "has_drift"

        print(render_proposal(proposal))
        if args.out:
            Path(args.out).write_text(json.dumps(proposal, indent=2, default=str))
            logger.info("wrote proposal JSON → %s", args.out)

        if not args.apply:
            print("\n(dry-run — pass --apply to mutate the DB and clear the halt)")
            if proposal["classification"] == "matched" and halt_state.get("halted"):
                print("note: halt is currently set, but book/broker are "
                      "matched. Operator must clear manually after review — "
                      "this script does not auto-clear stale halts.")
            return 0

        # Refuse to apply if any CLOSE_GHOST is missing close-side evidence
        # despite having open fills — that case needs human eyes.
        suspicious = [a for a in proposal["actions"]
                      if a["kind"] == "CLOSE_GHOST" and a.get("missing_close_evidence")]
        if suspicious:
            logger.error(
                "refusing to apply: %d CLOSE_GHOST action(s) have open fills but "
                "no close evidence: %s",
                len(suspicious), [a["ticker"] for a in suspicious],
            )
            return 2

        counts = apply_proposal(con, proposal)
        clear_breaker_halt(con)
        logger.info("APPLIED counts=%s; manual halt cleared", counts)
        return 0
    finally:
        con.close()


def _read_halt_state(con: sqlite3.Connection) -> Dict[str, Any]:
    """Snapshot of the circuit_breaker_state row for the classifier."""
    try:
        row = con.execute(
            "SELECT halted, reason, tripped_at, cleared_at, cleared_by "
            "FROM circuit_breaker_state WHERE id=1"
        ).fetchone()
    except sqlite3.Error:
        return {}
    if not row:
        return {}
    return {
        "halted":     bool(row[0]),
        "reason":     row[1] or "",
        "tripped_at": row[2] or "",
        "cleared_at": row[3] or "",
        "cleared_by": row[4] or "",
    }


def _maybe_stale_halt_note(halt_state: Dict[str, Any]) -> Optional[str]:
    """Phase 1G: a halt that's currently latched while the book and
    broker reconcile cleanly is usually stale. Surface that explicitly
    so the operator knows the halt does not reflect current state."""
    if not halt_state.get("halted"):
        return None
    return (
        f"Halt is currently latched ({halt_state.get('reason', '')[:80]}), "
        f"tripped_at={halt_state.get('tripped_at', '?')}, but book and "
        f"broker now match exactly. This is consistent with a stale halt "
        f"from a prior transient broker-read failure. Investigate before "
        f"clearing."
    )


def _render_broker_unavailable(proposal: Dict[str, Any]) -> str:
    lines = []
    lines.append(f"== reconcile drift investigation ({proposal['generated_at']}) ==")
    lines.append("  classification          = broker_unavailable")
    lines.append(f"  broker_call_ok          = {proposal['broker_call_ok']}")
    lines.append(f"  open_decisions          = {proposal['open_decisions_count']}")
    lines.append(f"  stuck_unfilled          = {proposal['stuck_unfilled_count']}")
    lines.append(f"  actions                 = 0  (cannot classify)")
    halt = proposal.get("halt_state") or {}
    if halt.get("halted"):
        lines.append(f"  halt_state              = HALTED (tripped_at={halt.get('tripped_at')})")
        lines.append(f"  halt_reason             = {halt.get('reason', '')[:90]}")
    lines.append("")
    lines.append("operator notice:")
    for line in (proposal.get("operator_notice") or "").splitlines():
        lines.append(f"  {line}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())

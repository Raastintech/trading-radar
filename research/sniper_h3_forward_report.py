"""
research/sniper_h3_forward_report.py — Phase 12B forward report.

Reads `db/trading.db` SNIPER paper signals + outcomes, restricted to rows that
carry the Phase 12B `aux_h3` JSON metadata, and reports:

  - total SNIPER signals
  - total H3 candidates (h3_candidate = True)
  - closed H3 candidates
  - WR / avg adjusted return / stop+target hit on closed H3 candidates
  - same stats on closed non-H3 SNIPER candidates (control)
  - missing-metadata counts for the auxiliary research-context fields
  - per-gate fail-attribution (which gate is the binding constraint forward?)
  - status banner: open X · closed Y · insufficient until 20–30 closed

Mode: analysis only. Reads the DB; never mutates. Runs cleanly with **zero
rows** of data — produces a "no data yet" report rather than crashing.

Usage
-----
  cd /home/gem/trading-production
  set -a; . /home/gem/secure/trading.env; set +a   # if not already exported
  .venv/bin/python research/sniper_h3_forward_report.py
  .venv/bin/python research/sniper_h3_forward_report.py --json
  .venv/bin/python research/sniper_h3_forward_report.py --since 2026-05-05
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parent.parent
DB_PATH_DEFAULT = REPO / "db" / "trading.db"
OUT_JSON = REPO / "docs" / "scorecards" / "sniper_h3_forward_report.json"

# Pass-criteria thresholds (mirrored from Phase 12A so the forward report is
# directly comparable to the historical screen).
TARGET_CLOSED_LOWER = 20  # below this we say "insufficient until …"
TARGET_CLOSED_UPPER = 30
WR_TARGET_PCT = 55.0


# ──────────────────────────────────────────────────────────────────────────────
# Loading
# ──────────────────────────────────────────────────────────────────────────────

def _open_db(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise SystemExit(f"db not found: {path}")
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    return con


def _has_aux_h3_column(con: sqlite3.Connection) -> bool:
    cols = [r[1] for r in con.execute("PRAGMA table_info(paper_signals)").fetchall()]
    return "aux_h3" in cols


def load_sniper_rows(con: sqlite3.Connection, since: Optional[str]) -> List[Dict[str, Any]]:
    """Return SNIPER paper_signals rows, joined with the latest paper_signal_outcomes
    record per signal. Tolerant of missing outcomes (still_open True or no row).
    """
    if not _has_aux_h3_column(con):
        return []
    where = ["sleeve = 'SNIPER_V6'"]
    params: List[Any] = []
    if since:
        where.append("date(logged_at) >= date(?)")
        params.append(since)
    sql = f"""
      SELECT
        ps.id            AS signal_id,
        ps.logged_at,
        ps.ticker,
        ps.side,
        ps.entry_price,
        ps.stop_loss,
        ps.target_price,
        ps.score,
        ps.sector,
        ps.status        AS signal_status,
        ps.aux_h3,
        po.return_pct,
        po.adjusted_return_pct,
        po.stop_hit,
        po.target_hit,
        po.still_open,
        po.hold_complete,
        po.horizon_days,
        po.measured_at
      FROM paper_signals ps
      LEFT JOIN paper_signal_outcomes po
        ON po.signal_id = ps.id
       AND po.horizon_days = 10  -- SNIPER primary horizon
      WHERE {' AND '.join(where)}
      ORDER BY ps.logged_at ASC
    """
    rows = [dict(r) for r in con.execute(sql, params).fetchall()]
    for r in rows:
        if r.get("aux_h3"):
            try:
                r["aux_h3_obj"] = json.loads(r["aux_h3"])
            except Exception:
                r["aux_h3_obj"] = None
        else:
            r["aux_h3_obj"] = None
    return rows


# ──────────────────────────────────────────────────────────────────────────────
# Stats
# ──────────────────────────────────────────────────────────────────────────────

def _is_closed(row: Dict[str, Any]) -> bool:
    """A signal is treated as closed if either the paper_signals.status
    indicates a closure OR the outcome row is no longer still_open and has a
    return_pct."""
    status = (row.get("signal_status") or "").lower()
    if status in {"closed", "stopped", "target", "timeout", "invalidated"}:
        return True
    if row.get("still_open") in (0, False) and row.get("adjusted_return_pct") is not None:
        return True
    return False


def _aux_field(row: Dict[str, Any], key: str) -> Any:
    obj = row.get("aux_h3_obj") or {}
    return obj.get(key)


def _is_h3_candidate(row: Dict[str, Any]) -> bool:
    return bool(_aux_field(row, "h3_candidate"))


def cohort_stats(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return {
            "n_total": 0, "n_closed": 0, "n_open": 0,
            "wr_pct": None, "avg_adj_pct": None,
            "stop_hit_pct": None, "target_hit_pct": None,
            "by_status_count": {}, "by_signal_status": {}, "tickers": [],
        }
    closed = [r for r in rows if _is_closed(r)]
    n_closed = len(closed)
    adjs = [r["adjusted_return_pct"] for r in closed if r.get("adjusted_return_pct") is not None]
    wins = sum(1 for a in adjs if a > 0)
    stops = sum(1 for r in closed if r.get("stop_hit") in (1, True))
    tgts = sum(1 for r in closed if r.get("target_hit") in (1, True))

    sig_status_counts: Dict[str, int] = {}
    for r in rows:
        s = r.get("signal_status") or "unknown"
        sig_status_counts[s] = sig_status_counts.get(s, 0) + 1

    return {
        "n_total": len(rows),
        "n_closed": n_closed,
        "n_open": sum(1 for r in rows if not _is_closed(r)),
        "wr_pct": round(100.0 * wins / len(adjs), 2) if adjs else None,
        "avg_adj_pct": round(statistics.fmean(adjs), 3) if adjs else None,
        "stop_hit_pct": round(100.0 * stops / n_closed, 2) if n_closed else None,
        "target_hit_pct": round(100.0 * tgts / n_closed, 2) if n_closed else None,
        "by_signal_status": sig_status_counts,
        "tickers": sorted({r["ticker"] for r in rows if r.get("ticker")}),
    }


def gate_fail_attribution(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    """For non-H3 SNIPER signals, count how often each gate failed."""
    counts = {
        "score_80_89_fail": 0,
        "vix_15_20_fail": 0,
        "vol_ratio_lt_1_5_fail": 0,
        "sector_in_HC_COMM_TECH_fail": 0,
    }
    for r in rows:
        reason = (_aux_field(r, "h3_reason") or {})
        if not reason:
            continue
        for gate, key in [
            ("score_80_89_pass", "score_80_89_fail"),
            ("vix_15_20_pass", "vix_15_20_fail"),
            ("vol_ratio_lt_1_5_pass", "vol_ratio_lt_1_5_fail"),
            ("sector_in_HC_COMM_TECH_pass", "sector_in_HC_COMM_TECH_fail"),
        ]:
            if reason.get(gate) is False:
                counts[key] += 1
    return counts


def missing_metadata_counts(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    """For the auxiliary research-context fields that may not be populated yet,
    count how many signals are missing each."""
    aux_keys = [
        "daily_entry_validator_state",
        "market_forecast_regime",
        "market_forecast_bias_5d",
        "market_forecast_bias_10d",
        "market_posture_bias",
        "options_quality",
        "stock_extension_state",
        "alpha_discovery_state",
    ]
    counts = {k: 0 for k in aux_keys}
    counts["__rows_with_aux_h3"] = 0
    counts["__rows_without_aux_h3"] = 0
    for r in rows:
        if r.get("aux_h3_obj") is None:
            counts["__rows_without_aux_h3"] += 1
            continue
        counts["__rows_with_aux_h3"] += 1
        for k in aux_keys:
            if r["aux_h3_obj"].get(k) is None:
                counts[k] += 1
    return counts


def status_banner(h3_stats: Dict[str, Any]) -> str:
    n_open = h3_stats.get("n_open", 0)
    n_closed = h3_stats.get("n_closed", 0)
    if n_closed >= TARGET_CLOSED_UPPER:
        suffix = f"target reached ({n_closed} ≥ {TARGET_CLOSED_UPPER})"
    elif n_closed >= TARGET_CLOSED_LOWER:
        suffix = f"approaching threshold ({n_closed}/{TARGET_CLOSED_UPPER})"
    else:
        suffix = f"insufficient until {TARGET_CLOSED_LOWER}–{TARGET_CLOSED_UPPER} closed"
    return f"SNIPER H3 OOS: open {n_open} · closed {n_closed} · {suffix}"


# ──────────────────────────────────────────────────────────────────────────────
# Render
# ──────────────────────────────────────────────────────────────────────────────

def render_text(report: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("SNIPER H3 forward OOS report")
    lines.append(f"Generated: {report['generated_at']}")
    lines.append(f"DB: {report['db_path']}")
    if report.get("since"):
        lines.append(f"Since: {report['since']}")
    lines.append("")
    lines.append(report["status_banner"])
    lines.append("")

    if not report["aux_h3_column_present"]:
        lines.append("⚠  paper_signals.aux_h3 column not present — Phase 12B migration has not run.")
        lines.append("    Importing core.paper_validation triggers the migration on next signal log.")
        return "\n".join(lines)

    if report["sniper_total"]["n_total"] == 0:
        lines.append("No SNIPER paper signals in the selected window.")
        return "\n".join(lines)

    s = report["sniper_total"]
    lines.append(f"All SNIPER signals (n={s['n_total']}, closed={s['n_closed']}, open={s['n_open']})")
    if s["n_closed"]:
        lines.append(f"  WR {s['wr_pct']}%  ·  avg adj {s['avg_adj_pct']}%  ·  "
                     f"stop-hit {s['stop_hit_pct']}%  ·  target-hit {s['target_hit_pct']}%")
    lines.append(f"  signal_status counts: {s['by_signal_status']}")
    lines.append("")

    h = report["h3_candidates"]
    lines.append(f"H3 candidates (n={h['n_total']}, closed={h['n_closed']}, open={h['n_open']})")
    if h["n_closed"]:
        lines.append(f"  WR {h['wr_pct']}%  ·  avg adj {h['avg_adj_pct']}%  ·  "
                     f"stop-hit {h['stop_hit_pct']}%  ·  target-hit {h['target_hit_pct']}%")
    elif h["n_total"]:
        lines.append("  (open only — no closed H3 candidates yet)")
    else:
        lines.append("  (none yet — H3 cohort empty in window)")
    lines.append("")

    nh = report["non_h3_sniper"]
    lines.append(f"Non-H3 SNIPER signals — control cohort (n={nh['n_total']}, closed={nh['n_closed']})")
    if nh["n_closed"]:
        lines.append(f"  WR {nh['wr_pct']}%  ·  avg adj {nh['avg_adj_pct']}%  ·  "
                     f"stop-hit {nh['stop_hit_pct']}%  ·  target-hit {nh['target_hit_pct']}%")
    lines.append("")

    if h["n_closed"] and nh["n_closed"]:
        lines.append("H3 vs non-H3 comparison (closed cohorts)")
        try:
            d_wr = (h["wr_pct"] or 0) - (nh["wr_pct"] or 0)
            d_adj = (h["avg_adj_pct"] or 0) - (nh["avg_adj_pct"] or 0)
            lines.append(f"  Δ WR: {d_wr:+.2f}pp  ·  Δ avg adj: {d_adj:+.2f}pp")
        except Exception:
            lines.append("  (delta computation failed — likely None)")
        lines.append("")

    g = report["gate_fail_attribution"]
    lines.append("Gate-fail attribution across all SNIPER signals "
                 "(why each non-H3 signal failed the cohort)")
    for k, v in g.items():
        lines.append(f"  {k}: {v}")
    lines.append("")

    m = report["missing_metadata_counts"]
    lines.append(f"Auxiliary metadata coverage "
                 f"(rows with aux_h3 = {m['__rows_with_aux_h3']}, "
                 f"without = {m['__rows_without_aux_h3']})")
    for k, v in m.items():
        if k.startswith("__"):
            continue
        lines.append(f"  {k}: missing in {v} rows")
    lines.append("")

    lines.append("Verdict: SNIPER H3 is FORWARD-INSTRUMENTED, NOT VALIDATED.")
    lines.append("No promotion. Re-evaluate after ~6 months of forward accrual or "
                 f"when n_closed reaches {TARGET_CLOSED_LOWER}–{TARGET_CLOSED_UPPER}.")
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB_PATH_DEFAULT))
    ap.add_argument("--since", default=None, help="Only include signals logged on/after this date (YYYY-MM-DD)")
    ap.add_argument("--json", action="store_true", help="Print JSON instead of text")
    ap.add_argument("--out", default=str(OUT_JSON), help="Always write JSON to this path")
    args = ap.parse_args(argv)

    db_path = Path(args.db)
    con = _open_db(db_path)
    has_aux = _has_aux_h3_column(con)

    rows = load_sniper_rows(con, args.since) if has_aux else []
    h3_rows = [r for r in rows if _is_h3_candidate(r)]
    non_h3_rows = [r for r in rows if not _is_h3_candidate(r)]

    h3_stats = cohort_stats(h3_rows)
    sniper_stats = cohort_stats(rows)
    non_h3_stats = cohort_stats(non_h3_rows)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "phase": "12B — SNIPER H3 forward OOS report",
        "db_path": str(db_path),
        "since": args.since,
        "aux_h3_column_present": has_aux,
        "status_banner": status_banner(h3_stats),
        "thresholds": {
            "target_closed_lower": TARGET_CLOSED_LOWER,
            "target_closed_upper": TARGET_CLOSED_UPPER,
            "wr_target_pct": WR_TARGET_PCT,
        },
        "sniper_total": sniper_stats,
        "h3_candidates": h3_stats,
        "non_h3_sniper": non_h3_stats,
        "gate_fail_attribution": gate_fail_attribution(rows),
        "missing_metadata_counts": missing_metadata_counts(rows),
        "limitations": (
            "Forward instrumentation only. Phase 12B does not change SNIPER "
            "thresholds, scanner logic, paper governance, execution, sleeve "
            "status, or dashboard scoring. The cohort verdict is "
            "INSUFFICIENT_DATA until n_closed ≥ "
            f"{TARGET_CLOSED_LOWER}–{TARGET_CLOSED_UPPER}, at which point "
            "research/sniper_h3_validation.py should be re-run on the live "
            "cohort."
        ),
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, default=str))

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(render_text(report))
    print(f"\n(JSON also written to {out_path})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

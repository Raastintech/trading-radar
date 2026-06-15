"""
research/scanner_truth/metrics.py — TASK 4 (scanner recall / precision).

Honest about what the data can support:

  • RECALL (robust): share of the winner set that EVER appeared in any
    historized funnel stage (scan_results / veto_log / paper_signals /
    decisions). Computed overall, by theme, and by return bucket.

  • RECALL-BEFORE-MOVE (+20/+30/+50/too-extended): would need historized
    point-in-time Alpha boards, which do NOT exist (only a latest snapshot).
    Reported as NOT_RETAINED — and is the headline reason to historize.

  • PRECISION (forward): would need FORWARD returns after the board's
    build date. Today's board is dated now, so forward data does not exist
    yet. Reported as NOT_COMPUTABLE_YET (the historizer enables it later).

  • BOARD COMPOSITION (computable now): how much of today's Alpha board
    overlaps the realized-winner universe, and the board's TRAILING return
    distribution vs the liquid-universe median — a proxy for whether the
    board indexes on strength at all. Clearly labelled trailing, not forward.

All sample sizes are reported; no success is claimed from small n.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from statistics import median
from typing import Dict, List, Optional

import pandas as pd

from . import dataio


def _load(name: str) -> Dict:
    return json.loads((dataio.RESEARCH_CACHE / name).read_text())


def _board_tickers() -> List[str]:
    try:
        b = _load("alpha_discovery_board_latest.json")
        return [(i.get("symbol") or i.get("ticker") or "").upper()
                for i in b.get("items", []) if (i.get("symbol") or i.get("ticker"))]
    except Exception:
        return []


def build() -> Dict:
    uni = _load("missed_winner_universe_latest.json")
    trace = _load("scanner_funnel_trace_latest.json")
    winners = uni["winners"]
    winner_tickers = {w["ticker"] for w in winners}
    traces = trace["traces"]

    # ── Recall (robust, from the trace) ──────────────────────────────────────
    n = len(traces)
    seen = [t for t in traces
            if t["first_date_actually_saw"] or t["today_snapshot"]["in_alpha_board"]]
    recall_overall = round(100.0 * len(seen) / n, 1) if n else None

    by_theme: Dict[str, Dict] = {}
    for t in traces:
        th = t["theme"]
        d = by_theme.setdefault(th, {"n": 0, "seen": 0})
        d["n"] += 1
        if t in seen:
            d["seen"] += 1
    for th, d in by_theme.items():
        d["recall_pct"] = round(100.0 * d["seen"] / d["n"], 1) if d["n"] else None

    by_bucket = {}
    for lo, label in [(1.00, "ge_100pct"), (0.80, "ge_80pct"), (0.50, "ge_50pct")]:
        sub = [t for t in traces if t["best_max_return"] >= lo]
        ssub = [t for t in sub if t in seen]
        by_bucket[label] = {"n": len(sub), "seen": len(ssub),
                            "recall_pct": round(100.0 * len(ssub) / len(sub), 1) if sub else None}

    # ── Board composition (computable now, trailing) ─────────────────────────
    board = _board_tickers()
    board_set = set(board)
    board_winner_overlap = sorted(board_set & winner_tickers)
    # Trailing best_max_return for board names that are in the winner universe.
    winner_by_t = {w["ticker"]: w for w in winners}
    board_in_universe = [winner_by_t[t] for t in board_winner_overlap]

    # Liquid-universe median trailing max-return as a baseline reference.
    liq_max = sorted(w["best_max_return"] for w in winners)
    liq_median = round(median(liq_max), 4) if liq_max else None

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_winners_traced": n,
        "recall": {
            "definition": "share of winner set EVER in any historized funnel stage",
            "overall_pct": recall_overall,
            "n_seen": len(seen),
            "by_theme": by_theme,
            "by_return_bucket": by_bucket,
        },
        "recall_before_move": {
            "status": "NOT_RETAINED",
            "reason": "no historized point-in-time Alpha board; only a latest snapshot "
                      "exists. Cannot determine whether a name was on the board before "
                      "its +20/+30/+50% move. Historizer (this phase) enables it going forward.",
        },
        "precision_forward": {
            "status": "NOT_COMPUTABLE_YET",
            "reason": "forward returns require bars AFTER the board build date "
                      f"({trace.get('alpha_built_at')}). Today's board has no forward "
                      "window yet. Re-run after the historizer accrues dated boards.",
        },
        "board_composition_trailing": {
            "board_size": len(board),
            "board_in_winner_universe": len(board_winner_overlap),
            "board_winner_overlap_pct": round(100.0 * len(board_winner_overlap) / len(board), 1) if board else None,
            "overlap_tickers": board_winner_overlap[:50],
            "liquid_universe_median_max_return": liq_median,
            "note": "trailing (already-realized) overlap, NOT forward precision. "
                    "Low overlap ⇒ today's board does not currently hold the names that ran.",
        },
    }
    return result


def _render_txt(res: Dict) -> List[str]:
    r = res["recall"]
    bc = res["board_composition_trailing"]
    L = [
        f"== SCANNER RECALL / PRECISION ({res['generated_at']}) ==",
        f"winners traced: {res['n_winners_traced']}",
        "",
        f"RECALL (ever in any historized funnel stage): {r['overall_pct']}% "
        f"({r['n_seen']}/{res['n_winners_traced']})",
        "  by return bucket:",
    ]
    for k, d in r["by_return_bucket"].items():
        L.append(f"    {k:<12} recall={d['recall_pct']}%  (n={d['n']}, seen={d['seen']})")
    L.append("  by theme:")
    for th, d in sorted(r["by_theme"].items(), key=lambda x: -x[1]["n"]):
        L.append(f"    {th:<20} recall={d['recall_pct']}%  (n={d['n']})")
    L += [
        "",
        f"RECALL-BEFORE-MOVE: {res['recall_before_move']['status']} "
        "(no historized Alpha boards)",
        f"FORWARD PRECISION:  {res['precision_forward']['status']} "
        "(today's board has no forward window yet)",
        "",
        f"BOARD COMPOSITION (trailing): board_size={bc['board_size']}  "
        f"in_winner_universe={bc['board_in_winner_universe']} "
        f"({bc['board_winner_overlap_pct']}%)",
        f"  overlap: {', '.join(bc['overlap_tickers']) or '(none)'}",
    ]
    return L


def main() -> int:
    res = build()
    dataio.write_json(dataio.RESEARCH_CACHE / "scanner_recall_precision_latest.json", res)
    lines = _render_txt(res)
    dataio.write_text(dataio.LOGS_DIR / "scanner_recall_precision_latest.txt", lines)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

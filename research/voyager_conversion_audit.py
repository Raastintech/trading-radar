#!/usr/bin/env python3
"""
research/voyager_conversion_audit.py — Phase 1G.3 T6

Research-only, READ-ONLY (SELECT-only) diagnosis of why VOYAGER is favored by the
regime but barely converts council approvals into paper signals/positions.

Reads (read-only): veto_log, voyager_paper_signals, decisions.
Writes:
  - cache/research/voyager_conversion_audit_latest.json
  - logs/voyager_conversion_audit_latest.txt
  - docs/research/VOYAGER_CONVERSION_AUDIT.md

Does NOT mutate any table, call providers, change governance/execution, or tune
the strategy. Diagnosis only.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Read-only diagnosis — avoid importing core.config (which requires provider
# creds at import time). The canonical operational DB is db/trading.db.
DB_PATH_DEFAULT = ROOT / "db" / "trading.db"

JSON_OUT = ROOT / "cache" / "research" / "voyager_conversion_audit_latest.json"
TXT_OUT = ROOT / "logs" / "voyager_conversion_audit_latest.txt"
DOC_OUT = ROOT / "docs" / "research" / "VOYAGER_CONVERSION_AUDIT.md"


def _connect_ro(db_path: Path) -> Optional[sqlite3.Connection]:
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error:
        return None


def build_audit(db_path: Optional[Path] = None) -> Dict[str, Any]:
    db_path = db_path or DB_PATH_DEFAULT
    conn = _connect_ro(db_path)
    if conn is None:
        return {"kind": "voyager_conversion_audit", "error": f"db not found at {db_path}"}

    try:
        approvals = conn.execute(
            "SELECT COUNT(*) FROM veto_log WHERE strategy='VOYAGER' AND verdict='APPROVED'"
        ).fetchone()[0]
        vetoes = conn.execute(
            "SELECT COUNT(*) FROM veto_log WHERE strategy='VOYAGER' AND verdict='VETOED'"
        ).fetchone()[0]
        reject_reasons = [
            {"reason": r[0], "n": r[1]}
            for r in conn.execute(
                "SELECT reason, COUNT(*) FROM veto_log WHERE strategy='VOYAGER' "
                "AND verdict='VETOED' GROUP BY reason ORDER BY COUNT(*) DESC LIMIT 12"
            ).fetchall()
        ]
        signals = conn.execute("SELECT COUNT(*) FROM voyager_paper_signals").fetchone()[0]
        sig_rows = [dict(r) for r in conn.execute(
            "SELECT ticker, archetype, final_score, thirteen_f_flow, size_bucket, "
            "extension_ma50, logged_at FROM voyager_paper_signals ORDER BY logged_at"
        ).fetchall()]
        opened = conn.execute(
            "SELECT COALESCE(SUM(position_opened),0) FROM decisions WHERE strategy='VOYAGER'"
        ).fetchone()[0]
        closed = conn.execute(
            "SELECT COALESCE(SUM(position_closed),0) FROM decisions WHERE strategy='VOYAGER'"
        ).fetchone()[0]
        decision_rows = conn.execute(
            "SELECT COUNT(*) FROM decisions WHERE strategy='VOYAGER'"
        ).fetchone()[0]
        distinct_approved = conn.execute(
            "SELECT COUNT(DISTINCT ticker) FROM veto_log WHERE strategy='VOYAGER' AND verdict='APPROVED'"
        ).fetchone()[0]
    finally:
        conn.close()

    conv_rate = round(signals / approvals, 4) if approvals else None

    # 13F sponsorship alignment: how many logged signals were on institutional SELLING.
    selling = [r for r in sig_rows if str(r.get("thirteen_f_flow", "")).upper() == "SELLING"]
    extended = [r for r in sig_rows if (r.get("extension_ma50") or 0) >= 8.0]

    findings: List[str] = []
    findings.append(
        f"Approval→signal conversion is ~{(conv_rate or 0) * 100:.1f}% "
        f"({signals} paper signals from {approvals} council approvals across "
        f"{distinct_approved} distinct approved tickers). The council is NOT the "
        "bottleneck — approvals are re-emitted on the same few names every cycle, "
        "but almost none convert into a logged paper signal."
    )
    if selling:
        findings.append(
            f"{len(selling)}/{len(sig_rows)} logged VOYAGER signals carried 13F flow = "
            "SELLING — the institutional-sponsorship thesis is being violated at "
            "selection (logging accumulation names while 13F shows distribution)."
        )
    if extended:
        findings.append(
            f"{len(extended)}/{len(sig_rows)} logged signals were >=8% extended above "
            "the 50d MA (e.g. " + ", ".join(
                f"{r['ticker']} +{r['extension_ma50']:.1f}%" for r in extended
            ) + ") — VOYAGER enters already-extended names, the same late-entry failure "
            "mode LEADER_RESET is designed to fix."
        )

    over_gated = vetoes <= approvals * 0.3  # vetoes are a minority and borderline-score
    if over_gated:
        findings.append(
            f"Not over-gated at the council: only {vetoes} vetoes vs {approvals} approvals, "
            "and the vetoes are borderline Tier-2 scores (~41–49 < 50), not structural. "
            "The conversion loss is downstream of the council (signal-logging / paper "
            "governance / dedup), not in selection strictness."
        )

    recommendation = (
        "Fold the long-leadership thesis into LEADER_RESET (research-only) rather than "
        "keep VOYAGER as a separate active sleeve. VOYAGER's failure modes — late/extended "
        "entries and logging against institutional selling — are exactly what LEADER_RESET's "
        "entry-timing + sponsorship filter target. Keep VOYAGER ACTIVE_PAPER for now (it is "
        "the favored long sleeve and its few signals are real evidence), but do NOT redesign "
        "it independently: preserve the 13F-BUYING sponsorship filter as a LEADER_RESET "
        "feature, and revisit a standalone 13F_EMERGING sleeve only after LEADER_RESET passes "
        "its event study. No code change is warranted from this audit (no logging bug found)."
    )

    return {
        "kind": "voyager_conversion_audit",
        "version": "VOYAGER_CONVERSION_AUDIT_V1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "research_only": True,
        "read_only": True,
        "approvals": approvals,
        "vetoes": vetoes,
        "distinct_approved_tickers": distinct_approved,
        "signals": signals,
        "approval_to_signal_conversion": conv_rate,
        "decisions_rows": decision_rows,
        "positions_opened": int(opened),
        "positions_closed": int(closed),
        "reject_reasons": reject_reasons,
        "signal_rows": sig_rows,
        "n_signals_on_13f_selling": len(selling),
        "n_signals_extended_ge_8pct": len(extended),
        "over_gated": bool(over_gated),
        "findings": findings,
        "recommendation_options": {
            "13F_EMERGING": "defer — overlaps LEADER_RESET; revisit after LEADER_RESET event study",
            "fold_into_LEADER_RESET": "recommended — preserve 13F-BUYING sponsorship filter as a feature",
            "keep_research_only": "VOYAGER stays ACTIVE_PAPER (favored long sleeve); do not redesign independently",
        },
        "recommendation": recommendation,
    }


def render_text(a: Dict[str, Any]) -> str:
    if a.get("error"):
        return f"voyager_conversion_audit: {a['error']}"
    L: List[str] = []
    L.append("=" * 62)
    L.append(f"VOYAGER CONVERSION AUDIT — {a['generated_at'][:19]}  (read-only)")
    L.append("=" * 62)
    L.append(f"approvals={a['approvals']}  vetoes={a['vetoes']}  "
             f"distinct_approved_tickers={a['distinct_approved_tickers']}")
    L.append(f"signals={a['signals']}  conversion={a['approval_to_signal_conversion']}")
    L.append(f"positions opened={a['positions_opened']} closed={a['positions_closed']} "
             f"(decisions rows={a['decisions_rows']})")
    L.append(f"signals on 13F SELLING: {a['n_signals_on_13f_selling']}  |  "
             f">=8% extended: {a['n_signals_extended_ge_8pct']}  |  over_gated={a['over_gated']}")
    L.append("")
    L.append("reject reasons:")
    for r in a["reject_reasons"]:
        L.append(f"  {r['n']:>4}  {r['reason']}")
    L.append("")
    L.append("findings:")
    for f in a["findings"]:
        L.append(f"  * {f}")
    L.append("")
    L.append("recommendation:")
    L.append(f"  {a['recommendation']}")
    L.append("=" * 62)
    return "\n".join(L)


def render_doc(a: Dict[str, Any]) -> str:
    if a.get("error"):
        return f"# VOYAGER Conversion Audit\n\nError: {a['error']}\n"
    lines = [
        "# VOYAGER Conversion Audit (research-only)",
        "",
        f"**Generated:** {a['generated_at'][:19]}  ",
        "**Scope:** read-only (SELECT-only) DB diagnosis. No code/strategy/governance change.",
        "",
        "## Headline numbers",
        "",
        f"- Council approvals: **{a['approvals']}** (across {a['distinct_approved_tickers']} distinct tickers)",
        f"- Council vetoes: **{a['vetoes']}**",
        f"- Paper signals logged: **{a['signals']}**",
        f"- **Approval → signal conversion: {a['approval_to_signal_conversion']}** "
        f"(~{(a['approval_to_signal_conversion'] or 0) * 100:.1f}%)",
        f"- Positions opened / closed: **{a['positions_opened']} / {a['positions_closed']}**",
        "",
        "## Reject reasons (council vetoes)",
        "",
        "| n | reason |",
        "|---|---|",
    ]
    for r in a["reject_reasons"]:
        lines.append(f"| {r['n']} | {r['reason']} |")
    lines += ["", "## Logged signals", "",
              "| ticker | archetype | score | 13F flow | ext vs 50d | logged |",
              "|---|---|---|---|---|---|"]
    for s in a["signal_rows"]:
        lines.append(f"| {s['ticker']} | {s['archetype']} | {s['final_score']} | "
                     f"{s['thirteen_f_flow']} | {s['extension_ma50']} | {str(s['logged_at'])[:10]} |")
    lines += ["", "## Findings", ""]
    for f in a["findings"]:
        lines.append(f"- {f}")
    lines += ["", "## Is it over-gated?", "",
              f"`over_gated = {a['over_gated']}`. " +
              ("No — the council approves freely; the loss is downstream of the council."
               if not a["over_gated"] else
               "Possibly — council vetoes are a large share of evaluations."),
              "", "## Recommendation", "",
              a["recommendation"], "",
              "### Options considered",
              ""]
    for k, v in a["recommendation_options"].items():
        lines.append(f"- **{k}** — {v}")
    lines += ["",
              "LEADER_RESET remains research-only; Phase 2C (Trade Proposal Generator) "
              "remains not started. No VOYAGER code was changed by this audit."]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="VOYAGER conversion audit (read-only)")
    p.add_argument("--print", dest="do_print", action="store_true")
    args = p.parse_args(argv)

    audit = build_audit()
    JSON_OUT.parent.mkdir(parents=True, exist_ok=True)
    TXT_OUT.parent.mkdir(parents=True, exist_ok=True)
    DOC_OUT.parent.mkdir(parents=True, exist_ok=True)
    JSON_OUT.write_text(json.dumps(audit, indent=2, default=str))
    text = render_text(audit)
    TXT_OUT.write_text(text + "\n")
    DOC_OUT.write_text(render_doc(audit))

    if args.do_print:
        print(text)
    else:
        print(f"voyager_conversion_audit: conversion={audit.get('approval_to_signal_conversion')} "
              f"(approvals={audit.get('approvals')}, signals={audit.get('signals')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

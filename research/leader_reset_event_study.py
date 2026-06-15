#!/usr/bin/env python3
"""
research/leader_reset_event_study.py — Phase 1G.3 T5

Research-only event study for the proposed LEADER_RESET sleeve
(see docs/strategy/LEADER_RESET_V0_SPEC.md). It does NOT activate a paper
sleeve, create paper_signals, register a strategy, or touch governance/execution.

It builds an event dataset from the *existing* Stock Lens forward log
(``data/state/stock_lens_forward_log.jsonl``), which already carries, per
historical snapshot: the entry/leadership/sector/options layers AND the
resolved forward outcomes (5d/10d return, MAE, MFE, return vs SPY). Each
snapshot is classified into a LEADER_RESET candidate state using the v0 gates
as *research filters only*, then forward behavior is compared across cohorts.

Candidate states: RESEARCH_READY, WATCH_RECLAIM, LATE_EXTENDED, BLOCKED, NO_EDGE.

Verdict: NOT_READY | NEED_MORE_DATA | READY_FOR_PAPER_SPEC | REJECT.

Writes:
  - cache/research/leader_reset_event_study_latest.json
  - logs/leader_reset_event_study_latest.txt
  - docs/research/LEADER_RESET_EVENT_STUDY_SUMMARY.md
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import forecast_forward_tracker as fft

# Friction benchmark, shared with the tactical resolver (round-trip pct).
try:
    from research.paper_trades.resolve_tactical_outcomes import ROUND_TRIP_FRICTION_PCT
except Exception:  # pragma: no cover
    ROUND_TRIP_FRICTION_PCT = 0.30

JSON_OUT = ROOT / "cache" / "research" / "leader_reset_event_study_latest.json"
TXT_OUT = ROOT / "logs" / "leader_reset_event_study_latest.txt"
DOC_OUT = ROOT / "docs" / "research" / "LEADER_RESET_EVENT_STUDY_SUMMARY.md"

STATE_READY = "RESEARCH_READY"
STATE_WATCH = "WATCH_RECLAIM"
STATE_LATE = "LATE_EXTENDED"
STATE_BLOCKED = "BLOCKED"
STATE_NO_EDGE = "NO_EDGE"
STATES = (STATE_READY, STATE_WATCH, STATE_LATE, STATE_BLOCKED, STATE_NO_EDGE)

# Activation gates (must all pass before a paper sleeve is even spec'd).
MIN_SAMPLE_READY = 40          # resolved RESEARCH_READY events with a 5d outcome
MAE_FLOOR_PCT = -8.0           # mean MAE must not be worse than this


def _f(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def classify_state(row: Dict[str, Any]) -> Dict[str, Any]:
    """Map a lens snapshot to a LEADER_RESET candidate state using v0 gates as
    research filters. Returns {state, reason}."""
    label = str(row.get("label") or "").lower()
    layers = row.get("layers") or {}
    tech = layers.get("tech") or {}
    entry = layers.get("entry") or {}
    options = layers.get("options") or {}
    market = layers.get("market") or {}
    sector = layers.get("sector") or {}
    hard_caps = row.get("hard_caps_fired") or []

    options_view = str(options.get("view") or "").lower()
    market_view = str(market.get("view") or "").lower()
    market_regime = str(market.get("regime") or "").lower()

    # BLOCKED — hard caps, bearish options hedge, or risk-off/stress regime gate.
    if hard_caps:
        return {"state": STATE_BLOCKED, "reason": f"hard caps fired: {hard_caps}"}
    if "bearish" in options_view and "hedge" in options_view:
        return {"state": STATE_BLOCKED, "reason": "bearish options hedge"}
    if any(t in market_regime or t in market_view for t in ("risk-off", "stress", "bear")):
        return {"state": STATE_BLOCKED, "reason": f"regime gate ({market_regime or market_view})"}

    is_bullish = "bullish" in label
    extended = bool(tech.get("extended")) or "extended" in label \
        or "extended" in str(entry.get("view") or "").lower()

    if not is_bullish:
        return {"state": STATE_NO_EDGE, "reason": f"non-bullish label ({label!r})"}
    if extended:
        return {"state": STATE_LATE, "reason": "leader but entry too extended"}

    actionable = entry.get("actionable_now")
    if actionable:
        return {"state": STATE_READY, "reason": "bullish leader, entry actionable (reclaim-ready)"}
    return {"state": STATE_WATCH, "reason": "bullish leader, entry not yet actionable"}


def _cohort_metrics(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Forward metrics for a cohort. Net = raw minus round-trip friction."""
    def vals(key: str) -> List[float]:
        out = []
        for r in rows:
            v = _f((r.get("outcomes") or {}).get(key))
            if v is not None:
                out.append(v)
        return out

    r5, r10 = vals("return_5d_pct"), vals("return_10d_pct")
    mae5, mfe5 = vals("max_drawdown_5d_pct"), vals("max_favorable_5d_pct")
    rel5 = vals("rel_spy_5d_pct")
    spy5 = vals("spy_5d_pct")

    def agg(v: List[float]) -> Optional[float]:
        return round(statistics.mean(v), 4) if v else None

    def win_rate(v: List[float]) -> Optional[float]:
        return round(sum(1 for x in v if x > 0) / len(v), 4) if v else None

    return {
        "n": len(rows),
        "n_resolved_5d": len(r5),
        "n_resolved_10d": len(r10),
        "expectancy_5d_raw": agg(r5),
        "expectancy_10d_raw": agg(r10),
        "expectancy_5d_net": (round(statistics.mean(r5) - ROUND_TRIP_FRICTION_PCT, 4) if r5 else None),
        "expectancy_10d_net": (round(statistics.mean(r10) - ROUND_TRIP_FRICTION_PCT, 4) if r10 else None),
        "win_rate_5d": win_rate(r5),
        "win_rate_10d": win_rate(r10),
        "mean_mae_5d": agg(mae5),
        "mean_mfe_5d": agg(mfe5),
        "mean_rel_spy_5d": agg(rel5),
        "mean_spy_5d_baseline": agg(spy5),
    }


def _verdict(cohorts: Dict[str, Any]) -> Dict[str, Any]:
    ready = cohorts.get(STATE_READY, {})
    n_ready = ready.get("n_resolved_5d", 0) or 0
    e5 = ready.get("expectancy_5d_net")
    e10 = ready.get("expectancy_10d_net")
    rel5 = ready.get("mean_rel_spy_5d")
    mae = ready.get("mean_mae_5d")

    blockers: List[str] = []
    if n_ready < MIN_SAMPLE_READY:
        blockers.append(
            f"RESEARCH_READY resolved-5d sample {n_ready} < required {MIN_SAMPLE_READY}"
        )

    if n_ready < MIN_SAMPLE_READY:
        verdict = "NEED_MORE_DATA"
        rationale = (
            "Not enough resolved RESEARCH_READY events to evaluate edge. The system "
            "rarely produces an actionable bullish-leader entry, so the sleeve cannot "
            "yet be accepted or rejected. Accumulate more clean-epoch evidence and "
            "let forward outcomes mature (next maturity 2026-05-28)."
        )
        return {"verdict": verdict, "rationale": rationale, "blockers": blockers,
                "gates": _gate_table(n_ready, e5, e10, rel5, mae)}

    # Sample is adequate — judge edge.
    positive = (e5 is not None and e5 > 0) and (e10 is not None and e10 > 0)
    beats_spy = rel5 is not None and rel5 > 0
    mae_ok = mae is not None and mae > MAE_FLOOR_PCT

    if positive and beats_spy and mae_ok:
        verdict, rationale = "READY_FOR_PAPER_SPEC", "Positive net 5d/10d expectancy, beats SPY, MAE acceptable."
    elif (e5 is not None and e5 < 0) and (e10 is not None and e10 < 0):
        verdict, rationale = "REJECT", "Negative net expectancy at both horizons on an adequate sample."
    else:
        verdict, rationale = "NOT_READY", "Adequate sample but edge is mixed/marginal; refine the spec before paper."
        if not positive:
            blockers.append("net expectancy not positive at both 5d and 10d")
        if not beats_spy:
            blockers.append("does not beat SPY baseline (rel_spy_5d <= 0)")
        if not mae_ok:
            blockers.append(f"mean MAE {mae} worse than floor {MAE_FLOOR_PCT}")

    return {"verdict": verdict, "rationale": rationale, "blockers": blockers,
            "gates": _gate_table(n_ready, e5, e10, rel5, mae)}


def _gate_table(n_ready, e5, e10, rel5, mae) -> List[Dict[str, Any]]:
    return [
        {"gate": "min_sample", "need": f">= {MIN_SAMPLE_READY} resolved RESEARCH_READY 5d", "got": n_ready,
         "pass": (n_ready or 0) >= MIN_SAMPLE_READY},
        {"gate": "net_5d_expectancy_positive", "need": "> 0", "got": e5, "pass": bool(e5 is not None and e5 > 0)},
        {"gate": "net_10d_expectancy_positive", "need": "> 0", "got": e10, "pass": bool(e10 is not None and e10 > 0)},
        {"gate": "beats_spy_5d", "need": "rel_spy_5d > 0", "got": rel5, "pass": bool(rel5 is not None and rel5 > 0)},
        {"gate": "mae_acceptable", "need": f"mean MAE > {MAE_FLOOR_PCT}", "got": mae,
         "pass": bool(mae is not None and mae > MAE_FLOOR_PCT)},
    ]


def build_event_study() -> Dict[str, Any]:
    rows = fft.load_stock_lens_log()

    buckets: Dict[str, List[Dict[str, Any]]] = {s: [] for s in STATES}
    classified: List[Dict[str, Any]] = []
    for row in rows:
        c = classify_state(row)
        row_state = c["state"]
        buckets[row_state].append(row)
        classified.append({
            "ticker": row.get("ticker"),
            "anchor_date": row.get("anchor_date"),
            "state": row_state,
            "reason": c["reason"],
            "label": row.get("label"),
            "return_5d_pct": (row.get("outcomes") or {}).get("return_5d_pct"),
            "return_10d_pct": (row.get("outcomes") or {}).get("return_10d_pct"),
        })

    cohorts = {s: _cohort_metrics(buckets[s]) for s in STATES}
    pooled = _cohort_metrics(rows)  # random-liquid-control proxy: all lens names pooled

    # Secondary view: the existing entry validator's `view` is the closest proxy
    # for a reclaim trigger. `actionable_now` is the v0 RESEARCH_READY gate but it
    # is essentially never True in the log, so we also report entry.view cohorts.
    n_actionable = sum(
        1 for r in rows if (r.get("layers") or {}).get("entry", {}).get("actionable_now")
    )
    view_buckets: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        v = str((row.get("layers") or {}).get("entry", {}).get("view") or "Unknown")
        view_buckets.setdefault(v, []).append(row)
    entry_view_cohorts = {v: _cohort_metrics(rs) for v, rs in view_buckets.items()}

    verdict = _verdict(cohorts)

    key_findings = [
        (f"RESEARCH_READY = {cohorts[STATE_READY]['n']}: the Stock Lens entry validator "
         f"reported actionable_now=True in only {n_actionable}/{len(rows)} snapshots. The "
         "existing entry layer never emits an actionable reclaim — so LEADER_RESET's "
         "trigger must be built fresh; the validator alone will not produce entries. "
         "This is the structural reason the system opens almost nothing."),
        ("In this bull-tape sample, LATE_EXTENDED forward returns "
         f"(exp5d_net={cohorts[STATE_LATE]['expectancy_5d_net']}, "
         f"rel_spy_5d={cohorts[STATE_LATE]['mean_rel_spy_5d']}) BEAT WATCH_RECLAIM "
         f"(exp5d_net={cohorts[STATE_WATCH]['expectancy_5d_net']}, "
         f"rel_spy_5d={cohorts[STATE_WATCH]['mean_rel_spy_5d']}). Momentum outran reset "
         "in-sample — a real thesis risk. The reset premise must be tested across "
         "regimes (incl. risk-off) in the formal backtest before activation."),
        ("Closest existing reclaim proxies in entry.view: "
         f"'Pullback Forming' n={len(view_buckets.get('Pullback Forming', []))}, "
         f"'Watch Reclaim' n={len(view_buckets.get('Watch Reclaim', []))}."),
    ]

    return {
        "kind": "leader_reset_event_study",
        "version": "LEADER_RESET_EVENT_STUDY_V1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "research_only": True,
        "source": "data/state/stock_lens_forward_log.jsonl",
        "n_snapshots": len(rows),
        "friction_round_trip_pct": ROUND_TRIP_FRICTION_PCT,
        "state_counts": {s: cohorts[s]["n"] for s in STATES},
        "cohorts": cohorts,
        "pooled_control": pooled,
        "n_actionable_now": n_actionable,
        "entry_view_cohorts": entry_view_cohorts,
        "key_findings": key_findings,
        "verdict": verdict["verdict"],
        "verdict_rationale": verdict["rationale"],
        "verdict_blockers": verdict["blockers"],
        "activation_gates": verdict["gates"],
        "activation_gate_doctrine": [
            f"minimum sample: >= {MIN_SAMPLE_READY} resolved RESEARCH_READY 5d events",
            "positive net 5d AND 10d expectancy (after round-trip friction)",
            "beats SPY baseline (rel_spy_5d > 0) and pooled random-liquid control",
            f"acceptable MAE (mean 5d MAE > {MAE_FLOOR_PCT}%)",
            "clean_epoch remains ready; forward resolver healthy (see forward_resolution_health)",
            "no concentration / slippage red flags",
        ],
        "examples_research_ready": [c for c in classified if c["state"] == STATE_READY][:10],
    }


def render_text(s: Dict[str, Any]) -> str:
    L: List[str] = []
    L.append("=" * 66)
    L.append(f"LEADER_RESET EVENT STUDY — {s['generated_at'][:19]}  (research-only)")
    L.append("=" * 66)
    L.append(f"source: {s['source']}  |  snapshots: {s['n_snapshots']}")
    L.append(f"state counts: {s['state_counts']}")
    L.append("")
    hdr = f"{'state':<16}{'n':>5}{'n5d':>6}{'exp5d_net':>11}{'exp10d_net':>12}{'win5d':>8}{'relSPY5d':>10}{'MAE5d':>8}"
    L.append(hdr)
    L.append("-" * len(hdr))
    for st in STATES:
        c = s["cohorts"][st]
        L.append(f"{st:<16}{c['n']:>5}{c['n_resolved_5d']:>6}"
                 f"{str(c['expectancy_5d_net']):>11}{str(c['expectancy_10d_net']):>12}"
                 f"{str(c['win_rate_5d']):>8}{str(c['mean_rel_spy_5d']):>10}{str(c['mean_mae_5d']):>8}")
    p = s["pooled_control"]
    L.append(f"{'POOLED(ctrl)':<16}{p['n']:>5}{p['n_resolved_5d']:>6}"
             f"{str(p['expectancy_5d_net']):>11}{str(p['expectancy_10d_net']):>12}"
             f"{str(p['win_rate_5d']):>8}{str(p['mean_rel_spy_5d']):>10}{str(p['mean_mae_5d']):>8}")
    L.append("")
    L.append("KEY FINDINGS:")
    for k in s.get("key_findings", []):
        L.append(f"  * {k}")
    L.append("")
    L.append(f"VERDICT: {s['verdict']}")
    L.append(f"  {s['verdict_rationale']}")
    if s["verdict_blockers"]:
        L.append("  blockers:")
        for b in s["verdict_blockers"]:
            L.append(f"    - {b}")
    L.append("")
    L.append("activation gates:")
    for g in s["activation_gates"]:
        L.append(f"    [{'PASS' if g['pass'] else 'FAIL'}] {g['gate']}: need {g['need']}, got {g['got']}")
    L.append("=" * 66)
    return "\n".join(L)


def render_doc(s: Dict[str, Any]) -> str:
    c = s["cohorts"]
    def row(st):
        m = c[st]
        return (f"| {st} | {m['n']} | {m['n_resolved_5d']} | {m['expectancy_5d_net']} | "
                f"{m['expectancy_10d_net']} | {m['win_rate_5d']} | {m['mean_rel_spy_5d']} | {m['mean_mae_5d']} |")
    lines = [
        "# LEADER_RESET — Event Study Summary (research-only)",
        "",
        f"**Generated:** {s['generated_at'][:19]}  ",
        f"**Source:** `{s['source']}` ({s['n_snapshots']} historical lens snapshots)  ",
        f"**Friction:** {s['friction_round_trip_pct']}% round-trip  ",
        "**Status:** Research only. No paper sleeve, no signals, no registry change.",
        "",
        "> This study reuses the Stock Lens forward log, which already carries each",
        "> snapshot's entry/leadership/options layers plus resolved forward outcomes.",
        "> Each snapshot is classified into a LEADER_RESET candidate state using the v0",
        "> gates as research filters, then forward cohorts are compared. It is a fast,",
        "> artifact-based event study — NOT the rigorous point-in-time backtest in",
        "> `LEADER_RESET_VALIDATION_PLAN.md`, which remains a prerequisite for activation.",
        "",
        "## Cohort forward metrics (net of friction)",
        "",
        "| state | n | n(5d resolved) | exp 5d net | exp 10d net | win 5d | rel SPY 5d | mean MAE 5d |",
        "|---|---|---|---|---|---|---|---|",
        row(STATE_READY), row(STATE_WATCH), row(STATE_LATE), row(STATE_BLOCKED), row(STATE_NO_EDGE),
        "",
        f"Pooled control (all lens names): n={s['pooled_control']['n']}, "
        f"exp5d_net={s['pooled_control']['expectancy_5d_net']}, "
        f"rel_spy_5d={s['pooled_control']['mean_rel_spy_5d']}.",
        "",
        "## Key findings",
        "",
    ]
    for k in s.get("key_findings", []):
        lines.append(f"- {k}")
    lines += [
        "",
        f"## Verdict: **{s['verdict']}**",
        "",
        s["verdict_rationale"],
        "",
    ]
    if s["verdict_blockers"]:
        lines.append("**Blockers:**")
        lines += [f"- {b}" for b in s["verdict_blockers"]]
        lines.append("")
    lines.append("## Activation gates (all must pass before a paper sleeve is spec'd)")
    lines.append("")
    for g in s["activation_gates"]:
        lines.append(f"- [{'x' if g['pass'] else ' '}] **{g['gate']}** — need {g['need']}, got `{g['got']}`")
    lines.append("")
    lines.append("## Doctrine reminder")
    lines.append("")
    for d in s["activation_gate_doctrine"]:
        lines.append(f"- {d}")
    lines.append("")
    lines.append("LEADER_RESET stays research-only until this study (and the formal "
                 "validation-plan backtest) clears every gate. Phase 2C (Trade Proposal "
                 "Generator) remains not started.")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="LEADER_RESET event study (research-only)")
    p.add_argument("--print", dest="do_print", action="store_true")
    args = p.parse_args(argv)

    study = build_event_study()
    JSON_OUT.parent.mkdir(parents=True, exist_ok=True)
    TXT_OUT.parent.mkdir(parents=True, exist_ok=True)
    DOC_OUT.parent.mkdir(parents=True, exist_ok=True)
    JSON_OUT.write_text(json.dumps(study, indent=2, default=str))
    text = render_text(study)
    TXT_OUT.write_text(text + "\n")
    DOC_OUT.write_text(render_doc(study))

    if args.do_print:
        print(text)
    else:
        print(f"leader_reset_event_study: verdict {study['verdict']} "
              f"(RESEARCH_READY n={study['state_counts'][STATE_READY]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

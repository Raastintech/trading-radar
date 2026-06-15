"""
research/holdout_feasibility_audit.py — Phase 1G.17 Task 4.

Statistical feasibility check of the pre-registered 2026H2 holdout
(docs/research/PRE_REGISTERED_HOLDOUT_2026H2.md): can it still reach its own
first acceptance criterion — ">= 30 closed trades inside [2026-06-01,
2026-12-01)" — at the emission rates the system is actually producing?

Three Poisson projections, most-pessimistic to most-generous, all documented:

  decision_basis      λ from actual DECISIONS in the last 30 calendar days
                      (the real closed-trade pipeline: signal → council →
                      governance → order → close)
  paper_signal_basis  λ from active-sleeve PAPER SIGNALS in the last 30 days
                      (assumes 100% of paper signals became closable trades —
                      strictly generous)
  replay_basis        λ from the SNIPER gate-replay expectation
                      (sniper_starvation_audit) + VOYAGER's observed paper
                      trickle (generous: replay ≠ live conversion)

Also flags a covenant design issue: VOYAGER's intended hold is 6–18 months,
so VOYAGER trades opened mid-window cannot CLOSE inside the window at all
unless stopped out — the ">=30 closed" bar was written for tactical cadence.

EVIDENCE PRESERVATION: this audit never deletes or mutates holdout evidence
or the covenant document. If restating is recommended, the recommendation is
a PROPOSAL for the operator: the original covenant is designated
STARVED_HOLDOUT_V1 *by reference* in the audit doc, and a draft V2 plan is
written as a separate proposal file. Nothing existing is rewritten.

RESEARCH-ONLY / CACHE-ONLY / READ-ONLY.
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone, date, timedelta
from typing import Dict, List, Optional

from research.scanner_truth import dataio

OUT_JSON = dataio.RESEARCH_CACHE / "holdout_feasibility_audit_latest.json"
OUT_DOC = dataio.REPO / "docs" / "research" / "HOLDOUT_FEASIBILITY_AUDIT.md"
OUT_TXT = dataio.LOGS_DIR / "holdout_feasibility_audit_latest.txt"
V2_PROPOSAL = dataio.REPO / "docs" / "research" / "HOLDOUT_2026H2_V2_RESTATEMENT_PROPOSAL.md"
SCOREBOARD = dataio.RESEARCH_CACHE / "holdout_2026h2_scoreboard_latest.json"
SNIPER_AUDIT = dataio.RESEARCH_CACHE / "sniper_starvation_audit_latest.json"

REQUIRED_CLOSES = 30
WINDOW_START = "2026-06-01"
WINDOW_END = "2026-12-01"
RATE_LOOKBACK_DAYS = 30


def poisson_sf(k_minus_1: int, lam: float) -> float:
    """P(N >= k) = 1 - CDF(k-1) for Poisson(lam), pure-python."""
    if lam <= 0:
        return 0.0
    acc = 0.0
    for i in range(0, k_minus_1 + 1):
        acc += math.exp(-lam + i * math.log(lam) - math.lgamma(i + 1))
    return max(0.0, min(1.0, 1.0 - acc))


def _count_since(sql: str, since: str) -> int:
    with dataio._ro_conn() as con:
        return int(con.execute(sql, (since,)).fetchone()[0] or 0)


def build(today: Optional[str] = None) -> Dict:
    today_d = date.fromisoformat(today) if today else datetime.now(timezone.utc).date()
    start = date.fromisoformat(WINDOW_START)
    end = date.fromisoformat(WINDOW_END)
    days_elapsed = max(0, (today_d - start).days)
    days_remaining = max(0, (end - today_d).days)

    sb: Dict = {}
    try:
        sb = json.loads(SCOREBOARD.read_text())
    except Exception:
        pass

    lookback = (today_d - timedelta(days=RATE_LOOKBACK_DAYS)).isoformat()

    # observed counts
    in_window_signals = (
        _count_since("SELECT count(*) FROM paper_signals WHERE logged_at>=? "
                     "AND strategy IN ('SNIPER','VOYAGER')", WINDOW_START)
        + _count_since("SELECT count(*) FROM voyager_paper_signals "
                       "WHERE logged_at>=?", WINDOW_START))
    in_window_decisions = _count_since(
        "SELECT count(*) FROM decisions WHERE ts>=? AND strategy IN "
        "('SNIPER','VOYAGER')", WINDOW_START)
    in_window_closes = _count_since(
        "SELECT count(*) FROM decisions WHERE ts>=? AND strategy IN "
        "('SNIPER','VOYAGER') AND position_closed=1", WINDOW_START)

    recent_decisions = _count_since(
        "SELECT count(*) FROM decisions WHERE ts>=? AND strategy IN "
        "('SNIPER','VOYAGER')", lookback)
    recent_signals = (
        _count_since("SELECT count(*) FROM paper_signals WHERE logged_at>=? "
                     "AND strategy IN ('SNIPER','VOYAGER')", lookback)
        + _count_since("SELECT count(*) FROM voyager_paper_signals "
                       "WHERE logged_at>=?", lookback))

    # replay expectation (SNIPER) from Task 2 sidecar, if present
    sniper_replay_per_week = None
    try:
        sa = json.loads(SNIPER_AUDIT.read_text())
        sniper_replay_per_week = sa["verdicts"]["expected_emissions_per_week_pool"]
    except Exception:
        pass

    needed = max(0, REQUIRED_CLOSES - in_window_closes)
    bases: Dict[str, Dict] = {}

    def basis(name: str, daily_rate: float, note: str) -> None:
        lam = daily_rate * days_remaining
        bases[name] = {
            "daily_rate": round(daily_rate, 4),
            "projected_additional": round(lam, 1),
            "projected_total_by_deadline": round(in_window_closes + lam, 1),
            "prob_reaching_30_closed": round(poisson_sf(needed - 1, lam), 6)
            if needed > 0 else 1.0,
            "note": note,
        }

    basis("decision_basis", recent_decisions / RATE_LOOKBACK_DAYS,
          "actual decisions (real closed-trade pipeline), last 30d")
    basis("paper_signal_basis", recent_signals / RATE_LOOKBACK_DAYS,
          "active-sleeve paper signals, last 30d; assumes 100% conversion "
          "to closed trades (strictly generous)")
    replay_daily = ((sniper_replay_per_week or 0) / 7.0
                    + recent_signals / RATE_LOOKBACK_DAYS)
    basis("replay_basis", replay_daily,
          "UPPER BOUND ONLY: SNIPER replay expectation + observed VOYAGER "
          "trickle; counterfactual replay emissions have NEVER converted to "
          "a decision in-window, so this is not a planning basis")

    # Viability rests on the paper-signal basis: it already assumes 100%
    # signal→closed-trade conversion (generous — actual conversion in the
    # window is 0/1). The replay basis is reported as an upper bound but
    # deliberately excluded from the verdict: it stacks a second
    # never-observed assumption (counterfactual emissions converting) on
    # top of the first.
    planning_p = bases["paper_signal_basis"]["prob_reaching_30_closed"]
    best_p = max(b["prob_reaching_30_closed"] for b in bases.values())
    viable = planning_p >= 0.20

    emission = {
        "in_window_raw_signals": in_window_signals,
        "in_window_decisions": in_window_decisions,
        "in_window_closed_trades": in_window_closes,
        "scoreboard_raw_signals": {
            s: (sb.get("evidence_summary", {}).get(s, {}) or {}).get("raw_signals")
            for s in ("SNIPER", "VOYAGER")
        },
        "recent_30d_decisions": recent_decisions,
        "recent_30d_paper_signals": recent_signals,
    }

    return {
        "kind": "holdout_feasibility_audit",
        "version": "v1",
        "phase": "1G.17",
        "research_only": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "disclaimer": ("read-only feasibility projection · no holdout "
                       "evidence deleted or mutated · restatement is a "
                       "PROPOSAL requiring operator ratification"),
        "holdout": {
            "doc": "docs/research/PRE_REGISTERED_HOLDOUT_2026H2.md",
            "window": [WINDOW_START, WINDOW_END],
            "required_closed_trades": REQUIRED_CLOSES,
            "days_elapsed": days_elapsed,
            "days_remaining": days_remaining,
            "acceptance_criteria": sb.get("acceptance_criteria"),
        },
        "emission": emission,
        "projections": bases,
        "design_flags": [
            ("VOYAGER hold horizon is 6-18 months; trades opened mid-window "
             "cannot close inside the window except via stop — the '>=30 "
             "closed' bar implicitly assumed tactical cadence"),
            ("SHORT_A was frozen 2026-05-24 (after registration on "
             "2026-05-07); the covenant's third active sleeve no longer "
             "emits, removing its expected contribution"),
        ],
        "verdicts": {
            "statistically_viable": viable,
            "planning_basis_probability": planning_p,
            "best_case_probability": best_p,
            "recommendation": ("RESTATE_AFTER_REPAIR" if not viable else "CONTINUE"),
            "recommendation_detail": (
                "The holdout cannot plausibly reach its own n>=30 bar at "
                "observed emission rates. Recommended sequence: (1) land the "
                "cache-layer data-correctness fix, (2) run the emission-"
                "calibration study and pick gate sets at a 1-3/week research "
                "flow with non-negative forward quality, (3) restate the "
                "holdout as V2 with a fresh pre-registration date and "
                "realistic sample bar. The V1 covenant and all its evidence "
                "remain untouched, designated STARVED_HOLDOUT_V1 by "
                "reference." if not viable else
                "Current rates clear the bar; keep collecting."),
            "old_holdout_designation": "STARVED_HOLDOUT_V1 (by reference — "
                                       "original file unmodified)",
        },
    }


def _render_txt(res: Dict) -> List[str]:
    h, e, v = res["holdout"], res["emission"], res["verdicts"]
    lines = [
        f"HOLDOUT FEASIBILITY AUDIT — {res['generated_at'][:10]} "
        f"(research-only; nothing mutated)",
        "=" * 78,
        f"window {h['window'][0]} → {h['window'][1]}  day {h['days_elapsed']}"
        f"/{h['days_elapsed'] + h['days_remaining']}  requirement: "
        f">={h['required_closed_trades']} closed trades",
        f"in-window: raw_signals={e['in_window_raw_signals']}  "
        f"decisions={e['in_window_decisions']}  closed="
        f"{e['in_window_closed_trades']}",
        f"recent 30d: decisions={e['recent_30d_decisions']}  "
        f"paper_signals={e['recent_30d_paper_signals']}",
        "",
        "projections (Poisson, P(total >= 30 by deadline)):",
    ]
    for name, b in res["projections"].items():
        lines.append(
            f"  {name:20s} rate={b['daily_rate']:7.4f}/day  projected_total="
            f"{b['projected_total_by_deadline']:6.1f}  P(>=30)="
            f"{b['prob_reaching_30_closed']:.4f}")
    lines += [
        "",
        "design flags:",
        *[f"  - {f}" for f in res["design_flags"]],
        "",
        f"VERDICT statistically_viable={v['statistically_viable']}  "
        f"planning_P={v['planning_basis_probability']:.4f}  "
        f"upper_bound_P={v['best_case_probability']:.4f}",
        f"recommendation: {v['recommendation']}",
    ]
    return lines


def _write_docs(res: Dict) -> None:
    h, e, v = res["holdout"], res["emission"], res["verdicts"]
    proj_rows = "\n".join(
        f"| `{n}` | {b['daily_rate']}/day | {b['projected_total_by_deadline']} "
        f"| {b['prob_reaching_30_closed']:.4f} | {b['note']} |"
        for n, b in res["projections"].items())
    OUT_DOC.parent.mkdir(parents=True, exist_ok=True)
    OUT_DOC.write_text(f"""# Holdout Feasibility Audit (Phase 1G.17)

Generated: {res['generated_at'][:19]}Z · research-only · the V1 covenant
(`{h['doc']}`) and all in-window evidence are **unmodified**.

## Question

Can the pre-registered 2026H2 holdout still meet its own first acceptance
criterion — **≥{h['required_closed_trades']} closed trades inside
[{h['window'][0]}, {h['window'][1]})** — at observed emission rates?

## Observed (day {h['days_elapsed']} of {h['days_elapsed'] + h['days_remaining']})

| Metric | Value |
|---|---|
| In-window raw signals (active sleeves) | {e['in_window_raw_signals']} |
| In-window decisions | {e['in_window_decisions']} |
| In-window closed trades | {e['in_window_closed_trades']} |
| Decisions, last 30 days | {e['recent_30d_decisions']} |
| Paper signals, last 30 days | {e['recent_30d_paper_signals']} |

## Projections

| Basis | Rate | Projected total by deadline | P(≥30) | Notes |
|---|---|---|---|---|
{proj_rows}

## Covenant design flags

{chr(10).join('- ' + f for f in res['design_flags'])}

## Verdict

**statistically_viable = {v['statistically_viable']}** (planning-basis
P = {v['planning_basis_probability']:.4f}; replay upper bound
P = {v['best_case_probability']:.4f}, excluded from the verdict because it
stacks a never-observed conversion assumption).

**Recommendation: {v['recommendation']}.** {v['recommendation_detail']}

Designation: the original covenant is referred to as
**STARVED_HOLDOUT_V1** going forward. The file itself is not renamed,
edited, or deleted — pre-registered history is immutable. A draft V2
restatement proposal (operator must ratify; not active) is at
`docs/research/HOLDOUT_2026H2_V2_RESTATEMENT_PROPOSAL.md`.
""")
    if v["recommendation"] == "RESTATE_AFTER_REPAIR" and not V2_PROPOSAL.exists():
        V2_PROPOSAL.write_text(f"""# DRAFT — Holdout 2026H2 V2 Restatement Proposal (NOT ACTIVE)

**Status: PROPOSAL ONLY.** This document has no force until the operator
ratifies it with a new pre-registration date. The V1 covenant
(`docs/research/PRE_REGISTERED_HOLDOUT_2026H2.md`, designated
STARVED_HOLDOUT_V1) remains the active covenant until then, and its
evidence is preserved untouched.

## Why restate

V1's ">=30 closed trades by 2026-12-01" is statistically unreachable at
observed emission (see `docs/research/HOLDOUT_FEASIBILITY_AUDIT.md`,
generated {res['generated_at'][:10]}). Waiting out the window collects
~zero information while paying ~6 months of calendar time.

## Pre-conditions before ratifying V2 (in order)

1. Cache-layer data-correctness fix landed and verified (depth preserved
   across refreshes; no scanner behavior change).
2. Emission-calibration study (`research/emission_calibration_study.py`)
   has a variant per sleeve at 1–3 candidates/week with non-negative
   forward quality vs random; the chosen variant is documented BEFORE the
   new window opens.
3. Recalibrated gates run in paper for >=5 trading days to confirm
   realized emission matches the study estimate (no tuning to force flow).

## Proposed V2 shape (to be finalized at ratification)

- Window: 6 months from ratification date.
- Sample bar: derived from the verified emission rate so that
  P(reach bar | study rate) >= 80%, floor n >= 20 closes.
- All other V1 acceptance criteria (bootstrap lower-CI WR > 50% at 0.30%
  RT friction, beat random-entry control by >=5pp, regime-conditioned WR
  within 5pp) carry over unchanged.
- VOYAGER closes counted at its paper time-stop horizon, not 6-18 month
  thesis horizon (V1 design flag).

## What does NOT change

- No retuning inside the new window once ratified.
- SHORT_A stays frozen; no new sleeve enters via this proposal.
- The live-capital env gate stays OFF regardless (see core/config.py
  three-key gate; this proposal never touches it).
""")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Holdout feasibility audit (1G.17)")
    ap.add_argument("--today", default=None, help="override evaluation date (ISO)")
    args = ap.parse_args(argv)
    res = build(today=args.today)
    dataio.write_json(OUT_JSON, res)
    lines = _render_txt(res)
    dataio.write_text(OUT_TXT, lines)
    _write_docs(res)
    print("\n".join(lines))
    print(f"\nwrote {dataio.rel_to_repo(OUT_JSON)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
research/recall_shadow_lens_feeder.py — Phase 1G.17 Tasks 5 + 7.

RESEARCH-ONLY feeder that routes the top recall-repair shadow-lane candidates
to Stock Lens / Executive Gatekeeper *research review*. It exists because the
shadow lane is currently the only surface with demonstrated forward edge, but
its candidates have no Lens/Gatekeeper artifacts, so the operator cannot
research-review them.

Hard rules (enforced in code and tests):
  * NO paper signals          — never writes paper_signals / voyager_paper_signals
  * NO trade proposals        — output rows are research-review items only
  * NO execution path         — never imports execution/* or council/*
  * NO governance path        — never touches paper governance
  * NO production universe or strategy-registry change
  * NO DB writes of any kind  — DB is opened read-only if at all
  * NO provider calls by default — artifact refresh is a PLAN unless the
    operator passes --execute, and even then the lens refresh (provider-
    heavy) is the only provider touch, run via the existing runner scripts.

Readiness gate (Task 5): the feeder verifies the shadow lane's forward
verdict from its own sidecars before selecting anything. If the documented
gate (verdict READY_TO_FEED_LENS_RESEARCH_ONLY + history maturity + beats
random at 5d AND 10d) does not hold, the feeder emits an empty board with
the failed checks and exits 0 — keep collecting, no routing.

Outputs:
  cache/research/recall_shadow_lens_feeder_latest.json
  logs/recall_shadow_lens_feeder_latest.txt
  data/research/recall_shadow_lens_feeder_history.jsonl  (append-only,
      idempotent per (asof_date, ticker))
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from research.scanner_truth import dataio

LANE_JSON = dataio.RESEARCH_CACHE / "recall_repair_shadow_lane_latest.json"
FWD_JSON = dataio.RESEARCH_CACHE / "recall_repair_shadow_forward_latest.json"
OUT_JSON = dataio.RESEARCH_CACHE / "recall_shadow_lens_feeder_latest.json"
OUT_TXT = dataio.LOGS_DIR / "recall_shadow_lens_feeder_latest.txt"
HISTORY = dataio.HISTORY_DIR / "recall_shadow_lens_feeder_history.jsonl"

REQUIRED_VERDICT = "READY_TO_FEED_LENS_RESEARCH_ONLY"
MIN_HISTORY_DAYS = 10
DEFAULT_CAP = 20
LENS_STALE_H = 24.0
GATEKEEPER_STALE_H = 24.0
# rough per-ticker provider cost of one lens build (FMP profile/fundamentals/
# events + Alpaca bars) — an ESTIMATE for budgeting, not a measured invoice.
EST_PROVIDER_CALLS_PER_LENS = 8

# labels the feeder refuses to route (late/parabolic names are marked
# never-chase by the lane; no-edge rows carry no thesis to review)
EXCLUDED_LABELS = {"SHADOW_NO_EDGE", "SHADOW_LATE_EXTENDED"}


# ── Task 5: readiness verification ────────────────────────────────────────────

def verify_readiness(fwd: Optional[Dict] = None) -> Tuple[bool, Dict]:
    """Documented gate: verdict + history maturity + beats-random at 5d AND
    10d, recomputed from the forward sidecar's own numbers (not just the
    label, so a mislabeled sidecar cannot route candidates)."""
    checks: Dict = {}
    if fwd is None:
        try:
            fwd = json.loads(FWD_JSON.read_text())
        except Exception as exc:
            return False, {"sidecar_readable": f"NO ({exc})"}
    checks["sidecar_readable"] = "yes"

    verdict = str(fwd.get("verdict") or "")
    checks["verdict"] = verdict
    checks["verdict_is_ready"] = verdict.startswith(REQUIRED_VERDICT)

    hist_days = int(fwd.get("history_days") or 0)
    checks["history_days"] = hist_days
    checks["history_mature"] = hist_days >= MIN_HISTORY_DAYS

    by_h = fwd.get("by_horizon") or {}
    beats = {}
    for h in ("5", "10"):
        row = by_h.get(h) or {}
        s, r = row.get("shadow_rel_spy_avg"), row.get("random_rel_spy_avg")
        beats[h] = (s is not None and r is not None and float(s) > float(r))
        checks[f"rel_spy_{h}d_shadow"] = s
        checks[f"rel_spy_{h}d_random"] = r
    checks["beats_random_5d_and_10d"] = beats.get("5", False) and beats.get("10", False)

    ready = (checks["verdict_is_ready"] and checks["history_mature"]
             and checks["beats_random_5d_and_10d"])
    checks["ready"] = ready
    return ready, checks


# ── candidate selection + artifact status ─────────────────────────────────────

def _artifact_status(path: Path, stale_h: float) -> Dict:
    if not path.exists():
        return {"status": "missing", "age_hours": None}
    age_h = (time.time() - path.stat().st_mtime) / 3600.0
    return {"status": "stale" if age_h > stale_h else "fresh",
            "age_hours": round(age_h, 1)}


def select_candidates(lane: Dict, cap: int = DEFAULT_CAP) -> List[Dict]:
    """Top `cap` lane candidates by rank, excluding never-route labels.
    Pure selection — no side effects."""
    rows = []
    for c in lane.get("candidates") or []:
        label = str(c.get("label") or "")
        if label in EXCLUDED_LABELS:
            continue
        rows.append(c)
    rows.sort(key=lambda c: c.get("rank") or 10**9)
    out = []
    for c in rows[:cap]:
        t = str(c.get("ticker") or "").upper()
        lens = _artifact_status(
            dataio.RESEARCH_CACHE / f"stock_lens_{t}_latest.json", LENS_STALE_H)
        gk = _artifact_status(
            dataio.RESEARCH_CACHE / f"executive_gatekeeper_{t}_latest.json",
            GATEKEEPER_STALE_H)
        reviewable = lens["status"] == "fresh" and gk["status"] == "fresh"
        out.append({
            "ticker": t,
            "asof_date": c.get("asof_date"),
            "rank": c.get("rank"),
            "label": c.get("label"),
            "theme": c.get("theme"),
            "sector": c.get("sector"),
            "rank_score": c.get("rank_score"),
            "reason_codes": c.get("reason_codes"),
            "lens": lens,
            "gatekeeper": gk,
            "research_reviewable_now": reviewable,
            "note": ("research review candidate only · NOT a signal · NOT a "
                     "trade proposal · NOT paper evidence"),
        })
    return out


def refresh_plan(cands: List[Dict]) -> Dict:
    need_lens = [c["ticker"] for c in cands if c["lens"]["status"] != "fresh"]
    need_gk = [c["ticker"] for c in cands if c["gatekeeper"]["status"] != "fresh"]
    return {
        "lens_refresh_needed": need_lens,
        "gatekeeper_refresh_needed": need_gk,
        "lens_command": (f"./scripts/run_research_cycle.sh lens "
                         f"{' '.join(need_lens)}" if need_lens else None),
        "gatekeeper_command": (f"./scripts/run_research_cycle.sh gatekeeper "
                               f"{' '.join(need_gk)}" if need_gk else None),
        "expected_provider_calls": len(need_lens) * EST_PROVIDER_CALLS_PER_LENS,
        "provider_cost_note": ("lens refresh is PROVIDER-heavy "
                               f"(~{EST_PROVIDER_CALLS_PER_LENS} calls/ticker, "
                               "estimate); gatekeeper refresh is cache-only "
                               "(0 provider calls)"),
        "executed": False,
    }


def execute_refresh(plan: Dict, py: str, repo: Path) -> Dict:
    """Operator-invoked (--execute) refresh via the existing scripts. The
    feeder itself still makes no provider calls — it shells to the same
    runners the operator would use by hand."""
    results = []
    for t in plan["lens_refresh_needed"]:
        r = subprocess.run([py, "research/regime_forecast.py", "--ticker", t],
                           cwd=repo, capture_output=True, text=True, timeout=600)
        results.append({"ticker": t, "step": "lens", "rc": r.returncode})
    for t in plan["gatekeeper_refresh_needed"]:
        r = subprocess.run([py, "research/executive_gatekeeper_report.py",
                            "--ticker", t],
                           cwd=repo, capture_output=True, text=True, timeout=600)
        results.append({"ticker": t, "step": "gatekeeper", "rc": r.returncode})
    plan = dict(plan)
    plan["executed"] = True
    plan["execution_results"] = results
    return plan


# ── history (append-only, idempotent) ─────────────────────────────────────────

def historize(cands: List[Dict], history_path: Path = HISTORY) -> int:
    seen = {(r.get("asof_date"), r.get("ticker"))
            for r in dataio.read_jsonl(history_path)}
    fresh = [
        {"asof_date": c["asof_date"], "ticker": c["ticker"],
         "rank": c["rank"], "label": c["label"], "theme": c["theme"],
         "rank_score": c["rank_score"],
         "routed_at": datetime.now(timezone.utc).isoformat(),
         "kind": "lens_feeder_route", "research_only": True}
        for c in cands if (c["asof_date"], c["ticker"]) not in seen
    ]
    return dataio.append_jsonl(history_path, fresh) if fresh else 0


# ── build ─────────────────────────────────────────────────────────────────────

def build(cap: int = DEFAULT_CAP, execute: bool = False) -> Dict:
    ready, checks = verify_readiness()
    base = {
        "kind": "recall_shadow_lens_feeder",
        "version": "v1",
        "phase": "1G.17",
        "research_only": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "disclaimer": ("research-review routing only · NO paper signals, NO "
                       "trade proposals, NO execution/governance/universe/"
                       "registry change, NO DB writes"),
        "readiness": checks,
        "cap": cap,
    }
    if not ready:
        base.update({
            "candidates": [],
            "refresh_plan": None,
            "action": ("NOT ROUTING — readiness gate failed; keep collecting "
                       "until the documented gate passes"),
        })
        return base

    try:
        lane = json.loads(LANE_JSON.read_text())
    except Exception as exc:
        base.update({"candidates": [], "refresh_plan": None,
                     "action": f"NOT ROUTING — lane sidecar unreadable: {exc}"})
        return base

    cands = select_candidates(lane, cap=cap)
    plan = refresh_plan(cands)
    if execute and (plan["lens_refresh_needed"] or plan["gatekeeper_refresh_needed"]):
        py = str(dataio.REPO / ".venv" / "bin" / "python")
        plan = execute_refresh(plan, py, dataio.REPO)
        # re-read artifact status after refresh
        cands = select_candidates(lane, cap=cap)

    routed = historize(cands)
    base.update({
        "lane_asof": lane.get("asof_date"),
        "candidates": cands,
        "n_reviewable_now": sum(1 for c in cands if c["research_reviewable_now"]),
        "refresh_plan": plan,
        "history_rows_appended": routed,
        "action": ("ROUTED to research review (no signals); refresh artifacts "
                   "via the plan commands or rerun with --execute"),
    })
    return base


def _render_txt(res: Dict) -> List[str]:
    lines = [
        f"RECALL-SHADOW → LENS/GATEKEEPER FEEDER — {res['generated_at'][:10]} "
        f"(research-only; no signals)",
        "=" * 78,
        f"readiness: ready={res['readiness'].get('ready')}  "
        f"verdict={res['readiness'].get('verdict')}  "
        f"history_days={res['readiness'].get('history_days')}  "
        f"beats_random_5d_and_10d="
        f"{res['readiness'].get('beats_random_5d_and_10d')}",
        f"action: {res['action']}",
        "",
    ]
    for c in res.get("candidates") or []:
        lines.append(
            f"  #{c['rank']:<3} {c['ticker']:6s} {c['label']:24s} "
            f"theme={c['theme'] or '—':16s} lens={c['lens']['status']:7s} "
            f"gatekeeper={c['gatekeeper']['status']:7s} "
            f"reviewable={'YES' if c['research_reviewable_now'] else 'no'}")
    plan = res.get("refresh_plan")
    if plan:
        lines += [
            "",
            f"refresh plan: lens={len(plan['lens_refresh_needed'])} tickers  "
            f"gatekeeper={len(plan['gatekeeper_refresh_needed'])}  "
            f"est provider calls={plan['expected_provider_calls']}  "
            f"executed={plan['executed']}",
        ]
        if plan.get("lens_command"):
            lines.append(f"  {plan['lens_command']}")
        if plan.get("gatekeeper_command"):
            lines.append(f"  {plan['gatekeeper_command']}")
    return lines


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Recall-shadow → Lens research-only feeder (1G.17)")
    ap.add_argument("--cap", type=int, default=DEFAULT_CAP)
    ap.add_argument("--execute", action="store_true",
                    help="actually run the artifact refresh (lens step is "
                         "provider-heavy); default is plan-only")
    args = ap.parse_args(argv)
    res = build(cap=args.cap, execute=args.execute)
    dataio.write_json(OUT_JSON, res)
    lines = _render_txt(res)
    dataio.write_text(OUT_TXT, lines)
    print("\n".join(lines))
    print(f"\nwrote {dataio.rel_to_repo(OUT_JSON)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

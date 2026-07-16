"""
research/forward_evidence_milestones.py — forward-evidence re-audit hook.

LLM audit queue task 52439f07a3b5: "Add a forward-evidence re-audit hook
that fires when the first 20d cohort and >=10 shortlist 10d episodes
mature."

Watches two maturity milestones and, on the run where one first crosses,
flags a RE-AUDIT DUE so the digest and journal audit re-examine the
forward evidence with the newly matured sample instead of coasting on a
verdict computed before the data existed:

  first_20d_cohort_matured   — general forward tracker reports >0 matured
                               20d episodes (research_forward_latest.json)
  shortlist_10d_matured_floor — high-conviction shortlist has >=10 matured
                               10d episodes, the pre-registered verdict
                               floor (high_conviction_forward_latest.json)

"Fires" means: writes reaudit_due=true into the sidecar that the nightly
digest and journal audit read on the same cycle.  Nothing auto-executes
and no verdict is changed here — per standing doctrine, a human (or the
nightly audit) acts on the flag.

Crossings are persisted write-once in
data/research/forward_evidence_milestones_state.json so the flag fires on
the crossing date only; later runs report the milestone as crossed with
its date.  A metric that later regresses below its threshold never
un-crosses (history is immutable evidence).

RESEARCH-ONLY / CACHE-ONLY: no provider calls, no DB access, no signals.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
RESEARCH_CACHE = REPO_ROOT / "cache" / "research"
OUT_JSON = RESEARCH_CACHE / "forward_evidence_milestones_latest.json"
OUT_TXT = REPO_ROOT / "logs" / "forward_evidence_milestones_latest.txt"

SHORTLIST_10D_FLOOR = 10  # mirrors high_conviction_forward.MIN_MATURED_FOR_VERDICT

_REASON_N_RE = re.compile(r"only\s+(\d+)\s+matured 10d shortlist")


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _matured_20d(forward: Optional[Dict[str, Any]]) -> Optional[int]:
    if not forward:
        return None
    mbh = (forward.get("overall") or {}).get("matured_by_horizon") or {}
    v = mbh.get("20d")
    return int(v) if v is not None else None


def _shortlist_matured_10d(hc_fwd: Optional[Dict[str, Any]]) -> Optional[int]:
    if not hc_fwd or not hc_fwd.get("present"):
        return None
    try:
        n = hc_fwd["cohorts"]["full_shortlist"]["10d"]["vs_spy"]["n"]
        if n is not None:
            return int(n)
    except Exception:
        pass
    m = _REASON_N_RE.search(str(hc_fwd.get("verdict_reason") or ""))
    return int(m.group(1)) if m else None


def build_report(root: Optional[Path] = None,
                 today: Optional[str] = None) -> Dict[str, Any]:
    """Detect milestone crossings.  Pure computation apart from the
    write-once state file; today override is for tests."""
    root = root or REPO_ROOT
    cache = root / "cache" / "research"
    state_path = root / "data" / "research" / "forward_evidence_milestones_state.json"
    today_s = today or datetime.now(timezone.utc).date().isoformat()
    now_iso = datetime.now(timezone.utc).isoformat()

    forward = _load_json(cache / "research_forward_latest.json")
    hc_fwd = _load_json(cache / "high_conviction_forward_latest.json")

    specs = [
        {
            "id": "first_20d_cohort_matured",
            "label": "first 20d cohort matured (general forward tracker)",
            "value": _matured_20d(forward),
            "threshold": 1,
            "source": "research_forward_latest.json",
        },
        {
            "id": "shortlist_10d_matured_floor",
            "label": (f"shortlist 10d matured episodes reached the "
                      f"≥{SHORTLIST_10D_FLOOR} verdict floor"),
            "value": _shortlist_matured_10d(hc_fwd),
            "threshold": SHORTLIST_10D_FLOOR,
            "source": "high_conviction_forward_latest.json",
        },
    ]

    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        state = {}

    milestones: List[Dict[str, Any]] = []
    newly_crossed: List[str] = []
    state_changed = False
    for spec in specs:
        rec = dict(spec)
        prior = state.get(spec["id"]) or {}
        crossed_at = prior.get("crossed_at")
        value = spec["value"]
        meets = value is not None and value >= spec["threshold"]
        if crossed_at:
            rec["crossed"] = True
            rec["crossed_at"] = crossed_at
        elif meets:
            rec["crossed"] = True
            rec["crossed_at"] = today_s
            state[spec["id"]] = {"crossed_at": today_s,
                                 "value_at_crossing": value,
                                 "recorded_at": now_iso}
            state_changed = True
            newly_crossed.append(spec["id"])
        else:
            rec["crossed"] = False
            rec["crossed_at"] = None
        milestones.append(rec)

    if state_changed:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, indent=2, sort_keys=True),
                              encoding="utf-8")

    # RE-AUDIT DUE on the crossing date only (idempotent for same-day
    # re-runs: crossed_at == today keeps the flag up all day).
    due = [m["id"] for m in milestones if m["crossed"]
           and m["crossed_at"] == today_s]
    return {
        "kind": "forward_evidence_milestones",
        "version": 1,
        "generated_at": now_iso,
        "research_only": True,
        "disclaimer": ("Re-audit hook only — flags maturity milestones for "
                       "the nightly digest/audit; changes no verdict, gate, "
                       "or ranking."),
        "milestones": milestones,
        "newly_crossed": newly_crossed,
        "reaudit_due": bool(due),
        "reaudit_due_for": due,
    }


def _render_txt(report: Dict[str, Any]) -> str:
    lines = [f"FORWARD-EVIDENCE MILESTONES ({report['generated_at']})",
             f"  reaudit_due: {report['reaudit_due']}"]
    for m in report["milestones"]:
        status = (f"CROSSED {m['crossed_at']}" if m["crossed"]
                  else "not crossed")
        lines.append(f"  {m['id']}: value={m['value']} "
                     f"threshold>={m['threshold']} -> {status}")
    return "\n".join(lines) + "\n"


def main() -> int:
    report = build_report()
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(report, indent=2), encoding="utf-8")
    OUT_TXT.parent.mkdir(parents=True, exist_ok=True)
    OUT_TXT.write_text(_render_txt(report), encoding="utf-8")
    print(_render_txt(report), end="")
    print(f"written: {OUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

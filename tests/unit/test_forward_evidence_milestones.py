"""
Forward-evidence re-audit hook (LLM audit queue task 52439f07a3b5).

Covers:
  1. milestone crossing detection + write-once state
  2. same-day idempotence and next-day quieting of reaudit_due
  3. missing sidecars never cross (no fabrication)
  4. shortlist floor from the structured cohort field and the
     verdict_reason fallback
  5. metric regression never un-crosses a recorded milestone
  6. digest rendering (RE-AUDIT DUE on crossing date, dated fact after)
  7. rule-based journal audit picks up the digest line as a P0 action
  8. module stays cache-only (no provider/execution imports)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import research.forward_evidence_milestones as fem  # noqa: E402


def _write(root: Path, name: str, obj) -> None:
    d = root / "cache" / "research"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(json.dumps(obj), encoding="utf-8")


def _forward_sidecar(n_20d) -> dict:
    return {"overall": {"matured_by_horizon": {"5d": 100, "20d": n_20d}}}


def _hc_sidecar(n_10d, structured: bool = True) -> dict:
    base = {"present": True, "verdict": "NEED_MORE_DATA",
            "verdict_reason": (f"only {n_10d} matured 10d shortlist "
                               "episodes (need ≥10)")}
    if structured:
        base["cohorts"] = {"full_shortlist": {
            "10d": {"vs_spy": {"n": n_10d}}}}
    return base


def test_first_20d_crossing_fires_and_persists(tmp_path):
    _write(tmp_path, "research_forward_latest.json", _forward_sidecar(32))
    _write(tmp_path, "high_conviction_forward_latest.json", _hc_sidecar(0))
    rep = fem.build_report(tmp_path, today="2026-07-16")
    by_id = {m["id"]: m for m in rep["milestones"]}
    assert by_id["first_20d_cohort_matured"]["crossed"] is True
    assert by_id["first_20d_cohort_matured"]["crossed_at"] == "2026-07-16"
    assert by_id["shortlist_10d_matured_floor"]["crossed"] is False
    assert rep["reaudit_due"] is True
    assert rep["reaudit_due_for"] == ["first_20d_cohort_matured"]
    state = json.loads((tmp_path / "data" / "research" /
                        "forward_evidence_milestones_state.json").read_text())
    assert state["first_20d_cohort_matured"]["crossed_at"] == "2026-07-16"
    assert state["first_20d_cohort_matured"]["value_at_crossing"] == 32


def test_same_day_rerun_keeps_flag_next_day_quiets(tmp_path):
    _write(tmp_path, "research_forward_latest.json", _forward_sidecar(32))
    _write(tmp_path, "high_conviction_forward_latest.json", _hc_sidecar(0))
    fem.build_report(tmp_path, today="2026-07-16")
    again = fem.build_report(tmp_path, today="2026-07-16")
    assert again["reaudit_due"] is True          # same-day rerun: flag stays
    assert again["newly_crossed"] == []          # but state not re-written
    later = fem.build_report(tmp_path, today="2026-07-17")
    assert later["reaudit_due"] is False
    by_id = {m["id"]: m for m in later["milestones"]}
    assert by_id["first_20d_cohort_matured"]["crossed_at"] == "2026-07-16"


def test_missing_sidecars_never_cross(tmp_path):
    rep = fem.build_report(tmp_path, today="2026-07-16")
    assert all(m["crossed"] is False for m in rep["milestones"])
    assert all(m["value"] is None for m in rep["milestones"])
    assert rep["reaudit_due"] is False
    assert not (tmp_path / "data" / "research" /
                "forward_evidence_milestones_state.json").exists()


def test_shortlist_floor_structured_and_fallback(tmp_path):
    _write(tmp_path, "research_forward_latest.json", _forward_sidecar(0))
    _write(tmp_path, "high_conviction_forward_latest.json", _hc_sidecar(12))
    rep = fem.build_report(tmp_path, today="2026-07-20")
    by_id = {m["id"]: m for m in rep["milestones"]}
    assert by_id["shortlist_10d_matured_floor"]["crossed"] is True
    assert by_id["first_20d_cohort_matured"]["crossed"] is False

    # fallback path: no structured cohort field, parse verdict_reason
    hc = _hc_sidecar(11, structured=False)
    _write(tmp_path, "high_conviction_forward_latest.json", hc)
    assert fem._shortlist_matured_10d(hc) == 11


def test_regression_never_uncrosses(tmp_path):
    _write(tmp_path, "research_forward_latest.json", _forward_sidecar(32))
    _write(tmp_path, "high_conviction_forward_latest.json", _hc_sidecar(0))
    fem.build_report(tmp_path, today="2026-07-16")
    # tracker artifact later regresses to 0 matured 20d (e.g. restatement)
    _write(tmp_path, "research_forward_latest.json", _forward_sidecar(0))
    rep = fem.build_report(tmp_path, today="2026-07-18")
    by_id = {m["id"]: m for m in rep["milestones"]}
    assert by_id["first_20d_cohort_matured"]["crossed"] is True
    assert by_id["first_20d_cohort_matured"]["crossed_at"] == "2026-07-16"


def test_digest_milestone_lines_due_and_after():
    import dashboards.research_command_center.journal_digest as jd
    fm = {"milestones": [
            {"id": "first_20d_cohort_matured", "crossed": True,
             "crossed_at": "2026-07-16", "value": 32,
             "label": "first 20d cohort matured (general forward tracker)"},
            {"id": "shortlist_10d_matured_floor", "crossed": False,
             "crossed_at": None, "value": 0, "label": "shortlist floor"}],
          "reaudit_due": True,
          "reaudit_due_for": ["first_20d_cohort_matured"]}
    lines = jd._milestone_lines({"forward_milestones": fm})
    assert len(lines) == 1
    assert lines[0].startswith("- RE-AUDIT DUE")
    assert "crossed 2026-07-16" in lines[0]

    fm["reaudit_due"] = False
    fm["reaudit_due_for"] = []
    lines = jd._milestone_lines({"forward_milestones": fm})
    assert lines[0].startswith("- Forward-evidence milestone:")
    # sidecar missing -> no lines, no crash
    assert jd._milestone_lines({"forward_milestones": None}) == []


def test_reviewer_emits_p0_reaudit_action():
    import research.journal_audit_reviewer as jar
    signals = jar.extract_digest_signals(
        "## 4. Forward Evidence\n"
        "- Tracker verdict: MIXED\n"
        "- RE-AUDIT DUE — forward-evidence milestone crossed today: "
        "first 20d cohort matured (value 32, crossed 2026-07-16)\n")
    assert signals["forward_reaudit_due"] is True
    actions = jar.build_next_system_actions(signals, [])
    reaudit = [a for a in actions if a["area"] == "forward_evidence"
               and "Re-examine the forward-evidence verdicts" in a["task"]]
    assert reaudit and reaudit[0]["priority"] == "P0"
    assert "Do not change any gate or ranking" in reaudit[0]["task"]


def test_no_execution_imports():
    src = (ROOT / "research" / "forward_evidence_milestones.py").read_text()
    for sym in ("submit_order", "OrderManager", "paper_governance",
                "alpaca", "tradier", "requests", "sqlite3"):
        assert sym not in src, f"forbidden symbol {sym!r} in milestone hook"

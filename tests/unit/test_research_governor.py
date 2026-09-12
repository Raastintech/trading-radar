"""Tests for the Research Governor v0 (deterministic, audit-only).

Pins the governor's contract:
  - no provider or LLM client is imported, and a run touches no network
  - the registry only accepts approved enums, and quarantined / decommissioned
    / disabled / contradicted components can never surface candidates or be
    allowed in the digest or dashboard
  - every decision uses exactly one approved action
  - passed dates are flagged; future maturity dates wait instead of acting
  - output is valid JSON plus markdown free of trade language, written only
    to the two approved paths
  - missing artifacts produce warnings, never crashes

Fixture roots live in tmp_path; the production registry is read, never
written.
"""
from __future__ import annotations

import ast
import json
import re
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from research import research_governor as rg

REPO = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 9, 12, 1, 0, tzinfo=timezone.utc)  # ET session 2026-09-11

TRADE_LANGUAGE = re.compile(r"\b(buy|sell|hold|top[- ]picks?|price[- ]targets?)\b",
                            re.IGNORECASE)

RUNNER = """#!/usr/bin/env bash
cmd_nightly() {
    cmd_dead_report
    cmd_fetch
    # cmd_commented_out
}
cmd_dead_report() {
    "$PY" -m research.dead_report
}
cmd_fetch() {
    log "[PROVIDER] fetch things"
}
cmd_commented_out() {
    "$PY" -m research.commented
}
case "$CMD" in
    nightly)  cmd_nightly "${POS[@]}" ;;
esac
"""


def comp(cid: str, **over) -> dict:
    base = {
        "component_id": cid, "name": cid.replace("_", " "),
        "owner_file_or_artifact": [f"research/{cid}.py"],
        "kind": "infrastructure", "status": "SUPPORT",
        "evidence_level": "OPERATIONAL_CONTROL_ONLY",
        "daily_decision_impact": False, "provider_call_risk": "NONE",
        "allowed_to_surface_candidates": False, "allowed_in_daily_digest": False,
        "allowed_in_dashboard": False, "requires_human_approval_to_change": True,
        "next_decision_date": None, "maturity_date": None, "expiration_date": None,
        "cadence": "on_demand", "purpose": "fixture", "consumers": ["tests"],
        "notes": "fixture component",
    }
    base.update(over)
    return base


def write_json(root: Path, rel: str, payload) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload), encoding="utf-8")


def make_root(tmp_path: Path, components: list, *, budget: bool = True,
              **registry_over) -> Path:
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "run_research_cycle.sh").write_text(RUNNER)
    units = root / "units"
    (units / "timers.target.wants").mkdir(parents=True)
    (units / "gem-trader-test.timer").write_text("[Timer]\nOnCalendar=Mon-Fri 20:30\n")
    (units / "gem-trader-test.service").write_text(
        "[Service]\nExecStart=/x/scripts/run_research_cycle.sh nightly\n")
    (units / "timers.target.wants" / "gem-trader-test.timer").write_text("")
    if budget:
        (root / "core").mkdir()
        (root / "core" / "provider_budget.py").write_text(
            "DEFAULT_MONTHLY_CAP = 120_000\nDEFAULT_DAILY_CAP = 12_000\n"
            "PLANNED_CALL_CONFIRM_THRESHOLD = 100\n"
            "# ALLOW_UNLIMITED_PROVIDER_CALLS opt-in\n"
            "def _cap(n, default):\n    return n if n > 0 else default\n")
        (root / "core" / "data_gatekeeper.py").write_text("check_spend(0, 0)\n")
    registry = {
        "schema_version": 1, "components": components, "build_proposals": [],
        "known_test_debt": [], "generated_docs": [], "reader_surfaces": [],
        "label_scan_sources": [],
        "scheduler": {"runner": "scripts/run_research_cycle.sh", "unit_dirs": ["units"]},
        "cost_sources": {"scan_dirs": ["research"]},
        "surface_scan_dirs": ["cache/research"],
    }
    registry.update(registry_over)
    write_json(root, rg.DEFAULT_REGISTRY_REL, registry)
    return root


def build(root: Path) -> dict:
    return rg.build_report(root=root, now=NOW)


def decision(report: dict, cid: str) -> dict:
    return next(d for d in report["decisions_due"] if d["component_id"] == cid)


def rules(registry: dict) -> set:
    return {e["rule"] for e in rg.validate_registry(registry)}


# ── no provider / LLM access ────────────────────────────────────────────────


def test_governor_imports_no_provider_or_llm_client():
    tree = ast.parse(Path(rg.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    forbidden = ("core", "requests", "urllib", "httpx", "aiohttp", "socket",
                 "openai", "anthropic", "deepseek", "alpaca", "tradier",
                 "yfinance", "fmp", "dashboards", "execution", "strategies")
    for mod in imported:
        assert not any(mod == f or mod.startswith(f + ".") for f in forbidden), mod
        assert not any(t in mod for t in ("llm", "provider", "gatekeeper")), mod


def _block_network(monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("the governor attempted network access")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def test_default_run_on_the_real_repo_makes_zero_provider_calls(monkeypatch):
    _block_network(monkeypatch)
    before = set(sys.modules)
    report = rg.build_report(root=REPO, now=NOW)  # real registry; writes nothing
    new_modules = set(sys.modules) - before
    assert report["provider_calls_made"] == 0
    assert report["llm_calls_made"] == 0
    assert not [m for m in new_modules
                if m.startswith(("core.", "requests", "openai", "anthropic", "httpx"))
                or "llm" in m]
    assert not [w for w in report["drift_warnings"] if w["category"] == "check_error"]
    assert not TRADE_LANGUAGE.search(rg.render_markdown(report))


def test_cli_run_writes_zero_provider_calls(monkeypatch, tmp_path):
    _block_network(monkeypatch)
    root = make_root(tmp_path, [comp("infra")])
    assert rg.main(["--root", str(root), "--quiet"]) == 0
    payload = json.loads((root / rg.DEFAULT_OUTPUT_JSON_REL).read_text())
    assert payload["provider_calls_made"] == 0
    assert payload["research_only"] is True


# ── registry rules ───────────────────────────────────────────────────────────


def test_real_registry_is_valid_and_honest():
    registry = json.loads((REPO / rg.DEFAULT_REGISTRY_REL).read_text(encoding="utf-8"))
    assert rg.validate_registry(registry) == []
    by = {c["component_id"]: c for c in registry["components"]}
    assert all(c["requires_human_approval_to_change"] is True for c in by.values())
    assert not [c for c in by.values() if c["evidence_level"] == "VALIDATED_EDGE"]
    assert by["scanner_truth_autopsy"]["status"] in ("DECOMMISSIONED", "RESEARCH_ONLY")
    retired = ("QUARANTINE", "DISABLED", "DECOMMISSIONED")
    assert by["ten_x_candidate_radar"]["status"] in retired
    assert by["social_attention_v11"]["status"] in retired
    # Operator decision 2026-09-11: exactly one daily candidate source.
    surfacing = [c["component_id"] for c in registry["components"]
                 if c["allowed_to_surface_candidates"]]
    assert surfacing == ["daily_alpha_radar", "m1_daily_review_packet"]
    daily_lists = [c["component_id"] for c in registry["components"]
                   for entry in c.get("candidate_lists") or []
                   if entry.get("daily_review_list")]
    assert daily_lists == ["daily_alpha_radar"]
    assert by["m1_source_pool"]["evidence_level"] == "PROVISIONAL_POOL_LEVEL_ONLY"
    assert by["m1_frozen_cohort_tracker"]["evidence_level"] == "FORWARD_IMMATURE"
    style = by["style_cell_leader_forward_shadow"]
    assert style["evidence_level"] == "FORWARD_IMMATURE"
    assert style["research_label"] == "FORWARD_SHADOW_RESEARCH_ONLY"
    assert by["research_governor"]["expiration_date"] == "2026-10-23"


def test_real_registry_m1_cohort_waits_for_maturity():
    report = rg.build_report(root=REPO, now=NOW)
    assert decision(report, "m1_frozen_cohort_tracker")["action"] == "WAIT_FOR_MATURITY"
    assert decision(report, "style_cell_leader_forward_shadow")["action"] == "WAIT_FOR_MATURITY"


@pytest.mark.parametrize("field, value, rule", [
    ("status", "LIVE", "status_enum"),
    ("evidence_level", "PROBABLY_GOOD", "evidence_enum"),
    ("provider_call_risk", "SOME", "risk_enum"),
    ("requires_human_approval_to_change", False, "human_approval"),
])
def test_only_approved_enums_are_accepted(field, value, rule):
    assert rule in rules({"components": [comp("x", **{field: value})]})


def test_approved_enums_pass():
    for status in rg.STATUSES:
        c = comp("x", status=status)
        assert not ({"status_enum"} & rules({"components": [c]}))
    for level in rg.EVIDENCE_LEVELS:
        if level == "VALIDATED_EDGE":
            continue
        assert "evidence_enum" not in rules({"components": [comp("x", evidence_level=level)]})


@pytest.mark.parametrize("status", sorted(rg.BLOCKED_STATUSES))
def test_blocked_components_cannot_surface_candidates(status):
    c = comp("x", status=status, allowed_to_surface_candidates=True)
    assert "surface_forbidden" in rules({"components": [c]})


@pytest.mark.parametrize("status", sorted(rg.BLOCKED_STATUSES))
def test_blocked_components_cannot_be_in_digest_or_dashboard(status):
    got = rules({"components": [comp("x", status=status, allowed_in_daily_digest=True,
                                     allowed_in_dashboard=True)]})
    assert {"digest_forbidden", "dashboard_forbidden"} <= got


@pytest.mark.parametrize("level", ["REPLAY_CONTRADICTED", "FAILED_GATES"])
def test_contradicted_components_cannot_surface_candidates(level):
    c = comp("x", evidence_level=level, allowed_to_surface_candidates=True)
    assert "surface_forbidden" in rules({"components": [c]})


def test_validated_edge_needs_explicit_approval():
    bare = comp("x", evidence_level="VALIDATED_EDGE")
    assert "validated_edge_unapproved" in rules({"components": [bare]})
    approved = comp("x", evidence_level="VALIDATED_EDGE",
                    notes="Approved after 60d forward validation vs random control.",
                    validated_edge_approval={"approved_by": "operator",
                                             "approved_on": "2026-12-01",
                                             "evidence_ref": "docs/x.md"})
    assert "validated_edge_unapproved" not in rules({"components": [approved]})


def test_broken_registry_blocks_the_verdict_without_crashing(tmp_path):
    root = make_root(tmp_path, [comp("bad", status="QUARANTINE",
                                     allowed_in_daily_digest=True)])
    report = build(root)
    assert report["verdict"] == "BLOCKED"
    assert decision(report, "bad")["action"] == "FIX_NEXT"


def test_missing_registry_blocks_without_crashing(tmp_path):
    report = rg.build_report(root=tmp_path, now=NOW)
    assert report["verdict"] == "BLOCKED"
    assert report["registry_errors"][0]["rule"] == "registry_unreadable"


# ── decisions ────────────────────────────────────────────────────────────────


def _busy_root(tmp_path: Path) -> Path:
    """A fixture that exercises every action."""
    return make_root(tmp_path, [
        comp("keeper"),
        comp("dead_report", kind="report", status="DECOMMISSIONED",
             evidence_level="FAILED_GATES", cadence="nightly",
             runner_refs=["research.dead_report"]),
        comp("contradicted", evidence_level="REPLAY_CONTRADICTED",
             daily_decision_impact=True),
        comp("young_cohort", kind="tracker", evidence_level="FORWARD_IMMATURE",
             maturity_date="2026-12-04", forward_hypothesis="h"),
        comp("late_gate", kind="gate", next_decision_date="2026-09-01"),
        comp("spender", provider_call_risk="HIGH", hard_guard=False),
    ], build_proposals=[
        {"proposal_id": "new_radar", "name": "Another radar",
         "decision": "DO_NOT_BUILD", "reason": "enough surfaces"},
    ])


def test_decision_queue_uses_only_approved_actions(tmp_path):
    report = build(_busy_root(tmp_path))
    ids = [d["component_id"] for d in report["decisions_due"]]
    assert len(ids) == len(set(ids))
    fields = {"component_id", "component_name", "action", "reason", "evidence_level",
              "provider_call_risk", "stale_artifact_risk", "human_approval_required",
              "suggested_next_step"}
    for d in report["decisions_due"]:
        assert d["action"] in rg.ACTIONS
        assert fields <= set(d)
    assert {d["action"] for d in report["decisions_due"]} == set(rg.ACTIONS)


def test_expected_actions_per_rule(tmp_path):
    report = build(_busy_root(tmp_path))
    assert decision(report, "keeper")["action"] == "KEEP"
    assert decision(report, "dead_report")["action"] == "FIX_NEXT"      # scheduled
    assert decision(report, "contradicted")["action"] == "STOP_NOW"
    assert decision(report, "spender")["action"] == "FIX_NEXT"
    assert decision(report, "proposal:new_radar")["action"] == "DO_NOT_BUILD"
    assert report["verdict"] == "DRIFTING"


def test_passed_decision_dates_are_flagged(tmp_path):
    report = build(_busy_root(tmp_path))
    assert any(w["category"] == "decision_date_passed" and w["component_id"] == "late_gate"
               for w in report["drift_warnings"])
    d = decision(report, "late_gate")
    assert d["action"] == "NEEDS_HUMAN_APPROVAL"
    assert d["human_approval_required"] is True


def test_future_maturity_waits_instead_of_acting(tmp_path):
    report = build(_busy_root(tmp_path))
    d = decision(report, "young_cohort")
    assert d["action"] == "WAIT_FOR_MATURITY"
    assert d["human_approval_required"] is False
    matured = build(make_root(tmp_path / "m", [comp(
        "old_cohort", kind="tracker", evidence_level="FORWARD_IMMATURE",
        maturity_date="2026-09-01", forward_hypothesis="h")]))
    assert decision(matured, "old_cohort")["action"] == "NEEDS_HUMAN_APPROVAL"


def test_scheduled_blocked_component_is_flagged_but_commented_call_is_not(tmp_path):
    root = make_root(tmp_path, [
        comp("dead_report", status="DECOMMISSIONED", runner_refs=["research.dead_report"]),
        comp("commented", status="QUARANTINE", next_decision_date="2026-12-01",
             runner_refs=["cmd_commented_out"]),
    ])
    report = build(root)
    assert decision(report, "dead_report")["action"] == "FIX_NEXT"
    comps = {c["component_id"]: c for c in report["components"]}
    assert comps["dead_report"]["scheduled_by"] == ["gem-trader-test.timer"]
    assert comps["commented"]["scheduled_by"] == []
    assert decision(report, "commented")["action"] == "KEEP"


def test_quarantined_surface_read_without_gate_is_stop_now(tmp_path):
    artifact = "cache/research/ten_x_candidates_latest.json"
    surface = comp("ten_x", kind="surface", status="QUARANTINE",
                   next_decision_date="2026-12-01", artifacts=[artifact])
    root = make_root(tmp_path, [surface],
                     reader_surfaces=[{"path": "dash/view.py", "kind": "dashboard"}])
    (root / "dash").mkdir()
    (root / "dash" / "view.py").write_text(f"load('{artifact}')\n")
    assert decision(build(root), "ten_x")["action"] == "STOP_NOW"

    (root / "dash" / "view.py").write_text(
        f"from core import quarantined_surfaces\nload('{artifact}')\n")
    assert decision(build(root), "ten_x")["action"] == "KEEP"


def test_unregistered_candidate_surface_needs_human_approval(tmp_path):
    root = make_root(tmp_path, [comp("infra")])
    write_json(root, "cache/research/mystery_list_latest.json",
               {"candidates": [{"ticker": t} for t in ("AAA", "BBB", "CCC")]})
    d = decision(build(root), "unregistered:mystery_list")
    assert d["action"] == "NEEDS_HUMAN_APPROVAL"


def test_approved_build_proposal_is_not_refused(tmp_path):
    root = make_root(tmp_path, [comp("infra")], build_proposals=[
        {"proposal_id": "ok", "name": "Approved thing", "decision": "APPROVED",
         "approved_by": "operator", "approval_ref": "docs/decision.md", "reason": "r"},
        {"proposal_id": "no", "name": "Refused thing", "decision": "DO_NOT_BUILD",
         "reason": "r"}])
    report = build(root)
    assert decision(report, "proposal:ok")["action"] == "KEEP"
    assert decision(report, "proposal:no")["action"] == "DO_NOT_BUILD"


def test_daily_list_over_limit_and_fake_precision_are_fix_next(tmp_path):
    artifact = "cache/research/board_latest.json"
    root = make_root(tmp_path, [
        comp("board", kind="surface", allowed_to_surface_candidates=True,
             forward_hypothesis="h", candidate_lists=[
                 {"artifact": artifact, "path": "names", "daily_review_list": True}]),
        comp("tracker", kind="tracker", precision_probes=[
            {"kind": "mean_median_gap", "artifact": artifact, "mean_path": "mean",
             "median_path": "median", "max_gap_pp": 10}]),
    ])
    write_json(root, artifact, {"names": [f"T{i}" for i in range(20)],
                                "mean": 29.4, "median": -1.7})
    report = build(root)
    assert decision(report, "board")["action"] == "FIX_NEXT"
    assert decision(report, "tracker")["action"] == "FIX_NEXT"
    cats = {w["category"] for w in report["drift_warnings"]}
    assert {"daily_list_over_limit", "fake_precision"} <= cats


def test_clean_fixture_is_research_only_no_edge(tmp_path):
    report = build(make_root(tmp_path, [comp("infra")]))
    assert report["verdict"] == "RESEARCH_ONLY_NO_EDGE"
    assert report["apparatus_answer"]["answer"] in rg.APPARATUS_ANSWERS


# ── outputs ──────────────────────────────────────────────────────────────────


def test_generated_json_is_valid(tmp_path):
    root = _busy_root(tmp_path)
    assert rg.main(["--root", str(root), "--quiet"]) == 0
    payload = json.loads((root / rg.DEFAULT_OUTPUT_JSON_REL).read_text())
    for key in ("generated_at", "verdict", "registry_component_count",
                "drift_warning_count", "decisions_due_count", "provider_calls_made",
                "components", "evidence", "cost_risks", "decisions_due", "research_only"):
        assert key in payload
    assert payload["verdict"] in rg.VERDICTS
    assert payload["provider_calls_made"] == 0
    assert payload["research_only"] is True
    assert payload["decisions_due_count"] == len(payload["decisions_due"])


def test_markdown_has_no_trade_language(tmp_path):
    root = _busy_root(tmp_path)
    rg.main(["--root", str(root), "--quiet"])
    text = (root / rg.DEFAULT_OUTPUT_MD_REL).read_text()
    assert "## 10. Final answer" in text
    assert not TRADE_LANGUAGE.search(text)


def test_registry_text_with_trade_language_is_rejected():
    c = comp("x", notes="a strong buy candidate")
    assert "banned_language" in rules({"components": [c]})


def _snapshot(root: Path) -> dict:
    return {p: p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_report_writes_only_to_approved_paths(tmp_path):
    root = _busy_root(tmp_path)
    before = _snapshot(root)
    assert rg.main(["--root", str(root), "--quiet"]) == 0
    after = _snapshot(root)
    changed = {p for p in set(before) | set(after) if before.get(p) != after.get(p)}
    assert changed == {root / rg.DEFAULT_OUTPUT_MD_REL, root / rg.DEFAULT_OUTPUT_JSON_REL}
    assert after[root / rg.DEFAULT_REGISTRY_REL] == before[root / rg.DEFAULT_REGISTRY_REL]

    for bad in (["--output-md", "elsewhere/report.md"],
                ["--output-json", "docs/research/x.json"],
                ["--output-md", "../outside.md"]):
        snap = _snapshot(root)
        assert rg.main(["--root", str(root), "--quiet", *bad]) == 2
        assert _snapshot(root) == snap


def test_missing_artifacts_produce_warnings_not_crashes(tmp_path):
    missing = "cache/research/absent_latest.json"
    root = make_root(tmp_path, [comp(
        "ghost", kind="tracker", cadence="nightly", allowed_in_daily_digest=True,
        forward_hypothesis="h", artifacts=[missing],
        evidence_probe={"artifact": missing, "path": "verdict"},
        evidence_path_label="ghost path",
        candidate_lists=[{"artifact": missing, "path": "names"}],
        precision_probes=[{"kind": "ratio_max", "artifact": missing, "path": "x", "max": 1}],
        decision_date_probes=[{"artifact": missing, "path": "next"}])],
        label_scan_sources=[{"component_id": "ghost", "path": "logs/nothing.md"}],
        generated_docs=[], known_test_debt=[{"test": "tests/unit/test_gone.py"}])
    report = build(root)
    assert not [w for w in report["drift_warnings"] if w["category"] == "check_error"]
    cats = {w["category"] for w in report["drift_warnings"] + report["info_notes"]}
    assert "missing_artifact" in cats
    ev = next(e for e in report["evidence"] if e["component_id"] == "ghost")
    assert ev["observed_evidence_level"] == "NOT_ENOUGH_EVIDENCE"


def test_ignored_generated_reports_are_not_counted_as_git_noise():
    """An untracked, ignored report cannot dirty the tree, so it is not a
    finding — including while its untracking is still staged as a deletion."""
    generated = ["docs/research/DAILY_ALPHA_RADAR_REPORT.md",
                 "docs/research/NIGHTLY_OPERATOR_SUMMARY.md"]
    tracked, error = rg._git_tracked(REPO, generated)
    if error:
        pytest.skip(f"git unavailable: {error}")
    assert tracked == [], "a generated report is still tracked"
    durable, error = rg._git_tracked(REPO, ["docs/ops/RESEARCH_GOVERNOR_RUNBOOK.md"])
    assert error is None and durable, "durable docs must stay tracked"


def test_artifact_without_a_verdict_is_unverified_not_a_mismatch(tmp_path):
    artifact = "cache/research/pool_latest.json"
    root = make_root(tmp_path, [comp(
        "pool", kind="surface", evidence_level="PROVISIONAL_POOL_LEVEL_ONLY",
        evidence_probe={"artifact": artifact, "path": "verdict"},
        evidence_path_label="pool")])
    write_json(root, artifact, {"entries": []})
    report = build(root)
    ev = next(e for e in report["evidence"] if e["component_id"] == "pool")
    assert ev["agreement"] == "UNVERIFIED"
    assert decision(report, "pool")["action"] == "KEEP"


def test_large_uncapped_run_is_fix_next_for_its_owner(tmp_path):
    root = make_root(tmp_path, [comp("studies", kind="archive",
                                     owner_file_or_artifact=["research/studies/"])])
    studies = root / "research" / "studies"
    studies.mkdir(parents=True)
    (studies / "big_fetch.py").write_text("DEFAULT_MAX_CALLS = 11_500\n")
    (studies / "gated_fetch.py").write_text(
        "DEFAULT_MAX_CALLS = 9000\nassert_planned_calls_authorised(9000)\n")
    (studies / "small_fetch.py").write_text("DEFAULT_MAX_CALLS = 1500\n")
    d = decision(build(root), "studies")
    assert d["action"] == "FIX_NEXT"
    assert "big_fetch.py" in d["reason"]
    assert "gated_fetch" not in d["reason"] and "small_fetch" not in d["reason"]

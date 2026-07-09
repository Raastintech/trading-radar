"""Tests for the feedback queue review workflow (research-only bookkeeping).

Covers the eight required behaviors:
  1. Queue review reads and dedupes repeated tasks across audits.
  2. Highest-priority tasks appear first (P0 -> P1 -> P2 -> REC).
  3. Resolving a task writes an append-only state entry.
  4. Dismissing a task does not delete the original queue entry.
  5. The latest audit can mark a task as resolved_by_audit.
  6. Commit hash linkage is preserved end to end.
  7. No broker/execution imports are introduced.
  8. The workflow does not change scanner thresholds, gates, or scores —
     review writes nothing; resolve/dismiss write only the state ledger.

All tests run against temp roots — the production queue, state ledger,
and audit sidecar are never touched.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from research import feedback_queue as fq

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64

TS_A = "2026-07-04T06:21:00+00:00"
TS_B = "2026-07-08T00:47:00+00:00"


def _queue_line(*, ts: str, digest: str, kind=None, priority=None,
                area=None, task: str, why: str = "", metric: str = ""):
    entry = {
        "queued_at": ts,
        "source": "journal_audit_reviewer",
        "audit_source": "llm",
        "digest_sha256": digest,
        "engine_health": "OPERATIONAL_WITH_BLOCKERS",
        "research_verdict": "RESEARCH_ONLY",
        "task": task,
    }
    if kind:
        entry["kind"] = kind
    if priority:
        entry["priority"] = priority
        entry["area"] = area
        entry["why"] = why
        entry["success_metric"] = metric
    return entry


P0_TASK = ("Build scanner recall diagnostics: report reject counts by "
           "filter and compare cohorts.")
P1_TASK = "Improve forward-evidence tracking with benchmarked cohorts."
P2_TASK = "Report options coverage health for the disabled overlay."
LEGACY_TASK = "Continue the recall-repair shadow lane work."


@pytest.fixture
def root(tmp_path: Path) -> Path:
    queue = tmp_path / fq.QUEUE_REL
    queue.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        # digest A: P0 + P2 + one legacy entry (no kind, no priority)
        _queue_line(ts=TS_A, digest=DIGEST_A, kind="system_repair_task",
                    priority="P0", area="scanner_recall", task=P0_TASK,
                    why="recall is 2.0% vs baseline",
                    metric="diagnostics sidecar exists"),
        _queue_line(ts=TS_A, digest=DIGEST_A, kind="system_repair_task",
                    priority="P2", area="options_overlay", task=P2_TASK,
                    why="overlay disabled", metric="coverage report"),
        _queue_line(ts=TS_A, digest=DIGEST_A, task=LEGACY_TASK),
        # digest B: P0 re-queued (twice — same-night duplicate) + new P1
        _queue_line(ts=TS_B, digest=DIGEST_B, kind="system_repair_task",
                    priority="P0", area="scanner_recall", task=P0_TASK,
                    why="recall still 2.0%", metric="cohorts accrue returns"),
        _queue_line(ts=TS_B, digest=DIGEST_B, kind="system_repair_task",
                    priority="P0", area="scanner_recall", task=P0_TASK,
                    why="recall still 2.0%", metric="cohorts accrue returns"),
        _queue_line(ts=TS_B, digest=DIGEST_B, kind="system_repair_task",
                    priority="P1", area="forward_evidence", task=P1_TASK,
                    why="NO_FORWARD_EDGE", metric="tracker reports cohorts"),
    ]
    queue.write_text(
        "\n".join(json.dumps(e) for e in lines) + "\n", encoding="utf-8")

    # Latest audit = digest B; scanner_recall + forward_evidence still
    # flagged; options_overlay resolved per the trend.
    sidecar = tmp_path / fq.AUDIT_SIDECAR_REL
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(json.dumps({
        "digest_sha256": DIGEST_B,
        "generated_at": TS_B,
        "engine_health": "OPERATIONAL_WITH_BLOCKERS",
        "flaws_detected": [
            {"severity": "HIGH", "area": "scanner_recall", "issue": "x"},
            {"severity": "CRITICAL", "area": "forward_evidence",
             "issue": "y"},
        ],
        "next_system_actions": [],
        "audit_trend": {"blockers": [
            {"area": "options_overlay", "direction": "RESOLVED"},
        ]},
    }), encoding="utf-8")
    return tmp_path


def _task_by_text(tasks, text):
    return next(t for t in tasks if t["task"] == text)


# 1. dedupe ------------------------------------------------------------------


def test_review_reads_and_dedupes_repeated_tasks(root):
    tasks = fq.load_queue_tasks(root)
    # 6 raw lines (one an exact same-digest duplicate) -> 4 distinct tasks
    assert len(tasks) == 4
    p0 = _task_by_text(tasks, P0_TASK)
    assert p0["repeat_count"] == 2
    assert p0["source_digests"] == [DIGEST_A, DIGEST_B]
    assert p0["first_seen"] == TS_A
    assert p0["last_seen"] == TS_B
    # the later audit's wording wins for why/success_metric
    assert p0["why"] == "recall still 2.0%"
    # legacy shape (no kind/priority) is normalized, not dropped
    legacy = _task_by_text(tasks, LEGACY_TASK)
    assert legacy["kind"] == "recommended_task"
    assert legacy["priority"] == "REC"
    assert legacy["area"] == "general"


# 2. priority ordering -------------------------------------------------------


def test_highest_priority_tasks_first(root):
    tasks = fq.load_queue_tasks(root)
    assert [t["priority"] for t in tasks] == ["P0", "P1", "P2", "REC"]
    review = fq.build_review(root)
    top = review["top_open_p0_p1"]
    assert [t["priority"] for t in top] == ["P0", "P1"]
    assert review["recommended_next"]["task"] == P0_TASK
    repeat = review["repeat_offenders"]
    assert [t["task"] for t in repeat] == [P0_TASK]


# 3. resolve appends ---------------------------------------------------------


def test_resolve_writes_append_only_state_entry(root):
    p0 = _task_by_text(fq.load_queue_tasks(root), P0_TASK)
    fq.append_state_entry(p0["task_id"], "IN_PROGRESS", root=root,
                          notes="picked up")
    fq.append_state_entry(p0["task_id"], "ADDRESSED", root=root,
                          commit="abc1234", notes="done")
    ledger = (root / fq.STATE_REL).read_text().splitlines()
    assert len(ledger) == 2  # both entries kept — nothing rewritten
    first, second = (json.loads(l) for l in ledger)
    assert first["status"] == "IN_PROGRESS"
    assert second["status"] == "ADDRESSED"
    assert fq.load_state(root)[p0["task_id"]]["status"] == "ADDRESSED"
    with pytest.raises(ValueError):
        fq.append_state_entry("nonexistent000", "ADDRESSED", root=root)
    with pytest.raises(ValueError):
        fq.append_state_entry(p0["task_id"], "OPEN", root=root)


# 4. dismiss preserves the queue ---------------------------------------------


def test_dismiss_does_not_delete_queue_entry(root):
    queue_path = root / fq.QUEUE_REL
    before = queue_path.read_bytes()
    p2 = _task_by_text(fq.load_queue_tasks(root), P2_TASK)
    rc = fq.main(["--root", str(root), "dismiss", p2["task_id"],
                  "--notes", "not worth a report"])
    assert rc == 0
    assert queue_path.read_bytes() == before  # queue untouched
    entry = fq.load_state(root)[p2["task_id"]]
    assert entry["status"] == "DISMISSED"
    assert entry["notes"] == "not worth a report"
    review = fq.build_review(root)
    assert review["counts"]["dismissed"] == 1


# 5. resolved by latest audit ------------------------------------------------


def test_latest_audit_marks_task_resolved_by_audit(root):
    review = fq.build_review(root)
    resolved_texts = [t["task"] for t in review["audit_resolved_candidates"]]
    # options_overlay: trend RESOLVED + not re-queued by digest B
    assert P2_TASK in resolved_texts
    # scanner_recall: re-queued by the latest digest — still live
    assert P0_TASK not in resolved_texts
    p0 = _task_by_text(review["tasks"], P0_TASK)
    assert p0["still_in_latest_audit"] is True

    p2 = _task_by_text(review["tasks"], P2_TASK)
    fq.append_state_entry(p2["task_id"], "RESOLVED_BY_AUDIT", root=root,
                          notes="overlay flaw gone from 07-08 audit")
    review = fq.build_review(root)
    p2 = _task_by_text(review["tasks"], P2_TASK)
    assert p2["status"] == "RESOLVED_BY_AUDIT"
    # closed tasks drop out of the candidates section
    assert P2_TASK not in [
        t["task"] for t in review["audit_resolved_candidates"]]
    assert review["counts"]["resolved_by_audit"] == 1


# 6. commit linkage ----------------------------------------------------------


def test_commit_hash_linkage_preserved(root):
    p0 = _task_by_text(fq.load_queue_tasks(root), P0_TASK)
    rc = fq.main(["--root", str(root), "resolve", p0["task_id"],
                  "--status", "addressed", "--commit", "92c8448",
                  "--notes", "recall diagnostics built",
                  "--files", "research/scanner_recall_diagnostics.py,"
                             "tests/unit/test_scanner_recall.py",
                  "--follow-up"])
    assert rc == 0
    raw = json.loads((root / fq.STATE_REL).read_text().splitlines()[-1])
    assert raw["commit"] == "92c8448"
    assert raw["files_changed"] == [
        "research/scanner_recall_diagnostics.py",
        "tests/unit/test_scanner_recall.py"]
    assert raw["follow_up_needed"] is True

    review = fq.build_review(root)
    p0 = _task_by_text(review["tasks"], P0_TASK)
    assert p0["commit"] == "92c8448"
    # addressed with a commit but still flagged by the latest audit
    # -> surfaces in the "not yet confirmed" section
    assert [t["task"] for t in review["addressed_unconfirmed"]] == [P0_TASK]


# 7. no broker/execution imports ---------------------------------------------


def test_no_broker_or_execution_imports():
    source = Path(fq.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    forbidden = ("alpaca", "tradier", "execution", "broker", "core.config",
                 "core.alpaca", "order", "strategies", "council")
    for mod in imported:
        assert not any(mod.startswith(f) or f in mod for f in forbidden), \
            f"forbidden import in feedback_queue: {mod}"
    # stdlib only — no network / provider libraries either
    assert "requests" not in imported
    assert "urllib" not in imported


# 8. no engine mutation ------------------------------------------------------


def _snapshot(root: Path):
    return {p: p.read_bytes() for p in sorted(root.rglob("*"))
            if p.is_file()}


def test_workflow_never_changes_scanner_gates_or_scores(root, capsys):
    before = _snapshot(root)
    rc = fq.main(["--root", str(root), "review"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "no task is auto-executed" in out
    # review is strictly read-only: not a single file changed
    assert _snapshot(root) == before

    p0 = _task_by_text(fq.load_queue_tasks(root), P0_TASK)
    fq.append_state_entry(p0["task_id"], "ADDRESSED", root=root,
                          commit="abc1234")
    after = _snapshot(root)
    changed = {p for p in set(before) | set(after)
               if before.get(p) != after.get(p)}
    # resolve touches exactly one file: the append-only state ledger
    assert changed == {root / fq.STATE_REL}

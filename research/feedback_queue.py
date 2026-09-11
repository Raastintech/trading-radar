#!/usr/bin/env python3
"""Feedback queue review workflow — consumer side of the journal audit.

The LLM journal audit (research/journal_audit_reviewer.py) appends repair
tasks and recommended tasks to logs/research_engine_feedback_queue.jsonl,
but nothing reads that queue programmatically.  This module is the
missing consumer-side bookkeeping layer: it aggregates queued tasks into
stable task ids, tracks their lifecycle in an append-only state ledger,
and prints a session-start review so a human (or a Claude Code session,
under human selection) can decide what to work on next.

Doctrine:
  - TASK REVIEW AND BOOKKEEPING ONLY.  Never executes a queued task,
    never changes scanner thresholds, gates, scores, watchlists, or
    artifacts, and never promotes a ticker to a signal.
  - READ-ONLY on the queue.  logs/research_engine_feedback_queue.jsonl
    is written only by the journal audit reviewer; this module never
    mutates or rewrites it.  The audit sidecar
    (cache/research/journal_audit_latest.json) is also read-only here.
  - APPEND-ONLY state.  The only write is appending lifecycle entries to
    data/research/feedback_queue_state.jsonl.  A task's current status is
    the most recent entry for its task_id; history is never rewritten.
  - HUMAN SELECTS.  The review presents the queue; it does not act on it.
    Marking a task resolved-by-audit is an explicit resolve action — the
    review only surfaces candidates.
  - CRED-FREE.  No core.config import, no providers, no broker or
    execution modules, no DB access.

Lifecycle statuses:
  OPEN (implicit default) -> IN_PROGRESS -> ADDRESSED (work done, commit
  linked, awaiting audit confirmation) -> RESOLVED_BY_AUDIT (the latest
  audit no longer raises the issue).  DISMISSED and SUPERSEDED close a
  task without code changes.

Usage:
  GEM_TRADER_SKIP_DOTENV=true .venv/bin/python research/feedback_queue.py review
  GEM_TRADER_SKIP_DOTENV=true .venv/bin/python research/feedback_queue.py \
      resolve TASK_ID --status addressed --commit abc1234 \
      --notes "built recall diagnostics" --files research/foo.py --follow-up
  GEM_TRADER_SKIP_DOTENV=true .venv/bin/python research/feedback_queue.py \
      dismiss TASK_ID --notes "duplicate of ..."
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]

QUEUE_REL = Path("logs") / "research_engine_feedback_queue.jsonl"
STATE_REL = Path("data") / "research" / "feedback_queue_state.jsonl"
AUDIT_SIDECAR_REL = Path("cache") / "research" / "journal_audit_latest.json"

# Priority sort rank; recommended tasks (no priority) sort after P0-P3.
PRIORITY_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "REC": 9}

STATUSES = ("OPEN", "IN_PROGRESS", "ADDRESSED", "DISMISSED",
            "SUPERSEDED", "RESOLVED_BY_AUDIT")
# Statuses a resolve/dismiss action may append (OPEN is the implicit
# default for any queued task with no ledger entry).
RECORDABLE_STATUSES = ("IN_PROGRESS", "ADDRESSED", "DISMISSED",
                       "SUPERSEDED", "RESOLVED_BY_AUDIT")
CLOSED_STATUSES = frozenset({"DISMISSED", "SUPERSEDED", "RESOLVED_BY_AUDIT"})


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except Exception:
            continue
        if isinstance(entry, dict):
            rows.append(entry)
    return rows


# ── queue aggregation ────────────────────────────────────────────────────────


def _normalize_task_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def compute_task_id(kind: str, priority: str, area: str, task: str) -> str:
    """Stable id for one distinct piece of queued work.  Identical tasks
    re-queued by later audits (different digest hashes) collapse onto the
    same id so the ledger tracks the work, not the night it was queued."""
    key = f"{kind}|{priority}|{area}|{_normalize_task_text(task)}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


def _normalize_queue_entry(entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    task = str(entry.get("task") or "").strip()
    if not task:
        return None
    kind = entry.get("kind") or (
        "system_repair_task" if entry.get("priority") else "recommended_task")
    priority = str(entry.get("priority") or "REC").upper()
    if priority not in PRIORITY_RANK:
        priority = "REC"
    area = str(entry.get("area") or "general").strip() or "general"
    return {
        "task_id": compute_task_id(kind, priority, area, task),
        "kind": kind,
        "priority": priority,
        "area": area,
        "task": task,
        "why": str(entry.get("why") or "").strip(),
        "success_metric": str(entry.get("success_metric") or "").strip(),
        "queued_at": entry.get("queued_at") or "",
        "digest_sha256": entry.get("digest_sha256") or "",
        "audit_source": entry.get("audit_source"),
    }


def load_queue_tasks(root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Aggregate raw queue lines into one record per distinct task.

    Dedupe key is (digest hash, task id) within a night — identical lines
    re-appended for the same digest count once — and task id across
    nights, accumulating first_seen / last_seen / repeat_count (number of
    distinct digests that queued it)."""
    root = Path(root) if root else REPO_ROOT
    tasks: Dict[str, Dict[str, Any]] = {}
    for entry in _read_jsonl(root / QUEUE_REL):
        norm = _normalize_queue_entry(entry)
        if norm is None:
            continue
        tid = norm["task_id"]
        agg = tasks.get(tid)
        if agg is None:
            tasks[tid] = {
                **{k: norm[k] for k in (
                    "task_id", "kind", "priority", "area", "task",
                    "why", "success_metric")},
                "first_seen": norm["queued_at"],
                "last_seen": norm["queued_at"],
                "source_digests": [norm["digest_sha256"]]
                if norm["digest_sha256"] else [],
            }
            continue
        if norm["queued_at"]:
            if not agg["first_seen"] or norm["queued_at"] < agg["first_seen"]:
                agg["first_seen"] = norm["queued_at"]
            if norm["queued_at"] > agg["last_seen"]:
                agg["last_seen"] = norm["queued_at"]
                # Later audits may sharpen the wording of why/metric.
                if norm["why"]:
                    agg["why"] = norm["why"]
                if norm["success_metric"]:
                    agg["success_metric"] = norm["success_metric"]
        if norm["digest_sha256"] \
                and norm["digest_sha256"] not in agg["source_digests"]:
            agg["source_digests"].append(norm["digest_sha256"])
    out = list(tasks.values())
    for t in out:
        t["repeat_count"] = max(1, len(t["source_digests"]))
    out.sort(key=_task_sort_key)
    return out


def _task_sort_key(task: Dict[str, Any]):
    return (PRIORITY_RANK.get(task.get("priority"), 9),
            -int(task.get("repeat_count") or 1),
            task.get("first_seen") or "",
            task.get("task_id") or "")


# ── state ledger ─────────────────────────────────────────────────────────────


def load_state(root: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """Latest ledger entry per task_id (append-only file, last entry wins)."""
    root = Path(root) if root else REPO_ROOT
    latest: Dict[str, Dict[str, Any]] = {}
    for entry in _read_jsonl(root / STATE_REL):
        tid = entry.get("task_id")
        if tid and entry.get("status") in STATUSES:
            latest[tid] = entry
    return latest


def append_state_entry(task_id: str, status: str, *,
                       root: Optional[Path] = None,
                       commit: Optional[str] = None,
                       notes: Optional[str] = None,
                       files_changed: Optional[List[str]] = None,
                       follow_up_needed: bool = False) -> Dict[str, Any]:
    """Append one lifecycle entry for a queued task.  The queue file is
    never touched — this is the module's only write."""
    root = Path(root) if root else REPO_ROOT
    status = str(status or "").upper()
    if status not in RECORDABLE_STATUSES:
        raise ValueError(
            f"invalid status {status!r}; expected one of "
            f"{', '.join(RECORDABLE_STATUSES)}")
    known = {t["task_id"] for t in load_queue_tasks(root)}
    if task_id not in known:
        raise ValueError(
            f"unknown task_id {task_id!r} — run 'review' to list open "
            "task ids")
    entry = {
        "ts": _utcnow(),
        "task_id": task_id,
        "status": status,
        "commit": (commit or "").strip() or None,
        "notes": (notes or "").strip() or None,
        "files_changed": [f for f in (files_changed or []) if f],
        "follow_up_needed": bool(follow_up_needed),
        "recorded_by": "feedback_queue_cli",
    }
    path = root / STATE_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


# ── latest-audit cross-reference ─────────────────────────────────────────────


def load_latest_audit(root: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    root = Path(root) if root else REPO_ROOT
    path = root / AUDIT_SIDECAR_REL
    if not path.exists():
        return None
    try:
        audit = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return audit if isinstance(audit, dict) else None


def _latest_audit_flagged_areas(audit: Dict[str, Any]) -> set:
    areas = set()
    for flaw in audit.get("flaws_detected") or []:
        if isinstance(flaw, dict) and flaw.get("area"):
            areas.add(str(flaw["area"]))
    for action in audit.get("next_system_actions") or []:
        if isinstance(action, dict) and action.get("area"):
            areas.add(str(action["area"]))
    return areas


def _trend_resolved_areas(audit: Dict[str, Any]) -> set:
    trend = audit.get("audit_trend") or {}
    return {str(b.get("area")) for b in trend.get("blockers") or []
            if isinstance(b, dict) and b.get("direction") == "RESOLVED"}


# ── review model ─────────────────────────────────────────────────────────────


def build_review(root: Optional[Path] = None) -> Dict[str, Any]:
    """Assemble the full session-start review model (read-only)."""
    root = Path(root) if root else REPO_ROOT
    tasks = load_queue_tasks(root)
    state = load_state(root)
    audit = load_latest_audit(root)

    latest_digest = (audit or {}).get("digest_sha256") or None
    flagged_areas = _latest_audit_flagged_areas(audit) if audit else set()
    resolved_areas = _trend_resolved_areas(audit) if audit else set()

    for task in tasks:
        ledger = state.get(task["task_id"])
        task["status"] = (ledger or {}).get("status") or "OPEN"
        task["status_ts"] = (ledger or {}).get("ts")
        task["commit"] = (ledger or {}).get("commit")
        task["notes"] = (ledger or {}).get("notes")
        task["follow_up_needed"] = bool((ledger or {}).get(
            "follow_up_needed", False))
        if audit is None:
            task["requeued_by_latest_audit"] = None
            task["still_in_latest_audit"] = None
            task["audit_resolved_hint"] = False
            continue
        requeued = bool(latest_digest
                        and latest_digest in task["source_digests"])
        area_flagged = task["area"] in flagged_areas
        task["requeued_by_latest_audit"] = requeued
        task["still_in_latest_audit"] = requeued or area_flagged
        # An ADDRESSED task is confirmed when the latest audit stops
        # queueing the task itself — not when its whole area goes quiet.
        # An area can stay flagged for a reason the task cannot clear
        # (forward evidence waiting to mature), and keying on the area kept
        # the two 2026-07-10 P0 deliverables "unconfirmed" indefinitely.
        confirmed_addressed = (task["status"] == "ADDRESSED"
                               and bool(task["commit"]) and not requeued)
        task["audit_resolved_hint"] = (
            task["area"] in resolved_areas
            or (not requeued and not area_flagged)
            or confirmed_addressed)

    open_like = [t for t in tasks if t["status"] in ("OPEN", "IN_PROGRESS")]
    top_p0_p1 = [t for t in open_like if t["priority"] in ("P0", "P1")][:5]
    repeat_offenders = [t for t in open_like if t["repeat_count"] >= 2]
    audit_resolved = [t for t in tasks
                      if t["status"] not in CLOSED_STATUSES
                      and t["audit_resolved_hint"]]
    addressed_unconfirmed = [
        t for t in tasks
        if t["status"] == "ADDRESSED" and t.get("commit")
        and t.get("requeued_by_latest_audit")]
    recommended_next = open_like[0] if open_like else None

    counts = {"total_tasks": len(tasks)}
    for status in STATUSES:
        counts[status.lower()] = sum(
            1 for t in tasks if t["status"] == status)

    return {
        "generated_at": _utcnow(),
        "research_only": True,
        "queue_path": str(root / QUEUE_REL),
        "state_path": str(root / STATE_REL),
        "counts": counts,
        "latest_audit": {
            "present": audit is not None,
            "generated_at": (audit or {}).get("generated_at"),
            "digest_sha256": latest_digest,
            "engine_health": (audit or {}).get("engine_health"),
        },
        "tasks": tasks,
        "top_open_p0_p1": top_p0_p1,
        "repeat_offenders": repeat_offenders,
        "audit_resolved_candidates": audit_resolved,
        "addressed_unconfirmed": addressed_unconfirmed,
        "recommended_next": recommended_next,
    }


# ── rendering ────────────────────────────────────────────────────────────────


def _fmt_task(task: Dict[str, Any], *, verbose: bool = False) -> str:
    seen = task.get("first_seen") or ""
    last = task.get("last_seen") or ""
    line = (f"  [{task['task_id']}] {task['priority']:<3} "
            f"{task['area']:<18} x{task['repeat_count']} "
            f"{task['status']:<12} {task['task'][:96]}")
    if not verbose:
        return line
    extra = [line]
    if task.get("why"):
        extra.append(f"      why: {task['why'][:140]}")
    if task.get("success_metric"):
        extra.append(f"      success: {task['success_metric'][:140]}")
    extra.append(
        f"      first_seen: {seen[:16]} | last_seen: {last[:16]} | "
        f"digests: {len(task.get('source_digests') or [])} | "
        f"in_latest_audit: {task.get('still_in_latest_audit')}")
    if task.get("commit"):
        extra.append(f"      commit: {task['commit']} | "
                     f"follow_up: {task.get('follow_up_needed')}")
    return "\n".join(extra)


def render_review(review: Dict[str, Any], *, verbose: bool = False) -> str:
    lines: List[str] = []
    counts = review["counts"]
    audit = review["latest_audit"]
    lines.append("FEEDBACK QUEUE REVIEW (research-only bookkeeping — "
                 "no task is auto-executed)")
    lines.append(
        f"  tasks: {counts['total_tasks']} total | {counts['open']} open | "
        f"{counts['in_progress']} in-progress | "
        f"{counts['addressed']} addressed | {counts['dismissed']} dismissed "
        f"| {counts['superseded']} superseded | "
        f"{counts['resolved_by_audit']} resolved-by-audit")
    if audit["present"]:
        lines.append(f"  latest audit: {str(audit['generated_at'])[:16]} "
                     f"({audit['engine_health']}) "
                     f"digest {str(audit['digest_sha256'])[:12]}")
    else:
        lines.append("  latest audit: MISSING — audit cross-reference "
                     "unavailable")

    lines.append("\n1. TOP OPEN P0/P1 TASKS")
    if review["top_open_p0_p1"]:
        for t in review["top_open_p0_p1"]:
            lines.append(_fmt_task(t, verbose=verbose))
    else:
        lines.append("  (none open)")

    lines.append("\n2. REPEATEDLY QUEUED ACROSS AUDITS (open, >=2 digests)")
    if review["repeat_offenders"]:
        for t in review["repeat_offenders"]:
            lines.append(_fmt_task(t, verbose=verbose))
    else:
        lines.append("  (none)")

    lines.append("\n3. APPEAR RESOLVED BY LATEST AUDIT — no longer queued "
                 "(confirm with: resolve <id> --status resolved_by_audit)")
    if review["audit_resolved_candidates"]:
        for t in review["audit_resolved_candidates"]:
            lines.append(_fmt_task(t, verbose=verbose))
    else:
        lines.append("  (none)")

    lines.append("\n4. ADDRESSED WITH COMMIT, NOT YET CONFIRMED BY AUDIT")
    if review["addressed_unconfirmed"]:
        for t in review["addressed_unconfirmed"]:
            lines.append(_fmt_task(t, verbose=verbose))
    else:
        lines.append("  (none)")

    lines.append("\n5. RECOMMENDED NEXT TASK (human decides — nothing "
                 "runs automatically)")
    if review["recommended_next"]:
        lines.append(_fmt_task(review["recommended_next"], verbose=True))
    else:
        lines.append("  (queue clear)")
    return "\n".join(lines)


# ── CLI ──────────────────────────────────────────────────────────────────────


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Feedback queue review workflow (research-only; "
                    "task bookkeeping — never executes queued tasks).")
    p.add_argument("--root", default=None, help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="command", required=True)

    rev = sub.add_parser("review", help="session-start queue summary")
    rev.add_argument("--json", action="store_true",
                     help="emit the full review model as JSON")
    rev.add_argument("--verbose", action="store_true",
                     help="show why / success metric / audit linkage")

    res = sub.add_parser("resolve", help="record progress on a task")
    res.add_argument("task_id")
    res.add_argument("--status", default="addressed",
                     choices=[s.lower() for s in RECORDABLE_STATUSES
                              if s != "DISMISSED"],
                     help="lifecycle status to record (default: addressed)")
    res.add_argument("--commit", default=None,
                     help="commit hash that addressed the task")
    res.add_argument("--notes", default=None)
    res.add_argument("--files", default=None,
                     help="comma-separated files changed")
    res.add_argument("--follow-up", action="store_true",
                     help="mark that follow-up work is still needed")

    dis = sub.add_parser("dismiss", help="dismiss a task (queue entry is "
                                         "preserved; only state is appended)")
    dis.add_argument("task_id")
    dis.add_argument("--notes", default=None)
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    root = Path(args.root) if args.root else REPO_ROOT

    if args.command == "review":
        review = build_review(root)
        if args.json:
            print(json.dumps(review, ensure_ascii=False, indent=2))
        else:
            print(render_review(review, verbose=args.verbose))
        return 0

    if args.command == "resolve":
        files = [f.strip() for f in (args.files or "").split(",")
                 if f.strip()]
        try:
            entry = append_state_entry(
                args.task_id, args.status.upper(), root=root,
                commit=args.commit, notes=args.notes,
                files_changed=files, follow_up_needed=args.follow_up)
        except ValueError as exc:
            print(f"error: {exc}")
            return 1
        print(json.dumps(entry, ensure_ascii=False, indent=2))
        return 0

    if args.command == "dismiss":
        try:
            entry = append_state_entry(
                args.task_id, "DISMISSED", root=root, notes=args.notes)
        except ValueError as exc:
            print(f"error: {exc}")
            return 1
        print(json.dumps(entry, ensure_ascii=False, indent=2))
        return 0

    return 64


if __name__ == "__main__":
    sys.exit(main())

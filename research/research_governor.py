#!/usr/bin/env python3
"""Research Governor v0 — deterministic, audit-only review of the research system.

What it is
----------
An on-demand checker.  It reads the hand-edited component registry
(data/research/component_registry.json) plus whatever local artifacts,
ledgers, logs, scheduler unit files and budget counters exist, and says for
every component exactly one of: STOP_NOW, KEEP, FIX_NEXT, WAIT_FOR_MATURITY,
DO_NOT_BUILD, NEEDS_HUMAN_APPROVAL.  Its purpose is to catch drift, API
waste, stale surfaces, duplicate tools, fake precision and unvalidated alpha
claims before they cost attention.

What it is not
--------------
Not an LLM agent, not a scheduler, not a trading tool and not a ranker.  It
names no stocks.  It never changes a component status, never edits the
registry, never runs a recommended action, and never touches scanner, M1,
HC/EO/Alpha Focus, dashboard, MCP or timer code.  Every change it recommends
is made by a human.

Hard rules (pinned by tests/unit/test_research_governor.py)
-----------------------------------------------------------
  - Zero provider calls, zero LLM calls.  No provider or LLM client is
    imported, and no ``core.*`` module either: core.config needs credentials
    and core.provider_budget imports it lazily.  Budget facts are read from
    source text and, read-only, from the SQLite counters.
  - Writes exactly two files: the markdown report under docs/research/ and
    the JSON summary under cache/research/.  Any other output path is refused.
  - Missing or unreadable inputs become warnings, never crashes.
  - The only subprocess is ``git --no-optional-locks status`` (local, read-only),
    used to detect generated docs that dirty the working tree.

Usage:
  GEM_TRADER_SKIP_DOTENV=true .venv/bin/python -m research.research_governor
  ... [--registry PATH] [--output-md PATH] [--output-json PATH] [--quiet]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
VERSION = "RESEARCH_GOVERNOR_V0"

DEFAULT_REGISTRY_REL = "data/research/component_registry.json"
DEFAULT_OUTPUT_MD_REL = "docs/research/RESEARCH_GOVERNOR_REPORT_latest.md"
DEFAULT_OUTPUT_JSON_REL = "cache/research/research_governor_latest.json"
# The only places this module may write, keyed by file suffix.
APPROVED_OUTPUT_DIRS = {".md": "docs/research", ".json": "cache/research"}

SESSION_TZ = ZoneInfo("America/New_York")

# ── approved enums ───────────────────────────────────────────────────────────

STATUSES = ("CORE", "SUPPORT", "RESEARCH_ONLY", "QUARANTINE",
            "SUNSET_CANDIDATE", "DISABLED", "DECOMMISSIONED")
EVIDENCE_LEVELS = ("VALIDATED_EDGE", "PROVISIONAL_POOL_LEVEL_ONLY",
                   "FORWARD_IMMATURE", "REPLAY_CONTRADICTED", "FAILED_GATES",
                   "NOT_ENOUGH_EVIDENCE", "OPERATIONAL_CONTROL_ONLY")
RISK_LEVELS = ("NONE", "LOW", "MEDIUM", "HIGH", "UNKNOWN")
ACTIONS = ("STOP_NOW", "KEEP", "FIX_NEXT", "WAIT_FOR_MATURITY",
           "DO_NOT_BUILD", "NEEDS_HUMAN_APPROVAL")
VERDICTS = ("HEALTHY", "CAUTION", "DRIFTING", "BLOCKED",
            "RESEARCH_ONLY_NO_EDGE")
APPARATUS_ANSWERS = ("CLOSER_TO_ALPHA", "WAITING_ON_EVIDENCE",
                     "BUILDING_APPARATUS")

BLOCKED_STATUSES = frozenset({"QUARANTINE", "DECOMMISSIONED", "DISABLED"})
NO_SURFACE_EVIDENCE = frozenset({"REPLAY_CONTRADICTED", "FAILED_GATES"})
HUMAN_ACTIONS = frozenset({"STOP_NOW", "FIX_NEXT", "NEEDS_HUMAN_APPROVAL"})
# One action per item: the most urgent finding wins.
ACTION_PRECEDENCE = ("STOP_NOW", "FIX_NEXT", "NEEDS_HUMAN_APPROVAL",
                     "WAIT_FOR_MATURITY", "DO_NOT_BUILD", "KEEP")

REQUIRED_FIELDS = (
    "component_id", "name", "owner_file_or_artifact", "status",
    "evidence_level", "daily_decision_impact", "provider_call_risk",
    "allowed_to_surface_candidates", "allowed_in_daily_digest",
    "allowed_in_dashboard", "requires_human_approval_to_change",
    "next_decision_date", "maturity_date", "expiration_date", "notes")
BOOL_FIELDS = ("daily_decision_impact", "allowed_to_surface_candidates",
               "allowed_in_daily_digest", "allowed_in_dashboard",
               "requires_human_approval_to_change")
DATE_FIELDS = ("next_decision_date", "maturity_date", "expiration_date")
CADENCES = ("nightly", "premarket", "weekday", "cron_weekday", "weekly",
            "monthly", "manual", "on_demand", "continuous", "none")
# Weekday jobs get 96h so a Friday artifact is not "stale" on Monday.
CADENCE_MAX_AGE_HOURS = {"nightly": 96, "premarket": 96, "weekday": 96,
                         "cron_weekday": 96, "weekly": 216, "monthly": 840}
KINDS = ("surface", "tracker", "report", "digest", "audit", "queue", "gate",
         "filter", "library", "archive", "collector", "infrastructure",
         "display", "scheduler")
PURPOSE_CHECK_KINDS = frozenset({"surface", "tracker", "report"})
PROPOSAL_DECISIONS = ("DO_NOT_BUILD", "APPROVED")

DEFAULT_LIMITS = {
    "daily_review_list_max": 15,
    "max_active_candidate_surfaces": 2,
    "untriaged_days": 7,
    "drifting_warning_threshold": 12,
    "duplicate_overlap": 0.6,
    "duplicate_min_size": 5,
    "upcoming_days": 14,
    "unregistered_recent_days": 7,
    # A manual run able to spend more than this with no planned-call gate is
    # the 2026-09-05 accident class (20,260 calls in a day) → FIX_NEXT.
    "large_run_calls": 2000,
}

# Verdict strings found in local artifacts → evidence level.  Conservative:
# anything unmapped reads as NOT_ENOUGH_EVIDENCE.
_FI, _NEE, _FG = "FORWARD_IMMATURE", "NOT_ENOUGH_EVIDENCE", "FAILED_GATES"
VERDICT_TO_EVIDENCE = {
    "VALIDATED_EDGE": "VALIDATED_EDGE",
    "NEED_MORE_DATA": _FI, "NOT_MATURE": _FI,
    "INSUFFICIENT_MATURE_EVIDENCE": _FI, "INCONCLUSIVE": _FI,
    "PROMISING_BUT_UNPROVEN": _FI, "PROMISING_RESEARCH_SURFACE": _FI,
    "FORWARD_SHADOW_RESEARCH_ONLY": _FI, "IMMATURE": _FI,
    "MIXED": _NEE, "UNKNOWN": _NEE,
    # A positive comparative read is still only a comparison: the shortlist
    # beat a baseline on one regime window, which is not evidence of edge
    # until its own pre-registered maturity is met.  Mapped explicitly so
    # this is a decision rather than an unmapped default.
    "SHORTLIST_IMPROVES_OUTCOMES": _NEE,
    "NO_VALUE": _FG, "NO_FORWARD_EDGE": _FG, "FAIL": _FG, "FAILED": _FG,
    "LOOSE_NOT_BETTER": _FG, "NO_IMPROVEMENT_OVER_PROGRAM_CANDIDATES": _FG,
    "NO_EVIDENCE_OF_EDGE": _FG, "DECOMMISSIONED_AUTOPSY": _FG,
}
EVIDENCE_RANK = {"VALIDATED_EDGE": 5, "PROVISIONAL_POOL_LEVEL_ONLY": 3,
                 "FORWARD_IMMATURE": 2, "NOT_ENOUGH_EVIDENCE": 1,
                 "FAILED_GATES": 0, "REPLAY_CONTRADICTED": 0}

# Keys that make a sidecar look like a candidate list (used only to find
# candidate surfaces missing from the registry).
CANDIDATE_LIST_KEYS = ("candidates", "shortlist", "watch", "watchlist",
                       "leads", "review_now", "picks", "reviewed_candidates",
                       "items", "names", "top_candidates")
PER_TICKER_ARTIFACT_RE = re.compile(
    r"^(stock_lens_|executive_gatekeeper_|mcp_audit_ticker_|lens_)")

BANNED_LANGUAGE = re.compile(
    r"\b(?:buy|sell|hold|top[- ]picks?|price[- ]targets?)\b", re.IGNORECASE)
# Verdict tokens that read as promises out of context, and the words that
# qualify them.  Case-sensitive on the tokens: they are machine labels.
OPTIMISTIC_TOKEN_RE = re.compile(
    r"\b(PROMISING\w*|CORE_ENGINE_CANDIDATE|READY_TO_FEED_LENS|LENS_READY|"
    r"READY_FOR_[A-Z_]+|VALIDATED_EDGE|STRONG)\b")
OPTIMISTIC_PHRASE_RE = re.compile(r"promotion review|proven edge",
                                  re.IGNORECASE)
QUALIFIER_RE = re.compile(
    r"unproven|immature|not validated|not yet|not proven|research[- ]only|"
    r"need[_ ]more[_ ]data|insufficient|provisional|shadow|blocked|caution|"
    r"unverified|\bnot\b|\bno\b|\bnever\b|\buntil\b", re.IGNORECASE)
HISTORICAL_QUALIFIER_RE = re.compile(
    r"legacy|decommission|quarantin|historical|superseded|autopsy",
    re.IGNORECASE)

_PROVIDER_STEP_RE = re.compile(r'log\s+"\[PROVIDER\]')
_FN_START_RE = re.compile(r"^(cmd_[A-Za-z0-9_]+)\(\)\s*\{")
_DISPATCH_RE = re.compile(r"^\s*([a-z0-9][a-z0-9-]*)\)\s+(cmd_[a-z0-9_]+)\b")
_CMD_TOKEN_RE = re.compile(r"\bcmd_[A-Za-z0-9_]+\b")
_RUNNER_ARG_RE = re.compile(r"run_research_cycle\.sh\s+([a-z0-9][a-z0-9-]*)")
_CALL_CAP_RE = re.compile(
    r"^([A-Z][A-Z0-9_]*(?:MAX_CALLS|HARD_CAP|CALL_CAP)[A-Z0-9_]*)\s*=\s*"
    r"([0-9_]+)", re.MULTILINE)


# ── small helpers ────────────────────────────────────────────────────────────


def session_date(now: datetime) -> date:
    """The US session a run at ``now`` belongs to (ET calendar date)."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(SESSION_TZ).date()


def _parse_date(value: Any) -> Optional[date]:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _parse_ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return [str(value)]


def _read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _load_json(path: Path) -> Tuple[Any, Optional[str]]:
    if not path.exists():
        return None, "missing"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except Exception as exc:  # corrupt or partial file
        return None, f"unreadable ({type(exc).__name__})"


def dig(obj: Any, path: str) -> List[Any]:
    """Values at a dotted path; ``*`` fans out over dict values / list items."""
    current = [obj]
    for part in (path.split(".") if path else []):
        nxt: List[Any] = []
        for node in current:
            if part == "*":
                if isinstance(node, dict):
                    nxt.extend(node.values())
                elif isinstance(node, list):
                    nxt.extend(node)
            elif isinstance(node, dict) and part in node:
                nxt.append(node[part])
        current = nxt
    return current


def _extract_tickers(value: Any) -> List[str]:
    out: List[str] = []
    if isinstance(value, list):
        for v in value:
            if isinstance(v, str):
                out.append(v)
            elif isinstance(v, dict) and isinstance(v.get("ticker"), str):
                out.append(v["ticker"])
    return out


def _stem(path: str) -> str:
    name = Path(path).name
    for suffix in ("_latest.json", ".jsonl", ".json", ".md", ".py"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def language_hits(text: str) -> List[str]:
    return sorted({m.group(0).lower() for m in BANNED_LANGUAGE.finditer(text or "")})


def _md(text: Any) -> str:
    return str(text if text is not None else "—").replace("|", "/").replace("\n", " ")


# ── registry validation ──────────────────────────────────────────────────────


def validate_registry(registry: Any) -> List[Dict[str, Any]]:
    """Every rule the registry must satisfy before the governor trusts it."""
    errors: List[Dict[str, Any]] = []

    def err(cid: Optional[str], rule: str, message: str) -> None:
        errors.append({"component_id": cid, "rule": rule, "message": message})

    if not isinstance(registry, dict):
        err(None, "registry_shape", "registry is not a JSON object")
        return errors
    comps = registry.get("components")
    if not isinstance(comps, list) or not comps:
        err(None, "registry_shape", "registry has no components list")
        return errors

    seen: set = set()
    for i, c in enumerate(comps):
        if not isinstance(c, dict):
            err(f"#{i}", "component_shape", "component entry is not an object")
            continue
        cid = str(c.get("component_id") or f"#{i}")
        if cid in seen:
            err(cid, "duplicate_id", "component_id appears more than once")
        seen.add(cid)
        missing = [f for f in REQUIRED_FIELDS if f not in c]
        if missing:
            err(cid, "missing_fields", "missing: " + ", ".join(missing))
        status, ev = c.get("status"), c.get("evidence_level")
        if status not in STATUSES:
            err(cid, "status_enum", f"status {status!r} is not an approved status")
        if ev not in EVIDENCE_LEVELS:
            err(cid, "evidence_enum",
                f"evidence_level {ev!r} is not an approved evidence level")
        if c.get("provider_call_risk") not in RISK_LEVELS:
            err(cid, "risk_enum", f"provider_call_risk {c.get('provider_call_risk')!r} "
                "is not an approved risk level")
        for f in BOOL_FIELDS:
            if f in c and not isinstance(c[f], bool):
                err(cid, "bool_field", f"{f} must be true or false")
        if c.get("requires_human_approval_to_change") is not True:
            err(cid, "human_approval",
                "requires_human_approval_to_change must be true")
        for f in DATE_FIELDS:
            if c.get(f) is not None and _parse_date(c.get(f)) is None:
                err(cid, "date_field", f"{f} is not an ISO date")
        if c.get("cadence") is not None and c.get("cadence") not in CADENCES:
            err(cid, "cadence_enum", f"cadence {c.get('cadence')!r} is not approved")
        if c.get("kind") is not None and c.get("kind") not in KINDS:
            err(cid, "kind_enum", f"kind {c.get('kind')!r} is not approved")
        if c.get("allowed_to_surface_candidates") is True and (
                status in BLOCKED_STATUSES or ev in NO_SURFACE_EVIDENCE):
            err(cid, "surface_forbidden",
                f"a {status}/{ev} component cannot have "
                "allowed_to_surface_candidates=true")
        if status in BLOCKED_STATUSES:
            if c.get("allowed_in_daily_digest") is True:
                err(cid, "digest_forbidden",
                    f"a {status} component cannot be allowed_in_daily_digest")
            if c.get("allowed_in_dashboard") is True:
                err(cid, "dashboard_forbidden",
                    f"a {status} component cannot be allowed_in_dashboard")
        if ev == "VALIDATED_EDGE":
            approval = c.get("validated_edge_approval")
            approved = (isinstance(approval, dict)
                        and all(approval.get(k) for k in
                                ("approved_by", "approved_on", "evidence_ref"))
                        and len(str(c.get("notes") or "")) >= 20)
            if not approved:
                err(cid, "validated_edge_unapproved",
                    "VALIDATED_EDGE needs validated_edge_approval "
                    "(approved_by, approved_on, evidence_ref) and supporting notes")
        text = " ".join(str(c.get(k) or "") for k in
                        ("name", "notes", "purpose", "forward_hypothesis"))
        hits = language_hits(text)
        if hits:
            err(cid, "banned_language", "registry text uses: " + ", ".join(hits))

    for p in registry.get("build_proposals") or []:
        pid = str((p or {}).get("proposal_id") or "?")
        decision = (p or {}).get("decision")
        if decision not in PROPOSAL_DECISIONS:
            err(f"proposal:{pid}", "proposal_decision",
                f"decision {decision!r} must be DO_NOT_BUILD or APPROVED")
        if decision == "APPROVED" and not (p.get("approved_by") and p.get("approval_ref")):
            err(f"proposal:{pid}", "proposal_approval",
                "an APPROVED proposal needs approved_by and approval_ref")
    return errors


# ── run context ──────────────────────────────────────────────────────────────


class _Run:
    """Accumulates everything one governor pass observes."""

    def __init__(self, root: Path, registry: Dict[str, Any], now: datetime):
        self.root = root
        self.registry = registry
        self.now = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
        self.today = session_date(self.now)
        self.limits = {**DEFAULT_LIMITS, **(registry.get("limits") or {})}
        self.comps: List[Dict[str, Any]] = [
            c for c in (registry.get("components") or [])
            if isinstance(c, dict) and c.get("component_id")]
        self.by_id = {c["component_id"]: c for c in self.comps}
        self.registry_errors: List[Dict[str, Any]] = []
        self.warnings: List[Dict[str, Any]] = []
        self.notes: List[Dict[str, Any]] = []
        self.findings: Dict[str, List[Dict[str, str]]] = {}
        self.system_items: List[Dict[str, Any]] = []
        self.freshness: Dict[str, Dict[str, Any]] = {}
        self.lists: Dict[str, List[Dict[str, Any]]] = {}
        self.scheduled: Dict[str, List[str]] = {}
        self.schedule: Dict[str, Any] = {"timers": [], "provider_steps": {}}
        self.evidence: List[Dict[str, Any]] = []
        self.cost: Dict[str, Any] = {}
        self.queue: Dict[str, Any] = {}
        self.active_surfaces: List[str] = []
        self._json: Dict[Path, Tuple[Any, Optional[str]]] = {}
        self._digest_note: Optional[str] = None
        self._digest_loaded = False

    # paths / io
    def path(self, rel: str) -> Path:
        p = Path(os.path.expanduser(rel))
        return p if p.is_absolute() else self.root / p

    def json(self, rel: str) -> Tuple[Any, Optional[str]]:
        p = self.path(rel)
        if p not in self._json:
            self._json[p] = _load_json(p)
        return self._json[p]

    def digest_note(self) -> Optional[str]:
        """Latest Daily Research Digest note (read-only)."""
        if not self._digest_loaded:
            self._digest_loaded = True
            rel = (self.registry.get("digest_source") or {}).get(
                "journal", "data/research/journal.jsonl")
            text = _read_text(self.path(rel))
            for line in (text or "").splitlines():
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                if entry.get("source_view") == "journal_digest_script" and entry.get("note"):
                    self._digest_note = str(entry["note"])
        return self._digest_note

    # recording
    def warn(self, category: str, message: str, *,
             component_id: Optional[str] = None, severity: str = "WARN") -> None:
        row = {"category": category, "component_id": component_id,
               "severity": severity, "message": message}
        (self.notes if severity == "INFO" else self.warnings).append(row)

    def find(self, cid: str, action: str, reason: str, next_step: str,
             category: str) -> None:
        self.findings.setdefault(cid, []).append(
            {"action": action, "reason": reason, "next_step": next_step,
             "category": category})

    def system_item(self, cid: str, name: str, action: str, reason: str,
                    next_step: str, *, evidence: str = "OPERATIONAL_CONTROL_ONLY",
                    risk: str = "NONE", stale: str = "NOT_CHECKED") -> None:
        self.system_items.append(_item(cid, name, action, reason, evidence,
                                       risk, stale, next_step))


def _item(cid: str, name: str, action: str, reason: str, evidence: str,
          risk: str, stale: str, next_step: str,
          other: Optional[List[str]] = None) -> Dict[str, Any]:
    return {"component_id": cid, "component_name": name, "action": action,
            "reason": reason, "evidence_level": evidence,
            "provider_call_risk": risk, "stale_artifact_risk": stale,
            "human_approval_required": action in HUMAN_ACTIONS,
            "suggested_next_step": next_step, "other_findings": other or []}


def _is_candidate_surface(c: Dict[str, Any]) -> bool:
    return c.get("kind") == "surface" or bool(c.get("candidate_lists"))


def _artifact_specs(c: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for a in c.get("artifacts") or []:
        if isinstance(a, str):
            out.append({"path": a, "cadence": c.get("cadence")})
        elif isinstance(a, dict) and a.get("path"):
            out.append({"path": a["path"], "cadence": a.get("cadence", c.get("cadence"))})
    return out


def _component_stems(c: Dict[str, Any]) -> List[str]:
    stems = {_stem(a["path"]) for a in _artifact_specs(c)}
    stems |= set(_as_list(c.get("reader_refs")))
    return sorted(s for s in stems if len(s) >= 6)


# ── checks ───────────────────────────────────────────────────────────────────


def _parse_runner(text: str) -> Tuple[Dict[str, List[str]], Dict[str, str]]:
    """cmd_* function bodies (comment lines dropped) and the CLI dispatch map."""
    functions: Dict[str, List[str]] = {}
    dispatch: Dict[str, str] = {}
    current: Optional[str] = None
    for line in text.splitlines():
        m = _FN_START_RE.match(line)
        if m:
            current = m.group(1)
            functions[current] = []
            continue
        if current is not None:
            if line.startswith("}"):
                current = None
                continue
            if not line.lstrip().startswith("#"):
                functions[current].append(line)
            continue
        d = _DISPATCH_RE.match(line)
        if d and not line.lstrip().startswith("#"):
            dispatch.setdefault(d.group(1), d.group(2))
    return functions, dispatch


def _reachable(functions: Dict[str, List[str]], start: str) -> List[str]:
    seen: List[str] = []
    stack = [start]
    while stack:
        fn = stack.pop()
        if fn in seen or fn not in functions:
            continue
        seen.append(fn)
        for line in functions[fn]:
            stack.extend(t for t in _CMD_TOKEN_RE.findall(line) if t not in seen)
    return seen


def check_schedule(run: _Run) -> None:
    """Enabled timers → runner entry points → reachable steps."""
    sched = run.registry.get("scheduler") or {}
    runner_text = _read_text(run.path(sched.get("runner", "scripts/run_research_cycle.sh")))
    functions, dispatch = _parse_runner(runner_text or "")
    if runner_text is None:
        run.warn("scheduler_unreadable", "research runner script not found",
                 severity="INFO")
    timers: List[Dict[str, Any]] = []
    for d in sched.get("unit_dirs") or []:
        unit_dir = run.path(d)
        if not unit_dir.is_dir():
            run.warn("scheduler_unreadable", f"unit dir {d} not readable", severity="INFO")
            continue
        for timer in sorted(unit_dir.glob(sched.get("unit_glob", "gem-trader*.timer"))):
            timer_text = _read_text(timer) or ""
            service_text = _read_text(timer.with_suffix(".service")) or ""
            schedule = next((l.split("=", 1)[1].strip() for l in timer_text.splitlines()
                             if l.startswith("OnCalendar=")), None)
            execs = [l.split("=", 1)[1].strip() for l in service_text.splitlines()
                     if l.startswith("ExecStart=")]
            enabled = (unit_dir / "timers.target.wants" / timer.name).exists()
            runner_arg = None
            for e in execs:
                m = _RUNNER_ARG_RE.search(e)
                if m:
                    runner_arg = m.group(1)
            entry = dispatch.get(runner_arg) if runner_arg else None
            reach = _reachable(functions, entry) if entry else []
            provider_steps = sorted(fn for fn in reach
                                    if any(_PROVIDER_STEP_RE.search(l) for l in functions.get(fn, [])))
            timers.append({"timer": timer.name, "unit_dir": d, "enabled": enabled,
                           "schedule": schedule, "exec": execs,
                           "runner_arg": runner_arg, "entry_function": entry,
                           "reachable": reach, "provider_steps": provider_steps})
    run.schedule = {"timers": timers, "functions": functions,
                    "cron_note": sched.get("cron_note")}
    if not timers:
        run.warn("scheduler_unreadable", "no timer unit files found in the configured "
                 "unit dirs; scheduled-job checks are incomplete", severity="INFO")

    enabled = [t for t in timers if t["enabled"]]
    for c in run.comps:
        refs = _as_list(c.get("runner_refs"))
        owners = _as_list(c.get("owner_file_or_artifact"))
        hits: List[str] = []
        for t in enabled:
            body = "\n".join("\n".join(functions.get(fn, [])) for fn in t["reachable"])
            called = set(t["reachable"])
            if any((r in called) or (r in body) for r in refs):
                hits.append(t["timer"])
            elif any(o and any(o in e for e in t["exec"]) for o in owners):
                hits.append(t["timer"])
        run.scheduled[c["component_id"]] = sorted(set(hits))


def check_freshness(run: _Run) -> None:
    for c in run.comps:
        cid = c["component_id"]
        shown = bool(c.get("allowed_in_daily_digest") or c.get("allowed_in_dashboard")
                     or c.get("daily_decision_impact"))
        blocked = c.get("status") in BLOCKED_STATUSES
        rows, state = [], "NOT_CHECKED"
        for spec in _artifact_specs(c):
            p = run.path(spec["path"])
            max_age = CADENCE_MAX_AGE_HOURS.get(spec.get("cadence") or "")
            if not p.exists():
                rows.append({"path": spec["path"], "state": "MISSING"})
                if max_age and not blocked:
                    run.warn("missing_artifact", f"{spec['path']} is missing",
                             component_id=cid)
                    if shown:
                        run.find(cid, "FIX_NEXT", f"artifact {spec['path']} is missing",
                                 "rerun its producer or remove it from the registry",
                                 "missing_artifact")
                else:
                    run.warn("missing_artifact", f"{spec['path']} is missing",
                             component_id=cid, severity="INFO")
                state = "MISSING"
                continue
            payload = run.json(spec["path"])[0] if p.suffix == ".json" else None
            ts = _parse_ts(payload.get("generated_at")) if isinstance(payload, dict) else None
            if ts is None:
                ts = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
            age_h = round((run.now - ts).total_seconds() / 3600.0, 1)
            stale = bool(max_age and age_h > max_age)
            rows.append({"path": spec["path"], "age_hours": age_h,
                         "state": "STALE" if stale else ("FRESH" if max_age else "NOT_CHECKED")})
            if stale:
                if state != "MISSING":
                    state = "STALE"
                if shown and not blocked:
                    run.warn("stale_live_artifact",
                             f"{spec['path']} is {age_h:.0f}h old against a "
                             f"{spec.get('cadence')} cadence but is still shown as current",
                             component_id=cid)
                    run.find(cid, "FIX_NEXT", f"{spec['path']} is stale ({age_h:.0f}h) "
                             "while still shown as current",
                             "rerun the producer, or label the surface stale until it is",
                             "stale_live_artifact")
            elif max_age and state == "NOT_CHECKED":
                state = "FRESH"
        run.freshness[cid] = {"state": state, "artifacts": rows}


def check_candidate_surfaces(run: _Run) -> None:
    limit = int(run.limits["daily_review_list_max"])
    primary: Dict[str, set] = {}
    for c in run.comps:
        cid = c["component_id"]
        entries = []
        for spec in c.get("candidate_lists") or []:
            payload, error = run.json(spec.get("artifact", ""))
            names = _extract_tickers(next(iter(dig(payload, spec.get("path", ""))), None)) \
                if payload is not None else []
            entries.append({"label": spec.get("label") or spec.get("path"),
                            "artifact": spec.get("artifact"),
                            "count": len(names) if payload is not None else None,
                            "daily_review_list": bool(spec.get("daily_review_list")),
                            "error": error})
            if payload is not None and not primary.get(cid):
                primary[cid] = set(names)
            if spec.get("daily_review_list") and payload is not None and len(names) > limit:
                run.warn("daily_list_over_limit",
                         f"{c.get('name')}: daily review list '{entries[-1]['label']}' "
                         f"has {len(names)} names (limit {limit})", component_id=cid)
                run.find(cid, "FIX_NEXT",
                         f"daily review list has {len(names)} names, above the {limit}-name limit",
                         f"cap the list at {limit}, or document it as weekly depth (human decision)",
                         "daily_list_over_limit")
        run.lists[cid] = entries

    active = [c for c in run.comps
              if c.get("allowed_to_surface_candidates") is True
              and c.get("status") not in BLOCKED_STATUSES]
    run.active_surfaces = [c["component_id"] for c in active]
    max_surfaces = int(run.limits["max_active_candidate_surfaces"])
    if len(active) > max_surfaces:
        run.warn("too_many_candidate_surfaces",
                 f"{len(active)} active candidate surfaces against a limit of {max_surfaces}",
                 severity="HIGH")
        run.system_item(
            "system:candidate_surface_count", "Active candidate surfaces",
            "NEEDS_HUMAN_APPROVAL",
            f"{len(active)} components may surface candidates "
            f"({', '.join(run.active_surfaces)}); the registry limit is {max_surfaces}",
            "decide which surfaces stay as sources of names; demote the rest to "
            "labels on the board (registry change, human decision)")

    thr, min_size = float(run.limits["duplicate_overlap"]), int(run.limits["duplicate_min_size"])
    ids = sorted(primary)
    pairs = []
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            if a not in run.active_surfaces or b not in run.active_surfaces:
                continue
            sa, sb = primary[a], primary[b]
            small = min(len(sa), len(sb))
            if small < min_size:
                continue
            overlap = len(sa & sb) / small
            if overlap >= thr:
                pairs.append(f"{a} ~ {b} ({overlap:.0%} of the smaller list)")
    if pairs:
        run.warn("duplicate_candidate_lists", "overlapping candidate lists: " + "; ".join(pairs))
        run.system_item("system:duplicate_candidate_lists", "Duplicate candidate lists",
                        "NEEDS_HUMAN_APPROVAL", "; ".join(pairs),
                        "keep one list per question; show the other as a label (human decision)")


def check_unregistered_surfaces(run: _Run) -> None:
    claimed = {str(run.path(a["path"])) for c in run.comps for a in _artifact_specs(c)}
    claimed |= {str(run.path(s.get("artifact", ""))) for c in run.comps
                for s in c.get("candidate_lists") or []}
    claimed |= {str(run.path(p)) for p in run.registry.get("ignored_artifacts") or []}
    claimed.add(str(run.path(DEFAULT_OUTPUT_JSON_REL)))
    recent = float(run.limits["unregistered_recent_days"]) * 86400
    for d in run.registry.get("surface_scan_dirs") or ["cache/research"]:
        base = run.path(d)
        if not base.is_dir():
            continue
        for p in sorted(base.glob("*_latest.json")):
            if PER_TICKER_ARTIFACT_RE.match(p.name) or str(p) in claimed:
                continue
            if run.now.timestamp() - p.stat().st_mtime > recent:
                continue
            payload, _ = _load_json(p)
            if not isinstance(payload, dict):
                continue
            keys = [k for k in CANDIDATE_LIST_KEYS
                    if isinstance(payload.get(k), list) and len(payload[k]) >= 3
                    and isinstance(payload[k][0], dict) and "ticker" in payload[k][0]]
            if not keys:
                continue
            rel = str(p.relative_to(run.root)) if run.root in p.parents else str(p)
            run.warn("unregistered_candidate_surface",
                     f"{rel} emits a candidate list ({', '.join(keys)}) and is not in the registry",
                     severity="HIGH")
            run.system_item(f"unregistered:{_stem(p.name)}", f"Unregistered surface {rel}",
                            "NEEDS_HUMAN_APPROVAL",
                            "recently updated sidecar emits a candidate list but has no registry entry",
                            "register it with a status and evidence level, or add it to "
                            "ignored_artifacts if it is not a candidate surface",
                            evidence="NOT_ENOUGH_EVIDENCE", risk="UNKNOWN")


def check_readers(run: _Run) -> None:
    surfaces = []
    for s in run.registry.get("reader_surfaces") or []:
        text = _read_text(run.path(s.get("path", "")))
        if text is None:
            run.warn("missing_reader_surface", f"reader surface {s.get('path')} not found",
                     severity="INFO")
            continue
        surfaces.append((s.get("path"), s.get("kind", "dashboard"), text,
                         "quarantined_surfaces" in text))
    for c in run.comps:
        cid = c["component_id"]
        stems = _component_stems(c)
        owners = set(_as_list(c.get("owner_file_or_artifact")))
        blocked = c.get("status") in BLOCKED_STATUSES
        for path, kind, text, gated in surfaces:
            if path in owners:
                continue
            hit = [s for s in stems if s in text]
            if not hit:
                continue
            if blocked and gated:
                run.warn("quarantined_reference_gated",
                         f"{path} reads {', '.join(hit)} through the quarantine gate",
                         component_id=cid, severity="INFO")
            elif blocked:
                run.warn("quarantined_referenced",
                         f"{path} reads {', '.join(hit)} of a {c.get('status')} component "
                         "without the quarantine gate", component_id=cid, severity="HIGH")
                if _is_candidate_surface(c):
                    run.find(cid, "STOP_NOW",
                             f"quarantined candidate surface is read by {path} without the gate",
                             "remove the read or gate it through core.quarantined_surfaces",
                             "quarantined_referenced")
                else:
                    run.find(cid, "FIX_NEXT",
                             f"{path} loads its artifact without the quarantine gate",
                             "drop the load or gate it through core.quarantined_surfaces",
                             "quarantined_referenced")
            else:
                allowed = (c.get("allowed_in_dashboard") if kind == "dashboard"
                           else c.get("allowed_in_daily_digest"))
                if allowed is False:
                    run.warn("not_allowed_reference",
                             f"{path} ({kind}) reads {', '.join(hit)} although the registry "
                             f"does not allow {c.get('name')} there", component_id=cid)
                    run.find(cid, "NEEDS_HUMAN_APPROVAL",
                             f"{path} ({kind}) reads it although the registry does not allow it",
                             "remove the read, or change the registry flag (human decision)",
                             "not_allowed_reference")

    note = run.digest_note()
    if not note:
        return
    lines = note.splitlines()
    for c in run.comps:
        if c.get("status") not in BLOCKED_STATUSES:
            continue
        for alias in _as_list(c.get("display_aliases")):
            for n, line in enumerate(lines, 1):
                if alias.lower() in line.lower() and not HISTORICAL_QUALIFIER_RE.search(line):
                    cid = c["component_id"]
                    run.warn("quarantined_shown_as_live",
                             f"latest digest line {n} mentions '{alias}' with no "
                             "legacy/quarantine qualifier", component_id=cid, severity="HIGH")
                    run.find(cid, "STOP_NOW" if _is_candidate_surface(c) else "FIX_NEXT",
                             f"the daily digest shows '{alias}' as if current",
                             "remove it from the digest or label it historical",
                             "quarantined_shown_as_live")
                    break


def check_labels(run: _Run) -> None:
    for src in run.registry.get("label_scan_sources") or []:
        cid = src.get("component_id")
        if src.get("source") == "journal_digest_note":
            text, where = run.digest_note(), "latest digest note"
        elif src.get("json_path"):
            payload, _ = run.json(src.get("path", ""))
            text = "\n".join(str(v) for v in dig(payload, src["json_path"])
                             if isinstance(v, str)) if payload is not None else None
            where = f"{src.get('path')}:{src['json_path']}"
        else:
            text, where = _read_text(run.path(src.get("path", ""))), src.get("path")
        if text is None:
            run.warn("missing_artifact", f"label scan source {where} not found",
                     component_id=cid, severity="INFO")
            continue
        flagged = []
        for n, line in enumerate(text.splitlines(), 1):
            tokens = OPTIMISTIC_TOKEN_RE.findall(line) + OPTIMISTIC_PHRASE_RE.findall(line)
            if tokens and not QUALIFIER_RE.search(line):
                flagged.append(f"line {n}: {sorted(set(tokens))[0]}")
        if flagged:
            run.warn("optimistic_label",
                     f"{where}: {len(flagged)} optimistic label(s) without qualifying "
                     f"language ({'; '.join(flagged[:4])})", component_id=cid)
            if cid in run.by_id:
                run.find(cid, "FIX_NEXT",
                         f"{len(flagged)} optimistic label(s) with no qualifier in {where}",
                         "add the qualifier (unproven / immature / research-only) or reword",
                         "optimistic_label")


def check_line_counts(run: _Run) -> None:
    for c in run.comps:
        probe = c.get("line_count_probe")
        if not probe:
            continue
        cid = c["component_id"]
        text = (run.digest_note() if probe.get("source") == "journal_digest_note"
                else _read_text(run.path(probe.get("source", ""))))
        if text is None:
            run.warn("missing_artifact", f"{c.get('name')}: line-count source not found",
                     component_id=cid, severity="INFO")
            continue
        n, cap = len(text.splitlines()), int(probe.get("max_lines", 60))
        if n > cap:
            run.warn("digest_sprawl", f"{c.get('name')}: {n} lines (limit {cap})",
                     component_id=cid)
            run.find(cid, "FIX_NEXT", f"{n} lines against a {cap}-line limit",
                     "cut sections that have not changed a decision in 30 days",
                     "digest_sprawl")


def _git_modified(root: Path, paths: List[str]) -> Tuple[Optional[List[str]], Optional[str]]:
    try:
        out = subprocess.run(
            ["git", "--no-optional-locks", "status", "--porcelain",
             "--untracked-files=no", "--", *paths],
            cwd=str(root), capture_output=True, text=True, timeout=20)
    except Exception as exc:
        return None, type(exc).__name__
    if out.returncode != 0:
        return None, (out.stderr.strip().splitlines() or [f"exit {out.returncode}"])[0][:120]
    return sorted(line[3:].strip() for line in out.stdout.splitlines() if line.strip()), None


def _git_tracked(root: Path, paths: List[str]) -> Tuple[Optional[List[str]], Optional[str]]:
    """Which of ``paths`` git still tracks (read-only)."""
    try:
        out = subprocess.run(
            ["git", "--no-optional-locks", "ls-files", "--", *paths],
            cwd=str(root), capture_output=True, text=True, timeout=20)
    except Exception as exc:
        return None, type(exc).__name__
    if out.returncode != 0:
        return None, (out.stderr.strip().splitlines() or [f"exit {out.returncode}"])[0][:120]
    return sorted(p.strip() for p in out.stdout.splitlines() if p.strip()), None


def check_generated_docs(run: _Run) -> None:
    docs = [d for d in run.registry.get("generated_docs") or []
            if d != DEFAULT_OUTPUT_MD_REL]
    if not docs:
        return
    # Only a *tracked* report can dirty the tree. Once a generated report is
    # untracked and ignored it is no longer version-controlled noise, so it is
    # not a finding — and while the untracking is still staged, git reports the
    # path as a deletion, which would otherwise read as one more dirty doc.
    tracked, error = _git_tracked(run.root, docs)
    if error:
        run.warn("git_unavailable", f"git status unavailable ({error}); generated-doc "
                 "check skipped", severity="INFO")
        return
    if not tracked:
        run.warn("generated_docs_ignored",
                 f"{len(docs)} generated report(s) are untracked and ignored, so they "
                 "cannot dirty the working tree", severity="INFO")
        return
    modified, error = _git_modified(run.root, tracked)
    if error:
        run.warn("git_unavailable", f"git status unavailable ({error}); generated-doc "
                 "check skipped", severity="INFO")
        return
    if modified:
        run.warn("generated_docs_dirty",
                 f"{len(modified)} tracked generated doc(s) rewritten by scheduled jobs "
                 f"leave the working tree dirty: {', '.join(modified)}")
        run.system_item("system:generated_docs", "Generated docs dirtying git",
                        "FIX_NEXT",
                        f"{len(modified)} tracked docs are rewritten by scheduled jobs",
                        "stop tracking generated reports (git rm --cached + ignore) or "
                        "write them outside docs/ (human decision)")


def check_test_debt(run: _Run) -> None:
    for t in run.registry.get("known_test_debt") or []:
        test = str(t.get("test") or "?")
        if not run.path(test).exists():
            run.warn("test_debt", f"{test} listed as test debt but no longer exists",
                     severity="INFO")
            continue
        run.warn("date_rotted_test",
                 f"{test}: {t.get('failing', '?')} failing — {t.get('issue')}")
        run.system_item(f"system:test_debt:{_stem(test)}", f"Test debt {test}", "FIX_NEXT",
                        f"{t.get('failing', '?')} known failures since "
                        f"{t.get('first_recorded', '?')}: {t.get('issue')}",
                        "pin the fixture dates (inject 'today') so the suite stays green")


def check_queue(run: _Run) -> None:
    try:
        from research import feedback_queue as fq  # stdlib-only bookkeeping module
    except ImportError:
        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        from research import feedback_queue as fq
    review = fq.build_review(run.root)
    counts = review.get("counts") or {}
    state_ts = [_parse_ts(r.get("ts")) for r in fq._read_jsonl(run.root / fq.STATE_REL)]
    queue_ts = [_parse_ts(r.get("queued_at")) for r in fq._read_jsonl(run.root / fq.QUEUE_REL)]
    last_state = max((t for t in state_ts if t), default=None)
    last_queued = max((t for t in queue_ts if t), default=None)
    pending = (int(counts.get("open", 0)) + int(counts.get("in_progress", 0))
               + len(review.get("addressed_unconfirmed") or [])
               + len(review.get("audit_resolved_candidates") or []))
    age = (run.now - last_state).days if last_state else None
    run.queue = {"queue_lines": len(queue_ts), "distinct_tasks": counts.get("total_tasks", 0),
                 "counts": counts, "pending_human_review": pending,
                 "last_state_entry": last_state.isoformat() if last_state else None,
                 "last_queued": last_queued.isoformat() if last_queued else None,
                 "days_since_triage": age}
    limit = int(run.limits["untriaged_days"])
    if pending and (age is None or age > limit):
        msg = (f"{pending} queue item(s) await a human; last triage "
               + (f"{age} days ago" if age is not None else "never recorded"))
        run.warn("untriaged_queue", msg, component_id="feedback_queue")
        if "feedback_queue" in run.by_id:
            run.find("feedback_queue", "FIX_NEXT", msg,
                     "run ./scripts/run_research_cycle.sh feedback-queue-review and "
                     "resolve or dismiss each item", "untriaged_queue")
        else:
            run.system_item("system:feedback_queue", "Feedback queue", "FIX_NEXT", msg,
                            "run feedback-queue-review and triage")


def check_dates(run: _Run) -> None:
    upcoming = int(run.limits["upcoming_days"])
    for c in run.comps:
        cid = c["component_id"]
        nd = _parse_date(c.get("next_decision_date"))
        if nd and nd < run.today:
            run.warn("decision_date_passed",
                     f"{c.get('name')}: next decision date {nd} has passed", component_id=cid)
            run.find(cid, "NEEDS_HUMAN_APPROVAL", f"decision date {nd} passed with no "
                     "recorded decision", "record the decision and set the next date",
                     "decision_date_passed")
        elif nd and (nd - run.today).days <= upcoming:
            run.warn("decision_date_upcoming", f"{c.get('name')}: decision due {nd}",
                     component_id=cid, severity="INFO")
        exp = _parse_date(c.get("expiration_date"))
        if exp and exp < run.today:
            run.warn("expired", f"{c.get('name')}: expired {exp}", component_id=cid)
            run.find(cid, "NEEDS_HUMAN_APPROVAL", f"expiration date {exp} has passed",
                     "review whether it drove a decision; retire it if not", "expired")
        mat = _parse_date(c.get("maturity_date"))
        if mat and mat > run.today:
            run.warn("maturity_gate_not_reached", f"{c.get('name')}: matures {mat}",
                     component_id=cid, severity="INFO")
        for probe in c.get("decision_date_probes") or []:
            payload, error = run.json(probe.get("artifact", ""))
            if payload is None:
                continue
            for v in dig(payload, probe.get("path", "")):
                d = _parse_date(v)
                if d and d < run.today:
                    run.warn("artifact_decision_date_passed",
                             f"{probe.get('artifact')} names {d} as the next decision date, "
                             "which has passed", component_id=cid)
                    run.find(cid, "FIX_NEXT", f"its artifact still names passed date {d} as next",
                             "rerun the producer (the 24deb80 fix picks the next valid date)",
                             "artifact_decision_date_passed")


def check_purpose(run: _Run) -> None:
    for c in run.comps:
        cid = c["component_id"]
        if c.get("status") == "SUNSET_CANDIDATE":
            run.find(cid, "NEEDS_HUMAN_APPROVAL", "marked SUNSET_CANDIDATE",
                     "decide: retire (DECOMMISSIONED) or keep with a dated reason",
                     "sunset_candidate")
        if c.get("status") == "QUARANTINE" and not c.get("next_decision_date"):
            run.find(cid, "NEEDS_HUMAN_APPROVAL", "quarantined with no decision date",
                     "retire it, or register a forward hypothesis and a date",
                     "quarantine_undated")
        if c.get("status") in BLOCKED_STATUSES or c.get("kind") not in PURPOSE_CHECK_KINDS:
            continue
        dated = any(c.get(f) for f in DATE_FIELDS)
        if (not dated and not c.get("forward_hypothesis")
                and (not c.get("purpose") or c.get("consumers") == [])):
            run.warn("no_decision_date_unclear_purpose",
                     f"{c.get('name')}: no decision date, no forward hypothesis, "
                     "no consumer", component_id=cid)
            run.find(cid, "NEEDS_HUMAN_APPROVAL",
                     "no decision date, no forward hypothesis and no consumer",
                     "give it a hypothesis and a date, or schedule it for retirement",
                     "no_decision_date_unclear_purpose")
        if (c.get("allowed_to_surface_candidates") is True
                and not c.get("forward_hypothesis")):
            run.warn("unvalidated_candidate_surface",
                     f"{c.get('name')} surfaces candidates with no registered forward hypothesis",
                     component_id=cid)
            run.find(cid, "NEEDS_HUMAN_APPROVAL",
                     "surfaces candidates with no registered forward hypothesis",
                     "register a pre-declared forward test, or treat it as display ordering only",
                     "unvalidated_candidate_surface")


def check_scheduled_components(run: _Run) -> None:
    for c in run.comps:
        cid = c["component_id"]
        timers = run.scheduled.get(cid) or []
        if not timers:
            continue
        if c.get("status") in BLOCKED_STATUSES | {"SUNSET_CANDIDATE"}:
            run.warn("scheduled_blocked_component",
                     f"{c.get('name')} is {c.get('status')} but still runs from "
                     f"{', '.join(timers)}", component_id=cid)
            run.find(cid, "FIX_NEXT", f"{c.get('status')} but still scheduled ({', '.join(timers)})",
                     "remove it from the scheduled cycle; keep its files as history",
                     "scheduled_blocked_component")
        elif (c.get("evidence_level") in NO_SURFACE_EVIDENCE
              and c.get("kind") in {"surface", "report"}):
            run.warn("scheduled_failed_research",
                     f"{c.get('name')} has {c.get('evidence_level')} but still runs from "
                     f"{', '.join(timers)}", component_id=cid)
            run.find(cid, "FIX_NEXT", f"{c.get('evidence_level')} yet still scheduled "
                     f"({', '.join(timers)})", "move it to weekly or on-demand",
                     "scheduled_failed_research")


def check_precision(run: _Run) -> None:
    for c in run.comps:
        cid = c["component_id"]
        for probe in c.get("precision_probes") or []:
            payload, error = run.json(probe.get("artifact", ""))
            label = probe.get("label") or probe.get("path") or probe.get("mean_path")
            if payload is None:
                run.warn("missing_artifact", f"precision probe artifact {probe.get('artifact')} "
                         f"{error}", component_id=cid, severity="INFO")
                continue
            detail = None
            if probe.get("kind") == "ratio_max":
                v = next(iter(dig(payload, probe.get("path", ""))), None)
                if isinstance(v, (int, float)) and v > float(probe.get("max", 1)):
                    detail = f"{label} is {v} (max {probe.get('max')})"
            elif probe.get("kind") == "mean_median_gap":
                mean = next(iter(dig(payload, probe.get("mean_path", ""))), None)
                median = next(iter(dig(payload, probe.get("median_path", ""))), None)
                if (isinstance(mean, (int, float)) and isinstance(median, (int, float))
                        and abs(mean - median) > float(probe.get("max_gap_pp", 10))):
                    detail = (f"{label}: mean {mean} vs median {median} — outlier-contaminated "
                              "headline")
            if detail:
                run.warn("fake_precision", f"{c.get('name')}: {detail}", component_id=cid)
                run.find(cid, "FIX_NEXT", f"fake precision: {detail}",
                         "use the median / winsorized statistic (or deduplicated n) "
                         "before any claim rests on it", "fake_precision")


def check_evidence(run: _Run) -> None:
    for c in run.comps:
        probe = c.get("evidence_probe")
        label = c.get("evidence_path_label")
        if not probe and not label:
            continue
        cid, reg_level = c["component_id"], c.get("evidence_level")
        observed_verdicts: List[str] = []
        observed_level, age_h, note = None, None, ""
        artifact_missing = False
        if probe:
            payload, error = run.json(probe.get("artifact", ""))
            if payload is None:
                artifact_missing = True
                observed_level, note = "NOT_ENOUGH_EVIDENCE", f"artifact {error}"
                run.warn("missing_artifact", f"evidence artifact {probe.get('artifact')} "
                         f"{error}", component_id=cid)
            else:
                observed_verdicts = sorted({str(v).upper() for v in
                                            dig(payload, probe.get("path", "")) if v})
                levels = [VERDICT_TO_EVIDENCE.get(v, "NOT_ENOUGH_EVIDENCE")
                          for v in observed_verdicts]
                observed_level = (min(levels, key=lambda l: EVIDENCE_RANK.get(l, 1))
                                  if levels else "NOT_ENOUGH_EVIDENCE")
                ts = _parse_ts(payload.get("generated_at")) if isinstance(payload, dict) else None
                if ts:
                    age_h = round((run.now - ts).total_seconds() / 3600.0, 1)
                if not observed_verdicts:
                    note = "artifact carries no verdict field"
        # No probe, or an artifact that carries no verdict: nothing to compare.
        # A MISSING artifact is compared (as NOT_ENOUGH_EVIDENCE) on purpose.
        if not probe or (not artifact_missing and not observed_verdicts):
            agreement = "UNVERIFIED"
            observed_level = observed_level or "NOT_ENOUGH_EVIDENCE"
        elif reg_level not in EVIDENCE_RANK:
            agreement = "NOT_APPLICABLE"
        elif EVIDENCE_RANK[reg_level] > EVIDENCE_RANK.get(observed_level, 1):
            agreement = "REGISTRY_MORE_OPTIMISTIC"
        elif EVIDENCE_RANK[reg_level] < EVIDENCE_RANK.get(observed_level, 1):
            agreement = "REGISTRY_MORE_CONSERVATIVE"
        else:
            agreement = "AGREES"
        if agreement == "REGISTRY_MORE_OPTIMISTIC":
            run.warn("registry_evidence_mismatch",
                     f"{c.get('name')}: registry says {reg_level}, artifact says "
                     f"{'/'.join(observed_verdicts) or observed_level}", component_id=cid)
            run.find(cid, "NEEDS_HUMAN_APPROVAL",
                     f"registry evidence {reg_level} is more optimistic than the artifact "
                     f"({'/'.join(observed_verdicts) or observed_level})",
                     "reconcile: fix the artifact's statistic or downgrade the registry level",
                     "registry_evidence_mismatch")
        elif agreement == "REGISTRY_MORE_CONSERVATIVE":
            run.warn("registry_more_conservative",
                     f"{c.get('name')}: registry {reg_level} is stricter than the artifact "
                     f"({'/'.join(observed_verdicts)})", component_id=cid, severity="INFO")
        run.evidence.append({
            "component_id": cid, "path_label": label or c.get("name"),
            "registry_evidence_level": reg_level,
            "observed_verdicts": observed_verdicts,
            "observed_evidence_level": observed_level,
            "agreement": agreement, "artifact": (probe or {}).get("artifact"),
            "artifact_age_hours": age_h,
            "evidence_source": c.get("evidence_source"), "note": note})


def _static_budget_facts(run: _Run) -> Dict[str, Any]:
    src = run.registry.get("cost_sources") or {}
    pb = _read_text(run.path(src.get("provider_budget_module", "core/provider_budget.py"))) or ""
    gk = _read_text(run.path(src.get("gatekeeper_module", "core/data_gatekeeper.py"))) or ""
    lu = _read_text(run.path(src.get("llm_usage_module", "core/llm_clients/usage.py"))) or ""

    def const(text: str, name: str) -> Optional[int]:
        m = re.search(rf"^{name}\s*=\s*([0-9_]+)", text, re.MULTILINE)
        return int(m.group(1).replace("_", "")) if m else None

    zero_default = "n if n > 0 else default" in pb
    unlimited_var = "ALLOW_UNLIMITED_PROVIDER_CALLS" in pb
    return {
        "provider_budget_module_present": bool(pb),
        "budget_refusal_guards_exist": bool(pb) and "check_spend" in gk,
        "default_monthly_cap": const(pb, "DEFAULT_MONTHLY_CAP"),
        "default_daily_cap": const(pb, "DEFAULT_DAILY_CAP"),
        "planned_call_confirm_threshold": const(pb, "PLANNED_CALL_CONFIRM_THRESHOLD"),
        "fmp_monthly_budget_zero_means": (
            "the documented default cap; unlimited requires ALLOW_UNLIMITED_PROVIDER_CALLS=true"
            if zero_default and unlimited_var else
            "UNKNOWN — could not confirm from core/provider_budget.py; treat 0 as unlimited"),
        "unlimited_requires_explicit_opt_in": zero_default and unlimited_var,
        "llm_daily_max_calls_default": const(lu, "DEFAULT_DAILY_MAX_CALLS"),
        "llm_daily_max_tokens_default": const(lu, "DEFAULT_DAILY_MAX_TOKENS"),
        "env_overrides": "not read — the credentials file is never opened by design",
    }


def _fmp_usage(run: _Run) -> Dict[str, Any]:
    rel = (run.registry.get("cost_sources") or {}).get("fmp_db", "db/trading.db")
    p = run.path(rel)
    if not p.exists():
        run.warn("missing_artifact", f"FMP budget database {rel} not found", severity="INFO")
        return {"available": False}
    month = run.today.strftime("%Y-%m")
    since = (run.today - timedelta(days=30)).isoformat()
    try:
        conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=5)
        try:
            row = conn.execute("SELECT calls_used FROM fmp_budget_monthly WHERE month=?",
                               (month,)).fetchone()
            big = conn.execute("SELECT day, calls_used FROM fmp_budget WHERE day >= ? "
                               "ORDER BY calls_used DESC, day DESC LIMIT 1", (since,)).fetchone()
            prev = conn.execute("SELECT month, calls_used FROM fmp_budget_monthly "
                                "WHERE month < ? ORDER BY month DESC LIMIT 3",
                                (month,)).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        run.warn("missing_artifact", f"FMP budget counters unreadable ({exc})", severity="INFO")
        return {"available": False}
    return {"available": True, "month": month,
            "month_to_date_calls": row[0] if row else 0,
            "largest_day_last_30d": {"day": big[0], "calls": big[1]} if big else None,
            "previous_months": [{"month": m, "calls": n} for m, n in prev]}


def _llm_usage(run: _Run) -> Dict[str, Any]:
    rel = (run.registry.get("cost_sources") or {}).get("llm_usage_log", "logs/llm_usage.jsonl")
    text = _read_text(run.path(rel))
    if text is None:
        return {"available": False}
    month = run.now.strftime("%Y-%m")
    calls = tokens = 0
    for line in text.splitlines():
        try:
            r = json.loads(line)
        except Exception:
            continue
        if str(r.get("ts", "")).startswith(month) and r.get("status") in ("ok", "error"):
            calls += 1
            tokens += int(r.get("prompt_tokens") or 0) + int(r.get("completion_tokens") or 0)
    return {"available": True, "month": month, "month_to_date_calls": calls,
            "month_to_date_tokens": tokens}


def _big_spend_modules(run: _Run) -> List[Dict[str, Any]]:
    out = []
    for d in (run.registry.get("cost_sources") or {}).get("scan_dirs") or ["research", "scripts"]:
        base = run.path(d)
        if not base.is_dir():
            continue
        for py in sorted(base.rglob("*.py")):
            parts = set(py.parts)
            if {"archived", "__pycache__", "tests"} & parts or py.name == "research_governor.py":
                continue
            text = _read_text(py) or ""
            for name, value in _CALL_CAP_RE.findall(text):
                n = int(value.replace("_", ""))
                if n > 100:
                    out.append({
                        "module": str(py.relative_to(run.root)) if run.root in py.parents else str(py),
                        "constant": name, "calls": n,
                        # require_execute_fetch counts: since 2026-09-11 the
                        # shared replay helper carries the planned-call gate,
                        # so a stage calling it is size-gated too.
                        "planned_call_gate": any(
                            marker in text for marker in
                            ("assert_planned_calls_authorised",
                             "assert_within_hard_cap",
                             "require_execute_fetch"))})
    return out


def check_cost(run: _Run) -> None:
    facts = _static_budget_facts(run)
    timers = run.schedule.get("timers") or []
    auto = []
    for t in timers:
        if not t["enabled"]:
            continue
        external = [e for e in t["exec"] if not _RUNNER_ARG_RE.search(e)]
        auto.append({"timer": t["timer"], "schedule": t["schedule"],
                     "entry": t["runner_arg"] or (external[0] if external else None),
                     "provider_steps": t["provider_steps"],
                     "external_script": bool(external),
                     "can_spend_automatically": bool(t["provider_steps"]) or bool(external)})
    components = []
    for c in run.comps:
        risk = c.get("provider_call_risk")
        if risk in (None, "NONE"):
            continue
        cid = c["component_id"]
        components.append({"component_id": cid, "name": c.get("name"),
                           "provider_call_risk": risk, "hard_guard": c.get("hard_guard"),
                           "calls_per_run_max": c.get("provider_calls_per_run_max"),
                           "scheduled_by": run.scheduled.get(cid) or []})
        if risk == "HIGH" and c.get("hard_guard") is not True:
            run.warn("uncontrolled_provider_risk",
                     f"{c.get('name')}: HIGH provider-call risk with no hard guard",
                     component_id=cid, severity="HIGH")
            run.find(cid, "FIX_NEXT", "HIGH provider-call risk with no hard guard",
                     "add a per-run hard cap and a planned-call gate before the next run",
                     "uncontrolled_provider_risk")
    if not facts["budget_refusal_guards_exist"]:
        run.warn("uncontrolled_provider_risk",
                 "no provider budget refusal guard found in core/", severity="HIGH")
        run.system_item("system:provider_budget", "Provider budget guard", "FIX_NEXT",
                        "no refusal guard found in core/provider_budget.py + data_gatekeeper",
                        "restore the monthly/daily refusal gate", risk="HIGH")
    big = _big_spend_modules(run)
    large = int(run.limits.get("large_run_calls", 2000))

    def owning_component(module: str) -> Optional[str]:
        best, best_len = None, -1
        for c in run.comps:
            for o in _as_list(c.get("owner_file_or_artifact")):
                if (module == o or (o.endswith("/") and module.startswith(o))) \
                        and len(o) > best_len:
                    best, best_len = c["component_id"], len(o)
        return best

    for m in big:
        if m["planned_call_gate"] or m["calls"] <= 1000:
            continue
        msg = (f"{m['module']} can spend up to {m['calls']} calls per run "
               f"({m['constant']}) with no planned-call gate")
        if m["calls"] <= large:
            run.warn("large_run_without_planned_gate", msg, severity="INFO")
            continue
        owner = owning_component(m["module"])
        run.warn("large_run_without_planned_gate", msg, component_id=owner, severity="HIGH")
        step = "add a planned-call gate (assert_planned_calls_authorised) before the next run"
        if owner:
            run.find(owner, "FIX_NEXT", msg, step, "large_run_without_planned_gate")
        else:
            run.system_item(f"system:large_run:{_stem(m['module'])}",
                            f"Ungated large run {m['module']}", "FIX_NEXT", msg, step,
                            risk="HIGH")
    run.cost = {
        "fmp": _fmp_usage(run), "llm": _llm_usage(run), "budget_controls": facts,
        "commands_over_100_calls": big, "scheduled_jobs": auto,
        "any_scheduled_job_spends_automatically": any(a["can_spend_automatically"] for a in auto),
        "cron_note": run.schedule.get("cron_note"),
        "component_risks": components,
    }


def build_proposals(run: _Run) -> None:
    for p in run.registry.get("build_proposals") or []:
        pid = str(p.get("proposal_id") or "?")
        approved = p.get("decision") == "APPROVED"
        run.system_item(
            f"proposal:{pid}", p.get("name") or pid, "KEEP" if approved else "DO_NOT_BUILD",
            p.get("reason") or "", ("build as approved in " + str(p.get("approval_ref")))
            if approved else "do not build; revisit only with a registry decision",
            evidence="NOT_ENOUGH_EVIDENCE")
    # Build suggestions arriving through the audit queue get the same answer.
    build_re = re.compile(r"\b(build|create|add|new)\b[^.]{0,40}\b(radar|lens|overlay|"
                          r"detector|ranker|ranking model|shortlist|panel)\b", re.IGNORECASE)
    try:
        from research import feedback_queue as fq
        tasks = fq.build_review(run.root).get("tasks") or []
    except Exception:
        tasks = []
    for t in tasks:
        if t.get("status") in ("OPEN", "IN_PROGRESS") and build_re.search(t.get("task") or ""):
            run.system_item(f"queue:{t['task_id']}", "Queued build suggestion",
                            "DO_NOT_BUILD",
                            "an audit queue task proposes a new radar/lens/overlay/ranker",
                            "dismiss it, or add an APPROVED build_proposal first",
                            evidence="NOT_ENOUGH_EVIDENCE")


# ── decisions, verdict, answer ───────────────────────────────────────────────


def build_decisions(run: _Run) -> List[Dict[str, Any]]:
    for e in run.registry_errors:
        cid = e.get("component_id")
        if cid in run.by_id:
            run.find(cid, "FIX_NEXT", f"registry rule broken: {e['message']}",
                     "fix the registry entry (human edit)", "registry_error")
    items: List[Dict[str, Any]] = []
    for c in run.comps:
        cid = c["component_id"]
        found = list(run.findings.get(cid, []))
        if c.get("evidence_level") in NO_SURFACE_EVIDENCE and c.get("daily_decision_impact"):
            found.append({"action": "STOP_NOW", "category": "contradicted_in_daily_use",
                          "reason": f"{c.get('evidence_level')} yet marked as influencing "
                                    "daily decisions",
                          "next_step": "remove it from the daily path until new evidence exists"})
        if c.get("evidence_level") == "FORWARD_IMMATURE":
            mat = _parse_date(c.get("maturity_date"))
            if mat and mat <= run.today:
                found.append({"action": "NEEDS_HUMAN_APPROVAL", "category": "matured",
                              "reason": f"maturity date {mat} reached",
                              "next_step": "read the matured verdict and record the decision"})
            else:
                until = f" until {mat}" if mat else " (gate set by its own verdict ladder)"
                found.append({"action": "WAIT_FOR_MATURITY", "category": "immature",
                              "reason": "forward evidence immature" + until,
                              "next_step": "do not re-run, re-date or re-read early"})
        if not found:
            keep = ("preserved as history; not shown and not scheduled"
                    if c.get("status") in BLOCKED_STATUSES
                    else "registered, current, no drift finding")
            found.append({"action": "KEEP", "category": "keep", "reason": keep,
                          "next_step": "no change"})
        action = next(a for a in ACTION_PRECEDENCE if any(f["action"] == a for f in found))
        primary = [f for f in found if f["action"] == action]
        reasons = list(dict.fromkeys(f["reason"] for f in primary))
        others = list(dict.fromkeys(f"{f['action']}: {f['reason']}"
                                    for f in found if f["action"] != action))
        items.append(_item(cid, c.get("name") or cid, action, "; ".join(reasons),
                           c.get("evidence_level"), c.get("provider_call_risk"),
                           (run.freshness.get(cid) or {}).get("state", "NOT_CHECKED"),
                           primary[0]["next_step"], others))
    items.extend(run.system_items)
    rank = {a: i for i, a in enumerate(ACTION_PRECEDENCE)}
    items.sort(key=lambda i: (rank[i["action"]], i["component_id"]))
    return items


def decide_verdict(run: _Run, decisions: List[Dict[str, Any]]) -> Tuple[str, List[str]]:
    counts = _action_counts(decisions)
    cats = {w["category"] for w in run.warnings}
    too_many = len(run.active_surfaces) > int(run.limits["max_active_candidate_surfaces"])
    validated = any(c.get("evidence_level") == "VALIDATED_EDGE" for c in run.comps)
    reasons: List[str] = []
    if run.registry_errors:
        return "BLOCKED", [f"{len(run.registry_errors)} registry rule(s) broken — the "
                           "governor cannot certify anything until the registry is fixed"]
    drifting = []
    if counts.get("STOP_NOW"):
        drifting.append(f"{counts['STOP_NOW']} STOP_NOW item(s)")
    if too_many:
        drifting.append(f"{len(run.active_surfaces)} active candidate surfaces "
                        f"(limit {run.limits['max_active_candidate_surfaces']})")
    if len(run.warnings) >= int(run.limits["drifting_warning_threshold"]):
        drifting.append(f"{len(run.warnings)} drift warnings")
    if drifting:
        return "DRIFTING", drifting
    caution = []
    for cat, text in (("untriaged_queue", "untriaged audit queue"),
                      ("stale_live_artifact", "stale surfaces still shown as current"),
                      ("uncontrolled_provider_risk", "uncontrolled provider-call risk"),
                      ("decision_date_passed", "decision dates passed without a decision")):
        if cat in cats:
            caution.append(text)
    if counts.get("FIX_NEXT"):
        caution.append(f"{counts['FIX_NEXT']} FIX_NEXT item(s)")
    if caution:
        return "CAUTION", caution + ([] if validated else ["no validated edge"])
    if not validated:
        return "RESEARCH_ONLY_NO_EDGE", ["no drift finding, and no component has a validated edge"]
    return "HEALTHY", reasons or ["no drift finding; a registry-approved validated edge exists"]


def _action_counts(decisions: List[Dict[str, Any]]) -> Dict[str, int]:
    return {a: sum(1 for d in decisions if d["action"] == a) for a in ACTIONS}


def apparatus_answer(run: _Run, verdict: str) -> Dict[str, Any]:
    validated = [c["component_id"] for c in run.comps
                 if c.get("evidence_level") == "VALIDATED_EDGE"]
    measuring = [c for c in run.comps if not c.get("daily_decision_impact")
                 and c.get("status") not in BLOCKED_STATUSES]
    upcoming = sorted(
        (d, c.get("name"), f) for c in run.comps for f in ("maturity_date", "next_decision_date")
        for d in [_parse_date(c.get(f))] if d and d >= run.today)
    if validated:
        answer = "CLOSER_TO_ALPHA"
    elif verdict == "DRIFTING" or len(run.active_surfaces) > int(
            run.limits["max_active_candidate_surfaces"]):
        answer = "BUILDING_APPARATUS"
    else:
        answer = "WAITING_ON_EVIDENCE"
    return {
        "answer": answer,
        "validated_edges": len(validated),
        "registered_components": len(run.comps),
        "active_candidate_surfaces": len(run.active_surfaces),
        "components_not_feeding_daily_decisions": len(measuring),
        "next_evidence_dates": [{"date": d.isoformat(), "component": n, "field": f}
                                for d, n, f in upcoming[:5]],
    }


def daily_workflow(run: _Run) -> List[str]:
    limit = int(run.limits["daily_review_list_max"])
    within, over, total = [], [], 0
    for cid in run.active_surfaces:
        for entry in run.lists.get(cid) or []:
            if entry["daily_review_list"] and entry["count"] is not None:
                target = within if entry["count"] <= limit else over
                target.append(f"{run.by_id[cid].get('name')} — {entry['label']} "
                              f"({entry['count']} names)")
                if entry["count"] <= limit:
                    total += entry["count"]
    blocked = [c.get("name") for c in run.comps if c.get("status") in BLOCKED_STATUSES]
    context = [c.get("name") for c in run.comps
               if c.get("kind") == "surface" and c.get("status") not in BLOCKED_STATUSES
               and not c.get("allowed_to_surface_candidates")]
    steps = ["Confirm the session is aligned: ./scripts/run_research_cycle.sh freshness-audit. "
             "If it is not, the data is today's whole finding."]
    if within:
        together = (f" Together they list {total} names, so stop at {limit}."
                    if total > limit else "")
        steps.append(f"Review at most {limit} names in total, in this order: "
                     + "; ".join(within) + "." + together)
    for o in over:
        steps.append(f"Treat {o} as weekly depth, not daily — it exceeds the {limit}-name limit.")
    steps.append("Skim the Nightly Operator Summary for data-quality warnings only.")
    if context:
        steps.append("Use only as context for names already on the list, never as a source: "
                     + ", ".join(context) + ".")
    if blocked:
        steps.append("Ignore as current truth (quarantined / decommissioned): "
                     + ", ".join(blocked) + ".")
    steps.append("Weekly: feedback-queue-review, then this governor; act only on STOP_NOW "
                 "and FIX_NEXT.")
    return steps


# ── build + render ───────────────────────────────────────────────────────────


CHECKS: Tuple[Tuple[str, Callable[[_Run], None]], ...] = (
    ("schedule", check_schedule),
    ("freshness", check_freshness),
    ("candidate_surfaces", check_candidate_surfaces),
    ("unregistered_surfaces", check_unregistered_surfaces),
    ("readers", check_readers),
    ("labels", check_labels),
    ("line_counts", check_line_counts),
    ("generated_docs", check_generated_docs),
    ("test_debt", check_test_debt),
    ("queue", check_queue),
    ("dates", check_dates),
    ("purpose", check_purpose),
    ("scheduled_components", check_scheduled_components),
    ("precision", check_precision),
    ("evidence", check_evidence),
    ("cost", check_cost),
    ("proposals", build_proposals),
)


def build_report(root: Optional[Path] = None, registry_path: Optional[str] = None,
                 now: Optional[datetime] = None) -> Dict[str, Any]:
    """Run every check and return the report model.  Pure read — writes nothing."""
    root = Path(root) if root else REPO_ROOT
    now = now or datetime.now(timezone.utc)
    reg_rel = registry_path or DEFAULT_REGISTRY_REL
    reg_file = Path(os.path.expanduser(reg_rel))
    reg_file = reg_file if reg_file.is_absolute() else root / reg_file
    registry, load_error = _load_json(reg_file)
    run = _Run(root, registry if isinstance(registry, dict) else {}, now)
    if load_error:
        run.registry_errors = [{"component_id": None, "rule": "registry_unreadable",
                                "message": f"{reg_rel} is {load_error}"}]
        run.warn("registry_unreadable", f"{reg_rel} is {load_error}", severity="HIGH")
    else:
        run.registry_errors = validate_registry(registry)
    for name, fn in CHECKS:
        try:
            fn(run)
        except Exception as exc:  # a broken input never crashes the governor
            run.warn("check_error", f"check '{name}' failed: {type(exc).__name__}: {exc}")
    decisions = build_decisions(run)
    verdict, reasons = decide_verdict(run, decisions)
    components = [{
        "component_id": c["component_id"], "name": c.get("name"),
        "status": c.get("status"), "evidence_level": c.get("evidence_level"),
        "research_label": c.get("research_label"), "kind": c.get("kind"),
        "cadence": c.get("cadence"),
        "daily_decision_impact": c.get("daily_decision_impact"),
        "provider_call_risk": c.get("provider_call_risk"),
        "allowed_to_surface_candidates": c.get("allowed_to_surface_candidates"),
        "allowed_in_daily_digest": c.get("allowed_in_daily_digest"),
        "allowed_in_dashboard": c.get("allowed_in_dashboard"),
        "next_decision_date": c.get("next_decision_date"),
        "maturity_date": c.get("maturity_date"),
        "expiration_date": c.get("expiration_date"),
        "freshness": run.freshness.get(c["component_id"]),
        "candidate_lists": run.lists.get(c["component_id"]) or [],
        "scheduled_by": run.scheduled.get(c["component_id"]) or [],
        "action": next((d["action"] for d in decisions
                        if d["component_id"] == c["component_id"]), None),
    } for c in run.comps]
    return {
        "kind": "research_governor", "version": VERSION,
        "generated_at": now.astimezone(timezone.utc).isoformat(),
        "session_date": run.today.isoformat(),
        "verdict": verdict, "verdict_reasons": reasons,
        "registry_path": reg_rel,
        "registry_component_count": len(run.comps),
        "registry_errors": run.registry_errors,
        "drift_warning_count": len(run.warnings),
        "drift_warnings": run.warnings,
        "info_notes": run.notes,
        "decisions_due_count": len(decisions),
        "action_counts": _action_counts(decisions),
        "decisions_due": decisions,
        "components": components,
        "evidence": run.evidence,
        "cost_risks": run.cost,
        "queue": run.queue,
        "active_candidate_surfaces": run.active_surfaces,
        "daily_workflow": daily_workflow(run),
        "apparatus_answer": apparatus_answer(run, verdict),
        "provider_calls_made": 0,
        "llm_calls_made": 0,
        "research_only": True,
        "promote_to_signal": False,
        "may_change_statuses": False,
    }


def render_markdown(report: Dict[str, Any]) -> str:
    L: List[str] = []
    add = L.append
    counts = report["action_counts"]
    add("# Research Governor Report")
    add("")
    add(f"*Generated {report['generated_at']} for session {report['session_date']}. "
        f"{VERSION}. Deterministic; {report['provider_calls_made']} provider calls, "
        f"{report['llm_calls_made']} LLM calls. Registry: `{report['registry_path']}`.*")
    add("")
    add("> **Audit only.** Recommends; never changes a status, the registry, a scanner, "
        "a routing rule, a dashboard or a timer. No stock is named, ordered or recommended.")
    add("")
    add("## 1. Executive verdict")
    add("")
    add(f"**{report['verdict']}** — " + "; ".join(report["verdict_reasons"]) + ".")
    add("")
    add(f"- Components registered: {report['registry_component_count']}; registry errors: "
        f"{len(report['registry_errors'])}; drift warnings: {report['drift_warning_count']}")
    add("- Decisions: " + " · ".join(f"{a} {counts.get(a, 0)}" for a in ACTION_PRECEDENCE))
    for e in report["registry_errors"]:
        add(f"- Registry error `{e.get('component_id')}` ({e['rule']}): {_md(e['message'])}")
    add("")
    add("## 2. Component status table")
    add("")
    add("| Component | Status | Evidence | Daily impact | Provider risk | Surface / Digest / Dashboard | Cadence | Freshness | Action |")
    add("|---|---|---|---|---|---|---|---|---|")
    for c in report["components"]:
        flags = "/".join("Y" if c.get(k) else "N" for k in
                         ("allowed_to_surface_candidates", "allowed_in_daily_digest",
                          "allowed_in_dashboard"))
        ev = c.get("evidence_level") or "—"
        if c.get("research_label"):
            ev += f" ({c['research_label']})"
        add(f"| {_md(c['name'])} | {c.get('status')} | {ev} | "
            f"{'yes' if c.get('daily_decision_impact') else 'no'} | {c.get('provider_call_risk')} | "
            f"{flags} | {c.get('cadence') or '—'} | "
            f"{(c.get('freshness') or {}).get('state', '—')} | {c.get('action')} |")
    add("")
    add("## 3. Evidence table")
    add("")
    add("| Path | Registry level | Artifact verdict | Observed level | Agreement | Artifact age |")
    add("|---|---|---|---|---|---|")
    for e in report["evidence"]:
        age = f"{e['artifact_age_hours']:.0f}h" if e.get("artifact_age_hours") is not None else "—"
        verdicts = ", ".join(e["observed_verdicts"]) or (e.get("note") or "no verdict")
        add(f"| {_md(e['path_label'])} | {e['registry_evidence_level']} | {_md(verdicts)} | "
            f"{e['observed_evidence_level']} | {e['agreement']} | {age} |")
    add("")
    add("No path has a validated forward edge unless a row above reads VALIDATED_EDGE "
        "with AGREES.")
    add("")
    add("## 4. API / cost risk table")
    add("")
    cost = report.get("cost_risks") or {}
    fmp, llm, bc = cost.get("fmp") or {}, cost.get("llm") or {}, cost.get("budget_controls") or {}
    add("| Item | Value |")
    add("|---|---|")
    if fmp.get("available"):
        big = fmp.get("largest_day_last_30d") or {}
        add(f"| FMP calls month-to-date ({fmp.get('month')}) | {fmp.get('month_to_date_calls')} |")
        add(f"| Largest FMP day, last 30 days | {big.get('calls', '—')} ({big.get('day', '—')}) |")
        prev = ", ".join(f"{p['month']} {p['calls']}" for p in fmp.get("previous_months") or [])
        add(f"| Previous months | {prev or '—'} |")
    else:
        add("| FMP counters | unavailable |")
    if llm.get("available"):
        add(f"| LLM calls / tokens month-to-date ({llm.get('month')}) | "
            f"{llm.get('month_to_date_calls')} / {llm.get('month_to_date_tokens')} |")
    add(f"| Budget refusal guards exist | {bc.get('budget_refusal_guards_exist')} |")
    add(f"| Default monthly / daily FMP cap | {bc.get('default_monthly_cap')} / "
        f"{bc.get('default_daily_cap')} |")
    add(f"| Planned-call confirmation threshold | {bc.get('planned_call_confirm_threshold')} |")
    add(f"| FMP_MONTHLY_BUDGET=0 means | {_md(bc.get('fmp_monthly_budget_zero_means'))} |")
    add(f"| LLM daily call / token caps (defaults) | {bc.get('llm_daily_max_calls_default')} / "
        f"{bc.get('llm_daily_max_tokens_default')} |")
    add(f"| Env overrides | {_md(bc.get('env_overrides'))} |")
    add(f"| Any scheduled job spends provider calls automatically | "
        f"{cost.get('any_scheduled_job_spends_automatically')} |")
    add("")
    add("Commands able to spend more than 100 calls per run:")
    add("")
    add("| Module | Constant | Calls | Planned-call gate |")
    add("|---|---|---|---|")
    for m in cost.get("commands_over_100_calls") or []:
        add(f"| `{m['module']}` | {m['constant']} | {m['calls']} | {m['planned_call_gate']} |")
    add("")
    add("Enabled scheduled jobs:")
    add("")
    add("| Timer | Schedule | Entry | Provider steps |")
    add("|---|---|---|---|")
    for j in cost.get("scheduled_jobs") or []:
        steps = ", ".join(j["provider_steps"]) or ("external script" if j["external_script"] else "none")
        add(f"| {j['timer']} | {_md(j['schedule'])} | {_md(j['entry'])} | {_md(steps)} |")
    if cost.get("cron_note"):
        add("")
        add(f"Cron: {_md(cost['cron_note'])}")
    add("")
    add("| Component | Provider risk | Hard guard | Max calls / run | Scheduled by |")
    add("|---|---|---|---|---|")
    for r in cost.get("component_risks") or []:
        add(f"| {_md(r['name'])} | {r['provider_call_risk']} | {r.get('hard_guard')} | "
            f"{r.get('calls_per_run_max') or '—'} | {', '.join(r['scheduled_by']) or '—'} |")
    add("")
    add("## 5. Drift warnings")
    add("")
    if not report["drift_warnings"]:
        add("None.")
    for w in report["drift_warnings"]:
        tag = f" `{w['component_id']}`" if w.get("component_id") else ""
        add(f"- **{w['severity']}** {w['category']}{tag}: {_md(w['message'])}")
    q = report.get("queue") or {}
    if q:
        add("")
        add(f"Audit queue: {q.get('queue_lines')} lines, {q.get('distinct_tasks')} distinct "
            f"tasks, {q.get('pending_human_review')} awaiting a human; last triage "
            f"{q.get('last_state_entry') or 'never'} ({q.get('days_since_triage')} days).")
    add("")
    add("## 6. Decisions due")
    add("")
    add("| Component | Action | Reason | Evidence | Provider risk | Stale risk | Human approval | Suggested next step |")
    add("|---|---|---|---|---|---|---|---|")
    for d in report["decisions_due"]:
        add(f"| {_md(d['component_name'])} (`{d['component_id']}`) | **{d['action']}** | "
            f"{_md(d['reason'])} | {d['evidence_level']} | {d['provider_call_risk']} | "
            f"{d['stale_artifact_risk']} | {'yes' if d['human_approval_required'] else 'no'} | "
            f"{_md(d['suggested_next_step'])} |")
    add("")
    add("## 7. Daily workflow recommendation")
    add("")
    for i, step in enumerate(report["daily_workflow"], 1):
        add(f"{i}. {step}")
    add("")
    add("## 8. STOP / KEEP / FIX / WAIT / DO_NOT_BUILD summary")
    add("")
    for action in ("STOP_NOW", "FIX_NEXT", "NEEDS_HUMAN_APPROVAL", "WAIT_FOR_MATURITY",
                   "DO_NOT_BUILD", "KEEP"):
        names = [d["component_name"] for d in report["decisions_due"] if d["action"] == action]
        add(f"- **{action}** ({len(names)}): " + (", ".join(_md(n) for n in names) or "none"))
    add("")
    add("## 9. Human approvals required")
    add("")
    need = [d for d in report["decisions_due"] if d["human_approval_required"]]
    if not need:
        add("None.")
    for d in need:
        add(f"- [{d['action']}] {_md(d['component_name'])}: {_md(d['suggested_next_step'])}")
    add("")
    add("## 10. Final answer: are we closer to alpha or building apparatus?")
    add("")
    ans = report["apparatus_answer"]
    add(f"**{ans['answer']}.** Validated edges: {ans['validated_edges']}. Registered "
        f"components: {ans['registered_components']}, of which "
        f"{ans['components_not_feeding_daily_decisions']} do not feed a daily decision. "
        f"Active candidate surfaces: {ans['active_candidate_surfaces']}.")
    if ans["next_evidence_dates"]:
        add("")
        add("Next dates that can change this answer: " + "; ".join(
            f"{x['date']} — {_md(x['component'])} ({x['field']})"
            for x in ans["next_evidence_dates"]) + ".")
    add("")
    add("*Research only. Nothing here is evidence of edge or a change to any live rule.*")
    return "\n".join(L) + "\n"


# ── CLI ──────────────────────────────────────────────────────────────────────


def approved_output_path(root: Path, value: str) -> Path:
    """Resolve an output path; refuse anything outside the approved directory."""
    p = Path(os.path.expanduser(value))
    p = (p if p.is_absolute() else root / p).resolve()
    allowed = APPROVED_OUTPUT_DIRS.get(p.suffix)
    if allowed is None:
        raise ValueError(f"refused output {value}: only .md and .json outputs are allowed")
    allowed_dir = (root / allowed).resolve()
    if p.parent != allowed_dir and allowed_dir not in p.parents:
        raise ValueError(f"refused output {value}: {p.suffix} files may only be written "
                         f"under {allowed}/")
    return p


def write_outputs(report: Dict[str, Any], md_path: Path, json_path: Path) -> None:
    text = render_markdown(report)
    hits = language_hits(text)
    if hits:  # registry text is validated, so this should never trigger
        report["language_guard_hits"] = hits
        text = BANNED_LANGUAGE.sub("[term removed]", text)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(text, encoding="utf-8")
    json_path.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Research Governor v0 — deterministic, "
                                "audit-only review (no LLM, no provider calls).")
    p.add_argument("--registry", default=DEFAULT_REGISTRY_REL)
    p.add_argument("--output-md", default=DEFAULT_OUTPUT_MD_REL)
    p.add_argument("--output-json", default=DEFAULT_OUTPUT_JSON_REL)
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--root", default=None, help=argparse.SUPPRESS)
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    root = Path(args.root).resolve() if args.root else REPO_ROOT
    try:
        md_path = approved_output_path(root, args.output_md)
        json_path = approved_output_path(root, args.output_json)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    report = build_report(root=root, registry_path=args.registry)
    write_outputs(report, md_path, json_path)
    if not args.quiet:
        counts = report["action_counts"]
        print(f"RESEARCH GOVERNOR — {report['verdict']} "
              f"({report['registry_component_count']} components, "
              f"{report['drift_warning_count']} drift warnings, "
              f"{report['decisions_due_count']} decisions)")
        print("  " + " · ".join(f"{a} {counts.get(a, 0)}" for a in ACTION_PRECEDENCE))
        print(f"  report: {md_path}")
        print(f"  json:   {json_path}")
        print("  provider calls made: 0 · LLM calls made: 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())

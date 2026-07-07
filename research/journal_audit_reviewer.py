#!/usr/bin/env python3
"""Journal digest audit reviewer — LLM audit layer over the daily digest.

Reads the final Daily Research Digest note and returns a structured audit
verdict: is the research engine working, what flaws or contradictions show
up in its own output, and what should be reviewed or corrected later.

This is an AUDIT layer, not a signal generator and not a ticker picker.

Doctrine:
  - RESEARCH_ONLY.  ``promote_to_signal`` is always false — enforced in
    code after every path, including the LLM path.  No price prediction,
    no trade language.
  - READ-ONLY on the engine.  Never mutates scanner scores, rankings,
    gates, watchlists, artifacts, or execution logic.  The only writes are
    the audit sidecar (cache/research/journal_audit_latest.json), the
    feedback queue (logs/research_engine_feedback_queue.jsonl), and the
    audit-trend history (data/research/journal_audit_history.jsonl).
  - REPAIR TASKS ARE EXPERIMENTS, NOT CHANGES.  ``next_system_actions``
    recommends diagnostics and forward-evidence experiments a human (or a
    coding assistant, under review) can run later.  It never loosens a
    filter, never changes a threshold, and never promotes a ticker —
    a looser configuration may only be *proposed* after its own forward
    evidence proves it works.
  - CRED-FREE.  No dependency on core.config; the Anthropic key is
    optional.  If the LLM call fails, times out, has no API key, or
    returns invalid JSON, a deterministic rule-based fallback audit is
    returned instead — the nightly cycle never blocks on the LLM.
  - GROUNDED.  The audit may only reference what is in the digest text;
    hard invariants (Phase 4B blocked => RESEARCH_ONLY, immature forward
    evidence => never STRONG) are re-enforced on LLM output.

Usage:
  GEM_TRADER_SKIP_DOTENV=true .venv/bin/python \
      research/journal_audit_reviewer.py --dry-run
  GEM_TRADER_SKIP_DOTENV=true .venv/bin/python \
      research/journal_audit_reviewer.py            # write sidecar + queue
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]

AUDIT_SIDECAR_REL = Path("cache") / "research" / "journal_audit_latest.json"
FEEDBACK_QUEUE_REL = Path("logs") / "research_engine_feedback_queue.jsonl"
JOURNAL_JSONL_REL = Path("data") / "research" / "journal.jsonl"
AUDIT_HISTORY_REL = Path("data") / "research" / "journal_audit_history.jsonl"

# Env override, mirroring the social-arb reviewer convention.
DEFAULT_MODEL = "claude-opus-4-8"
MODEL_ENV_VAR = "JOURNAL_AUDIT_ANTHROPIC_MODEL"
LLM_TIMEOUT_SECONDS = 90.0
LLM_MAX_TOKENS = 4000

RESEARCH_VERDICTS = ("RESEARCH_ONLY", "CAUTION", "READY_FOR_HUMAN_REVIEW")
QUALITY_LEVELS = ("WEAK", "WEAK_TO_MIXED", "MIXED", "IMPROVING", "STRONG")
ENGINE_HEALTH = ("OPERATIONAL", "OPERATIONAL_WITH_BLOCKERS", "DEGRADED",
                 "INSUFFICIENT_DATA")
SEVERITIES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
FLAW_AREAS = ("scanner_recall", "data_quality", "forward_evidence",
              "fundamental_overlay", "options_overlay", "regime_filter",
              "ranking_logic", "journal_wording", "other")

# Tracker verdicts that mean the forward evidence is not yet trustworthy.
IMMATURE_FORWARD_VERDICTS = {"MIXED", "INCONCLUSIVE", "NEED_MORE_DATA"}
# Tracker verdicts that mean the forward evidence is actively negative.
NEGATIVE_FORWARD_VERDICTS = {"NO_FORWARD_EDGE", "NO_VALUE", "FAIL"}

SCANNER_RECALL_FLOOR_PCT = 5.0

# next_system_actions schema
PRIORITIES = ("P0", "P1", "P2")
ACTION_AREAS = ("scanner_recall", "forward_evidence", "options_overlay",
                "data_quality", "fundamental_overlay")
MAX_SYSTEM_ACTIONS = 8

# audit_trend
TREND_WINDOW = 5
TREND_DIRECTIONS = ("IMPROVING", "WORSENING", "UNCHANGED", "NEW",
                    "RESOLVED", "INSUFFICIENT_HISTORY")
SEVERITY_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
# Higher rank = healthier forward evidence.  Unknown verdicts fall back to
# severity comparison.
TRACKER_VERDICT_RANK = {
    "NO_FORWARD_EDGE": 0, "NO_VALUE": 0, "FAIL": 0,
    "MIXED": 1, "INCONCLUSIVE": 1, "NEED_MORE_DATA": 1, "TOO_EARLY": 1,
    "PROMISING": 2,
}

_RECALL_RE = re.compile(
    r"recall[^0-9%\n]{0,40}?([0-9]+(?:\.[0-9]+)?)\s*%", re.IGNORECASE)
_RECALL_BASELINE_RE = re.compile(
    r"baseline[:\s]+([0-9]+(?:\.[0-9]+)?)\s*%", re.IGNORECASE)
_RECALL_MAIN_MISS_RE = re.compile(
    r"main miss[:\s]+([A-Z_]+)", re.IGNORECASE)
_TRACKER_VERDICT_RE = re.compile(
    r"tracker verdict:\s*([A-Z_]+)", re.IGNORECASE)
_QUARANTINED_RE = re.compile(r"quarantined:\s*(\d+)", re.IGNORECASE)


# ── digest signal extraction (deterministic, text-only) ──────────────────────


def extract_digest_signals(digest_text: str) -> Dict[str, Any]:
    """Parse the digest note for the facts the hard rules key on.  Only ever
    reads the provided text — never touches artifacts or providers."""
    text = digest_text or ""
    lower = text.lower()

    tracker_verdict = None
    m = _TRACKER_VERDICT_RE.search(text)
    if m:
        tracker_verdict = m.group(1).upper()

    recall_pct = None
    m = _RECALL_RE.search(text)
    if m:
        try:
            recall_pct = float(m.group(1))
        except ValueError:
            recall_pct = None

    recall_baseline_pct = None
    m = _RECALL_BASELINE_RE.search(text)
    if m:
        try:
            recall_baseline_pct = float(m.group(1))
        except ValueError:
            recall_baseline_pct = None

    recall_main_miss = None
    m = _RECALL_MAIN_MISS_RE.search(text)
    if m:
        recall_main_miss = m.group(1).upper()

    quarantine_count = None
    m = _QUARANTINED_RE.search(text)
    if m:
        try:
            quarantine_count = int(m.group(1))
        except ValueError:
            quarantine_count = None

    red_flag_lines = [ln.strip() for ln in text.splitlines()
                      if "RED FLAG" in ln.upper()]
    dilution_red_flag = any(
        "dilution" in ln.lower() or "negative gross margin" in ln.lower()
        for ln in red_flag_lines)

    backfill_warning = any(
        "backfill" in ln.lower()
        for ln in text.splitlines()
        if "warning" in ln.lower() or "warn" in ln.lower())

    return {
        "empty": len(text.strip()) < 40,
        # \b keeps "UNBLOCKED" from matching
        "phase4b_blocked": bool(re.search(r"phase\s*4b[^\n]{0,40}?\bblocked",
                                          lower)),
        "tracker_verdict": tracker_verdict,
        "forward_immature": tracker_verdict in IMMATURE_FORWARD_VERDICTS,
        "forward_negative": tracker_verdict in NEGATIVE_FORWARD_VERDICTS,
        "scanner_recall_pct": recall_pct,
        "recall_baseline_pct": recall_baseline_pct,
        "recall_main_miss": recall_main_miss,
        "options_overlay_disabled": bool(
            re.search(r"options\s+overlay[^\n]{0,60}disabled", lower)),
        "quarantine_count": quarantine_count,
        "missing_artifacts": "missing_artifact" in lower,
        "backfill_warning": backfill_warning,
        "dilution_red_flag": dilution_red_flag,
        "freshness_stale": bool(re.search(r"freshness:\s*stale", lower)),
        "nightly_failed": bool(re.search(r"nightly status\s+(fail|error)",
                                         lower)),
    }


# ── rule-based fallback audit ────────────────────────────────────────────────


def _flaw(severity: str, area: str, issue: str, why: str,
          fix: str) -> Dict[str, str]:
    return {"severity": severity, "area": area, "issue": issue,
            "why_it_matters": why, "suggested_fix": fix}


def build_fallback_audit(digest_text: str,
                         reason: str = "llm_unavailable") -> Dict[str, Any]:
    """Deterministic audit from the digest text alone.  Used whenever the
    LLM path is unavailable or returns something unusable."""
    sig = extract_digest_signals(digest_text)
    flaws: List[Dict[str, str]] = []

    recall = sig["scanner_recall_pct"]
    if recall is not None and recall < SCANNER_RECALL_FLOOR_PCT:
        flaws.append(_flaw(
            "HIGH", "scanner_recall",
            f"Scanner recall {recall:.1f}% is below the "
            f"{SCANNER_RECALL_FLOOR_PCT:.0f}% floor.",
            "The funnel is missing most eventual winners, so the research "
            "board under-represents the opportunity set.",
            "Continue the recall-repair shadow lane work and re-check the "
            "score gates that kill early leaders."))
    if sig["options_overlay_disabled"]:
        flaws.append(_flaw(
            "MEDIUM", "options_overlay",
            "Options overlay is DISABLED in the digest.",
            "Candidates are ranked without options participation or IV "
            "context, weakening evidence quality.",
            "Check the options feed / Tradier token and re-enable the "
            "overlay in the research cycle."))
    if (sig["quarantine_count"] or 0) > 0 or sig["backfill_warning"] \
            or sig["missing_artifacts"]:
        detail = []
        if (sig["quarantine_count"] or 0) > 0:
            detail.append(f"{sig['quarantine_count']} ticker(s) quarantined")
        if sig["backfill_warning"]:
            detail.append("backfill warnings present")
        if sig["missing_artifacts"]:
            detail.append("missing artifacts reported")
        flaws.append(_flaw(
            "MEDIUM", "data_quality",
            "Data-quality issues in the digest: " + "; ".join(detail) + ".",
            "Quarantined or missing data hides candidates and can distort "
            "relative-strength and MA-based fields.",
            "Run the targeted backfill plan and clear the quarantine / "
            "missing-artifact causes."))
    if sig["dilution_red_flag"]:
        flaws.append(_flaw(
            "MEDIUM", "fundamental_overlay",
            "High-priority/review names carry fundamental red flags "
            "(severe dilution and/or negative gross margin).",
            "Names surfaced for review may be structurally low quality; "
            "human review time gets spent on uninvestable candidates.",
            "Surface the red flags earlier in ranking context (display "
            "only) so review order accounts for fundamental quality."))
    if sig["forward_negative"]:
        flaws.append(_flaw(
            "CRITICAL", "forward_evidence",
            f"Tracker verdict is {sig['tracker_verdict']} — matured forward "
            "evidence shows no selection edge.",
            "Without demonstrated forward edge no candidate is validated; "
            "Phase 4B stays blocked and any promotion would be unsupported.",
            "Keep collecting matured 5d/10d/20d samples and review whether "
            "the post-repair cohort needs its own epoch-split verdict; do "
            "not unblock anything automatically."))
    elif sig["forward_immature"]:
        flaws.append(_flaw(
            "LOW", "forward_evidence",
            f"Forward evidence is still "
            f"{sig['tracker_verdict'] or 'immature'}.",
            "No proven selection edge yet — conclusions drawn from the "
            "board would be premature.",
            "Keep accumulating matured 5d/10d/20d samples before changing "
            "any gate or ranking logic."))
    if sig["nightly_failed"] or sig["freshness_stale"]:
        flaws.append(_flaw(
            "HIGH", "data_quality",
            "Nightly run failed or scanner artifacts are stale.",
            "Everything downstream of the scanner reads outdated state.",
            "Re-run the nightly cycle and check the research timer logs."))

    # engine health
    if sig["empty"]:
        health = "INSUFFICIENT_DATA"
    elif sig["nightly_failed"] or sig["freshness_stale"]:
        health = "DEGRADED"
    elif sig["phase4b_blocked"] or flaws:
        health = "OPERATIONAL_WITH_BLOCKERS"
    else:
        health = "OPERATIONAL"

    # alpha discovery quality — the fallback never claims STRONG.
    verdict = sig["tracker_verdict"]
    if sig["empty"] or verdict is None:
        quality = "WEAK_TO_MIXED"
    elif verdict == "PROMISING":
        quality = "IMPROVING"
    elif verdict in IMMATURE_FORWARD_VERDICTS:
        quality = "WEAK_TO_MIXED"
    else:  # NO_VALUE / negative verdicts
        quality = "WEAK"

    # research verdict — conservative ladder.
    if sig["phase4b_blocked"] or sig["empty"]:
        research_verdict = "RESEARCH_ONLY"
    elif any(f["severity"] in ("HIGH", "CRITICAL") for f in flaws):
        research_verdict = "CAUTION"
    elif verdict == "PROMISING":
        research_verdict = "READY_FOR_HUMAN_REVIEW"
    else:
        research_verdict = "CAUTION"

    working: List[str] = []
    if not sig["empty"]:
        working.append("Daily digest was generated from cached artifacts.")
    if not sig["nightly_failed"] and not sig["empty"]:
        working.append("Nightly research cycle completed and produced a "
                       "full seven-section digest.")
    if not sig["freshness_stale"] and not sig["empty"]:
        working.append("Scanner artifacts are fresh.")
    if not sig["missing_artifacts"] and not sig["empty"]:
        working.append("No missing research artifacts were reported.")

    if sig["empty"]:
        summary = ("Digest is empty or unreadable — engine state cannot be "
                   "audited; treat as insufficient data.")
    else:
        summary = (f"Engine {health.replace('_', ' ').lower()}; forward "
                   f"evidence {verdict or 'unknown'}; "
                   f"{len(flaws)} flaw(s) flagged; research-only posture "
                   "holds.")

    tasks = [f["suggested_fix"] for f in flaws]
    if not tasks and not sig["empty"]:
        tasks = ["No corrective task required — keep collecting forward "
                 "evidence."]

    return {
        "research_verdict": research_verdict,
        "alpha_discovery_quality": quality,
        "engine_health": health,
        "promote_to_signal": False,
        "one_line_summary": summary,
        "what_is_working": working,
        "flaws_detected": flaws,
        "recommended_claude_code_tasks": tasks,
        "audit_source": "rule_based_fallback",
        "fallback_reason": reason,
        "model": None,
    }


# ── next_system_actions (deterministic repair-task generator) ────────────────
#
# Actions are generated in code from the digest signals so the schema and the
# safety language are guaranteed on every path (LLM or fallback).  Sanitized
# LLM-proposed actions are merged in afterwards for areas not already covered.
# Every action recommends a DIAGNOSTIC or a forward-evidence EXPERIMENT —
# never a direct change to filters, thresholds, scores, or gates.


def _action(priority: str, area: str, task: str, why: str,
            success_metric: str) -> Dict[str, str]:
    return {"priority": priority, "area": area, "task": task, "why": why,
            "success_metric": success_metric}


def build_next_system_actions(
        signals: Dict[str, Any],
        flaws: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Map detected blockers to structured, research-only repair tasks."""
    actions: List[Dict[str, str]] = []
    flaw_areas = {f.get("area") for f in flaws}

    # P0 — scanner recall diagnostics (never auto-loosen)
    recall = signals.get("scanner_recall_pct")
    recall_low = (recall is not None
                  and recall < SCANNER_RECALL_FLOOR_PCT) \
        or "scanner_recall" in flaw_areas
    if recall_low:
        baseline = signals.get("recall_baseline_pct")
        main_miss = signals.get("recall_main_miss")
        why_bits = []
        if recall is not None:
            why_bits.append(f"scanner recall is {recall:.1f}%")
        if baseline is not None:
            why_bits.append(f"vs a {baseline:.1f}% simple-RS baseline")
        if main_miss:
            why_bits.append(f"(main miss: {main_miss})")
        why = (" ".join(why_bits) if why_bits
               else "scanner recall is far below the simple-RS baseline")
        why = why[0].upper() + why[1:]
        actions.append(_action(
            "P0", "scanner_recall",
            "Build scanner recall diagnostics: report reject counts by "
            "filter, list the top 25 rejected names from the simple-RS "
            "baseline, and compare the strict scanner vs the simple-RS "
            "baseline vs a looser scanner variant, tracking 5d, 10d, and "
            "20d forward performance for all three groups. Do not loosen "
            "any filter automatically — a looser configuration may only be "
            "proposed after its forward evidence proves it works.",
            why + " — the funnel discards most eventual "
            "winners before human review.",
            "Recall-diagnostics sidecar exists with per-filter reject "
            "counts and the top-25 rejected baseline names; strict, "
            "simple-RS, and looser cohorts each accrue matured 5d/10d/20d "
            "forward returns; any filter-change proposal cites that cohort "
            "forward evidence."))

    # P0 — forward-evidence tracking depth
    if signals.get("forward_negative") or signals.get("forward_immature") \
            or signals.get("phase4b_blocked") \
            or "forward_evidence" in flaw_areas:
        verdict = signals.get("tracker_verdict") or "immature"
        actions.append(_action(
            "P0", "forward_evidence",
            "Improve forward-evidence tracking: report hit rate vs SPY, "
            "median forward return, average forward return, and max "
            "drawdown after selection, each at 5d, 10d, and 20d horizons, "
            "with high-priority names separated from watch-only names.",
            f"Tracker verdict is {verdict} — without benchmarked "
            "per-cohort forward statistics the engine cannot demonstrate "
            "(or falsify) a selection edge.",
            "Forward tracker sidecar reports hit-rate vs SPY, "
            "median/average forward return, and post-selection max "
            "drawdown at 5d/10d/20d, split into high-priority vs "
            "watch-only cohorts."))

    # P1 — data quality (backfill / quarantine / missing artifacts)
    if (signals.get("quarantine_count") or 0) > 0 \
            or signals.get("backfill_warning") \
            or signals.get("missing_artifacts") \
            or "data_quality" in flaw_areas:
        detail = []
        if (signals.get("quarantine_count") or 0) > 0:
            detail.append(f"{signals['quarantine_count']} ticker(s) "
                          "quarantined")
        if signals.get("backfill_warning"):
            detail.append("targeted-backfill warnings present")
        if signals.get("missing_artifacts"):
            detail.append("missing artifacts reported")
        actions.append(_action(
            "P1", "data_quality",
            "Run the targeted price-cache backfill plan (dry-run first, "
            "then --execute) and produce a quarantine-cause report giving "
            "each quarantined ticker an explicit reason and clearance "
            "condition.",
            ("Data-quality issues in the digest: " + "; ".join(detail) + "."
             if detail else "Data-quality issues flagged in the digest.")
            + " Thin or quarantined history distorts RS and MA fields.",
            "Backfill run is logged with the tickers that now meet the "
            "bar-depth floor; quarantine report shows a reason and "
            "clearance condition for every quarantined ticker."))

    # P2 — options coverage health
    if signals.get("options_overlay_disabled") \
            or "options_overlay" in flaw_areas:
        actions.append(_action(
            "P2", "options_overlay",
            "Report options coverage health: number of tickers with valid "
            "options data, number skipped, the reason coverage is "
            "insufficient for each, and whether the options overlay is "
            "required or optional for each consuming strategy or report.",
            "The options overlay is DISABLED for insufficient coverage; "
            "without a coverage report it is unclear what data is missing "
            "and which consumers are degraded.",
            "Coverage-health sidecar lists valid/skipped ticker counts, "
            "per-ticker insufficiency reasons, and a required-vs-optional "
            "flag for every overlay consumer."))

    # P2 — fundamental red flags in review ordering (display only)
    if signals.get("dilution_red_flag") \
            or "fundamental_overlay" in flaw_areas:
        actions.append(_action(
            "P2", "fundamental_overlay",
            "Surface fundamental red flags (severe dilution, negative "
            "margins, UNPROFITABLE_FUNDED status) directly in the journal "
            "review-queue context — display only, no score or ranking "
            "changes.",
            "High-priority names carrying fundamental red flags consume "
            "human review time and raise false-positive risk when "
            "technicals alone drive prioritization.",
            "Review-queue rows show fundamental red-flag annotations; no "
            "scanner score or ranking logic changed."))

    return actions


def _sanitize_actions(value: Any) -> List[Dict[str, str]]:
    """Coerce LLM-proposed next_system_actions into the strict schema;
    anything outside the allowed areas/priorities is dropped or defaulted."""
    out: List[Dict[str, str]] = []
    if not isinstance(value, list):
        return out
    for a in value[:MAX_SYSTEM_ACTIONS]:
        if not isinstance(a, dict):
            continue
        area = str(a.get("area") or "").strip().lower()
        if area not in ACTION_AREAS:
            continue
        priority = str(a.get("priority") or "").strip().upper()
        if priority not in PRIORITIES:
            priority = "P2"
        task = str(a.get("task") or "").strip()
        if not task:
            continue
        out.append(_action(
            priority, area, task,
            str(a.get("why") or "").strip(),
            str(a.get("success_metric") or "").strip()))
    return out


def merge_system_actions(
        deterministic: List[Dict[str, str]],
        llm_proposed: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Deterministic actions win; LLM extras only fill uncovered areas."""
    covered = {a["area"] for a in deterministic}
    merged = deterministic + [a for a in llm_proposed
                              if a["area"] not in covered]
    merged.sort(key=lambda a: a["priority"])  # P0 < P1 < P2 lexically
    return merged[:MAX_SYSTEM_ACTIONS]


# ── audit_trend (blocker trajectory vs prior audits) ─────────────────────────


def build_blocker_snapshot(audit: Dict[str, Any],
                           signals: Dict[str, Any]) -> Dict[str, Dict]:
    """Compact per-area blocker state used for trend comparison: worst
    severity per flaw area plus the comparable metric when one exists."""
    snap: Dict[str, Dict[str, Any]] = {}
    for f in audit.get("flaws_detected") or []:
        area = f.get("area") or "other"
        sev = f.get("severity") or "LOW"
        cur = snap.get(area)
        if cur is None or SEVERITY_RANK.get(sev, 0) \
                > SEVERITY_RANK.get(cur["severity"], 0):
            snap[area] = {"severity": sev}
    if "scanner_recall" in snap \
            and signals.get("scanner_recall_pct") is not None:
        snap["scanner_recall"]["recall_pct"] = signals["scanner_recall_pct"]
    if "forward_evidence" in snap and signals.get("tracker_verdict"):
        snap["forward_evidence"]["tracker_verdict"] = \
            signals["tracker_verdict"]
    if "options_overlay" in snap:
        snap["options_overlay"]["disabled"] = \
            bool(signals.get("options_overlay_disabled"))
    if "data_quality" in snap \
            and signals.get("quarantine_count") is not None:
        snap["data_quality"]["quarantine_count"] = \
            signals["quarantine_count"]
    return snap


def _blocker_direction(area: str, latest: Dict[str, Any],
                       prev: Dict[str, Any]) -> str:
    """Metric-based comparison when the area has one; severity otherwise."""
    if area == "scanner_recall":
        a, b = latest.get("recall_pct"), prev.get("recall_pct")
        if a is not None and b is not None:
            if a > b + 0.05:
                return "IMPROVING"
            if a < b - 0.05:
                return "WORSENING"
            return "UNCHANGED"
    if area == "forward_evidence":
        a = TRACKER_VERDICT_RANK.get(latest.get("tracker_verdict") or "")
        b = TRACKER_VERDICT_RANK.get(prev.get("tracker_verdict") or "")
        if a is not None and b is not None:
            if a > b:
                return "IMPROVING"
            if a < b:
                return "WORSENING"
            return "UNCHANGED"
    if area == "data_quality":
        a, b = latest.get("quarantine_count"), prev.get("quarantine_count")
        if a is not None and b is not None:
            if a < b:
                return "IMPROVING"
            if a > b:
                return "WORSENING"
            return "UNCHANGED"
    a = SEVERITY_RANK.get(latest.get("severity") or "", 0)
    b = SEVERITY_RANK.get(prev.get("severity") or "", 0)
    if a < b:
        return "IMPROVING"
    if a > b:
        return "WORSENING"
    return "UNCHANGED"


def compute_audit_trend(snapshot: Dict[str, Dict],
                        history: List[Dict[str, Any]],
                        window: int = TREND_WINDOW) -> Dict[str, Any]:
    """Compare the latest blocker snapshot with up to ``window`` prior
    audits.  Direction is measured against the most recent prior audit;
    the window provides context (how many priors the area appeared in)."""
    priors = [h for h in history
              if isinstance(h.get("blockers"), dict)][-window:]
    prev = priors[-1]["blockers"] if priors else None

    areas = list(snapshot.keys())
    if prev:
        areas += [a for a in prev if a not in snapshot]

    blockers: List[Dict[str, Any]] = []
    for area in areas:
        latest = snapshot.get(area)
        prior = (prev or {}).get(area)
        if latest is None:  # present before, gone now
            blockers.append({
                "area": area, "severity": None, "direction": "RESOLVED",
                "latest": None, "previous": prior,
                "seen_in_prior_audits": sum(
                    1 for h in priors if area in h["blockers"]),
            })
            continue
        if not priors:
            direction = "INSUFFICIENT_HISTORY"
        elif prior is None:
            direction = "NEW"
        else:
            direction = _blocker_direction(area, latest, prior)
        blockers.append({
            "area": area, "severity": latest.get("severity"),
            "direction": direction, "latest": latest, "previous": prior,
            "seen_in_prior_audits": sum(
                1 for h in priors if area in h["blockers"]),
        })
    blockers.sort(
        key=lambda b: -SEVERITY_RANK.get(b.get("severity") or "", -1))
    return {
        "n_prior_audits": len(priors),
        "window": window,
        "prior_generated_at": [h.get("generated_at") for h in priors],
        "blockers": blockers,
    }


def load_audit_history(root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Prior audit-history records, oldest first (read-only, tolerant)."""
    root = Path(root) if root else REPO_ROOT
    path = root / AUDIT_HISTORY_REL
    records: List[Dict[str, Any]] = []
    if not path.exists():
        return records
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except Exception:
                continue
            if isinstance(entry, dict):
                records.append(entry)
    except Exception:
        return records
    return records


# ── LLM path ─────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "You are a research-engine auditor for a RESEARCH-ONLY stock "
    "intelligence system.  You audit the engine's own daily digest for "
    "internal consistency, evidence quality, system blockers, "
    "false-positive risk, and improvement areas.\n"
    "Hard rules you must never break:\n"
    "  - Never promote a ticker to a trade signal; promote_to_signal is "
    "always false.\n"
    "  - Never predict price direction.\n"
    "  - Never invent data that is not in the digest.\n"
    "  - Never propose changing scanner scores, rankings, gates, "
    "artifacts, watchlists, or execution logic yourself — only describe "
    "what a human should review or correct later.\n"
    "Output must be a single strict JSON object, no markdown fences, no "
    "prose outside the JSON.")

_USER_PROMPT_TEMPLATE = """Audit the daily research digest below and return \
exactly this JSON schema:

{{
  "research_verdict": "RESEARCH_ONLY" | "CAUTION" | "READY_FOR_HUMAN_REVIEW",
  "alpha_discovery_quality": "WEAK" | "WEAK_TO_MIXED" | "MIXED" | "IMPROVING" | "STRONG",
  "engine_health": "OPERATIONAL" | "OPERATIONAL_WITH_BLOCKERS" | "DEGRADED" | "INSUFFICIENT_DATA",
  "promote_to_signal": false,
  "one_line_summary": "...",
  "what_is_working": ["..."],
  "flaws_detected": [
    {{
      "severity": "LOW" | "MEDIUM" | "HIGH" | "CRITICAL",
      "area": "scanner_recall" | "data_quality" | "forward_evidence" | "fundamental_overlay" | "options_overlay" | "regime_filter" | "ranking_logic" | "journal_wording" | "other",
      "issue": "...",
      "why_it_matters": "...",
      "suggested_fix": "..."
    }}
  ],
  "recommended_claude_code_tasks": ["..."],
  "next_system_actions": [
    {{
      "priority": "P0" | "P1" | "P2",
      "area": "scanner_recall" | "forward_evidence" | "options_overlay" | "data_quality" | "fundamental_overlay",
      "task": "...",
      "why": "...",
      "success_metric": "..."
    }}
  ]
}}

Grading rules:
- If Phase 4B is BLOCKED, research_verdict must be "RESEARCH_ONLY".
- If forward evidence is MIXED, INCONCLUSIVE, or NEED_MORE_DATA,
  alpha_discovery_quality must not be "STRONG".
- Base every statement strictly on the digest text; if something is not in
  the digest, do not claim it.
- flaws_detected should cover contradictions, weak evidence, blockers, and
  false-positive risk you can point to in the text.
- recommended_claude_code_tasks are short, concrete engineering follow-ups
  a coding assistant could execute later (audits, report fixes, data
  repairs) — never trades.
- next_system_actions are structured repair tasks: diagnostics and
  forward-evidence experiments only.  Never propose loosening a filter,
  changing a threshold, or promoting a ticker — a looser configuration may
  only be proposed as an experiment whose forward evidence must prove it
  works first.  Each action needs a measurable success_metric.

Digest:
---
{digest}
---"""


def _extract_json_object(text: str) -> Dict[str, Any]:
    stripped = (text or "").strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    try:
        obj = json.loads(stripped)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    # last resort: first balanced {...} span
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        obj = json.loads(stripped[start:end + 1])
        if isinstance(obj, dict):
            return obj
    raise ValueError("response did not contain a JSON object")


def _resolve_api_key() -> str:
    """ANTHROPIC_API_KEY with the repo's canonical credential file
    (SNIPER_ENV_PATH) taking precedence over an inherited shell value —
    a stale ``export ANTHROPIC_API_KEY=...`` in the invoking shell must
    not shadow a rotated key in trading.env.  GEM_TRADER_SKIP_DOTENV
    disables the file read (tests / cred-free tooling)."""
    if os.getenv("GEM_TRADER_SKIP_DOTENV", "").lower() not in ("1", "true", "yes"):
        env_path = os.getenv("SNIPER_ENV_PATH", "").strip()
        if env_path:
            try:
                from dotenv import dotenv_values
                v = (dotenv_values(env_path).get("ANTHROPIC_API_KEY")
                     or "").strip()
                if v:
                    return v
            except Exception:
                pass
    return os.getenv("ANTHROPIC_API_KEY", "").strip()


def _llm_audit(digest_text: str) -> Dict[str, Any]:
    """Single Anthropic messages call.  Raises on any problem — the caller
    converts every failure into the deterministic fallback."""
    api_key = _resolve_api_key()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    import anthropic  # local import — cred-free module load

    client = anthropic.Anthropic(
        api_key=api_key, timeout=LLM_TIMEOUT_SECONDS, max_retries=1)
    model = os.getenv(MODEL_ENV_VAR, DEFAULT_MODEL)
    msg = client.messages.create(
        model=model,
        max_tokens=LLM_MAX_TOKENS,
        system=_SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": _USER_PROMPT_TEMPLATE.format(digest=digest_text),
        }],
    )
    if getattr(msg, "stop_reason", None) == "refusal":
        raise RuntimeError("model refused the request")
    text = "".join(getattr(b, "text", "") or "" for b in msg.content)
    audit = _extract_json_object(text)
    audit["audit_source"] = "llm"
    audit["fallback_reason"] = None
    audit["model"] = model
    return audit


# ── schema sanitation + hard invariants ──────────────────────────────────────


def _coerce_enum(value: Any, allowed: tuple, default: str) -> str:
    v = str(value or "").strip().upper()
    return v if v in allowed else default


def _coerce_str_list(value: Any, cap: int = 12) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v or "").strip()][:cap]


def _sanitize_flaws(value: Any) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    if not isinstance(value, list):
        return out
    for f in value[:12]:
        if not isinstance(f, dict):
            continue
        area = str(f.get("area") or "").strip().lower()
        out.append({
            "severity": _coerce_enum(f.get("severity"), SEVERITIES, "LOW"),
            "area": area if area in FLAW_AREAS else "other",
            "issue": str(f.get("issue") or "").strip(),
            "why_it_matters": str(f.get("why_it_matters") or "").strip(),
            "suggested_fix": str(f.get("suggested_fix") or "").strip(),
        })
    return out


def sanitize_audit(audit: Dict[str, Any],
                   signals: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce any audit dict (LLM or fallback) into the exact schema and
    re-enforce the non-negotiable invariants."""
    clean = {
        "research_verdict": _coerce_enum(
            audit.get("research_verdict"), RESEARCH_VERDICTS,
            "RESEARCH_ONLY"),
        "alpha_discovery_quality": _coerce_enum(
            audit.get("alpha_discovery_quality"), QUALITY_LEVELS,
            "WEAK_TO_MIXED"),
        "engine_health": _coerce_enum(
            audit.get("engine_health"), ENGINE_HEALTH, "INSUFFICIENT_DATA"),
        "promote_to_signal": False,  # ALWAYS false — no exceptions
        "one_line_summary": str(audit.get("one_line_summary") or "").strip()
        or "Audit produced no summary.",
        "what_is_working": _coerce_str_list(audit.get("what_is_working")),
        "flaws_detected": _sanitize_flaws(audit.get("flaws_detected")),
        "recommended_claude_code_tasks": _coerce_str_list(
            audit.get("recommended_claude_code_tasks")),
        "next_system_actions": _sanitize_actions(
            audit.get("next_system_actions")),
        "audit_source": audit.get("audit_source") or "rule_based_fallback",
        "fallback_reason": audit.get("fallback_reason"),
        "model": audit.get("model"),
    }
    # Hard invariants, independent of who produced the audit:
    if signals.get("phase4b_blocked"):
        clean["research_verdict"] = "RESEARCH_ONLY"
    if signals.get("forward_immature") \
            and clean["alpha_discovery_quality"] == "STRONG":
        clean["alpha_discovery_quality"] = "MIXED"
    # Repair tasks: deterministic actions from the digest signals are the
    # contract; sanitized LLM proposals only fill areas not already covered.
    clean["next_system_actions"] = merge_system_actions(
        build_next_system_actions(signals, clean["flaws_detected"]),
        clean["next_system_actions"])
    return clean


# ── public API ───────────────────────────────────────────────────────────────


def audit_daily_digest(digest_text: str, *,
                       use_llm: Optional[bool] = None) -> Dict[str, Any]:
    """Audit one digest note and return the structured verdict dict.

    ``use_llm``: None = try the LLM when a key is available, fall back on
    any failure; False = deterministic rule-based audit only.
    """
    digest_text = digest_text or ""
    signals = extract_digest_signals(digest_text)

    audit: Optional[Dict[str, Any]] = None
    if use_llm is not False:
        try:
            audit = _llm_audit(digest_text)
        except Exception as exc:
            audit = build_fallback_audit(
                digest_text,
                reason=f"{type(exc).__name__}: {exc}"[:200])
    else:
        audit = build_fallback_audit(digest_text, reason="llm_disabled")

    clean = sanitize_audit(audit, signals)
    clean["generated_at"] = datetime.now(timezone.utc).isoformat()
    clean["digest_sha256"] = hashlib.sha256(
        digest_text.encode("utf-8")).hexdigest()
    clean["research_only"] = True
    return clean


def _jsonl_has_digest(path: Path, digest_sha: str,
                      audit_source: str) -> bool:
    """True when this digest was already appended by the same audit source.
    An LLM re-audit of a digest a fallback already covered still appends —
    its output is strictly richer; identical re-runs stay deduped."""
    if not path.exists():
        return False
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except Exception:
                continue
            if entry.get("digest_sha256") == digest_sha \
                    and entry.get("audit_source") == audit_source:
                return True
    except Exception:
        return False
    return False


def write_audit_outputs(audit: Dict[str, Any],
                        root: Optional[Path] = None,
                        blocker_snapshot: Optional[Dict[str, Dict]] = None,
                        ) -> Dict[str, Path]:
    """Persist the audit: full JSON sidecar, repair tasks + recommended
    tasks appended to the feedback queue, and a compact trend record
    appended to the audit history (both deduped per digest hash).  These
    are the module's only writes."""
    root = Path(root) if root else REPO_ROOT
    sidecar = root / AUDIT_SIDECAR_REL
    queue = root / FEEDBACK_QUEUE_REL
    history = root / AUDIT_HISTORY_REL

    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")

    digest_sha = audit.get("digest_sha256") or ""
    audit_source = audit.get("audit_source") or "rule_based_fallback"
    common = {
        "queued_at": audit.get("generated_at"),
        "source": "journal_audit_reviewer",
        "audit_source": audit_source,
        "digest_sha256": digest_sha,
        "engine_health": audit.get("engine_health"),
        "research_verdict": audit.get("research_verdict"),
    }
    actions = audit.get("next_system_actions") or []
    tasks = audit.get("recommended_claude_code_tasks") or []
    if (actions or tasks) \
            and not _jsonl_has_digest(queue, digest_sha, audit_source):
        queue.parent.mkdir(parents=True, exist_ok=True)
        with queue.open("a", encoding="utf-8") as fh:
            for action in actions:
                fh.write(json.dumps({
                    **common,
                    "kind": "system_repair_task",
                    "priority": action["priority"],
                    "area": action["area"],
                    "task": action["task"],
                    "why": action["why"],
                    "success_metric": action["success_metric"],
                }, ensure_ascii=False) + "\n")
            for task in tasks:
                fh.write(json.dumps({
                    **common,
                    "kind": "recommended_task",
                    "task": task,
                }, ensure_ascii=False) + "\n")

    if blocker_snapshot is not None \
            and not _jsonl_has_digest(history, digest_sha, audit_source):
        history.parent.mkdir(parents=True, exist_ok=True)
        with history.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "generated_at": audit.get("generated_at"),
                "digest_sha256": digest_sha,
                "audit_source": audit_source,
                "engine_health": audit.get("engine_health"),
                "alpha_discovery_quality": audit.get(
                    "alpha_discovery_quality"),
                "research_verdict": audit.get("research_verdict"),
                "blockers": blocker_snapshot,
            }, ensure_ascii=False) + "\n")
    return {"sidecar": sidecar, "queue": queue, "history": history}


def run_audit(digest_text: str, *, root: Optional[Path] = None,
              write: bool = True,
              use_llm: Optional[bool] = None) -> Dict[str, Any]:
    """Audit the digest, attach the blocker trend vs prior audits, and (by
    default) persist sidecar + feedback queue + audit history."""
    root_path = Path(root) if root else REPO_ROOT
    audit = audit_daily_digest(digest_text, use_llm=use_llm)
    signals = extract_digest_signals(digest_text or "")
    snapshot = build_blocker_snapshot(audit, signals)
    audit["audit_trend"] = compute_audit_trend(
        snapshot, load_audit_history(root_path))
    if write:
        write_audit_outputs(audit, root=root_path,
                            blocker_snapshot=snapshot)
    return audit


# ── CLI ──────────────────────────────────────────────────────────────────────


def load_latest_digest_note(root: Optional[Path] = None) -> Optional[str]:
    """Most recent Daily Research Digest note body from the Phase 6 journal
    (read-only)."""
    root = Path(root) if root else REPO_ROOT
    path = root / JOURNAL_JSONL_REL
    if not path.exists():
        return None
    latest: Optional[str] = None
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except Exception:
                continue
            if entry.get("source_view") == "journal_digest_script" \
                    and entry.get("note"):
                latest = str(entry["note"])
    except Exception:
        return None
    return latest


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Audit the latest daily research journal digest "
                    "(research-only; LLM with deterministic fallback).")
    p.add_argument("--digest-file", default=None, metavar="PATH",
                   help="audit this text file instead of the latest "
                        "journal digest note")
    p.add_argument("--dry-run", action="store_true",
                   help="print the audit; write nothing")
    p.add_argument("--skip-llm", action="store_true",
                   help="force the deterministic rule-based audit")
    p.add_argument("--root", default=None, help=argparse.SUPPRESS)
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    root = Path(args.root) if args.root else REPO_ROOT

    if args.digest_file:
        digest_path = Path(args.digest_file)
        if not digest_path.exists():
            print(f"digest file not found: {digest_path}")
            return 1
        digest_text = digest_path.read_text(encoding="utf-8")
    else:
        digest_text = load_latest_digest_note(root)
        if digest_text is None:
            print("no daily digest note found in "
                  f"{root / JOURNAL_JSONL_REL} — run journal-digest first.")
            return 1

    audit = run_audit(digest_text, root=root, write=not args.dry_run,
                      use_llm=False if args.skip_llm else None)
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    if args.dry_run:
        print("\n[dry-run] nothing written.")
    else:
        print(f"\naudit written to {root / AUDIT_SIDECAR_REL}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

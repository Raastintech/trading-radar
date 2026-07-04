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
    the audit sidecar (cache/research/journal_audit_latest.json) and the
    feedback queue (logs/research_engine_feedback_queue.jsonl).
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

SCANNER_RECALL_FLOOR_PCT = 5.0

_RECALL_RE = re.compile(
    r"recall[^0-9%\n]{0,40}?([0-9]+(?:\.[0-9]+)?)\s*%", re.IGNORECASE)
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
        "phase4b_blocked": bool(re.search(r"phase\s*4b[^\n]{0,40}blocked",
                                          lower)),
        "tracker_verdict": tracker_verdict,
        "forward_immature": tracker_verdict in IMMATURE_FORWARD_VERDICTS,
        "scanner_recall_pct": recall_pct,
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
    if sig["forward_immature"]:
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
  "recommended_claude_code_tasks": ["..."]
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


def _queue_has_digest(queue_path: Path, digest_sha: str,
                      audit_source: str) -> bool:
    """True when this digest was already queued by the same audit source.
    An LLM re-audit of a digest a fallback already covered still queues —
    its tasks are strictly richer; identical re-runs stay deduped."""
    if not queue_path.exists():
        return False
    try:
        for line in queue_path.read_text(encoding="utf-8").splitlines():
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
                        root: Optional[Path] = None) -> Dict[str, Path]:
    """Persist the audit: full JSON sidecar + recommended tasks appended to
    the feedback queue (deduped per digest hash).  These are the module's
    only writes."""
    root = Path(root) if root else REPO_ROOT
    sidecar = root / AUDIT_SIDECAR_REL
    queue = root / FEEDBACK_QUEUE_REL

    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")

    digest_sha = audit.get("digest_sha256") or ""
    audit_source = audit.get("audit_source") or "rule_based_fallback"
    if audit.get("recommended_claude_code_tasks") \
            and not _queue_has_digest(queue, digest_sha, audit_source):
        queue.parent.mkdir(parents=True, exist_ok=True)
        with queue.open("a", encoding="utf-8") as fh:
            for task in audit["recommended_claude_code_tasks"]:
                fh.write(json.dumps({
                    "queued_at": audit.get("generated_at"),
                    "source": "journal_audit_reviewer",
                    "audit_source": audit_source,
                    "digest_sha256": digest_sha,
                    "engine_health": audit.get("engine_health"),
                    "research_verdict": audit.get("research_verdict"),
                    "task": task,
                }, ensure_ascii=False) + "\n")
    return {"sidecar": sidecar, "queue": queue}


def run_audit(digest_text: str, *, root: Optional[Path] = None,
              write: bool = True,
              use_llm: Optional[bool] = None) -> Dict[str, Any]:
    """Audit the digest and (by default) persist sidecar + feedback queue."""
    audit = audit_daily_digest(digest_text, use_llm=use_llm)
    if write:
        write_audit_outputs(audit, root=root)
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

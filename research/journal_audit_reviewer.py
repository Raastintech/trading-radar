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
  - CRED-FREE.  No dependency on core.config; the LLM provider key is
    optional (default provider: DeepSeek via core/llm_clients — see
    docs/ops/LLM_PROVIDER.md).  If the LLM call fails, times out, has no
    API key, or returns invalid JSON, a deterministic rule-based fallback
    audit is returned instead — the nightly cycle never blocks on the LLM.
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

# Optional explicit model override for the active provider; empty means
# "use the provider's reasoner-role default" (deepseek-reasoner for the
# default DeepSeek provider).  The pre-migration
# JOURNAL_AUDIT_ANTHROPIC_MODEL var is honoured only when the provider is
# the explicitly re-enabled Anthropic fallback.
MODEL_ENV_VAR = "JOURNAL_AUDIT_LLM_MODEL"
LEGACY_ANTHROPIC_MODEL_ENV_VAR = "JOURNAL_AUDIT_ANTHROPIC_MODEL"
LLM_TIMEOUT_SECONDS = 240.0
# 2026-07-16: raised 4000 -> 8000 after a real truncation — the 2026-07-16
# nightly response hit the 4000-token cap mid-JSON (~9.5k chars) and fell
# back with "JSONDecodeError: Expecting ',' delimiter ... char 9525".
# 2026-07-21: raised 8000 -> 16000 — 8000 was still truncating (usage log
# shows completion_tokens=8000 exactly, i.e. the model used its whole
# budget and got cut mid-response on 2026-07-18 and 2026-07-21). The
# 8000 cap was also hitting core/llm_clients/deepseek_client.py's
# PROVIDER_MAX_OUTPUT_TOKENS clamp (was 8192, now 65536 — see that file),
# so both layers were capping this call; 16000 gives real headroom under
# the new clamp. Timeout raised 90s -> 240s to match: the 8000-token
# completion on 2026-07-21 already took ~133s, so a larger budget needs
# more wall-clock room. This is a nightly batch job — latency isn't
# user-facing.
LLM_MAX_TOKENS = 16000
# Raw response preserved here whenever the LLM reply cannot be parsed, so
# a fallback night leaves the evidence needed to diagnose it.
LLM_RAW_ERROR_REL = Path("logs") / "journal_audit_llm_raw_error.txt"

RESEARCH_VERDICTS = ("RESEARCH_ONLY", "CAUTION", "READY_FOR_HUMAN_REVIEW")
QUALITY_LEVELS = ("WEAK", "WEAK_TO_MIXED", "MIXED", "IMPROVING", "STRONG")
ENGINE_HEALTH = ("OPERATIONAL", "OPERATIONAL_WITH_BLOCKERS", "DEGRADED",
                 "INSUFFICIENT_DATA")
SEVERITIES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
FLAW_AREAS = ("scanner_recall", "data_quality", "forward_evidence",
              "fundamental_overlay", "options_overlay", "regime_filter",
              "ranking_logic", "journal_wording", "high_conviction_selection",
              "other")

# candidate_quality_summary — balanced analyst interpretation of the board.
CANDIDATE_QUALITY_LEVELS = ("IMPROVING_BUT_UNPROVEN", "WEAK", "MIXED",
                            "STRONG_BUT_BLOCKED")
# Tier rank for the conservative-only LLM merge: the LLM may DEMOTE a name
# (higher -> caution -> speculative) but never promote one.
_TIER_RANK = {"higher_quality_manual_review": 2, "caution_manual_review": 1,
              "speculative_or_prove_it": 0}

MISSING_EVIDENCE_AREAS = ("forward_evidence", "data_quality",
                          "sector_alignment", "options_overlay", "other")

# Fundamental tiering thresholds (digest-text interpretation only — these
# never touch scanner scores, rankings, or gates).
THIN_OPERATING_MARGIN_PCT = 3.0
HIGH_DILUTION_3Q_PCT = 5.0

# Weak-sector ETFs -> sector-name fragments used for the alignment
# cross-check (substring match against digest top-name sectors).
SECTOR_ETF_NAMES = {
    "XLK": "Technology", "XLU": "Utilities", "XLB": "Materials",
    "XLE": "Energy", "XLF": "Financial", "XLV": "Health",
    "XLI": "Industrials", "XLY": "Consumer Cyclical",
    "XLP": "Consumer Defensive", "XLC": "Communication Services",
    "XLRE": "Real Estate",
}

# Tracker verdicts that mean the forward evidence is not yet trustworthy.
IMMATURE_FORWARD_VERDICTS = {"MIXED", "INCONCLUSIVE", "NEED_MORE_DATA"}
# Tracker verdicts that mean the forward evidence is actively negative.
NEGATIVE_FORWARD_VERDICTS = {"NO_FORWARD_EDGE", "NO_VALUE", "FAIL"}

SCANNER_RECALL_FLOOR_PCT = 5.0

# next_system_actions schema
PRIORITIES = ("P0", "P1", "P2")
ACTION_AREAS = ("scanner_recall", "forward_evidence", "options_overlay",
                "data_quality", "fundamental_overlay", "sector_alignment")
MAX_SYSTEM_ACTIONS = 10

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

# ── analyst-context regexes (deterministic pre-parse for the LLM) ────────────
_TICKER = r"[A-Z][A-Z0-9.\-]{0,7}"
_HIGH_PRIORITY_RE = re.compile(
    r"^-\s*Broad-scanner candidates[^:]*:\s*(.+)$",
    re.IGNORECASE | re.MULTILINE)
_REVIEW_FIRST_RE = re.compile(
    r"^-\s*Review first:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_FUNDAMENTAL_ROW_RE = re.compile(
    rf"^-\s*({_TICKER}):\s*quality\s+([A-Z_]+)"
    r"(?:\s*\|\s*GM\s+(-?[\d.]+)%)?"
    r"(?:\s*\|\s*OM\s+(-?[\d.]+)%)?"
    r"(?:\s*\|\s*(net debt|net cash))?"
    r"(?:\s*\|\s*dilution\s+3q\s+([+-]?[\d.]+)%)?",
    re.MULTILINE)
_SOCIAL_SOURCE_RE = re.compile(
    rf"^-\s*({_TICKER}):\s*Social attention signal",
    re.IGNORECASE | re.MULTILINE)
_MOMENT_OF_TRUTH_RE = re.compile(
    r"moment-of-truth verdict:\s*([A-Z_]+)", re.IGNORECASE)
_MATURED_RE = re.compile(
    r"matured\s+(5d|10d|20d):\s*([^|\n]+)", re.IGNORECASE)
_SESSION_READINESS_RE = re.compile(
    r"scan readiness:\s*(\w+)", re.IGNORECASE)
_REQUIRED_SESSION_RE = re.compile(
    r"required market session:\s*(\d{4}-\d{2}-\d{2})", re.IGNORECASE)
_BENCH_ALIGNED_RE = re.compile(
    r"benchmarks aligned:\s*(\w+)", re.IGNORECASE)
_SESSION_MISMATCH_RE = re.compile(
    r"session-mismatch excluded:\s*(\d+)", re.IGNORECASE)
_FROZEN_SOURCE_RE = re.compile(
    r"frozen-source status:.*?\bSTALE\b", re.IGNORECASE)
_STALE_SKIPPED_RE = re.compile(
    r"stale-price skipped:\s*(\d+)", re.IGNORECASE)
_SUSPECT_SKIPPED_RE = re.compile(
    r"suspect-feed skipped:\s*(\d+)", re.IGNORECASE)
_BACKFILL_COUNT_RE = re.compile(
    r"(\d+)\s+tickers?\s+need\s*>=?\s*300\s*bars", re.IGNORECASE)
_REGIME_RE = re.compile(
    r"^-\s*Regime:\s*(.+?)\s*\(confidence\s+(\w+)",
    re.IGNORECASE | re.MULTILINE)
_SECTORS_RE = re.compile(
    r"Leading sectors:\s*([^|\n]+?)\s*\|\s*weak sectors:\s*([^\n]+)",
    re.IGNORECASE)
_TOP_NAME_SECTORS_RE = re.compile(
    r"^-\s*Top-name sectors:\s*(.+?)\s*[—-]+\s*alignment:\s*(\w+)",
    re.IGNORECASE | re.MULTILINE)
_PROGRAM_LINE_RE = re.compile(
    r"^-\s*(TACTICAL|SWING|LONG_TERM)\s*\(hold[^)]*\):\s*candidates today"
    r"\s*(\d+)\s*\|\s*verdict\s*([A-Z_]+)",
    re.IGNORECASE | re.MULTILINE)

# Per-program verdict vocabulary (Phase 5.1) — matches the pre-registered
# gates in research/research_programs.py plus UNKNOWN for a missing section.
PROGRAM_VERDICTS = ("VALIDATED_EDGE", "PROMISING_BUT_UNPROVEN",
                    "NO_EVIDENCE_OF_EDGE", "INSUFFICIENT_MATURE_EVIDENCE",
                    "UNKNOWN")


def _parse_ticker_list(raw: str) -> List[str]:
    """Comma-separated tickers from a digest line; '(+N more)' and
    non-ticker tokens are dropped.  A trailing '(QUALITY_TIER)' annotation
    (e.g. 'FRMM (UNPROFITABLE_FUNDED)') is stripped, keeping the ticker."""
    out: List[str] = []
    for token in re.sub(r"\(\+\d+\s+more\)", "", raw or "").split(","):
        token = re.sub(r"\s*\([A-Z_]+\)$", "", token.strip())
        if token and re.fullmatch(_TICKER, token) and token not in out:
            out.append(token)
    return out


def _parse_name_list(raw: str) -> List[str]:
    """Comma-separated sector names; 'none' means empty."""
    items = [s.strip() for s in (raw or "").split(",")]
    items = [s for s in items if s and s.lower() not in ("none", "n/a", "-")]
    return items


def _float_or_none(raw: Any) -> Optional[float]:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


# ── digest signal extraction (deterministic, text-only) ──────────────────────


# One-line contract shipped with every audit so a reader (human or LLM)
# never mistakes an honest non-aligned label + flag=false for a bug.
_ALIGNMENT_FLAG_SEMANTICS = (
    "sector_alignment_questionable fires ONLY when the digest claims "
    "'aligned' despite no leading sectors and weak-sector overlap (a "
    "dishonest label). Honest labels — misaligned / mixed / "
    "not_assessable / unconfirmed — describe the market tape and are "
    "intentionally NOT flagged; they are not an inconsistency.")


def reconcile_sector_alignment(alignment_label: Optional[str],
                               leading_sectors: List[str],
                               weak_overlap: List[str],
                               sectors_line_present: bool,
                               questionable: bool) -> Dict[str, Any]:
    """Assert the alignment label and the questionable flag agree with the
    flag's contract, so a drifted edit to either side surfaces as a logged
    mismatch instead of a silent contradiction in the audit context."""
    expected = bool(
        sectors_line_present and not leading_sectors and weak_overlap
        and (alignment_label or "aligned") == "aligned")
    consistent = questionable == expected
    if consistent:
        note = (f"label '{alignment_label or 'absent'}' and "
                f"questionable={questionable} agree with the flag contract")
    else:
        note = (f"label '{alignment_label or 'absent'}' with "
                f"questionable={questionable} contradicts the flag contract "
                f"(expected {expected}; leading={leading_sectors}, "
                f"weak_overlap={weak_overlap})")
    return {
        "consistent": consistent,
        "expected_questionable": expected,
        "note": note,
        "flag_semantics": _ALIGNMENT_FLAG_SEMANTICS,
    }


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

    # ── analyst context: candidates, fundamentals, forward depth, sectors ──
    m = _HIGH_PRIORITY_RE.search(text)
    high_priority = _parse_ticker_list(m.group(1)) if m else []
    m = _REVIEW_FIRST_RE.search(text)
    review_first = _parse_ticker_list(m.group(1)) if m else []

    fundamentals: Dict[str, Dict[str, Any]] = {}
    for fm in _FUNDAMENTAL_ROW_RE.finditer(text):
        fundamentals[fm.group(1)] = {
            "quality": fm.group(2).upper(),
            "gm_pct": _float_or_none(fm.group(3)),
            "om_pct": _float_or_none(fm.group(4)),
            "balance": (fm.group(5) or "").lower() or None,
            "dilution_3q_pct": _float_or_none(fm.group(6)),
        }

    social_source = []
    for sm in _SOCIAL_SOURCE_RE.finditer(text):
        if sm.group(1) not in social_source:
            social_source.append(sm.group(1))

    moment_of_truth = None
    m = _MOMENT_OF_TRUTH_RE.search(text)
    if m:
        moment_of_truth = m.group(1).upper()

    matured: Dict[str, Optional[int]] = {}
    matured_20d_missing = False
    for mm in _MATURED_RE.finditer(text):
        horizon, raw = mm.group(1).lower(), mm.group(2).strip()
        digits = re.match(r"(\d+)", raw)
        matured[horizon] = int(digits.group(1)) if digits else None
        if horizon == "20d" and digits is None:
            # explicit "matured 20d: not tracked ..." (or similar prose)
            matured_20d_missing = True

    m = _STALE_SKIPPED_RE.search(text)
    stale_count = int(m.group(1)) if m else None
    m = _SUSPECT_SKIPPED_RE.search(text)
    suspect_count = int(m.group(1)) if m else None
    m = _BACKFILL_COUNT_RE.search(text)
    backfill_ticker_count = int(m.group(1)) if m else None

    regime = regime_confidence = None
    m = _REGIME_RE.search(text)
    if m:
        regime, regime_confidence = m.group(1).strip(), m.group(2).lower()

    leading_sectors: List[str] = []
    weak_sectors: List[str] = []
    m = _SECTORS_RE.search(text)
    sectors_line_present = m is not None
    if m:
        leading_sectors = _parse_name_list(m.group(1))
        weak_sectors = [s.upper() for s in _parse_name_list(m.group(2))]

    top_name_sectors: List[str] = []
    alignment_label = None
    m = _TOP_NAME_SECTORS_RE.search(text)
    if m:
        top_name_sectors = _parse_name_list(m.group(1))
        alignment_label = m.group(2).lower()

    programs: Dict[str, Dict[str, Any]] = {}
    for pm in _PROGRAM_LINE_RE.finditer(text):
        programs[pm.group(1).upper()] = {
            "candidates_today": int(pm.group(2)),
            "verdict": pm.group(3).upper(),
        }

    weak_overlap = sorted({
        etf for etf in weak_sectors
        if any(SECTOR_ETF_NAMES.get(etf, etf).lower() in s.lower()
               for s in top_name_sectors)})
    # Questionable only when the digest CLAIMS alignment despite a
    # no-leadership tape with weak-sector overlap — the contradiction.
    # An honest label ("not_assessable", "mixed", "misaligned") for the
    # same tape is the correct behavior and must not be flagged.
    sector_alignment_questionable = bool(
        sectors_line_present and not leading_sectors and weak_overlap
        and (alignment_label or "aligned") == "aligned")
    sector_alignment_reconciliation = reconcile_sector_alignment(
        alignment_label, leading_sectors, weak_overlap,
        sectors_line_present, sector_alignment_questionable)
    if not sector_alignment_reconciliation["consistent"]:
        print("WARNING: sector-alignment reconciliation mismatch: "
              f"{sector_alignment_reconciliation['note']}", file=sys.stderr)

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
        # P0 scan-integrity signals (2026-07-10): mixed-session comparisons
        # and stale contextual modules are CRITICAL data-quality flaws.
        "scan_readiness": (
            _SESSION_READINESS_RE.search(text).group(1).upper()
            if _SESSION_READINESS_RE.search(text) else None),
        "required_market_session": (
            _REQUIRED_SESSION_RE.search(text).group(1)
            if _REQUIRED_SESSION_RE.search(text) else None),
        "benchmarks_aligned": (
            _BENCH_ALIGNED_RE.search(text).group(1).lower() == "true"
            if _BENCH_ALIGNED_RE.search(text) else None),
        "session_mismatch_count": (
            int(_SESSION_MISMATCH_RE.search(text).group(1))
            if _SESSION_MISMATCH_RE.search(text) else None),
        "frozen_source_stale": bool(_FROZEN_SOURCE_RE.search(text)),
        # analyst context (deterministic pre-parse)
        "high_priority_tickers": high_priority,
        "review_first_tickers": review_first,
        "fundamentals": fundamentals,
        "social_source_tickers": social_source,
        "moment_of_truth_verdict": moment_of_truth,
        "matured_5d": matured.get("5d"),
        "matured_10d": matured.get("10d"),
        "matured_20d": matured.get("20d"),
        "matured_20d_missing": matured_20d_missing,
        "stale_count": stale_count,
        "suspect_count": suspect_count,
        "backfill_ticker_count": backfill_ticker_count,
        "regime": regime,
        "regime_confidence": regime_confidence,
        "leading_sectors": leading_sectors,
        "weak_sectors": weak_sectors,
        "top_name_sectors": top_name_sectors,
        "sector_alignment_label": alignment_label,
        "weak_sector_overlap": weak_overlap,
        "sector_alignment_questionable": sector_alignment_questionable,
        "sector_alignment_reconciliation": sector_alignment_reconciliation,
        "digest_programs": programs,
        # Deliverable declarations (Phase 5.1 queue-quieting): when the
        # digest textually states that a diagnostic already exists, the
        # deterministic repair actions must not re-propose building it.
        # Text-grounded on purpose — if the report stops being produced
        # the declaration disappears and the action re-fires.
        "options_coverage_report_referenced":
            "options-coverage report" in lower,
        "recall_diagnostics_referenced":
            "scanner-recall diagnostics report" in lower,
        "red_flags_line_present": "red flags in review order:" in lower,
        "quarantine_report_referenced":
            "see quarantine-cause report" in lower,
        "quarantine_backfill_pending": "backfill_pending" in lower,
        # A PLAN-style warning ("backfill plan: N tickers need ...") is
        # actionable; a RESULT line ("Backfill: N succeeded") is not.
        "backfill_plan_pending": bool(
            re.search(r"backfill plan:\s*\d+\s+tickers?\s+need", lower)),
        # Forward-evidence milestone hook fired: a maturity threshold was
        # crossed today, so the forward verdicts must be re-examined
        # against the newly matured sample.
        "forward_reaudit_due": "re-audit due" in lower,
    }


# ── balanced analyst interpretation (deterministic) ─────────────────────────
#
# These builders read only the parsed digest signals.  They interpret the
# digest content — they never predict price direction, never promote a
# ticker, and never touch scanner scores, rankings, thresholds, gates, or
# artifacts.  They are the contract on every path: the LLM may DEMOTE a
# name to a more conservative tier or add missing-evidence items, but it
# can never promote a name or drop a deterministic finding.


def build_candidate_quality_summary(
        signals: Dict[str, Any]) -> Dict[str, Any]:
    """Tier the high-priority names by their digest fundamentals."""
    fundamentals = signals.get("fundamentals") or {}
    high_priority = signals.get("high_priority_tickers") or []
    higher: List[str] = []
    caution: List[str] = []
    speculative: List[str] = []
    for ticker in high_priority:
        row = fundamentals.get(ticker)
        if row is None:
            caution.append(ticker)  # no overlay row -> unverified
            continue
        om = row.get("om_pct")
        dilution = row.get("dilution_3q_pct")
        if row.get("quality") == "UNPROFITABLE_FUNDED" \
                or (om is not None and om < 0):
            speculative.append(ticker)
        elif (om is not None and om < THIN_OPERATING_MARGIN_PCT) \
                or (dilution is not None
                    and dilution >= HIGH_DILUTION_3Q_PCT):
            caution.append(ticker)
        elif row.get("quality") == "PROFITABLE_CASHGEN":
            higher.append(ticker)
        else:
            caution.append(ticker)

    unproven = bool(signals.get("phase4b_blocked")
                    or signals.get("forward_negative")
                    or signals.get("forward_immature"))
    tiered = len(higher) + len(caution) + len(speculative)
    if tiered == 0:
        overall = "WEAK"
    elif len(higher) == tiered and unproven:
        overall = "STRONG_BUT_BLOCKED"
    elif len(higher) * 2 >= tiered:
        overall = "IMPROVING_BUT_UNPROVEN"
    elif higher:
        overall = "MIXED"
    else:
        overall = "WEAK"

    return {
        "overall": overall,
        "higher_quality_manual_review": higher,
        "caution_manual_review": caution,
        "speculative_or_prove_it": speculative,
        "social_signal_requires_confirmation":
            list(signals.get("social_source_tickers") or []),
    }


def _missing(area: str, issue: str, why: str, fix: str) -> Dict[str, str]:
    return {"area": area, "issue": issue, "why_it_matters": why,
            "suggested_fix": fix}


def build_missing_evidence(signals: Dict[str, Any]) -> List[Dict[str, str]]:
    """Evidence/artifact gaps that weaken the digest's own conclusions."""
    out: List[Dict[str, str]] = []
    if signals.get("matured_20d_missing"):
        depth = []
        if signals.get("matured_5d") is not None:
            depth.append(f"matured 5d: {signals['matured_5d']}")
        if signals.get("matured_10d") is not None:
            depth.append(f"matured 10d: {signals['matured_10d']}")
        out.append(_missing(
            "forward_evidence",
            "Matured 20d forward evidence is not tracked in current "
            "artifacts" + (f" ({'; '.join(depth)})" if depth else "") + ".",
            "Swing/alpha research needs the 20d horizon; without it the "
            "edge verdict rests on short horizons only and could flip "
            "when longer outcomes mature.",
            "Add or repair 20d forward-outcome tracking in the "
            "forward-evidence artifacts and dashboard."))
    if signals.get("sector_alignment_questionable"):
        overlap = ", ".join(signals.get("weak_sector_overlap") or [])
        label = signals.get("sector_alignment_label") or "aligned"
        out.append(_missing(
            "sector_alignment",
            f"Alignment is labeled '{label}' while leading sectors are "
            f"none and weak sectors ({overlap}) overlap the top-name "
            "sectors.",
            "A no-leadership tape with top names concentrated in weak "
            "sectors contradicts an 'aligned' label and raises "
            "false-positive risk in candidate selection.",
            "Review the sector-alignment logic and wording for the "
            "no-leadership case; explain what 'aligned' means when weak "
            "sectors overlap top-name sectors."))
    if signals.get("options_overlay_disabled"):
        out.append(_missing(
            "options_overlay",
            "Options overlay is DISABLED for insufficient coverage.",
            "A corroborating evidence layer (options participation / IV "
            "context) is absent from candidate context.",
            "Produce an options-coverage gap report listing which top and "
            "watch names lack options data."))
    if signals.get("backfill_warning") \
            or signals.get("backfill_ticker_count"):
        n = signals.get("backfill_ticker_count")
        out.append(_missing(
            "data_quality",
            (f"Targeted backfill plan flags {n} tickers needing >=300 bars."
             if n else "Targeted backfill warnings are present."),
            "Insufficient price history weakens RS/MA fields and can "
            "produce unreliable candidate qualification.",
            "Run the targeted backfill (dry-run first, then --execute) and "
            "verify the bar-count floor afterwards."))
    return out


def build_unbiased_next_steps(signals: Dict[str, Any],
                              tiers: Dict[str, Any]) -> List[str]:
    """Neutral, research-only next steps derived from the digest facts."""
    steps: List[str] = []
    if signals.get("phase4b_blocked") or signals.get("forward_negative"):
        steps.append("Keep the research verdict RESEARCH_ONLY — no "
                     "promotion path until forward evidence proves an "
                     "edge.")
    if signals.get("high_priority_tickers"):
        steps.append("Run structured manual review only on the "
                     "high-priority names; watch-only names stay "
                     "watch-only.")
    if tiers.get("caution_manual_review") \
            or tiers.get("speculative_or_prove_it"):
        steps.append("Tier the high-priority names by fundamentals "
                     "(profitable cash generators vs thin-margin vs "
                     "unprofitable) instead of treating them as equals.")
    if signals.get("backfill_warning") \
            or signals.get("backfill_ticker_count"):
        n = signals.get("backfill_ticker_count")
        steps.append(f"Run the targeted backfill for the "
                     f"{n if n else 'flagged'} tickers needing >=300 "
                     "bars.")
    if signals.get("matured_20d_missing"):
        steps.append("Add or repair 20d forward-evidence tracking in the "
                     "current artifacts.")
    recall = signals.get("scanner_recall_pct")
    if recall is not None and recall < SCANNER_RECALL_FLOOR_PCT:
        steps.append("Continue the shadow recall experiment rather than "
                     "loosening production gates.")
    if signals.get("sector_alignment_questionable"):
        steps.append("Review the sector-alignment logic when leading "
                     "sectors are none but weak sectors overlap top-name "
                     "sectors.")
    social = tiers.get("social_signal_requires_confirmation") or []
    if social:
        steps.append("Require price/volume/RS and fundamental "
                     f"confirmation for social-attention-driven names "
                     f"({', '.join(social)}) before trusting their "
                     "placement.")
    return steps


def build_program_verdicts(signals: Dict[str, Any],
                           research_verdict: str) -> Dict[str, str]:
    """Per-program verdicts, restated from the digest's Research Programs
    section (which carries the pre-registered gate outcomes).  The LLM
    never overrides these; a missing section yields UNKNOWN.  Tactical,
    Swing, and Long-Term are never blended into one conclusion —
    overall_engine only reflects the safety verdict."""
    digest_programs = signals.get("digest_programs") or {}

    def _verdict(pid: str) -> str:
        raw = str((digest_programs.get(pid) or {}).get("verdict") or "")
        return raw if raw in PROGRAM_VERDICTS else "UNKNOWN"

    return {
        "tactical": _verdict("TACTICAL"),
        "swing": _verdict("SWING"),
        "long_term": _verdict("LONG_TERM"),
        "overall_engine": research_verdict,
    }


def _compose_balanced_takeaway(signals: Dict[str, Any],
                               tiers: Dict[str, Any]) -> str:
    higher = tiers.get("higher_quality_manual_review") or []
    tiered = (len(higher) + len(tiers.get("caution_manual_review") or [])
              + len(tiers.get("speculative_or_prove_it") or []))
    verdict = signals.get("tracker_verdict") or "unproven"
    blocked = " and Phase 4B stays BLOCKED" \
        if signals.get("phase4b_blocked") else ""
    if higher:
        return (f"The engine is operational and finding cleaner research "
                f"candidates ({len(higher)} of {tiered} high-priority "
                f"names are profitable cash generators), but it has not "
                f"proven forward alpha (tracker verdict {verdict}"
                f"{blocked}) — names deserve structured manual review, "
                "not promotion.")
    return (f"The engine is operational but candidate quality is weak and "
            f"forward alpha is unproven (tracker verdict {verdict}"
            f"{blocked}) — research-only posture holds.")


def _compose_two_sided_summary(signals: Dict[str, Any],
                               tiers: Dict[str, Any]) -> str:
    """One-line summary that reports candidate quality AND the blockers."""
    higher = tiers.get("higher_quality_manual_review") or []
    tiered = (len(higher) + len(tiers.get("caution_manual_review") or [])
              + len(tiers.get("speculative_or_prove_it") or []))
    blockers: List[str] = []
    if signals.get("tracker_verdict") in NEGATIVE_FORWARD_VERDICTS:
        blockers.append(signals["tracker_verdict"])
    if signals.get("phase4b_blocked"):
        blockers.append("blocked Phase 4B")
    recall = signals.get("scanner_recall_pct")
    if recall is not None and recall < SCANNER_RECALL_FLOOR_PCT:
        blockers.append(f"low recall ({recall:.1f}%)")
    if signals.get("options_overlay_disabled"):
        blockers.append("disabled options overlay")
    if signals.get("matured_20d_missing"):
        blockers.append("missing 20d evidence")
    if higher and blockers:
        return (f"Candidate quality improved with {len(higher)} of "
                f"{tiered} high-priority names profitable, but "
                f"{', '.join(blockers)} keep the engine research-only.")
    return ""


# ── rule-based fallback audit ────────────────────────────────────────────────


def _flaw(severity: str, area: str, issue: str, why: str,
          fix: str) -> Dict[str, str]:
    return {"severity": severity, "area": area, "issue": issue,
            "why_it_matters": why, "suggested_fix": fix}


# High-Conviction Alpha Shortlist line, emitted by
# journal_digest._section_high_conviction (stable machine format):
#   "  1. NVO [HIGH_CONVICTION] SWING score 81.4 (quality 100.0, growth
#    66.0, momentum 90.0, value 80.0) — ...why... | risk: ... |
#    Business Deterioration Risk: Low"
_HC_LINE_RE = re.compile(
    r"^\s*\d+\.\s+([A-Z0-9.\-]+)\s+\[([A-Z_]+)\]\s+(\w+)\s+score\s+([\d.]+)\s+"
    r"\(quality\s+(\S+),\s+growth\s+(\S+),\s+momentum\s+(\S+),\s+value\s+(\S+)\)"
    r"(.*)$")


def extract_high_conviction_signals(digest_text: str) -> Dict[str, Any]:
    """Parse the High-Conviction Alpha Shortlist section from the digest
    text.  Text-only (no artifact reads) so the auditor keeps its
    cred-free, deterministic contract."""
    members: List[Dict[str, Any]] = []
    in_section = False
    for raw in (digest_text or "").splitlines():
        line = raw.rstrip()
        if line.startswith("## "):
            in_section = "High-Conviction Alpha Shortlist" in line
            continue
        if not in_section:
            continue
        m = _HC_LINE_RE.match(line)
        if not m:
            continue

        def _f(v: str) -> Optional[float]:
            try:
                return float(v)
            except ValueError:
                return None

        tail = m.group(9) or ""
        dh = None
        dm = re.search(r"[Bb]usiness [Dd]eterioration [Rr]isk:\s+(\w+)", tail)
        if dm:
            dh = dm.group(1).upper()
        risk_txt = ""
        rm = re.search(r"\|\s*risk:\s*(.+?)"
                       r"(?:\s*\|\s*[Bb]usiness [Dd]eterioration|\s*$)", tail)
        if rm:
            risk_txt = rm.group(1).lower()
        members.append({
            "ticker": m.group(1),
            "classification": m.group(2),
            "program": m.group(3),
            "score": _f(m.group(4)),
            "quality": _f(m.group(5)),
            "growth": _f(m.group(6)),
            "momentum": _f(m.group(7)),
            "value_raw": m.group(8),
            "dead_horse": dh,
            "risk_text": risk_txt,
        })
    return {"members": members}


def build_emerging_outlier_flaws(digest_text: str) -> List[Dict[str, str]]:
    """Deterministic checks on the Emerging Outlier Watch section: the lane
    must not admit a HIGH business-deterioration name, and the digest itself
    surfaces per-name AUDIT FLAG lines the auditor promotes to findings.
    Text-only; keeps the auditor cred-free."""
    flaws: List[Dict[str, str]] = []
    in_section = False
    for raw in (digest_text or "").splitlines():
        line = raw.strip()
        if line.startswith("## "):
            in_section = "Emerging Outlier Watch" in line
            continue
        if not in_section:
            continue
        # per-name deterioration risk on a watch line
        m = re.search(r"-\s+([A-Z0-9.\-]+)\s+\([A-Z_]+\):.*"
                      r"business-deterioration\s+(HIGH)", line)
        if m:
            flaws.append(_flaw(
                "HIGH", "high_conviction_selection",
                f"Emerging Outlier {m.group(1)} entered with HIGH "
                "business-deterioration risk.",
                "The emerging lane must exclude multi-signal severe "
                "deterioration — this is a gate violation.",
                "Fix the deterioration gate in "
                "research/emerging_outlier_watch.py."))
        if line.startswith("- AUDIT FLAG:"):
            flaws.append(_flaw(
                "MEDIUM", "high_conviction_selection",
                "Emerging Outlier lane audit flag: "
                + line[len("- AUDIT FLAG:"):].strip(),
                "The emerging lane admitted a name that fails its own "
                "evidence bar (momentum-only or non-substantive).",
                "Review research/emerging_outlier_watch.py admission rules."))
    return flaws


def build_high_conviction_flaws(digest_text: str) -> List[Dict[str, str]]:
    """Deterministic checks on the shortlist: dead-horse names that slipped
    in, momentum-dominated selections, missing quality/growth/value inputs,
    and any fundamental red flag (dilution / negative gross margin / heavy
    debt) inside a shortlisted name.  The LLM may add to these but may not
    remove them or promote a rejected name."""
    hc = extract_high_conviction_signals(digest_text)
    flaws: List[Dict[str, str]] = []
    for c in hc["members"]:
        tk = c["ticker"]
        if c["dead_horse"] and c["dead_horse"] not in ("LOW", None):
            flaws.append(_flaw(
                "HIGH", "high_conviction_selection",
                f"Shortlisted {tk} carries business-deterioration risk "
                f"{c['dead_horse']}.",
                "A name with elevated business-deterioration risk should not "
                "sit in the high-conviction shortlist — price interest may be "
                "disconnected from deteriorating fundamentals.",
                "Review the deterioration gate; a HIGH name must be excluded "
                "and a MEDIUM name demoted below high conviction."))
        # missing quality / growth / value inputs → score built on partial
        # coverage (never impute; the audit surfaces the gap)
        missing = [n for n, v in (("quality", c["quality"]),
                                  ("growth", c["growth"]))
                   if v is None]
        value_missing = c["value_raw"] in ("VALUATION_UNAVAILABLE", "n/a",
                                           "None")
        if missing:
            flaws.append(_flaw(
                "MEDIUM", "high_conviction_selection",
                f"Shortlisted {tk} is missing {', '.join(missing)} "
                "input(s).",
                "A composite built on incomplete fundamentals can overstate "
                "conviction; missing factors must be reported, not imputed.",
                "Confirm the coverage cap held and the missing factors are "
                "shown explicitly on the candidate."))
        # momentum-dominated: strong price momentum with no fundamental base
        if (c["momentum"] is not None and c["momentum"] >= 80
                and (c["quality"] is None or value_missing)
                and (c["growth"] is None)):
            flaws.append(_flaw(
                "HIGH", "high_conviction_selection",
                f"Shortlisted {tk} looks momentum-dominated (momentum "
                f"{c['momentum']} with weak/absent fundamental support).",
                "The shortlist must distinguish a strong signal from a strong "
                "candidate; RS alone should not qualify a name.",
                "Verify the single-component cap and program burden gate "
                "applied to this name."))
        if "negative gross margin" in c["risk_text"] \
                or "extreme dilution" in c["risk_text"]:
            flaws.append(_flaw(
                "CRITICAL", "high_conviction_selection",
                f"Shortlisted {tk} shows a hard-exclusion red flag "
                "(negative gross margin or extreme dilution).",
                "These are gate violations — such a name must not appear in "
                "the shortlist at all.",
                "Fix the hard-exclusion gate in "
                "research/high_conviction_alpha.py."))
    return flaws


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
    # P0 scan integrity (2026-07-10): mixed-session comparisons are CRITICAL.
    if sig.get("benchmarks_aligned") is False or sig.get("scan_readiness") == "BLOCKED":
        flaws.append(_flaw(
            "CRITICAL", "data_quality",
            "Mixed-session scan: benchmark bars are not on the required "
            f"market session ({sig.get('required_market_session') or 'unknown'}).",
            "Every relative-strength and momentum figure compares candidate "
            "and benchmark prices from different sessions — the rankings "
            "are not trustworthy.",
            "Re-run refresh-universe-prices and the scanner; verify the "
            "benchmark parquets reach the required session."))
    if (sig.get("session_mismatch_count") or 0) > 50:
        flaws.append(_flaw(
            "HIGH", "data_quality",
            f"{sig['session_mismatch_count']} candidates were excluded with "
            "SESSION_MISMATCH.",
            "Coverage is materially reduced; the scan is honest but sees "
            "only part of the universe.",
            "Check the refresh step's failures/budget and the dead-symbol "
            "tombstone ledger for the excluded names."))
    if sig.get("frozen_source_stale") and sig.get("scan_readiness") in (None, "UNGATED", "UNKNOWN"):
        flaws.append(_flaw(
            "CRITICAL", "data_quality",
            "A stale contextual module (frozen universe snapshot) may be "
            "feeding apparently-current output while the scan ran ungated.",
            "Stale context silently distorts posture/breadth/labels.",
            "Verify the P0-B freshness gates are active and the scanner "
            "runs with the session gate enabled."))

    # High-Conviction Alpha Shortlist checks — deterministic, always run
    # (independent of the LLM path).  These verify the shortlist did not
    # let a momentum-only / dead-horse / red-flag name through.
    flaws.extend(build_high_conviction_flaws(digest_text))

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

    tiers = build_candidate_quality_summary(sig)
    if sig["empty"]:
        summary = ("Digest is empty or unreadable — engine state cannot be "
                   "audited; treat as insufficient data.")
    else:
        summary = _compose_two_sided_summary(sig, tiers) or (
            f"Engine {health.replace('_', ' ').lower()}; forward "
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
        "balanced_research_takeaway": _compose_balanced_takeaway(sig, tiers)
        if not sig["empty"] else "Digest unreadable — no takeaway.",
        "program_verdicts": build_program_verdicts(sig, research_verdict),
        "candidate_quality_summary": tiers,
        "missing_evidence_or_artifacts": build_missing_evidence(sig),
        "unbiased_next_steps": build_unbiased_next_steps(sig, tiers),
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
    # Suppressed when the digest declares the recall-diagnostics report
    # exists — the deliverable this action asks for is already built;
    # low recall itself stays visible as a HIGH flaw.
    if recall_low and not signals.get("recall_diagnostics_referenced"):
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

    # P0 — forward-evidence milestone crossed today (re-audit hook fired):
    # the verdicts on file were computed before this sample existed.
    if signals.get("forward_reaudit_due"):
        actions.append(_action(
            "P0", "forward_evidence",
            "Re-examine the forward-evidence verdicts against the newly "
            "matured sample: re-read the tracker, research-program, and "
            "high-conviction shortlist verdicts produced this cycle and "
            "state whether each changed, stayed, or needs more data. "
            "Do not change any gate or ranking from this re-read alone.",
            "A maturity milestone crossed today (RE-AUDIT DUE in the "
            "digest) — prior verdicts predate the sample that just "
            "matured.",
            "The audit records a per-verdict re-read (tracker / programs "
            "/ shortlist) dated on or after the milestone crossing."))

    # P0 — 20d matured horizon missing from current artifacts
    if signals.get("matured_20d_missing"):
        actions.append(_action(
            "P0", "forward_evidence",
            "Add matured 20d forward evidence to the current artifacts "
            "and dashboard so the swing/alpha horizon is measured "
            "alongside 5d and 10d.",
            "The digest reports matured 20d as not tracked in current "
            "artifacts — the horizon that matters most for swing/alpha "
            "research is unmeasured, so the edge verdict rests on short "
            "horizons only.",
            "Every digest reports 5d, 10d, and 20d forward stats with "
            "hit rate, mean, median, winsorized mean, excess vs SPY, and "
            "max adverse excursion."))

    # NOTE: no repair task is emitted for candidate-quality tiering.  The
    # audit itself now separates profitable cash generators / caution /
    # speculative / social-signal names (candidate_quality_summary), and
    # mixed fundamentals describe the market board, not a system gap — a
    # task keyed on that condition would re-queue forever.

    # P1 — sector-alignment logic review (wording/logic diagnostic only)
    if signals.get("sector_alignment_questionable"):
        overlap = ", ".join(signals.get("weak_sector_overlap") or [])
        actions.append(_action(
            "P1", "sector_alignment",
            "Review the sector-alignment logic and wording for the "
            "no-leadership case: explain how alignment is computed when "
            "leading sectors are none and weak sectors overlap the "
            "top-name sectors.",
            "The digest labels alignment "
            f"'{signals.get('sector_alignment_label') or 'aligned'}' "
            f"while leading sectors are none and weak sectors ({overlap}) "
            "overlap top-name sectors — the label may overstate "
            "confirmation.",
            "Audit/digest explains the alignment verdict when leading "
            "sectors are none and weak sectors overlap top-name sectors; "
            "no ranking or gate change."))

    # P1 — data quality (backfill / quarantine / missing artifacts).
    # Suppressed when the digest declares the quarantine-cause report
    # exists AND nothing is backfill-actionable (remaining quarantine is
    # young listings / repaired names awaiting re-scan) — re-running the
    # backfill would change nothing.
    data_quality_covered = (
        signals.get("quarantine_report_referenced")
        and not signals.get("quarantine_backfill_pending")
        and not signals.get("backfill_plan_pending")
        and not signals.get("missing_artifacts"))
    if ((signals.get("quarantine_count") or 0) > 0
            or signals.get("backfill_warning")
            or signals.get("missing_artifacts")
            or "data_quality" in flaw_areas) \
            and not data_quality_covered:
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

    # P2 — options coverage health.  Suppressed when the digest already
    # cites the options-coverage report (the deliverable exists; the
    # remaining gap is a provider-budget decision, not a diagnostic).
    if (signals.get("options_overlay_disabled")
            or "options_overlay" in flaw_areas) \
            and not signals.get("options_coverage_report_referenced"):
        actions.append(_action(
            "P2", "options_overlay",
            "Report options coverage health: number of tickers with valid "
            "options data, number skipped, the reason coverage is "
            "insufficient for each, and whether the options overlay is "
            "required or optional for each consuming research surface or report.",
            "The options overlay is DISABLED for insufficient coverage; "
            "without a coverage report it is unclear what data is missing "
            "and which consumers are degraded.",
            "Coverage-health sidecar lists valid/skipped ticker counts, "
            "per-ticker insufficiency reasons, and a required-vs-optional "
            "flag for every overlay consumer."))

    # P2 — fundamental red flags in review ordering (display only).
    # Suppressed when the digest already carries the red-flags line —
    # the surfacing exists; flags appearing is the feature working.
    if (signals.get("dilution_red_flag")
            or "fundamental_overlay" in flaw_areas) \
            and not signals.get("red_flags_line_present"):
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


def declared_covered_areas(signals: Dict[str, Any]) -> set:
    """Areas whose deliverable the digest textually declares to exist —
    neither the deterministic generator nor LLM proposals may re-propose
    diagnostics for these (Phase 5.1 queue-quieting)."""
    covered = set()
    if signals.get("options_coverage_report_referenced"):
        covered.add("options_overlay")
    if signals.get("recall_diagnostics_referenced"):
        covered.add("scanner_recall")
    if signals.get("red_flags_line_present"):
        covered.add("fundamental_overlay")
    if signals.get("quarantine_report_referenced") \
            and not signals.get("quarantine_backfill_pending") \
            and not signals.get("backfill_plan_pending") \
            and not signals.get("missing_artifacts"):
        covered.add("data_quality")
    return covered


def merge_system_actions(
        deterministic: List[Dict[str, str]],
        llm_proposed: List[Dict[str, str]],
        suppressed_areas: Optional[set] = None) -> List[Dict[str, str]]:
    """Deterministic actions win; LLM extras only fill uncovered areas,
    and never areas whose deliverable the digest declares to exist."""
    suppressed = suppressed_areas or set()
    covered = {a["area"] for a in deterministic} | suppressed
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
    "  - For the High-Conviction Alpha Shortlist section: review whether "
    "selections are consistent with their factors — whether weak "
    "fundamentals are overruled by momentum, whether valuation is ignored, "
    "whether extended names were promoted instead of routed to "
    "'quality but extended', whether any high business-deterioration-risk name entered, "
    "and whether missing data distorted a score.  You MAY demote or flag a "
    "shortlisted name; you may NOT promote a rejected name or alter its "
    "deterministic score.\n"
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
  "balanced_research_takeaway": "...",
  "program_verdicts": {{
    "tactical": "VALIDATED_EDGE" | "PROMISING_BUT_UNPROVEN" | "NO_EVIDENCE_OF_EDGE" | "INSUFFICIENT_MATURE_EVIDENCE" | "UNKNOWN",
    "swing": "...same enum...",
    "long_term": "...same enum...",
    "overall_engine": "RESEARCH_ONLY"
  }},
  "candidate_quality_summary": {{
    "overall": "IMPROVING_BUT_UNPROVEN" | "WEAK" | "MIXED" | "STRONG_BUT_BLOCKED",
    "higher_quality_manual_review": ["TICKER"],
    "caution_manual_review": ["TICKER"],
    "speculative_or_prove_it": ["TICKER"],
    "social_signal_requires_confirmation": ["TICKER"]
  }},
  "missing_evidence_or_artifacts": [
    {{
      "area": "forward_evidence" | "data_quality" | "sector_alignment" | "options_overlay" | "other",
      "issue": "...",
      "why_it_matters": "...",
      "suggested_fix": "..."
    }}
  ],
  "unbiased_next_steps": ["..."],
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

You are a balanced analyst, not only a blocker reporter.  Answer ALL of
these questions through the schema fields:
1. What is working?  (what_is_working)
2. What is still blocking alpha proof?  (flaws_detected + one_line_summary)
3. Are the highlighted names fundamentally equal, or should they be
   tiered?  (candidate_quality_summary — use the fundamental overlay rows)
4. Are any names over-promoted relative to their fundamentals?
   (caution_manual_review / speculative_or_prove_it)
5. Did any ticker appear because of social attention and therefore require
   price/volume/RS + fundamental confirmation?
   (social_signal_requires_confirmation)
6. Is any evidence missing that weakens the conclusion — e.g. an untracked
   forward horizon?  (missing_evidence_or_artifacts)
7. Are there wording or logic inconsistencies in sector alignment, forward
   evidence, or the final finding?  (flaws_detected and/or
   missing_evidence_or_artifacts area "sector_alignment")
8. What can be done next without bias?  (unbiased_next_steps)

Grading rules:
- Analyze each research program INDEPENDENTLY (Tactical / Swing /
  Long-Term).  Never blend them into one global performance conclusion.
  program_verdicts must restate the digest's "Research Programs" section
  verdicts exactly; overall_engine only restates the safety verdict.
- MIXED-SESSION comparisons are a CRITICAL data-quality flaw: if the
  digest shows benchmarks not aligned to the required market session,
  scan readiness BLOCKED, or a large SESSION_MISMATCH exclusion count,
  report a CRITICAL "data_quality" flaw — every RS/momentum figure in
  that scan compares prices from different sessions.
- STALE CONTEXTUAL MODULES are a CRITICAL data-quality flaw: if a
  frozen/stale source (e.g. the legacy universe snapshot) appears to feed
  apparently-current output rather than being gated UNAVAILABLE, report
  it as CRITICAL.
- If Phase 4B is BLOCKED, research_verdict must be "RESEARCH_ONLY".
- If forward evidence is MIXED, INCONCLUSIVE, or NEED_MORE_DATA,
  alpha_discovery_quality must not be "STRONG".
- Base every statement strictly on the digest text and the structured
  context below; if something is not there, do not claim it.
- one_line_summary must report BOTH sides: what improved (e.g. candidate
  quality) AND what keeps the engine research-only.  Not only the
  blockers.
- balanced_research_takeaway is the analyst conclusion: operational
  reality + candidate quality + why nothing is promotable yet.
- candidate_quality_summary may only contain tickers named in the digest.
  Tier by the fundamental overlay: profitable cash generators with sound
  margins are higher-quality manual review; thin operating margin
  (< 3%) or heavy dilution means caution; UNPROFITABLE_FUNDED or negative
  operating margin means speculative/prove-it.  Tiers describe review
  order for a human — never a buy/sell view.
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

Structured digest context (deterministically pre-parsed — ground your
tiering and analysis in this AND the digest text):
{context}

Digest:
---
{digest}
---"""


def _build_llm_context(signals: Dict[str, Any]) -> Dict[str, Any]:
    """Compact structured context handed to the LLM so it analyses the
    whole digest, not only the largest blockers."""
    return {
        "high_priority_tickers": signals.get("high_priority_tickers"),
        "review_first_tickers": signals.get("review_first_tickers"),
        "fundamental_overlay": signals.get("fundamentals"),
        "social_attention_source_tickers":
            signals.get("social_source_tickers"),
        "deterministic_candidate_tiers":
            build_candidate_quality_summary(signals),
        "forward_evidence": {
            "tracker_verdict": signals.get("tracker_verdict"),
            "moment_of_truth_verdict":
                signals.get("moment_of_truth_verdict"),
            "phase4b_blocked": signals.get("phase4b_blocked"),
            "matured_5d": signals.get("matured_5d"),
            "matured_10d": signals.get("matured_10d"),
            "matured_20d": signals.get("matured_20d"),
            "matured_20d_missing": signals.get("matured_20d_missing"),
        },
        "system_warnings": {
            "scanner_recall_pct": signals.get("scanner_recall_pct"),
            "recall_baseline_pct": signals.get("recall_baseline_pct"),
            "options_overlay_disabled":
                signals.get("options_overlay_disabled"),
            "backfill_ticker_count":
                signals.get("backfill_ticker_count"),
            "quarantine_count": signals.get("quarantine_count"),
            "stale_count": signals.get("stale_count"),
            "suspect_count": signals.get("suspect_count"),
        },
        "research_programs": signals.get("digest_programs"),
        "sector_regime": {
            "regime": signals.get("regime"),
            "confidence": signals.get("regime_confidence"),
            "leading_sectors": signals.get("leading_sectors"),
            "weak_sectors": signals.get("weak_sectors"),
            "top_name_sectors": signals.get("top_name_sectors"),
            "alignment_label": signals.get("sector_alignment_label"),
            "sector_alignment_questionable":
                signals.get("sector_alignment_questionable"),
            # Reconciliation ships with the context so the LLM never
            # reports an honest label + questionable=false as a bug.
            "sector_alignment_reconciliation":
                signals.get("sector_alignment_reconciliation"),
        },
    }


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


def _get_llm_client():
    """Active LLM client from core/llm_clients (lazy, cred-free import).
    Bootstraps sys.path for direct-script invocation
    (``python research/journal_audit_reviewer.py``)."""
    try:
        from core.llm_clients import get_llm_client
    except ImportError:
        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        from core.llm_clients import get_llm_client
    return get_llm_client()


def _llm_fail_open() -> bool:
    """LLM_FAIL_OPEN consult with an inline default (True) so a broken
    core/llm_clients import can never block the nightly."""
    try:
        from core.llm_clients import llm_fail_open
        return llm_fail_open()
    except ImportError:
        return os.getenv("LLM_FAIL_OPEN", "").strip().lower() not in (
            "0", "false", "no", "off")


def _llm_audit(digest_text: str) -> Dict[str, Any]:
    """Single completion via the configured provider (default: DeepSeek
    ``deepseek-reasoner`` — this is the reasoning-heavy audit path).
    Raises on any problem — the caller converts every failure into the
    deterministic fallback."""
    client = _get_llm_client()
    if client is None:
        raise RuntimeError(
            "LLM provider not configured "
            "(default deepseek — set DEEPSEEK_API_KEY in trading.env)")
    model_override = None
    if client.provider_name == "anthropic":
        model_override = (os.getenv(LEGACY_ANTHROPIC_MODEL_ENV_VAR, "").strip()
                          or None)
    model_override = os.getenv(MODEL_ENV_VAR, "").strip() or model_override

    context = json.dumps(
        _build_llm_context(extract_digest_signals(digest_text)),
        ensure_ascii=False, indent=2)
    resp = client.complete(
        _USER_PROMPT_TEMPLATE.format(context=context, digest=digest_text),
        system=_SYSTEM_PROMPT,
        role="chat",
        model=model_override,
        max_tokens=LLM_MAX_TOKENS,
        timeout=LLM_TIMEOUT_SECONDS,
        artifact=str(AUDIT_SIDECAR_REL),
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("model refused the request")
    text = resp.text
    if resp.stop_reason == "max_tokens":
        _save_raw_llm_error(text, "stop_reason=max_tokens")
        raise RuntimeError(
            f"LLM response truncated at max_tokens={LLM_MAX_TOKENS} "
            f"({len(text)} chars) — raw saved to {LLM_RAW_ERROR_REL}")
    try:
        audit = _extract_json_object(text)
    except Exception as exc:
        _save_raw_llm_error(text, f"{type(exc).__name__}: {exc}")
        raise
    audit["audit_source"] = "llm"
    audit["fallback_reason"] = None
    audit["model"] = resp.model
    audit["llm_provider"] = resp.provider
    return audit


def _save_raw_llm_error(text: str, reason: str) -> None:
    """Best-effort dump of an unparseable LLM reply — diagnosis only."""
    try:
        path = REPO_ROOT / LLM_RAW_ERROR_REL
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).isoformat()
        path.write_text(f"# {stamp} — {reason}\n{text}", encoding="utf-8")
    except Exception:
        pass


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


def _sanitize_candidate_summary(value: Any,
                                deterministic: Dict[str, Any],
                                known_tickers: set) -> Dict[str, Any]:
    """Conservative-only merge of the LLM's tiering onto the deterministic
    tiers.  The LLM may demote a name to a more conservative tier or flag
    extra digest tickers for caution/speculation/social confirmation; it
    can never promote a name to a better tier, and tickers not named in
    the digest are dropped."""
    det = {k: list(deterministic.get(k) or []) for k in _TIER_RANK}
    social = list(
        deterministic.get("social_signal_requires_confirmation") or [])
    overall = deterministic.get("overall") or "WEAK"

    if isinstance(value, dict):
        overall = _coerce_enum(value.get("overall"),
                               CANDIDATE_QUALITY_LEVELS, overall)

        def _clean(key: str) -> List[str]:
            return [t.upper() for t in _coerce_str_list(value.get(key), 16)
                    if t.upper() in known_tickers]

        llm_rank: Dict[str, int] = {}
        for tier, rank in _TIER_RANK.items():
            for t in _clean(tier):
                llm_rank[t] = min(rank, llm_rank.get(t, rank))

        det_rank: Dict[str, int] = {}
        for tier, rank in _TIER_RANK.items():
            for t in det[tier]:
                det_rank[t] = rank

        final_rank: Dict[str, int] = dict(det_rank)
        for t, rank in llm_rank.items():
            if t in final_rank:
                final_rank[t] = min(final_rank[t], rank)  # demote only
            elif rank < _TIER_RANK["higher_quality_manual_review"]:
                final_rank[t] = rank  # new caution/speculative flag ok
            # LLM-only "higher quality" additions are dropped.

        det = {tier: [t for t, r in final_rank.items() if r == rank]
               for tier, rank in _TIER_RANK.items()}
        # keep deterministic ordering where possible
        order = {t: i for i, t in enumerate(known_tickers_ordered(
            deterministic, llm_rank))}
        for tier in det:
            det[tier].sort(key=lambda t: order.get(t, 999))

        for t in _clean("social_signal_requires_confirmation"):
            if t not in social:
                social.append(t)

    return {
        "overall": overall,
        "higher_quality_manual_review":
            det["higher_quality_manual_review"],
        "caution_manual_review": det["caution_manual_review"],
        "speculative_or_prove_it": det["speculative_or_prove_it"],
        "social_signal_requires_confirmation": social,
    }


def known_tickers_ordered(deterministic: Dict[str, Any],
                          llm_rank: Dict[str, int]) -> List[str]:
    """Stable display order: deterministic tier order first, then any
    LLM-added tickers."""
    seen: List[str] = []
    for tier in _TIER_RANK:
        for t in deterministic.get(tier) or []:
            if t not in seen:
                seen.append(t)
    for t in llm_rank:
        if t not in seen:
            seen.append(t)
    return seen


def _sanitize_missing_evidence(value: Any) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    if not isinstance(value, list):
        return out
    for e in value[:8]:
        if not isinstance(e, dict):
            continue
        issue = str(e.get("issue") or "").strip()
        if not issue:
            continue
        area = str(e.get("area") or "").strip().lower()
        out.append(_missing(
            area if area in MISSING_EVIDENCE_AREAS else "other",
            issue,
            str(e.get("why_it_matters") or "").strip(),
            str(e.get("suggested_fix") or "").strip()))
    return out


def _merge_missing_evidence(
        deterministic: List[Dict[str, str]],
        llm_proposed: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Deterministic entries win; LLM extras only fill areas not already
    covered (same convention as merge_system_actions) — otherwise every
    LLM rewording of a deterministic gap piles up as a near-duplicate."""
    covered = {e["area"] for e in deterministic}
    merged = deterministic + [e for e in llm_proposed
                              if e["area"] not in covered]
    return merged[:8]


def _normalize_line(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _merge_steps(deterministic: List[str],
                 llm_proposed: List[str]) -> List[str]:
    seen = {_normalize_line(s) for s in deterministic}
    merged = list(deterministic)
    for s in llm_proposed:
        key = _normalize_line(s)
        if key and key not in seen:
            seen.add(key)
            merged.append(s)
    return merged[:10]


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
        "llm_provider": audit.get("llm_provider"),
    }
    # Hard invariants, independent of who produced the audit:
    if signals.get("phase4b_blocked") or signals.get("forward_negative"):
        clean["research_verdict"] = "RESEARCH_ONLY"
    if signals.get("forward_immature") \
            and clean["alpha_discovery_quality"] == "STRONG":
        clean["alpha_discovery_quality"] = "MIXED"

    # Per-program verdicts (Phase 5.1): the digest's Research Programs
    # section is ground truth — the LLM's proposal is ignored except as a
    # fallback for programs the digest did not report.
    det_programs = build_program_verdicts(signals,
                                          clean["research_verdict"])
    llm_programs = audit.get("program_verdicts") \
        if isinstance(audit.get("program_verdicts"), dict) else {}
    clean["program_verdicts"] = {
        key: (det_programs[key] if det_programs[key] != "UNKNOWN"
              else _coerce_enum(llm_programs.get(key), PROGRAM_VERDICTS,
                                "UNKNOWN"))
        for key in ("tactical", "swing", "long_term")
    }
    clean["program_verdicts"]["overall_engine"] = clean["research_verdict"]

    # Balanced analyst interpretation: deterministic tiers are the base;
    # the LLM may only demote names or add conservative flags.
    det_tiers = build_candidate_quality_summary(signals)
    known_tickers = (
        set(signals.get("high_priority_tickers") or [])
        | set(signals.get("review_first_tickers") or [])
        | set((signals.get("fundamentals") or {}).keys())
        | set(signals.get("social_source_tickers") or []))
    clean["candidate_quality_summary"] = _sanitize_candidate_summary(
        audit.get("candidate_quality_summary"), det_tiers, known_tickers)
    clean["balanced_research_takeaway"] = str(
        audit.get("balanced_research_takeaway") or "").strip() \
        or _compose_balanced_takeaway(signals,
                                      clean["candidate_quality_summary"])
    clean["missing_evidence_or_artifacts"] = _merge_missing_evidence(
        build_missing_evidence(signals),
        _sanitize_missing_evidence(
            audit.get("missing_evidence_or_artifacts")))
    clean["unbiased_next_steps"] = _merge_steps(
        build_unbiased_next_steps(signals,
                                  clean["candidate_quality_summary"]),
        _coerce_str_list(audit.get("unbiased_next_steps"), 10))

    # Repair tasks: deterministic actions from the digest signals are the
    # contract; sanitized LLM proposals only fill areas not already covered.
    clean["next_system_actions"] = merge_system_actions(
        build_next_system_actions(signals, clean["flaws_detected"]),
        clean["next_system_actions"],
        suppressed_areas=declared_covered_areas(signals))
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
            # LLM_FAIL_OPEN=true (default): degrade to the deterministic
            # fallback so the nightly never blocks on the LLM.  Setting it
            # false propagates the failure (debugging/verification only).
            if not _llm_fail_open():
                raise
            audit = build_fallback_audit(
                digest_text,
                reason=f"{type(exc).__name__}: {exc}"[:200])
    else:
        audit = build_fallback_audit(digest_text, reason="llm_disabled")

    # Deterministic High-Conviction Shortlist flaws are enforced on EVERY
    # path: the LLM may add findings but can never drop these, and it can
    # never promote a rejected name or alter the deterministic score.
    hc_flaws = (build_high_conviction_flaws(digest_text)
                + build_emerging_outlier_flaws(digest_text))
    if hc_flaws:
        existing = audit.get("flaws_detected") or []
        seen = {(f.get("severity"), f.get("issue")) for f in existing
                if isinstance(f, dict)}
        merged = list(existing)
        for f in hc_flaws:
            if (f["severity"], f["issue"]) not in seen:
                merged.append(f)
                seen.add((f["severity"], f["issue"]))
        audit["flaws_detected"] = merged

    clean = sanitize_audit(audit, signals)
    clean["generated_at"] = datetime.now(timezone.utc).isoformat()
    clean["digest_sha256"] = hashlib.sha256(
        digest_text.encode("utf-8")).hexdigest()
    _apply_safety_fields(clean)
    return clean


def _apply_safety_fields(obj: Dict[str, Any]) -> Dict[str, Any]:
    """Force the research-only safety contract onto the audit artifact —
    published invariants, not model claims.  Inline fallback keeps the
    module usable even if core/llm_clients is unimportable."""
    try:
        from core.llm_clients import apply_safety_fields
        return apply_safety_fields(obj)
    except ImportError:
        obj.update({
            "research_only": True,
            "promote_to_signal": False,
            "may_change_scores": False,
            "may_change_rankings": False,
            "may_change_gates": False,
            "may_change_verdicts": False,
        })
        return obj


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

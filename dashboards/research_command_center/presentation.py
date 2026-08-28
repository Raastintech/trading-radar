"""Candidate presentation helpers — cosmetic layer over the latest-scan
sidecar.

Everything in this module is DISPLAY ONLY.  It maps fields the research
engine already produced (score, priority, label, lifecycle, verdict,
session integrity) into operator-facing badges, compact evidence labels,
and a deterministic top-N split.  It never invents a score, never
reorders the underlying payload, and never mutates the sidecar on disk.

Shared by three renderers:
  - the Command Center web UI (via data_adapter.build_latest_research_scan)
  - the terminal output of research/latest_scan_programs.py
  - the journal digest's Top Candidates subsection

Doctrine: RESEARCH-ONLY.  No provider, DB, scanner, or scoring imports —
pure functions over dicts.  Canonical enum values (program_verdict,
priority, lifecycle) are passed through untouched; display labels live
alongside them, never in place of them.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

TOP_N_DEFAULT = 5

PAGE_RESEARCH_NOTE = ("All candidates are research-only and program "
                      "evidence remains unvalidated unless stated "
                      "otherwise.")

# Canonical verdict -> operator display label.  The canonical value is
# ALWAYS preserved in the payload; these are UI strings only.
VERDICT_DISPLAY_LABELS: Dict[str, str] = {
    "INSUFFICIENT_MATURE_EVIDENCE": "Insufficient evidence",
    "PROMISING_BUT_UNPROVEN": "Promising, unproven",
    "VALIDATED_EDGE": "Validated edge",
    "NO_EVIDENCE_OF_EDGE": "No evidence of edge",
}

# Priority labels the engine already emits (research/research_scoring.py
# PRIORITY_LABELS) -> (badge text, tone).  Tone: "positive" | "caution".
# A None entry means the priority carries no badge (e.g. plain watchlist).
PRIORITY_BADGES: Dict[str, Optional[Tuple[str, str]]] = {
    "TOP_RESEARCH": ("TOP SCORE", "positive"),
    "HIGH_PRIORITY_RESEARCH": ("HIGH PRIORITY", "positive"),
    "WATCHLIST_RESEARCH": None,
    "RESET_WATCH": ("RESET WATCH", "positive"),
    "RECLAIM_WATCH": ("RECLAIM WATCH", "positive"),
    "CONFLICTED_SIGNAL": ("CONFLICTED", "caution"),
    "EXTENDED_CROWDED": ("EXTENDED", "caution"),
    "DATA_QUARANTINE": ("DATA ISSUE", "caution"),
    "INVALID": ("DATA ISSUE", "caution"),
}

# Watchlist label -> friendly badge.  The raw label stays on the card;
# this is the plain-English translation next to it.
#
# NOTE: for label "SOCIAL_ARB"/"CROWDED" this default is only used as a
# fallback when a row's signal origin can't be determined (e.g. an older
# cached artifact from before crowd_stage/signal_origin passthrough) —
# _social_arb_badge() below is the real lookup for social_arb_attention
# rows and distinguishes News Catalyst from Social Attention Radar.
LABEL_BADGES: Dict[str, Optional[Tuple[str, str]]] = {
    "SOCIAL_ARB": ("SOCIAL ATTENTION", "positive"),
    "CATALYST": ("CATALYST", "positive"),
    "RS_MOMENTUM_LEADER": ("STRONG RS", "positive"),
    "SECTOR_LEADER": ("SECTOR LEADER", "positive"),
    "EARLY_ACCUMULATION": ("ACCUMULATION", "positive"),
    "BEATEN_DOWN": ("RECOVERY SETUP", "positive"),
    "ASYMMETRIC_RECOVERY_WATCH": ("ASYMMETRIC RECOVERY", "positive"),
    "SPECULATIVE_10X": ("SPECULATIVE", "caution"),
    "RISKY": ("SPECULATIVE", "caution"),
    "EXTENDED": ("EXTENDED", "caution"),
    "NO_SOCIAL_DATA": None,
    "WATCH": None,
}

LIFECYCLE_BADGES: Dict[str, Optional[Tuple[str, str]]] = {
    "EARLY_CONFIRMATION": ("EARLY CONFIRMATION", "positive"),
    "VALIDATED_RESEARCH_CANDIDATE": ("VALIDATED", "positive"),
    "REJECTED": ("REJECTED", "caution"),
}

# Statuses that mean "do not show this as a normal top idea" — the
# candidate moves to the Data Issues subsection (never deleted).
DATA_ISSUE_PRIORITIES = ("DATA_QUARANTINE", "INVALID")
SUSPECT_DATA_CONFIDENCE = ("LOW", "SUSPECT", "SUSPECT_FEED", "STALE_SOURCE")

# Display tie-break ladders (presentation order only; the engine's
# numeric score always wins first).
_PRIORITY_ORDER = ("TOP_RESEARCH", "HIGH_PRIORITY_RESEARCH",
                   "WATCHLIST_RESEARCH", "RESET_WATCH", "RECLAIM_WATCH",
                   "CONFLICTED_SIGNAL", "EXTENDED_CROWDED",
                   "DATA_QUARANTINE", "INVALID")
_STATUS_ORDER = ("NEW", "RETURNING", "REPEAT")
_LIFECYCLE_ORDER = ("VALIDATED_RESEARCH_CANDIDATE", "EARLY_CONFIRMATION",
                    "UNDER_OBSERVATION", "RESEARCH_CANDIDATE", "DETECTED",
                    "REJECTED")


def verdict_display(verdict: Optional[str]) -> str:
    """UI label for a canonical verdict; unknown values pass through
    unchanged so nothing is ever silently relabelled."""
    if not verdict:
        return "—"
    return VERDICT_DISPLAY_LABELS.get(verdict, verdict)


def data_issue_reasons(candidate: Dict[str, Any]) -> List[str]:
    """Why a candidate belongs in the Data Issues subsection (empty list
    = clean).  Uses only statuses the engine already stamped."""
    reasons: List[str] = []
    priority = candidate.get("priority")
    if priority in DATA_ISSUE_PRIORITIES:
        reasons.append(str(priority))
    if candidate.get("same_session") is False:
        reasons.append("SESSION_MISMATCH")
    if not candidate.get("bar_as_of_date"):
        reasons.append("MISSING_BAR")
    conf = str(candidate.get("data_confidence") or "").upper()
    if conf in SUSPECT_DATA_CONFIDENCE:
        reasons.append(f"DATA_CONFIDENCE_{conf}")
    return reasons


def is_data_issue(candidate: Dict[str, Any]) -> bool:
    return bool(data_issue_reasons(candidate))


def evidence_summary(evidence: Optional[Dict[str, str]]) -> str:
    """Compact one-liner replacing the per-horizon string.  Never turns
    missing evidence into a conclusion — it only says what matured."""
    if not evidence:
        return "Evidence: none tracked"

    def _td(h: str) -> int:
        try:
            return int(str(h).rstrip("d"))
        except Exception:
            return 10 ** 6

    horizons = sorted(evidence, key=_td)
    matured = [h for h in horizons if evidence[h] == "MATURED"]
    trackable = [h for h in horizons if evidence[h] != "NOT_COLLECTED"]
    if not trackable:
        return "Evidence: none tracked"
    if not matured:
        return "Evidence: pending"
    if len(matured) == len(trackable):
        return f"Evidence: all horizons matured ({'/'.join(matured)})"
    return f"Evidence: {'/'.join(matured)} matured"


def _social_arb_badge(candidate: Dict[str, Any],
                      label: str) -> Optional[Tuple[str, str]]:
    """Badge for a social_arb_attention row — distinguishes News Catalyst
    Radar pickups from Social Attention Radar leads and surfaces
    EXHAUSTION_RISK, instead of the old blanket ("SOCIAL ATTENTION",
    "positive") applied to every row in this category regardless of source
    or crowd stage. Forward validation showed unfiltered social leads
    underperform a random control and EXHAUSTION_RISK is the strongest
    validated *negative* cohort — this badge is display-only and does not
    change watchlist_label, research_score, or routing.
    Only engaged for rows the scanner actually tagged social_arb_attention;
    every other category keeps using the plain LABEL_BADGES lookup below,
    since WATCH/RISKY/CROWDED are reused by unrelated scan categories."""
    stage = str(candidate.get("crowd_stage") or "").upper()
    if stage == "EXHAUSTION_RISK":
        return ("EXHAUSTION RISK", "caution")
    if label == "CROWDED":
        return ("CROWDED", "caution")
    if label == "SOCIAL_ARB":
        origin = str(candidate.get("signal_origin") or "").upper()
        if origin == "NEWS_CATALYST":
            return ("NEWS CATALYST", "positive")
        if origin == "SOCIAL_ATTENTION":
            return ("SOCIAL ATTENTION (unconfirmed)", "caution")
        if origin == "BOTH":
            return ("NEWS + SOCIAL ATTENTION", "positive")
    return LABEL_BADGES.get(label)


def candidate_badges(candidate: Dict[str, Any]) -> List[Dict[str, str]]:
    """Concise operator badges from fields the engine already produced.
    Order: scan status, priority, label translation, lifecycle, quality,
    cross-horizon.  No predictive potential is invented."""
    badges: List[Dict[str, str]] = []

    def _add(entry: Optional[Tuple[str, str]]) -> None:
        if entry and not any(b["text"] == entry[0] for b in badges):
            badges.append({"text": entry[0], "tone": entry[1]})

    status = candidate.get("scan_status")
    if status == "NEW":
        _add(("NEW", "positive"))
    elif status == "RETURNING":
        _add(("RETURNING", "positive"))

    label = str(candidate.get("label") or "")
    _add(PRIORITY_BADGES.get(str(candidate.get("priority") or "")))
    if candidate.get("scanner_category") == "social_arb_attention":
        _add(_social_arb_badge(candidate, label))
    else:
        _add(LABEL_BADGES.get(label))
    _add(LIFECYCLE_BADGES.get(str(candidate.get("lifecycle_stage") or "")))

    quality = str(candidate.get("fundamental_quality") or "")
    if quality == "PROFITABLE_CASHGEN":
        _add(("PROFITABLE", "positive"))
    elif quality.startswith("UNPROFITABLE"):
        _add(("UNPROFITABLE", "caution"))

    for reason in data_issue_reasons(candidate):
        if reason == "SESSION_MISMATCH":
            _add(("SESSION MISMATCH", "caution"))
        else:
            _add(("DATA ISSUE", "caution"))

    for prog in candidate.get("secondary_programs") or []:
        _add((f"+{str(prog).replace('_', '-')}", "cross"))
    return badges


def _rank_in(ladder: Tuple[str, ...], value: Any) -> int:
    try:
        return ladder.index(str(value))
    except ValueError:
        return len(ladder)


def display_sort_key(candidate: Dict[str, Any]):
    """Deterministic presentation order.  The engine's numeric score is
    primary; the rest only breaks ties (a NEW candidate never outranks a
    materially stronger REPEAT).  sorted() is stable, so candidates that
    tie on every key keep their existing artifact order."""
    score = candidate.get("research_score")
    has_score = isinstance(score, (int, float))
    return (
        0 if has_score else 1,
        -(score if has_score else 0),
        _rank_in(_PRIORITY_ORDER, candidate.get("priority")),
        _rank_in(_STATUS_ORDER, candidate.get("scan_status")),
        _rank_in(_LIFECYCLE_ORDER, candidate.get("lifecycle_stage")),
        str(candidate.get("ticker") or ""),
    )


def split_candidates(block: Dict[str, Any], top_n: int = TOP_N_DEFAULT
                     ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]],
                                List[Dict[str, Any]]]:
    """(top, rest, data_issues) for one program block.  Pure read — the
    block and its candidate list are never modified.  Data-issue
    candidates are separated, never hidden or deleted: top + rest +
    data_issues always re-covers every candidate exactly once."""
    candidates = list(block.get("candidates") or [])
    issues = [c for c in candidates if is_data_issue(c)]
    clean = sorted((c for c in candidates if not is_data_issue(c)),
                   key=display_sort_key)
    return clean[:top_n], clean[top_n:], issues


def common_reason(block: Dict[str, Any]) -> Optional[str]:
    """When every clean candidate in a program shares one generic
    detection reason (e.g. the Long-Term boilerplate), return it so
    renderers can state it once at the program level instead of on
    every card.  None when reasons differ or there are no candidates."""
    reasons = {(c.get("detection_reason") or "").strip()
               for c in block.get("candidates") or []
               if not is_data_issue(c)}
    reasons.discard("")
    return reasons.pop() if len(reasons) == 1 else None


def short_reason(candidate: Dict[str, Any], limit: int = 110) -> str:
    reason = (candidate.get("detection_reason") or "").strip()
    if not reason:
        reason = (candidate.get("research_hypothesis") or "").strip()
    if len(reason) > limit:
        reason = reason[:limit - 1].rstrip() + "…"
    return reason


def decorate_scan_payload(payload: Dict[str, Any],
                          top_n: int = TOP_N_DEFAULT) -> Dict[str, Any]:
    """Attach display metadata to an in-memory latest-scan payload.

    Adds a ``display`` dict per candidate, per program block, and at the
    payload root.  Additive only: candidate order, counts, scores, ranks
    and every canonical field are left untouched (guarded by tests).
    """
    if not payload or not payload.get("present"):
        return payload
    for block in (payload.get("programs") or {}).values():
        candidates = block.get("candidates") or []
        for c in candidates:
            c["display"] = {
                "badges": candidate_badges(c),
                "evidence_summary": evidence_summary(
                    c.get("evidence_maturity")),
                "verdict_label": verdict_display(c.get("program_verdict")),
                "short_reason": short_reason(c),
                "data_issue": is_data_issue(c),
                "data_issue_reasons": data_issue_reasons(c),
            }
        top, rest, issues = split_candidates(block, top_n)
        index = {id(c): i for i, c in enumerate(candidates)}
        block["display"] = {
            "top_idx": [index[id(c)] for c in top],
            "more_idx": [index[id(c)] for c in rest],
            "data_issue_idx": [index[id(c)] for c in issues],
            "common_reason": common_reason(block),
        }
    payload["display"] = {
        "top_n": top_n,
        "page_note": PAGE_RESEARCH_NOTE,
        "presentation_only": True,
    }
    return payload

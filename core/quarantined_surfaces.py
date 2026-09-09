"""
core/quarantined_surfaces.py — which cached artifacts may speak as CURRENT truth.

Why this module exists
----------------------
The 2026-09 drift audit found three artifacts that were still being rendered as
if they described the system today, when they did not:

* **Scanner Truth Review** publishes ``winner_recall_pct: 0.0`` nightly against
  ``measured_pipeline: "decommissioned_council_funnel_autopsy"`` — an autopsy of
  the council funnel frozen on 2026-06-13. It is structurally pinned near zero
  and cannot move, so a red 0% trained the reader to ignore a red number.
* **The 10x candidate radar** emitted 30 names twice a day with no registered
  forward hypothesis and no consumer that validates it — a list shaped like a
  finding.
* **Social Attention V11** went 12.6 days stale with no cadence at all while a
  V0 of the same idea ran nightly, so two surfaces disagreed and neither said
  which was live.

The fix is not deletion. Every one of these is legitimate history and stays on
disk, readable, exactly as written. What changes is that **operator surfaces ask
this module before presenting one as current**, and get back a reason they can
print instead of a number they would misread.

Contract
--------
``classify(name, payload)`` returns a verdict dict; ``is_current(...)`` is the
boolean most callers want. A quarantined artifact is never deleted, never
rewritten, and never silently hidden — the caller is expected to render the
``label`` and ``reason`` in place of the value.

RESEARCH-ONLY: this decides display eligibility. It computes no metric, emits no
signal, score, ranking or verdict about any stock, and changes no threshold.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

#: Status tokens. These are display classifications, not research verdicts.
CURRENT = "CURRENT"
DECOMMISSIONED_AUTOPSY = "DECOMMISSIONED_AUTOPSY"
QUARANTINE_NO_HYPOTHESIS = "QUARANTINE_NO_HYPOTHESIS"
SUPERSEDED_STALE = "SUPERSEDED_STALE"

#: Substrings in a producer's own ``measured_pipeline`` that mean "this measures
#: something that no longer runs". Matched case-insensitively so a producer can
#: spell it however it likes as long as it is honest about it.
DECOMMISSIONED_PIPELINE_MARKERS: tuple[str, ...] = (
    "decommissioned",
    "council_funnel",
    "legacy_funnel",
)

#: Artifacts quarantined by name, with the reason a reader should see instead.
#:
#: `scanner_truth_summary` is deliberately NOT listed here: it is classified from
#: its own `measured_pipeline` field, so if the review is ever re-pointed at the
#: live board it becomes CURRENT again on its own, with no edit here.
QUARANTINED_BY_NAME: dict[str, dict[str, str]] = {
    "ten_x_candidates": {
        "status": QUARANTINE_NO_HYPOTHESIS,
        "label": "QUARANTINE — RESEARCH ONLY",
        "reason": (
            "no registered forward hypothesis and no validating consumer; "
            "emitted names are not a current candidate list"),
    },
    "social_attention_v11": {
        "status": SUPERSEDED_STALE,
        "label": "SUPERSEDED — HISTORICAL",
        "reason": (
            "superseded by the nightly Social Attention radar; V11 has no "
            "cadence and its artifacts are historical, not current truth"),
    },
    "social_attention_v11_forward": {
        "status": SUPERSEDED_STALE,
        "label": "SUPERSEDED — HISTORICAL",
        "reason": "forward view of the superseded V11 radar; historical only",
    },
    "top_market_attention": {
        "status": SUPERSEDED_STALE,
        "label": "SUPERSEDED — HISTORICAL",
        "reason": "written by the superseded V11 radar; historical only",
    },
}


def _measures_decommissioned(payload: Optional[Mapping[str, Any]]) -> bool:
    if not payload:
        return False
    pipeline = str(payload.get("measured_pipeline") or "").lower()
    if any(m in pipeline for m in DECOMMISSIONED_PIPELINE_MARKERS):
        return True
    # A producer may also stamp itself directly.
    return str(payload.get("measurement_status") or "") == DECOMMISSIONED_AUTOPSY


def classify(name: str, payload: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    """Decide whether ``name``'s artifact may be presented as current truth.

    ``name`` is the sidecar's stem without ``_latest.json`` (e.g.
    ``"ten_x_candidates"``). ``payload`` is its parsed contents, when the caller
    has it — some classifications are made from the artifact's own fields so the
    rule follows the data rather than a hard-coded list.
    """
    if _measures_decommissioned(payload):
        return {
            "name": name,
            "status": DECOMMISSIONED_AUTOPSY,
            "is_current": False,
            "label": "DECOMMISSIONED AUTOPSY — NOT LIVE RECALL",
            "reason": (
                "measures the council funnel decommissioned 2026-06-13; the "
                "number cannot move and is not a reading on the live board"),
            "preserved": True,
        }
    hit = QUARANTINED_BY_NAME.get(name)
    if hit:
        return {"name": name, "is_current": False, "preserved": True, **hit}
    return {
        "name": name,
        "status": CURRENT,
        "is_current": True,
        "label": "",
        "reason": "",
        "preserved": True,
    }


def is_current(name: str, payload: Optional[Mapping[str, Any]] = None) -> bool:
    """True when this artifact may be shown as a current reading."""
    return bool(classify(name, payload)["is_current"])


def quarantine_note(name: str, payload: Optional[Mapping[str, Any]] = None) -> str:
    """One-line replacement text for a value that must not be shown."""
    v = classify(name, payload)
    if v["is_current"]:
        return ""
    return f"{v['label']} — {v['reason']}"

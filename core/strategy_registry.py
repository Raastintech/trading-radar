"""
core.strategy_registry - Current platform sleeve status.

This is the source of truth for the active paper-validation phase. It is an
operational registry, not strategy doctrine and not a tuning surface.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Optional


ACTIVE_PAPER = "active_paper"
FROZEN = "frozen"
FUTURE_RESEARCH = "future_research"
DECOMMISSIONED = "decommissioned"


@dataclass(frozen=True)
class SleeveStatus:
    key: str
    display_name: str
    status: str
    baseline_tag: str
    scanner_key: str = ""
    paper_ledger: bool = False
    tactical_outcomes: tuple[int, ...] = ()
    notes: str = ""


SLEEVE_REGISTRY: Dict[str, SleeveStatus] = {
    "VOYAGER": SleeveStatus(
        key="VOYAGER",
        display_name="VOYAGER",
        status=DECOMMISSIONED,
        baseline_tag="VOYAGER_PAPER",
        scanner_key="voyager",
        paper_ledger=False,
        tactical_outcomes=(),
        notes=(
            "Phase 3A decommission complete 2026-06-13. Trading daemon stopped. "
            "No new paper signals. Historical paper rows preserved for record-keeping. "
            "System is permanently RESEARCH_ONLY."
        ),
    ),
    "SNIPER": SleeveStatus(
        key="SNIPER",
        display_name="SNIPER v6",
        status=DECOMMISSIONED,
        baseline_tag="SNIPER_V6",
        scanner_key="sniper",
        paper_ledger=True,
        tactical_outcomes=(1, 3, 5, 10),
        notes=(
            "Phase 3A decommission complete 2026-06-13. Trading daemon stopped. "
            "No new paper signals. Historical paper rows preserved; "
            "paper_ledger=True kept so historical resolution continues. "
            "System is permanently RESEARCH_ONLY."
        ),
    ),
    "SHORT": SleeveStatus(
        key="SHORT",
        display_name="SHORT Sleeve A",
        status=FROZEN,
        baseline_tag="SHORT_A",
        scanner_key="short",
        paper_ledger=True,
        tactical_outcomes=(3, 5, 10),
        notes=(
            "Phase 1G.3 — FROZEN / research-only. Frozen 2026-05-24 after the "
            "Strategy Truth Review: net-negative realized sample with -18%/-20% "
            "short-squeeze tails, ~99.6% re-emission noise against the paper "
            "position cap, and a regime stance of 'avoid' in the bull-continuation "
            "tape. Historical SHORT_A evidence is preserved unchanged; the sleeve "
            "stays available for research/scorecards. Short-side awareness is kept "
            "alive by the research-only Short Opportunity Radar "
            "(research/short_opportunity_radar.py). No new paper signals are "
            "emitted while frozen. paper_ledger/tactical_outcomes are retained so "
            "historical reports keep resolving the existing rows."
        ),
    ),
    "REMORA": SleeveStatus(
        key="REMORA",
        display_name="REMORA",
        status=FROZEN,
        baseline_tag="REMORA_RESEARCH_ONLY",
        scanner_key="remora",
        notes="Frozen/research-only for this phase.",
    ),
    "CONTRARIAN": SleeveStatus(
        key="CONTRARIAN",
        display_name="CONTRARIAN",
        status=FROZEN,
        baseline_tag="CONTRARIAN_RESEARCH_ONLY",
        scanner_key="contrarian",
        notes="Frozen/research-only for this phase.",
    ),
    "SHORT_B": SleeveStatus(
        key="SHORT_B",
        display_name="SHORT Sleeve B",
        status=FROZEN,
        baseline_tag="SHORT_B_RESEARCH_ONLY",
        notes="Frozen/research-only; not part of the paper engine.",
    ),
    "PATHFINDER": SleeveStatus(
        key="PATHFINDER",
        display_name="PATHFINDER",
        status=FUTURE_RESEARCH,
        baseline_tag="PATHFINDER_V1",
        scanner_key="pathfinder",
        notes="Future research-only and paused; not operational.",
    ),
}

ALIASES = {
    "SNIPER_V6": "SNIPER",
    "VOYAGER_PAPER": "VOYAGER",
    "SHORT_A": "SHORT",
    "SHORT_SLEEVE": "SHORT",
    "SHORT SLEEVE A": "SHORT",
    "SHORT SLEEVE B": "SHORT_B",
}


def normalize_strategy(value: object) -> str:
    raw = str(value or "").strip().upper().replace("-", "_")
    return ALIASES.get(raw, raw)


def sleeve_status(strategy: object) -> Optional[SleeveStatus]:
    return SLEEVE_REGISTRY.get(normalize_strategy(strategy))


def is_active_paper_strategy(strategy: object) -> bool:
    sleeve = sleeve_status(strategy)
    return bool(sleeve and sleeve.status == ACTIVE_PAPER)


def is_frozen_strategy(strategy: object) -> bool:
    sleeve = sleeve_status(strategy)
    return bool(sleeve and sleeve.status in {FROZEN, FUTURE_RESEARCH, DECOMMISSIONED})


def active_paper_strategies() -> tuple[str, ...]:
    return tuple(k for k, v in SLEEVE_REGISTRY.items() if v.status == ACTIVE_PAPER)


def active_paper_ledger_strategies() -> tuple[str, ...]:
    return tuple(k for k, v in SLEEVE_REGISTRY.items() if v.status == ACTIVE_PAPER and v.paper_ledger)


def frozen_strategies() -> tuple[str, ...]:
    return tuple(k for k, v in SLEEVE_REGISTRY.items() if v.status in {FROZEN, FUTURE_RESEARCH, DECOMMISSIONED})


def decommissioned_strategies() -> tuple[str, ...]:
    return tuple(k for k, v in SLEEVE_REGISTRY.items() if v.status == DECOMMISSIONED)


def active_scanner_keys() -> tuple[str, ...]:
    return tuple(v.scanner_key for v in SLEEVE_REGISTRY.values() if v.status == ACTIVE_PAPER and v.scanner_key)


def active_paper_tags() -> Dict[str, str]:
    return {k: v.baseline_tag for k, v in SLEEVE_REGISTRY.items() if v.status == ACTIVE_PAPER}


def tactical_horizons() -> Dict[str, list[int]]:
    return {
        k: list(v.tactical_outcomes)
        for k, v in SLEEVE_REGISTRY.items()
        if v.status == ACTIVE_PAPER and v.tactical_outcomes
    }


# --- Historical-resolution helpers (Phase 1G.3) -----------------------------
# A sleeve that is FROZEN but still carries paper_ledger=True (e.g. SHORT_A
# after the 2026-05-24 freeze) must keep resolving the forward outcomes of its
# *already-logged* paper rows so historical reports/scorecards stay correct.
# These helpers therefore include paper-ledger sleeves regardless of frozen
# status. They are for RESOLUTION/REPORTING ONLY — new-signal emission is gated
# separately by active_scanner_keys() and is_active_paper_strategy(), which both
# exclude frozen sleeves, so a frozen sleeve never emits new signals.

def paper_ledger_strategies() -> tuple[str, ...]:
    return tuple(k for k, v in SLEEVE_REGISTRY.items() if v.paper_ledger)


def paper_ledger_tags() -> Dict[str, str]:
    return {k: v.baseline_tag for k, v in SLEEVE_REGISTRY.items() if v.paper_ledger}


def paper_ledger_horizons() -> Dict[str, list[int]]:
    return {
        k: list(v.tactical_outcomes)
        for k, v in SLEEVE_REGISTRY.items()
        if v.paper_ledger and v.tactical_outcomes
    }


def registry_rows(keys: Optional[Iterable[str]] = None) -> list[SleeveStatus]:
    selected = list(keys) if keys is not None else list(SLEEVE_REGISTRY)
    return [SLEEVE_REGISTRY[k] for k in selected if k in SLEEVE_REGISTRY]

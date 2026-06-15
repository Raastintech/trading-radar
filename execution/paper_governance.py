"""
execution/paper_governance.py — PERMANENTLY DISABLED (Phase 3A, 2026-06-13)

Archived for reproducibility only. Original preserved at:
  archive/execution_disabled/paper_governance.py

Paper-trade routing is permanently disabled. evaluate_paper_signal() raises
ResearchOnlyModeError on any call. Re-enabling requires deliberate code
restoration from the archive.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from typing import Dict, Iterable

from core.research_mode import ResearchOnlyModeError

logger = logging.getLogger(__name__)

_DISABLED_MSG = (
    "RESEARCH_ONLY_MODE: paper-trade routing is permanently disabled. "
    "evaluate_paper_signal() will never approve a signal. "
    "See archive/execution_disabled/paper_governance.py."
)


@dataclass(frozen=True)
class PaperGovernanceDecision:
    approved: bool
    reason: str
    allocation_bucket: str = ""
    allocation_pct: float = 0.0


def evaluate_paper_signal(signal: Dict, open_paper_positions: Iterable[Dict]) -> PaperGovernanceDecision:
    raise ResearchOnlyModeError(_DISABLED_MSG)

"""
execution/portfolio_allocator.py — PERMANENTLY DISABLED (Phase 3A, 2026-06-13)

Archived for reproducibility only. Original preserved at:
  archive/execution_disabled/portfolio_allocator.py

Allocation decisions are only meaningful when orders are being placed.
In RESEARCH_ONLY mode, PortfolioAllocator.evaluate() raises ResearchOnlyModeError.
Re-enabling requires deliberate code restoration from the archive.
"""
from __future__ import annotations
import logging
from typing import Dict, List, Optional, Tuple

from core.research_mode import ResearchOnlyModeError

logger = logging.getLogger(__name__)

_DISABLED_MSG = (
    "RESEARCH_ONLY_MODE: portfolio allocation is permanently disabled. "
    "See archive/execution_disabled/portfolio_allocator.py."
)


class PortfolioAllocator:
    def evaluate(
        self,
        signal: Dict,
        positions: List[Dict],
        equity: Optional[float] = None,
    ) -> Tuple[bool, str, str]:
        raise ResearchOnlyModeError(_DISABLED_MSG)

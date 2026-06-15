"""
execution/portfolio_risk.py — PERMANENTLY DISABLED (Phase 3A, 2026-06-13)

Archived for reproducibility only. Original preserved at:
  archive/execution_disabled/portfolio_risk.py

Portfolio risk enforcement is only meaningful when orders are being placed.
In RESEARCH_ONLY mode, PortfolioRisk methods raise ResearchOnlyModeError.
Re-enabling requires deliberate code restoration from the archive.
"""
from __future__ import annotations
import logging
from typing import Dict, List, Tuple

from core.research_mode import ResearchOnlyModeError

logger = logging.getLogger(__name__)

_DISABLED_MSG = (
    "RESEARCH_ONLY_MODE: portfolio risk enforcement is permanently disabled. "
    "See archive/execution_disabled/portfolio_risk.py."
)

# Constants preserved so existing importers don't break at import time.
BOOK_A = {"SNIPER", "REMORA"}
BOOK_B = {"CONTRARIAN"}
BOOK_C = {"VOYAGER", "SHORT"}
MAX_SINGLE_NAME_PCT   = 0.05
MAX_BOOK_A_PCT        = 0.60
MAX_BOOK_B_PCT        = 0.20
MAX_BOOK_C_PCT        = 0.30
MAX_GROSS_SHORT_PCT   = 0.30
MAX_POSITIONS_TOTAL   = 10


class PortfolioRisk:
    def check(self, signal: Dict, positions: List[Dict], equity: float) -> Tuple[bool, str]:
        raise ResearchOnlyModeError(_DISABLED_MSG)

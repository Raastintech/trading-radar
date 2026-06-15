"""
execution/position_monitor.py — PERMANENTLY DISABLED (Phase 3A, 2026-06-13)

Archived for reproducibility only. Original preserved at:
  archive/execution_disabled/position_monitor.py

The system is in RESEARCH_ONLY mode. Position exits and close orders are
permanently disabled. Re-enabling requires deliberate code restoration from
the archive.
"""
from __future__ import annotations
import logging
from typing import Dict, List

from core.decision_logger import DecisionLogger
from core.research_mode import ResearchOnlyModeError

logger = logging.getLogger(__name__)

_DISABLED_MSG = (
    "RESEARCH_ONLY_MODE: position monitoring and broker exit orders are "
    "permanently disabled. See archive/execution_disabled/position_monitor.py."
)


class PositionMonitor:
    def __init__(self, decision_logger: DecisionLogger):
        self._log = decision_logger
        logger.warning(_DISABLED_MSG)

    def check_exits(self) -> List[Dict]:
        raise ResearchOnlyModeError(_DISABLED_MSG)

    def portfolio_state(self) -> Dict:
        raise ResearchOnlyModeError(_DISABLED_MSG)

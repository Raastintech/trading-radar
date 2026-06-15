"""
execution/order_manager.py — PERMANENTLY DISABLED (Phase 3A, 2026-06-13)

Archived for reproducibility only. Original preserved at:
  archive/execution_disabled/order_manager.py

The system is in RESEARCH_ONLY mode. Any call to OrderManager.execute()
raises ResearchOnlyModeError. Re-enabling requires deliberate code
restoration from the archive — a config flag flip alone is not sufficient.
"""
from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional

from core.research_mode import ResearchOnlyModeError
from core.decision_logger import DecisionLogger

logger = logging.getLogger(__name__)

_DISABLED_MSG = (
    "RESEARCH_ONLY_MODE: broker execution is permanently disabled. "
    "OrderManager.execute() will never place an order. "
    "See archive/execution_disabled/order_manager.py to restore."
)


class OrderManager:
    def __init__(
        self,
        decision_logger: DecisionLogger,
        *,
        circuit_breakers: Any = None,
        portfolio_risk: Any = None,
    ) -> None:
        self._log = decision_logger
        logger.warning(_DISABLED_MSG)

    def execute(
        self,
        signal: Dict,
        council_result: Dict,
        *,
        portfolio_state: Optional[Dict] = None,
        open_positions: Optional[List[Dict]] = None,
        open_orders: Optional[List[Dict]] = None,
        equity: Optional[float] = None,
    ) -> Optional[Dict]:
        raise ResearchOnlyModeError(_DISABLED_MSG)

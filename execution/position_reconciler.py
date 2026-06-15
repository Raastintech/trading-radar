"""
execution/position_reconciler.py — PERMANENTLY DISABLED (Phase 3A, 2026-06-13)

Archived for reproducibility only. Original preserved at:
  archive/execution_disabled/position_reconciler.py

Broker reconciliation requires an active Alpaca connection. In RESEARCH_ONLY
mode there is no broker state to reconcile. reconcile_and_audit() raises
ResearchOnlyModeError on any call. Re-enabling requires deliberate code
restoration from the archive.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.research_mode import ResearchOnlyModeError

logger = logging.getLogger(__name__)

_DISABLED_MSG = (
    "RESEARCH_ONLY_MODE: broker reconciliation is permanently disabled. "
    "See archive/execution_disabled/position_reconciler.py."
)

# Stubs kept so importers that reference these types don't break at import time.
QTY_TOLERANCE: float = 0.5
PRICE_DRIFT_BPS: float = 50.0


@dataclass
class DriftRecord:
    category: str = ""
    ticker: str = ""
    detail: str = ""


@dataclass
class ReconcileReport:
    drifts: List[DriftRecord] = field(default_factory=list)
    broker_call_ok: bool = False
    skipped_reason: str = _DISABLED_MSG
    broker_only: List[str] = field(default_factory=list)
    decisions_only: List[str] = field(default_factory=list)
    qty_mismatch: List[str] = field(default_factory=list)
    price_drift: List[str] = field(default_factory=list)
    hard_drift: bool = False


def reconcile_and_audit(
    *,
    alpaca: Any = None,
    decision_logger: Any = None,
    circuit_breakers: Any = None,
    halt_on_drift: bool = True,
) -> ReconcileReport:
    raise ResearchOnlyModeError(_DISABLED_MSG)

"""
core/research_mode.py — Research-only mode constants and enforcement error.

Phase 3A (2026-06-13): The gem-trader system has been permanently converted
to a research-only market intelligence engine. Auto-trading, broker execution,
paper-trade routing, and Alpaca dependency are all disabled.

This module is the single source of truth for mode flags. All execution paths
that previously placed orders or routed strategies must raise ResearchOnlyModeError
instead. Re-enabling execution requires deliberate code restoration from
archive/execution_disabled/ — a flag flip alone is not enough.
"""
from __future__ import annotations

# ── Mode declaration ─────────────────────────────────────────────────────────
SYSTEM_MODE = "RESEARCH_ONLY"

LIVE_TRADING_ENABLED       = False
PAPER_TRADING_ENABLED      = False
BROKER_EXECUTION_ENABLED   = False
STRATEGY_PROMOTION_ENABLED = False
AUTO_ORDER_ROUTING_ENABLED = False

ALPACA_REQUIRED = False
ALPACA_ACTIVE   = False

TRADIER_RESEARCH_ENABLED  = True
TRADIER_EXECUTION_ENABLED = False   # Tradier is kept for research data only

FMP_RESEARCH_ENABLED = True

# ── Console banner ───────────────────────────────────────────────────────────
_BANNER_RULE = "=" * 70
RESEARCH_ONLY_BANNER = (
    _BANNER_RULE + "\n"
    "  RESEARCH_ONLY_MODE — NO AUTO TRADING — HUMAN REVIEW ONLY\n"
    "  Broker execution, paper-trade routing, and Alpaca are disabled.\n"
    "  FMP + Tradier (research) remain active.\n"
    + _BANNER_RULE
)


# ── Enforcement error ────────────────────────────────────────────────────────
class ResearchOnlyModeError(RuntimeError):
    """
    Raised when any execution path is called in research-only mode.

    All order placement, position closing, paper-signal routing, strategy
    promotion, and broker-state mutation functions raise this error. The
    system is permanently in RESEARCH_ONLY mode. To restore execution,
    manually recover the original files from archive/execution_disabled/.
    """
    pass

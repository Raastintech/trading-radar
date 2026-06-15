"""
main.py — Research-only system health check (Phase 3A, 2026-06-13)

The gem-trader auto-trading daemon has been decommissioned. This entry point
now runs the research-stack startup checks and reports system health.

Exit codes:
  0 — OK or DEGRADED (research can proceed; DEGRADED means some optional
       checks failed, e.g. price cache missing, Tradier not configured)
  1 — HALTED (critical check failed; e.g. FMP key invalid, DB unwritable)

The original trading daemon is preserved at:
  archive/execution_disabled/main.py

Research commands:
  ./scripts/run_research_cycle.sh <command>

See docs/research/PHASE_3A_DECOMMISSION_LOG.md for the full decision record.
"""
from __future__ import annotations
import logging
import sys
from pathlib import Path

# ── Bootstrap credentials (same pattern as every other script) ────────────────
import os
_cred = os.environ.get("SNIPER_ENV_PATH", "/home/gem/secure/trading.env")
if Path(_cred).exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_cred, override=True)
    except ImportError:
        pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
_logger = logging.getLogger("main")

# ── Module-level constants (imported by heartbeat tests and other callers) ─────
DECOMMISSIONED = True
SYSTEM_MODE = "RESEARCH_ONLY"


def main() -> int:
    from core.research_mode import RESEARCH_ONLY_BANNER
    print(RESEARCH_ONLY_BANNER)
    print()

    try:
        from core.startup_checks import run_startup_checks
        state = run_startup_checks()
    except Exception as exc:
        _logger.error("startup_checks import failed: %s", exc)
        print("ERROR: Could not run startup checks — check FMP_API_KEY is set.")
        return 1

    if state.halted:
        print()
        print("SYSTEM HALTED — one or more critical checks failed.")
        print("Fix the issues above, then re-run.")
        return 1

    if state.degraded:
        print()
        reasons = ", ".join(state.degraded_reasons)
        print(f"SYSTEM DEGRADED — non-critical checks failed: {reasons}")
        print("Research continues; run ./scripts/nightly_refresh.py to repair cache.")
    else:
        print()
        print("SYSTEM OK — all research-stack checks passed.")

    print()
    print("Research commands:")
    print("  ./scripts/run_research_cycle.sh nightly")
    print("  ./scripts/run_research_cycle.sh premarket")
    print("  ./scripts/run_research_cycle.sh mcp-audit-session")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

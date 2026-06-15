"""
tests/unit/test_outcome_units_convention.py

Pins the units convention for tactical-outcome return values so a future
refactor can't silently swap PERCENT ↔ FRACTION and contaminate the
validation scoreboard.

Convention:
  - paper_signal_outcomes.return_pct  → PERCENT  (e.g. 14.19 == +14.19%)
  - decisions.pnl_pct                 → FRACTION (e.g. 0.1419 == +14.19%)

A +1% move on a $100 entry must produce return_pct=1.00 (not 0.01,
not 100.0). The 2026-05-15 weekly audit double-displayed return_pct
because the convention wasn't pinned anywhere readers could find it.
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from research.paper_trades.resolve_tactical_outcomes import _raw_return


class TestRawReturnUnits:

    def test_long_one_percent_move_returns_one(self):
        """+1% LONG: entry 100, exit 101 → return_pct == 1.00."""
        v = _raw_return("LONG", 100.0, 101.0)
        assert math.isclose(v, 1.0, rel_tol=1e-9), (
            f"LONG +1% should be 1.00 (PERCENT), got {v}. "
            "If you changed _raw_return to return a fraction, also fix "
            "the paper_signal_outcomes UNITS NOTE and every downstream "
            "consumer."
        )

    def test_short_one_percent_drop_returns_one(self):
        """+1% SHORT (price drops 1%): entry 100, exit 99 → +1.00."""
        v = _raw_return("SHORT", 100.0, 99.0)
        assert math.isclose(v, 1.0, rel_tol=1e-9), v

    def test_long_panw_real_example(self):
        """PANW LONG 196.53 → 224.55 (the example that broke the audit).
        Must produce ~14.19, NOT 0.1419 and NOT 1418.61."""
        v = _raw_return("LONG", 196.53, 224.55)
        assert 14.0 < v < 14.5, (
            f"PANW LONG 196.53→224.55 should be ~14.19 (PERCENT), got {v}. "
            "If this fails the resolver scale is wrong; downstream reports "
            "and the validation scoreboard will be off by 100x."
        )

    def test_short_insm_real_example(self):
        """INSM SHORT 101.35 → 111.50: a 10% adverse move. Must be ~-10."""
        v = _raw_return("SHORT", 101.35, 111.50)
        assert -10.5 < v < -9.9, (
            f"INSM SHORT 101.35→111.50 should be ~-10 (PERCENT), got {v}"
        )

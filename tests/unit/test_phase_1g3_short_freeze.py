"""
tests/unit/test_phase_1g3_short_freeze.py — Phase 1G.3 T1

SHORT_A is frozen to research-only. Asserts:
  - SHORT is FROZEN, not active_paper.
  - The 'short' scanner is no longer in the active scanner set, so main.py
    will not run it (no new paper signals can be emitted).
  - paper_governance.evaluate_paper_signal rejects a SHORT signal.
  - Historical resolution is preserved: SHORT stays a paper-ledger sleeve with
    its tactical horizons/tag, so already-logged SHORT rows keep maturing.
  - Other active sleeves are unaffected.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
from core import strategy_registry as reg
from core.research_mode import ResearchOnlyModeError
from execution import paper_governance as pg


def test_short_is_frozen_not_active():
    assert reg.is_frozen_strategy("SHORT") is True
    assert reg.is_active_paper_strategy("SHORT") is False
    assert reg.is_active_paper_strategy("SHORT_A") is False  # alias
    assert "SHORT" not in reg.active_paper_strategies()
    assert "SHORT" in reg.frozen_strategies()


def test_short_scanner_not_active():
    # No active scanners route signals to execution — all raise ResearchOnlyModeError.
    assert "short" not in reg.active_scanner_keys()


def test_paper_governance_raises_for_any_signal():
    # Phase 3A: paper governance is permanently disabled.
    # SHORT signals (and all signals) raise ResearchOnlyModeError — a stronger
    # guarantee than the former approved=False return.
    with pytest.raises(ResearchOnlyModeError):
        pg.evaluate_paper_signal(
            {"strategy": "SHORT", "ticker": "CHTR", "sector": "Communication Services"},
            open_paper_positions=[],
        )


def test_historical_resolution_preserved():
    # Frozen-but-paper-ledger SHORT must still resolve existing rows.
    assert "SHORT" in reg.paper_ledger_strategies()
    assert reg.paper_ledger_tags().get("SHORT") == "SHORT_A"
    assert reg.paper_ledger_horizons().get("SHORT") == [3, 5, 10]


def test_decommissioned_sleeves_not_active():
    # Phase 5: SNIPER and VOYAGER decommissioned (2026-06-14); no longer active_paper.
    assert reg.is_active_paper_strategy("SNIPER") is False
    assert reg.is_active_paper_strategy("VOYAGER") is False
    assert reg.is_frozen_strategy("SNIPER") is True
    assert reg.is_frozen_strategy("VOYAGER") is True
    assert "SNIPER" in reg.decommissioned_strategies()
    assert "VOYAGER" in reg.decommissioned_strategies()
    # Scanner keys empty — no active sleeves
    assert "sniper" not in reg.active_scanner_keys()
    assert "voyager" not in reg.active_scanner_keys()
    # Paper ledger preserved for SNIPER (historical resolution)
    assert "SNIPER" in reg.paper_ledger_strategies()

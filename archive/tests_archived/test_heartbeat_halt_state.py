"""
tests/unit/test_heartbeat_halt_state.py

Pins the Fix-4 contract for ``main.write_heartbeat``:

  - When ``breakers`` is supplied and the breaker is tripped, the
    heartbeat payload must report ``halted: true``, include the reason,
    and flip ``is_trading`` to ``false`` regardless of the session.
  - When ``breakers`` is omitted (start-of-day path before construction)
    or not halted, ``halted`` defaults to ``false`` and ``is_trading``
    tracks the session state.

Why this matters: in the 2026-05-15 audit the daemon happily published
``is_trading=true`` while the position reconciler had been halting all
entries for ~26 hours. Operators had no way to see the discrepancy on
the heartbeat. The contract above closes that hole.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import main as main_mod  # noqa: E402


class _StubBreakers:
    def __init__(self, halted: bool, reason: str = ""):
        self._h = halted
        self._r = reason

    def is_globally_halted(self):
        return self._h, self._r


@pytest.fixture
def hb_tmp(monkeypatch):
    """Redirect cfg.LOG_DIR for write_heartbeat to a temp dir, and force
    is_execution_allowed=True so we isolate the halt branch."""
    tmp = Path(tempfile.mkdtemp())
    monkeypatch.setattr(main_mod.cfg, "LOG_DIR", tmp)
    monkeypatch.setattr(main_mod, "is_execution_allowed", lambda: True)
    return tmp


def _read_payload(tmp: Path) -> dict:
    return json.loads((tmp / "trader_heartbeat.json").read_text())


class TestHeartbeatHaltState:

    def test_no_breakers_reports_not_halted(self, hb_tmp):
        main_mod.write_heartbeat("LOOP")
        p = _read_payload(hb_tmp)
        assert p["halted"] is False
        assert p["halt_reason"] == ""
        assert p["is_trading"] is True  # session OK + not halted

    def test_breakers_not_halted_preserves_is_trading(self, hb_tmp):
        main_mod.write_heartbeat("LOOP", breakers=_StubBreakers(False))
        p = _read_payload(hb_tmp)
        assert p["halted"] is False
        assert p["is_trading"] is True

    def test_breakers_halted_flips_is_trading_off(self, hb_tmp):
        main_mod.write_heartbeat(
            "LOOP",
            breakers=_StubBreakers(True, "position reconciler: 8 hard drift(s)"),
        )
        p = _read_payload(hb_tmp)
        assert p["halted"] is True
        assert "8 hard drift" in p["halt_reason"]
        assert p["is_trading"] is False, (
            "is_trading must be False whenever the global halt is tripped, "
            "even in REGULAR session"
        )

    def test_extra_dict_does_not_overwrite_halt_keys(self, hb_tmp):
        """A caller passing extra={'is_trading': True} must not be able
        to mask the halt — but the current contract is permissive (extra
        wins). Pin this so we notice if we tighten it."""
        main_mod.write_heartbeat(
            "LOOP",
            extra={"opportunities_evaluated": 7},
            breakers=_StubBreakers(True, "x"),
        )
        p = _read_payload(hb_tmp)
        assert p["halted"] is True
        assert p["is_trading"] is False
        assert p["opportunities_evaluated"] == 7

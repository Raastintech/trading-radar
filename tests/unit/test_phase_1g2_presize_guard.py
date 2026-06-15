"""
tests/unit/test_phase_1g2_presize_guard.py — Phase 1G.2 T2

Tests for ``core.signal_hygiene.compute_presize_verdict``. The verdict
is diagnostic-only and never lets an oversized trade through. Missing
equity hint blocks conservatively.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from core import signal_hygiene as sh


def test_oversized_signal_is_blocked():
    opp = {"shares": 100, "entry_price": 313.04}
    v = sh.compute_presize_verdict(opp, equity=82_000.0, equity_source="heartbeat")
    # cap = 82000 * 0.05 = 4100; proposed = 31304 — well over.
    assert v.ok is False
    assert v.size_ratio is not None and v.size_ratio > 1.0
    assert v.proposed_notional == pytest.approx(31_304.0)
    assert v.cap_notional == pytest.approx(4_100.0)
    assert "exceeds single-name cap" in v.reason


def test_valid_signal_passes():
    opp = {"shares": 10, "entry_price": 313.04}  # $3130 ≈ inside $4100 cap
    v = sh.compute_presize_verdict(opp, equity=82_000.0, equity_source="heartbeat")
    assert v.ok is True
    assert v.size_ratio is not None and v.size_ratio < 1.0
    assert v.reason == ""


def test_missing_equity_blocks_conservatively():
    opp = {"shares": 100, "entry_price": 50.0}
    v = sh.compute_presize_verdict(opp, equity=None, equity_source="unavailable")
    assert v.ok is False
    assert "equity hint unavailable" in v.reason
    assert v.proposed_notional == pytest.approx(5_000.0)
    assert v.cap_notional is None


def test_missing_shares_or_entry_passes_through():
    # Scanner hasn't sized yet — let governance handle it.
    opp = {"entry_price": 100.0}  # no shares
    v = sh.compute_presize_verdict(opp, equity=100_000.0)
    assert v.ok is True
    assert v.proposed_notional is None
    assert "pre-size check skipped" in v.reason


def test_zero_equity_blocks_conservatively():
    opp = {"shares": 10, "entry_price": 50.0}
    v = sh.compute_presize_verdict(opp, equity=0.0)
    assert v.ok is False


def test_alternate_cap_pct():
    opp = {"shares": 50, "entry_price": 100.0}  # $5000
    # 10% cap on $100k = $10k → 5k passes
    v_pass = sh.compute_presize_verdict(opp, equity=100_000.0, max_single_name_pct=0.10)
    assert v_pass.ok is True
    # 1% cap on $100k = $1k → 5k blocks
    v_block = sh.compute_presize_verdict(opp, equity=100_000.0, max_single_name_pct=0.01)
    assert v_block.ok is False


def test_read_equity_hint_reads_heartbeat(tmp_path: Path):
    hb = tmp_path / "trader_heartbeat.json"
    hb.write_text(json.dumps({"equity": 82500.0, "halted": False}))
    eq, src = sh.read_equity_hint(heartbeat_path=hb, broker_snapshot_path=tmp_path / "missing.json")
    assert eq == pytest.approx(82500.0)
    assert src == "heartbeat"


def test_read_equity_hint_returns_none_when_no_inputs(tmp_path: Path):
    eq, src = sh.read_equity_hint(
        heartbeat_path=tmp_path / "missing_hb.json",
        broker_snapshot_path=tmp_path / "missing_bs.json",
    )
    assert eq is None
    assert src == "unavailable"


def test_presize_counters_record_size_ratio():
    counters = sh.HygieneCounters()
    v = sh.compute_presize_verdict(
        {"shares": 100, "entry_price": 313.04}, equity=82_000.0, equity_source="heartbeat",
    )
    counters.record_presize("SHORT", "INTU", v)
    snap = counters.snapshot()
    assert snap["presize_rejected"]["count"] == 1
    assert snap["presize_rejected"]["by_ticker"]["INTU"] == 1
    assert snap["presize_rejected"]["latest_size_ratio"] is not None
    assert snap["presize_rejected"]["latest_proposed_notional"] == pytest.approx(31_304.0)

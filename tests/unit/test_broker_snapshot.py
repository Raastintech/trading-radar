"""
tests/unit/test_broker_snapshot.py

Pins the Fix-7 contract: core.broker_snapshot.write_snapshot writes an
atomic JSON sidecar in the shape Phase 1E's hygiene-report enricher
expects (ticker uppercased, side lowercased, qty as float).

Why: the 2026-05-15 audit caught a 3-day-stale snapshot because no
process owned the refresh. The daemon now tees a write on each
reconcile cycle — verify the writer doesn't drift in shape.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from core import broker_snapshot as bs  # noqa: E402


def test_normalize_position_uppercases_ticker_and_floats_qty():
    raw = {
        "ticker": "sbac",
        "qty": "42",
        "side": "LONG",
        "entry_price": 217.55,
        "current_price": 215.50,
        "market_value": 9051.0,
        "unrealized_pnl": -86.1,
    }
    out = bs.normalize_position(raw)
    assert out["ticker"] == "SBAC"
    assert out["qty"] == 42.0
    assert out["side"] == "long"
    assert out["entry_price"] == 217.55


def test_write_snapshot_atomic_and_round_trip(tmp_path):
    path = tmp_path / "state" / "snapshot.json"
    positions = [
        {"ticker": "sbac", "qty": 42, "side": "long",
         "entry_price": 217.55, "current_price": 215.5,
         "market_value": 9051.0, "unrealized_pnl": -86.1},
        {"ticker": "crk", "qty": -234, "side": "short",
         "entry_price": 14.65, "current_price": 14.95,
         "market_value": -3498.3, "unrealized_pnl": -70.2},
    ]
    payload = bs.write_snapshot(positions, path)
    assert path.exists()
    on_disk = json.loads(path.read_text())
    assert on_disk == payload
    assert on_disk["source"] == "alpaca.get_positions"
    assert on_disk["count"] == 2
    tickers = [p["ticker"] for p in on_disk["positions"]]
    assert tickers == ["SBAC", "CRK"]
    sides = {p["side"] for p in on_disk["positions"]}
    assert sides == {"long", "short"}


def test_write_snapshot_no_temp_file_left_behind(tmp_path):
    path = tmp_path / "snapshot.json"
    bs.write_snapshot([], path)
    tmp_artifact = path.with_suffix(path.suffix + ".tmp")
    assert not tmp_artifact.exists(), (
        "atomic rename must clean up the .tmp file"
    )

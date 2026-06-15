"""
tests/unit/test_phase_1g3_forward_resolution_health.py — Phase 1G.3 T4

Unit tests for the forward-resolution health classifier:
  - a mature snapshot (old enough + final-horizon return present) counts matured
  - an immature snapshot stays not-mature-yet
  - an old snapshot missing its final-horizon price counts missing_price
  - an unparseable anchor counts as an error row, not a crash
  - next_maturity_due is computed from the oldest open anchor
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from research import forward_resolution_health as frh

HORIZONS = [1, 5, 10, 20]


def _final_key(h: int) -> str:
    return f"spy_{h}d_return_pct"


def _classify(rows, today):
    return frh._classify_log(rows, HORIZONS, lambda h: _final_key(h), today)


def test_mature_snapshot_counts_matured():
    rows = [{
        "anchor_date": "2026-04-01",
        "status": "matured",
        "outcomes": {_final_key(20): 3.1, _final_key(10): 1.2},
    }]
    out = _classify(rows, date(2026, 5, 24))
    assert out["matured"] == 1
    assert out["open"] == 0


def test_immature_snapshot_stays_open_not_mature_yet():
    # 10 days old, 20d horizon needs 31 calendar days -> too young.
    rows = [{"anchor_date": "2026-05-14", "status": "open", "outcomes": {_final_key(5): 0.4}}]
    out = _classify(rows, date(2026, 5, 24))
    assert out["open"] == 1
    assert out["unresolved_not_mature_yet"] == 1
    assert out["unresolved_missing_price"] == 0


def test_old_but_missing_final_price_counts_missing_price():
    # 60 days old (well past 31) but no 20d return present -> price gap.
    rows = [{"anchor_date": "2026-03-25", "status": "open", "outcomes": {_final_key(5): 0.4}}]
    out = _classify(rows, date(2026, 5, 24))
    assert out["unresolved_missing_price"] == 1
    assert out["unresolved_not_mature_yet"] == 0


def test_unparseable_anchor_counts_error_not_crash():
    rows = [{"anchor_date": None, "status": "open", "outcomes": {}},
            {"anchor_date": "not-a-date", "status": "open", "outcomes": {}}]
    out = _classify(rows, date(2026, 5, 24))
    assert out["unresolved_error"] == 2


def test_next_maturity_due_from_oldest_open_anchor():
    rows = [
        {"anchor_date": "2026-05-14", "status": "open", "outcomes": {}},
        {"anchor_date": "2026-05-10", "status": "open", "outcomes": {}},
    ]
    out = _classify(rows, date(2026, 5, 24))
    # oldest open anchor 2026-05-10 + 31 calendar days = 2026-06-10
    assert out["oldest_open_anchor"] == "2026-05-10"
    assert out["next_maturity_due"] == "2026-06-10"


def test_per_horizon_fill_counts():
    rows = [
        {"anchor_date": "2026-05-14", "status": "open",
         "outcomes": {_final_key(1): 0.1, _final_key(5): 0.4}},
        {"anchor_date": "2026-05-13", "status": "open",
         "outcomes": {_final_key(1): 0.2}},
    ]
    out = _classify(rows, date(2026, 5, 24))
    assert out["per_horizon_filled"]["1d"] == 2
    assert out["per_horizon_filled"]["5d"] == 1
    assert out["per_horizon_filled"]["20d"] == 0

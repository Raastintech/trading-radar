"""Tests for the prospective scanner-recall cohort lane (P0 completion).

Covers: registration-row construction, ledger idempotency per date,
forward resolution + recall math, the pre-registered verdict ladder,
read-only behavior, and import hygiene.  All synthetic — no providers,
no production files.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pandas as pd
import pytest

from research import scanner_recall_cohorts as src


def _world():
    return {
        "__meta__": {"excluded_stale": 2, "excluded_data_suspect": 1},
        "AAA": {"strict_pass": True, "loose_pass": True,
                "rs20": 0.10, "r40": 0.2},
        "BBB": {"strict_pass": False, "loose_pass": True,
                "rs20": 0.05, "r40": 0.1},
        "CCC": {"strict_pass": False, "loose_pass": False,
                "rs20": 0.20, "r40": 0.3},
        "DDD": {"strict_pass": False, "loose_pass": False,
                "rs20": -0.10, "r40": -0.2},  # negative r40: no RS baseline
    }


def test_registration_row_cohorts():
    row = src.build_registration_row(_world(), ["AAA", "ZZZ"], "2026-07-09")
    assert row["n_liquid"] == 4
    assert row["cohorts"]["strict_mirror"] == ["AAA"]
    assert row["cohorts"]["loose_scanner"] == ["AAA", "BBB"]
    # watchlist intersected with the liquid world (ZZZ not liquid)
    assert row["cohorts"]["scanner_watchlist"] == ["AAA"]
    # rs_baseline requires positive r40 (DDD excluded), ranked by rs20
    assert "DDD" not in row["cohorts"]["rs_baseline"]
    assert "CCC" in row["cohorts"]["rs_baseline"]
    # random control is seeded by date -> deterministic
    row2 = src.build_registration_row(_world(), ["AAA"], "2026-07-09")
    assert row["cohorts"]["random_control"] == \
        row2["cohorts"]["random_control"]
    assert row["meta"]["excluded_stale"] == 2


def test_ledger_append_idempotent_per_date(tmp_path):
    row = src.build_registration_row(_world(), [], "2026-07-09")
    assert src.append_registration(row, tmp_path) is True
    assert src.append_registration(row, tmp_path) is False  # same date no-op
    rows = src.load_ledger(tmp_path)
    assert len(rows) == 1 and rows[0]["date"] == "2026-07-09"


# ── forward resolution ───────────────────────────────────────────────────────


def _loader(series_map):
    def _load(t):
        return series_map.get(t)
    return _load


def _series(start_price, daily_pct, n=40, start="2026-06-01"):
    idx = pd.bdate_range(start, periods=n)
    vals = [start_price * (1 + daily_pct) ** i for i in range(n)]
    return pd.Series(vals, index=idx)


def test_report_resolves_forward_and_recall():
    idx = pd.bdate_range("2026-06-01", periods=40)
    date = str(idx[5].date())  # registration lands exactly on a bar
    series = {
        "SPY": _series(100, 0.000),
        "WIN": _series(10, 0.01),    # ~+22% in 20 bars -> forward winner
        "LOS": _series(10, -0.01),
    }
    rows = [{
        "date": date, "n_liquid": 2, "liquid": ["WIN", "LOS"],
        "cohorts": {
            "strict_mirror": ["LOS"],
            "scanner_watchlist": [],
            "rs_baseline": ["WIN"],
            "loose_scanner": ["WIN", "LOS"],
            "random_control": ["LOS"],
        },
    }]
    report = src.build_report(rows, _loader(series))
    assert report["matured_dates"] == 1
    loose = report["cohorts"]["loose_scanner"]
    strict = report["cohorts"]["strict_mirror"]
    assert loose["winner_recall_pct"] == 100.0   # caught WIN
    assert strict["winner_recall_pct"] == 0.0    # missed WIN
    assert loose["matured_ticker_days_20d"] == 2
    h20 = loose["horizons"]["20d"]
    assert h20["n"] == 2
    assert h20["mean_excess_vs_spy_pct"] is not None
    assert report["verdict"] == "NEED_MORE_DATA"  # 1 date < 15 floor


def test_report_skips_unmatured_dates():
    idx = pd.bdate_range("2026-06-01", periods=40)
    late = str(idx[-3].date())  # only 2 bars of forward data -> not matured
    series = {"SPY": _series(100, 0.0), "WIN": _series(10, 0.01)}
    rows = [{"date": late, "liquid": ["WIN"],
             "cohorts": {n: ["WIN"] for n in src.COHORT_NAMES}}]
    report = src.build_report(rows, _loader(series))
    assert report["registered_dates"] == 1
    assert report["matured_dates"] == 0
    assert report["verdict"] == "NEED_MORE_DATA"


# ── verdict ladder (pre-registered) ──────────────────────────────────────────


def _cohorts(l_recall, s_recall, l_ex, s_ex, days=400):
    return {
        "loose_scanner": {
            "winner_recall_pct": l_recall, "winner_precision_pct": 20.0,
            "matured_ticker_days_20d": days,
            "horizons": {"20d": {"mean_excess_vs_spy_pct": l_ex}}},
        "strict_mirror": {
            "winner_recall_pct": s_recall, "winner_precision_pct": 30.0,
            "matured_ticker_days_20d": 100,
            "horizons": {"20d": {"mean_excess_vs_spy_pct": s_ex}}},
    }


def test_verdict_need_more_data_below_floors():
    v, _ = src._verdict(_cohorts(30, 5, 2.0, 1.0, days=100), 20, 80.0)
    assert v == "NEED_MORE_DATA"  # ticker-days below floor
    v, _ = src._verdict(_cohorts(30, 5, 2.0, 1.0), 10, 80.0)
    assert v == "NEED_MORE_DATA"  # dates below floor


def test_verdict_proposal_allowed_only_when_all_gates_pass():
    v, reason = src._verdict(_cohorts(30, 5, 2.0, 1.0), 20, 80.0)
    assert v == "LOOSE_PROMISING_PROPOSAL_ALLOWED"
    assert "human review" in reason


def test_verdict_loose_not_better_on_any_gate_failure():
    # fails recall factor (30 < 2x20)
    v, _ = src._verdict(_cohorts(30, 20, 2.0, 1.0), 20, 80.0)
    assert v == "LOOSE_NOT_BETTER"
    # fails positive excess
    v, _ = src._verdict(_cohorts(30, 5, -0.5, -1.0), 20, 80.0)
    assert v == "LOOSE_NOT_BETTER"
    # fails beats-random
    v, _ = src._verdict(_cohorts(30, 5, 2.0, 1.0), 20, 40.0)
    assert v == "LOOSE_NOT_BETTER"
    # fails excess >= strict
    v, _ = src._verdict(_cohorts(30, 5, 1.0, 3.0), 20, 80.0)
    assert v == "LOOSE_NOT_BETTER"


# ── hygiene ──────────────────────────────────────────────────────────────────


def test_report_is_read_only(tmp_path):
    row = src.build_registration_row(_world(), [], "2026-07-09")
    src.append_registration(row, tmp_path)
    ledger = tmp_path / src.LEDGER_REL
    before = ledger.read_bytes()
    src.build_report(src.load_ledger(tmp_path), _loader({}))
    assert ledger.read_bytes() == before


def test_no_broker_or_execution_imports():
    source = Path(src.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    forbidden = ("alpaca", "tradier", "execution", "broker", "core.config",
                 "core.alpaca", "order", "strategies", "council",
                 "requests", "anthropic")
    for mod in imported:
        assert not any(mod.startswith(f) or f in mod for f in forbidden), \
            f"forbidden import in scanner_recall_cohorts: {mod}"

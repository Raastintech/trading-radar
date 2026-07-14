"""Tests for the quarantine cause report (P1 data-quality task).

Pure-classification tests plus a synthetic-root build: every quarantined
ticker gets a cause and a clearance condition; nothing but the report's
own sidecar/log is written; no broker/execution imports.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pandas as pd

from research import quarantine_cause_report as qcr

TODAY = "2026-07-10"


def test_repaired_when_bars_meet_floor():
    rec = qcr.classify("AAA", 468, "2026-07-09", None, today=TODAY)
    assert rec["cause"] == "REPAIRED"
    assert "nightly scanner" in rec["clearance"]


def test_young_listing_when_backfill_exhausted_source():
    rec = qcr.classify("BBB", 144, "2026-07-09",
                       {"action": "backfilled", "after": 144}, today=TODAY)
    assert rec["cause"] == "YOUNG_LISTING"
    assert "156 trading days" in rec["clearance"]  # 300 - 144
    assert "2027-02" in rec["clearance"]           # ~eta date


def test_young_listing_when_backfiller_skips_exhausted_source():
    """A later run where the backfiller itself skips the provider call
    (skip_source_exhausted) is the same proof of exhaustion as a
    zero-gain fetch — the ticker must not regress to BACKFILL_PENDING."""
    rec = qcr.classify("BBB", 144, "2026-07-09",
                       {"action": "skip_source_exhausted"}, today=TODAY)
    assert rec["cause"] == "YOUNG_LISTING"


def test_backfill_pending_when_no_attempt():
    rec = qcr.classify("CCC", 120, "2026-07-09", None, today=TODAY)
    assert rec["cause"] == "BACKFILL_PENDING"
    assert "targeted-backfill --execute" in rec["clearance"]


def test_stale_feed_beats_depth():
    rec = qcr.classify("DDD", 468, "2026-06-12", None, today=TODAY)
    assert rec["cause"] == "STALE_FEED"
    assert "delisting" in rec["reason"]
    missing = qcr.classify("EEE", None, None, None, today=TODAY)
    assert missing["cause"] == "STALE_FEED"


def test_build_report_covers_every_quarantined_ticker(tmp_path):
    research = tmp_path / "cache" / "research"
    research.mkdir(parents=True)
    (research / "daily_alpha_radar_latest.json").write_text(json.dumps({
        "generated_at": TODAY,
        "priority_tickers": {"DATA_QUARANTINE": ["AAA", "BBB"]},
    }), encoding="utf-8")
    (research / "targeted_price_backfill_latest.json").write_text(
        json.dumps({"plan": [
            {"ticker": "BBB", "action": "backfilled", "after": 100},
        ]}), encoding="utf-8")
    prices = tmp_path / "cache" / "prices"
    prices.mkdir(parents=True)
    idx = pd.bdate_range(end=TODAY, periods=400)
    pd.DataFrame({"close": range(400)}, index=idx).to_parquet(
        prices / "AAA.parquet")
    pd.DataFrame({"close": range(100)}, index=idx[-100:]).to_parquet(
        prices / "BBB.parquet")

    before = {p: p.stat().st_mtime for p in tmp_path.rglob("*.parquet")}
    report = qcr.build_report(tmp_path)
    assert report["n_quarantined"] == 2
    by_ticker = {r["ticker"]: r for r in report["tickers"]}
    assert by_ticker["AAA"]["cause"] == "REPAIRED"
    assert by_ticker["BBB"]["cause"] == "YOUNG_LISTING"
    # every ticker has both fields (the success metric)
    for r in report["tickers"]:
        assert r["reason"] and r["clearance"]
    # read-only on inputs
    assert before == {p: p.stat().st_mtime
                      for p in tmp_path.rglob("*.parquet")}


def test_no_broker_or_execution_imports():
    source = Path(qcr.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    forbidden = ("alpaca", "tradier", "execution", "broker", "core.config",
                 "order", "strategies", "council", "requests", "anthropic")
    for mod in imported:
        assert not any(mod.startswith(f) or f in mod for f in forbidden), \
            f"forbidden import: {mod}"

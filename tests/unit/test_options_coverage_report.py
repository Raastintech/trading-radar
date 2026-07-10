"""Tests for the options coverage report (P2 options_overlay task)."""
from __future__ import annotations

import ast
import json
from pathlib import Path

from research import options_coverage_report as ocr


def test_classify_covered_and_uncovered():
    cov = ocr.classify_ticker("AAPL", {"AAPL", "SPY"}, "2026-07-09")
    assert cov["status"] == "COVERED"
    unc = ocr.classify_ticker("SDGR", {"AAPL"}, "2026-07-09")
    assert unc["status"] == "NOT_IN_COLLECTION_UNIVERSE"
    assert "design, not failure" in unc["reason"]
    assert unc["clearance"]
    none = ocr.classify_ticker("SDGR", set(), None)
    assert none["status"] == "NO_SNAPSHOT_DAY"


def test_every_consumer_has_requirement_flag():
    for c in ocr.CONSUMERS:
        assert c["requirement"] in ("REQUIRED", "OPTIONAL"), c
        assert c["note"]
    surfaces = {c["surface"] for c in ocr.CONSUMERS}
    # the doctrine-known consumers are all present
    assert {"alpha_discovery_overlay", "stock_lens_options_context",
            "options_regime_lens"} <= surfaces


def test_build_report_on_synthetic_root(tmp_path):
    research = tmp_path / "cache" / "research"
    research.mkdir(parents=True)
    (research / "research_scanner_latest.json").write_text(json.dumps({
        "watchlist": [{"ticker": "AAPL"}, {"ticker": "SDGR"},
                      {"ticker": "BLMN"}]}), encoding="utf-8")
    snap = tmp_path / "data" / "options_snapshots" / "2026-07-09"
    snap.mkdir(parents=True)
    (snap / "AAPL.parquet").write_bytes(b"x")

    report = ocr.build_report(tmp_path)
    assert report["watchlist_total"] == 3
    assert report["covered"] == 1 and report["uncovered"] == 2
    assert report["coverage_pct"] == 33.3
    assert report["overlay_state"] == "DISABLED"  # < 50%
    assert report["snapshot_date"] == "2026-07-09"
    # every uncovered ticker has a reason (the success metric)
    for r in report["uncovered_detail"]:
        assert r["reason"] and r["clearance"]
    assert report["consumers"]


def test_no_broker_or_execution_imports():
    source = Path(ocr.__file__).read_text(encoding="utf-8")
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
        assert not any(mod.startswith(f) or f in mod for f in forbidden), mod

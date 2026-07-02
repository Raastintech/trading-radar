"""Tests for the Research Command Center dashboard (Phase 1).

Covers the ten Phase-1 requirements: missing-artifact tolerance, correct
artifact reads, missing-return handling, research-only footer, forbidden
trading language, data-quality flags, Phase 4B blocked status, no artifact
mutation, no production writes, and no coupling to scanner scoring.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from dashboards.research_command_center.data_adapter import (
    ArtifactStore,
    BENCHMARK_MISSING,
    IMMATURE_FORWARD_EVIDENCE,
    INSUFFICIENT_HISTORY,
    MISSING_ARTIFACT,
    NO_DATA,
    RESEARCH_ONLY_FOOTER,
    STALE_PRICE,
    SUSPECT_FEED,
    build_leaderboard,
    build_status,
    build_ticker_detail,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

# Forbidden phrases are assembled from fragments so a plain-text grep of the
# test suite never matches them.
_FORBIDDEN = [
    "buy" + " now", "sell" + " now", "entry" + " price", "stop" + " loss",
    "target" + " price", "position" + " size", "trade" + " recommendation",
    "paper" + " signal", "live" + " signal", "auto" + " trade",
    "place" + " order", "submit" + " order", "trade" + " idea generator",
]


# ── fixtures ─────────────────────────────────────────────────────────────────


def _write_fixture_artifacts(root: Path, *, stale_price_date="2026-01-02",
                             verdict="MIXED") -> None:
    research = root / "cache" / "research"
    research.mkdir(parents=True)
    (root / "data" / "research").mkdir(parents=True)

    (research / "research_scanner_latest.json").write_text(json.dumps({
        "generated_at": "2026-07-02T17:00:00+00:00",
        "stale_price_skipped_count": 3,
        "stale_price_skipped_sample": ["OLDX"],
        "data_suspect_skipped_count": 2,
        "data_suspect_skipped_sample": ["BADF"],
        "watchlist": [
            {   # healthy row with matured forward evidence
                "ticker": "GOODCO", "company_name": "Good Co",
                "watchlist_label": "EARLY_ACCUMULATION",
                "category": "early_accumulation",
                "all_categories": ["early_accumulation"],
                "sector": "Technology", "company_sector_etf": "XLK",
                "latest_close": 42.5, "latest_price_date": "2026-07-01",
                "data_confidence": "HIGH", "research_score": 88.0,
                "rs_20d_vs_spy": 12.0, "rs_63d_vs_spy": 30.0,
                "bars_available": 320,
                "insufficient_history_for_ma200": False,
                "why_appeared": "Rising volume + improving RS",
            },
            {   # young listing, stale price, no forward evidence
                "ticker": "YOUNGY", "company_name": "Young Inc",
                "watchlist_label": "WATCH", "category": "catalyst_watch",
                "all_categories": ["catalyst_watch"],
                "sector": "Healthcare", "company_sector_etf": "XLV",
                "latest_close": 5.1, "latest_price_date": stale_price_date,
                "data_confidence": "LOW", "research_score": 55.0,
                "bars_available": 40,
                "insufficient_history_for_ma200": True,
                "why_appeared": "Upcoming earnings",
            },
            {   # suspect feed
                "ticker": "BADF", "company_name": "Bad Feed Corp",
                "watchlist_label": "RISKY", "category": "rs_momentum_leader",
                "all_categories": ["rs_momentum_leader"],
                "sector": "Industrials",
                "latest_close": 9.9, "latest_price_date": "2026-07-01",
                "data_confidence": "MEDIUM", "research_score": 60.0,
                "bars_available": 200,
                "insufficient_history_for_ma200": False,
                "why_appeared": "Strong RS vs SPY",
            },
        ],
    }))
    (research / "research_forward_latest.json").write_text(json.dumps({
        "generated_at": "2026-07-02T17:00:00+00:00",
        "overall": {"verdict": verdict, "matured_5d_entries": 464,
                    "matured_entries": 134, "sample_status": "ROBUST"},
    }))
    (research / "daily_alpha_radar_latest.json").write_text(json.dumps({
        "generated_at": "2026-07-02T17:00:00+00:00",
        "priority_tickers": [["HIGH_PRIORITY_RESEARCH", ["GOODCO"]]],
        "quarantine_breakdown": {"INSUFFICIENT_HISTORY": 3, "DATA_QUARANTINE": 3},
    }))
    (research / "nightly_operator_summary_latest.json").write_text(json.dumps({
        "generated_at": "2026-07-02T17:00:00+00:00",
        "overall_status": {"overall": "PASS", "nightly_completed": True},
        "market_context": {"regime": "Chop / Range"},
        "forward_evidence": {"benchmark_readiness": "READY", "verdict": verdict},
        "warnings": ["Scanner recall low"],
    }))
    (research / "research_universe_build_latest.json").write_text(json.dumps({
        "universe_full": [
            {"ticker": "GOODCO", "source": "ranked_fill"},
            {"ticker": "YOUNGY", "source": "social_arb"},
        ],
    }))
    (root / "data" / "research" / "research_watchlist_history.jsonl").write_text(
        json.dumps({
            "ticker": "GOODCO", "appearance_date": "2026-06-20",
            "ret_5d": 3.2, "ret_10d": -1.1, "ret_20d": None,
            "ret_5d_vs_spy": 1.5, "ret_5d_vs_qqq": 0.7, "ret_5d_vs_sector": 2.0,
            "ret_10d_vs_spy": -2.0, "ret_10d_vs_qqq": -2.5,
            "ret_10d_vs_sector": None, "benchmark_sector_etf": "XLK",
        }) + "\n")


@pytest.fixture
def fixture_store(tmp_path):
    _write_fixture_artifacts(tmp_path)
    return ArtifactStore(root=tmp_path)


def _tree_digest(root: Path) -> str:
    h = hashlib.md5()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(root)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


# ── 1. loads even when artifacts are missing ─────────────────────────────────


def test_missing_artifacts_are_graceful(tmp_path):
    store = ArtifactStore(root=tmp_path)  # empty dir — nothing exists
    status = build_status(store)
    board = build_leaderboard(store)
    detail = build_ticker_detail("ANY", store)
    assert status["fallback"] == MISSING_ARTIFACT
    assert len(status["missing_artifacts"]) == 4
    assert board["fallback"] == MISSING_ARTIFACT and board["rows"] == []
    assert detail["fallback"] == MISSING_ARTIFACT
    assert detail["research_only_footer"] == RESEARCH_ONLY_FOOTER


# ── 2. reads latest artifacts correctly ──────────────────────────────────────


def test_reads_artifacts_correctly(fixture_store):
    status = build_status(fixture_store)
    assert status["matured_5d"] == 464
    assert status["matured_10d"] == 134
    assert status["market_regime"] == "Chop / Range"
    assert status["benchmark_readiness"] == "READY"
    assert status["stale_skipped"] == 3
    assert status["suspect_skipped"] == 2
    assert status["quarantine_count"] == 6

    board = build_leaderboard(fixture_store)
    good = next(r for r in board["rows"] if r["ticker"] == "GOODCO")
    assert good["company"] == "Good Co"
    assert good["price"] == 42.5
    assert good["returns"]["5d"] == 3.2
    assert good["benchmarks"]["spy"] == 1.5
    assert good["label"] == "HIGH_PRIORITY_RESEARCH"   # radar overlay wins
    assert good["watchlist_label"] == "EARLY_ACCUMULATION"
    assert good["source"] == "ranked_fill"
    assert good["sector_etf"] == "XLK"


# ── 3. heatmap model handles missing returns ─────────────────────────────────


def test_missing_returns_are_null_and_flagged(fixture_store):
    board = build_leaderboard(fixture_store)
    young = next(r for r in board["rows"] if r["ticker"] == "YOUNGY")
    assert young["returns"]["5d"] is None
    assert young["returns"]["10d"] is None
    assert young["forward_maturity"] == {
        "has_5d": False, "has_10d": False, "has_20d": False}
    assert IMMATURE_FORWARD_EVIDENCE in young["warnings"]
    # 1d trailing return is not published — must be None, never fabricated
    assert all(r["returns"]["1d"] is None for r in board["rows"])


# ── 4. detail drawer has research-only footer ────────────────────────────────


def test_detail_has_research_only_footer(fixture_store):
    detail = build_ticker_detail("GOODCO", fixture_store)
    assert detail["research_only_footer"] == RESEARCH_ONLY_FOOTER
    assert detail["manual_review_required"] is True
    assert detail["reason"] == "Rising volume + improving RS"
    assert detail["forward"]["returns"]["5d"] == 3.2


# ── 5. forbidden trading language never appears ──────────────────────────────


def test_no_forbidden_language_in_models(fixture_store):
    blobs = [
        json.dumps(build_status(fixture_store)),
        json.dumps(build_leaderboard(fixture_store)),
        json.dumps(build_ticker_detail("GOODCO", fixture_store)),
    ]
    for blob in blobs:
        low = blob.lower()
        for phrase in _FORBIDDEN:
            assert phrase not in low, phrase


def test_no_forbidden_language_in_ui_sources():
    pkg = REPO_ROOT / "dashboards" / "research_command_center"
    for path in list(pkg.rglob("*.py")) + list(pkg.rglob("*.html")):
        low = path.read_text(encoding="utf-8").lower()
        for phrase in _FORBIDDEN:
            assert phrase not in low, f"{phrase!r} in {path.name}"


# ── 6. data-quality flags ────────────────────────────────────────────────────


def test_data_quality_flags(fixture_store):
    board = build_leaderboard(fixture_store)
    young = next(r for r in board["rows"] if r["ticker"] == "YOUNGY")
    bad = next(r for r in board["rows"] if r["ticker"] == "BADF")
    assert STALE_PRICE in young["warnings"]
    assert INSUFFICIENT_HISTORY in young["warnings"]
    assert young["data_quality"] == "QUARANTINE"
    assert SUSPECT_FEED in bad["warnings"]
    assert bad["data_quality"] == "QUARANTINE"


def test_benchmark_missing_flag(tmp_path):
    _write_fixture_artifacts(tmp_path)
    hist = tmp_path / "data" / "research" / "research_watchlist_history.jsonl"
    hist.write_text(json.dumps({
        "ticker": "GOODCO", "appearance_date": "2026-06-20",
        "ret_5d": 3.2, "ret_10d": None, "ret_20d": None,
        "ret_5d_vs_spy": None, "ret_5d_vs_qqq": None, "ret_5d_vs_sector": None,
        "ret_10d_vs_spy": None, "ret_10d_vs_qqq": None, "ret_10d_vs_sector": None,
    }) + "\n")
    board = build_leaderboard(ArtifactStore(root=tmp_path))
    good = next(r for r in board["rows"] if r["ticker"] == "GOODCO")
    assert BENCHMARK_MISSING in good["warnings"]


def test_no_price_is_no_data(tmp_path):
    _write_fixture_artifacts(tmp_path)
    scanner = tmp_path / "cache" / "research" / "research_scanner_latest.json"
    data = json.loads(scanner.read_text())
    data["watchlist"][0]["latest_close"] = None
    scanner.write_text(json.dumps(data))
    board = build_leaderboard(ArtifactStore(root=tmp_path))
    good = next(r for r in board["rows"] if r["ticker"] == "GOODCO")
    assert NO_DATA in good["warnings"]
    assert good["price"] is None


# ── 7. Phase 4B blocked when evidence is insufficient ────────────────────────


def test_phase_4b_blocked_for_non_promising_verdicts(tmp_path):
    for verdict in ("MIXED", "NEED_MORE_DATA", "NO_FORWARD_EDGE", "EARLY_SIGNAL"):
        root = tmp_path / verdict
        _write_fixture_artifacts(root, verdict=verdict)
        status = build_status(ArtifactStore(root=root))
        assert status["phase_4b"]["status"] == "BLOCKED", verdict
        assert status["operating_verdict"] == "NEED_MORE_DATA", verdict


def test_phase_4b_unblocked_only_on_promising(tmp_path):
    _write_fixture_artifacts(tmp_path, verdict="PROMISING")
    status = build_status(ArtifactStore(root=tmp_path))
    assert status["phase_4b"]["status"] == "UNBLOCKED"
    assert status["operating_verdict"] == "PASS"


# ── 8 + 9. no mutation of artifacts, no production writes ────────────────────


def test_adapter_never_writes(fixture_store, tmp_path):
    before = _tree_digest(tmp_path)
    build_status(fixture_store)
    build_leaderboard(fixture_store)
    build_ticker_detail("GOODCO", fixture_store)
    build_ticker_detail("UNKNOWN", fixture_store)
    assert _tree_digest(tmp_path) == before


def test_no_production_writes_against_real_artifacts():
    """Adapter runs against the REAL repo artifacts without touching them."""
    store = ArtifactStore()  # real repo root
    watched = [store.scanner_json, store.forward_json, store.radar_json,
               store.summary_json, store.universe_json, store.history_jsonl]
    stats = {p: (p.stat().st_mtime_ns, p.stat().st_size)
             for p in watched if p.exists()}
    build_status(store)
    board = build_leaderboard(store)
    if board["rows"]:
        build_ticker_detail(board["rows"][0]["ticker"], store)
    for p, (mtime, size) in stats.items():
        assert (p.stat().st_mtime_ns, p.stat().st_size) == (mtime, size), p


# ── 10. no coupling to scanner scoring/thresholds ────────────────────────────


def test_dashboard_does_not_import_scanner_internals():
    """The adapter must stay a pure artifact reader — importing research/
    scanner modules would couple the UI to scoring internals."""
    pkg = REPO_ROOT / "dashboards" / "research_command_center"
    for path in pkg.rglob("*.py"):
        src = path.read_text(encoding="utf-8")
        assert "from research" not in src, path.name
        assert "import research." not in src, path.name
        assert "import core.config" not in src, path.name
        assert "from core" not in src, path.name


# ── Phase 2: ticker chart series ─────────────────────────────────────────────


def _write_price_parquets(root: Path) -> None:
    import numpy as np
    import pandas as pd

    prices = root / "cache" / "prices"
    prices.mkdir(parents=True, exist_ok=True)
    dates = pd.date_range("2026-01-02", periods=120, freq="B")
    for sym, start, drift in [("GOODCO", 30.0, 0.15), ("SPY", 500.0, 0.3),
                              ("QQQ", 430.0, 0.35), ("XLK", 210.0, 0.2)]:
        closes = start + np.arange(120) * drift
        pd.DataFrame({"close": closes, "volume": [1e6] * 120},
                     index=dates).to_parquet(prices / f"{sym}.parquet")


@pytest.fixture
def series_store(tmp_path):
    _write_fixture_artifacts(tmp_path)
    _write_price_parquets(tmp_path)
    return ArtifactStore(root=tmp_path)


def test_series_payload_shape(series_store):
    from dashboards.research_command_center.data_adapter import (
        build_ticker_series)
    s = build_ticker_series("GOODCO", series_store)
    assert s["fallback"] is None
    assert s["bars"] == 120
    assert len(s["dates"]) == len(s["close"]) == len(s["volume"]) == 120
    # MA50 needs 50 bars: first 49 None, rest populated
    assert s["ma50"][:49] == [None] * 49
    assert s["ma50"][50] is not None
    # only 120 bars — MA200 entirely None, never fabricated
    assert all(v is None for v in s["ma200"])
    # RS series exist and are date-aligned (same length as dates)
    for key in ("rs_vs_spy", "rs_vs_qqq", "rs_vs_sector"):
        assert s[key] is not None and len(s[key]) == 120, key
    assert s["rs_vs_spy"][0] == 0.0  # relative return starts at zero
    assert s["sector_etf"] == "XLK"
    assert s["research_only_footer"] == RESEARCH_ONLY_FOOTER


def test_series_missing_parquet_is_no_data(fixture_store):
    from dashboards.research_command_center.data_adapter import (
        build_ticker_series)
    s = build_ticker_series("GOODCO", fixture_store)  # no parquets written
    assert s["fallback"] == NO_DATA
    assert s["research_only_footer"] == RESEARCH_ONLY_FOOTER


def test_series_missing_benchmark_flagged(tmp_path):
    from dashboards.research_command_center.data_adapter import (
        BENCHMARK_MISSING as BM, build_ticker_series)
    _write_fixture_artifacts(tmp_path)
    _write_price_parquets(tmp_path)
    (tmp_path / "cache" / "prices" / "SPY.parquet").unlink()
    s = build_ticker_series("GOODCO", ArtifactStore(root=tmp_path))
    assert s["rs_vs_spy"] is None
    assert BM in s["warnings"]


def test_series_never_writes(series_store, tmp_path):
    from dashboards.research_command_center.data_adapter import (
        build_ticker_series)
    before = _tree_digest(tmp_path)
    build_ticker_series("GOODCO", series_store)
    build_ticker_series("MISSING", series_store)
    assert _tree_digest(tmp_path) == before


def test_series_served_over_http(tmp_path):
    import threading
    import urllib.request

    _write_fixture_artifacts(tmp_path)
    _write_price_parquets(tmp_path)
    from dashboards.research_command_center.server import make_server

    srv = make_server(port=0, root=tmp_path)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        s = json.loads(urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/series/GOODCO").read())
        assert s["bars"] == 120 and s["rs_vs_spy"] is not None
    finally:
        srv.shutdown()
        srv.server_close()


# ── server smoke ─────────────────────────────────────────────────────────────


def test_server_serves_api_and_rejects_writes(tmp_path):
    import threading
    import urllib.request

    _write_fixture_artifacts(tmp_path)
    from dashboards.research_command_center.server import make_server

    srv = make_server(port=0, root=tmp_path)
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{port}"
        status = json.loads(urllib.request.urlopen(f"{base}/api/status").read())
        assert status["mode"] == "RESEARCH_ONLY"
        board = json.loads(urllib.request.urlopen(f"{base}/api/leaderboard").read())
        assert board["count"] == 3
        detail = json.loads(urllib.request.urlopen(
            f"{base}/api/ticker/GOODCO").read())
        assert detail["research_only_footer"] == RESEARCH_ONLY_FOOTER
        stub = json.loads(urllib.request.urlopen(
            f"{base}/api/stub/data-quality").read())
        assert stub["status"] == "TODO"
        html = urllib.request.urlopen(f"{base}/").read().decode()
        assert "RESEARCH ONLY" in html
        # write methods are refused
        req = urllib.request.Request(f"{base}/api/status", data=b"{}",
                                     method="POST")
        try:
            urllib.request.urlopen(req)
            raised = False
        except urllib.error.HTTPError as e:
            raised = e.code == 405
        assert raised
    finally:
        srv.shutdown()
        srv.server_close()

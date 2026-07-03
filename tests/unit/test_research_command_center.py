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
                    "matured_entries": 134, "total_entries": 1146,
                    "sample_status": "ROBUST"},
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


# ── Phase 2.5: terminal app shell ────────────────────────────────────────────

_INDEX_HTML = (REPO_ROOT / "dashboards" / "research_command_center" /
               "static" / "index.html")


def test_app_shell_structure():
    """Shell has fixed sidebar, persistent global header, tab row, and a
    dynamic workspace container."""
    html = _INDEX_HTML.read_text(encoding="utf-8")
    for anchor in ('id="sidebar"', 'id="gheader"', 'id="tabbar"',
                   'id="workspace"', 'id="nav"'):
        assert anchor in html, anchor


def test_global_header_has_mode_and_phase4b():
    html = _INDEX_HTML.read_text(encoding="utf-8")
    assert "RESEARCH_ONLY" in html
    assert "PHASE 4B" in html
    # header renderer exists and is fed by /api/status
    assert "renderHeader" in html


def test_workspace_switches_on_nav():
    """Sidebar clicks drive the workspace: nav handler sets activePage and
    re-renders only the workspace content."""
    html = _INDEX_HTML.read_text(encoding="utf-8")
    assert "openPage(" in html
    assert "activePage" in html
    assert "renderPage()" in html


def test_home_has_summary_cards():
    html = _INDEX_HTML.read_text(encoding="utf-8")
    for card_title in ("Engine status", "Evidence readiness", "Data quality",
                       "Candidate summary", "Warnings"):
        assert card_title in html, card_title
    # quick-action navigation cards
    assert "actionCard(" in html


def test_leaderboard_terminal_features():
    html = _INDEX_HTML.read_text(encoding="utf-8")
    assert "position:sticky" in html          # sticky table header
    assert "sel" in html and "selectedTicker" in html   # selected-row state
    assert "of ${b.rows.length} candidates" in html      # summary line
    assert 'title="${esc(r.warnings.join' in html        # warning tooltips


def test_sidebar_collapse_control():
    html = _INDEX_HTML.read_text(encoding="utf-8")
    assert 'id="side-toggle"' in html
    assert "side-collapsed" in html


def test_placeholder_pages_are_honest():
    """Later-phase stubs say 'pending approval' and never imply alpha proof."""
    from dashboards.research_command_center.server import STUB_PAGES
    for page, note in STUB_PAGES.items():
        assert "pending approval" in note, page
        assert "not implemented yet" in note, page
    html = _INDEX_HTML.read_text(encoding="utf-8")
    assert "pending approval" in html


def test_status_exposes_home_card_fields(fixture_store):
    status = build_status(fixture_store)
    assert status["total_tracked"] == 1146
    assert status["sample_status"] == "ROBUST"
    assert status["quarantine_breakdown"] == {
        "INSUFFICIENT_HISTORY": 3, "DATA_QUARANTINE": 3}


# ── Phase 2.6: design-system polish ──────────────────────────────────────────


def test_badge_system_covers_key_statuses():
    """Consistent pill badges exist and the mapping covers the key states.
    Phase 4B BLOCKED must map amber (safety gate), never red."""
    html = _INDEX_HTML.read_text(encoding="utf-8")
    for cls in (".bdg.g", ".bdg.a", ".bdg.r", ".bdg.n"):
        assert cls in html, cls
    assert "badgeClass" in html and "function badge(" in html
    green = html.split("BADGE_GREEN=")[1].split(";")[0]
    amber = html.split("BADGE_AMBER=")[1].split(";")[0]
    red = html.split("BADGE_RED=")[1].split(";")[0]
    for v in ("PASS", "READY", "FRESH", "RESEARCH_ONLY"):
        assert f'"{v}"' in green, v
    for v in ("BLOCKED", "NEED_MORE_DATA", "INCONCLUSIVE", "MIXED", "PARTIAL",
              "STALE", "DATA_QUARANTINE", "IMMATURE_FORWARD_EVIDENCE"):
        assert f'"{v}"' in amber, v
    assert '"BLOCKED"' not in red  # gate, not failure
    for v in ("FAIL", "MISSING", "ERROR"):
        assert f'"{v}"' in red, v


def test_action_cards_are_clickable_containers():
    html = _INDEX_HTML.read_text(encoding="utf-8")
    assert 'class="card action"' in html
    assert 'tabindex="0"' in html          # keyboard accessible
    assert 'role="button"' in html
    assert "onkeydown" in html             # Enter/Space activate
    assert ".card.action:hover" in html    # hover affordance on whole card


def test_card_system_polish():
    html = _INDEX_HTML.read_text(encoding="utf-8")
    assert ".card.accent" in html          # top accent border variant
    assert "translateY(-1px)" in html      # subtle hover lift
    # footer disclaimer present and styled muted (amber/dim, not loud)
    assert html.count("Research only — not a signal or recommendation.") >= 2


def test_placeholder_empty_state_cards():
    html = _INDEX_HTML.read_text(encoding="utf-8")
    assert 'class="stub"' in html
    assert "TODO — pending approval" in html
    # stub badge uses the neutral badge class, not a loud color
    assert 'bdg n">TODO' in html


# ── Phase 2.7: visual cleanup ────────────────────────────────────────────────


def test_sidebar_initials_hidden_when_expanded():
    """Expanded sidebar shows names only; collapsed shows centered initials
    with full-name title tooltips."""
    html = _INDEX_HTML.read_text(encoding="utf-8")
    assert "#nav .ini{display:none" in html
    assert "body.side-collapsed #nav .ini{display:inline-block}" in html
    assert "body.side-collapsed #nav .lbl" in html      # labels hidden collapsed
    assert 'title="${n.label}"' in html                  # tooltip carries name


def test_phase_badges_muted():
    html = _INDEX_HTML.read_text(encoding="utf-8")
    # P3-P6 badges still render but use the muted color, not amber
    assert '<span class="ph">P${n.stub}</span>' in html
    ph_css = html.rsplit("#nav .ph{", 1)[1].split("}")[0]
    assert "var(--dim)" in ph_css and "var(--amber)" not in ph_css


def test_header_two_row_grouping_keeps_safety_chips():
    html = _INDEX_HTML.read_text(encoding="utf-8")
    # primary row keeps every safety-critical chip; data row is grouped
    primary = html.split("const primary=[")[1].split("];")[0]
    for label in ('"MODE"', '"NIGHTLY"', '"PHASE 4B"', "VERDICT",
                  '"MATURED 5D"', '"MATURED 10D"', '"BENCHMARKS"'):
        assert label in primary, label
    assert '<span class="grp">Data:</span>' in html


def test_home_reason_renders_as_note_block():
    html = _INDEX_HTML.read_text(encoding="utf-8")
    assert '<div class="note">${esc(p4.reason||"—")}</div>' in html
    assert ".note{" in html


def test_alpha_radar_summary_strip():
    html = _INDEX_HTML.read_text(encoding="utf-8")
    assert 'id="alpha-strip"' in html
    for label in ("HIGH PRIORITY:", "RESET / RECLAIM:", "EXTENDED / CROWDED:",
                  "QUARANTINE / YOUNG:"):
        assert label in html, label
    assert "curated research queue" in html


def test_row_warning_icon_muted_with_tooltip():
    html = _INDEX_HTML.read_text(encoding="utf-8")
    assert 'class="wico" title="${esc(r.warnings.join' in html
    assert "tr.row:hover .wico" in html    # full opacity on hover/selection


def test_table_natural_widths_no_truncation():
    html = _INDEX_HTML.read_text(encoding="utf-8")
    assert "width:max-content;min-width:100%" in html
    assert '["scanner_category","Category"]' in html    # column still present


def test_tab_row_is_capped():
    html = _INDEX_HTML.read_text(encoding="utf-8")
    assert "MAX_TABS" in html
    assert "openTabs.length>MAX_TABS" in html


# ── Phase 3: cohort analytics ────────────────────────────────────────────────


def _write_history_multi(root: Path) -> None:
    """Multi-entry history: pre-fix matured entries + post-fix immature."""
    lines = []
    for i in range(12):   # pre-fix, matured 10d, category A
        lines.append(json.dumps({
            "ticker": f"PRE{i}", "appearance_date": "2026-06-20",
            "category": "early_accumulation", "watchlist_label": "EARLY_ACCUMULATION",
            "ret_5d": 2.0, "ret_10d": 1.0 if i % 2 else -1.0, "ret_20d": None,
            "ret_10d_vs_spy": 0.5, "ret_10d_vs_qqq": 0.4, "ret_10d_vs_sector": None,
        }))
    for i in range(5):    # post-fix, nothing matured
        lines.append(json.dumps({
            "ticker": f"POST{i}", "appearance_date": "2026-07-03",
            "category": "rs_momentum_leader", "watchlist_label": "RS_MOMENTUM_LEADER",
            "ret_5d": None, "ret_10d": None, "ret_20d": None,
        }))
    (root / "data" / "research" / "research_watchlist_history.jsonl").write_text(
        "\n".join(lines) + "\n")


def test_forward_cohorts_shape_and_era_split(tmp_path):
    from dashboards.research_command_center.data_adapter import (
        build_forward_cohorts)
    _write_fixture_artifacts(tmp_path)
    _write_history_multi(tmp_path)
    d = build_forward_cohorts(ArtifactStore(root=tmp_path))
    assert d["fallback"] is None
    # tracker verdicts passed through unmodified
    assert d["by_label"] == json.loads(
        (tmp_path / "cache" / "research" / "research_forward_latest.json")
        .read_text()).get("verdicts_by_label", [])
    # era split at the price-fix boundary
    eras = {e["cohort"]: e for e in d["by_era"]}
    assert eras["pre_fix"]["n_entries"] == 12
    assert eras["pre_fix"]["n_matured_10d"] == 12
    assert eras["post_fix"]["n_entries"] == 5
    assert eras["post_fix"]["n_matured_10d"] == 0
    assert eras["post_fix"]["sample_status"] == "TOO_EARLY"
    # computed cohorts carry stats, never verdicts
    for row in d["by_category"] + d["by_source"] + d["by_era"]:
        assert "verdict" not in row
    assert any("do not pool" in c.lower() for c in d["caveats"])


def test_forward_cohort_math(tmp_path):
    from dashboards.research_command_center.data_adapter import (
        build_forward_cohorts)
    _write_fixture_artifacts(tmp_path)
    _write_history_multi(tmp_path)
    d = build_forward_cohorts(ArtifactStore(root=tmp_path))
    ea = next(r for r in d["by_category"] if r["cohort"] == "early_accumulation")
    assert ea["n_matured_10d"] == 12
    assert ea["win_rate_10d"] == 0.5          # 6 of 12 positive
    assert ea["avg_ret_10d"] == 0.0           # +1/-1 alternating
    assert ea["mean_vs_spy_10d"] == 0.5
    assert ea["sample_status"] == "PROVISIONAL"   # 10-29 matured


def test_forward_cohorts_maturity_ladder():
    from dashboards.research_command_center.data_adapter import _sample_status
    assert _sample_status(0) == "TOO_EARLY"
    assert _sample_status(9) == "TOO_EARLY"
    assert _sample_status(10) == "PROVISIONAL"
    assert _sample_status(30) == "MEANINGFUL"
    assert _sample_status(100) == "ROBUST"


def test_forward_cohorts_missing_artifacts(tmp_path):
    from dashboards.research_command_center.data_adapter import (
        build_forward_cohorts)
    d = build_forward_cohorts(ArtifactStore(root=tmp_path))
    assert d["fallback"] == MISSING_ARTIFACT
    assert d["research_only_footer"] == RESEARCH_ONLY_FOOTER


def test_sector_compass(tmp_path):
    from dashboards.research_command_center.data_adapter import (
        build_sector_compass)
    _write_fixture_artifacts(tmp_path)
    _write_price_parquets(tmp_path)   # writes SPY/QQQ/XLK/GOODCO
    d = build_sector_compass(ArtifactStore(root=tmp_path))
    assert d["fallback"] is None
    xlk = next(s for s in d["sectors"] if s["etf"] == "XLK")
    assert xlk["rs_20d_vs_spy"] is not None
    assert len(xlk["spark_rs_vs_spy"]) > 0
    # ETFs without parquets degrade to NO_DATA, never fabricated
    xle = next(s for s in d["sectors"] if s["etf"] == "XLE")
    assert xle["fallback"] == NO_DATA


def test_sector_compass_no_spy(tmp_path):
    from dashboards.research_command_center.data_adapter import (
        build_sector_compass)
    d = build_sector_compass(ArtifactStore(root=tmp_path))
    assert d["fallback"] == NO_DATA


def test_social_overview(tmp_path):
    from dashboards.research_command_center.data_adapter import (
        build_social_overview)
    _write_fixture_artifacts(tmp_path)
    (tmp_path / "cache" / "research" /
     "social_attention_forward_latest.json").write_text(json.dumps({
        "generated_at": "2026-07-03T00:00:00+00:00",
        "verdict": "NEED_MORE_DATA",
        "verdict_reason": "insufficient matured social-led entries",
        "matured_social_led_primary": 3,
        "by_cohort": {"lead_SOCIAL_LED": {"n": 3}},
        "history_days": 12,
     }))
    d = build_social_overview(ArtifactStore(root=tmp_path))
    assert d["fallback"] is None
    assert d["verdict"] == "NEED_MORE_DATA"      # validator verdict unmodified
    assert d["matured_social_led"] == 3
    assert d["research_only_footer"] == RESEARCH_ONLY_FOOTER


def test_phase3_pages_render_in_ui():
    html = _INDEX_HTML.read_text(encoding="utf-8")
    for marker in ("renderForward", "renderSectors", "renderSocial",
                   "api/forward-cohorts", "api/sectors", "api/social",
                   "Do not pool pre-fix and post-fix eras",
                   "tracker-published verdicts",
                   "joins CURRENT source map"):
        assert marker in html, marker
    # implemented pages no longer carry stub badges
    assert '{id:"forward-evidence", label:"Forward Evidence", ini:"FE"}' in html


def test_phase3_endpoints_served(tmp_path):
    import threading
    import urllib.request

    _write_fixture_artifacts(tmp_path)
    _write_history_multi(tmp_path)
    _write_price_parquets(tmp_path)
    from dashboards.research_command_center.server import make_server

    srv = make_server(port=0, root=tmp_path)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{port}"
        fc = json.loads(urllib.request.urlopen(f"{base}/api/forward-cohorts").read())
        assert {e["cohort"] for e in fc["by_era"]} == {"pre_fix", "post_fix"}
        sc = json.loads(urllib.request.urlopen(f"{base}/api/sectors").read())
        assert len(sc["sectors"]) == 11
        so = json.loads(urllib.request.urlopen(f"{base}/api/social").read())
        assert "research_only_footer" in so
    finally:
        srv.shutdown()
        srv.server_close()


def test_phase3_never_writes(tmp_path):
    from dashboards.research_command_center.data_adapter import (
        build_forward_cohorts, build_sector_compass, build_social_overview)
    _write_fixture_artifacts(tmp_path)
    _write_history_multi(tmp_path)
    _write_price_parquets(tmp_path)
    store = ArtifactStore(root=tmp_path)
    before = _tree_digest(tmp_path)
    build_forward_cohorts(store)
    build_sector_compass(store)
    build_social_overview(store)
    assert _tree_digest(tmp_path) == before


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

"""tests/unit/test_gatekeeper_refresh.py — Phase 2B.2 ticker selection.

Validates the cache-only ticker selection logic in
``research/gatekeeper_refresh.py``:

  - open positions (DB)
  - top Alpha Discovery candidates (JSON cache)
  - explicit watchlist
  - missing / stale Gatekeeper artifacts (filesystem)

and verifies the cap is enforced.  No FMP calls in these tests — the
earnings source is exercised through ``--skip-earnings``.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from research import gatekeeper_refresh as gr  # noqa: E402


HOUR = 3600


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def fake_db(tmp_path: Path) -> Path:
    """Minimal decisions table with two open + one closed position."""
    db_path = tmp_path / "trading.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE decisions("
            "  id INTEGER PRIMARY KEY,"
            "  ticker TEXT,"
            "  position_opened INTEGER,"
            "  position_closed INTEGER)"
        )
        conn.executemany(
            "INSERT INTO decisions(ticker, position_opened, position_closed) "
            "VALUES (?, ?, ?)",
            [
                ("AAPL", 1, 0),
                ("NVDA", 1, 0),
                ("TSLA", 1, 1),  # closed — should be ignored
                ("AAPL", 1, 0),  # duplicate — distinct dedupes
            ],
        )
        conn.commit()
    finally:
        conn.close()
    return db_path


@pytest.fixture
def alpha_board(tmp_path: Path) -> Path:
    cache = tmp_path / "research"
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / "alpha_discovery_board_latest.json"
    payload = {
        "items": [
            {"ticker": "DINO", "data_tier": "A", "alpha_score": 76.1},
            {"ticker": "AMZN", "data_tier": "A", "alpha_score": 77.1},
            {"ticker": "MOH",  "data_tier": "A", "alpha_score": 73.8},
            {"ticker": "MWH",  "data_tier": "B", "alpha_score": 71.3},
            {"ticker": "CNC",  "data_tier": "C", "alpha_score": 67.0},
        ],
    }
    path.write_text(json.dumps(payload))
    return path


@pytest.fixture
def stock_lens_dir(tmp_path: Path) -> Path:
    """Three stock-lens files: one with fresh Gatekeeper, one stale, one missing."""
    cache = tmp_path / "research"
    cache.mkdir(parents=True, exist_ok=True)
    for tk in ("NVDA", "AAPL", "TSLA"):
        (cache / f"stock_lens_{tk}_latest.json").write_text("{}")

    # NVDA: stale Gatekeeper (36h old)
    nvda_gk = cache / "executive_gatekeeper_NVDA_latest.json"
    nvda_gk.write_text("{}")
    old = time.time() - 36 * HOUR
    os.utime(nvda_gk, (old, old))

    # AAPL: fresh Gatekeeper (1h old)
    aapl_gk = cache / "executive_gatekeeper_AAPL_latest.json"
    aapl_gk.write_text("{}")
    one_hour_ago = time.time() - 1 * HOUR
    os.utime(aapl_gk, (one_hour_ago, one_hour_ago))

    # TSLA: missing Gatekeeper
    return cache


# ── Per-source selectors ────────────────────────────────────────────────────

def test_select_open_positions_dedupes(fake_db):
    cands = gr.select_open_positions(fake_db)
    tickers = sorted(c.ticker for c in cands)
    assert tickers == ["AAPL", "NVDA"]
    assert all("open_position" in c.reasons for c in cands)


def test_select_open_positions_missing_db_safe(tmp_path):
    cands = gr.select_open_positions(tmp_path / "nope.db")
    assert cands == []


def test_select_alpha_top_a_first(alpha_board):
    cands = gr.select_alpha_top(alpha_board, cap=10)
    # A-tier comes first; within tier, alpha_score desc.
    a_tier = [c.ticker for c in cands
              if any(r.startswith("alpha_top:A:") for r in c.reasons)]
    assert a_tier == ["AMZN", "DINO", "MOH"]


def test_select_alpha_top_cap(alpha_board):
    cands = gr.select_alpha_top(alpha_board, cap=2)
    assert len(cands) == 2


def test_select_alpha_top_missing_file_safe(tmp_path):
    cands = gr.select_alpha_top(tmp_path / "missing.json", cap=10)
    assert cands == []


def test_select_explicit_normalizes():
    cands = gr.select_explicit(["nvda", "aapl", "", None, " tsla "])
    tickers = sorted(c.ticker for c in cands)
    assert tickers == ["AAPL", "NVDA", "TSLA"]


def test_select_missing_or_stale(stock_lens_dir, monkeypatch):
    # Redirect the helper that resolves Gatekeeper paths to our tmp dir.
    monkeypatch.setattr(
        gr,
        "gatekeeper_artifact_path",
        lambda tk: stock_lens_dir / f"executive_gatekeeper_{tk.upper()}_latest.json",
    )
    cands = gr.select_missing_or_stale(stock_lens_dir, cap=10)
    by_ticker = {c.ticker: c for c in cands}
    # TSLA missing, NVDA stale; AAPL fresh and excluded.
    assert "TSLA" in by_ticker
    assert "NVDA" in by_ticker
    assert "AAPL" not in by_ticker
    assert any("missing_gatekeeper" in r for r in by_ticker["TSLA"].reasons)
    assert any(r.startswith("stale:") for r in by_ticker["NVDA"].reasons)


# ── build_plan: merge + cap ────────────────────────────────────────────────

def test_build_plan_merges_and_caps(fake_db, alpha_board, stock_lens_dir,
                                    monkeypatch):
    # No earnings (skip_earnings=True) so we don't hit FMP.
    monkeypatch.setattr(
        gr,
        "gatekeeper_artifact_path",
        lambda tk: stock_lens_dir / f"executive_gatekeeper_{tk.upper()}_latest.json",
    )
    plan = gr.build_plan(
        max_tickers=3,
        earnings_days_ahead=5,
        explicit_watch=["VOO"],
        db_path=fake_db,
        cache_dir=stock_lens_dir,
        skip_earnings=True,
        alpha_cap=10,
        missing_cap=10,
    )
    assert len(plan) == 3
    # Open positions are priority 10 — they should land in the top 3.
    top_tickers = {c.ticker for c in plan}
    assert "AAPL" in top_tickers
    assert "NVDA" in top_tickers


def test_build_plan_open_position_beats_alpha(fake_db, alpha_board,
                                              stock_lens_dir, monkeypatch):
    monkeypatch.setattr(
        gr,
        "gatekeeper_artifact_path",
        lambda tk: stock_lens_dir / f"executive_gatekeeper_{tk.upper()}_latest.json",
    )
    plan = gr.build_plan(
        max_tickers=25,
        earnings_days_ahead=5,
        explicit_watch=[],
        db_path=fake_db,
        cache_dir=stock_lens_dir,
        skip_earnings=True,
        alpha_cap=10,
        missing_cap=10,
    )
    priorities = {c.ticker: c.priority for c in plan}
    # Open positions priority 10 < alpha_top priority 50.
    assert priorities["AAPL"] < priorities["DINO"]


def test_build_plan_max_zero_yields_empty(fake_db, alpha_board, stock_lens_dir,
                                          monkeypatch):
    monkeypatch.setattr(
        gr,
        "gatekeeper_artifact_path",
        lambda tk: stock_lens_dir / f"executive_gatekeeper_{tk.upper()}_latest.json",
    )
    plan = gr.build_plan(
        max_tickers=0,
        earnings_days_ahead=5,
        explicit_watch=["NVDA"],
        db_path=fake_db,
        cache_dir=stock_lens_dir,
        skip_earnings=True,
    )
    assert plan == []


def test_build_plan_dedupes_across_sources(fake_db, alpha_board, stock_lens_dir,
                                            monkeypatch):
    # NVDA is open-position AND will be on the watch list — should appear once,
    # with the open-position priority winning.
    monkeypatch.setattr(
        gr,
        "gatekeeper_artifact_path",
        lambda tk: stock_lens_dir / f"executive_gatekeeper_{tk.upper()}_latest.json",
    )
    plan = gr.build_plan(
        max_tickers=25,
        earnings_days_ahead=5,
        explicit_watch=["NVDA"],
        db_path=fake_db,
        cache_dir=stock_lens_dir,
        skip_earnings=True,
        alpha_cap=10,
        missing_cap=10,
    )
    nvdas = [c for c in plan if c.ticker == "NVDA"]
    assert len(nvdas) == 1
    assert nvdas[0].priority == gr.PRIORITY["open_position"]
    # Merged reasons union both inputs.
    assert "open_position" in nvdas[0].reasons


# ── run_refresh dry-run never imports the gatekeeper ───────────────────────

def test_run_refresh_dry_run_does_not_import_gatekeeper(monkeypatch):
    cands = [gr.Candidate(ticker="NVDA", priority=10, reasons=["open_position"])]
    # Replace the lazy import target with one that raises if called.
    def _boom(*a, **kw):
        raise AssertionError("dry_run must not call run_executive_gatekeeper")
    monkeypatch.setattr(
        "core.executive_gatekeeper.run_executive_gatekeeper", _boom,
        raising=False,
    )
    out = gr.run_refresh(cands, dry_run=True)
    assert out["NVDA"]["status"] == "DRY_RUN"


# ── CLI smoke ───────────────────────────────────────────────────────────────

def test_main_dry_run_json(tmp_path, fake_db, alpha_board, stock_lens_dir,
                            monkeypatch, capsys):
    monkeypatch.setattr(gr, "DB_PATH", fake_db)
    monkeypatch.setattr(gr, "CACHE_DIR", stock_lens_dir)
    monkeypatch.setattr(
        gr,
        "gatekeeper_artifact_path",
        lambda tk: stock_lens_dir / f"executive_gatekeeper_{tk.upper()}_latest.json",
    )
    rc = gr.main([
        "--max", "5",
        "--skip-earnings",
        "--dry-run",
        "--json",
        "--watch", "NVDA",
    ])
    assert rc == 0
    captured = capsys.readouterr()
    parsed = json.loads(captured.out)
    assert "plan" in parsed
    assert parsed["summary"]["dry_run"] == len(parsed["plan"])
    assert parsed["summary"]["ok"] == 0
    assert parsed["summary"]["error"] == 0

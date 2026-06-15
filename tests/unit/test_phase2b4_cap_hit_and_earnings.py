"""tests/unit/test_phase2b4_cap_hit_and_earnings.py — Phase 2B.4
gatekeeper-refresh cap visibility + dashboard EARNINGS DATA STALE marker.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Optional

import pytest
from rich.console import Console

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from research import gatekeeper_refresh as gr  # noqa: E402
from dashboards.gem_trader_hq import PB, _EARNINGS_CACHE_STALE_S  # noqa: E402


HOUR = 3600


# ── Fixtures (mirrored from the Phase 2B.2 cap-bound test) ──────────────────

@pytest.fixture
def fake_db(tmp_path: Path) -> Path:
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
        conn.execute(
            "INSERT INTO decisions(ticker, position_opened, position_closed) "
            "VALUES (?, ?, ?)", ("NVDA", 1, 0),
        )
        conn.commit()
    finally:
        conn.close()
    return db_path


@pytest.fixture
def board_with_many_alpha(tmp_path: Path) -> Path:
    cache = tmp_path / "research"
    cache.mkdir(parents=True, exist_ok=True)
    items = []
    # Generate 30 A-tier candidates so a cap of 5 must drop the tail.
    for i in range(30):
        items.append({
            "ticker": f"AAA{i:02d}", "data_tier": "A",
            "alpha_score": 90.0 - i * 0.5,
        })
    (cache / "alpha_discovery_board_latest.json").write_text(
        json.dumps({"items": items}),
    )
    return cache


# ── Cap-hit ─────────────────────────────────────────────────────────────────

def test_cap_hit_when_more_candidates_than_max(fake_db, board_with_many_alpha,
                                                monkeypatch):
    monkeypatch.setattr(
        gr,
        "gatekeeper_artifact_path",
        lambda tk: board_with_many_alpha / f"gk_{tk}.json",
    )
    result = gr.build_plan_detailed(
        max_tickers=5,
        earnings_days_ahead=5,
        explicit_watch=[],
        db_path=fake_db,
        cache_dir=board_with_many_alpha,
        skip_earnings=True,
        alpha_cap=30,
        missing_cap=0,
    )
    assert result.cap_hit is True
    assert len(result.selected) == 5
    assert len(result.dropped) >= 25
    # Open position (priority 10) is in selected; alpha tail in dropped.
    selected_tk = {c.ticker for c in result.selected}
    assert "NVDA" in selected_tk
    # The dropped tail is dominated by alpha_top.
    by_source = result.dropped_by_source()
    assert by_source.get("alpha_top", 0) >= 25


def test_no_cap_hit_when_plan_fits(fake_db, board_with_many_alpha, monkeypatch):
    monkeypatch.setattr(
        gr,
        "gatekeeper_artifact_path",
        lambda tk: board_with_many_alpha / f"gk_{tk}.json",
    )
    result = gr.build_plan_detailed(
        max_tickers=100,
        earnings_days_ahead=5,
        explicit_watch=[],
        db_path=fake_db,
        cache_dir=board_with_many_alpha,
        skip_earnings=True,
        alpha_cap=30,
        missing_cap=0,
    )
    assert result.cap_hit is False
    assert result.dropped == []


def test_build_plan_backcompat_returns_selected(fake_db, board_with_many_alpha,
                                                 monkeypatch):
    """The legacy ``build_plan`` shim must still return the selected list
    only — Phase 2B.3 callers depend on this signature.
    """
    monkeypatch.setattr(
        gr,
        "gatekeeper_artifact_path",
        lambda tk: board_with_many_alpha / f"gk_{tk}.json",
    )
    plan = gr.build_plan(
        max_tickers=5,
        earnings_days_ahead=5,
        explicit_watch=[],
        db_path=fake_db,
        cache_dir=board_with_many_alpha,
        skip_earnings=True,
        alpha_cap=30,
        missing_cap=0,
    )
    assert isinstance(plan, list)
    assert len(plan) == 5
    assert all(isinstance(c, gr.Candidate) for c in plan)


def test_main_writes_sidecar_with_cap_hit(tmp_path, fake_db,
                                          board_with_many_alpha, monkeypatch,
                                          capsys):
    """The CLI writes a gatekeeper_refresh_latest.json sidecar that
    carries cap_hit / dropped_by_source / dropped tail.  The provider
    audit picks this up.
    """
    monkeypatch.setattr(gr, "DB_PATH", fake_db)
    monkeypatch.setattr(gr, "CACHE_DIR", board_with_many_alpha)
    monkeypatch.setattr(
        gr,
        "gatekeeper_artifact_path",
        lambda tk: board_with_many_alpha / f"gk_{tk}.json",
    )
    rc = gr.main([
        "--max", "5", "--skip-earnings", "--dry-run", "--json",
        "--alpha-cap", "30", "--missing-cap", "0",
    ])
    assert rc == 0
    sidecar = board_with_many_alpha / "gatekeeper_refresh_latest.json"
    assert sidecar.exists()
    data = json.loads(sidecar.read_text())
    assert data["cap_hit"] is True
    assert data["summary"]["dropped"] >= 25
    # dropped_by_source surfaces alpha_top as the dominant cap-bound source.
    assert data["dropped_by_source"].get("alpha_top", 0) >= 25
    # JSON stdout payload also carries cap_hit.
    captured = capsys.readouterr().out
    parsed = json.loads(captured)
    assert parsed["cap_hit"] is True


# ── Dashboard EARNINGS DATA STALE marker ────────────────────────────────────

class _StubData:
    def __init__(self, *, earnings, cache_age):
        self._earnings = earnings
        self._cache_age = cache_age

    def get(self, key, default=None):
        if key == "earnings":
            return self._earnings
        if key == "earnings_cache_age":
            return self._cache_age
        if key == "positions":
            return []
        if key == "universe_snap":
            return {}
        return default


def _render(panel) -> str:
    c = Console(record=True, width=140, force_terminal=False, color_system=None)
    c.print(panel)
    return c.export_text()


def _state(ticker: str = "NVDA") -> SimpleNamespace:
    return SimpleNamespace(
        ticker=ticker, history=[], search_active=False, search_buf="",
        lens_pending_ticker=None, lens_last_error=None, analysis=None,
        alpha_show_more=0,
    )


def _earn_today(symbol: str = "NVDA") -> Dict[str, Any]:
    d = datetime.now(timezone.utc).date()
    return {"symbol": symbol, "date": d.isoformat(), "epsEstimated": 1.76}


def test_ticker_lookup_stale_cache_shows_marker():
    data = _StubData(
        earnings=[_earn_today("NVDA")],
        cache_age=int(_EARNINGS_CACHE_STALE_S + HOUR),
    )
    text = _render(PB.ticker_lookup(_state("NVDA"), data))
    assert "EARNINGS DATA STALE" in text


def test_ticker_lookup_fresh_cache_no_marker():
    data = _StubData(
        earnings=[_earn_today("NVDA")],
        cache_age=2 * HOUR,
    )
    text = _render(PB.ticker_lookup(_state("NVDA"), data))
    assert "EARNINGS DATA STALE" not in text


def test_ticker_lookup_no_cache_age_does_not_crash():
    data = _StubData(earnings=[_earn_today("NVDA")], cache_age=None)
    text = _render(PB.ticker_lookup(_state("NVDA"), data))
    # No marker when cache_age is unknown — but EARNINGS TODAY still shows.
    assert "EARNINGS DATA STALE" not in text
    assert "EARNINGS TODAY" in text


def test_earnings_panel_stale_cache_shows_banner():
    data = _StubData(
        earnings=[_earn_today("NVDA")],
        cache_age=int(_EARNINGS_CACHE_STALE_S + 2 * HOUR),
    )
    text = _render(PB.earnings(data))
    assert "EARNINGS DATA STALE" in text


def test_earnings_panel_fresh_cache_no_banner():
    data = _StubData(
        earnings=[_earn_today("NVDA")],
        cache_age=3 * HOUR,
    )
    text = _render(PB.earnings(data))
    assert "EARNINGS DATA STALE" not in text


def test_doctrine_threshold_constants():
    """The dashboard staleness threshold sits one hour past the bumped
    TTL_EARNINGS_CAL — so a benign nightly→premarket lag does not trip
    the marker, but a missed cycle does.
    """
    from core import data_gatekeeper as dgk
    assert _EARNINGS_CACHE_STALE_S == 14 * HOUR
    assert _EARNINGS_CACHE_STALE_S > dgk.TTL_EARNINGS_CAL

"""Tests that the MCP late-chase audit propagates the board-side annotation
flags (OPTIONS_BEARISH_HEDGE / OPTIONS_BULLISH_BUT_LATE / OPTIONS_NO_EDGE /
BOARD_LENS_CONFLICT / LENS_STALE) into its candidate records, and that the
extended `_lens_late_chase_flags` map covers BEARISH_HEDGE / BEARISH_CALL_CHASE.

Predicate must be UNCHANGED — a ticker carrying only board OPTIONS_* flags
but with no derived late-chase signal must still be excluded from the audit.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

import audit_mcp.stocklens_mcp_tools as tools


@pytest.fixture
def isolated_repo(monkeypatch, tmp_path):
    """Point the MCP tools at a temp repo root so we can stage artifacts.

    The tools resolve cache paths via STOCKLENS_ROOT — without this fixture
    the audit would read from the real repo cache and the test wouldn't be
    isolated.
    """
    monkeypatch.setenv("STOCKLENS_ROOT", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    (tmp_path / "cache" / "research").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _write_board(repo: Path, items):
    path = repo / "cache" / "research" / "alpha_discovery_board_latest.json"
    path.write_text(json.dumps({
        "built_at": "2026-05-22T00:00:00",
        "items": items,
    }))


def _write_lens(repo: Path, ticker: str, lens: Dict[str, Any]):
    path = repo / "cache" / "research" / f"stock_lens_{ticker}_latest.json"
    path.write_text(json.dumps(lens))


def _late_chase_item(ticker="ARW", **kw):
    """A board item that passes the late-chase predicate by default."""
    base = {
        "ticker": ticker,
        "alpha_score": 75.0,
        "validator_state": "Too Extended",
        "bucket": "Too Late / Crowded",
        "action_label": "Too Extended",
        "actionable_now": False,
        "validator_flags": [],
        "alpha_flags": [],
    }
    base.update(kw)
    return base


def _clean_item(ticker="ZZZ", **kw):
    """A board item that should NOT pass the late-chase predicate."""
    base = {
        "ticker": ticker,
        "alpha_score": 50.0,            # below the high-alpha threshold
        "validator_state": "Watch Reclaim",
        "bucket": "Early Discovery",
        "action_label": "Watch Reclaim",
        "actionable_now": False,
        "validator_flags": [],
        "alpha_flags": [],
    }
    base.update(kw)
    return base


# ── propagation: board flags surface on included records ─────────────────────

def test_board_options_flags_propagate_to_audit_record(isolated_repo):
    _write_board(isolated_repo, [
        _late_chase_item("ARW", alpha_flags=["OPTIONS_BEARISH_HEDGE"]),
    ])
    out = tools.audit_late_chase_candidates(top_n=10)
    rec = next(r for r in out["candidates"] if r["ticker"] == "ARW")
    assert "OPTIONS_BEARISH_HEDGE" in rec["alpha_flags"]


def test_board_lens_conflict_flag_propagates(isolated_repo):
    _write_board(isolated_repo, [
        _late_chase_item("OSCR", alpha_flags=["BOARD_LENS_CONFLICT"]),
    ])
    out = tools.audit_late_chase_candidates(top_n=10)
    rec = next(r for r in out["candidates"] if r["ticker"] == "OSCR")
    assert "BOARD_LENS_CONFLICT" in rec["alpha_flags"]


def test_lens_stale_flag_propagates(isolated_repo):
    _write_board(isolated_repo, [
        _late_chase_item("TWLO", alpha_flags=["LENS_STALE"]),
    ])
    out = tools.audit_late_chase_candidates(top_n=10)
    rec = next(r for r in out["candidates"] if r["ticker"] == "TWLO")
    assert "LENS_STALE" in rec["alpha_flags"]


def test_board_lens_annotation_fields_surface_on_record(isolated_repo):
    _write_board(isolated_repo, [
        _late_chase_item(
            "MTSI",
            alpha_flags=["OPTIONS_BULLISH_BUT_LATE"],
            lens_label="Bullish but not buyable yet",
            lens_age_hours=0.5,
            lens_stale=False,
            entry_validator_state="Too Extended",
            options_quality="BULLISH_BUT_LATE",
            board_lens_conflict=False,
            original_action_label="Too Extended",
            original_alpha_score=74.0,
        ),
    ])
    out = tools.audit_late_chase_candidates(top_n=10)
    rec = next(r for r in out["candidates"] if r["ticker"] == "MTSI")
    assert rec["lens_age_hours"] == 0.5
    assert rec["entry_validator_state"] == "Too Extended"
    assert rec["options_quality"] == "BULLISH_BUT_LATE"
    assert rec["board_lens_conflict"] is False
    assert rec["original_action_label"] == "Too Extended"


# ── predicate is NOT widened ─────────────────────────────────────────────────

def test_clean_item_with_only_board_options_flag_is_excluded(isolated_repo):
    """A ticker that DOES NOT trigger any derived late-chase flag must NOT
    appear in the audit, even if the board carries an OPTIONS_NO_EDGE flag."""
    _write_board(isolated_repo, [
        _clean_item("ZZZ", alpha_flags=["OPTIONS_NO_EDGE"]),
    ])
    out = tools.audit_late_chase_candidates(top_n=10)
    tickers = [r["ticker"] for r in out["candidates"]]
    assert "ZZZ" not in tickers


def test_clean_item_with_only_lens_stale_is_excluded(isolated_repo):
    _write_board(isolated_repo, [
        _clean_item("YYY", alpha_flags=["LENS_STALE"]),
    ])
    out = tools.audit_late_chase_candidates(top_n=10)
    tickers = [r["ticker"] for r in out["candidates"]]
    assert "YYY" not in tickers


# ── extended _lens_late_chase_flags coverage ─────────────────────────────────

@pytest.mark.parametrize(
    "quality,should_flag",
    [
        ("BEARISH_HEDGE", True),
        ("BEARISH_CALL_CHASE", True),
        ("BULLISH_BUT_LATE", True),
        ("SPECULATIVE_CALL_CHASE", True),
        # absence-of-signal labels: still NOT a chase signal in lens flags
        # (they are surfaced separately via the board's propagated alpha_flags)
        ("OPTIONS_NO_EDGE", False),
        ("OPTIONS_MISSING", False),
    ],
)
def test_lens_late_chase_options_quality_map(quality, should_flag):
    lens = {
        "layers": {
            "options": {"options_quality": quality},
        },
    }
    flags = tools._lens_late_chase_flags(lens)
    if should_flag:
        assert f"options_quality:{quality}" in flags
    else:
        assert not any(f.startswith("options_quality:") for f in flags)


# ── lens-only path still works when board has no annotation ──────────────────

def test_lens_only_bearish_hedge_surfaces_when_board_not_annotated(isolated_repo):
    """Older boards without the annotation patch should still surface the
    new chase warnings via the extended lens flag map."""
    _write_board(isolated_repo, [_late_chase_item("ARW")])  # no board annotation
    _write_lens(isolated_repo, "ARW", {
        "label": "Bullish but not buyable yet",
        "layers": {
            "entry_validator": {"view": "Too Extended"},
            "options": {"options_quality": "BEARISH_HEDGE"},
        },
    })
    out = tools.audit_late_chase_candidates(top_n=10)
    rec = next(r for r in out["candidates"] if r["ticker"] == "ARW")
    assert "options_quality:BEARISH_HEDGE" in rec["lens_flags"]


# ── propagation does not duplicate gatekeeper or other tags ──────────────────

def test_gatekeeper_flag_added_once_alongside_propagation(isolated_repo):
    _write_board(isolated_repo, [
        _late_chase_item("ARW", alpha_flags=["OPTIONS_BEARISH_HEDGE"]),
    ])
    gk_path = (isolated_repo / "cache" / "research"
               / "executive_gatekeeper_ARW_latest.json")
    gk_path.write_text(json.dumps({"final_status": "BLOCK"}))
    out = tools.audit_late_chase_candidates(top_n=10)
    rec = next(r for r in out["candidates"] if r["ticker"] == "ARW")
    assert rec["alpha_flags"].count("gatekeeper:BLOCK") == 1
    assert "OPTIONS_BEARISH_HEDGE" in rec["alpha_flags"]

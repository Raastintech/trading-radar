"""Regression test: re-annotating a board must be idempotent.

Bug found in live run: managed flags (LENS_STALE / BOARD_LENS_CONFLICT /
OPTIONS_*) accumulated across runs because annotate appended without
removing the previous run's flags first.
"""
from __future__ import annotations

import time

from core import alpha_discovery as ad


def _make_item(ticker="CVE", action_label="Watch Reclaim", **kw):
    base = {
        "ticker": ticker,
        "action_label": action_label,
        "alpha_score": 75.3,
        "actionable_now": False,
        "validator_state": action_label,
        "bucket": "Early Discovery",
        "alpha_flags": [],
    }
    base.update(kw)
    return base


def _lens(now_ts, *, age_h=1.0, view="Watch Reclaim", quality="OPTIONS_NO_EDGE"):
    return {
        "label": "Bullish but not buyable yet",
        "layers": {
            "entry_validator": {"view": view, "actionable_now": False},
            "options": {"options_quality": quality, "view": quality},
        },
        "_mtime": now_ts - age_h * 3600.0,
    }


def test_reannotate_does_not_duplicate_managed_flags():
    now = time.time()
    items = [_make_item("CVE")]
    pass1 = ad.annotate_alpha_items_with_lens(
        items, now_ts=now,
        read_lens=lambda t: _lens(now, age_h=36.0, quality="BEARISH_HEDGE"),
        read_gatekeeper=lambda t: None,
    )
    assert pass1[0]["alpha_flags"].count("LENS_STALE") == 1
    pass2 = ad.annotate_alpha_items_with_lens(
        pass1, now_ts=now,
        read_lens=lambda t: _lens(now, age_h=36.0, quality="BEARISH_HEDGE"),
        read_gatekeeper=lambda t: None,
    )
    assert pass2[0]["alpha_flags"].count("LENS_STALE") == 1


def test_stale_to_fresh_transition_drops_lens_stale_flag():
    now = time.time()
    items = [_make_item("CVE")]
    stale = ad.annotate_alpha_items_with_lens(
        items, now_ts=now,
        read_lens=lambda t: _lens(now, age_h=36.0),
        read_gatekeeper=lambda t: None,
    )
    assert "LENS_STALE" in stale[0]["alpha_flags"]
    fresh = ad.annotate_alpha_items_with_lens(
        stale, now_ts=now,
        read_lens=lambda t: _lens(now, age_h=1.0),
        read_gatekeeper=lambda t: None,
    )
    assert "LENS_STALE" not in fresh[0]["alpha_flags"]


def test_options_quality_change_drops_old_flag():
    """BEARISH_HEDGE on run 1, BULLISH_CONFIRMING on run 2 — the previous
    options flag must not survive when options quality flips."""
    now = time.time()
    items = [_make_item("CVE")]
    run1 = ad.annotate_alpha_items_with_lens(
        items, now_ts=now,
        read_lens=lambda t: _lens(now, age_h=1.0, quality="BEARISH_HEDGE"),
        read_gatekeeper=lambda t: None,
    )
    assert "OPTIONS_BEARISH_HEDGE" in run1[0]["alpha_flags"]
    run2 = ad.annotate_alpha_items_with_lens(
        run1, now_ts=now,
        # BULLISH_CONFIRMING is NOT in our warning map → no flag emitted
        read_lens=lambda t: _lens(now, age_h=1.0, quality="BULLISH_CONFIRMING"),
        read_gatekeeper=lambda t: None,
    )
    assert "OPTIONS_BEARISH_HEDGE" not in run2[0]["alpha_flags"]


def test_conflict_resolved_drops_board_lens_conflict():
    now = time.time()
    items = [_make_item("OSCR", action_label="Watch Reclaim")]
    conflict = ad.annotate_alpha_items_with_lens(
        items, now_ts=now,
        read_lens=lambda t: _lens(now, age_h=1.0, view="Too Extended"),
        read_gatekeeper=lambda t: None,
    )
    assert conflict[0]["board_lens_conflict"] is True
    assert "BOARD_LENS_CONFLICT" in conflict[0]["alpha_flags"]
    # Lens later agrees with board → conflict should clear
    resolved = ad.annotate_alpha_items_with_lens(
        # Reset action_label back to the original board view to simulate the
        # next board build (the in-memory downgrade is sticky on the item,
        # so feed back the original_action_label).
        [{**conflict[0],
          "action_label": conflict[0]["original_action_label"],
          "actionable_now": False}],
        now_ts=now,
        read_lens=lambda t: _lens(now, age_h=1.0, view="Watch Reclaim"),
        read_gatekeeper=lambda t: None,
    )
    assert resolved[0]["board_lens_conflict"] is False
    assert "BOARD_LENS_CONFLICT" not in resolved[0]["alpha_flags"]


def test_unmanaged_flags_are_preserved_across_runs():
    """Anything outside our managed set (e.g. a flag set by another system)
    must survive an annotation pass intact."""
    now = time.time()
    items = [_make_item("CVE", alpha_flags=["alpha_high_but_not_actionable",
                                              "validator_state:too_extended"])]
    out = ad.annotate_alpha_items_with_lens(
        items, now_ts=now,
        read_lens=lambda t: _lens(now, age_h=1.0, quality="BEARISH_HEDGE"),
        read_gatekeeper=lambda t: None,
    )
    flags = out[0]["alpha_flags"]
    assert "alpha_high_but_not_actionable" in flags
    assert "validator_state:too_extended" in flags
    assert "OPTIONS_BEARISH_HEDGE" in flags

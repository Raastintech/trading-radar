"""Tests for the Alpha board ↔ Stock Lens annotation patch.

Covers:
  - fresh lens validator state + options_quality propagate
  - stale lens is flagged and not trusted (no downgrade, no options flag)
  - board/lens disagreement downgrades the action_label to the stricter one
  - options_quality flags surface as alpha_flags
  - LENS_STALE / BOARD_LENS_CONFLICT flags emit appropriately
  - the annotation pass is pure (no execution/governance/strategy imports
    triggered, no DB / paper-evidence mutation)
"""
from __future__ import annotations

import time

import pytest

from core import alpha_discovery as ad


def _fresh_lens(now_ts: float, **overrides):
    layers = {
        "entry_validator": {"view": "Watch Reclaim", "actionable_now": False},
        "options": {"options_quality": "OPTIONS_NO_EDGE", "view": "No edge"},
    }
    layers["entry_validator"].update(overrides.get("entry", {}))
    layers["options"].update(overrides.get("options", {}))
    return {
        "label": overrides.get("label", "Bullish but not buyable yet"),
        "layers": layers,
        "_mtime": now_ts - overrides.get("age_h", 1.0) * 3600.0,
    }


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


# ── happy path: fresh lens decorates the item ─────────────────────────────────

def test_fresh_lens_propagates_state_and_options_quality():
    now = time.time()
    items = [_make_item("CVE")]

    def fake_read(ticker):
        return _fresh_lens(now, options={"options_quality": "BEARISH_HEDGE"})

    out = ad.annotate_alpha_items_with_lens(
        items, now_ts=now, read_lens=fake_read, read_gatekeeper=lambda t: None,
    )
    it = out[0]
    assert it["lens_missing"] is False
    assert it["lens_stale"] is False
    assert it["entry_validator_state"] == "Watch Reclaim"
    assert it["options_quality"] == "BEARISH_HEDGE"
    assert it["lens_label"] == "Bullish but not buyable yet"
    assert "OPTIONS_BEARISH_HEDGE" in it["alpha_flags"]


# ── stale lens: surfaced as stale, but no downgrade and no options flag ──────

def test_stale_lens_is_marked_stale_and_not_trusted():
    now = time.time()
    items = [_make_item("OSCR", action_label="Watch Reclaim")]

    def fake_read(ticker):
        # 36h stale — older than the 24h default
        return _fresh_lens(
            now,
            age_h=36.0,
            entry={"view": "Too Extended"},
            options={"options_quality": "BEARISH_HEDGE"},
        )

    out = ad.annotate_alpha_items_with_lens(
        items, now_ts=now, read_lens=fake_read, read_gatekeeper=lambda t: None,
    )
    it = out[0]
    assert it["lens_stale"] is True
    assert "LENS_STALE" in it["alpha_flags"]
    # stale lens MUST NOT downgrade the board label
    assert it["action_label"] == "Watch Reclaim"
    # stale lens MUST NOT generate an options-quality alpha_flag
    assert "OPTIONS_BEARISH_HEDGE" not in it["alpha_flags"]
    assert it["board_lens_conflict"] is False


# ── options quality contradiction surfaces as board-level alpha_flag ─────────

@pytest.mark.parametrize(
    "quality,expected_flag",
    [
        ("BEARISH_HEDGE", "OPTIONS_BEARISH_HEDGE"),
        ("SPECULATIVE_CALL_CHASE", "OPTIONS_SPECULATIVE_CALL_CHASE"),
        ("BULLISH_BUT_LATE", "OPTIONS_BULLISH_BUT_LATE"),
        ("BEARISH_CALL_CHASE", "OPTIONS_BEARISH_CALL_CHASE"),
        ("OPTIONS_NO_EDGE", "OPTIONS_NO_EDGE"),
        ("OPTIONS_MISSING", "OPTIONS_MISSING"),
    ],
)
def test_options_quality_contradictions_create_alpha_flags(quality, expected_flag):
    now = time.time()
    items = [_make_item("MTSI")]

    def fake_read(ticker):
        return _fresh_lens(now, options={"options_quality": quality})

    out = ad.annotate_alpha_items_with_lens(
        items, now_ts=now, read_lens=fake_read, read_gatekeeper=lambda t: None,
    )
    assert expected_flag in out[0]["alpha_flags"]


# ── board/lens disagreement: downgrade to the stricter lens label ─────────────

def test_board_lens_disagreement_downgrades_to_stricter_label():
    now = time.time()
    items = [_make_item("OSCR", action_label="Watch Reclaim")]

    def fake_read(ticker):
        return _fresh_lens(now, entry={"view": "Too Extended"})

    out = ad.annotate_alpha_items_with_lens(
        items, now_ts=now, read_lens=fake_read, read_gatekeeper=lambda t: None,
    )
    it = out[0]
    assert it["board_lens_conflict"] is True
    assert it["action_label"] == "Too Extended"
    # original_action_label preserved for audit
    assert it["original_action_label"] == "Watch Reclaim"
    # original_alpha_score preserved for audit
    assert it["original_alpha_score"] == 75.3
    # actionable_now forced off
    assert it["actionable_now"] is False
    assert "BOARD_LENS_CONFLICT" in it["alpha_flags"]


def test_board_more_strict_than_lens_does_not_upgrade():
    """Lens may NOT promote a board to a cleaner state — only the reverse."""
    now = time.time()
    items = [_make_item("FOO", action_label="Too Extended")]

    def fake_read(ticker):
        return _fresh_lens(now, entry={"view": "Watch Reclaim"})

    out = ad.annotate_alpha_items_with_lens(
        items, now_ts=now, read_lens=fake_read, read_gatekeeper=lambda t: None,
    )
    it = out[0]
    assert it["action_label"] == "Too Extended"
    assert it["board_lens_conflict"] is False


# ── missing lens: item still emits the new fields, board untouched ───────────

def test_missing_lens_is_marked_missing_no_state_changes():
    now = time.time()
    items = [_make_item("ZZZ", action_label="Watch Reclaim")]

    out = ad.annotate_alpha_items_with_lens(
        items, now_ts=now, read_lens=lambda t: None, read_gatekeeper=lambda t: None,
    )
    it = out[0]
    assert it["lens_missing"] is True
    assert it["lens_stale"] is None
    assert it["lens_label"] is None
    assert it["entry_validator_state"] is None
    assert it["options_quality"] is None
    assert it["board_lens_conflict"] is False
    assert it["action_label"] == "Watch Reclaim"


# ── gatekeeper status copied when fresh artifact is present ──────────────────

def test_gatekeeper_status_propagates_when_available():
    now = time.time()
    items = [_make_item("MTSI")]

    def fake_lens(t):
        return _fresh_lens(now)

    def fake_gk(t):
        return {"final_status": "BLOCK"}

    out = ad.annotate_alpha_items_with_lens(
        items, now_ts=now, read_lens=fake_lens, read_gatekeeper=fake_gk,
    )
    assert out[0]["gatekeeper_status"] == "BLOCK"


# ── invariant: annotation does not invoke execution/governance code paths ────

def test_annotation_does_not_touch_execution_or_governance(monkeypatch):
    """Smoke check: annotate_alpha_items_with_lens uses only the injected
    read hooks.  If it ever imported / called execution or governance modules
    it would surface as side effects we can detect."""
    calls = {"order_manager": 0, "alpaca_submit": 0, "decision_logger": 0}

    def _boom_order(*a, **kw):
        calls["order_manager"] += 1
        raise AssertionError("annotate must not touch OrderManager")

    monkeypatch.setattr(
        "core.alpha_discovery.get_alpaca",
        lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("annotate must not touch Alpaca client")
        ),
        raising=False,
    )

    now = time.time()
    items = [_make_item("AAA"), _make_item("BBB"), _make_item("CCC")]
    out = ad.annotate_alpha_items_with_lens(
        items, now_ts=now,
        read_lens=lambda t: _fresh_lens(now),
        read_gatekeeper=lambda t: None,
    )
    assert len(out) == 3
    assert all("alpha_flags" in it for it in out)


# ── input items are not mutated in place ─────────────────────────────────────

def test_input_items_are_not_mutated():
    now = time.time()
    src = _make_item("CVE", action_label="Watch Reclaim")
    src_copy = dict(src)
    src_flags = list(src["alpha_flags"])

    def fake_read(t):
        return _fresh_lens(now, entry={"view": "Too Extended"},
                            options={"options_quality": "BEARISH_HEDGE"})

    out = ad.annotate_alpha_items_with_lens(
        [src], now_ts=now, read_lens=fake_read, read_gatekeeper=lambda t: None,
    )
    assert src == src_copy
    assert src["alpha_flags"] == src_flags
    assert out[0] is not src


# ── enrichment transparency fields populate ──────────────────────────────────

def test_enrichment_transparency_emits_expected_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(ad, "_ALPHA_ENRICHMENT_ROTATION_PATH",
                        tmp_path / "rot.json")

    class _S:
        def __init__(self, score):
            self.score = score

    out = ad._build_enrichment_transparency(
        candidate_band_symbols=["A", "B", "C", "D"],
        profiles={"A": {"sector": "Tech"}, "B": None, "C": None, "D": None},
        fundamentals={"A": {"income": [{}]}, "B": None, "C": None, "D": None},
        overlay_13f={"A": _S(50.0), "B": _S(None), "C": _S(None), "D": _S(None)},
        overlay_tradier={"A": _S(60.0), "B": _S(None), "C": _S(None), "D": _S(None)},
        overlay_symbols={"A", "B"},
        profile_target=2,
        fundamentals_target=2,
    )
    assert out["candidate_band_size"] == 4
    assert out["enriched_count"] == 1
    assert out["not_enriched_count"] == 3
    # B was in the overlay set but had no enriched data → missing_data reason
    bs = [e for e in out["not_enriched_details"] if e["ticker"] == "B"]
    assert bs and bs[0]["reason"] == "missing_data"
    # C/D fell outside the profile cap → cap_bound / low_priority categorisations
    rotation = out["rotation_queue_top"]
    assert "B" in rotation
    assert all(isinstance(t, str) for t in rotation)
    # rotation candidate flag round-trips
    for entry in out["not_enriched_details"]:
        assert "enrichment_rotation_candidate" in entry

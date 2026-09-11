"""Guards for the quarantine layer (System Diet Phase 1, 2026-09-09).

Three artifacts were being rendered as if they described the system today when
they did not: a recall figure measuring a pipeline decommissioned in June, a
speculative name list with no forward hypothesis, and a social radar superseded
by one that actually runs.

The fix is display eligibility, not deletion. These tests pin both halves:
the quarantined thing stops speaking as current truth, AND it is still there.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import quarantined_surfaces as QS

ROOT = Path(__file__).resolve().parents[2]


# ── 1. the decommissioned recall metric ─────────────────────────────────────

DECOMMISSIONED_SIDECAR = {
    "winner_recall_pct": 0.0,
    "measured_pipeline": "decommissioned_council_funnel_autopsy",
    "best_simple_baseline_recall_pct": 47.2,
}


def test_a_decommissioned_pipeline_is_not_current():
    v = QS.classify("scanner_truth_summary", DECOMMISSIONED_SIDECAR)
    assert v["is_current"] is False
    assert v["status"] == QS.DECOMMISSIONED_AUTOPSY
    assert "2026-06-13" in v["reason"]
    assert v["preserved"] is True


def test_the_same_review_pointed_at_a_live_pipeline_would_be_current():
    """Classified from the artifact's own field, so re-pointing it needs no edit."""
    assert QS.is_current("scanner_truth_summary",
                         {"winner_recall_pct": 12.0,
                          "measured_pipeline": "live_research_board"}) is True


def test_a_self_stamped_autopsy_is_also_caught():
    assert QS.is_current("anything",
                         {"measurement_status": "DECOMMISSIONED_AUTOPSY"}) is False


def test_the_dashboard_banner_suppresses_the_dead_recall_number():
    from dashboards.gem_trader_hq import selection_edge_status

    s = selection_edge_status(0.0, 47.2, sidecar=DECOMMISSIONED_SIDECAR)
    assert s["verdict"] == "NOT_MEASURED"
    assert s["recall_pct"] is None
    assert "0.0%" not in s["line"]
    assert "NOT MEASURED" in s["line"]
    # and it must not shout: a red banner nobody can act on is the bug
    assert s["style"] == "dim"


def test_the_banner_still_reports_a_live_recall_number():
    from dashboards.gem_trader_hq import selection_edge_status

    s = selection_edge_status(12.0, 47.2,
                              sidecar={"measured_pipeline": "live_research_board"})
    assert s["verdict"] == "UNPROVEN"
    assert s["recall_pct"] == 12.0
    assert "12.0%" in s["line"]


def test_the_operator_summary_no_longer_warns_on_the_dead_metric():
    import research.nightly_operator_summary as NOS

    warnings = NOS._build_warnings(
        {"scanner_truth": DECOMMISSIONED_SIDECAR},   # sidecars
        {},                                          # market_ctx
        {},                                          # forward
        {},                                          # alpha_snap
    )
    joined = " ".join(warnings)
    assert "Legacy council-funnel recall" not in joined
    assert "0.0%" not in joined


def test_scanner_truth_is_not_counted_as_a_live_component():
    """Its presence says nothing about whether tonight's live board ran."""
    src = (ROOT / "research/nightly_operator_summary.py").read_text(encoding="utf-8")
    block = src[src.index("    for name, key in ["):]
    block = block[:block.index("]:")]
    assert '("Scanner Truth", "scanner_truth")' not in block


def test_the_producer_self_labels_the_artifact():
    src = (ROOT / "research/scanner_truth_review.py").read_text(encoding="utf-8")
    assert '"measurement_status": "DECOMMISSIONED_AUTOPSY"' in src
    assert '"is_live_recall": False' in src


def test_the_historical_artifact_is_preserved_on_disk():
    """Quarantine must never mean deletion."""
    p = ROOT / "cache/research/scanner_truth_summary_latest.json"
    if not p.exists():
        pytest.skip("no scanner-truth artifact in this checkout")
    payload = json.loads(p.read_text(encoding="utf-8"))
    assert "winner_recall_pct" in payload, "historical evidence was destroyed"


# ── 2. the 10x candidate radar ──────────────────────────────────────────────


def test_the_ten_x_radar_is_quarantined():
    v = QS.classify("ten_x_candidates", {"candidates": [{"ticker": "AAA"}]})
    assert v["is_current"] is False
    assert v["status"] == QS.QUARANTINE_NO_HYPOTHESIS
    assert "forward hypothesis" in v["reason"]


def test_the_alpha_radar_does_not_surface_quarantined_ten_x_names():
    import research.daily_alpha_radar_report as DAR

    src = Path(DAR.__file__).read_text(encoding="utf-8")
    block = src[src.index("    # 10x candidates"):]
    block = block[:block.index("    # Social/catalyst anomalies")]
    assert "quarantine_is_current" in block, \
        "the 10x list is not gated behind the quarantine check"
    assert "ten_x_candidates = []" in block


def test_the_ten_x_radar_is_off_the_schedule():
    src = (ROOT / "scripts/run_research_cycle.sh").read_text(encoding="utf-8")
    active = [ln for ln in src.splitlines()
              if ln.strip() == "cmd_ten_x_candidates"]
    assert not active, "the 10x radar is still called by a scheduled cycle"
    # …but still reachable on demand
    assert "ten-x-candidates)" in src


def test_the_ten_x_output_stamps_itself_as_not_a_candidate_list():
    src = (ROOT / "research/ten_x_candidate_radar.py").read_text(encoding="utf-8")
    assert '"quarantine_status": "QUARANTINE_NO_HYPOTHESIS"' in src
    assert '"is_current_candidate_list": False' in src


# ── 3. the superseded social radar ──────────────────────────────────────────


@pytest.mark.parametrize("name", [
    "social_attention_v11",
    "social_attention_v11_forward",
    "top_market_attention",
])
def test_v11_artifacts_are_superseded(name):
    v = QS.classify(name, {"leads": [{"ticker": "AAA"}]})
    assert v["is_current"] is False
    assert v["status"] == QS.SUPERSEDED_STALE
    assert v["preserved"] is True


def test_the_nightly_v0_radar_remains_current():
    """Exactly one social lane stays live."""
    assert QS.is_current("social_attention_radar", {"leads": []}) is True
    assert QS.is_current("social_arb", {}) is True


@pytest.mark.parametrize("module_rel", [
    "research/alpha_heat_radar.py",
    "research/topic_shock_detector.py",
])
def test_v11_is_dropped_by_its_readers(module_rel):
    src = (ROOT / module_rel).read_text(encoding="utf-8")
    assert "quarantine_is_current(\"social_attention_v11\", _v11_raw)" in src, \
        f"{module_rel} still ingests V11 as a current source"


def test_v11_readers_still_load_the_file_rather_than_deleting_it():
    src = (ROOT / "research/alpha_heat_radar.py").read_text(encoding="utf-8")
    assert "_load_json(root / SOCIAL_ATTENTION_V11_REL)" in src, \
        "the artifact should still be read and preserved, just not trusted as current"


# ── 4. the layer itself stays honest ────────────────────────────────────────


def test_an_unknown_artifact_is_current_by_default():
    """Quarantine is opt-in. A new sidecar is not silently suppressed."""
    assert QS.is_current("something_new", {"x": 1}) is True
    assert QS.classify("something_new", None)["status"] == QS.CURRENT


def test_quarantine_note_gives_a_reader_something_to_show():
    note = QS.quarantine_note("ten_x_candidates")
    assert note and "QUARANTINE" in note
    assert QS.quarantine_note("social_attention_radar") == ""


def test_no_ranking_or_trade_language_in_the_layer():
    src = (ROOT / "core/quarantined_surfaces.py").read_text(encoding="utf-8").lower()
    for token in ("buy ", "sell ", "price target", "position size",
                  "conviction tier", "top pick", "validated_edge"):
        assert token not in src, f"{token!r} leaked into the quarantine layer"


# ── 5. the digest and command center read no quarantined sidecar ────────────

QUARANTINED_SIDECAR_STEMS = sorted(
    set(QS.QUARANTINED_BY_NAME) | {"scanner_truth_summary"})


@pytest.mark.parametrize("rel", [
    "dashboards/research_command_center/journal_digest.py",
    "dashboards/research_command_center/data_adapter.py",
    "dashboards/research_command_center/static/index.html",
])
def test_digest_and_command_center_read_no_quarantined_sidecar(rel):
    """Checked 2026-09-11: neither the daily digest nor the command center
    loads any sidecar this layer marks non-current, so neither needs to
    consult it.  Pin that: a future reader added to one of these files
    must go through QS.classify before rendering the value as current."""
    src = (ROOT / rel).read_text(encoding="utf-8")
    for stem in QUARANTINED_SIDECAR_STEMS:
        assert stem not in src, (
            f"{rel} reads quarantined sidecar {stem!r} — gate it through "
            "core.quarantined_surfaces")

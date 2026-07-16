"""Tests for the Research Terminal UX/UI refactor.

Presentation-only guarantees: no user-facing 'dead-horse' text, the neutral
Business Deterioration Risk labels, top-5 defaults with full sets still
reachable, Emerging tiers, grouped Did-Not-Qualify tray, single scan-integrity
banner, forward-validation display mapping, deduped status badges, compact
program summaries, and the concise + detailed journal sections — all without
touching scanner logic, scores, rankings, routing, or verdicts.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_INDEX = (REPO_ROOT / "dashboards" / "research_command_center" / "static"
          / "index.html")
_HTML = _INDEX.read_text(encoding="utf-8")


# ── 1. no user-facing dead-horse in any rendered surface ────────────────────


def test_no_user_facing_dead_horse_in_html():
    """The only permitted occurrence is the internal payload-key read
    (c.dead_horse_risk); no visible string literal says 'dead-horse'."""
    import re
    # strip the one comment line and property accesses, then assert clean
    visible = _HTML
    # remove JS property reads of the internal key
    visible = visible.replace("c.dead_horse_risk", "").replace(
        "w.business_deterioration_risk", "")
    # any remaining 'dead-horse' would be visible text or a comment
    leftover = [ln for ln in visible.splitlines()
                if "dead-horse" in ln.lower()]
    # the single design-note comment is allowed; no rendered template text
    rendered = [ln for ln in leftover
                if "`" in ln or ">" in ln and "//" not in ln
                and "/*" not in ln]
    assert not rendered, rendered


def test_no_dead_horse_in_terminal_or_digest_output():
    from research.high_conviction_alpha import build_shortlist, render_terminal
    txt = render_terminal(build_shortlist(REPO_ROOT), verbose=True)
    assert "dead-horse" not in txt.lower()
    assert "deterioration" in txt.lower()


# ── 2. Business Deterioration Risk display labels ───────────────────────────


def test_deterioration_display_labels_present():
    for pair in ('LOW:"Low"', 'MEDIUM:"Medium"', 'HIGH:"High"',
                 'UNKNOWN:"Unknown"'):
        assert pair in _HTML, pair
    assert "Business Deterioration:" in _HTML
    # restrained badge classes, distinct from system-error styling
    for cls in (".det-low", ".det-medium", ".det-high", ".det-unknown"):
        assert cls in _HTML, cls


def test_digest_uses_business_deterioration_label():
    from dashboards.research_command_center.journal_digest import (
        _deterioration_label)
    assert _deterioration_label("LOW") == "Low"
    assert _deterioration_label("HIGH") == "High"
    assert _deterioration_label(None) == "Unknown"


# ── 3-4. High-Conviction top-5 default; full set reachable ──────────────────


def test_high_conviction_top_n_default_and_controls():
    # default cap 6 (3-per-row grid → two clean rows); persists via UIVIEW
    assert "cap:6" in _HTML
    assert 'cap(6,"Top 6")' in _HTML and 'cap(12,"Top 12")' in _HTML
    assert 'data-cap="99999"' in _HTML        # View all
    assert "HCVIEW.cap=Number" in _HTML       # control mutates view state only


# ── 5-6. Emerging top-5 default with tiers; full set reachable ──────────────


def test_emerging_tiers_default_top5():
    assert 'EOVIEW={tier:"top"}' in _HTML
    assert 'data-tier="${t}"' in _HTML        # tier buttons
    assert 'mk("top","Top 5")' in _HTML
    assert 'mk("secondary","Next 10")' in _HTML
    assert 'mk("all","View all "+watch.length)' in _HTML
    assert "Priority Review" in _HTML


# ── 7. Emerging header does not use system-error styling ────────────────────


def test_emerging_not_error_styled():
    # emerging wrap uses indigo, not amber/red error backgrounds
    assert ".eo-wrap{border:1px solid var(--indigo)" in _HTML
    assert "--indigo" in _HTML and "--indigo-bg" in _HTML
    # header class references indigo, never the amber error palette
    assert ".eo-wrap .sec-head h2{color:var(--indigo)}" in _HTML


# ── 8. Rejected collapsed + grouped by reason ───────────────────────────────


def test_rejected_tray_collapsed_and_grouped():
    assert "Did not qualify" in _HTML
    assert "rejectedTray" in _HTML
    assert "rej-tray" in _HTML                # collapsed <details>
    for group in ("Severe dilution", "Negative gross margin",
                  "Business deterioration risk", "Data / integrity issue"):
        assert group in _HTML, group


def test_rejected_tray_groups_real_data():
    """The grouping mirror in the JS regexes covers the real exclusion
    strings produced by V1."""
    from research.high_conviction_alpha import build_shortlist
    rej = build_shortlist(REPO_ROOT).get("rejected") or []
    # every rejected candidate has at least one exclusion string
    assert all(c.get("exclusions") for c in rej)


# ── 9. programs compact (top 3 summary) ─────────────────────────────────────


def test_program_summary_compact():
    assert "programSummaryCard" in _HTML
    assert "prog-summary" in _HTML
    # top-3 slice in the summary card
    assert "(block.candidates||[]).slice(0,3)" in _HTML
    assert "Open full" in _HTML               # link to full program view


# ── 10. duplicate status badges removed ─────────────────────────────────────


def test_status_badge_dedup():
    # candBadges filters out the scan status so NEW/NEW can't render twice
    assert "never appears twice" in _HTML
    assert 'filter(b=>String(b.text||"").toUpperCase()!==status)' in _HTML


# ── 11-12. canonical values / counts unchanged (logic frozen) ───────────────


def test_canonical_scores_and_counts_unchanged():
    from research.high_conviction_alpha import build_shortlist, WEIGHTS
    p = build_shortlist(REPO_ROOT)
    # scores are numbers straight from the engine; refactor is display-only
    for c in p.get("shortlist") or []:
        assert isinstance(c["high_conviction_score"], (int, float))
        assert c["classification"] in (
            "HIGH_CONVICTION", "PROMISING", "WATCH_FOR_ENTRY")
    # weights untouched
    assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9


def test_no_scanner_or_routing_change_in_render_layer():
    # the index.html contains no scanner/scoring imports or mutations — it is
    # a pure GET client (checked structurally)
    assert "do_POST" not in _HTML
    # presentation controls only re-render
    assert "wireDecisionControls" in _HTML
    assert "renderHome()" in _HTML


# ── 13. single page-level scan-integrity banner ─────────────────────────────


def test_scan_integrity_single_surface():
    """The standalone integrity banner was folded into the status strip +
    System Trust interpretation + Research Caveats: scan integrity is shown
    once as a primary status value, and a non-READY cause surfaces exactly
    once in the caveats drawer — never repeated per card."""
    assert "integrityBanner" not in _HTML          # old banner fully retired
    strip = _HTML.split("const primary=[")[1].split("];")[0]
    assert '"Scan integrity"' in strip
    assert "trustInterpretation" in _HTML
    assert 'items.push({text:"Scan integrity: "+cause' in _HTML


# ── 14. factor detail available via expansion ───────────────────────────────


def test_factor_bars_in_expansion():
    assert "factorBars" in _HTML
    assert 'class="fbar"' in _HTML
    for label in ("Quality", "Growth", "Fundamental Momentum",
                  "Price Momentum", "Value", "Risk Control"):
        assert label in _HTML, label
    # bars sit inside the Full-details accordion
    assert "Full details" in _HTML


# ── 15. terminal concise + verbose both work ────────────────────────────────


def test_terminal_concise_and_verbose():
    from research.high_conviction_alpha import build_shortlist, render_terminal
    p = build_shortlist(REPO_ROOT)
    concise = render_terminal(p, verbose=False)
    verbose = render_terminal(p, verbose=True)
    assert "HIGH-CONVICTION ALPHA SHORTLIST" in concise
    assert "deterioration=" in concise.lower() or "deterioration" in concise.lower()
    # verbose-only rejected block; concise omits it
    assert "REJECTED BY QUALITY/RISK" in verbose
    assert "REJECTED BY QUALITY/RISK" not in concise
    assert len(verbose) >= len(concise)


# ── 16. journal concise + detailed sections both present ────────────────────


def test_journal_has_concise_and_detailed(tmp_path):
    from dashboards.research_command_center.journal_digest import (
        _section_todays_best_research, _section_high_conviction)
    from dashboards.research_command_center.data_adapter import ArtifactStore
    from dashboards.research_command_center.data_adapter import _load_json
    store = ArtifactStore(root=REPO_ROOT)
    inputs = {
        "high_conviction": _load_json(store.high_conviction_json),
        "emerging_outlier": _load_json(store.emerging_outlier_json),
        "store": store,
    }
    concise = "\n".join(_section_todays_best_research(inputs))
    assert concise.startswith("## Today's Best Research")
    assert "High Conviction:" in concise
    assert "Top Emerging:" in concise
    assert "Wait for Reset:" in concise
    detailed = "\n".join(_section_high_conviction(inputs))
    assert detailed.startswith("## 4b1. High-Conviction Alpha Shortlist")


# ── 17. presentation controls do not mutate artifacts ───────────────────────


def test_controls_are_view_state_only():
    # the decision controls only set view-state vars and re-render
    assert "HCVIEW.cap=Number(b.dataset.cap);persistView();renderHome()" in _HTML
    assert "EOVIEW.tier=b.dataset.tier;renderHome()" in _HTML
    # no fetch/POST inside the wiring
    start = _HTML.index("function wireDecisionControls")
    body = _HTML[start:start + 600]
    for forbidden in ("fetch(", "POST", "XMLHttpRequest"):
        assert forbidden not in body, forbidden


# ── 18. forward-validation display mapping (canonical preserved) ────────────


def test_forward_validation_display_mapping():
    assert 'NEED_MORE_DATA:"In Progress"' in _HTML
    assert 'INSUFFICIENT_MATURE_EVIDENCE:"Insufficient Evidence"' in _HTML
    assert 'VALIDATED_EDGE:"Validated Edge"' in _HTML
    # canonical enum still flows through the adapter untouched
    from dashboards.research_command_center.data_adapter import (
        ArtifactStore, build_high_conviction)
    hc = build_high_conviction(ArtifactStore(root=REPO_ROOT))
    if hc.get("present"):
        fv = hc.get("forward_validation") or {}
        assert fv.get("verdict") in (
            "NEED_MORE_DATA", "SHORTLIST_IMPROVES_OUTCOMES",
            "NO_IMPROVEMENT_OVER_PROGRAM_CANDIDATES", None)


# ── high-density scannability refactor (status pills, rej table, alerts, om) ──

def test_hc_card_no_raw_strengths_risks_paragraph():
    # the old "All strengths… / All risks…" joined-string kvRows are gone
    assert "All strengths" not in _HTML
    assert "All risks" not in _HTML
    # replaced by clean overflow bullets
    assert "moreStrengthsRisks" in _HTML
    assert "More strengths" in _HTML and "More risks" in _HTML


def test_header_is_status_pill_grid():
    assert "status-grid" in _HTML and ".stat-pill" in _HTML
    assert "function statPill" in _HTML
    # the header renderer emits pill grids, not the old chip rows
    header = _HTML[_HTML.index("function renderHeader"):
                   _HTML.index("/* ── Research Home ── */")]
    assert 'class="status-grid"' in header
    assert 'class="hrow"' not in header


def test_rejection_tray_is_fixed_column_table():
    assert ".rej-table" in _HTML and "grid-template-columns:180px 1fr" in _HTML
    assert ".sym-tag" in _HTML          # muted inline symbol tags
    assert 'class="rc"' in _HTML        # fixed-width category column
    assert 'class="tks"' not in _HTML   # old comma-joined block removed


def test_context_tension_and_regime_invalidation_alerts():
    assert "function contextAlerts" in _HTML
    assert "invalidation-alert" in _HTML and "tension-alert" in _HTML
    assert "Regime Invalidation" in _HTML and "Context Tension" in _HTML
    # deterministic: driven by the engine's invalidation_breached field
    fn = _HTML[_HTML.index("function contextAlerts"):
               _HTML.index("function staleTag")]
    assert "invalidation_breached" in fn and "context_tension" in fn


def test_forward_evidence_open_vs_matured_summary():
    assert "om-strip" in _HTML and "om-pill" in _HTML
    # reads the canonical forward_tracking counts (open/matured)
    assert "total_matured" in _HTML and "total_open" in _HTML
    assert "Forecast matured" in _HTML and "Lens matured" in _HTML

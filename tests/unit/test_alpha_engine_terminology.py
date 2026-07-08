"""
tests/unit/test_alpha_engine_terminology.py — decommissioned-strategy
terminology guards.

Voyager, Sniper, Remora, and the Short sleeves were permanently shut down
(2026-06-13). The alpha engine is a research-only market intelligence engine
(production alpha scanner, strict production gates, shadow recall lane,
simple-RS baseline, loose recall lane, forward evidence tracker, journal
audit layer, system repair tasks). These tests pin that framing:

  1. dashboard display helpers never expose the old sleeves as active,
  2. journal audit output templates use neutral alpha-engine terminology,
  3. scanner recall diagnostics report production_structural /
     production_breakout lane labels,
  4. legacy names survive only in explicitly marked internal compatibility
     mappings,
  5. no active report module calls anything a "trading strategy" or presents
     the old strategy system as active.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from core.strategy_registry import (  # noqa: E402
    active_paper_strategies, active_scanner_keys, is_active_paper_strategy)
from dashboards.gem_trader_hq import _strategy_fit_label  # noqa: E402
from research import scanner_recall_diagnostics as SRD  # noqa: E402
from research import scanner_recall_diagnostics_batch as BATCH  # noqa: E402

LEGACY_NAMES = ("voyager", "sniper", "remora")

# Modules that write user-facing reports for the active research engine.
ACTIVE_REPORT_MODULES = [
    REPO / "research" / "scanner_recall_diagnostics.py",
    REPO / "research" / "scanner_recall_diagnostics_batch.py",
    REPO / "research" / "journal_audit_reviewer.py",
    REPO / "research" / "nightly_operator_summary.py",
    REPO / "research" / "daily_alpha_radar_report.py",
    REPO / "research" / "mcp_audit_orchestrator.py",
    REPO / "scripts" / "generate_research_journal_digest.py",
]


# ── 1. dashboard never presents old sleeves as active ────────────────────────

def test_registry_confirms_no_active_strategies():
    assert active_paper_strategies() == ()
    assert active_scanner_keys() == ()
    for s in ("SNIPER", "VOYAGER", "SHORT", "REMORA"):
        assert not is_active_paper_strategy(s)


def test_dashboard_fit_labels_are_neutral():
    for code in ("SNP", "SNIPER", "VOY", "VOYAGER", "SHA", "SHORT",
                 "REM", "REMORA", "SHORT_B", "SHB"):
        label = _strategy_fit_label(code).lower()
        for name in LEGACY_NAMES + ("short a", "short b resemblance"):
            assert name not in label, f"{code} → {label!r} leaks {name!r}"
        assert "research" in label      # every archetype label carries the framing


def test_dashboard_participation_strip_uses_lane_labels():
    src = (REPO / "dashboards" / "gem_trader_hq.py").read_text()
    assert "breakout_lane_flow=" in src and "structural_lane_flow=" in src
    # the old strategy-named render labels must not appear in any f-string
    assert re.search(r'f?"[^"\n]*sniper_flow=\{', src) is None
    assert re.search(r'f?"[^"\n]*voyager_flow=\{', src) is None
    # the research-classification prompt no longer name-drops the sleeves
    assert "SNIPER-style" not in src and "VOYAGER-style" not in src


# ── 2. journal audit layer uses neutral terminology ──────────────────────────

def test_journal_audit_templates_are_neutral():
    src = (REPO / "research" / "journal_audit_reviewer.py").read_text()
    for name in LEGACY_NAMES:
        hits = [l for l in src.splitlines()
                if name in l.lower() and "SNIPER_ENV_PATH" not in l]
        assert hits == [], f"journal audit leaks {name!r}: {hits}"
    assert "trading strategy" not in src.lower()


# ── 3. diagnostics report neutral lane labels ────────────────────────────────

def test_diagnostics_lane_constants():
    assert SRD.LANE_STRUCTURAL == "production_structural"
    assert SRD.LANE_BREAKOUT == "production_breakout"
    assert BATCH._LANE_TO_REASONS_KEY == {
        "production_structural": "voyager_reasons",
        "production_breakout": "sniper_reasons"}


def test_diagnostics_reject_output_uses_neutral_lanes():
    world = {
        "__spy__": {"fwd": {5: 0.0, 10: 0.0, 20: 0.01}, "r20": 0.0},
        "__meta__": {"excluded_stale": 0, "excluded_data_suspect": 0},
        "T1": {"strict_pass": False, "loose_pass": True,
               "voyager_reasons": ["too_extended"],
               "sniper_reasons": ["no_breakout"],
               "rs20": 0.1, "r40": 0.2, "mae20": -0.02,
               "fwd": {5: 0.05, 10: 0.1, 20: 0.3}},
    }
    counts = SRD._reject_counts(world, {"T1"}, {"T1"})
    assert {r["lane"] for r in counts} == {"production_structural",
                                           "production_breakout"}
    top = SRD._top_rejected(world, {"T1"}, {"T1"}, set(), {}, 5)
    assert top[0]["reject_reasons"] == ["production_structural:too_extended",
                                        "production_breakout:no_breakout"]
    # loose-threshold param keys are neutral too
    assert not any(k.startswith(("voyager", "sniper"))
                   for k in SRD.LOOSE_THRESHOLDS)


# ── 4. legacy names only inside marked compatibility mappings ────────────────

_ALLOWED_LEGACY_LINE = re.compile(
    r"(SNIPER_ENV_PATH"                      # credentials env-var convention
    r"|voyager_reasons|sniper_reasons"       # internal record keys (mapped at output)
    r"|loose_voyager|loose_sniper"           # internal function names (rule: may remain)
    r"|voyager_structural|sniper_breakout"   # drift-guarded production-gate mirrors
    r"|LOOSE_VOY_|LOOSE_SNI_|VOY_|SNI_"      # internal constants
    r"|legacy|Legacy|LEGACY|decommissioned|NOT active|internal|Internal"
    r"|drift-guarded|mirror|sleeve rules)")


def test_legacy_names_only_in_marked_compat_lines():
    for path in (REPO / "research" / "scanner_recall_diagnostics.py",
                 REPO / "research" / "scanner_recall_diagnostics_batch.py"):
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if any(name in line.lower() for name in LEGACY_NAMES):
                assert _ALLOWED_LEGACY_LINE.search(line), \
                    f"{path.name}:{n} unmarked legacy name: {line.strip()}"


# ── 5. no active report presents the old strategy system as active ───────────

def test_no_active_report_says_trading_strategy():
    for path in ACTIVE_REPORT_MODULES:
        src = path.read_text().lower()
        assert "trading strategy" not in src, path.name
        assert "active strategy" not in src.replace("no active strateg", ""), path.name


def test_batch_artifact_text_has_no_legacy_strategy_names(monkeypatch, tmp_path):
    """End-to-end: a rendered batch report contains no legacy sleeve names."""
    from tests.unit.test_scanner_recall_diagnostics import _world
    _world(monkeypatch, tmp_path)
    res = BATCH.build(grid=[21, 26])
    txt = "\n".join(BATCH._render_txt(res)).lower()
    for name in LEGACY_NAMES:
        assert name not in txt
    assert "production_structural" in txt or "production_breakout" in txt

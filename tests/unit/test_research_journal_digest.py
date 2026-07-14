"""Tests for the daily research journal digest generator.

Covers: dry-run/append/duplicate/force behavior, graceful degradation on
missing artifacts, fundamental overlay, evidence-snapshot shape, the
seven-section note body, forbidden-wording absence, write isolation
(journal.jsonl only), top-N limiting, and status selection.

All tests run against temp directories — the production journal is never
touched.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from dashboards.research_command_center.data_adapter import ArtifactStore
from dashboards.research_command_center.journal_digest import (
    BASE_TAGS,
    build_digest_entry,
    compute_digest_key,
    collect_inputs,
    find_existing_digest,
)
from scripts.generate_research_journal_digest import main as digest_main

REPO_ROOT = Path(__file__).resolve().parents[2]

FORBIDDEN = re.compile(
    r"buy now|sell now|entry price|stop loss|target price|position size|"
    r"trade recommendation|paper signal|live signal|auto trade|place order|"
    r"submit order|trade idea generator", re.IGNORECASE)

SECTION_HEADINGS = [
    "# Daily Research Digest",
    "## 1. Data Quality",
    "## 2. Scanner / Why Names Appeared",
    "## 3. Sector / Regime",
    "## 4. Forward Evidence",
    "## 5. Fundamental Overlay",
    "## 6. Journal Review Queue",
    "## 7. Final Finding",
]

SNAPSHOT_KEYS = [
    "mode", "phase4b_status", "verdict", "market_regime", "data_quality",
    "total_candidates", "high_priority", "reset_reclaim",
    "extended_crowded_count", "data_quarantine_count", "matured_5d",
    "matured_10d", "benchmark_status", "top_review_candidates",
]


# ── fixtures ─────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


def write_fixture_artifacts(root: Path, *,
                            high_priority=("GOODCO",),
                            warnings=(),
                            verdict="NEED_MORE_DATA",
                            stale_skipped=2,
                            suspect_skipped=1,
                            n_watchlist=12) -> None:
    research = root / "cache" / "research"
    now = _now_iso()

    tickers = [f"T{i:02d}" for i in range(n_watchlist)]
    watchlist = [{
        "ticker": t,
        "research_score": 100 - i,
        "why_appeared": f"Strong relative strength for {t}",
        "sector": "Technology" if i % 2 == 0 else "Healthcare",
    } for i, t in enumerate(tickers)]

    _write(research / "nightly_operator_summary_latest.json", {
        "generated_at": now,
        "system_mode": "RESEARCH_ONLY",
        "overall_status": {"overall": "PASS", "nightly_completed": True},
        "market_context": {
            "regime": "Chop / Range", "confidence": "low",
            "bias_5d": "constructive", "bias_10d": "constructive",
            "bias_30d": "mixed",
            "leading_sectors": ["Technology"], "weak_sectors": ["Energy"],
            "research_posture": "stalking mode — human review required",
        },
        "alpha_snapshot": {
            "high_priority_tickers": list(high_priority),
            "reset_watch_tickers": ["RSTA", "RSTB"],
            "reclaim_watch_tickers": [],
            "extended_crowded_count": 3,
            "extended_crowded_tickers": ["EXT1", "EXT2", "EXT3"],
            "data_quarantine_count": 1,
        },
        "best_research_names": {
            "primary": [{"ticker": t} for t in high_priority],
            "secondary": [{"ticker": "T00"}, {"ticker": "T01"}],
            "watchlist": [{"ticker": "T02"}],
        },
        "forward_evidence": {"benchmark_readiness": "READY",
                             "verdict": verdict},
        "warnings": list(warnings),
    })
    _write(research / "daily_alpha_radar_latest.json", {
        "generated_at": now,
        "total_candidates": n_watchlist,
        "priority_counts": {"EXTENDED_CROWDED": 3, "DATA_QUARANTINE": 1,
                            "HIGH_PRIORITY_RESEARCH": len(high_priority)},
        "priority_tickers": {
            "HIGH_PRIORITY_RESEARCH": list(high_priority),
            "RESET_WATCH": ["RSTA", "RSTB"],
            "EXTENDED_CROWDED": ["EXT1", "EXT2", "EXT3"],
            "DATA_QUARANTINE": ["QUAR1"],
        },
        "quarantine_breakdown": {"INSUFFICIENT_HISTORY": 1},
    })
    _write(research / "research_scanner_latest.json", {
        "generated_at": now,
        "universe_size": 100,
        "stale_price_skipped_count": stale_skipped,
        "data_suspect_skipped_count": suspect_skipped,
        "watchlist": watchlist,
        "watchlist_size": len(watchlist),
    })
    _write(research / "research_forward_latest.json", {
        "generated_at": now,
        "total_history_entries": 50,
        "overall": {"total_entries": 50, "matured_5d_entries": 20,
                    "matured_entries": 8, "sample_status": "PROVISIONAL",
                    "verdict": verdict},
        "benchmark_readiness": {"spy_available": True, "qqq_available": True},
    })
    _write(research / "research_universe_build_latest.json", {
        "generated_at": now, "total_universe_size": 100,
    })
    _write(research / "moment_of_truth_2026_06_27.json", {
        "generated_at": "2026-06-27T14:55:00+00:00",
        "verdict": "INCONCLUSIVE",
        "phase_4b_allowed": False,
    })

    # fundamentals for the first ticker only — the rest degrade gracefully
    _write(root / "cache" / "fundamentals" / "GOODCO.json", {
        "ticker": "GOODCO",
        "income": [{"date": "2026-04-30", "revenue": 100.0,
                    "grossProfit": 60.0, "operatingIncome": 10.0,
                    "netIncome": 5.0, "weightedAverageShsOutDil": 100}],
        "balance": [{"date": "2026-04-30",
                     "cashAndShortTermInvestments": 50.0,
                     "totalDebt": 10.0, "netDebt": -40.0}],
        "cashflow": [{"date": "2026-04-30", "freeCashFlow": 8.0,
                      "operatingCashFlow": 9.0}],
    })

    # docs/logs companions (existence probes)
    for rel in ("docs/research/NIGHTLY_OPERATOR_SUMMARY.md",
                "docs/research/DAILY_ALPHA_RADAR_REPORT.md",
                "logs/research_scanner_latest.txt",
                "logs/research_universe_build_latest.txt"):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("fixture", encoding="utf-8")


@pytest.fixture
def fixture_root(tmp_path):
    write_fixture_artifacts(tmp_path)
    return tmp_path


def _tree_digest(root: Path, exclude: Path = None) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file() and p != exclude:
            h.update(str(p.relative_to(root)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def _journal(root: Path) -> Path:
    return root / "data" / "research" / "journal.jsonl"


def _run(root: Path, *args) -> int:
    return digest_main(["--root", str(root), *args])


# ── 1. dry-run writes nothing ────────────────────────────────────────────────


def test_dry_run_writes_nothing(fixture_root, capsys):
    before = _tree_digest(fixture_root)
    assert _run(fixture_root, "--dry-run") == 0
    assert _tree_digest(fixture_root) == before
    assert not _journal(fixture_root).exists()
    assert "dry-run" in capsys.readouterr().out


def test_default_without_flags_is_dry_run(fixture_root):
    assert _run(fixture_root) == 0
    assert not _journal(fixture_root).exists()


# ── 2–4. append / duplicate / force ─────────────────────────────────────────


def test_append_writes_exactly_one_entry(fixture_root):
    assert _run(fixture_root, "--append") == 0
    lines = _journal(fixture_root).read_text().strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["source_view"] == "journal_digest_script"
    assert entry["title"].startswith("Daily Research Digest — ")
    assert entry["research_only_footer"] == (
        "Research only — not a signal or recommendation.")


def test_duplicate_digest_does_not_append_again(fixture_root, capsys):
    assert _run(fixture_root, "--append") == 0
    assert _run(fixture_root, "--append") == 0
    lines = _journal(fixture_root).read_text().strip().splitlines()
    assert len(lines) == 1
    assert "duplicate" in capsys.readouterr().out


def test_force_appends_duplicate(fixture_root):
    assert _run(fixture_root, "--append") == 0
    assert _run(fixture_root, "--append", "--force") == 0
    lines = _journal(fixture_root).read_text().strip().splitlines()
    assert len(lines) == 2


def test_digest_key_is_deterministic_and_findable(fixture_root):
    store = ArtifactStore(root=fixture_root)
    inputs = collect_inputs(store)
    k1 = compute_digest_key(inputs, "2026-07-04")
    k2 = compute_digest_key(collect_inputs(store), "2026-07-04")
    assert k1 == k2
    assert k1.startswith("daily_digest:2026-07-04:")
    assert find_existing_digest(store, k1) is None
    _run(fixture_root, "--append", "--date", "2026-07-04")
    assert find_existing_digest(store, k1) is not None


# ── 5. missing artifacts degrade gracefully ──────────────────────────────────


def test_missing_artifacts_graceful(tmp_path):
    entry = build_digest_entry(ArtifactStore(root=tmp_path))
    assert entry["status"] == "DATA_QUALITY_CONCERN"
    assert "MISSING_ARTIFACT" in entry["note"]
    snap = entry["evidence_snapshot"]
    assert snap["total_candidates"] is None
    assert snap["high_priority"] == []
    assert snap["top_review_candidates"] == []
    for heading in SECTION_HEADINGS:
        assert heading in entry["note"]


# ── 6. fundamental overlay handles missing fundamentals ─────────────────────


def test_fundamental_overlay_handles_missing(fixture_root):
    entry = build_digest_entry(ArtifactStore(root=fixture_root))
    # GOODCO has cached statements; the T## fixtures do not.
    assert "GOODCO: quality" in entry["note"]
    assert "fundamentals unavailable (no cached statements)" in entry["note"]


# ── 7. evidence snapshot keys ────────────────────────────────────────────────


def test_evidence_snapshot_contains_required_keys(fixture_root):
    snap = build_digest_entry(
        ArtifactStore(root=fixture_root))["evidence_snapshot"]
    for k in SNAPSHOT_KEYS:
        assert k in snap, f"missing snapshot key {k}"
    assert snap["mode"] == "RESEARCH_ONLY"
    assert snap["high_priority"] == ["GOODCO"]
    assert snap["matured_5d"] == 20
    assert snap["matured_10d"] == 8
    assert snap["benchmark_status"] == "READY"


# ── 8. seven-section note body ───────────────────────────────────────────────


def test_note_body_has_all_sections_and_footer(fixture_root):
    entry = build_digest_entry(ArtifactStore(root=fixture_root))
    for heading in SECTION_HEADINGS:
        assert heading in entry["note"]
    assert entry["note"].strip().endswith(
        "Research only — not a signal or recommendation.")
    for tag in BASE_TAGS:
        assert tag in entry["tags"]
    assert "high-priority" in entry["tags"]
    assert "phase4b-blocked" in entry["tags"]
    assert "needs-more-data" in entry["tags"]


# ── 9. forbidden wording ─────────────────────────────────────────────────────


def test_no_forbidden_wording_in_sources_and_output(fixture_root):
    for rel in ("scripts/generate_research_journal_digest.py",
                "dashboards/research_command_center/journal_digest.py"):
        src = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert not FORBIDDEN.search(src), f"forbidden wording in {rel}"
    entry = build_digest_entry(ArtifactStore(root=fixture_root))
    assert not FORBIDDEN.search(json.dumps(entry))


# ── 10–11. write isolation ───────────────────────────────────────────────────


def test_append_writes_only_journal(fixture_root):
    journal = _journal(fixture_root)
    before = _tree_digest(fixture_root, exclude=journal)
    assert _run(fixture_root, "--append") == 0
    assert _tree_digest(fixture_root, exclude=journal) == before
    assert journal.exists()


def test_build_never_mutates_artifacts(fixture_root):
    before = _tree_digest(fixture_root)
    build_digest_entry(ArtifactStore(root=fixture_root))
    assert _tree_digest(fixture_root) == before


# ── 12. top-N limit ──────────────────────────────────────────────────────────


def test_top_candidate_limit(fixture_root):
    store = ArtifactStore(root=fixture_root)
    e3 = build_digest_entry(store, top_n=3)
    e10 = build_digest_entry(store, top_n=10)
    assert len(e3["evidence_snapshot"]["top_review_candidates"]) == 3
    assert len(e10["evidence_snapshot"]["top_review_candidates"]) == 10


# ── 13. status selection ─────────────────────────────────────────────────────


def test_status_data_quality_concern_on_heavy_skips(tmp_path):
    write_fixture_artifacts(tmp_path, stale_skipped=10, suspect_skipped=10)
    entry = build_digest_entry(ArtifactStore(root=tmp_path))
    assert entry["status"] == "DATA_QUALITY_CONCERN"


def test_status_needs_review_on_high_priority(tmp_path):
    write_fixture_artifacts(tmp_path, high_priority=("GOODCO",))
    entry = build_digest_entry(ArtifactStore(root=tmp_path))
    assert entry["status"] == "NEEDS_REVIEW"


def test_status_needs_review_on_warnings(tmp_path):
    write_fixture_artifacts(tmp_path, high_priority=(),
                            warnings=("Options overlay disabled",))
    entry = build_digest_entry(ArtifactStore(root=tmp_path))
    assert entry["status"] == "NEEDS_REVIEW"


def test_status_follow_up_when_evidence_immature(tmp_path):
    write_fixture_artifacts(tmp_path, high_priority=(),
                            verdict="NEED_MORE_DATA")
    entry = build_digest_entry(ArtifactStore(root=tmp_path))
    assert entry["status"] == "FOLLOW_UP"
    assert "needs-more-data" in entry["tags"]


def test_status_watching_when_clean_and_proven(tmp_path):
    write_fixture_artifacts(tmp_path, high_priority=(), verdict="PROMISING")
    entry = build_digest_entry(ArtifactStore(root=tmp_path))
    assert entry["status"] == "WATCHING"
    assert "phase4b-blocked" not in entry["tags"]
    assert "needs-more-data" not in entry["tags"]


# ── sector-alignment label (P1 d29046c033c6) ─────────────────────────────────

from dashboards.research_command_center.journal_digest import (  # noqa: E402
    _alignment_label, _sectors_matching,
)


def test_etf_symbols_match_sector_names():
    # the original bug: names vs ETF symbols never matched -> always
    # "aligned"; the mapping layer must bridge the namespaces
    assert _sectors_matching(["XLK"], ["Technology"]) == ["Technology"]
    assert _sectors_matching(["XLV"], ["Healthcare"]) == ["Healthcare"]
    assert _sectors_matching(["XLU"], ["Technology"]) == []


def test_alignment_not_assessable_without_leadership():
    label = _alignment_label([], ["XLK", "XLU"],
                             ["Technology", "Industrials"])
    assert label.startswith("not_assessable")
    assert "1 of 2" in label and "Technology" in label
    assert "aligned —" not in label
    clean = _alignment_label([], ["XLU"], ["Technology"])
    assert clean.startswith("not_assessable")
    assert "no top-name sector is weak" in clean


def test_alignment_verdicts_with_leadership():
    assert _alignment_label(["XLI"], ["XLU"], ["Industrials"]) \
        .startswith("aligned")
    assert _alignment_label(["XLI"], ["XLK"], ["Technology"]) \
        .startswith("misaligned")
    assert _alignment_label(["XLI"], ["XLK"],
                            ["Industrials", "Technology"]) \
        .startswith("mixed")
    assert _alignment_label(["XLI"], ["XLK"], ["Energy"]) \
        .startswith("unconfirmed")
    assert _alignment_label([], [], ["Energy"]) == "unknown"


# ── fundamental red flags in review order (P2 70884dde3217) ──────────────────

from dashboards.research_command_center.journal_digest import (  # noqa: E402
    _red_flags_for, _section_review_queue,
)


def test_red_flags_for_unprofitable_and_dilution(monkeypatch):
    import dashboards.research_command_center.journal_digest as jd
    fundamentals = {
        "SDGR": {"quality_label": "UNPROFITABLE_FUNDED",
                 "operating_margin_pct": -64.65,
                 "gross_margin_pct": 55.3, "dilution_3q_pct": 0.8},
        "GRAL": {"quality_label": "UNPROFITABLE_FUNDED",
                 "operating_margin_pct": -348.7,
                 "gross_margin_pct": -14.8, "dilution_3q_pct": 15.9},
        "FBIN": {"quality_label": "PROFITABLE_CASHGEN",
                 "operating_margin_pct": 11.0,
                 "gross_margin_pct": 44.0, "dilution_3q_pct": -2.1},
        "MISS": {"fallback": True},
    }
    monkeypatch.setattr(jd, "build_fundamentals",
                        lambda t, store: fundamentals.get(t, {"fallback": True}))
    assert "unprofitable" in _red_flags_for("SDGR", None)[0]
    gral = _red_flags_for("GRAL", None)
    assert any("dilution +15.9" in f for f in gral)
    assert any("negative GM" in f for f in gral)
    assert _red_flags_for("FBIN", None) == []   # clean name: no flags
    assert _red_flags_for("MISS", None) == []   # no data: no fabrication


def test_review_queue_carries_red_flag_line(monkeypatch):
    import dashboards.research_command_center.journal_digest as jd
    monkeypatch.setattr(jd, "build_fundamentals", lambda t, store: {
        "quality_label": "UNPROFITABLE_FUNDED",
        "operating_margin_pct": -50.0} if t == "SDGR"
        else {"quality_label": "PROFITABLE_CASHGEN",
              "operating_margin_pct": 10.0})
    inputs = {"summary": {}, "radar": {}, "store": object()}
    lines = _section_review_queue(inputs, ["FBIN", "SDGR"],
                                  ["FBIN", "SDGR"], [])
    text = "\n".join(lines)
    assert "- Review first: FBIN, SDGR" in text
    assert "Red flags in review order: SDGR (unprofitable, OM -50.0%)" \
        in text
    # ticker list line stays clean (audit regex parses it unchanged)
    review_line = [l for l in lines if l.startswith("- Review first")][0]
    assert "(" not in review_line


# ── options-overlay structural line + final-finding annotation ───────────────


def _note(root: Path) -> str:
    return build_digest_entry(ArtifactStore(root=root))["note"]


def _write_options_coverage(root: Path, state: str) -> None:
    _write(root / "cache" / "research" / "options_coverage_report_latest.json", {
        "generated_at": _now_iso(),
        "overlay_state": state,
        "coverage_pct": 1.0 if state != "ENABLED" else 92.0,
        "covered": 1 if state != "ENABLED" else 92,
        "watchlist_total": 101,
    })


def test_options_overlay_disabled_is_first_class_structural_line(fixture_root):
    _write_options_coverage(fixture_root, "DISABLED")
    note = _note(fixture_root)
    dq = note.split("## 2.")[0]
    assert "- Options overlay: DISABLED" in dq
    assert "structural gate" in dq
    assert "NOT a candidate-level defect" in dq
    final = note.split("## 7. Final Finding")[1]
    assert "Known structural context" in final
    assert "options overlay is DISABLED" in final


def test_options_overlay_enabled_has_no_structural_clause(fixture_root):
    _write_options_coverage(fixture_root, "ENABLED")
    note = _note(fixture_root)
    dq = note.split("## 2.")[0]
    assert "- Options overlay: ENABLED" in dq
    assert "structural gate" not in dq
    final = note.split("## 7. Final Finding")[1]
    assert "options overlay" not in final


def test_options_overlay_missing_sidecar_reports_unknown(fixture_root):
    note = _note(fixture_root)
    assert "- Options overlay: state unknown" in note


def test_final_finding_notes_non_aligned_sector_read(fixture_root):
    # no leading sectors + weak overlap -> honest not_assessable label,
    # which the final finding must restate as structural context
    summary_path = (fixture_root / "cache" / "research"
                    / "nightly_operator_summary_latest.json")
    summary = json.loads(summary_path.read_text())
    summary["market_context"]["leading_sectors"] = []
    summary["market_context"]["weak_sectors"] = ["Technology"]
    _write(summary_path, summary)
    final = _note(fixture_root).split("## 7. Final Finding")[1]
    assert "sector alignment reads not_assessable" in final


def test_final_finding_aligned_read_adds_no_alignment_note(fixture_root):
    # fixture default: leading=[Technology], top sectors include Technology
    final = _note(fixture_root).split("## 7. Final Finding")[1]
    assert "sector alignment reads" not in final


# ── forward-evidence maturity ETA ────────────────────────────────────────────


def test_trading_day_helpers():
    from datetime import date
    from dashboards.research_command_center.journal_digest import (
        _add_trading_days, _trading_days_between)
    # Mon 2026-07-06 + 5 td = Mon 2026-07-13 (skips the weekend)
    assert _add_trading_days(date(2026, 7, 6), 5) == date(2026, 7, 13)
    assert _trading_days_between(date(2026, 7, 6), date(2026, 7, 13)) == 5
    assert _trading_days_between(date(2026, 7, 6), date(2026, 7, 6)) == 0


def _write_history(root: Path, appearance: str, n: int = 3,
                   fname: str = "research_watchlist_history.jsonl") -> None:
    p = root / "data" / "research" / fname
    p.parent.mkdir(parents=True, exist_ok=True)
    rows = [json.dumps({"ticker": f"H{i}", "appearance_date": appearance})
            for i in range(n)]
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")


def test_maturity_eta_lines_project_unmatured_horizons(fixture_root):
    from datetime import date, timedelta
    recent = (date.today() - timedelta(days=10)).isoformat()
    _write_history(fixture_root, recent)
    note = _note(fixture_root)
    fwd = note.split("## 4. Forward Evidence")[1].split("## 4b.")[0]
    assert "Maturity ETA" in fwd
    # all three unmatured horizons are projected with dates in the future
    for h in ("20d", "45d", "60d"):
        assert h in fwd.split("Maturity ETA")[1].splitlines()[0]


def test_maturity_eta_skips_matured_horizons(fixture_root):
    from datetime import date, timedelta
    recent = (date.today() - timedelta(days=10)).isoformat()
    _write_history(fixture_root, recent)
    fwd_path = (fixture_root / "cache" / "research"
                / "research_forward_latest.json")
    fwd_obj = json.loads(fwd_path.read_text())
    fwd_obj["overall"]["matured_by_horizon"] = {"20d": 7, "45d": 0, "60d": 0}
    _write(fwd_path, fwd_obj)
    note = _note(fixture_root)
    eta_line = [ln for ln in note.splitlines() if "Maturity ETA" in ln][0]
    assert "20d" not in eta_line
    assert "45d" in eta_line and "60d" in eta_line


def test_maturity_eta_marks_overdue_cohorts(fixture_root):
    from datetime import date, timedelta
    old = (date.today() - timedelta(days=60)).isoformat()
    _write_history(fixture_root, old)
    note = _note(fixture_root)
    eta_line = [ln for ln in note.splitlines() if "Maturity ETA" in ln][0]
    assert "resolution pending" in eta_line


def test_shortlist_maturity_eta_line(fixture_root):
    from datetime import date, timedelta
    recent = (date.today() - timedelta(days=3)).isoformat()
    _write_history(fixture_root, recent,
                   fname="high_conviction_history.jsonl")
    note = _note(fixture_root)
    assert "Shortlist maturity ETA: first 10d episodes ≈" in note
    assert "verdict needs ≥10 matured" in note


def test_no_history_files_add_no_eta_lines(fixture_root):
    note = _note(fixture_root)
    assert "Maturity ETA" not in note
    assert "Shortlist maturity ETA" not in note


# ── scanner-recall diagnostics declaration ───────────────────────────────────


def test_scanner_section_declares_recall_diagnostics(fixture_root):
    _write(fixture_root / "cache" / "research"
           / "scanner_recall_diagnostics_latest.json", {
               "generated_at": _now_iso(),
               "asof_date": "2026-06-04",
               "reject_counts_by_filter": [
                   {"filter": "no_atr_contraction", "rejected_n": 511,
                    "winners_missed": 128},
                   {"filter": "volume_insufficient", "rejected_n": 500,
                    "winners_missed": 129},
               ],
           })
    note = _note(fixture_root)
    scanner = note.split("## 2.")[1].split("## 3.")[0]
    assert "scanner-recall diagnostics report" in scanner
    assert "no_atr_contraction (511 rejected/128 winners missed)" in scanner
    assert "Gates unchanged pending forward evidence" in scanner


def test_no_recall_sidecar_adds_no_declaration(fixture_root):
    note = _note(fixture_root)
    assert "scanner-recall diagnostics report" not in note

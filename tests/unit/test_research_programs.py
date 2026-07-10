"""Tests for the Phase 5 research-program routing + validation layer.

Covers:
  1. Every known watchlist label routes to exactly one program with a
     hypothesis and horizons; unknown labels come back UNROUTED.
  2. Episode dedupe keeps only the first appearance per ticker+label.
  3. Horizon-coverage reports the tracker's gaps per program.
  4. The pre-registered verdict ladder: INSUFFICIENT / NO_EVIDENCE /
     PROMISING / VALIDATED under controlled inputs.
  5. Diagnostic signal is context only (POSITIVE/NEGATIVE/...).
  6. Full build on a synthetic root: structure, verdicts, ledger left
     byte-identical (read-only), and only sidecar+log written.
  7. No broker/execution imports.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pandas as pd
import pytest

from research import research_programs as rp

KNOWN_LABELS = [
    "CATALYST", "SOCIAL_ARB", "NO_SOCIAL_DATA", "RISKY", "WATCH",
    "BEATEN_DOWN", "EARLY_ACCUMULATION", "RS_MOMENTUM_LEADER",
    "SECTOR_LEADER", "EXTENDED", "ASYMMETRIC_RECOVERY_WATCH",
    "SPECULATIVE_10X",
]


# ── 1. routing ───────────────────────────────────────────────────────────────


def test_every_known_label_is_routed():
    for label in KNOWN_LABELS:
        rec = rp.classify_label(label)
        assert rec["program"] in rp.PROGRAMS, label
        assert rec["hypothesis"], label
        assert rec["expected_holding_period_td"], label
        assert rec["evaluation_horizons_td"], label


def test_unknown_label_is_unrouted():
    rec = rp.classify_label("MYSTERY_LABEL")
    assert rec["program"] == "UNROUTED"
    assert rec["confidence"] == "none"


def test_each_program_has_labels():
    by_prog = {}
    for label, route in rp.LABEL_PROGRAMS.items():
        by_prog.setdefault(route["program"], []).append(label)
    assert set(by_prog) == set(rp.PROGRAMS)


# ── 2. episode dedupe ────────────────────────────────────────────────────────


def test_episodes_dedupe_to_first_appearance():
    rows = [
        {"ticker": "AAA", "watchlist_label": "CATALYST",
         "appearance_date": "2026-06-20", "ret_5d": 1.0},
        {"ticker": "AAA", "watchlist_label": "CATALYST",
         "appearance_date": "2026-06-15", "ret_5d": 2.0},
        {"ticker": "AAA", "watchlist_label": "WATCH",
         "appearance_date": "2026-06-18", "ret_5d": 3.0},
        {"ticker": "BBB", "watchlist_label": "CATALYST",
         "appearance_date": "2026-06-16", "ret_5d": 4.0},
    ]
    eps = rp.build_episodes(rows)
    assert len(eps) == 3  # AAA/CATALYST deduped to earliest
    aaa = [e for e in eps if e["ticker"] == "AAA"
           and e["watchlist_label"] == "CATALYST"][0]
    assert aaa["appearance_date"] == "2026-06-15"


# ── 3. horizon coverage ──────────────────────────────────────────────────────


def test_horizon_coverage_all_program_horizons_collected():
    """Phase 5.1: the tracker's canonical horizon set covers every
    program's primary and diagnostic horizons — no measurement gap."""
    for prog in rp.PROGRAMS.values():
        cov = rp.horizon_coverage(prog["primary_horizons_td"],
                                  prog["diagnostic_horizons_td"])
        assert cov["primary_not_collected"] == [], prog["name"]
        assert cov["diagnostic_not_collected"] == [], prog["name"]
    # a hypothetical horizon outside the set still reports as a gap
    fake = rp.horizon_coverage((7,), ())
    assert fake["primary_not_collected"] == [7]


# ── 4. verdict ladder (pre-registered) ───────────────────────────────────────


def _hstats(h, n, dates, mean, win, ci):
    return {h: {"n_episodes": n, "n_dates": dates,
                "vs_qqq": {"n": n, "mean_pct": mean, "median_pct": mean,
                           "win_rate_pct": win, "boot95_mean": ci},
                "vs_sector": {"n": n, "mean_pct": mean,
                              "median_pct": mean, "win_rate_pct": win,
                              "boot95_mean": ci}}}


def test_verdict_insufficient_when_nothing_matured():
    v = rp.assign_verdict({}, (30, 45, 60), 100, 15)
    assert v["verdict"] == "INSUFFICIENT_MATURE_EVIDENCE"


def test_verdict_no_evidence_when_coverage_met_and_negative():
    stats = _hstats(10, 150, 20, -2.0, 40.0, [-3.0, -1.0])
    v = rp.assign_verdict(stats, (10,), 100, 15)
    assert v["verdict"] == "NO_EVIDENCE_OF_EDGE"


def test_verdict_validated_when_coverage_met_and_ci_positive():
    stats = _hstats(10, 150, 20, 3.0, 60.0, [1.0, 5.0])
    v = rp.assign_verdict(stats, (10,), 100, 15)
    assert v["verdict"] == "VALIDATED_EDGE"


def test_verdict_promising_when_positive_below_floors():
    stats = _hstats(10, 20, 5, 4.0, 70.0, [-1.0, 9.0])
    v = rp.assign_verdict(stats, (10,), 100, 15)
    assert v["verdict"] == "PROMISING_BUT_UNPROVEN"


def test_verdict_insufficient_when_partial_primary_coverage():
    # 10d matured strongly but 30/45 primary horizons never matured:
    # coverage requires ALL primary horizons.
    stats = _hstats(10, 150, 20, 3.0, 60.0, [1.0, 5.0])
    v = rp.assign_verdict(stats, (10, 30, 45), 100, 15)
    assert v["verdict"] in ("PROMISING_BUT_UNPROVEN",)
    # positive but cannot be VALIDATED without the full horizon set
    assert v["verdict"] != "VALIDATED_EDGE"


# ── 5. diagnostic signal ─────────────────────────────────────────────────────


def test_diagnostic_signal_positive_negative_and_small_n():
    pos = rp.diagnostic_signal(_hstats(10, 12, 4, 8.0, 70.0, None), (5, 10))
    assert pos["diagnostic_signal"] == "POSITIVE"
    neg = rp.diagnostic_signal(_hstats(10, 12, 4, -4.0, 20.0, None), (5, 10))
    assert neg["diagnostic_signal"] == "NEGATIVE"
    few = rp.diagnostic_signal(_hstats(10, 4, 2, 9.0, 100.0, None), (5, 10))
    assert few["diagnostic_signal"] == "TOO_FEW_EPISODES"
    none = rp.diagnostic_signal({}, (5, 10))
    assert none["diagnostic_signal"] == "NONE"


# ── 6. full build on synthetic root ──────────────────────────────────────────


@pytest.fixture
def root(tmp_path: Path) -> Path:
    dates = pd.bdate_range("2026-05-01", periods=60)
    prices = tmp_path / rp.PRICES_REL
    prices.mkdir(parents=True)

    def write(sym, drift):
        close = pd.Series([100 * (1 + drift) ** i for i in range(60)],
                          index=dates)
        pd.DataFrame({"close": close}).to_parquet(prices / f"{sym}.parquet")

    write("SPY", 0.001)
    write("QQQ", 0.001)
    write("XLK", 0.001)
    write("AAA", 0.01)   # strong winner vs benchmarks
    write("BBB", -0.01)  # loser

    ledger = tmp_path / rp.LEDGER_REL
    ledger.parent.mkdir(parents=True)
    rows = []
    for i, d in enumerate(pd.bdate_range("2026-05-05", periods=4)):
        for tkr, ret5 in (("AAA", 5.0), ("BBB", -5.0)):
            rows.append({
                "ticker": tkr, "watchlist_label": "BEATEN_DOWN",
                "category": "beaten_down_recovery",
                "appearance_date": str(d.date()),
                "benchmark_sector_etf": "XLK",
                "sector": "Technology", "research_score": 70.0,
                "ret_5d": ret5, "ret_10d": ret5 * 2,
                "ret_20d": None, "ret_60d": None, "resolved": False})
        # duplicate re-appearance next day (must dedupe)
        rows.append({**rows[-1]})
    ledger.write_text("\n".join(json.dumps(r) for r in rows) + "\n",
                      encoding="utf-8")
    return tmp_path


def test_full_build_structure_and_read_only(root):
    ledger_path = root / rp.LEDGER_REL
    before = ledger_path.read_bytes()
    report = rp.build_program_validation(root)
    assert ledger_path.read_bytes() == before  # read-only on the ledger

    assert set(report["programs"]) == {"TACTICAL", "SWING", "LONG_TERM"}
    assert report["research_only"] is True
    assert report["guardrails"]["no_production_change"] is True
    # 2 tickers x 1 label -> 2 episodes: re-appearances on later dates
    # collapse onto the first appearance (overlap control)
    assert report["deduped_episodes"] == 2
    swing = report["programs"]["SWING"]
    # 30/45td are collected (Phase 5.1) but not matured in this fixture
    # -> verdict stays INSUFFICIENT on primary horizons
    assert swing["verdict"] == "INSUFFICIENT_MATURE_EVIDENCE"
    assert swing["horizon_coverage"]["primary_not_collected"] == []
    bd = swing["label_verdicts"]["BEATEN_DOWN"]
    assert bd["program"] == "SWING"
    assert bd["n_episodes"] == 2
    assert "10" in bd["horizons"]


def test_write_outputs_only_sidecar_and_log(root):
    report = rp.build_program_validation(root)
    files_before = {p for p in root.rglob("*") if p.is_file()}
    paths = rp.write_outputs(report, root)
    files_after = {p for p in root.rglob("*") if p.is_file()}
    assert files_after - files_before == {paths["sidecar"], paths["log"]}
    saved = json.loads(paths["sidecar"].read_text(encoding="utf-8"))
    assert saved["kind"] == "research_program_validation"
    text = paths["log"].read_text(encoding="utf-8")
    assert "SWING" in text and "VERDICT" in text


# ── 7. import hygiene ────────────────────────────────────────────────────────


def test_no_broker_or_execution_imports():
    source = Path(rp.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    forbidden = ("alpaca", "tradier", "execution", "broker",
                 "core.config", "core.alpaca", "order", "strategies",
                 "council", "requests", "anthropic")
    for mod in imported:
        assert not any(mod.startswith(f) or f in mod for f in forbidden), \
            f"forbidden import in research_programs: {mod}"


# ── 8. lifecycle stages ──────────────────────────────────────────────────────


def test_lifecycle_stage_ladder():
    hs = _hstats(10, 12, 4, 8.0, 70.0, None)
    assert rp.lifecycle_stage(0, {}, "INSUFFICIENT_MATURE_EVIDENCE",
                              "NONE") == "DETECTED"
    assert rp.lifecycle_stage(5, {}, "INSUFFICIENT_MATURE_EVIDENCE",
                              "NONE") == "RESEARCH_CANDIDATE"
    assert rp.lifecycle_stage(5, hs, "INSUFFICIENT_MATURE_EVIDENCE",
                              "FLAT_OR_MIXED") == "UNDER_OBSERVATION"
    assert rp.lifecycle_stage(5, hs, "INSUFFICIENT_MATURE_EVIDENCE",
                              "POSITIVE") == "EARLY_CONFIRMATION"
    assert rp.lifecycle_stage(150, hs, "PROMISING_BUT_UNPROVEN",
                              "NONE") == "EARLY_CONFIRMATION"
    assert rp.lifecycle_stage(150, hs, "VALIDATED_EDGE",
                              "POSITIVE") == "VALIDATED_RESEARCH_CANDIDATE"
    assert rp.lifecycle_stage(150, hs, "NO_EVIDENCE_OF_EDGE",
                              "NEGATIVE") == "REJECTED"


def test_label_blocks_carry_lifecycle(root):
    report = rp.build_program_validation(root)
    bd = report["programs"]["SWING"]["label_verdicts"]["BEATEN_DOWN"]
    assert bd["lifecycle_stage"] in rp.LIFECYCLE_STAGES

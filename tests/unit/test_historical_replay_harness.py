"""Safety tripwires for the historical replay harness (research/backtests/).

The replay imports the frozen production scanner and the frozen HC/EO builders
and runs them over four years of history. That makes it the one place in the
repo where research code executes production logic at scale, so the guarantees
it needs are not stylistic — they are the difference between a research artifact
and a corrupted live cache.

These tests pin six of them:

1. the write guard admits replay namespaces and refuses live paths;
2. artifacts carry the replay provenance flags;
3. every provider-touching stage refuses to run without ``--execute-fetch``;
4. the replay verdict ladder is disjoint from the live verdict ladder, and
   ``VALIDATED_EDGE`` in particular cannot be emitted;
5. Alpha Focus is skipped unless replay-local inputs exist;
6. no module writes a live cache path or ``data/research``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from research.backtests import common as C
from research.backtests import historical_replay_fundamental as HRF
from research.backtests import historical_replay_fundamental_fetch as HRFF
from research.backtests import historical_replay_price_backfill as HRPB
from research.backtests import historical_replay_universe_build as HRUB

REPLAY_MODULES = (
    "common",
    "historical_replay_universe_build",
    "historical_replay_price_backfill",
    "historical_replay_price_only",
    "historical_replay_diagnostics",
    "historical_replay_fundamental_fetch",
    "historical_replay_fundamental",
)


# ── 1. write containment ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "cache/replay_prices/AAPL.parquet",
        "cache/replay_fundamentals/AAPL.json",
        "cache/replay_market_cap/AAPL.parquet",
        "cache/replay_universe/replay_price_quarantine.json",
        "cache/research/historical_replay_latest.json",
        "cache/research/historical_replay_episodes.jsonl",
        "cache/research/historical_fundamental_replay_latest.json",
        "cache/research/historical_replay_intermediates/control.json",
        "logs/historical_replay_latest.txt",
        "logs/historical_fundamental_replay_latest.txt",
    ],
)
def test_replay_namespaces_are_writable(path):
    assert C.assert_replay_write_path(path)


@pytest.mark.parametrize(
    "path",
    [
        "cache/prices/AAPL.parquet",
        "cache/prices_deep/AAPL.parquet",
        "cache/backtest_prices/AAPL.parquet",
        "cache/fundamentals/AAPL.json",
        "cache/universe/universe.json",
        "cache/state/broker_positions_snapshot.json",
        "cache/research/research_scanner_latest.json",
        "cache/research/latest_scan_programs_latest.json",
        "cache/research/high_conviction_alpha_latest.json",
        "cache/research/alpha_focus_latest.json",
        "data/research/journal.jsonl",
        "data/research/research_watchlist_history.jsonl",
        "data/state/paper_legacy_quarantine.json",
        "db/trading.db",
        "logs/research_scanner_latest.txt",
        "logs/nightly_operator_summary_latest.md",
    ],
)
def test_live_paths_are_refused(path):
    with pytest.raises(C.ReplayWriteViolation):
        C.assert_replay_write_path(path)


@pytest.mark.parametrize(
    "path",
    [
        # A shared leading string is not membership: these are siblings of a
        # replay namespace, not paths inside one.
        "cache/replay_pricesEVIL/AAA.parquet",
        "cache/research/historical_replay_intermediatesX/y.json",
        # A file prefix must not be used to claim a whole subtree.
        "cache/research/historical_replay_sneaky/deep/nested.json",
        "logs/historical_replay_dir/y.txt",
    ],
)
def test_sibling_prefixes_do_not_grant_access(path):
    with pytest.raises(C.ReplayWriteViolation):
        C.assert_replay_write_path(path)


def test_nested_paths_inside_a_replay_dir_are_allowed():
    assert C.assert_replay_write_path(
        "cache/research/historical_replay_intermediates/a/b.json"
    )


def test_write_helpers_refuse_live_paths(tmp_path):
    with pytest.raises(C.ReplayWriteViolation):
        C.write_replay_json("data/research/journal.jsonl", {"x": 1})
    with pytest.raises(C.ReplayWriteViolation):
        C.write_replay_text("cache/prices/AAA.parquet", "nope")


def test_live_artifact_tripwire_detects_a_touch(tmp_path):
    live = tmp_path / "cache" / "research" / "research_scanner_latest.json"
    live.parent.mkdir(parents=True)
    live.write_text("{}")
    tw = C.LiveArtifactTripwire.snapshot(tmp_path)
    assert tw.diff() == []
    tw.assert_clean()  # unchanged -> no raise

    # A rewrite of different length: caught by size even if the filesystem
    # hands both writes the same mtime.
    live.write_text('{"touched": true, "by": "an unpatched production writer"}')
    assert "cache/research/research_scanner_latest.json" in tw.diff()
    with pytest.raises(C.LiveArtifactTouched):
        tw.assert_clean()


def test_tripwire_catches_a_same_mtime_rewrite(tmp_path, monkeypatch):
    """mtime alone is not enough — a same-timestamp rewrite must still trip."""
    live = tmp_path / "cache" / "research" / "research_scanner_latest.json"
    live.parent.mkdir(parents=True)
    live.write_text("{}")
    tw = C.LiveArtifactTripwire.snapshot(tmp_path)

    frozen = live.stat().st_mtime_ns
    real_stat = Path.stat

    def fixed_stat(self, *a, **kw):
        st = real_stat(self, *a, **kw)
        if self == live:
            return type("S", (), {"st_mtime_ns": frozen, "st_size": st.st_size})()
        return st

    live.write_text('{"rewritten": "same mtime"}')
    monkeypatch.setattr(Path, "stat", fixed_stat)
    with pytest.raises(C.LiveArtifactTouched):
        tw.assert_clean()


def test_tripwire_watches_live_price_dirs(tmp_path):
    px = tmp_path / "cache" / "prices"
    px.mkdir(parents=True)
    (px / "AAA.parquet").write_text("x")
    tw = C.LiveArtifactTripwire.snapshot(tmp_path)
    tw.assert_clean()
    (px / "BBB.parquet").write_text("y")  # a new live parquet appears
    with pytest.raises(C.LiveArtifactTouched):
        tw.assert_clean()


# ── 2. provenance ───────────────────────────────────────────────────────────


def test_provenance_flags_present():
    flags = C.provenance_flags()
    for k in C.REQUIRED_PROVENANCE_KEYS:
        assert flags[k] is True, k
    assert flags["research_only"] is True
    assert flags["not_live_evidence"] is True
    assert flags["not_backtest_evidence_for_phase4b"] is True
    assert "restatement_contaminated" not in flags


def test_fundamental_provenance_marks_restatement_contamination():
    flags = C.provenance_flags(fundamentals=True)
    assert flags["restatement_contaminated"] is True
    assert "restatement" in flags["note"].lower()


def test_assert_provenance_rejects_a_bare_artifact():
    with pytest.raises(ValueError):
        C.assert_provenance({"kind": "SOMETHING", "research_only": True})
    C.assert_provenance({**C.provenance_flags(), "kind": "OK"})


# ── 3. provider calls require an explicit flag ──────────────────────────────


def test_require_execute_fetch_blocks_by_default():
    class A:
        execute_fetch = False

    with pytest.raises(C.FetchNotAuthorised):
        C.require_execute_fetch(A(), planned_calls=1000, what="test")


def test_require_execute_fetch_allows_when_authorised():
    class A:
        execute_fetch = True

    C.require_execute_fetch(A(), planned_calls=1000, what="test")  # no raise


def test_call_cap_aborts_before_the_first_call():
    with pytest.raises(C.FetchNotAuthorised):
        C.enforce_call_cap(9000, 8500, what="test")
    C.enforce_call_cap(10, 8500, what="test")  # under the cap: no raise


def test_harvest_cli_refuses_without_execute_fetch(tmp_path, capsys):
    """The CLI must exit 3 with an explicit refusal, not crash and not fetch."""
    rc = HRUB.main(["harvest", "--root", str(tmp_path)])
    assert rc == 3
    out = capsys.readouterr().out
    assert "REFUSED" in out
    assert "No calls were made" in out
    assert not (tmp_path / "cache").exists()


def test_fundamental_fetch_refuses_without_execute_fetch(tmp_path, capsys):
    ep = tmp_path / "cache" / "research" / "historical_replay_episodes.jsonl"
    ep.parent.mkdir(parents=True)
    ep.write_text(json.dumps({"ticker": "AAA", "scan_date": "2024-01-05"}) + "\n")
    rc = HRFF.main(["--root", str(tmp_path)])
    assert rc == 3
    out = capsys.readouterr().out
    assert "REFUSED" in out and "No calls were made" in out
    assert not (tmp_path / "cache" / "replay_fundamentals").exists()


def test_every_fetching_stage_exposes_execute_fetch():
    for mod, argv in (
        (HRUB, ["harvest"]),
        (HRPB, ["fetch"]),
        (HRFF, []),
    ):
        args = mod.build_parser().parse_args(argv)
        assert hasattr(args, "execute_fetch")
        assert args.execute_fetch is False, f"{mod.__name__} defaults to fetching"


# ── 4. the replay verdict ladder is separate from the live one ──────────────


def test_validated_edge_can_never_be_emitted():
    with pytest.raises(ValueError, match="live verdict ladder"):
        C.assert_replay_verdict("VALIDATED_EDGE")


@pytest.mark.parametrize(
    "live_verdict",
    ["VALIDATED_EDGE", "READY_TO_GATE", "LENS_READY", "CORE_ENGINE_CANDIDATE",
     "NEED_MORE_DATA", "INSUFFICIENT_MATURE_EVIDENCE", "MIXED", "NO_VALUE"],
)
def test_live_verdicts_are_refused(live_verdict):
    with pytest.raises(ValueError):
        C.assert_replay_verdict(live_verdict)


@pytest.mark.parametrize("v", sorted(C.REPLAY_VERDICTS))
def test_replay_verdicts_are_accepted(v):
    assert C.assert_replay_verdict(v) == v


def test_ladders_are_disjoint():
    assert not (C.REPLAY_VERDICTS & C.FORBIDDEN_LIVE_VERDICTS)


def test_unknown_verdicts_are_refused():
    with pytest.raises(ValueError):
        C.assert_replay_verdict("PROBABLY_FINE")


# ── 5. Alpha Focus stays skipped ────────────────────────────────────────────


def test_alpha_focus_skipped_on_an_empty_sandbox(tmp_path):
    safe, reason = HRF.alpha_focus_replay_safe(tmp_path)
    assert safe is False
    assert "NOT SAFE" in reason
    assert "future information" in reason


def test_alpha_focus_skipped_when_inputs_are_only_partly_local(tmp_path):
    # Two of three present is still unsafe: the rule would not be the frozen rule.
    for rel in HRF.ALPHA_FOCUS_LIVE_INPUTS[:2]:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}")
    safe, _ = HRF.alpha_focus_replay_safe(tmp_path)
    assert safe is False


def test_alpha_focus_allowed_only_with_all_replay_local_inputs(tmp_path):
    for rel in HRF.ALPHA_FOCUS_LIVE_INPUTS:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}")
    safe, reason = HRF.alpha_focus_replay_safe(tmp_path)
    assert safe is True
    assert "replay-local" in reason


# ── 6. no module writes a live path ─────────────────────────────────────────


def _module_sources():
    base = Path(C.__file__).parent
    for name in REPLAY_MODULES:
        yield name, (base / f"{name}.py").read_text()


@pytest.mark.parametrize("forbidden", ["data/research", "cache/prices/", "cache/prices_deep",
                                       "cache/backtest_prices", "cache/fundamentals/"])
def test_no_module_targets_a_live_write_path(forbidden):
    """A live path may be READ (survivors, live-artifact watch lists) but must
    never appear as a write target. Writes go through write_replay_* helpers,
    which are guarded, so this scans for raw write calls on those literals."""
    for name, src in _module_sources():
        for i, line in enumerate(src.splitlines(), 1):
            if forbidden not in line:
                continue
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"'):
                continue
            assert not any(
                w in line for w in (".write_text(", ".write_bytes(", "to_parquet(", "open(")
            ), f"{name}.py:{i} writes a live path: {stripped}"


def test_forbidden_prefixes_cover_the_live_caches():
    for p in ("cache/prices", "cache/prices_deep", "cache/backtest_prices", "data"):
        assert p in C.FORBIDDEN_WRITE_PREFIXES


def test_replay_modules_are_not_imported_by_production():
    """Production must never import the replay harness (hard separation rule)."""
    repo = Path(C.ROOT)
    offenders = []
    for area in ("core", "council", "execution", "strategies", "dashboards"):
        for py in (repo / area).rglob("*.py"):
            if "research.backtests" in py.read_text():
                offenders.append(str(py.relative_to(repo)))
    assert not offenders, f"production imports the replay harness: {offenders}"

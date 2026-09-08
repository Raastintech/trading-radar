"""Guards for the M1 shadow source pool (research-only).

The report exists to publish the one thing four studies support — membership of
a wide 12-1 momentum pool — and to publish nothing else. These tests pin the
properties that make that safe:

* it does not run at all unless the data guard says the data can support 12-1;
* it never ranks, tiers, scores or orders the pool, and never publishes the
  per-name momentum that would let a reader re-sort it;
* every tag is an annotation, never combined into a number;
* forward rows are an unresolved research scaffold, never Phase 4B evidence;
* it writes only its four artifacts, never a live one, and calls no provider.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

import research.backtests.m1_shadow_pool as M
from research.backtests.common import (
    FORBIDDEN_LIVE_VERDICTS, ReplayWriteViolation, assert_replay_write_path,
)
from research.backtests.m1_data_guard import M1DataRefusal, TickerDepth

SESSION = date(2026, 9, 4)


def depth(ticker: str, *, bars: int = 400, mom: float = 50.0, price: float = 40.0,
          dvol: float = 2.0e7, last: date = SESSION, span: int = 366,
          contaminated: bool = False) -> TickerDepth:
    return TickerDepth(
        ticker=ticker, bars=bars, first_bar="2020-01-02", last_bar=last.isoformat(),
        price=price, dvol_med_20=dvol, span_252_calendar_days=span, mom_12_1=mom,
        readable=True, contaminated=contaminated,
        contamination_reason="split_artifact_single_day" if contaminated else None)


def universe(n: int = 2600, **kw) -> list[TickerDepth]:
    """A synthetic cache big enough to satisfy the guard's coverage floors.

    The .37 offset keeps every synthetic momentum value distinguishable from
    the round numbers the stubbed annotations use, so the leak check below
    cannot pass or fail by coincidence.
    """
    return [depth(f"T{i:04d}", mom=float(i % 500) + 0.37, **kw) for i in range(n)]


def _args(root: Path, **kw) -> argparse.Namespace:
    base = dict(root=str(root), size=M.POOL_DEFAULT, seed=None, limit=None,
                stage="report")
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.fixture
def wired(monkeypatch):
    """Point the module at synthetic depths and a fixed session."""
    def _wire(depths, *, price_ann=None):
        monkeypatch.setattr(M, "scan_cache", lambda root, **kw: list(depths))
        monkeypatch.setattr(M, "load_quarantine_excludes", lambda root: set())
        monkeypatch.setattr(M, "load_refresh_outcome", lambda root: {
            "source": "test", "failed_refresh_count": 0, "missing_ticker_count": 0,
            "history_drift_count": 0, "generated_at": None})
        monkeypatch.setattr(M, "_required_session", lambda *a, **k: SESSION)
        monkeypatch.setattr(
            M, "price_annotations",
            price_ann or (lambda root, tickers: {
                t: {"price": 40.0, "last_bar": SESSION.isoformat(), "bars": 400,
                    "liquidity_band": "liquid_20M_100M", "risk_flags": [],
                    "data_quality": [], "atr14_pct": 4.0,
                    "dollar_volume_median_20d": 20.0} for t in tickers}))
    return _wire


# ── 1. the gate ─────────────────────────────────────────────────────────────


def test_refuses_when_the_guard_refuses(tmp_path, wired):
    wired([depth(f"T{i}", bars=150, mom=None) for i in range(50)])
    with pytest.raises(M1DataRefusal):
        M.build_pool_report(tmp_path, _args(tmp_path))


def test_shallow_cache_is_refused_and_writes_nothing(tmp_path, wired):
    """The live-cache failure mode: a median 113 bars cannot compute 12-1."""
    wired([depth(f"T{i:04d}", bars=113, mom=None, span=None) for i in range(2600)])
    rc = M.report_stage(_args(tmp_path))
    assert rc == 2, "a refusal is an orderly non-zero exit, not a crash"
    for rel in (M.LATEST_REL, M.REPORT_TXT_REL, M.DOC_REL, M.FORWARD_ROWS_REL):
        assert not (tmp_path / rel).exists(), f"{rel} was written despite a refusal"


def test_stale_cache_is_refused(tmp_path, wired):
    old = SESSION - timedelta(days=6)
    wired([depth(f"T{i:04d}", last=old) for i in range(2600)])
    rc = M.report_stage(_args(tmp_path))
    assert rc == 2
    assert not (tmp_path / M.LATEST_REL).exists()


def test_a_thin_membership_is_refused_rather_than_published(tmp_path, wired):
    """Fewer than 50 names would read as a shortlist, so it is not published."""
    depths = universe(2600)
    monkey = [replace(d, mom_12_1=1.0) for d in depths]
    wired(monkey)
    import research.backtests.m1_shadow_pool as mod
    orig = mod.m1_membership
    try:
        mod.m1_membership = lambda depths_, session: (
            [depth(f"S{i}") for i in range(10)], {"members": 10, "eligible_names": 10,
                                                  "quintile": 0.2, "cut_rule": "x",
                                                  "cut_value_pct": None,
                                                  "membership_momentum_distribution_pct": {},
                                                  "ordering_note": "x"})
        with pytest.raises(M1DataRefusal) as e:
            mod.build_pool_report(tmp_path, _args(tmp_path))
        assert "floor" in str(e.value)
    finally:
        mod.m1_membership = orig


def test_the_guard_header_travels_with_the_report(tmp_path, wired):
    wired(universe())
    p = M.build_pool_report(tmp_path, _args(tmp_path))
    h = p["data_integrity_header"]
    for k in ("universe_size", "names_with_enough_bars", "coverage_pct",
              "median_bars", "stale_count", "shallow_count", "contamination_pct",
              "m1_pool_size_if_run", "refusal_reason"):
        assert k in h, f"integrity header is missing {k}"


# ── 2. membership and the draw ──────────────────────────────────────────────


def test_membership_is_the_quintile_and_excludes_bad_series(tmp_path):
    depths = (universe(1000)
              + [depth("CONTAM", mom=999.0, contaminated=True),
                 depth("STALE", mom=999.0, last=SESSION - timedelta(days=30)),
                 depth("PENNY", mom=999.0, price=0.5),
                 depth("THIN", mom=999.0, dvol=1000.0)])
    members, meta = M.m1_membership(depths, SESSION)
    names = {d.ticker for d in members}
    assert not ({"CONTAM", "STALE", "PENNY", "THIN"} & names)
    assert meta["members"] == round(meta["eligible_names"] * 0.20)


def test_the_pool_is_alphabetical_not_ranked(tmp_path, wired):
    wired(universe())
    p = M.build_pool_report(tmp_path, _args(tmp_path))
    names = p["pool_unordered"]
    assert names == sorted(names), "the published order must be alphabetical"
    entries = [e["ticker"] for e in p["entries"]]
    assert entries == sorted(entries)


def test_per_name_momentum_is_never_published(tmp_path, wired):
    """Publishing it would let a reader re-sort, and a re-sort is a ranking."""
    wired(universe())
    p = M.build_pool_report(tmp_path, _args(tmp_path))
    p.pop("_forward_rows", None)

    def keys(obj, out):
        if isinstance(obj, dict):
            for k, v in obj.items():
                out.add(str(k).lower())
                keys(v, out)
        elif isinstance(obj, list):
            for v in obj:
                keys(v, out)

    entry_keys: set[str] = set()
    keys(p["entries"], entry_keys)
    assert not [k for k in entry_keys if "mom" in k], (
        f"a per-name momentum field would invite a re-sort: {entry_keys}")
    # Prose may NAME the field; what must never appear is a per-name VALUE.
    moms = {round(float(d.mom_12_1), 2) for d in
            M.m1_membership(universe(), SESSION)[0]}
    for e in p["entries"]:
        vals = set()
        keys_only_numbers(e, vals)
        assert not (vals & moms), f"{e['ticker']} leaks its momentum value"
    assert p["membership"]["membership_momentum_distribution_pct"], (
        "the pool-level distribution is still published — it orders nothing")


def keys_only_numbers(obj, out: set) -> None:
    if isinstance(obj, dict):
        for v in obj.values():
            keys_only_numbers(v, out)
    elif isinstance(obj, list):
        for v in obj:
            keys_only_numbers(v, out)
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        out.add(round(float(obj), 2))


def test_the_draw_is_seeded_and_reproducible(tmp_path, wired):
    wired(universe())
    a = M.build_pool_report(tmp_path, _args(tmp_path))["pool_unordered"]
    b = M.build_pool_report(tmp_path, _args(tmp_path))["pool_unordered"]
    assert a == b
    c = M.build_pool_report(tmp_path, _args(tmp_path, seed=99))["pool_unordered"]
    assert c != a, "an explicit seed must actually change the draw"


def test_the_draw_is_not_the_top_of_the_membership(tmp_path):
    members = [depth(f"T{i:04d}", mom=float(1000 - i)) for i in range(600)]
    pool, meta = M.draw_pool(members, size=75, session=SESSION)
    top = {d.ticker for d in sorted(members, key=lambda d: d.mom_12_1,
                                    reverse=True)[:75]}
    drawn = {d.ticker for d in pool}
    assert drawn != top, "a top-N draw would be exactly the ranking this refuses"
    assert meta["selection"].startswith("uniform random")


@pytest.mark.parametrize("asked,expected", [(10, M.POOL_MIN), (75, 75),
                                            (500, M.POOL_MAX)])
def test_pool_size_is_clamped_to_the_published_band(tmp_path, wired, asked, expected):
    wired(universe())
    p = M.build_pool_report(tmp_path, _args(tmp_path, size=asked))
    assert len(p["pool_unordered"]) == expected
    assert M.POOL_MIN <= len(p["pool_unordered"]) <= M.POOL_MAX


# ── 3. tags are annotations ─────────────────────────────────────────────────


def test_no_tag_is_a_score_or_a_tier(tmp_path, wired):
    wired(universe())
    p = M.build_pool_report(tmp_path, _args(tmp_path))
    for e in p["entries"]:
        blob = json.dumps(e["tags"]).lower()
        for banned in ("tier", "rank", "grade_score", "conviction", "buy", "sell"):
            assert banned not in blob, f"{e['ticker']} tag block contains {banned!r}"
        assert e["position_in_this_list_means_nothing"] is True


def test_every_entry_carries_the_human_checklist_unfilled(tmp_path, wired):
    wired(universe())
    p = M.build_pool_report(tmp_path, _args(tmp_path))
    for e in p["entries"]:
        fields = e["checklist"]["fields"]
        assert set(fields) == {k for k, _ in M.CHECKLIST_FIELDS}
        assert all(v is None for v in fields.values()), (
            "the report supplies facts, never the judgement")


def test_saturation_warning_annotates_and_never_filters(tmp_path, wired, monkeypatch):
    wired(universe())
    monkeypatch.setattr(M, "saturation_warnings",
                        lambda root, tickers: {sorted(tickers)[0]: {
                            "tag": "scanner_score_saturated", "score": 100.0,
                            "reading": "historically negative cohort"}})
    p = M.build_pool_report(tmp_path, _args(tmp_path))
    warned = [e for e in p["entries"] if e["tags"]["scanner_saturation_warning"]]
    assert len(warned) == 1, "the warned name must still be IN the pool"


def test_stale_earnings_data_is_reported_as_stale_not_as_a_beat(tmp_path):
    feed = tmp_path / M.EARNINGS_FEED_REL
    feed.parent.mkdir(parents=True, exist_ok=True)
    feed.write_text(json.dumps({"symbol": "AAA", "date": "2025-01-15",
                                "epsActual": 2.0, "epsEstimated": 1.0}) + "\n")
    out = M.earnings_surprise(tmp_path, {"AAA"}, SESSION)
    assert out["AAA"]["tag"] == "no_recent_report_in_cached_feed"
    assert out["AAA"]["age_days"] > M.EARNINGS_STALE_DAYS


def test_a_recent_beat_is_tagged_as_one(tmp_path):
    feed = tmp_path / M.EARNINGS_FEED_REL
    feed.parent.mkdir(parents=True, exist_ok=True)
    feed.write_text(json.dumps({"symbol": "AAA",
                                "date": (SESSION - timedelta(days=20)).isoformat(),
                                "epsActual": 2.0, "epsEstimated": 1.0,
                                "revenueActual": 10, "revenueEstimated": 9}) + "\n")
    assert M.earnings_surprise(tmp_path, {"AAA"}, SESSION)["AAA"]["tag"] == "beat"


def test_analyst_tag_carries_the_attention_not_direction_finding(tmp_path):
    feed = tmp_path / M.ANALYST_FEED_REL
    feed.parent.mkdir(parents=True, exist_ok=True)
    feed.write_text(json.dumps({
        "symbol": "AAA", "date": (SESSION - timedelta(days=10)).isoformat(),
        "firm": "Bank", "prev": "Hold", "new": "Buy", "action": "upgrade"}) + "\n")
    out = M.analyst_attention(tmp_path, {"AAA"}, SESSION)
    assert out["AAA"]["tag"] == "rating_raised"
    assert "attention, not direction" in out["AAA"]["reading"]


def test_an_action_dated_today_is_not_yet_usable(tmp_path):
    feed = tmp_path / M.ANALYST_FEED_REL
    feed.parent.mkdir(parents=True, exist_ok=True)
    feed.write_text(json.dumps({"symbol": "AAA", "date": SESSION.isoformat(),
                                "firm": "Bank", "prev": "Hold", "new": "Buy",
                                "action": "upgrade"}) + "\n")
    out = M.analyst_attention(tmp_path, {"AAA"}, SESSION)
    assert out["AAA"]["upgrades"] == 0


# ── 4. language ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", [
    "our conviction tier for this name",
    "this is a trade signal",
    "entry price 40.10, stop loss 35",
    "the strongest name in the pool",
    "VALIDATED_EDGE",
    "alpha picks for today",
])
def test_recommendation_language_is_refused(bad):
    with pytest.raises(M.ForbiddenLanguage):
        M.assert_clean_language(bad, where="test")


@pytest.mark.parametrize("ok", [
    "This report has no conviction tiers.",
    "It never emits a trade signal, entry, stop, target or size.",
    "no ranking, no top pick, no best idea",
    "The pool is unordered and equal weight.",
])
def test_disclaimers_are_allowed(ok):
    assert M.assert_clean_language(ok, where="test") == ok


def test_validated_edge_is_refused_even_when_negated():
    with pytest.raises(M.ForbiddenLanguage):
        M.assert_clean_language("this never emits VALIDATED_EDGE", where="test")


def test_no_live_verdict_token_appears_in_the_module():
    src = Path(M.__file__).read_text()
    for token in FORBIDDEN_LIVE_VERDICTS:
        assert token not in src, f"{token} must not be emittable here"


def test_the_rendered_report_passes_its_own_language_check(tmp_path, wired):
    wired(universe())
    p = M.build_pool_report(tmp_path, _args(tmp_path))
    p.pop("_forward_rows")
    for renderer in (M.render_text, M.render_doc):
        M.assert_clean_language(renderer(p), where="render")


# ── 5. forward scaffold ─────────────────────────────────────────────────────


def test_forward_rows_are_unresolved_research_only_and_not_phase4b(tmp_path, wired):
    wired(universe())
    p = M.build_pool_report(tmp_path, _args(tmp_path))
    rows = p["_forward_rows"]
    assert rows and len(rows) == len(p["pool_unordered"])
    for r in rows:
        assert r["resolved"] is False
        assert r["research_only"] is True and r["not_phase4b"] is True
        assert r["not_live_evidence"] is True
        assert set(r["forward_returns_pct"]) == {f"{h}d" for h in M.FORWARD_HORIZONS}
        assert all(v is None for v in r["forward_returns_pct"].values())
        assert r["weight"] == "equal"


def test_forward_rows_are_append_only_and_idempotent(tmp_path, wired):
    wired(universe())
    p = M.build_pool_report(tmp_path, _args(tmp_path))
    rows = p["_forward_rows"]
    first = M.append_forward_rows(tmp_path, rows)
    second = M.append_forward_rows(tmp_path, rows)
    assert first["rows_appended"] == len(rows)
    assert second["rows_appended"] == 0, "re-running a session must not duplicate"
    lines = (tmp_path / M.FORWARD_ROWS_REL).read_text().strip().splitlines()
    assert len(lines) == len(rows)


# ── 6. write containment ────────────────────────────────────────────────────


@pytest.mark.parametrize("rel", [M.LATEST_REL, M.REPORT_TXT_REL, M.DOC_REL,
                                 M.FORWARD_ROWS_REL])
def test_artifacts_are_inside_the_replay_namespace(rel):
    assert assert_replay_write_path(rel)


@pytest.mark.parametrize("forbidden", [
    "cache/research/research_scanner_latest.json",
    "cache/research/high_conviction_alpha_latest.json",
    "cache/research/alpha_focus_latest.json",
    "cache/prices/AAPL.parquet",
    "data/research/journal.jsonl",
    "db/trading.db",
    "docs/research/DAILY_ALPHA_RADAR_REPORT.md",
    "docs/ROADMAP_PHASES.md",
])
def test_live_and_neighbouring_paths_are_refused(forbidden):
    with pytest.raises(ReplayWriteViolation):
        assert_replay_write_path(forbidden)


def test_only_the_four_declared_artifacts_are_written(tmp_path, wired):
    wired(universe())
    rc = M.report_stage(_args(tmp_path))
    assert rc == 0
    written = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*")
                     if p.is_file())
    assert written == sorted([M.LATEST_REL, M.FORWARD_ROWS_REL, M.REPORT_TXT_REL,
                              M.DOC_REL])


def test_the_report_makes_no_provider_call(tmp_path, wired, monkeypatch):
    import socket

    monkeypatch.setattr(socket.socket, "connect",
                        lambda *a, **k: pytest.fail("report attempted a network call"))
    wired(universe())
    assert M.report_stage(_args(tmp_path)) == 0
    payload = json.loads((tmp_path / M.LATEST_REL).read_text())
    assert payload["provider_calls"] == 0


def test_overlap_reading_is_context_not_endorsement(tmp_path, wired):
    wired(universe())
    p = M.build_pool_report(tmp_path, _args(tmp_path))
    reading = p["pool_stats"]["overlap_with_live_surfaces"]["reading"]
    assert "not endorsement" in reading

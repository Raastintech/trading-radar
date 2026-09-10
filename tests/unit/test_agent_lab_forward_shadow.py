"""Guards for the `style_cell_leader_pool` forward shadow ledger.

The ledger's value depends entirely on it refusing to run on bad data, never
calling a provider, and never accreting anything that could be read as a stock
selection. These tests pin those properties. They build a synthetic price cache
in a tmp dir and touch no repo artifact.
"""
from __future__ import annotations

import json
import socket

import numpy as np
import pandas as pd
import pytest

from research.agent_lab import forward_shadow as F


# ------------------------------------------------------------- fixtures -----

def _series(n: int, *, start: float = 100.0, drift: float = 0.0004, vol: float = 0.02,
            end: str = "2026-09-09", seed: int = 0, volume: float = 1_000_000.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end=pd.Timestamp(end), periods=n)
    steps = rng.normal(drift, vol, size=n)
    close = start * np.exp(np.cumsum(steps))
    high = close * (1 + np.abs(rng.normal(0, 0.005, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.005, n)))
    return pd.DataFrame(
        {"open": close, "high": high, "low": low, "close": close,
         "volume": np.full(n, volume)}, index=idx)


def _cache(tmp_path, specs: dict[str, dict]) -> F.PriceSource:
    """specs: ticker -> kwargs for _series. Returns a source pointed at it."""
    d = tmp_path / "prices"
    d.mkdir(parents=True, exist_ok=True)
    for t, kw in specs.items():
        _series(**kw).to_parquet(d / f"{t}.parquet")
    src = F.PriceSource("test_cache", (str(d),))
    # PriceSource.paths() joins against REPO; an absolute dir survives that.
    return src


def _healthy_specs(n_names: int = 140, bars: int = 400, end: str = "2026-09-09") -> dict:
    specs = {}
    for i in range(n_names):
        specs[f"T{i:03d}"] = {
            "n": bars, "seed": i, "end": end,
            "start": 20.0 + i,                      # price >= $5
            "vol": 0.01 + (i % 10) * 0.004,         # spreads the volatility deciles
            "drift": 0.0002 * ((i % 7) - 3),        # spreads momentum
            "volume": 2_000_000.0 * (1 + i % 11),   # spreads the liquidity deciles
        }
    for b in F.BENCHMARKS:
        specs[b] = {"n": bars, "seed": 900 + hash(b) % 50, "end": end, "start": 400.0}
    return specs


# --------------------------------------------------- refusal on coverage ----

def test_refuses_when_too_little_of_the_universe_is_fresh(tmp_path):
    specs = _healthy_specs()
    # Age most of the universe past the freshness floor.
    for i, t in enumerate(list(specs)):
        if t in F.BENCHMARKS:
            continue
        if i % 4:
            specs[t]["end"] = "2026-06-01"
    src = _cache(tmp_path, specs)
    frame, session = F.build_session_frame(src)
    cov = F.coverage_report(frame, session)
    assert cov["status"] == F.REFUSAL_INSUFFICIENT
    assert "have a bar within" in cov["reason"]
    assert cov["fresh_share_pct"] < F.MIN_FRESH_SHARE * 100


def test_refuses_when_fresh_names_lack_the_252_bars_momentum_needs(tmp_path):
    specs = _healthy_specs()
    for i, t in enumerate(list(specs)):
        if t not in F.BENCHMARKS and i % 3:
            specs[t]["n"] = 130          # fresh, liquid, but far too shallow
    src = _cache(tmp_path, specs)
    frame, session = F.build_session_frame(src)
    cov = F.coverage_report(frame, session)
    assert cov["status"] == F.REFUSAL_INSUFFICIENT
    assert str(F.MIN_BARS) in cov["reason"]


def test_a_refused_session_is_still_recorded_so_a_gap_is_never_ambiguous(tmp_path):
    specs = _healthy_specs()
    for i, t in enumerate(list(specs)):
        if t not in F.BENCHMARKS and i % 4:
            specs[t]["end"] = "2026-06-01"
    src = _cache(tmp_path, specs)
    frame, session = F.build_session_frame(src)
    cov = F.coverage_report(frame, session)
    rec = F.snapshot_record(session, src, cov, pd.DataFrame(), pd.DataFrame(), now=session)
    assert rec["status"] == F.REFUSAL_INSUFFICIENT
    assert rec["refusal_reason"]
    assert rec["pool_size"] == 0
    assert "pool_members_unordered" not in rec


def test_a_healthy_cache_passes_both_gates_and_builds_a_pool(tmp_path):
    src = _cache(tmp_path, _healthy_specs())
    frame, session = F.build_session_frame(src)
    cov = F.coverage_report(frame, session)
    assert cov["status"] == "OK", cov.get("reason")
    uni = F.eligible_universe(frame, session)
    pool = F.build_pool(uni)
    assert len(pool) == uni["style_cell"].nunique()
    assert pool["ticker"].is_unique


def test_the_pool_takes_the_highest_momentum_name_in_each_cell(tmp_path):
    src = _cache(tmp_path, _healthy_specs())
    frame, session = F.build_session_frame(src)
    uni = F.eligible_universe(frame, session)
    pool = F.build_pool(uni)
    for cell, g in uni.groupby("style_cell"):
        chosen = pool.loc[pool["style_cell"] == cell, "mom_12_1"]
        assert not chosen.empty
        assert chosen.iloc[0] == pytest.approx(g["mom_12_1"].max())


# ------------------------------------------------------- zero provider IO ---

def test_a_session_makes_zero_provider_calls_with_the_network_removed(tmp_path, monkeypatch):
    """The strongest form of the claim: run with sockets disabled."""
    def _no_network(*a, **k):
        raise AssertionError("the forward shadow ledger attempted a network call")

    monkeypatch.setattr(socket, "socket", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)

    src = _cache(tmp_path, _healthy_specs())
    monkeypatch.setattr(F, "resolve_source", lambda name: src)
    ledger = tmp_path / "ledger.jsonl"
    out = F.run_session(as_of="2026-09-09", ledger_path=str(ledger), verbose=False)
    assert out["snapshot"]["provider_calls"] == 0
    assert out["snapshot"]["status"] == "OK"


def test_the_refresh_plan_refuses_above_the_hundred_call_cap(tmp_path):
    from research.backtests.common import FetchNotAuthorised
    specs = _healthy_specs()
    for i, t in enumerate(list(specs)):
        if t not in F.BENCHMARKS:
            specs[t]["end"] = "2026-06-01"      # everything needs a refresh
    src = _cache(tmp_path, specs)
    frame, session = F.build_session_frame(src, pd.Timestamp("2026-09-09"))
    with pytest.raises(FetchNotAuthorised, match="exceed"):
        F.plan_refresh(frame, session, None)


def test_the_refresh_plan_never_fetches_even_when_it_fits_under_the_cap(tmp_path):
    src = _cache(tmp_path, _healthy_specs())
    frame, session = F.build_session_frame(src)
    plan = F.plan_refresh(frame, session, 1000)
    assert plan["this_module_makes_no_provider_calls"] is True
    assert "refresh_universe_prices" in plan["delegate_to"]


# --------------------------------------------- no ranking, no trade talk ----

def test_no_record_carries_a_per_name_score_or_ordering_field(tmp_path):
    src = _cache(tmp_path, _healthy_specs())
    frame, session = F.build_session_frame(src)
    cov = F.coverage_report(frame, session)
    uni = F.eligible_universe(frame, session)
    rec = F.snapshot_record(session, src, cov, uni, F.build_pool(uni), now=session)
    banned_keys = {"rank", "ranking", "score", "scores", "top_n", "ordering",
                   "conviction", "weight", "target", "signal"}
    assert not (set(rec) & banned_keys)
    assert rec["no_per_name_score_stored"] is True
    # the stored members are bare strings, so nothing can be re-ordered by merit
    assert all(isinstance(m, str) for m in rec["pool_members_unordered"])
    assert rec["pool_members_unordered"] == sorted(rec["pool_members_unordered"])


# Fields and sections whose whole job is to state prohibitions. They are
# expected to contain the banned words; the guard below checks the substantive
# output around them, and a separate test checks the disclaimers are present.
DISCLAIMER_KEYS = (
    "not_supported", "verdict_pin_reason", "contemporaneous_note",
    "verdict_is_pinned", "refusal_reason", "no_recommendation",
)


def _substantive(ledger_text: str, summary: dict, doc: str) -> str:
    """Everything the module emits, minus the declared disclaimer surfaces."""
    trimmed = {k: v for k, v in summary.items() if k not in DISCLAIMER_KEYS}
    body = []
    skipping = False
    for line in doc.splitlines():
        if line.startswith("> ") or line.startswith("## What this does not support"):
            skipping = line.startswith("## What this does not support")
            continue
        if skipping and line.startswith("## "):
            skipping = False
        if not skipping:
            body.append(line)
    return (ledger_text + json.dumps(trimmed) + "\n".join(body)).lower()


def test_emitted_records_use_no_trade_or_signal_language(tmp_path, monkeypatch):
    src = _cache(tmp_path, _healthy_specs())
    monkeypatch.setattr(F, "resolve_source", lambda name: src)
    ledger = tmp_path / "ledger.jsonl"
    F.run_session(as_of="2026-09-09", ledger_path=str(ledger), verbose=False)
    summary = F.summarise(str(ledger))
    blob = _substantive(ledger.read_text(), summary, F.render_doc(summary))
    for phrase in F.BANNED_LANGUAGE:
        assert phrase not in blob, f"{phrase!r} appears outside a disclaimer"


def test_the_banned_language_guard_can_actually_fail():
    """A guard that cannot fail is not a guard."""
    blob = _substantive("", {"headline": "our top pick this week"}, "")
    assert "top pick" in blob
    # and a real disclaimer surface is correctly exempt
    exempt = _substantive("", {"not_supported": ["ranking the pool"]}, "")
    assert "ranking" not in exempt


def test_the_required_disclaimers_are_actually_present(tmp_path, monkeypatch):
    """The exemption above is only safe if the disclaimers exist at all."""
    src = _cache(tmp_path, _healthy_specs())
    monkeypatch.setattr(F, "resolve_source", lambda name: src)
    ledger = tmp_path / "ledger.jsonl"
    F.run_session(as_of="2026-09-09", ledger_path=str(ledger), verbose=False)
    summary = F.summarise(str(ledger))
    doc = F.render_doc(summary).lower()
    assert "not" in doc and "a trade signal" in doc
    assert "forward_shadow_research_only" in doc.lower()
    assert any("ranking" in x for x in summary["not_supported"])
    assert summary["no_recommendation"] is True


def test_the_summary_states_what_it_does_not_support(tmp_path, monkeypatch):
    src = _cache(tmp_path, _healthy_specs())
    monkeypatch.setattr(F, "resolve_source", lambda name: src)
    ledger = tmp_path / "ledger.jsonl"
    F.run_session(as_of="2026-09-09", ledger_path=str(ledger), verbose=False)
    s = F.summarise(str(ledger))
    joined = " ".join(s["not_supported"]).lower()
    assert "individual stock selection" in joined
    assert s["basket_not_selection"] is True
    assert s["method_definition"]["ordering"].startswith("none")


# ------------------------------------------------------ append-only ledger --

def test_rerunning_a_session_appends_nothing_and_rewrites_nothing(tmp_path, monkeypatch):
    src = _cache(tmp_path, _healthy_specs())
    monkeypatch.setattr(F, "resolve_source", lambda name: src)
    ledger = tmp_path / "ledger.jsonl"
    F.run_session(as_of="2026-09-09", ledger_path=str(ledger), verbose=False)
    first = ledger.read_text()
    F.run_session(as_of="2026-09-09", ledger_path=str(ledger), verbose=False)
    assert ledger.read_text() == first


def test_append_ledger_never_touches_existing_lines(tmp_path):
    p = tmp_path / "l.jsonl"
    F.append_ledger([{"record_key": "a", "v": 1}], str(p))
    F.append_ledger([{"record_key": "b", "v": 2}], str(p))
    rows = F.read_ledger(str(p))
    assert [r["record_key"] for r in rows] == ["a", "b"]
    assert rows[0]["v"] == 1
    # a duplicate key is skipped rather than overwriting or duplicating
    assert F.append_ledger([{"record_key": "a", "v": 99}], str(p)) == 0
    assert F.read_ledger(str(p))[0]["v"] == 1


def test_a_backdated_session_is_not_written_without_the_explicit_flag(tmp_path, monkeypatch):
    src = _cache(tmp_path, _healthy_specs(end="2026-01-15"))
    monkeypatch.setattr(F, "resolve_source", lambda name: src)
    ledger = tmp_path / "ledger.jsonl"
    out = F.run_session(as_of="2026-01-15", ledger_path=str(ledger),
                        now=pd.Timestamp("2026-09-10"), verbose=False)
    assert out["records_written"] == 0
    assert out["not_written_reason"] == "backdated_session"
    assert not ledger.exists()


def test_a_backdated_row_is_excluded_from_every_published_statistic(tmp_path, monkeypatch):
    src = _cache(tmp_path, _healthy_specs(end="2026-01-15"))
    monkeypatch.setattr(F, "resolve_source", lambda name: src)
    ledger = tmp_path / "ledger.jsonl"
    F.run_session(as_of="2026-01-15", ledger_path=str(ledger), allow_backdate=True,
                  now=pd.Timestamp("2026-09-10"), verbose=False)
    rows = F.read_ledger(str(ledger))
    assert rows[0]["contemporaneous"] is False
    assert "NOT unseen forward data" in rows[0]["contemporaneous_note"]
    s = F.summarise(str(ledger))
    assert s["sessions_recorded"] == 0
    assert s["backfilled_rows_excluded"] == 1


# ------------------------------------------------------------- the verdict --

def test_the_verdict_is_pinned_before_the_maturity_floor(tmp_path, monkeypatch):
    src = _cache(tmp_path, _healthy_specs())
    monkeypatch.setattr(F, "resolve_source", lambda name: src)
    ledger = tmp_path / "ledger.jsonl"
    F.run_session(as_of="2026-09-09", ledger_path=str(ledger), verbose=False)
    s = F.summarise(str(ledger))
    assert s["verdict"] == "FORWARD_SHADOW_RESEARCH_ONLY"
    assert s["maturity"]["floor_reached"] is False
    assert s["maturity"]["matured_snapshots_at_primary_horizon"] == 0


def test_the_verdict_stays_pinned_even_once_the_floor_is_reached(tmp_path):
    """The floor makes the evidence worth reading; it does not grant a verdict."""
    ledger = tmp_path / "l.jsonl"
    rows = []
    for i in range(F.MATURITY_FLOOR["matured_snapshots_at_primary_horizon"] + 5):
        d = (pd.Timestamp("2026-01-05") + pd.Timedelta(days=120 * i)).date()
        rows.append({"kind": "POOL_SNAPSHOT", "record_key": f"{d}|POOL_SNAPSHOT|s",
                     "session_date": str(d), "status": "OK", "contemporaneous": True,
                     "price_source": "s", "pool_members_unordered": ["A"]})
        rows.append({"kind": "HORIZON_RESOLUTION", "record_key": f"{d}|RESOLUTION|60|s",
                     "session_date": str(d), "horizon_sessions": 60, "price_source": "s",
                     "basket": {"mean_return_pct": 3.0, "median_return_pct": 2.0},
                     "selection_vs_universe_mean_pp": 2.4, "benchmarks": {},
                     "controls": {}})
    F.append_ledger(rows, str(ledger))
    s = F.summarise(str(ledger), "s")
    assert s["maturity"]["floor_reached"] is True
    assert s["verdict"] == "FORWARD_SHADOW_RESEARCH_ONLY"
    assert s["verdict_is_pinned"] is True


def test_non_overlapping_windows_are_counted_by_calendar_distance():
    close = [{"session_date": "2026-01-05", "horizon_sessions": 60},
             {"session_date": "2026-01-12", "horizon_sessions": 60},
             {"session_date": "2026-01-19", "horizon_sessions": 60}]
    assert F._non_overlapping_count(close, 60) == 1
    spread = [{"session_date": "2026-01-05", "horizon_sessions": 60},
              {"session_date": "2026-05-05", "horizon_sessions": 60},
              {"session_date": "2026-09-05", "horizon_sessions": 60}]
    assert F._non_overlapping_count(spread, 60) == 3


# ---------------------------------------------------- resolution mechanics --

def test_a_delisted_name_is_measured_to_its_final_bar_not_dropped(tmp_path):
    d = tmp_path / "p"
    d.mkdir()
    _series(300, end="2026-05-01", seed=3).to_parquet(d / "GONE.parquet")
    r, truncated = F._forward_return((str(d / "GONE.parquet"),), pd.Timestamp("2026-04-20"), 60)
    assert np.isfinite(r)
    assert truncated is True


def test_forward_return_is_nan_when_the_session_has_no_bars_after_it(tmp_path):
    d = tmp_path / "p"
    d.mkdir()
    _series(300, end="2026-05-01", seed=4).to_parquet(d / "X.parquet")
    r, _ = F._forward_return((str(d / "X.parquet"),), pd.Timestamp("2026-05-01"), 20)
    assert not np.isfinite(r)


def test_controls_are_computed_without_any_provider_call():
    members = [f"T{i}" for i in range(10)]
    cells = {f"T{i}": [i % 3, i % 4, 10.0] for i in range(60)}
    uni = {f"T{i}": float(i % 7) for i in range(60)}
    out = F._controls(members, cells, uni, basket_mean=3.0, draws=50, seed=1)
    assert out["provider_calls"] == 0
    assert out["same_date_random"]["pct_of_draws_beaten"] >= 0
    assert out["matched_random"]["cells_matched"] == len(members)


# ------------------------------------------------------------ cache merge ---

def test_the_merge_keeps_the_freshest_bar_not_the_deepest_file(tmp_path):
    """The bug this replaced: a deeper but staler cache hid the newest bar."""
    deep, live = tmp_path / "deep", tmp_path / "live"
    deep.mkdir(), live.mkdir()
    # Two views of ONE underlying series: a deep cache that stops a day early,
    # and a shallow cache that is current. This is the real shape in the repo.
    full = _series(801, end="2026-09-09", seed=1)
    full.iloc[:-1].to_parquet(deep / "AAA.parquet")   # 800 bars, ends 09-08
    full.tail(300).to_parquet(live / "AAA.parquet")   # 300 bars, ends 09-09
    src = F.PriceSource("merged", (str(deep), str(live)))
    df, tag = F.load_series(src.paths()["AAA"], ["close"])
    assert tag == "merged"
    assert df.index.max() == pd.Timestamp("2026-09-09")   # freshness from live
    assert len(df) == 801                                  # depth from the deep cache


def test_the_higher_priority_cache_wins_on_a_shared_date(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    lo = _series(300, end="2026-09-09", seed=2)[["close"]]
    hi = lo.copy()
    hi.loc[hi.index[-1], "close"] = lo["close"].iloc[-1] * 1.001
    lo.to_parquet(a / "AAA.parquet")
    hi.to_parquet(b / "AAA.parquet")
    df, _ = F.load_series((str(a / "AAA.parquet"), str(b / "AAA.parquet")), ["close"])
    assert df["close"].iloc[-1] == pytest.approx(hi["close"].iloc[-1])


def test_caches_that_disagree_on_price_are_not_spliced(tmp_path):
    """An unadjusted split across caches must not manufacture a price gap."""
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    base = _series(400, end="2026-09-08", seed=5)
    (base * 4).to_parquet(a / "AAA.parquet")        # pre-split prices
    base.tail(200).to_parquet(b / "AAA.parquet")     # adjusted prices
    df, tag = F.load_series((str(a / "AAA.parquet"), str(b / "AAA.parquet")), ["close"])
    assert tag == "single_adjustment_mismatch"
    # the result must be one of the inputs verbatim, never a concatenation of both
    lo = pd.read_parquet(a / "AAA.parquet", columns=["close"]).sort_index()
    hi = pd.read_parquet(b / "AAA.parquet", columns=["close"]).sort_index()
    assert df.equals(lo) or df.equals(hi)
    assert len(df) in (len(lo), len(hi))


def test_the_combined_source_puts_the_maintained_cache_last(tmp_path):
    src = F.resolve_source("live_plus_replay_cache")
    assert src.dirs[-len(F.LIVE_CACHE_DIRS):] == F.LIVE_CACHE_DIRS
    assert src.dirs[0] in F.REPLAY_CACHE_DIRS


# ------------------------------------------------- merged-series integrity --

def test_a_spliced_reverse_split_is_caught_and_the_merge_discarded(tmp_path):
    """The WOLF case: caches agree on their overlap and disagree before it.

    A pre-reverse-split segment spliced onto the post-split series manufactured
    a +1,726% session. Checking only the overlap cannot see it, because the bad
    segment is the part the overlap does not cover.
    """
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir(), new.mkdir()
    post = _series(240, end="2026-09-09", seed=11, start=22.0)
    pre = _series(200, end="2025-09-26", seed=12, start=0.40)   # pre-split basis
    pd.concat([pre, post]).to_parquet(old / "WOLFY.parquet")     # deep + corrupt
    post.to_parquet(new / "WOLFY.parquet")                       # clean, current
    src = F.PriceSource("s", (str(old), str(new)))
    df, tag = F.load_series(src.paths()["WOLFY"], ["close"])
    assert tag.startswith("single_merge_artifact")
    ok, _, worst = F.series_integrity(df)
    assert ok and worst < F.MAX_PLAUSIBLE_SESSION_GAIN_PCT
    assert len(df) == len(post)          # fell back to the clean series


def test_series_integrity_flags_an_implausible_session_gain():
    df = _series(300, end="2026-09-09", seed=13)[["close"]]
    df.iloc[150, 0] = df.iloc[149, 0] * 8.0
    ok, why, worst = F.series_integrity(df)
    assert not ok and "implausible_session_gain" in why and worst > 400


def test_series_integrity_does_not_flag_a_real_crash():
    """A -85% session is a failed read-out, not a data error. Removing those
    would delete genuine disasters and flatter every basket."""
    df = _series(300, end="2026-09-09", seed=14)[["close"]]
    df.iloc[150:, 0] = df.iloc[150:, 0] * 0.12
    ok, _, _ = F.series_integrity(df)
    assert ok


def test_a_name_with_no_clean_source_is_excluded_from_the_universe(tmp_path):
    specs = _healthy_specs()
    src = _cache(tmp_path, specs)
    d = tmp_path / "prices"
    bad = _series(400, end="2026-09-09", seed=15, start=30.0, volume=5_000_000.0)
    bad.iloc[200] = bad.iloc[199] * 9.0
    bad.to_parquet(d / "BADCO.parquet")
    frame, session = F.build_session_frame(src)
    assert "BADCO" not in set(frame["ticker"])
    assert any(k.startswith("excluded_") for k in frame.attrs["provenance"])


def test_the_fallback_picks_the_freshest_clean_source_not_the_highest_priority(tmp_path):
    """Priority decides who wins a shared date; it must not decide the fallback.

    Letting it choose cost 2,017 symbols their most recent bar.
    """
    stale_dir, fresh_dir = tmp_path / "a", tmp_path / "b"
    stale_dir.mkdir(), fresh_dir.mkdir()
    fresh = _series(300, end="2026-09-09", seed=16, start=40.0)
    stale = _series(300, end="2026-05-01", seed=17, start=9.0)   # different basis
    # highest priority is the STALE one, and the two cannot be spliced
    stale.to_parquet(fresh_dir / "AAA.parquet")
    fresh.to_parquet(stale_dir / "AAA.parquet")
    df, tag = F.load_series((str(stale_dir / "AAA.parquet"), str(fresh_dir / "AAA.parquet")), ["close"])
    assert df.index.max() == pd.Timestamp("2026-09-09")


def test_live_prices_outranks_the_research_deep_cache():
    """cache/prices is timer-maintained; cache/prices_deep is not, and carries a
    different dividend-adjustment vintage on the same dates."""
    assert F.LIVE_CACHE_DIRS[-1] == "cache/prices"
    assert F.resolve_source("live_plus_replay_cache").dirs[-1] == "cache/prices"


# ------------------------------------------------------------ invalidation --

def test_a_bad_snapshot_is_retracted_by_appending_not_deleting(tmp_path):
    ledger = tmp_path / "l.jsonl"
    snap = {"kind": "POOL_SNAPSHOT", "record_key": "2026-09-09|POOL_SNAPSHOT|s",
            "session_date": "2026-09-09", "price_source": "s", "status": "OK",
            "contemporaneous": True, "pool_members_unordered": ["A"], "pool_size": 1,
            "universe_size": 10, "coverage": {}}
    F.append_ledger([snap], str(ledger))
    F.append_ledger([F.invalidation_record(snap["record_key"], "bad merge",
                                           session_date="2026-09-09", price_source="s")], str(ledger))
    rows = F.read_ledger(str(ledger))
    assert len(rows) == 2                      # the bad row is still there
    assert rows[0]["record_key"] == snap["record_key"]
    s = F.summarise(str(ledger), "s")
    assert s["sessions_recorded"] == 0         # ...and excluded from statistics
    assert s["retracted_snapshots"] == 1


def test_a_retracted_snapshots_resolutions_are_excluded_too(tmp_path):
    ledger = tmp_path / "l.jsonl"
    F.append_ledger([
        {"kind": "POOL_SNAPSHOT", "record_key": "2026-01-05|POOL_SNAPSHOT|s",
         "session_date": "2026-01-05", "price_source": "s", "status": "OK",
         "contemporaneous": True, "pool_members_unordered": ["A"], "coverage": {}},
        {"kind": "HORIZON_RESOLUTION", "record_key": "2026-01-05|RESOLUTION|60|s",
         "session_date": "2026-01-05", "price_source": "s", "horizon_sessions": 60,
         "basket": {"mean_return_pct": 99.0, "median_return_pct": 99.0},
         "selection_vs_universe_mean_pp": 99.0, "benchmarks": {}, "controls": {}},
    ], str(ledger))
    assert F.summarise(str(ledger), "s")["by_horizon"]["60"]["matured_sessions"] == 1
    F.append_ledger([F.invalidation_record("2026-01-05|POOL_SNAPSHOT|s", "bad",
                                           session_date="2026-01-05", price_source="s")], str(ledger))
    after = F.summarise(str(ledger), "s")
    assert after["by_horizon"]["60"]["matured_sessions"] == 0


def test_a_revision_gets_its_own_key_so_it_cannot_collide(tmp_path):
    src = _cache(tmp_path, _healthy_specs())
    frame, session = F.build_session_frame(src)
    cov = F.coverage_report(frame, session)
    uni = F.eligible_universe(frame, session)
    a = F.snapshot_record(session, src, cov, uni, F.build_pool(uni), now=session, revision=0)
    b = F.snapshot_record(session, src, cov, uni, F.build_pool(uni), now=session, revision=2)
    assert a["record_key"] != b["record_key"]
    assert b["record_key"].endswith("|r2") and b["revision"] == 2


def test_sources_are_summarised_separately_and_never_pooled(tmp_path):
    ledger = tmp_path / "l.jsonl"
    F.append_ledger([
        {"kind": "POOL_SNAPSHOT", "record_key": "2026-01-05|POOL_SNAPSHOT|live_cache",
         "session_date": "2026-01-05", "price_source": "live_cache",
         "status": "REFUSED_X", "contemporaneous": True, "coverage": {}},
        {"kind": "POOL_SNAPSHOT", "record_key": "2026-01-05|POOL_SNAPSHOT|other",
         "session_date": "2026-01-05", "price_source": "other", "status": "OK",
         "contemporaneous": True, "pool_members_unordered": ["A"], "coverage": {}},
    ], str(ledger))
    assert F.summarise(str(ledger), "live_cache")["sessions_ok"] == 0
    assert F.summarise(str(ledger), "other")["sessions_ok"] == 1
    assert F.summarise(str(ledger), "other")["sources_present_in_ledger"] == ["live_cache", "other"]


# ------------------------------------------- operator-discarded debug days --

def _ledger_with_retracted_ok(tmp_path, reason: str):
    ledger = tmp_path / "l.jsonl"
    snap = {"kind": "POOL_SNAPSHOT", "record_key": "2026-09-09|POOL_SNAPSHOT|s|r2",
            "session_date": "2026-09-09", "price_source": "s", "status": "OK",
            "contemporaneous": True, "revision": 2, "pool_size": 100,
            "pool_members_unordered": ["A"], "universe_size": 1856, "coverage": {}}
    F.append_ledger([snap], str(ledger))
    F.append_ledger([F.invalidation_record(snap["record_key"], reason,
                                           session_date="2026-09-09",
                                           price_source="s", sequence=3)], str(ledger))
    return ledger


def test_an_operator_discarded_ok_row_leaves_zero_accepted_sessions(tmp_path):
    ledger = _ledger_with_retracted_ok(
        tmp_path, "operator_discarded_debug_run_before_first_evidence_maturity")
    s = F.summarise(str(ledger), "s")
    assert s["accepted_ok_sessions"] == 0
    assert s["accepted_ok_sessions_all_sources"] == 0
    assert s["first_accepted_session_pending"] is True
    assert s["retracted_snapshots"] == 1


def test_the_discarded_row_keeps_its_original_status_and_reason(tmp_path):
    reason = "operator_discarded_debug_run_before_first_evidence_maturity"
    ledger = _ledger_with_retracted_ok(tmp_path, reason)
    rows = F.read_ledger(str(ledger))
    assert len(rows) == 2                      # nothing deleted
    assert rows[0]["status"] == "OK" and rows[0]["pool_size"] == 100
    d = F.summarise(str(ledger), "s")["discarded_rows"]
    assert len(d) == 1
    assert d[0]["original_status"] == "OK"
    assert d[0]["original_pool_size"] == 100
    assert d[0]["reason"] == reason


def test_the_report_states_that_no_accepted_session_exists(tmp_path):
    ledger = _ledger_with_retracted_ok(
        tmp_path, "operator_discarded_debug_run_before_first_evidence_maturity")
    doc = F.render_doc(F.summarise(str(ledger), "s"))
    assert "No accepted forward sessions yet" in doc
    assert "zero accepted OK forward observations" in doc
    assert "debug and validation day" in doc
    assert "pending" in doc
    assert "No accepted session exists yet, so no horizon can mature" in doc
    assert "Nothing above was deleted" in doc


def test_the_retracted_rows_table_has_well_formed_cells(tmp_path):
    """Record keys contain '|', which would otherwise split the table cell."""
    import re
    ledger = _ledger_with_retracted_ok(tmp_path, "reason|with|pipes")
    doc = F.render_doc(F.summarise(str(ledger), "s"))
    sec = doc.split("### Retracted rows, in order")[1].split("Nothing above")[0]
    for line in [l for l in sec.splitlines() if l.startswith("|")]:
        assert len(re.split(r"(?<!\\)\|", line.strip())[1:-1]) == 4, line


def test_a_later_accepted_session_clears_the_pending_state(tmp_path):
    """The pending banner must disappear on its own once evidence starts."""
    ledger = _ledger_with_retracted_ok(
        tmp_path, "operator_discarded_debug_run_before_first_evidence_maturity")
    F.append_ledger([{
        "kind": "POOL_SNAPSHOT", "record_key": "2026-09-16|POOL_SNAPSHOT|s",
        "session_date": "2026-09-16", "price_source": "s", "status": "OK",
        "contemporaneous": True, "pool_size": 100,
        "pool_members_unordered": ["A"], "universe_size": 1800, "coverage": {}}], str(ledger))
    s = F.summarise(str(ledger), "s")
    assert s["accepted_ok_sessions"] == 1
    assert s["first_accepted_session_pending"] is False
    assert "No accepted forward sessions yet" not in F.render_doc(s)

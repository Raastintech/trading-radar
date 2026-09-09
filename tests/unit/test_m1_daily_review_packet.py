"""Guards for the M1 daily manual-review packet (research-only, cache-only).

The packet exists to hand a human everything needed to review the M1 pool by
hand — and nothing that would do the reviewing for them. These tests pin the
line between those two things:

* the guard runs first, and a refusal produces a refusal packet with no pool;
* it makes no provider call, and the refresh is planned but never executed;
* it writes only its own three artifacts, never a live one;
* it publishes no rank, no score, no momentum and no trade language;
* the manual columns exist and are left blank;
* the same session produces the same packet, run twice.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import research.backtests.m1_daily_review_packet as P
from research.backtests.common import ReplayWriteViolation, assert_replay_write_path
from research.backtests.m1_data_guard import M1DataAudit, M1DataStatus, M1RefusalReason
from research.backtests.m1_shadow_pool import ForbiddenLanguage

SESSION = "2026-09-04"


def _audit(*, may_run: bool) -> M1DataAudit:
    refusals = [] if may_run else [M1RefusalReason.CACHE_STALE]
    return M1DataAudit(
        status=M1DataStatus.M1_DATA_DEGRADED if may_run else M1DataStatus.M1_DATA_BLOCKED,
        refusals=refusals,
        warnings=["a gappy series was excluded"],
        header={"status": "M1_DATA_DEGRADED", "may_run": may_run,
                "universe_size": 3000, "names_with_enough_bars": 2800,
                "coverage_pct": 93.3, "median_bars": 1600.0, "stale_count": 3,
                "shallow_count": 100, "contamination_pct": 1.1,
                "cache_lag_sessions": 0, "m1_pool_size_if_run": 560,
                "refusal_reason": None if may_run else "cache_stale"},
    )


def _pool_artifact(tickers=("BBB", "AAA")) -> dict:
    entries = []
    for t in tickers:
        entries.append({
            "ticker": t, "sector": "Technology",
            "tags": {
                "analyst_attention": {"tag": "rating_raised"},
                "earnings_surprise": {"tag": "no_data_in_cached_feed"},
                "fundamental_quality": {"tag": "quality_pass"},
                "liquidity": {"band": "liquid_20M_100M",
                              "dollar_volume_median_20d_musd": 42.0},
                "volatility_exhaustion": {"atr14_pct": 3.0,
                                          "risk_flags": ["at_or_near_52w_high"]},
                "scanner_saturation_warning": None,
                "live_surface_overlap": [],
                "data_quality_warnings": [],
            },
            "reference": {"close": 10.0, "last_bar": SESSION, "bars": 400},
        })
    return {"kind": "M1_SHADOW_SOURCE_POOL", "session": SESSION,
            "pool_unordered": sorted(tickers), "entries": entries,
            "membership": {"membership_size": 560},
            "pool_stats": {"earnings_feed_last_report_date": None,
                           "earnings_feed_sessions_behind": None}}


def sandbox(tmp_path: Path, *, pool: dict | None = None) -> Path:
    (tmp_path / "cache/research").mkdir(parents=True)
    if pool is not None:
        (tmp_path / P.POOL_ARTIFACT_REL).write_text(json.dumps(pool))
    return tmp_path


def args_for(root: Path, **kw):
    argv = ["packet", "--root", str(root)]
    for k, v in kw.items():
        flag = "--" + k.replace("_", "-")
        argv += [flag] if v is True else [flag, str(v)]
    return P.build_parser().parse_args(argv)


def run(root: Path, monkeypatch, *, may_run: bool = True, **kw) -> int:
    monkeypatch.setattr(P, "run_audit", lambda *a, **k: _audit(may_run=may_run))
    # Pin the session to the fixture pool's own date. Without this the target
    # is whatever the wall clock says the required session is, the artifact no
    # longer matches, and the packet tries to REBUILD the pool — reaching the
    # real guard against an empty sandbox. These tests are about the packet,
    # not about what day it is.
    kw.setdefault("date", SESSION)
    return P.packet_stage(args_for(root, **kw))


def payload_of(root: Path) -> dict:
    return json.loads((root / P.LATEST_REL).read_text())


# ── 1. the guard runs first ─────────────────────────────────────────────────


def test_a_guard_refusal_writes_a_refusal_packet_and_no_pool(tmp_path, monkeypatch):
    root = sandbox(tmp_path, pool=_pool_artifact())
    rc = run(root, monkeypatch, may_run=False)
    assert rc == 3

    p = payload_of(root)
    assert p["kind"] == "M1_DAILY_REVIEW_PACKET_REFUSED"
    assert p["may_run"] is False
    assert p["pool_emitted"] is None and p["pool_size"] == 0
    assert "names" not in p
    doc = (root / P.DOC_REL).read_text()
    assert "REFUSED" in doc and "no pool was formed" in doc.lower()


def test_may_run_true_builds_the_packet(tmp_path, monkeypatch):
    root = sandbox(tmp_path, pool=_pool_artifact())
    assert run(root, monkeypatch) == 0
    p = payload_of(root)
    assert p["kind"] == "M1_DAILY_REVIEW_PACKET"
    assert p["may_run"] is True
    assert p["pool_size_emitted"] == 2


# ── 2. cache-only, plan-only ────────────────────────────────────────────────


def test_no_provider_call_is_made(tmp_path, monkeypatch):
    import socket

    monkeypatch.setattr(socket.socket, "connect",
                        lambda *a, **k: pytest.fail("packet attempted a network call"))
    root = sandbox(tmp_path, pool=_pool_artifact())
    run(root, monkeypatch)
    assert payload_of(root)["provider_calls"] == 0


def test_the_refresh_is_planned_and_never_executed(tmp_path, monkeypatch):
    root = sandbox(tmp_path, pool=_pool_artifact())
    run(root, monkeypatch)
    r = payload_of(root)["refresh"]
    assert r["mode"] == "plan_only"
    assert r["provider_calls_made"] == 0
    assert r["this_module_did_not_run_it"] is True


def test_execute_refresh_is_refused_rather_than_silently_ignored(tmp_path, monkeypatch):
    root = sandbox(tmp_path, pool=_pool_artifact())
    monkeypatch.setattr(P, "run_audit", lambda *a, **k: _audit(may_run=True))
    assert P.main(["packet", "--root", str(root), "--execute-refresh"]) == 3


def test_a_small_queue_is_called_optional_not_required(tmp_path, monkeypatch):
    root = sandbox(tmp_path, pool=_pool_artifact())
    run(root, monkeypatch)
    assert payload_of(root)["refresh"]["necessity"] == "optional"


# ── 3. containment ──────────────────────────────────────────────────────────


def test_the_three_artifacts_are_replay_paths():
    for rel in (P.LATEST_REL, P.REPORT_TXT_REL, P.DOC_REL):
        assert_replay_write_path(rel)


def test_the_docs_allowance_is_one_exact_file_not_the_directory():
    """A directory allowance over docs/research/ would let a replay overwrite
    live operator documents. It must stay a single named file."""
    assert_replay_write_path("docs/research/M1_DAILY_REVIEW_PACKET.md")
    for escape in ("docs/research/DAILY_ALPHA_RADAR_REPORT.md",
                   "docs/research/NIGHTLY_OPERATOR_SUMMARY.md",
                   "docs/research/M1_DAILY_REVIEW_PACKET/nested.md",
                   "docs/research/anything_else.md"):
        with pytest.raises(ReplayWriteViolation):
            assert_replay_write_path(escape)


@pytest.mark.parametrize("forbidden", [
    "cache/prices/AAA.parquet",
    "cache/research/research_scanner_latest.json",
    "cache/research/high_conviction_alpha_latest.json",
    "cache/research/alpha_focus_latest.json",
    "data/research/journal.jsonl",
    "db/trading.db",
])
def test_the_packet_cannot_write_a_live_artifact_or_ledger(forbidden):
    with pytest.raises(ReplayWriteViolation):
        assert_replay_write_path(forbidden)


def test_a_run_touches_no_live_artifact(tmp_path, monkeypatch):
    """The tripwire asserts this inside the stage; a violation raises."""
    root = sandbox(tmp_path, pool=_pool_artifact())
    assert run(root, monkeypatch) == 0


# ── 4. it never becomes a recommendation ────────────────────────────────────


def test_no_validated_edge_token_anywhere(tmp_path, monkeypatch):
    root = sandbox(tmp_path, pool=_pool_artifact())
    run(root, monkeypatch)
    blob = ((root / P.DOC_REL).read_text() + (root / P.REPORT_TXT_REL).read_text()
            + (root / P.LATEST_REL).read_text()).lower()
    assert "validated_edge" not in blob


def test_trade_signal_language_is_refused_at_render_time():
    with pytest.raises(ForbiddenLanguage):
        P.assert_packet_language("our best idea with an entry price", where="test")
    with pytest.raises(ForbiddenLanguage):
        P.assert_packet_language("ranked by strength, the strongest name is AAA",
                                 where="test")


def test_the_disclaimers_are_still_sayable():
    """A checker that cannot tell 'not a buy list' from 'buy list' would force
    the disclaimers out of the document — the exact wrong outcome."""
    P.assert_packet_language(
        "This is not a buy list. It is not ranked and has no conviction tier.",
        where="test")


def test_no_ranking_scoring_or_ordering_field_is_published(tmp_path, monkeypatch):
    root = sandbox(tmp_path, pool=_pool_artifact())
    run(root, monkeypatch)
    banned = ("rank", "score", "tier", "conviction", "signal", "momentum",
              "mom_12_1", "strength", "position_size", "target")
    for row in payload_of(root)["names"]:
        for key in row:
            assert not any(b in key.lower() for b in banned), key


def test_no_per_name_momentum_leaks_into_the_output(tmp_path, monkeypatch):
    """Publishing per-name 12-1 would invite exactly the re-sort the evidence
    says carries no information."""
    root = sandbox(tmp_path, pool=_pool_artifact())
    run(root, monkeypatch)
    blob = (root / P.DOC_REL).read_text().lower()
    assert "mom_12_1" not in blob
    assert "12-1 momentum" not in blob or "never publishes per-name" in blob
    for row in payload_of(root)["names"]:
        assert "mom_12_1" not in row


def test_the_display_order_is_declared_meaningless(tmp_path, monkeypatch):
    root = sandbox(tmp_path, pool=_pool_artifact(("ZZZ", "AAA", "MMM")))
    run(root, monkeypatch)
    p = payload_of(root)
    assert [r["ticker"] for r in p["names"]] == ["AAA", "MMM", "ZZZ"]
    assert "carries no information" in p["display_order"]


# ── 5. the manual half is left to the human ─────────────────────────────────


def test_manual_bucket_and_notes_exist_and_are_blank(tmp_path, monkeypatch):
    root = sandbox(tmp_path, pool=_pool_artifact())
    run(root, monkeypatch)
    p = payload_of(root)
    for row in p["names"]:
        assert row["manual_bucket"] == ""
        assert row["manual_notes"] == ""
    assert p["manual_buckets_available"] == ["Research Now", "Watch", "Reject"]
    assert "| bucket | notes |" in (root / P.DOC_REL).read_text()


def test_the_full_review_checklist_is_present(tmp_path, monkeypatch):
    root = sandbox(tmp_path, pool=_pool_artifact())
    run(root, monkeypatch)
    checklist = payload_of(root)["manual_review_checklist"]
    assert len(checklist) == 12
    assert "What invalidates the setup?" in checklist


def test_the_chatgpt_paste_block_is_included_by_default(tmp_path, monkeypatch):
    root = sandbox(tmp_path, pool=_pool_artifact())
    run(root, monkeypatch)
    p = payload_of(root)
    assert "Do not rank them" in p["chatgpt_brief"]
    doc = (root / P.DOC_REL).read_text()
    assert "Paste this into ChatGPT" in doc
    assert "Research Now, Watch, and Reject" in doc


def test_the_paste_block_can_be_turned_off(tmp_path, monkeypatch):
    root = sandbox(tmp_path, pool=_pool_artifact())
    monkeypatch.setattr(P, "run_audit", lambda *a, **k: _audit(may_run=True))
    P.packet_stage(P.build_parser().parse_args(
        ["packet", "--root", str(root), "--date", SESSION, "--no-chatgpt-brief"]))
    assert "chatgpt_brief" not in payload_of(root)


# ── 6. determinism ──────────────────────────────────────────────────────────


def test_the_same_session_produces_the_same_packet(tmp_path, monkeypatch):
    root = sandbox(tmp_path, pool=_pool_artifact())
    run(root, monkeypatch)
    first = payload_of(root)
    doc_first = (root / P.DOC_REL).read_text()

    run(root, monkeypatch)
    second = payload_of(root)
    doc_second = (root / P.DOC_REL).read_text()

    for d in (first, second):
        d.pop("generated_at")
    assert first == second

    strip = lambda s: "\n".join(l for l in s.splitlines() if "Generated" not in l)
    assert strip(doc_first) == strip(doc_second)


def test_a_pool_for_a_past_session_is_refused_rather_than_re_drawn(tmp_path, monkeypatch):
    """Re-dating the draw would publish a pool that session never published."""
    root = sandbox(tmp_path, pool=_pool_artifact())
    monkeypatch.setattr(P, "run_audit", lambda *a, **k: _audit(may_run=True))
    assert P.main(["packet", "--root", str(root), "--date", "2020-01-02"]) == 3


def test_no_seed_flag_is_exposed():
    """A --seed would let someone re-roll the draw until they liked the names."""
    assert not any("seed" in (a.dest or "")
                   for a in P.build_parser()._actions)


# ── 7. the frozen cohort is read, never written ─────────────────────────────


def test_the_cohort_section_is_read_only(tmp_path, monkeypatch):
    root = sandbox(tmp_path, pool=_pool_artifact())
    cohorts = root / "research/backtests/cohorts"
    cohorts.mkdir(parents=True)
    cohort = {"session": SESSION, "n_picks": 2, "n_control": 5,
              "picks": [{"ticker": "AAA", "reference_bar": SESSION},
                        {"ticker": "BBB", "reference_bar": SESSION}],
              "horizons_sessions": [20, 45], "decisive_horizons": [45],
              "hypothesis": "h"}
    path = cohorts / f"m1_manual_picks_{SESSION}.json"
    path.write_text(json.dumps(cohort))
    before = path.read_text()

    run(root, monkeypatch)
    c = payload_of(root)["cohort"]
    assert c["present"] is True and c["picks"] == ["AAA", "BBB"]
    assert c["not_modified_by_this_packet"] is True
    assert c["maturity_dates_business_day_estimate"]["45d"] > SESSION
    assert path.read_text() == before          # byte-identical after the run


def test_a_missing_cohort_is_reported_not_invented(tmp_path, monkeypatch):
    root = sandbox(tmp_path, pool=_pool_artifact())
    run(root, monkeypatch)
    assert payload_of(root)["cohort"]["present"] is False


# ── 8. the review-only filter ───────────────────────────────────────────────
#
# The filter narrows REVIEW TIME. These pin that it never narrows M1: the full
# pool stays in the artifact, and nothing here reaches membership, the 12-1
# construction, the frozen cohort, or any live surface.


def _entry(ticker, *, sector="Technology", band="liquid_20M_100M",
           quality="quality_pass", analyst="rating_raised", flags=(), warnings=()):
    return {
        "ticker": ticker, "sector": sector,
        "tags": {
            "analyst_attention": {"tag": analyst},
            "earnings_surprise": {"tag": "no_data_in_cached_feed"},
            "fundamental_quality": {"tag": quality},
            "liquidity": {"band": band, "dollar_volume_median_20d_musd": 42.0},
            "volatility_exhaustion": {"atr14_pct": 3.0, "risk_flags": list(flags)},
            "scanner_saturation_warning": None,
            "live_surface_overlap": [],
            "data_quality_warnings": list(warnings),
        },
        "reference": {"close": 10.0, "last_bar": SESSION, "bars": 400},
    }


def _mixed_pool() -> dict:
    """One clean name plus one for every hard-exclusion rule."""
    entries = [
        _entry("KEEP"),
        _entry("KEEPTWO", quality="quality_fail", analyst="no_action_in_window"),
        _entry("THIN", band="thin_under_5M"),
        _entry("BELOW", flags=["below_ma200"]),
        _entry("DRAWDOWN", flags=["deep_drawdown_from_52w_high"]),
        _entry("EXTENDED", flags=["extended_far_above_ma50"]),
        _entry("RANGE", flags=["high_daily_range"]),
        _entry("FILINGS", quality="insufficient_filings"),
        _entry("CHEAP", warnings=["low_price"]),
        _entry("NOSECTOR", sector="UNKNOWN", quality="no_fundamental_data"),
        _entry("NOSECTORLIQUID", sector="UNKNOWN", quality="no_fundamental_data",
               band="very_liquid_100M_plus"),
    ]
    tickers = sorted(e["ticker"] for e in entries)
    return {"kind": "M1_SHADOW_SOURCE_POOL", "session": SESSION,
            "pool_unordered": tickers, "entries": entries,
            "membership": {"membership_size": 560},
            "pool_stats": {"earnings_feed_last_report_date": None,
                           "earnings_feed_sessions_behind": None}}


def test_the_review_filter_excludes_every_hard_excluded_shape(tmp_path, monkeypatch):
    root = sandbox(tmp_path, pool=_mixed_pool())
    run(root, monkeypatch)
    mr = payload_of(root)["manual_review"]

    kept = {r["ticker"] for r in mr["candidates"]}
    assert kept == {"KEEP", "KEEPTWO", "NOSECTORLIQUID"}
    for gone in ("THIN", "BELOW", "DRAWDOWN", "EXTENDED", "RANGE", "FILINGS",
                 "CHEAP", "NOSECTOR"):
        assert gone not in kept, f"{gone} should not be a review candidate"


def test_a_very_liquid_unknown_name_survives_the_unknown_rule(tmp_path, monkeypatch):
    """UNKNOWN + no fundamentals is a cache gap when the name is this liquid."""
    root = sandbox(tmp_path, pool=_mixed_pool())
    run(root, monkeypatch)
    mr = payload_of(root)["manual_review"]
    assert "NOSECTORLIQUID" in {r["ticker"] for r in mr["candidates"]}
    assert "NOSECTOR" in {e["ticker"] for e in mr["excluded"]}


def test_the_exclusion_counts_are_published(tmp_path, monkeypatch):
    root = sandbox(tmp_path, pool=_mixed_pool())
    run(root, monkeypatch)
    mr = payload_of(root)["manual_review"]
    assert mr["full_pool_count"] == 11
    assert mr["manual_review_candidate_count"] == 3
    assert mr["excluded_thin_liquidity"] == 1
    assert mr["excluded_below_ma200"] == 1
    assert mr["excluded_deep_drawdown"] == 1
    assert mr["excluded_extended"] == 1
    assert mr["excluded_high_daily_range"] == 1
    assert mr["excluded_data_quality"] == 2      # insufficient_filings + low_price
    assert mr["excluded_unknown_no_fundamentals"] == 1
    assert mr["excluded_distinct_name_count"] == 8


def test_a_name_tripping_several_rules_counts_once_per_reason(tmp_path, monkeypatch):
    root = sandbox(tmp_path, pool={
        **_mixed_pool(),
        "entries": [_entry("KEEP"),
                    _entry("MULTI", band="thin_under_5M",
                           flags=["below_ma200", "high_daily_range"])],
        "pool_unordered": ["KEEP", "MULTI"]})
    run(root, monkeypatch)
    mr = payload_of(root)["manual_review"]
    assert mr["excluded_distinct_name_count"] == 1
    assert (mr["excluded_thin_liquidity"] + mr["excluded_below_ma200"]
            + mr["excluded_high_daily_range"]) == 3
    reasons = mr["excluded"][0]["exclusion_reasons"]
    assert set(reasons) == {"thin_liquidity", "below_ma200", "high_daily_range"}


def test_the_full_pool_is_preserved_for_measurement_and_audit(tmp_path, monkeypatch):
    """The filter is a view. Losing the pool would break forward measurement."""
    root = sandbox(tmp_path, pool=_mixed_pool())
    run(root, monkeypatch)
    p = payload_of(root)

    assert len(p["names"]) == 11
    assert p["pool_size_emitted"] == 11
    assert {r["ticker"] for r in p["names"]} == {
        "KEEP", "KEEPTWO", "THIN", "BELOW", "DRAWDOWN", "EXTENDED", "RANGE",
        "FILINGS", "CHEAP", "NOSECTOR", "NOSECTORLIQUID"}
    # every excluded name is still somewhere in the artifact
    for e in p["manual_review"]["excluded"]:
        assert e["ticker"] in {r["ticker"] for r in p["names"]}
    doc = (root / P.DOC_REL).read_text()
    assert "## 5. Name table" in doc and "THIN" in doc


def test_the_review_section_is_rendered_with_its_counts(tmp_path, monkeypatch):
    root = sandbox(tmp_path, pool=_mixed_pool())
    run(root, monkeypatch)
    doc = (root / P.DOC_REL).read_text()
    assert "## 6. M1 Manual Review Candidates" in doc
    for key in ("full_pool_count", "manual_review_candidate_count",
                "excluded_thin_liquidity", "excluded_below_ma200",
                "excluded_deep_drawdown", "excluded_extended",
                "excluded_high_daily_range", "excluded_data_quality",
                "excluded_unknown_no_fundamentals"):
        assert key in doc


def test_preferences_annotate_but_never_order_or_score(tmp_path, monkeypatch):
    root = sandbox(tmp_path, pool=_mixed_pool())
    run(root, monkeypatch)
    mr = payload_of(root)["manual_review"]

    tickers = [r["ticker"] for r in mr["candidates"]]
    assert tickers == sorted(tickers), "candidates must stay alphabetical"
    prefs = {r["ticker"]: r["review_preferences"] for r in mr["candidates"]}
    assert prefs["KEEP"]["quality_pass"] is True
    assert prefs["KEEPTWO"]["quality_pass"] is False
    # KEEPTWO has fewer preferences than KEEP yet is not moved, dropped or scored
    assert "KEEPTWO" in tickers
    for r in mr["candidates"]:
        assert all(isinstance(v, bool) for v in r["review_preferences"].values())
        assert not any(k in r for k in
                       ("score", "rank", "tier", "preference_score", "priority"))


# ── 9. the paste block defaults to the short list ───────────────────────────


def test_the_paste_block_uses_review_candidates_by_default(tmp_path, monkeypatch):
    root = sandbox(tmp_path, pool=_mixed_pool())
    run(root, monkeypatch)
    brief = payload_of(root)["chatgpt_brief"]

    assert "KEEP  " in brief or "KEEP " in brief
    for gone in ("THIN", "BELOW", "DRAWDOWN", "EXTENDED", "RANGE", "CHEAP"):
        assert f"\n{gone} " not in brief, f"{gone} should not be pasted"
    assert "manual-review candidates" in brief
    assert "held back:" in brief


def test_the_paste_block_can_still_carry_the_full_pool(tmp_path, monkeypatch):
    root = sandbox(tmp_path, pool=_mixed_pool())
    run(root, monkeypatch, chatgpt_brief_scope="full-pool")
    brief = payload_of(root)["chatgpt_brief"]
    for name in ("KEEP", "THIN", "BELOW", "DRAWDOWN", "EXTENDED", "RANGE"):
        assert name in brief
    assert "full unfiltered pool" in brief


# ── 10. the API safety guard ────────────────────────────────────────────────


def test_the_default_run_makes_zero_provider_calls(tmp_path, monkeypatch):
    import socket

    monkeypatch.setattr(socket.socket, "connect",
                        lambda *a, **k: pytest.fail("packet attempted a network call"))
    root = sandbox(tmp_path, pool=_mixed_pool())
    assert run(root, monkeypatch) == 0
    p = payload_of(root)
    assert p["provider_calls"] == 0
    assert p["refresh"]["provider_calls_made"] == 0
    assert p["refresh"]["mode"] == "plan_only"


def test_a_provider_calling_path_is_refused_without_the_flag(tmp_path, monkeypatch):
    """If a future path ever spends, it stops here rather than at the invoice."""
    root = sandbox(tmp_path, pool=_mixed_pool())
    monkeypatch.setattr(P, "run_audit", lambda *a, **k: _audit(may_run=True))
    args = args_for(root, date=SESSION)

    payload = {"provider_calls": 3, "refresh": {"planned_calls": 0}}
    with pytest.raises(P.FetchNotAuthorised, match="cache-only by design"):
        P.assert_api_safety(payload, args)

    args.allow_provider_calls = True
    P.assert_api_safety(payload, args)      # explicit authorisation passes


def test_a_large_planned_refresh_is_refused_without_an_override(tmp_path, monkeypatch):
    root = sandbox(tmp_path, pool=_mixed_pool())
    args = args_for(root, date=SESSION)
    over = {"provider_calls": 0,
            "refresh": {"planned_calls": P.MAX_PLANNED_REFRESH_CALLS + 1}}

    with pytest.raises(P.FetchNotAuthorised, match="refresh call"):
        P.assert_api_safety(over, args)

    args.allow_large_refresh_plan = True
    P.assert_api_safety(over, args)

    at_ceiling = {"provider_calls": 0,
                  "refresh": {"planned_calls": P.MAX_PLANNED_REFRESH_CALLS}}
    P.assert_api_safety(at_ceiling, args_for(root, date=SESSION))


def test_the_ceiling_refusal_fetches_nothing(tmp_path, monkeypatch):
    import socket

    monkeypatch.setattr(socket.socket, "connect",
                        lambda *a, **k: pytest.fail("packet attempted a network call"))
    root = sandbox(tmp_path, pool=_mixed_pool())
    monkeypatch.setattr(P, "refresh_block", lambda r: {
        "mode": "plan_only", "planned_calls": 5000, "necessity": "optional",
        "n_stale": 5000, "n_shallow": 0, "n_missing": 0,
        "n_young_listing_excluded": 0, "n_provider_lag_deferred": 0,
        "provider_calls_made": 0, "this_module_did_not_run_it": True,
        "operator_command": None})
    with pytest.raises(P.FetchNotAuthorised):
        run(root, monkeypatch)


# ── 11. nothing production-facing moved ─────────────────────────────────────


def test_the_review_filter_publishes_no_rank_score_tier_or_trade_language(
        tmp_path, monkeypatch):
    root = sandbox(tmp_path, pool=_mixed_pool())
    run(root, monkeypatch)
    p = payload_of(root)
    blob = json.dumps(p["manual_review"]).lower()

    for token in ("rank", "score", "tier", "conviction", "buy", "sell",
                  "trade signal", "entry", "target", "position size"):
        assert token not in blob, f"{token!r} leaked into the review section"

    # the rendered surfaces go through the language guard too
    P.assert_packet_language(P.render_doc(p), where="doc")
    P.assert_packet_language(p["chatgpt_brief"], where="brief")


def test_the_filter_touches_no_production_or_live_artifact(tmp_path, monkeypatch):
    """Same tripwire as the rest of the packet, re-pinned for the new section."""
    root = sandbox(tmp_path, pool=_mixed_pool())
    run(root, monkeypatch)

    written = {p.relative_to(root).as_posix()
               for p in root.rglob("*") if p.is_file()}
    assert written == {P.POOL_ARTIFACT_REL, P.LATEST_REL, P.DOC_REL, P.REPORT_TXT_REL}
    for forbidden in ("cache/research/research_scanner_latest.json",
                      "cache/research/alpha_focus_latest.json",
                      "cache/research/high_conviction_alpha_latest.json",
                      "db/trading.db"):
        assert not (root / forbidden).exists()


def test_the_frozen_cohort_is_not_touched_by_the_filter(tmp_path, monkeypatch):
    root = sandbox(tmp_path, pool=_mixed_pool())
    cohorts = root / "research/backtests/cohorts"
    cohorts.mkdir(parents=True)
    frozen = cohorts / "m1_manual_picks_2026-09-04.json"
    body = json.dumps({"kind": "M1_MANUAL_PICK_COHORT", "session": "2026-09-04",
                       "picks": [{"ticker": "THIN", "reference_close": 1.0,
                                  "reference_bar": "2026-09-04"}],
                       "control_not_picked": [], "decisive_horizons": [45, 60],
                       "horizons_sessions": [20, 45, 60, 90]})
    frozen.write_text(body)

    run(root, monkeypatch)
    assert frozen.read_text() == body, "the packet rewrote the frozen cohort"

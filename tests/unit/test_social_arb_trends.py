"""Unit tests for Google Trends integration in research/social_arb_radar.py.

Covers the seven scenarios called out in the audit:
    1. pytrends unavailable -> graceful no-op
    2. below-threshold spike ignored
    3. above-threshold spike attached to an existing news group
    4. Trends without a matching news group is invisible
    5. Trends + weak/no tape does NOT become Cross-Confirmed Lead
    6. Trends + valid news + tape_ok adds corroboration (XCONF allowed only
       when the deterministic-score floor is also met)
    7. source_count / source_types stay honest (Trends not counted as source)
    8. Threshold gate: z=1.4 with default threshold 2.0 emits nothing
    9. Absolute-relevance floor: latest=5 with z>=threshold emits nothing
   10. Ambiguous-alias selection avoids generic English words

These are pure-Python tests — no network, no provider calls. pytrends is
stubbed via monkeypatch where needed.
"""
from __future__ import annotations

import sys
import types
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import pytest

from research import social_arb_radar as sar


# ─── Helpers ────────────────────────────────────────────────────────────────


def _fresh_stats() -> Dict[str, Any]:
    return {"source_status": {}, "source_errors": [], "api_attempts": {}}


def _make_news_group(
    ticker: str = "AMZN",
    freshness_hours: float = 12.0,
    sources: tuple = ("Reuters",),
    source_types: tuple = ("news_api",),
    titles: tuple = ("Amazon reports record earnings",),
    mapping_method: str = "company_name",
) -> Dict[str, Any]:
    return {
        "ticker": ticker,
        "theme": "Consumer/App Demand",
        "titles": list(titles),
        "urls": ["https://example.com/news"],
        "sources": set(sources),
        "source_types": set(source_types),
        "freshness_hours": freshness_hours,
        "mapping_confidence": 0.9,
        "mapping_method": mapping_method,
        "mapping_labels": {"AMZN matched on company name"},
        "noise_terms": [],
    }


def _make_trend_item(
    ticker: str = "AMZN",
    z: float = 2.5,
    latest: float = 80.0,
    age_hours: float = 0.05,
) -> sar.NormalizedItem:
    title = (
        f"Relative Google Trends spike for ${ticker}: normalized interest "
        f"index {int(latest)}/100 (z={z:+.2f} vs 7d baseline; index is "
        f"relative to window peak, not absolute search volume)"
    )
    return sar.NormalizedItem(
        item_id=f"trend:{ticker}",
        title=title,
        source="Google Trends",
        source_type="google_trends",
        timestamp="2026-05-17T20:00:00+00:00",
        freshness_hours=age_hours,
        url=f"https://trends.google.com/trends/explore?q={ticker.lower()}",
        tickers_mentioned=[ticker],
        company_names=[],
        theme="Unclassified",
        source_payload_ref="raw[0]",
    )


# ─── 1. pytrends unavailable ────────────────────────────────────────────────


def test_pytrends_missing_returns_empty(monkeypatch):
    """If pytrends import fails, the fetcher must log a single error and return
    [], never raise."""
    # Force ImportError by inserting a sentinel that fails on attribute access
    saved = sys.modules.pop("pytrends", None)
    saved_request = sys.modules.pop("pytrends.request", None)
    sys.modules["pytrends"] = None  # type: ignore[assignment]
    try:
        stats = _fresh_stats()
        out = sar._fetch_google_trends(["AAPL", "NVDA"], stats)
        assert out == []
        assert stats["source_status"]["google_trends"] == "pytrends missing"
        assert any("pytrends not installed" in e for e in stats["source_errors"])
    finally:
        sys.modules.pop("pytrends", None)
        if saved is not None:
            sys.modules["pytrends"] = saved
        if saved_request is not None:
            sys.modules["pytrends.request"] = saved_request


# ─── 2 & 8 & 9. Threshold + relevance floor ─────────────────────────────────


class _StubTrendReq:
    """Stub pytrends client that returns a fixed pandas DataFrame for one call.

    Use `series_by_term` to feed a specific 7-day hourly series per term so
    tests can drive the z calculation directly.
    """

    def __init__(self, series_by_term: Dict[str, List[float]]):
        self._series = series_by_term
        self._current: List[str] = []

    def __call__(self, *a, **kw):  # mimics TrendReq(...) constructor
        return self

    def build_payload(self, kw_list, timeframe="now 7-d", geo="US"):
        self._current = list(kw_list)

    def interest_over_time(self):
        import pandas as pd

        rows = max((len(v) for v in self._series.values()), default=0)
        data = {t: (self._series.get(t) or [0] * rows) for t in self._current}
        # pad shorter lists with 0
        for t in data:
            if len(data[t]) < rows:
                data[t] = list(data[t]) + [0] * (rows - len(data[t]))
        df = pd.DataFrame(data, index=range(rows))
        return df


def _install_stub_pytrends(monkeypatch, series_by_term: Dict[str, List[float]]):
    stub_mod = types.ModuleType("pytrends.request")
    stub_mod.TrendReq = _StubTrendReq(series_by_term)  # type: ignore[attr-defined]
    pkg = types.ModuleType("pytrends")
    monkeypatch.setitem(sys.modules, "pytrends", pkg)
    monkeypatch.setitem(sys.modules, "pytrends.request", stub_mod)


def test_below_threshold_spike_ignored(monkeypatch):
    """z=1.4 with default threshold 2.0 must NOT emit a row."""
    # 6-flat baseline + slight bump → z ~ 1.4
    baseline = [50, 50, 50, 50, 50, 50]
    series = baseline + [60]
    _install_stub_pytrends(monkeypatch, {"apple": series})
    stats = _fresh_stats()
    out = sar._fetch_google_trends(["AAPL"], stats, z_threshold=2.0)
    assert out == []
    # No errors — this is a clean "no spike" outcome.
    assert stats["source_errors"] == []


def test_above_threshold_spike_emits_row(monkeypatch):
    """z >> 2.0 with latest >= TRENDS_MIN_LATEST emits exactly one row."""
    baseline = [50, 50, 50, 50, 50, 50]
    series = baseline + [95]  # huge spike, z = inf-ish (no variance) — make some
    series = [40, 45, 50, 55, 50, 45] + [95]  # gives z ≈ 8
    _install_stub_pytrends(monkeypatch, {"apple": series})
    stats = _fresh_stats()
    out = sar._fetch_google_trends(["AAPL"], stats, z_threshold=2.0)
    assert len(out) == 1
    row = out[0]
    assert row["source_type"] == "google_trends"
    assert row["symbol"] == "AAPL"
    # Wording check: "relative" + "not absolute" disclaimers must be present.
    assert "relative" in row["title"].lower()
    assert "absolute" in row["title"].lower()
    # Raw payload field names should reflect the relative-index semantics.
    assert "latest_relative_index" in row["raw"]
    assert "z_vs_7d_baseline" in row["raw"]


def test_low_latest_blocks_emission_even_with_high_z(monkeypatch):
    """Spike from 1→5 (huge z, but latest=5 < TRENDS_MIN_LATEST=20) must be dropped."""
    series = [1, 1, 1, 1, 1, 1, 5]  # z huge, latest=5
    _install_stub_pytrends(monkeypatch, {"apple": series})
    stats = _fresh_stats()
    out = sar._fetch_google_trends(["AAPL"], stats, z_threshold=2.0)
    assert out == []


# ─── 3. Above-threshold spike attaches to news group ────────────────────────


def test_above_threshold_attaches_to_group():
    groups = {"AMZN:earnings": _make_news_group()}
    trend = _make_trend_item(ticker="AMZN", z=3.0, latest=90)
    stats = _fresh_stats()
    attached = sar.attach_trends_to_groups(groups, [trend], stats)
    assert attached == 1
    g = groups["AMZN:earnings"]
    assert g.get("has_google_trends") is True
    assert g.get("trend_z") == pytest.approx(3.0)
    assert g.get("trend_latest") == pytest.approx(90)


# ─── 4. Trends without news group is invisible ──────────────────────────────


def test_trends_without_news_group_is_invisible():
    """No matching group → no attachment, no mutation, no side effects."""
    groups: Dict[str, Dict[str, Any]] = {}
    trend = _make_trend_item(ticker="AMZN")
    stats = _fresh_stats()
    attached = sar.attach_trends_to_groups(groups, [trend], stats)
    assert attached == 0
    assert groups == {}


def test_trends_for_unrelated_ticker_is_invisible():
    """Trends spike for AMZN does NOT touch a TSLA news group."""
    groups = {"TSLA:robotaxi": _make_news_group(ticker="TSLA")}
    trend = _make_trend_item(ticker="AMZN")
    stats = _fresh_stats()
    attached = sar.attach_trends_to_groups(groups, [trend], stats)
    assert attached == 0
    tsla = groups["TSLA:robotaxi"]
    assert tsla.get("has_google_trends") is None
    assert tsla.get("trend_z") is None


# ─── 5. Trends + weak/no tape does NOT promote to XCONF ─────────────────────


def test_trends_with_no_tape_stays_news_catalyst():
    """tape_ok = False + Trends spike + 1 news source must NOT promote."""
    no_tape = {"available": True, "confirmation": False}
    bucket = sar._bucket_for_candidate(
        source_count=1,
        mapping_method="company_name",
        tape=no_tape,
        options={"available": False, "confirmation": False},
        specific_catalyst=True,
        score=80.0,  # high score, but no tape
        has_google_trends=True,
        trend_z=3.5,
        trend_z_threshold=2.0,
    )
    assert bucket == "News Catalyst"


def test_trends_with_weak_score_stays_news_catalyst():
    """tape_ok = True + Trends + specific catalyst but score < floor must NOT promote."""
    tape_ok = {"available": True, "confirmation": True}
    bucket = sar._bucket_for_candidate(
        source_count=1,
        mapping_method="company_name",
        tape=tape_ok,
        options={"available": False, "confirmation": False},
        specific_catalyst=True,
        score=sar.TRENDS_XCONF_SCORE_FLOOR - 0.1,  # just under
        has_google_trends=True,
        trend_z=3.0,
        trend_z_threshold=2.0,
    )
    assert bucket == "News Catalyst"


def test_trends_with_low_z_stays_news_catalyst():
    """tape_ok + score above floor but trend_z below threshold must NOT promote."""
    tape_ok = {"available": True, "confirmation": True}
    bucket = sar._bucket_for_candidate(
        source_count=1,
        mapping_method="company_name",
        tape=tape_ok,
        options={"available": False, "confirmation": False},
        specific_catalyst=True,
        score=80.0,
        has_google_trends=True,
        trend_z=1.5,  # below threshold
        trend_z_threshold=2.0,
    )
    assert bucket == "News Catalyst"


def test_trends_without_specific_catalyst_stays_emerging_or_below():
    """No named catalyst (theme inference only) cannot become XCONF via Trends."""
    tape_ok = {"available": True, "confirmation": True}
    bucket = sar._bucket_for_candidate(
        source_count=1,
        mapping_method="theme_inference",
        tape=tape_ok,
        options={"available": False, "confirmation": False},
        specific_catalyst=False,
        score=80.0,
        has_google_trends=True,
        trend_z=3.0,
        trend_z_threshold=2.0,
    )
    assert bucket != "Cross-Confirmed Lead"


# ─── 6. Trends + valid news + tape_ok can corroborate ──────────────────────


def test_trends_corroborates_news_into_xconf():
    """All conditions met: 1 real news + tape + named catalyst + z >= threshold
    + score >= floor → XCONF allowed."""
    tape_ok = {"available": True, "confirmation": True}
    bucket = sar._bucket_for_candidate(
        source_count=1,
        mapping_method="company_name",
        tape=tape_ok,
        options={"available": False, "confirmation": False},
        specific_catalyst=True,
        score=72.0,
        has_google_trends=True,
        trend_z=2.5,
        trend_z_threshold=2.0,
    )
    assert bucket == "Cross-Confirmed Lead"


def test_two_real_news_sources_still_xconf_without_trends():
    """Existing real-news XCONF path must keep working unchanged."""
    tape_ok = {"available": True, "confirmation": True}
    bucket = sar._bucket_for_candidate(
        source_count=2,
        mapping_method="company_name",
        tape=tape_ok,
        options={"available": False, "confirmation": False},
        specific_catalyst=True,
        score=60.0,
        has_google_trends=False,
        trend_z=None,
        trend_z_threshold=2.0,
    )
    assert bucket == "Cross-Confirmed Lead"


# ─── 7. source_count / source_types stay honest ─────────────────────────────


def test_attach_does_not_mutate_sources_or_source_types():
    """The decoupling invariant: Trends attachment must NOT add to sources,
    source_types, or urls."""
    group = _make_news_group()
    original_sources = set(group["sources"])
    original_source_types = set(group["source_types"])
    original_urls = list(group["urls"])
    trend = _make_trend_item(ticker="AMZN", z=3.0)
    sar.attach_trends_to_groups({"AMZN:e": group}, [trend], _fresh_stats())
    assert group["sources"] == original_sources
    assert group["source_types"] == original_source_types
    assert group["urls"] == original_urls


def test_attach_respects_max_age_window():
    """A 100h-old news group must not absorb a Trends spike with default 72h window."""
    group = _make_news_group(freshness_hours=100.0)
    trend = _make_trend_item(ticker="AMZN", z=3.0)
    sar.attach_trends_to_groups({"AMZN:e": group}, [trend], _fresh_stats(), max_age_hours=72.0)
    assert group.get("has_google_trends") is None


# ─── 10. Alias handling ────────────────────────────────────────────────────


def test_ambiguous_alias_is_skipped():
    """MSTR has aliases ['microstrategy', 'strategy']. 'strategy' is in the
    ambiguous denylist; 'microstrategy' must be selected."""
    # Sanity-check that 'strategy' is actually in the denylist used by the module
    assert "strategy" in sar.AMBIGUOUS_TRENDS_TERMS
    assert sar._google_trends_term("MSTR") == "microstrategy"


def test_known_clean_alias_is_used():
    """For unambiguous tickers, the canonical first alias is returned."""
    assert sar._google_trends_term("AAPL") == "apple"
    assert sar._google_trends_term("NVDA") == "nvidia"


def test_unknown_ticker_falls_back_to_symbol():
    """No alias → return the bare ticker (the caller's prior filter should
    keep ambiguous bare tickers out of the universe, but the function itself
    must still degrade safely)."""
    assert sar._google_trends_term("ZZZZZ") == "ZZZZZ"


def test_all_aliases_ambiguous_falls_back_to_ticker(monkeypatch):
    """If every alias for a symbol is in the denylist, fall back to the ticker."""
    monkeypatch.setattr(sar, "COMPANY_ALIASES", {"FOO": ["target", "snap"]}, raising=False)
    # All aliases ambiguous → should return ticker
    assert sar._google_trends_term("FOO") == "FOO"


# ─── 11. News-mapping precision (ambiguous alias / word boundary / location) ──


def _make_raw_news(title: str, description: str = "", symbol: str = "",
                   source: str = "zacks.com", timestamp: str | None = None):
    # Deterministic freshness: default to a recent timestamp relative to *now*
    # so the 168h `stale_source` gate in build_story_groups never drops the
    # fixture purely because wall-clock time has advanced since the test was
    # written (the old fixed "2026-05-29" string silently rotted past +7d and
    # turned this into a flaky stale-drop).  Pass an explicit `timestamp` to
    # test stale behavior on purpose.
    if timestamp is None:
        ts = datetime.now(timezone.utc) - timedelta(hours=2)
        timestamp = ts.strftime("%Y-%m-%d %H:%M:%S")
    return {
        "source_type": "fmp_stock_news",
        "source": source,
        "title": title,
        "description": description,
        "timestamp": timestamp,
        "url": "https://example.com/a",
        "symbol": symbol,
    }


def test_ambiguous_alias_terms_back_compat():
    """The denylist now serves both Trends and news mapping; the old name still
    points at the same set so existing callers/tests don't break."""
    assert sar.AMBIGUOUS_TRENDS_TERMS is sar.AMBIGUOUS_ALIAS_TERMS
    assert "strategy" in sar.AMBIGUOUS_ALIAS_TERMS


def test_netflix_apple_article_does_not_map_to_mstr():
    """Regression: the Zacks 'Netflix vs. Apple' article (FMP symbol AAPL) must
    map to {AAPL, NFLX} and NOT MSTR — MicroStrategy's generic 'strategy' alias
    appears only as the English word in 'premium content strategy'."""
    known = {"AAPL", "NFLX", "MSTR"}
    raw = [_make_raw_news(
        title="Netflix vs. Apple: Which Streaming Giant Is the Better Buy Right Now?",
        description="AAPL edges NFLX, backed by premium content strategy and resilient ecosystem momentum.",
        symbol="AAPL",
    )]
    item = sar.normalize_items(raw, known)[0]
    maps = {m.ticker: m for m in sar.map_item_to_tickers(item, known)}

    assert maps["MSTR"].method == "ambiguous_alias"
    assert maps["MSTR"].confidence == 0.0
    assert maps["AAPL"].confidence >= 0.80
    assert maps["NFLX"].confidence >= 0.80

    # And the suppressed name must not survive into a story group.
    ds = {"total": 0, "reasons": {}, "examples": []}
    groups, _ = sar.build_story_groups(item and [item], known, ds)
    assert sorted({g["ticker"] for g in groups.values()}) == ["AAPL", "NFLX"]
    assert ds["reasons"].get("ambiguous_generic_word_alias") == 1


def test_make_raw_news_fixture_is_fresh_by_default():
    """Regression guard: the news fixture must be FRESH relative to now so the
    168h `stale_source` gate in build_story_groups never silently drops it as
    wall-clock time advances.  (Previously a hard-coded date rotted past +7d and
    turned the Netflix/Apple mapping test into a flaky stale-drop.)"""
    item = sar.normalize_items([_make_raw_news(title="Apple earnings catalyst")],
                               {"AAPL"})[0]
    assert item.freshness_hours < 168, (
        f"fixture is stale ({item.freshness_hours:.0f}h) — story-group tests will "
        "flake; keep _make_raw_news timestamp relative to now")
    # An explicit stale override must still be honoured for stale-path tests.
    stale = sar.normalize_items(
        [_make_raw_news(title="x", timestamp="2020-01-01 00:00:00")], {"AAPL"})[0]
    assert stale.freshness_hours > 168


def test_explicit_ticker_relevance_not_overridden_by_ambiguous_alias():
    """Theme/ambiguous inference must not override an explicit ticker's relevance.
    'Apple' is the explicit subject (high confidence); the generic English word
    'strategy' (MicroStrategy's alias) must stay suppressed, not promote MSTR."""
    known = {"AAPL", "MSTR"}
    raw = [_make_raw_news(
        title="Apple earnings beat as its content strategy pays off",
        description="A bold product strategy underpins the quarter.",
        symbol="AAPL",
    )]
    item = sar.normalize_items(raw, known)[0]
    maps = {m.ticker: m for m in sar.map_item_to_tickers(item, known)}
    assert maps["AAPL"].confidence >= 0.80
    assert maps["AAPL"].in_title is True
    assert maps["MSTR"].method == "ambiguous_alias"
    assert maps["MSTR"].confidence == 0.0


def test_ambiguous_terms_do_not_create_false_ticker_mapping():
    """Ambiguous English-word aliases ('strategy'→MSTR, 'target'→TGT) must never
    create a real ticker mapping when they are the ONLY signal — they map at
    confidence 0.0 / method 'ambiguous_alias' and are dropped from story groups."""
    known = {"MSTR", "TGT"}
    item = sar.normalize_items(
        [_make_raw_news(title="A new corporate strategy reshapes the retail target market")],
        known)[0]
    maps = {m.ticker: m for m in sar.map_item_to_tickers(item, known)}
    for tk in ("MSTR", "TGT"):
        assert maps[tk].method == "ambiguous_alias"
        assert maps[tk].confidence == 0.0
    # None of them may form a surviving story group.
    ds = {"total": 0, "reasons": {}, "examples": []}
    groups, _ = sar.build_story_groups([item], known, ds)
    assert all(g["ticker"] not in known for g in groups.values()) or not groups


def test_alias_match_is_word_boundary_not_substring():
    """A clean alias must match on word boundaries — 'disney' must not match
    inside 'disneyland', which the old `alias in low` substring test allowed."""
    known = {"DIS"}
    item = sar.normalize_items(
        [_make_raw_news(title="Disneyland opens a new themed park expansion")], known
    )[0]
    assert "DIS" not in item.ticker_evidence  # no spurious substring hit
    item2 = sar.normalize_items(
        [_make_raw_news(title="Disney raises streaming prices again")], known
    )[0]
    assert item2.ticker_evidence.get("DIS", {}).get("in_title") is True


def test_confidence_is_location_aware():
    """Headline > FMP-subject > body, per _confidence_from_evidence."""
    headline = sar._confidence_from_evidence(
        {"in_title": True, "direct_symbol": True, "is_subject": False}
    )
    body = sar._confidence_from_evidence({"in_body": True, "alias": "oracle"})
    subject = sar._confidence_from_evidence({"is_subject": True})
    ambiguous = sar._confidence_from_evidence({"ambiguous_hit": True})

    assert headline[0] == 0.95 and headline[1] == "direct_ticker"
    assert subject[0] == 0.80 and subject[1] == "fmp_subject"
    assert body[0] == 0.45 and body[1] == "company_name_body"
    assert ambiguous[0] == 0.0 and ambiguous[1] == "ambiguous_alias"
    # Strict ordering — body must score below subject must score below headline.
    assert body[0] < subject[0] < headline[0]


def test_body_only_mention_flags_not_in_headline():
    """A clean alias appearing only in the body (not headline, not FMP subject)
    is a body-only mention: it maps with low confidence and the group carries
    in_title=False / is_subject=False so the #4 gate can cap it to watch-only."""
    known = {"ORCL", "NVDA"}
    # Headline is about NVDA; ORCL ('oracle') is only namechecked in the body.
    raw = [_make_raw_news(
        title="Nvidia unveils next-gen data-center GPU",
        description="Analysts note Oracle could benefit as a downstream cloud buyer.",
        symbol="NVDA",
    )]
    item = sar.normalize_items(raw, known)[0]
    maps = {m.ticker: m for m in sar.map_item_to_tickers(item, known)}
    assert maps["ORCL"].method == "company_name_body"
    assert maps["ORCL"].in_title is False and maps["ORCL"].is_subject is False
    assert maps["NVDA"].in_title is True

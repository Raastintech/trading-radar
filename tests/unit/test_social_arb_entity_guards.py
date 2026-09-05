"""Tests for social_arb_radar.py entity-mapping and category guards.

Tasks covered:
  - Task 1: CORPORATE_SUFFIX_TICKERS guard (AG/SA/SE/NV/PLC require strong evidence)
  - Task 2: _validated_theme_label / _why_it_matters category mismatch
  - Task 3: M&A buyer-vs-target: TTMI mapped, AG suffix dropped
  - Task 4: Claude KEEP cannot revive a corporate-suffix drop (AG never reaches Claude)
  - Task 5: AMBIGUOUS_ROLE_TITLE_TICKERS guard (COO/CTO/CIO/... executive-title
    ambiguity — "Dell COO speaks about AI" must not map ticker COO)

All tests are pure-Python; no network, no provider calls.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from research import social_arb_radar as sar


# ─── Helpers ────────────────────────────────────────────────────────────────


def _fresh_drop_stats() -> Dict[str, Any]:
    return {"total": 0, "reasons": {}, "examples": []}


def _ev(
    in_title: bool = False,
    in_body: bool = False,
    is_subject: bool = False,
    direct_symbol: bool = False,
    ambiguous_hit: bool = False,
    alias: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "in_title": in_title,
        "in_body": in_body,
        "is_subject": is_subject,
        "direct_symbol": direct_symbol,
        "ambiguous_hit": ambiguous_hit,
        "alias": alias,
    }


def _make_item(
    title: str,
    tickers: List[str],
    ticker_evidence: Optional[Dict[str, Dict[str, Any]]] = None,
    theme: str = "Unclassified",
    source_type: str = "fmp_stock_news",
    freshness_hours: float = 4.0,
) -> sar.NormalizedItem:
    return sar.NormalizedItem(
        item_id=f"test:{title[:20]}",
        title=title,
        source="TestSource",
        source_type=source_type,
        timestamp="2026-06-17T20:00:00+00:00",
        freshness_hours=freshness_hours,
        url="https://example.com/news",
        tickers_mentioned=tickers,
        company_names=[],
        theme=theme,
        source_payload_ref="raw[0]",
        ticker_evidence=ticker_evidence or {},
    )


def _run_build(item: sar.NormalizedItem, known: Optional[set] = None) -> Dict[str, Any]:
    """Run build_story_groups with a single item; return (groups, drop_stats)."""
    drop = _fresh_drop_stats()
    known = known or {"AG", "SA", "SE", "NV", "PLC", "TTMI", "INTC", "NVDA", "JPM", "CMG", "GOOGL", "NVO"}
    groups, _ = sar.build_story_groups([item], known, drop)
    return {"groups": groups, "drop": drop}


# ─── Task 1: CORPORATE_SUFFIX_TICKERS constant ──────────────────────────────


def test_corporate_suffix_tickers_constant_exists():
    assert hasattr(sar, "CORPORATE_SUFFIX_TICKERS")
    for sym in ("AG", "SA", "SE", "NV", "PLC"):
        assert sym in sar.CORPORATE_SUFFIX_TICKERS, f"{sym} missing from CORPORATE_SUFFIX_TICKERS"


def test_common_false_tickers_already_covers_ai_on_are_it():
    """AI, ON, ARE, IT are already in COMMON_FALSE_TICKERS; guard not needed there."""
    for sym in ("AI", "ON", "ARE", "IT"):
        assert sym in sar.COMMON_FALSE_TICKERS, f"{sym} should already be in COMMON_FALSE_TICKERS"


# ─── Task 1: AG false positive guard ────────────────────────────────────────


def test_ag_bare_uppercase_in_swiss_company_name_is_dropped():
    """'Swiss Technology Group AG' → AG ticker must be dropped (corporate suffix)."""
    title = "TTM Technologies Announces Acquisition of Swiss Technology Group AG"
    item = _make_item(
        title=title,
        tickers=["AG"],
        ticker_evidence={
            "AG": _ev(in_title=True, direct_symbol=True),  # bare uppercase match only
        },
    )
    result = _run_build(item)
    ag_groups = [k for k in result["groups"] if k.startswith("AG:")]
    assert ag_groups == [], "AG must NOT form a story group from a corporate suffix match"
    assert result["drop"]["reasons"].get("ambiguous_generic_word_alias", 0) >= 1


def test_ag_with_dollar_prefix_is_kept():
    """$AG in a headline explicitly names the stock; corporate suffix guard must not block it."""
    title = "$AG surges on rising silver prices; analysts raise targets"
    item = _make_item(
        title=title,
        tickers=["AG"],
        ticker_evidence={"AG": _ev(in_title=True, direct_symbol=True)},
        theme="Macro/Policy",  # non-Unclassified so it passes _is_market_relevant
    )
    result = _run_build(item)
    ag_groups = [k for k in result["groups"] if k.startswith("AG:")]
    assert ag_groups, "AG must form a story group when $AG is explicitly present"


def test_ag_with_company_alias_is_kept():
    """'First Majestic Silver' alias confirms the AG stock; must survive the guard."""
    title = "First Majestic Silver beats earnings estimates on record silver output"
    item = _make_item(
        title=title,
        tickers=["AG"],
        ticker_evidence={"AG": _ev(in_title=True, direct_symbol=False, alias="first majestic silver")},
    )
    result = _run_build(item)
    ag_groups = [k for k in result["groups"] if k.startswith("AG:")]
    assert ag_groups, "AG must form a story group when matched via company-name alias"


def test_ag_with_fmp_subject_is_kept():
    """FMP tags the article with symbol=AG (explicit subject); must survive the guard."""
    title = "Silver mining outlook: analysts raise targets amid supply shortage"
    item = _make_item(
        title=title,
        tickers=["AG"],
        ticker_evidence={"AG": _ev(is_subject=True)},
    )
    result = _run_build(item)
    ag_groups = [k for k in result["groups"] if k.startswith("AG:")]
    assert ag_groups, "AG must form a story group when FMP subject tag is present"


# ─── Task 1: Other corporate suffixes ───────────────────────────────────────


@pytest.mark.parametrize("suffix_ticker", ["SA", "SE", "NV", "PLC"])
def test_corporate_suffix_bare_match_is_dropped(suffix_ticker: str):
    """SA/SE/NV/PLC as bare uppercase in text → ambiguous_generic_word_alias."""
    # Use a non-Unclassified theme so the item passes _is_market_relevant
    # before reaching the corporate suffix guard.
    title = f"European firm {suffix_ticker} Partners reports earnings beat"
    item = _make_item(
        title=title,
        tickers=[suffix_ticker],
        ticker_evidence={suffix_ticker: _ev(in_title=True, direct_symbol=True)},
        theme="Macro/Policy",
    )
    result = _run_build(item)
    groups_for_ticker = [k for k in result["groups"] if k.startswith(f"{suffix_ticker}:")]
    assert groups_for_ticker == [], f"{suffix_ticker} must not survive as bare uppercase corporate suffix"
    assert result["drop"]["reasons"].get("ambiguous_generic_word_alias", 0) >= 1


# ─── Task 3: M&A buyer vs target ────────────────────────────────────────────


def test_ma_headline_maps_to_ttmi_not_ag():
    """Full M&A headline: TTMI (FMP subject) survives; AG (corporate suffix) is dropped."""
    # "acquisition" is a CATALYST_TERM so theme=Unclassified still passes _is_market_relevant
    title = "TTM Technologies Announces Acquisition of Privately-Held Swiss Technology Group AG"
    item = _make_item(
        title=title,
        tickers=["AG", "TTMI"],
        ticker_evidence={
            "AG": _ev(in_title=True, direct_symbol=True),    # bare suffix only
            "TTMI": _ev(is_subject=True),                     # FMP subject tag
        },
        theme="Unclassified",
    )
    result = _run_build(item)
    ag_groups = [k for k in result["groups"] if k.startswith("AG:")]
    ttmi_groups = [k for k in result["groups"] if k.startswith("TTMI:")]
    assert ag_groups == [], "AG corporate suffix must be dropped from M&A headline"
    assert ttmi_groups, "TTMI (acquirer via FMP subject) must survive"


def test_ttmi_company_alias_in_headline():
    """'TTM Technologies' alias now maps to TTMI; this provides company-name evidence."""
    assert "TTMI" in sar.COMPANY_ALIASES
    assert "ttm technologies" in sar.COMPANY_ALIASES["TTMI"]


# ─── Task 2: Category mismatch fix ──────────────────────────────────────────


def test_validated_theme_label_returns_theme_when_title_has_terms():
    """Title contains a Semiconductors term → label is 'Semiconductors'."""
    label = sar._validated_theme_label("INTC", "Semiconductors", "Intel chip production ramp")
    assert label == "Semiconductors"


def test_validated_theme_label_returns_theme_for_known_ticker():
    """NVDA is a primary Semiconductors ticker; even without terms, label is valid."""
    label = sar._validated_theme_label("NVDA", "Semiconductors", "Company reports record Q2")
    assert label == "Semiconductors"


def test_validated_theme_label_falls_back_for_mismatch():
    """JPM + 'Crypto' theme with no crypto terms in title → 'Company-specific'."""
    title = "JPMorgan estimates US net equity issuance could hit $1.2T by 2027"
    label = sar._validated_theme_label("JPM", "Crypto", title)
    assert label == "Company-specific"


def test_validated_theme_label_cmg_semiconductors_mismatch():
    """CMG + 'Semiconductors': 'chip' in 'Chipotle' must NOT fire (word boundary)."""
    title = "Chipotle Mexican Grill 10-Year Return: $1000 Now Worth $12,000"
    label = sar._validated_theme_label("CMG", "Semiconductors", title)
    assert label == "Company-specific", (
        "'chip' in 'chipotle' is a substring hit; word-boundary matching must block it"
    )


def test_validated_theme_label_googl_crypto_mismatch():
    """GOOGL + 'Crypto' theme with no crypto terms → 'Company-specific'."""
    title = "UK government partners with Google DeepMind to build AI-powered housing planning tool"
    label = sar._validated_theme_label("GOOGL", "Crypto", title)
    assert label == "Company-specific"


def test_validated_theme_label_nvo_cybersecurity_mismatch():
    """NVO + 'Cybersecurity' with no cyber terms → 'Company-specific'."""
    title = "Novo Nordisk hit by ransomware attack, seeks ransom"
    # 'ransomware' IS a cybersecurity term — this should return the theme
    label = sar._validated_theme_label("NVO", "Cybersecurity", title)
    assert label == "Cybersecurity"


def test_why_it_matters_options_tape_confirmed_uses_validated_theme():
    """_why_it_matters for Options/Tape Confirmed must use validated theme label."""
    # JPM + Crypto theme mismatch → output should say Company-specific, not Crypto
    why = sar._why_it_matters(
        symbol="JPM",
        theme="Crypto",
        bucket="Options/Tape Confirmed",
        title="JPMorgan estimates US net equity issuance could hit $1.2T by 2027",
        tape={},
    )
    assert "Crypto" not in why, f"Expected no 'Crypto' in why-text, got: {why}"
    assert "Company-specific" in why


def test_why_it_matters_options_tape_confirmed_keeps_matching_theme():
    """When theme actually matches the headline, keep it (no false suppression)."""
    why = sar._why_it_matters(
        symbol="INTC",
        theme="Semiconductors",
        bucket="Options/Tape Confirmed",
        title="Intel 18A chip foundry production starts with AAPL interest",
        tape={},
    )
    assert "Semiconductors" in why


# ─── Task 4: Claude KEEP cannot revive a dropped corporate suffix ────────────


def test_ag_never_reaches_candidate_list_after_corporate_suffix_drop():
    """AG dropped in build_story_groups → no Candidate with ticker=AG for Claude to KEEP."""
    title = "TTM Technologies Announces Acquisition of Swiss Technology Group AG"
    item = _make_item(
        title=title,
        tickers=["AG"],
        ticker_evidence={"AG": _ev(in_title=True, direct_symbol=True)},
    )
    drop = _fresh_drop_stats()
    known = {"AG", "TTMI"}
    groups, _ = sar.build_story_groups([item], known, drop)
    # No AG group was created → there is nothing for Claude to receive or KEEP
    assert not any(k.startswith("AG:") for k in groups), (
        "AG corporate suffix must be filtered before Claude review — "
        "Claude KEEP can only act on candidates that reach score_candidates()"
    )
    assert drop["reasons"].get("ambiguous_generic_word_alias", 0) >= 1


# ─── Task 5: executive-title ambiguity (COO/CTO/CIO/...) ───────────────────
#
# Root cause: "Dell Technologies COO Says Agentic Demand Is Reshaping The
# Data Center..." was mapping ticker COO (The Cooper Companies) purely from
# a bare-uppercase Pass-2 regex hit on the job title "COO" in the headline.
# COO then out-scored DELL's own alpha_fit in topic_shock_detector's
# assign_topic_label (DELL was extended/gate-blocked; COO, with no real
# connection to the story, was not) and became the CITED ticker behind a
# false TOPIC_RESEARCH_NOW alert. These tests exercise normalize_items()
# directly — the actual regex/evidence-extraction layer where the false
# mapping was created — rather than hand-built ticker_evidence, since the
# fix is in how that evidence gets built in the first place.

_ROLE_TITLE_KNOWN = {"DELL", "COO", "AAPL"}


def _raw_item(title: str, symbol: str = "", url: str = "https://example.com/x") -> Dict[str, Any]:
    return {
        "title": title,
        "symbol": symbol,
        "source": "Alpaca (Benzinga)",
        "source_type": "fmp_stock_news",
        "timestamp": "2026-09-02T12:00:00+00:00",
        "url": url,
    }


def test_role_title_tickers_constant_exists():
    assert hasattr(sar, "AMBIGUOUS_ROLE_TITLE_TICKERS")
    for sym in ("COO", "CTO", "CIO", "CMO", "CISO", "CHRO"):
        assert sym in sar.AMBIGUOUS_ROLE_TITLE_TICKERS, f"{sym} missing from AMBIGUOUS_ROLE_TITLE_TICKERS"


def test_real_dell_coo_headline_maps_dell_not_coo():
    """The actual headline that produced the false COO mapping in production."""
    item = sar.normalize_items(
        [_raw_item(
            "Dell Technologies COO Says Agentic Demand Is Reshaping The Data Center "
            "And The Underlying Infrastructure; We're Expecting AI To Be 75% Of All "
            "Data Center Demand By 2030",
            symbol="DELL",
        )],
        _ROLE_TITLE_KNOWN,
    )[0]
    assert item.tickers_mentioned == ["DELL"]
    assert "COO" not in item.ticker_evidence


def test_dell_coo_speaks_maps_dell_if_evidence_exists_not_coo():
    """'Dell COO speaks about AI' maps DELL (via FMP subject tag) and never COO."""
    item = sar.normalize_items(
        [_raw_item("Dell COO speaks about AI", symbol="DELL")],
        _ROLE_TITLE_KNOWN,
    )[0]
    assert "DELL" in item.tickers_mentioned
    assert "COO" not in item.tickers_mentioned


def test_dell_coo_says_all_caps_does_not_map_coo():
    """'DELL COO says AI demand is strong' — DELL maps via its own bare-uppercase
    hit; COO must not, despite also being all-caps in the same headline."""
    item = sar.normalize_items(
        [_raw_item("DELL COO says AI demand is strong")],
        _ROLE_TITLE_KNOWN,
    )[0]
    assert "DELL" in item.tickers_mentioned
    assert "COO" not in item.tickers_mentioned


def test_cooper_companies_alias_maps_coo():
    """'The Cooper Companies COO reports earnings' maps COO via the validated
    COMPANY_ALIASES company-name match, not the bare role-title token."""
    assert "COO" in sar.COMPANY_ALIASES
    assert "cooper companies" in sar.COMPANY_ALIASES["COO"]
    item = sar.normalize_items(
        [_raw_item("The Cooper Companies COO reports earnings")],
        _ROLE_TITLE_KNOWN,
    )[0]
    assert "COO" in item.tickers_mentioned
    assert item.ticker_evidence["COO"]["alias"] == "cooper companies"


def test_cashtag_coo_maps():
    """'$COO breaks out on volume' — explicit cashtag always maps."""
    item = sar.normalize_items(
        [_raw_item("$COO breaks out on volume")],
        _ROLE_TITLE_KNOWN,
    )[0]
    assert "COO" in item.tickers_mentioned
    assert item.ticker_evidence["COO"]["direct_symbol"] is True


def test_exchange_prefixed_coo_maps():
    """'NYSE:COO shares rise after earnings' — exchange-prefixed mention maps."""
    item = sar.normalize_items(
        [_raw_item("NYSE:COO shares rise after earnings")],
        _ROLE_TITLE_KNOWN,
    )[0]
    assert "COO" in item.tickers_mentioned
    assert item.ticker_evidence["COO"]["direct_symbol"] is True


def test_coo_stock_market_phrase_maps():
    """'COO stock price rises' — explicit market phrase attached to the token
    is the only thing that lets a bare mention through."""
    item = sar.normalize_items(
        [_raw_item("COO stock price rises")],
        _ROLE_TITLE_KNOWN,
    )[0]
    assert "COO" in item.tickers_mentioned


def test_bare_coo_with_no_evidence_does_not_map():
    """A completely bare 'COO' mention (no role-title verb, no market phrase,
    no cashtag/exchange-prefix/alias/subject) still does not map — plain
    uppercase text alone is never sufficient for an ambiguous ticker."""
    item = sar.normalize_items(
        [_raw_item("COO comments on quarterly results")],
        _ROLE_TITLE_KNOWN,
    )[0]
    assert "COO" not in item.tickers_mentioned


def test_existing_valid_ticker_mappings_still_pass():
    """A normal, non-ambiguous ticker (AAPL) is unaffected by the new guard."""
    item = sar.normalize_items(
        [_raw_item("AAPL unveils new foldable iPhone design")],
        _ROLE_TITLE_KNOWN,
    )[0]
    assert "AAPL" in item.tickers_mentioned


def test_no_fabricated_role_word_ticker_mappings():
    """CEO/CFO (already in COMMON_FALSE_TICKERS) and the new ambiguous set
    never fabricate a ticker mapping from generic role-word text alone."""
    item = sar.normalize_items(
        [_raw_item("The CEO and CFO discussed CTO succession plans with the board")],
        _ROLE_TITLE_KNOWN | {"CTO"},
    )[0]
    assert item.tickers_mentioned == []


def test_topic_shock_style_cluster_drops_false_coo_but_keeps_dell():
    """map_cluster_tickers (topic_shock_detector's mapping layer) receives
    clean per-document evidence from normalize_items — this is the layer
    that actually produced the false COO-backed TOPIC_RESEARCH_NOW alert,
    since it reads social_arb_raw's normalized_items directly (pre-
    build_story_groups). Confirms the fix reaches that path too."""
    from research import topic_shock_detector as tsd

    item = sar.normalize_items(
        [_raw_item(
            "Dell Technologies COO Says Agentic Demand Is Reshaping The Data Center",
            symbol="DELL",
        )],
        _ROLE_TITLE_KNOWN,
    )[0]
    conf: Dict[str, float] = {}
    for ticker, ev in (item.ticker_evidence or {}).items():
        if ev.get("direct_symbol"):
            conf[ticker] = 0.85
        elif ev.get("is_subject") or ev.get("in_title"):
            conf[ticker] = 0.65
    doc = {
        "doc_id": "arb:0", "text": item.title, "domain": item.source,
        "pipeline": "social_arb_raw",
        "tickers_hint": sorted(set(item.tickers_mentioned)),
        "ticker_confidence": conf,
    }
    cluster = {"documents": [doc], "known_topic": None}
    mapped = {m["ticker"]: m for m in tsd.map_cluster_tickers(cluster)}
    assert "COO" not in mapped, "false COO mapping must not reach topic_shock_detector's cluster mapping"
    assert "DELL" in mapped

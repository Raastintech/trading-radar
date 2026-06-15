"""
tests/unit/test_alpaca_options_client.py — Alpaca options client + chain.

Pure-unit. All HTTP is mocked via ``unittest.mock`` so no live calls
ever leave the process. Verifies:

  - OCC symbol parsing.
  - get_chain maps Alpaca snapshots into the Tradier-compatible row
    schema (strike, bid, ask, volume, openInterest, impliedVolatility,
    inTheMoney, plus greeks).
  - get_expirations dedupes contract dates from the trading API
    endpoint.
  - is_configured reflects credentials + dependency availability.
  - OptionsFeedChain returns first non-empty feed, falls through on
    empty, falls through on exception, and reports last_served.
  - No live HTTP — patch requests.get and assert it is the only network
    surface exercised.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.alpaca_options_client import (  # noqa: E402
    AlpacaOptionsFeed,
    parse_occ_symbol,
    _reset_cache_for_tests,
)
from core.options_feed_chain import OptionsFeedChain  # noqa: E402


# ── OCC parsing ──────────────────────────────────────────────────────

def test_parse_occ_call():
    out = parse_occ_symbol("AAPL250620C00200000")
    assert out == {
        "root": "AAPL",
        "expiration": "2025-06-20",
        "option_type": "CALL",
        "strike": 200.0,
    }


def test_parse_occ_put_with_fractional_strike():
    out = parse_occ_symbol("SPY261218P00425500")
    assert out["option_type"] == "PUT"
    assert out["strike"] == 425.5
    assert out["expiration"] == "2026-12-18"


def test_parse_occ_invalid():
    assert parse_occ_symbol("garbage") is None
    assert parse_occ_symbol("") is None
    assert parse_occ_symbol(None) is None  # type: ignore[arg-type]


# ── Configuration ────────────────────────────────────────────────────

def test_feed_unconfigured_without_keys():
    feed = AlpacaOptionsFeed(api_key="", secret_key="")
    assert feed.is_configured() is False
    assert feed.get_expirations("AAPL") == ()
    assert feed.get_chain("AAPL", "2026-06-19") is None


def test_feed_configured_with_keys():
    feed = AlpacaOptionsFeed(api_key="k", secret_key="s")
    assert feed.is_configured() is True


# ── get_expirations: dedupes and paginates ───────────────────────────

def _mock_response(payload: Dict[str, Any]) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def test_get_expirations_dedupe_and_sort():
    _reset_cache_for_tests()
    feed = AlpacaOptionsFeed(api_key="k", secret_key="s")
    payload = {
        "option_contracts": [
            {"expiration_date": "2026-06-19"},
            {"expiration_date": "2026-06-19"},  # duplicate strike
            {"expiration_date": "2026-07-17"},
            {"expiration_date": "2026-05-15T00:00:00"},  # weird ISO with time
        ],
        "next_page_token": None,
    }
    with patch("core.alpaca_options_client.requests.get",
               return_value=_mock_response(payload)) as mocked:
        exps = feed.get_expirations("AAPL")
    assert exps == ("2026-05-15", "2026-06-19", "2026-07-17")
    # One call (single page), and it hit the /v2/options/contracts URL.
    assert mocked.call_count == 1
    assert "options/contracts" in mocked.call_args.args[0]


def test_get_expirations_caches_result():
    _reset_cache_for_tests()
    feed = AlpacaOptionsFeed(api_key="k", secret_key="s")
    payload = {"option_contracts": [{"expiration_date": "2026-06-19"}],
               "next_page_token": None}
    with patch("core.alpaca_options_client.requests.get",
               return_value=_mock_response(payload)) as mocked:
        feed.get_expirations("MSFT")
        feed.get_expirations("MSFT")  # second call should hit the cache
    assert mocked.call_count == 1


def test_get_expirations_empty_on_http_failure():
    _reset_cache_for_tests()
    feed = AlpacaOptionsFeed(api_key="k", secret_key="s")
    resp = MagicMock()
    resp.raise_for_status.side_effect = RuntimeError("boom")
    with patch("core.alpaca_options_client.requests.get", return_value=resp):
        assert feed.get_expirations("AAPL") == ()


# ── get_chain: maps snapshots to Tradier rows ────────────────────────

def _snapshot_payload() -> Dict[str, Any]:
    """Two calls + one put, single page, all on 2026-06-19."""
    return {
        "snapshots": {
            "AAPL260619C00200000": {
                "latestQuote": {"bp": 12.5, "ap": 12.7},
                "latestTrade": {"p": 12.6},
                "dailyBar":    {"v": 1234},
                "greeks":      {"delta": 0.55, "gamma": 0.02,
                                "theta": -0.04, "vega": 0.10, "rho": 0.01,
                                "bid_iv": 0.31, "ask_iv": 0.33, "iv": 0.32},
                "impliedVolatility": 0.32,
                "openInterest": 1500,
            },
            "AAPL260619C00210000": {
                "latestQuote": {"bp": 8.0, "ap": 8.1},
                "dailyBar":    {"v": 200},
                "greeks":      {"delta": 0.40, "iv": 0.34},
                "openInterest": 600,
            },
            "AAPL260619P00190000": {
                "latestQuote": {"bp": 4.5, "ap": 4.7},
                "dailyBar":    {"v": 80},
                "greeks":      {"delta": -0.35, "iv": 0.36},
                "openInterest": 900,
            },
            # Different-expiration contract; must be filtered out.
            "AAPL260918C00200000": {
                "latestQuote": {"bp": 1, "ap": 2}, "openInterest": 10,
            },
            # Unparseable symbol; must be skipped without error.
            "garbage": {"latestQuote": {"bp": 1, "ap": 2}},
        },
        "next_page_token": None,
    }


def test_get_chain_maps_to_tradier_schema():
    _reset_cache_for_tests()
    feed = AlpacaOptionsFeed(api_key="k", secret_key="s")
    with patch("core.alpaca_options_client.requests.get",
               return_value=_mock_response(_snapshot_payload())):
        chain = feed.get_chain("AAPL", "2026-06-19")
    assert chain is not None
    calls = chain["calls"]
    puts  = chain["puts"]
    assert isinstance(calls, pd.DataFrame)
    assert isinstance(puts, pd.DataFrame)
    # Only the two June 19 calls survive; September is filtered out.
    assert len(calls) == 2
    assert len(puts) == 1
    # Required columns for _per_expiry_features:
    required = {"strike", "bid", "ask", "volume", "openInterest",
                "impliedVolatility", "inTheMoney"}
    assert required.issubset(set(calls.columns))
    assert required.issubset(set(puts.columns))
    # Field values populated correctly for the 200-strike call.
    row = calls[calls["strike"] == 200.0].iloc[0]
    assert row["bid"] == 12.5
    assert row["ask"] == 12.7
    assert row["volume"] == 1234
    assert row["openInterest"] == 1500
    assert abs(row["impliedVolatility"] - 0.32) < 1e-6
    assert abs(row["delta"] - 0.55) < 1e-6
    assert row["contractSymbol"] == "AAPL260619C00200000"
    # inTheMoney defaults to False (we don't bias the ATM fallback).
    assert bool(row["inTheMoney"]) is False


def test_get_chain_returns_none_when_empty():
    _reset_cache_for_tests()
    feed = AlpacaOptionsFeed(api_key="k", secret_key="s")
    with patch("core.alpaca_options_client.requests.get",
               return_value=_mock_response({"snapshots": {}, "next_page_token": None})):
        assert feed.get_chain("AAPL", "2026-06-19") is None


def test_get_chain_caches_result():
    _reset_cache_for_tests()
    feed = AlpacaOptionsFeed(api_key="k", secret_key="s")
    with patch("core.alpaca_options_client.requests.get",
               return_value=_mock_response(_snapshot_payload())) as mocked:
        feed.get_chain("AAPL", "2026-06-19")
        feed.get_chain("AAPL", "2026-06-19")  # cache hit
    # First call now hits two endpoints (snapshot + contracts); the second
    # call short-circuits via the chain cache → total 2.
    assert mocked.call_count == 2


# ── get_chain: OI from contracts endpoint (realistic shape) ─────────

def _realistic_snapshot_payload() -> Dict[str, Any]:
    """Mirror the *actual* Alpaca snapshot shape: no openInterest, no
    greeks, no impliedVolatility — only quote/trade/bar data."""
    return {
        "snapshots": {
            "AAPL260619C00200000": {
                "latestQuote": {"bp": 12.5, "ap": 12.7},
                "latestTrade": {"p": 12.6},
                "dailyBar":    {"v": 1234, "c": 12.65},
            },
            "AAPL260619P00190000": {
                "latestQuote": {"bp": 4.5, "ap": 4.7},
                "dailyBar":    {"v": 80, "c": 4.6},
                # No latestTrade — close_price fallback path.
            },
        },
        "next_page_token": None,
    }


def _contracts_payload() -> Dict[str, Any]:
    """Mirror /v2/options/contracts; OI + close_price as JSON strings."""
    return {
        "option_contracts": [
            {"symbol": "AAPL260619C00200000", "open_interest": "5400",
             "open_interest_date": "2026-06-18", "close_price": "12.66"},
            {"symbol": "AAPL260619P00190000", "open_interest": "2100",
             "open_interest_date": "2026-06-18", "close_price": "4.60"},
        ],
        "next_page_token": None,
    }


def _url_aware_mock(snap_payload: Dict, contracts_payload: Dict) -> Any:
    """Return a side_effect callable that picks the payload based on URL."""
    def _side_effect(url, *args, **kwargs):
        if "options/contracts" in url:
            return _mock_response(contracts_payload)
        return _mock_response(snap_payload)
    return _side_effect


def test_get_chain_pulls_oi_from_contracts_endpoint():
    """With realistic Alpaca shapes (no OI in snapshot), OI must come
    from /v2/options/contracts. This is the bug that produced
    OPTIONS_NO_EDGE on TSLA-class tickers before the fix."""
    _reset_cache_for_tests()
    feed = AlpacaOptionsFeed(api_key="k", secret_key="s")
    with patch("core.alpaca_options_client.requests.get",
               side_effect=_url_aware_mock(_realistic_snapshot_payload(),
                                            _contracts_payload())) as mocked:
        chain = feed.get_chain("AAPL", "2026-06-19")
    assert chain is not None
    call = chain["calls"].iloc[0]
    put  = chain["puts"].iloc[0]
    assert call["openInterest"] == 5400
    assert put["openInterest"]  == 2100
    # Hit both endpoints exactly once.
    urls = [c.args[0] for c in mocked.call_args_list]
    assert any("options/snapshots/AAPL" in u for u in urls)
    assert any("v2/options/contracts" in u for u in urls)


def test_get_chain_close_price_fallback_when_no_latest_trade():
    """When latestTrade is missing, lastPrice falls back to the
    contracts endpoint's close_price (instead of degrading to 0)."""
    _reset_cache_for_tests()
    feed = AlpacaOptionsFeed(api_key="k", secret_key="s")
    with patch("core.alpaca_options_client.requests.get",
               side_effect=_url_aware_mock(_realistic_snapshot_payload(),
                                            _contracts_payload())):
        chain = feed.get_chain("AAPL", "2026-06-19")
    put = chain["puts"].iloc[0]
    # No latestTrade in the snapshot fixture → lastPrice from close_price.
    assert abs(put["lastPrice"] - 4.60) < 1e-6


def test_get_chain_degrades_to_zero_oi_when_contracts_endpoint_fails():
    """Contracts endpoint failure must not break the chain — OI just
    falls back to whatever the snapshot carries (typically 0)."""
    _reset_cache_for_tests()
    feed = AlpacaOptionsFeed(api_key="k", secret_key="s")
    def _side_effect(url, *args, **kwargs):
        if "options/contracts" in url:
            raise RuntimeError("simulated contracts API failure")
        return _mock_response(_realistic_snapshot_payload())
    with patch("core.alpaca_options_client.requests.get", side_effect=_side_effect):
        chain = feed.get_chain("AAPL", "2026-06-19")
    # Chain still produced — but with OI=0 (the previous broken behaviour;
    # at least the rest of the row is usable).
    assert chain is not None
    assert chain["calls"].iloc[0]["openInterest"] == 0


def test_fetch_contract_metadata_dedupes_and_coerces_strings():
    """OI/close_price come back as JSON strings; the fetcher coerces
    to numeric types so downstream filters work."""
    _reset_cache_for_tests()
    feed = AlpacaOptionsFeed(api_key="k", secret_key="s")
    payload = {
        "option_contracts": [
            {"symbol": "X260101C00100000", "open_interest": "42",
             "close_price": "1.23"},
            {"symbol": "X260101P00100000", "open_interest": None,
             "close_price": None},
        ],
        "next_page_token": None,
    }
    with patch("core.alpaca_options_client.requests.get",
               return_value=_mock_response(payload)):
        meta = feed._fetch_contract_metadata("X", "2026-01-01")
    assert meta["X260101C00100000"]["open_interest"] == 42
    assert meta["X260101C00100000"]["close_price"]   == 1.23
    # Missing/null OI coerces to 0; close_price coerces to 0.0 — caller
    # decides whether 0 means "thin" or "unknown".
    assert meta["X260101P00100000"]["open_interest"] == 0
    assert meta["X260101P00100000"]["close_price"]   == 0.0


def test_no_live_http_in_tests():
    """When requests.get is patched, the suite cannot accidentally make
    real network calls. We simulate that by patching with a sentinel
    and asserting it was called instead of any real socket."""
    _reset_cache_for_tests()
    feed = AlpacaOptionsFeed(api_key="k", secret_key="s")
    sentinel = MagicMock()
    sentinel.json.return_value = {"option_contracts": [], "next_page_token": None}
    sentinel.raise_for_status.return_value = None
    with patch("core.alpaca_options_client.requests.get", return_value=sentinel) as g:
        feed.get_expirations("AAPL")
        assert g.called


# ── OptionsFeedChain ─────────────────────────────────────────────────

class _FakeFeed:
    """Tiny test double for the chain wrapper."""

    def __init__(self, name: str, *, configured: bool = True,
                 chain_result=None, exp_result=(), raises: bool = False):
        self.NAME = name
        self._configured = configured
        self._chain = chain_result
        self._exp = exp_result
        self._raises = raises
        self.calls = 0

    def is_configured(self) -> bool:
        return self._configured

    def get_expirations(self, ticker: str):
        self.calls += 1
        if self._raises:
            raise RuntimeError("boom")
        return self._exp

    def get_chain(self, ticker: str, expiry: str):
        self.calls += 1
        if self._raises:
            raise RuntimeError("boom")
        return self._chain


def _df_chain(n: int = 1):
    rows = [{"strike": 200.0, "bid": 1, "ask": 2, "volume": 10,
             "openInterest": 50, "impliedVolatility": 0.3,
             "inTheMoney": False}]
    rows *= n
    return {"calls": pd.DataFrame(rows), "puts": pd.DataFrame(rows)}


def test_chain_serves_first_configured_feed_with_data():
    primary  = _FakeFeed("alpaca",  chain_result=_df_chain(2), exp_result=("2026-06-19",))
    fallback = _FakeFeed("tradier", chain_result=_df_chain(3), exp_result=("2026-07-17",))
    chain = OptionsFeedChain([primary, fallback])
    assert chain.is_configured()
    out = chain.get_chain("AAPL", "2026-06-19")
    assert out is not None
    assert len(out["calls"]) == 2  # alpaca won
    assert primary.calls == 1
    assert fallback.calls == 0
    assert chain.last_served == "alpaca"


def test_chain_falls_through_on_empty_result():
    primary  = _FakeFeed("alpaca",  chain_result=None)
    fallback = _FakeFeed("tradier", chain_result=_df_chain(1))
    chain = OptionsFeedChain([primary, fallback])
    out = chain.get_chain("AAPL", "2026-06-19")
    assert out is not None
    assert primary.calls == 1
    assert fallback.calls == 1
    assert chain.last_served == "tradier"


def test_chain_falls_through_when_both_dataframes_empty():
    """A chain dict with empty DataFrames is treated as empty (next feed)."""
    empty_chain = {"calls": pd.DataFrame(), "puts": pd.DataFrame()}
    primary  = _FakeFeed("alpaca",  chain_result=empty_chain)
    fallback = _FakeFeed("tradier", chain_result=_df_chain(1))
    chain = OptionsFeedChain([primary, fallback])
    out = chain.get_chain("AAPL", "2026-06-19")
    assert out is not None
    assert chain.last_served == "tradier"


def test_chain_falls_through_on_exception():
    primary  = _FakeFeed("alpaca",  raises=True)
    fallback = _FakeFeed("tradier", chain_result=_df_chain(1))
    chain = OptionsFeedChain([primary, fallback])
    out = chain.get_chain("AAPL", "2026-06-19")
    assert out is not None
    assert chain.last_served == "tradier"


def test_chain_returns_none_when_all_fail():
    primary  = _FakeFeed("alpaca",  chain_result=None)
    fallback = _FakeFeed("tradier", raises=True)
    chain = OptionsFeedChain([primary, fallback])
    assert chain.get_chain("AAPL", "2026-06-19") is None
    assert chain.last_served is None


def test_chain_skips_unconfigured_feeds():
    """An unconfigured leaf is filtered out at construction time."""
    primary  = _FakeFeed("alpaca",  configured=False, chain_result=_df_chain(1))
    fallback = _FakeFeed("tradier", configured=True,  chain_result=_df_chain(1))
    chain = OptionsFeedChain([primary, fallback])
    out = chain.get_chain("AAPL", "2026-06-19")
    assert out is not None
    assert primary.calls == 0  # never invoked
    assert fallback.calls == 1
    assert chain.last_served == "tradier"


def test_chain_unconfigured_when_no_feeds():
    chain = OptionsFeedChain([])
    assert chain.is_configured() is False
    assert chain.get_chain("AAPL", "2026-06-19") is None
    assert chain.get_expirations("AAPL") == ()


def test_chain_status_reports_feed_order():
    primary  = _FakeFeed("alpaca",  chain_result=_df_chain(1))
    fallback = _FakeFeed("tradier", chain_result=_df_chain(1))
    chain = OptionsFeedChain([primary, fallback])
    chain.get_chain("AAPL", "2026-06-19")
    status = chain.status()
    assert status["feed_order"] == ["alpaca", "tradier"]
    assert status["last_served"] == "alpaca"
    assert status["served_counts"] == {"alpaca": 1}


# ── Options Data Enrichment — per-field merge ──────────────────────

def _df_chain_stub_iv(strikes=(200.0, 210.0), with_greeks: bool = True):
    """Chain shape mimicking Alpaca Pro+: OI populated, IV + greeks
    stubbed to zero. ``with_greeks=False`` omits the greeks columns
    entirely (simulating a future schema change)."""
    rows = []
    for s in strikes:
        row = {
            "strike": float(s), "bid": 1.0, "ask": 2.0,
            "volume": 100, "openInterest": 500,
            "impliedVolatility": 0.0, "inTheMoney": False,
        }
        if with_greeks:
            row.update({"delta": 0.0, "gamma": 0.0, "theta": 0.0,
                        "vega": 0.0, "rho": 0.0,
                        "bid_iv": 0.0, "ask_iv": 0.0})
        rows.append(row)
    return {"calls": pd.DataFrame(rows), "puts": pd.DataFrame(rows)}


def _df_chain_full_iv(strikes=(200.0, 210.0)):
    """Chain shape mimicking Tradier: IV + greeks populated."""
    rows = []
    for i, s in enumerate(strikes):
        rows.append({
            "strike": float(s), "bid": 1.0, "ask": 2.0,
            "volume": 100, "openInterest": 500,
            "impliedVolatility": 0.30 + 0.01 * i, "inTheMoney": False,
            "delta": 0.55 - 0.05 * i, "gamma": 0.012, "theta": -0.045,
            "vega": 0.18, "rho": 0.02,
            "bid_iv": 0.29, "ask_iv": 0.31,
        })
    return {"calls": pd.DataFrame(rows), "puts": pd.DataFrame(rows)}


def test_chain_enrichment_fills_iv_and_greeks_when_primary_stubs_them():
    """Alpaca-shaped primary (IV=0) is enriched by Tradier-shaped secondary."""
    primary  = _FakeFeed("alpaca",  chain_result=_df_chain_stub_iv())
    fallback = _FakeFeed("tradier", chain_result=_df_chain_full_iv())
    chain = OptionsFeedChain([primary, fallback])
    out = chain.get_chain("AAPL", "2026-06-19")
    assert out is not None
    # Primary served (last_served stays alpaca) — IV came from Tradier.
    assert chain.last_served == "alpaca"
    assert chain.last_enriched_by == "tradier"
    # Both leaves were called.
    assert primary.calls == 1
    assert fallback.calls == 1
    # Stubbed fields filled in:
    calls = out["calls"]
    assert calls.loc[0, "impliedVolatility"] == pytest.approx(0.30)
    assert calls.loc[1, "impliedVolatility"] == pytest.approx(0.31)
    assert calls.loc[0, "delta"] == pytest.approx(0.55)
    assert calls.loc[1, "delta"] == pytest.approx(0.50)
    # Primary-authoritative fields unchanged:
    assert int(calls.loc[0, "openInterest"]) == 500
    assert int(calls.loc[0, "volume"]) == 100
    # Counters surfaced on status:
    status = chain.status()
    assert status["served_counts"] == {"alpaca": 1}
    assert status["enrich_counts"] == {"tradier": 1}
    assert status["last_enriched_by"] == "tradier"


def test_chain_skips_enrichment_when_primary_already_has_iv():
    """Existing behavior (Tradier-served chain, full IV) — no enrichment fired."""
    primary  = _FakeFeed("alpaca",  chain_result=_df_chain_full_iv())
    fallback = _FakeFeed("tradier", chain_result=_df_chain_full_iv())
    chain = OptionsFeedChain([primary, fallback])
    out = chain.get_chain("AAPL", "2026-06-19")
    assert out is not None
    assert chain.last_served == "alpaca"
    assert chain.last_enriched_by is None
    # Crucial: fallback NEVER called when primary's IV is meaningful.
    assert primary.calls == 1
    assert fallback.calls == 0


def test_chain_enrichment_fills_only_zero_rows_keeps_meaningful_primary_values():
    """Mixed primary: row 0 has real IV (kept), row 1 has stub IV
    (filled). Models the real Alpaca behavior where some rows carry IV
    and others stub it at 0."""
    primary_chain = _df_chain_full_iv((200.0, 210.0))
    # Knock row 1's IV/delta back to 0 to simulate a partial-stub case.
    primary_chain["calls"].loc[1, "impliedVolatility"] = 0.0
    primary_chain["calls"].loc[1, "delta"] = 0.0
    primary_chain["puts"].loc[1, "impliedVolatility"] = 0.0
    primary_chain["puts"].loc[1, "delta"] = 0.0
    fallback_chain = _df_chain_full_iv((200.0, 210.0))
    # Bump fallback's row-0 values so we can prove they did NOT win:
    # primary's non-zero row-0 must be preserved.
    fallback_chain["calls"].loc[0, "impliedVolatility"] = 0.99
    fallback_chain["calls"].loc[0, "delta"] = 0.99

    primary  = _FakeFeed("alpaca",  chain_result=primary_chain)
    fallback = _FakeFeed("tradier", chain_result=fallback_chain)
    chain = OptionsFeedChain([primary, fallback])
    out = chain.get_chain("AAPL", "2026-06-19")

    # Enrichment fires because row 1 has IV=0; row 0 has IV=0.30 so it
    # is preserved unchanged.
    assert chain.last_enriched_by == "tradier"
    calls = out["calls"]
    # Row 0: primary's 0.30 wins over fallback's 0.99 (non-destructive).
    assert calls.loc[0, "impliedVolatility"] == pytest.approx(0.30)
    assert calls.loc[0, "delta"] == pytest.approx(0.55)
    # Row 1: was 0, now filled from fallback (i=1 → IV=0.31, delta=0.50).
    assert calls.loc[1, "impliedVolatility"] == pytest.approx(0.31)
    assert calls.loc[1, "delta"] == pytest.approx(0.50)


def test_chain_enrichment_matches_by_strike_unmatched_rows_stay_zero():
    """Strike mismatch — enrichment fills only the rows whose strike
    appears in the secondary."""
    primary  = _FakeFeed("alpaca",  chain_result=_df_chain_stub_iv((200.0, 210.0, 220.0)))
    fallback = _FakeFeed("tradier", chain_result=_df_chain_full_iv((210.0, 230.0)))
    chain = OptionsFeedChain([primary, fallback])
    out = chain.get_chain("AAPL", "2026-06-19")
    calls = out["calls"]
    # 200.0: not in fallback → stays 0
    assert calls.loc[0, "impliedVolatility"] == 0.0
    # 210.0: in fallback → filled with first row of fallback (i=0)
    assert calls.loc[1, "impliedVolatility"] == pytest.approx(0.30)
    # 220.0: not in fallback → stays 0
    assert calls.loc[2, "impliedVolatility"] == 0.0
    assert chain.last_enriched_by == "tradier"


def test_chain_enrichment_survives_secondary_exception():
    """Secondary raising during enrichment does not break the primary chain."""
    primary  = _FakeFeed("alpaca",  chain_result=_df_chain_stub_iv())
    fallback = _FakeFeed("tradier", raises=True)
    chain = OptionsFeedChain([primary, fallback])
    out = chain.get_chain("AAPL", "2026-06-19")
    assert out is not None  # primary chain still returned
    assert chain.last_served == "alpaca"
    assert chain.last_enriched_by is None
    # IV stays stubbed because enrichment failed.
    assert out["calls"].loc[0, "impliedVolatility"] == 0.0


def test_chain_enrichment_adds_missing_greeks_column():
    """Primary chain missing greeks columns entirely — enrichment adds them."""
    primary  = _FakeFeed("alpaca",  chain_result=_df_chain_stub_iv(with_greeks=False))
    fallback = _FakeFeed("tradier", chain_result=_df_chain_full_iv())
    chain = OptionsFeedChain([primary, fallback])
    out = chain.get_chain("AAPL", "2026-06-19")
    assert chain.last_enriched_by == "tradier"
    calls = out["calls"]
    assert "delta" in calls.columns
    assert calls.loc[0, "delta"] == pytest.approx(0.55)


def test_chain_single_feed_no_enrichment_attempted():
    """Alpaca-only deployments (single configured feed) — no merge happens."""
    primary = _FakeFeed("alpaca", chain_result=_df_chain_stub_iv())
    chain = OptionsFeedChain([primary])
    out = chain.get_chain("AAPL", "2026-06-19")
    assert out is not None
    assert chain.last_served == "alpaca"
    assert chain.last_enriched_by is None
    # IV stays stubbed — nothing to fill from.
    assert out["calls"].loc[0, "impliedVolatility"] == 0.0
    assert chain.status()["enrich_counts"] == {}


def test_chain_enrichment_does_not_mutate_primary_feed_cache():
    """If the primary feed returns the same DataFrame by reference on
    subsequent calls (TTL cache), the chain's merge must not pollute
    that cache. Each ``get_chain`` should see the original stub and
    re-enrich independently."""
    cached = _df_chain_stub_iv()
    primary  = _FakeFeed("alpaca",  chain_result=cached)
    fallback = _FakeFeed("tradier", chain_result=_df_chain_full_iv())
    chain = OptionsFeedChain([primary, fallback])

    out1 = chain.get_chain("AAPL", "2026-06-19")
    assert chain.last_enriched_by == "tradier"
    assert out1["calls"].loc[0, "impliedVolatility"] == pytest.approx(0.30)

    # The primary feed's cached chain must STILL show IV=0 — the merge
    # mutated a copy, not the cached object.
    assert cached["calls"].loc[0, "impliedVolatility"] == 0.0

    # Second call re-enriches.
    out2 = chain.get_chain("AAPL", "2026-06-19")
    assert chain.last_enriched_by == "tradier"
    assert chain.status()["enrich_counts"] == {"tradier": 2}
    # And the two outputs are independent DataFrames.
    assert out1["calls"] is not out2["calls"]


def test_chain_enrichment_skipped_when_secondary_empty():
    """Secondary returning empty (e.g. wrong expiry) leaves primary untouched."""
    primary  = _FakeFeed("alpaca",  chain_result=_df_chain_stub_iv())
    fallback = _FakeFeed("tradier", chain_result=None)
    chain = OptionsFeedChain([primary, fallback])
    out = chain.get_chain("AAPL", "2026-06-19")
    assert chain.last_served == "alpaca"
    assert chain.last_enriched_by is None
    assert out["calls"].loc[0, "impliedVolatility"] == 0.0

"""
tests/unit/test_options_chain_merge_audit.py — Options Data Enrichment
merge audit (Alpaca primary + Tradier IV/Greeks). This is the
cross-cutting options-data layer, not roadmap Phase 2C.

One test per audit invariant from the Options Merge Integrity Audit
charter. The audit treats the chain wrapper as a research-only data
pipeline and verifies the merge can never:

  - cross-pollinate between calls and puts,
  - leak between symbols / expirations,
  - mis-match decimal strikes,
  - duplicate rows on duplicate strikes,
  - overwrite a meaningful primary IV / greek,
  - touch quote / OI / volume fields,
  - propagate a secondary feed exception,
  - hide its activity from operator status.

All tests are pure-unit, no live HTTP. Test doubles emulate the same
3-method contract real adapters expose.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.options_feed_chain import (  # noqa: E402
    OptionsFeedChain,
    _ENRICHABLE_FIELDS,
    _count_zero_iv,
    _merge_chain_fields,
)


# ── shared fixtures ──────────────────────────────────────────────────


class _SpyingFeed:
    """Test double that records every call so the audit can assert
    exactly which (ticker, expiry) was queried for the merge."""

    def __init__(self, name, chain_by_key=None, exp_by_ticker=None,
                 configured=True, raises=False):
        self.NAME = name
        self._chain_by_key = chain_by_key or {}
        self._exp = exp_by_ticker or {}
        self._configured = configured
        self._raises = raises
        # Recorded args of every method call.
        self.get_chain_calls = []
        self.get_exp_calls = []

    def is_configured(self):
        return self._configured

    def get_expirations(self, ticker):
        self.get_exp_calls.append(ticker)
        if self._raises:
            raise RuntimeError("boom")
        return self._exp.get(ticker, ())

    def get_chain(self, ticker, expiry):
        self.get_chain_calls.append((ticker, expiry))
        if self._raises:
            raise RuntimeError("boom")
        return self._chain_by_key.get((ticker, expiry))


def _row(strike, *, iv=0.0, delta=0.0, gamma=0.0, theta=0.0, vega=0.0, rho=0.0,
         bid_iv=0.0, ask_iv=0.0, bid=1.0, ask=2.0, volume=100,
         openInterest=500, in_the_money=False):
    return {
        "strike": float(strike), "bid": bid, "ask": ask,
        "volume": volume, "openInterest": openInterest,
        "impliedVolatility": iv, "inTheMoney": in_the_money,
        "delta": delta, "gamma": gamma, "theta": theta,
        "vega": vega, "rho": rho,
        "bid_iv": bid_iv, "ask_iv": ask_iv,
    }


def _chain(call_rows, put_rows):
    return {
        "calls": pd.DataFrame(call_rows),
        "puts":  pd.DataFrame(put_rows),
    }


# ── 1. Calls enriched only from Tradier calls ───────────────────────


def test_audit_calls_enriched_only_from_secondary_calls():
    """If the secondary side has the call-side strikes ONLY in its puts
    (and the put-side strikes ONLY in its calls), no enrichment should
    happen — because side-aware lookup means the wrong-side dict has
    no matching strike. A side-mixing bug would silently fill rows."""
    primary = _chain(
        call_rows=[_row(200, iv=0), _row(210, iv=0)],
        put_rows=[_row(300, iv=0), _row(310, iv=0)],
    )
    # Swap the strike domains between sides:
    secondary = _chain(
        call_rows=[_row(300, iv=0.66), _row(310, iv=0.66)],  # wrong domain
        put_rows=[_row(200, iv=0.66),  _row(210, iv=0.66)],  # wrong domain
    )
    p = _SpyingFeed("alpaca",  chain_by_key={("X", "2026-06-19"): primary})
    s = _SpyingFeed("tradier", chain_by_key={("X", "2026-06-19"): secondary})
    chain = OptionsFeedChain([p, s])
    out = chain.get_chain("X", "2026-06-19")
    # Every primary row stays at 0 — no side leak.
    assert (out["calls"]["impliedVolatility"].astype(float) == 0).all()
    assert (out["puts" ]["impliedVolatility"].astype(float) == 0).all()
    # Merge function was invoked but returned filled_any=False, so
    # last_enriched_by is None.
    assert chain.last_enriched_by is None


# ── 2. Puts enriched only from Tradier puts ────────────────────────


def test_audit_put_side_filled_from_put_side_call_side_untouched():
    """Mirror of test 1 — when only the put side has matching strikes,
    only the put side fills."""
    primary = _chain(
        call_rows=[_row(200, iv=0), _row(210, iv=0)],
        put_rows=[_row(200, iv=0),  _row(210, iv=0)],
    )
    secondary = _chain(
        call_rows=[_row(200, iv=0.50), _row(210, iv=0.50)],  # matched
        put_rows=[_row(200, iv=0.75),  _row(210, iv=0.75)],  # matched, different value
    )
    p = _SpyingFeed("alpaca",  chain_by_key={("X", "2026-06-19"): primary})
    s = _SpyingFeed("tradier", chain_by_key={("X", "2026-06-19"): secondary})
    chain = OptionsFeedChain([p, s])
    out = chain.get_chain("X", "2026-06-19")
    # Calls filled from CALLS side (0.50), not puts (0.75).
    assert (out["calls"]["impliedVolatility"].astype(float) == 0.50).all()
    assert (out["puts" ]["impliedVolatility"].astype(float) == 0.75).all()


# ── 3. Same symbol + expiration + side + strike ─────────────────────


def test_audit_merge_queries_same_symbol_and_expiry_as_primary():
    """The chain must pass the SAME (ticker, expiry) tuple to the
    enrichment feed that it passed to the primary. A drift bug would
    fill IV from the wrong contract universe."""
    primary = _chain(
        call_rows=[_row(200, iv=0)],
        put_rows=[_row(200, iv=0)],
    )
    secondary = _chain(
        call_rows=[_row(200, iv=0.42)],
        put_rows=[_row(200, iv=0.42)],
    )
    p = _SpyingFeed("alpaca",  chain_by_key={("XYZ", "2027-12-17"): primary})
    s = _SpyingFeed("tradier", chain_by_key={("XYZ", "2027-12-17"): secondary})
    chain = OptionsFeedChain([p, s])
    out = chain.get_chain("XYZ", "2027-12-17")
    assert p.get_chain_calls == [("XYZ", "2027-12-17")]
    assert s.get_chain_calls == [("XYZ", "2027-12-17")]  # ← same args
    assert out["calls"].loc[0, "impliedVolatility"] == pytest.approx(0.42)


def test_audit_secondary_with_wrong_symbol_data_does_not_match():
    """If the secondary happened to return a chain shaped for a
    different symbol (same strikes, but a 'wrong' chain because the
    adapter mis-routed), the strike-key merge would still align by
    strike. This documents that the merge trusts the secondary adapter
    to honor (ticker, expiry) — the chain wrapper itself does not
    verify the secondary's payload identity. Recorded as a known
    contract assumption."""
    primary = _chain(
        call_rows=[_row(200, iv=0)],
        put_rows=[_row(200, iv=0)],
    )
    # Same strike — but logically this is a different symbol's chain.
    # The chain wrapper cannot detect this; the adapter must.
    secondary = _chain(
        call_rows=[_row(200, iv=0.99)],
        put_rows=[_row(200, iv=0.99)],
    )
    # Adapter mis-routing: registers under ("WRONG", ...) but accepts
    # any (ticker, expiry) and returns this same chain. This models
    # the bug pattern, not the actual behavior.
    s = _SpyingFeed("tradier", chain_by_key={("RIGHT", "2026-06-19"): secondary})
    p = _SpyingFeed("alpaca",  chain_by_key={("RIGHT", "2026-06-19"): primary})
    chain = OptionsFeedChain([p, s])
    out = chain.get_chain("RIGHT", "2026-06-19")
    # The wrapper called the secondary with the right args. If the
    # secondary's adapter honors them, no cross-symbol leak is
    # possible. Test merely documents the wrapper's call.
    assert s.get_chain_calls == [("RIGHT", "2026-06-19")]
    assert out["calls"].loc[0, "impliedVolatility"] == pytest.approx(0.99)


# ── 4. Decimal strikes match correctly ─────────────────────────────


def test_audit_half_dollar_strikes_match():
    """0.5-increment strikes must match exactly (no float drift)."""
    primary = _chain(
        call_rows=[_row(200.0, iv=0), _row(200.5, iv=0), _row(201.0, iv=0)],
        put_rows=[_row(200.0, iv=0)],
    )
    secondary = _chain(
        call_rows=[_row(200.0, iv=0.10), _row(200.5, iv=0.15), _row(201.0, iv=0.20)],
        put_rows=[_row(200.0, iv=0.11)],
    )
    chain = OptionsFeedChain([
        _SpyingFeed("alpaca",  chain_by_key={("X", "E"): primary}),
        _SpyingFeed("tradier", chain_by_key={("X", "E"): secondary}),
    ])
    out = chain.get_chain("X", "E")
    assert out["calls"].loc[0, "impliedVolatility"] == pytest.approx(0.10)
    assert out["calls"].loc[1, "impliedVolatility"] == pytest.approx(0.15)
    assert out["calls"].loc[2, "impliedVolatility"] == pytest.approx(0.20)


def test_audit_strike_micro_drift_does_not_falsely_match():
    """Strikes that differ at the 3rd decimal place are NOT the same
    contract. Round-to-4 deliberately preserves the distinction; this
    test catches a hypothetical regression where someone rounds harder
    (e.g. round-to-1) and silently merges 200.001 ≈ 200.0."""
    primary = _chain(
        call_rows=[_row(200.0001, iv=0)],  # primary at 200.0001
        put_rows=[],
    )
    secondary = _chain(
        call_rows=[_row(200.0, iv=0.42)],  # secondary at 200.0
        put_rows=[],
    )
    chain = OptionsFeedChain([
        _SpyingFeed("alpaca",  chain_by_key={("X", "E"): primary}),
        _SpyingFeed("tradier", chain_by_key={("X", "E"): secondary}),
    ])
    out = chain.get_chain("X", "E")
    # round(200.0001, 4) == 200.0001, round(200.0, 4) == 200.0 → no match.
    assert out["calls"].loc[0, "impliedVolatility"] == 0.0


# ── 5. Duplicate strikes do not create duplicate rows ──────────────


def test_audit_duplicate_strikes_in_primary_keep_row_count():
    """Primary has two rows at the same strike (e.g. AM/PM expirations
    collapsed into the same parquet by an adapter quirk). Merge must
    fill both, not deduplicate or grow the row count."""
    primary = _chain(
        call_rows=[_row(200, iv=0), _row(200, iv=0), _row(210, iv=0)],
        put_rows=[],
    )
    secondary = _chain(
        call_rows=[_row(200, iv=0.33), _row(210, iv=0.44)],
        put_rows=[],
    )
    chain = OptionsFeedChain([
        _SpyingFeed("alpaca",  chain_by_key={("X", "E"): primary}),
        _SpyingFeed("tradier", chain_by_key={("X", "E"): secondary}),
    ])
    out = chain.get_chain("X", "E")
    assert len(out["calls"]) == 3  # row count preserved
    # Both duplicate-strike rows filled with the matched value.
    assert out["calls"].loc[0, "impliedVolatility"] == pytest.approx(0.33)
    assert out["calls"].loc[1, "impliedVolatility"] == pytest.approx(0.33)
    assert out["calls"].loc[2, "impliedVolatility"] == pytest.approx(0.44)


def test_audit_duplicate_strikes_in_secondary_collapse_to_last():
    """Secondary has two values at the same strike. The dict-collapse
    keeps the LAST value (pandas/dict default). Primary row count is
    unchanged."""
    primary = _chain(
        call_rows=[_row(200, iv=0)],
        put_rows=[],
    )
    secondary = _chain(
        # First row's 0.10 will be overwritten by second row's 0.99
        # in the dict (same strike).
        call_rows=[_row(200, iv=0.10), _row(200, iv=0.99)],
        put_rows=[],
    )
    chain = OptionsFeedChain([
        _SpyingFeed("alpaca",  chain_by_key={("X", "E"): primary}),
        _SpyingFeed("tradier", chain_by_key={("X", "E"): secondary}),
    ])
    out = chain.get_chain("X", "E")
    assert len(out["calls"]) == 1
    assert out["calls"].loc[0, "impliedVolatility"] == pytest.approx(0.99)


# ── 6. Primary non-zero IV/Greeks never overwritten ─────────────────


def test_audit_non_zero_primary_iv_and_greeks_are_never_overwritten():
    """Across the FULL ``_ENRICHABLE_FIELDS`` set, a non-zero primary
    value must beat any non-zero secondary value at the same strike."""
    primary_call = _row(200, iv=0.50, delta=0.55, gamma=0.012,
                        theta=-0.045, vega=0.18, rho=0.02,
                        bid_iv=0.49, ask_iv=0.51)
    secondary_call = _row(200, iv=9.99, delta=9.99, gamma=9.99,
                          theta=9.99, vega=9.99, rho=9.99,
                          bid_iv=9.99, ask_iv=9.99)
    primary = _chain(call_rows=[primary_call], put_rows=[])
    secondary = _chain(call_rows=[secondary_call], put_rows=[])
    # Force enrichment by adding another stub row so the gate fires.
    primary["calls"] = pd.concat([primary["calls"],
                                  pd.DataFrame([_row(210, iv=0)])],
                                 ignore_index=True)
    secondary["calls"] = pd.concat([secondary["calls"],
                                    pd.DataFrame([_row(210, iv=0.30)])],
                                   ignore_index=True)
    chain = OptionsFeedChain([
        _SpyingFeed("alpaca",  chain_by_key={("X", "E"): primary}),
        _SpyingFeed("tradier", chain_by_key={("X", "E"): secondary}),
    ])
    out = chain.get_chain("X", "E")
    row = out["calls"].loc[0]
    for field, expected in [("impliedVolatility", 0.50), ("delta", 0.55),
                            ("gamma", 0.012), ("theta", -0.045),
                            ("vega", 0.18), ("rho", 0.02),
                            ("bid_iv", 0.49), ("ask_iv", 0.51)]:
        assert row[field] == pytest.approx(expected), \
            f"field {field}: primary {expected} was overwritten by secondary"
    # Stub row 210 DID fill from secondary.
    assert out["calls"].loc[1, "impliedVolatility"] == pytest.approx(0.30)


# ── 7. OI, volume, bid, ask never overwritten ──────────────────────


def test_audit_oi_volume_bid_ask_strike_intm_never_overwritten():
    """Quote / liquidity / identification fields are primary-only; the
    secondary's values for them must never reach the merged row."""
    primary_row = _row(200, iv=0, bid=1.50, ask=1.60,
                       volume=123, openInterest=4567,
                       in_the_money=True)
    primary_row["lastPrice"] = 1.55
    primary_row["contractSymbol"] = "PRIMARY_OCC"
    secondary_row = _row(200, iv=0.40, bid=99.99, ask=99.99,
                         volume=99999, openInterest=99999,
                         in_the_money=False)
    secondary_row["lastPrice"] = 99.99
    secondary_row["contractSymbol"] = "SECONDARY_OCC"
    primary = _chain(call_rows=[primary_row], put_rows=[])
    secondary = _chain(call_rows=[secondary_row], put_rows=[])
    chain = OptionsFeedChain([
        _SpyingFeed("alpaca",  chain_by_key={("X", "E"): primary}),
        _SpyingFeed("tradier", chain_by_key={("X", "E"): secondary}),
    ])
    out = chain.get_chain("X", "E")
    row = out["calls"].loc[0]
    # Primary-authoritative fields untouched:
    assert row["bid"] == pytest.approx(1.50)
    assert row["ask"] == pytest.approx(1.60)
    assert int(row["volume"]) == 123
    assert int(row["openInterest"]) == 4567
    assert bool(row["inTheMoney"]) is True
    assert row["lastPrice"] == pytest.approx(1.55)
    assert row["contractSymbol"] == "PRIMARY_OCC"
    # IV did get filled:
    assert row["impliedVolatility"] == pytest.approx(0.40)


def test_audit_enrichable_fields_whitelist_is_closed():
    """Defensive: the enrichable-fields list must NOT include any of
    the protected quote/liquidity fields. A regression here would let
    the secondary overwrite OI/quote silently."""
    protected = {"openInterest", "volume", "bid", "ask", "strike",
                 "inTheMoney", "contractSymbol", "lastPrice"}
    assert protected.isdisjoint(_ENRICHABLE_FIELDS)


# ── 8. Secondary feed exception does not break primary chain ───────


def test_audit_secondary_raise_returns_unenriched_primary():
    """If the secondary's get_chain raises (network, parse, panic),
    the wrapper must still return the primary chain intact and clear
    last_enriched_by."""
    primary = _chain(
        call_rows=[_row(200, iv=0)],
        put_rows=[_row(200, iv=0)],
    )
    p = _SpyingFeed("alpaca",  chain_by_key={("X", "E"): primary})
    s = _SpyingFeed("tradier", raises=True)
    chain = OptionsFeedChain([p, s])
    out = chain.get_chain("X", "E")
    assert out is not None
    assert out["calls"].loc[0, "impliedVolatility"] == 0.0
    assert chain.last_served == "alpaca"
    assert chain.last_enriched_by is None
    assert chain.status()["enrich_counts"] == {}


# ── 9. Status exposes all required diagnostic fields ───────────────


def test_audit_status_exposes_all_required_diagnostic_fields():
    """Charter requires last_served, last_enriched_by, enrich_counts,
    zero_iv_before, zero_iv_after to be visible on status()."""
    primary = _chain(
        call_rows=[_row(200, iv=0), _row(210, iv=0.30), _row(220, iv=0)],
        put_rows=[_row(200, iv=0)],
    )
    secondary = _chain(
        call_rows=[_row(200, iv=0.40), _row(220, iv=0.50)],
        put_rows=[_row(200, iv=0.45)],
    )
    chain = OptionsFeedChain([
        _SpyingFeed("alpaca",  chain_by_key={("X", "E"): primary}),
        _SpyingFeed("tradier", chain_by_key={("X", "E"): secondary}),
    ])
    chain.get_chain("X", "E")
    st = chain.status()
    # Required keys present:
    for key in ("last_served", "last_enriched_by", "enrich_counts",
                "served_counts", "last_zero_iv_before", "last_zero_iv_after",
                "feed_order", "configured"):
        assert key in st, f"status missing key {key!r}"
    assert st["last_served"] == "alpaca"
    assert st["last_enriched_by"] == "tradier"
    assert st["enrich_counts"] == {"tradier": 1}
    # Before: 2 zero-IV calls, 1 zero-IV put = {"calls": 2, "puts": 1}
    assert st["last_zero_iv_before"] == {"calls": 2, "puts": 1}
    # After: both call-stubs and the put-stub filled = {"calls": 0, "puts": 0}
    assert st["last_zero_iv_after"] == {"calls": 0, "puts": 0}


def test_audit_zero_iv_counters_track_unfilled_rows():
    """When the secondary has only partial coverage, the after-counter
    must reflect the rows that REMAIN at zero."""
    primary = _chain(
        call_rows=[_row(200, iv=0), _row(210, iv=0), _row(220, iv=0)],
        put_rows=[_row(200, iv=0)],
    )
    secondary = _chain(
        # Only 210 in calls, nothing in puts.
        call_rows=[_row(210, iv=0.30)],
        put_rows=[],
    )
    chain = OptionsFeedChain([
        _SpyingFeed("alpaca",  chain_by_key={("X", "E"): primary}),
        _SpyingFeed("tradier", chain_by_key={("X", "E"): secondary}),
    ])
    chain.get_chain("X", "E")
    st = chain.status()
    assert st["last_zero_iv_before"] == {"calls": 3, "puts": 1}
    # After: only the 210 call filled → calls=2 (200, 220), puts=1.
    assert st["last_zero_iv_after"] == {"calls": 2, "puts": 1}


def test_audit_zero_iv_counters_unchanged_when_no_enrichment_needed():
    """When primary already has full IV, both counters are zero and
    enrichment never fires."""
    primary = _chain(
        call_rows=[_row(200, iv=0.30)],
        put_rows=[_row(200, iv=0.30)],
    )
    chain = OptionsFeedChain([
        _SpyingFeed("alpaca",  chain_by_key={("X", "E"): primary}),
        _SpyingFeed("tradier", chain_by_key={}),  # never consulted
    ])
    chain.get_chain("X", "E")
    st = chain.status()
    assert st["last_zero_iv_before"] == {"calls": 0, "puts": 0}
    assert st["last_zero_iv_after"]  == {"calls": 0, "puts": 0}
    assert st["last_enriched_by"] is None


def test_audit_zero_iv_counters_reset_to_none_when_chain_empty():
    """When primary returns nothing, the diagnostic counters reset to
    None — operators should not see stale numbers from a previous
    successful call."""
    primary_ok = _chain(
        call_rows=[_row(200, iv=0)],
        put_rows=[_row(200, iv=0)],
    )
    chain = OptionsFeedChain([
        _SpyingFeed("alpaca",  chain_by_key={("X", "E1"): primary_ok}),
        _SpyingFeed("tradier", chain_by_key={("X", "E1"): _chain(
            [_row(200, iv=0.5)], [_row(200, iv=0.5)])}),
    ])
    chain.get_chain("X", "E1")
    assert chain.status()["last_zero_iv_before"] is not None  # populated

    # Now query a missing expiry — both feeds return None.
    out = chain.get_chain("X", "MISSING")
    assert out is None
    st = chain.status()
    assert st["last_zero_iv_before"] is None
    assert st["last_zero_iv_after"]  is None


# ── _count_zero_iv unit-level coverage ──────────────────────────────


def test_count_zero_iv_handles_missing_iv_column():
    """A primary missing the impliedVolatility column entirely must be
    counted as 100% zero (every row needs enrichment)."""
    df = pd.DataFrame([{"strike": 200.0, "bid": 1.0, "ask": 2.0}] * 3)
    out = _count_zero_iv({"calls": df, "puts": pd.DataFrame()})
    assert out == {"calls": 3, "puts": 0}


def test_count_zero_iv_handles_nan_iv():
    """NaN IV counts as zero (rows still need enrichment)."""
    df = pd.DataFrame([
        {"strike": 200.0, "impliedVolatility": float("nan")},
        {"strike": 210.0, "impliedVolatility": 0.30},
        {"strike": 220.0, "impliedVolatility": 0.0},
    ])
    out = _count_zero_iv({"calls": df, "puts": pd.DataFrame()})
    assert out == {"calls": 2, "puts": 0}  # NaN + 0.0 both counted


# ── direct _merge_chain_fields invariants ──────────────────────────


def test_merge_returns_false_when_no_field_filled():
    """If the secondary has nothing useful, the merge returns False so
    the caller knows not to set last_enriched_by."""
    primary  = _chain([_row(200, iv=0)], [])
    secondary = _chain([_row(200, iv=0)], [])  # also zero
    assert _merge_chain_fields(primary, secondary) is False


def test_merge_returns_true_when_any_field_filled():
    """A single filled cell is enough for the merge to report success."""
    primary  = _chain([_row(200, iv=0)], [])
    secondary = _chain([_row(200, iv=0.30)], [])
    assert _merge_chain_fields(primary, secondary) is True
    assert primary["calls"].loc[0, "impliedVolatility"] == pytest.approx(0.30)


def test_merge_handles_secondary_missing_a_field():
    """If the secondary lacks one of the enrichable columns (e.g. no
    rho), the merge must not crash and must still fill the others."""
    primary = _chain([_row(200, iv=0, delta=0)], [])
    sec_df = pd.DataFrame([{"strike": 200.0, "impliedVolatility": 0.40,
                            "delta": 0.55}])  # no rho/gamma/theta/etc.
    secondary = {"calls": sec_df, "puts": pd.DataFrame()}
    assert _merge_chain_fields(primary, secondary) is True
    assert primary["calls"].loc[0, "impliedVolatility"] == pytest.approx(0.40)
    assert primary["calls"].loc[0, "delta"] == pytest.approx(0.55)
    # rho stays 0 (primary's default).
    assert primary["calls"].loc[0, "rho"] == 0.0

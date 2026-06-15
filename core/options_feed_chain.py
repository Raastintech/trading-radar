"""
core/options_feed_chain.py — fallback wrapper for options feeds.

Wraps an ordered list of feeds (each exposing the same 3-method
contract: ``is_configured() / get_expirations() / get_chain()``) and
serves the first non-empty response. The wrapper itself is also a
feed, so callers don't need to know the chain exists.

Options Data Enrichment — per-field merge (shipped 2026-05-21). Note
this is the cross-cutting options-data layer, not roadmap Phase 2C
(which is reserved for the future Trade Proposal Generator).
``get_chain`` still picks the first non-empty leaf as the *primary*
result, but when that primary leaf is missing IV / greeks (e.g.
Alpaca Pro+ which doesn't expose them), the chain walks the remaining
configured leaves and merges IV / greeks from a secondary leaf onto
the primary's rows, matched by ``strike``.
Primary values that are already meaningful are never overwritten —
this is a pure fill-in for the stubbed-as-zero fields on Alpaca-served
rows. Diagnostics expose ``last_enriched_by`` and ``enrich_counts`` so
operators can confirm "Alpaca served the chain, Tradier filled IV."

Doctrine:

  - Read-only / data-only. No execution, no governance, no DB writes.
  - Fail-soft. Catches exceptions from each leaf feed and falls
    through to the next; an enrichment failure is logged at debug and
    leaves the primary chain unchanged.
  - Observable. Records which leaf served each call in
    ``last_served`` and which (if any) enriched it in
    ``last_enriched_by``.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# Fields the merger will fill on the primary chain when they are
# missing (0 / NaN / None). These are exactly the fields Alpaca Pro+
# stubs as 0 because its snapshot endpoint does not expose IV/greeks.
# Quote / trade / volume / OI / strike are NOT in this set — they come
# from the primary feed and are never overwritten.
_ENRICHABLE_FIELDS: Tuple[str, ...] = (
    "impliedVolatility",
    "delta", "gamma", "theta", "vega", "rho",
    "bid_iv", "ask_iv",
)

_STRIKE_MATCH_DECIMALS = 4


class OptionsFeedChain:
    """Try each feed in order; return the first non-empty result.
    Optionally enrich IV / greeks from a secondary leaf."""

    NAME = "chain"

    def __init__(self, feeds: List[Any]):
        # Keep only feeds that are configured. This means a chain with
        # only-Alpaca-configured returns Alpaca for everything; a chain
        # with both returns Alpaca first, Tradier as fallback; an empty
        # chain reports is_configured()==False so the lens cleanly
        # falls through to OPTIONS_MISSING.
        self._feeds: List[Any] = [f for f in feeds if f is not None and f.is_configured()]
        self.last_served: Optional[str] = None
        self.last_enriched_by: Optional[str] = None
        self.served_counts: Dict[str, int] = {}
        self.enrich_counts: Dict[str, int] = {}
        # Diagnostics: count of rows with impliedVolatility == 0 in the
        # most recent chain, before/after the per-field merge. Populated
        # by get_chain. Stays None until the first call.
        self.last_zero_iv_before: Optional[Dict[str, int]] = None
        self.last_zero_iv_after:  Optional[Dict[str, int]] = None

    def is_configured(self) -> bool:
        return bool(self._feeds)

    # ── helpers ───────────────────────────────────────────────────────

    def _name(self, feed: Any) -> str:
        return str(getattr(feed, "NAME", feed.__class__.__name__))

    def _try(self, method_name: str, *args, **kwargs) -> Tuple[Optional[str], Any, int]:
        """Invoke ``method_name`` on each feed in turn; return
        ``(feed_name, result, index)`` for the first feed that returns
        a non-empty value. ``index`` is the position in ``self._feeds``
        so the caller can walk feeds *after* the primary for
        enrichment. Empty == None, empty tuple, empty dict, or a dict
        whose ``calls`` AND ``puts`` are both empty."""
        for idx, feed in enumerate(self._feeds):
            name = self._name(feed)
            try:
                result = getattr(feed, method_name)(*args, **kwargs)
            except Exception as exc:
                logger.info("options chain: %s.%s raised: %s — trying next feed",
                            name, method_name, exc)
                continue
            if _is_empty(result):
                logger.debug("options chain: %s.%s empty — trying next feed",
                             name, method_name)
                continue
            self.last_served = name
            self.served_counts[name] = self.served_counts.get(name, 0) + 1
            return name, result, idx
        return None, None, -1

    # ── public surface (same as Tradier/Alpaca feeds) ─────────────────

    def get_expirations(self, ticker: str) -> Tuple[str, ...]:
        _, result, _ = self._try("get_expirations", ticker)
        return tuple(result) if result else ()

    def get_chain(self, ticker: str, expiry: str) -> Optional[Dict[str, Any]]:
        primary_name, primary, idx = self._try("get_chain", ticker, expiry)
        if primary is None:
            self.last_enriched_by = None
            self.last_zero_iv_before = None
            self.last_zero_iv_after = None
            return None

        # Snapshot zero-IV row counts BEFORE any merge. Captured on the
        # primary feed's response (pre-copy) for parity with the cached
        # state an operator would otherwise see.
        self.last_zero_iv_before = _count_zero_iv(primary)

        # Options Data Enrichment — opportunistic per-field merge.
        # Only fires when the
        # primary chain is missing IV/greeks AND there is at least one
        # downstream feed configured. This keeps the cost at zero for
        # Tradier-served chains (already complete) and Alpaca-only
        # deployments (no downstream feed to consult).
        #
        # Before merging we copy the primary's DataFrames so the
        # mutation never leaks back into the primary feed's response
        # cache (some adapters memoize DataFrame instances by
        # (ticker, expiry) for TTL windows).
        self.last_enriched_by = None
        if idx >= 0 and _chain_needs_enrichment(primary) and idx + 1 < len(self._feeds):
            primary = _shallow_copy_chain(primary)
            for enricher in self._feeds[idx + 1:]:
                e_name = self._name(enricher)
                try:
                    enrich_result = enricher.get_chain(ticker, expiry)
                except Exception as exc:
                    logger.debug("options chain: %s.get_chain enrichment raised: %s",
                                 e_name, exc)
                    continue
                if _is_empty(enrich_result):
                    continue
                if _merge_chain_fields(primary, enrich_result):
                    self.last_enriched_by = e_name
                    self.enrich_counts[e_name] = self.enrich_counts.get(e_name, 0) + 1
                    break  # one successful enricher is enough

        # Snapshot zero-IV AFTER merge (or no-op merge — same DataFrame).
        self.last_zero_iv_after = _count_zero_iv(primary)
        return primary

    # ── status dict for diagnostics ───────────────────────────────────

    def status(self) -> Dict[str, Any]:
        return {
            "name":                self.NAME,
            "configured":          self.is_configured(),
            "feed_order":          [self._name(f) for f in self._feeds],
            "served_counts":       dict(self.served_counts),
            "last_served":         self.last_served,
            "enrich_counts":       dict(self.enrich_counts),
            "last_enriched_by":    self.last_enriched_by,
            "last_zero_iv_before": dict(self.last_zero_iv_before) if self.last_zero_iv_before is not None else None,
            "last_zero_iv_after":  dict(self.last_zero_iv_after)  if self.last_zero_iv_after  is not None else None,
        }


# ── helpers ───────────────────────────────────────────────────────────

def _is_empty(value: Any) -> bool:
    """A result is "empty" if the caller would want to keep looking."""
    if value is None:
        return True
    if isinstance(value, (list, tuple, set, dict)) and not value:
        return True
    # Chain dict: {"calls": DataFrame, "puts": DataFrame}. If both
    # DataFrames are empty, treat as empty — even though the dict
    # itself is non-empty.
    if isinstance(value, dict) and "calls" in value and "puts" in value:
        try:
            calls = value.get("calls")
            puts  = value.get("puts")
            c_empty = calls is None or _df_empty(calls)
            p_empty = puts  is None or _df_empty(puts)
            return bool(c_empty and p_empty)
        except Exception:
            return False
    return False


def _shallow_copy_chain(result: Dict[str, Any]) -> Dict[str, Any]:
    """Return a new chain dict whose ``calls`` / ``puts`` DataFrames are
    independent copies. Used to isolate per-field merging from any
    upstream feed cache that returns the same DataFrame by reference
    across calls."""
    out: Dict[str, Any] = dict(result)
    for side in ("calls", "puts"):
        df = out.get(side)
        if df is None:
            continue
        copy_fn = getattr(df, "copy", None)
        if callable(copy_fn):
            try:
                out[side] = copy_fn()
            except Exception:
                # If copy fails for an exotic type, leave the reference
                # in place — the merge will still work, just with the
                # pre-existing pollution risk.
                continue
    return out


def _df_empty(df: Any) -> bool:
    """``True`` if ``df`` is a pandas DataFrame and ``empty``, or
    a list/tuple that's empty."""
    if df is None:
        return True
    empty_attr = getattr(df, "empty", None)
    if empty_attr is not None:
        return bool(empty_attr)
    if isinstance(df, (list, tuple)):
        return len(df) == 0
    return False


def _count_zero_iv(result: Any) -> Dict[str, int]:
    """Return ``{"calls": N, "puts": M}`` of rows whose
    ``impliedVolatility`` is zero (or the column is absent — every row
    counts as zero in that case). Used for operator diagnostics: how
    many rows did the merge actually need to fill?"""
    out: Dict[str, int] = {"calls": 0, "puts": 0}
    if not isinstance(result, dict):
        return out
    for side in ("calls", "puts"):
        df = result.get(side)
        if df is None or _df_empty(df):
            continue
        cols = getattr(df, "columns", [])
        n = len(df) if hasattr(df, "__len__") else 0
        if "impliedVolatility" not in cols:
            out[side] = int(n)
            continue
        try:
            iv = df["impliedVolatility"].fillna(0).astype(float)
            out[side] = int((iv.abs() == 0).sum())
        except Exception:
            out[side] = int(n)
    return out


def _chain_needs_enrichment(result: Any) -> bool:
    """Return True iff the primary chain has at least one row whose
    ``impliedVolatility`` is missing (0 / NaN / None). The per-row
    merge in :func:`_merge_chain_fields` will then fill only those
    zero rows; rows with a meaningful primary IV are left alone.

    This is intentionally fine-grained: real Alpaca chains return IV
    for a fraction of contracts (typically the active strikes) and
    stub the rest at 0. A coarse "no IV anywhere => enrich" gate
    would skip enrichment on those chains entirely.

    Why IV and not greeks? Both adapters write IV alongside greeks
    from the same source — when IV is stubbed, greeks are stubbed
    too. Checking IV alone keeps the gate cheap."""
    if not isinstance(result, dict):
        return False
    for side in ("calls", "puts"):
        df = result.get(side)
        if df is None or _df_empty(df):
            continue
        if "impliedVolatility" not in getattr(df, "columns", []):
            return True
        try:
            iv = df["impliedVolatility"].fillna(0).astype(float)
        except Exception:
            return True
        if (iv.abs() == 0).any():
            return True
    return False


def _merge_chain_fields(primary: Dict[str, Any], enrich: Dict[str, Any]) -> bool:
    """Fill ``_ENRICHABLE_FIELDS`` on ``primary`` from ``enrich``,
    matched by strike. Returns True if any value was filled. Mutates
    ``primary`` in place. Never overwrites a meaningful (non-zero)
    primary value."""
    if not isinstance(primary, dict) or not isinstance(enrich, dict):
        return False
    filled_any = False
    for side in ("calls", "puts"):
        p = primary.get(side)
        e = enrich.get(side)
        if p is None or e is None or _df_empty(p) or _df_empty(e):
            continue
        if "strike" not in getattr(p, "columns", []) or "strike" not in getattr(e, "columns", []):
            continue
        try:
            p_strikes = p["strike"].astype(float).round(_STRIKE_MATCH_DECIMALS).tolist()
            e_strikes = e["strike"].astype(float).round(_STRIKE_MATCH_DECIMALS).tolist()
        except Exception as exc:
            logger.debug("options chain: strike coercion failed (%s) — skipping merge", exc)
            continue

        for field in _ENRICHABLE_FIELDS:
            if field not in e.columns:
                continue
            try:
                # Build strike -> enrichment value lookup. Duplicate
                # strikes (rare; quarterlies vs weeklies on same date
                # filtered by the adapter) keep the last value, which
                # matches pandas default.
                e_vals = e[field].fillna(0).astype(float).tolist()
                e_map = {k: v for k, v in zip(e_strikes, e_vals)}
            except Exception as exc:
                logger.debug("options chain: enrich coercion failed (%s/%s): %s",
                             side, field, exc)
                continue

            if field not in p.columns:
                # Primary doesn't have the column at all — create it.
                p[field] = 0.0

            try:
                p_vals = p[field].fillna(0).astype(float).tolist()
            except Exception as exc:
                logger.debug("options chain: primary coercion failed (%s/%s): %s",
                             side, field, exc)
                continue

            merged = []
            side_filled = False
            for k, cur in zip(p_strikes, p_vals):
                if cur and float(cur) != 0.0:
                    merged.append(float(cur))
                else:
                    e_val = e_map.get(k, 0.0)
                    if e_val and float(e_val) != 0.0:
                        side_filled = True
                    merged.append(float(e_val))
            p[field] = merged
            if side_filled:
                filled_any = True

    return filled_any

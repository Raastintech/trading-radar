"""Statistical safety rails for the forward-evidence reports.

Three failure modes this module exists to stop:

1. **A corrupted bar becoming the headline.**  ``cache/prices/PI.parquet``
   carries four weekend bars priced near $0.09 — a 24/7 instrument's series
   spliced into Impinj's daily equity bars.  The forward tracker took the
   Saturday 2026-08-15 close as an entry price and resolved a +189,857% 10d
   return.  That one row moved the 6,216-row mean from -1.7% to +28.9% and,
   through the program-candidate baseline, decided a published shortlist
   verdict.
2. **A mean quoted beside a median that contradicts it.**  Forward returns
   are skewed even when every bar is clean, so a bare mean overstates the
   typical outcome.
3. **Overlapping observations counted as independent evidence.**  The same
   ticker re-appears on the watchlist night after night; 6,216 matured rows
   are 807 distinct names.

Cache-only, cred-free, no provider calls.  Every helper here is reporting
only: nothing changes a score, a ranking, a gate, a threshold, or which
candidates a surface selects.
"""
from __future__ import annotations

import statistics
from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

#: A US equity daily series cannot have a bar on a weekend.  A bar that does
#: is another instrument's row, so both its price and its position in the
#: bar count are wrong.
SESSION_WEEKDAYS = frozenset({0, 1, 2, 3, 4})

#: Data-error net, not an outlier filter.  A ten-bagger inside three months
#: is a price-cache fault, not a trade; genuine fat tails stay in the sample
#: and are handled by reporting the median and the winsorized mean.  Longer
#: horizons get a higher bar because multi-year multiples do happen.
ARTIFACT_ABS_RETURN_PCT = 1000.0
ARTIFACT_LONG_HORIZON_TD = 60
ARTIFACT_LONG_ABS_RETURN_PCT = 2000.0

#: Winsorization trims this fraction from each tail before re-averaging.
WINSOR_TAIL_FRACTION = 0.05
#: Below this count winsorizing says more about the trim than the sample.
WINSOR_MIN_N = 5
#: A mean this far from its median is not describing the typical outcome.
MEAN_MEDIAN_GAP_PP = 10.0


# ── session hygiene ──────────────────────────────────────────────────────────


def is_session_date(value: Any) -> bool:
    """True when ``value`` (YYYY-MM-DD or anything starting with one) is a
    weekday.  Holidays are not knowable from the cache and are not the
    failure mode this guards — a wrong-instrument splice shows up on
    weekends."""
    try:
        d = date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return False
    return d.weekday() in SESSION_WEEKDAYS


def drop_non_session_bars(
        pairs: Sequence[Tuple[str, float]]
) -> Tuple[List[Tuple[str, float]], List[Tuple[str, float]]]:
    """Split ``(date, close)`` pairs into session bars and rejected ones.

    Dropping these is not cosmetic: a stray bar both supplies a wrong entry
    price and shifts every horizon, because forward returns are counted in
    bars.
    """
    kept, dropped = [], []
    for pair in pairs:
        (kept if is_session_date(pair[0]) else dropped).append(pair)
    return kept, dropped


# ── data-error net ───────────────────────────────────────────────────────────


def is_price_artifact(ret_pct: Any, horizon_td: Optional[int] = None) -> bool:
    """True when a return is too large to be a price move rather than a
    broken bar."""
    try:
        value = abs(float(ret_pct))
    except (TypeError, ValueError):
        return False
    limit = (ARTIFACT_LONG_ABS_RETURN_PCT
             if horizon_td and horizon_td > ARTIFACT_LONG_HORIZON_TD
             else ARTIFACT_ABS_RETURN_PCT)
    return value >= limit


def artifact_fields(row: Dict[str, Any],
                    horizons: Iterable[int]) -> List[str]:
    """Return-field names in ``row`` that fail the data-error net."""
    hit = []
    for h in horizons:
        field = f"ret_{h}d"
        if field in row and is_price_artifact(row.get(field), h):
            hit.append(field)
    return hit


def split_price_artifacts(
        rows: Sequence[Dict[str, Any]], horizons: Iterable[int],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Partition ledger rows into usable rows and suspected price artifacts.

    A row is quarantined whole rather than per-horizon: when one horizon of
    an entry is impossible the entry price itself is suspect, so its other
    horizons are not evidence either.  The quarantined rows are returned,
    never dropped silently — the caller reports them.
    """
    horizons = list(horizons)
    clean, artifacts = [], []
    for row in rows:
        fields = artifact_fields(row, horizons)
        if fields:
            artifacts.append({
                "ticker": row.get("ticker"),
                "appearance_date": row.get("appearance_date"),
                "fields": fields,
                "values": {f: row.get(f) for f in fields},
                "reason": "return magnitude implies a broken price bar, "
                          "not a price move",
            })
        else:
            clean.append(row)
    return clean, artifacts


# ── robust statistics ────────────────────────────────────────────────────────


def _round(value: Optional[float]) -> Optional[float]:
    return round(value, 2) if value is not None else None


def winsorized_mean(values: Sequence[float]) -> Optional[float]:
    """Mean after pulling each tail in to the 5th/95th percentile value."""
    vals = sorted(float(v) for v in values)
    n = len(vals)
    if not vals:
        return None
    if n < WINSOR_MIN_N:
        return _round(sum(vals) / n)
    k = max(1, int(n * WINSOR_TAIL_FRACTION))
    lo, hi = vals[k], vals[-k - 1]
    return _round(sum(min(max(v, lo), hi) for v in vals) / n)


def robust_stats(values: Sequence[float]) -> Dict[str, Any]:
    """Mean, median, winsorized mean, win rate — plus the headline to quote.

    ``headline_mean`` is the winsorized mean whenever mean and median
    disagree by more than the gap the governor flags, so a skewed sample
    cannot be summarised by a number no single observation resembles.
    """
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return {"n": 0, "mean": None, "median": None,
                "winsorized_mean": None, "win_rate": None,
                "mean_median_gap_pp": None, "outlier_driven": False,
                "headline_mean": None}
    n = len(vals)
    mean = sum(vals) / n
    median = statistics.median(vals)
    winsorized = winsorized_mean(vals)
    gap = abs(mean - median)
    outlier_driven = gap > MEAN_MEDIAN_GAP_PP
    return {
        "n": n,
        "mean": _round(mean),
        "median": _round(median),
        "winsorized_mean": winsorized,
        "win_rate": round(100.0 * sum(1 for v in vals if v > 0) / n, 1),
        "mean_median_gap_pp": _round(gap),
        "outlier_driven": outlier_driven,
        "headline_mean": winsorized if outlier_driven else _round(mean),
    }


def per_entity_stats(rows: Sequence[Dict[str, Any]], value_field: str,
                     key_field: str = "ticker") -> Dict[str, Any]:
    """Robust stats over one observation per entity.

    Each ticker contributes the mean of its own appearances, so a name the
    scanner surfaced thirty nights running counts once.  This is the sample
    a claim about the surface should rest on; the row-weighted figures stay
    published beside it.
    """
    by_key: Dict[str, List[float]] = {}
    for row in rows:
        value = row.get(value_field)
        key = row.get(key_field)
        if value is None or key is None:
            continue
        try:
            by_key.setdefault(str(key), []).append(float(value))
        except (TypeError, ValueError):
            continue
    stats = robust_stats([sum(v) / len(v) for v in by_key.values()])
    stats["basis"] = f"one observation per {key_field}"
    stats["rows"] = sum(len(v) for v in by_key.values())
    stats["overlap_factor"] = (round(stats["rows"] / stats["n"], 2)
                               if stats["n"] else None)
    return stats

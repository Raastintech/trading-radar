"""M1 price-depth audit and staleness guard (research-only, zero provider calls).

The M1 Decision Memo (``docs/research/M1_SYSTEM_DECISION_MEMO_2026_09.md``)
found that 12-1 momentum is the only source-pool signal with out-of-sample
support, and that **the live price cache cannot compute it**: ``cache/prices``
carries a median 113 bars against the 253 the factor needs. A shadow M1 report
running on that cache would accumulate a forward ledger of a signal that was
never actually computed — worse than no report at all.

This module is the gate that makes that impossible. It answers one question,
offline, before any shadow report is allowed to form a list:

    *Can 12-1 momentum be computed today, for enough of the universe, on data
    that is fresh and not contaminated?*

Design commitments
------------------
* **The M1 definition is imported, never restated.** ``mom_12_1`` is computed
  with the same ``_lag_ratio`` the tournament used, at the same lookbacks, and
  the tradability floor and quarantine are the tournament's own. A guard that
  re-derived them could pass on a pool that is not M1.
* **Zero provider calls.** Nothing here reads credentials or reaches a network.
  Refreshing the cache is a separate, explicitly gated stage
  (``research/backtests/m1_price_refresh.py``).
* **Counts, not names, and never an ordering.** The guard reports pool *size*
  and coverage. It does not emit the pool's members, a score, or a rank — per
  the memo's §5, any ordering inside M1 is a claim the evidence contradicts.
* **Its own status ladder.** :class:`M1DataStatus` is deliberately disjoint
  from the live evidence ladder, ``ReplayVerdict``, ``TournamentVerdict`` and
  ``ConvictionVerdict``. ``VALIDATED_EDGE`` cannot be emitted from here, and
  a data-integrity status is not evidence about a signal.

Writes ONLY:
    cache/research/m1_data_guard_latest.json
    logs/m1_data_guard_latest.txt

Reads only ``cache/replay_prices/``, ``cache/replay_universe/`` and (optionally)
the refresh artifact. Touches nothing in ``cache/prices``, ``cache/prices_deep``,
``data/``, ``db/``, or any scanner, HC/EO, Alpha Focus, program, routing, MCP or
dashboard artifact.

Usage::

    GEM_TRADER_SKIP_DOTENV=true .venv/bin/python -m research.backtests.m1_data_guard audit
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from research.backtests.common import (
    LiveArtifactTripwire,
    ROOT,
    assert_provenance,
    offline_env,
    provenance_flags,
    run_cli,
    write_replay_json,
    write_replay_text,
)
from research.backtests.alpha_reconstruction_lab import (
    SPLIT_ARTIFACT_MAX_1D_RETURN,
    SPLIT_ARTIFACT_RANGE_RATIO,
    TEST_SYMBOLS,
    _lag_ratio,
    _series_integrity,
    load_quarantine_excludes,
)
from research.backtests.alpha_tournament import BASE_FLOOR, FAKE_LIQUIDITY_RATIO

offline_env()

# ── paths ───────────────────────────────────────────────────────────────────

PRICES_REL = "cache/replay_prices"
QUARANTINE_REL = "cache/replay_universe/replay_price_quarantine.json"
REFRESH_ARTIFACT_REL = "cache/research/m1_price_refresh_latest.json"
LATEST_REL = "cache/research/m1_data_guard_latest.json"
REPORT_TXT_REL = "logs/m1_data_guard_latest.txt"

# ── bar requirements, derived from the M1 definition ────────────────────────
#
# alpha_tournament.py:232-233 —
#     r252, r21 = _lag_ratio(c, 252), _lag_ratio(c, 21)
#     mom_12_1  = ((1 + r252/100) / (1 + r21/100) - 1) * 100
#
# _lag_ratio(x, k)[i] reads x[i] and x[i-k], so evaluating mom_12_1 at the last
# bar needs index -253 to exist: 252 sessions of lookback plus the bar itself.
# The 21-session leg is a subset of that span and adds no depth requirement of
# its own — it is the *excluded* recent month, not an extra lookback.
M1_LOOKBACK_SESSIONS = 252
M1_SKIP_SESSIONS = 21

#: Positional floor — below this ``mom_12_1`` is NaN by construction.
M1_MIN_BARS_STRICT = M1_LOOKBACK_SESSIONS + 1  # 253

#: Operating floor. The strict minimum plus a one-month buffer, so a series
#: that is missing scattered bars still spans a genuine twelve months rather
#: than reaching back through its own gaps. Requirement: "at least 252 trading
#: days plus buffer for the excluded recent month."
M1_MIN_BARS_REQUIRED = M1_LOOKBACK_SESSIONS + M1_SKIP_SESSIONS  # 273

# Non-trading days and missing bars, handled explicitly. Depth alone is not
# enough: 253 bars spread over three years is not a twelve-month lookback. The
# bar at position -253 must sit a plausible twelve months back in CALENDAR
# time. 252 US sessions ≈ 366 calendar days; the band below tolerates roughly
# 10% missing bars in either direction before the span is called broken.
M1_SPAN_MIN_CALENDAR_DAYS = 330
M1_SPAN_MAX_CALENDAR_DAYS = 430

# ── coverage thresholds for a valid run ─────────────────────────────────────
#
# Calibrated against the replay the memo rests on: a median 3,077 investable
# names per week, of which M1 (top quintile) held a median 597. A run that
# cannot approximately reproduce that shape is not running M1.

#: Minimum share of the floor-passing candidate universe with computable 12-1.
MIN_COVERAGE_PCT = 80.0

#: Minimum names carrying computable 12-1 — the set the top quintile is drawn
#: from. Below this the universe is not comparable to the replay's 3,077/week
#: and the quintile is a different object.
#:
#: Named "pool input" rather than "scored": nothing here scores a name, and a
#: field called `scored_names` would imply a per-name score exists. Per the M1
#: decision memo §5, it must not.
MIN_POOL_INPUT_NAMES = 2000

#: Share of the active candidate universe allowed to sit below the 12-1 bar
#: floor. This is the check that fires on the live cache: ``cache/prices`` has
#: a median 113 bars, so essentially 100% of it is shallow.
MAX_SHALLOW_PCT = 20.0

#: Minimum M1 pool size. The memo's product is a wide 50-100 name equal-weight
#: list; a quintile that cannot comfortably fill one is not a pool.
MIN_POOL_NAMES = 300

#: A symbol whose last bar is older than this is treated as no longer trading
#: (delisted, halted, or renamed) and drops OUT of the candidate universe — it
#: is not a freshness failure. The replay cache deliberately carries delisted
#: names to their final bar (1,875 of 7,474 series as of 2026-09-06), and every
#: one of them passes a tradability floor evaluated at the bar it died on.
#: Counting those as "stale" would block every run forever.
#:
#: This is a calendar heuristic, used ONLY to answer "is this symbol still
#: trading". It is deliberately NOT the freshness rule: freshness is decided
#: against the required completed session from core/market_session.py, per the
#: 2026-07-10 pipeline review (P0), which found calendar-age thresholds
#: silently accept bars one completed session behind the benchmark.
INACTIVE_AFTER_CALENDAR_DAYS = 14  # ~10 trading sessions

#: Share of the ACTIVE candidate universe allowed to be behind the required
#: session. Inactive (delisted) names are excluded before this is computed.
MAX_STALE_PCT = 10.0

#: Whole-cache lag ceiling, in completed sessions. This is the 2026-07-02
#: failure mode — everything frozen together while a benchmark stays current.
MAX_CACHE_LAG_SESSIONS = 2

#: Split/price contamination allowed in the active post-quarantine universe.
#:
#: This is a REGRESSION TRIPWIRE, not an absolute quality bar, and the number
#: is empirical rather than principled: measured 2026-09-06, 52 of 3,363 active
#: candidates (1.55%) trip the reconstruction lab's split-artifact rules on top
#: of the backfill's own 296-name quarantine. That standing rate
#: is a property of a cache that deliberately keeps dying microcaps, not a new
#: problem. The ceiling sits above it with margin so the check fires when a
#: refresh writes bad bars — which is the failure it exists to catch.
#:
#: Contaminated names are ALWAYS excluded from the pool regardless of this
#: threshold; the threshold only decides whether the run is refused outright.
CONTAMINATION_BASELINE_PCT = 1.55  # observed 2026-09-06, 52/3,363
MAX_NEW_CONTAMINATION_PCT = 2.5

#: Any historical rewrite is a refusal. A provider re-adjusting history behind
#: us silently changes what four completed studies measured.
MAX_HISTORY_DRIFT = 0

NOTE = (
    "M1 DATA GUARD — RESEARCH ONLY. A data-integrity gate for a shadow research "
    "report, not evidence about any signal. NOT live forward evidence, NOT "
    "backtest evidence for Phase 4B, and NOT a gate/threshold/score/routing "
    "recommendation. Emits no signal, no ranking and no ticker ordering. Must "
    "never be pooled with the live forward ledger, program verdicts, HC/EO "
    "routing, Alpha Focus, MCP or the dashboard."
)


class M1DataStatus(str, Enum):
    """Data-readiness ladder. Deliberately disjoint from every live ladder.

    This describes the *data*, never the signal. ``M1_DATA_READY`` says 12-1
    can be computed faithfully today — it says nothing whatsoever about whether
    12-1 works, which is a separate hypothesis with its own forward evidence.
    """

    #: 12-1 is computable for enough of a fresh, uncontaminated universe.
    M1_DATA_READY = "M1_DATA_READY"
    #: Runnable, but something is imperfect — the caveats must travel with any
    #: artifact produced from this data.
    M1_DATA_DEGRADED = "M1_DATA_DEGRADED"
    #: At least one refusal condition fired. The shadow report must not run.
    M1_DATA_BLOCKED = "M1_DATA_BLOCKED"


class M1RefusalReason(str, Enum):
    """Why the shadow report must not run. Ordered by severity."""

    DEEP_CACHE_MISSING = "DEEP_CACHE_MISSING"
    HISTORY_DRIFT_DETECTED = "HISTORY_DRIFT_DETECTED"
    CONTAMINATION_ABOVE_THRESHOLD = "CONTAMINATION_ABOVE_THRESHOLD"
    CACHE_STALE = "CACHE_STALE"
    STALE_CANDIDATES_ABOVE_THRESHOLD = "STALE_CANDIDATES_ABOVE_THRESHOLD"
    SHALLOW_HISTORY = "SHALLOW_HISTORY"
    UNIVERSE_COVERAGE_BELOW_THRESHOLD = "UNIVERSE_COVERAGE_BELOW_THRESHOLD"
    INSUFFICIENT_POOL_INPUT = "INSUFFICIENT_POOL_INPUT"
    POOL_TOO_SMALL = "POOL_TOO_SMALL"


#: Refusals severe enough to block. Everything else degrades.
BLOCKING_REASONS: frozenset[M1RefusalReason] = frozenset(M1RefusalReason)


def assert_m1_data_status(status: str) -> str:
    """Reject any status outside this module's ladder."""
    allowed = {s.value for s in M1DataStatus}
    if status not in allowed:
        raise ValueError(
            f"{status!r} is not an M1 data status. Allowed: {sorted(allowed)}. "
            "This ladder is disjoint from the live evidence ladder by design."
        )
    return status


# ── per-ticker depth ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TickerDepth:
    """Everything the guard knows about one cached series. No forward data."""

    ticker: str
    bars: int
    first_bar: str | None
    last_bar: str | None
    price: float | None
    dvol_med_20: float | None
    span_252_calendar_days: int | None
    mom_12_1: float | None
    readable: bool = True
    contaminated: bool = False
    contamination_reason: str | None = None

    @property
    def depth_ok(self) -> bool:
        return self.bars >= M1_MIN_BARS_REQUIRED

    @property
    def span_ok(self) -> bool:
        d = self.span_252_calendar_days
        return d is not None and M1_SPAN_MIN_CALENDAR_DAYS <= d <= M1_SPAN_MAX_CALENDAR_DAYS

    @property
    def passes_liquidity_floor(self) -> bool:
        """The tournament's price and dollar-volume floor, at the last bar.

        Deliberately WITHOUT ``BASE_FLOOR["min_bars"]`` (120). That floor asks
        "is there enough data to compute anything"; this guard asks the much
        stricter "is there enough data to compute 12-1", and depth is its own
        business. Folding 120 bars in here would silently drop every shallow
        name out of the candidate universe — hiding the exact failure the
        guard exists to report, and turning a 113-bar cache into an empty
        universe rather than a loud SHALLOW_HISTORY refusal.
        """
        return (
            self.price is not None
            and self.dvol_med_20 is not None
            and self.price >= BASE_FLOOR["min_price"]
            and self.dvol_med_20 >= BASE_FLOOR["min_dvol_med"]
        )

    @property
    def passes_floor(self) -> bool:
        """The tournament's full tradability floor, including its bar minimum."""
        return self.passes_liquidity_floor and self.bars >= BASE_FLOOR["min_bars"]

    @property
    def has_m1(self) -> bool:
        """12-1 is computable AND trustworthy for this series."""
        import math

        return bool(
            self.readable
            and not self.contaminated
            and self.depth_ok
            and self.span_ok
            and self.mom_12_1 is not None
            and math.isfinite(self.mom_12_1)
        )

    def is_stale(self, required_session: date) -> bool:
        """Behind the required completed session. Session-based, not calendar."""
        if not self.last_bar:
            return True
        return date.fromisoformat(self.last_bar) < required_session

    def is_active(self, required_session: date) -> bool:
        """Still trading, as opposed to delisted and carried to a final bar."""
        if not self.last_bar:
            return False
        age = (required_session - date.fromisoformat(self.last_bar)).days
        return age <= INACTIVE_AFTER_CALENDAR_DAYS


def _median(xs: Sequence[float]) -> float | None:
    s = sorted(x for x in xs if x is not None)
    if not s:
        return None
    n = len(s)
    return float(s[n // 2]) if n % 2 else float((s[n // 2 - 1] + s[n // 2]) / 2.0)


def measure_series(ticker: str, df) -> TickerDepth:
    """Depth, freshness, floor and 12-1 for one price frame. Pure."""
    import math

    import numpy as np
    import pandas as pd

    if df is None or len(df) == 0 or "close" not in df.columns:
        return TickerDepth(ticker, 0, None, None, None, None, None, None, readable=False)

    df = df.sort_index()
    close = np.asarray(df["close"], dtype=np.float64)
    n = int(close.size)
    idx = pd.to_datetime(df.index)
    first_bar = str(idx[0].date())
    last_bar = str(idx[-1].date())

    integ = _series_integrity(close)
    contaminated, reason = False, None
    if ticker in TEST_SYMBOLS:
        contaminated, reason = True, "exchange_test_symbol"
    elif not integ["usable"]:
        contaminated, reason = True, "unusable_series"
    elif integ["max_1d"] is not None and integ["max_1d"] >= SPLIT_ARTIFACT_MAX_1D_RETURN:
        contaminated, reason = True, "split_artifact_single_day"
    elif integ["range_ratio"] is not None and integ["range_ratio"] >= SPLIT_ARTIFACT_RANGE_RATIO:
        contaminated, reason = True, "split_artifact_range_ratio"

    price = float(close[-1]) if n and math.isfinite(close[-1]) else None

    dvol = None
    if "volume" in df.columns and n:
        vol = np.asarray(df["volume"], dtype=np.float64)
        w = min(20, n)
        dv = close[-w:] * vol[-w:]
        dv = dv[np.isfinite(dv)]
        if dv.size:
            dvol = float(np.median(dv))

    # Calendar span of the 252-session lookback: the bar mom_12_1 reaches back
    # to must actually be about twelve months old, not merely 253 rows away.
    span = None
    if n >= M1_MIN_BARS_STRICT:
        span = int((idx[-1] - idx[-M1_MIN_BARS_STRICT]).days)

    mom = None
    if n >= M1_MIN_BARS_STRICT:
        r252 = _lag_ratio(close, M1_LOOKBACK_SESSIONS)[-1]
        r21 = _lag_ratio(close, M1_SKIP_SESSIONS)[-1]
        if math.isfinite(r252) and math.isfinite(r21) and (1 + r21 / 100.0) != 0:
            v = ((1 + r252 / 100.0) / (1 + r21 / 100.0) - 1.0) * 100.0
            mom = float(v) if math.isfinite(v) else None

    return TickerDepth(
        ticker=ticker,
        bars=n,
        first_bar=first_bar,
        last_bar=last_bar,
        price=price,
        dvol_med_20=dvol,
        span_252_calendar_days=span,
        mom_12_1=mom,
        readable=True,
        contaminated=contaminated,
        contamination_reason=reason,
    )


# ── audit ───────────────────────────────────────────────────────────────────


@dataclass
class M1DataAudit:
    """The full audit. ``header`` is the data-integrity header (requirement 4)."""

    status: M1DataStatus
    refusals: list[M1RefusalReason] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    header: dict[str, Any] = field(default_factory=dict)
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def may_run(self) -> bool:
        """Whether a shadow M1 report is permitted to form a list right now.

        Refusal is driven by :class:`M1RefusalReason` alone — the specific
        conditions the guard exists to catch — never by the presence of a
        warning. ``M1_DATA_DEGRADED`` is runnable *with the caveats attached*:
        a cache this size will almost always have a gappy series or an
        unreadable file in it, and a guard that blocked on that would be
        switched off within a week, which is the real failure mode.

        An operator who wants a clean bill of health asks for one explicitly
        via ``require_m1_data_ready(..., require_clean=True)``.
        """
        return not self.refusals


def _required_session(now: datetime | None = None) -> date:
    from core.market_session import required_market_session

    return required_market_session(now) if now is not None else required_market_session()


def _sessions_between(a: date, b: date) -> int:
    """Completed US trading sessions in (a, b]. Small ranges only."""
    from core.session import _is_trading_day

    if b <= a:
        return 0
    from datetime import timedelta

    n, cur = 0, a
    while cur < b and n < 400:
        cur += timedelta(days=1)
        if _is_trading_day(cur):
            n += 1
    return n


def load_refresh_outcome(root: Path) -> dict[str, Any]:
    """Failed/missing counts from the last refresh, if one has run.

    Absent artifact is not an error — it degrades to ``unavailable`` counts so
    the header never asserts a zero it cannot support.
    """
    p = root / REFRESH_ARTIFACT_REL
    if not p.exists():
        return {"source": "unavailable_no_refresh_artifact",
                "failed_refresh_count": None, "missing_ticker_count": None,
                "history_drift_count": None, "generated_at": None}
    try:
        d = json.loads(p.read_text())
    except Exception:
        return {"source": "unreadable_refresh_artifact",
                "failed_refresh_count": None, "missing_ticker_count": None,
                "history_drift_count": None, "generated_at": None}
    return {
        "source": "m1_price_refresh_latest",
        "failed_refresh_count": d.get("failed_refresh_count"),
        "missing_ticker_count": d.get("missing_ticker_count"),
        "history_drift_count": d.get("history_drift_count"),
        "generated_at": d.get("generated_at"),
    }


def scan_cache(root: Path, *, limit: int | None = None,
               exclude: Iterable[str] | None = None) -> list[TickerDepth]:
    """Measure every usable series in the deep cache. Zero provider calls."""
    import pandas as pd

    excl = set(exclude or ())
    out: list[TickerDepth] = []
    files = sorted((root / PRICES_REL).glob("*.parquet"))
    if limit:
        files = files[:limit]
    for f in files:
        sym = f.stem
        if sym in excl:
            continue
        try:
            df = pd.read_parquet(f, columns=["close", "volume"])
        except Exception:
            try:
                df = pd.read_parquet(f)
            except Exception:
                out.append(TickerDepth(sym, 0, None, None, None, None, None, None,
                                       readable=False))
                continue
        out.append(measure_series(sym, df))
    return out


def evaluate(depths: Sequence[TickerDepth], *, required_session: date,
             refresh: Mapping[str, Any] | None = None,
             cache_present: bool = True,
             quarantined: int = 0) -> M1DataAudit:
    """Turn per-ticker measurements into a status, refusals and a header.

    Pure: no I/O, so the thresholds can be tested directly against synthetic
    universes without a cache on disk.
    """
    refresh = dict(refresh or {})
    refusals: list[M1RefusalReason] = []
    warnings: list[str] = []

    if not cache_present or not depths:
        header = _empty_header(required_session, refresh, cache_present)
        return M1DataAudit(
            status=M1DataStatus.M1_DATA_BLOCKED,
            refusals=[M1RefusalReason.DEEP_CACHE_MISSING],
            warnings=["cache/replay_prices is missing or empty — nothing to audit"],
            header=header,
            detail={"cache_present": cache_present, "series_seen": len(depths)},
        )

    readable = [d for d in depths if d.readable]
    unreadable = [d for d in depths if not d.readable]

    # The candidate universe is the tournament's own tradability floor,
    # evaluated at the last bar and then restricted to names that are still
    # trading. The second step matters: the replay cache carries delisted names
    # to their final bar by design, and each of them passed a floor on the day
    # it stopped trading. They are not candidates and they are not stale — they
    # are gone, and they are counted separately.
    floor_passing = [d for d in readable if d.passes_liquidity_floor]
    candidates = [d for d in floor_passing if d.is_active(required_session)]
    inactive = [d for d in floor_passing if not d.is_active(required_session)]
    n_cand = len(candidates)

    contaminated = [d for d in candidates if d.contaminated]
    contamination_pct = 100.0 * len(contaminated) / n_cand if n_cand else 0.0

    stale = [d for d in candidates if d.is_stale(required_session)]
    stale_pct = 100.0 * len(stale) / n_cand if n_cand else 0.0

    with_m1 = [d for d in candidates if d.has_m1]
    fresh_with_m1 = [d for d in with_m1 if not d.is_stale(required_session)]
    coverage_pct = 100.0 * len(with_m1) / n_cand if n_cand else 0.0

    shallow = [d for d in candidates if not d.depth_ok]
    shallow_pct = 100.0 * len(shallow) / n_cand if n_cand else 0.0
    span_broken = [d for d in candidates if d.depth_ok and not d.span_ok]

    n_pool_input = len(fresh_with_m1)
    pool_size = int(n_pool_input * 0.20)  # within-date top quintile of mom_12_1

    newest = max((d.last_bar for d in readable if d.last_bar), default=None)
    # (max over all readable series: a delisted name cannot be NEWER than a
    # live one, so this is the true cache frontier.)
    cache_lag = (
        _sessions_between(date.fromisoformat(newest), required_session)
        if newest else 999
    )

    drift = refresh.get("history_drift_count")

    # ── refusal checks, most severe first ───────────────────────────────────
    if drift is not None and drift > MAX_HISTORY_DRIFT:
        refusals.append(M1RefusalReason.HISTORY_DRIFT_DETECTED)
    if contamination_pct > MAX_NEW_CONTAMINATION_PCT:
        refusals.append(M1RefusalReason.CONTAMINATION_ABOVE_THRESHOLD)
    if cache_lag > MAX_CACHE_LAG_SESSIONS:
        refusals.append(M1RefusalReason.CACHE_STALE)
    if stale_pct > MAX_STALE_PCT:
        refusals.append(M1RefusalReason.STALE_CANDIDATES_ABOVE_THRESHOLD)
    if shallow_pct > MAX_SHALLOW_PCT:
        refusals.append(M1RefusalReason.SHALLOW_HISTORY)
    if coverage_pct < MIN_COVERAGE_PCT:
        refusals.append(M1RefusalReason.UNIVERSE_COVERAGE_BELOW_THRESHOLD)
    if n_pool_input < MIN_POOL_INPUT_NAMES:
        refusals.append(M1RefusalReason.INSUFFICIENT_POOL_INPUT)
    if pool_size < MIN_POOL_NAMES:
        refusals.append(M1RefusalReason.POOL_TOO_SMALL)

    if 0 < cache_lag <= MAX_CACHE_LAG_SESSIONS:
        warnings.append(
            f"cache newest bar {newest} is {cache_lag} session(s) behind the "
            f"required session {required_session} — inside tolerance, not fresh")
    if span_broken:
        warnings.append(
            f"{len(span_broken)} candidates have >= {M1_MIN_BARS_REQUIRED} bars but a "
            "252-session span outside the twelve-month calendar band — gappy series, "
            "excluded from 12-1 coverage")
    if unreadable:
        warnings.append(f"{len(unreadable)} parquet(s) unreadable and skipped")
    if refresh.get("failed_refresh_count"):
        warnings.append(
            f"last refresh left {refresh['failed_refresh_count']} ticker(s) failed")

    status = (
        M1DataStatus.M1_DATA_BLOCKED if refusals
        else M1DataStatus.M1_DATA_DEGRADED if warnings
        else M1DataStatus.M1_DATA_READY
    )

    header = {
        "universe_size": n_cand,
        "names_with_enough_bars": len(with_m1),
        "coverage_pct": round(coverage_pct, 2),
        "median_bars": _median([float(d.bars) for d in candidates]),
        "stale_count": len(stale),
        "failed_refresh_count": refresh.get("failed_refresh_count"),
        "missing_ticker_count": refresh.get("missing_ticker_count"),
        "refusal_reason": refusals[0].value if refusals else None,
        # context the eight required fields are meaningless without
        "status": status.value,
        "may_run": not refusals,
        "all_refusal_reasons": [r.value for r in refusals],
        "series_scanned": len(depths),
        "floor_passing_total": len(floor_passing),
        "inactive_excluded": len(inactive),
        "quarantined_excluded": quarantined,
        "pool_input_names": n_pool_input,
        "m1_pool_size_if_run": pool_size,
        "stale_pct": round(stale_pct, 2),
        "shallow_count": len(shallow),
        "shallow_pct": round(shallow_pct, 2),
        "span_broken_count": len(span_broken),
        "contaminated_count": len(contaminated),
        "contamination_pct": round(contamination_pct, 3),
        "history_drift_count": drift,
        "unreadable_count": len(unreadable),
        "cache_newest_bar": newest,
        "required_session": required_session.isoformat(),
        "cache_lag_sessions": cache_lag,
        "refresh_source": refresh.get("source"),
        "refresh_generated_at": refresh.get("generated_at"),
    }

    detail = {
        "thresholds": thresholds_block(),
        "median_bars_all_series": _median([float(d.bars) for d in readable]),
        "median_bars_with_m1": _median([float(d.bars) for d in with_m1]),
        "shallow_sample": sorted(d.ticker for d in shallow)[:25],
        "stale_sample": sorted(d.ticker for d in stale)[:25],
        "contaminated_sample": sorted(
            f"{d.ticker}:{d.contamination_reason}" for d in contaminated)[:25],
        "span_broken_sample": sorted(d.ticker for d in span_broken)[:25],
        "note_no_ordering": (
            "Pool SIZE only. The guard never emits pool members and never "
            "orders them: per the M1 decision memo, any ordering inside M1 is "
            "a claim the evidence contradicts."),
    }
    return M1DataAudit(status=status, refusals=refusals, warnings=warnings,
                       header=header, detail=detail)


def _empty_header(required_session: date, refresh: Mapping[str, Any],
                  cache_present: bool) -> dict[str, Any]:
    return {
        "universe_size": 0,
        "names_with_enough_bars": 0,
        "coverage_pct": 0.0,
        "median_bars": None,
        "stale_count": 0,
        "failed_refresh_count": refresh.get("failed_refresh_count"),
        "missing_ticker_count": refresh.get("missing_ticker_count"),
        "refusal_reason": M1RefusalReason.DEEP_CACHE_MISSING.value,
        "status": M1DataStatus.M1_DATA_BLOCKED.value,
        "may_run": False,
        "all_refusal_reasons": [M1RefusalReason.DEEP_CACHE_MISSING.value],
        "series_scanned": 0,
        "cache_present": cache_present,
        "required_session": required_session.isoformat(),
        "refresh_source": refresh.get("source"),
    }


def thresholds_block() -> dict[str, Any]:
    """Every threshold, published so a reader never has to infer one."""
    return {
        "m1_lookback_sessions": M1_LOOKBACK_SESSIONS,
        "m1_skip_sessions": M1_SKIP_SESSIONS,
        "min_bars_strict": M1_MIN_BARS_STRICT,
        "min_bars_required": M1_MIN_BARS_REQUIRED,
        "span_calendar_band_days": [M1_SPAN_MIN_CALENDAR_DAYS, M1_SPAN_MAX_CALENDAR_DAYS],
        "min_coverage_pct": MIN_COVERAGE_PCT,
        "min_pool_input_names": MIN_POOL_INPUT_NAMES,
        "max_shallow_pct": MAX_SHALLOW_PCT,
        "min_pool_names": MIN_POOL_NAMES,
        "inactive_after_calendar_days": INACTIVE_AFTER_CALENDAR_DAYS,
        "max_stale_pct": MAX_STALE_PCT,
        "max_cache_lag_sessions": MAX_CACHE_LAG_SESSIONS,
        "contamination_baseline_pct": CONTAMINATION_BASELINE_PCT,
        "max_new_contamination_pct": MAX_NEW_CONTAMINATION_PCT,
        "max_history_drift": MAX_HISTORY_DRIFT,
        "tradability_floor": dict(BASE_FLOOR),
        "fake_liquidity_ratio": FAKE_LIQUIDITY_RATIO,
    }


def run_audit(root: Path, *, limit: int | None = None,
              now: datetime | None = None) -> M1DataAudit:
    """Scan the deep cache and evaluate it. Zero provider calls."""
    prices = root / PRICES_REL
    required = _required_session(now)
    refresh = load_refresh_outcome(root)
    if not prices.is_dir():
        return evaluate([], required_session=required, refresh=refresh,
                        cache_present=False)
    excl = load_quarantine_excludes(root)
    depths = scan_cache(root, limit=limit, exclude=excl)
    return evaluate(depths, required_session=required, refresh=refresh,
                    cache_present=True, quarantined=len(excl))


# ── the gate the shadow report calls ────────────────────────────────────────


class M1DataRefusal(RuntimeError):
    """Raised when a shadow M1 report is not permitted to run."""


def require_m1_data_ready(root: Path | str | None = None, *,
                          now: datetime | None = None,
                          require_clean: bool = False,
                          audit: M1DataAudit | None = None) -> M1DataAudit:
    """Gate entry point. Raise unless the data can support 12-1 today.

    The future shadow report calls this FIRST, before it builds anything, and
    attaches the returned ``audit.header`` to everything it writes::

        audit = require_m1_data_ready(ROOT)
        ...
        payload["m1_data_integrity"] = audit.header

    Pass ``require_clean=True`` to reject ``M1_DATA_DEGRADED`` as well — for a
    caller that would rather stop than publish a caveat. Pass ``audit=`` to
    re-gate an audit already computed, without re-scanning the cache.
    """
    audit = audit if audit is not None else run_audit(Path(root or ROOT), now=now)
    if audit.may_run and not (require_clean
                              and audit.status is M1DataStatus.M1_DATA_DEGRADED):
        return audit
    raise M1DataRefusal(
        f"M1 shadow report refused — {audit.status.value}: "
        + (", ".join(r.value for r in audit.refusals)
           if audit.refusals else "require_clean=True and status is DEGRADED: "
           + "; ".join(audit.warnings))
        + f" | coverage {audit.header.get('coverage_pct')}% of "
        f"{audit.header.get('universe_size')} candidates, "
        f"{audit.header.get('names_with_enough_bars')} with 12-1, "
        f"median {audit.header.get('median_bars')} bars"
    )


# ── report ──────────────────────────────────────────────────────────────────


def render_report(audit: M1DataAudit) -> str:
    h, d = audit.header, audit.detail
    L: list[str] = []
    A = L.append
    A("=" * 78)
    A("M1 DATA GUARD — price-depth and staleness audit")
    A("=" * 78)
    A(NOTE)
    A("")
    A(f"STATUS: {h['status']}    may_run={h['may_run']}")
    if audit.refusals:
        A("")
        A("REFUSED — the M1 shadow report must not run:")
        for r in audit.refusals:
            A(f"  !! {r.value}")
    A("")
    A("DATA-INTEGRITY HEADER")
    A("-" * 78)
    for k in ("universe_size", "names_with_enough_bars", "coverage_pct",
              "median_bars", "stale_count", "failed_refresh_count",
              "missing_ticker_count", "refusal_reason"):
        A(f"  {k:<26} {h.get(k)}")
    A("")
    A("CONTEXT")
    A("-" * 78)
    for k in ("series_scanned", "floor_passing_total", "inactive_excluded",
              "quarantined_excluded", "pool_input_names",
              "m1_pool_size_if_run", "stale_pct", "shallow_count", "shallow_pct",
              "span_broken_count", "contaminated_count", "contamination_pct",
              "history_drift_count", "unreadable_count", "cache_newest_bar",
              "required_session", "cache_lag_sessions", "refresh_source"):
        A(f"  {k:<26} {h.get(k)}")
    if audit.warnings:
        A("")
        A("WARNINGS")
        A("-" * 78)
        for w in audit.warnings:
            A(f"  ~ {w}")
    A("")
    A("THRESHOLDS")
    A("-" * 78)
    for k, v in (d.get("thresholds") or {}).items():
        A(f"  {k:<26} {v}")
    A("")
    A("DIAGNOSTIC SAMPLES (refusal diagnostics — not a candidate list)")
    A("-" * 78)
    for k in ("shallow_sample", "stale_sample", "contaminated_sample",
              "span_broken_sample"):
        v = d.get(k) or []
        A(f"  {k:<26} {len(v)} shown: {', '.join(map(str, v[:12]))}")
    A("")
    A("12-1 REQUIREMENT")
    A("-" * 78)
    A(f"  mom_12_1 = ((1+r252)/(1+r21)-1)*100, imported from alpha_tournament.")
    A(f"  Needs index -{M1_MIN_BARS_STRICT} to exist: {M1_LOOKBACK_SESSIONS} sessions of")
    A(f"  lookback plus the bar itself. Operating floor {M1_MIN_BARS_REQUIRED} bars adds a")
    A(f"  {M1_SKIP_SESSIONS}-session buffer for missing bars, and the 252-session span must")
    A(f"  fall inside {M1_SPAN_MIN_CALENDAR_DAYS}-{M1_SPAN_MAX_CALENDAR_DAYS} calendar days.")
    A("")
    A("This guard reports POOL SIZE only — never members, scores or an ordering.")
    A("It says whether 12-1 can be COMPUTED, never whether 12-1 WORKS.")
    A("=" * 78)
    return "\n".join(L)


def audit_stage(args) -> int:
    root = Path(args.root)
    t0 = time.time()
    tripwire = LiveArtifactTripwire.snapshot(root)
    audit = run_audit(root, limit=args.limit)

    payload: dict[str, Any] = {
        "kind": "M1_DATA_GUARD",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": audit.status.value,
        "may_run": audit.may_run,
        "refusal_reasons": [r.value for r in audit.refusals],
        "warnings": audit.warnings,
        "integrity_header": audit.header,
        "detail": audit.detail,
        "cache_namespace": {
            "reads_prices": PRICES_REL,
            "reads_quarantine": QUARANTINE_REL,
            "reads_refresh": REFRESH_ARTIFACT_REL,
            "writes": [LATEST_REL, REPORT_TXT_REL],
        },
        "provider_calls": 0,
        "emits_signal": False,
        "emits_ranking": False,
        "note": NOTE,
        **provenance_flags(),
    }
    assert_m1_data_status(payload["status"])
    assert_provenance(payload)
    write_replay_json(root / LATEST_REL, payload, root=root)

    report = render_report(audit)
    write_replay_text(root / REPORT_TXT_REL, report, root=root)
    print(report)
    print(f"\n{tripwire.report()}")
    print(f"elapsed {time.time() - t0:.1f}s · provider calls: 0")
    tripwire.assert_clean()
    return 0 if audit.may_run else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("stage", nargs="?", default="audit", choices=["audit"])
    p.add_argument("--root", default=str(ROOT))
    p.add_argument("--limit", type=int, default=None,
                   help="Audit only the first N series (debugging).")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_cli(audit_stage, args)


if __name__ == "__main__":
    raise SystemExit(main())

"""Alpha Reconstruction Lab — what did the winners look like *before* they won?

RESEARCH ONLY. This module studies the 2022-2025 replay window to answer one
question: out of the thousands of names tradable on any given week, is there a
describable *pre-move* fingerprint that concentrates the eventual big winners
without walking into the extension trap the old scanner walked into?

It changes nothing. It reads ``cache/replay_prices`` / ``cache/replay_market_cap``
/ ``cache/replay_fundamentals`` and the two existing replay episode panels, and
writes only under ``cache/research/alpha_reconstruction_*`` and
``logs/alpha_reconstruction_*``. No production scanner, score, gate, threshold,
HC/EO rule, routing decision, dashboard artifact, ledger, or program verdict is
read for control purposes only or written at all. It emits no trade signal, no
ticker recommendation, and — by construction, via
:func:`~research.backtests.common.assert_fingerprint_verdict` — no live verdict
token.

Why it exists: ``docs/research/HISTORICAL_REPLAY_RESULTS_2026_09.md`` found the
broad price-only scanner had *negative* selection against its own universe at
every horizon (REPLAY_CONTRADICTS), driven by an RS score that saturates at 100
exactly where forward returns are worst. The response is not to guess new
thresholds for that scanner. It is to start from the outcomes — find the names
that actually won — and ask what was visible about them beforehand.

Sub-stages::

    panel        build the as-of feature + forward-return panel   (zero API)
    winners      Part 1: define and describe the winner universe  (zero API)
    autopsy      Parts 2-3: pre-move features, winners vs controls(zero API)
    fingerprints Parts 4-6: evaluate fingerprints, train/holdout  (zero API)
    report       Part 8: the written report                       (zero API)
    all          every stage in order                             (zero API)

Discipline (Part 6), enforced in code rather than by author intent:

* Every explanatory feature is computed from bars at or before the scan date.
  Forward returns define labels only. :func:`_assert_no_lookahead` re-derives a
  sample of features from a truncated series and fails the stage on mismatch.
* Discovery — every ranking, cut, and comparison in the ``autopsy`` stage —
  runs on 2022-2024 only. 2025 is not read until the ``fingerprints`` stage
  evaluates the already-fixed rules on it exactly once.
* Fingerprints are declared as literal rules in :data:`FINGERPRINTS` before
  they are scored. None of them has a tuned threshold: thresholds are round
  numbers chosen from the hypothesis, not fitted to the outcome.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from research.backtests.common import (
    BENCHMARKS,
    LiveArtifactTripwire,
    REPLAY_WINDOW_END,
    REPLAY_WINDOW_START,
    ROOT,
    assert_fingerprint_verdict,
    assert_provenance,
    offline_env,
    provenance_flags,
    run_cli,
    write_replay_json,
    write_replay_text,
)

offline_env()  # before anything can import core.config

# ── artifact paths (all inside the lab's own namespace) ─────────────────────

INTERMEDIATES_REL = "cache/research/alpha_reconstruction_intermediates"
PANEL_REL = f"{INTERMEDIATES_REL}/panel.parquet"
WINNERS_REL = "cache/research/alpha_reconstruction_winner_universe.json"
AUTOPSY_REL = "cache/research/alpha_reconstruction_winner_autopsy.json"
FINGERPRINTS_REL = "cache/research/alpha_reconstruction_fingerprints.json"
LATEST_REL = "cache/research/alpha_reconstruction_latest.json"
REPORT_TXT_REL = "logs/alpha_reconstruction_latest.txt"

# ── window, horizons, splits ────────────────────────────────────────────────

# Point-in-time forward horizons, in trading days. 90/126 are reachable for the
# whole window because the replay price cache runs past the last scan date by
# more than 126 sessions; the panel still labels maturity per row rather than
# assuming it.
HORIZONS: tuple[int, ...] = (5, 10, 20, 30, 45, 60, 90, 126)

# Path-based ("gained X% within N days") labels — max close inside the window,
# which is a different and easier question than the point return at N.
MFE_LABELS: tuple[tuple[str, float, int], ...] = (
    ("up25_20d", 25.0, 20),
    ("up50_45d", 50.0, 45),
    ("up80_60d", 80.0, 60),
    ("up100_126d", 100.0, 126),
)

TRAIN_YEARS: tuple[int, ...] = (2022, 2023, 2024)
HOLDOUT_YEARS: tuple[int, ...] = (2025,)

# Regime windows are declared from market history, not fitted: 2022 is the bear
# market, 2023-2024 the recovery, 2025 the holdout year.
REGIMES: dict[str, tuple[int, ...]] = {
    "bear_2022": (2022,),
    "recovery_2023_2024": (2023, 2024),
    "holdout_2025": (2025,),
}

# ── tradability floor ───────────────────────────────────────────────────────
# The winner set is meaningless without one: unfiltered, the top 1% of forward
# returns is dominated by sub-dollar names whose "return" is a bid-ask artifact
# no one could have captured. The primary floor is deliberately loose enough to
# keep genuine small caps (that is where winners live) and every alternative is
# reported alongside it in the winners stage.
# min_dvol is applied to the MEDIAN 20-day dollar volume (see dvol_med_20).
PRIMARY_FLOOR = {"min_price": 2.0, "min_dvol": 1_000_000.0, "min_bars": 120}
FLOOR_VARIANTS = {
    "loose": {"min_price": 1.0, "min_dvol": 250_000.0, "min_bars": 120},
    "primary": PRIMARY_FLOOR,
    "strict": {"min_price": 5.0, "min_dvol": 5_000_000.0, "min_bars": 250},
}


# ── price cache ─────────────────────────────────────────────────────────────


# Populated by load_prices; published in the panel meta so every exclusion is
# auditable rather than silent.
QUARANTINED: dict[str, dict] = {}


def load_quarantine_excludes(root: Path) -> set[str]:
    """Series the price backfill flagged as unusable as-is.

    Both lists are excluded, not just symbol reuse: a sub-penny series produces
    enormous percentage "returns" that would otherwise dominate every winner
    cohort defined here.
    """
    q = root / "cache" / "replay_universe" / "replay_price_quarantine.json"
    if not q.exists():
        return set()
    d = json.loads(q.read_text())
    out: set[str] = set()
    for key in ("symbol_reuse_bars_past_delisting_over_30d", "near_zero_close_under_1c"):
        v = d.get(key)
        if isinstance(v, list):
            out |= set(v)
    return out


# Series-integrity quarantine. The replay's own quarantine caught sub-penny
# series and symbol reuse; it does not catch unadjusted reverse splits, which
# are endemic in dying microcaps and produce the single most damaging artifact
# for this study: fake winners. PPCB's series runs 37,500,000 -> 1.35 and
# reports forward returns of 4,999,900%; without a filter it and a handful of
# siblings own the entire "top winners" table.
#
# Two triggers, both physically implausible for a correctly adjusted series and
# neither of which fires on a real moonshot:
#   * a single session of +400% or more (5x in a day);
#   * a max/min close ratio of 1000x or more across the cached history.
# Verified against known real winners — NVDA (48x range, +24% best day), CVNA
# (129x, +56%), SMCI, APP, CELH, MSTR, SOUN, RKLB — all survive.
#
# Deliberately NOT a trigger: a single session of -85% or worse. Those are real
# events (a failed Phase 3 read-out), and removing them would quietly delete
# genuine disasters from the control pool and flatter every cohort measured
# against it.
SPLIT_ARTIFACT_MAX_1D_RETURN = 4.0     # +400% in one session
SPLIT_ARTIFACT_RANGE_RATIO = 1000.0    # max close / min close over the series

# Exchange test symbols. Caught by the rules above anyway; named so a reader of
# the quarantine list does not have to wonder what ZVZZT is.
TEST_SYMBOLS: frozenset[str] = frozenset({"ZVZZT", "ZWZZT", "ZXZZT", "ZJZZT", "ZBZZT"})


def _series_integrity(c) -> dict:
    """Integrity statistics for one close series."""
    import numpy as np

    good = c[np.isfinite(c) & (c > 0)]
    if good.size < 30:
        return {"max_1d": None, "range_ratio": None, "usable": False}
    r = good[1:] / good[:-1] - 1.0
    return {
        "max_1d": float(np.nanmax(r)),
        "min_1d": float(np.nanmin(r)),
        "range_ratio": float(good.max() / good.min()),
        "usable": True,
    }


def load_prices(root: Path, exclude: set[str]) -> dict:
    """Every usable replay series as plain numpy arrays."""
    import numpy as np
    import pandas as pd

    out: dict = {}
    t0 = time.time()
    for f in sorted((root / "cache" / "replay_prices").glob("*.parquet")):
        sym = f.stem
        if sym in exclude:
            continue
        try:
            df = pd.read_parquet(f)
        except Exception:
            continue
        if df is None or df.empty or len(df) < 60:
            continue
        df = df.sort_index()
        close = df["close"].to_numpy(np.float64)
        integ = _series_integrity(close)
        if sym in TEST_SYMBOLS or not integ["usable"] or (
            integ["max_1d"] >= SPLIT_ARTIFACT_MAX_1D_RETURN
            or integ["range_ratio"] >= SPLIT_ARTIFACT_RANGE_RATIO
        ):
            QUARANTINED[sym] = {
                "reason": "exchange_test_symbol" if sym in TEST_SYMBOLS
                else "unusable_series" if not integ["usable"]
                else "split_artifact_single_day" if integ["max_1d"] >= SPLIT_ARTIFACT_MAX_1D_RETURN
                else "split_artifact_range_ratio",
                **{k: (None if v is None else round(v, 4)) for k, v in integ.items()
                   if k != "usable"},
            }
            continue
        out[sym] = {
            "idx": df.index.values.astype("datetime64[ns]"),
            "open": df["open"].to_numpy(np.float64),
            "high": df["high"].to_numpy(np.float64),
            "low": df["low"].to_numpy(np.float64),
            "close": close,
            "volume": df["volume"].to_numpy(np.float64),
        }
    print(f"loaded {len(out):,} series in {time.time() - t0:.0f}s "
          f"({len(QUARANTINED):,} quarantined for series integrity)", flush=True)
    return out


def weekly_scan_dates(prices: dict, start: str, end: str):
    """Last trading session of each ISO week, from SPY's own calendar.

    Same definition as the price-only replay, so every cross-reference against
    its episode panel lines up date-for-date.
    """
    import pandas as pd

    spy_idx = pd.DatetimeIndex(prices["SPY"]["idx"])
    cal = spy_idx[(spy_idx >= start) & (spy_idx <= end)]
    weekly = (
        pd.Series(cal, index=cal)
        .groupby([cal.isocalendar().year, cal.isocalendar().week])
        .max()
    )
    return sorted(pd.DatetimeIndex(weekly.values)), spy_idx


# ── as-of feature engine ────────────────────────────────────────────────────
#
# Every feature below is a function of bars at or before the sample position.
# The engine computes each one as a full-series array (so the arithmetic is one
# vectorised pass) and then gathers the scan-date positions out of it — the
# gather is what makes it as-of, and _assert_no_lookahead re-checks it against
# a physically truncated series.


def _roll_mean(x, w: int):
    """Trailing mean over *w* bars, NaN until the window fills."""
    import numpy as np

    cs = np.concatenate(([0.0], np.cumsum(x)))
    out = np.full(x.size, np.nan)
    if x.size >= w:
        out[w - 1:] = (cs[w:] - cs[:-w]) / w
    return out


def _roll_std(x, w: int):
    """Trailing sample std over *w* bars, NaN until the window fills."""
    import numpy as np

    cs = np.concatenate(([0.0], np.cumsum(x)))
    cs2 = np.concatenate(([0.0], np.cumsum(x * x)))
    out = np.full(x.size, np.nan)
    if x.size >= w:
        s = cs[w:] - cs[:-w]
        s2 = cs2[w:] - cs2[:-w]
        var = (s2 - s * s / w) / max(w - 1, 1)
        out[w - 1:] = np.sqrt(np.maximum(var, 0.0))
    return out


def _roll_median(x, w: int):
    import pandas as pd

    return pd.Series(x).rolling(w).median().to_numpy()


def _roll_max(x, w: int):
    import pandas as pd

    return pd.Series(x).rolling(w, min_periods=1).max().to_numpy()


def _roll_min(x, w: int):
    import pandas as pd

    return pd.Series(x).rolling(w, min_periods=1).min().to_numpy()


def _lag_ratio(x, k: int):
    """x[i] / x[i-k] - 1, in percent, NaN where the lag is unavailable."""
    import numpy as np

    out = np.full(x.size, np.nan)
    if x.size > k:
        prev = x[:-k]
        with np.errstate(divide="ignore", invalid="ignore"):
            out[k:] = np.where(prev > 0, x[k:] / prev - 1.0, np.nan) * 100.0
    return out


def series_features(p: dict) -> dict:
    """Full-series feature arrays for one symbol. No forward information."""
    import numpy as np

    o, h, l, c, v = p["open"], p["high"], p["low"], p["close"], p["volume"]
    n = c.size
    f: dict = {}

    f["price"] = c
    f["bars_available"] = np.arange(1, n + 1, dtype=np.float64)

    dv = c * v
    f["dvol_20"] = _roll_mean(dv, 20)
    f["dvol_63"] = _roll_mean(dv, 63)
    # Median, not mean, is the tradability read. A shell that trades 1,000
    # shares a day but prints one 5-million-share block passes a mean-based
    # $1M floor while being uninvestable — and those shells are exactly the
    # names that later post four-digit "returns", so a mean floor would seed
    # the winner cohort with names nobody could have bought.
    f["dvol_med_20"] = _roll_median(dv, 20)
    with np.errstate(divide="ignore", invalid="ignore"):
        f["dvol_trend_20_63"] = f["dvol_20"] / f["dvol_63"]

    # Run-ups (the "how much has already happened" axis).
    for k in (5, 10, 20, 63, 126):
        f[f"ret_{k}d"] = _lag_ratio(c, k)

    # Moving averages, distance, and slope.
    for w in (20, 50, 200):
        ma = _roll_mean(c, w)
        f[f"ma{w}"] = ma
        with np.errstate(divide="ignore", invalid="ignore"):
            f[f"dist_ma{w}_pct"] = np.where(ma > 0, c / ma - 1.0, np.nan) * 100.0
    f["ma50_slope_20d_pct"] = _lag_ratio(f["ma50"], 20)
    f["ma200_slope_20d_pct"] = _lag_ratio(f["ma200"], 20)

    # Position within the 52-week range, and how long since that high was set.
    hi252 = _roll_max(c, 252)
    with np.errstate(divide="ignore", invalid="ignore"):
        f["dd_from_high_pct"] = np.where(hi252 > 0, c / hi252 - 1.0, np.nan) * 100.0
    at_high = c >= hi252 - 1e-12
    f["days_since_252d_high"] = (
        np.arange(n, dtype=np.float64)
        - np.maximum.accumulate(np.where(at_high, np.arange(n), 0)).astype(np.float64)
    )

    # Volatility: level, and the 20-vs-63 ratio that reads as compression.
    with np.errstate(divide="ignore", invalid="ignore"):
        logret = np.concatenate(([np.nan], np.log(np.where(c[1:] > 0, c[1:], np.nan) /
                                                 np.where(c[:-1] > 0, c[:-1], np.nan))))
    lr = np.nan_to_num(logret, nan=0.0)
    f["rvol_20d_pct"] = _roll_std(lr, 20) * math.sqrt(252) * 100.0
    f["rvol_63d_pct"] = _roll_std(lr, 63) * math.sqrt(252) * 100.0
    with np.errstate(divide="ignore", invalid="ignore"):
        f["rvol_ratio_20_63"] = f["rvol_20d_pct"] / f["rvol_63d_pct"]

    # Volume behaviour: the trend the frozen scanner uses (10/30) plus a
    # shorter expansion read and a longer trend.
    m10, m30 = _roll_mean(v, 10), _roll_mean(v, 30)
    m5, m20, m63 = _roll_mean(v, 5), _roll_mean(v, 20), _roll_mean(v, 63)
    with np.errstate(divide="ignore", invalid="ignore"):
        f["vol_trend_10_30"] = np.where(m30 > 0, m10 / m30, np.nan)
        f["vol_expansion_5_63"] = np.where(m63 > 0, m5 / m63, np.nan)
        f["vol_trend_20_63"] = np.where(m63 > 0, m20 / m63, np.nan)

    # Structure: are the recent lows higher than the prior block's lows?
    lo20 = _roll_min(l, 20)
    f["higher_lows_20_40"] = np.concatenate(
        (np.full(min(20, n), np.nan), (lo20[20:] > lo20[:-20]).astype(np.float64))
    )[:n]

    # Overnight gap behaviour and true range.
    prev_c = np.concatenate(([np.nan], c[:-1]))
    with np.errstate(divide="ignore", invalid="ignore"):
        gap = np.abs(np.where(prev_c > 0, o / prev_c - 1.0, np.nan)) * 100.0
    f["gap_abs_20d_pct"] = _roll_mean(np.nan_to_num(gap, nan=0.0), 20)
    tr = np.maximum.reduce([
        h - l,
        np.abs(h - np.nan_to_num(prev_c, nan=h[0])),
        np.abs(l - np.nan_to_num(prev_c, nan=l[0])),
    ])
    with np.errstate(divide="ignore", invalid="ignore"):
        f["atr14_pct"] = np.where(c > 0, _roll_mean(tr, 14) / c, np.nan) * 100.0
    return f


# Features the panel carries per (ticker, scan_date). Order is the column order.
FEATURE_COLS: tuple[str, ...] = (
    "price", "bars_available", "dvol_20", "dvol_63", "dvol_med_20", "dvol_trend_20_63",
    "ret_5d", "ret_10d", "ret_20d", "ret_63d", "ret_126d",
    "dist_ma20_pct", "dist_ma50_pct", "dist_ma200_pct",
    "ma50_slope_20d_pct", "ma200_slope_20d_pct",
    "dd_from_high_pct", "days_since_252d_high",
    "rvol_20d_pct", "rvol_63d_pct", "rvol_ratio_20_63",
    "vol_trend_10_30", "vol_expansion_5_63", "vol_trend_20_63",
    "higher_lows_20_40", "gap_abs_20d_pct", "atr14_pct",
)

# Derived at panel-assembly time because they need SPY on the same dates.
RS_COLS: tuple[str, ...] = (
    "rs_5d", "rs_10d", "rs_20d", "rs_63d", "rs_126d",
    "rs_accel_10_20", "rs_accel_20_63", "combined_rs", "replicated_score",
)


# ── forward returns (labels only) ───────────────────────────────────────────


def _forward_point(c, pos, h: int):
    """(return %, matured flag) *h* sessions after each position.

    A name that stops trading inside the horizon is carried to its final bar
    rather than dropped — dropping it would restore the survivorship bias the
    replay universe was built to remove — and flagged as truncated.
    """
    import numpy as np

    n = c.size
    base = c[pos]
    j = pos + h
    ok = j < n
    j_clipped = np.where(ok, j, n - 1)
    with np.errstate(divide="ignore", invalid="ignore"):
        ret = np.where(base > 0, c[j_clipped] / base - 1.0, np.nan) * 100.0
    return ret.astype(np.float64), ok


def _forward_mfe(c, pos, w: int):
    """Max close return inside (pos, pos+w] — the "gained X% within N" question."""
    import numpy as np
    import pandas as pd

    n = c.size
    rev = c[::-1]
    fwd_max = pd.Series(rev).rolling(w, min_periods=1).max().to_numpy()[::-1]
    nxt = np.minimum(pos + 1, n - 1)
    peak = fwd_max[nxt]
    base = c[pos]
    with np.errstate(divide="ignore", invalid="ignore"):
        return (np.where(base > 0, peak / base - 1.0, np.nan) * 100.0).astype(np.float64)


def build_panel(root: Path, args) -> "object":
    """Assemble the (ticker × scan_date) panel of as-of features and labels."""
    import numpy as np
    import pandas as pd

    exclude = load_quarantine_excludes(root)
    prices = load_prices(root, exclude)
    for b in BENCHMARKS:
        if b not in prices:
            raise SystemExit(f"benchmark {b} missing from cache/replay_prices")
    scan_dates, _ = weekly_scan_dates(prices, args.start, args.end)
    if args.limit:
        scan_dates = scan_dates[: args.limit]
    dates64 = np.array([np.datetime64(d) for d in scan_dates])
    print(f"scan dates: {len(scan_dates)}  {scan_dates[0].date()} .. "
          f"{scan_dates[-1].date()}", flush=True)

    # Benchmark returns on the same dates, for the RS columns and the excess
    # labels. Computed once.
    bench: dict[str, dict] = {}
    for b in BENCHMARKS:
        p = prices[b]
        bpos = np.searchsorted(p["idx"], dates64, side="right") - 1
        bench[b] = {"pos": bpos, "close": p["close"]}
        for k in (5, 10, 20, 63, 126):
            bench[b][f"ret_{k}d"] = _lag_ratio(p["close"], k)[bpos]
        for h in HORIZONS:
            bench[b][f"fwd_{h}"] = _forward_point(p["close"], bpos, h)[0]

    chunks: list[dict] = []
    t0 = time.time()
    for n_sym, (sym, p) in enumerate(sorted(prices.items()), 1):
        c, idx = p["close"], p["idx"]
        pos = np.searchsorted(idx, dates64, side="right") - 1
        alive = pos >= 0
        if not alive.any():
            continue
        # The sampled bar must be recent enough to be the name's *current*
        # quote on that date. Without this a name that stopped trading in 2023
        # would keep reporting its final bar as a live 2025 price and then show
        # a flat forward return for every remaining date.
        lag_days = np.full(dates64.size, np.nan)
        lag_days[alive] = (
            (dates64[alive] - idx[pos[alive]]) / np.timedelta64(1, "D")
        ).astype(float)
        keep = alive & (lag_days <= float(args.max_bar_lag_days))
        if not keep.any():
            continue

        pk = pos[keep]
        feats = series_features(p)
        rec: dict = {
            "ticker": np.full(pk.size, sym, dtype=object),
            "scan_date": dates64[keep],
            "bar_lag_days": lag_days[keep],
        }
        for col in FEATURE_COLS:
            rec[col] = feats[col][pk]
        for h in HORIZONS:
            ret, matured = _forward_point(c, pk, h)
            rec[f"fwd_{h}d"] = ret
            rec[f"fwd_{h}d_matured"] = matured.astype(np.float64)
            for b in BENCHMARKS:
                rec[f"fwd_{h}d_vs_{b.lower()}"] = ret - bench[b][f"fwd_{h}"][keep]
        for name, _thr, w in MFE_LABELS:
            rec[f"mfe_{w}d"] = _forward_mfe(c, pk, w)
        for k in (5, 10, 20, 63, 126):
            rec[f"rs_{k}d"] = rec[f"ret_{k}d"] - bench["SPY"][f"ret_{k}d"][keep]
        chunks.append(rec)
        if n_sym % 1500 == 0:
            print(f"  [{n_sym}/{len(prices)}] {time.time() - t0:.0f}s", flush=True)

    cols = list(chunks[0].keys())
    data = {col: np.concatenate([ch[col] for ch in chunks]) for col in cols}
    df = pd.DataFrame(data)
    df["scan_date"] = pd.to_datetime(df["scan_date"])
    df["year"] = df["scan_date"].dt.year

    # RS acceleration and the replicated old-scanner score. The score formula
    # is mirrored here ONLY so trap exposure can be measured on the full
    # universe; nothing here feeds the live scanner, which is untouched.
    df["rs_accel_10_20"] = df["rs_10d"] - df["rs_20d"] * (10.0 / 20.0)
    df["rs_accel_20_63"] = df["rs_20d"] - df["rs_63d"] * (20.0 / 63.0)
    df["combined_rs"] = df["rs_63d"] * 0.6 + df["rs_20d"] * 0.4
    df["replicated_score"] = (50.0 + df["combined_rs"] * 0.8).clip(0.0, 100.0)
    df["in_score_100_zone"] = df["combined_rs"] >= 62.5

    for name, floor in FLOOR_VARIANTS.items():
        df[f"eligible_{name}"] = (
            (df["price"] >= floor["min_price"])
            & (df["dvol_med_20"] >= floor["min_dvol"])
            & (df["bars_available"] >= floor["min_bars"])
        )
    df["eligible"] = df["eligible_primary"]

    for name, thr, w in MFE_LABELS:
        df[f"win_{name}"] = df[f"mfe_{w}d"] >= thr

    print(f"panel: {len(df):,} rows · {df.ticker.nunique():,} tickers · "
          f"{df.scan_date.nunique()} dates · {time.time() - t0:.0f}s", flush=True)
    return df


def _assert_no_lookahead(root: Path, df, n_checks: int = 40, seed: int = 7) -> dict:
    """Recompute a sample of feature rows from a physically truncated series.

    The panel's as-of property rests on gathering full-series arrays at the
    scan-date position. That is correct only if every feature is backward
    looking. This re-derives the same rows from a series cut at the scan date —
    where a forward-looking feature would have nothing to read — and fails the
    stage if any value moves.
    """
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(seed)
    sample = df.iloc[rng.choice(len(df), size=min(n_checks, len(df)), replace=False)]
    checked = 0
    for _, row in sample.iterrows():
        f = root / "cache" / "replay_prices" / f"{row['ticker']}.parquet"
        if not f.exists():
            continue
        raw = pd.read_parquet(f).sort_index()
        cut = raw[raw.index <= row["scan_date"]]
        if cut.empty:
            continue
        p = {k: cut[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume")}
        p["idx"] = cut.index.values.astype("datetime64[ns]")
        feats = series_features(p)
        for col in FEATURE_COLS:
            a, b = float(feats[col][-1]), float(row[col])
            if math.isnan(a) and math.isnan(b):
                continue
            if not math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9):
                raise AssertionError(
                    f"lookahead check failed: {row['ticker']} {row['scan_date']} "
                    f"{col}: truncated={a} panel={b}"
                )
        checked += 1
    return {"rows_checked": checked, "features_per_row": len(FEATURE_COLS)}


# ── stage: panel ────────────────────────────────────────────────────────────


def panel_stage(args) -> int:
    root = Path(args.root)
    tripwire = LiveArtifactTripwire.snapshot(root)
    df = build_panel(root, args)
    audit = _assert_no_lookahead(root, df, n_checks=args.lookahead_checks)
    print(f"lookahead audit: {audit['rows_checked']} rows × "
          f"{audit['features_per_row']} features re-derived from truncated "
          f"series, all identical")

    out = root / PANEL_REL
    out.parent.mkdir(parents=True, exist_ok=True)
    from research.backtests.common import assert_replay_write_path

    assert_replay_write_path(out, root=root)
    # float32 + zstd: the panel is an intermediate, and 4-byte precision is far
    # finer than the noise in a percentage return.
    slim = df.copy()
    for col, dt in slim.dtypes.items():
        if dt == "float64":
            slim[col] = slim[col].astype("float32")
    slim["ticker"] = slim["ticker"].astype("category")
    slim.to_parquet(out, index=False, compression="zstd")

    meta = {
        "kind": "ALPHA_RECONSTRUCTION_PANEL_META",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rows": int(len(df)),
        "tickers": int(df.ticker.nunique()),
        "dates": int(df.scan_date.nunique()),
        "window": [str(df.scan_date.min().date()), str(df.scan_date.max().date())],
        "eligible_rows": {
            name: int(df[f"eligible_{name}"].sum()) for name in FLOOR_VARIANTS
        },
        "median_eligible_names_per_date": {
            name: float(df.groupby("scan_date")[f"eligible_{name}"].sum().median())
            for name in FLOOR_VARIANTS
        },
        "floors": FLOOR_VARIANTS,
        "max_bar_lag_days": args.max_bar_lag_days,
        "series_integrity_quarantine": {
            "n": len(QUARANTINED),
            "rules": {
                "split_artifact_single_day": f">= +{SPLIT_ARTIFACT_MAX_1D_RETURN * 100:.0f}% in one session",
                "split_artifact_range_ratio": f"max/min close >= {SPLIT_ARTIFACT_RANGE_RATIO:.0f}x",
                "exchange_test_symbol": sorted(TEST_SYMBOLS),
            },
            "by_reason": {
                r: sum(1 for v in QUARANTINED.values() if v["reason"] == r)
                for r in sorted({v["reason"] for v in QUARANTINED.values()})
            },
            "symbols": QUARANTINED,
        },
        "lookahead_audit": audit,
        "horizons": list(HORIZONS),
        **provenance_flags(),
    }
    assert_provenance(meta)
    write_replay_json(root / f"{INTERMEDIATES_REL}/panel_meta.json", meta, root=root)
    print(tripwire.report())
    tripwire.assert_clean()
    print(json.dumps(meta["median_eligible_names_per_date"], indent=1))
    return 0




# ── statistics ──────────────────────────────────────────────────────────────


def _trim_mean(a, prop: float = 0.10) -> float:
    import numpy as np

    n = a.size
    k = int(prop * n)
    if n - 2 * k <= 0:
        return float(a.mean())
    return float(np.sort(a)[k: n - k].mean())


def _winsor_mean(a, lo: float = 5.0, hi: float = 95.0) -> float:
    import numpy as np

    if a.size == 0:
        return float("nan")
    return float(np.clip(a, np.percentile(a, lo), np.percentile(a, hi)).mean())


def cohort_stats(series) -> dict | None:
    """Central tendency of a return series, reported four ways.

    Return distributions are right-skewed enough that mean and median tell
    different stories; both are reported, plus trimmed and winsorized means, so
    a cohort cannot look good on one statistic alone.
    """
    import numpy as np

    a = np.asarray(series, dtype=float)
    a = a[np.isfinite(a)]
    if a.size == 0:
        return None
    return {
        "n": int(a.size),
        "mean": round(float(a.mean()), 3),
        "median": round(float(np.median(a)), 3),
        "trim10": round(_trim_mean(a), 3),
        "winsor5_95": round(_winsor_mean(a), 3),
        "win_rate": round(float((a > 0).mean() * 100), 1),
        "p90": round(float(np.percentile(a, 90)), 3),
    }


def _cluster_ci(values, n_boot: int, seed: int) -> list:
    """95% CI of the mean by resampling cluster-level values with replacement."""
    import numpy as np

    v = np.asarray([x for x in values if x is not None], dtype=float)
    v = v[np.isfinite(v)]
    if v.size < 3:
        return [None, None]
    rng = np.random.default_rng(seed)
    boots = np.array([
        float(np.mean(rng.choice(v, size=v.size, replace=True))) for _ in range(n_boot)
    ])
    return [round(float(np.percentile(boots, 2.5)), 3),
            round(float(np.percentile(boots, 97.5)), 3)]


def selection_vs_universe(picks, universe, h: int, n_boot: int, seed: int) -> dict | None:
    """Per-date median of *picks* minus per-date median of *universe*.

    This is the only honest read of a selector: the difference between what it
    chose and the pool it chose from, on the same day. An absolute return
    number mostly measures the market. Confidence intervals are clustered two
    ways because episodes are correlated both within a scan date (shared market
    day) and within a ticker (the same name recurs for weeks).
    """
    import numpy as np
    import pandas as pd

    col = f"fwd_{h}d"
    if picks.empty:
        return None
    uni_med = universe.groupby("scan_date")[col].median()
    pick_med = picks.groupby("scan_date")[col].median()
    joined = pd.DataFrame({"pick": pick_med, "uni": uni_med}).dropna()
    if len(joined) < 3:
        return None
    diff = (joined["pick"] - joined["uni"]).to_numpy()

    # Ticker-clustered: per-ticker excess over its own date's universe median.
    # The MEDIAN is taken per ticker, deliberately: a mean here would be a
    # different estimand from the date-clustered number above (which is built
    # from medians), and comparing a mean-based CI with a median-based one as
    # though they bracket the same quantity is how a right-skewed cohort ends
    # up looking significant on one clustering and not the other. The
    # mean-based version is reported alongside, never used as the test.
    m = picks[["ticker", "scan_date", col]].dropna().copy()
    m["uni_med"] = m["scan_date"].map(uni_med)
    m = m.dropna(subset=["uni_med"])
    m["excess"] = m[col] - m["uni_med"]
    g = m.groupby("ticker", observed=True)["excess"]
    by_ticker = g.median().to_numpy()
    by_ticker_mean = g.mean().to_numpy()

    return {
        "picks_median": round(float(joined["pick"].mean()), 3),
        "universe_median": round(float(joined["uni"].mean()), 3),
        "selection": round(float(diff.mean()), 3),
        "date_cluster_ci": _cluster_ci(diff, n_boot, seed),
        "ticker_cluster_ci": _cluster_ci(by_ticker, n_boot, seed + 1),
        "ticker_cluster_ci_mean_based": _cluster_ci(by_ticker_mean, n_boot, seed + 2),
        "ticker_median_excess": round(float(np.median(by_ticker)), 3) if by_ticker.size else None,
        "dates_won_pct": round(float((diff > 0).mean() * 100), 1),
        "n_dates": int(len(joined)),
        "n_tickers": int(by_ticker.size),
        "n_episodes": int(len(m)),
    }


# ── metadata joins (all clearly labelled by provenance) ─────────────────────


def load_delisted(root: Path) -> set[str]:
    """Symbols harvested from the provider's delisted-companies endpoint."""
    f = root / "cache" / "replay_universe" / "delisted_companies.json"
    if not f.exists():
        return set()
    d = json.loads(f.read_text())
    rows = d if isinstance(d, list) else (d.get("rows") or [])
    out = set()
    for r in rows:
        if isinstance(r, str):
            out.add(r.upper())
        elif isinstance(r, dict):
            sym = r.get("symbol") or r.get("ticker")
            if sym:
                out.add(str(sym).upper())
    return out


def load_delisted_dates(root: Path) -> dict:
    """symbol -> delisting date, for the names that stopped trading in-window."""
    f = root / "cache" / "replay_universe" / "delisted_companies.json"
    if not f.exists():
        return {}
    d = json.loads(f.read_text())
    out = {}
    for r in (d.get("rows") or []):
        sym, dd = r.get("symbol"), r.get("delistedDate")
        if sym and dd:
            out[str(sym).upper()] = str(dd)
    return out


def load_sector_map(root: Path) -> dict:
    """CURRENT-METADATA APPROXIMATION — sector as the provider reports it today.

    Read from the live Gatekeeper profile cache (read-only). It is not
    point-in-time: a company's sector label today may differ from its label in
    2022, and delisted names are absent entirely. Used for descriptive
    breakdowns only — never as a feature in any fingerprint.
    """
    import sqlite3

    db = root / "db" / "trading.db"
    if not db.exists():
        return {}
    out: dict = {}
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            for key, payload in con.execute(
                "SELECT key, payload FROM cache_meta WHERE key LIKE 'fmp:profile:%'"
            ):
                try:
                    d = json.loads(payload)
                except Exception:
                    continue
                sym = str(key).split(":")[-1].upper()
                if isinstance(d, dict) and d.get("sector"):
                    out[sym] = d["sector"]
        finally:
            con.close()
    except Exception:
        return {}
    return out


def market_cap_panel(root: Path, df) -> dict:
    """As-of market cap per (ticker, scan_date), where the replay cached it.

    POINT-IN-TIME SAFE but INCOMPLETE: the market-cap cache was fetched for the
    2,671 tickers the old scanner's replay surfaced, so coverage is conditioned
    on the old scanner having picked the name at some point in the window.
    """
    import numpy as np
    import pandas as pd

    src = root / "cache" / "replay_market_cap"
    dates = np.sort(df["scan_date"].unique())
    out: dict[tuple, float] = {}
    for f in src.glob("*.parquet"):
        try:
            d = pd.read_parquet(f)
        except Exception:
            continue
        if d.empty:
            continue
        idx = d.index.values.astype("datetime64[ns]")
        vals = d["marketCap"].to_numpy(float)
        pos = np.searchsorted(idx, dates, side="right") - 1
        for dt, i in zip(dates, pos):
            if i >= 0 and np.isfinite(vals[i]) and vals[i] > 0:
                out[(f.stem, pd.Timestamp(dt))] = float(vals[i])
    return out


# ── stage: winners (Part 1) ─────────────────────────────────────────────────

# Cross-sectional winner definitions, applied per scan date so that a rising
# market does not manufacture winners and a falling one does not erase them.
PCT_CUTS: tuple[float, ...] = (1.0, 2.0, 5.0)
PRIMARY_WINNER_H = 60          # the horizon the autopsy and fingerprints target
BENCH_MARGIN_PP = 25.0         # "beat the benchmarks by a large margin"


def load_panel(root: Path, columns=None):
    import pandas as pd

    f = root / PANEL_REL
    if not f.exists():
        raise SystemExit(f"missing {PANEL_REL} — run the panel stage first")
    df = pd.read_parquet(f, columns=columns)
    df["ticker"] = df["ticker"].astype(str)
    return df


def label_winners(df):
    """Attach every winner label to an eligible panel. Labels only — no features.

    Percentile labels are computed *within scan date* on the eligible universe,
    so "top 1%" means top 1% of what was investable that week.
    """
    for h in HORIZONS:
        col = f"fwd_{h}d"
        rank = df.groupby("scan_date")[col].rank(pct=True, ascending=False) * 100.0
        df[f"pctrank_{h}d"] = rank
        for cut in PCT_CUTS:
            df[f"top{cut:g}pct_{h}d"] = rank <= cut
    for h in (20, 60, 126):
        df[f"beat_all_bench_{h}d"] = (
            (df[f"fwd_{h}d_vs_spy"] >= BENCH_MARGIN_PP)
            & (df[f"fwd_{h}d_vs_qqq"] >= BENCH_MARGIN_PP)
            & (df[f"fwd_{h}d_vs_iwm"] >= BENCH_MARGIN_PP)
        )
    return df


def _bucket_counts(sub, series, order=None) -> dict:
    vc = series.value_counts()
    if order:
        return {k: int(vc.get(k, 0)) for k in order}
    return {str(k): int(v) for k, v in vc.items()}


def winners_stage(args) -> int:
    import numpy as np
    import pandas as pd

    root = Path(args.root)
    tripwire = LiveArtifactTripwire.snapshot(root)
    df = load_panel(root)
    elig = df[df["eligible"]].copy()
    elig = label_winners(elig)

    delisted = load_delisted(root)
    sectors = load_sector_map(root)
    mcap = market_cap_panel(root, elig)
    elig["market_cap"] = [
        mcap.get((t, d)) for t, d in zip(elig["ticker"], elig["scan_date"])
    ]
    elig["sector_current_meta"] = elig["ticker"].map(sectors).fillna("UNKNOWN")
    elig["from_delisted_pool"] = elig["ticker"].isin(delisted)

    mc_bins = [0, 3e8, 2e9, 1e10, 5e10, np.inf]
    mc_names = ["nano_micro_<300M", "small_300M_2B", "mid_2B_10B", "large_10B_50B", "mega_>50B"]
    elig["mcap_bucket"] = pd.cut(elig["market_cap"], mc_bins, labels=mc_names)
    dv_bins = [0, 3e6, 2e7, 1e8, np.inf]
    dv_names = ["dvol_<3M", "dvol_3M_20M", "dvol_20M_100M", "dvol_>100M"]
    elig["dvol_bucket"] = pd.cut(elig["dvol_20"], dv_bins, labels=dv_names)

    out: dict = {
        "kind": "ALPHA_RECONSTRUCTION_WINNER_UNIVERSE",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window": [str(elig.scan_date.min().date()), str(elig.scan_date.max().date())],
        "eligible_rows": int(len(elig)),
        "eligible_tickers": int(elig.ticker.nunique()),
        "scan_dates": int(elig.scan_date.nunique()),
        "median_eligible_per_date": float(elig.groupby("scan_date").size().median()),
        "floor": PRIMARY_FLOOR,
        "floor_sensitivity": {
            name: {
                "median_names_per_date": float(
                    df.groupby("scan_date")[f"eligible_{name}"].sum().median()),
                "floor": floor,
            }
            for name, floor in FLOOR_VARIANTS.items()
        },
        **provenance_flags(),
    }

    # 1-3: percentile winners, per horizon.
    pct_block: dict = {}
    for h in HORIZONS:
        node = {}
        for cut in PCT_CUTS:
            col = f"top{cut:g}pct_{h}d"
            w = elig[elig[col]]
            node[f"top{cut:g}pct"] = {
                "episodes": int(len(w)),
                "unique_tickers": int(w.ticker.nunique()),
                "min_return_in_cohort": round(float(w[f"fwd_{h}d"].min()), 2) if len(w) else None,
                "median_return": round(float(w[f"fwd_{h}d"].median()), 2) if len(w) else None,
                "mean_return": round(float(w[f"fwd_{h}d"].mean()), 2) if len(w) else None,
            }
        pct_block[f"{h}d"] = node
    out["percentile_winners"] = pct_block

    # 4: path-based threshold winners.
    thr_block: dict = {}
    for name, thr, w_days in MFE_LABELS:
        w = elig[elig[f"win_{name}"]]
        thr_block[name] = {
            "definition": f"max close gain >= +{thr:.0f}% within {w_days} sessions",
            "episodes": int(len(w)),
            "unique_tickers": int(w.ticker.nunique()),
            "episode_rate_pct": round(100.0 * len(w) / max(len(elig), 1), 3),
        }
    out["threshold_winners"] = thr_block

    # 5: winners that also beat all three benchmarks by a wide margin.
    out["benchmark_beating_winners"] = {
        f"{h}d": {
            "definition": f"forward {h}d excess >= +{BENCH_MARGIN_PP:.0f}pp vs SPY and QQQ and IWM",
            "episodes": int(elig[f"beat_all_bench_{h}d"].sum()),
            "unique_tickers": int(elig[elig[f"beat_all_bench_{h}d"]].ticker.nunique()),
        }
        for h in (20, 60, 126)
    }

    # Composition of the primary cohort.
    prim_col = f"top1pct_{PRIMARY_WINNER_H}d"
    w = elig[elig[prim_col]].copy()
    per_ticker = w.groupby("ticker", observed=True).size()
    out["primary_cohort"] = {
        "definition": f"top 1% of eligible names by forward {PRIMARY_WINNER_H}d return, per scan date",
        "episodes": int(len(w)),
        "unique_tickers": int(w.ticker.nunique()),
        "one_time_winners": int((per_ticker == 1).sum()),
        "repeat_winners": int((per_ticker > 1).sum()),
        "repeat_share_of_episodes_pct": round(
            100.0 * int(per_ticker[per_ticker > 1].sum()) / max(len(w), 1), 1),
        "max_episodes_one_ticker": int(per_ticker.max()) if len(per_ticker) else 0,
        "top_repeat_tickers": {
            str(k): int(v) for k, v in per_ticker.nlargest(15).items()
        },
        "by_year": {
            str(int(y)): {
                "episodes": int(g[prim_col].sum()),
                "unique_tickers": int(g[g[prim_col]].ticker.nunique()),
                "median_return": round(float(g[g[prim_col]][f"fwd_{PRIMARY_WINNER_H}d"].median()), 2),
            }
            for y, g in elig.groupby("year")
        },
        "by_regime": {
            name: {
                "episodes": int(elig[elig.year.isin(years)][prim_col].sum()),
                "median_return": round(float(
                    elig[elig.year.isin(years) & elig[prim_col]][f"fwd_{PRIMARY_WINNER_H}d"].median()), 2),
            }
            for name, years in REGIMES.items()
        },
        "by_mcap_bucket": {
            "coverage_pct": round(100.0 * w["market_cap"].notna().mean(), 1),
            "winners": _bucket_counts(w, w["mcap_bucket"].astype(str), mc_names + ["nan"]),
            "universe": _bucket_counts(elig, elig["mcap_bucket"].astype(str), mc_names + ["nan"]),
        },
        "by_dvol_bucket": {
            "winners": _bucket_counts(w, w["dvol_bucket"].astype(str), dv_names),
            "universe": _bucket_counts(elig, elig["dvol_bucket"].astype(str), dv_names),
        },
        "by_sector_current_metadata_approximation": {
            "coverage_pct": round(100.0 * (w["sector_current_meta"] != "UNKNOWN").mean(), 1),
            "winners": _bucket_counts(w, w["sector_current_meta"]),
        },
        "survivor_vs_delisted": {
            "delisted_pool_size": len(delisted),
            "winner_episodes_from_delisted_pool": int(w["from_delisted_pool"].sum()),
            "winner_episodes_from_survivor_pool": int((~w["from_delisted_pool"]).sum()),
            "delisted_share_of_universe_pct": round(
                100.0 * elig["from_delisted_pool"].mean(), 2),
            "delisted_share_of_winners_pct": round(
                100.0 * w["from_delisted_pool"].mean(), 2),
        },
    }

    # Did the delisted pool produce winners, or only losers? The question the
    # survivorship correction exists to answer.
    dl = elig[elig["from_delisted_pool"]]
    sv = elig[~elig["from_delisted_pool"]]
    out["delisted_pool_outcome"] = {
        "note": "Names that stopped trading inside the window, carried to their "
                "final bar rather than dropped.",
        "episodes": int(len(dl)),
        "unique_tickers": int(dl.ticker.nunique()),
        f"fwd_{PRIMARY_WINNER_H}d_delisted": cohort_stats(dl[f"fwd_{PRIMARY_WINNER_H}d"]),
        f"fwd_{PRIMARY_WINNER_H}d_survivors": cohort_stats(sv[f"fwd_{PRIMARY_WINNER_H}d"]),
        "top1pct_rate_delisted_pct": round(100.0 * dl[prim_col].mean(), 3) if len(dl) else None,
        "top1pct_rate_survivors_pct": round(100.0 * sv[prim_col].mean(), 3) if len(sv) else None,
    }

    # The 25 largest single episodes, so a reader can eyeball the cohort for
    # residual data artifacts rather than trusting the quarantine blindly.
    big = elig.nlargest(25, f"fwd_{PRIMARY_WINNER_H}d")
    out["largest_episodes_for_eyeball_audit"] = [
        {
            "ticker": r.ticker,
            "scan_date": str(r.scan_date.date()),
            "price": round(float(r.price), 2),
            "dvol_med_20_musd": round(float(r.dvol_med_20) / 1e6, 2),
            f"fwd_{PRIMARY_WINNER_H}d_pct": round(float(getattr(r, f"fwd_{PRIMARY_WINNER_H}d")), 1),
        }
        for r in big.itertuples()
    ]

    assert_provenance(out)
    write_replay_json(root / WINNERS_REL, out, root=root)
    print(tripwire.report())
    tripwire.assert_clean()
    print(f"winner universe: {out['primary_cohort']['episodes']:,} episodes · "
          f"{out['primary_cohort']['unique_tickers']:,} tickers "
          f"(top 1% @ {PRIMARY_WINNER_H}d)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="alpha_reconstruction_lab",
        description="Alpha Reconstruction Lab — research-only winner autopsy "
                    "over the 2022-2025 replay window. Reads replay caches, "
                    "writes only alpha_reconstruction_* artifacts.",
    )
    sub = ap.add_subparsers(dest="stage", required=True)
    for name in ("panel", "winners", "autopsy", "fingerprints", "report", "all"):
        s = sub.add_parser(name)
        s.add_argument("--root", default=str(ROOT))
        s.add_argument("--start", default=REPLAY_WINDOW_START)
        s.add_argument("--end", default=REPLAY_WINDOW_END)
        s.add_argument("--limit", type=int, default=0,
                       help="cap the number of scan dates (smoke runs)")
        s.add_argument("--max-bar-lag-days", type=float, default=5.0)
        s.add_argument("--lookahead-checks", type=int, default=40)
        s.add_argument("--bootstrap", type=int, default=2000)
        s.add_argument("--seed", type=int, default=11)
    return ap


STAGE_ORDER: tuple[str, ...] = ("panel", "winners", "autopsy", "fingerprints", "report")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.stage == "all":
        for name in STAGE_ORDER:
            print(f"\n{'=' * 70}\n== {name}\n{'=' * 70}", flush=True)
            rc = run_cli(globals()[f"{name}_stage"], args)
            if rc:
                return rc
        return 0
    # Resolved by name so stage functions may be defined anywhere in the file.
    fn = globals().get(f"{args.stage}_stage")
    if fn is None:
        print(f"stage {args.stage!r} not implemented yet")
        return 2
    return run_cli(fn, args)




# ── stage: autopsy (Parts 2-3) ──────────────────────────────────────────────
#
# TRAIN YEARS ONLY. Every number produced by this stage is computed on
# 2022-2024. 2025 is never touched here, so the fingerprint stage's holdout is
# genuinely out-of-sample with respect to everything the author saw.

# Features grouped by what they are, so the report can talk about mechanisms
# rather than a flat list of 30 columns.
FEATURE_GROUPS: dict[str, tuple[str, ...]] = {
    "trend_position": (
        "dist_ma20_pct", "dist_ma50_pct", "dist_ma200_pct",
        "ma50_slope_20d_pct", "ma200_slope_20d_pct",
        "dd_from_high_pct", "days_since_252d_high",
    ),
    "relative_strength": (
        "rs_5d", "rs_10d", "rs_20d", "rs_63d", "rs_126d",
        "rs_accel_10_20", "rs_accel_20_63", "combined_rs",
    ),
    "runup": ("ret_5d", "ret_10d", "ret_20d", "ret_63d", "ret_126d"),
    "volatility": ("rvol_20d_pct", "rvol_63d_pct", "rvol_ratio_20_63",
                   "atr14_pct", "gap_abs_20d_pct"),
    "volume_liquidity": ("vol_trend_10_30", "vol_expansion_5_63", "vol_trend_20_63",
                         "dvol_trend_20_63", "dvol_med_20", "dvol_20"),
    "structure": ("higher_lows_20_40", "price", "bars_available"),
}
ALL_FEATURES: tuple[str, ...] = tuple(
    f for grp in FEATURE_GROUPS.values() for f in grp
)

# Provenance of every feature the lab can compute, per Part 2's requirement to
# separate what is genuinely knowable as-of from what only looks that way.
FEATURE_PROVENANCE: dict[str, list[str]] = {
    "point_in_time_safe": [
        "every price/volume feature in FEATURE_GROUPS — derived from replay OHLCV "
        "bars at or before the scan date, re-verified by the panel stage's "
        "truncated-series audit",
        "market_cap — daily series from the replay market-cap cache, sampled as-of "
        "(but see coverage caveat below)",
    ],
    "restatement_contaminated": [
        "every fundamental metric — the provider serves the CURRENT version of each "
        "statement, correctly stamped with acceptedDate but not as-originally-filed, "
        "so a later restatement leaks backward into a historical date",
    ],
    "current_metadata_approximation": [
        "sector / industry — read from today's profile cache, not point-in-time, and "
        "absent for delisted names; used for description only, never as a rule input",
    ],
    "unavailable_historically": [
        "social attention, news catalyst, topic shock, options chain/IV — no historical "
        "record exists in this repo for the replay window; their seeds were disabled "
        "in the price-only replay and nothing here tests them in either direction",
        "13F/institutional sponsorship, short interest, borrow — never collected",
        "intraday data of any kind — the panel is daily bars only",
    ],
    "coverage_biased": [
        "market cap and fundamentals cover only the 2,671 tickers the OLD SCANNER "
        "surfaced during its replay. Membership in that set depends on the scanner's "
        "behaviour across the whole window, so any fundamental cut is conditioned on "
        "a selection that is partly forward-looking. Treated as secondary evidence "
        "throughout and never used to define a headline fingerprint.",
    ],
}


def _within_date_auc(g, feat: str, label: str) -> float | None:
    """Rank-AUC of *feat* against *label* inside one scan date.

    AUC is the probability that a randomly chosen winner scores higher on the
    feature than a randomly chosen non-winner. 0.5 is no separation. Computed
    within a date so that cross-sectional comparison is like-for-like and no
    market-wide drift can masquerade as separation.
    """
    import numpy as np
    import pandas as pd

    x = g[feat].to_numpy(dtype=float)
    y = g[label].to_numpy(dtype=bool)
    ok = np.isfinite(x)
    x, y = x[ok], y[ok]
    n_pos, n_neg = int(y.sum()), int((~y).sum())
    if n_pos < 3 or n_neg < 3:
        return None
    r = pd.Series(x).rank().to_numpy()
    return float((r[y].sum() - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))


def feature_separation(train, label: str, n_boot: int, seed: int) -> list[dict]:
    """How well each feature separates winners from everything else."""
    import numpy as np

    rows = []
    for feat in ALL_FEATURES:
        aucs, dates = [], []
        for d, g in train.groupby("scan_date"):
            a = _within_date_auc(g, feat, label)
            if a is not None:
                aucs.append(a)
                dates.append(d)
        if len(aucs) < 10:
            continue
        arr = np.array(aucs)
        w = train[train[label]][feat].astype(float)
        u = train[feat].astype(float)
        rows.append({
            "feature": feat,
            "group": next(k for k, v in FEATURE_GROUPS.items() if feat in v),
            "auc_mean": round(float(arr.mean()), 4),
            "auc_ci": _cluster_ci(arr, n_boot, seed),
            "dates_above_half_pct": round(float((arr > 0.5).mean() * 100), 1),
            "winner_median": round(float(np.nanmedian(w)), 3),
            "universe_median": round(float(np.nanmedian(u)), 3),
            "n_dates": len(aucs),
        })
    rows.sort(key=lambda r: abs(r["auc_mean"] - 0.5), reverse=True)
    return rows


def decile_profile(train, feat: str, label: str, h: int) -> list[dict]:
    """Winner rate and forward return by within-date decile of *feat*.

    The point of deciles rather than a correlation: the interesting shapes here
    are not monotone. If momentum helps up to a point and then hurts, only a
    decile profile shows it — a mean or a correlation coefficient hides it.
    """
    import numpy as np
    import pandas as pd

    df = train[["scan_date", feat, label, f"fwd_{h}d"]].dropna(subset=[feat])
    if df.empty:
        return []
    dec = df.groupby("scan_date")[feat].transform(
        lambda s: pd.qcut(s.rank(method="first"), 10, labels=False, duplicates="drop")
    )
    df = df.assign(decile=dec).dropna(subset=["decile"])
    out = []
    for d, g in df.groupby("decile"):
        out.append({
            "decile": int(d) + 1,
            "n": int(len(g)),
            "feature_median": round(float(g[feat].median()), 3),
            "winner_rate_pct": round(float(g[label].mean() * 100), 3),
            "fwd_median": round(float(g[f"fwd_{h}d"].median()), 3),
            "fwd_mean": round(float(g[f"fwd_{h}d"].mean()), 3),
            "fwd_trim10": round(_trim_mean(g[f"fwd_{h}d"].dropna().to_numpy()), 3),
        })
    return out


def load_old_scanner_episodes(root: Path):
    """The old scanner's replay picks, as a control cohort.

    Read-only, and read for comparison only: nothing here writes to, tunes, or
    otherwise touches the live scanner.
    """
    import pandas as pd

    f = root / "cache" / "research" / "historical_replay_episodes.jsonl"
    if not f.exists():
        return None
    rows = [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
    df = pd.DataFrame(rows)
    df["scan_date"] = pd.to_datetime(df["scan_date"])
    return df[["scan_date", "ticker", "category", "research_score"]]


def load_fundamental_episodes(root: Path):
    """HC / EO membership per (ticker, date) from the fundamental replay."""
    import pandas as pd

    f = root / "cache" / "research" / "historical_fundamental_replay_episodes.parquet"
    if not f.exists():
        return None
    df = pd.read_parquet(f)
    df["scan_date"] = pd.to_datetime(df["scan_date"])
    keep = [c for c in ("scan_date", "ticker", "hc_shortlisted", "eo_selected",
                        "is_score_100", "market_cap") if c in df.columns]
    return df[keep]


def attach_controls(elig, root: Path):
    """Flag each panel row with its old-scanner / HC / EO membership."""
    import pandas as pd

    old = load_old_scanner_episodes(root)
    elig["in_old_scanner"] = False
    elig["old_scanner_category"] = None
    elig["old_score_100"] = False
    elig["old_long_term_asymmetric"] = False
    if old is not None:
        key = old.assign(
            in_old_scanner=True,
            old_score_100=old["research_score"] >= 100,
            old_long_term_asymmetric=old["category"] == "long_term_asymmetric",
        ).rename(columns={"category": "old_scanner_category"})
        key = key.drop_duplicates(["scan_date", "ticker"])
        elig = elig.drop(columns=["in_old_scanner", "old_scanner_category",
                                  "old_score_100", "old_long_term_asymmetric"]).merge(
            key[["scan_date", "ticker", "in_old_scanner", "old_scanner_category",
                 "old_score_100", "old_long_term_asymmetric"]],
            on=["scan_date", "ticker"], how="left")
        for c in ("in_old_scanner", "old_score_100", "old_long_term_asymmetric"):
            elig[c] = elig[c].fillna(False).astype(bool)

    fund = load_fundamental_episodes(root)
    elig["in_hc"] = False
    elig["in_eo"] = False
    if fund is not None:
        f = fund.drop_duplicates(["scan_date", "ticker"]).copy()
        f["in_hc"] = f.get("hc_shortlisted", False).fillna(False).astype(bool)
        f["in_eo"] = f.get("eo_selected", False).fillna(False).astype(bool)
        elig = elig.drop(columns=["in_hc", "in_eo"]).merge(
            f[["scan_date", "ticker", "in_hc", "in_eo"]],
            on=["scan_date", "ticker"], how="left")
        for c in ("in_hc", "in_eo"):
            elig[c] = elig[c].fillna(False).astype(bool)
    return elig


def _control_cohorts(train) -> dict:
    """The control masks Part 3 requires, as named boolean series."""
    import numpy as np

    rng = np.random.default_rng(4242)
    label = f"top1pct_{PRIMARY_WINNER_H}d"
    non_winner = ~train[label]
    # Same-date random non-winners, matched in count to the old scanner's board
    # so the comparison is not a sample-size artifact.
    rand = np.zeros(len(train), dtype=bool)
    idx = np.flatnonzero(non_winner.to_numpy())
    take = rng.choice(idx, size=min(len(idx), 60_000), replace=False)
    rand[take] = True
    return {
        "full_universe": np.ones(len(train), dtype=bool),
        "random_non_winners": rand,
        "old_scanner_board": train["in_old_scanner"].to_numpy(),
        "old_scanner_score_100": train["old_score_100"].to_numpy(),
        "old_long_term_asymmetric": train["old_long_term_asymmetric"].to_numpy(),
        "hc_shortlist": train["in_hc"].to_numpy(),
        "eo_watch": train["in_eo"].to_numpy(),
        "replicated_score_100_zone": train["in_score_100_zone"].to_numpy(),
    }


def autopsy_stage(args) -> int:
    import numpy as np
    import pandas as pd

    root = Path(args.root)
    tripwire = LiveArtifactTripwire.snapshot(root)
    df = load_panel(root)
    elig = df[df["eligible"]].copy()
    elig = label_winners(elig)
    elig = attach_controls(elig, root)

    train = elig[elig["year"].isin(TRAIN_YEARS)].copy()
    label = f"top1pct_{PRIMARY_WINNER_H}d"
    print(f"train rows {len(train):,} ({sorted(TRAIN_YEARS)}) · "
          f"winners {int(train[label].sum()):,}", flush=True)

    out: dict = {
        "kind": "ALPHA_RECONSTRUCTION_WINNER_AUTOPSY",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "discovery_window": {
            "train_years": list(TRAIN_YEARS),
            "holdout_years_not_read_here": list(HOLDOUT_YEARS),
            "note": "Every statistic in this artifact is computed on the train "
                    "years only. The holdout is not read by this stage.",
        },
        "primary_label": {
            "name": label,
            "definition": f"top 1% of the eligible universe by forward "
                          f"{PRIMARY_WINNER_H}d return, ranked within each scan date",
            "train_episodes": int(train[label].sum()),
            "train_tickers": int(train[train[label]].ticker.nunique()),
        },
        "feature_provenance": FEATURE_PROVENANCE,
        **provenance_flags(),
    }

    # Part 2 — the autopsy proper: what did winners look like beforehand?
    out["feature_separation"] = feature_separation(train, label, args.bootstrap, args.seed)

    # The single most important control in this study. The top-1% cohort is
    # defined by an extreme in one tail; if the same features also mark the
    # BOTTOM 1%, they identify variance rather than direction, and a filter
    # built on them buys lottery tickets. Run the identical separation against
    # the loser label and correlate the two AUC vectors.
    train["bottom1pct_60d"] = (
        train.groupby("scan_date")[f"fwd_{PRIMARY_WINNER_H}d"].rank(pct=True) * 100.0
    ) <= 1.0
    loser_sep = feature_separation(train, "bottom1pct_60d", args.bootstrap, args.seed)
    w_auc = {r["feature"]: r["auc_mean"] for r in out["feature_separation"]}
    l_auc = {r["feature"]: r["auc_mean"] for r in loser_sep}
    both = [f for f in w_auc if f in l_auc]
    wv = np.array([w_auc[f] - 0.5 for f in both])
    lv = np.array([l_auc[f] - 0.5 for f in both])
    out["loser_mirror"] = {
        "question": "Do the features that mark the top 1% also mark the bottom 1%?",
        "interpretation": "A correlation near +1 means the features separate "
                          "VOLATILITY, not direction: the same names populate both "
                          "tails, and a filter built on them is a lottery ticket, "
                          "not an edge. Near -1 would mean genuine directional "
                          "separation.",
        "auc_correlation_winner_vs_loser": round(float(np.corrcoef(wv, lv)[0, 1]), 4),
        "per_feature": [
            {"feature": f,
             "winner_auc": round(w_auc[f], 4),
             "loser_auc": round(l_auc[f], 4),
             "same_direction": bool((w_auc[f] - 0.5) * (l_auc[f] - 0.5) > 0)}
            for f in sorted(both, key=lambda x: -abs(w_auc[x] - 0.5))
        ],
        "share_same_direction_pct": round(
            100.0 * float(np.mean((wv * lv) > 0)), 1),
    }

    # Same question asked of the outcome rather than the features: what does the
    # cohort that produced the winners look like on average?
    hi_vol = train[train["atr14_pct"] >= train.groupby("scan_date")["atr14_pct"]
                   .transform(lambda s: s.quantile(0.9))]
    out["high_volatility_decile"] = {
        "note": "Top within-date decile of ATR% — the cohort the winner "
                "separation points at.",
        "episodes": int(len(hi_vol)),
        "winner_rate_pct": round(float(hi_vol[label].mean() * 100), 3),
        "loser_rate_pct": round(float(hi_vol["bottom1pct_60d"].mean() * 100), 3),
        **{f"fwd_{h}d": cohort_stats(hi_vol[f"fwd_{h}d"]) for h in (20, 60)},
        "selection_60d": selection_vs_universe(hi_vol, train, 60, args.bootstrap, args.seed),
    }

    # Part 2/3 — decile profiles for the features that matter most, plus the
    # ones the old scanner's failure implicates whether they separate or not.
    focus = [r["feature"] for r in out["feature_separation"][:10]]
    for must in ("combined_rs", "rs_63d", "rs_20d", "dist_ma50_pct", "dist_ma200_pct",
                 "dd_from_high_pct", "rvol_ratio_20_63", "vol_expansion_5_63",
                 "dvol_med_20", "days_since_252d_high"):
        if must not in focus:
            focus.append(must)
    out["decile_profiles"] = {
        f: decile_profile(train, f, label, PRIMARY_WINNER_H) for f in focus
    }

    # Part 3 — winners against every control cohort, at three horizons.
    cohorts = _control_cohorts(train)
    winners_mask = train[label].to_numpy()
    comp: dict = {}
    for name, mask in cohorts.items():
        sub = train[mask]
        node = {"episodes": int(mask.sum()),
                "unique_tickers": int(sub.ticker.nunique()),
                "winner_rate_pct": round(float(sub[label].mean() * 100), 3)}
        for h in (20, 45, 60):
            node[f"fwd_{h}d"] = cohort_stats(sub[f"fwd_{h}d"])
            sel = selection_vs_universe(sub, train, h, args.bootstrap, args.seed)
            node[f"selection_{h}d"] = sel
        comp[name] = node
    comp["winners_top1pct"] = {
        "episodes": int(winners_mask.sum()),
        "unique_tickers": int(train[winners_mask].ticker.nunique()),
        **{f"fwd_{h}d": cohort_stats(train[winners_mask][f"fwd_{h}d"]) for h in (20, 45, 60)},
    }
    out["control_comparison"] = comp

    # How much of the winner set did the old scanner ever see? The recall
    # question, asked of the replay board rather than the live one.
    w = train[winners_mask]
    out["old_scanner_overlap"] = {
        "winner_episodes": int(len(w)),
        "seen_by_old_scanner_pct": round(float(w["in_old_scanner"].mean() * 100), 2),
        "seen_and_score_100_pct": round(float(w["old_score_100"].mean() * 100), 2),
        "seen_in_long_term_asymmetric_pct": round(
            float(w["old_long_term_asymmetric"].mean() * 100), 2),
        "in_hc_shortlist_pct": round(float(w["in_hc"].mean() * 100), 2),
        "in_eo_watch_pct": round(float(w["in_eo"].mean() * 100), 2),
        "universe_seen_by_old_scanner_pct": round(
            float(train["in_old_scanner"].mean() * 100), 2),
        "unique_winner_tickers_ever_seen_pct": round(
            100.0 * w[w["in_old_scanner"]].ticker.nunique() / max(w.ticker.nunique(), 1), 1),
    }

    # The trap, measured directly: what happens to a name already in the
    # saturation zone of the old score?
    trap = {}
    for zone, mask in (("in_score_100_zone", train["in_score_100_zone"]),
                       ("outside_score_100_zone", ~train["in_score_100_zone"])):
        sub = train[mask]
        trap[zone] = {
            "episodes": int(len(sub)),
            "share_of_universe_pct": round(float(mask.mean() * 100), 2),
            "winner_rate_pct": round(float(sub[label].mean() * 100), 3),
            **{f"fwd_{h}d": cohort_stats(sub[f"fwd_{h}d"]) for h in (20, 60)},
        }
    out["score_100_zone"] = trap

    # Combinations, pre-specified from the hypotheses in the brief rather than
    # searched: each is a 2x2 of a "quality" axis against an "extension" axis.
    combos = {}
    pairs = [
        ("rs_63d_positive_not_extended",
         (train["rs_63d"] > 0) & (train["dist_ma50_pct"].between(-5, 15))),
        ("rs_63d_positive_extended",
         (train["rs_63d"] > 0) & (train["dist_ma50_pct"] > 30)),
        ("volume_expanding_vol_compressed",
         (train["vol_expansion_5_63"] > 1.3) & (train["rvol_ratio_20_63"] < 0.9)),
        ("deep_drawdown_stabilising",
         (train["dd_from_high_pct"] < -50) & (train["dist_ma50_pct"] > 0)
         & (train["rs_20d"] > 0)),
        ("quiet_base_long_since_high",
         (train["days_since_252d_high"] > 120) & (train["rvol_ratio_20_63"] < 0.9)
         & (train["rs_20d"] > 0)),
        ("extreme_rs_tail",
         train["combined_rs"] > 100),
    ]
    for name, mask in pairs:
        sub = train[mask.fillna(False)]
        if sub.empty:
            continue
        combos[name] = {
            "episodes": int(len(sub)),
            "share_of_universe_pct": round(float(mask.fillna(False).mean() * 100), 2),
            "winner_rate_pct": round(float(sub[label].mean() * 100), 3),
            "winner_rate_lift_vs_universe": round(
                float(sub[label].mean() / max(train[label].mean(), 1e-9)), 2),
            "selection_60d": selection_vs_universe(sub, train, 60, args.bootstrap, args.seed),
        }
    out["prespecified_combinations"] = combos

    assert_provenance(out)
    write_replay_json(root / AUTOPSY_REL, out, root=root)
    print(tripwire.report())
    tripwire.assert_clean()
    top = out["feature_separation"][:6]
    print("\ntop separating features (AUC vs 0.5):")
    for r in top:
        print(f"  {r['feature']:<22} auc={r['auc_mean']:.3f} ci={r['auc_ci']} "
              f"win_med={r['winner_median']} uni_med={r['universe_median']}")
    return 0

# ── point-in-time fundamentals (secondary evidence only) ────────────────────
#
# COVERAGE BIAS, stated once and carried into every artifact that uses these:
# the replay fetched fundamentals for the 2,671 tickers the OLD SCANNER
# surfaced during its own replay. Membership in that set depends on the
# scanner's behaviour across the entire window, so it is partly a function of
# the future relative to any single scan date. Fundamental results are
# therefore reported on a fundamentals-covered sub-universe with its own
# control, never pooled with the full-universe numbers, and never used to
# define a headline fingerprint.
#
# RESTATEMENT CONTAMINATION: the provider serves the current version of each
# statement. acceptedDate placement is correct, the numbers are not
# as-originally-filed.

# Denominator floors for the ratio features above.
MIN_REVENUE_BASE = 1_000_000.0     # $1M of trailing revenue
MIN_SHARE_BASE = 100_000.0         # 100k diluted shares

FUND_COLS: tuple[str, ...] = (
    "rev_yoy_pct", "rev_accel_pp", "gross_margin_pct", "op_margin_pct",
    "op_margin_delta_pp", "fcf_ttm", "fcf_positive", "net_income_ttm",
    "profitable", "net_cash", "debt_to_cash", "dilution_yoy_pct",
    "quarters_available",
)


def _usable_date(accepted: str):
    """First session a filing could have been acted on; 16:00+ rolls forward.

    Same rule as the fundamental replay, so the two studies agree on what was
    knowable when.
    """
    try:
        ts = datetime.strptime(str(accepted)[:19], "%Y-%m-%d %H:%M:%S")
    except Exception:
        try:
            return datetime.strptime(str(accepted)[:10], "%Y-%m-%d").date()
        except Exception:
            return None
    d = ts.date()
    return (d + timedelta(days=1)) if ts.hour >= 16 else d


def _num(v):
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _sum4(rows, key, start: int = 0):
    """Sum of *key* over four consecutive quarters, or None if any is missing."""
    vals = [_num(r.get(key)) for r in rows[start:start + 4]]
    if len(vals) < 4 or any(v is None for v in vals):
        return None
    return sum(vals)


def _fund_features(rows: list[dict]) -> dict:
    """Trailing-twelve-month fundamentals from newest-first merged quarters."""
    out = {c: None for c in FUND_COLS}
    out["quarters_available"] = len(rows)
    if len(rows) < 4:
        return out

    # Every ratio needs a denominator big enough to mean something. A shell with
    # $40k of revenue produces growth rates in the billions of percent and
    # margins in the millions; those are arithmetic, not information, and left
    # unguarded they dominate any percentile cut built on these columns.
    rev_ttm, rev_prior = _sum4(rows, "revenue"), _sum4(rows, "revenue", 4)
    if rev_ttm is not None and rev_prior is not None and rev_prior >= MIN_REVENUE_BASE:
        out["rev_yoy_pct"] = (rev_ttm / rev_prior - 1.0) * 100.0
    # Acceleration: this quarter's YoY growth minus the prior quarter's, which
    # needs a ninth quarter of history; None rather than a guess when short.
    q0, q4, q1, q5 = (_num(rows[i].get("revenue")) if len(rows) > i else None
                      for i in (0, 4, 1, 5))
    if (None not in (q0, q4, q1, q5)
            and q4 >= MIN_REVENUE_BASE / 4 and q5 >= MIN_REVENUE_BASE / 4):
        out["rev_accel_pp"] = ((q0 / q4 - 1.0) - (q1 / q5 - 1.0)) * 100.0

    big_rev = rev_ttm is not None and rev_ttm >= MIN_REVENUE_BASE
    gp_ttm = _sum4(rows, "grossProfit")
    if gp_ttm is not None and big_rev:
        out["gross_margin_pct"] = gp_ttm / rev_ttm * 100.0
    oi_ttm, oi_prior = _sum4(rows, "operatingIncome"), _sum4(rows, "operatingIncome", 4)
    if oi_ttm is not None and big_rev:
        out["op_margin_pct"] = oi_ttm / rev_ttm * 100.0
        if oi_prior is not None and rev_prior is not None and rev_prior >= MIN_REVENUE_BASE:
            out["op_margin_delta_pp"] = out["op_margin_pct"] - (oi_prior / rev_prior * 100.0)

    fcf = _sum4(rows, "freeCashFlow")
    if fcf is not None:
        out["fcf_ttm"] = fcf
        out["fcf_positive"] = float(fcf > 0)
    ni = _sum4(rows, "netIncome")
    if ni is not None:
        out["net_income_ttm"] = ni
        out["profitable"] = float(ni > 0)

    cash = _num(rows[0].get("cashAndShortTermInvestments"))
    debt = _num(rows[0].get("totalDebt"))
    if cash is not None and debt is not None:
        out["net_cash"] = cash - debt
        out["debt_to_cash"] = debt / cash if cash > 0 else (999.0 if debt > 0 else 0.0)

    sh0 = _num(rows[0].get("weightedAverageShsOutDil"))
    sh4 = _num(rows[4].get("weightedAverageShsOutDil")) if len(rows) > 4 else None
    if sh0 and sh4 and sh4 >= MIN_SHARE_BASE:
        dil = (sh0 / sh4 - 1.0) * 100.0
        # A share count that moved by more than 10x in a year is a split the
        # provider did not adjust, not a financing event.
        out["dilution_yoy_pct"] = dil if -95.0 <= dil <= 900.0 else None
    return out


def build_fundamental_panel(root: Path, scan_dates) -> "object":
    """PIT fundamental features per (ticker, scan_date) for covered names."""
    import pandas as pd

    src = root / "cache" / "replay_fundamentals"
    files = sorted(src.glob("*.json"))
    print(f"fundamentals: {len(files):,} tickers", flush=True)
    recs = []
    t0 = time.time()
    for n, f in enumerate(files, 1):
        try:
            raw = json.loads(f.read_text())
        except Exception:
            continue
        merged: dict[str, dict] = {}
        for kind in ("income", "balance", "cashflow"):
            for r in (raw.get(kind) or []):
                d = str(r.get("date") or "")
                if not d or not r.get("acceptedDate"):
                    continue
                node = merged.setdefault(d, {"date": d, "_usable": None})
                node.update({k: v for k, v in r.items() if k not in ("date",)})
                u = _usable_date(r["acceptedDate"])
                # The statement set for a quarter becomes usable when its LAST
                # component is filed, not its first.
                if u is not None and (node["_usable"] is None or u > node["_usable"]):
                    node["_usable"] = u
        rows = sorted((v for v in merged.values() if v["_usable"]),
                      key=lambda r: r["date"])
        if not rows:
            continue
        usable = [r["_usable"] for r in rows]
        for ts in scan_dates:
            d = ts.date()
            known = [r for r, u in zip(rows, usable) if u <= d]
            if len(known) < 4:
                continue
            newest = list(reversed(known[-9:]))
            feats = _fund_features(newest)
            feats["ticker"] = f.stem
            feats["scan_date"] = ts
            recs.append(feats)
        if n % 500 == 0:
            print(f"  [{n}/{len(files)}] {time.time() - t0:.0f}s", flush=True)
    df = pd.DataFrame(recs)
    print(f"fundamental panel: {len(df):,} rows · {df.ticker.nunique():,} tickers "
          f"· {time.time() - t0:.0f}s", flush=True)
    return df

# ── fingerprints (Parts 4-5) ────────────────────────────────────────────────
#
# Each fingerprint is a hypothesis from the brief, written as a literal rule
# BEFORE it is scored, with round-number thresholds taken from the hypothesis
# rather than fitted to the outcome. Two are deliberate controls: a pure
# "big and calm" cohort that tests whether any apparent edge is just size and
# quiet, and the trap cohort itself, which should be strongly negative and is
# scored so that the failure mode is measured rather than assumed.
#
# `pct` below is a within-date percentile of the eligible universe, so a rule
# means the same thing in a bull week and a crash week.


def _pct(df, col: str):
    """Within-date percentile rank (0-1) of a column."""
    return df.groupby("scan_date")[col].rank(pct=True)


def _z(df, col: str):
    """Within-date z-score, robust to the fat tails in these columns."""
    g = df.groupby("scan_date")[col]
    med = g.transform("median")
    iqr = g.transform(lambda s: s.quantile(0.75) - s.quantile(0.25))
    return (df[col] - med) / iqr.replace(0, float("nan"))


FINGERPRINTS: list[dict] = [
    {
        "name": "anti_exhaustion_momentum",
        "hypothesis": "Part 4.1 — momentum that is improving but not yet extended; "
                      "explicitly outside the score=100 saturation zone.",
        "universe": "full",
        "requires": ["rs_63d", "rs_20d", "dist_ma50_pct", "combined_rs", "dist_ma200_pct"],
        "excludes": ["combined_rs >= 62.5 (the old score's saturation zone)",
                     "dist_ma50_pct > 20 (already extended)"],
        "mask": lambda d: (d.rs_63d.between(0, 30) & (d.rs_20d > 0)
                           & d.dist_ma50_pct.between(-5, 15)
                           & (d.combined_rs < 62.5) & (d.dist_ma200_pct > 0)),
        "rank": lambda d: _z(d, "rs_accel_20_63"),
    },
    {
        "name": "quiet_accumulation",
        "hypothesis": "Part 4.2 — volume rising while volatility stays controlled and "
                      "price is not yet euphoric.",
        "universe": "full",
        "requires": ["vol_expansion_5_63", "rvol_ratio_20_63", "rs_20d", "dist_ma50_pct"],
        "excludes": ["dd_from_high_pct < -40 (broken, not quiet)"],
        "mask": lambda d: ((d.vol_expansion_5_63 > 1.2) & (d.rvol_ratio_20_63 < 0.9)
                           & (d.rs_20d > 0) & d.dist_ma50_pct.between(-10, 10)
                           & (d.dd_from_high_pct > -40)),
        "rank": lambda d: _z(d, "vol_expansion_5_63"),
    },
    {
        "name": "pre_breakout_compression",
        "hypothesis": "Part 4.9 — volatility compression and a base, with volume "
                      "starting to expand and relative strength turning up.",
        "universe": "full",
        "requires": ["rvol_ratio_20_63", "dd_from_high_pct", "vol_expansion_5_63", "rs_20d"],
        "excludes": ["days_since_252d_high > 500 (a dead base, not a coiled one)"],
        "mask": lambda d: ((d.rvol_ratio_20_63 < 0.75) & (d.dd_from_high_pct > -25)
                           & (d.vol_expansion_5_63 > 1.1) & (d.rs_20d > -2)
                           & (d.days_since_252d_high <= 500)),
        "rank": lambda d: -_z(d, "rvol_ratio_20_63"),
    },
    {
        "name": "broken_stock_recovery",
        "hypothesis": "Part 4.4 — large drawdown, stabilising, reclaiming its MA50.",
        "universe": "full",
        "requires": ["dd_from_high_pct", "dist_ma50_pct", "rs_20d", "rvol_ratio_20_63"],
        "excludes": [],
        "mask": lambda d: ((d.dd_from_high_pct < -50) & (d.dist_ma50_pct > 0)
                           & (d.rs_20d > 0) & (d.rvol_ratio_20_63 < 1.0)),
        "rank": lambda d: _z(d, "rs_20d"),
    },
    {
        "name": "quality_at_reasonable_neglect",
        "hypothesis": "Part 4.5 — liquid, calm, near its highs, with moderate rather "
                      "than crowded relative strength.",
        "universe": "full",
        "requires": ["dvol_med_20", "rvol_63d_pct", "dd_from_high_pct", "combined_rs"],
        "excludes": ["combined_rs > 30 (crowded)", "atr top decile"],
        "mask": lambda d: ((_pct(d, "dvol_med_20") >= 0.60)
                           & (_pct(d, "rvol_63d_pct") <= 0.50)
                           & (d.dd_from_high_pct > -15)
                           & d.combined_rs.between(0, 30)
                           & (d.dist_ma50_pct < 12)),
        "rank": lambda d: _z(d, "rs_63d") - _z(d, "rvol_63d_pct"),
    },
    {
        "name": "liquid_low_volatility_control",
        "hypothesis": "CONTROL — 'just buy the big calm ones'. If a fingerprint cannot "
                      "beat this, its structure is adding nothing over size and quiet.",
        "universe": "full",
        "requires": ["dvol_med_20", "rvol_63d_pct"],
        "excludes": [],
        "mask": lambda d: ((_pct(d, "dvol_med_20") >= 0.70)
                           & (_pct(d, "rvol_63d_pct") <= 0.30)),
        "rank": lambda d: _z(d, "dvol_med_20"),
    },
    {
        "name": "trap_zone_control",
        "hypothesis": "CONTROL / Part 4.10 — the old score's saturation zone. Expected "
                      "strongly negative; scored so the failure is measured.",
        "universe": "full",
        "requires": ["combined_rs"],
        "excludes": [],
        "mask": lambda d: d.combined_rs >= 62.5,
        "rank": lambda d: _z(d, "combined_rs"),
    },
    {
        "name": "lottery_ticket_control",
        "hypothesis": "CONTROL / Part 4.10 — the cohort the naive winner autopsy points "
                      "at: cheap, volatile, deeply broken. Expected strongly negative.",
        "universe": "full",
        "requires": ["atr14_pct", "price", "dd_from_high_pct"],
        "excludes": [],
        "mask": lambda d: ((_pct(d, "atr14_pct") >= 0.90) & (d.price < 10)
                           & (d.dd_from_high_pct < -50)),
        "rank": lambda d: _z(d, "atr14_pct"),
    },
    {
        "name": "fundamental_inflection",
        "hypothesis": "Part 4.3 — revenue acceleration and margin improvement while "
                      "price has not yet re-rated.",
        "universe": "fundamentals_covered",
        "requires": ["rev_yoy_pct", "rev_accel_pp", "op_margin_delta_pp",
                     "dilution_yoy_pct", "dist_ma50_pct"],
        "excludes": ["dilution_yoy_pct > 10 (growth funded by issuance)"],
        "mask": lambda d: ((d.rev_yoy_pct > 10) & (d.rev_accel_pp > 0)
                           & (d.op_margin_delta_pp > 0) & (d.dilution_yoy_pct < 10)
                           & (d.dist_ma50_pct < 15)),
        "rank": lambda d: _z(d, "rev_accel_pp"),
    },
    {
        "name": "small_mid_operating_leverage",
        "hypothesis": "Part 4.6 — revenue growth plus margin expansion plus controlled "
                      "dilution, in a name still small enough for it to matter.",
        "universe": "fundamentals_covered",
        "requires": ["rev_yoy_pct", "op_margin_delta_pp", "dilution_yoy_pct", "dvol_med_20"],
        "excludes": [],
        "mask": lambda d: ((d.rev_yoy_pct > 20) & (d.op_margin_delta_pp > 2)
                           & (d.dilution_yoy_pct < 5)
                           & (_pct(d, "dvol_med_20").between(0.30, 0.90))),
        "rank": lambda d: _z(d, "op_margin_delta_pp"),
    },
    {
        "name": "asymmetric_balance_sheet_setup",
        "hypothesis": "Part 4.8 — limited downside from the balance sheet (net cash, "
                      "positive FCF) with early price recovery.",
        "universe": "fundamentals_covered",
        "requires": ["net_cash", "fcf_positive", "dist_ma50_pct", "rs_20d"],
        "excludes": [],
        "mask": lambda d: ((d.net_cash > 0) & (d.fcf_positive == 1.0)
                           & (d.dist_ma50_pct > 0) & (d.rs_20d > 0)
                           & (d.dd_from_high_pct < -20)),
        "rank": lambda d: _z(d, "net_cash"),
    },
    {
        "name": "profitable_quality_trend",
        "hypothesis": "Parts 4.5+4.6 combined — profitable, growing, not diluting, "
                      "trending but not extended.",
        "universe": "fundamentals_covered",
        "requires": ["profitable", "rev_yoy_pct", "dilution_yoy_pct", "combined_rs"],
        "excludes": ["combined_rs >= 62.5"],
        "mask": lambda d: ((d.profitable == 1.0) & (d.rev_yoy_pct > 5)
                           & (d.dilution_yoy_pct < 5) & d.combined_rs.between(0, 40)
                           & (d.dist_ma50_pct < 15) & (d.dd_from_high_pct > -30)),
        "rank": lambda d: _z(d, "rev_yoy_pct"),
    },
]

# Part 4.7 has no historical substrate in this repo and is declared untestable
# rather than approximated with today's sector labels, which would be lookahead.
UNTESTABLE_FINGERPRINTS: list[dict] = [
    {
        "name": "second_order_theme_beneficiary",
        "hypothesis": "Part 4.7 — smaller suppliers and adjacent names behind an "
                      "obvious theme leader.",
        "why_untestable": "Requires a point-in-time theme/sector map and a supply-chain "
                          "or co-movement graph for 2022-2025. The only sector labels "
                          "available are today's, which would leak both the sector "
                          "reclassification and the survivorship of the label itself. "
                          "No historical news/theme record exists in this repo for the "
                          "window. Approximating it would produce a number that looks "
                          "like evidence and is not.",
    },
    {
        "name": "single_source_hype_filter",
        "hypothesis": "Part 4.10 — de-weight names carried by one noisy social/news source.",
        "why_untestable": "Social attention, news catalyst and topic shock have no "
                          "historical record for the replay window; their seeds were "
                          "disabled in the price-only replay. Nothing here tests them in "
                          "either direction.",
    },
]

# ── fingerprint evaluation (Parts 5-6) ──────────────────────────────────────

TOP_N_VIEWS: tuple[int, ...] = (50, 100)

# Pre-declared promotion bar. Written before the numbers were seen, and
# deliberately hard: a fingerprint discovered by looking at these very years
# has to clear an out-of-sample test, two clustering schemes, a "big and calm"
# control, and the trap it was designed to avoid.
PROMOTION_RULES = {
    "min_train_episodes": 500,
    "min_dates_covered": 60,
    "train_selection_60d_positive_both_cis": True,
    "holdout_selection_60d_must_be": "> 0",
    "must_beat_old_scanner_board": True,
    "must_beat_liquid_low_vol_control": True,
    "trap_exposure_must_be_below_universe_rate": True,
    "min_regimes_positive": 2,
}


def evaluate_cohort(sub, universe, args, label: str) -> dict:
    """Every number a fingerprint cohort is judged on, for one slice of years."""
    import numpy as np

    if sub.empty:
        return {"episodes": 0}
    out = {
        "episodes": int(len(sub)),
        "unique_tickers": int(sub.ticker.nunique()),
        "dates_covered": int(sub.scan_date.nunique()),
        "median_names_per_date": float(sub.groupby("scan_date").size().median()),
        "share_of_universe_pct": round(100.0 * len(sub) / max(len(universe), 1), 2),
    }
    for h in (20, 45, 60):
        out[f"fwd_{h}d"] = cohort_stats(sub[f"fwd_{h}d"])
        out[f"selection_{h}d"] = selection_vs_universe(sub, universe, h,
                                                       args.bootstrap, args.seed)
    # Winner capture and the loser mirror of it.
    out["winner_capture"] = {
        "cohort_winner_rate_pct": round(float(sub[label].mean() * 100), 3),
        "universe_winner_rate_pct": round(float(universe[label].mean() * 100), 3),
        "lift": round(float(sub[label].mean() / max(universe[label].mean(), 1e-9)), 2),
        "share_of_all_winners_captured_pct": round(
            100.0 * float(sub[label].sum()) / max(float(universe[label].sum()), 1e-9), 2),
    }
    bottom = (universe.groupby("scan_date")[f"fwd_{PRIMARY_WINNER_H}d"]
              .rank(pct=True) * 100.0) <= 1.0
    out["false_positive_profile"] = {
        "cohort_bottom1pct_rate_pct": round(float(bottom.reindex(sub.index).mean() * 100), 3),
        "universe_bottom1pct_rate_pct": round(float(bottom.mean() * 100), 3),
        "share_down_more_than_25pct_60d": round(
            float((sub["fwd_60d"] < -25).mean() * 100), 2),
        "universe_share_down_more_than_25pct_60d": round(
            float((universe["fwd_60d"] < -25).mean() * 100), 2),
    }
    out["trap_exposure"] = {
        "cohort_in_score_100_zone_pct": round(float(sub["in_score_100_zone"].mean() * 100), 3),
        "universe_in_score_100_zone_pct": round(
            float(universe["in_score_100_zone"].mean() * 100), 3),
        "cohort_on_old_scanner_board_pct": round(float(sub["in_old_scanner"].mean() * 100), 2),
    }
    return out


def _selection_vs_cohort(sub, other, universe, h, args) -> dict | None:
    """Selection of *sub* minus selection of *other*, per date."""
    import numpy as np
    import pandas as pd

    if sub.empty or other.empty:
        return None
    a = sub.groupby("scan_date")[f"fwd_{h}d"].median()
    b = other.groupby("scan_date")[f"fwd_{h}d"].median()
    j = pd.DataFrame({"a": a, "b": b}).dropna()
    if len(j) < 10:
        return None
    diff = (j["a"] - j["b"]).to_numpy()
    return {
        "difference": round(float(diff.mean()), 3),
        "date_cluster_ci": _cluster_ci(diff, args.bootstrap, args.seed),
        "dates_won_pct": round(float((diff > 0).mean() * 100), 1),
        "n_dates": int(len(j)),
    }


def matched_control_selection(cohort, universe, h, args, seed_offset: int = 0) -> dict | None:
    """Cohort vs a same-date twin matched on volatility and liquidity decile.

    The strongest alternative explanation for anything found here is that the
    rule is a slow proxy for "big and calm" — the two features with the largest
    univariate effect in the train window. This draws, for every cohort episode,
    a non-member from the same scan date AND the same (volatility decile,
    liquidity decile) cell, then compares per-date medians. A rule that keeps
    its edge against its own vol/liquidity twin is doing something those two
    axes do not already do.
    """
    import numpy as np
    import pandas as pd

    if cohort.empty:
        return None
    u = universe.copy()
    u["_vd"] = u.groupby("scan_date")["rvol_63d_pct"].transform(
        lambda s: pd.qcut(s.rank(method="first"), 10, labels=False, duplicates="drop"))
    u["_ld"] = u.groupby("scan_date")["dvol_med_20"].transform(
        lambda s: pd.qcut(s.rank(method="first"), 10, labels=False, duplicates="drop"))
    u = u.dropna(subset=["_vd", "_ld"])
    in_cohort = u.index.isin(cohort.index)
    need = (u[in_cohort].groupby(["scan_date", "_vd", "_ld"], observed=True)
            .size().rename("k").reset_index())
    if need.empty:
        return None
    pool = u[~in_cohort]
    rng = np.random.default_rng(99 + seed_offset)
    picks = []
    for (d, vd, ld), grp in pool.groupby(["scan_date", "_vd", "_ld"], observed=True):
        row = need[(need.scan_date == d) & (need._vd == vd) & (need._ld == ld)]
        if row.empty:
            continue
        k = min(int(row.k.iloc[0]), len(grp))
        picks.append(grp.iloc[rng.choice(len(grp), size=k, replace=False)])
    if not picks:
        return None
    twin = pd.concat(picks)
    a = cohort.groupby("scan_date")[f"fwd_{h}d"].median()
    b = twin.groupby("scan_date")[f"fwd_{h}d"].median()
    j = pd.DataFrame({"a": a, "b": b}).dropna()
    if len(j) < 10:
        return None
    diff = (j["a"] - j["b"]).to_numpy()
    return {
        "difference": round(float(diff.mean()), 3),
        "date_cluster_ci": _cluster_ci(diff, args.bootstrap, args.seed),
        "dates_won_pct": round(float((diff > 0).mean() * 100), 1),
        "n_dates": int(len(j)),
        "twin_episodes": int(len(twin)),
    }


def _top_n_slice(sub, rank_series, n: int):
    """The n best-ranked names per scan date inside a cohort."""
    if sub.empty:
        return sub
    r = rank_series.reindex(sub.index)
    order = r.fillna(float("-inf")).groupby(sub["scan_date"]).rank(
        ascending=False, method="first")
    return sub[order <= n]


def verdict_for(res: dict) -> tuple[str, list[str]]:
    """Apply PROMOTION_RULES. Returns (verdict, reasons)."""
    tr, ho = res.get("train") or {}, res.get("holdout") or {}
    reasons: list[str] = []
    if not tr or tr.get("episodes", 0) == 0:
        return assert_fingerprint_verdict("INCONCLUSIVE"), ["no train episodes"]

    tsel = (tr.get("selection_60d") or {})
    hsel = (ho.get("selection_60d") or {})
    t_point = tsel.get("selection")
    h_point = hsel.get("selection")
    d_ci = tsel.get("date_cluster_ci") or [None, None]
    t_ci = tsel.get("ticker_cluster_ci") or [None, None]

    small = tr["episodes"] < PROMOTION_RULES["min_train_episodes"]
    thin_dates = tr.get("dates_covered", 0) < PROMOTION_RULES["min_dates_covered"]

    # Significantly negative in train or holdout -> negative selection, full stop.
    if (d_ci[1] is not None and d_ci[1] < 0) or (
            h_point is not None and (hsel.get("date_cluster_ci") or [None, None])[1] is not None
            and hsel["date_cluster_ci"][1] < 0):
        return assert_fingerprint_verdict("REJECTED_NEGATIVE_SELECTION"), [
            f"60d selection negative with CI excluding zero "
            f"(train {t_point}, holdout {h_point})"]

    if small:
        reasons.append(f"train sample {tr['episodes']} below "
                       f"{PROMOTION_RULES['min_train_episodes']}")
    if thin_dates:
        reasons.append(f"covers {tr.get('dates_covered')} dates, below "
                       f"{PROMOTION_RULES['min_dates_covered']}")

    train_pos = (d_ci[0] is not None and d_ci[0] > 0 and t_ci[0] is not None and t_ci[0] > 0)
    if not train_pos:
        reasons.append("train 60d selection not positive with both clustered CIs "
                       "excluding zero")
    holdout_pos = h_point is not None and h_point > 0
    if not holdout_pos:
        reasons.append(f"holdout 60d selection {h_point} not above zero")

    beats_board = (res.get("vs_old_scanner_board_60d") or {}).get("difference")
    beats_ctrl = (res.get("vs_liquid_lowvol_control_60d") or {}).get("difference")
    if beats_board is not None and beats_board <= 0:
        reasons.append("does not beat the old scanner board")
    if beats_ctrl is not None and beats_ctrl <= 0:
        reasons.append("does not beat the liquid/low-volatility control")

    trap = tr.get("trap_exposure") or {}
    if (trap.get("cohort_in_score_100_zone_pct") is not None
            and trap["cohort_in_score_100_zone_pct"] > trap.get("universe_in_score_100_zone_pct", 0)):
        reasons.append("carries more score=100 exposure than the universe")

    regimes = res.get("by_regime") or {}
    pos_regimes = sum(
        1 for v in regimes.values()
        if (v.get("selection_60d") or {}).get("selection", -1) > 0)
    if pos_regimes < PROMOTION_RULES["min_regimes_positive"]:
        reasons.append(f"positive in only {pos_regimes} of {len(regimes)} regimes")

    if train_pos and not holdout_pos:
        return assert_fingerprint_verdict("REJECTED_OVERFIT"), (
            ["train-only effect: positive in 2022-2024, not in the 2025 holdout"] + reasons)
    if not reasons:
        return assert_fingerprint_verdict("PROMISING_RESEARCH_FINGERPRINT"), [
            "clears every pre-declared promotion rule"]
    return assert_fingerprint_verdict("INCONCLUSIVE"), reasons


def evaluate_fingerprint(fp: dict, panel_full, panel_fund, args) -> dict:
    """Score one fingerprint on train, holdout, regimes, years, and top-N cuts."""
    import numpy as np

    label = f"top1pct_{PRIMARY_WINNER_H}d"
    universe = panel_fund if fp["universe"] == "fundamentals_covered" else panel_full
    try:
        mask = fp["mask"](universe).fillna(False)
    except Exception as e:  # a missing column is a result, not a crash
        return {"name": fp["name"], "error": f"{type(e).__name__}: {e}"}
    cohort = universe[mask]
    rank = fp["rank"](universe) if not cohort.empty else None

    res: dict = {
        "name": fp["name"],
        "hypothesis": fp["hypothesis"],
        "universe": fp["universe"],
        "universe_note": (
            "fundamentals-covered sub-universe — coverage is conditioned on the old "
            "scanner having surfaced the name, which is partly forward-looking; "
            "compared only against the same sub-universe"
            if fp["universe"] == "fundamentals_covered"
            else "full eligible universe"),
        "required_features": fp["requires"],
        "excluded_features": fp["excludes"],
        "total_episodes": int(len(cohort)),
    }
    if cohort.empty:
        res["verdict"], res["verdict_reasons"] = (
            assert_fingerprint_verdict("INCONCLUSIVE"), ["rule selected no episodes"])
        return res

    tr_u = universe[universe.year.isin(TRAIN_YEARS)]
    ho_u = universe[universe.year.isin(HOLDOUT_YEARS)]
    tr_c = cohort[cohort.year.isin(TRAIN_YEARS)]
    ho_c = cohort[cohort.year.isin(HOLDOUT_YEARS)]
    res["train"] = evaluate_cohort(tr_c, tr_u, args, label)
    res["holdout"] = evaluate_cohort(ho_c, ho_u, args, label)

    res["by_regime"] = {
        name: evaluate_cohort(cohort[cohort.year.isin(yrs)],
                              universe[universe.year.isin(yrs)], args, label)
        for name, yrs in REGIMES.items()
    }
    res["by_year"] = {
        str(int(y)): {
            "episodes": int((cohort.year == y).sum()),
            "selection_60d": (selection_vs_universe(
                cohort[cohort.year == y], universe[universe.year == y], 60,
                args.bootstrap, args.seed) or {}).get("selection"),
            "median_fwd_60d": (round(float(cohort[cohort.year == y]["fwd_60d"].median()), 3)
                               if (cohort.year == y).any() else None),
        }
        for y in sorted(universe.year.unique())
    }

    # Against the two cohorts that matter for "is this better than what exists".
    board = universe[universe["in_old_scanner"]]
    ctrl_mask = ((_pct(universe, "dvol_med_20") >= 0.70)
                 & (_pct(universe, "rvol_63d_pct") <= 0.30)).fillna(False)
    res["vs_old_scanner_board_60d"] = _selection_vs_cohort(cohort, board, universe, 60, args)
    res["vs_liquid_lowvol_control_60d"] = _selection_vs_cohort(
        cohort, universe[ctrl_mask], universe, 60, args)
    res["vs_vol_liquidity_matched_twin_60d"] = {
        "train": matched_control_selection(tr_c, tr_u, 60, args),
        "holdout": matched_control_selection(ho_c, ho_u, 60, args, seed_offset=1),
    }

    # Part 7's operating shape: what if you only took the best N per day?
    res["top_n"] = {}
    for n in TOP_N_VIEWS:
        top = _top_n_slice(cohort, rank, n)
        res["top_n"][f"top{n}"] = {
            "train": evaluate_cohort(top[top.year.isin(TRAIN_YEARS)], tr_u, args, label),
            "holdout": evaluate_cohort(top[top.year.isin(HOLDOUT_YEARS)], ho_u, args, label),
        }

    # A pure ranking rule (the composite) selects the whole universe by design,
    # so its own mask has no selection effect to judge. Its verdict is taken
    # from the top-100 cut, which is how it would actually be used.
    if fp.get("rank_only"):
        basis = {
            "train": res["top_n"]["top100"]["train"],
            "holdout": res["top_n"]["top100"]["holdout"],
            "by_regime": {
                name: evaluate_cohort(
                    _top_n_slice(cohort[cohort.year.isin(yrs)],
                                 fp["rank"](universe[universe.year.isin(yrs)]), 100),
                    universe[universe.year.isin(yrs)], args, label)
                for name, yrs in REGIMES.items()
            },
            "vs_old_scanner_board_60d": _selection_vs_cohort(
                _top_n_slice(cohort, rank, 100), board, universe, 60, args),
            "vs_liquid_lowvol_control_60d": _selection_vs_cohort(
                _top_n_slice(cohort, rank, 100), universe[ctrl_mask], universe, 60, args),
        }
        res["verdict_basis"] = "top100 cut (a ranking rule has no cohort of its own)"
        # Every downstream reader (report, tables) should see the cells the
        # verdict was actually taken from, not the whole-universe mask.
        res["full_mask_train"], res["full_mask_holdout"] = res["train"], res["holdout"]
        res["train"], res["holdout"] = basis["train"], basis["holdout"]
        res["by_regime"] = basis["by_regime"]
        res["vs_old_scanner_board_60d"] = basis["vs_old_scanner_board_60d"]
        res["vs_liquid_lowvol_control_60d"] = basis["vs_liquid_lowvol_control_60d"]
        res["vs_vol_liquidity_matched_twin_60d"] = {
            "train": matched_control_selection(
                _top_n_slice(tr_c, rank, 100), tr_u, 60, args),
            "holdout": matched_control_selection(
                _top_n_slice(ho_c, rank, 100), ho_u, 60, args, seed_offset=1),
        }
        res["verdict"], res["verdict_reasons"] = verdict_for(basis)
    else:
        res["verdict_basis"] = "the rule's own cohort"
        res["verdict"], res["verdict_reasons"] = verdict_for(res)

    # Applied after the pre-declared ladder, never folded into it: a rule that
    # cannot beat its own volatility/liquidity twin is a factor tilt.
    twin = (res.get("vs_vol_liquidity_matched_twin_60d") or {}).get("holdout") or {}
    res["beats_matched_twin_in_holdout"] = (
        None if twin.get("difference") is None else bool(twin["difference"] > 0))
    return res

# ── the composite score (Part 5) ────────────────────────────────────────────


def alpha_reconstruction_score(d):
    """A research-only ranking score, built from the train-window decile shapes.

    DISCOVERY-FITTED BY CONSTRUCTION: the sign and shape of every term was read
    off the 2022-2024 decile profiles in the autopsy, so its train performance
    is not evidence of anything. Only the 2025 holdout column is informative,
    and even that is one look at one year.

    It ranks names; it is not a signal, a probability, or a target weight. It
    exists so the "does any combination of these do better than its parts"
    question has a single answer instead of twelve.
    """
    # Each term is a within-date z-score, sign-oriented so that higher is the
    # side the train deciles favoured.
    calm = -_z(d, "rvol_63d_pct")                 # low volatility won
    liquid = _z(d, "dvol_med_20")                 # liquidity won
    near_high = _z(d, "dd_from_high_pct")         # near the 52w high won
    steady = -_z(d, "gap_abs_20d_pct")            # small overnight gaps won
    # Momentum entered as a hump, not a slope: the middle of the RS distribution
    # beat both tails, so distance from a modest positive RS is the penalty.
    moderate_rs = -(d["combined_rs"] - 8.0).abs().pipe(
        lambda s: (s - s.groupby(d["scan_date"]).transform("median"))
        / s.groupby(d["scan_date"]).transform(lambda x: x.quantile(0.75) - x.quantile(0.25)).replace(0, float("nan")))
    return (calm + liquid + near_high + steady + moderate_rs).fillna(float("-inf"))


COMPOSITE = {
    "name": "alpha_reconstruction_score_composite",
    "hypothesis": "Part 5 — the five train-window directions combined into one "
                  "ranking: calm, liquid, near-high, steady, moderately strong.",
    "universe": "full",
    "requires": ["rvol_63d_pct", "dvol_med_20", "dd_from_high_pct",
                 "gap_abs_20d_pct", "combined_rs"],
    "excludes": ["nothing structurally — the score ranks, the top-N cut selects"],
    "mask": lambda d: d["price"].notna(),
    "rank": alpha_reconstruction_score,
    "discovery_fitted": True,
    "rank_only": True,
}


def fingerprints_stage(args) -> int:
    import numpy as np
    import pandas as pd

    root = Path(args.root)
    tripwire = LiveArtifactTripwire.snapshot(root)
    df = load_panel(root)
    elig = df[df["eligible"]].copy()
    elig = label_winners(elig)
    elig = attach_controls(elig, root)

    # Fundamentals, joined for the fingerprints that need them.
    fund_path = root / f"{INTERMEDIATES_REL}/fundamental_panel.parquet"
    if fund_path.exists():
        fund = pd.read_parquet(fund_path)
    else:
        prices = load_prices(root, load_quarantine_excludes(root))
        scan_dates, _ = weekly_scan_dates(prices, args.start, args.end)
        del prices
        fund = build_fundamental_panel(root, scan_dates)
        from research.backtests.common import assert_replay_write_path
        assert_replay_write_path(fund_path, root=root)
        fund_path.parent.mkdir(parents=True, exist_ok=True)
        fund.to_parquet(fund_path, index=False, compression="zstd")
    fund["scan_date"] = pd.to_datetime(fund["scan_date"])
    panel_fund = elig.merge(fund, on=["ticker", "scan_date"], how="inner")
    print(f"full universe {len(elig):,} rows · fundamentals-covered "
          f"{len(panel_fund):,} rows ({panel_fund.ticker.nunique():,} tickers)",
          flush=True)

    results = []
    for fp in FINGERPRINTS + [COMPOSITE]:
        t0 = time.time()
        r = evaluate_fingerprint(fp, elig, panel_fund, args)
        if fp.get("discovery_fitted"):
            r["discovery_fitted"] = True
            r["discovery_note"] = (
                "Every term was chosen from the train-window decile profiles. "
                "Train numbers are in-sample by construction and carry no "
                "evidential weight; read the holdout column only.")
        results.append(r)
        sel = ((r.get("train") or {}).get("selection_60d") or {}).get("selection")
        hsel = ((r.get("holdout") or {}).get("selection_60d") or {}).get("selection")
        print(f"  {fp['name']:<38} n={r.get('total_episodes', 0):>7} "
              f"train_sel60={sel} holdout_sel60={hsel} -> {r.get('verdict')} "
              f"({time.time() - t0:.0f}s)", flush=True)

    out = {
        "kind": "ALPHA_RECONSTRUCTION_FINGERPRINTS",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "primary_horizon_days": PRIMARY_WINNER_H,
        "train_years": list(TRAIN_YEARS),
        "holdout_years": list(HOLDOUT_YEARS),
        "promotion_rules": PROMOTION_RULES,
        "clustering_note": (
            "The two confidence intervals answer different questions and can "
            "disagree without either being wrong. The date-clustered CI weights "
            "every scan date equally and asks 'was this a good week to hold this "
            "cohort'. The ticker-clustered CI weights every name equally and asks "
            "'was the typical name in it good'. A cohort that is large on bad weeks "
            "and small on good ones can pass one and fail the other. The promotion "
            "bar requires both precisely because a rule that only satisfies one is "
            "a composition effect, not a selection skill."),
        "multiplicity_warning": (
            f"{len(FINGERPRINTS) + 1} fingerprints were scored on the same window. "
            "At a 5% level roughly one would clear a single-test bar by chance, "
            "which is why the bar requires an out-of-sample year, two clustering "
            "schemes, and two control cohorts rather than one p-value."),
        "fingerprints": results,
        "untestable": UNTESTABLE_FINGERPRINTS,
        "verdict_counts": {},
        **provenance_flags(fundamentals=True),
    }
    for r in results:
        v = r.get("verdict", "INCONCLUSIVE")
        out["verdict_counts"][v] = out["verdict_counts"].get(v, 0) + 1
    assert_provenance(out)
    write_replay_json(root / FINGERPRINTS_REL, out, root=root)
    print(tripwire.report())
    tripwire.assert_clean()
    print("verdicts:", json.dumps(out["verdict_counts"]))
    return 0

# ── stage: report (Parts 7-8) ───────────────────────────────────────────────


def _fmt_sel(sel: dict | None) -> str:
    if not sel or sel.get("selection") is None:
        return "n/a"
    return (f"{sel['selection']:+.2f}pp "
            f"date-CI[{sel['date_cluster_ci'][0]}, {sel['date_cluster_ci'][1]}] "
            f"ticker-CI[{sel['ticker_cluster_ci'][0]}, {sel['ticker_cluster_ci'][1]}]")


def _cell(fp: dict, path: tuple) -> dict:
    node = fp
    for k in path:
        node = (node or {}).get(k) or {}
    return node


def report_stage(args) -> int:
    root = Path(args.root)
    tripwire = LiveArtifactTripwire.snapshot(root)
    win = json.loads((root / WINNERS_REL).read_text())
    aut = json.loads((root / AUTOPSY_REL).read_text())
    fps = json.loads((root / FINGERPRINTS_REL).read_text())
    by_name = {f["name"]: f for f in fps["fingerprints"]}

    L: list[str] = []
    A = L.append
    A("=" * 78)
    A("ALPHA RECONSTRUCTION LAB — what did the winners look like before they won?")
    A("=" * 78)
    A(f"Replay window {win['window'][0]} .. {win['window'][1]} · "
      f"{win['scan_dates']} weekly scan dates · generated "
      f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    A("")
    A("RESEARCH ONLY. Replay evidence over a fixed historical window. Not live")
    A("forward evidence, not Phase 4B input, not a gate/threshold/score change,")
    A("not a trade signal. Fingerprint verdicts sit on their own ladder and must")
    A("never be pooled with live program verdicts, HC/EO routing, or the board.")
    A("")

    # ── 1
    A("-" * 78)
    A("1. WINNER UNIVERSE")
    A("-" * 78)
    A(f"Eligible rows {win['eligible_rows']:,} · {win['eligible_tickers']:,} tickers · "
      f"median {win['median_eligible_per_date']:.0f} investable names per week.")
    A(f"Floor: price >= ${win['floor']['min_price']:.0f}, MEDIAN 20d dollar volume "
      f">= ${win['floor']['min_dvol']/1e6:.0f}M, >= {win['floor']['min_bars']} bars.")
    A("Floor sensitivity (median names/week): " + ", ".join(
        f"{k}={v['median_names_per_date']:.0f}"
        for k, v in win["floor_sensitivity"].items()))
    A("")
    pc = win["primary_cohort"]
    A(f"Primary cohort — {pc['definition']}:")
    A(f"  {pc['episodes']:,} winner episodes · {pc['unique_tickers']:,} unique tickers · "
      f"{pc['repeat_winners']:,} repeat vs {pc['one_time_winners']:,} one-time "
      f"({pc['repeat_share_of_episodes_pct']}% of episodes come from repeat names).")
    A("  By year: " + ", ".join(
        f"{y}: {v['episodes']} eps / {v['unique_tickers']} tickers "
        f"(median +{v['median_return']:.0f}%)" for y, v in pc["by_year"].items()))
    A("  By market cap: " + ", ".join(
        f"{k}={v}" for k, v in pc["by_mcap_bucket"]["winners"].items() if v))
    A("  By liquidity: " + ", ".join(
        f"{k}={v}" for k, v in pc["by_dvol_bucket"]["winners"].items()))
    sv = pc["survivor_vs_delisted"]
    A(f"  Delisted pool: {sv['delisted_share_of_universe_pct']}% of universe rows but "
      f"{sv['delisted_share_of_winners_pct']}% of winners.")
    dl = win["delisted_pool_outcome"]
    A(f"  Delisted names produced winners at {dl['top1pct_rate_delisted_pct']}% vs "
      f"{dl['top1pct_rate_survivors_pct']}% for survivors, while their trimmed mean "
      f"60d return was {dl['fwd_60d_delisted']['trim10']}% vs "
      f"{dl['fwd_60d_survivors']['trim10']}%. Fatter in BOTH tails — lottery tickets,")
    A("  not hidden gems. Dropping them would have flattered every cohort here.")
    A("")
    A("Threshold winners (path-based, max close inside the window):")
    for k, v in win["threshold_winners"].items():
        A(f"  {k:<14} {v['episodes']:>7,} episodes · {v['unique_tickers']:>5,} tickers "
          f"· {v['episode_rate_pct']}% of all rows")
    A("")

    # ── 2
    A("-" * 78)
    A("2. WHAT THE WINNERS LOOKED LIKE BEFOREHAND")
    A("-" * 78)
    A("Rank-AUC within each scan date; 0.50 is no separation. Train years only.")
    A("")
    for r in aut["feature_separation"][:12]:
        d = "higher" if r["auc_mean"] > 0.5 else "LOWER"
        A(f"  {r['feature']:<22} AUC {r['auc_mean']:.3f}  winners {d}  "
          f"(winner median {r['winner_median']} vs universe {r['universe_median']})")
    A("")
    A("Read plainly: the pre-move fingerprint of a top-1% winner is a small, cheap,")
    A("highly volatile, deeply drawn-down, illiquid stock a long way past its high —")
    A("with BELOW-average relative strength. Every momentum feature sits below 0.50,")
    A("i.e. mildly anti-predictive of being a big winner.")
    A("")
    lm = aut["loser_mirror"]
    A(f"THE DECISIVE CONTROL: the same features against the BOTTOM 1%.")
    A(f"  Correlation of winner-AUC and loser-AUC across features: "
      f"{lm['auc_correlation_winner_vs_loser']:+.3f}")
    A(f"  {lm['share_same_direction_pct']}% of features point the SAME way for both tails.")
    hv = aut["high_volatility_decile"]
    A(f"  Top ATR decile: winner rate {hv['winner_rate_pct']}% but loser rate "
      f"{hv['loser_rate_pct']}%, median 60d {hv['fwd_60d']['median']}%, "
      f"selection {hv['selection_60d']['selection']}pp.")
    A("")
    A("  => The winner fingerprint is a VOLATILITY fingerprint, not an alpha")
    A("     fingerprint. Reverse-engineering the biggest gainers and buying that")
    A("     description buys both tails, and the left tail is bigger.")
    A("")

    # ── 3
    A("-" * 78)
    A("3. PATTERNS THAT FAILED")
    A("-" * 78)
    for name in ("broken_stock_recovery", "quiet_accumulation", "pre_breakout_compression",
                 "asymmetric_balance_sheet_setup", "anti_exhaustion_momentum"):
        f = by_name.get(name)
        if not f:
            continue
        A(f"  {name:<32} {f['verdict']}")
        A(f"      train {_fmt_sel(_cell(f, ('train', 'selection_60d')))}")
        A(f"      hold  {_fmt_sel(_cell(f, ('holdout', 'selection_60d')))}")
    A("")
    A("  'Buy the broken stock as it stabilises' was the worst of the hypotheses:")
    A("  strongly negative selection in the train window with the CI clear of zero.")
    A("  'Quiet accumulation' and 'pre-breakout compression' were flat in train and")
    A("  negative in the holdout — volume-expansion features showed AUC ~0.50, i.e.")
    A("  no separation at all, in either direction.")
    A("")

    # ── 4-5
    A("-" * 78)
    A("4. OVERLAP WITH THE OLD SCANNER, AND 5. THE score=100 TRAP")
    A("-" * 78)
    ov = aut["old_scanner_overlap"]
    A(f"  Of {ov['winner_episodes']:,} winner episodes the old replay board ever saw "
      f"{ov['seen_by_old_scanner_pct']}%")
    A(f"  ({ov['unique_winner_tickers_ever_seen_pct']}% of unique winner tickers). HC "
      f"held {ov['in_hc_shortlist_pct']}%, EO {ov['in_eo_watch_pct']}%.")
    z = aut["score_100_zone"]
    A(f"  Replicated saturation zone (combined_rs >= 62.5) measured on the FULL")
    A(f"  universe, not just the board: {z['in_score_100_zone']['share_of_universe_pct']}% "
      f"of rows, winner rate {z['in_score_100_zone']['winner_rate_pct']}% "
      f"(vs {z['outside_score_100_zone']['winner_rate_pct']}% outside) —")
    A(f"  a {z['in_score_100_zone']['winner_rate_pct'] / max(z['outside_score_100_zone']['winner_rate_pct'], 1e-9):.1f}x winner-rate lift — and a median 60d return of "
      f"{z['in_score_100_zone']['fwd_60d']['median']}% against "
      f"{z['outside_score_100_zone']['fwd_60d']['median']}%.")
    tz = by_name.get("trap_zone_control", {})
    A(f"  As a fingerprint: {tz.get('verdict')} — train "
      f"{_fmt_sel(_cell(tz, ('train', 'selection_60d')))}")
    A(f"                     holdout {_fmt_sel(_cell(tz, ('holdout', 'selection_60d')))}")
    A("")
    A("  This reframes the trap. It was not merely 'chasing extension': the zone is a")
    A("  HIGH-WINNER-RATE bucket. It genuinely contains five times the normal density")
    A("  of top-1% winners. It loses money anyway, because it contains even more of")
    A("  the bottom 1%. The old score was not selecting badly at random — it was")
    A("  selecting variance and being paid the left tail for it.")
    A("")

    # ── 6
    A("-" * 78)
    A("6. THE FINGERPRINTS")
    A("-" * 78)
    A(f"{'fingerprint':<34} {'verdict':<32} {'train 60d':>10} {'holdout':>9} {'twin(ho)':>9}")
    for f in fps["fingerprints"]:
        tw = (_cell(f, ("vs_vol_liquidity_matched_twin_60d", "holdout")) or {}).get("difference")
        A(f"  {f['name'][:32]:<32} {f.get('verdict', ''):<32} "
          f"{str(_cell(f, ('train', 'selection_60d')).get('selection')):>10} "
          f"{str(_cell(f, ('holdout', 'selection_60d')).get('selection')):>9} "
          f"{str(tw):>9}")
    A("")
    A("  'twin' = selection against a same-date cohort matched on volatility decile")
    A("  and liquidity decile. A rule that cannot beat its own twin is a factor tilt")
    A("  wearing a rule's clothes. The composite is a ranking rule with no cohort of")
    A("  its own, so its row is its top-100 cut.")
    A("")
    A("  On the two CIs, which disagree in several rows below: the date-clustered one")
    A("  weights every week equally and asks whether this was a good week to hold the")
    A("  cohort; the ticker-clustered one weights every name equally and asks whether")
    A("  the typical name was good. A cohort that is large in bad weeks and small in")
    A("  good ones passes one and fails the other. That is a composition effect, and")
    A("  requiring both is how it gets caught.")
    A("")
    for name in ("fundamental_inflection", "profitable_quality_trend",
                 "small_mid_operating_leverage", "alpha_reconstruction_score_composite"):
        f = by_name.get(name)
        if not f:
            continue
        A(f"  {name} — {f['verdict']}")
        A(f"    {f['hypothesis']}")
        A(f"    universe: {f['universe_note']}")
        A(f"    train   {_fmt_sel(_cell(f, ('train', 'selection_60d')))}")
        A(f"    holdout {_fmt_sel(_cell(f, ('holdout', 'selection_60d')))}")
        for reason in f.get("verdict_reasons", []):
            A(f"    - {reason}")
        A("")

    # ── 7
    A("-" * 78)
    A("7. HOLDOUT PERFORMANCE (2025, evaluated once)")
    A("-" * 78)
    A("Every price-only rule that looked positive in 2022-2024 went flat or negative")
    A("in 2025, INCLUDING the 'big and calm' control:")
    for name in ("liquid_low_volatility_control", "quality_at_reasonable_neglect",
                 "anti_exhaustion_momentum"):
        f = by_name.get(name, {})
        A(f"  {name:<32} train "
          f"{str(_cell(f, ('train', 'selection_60d')).get('selection')):>7} -> holdout "
          f"{str(_cell(f, ('holdout', 'selection_60d')).get('selection')):>7}")
    A("")
    A("The fundamental-quality family held its sign in the holdout, and is the only")
    A("family that did. It is also the family with the weakest data provenance.")
    A("")

    # ── 8
    A("-" * 78)
    A("8. CAN ANY FINGERPRINT FIND THE BEST 50-100 NAMES?  NO.")
    A("-" * 78)
    A(f"{'fingerprint (top-50/day, holdout)':<40} {'selection':>10} {'win%':>7} "
      f"{'winner lift':>12} {'% of winners':>13}")
    for f in fps["fingerprints"]:
        cell = _cell(f, ("top_n", "top50", "holdout"))
        if not cell.get("episodes"):
            continue
        wc = cell["winner_capture"]
        A(f"  {f['name']:<38} "
          f"{str((cell.get('selection_60d') or {}).get('selection')):>10} "
          f"{cell['fwd_60d']['win_rate']:>7} {wc['lift']:>12} "
          f"{wc['share_of_all_winners_captured_pct']:>13}")
    A("")
    A("The trade-off is the finding. Cohorts that CATCH winners (lottery ticket: 6.5x")
    A("lift, 10.5% of all winners; trap zone: 5.6x, 7.8%) lose money. Cohorts with")
    A("positive selection hold FOUR TIMES FEWER winners than random (composite top-50")
    A("lift 0.27, capturing 0.4% of them). Over this window the two objectives are")
    A("not merely different — they are opposed. A daily list of 50 names that beats")
    A("the market modestly and a daily list of 50 names containing the year's")
    A("moonshots are two different products, and this data supports building only")
    A("the first, weakly.")
    A("")

    # ── 9
    A("-" * 78)
    A("9. WHAT DATA IS STILL MISSING")
    A("-" * 78)
    for cat, items in aut["feature_provenance"].items():
        A(f"  [{cat}]")
        for it in items:
            A(f"    - {it}")
    A("")
    for u in fps["untestable"]:
        A(f"  UNTESTABLE — {u['name']}: {u['why_untestable']}")
    A("")

    # ── 7 (daily design) + 10
    A("-" * 78)
    A("10. DAILY USAGE DESIGN, AND WHAT THE PROJECT SHOULD DO")
    A("-" * 78)
    A("What this evidence supports building, in order of how well it is supported:")
    A("")
    A("  (a) AN AVOID LIST — the best-supported product here. Two cohorts are")
    A("      reliably negative across train AND holdout with CIs clear of zero:")
    A("      the score=100 saturation zone and the cheap/volatile/broken cohort.")
    A("      Both are large, easy to compute daily, and negative for a mechanical")
    A("      reason (variance, not sentiment). Surfacing 'this name sits in a")
    A("      historically money-losing bucket' is defensible today.")
    A("")
    A("  (b) A SHADOW RESEARCH LIST from the fundamental-quality direction, ranked,")
    A("      capped at 50-100 names, explicitly NOT a moonshot finder. Its measured")
    A("      effect is ~+1 to +3pp per 60 days against the same-date universe, it")
    A("      survives a vol/liquidity-matched twin in the holdout, and it holds four")
    A("      times fewer big winners than random. Forward-only, no promotion path")
    A("      until it has independent forward evidence.")
    A("")
    A("  (c) NOT a replacement selector. Nothing here earns the right to rank the")
    A("      board or to feed conviction. The one PROMISING verdict sits on a")
    A("      coverage-biased sub-universe and its holdout date-CI includes zero.")
    A("")
    A("On the four options in the brief:")
    A("  - Repair around new fingerprints:  NOT YET. No fingerprint cleared the bar")
    A("    on the full universe. The quality family is a candidate for forward")
    A("    tracking, not a repair blueprint.")
    A("  - Preserve long_term_asymmetric / EO non-score-100:  CONSISTENT with this")
    A("    work. Both are low-extension, non-trap cohorts, and the trap remains the")
    A("    single most reliably negative thing measured in either study.")
    A("  - Sunset the broad scanner as an ALPHA RANKER:  SUPPORTED. Two independent")
    A("    studies now show its ranking axis is inverted. Keep it as a discovery")
    A("    net if it is cheap; stop reading its order as conviction.")
    A("  - Continue forward-only social/topic shock:  UNAFFECTED. No historical")
    A("    substrate exists; this study says nothing about it in either direction.")
    A("")
    A("=" * 78)
    A("BOTTOM LINE")
    A("=" * 78)
    A("The winners are not describable in advance by the features available here.")
    A("What is describable in advance is which names are likely to be VOLATILE, and")
    A("volatility pays the left tail more often than the right. The one direction")
    A("with a positive, holdout-surviving sign is dull fundamental quality, on the")
    A("least trustworthy data in the study, at an effect size that a transaction")
    A("cost model could plausibly erase. Nothing here is a validated edge, and")
    A("nothing here should change a live rule.")
    A("")

    text = "\n".join(L)
    write_replay_text(root / REPORT_TXT_REL, text + "\n", root=root)

    summary = {
        "kind": "ALPHA_RECONSTRUCTION_LATEST",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window": win["window"],
        "headline": (
            "The pre-move fingerprint of a top-1% winner is a volatility fingerprint: "
            "winner-AUC and loser-AUC across features correlate "
            f"{lm['auc_correlation_winner_vs_loser']:+.2f}. No fingerprint both catches "
            "winners and earns positive selection; over this window the two objectives "
            "are opposed."),
        "winner_universe": {k: win[k] for k in
                            ("eligible_rows", "eligible_tickers", "scan_dates",
                             "median_eligible_per_date")},
        "primary_cohort_episodes": win["primary_cohort"]["episodes"],
        "loser_mirror_correlation": lm["auc_correlation_winner_vs_loser"],
        "verdict_counts": fps["verdict_counts"],
        "fingerprint_verdicts": {f["name"]: f.get("verdict") for f in fps["fingerprints"]},
        "best_supported_products": [
            "avoid-list from the score=100 saturation zone and the cheap/volatile/broken "
            "cohort (negative in train and holdout, CIs clear of zero)",
            "shadow-only ranked research list from the fundamental-quality direction "
            "(~+1 to +3pp / 60d, survives a vol/liquidity-matched twin in holdout, "
            "coverage-biased sub-universe)",
        ],
        "not_supported": [
            "any claim that a daily 50-100 name list can contain the period's big winners",
            "any ranking, gate, threshold, score, HC/EO, Alpha Focus or routing change",
            "any promotion of a fingerprint to a signal",
        ],
        "report_txt": REPORT_TXT_REL,
        "artifacts": [WINNERS_REL, AUTOPSY_REL, FINGERPRINTS_REL],
        **provenance_flags(fundamentals=True),
    }
    assert_provenance(summary)
    write_replay_json(root / LATEST_REL, summary, root=root)
    print(tripwire.report())
    tripwire.assert_clean()
    print(text)
    return 0



if __name__ == "__main__":
    raise SystemExit(main())

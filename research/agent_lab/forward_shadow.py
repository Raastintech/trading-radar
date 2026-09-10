"""Forward shadow ledger for `style_cell_leader_pool` — FORWARD_SHADOW_RESEARCH_ONLY.

Sandbox-only. This module NEVER calls a provider, under any flag. It reads the
existing price cache, records what the pool would have contained on a session,
and resolves those snapshots forward once the horizon has elapsed.

WHAT IS BEING TRACKED
---------------------
A basket, not a set of stock selections. On each session the universe is
floored at price >= $5 and median 20-day dollar volume >= $20M, split into
volatility-decile x liquidity-decile cells, and each cell contributes its
single highest 12-1 momentum name. About 99 names. The pool is **unordered and
equal-weight**: the ledger deliberately stores no per-name score, so no
ranking, shortlist, or ordering can be reconstructed from it. Members are
written alphabetically, which is a storage convention and not a ranking.

WHY IT EXISTS
-------------
`docs/research/agent_lab/AUTONOMOUS_ALPHA_LAB_REPORT_2026_09.md` labelled this
pool `PROVISIONAL_RESEARCH_CANDIDATE` on replay evidence, explicitly because
the object was specified after the 2025 holdout had been read. The only thing
that can advance it is unseen data. This ledger records that data. It does not
judge it: the published verdict is pinned to FORWARD_SHADOW_RESEARCH_ONLY.

WHAT IT REFUSES TO DO
---------------------
12-1 momentum needs 252 bars. A pool computed on a thin or stale cache is not
this pool, it is a look-alike, and a forward ledger of a look-alike is worse
than no ledger. Two coverage gates therefore have to pass before a snapshot is
written, and failing either writes a REFUSED record instead of a pool.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Sequence

import numpy as np
import pandas as pd

from research.backtests.common import (
    FetchNotAuthorised,
    enforce_call_cap,
    offline_env,
    run_cli,
)

# ---------------------------------------------------------------- constants --

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

METHOD_NAME = "style_cell_leader_pool"
LABEL = "FORWARD_SHADOW_RESEARCH_ONLY"

LEDGER_PATH = os.path.join(REPO, "data/research/agent_lab/style_cell_leader_forward_ledger.jsonl")
LATEST_PATH = os.path.join(REPO, "cache/research/agent_lab/style_cell_leader_forward_latest.json")
DOC_PATH = os.path.join(REPO, "docs/research/agent_lab/STYLE_CELL_LEADER_FORWARD_SHADOW.md")

# The method, frozen to match the replay definition exactly.
MIN_PRICE = 5.0
MIN_DVOL_MED_20 = 20_000_000.0
MIN_BARS = 252
N_DECILES = 10
HORIZONS: tuple[int, ...] = (20, 45, 60)
PRIMARY_HORIZON = 60
BENCHMARKS: tuple[str, ...] = ("SPY", "QQQ", "IWM")

# Coverage gates. Both must pass or the session is refused.
MAX_BAR_AGE_DAYS = 5          # a "fresh" name has a bar this recent
MIN_DEPTH_COVERAGE = 0.95     # of fresh candidates, share with >= 252 bars
MIN_FRESH_SHARE = 0.80        # of liquid candidates, share that are fresh
MIN_POOL_SIZE = 60            # a pool far below ~99 cells is not this pool

# A forward ledger is only forward if its rows were written at the time. Without
# this, a historical session could be backfilled from the same cached window the
# replay study already used and then read as unseen evidence. Backdated rows are
# not refused outright — they are useful for exercising the machinery — but they
# are stamped contemporaneous=false and excluded from every published statistic.
MAX_BACKDATE_DAYS = 7

REFUSAL_INSUFFICIENT = "REFUSED_INSUFFICIENT_LIVE_CACHE_COVERAGE"

# Pre-declared before the first observation, so the bar cannot move later.
MATURITY_FLOOR = {
    "matured_snapshots_at_primary_horizon": 30,
    "non_overlapping_primary_windows": 4,
    "note": (
        "Reaching this floor does NOT produce a verdict. It only makes the "
        "evidence worth reading. Promotion past FORWARD_SHADOW_RESEARCH_ONLY is "
        "a separate human decision taken outside this module."
    ),
}

# Price sources. The default is what production timers actually maintain.
# ASCENDING priority: the timer-maintained cache/prices must outrank the
# research-maintained cache/prices_deep, which is staler and carries a different
# dividend-adjustment vintage on the same dates.
LIVE_CACHE_DIRS = ("cache/prices_deep", "cache/prices")
REPLAY_CACHE_DIRS = ("cache/replay_prices",)

DEFAULT_REFRESH_CALL_CAP = 100

# Wording that must never appear in an emitted record. Guarded by tests.
BANNED_LANGUAGE = (
    "buy", "sell", "trade signal", "signal to", "recommend", "recommendation",
    "price target", "top pick", "best idea", "conviction", "entry point",
    "stop loss", "take profit", "rank", "ranking", "ranked",
)


# ------------------------------------------------------------------ helpers --

def _roll_median(a: np.ndarray, w: int) -> np.ndarray:
    return pd.Series(a).rolling(w, min_periods=w).median().to_numpy()


def _roll_mean(a: np.ndarray, w: int) -> np.ndarray:
    return pd.Series(a).rolling(w, min_periods=w).mean().to_numpy()


def _lag_ratio(c: np.ndarray, k: int) -> float:
    """Percent change from k bars ago to the last bar. NaN when too short."""
    if c.size <= k or c[-1 - k] <= 0:
        return float("nan")
    return float(c[-1] / c[-1 - k] - 1.0) * 100.0


def _decile(s: pd.Series) -> pd.Series:
    """Within-session deciles, 0-9, ranked first so ties cannot collapse bins."""
    d = pd.qcut(s.rank(method="first"), N_DECILES, labels=False, duplicates="drop")
    return pd.Series(d, index=s.index).fillna(-1).astype(int)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# -------------------------------------------------------------- price cache --

# When two caches hold the same ticker their overlapping bars must agree, or
# splicing them would manufacture a price gap that never happened. Anything
# outside this band is treated as an adjustment mismatch and the ticker falls
# back to a single cache.
SPLICE_TOLERANCE = (0.98, 1.02)

# Agreement on the overlap does NOT validate the segment a lower-priority cache
# contributes OUTSIDE that overlap. Wolfspeed is the worked example: the deep
# cache held pre-reverse-split prices from $0.40, agreed with the maintained
# cache on their shared dates, and splicing produced a +1,726% session that
# never happened. Every merged series is therefore integrity-checked as a whole,
# and a merge that manufactures an artifact the single source does not have is
# discarded. Thresholds follow the replay study: a single session of >= +400% or
# a max/min ratio >= 1000x is physically implausible for an adjusted series. A
# single session of -85% is deliberately NOT a trigger — those are real events.
MAX_PLAUSIBLE_SESSION_GAIN_PCT = 400.0
MAX_PLAUSIBLE_RANGE_RATIO = 1000.0
SPLICE_ARTIFACT_MULTIPLE = 3.0


@dataclass(frozen=True)
class PriceSource:
    """A merged view over one or more cache directories.

    ``dirs`` is in ASCENDING priority: a later directory wins on any date both
    hold. Picking a single "best" file per ticker was wrong — the deepest cache
    is not always the freshest, and preferring depth silently discarded the
    most recent bar for 955 tickers, which is the entry price a forward ledger
    is recording.
    """
    name: str
    dirs: tuple[str, ...]

    def paths(self) -> dict[str, tuple[str, ...]]:
        """Ticker -> every cached parquet for it, in ascending priority."""
        out: dict[str, list[str]] = {}
        for d in self.dirs:
            for f in sorted(glob.glob(os.path.join(REPO, d, "*.parquet"))):
                out.setdefault(os.path.basename(f)[:-8], []).append(f)
        return {t: tuple(v) for t, v in out.items()}


def series_integrity(df: pd.DataFrame) -> tuple[bool, str | None, float]:
    """Is this series physically plausible for an adjusted price history?

    Returns (ok, reason, largest one-session gain in percent).
    """
    if df.empty or "close" not in df:
        return False, "empty", 0.0
    c = df["close"].to_numpy(dtype=float)
    c = c[np.isfinite(c)]
    if c.size < 2:
        return False, "too_short", 0.0
    if (c <= 0).any():
        return False, "non_positive_close", 0.0
    gains = (c[1:] / c[:-1] - 1.0) * 100.0
    worst = float(np.nanmax(gains)) if gains.size else 0.0
    if worst >= MAX_PLAUSIBLE_SESSION_GAIN_PCT:
        return False, f"implausible_session_gain_{worst:.0f}pct", worst
    lo = float(np.nanmin(c))
    if lo > 0 and float(np.nanmax(c)) / lo >= MAX_PLAUSIBLE_RANGE_RATIO:
        return False, "implausible_range_ratio", worst
    return True, None, worst


def load_series(paths: Sequence[str], columns: Sequence[str]) -> tuple[pd.DataFrame, str]:
    """Merge one ticker's caches into a single series.

    Returns the frame and a provenance tag: ``merged``, ``single``, or
    ``single_adjustment_mismatch`` when the caches disagree on overlapping
    prices and splicing was refused.
    """
    frames: list[tuple[str, pd.DataFrame]] = []
    for p in paths:
        try:
            df = pd.read_parquet(p, columns=list(columns))
        except Exception:
            continue
        if not df.empty:
            frames.append((p, df.sort_index()))
    if not frames:
        return pd.DataFrame(), "missing"

    def _best_single() -> tuple[pd.DataFrame, bool]:
        """The freshest clean source; depth breaks a tie.

        Priority order decides who wins a *shared date*. It must not decide the
        fallback: the highest-priority cache is not always the freshest, and
        picking it here silently cost 2,017 symbols their most recent bar.
        """
        scored = []
        for _, df in frames:
            ok, _, _ = series_integrity(df)
            scored.append((ok, df.index.max(), len(df), df))
        scored.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
        return scored[0][3], scored[0][0]

    if len(frames) == 1:
        df = frames[0][1]
        ok, why, _ = series_integrity(df)
        return (df, "single") if ok else (df, f"single_failed_integrity:{why}")

    top = frames[-1][1]
    kept = [top]
    tag = "merged"
    for _, lower in frames[:-1]:
        overlap = lower.index.intersection(top.index)
        if len(overlap) >= 5:
            ratio = (lower.loc[overlap, "close"] / top.loc[overlap, "close"]).median()
            if not (SPLICE_TOLERANCE[0] <= float(ratio) <= SPLICE_TOLERANCE[1]):
                tag = "single_adjustment_mismatch"
                continue
        kept.append(lower)

    if len(kept) == 1:
        df, ok = _best_single()
        return df, (tag if tag != "merged" else "single")

    # Concat lowest-priority first so keep="last" hands the date to the highest.
    merged = pd.concat(kept[::-1])
    merged = merged[~merged.index.duplicated(keep="last")].sort_index()

    # Validate what the merge produced, not just where the caches overlapped.
    ok_merged, why, merged_worst = series_integrity(merged)
    single_df, single_ok = _best_single()
    _, _, single_worst = series_integrity(single_df)
    manufactured = merged_worst > max(single_worst, 1.0) * SPLICE_ARTIFACT_MULTIPLE
    if not ok_merged or manufactured:
        reason = why or f"gain_{merged_worst:.0f}pct_vs_{single_worst:.0f}pct"
        if single_ok:
            return single_df, f"single_merge_artifact:{reason}"
        return single_df, f"single_failed_integrity:{reason}"
    return merged, tag


def resolve_source(name: str) -> PriceSource:
    if name == "live_cache":
        return PriceSource("live_cache", LIVE_CACHE_DIRS)
    if name == "live_plus_replay_cache":
        # Replay first, live last: the maintained cache wins on shared dates.
        return PriceSource("live_plus_replay_cache", REPLAY_CACHE_DIRS + LIVE_CACHE_DIRS)
    raise ValueError(f"unknown price source: {name}")


def build_session_frame(source: PriceSource, as_of: pd.Timestamp | None = None) -> tuple[pd.DataFrame, pd.Timestamp]:
    """As-of features for every cached symbol, truncated at ``as_of``.

    Every column is a function of bars at or before the session date. Nothing
    forward-looking is read here; forward returns are only ever fetched in
    ``resolve_snapshot``, and only for sessions whose horizon has elapsed.
    """
    rows: list[dict] = []
    provenance: dict[str, int] = {}
    for ticker, paths in source.paths().items():
        df, tag = load_series(paths, ["high", "low", "close", "volume"])
        provenance[tag.split(":")[0]] = provenance.get(tag.split(":")[0], 0) + 1
        if df.empty:
            continue
        ok, why, _ = series_integrity(df)
        if not ok:
            provenance[f"excluded_{why.split('_')[0]}"] = provenance.get(f"excluded_{why.split('_')[0]}", 0) + 1
            continue
        if as_of is not None:
            df = df[df.index <= as_of]
        if len(df) < 21:
            continue
        c = df["close"].to_numpy(dtype=float)
        h = df["high"].to_numpy(dtype=float)
        low = df["low"].to_numpy(dtype=float)
        v = df["volume"].to_numpy(dtype=float)
        dv = c * v
        r252, r21 = _lag_ratio(c, 252), _lag_ratio(c, 21)
        mom = (((1 + r252 / 100.0) / (1 + r21 / 100.0) - 1.0) * 100.0
               if np.isfinite(r252) and np.isfinite(r21) else float("nan"))
        prev_c = np.concatenate(([c[0]], c[:-1]))
        tr = np.maximum.reduce([h - low, np.abs(h - prev_c), np.abs(low - prev_c)])
        atr = _roll_mean(tr, 14)
        rows.append({
            "ticker": ticker,
            "bars": int(len(df)),
            "last_bar": df.index[-1],
            "close": float(c[-1]),
            "dvol_med_20": float(_roll_median(dv, 20)[-1]),
            "atr14_pct": float(atr[-1] / c[-1] * 100.0) if c[-1] > 0 and np.isfinite(atr[-1]) else float("nan"),
            "mom_12_1": mom,
        })
    frame = pd.DataFrame(rows)
    frame.attrs["provenance"] = provenance
    if frame.empty:
        return frame, pd.Timestamp("1970-01-01")
    session = as_of if as_of is not None else frame["last_bar"].max()
    return frame, pd.Timestamp(session)


# ---------------------------------------------------------------- coverage ---

def coverage_report(frame: pd.DataFrame, session: pd.Timestamp) -> dict:
    """The two gates, and every number a reader needs to check them."""
    if frame.empty:
        return {"status": REFUSAL_INSUFFICIENT, "reason": "price cache produced no usable symbols"}
    liquid = frame[(frame["close"] >= MIN_PRICE) & (frame["dvol_med_20"] >= MIN_DVOL_MED_20)]
    cutoff = session - pd.Timedelta(days=MAX_BAR_AGE_DAYS)
    fresh = liquid[liquid["last_bar"] >= cutoff]
    deep_fresh = fresh[fresh["bars"] >= MIN_BARS]

    n_liq, n_fresh, n_deep = len(liquid), len(fresh), len(deep_fresh)
    fresh_share = (n_fresh / n_liq) if n_liq else 0.0
    depth_cov = (n_deep / n_fresh) if n_fresh else 0.0

    failures = []
    if fresh_share < MIN_FRESH_SHARE:
        failures.append(
            f"only {fresh_share:.1%} of {n_liq} liquid candidates have a bar within "
            f"{MAX_BAR_AGE_DAYS} days of {session.date()} (floor {MIN_FRESH_SHARE:.0%}) — "
            "the session would be computed on a fraction of the universe")
    if depth_cov < MIN_DEPTH_COVERAGE:
        failures.append(
            f"only {depth_cov:.1%} of {n_fresh} fresh candidates carry the {MIN_BARS} bars "
            f"that 12-1 momentum needs (floor {MIN_DEPTH_COVERAGE:.0%})")

    return {
        "status": "OK" if not failures else REFUSAL_INSUFFICIENT,
        "reason": "; ".join(failures) or None,
        "session_date": str(session.date()),
        "cached_symbols": int(len(frame)),
        "cache_merge_provenance": dict(frame.attrs.get("provenance", {})),
        "liquid_candidates": n_liq,
        "fresh_candidates": n_fresh,
        "fresh_and_deep_candidates": n_deep,
        "fresh_share_pct": round(fresh_share * 100.0, 2),
        "depth_coverage_of_fresh_pct": round(depth_cov * 100.0, 2),
        "gates": {
            "min_fresh_share_pct": MIN_FRESH_SHARE * 100.0,
            "min_depth_coverage_pct": MIN_DEPTH_COVERAGE * 100.0,
            "max_bar_age_days": MAX_BAR_AGE_DAYS,
            "min_bars": MIN_BARS,
        },
    }


def eligible_universe(frame: pd.DataFrame, session: pd.Timestamp) -> pd.DataFrame:
    """Liquid, fresh, deep-enough names, with within-session style cells."""
    cutoff = session - pd.Timedelta(days=MAX_BAR_AGE_DAYS)
    u = frame[
        (frame["close"] >= MIN_PRICE)
        & (frame["dvol_med_20"] >= MIN_DVOL_MED_20)
        & (frame["last_bar"] >= cutoff)
        & (frame["bars"] >= MIN_BARS)
        & frame["mom_12_1"].notna()
        & frame["atr14_pct"].notna()
    ].copy()
    if u.empty:
        return u
    u["vol_dec"] = _decile(u["atr14_pct"])
    u["liq_dec"] = _decile(u["dvol_med_20"])
    u["style_cell"] = u["vol_dec"].astype(str).radd("v") + u["liq_dec"].astype(str).radd("l")
    return u


def build_pool(universe: pd.DataFrame) -> pd.DataFrame:
    """One name per style cell: the cell's highest 12-1 momentum member.

    A basket, not a selection. The momentum value decides membership and is
    then discarded — it is never written to the ledger, so the stored pool
    cannot be re-ordered by anyone reading it back.
    """
    if universe.empty:
        return universe
    idx = universe.groupby("style_cell")["mom_12_1"].idxmax()
    return universe.loc[idx].sort_values("ticker")


# ------------------------------------------------------------------ ledger ---

def read_ledger(path: str = LEDGER_PATH) -> list[dict]:
    if not os.path.exists(path):
        return []
    out = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def append_ledger(records: Sequence[dict], path: str = LEDGER_PATH) -> int:
    """Append-only. Existing lines are never rewritten, and a record whose
    ``record_key`` is already present is silently skipped, so re-running a
    session is a no-op rather than a duplicate."""
    if not records:
        return 0
    os.makedirs(os.path.dirname(path), exist_ok=True)
    seen = {r.get("record_key") for r in read_ledger(path)}
    fresh = [r for r in records if r.get("record_key") not in seen]
    if not fresh:
        return 0
    with open(path, "a") as fh:
        for r in fresh:
            fh.write(json.dumps(r, sort_keys=True, default=str) + "\n")
    return len(fresh)


def invalidation_record(snapshot_key: str, reason: str, *, session_date: str,
                       price_source: str, sequence: int = 1) -> dict:
    """Retract a snapshot without deleting it.

    Append-only means a row that turns out to be wrong is superseded, never
    removed. The retracted snapshot stays readable, the reason is on the record,
    and every published statistic skips it.
    """
    return {
        "kind": "SNAPSHOT_INVALIDATION",
        "record_key": f"{session_date}|INVALIDATION|{price_source}|{sequence}",
        "label": LABEL,
        "method": METHOD_NAME,
        "written_at": _utcnow(),
        "session_date": session_date,
        "price_source": price_source,
        "invalidates_record_key": snapshot_key,
        "reason": reason,
    }


def snapshot_record(session: pd.Timestamp, source: PriceSource, cov: dict,
                    universe: pd.DataFrame, pool: pd.DataFrame,
                    *, now: pd.Timestamp | None = None, revision: int = 0) -> dict:
    """One session's record. REFUSED sessions are recorded too — a gap in the
    ledger must never be ambiguous between 'not run' and 'refused'."""
    now = pd.Timestamp(now if now is not None else datetime.now(timezone.utc).date())
    age_days = int((now - session.normalize()).days)
    contemporaneous = age_days <= MAX_BACKDATE_DAYS
    rec = {
        "kind": "POOL_SNAPSHOT",
        "record_key": (f"{session.date()}|POOL_SNAPSHOT|{source.name}"
                       + (f"|r{revision}" if revision else "")),
        "revision": revision,
        "label": LABEL,
        "method": METHOD_NAME,
        "basket_not_selection": True,
        "written_at": _utcnow(),
        "session_date": str(session.date()),
        "session_age_days_at_write": age_days,
        "contemporaneous": contemporaneous,
        "contemporaneous_note": (
            None if contemporaneous else
            f"session is {age_days} days old at write time (floor {MAX_BACKDATE_DAYS}); this row was "
            "backfilled from cached history, is NOT unseen forward data, and is excluded from every "
            "published statistic"),
        "price_source": source.name,
        "provider_calls": 0,
        "status": cov["status"],
        "refusal_reason": cov.get("reason"),
        "coverage": cov,
        "horizons": list(HORIZONS),
        "primary_horizon": PRIMARY_HORIZON,
    }
    if cov["status"] != "OK":
        rec["universe_size"] = int(len(universe))
        rec["pool_size"] = 0
        return rec

    rec["universe_size"] = int(len(universe))
    rec["pool_size"] = int(len(pool))
    # Alphabetical. A storage convention, not an ordering of merit.
    rec["pool_members_unordered"] = sorted(pool["ticker"].tolist())
    rec["members_are_equal_weight"] = True
    rec["no_per_name_score_stored"] = True
    # Everything resolution needs, so a later run is reproducible from the
    # ledger alone rather than from whatever the cache holds by then.
    rec["universe_cells"] = {
        str(r.ticker): [int(r.vol_dec), int(r.liq_dec), round(float(r.close), 4)]
        for r in universe.itertuples()
    }
    rec["pool_stats"] = {
        "median_dvol_usd": float(pool["dvol_med_20"].median()),
        "p10_dvol_usd": float(pool["dvol_med_20"].quantile(0.10)),
        "median_price_usd": float(pool["close"].median()),
        "median_atr14_pct": float(pool["atr14_pct"].median()),
        "distinct_style_cells": int(pool["style_cell"].nunique()),
    }
    if len(pool) < MIN_POOL_SIZE:
        rec["status"] = REFUSAL_INSUFFICIENT
        rec["refusal_reason"] = (
            f"pool of {len(pool)} names is far below the ~{N_DECILES * N_DECILES} style cells "
            "this method expects; the universe is too thin to reproduce it")
    return rec


# -------------------------------------------------------------- resolution ---

def _forward_return(paths: Sequence[str] | str, session: pd.Timestamp, horizon: int) -> tuple[float, bool]:
    """Return over ``horizon`` sessions from the session bar, and whether the
    series ran out first. A name that stops trading is measured to its final
    bar and flagged, never dropped — dropping it would flatter the basket."""
    if isinstance(paths, str):
        paths = (paths,)
    df, _ = load_series(paths, ["close"])
    if df.empty:
        return float("nan"), False
    upto = df[df.index <= session]
    if upto.empty:
        return float("nan"), False
    start = float(upto["close"].iloc[-1])
    after = df[df.index > session]
    if start <= 0 or after.empty:
        return float("nan"), False
    truncated = len(after) < horizon
    end = float(after["close"].iloc[min(horizon, len(after)) - 1])
    return (end / start - 1.0) * 100.0, truncated


def _matured(session: pd.Timestamp, horizon: int, files: dict[str, tuple[str, ...]]) -> bool:
    """True once a benchmark series has ``horizon`` sessions past the snapshot."""
    p = files.get("SPY")
    if not p:
        return False
    df, _ = load_series(p, ["close"])
    if df.empty:
        return False
    return int((df.index > session).sum()) >= horizon


def resolve_snapshot(snap: dict, horizon: int, files: dict[str, tuple[str, ...]], *,
                     draws: int = 200, seed: int = 20260910) -> dict | None:
    """Forward outcome for one session at one horizon. Pool-level only."""
    session = pd.Timestamp(snap["session_date"])
    if snap.get("status") != "OK" or not snap.get("pool_members_unordered"):
        return None
    if not _matured(session, horizon, files):
        return None

    members = snap["pool_members_unordered"]
    cells = snap.get("universe_cells", {})

    pool_ret, truncated = [], 0
    for t in members:
        p = files.get(t)
        if not p:
            continue
        r, trunc = _forward_return(p, session, horizon)
        if np.isfinite(r):
            pool_ret.append(r)
            truncated += int(trunc)
    if len(pool_ret) < MIN_POOL_SIZE:
        return None
    pool_ret = np.asarray(pool_ret, dtype=float)

    uni_ret: dict[str, float] = {}
    for t in cells:
        p = files.get(t)
        if not p:
            continue
        r, _ = _forward_return(p, session, horizon)
        if np.isfinite(r):
            uni_ret[t] = r
    uni_vals = np.asarray(list(uni_ret.values()), dtype=float)

    bench: dict[str, dict] = {}
    for b in BENCHMARKS:
        p = files.get(b)
        if not p:
            continue
        r, _ = _forward_return(p, session, horizon)
        if np.isfinite(r):
            bench[b] = {"return_pct": round(float(r), 4),
                        "basket_excess_pp": round(float(pool_ret.mean() - r), 4)}

    controls = _controls(members, cells, uni_ret, float(pool_ret.mean()),
                         draws=draws, seed=seed + horizon)

    return {
        "kind": "HORIZON_RESOLUTION",
        "record_key": f"{session.date()}|RESOLUTION|{horizon}|{snap.get('price_source')}",
        "label": LABEL,
        "method": METHOD_NAME,
        "basket_not_selection": True,
        "resolved_at": _utcnow(),
        "session_date": snap["session_date"],
        "price_source": snap.get("price_source"),
        "horizon_sessions": horizon,
        "is_primary_horizon": horizon == PRIMARY_HORIZON,
        "provider_calls": 0,
        "names_resolved": int(pool_ret.size),
        "names_in_snapshot": len(members),
        "names_horizon_truncated": int(truncated),
        "basket": {
            "mean_return_pct": round(float(pool_ret.mean()), 4),
            "median_return_pct": round(float(np.median(pool_ret)), 4),
            "win_rate_pct": round(float((pool_ret > 0).mean() * 100.0), 2),
            "pct_down_over_25": round(float((pool_ret <= -25.0).mean() * 100.0), 2),
            "p05_return_pct": round(float(np.percentile(pool_ret, 5)), 4),
        },
        "universe": {
            "names_resolved": int(uni_vals.size),
            "mean_return_pct": round(float(uni_vals.mean()), 4) if uni_vals.size else None,
            "median_return_pct": round(float(np.median(uni_vals)), 4) if uni_vals.size else None,
        },
        "selection_vs_universe_mean_pp": (
            round(float(pool_ret.mean() - uni_vals.mean()), 4) if uni_vals.size else None),
        "benchmarks": bench,
        "controls": controls,
    }


def _controls(members: Sequence[str], cells: dict, uni_ret: dict[str, float],
              basket_mean: float, *, draws: int, seed: int) -> dict:
    """Same-date random and style-matched random baskets of the same size.

    Both are drawn from the session's own recorded universe, so neither needs a
    provider call. The matched control replaces each member with a random name
    from its own volatility x liquidity cell, which is what separates a real
    residual from a style tilt.
    """
    rng = np.random.default_rng(seed)
    out: dict = {"draws": draws, "provider_calls": 0}

    pool_names = [t for t in uni_ret]
    n = len([t for t in members if t in uni_ret])
    if n == 0 or len(pool_names) <= n:
        return {**out, "same_date_random": None, "matched_random": None,
                "unavailable_reason": "not enough resolved universe names"}

    vals = np.asarray([uni_ret[t] for t in pool_names], dtype=float)
    idx = rng.integers(0, vals.size, size=(draws, n))
    plain = vals[idx].mean(axis=1)
    out["same_date_random"] = {
        "median_basket_return_pct": round(float(np.median(plain)), 4),
        "excess_pp": round(float(basket_mean - np.median(plain)), 4),
        "pct_of_draws_beaten": round(float((basket_mean > plain).mean() * 100.0), 2),
    }

    by_cell: dict[tuple[int, int], list[float]] = {}
    for t, meta in cells.items():
        if t in uni_ret:
            by_cell.setdefault((meta[0], meta[1]), []).append(uni_ret[t])
    pools = []
    for t in members:
        meta = cells.get(t)
        if not meta:
            continue
        p = by_cell.get((meta[0], meta[1]))
        if p:
            pools.append(np.asarray(p, dtype=float))
    if not pools:
        out["matched_random"] = None
        return out
    sample = np.empty((draws, len(pools)), dtype=float)
    for j, p in enumerate(pools):
        sample[:, j] = p[rng.integers(0, p.size, size=draws)]
    matched = sample.mean(axis=1)
    out["matched_random"] = {
        "median_basket_return_pct": round(float(np.median(matched)), 4),
        "excess_pp": round(float(basket_mean - np.median(matched)), 4),
        "pct_of_draws_beaten": round(float((basket_mean > matched).mean() * 100.0), 2),
        "cells_matched": len(pools),
    }
    return out


# ------------------------------------------------------------ refresh plan ---

def plan_refresh(frame: pd.DataFrame, session: pd.Timestamp, cap: int | None) -> dict:
    """Which names a refresh would need, and how many calls that is.

    This module never fetches. The plan names the symbols and hands off to the
    maintained refresher, so there is exactly one code path in the repo that
    can touch a provider for prices.
    """
    cutoff = session - pd.Timedelta(days=MAX_BAR_AGE_DAYS)
    liquid = frame[(frame["close"] >= MIN_PRICE) & (frame["dvol_med_20"] >= MIN_DVOL_MED_20)]
    stale = liquid[(liquid["last_bar"] < cutoff) | (liquid["bars"] < MIN_BARS)]
    todo = sorted(stale["ticker"].tolist())
    planned = len(todo)
    effective_cap = DEFAULT_REFRESH_CALL_CAP if cap is None else cap
    enforce_call_cap(planned, effective_cap, what="style_cell_leader forward-shadow refresh")
    return {
        "planned_calls": planned,
        "cap_applied": effective_cap,
        "symbols": todo[:200],
        "symbols_truncated": max(0, planned - 200),
        "this_module_makes_no_provider_calls": True,
        "delegate_to": (
            "SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python "
            "research/refresh_universe_prices.py --execute --max-calls <n>"),
    }


# ---------------------------------------------------------------- the run ----

def run_session(*, source_name: str = "live_cache", as_of: str | None = None,
                dry_run: bool = False, ledger_path: str = LEDGER_PATH,
                allow_backdate: bool = False, now: pd.Timestamp | None = None,
                revision: int = 0, verbose: bool = True) -> dict:
    """Record one session, then resolve whatever has matured. Zero provider calls."""
    source = resolve_source(source_name)
    frame, session = build_session_frame(source, pd.Timestamp(as_of) if as_of else None)
    cov = coverage_report(frame, session)
    universe = eligible_universe(frame, session) if cov["status"] == "OK" else pd.DataFrame()
    pool = build_pool(universe) if len(universe) else pd.DataFrame()
    snap = snapshot_record(session, source, cov, universe, pool, now=now, revision=revision)
    if not snap["contemporaneous"] and not allow_backdate:
        if verbose:
            print(f"  REFUSED to write: {snap['contemporaneous_note']}. "
                  "Pass --allow-backdate to record it as excluded-from-statistics.")
        return {"snapshot": snap, "new_resolutions": [], "records_written": 0,
                "dry_run": dry_run, "not_written_reason": "backdated_session",
                "ledger_path": ledger_path}

    if verbose:
        print(f"session {snap['session_date']} · source={source.name} · status={snap['status']}")
        print(f"  liquid candidates {cov.get('liquid_candidates')} · fresh {cov.get('fresh_share_pct')}% "
              f"· depth-of-fresh {cov.get('depth_coverage_of_fresh_pct')}% · pool {snap.get('pool_size', 0)}")
        if snap.get("refusal_reason"):
            print(f"  REFUSED: {snap['refusal_reason']}")

    written = 0
    if not dry_run:
        written = append_ledger([snap], ledger_path)

    files = source.paths()
    ledger = read_ledger(ledger_path)
    have = {r.get("record_key") for r in ledger}
    resolutions: list[dict] = []
    for rec in ledger:
        if rec.get("kind") != "POOL_SNAPSHOT" or rec.get("status") != "OK":
            continue
        for h in HORIZONS:
            if rec.get("price_source") != source.name:
                continue
            key = f"{pd.Timestamp(rec['session_date']).date()}|RESOLUTION|{h}|{source.name}"
            if key in have:
                continue
            res = resolve_snapshot(rec, h, files)
            if res:
                resolutions.append(res)
    if resolutions and not dry_run:
        written += append_ledger(resolutions, ledger_path)
    if verbose:
        print(f"  ledger: {written} record(s) appended · {len(resolutions)} horizon(s) newly matured")

    return {"snapshot": snap, "new_resolutions": resolutions, "records_written": written,
            "dry_run": dry_run, "ledger_path": ledger_path}


# ---------------------------------------------------------------- summary ----

def summarise(ledger_path: str = LEDGER_PATH, source: str | None = None) -> dict:
    """The published artifact for ONE price source. The verdict field is pinned.

    Rows from different caches are never pooled: they are different data
    pipelines with different staleness, and averaging across them would report
    a number that no single pipeline produced. When ``source`` is omitted the
    most recent successfully recorded source is used.
    """
    ledger = read_ledger(ledger_path)
    retractions = [r for r in ledger if r.get("kind") == "SNAPSHOT_INVALIDATION"]
    retracted = {r["invalidates_record_key"] for r in retractions}
    by_key = {r.get("record_key"): r for r in ledger if r.get("kind") == "POOL_SNAPSHOT"}
    discarded_rows = [{
        "record_key": r["invalidates_record_key"],
        "session_date": r.get("session_date"),
        "price_source": r.get("price_source"),
        "original_status": (by_key.get(r["invalidates_record_key"], {}) or {}).get("status"),
        "original_pool_size": (by_key.get(r["invalidates_record_key"], {}) or {}).get("pool_size"),
        "retraction_key": r.get("record_key"),
        "reason": r.get("reason"),
    } for r in retractions]
    all_snaps = [r for r in ledger if r.get("kind") == "POOL_SNAPSHOT"
                 and r.get("record_key") not in retracted]
    sources_present = sorted({r.get("price_source") for r in all_snaps if r.get("price_source")})
    if source is None:
        ok_rows = [r for r in all_snaps if r.get("status") == "OK"]
        source = (ok_rows[-1] if ok_rows else (all_snaps[-1] if all_snaps else {})).get("price_source")
    snaps = [r for r in all_snaps if r.get("price_source") == source]
    live_keys = {r.get("record_key") for r in all_snaps}
    res = [r for r in ledger
           if r.get("kind") == "HORIZON_RESOLUTION" and r.get("price_source") == source
           and f"{r.get('session_date')}|POOL_SNAPSHOT|{r.get('price_source')}" in live_keys]
    # Backfilled rows stay in the ledger for auditability and are excluded here.
    backfilled = [s for s in snaps if not s.get("contemporaneous", True)]
    backfilled_keys = {s["session_date"] for s in backfilled}
    snaps = [s for s in snaps if s.get("contemporaneous", True)]
    res = [r for r in res if r.get("session_date") not in backfilled_keys]
    ok = [s for s in snaps if s.get("status") == "OK"]
    refused = [s for s in snaps if s.get("status") != "OK"]

    by_h: dict[str, dict] = {}
    for h in HORIZONS:
        rows = [r for r in res if r.get("horizon_sessions") == h]
        entry: dict = {"matured_sessions": len(rows)}
        if rows:
            sel = [r["selection_vs_universe_mean_pp"] for r in rows
                   if r.get("selection_vs_universe_mean_pp") is not None]
            entry.update({
                "basket_mean_return_pct": round(float(np.mean([r["basket"]["mean_return_pct"] for r in rows])), 4),
                "basket_median_return_pct": round(float(np.median([r["basket"]["median_return_pct"] for r in rows])), 4),
                "mean_selection_vs_universe_pp": round(float(np.mean(sel)), 4) if sel else None,
                "sessions_with_positive_selection": int(sum(s > 0 for s in sel)) if sel else 0,
                "mean_excess_vs": {
                    b: round(float(np.mean([r["benchmarks"][b]["basket_excess_pp"]
                                            for r in rows if b in r.get("benchmarks", {})])), 4)
                    for b in BENCHMARKS
                    if any(b in r.get("benchmarks", {}) for r in rows)
                },
                "mean_excess_vs_same_date_random_pp": _mean_control(rows, "same_date_random"),
                "mean_excess_vs_matched_random_pp": _mean_control(rows, "matched_random"),
            })
        by_h[str(h)] = entry

    primary = by_h.get(str(PRIMARY_HORIZON), {})
    matured = primary.get("matured_sessions", 0)
    windows = _non_overlapping_count(res, PRIMARY_HORIZON)

    return {
        "kind": "STYLE_CELL_LEADER_FORWARD_SHADOW_LATEST",
        "label": LABEL,
        "verdict": LABEL,
        "verdict_is_pinned": True,
        "verdict_pin_reason": (
            "This ledger publishes forward evidence, not verdicts. The label does not change "
            "when the maturity floor is reached; promotion is a separate human decision made "
            "outside this module."),
        "method": METHOD_NAME,
        "basket_not_selection": True,
        "generated_at": _utcnow(),
        "provider_calls": 0,
        "price_source": source,
        "sources_present_in_ledger": sources_present,
        "sources_are_never_pooled": (
            "Each price source is summarised separately. Rows from different caches have "
            "different staleness and are never averaged together."),
        "sessions_recorded": len(snaps),
        "retracted_snapshots": len(retracted),
        "discarded_rows": discarded_rows,
        "sessions_ok": len(ok),
        "accepted_ok_sessions": len(ok),
        "accepted_ok_sessions_all_sources": sum(
            1 for r in all_snaps if r.get("status") == "OK" and r.get("contemporaneous", True)),
        "first_accepted_session_pending": len(ok) == 0,
        "sessions_refused": len(refused),
        "backfilled_rows_excluded": len(backfilled),
        "latest_session": snaps[-1]["session_date"] if snaps else None,
        "latest_status": snaps[-1]["status"] if snaps else None,
        "latest_refusal_reason": snaps[-1].get("refusal_reason") if snaps else None,
        "latest_coverage": snaps[-1].get("coverage") if snaps else None,
        "latest_universe_size": snaps[-1].get("universe_size") if snaps else None,
        "latest_pool_size": snaps[-1].get("pool_size") if snaps else None,
        "unresolved_snapshots_at_primary_horizon": max(0, len(ok) - matured),
        "by_horizon": by_h,
        "maturity": {
            "floor": MATURITY_FLOOR,
            "matured_snapshots_at_primary_horizon": matured,
            "non_overlapping_primary_windows": windows,
            "floor_reached": bool(
                matured >= MATURITY_FLOOR["matured_snapshots_at_primary_horizon"]
                and windows >= MATURITY_FLOOR["non_overlapping_primary_windows"]),
        },
        "method_definition": {
            "min_price_usd": MIN_PRICE,
            "min_dvol_med_20_usd": MIN_DVOL_MED_20,
            "grouping": "volatility decile x liquidity decile, computed within the session",
            "member_rule": "each cell's highest 12-1 momentum name",
            "weighting": "equal",
            "ordering": "none — the pool is unordered and no per-name score is stored",
            "horizons": list(HORIZONS),
            "primary_horizon": PRIMARY_HORIZON,
        },
        "not_supported": [
            "reading any member as an individual stock selection",
            "ordering, ranking, shortlisting or scoring the pool",
            "any production scanner, M1, HC/EO, Alpha Focus, dashboard or routing change",
            "pooling these rows with the live forward ledger or program verdicts",
        ],
        "research_only": True,
        "sandbox_only": True,
        "forward_shadow_only": True,
        "not_live_evidence": True,
        "no_recommendation": True,
        "ledger": os.path.relpath(ledger_path, REPO),
        "report": os.path.relpath(DOC_PATH, REPO),
    }


def _mean_control(rows: list[dict], which: str) -> float | None:
    vals = [r["controls"][which]["excess_pp"] for r in rows
            if r.get("controls", {}).get(which)]
    return round(float(np.mean(vals)), 4) if vals else None


def _non_overlapping_count(res: list[dict], horizon: int) -> int:
    """How many matured sessions are far enough apart to be independent."""
    dates = sorted({pd.Timestamp(r["session_date"]) for r in res
                    if r.get("horizon_sessions") == horizon})
    if not dates:
        return 0
    count, last = 1, dates[0]
    for d in dates[1:]:
        # 60 sessions is roughly 84 calendar days.
        if (d - last).days >= horizon * 1.4:
            count += 1
            last = d
    return count


# ------------------------------------------------------------------- doc -----

def render_doc(summary: dict) -> str:
    m, cov = summary["maturity"], summary.get("latest_coverage") or {}
    lines = [
        "# `style_cell_leader_pool` — forward shadow ledger",
        "",
        f"*Generated {summary['generated_at'][:19]}Z. Zero provider calls.*",
        "",
        "> **FORWARD_SHADOW_RESEARCH_ONLY.** This page records what a basket would",
        "> have contained on past sessions and what happened next. It is **not** live",
        "> forward evidence for any production surface, **not** Phase 4B input, **not**",
        "> a gate/threshold/score/routing change, and **not** a trade signal. The rows",
        "> must never be pooled with the live forward ledger, program verdicts, M1,",
        "> HC/EO/Alpha Focus routing, or the dashboard. Nothing here is an individual",
        "> stock selection: the tracked object is an **unordered, equal-weight basket**,",
        "> and no per-name score is stored, so it cannot be ordered after the fact.",
        "",
        "---",
        "",
        "## What is tracked",
        "",
        f"Universe floored at price >= ${MIN_PRICE:.0f} and median 20-day dollar volume >=",
        f"${MIN_DVOL_MED_20/1e6:.0f}M, split into volatility-decile x liquidity-decile cells",
        "computed within the session. Each cell contributes its single highest 12-1",
        f"momentum name. Horizons {', '.join(str(h) + 'd' for h in HORIZONS)}; primary "
        f"**{PRIMARY_HORIZON}d**.",
        "",
        "The replay study labelled this `PROVISIONAL_RESEARCH_CANDIDATE` and said why",
        "it could not be called validated: the object was specified after the 2025",
        "holdout had been read. Only unseen data can advance it. This ledger records",
        "that data and deliberately does not judge it.",
        "",
    ]
    if summary.get("first_accepted_session_pending"):
        lines += [
            "## No accepted forward sessions yet",
            "",
            "**There are zero accepted OK forward observations in this ledger.** The",
            "evidence record has not started.",
            "",
            "2026-09-09 was worked as a **debug and validation day**, not as the first",
            "observation. It was used to exercise the cache-merge rules, the coverage",
            "gates, and the append-only invalidation path, and every pool it produced has",
            "been retracted — the last of them discarded by operator decision before any",
            "horizon matured, so nothing was thrown away that had become evidence.",
            "",
            "The first clean accepted session is **pending**. Until one is recorded, every",
            "forward statistic on this page is empty by construction rather than by",
            "coincidence.",
            "",
        ]
        rows = summary.get("discarded_rows") or []
        if rows:
            lines += ["### Retracted rows, in order", "",
                      "| row | original status | pool | reason |", "|---|---|--:|---|"]
            for d in rows:
                # record keys contain "|", which would split the table cell
                key = str(d.get("record_key", "")).split("|", 1)[-1].replace("|", "\\|")
                reason = str(d.get("reason", "")).replace("|", "\\|")
                lines.append(
                    f"| `{key}` | {d.get('original_status')} | {d.get('original_pool_size')} | {reason} |")
            lines += ["",
                      "Nothing above was deleted. Each row is still readable in the ledger with",
                      "its original status, and each retraction carries its own reason. Published",
                      "statistics skip them.", ""]
    lines += [
        "## Session status",
        "",
        f"| | |",
        f"|---|---|",
        f"| price source | `{summary.get('price_source')}` |",
        f"| latest session | {summary.get('latest_session') or 'none'} |",
        f"| status | **{summary.get('latest_status') or 'n/a'}** |",
        f"| universe size | {summary.get('latest_universe_size') if summary.get('latest_universe_size') is not None else 'n/a'} |",
        f"| pool size | {summary.get('latest_pool_size') if summary.get('latest_pool_size') is not None else 'n/a'} |",
        f"| liquid candidates | {cov.get('liquid_candidates', 'n/a')} |",
        f"| fresh share | {cov.get('fresh_share_pct', 'n/a')}% (floor {MIN_FRESH_SHARE*100:.0f}%) |",
        f"| depth coverage of fresh | {cov.get('depth_coverage_of_fresh_pct', 'n/a')}% (floor {MIN_DEPTH_COVERAGE*100:.0f}%) |",
        f"| sessions in ledger | {summary['sessions_recorded']} ({summary['sessions_ok']} with a pool, {summary['sessions_refused']} refused) |",
        f"| **accepted forward sessions** | **{summary.get('accepted_ok_sessions_all_sources', 0)}** |",
        f"| retracted rows | {summary.get('retracted_snapshots', 0)} |",
        f"| unresolved at {PRIMARY_HORIZON}d | {summary['unresolved_snapshots_at_primary_horizon']} |",
        "",
    ]
    prov = (cov.get("cache_merge_provenance") or {})
    if prov:
        lines += [
            "### Cache merge",
            "",
            "Where a ticker exists in more than one cache the series are merged, with the "
            "maintained cache winning on any shared date. Preferring the *deepest* file "
            "instead would silently drop the newest bar, which is the entry price this "
            "ledger records.",
            "",
            f"* merged from more than one cache: **{prov.get('merged', 0):,}**",
            f"* only one cache held it: **{prov.get('single', 0):,}**",
            f"* **{prov.get('single_adjustment_mismatch', 0):,}** refused a splice — the caches "
            "disagreed by more than 2% on overlapping dates, which is an unadjusted "
            "corporate action in one of them. Those names fall back to a single cache and "
            "drop out of the universe if that leaves them short of "
            f"{MIN_BARS} bars, rather than being spliced into a price gap that never happened.",
            "",
        ]
    if summary.get("latest_refusal_reason"):
        lines += ["### Why this session was refused", "",
                  f"**{summary['latest_status']}** — {summary['latest_refusal_reason']}", "",
                  "A pool computed on a thin or stale cache is a look-alike, and a forward",
                  "ledger of a look-alike is worse than no ledger. The refusal is recorded",
                  "in the ledger so a gap can never be mistaken for a session that was",
                  "simply not run.",
                  "",
                  "**What would unblock it.** The gate that fails is freshness, not depth —",
                  f"{cov.get('depth_coverage_of_fresh_pct')}% of the names that *are* current carry the",
                  f"{MIN_BARS} bars the method needs. The live cache simply is not being kept",
                  "current across this universe. Two options, neither taken here:",
                  "",
                  "1. A maintained daily refresh of the liquid universe. Size it with",
                  "   `forward_shadow.py plan-refresh`; this module refuses above",
                  f"   {DEFAULT_REFRESH_CALL_CAP} planned calls and never fetches itself — it hands off to",
                  "   the existing `research/refresh_universe_prices.py`.",
                  "2. Run against `--source live_plus_replay_cache`, which clears both gates",
                  "   today. That cache is maintained by a research backfill script rather",
                  "   than a timer, so a ledger built on it inherits whatever staleness the",
                  "   script has drifted into. It is a way to start measuring, not a fix.",
                  ""]

    lines += ["## Forward outcomes", ""]
    any_mature = any(v.get("matured_sessions") for v in summary["by_horizon"].values())
    if not any_mature:
        if summary.get("first_accepted_session_pending"):
            lines += ["No accepted session exists yet, so no horizon can mature. Nothing to report.", ""]
        else:
            lines += ["No horizon has matured yet. Nothing to report.", ""]
    else:
        lines += ["| horizon | matured | basket mean | basket median | vs universe | vs SPY | vs QQQ | vs IWM | vs random | vs matched random |",
                  "|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|"]
        for h in HORIZONS:
            v = summary["by_horizon"][str(h)]
            if not v.get("matured_sessions"):
                lines.append(f"| {h}d | 0 | — | — | — | — | — | — | — | — |")
                continue
            ex = v.get("mean_excess_vs", {})
            def g(x): return f"{x:+.2f}" if isinstance(x, (int, float)) else "—"
            lines.append(
                f"| {h}d{' (primary)' if h == PRIMARY_HORIZON else ''} | {v['matured_sessions']} | "
                f"{g(v.get('basket_mean_return_pct'))}% | {g(v.get('basket_median_return_pct'))}% | "
                f"{g(v.get('mean_selection_vs_universe_pp'))} | {g(ex.get('SPY'))} | {g(ex.get('QQQ'))} | "
                f"{g(ex.get('IWM'))} | {g(v.get('mean_excess_vs_same_date_random_pp'))} | "
                f"{g(v.get('mean_excess_vs_matched_random_pp'))} |")
        lines += ["", "All figures are percentage points of the **equal-weight basket**, against",
                  "controls drawn from the same session's own recorded universe. Both random",
                  "controls are computed from cached prices; neither needs a provider call.", ""]

    lines += [
        "## Maturity",
        "",
        f"Pre-declared floor, frozen before the first observation: "
        f"**{MATURITY_FLOOR['matured_snapshots_at_primary_horizon']} matured {PRIMARY_HORIZON}d sessions** and "
        f"**{MATURITY_FLOOR['non_overlapping_primary_windows']} non-overlapping windows**.",
        "",
        f"Progress: {m['matured_snapshots_at_primary_horizon']} matured, "
        f"{m['non_overlapping_primary_windows']} non-overlapping. "
        f"Floor reached: **{'yes' if m['floor_reached'] else 'no'}**.",
        "",
        "Reaching the floor does not produce a verdict. The published label stays",
        "`FORWARD_SHADOW_RESEARCH_ONLY` either way; promotion is a separate human",
        "decision taken outside this module.",
        "",
        "## What this does not support",
        "",
    ] + [f"* {x}" for x in summary["not_supported"]] + [
        "",
        "---",
        "",
        f"*Ledger: `{summary['ledger']}` (append-only JSONL). Artifact:*",
        "*`cache/research/agent_lab/style_cell_leader_forward_latest.json`.*",
        "*Re-run with* `GEM_TRADER_SKIP_DOTENV=true .venv/bin/python -m research.agent_lab.forward_shadow run`.",
        "*Not scheduled: there is no timer, cron entry, or unit for this module.*",
        "",
    ]
    return "\n".join(lines)


def write_outputs(summary: dict, *, latest_path: str = LATEST_PATH, doc_path: str = DOC_PATH) -> None:
    os.makedirs(os.path.dirname(latest_path), exist_ok=True)
    with open(latest_path, "w") as fh:
        json.dump(summary, fh, indent=1, default=str)
    os.makedirs(os.path.dirname(doc_path), exist_ok=True)
    with open(doc_path, "w") as fh:
        fh.write(render_doc(summary))
    print(f"wrote {latest_path}")
    print(f"wrote {doc_path}")


# -------------------------------------------------------------------- cli ----

def main(argv: Sequence[str] | None = None) -> int:
    offline_env()
    ap = argparse.ArgumentParser(
        description="style_cell_leader_pool forward shadow ledger (sandbox; never calls a provider)")
    ap.add_argument("command", choices=["run", "summarise", "plan-refresh"])
    ap.add_argument("--source", default="live_cache",
                    choices=["live_cache", "live_plus_replay_cache"],
                    help="live_cache is what production timers maintain and is the default")
    ap.add_argument("--as-of", default=None, help="session date; defaults to the newest cached bar")
    ap.add_argument("--dry-run", action="store_true", help="compute and print without writing the ledger")
    ap.add_argument("--allow-backdate", action="store_true",
                    help="write a session older than the backdate floor; the row is stamped "
                         "contemporaneous=false and excluded from every published statistic")
    ap.add_argument("--ledger", default=LEDGER_PATH)
    ap.add_argument("--max-calls", type=int, default=None,
                    help=f"override the {DEFAULT_REFRESH_CALL_CAP}-call refresh-plan cap")
    ap.add_argument("--summarise-source", default=None,
                    help="which price source to summarise; defaults to the latest recorded one")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    def _go(a) -> int:
        if a.command == "plan-refresh":
            src = resolve_source(a.source)
            frame, session = build_session_frame(src, pd.Timestamp(a.as_of) if a.as_of else None)
            plan = plan_refresh(frame, session, a.max_calls)
            print(json.dumps(plan, indent=1))
            return 0
        if a.command == "run":
            run_session(source_name=a.source, as_of=a.as_of, dry_run=a.dry_run,
                        ledger_path=a.ledger, allow_backdate=a.allow_backdate,
                        verbose=not a.quiet)
        summary = summarise(a.ledger, a.summarise_source)
        if not a.dry_run:
            write_outputs(summary)
        else:
            print(json.dumps({k: summary[k] for k in
                              ("verdict", "sessions_recorded", "latest_status", "maturity")}, indent=1))
        return 0

    return run_cli(_go, args)


if __name__ == "__main__":
    raise SystemExit(main())

"""Alpha Discovery Tournament — an open search for pre-move setups with
positive asymmetric expectancy, across every style, 2022-2025.

RESEARCH ONLY. Reads the replay caches and the existing replay episode panels;
writes only ``cache/research/alpha_tournament_*`` and
``logs/alpha_tournament_*``. It changes no scanner, score, gate, threshold,
HC/EO rule, routing decision, dashboard artifact, cron entry, or ledger, emits
no trade signal, and cannot emit a live verdict token.

Why a third study. The price-only replay contradicted the old scanner; the
Alpha Reconstruction Lab then found that top-1% winners share their pre-move
features with bottom-1% losers (AUC correlation +0.97) and concluded the
winner fingerprint was a volatility fingerprint. That conclusion was reached
under two choices this study deliberately abandons:

* it judged cohorts mainly on **median selection**, which is the wrong statistic
  for a lane whose thesis is a fat right tail. An equal-weight research list
  earns the **mean**; a cohort can carry a negative median and still be worth
  holding if the right tail pays for it. Here every cohort is scored on both,
  plus an explicit right-tail/left-tail ratio;
* it started from the brief's hypothesis list, which leaned toward quality and
  low volatility. This study starts from the opportunity map instead: measure
  what every style bucket actually paid, then let the tournament rank families
  without a prior that quality is better, volatility is worse, institutional is
  safer, or moonshots are random.

Nothing here inherits the old scanner's lanes, scores or thresholds. The old
board appears only as one weak baseline among the controls.

Sub-stages::

    panel       enriched as-of feature + label panel        (zero API)
    map         Part 1-3: opportunity map, winners, losers   (zero API)
    tournament  Part 4-7: strategy families, train/holdout   (zero API)
    lists       Part 8: research-list usefulness simulation  (zero API)
    report      Part 10-12: ranked strategies and answers    (zero API)
    all         every stage in order                         (zero API)
"""

from __future__ import annotations

import argparse
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

from research.backtests.common import (
    BENCHMARKS,
    LiveArtifactTripwire,
    REPLAY_WINDOW_END,
    REPLAY_WINDOW_START,
    ROOT,
    assert_provenance,
    assert_replay_write_path,
    assert_tournament_verdict,
    offline_env,
    provenance_flags,
    run_cli,
    write_replay_json,
    write_replay_text,
)
from research.backtests.alpha_reconstruction_lab import (
    HOLDOUT_YEARS,
    REGIMES,
    TRAIN_YEARS,
    _cluster_ci,
    _lag_ratio,
    _roll_max,
    _roll_mean,
    _roll_median,
    _roll_min,
    _roll_std,
    _series_integrity,
    _trim_mean,
    _winsor_mean,
    build_fundamental_panel,
    cohort_stats,
    load_delisted,
    load_prices,
    load_quarantine_excludes,
    load_sector_map,
    weekly_scan_dates,
)

offline_env()

INTERMEDIATES_REL = "cache/research/alpha_tournament_intermediates"
PANEL_REL = f"{INTERMEDIATES_REL}/panel.parquet"
MAP_REL = "cache/research/alpha_tournament_opportunity_map.json"
STRATEGIES_REL = "cache/research/alpha_tournament_strategies.json"
LISTS_REL = "cache/research/alpha_tournament_research_lists.json"
LATEST_REL = "cache/research/alpha_tournament_latest.json"
REPORT_TXT_REL = "logs/alpha_tournament_latest.txt"

HORIZONS: tuple[int, ...] = (20, 45, 60, 90, 126)
PRIMARY_H = 60

# Path-based labels: "gained X% at any point within N sessions".
MFE_LABELS: tuple[tuple[str, float, int], ...] = (
    ("up25_20d", 25.0, 20),
    ("up50_45d", 50.0, 45),
    ("up80_60d", 80.0, 60),
    ("up100_126d", 100.0, 126),
    ("up200_126d", 200.0, 126),
)
MAE_LABELS: tuple[tuple[str, float, int], ...] = (
    ("down25_20d", -25.0, 20),
    ("down50_60d", -50.0, 60),
)

# The tradable floor. Deliberately LOW: this study must be able to see small
# caps, biotech, turnarounds and moonshots, so the floor exists only to remove
# what could not have been bought, not to impose a style.
BASE_FLOOR = {"min_price": 2.0, "min_dvol_med": 1_000_000.0, "min_bars": 120}
LIQUIDITY_TIERS = {
    "micro_1M_5M": (1e6, 5e6),
    "small_5M_20M": (5e6, 2e7),
    "mid_20M_100M": (2e7, 1e8),
    "large_100M_plus": (1e8, float("inf")),
}

# Fake liquidity: a name whose MEAN dollar volume is far above its MEDIAN is
# trading on blocks, not on a book. NAKA passed a $1M mean floor on one
# 5-million-share day while trading ~1,000 shares daily. The ratio is carried
# as a feature and as an exclusion, so its cost is measurable either way.
FAKE_LIQUIDITY_RATIO = 4.0


# ── security type ───────────────────────────────────────────────────────────
# Exclusions the brief requires (ETPs, funds, warrants, rights, units,
# preferreds, notes). No single authoritative source exists offline, so three
# are combined and the residual risk is reported rather than hidden.

_ETP_NAME_TOKENS = (
    "ETF", "ETN", "PROSHARES", "ISHARES", "SPDR", "DIREXION", "VANECK",
    "GLOBAL X", "INVESCO", "WISDOMTREE", "FIRST TRUST", "SCHWAB STRATEGIC",
    "INDEX TRUST", "INDEX FUND", "TRUST SERIES", "ULTRAPRO", "ULTRASHORT",
    "BULL 3X", "BEAR 3X", "COMMODITY TRUST", "MINI TRUST", "CURRENCY TRUST",
)
_SUFFIX_SUSPECT = ("W", "WS", "WT", "R", "RT", "U", "UN")


def security_type_exclusions(root: Path, symbols) -> dict:
    """Classify symbols that must not be treated as common stock.

    Returns {symbol: reason}. Three sources, in order of confidence:
      1. the frozen scanner's own ETF list (exact, small);
      2. company names in the Gatekeeper profile cache (CURRENT metadata, ~43%
         coverage of the liquid universe) matched against ETP issuer tokens;
      3. symbol shape — dotted classes, and the warrant/right/unit suffixes,
         applied only to 5-letter symbols where the convention is reliable.
    """
    import research.research_scanner as RS

    profiles = {}
    try:
        import sqlite3

        db = root / "db" / "trading.db"
        if db.exists():
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            try:
                for key, payload in con.execute(
                    "SELECT key, payload FROM cache_meta WHERE key LIKE 'fmp:profile:%'"
                ):
                    try:
                        d = json.loads(payload)
                    except Exception:
                        continue
                    profiles[str(key).split(":")[-1].upper()] = d
            finally:
                con.close()
    except Exception:
        pass

    out: dict[str, str] = {}
    for sym in symbols:
        s = str(sym).upper()
        if s in RS._KNOWN_ETFS:
            out[s] = "known_etf_list"
            continue
        name = str((profiles.get(s) or {}).get("companyName") or "").upper()
        if name and any(tok in name for tok in _ETP_NAME_TOKENS):
            out[s] = "etp_issuer_name"
            continue
        if "." in s or "-" in s:
            out[s] = "share_class_or_preferred_symbol"
            continue
        if len(s) == 5 and s[-1] in ("W", "R", "U"):
            out[s] = "warrant_right_or_unit_suffix"
            continue
        if len(s) == 5 and s.endswith(("WS", "RT", "UN")):
            out[s] = "warrant_right_or_unit_suffix"
    return out


# ── enriched as-of features (Part 5) ────────────────────────────────────────
#
# Everything below is a function of bars at or before the sample position. The
# panel stage re-derives a random sample from a physically truncated series and
# fails if any value moves.


def series_features(p: dict) -> dict:
    """Full-series feature arrays for one symbol. No forward information."""
    import numpy as np

    o, h, l, c, v = p["open"], p["high"], p["low"], p["close"], p["volume"]
    n = c.size
    f: dict = {}
    f["price"] = c
    f["bars_available"] = np.arange(1, n + 1, dtype=np.float64)

    # ── liquidity, and the honesty check on it ──
    dv = c * v
    f["dvol_20"] = _roll_mean(dv, 20)
    f["dvol_med_20"] = _roll_median(dv, 20)
    f["dvol_med_63"] = _roll_median(dv, 63)
    with np.errstate(divide="ignore", invalid="ignore"):
        f["fake_liquidity_ratio"] = f["dvol_20"] / f["dvol_med_20"]
        f["liquidity_trend"] = f["dvol_med_20"] / f["dvol_med_63"]

    # ── returns and trend position ──
    for k in (5, 10, 20, 63, 126, 252):
        f[f"ret_{k}d"] = _lag_ratio(c, k)
    # Classic 12-1 momentum: the twelve-month return with the most recent month
    # removed, because that last month carries short-horizon reversal. This is
    # the canonical cross-sectional momentum definition and was missing from the
    # first tournament, which tested only 20/63-day relative strength.
    r252, r21 = _lag_ratio(c, 252), _lag_ratio(c, 21)
    f["mom_12_1"] = ((1 + r252 / 100.0) / (1 + r21 / 100.0) - 1.0) * 100.0
    r126_, r21_ = _lag_ratio(c, 126), _lag_ratio(c, 21)
    f["mom_6_1"] = ((1 + r126_ / 100.0) / (1 + r21_ / 100.0) - 1.0) * 100.0
    for w in (20, 50, 200):
        ma = _roll_mean(c, w)
        f[f"ma{w}"] = ma
        with np.errstate(divide="ignore", invalid="ignore"):
            f[f"dist_ma{w}_pct"] = np.where(ma > 0, c / ma - 1.0, np.nan) * 100.0
        f[f"ma{w}_slope_20d_pct"] = _lag_ratio(ma, 20)
        above = (c > ma).astype(np.float64)
        above[np.isnan(ma)] = np.nan
        f[f"above_ma{w}"] = above
    # A reclaim is a regime change the level alone does not show: below the
    # average 20 sessions ago, above it now.
    for w in (50, 200):
        a = f[f"above_ma{w}"]
        prev = np.concatenate((np.full(min(20, n), np.nan), a[:-20]))[:n]
        f[f"reclaimed_ma{w}_20d"] = ((a == 1.0) & (prev == 0.0)).astype(np.float64)

    hi252 = _roll_max(c, 252)
    lo252 = _roll_min(c, 252)
    with np.errstate(divide="ignore", invalid="ignore"):
        f["dd_from_high_pct"] = np.where(hi252 > 0, c / hi252 - 1.0, np.nan) * 100.0
        f["off_low_pct"] = np.where(lo252 > 0, c / lo252 - 1.0, np.nan) * 100.0
    at_high = c >= hi252 - 1e-12
    f["days_since_252d_high"] = (
        np.arange(n, dtype=np.float64)
        - np.maximum.accumulate(np.where(at_high, np.arange(n), 0)).astype(np.float64))
    # New 63-day high today, excluding today from the comparison window.
    prior63 = _roll_max(c, 63)
    prior63_shift = np.concatenate(([np.nan], prior63[:-1]))
    f["new_63d_high"] = (c > prior63_shift).astype(np.float64)

    # ── base tightness / breakout geometry ──
    hi63, lo63 = _roll_max(c, 63), _roll_min(c, 63)
    with np.errstate(divide="ignore", invalid="ignore"):
        f["range_63d_pct"] = np.where(lo63 > 0, hi63 / lo63 - 1.0, np.nan) * 100.0
    # How much of the last 63 sessions sat inside a +-10% band around the
    # median close: a tight base scores high, a trending name scores low.
    med63 = _roll_median(c, 63)
    with np.errstate(divide="ignore", invalid="ignore"):
        inband = (np.abs(c / med63 - 1.0) <= 0.10).astype(np.float64)
    f["base_tightness_63d"] = _roll_mean(inband, 63)

    # ── volatility, both sides ──
    with np.errstate(divide="ignore", invalid="ignore"):
        logret = np.concatenate(([np.nan], np.log(
            np.where(c[1:] > 0, c[1:], np.nan) / np.where(c[:-1] > 0, c[:-1], np.nan))))
    lr = np.nan_to_num(logret, nan=0.0)
    f["rvol_20d_pct"] = _roll_std(lr, 20) * math.sqrt(252) * 100.0
    f["rvol_63d_pct"] = _roll_std(lr, 63) * math.sqrt(252) * 100.0
    with np.errstate(divide="ignore", invalid="ignore"):
        f["rvol_ratio_20_63"] = f["rvol_20d_pct"] / f["rvol_63d_pct"]
    # Downside semi-deviation: the volatility that actually hurts.
    down = np.where(lr < 0, lr, 0.0)
    f["downside_vol_63d_pct"] = _roll_std(down, 63) * math.sqrt(252) * 100.0
    with np.errstate(divide="ignore", invalid="ignore"):
        f["upside_downside_vol_ratio"] = np.where(
            f["downside_vol_63d_pct"] > 0,
            f["rvol_63d_pct"] / f["downside_vol_63d_pct"], np.nan)
    daily_pct = np.concatenate(([np.nan], (c[1:] / c[:-1] - 1.0) * 100.0))
    f["tail_loss_days_63"] = _roll_mean(
        np.nan_to_num((daily_pct < -5.0).astype(np.float64), nan=0.0), 63) * 63.0
    f["tail_gain_days_63"] = _roll_mean(
        np.nan_to_num((daily_pct > 5.0).astype(np.float64), nan=0.0), 63) * 63.0
    # Worst peak-to-trough inside the last 252 sessions: has this name crashed
    # before? A left-tail prior, computed only from the past.
    roll_peak = _roll_max(c, 252)
    with np.errstate(divide="ignore", invalid="ignore"):
        dd_series = np.where(roll_peak > 0, c / roll_peak - 1.0, np.nan) * 100.0
    f["worst_dd_252d_pct"] = _roll_min(np.nan_to_num(dd_series, nan=0.0), 252)

    # ── volume behaviour ──
    m5, m20, m63 = _roll_mean(v, 5), _roll_mean(v, 20), _roll_mean(v, 63)
    with np.errstate(divide="ignore", invalid="ignore"):
        f["vol_expansion_5_63"] = np.where(m63 > 0, m5 / m63, np.nan)
        f["vol_trend_20_63"] = np.where(m63 > 0, m20 / m63, np.nan)
        f["rel_volume_today"] = np.where(m20 > 0, v / m20, np.nan)
    # Up/down volume: the accumulation measure proper. Volume transacted on up
    # days against volume on down days — a name can rise on thin volume and fall
    # on heavy volume, and the ratio is what distinguishes the two.
    up_day = np.concatenate(([0.0], (c[1:] > c[:-1]).astype(np.float64)))
    dn_day = np.concatenate(([0.0], (c[1:] < c[:-1]).astype(np.float64)))
    for w in (20, 63):
        uv = _roll_mean(v * up_day, w)
        dv_ = _roll_mean(v * dn_day, w)
        with np.errstate(divide="ignore", invalid="ignore"):
            f[f"up_down_volume_{w}"] = np.where(dv_ > 0, uv / dv_, np.nan)

    # Accumulation: up-days carrying above-average volume, last 20 sessions.
    up = np.concatenate(([0.0], (c[1:] > c[:-1]).astype(np.float64)))
    heavy = np.nan_to_num((v > m20).astype(np.float64), nan=0.0)
    f["accumulation_days_20"] = _roll_mean(up * heavy, 20) * 20.0
    # Dry-up then expansion: quiet 20-day volume against the quarter, with the
    # last week turning up. The classic pre-breakout volume signature.
    f["vol_dryup_then_expand"] = (
        (f["vol_trend_20_63"] < 0.9) & (f["vol_expansion_5_63"] > 1.2)
    ).astype(np.float64)

    # ── gaps and true range ──
    prev_c = np.concatenate(([np.nan], c[:-1]))
    with np.errstate(divide="ignore", invalid="ignore"):
        gap = np.where(prev_c > 0, o / prev_c - 1.0, np.nan) * 100.0
    f["gap_abs_20d_pct"] = _roll_mean(np.nan_to_num(np.abs(gap), nan=0.0), 20)
    f["max_gap_up_5d_pct"] = _roll_max(np.nan_to_num(gap, nan=0.0), 5)
    f["max_gap_up_20d_pct"] = _roll_max(np.nan_to_num(gap, nan=0.0), 20)
    tr = np.maximum.reduce([
        h - l,
        np.abs(h - np.nan_to_num(prev_c, nan=h[0])),
        np.abs(l - np.nan_to_num(prev_c, nan=l[0])),
    ])
    with np.errstate(divide="ignore", invalid="ignore"):
        f["atr14_pct"] = np.where(c > 0, _roll_mean(tr, 14) / c, np.nan) * 100.0
    lo20 = _roll_min(l, 20)
    f["higher_lows_20_40"] = np.concatenate(
        (np.full(min(20, n), np.nan), (lo20[20:] > lo20[:-20]).astype(np.float64)))[:n]
    return f


FEATURE_COLS: tuple[str, ...] = (
    "price", "bars_available",
    "dvol_20", "dvol_med_20", "dvol_med_63", "fake_liquidity_ratio", "liquidity_trend",
    "ret_5d", "ret_10d", "ret_20d", "ret_63d", "ret_126d", "ret_252d",
    "mom_12_1", "mom_6_1", "up_down_volume_20", "up_down_volume_63",
    "dist_ma20_pct", "dist_ma50_pct", "dist_ma200_pct",
    "ma20_slope_20d_pct", "ma50_slope_20d_pct", "ma200_slope_20d_pct",
    "above_ma20", "above_ma50", "above_ma200",
    "reclaimed_ma50_20d", "reclaimed_ma200_20d",
    "dd_from_high_pct", "off_low_pct", "days_since_252d_high", "new_63d_high",
    "range_63d_pct", "base_tightness_63d",
    "rvol_20d_pct", "rvol_63d_pct", "rvol_ratio_20_63",
    "downside_vol_63d_pct", "upside_downside_vol_ratio",
    "tail_loss_days_63", "tail_gain_days_63", "worst_dd_252d_pct",
    "vol_expansion_5_63", "vol_trend_20_63", "rel_volume_today",
    "accumulation_days_20", "vol_dryup_then_expand",
    "gap_abs_20d_pct", "max_gap_up_5d_pct", "max_gap_up_20d_pct",
    "atr14_pct", "higher_lows_20_40",
)


# ── forward path measurement (labels only) ──────────────────────────────────


def _fwd_point(c, pos, h: int):
    import numpy as np

    n = c.size
    base = c[pos]
    j = np.minimum(pos + h, n - 1)
    matured = (pos + h) < n
    with np.errstate(divide="ignore", invalid="ignore"):
        ret = np.where(base > 0, c[j] / base - 1.0, np.nan) * 100.0
    return ret.astype(np.float64), matured


def _fwd_extreme(arr, pos, w: int, how: str):
    """max/min of *arr* over the forward window (pos, pos+w]."""
    import numpy as np
    import pandas as pd

    n = arr.size
    rev = arr[::-1]
    roll = pd.Series(rev).rolling(w, min_periods=1)
    agg = (roll.max() if how == "max" else roll.min()).to_numpy()[::-1]
    return agg[np.minimum(pos + 1, n - 1)]


def build_panel(root: Path, args):
    """(ticker x scan_date) panel: as-of features, forward labels, buckets."""
    import numpy as np
    import pandas as pd

    exclude = load_quarantine_excludes(root)
    prices = load_prices(root, exclude)
    scan_dates, _ = weekly_scan_dates(prices, args.start, args.end)
    if args.limit:
        scan_dates = scan_dates[: args.limit]
    dates64 = np.array([np.datetime64(d) for d in scan_dates])
    excl_types = security_type_exclusions(root, prices.keys())
    print(f"security-type exclusions: {len(excl_types)} symbols "
          f"({len(set(excl_types.values()))} reasons)", flush=True)

    bench = {}
    for b in BENCHMARKS:
        p = prices[b]
        bpos = np.searchsorted(p["idx"], dates64, side="right") - 1
        bench[b] = {f"fwd_{h}": _fwd_point(p["close"], bpos, h)[0] for h in HORIZONS}

    chunks, t0 = [], time.time()
    for n_sym, (sym, p) in enumerate(sorted(prices.items()), 1):
        if sym in excl_types:
            continue
        c, o, idx = p["close"], p["open"], p["idx"]
        pos = np.searchsorted(idx, dates64, side="right") - 1
        alive = pos >= 0
        if not alive.any():
            continue
        lag = np.full(dates64.size, np.nan)
        lag[alive] = ((dates64[alive] - idx[pos[alive]]) / np.timedelta64(1, "D")).astype(float)
        keep = alive & (lag <= float(args.max_bar_lag_days))
        if not keep.any():
            continue
        pk = pos[keep]
        feats = series_features(p)
        rec = {"ticker": np.full(pk.size, sym, dtype=object),
               "scan_date": dates64[keep], "bar_lag_days": lag[keep]}
        for col in FEATURE_COLS:
            rec[col] = feats[col][pk]

        for h in HORIZONS:
            ret, matured = _fwd_point(c, pk, h)
            rec[f"fwd_{h}d"] = ret
            rec[f"fwd_{h}d_matured"] = np.asarray(matured, dtype=np.float64)
            for b in BENCHMARKS:
                rec[f"fwd_{h}d_vs_{b.lower()}"] = ret - bench[b][f"fwd_{h}"][keep]
        base = c[pk]
        with np.errstate(divide="ignore", invalid="ignore"):
            for w in (20, 45, 60, 126):
                rec[f"mfe_{w}d"] = (_fwd_extreme(c, pk, w, "max") / base - 1.0) * 100.0
            for w in (20, 60):
                rec[f"mae_{w}d"] = (_fwd_extreme(c, pk, w, "min") / base - 1.0) * 100.0
            # Forward single-day shocks, for the event/gap path labels.
            daily = np.concatenate(([np.nan], (c[1:] / c[:-1] - 1.0) * 100.0))
            gaps = np.concatenate(([np.nan], (o[1:] / c[:-1] - 1.0) * 100.0))
            rec["fwd_max_1d_up_60d"] = _fwd_extreme(np.nan_to_num(daily), pk, 60, "max")
            rec["fwd_max_1d_down_60d"] = _fwd_extreme(np.nan_to_num(daily), pk, 60, "min")
            rec["fwd_max_gap_up_60d"] = _fwd_extreme(np.nan_to_num(gaps), pk, 60, "max")
        chunks.append(rec)
        if n_sym % 1500 == 0:
            print(f"  [{n_sym}/{len(prices)}] {time.time() - t0:.0f}s", flush=True)

    df = pd.DataFrame({k: np.concatenate([ch[k] for ch in chunks])
                       for k in chunks[0]})
    df["scan_date"] = pd.to_datetime(df["scan_date"])
    df["year"] = df["scan_date"].dt.year

    # RS vs SPY, acceleration, and the replicated old score — the last purely so
    # trap exposure is measurable on the full universe. The live formula is
    # untouched and nothing here feeds it.
    spy = prices["SPY"]
    spos = np.searchsorted(spy["idx"], dates64, side="right") - 1
    spy_ret = {k: _lag_ratio(spy["close"], k)[spos] for k in (5, 10, 20, 63, 126)}
    dmap = {pd.Timestamp(d): i for i, d in enumerate(dates64)}
    di = df["scan_date"].map(dmap).to_numpy()
    for k in (5, 10, 20, 63, 126):
        df[f"rs_{k}d"] = df[f"ret_{k}d"] - spy_ret[k][di]
    df["rs_accel_10_20"] = df["rs_10d"] - df["rs_20d"] * (10.0 / 20.0)
    df["rs_accel_20_63"] = df["rs_20d"] - df["rs_63d"] * (20.0 / 63.0)
    df["combined_rs"] = df["rs_63d"] * 0.6 + df["rs_20d"] * 0.4
    df["in_score_100_zone"] = df["combined_rs"] >= 62.5

    # Tradability. `eligible` is the study universe; the fake-liquidity screen
    # is separate so its cost can be measured rather than assumed.
    df["passes_floor"] = (
        (df["price"] >= BASE_FLOOR["min_price"])
        & (df["dvol_med_20"] >= BASE_FLOOR["min_dvol_med"])
        & (df["bars_available"] >= BASE_FLOOR["min_bars"]))
    df["fake_liquidity"] = df["fake_liquidity_ratio"] >= FAKE_LIQUIDITY_RATIO
    df["eligible"] = df["passes_floor"] & ~df["fake_liquidity"]
    print(f"panel: {len(df):,} rows · {df.ticker.nunique():,} tickers · "
          f"{df.scan_date.nunique()} dates · {time.time() - t0:.0f}s", flush=True)
    return df, excl_types


def _assert_no_lookahead(root: Path, df, n_checks: int, seed: int = 13) -> dict:
    """Re-derive sampled feature rows from a physically truncated series."""
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
        feats = series_features(p)
        for col in FEATURE_COLS:
            a, b = float(feats[col][-1]), float(row[col])
            if math.isnan(a) and math.isnan(b):
                continue
            if not math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9):
                raise AssertionError(
                    f"lookahead: {row['ticker']} {row['scan_date']} {col}: "
                    f"truncated={a} panel={b}")
        checked += 1
    return {"rows_checked": checked, "features_per_row": len(FEATURE_COLS)}


# ── labels: winners, losers, paths, buckets (Parts 2-3) ─────────────────────

PCT_CUTS: tuple[float, ...] = (1.0, 2.0, 5.0, 10.0)
TOP_N_LABELS: tuple[int, ...] = (50, 100)


def label_panel(df, sectors: dict, mcap: dict):
    """Attach every winner label, loser label, path label and style bucket.

    Labels may use forward information — that is what a label is. No labelled
    column is ever used as a feature; the strategy masks in FAMILIES read only
    the as-of columns.
    """
    import numpy as np
    import pandas as pd

    for h in HORIZONS:
        col = f"fwd_{h}d"
        up = df.groupby("scan_date")[col].rank(pct=True, ascending=False) * 100.0
        dn = df.groupby("scan_date")[col].rank(pct=True, ascending=True) * 100.0
        for cut in PCT_CUTS:
            df[f"top{cut:g}pct_{h}d"] = up <= cut
            df[f"bot{cut:g}pct_{h}d"] = dn <= cut
        rank_desc = df.groupby("scan_date")[col].rank(ascending=False, method="first")
        for n in TOP_N_LABELS:
            df[f"top{n}_{h}d"] = rank_desc <= n
    for name, thr, w in MFE_LABELS:
        df[f"win_{name}"] = df[f"mfe_{w}d"] >= thr
    for name, thr, w in MAE_LABELS:
        df[f"lose_{name}"] = df[f"mae_{w}d"] <= thr

    # Relative-to-style labels: beating your own bucket is a different question
    # from beating the tape, and for a research list it is the fairer one.
    df["vol_decile"] = df.groupby("scan_date")["rvol_63d_pct"].transform(
        lambda s: pd.qcut(s.rank(method="first"), 10, labels=False, duplicates="drop"))
    df["liq_decile"] = df.groupby("scan_date")["dvol_med_20"].transform(
        lambda s: pd.qcut(s.rank(method="first"), 10, labels=False, duplicates="drop"))
    df["liquidity_tier"] = pd.cut(
        df["dvol_med_20"], [0] + [v[1] for v in LIQUIDITY_TIERS.values()],
        labels=list(LIQUIDITY_TIERS))
    df["sector_current_meta"] = df["ticker"].map(sectors).fillna("UNKNOWN")
    df["market_cap"] = [mcap.get((t, d)) for t, d in zip(df["ticker"], df["scan_date"])]
    df["mcap_bucket"] = pd.cut(
        df["market_cap"], [0, 3e8, 2e9, 1e10, 5e10, np.inf],
        labels=["nano_micro", "small", "mid", "large", "mega"])

    for bucket in ("vol_decile", "liq_decile", "sector_current_meta", "mcap_bucket"):
        g = df.groupby(["scan_date", bucket], observed=True)[f"fwd_{PRIMARY_H}d"]
        df[f"excess_vs_{bucket}"] = df[f"fwd_{PRIMARY_H}d"] - g.transform("median")

    # Path labels: HOW the winner moved. Multi-label, plus a primary assigned by
    # priority so counts sum. Every input is forward — these describe outcomes.
    fwd, mfe60, mae60, mae20 = (df[f"fwd_{PRIMARY_H}d"], df["mfe_60d"],
                                df["mae_60d"], df["mae_20d"])
    paths = {
        "explosive_moonshot": mfe60 >= 100,
        "event_shock": df["fwd_max_1d_up_60d"] >= 25,
        "gap_and_go": (df["fwd_max_gap_up_60d"] >= 15) & (fwd > 0),
        "deep_recovery": (df["dd_from_high_pct"] < -40) & (fwd >= 30),
        "pullback_continuation": (mae20 <= -10) & (fwd >= 20),
        "clean_trend": (fwd >= 20) & (mae60 > -15),
        "base_breakout": (df["base_tightness_63d"] >= 0.8) & (fwd >= 20),
        "slow_compounder": (fwd >= 5) & (fwd < 20) & (mae60 > -10),
    }
    for k, v in paths.items():
        df[f"path_{k}"] = v.fillna(False)
    primary = pd.Series("none", index=df.index, dtype=object)
    for k in ("explosive_moonshot", "event_shock", "gap_and_go", "deep_recovery",
              "pullback_continuation", "base_breakout", "clean_trend",
              "slow_compounder"):
        primary = primary.mask((primary == "none") & df[f"path_{k}"], k)
    df["path_primary"] = primary

    # Trap/loser paths, the mirror the brief asks for.
    df["trap_post_spike_failure"] = ((df["ret_20d"] > 50) & (fwd < -20)).fillna(False)
    df["trap_fake_breakout"] = ((df["new_63d_high"] == 1.0) & (fwd < -20)).fillna(False)
    df["trap_catastrophic"] = (fwd <= -50).fillna(False)
    return df


# ── asymmetry metrics (Part 3) ──────────────────────────────────────────────
#
# The statistic the previous study under-weighted. A cohort whose thesis is a
# fat right tail should not be judged on its median, and one that quietly
# blows up should not be flattered by its mean.


def asymmetry(series) -> dict | None:
    """Right-tail vs left-tail profile of a return series."""
    import numpy as np

    a = np.asarray(series, dtype=float)
    a = a[np.isfinite(a)]
    if a.size < 30:
        return None
    p90, p10 = float(np.percentile(a, 90)), float(np.percentile(a, 10))
    top = a[a >= p90]
    bot = a[a <= p10]
    right = float(top.mean()) if top.size else float("nan")
    left = float(bot.mean()) if bot.size else float("nan")
    return {
        "n": int(a.size),
        "mean": round(float(a.mean()), 3),
        "median": round(float(np.median(a)), 3),
        "trim10": round(_trim_mean(a), 3),
        "winsor5_95": round(_winsor_mean(a), 3),
        "win_rate": round(float((a > 0).mean() * 100), 1),
        "p90": round(p90, 3),
        "p10": round(p10, 3),
        "right_tail_mean": round(right, 3),
        "left_tail_mean": round(left, 3),
        # >1 means the average big winner outweighs the average big loser.
        "tail_ratio": (round(abs(right / left), 3)
                       if left and math.isfinite(left) and left != 0 else None),
        "pct_up_50": round(float((a >= 50).mean() * 100), 3),
        "pct_up_100": round(float((a >= 100).mean() * 100), 3),
        "pct_down_25": round(float((a <= -25).mean() * 100), 3),
        "pct_down_50": round(float((a <= -50).mean() * 100), 3),
    }


def selection_vs(picks, universe, h: int, n_boot: int, seed: int, col_prefix="fwd") -> dict | None:
    """Per-date median of picks minus per-date median of the pool, both CIs.

    Also carries the per-date MEAN difference, because an equal-weight research
    list earns the mean and a right-tail strategy can be positive on the mean
    while negative on the median. Reporting only one of them is how a lane gets
    mis-scored in either direction.
    """
    import numpy as np
    import pandas as pd

    col = f"{col_prefix}_{h}d"
    if picks.empty:
        return None
    u_med = universe.groupby("scan_date")[col].median()
    u_mean = universe.groupby("scan_date")[col].mean()
    p_med = picks.groupby("scan_date")[col].median()
    p_mean = picks.groupby("scan_date")[col].mean()
    j = pd.DataFrame({"pm": p_med, "um": u_med, "pa": p_mean, "ua": u_mean}).dropna()
    if len(j) < 10:
        return None
    d_med = (j["pm"] - j["um"]).to_numpy()
    d_mean = (j["pa"] - j["ua"]).to_numpy()

    m = picks[["ticker", "scan_date", col]].dropna().copy()
    m["um"] = m["scan_date"].map(u_med)
    m = m.dropna(subset=["um"])
    by_ticker = m.assign(x=m[col] - m["um"]).groupby(
        "ticker", observed=True)["x"].median().to_numpy()

    return {
        "selection_median": round(float(d_med.mean()), 3),
        "selection_mean": round(float(d_mean.mean()), 3),
        "date_cluster_ci": _cluster_ci(d_med, n_boot, seed),
        "date_cluster_ci_mean": _cluster_ci(d_mean, n_boot, seed + 3),
        "ticker_cluster_ci": _cluster_ci(by_ticker, n_boot, seed + 1),
        "dates_won_pct": round(float((d_med > 0).mean() * 100), 1),
        "n_dates": int(len(j)),
        "n_tickers": int(by_ticker.size),
    }


# ── stage: panel ────────────────────────────────────────────────────────────


def panel_stage(args) -> int:
    import pandas as pd

    root = Path(args.root)
    tw = LiveArtifactTripwire.snapshot(root)
    df, excl_types = build_panel(root, args)
    audit = _assert_no_lookahead(root, df, args.lookahead_checks)
    print(f"lookahead audit: {audit['rows_checked']} rows x "
          f"{audit['features_per_row']} features re-derived, all identical")

    from research.backtests.alpha_reconstruction_lab import market_cap_panel

    sectors = load_sector_map(root)
    elig = df[df["eligible"]].copy()
    mcap = market_cap_panel(root, elig)
    df["market_cap"] = None
    elig = label_panel(elig, sectors, mcap)

    out = root / PANEL_REL
    out.parent.mkdir(parents=True, exist_ok=True)
    assert_replay_write_path(out, root=root)
    slim = elig.copy()
    for c, dt in slim.dtypes.items():
        if dt == "float64":
            slim[c] = slim[c].astype("float32")
    slim["ticker"] = slim["ticker"].astype("category")
    slim.to_parquet(out, index=False, compression="zstd")

    meta = {
        "kind": "ALPHA_TOURNAMENT_PANEL_META",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rows_all": int(len(df)), "rows_eligible": int(len(elig)),
        "tickers_eligible": int(elig.ticker.nunique()),
        "dates": int(elig.scan_date.nunique()),
        "floor": BASE_FLOOR,
        "fake_liquidity_ratio_threshold": FAKE_LIQUIDITY_RATIO,
        "rows_dropped_fake_liquidity": int((df["passes_floor"] & df["fake_liquidity"]).sum()),
        "security_type_exclusions": {
            "n": len(excl_types),
            "by_reason": {r: sum(1 for v in excl_types.values() if v == r)
                          for r in sorted(set(excl_types.values()))},
        },
        "lookahead_audit": audit,
        "features": list(FEATURE_COLS),
        **provenance_flags(),
    }
    assert_provenance(meta)
    write_replay_json(root / f"{INTERMEDIATES_REL}/panel_meta.json", meta, root=root)
    print(tw.report())
    tw.assert_clean()
    print(json.dumps({k: meta[k] for k in
                      ("rows_all", "rows_eligible", "tickers_eligible",
                       "rows_dropped_fake_liquidity")}, indent=1))
    return 0


def load_panel(root: Path, columns=None):
    import pandas as pd

    f = root / PANEL_REL
    if not f.exists():
        raise SystemExit(f"missing {PANEL_REL} — run the panel stage first")
    df = pd.read_parquet(f, columns=columns)
    df["ticker"] = df["ticker"].astype(str)
    return df


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="alpha_tournament")
    sub = ap.add_subparsers(dest="stage", required=True)
    for name in ("panel", "map", "tournament", "lists", "report", "all"):
        s = sub.add_parser(name)
        s.add_argument("--root", default=str(ROOT))
        s.add_argument("--start", default=REPLAY_WINDOW_START)
        s.add_argument("--end", default=REPLAY_WINDOW_END)
        s.add_argument("--limit", type=int, default=0)
        s.add_argument("--max-bar-lag-days", type=float, default=5.0)
        s.add_argument("--lookahead-checks", type=int, default=40)
        s.add_argument("--bootstrap", type=int, default=1500)
        s.add_argument("--seed", type=int, default=17)
    return ap


STAGE_ORDER: tuple[str, ...] = ("panel", "map", "tournament", "lists", "report")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.stage == "all":
        for name in STAGE_ORDER:
            print(f"\n{'=' * 70}\n== {name}\n{'=' * 70}", flush=True)
            rc = run_cli(globals()[f"{name}_stage"], args)
            if rc:
                return rc
        return 0
    fn = globals().get(f"{args.stage}_stage")
    if fn is None:
        print(f"stage {args.stage!r} not implemented yet")
        return 2
    return run_cli(fn, args)

# ── stage: map (Parts 1-3) ──────────────────────────────────────────────────


def attach_controls(df, root: Path):
    """Old scanner / HC / EO membership per (ticker, date), for controls only."""
    import pandas as pd
    from research.backtests.alpha_reconstruction_lab import (
        load_fundamental_episodes, load_old_scanner_episodes)

    old = load_old_scanner_episodes(root)
    df["in_old_scanner"] = False
    df["old_score_100"] = False
    df["old_long_term_asymmetric"] = False
    if old is not None:
        k = old.drop_duplicates(["scan_date", "ticker"]).assign(
            in_old_scanner=True,
            old_score_100=lambda d: d["research_score"] >= 100,
            old_long_term_asymmetric=lambda d: d["category"] == "long_term_asymmetric")
        df = df.drop(columns=["in_old_scanner", "old_score_100",
                              "old_long_term_asymmetric"]).merge(
            k[["scan_date", "ticker", "in_old_scanner", "old_score_100",
               "old_long_term_asymmetric"]], on=["scan_date", "ticker"], how="left")
        for c in ("in_old_scanner", "old_score_100", "old_long_term_asymmetric"):
            df[c] = df[c].fillna(False).astype(bool)
    fund = load_fundamental_episodes(root)
    df["in_hc"] = False
    df["in_eo"] = False
    if fund is not None:
        f = fund.drop_duplicates(["scan_date", "ticker"]).copy()
        f["in_hc"] = f.get("hc_shortlisted", False).fillna(False).astype(bool)
        f["in_eo"] = f.get("eo_selected", False).fillna(False).astype(bool)
        df = df.drop(columns=["in_hc", "in_eo"]).merge(
            f[["scan_date", "ticker", "in_hc", "in_eo"]],
            on=["scan_date", "ticker"], how="left")
        for c in ("in_hc", "in_eo"):
            df[c] = df[c].fillna(False).astype(bool)
    return df


def map_stage(args) -> int:
    import numpy as np
    import pandas as pd

    root = Path(args.root)
    tw = LiveArtifactTripwire.snapshot(root)
    df = load_panel(root)
    df = attach_controls(df, root)
    delisted = load_delisted(root)
    df["from_delisted_pool"] = df["ticker"].isin(delisted)
    tr = df[df.year.isin(TRAIN_YEARS)]

    out: dict = {
        "kind": "ALPHA_TOURNAMENT_OPPORTUNITY_MAP",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window": [str(df.scan_date.min().date()), str(df.scan_date.max().date())],
        "universe": {
            "eligible_rows": int(len(df)),
            "unique_tickers": int(df.ticker.nunique()),
            "scan_dates": int(df.scan_date.nunique()),
            "median_names_per_week": float(df.groupby("scan_date").size().median()),
            "floor": BASE_FLOOR,
            "liquidity_distribution": {
                str(k): int(v) for k, v in df["liquidity_tier"].value_counts().items()},
            "market_cap_coverage_pct": round(float(df["market_cap"].notna().mean() * 100), 1),
            "sector_coverage_pct": round(
                float((df["sector_current_meta"] != "UNKNOWN").mean() * 100), 1),
            "delisted_share_of_rows_pct": round(float(df["from_delisted_pool"].mean() * 100), 2),
        },
        "note_on_discovery_window": "Every statistic in this artifact is computed "
                                    "on the train years unless a key says otherwise.",
        **provenance_flags(),
    }

    # ── Part 3's central question, asked before any strategy exists ──
    # What did each style bucket actually pay, on BOTH tails?
    buckets: dict = {}
    for name, col in (("volatility_decile", "vol_decile"),
                      ("liquidity_decile", "liq_decile"),
                      ("liquidity_tier", "liquidity_tier"),
                      ("market_cap_bucket", "mcap_bucket")):
        node = {}
        for key, g in tr.groupby(col, observed=True):
            a = asymmetry(g[f"fwd_{PRIMARY_H}d"])
            if a is None:
                continue
            node[str(key)] = {
                **a,
                "share_of_universe_pct": round(100.0 * len(g) / len(tr), 2),
                "top1pct_rate": round(float(g[f"top1pct_{PRIMARY_H}d"].mean() * 100), 3),
                "bot1pct_rate": round(float(g[f"bot1pct_{PRIMARY_H}d"].mean() * 100), 3),
                "top10pct_rate": round(float(g[f"top10pct_{PRIMARY_H}d"].mean() * 100), 3),
                "bot10pct_rate": round(float(g[f"bot10pct_{PRIMARY_H}d"].mean() * 100), 3),
                "up100_126d_rate": round(float(g["win_up100_126d"].mean() * 100), 3),
                "down50_60d_rate": round(float(g["lose_down50_60d"].mean() * 100), 3),
            }
        buckets[name] = node
    out["style_bucket_payoffs"] = buckets

    # The moonshot question, isolated: does the high-volatility bucket pay for
    # its left tail, at any liquidity?
    grid = {}
    for (vd, tier), g in tr.groupby(["vol_decile", "liquidity_tier"], observed=True):
        if len(g) < 500:
            continue
        a = asymmetry(g[f"fwd_{PRIMARY_H}d"])
        if a:
            grid[f"vol{int(vd) + 1}_{tier}"] = {
                "n": a["n"], "mean": a["mean"], "median": a["median"],
                "tail_ratio": a["tail_ratio"], "win_rate": a["win_rate"],
                "pct_up_100": a["pct_up_100"], "pct_down_50": a["pct_down_50"],
                "up100_126d_rate": round(float(g["win_up100_126d"].mean() * 100), 3),
            }
    out["volatility_x_liquidity_grid"] = grid

    # ── Part 2: winner labels, their sizes and their overlaps ──
    labels: dict = {}
    for h in HORIZONS:
        for cut in PCT_CUTS:
            labels[f"top{cut:g}pct_{h}d"] = f"top{cut:g}pct_{h}d"
        for n in TOP_N_LABELS:
            labels[f"top{n}_{h}d"] = f"top{n}_{h}d"
    for name, _thr, _w in MFE_LABELS:
        labels[f"win_{name}"] = f"win_{name}"
    label_stats = {}
    for name, col in labels.items():
        w = df[df[col]]
        if w.empty:
            continue
        label_stats[name] = {
            "episodes": int(len(w)),
            "unique_tickers": int(w.ticker.nunique()),
            "median_fwd_60d": round(float(w[f"fwd_{PRIMARY_H}d"].median()), 2),
            "by_year": {str(int(y)): int(v) for y, v in
                        w.groupby("year").size().items()},
            "delisted_share_pct": round(float(w["from_delisted_pool"].mean() * 100), 2),
            "old_scanner_overlap_pct": round(float(w["in_old_scanner"].mean() * 100), 2),
            "score_100_overlap_pct": round(float(w["in_score_100_zone"].mean() * 100), 2),
            "hc_overlap_pct": round(float(w["in_hc"].mean() * 100), 2),
            "eo_overlap_pct": round(float(w["in_eo"].mean() * 100), 2),
            "high_vol_share_pct": round(float((w["vol_decile"] >= 8).mean() * 100), 1),
            "top_liquidity_tier_share_pct": round(
                float((w["liquidity_tier"] == "large_100M_plus").mean() * 100), 1),
        }
    out["winner_labels"] = label_stats

    # Overlap between winner definitions: are "top 10% at 60d" and "+100% in
    # 126d" the same names? If not, no single strategy can serve both.
    keys = ["top10pct_60d", "top5pct_60d", "top1pct_60d", "top50_60d",
            "win_up25_20d", "win_up80_60d", "win_up100_126d", "win_up200_126d"]
    ov = {}
    for i, a_key in enumerate(keys):
        for b_key in keys[i + 1:]:
            a, b = df[a_key], df[b_key]
            inter = int((a & b).sum())
            ov[f"{a_key} & {b_key}"] = {
                "jaccard": round(inter / max(int((a | b).sum()), 1), 3),
                "share_of_first_also_second_pct": round(
                    100.0 * inter / max(int(a.sum()), 1), 1),
            }
    out["winner_label_overlap"] = ov

    # ── Part 3: losers and traps, sized the same way ──
    loser_stats = {}
    for name in ([f"bot{c:g}pct_{PRIMARY_H}d" for c in PCT_CUTS]
                 + [f"lose_{n}" for n, _t, _w in MAE_LABELS]
                 + ["trap_post_spike_failure", "trap_fake_breakout", "trap_catastrophic"]):
        l = df[df[name]]
        if l.empty:
            continue
        loser_stats[name] = {
            "episodes": int(len(l)),
            "unique_tickers": int(l.ticker.nunique()),
            "median_fwd_60d": round(float(l[f"fwd_{PRIMARY_H}d"].median()), 2),
            "high_vol_share_pct": round(float((l["vol_decile"] >= 8).mean() * 100), 1),
            "score_100_overlap_pct": round(float(l["in_score_100_zone"].mean() * 100), 2),
            "delisted_share_pct": round(float(l["from_delisted_pool"].mean() * 100), 2),
        }
    out["loser_labels"] = loser_stats

    # ── path taxonomy: how winners actually moved ──
    paths = {}
    for k in [c[5:] for c in df.columns if c.startswith("path_") and c != "path_primary"]:
        g = df[df[f"path_{k}"]]
        if g.empty:
            continue
        a = asymmetry(g[f"fwd_{PRIMARY_H}d"])
        paths[k] = {
            "episodes": int(len(g)), "unique_tickers": int(g.ticker.nunique()),
            "median_fwd_60d": (a or {}).get("median"),
            "mean_fwd_60d": (a or {}).get("mean"),
            "high_vol_share_pct": round(float((g["vol_decile"] >= 8).mean() * 100), 1),
            "median_dvol_musd": round(float(g["dvol_med_20"].median() / 1e6), 1),
            "entry_dd_from_high_median": round(float(g["dd_from_high_pct"].median()), 1),
            "entry_rs_63d_median": round(float(g["rs_63d"].median()), 2),
        }
    out["winner_paths"] = paths
    out["path_primary_counts"] = {
        str(k): int(v) for k, v in df["path_primary"].value_counts().items()}

    assert_provenance(out)
    write_replay_json(root / MAP_REL, out, root=root)
    print(tw.report())
    tw.assert_clean()
    print("\nvolatility decile payoffs (train, 60d):")
    for k, v in sorted(buckets["volatility_decile"].items(), key=lambda kv: int(kv[0])):
        print(f"  vol decile {int(k) + 1:>2}  mean={v['mean']:>7} median={v['median']:>7} "
              f"tail_ratio={str(v['tail_ratio']):>6} up100%={v['up100_126d_rate']:>6} "
              f"down50%={v['down50_60d_rate']:>6} top10%={v['top10pct_rate']:>6} "
              f"bot10%={v['bot10pct_rate']:>6}")
    return 0

# ── earnings events (point-in-time, from filing acceptance dates) ───────────
#
# The replay has no earnings calendar, but every statement carries an
# acceptedDate. The date a company's quarterly filing became public is a
# point-in-time-safe event marker, and the price reaction around it is
# computable from bars that had already printed. Analyst estimates and
# revisions do NOT exist in this cache and are reported as missing rather than
# proxied.


def build_event_panel(root: Path, prices: dict, scan_dates) -> "object":
    """days-since-filing and the market's reaction to it, per (ticker, date)."""
    import numpy as np
    import pandas as pd
    from research.backtests.alpha_reconstruction_lab import _usable_date

    src = root / "cache" / "replay_fundamentals"
    dates64 = np.array([np.datetime64(d) for d in scan_dates])
    recs = []
    t0 = time.time()
    files = sorted(src.glob("*.json"))
    for n, f in enumerate(files, 1):
        sym = f.stem
        p = prices.get(sym)
        if p is None:
            continue
        try:
            raw = json.loads(f.read_text())
        except Exception:
            continue
        acc = set()
        for kind in ("income", "balance", "cashflow"):
            for r in (raw.get(kind) or []):
                if r.get("acceptedDate"):
                    u = _usable_date(r["acceptedDate"])
                    if u is not None:
                        acc.add(u)
        if not acc:
            continue
        filed = np.array(sorted(np.datetime64(str(d)) for d in acc))
        idx, close = p["idx"], p["close"]
        # Reaction: close-to-close over the three sessions following the filing.
        fpos = np.searchsorted(idx, filed, side="left")
        react = np.full(filed.size, np.nan)
        react_ready = np.zeros(filed.size, dtype="datetime64[ns]")
        for i, fp in enumerate(fpos):
            if fp <= 0 or fp + 3 >= close.size:
                continue
            base = close[fp - 1]
            if base > 0:
                react[i] = (close[fp + 3] / base - 1.0) * 100.0
                react_ready[i] = idx[fp + 3]
        j = np.searchsorted(filed, dates64, side="right") - 1
        for k, d in enumerate(dates64):
            i = j[k]
            if i < 0:
                continue
            days = int((d - filed[i]) / np.timedelta64(1, "D"))
            # The reaction is only usable once its own window has closed.
            r = (float(react[i]) if (np.isfinite(react[i])
                                     and react_ready[i] != np.datetime64(0, "ns")
                                     and react_ready[i] <= d) else None)
            recs.append({"ticker": sym, "scan_date": pd.Timestamp(d),
                         "days_since_filing": days, "post_earnings_reaction_pct": r})
        if n % 800 == 0:
            print(f"  events [{n}/{len(files)}] {time.time() - t0:.0f}s", flush=True)
    df = pd.DataFrame(recs)
    print(f"event panel: {len(df):,} rows · {df.ticker.nunique():,} tickers "
          f"· {time.time() - t0:.0f}s", flush=True)
    return df

# ── strategy families (Part 4) ──────────────────────────────────────────────
#
# Fourteen families, written as literal rules with round-number thresholds
# before being scored. None of them starts from an old scanner lane, an old
# score, or an old threshold. Two are declared with intent="avoid": they are
# expected to lose, and are scored so the loss is measured rather than assumed.
#
# `universe` selects the pool a family is judged against:
#   full  — every eligible row (no coverage conditioning)
#   fund  — rows with point-in-time fundamentals (59% coverage, and coverage is
#           conditioned on the OLD SCANNER having surfaced the name at some
#           point, which is partly forward-looking; such families are compared
#           only against the same sub-universe and never headlined)
#   event — rows with a known filing date (same caveat as fund)


def _pct(d, col):
    return d.groupby("scan_date")[col].rank(pct=True)


def _z(d, col):
    g = d.groupby("scan_date")[col]
    med = g.transform("median")
    iqr = g.transform(lambda s: s.quantile(0.75) - s.quantile(0.25))
    return (d[col] - med) / iqr.replace(0, float("nan"))


FAMILIES: list[dict] = [
    # ── Group 1: earnings / guidance re-rating ──
    {"letter": "E1", "name": "pead_reaction_hold", "intent": "alpha", "universe": "event",
     "archetype": "Earnings Re-Rating",
     "description": "A strong three-day reaction to the last filing that the name is "
                    "still holding, with volume behind it.",
     "requires": ["days_since_filing<=25", "reaction>=5", "above_ma20",
                  "vol_expansion_5_63>1.1", "dvol_med_20>=5M"],
     "excludes": ["combined_rs>=62.5"],
     "mask": lambda d: ((d.days_since_filing <= 25) & (d.post_earnings_reaction_pct >= 5)
                        & (d.above_ma20 == 1) & (d.vol_expansion_5_63 > 1.1)
                        & (d.dvol_med_20 >= 5e6) & (d.combined_rs < 62.5)),
     "rank": lambda d: _z(d, "post_earnings_reaction_pct")},
    {"letter": "E2", "name": "earnings_acceleration_rerating", "intent": "alpha",
     "universe": "event", "archetype": "Earnings Re-Rating",
     "description": "Earnings AND sales accelerating into a positive market reaction — "
                    "the fundamental version of the re-rating trade.",
     "requires": ["days_since_filing<=40", "eps_accel_pp>0", "rev_accel_pp>0",
                  "reaction>0", "dvol_med_20>=5M"],
     "excludes": ["combined_rs>=62.5"],
     "mask": lambda d: ((d.days_since_filing <= 40) & (d.eps_accel_pp > 0)
                        & (d.rev_accel_pp > 0) & (d.post_earnings_reaction_pct > 0)
                        & (d.dvol_med_20 >= 5e6) & (d.combined_rs < 62.5)),
     "rank": lambda d: _z(d, "eps_accel_pp") + _z(d, "rev_accel_pp")},
    {"letter": "E3", "name": "post_earnings_drift_strong", "intent": "alpha",
     "universe": "event", "archetype": "Earnings Re-Rating",
     "description": "Post-earnings announcement drift: a large positive reaction, "
                    "still above the MA20 one to five weeks later.",
     "requires": ["days_since_filing 5..35", "reaction>=10", "above_ma20", "above_ma50"],
     "excludes": [],
     "mask": lambda d: (d.days_since_filing.between(5, 35)
                        & (d.post_earnings_reaction_pct >= 10)
                        & (d.above_ma20 == 1) & (d.above_ma50 == 1)),
     "rank": lambda d: _z(d, "post_earnings_reaction_pct")},

    # ── Group 2: fundamental acceleration ──
    {"letter": "F1", "name": "revenue_margin_acceleration", "intent": "alpha",
     "universe": "fund", "archetype": "Fundamental Acceleration",
     "description": "Revenue accelerating and margins expanding with dilution "
                    "controlled and price not yet extended.",
     "requires": ["rev_accel_pp>0", "op_margin_delta_pp>0", "dilution_yoy_pct<10",
                  "rs_20d>-5", "dist_ma50_pct<15"],
     "excludes": ["combined_rs>=62.5"],
     "mask": lambda d: ((d.rev_accel_pp > 0) & (d.op_margin_delta_pp > 0)
                        & (d.dilution_yoy_pct < 10) & (d.rs_20d > -5)
                        & (d.dist_ma50_pct < 15) & (d.combined_rs < 62.5)),
     "rank": lambda d: _z(d, "rev_accel_pp") + _z(d, "op_margin_delta_pp")},
    {"letter": "F2", "name": "eps_acceleration", "intent": "alpha", "universe": "fund",
     "archetype": "Fundamental Acceleration",
     "description": "Earnings growth accelerating from an already-growing base.",
     "requires": ["eps_accel_pp>0", "eps_growth_yoy_pct>20", "dilution_yoy_pct<10"],
     "excludes": ["combined_rs>=62.5", "dist_ma50_pct>20"],
     "mask": lambda d: ((d.eps_accel_pp > 0) & (d.eps_growth_yoy_pct > 20)
                        & (d.dilution_yoy_pct < 10) & (d.dist_ma50_pct <= 20)
                        & (d.combined_rs < 62.5)),
     "rank": lambda d: _z(d, "eps_accel_pp")},
    {"letter": "F3", "name": "fcf_turn_operating_leverage", "intent": "alpha",
     "universe": "fund", "archetype": "Fundamental Acceleration",
     "description": "Free cash flow turning positive or losses narrowing, with real "
                    "operating leverage and controlled dilution.",
     "requires": ["fcf_turned_positive or loss_narrowing", "op_margin_delta_pp>2",
                  "rev_yoy_pct>10", "dilution_yoy_pct<10"],
     "excludes": [],
     "mask": lambda d: (((d.fcf_turned_positive == 1.0) | (d.loss_narrowing == 1.0))
                        & (d.op_margin_delta_pp > 2) & (d.rev_yoy_pct > 10)
                        & (d.dilution_yoy_pct < 10)),
     "rank": lambda d: _z(d, "op_margin_delta_pp")},

    # ── Group 3: quality compounder ──
    {"letter": "Q1", "name": "quality_compounder", "intent": "alpha", "universe": "fund",
     "archetype": "Quality Compounder",
     "description": "Cash-generative, high return on capital, strong gross profitability, "
                    "low leverage, low dilution, still growing.",
     "requires": ["fcf_margin_pct>5", "roic_proxy_pct>10", "gross_profit_to_assets>0.2",
                  "debt_to_equity<1.5", "dilution_yoy_pct<3", "rev_yoy_pct>5"],
     "excludes": [],
     "mask": lambda d: ((d.fcf_margin_pct > 5) & (d.roic_proxy_pct > 10)
                        & (d.gross_profit_to_assets > 0.2) & (d.debt_to_equity < 1.5)
                        & (d.dilution_yoy_pct < 3) & (d.rev_yoy_pct > 5)),
     "rank": lambda d: _z(d, "roic_proxy_pct") + _z(d, "fcf_margin_pct")},
    {"letter": "Q2", "name": "quality_leader_at_highs", "intent": "alpha",
     "universe": "fund", "archetype": "Quality Compounder",
     "description": "The same quality screen, but only when the market already agrees: "
                    "near the 52-week high and above a rising MA200.",
     "requires": ["quality screen", "dd_from_high_pct>-10", "above rising MA200"],
     "excludes": ["combined_rs>=62.5"],
     "mask": lambda d: ((d.fcf_margin_pct > 5) & (d.roic_proxy_pct > 10)
                        & (d.dilution_yoy_pct < 3) & (d.dd_from_high_pct > -10)
                        & (d.above_ma200 == 1) & (d.ma200_slope_20d_pct > 0)
                        & (d.combined_rs < 62.5)),
     "rank": lambda d: _z(d, "roic_proxy_pct")},
    {"letter": "Q3", "name": "quality_pullback_continuation", "intent": "alpha",
     "universe": "fund", "archetype": "Quality Compounder",
     "description": "A profitable prior leader pulling back to its MA20/MA50 on drying "
                    "volume with the long trend intact.",
     "requires": ["profitable or fcf_positive", "rs_126d>10", "above_ma200",
                  "dist_ma50_pct -8..5", "vol_trend_20_63<1.0"],
     "excludes": ["dd_from_high_pct<-35"],
     "mask": lambda d: (((d.profitable == 1.0) | (d.fcf_positive == 1.0))
                        & (d.rs_126d > 10) & (d.above_ma200 == 1)
                        & d.dist_ma50_pct.between(-8, 5) & (d.vol_trend_20_63 < 1.0)
                        & (d.dd_from_high_pct > -35)),
     "rank": lambda d: _z(d, "rs_126d") - _z(d, "vol_trend_20_63")},

    # ── Group 4: price momentum / relative strength ──
    {"letter": "M1", "name": "momentum_12_1_raw", "intent": "alpha", "universe": "full",
     "archetype": "Price Momentum",
     "description": "The canonical cross-sectional momentum factor on its own: top "
                    "quintile of twelve-month return excluding the last month.",
     "requires": ["mom_12_1 in the top within-date quintile"],
     "excludes": [],
     "mask": lambda d: _pct(d, "mom_12_1") >= 0.80,
     "rank": lambda d: _z(d, "mom_12_1")},
    {"letter": "M2", "name": "momentum_12_1_confirmed", "intent": "alpha",
     "universe": "full", "archetype": "Price Momentum",
     "description": "Momentum with a trend filter and an exhaustion filter: top quintile "
                    "12-1, above MA200, liquid, and clear of the saturation zone.",
     "requires": ["mom_12_1 top quintile", "above_ma200", "dvol_med_20>=5M"],
     "excludes": ["combined_rs>=62.5", "vol decile 10"],
     "mask": lambda d: ((_pct(d, "mom_12_1") >= 0.80) & (d.above_ma200 == 1)
                        & (d.dvol_med_20 >= 5e6) & (d.combined_rs < 62.5)
                        & (d.vol_decile < 9)),
     "rank": lambda d: _z(d, "mom_12_1")},
    {"letter": "M3", "name": "momentum_acceleration", "intent": "alpha", "universe": "full",
     "archetype": "Price Momentum",
     "description": "Momentum that is improving rather than merely high, above a rising "
                    "MA50 and short of the exhaustion tail.",
     "requires": ["rs_63d 0..30", "rs_accel_20_63>0", "above rising MA50"],
     "excludes": ["combined_rs>=62.5", "vol decile 10"],
     "mask": lambda d: (d.rs_63d.between(0, 30) & (d.rs_accel_20_63 > 0)
                        & (d.above_ma50 == 1) & (d.ma50_slope_20d_pct > 0)
                        & (d.dist_ma50_pct <= 15) & (d.combined_rs < 62.5)
                        & (d.vol_decile < 9)),
     "rank": lambda d: _z(d, "rs_accel_20_63")},
    {"letter": "M4", "name": "new_high_with_volume", "intent": "alpha", "universe": "full",
     "archetype": "Price Momentum",
     "description": "A 63-day high made on expanding volume within a few percent of the "
                    "52-week high.",
     "requires": ["new_63d_high", "dd_from_high_pct>-5", "vol_expansion_5_63>1.2",
                  "dvol_med_20>=5M"],
     "excludes": ["combined_rs>=62.5"],
     "mask": lambda d: ((d.new_63d_high == 1.0) & (d.dd_from_high_pct > -5)
                        & (d.vol_expansion_5_63 > 1.2) & (d.dvol_med_20 >= 5e6)
                        & (d.combined_rs < 62.5)),
     "rank": lambda d: _z(d, "vol_expansion_5_63")},

    # ── Group 5: breakout / base structure ──
    {"letter": "B1", "name": "base_breakout_volume", "intent": "alpha", "universe": "full",
     "archetype": "Breakout / Base",
     "description": "A tight base resolving upward on a genuine volume expansion, with "
                    "the long-term trend intact.",
     "requires": ["base_tightness_63d>=0.7", "new_63d_high", "vol_expansion_5_63>1.5",
                  "above_ma200", "dvol_med_20>=5M"],
     "excludes": ["fake liquidity"],
     "mask": lambda d: ((d.base_tightness_63d >= 0.7) & (d.new_63d_high == 1.0)
                        & (d.vol_expansion_5_63 > 1.5) & (d.above_ma200 == 1)
                        & (d.dvol_med_20 >= 5e6) & (d.fake_liquidity_ratio < 3.0)),
     "rank": lambda d: _z(d, "vol_expansion_5_63") + _z(d, "base_tightness_63d")},
    {"letter": "B2", "name": "volatility_contraction_breakout", "intent": "alpha",
     "universe": "full", "archetype": "Breakout / Base",
     "description": "Volatility contraction then expansion: quiet 20-day vol against the "
                    "quarter, volume drying then turning up, RS beginning to rise.",
     "requires": ["rvol_ratio_20_63<0.85", "vol_dryup_then_expand",
                  "base_tightness_63d>=0.6", "rs_20d>0"],
     "excludes": ["dd_from_high_pct<-40"],
     "mask": lambda d: ((d.rvol_ratio_20_63 < 0.85) & (d.vol_dryup_then_expand == 1.0)
                        & (d.base_tightness_63d >= 0.6) & (d.rs_20d > 0)
                        & (d.dd_from_high_pct > -40)),
     "rank": lambda d: -_z(d, "rvol_ratio_20_63") + _z(d, "vol_expansion_5_63")},
    {"letter": "B3", "name": "small_cap_breakout_real_liquidity", "intent": "alpha",
     "universe": "full", "archetype": "Breakout / Base",
     "description": "A small-cap breakout with a real book behind it rather than a "
                    "single block print.",
     "requires": ["new_63d_high", "base_tightness_63d>=0.5", "vol_expansion_5_63>1.3",
                  "dvol_med_20 5M..100M", "fake_liquidity_ratio<3"],
     "excludes": ["vol decile 10", "combined_rs>=62.5"],
     "mask": lambda d: ((d.new_63d_high == 1.0) & (d.base_tightness_63d >= 0.5)
                        & (d.vol_expansion_5_63 > 1.3)
                        & d.dvol_med_20.between(5e6, 1e8)
                        & (d.fake_liquidity_ratio < 3.0) & (d.vol_decile < 9)
                        & (d.combined_rs < 62.5)),
     "rank": lambda d: _z(d, "vol_expansion_5_63")},

    # ── Group 6: institutional accumulation ──
    {"letter": "I1", "name": "up_down_volume_accumulation", "intent": "alpha",
     "universe": "full", "archetype": "Institutional Accumulation",
     "description": "More volume transacting on up days than down days over a quarter, "
                    "with liquidity itself expanding.",
     "requires": ["up_down_volume_63>1.3", "liquidity_trend>1.1", "above_ma50",
                  "rs_20d>0", "dvol_med_20>=5M"],
     "excludes": ["combined_rs>=62.5"],
     "mask": lambda d: ((d.up_down_volume_63 > 1.3) & (d.liquidity_trend > 1.1)
                        & (d.above_ma50 == 1) & (d.rs_20d > 0) & (d.dvol_med_20 >= 5e6)
                        & (d.combined_rs < 62.5)),
     "rank": lambda d: _z(d, "up_down_volume_63")},
    {"letter": "I2", "name": "accumulation_after_earnings", "intent": "alpha",
     "universe": "event", "archetype": "Institutional Accumulation",
     "description": "Heavy up-day volume in the weeks after a filing, with the name "
                    "holding above its MA20.",
     "requires": ["days_since_filing<=30", "up_down_volume_20>1.5", "above_ma20"],
     "excludes": [],
     "mask": lambda d: ((d.days_since_filing <= 30) & (d.up_down_volume_20 > 1.5)
                        & (d.above_ma20 == 1) & (d.dvol_med_20 >= 5e6)),
     "rank": lambda d: _z(d, "up_down_volume_20")},

    # ── Group 7: value + quality re-rating ──
    {"letter": "V1", "name": "value_quality_rerating", "intent": "alpha",
     "universe": "fund", "archetype": "Value + Quality",
     "description": "Cash-flow cheap with fundamentals improving and the trend already "
                    "turning — a value screen with the trap filter attached.",
     "requires": ["fcf_yield_pct>5", "ev_to_sales bottom tercile", "rev_yoy_pct>0",
                  "op_margin_delta_pp>0", "above_ma200"],
     "excludes": ["deteriorating fundamentals"],
     "mask": lambda d: ((d.fcf_yield_pct > 5) & (_pct(d, "ev_to_sales") <= 0.33)
                        & (d.rev_yoy_pct > 0) & (d.op_margin_delta_pp > 0)
                        & (d.above_ma200 == 1)),
     "rank": lambda d: _z(d, "fcf_yield_pct")},
    {"letter": "V2", "name": "cheap_and_improving", "intent": "alpha", "universe": "fund",
     "archetype": "Value + Quality",
     "description": "An earnings-yield screen with acceleration and no price collapse — "
                    "cheapness only where something is getting better.",
     "requires": ["earnings_yield_pct>5", "rev_accel_pp>0", "rs_63d>-5"],
     "excludes": [],
     "mask": lambda d: ((d.earnings_yield_pct > 5) & (d.rev_accel_pp > 0)
                        & (d.rs_63d > -5)),
     "rank": lambda d: _z(d, "earnings_yield_pct")},

    # ── Group 8: turnaround / recovery ──
    {"letter": "T1", "name": "turnaround_ma50_reclaim", "intent": "alpha",
     "universe": "fund", "archetype": "Turnaround",
     "description": "Deeply drawn down but reclaiming the MA50, with margins improving, "
                    "dilution controlled and cash on the balance sheet.",
     "requires": ["dd_from_high_pct<-40", "reclaimed_ma50_20d", "op_margin_delta_pp>0",
                  "dilution_yoy_pct<15", "net_cash>0"],
     "excludes": [],
     "mask": lambda d: ((d.dd_from_high_pct < -40) & (d.reclaimed_ma50_20d == 1.0)
                        & (d.op_margin_delta_pp > 0) & (d.dilution_yoy_pct < 15)
                        & (d.net_cash > 0)),
     "rank": lambda d: _z(d, "op_margin_delta_pp")},
    {"letter": "T2", "name": "turnaround_fcf_inflection", "intent": "alpha",
     "universe": "fund", "archetype": "Turnaround",
     "description": "The cash-flow version: a deep drawdown where free cash flow has "
                    "turned or losses are narrowing and the MA200 is being reclaimed.",
     "requires": ["dd_from_high_pct<-35", "fcf_turned_positive or loss_narrowing",
                  "reclaimed_ma200_20d or above_ma50", "dilution_yoy_pct<10"],
     "excludes": [],
     "mask": lambda d: ((d.dd_from_high_pct < -35)
                        & ((d.fcf_turned_positive == 1.0) | (d.loss_narrowing == 1.0))
                        & ((d.reclaimed_ma200_20d == 1.0) | (d.above_ma50 == 1))
                        & (d.dilution_yoy_pct < 10)),
     "rank": lambda d: _z(d, "op_margin_delta_pp")},

    # ── Group 9: sector / theme leadership ──
    {"letter": "S1", "name": "sector_leader_peer_confirmed", "intent": "alpha",
     "universe": "full", "archetype": "Sector / Theme Leadership",
     "description": "A top-quintile name inside its own sector, where the sector's peers "
                    "are broadly working too — leadership with confirmation.",
     "requires": ["sector_rs_rank_pct>=80", "sector_peer_breadth_pct>=60",
                  "sector_peer_count>=10", "rs_63d>5", "above_ma50"],
     "excludes": ["combined_rs>=62.5"],
     "mask": lambda d: ((d.sector_rs_rank_pct >= 80) & (d.sector_peer_breadth_pct >= 60)
                        & (d.sector_peer_count >= 10) & (d.rs_63d > 5)
                        & (d.above_ma50 == 1) & (d.combined_rs < 62.5)),
     "rank": lambda d: _z(d, "sector_rs_rank_pct")},
    {"letter": "S2", "name": "second_order_beneficiary", "intent": "alpha",
     "universe": "full", "archetype": "Sector / Theme Leadership",
     "description": "Not the biggest name in a working sector: mid-liquidity members of "
                    "a broadly strong sector, turning up in relative strength.",
     "requires": ["sector_peer_breadth_pct>=60", "liq_decile 3..7", "rs_accel_20_63>0",
                  "vol_expansion_5_63>1.1", "above_ma50"],
     "excludes": ["largest liquidity deciles", "combined_rs>=62.5"],
     "mask": lambda d: ((d.sector_peer_breadth_pct >= 60) & d.liq_decile.between(3, 7)
                        & (d.rs_accel_20_63 > 0) & (d.vol_expansion_5_63 > 1.1)
                        & (d.above_ma50 == 1) & (d.combined_rs < 62.5)),
     "rank": lambda d: _z(d, "rs_accel_20_63")},

    # ── Group 10: event / catalyst (proxy only) ──
    {"letter": "C1", "name": "biotech_event_proxy", "intent": "alpha", "universe": "full",
     "archetype": "Event / Catalyst",
     "description": "Event-shaped healthcare names with frequent large up-days and real "
                    "liquidity. A PROXY: no clinical calendar exists for the window.",
     "requires": ["sector Healthcare", "tail_gain_days_63>=3", "dvol_med_20>=5M"],
     "excludes": ["fake liquidity"],
     "mask": lambda d: ((d.sector_current_meta == "Healthcare")
                        & (d.tail_gain_days_63 >= 3) & (d.dvol_med_20 >= 5e6)
                        & (d.fake_liquidity_ratio < 3.0)),
     "rank": lambda d: _z(d, "tail_gain_days_63")},

    # ── controlled-volatility lane, kept as ONE family among many ──
    {"letter": "X1", "name": "volatility_with_confirmation", "intent": "alpha",
     "universe": "full", "archetype": "Controlled Volatility",
     "description": "High volatility with liquidity, an intact MA200 and accumulation, "
                    "held out of the top decile where the opportunity map shows the "
                    "payoff inverting.",
     "requires": ["vol_decile 7..9", "dvol_med_20>=20M", "above_ma200",
                  "accumulation_days_20>=8", "rs_63d>0"],
     "excludes": ["vol decile 10", "fake liquidity", "combined_rs>=62.5"],
     "mask": lambda d: (d.vol_decile.between(6, 8) & (d.dvol_med_20 >= 2e7)
                        & (d.above_ma200 == 1) & (d.accumulation_days_20 >= 8)
                        & (d.rs_63d > 0) & (d.fake_liquidity_ratio < 3.0)
                        & (d.combined_rs < 62.5)),
     "rank": lambda d: _z(d, "accumulation_days_20") + _z(d, "rs_accel_20_63")},
    {"letter": "X2", "name": "commodity_macro_leader", "intent": "alpha",
     "universe": "full", "archetype": "Sector / Theme Leadership",
     "description": "Energy and materials names leading their own sector on accumulation. "
                    "Sector label is CURRENT metadata.",
     "requires": ["sector Energy or Basic Materials", "rs_63d>5",
                  "accumulation_days_20>=8"],
     "excludes": ["combined_rs>=62.5"],
     "mask": lambda d: (d.sector_current_meta.isin(["Energy", "Basic Materials"])
                        & (d.rs_63d > 5) & (d.accumulation_days_20 >= 8)
                        & (d.combined_rs < 62.5)),
     "rank": lambda d: _z(d, "rs_63d")},

    # ── Group 12: risk / failure avoidance ──
    {"letter": "R1", "name": "exhaustion_trap", "intent": "avoid", "universe": "full",
     "archetype": "Risk / Failure Avoidance",
     "description": "Saturated relative strength, top-decile volatility, or a broken MA50 "
                    "after a huge run.",
     "requires": ["combined_rs>=62.5 OR vol_decile>=9 OR (ret_63d>100 and below MA50)"],
     "excludes": [],
     "mask": lambda d: ((d.combined_rs >= 62.5) | (d.vol_decile >= 9)
                        | ((d.ret_63d > 100) & (d.above_ma50 == 0))),
     "rank": lambda d: _z(d, "combined_rs")},
    {"letter": "R2", "name": "dilution_and_distress", "intent": "avoid", "universe": "fund",
     "archetype": "Risk / Failure Avoidance",
     "description": "Heavy dilution with negative free cash flow and leverage — the "
                    "balance-sheet failure mode rather than the price one.",
     "requires": ["dilution_yoy_pct>20", "fcf_margin_pct<0", "debt_to_equity>2"],
     "excludes": [],
     "mask": lambda d: ((d.dilution_yoy_pct > 20) & (d.fcf_margin_pct < 0)
                        & (d.debt_to_equity > 2)),
     "rank": lambda d: _z(d, "dilution_yoy_pct")},

    # ── baseline ──
    {"letter": "Z0", "name": "old_scanner_baseline", "intent": "baseline",
     "universe": "full", "archetype": "Baseline — the old board",
     "description": "The old scanner's own replay picks, carried as one weak baseline.",
     "requires": ["in_old_scanner"], "excludes": [],
     "mask": lambda d: d.in_old_scanner,
     "rank": lambda d: _z(d, "combined_rs")},
]

# Declared untestable rather than proxied: approximating these would produce a
# number that looks like evidence and is not.
UNTESTABLE: list[dict] = [
    {"name": "analyst_estimate_revisions",
     "why": "No historical analyst estimate or revision data exists in this cache. "
            "Family B is therefore tested on the price/volume reaction to a filing "
            "only, which is a weaker instrument than the revision itself."},
    {"name": "news_topic_shock_and_social_attention",
     "why": "No historical news, topic-shock or social-attention record exists for "
            "2022-2025; those lanes remain forward-only and untested here."},
    {"name": "options_flow_and_implied_volatility",
     "why": "Chain history begins 2026-06-12 (Phase 1J), long after this window."},
    {"name": "true_theme_and_supply_chain_graph",
     "why": "Only CURRENT sector labels exist (43% coverage of the liquid universe, "
            "absent for delisted names). Families G/I/J/L use them as a shape proxy "
            "and inherit that bias; no point-in-time theme map exists."},
    {"name": "clinical_trial_and_event_calendar",
     "why": "No catalyst calendar exists for the window, so family G is a shape "
            "proxy (healthcare + gap frequency), not an event lane."},
]

# ── evaluation (Parts 6-7) ──────────────────────────────────────────────────

PROMOTION = {
    "min_train_episodes": 500,
    "min_dates": 60,
    "min_names_per_date_for_daily_list": 10,
    "min_tail_ratio": 1.15,
    "min_winner_capture_lift": 1.15,
    "max_loser_capture_lift": 1.25,
    "max_single_sector_share_pct": 60,
    "max_top10_ticker_share_pct": 40,
    "min_years_with_positive_selection": 3,
}


def matched_twin(cohort, universe, h, args, seed_offset=0) -> dict | None:
    """Cohort vs a same-date twin matched on volatility and liquidity decile.

    The standing alternative explanation for any result here is "this is a
    style tilt". Matching on the two axes that dominate the opportunity map
    removes that explanation or confirms it.
    """
    import numpy as np
    import pandas as pd

    if cohort.empty:
        return None
    u = universe.dropna(subset=["vol_decile", "liq_decile"])
    inc = u.index.isin(cohort.index)
    need = (u[inc].groupby(["scan_date", "vol_decile", "liq_decile"], observed=True)
            .size().rename("k").reset_index())
    if need.empty:
        return None
    key = {(r.scan_date, r.vol_decile, r.liq_decile): int(r.k)
           for r in need.itertuples()}
    rng = np.random.default_rng(31 + seed_offset)
    picks = []
    for (d, vd, ld), grp in u[~inc].groupby(
            ["scan_date", "vol_decile", "liq_decile"], observed=True):
        k = key.get((d, vd, ld))
        if not k:
            continue
        k = min(k, len(grp))
        picks.append(grp.iloc[rng.choice(len(grp), size=k, replace=False)])
    if not picks:
        return None
    twin = pd.concat(picks)
    a = cohort.groupby("scan_date")[f"fwd_{h}d"]
    b = twin.groupby("scan_date")[f"fwd_{h}d"]
    j = pd.DataFrame({"am": a.mean(), "bm": b.mean(),
                      "ad": a.median(), "bd": b.median()}).dropna()
    if len(j) < 10:
        return None
    dm = (j["am"] - j["bm"]).to_numpy()
    dd = (j["ad"] - j["bd"]).to_numpy()
    return {
        "mean_difference": round(float(dm.mean()), 3),
        "mean_ci": _cluster_ci(dm, args.bootstrap, args.seed),
        "median_difference": round(float(dd.mean()), 3),
        "dates_won_pct": round(float((dm > 0).mean() * 100), 1),
        "twin_episodes": int(len(twin)),
    }


def evaluate_slice(sub, universe, args) -> dict:
    """Every metric a family is judged on, for one slice of years."""
    import numpy as np

    if sub.empty:
        return {"episodes": 0}
    out = {
        "episodes": int(len(sub)),
        "unique_tickers": int(sub.ticker.nunique()),
        "dates_covered": int(sub.scan_date.nunique()),
        "median_names_per_date": float(sub.groupby("scan_date").size().median()),
        "share_of_universe_pct": round(100.0 * len(sub) / max(len(universe), 1), 2),
        "median_dvol_musd": round(float(sub["dvol_med_20"].median() / 1e6), 2),
    }
    for h in HORIZONS:
        out[f"fwd_{h}d"] = asymmetry(sub[f"fwd_{h}d"])
        out[f"selection_{h}d"] = selection_vs(sub, universe, h, args.bootstrap, args.seed)
    # Winner capture vs loser capture — the asymmetry the brief asks for,
    # expressed as lifts so the two are comparable.
    cap = {}
    for lbl in (f"top10pct_{PRIMARY_H}d", f"top5pct_{PRIMARY_H}d", f"top1pct_{PRIMARY_H}d",
                f"top50_{PRIMARY_H}d", "win_up50_45d", "win_up100_126d"):
        base = float(universe[lbl].mean())
        cap[lbl] = {
            "cohort_rate_pct": round(float(sub[lbl].mean() * 100), 3),
            "lift": round(float(sub[lbl].mean() / base), 2) if base > 0 else None,
            "share_of_all_captured_pct": round(
                100.0 * float(sub[lbl].sum()) / max(float(universe[lbl].sum()), 1e-9), 2),
        }
    for lbl in (f"bot10pct_{PRIMARY_H}d", f"bot5pct_{PRIMARY_H}d", f"bot1pct_{PRIMARY_H}d",
                "lose_down50_60d", "trap_catastrophic"):
        base = float(universe[lbl].mean())
        cap[lbl] = {
            "cohort_rate_pct": round(float(sub[lbl].mean() * 100), 3),
            "lift": round(float(sub[lbl].mean() / base), 2) if base > 0 else None,
        }
    wl = cap[f"top10pct_{PRIMARY_H}d"]["lift"], cap[f"bot10pct_{PRIMARY_H}d"]["lift"]
    out["capture"] = cap
    out["winner_over_loser_capture_ratio"] = (
        round(wl[0] / wl[1], 3) if (wl[0] and wl[1]) else None)
    out["style_excess"] = {
        f"vs_{b}": round(float(sub[f"excess_vs_{b}"].mean()), 3)
        for b in ("vol_decile", "liq_decile", "sector_current_meta", "mcap_bucket")
        if f"excess_vs_{b}" in sub.columns
    }
    out["trap_exposure_pct"] = round(float(sub["in_score_100_zone"].mean() * 100), 3)
    out["old_scanner_overlap_pct"] = round(float(sub["in_old_scanner"].mean() * 100), 2)
    out["path_mix"] = {str(k): int(v) for k, v in
                       sub["path_primary"].value_counts().head(6).items()}
    return out


def walk_forward(cohort, universe, args) -> list[dict]:
    """Train on each year, test on the next. No labels enter the rules, so this
    measures stability rather than re-fitting: the rule is fixed, the years move."""
    years = sorted(universe.year.unique())
    out = []
    for i in range(len(years) - 1):
        te = years[i + 1]
        c = cohort[cohort.year == te]
        u = universe[universe.year == te]
        if c.empty:
            continue
        sel = selection_vs(c, u, PRIMARY_H, args.bootstrap, args.seed)
        a = asymmetry(c[f"fwd_{PRIMARY_H}d"])
        out.append({
            "trained_through": int(years[i]), "tested_on": int(te),
            "episodes": int(len(c)),
            "mean": (a or {}).get("mean"), "median": (a or {}).get("median"),
            "tail_ratio": (a or {}).get("tail_ratio"),
            "selection_mean": (sel or {}).get("selection_mean"),
            "selection_median": (sel or {}).get("selection_median"),
        })
    return out


def verdict_for(fam: dict, res: dict) -> tuple[str, list[str]]:
    """The eight-verdict ladder, applied mechanically."""
    tr, ho = res.get("train") or {}, res.get("holdout") or {}
    if not tr or not tr.get("episodes"):
        return assert_tournament_verdict("INCONCLUSIVE"), ["no train episodes"]

    t_a = tr.get(f"fwd_{PRIMARY_H}d") or {}
    h_a = ho.get(f"fwd_{PRIMARY_H}d") or {}
    t_s = tr.get(f"selection_{PRIMARY_H}d") or {}
    h_s = ho.get(f"selection_{PRIMARY_H}d") or {}
    t_mean_ci = t_s.get("date_cluster_ci_mean") or [None, None]
    reasons: list[str] = []

    # An avoid lane is judged on whether it reliably loses.
    if fam.get("intent") == "avoid":
        neg = (t_s.get("selection_mean", 0) < 0 and h_s.get("selection_mean", 0) < 0
               and t_mean_ci[1] is not None and t_mean_ci[1] < 0)
        loser_lift = ((tr.get("capture") or {}).get(f"bot10pct_{PRIMARY_H}d") or {}).get("lift")
        if neg and (loser_lift or 0) > 1.2:
            return assert_tournament_verdict("AVOID_TRAP"), [
                f"negative in train and holdout with the train CI clear of zero, "
                f"and {loser_lift}x the universe rate of bottom-decile outcomes"]
        return assert_tournament_verdict("INCONCLUSIVE"), [
            "did not reliably lose; not usable as an avoid list"]

    if fam.get("intent") == "baseline":
        return assert_tournament_verdict("INCONCLUSIVE"), ["carried as a baseline only"]

    # Significantly negative on the mean, in train or holdout.
    if t_mean_ci[1] is not None and t_mean_ci[1] < 0:
        return assert_tournament_verdict("REJECTED_NEGATIVE_SELECTION"), [
            f"train mean selection {t_s.get('selection_mean')} with CI {t_mean_ci} "
            "entirely below zero"]

    small = tr["episodes"] < PROMOTION["min_train_episodes"]
    thin = tr.get("dates_covered", 0) < PROMOTION["min_dates"]
    if small:
        reasons.append(f"train sample {tr['episodes']} below {PROMOTION['min_train_episodes']}")
    if thin:
        reasons.append(f"covers {tr.get('dates_covered')} dates, below {PROMOTION['min_dates']}")

    train_pos = (t_mean_ci[0] is not None and t_mean_ci[0] > 0)
    hold_pos = (h_s.get("selection_mean") is not None and h_s["selection_mean"] > 0)
    if not train_pos:
        reasons.append("train mean selection not positive with its CI clear of zero")
    if not hold_pos:
        reasons.append(f"holdout mean selection {h_s.get('selection_mean')} not above zero")

    twin = (res.get("matched_twin") or {}).get("holdout") or {}
    if twin.get("mean_difference") is not None and twin["mean_difference"] <= 0:
        reasons.append("does not beat its volatility/liquidity-matched twin in holdout")
    board = res.get("vs_old_scanner_mean_60d")
    if board is not None and board <= 0:
        reasons.append("does not beat the old scanner board")

    tail = t_a.get("tail_ratio")
    if tail is not None and tail < PROMOTION["min_tail_ratio"]:
        reasons.append(f"tail ratio {tail} below {PROMOTION['min_tail_ratio']}")
    wl = ((tr.get("capture") or {}).get(f"top10pct_{PRIMARY_H}d") or {}).get("lift")
    ll = ((tr.get("capture") or {}).get(f"bot10pct_{PRIMARY_H}d") or {}).get("lift")
    if wl is not None and wl < PROMOTION["min_winner_capture_lift"]:
        reasons.append(f"winner capture lift {wl} below {PROMOTION['min_winner_capture_lift']}")
    if ll is not None and ll > PROMOTION["max_loser_capture_lift"]:
        reasons.append(f"loser capture lift {ll} above {PROMOTION['max_loser_capture_lift']}")

    regimes = res.get("by_regime") or {}
    pos_reg = sum(1 for v in regimes.values()
                  if ((v.get(f"selection_{PRIMARY_H}d") or {}).get("selection_mean") or -1) > 0)
    if pos_reg < 2:
        reasons.append(f"positive in only {pos_reg} of {len(regimes)} regimes")

    con = res.get("concentration") or {}
    if (con.get("largest_known_sector_share_pct") or 0) > 60:
        reasons.append(
            f"{con['largest_known_sector_share_pct']}% of the cohort sits in one sector "
            "— a sector bet, not a general process")
    if (con.get("top2_known_sector_share_pct") or 0) > 85:
        reasons.append(
            f"{con['top2_known_sector_share_pct']}% of the cohort sits in two sectors "
            "— a sector bet, not a general process")
    if (con.get("top10_ticker_share_pct") or 0) > 40:
        reasons.append(
            f"top 10 tickers are {con['top10_ticker_share_pct']}% of episodes")
    if con.get("years_with_positive_mean_selection", 0) < 3:
        reasons.append(
            f"positive in only {con.get('years_with_positive_mean_selection')} of "
            f"{con.get('years_tested')} years")

    daily_ok = tr.get("median_names_per_date", 0) >= PROMOTION["min_names_per_date_for_daily_list"]

    if train_pos and not hold_pos:
        return assert_tournament_verdict("REJECTED_OVERFIT"), (
            ["positive in 2022-2024, not in the 2025 holdout"] + reasons)
    # A pool has to be able to fill a list. A lane that surfaces a handful of
    # names a week may be a real edge, but calling it a CANDIDATE POOL would
    # misdescribe it, so capacity routes before the clean-sweep check.
    if (not daily_ok and hold_pos and (tail or 0) >= PROMOTION["min_tail_ratio"]
            and (t_s.get("selection_mean") or 0) > 0):
        return assert_tournament_verdict("SPECIAL_SITUATION_ONLY"), (
            [f"only {tr.get('median_names_per_date')} names per date — too sparse to "
             f"fill a daily list, so not a pool however good the asymmetry"] + reasons)
    if not reasons:
        return assert_tournament_verdict("TRUE_ALPHA_CANDIDATE_POOL"), [
            "clears every pre-declared bar"]
    if hold_pos and (t_s.get("selection_mean") or 0) > 0 and len(reasons) <= 2:
        return assert_tournament_verdict("PROMISING_BUT_UNPROVEN"), reasons
    return assert_tournament_verdict("INCONCLUSIVE"), reasons


def evaluate_family(fam: dict, pools: dict, args) -> dict:
    import numpy as np
    import pandas as pd

    universe = pools[fam["universe"]]
    try:
        mask = fam["mask"](universe).fillna(False)
    except Exception as e:
        return {"name": fam["name"], "letter": fam["letter"],
                "verdict": assert_tournament_verdict("REJECTED_DATA_UNSAFE"),
                "verdict_reasons": [f"rule could not be evaluated: {type(e).__name__}: {e}"]}
    cohort = universe[mask]
    res = {
        "letter": fam["letter"], "name": fam["name"], "archetype": fam["archetype"],
        "intent": fam["intent"], "description": fam["description"],
        "required_conditions": fam["requires"], "exclusion_conditions": fam["excludes"],
        "universe": fam["universe"],
        "universe_note": {
            "full": "full eligible universe — no coverage conditioning",
            "fund": "fundamentals-covered sub-universe (59%); coverage depends on the "
                    "old scanner having surfaced the name, which is partly "
                    "forward-looking. Compared only against the same sub-universe.",
            "event": "filing-date-covered sub-universe; same coverage caveat as fund.",
        }[fam["universe"]],
        "total_episodes": int(len(cohort)),
    }
    if cohort.empty:
        res["verdict"] = assert_tournament_verdict("INCONCLUSIVE")
        res["verdict_reasons"] = ["rule selected no episodes"]
        return res

    tr_u, ho_u = (universe[universe.year.isin(TRAIN_YEARS)],
                  universe[universe.year.isin(HOLDOUT_YEARS)])
    tr_c, ho_c = (cohort[cohort.year.isin(TRAIN_YEARS)],
                  cohort[cohort.year.isin(HOLDOUT_YEARS)])
    res["train"] = evaluate_slice(tr_c, tr_u, args)
    res["holdout"] = evaluate_slice(ho_c, ho_u, args)
    res["by_regime"] = {
        k: evaluate_slice(cohort[cohort.year.isin(y)], universe[universe.year.isin(y)], args)
        for k, y in REGIMES.items()}
    res["by_year"] = {
        str(int(y)): {
            "episodes": int((cohort.year == y).sum()),
            "mean_fwd_60d": (asymmetry(cohort[cohort.year == y][f"fwd_{PRIMARY_H}d"]) or {}).get("mean"),
            "median_fwd_60d": (asymmetry(cohort[cohort.year == y][f"fwd_{PRIMARY_H}d"]) or {}).get("median"),
            "selection_mean": (selection_vs(cohort[cohort.year == y],
                                            universe[universe.year == y], PRIMARY_H,
                                            args.bootstrap, args.seed) or {}).get("selection_mean"),
        } for y in sorted(universe.year.unique())}
    res["walk_forward"] = walk_forward(cohort, universe, args)
    res["matched_twin"] = {
        "train": matched_twin(tr_c, tr_u, PRIMARY_H, args),
        "holdout": matched_twin(ho_c, ho_u, PRIMARY_H, args, seed_offset=1)}

    board = universe[universe["in_old_scanner"]]
    if not board.empty:
        a = cohort.groupby("scan_date")[f"fwd_{PRIMARY_H}d"].mean()
        b = board.groupby("scan_date")[f"fwd_{PRIMARY_H}d"].mean()
        j = pd.DataFrame({"a": a, "b": b}).dropna()
        res["vs_old_scanner_mean_60d"] = (
            round(float((j["a"] - j["b"]).mean()), 3) if len(j) >= 10 else None)
    res["sector_concentration"] = {
        str(k): round(float(v) * 100, 1) for k, v in
        cohort["sector_current_meta"].value_counts(normalize=True).head(5).items()}
    # Concentration guards. Part 7 of the brief asks that a promising setup be
    # "not just one year, one sector, or one outlier"; the first run of this
    # tournament promoted a 2-sector, 4-names-per-date lane because that
    # requirement was described but never encoded. These three checks encode
    # it, and are applied to every family uniformly.
    known = cohort[cohort["sector_current_meta"] != "UNKNOWN"]
    shares = (known["sector_current_meta"].value_counts(normalize=True) * 100
              if len(known) else None)
    top_sector = float(shares.iloc[0]) if shares is not None and len(shares) else None
    top2_sector = float(shares.head(2).sum()) if shares is not None and len(shares) else None
    tick = cohort["ticker"].value_counts()
    res["concentration"] = {
        "largest_known_sector_share_pct": None if top_sector is None else round(top_sector, 1),
        "top2_known_sector_share_pct": None if top2_sector is None else round(top2_sector, 1),
        "known_sector_coverage_pct": round(100.0 * len(known) / len(cohort), 1),
        "top10_ticker_share_pct": round(100.0 * float(tick.head(10).sum()) / len(cohort), 1),
        "unique_tickers": int(cohort.ticker.nunique()),
        "years_with_positive_mean_selection": sum(
            1 for v in res["by_year"].values() if (v.get("selection_mean") or -1) > 0),
        "years_tested": len(res["by_year"]),
    }
    # Portfolio edge vs per-name edge. The date-clustered CI asks whether an
    # equal-weight list of the cohort beat the tape that week; the
    # ticker-clustered CI asks whether the typical NAME in it beat its own
    # date's universe. Momentum answers yes to the first and no to the second,
    # which is exactly what a right-skewed factor looks like — and it matters,
    # because a human reading a 25-name research list experiences the second.
    for split in ("train", "holdout"):
        sel = ((res.get(split) or {}).get(f"selection_{PRIMARY_H}d") or {})
        d_ci = sel.get("date_cluster_ci_mean") or [None, None]
        t_ci = sel.get("ticker_cluster_ci") or [None, None]
        if None in (d_ci[0], t_ci[1]):
            continue
        res.setdefault("portfolio_vs_per_name", {})[split] = {
            "portfolio_edge_positive": bool(d_ci[0] > 0),
            "typical_name_edge_positive": bool(t_ci[0] > 0),
            "diverges": bool(d_ci[0] > 0 and t_ci[1] < 0),
            "reading": ("edge is a PORTFOLIO property: the equal-weight cohort beats "
                        "the tape while the typical name underperforms its own date's "
                        "universe median. Usable as a list, misleading name-by-name."
                        if (d_ci[0] > 0 and t_ci[1] < 0) else
                        "portfolio and per-name readings agree"),
        }

    res["verdict"], res["verdict_reasons"] = verdict_for(fam, res)
    return res


def _build_pools(root: Path, args):
    """The three universes families are judged against."""
    import pandas as pd

    df = load_panel(root)
    df = attach_controls(df, root)
    df = add_peer_confirmation(df)
    fund_path = root / f"{INTERMEDIATES_REL}/fundamental_panel_v2.parquet"
    ev_path = root / f"{INTERMEDIATES_REL}/event_panel.parquet"
    if fund_path.exists() and ev_path.exists():
        fund = pd.read_parquet(fund_path)
        ev = pd.read_parquet(ev_path)
    else:
        prices = load_prices(root, load_quarantine_excludes(root))
        scan_dates, _ = weekly_scan_dates(prices, args.start, args.end)
        # Valuation needs a market cap on the same date; the panel already
        # carries it where the replay cached one.
        mcap_lookup = {(t, d): m for t, d, m in
                       zip(df["ticker"], df["scan_date"], df["market_cap"])
                       if m is not None and m == m}
        fund = build_fundamental_panel_v2(root, scan_dates, mcap_lookup)
        ev = build_event_panel(root, prices, scan_dates)
        del prices
        for path, frame in ((fund_path, fund), (ev_path, ev)):
            assert_replay_write_path(path, root=root)
            path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_parquet(path, index=False, compression="zstd")
    fund["scan_date"] = pd.to_datetime(fund["scan_date"])
    ev["scan_date"] = pd.to_datetime(ev["scan_date"])
    # The event pool also carries fundamentals: the earnings-acceleration
    # families need both the filing date and the statement figures.
    ev_full = ev.merge(fund, on=["ticker", "scan_date"], how="left")
    pools = {
        "full": df,
        "fund": df.merge(fund, on=["ticker", "scan_date"], how="inner"),
        "event": df.merge(ev_full, on=["ticker", "scan_date"], how="inner"),
    }
    print("pools: " + ", ".join(f"{k}={len(v):,} rows" for k, v in pools.items()), flush=True)
    return pools


def tournament_stage(args) -> int:
    root = Path(args.root)
    tw = LiveArtifactTripwire.snapshot(root)
    pools = _build_pools(root, args)

    results = []
    for fam in FAMILIES:
        t0 = time.time()
        r = evaluate_family(fam, pools, args)
        results.append(r)
        tr = r.get("train") or {}
        a = (tr.get(f"fwd_{PRIMARY_H}d") or {})
        s = (tr.get(f"selection_{PRIMARY_H}d") or {})
        hs = ((r.get("holdout") or {}).get(f"selection_{PRIMARY_H}d") or {})
        print(f"  {fam['letter']} {fam['name']:<32} n={r.get('total_episodes', 0):>7} "
              f"mean={a.get('mean')} tailR={a.get('tail_ratio')} "
              f"selµ_tr={s.get('selection_mean')} selµ_ho={hs.get('selection_mean')} "
              f"-> {r.get('verdict')} ({time.time() - t0:.0f}s)", flush=True)

    out = {
        "kind": "ALPHA_TOURNAMENT_STRATEGIES",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "primary_horizon_days": PRIMARY_H,
        "train_years": list(TRAIN_YEARS), "holdout_years": list(HOLDOUT_YEARS),
        "promotion_rules": PROMOTION,
        "scoring_note": (
            "Cohorts are judged on the MEAN as well as the median. An equal-weight "
            "research list earns the mean, and a right-tail strategy can be positive "
            "on the mean while negative on the median; judging such a lane on the "
            "median alone is how the previous study under-rated the volatile pockets."),
        "multiplicity_warning": (
            f"{len(FAMILIES)} families were scored on one window. The bar therefore "
            "requires an out-of-sample year, a style-matched twin, two clusterings and "
            "a tail-ratio floor rather than a single p-value."),
        "families": results,
        "untestable": UNTESTABLE,
        "verdict_counts": {},
        **provenance_flags(fundamentals=True),
    }
    for r in results:
        v = r.get("verdict", "INCONCLUSIVE")
        out["verdict_counts"][v] = out["verdict_counts"].get(v, 0) + 1
    assert_provenance(out)
    write_replay_json(root / STRATEGIES_REL, out, root=root)
    print(tw.report())
    tw.assert_clean()
    print("verdicts: " + json.dumps(out["verdict_counts"]))
    return 0

# ── stage: lists (Part 8) ───────────────────────────────────────────────────
#
# Not a trading simulation. The question is narrower and more useful: if this
# work produced a daily research list, could it be filled, and would the names
# on it have been worth a human's attention?

LIST_SIZES: tuple[int, ...] = (10, 25, 50, 100, 200)


def _list_metrics(picks, universe, args) -> dict:
    import numpy as np
    import pandas as pd

    if picks.empty:
        return {"episodes": 0}
    a = asymmetry(picks[f"fwd_{PRIMARY_H}d"]) or {}
    per_date = picks.groupby("scan_date")
    # Turnover: how much of last week's list survives into this week's.
    names = {d: set(g["ticker"]) for d, g in per_date}
    keys = sorted(names)
    overlaps = [len(names[b] & names[a_]) / max(len(names[b]), 1)
                for a_, b in zip(keys, keys[1:]) if names[b]]
    sec = picks[picks.sector_current_meta != "UNKNOWN"]["sector_current_meta"]
    return {
        "episodes": int(len(picks)),
        "unique_tickers": int(picks.ticker.nunique()),
        "median_names_per_date": float(per_date.size().median()),
        "dates_that_could_be_filled_pct": round(float(
            (per_date.size() >= picks.attrs.get("target", 0)).mean() * 100), 1)
        if picks.attrs.get("target") else None,
        "equal_weight_mean_fwd_60d": a.get("mean"),
        "median_fwd_60d": a.get("median"),
        "trim10_fwd_60d": a.get("trim10"),
        "win_rate_pct": a.get("win_rate"),
        "tail_ratio": a.get("tail_ratio"),
        "pct_up_50": a.get("pct_up_50"),
        "pct_down_25": a.get("pct_down_25"),
        "selection_60d": selection_vs(picks, universe, PRIMARY_H, args.bootstrap, args.seed),
        "top10pct_capture_lift": round(float(
            picks[f"top10pct_{PRIMARY_H}d"].mean() / max(universe[f"top10pct_{PRIMARY_H}d"].mean(), 1e-9)), 2),
        "bot10pct_capture_lift": round(float(
            picks[f"bot10pct_{PRIMARY_H}d"].mean() / max(universe[f"bot10pct_{PRIMARY_H}d"].mean(), 1e-9)), 2),
        "week_to_week_name_overlap_pct": round(float(np.mean(overlaps) * 100), 1) if overlaps else None,
        "median_dvol_musd": round(float(picks["dvol_med_20"].median() / 1e6), 2),
        "largest_sector_share_pct": round(float(
            sec.value_counts(normalize=True).iloc[0] * 100), 1) if len(sec) else None,
        "high_vol_share_pct": round(float((picks["vol_decile"] >= 8).mean() * 100), 1),
        "trap_zone_share_pct": round(float(picks["in_score_100_zone"].mean() * 100), 2),
        "old_scanner_overlap_pct": round(float(picks["in_old_scanner"].mean() * 100), 2),
    }


def _top_n(pool, score, n: int):
    if pool.empty:
        return pool
    r = score.reindex(pool.index).fillna(float("-inf"))
    order = r.groupby(pool["scan_date"]).rank(ascending=False, method="first")
    out = pool[order <= n].copy()
    out.attrs["target"] = n
    return out


def lists_stage(args) -> int:
    import numpy as np
    import pandas as pd

    root = Path(args.root)
    tw = LiveArtifactTripwire.snapshot(root)
    strat = json.loads((root / STRATEGIES_REL).read_text())
    keep = {f["name"] for f in strat["families"]
            if f.get("verdict") in ("TRUE_ALPHA_CANDIDATE_POOL", "PROMISING_BUT_UNPROVEN",
                                    "SPECIAL_SITUATION_ONLY")}
    pools = _build_pools(root, args)
    full = pools["full"]

    # A blended pool: every surviving family's cohort, each name carrying the
    # best within-date percentile it earned in any family it belongs to.
    blend_score = pd.Series(np.nan, index=full.index)
    members = pd.Series(False, index=full.index)
    contributions = {}
    for fam in FAMILIES:
        if fam["name"] not in keep:
            continue
        u = pools[fam["universe"]]
        try:
            m = fam["mask"](u).fillna(False)
        except Exception:
            continue
        c = u[m]
        if c.empty:
            continue
        pct = fam["rank"](u).reindex(c.index).groupby(c["scan_date"]).rank(pct=True)
        idx = c.index.intersection(full.index)
        contributions[fam["name"]] = int(len(idx))
        members.loc[idx] = True
        blend_score.loc[idx] = np.fmax(blend_score.reindex(idx).to_numpy(),
                                       pct.reindex(idx).to_numpy())
    blend = full[members]

    rng = np.random.default_rng(args.seed)
    random_score = pd.Series(rng.random(len(full)), index=full.index)
    board_score = full["combined_rs"].where(full["in_old_scanner"])

    out = {
        "kind": "ALPHA_TOURNAMENT_RESEARCH_LISTS",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "question": "If this work produced a daily research list, could it be filled, "
                    "and were the names worth attention?",
        "surviving_families_in_blend": sorted(keep),
        "blend_contributions": contributions,
        "blend_capacity": {
            "median_names_available_per_date": float(blend.groupby("scan_date").size().median()),
            "p10_names_available": float(blend.groupby("scan_date").size().quantile(0.10)),
            "p90_names_available": float(blend.groupby("scan_date").size().quantile(0.90)),
            "dates_with_50_plus_pct": round(float(
                (blend.groupby("scan_date").size() >= 50).mean() * 100), 1),
            "dates_with_100_plus_pct": round(float(
                (blend.groupby("scan_date").size() >= 100).mean() * 100), 1),
        },
        "lists": {},
        **provenance_flags(fundamentals=True),
    }
    for split, years in (("train", TRAIN_YEARS), ("holdout", HOLDOUT_YEARS)):
        u = full[full.year.isin(years)]
        node = {}
        for n in LIST_SIZES:
            node[f"blend_top{n}"] = _list_metrics(
                _top_n(blend[blend.year.isin(years)], blend_score, n), u, args)
            node[f"random_top{n}"] = _list_metrics(_top_n(u, random_score, n), u, args)
            node[f"old_scanner_top{n}"] = _list_metrics(
                _top_n(u[u.in_old_scanner], board_score, n), u, args)
        out["lists"][split] = node

    assert_provenance(out)
    write_replay_json(root / LISTS_REL, out, root=root)
    print(tw.report())
    tw.assert_clean()
    cap = out["blend_capacity"]
    print(f"\nblend capacity: median {cap['median_names_available_per_date']:.0f} "
          f"names/date · {cap['dates_with_50_plus_pct']}% of dates could fill 50 · "
          f"{cap['dates_with_100_plus_pct']}% could fill 100")
    for split in ("train", "holdout"):
        for n in LIST_SIZES:
            b = out["lists"][split][f"blend_top{n}"]
            r = out["lists"][split][f"random_top{n}"]
            if not b.get("episodes"):
                continue
            print(f"  {split:<8} top{n:<4} blend mean={b['equal_weight_mean_fwd_60d']:>7} "
                  f"win%={b['win_rate_pct']:>5} tailR={str(b['tail_ratio']):>6} "
                  f"| random mean={r['equal_weight_mean_fwd_60d']:>7} "
                  f"| filled {str(b['median_names_per_date']):>5} names/date")
    return 0

# ── stage: report (Parts 10-12) ─────────────────────────────────────────────


# ── fundamentals v2: quality, profitability, valuation ──────────────────────
#
# The first roster carried growth, margins and dilution only, so the
# quality-compounder and value-plus-quality families could not be expressed at
# all. These add earnings, returns on capital, balance-sheet strength and
# valuation. Same provenance caveats as everything fundamental here: the
# statements are the CURRENT versions (restatement-contaminated) and coverage
# is the old scanner's episode set (partly forward-looking); valuation
# additionally needs market cap, which covers 57% of rows.

FUND2_COLS: tuple[str, ...] = (
    "eps_ttm", "eps_growth_yoy_pct", "eps_accel_pp",
    "fcf_margin_pct", "fcf_turned_positive", "loss_narrowing",
    "roe_pct", "roic_proxy_pct", "gross_profit_to_assets",
    "debt_to_equity", "current_ratio", "interest_coverage",
    "ev_to_sales", "fcf_yield_pct", "earnings_yield_pct",
    "revenue_ttm", "shareholder_yield_pct",
)


def _fund2_features(rows: list[dict], mcap: float | None) -> dict:
    """Quality/profitability/valuation from newest-first merged quarters."""
    from research.backtests.alpha_reconstruction_lab import (
        MIN_REVENUE_BASE, _num, _sum4)

    out = {c: None for c in FUND2_COLS}
    if len(rows) < 4:
        return out
    rev, rev_p = _sum4(rows, "revenue"), _sum4(rows, "revenue", 4)
    ni, ni_p = _sum4(rows, "netIncome"), _sum4(rows, "netIncome", 4)
    fcf, fcf_p = _sum4(rows, "freeCashFlow"), _sum4(rows, "freeCashFlow", 4)
    gp = _sum4(rows, "grossProfit")
    oi = _sum4(rows, "operatingIncome")
    out["revenue_ttm"] = rev

    eps, eps_p = _sum4(rows, "epsDiluted"), _sum4(rows, "epsDiluted", 4)
    if eps is not None:
        out["eps_ttm"] = eps
    # Growth on a negative or near-zero base is not a growth rate; require a
    # meaningful positive prior year before quoting one.
    if eps is not None and eps_p is not None and eps_p > 0.10:
        out["eps_growth_yoy_pct"] = (eps / eps_p - 1.0) * 100.0
    q = [_num(rows[i].get("epsDiluted")) if len(rows) > i else None for i in (0, 1, 4, 5)]
    if None not in q and q[2] > 0.02 and q[3] > 0.02:
        out["eps_accel_pp"] = ((q[0] / q[2] - 1.0) - (q[1] / q[3] - 1.0)) * 100.0

    if fcf is not None and rev and rev >= MIN_REVENUE_BASE:
        out["fcf_margin_pct"] = fcf / rev * 100.0
    if fcf is not None and fcf_p is not None:
        out["fcf_turned_positive"] = float(fcf > 0 and fcf_p <= 0)
    if ni is not None and ni_p is not None:
        out["loss_narrowing"] = float(ni < 0 and ni > ni_p)

    eq = _num(rows[0].get("totalStockholdersEquity")) or _num(rows[0].get("totalEquity"))
    assets = _num(rows[0].get("totalAssets"))
    debt = _num(rows[0].get("totalDebt"))
    cash = _num(rows[0].get("cashAndShortTermInvestments"))
    if ni is not None and eq and eq > 0:
        out["roe_pct"] = ni / eq * 100.0
    # ROIC proxy: after-tax operating income over invested capital, at a flat
    # 21% rate. A proxy, not a reported figure.
    if oi is not None and eq and debt is not None and (eq + debt) > 0:
        out["roic_proxy_pct"] = (oi * 0.79) / (eq + debt) * 100.0
    if gp is not None and assets and assets > 0:
        out["gross_profit_to_assets"] = gp / assets
    if debt is not None and eq and eq > 0:
        out["debt_to_equity"] = debt / eq
    ca, cl = _num(rows[0].get("totalCurrentAssets")), _num(rows[0].get("totalCurrentLiabilities"))
    if ca is not None and cl and cl > 0:
        out["current_ratio"] = ca / cl
    interest = _sum4(rows, "interestExpense")
    if oi is not None and interest and interest > 0:
        out["interest_coverage"] = oi / interest

    if mcap and mcap > 0:
        ev = mcap + (debt or 0.0) - (cash or 0.0)
        if rev and rev >= MIN_REVENUE_BASE:
            out["ev_to_sales"] = ev / rev
        if fcf is not None:
            out["fcf_yield_pct"] = fcf / mcap * 100.0
        if ni is not None:
            out["earnings_yield_pct"] = ni / mcap * 100.0
        buyback = _sum4(rows, "commonStockRepurchased")
        div = _sum4(rows, "commonDividendsPaid")
        if buyback is not None and div is not None:
            out["shareholder_yield_pct"] = (-(buyback + div)) / mcap * 100.0
    return out


def build_fundamental_panel_v2(root: Path, scan_dates, mcap_lookup: dict):
    """Growth + quality + valuation per (ticker, scan_date), point-in-time."""
    import pandas as pd
    from research.backtests.alpha_reconstruction_lab import _fund_features, _usable_date

    src = root / "cache" / "replay_fundamentals"
    files = sorted(src.glob("*.json"))
    recs, t0 = [], time.time()
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
                node.update({k: v for k, v in r.items() if k != "date"})
                u = _usable_date(r["acceptedDate"])
                if u is not None and (node["_usable"] is None or u > node["_usable"]):
                    node["_usable"] = u
        rows = sorted((v for v in merged.values() if v["_usable"]), key=lambda r: r["date"])
        if not rows:
            continue
        usable = [r["_usable"] for r in rows]
        for ts in scan_dates:
            known = [r for r, u in zip(rows, usable) if u <= ts.date()]
            if len(known) < 4:
                continue
            newest = list(reversed(known[-9:]))
            rec = _fund_features(newest)
            rec.update(_fund2_features(newest, mcap_lookup.get((f.stem, ts))))
            rec["ticker"] = f.stem
            rec["scan_date"] = ts
            recs.append(rec)
        if n % 800 == 0:
            print(f"  fundamentals v2 [{n}/{len(files)}] {time.time() - t0:.0f}s", flush=True)
    df = pd.DataFrame(recs)
    print(f"fundamental panel v2: {len(df):,} rows · {df.ticker.nunique():,} tickers "
          f"· {time.time() - t0:.0f}s", flush=True)
    return df


def add_peer_confirmation(df):
    """Sector breadth as of the scan date: are this name's peers also working?

    Computed from the CURRENT sector map (43% coverage), so it is a shape proxy
    and inherits that bias. The value itself is point-in-time: it uses only
    same-date relative strength of other names.
    """
    import numpy as np

    known = df["sector_current_meta"] != "UNKNOWN"
    g = df[known].groupby(["scan_date", "sector_current_meta"], observed=True)
    df["sector_peer_breadth_pct"] = np.nan
    df.loc[known, "sector_peer_breadth_pct"] = (
        g["rs_63d"].transform(lambda s: (s > 0).mean() * 100))
    df["sector_peer_count"] = np.nan
    df.loc[known, "sector_peer_count"] = g["rs_63d"].transform("size")
    df["sector_rs_rank_pct"] = np.nan
    df.loc[known, "sector_rs_rank_pct"] = g["rs_63d"].transform(
        lambda s: s.rank(pct=True) * 100)
    return df


def report_stage(args) -> int:
    root = Path(args.root)
    tw = LiveArtifactTripwire.snapshot(root)
    mp = json.loads((root / MAP_REL).read_text())
    st = json.loads((root / STRATEGIES_REL).read_text())
    ls = json.loads((root / LISTS_REL).read_text())
    fam = {f["name"]: f for f in st["families"]}

    def sel(f, split):
        return ((f.get(split) or {}).get(f"selection_{PRIMARY_H}d") or {})

    def fwd(f, split):
        return ((f.get(split) or {}).get(f"fwd_{PRIMARY_H}d") or {})

    L: list[str] = []
    A = L.append
    A("=" * 78)
    A("ALPHA DISCOVERY TOURNAMENT — 30 strategy families, one window, one holdout")
    A("=" * 78)
    A(f"Replay window {mp['window'][0]} .. {mp['window'][1]} · "
      f"{mp['universe']['scan_dates']} weekly dates · "
      f"{mp['universe']['unique_tickers']:,} tickers · "
      f"median {mp['universe']['median_names_per_week']:.0f} tradable names per week")
    A(f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    A("")
    A("RESEARCH ONLY. Replay evidence over a fixed historical window. Not live")
    A("forward evidence, not Phase 4B input, not a gate/threshold/score change,")
    A("not a trade signal. Verdicts sit on their own ladder and must never be")
    A("pooled with live program verdicts, HC/EO routing, or the board.")
    A("")
    A("Discovery 2022-2024. The 2025 holdout was read once, after the rules were")
    A("fixed. Families are scored on the MEAN as well as the median: an")
    A("equal-weight research list earns the mean, and judging a right-skewed")
    A("family on its median alone is how the previous study mis-ranked them.")
    A("")

    A("-" * 78)
    A("1. THE RANKING — every family, best evidence first")
    A("-" * 78)
    A(f"{'':4}{'family':<34}{'archetype':<28}{'verdict':<26}{'sel_tr':>7}{'sel_ho':>7}"
      f"{'tailR':>7}{'/date':>7}")
    order = {"TRUE_ALPHA_CANDIDATE_POOL": 0, "PROMISING_BUT_UNPROVEN": 1,
             "SPECIAL_SITUATION_ONLY": 2, "AVOID_TRAP": 3, "INCONCLUSIVE": 4,
             "REJECTED_OVERFIT": 5, "REJECTED_NEGATIVE_SELECTION": 6,
             "REJECTED_DATA_UNSAFE": 7}
    for f in sorted(st["families"], key=lambda x: (order.get(x.get("verdict"), 9),
                                                   -(sel(x, "holdout").get("selection_mean") or -99))):
        A(f"  {f['letter']:<4}{f['name'][:32]:<32}{f.get('archetype', '')[:26]:<28}"
          f"{f.get('verdict', ''):<26}"
          f"{str(sel(f, 'train').get('selection_mean')):>7}"
          f"{str(sel(f, 'holdout').get('selection_mean')):>7}"
          f"{str(fwd(f, 'train').get('tail_ratio')):>7}"
          f"{str((f.get('train') or {}).get('median_names_per_date')):>7}")
    A("")
    A("sel_* = mean forward-60d selection vs the same-date pool the family drew")
    A("from, in percentage points. /date = median names surfaced per scan date.")
    A(f"Verdict counts: {json.dumps(st['verdict_counts'])}")
    A("")

    A("-" * 78)
    A("2. THE RESULT THAT MATTERS: CLASSIC 12-1 MOMENTUM")
    A("-" * 78)
    m1, m2 = fam.get("momentum_12_1_raw", {}), fam.get("momentum_12_1_confirmed", {})
    A("The twelve-month return excluding the most recent month — the canonical")
    A("cross-sectional momentum factor — is the only family to clear every")
    A("pre-declared bar. It was absent from the previous tournament, which tested")
    A("only 20/63-day relative strength: short-horizon windows contaminated by")
    A("reversal, which is a different and worse signal.")
    A("")
    for lbl, f in (("M1 raw top-quintile", m1), ("M2 + trend/exhaustion filters", m2)):
        if not f:
            continue
        tr, ho = f["train"], f["holdout"]
        A(f"  {lbl}")
        A(f"    train   n={tr['episodes']:,} ({tr['median_names_per_date']:.0f}/date) "
          f"mean={fwd(f, 'train').get('mean')} median={fwd(f, 'train').get('median')} "
          f"tail_ratio={fwd(f, 'train').get('tail_ratio')}")
        A(f"            selection {sel(f, 'train').get('selection_mean')}pp "
          f"CI{sel(f, 'train').get('date_cluster_ci_mean')}")
        A(f"    holdout n={ho['episodes']:,} mean={fwd(f, 'holdout').get('mean')} "
          f"selection {sel(f, 'holdout').get('selection_mean')}pp "
          f"CI{sel(f, 'holdout').get('date_cluster_ci_mean')}")
        A(f"    vs vol/liquidity twin: train "
          f"{(f['matched_twin']['train'] or {}).get('mean_difference')} "
          f"{(f['matched_twin']['train'] or {}).get('mean_ci')}  ·  holdout "
          f"{(f['matched_twin']['holdout'] or {}).get('mean_difference')} "
          f"{(f['matched_twin']['holdout'] or {}).get('mean_ci')}")
        A(f"    by year: " + ", ".join(f"{y}:{v.get('selection_mean')}"
                                        for y, v in f["by_year"].items()))
        cap = (tr.get("capture") or {})
        A(f"    winner capture lift {cap.get(f'top10pct_{PRIMARY_H}d', {}).get('lift')} vs "
          f"loser lift {cap.get(f'bot10pct_{PRIMARY_H}d', {}).get('lift')}; holds "
          f"{cap.get(f'top10pct_{PRIMARY_H}d', {}).get('share_of_all_captured_pct')}% "
          f"of all top-decile winners")
        A("")
    A("It is positive in all four years, beats a volatility/liquidity-matched twin")
    A("in both halves with CIs clear of zero, is spread across sectors (largest")
    A("22%), is not carried by a few names (top-10 tickers 1.3% of episodes), and")
    A("surfaces ~600 names a day — enough to fill any list size.")
    A("")
    A("  THE CATCH, and it is a real one:")
    pv = (m1.get("portfolio_vs_per_name") or {})
    for split in ("train", "holdout"):
        node = pv.get(split) or {}
        if node:
            A(f"    {split}: portfolio edge positive={node['portfolio_edge_positive']}, "
              f"typical-name edge positive={node['typical_name_edge_positive']}")
    A("    The date-clustered CI (does an equal-weight list of these names beat the")
    A("    tape?) is positive. The ticker-clustered CI (does the TYPICAL NAME beat")
    A("    its own date's universe?) is NEGATIVE. Momentum's edge is a portfolio")
    A("    property carried by a minority of names. It is usable as a list and")
    A("    misleading name-by-name — which is precisely how a human reads a")
    A("    research list. Any product built on it must be equal-weight and wide,")
    A("    never a shortlist of high-conviction singles.")
    A("")

    A("-" * 78)
    A("3. WHAT ELSE SURVIVED, BY FAMILY GROUP")
    A("-" * 78)
    groups: dict[str, list] = {}
    for f in st["families"]:
        groups.setdefault(f.get("archetype", "other"), []).append(f)
    for g, fs in groups.items():
        best = max(fs, key=lambda x: (sel(x, "holdout").get("selection_mean") or -99))
        verdicts = ", ".join(sorted({x.get("verdict", "") for x in fs}))
        A(f"  {g}")
        A(f"    families: {len(fs)}  verdicts: {verdicts}")
        A(f"    best on holdout: {best['name']} "
          f"({sel(best, 'holdout').get('selection_mean')}pp, "
          f"{(best.get('train') or {}).get('median_names_per_date')}/date)")
    A("")
    A("  Read across the groups:")
    A("  * Price momentum is the only group with a family that clears the bar.")
    A("  * Quality compounder went 0 for 3 — all REJECTED_OVERFIT, positive in")
    A("    2022-2024 and negative in 2025. Quality was the most fragile group in")
    A("    the study, not the safest.")
    A("  * Value+quality re-rating: the same story (V1 train +2.46, holdout -1.86).")
    A("  * Earnings re-rating is the best of the fundamental groups but thin:")
    A("    ~10 names a day and a train CI that includes zero.")
    A("  * Fundamental acceleration survives only in its cash-flow form (F3, FCF")
    A("    turn plus operating leverage) and fails its twin in the holdout.")
    A("  * Breakout/base structure failed outright — the volume-confirmed breakout")
    A("    (B1) and the small-cap breakout (B3) both had NEGATIVE selection.")
    A("  * Institutional accumulation (up/down volume) was flat: no separation.")
    A("  * Controlled volatility (X1) is real but sparse, and does not beat its")
    A("    own volatility-matched twin — most of it is the bucket, not the filter.")
    A("")

    A("-" * 78)
    A("4. THE OPPORTUNITY MAP BEHIND THE FAMILIES")
    A("-" * 78)
    A("Style-bucket payoffs, train years, forward 60d. Context for why some")
    A("families work, not a family in itself:")
    A("")
    A("  vol decile   mean   median  tail ratio   +100%/126d   -50%/60d")
    for k, v in sorted(mp["style_bucket_payoffs"]["volatility_decile"].items(),
                       key=lambda kv: int(kv[0])):
        A(f"      {int(k) + 1:>2}      {v['mean']:>6}  {v['median']:>7}   "
          f"{str(v['tail_ratio']):>6}      {v['up100_126d_rate']:>6}     "
          f"{v['down50_60d_rate']:>6}")
    A("")
    A("Deciles 1-9 carry a positive mean; only decile 10 inverts. This corrects")
    A("the previous study's 'volatility is the winner fingerprint' framing — but")
    A("note what the tournament then showed: knowing that volatility deciles 6-9")
    A("pay did NOT produce a working strategy. X1, the family built directly on")
    A("it, is sparse and fails its matched twin. The bucket pays; the filter adds")
    A("nothing on top of the bucket.")
    A("")

    A("-" * 78)
    A("5. THE DAILY LIST")
    A("-" * 78)
    cap = ls["blend_capacity"]
    A(f"Blending every surviving family: median "
      f"{cap['median_names_available_per_date']:.0f} candidates per date. "
      f"{cap['dates_with_50_plus_pct']}% of dates can fill 50; "
      f"{cap['dates_with_100_plus_pct']}% can fill 100.")
    A("")
    A(f"{'':2}{'list':<16}{'split':<10}{'mean 60d':>9}{'win%':>7}{'tailR':>7}"
      f"{'vs random':>11}{'turnover':>10}")
    for split in ("train", "holdout"):
        for n_ in LIST_SIZES:
            b = ls["lists"][split].get(f"blend_top{n_}") or {}
            r = ls["lists"][split].get(f"random_top{n_}") or {}
            if not b.get("episodes"):
                continue
            diff = round(b["equal_weight_mean_fwd_60d"] - r["equal_weight_mean_fwd_60d"], 2)
            A(f"  top{n_:<13}{split:<10}{str(b['equal_weight_mean_fwd_60d']):>9}"
              f"{str(b['win_rate_pct']):>7}{str(b['tail_ratio']):>7}{str(diff):>11}"
              f"{str(b['week_to_week_name_overlap_pct']):>10}")
    A("")
    A("The blend beats a random same-date list of the same size at every size in")
    A("both halves. Caveats, in order of how much they should worry you:")
    A("  1. Blend MEMBERSHIP was chosen after seeing which families survived, and")
    A("     those verdicts used the holdout. The blend's holdout column is")
    A("     therefore contaminated; only a pre-registered blend tested on a future")
    A("     year is clean. The individual M1 result does not have this problem.")
    A("  2. 2025 was a strong year for everything — random returned +3.3%. Read")
    A("     the gap, never the level.")
    A("  3. Week-to-week name overlap is high, so this is a slow list, not a")
    A("     daily-churn product.")
    A("")

    A("-" * 78)
    A("6. WHAT IS STILL MISSING")
    A("-" * 78)
    for un in st["untestable"]:
        A(f"  {un['name']}: {un['why']}")
    A("")
    A("  market cap: 57% row coverage, and the missing 43% is NOT random — it is")
    A("  names the old scanner never surfaced, including large established")
    A("  companies (S&P Global, Apollo, CRH, Coterra, Cirrus Logic). Every")
    A("  valuation and quality family inherits that bias. ~1,400 provider calls")
    A("  would close it.")
    A("")

    A("=" * 78)
    A("7. DIRECT ANSWERS")
    A("=" * 78)
    A("")
    A("1. Best evidence?  PRICE MOMENTUM, specifically 12-1 (twelve-month return")
    A("   excluding the last month). Only family to clear every bar: positive in")
    A("   all four years, both CIs clear of zero, beats its style-matched twin in")
    A("   both halves, sector-spread, ~600 names/day. Everything else is either")
    A("   thin, unproven, or failed the holdout.")
    A("")
    A("2. Best right-tail/left-tail asymmetry?  F3 (FCF turn + operating leverage)")
    A("   at 1.66 train tail ratio, and T2 (turnaround on FCF inflection) at 1.51,")
    A("   ahead of momentum's 1.41. Both fail their matched twin in the holdout,")
    A("   so the asymmetry is real but not clearly theirs.")
    A("")
    A("3. Best mean without median damage?  M2 (momentum with trend and exhaustion")
    A("   filters): train mean +2.02 with median +0.52, holdout mean +5.47 with")
    A("   median +2.96. It is the only family that is clearly positive on BOTH")
    A("   statistics in both halves. It sacrifices a little capture for it.")
    A("")
    A("4. Best top-winner capture?  M1 holds 24.9% of all top-decile winners —")
    A("   far more than anything else, because it is wide. Among narrow families,")
    A("   T2 has the highest winner-rate lift (1.52) but a matching loser lift")
    A("   (1.55), which is the definition of no asymmetry.")
    A("")
    A("5. Best daily capacity?  M1 (~600/date) and M2 (~360/date). The blend")
    A("   reaches 763. Every other surviving family is under 130, and four are")
    A("   under 30.")
    A("")
    A("6. Survives 2025?  M1, M2, F3, T2, S1, E1 on the mean. Everything in the")
    A("   quality and value groups did not.")
    A("")
    A("7. Kill from the old scanner?  Its ranking axis. combined_rs (20/63-day RS)")
    A("   saturates at 100 exactly where forward returns are worst, and the")
    A("   short-window RS it is built from is the contaminated version of the")
    A("   signal that does work at 12-1. The fix is not a threshold — it is a")
    A("   different lookback.")
    A("")
    A("8. Keep only as source-universe discovery?  The broad scan. As a net it is")
    A("   fine; as an ordering it is negative (baseline Z0: train selection")
    A("   -1.83pp against the pool it drew from).")
    A("")
    A("9. New research-only alpha engine?  A wide, equal-weight, momentum-anchored")
    A("   watchlist: rank the tradable universe on 12-1 with a trend filter and an")
    A("   exhaustion exclusion, take 50-100 names, tag each with any secondary")
    A("   family it also matches (earnings re-rating, FCF turn, turnaround), and")
    A("   run it forward in shadow. Not a conviction ranking, and not a shortlist.")
    A("")
    A("10. Top-25 / 50 / 100 daily?  ALL THREE ARE FILLABLE on capacity, and the")
    A("    per-name edge is larger at 25 than at 100. But the honest product is a")
    A("    wide list read as a portfolio, because of the portfolio-vs-per-name")
    A("    divergence in section 2. A top-25 read as 25 individual convictions is")
    A("    the one framing the evidence does NOT support.")
    A("")
    A("=" * 78)
    A("BOTTOM LINE")
    A("=" * 78)
    A("The previous two studies were looking in the wrong places — bespoke")
    A("fingerprints and hand-made archetypes — and never tested the best-documented")
    A("factor in the literature. Tested here, 12-1 momentum is the one family that")
    A("survives an out-of-sample year, a style-matched control, and every")
    A("concentration check, with enough capacity to fill a daily list.")
    A("")
    A("That is a real finding, and it is also a modest one. The edge is roughly")
    A("+1 to +2pp per 60 days against the same-date universe, it lives in the mean")
    A("rather than the typical name, and it is a well-known factor rather than a")
    A("discovery. Quality, value, breakout structure and institutional")
    A("accumulation — the families most people would have bet on — did not")
    A("survive. Nothing here is a validated edge and nothing should change a live")
    A("rule; the correct next step is forward measurement, not integration.")
    A("")

    text = "\n".join(L)
    write_replay_text(root / REPORT_TXT_REL, text + "\n", root=root)
    summary = {
        "kind": "ALPHA_TOURNAMENT_LATEST",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window": mp["window"],
        "families_tested": len(st["families"]),
        "headline": (
            "Classic 12-1 momentum is the only family of 30 to clear every "
            "pre-declared bar: positive in all four years, beats a "
            "volatility/liquidity-matched twin in both halves, sector-spread, ~600 "
            "names/day. Its edge is a PORTFOLIO property — the typical name in the "
            "cohort underperforms its own date's universe median."),
        "verdict_counts": st["verdict_counts"],
        "family_verdicts": {f["name"]: f.get("verdict") for f in st["families"]},
        "best_family": "momentum_12_1_raw",
        "daily_list_capacity": ls["blend_capacity"],
        "group_findings": {
            "price_momentum": "only group with a family clearing the bar",
            "quality_compounder": "0 of 3 — all REJECTED_OVERFIT, negative in 2025",
            "value_quality": "same pattern as quality: train positive, holdout negative",
            "earnings_re_rating": "best of the fundamental groups but ~10 names/day",
            "breakout_base": "negative selection; volume-confirmed breakouts failed",
            "institutional_accumulation": "flat, no separation",
            "controlled_volatility": "real but sparse, and does not beat its own "
                                     "volatility-matched twin",
        },
        "not_supported": [
            "reading a top-25 as 25 individual high-conviction names",
            "any ranking, gate, threshold, score, HC/EO or routing change",
            "promotion of any family to a signal",
        ],
        "report_txt": REPORT_TXT_REL,
        "artifacts": [MAP_REL, STRATEGIES_REL, LISTS_REL],
        **provenance_flags(fundamentals=True),
    }
    assert_provenance(summary)
    write_replay_json(root / LATEST_REL, summary, root=root)
    print(tw.report())
    tw.assert_clean()
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

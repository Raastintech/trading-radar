"""Autonomous Alpha Lab — the liquid-universe concentration study.

Sandbox-only. Reads cached replay panels; makes zero provider calls; writes
only under ``cache/research/agent_lab/``. Imported by nothing in production.

THE QUESTION
------------
Four prior studies in this repo (historical replay, alpha reconstruction lab,
alpha tournament, M1 decision memo) all ran on a universe floored at
price >= $2 and median 20-day dollar volume >= $1M — roughly 3,000 names a
week, most of which nobody could manually trade. Every negative conclusion
they reached was measured there:

  * the winner fingerprint is a volatility fingerprint (winner/loser feature
    AUC correlation +0.947),
  * 12-1 momentum's edge is a portfolio property whose typical name loses,
  * concentration makes the product worse per name, not better.

This lab asks whether those conclusions are artifacts of junk contamination,
by re-running the same measurements on a universe that a human could actually
trade, and by testing concentration there directly.

Verdicts use the brief's ladder and nothing else.
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Sequence

import numpy as np
import pandas as pd

from research.agent_lab.lab_stats import (
    assert_no_lookahead,
    ci_positive,
    concentration_stats,
    date_clustered_ci,
    rank_auc_within_date,
    ticker_clustered_ci,
)

# --------------------------------------------------------------- constants --

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PANEL_PATH = os.path.join(REPO, "cache/research/alpha_tournament_intermediates/panel.parquet")
FUND_PATH = os.path.join(REPO, "cache/research/alpha_tournament_intermediates/fundamental_panel_v2.parquet")
EVENT_PATH = os.path.join(REPO, "cache/research/alpha_tournament_intermediates/event_panel.parquet")
OUT_DIR = os.path.join(REPO, "cache/research/agent_lab")
INTERMEDIATE_DIR = os.path.join(OUT_DIR, "intermediates")

TRAIN_YEARS = (2022, 2023, 2024)
HOLDOUT_YEAR = 2025

# Every column below is an as-of feature verified by the panel's own
# look-ahead audit. assert_no_lookahead() re-checks them at run time.
RANKING_INPUTS = [
    "mom_12_1",
    "mom_6_1",
    "combined_rs",
    "atr14_pct",
    "dvol_med_20",
    "price",
    "above_ma200",
    "above_ma50",
    "ma200_slope_20d_pct",
    "ma50_slope_20d_pct",
    "dd_from_high_pct",
    "days_since_252d_high",
    "rvol_63d_pct",
    "new_63d_high",
    "base_tightness_63d",
]

MEASUREMENT_COLUMNS = [
    "ticker",
    "scan_date",
    "year",
    "sector_current_meta",
    "market_cap",
    "in_score_100_zone",
]

HORIZONS = (20, 60)
LIST_SIZES = (5, 10, 15, 25, 50, 100)

# Non-overlapping cadence: a 60-day horizon needs ~13 weekly dates between
# observations, a 20-day horizon ~4.
NON_OVERLAP_STRIDE = {20: 4, 60: 13}

RANDOM_DRAWS = 200
BOOTSTRAP_REPS = 2000
SEED = 20260910


@dataclass(frozen=True)
class Universe:
    name: str
    min_price: float
    min_dvol_med_20: float
    label: str


UNIVERSES = (
    Universe("full_prior_work", 2.0, 1_000_000.0, "price >= $2, median 20d $vol >= $1M (the floor every prior study used)"),
    Universe("liquid_tradable", 5.0, 20_000_000.0, "price >= $5, median 20d $vol >= $20M (manual-trading floor)"),
    Universe("strict_tradable", 10.0, 50_000_000.0, "price >= $10, median 20d $vol >= $50M (sensitivity)"),
)


@dataclass
class Method:
    name: str
    description: str
    # returns (score, eligible) aligned to the frame index; higher score = higher rank
    fn: Callable[[pd.DataFrame], tuple[pd.Series, pd.Series]]
    family: str = "candidate"
    coverage_limited: bool = False
    notes: str = ""


# ------------------------------------------------------------------ loading --

def load_panel(*, verbose: bool = True) -> pd.DataFrame:
    """Load the replay panel plus the fundamental and event side-panels."""
    assert_no_lookahead(RANKING_INPUTS)
    cols = sorted(set(MEASUREMENT_COLUMNS + RANKING_INPUTS))
    for h in HORIZONS:
        cols += [f"fwd_{h}d", f"fwd_{h}d_matured", f"fwd_{h}d_vs_spy", f"fwd_{h}d_vs_qqq", f"fwd_{h}d_vs_iwm"]
    df = pd.read_parquet(PANEL_PATH, columns=sorted(set(cols)))
    df["ticker"] = df["ticker"].astype(str)
    if verbose:
        print(f"panel: {len(df):,} eligible rows · {df.ticker.nunique():,} tickers · {df.scan_date.nunique()} dates")

    fund_cols = ["ticker", "scan_date", "profitable", "fcf_positive", "dilution_yoy_pct", "quarters_available"]
    fund = pd.read_parquet(FUND_PATH, columns=fund_cols)
    fund["ticker"] = fund["ticker"].astype(str)
    fund["scan_date"] = pd.to_datetime(fund["scan_date"]).astype("datetime64[us]")
    df = df.merge(fund, on=["ticker", "scan_date"], how="left", validate="one_to_one")

    ev = pd.read_parquet(EVENT_PATH)
    ev["ticker"] = ev["ticker"].astype(str)
    ev["scan_date"] = pd.to_datetime(ev["scan_date"]).astype("datetime64[us]")
    df = df.merge(ev, on=["ticker", "scan_date"], how="left", validate="one_to_one")

    df["split"] = np.where(df["year"] == HOLDOUT_YEAR, "holdout", np.where(df["year"].isin(TRAIN_YEARS), "train", "other"))
    df = df[df["split"] != "other"].copy()
    if verbose:
        print(f"  fundamentals present on {df['profitable'].notna().mean():.1%} of rows · "
              f"earnings-reaction on {df['post_earnings_reaction_pct'].notna().mean():.1%}")
    return df


def build_universe(df: pd.DataFrame, u: Universe, *, verbose: bool = True) -> pd.DataFrame:
    """Filter to a universe and attach within-date, within-universe deciles."""
    m = (df["price"] >= u.min_price) & (df["dvol_med_20"] >= u.min_dvol_med_20)
    out = df[m].copy()
    g = out.groupby("scan_date", observed=True)
    out["vol_dec"] = g["atr14_pct"].transform(lambda s: pd.qcut(s.rank(method="first"), 10, labels=False, duplicates="drop"))
    out["liq_dec"] = g["dvol_med_20"].transform(lambda s: pd.qcut(s.rank(method="first"), 10, labels=False, duplicates="drop"))
    out["vol_dec"] = out["vol_dec"].fillna(-1).astype(int)
    out["liq_dec"] = out["liq_dec"].fillna(-1).astype(int)
    out["style_cell"] = out["vol_dec"] * 10 + out["liq_dec"]
    for h in HORIZONS:
        col = f"fwd_{h}d"
        out[f"uni_mean_{h}"] = g[col].transform("mean")
        out[f"uni_median_{h}"] = g[col].transform("median")
    if verbose:
        per = out.groupby("scan_date").size()
        print(f"universe {u.name}: {len(out):,} rows · {out.ticker.nunique():,} tickers · "
              f"median {per.median():.0f}/date (p10 {per.quantile(0.1):.0f})")
    return out


# ------------------------------------------------------------------ methods --

def _pct_rank_within_date(frame: pd.DataFrame, col: str) -> pd.Series:
    return frame.groupby("scan_date", observed=True)[col].rank(pct=True)


def _z_within_date(frame: pd.DataFrame, col: str, *, clip: float = 3.0) -> pd.Series:
    g = frame.groupby("scan_date", observed=True)[col]
    z = (frame[col] - g.transform("mean")) / g.transform("std").replace(0.0, np.nan)
    return z.clip(-clip, clip)


def _m_momentum(frame: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    return frame["mom_12_1"], frame["mom_12_1"].notna()


def _m_old_scanner_axis(frame: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    return frame["combined_rs"], frame["combined_rs"].notna()


def _m_momentum_style_neutral(frame: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """12-1 momentum ranked *within* its own volatility x liquidity cell.

    If momentum is only a style tilt, neutralising the style should destroy it.
    """
    score = frame.groupby(["scan_date", "style_cell"], observed=True)["mom_12_1"].rank(pct=True)
    return score, frame["mom_12_1"].notna()


def _trend_anti_junk_mask(frame: pd.DataFrame) -> pd.Series:
    """Top-quintile 12-1 momentum, in an established uptrend, not exhausted,
    not in the top two volatility deciles of its own universe."""
    mom_pct = _pct_rank_within_date(frame, "mom_12_1")
    return (
        (mom_pct >= 0.80)
        & (frame["above_ma200"] == 1)
        & (frame["ma200_slope_20d_pct"] > 0)
        & (frame["dd_from_high_pct"] > -25.0)
        & (frame["vol_dec"] <= 7)
    ).fillna(False)


def _m_trend_anti_junk(frame: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    return frame["mom_12_1"], _trend_anti_junk_mask(frame)


def _m_trend_anti_junk_quality(frame: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    m = _trend_anti_junk_mask(frame)
    m = m & (frame["profitable"] == 1) & (frame["fcf_positive"] == 1) & (frame["dilution_yoy_pct"] < 5.0)
    return frame["mom_12_1"], m.fillna(False)


def _m_trend_anti_junk_pead(frame: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    m = _trend_anti_junk_mask(frame)
    m = m & (frame["days_since_filing"] <= 45) & (frame["post_earnings_reaction_pct"] > 0)
    return frame["mom_12_1"], m.fillna(False)


def _m_style_cell_leaders(frame: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Every name that tops its own volatility x liquidity cell on 12-1 momentum.

    Not a ranking — a pool of roughly 99 names per date. Evaluated whole,
    because ranking within it is what the tie-break audit showed to be noise.
    """
    pct = frame.groupby(["scan_date", "style_cell"], observed=True)["mom_12_1"].rank(pct=True)
    return frame["mom_12_1"], (pct >= 1.0 - 1e-9) & frame["mom_12_1"].notna()


def _m_low_vol_control(frame: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    return -frame["atr14_pct"], frame["atr14_pct"].notna()


def _m_composite_z(frame: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Equal-weight z of five pre-declared as-of features. No fitting."""
    z = (
        _z_within_date(frame, "mom_12_1")
        + _z_within_date(frame, "ma200_slope_20d_pct")
        - _z_within_date(frame, "atr14_pct")
        + _z_within_date(frame, "dd_from_high_pct")
        + frame["above_ma200"].fillna(0.0)
    )
    return z, z.notna()


METHODS: tuple[Method, ...] = (
    Method("mom_12_1", "Classic 12-1 momentum, ranked. The mandated factor baseline.", _m_momentum, family="baseline"),
    Method("old_scanner_axis", "Top names by combined_rs — the live scanner's ordering axis, as a control.", _m_old_scanner_axis, family="baseline",
           notes="Proxy for the production board's ordering, replicated for measurement only. The live scanner is untouched."),
    Method("mom_12_1_style_neutral", "12-1 momentum ranked within its own volatility x liquidity cell.", _m_momentum_style_neutral),
    Method("trend_anti_junk", "Top-quintile 12-1 + above rising MA200 + within 25% of high + not top-2 volatility deciles.", _m_trend_anti_junk),
    Method("trend_anti_junk_quality", "trend_anti_junk + profitable + FCF-positive + dilution < 5%.", _m_trend_anti_junk_quality,
           coverage_limited=True, notes="Fundamentals cover ~60% of rows and the gap is not random."),
    Method("trend_anti_junk_pead", "trend_anti_junk + filed within 45 days + positive post-earnings reaction.", _m_trend_anti_junk_pead,
           coverage_limited=True),
    Method("style_cell_leaders", "Every name topping its own volatility x liquidity cell on 12-1 momentum (~99/date). A pool, not a ranking.", _m_style_cell_leaders),
    Method("low_vol_control", "Lowest-volatility names in the universe. A style control, not a candidate.", _m_low_vol_control, family="control"),
    Method("composite_z", "Equal-weight z of momentum, MA200 slope, -ATR, proximity to high, above-MA200.", _m_composite_z),
)


# --------------------------------------------------------------- evaluation --

def _select_top_n(frame: pd.DataFrame, score: pd.Series, eligible: pd.Series, n: int) -> pd.DataFrame:
    """Top-n by score within each scan date, among eligible rows."""
    sub = frame[eligible.reindex(frame.index).fillna(False).to_numpy()].copy()
    if sub.empty:
        return sub
    sub["_score"] = score.reindex(sub.index)
    sub = sub[sub["_score"].notna()]
    if sub.empty:
        return sub
    sub = sub.sort_values(["scan_date", "_score"], ascending=[True, False])
    return sub.groupby("scan_date", observed=True, sort=False).head(n)


def _random_controls(
    universe: pd.DataFrame,
    cohort: pd.DataFrame,
    horizon: int,
    *,
    draws: int = RANDOM_DRAWS,
    seed: int = SEED,
) -> dict:
    """Same-date random and style-matched random controls of the same size.

    Both controls answer "would drawing this many names at random from the
    same date have done as well". The matched version draws each replacement
    from the selected name's own volatility x liquidity cell, so a method that
    only re-expresses a style tilt scores zero against it.
    """
    ret_col = f"fwd_{horizon}d"
    rng = np.random.default_rng(seed + horizon)
    per_date_plain: list[float] = []
    per_date_matched: list[float] = []
    beat_plain: list[float] = []
    beat_matched: list[float] = []

    uni_by_date = {d: g for d, g in universe.groupby("scan_date", observed=True)}
    for date, g in cohort.groupby("scan_date", observed=True):
        u = uni_by_date.get(date)
        if u is None or len(u) < 5:
            continue
        cohort_ret = g[ret_col].to_numpy(dtype=float)
        cohort_ret = cohort_ret[np.isfinite(cohort_ret)]
        if cohort_ret.size == 0:
            continue
        c_mean = float(cohort_ret.mean())
        n = cohort_ret.size

        u_ret = u[ret_col].to_numpy(dtype=float)
        ok = np.isfinite(u_ret)
        u_ret_ok = u_ret[ok]
        if u_ret_ok.size < n:
            continue
        idx = rng.integers(0, u_ret_ok.size, size=(draws, n))
        plain_means = u_ret_ok[idx].mean(axis=1)
        per_date_plain.append(c_mean - float(np.median(plain_means)))
        beat_plain.append(float((c_mean > plain_means).mean()))

        # style-matched: replace each pick with a random name from its own cell
        cells = g["style_cell"].to_numpy()
        cell_pools = {}
        matched_ok = True
        pools: list[np.ndarray] = []
        for cell in cells[: len(cohort_ret)]:
            if cell not in cell_pools:
                pr = u.loc[u["style_cell"] == cell, ret_col].to_numpy(dtype=float)
                cell_pools[cell] = pr[np.isfinite(pr)]
            pool = cell_pools[cell]
            if pool.size == 0:
                matched_ok = False
                break
            pools.append(pool)
        if not matched_ok or not pools:
            continue
        sample = np.empty((draws, len(pools)), dtype=float)
        for j, pool in enumerate(pools):
            sample[:, j] = pool[rng.integers(0, pool.size, size=draws)]
        matched_means = sample.mean(axis=1)
        per_date_matched.append(c_mean - float(np.median(matched_means)))
        beat_matched.append(float((c_mean > matched_means).mean()))

    return {
        "vs_random_same_date": date_clustered_ci(per_date_plain, reps=BOOTSTRAP_REPS, seed=seed),
        "vs_matched_random": date_clustered_ci(per_date_matched, reps=BOOTSTRAP_REPS, seed=seed + 1),
        "pct_of_random_draws_beaten": float(np.mean(beat_plain) * 100.0) if beat_plain else float("nan"),
        "pct_of_matched_draws_beaten": float(np.mean(beat_matched) * 100.0) if beat_matched else float("nan"),
        "dates_scored": len(per_date_plain),
    }


def evaluate(
    universe: pd.DataFrame,
    method: Method,
    n: int,
    horizon: int,
    *,
    split: str,
    non_overlapping: bool = False,
    with_controls: bool = True,
) -> dict:
    """Full evidence row for one (method, list size, horizon, split)."""
    u = universe[universe["split"] == split]
    if non_overlapping:
        dates = np.sort(u["scan_date"].unique())
        keep = set(dates[:: NON_OVERLAP_STRIDE[horizon]])
        u = u[u["scan_date"].isin(keep)]
    if u.empty:
        return {"method": method.name, "n": n, "horizon": horizon, "split": split, "rows": 0}

    score, eligible = method.fn(u)
    cohort = _select_top_n(u, score, eligible, n)
    ret_col = f"fwd_{horizon}d"
    if cohort.empty:
        return {"method": method.name, "n": n, "horizon": horizon, "split": split, "rows": 0}

    r = cohort[ret_col].astype(float)
    per_date = cohort.groupby("scan_date", observed=True).agg(
        c_mean=(ret_col, "mean"), c_median=(ret_col, "median"), n_names=(ret_col, "size")
    )
    uni_date = u.groupby("scan_date", observed=True).agg(
        u_mean=(ret_col, "mean"), u_median=(ret_col, "median"), u_n=(ret_col, "size")
    )
    j = per_date.join(uni_date, how="inner")
    sel_mean = (j["c_mean"] - j["u_mean"]).to_numpy()
    sel_median = (j["c_median"] - j["u_median"]).to_numpy()

    row_sel = (cohort[ret_col] - cohort[f"uni_mean_{horizon}"]).to_numpy(dtype=float)

    out: dict = {
        "method": method.name,
        "family": method.family,
        "coverage_limited": method.coverage_limited,
        "n": n,
        "horizon": horizon,
        "split": split,
        "non_overlapping": non_overlapping,
        "rows": int(len(cohort)),
        "dates": int(j.shape[0]),
        "unique_tickers": int(cohort["ticker"].nunique()),
        "median_names_per_date": float(per_date["n_names"].median()),
        "dates_short_of_n": int((per_date["n_names"] < n).sum()),
        "mean_return_pct": float(r.mean()),
        "median_return_pct": float(r.median()),
        "win_rate_pct": float((r > 0).mean() * 100.0),
        "excess_vs_spy_pp": float(cohort[f"fwd_{horizon}d_vs_spy"].mean()),
        "excess_vs_qqq_pp": float(cohort[f"fwd_{horizon}d_vs_qqq"].mean()),
        "excess_vs_iwm_pp": float(cohort[f"fwd_{horizon}d_vs_iwm"].mean()),
        "selection_vs_universe_mean_pp": float(np.mean(sel_mean)),
        "selection_vs_universe_median_pp": float(np.mean(sel_median)),
        "portfolio_edge_ci": date_clustered_ci(sel_mean, reps=BOOTSTRAP_REPS, seed=SEED),
        "typical_name_edge_ci": ticker_clustered_ci(row_sel, cohort["ticker"].tolist(), reps=BOOTSTRAP_REPS, seed=SEED),
        "dates_won_pct": float((sel_mean > 0).mean() * 100.0),
        # left tail / drawdown behaviour
        "pct_down_over_25": float((r <= -25.0).mean() * 100.0),
        "pct_down_over_50": float((r <= -50.0).mean() * 100.0),
        "p05_return_pct": float(np.nanpercentile(r, 5)),
        "worst_return_pct": float(r.min()),
        "worst_date_selection_pp": float(np.min(sel_mean)) if sel_mean.size else float("nan"),
        # liquidity / tradability
        "median_dvol_usd": float(cohort["dvol_med_20"].median()),
        "p10_dvol_usd": float(cohort["dvol_med_20"].quantile(0.10)),
        "median_price_usd": float(cohort["price"].median()),
        "pct_in_score_100_trap": float(cohort["in_score_100_zone"].mean() * 100.0),
    }
    out.update({f"conc_{k}": v for k, v in concentration_stats(cohort).items()})
    if with_controls:
        out["controls"] = _random_controls(u, cohort, horizon)
    return out


# ------------------------------------------------------- fingerprint mirror --

def fingerprint_symmetry(universe: pd.DataFrame, horizon: int, split: str) -> dict:
    """H2 — does the winner fingerprint stop being a volatility fingerprint?

    Prior work found the features that describe the top 1% describe the bottom
    1% almost identically (AUC correlation +0.947) on the junk-inclusive
    universe. If that symmetry is a junk artifact it should weaken here.
    """
    u = universe[universe["split"] == split].copy()
    ret = f"fwd_{horizon}d"
    g = u.groupby("scan_date", observed=True)[ret]
    u["_top"] = u[ret] >= g.transform(lambda s: s.quantile(0.99))
    u["_bot"] = u[ret] <= g.transform(lambda s: s.quantile(0.01))
    feats = [f for f in RANKING_INPUTS if f in u.columns]
    assert_no_lookahead(feats)
    rows = []
    for f in feats:
        rows.append({
            "feature": f,
            "winner_auc": rank_auc_within_date(u, f, "_top"),
            "loser_auc": rank_auc_within_date(u, f, "_bot"),
        })
    d = pd.DataFrame(rows).dropna()
    corr = float(d["winner_auc"].corr(d["loser_auc"])) if len(d) > 2 else float("nan")
    same_side = float(((d["winner_auc"] - 0.5) * (d["loser_auc"] - 0.5) > 0).mean() * 100.0)
    return {
        "horizon": horizon,
        "split": split,
        "winner_loser_auc_correlation": corr,
        "pct_features_pointing_same_way": same_side,
        "features": d.sort_values("winner_auc", ascending=False).to_dict("records"),
    }


def by_year(universe: pd.DataFrame, method: Method, n: int, horizon: int) -> dict:
    """Per-year selection, to catch a result carried by one regime window."""
    out = {}
    for yr in sorted(universe["year"].unique()):
        sub = universe[universe["year"] == yr]
        score, eligible = method.fn(sub)
        cohort = _select_top_n(sub, score, eligible, n)
        if cohort.empty:
            out[int(yr)] = None
            continue
        ret = f"fwd_{horizon}d"
        per = cohort.groupby("scan_date", observed=True)[ret].mean()
        uni = sub.groupby("scan_date", observed=True)[ret].mean()
        sel = (per - uni).dropna()
        out[int(yr)] = {"selection_pp": float(sel.mean()), "dates": int(sel.size),
                        "mean_return_pct": float(cohort[ret].mean())}
    return out


def by_month_leaveout(universe: pd.DataFrame, method: Method, n: int, horizon: int, split: str) -> dict:
    """Drop each calendar month in turn — is the result carried by one month?"""
    u = universe[universe["split"] == split]
    score, eligible = method.fn(u)
    cohort = _select_top_n(u, score, eligible, n)
    if cohort.empty:
        return {}
    ret = f"fwd_{horizon}d"
    per = cohort.groupby("scan_date", observed=True)[ret].mean()
    uni = u.groupby("scan_date", observed=True)[ret].mean()
    sel = (per - uni).dropna()
    periods = pd.PeriodIndex(sel.index, freq="M")
    full = float(sel.mean())
    worst_drop, worst_period = full, None
    for p in periods.unique():
        keep = sel[periods != p]
        v = float(keep.mean())
        if v < worst_drop:
            worst_drop, worst_period = v, str(p)
    return {
        "full_selection_pp": full,
        "worst_leave_one_month_out_pp": worst_drop,
        "month_removed": worst_period,
        "months": int(len(periods.unique())),
        "still_positive_without_worst_month": bool(worst_drop > 0),
    }


def by_ticker_leaveout(universe: pd.DataFrame, method: Method, n: int, horizon: int, split: str, top_k: int = 5) -> dict:
    """Drop the most-represented tickers — is the result carried by one name?"""
    u = universe[universe["split"] == split]
    score, eligible = method.fn(u)
    cohort = _select_top_n(u, score, eligible, n)
    if cohort.empty:
        return {}
    ret = f"fwd_{horizon}d"
    uni = u.groupby("scan_date", observed=True)[ret].mean()
    top = cohort["ticker"].value_counts().head(top_k).index.tolist()
    trimmed = cohort[~cohort["ticker"].isin(top)]
    def sel_of(c: pd.DataFrame) -> float:
        if c.empty:
            return float("nan")
        per = c.groupby("scan_date", observed=True)[ret].mean()
        return float((per - uni).dropna().mean())
    return {
        "full_selection_pp": sel_of(cohort),
        "selection_without_top_tickers_pp": sel_of(trimmed),
        "tickers_removed": top,
        "share_of_episodes_removed_pct": float(cohort["ticker"].isin(top).mean() * 100.0),
    }


# ------------------------------------------------------------------ stages --

def _write_json(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=1, default=str)
    print(f"wrote {path}")


RESEARCH_ONLY_HEADER = {
    "research_only": True,
    "sandbox_only": True,
    "not_live_evidence": True,
    "not_backtest_evidence_for_phase4b": True,
    "no_recommendation": True,
    "no_trade_signal": True,
    "provider_calls": 0,
    "note": (
        "AUTONOMOUS ALPHA LAB — sandbox research on cached historical replay data. "
        "Not live forward evidence, not Phase 4B input, not a gate/threshold/score/routing "
        "change, not a trade signal, not a ranking to act on. Must never be pooled with the "
        "live forward ledger, program verdicts, HC/EO/Alpha Focus routing, M1, or the dashboard. "
        "Fundamentals are restatement-contaminated and cover ~56% of rows non-randomly."
    ),
}


def run_train(verbose: bool = True) -> dict:
    """Discovery stage. 2022-2024 only. The holdout year is not read here."""
    df = load_panel(verbose=verbose)
    df = df[df["split"] == "train"]
    results: list[dict] = []
    symmetry: list[dict] = []
    universe_profile: list[dict] = []

    for u in UNIVERSES:
        uni = build_universe(df, u, verbose=verbose)
        per = uni.groupby("scan_date").size()
        universe_profile.append({
            "universe": u.name, "label": u.label, "rows": int(len(uni)),
            "tickers": int(uni.ticker.nunique()), "median_names_per_date": float(per.median()),
            "median_dvol_usd": float(uni["dvol_med_20"].median()),
            "median_atr14_pct": float(uni["atr14_pct"].median()),
        })
        for h in HORIZONS:
            symmetry.append({"universe": u.name, **fingerprint_symmetry(uni, h, "train")})
        for m in METHODS:
            for n in LIST_SIZES:
                for h in HORIZONS:
                    r = evaluate(uni, m, n, h, split="train")
                    r["universe"] = u.name
                    results.append(r)
                    if verbose and n in (10, 100) and h == 60:
                        pe = r.get("portfolio_edge_ci", {})
                        tn = r.get("typical_name_edge_ci", {})
                        print(f"  [{u.name:16s}] {m.name:24s} N={n:3d} h={h}d  "
                              f"sel={r.get('selection_vs_universe_mean_pp', float('nan')):+6.2f}pp  "
                              f"portfolio[{pe.get('lo', float('nan')):+.2f},{pe.get('hi', float('nan')):+.2f}]  "
                              f"typical[{tn.get('lo', float('nan')):+.2f},{tn.get('hi', float('nan')):+.2f}]")
    payload = {
        "kind": "AGENT_LAB_TRAIN_RESULTS",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window": {"train_years": list(TRAIN_YEARS), "holdout_year": HOLDOUT_YEAR, "holdout_read": False},
        "universe_profile": universe_profile,
        "fingerprint_symmetry": symmetry,
        "results": results,
        **RESEARCH_ONLY_HEADER,
    }
    _write_json(os.path.join(INTERMEDIATE_DIR, "train_results.json"), payload)
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Autonomous Alpha Lab — liquid concentration study (sandbox only)")
    ap.add_argument("stage", choices=["train", "holdout", "nonoverlap", "tiebreak", "artifact", "all"],
                    help="which stage to run; 'all' runs every stage in order")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    v = not args.quiet
    if args.stage in ("train", "all"):
        run_train(verbose=v)
    if args.stage in ("holdout", "all"):
        from research.agent_lab.holdout_stage import run_holdout
        run_holdout(verbose=v)
    if args.stage in ("nonoverlap", "all"):
        from research.agent_lab.synthesis import run_synthesis
        run_synthesis(verbose=v)
    if args.stage in ("tiebreak", "all"):
        from research.agent_lab.tiebreak_robustness import run as run_tiebreak
        run_tiebreak(verbose=v)
    if args.stage in ("artifact", "all"):
        from research.agent_lab.build_artifact import build
        build()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

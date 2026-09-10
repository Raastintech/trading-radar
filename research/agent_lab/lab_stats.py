"""Reusable statistics for the Autonomous Alpha Lab (sandbox-only).

Block-bootstrap confidence intervals clustered two ways, plus the leakage
deny-list that every feature set in the lab must pass.

RESEARCH ONLY. Not imported by production code.
"""
from __future__ import annotations

import re
from typing import Iterable, Sequence

import numpy as np
import pandas as pd

# ---------------------------------------------------------------- leakage ---

# Any column matching one of these is an outcome or a forward-looking label.
# It may be used as a *measurement* target, never as a ranking input.
FORWARD_COLUMN_PATTERNS: tuple[str, ...] = (
    r"^fwd_",
    r"^mfe_",
    r"^mae_",
    r"^top\d+pct_",
    r"^bot\d+pct_",
    r"^top\d+_\d+d$",
    r"^win_",
    r"^lose_",
    r"^path_",
    r"^trap_",
    r"^excess_vs_",
    r"_matured$",
)

_FORWARD_RE = re.compile("|".join(FORWARD_COLUMN_PATTERNS))


def is_forward_column(name: str) -> bool:
    """True if ``name`` is an outcome/forward column and must not rank names."""
    return bool(_FORWARD_RE.search(name))


def assert_no_lookahead(features: Iterable[str]) -> None:
    """Raise if any candidate ranking input is a forward/outcome column."""
    bad = sorted(f for f in features if is_forward_column(f))
    if bad:
        raise ValueError(f"look-ahead columns used as ranking inputs: {bad}")


# ------------------------------------------------------------- bootstraps ---

def _pooled_mean(values: np.ndarray) -> float:
    return float(np.mean(values)) if values.size else float("nan")


def date_clustered_ci(
    per_date_values: Sequence[float],
    *,
    reps: int = 2000,
    seed: int = 20260910,
    alpha: float = 0.05,
) -> dict:
    """CI on the mean of one number per scan date.

    Answers "was an equal-weight list of these names a good thing to hold in
    the typical week", weighting every week equally.
    """
    vals = np.asarray([v for v in per_date_values if np.isfinite(v)], dtype=float)
    n = vals.size
    if n < 2:
        return {"point": _pooled_mean(vals), "lo": float("nan"), "hi": float("nan"), "n_clusters": int(n)}
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(reps, n))
    draws = vals[idx].mean(axis=1)
    lo, hi = np.quantile(draws, [alpha / 2.0, 1.0 - alpha / 2.0])
    return {"point": float(vals.mean()), "lo": float(lo), "hi": float(hi), "n_clusters": int(n)}


def ticker_clustered_ci(
    values: Sequence[float],
    tickers: Sequence[str],
    *,
    reps: int = 2000,
    seed: int = 20260910,
    alpha: float = 0.05,
) -> dict:
    """CI on the pooled mean, resampling whole tickers with replacement.

    Answers "did the typical *name* in this cohort beat its own date's
    universe", weighting every ticker equally regardless of how many episodes
    it contributed. A cohort that is large in good weeks and small in bad ones
    can pass the date-clustered test and fail this one; that disagreement is
    a composition effect, and it is the point of running both.
    """
    v = np.asarray(values, dtype=float)
    t = pd.Index(pd.Series(list(tickers), dtype="object"))
    keep = np.isfinite(v)
    v, t = v[keep], t[keep]
    if v.size < 2:
        return {"point": _pooled_mean(v), "lo": float("nan"), "hi": float("nan"), "n_clusters": 0}
    codes, uniques = pd.factorize(t)
    n_groups = len(uniques)
    if n_groups < 2:
        return {"point": float(v.mean()), "lo": float("nan"), "hi": float("nan"), "n_clusters": int(n_groups)}
    order = np.argsort(codes, kind="stable")
    v_sorted = v[order]
    codes_sorted = codes[order]
    starts = np.searchsorted(codes_sorted, np.arange(n_groups), side="left")
    ends = np.searchsorted(codes_sorted, np.arange(n_groups), side="right")
    sums = np.add.reduceat(v_sorted, starts) if v_sorted.size else np.zeros(n_groups)
    counts = (ends - starts).astype(float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n_groups, size=(reps, n_groups))
    draw_sums = sums[idx].sum(axis=1)
    draw_counts = counts[idx].sum(axis=1)
    draws = np.divide(draw_sums, draw_counts, out=np.full(reps, np.nan), where=draw_counts > 0)
    lo, hi = np.nanquantile(draws, [alpha / 2.0, 1.0 - alpha / 2.0])
    return {"point": float(v.mean()), "lo": float(lo), "hi": float(hi), "n_clusters": int(n_groups)}


def ci_excludes_zero(ci: dict) -> bool:
    lo, hi = ci.get("lo"), ci.get("hi")
    if lo is None or hi is None or not np.isfinite(lo) or not np.isfinite(hi):
        return False
    return (lo > 0.0) or (hi < 0.0)


def ci_positive(ci: dict) -> bool:
    """CI lies entirely above zero."""
    lo = ci.get("lo")
    return bool(lo is not None and np.isfinite(lo) and lo > 0.0)


# ------------------------------------------------------------------ misc ----

def rank_auc_within_date(frame: pd.DataFrame, feature: str, label: str) -> float:
    """Mean within-date rank-AUC of ``feature`` for separating ``label``.

    0.50 means no separation. Dates with no positive or no negative label
    contribute nothing.
    """
    aucs: list[float] = []
    for _, g in frame.groupby("scan_date", observed=True):
        y = g[label].to_numpy(dtype=bool)
        x = g[feature].to_numpy(dtype=float)
        ok = np.isfinite(x)
        y, x = y[ok], x[ok]
        if y.sum() == 0 or (~y).sum() == 0:
            continue
        r = pd.Series(x).rank().to_numpy()
        n1, n0 = float(y.sum()), float((~y).sum())
        aucs.append(float((r[y].sum() - n1 * (n1 + 1.0) / 2.0) / (n1 * n0)))
    return float(np.mean(aucs)) if aucs else float("nan")


def concentration_stats(frame: pd.DataFrame, sector_col: str = "sector_current_meta") -> dict:
    """Sector and ticker concentration of a cohort, for the fragility check."""
    out: dict = {"n_rows": int(len(frame)), "n_tickers": int(frame["ticker"].nunique())}
    if len(frame) == 0:
        return out
    tick_share = frame["ticker"].value_counts(normalize=True)
    out["top10_ticker_share_pct"] = float(tick_share.head(10).sum() * 100.0)
    if sector_col in frame.columns:
        known = frame[frame[sector_col].astype(str) != "UNKNOWN"]
        if len(known):
            sec = known[sector_col].astype(str).value_counts(normalize=True)
            out["largest_sector"] = str(sec.index[0])
            out["largest_sector_share_pct"] = float(sec.iloc[0] * 100.0)
            out["sector_label_coverage_pct"] = float(len(known) / len(frame) * 100.0)
    return out

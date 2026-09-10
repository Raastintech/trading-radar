"""Tie-break robustness for the style-neutral momentum pool.

Ranking 12-1 momentum *within* a volatility x liquidity cell produces about
99 names per date tied at a within-cell percentile of exactly 1.0 — one leader
per cell. A "top 25" of that score is therefore not a ranking at all: it is 25
arbitrary names drawn from the same ~99-name pool, and in the first
implementation the arbitrary tie-break was alphabetical.

This module measures the pool directly and re-draws the subset under several
deterministic seeds, so the reported effect cannot be an artifact of which
names happen to sort first.

Sandbox-only. Zero provider calls.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from research.agent_lab.lab_stats import date_clustered_ci, ticker_clustered_ci
from research.agent_lab.liquid_concentration_lab import (
    INTERMEDIATE_DIR,
    RESEARCH_ONLY_HEADER,
    UNIVERSES,
    _random_controls,
    _write_json,
    build_universe,
    load_panel,
)

LEADER_EPS = 1e-9


def cell_leader_pool(universe: pd.DataFrame) -> pd.DataFrame:
    """The names that top their own volatility x liquidity cell on 12-1 momentum."""
    pct = universe.groupby(["scan_date", "style_cell"], observed=True)["mom_12_1"].rank(pct=True)
    return universe[(pct >= 1.0 - LEADER_EPS) & universe["mom_12_1"].notna()].copy()


def _metrics(cohort: pd.DataFrame, uni: pd.DataFrame, horizon: int, *, with_controls: bool = True) -> dict:
    ret = f"fwd_{horizon}d"
    c_per = cohort.groupby("scan_date", observed=True)[ret].mean()
    u_per = uni.groupby("scan_date", observed=True)[ret].mean()
    sel = (c_per - u_per).dropna()
    row_sel = (cohort[ret] - cohort[f"uni_mean_{horizon}"]).to_numpy(dtype=float)
    r = cohort[ret].astype(float)
    out = {
        "rows": int(len(cohort)),
        "dates": int(sel.size),
        "unique_tickers": int(cohort["ticker"].nunique()),
        "median_names_per_date": float(cohort.groupby("scan_date", observed=True).size().median()),
        "selection_pp": float(sel.mean()),
        "mean_return_pct": float(r.mean()),
        "median_return_pct": float(r.median()),
        "win_rate_pct": float((r > 0).mean() * 100.0),
        "pct_down_over_25": float((r <= -25.0).mean() * 100.0),
        "dates_won_pct": float((sel > 0).mean() * 100.0),
        "portfolio_edge_ci": date_clustered_ci(sel.to_numpy()),
        "typical_name_edge_ci": ticker_clustered_ci(row_sel, cohort["ticker"].tolist()),
    }
    if with_controls:
        out["controls"] = _random_controls(uni, cohort, horizon)
    return out


def run(horizon: int = 60, sizes: tuple[int, ...] = (10, 15, 25), seeds: tuple[int, ...] = (1, 2, 3, 4, 5),
        verbose: bool = True) -> dict:
    df = load_panel(verbose=verbose)
    uni_all = build_universe(df, UNIVERSES[1], verbose=verbose)  # liquid_tradable
    pool_all = cell_leader_pool(uni_all)

    results: dict = {"pool": {}, "subsets": {}, "alphabetical_subsets": {}}
    for split in ("train", "holdout"):
        uni = uni_all[uni_all["split"] == split]
        pool = pool_all[pool_all["split"] == split]
        results["pool"][split] = _metrics(pool, uni, horizon)
        if verbose:
            p = results["pool"][split]
            print(f"pool[{split}]: {p['median_names_per_date']:.0f} names/date  sel={p['selection_pp']:+.2f}pp  "
                  f"date[{p['portfolio_edge_ci']['lo']:+.2f},{p['portfolio_edge_ci']['hi']:+.2f}]  "
                  f"ticker[{p['typical_name_edge_ci']['lo']:+.2f},{p['typical_name_edge_ci']['hi']:+.2f}]  "
                  f"median={p['median_return_pct']:+.2f}  dn25={p['pct_down_over_25']:.1f}%")

        for n in sizes:
            per_seed = []
            for seed in seeds:
                rng = np.random.default_rng(seed * 1000 + n)
                keys = rng.random(len(pool))
                sub = pool.assign(_k=keys).sort_values(["scan_date", "_k"]).groupby(
                    "scan_date", observed=True, sort=False).head(n)
                per_seed.append(_metrics(sub, uni, horizon, with_controls=False))
            sels = [m["selection_pp"] for m in per_seed]
            meds = [m["median_return_pct"] for m in per_seed]
            tick_los = [m["typical_name_edge_ci"]["lo"] for m in per_seed]
            date_los = [m["portfolio_edge_ci"]["lo"] for m in per_seed]
            results["subsets"].setdefault(split, {})[str(n)] = {
                "seeds": list(seeds),
                "selection_pp_mean": float(np.mean(sels)),
                "selection_pp_min": float(np.min(sels)),
                "selection_pp_max": float(np.max(sels)),
                "median_return_pct_mean": float(np.mean(meds)),
                "ticker_ci_lo_min": float(np.min(tick_los)),
                "ticker_ci_lo_max": float(np.max(tick_los)),
                "date_ci_lo_min": float(np.min(date_los)),
                "seeds_with_positive_selection": int(sum(s > 0 for s in sels)),
                "per_seed": per_seed,
            }
            # the original alphabetical tie-break, for comparison
            alpha_sub = pool.sort_values(["scan_date", "ticker"]).groupby("scan_date", observed=True, sort=False).head(n)
            results["alphabetical_subsets"].setdefault(split, {})[str(n)] = _metrics(alpha_sub, uni, horizon, with_controls=False)
            if verbose:
                s = results["subsets"][split][str(n)]
                a = results["alphabetical_subsets"][split][str(n)]
                print(f"  N={n:3d} [{split}] random tie-break sel {s['selection_pp_min']:+.2f}..{s['selection_pp_max']:+.2f} "
                      f"(mean {s['selection_pp_mean']:+.2f}, {s['seeds_with_positive_selection']}/{len(seeds)} seeds +) "
                      f"| alphabetical {a['selection_pp']:+.2f}")

    payload = {
        "kind": "AGENT_LAB_TIEBREAK_ROBUSTNESS",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "horizon_days": horizon,
        "finding": (
            "Ranking 12-1 momentum within volatility x liquidity cells leaves ~99 names per date "
            "tied at the top. Any 'top N' below that is an arbitrary subset of one pool, so the "
            "N-sweep for this method measures sampling noise, not concentration."
        ),
        "results": results,
        **RESEARCH_ONLY_HEADER,
    }
    _write_json(os.path.join(INTERMEDIATE_DIR, "tiebreak_robustness.json"), payload)
    return payload


if __name__ == "__main__":
    run()

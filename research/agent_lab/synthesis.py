"""Synthesis stage — the checks that need the whole window, and the artifact.

Sandbox-only. Zero provider calls.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from research.agent_lab import preregistration as PRE
from research.agent_lab.lab_stats import date_clustered_ci
from research.agent_lab.liquid_concentration_lab import (
    HORIZONS,
    INTERMEDIATE_DIR,
    LIST_SIZES,
    METHODS,
    NON_OVERLAP_STRIDE,
    OUT_DIR,
    RESEARCH_ONLY_HEADER,
    UNIVERSES,
    _select_top_n,
    _write_json,
    build_universe,
    load_panel,
)

_METHOD_BY_NAME = {m.name: m for m in METHODS}


def non_overlap_all_offsets(universe: pd.DataFrame, method, n: int, horizon: int, split: str) -> dict:
    """Non-overlapping-window selection at every phase offset.

    One offset over a 53-date holdout leaves 5 observations, which is not a
    test. Rotating through all 13 offsets gives 13 estimates, each built from
    genuinely non-overlapping 60-day windows, and reports how many are
    positive rather than resting on whichever phase happened to be picked.
    """
    u = universe[universe["split"] == split]
    if u.empty:
        return {}
    stride = NON_OVERLAP_STRIDE[horizon]
    dates = np.sort(u["scan_date"].unique())
    ret = f"fwd_{horizon}d"
    score, eligible = method.fn(u)
    cohort = _select_top_n(u, score, eligible, n)
    if cohort.empty:
        return {}
    c_per = cohort.groupby("scan_date", observed=True)[ret].mean()
    u_per = u.groupby("scan_date", observed=True)[ret].mean()
    sel = (c_per - u_per).dropna()

    offsets = []
    for off in range(stride):
        keep = set(dates[off::stride])
        s = sel[sel.index.isin(keep)]
        if s.size < 2:
            continue
        offsets.append({"offset": off, "dates": int(s.size), "selection_pp": float(s.mean())})
    if not offsets:
        return {}
    vals = np.array([o["selection_pp"] for o in offsets])
    return {
        "stride_weeks": stride,
        "offsets_tested": len(offsets),
        "dates_per_offset": int(np.median([o["dates"] for o in offsets])),
        "mean_across_offsets_pp": float(vals.mean()),
        "min_offset_pp": float(vals.min()),
        "max_offset_pp": float(vals.max()),
        "pct_offsets_positive": float((vals > 0).mean() * 100.0),
        "ci_across_offsets": date_clustered_ci(vals.tolist()),
        "by_offset": offsets,
    }


def run_synthesis(verbose: bool = True) -> dict:
    df = load_panel(verbose=verbose)
    universes = {u.name: build_universe(df, u, verbose=verbose) for u in UNIVERSES}

    cells = [dict(PRE.PRIMARY, role="primary")]
    cells += [dict(c, role="secondary") for c in PRE.SECONDARY]
    cells += [dict(method=b, universe=PRE.PRIMARY["universe"], list_size=PRE.PRIMARY["list_size"],
                   horizon_days=PRE.PRIMARY["horizon_days"], role="baseline") for b in PRE.BASELINES]

    rows = []
    for c in cells:
        m = _METHOD_BY_NAME[c["method"]]
        uni = universes[c["universe"]]
        entry = {"cell": {k: v for k, v in c.items() if k != "role"}, "role": c["role"]}
        for split in ("train", "holdout"):
            entry[split] = non_overlap_all_offsets(uni, m, c["list_size"], c["horizon_days"], split)
        rows.append(entry)
        if verbose and entry["train"] and entry["holdout"]:
            t, h = entry["train"], entry["holdout"]
            print(f"[{c['role']:9s}] {c['method']:24s} N={c['list_size']:3d} h={c['horizon_days']}d  "
                  f"train {t['mean_across_offsets_pp']:+6.2f}pp ({t['pct_offsets_positive']:.0f}% offsets +)  "
                  f"holdout {h['mean_across_offsets_pp']:+6.2f}pp ({h['pct_offsets_positive']:.0f}% offsets +)")

    payload = {
        "kind": "AGENT_LAB_NON_OVERLAPPING_WINDOWS",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "method_note": (
            "Every phase offset is rotated so the result does not depend on which week the "
            "non-overlapping sample happened to start on. Each offset uses windows that do not "
            "overlap in time; offsets do overlap with each other, so the spread across offsets "
            "measures phase sensitivity, not independent replication."
        ),
        "rows": rows,
        **RESEARCH_ONLY_HEADER,
    }
    _write_json(os.path.join(INTERMEDIATE_DIR, "non_overlapping_windows.json"), payload)
    return payload


if __name__ == "__main__":
    run_synthesis()

"""Holdout stage — the 2025 year is read here, once, against a frozen bar.

Sandbox-only. Reads cached replay panels; zero provider calls.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from research.agent_lab import preregistration as PRE
from research.agent_lab.lab_stats import ci_positive
from research.agent_lab.liquid_concentration_lab import (
    HORIZONS,
    INTERMEDIATE_DIR,
    LIST_SIZES,
    METHODS,
    OUT_DIR,
    RESEARCH_ONLY_HEADER,
    UNIVERSES,
    _write_json,
    build_universe,
    by_month_leaveout,
    by_ticker_leaveout,
    by_year,
    evaluate,
    fingerprint_symmetry,
    load_panel,
)

_METHOD_BY_NAME = {m.name: m for m in METHODS}
_UNIVERSE_BY_NAME = {u.name: u for u in UNIVERSES}


def _cell_key(method: str, universe: str, n: int, h: int, split: str, non_overlap: bool) -> str:
    return f"{universe}|{method}|N{n}|h{h}|{split}|{'nonoverlap' if non_overlap else 'all'}"


def score_bar(primary: dict, train: dict, holdout: dict, train_no: dict, hold_no: dict,
              month_tr: dict, month_ho: dict, tick_tr: dict, tick_ho: dict) -> dict:
    """Mechanically evaluate the frozen bar. No judgement calls in here."""
    c = holdout.get("controls", {})
    checks = {
        "holdout_selection_positive": holdout.get("selection_vs_universe_mean_pp", -1) > 0,
        "non_overlapping_positive": (train_no.get("selection_vs_universe_mean_pp", -1) > 0
                                     and hold_no.get("selection_vs_universe_mean_pp", -1) > 0),
        "mean_and_median_positive": (holdout.get("mean_return_pct", -1) > 0
                                     and holdout.get("median_return_pct", -1) > 0),
        "beats_same_date_random": ci_positive(c.get("vs_random_same_date", {})),
        "beats_matched_random": ci_positive(c.get("vs_matched_random", {})),
        "date_clustered_ci_positive": ci_positive(holdout.get("portfolio_edge_ci", {})),
        "ticker_clustered_ci_positive": ci_positive(holdout.get("typical_name_edge_ci", {})),
        "not_one_month": (month_tr.get("still_positive_without_worst_month", False)
                          and month_ho.get("still_positive_without_worst_month", False)),
        "not_one_ticker": (tick_tr.get("selection_without_top_tickers_pp", -1) > 0
                           and tick_ho.get("selection_without_top_tickers_pp", -1) > 0),
        "not_one_sector": holdout.get("conc_largest_sector_share_pct", 100.0) <= 40.0,
        "enough_observations": (holdout.get("dates", 0) >= 40
                                and (train.get("unique_tickers", 0) + holdout.get("unique_tickers", 0)) >= 100),
        "no_lookahead": True,  # enforced at load time by assert_no_lookahead
    }
    failed = sorted(k for k, v in checks.items() if not v)

    train_sel = train.get("selection_vs_universe_mean_pp", 0.0)
    hold_sel = holdout.get("selection_vs_universe_mean_pp", 0.0)
    both_ci = checks["date_clustered_ci_positive"] and checks["ticker_clustered_ci_positive"]
    fragility_ok = checks["not_one_month"] and checks["not_one_ticker"] and checks["not_one_sector"]

    if not failed:
        verdict = "VALIDATED_EDGE_CANDIDATE"
    elif hold_sel > 0 and both_ci and checks["beats_matched_random"] and fragility_ok:
        verdict = "PROVISIONAL_RESEARCH_CANDIDATE"
    elif hold_sel > 0 and checks["beats_matched_random"]:
        verdict = "PROMISING_BUT_UNPROVEN"
    elif train_sel > 0 and hold_sel <= 0:
        verdict = "OVERFIT"
    elif hold_sel > 0:
        verdict = "INCONCLUSIVE"
    else:
        verdict = "REJECTED"

    return {"checks": checks, "failed_checks": failed, "verdict": verdict,
            "train_selection_pp": train_sel, "holdout_selection_pp": hold_sel}


def run_holdout(verbose: bool = True) -> dict:
    df = load_panel(verbose=verbose)
    universes = {u.name: build_universe(df, u, verbose=verbose) for u in UNIVERSES}

    # Full grid on the holdout year, so every train cell has a matching read.
    grid: list[dict] = []
    for uname, uni in universes.items():
        for m in METHODS:
            for n in LIST_SIZES:
                for h in HORIZONS:
                    r = evaluate(uni, m, n, h, split="holdout")
                    r["universe"] = uname
                    grid.append(r)

    cells = [PRE.PRIMARY] + list(PRE.SECONDARY)
    for b in PRE.BASELINES:
        cells.append({"method": b, "universe": PRE.PRIMARY["universe"],
                      "list_size": PRE.PRIMARY["list_size"], "horizon_days": PRE.PRIMARY["horizon_days"],
                      "baseline": True})

    scored: list[dict] = []
    for i, cell in enumerate(cells):
        m = _METHOD_BY_NAME[cell["method"]]
        uni = universes[cell["universe"]]
        n, h = cell["list_size"], cell["horizon_days"]
        train = evaluate(uni, m, n, h, split="train")
        hold = evaluate(uni, m, n, h, split="holdout")
        train_no = evaluate(uni, m, n, h, split="train", non_overlapping=True, with_controls=False)
        hold_no = evaluate(uni, m, n, h, split="holdout", non_overlapping=True, with_controls=False)
        month_tr = by_month_leaveout(uni, m, n, h, "train")
        month_ho = by_month_leaveout(uni, m, n, h, "holdout")
        tick_tr = by_ticker_leaveout(uni, m, n, h, "train")
        tick_ho = by_ticker_leaveout(uni, m, n, h, "holdout")
        bar = score_bar(cell, train, hold, train_no, hold_no, month_tr, month_ho, tick_tr, tick_ho)
        entry = {
            "cell": cell,
            "role": "primary" if i == 0 else ("baseline" if cell.get("baseline") else "secondary"),
            "train": train, "holdout": hold,
            "train_non_overlapping": train_no, "holdout_non_overlapping": hold_no,
            "by_year": by_year(uni, m, n, h),
            "leave_one_month_out": {"train": month_tr, "holdout": month_ho},
            "leave_top_tickers_out": {"train": tick_tr, "holdout": tick_ho},
            **bar,
        }
        scored.append(entry)
        if verbose:
            print(f"[{entry['role']:9s}] {cell['method']:24s} {cell['universe']:16s} N={n:3d} h={h}d  "
                  f"train={bar['train_selection_pp']:+6.2f}  holdout={bar['holdout_selection_pp']:+6.2f}  "
                  f"-> {bar['verdict']}  failed={bar['failed_checks']}")

    symmetry = []
    for uname, uni in universes.items():
        for h in HORIZONS:
            symmetry.append({"universe": uname, **fingerprint_symmetry(uni, h, "holdout")})

    payload = {
        "kind": "AGENT_LAB_HOLDOUT_RESULTS",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "preregistered_at": PRE.PREREGISTERED_AT,
        "bar": PRE.BAR,
        "ladder": list(PRE.LADDER),
        "multiplicity": PRE.MULTIPLICITY,
        "scored_cells": scored,
        "holdout_grid": grid,
        "fingerprint_symmetry_holdout": symmetry,
        **RESEARCH_ONLY_HEADER,
    }
    _write_json(os.path.join(INTERMEDIATE_DIR, "holdout_results.json"), payload)
    return payload

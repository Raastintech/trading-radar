"""Assemble the single published artifact for the Autonomous Alpha Lab.

Sandbox-only. Reads the stage intermediates and writes
``cache/research/agent_lab/autonomous_alpha_lab_latest.json``.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from research.agent_lab import preregistration as PRE
from research.agent_lab.liquid_concentration_lab import (
    INTERMEDIATE_DIR,
    OUT_DIR,
    RESEARCH_ONLY_HEADER,
    _write_json,
)


def _load(name: str) -> dict:
    with open(os.path.join(INTERMEDIATE_DIR, name)) as fh:
        return json.load(fh)


def _slim(x: dict, keys: tuple[str, ...]) -> dict:
    return {k: x.get(k) for k in keys if k in x}


SLIM = (
    "dates", "rows", "unique_tickers", "median_names_per_date",
    "mean_return_pct", "median_return_pct", "win_rate_pct", "dates_won_pct",
    "selection_vs_universe_mean_pp", "selection_vs_universe_median_pp",
    "excess_vs_spy_pp", "excess_vs_qqq_pp", "excess_vs_iwm_pp",
    "portfolio_edge_ci", "typical_name_edge_ci", "controls",
    "pct_down_over_25", "p05_return_pct", "worst_return_pct", "worst_date_selection_pp",
    "median_dvol_usd", "p10_dvol_usd", "median_price_usd", "pct_in_score_100_trap",
    "conc_largest_sector", "conc_largest_sector_share_pct", "conc_top10_ticker_share_pct",
)


def build() -> dict:
    train = _load("train_results.json")
    hold = _load("holdout_results.json")
    nonov = _load("non_overlapping_windows.json")
    tie = _load("tiebreak_robustness.json")
    pool = _load("pool_full_bar.json")

    primary = next(c for c in hold["scored_cells"] if c["role"] == "primary")
    best = pool["liquid_tradable|h60"]

    payload = {
        "kind": "AUTONOMOUS_ALPHA_LAB_LATEST",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "report": "docs/research/agent_lab/AUTONOMOUS_ALPHA_LAB_REPORT_2026_09.md",
        "window": {
            "replay_dates": 209, "weekly": True,
            "train_years": list(PRE.TRAIN_YEARS if hasattr(PRE, "TRAIN_YEARS") else (2022, 2023, 2024)),
            "holdout_year": 2025,
            "forward_2026_used": False,
        },

        "executive_verdict": {
            "preregistered_primary": {
                "cell": primary["cell"],
                "verdict": primary["verdict"],
                "train_selection_pp": primary["train_selection_pp"],
                "holdout_selection_pp": primary["holdout_selection_pp"],
                "failed_checks": primary["failed_checks"],
                "reading": (
                    "The pre-registered hypothesis — that a concentrated 5-15 name list from a "
                    "manually tradable universe would beat the controls — FAILED. Train +4.79pp "
                    "collapsed to +0.39pp out of sample, and only 46% of non-overlapping window "
                    "phases were positive in the holdout."
                ),
            },
            "best_object_found": {
                "name": "style_cell_leader_pool",
                "definition": (
                    "In a universe floored at price >= $5 and median 20-day dollar volume >= $20M, "
                    "take the single highest 12-1 momentum name in each volatility-decile x "
                    "liquidity-decile cell. About 99 names per week. Equal weight, unordered, "
                    "held 60 sessions."
                ),
                "verdict": "PROVISIONAL_RESEARCH_CANDIDATE",
                "why_not_validated": (
                    "Every measurable item on the frozen bar passes, but the object itself was "
                    "specified AFTER the 2025 holdout had been read, so the out-of-sample year is "
                    "no longer out-of-sample for it. It needs a fresh pre-registration and forward "
                    "measurement before any stronger label is defensible."
                ),
                "mechanically_scored_bar": best["bar"]["verdict"],
                "train": _slim(best["train"], SLIM),
                "holdout": _slim(best["holdout"], SLIM),
                "by_year_selection_pp": {k: (v or {}).get("selection_pp") for k, v in best["by_year"].items()},
                "non_overlapping": {
                    s: {kk: best["nonoverlap"][s][kk] for kk in
                        ("stride_weeks", "offsets_tested", "mean_across_offsets_pp", "pct_offsets_positive", "min_offset_pp")}
                    for s in ("train", "holdout")
                },
                "leave_one_month_out": best["lomo"],
                "leave_top_tickers_out": best["loto"],
                "horizon_20d_verdict": pool["liquid_tradable|h20"]["bar"]["verdict"],
                "sensitivity_strict_universe_verdict": pool["strict_tradable|h60"]["bar"]["verdict"],
                "sensitivity_full_universe_verdict": pool["full_prior_work|h60"]["bar"]["verdict"],
            },
        },

        "hypotheses": {
            "H1_liquidity_fixes_per_name_edge": {
                "verdict": "PARTIALLY_SUPPORTED",
                "detail": (
                    "The liquidity floor alone does not rescue a concentrated list, but it is "
                    "required for the pool's per-name claim: the same pool on the prior-work "
                    "universe fails its ticker-clustered CI in the holdout "
                    f"({pool['full_prior_work|h60']['holdout']['typical_name_edge_ci']['lo']:+.2f} lower bound) "
                    "and passes on the liquid one."
                ),
            },
            "H2_winner_fingerprint_stops_being_volatility": {
                "verdict": "REJECTED",
                "detail": (
                    "Winner/loser feature-AUC correlation stays between +0.925 and +0.991 in every "
                    "universe and both splits. Volatility separates BOTH tails equally everywhere. "
                    "Prior work's central negative is not an artifact of junk names."
                ),
                "new_observation": (
                    "The winner-minus-loser AUC gap for trend and momentum features grows as the "
                    "universe gets more liquid (+0.049 full, +0.099 liquid, +0.124 strict), while "
                    "the gap for volatility features stays at zero. That asymmetry is what the "
                    "surviving method exploits."
                ),
            },
            "H3_concentration_to_5_15_names": {
                "verdict": "REJECTED",
                "detail": (
                    "No concentrated list survived. The pre-registered trend_anti_junk collapsed "
                    "out of sample at every size from 5 to 25. Raw 12-1 momentum at N=10 returned "
                    "+13.76pp selection in the holdout but was 51.6% Technology with the top ten "
                    "tickers making up 53% of episodes and 24.5% of names down over 25% — a "
                    "single-theme ride, caught by the sector check."
                ),
            },
            "H4_secondary_confirms_add_value": {
                "verdict": "REJECTED",
                "detail": (
                    "Both fundamental-quality and post-earnings-reaction overlays REDUCED selection "
                    "versus the same rule without them, in both splits. This replicates the M1 memo's "
                    "finding that the earnings-beat screen was worth -0.30."
                ),
            },
            "H5_replicates_on_non_overlapping_windows": {
                "verdict": "SUPPORTED_FOR_THE_POOL_ONLY",
                "detail": (
                    "The pool is positive on 100% of non-overlapping phase offsets in train and 92% "
                    "in the holdout. Every concentrated candidate fell below 62%."
                ),
            },
        },

        "tie_break_audit": {
            "finding": tie["finding"],
            "why_it_mattered": (
                "The first implementation broke ties alphabetically, which flattered the holdout "
                "result by roughly 1.5pp (top-25 selection +4.49 alphabetical vs +2.94 mean across "
                "five random tie-breaks). The correction reduced the headline number; it was not "
                "a search for a better one."
            ),
            "random_tiebreak_holdout": tie["results"]["subsets"]["holdout"],
            "alphabetical_holdout": {k: {kk: v[kk] for kk in ("selection_pp", "median_return_pct")}
                                     for k, v in tie["results"]["alphabetical_subsets"]["holdout"].items()},
        },

        "baselines_at_the_preregistered_cell": [
            {"method": c["cell"]["method"], "verdict": c["verdict"],
             "train_selection_pp": c["train_selection_pp"], "holdout_selection_pp": c["holdout_selection_pp"],
             "failed_checks": c["failed_checks"]}
            for c in hold["scored_cells"] if c["role"] == "baseline"
        ],

        "cost": {
            "provider_calls_made_by_this_study": 0,
            "liquid_universe_names": 1949,
            "share_of_them_with_252_bars_in_live_cache_pct": 58.9,
            "share_covered_by_replay_cache_pct": 100.0,
            "replay_cache_symbols_stale_before_2026_09_01": 2347,
            "estimated_calls_for_a_daily_refresh_per_month": 42000,
            "recent_monthly_fmp_usage": {"2026-07": 107739, "2026-08": 88157, "2026-09_to_date": 56284},
            "note": (
                "12-1 momentum needs 252 bars per name. Only 58.9% of the liquid universe has that "
                "depth in the live cache; the replay cache has all of it but is maintained by a "
                "research backfill script, not a timer, and 2,347 of its symbols are already stale. "
                "A daily refresh of ~2,000 names is roughly 42,000 calls a month on top of the "
                "existing cycle."
            ),
        },

        "prohibitions_observed": [
            "no production scanner logic touched",
            "no M1 membership logic touched",
            "no frozen M1 manual-pick cohort touched",
            "no HC/EO/Alpha Focus routing touched",
            "no dashboard, MCP, cron, systemd or live-ledger change",
            "no scheduled job created",
            "zero provider/API calls",
            "no 2026 forward or live artifact used for tuning",
            "nothing committed",
            "no buy/sell/trade recommendation, price target, ranking or top-pick list emitted",
        ],
        "preregistration": {
            "frozen_at": PRE.PREREGISTERED_AT,
            "primary": PRE.PRIMARY, "secondary": PRE.SECONDARY, "baselines": list(PRE.BASELINES),
            "bar": PRE.BAR, "ladder": list(PRE.LADDER), "multiplicity": PRE.MULTIPLICITY,
        },
        "artifacts": {
            "train": "cache/research/agent_lab/intermediates/train_results.json",
            "holdout": "cache/research/agent_lab/intermediates/holdout_results.json",
            "non_overlapping": "cache/research/agent_lab/intermediates/non_overlapping_windows.json",
            "tiebreak": "cache/research/agent_lab/intermediates/tiebreak_robustness.json",
            "pool_full_bar": "cache/research/agent_lab/intermediates/pool_full_bar.json",
            "style_neutral_exploratory": "cache/research/agent_lab/intermediates/style_neutral_exploratory.json",
        },
        **RESEARCH_ONLY_HEADER,
    }
    _write_json(os.path.join(OUT_DIR, "autonomous_alpha_lab_latest.json"), payload)
    return payload


if __name__ == "__main__":
    build()

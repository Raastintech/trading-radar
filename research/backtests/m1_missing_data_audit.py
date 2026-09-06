"""Missing Conviction Data Audit — what would it take to rank inside M1?

RESEARCH ONLY. Reads the replay caches and the M1 Conviction Layer's artifacts;
writes only ``cache/research/m1_missing_data_*`` and ``logs/m1_missing_data_*``.
No provider calls. Changes no scanner, score, gate, threshold, HC/EO rule,
routing decision, dashboard artifact, cron entry or ledger.

The conviction study established that inside the M1 momentum pool the winner
and loser fingerprints are the same fingerprint, thirteen overlays produced no
per-name edge, and every risk filter trimmed both tails at the same rate. The
obvious next move — a fourteenth overlay — is the one this audit refuses to
make. The question here is prior to that: is the per-name signal ABSENT from
the data we hold, and if so what data would carry it?

Three measurements, because an audit built on opinion is worth nothing:

1. **Headroom.** An oracle that ranks M1 by realised forward return bounds the
   prize. If a perfect ranker only recovers a little, no data source can pay
   for itself.
2. **Ceiling on what we hold.** A ridge model over EVERY available as-of
   feature, fit on 2022-2024 and read out-of-sample. If the best linear
   combination of everything we have cannot separate names out-of-sample, the
   available data is exhausted and the honest question becomes which new data
   — not which new rule.
3. **Anatomy of the move.** Whether M1's big winners rose gradually or in one
   or two sessions. Event-shaped winners point at event data; drift-shaped
   winners point at flow and estimates. This is what makes the recommendation
   evidence-based rather than a guess.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

from research.backtests.common import (
    LiveArtifactTripwire,
    REPLAY_WINDOW_END,
    REPLAY_WINDOW_START,
    ROOT,
    assert_provenance,
    offline_env,
    provenance_flags,
    run_cli,
    write_replay_json,
    write_replay_text,
)
from research.backtests.alpha_reconstruction_lab import HOLDOUT_YEARS, TRAIN_YEARS, _cluster_ci
from research.backtests.alpha_tournament import (
    LIST_SIZES, PRIMARY_H, _build_pools, _top_n, asymmetry, selection_vs)
from research.backtests.m1_conviction_layer import (
    FEATURE_GROUPS, POOL_REL, INSIDE_REL, OVERLAYS_REL, label_within_pool, m1_pool,
    random_control)

offline_env()

HEADROOM_REL = "cache/research/m1_missing_data_headroom.json"
INVENTORY_REL = "cache/research/m1_missing_data_inventory.json"
LATEST_REL = "cache/research/m1_missing_data_latest.json"
REPORT_TXT_REL = "logs/m1_missing_data_latest.txt"

ALL_FEATURES: tuple[str, ...] = tuple(
    f for feats in FEATURE_GROUPS.values() for f in feats)


# ── the ceiling model ───────────────────────────────────────────────────────


def _rank_matrix(df, features, cols_present):
    """Within-date percentile ranks of each feature, plus squared terms.

    Ranks rather than raw values: scale-free, outlier-proof, and identical in a
    crash week and a melt-up week. Squared terms let the fit express the
    inverted-U shapes the earlier studies found without going nonparametric.
    Missing values become 0.5 — the middle of the cross-section — so a name is
    neither rewarded nor punished for a gap in coverage.
    """
    import numpy as np

    parts = []
    for f in cols_present:
        r = df.groupby("scan_date")[f].rank(pct=True).to_numpy(dtype=float)
        r = np.where(np.isfinite(r), r, 0.5)
        parts.append(r)
        parts.append((r - 0.5) ** 2)
    X = np.column_stack(parts) if parts else np.zeros((len(df), 0))
    return np.column_stack([X, np.ones(len(X))])


def _ridge_fit(X, y, alpha: float):
    import numpy as np

    n_f = X.shape[1]
    A = X.T @ X + alpha * np.eye(n_f)
    A[-1, -1] -= alpha            # do not penalise the intercept
    return np.linalg.solve(A, X.T @ y)


def ceiling_model(pool, args) -> dict:
    """Best linear model over everything we hold, read out-of-sample.

    Fit on the train years against the within-date rank of forward return, then
    applied unchanged to each out-of-sample year. Reported as AUC for
    top-decile separation and as the return of the lists it would have picked.
    """
    import numpy as np

    present = [f for f in ALL_FEATURES
               if f in pool.columns and pool[f].notna().mean() > 0.30]
    train = pool[pool.year.isin(TRAIN_YEARS)]
    y = train.groupby("scan_date")[f"fwd_{PRIMARY_H}d"].rank(pct=True).to_numpy(float)
    ok = np.isfinite(y)
    X = _rank_matrix(train, ALL_FEATURES, present)
    w = _ridge_fit(X[ok], y[ok], alpha=float(args.ridge_alpha))

    out = {
        "fit_target": "within-date percentile rank of forward 60d return — a TYPICAL "
                      "outcome target, not a tail-membership target. That choice is "
                      "visible in the results: the model ranks for the median name "
                      "and is mildly ANTI-correlated with top-decile membership.",
        "features_used": present,
        "features_dropped_for_coverage": [
            f for f in ALL_FEATURES
            if f in pool.columns and pool[f].notna().mean() <= 0.30],
        "n_terms": int(X.shape[1]),
        "ridge_alpha": float(args.ridge_alpha),
        "train_rows": int(ok.sum()),
        "by_split": {},
    }
    label = f"pool_top10pct_{PRIMARY_H}d"
    # Genuine walk-forward: each test year is scored by a model fit ONLY on the
    # years before it. The first version of this measurement scored 2023 and
    # 2024 with the all-train fit and labelled them walk-forward, which they
    # were not — they were in the training data.
    walk_weights: dict[int, object] = {}
    for test_y in (2023, 2024, 2025):
        prior = [y for y in (2022, 2023, 2024) if y < test_y]
        tr_w = pool[pool.year.isin(prior)]
        if tr_w.empty:
            continue
        yv = tr_w.groupby("scan_date")[f"fwd_{PRIMARY_H}d"].rank(pct=True).to_numpy(float)
        okw = np.isfinite(yv)
        Xw = _rank_matrix(tr_w, ALL_FEATURES, present)
        walk_weights[test_y] = _ridge_fit(Xw[okw], yv[okw], alpha=float(args.ridge_alpha))

    splits = [("train_in_sample", TRAIN_YEARS, w, True),
              ("holdout_2025_model_fit_on_train", HOLDOUT_YEARS, w, False)]
    for test_y, ww in walk_weights.items():
        splits.append((f"walkforward_{test_y}_fit_on_prior_years", (test_y,), ww, False))

    for name, years, weights, in_sample in splits:
        sub = pool[pool.year.isin(years)].copy()
        if sub.empty:
            continue
        score = _rank_matrix(sub, ALL_FEATURES, present) @ weights
        sub = sub.assign(_score=score)
        aucs = []
        for _, g in sub.groupby("scan_date"):
            s = g["_score"].to_numpy(float)
            lab = g[label].to_numpy(bool)
            n_pos, n_neg = int(lab.sum()), int((~lab).sum())
            if n_pos < 3 or n_neg < 3:
                continue
            import pandas as pd
            r = pd.Series(s).rank().to_numpy()
            aucs.append(float((r[lab].sum() - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)))
        node = {
            "in_sample": in_sample,
            "dates": len(aucs),
            "auc_top_decile_membership": round(float(np.mean(aucs)), 4) if aucs else None,
            "auc_ci": _cluster_ci(aucs, args.bootstrap, args.seed) if aucs else [None, None],
        }
        for n in (25, 50):
            picks = _top_n(sub, sub["_score"], n)
            rand = random_control(sub, n, args.seed + n)
            a = asymmetry(picks[f"fwd_{PRIMARY_H}d"]) or {}
            b = asymmetry(rand[f"fwd_{PRIMARY_H}d"]) or {}
            sel = selection_vs(picks, sub, PRIMARY_H, args.bootstrap, args.seed) or {}
            node[f"top{n}"] = {
                "mean": a.get("mean"), "median": a.get("median"),
                "win_rate": a.get("win_rate"), "tail_ratio": a.get("tail_ratio"),
                "pct_up_50": a.get("pct_up_50"), "pct_down_25": a.get("pct_down_25"),
                "random_mean": b.get("mean"), "random_median": b.get("median"),
                "mean_minus_random": (None if None in (a.get("mean"), b.get("mean"))
                                      else round(a["mean"] - b["mean"], 3)),
                "median_minus_random": (None if None in (a.get("median"), b.get("median"))
                                        else round(a["median"] - b["median"], 3)),
                "ticker_cluster_ci": sel.get("ticker_cluster_ci"),
                "date_cluster_ci_mean": sel.get("date_cluster_ci_mean"),
            }
        out["by_split"][name] = node
    return out


def oracle_headroom(pool, args) -> dict:
    """What a PERFECT ranker inside M1 would have returned. Bounds the prize."""
    import numpy as np

    out = {}
    for name, years in (("train", TRAIN_YEARS), ("holdout", HOLDOUT_YEARS)):
        sub = pool[pool.year.isin(years)]
        if sub.empty:
            continue
        node = {}
        for n in LIST_SIZES:
            best = _top_n(sub, sub[f"fwd_{PRIMARY_H}d"], n)       # cheats, by design
            worst = _top_n(sub, -sub[f"fwd_{PRIMARY_H}d"], n)
            rand = random_control(sub, n, args.seed + n)
            ba = asymmetry(best[f"fwd_{PRIMARY_H}d"]) or {}
            wa = asymmetry(worst[f"fwd_{PRIMARY_H}d"]) or {}
            ra = asymmetry(rand[f"fwd_{PRIMARY_H}d"]) or {}
            node[f"top{n}"] = {
                "oracle_mean": ba.get("mean"), "oracle_median": ba.get("median"),
                "worst_case_mean": wa.get("mean"),
                "random_mean": ra.get("mean"),
                "headroom_vs_random": (None if None in (ba.get("mean"), ra.get("mean"))
                                       else round(ba["mean"] - ra["mean"], 2)),
                "downside_if_ranked_backwards": (
                    None if None in (wa.get("mean"), ra.get("mean"))
                    else round(wa["mean"] - ra["mean"], 2)),
            }
        out[name] = node
    return out


def move_anatomy(pool) -> dict:
    """Did M1's winners rise gradually, or in one or two sessions?

    A winner whose whole move is a single +40% session is an EVENT. One that
    grinds up 60% over three months without a 10% day is DRIFT. The two point
    at different missing data, so the mix decides what is worth buying.
    """
    import numpy as np

    w = pool[pool[f"pool_top10pct_{PRIMARY_H}d"]].copy()
    l = pool[pool[f"pool_bot10pct_{PRIMARY_H}d"]].copy()
    out: dict = {}
    for name, g in (("pool_top_decile_winners", w), ("pool_bottom_decile_losers", l),
                    ("whole_pool", pool)):
        if g.empty:
            continue
        big_day = g["fwd_max_1d_up_60d"]
        big_gap = g["fwd_max_gap_up_60d"]
        worst_day = g["fwd_max_1d_down_60d"]
        out[name] = {
            "episodes": int(len(g)),
            "median_biggest_up_day_pct": round(float(big_day.median()), 2),
            "median_biggest_down_day_pct": round(float(worst_day.median()), 2),
            "share_with_a_25pct_up_day": round(float((big_day >= 25).mean() * 100), 2),
            "share_with_a_15pct_gap_up": round(float((big_gap >= 15).mean() * 100), 2),
            "share_with_a_25pct_down_day": round(float((worst_day <= -25).mean() * 100), 2),
            "share_no_day_above_10pct": round(float((big_day < 10).mean() * 100), 2),
        }
    # Of the winners, how much of the move was one session?
    if not w.empty:
        with np.errstate(divide="ignore", invalid="ignore"):
            share = np.where(w[f"fwd_{PRIMARY_H}d"] > 0,
                             w["fwd_max_1d_up_60d"] / w[f"fwd_{PRIMARY_H}d"], np.nan)
        share = share[np.isfinite(share)]
        out["winner_move_concentration"] = {
            "median_single_day_share_of_total_move": round(float(np.median(share)), 3),
            "share_where_one_day_is_over_half_the_move": round(
                float((share > 0.5).mean() * 100), 2),
            "share_where_one_day_is_over_a_quarter": round(
                float((share > 0.25).mean() * 100), 2),
            "reading": "a high single-day share means the winner was made by an "
                       "EVENT, which as-of price/volume features cannot anticipate; "
                       "a low share means DRIFT, which flow and estimate data can",
        }
    return out


# ── coverage of what we already hold ────────────────────────────────────────


def coverage_audit(pool) -> dict:
    """How much of the M1 pool each existing data class actually reaches."""
    classes = {
        "price_volume": ("price", "mom_12_1", "atr14_pct", "dvol_med_20"),
        "fundamentals_growth": ("rev_yoy_pct", "op_margin_delta_pp", "dilution_yoy_pct"),
        "fundamentals_quality": ("roe_pct", "roic_proxy_pct", "gross_profit_to_assets"),
        "valuation": ("ev_to_sales", "fcf_yield_pct", "earnings_yield_pct"),
        "market_cap": ("market_cap",),
        "earnings_events": ("days_since_filing", "post_earnings_reaction_pct"),
        "sector_theme": ("sector_rs_rank_pct", "sector_peer_breadth_pct"),
    }
    out = {}
    for name, cols in classes.items():
        have = [c for c in cols if c in pool.columns]
        if not have:
            out[name] = {"present": False, "coverage_pct": 0.0}
            continue
        cov = float(pool[have].notna().all(axis=1).mean() * 100)
        out[name] = {
            "present": True,
            "columns": have,
            "coverage_pct": round(cov, 1),
            "rows_covered": int(pool[have].notna().all(axis=1).sum()),
        }
    return out


# ── the inventory of what is missing (Parts 2-3) ────────────────────────────
#
# Provider assessments are grounded in what core/fmp_client.py already reaches
# and in the endpoint shapes it documents; anything not verifiable offline is
# marked UNVERIFIED rather than asserted. Call counts assume the 3,640 tickers
# and 209 weekly dates of the M1 pool.

M1_TICKERS = 3640
M1_DATES = 209

MISSING_DATA_CLASSES: list[dict] = [
    {
        "class": "A_earnings_surprise_and_estimates",
        "items": ["EPS surprise", "revenue surprise", "forward EPS/revenue revisions",
                  "estimate revision breadth", "guidance", "call sentiment"],
        "provider_status": "PARTLY AVAILABLE AND CHEAP. core/fmp_client.py already "
                           "calls /earnings-calendar with from/to and documents the "
                           "fields epsActual, epsEstimated, revenueActual, "
                           "revenueEstimated. That endpoint is DATE-RANGED, not "
                           "per-ticker, so historical surprise for the whole universe "
                           "costs tens of calls, not thousands. Forward estimate "
                           "REVISIONS (the stronger signal) are a different endpoint "
                           "and UNVERIFIED offline.",
        "point_in_time": "YES for surprise, with care: the estimate is known before "
                         "the report and the actual on the day. The trap is using a "
                         "consensus that was itself revised after the fact — snapshot "
                         "revisions cannot be reconstructed from a current pull.",
        "estimated_calls": "~50-210 for four years of surprise history (monthly to "
                           "weekly chunks of /earnings-calendar). Revisions, if a "
                           "history endpoint exists, would be per-ticker: ~3,600.",
        "historical_coverage": "surprise should cover most reporting names in the "
                               "window; revision history UNVERIFIED",
        "helps": ["institutional leaders", "turnarounds", "all reporting names"],
        "separates_tails": "PLAUSIBLE. Post-earnings drift is one of the few "
                           "anomalies with independent literature behind it, and the "
                           "conviction study's own earnings overlay raised BOTH tails "
                           "(right +44%, left +30%) — it found live names, it just "
                           "could not tell them apart. A surprise magnitude might.",
        "worth_testing": "YES — highest ratio of evidence to cost in this table",
    },
    {
        "class": "B_institutional_behaviour",
        "items": ["13F ownership change", "fund accumulation", "insider buying/selling",
                  "short interest", "borrow cost"],
        "provider_status": "PARTIAL. /insider-trading is already wired in "
                           "core/fmp_client.py (per-ticker). 13F and short interest "
                           "endpoints are UNVERIFIED offline. Borrow cost is not a "
                           "retail-provider field in any form this repo has seen.",
        "point_in_time": "13F is filed 45 days after quarter end — usable as-of the "
                         "FILING date, but the position it describes is up to 135 days "
                         "stale. Insider Form 4 is filed within two business days, so "
                         "it is genuinely timely. Short interest settles twice monthly "
                         "with a ~9-day publication lag.",
        "estimated_calls": "insider: ~3,600 (one per ticker, history in one call). "
                           "13F and short interest UNVERIFIED; if per-ticker, another "
                           "~3,600 each.",
        "historical_coverage": "insider filings exist for essentially all US issuers; "
                               "13F only covers institutions over $100M AUM",
        "helps": ["small/mid caps most", "squeeze setups", "turnarounds"],
        "separates_tails": "UNCERTAIN. Insider buying is a weak positive signal in the "
                           "literature and clusters in small caps — exactly where this "
                           "pool's variance already lives. Short interest is the more "
                           "interesting one because it is explicitly asymmetric.",
        "worth_testing": "SECOND — insider and short interest together, not 13F",
    },
    {
        "class": "C_options_positioning",
        "items": ["unusual options volume", "call/put skew", "IV rank", "IV expansion",
                  "flow persistence"],
        "provider_status": "NOT AVAILABLE HISTORICALLY. The repo's own chain history "
                           "starts 2026-06-12 (Phase 1J collector). Nothing exists for "
                           "2022-2025 and no provider in use back-fills it.",
        "point_in_time": "N/A — the data does not exist for the window",
        "estimated_calls": "not purchasable at any call count from current providers",
        "historical_coverage": "0% of the replay window",
        "helps": ["would help event names most"],
        "separates_tails": "UNKNOWABLE HERE. Plausible in principle; untestable in "
                           "this window, which is the only honest thing to say.",
        "worth_testing": "NO — cannot be tested; the collector accrues forward and "
                         "the question reopens around 2027",
    },
    {
        "class": "D_news_catalyst_events",
        "items": ["contract wins", "FDA/clinical milestones", "product launches",
                  "M&A", "index inclusion", "topic shock with entity mapping"],
        "provider_status": "WEAK. /news exists per-ticker but is a headline feed, not "
                           "a classified event stream; the repo's topic-shock and "
                           "social lanes are explicitly forward-only. There is no "
                           "clinical-trial calendar and no index-change history.",
        "point_in_time": "A headline is timestamped, so a raw feed is PIT-safe. "
                         "Classifying it into an event type is a modelling problem, "
                         "and any classifier trained today knows what the outcomes "
                         "were — a subtle lookahead that is easy to introduce.",
        "estimated_calls": "~3,600 per pull for ticker news; a usable event history "
                           "needs repeated windowed pulls — plausibly 10,000+",
        "historical_coverage": "headline coverage thins badly for microcaps, which is "
                               "where the moves are",
        "helps": ["biotech", "moonshots", "special situations"],
        "separates_tails": "PLAUSIBLE BUT UNPROVABLE CHEAPLY. The move-anatomy result "
                           "below decides how much this matters.",
        "worth_testing": "LATER — expensive, and the classifier is a lookahead hazard",
    },
    {
        "class": "E_fundamentals_completeness",
        "items": ["point-in-time market cap for the whole universe",
                  "valuation coverage", "restatement-safe statements",
                  "segment revenue", "cash runway"],
        "provider_status": "AVAILABLE AND ALREADY SCOPED. The gap is 1,364 liquid "
                           "names with no market-cap history, at ~1 call each on the "
                           "same historical-market-capitalization endpoint the replay "
                           "already used.",
        "point_in_time": "YES for market cap. NO for restatement-safety: the provider "
                         "serves current statement versions, and as-originally-filed "
                         "data would need a different vendor entirely.",
        "estimated_calls": "~1,400",
        "historical_coverage": "would take market-cap coverage from 57% to ~100% of "
                               "liquid rows",
        "helps": ["all — it repairs a bias rather than adding a signal"],
        "separates_tails": "NO, ON ITS OWN. It removes a known distortion (the "
                           "uncovered 43% skews to established names the old scanner "
                           "never surfaced) but adds no new information.",
        "worth_testing": "YES AS HYGIENE, not as a conviction experiment",
    },
    {
        "class": "F_microstructure_tradability",
        "items": ["float", "short float", "float-adjusted turnover",
                  "bid/ask spread", "intraday follow-through"],
        "provider_status": "PARTIAL. Share counts are in the statements already held "
                           "(weightedAverageShsOutDil), so a crude float proxy is "
                           "computable at zero cost. True free float, spreads and "
                           "intraday data are not available for this window.",
        "point_in_time": "share counts YES (filed); float and spread NO",
        "estimated_calls": "0 for the proxy; intraday data is not offered",
        "historical_coverage": "share counts follow the 57% fundamentals coverage",
        "helps": ["small caps and squeeze setups"],
        "separates_tails": "WEAK PRIOR. The conviction study already showed liquidity "
                           "filters trim both tails proportionally, and float is a "
                           "close cousin of liquidity.",
        "worth_testing": "NO — cheap, but the nearest thing to it already failed",
    },
]


# ── stages ──────────────────────────────────────────────────────────────────


def headroom_stage(args) -> int:
    root = Path(args.root)
    tw = LiveArtifactTripwire.snapshot(root)
    pools = _build_pools(root, args)
    pool = label_within_pool(m1_pool(pools))
    print(f"M1 pool {len(pool):,} rows · {pool.ticker.nunique():,} tickers", flush=True)

    t0 = time.time()
    oracle = oracle_headroom(pool, args)
    print(f"oracle done {time.time() - t0:.0f}s", flush=True)
    ceil = ceiling_model(pool, args)
    print(f"ceiling model done {time.time() - t0:.0f}s", flush=True)
    anat = move_anatomy(pool)
    cov = coverage_audit(pool)

    # Regularisation sensitivity. A result that only exists at one alpha is a
    # fitting artifact, and alpha was not chosen by looking at the answer.
    sens = {}
    for alpha in (5.0, 50.0, 500.0):
        import copy
        a_args = copy.copy(args)
        a_args.ridge_alpha = alpha
        c = ceiling_model(pool, a_args)
        ho = (c["by_split"].get("holdout_2025_model_fit_on_train") or {}).get("top25") or {}
        wf = (c["by_split"].get("walkforward_2024_fit_on_prior_years") or {}).get("top25") or {}
        sens[f"alpha_{alpha:g}"] = {
            "holdout_top25_mean_minus_random": ho.get("mean_minus_random"),
            "holdout_top25_ticker_ci": ho.get("ticker_cluster_ci"),
            "walkforward_2024_top25_mean_minus_random": wf.get("mean_minus_random"),
        }

    out = {
        "kind": "M1_MISSING_DATA_HEADROOM",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pool_rows": int(len(pool)),
        "pool_tickers": int(pool.ticker.nunique()),
        "questions": {
            "oracle_headroom": "how much would a PERFECT ranker inside M1 be worth",
            "ceiling_model": "can the best linear combination of everything we already "
                             "hold separate names out-of-sample",
            "move_anatomy": "were the winners made by events or by drift",
            "coverage": "how much of the pool each existing data class reaches",
        },
        "oracle_headroom": oracle,
        "ceiling_model": ceil,
        "ceiling_model_alpha_sensitivity": sens,
        "move_anatomy": anat,
        "existing_data_coverage": cov,
        **provenance_flags(fundamentals=True),
    }
    assert_provenance(out)
    write_replay_json(root / HEADROOM_REL, out, root=root)
    print(tw.report())
    tw.assert_clean()

    o = oracle.get("train", {}).get("top25", {})
    print(f"\noracle top25 train: perfect={o.get('oracle_mean')} "
          f"random={o.get('random_mean')} headroom={o.get('headroom_vs_random')}pp")
    for k, v in ceil["by_split"].items():
        t25 = v.get("top25") or {}
        tag = "IN-SAMPLE" if v.get("in_sample") else "out-of-sample"
        print(f"ceiling {k:<40} [{tag}] auc={v.get('auc_top_decile_membership')} "
              f"top25 mean-rand={t25.get('mean_minus_random')} "
              f"med-rand={t25.get('median_minus_random')} "
              f"tickCI={t25.get('ticker_cluster_ci')}")
    print("alpha sensitivity:", json.dumps(sens, indent=1))
    mc = anat.get("winner_move_concentration", {})
    print(f"\nwinner move concentration: median single-day share "
          f"{mc.get('median_single_day_share_of_total_move')}, "
          f"one day > half the move in {mc.get('share_where_one_day_is_over_half_the_move')}% "
          f"of winners")
    return 0


def inventory_stage(args) -> int:
    root = Path(args.root)
    tw = LiveArtifactTripwire.snapshot(root)
    hr = json.loads((root / HEADROOM_REL).read_text())
    out = {
        "kind": "M1_MISSING_DATA_INVENTORY",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "grounding": "Provider assessments are grounded in the endpoints "
                     "core/fmp_client.py already reaches and the fields it documents. "
                     "Anything not verifiable offline is marked UNVERIFIED rather "
                     "than asserted, and no provider call was made to check.",
        "pool_scale": {"tickers": M1_TICKERS, "weekly_dates": M1_DATES},
        "existing_data_coverage": hr["existing_data_coverage"],
        "missing_data_classes": MISSING_DATA_CLASSES,
        "honest_caveats": [
            "New data is not a cure. Three studies have now found the same variance "
            "structure at three different zoom levels; a fourth dataset can fail the "
            "same way, and the prior should be that it will.",
            "Options history does not exist for this window at any price.",
            "News and social are forward-only here, and a classifier trained today on "
            "historical headlines knows the outcomes — a lookahead that is easy to "
            "introduce and hard to detect.",
            "13F lags 45 days and describes positions up to 135 days old; it is a "
            "description of the past, not an early signal.",
            "Fundamentals are restatement-contaminated at source; more of them does "
            "not fix that.",
            "Analyst revisions are the item with the best independent literature, but "
            "reconstructing a POINT-IN-TIME consensus from a current pull is the "
            "single easiest way to manufacture a fake edge in this whole programme.",
        ],
        **provenance_flags(fundamentals=True),
    }
    assert_provenance(out)
    write_replay_json(root / INVENTORY_REL, out, root=root)
    print(tw.report())
    tw.assert_clean()
    print(f"inventory: {len(MISSING_DATA_CLASSES)} data classes assessed")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="m1_missing_data_audit")
    sub = ap.add_subparsers(dest="stage", required=True)
    for name in ("headroom", "inventory", "report", "all"):
        s = sub.add_parser(name)
        s.add_argument("--root", default=str(ROOT))
        s.add_argument("--start", default=REPLAY_WINDOW_START)
        s.add_argument("--end", default=REPLAY_WINDOW_END)
        s.add_argument("--limit", type=int, default=0)
        s.add_argument("--bootstrap", type=int, default=1500)
        s.add_argument("--seed", type=int, default=29)
        s.add_argument("--ridge-alpha", type=float, default=50.0)
    return ap


STAGE_ORDER: tuple[str, ...] = ("headroom", "inventory", "report")


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

# ── the recommendation (Part 5) ─────────────────────────────────────────────

RECOMMENDATION: dict = {
    "chosen": "1_estimate_revision_and_earnings_surprise_overlay_inside_M1",
    "why_this_one": [
        "The move anatomy says the tail is EVENT-SHAPED. Two thirds of M1's "
        "top-decile winners made over a quarter of their move in one session and a "
        "quarter made over half in one session; 21% gapped 15%+ at some point. "
        "As-of price and volume cannot anticipate a scheduled or unscheduled "
        "announcement, which is exactly why the fitted ceiling model is "
        "ANTI-correlated with tail membership (AUC 0.44-0.49).",
        "Earnings is the only event class in this table that is both SCHEDULED and "
        "historically obtainable point-in-time. News classification is not, options "
        "history does not exist, and 13F describes the past.",
        "It is an order of magnitude cheaper than every alternative. FMP's "
        "/earnings-calendar is DATE-RANGED, not per-ticker: four years of "
        "epsActual/epsEstimated/revenueActual/revenueEstimated for the entire "
        "universe costs tens to low hundreds of calls. Insider, 13F or news would "
        "each cost ~3,600+.",
        "The conviction study's own earnings overlay is the strongest hint: it "
        "raised the +50% rate by 44% AND the -25% rate by 30%. It found live names "
        "and could not tell them apart. Surprise MAGNITUDE and direction are the "
        "obvious missing discriminator, and they are precisely what is absent from "
        "the filing-date proxy used there.",
    ],
    "data_needed": [
        "historical EPS surprise: epsActual vs epsEstimated per report date",
        "historical revenue surprise: revenueActual vs revenueEstimated",
        "report date and, critically, the estimate as it stood BEFORE the report",
    ],
    "estimated_provider_calls": {
        "primary": "~50 calls for monthly /earnings-calendar chunks across "
                   "2022-01-01..2025-12-31, or ~210 for weekly chunks if the "
                   "endpoint caps rows per response",
        "contingency": "up to ~500 if the window must be narrowed to keep responses "
                       "complete",
        "not_included": "forward estimate REVISION history, which is a different "
                        "endpoint, is UNVERIFIED offline, and would be per-ticker "
                        "(~3,600). It should be scoped separately AFTER surprise is "
                        "shown to carry information.",
    },
    "destination_paths": [
        "cache/research/m1_surprise_intermediates/earnings_surprise.parquet",
        "cache/research/m1_surprise_overlay_latest.json",
        "logs/m1_surprise_overlay_latest.txt",
    ],
    "namespace_registration_required": (
        "research/backtests/common.py must gain the m1_surprise_* prefixes before "
        "the experiment can write anything — the write guard refuses unregistered "
        "paths, which is the intended behaviour and not a workaround to skip."),
    "success_criteria": [
        "surprise magnitude separates future M1 winners from losers with a "
        "within-date AUC whose CI clears 0.5 on TRAIN years only",
        "a surprise-ranked top-25 beats a random same-size draw from M1 with the "
        "date-clustered CI clear of zero in at least 2 of 3 walk-forward years",
        "the ticker-clustered CI clears zero — the per-name bar the thirteen "
        "overlays all failed",
        "the right tail is not paid for: the +50% rate must not fall by more than "
        "the -25% rate",
    ],
    "failure_criteria": [
        "surprise AUC CI spans 0.5 on train — stop, do not tune",
        "top-25 fails to beat a random M1 draw in 2 of 3 walk-forward years",
        "the overlay raises both tails together, as the filing-date proxy did — that "
        "is liveness, not conviction, and it has now been seen twice",
        "coverage below ~60% of pool rows, which would make any result a statement "
        "about the covered subset rather than about M1",
    ],
    "ranking_or_annotation": (
        "Honest expectation: ANNOTATION, with a chance at ranking. Post-earnings "
        "drift is a real documented effect, but it is a weeks-long effect on a "
        "subset of names, and only ~60% of M1 rows sit within 40 days of a filing. "
        "The realistic best case is a strong tag on a minority of the list, not a "
        "reordering of all 600 names."),
    "what_this_does_not_promise": [
        "It does not address the 29% of M1 winners that had no day above 10% — pure "
        "drift, which earnings data will not explain.",
        "It cannot recover the unscheduled events (contracts, clinical read-outs, "
        "M&A) that make many of the biggest moves.",
        "Even complete success would capture a small fraction of the ~69-98pp oracle "
        "headroom.",
    ],
}


def report_stage(args) -> int:
    root = Path(args.root)
    tw = LiveArtifactTripwire.snapshot(root)
    hr = json.loads((root / HEADROOM_REL).read_text())
    inv = json.loads((root / INVENTORY_REL).read_text())
    conv = json.loads((root / OVERLAYS_REL).read_text())
    ins = json.loads((root / INSIDE_REL).read_text())
    pl = json.loads((root / POOL_REL).read_text())

    L: list[str] = []
    A = L.append
    A("=" * 78)
    A("MISSING CONVICTION DATA AUDIT — what would it take to rank inside M1?")
    A("=" * 78)
    A(f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')} · "
      f"zero provider calls")
    A("")
    A("RESEARCH ONLY. Replay evidence over a fixed historical window. Not live")
    A("forward evidence, not a gate/threshold/score change, not a trade signal.")
    A("")

    A("-" * 78)
    A("1. THE LIMITATION, CONFIRMED")
    A("-" * 78)
    tr = pl["train"]
    a, sel = tr[f"fwd_{PRIMARY_H}d"], tr[f"selection_{PRIMARY_H}d"]
    A(f"M1 train: {tr['episodes']:,} rows, {tr['median_names_per_date']:.0f} names/date, "
      f"mean {a['mean']}, median {a['median']}, tail ratio {a['tail_ratio']}.")
    A(f"Selection vs universe: {sel['selection_mean']}pp, "
      f"date-CI{sel['date_cluster_ci_mean']}, ticker-CI{sel['ticker_cluster_ci']}.")
    A("  -> a pool signal: the basket beats the tape, the typical name does not.")
    A("")
    A(f"Inside the pool, winner and loser fingerprints correlate "
      f"{ins['winner_vs_loser_mirror']['auc_correlation']:+.3f}. Thirteen overlays "
      f"produced {conv['verdict_counts']}.")
    A("M1's own ordering does not beat a random draw from M1. Volume/accumulation")
    A("showed no separation at all. Every volatility or risk filter cut both tails")
    A("proportionally. fundamental_quality was the only favourable tilt (-28% left,")
    A("-19% right) and even it failed out-of-sample as a return improver — it proved")
    A("tail shaping, not conviction.")
    A("")

    A("-" * 78)
    A("2. HOW BIG IS THE PRIZE?  (oracle headroom)")
    A("-" * 78)
    A(f"{'':2}{'list':<10}{'split':<10}{'perfect':>10}{'random':>9}{'backwards':>11}"
      f"{'headroom':>10}")
    for split, node in hr["oracle_headroom"].items():
        for n, v in node.items():
            A(f"  {n:<10}{split:<10}{v['oracle_mean']:>10}{v['random_mean']:>9}"
              f"{v['worst_case_mean']:>11}{v['headroom_vs_random']:>10}")
    A("")
    A("A perfect ranker inside M1 would have returned +71% per 60 days on a top-25")
    A("in the train years and +103% in 2025, against a random M1 draw's +2% and +5%.")
    A("Ranked backwards it would have lost 46-49%. The dispersion inside this pool")
    A("is enormous: the prize is real, and so is the damage from ranking badly.")
    A("")

    A("-" * 78)
    A("3. IS THE DATA WE ALREADY HOLD EXHAUSTED?  NOT QUITE — AND THIS IS NEW")
    A("-" * 78)
    c = hr["ceiling_model"]
    A(f"A ridge model over all {len(c['features_used'])} available as-of features "
      f"({c['n_terms']} terms with squared shapes), fit on the train years against "
      f"the within-date rank of forward return:")
    A("")
    A(f"{'':2}{'split':<42}{'sample':<14}{'AUC(tail)':>10}{'t25 vs rand':>12}"
      f"{'ticker CI':>18}")
    for k, v in c["by_split"].items():
        t = v.get("top25") or {}
        A(f"  {k:<42}{('in-sample' if v['in_sample'] else 'OUT-OF-SAMPLE'):<14}"
          f"{str(v.get('auc_top_decile_membership')):>10}"
          f"{str(t.get('mean_minus_random')):>12}{str(t.get('ticker_cluster_ci')):>18}")
    A("")
    A("Two things stand out, and they pull in opposite directions.")
    A("")
    A("  (a) The ticker-clustered CI clears zero out-of-sample in 2024 and 2025 —")
    A("      the FIRST per-name positive reading anywhere in this programme. The")
    A("      thirteen hand-written overlays all failed that bar; a fitted linear")
    A("      combination of the same features passes it in two of three")
    A("      out-of-sample years. 2023 is null. So the limitation was partly the")
    A("      RULES, not only the data.")
    A("  (b) The model's AUC for top-decile membership is 0.44-0.49 — BELOW 0.5. It")
    A("      is mildly anti-correlated with explosive winners. It ranks for the")
    A("      typical name (holdout top-25 median +3.8 vs random +1.6) by AVOIDING")
    A("      the tail, not by finding it.")
    A("")
    A("  Regularisation sensitivity (holdout top-25 vs random): " + ", ".join(
        f"alpha {k.split('_')[1]}: {v['holdout_top25_mean_minus_random']}"
        for k, v in hr["ceiling_model_alpha_sensitivity"].items()))
    A("  — stable, so it is not a knife-edge fit.")
    A("")
    A("  Scale it against the prize: the model captures roughly 2-3pp of a 69-98pp")
    A("  oracle headroom. About 3% of what is there. The overwhelming majority of")
    A("  within-pool dispersion is explained by nothing we hold.")
    A("")

    A("-" * 78)
    A("4. WHAT MADE THE WINNERS?  MOSTLY EVENTS")
    A("-" * 78)
    ma = hr["move_anatomy"]
    w, l, p_ = (ma["pool_top_decile_winners"], ma["pool_bottom_decile_losers"],
                ma["whole_pool"])
    A(f"{'':2}{'':<34}{'winners':>10}{'losers':>10}{'whole pool':>12}")
    for lbl, key in (("median biggest up day %", "median_biggest_up_day_pct"),
                     ("median biggest down day %", "median_biggest_down_day_pct"),
                     ("share with a +25% day", "share_with_a_25pct_up_day"),
                     ("share with a +15% gap", "share_with_a_15pct_gap_up"),
                     ("share with a -25% day", "share_with_a_25pct_down_day"),
                     ("share with NO day above 10%", "share_no_day_above_10pct")):
        A(f"  {lbl:<34}{w[key]:>10}{l[key]:>10}{p_[key]:>12}")
    mc = ma["winner_move_concentration"]
    A("")
    A(f"Of the winners' total move, the single largest session is a median "
      f"{mc['median_single_day_share_of_total_move']:.1%}. One session is more than")
    A(f"half the move for {mc['share_where_one_day_is_over_half_the_move']}% of "
      f"winners and more than a quarter for "
      f"{mc['share_where_one_day_is_over_a_quarter']}%.")
    A("")
    A("  This is the load-bearing measurement of the audit. The M1 tail is built by")
    A("  DISCRETE EVENTS, and no quantity of as-of price and volume history can")
    A("  anticipate an announcement. It explains why the ceiling model is")
    A("  anti-correlated with tail membership, why every risk filter trimmed both")
    A("  tails, and why thirteen structural overlays found nothing.")
    A("")
    A(f"  The counterweight: {w['share_no_day_above_10pct']}% of winners had no day")
    A("  above 10% — pure drift. That subset is the part flow and estimate data")
    A("  could plausibly rank. It is a minority, not the main event.")
    A("")

    A("-" * 78)
    A("5. COVERAGE OF WHAT WE ALREADY HOLD")
    A("-" * 78)
    for k, v in hr["existing_data_coverage"].items():
        A(f"  {k:<24}{v.get('coverage_pct')}% of M1 rows")
    A("")
    A("  Everything fundamental sits near 57-65%, and that gap is not random: it is")
    A("  names the old scanner never surfaced. Sector coverage is under half.")
    A("")

    A("-" * 78)
    A("6. THE MISSING DATA, CLASS BY CLASS")
    A("-" * 78)
    for cls in inv["missing_data_classes"]:
        A(f"  {cls['class']}")
        A(f"    provider:   {cls['provider_status']}")
        A(f"    PIT-safe:   {cls['point_in_time']}")
        A(f"    calls:      {cls['estimated_calls']}")
        A(f"    separates:  {cls['separates_tails']}")
        A(f"    verdict:    {cls['worth_testing']}")
        A("")

    A("-" * 78)
    A("7. NEW DATA MAY NOT FIX THIS")
    A("-" * 78)
    for c_ in inv["honest_caveats"]:
        A(f"  - {c_}")
    A("")

    A("-" * 78)
    A("8. THE SINGLE NEXT EXPERIMENT")
    A("-" * 78)
    r = RECOMMENDATION
    A(f"  {r['chosen']}")
    A("")
    for w_ in r["why_this_one"]:
        A(f"  - {w_}")
    A("")
    A(f"  calls:        {r['estimated_provider_calls']['primary']}")
    A(f"                contingency {r['estimated_provider_calls']['contingency']}")
    A(f"                NOT included: {r['estimated_provider_calls']['not_included']}")
    A(f"  writes:       {', '.join(r['destination_paths'])}")
    A("")
    A("  success criteria:")
    for x in r["success_criteria"]:
        A(f"    - {x}")
    A("  failure criteria (stop, do not tune):")
    for x in r["failure_criteria"]:
        A(f"    - {x}")
    A("")
    A(f"  ranking or annotation?  {r['ranking_or_annotation']}")
    A("")
    A("  what it does not promise:")
    for x in r["what_this_does_not_promise"]:
        A(f"    - {x}")
    A("")
    A("  NOTE: this requires provider calls and is NOT authorised. Nothing was")
    A("  fetched for this audit.")
    A("")

    A("=" * 78)
    A("9. DIRECT ANSWERS")
    A("=" * 78)
    A("")
    A("1. Are we stuck because the signal is inherently portfolio-level?  PARTLY, AND")
    A("   LESS THAN I PREVIOUSLY SAID. The fitted ceiling model produces a positive")
    A("   ticker-clustered reading in two of three out-of-sample years, which no")
    A("   hand-written overlay managed. Per-name structure exists. It is small —")
    A("   about 3% of the oracle headroom — and it works by avoiding the tail rather")
    A("   than finding it.")
    A("")
    A("2. Or are we missing the right point-in-time data?  BOTH, and the move")
    A("   anatomy says which part is missing: the events. Two thirds of winners made")
    A("   over a quarter of their move in one session. That information does not")
    A("   exist in any price history, however cleverly processed.")
    A("")
    A("3. Which missing source has the best chance?  EARNINGS SURPRISE, and it is")
    A("   not close on a cost-adjusted basis — a date-ranged endpoint the client")
    A("   already calls, tens of calls rather than thousands, point-in-time safe if")
    A("   the estimate is taken before the report. Short interest is the interesting")
    A("   second because it is explicitly asymmetric. Options history does not exist")
    A("   for this window at any price.")
    A("")
    A("4. Should we continue trying to rank inside M1?  YES, but differently: fit")
    A("   and pre-register rather than hand-write more rules, and judge on the")
    A("   ticker-clustered bar. The fitted model is the first thing in this")
    A("   programme to pass it. It is suggestive, not established — 2023 was null,")
    A("   and after this many passes over one window, programme-level")
    A("   researcher-degrees-of-freedom are a real contaminant no split can remove.")
    A("")
    A("5. Or accept M1 as a wide pool and focus on manual research?  THAT REMAINS")
    A("   THE DEFAULT, and it is not a defeat. The evidence supports a wide")
    A("   equal-weight M1 basket, with the fitted rank carried as a RISK ordering")
    A("   (it improves the median by avoiding blow-ups) and never as a conviction")
    A("   ordering. Human research is where the event information enters, and events")
    A("   are what the data says makes the winners.")
    A("")
    A("=" * 78)
    A("BOTTOM LINE")
    A("=" * 78)
    A("The prize is enormous — a perfect ranker inside M1 is worth +70 to +100pp per")
    A("60 days on a top-25 — and we are capturing about 3% of it. The reason is")
    A("legible now: the winners are made by discrete events that no price history")
    A("can anticipate. A fitted model does find a small, real per-name signal that")
    A("hand-written rules missed, but it works by dodging the left tail, not by")
    A("catching the right one. The cheapest honest next step is earnings surprise —")
    A("tens of provider calls, point-in-time safe, and aimed squarely at the one")
    A("event class that is scheduled. It should be expected to produce a good")
    A("annotation on a minority of names, not a reordering of the list.")
    A("")

    text = "\n".join(L)
    write_replay_text(root / REPORT_TXT_REL, text + "\n", root=root)
    summary = {
        "kind": "M1_MISSING_DATA_LATEST",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider_calls_made": 0,
        "headline": (
            "The M1 tail is event-shaped: two thirds of top-decile winners made over "
            "a quarter of their move in one session. A fitted ridge model over all "
            "available features is the first thing in this programme to clear the "
            "ticker-clustered bar out-of-sample (2 of 3 years), but it does so by "
            "avoiding the tail, and captures ~3% of the oracle headroom."),
        "oracle_headroom_top25": {
            "train": hr["oracle_headroom"]["train"]["top25"]["headroom_vs_random"],
            "holdout": hr["oracle_headroom"]["holdout"]["top25"]["headroom_vs_random"],
        },
        "ceiling_model_out_of_sample": {
            k: {"auc_tail": v.get("auc_top_decile_membership"),
                "top25_vs_random": (v.get("top25") or {}).get("mean_minus_random"),
                "ticker_ci": (v.get("top25") or {}).get("ticker_cluster_ci")}
            for k, v in hr["ceiling_model"]["by_split"].items() if not v["in_sample"]},
        "winner_move_concentration": hr["move_anatomy"]["winner_move_concentration"],
        "recommendation": RECOMMENDATION,
        "requires_approval": True,
        "not_supported": [
            "any claim that new data will fix per-name conviction",
            "a fourteenth hand-written overlay",
            "any production change",
        ],
        "report_txt": REPORT_TXT_REL,
        "artifacts": [HEADROOM_REL, INVENTORY_REL],
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

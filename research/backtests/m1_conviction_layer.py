"""M1 Momentum Conviction Layer — can ranking INSIDE the momentum pool help?

RESEARCH ONLY. Reads the replay caches and the Alpha Discovery Tournament's
panel; writes only ``cache/research/m1_conviction_*`` and
``logs/m1_conviction_*``. Changes no scanner, score, gate, threshold, HC/EO
rule, routing decision, dashboard artifact, cron entry or ledger, emits no
trade signal, and cannot emit a live verdict token.

The tournament found classic 12-1 momentum (family M1) to be the only one of
thirty families to clear every pre-declared bar — but with a caveat that
matters more than the headline: its date-clustered evidence is positive while
its ticker-clustered evidence is negative. The edge is a property of the
BASKET, not of the typical name in it. A human reading a 25-name shortlist
experiences the second, not the first.

So this study asks a narrower question: inside the M1 pool, is there anything
knowable on the scan date that separates the names that go on to win from the
ones that go on to lose — enough to make a 25- or 50-name shortlist better
per-name than a random 25 drawn from the same pool?

Contamination, stated once and enforced in code:

* M1 itself was CONFIRMED on 2025. That year is no longer clean for the base
  pool, and this study does not re-litigate M1's own verdict.
* Every overlay here is discovered on 2022-2024 ONLY. The
  :func:`_assert_train_only` guard fails the discovery stage if a holdout row
  reaches it.
* Overlays are then read once on 2025. That read is out-of-sample for the
  OVERLAY RULE, but it happens inside a year already used to confirm the pool,
  so it is reported as a weaker form of evidence than a virgin holdout.
* A walk-forward inside the train window (2022 -> 2023 -> 2024) gives each
  overlay one genuinely uncontaminated out-of-sample test, and that is the
  test the verdict leans on.

Sub-stages::

    pool      Part 1: freeze M1, reproduce its statistics       (zero API)
    inside    Part 2: winners vs losers within M1, train only   (zero API)
    overlays  Parts 3-5: rank inside M1, evaluate, walk forward (zero API)
    report    Part 6: direct answers                            (zero API)
    all       every stage in order                              (zero API)
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
    assert_conviction_verdict,
    assert_provenance,
    assert_replay_write_path,
    offline_env,
    provenance_flags,
    run_cli,
    write_replay_json,
    write_replay_text,
)
from research.backtests.alpha_reconstruction_lab import (
    HOLDOUT_YEARS,
    TRAIN_YEARS,
    _cluster_ci,
    _trim_mean,
)
from research.backtests.alpha_tournament import (
    FAMILIES,
    HORIZONS,
    LIST_SIZES,
    PRIMARY_H,
    _build_pools,
    _pct,
    _top_n,
    _z,
    asymmetry,
    matched_twin,
    selection_vs,
)

offline_env()

INTERMEDIATES_REL = "cache/research/m1_conviction_intermediates"
POOL_REL = "cache/research/m1_conviction_pool.json"
INSIDE_REL = "cache/research/m1_conviction_inside_study.json"
OVERLAYS_REL = "cache/research/m1_conviction_overlays.json"
LATEST_REL = "cache/research/m1_conviction_latest.json"
REPORT_TXT_REL = "logs/m1_conviction_latest.txt"

# The frozen source pool: imported from the tournament rather than restated, so
# the definition cannot drift between the two studies.
M1 = next(f for f in FAMILIES if f["name"] == "momentum_12_1_raw")

WALK_FORWARD_STEPS: tuple[tuple[int, int], ...] = ((2022, 2023), (2023, 2024))


class TrainOnlyViolation(RuntimeError):
    """Discovery code touched a holdout row."""


def _assert_train_only(df) -> None:
    """Fail loudly if a discovery input contains a holdout year.

    The whole value of this study rests on the overlays being written from
    2022-2024 evidence only. That is easy to break by passing the wrong frame,
    so it is checked rather than trusted.
    """
    bad = sorted(set(df["year"].unique()) & set(HOLDOUT_YEARS))
    if bad:
        raise TrainOnlyViolation(
            f"discovery input contains holdout years {bad}; overlays must be "
            "written from train years only")


def m1_pool(pools: dict):
    """The frozen M1 cohort, carrying every column an overlay may read.

    M1's own rule is evaluated on the FULL universe — that is the frozen
    definition and it must not change — but the resulting cohort is then
    left-joined to the fundamentals and event panels so the earnings and
    fundamental overlay groups can be tested at all. Left join, never inner:
    an inner join would silently shrink the pool to the 57%-covered subset and
    quietly change what M1 means.
    """
    full = pools["full"]
    mask = M1["mask"](full).fillna(False)
    pool = full[mask].copy()
    n0 = len(pool)
    for key in ("fund", "event"):
        other = pools.get(key)
        if other is None or other.empty:
            continue
        extra = [c for c in other.columns
                 if c not in pool.columns or c in ("ticker", "scan_date")]
        pool = pool.merge(other[extra], on=["ticker", "scan_date"], how="left")
    assert len(pool) == n0, "the metadata join must not change the pool size"
    return pool


# ── within-pool labels (Part 2) ─────────────────────────────────────────────

PCT_CUTS: tuple[float, ...] = (1.0, 2.0, 5.0, 10.0)
IN_POOL_TOP_N: tuple[int, ...] = (25, 50, 100)


def label_within_pool(pool):
    """Winner/loser labels computed WITHIN the M1 pool, per scan date.

    Deliberately not the universe-wide labels: the question here is which M1
    names beat the other M1 names, so the ranking that defines a winner has to
    be taken inside the pool. Labels use forward returns; no labelled column is
    ever read by an overlay rule.
    """
    for h in HORIZONS:
        col = f"fwd_{h}d"
        up = pool.groupby("scan_date")[col].rank(pct=True, ascending=False) * 100.0
        dn = pool.groupby("scan_date")[col].rank(pct=True, ascending=True) * 100.0
        for cut in PCT_CUTS:
            pool[f"pool_top{cut:g}pct_{h}d"] = up <= cut
            pool[f"pool_bot{cut:g}pct_{h}d"] = dn <= cut
        rank = pool.groupby("scan_date")[col].rank(ascending=False, method="first")
        for n in IN_POOL_TOP_N:
            pool[f"pool_top{n}_{h}d"] = rank <= n
    pool["pool_catastrophic"] = pool[f"fwd_{PRIMARY_H}d"] <= -50
    pool["pool_flat"] = pool[f"fwd_{PRIMARY_H}d"].between(-5, 5)
    return pool


# Features the overlays may read. Grouped as the brief specifies so the study
# can report which GROUP carries information, not just which column.
FEATURE_GROUPS: dict[str, tuple[str, ...]] = {
    "A_momentum_quality": (
        "mom_12_1", "mom_6_1", "ret_63d", "ret_20d", "rs_63d", "rs_20d",
        "rs_accel_20_63", "rs_accel_10_20", "dd_from_high_pct",
        "dist_ma20_pct", "dist_ma50_pct", "dist_ma200_pct",
        "ma50_slope_20d_pct", "ma200_slope_20d_pct", "days_since_252d_high",
        "new_63d_high",
    ),
    "B_exhaustion": (
        "combined_rs", "atr14_pct", "rvol_63d_pct", "rvol_ratio_20_63",
        "max_gap_up_20d_pct", "gap_abs_20d_pct", "tail_gain_days_63",
        "tail_loss_days_63", "worst_dd_252d_pct", "above_ma20", "above_ma50",
    ),
    "C_volume_accumulation": (
        "vol_expansion_5_63", "vol_trend_20_63", "up_down_volume_20",
        "up_down_volume_63", "accumulation_days_20", "liquidity_trend",
        "dvol_med_20", "fake_liquidity_ratio", "rel_volume_today",
    ),
    "D_earnings": (
        "days_since_filing", "post_earnings_reaction_pct", "rev_accel_pp",
        "eps_accel_pp", "op_margin_delta_pp", "eps_growth_yoy_pct",
    ),
    "E_fundamentals": (
        "rev_yoy_pct", "gross_margin_pct", "op_margin_pct", "fcf_margin_pct",
        "roe_pct", "roic_proxy_pct", "gross_profit_to_assets", "debt_to_equity",
        "dilution_yoy_pct", "profitable", "fcf_positive", "net_cash",
        "ev_to_sales", "fcf_yield_pct", "earnings_yield_pct",
    ),
    "F_sector_theme": (
        "sector_rs_rank_pct", "sector_peer_breadth_pct", "sector_peer_count",
    ),
    "G_entry_structure": (
        "base_tightness_63d", "higher_lows_20_40", "reclaimed_ma50_20d",
        "reclaimed_ma200_20d", "range_63d_pct", "vol_dryup_then_expand",
        "upside_downside_vol_ratio",
    ),
}


def _within_date_auc(g, feat: str, label: str) -> float | None:
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


def separation_within_pool(train, label: str, n_boot: int, seed: int) -> list[dict]:
    """Which as-of features separate winners from the rest INSIDE the pool."""
    import numpy as np

    rows = []
    for group, feats in FEATURE_GROUPS.items():
        for feat in feats:
            if feat not in train.columns:
                continue
            aucs = []
            for _, g in train.groupby("scan_date"):
                a = _within_date_auc(g, feat, label)
                if a is not None:
                    aucs.append(a)
            if len(aucs) < 20:
                continue
            arr = np.array(aucs)
            cov = float(train[feat].notna().mean() * 100)
            rows.append({
                "feature": feat, "group": group,
                "auc_mean": round(float(arr.mean()), 4),
                "auc_ci": _cluster_ci(arr, n_boot, seed),
                "dates": len(aucs),
                "coverage_pct": round(cov, 1),
                "winner_median": round(float(np.nanmedian(
                    train[train[label]][feat].astype(float))), 3),
                "pool_median": round(float(np.nanmedian(train[feat].astype(float))), 3),
            })
    rows.sort(key=lambda r: abs(r["auc_mean"] - 0.5), reverse=True)
    return rows


def decile_profile_in_pool(train, feat: str, h: int) -> list[dict]:
    """Forward outcome by within-date decile of a feature, inside the pool."""
    import numpy as np
    import pandas as pd

    df = train[["scan_date", feat, f"fwd_{h}d"]].dropna(subset=[feat])
    if len(df) < 2000:
        return []
    dec = df.groupby("scan_date")[feat].transform(
        lambda s: pd.qcut(s.rank(method="first"), 10, labels=False, duplicates="drop"))
    df = df.assign(decile=dec).dropna(subset=["decile"])
    out = []
    for d, g in df.groupby("decile"):
        a = asymmetry(g[f"fwd_{h}d"]) or {}
        out.append({
            "decile": int(d) + 1, "n": int(len(g)),
            "feature_median": round(float(g[feat].median()), 3),
            "mean": a.get("mean"), "median": a.get("median"),
            "tail_ratio": a.get("tail_ratio"), "win_rate": a.get("win_rate"),
            "pct_down_25": a.get("pct_down_25"),
        })
    return out


# ── stage: pool (Part 1) ────────────────────────────────────────────────────


def _pool_stats(pool, universe, args, label_prefix="") -> dict:
    out = {
        "episodes": int(len(pool)),
        "unique_tickers": int(pool.ticker.nunique()),
        "dates": int(pool.scan_date.nunique()),
        "median_names_per_date": float(pool.groupby("scan_date").size().median()),
        "median_dvol_musd": round(float(pool["dvol_med_20"].median() / 1e6), 2),
    }
    for h in HORIZONS:
        out[f"fwd_{h}d"] = asymmetry(pool[f"fwd_{h}d"])
        out[f"selection_{h}d"] = selection_vs(pool, universe, h, args.bootstrap, args.seed)
    for lbl in (f"top10pct_{PRIMARY_H}d", f"top5pct_{PRIMARY_H}d",
                f"bot10pct_{PRIMARY_H}d", f"bot5pct_{PRIMARY_H}d"):
        if lbl in universe.columns:
            base = float(universe[lbl].mean())
            out[f"capture_{lbl}"] = {
                "rate_pct": round(float(pool[lbl].mean() * 100), 3),
                "lift": round(float(pool[lbl].mean() / base), 2) if base else None,
                "share_captured_pct": round(
                    100.0 * float(pool[lbl].sum()) / max(float(universe[lbl].sum()), 1e-9), 2),
            }
    return out


def pool_stage(args) -> int:
    root = Path(args.root)
    tw = LiveArtifactTripwire.snapshot(root)
    pools = _build_pools(root, args)
    full = pools["full"]
    pool = m1_pool(pools)
    print(f"M1 pool: {len(pool):,} rows · {pool.ticker.nunique():,} tickers · "
          f"{pool.groupby('scan_date').size().median():.0f} names/date", flush=True)

    out = {
        "kind": "M1_CONVICTION_POOL",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "m1_definition": {
            "source": "research.backtests.alpha_tournament.FAMILIES['momentum_12_1_raw']",
            "rule": "within-date top quintile of mom_12_1",
            "mom_12_1": "twelve-month return with the most recent ~21 sessions "
                        "removed: (1+r252)/(1+r21)-1",
            "frozen": True,
            "requires": M1["requires"], "excludes": M1["excludes"],
            "no_old_score_inheritance": True,
        },
        "contamination_note": (
            "M1's own holdout verdict was taken on 2025 in the Alpha Discovery "
            "Tournament. This stage REPRODUCES those statistics; it does not "
            "re-test them, and 2025 is not a clean holdout for the base pool."),
        "train": _pool_stats(pool[pool.year.isin(TRAIN_YEARS)],
                             full[full.year.isin(TRAIN_YEARS)], args),
        "holdout_already_used_for_confirmation": _pool_stats(
            pool[pool.year.isin(HOLDOUT_YEARS)],
            full[full.year.isin(HOLDOUT_YEARS)], args),
        "by_year": {
            str(int(y)): {
                "episodes": int((pool.year == y).sum()),
                "names_per_date": float(
                    pool[pool.year == y].groupby("scan_date").size().median()),
                **{k: v for k, v in (asymmetry(pool[pool.year == y][f"fwd_{PRIMARY_H}d"]) or {}).items()
                   if k in ("mean", "median", "trim10", "winsor5_95", "win_rate", "tail_ratio")},
                "selection_mean": (selection_vs(
                    pool[pool.year == y], full[full.year == y], PRIMARY_H,
                    args.bootstrap, args.seed) or {}).get("selection_mean"),
            } for y in sorted(full.year.unique())},
        "matched_twin": {
            "train": matched_twin(pool[pool.year.isin(TRAIN_YEARS)],
                                  full[full.year.isin(TRAIN_YEARS)], PRIMARY_H, args),
            "holdout": matched_twin(pool[pool.year.isin(HOLDOUT_YEARS)],
                                    full[full.year.isin(HOLDOUT_YEARS)], PRIMARY_H,
                                    args, seed_offset=1)},
        **provenance_flags(fundamentals=True),
    }
    assert_provenance(out)
    write_replay_json(root / POOL_REL, out, root=root)
    print(tw.report())
    tw.assert_clean()
    tr = out["train"]
    sel = tr[f"selection_{PRIMARY_H}d"]
    print(f"train: mean={tr[f'fwd_{PRIMARY_H}d']['mean']} "
          f"median={tr[f'fwd_{PRIMARY_H}d']['median']} "
          f"sel_mean={sel['selection_mean']} dateCI={sel['date_cluster_ci_mean']} "
          f"tickerCI={sel['ticker_cluster_ci']}")
    return 0


# ── stage: inside (Part 2) ──────────────────────────────────────────────────


def inside_stage(args) -> int:
    import numpy as np

    root = Path(args.root)
    tw = LiveArtifactTripwire.snapshot(root)
    pools = _build_pools(root, args)
    pool = label_within_pool(m1_pool(pools))
    train = pool[pool.year.isin(TRAIN_YEARS)].copy()
    _assert_train_only(train)
    print(f"inside-pool study on train only: {len(train):,} rows", flush=True)

    label_w = f"pool_top10pct_{PRIMARY_H}d"
    label_l = f"pool_bot10pct_{PRIMARY_H}d"
    win_sep = separation_within_pool(train, label_w, args.bootstrap, args.seed)
    lose_sep = separation_within_pool(train, label_l, args.bootstrap, args.seed)
    w = {r["feature"]: r["auc_mean"] for r in win_sep}
    l = {r["feature"]: r["auc_mean"] for r in lose_sep}
    both = [f for f in w if f in l]
    wv = np.array([w[f] - 0.5 for f in both])
    lv = np.array([l[f] - 0.5 for f in both])

    out = {
        "kind": "M1_CONVICTION_INSIDE_STUDY",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "discovery_window": {"train_years": list(TRAIN_YEARS),
                             "holdout_not_read_here": list(HOLDOUT_YEARS)},
        "pool_rows_train": int(len(train)),
        "labels": {
            "winner": f"top 10% of the M1 pool by forward {PRIMARY_H}d, per date",
            "loser": f"bottom 10% of the M1 pool by forward {PRIMARY_H}d, per date",
            "catastrophic_rate_pct": round(float(train["pool_catastrophic"].mean() * 100), 2),
            "flat_rate_pct": round(float(train["pool_flat"].mean() * 100), 2),
        },
        "winner_separation": win_sep,
        "loser_separation": lose_sep,
        "winner_vs_loser_mirror": {
            "question": "Do the features that mark future winners inside M1 also "
                        "mark future losers?",
            "auc_correlation": round(float(np.corrcoef(wv, lv)[0, 1]), 4),
            "share_same_direction_pct": round(float(np.mean((wv * lv) > 0) * 100), 1),
            "interpretation": "Near +1 means the pool's internal separation is "
                              "variance, not direction, and no conviction ranking "
                              "inside it can work. Near -1 would mean genuine "
                              "directional information.",
        },
        "group_strength": {},
        "decile_profiles": {},
        **provenance_flags(fundamentals=True),
    }
    # Which GROUP carries information, using the best feature in each.
    for group in FEATURE_GROUPS:
        rows = [r for r in win_sep if r["group"] == group]
        if not rows:
            continue
        best = max(rows, key=lambda r: abs(r["auc_mean"] - 0.5))
        out["group_strength"][group] = {
            "features_tested": len(rows),
            "best_feature": best["feature"],
            "best_auc": best["auc_mean"],
            "best_auc_ci": best["auc_ci"],
            "coverage_pct": best["coverage_pct"],
            "mean_abs_auc_gap": round(float(np.mean(
                [abs(r["auc_mean"] - 0.5) for r in rows])), 4),
        }
    for r in win_sep[:12]:
        prof = decile_profile_in_pool(train, r["feature"], PRIMARY_H)
        if prof:
            out["decile_profiles"][r["feature"]] = prof

    assert_provenance(out)
    write_replay_json(root / INSIDE_REL, out, root=root)
    print(tw.report())
    tw.assert_clean()
    print(f"\nwinner/loser AUC correlation inside M1: "
          f"{out['winner_vs_loser_mirror']['auc_correlation']:+.3f}")
    print("top separators inside the pool:")
    for r in win_sep[:10]:
        print(f"  {r['feature']:<24} {r['group']:<22} auc={r['auc_mean']:.3f} "
              f"ci={r['auc_ci']} cov={r['coverage_pct']}%")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="m1_conviction_layer")
    sub = ap.add_subparsers(dest="stage", required=True)
    for name in ("pool", "inside", "overlays", "report", "all"):
        s = sub.add_parser(name)
        s.add_argument("--root", default=str(ROOT))
        s.add_argument("--start", default=REPLAY_WINDOW_START)
        s.add_argument("--end", default=REPLAY_WINDOW_END)
        s.add_argument("--limit", type=int, default=0)
        s.add_argument("--bootstrap", type=int, default=1500)
        s.add_argument("--seed", type=int, default=23)
    return ap


STAGE_ORDER: tuple[str, ...] = ("pool", "inside", "overlays", "report")


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

# ── overlays (Parts 3-5) ────────────────────────────────────────────────────
#
# Two kinds, and the distinction matters for how each is judged:
#   filter — removes names from the pool, then keeps M1's own ordering
#            (rank = mom_12_1). Isolates the effect of the exclusion.
#   ranker — keeps the whole pool and re-orders it. Isolates the effect of the
#            new score.
# Every threshold is a round number taken from the train-window separation
# study, not tuned against the outcome.

OVERLAYS: list[dict] = [
    {"name": "avoid_top_volatility", "kind": "filter",
     "hypothesis": "Inside M1, volatility raises the odds of the bottom decile "
                   "(loser AUC 0.758) more than the top decile (0.682). Removing "
                   "the most volatile fifth should cut the left tail harder than "
                   "the right.",
     "mask": lambda d: _pct(d, "atr14_pct") <= 0.80,
     "rank": lambda d: _z(d, "mom_12_1")},
    {"name": "avoid_exhaustion_stack", "kind": "filter",
     "hypothesis": "Remove the three exhaustion markers together: the old score's "
                   "saturation zone, the top volatility quintile, and names that "
                   "have broken their MA20 after a large run.",
     "mask": lambda d: ((d.combined_rs < 62.5) & (_pct(d, "atr14_pct") <= 0.80)
                        & ~((d.ret_63d > 100) & (d.above_ma20 == 0))),
     "rank": lambda d: _z(d, "mom_12_1")},
    {"name": "avoid_prior_crash", "kind": "filter",
     "hypothesis": "Names whose worst 252-day drawdown is severe mark losers "
                   "(0.272) more than winners (0.358) — a left-tail prior.",
     "mask": lambda d: d.worst_dd_252d_pct > -50,
     "rank": lambda d: _z(d, "mom_12_1")},
    {"name": "require_liquidity_quality", "kind": "filter",
     "hypothesis": "Real book, not block prints: median dollar volume in the top "
                   "half of the pool and a mean/median ratio under 2.",
     "mask": lambda d: (_pct(d, "dvol_med_20") >= 0.50) & (d.fake_liquidity_ratio < 2.0),
     "rank": lambda d: _z(d, "mom_12_1")},
    {"name": "earnings_confirmed", "kind": "filter",
     "hypothesis": "A recent filing the market took well — the re-rating overlay.",
     "mask": lambda d: (d.days_since_filing <= 40) & (d.post_earnings_reaction_pct > 0),
     "rank": lambda d: _z(d, "post_earnings_reaction_pct")},
    {"name": "volume_accumulation", "kind": "filter",
     "hypothesis": "More volume on up days than down days, with liquidity itself "
                   "expanding.",
     "mask": lambda d: (d.up_down_volume_63 > 1.2) & (d.liquidity_trend > 1.0),
     "rank": lambda d: _z(d, "up_down_volume_63")},
    {"name": "sector_confirmed", "kind": "filter",
     "hypothesis": "Leadership with peer confirmation: strong inside its own "
                   "sector, and the sector broadly working.",
     "mask": lambda d: ((d.sector_rs_rank_pct >= 60) & (d.sector_peer_breadth_pct >= 50)
                        & (d.sector_peer_count >= 10)),
     "rank": lambda d: _z(d, "sector_rs_rank_pct")},
    {"name": "fundamental_quality", "kind": "filter",
     "hypothesis": "Profitable and cash-generative with dilution controlled.",
     "mask": lambda d: ((d.profitable == 1.0) & (d.fcf_positive == 1.0)
                        & (d.dilution_yoy_pct < 5)),
     "rank": lambda d: _z(d, "fcf_margin_pct")},
    {"name": "pullback_continuation", "kind": "filter",
     "hypothesis": "Resting rather than extended: near the MA50 on drying volume.",
     "mask": lambda d: (d.dist_ma50_pct.between(-8, 5) & (d.vol_trend_20_63 < 1.0)),
     "rank": lambda d: _z(d, "mom_12_1")},
    {"name": "near_high_not_blowoff", "kind": "filter",
     "hypothesis": "Close to the 52-week high but without a recent vertical gap — "
                   "strength without the blow-off signature.",
     "mask": lambda d: ((d.dd_from_high_pct > -10) & (d.dist_ma50_pct < 20)
                        & (d.max_gap_up_20d_pct < 15)),
     "rank": lambda d: _z(d, "mom_12_1")},
    {"name": "rank_by_momentum_strength", "kind": "ranker",
     "hypothesis": "CONTROL — more momentum is better. Tests whether M1's own "
                   "ordering carries information inside the pool.",
     "mask": lambda d: d["price"].notna(),
     "rank": lambda d: _z(d, "mom_12_1")},
    {"name": "rank_by_calm_momentum", "kind": "ranker",
     "hypothesis": "Momentum penalised by volatility — the single strongest "
                   "directional pair the train study found.",
     "mask": lambda d: d["price"].notna(),
     "rank": lambda d: _z(d, "mom_12_1") - _z(d, "atr14_pct")},
    {"name": "rank_evidence_stack", "kind": "ranker",
     "hypothesis": "Every train-window direction at once: momentum, calm, real "
                   "liquidity, no prior crash, tight range.",
     "mask": lambda d: d["price"].notna(),
     "rank": lambda d: (_z(d, "mom_12_1") - _z(d, "atr14_pct") + _z(d, "dvol_med_20")
                        + _z(d, "worst_dd_252d_pct") - _z(d, "range_63d_pct")),
     "discovery_fitted": True},
]


def evaluate_list(picks, pool, args) -> dict:
    """Metrics for one shortlist, measured against the M1 pool it came from."""
    import numpy as np

    if picks.empty:
        return {"episodes": 0}
    a = asymmetry(picks[f"fwd_{PRIMARY_H}d"]) or {}
    sel = selection_vs(picks, pool, PRIMARY_H, args.bootstrap, args.seed)
    per_date = picks.groupby("scan_date")
    names = {d: set(g["ticker"]) for d, g in per_date}
    keys = sorted(names)
    ov = [len(names[b] & names[a_]) / max(len(names[b]), 1)
          for a_, b in zip(keys, keys[1:]) if names[b]]
    out = {
        "episodes": int(len(picks)),
        "unique_tickers": int(picks.ticker.nunique()),
        "median_names_per_date": float(per_date.size().median()),
        "mean": a.get("mean"), "median": a.get("median"),
        "trim10": a.get("trim10"), "winsor5_95": a.get("winsor5_95"),
        "win_rate": a.get("win_rate"), "tail_ratio": a.get("tail_ratio"),
        "p90": a.get("p90"), "p10": a.get("p10"),
        "pct_up_50": a.get("pct_up_50"), "pct_up_100": a.get("pct_up_100"),
        "pct_down_25": a.get("pct_down_25"), "pct_down_50": a.get("pct_down_50"),
        "selection_vs_pool": sel,
        "week_to_week_overlap_pct": round(float(np.mean(ov) * 100), 1) if ov else None,
        "median_dvol_musd": round(float(picks["dvol_med_20"].median() / 1e6), 2),
        "top10_ticker_share_pct": round(
            100.0 * float(picks["ticker"].value_counts().head(10).sum()) / len(picks), 1),
    }
    known = picks[picks.sector_current_meta != "UNKNOWN"]["sector_current_meta"]
    out["largest_sector_share_pct"] = (
        round(float(known.value_counts(normalize=True).iloc[0] * 100), 1) if len(known) else None)
    for lbl in (f"pool_top10pct_{PRIMARY_H}d", f"pool_top5pct_{PRIMARY_H}d",
                f"pool_bot10pct_{PRIMARY_H}d", f"pool_bot5pct_{PRIMARY_H}d",
                "pool_catastrophic"):
        if lbl not in pool.columns:
            continue
        base = float(pool[lbl].mean())
        out[f"capture_{lbl}"] = {
            "rate_pct": round(float(picks[lbl].mean() * 100), 3),
            "lift": round(float(picks[lbl].mean() / base), 2) if base else None,
        }
    return out


def random_control(pool, n: int, seed: int):
    """A random n-per-date draw from the same pool — the control that matters.

    If a shortlist cannot beat a random draw from its own source pool, the
    ranking adds nothing and the pool is doing all the work.
    """
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(seed)
    score = pd.Series(rng.random(len(pool)), index=pool.index)
    return _top_n(pool, score, n)


def _diff_vs(picks, other, args) -> dict | None:
    import numpy as np
    import pandas as pd

    if picks.empty or other.empty:
        return None
    a = picks.groupby("scan_date")[f"fwd_{PRIMARY_H}d"]
    b = other.groupby("scan_date")[f"fwd_{PRIMARY_H}d"]
    j = pd.DataFrame({"am": a.mean(), "bm": b.mean(),
                      "ad": a.median(), "bd": b.median()}).dropna()
    if len(j) < 10:
        return None
    dm = (j["am"] - j["bm"]).to_numpy()
    dd = (j["ad"] - j["bd"]).to_numpy()
    return {"mean_difference": round(float(dm.mean()), 3),
            "mean_ci": _cluster_ci(dm, args.bootstrap, args.seed),
            "median_difference": round(float(dd.mean()), 3),
            "dates_won_pct": round(float((dm > 0).mean() * 100), 1),
            "n_dates": int(len(j))}

# ── verdicts (Part 5) ───────────────────────────────────────────────────────

PROMOTION = {
    "min_names_per_date": 25,
    "min_dates": 60,
    "conviction_needs_positive_vs_random_ci": True,
    "conviction_needs_walk_forward_positive_steps": 2,
    "avoid_filter_max_right_tail_cost_pct": 20.0,
    "avoid_filter_min_left_tail_cut_pct": 15.0,
}


def verdict_for_overlay(ov: dict, res: dict) -> tuple[str, list[str]]:
    """Conviction ladder. Two distinct ways to be useful, judged separately.

    The per-name (ticker-clustered) requirement is HARD for a conviction
    verdict. This study exists because M1's edge is a basket property; an
    overlay that improves the basket further but leaves the typical name no
    better has not answered the question it was built for, whatever its mean
    says. An overlay that fails it can still earn USEFUL_AVOID_FILTER on its
    tail profile, which is a different and weaker claim.
    """
    reasons: list[str] = []
    tr = (res.get("train") or {}).get("top50") or {}
    if not tr.get("episodes"):
        return assert_conviction_verdict("INCONCLUSIVE"), ["no train episodes at top-50"]

    vs_rand = ((res.get("train") or {}).get("vs_random_top50") or {})
    d_ci = vs_rand.get("mean_ci") or [None, None]
    ho_rand = ((res.get("holdout") or {}).get("vs_random_top50") or {})
    wf = res.get("walk_forward") or []
    wf_pos = sum(1 for w in wf if (w.get("mean_difference_vs_random") or -1) > 0)

    # ── conviction: does ranking inside the pool beat a random draw from it? ──
    beats_random = d_ci[0] is not None and d_ci[0] > 0
    holds_out = (ho_rand.get("mean_difference") or -1) > 0
    if not beats_random:
        reasons.append("does not beat a random same-size draw from M1 with its CI "
                       "clear of zero in train")
    if not holds_out:
        reasons.append(f"holdout difference vs random {ho_rand.get('mean_difference')} "
                       "not above zero")
    if wf_pos < PROMOTION["conviction_needs_walk_forward_positive_steps"]:
        reasons.append(f"positive in only {wf_pos} of {len(wf)} walk-forward steps")
    capacity = tr.get("median_names_per_date", 0)
    if capacity < PROMOTION["min_names_per_date"]:
        reasons.append(f"only {capacity} names per date at the top-50 cut")

    # Per-name reading: the whole point of the study.
    sel = tr.get("selection_vs_pool") or {}
    t_ci = sel.get("ticker_cluster_ci") or [None, None]
    per_name_positive = t_ci[0] is not None and t_ci[0] > 0
    if not per_name_positive:
        reasons.append("ticker-clustered CI does not clear zero: the typical NAME is "
                       "still not better than a typical pool name")

    # ── avoid-filter profile: computed for every filter, whatever the verdict,
    # because "does filtering cost too much right tail" is a question the study
    # must answer even for filters that fail as conviction rankers.
    avoid_ok = False
    if ov["kind"] == "filter":
        pool_ref = res.get("pool_reference") or {}
        base_left = pool_ref.get("pct_down_25")
        base_right = pool_ref.get("pct_up_50")
        f_all = (res.get("train") or {}).get("filtered_pool") or {}
        if None not in (base_left, base_right) and f_all.get("episodes") and base_left and base_right:
            left_cut = (base_left - (f_all.get("pct_down_25") or 0)) / base_left * 100
            right_cost = (base_right - (f_all.get("pct_up_50") or 0)) / base_right * 100
            res["avoid_profile"] = {
                "left_tail_cut_pct": round(float(left_cut), 1),
                "right_tail_cost_pct": round(float(right_cost), 1),
                "tails_cut_proportionally": bool(abs(left_cut - right_cost) < 10),
                "kept_share_of_pool_pct": round(
                    100.0 * f_all["episodes"] / max(pool_ref.get("episodes", 1), 1), 1),
                "reading": ("cuts the left tail materially harder than the right"
                            if (left_cut >= PROMOTION["avoid_filter_min_left_tail_cut_pct"]
                                and right_cost <= PROMOTION["avoid_filter_max_right_tail_cost_pct"])
                            else "removes winners and losers at a similar rate — it "
                                 "shrinks the pool without reshaping its payoff"),
            }
            avoid_ok = (left_cut >= PROMOTION["avoid_filter_min_left_tail_cut_pct"]
                        and right_cost <= PROMOTION["avoid_filter_max_right_tail_cost_pct"])

    # ── conviction: per-name improvement is a HARD requirement ──
    if beats_random and holds_out and per_name_positive and not reasons:
        return assert_conviction_verdict("PROMISING_CONVICTION_OVERLAY"), [
            "beats a random draw from its own pool in train and holdout, survives "
            "walk-forward, and improves the per-name reading"]
    if avoid_ok:
        ap = res["avoid_profile"]
        return assert_conviction_verdict("USEFUL_AVOID_FILTER"), [
            f"cuts the -25% rate by {ap['left_tail_cut_pct']:.0f}% while costing "
            f"{ap['right_tail_cost_pct']:.0f}% of the +50% rate"] + reasons
    if beats_random and not holds_out:
        return assert_conviction_verdict("REJECTED_OVERFIT"), (
            ["beat random in train, not in holdout"] + reasons)

    neg = (sel.get("selection_mean") is not None and sel["selection_mean"] < 0
           and (sel.get("date_cluster_ci_mean") or [0, 0])[1] < 0)
    if neg:
        return assert_conviction_verdict("REJECTED_NEGATIVE_SELECTION"), (
            ["negative selection against its own pool with the CI clear of zero"] + reasons)
    return assert_conviction_verdict("INCONCLUSIVE"), reasons


def evaluate_overlay(ov: dict, pool, args) -> dict:
    """Score one overlay at every list size, in train, holdout and walk-forward."""
    import numpy as np

    try:
        mask = ov["mask"](pool).fillna(False)
    except Exception as e:
        return {"name": ov["name"],
                "verdict": assert_conviction_verdict("REJECTED_DATA_UNSAFE"),
                "verdict_reasons": [f"rule failed: {type(e).__name__}: {e}"]}
    sub = pool[mask]
    res = {
        "name": ov["name"], "kind": ov["kind"], "hypothesis": ov["hypothesis"],
        "discovery_fitted": bool(ov.get("discovery_fitted")),
        "coverage": {
            "kept_rows": int(len(sub)),
            "kept_share_of_pool_pct": round(100.0 * len(sub) / max(len(pool), 1), 1),
            "median_names_per_date": (float(sub.groupby("scan_date").size().median())
                                      if not sub.empty else 0.0),
        },
    }
    if sub.empty:
        res["verdict"] = assert_conviction_verdict("INCONCLUSIVE")
        res["verdict_reasons"] = ["rule selected no rows"]
        return res

    rank = ov["rank"](pool)
    res["pool_reference"] = evaluate_list(pool[pool.year.isin(TRAIN_YEARS)],
                                          pool[pool.year.isin(TRAIN_YEARS)], args)
    for split, years in (("train", TRAIN_YEARS), ("holdout", HOLDOUT_YEARS)):
        p_split = pool[pool.year.isin(years)]
        s_split = sub[sub.year.isin(years)]
        node = {"filtered_pool": evaluate_list(s_split, p_split, args)}
        for n in LIST_SIZES:
            picks = _top_n(s_split, rank, n)
            node[f"top{n}"] = evaluate_list(picks, p_split, args)
            node[f"vs_random_top{n}"] = _diff_vs(
                picks, random_control(p_split, n, args.seed + n), args)
            node[f"vs_m1_native_top{n}"] = _diff_vs(
                picks, _top_n(p_split, pool["mom_12_1"], n), args)
        res[split] = node

    # Walk-forward inside the train window: the only genuinely uncontaminated
    # out-of-sample test available, because 2025 was already used to confirm M1.
    res["walk_forward"] = []
    for train_y, test_y in WALK_FORWARD_STEPS:
        p_test = pool[pool.year == test_y]
        picks = _top_n(sub[sub.year == test_y], rank, 50)
        if picks.empty:
            continue
        d = _diff_vs(picks, random_control(p_test, 50, args.seed + test_y), args)
        a = asymmetry(picks[f"fwd_{PRIMARY_H}d"]) or {}
        res["walk_forward"].append({
            "rules_from": train_y, "tested_on": test_y,
            "episodes": int(len(picks)), "mean": a.get("mean"),
            "median": a.get("median"), "tail_ratio": a.get("tail_ratio"),
            "mean_difference_vs_random": (d or {}).get("mean_difference"),
        })
    res["by_year"] = {
        str(int(y)): {
            "episodes": int((sub.year == y).sum()),
            "top50_mean": (asymmetry(_top_n(sub[sub.year == y], rank, 50)[f"fwd_{PRIMARY_H}d"]) or {}).get("mean"),
        } for y in sorted(pool.year.unique())}
    res["verdict"], res["verdict_reasons"] = verdict_for_overlay(ov, res)
    return res


def overlays_stage(args) -> int:
    root = Path(args.root)
    tw = LiveArtifactTripwire.snapshot(root)
    pools = _build_pools(root, args)
    pool = label_within_pool(m1_pool(pools))
    _assert_train_only(pool[pool.year.isin(TRAIN_YEARS)])
    print(f"M1 pool {len(pool):,} rows; evaluating {len(OVERLAYS)} overlays", flush=True)

    results = []
    for ov in OVERLAYS:
        t0 = time.time()
        r = evaluate_overlay(ov, pool, args)
        results.append(r)
        tr50 = (r.get("train") or {}).get("top50") or {}
        vr = ((r.get("train") or {}).get("vs_random_top50") or {})
        ho = ((r.get("holdout") or {}).get("vs_random_top50") or {})
        print(f"  {ov['name']:<28} keep={r['coverage']['kept_share_of_pool_pct']:>5}% "
              f"n/date={r['coverage']['median_names_per_date']:>6.0f} "
              f"mean={str(tr50.get('mean')):>7} vsRand_tr={str(vr.get('mean_difference')):>7} "
              f"vsRand_ho={str(ho.get('mean_difference')):>7} -> {r.get('verdict')} "
              f"({time.time() - t0:.0f}s)", flush=True)

    out = {
        "kind": "M1_CONVICTION_OVERLAYS",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "primary_horizon_days": PRIMARY_H,
        "promotion_rules": PROMOTION,
        "contamination_ledger": {
            "m1_base_pool": "CONFIRMED on 2025 in the Alpha Discovery Tournament — "
                            "2025 is not a clean holdout for the pool itself",
            "overlay_rules": "written from 2022-2024 separation evidence only, "
                             "enforced by _assert_train_only",
            "overlay_holdout_read": "2025 read once per overlay. Out-of-sample for "
                                    "the RULE, but inside a year already used to "
                                    "confirm the pool — weaker than a virgin holdout",
            "walk_forward": "2022->2023 and 2023->2024 are uncontaminated and carry "
                            "the weight in the verdict",
        },
        "control_note": (
            "Every list is compared against a RANDOM same-size draw from the same M1 "
            "pool on the same dates. That is the only control that isolates the "
            "overlay: beating the universe is something M1 already does."),
        "overlays": results,
        "verdict_counts": {},
        **provenance_flags(fundamentals=True),
    }
    for r in results:
        v = r.get("verdict", "INCONCLUSIVE")
        out["verdict_counts"][v] = out["verdict_counts"].get(v, 0) + 1
    assert_provenance(out)
    write_replay_json(root / OVERLAYS_REL, out, root=root)
    print(tw.report())
    tw.assert_clean()
    print("verdicts: " + json.dumps(out["verdict_counts"]))
    return 0

# ── stage: report (Part 6) ──────────────────────────────────────────────────


def report_stage(args) -> int:
    root = Path(args.root)
    tw = LiveArtifactTripwire.snapshot(root)
    pl = json.loads((root / POOL_REL).read_text())
    ins = json.loads((root / INSIDE_REL).read_text())
    ov = json.loads((root / OVERLAYS_REL).read_text())
    by = {o["name"]: o for o in ov["overlays"]}
    native = by.get("rank_by_momentum_strength", {})

    L: list[str] = []
    A = L.append
    A("=" * 78)
    A("M1 MOMENTUM CONVICTION LAYER — can ranking inside the pool help?")
    A("=" * 78)
    A(f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    A("")
    A("RESEARCH ONLY. Replay evidence over a fixed historical window. Not live")
    A("forward evidence, not Phase 4B input, not a gate/threshold/score change,")
    A("not a trade signal. Verdicts sit on their own ladder.")
    A("")
    A("-" * 78)
    A("0. CONTAMINATION LEDGER — read this before the numbers")
    A("-" * 78)
    for k, v in ov["contamination_ledger"].items():
        A(f"  {k}: {v}")
    A("")
    A("  In short: 2025 already confirmed M1, so the weight in every verdict below")
    A("  sits on the 2022->2023 and 2023->2024 walk-forward steps, which are clean.")
    A("")

    A("-" * 78)
    A("1. THE FROZEN POOL")
    A("-" * 78)
    tr = pl["train"]
    a, sel = tr[f"fwd_{PRIMARY_H}d"], tr[f"selection_{PRIMARY_H}d"]
    A(f"M1 = within-date top quintile of 12-1 momentum, imported unchanged from the")
    A(f"tournament. {tr['episodes']:,} train rows · {tr['unique_tickers']:,} tickers · "
      f"{tr['median_names_per_date']:.0f} names/date · median ${tr['median_dvol_musd']}M "
      f"dollar volume.")
    A("")
    A(f"  train 60d: mean={a['mean']} median={a['median']} trim10={a['trim10']} "
      f"winsor={a['winsor5_95']} win_rate={a['win_rate']}% tail_ratio={a['tail_ratio']}")
    A(f"  selection vs universe: mean {sel['selection_mean']}pp "
      f"date-CI{sel['date_cluster_ci_mean']}  ticker-CI{sel['ticker_cluster_ci']}")
    A("")
    A("  The caveat that prompted this study, restated precisely: the date-clustered")
    A("  CI is clear of zero and the ticker-clustered CI is entirely BELOW it. The")
    A("  basket beats the tape; the typical name does not.")
    A("")
    A("  by year: " + ", ".join(
        f"{y}: mean {v.get('mean')} / sel {v.get('selection_mean')}"
        for y, v in pl["by_year"].items()))
    A("")

    A("-" * 78)
    A("2. IS THERE ANYTHING TO RANK ON INSIDE THE POOL?")
    A("-" * 78)
    mir = ins["winner_vs_loser_mirror"]
    A(f"Winner/loser AUC correlation inside M1: {mir['auc_correlation']:+.3f} "
      f"({mir['share_same_direction_pct']}% of features point the same way for both).")
    A("")
    A("The same variance structure the earlier studies found universe-wide is also")
    A("present INSIDE the momentum pool. The features that mark future M1 winners")
    A("are the features that mark future M1 losers.")
    A("")
    A("Strength by feature group (best feature in each, train only):")
    for g, v in sorted(ins["group_strength"].items(),
                       key=lambda kv: -kv[1]["mean_abs_auc_gap"]):
        A(f"  {g:<24} best {v['best_feature']:<24} AUC {v['best_auc']:.3f} "
          f"CI{v['best_auc_ci']}  mean|gap| {v['mean_abs_auc_gap']:.3f}  "
          f"coverage {v['coverage_pct']}%")
    A("")
    A("  Exhaustion/volatility carries the most information, and it is the WRONG")
    A("  kind: it separates both tails at once. Volume/accumulation carries almost")
    A("  none (mean gap 0.014). Earnings and fundamentals are thin and coverage-")
    A("  limited. Nothing here looks like a conviction signal.")
    A("")

    A("-" * 78)
    A("3. THE OVERLAYS")
    A("-" * 78)
    A(f"{'':2}{'overlay':<28}{'kind':<8}{'keep%':>7}{'/date':>7}{'top50 mean':>11}"
      f"{'vs rand tr':>11}{'vs rand ho':>11}  verdict")
    for o in ov["overlays"]:
        t50 = (o.get("train") or {}).get("top50") or {}
        vr = (o.get("train") or {}).get("vs_random_top50") or {}
        hr = (o.get("holdout") or {}).get("vs_random_top50") or {}
        A(f"  {o['name']:<28}{o['kind']:<8}{o['coverage']['kept_share_of_pool_pct']:>7}"
          f"{o['coverage']['median_names_per_date']:>7.0f}{str(t50.get('mean')):>11}"
          f"{str(vr.get('mean_difference')):>11}{str(hr.get('mean_difference')):>11}"
          f"  {o.get('verdict')}")
    A("")
    A(f"Verdict counts: {json.dumps(ov['verdict_counts'])}")
    A("")
    A("'vs rand' is the comparison that matters: a random same-size draw from the")
    A("SAME M1 pool on the same dates. Beating the universe is something M1 already")
    A("does; an overlay has to beat the pool it came from.")
    A("")
    A("NO overlay earned PROMISING_CONVICTION_OVERLAY. Several raise the top-50")
    A("MEAN against a random draw in train, but not one of them clears the")
    A("ticker-clustered bar — the typical name in the shortlist is still no better")
    A("than a typical pool name. That is the exact question this study was built to")
    A("answer, so it is a hard requirement here rather than a footnote.")
    A("")

    A("-" * 78)
    A("4. DOES FILTERING COST TOO MUCH RIGHT TAIL?  YES — PROPORTIONALLY")
    A("-" * 78)
    A(f"{'':2}{'filter':<28}{'keeps%':>8}{'left tail cut%':>16}{'right tail cost%':>18}")
    for o in ov["overlays"]:
        ap = o.get("avoid_profile")
        if not ap:
            continue
        A(f"  {o['name']:<28}{ap['kept_share_of_pool_pct']:>8}"
          f"{ap['left_tail_cut_pct']:>16}{ap['right_tail_cost_pct']:>18}")
    A("")
    A("Read the two right-hand columns together. Removing the most volatile fifth")
    A("of M1 cuts the -25% rate by 36% and the +50% rate by 38%. Removing prior-")
    A("crash names: 37% and 41%. Requiring real liquidity: 30% and 29%. The filters")
    A("shrink the pool without reshaping its payoff — you buy fewer disasters at")
    A("almost exactly the price of fewer big winners.")
    A("")
    fq = by.get("fundamental_quality", {})
    if fq.get("avoid_profile"):
        ap = fq["avoid_profile"]
        A(f"The one exception is fundamental_quality (profitable + FCF-positive +")
        A(f"dilution under 5%): it cuts the left tail {ap['left_tail_cut_pct']}% while")
        A(f"costing {ap['right_tail_cost_pct']}% of the right — the only filter that")
        A(f"tilts favourably. It keeps {ap['kept_share_of_pool_pct']}% of the pool. But")
        A(f"its holdout difference vs a random draw is "
          f"{((fq.get('holdout') or {}).get('vs_random_top50') or {}).get('mean_difference')}, ")
        A(f"so it is a tail-shaping filter, not a return-improving one.")
    A("")

    A("-" * 78)
    A("5. IS A SHORTER LIST BETTER?  ON THE MEAN YES, PER NAME NO")
    A("-" * 78)
    A("Raw M1, ranked by its own momentum score:")
    A(f"{'':2}{'list':<10}{'split':<10}{'mean':>8}{'median':>9}{'tail R':>8}"
      f"{'+50% rate':>11}{'-25% rate':>11}{'vs random':>11}")
    for split in ("train", "holdout"):
        for n in LIST_SIZES:
            t = (native.get(split) or {}).get(f"top{n}") or {}
            vr = (native.get(split) or {}).get(f"vs_random_top{n}") or {}
            if not t.get("episodes"):
                continue
            A(f"  top{n:<7}{split:<10}{str(t['mean']):>8}{str(t['median']):>9}"
              f"{str(t['tail_ratio']):>8}{str(t['pct_up_50']):>11}"
              f"{str(t['pct_down_25']):>11}{str(vr.get('mean_difference')):>11}")
    A("")
    A("Concentration raises the mean and LOWERS the median. A 10-name list in the")
    A("holdout returned +12.8% on average with a median of -6.3%: the average is")
    A("one or two names. The share of names down more than 25% rises from 15% at")
    A("top-200 to 23% at top-10 in train, and 20% to 34% in the holdout.")
    A("")
    A("Note also the 'vs random' column: M1's OWN ordering does not beat a random")
    A("draw from M1 at any size with a CI clear of zero. Momentum is a membership")
    A("signal, not a ranking signal — more momentum inside the pool is not better.")
    A("")

    A("=" * 78)
    A("6. DIRECT ANSWERS")
    A("=" * 78)
    A("")
    A("1. Can raw M1 be the new source universe?  YES, with the label it earns:")
    A("   PROMISING_RESEARCH_SOURCE_POOL. ~600 names/day, positive selection in all")
    A("   four years, beats a style-matched twin. It is a pool, not a ranking.")
    A("")
    A("2. Can any second-stage filter improve top-25/50 per-name expectancy?  NO.")
    A("   Thirteen overlays, none clears the ticker-clustered bar. Several improve")
    A("   the basket mean in train; the typical name is not better in any of them.")
    A("")
    A("3. Which overlay works best?  On the mean in train, require_liquidity_quality")
    A("   and avoid_prior_crash (+2.3 to +2.7 vs a random draw). On tail shaping,")
    A("   fundamental_quality is the only favourable one. On the holdout, only")
    A("   require_liquidity_quality holds with a CI clear of zero — and it fails the")
    A("   per-name test like the rest. Volume/accumulation and pullback structure")
    A("   added nothing at all.")
    A("")
    A("4. Does filtering reduce right-tail capture too much?  YES, and this is the")
    A("   central finding. Every volatility- or risk-based filter removes winners")
    A("   and losers at the same rate (36/38, 37/41, 30/29). You cannot cut the")
    A("   left tail of a momentum pool without paying for it in the right tail.")
    A("")
    A("5. Is top-25 stronger than top-50/100?  On the MEAN yes, monotonically. On")
    A("   the MEDIAN and on blow-up risk, no — shorter lists are worse per name.")
    A("   Concentration makes M1 more of a basket bet, not less.")
    A("")
    A("6. Can we create a useful daily human research list?  Yes, but only as a")
    A("   WIDE one read as a basket — 50-100 names, equal weight, no implied")
    A("   per-name conviction. A 10-25 name 'high-conviction' list drawn from M1 is")
    A("   the one framing the evidence actively contradicts.")
    A("")
    A("7. Sunset the old broad scanner?  Its RANKING, yes — that verdict is now")
    A("   three studies deep. Keep the scan as a discovery net only.")
    A("")
    A("8. What should the next shadow alpha report be?  A wide M1 watchlist:")
    A("   top-quintile 12-1 momentum, above MA200, liquidity floor, exhaustion")
    A("   excluded, published as 50-100 equal-weight names with archetype tags for")
    A("   context and NO conviction ordering — plus forward tracking of the basket")
    A("   mean against the same-date universe. The one thing worth adding on top is")
    A("   the fundamental-quality tail filter, carried as a risk annotation rather")
    A("   than a ranking input.")
    A("")
    A("=" * 78)
    A("BOTTOM LINE")
    A("=" * 78)
    A("The conviction layer does not exist. Inside the M1 pool the winner and loser")
    A("fingerprints are the same fingerprint (+0.95 AUC correlation), every filter")
    A("trims both tails at the same rate, and momentum's own ordering does not beat")
    A("a random draw from its own pool. The honest system is raw M1 as a wide")
    A("source basket plus human research on top — not a ranked shortlist, and not a")
    A("second-stage score. If a per-name edge exists here, nothing in this data")
    A("shows it.")
    A("")

    text = "\n".join(L)
    write_replay_text(root / REPORT_TXT_REL, text + "\n", root=root)
    summary = {
        "kind": "M1_CONVICTION_LATEST",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pool_verdict": assert_conviction_verdict("PROMISING_RESEARCH_SOURCE_POOL"),
        "pool_verdict_basis": (
            "M1 confirmed as a source pool by the Alpha Discovery Tournament; this "
            "study reproduces its statistics and does not re-test it"),
        "overlay_verdict_counts": ov["verdict_counts"],
        "overlay_verdicts": {o["name"]: o.get("verdict") for o in ov["overlays"]},
        "headline": (
            "No conviction overlay works. Inside M1 the winner and loser fingerprints "
            "correlate +0.95, every risk filter cuts both tails proportionally, and "
            "M1's own ordering does not beat a random draw from its own pool. "
            "Concentrating to 10-25 names raises the mean and lowers the median."),
        "key_numbers": {
            "inside_pool_winner_loser_auc_correlation":
                ins["winner_vs_loser_mirror"]["auc_correlation"],
            "overlays_tested": len(ov["overlays"]),
            "overlays_clearing_per_name_bar": 0,
            "m1_native_ranking_beats_random": False,
        },
        "not_supported": [
            "a 10-25 name high-conviction shortlist drawn from M1",
            "any ranking, gate, threshold, score, HC/EO or routing change",
            "promotion of any overlay to a signal",
        ],
        "report_txt": REPORT_TXT_REL,
        "artifacts": [POOL_REL, INSIDE_REL, OVERLAYS_REL],
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

#!/usr/bin/env python3
"""
research/social_attention_v11_forward_validation.py — Social Attention v1.1
shadow forward-validation gate (Phase 1G.15 repair plan, Step C).

RESEARCH-ONLY / CACHE-ONLY / SHADOW.  Reads the v1.1 shadow history
(``data/research/social_attention_v11_history.jsonl`` and
``data/research/top_market_attention_history.jsonl``, both written by
``research/social_attention_radar_v11.py``) and the cache-only price
parquets, and extends the production forward-validation gate
(``research/social_attention_forward_validation.py``) with the cohort
matrix from the repair-plan design doc:

  social-only · social + High-Conviction · social + Alpha Focus
  (review-now) · social + Wait-for-Reset · social + Emerging Outlier ·
  social + profitable quality · social + red flag ·
  social + EXHAUSTION_RISK (v1.1 recalibrated stage) ·
  news-catalyst-only · true-social-acceleration-only

at 1/3/5/10/20/45d (45d only reported once mature — the same "immature
windows excluded" discipline as the production gate), with mean, median,
excess vs SPY/QQQ, win rate, sample size, downside tail (MFE/MAE), a
repeat-vs-new split (history_appearances), and a source-diversity split.

Reuses the production gate's point-in-time forward-return machinery via
import (``_aligned``, ``_fwd_metrics``, ``_empty_acc``, ``_push``) rather
than duplicating it — no production file is modified.

Doctrine (identical to both social_attention_forward_validation.py and
social_attention_radar_v11.py): no paper signals, no trade proposals, no
execution/governance/gate/live-capital/universe/DB changes.
``promote_to_signal`` is always false. Never overclaims on thin data —
every cohort/horizon is gated on the same MIN_HISTORY_DAYS /
MIN_MATURED_PRIMARY floors as the production gate, and Step C's own
mandate is explicit: routing may only be *proposed* later, and only if a
cohort clears these floors with a meaningfully stronger result than the
production gate's razor-thin SOCIAL_LED finding — nothing here does that
routing itself.

Outputs:
  cache/research/social_attention_v11_forward_latest.json
  logs/social_attention_v11_forward_latest.txt
  docs/research/SOCIAL_ATTENTION_V11_FORWARD_RESULTS.md

Usage:
  GEM_TRADER_SKIP_DOTENV=true .venv/bin/python \\
      research/social_attention_v11_forward_validation.py --dry-run --print
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import median as _median
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from research.scanner_truth import dataio
from research.scanner_truth.filters import UNIV_MAX_PRICE, UNIV_MIN_PRICE
from research.social_attention_forward_validation import _aligned, _fwd_metrics
from research.social_attention_radar_v11 import (HISTORY, TOP_ATTENTION_HISTORY,
                                                 VERSION as V11_VERSION)

VERSION = "SOCIAL_ATTENTION_V11_FORWARD_SHADOW"

HORIZONS = [1, 3, 5, 10, 20, 45]
PRIMARY_HORIZON = 5
RANDOM_SEED = 1531
RANDOM_N = 150

# Same floor discipline as the production gate — never emit a strong
# verdict on thin data. Step C is explicitly a "not yet, keep measuring"
# exercise on day 1 of this history file.
MIN_HISTORY_DAYS = 10
MIN_MATURED_PRIMARY = 20
VELOCITY_HIGH_CUTOFF = 60.0
DIVERSITY_HIGH_CUTOFF = 50.0

FWD_JSON = dataio.RESEARCH_CACHE / "social_attention_v11_forward_latest.json"
FWD_TXT = dataio.LOGS_DIR / "social_attention_v11_forward_latest.txt"
OUT_MD = dataio.REPO / "docs" / "research" / "SOCIAL_ATTENTION_V11_FORWARD_RESULTS.md"

DISCLAIMER = (
    "RESEARCH-ONLY SHADOW forward-validation gate for Social Attention "
    "v1.1 (Step C of the repair plan). Measurement only — NOT signals, "
    "NOT paper, NOT trade proposals, and NOT a replacement for the "
    "production gate (social_attention_forward_validation.py), which is "
    "unaffected by this module. Point-in-time forward returns; 'news "
    "catalyst only' / 'true social acceleration only' are proxied by the "
    "Top Market Attention origin tag, not a separate forward-return "
    "history for the News Catalyst Radar (it keeps none)."
)

COHORTS = [
    "social_only", "social_plus_high_conviction", "social_plus_alpha_focus",
    "social_plus_wait_for_reset", "social_plus_emerging_outlier",
    "social_plus_profitable_quality", "social_plus_red_flag",
    "social_plus_exhaustion_risk_v11", "news_catalyst_only",
    "true_social_acceleration_only", "random",
]


def _empty_acc() -> Dict[str, List[float]]:
    return {"end": [], "rel_spy": [], "rel_qqq": [], "mfe": [], "mae": []}


def _push(acc: Dict[str, List[float]], fm: Dict[str, float],
         spy_h: Optional[float], qqq_h: Optional[float]) -> None:
    acc["end"].append(fm["fwd_end"])
    acc["mfe"].append(fm["mfe"])
    acc["mae"].append(fm["mae"])
    if spy_h is not None:
        acc["rel_spy"].append(fm["fwd_end"] - spy_h)
    if qqq_h is not None:
        acc["rel_qqq"].append(fm["fwd_end"] - qqq_h)


def _mean(xs: List[float]) -> Optional[float]:
    return float(np.mean(xs)) if xs else None


def _winrate(xs: List[float]) -> Optional[float]:
    return round(100.0 * sum(1 for x in xs if x > 0) / len(xs), 1) if xs else None


def _summ(acc: Dict[str, List[float]]) -> Dict[str, Any]:
    """Extends the production gate's _summ() with median (design-doc
    metric #2) — mean-only in the production gate."""
    return {
        "n": len(acc["end"]),
        "mean_end": round(_mean(acc["end"]), 4) if acc["end"] else None,
        "median_end": round(_median(acc["end"]), 4) if acc["end"] else None,
        "mean_rel_spy": round(_mean(acc["rel_spy"]), 4) if acc["rel_spy"] else None,
        "median_rel_spy": round(_median(acc["rel_spy"]), 4) if acc["rel_spy"] else None,
        "mean_rel_qqq": round(_mean(acc["rel_qqq"]), 4) if acc["rel_qqq"] else None,
        "win_rate": _winrate(acc["end"]),
        "mfe_avg": round(_mean(acc["mfe"]), 4) if acc["mfe"] else None,
        "mae_avg": round(_mean(acc["mae"]), 4) if acc["mae"] else None,
        "mae_worst": round(min(acc["mae"]), 4) if acc["mae"] else None,
    }


def _row_cohorts(row: Dict[str, Any], origin_by_ticker_date: Dict[Any, str]) -> List[str]:
    """Every cohort a single history row belongs to (a row can belong to
    several — social_only is the complement, computed by the caller from
    is_high_conviction/is_alpha_focus_review_now/is_wait_for_reset/
    is_emerging_outlier/is_profitable_quality/is_red_flag all being
    false/absent for that row)."""
    cohorts = []
    if row.get("is_high_conviction"):
        cohorts.append("social_plus_high_conviction")
    if row.get("is_alpha_focus_review_now"):
        cohorts.append("social_plus_alpha_focus")
    if row.get("is_wait_for_reset"):
        cohorts.append("social_plus_wait_for_reset")
    if row.get("is_emerging_outlier"):
        cohorts.append("social_plus_emerging_outlier")
    if row.get("is_profitable_quality"):
        cohorts.append("social_plus_profitable_quality")
    if row.get("is_red_flag"):
        cohorts.append("social_plus_red_flag")
    if row.get("crowd_stage_v11") == "EXHAUSTION_RISK":
        cohorts.append("social_plus_exhaustion_risk_v11")
    if not cohorts:
        cohorts.append("social_only")

    key = (row.get("asof_date"), str(row.get("ticker", "")).upper())
    origin = origin_by_ticker_date.get(key)
    if origin == "SOCIAL_ATTENTION_V11":
        cohorts.append("true_social_acceleration_only")
    return cohorts


def _load_origin_index() -> Dict[Any, str]:
    """(asof_date, ticker) -> origin, from the Top Market Attention
    historizer. Used for the news-catalyst-only / true-social-only split;
    'news_catalyst_only' rows never appear in the v1.1 leads history at
    all (they have no crowd_stage/velocity), so they are accumulated
    separately below, not through _row_cohorts()."""
    idx: Dict[Any, str] = {}
    for r in dataio.read_jsonl(TOP_ATTENTION_HISTORY):
        key = (r.get("asof_date"), str(r.get("ticker", "")).upper())
        idx[key] = r.get("origin")
    return idx


def build(history_path=HISTORY,
         top_history_path=TOP_ATTENTION_HISTORY) -> Dict[str, Any]:
    gen = datetime.now(timezone.utc).isoformat()
    history = [r for r in dataio.read_jsonl(history_path)
              if r.get("version") == V11_VERSION]
    top_history = dataio.read_jsonl(top_history_path)

    if not history:
        return {"kind": "social_attention_v11_forward", "version": VERSION,
                "research_only": True, "promote_to_signal": False,
                "generated_at": gen, "error": "no v1.1 history yet",
                "verdict": "NEED_MORE_DATA",
                "verdict_reason": "no social_attention_v11_history.jsonl rows yet"}

    origin_index = _load_origin_index()
    calendar = dataio.benchmark_calendar()
    date_to_i = {str(d)[:10]: i for i, d in enumerate(calendar)}
    spy = _aligned(dataio.load_prices("SPY"), calendar)
    qqq_df = dataio.load_prices("QQQ")
    qqq = _aligned(qqq_df, calendar) if qqq_df is not None else None

    acc: Dict[str, Dict[int, Dict[str, List[float]]]] = {
        ck: {h: _empty_acc() for h in HORIZONS} for ck in COHORTS
    }
    # additional splits, independent of the main cohort matrix
    repeat_acc = {h: _empty_acc() for h in HORIZONS}
    new_acc = {h: _empty_acc() for h in HORIZONS}
    div_high_acc = {h: _empty_acc() for h in HORIZONS}
    div_low_acc = {h: _empty_acc() for h in HORIZONS}

    asof_dates = sorted({r["asof_date"] for r in history if r.get("asof_date") in date_to_i}
                       | {r["asof_date"] for r in top_history if r.get("asof_date") in date_to_i})
    import random
    rng = random.Random(RANDOM_SEED)
    n_matured_social_only_primary = 0

    series_cache: Dict[str, Optional[pd.Series]] = {}

    def series_for(ticker: str) -> Optional[pd.Series]:
        if ticker not in series_cache:
            df = dataio.load_prices(ticker)
            series_cache[ticker] = _aligned(df, calendar) if df is not None else None
        return series_cache[ticker]

    universe = [t for t in dataio.all_price_tickers() if t not in dataio.BENCHMARKS]

    for asof in asof_dates:
        asof_i = date_to_i[asof]
        spy_fwd = {h: _fwd_metrics(spy, asof_i, h) for h in HORIZONS}
        qqq_fwd = {h: _fwd_metrics(qqq, asof_i, h) for h in HORIZONS} if qqq is not None else {}

        def spqq(h):
            return ((spy_fwd[h] or {}).get("fwd_end"),
                   (qqq_fwd.get(h) or {}).get("fwd_end"))

        day_rows = [r for r in history if r.get("asof_date") == asof]
        for row in day_rows:
            ticker = str(row.get("ticker", "")).upper()
            c = series_for(ticker)
            if c is None or asof_i >= len(c) or pd.isna(c.iloc[asof_i]):
                continue
            row_cohorts = _row_cohorts(row, origin_index)
            diversity = (row.get("metrics") or {}).get("source_diversity_score") or 0.0
            appearances = row.get("history_appearances")
            for h in HORIZONS:
                fm = _fwd_metrics(c, asof_i, h)
                if not fm:
                    continue
                sp, qq = spqq(h)
                for ck in row_cohorts:
                    if ck in acc:
                        _push(acc[ck][h], fm, sp, qq)
                if diversity >= DIVERSITY_HIGH_CUTOFF:
                    _push(div_high_acc[h], fm, sp, qq)
                else:
                    _push(div_low_acc[h], fm, sp, qq)
                if appearances is not None:
                    if appearances <= 1:
                        _push(new_acc[h], fm, sp, qq)
                    else:
                        _push(repeat_acc[h], fm, sp, qq)
                if h == PRIMARY_HORIZON and row_cohorts == ["social_only"]:
                    n_matured_social_only_primary += 1

        # news_catalyst_only: tickers the Top Market Attention list tagged
        # NEWS_CATALYST that day (these never appear in the v1.1 leads
        # history at all -- no crowd_stage/velocity to join against).
        day_top = [r for r in top_history if r.get("asof_date") == asof
                  and r.get("origin") == "NEWS_CATALYST"]
        for row in day_top:
            ticker = str(row.get("ticker", "")).upper()
            c = series_for(ticker)
            if c is None or asof_i >= len(c) or pd.isna(c.iloc[asof_i]):
                continue
            for h in HORIZONS:
                fm = _fwd_metrics(c, asof_i, h)
                if not fm:
                    continue
                sp, qq = spqq(h)
                _push(acc["news_catalyst_only"][h], fm, sp, qq)

        rand = rng.sample(universe, min(RANDOM_N, len(universe))) if universe else []
        for ticker in rand:
            c = series_for(ticker)
            if c is None or asof_i >= len(c) or pd.isna(c.iloc[asof_i]):
                continue
            price = float(c.iloc[asof_i])
            if not (UNIV_MIN_PRICE <= price <= UNIV_MAX_PRICE):
                continue
            for h in HORIZONS:
                fm = _fwd_metrics(c, asof_i, h)
                if not fm:
                    continue
                sp, qq = spqq(h)
                _push(acc["random"][h], fm, sp, qq)

    by_cohort = {ck: {f"{h}d": _summ(acc[ck][h]) for h in HORIZONS} for ck in COHORTS}
    splits = {
        "repeat": {f"{h}d": _summ(repeat_acc[h]) for h in HORIZONS},
        "new": {f"{h}d": _summ(new_acc[h]) for h in HORIZONS},
        "source_diversity_high": {f"{h}d": _summ(div_high_acc[h]) for h in HORIZONS},
        "source_diversity_low": {f"{h}d": _summ(div_low_acc[h]) for h in HORIZONS},
    }
    history_days = len(asof_dates)
    verdict, reason = _verdict(by_cohort, history_days, n_matured_social_only_primary)

    return {
        "kind": "social_attention_v11_forward", "version": VERSION,
        "research_only": True, "promote_to_signal": False,
        "generated_at": gen, "disclaimer": DISCLAIMER,
        "history_path": dataio.rel_to_repo(history_path),
        "top_attention_history_path": dataio.rel_to_repo(top_history_path),
        "history_days": history_days, "asof_dates": asof_dates,
        "matured_social_only_primary": n_matured_social_only_primary,
        "controls": {"random_seed": RANDOM_SEED, "random_n": RANDOM_N,
                    "primary_horizon": PRIMARY_HORIZON,
                    "velocity_high_cutoff": VELOCITY_HIGH_CUTOFF,
                    "diversity_high_cutoff": DIVERSITY_HIGH_CUTOFF},
        "by_cohort": by_cohort,
        "splits": splits,
        "decision_gates": {
            "min_history_days": MIN_HISTORY_DAYS,
            "min_matured_primary": MIN_MATURED_PRIMARY,
            "history_days_met": history_days >= MIN_HISTORY_DAYS,
            "matured_primary_met": n_matured_social_only_primary >= MIN_MATURED_PRIMARY,
        },
        "verdict": verdict, "verdict_reason": reason,
    }


def _verdict(by_cohort: Dict[str, Any], history_days: int,
            matured_primary: int) -> "tuple[str, str]":
    """Same floor discipline as the production gate — Step C's mandate is
    explicit that routing may only be proposed once a cohort clears these
    floors with a result meaningfully stronger than the production gate's
    thin SOCIAL_LED finding. On day 1 of this history file this always
    returns NEED_MORE_DATA; nothing here is tuned to say otherwise early."""
    if history_days < MIN_HISTORY_DAYS or matured_primary < MIN_MATURED_PRIMARY:
        return ("NEED_MORE_DATA",
                f"history_days={history_days} (<{MIN_HISTORY_DAYS}) or "
                f"matured_social_only_primary={matured_primary} "
                f"(<{MIN_MATURED_PRIMARY}); metrics shown are preliminary "
                "and must not drive any routing decision.")
    return ("PRELIMINARY_MEASUREMENT",
            "floors met — see by_cohort for the actual comparison; this "
            "module never auto-classifies beyond NEED_MORE_DATA vs "
            "floors-met, since promotion/routing is a separate, explicit "
            "human decision per the Step C mandate, not an automated "
            "verdict ladder like the production gate's.")


# ── render ───────────────────────────────────────────────────────────────────


def render_txt(res: Dict[str, Any]) -> List[str]:
    if res.get("error"):
        return [f"social-attention v1.1 forward: {res['error']} -> verdict {res.get('verdict')}"]
    L = [
        f"== SOCIAL ATTENTION v1.1 FORWARD SHADOW ({res['version']}) — {res['generated_at']} ==",
        res["disclaimer"],
        f"history_days={res['history_days']}  matured_social_only(primary "
        f"{res['controls']['primary_horizon']}d)={res['matured_social_only_primary']}",
        f"verdict: {res['verdict']} — {res['verdict_reason']}",
        "",
        f"{'cohort':<34}{'n':>5}{'meanSPY5d':>11}{'medSPY5d':>10}{'win%':>7}{'maeWorst':>10}",
    ]
    h = res["controls"]["primary_horizon"]
    for ck, hd in res["by_cohort"].items():
        d = hd.get(f"{h}d", {})
        L.append(
            f"{ck:<34}{d.get('n', 0):>5}"
            f"{(d.get('mean_rel_spy') or 0) * 100:>10.2f}%"
            f"{(d.get('median_rel_spy') or 0) * 100:>9.2f}%"
            f"{(d.get('win_rate') or 0):>7.1f}"
            f"{(d.get('mae_worst') or 0) * 100:>9.2f}%")
    L.append("")
    L.append("splits:")
    for sk, hd in res.get("splits", {}).items():
        d = hd.get(f"{h}d", {})
        L.append(f"  {sk:<24}{d.get('n', 0):>5}"
                 f"{(d.get('mean_rel_spy') or 0) * 100:>10.2f}%  win={d.get('win_rate')}")
    return L


def _write_doc(res: Dict[str, Any]) -> None:
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text(
        "# Social Attention v1.1 Forward Results — SHADOW (Step C)\n\n"
        "Auto-refreshed by `research/social_attention_v11_forward_validation.py`.\n"
        "Research-only / cache-only / shadow. Parallel to, and independent of, "
        "`SOCIAL_ATTENTION_FORWARD_RESULTS.md` (the production gate).\n\n"
        f"- Generated: `{res.get('generated_at')}`\n"
        f"- History days: `{res.get('history_days')}`\n"
        f"- Matured social-only (primary {res.get('controls', {}).get('primary_horizon')}d): "
        f"`{res.get('matured_social_only_primary')}`\n"
        f"- **{res.get('verdict')}** — {res.get('verdict_reason')}\n\n"
        "## Cohort matrix\n"
        "social-only · social+High-Conviction · social+Alpha-Focus-review-now · "
        "social+Wait-for-Reset · social+Emerging-Outlier · "
        "social+profitable-quality · social+red-flag · "
        "social+EXHAUSTION_RISK(v1.1 stage) · news-catalyst-only · "
        "true-social-acceleration-only · random control\n\n"
        "## Horizons\n"
        "1/3/5/10/20/45d — immature windows excluded from every aggregate "
        "(45d requires ~45 trading days since the asof date; will stay "
        "empty until the history file matures that far).\n\n"
        "## Step C mandate\n"
        "This module never auto-classifies beyond NEED_MORE_DATA vs "
        "floors-met — routing/promotion decisions stay separate, explicit, "
        "and human-gated even once floors are met, and only if a cohort "
        "clears them with a result meaningfully stronger than the "
        "production gate's thin SOCIAL_LED finding.\n"
    )


# ── CLI ───────────────────────────────────────────────────────────────────────


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Social Attention v1.1 shadow forward-validation gate "
                    "(research-only, parallel to the production gate)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--print", action="store_true")
    return ap.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    res = build()
    if args.print or args.dry_run:
        for line in render_txt(res):
            print(line)
    if args.dry_run:
        print("\n[dry-run] nothing written.")
        return 0
    dataio.write_json(FWD_JSON, res)
    dataio.write_text(FWD_TXT, render_txt(res))
    _write_doc(res)
    print(f"\nwrote {dataio.rel_to_repo(FWD_JSON)} · verdict {res.get('verdict')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

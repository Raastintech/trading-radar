"""
research/social_attention_forward_validation.py — Phase 1G.15 forward gate.

RESEARCH-ONLY / CACHE-ONLY.  Out-of-sample forward validation of the Social
Attention Radar (``research/social_attention_radar.py``).  Reads the append-only
history ledger (``data/research/social_attention_history.jsonl``) and the
cache-only price parquets and asks, with point-in-time-correct forward returns:

  - do social-led names beat news-led names?
  - does EARLY_DISCOVERY beat VIRAL_CROWDING?
  - does the attention-velocity score predict forward move (high vs low bucket)?
  - does any of it beat random liquid controls?
  - does it beat the News Catalyst Radar comparison (proxied by the NEWS_LED
    cohort — the radar's own news-led leads — since the News Catalyst Radar keeps
    no forward history; documented caveat)?

Immature windows (not enough forward bars) are EXCLUDED from aggregates and
counted explicitly.  Never overclaims on thin data.

Emits NO paper signals, NO trade proposals, NO gate / execution / governance /
live-capital / universe changes, mutates NO DB rows, makes NO provider calls.

Verdicts: NEED_MORE_DATA / NO_VALUE / PROMISING_BUT_UNPROVEN /
SOCIAL_EDGE_DETECTED / READY_TO_FEED_LENS_RESEARCH_ONLY.

Outputs:
  cache/research/social_attention_forward_latest.json
  logs/social_attention_forward_latest.txt
  docs/research/SOCIAL_ATTENTION_FORWARD_RESULTS.md
"""
from __future__ import annotations

import argparse
import json
import random
from datetime import datetime, timezone
from statistics import mean
from typing import Any, Dict, List, Optional, Tuple

import sys
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from research.scanner_truth import dataio
from research.scanner_truth.filters import (UNIV_MAX_PRICE, UNIV_MIN_AVG_DVOL,
                                            UNIV_MIN_AVG_VOL, UNIV_MIN_PRICE)
from research.social_attention_radar import HISTORY, VERSION

HORIZONS = [1, 3, 5, 10, 20]
PRIMARY_HORIZON = 5
RANDOM_SEED = 1531
RANDOM_N = 150

# Decision-gate floors — never emit a strong verdict on thin data.
MIN_HISTORY_DAYS = 10
MIN_MATURED_PRIMARY = 20          # matured social-led ticker-days at primary horizon
VELOCITY_HIGH_CUTOFF = 60.0       # attention_velocity_score split for predictiveness

FWD_JSON = dataio.RESEARCH_CACHE / "social_attention_forward_latest.json"
FWD_TXT = dataio.LOGS_DIR / "social_attention_forward_latest.txt"
OUT_MD = dataio.REPO / "docs" / "research" / "SOCIAL_ATTENTION_FORWARD_RESULTS.md"

DISCLAIMER = (
    "RESEARCH-ONLY forward-validation gate for the Social Attention Radar.  "
    "Measurement only — NOT signals, NOT paper, NOT trade proposals.  Point-in-time "
    "forward returns; the 'vs News Catalyst Radar' comparison is proxied by the "
    "NEWS_LED cohort (no news-radar forward history exists) — treat as best-effort.  "
    "No gate / execution / governance / live-capital / provider / DB side effects."
)


def _aligned(df: pd.DataFrame, calendar) -> pd.Series:
    c = df["close"].reindex(calendar)
    fv = c.first_valid_index()
    if fv is not None:
        c.loc[c.index >= fv] = c.loc[c.index >= fv].ffill()
    return c


def _fwd_metrics(c: pd.Series, asof_i: int, h: int) -> Optional[Dict[str, float]]:
    end_i = asof_i + h
    if end_i >= len(c):
        return None
    base = c.iloc[asof_i]
    if pd.isna(base) or base <= 0:
        return None
    seg = c.iloc[asof_i:end_i + 1].values.astype(float)
    seg = seg[~np.isnan(seg)]
    if len(seg) < 2 or pd.isna(c.iloc[end_i]):
        return None
    return {
        "fwd_end": float(c.iloc[end_i] / base - 1.0),
        "mfe": float(np.nanmax(seg) / base - 1.0),
        "mae": float(np.nanmin(seg) / base - 1.0),
    }


def _mean(xs: List[float]) -> Optional[float]:
    return float(np.mean(xs)) if xs else None


def _winrate(xs: List[float]) -> Optional[float]:
    return round(100.0 * sum(1 for x in xs if x > 0) / len(xs), 1) if xs else None


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


def _summ(acc: Dict[str, List[float]]) -> Dict[str, Any]:
    return {
        "n": len(acc["end"]),
        "mean_end": round(_mean(acc["end"]), 4) if acc["end"] else None,
        "mean_rel_spy": round(_mean(acc["rel_spy"]), 4) if acc["rel_spy"] else None,
        "mean_rel_qqq": round(_mean(acc["rel_qqq"]), 4) if acc["rel_qqq"] else None,
        "win_rate": _winrate(acc["end"]),
        "mfe_avg": round(_mean(acc["mfe"]), 4) if acc["mfe"] else None,
        "mae_avg": round(_mean(acc["mae"]), 4) if acc["mae"] else None,
    }


def build(history_path=HISTORY) -> Dict[str, Any]:
    gen = datetime.now(timezone.utc).isoformat()
    history = [r for r in dataio.read_jsonl(history_path) if r.get("version") == VERSION]
    if not history:
        return {"kind": "social_attention_forward", "version": VERSION,
                "generated_at": gen, "error": "no history yet",
                "verdict": "NEED_MORE_DATA",
                "verdict_reason": "no historized social-attention rows yet"}

    calendar = dataio.benchmark_calendar()
    date_to_i = {str(d)[:10]: i for i, d in enumerate(calendar)}
    spy = _aligned(dataio.load_prices("SPY"), calendar)
    qqq_df = dataio.load_prices("QQQ")
    qqq = _aligned(qqq_df, calendar) if qqq_df is not None else None

    # cohorts keyed by (group_kind, group_value) → per-horizon accumulators.
    cohorts = ["lead_SOCIAL_LED", "lead_NEWS_LED", "lead_SIMULTANEOUS",
               "stage_EARLY_DISCOVERY", "stage_STEALTH_ATTENTION",
               "stage_BROADENING_ATTENTION", "stage_VIRAL_CROWDING",
               "stage_EXHAUSTION_RISK",
               "vel_high", "vel_low", "all_leads", "random"]
    acc: Dict[str, Dict[int, Dict[str, List[float]]]] = {
        ck: {h: _empty_acc() for h in HORIZONS} for ck in cohorts
    }

    asof_dates = sorted({r["asof_date"] for r in history if r.get("asof_date") in date_to_i})
    rng = random.Random(RANDOM_SEED)
    n_matured_social_primary = 0

    # price series cache for any ticker we touch.
    series_cache: Dict[str, Optional[pd.Series]] = {}

    def series_for(ticker: str) -> Optional[pd.Series]:
        if ticker not in series_cache:
            df = dataio.load_prices(ticker)
            series_cache[ticker] = _aligned(df, calendar) if df is not None else None
        return series_cache[ticker]

    # random-control universe (liquid names with parquets).
    universe = [t for t in dataio.all_price_tickers() if t not in dataio.BENCHMARKS]

    for asof in asof_dates:
        asof_i = date_to_i[asof]
        spy_fwd = {h: _fwd_metrics(spy, asof_i, h) for h in HORIZONS}
        qqq_fwd = {h: _fwd_metrics(qqq, asof_i, h) for h in HORIZONS} if qqq is not None else {}

        def spqq(h):
            return ((spy_fwd[h] or {}).get("fwd_end"),
                    (qqq_fwd.get(h) or {}).get("fwd_end"))

        rows = [r for r in history if r.get("asof_date") == asof
                and r.get("label") not in (None, "NO_SOCIAL_EDGE")]

        for r in rows:
            ticker = str(r.get("ticker", "")).upper()
            c = series_for(ticker)
            if c is None or asof_i >= len(c) or pd.isna(c.iloc[asof_i]):
                continue
            vel = (r.get("metrics") or {}).get("attention_velocity_score") or 0.0
            keys = ["all_leads", f"lead_{r.get('lead_type')}", f"stage_{r.get('crowd_stage')}"]
            keys.append("vel_high" if vel >= VELOCITY_HIGH_CUTOFF else "vel_low")
            for h in HORIZONS:
                fm = _fwd_metrics(c, asof_i, h)
                if not fm:
                    continue
                sp, qq = spqq(h)
                for k in keys:
                    if k in acc:
                        _push(acc[k][h], fm, sp, qq)
                if h == PRIMARY_HORIZON and r.get("lead_type") == "SOCIAL_LED":
                    n_matured_social_primary += 1

        # random control: seeded sample, same as-of dates.
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

    by_cohort = {ck: {f"{h}d": _summ(acc[ck][h]) for h in HORIZONS} for ck in cohorts}
    history_days = len(asof_dates)
    verdict, reason = _verdict(by_cohort, history_days, n_matured_social_primary)

    return {
        "kind": "social_attention_forward",
        "version": VERSION,
        "research_only": True,
        "generated_at": gen,
        "disclaimer": DISCLAIMER,
        "history_path": dataio.rel_to_repo(history_path),
        "history_days": history_days,
        "asof_dates": asof_dates,
        "matured_social_led_primary": n_matured_social_primary,
        "controls": {"random_seed": RANDOM_SEED, "random_n": RANDOM_N,
                     "primary_horizon": PRIMARY_HORIZON,
                     "velocity_high_cutoff": VELOCITY_HIGH_CUTOFF},
        "by_cohort": by_cohort,
        "comparisons": _comparisons(by_cohort),
        "decision_gates": {
            "min_history_days": MIN_HISTORY_DAYS,
            "min_matured_primary": MIN_MATURED_PRIMARY,
            "history_days_met": history_days >= MIN_HISTORY_DAYS,
            "matured_primary_met": n_matured_social_primary >= MIN_MATURED_PRIMARY,
        },
        "verdict": verdict,
        "verdict_reason": reason,
    }


def _rel(by_cohort, cohort, h=PRIMARY_HORIZON) -> Optional[float]:
    return by_cohort.get(cohort, {}).get(f"{h}d", {}).get("mean_rel_spy")


def _comparisons(by_cohort) -> Dict[str, Any]:
    h = PRIMARY_HORIZON
    social = _rel(by_cohort, "lead_SOCIAL_LED", h)
    news = _rel(by_cohort, "lead_NEWS_LED", h)
    early = _rel(by_cohort, "stage_EARLY_DISCOVERY", h)
    viral = _rel(by_cohort, "stage_VIRAL_CROWDING", h)
    vhi = _rel(by_cohort, "vel_high", h)
    vlo = _rel(by_cohort, "vel_low", h)
    rand = _rel(by_cohort, "random", h)
    alll = _rel(by_cohort, "all_leads", h)

    def beats(a, b):
        return (a is not None and b is not None and a > b)
    return {
        "primary_horizon": h,
        "social_led_rel_spy": social, "news_led_rel_spy": news,
        "social_beats_news": beats(social, news),
        "early_rel_spy": early, "viral_rel_spy": viral,
        "early_beats_viral": beats(early, viral),
        "vel_high_rel_spy": vhi, "vel_low_rel_spy": vlo,
        "velocity_predictive": beats(vhi, vlo),
        "all_leads_rel_spy": alll, "random_rel_spy": rand,
        "leads_beat_random": beats(alll, rand),
        "social_beats_random": beats(social, rand),
    }


def _verdict(by_cohort, history_days: int, matured_primary: int) -> Tuple[str, str]:
    if history_days < MIN_HISTORY_DAYS or matured_primary < MIN_MATURED_PRIMARY:
        return ("NEED_MORE_DATA",
                f"history_days={history_days} (<{MIN_HISTORY_DAYS}) or "
                f"matured_social_led_primary={matured_primary} (<{MIN_MATURED_PRIMARY}); "
                "metrics shown are preliminary and must not drive routing.")
    cmp = _comparisons(by_cohort)
    social_beats_random = cmp["social_beats_random"]
    social_beats_news = cmp["social_beats_news"]
    early_beats_viral = cmp["early_beats_viral"]
    velocity_predictive = cmp["velocity_predictive"]
    wins = sum(bool(x) for x in (social_beats_random, social_beats_news,
                                 early_beats_viral, velocity_predictive))

    if not social_beats_random and not social_beats_news:
        return ("NO_VALUE",
                "social-led does not beat random controls nor the news-led cohort "
                "at the primary horizon — no early-crowd edge detected.")
    if wins == 4:
        return ("READY_TO_FEED_LENS_RESEARCH_ONLY",
                "social-led beats random AND news-led, early beats viral, and the "
                "velocity score is predictive — route SOCIAL_LED/EARLY candidates to "
                "Lens/Gatekeeper RESEARCH ONLY (still no production routing).")
    if wins >= 3 and social_beats_random:
        return ("SOCIAL_EDGE_DETECTED",
                "social-led beats random plus ≥2 of {beats-news, early>viral, "
                "velocity-predictive} — a social-attention edge is present; confirm "
                "the remaining gate before any lens routing.")
    return ("PROMISING_BUT_UNPROVEN",
            "partial edge (some comparisons favorable) but not decisive across the "
            "gates — keep accumulating history.")


# ── render ───────────────────────────────────────────────────────────────────────
def render_txt(res: Dict[str, Any]) -> List[str]:
    if res.get("error"):
        return [f"social-attention forward: {res['error']} → verdict {res.get('verdict')}"]
    cmp = res["comparisons"]
    L = [
        f"== SOCIAL ATTENTION FORWARD ({res['version']}) — {res['generated_at']} ==",
        res["disclaimer"],
        f"history_days={res['history_days']}  matured_social_led(primary "
        f"{res['controls']['primary_horizon']}d)={res['matured_social_led_primary']}",
        f"as-of: {', '.join(res['asof_dates'])}",
        "",
        f"{'cohort':<26}{'n':>5}{'relSPY5d':>11}{'win%':>8}{'mfe':>8}{'mae':>8}",
    ]
    for ck, hd in res["by_cohort"].items():
        d = hd.get(f"{res['controls']['primary_horizon']}d", {})
        L.append(
            f"{ck:<26}{d.get('n', 0):>5}"
            f"{(d.get('mean_rel_spy') or 0) * 100:>10.2f}%"
            f"{(d.get('win_rate') or 0):>7.1f}%"
            f"{(d.get('mfe_avg') or 0) * 100:>7.1f}%"
            f"{(d.get('mae_avg') or 0) * 100:>7.1f}%")
    L += ["",
          f"social_led {cmp['social_led_rel_spy']} vs news_led {cmp['news_led_rel_spy']} "
          f"→ social_beats_news={cmp['social_beats_news']}",
          f"early {cmp['early_rel_spy']} vs viral {cmp['viral_rel_spy']} "
          f"→ early_beats_viral={cmp['early_beats_viral']}",
          f"vel_high {cmp['vel_high_rel_spy']} vs vel_low {cmp['vel_low_rel_spy']} "
          f"→ velocity_predictive={cmp['velocity_predictive']}",
          f"all_leads {cmp['all_leads_rel_spy']} vs random {cmp['random_rel_spy']} "
          f"→ leads_beat_random={cmp['leads_beat_random']}",
          "",
          f"GATES: history_days≥{MIN_HISTORY_DAYS}={res['decision_gates']['history_days_met']}  "
          f"matured_primary≥{MIN_MATURED_PRIMARY}={res['decision_gates']['matured_primary_met']}",
          "",
          f"VERDICT: {res['verdict']}",
          f"  {res['verdict_reason']}"]
    return L


def _write_doc(res: Dict[str, Any]) -> None:
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text(
        "# Social Attention Forward Results (Phase 1G.15)\n\n"
        "Auto-refreshed by `research/social_attention_forward_validation.py`.\n"
        "Research-only / cache-only. See `SOCIAL_ATTENTION_RADAR_V0.md` and "
        "`SOCIAL_ARB_REALITY_CHECK.md`.\n\n"
        f"- Generated: `{res.get('generated_at')}`\n"
        f"- History days: `{res.get('history_days')}`\n"
        f"- Matured social-led (primary): `{res.get('matured_social_led_primary')}`\n"
        f"- **Verdict: `{res.get('verdict')}`** — {res.get('verdict_reason')}\n\n"
        "## What the gate measures\n"
        "- social-led vs news-led, early-discovery vs viral-crowding, high vs low\n"
        "  attention-velocity, and all leads vs seeded random liquid controls.\n"
        "- Point-in-time forward returns at 1/3/5/10/20d; immature windows excluded.\n\n"
        "## Verdict ladder\n"
        "NEED_MORE_DATA → NO_VALUE → PROMISING_BUT_UNPROVEN → SOCIAL_EDGE_DETECTED →\n"
        "READY_TO_FEED_LENS_RESEARCH_ONLY. Nothing here emits signals or trades.\n\n"
        "## Caveat\n"
        "The 'vs News Catalyst Radar' comparison is proxied by the NEWS_LED cohort\n"
        "because the News Catalyst Radar keeps no forward-outcome history. Treat it\n"
        "as best-effort context, not an as-of head-to-head.\n"
    )


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Social Attention forward validation (research-only)")
    ap.add_argument("--print", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    res = build()
    lines = render_txt(res)
    if args.dry_run:
        print("\n".join(lines))
        print("\n[dry-run] no files written")
        return 0
    dataio.write_json(FWD_JSON, res)
    dataio.write_text(FWD_TXT, lines)
    _write_doc(res)
    if args.print:
        print("\n".join(lines))
    print(f"\nwrote {dataio.rel_to_repo(FWD_JSON)} · {dataio.rel_to_repo(FWD_TXT)} · "
          f"verdict {res['verdict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

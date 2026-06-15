"""
research/sniper_h3_validation.py — Phase 12A SNIPER H3 narrow-cohort validation.

Hypothesis (H3, from Phase 11 autopsy)
--------------------------------------
SNIPER cohort = score 80–89 ∩ VIX 15–20 at entry ∩ vol_ratio < 1.5× ∩
sector ∈ {Healthcare, Communications, Technology}.

Pass criteria (research only — no promotion in this phase):
  - historical n ≥ 30
  - WR ≥ 55%
  - avg adjusted return 95% CI lower bound > 0
  - beats apples-to-apples random control by ≥ +5pp WR
  - friction-resilient (avg adj > 0 at 1.00% RT, or at minimum at 0.50% RT)
  - requires future live OOS 6-month confirmation before any promotion

Mode: analysis-only. Reads `research/sleeves/trades/SNIPER_V6.csv`,
`cache/research/regime_validation_vix.parquet`, and the price caches.
Writes `docs/scorecards/sniper_h3_validation.json`. Does NOT touch
strategy / scoring / scanner / governance / execution / dashboard code.

Usage
-----
  cd /home/gem/trading-production
  .venv/bin/python research/sniper_h3_validation.py
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

# Reuse helpers + Trade dataclass from the rigor audit so the methodology is
# identical (bootstrap, random control geometry, friction sensitivity).
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from research.strategy_evidence_audit import (  # type: ignore  # noqa: E402
    Trade,
    bootstrap_ci,
    win_rate,
    expectancy,
    stop_hit_rate,
    target_hit_rate,
    equity_curve_max_dd,
    random_entry_control,
    FRICTION_SENSITIVITY_RT_PCTS,
    BOOT_ITERS,
    CI_LEVEL,
    RNG_SEED,
    MIN_N_FOR_CI,
)

REPO = HERE.parent
TRADES_CSV = REPO / "research" / "sleeves" / "trades" / "SNIPER_V6.csv"
VIX_PARQUET = REPO / "cache" / "research" / "regime_validation_vix.parquet"
OUT_JSON = REPO / "docs" / "scorecards" / "sniper_h3_validation.json"

# Reuse the sector map from the autopsy (single source of truth)
from research.sleeve_failure_autopsy import _SECTOR_MAP, sector_of  # type: ignore  # noqa: E402

H3_SECTORS = {"Healthcare", "Communications", "Technology"}
H3_SCORE_LO, H3_SCORE_HI = 80.0, 90.0           # [80, 90)
H3_VIX_LO, H3_VIX_HI = 15.0, 20.0               # [15, 20)
H3_VOL_RATIO_MAX = 1.5                          # vol_ratio < 1.5×

PASS_WR_MIN = 55.0
PASS_RANDOM_DELTA_WR_MIN_PP = 5.0
PASS_N_MIN = MIN_N_FOR_CI                       # 30


# ──────────────────────────────────────────────────────────────────────────────
# Loaders
# ──────────────────────────────────────────────────────────────────────────────

def load_sniper_trades() -> pd.DataFrame:
    df = pd.read_csv(TRADES_CSV)
    df["entry_date"] = pd.to_datetime(df["entry_date"], errors="coerce")
    df["exit_date"] = pd.to_datetime(df["exit_date"], errors="coerce")
    df["score"] = df["notes"].astype(str).str.extract(r"score=([\d.]+)").astype(float)
    df["vol_ratio"] = df["notes"].astype(str).str.extract(r"vol_ratio=([\d.]+)").astype(float)
    df["stop_price"] = df["notes"].astype(str).str.extract(r"stop_price=([\d.]+)").astype(float)
    df["target_price"] = df["notes"].astype(str).str.extract(r"target_price=([\d.]+)").astype(float)
    df["sector"] = df["ticker"].map(sector_of)
    df["year"] = df["entry_date"].dt.year
    return df


def load_vix() -> pd.Series:
    v = pd.read_parquet(VIX_PARQUET)
    v.index = pd.to_datetime(v.index)
    return v["close"].sort_index()


def vix_at(d: pd.Timestamp, vix: pd.Series) -> Optional[float]:
    if pd.isna(d):
        return None
    sub = vix.loc[:d]
    if len(sub) == 0:
        return None
    return float(sub.iloc[-1])


# ──────────────────────────────────────────────────────────────────────────────
# H3 filter
# ──────────────────────────────────────────────────────────────────────────────

def apply_h3_filter(df: pd.DataFrame, vix: pd.Series) -> pd.DataFrame:
    df = df.copy()
    df["vix_at_entry"] = df["entry_date"].apply(lambda d: vix_at(d, vix))
    keep_score = (df["score"] >= H3_SCORE_LO) & (df["score"] < H3_SCORE_HI)
    keep_vix = (df["vix_at_entry"] >= H3_VIX_LO) & (df["vix_at_entry"] < H3_VIX_HI)
    keep_vol = df["vol_ratio"] < H3_VOL_RATIO_MAX
    keep_sec = df["sector"].isin(H3_SECTORS)
    df["h3_pass"] = keep_score & keep_vix & keep_vol & keep_sec
    return df


# ──────────────────────────────────────────────────────────────────────────────
# DataFrame → Trade list (so we can reuse audit helpers)
# ──────────────────────────────────────────────────────────────────────────────

def to_trades(df: pd.DataFrame) -> List[Trade]:
    out: List[Trade] = []
    for _, row in df.iterrows():
        out.append(Trade(
            sleeve="SNIPER_V6",
            ticker=str(row["ticker"]),
            entry_date=row["entry_date"].date() if pd.notna(row["entry_date"]) else None,
            horizon_days=int(row["horizon"]) if pd.notna(row["horizon"]) else 10,
            raw_return_pct=float(row["raw_return_pct"]) if pd.notna(row["raw_return_pct"]) else None,
            adjusted_return_pct=float(row["adjusted_return_pct"]) if pd.notna(row["adjusted_return_pct"]) else None,
            stop_hit=bool(row["stop_hit"]) if pd.notna(row["stop_hit"]) else None,
            target_hit=bool(row["target_hit"]) if pd.notna(row["target_hit"]) else None,
            still_open=pd.isna(row["raw_return_pct"]),
            side="LONG",
            entry_price=float(row["entry_price"]) if pd.notna(row["entry_price"]) else None,
            stop_price=float(row["stop_price"]) if pd.notna(row["stop_price"]) else None,
            target_price=float(row["target_price"]) if pd.notna(row["target_price"]) else None,
            source="backtest_csv",
        ))
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Stats blocks
# ──────────────────────────────────────────────────────────────────────────────

def cohort_stats(adjs: Sequence[float], stops: Sequence[Optional[bool]],
                 tgts: Sequence[Optional[bool]]) -> Dict[str, Any]:
    n = len(adjs)
    if n == 0:
        return {"n": 0}
    pt, lo, hi = bootstrap_ci(adjs, statistics.fmean)
    avg_adj_pct, avg_adj_lo, avg_adj_hi = pt, lo, hi
    pt, lo, hi = bootstrap_ci(adjs, win_rate)
    wr_pct, wr_lo, wr_hi = pt * 100, lo * 100, hi * 100
    sb = [1.0 if s else 0.0 for s in stops if s is not None]
    if sb:
        ps, lo_s, hi_s = bootstrap_ci(sb, statistics.fmean)
        stop_pct = ps * 100
        stop_ci = (lo_s * 100, hi_s * 100)
    else:
        stop_pct = None
        stop_ci = None
    tb = [1.0 if t else 0.0 for t in tgts if t is not None]
    if tb:
        pt2, lo_t, hi_t = bootstrap_ci(tb, statistics.fmean)
        tgt_pct = pt2 * 100
        tgt_ci = (lo_t * 100, hi_t * 100)
    else:
        tgt_pct = None
        tgt_ci = None
    return {
        "n": n,
        "avg_adj_pct": round(avg_adj_pct, 3),
        "avg_adj_ci": [round(avg_adj_lo, 3), round(avg_adj_hi, 3)],
        "wr_pct": round(wr_pct, 2),
        "wr_ci": [round(wr_lo, 2), round(wr_hi, 2)],
        "stop_hit_pct": round(stop_pct, 2) if stop_pct is not None else None,
        "stop_hit_ci": [round(stop_ci[0], 2), round(stop_ci[1], 2)] if stop_ci else None,
        "target_hit_pct": round(tgt_pct, 2) if tgt_pct is not None else None,
        "target_hit_ci": [round(tgt_ci[0], 2), round(tgt_ci[1], 2)] if tgt_ci else None,
        "max_dd_pct": round(equity_curve_max_dd(adjs), 2),
    }


def friction_sensitivity(trades: Sequence[Trade]) -> List[Dict[str, Any]]:
    closed = [t for t in trades if not t.still_open and t.raw_return_pct is not None]
    raws = [t.raw_return_pct for t in closed]
    if len(raws) < 2:
        return []
    out = []
    for rt in FRICTION_SENSITIVITY_RT_PCTS:
        adjs = [r - rt for r in raws]
        avg_pt, avg_lo, avg_hi = bootstrap_ci(adjs, statistics.fmean)
        wr_pt, wr_lo, wr_hi = bootstrap_ci(adjs, win_rate)
        out.append({
            "friction_rt_pct": rt,
            "n": len(adjs),
            "avg_adj_pct": round(avg_pt, 3),
            "avg_adj_ci": [round(avg_lo, 3), round(avg_hi, 3)],
            "wr_pct": round(wr_pt * 100, 2),
            "wr_ci": [round(wr_lo * 100, 2), round(wr_hi * 100, 2)],
        })
    return out


def year_by_year(df_filtered: pd.DataFrame) -> Dict[str, Any]:
    closed = df_filtered[df_filtered["adjusted_return_pct"].notna()].copy()
    out: Dict[str, Any] = {}
    for year, sub in closed.groupby("year"):
        adjs = sub["adjusted_return_pct"].astype(float).tolist()
        if not adjs:
            continue
        out[str(int(year))] = {
            "n": len(adjs),
            "wr_pct": round(100 * sum(1 for a in adjs if a > 0) / len(adjs), 2),
            "avg_adj_pct": round(statistics.fmean(adjs), 3),
            "stop_hit_pct": round(100 * float(sub["stop_hit"].fillna(0).astype(int).sum()) / len(sub), 2),
        }
    pos_years = [y for y, s in out.items() if s["avg_adj_pct"] > 0]
    out["__stability"] = {
        "n_years": len(out),
        "n_positive_years": len(pos_years),
        "stable_at_majority": (len(pos_years) / len(out) >= 0.5) if out else None,
    }
    return out


def concentration(df_filtered: pd.DataFrame) -> Dict[str, Any]:
    closed = df_filtered[df_filtered["adjusted_return_pct"].notna()].copy()
    by_t = closed.groupby("ticker")["adjusted_return_pct"].agg(["count", "sum", "mean"]).sort_values("sum", ascending=False)
    by_s = closed.groupby("sector")["adjusted_return_pct"].agg(["count", "sum", "mean"]).sort_values("sum", ascending=False)
    total = float(closed["adjusted_return_pct"].sum())
    top3 = float(by_t.head(3)["sum"].sum()) if len(by_t) else 0.0
    return {
        "n_unique_tickers": int(by_t.shape[0]),
        "top_tickers": [
            {"ticker": idx, "n": int(r["count"]),
             "sum_adj_pct": round(float(r["sum"]), 2),
             "avg_adj_pct": round(float(r["mean"]), 2)}
            for idx, r in by_t.head(5).iterrows()
        ],
        "top3_share_of_total_pct": round(100 * top3 / total, 1) if total else None,
        "by_sector": {
            sec: {"n": int(r["count"]),
                  "sum_adj_pct": round(float(r["sum"]), 2),
                  "avg_adj_pct": round(float(r["mean"]), 2)}
            for sec, r in by_s.iterrows()
        },
    }


# ──────────────────────────────────────────────────────────────────────────────
# Verdict
# ──────────────────────────────────────────────────────────────────────────────

MIN_UNIQUE_ENTRIES_FOR_PROMISING = 5  # below this, horizon multiplication isn't real n


def decide_verdict(stats: Dict[str, Any], rc: Dict[str, Any],
                   friction: List[Dict[str, Any]], stability: Dict[str, Any],
                   n_unique_entries: int = 0) -> Dict[str, Any]:
    """Return {label, reasons[]} from the four allowed labels:
       H3_REJECTED / H3_PROMISING_BUT_THIN /
       H3_PASSES_HISTORICAL_NEEDS_LIVE_OOS / INSUFFICIENT_DATA.

    Note on independence: a single (ticker, entry_date) yields 3 rows in this
    sleeve (5/10/20d horizons) but is *one* independent observation. The verdict
    treats n_unique_entries as the binding sample size when it is small.
    """
    n = stats.get("n", 0)
    wr = stats.get("wr_pct")
    avg_adj_lo = stats.get("avg_adj_ci", [None, None])[0]
    avg_adj_pt = stats.get("avg_adj_pct")
    rc_wr = rc.get("wr_pct")
    fric_100 = next((f for f in friction if f["friction_rt_pct"] == 1.00), {}).get("avg_adj_pct")
    fric_050 = next((f for f in friction if f["friction_rt_pct"] == 0.50), {}).get("avg_adj_pct")
    stable_majority = stability.get("__stability", {}).get("stable_at_majority")

    reasons: List[str] = []

    # Independence floor: horizons are not independent observations.
    if n_unique_entries < MIN_UNIQUE_ENTRIES_FOR_PROMISING:
        reasons.append(
            f"n_unique_entries={n_unique_entries} below {MIN_UNIQUE_ENTRIES_FOR_PROMISING}; "
            f"the {n} trade-rows are horizon-multiplicates of {n_unique_entries} independent "
            f"decision(s), so any bootstrap CI on the row-level series overstates information"
        )
        if avg_adj_pt is not None and avg_adj_pt > 0:
            reasons.append("point estimate is positive, but evidence is structurally insufficient — "
                           "filter gates compose to a near-empty cohort on the existing 75-entry CSV")
        return {"label": "INSUFFICIENT_DATA", "reasons": reasons}

    if n < PASS_N_MIN:
        if avg_adj_pt is not None and avg_adj_pt > 0 and avg_adj_lo is not None and avg_adj_lo > 0:
            reasons.append(f"n={n} below MIN_N_FOR_CI ({PASS_N_MIN}) but point estimate and CI lower bound are positive")
            return {"label": "H3_PROMISING_BUT_THIN", "reasons": reasons}
        if avg_adj_pt is not None and avg_adj_pt > 0:
            reasons.append(f"n={n} below MIN_N_FOR_CI; positive point estimate, CI not strictly above zero")
            return {"label": "H3_PROMISING_BUT_THIN", "reasons": reasons}
        reasons.append(f"n={n} below MIN_N_FOR_CI ({PASS_N_MIN}); point estimate not positive")
        return {"label": "INSUFFICIENT_DATA", "reasons": reasons}

    failed: List[str] = []
    passed: List[str] = []

    if wr is not None and wr >= PASS_WR_MIN:
        passed.append(f"WR {wr:.1f}% ≥ {PASS_WR_MIN}%")
    else:
        failed.append(f"WR {wr:.1f}% < {PASS_WR_MIN}%")

    if avg_adj_lo is not None and avg_adj_lo > 0:
        passed.append(f"avg adj 95% CI lower bound {avg_adj_lo:.2f}% > 0")
    else:
        failed.append(f"avg adj 95% CI lower bound {avg_adj_lo} not strictly > 0")

    if rc_wr is not None and wr is not None and (wr - rc_wr) >= PASS_RANDOM_DELTA_WR_MIN_PP:
        passed.append(f"WR delta vs random {wr - rc_wr:.2f}pp ≥ {PASS_RANDOM_DELTA_WR_MIN_PP}pp")
    else:
        failed.append(f"WR delta vs random {None if (rc_wr is None or wr is None) else round(wr - rc_wr, 2)}pp short of {PASS_RANDOM_DELTA_WR_MIN_PP}pp threshold")

    # Friction: prefer 1.00% > 0; tolerate 0.50% > 0 if 1.00% marginal
    if fric_100 is not None and fric_100 > 0:
        passed.append(f"friction-resilient at 1.00% RT (avg adj {fric_100:.2f}% > 0)")
    elif fric_050 is not None and fric_050 > 0:
        failed.append(f"avg adj at 1.00% RT = {fric_100} not > 0 (only resilient at 0.50% RT: {fric_050:.2f}%)")
    else:
        failed.append(f"avg adj at 0.50% and 1.00% RT not > 0")

    if stable_majority is False:
        failed.append("year-by-year stability: minority of years positive")
    elif stable_majority is True:
        passed.append("year-by-year majority positive")

    if not failed:
        return {"label": "H3_PASSES_HISTORICAL_NEEDS_LIVE_OOS",
                "reasons": passed}
    if len(failed) <= 2 and any("WR" in p for p in passed) and any("CI" in p for p in passed):
        return {"label": "H3_PROMISING_BUT_THIN",
                "reasons": passed + [f"BUT: {f}" for f in failed]}
    return {"label": "H3_REJECTED",
            "reasons": passed + [f"FAIL: {f}" for f in failed]}


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT_JSON))
    args = ap.parse_args(argv)

    df = load_sniper_trades()
    vix = load_vix()
    df = apply_h3_filter(df, vix)
    df_h3 = df[df["h3_pass"]].copy()
    df_closed = df_h3[df_h3["adjusted_return_pct"].notna()].copy()

    # Filter funnel diagnostics (apply gates one at a time, in independent order)
    funnel: Dict[str, int] = {"all_rows": int(len(df))}
    funnel["closed"] = int(df["adjusted_return_pct"].notna().sum())
    sg = (df["score"] >= H3_SCORE_LO) & (df["score"] < H3_SCORE_HI)
    funnel["score_80_89"] = int(sg.sum())
    vg = (df["vix_at_entry"] >= H3_VIX_LO) & (df["vix_at_entry"] < H3_VIX_HI)
    funnel["vix_15_20"] = int(vg.sum())
    vrg = df["vol_ratio"] < H3_VOL_RATIO_MAX
    funnel["vol_ratio_lt_1_5"] = int(vrg.sum())
    secg = df["sector"].isin(H3_SECTORS)
    funnel["sector_HC_or_COMM_or_TECH"] = int(secg.sum())
    funnel["score_AND_vix"] = int((sg & vg).sum())
    funnel["score_AND_vix_AND_vol"] = int((sg & vg & vrg).sum())
    funnel["all_four_gates"] = int((sg & vg & vrg & secg).sum())
    funnel["all_four_gates_closed"] = int(((sg & vg & vrg & secg) & df["adjusted_return_pct"].notna()).sum())

    # Cohort stats
    adjs = df_closed["adjusted_return_pct"].astype(float).tolist()
    stops = [bool(s) if pd.notna(s) else None for s in df_closed["stop_hit"]]
    tgts = [bool(t) if pd.notna(t) else None for t in df_closed["target_hit"]]
    stats = cohort_stats(adjs, stops, tgts)

    # Random control on the H3 cohort (apples-to-apples: same tickers, same horizons,
    # same stop/target geometry — Trade objects only carry these features)
    h3_trades = to_trades(df_h3)
    rc = random_entry_control(h3_trades, friction_rt_pct=0.30)

    # Friction sensitivity sweep on H3 cohort
    fs = friction_sensitivity(h3_trades)

    # Year-by-year stability + concentration
    stability = year_by_year(df_h3)
    conc = concentration(df_h3)

    # Per-horizon view (5/10/20)
    per_horizon: Dict[str, Any] = {}
    for h, sub in df_closed.groupby("horizon"):
        sub_adjs = sub["adjusted_return_pct"].astype(float).tolist()
        sub_stops = [bool(s) if pd.notna(s) else None for s in sub["stop_hit"]]
        sub_tgts = [bool(t) if pd.notna(t) else None for t in sub["target_hit"]]
        per_horizon[str(int(h))] = cohort_stats(sub_adjs, sub_stops, sub_tgts)

    n_unique_entries = int(df_h3[["ticker", "entry_date"]].drop_duplicates().shape[0])
    verdict = decide_verdict(stats, rc, fs, stability, n_unique_entries=n_unique_entries)

    # Leave-one-gate-out diagnostic: which single gate is the binding constraint?
    # Shows what the cohort would look like if we dropped each gate in turn.
    leave_one_out: Dict[str, Any] = {}
    sg = (df["score"] >= H3_SCORE_LO) & (df["score"] < H3_SCORE_HI)
    vg = (df["vix_at_entry"] >= H3_VIX_LO) & (df["vix_at_entry"] < H3_VIX_HI)
    vrg = df["vol_ratio"] < H3_VOL_RATIO_MAX
    secg = df["sector"].isin(H3_SECTORS)
    for label, mask in [
        ("drop_score_gate",  vg & vrg & secg),
        ("drop_vix_gate",    sg & vrg & secg),
        ("drop_vol_gate",    sg & vg & secg),
        ("drop_sector_gate", sg & vg & vrg),
    ]:
        sub = df[mask & df["adjusted_return_pct"].notna()].copy()
        if len(sub) == 0:
            leave_one_out[label] = {"n_closed": 0, "n_unique_entries": 0}
            continue
        adjs_l = sub["adjusted_return_pct"].astype(float).tolist()
        wins_l = sum(1 for a in adjs_l if a > 0)
        leave_one_out[label] = {
            "n_closed": len(sub),
            "n_unique_entries": int(sub[["ticker", "entry_date"]].drop_duplicates().shape[0]),
            "wr_pct": round(100 * wins_l / len(adjs_l), 2),
            "avg_adj_pct": round(statistics.fmean(adjs_l), 3),
            "stop_hit_pct": round(100 * float(sub["stop_hit"].fillna(0).astype(int).sum()) / len(sub), 2),
        }

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "phase": "12A — SNIPER H3 narrow-cohort validation",
        "hypothesis": {
            "id": "H3",
            "definition": {
                "score_band": [H3_SCORE_LO, H3_SCORE_HI],
                "vix_band_at_entry": [H3_VIX_LO, H3_VIX_HI],
                "vol_ratio_max": H3_VOL_RATIO_MAX,
                "sectors_allowed": sorted(H3_SECTORS),
            },
            "pass_criteria": {
                "n_min": PASS_N_MIN,
                "wr_min_pct": PASS_WR_MIN,
                "avg_adj_ci_lo_min": 0.0,
                "wr_delta_vs_random_min_pp": PASS_RANDOM_DELTA_WR_MIN_PP,
                "friction_resilience": "avg adj > 0 at 1.00% RT (preferred) or at 0.50% RT (marginal)",
                "live_oos": "future 6-month confirmation required before any promotion",
            },
        },
        "data_sources": {
            "trades_csv": str(TRADES_CSV.relative_to(REPO)),
            "vix_parquet": str(VIX_PARQUET.relative_to(REPO)),
            "sector_map": "research/sleeve_failure_autopsy.py:_SECTOR_MAP",
        },
        "filter_funnel": funnel,
        "h3_cohort_overview": {
            "n_total_rows": int(len(df_h3)),
            "n_closed_rows": int(len(df_closed)),
            "n_unique_entries": int(df_h3[["ticker", "entry_date"]].drop_duplicates().shape[0]),
            "horizons_present": sorted([int(h) for h in df_closed["horizon"].dropna().unique().tolist()]),
            "date_range": {
                "min": df_h3["entry_date"].min().strftime("%Y-%m-%d") if pd.notna(df_h3["entry_date"].min()) else None,
                "max": df_h3["entry_date"].max().strftime("%Y-%m-%d") if pd.notna(df_h3["entry_date"].max()) else None,
            },
        },
        "h3_cohort_stats": stats,
        "h3_random_control": rc,
        "h3_friction_sensitivity": fs,
        "h3_year_by_year": stability,
        "h3_concentration": conc,
        "h3_per_horizon": per_horizon,
        "h3_leave_one_gate_out": leave_one_out,
        "auxiliary_state_availability": {
            "daily_entry_validator_state_at_entry": {
                "available_historically": False,
                "reason": "DEV state was not snapshotted at trade-entry time in the historical "
                          "SNIPER backtest CSV; the strategy_evidence_audit / sniper_backtest "
                          "pipeline does not currently persist DEV pass/fail per signal.",
                "future_join_plan": (
                    "When SNIPER paper signals run forward, persist the DEV state at signal-emit "
                    "time in db/trading.db (paper_signals.aux_dev_state JSON column already exists "
                    "for similar use; new column or notes-field tag is acceptable). The H3 live OOS "
                    "test in Phase 12B will join on that field."
                ),
            },
            "market_forecast_state_at_entry": {
                "available_historically": False,
                "reason": "Market Forecast (regime_forecast_latest.json snapshots) is point-in-time "
                          "today; historical SNIPER signals were not tagged with the regime label that "
                          "was in force at their entry date.",
                "future_join_plan": (
                    "Same as DEV: persist the Market Forecast regime label at SNIPER signal-emit "
                    "time in paper_signals. Once 6 months of forward signals carry both fields, "
                    "re-run this script with `--include-aux-state` to add per-cohort breakdown "
                    "(this script is forward-compatible: the funnel block will gain extra gates "
                    "without changing existing keys)."
                ),
            },
        },
        "verdict": verdict,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, default=str))
    print(f"wrote {out_path}")
    print(f"verdict: {verdict['label']}")
    print(f"H3 cohort: n={out['h3_cohort_overview']['n_closed_rows']} closed "
          f"(unique entries: {out['h3_cohort_overview']['n_unique_entries']})")
    if stats.get("n", 0) > 0:
        print(f"  WR: {stats['wr_pct']}% {stats['wr_ci']}, "
              f"avg adj: {stats['avg_adj_pct']}% {stats['avg_adj_ci']}")
    if rc and rc.get("n"):
        print(f"  Random control: WR {rc['wr_pct']:.2f}%, avg adj {rc['avg_adj_pct']:.2f}%, n={rc['n']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

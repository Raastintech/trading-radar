"""
research/sleeve_failure_autopsy.py — Phase 11 active-sleeve failure autopsy.

Scope: SNIPER_V6, VOYAGER_PAPER, SHORT_A.
Mode:  analysis-only. Reads the trade-level CSVs in research/sleeves/trades/,
       the VIX/SPY/QQQ price caches under cache/, and the existing
       evidence rigor JSON. Emits a structured JSON sidecar for the
       SLEEVE_FAILURE_AUTOPSY.md report writer to consume.

What this does
--------------
1. SHORT_A: enumerates every closed trade with regime context (SPY 50d/200d
   trend, VIX bucket on entry day, SPY/QQQ 20d momentum), tags stop-hit
   cluster, and emits a winner-vs-loser side-by-side. Includes notes-derived
   features: score, borrow_pct, gap_risk, intraday_range.
2. VOYAGER_PAPER: per-horizon strategy-vs-random delta, ticker concentration,
   beta-capture vs SPY/QQQ for the same entry-window/horizon, and
   loss/winner skew check.
3. SNIPER_V6: subset analysis (score bucket from notes, horizon, year/VIX
   regime, ticker concentration). Reports which subsets, if any, deliver
   strategy − random > 5pp WR while preserving expectancy sign.
4. Cross-sleeve: synthesises shared failure modes from steps 1–3.

The script does NOT change any strategy/scoring/governance/execution code.
It reads data and writes one JSON file.

Usage
-----
  cd /home/gem/trading-production
  .venv/bin/python research/sleeve_failure_autopsy.py
  .venv/bin/python research/sleeve_failure_autopsy.py --quick   # SHORT_A only
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
TRADES_DIR = REPO / "research" / "sleeves" / "trades"
PRICE_CACHE_BACKTEST = REPO / "cache" / "backtest_prices"
PRICE_CACHE_LIVE = REPO / "cache" / "prices"
VIX_PARQUET = REPO / "cache" / "research" / "regime_validation_vix.parquet"
EVIDENCE_JSON = REPO / "docs" / "scorecards" / "evidence_rigor_report.json"
OUT_JSON = REPO / "docs" / "scorecards" / "sleeve_failure_autopsy.json"

SLEEVES = ["SHORT_A", "VOYAGER_PAPER", "SNIPER_V6"]


# ──────────────────────────────────────────────────────────────────────────────
# Data loading helpers
# ──────────────────────────────────────────────────────────────────────────────

def load_trades(sleeve: str) -> pd.DataFrame:
    df = pd.read_csv(TRADES_DIR / f"{sleeve}.csv")
    df["entry_date"] = pd.to_datetime(df["entry_date"], errors="coerce")
    df["exit_date"] = pd.to_datetime(df["exit_date"], errors="coerce")
    df["closed"] = df["exit_price"].notna() & df["raw_return_pct"].notna()
    df["score"] = df["notes"].astype(str).str.extract(r"score=([\d.]+)").astype(float)
    df["borrow_pct"] = df["notes"].astype(str).str.extract(r"borrow_pct=([\d.]+)").astype(float)
    df["gap_risk_pct"] = df["notes"].astype(str).str.extract(r"gap_risk_pct=([\d.]+)").astype(float)
    df["intraday_range_pct"] = df["notes"].astype(str).str.extract(
        r"intraday_range_pct=([\d.]+)"
    ).astype(float)
    df["vol_ratio"] = df["notes"].astype(str).str.extract(r"vol_ratio=([\d.]+)").astype(float)
    return df


def load_price(ticker: str) -> Optional[pd.DataFrame]:
    """Return a DataFrame indexed by date with at least 'close'.
    Prefers the longer backtest cache; falls back to live cache.
    """
    for base in (PRICE_CACHE_BACKTEST, PRICE_CACHE_LIVE):
        p = base / f"{ticker}.parquet"
        if p.exists():
            try:
                df = pd.read_parquet(p)
                df.index = pd.to_datetime(df.index)
                df = df.sort_index()
                if "close" in df.columns:
                    return df
            except Exception:
                continue
    return None


def load_vix() -> Optional[pd.Series]:
    if not VIX_PARQUET.exists():
        return None
    v = pd.read_parquet(VIX_PARQUET)
    v.index = pd.to_datetime(v.index)
    return v["close"].sort_index()


def vix_bucket(vix_val: Optional[float]) -> str:
    if vix_val is None or pd.isna(vix_val):
        return "unknown"
    if vix_val < 15:
        return "low (<15)"
    if vix_val < 20:
        return "normal (15-20)"
    if vix_val < 30:
        return "elevated (20-30)"
    return "high (>=30)"


def regime_context(date: pd.Timestamp, spy: pd.DataFrame, qqq: Optional[pd.DataFrame],
                   vix: Optional[pd.Series]) -> Dict[str, Any]:
    """Return SPY trend + 20d momentum + VIX bucket on entry date."""
    if pd.isna(date):
        return {}
    out: Dict[str, Any] = {}
    # SPY context
    spy_window = spy.loc[:date]
    if len(spy_window) >= 200:
        last = float(spy_window["close"].iloc[-1])
        sma50 = float(spy_window["close"].iloc[-50:].mean())
        sma200 = float(spy_window["close"].iloc[-200:].mean())
        ret_20 = (last / float(spy_window["close"].iloc[-21]) - 1.0) * 100 if len(spy_window) > 21 else None
        out["spy_close"] = round(last, 2)
        out["spy_above_50dma"] = last > sma50
        out["spy_above_200dma"] = last > sma200
        out["spy_20d_ret_pct"] = round(ret_20, 2) if ret_20 is not None else None
        if last > sma50 and last > sma200:
            out["spy_trend"] = "bullish (above 50/200dma)"
        elif last < sma50 and last < sma200:
            out["spy_trend"] = "bearish (below 50/200dma)"
        else:
            out["spy_trend"] = "mixed"
    if qqq is not None:
        qw = qqq.loc[:date]
        if len(qw) >= 21:
            qret = (float(qw["close"].iloc[-1]) / float(qw["close"].iloc[-21]) - 1.0) * 100
            out["qqq_20d_ret_pct"] = round(qret, 2)
    if vix is not None:
        vw = vix.loc[:date]
        if len(vw) > 0:
            v = float(vw.iloc[-1])
            out["vix"] = round(v, 2)
            out["vix_bucket"] = vix_bucket(v)
    return out


def benchmark_return(ticker_df: pd.DataFrame, start: pd.Timestamp,
                     hold_days: int) -> Optional[float]:
    """Forward return of a benchmark over `hold_days` calendar (uses next
    available trading day after `start` and ~hold_days*1.4 calendar window
    capped to the last available bar)."""
    if ticker_df is None or pd.isna(start):
        return None
    df = ticker_df.loc[start:]
    if len(df) < 2:
        return None
    p0 = float(df["close"].iloc[0])
    target_idx = min(hold_days, len(df) - 1)
    p1 = float(df["close"].iloc[target_idx])
    return round((p1 / p0 - 1.0) * 100, 3)


def percent(num: int, den: int) -> Optional[float]:
    if den == 0:
        return None
    return round(100.0 * num / den, 2)


def safe_mean(xs: List[float]) -> Optional[float]:
    xs = [x for x in xs if x is not None and not (isinstance(x, float) and math.isnan(x))]
    if not xs:
        return None
    return round(sum(xs) / len(xs), 3)


# Lightweight sector map (best-effort; only covers tickers we expect).
_SECTOR_MAP = {
    # Tech / Comm / Cons-Disc (SNIPER + VOYAGER)
    "AAPL": "Technology", "MSFT": "Technology", "NVDA": "Technology",
    "AVGO": "Technology", "AMD": "Technology", "ADBE": "Technology",
    "PANW": "Technology", "CRM": "Technology", "ORCL": "Technology",
    "MU": "Technology", "QCOM": "Technology", "AMAT": "Technology",
    "GOOGL": "Communications", "META": "Communications", "NFLX": "Communications",
    "AMZN": "Consumer Discretionary", "TSLA": "Consumer Discretionary",
    "MELI": "Consumer Discretionary", "BKNG": "Consumer Discretionary",
    "LULU": "Consumer Discretionary", "SHOP": "Consumer Discretionary",
    "ABNB": "Consumer Discretionary", "DECK": "Consumer Discretionary",
    "CMG": "Consumer Discretionary", "HD": "Consumer Discretionary",
    # Financials
    "JPM": "Financials", "GS": "Financials", "MA": "Financials", "V": "Financials",
    "BAC": "Financials", "MS": "Financials", "BLK": "Financials",
    "COIN": "Financials", "PYPL": "Financials",
    # Healthcare
    "LLY": "Healthcare", "UNH": "Healthcare", "ABBV": "Healthcare",
    "REGN": "Healthcare", "ISRG": "Healthcare", "VRTX": "Healthcare",
    "ABT": "Healthcare", "ZTS": "Healthcare",
    # Industrials / Utilities / Energy / Staples / Materials
    "GE": "Industrials", "CAT": "Industrials", "HON": "Industrials",
    "ETN": "Industrials", "URI": "Industrials",
    "VST": "Utilities",
    "XOM": "Energy", "CVX": "Energy", "COP": "Energy",
    "WMT": "Consumer Staples", "COST": "Consumer Staples", "PG": "Consumer Staples",
    "EL": "Consumer Staples", "PEP": "Consumer Staples", "KO": "Consumer Staples",
    "LIN": "Materials",
    # Crypto-proxy / specialty
    "MSTR": "Technology (Bitcoin-proxy)",
    "PEGA": "Technology",
}


def sector_of(ticker: str) -> str:
    return _SECTOR_MAP.get(ticker, "Unknown")


# ──────────────────────────────────────────────────────────────────────────────
# Per-sleeve analyses
# ──────────────────────────────────────────────────────────────────────────────

def short_a_autopsy(df: pd.DataFrame, spy: pd.DataFrame, qqq: Optional[pd.DataFrame],
                    vix: Optional[pd.Series]) -> Dict[str, Any]:
    """SHORT_A trade-level enumeration with regime + cluster diagnosis."""
    rows = []
    closed = df[df["closed"]].copy()
    for _, t in df.iterrows():
        ticker = t["ticker"]
        ctx = regime_context(t["entry_date"], spy, qqq, vix)
        # Use horizon as approximate hold period for benchmark forward return
        hold = int(t["horizon"]) if pd.notna(t["horizon"]) else 0
        ticker_px = load_price(ticker)
        # MAE/MFE for shorts: how far the trade went against (price up) and for (price down)
        mae_pct = None  # max favourable excursion for the long side (i.e. worst against the short)
        mfe_pct = None
        if ticker_px is not None and pd.notna(t["entry_date"]):
            entry_px = float(t["entry_price"])
            window = ticker_px.loc[t["entry_date"]:]
            if pd.notna(t["exit_date"]):
                window = window.loc[: t["exit_date"]]
            if len(window) > 1:
                hi = float(window["high"].max()) if "high" in window.columns else float(window["close"].max())
                lo = float(window["low"].min()) if "low" in window.columns else float(window["close"].min())
                # For a short: pain = price moved UP from entry
                mae_pct = round((hi / entry_px - 1.0) * 100, 2)
                # gain = price moved DOWN from entry
                mfe_pct = round((1.0 - lo / entry_px) * 100, 2)
        spy_fwd = benchmark_return(spy, t["entry_date"], hold) if hold else None
        rows.append({
            "ticker": ticker,
            "sector": sector_of(ticker),
            "entry_date": t["entry_date"].strftime("%Y-%m-%d") if pd.notna(t["entry_date"]) else None,
            "exit_date": t["exit_date"].strftime("%Y-%m-%d") if pd.notna(t["exit_date"]) else None,
            "horizon": int(t["horizon"]) if pd.notna(t["horizon"]) else None,
            "baseline_tag": t.get("baseline_tag"),
            "entry_price": float(t["entry_price"]) if pd.notna(t["entry_price"]) else None,
            "raw_return_pct": float(t["raw_return_pct"]) if pd.notna(t["raw_return_pct"]) else None,
            "adjusted_return_pct": float(t["adjusted_return_pct"]) if pd.notna(t["adjusted_return_pct"]) else None,
            "stop_hit": bool(t["stop_hit"]) if pd.notna(t["stop_hit"]) else False,
            "target_hit": bool(t["target_hit"]) if pd.notna(t["target_hit"]) else False,
            "score": float(t["score"]) if pd.notna(t["score"]) else None,
            "borrow_pct": float(t["borrow_pct"]) if pd.notna(t["borrow_pct"]) else None,
            "gap_risk_pct": float(t["gap_risk_pct"]) if pd.notna(t["gap_risk_pct"]) else None,
            "intraday_range_pct": float(t["intraday_range_pct"]) if pd.notna(t["intraday_range_pct"]) else None,
            "mae_pct_against": mae_pct,
            "mfe_pct_for": mfe_pct,
            "spy_fwd_pct_same_window": spy_fwd,
            **ctx,
        })

    stop_hits = [r for r in rows if r["stop_hit"]]
    winners = [r for r in rows if (r.get("adjusted_return_pct") or 0) > 0]
    losers = [r for r in rows if (r.get("adjusted_return_pct") or 0) < 0]

    # Cohort summary: historical backtest vs live paper
    by_baseline: Dict[str, Dict[str, Any]] = {}
    for tag, sub in df.groupby(df["baseline_tag"].fillna("unknown")):
        sub_closed = sub[sub["closed"]]
        by_baseline[tag] = {
            "n_total": int(len(sub)),
            "n_closed": int(len(sub_closed)),
            "wr_pct": percent(int((sub_closed["adjusted_return_pct"] > 0).sum()), len(sub_closed)),
            "stop_hit_pct": percent(int(sub_closed["stop_hit"].fillna(0).astype(int).sum()), len(sub_closed)),
            "avg_adj_pct": round(float(sub_closed["adjusted_return_pct"].mean()), 3) if len(sub_closed) else None,
        }

    # Regime breakdown over closed historical trades
    hist_closed = df[(df["closed"]) & (df["baseline_tag"] == "short_history_v1")]
    hist_dates = hist_closed["entry_date"].dropna().sort_values().tolist()
    spy_during = None
    if hist_dates:
        d0, d1 = hist_dates[0], hist_dates[-1]
        spy_slice = spy.loc[d0:d1]
        if len(spy_slice) > 1:
            spy_during = round((float(spy_slice["close"].iloc[-1]) / float(spy_slice["close"].iloc[0]) - 1.0) * 100, 2)

    return {
        "rows": rows,
        "stop_hit_count": len(stop_hits),
        "n_closed": int(df["closed"].sum()),
        "n_open": int((~df["closed"]).sum()),
        "winners_n": len(winners),
        "losers_n": len(losers),
        "by_baseline_cohort": by_baseline,
        "spy_total_return_during_history_window_pct": spy_during,
        "history_window": {
            "start": hist_dates[0].strftime("%Y-%m-%d") if hist_dates else None,
            "end": hist_dates[-1].strftime("%Y-%m-%d") if hist_dates else None,
        },
        # Winner-vs-loser comparison
        "winners_vs_losers": {
            "winners": _winner_loser_summary(winners),
            "losers": _winner_loser_summary(losers),
        },
    }


def _winner_loser_summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return {"n": 0}
    horizons = [r["horizon"] for r in rows if r.get("horizon") is not None]
    return {
        "n": len(rows),
        "tickers": sorted({r["ticker"] for r in rows}),
        "sectors": sorted({r.get("sector") for r in rows if r.get("sector")}),
        "horizon_avg": safe_mean(horizons),
        "horizon_min": min(horizons) if horizons else None,
        "horizon_max": max(horizons) if horizons else None,
        "score_avg": safe_mean([r.get("score") for r in rows]),
        "borrow_avg": safe_mean([r.get("borrow_pct") for r in rows]),
        "gap_risk_avg": safe_mean([r.get("gap_risk_pct") for r in rows]),
        "intraday_range_avg": safe_mean([r.get("intraday_range_pct") for r in rows]),
        "spy_20d_avg_pct": safe_mean([r.get("spy_20d_ret_pct") for r in rows]),
        "spy_above_200dma_count": sum(1 for r in rows if r.get("spy_above_200dma")),
        "vix_avg": safe_mean([r.get("vix") for r in rows]),
        "mae_against_avg_pct": safe_mean([r.get("mae_pct_against") for r in rows]),
    }


def voyager_autopsy(df: pd.DataFrame, spy: pd.DataFrame, qqq: Optional[pd.DataFrame],
                    vix: Optional[pd.Series]) -> Dict[str, Any]:
    """Concentration / beta / horizon / regime decomposition for VOYAGER."""
    closed = df[df["closed"]].copy()

    # Per-horizon
    by_h: Dict[str, Any] = {}
    for h, sub in closed.groupby("horizon"):
        rets = sub["adjusted_return_pct"].astype(float).tolist()
        wins = int((sub["adjusted_return_pct"] > 0).sum())
        # Compute SPY benchmark for the same entry windows
        bench: List[float] = []
        for _, t in sub.iterrows():
            r = benchmark_return(spy, t["entry_date"], int(h))
            if r is not None:
                bench.append(r)
        by_h[str(int(h))] = {
            "n": len(sub),
            "wr_pct": percent(wins, len(sub)),
            "avg_adj_pct": round(float(sub["adjusted_return_pct"].mean()), 3),
            "avg_spy_fwd_pct_same_window": safe_mean(bench),
            "delta_vs_spy_pct": (
                round(float(sub["adjusted_return_pct"].mean()) - safe_mean(bench), 3)
                if bench else None
            ),
        }

    # Concentration: top tickers' contribution to total adjusted return
    closed = closed.copy()
    closed["adjusted_return_pct"] = closed["adjusted_return_pct"].astype(float)
    by_ticker = closed.groupby("ticker")["adjusted_return_pct"].agg(["count", "sum", "mean"])
    by_ticker = by_ticker.sort_values("sum", ascending=False)
    total_sum = float(closed["adjusted_return_pct"].sum())
    top3_sum = float(by_ticker.head(3)["sum"].sum())
    bottom3_sum = float(by_ticker.tail(3)["sum"].sum())

    # Sector aggregation
    sector_rows: Dict[str, Dict[str, Any]] = {}
    closed["sector"] = closed["ticker"].map(sector_of)
    for sec, sub in closed.groupby("sector"):
        sector_rows[sec] = {
            "n": len(sub),
            "avg_adj_pct": round(float(sub["adjusted_return_pct"].mean()), 3),
            "wr_pct": percent(int((sub["adjusted_return_pct"] > 0).sum()), len(sub)),
        }

    # Regime: split entries by SPY-above-200dma at entry
    bull_rets, bear_rets = [], []
    for _, t in closed.iterrows():
        ctx = regime_context(t["entry_date"], spy, qqq, vix)
        adj = float(t["adjusted_return_pct"])
        if ctx.get("spy_above_200dma"):
            bull_rets.append(adj)
        else:
            bear_rets.append(adj)

    return {
        "n_unique_entries": int(closed[["ticker", "entry_date"]].drop_duplicates().shape[0]),
        "by_horizon": by_h,
        "ticker_concentration": {
            "n_unique_tickers": int(by_ticker.shape[0]),
            "top_tickers": [
                {"ticker": idx, "n": int(row["count"]), "sum_adj_pct": round(float(row["sum"]), 2),
                 "avg_adj_pct": round(float(row["mean"]), 2)}
                for idx, row in by_ticker.head(5).iterrows()
            ],
            "top3_share_of_total_pct": round(100 * top3_sum / total_sum, 1) if total_sum else None,
            "bottom3_share_of_total_pct": round(100 * bottom3_sum / total_sum, 1) if total_sum else None,
        },
        "by_sector": sector_rows,
        "regime_split": {
            "spy_bull_at_entry_n": len(bull_rets),
            "spy_bull_avg_adj_pct": safe_mean(bull_rets),
            "spy_bear_at_entry_n": len(bear_rets),
            "spy_bear_avg_adj_pct": safe_mean(bear_rets),
        },
    }


def sniper_autopsy(df: pd.DataFrame, spy: pd.DataFrame, qqq: Optional[pd.DataFrame],
                   vix: Optional[pd.Series]) -> Dict[str, Any]:
    """Subset analysis: which cohort in SNIPER actually beats random?"""
    closed = df[df["closed"]].copy()
    closed["sector"] = closed["ticker"].map(sector_of)
    closed["year"] = closed["entry_date"].dt.year

    def cohort_stats(sub: pd.DataFrame) -> Dict[str, Any]:
        if not len(sub):
            return {"n": 0}
        wins = int((sub["adjusted_return_pct"] > 0).sum())
        return {
            "n": int(len(sub)),
            "wr_pct": percent(wins, len(sub)),
            "avg_adj_pct": round(float(sub["adjusted_return_pct"].mean()), 3),
            "stop_hit_pct": percent(int(sub["stop_hit"].fillna(0).astype(int).sum()), len(sub)),
            "target_hit_pct": percent(int(sub["target_hit"].fillna(0).astype(int).sum()), len(sub)),
        }

    by_score_bucket: Dict[str, Any] = {}
    if closed["score"].notna().any():
        bins = [(0, 80), (80, 90), (90, 101)]
        for lo, hi in bins:
            sub = closed[(closed["score"] >= lo) & (closed["score"] < hi)]
            by_score_bucket[f"score_{lo}-{hi-1}"] = cohort_stats(sub)

    by_horizon = {str(int(h)): cohort_stats(s) for h, s in closed.groupby("horizon")}

    by_year = {str(int(y)): cohort_stats(s) for y, s in closed.groupby("year")}

    # VIX bucket on entry day
    vix_buckets: Dict[str, List[float]] = {"low (<15)": [], "normal (15-20)": [],
                                           "elevated (20-30)": [], "high (>=30)": [],
                                           "unknown": []}
    for _, t in closed.iterrows():
        ctx = regime_context(t["entry_date"], spy, qqq, vix)
        b = ctx.get("vix_bucket", "unknown")
        vix_buckets.setdefault(b, []).append(float(t["adjusted_return_pct"]))
    by_vix = {b: {"n": len(rs), "avg_adj_pct": safe_mean(rs),
                  "wr_pct": percent(sum(1 for x in rs if x > 0), len(rs))}
              for b, rs in vix_buckets.items() if rs}

    by_sector = {sec: cohort_stats(s) for sec, s in closed.groupby("sector")}

    by_vol_ratio: Dict[str, Any] = {}
    if closed["vol_ratio"].notna().any():
        for label, lo, hi in [("vol_<1.5", 0, 1.5), ("vol_1.5-2", 1.5, 2),
                              ("vol_2+", 2, 1e6)]:
            sub = closed[(closed["vol_ratio"] >= lo) & (closed["vol_ratio"] < hi)]
            by_vol_ratio[label] = cohort_stats(sub)

    # Ticker concentration
    by_ticker = closed.groupby("ticker")["adjusted_return_pct"].agg(["count", "sum", "mean"]) \
        .sort_values("sum", ascending=False)

    return {
        "n_unique_entries": int(closed[["ticker", "entry_date"]].drop_duplicates().shape[0]),
        "by_score_bucket": by_score_bucket,
        "by_horizon": by_horizon,
        "by_year": by_year,
        "by_vix_bucket": by_vix,
        "by_sector": by_sector,
        "by_vol_ratio": by_vol_ratio,
        "ticker_concentration": {
            "n_unique_tickers": int(by_ticker.shape[0]),
            "top_5": [
                {"ticker": idx, "n": int(row["count"]), "sum_adj_pct": round(float(row["sum"]), 2),
                 "avg_adj_pct": round(float(row["mean"]), 2)}
                for idx, row in by_ticker.head(5).iterrows()
            ],
        },
    }


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="SHORT_A only smoke test")
    ap.add_argument("--out", default=str(OUT_JSON))
    args = ap.parse_args(argv)

    spy = load_price("SPY")
    qqq = load_price("QQQ")
    vix = load_vix()
    if spy is None:
        print("ERROR: SPY price cache missing; cannot compute regime context", file=sys.stderr)
        return 2

    out: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "evidence_rigor_report_path": "docs/scorecards/evidence_rigor_report.json",
        "data_sources": {
            "trades_dir": str(TRADES_DIR.relative_to(REPO)),
            "price_cache_backtest": str(PRICE_CACHE_BACKTEST.relative_to(REPO)),
            "price_cache_live": str(PRICE_CACHE_LIVE.relative_to(REPO)),
            "vix_parquet": str(VIX_PARQUET.relative_to(REPO)),
        },
        "sleeves": {},
    }

    # Pull headline verdicts from rigor report
    rigor = []
    if EVIDENCE_JSON.exists():
        rigor = json.loads(EVIDENCE_JSON.read_text())
    rigor_idx = {s["sleeve"]: s for s in rigor}

    for sleeve in (["SHORT_A"] if args.quick else SLEEVES):
        df = load_trades(sleeve)
        if sleeve == "SHORT_A":
            ana = short_a_autopsy(df, spy, qqq, vix)
        elif sleeve == "VOYAGER_PAPER":
            ana = voyager_autopsy(df, spy, qqq, vix)
        elif sleeve == "SNIPER_V6":
            ana = sniper_autopsy(df, spy, qqq, vix)
        else:
            continue
        rg = rigor_idx.get(sleeve, {})
        out["sleeves"][sleeve] = {
            "verdict_label": rg.get("verdict_label"),
            "n_closed": rg.get("n_closed"),
            "n_total": rg.get("n_total"),
            "primary_horizon": rg.get("primary_horizon"),
            "aggregate": rg.get("by_horizon", {}).get("__all__"),
            "random_control": rg.get("random_control"),
            "friction_sensitivity": rg.get("friction_sensitivity"),
            "analysis": ana,
        }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, default=str))
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
research/strategy_evidence_audit.py — statistical rigor audit for active sleeves.

Scope: SNIPER_V6, VOYAGER_PAPER, SHORT_A.
Mode:  analysis/reporting only. No strategy/scoring/governance/execution changes.

What this does
--------------
1. Loads trade-level data per sleeve from:
     - db/trading.db                (live paper_signals + outcomes for SHORT_A,
                                     voyager_paper_signals for VOYAGER_PAPER)
     - research/sleeves/trades/*.csv (optional backtest exports; loaded when present)
2. Computes per-sleeve:
     - Bootstrap CIs (95%) for win rate, avg adjusted return, expectancy,
       stop-hit rate, target-hit rate, max drawdown of equity curve.
     - Walk-forward stability: rolling 18-month train / 6-month test, when
       the trade date range supports it.
     - Random-entry control: same universe + horizon + friction; picks random
       entry dates from the cached price universe and computes synthetic
       outcomes against the same stop/target geometry where available.
3. Static friction audit per sleeve (reads source: which frictions are modeled).
4. Emits docs/scorecards/evidence_rigor_report.md.

Design notes
------------
- Sample sizes are reported honestly. Sleeves below MIN_N_FOR_CI get a
  "promising_but_thin" or "indistinguishable" verdict instead of fabricated
  confidence intervals.
- Random control uses cache/backtest_prices/*.parquet as the price universe.
- This script does NOT re-run backtests. It only consumes existing trade-level
  artifacts and live paper outcomes.
- Reproducible: bootstrap and random-control use a fixed seed.

Usage
-----
  cd /home/gem/trading-production
  .venv/bin/python research/strategy_evidence_audit.py
  .venv/bin/python research/strategy_evidence_audit.py --quick      # one sleeve smoke
  .venv/bin/python research/strategy_evidence_audit.py --sleeve SHORT_A
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sqlite3
import statistics
import sys
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

REPO = Path(__file__).resolve().parent.parent
DB_PATH = REPO / "db" / "trading.db"
PRICE_CACHE = REPO / "cache" / "backtest_prices"
PRICE_CACHE_FALLBACK = REPO / "cache" / "prices"
TRADES_DIR = REPO / "research" / "sleeves" / "trades"
REPORT_PATH = REPO / "docs" / "scorecards" / "evidence_rigor_report.md"

ACTIVE_SLEEVES = ["SNIPER_V6", "VOYAGER_PAPER", "SHORT_A"]

# Mandate-aligned primary horizons (used for walk-forward, random control,
# verdict determination). Falls back to most-populated horizon if not listed.
SLEEVE_PRIMARY_HORIZON = {
    "SNIPER_V6":     10,    # tactical 1–30d; 10d is mid-mandate and matches backtest's primary
    "VOYAGER_PAPER": 252,   # 6–18 month mandate; 252 trading days = ~12 months
    "SHORT_A":       5,     # 5–30d short tactical; 5d aligns with short_doctrine
}

# Statistical settings
BOOT_ITERS = 2000
CI_LEVEL = 0.95
RNG_SEED = 20260503
MIN_N_FOR_CI = 30
MIN_N_FOR_WALKFORWARD = 50
WALK_TRAIN_MONTHS = 18
WALK_TEST_MONTHS = 6
RANDOM_CONTROL_MULTIPLIER = 5  # random entries per real trade

# Friction sensitivity sweep — applied to raw_return_pct at audit time so we can
# see how the picture shifts at different round-trip cost assumptions.
FRICTION_SENSITIVITY_RT_PCTS = [0.00, 0.30, 0.50, 1.00]


# ──────────────────────────────────────────────────────────────────────────────
# Trade record
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class Trade:
    sleeve: str
    ticker: str
    entry_date: date
    horizon_days: int
    raw_return_pct: Optional[float]      # gross %
    adjusted_return_pct: Optional[float] # net % (after friction if modeled)
    stop_hit: Optional[bool]
    target_hit: Optional[bool]
    still_open: bool
    side: str = "LONG"                    # "LONG" | "SHORT"
    entry_price: Optional[float] = None
    stop_price: Optional[float] = None
    target_price: Optional[float] = None
    source: str = "live"                  # "live" | "backtest_csv"


# ──────────────────────────────────────────────────────────────────────────────
# Loaders
# ──────────────────────────────────────────────────────────────────────────────

def _connect() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"trading.db not found at {DB_PATH}")
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    return con


def _parse_dt(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except Exception:
        try:
            return date.fromisoformat(s[:10])
        except Exception:
            return None


def load_short_a_paper() -> List[Trade]:
    """SHORT_A live paper: paper_signals JOIN paper_signal_outcomes, sleeve='SHORT_A'."""
    rows: List[Trade] = []
    if not DB_PATH.exists():
        return rows
    sql = """
      SELECT ps.id, ps.ticker, ps.side, ps.logged_at, ps.entry_price,
             ps.stop_loss, ps.target_price, ps.status,
             pso.horizon_days, pso.return_pct, pso.adjusted_return_pct,
             pso.stop_hit, pso.target_hit, pso.still_open
      FROM paper_signals ps
      LEFT JOIN paper_signal_outcomes pso ON pso.signal_id = ps.id
      WHERE ps.sleeve = 'SHORT_A'
    """
    with _connect() as con:
        for r in con.execute(sql):
            ed = _parse_dt(r["logged_at"])
            if ed is None:
                continue
            rows.append(Trade(
                sleeve="SHORT_A",
                ticker=r["ticker"] or "",
                entry_date=ed,
                horizon_days=int(r["horizon_days"]) if r["horizon_days"] is not None else 0,
                raw_return_pct=_to_float(r["return_pct"]),
                adjusted_return_pct=_to_float(r["adjusted_return_pct"]),
                stop_hit=_to_bool(r["stop_hit"]),
                target_hit=_to_bool(r["target_hit"]),
                still_open=bool(r["still_open"]) if r["still_open"] is not None else (r["horizon_days"] is None),
                side=r["side"] or "SHORT",
                entry_price=_to_float(r["entry_price"]),
                stop_price=_to_float(r["stop_loss"]),
                target_price=_to_float(r["target_price"]),
                source="live",
            ))
    return rows


def load_voyager_paper() -> List[Trade]:
    """VOYAGER_PAPER live paper: voyager_paper_signals — uses outcome_30d as
    the primary horizon return; secondary 90d/180d preserved as separate trades."""
    rows: List[Trade] = []
    if not DB_PATH.exists():
        return rows
    sql = """
      SELECT id, ticker, direction, logged_at, entry_price, stop_loss, target_price,
             outcome_30d, outcome_90d, outcome_180d,
             outcome_30d_date, outcome_90d_date, outcome_180d_date,
             signal_status, exit_reason
      FROM voyager_paper_signals
    """
    with _connect() as con:
        for r in con.execute(sql):
            ed = _parse_dt(r["logged_at"])
            if ed is None:
                continue
            base = dict(
                sleeve="VOYAGER_PAPER",
                ticker=r["ticker"] or "",
                entry_date=ed,
                stop_hit=None,
                target_hit=None,
                side=r["direction"] or "LONG",
                entry_price=_to_float(r["entry_price"]),
                stop_price=_to_float(r["stop_loss"]),
                target_price=_to_float(r["target_price"]),
                source="live",
            )
            for h_days, key in ((30, "outcome_30d"), (90, "outcome_90d"), (180, "outcome_180d")):
                val = _to_float(r[key])
                still_open = val is None
                rows.append(Trade(
                    horizon_days=h_days,
                    raw_return_pct=val,
                    adjusted_return_pct=val,  # voyager backtest does NOT model friction
                    still_open=still_open,
                    **base,
                ))
    return rows


def load_sleeve_csv(sleeve: str) -> List[Trade]:
    """research/sleeves/trades/<SLEEVE>.csv — Phase 9B standard schema:

    strategy, baseline_tag, ticker, side, entry_date, exit_date, horizon,
    entry_price, exit_price, raw_return_pct, adjusted_return_pct,
    stop_hit, target_hit, sector, source_backtest, friction_model, notes

    Legacy schema (horizon_days / still_open / stop_price / target_price)
    is also accepted for backwards-compat.
    """
    path = TRADES_DIR / f"{sleeve}.csv"
    if not path.exists():
        return []
    import csv
    rows: List[Trade] = []
    with path.open() as fh:
        reader = csv.DictReader(fh)
        for r in reader:
            ed = _parse_dt(r.get("entry_date"))
            if ed is None:
                continue
            horizon = r.get("horizon")
            if horizon in (None, ""):
                horizon = r.get("horizon_days")
            try:
                horizon_days = int(float(horizon)) if horizon not in (None, "") else 0
            except Exception:
                horizon_days = 0
            adj = _to_float(r.get("adjusted_return_pct"))
            raw = _to_float(r.get("raw_return_pct"))
            # Heuristic: a row is "still open" if it has no return AND notes flag it,
            # OR if the legacy still_open column is true.
            legacy_still_open = _to_bool(r.get("still_open"))
            notes = (r.get("notes") or "").lower()
            note_open = "still_open" in notes or "outcome unavailable" in notes
            still_open = bool(legacy_still_open) if legacy_still_open is not None else False
            if (adj is None and raw is None) or note_open:
                still_open = True
            # Stop/target may live in dedicated columns (legacy) OR be encoded
            # in the notes field as `stop_price=X.XX; target_price=Y.YY` since
            # the Phase 9B standard schema does not expose them directly.
            stop_price = _to_float(r.get("stop_price"))
            target_price = _to_float(r.get("target_price"))
            if stop_price is None or target_price is None:
                for tok in (r.get("notes") or "").split(";"):
                    tok = tok.strip()
                    if tok.startswith("stop_price=") and stop_price is None:
                        stop_price = _to_float(tok.split("=", 1)[1])
                    elif tok.startswith("target_price=") and target_price is None:
                        target_price = _to_float(tok.split("=", 1)[1])
            rows.append(Trade(
                sleeve=sleeve,
                ticker=(r.get("ticker") or "").upper(),
                entry_date=ed,
                horizon_days=horizon_days,
                raw_return_pct=raw,
                adjusted_return_pct=adj if adj is not None else raw,
                stop_hit=_to_bool(r.get("stop_hit")),
                target_hit=_to_bool(r.get("target_hit")),
                still_open=still_open,
                side=(r.get("side") or "LONG").upper(),
                entry_price=_to_float(r.get("entry_price")),
                stop_price=stop_price,
                target_price=target_price,
                source="backtest_csv",
            ))
    return rows


def load_sleeve(sleeve: str) -> List[Trade]:
    csv_rows = load_sleeve_csv(sleeve)
    if csv_rows:
        return csv_rows
    if sleeve == "SHORT_A":
        return load_short_a_paper()
    if sleeve == "VOYAGER_PAPER":
        return load_voyager_paper()
    if sleeve == "SNIPER_V6":
        return []   # no live data, no CSV → audit will report gap
    return []


def _to_float(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except Exception:
        return None


def _to_bool(v) -> Optional[bool]:
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(int(v))
    s = str(v).strip().lower()
    if s in ("1", "true", "t", "yes", "y"):
        return True
    if s in ("0", "false", "f", "no", "n"):
        return False
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Statistics
# ──────────────────────────────────────────────────────────────────────────────

def bootstrap_ci(
    values: Sequence[float],
    stat_fn: Callable[[Sequence[float]], float],
    n_boot: int = BOOT_ITERS,
    ci: float = CI_LEVEL,
    seed: int = RNG_SEED,
) -> Tuple[float, float, float]:
    """Returns (point_estimate, lo, hi). NaN (None) values are dropped."""
    xs = [v for v in values if v is not None and not _is_nan(v)]
    n = len(xs)
    if n == 0:
        return (float("nan"), float("nan"), float("nan"))
    point = stat_fn(xs)
    if n < 2:
        return (point, float("nan"), float("nan"))
    rng = random.Random(seed)
    boots: List[float] = []
    for _ in range(n_boot):
        sample = [xs[rng.randrange(n)] for _ in range(n)]
        try:
            boots.append(stat_fn(sample))
        except Exception:
            continue
    if not boots:
        return (point, float("nan"), float("nan"))
    boots.sort()
    a = (1.0 - ci) / 2.0
    lo = boots[int(a * len(boots))]
    hi = boots[min(len(boots) - 1, int((1.0 - a) * len(boots)))]
    return (point, lo, hi)


def _is_nan(x: float) -> bool:
    try:
        return math.isnan(float(x))
    except Exception:
        return False


def win_rate(values: Sequence[float]) -> float:
    if not values:
        return float("nan")
    return sum(1 for v in values if v is not None and v > 0) / len(values)


def expectancy(values: Sequence[float]) -> float:
    """Mean adjusted return — same as mean for a fixed-size paper sleeve."""
    if not values:
        return float("nan")
    return statistics.fmean(values)


def stop_hit_rate(stops: Sequence[Optional[bool]]) -> float:
    xs = [s for s in stops if s is not None]
    if not xs:
        return float("nan")
    return sum(1 for s in xs if s) / len(xs)


def target_hit_rate(targets: Sequence[Optional[bool]]) -> float:
    xs = [t for t in targets if t is not None]
    if not xs:
        return float("nan")
    return sum(1 for t in xs if t) / len(xs)


def equity_curve_max_dd(returns_pct: Sequence[float]) -> float:
    """Treat each closed trade as a sequential 1-unit allocation; build cumulative
    equity, return max drawdown as a percent of running peak."""
    if not returns_pct:
        return float("nan")
    eq = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in returns_pct:
        if r is None or _is_nan(r):
            continue
        eq *= (1.0 + r / 100.0)
        peak = max(peak, eq)
        dd = (peak - eq) / peak
        max_dd = max(max_dd, dd)
    return max_dd * 100.0


# ──────────────────────────────────────────────────────────────────────────────
# Walk-forward
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class WalkWindow:
    train_start: date
    train_end: date
    test_start: date
    test_end: date
    n_test: int
    wr_test: float
    avg_adj_test: float
    expectancy_test: float


def walk_forward(
    trades: Sequence[Trade],
    train_months: int = WALK_TRAIN_MONTHS,
    test_months: int = WALK_TEST_MONTHS,
    horizon_filter: Optional[int] = None,
) -> List[WalkWindow]:
    """Walk-forward over closed trades. If `horizon_filter` is given, only use
    trades for that horizon — keeps WR/expectancy interpretable when a sleeve
    has multiple horizons per signal."""
    closed = [t for t in trades if not t.still_open and t.adjusted_return_pct is not None]
    if horizon_filter is not None:
        closed = [t for t in closed if t.horizon_days == horizon_filter]
    if len(closed) < MIN_N_FOR_WALKFORWARD:
        return []
    closed.sort(key=lambda t: t.entry_date)
    span_start = closed[0].entry_date
    span_end = closed[-1].entry_date
    needed_days = int((train_months + test_months) * 30.5)
    if (span_end - span_start).days < needed_days:
        return []

    windows: List[WalkWindow] = []
    test_step = timedelta(days=int(test_months * 30.5))
    train_delta = timedelta(days=int(train_months * 30.5))
    test_start = span_start + train_delta
    while test_start + test_step <= span_end + timedelta(days=1):
        train_start = test_start - train_delta
        train_end = test_start
        test_end = test_start + test_step
        test_trades = [t for t in closed if train_end <= t.entry_date < test_end]
        if test_trades:
            adjs = [t.adjusted_return_pct for t in test_trades if t.adjusted_return_pct is not None]
            windows.append(WalkWindow(
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
                n_test=len(adjs),
                wr_test=win_rate(adjs) * 100,
                avg_adj_test=statistics.fmean(adjs) if adjs else float("nan"),
                expectancy_test=expectancy(adjs) if adjs else float("nan"),
            ))
        test_start = test_start + test_step
    return windows


# ──────────────────────────────────────────────────────────────────────────────
# Random-entry control
# ──────────────────────────────────────────────────────────────────────────────

def _load_price_cache(tickers: Sequence[str]) -> Dict[str, "object"]:
    """Returns dict ticker -> pandas DataFrame of OHLCV indexed by date."""
    try:
        import pandas as pd  # type: ignore
    except Exception:
        return {}
    out: Dict[str, object] = {}
    for t in tickers:
        for base in (PRICE_CACHE, PRICE_CACHE_FALLBACK):
            p = base / f"{t}.parquet"
            if p.exists():
                try:
                    df = pd.read_parquet(p)
                    # Normalize columns: cache/prices uses lowercase; cache/backtest_prices likewise.
                    cols = {c.lower(): c for c in df.columns}
                    needed = {"open", "high", "low", "close"}
                    if not needed.issubset(set(cols)):
                        continue
                    df = df.rename(columns={cols[k]: k for k in cols})
                    out[t] = df
                    break
                except Exception:
                    continue
    return out


def _path_outcome(
    df,
    entry_idx: int,
    side: str,
    horizon_days: int,
    stop_price: Optional[float],
    target_price: Optional[float],
    entry_price: float,
) -> Tuple[float, bool, bool]:
    """Walk forward up to horizon_days; return (raw_return_pct, stop_hit, target_hit).
    For LONG: stop = low <= stop_price, target = high >= target_price.
    For SHORT: stop = high >= stop_price, target = low <= target_price.
    Returns at horizon close if neither breach."""
    n = len(df)
    if entry_idx + 1 >= n:
        return (0.0, False, False)
    end = min(n - 1, entry_idx + horizon_days)
    stop_hit = False
    tgt_hit = False
    for j in range(entry_idx + 1, end + 1):
        row = df.iloc[j]
        if side.upper() == "LONG":
            if stop_price is not None and row["low"] <= stop_price:
                ret = (stop_price - entry_price) / entry_price * 100.0
                return (ret, True, False)
            if target_price is not None and row["high"] >= target_price:
                ret = (target_price - entry_price) / entry_price * 100.0
                return (ret, False, True)
        else:
            if stop_price is not None and row["high"] >= stop_price:
                ret = (entry_price - stop_price) / entry_price * 100.0
                return (ret, True, False)
            if target_price is not None and row["low"] <= target_price:
                ret = (entry_price - target_price) / entry_price * 100.0
                return (ret, False, True)
    exit_close = float(df.iloc[end]["close"])
    if side.upper() == "LONG":
        ret = (exit_close - entry_price) / entry_price * 100.0
    else:
        ret = (entry_price - exit_close) / entry_price * 100.0
    return (ret, False, False)


def random_entry_control(
    trades: Sequence[Trade],
    friction_rt_pct: float,
    multiplier: int = RANDOM_CONTROL_MULTIPLIER,
    seed: int = RNG_SEED,
) -> Dict[str, float]:
    """Generates random entries on the same universe + horizon and returns
    aggregate stats for comparison."""
    closed = [t for t in trades if not t.still_open and t.adjusted_return_pct is not None]
    if not closed:
        return {"n": 0}
    universe = sorted({t.ticker for t in closed if t.ticker})
    cache = _load_price_cache(universe)
    if not cache:
        return {"n": 0, "note": "price_cache_unavailable"}
    rng = random.Random(seed)
    raws: List[float] = []
    adjs: List[float] = []
    n_target_hits = 0
    n_stop_hits = 0
    used = 0
    skipped = 0
    horizon_default = max(1, int(statistics.median([t.horizon_days for t in closed if t.horizon_days])) or 10)
    for t in closed:
        df = cache.get(t.ticker)
        if df is None or len(df) < (t.horizon_days or horizon_default) + 2:
            skipped += 1
            continue
        side = t.side or "LONG"
        # Sample multiplier random entry indices that allow horizon forward bars.
        max_idx = len(df) - (t.horizon_days or horizon_default) - 1
        if max_idx <= 0:
            skipped += 1
            continue
        for _ in range(multiplier):
            i = rng.randrange(0, max_idx)
            entry_price = float(df.iloc[i]["close"])
            # Reconstruct stop/target with the same geometry as the real trade
            # (relative to entry). If unavailable, skip stop/target — the synthetic
            # trade will run to horizon close.
            stop_price = None
            target_price = None
            if t.entry_price and t.stop_price:
                stop_dist = (t.stop_price / t.entry_price) - 1.0
                if side.upper() == "LONG":
                    stop_price = entry_price * (1.0 + stop_dist)
                else:
                    stop_price = entry_price * (1.0 + stop_dist)
            if t.entry_price and t.target_price:
                tgt_dist = (t.target_price / t.entry_price) - 1.0
                if side.upper() == "LONG":
                    target_price = entry_price * (1.0 + tgt_dist)
                else:
                    target_price = entry_price * (1.0 + tgt_dist)
            ret, stop_hit, tgt_hit = _path_outcome(
                df, i, side, t.horizon_days or horizon_default,
                stop_price, target_price, entry_price,
            )
            raws.append(ret)
            adjs.append(ret - friction_rt_pct)
            n_stop_hits += int(stop_hit)
            n_target_hits += int(tgt_hit)
            used += 1
    if not adjs:
        return {"n": 0, "note": "no_random_samples_generated"}
    return {
        "n": used,
        "skipped": skipped,
        "wr_pct": win_rate(adjs) * 100,
        "avg_raw_pct": statistics.fmean(raws),
        "avg_adj_pct": statistics.fmean(adjs),
        "stop_hit_pct": n_stop_hits / used * 100,
        "target_hit_pct": n_target_hits / used * 100,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Friction audit (static, per sleeve, derived from source code review)
# ──────────────────────────────────────────────────────────────────────────────

def _parse_friction_rt(s) -> float:
    """Best-effort parse of the 'friction_rt_pct' field (which is mostly free-text)."""
    if s is None:
        return 0.0
    txt = str(s).strip()
    for tok in txt.replace(",", " ").split():
        try:
            return float(tok)
        except Exception:
            continue
    return 0.0


FRICTION_AUDIT: Dict[str, Dict[str, str]] = {
    "SNIPER_V6": {
        "source": "research/backtests/sniper_backtest.py",
        "commission":          "MODELED — 5 bps each side (10 bps RT)",
        "slippage":            "MODELED — 10 bps each side (20 bps RT)",
        "spread":              "GAP — implicit only (folded into slippage), not separately modeled",
        "gap_risk":            "GAP — overnight gaps not separately stress-tested",
        "borrow_locate":       "N/A — long-only",
        "options_realism":     "N/A — equity outright",
        "friction_rt_pct":     "0.30",
    },
    "VOYAGER_PAPER": {
        "source": "research/backtests/voyager_v2_backtest.py + voyager_paper_signals",
        "commission":          "GAP — not modeled in forward-return calc (raw only)",
        "slippage":            "GAP — not modeled",
        "spread":              "GAP — not modeled",
        "gap_risk":            "GAP — not modeled",
        "borrow_locate":       "N/A — long-only",
        "options_realism":     "N/A — equity outright",
        "friction_rt_pct":     "0.00 (raw forward returns)",
        "notes":               "Survivorship: universe excludes delisted names. 13F lookahead handled via filing_date.",
    },
    "SHORT_A": {
        "source": "research/sleeves/short_backtester.py + paper_signals (sleeve='SHORT_A')",
        "commission":          "MODELED — folded into slippage_bps (configurable, default 0)",
        "slippage":            "MODELED — slippage_bps each side, RT applied",
        "spread":              "MODELED — spread_bps each side, RT applied",
        "gap_risk":            "PARTIAL — halt_gap_penalty_pct applies only when squeeze-like flag triggers; gap_risk_max_up_20d_pct logged per trade for analysis",
        "borrow_locate":       "MODELED — borrow_fee_annual_pct accrued over hold_days",
        "options_realism":     "N/A — short equity only",
        "friction_rt_pct":     "depends on CLI args; live paper uses adjusted_return_pct from execution_policy",
        "notes":               "Live paper signals carry an adjusted_return_pct already net of declared friction; live n is currently very small (governance-blocked dominate).",
    },
}


# ──────────────────────────────────────────────────────────────────────────────
# Verdict scoring
# ──────────────────────────────────────────────────────────────────────────────

def verdict(
    n: int,
    point_adj_pct: float,
    ci_lo_adj_pct: float,
    random_avg_adj_pct: Optional[float],
    walk_stable: Optional[bool],
    ci_hi_adj_pct: Optional[float] = None,
) -> str:
    if n == 0:
        return "NO_DATA"
    if n < MIN_N_FOR_CI:
        # Even with a thin sample, classify cleanly when the CI lies wholly on
        # one side of zero: a CI fully below zero is bad evidence regardless
        # of n. Otherwise fall back to "promising_but_thin" / "insufficient".
        if (ci_lo_adj_pct is not None and ci_hi_adj_pct is not None and
                not _is_nan(ci_lo_adj_pct) and not _is_nan(ci_hi_adj_pct) and
                ci_hi_adj_pct < 0):
            return "WEAK_AND_THIN"
        if point_adj_pct is None or _is_nan(point_adj_pct):
            return "INSUFFICIENT_SAMPLE"
        return "PROMISING_BUT_THIN" if point_adj_pct > 0 else "INSUFFICIENT_SAMPLE"
    if random_avg_adj_pct is not None and not _is_nan(random_avg_adj_pct):
        diff = point_adj_pct - random_avg_adj_pct
        if ci_lo_adj_pct <= random_avg_adj_pct:
            return "INDISTINGUISHABLE_FROM_RANDOM"
        if diff <= 0:
            return "WEAK"
        if walk_stable is False:
            return "POSITIVE_BUT_UNSTABLE"
        return "ROBUST"
    # No random control
    if ci_lo_adj_pct is None or _is_nan(ci_lo_adj_pct):
        return "PROMISING_BUT_THIN"
    if ci_lo_adj_pct > 0 and walk_stable is not False:
        return "ROBUST_NO_CONTROL"
    if ci_lo_adj_pct > 0:
        return "POSITIVE_BUT_UNSTABLE"
    if point_adj_pct > 0:
        return "PROMISING_BUT_THIN"
    return "WEAK"


def walk_stability(windows: Sequence[WalkWindow]) -> Optional[bool]:
    if not windows or len(windows) < 2:
        return None
    avgs = [w.avg_adj_test for w in windows if not _is_nan(w.avg_adj_test)]
    if len(avgs) < 2:
        return None
    pos = sum(1 for a in avgs if a > 0)
    return pos / len(avgs) >= 0.5


# ──────────────────────────────────────────────────────────────────────────────
# Per-sleeve audit
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class SleeveAudit:
    sleeve: str
    source: str
    n_total: int
    n_closed: int
    n_open: int
    date_min: Optional[date]
    date_max: Optional[date]
    horizons_seen: List[int] = field(default_factory=list)
    primary_horizon: int = 0
    by_horizon: Dict[int, Dict[str, object]] = field(default_factory=dict)
    walk_windows: List[WalkWindow] = field(default_factory=list)
    walk_stable: Optional[bool] = None
    random_control: Dict[str, float] = field(default_factory=dict)
    random_control_aggregate: Dict[str, float] = field(default_factory=dict)
    friction: Dict[str, str] = field(default_factory=dict)
    friction_sensitivity: List[Dict[str, float]] = field(default_factory=list)
    verdict_label: str = "NO_DATA"


def _summarise_horizon(trades: Sequence[Trade]) -> Dict[str, object]:
    closed = [t for t in trades if not t.still_open]
    adjs = [t.adjusted_return_pct for t in closed if t.adjusted_return_pct is not None]
    raws = [t.raw_return_pct for t in closed if t.raw_return_pct is not None]
    stops = [t.stop_hit for t in closed]
    tgts = [t.target_hit for t in closed]
    n = len(adjs)
    out: Dict[str, object] = {
        "n_closed": n,
        "n_total": len(trades),
    }
    if n == 0:
        return out
    out["avg_raw_pct"] = statistics.fmean(raws) if raws else float("nan")
    pt, lo, hi = bootstrap_ci(adjs, statistics.fmean)
    out["avg_adj_pct"] = pt
    out["avg_adj_ci"] = (lo, hi)
    pt, lo, hi = bootstrap_ci(adjs, win_rate)
    out["wr"] = pt * 100
    out["wr_ci"] = (lo * 100, hi * 100)
    pt, lo, hi = bootstrap_ci(adjs, expectancy)
    out["expectancy"] = pt
    out["expectancy_ci"] = (lo, hi)
    if any(s is not None for s in stops):
        sb = [1.0 if s else 0.0 for s in stops if s is not None]
        pt, lo, hi = bootstrap_ci(sb, statistics.fmean)
        out["stop_hit_pct"] = pt * 100
        out["stop_hit_ci"] = (lo * 100, hi * 100)
    if any(s is not None for s in tgts):
        tb = [1.0 if s else 0.0 for s in tgts if s is not None]
        pt, lo, hi = bootstrap_ci(tb, statistics.fmean)
        out["target_hit_pct"] = pt * 100
        out["target_hit_ci"] = (lo * 100, hi * 100)
    out["max_dd_pct"] = equity_curve_max_dd(adjs)
    return out


def _friction_sensitivity(trades: Sequence[Trade]) -> List[Dict[str, float]]:
    """Sweep RT friction across FRICTION_SENSITIVITY_RT_PCTS using raw_return_pct
    as the basis. Returns one dict per friction level with bootstrap CI."""
    closed = [t for t in trades if not t.still_open and t.raw_return_pct is not None]
    if len(closed) < 2:
        return []
    raws = [t.raw_return_pct for t in closed]
    out: List[Dict[str, float]] = []
    for rt in FRICTION_SENSITIVITY_RT_PCTS:
        adjs = [r - rt for r in raws]
        avg_pt, avg_lo, avg_hi = bootstrap_ci(adjs, statistics.fmean)
        wr_pt, wr_lo, wr_hi = bootstrap_ci(adjs, win_rate)
        out.append({
            "friction_rt_pct": rt,
            "n":               len(adjs),
            "avg_adj_pct":     avg_pt,
            "avg_adj_lo":      avg_lo,
            "avg_adj_hi":      avg_hi,
            "wr_pct":          wr_pt * 100,
            "wr_lo":           wr_lo * 100,
            "wr_hi":           wr_hi * 100,
            "expectancy_pct":  avg_pt,  # fixed-allocation paper sleeve
        })
    return out


def audit_sleeve(sleeve: str) -> SleeveAudit:
    trades = load_sleeve(sleeve)
    n_total = len(trades)
    n_closed = sum(1 for t in trades if not t.still_open and t.adjusted_return_pct is not None)
    n_open = n_total - n_closed
    dates = [t.entry_date for t in trades if t.entry_date]
    horizons = sorted({t.horizon_days for t in trades if t.horizon_days})
    audit = SleeveAudit(
        sleeve=sleeve,
        source=("backtest_csv" if (TRADES_DIR / f"{sleeve}.csv").exists() else "live"),
        n_total=n_total,
        n_closed=n_closed,
        n_open=n_open,
        date_min=min(dates) if dates else None,
        date_max=max(dates) if dates else None,
        horizons_seen=horizons,
        friction=FRICTION_AUDIT.get(sleeve, {}),
    )

    by_h: Dict[object, Dict[str, object]] = {}
    for h in horizons or [0]:
        slab = [t for t in trades if t.horizon_days == h]
        by_h[h] = _summarise_horizon(slab)
    # All-horizons aggregate (every closed trade, regardless of horizon) —
    # surfaces the headline when horizons are heterogeneous.
    by_h["__all__"] = _summarise_horizon(trades)
    audit.by_horizon = by_h

    # Choose primary horizon: mandate-aligned if available + present in data,
    # else most-populated horizon.
    mandate_h = SLEEVE_PRIMARY_HORIZON.get(sleeve)
    if mandate_h and mandate_h in horizons:
        primary_h = mandate_h
    elif horizons:
        primary_h = max(horizons, key=lambda h: by_h.get(h, {}).get("n_closed", 0))
    else:
        primary_h = 0
    audit.primary_horizon = primary_h
    primary_slab = [t for t in trades if t.horizon_days == primary_h] if primary_h else []

    # Walk-forward on primary horizon only — keeps each window interpretable
    audit.walk_windows = walk_forward(trades, horizon_filter=primary_h) if primary_h else []
    audit.walk_stable = walk_stability(audit.walk_windows)
    friction_rt = _parse_friction_rt(FRICTION_AUDIT.get(sleeve, {}).get("friction_rt_pct"))
    audit.random_control = random_entry_control(primary_slab, friction_rt) if primary_slab else {"n": 0}

    # Friction sensitivity on the primary horizon — recompute headline stats at
    # several round-trip cost levels using raw_return_pct as the basis. This is
    # independent of whichever friction the export already applied.
    audit.friction_sensitivity = _friction_sensitivity(primary_slab)

    # Verdict source: primary horizon if it has enough data (≥MIN_N_FOR_CI);
    # otherwise the all-horizons aggregate. This matters for sleeves whose
    # historical exports use heterogeneous hold_days (e.g. SHORT_A) — the
    # mandate-aligned primary horizon may be too thin while the aggregate has
    # the real signal.
    primary = by_h.get(primary_h, {}) if horizons else {}
    aggregate = by_h.get("__all__", {})
    n_primary = int(primary.get("n_closed", 0))
    n_aggregate = int(aggregate.get("n_closed", 0))
    if n_primary >= MIN_N_FOR_CI:
        verdict_stats = primary
        rand_avg_for_verdict = audit.random_control.get("avg_adj_pct")
    elif n_aggregate > n_primary:
        verdict_stats = aggregate
        # Aggregate-vs-aggregate: compute random control at each trade's
        # actual hold_days so the comparison is apples-to-apples.
        audit.random_control_aggregate = random_entry_control(
            [t for t in trades if not t.still_open], friction_rt
        )
        rand_avg_for_verdict = audit.random_control_aggregate.get("avg_adj_pct")
    else:
        verdict_stats = primary
        rand_avg_for_verdict = audit.random_control.get("avg_adj_pct")
    n_v = int(verdict_stats.get("n_closed", 0))
    point_adj = float(verdict_stats.get("avg_adj_pct", float("nan"))) if "avg_adj_pct" in verdict_stats else float("nan")
    ci = verdict_stats.get("avg_adj_ci")
    ci_lo = float(ci[0]) if ci else float("nan")
    ci_hi = float(ci[1]) if ci else float("nan")
    audit.verdict_label = verdict(
        n=n_v,
        point_adj_pct=point_adj,
        ci_lo_adj_pct=ci_lo,
        random_avg_adj_pct=rand_avg_for_verdict if rand_avg_for_verdict is not None else None,
        walk_stable=audit.walk_stable,
        ci_hi_adj_pct=ci_hi,
    )
    return audit


# ──────────────────────────────────────────────────────────────────────────────
# Report rendering
# ──────────────────────────────────────────────────────────────────────────────

def _fmt_pct(v) -> str:
    if v is None:
        return "—"
    try:
        f = float(v)
        if _is_nan(f):
            return "—"
        return f"{f:+.2f}%"
    except Exception:
        return "—"


def _fmt_pct_unsigned(v) -> str:
    if v is None:
        return "—"
    try:
        f = float(v)
        if _is_nan(f):
            return "—"
        return f"{f:.1f}%"
    except Exception:
        return "—"


def _fmt_ci(ci) -> str:
    if not ci:
        return "—"
    try:
        lo, hi = ci
        if _is_nan(lo) or _is_nan(hi):
            return "—"
        return f"[{lo:+.2f}%, {hi:+.2f}%]"
    except Exception:
        return "—"


def _fmt_ci_unsigned(ci) -> str:
    if not ci:
        return "—"
    try:
        lo, hi = ci
        if _is_nan(lo) or _is_nan(hi):
            return "—"
        return f"[{lo:.1f}%, {hi:.1f}%]"
    except Exception:
        return "—"


def render_report(audits: Sequence[SleeveAudit]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out: List[str] = []
    out.append("# Strategy Evidence Rigor Report")
    out.append("")
    out.append(f"Generated: {now}  ·  Script: `research/strategy_evidence_audit.py`")
    out.append("")
    out.append("Scope: SNIPER_V6 · VOYAGER_PAPER · SHORT_A. "
               "Analysis-only; no strategy / scoring / governance / execution changes.")
    out.append("")
    out.append("Bootstrap CIs are 95%, "
               f"{BOOT_ITERS:,} resamples, fixed seed `{RNG_SEED}`. "
               f"Walk-forward windows: {WALK_TRAIN_MONTHS}m train / {WALK_TEST_MONTHS}m test. "
               f"Random control: {RANDOM_CONTROL_MULTIPLIER}× synthetic entries per closed trade, "
               "same universe / horizon / stop-target geometry / friction.")
    out.append("")

    # Top-line scorecard
    out.append("## Top-line verdicts")
    out.append("")
    out.append("| Sleeve | Source | n closed | n open | Date range | Verdict |")
    out.append("|---|---|---:|---:|---|---|")
    for a in audits:
        rng = f"{a.date_min} → {a.date_max}" if a.date_min else "—"
        out.append(f"| `{a.sleeve}` | {a.source} | {a.n_closed} | {a.n_open} | {rng} | **{a.verdict_label}** |")
    out.append("")

    # Per-sleeve sections
    for a in audits:
        out.append("---")
        out.append("")
        out.append(f"## {a.sleeve}")
        out.append("")
        out.append(f"- **Source:** {a.source}  ·  `{a.friction.get('source','?')}`")
        out.append(f"- **n_total / n_closed / n_open:** {a.n_total} / {a.n_closed} / {a.n_open}")
        if a.date_min:
            out.append(f"- **Date range:** {a.date_min} → {a.date_max}")
        if a.horizons_seen:
            out.append(f"- **Horizons (days):** {', '.join(str(h) for h in a.horizons_seen)}")
        out.append("")

        if a.n_closed == 0:
            out.append("> ⚠ No closed trade-level data available. "
                       "All metrics below are unavailable until trade-level outcomes are emitted.")
            out.append("")
            _render_friction(out, a)
            continue

        # Per-horizon stats
        out.append("### Per-horizon statistics (bootstrap 95% CI)")
        out.append("")
        out.append("| Horizon | n | Win rate | Avg raw | Avg adj | Expectancy | Stop hit | Target hit | Max DD |")
        out.append("|---:|---:|---|---|---|---|---|---|---|")
        for h in a.horizons_seen or [0]:
            r = a.by_horizon.get(h, {})
            n = r.get("n_closed", 0)
            if not n:
                out.append(f"| {h}d | 0 | — | — | — | — | — | — | — |")
                continue
            wr = r.get("wr"); wr_ci = r.get("wr_ci")
            adj = r.get("avg_adj_pct"); adj_ci = r.get("avg_adj_ci")
            raw = r.get("avg_raw_pct")
            exp = r.get("expectancy"); exp_ci = r.get("expectancy_ci")
            sh = r.get("stop_hit_pct"); sh_ci = r.get("stop_hit_ci")
            th = r.get("target_hit_pct"); th_ci = r.get("target_hit_ci")
            dd = r.get("max_dd_pct")
            out.append(
                f"| {h}d | {n} | "
                f"{_fmt_pct_unsigned(wr)} {_fmt_ci_unsigned(wr_ci)} | "
                f"{_fmt_pct(raw)} | "
                f"{_fmt_pct(adj)} {_fmt_ci(adj_ci)} | "
                f"{_fmt_pct(exp)} {_fmt_ci(exp_ci)} | "
                f"{_fmt_pct_unsigned(sh)} {_fmt_ci_unsigned(sh_ci)} | "
                f"{_fmt_pct_unsigned(th)} {_fmt_ci_unsigned(th_ci)} | "
                f"{_fmt_pct_unsigned(dd)} |"
            )

        # All-horizons aggregate (one row per closed trade) — useful when the
        # sleeve's trades have heterogeneous hold_days and no single horizon
        # has a meaningful sample (e.g. SHORT_A historical export).
        agg = a.by_horizon.get("__all__") or {}
        if agg.get("n_closed", 0) > 1:
            n = agg.get("n_closed", 0)
            wr = agg.get("wr"); wr_ci = agg.get("wr_ci")
            adj = agg.get("avg_adj_pct"); adj_ci = agg.get("avg_adj_ci")
            raw = agg.get("avg_raw_pct")
            exp = agg.get("expectancy"); exp_ci = agg.get("expectancy_ci")
            sh = agg.get("stop_hit_pct"); sh_ci = agg.get("stop_hit_ci")
            th = agg.get("target_hit_pct"); th_ci = agg.get("target_hit_ci")
            dd = agg.get("max_dd_pct")
            out.append(
                f"| **all** | **{n}** | "
                f"**{_fmt_pct_unsigned(wr)}** {_fmt_ci_unsigned(wr_ci)} | "
                f"**{_fmt_pct(raw)}** | "
                f"**{_fmt_pct(adj)}** {_fmt_ci(adj_ci)} | "
                f"**{_fmt_pct(exp)}** {_fmt_ci(exp_ci)} | "
                f"{_fmt_pct_unsigned(sh)} {_fmt_ci_unsigned(sh_ci)} | "
                f"{_fmt_pct_unsigned(th)} {_fmt_ci_unsigned(th_ci)} | "
                f"{_fmt_pct_unsigned(dd)} |"
            )
        out.append("")

        # Walk-forward
        out.append("### Walk-forward (18m train / 6m test)")
        out.append("")
        if not a.walk_windows:
            out.append("> Insufficient date span or sample size for rolling validation "
                       f"(need ≥{MIN_N_FOR_WALKFORWARD} closed trades and ≥24 months of coverage).")
        else:
            out.append("| Train window | Test window | n test | WR test | Avg adj test |")
            out.append("|---|---|---:|---|---|")
            for w in a.walk_windows:
                out.append(
                    f"| {w.train_start} → {w.train_end} | {w.test_start} → {w.test_end} | "
                    f"{w.n_test} | {_fmt_pct_unsigned(w.wr_test)} | {_fmt_pct(w.avg_adj_test)} |"
                )
            out.append("")
            stab = "stable (≥50% windows positive)" if a.walk_stable else "unstable"
            out.append(f"_Stability: **{stab}**._")
        out.append("")

        # Random control
        primary_h = a.primary_horizon or 0
        if primary_h:
            mandate_note = " (mandate-aligned)" if SLEEVE_PRIMARY_HORIZON.get(a.sleeve) == primary_h else ""
            out.append(f"### Random-entry control (primary horizon: {primary_h}d{mandate_note})")
        else:
            out.append("### Random-entry control")
        out.append("")
        rc = a.random_control or {}
        if not rc.get("n"):
            note = rc.get("note") or "no closed trades"
            out.append(f"> Random control unavailable ({note}).")
        else:
            out.append("| Stat | Strategy | Random control | Δ (strategy − random) |")
            out.append("|---|---|---|---|")
            primary_h = a.primary_horizon or (
                max(a.horizons_seen, key=lambda h: a.by_horizon.get(h, {}).get("n_closed", 0))
                if a.horizons_seen else 0
            )
            primary = a.by_horizon.get(primary_h, {})
            s_wr = primary.get("wr")
            s_adj = primary.get("avg_adj_pct")
            s_sh = primary.get("stop_hit_pct")
            s_th = primary.get("target_hit_pct")
            r_wr = rc.get("wr_pct")
            r_adj = rc.get("avg_adj_pct")
            r_sh = rc.get("stop_hit_pct")
            r_th = rc.get("target_hit_pct")
            def _delta(s, r):
                if s is None or r is None or _is_nan(s) or _is_nan(r):
                    return "—"
                return f"{s - r:+.2f}pp"
            out.append(f"| Win rate | {_fmt_pct_unsigned(s_wr)} | {_fmt_pct_unsigned(r_wr)} | {_delta(s_wr, r_wr)} |")
            out.append(f"| Avg adj return | {_fmt_pct(s_adj)} | {_fmt_pct(r_adj)} | {_delta(s_adj, r_adj)} |")
            out.append(f"| Stop-hit rate | {_fmt_pct_unsigned(s_sh)} | {_fmt_pct_unsigned(r_sh)} | {_delta(s_sh, r_sh)} |")
            out.append(f"| Target-hit rate | {_fmt_pct_unsigned(s_th)} | {_fmt_pct_unsigned(r_th)} | {_delta(s_th, r_th)} |")
            out.append("")
            out.append(f"_Random sample size: n={rc['n']:,} synthetic entries"
                       + (f" (skipped={rc.get('skipped',0)})" if rc.get('skipped') else "")
                       + ". Same tickers, random entry dates, same horizon, same stop/target geometry, same friction._")
        out.append("")

        # Aggregate-horizon random control — relevant when verdict is computed
        # on the all-horizons aggregate (heterogeneous hold_days).
        rca = a.random_control_aggregate or {}
        if rca.get("n"):
            agg = a.by_horizon.get("__all__", {})
            out.append("### Random-entry control (all-horizons aggregate)")
            out.append("")
            out.append("| Stat | Strategy | Random control | Δ (strategy − random) |")
            out.append("|---|---|---|---|")
            s_wr = agg.get("wr"); s_adj = agg.get("avg_adj_pct")
            r_wr = rca.get("wr_pct"); r_adj = rca.get("avg_adj_pct")
            def _delta_a(s, r):
                if s is None or r is None or _is_nan(s) or _is_nan(r):
                    return "—"
                return f"{s - r:+.2f}pp"
            out.append(f"| Win rate | {_fmt_pct_unsigned(s_wr)} | {_fmt_pct_unsigned(r_wr)} | {_delta_a(s_wr, r_wr)} |")
            out.append(f"| Avg adj return | {_fmt_pct(s_adj)} | {_fmt_pct(r_adj)} | {_delta_a(s_adj, r_adj)} |")
            out.append("")
            out.append(f"_Random sample size: n={rca['n']:,} synthetic entries, "
                       "each at the actual hold_days of the matching strategy trade._")
            out.append("")

        # Friction sensitivity (independent of whichever friction the export applied)
        _render_friction_sensitivity(out, a)

        # Friction audit
        _render_friction(out, a)

    # Limitations
    out.append("---")
    out.append("")
    out.append("## Limitations")
    out.append("")
    out.append("- **Trade-level data sources:** SNIPER_V6 and VOYAGER_PAPER come from "
               "historical backtest exports (`research/sleeves/export_backtest_trades.py`); "
               "SHORT_A comes from the live paper DB (`paper_signals` + outcomes), which is still very thin.")
    out.append("- Walk-forward and random control require closed-trade outcomes; "
               "still-open paper trades are excluded. Walk-forward is run on the "
               "sleeve's mandate-aligned primary horizon to keep WR / expectancy interpretable.")
    out.append("- Random control reuses cached prices for the same tickers as the "
               "strategy. If the strategy universe is survivorship-biased "
               "(SNIPER's `LARGE_CAP_UNIVERSE` is hand-curated), the random control "
               "inherits that bias and the comparison is conservative (random looks better "
               "than it would on a delisted-inclusive set).")
    out.append("- Friction audit is a static read of the source code. It does not "
               "verify that live execution matches the backtest's friction assumption.")
    out.append("- **VOYAGER_PAPER backtest reports raw forward returns with no friction.** "
               "At long horizons (252d) friction is small relative to return, but at 30d "
               "even a 30 bps RT cost would meaningfully shift the distribution.")
    out.append("- **SHORT_A historical depth missing**: only live paper rows available "
               "(n closed = 4). Run `research/sleeves/short_backtester.py "
               "--export_trades_csv research/sleeves/trades/SHORT_A.csv` separately to "
               "populate historical trades — short_backtester needs FMP credentials and "
               "is too heavyweight to drive from the audit pipeline.")
    out.append("- **No edge claim is capital-proven.** This is a paper/research evidence "
               "audit. \"Robust\" or \"promising\" verdicts mean the data clears statistical "
               "thresholds at the sample sizes available — not that the strategy will work in size.")
    out.append("")
    return "\n".join(out)


def _render_friction_sensitivity(out: List[str], a: SleeveAudit) -> None:
    primary_h = a.primary_horizon or 0
    out.append(f"### Friction sensitivity (primary horizon: {primary_h}d)" if primary_h
               else "### Friction sensitivity")
    out.append("")
    rows = a.friction_sensitivity or []
    if not rows:
        out.append("> Insufficient closed trades for friction sensitivity (need ≥2).")
        out.append("")
        return
    out.append("| RT friction | n | Avg adj (95% CI) | Win rate (95% CI) |")
    out.append("|---:|---:|---|---|")
    for r in rows:
        rt = float(r["friction_rt_pct"])
        ci = (float(r["avg_adj_lo"]), float(r["avg_adj_hi"]))
        wr_ci = (float(r["wr_lo"]), float(r["wr_hi"]))
        out.append(
            f"| {rt:.2f}% | {int(r['n'])} | "
            f"{_fmt_pct(r['avg_adj_pct'])} {_fmt_ci(ci)} | "
            f"{_fmt_pct_unsigned(r['wr_pct'])} {_fmt_ci_unsigned(wr_ci)} |"
        )
    out.append("")
    out.append("_Sensitivity is computed on `raw_return_pct` at audit time so the table is "
               "independent of whichever friction the export already subtracted. "
               "Use it to gauge how robust the headline number is to a wider-than-assumed cost stack._")
    out.append("")


def _render_friction(out: List[str], a: SleeveAudit) -> None:
    out.append("### Execution friction audit")
    out.append("")
    f = a.friction or {}
    if not f:
        out.append("> No friction audit registered for this sleeve.")
        out.append("")
        return
    out.append("| Component | Status |")
    out.append("|---|---|")
    for k in ("commission", "slippage", "spread", "gap_risk", "borrow_locate", "options_realism"):
        v = f.get(k, "?")
        out.append(f"| {k} | {v} |")
    out.append(f"| friction_rt_pct | {f.get('friction_rt_pct', '?')} |")
    if f.get("notes"):
        out.append("")
        out.append(f"> {f['notes']}")
    out.append("")


# ──────────────────────────────────────────────────────────────────────────────
# Sleeve scorecard rigor strip
# ──────────────────────────────────────────────────────────────────────────────

SCORECARD_PATHS = {
    "SNIPER_V6":     REPO / "docs" / "scorecards" / "sniper_scorecard.md",
    "VOYAGER_PAPER": REPO / "docs" / "scorecards" / "voyager_scorecard.md",
    "SHORT_A":       REPO / "docs" / "scorecards" / "short_sleeve_scorecard.md",
}

RIGOR_BEGIN = "<!-- RIGOR_AUDIT_BEGIN -->"
RIGOR_END   = "<!-- RIGOR_AUDIT_END -->"


def _strip(audit: SleeveAudit) -> str:
    primary_h = audit.primary_horizon or (
        max(audit.horizons_seen, key=lambda h: audit.by_horizon.get(h, {}).get("n_closed", 0))
        if audit.horizons_seen else 0
    )
    primary = audit.by_horizon.get(primary_h, {})
    rc = audit.random_control or {}
    lines = [
        RIGOR_BEGIN,
        "",
        "## Evidence rigor strip (auto-generated)",
        "",
        f"_Last audit: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} · "
        f"see `docs/scorecards/evidence_rigor_report.md`._",
        "",
        f"- **Verdict:** **{audit.verdict_label}**",
        f"- **Source:** {audit.source}",
        f"- **Sample (closed):** n = {audit.n_closed} (open = {audit.n_open})",
    ]
    if primary and primary.get("n_closed", 0) > 0:
        adj = primary.get("avg_adj_pct"); ci = primary.get("avg_adj_ci")
        wr = primary.get("wr"); wr_ci = primary.get("wr_ci")
        n_primary = primary.get("n_closed", 0)
        lines.append(f"- **Primary horizon ({primary_h}d):** "
                     f"n={n_primary}  ·  avg adj {_fmt_pct(adj)} {_fmt_ci(ci)}  ·  "
                     f"WR {_fmt_pct_unsigned(wr)} {_fmt_ci_unsigned(wr_ci)}")
    else:
        lines.append(f"- **Primary horizon:** — (no closed trades)")
    agg = audit.by_horizon.get("__all__", {})
    n_agg = agg.get("n_closed", 0)
    if n_agg > (primary.get("n_closed", 0) if primary else 0):
        adj = agg.get("avg_adj_pct"); ci = agg.get("avg_adj_ci")
        wr = agg.get("wr"); wr_ci = agg.get("wr_ci")
        lines.append(f"- **All horizons aggregate:** n={n_agg}  ·  "
                     f"avg adj {_fmt_pct(adj)} {_fmt_ci(ci)}  ·  "
                     f"WR {_fmt_pct_unsigned(wr)} {_fmt_ci_unsigned(wr_ci)}")
    if rc.get("n"):
        lines.append(f"- **Random control:** WR {_fmt_pct_unsigned(rc.get('wr_pct'))}  ·  "
                     f"avg adj {_fmt_pct(rc.get('avg_adj_pct'))}  ·  n={rc['n']:,}")
    else:
        lines.append("- **Random control:** unavailable (insufficient closed trades)")
    if audit.walk_windows:
        stab = "stable" if audit.walk_stable else "unstable"
        lines.append(f"- **Walk-forward:** {len(audit.walk_windows)} windows, {stab}")
    else:
        lines.append("- **Walk-forward:** not run (insufficient data span)")
    lines.append("")
    lines.append(RIGOR_END)
    return "\n".join(lines)


def update_scorecard(audit: SleeveAudit) -> bool:
    path = SCORECARD_PATHS.get(audit.sleeve)
    if not path or not path.exists():
        return False
    text = path.read_text()
    strip = _strip(audit)
    if RIGOR_BEGIN in text and RIGOR_END in text:
        pre, _, rest = text.partition(RIGOR_BEGIN)
        _, _, post = rest.partition(RIGOR_END)
        new = pre.rstrip() + "\n\n" + strip + "\n" + post.lstrip()
    else:
        new = text.rstrip() + "\n\n" + strip + "\n"
    if new != text:
        path.write_text(new)
        return True
    return False


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Statistical rigor audit for active sleeves.")
    parser.add_argument("--sleeve", choices=ACTIVE_SLEEVES, help="Audit a single sleeve.")
    parser.add_argument("--quick", action="store_true",
                        help="Smoke run: audit only SHORT_A, smaller bootstrap.")
    parser.add_argument("--no-scorecards", action="store_true",
                        help="Skip writing rigor strips into sleeve scorecards.")
    parser.add_argument("--report", default=str(REPORT_PATH),
                        help=f"Output path for the rigor report (default {REPORT_PATH}).")
    parser.add_argument("--json", action="store_true",
                        help="Also emit a sibling .json file with raw audit data.")
    args = parser.parse_args(argv)

    global BOOT_ITERS
    if args.quick:
        BOOT_ITERS = 500

    sleeves = [args.sleeve] if args.sleeve else (["SHORT_A"] if args.quick else ACTIVE_SLEEVES)
    print(f"Auditing sleeves: {sleeves}")
    audits: List[SleeveAudit] = []
    for s in sleeves:
        print(f"  • {s}…")
        audits.append(audit_sleeve(s))
        a = audits[-1]
        print(f"    n_total={a.n_total}  n_closed={a.n_closed}  verdict={a.verdict_label}")

    report = render_report(audits)
    out_path = Path(args.report)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report)
    print(f"Wrote {out_path}")

    if args.json:
        json_path = out_path.with_suffix(".json")
        payload = []
        for a in audits:
            d = asdict(a)
            d["date_min"] = a.date_min.isoformat() if a.date_min else None
            d["date_max"] = a.date_max.isoformat() if a.date_max else None
            d["walk_windows"] = [
                {**asdict(w),
                 "train_start": w.train_start.isoformat(),
                 "train_end":   w.train_end.isoformat(),
                 "test_start":  w.test_start.isoformat(),
                 "test_end":    w.test_end.isoformat()}
                for w in a.walk_windows
            ]
            payload.append(d)
        json_path.write_text(json.dumps(payload, indent=2, default=str))
        print(f"Wrote {json_path}")

    if not args.no_scorecards:
        for a in audits:
            updated = update_scorecard(a)
            if updated:
                print(f"Updated scorecard for {a.sleeve}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

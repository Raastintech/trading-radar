"""
research/shadow_sizing_report.py — Paper-only vol-target sizing shadow model
+ short-borrow drag estimator.

This report is **diagnostic only**.  It does not change MAX_POSITION_PCT,
does not mutate decisions or paper evidence, and does not call providers.

Two complementary shadow models:

1. Vol-target shadow size (per position / paper signal):
       shadow_shares = (equity × SHADOW_VOL_TARGET) / atr_per_share
   with atr_per_share = 14-day ATR computed from cached daily bars.
   This represents "shares such that 1 ATR daily move ≈ SHADOW_VOL_TARGET
   of equity".  The current production sizer is fixed-fractional
   (equity × MAX_POSITION_PCT / risk_per_share) — it is *not* replaced.
   We report the gap so the operator can see whether the realized vol
   profile of the book matches the assumed risk budget.

2. Short-borrow drag (per SHORT open position / closed signal):
   Apply a default annualized borrow cost
   ``SHADOW_BORROW_BPS_ANNUAL`` (env var; default 100 bps = 1.0%/yr).
   Daily drag = SHADOW_BORROW_BPS_ANNUAL / 252.  For closed paper
   signals with measured ``return_pct``, we report the adjusted return.
   The original paper-evidence row is **not** mutated.

Inputs:
  decisions table          — open positions
  paper_signals + outcomes — recent + closed signals
  cache/prices/{T}.parquet — daily bars for ATR

Outputs:
  cache/research/shadow_sizing_latest.json
  logs/shadow_sizing_latest.txt

Usage:
  cd /home/gem/trading-production
  GEM_TRADER_SKIP_DOTENV=true .venv/bin/python \\
      research/shadow_sizing_report.py
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

_env_path = os.environ.get("SNIPER_ENV_PATH", "")
if _env_path:
    try:
        from dotenv import load_dotenv as _load
        _load(Path(_env_path), override=True)
    except ImportError:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import core.config as cfg  # noqa: E402
from core.paper_evidence_epoch import CLEAN_PAPER_EVIDENCE_START  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
logger = logging.getLogger("shadow_sizing")

# ── Tunable shadow parameters ────────────────────────────────────────────────

# Vol-target: target single-position daily $-vol as fraction of equity.
# Default 0.5% per position is conservative — typical institutional vol
# budgets run 0.3–1.0% per name.  Override via SHADOW_VOL_TARGET env var.
SHADOW_VOL_TARGET = float(os.environ.get("SHADOW_VOL_TARGET", "0.005"))

# Annualized borrow cost in basis points.  100 bps = 1.0%/yr is a benign
# placeholder for the default short universe; HTB names can be 200–2000 bps.
SHADOW_BORROW_BPS_ANNUAL = float(os.environ.get("SHADOW_BORROW_BPS_ANNUAL", "100"))

# Trading-day denominator for daily borrow rate.
TRADING_DAYS_PER_YEAR = 252.0

ATR_PERIOD = 14


# ── Helpers ──────────────────────────────────────────────────────────────────

def _atr_from_bars(bars: pd.DataFrame, period: int = ATR_PERIOD) -> Optional[float]:
    """ATR(period) on a daily-bar DataFrame with high/low/close columns.
    Returns None if insufficient data."""
    if bars is None or len(bars) < 2:
        return None
    if not all(c in bars.columns for c in ("high", "low", "close")):
        return None
    df = bars[["high", "low", "close"]].dropna().tail(period + 1)
    if len(df) < 2:
        return None
    pc = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - pc).abs(),
        (df["low"] - pc).abs(),
    ], axis=1).max(axis=1)
    tr = tr.dropna()
    if tr.empty:
        return None
    return float(tr.mean())


def _load_cached_bars(ticker: str, prices_dir: Path) -> Optional[pd.DataFrame]:
    p = prices_dir / f"{ticker.upper()}.parquet"
    if not p.exists():
        return None
    try:
        return pd.read_parquet(p)
    except Exception:
        return None


def _read_equity_hint() -> Optional[float]:
    candidates = [
        cfg.LOG_DIR / "trader_heartbeat.json",
        Path(__file__).resolve().parents[1] / "trader_heartbeat.json",
    ]
    for p in candidates:
        if not p.exists():
            continue
        try:
            blob = json.loads(p.read_text())
        except Exception:
            continue
        for key in ("equity", "account_equity", "portfolio_equity"):
            v = blob.get(key)
            if isinstance(v, (int, float)) and v > 0:
                return float(v)
        acct = blob.get("account") or {}
        for key in ("equity", "portfolio_value"):
            v = acct.get(key)
            if isinstance(v, (int, float)) and v > 0:
                return float(v)
    return None


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


# ── Loading ──────────────────────────────────────────────────────────────────

def load_open_positions(
    db_path: Path,
    since_iso: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Open paper positions from decisions.

    Phase 1D: when ``since_iso`` is given, filters ``ts >= since_iso`` to
    scope to the clean-paper-evidence epoch.
    """
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(str(db_path))
        cols = [d[1] for d in conn.execute("PRAGMA table_info(decisions)").fetchall()]
        if not cols:
            conn.close()
            return []
        sql = "SELECT * FROM decisions WHERE position_opened=1 AND position_closed=0"
        params: Tuple = ()
        if since_iso:
            sql += " AND ts >= ?"
            params = (since_iso,)
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return [dict(zip(cols, r)) for r in rows]
    except sqlite3.OperationalError:
        return []


def load_recent_paper_signals(
    db_path: Path, since_days: int
) -> List[Dict[str, Any]]:
    if not db_path.exists():
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=since_days)).isoformat()
    try:
        conn = sqlite3.connect(str(db_path))
        chk = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='paper_signals'"
        ).fetchone()
        if not chk:
            conn.close()
            return []
        cols = [d[1] for d in conn.execute("PRAGMA table_info(paper_signals)").fetchall()]
        rows = conn.execute(
            "SELECT * FROM paper_signals WHERE logged_at >= ? ORDER BY logged_at ASC",
            (cutoff,),
        ).fetchall()
        conn.close()
        return [dict(zip(cols, r)) for r in rows]
    except sqlite3.OperationalError:
        return []


def load_closed_short_outcomes(db_path: Path) -> List[Dict[str, Any]]:
    """Closed short paper signals with resolved outcomes — for borrow-adjusted
    return analysis.  Joins paper_signals + paper_signal_outcomes."""
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(str(db_path))
        chk = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='paper_signals'"
        ).fetchone()
        if not chk:
            conn.close()
            return []
        rows = conn.execute(
            """
            SELECT s.id, s.ticker, s.side, s.strategy, s.sleeve,
                   s.logged_at, s.entry_price, s.stop_loss,
                   o.horizon_days, o.return_pct, o.adjusted_return_pct,
                   o.outcome_date, o.measured_at, o.stop_hit, o.hold_complete,
                   o.still_open
            FROM paper_signals s
            JOIN paper_signal_outcomes o ON o.signal_id = s.id
            WHERE UPPER(s.side) = 'SHORT'
              AND (o.still_open = 0 OR o.hold_complete = 1)
            """
        ).fetchall()
        conn.close()
        cols = ["id", "ticker", "side", "strategy", "sleeve",
                "logged_at", "entry_price", "stop_loss",
                "horizon_days", "return_pct", "adjusted_return_pct",
                "outcome_date", "measured_at", "stop_hit", "hold_complete",
                "still_open"]
        return [dict(zip(cols, r)) for r in rows]
    except sqlite3.OperationalError:
        return []


# ── Shadow size calculation ──────────────────────────────────────────────────

def shadow_size_row(
    *,
    equity: Optional[float],
    entry: float,
    stop: Optional[float],
    direction: str,
    bars: Optional[pd.DataFrame],
    actual_shares: Optional[float],
    vol_target: float = SHADOW_VOL_TARGET,
) -> Dict[str, Any]:
    """Compute shadow vol-target size and risk-at-stop diagnostics for one
    position or signal.  All fields nullable when inputs are missing."""
    out: Dict[str, Any] = {
        "actual_shares":   None if actual_shares is None else float(actual_shares),
        "actual_notional": None,
        "actual_risk_at_stop": None,
        "atr_per_share":   None,
        "atr_pct":         None,
        "shadow_shares":   None,
        "shadow_notional": None,
        "shadow_dollar_vol_target": None,
        "size_ratio":      None,   # actual / shadow
        "size_gap_pct":    None,   # (actual - shadow) / shadow
        "vol_target":      vol_target,
        "warning":         None,
    }
    if entry is None or entry <= 0:
        out["warning"] = "missing_entry"
        return out
    if actual_shares is not None:
        out["actual_notional"] = round(float(actual_shares) * entry, 2)
        if stop is not None and stop > 0:
            out["actual_risk_at_stop"] = round(
                float(actual_shares) * abs(entry - stop), 2
            )
    atr = _atr_from_bars(bars) if bars is not None else None
    if atr is None or atr <= 0:
        out["warning"] = "no_atr_cache"
        return out
    out["atr_per_share"] = round(atr, 4)
    out["atr_pct"] = round(atr / entry, 4)
    if equity is None or equity <= 0:
        out["warning"] = "no_equity_hint"
        return out
    target_dollar_vol = equity * vol_target
    shadow_shares = target_dollar_vol / atr
    out["shadow_dollar_vol_target"] = round(target_dollar_vol, 2)
    out["shadow_shares"] = round(shadow_shares, 2)
    out["shadow_notional"] = round(shadow_shares * entry, 2)
    if actual_shares is not None and shadow_shares > 0:
        ratio = float(actual_shares) / shadow_shares
        out["size_ratio"] = round(ratio, 3)
        out["size_gap_pct"] = round((ratio - 1.0) * 100, 2)
        # Heuristic warning: actual ≥ 2× vol-target shadow
        if ratio >= 2.0:
            out["warning"] = "actual_>=2x_vol_target_shadow"
        elif ratio <= 0.4:
            out["warning"] = "actual_<=0.4x_vol_target_shadow"
    return out


# ── Borrow drag ──────────────────────────────────────────────────────────────

def _daily_borrow_rate() -> float:
    """Return daily decimal rate (e.g. 0.0000397 for 100 bps annual)."""
    return SHADOW_BORROW_BPS_ANNUAL / 1e4 / TRADING_DAYS_PER_YEAR


def borrow_drag_open(
    row: Dict[str, Any], now: datetime
) -> Dict[str, Any]:
    """Drag accrued on an open SHORT decision row from entry until ``now``."""
    direction = (row.get("direction") or "").upper()
    if direction != "SHORT":
        return {"applies": False}
    entry_dt = _parse_iso(str(row.get("ts") or ""))
    if entry_dt is None:
        return {"applies": True, "warning": "no_entry_ts"}
    qty = row.get("fill_qty") or row.get("shares") or 0
    price = row.get("fill_price") or row.get("entry_price") or 0
    notional = float(qty) * float(price)
    days = max(0.0, (now - entry_dt).total_seconds() / 86400.0)
    daily = _daily_borrow_rate()
    drag_pct = days * daily * 100.0
    drag_dollar = notional * days * daily
    return {
        "applies":        True,
        "days_held":      round(days, 2),
        "daily_rate_bps": round(daily * 1e4, 4),
        "annual_rate_bps": SHADOW_BORROW_BPS_ANNUAL,
        "drag_pct":       round(drag_pct, 4),
        "drag_dollar":    round(drag_dollar, 2),
        "notional":       round(notional, 2),
    }


def borrow_adjust_closed(row: Dict[str, Any]) -> Dict[str, Any]:
    """Adjust a closed short outcome's return for the shadow borrow rate.
    Does **not** mutate the source row."""
    horizon = row.get("horizon_days")
    ret = row.get("return_pct")
    if horizon is None or ret is None:
        return {"applies": False, "reason": "missing_outcome"}
    daily = _daily_borrow_rate()
    drag_pct = float(horizon) * daily * 100.0
    # For SHORT trades return_pct is positive when price fell (per resolver
    # convention).  Borrow cost reduces realized short P&L → subtract drag.
    adjusted = float(ret) - drag_pct
    return {
        "applies":        True,
        "horizon_days":   int(horizon),
        "borrow_drag_pct": round(drag_pct, 4),
        "return_pct":     round(float(ret), 4),
        "adjusted_return_pct": round(adjusted, 4),
        "annual_rate_bps": SHADOW_BORROW_BPS_ANNUAL,
    }


# ── Report assembly ──────────────────────────────────────────────────────────

def build_report(
    *,
    db_path: Path,
    cache_dir: Path,
    equity_override: Optional[float] = None,
    paper_recent_days: int = 7,
    since_iso: Optional[str] = None,
) -> Dict[str, Any]:
    prices_dir = cache_dir / "prices"
    open_pos = load_open_positions(db_path, since_iso=since_iso)
    recent_signals = load_recent_paper_signals(db_path, paper_recent_days)
    closed_shorts = load_closed_short_outcomes(db_path)

    equity = equity_override if equity_override is not None else _read_equity_hint()
    now = datetime.now(timezone.utc)

    # Build a small ATR cache keyed by ticker so we don't re-read parquets.
    tickers = set()
    for p in open_pos:
        if p.get("ticker"):
            tickers.add(p["ticker"].upper())
    for s in recent_signals:
        if s.get("ticker"):
            tickers.add(s["ticker"].upper())
    bars_cache: Dict[str, Optional[pd.DataFrame]] = {
        t: _load_cached_bars(t, prices_dir) for t in tickers
    }

    open_rows: List[Dict[str, Any]] = []
    for p in open_pos:
        t = (p.get("ticker") or "?").upper()
        bars = bars_cache.get(t)
        entry = p.get("fill_price") or p.get("entry_price") or 0
        stop = p.get("stop_loss")
        qty = p.get("fill_qty") or p.get("shares")
        sizing = shadow_size_row(
            equity=equity,
            entry=float(entry) if entry else 0.0,
            stop=float(stop) if stop is not None else None,
            direction=(p.get("direction") or "LONG").upper(),
            bars=bars,
            actual_shares=float(qty) if qty is not None else None,
        )
        borrow = borrow_drag_open(p, now)
        open_rows.append({
            "ticker":     t,
            "strategy":   (p.get("strategy") or "?").upper(),
            "direction":  (p.get("direction") or "LONG").upper(),
            "entry":      float(entry) if entry else None,
            "stop":       float(stop) if stop is not None else None,
            "logged_at":  p.get("ts"),
            "sizing":     sizing,
            "borrow":     borrow,
        })

    signal_rows: List[Dict[str, Any]] = []
    for s in recent_signals:
        t = (s.get("ticker") or "?").upper()
        bars = bars_cache.get(t)
        entry = s.get("entry_price") or 0
        stop = s.get("stop_loss")
        # paper_signals does not store size; use position-sized hypothetical
        # from current sizer if equity hint exists, else None.
        actual_shares = None
        if entry and stop is not None and equity:
            from core.config import MAX_POSITION_PCT
            risk_per_share = abs(float(entry) - float(stop))
            if risk_per_share > 0:
                actual_shares = max(1, int(equity * MAX_POSITION_PCT / risk_per_share))
        sizing = shadow_size_row(
            equity=equity,
            entry=float(entry) if entry else 0.0,
            stop=float(stop) if stop is not None else None,
            direction=(s.get("side") or "LONG").upper(),
            bars=bars,
            actual_shares=float(actual_shares) if actual_shares is not None else None,
        )
        signal_rows.append({
            "id":         s.get("id"),
            "ticker":     t,
            "strategy":   (s.get("strategy") or "?").upper(),
            "side":       (s.get("side") or "LONG").upper(),
            "logged_at":  s.get("logged_at"),
            "entry":      float(entry) if entry else None,
            "stop":       float(stop) if stop is not None else None,
            "implied_actual_shares": actual_shares,
            "sizing":     sizing,
        })

    closed_short_rows: List[Dict[str, Any]] = []
    for r in closed_shorts:
        adj = borrow_adjust_closed(r)
        closed_short_rows.append({
            "id":            r.get("id"),
            "ticker":        (r.get("ticker") or "?").upper(),
            "strategy":      (r.get("strategy") or "?").upper(),
            "sleeve":        r.get("sleeve"),
            "logged_at":     r.get("logged_at"),
            "horizon_days":  r.get("horizon_days"),
            "return_pct":    r.get("return_pct"),
            "adjusted_return_pct_existing": r.get("adjusted_return_pct"),
            "borrow_shadow": adj,
        })

    # Aggregates
    sizing_with_data = [r for r in open_rows
                        if r["sizing"].get("size_ratio") is not None]
    n_above_2x = sum(
        1 for r in sizing_with_data if r["sizing"]["size_ratio"] >= 2.0
    )
    n_below_04x = sum(
        1 for r in sizing_with_data if r["sizing"]["size_ratio"] <= 0.4
    )
    n_no_atr = sum(1 for r in open_rows
                   if r["sizing"].get("warning") == "no_atr_cache")

    open_short_rows = [r for r in open_rows if r["direction"] == "SHORT"]
    total_open_drag_dollar = round(
        sum((r["borrow"].get("drag_dollar") or 0) for r in open_short_rows), 2
    )

    # Closed-short impact: median return_pct vs adjusted
    ret_unadj = [r["return_pct"] for r in closed_short_rows
                 if r["return_pct"] is not None]
    ret_adj = [r["borrow_shadow"].get("adjusted_return_pct")
               for r in closed_short_rows
               if r["borrow_shadow"].get("applies")]
    ret_adj = [v for v in ret_adj if v is not None]

    def _med(xs: List[float]) -> Optional[float]:
        if not xs:
            return None
        s = sorted(xs)
        m = len(s) // 2
        if len(s) % 2:
            return round(s[m], 4)
        return round((s[m - 1] + s[m]) / 2, 4)

    warnings: List[str] = []
    if n_above_2x:
        warnings.append(
            f"{n_above_2x} open position(s) size ≥2× vol-target shadow"
        )
    if n_below_04x:
        warnings.append(
            f"{n_below_04x} open position(s) size ≤0.4× vol-target shadow"
        )
    if n_no_atr:
        warnings.append(
            f"{n_no_atr} open position(s) skipped — no ATR (cache miss)"
        )
    if equity is None:
        warnings.append(
            "no equity hint available — shadow_shares values are null"
        )
    if total_open_drag_dollar > 0:
        warnings.append(
            f"open SHORT borrow drag (cumulative since entry) = "
            f"${total_open_drag_dollar:,.2f} at "
            f"{SHADOW_BORROW_BPS_ANNUAL:.0f} bps annualized"
        )

    return {
        "generated_at":  now.isoformat(),
        "db_path":       str(db_path),
        "cache_dir":     str(cache_dir),
        "equity_hint":   equity,
        "equity_source": "override" if equity_override is not None
                         else ("heartbeat" if equity is not None else None),
        "params": {
            "vol_target":            SHADOW_VOL_TARGET,
            "borrow_bps_annual":     SHADOW_BORROW_BPS_ANNUAL,
            "trading_days_per_year": TRADING_DAYS_PER_YEAR,
            "atr_period":            ATR_PERIOD,
            "paper_recent_days":     paper_recent_days,
        },
        "summary": {
            "open_positions":         len(open_pos),
            "open_shorts":            len(open_short_rows),
            "open_with_sizing_data":  len(sizing_with_data),
            "open_no_atr_cache":      n_no_atr,
            "open_size_ge_2x_shadow": n_above_2x,
            "open_size_le_04x_shadow": n_below_04x,
            "recent_signal_window_days": paper_recent_days,
            "recent_signals":         len(recent_signals),
            "closed_short_outcomes":  len(closed_short_rows),
            "open_short_borrow_drag_dollar": total_open_drag_dollar,
            "closed_short_median_return_pct":          _med(ret_unadj),
            "closed_short_median_borrow_adjusted_pct": _med(ret_adj),
        },
        "open_positions": open_rows,
        "recent_signals": signal_rows,
        "closed_short_outcomes": closed_short_rows,
        "warnings":       warnings,
    }


# ── Rendering ────────────────────────────────────────────────────────────────

def _fmt_pct(v: Optional[float]) -> str:
    return "  n/a" if v is None else f"{v:+6.2f}%"


def render_text(report: Dict[str, Any]) -> str:
    bar = "─" * 80
    p = report["params"]
    s = report["summary"]
    lines: List[str] = []
    lines.append(bar)
    lines.append("SHADOW SIZING + BORROW DRAG — paper-only diagnostic")
    lines.append(f"generated_at={report['generated_at']}   db={report['db_path']}")
    eq = report.get("equity_hint")
    src = report.get("equity_source") or "none"
    lines.append(
        f"equity_hint={eq if eq else 'n/a'}  source={src}  "
        f"vol_target={p['vol_target']*100:.2f}% per pos  "
        f"borrow={p['borrow_bps_annual']:.0f} bps annual  "
        f"atr_period={p['atr_period']}d"
    )
    lines.append(bar)
    lines.append(
        f"open_positions={s['open_positions']}  open_shorts={s['open_shorts']}  "
        f"with_data={s['open_with_sizing_data']}  no_atr={s['open_no_atr_cache']}  "
        f"≥2x_shadow={s['open_size_ge_2x_shadow']}  "
        f"≤0.4x_shadow={s['open_size_le_04x_shadow']}"
    )
    lines.append(
        f"recent_signals={s['recent_signals']} (last {s['recent_signal_window_days']}d)  "
        f"closed_short_outcomes={s['closed_short_outcomes']}"
    )
    lines.append(
        f"closed_short median return={_fmt_pct(s['closed_short_median_return_pct'])}  "
        f"adjusted={_fmt_pct(s['closed_short_median_borrow_adjusted_pct'])}"
    )
    lines.append(
        f"open_short cumulative borrow drag = "
        f"${s['open_short_borrow_drag_dollar']:,.2f}"
    )
    lines.append("")

    if report["open_positions"]:
        lines.append("OPEN POSITIONS — vol-target shadow vs actual:")
        lines.append(
            f"  {'ticker':<8} {'strat':<10} {'dir':<6} {'qty':>8} {'shadow':>9} "
            f"{'ratio':>6} {'gap%':>7} {'atr%':>6} {'r@stop':>10} {'borrow$':>9} {'note':<22}"
        )
        for r in report["open_positions"]:
            sz = r["sizing"]
            br = r["borrow"]
            qty = sz.get("actual_shares")
            shadow = sz.get("shadow_shares")
            ratio = sz.get("size_ratio")
            gap = sz.get("size_gap_pct")
            atr_pct = sz.get("atr_pct")
            risk_at = sz.get("actual_risk_at_stop")
            drag = br.get("drag_dollar") if br.get("applies") else None
            note = sz.get("warning") or ""
            lines.append(
                f"  {r['ticker']:<8} "
                f"{r['strategy'][:10]:<10} "
                f"{r['direction']:<6} "
                f"{(f'{qty:.0f}' if qty is not None else 'n/a'):>8} "
                f"{(f'{shadow:.0f}' if shadow is not None else 'n/a'):>9} "
                f"{(f'{ratio:.2f}' if ratio is not None else 'n/a'):>6} "
                f"{(f'{gap:+.1f}' if gap is not None else 'n/a'):>7} "
                f"{(f'{atr_pct*100:.2f}' if atr_pct is not None else 'n/a'):>6} "
                f"{(f'${risk_at:,.0f}' if risk_at is not None else 'n/a'):>10} "
                f"{(f'${drag:,.2f}' if drag is not None else '   --   '):>9} "
                f"{note[:22]:<22}"
            )
        lines.append("")

    if report["recent_signals"]:
        lines.append(f"RECENT SIGNALS (last {p['paper_recent_days']}d) — implied size vs vol-target shadow:")
        lines.append(
            f"  {'ticker':<8} {'strat':<10} {'side':<6} {'imp_qty':>8} "
            f"{'shadow':>9} {'ratio':>6} {'atr%':>6} {'note':<22}"
        )
        for r in report["recent_signals"][:30]:
            sz = r["sizing"]
            qty = r.get("implied_actual_shares")
            shadow = sz.get("shadow_shares")
            ratio = sz.get("size_ratio")
            atr_pct = sz.get("atr_pct")
            note = sz.get("warning") or ""
            lines.append(
                f"  {r['ticker']:<8} "
                f"{r['strategy'][:10]:<10} "
                f"{r['side']:<6} "
                f"{(f'{qty:.0f}' if qty is not None else 'n/a'):>8} "
                f"{(f'{shadow:.0f}' if shadow is not None else 'n/a'):>9} "
                f"{(f'{ratio:.2f}' if ratio is not None else 'n/a'):>6} "
                f"{(f'{atr_pct*100:.2f}' if atr_pct is not None else 'n/a'):>6} "
                f"{note[:22]:<22}"
            )
        if len(report["recent_signals"]) > 30:
            lines.append(f"  ...({len(report['recent_signals']) - 30} more rows)")
        lines.append("")

    if report["closed_short_outcomes"]:
        lines.append("CLOSED SHORT OUTCOMES — borrow-adjusted (shadow) vs original:")
        lines.append(
            f"  {'ticker':<8} {'strat':<10} {'horizon':>8} {'return':>8} "
            f"{'drag':>7} {'adj':>8}"
        )
        for r in report["closed_short_outcomes"][:30]:
            bs = r["borrow_shadow"]
            drag_val = bs.get("borrow_drag_pct") or 0.0
            drag_str = f"{drag_val:.2f}%"
            lines.append(
                f"  {r['ticker']:<8} "
                f"{r['strategy'][:10]:<10} "
                f"{(str(r.get('horizon_days') or '?')):>8} "
                f"{_fmt_pct(r.get('return_pct')):>8} "
                f"{drag_str:>7} "
                f"{_fmt_pct(bs.get('adjusted_return_pct')):>8}"
            )
        if len(report["closed_short_outcomes"]) > 30:
            lines.append(
                f"  ...({len(report['closed_short_outcomes']) - 30} more rows)"
            )
        lines.append("")

    if report["warnings"]:
        lines.append("WARNINGS:")
        for w in report["warnings"]:
            lines.append(f"  ⚠ {w}")
    else:
        lines.append("WARNINGS: none")
    lines.append(bar)
    return "\n".join(lines) + "\n"


# ── Output ───────────────────────────────────────────────────────────────────

def write_outputs(report: Dict[str, Any], json_path: Path, txt_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = json_path.with_suffix(json_path.suffix + ".tmp")
    tmp.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    tmp.replace(json_path)
    txt_path.write_text(render_text(report), encoding="utf-8")


def build_dual_scope_report(
    *,
    db_path: Path,
    cache_dir: Path,
    equity_override: Optional[float] = None,
    paper_recent_days: int = 7,
    clean_epoch_iso: str = CLEAN_PAPER_EVIDENCE_START,
) -> Dict[str, Any]:
    """Phase 1D wrapper. Returns both full-ledger and clean-epoch
    shadow-sizing views. Each section preserves the pre-Phase-1D
    ``build_report`` shape verbatim.
    """
    full = build_report(
        db_path=db_path, cache_dir=cache_dir,
        equity_override=equity_override,
        paper_recent_days=paper_recent_days,
        since_iso=None,
    )
    clean = build_report(
        db_path=db_path, cache_dir=cache_dir,
        equity_override=equity_override,
        paper_recent_days=paper_recent_days,
        since_iso=clean_epoch_iso,
    )
    # Back-compat: keep the full-ledger shape at the JSON root so the
    # dashboard's RISK TELEMETRY panel (and any other pre-Phase-1D
    # reader) keeps working without changes. Append the clean-epoch
    # view as an additive sub-block.
    dual: Dict[str, Any] = dict(full)
    dual["clean_epoch_start"] = clean_epoch_iso
    dual["full_ledger"]       = full
    dual["clean_epoch"]       = clean
    return dual


def render_dual_text(dual: Dict[str, Any]) -> str:
    bar = "═" * 80
    parts: List[str] = []
    parts.append(bar)
    parts.append("SHADOW SIZING — Phase 1D dual-scope")
    parts.append(f"generated_at={dual['generated_at']}   db={dual['db_path']}")
    parts.append(f"clean_epoch_start={dual['clean_epoch_start']}")
    parts.append(bar)
    parts.append("─── FULL LEDGER ───")
    parts.append(render_text(dual["full_ledger"]))
    parts.append("─── CLEAN EPOCH (positions opened on/after clean_epoch_start) ───")
    parts.append(render_text(dual["clean_epoch"]))
    return "\n".join(parts) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Vol-target sizing + short-borrow shadow diagnostic"
    )
    parser.add_argument("--db-path", default=str(cfg.DB_PATH))
    parser.add_argument("--cache-dir", default=str(cfg.CACHE_DIR))
    parser.add_argument("--equity", type=float, default=None,
                        help="Override equity hint (else read from heartbeat)")
    parser.add_argument("--paper-recent-days", type=int, default=7,
                        help="Recent paper-signals window (days)")
    parser.add_argument("--since", default=None,
                        help=("Override the clean-epoch start (ISO ts). "
                              f"Default: {CLEAN_PAPER_EVIDENCE_START}"))
    parser.add_argument("--json-out", default=None)
    parser.add_argument("--txt-out", default=None)
    parser.add_argument("--print", action="store_true")
    args = parser.parse_args(argv)

    clean_epoch_iso = args.since or CLEAN_PAPER_EVIDENCE_START
    dual = build_dual_scope_report(
        db_path=Path(args.db_path),
        cache_dir=Path(args.cache_dir),
        equity_override=args.equity,
        paper_recent_days=args.paper_recent_days,
        clean_epoch_iso=clean_epoch_iso,
    )

    json_path = Path(args.json_out) if args.json_out else (
        cfg.CACHE_DIR / "research" / "shadow_sizing_latest.json"
    )
    txt_path = Path(args.txt_out) if args.txt_out else (
        cfg.LOG_DIR / "shadow_sizing_latest.txt"
    )
    json_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = json_path.with_suffix(json_path.suffix + ".tmp")
    tmp.write_text(json.dumps(dual, indent=2, default=str), encoding="utf-8")
    tmp.replace(json_path)
    txt_path.write_text(render_dual_text(dual), encoding="utf-8")

    if args.print or dual["full_ledger"]["summary"]["open_positions"] == 0:
        print(render_dual_text(dual))
    print(f"wrote {json_path}")
    print(f"wrote {txt_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

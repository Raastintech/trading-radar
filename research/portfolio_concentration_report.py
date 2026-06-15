"""
research/portfolio_concentration_report.py — Paper-only portfolio concentration
and correlation diagnostics.

Reads only.  Does **not** mutate decisions, paper signals, or orders, and
does **not** call providers.  All sector + price data comes from the local
SQLite/Parquet cache.

Inputs:
  * decisions table (db/trading.db)        — open paper positions
  * cache_meta table (Gatekeeper SQLite)   — fmp:profile:{TICKER} for sector
  * paper_signals.sector                   — denormalized sector at signal time
  * cache/prices/{TICKER}.parquet          — daily bars (close column)

Outputs:
  cache/research/portfolio_concentration_latest.json
  logs/portfolio_concentration_latest.txt

Usage:
  cd /home/gem/trading-production
  GEM_TRADER_SKIP_DOTENV=true .venv/bin/python \\
      research/portfolio_concentration_report.py
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
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
logger = logging.getLogger("portfolio_concentration")

CORRELATION_WINDOW_DAYS = 60
HIGH_CORRELATION_THRESHOLD = 0.70   # cluster cutoff

# Phase 1G.1 calibration: with only a handful of open positions, the
# top-1 / top-3 / top-N gross concentration shares are mechanically
# extreme (100% / 100% / 100% for a single-position book). That is not
# a concentration risk, it is a sample-size artifact. Below this floor
# we surface the picture as INFO rather than as a portfolio warning;
# at or above it the original thresholds (>25% top1, >60% top3, >40%
# sector) drive actionable warnings.
CONCENTRATION_MIN_POSITIONS_FOR_WARNING = 3


# ── Position loading ─────────────────────────────────────────────────────────

def load_open_positions(
    db_path: Path,
    since_iso: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return open paper positions from the decisions table.

    Open = position_opened=1 AND position_closed=0.
    Mirrors core.decision_logger.get_open_decisions().

    Phase 1D: when ``since_iso`` is provided, also filters ``ts >= since_iso``
    so callers can scope to the clean-paper-evidence epoch and exclude
    legacy pre-Phase-0 rows from concentration math.
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


def load_paper_signal_sectors(db_path: Path) -> Dict[str, str]:
    """Return ticker -> latest non-empty sector from paper_signals."""
    if not db_path.exists():
        return {}
    out: Dict[str, str] = {}
    try:
        conn = sqlite3.connect(str(db_path))
        # Use most recent row per ticker that has a non-empty sector.
        rows = conn.execute(
            "SELECT ticker, sector FROM paper_signals "
            "WHERE sector IS NOT NULL AND sector != '' "
            "ORDER BY logged_at DESC"
        ).fetchall()
        conn.close()
        for tkr, sec in rows:
            t = (tkr or "").upper()
            if t and t not in out:
                out[t] = sec
    except sqlite3.OperationalError:
        pass
    return out


def load_gatekeeper_profiles(db_path: Path, tickers: List[str]) -> Dict[str, Dict[str, str]]:
    """Read fmp:profile:{TICKER} payloads from the Gatekeeper cache_meta
    table.  Stale rows are accepted — sector classification is effectively
    static and we are diagnostic-only.

    Returns ticker -> {sector, industry} dict for hits.
    """
    out: Dict[str, Dict[str, str]] = {}
    if not db_path.exists() or not tickers:
        return out
    try:
        conn = sqlite3.connect(str(db_path))
        # Confirm cache_meta exists
        chk = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='cache_meta'"
        ).fetchone()
        if not chk:
            conn.close()
            return out
        for t in tickers:
            key = f"fmp:profile:{t.upper()}"
            row = conn.execute(
                "SELECT payload FROM cache_meta WHERE key=?", (key,)
            ).fetchone()
            if not row or not row[0]:
                continue
            try:
                data = json.loads(row[0])
                out[t.upper()] = {
                    "sector":   data.get("sector") or "",
                    "industry": data.get("industry") or "",
                }
            except json.JSONDecodeError:
                continue
        conn.close()
    except sqlite3.OperationalError:
        pass
    return out


def resolve_sectors(
    tickers: List[str],
    paper_sectors: Dict[str, str],
    profile_sectors: Dict[str, Dict[str, str]],
) -> Dict[str, str]:
    """Layer paper_signals first, then FMP profile cache, else UNKNOWN."""
    out: Dict[str, str] = {}
    for t in tickers:
        u = t.upper()
        sec = paper_sectors.get(u) or (profile_sectors.get(u) or {}).get("sector") or ""
        out[u] = (sec or "UNKNOWN").strip() or "UNKNOWN"
    return out


# ── Exposure math ────────────────────────────────────────────────────────────

def _position_qty(row: Dict[str, Any]) -> float:
    """Prefer fill_qty (broker truth) over shares (intent)."""
    fq = row.get("fill_qty")
    if fq is not None and fq > 0:
        return float(fq)
    return float(row.get("shares") or 0)


def _position_price(row: Dict[str, Any]) -> float:
    """Prefer fill_price (broker truth) over entry_price (intent)."""
    fp = row.get("fill_price")
    if fp is not None and fp > 0:
        return float(fp)
    return float(row.get("entry_price") or 0)


def compute_exposure(
    positions: List[Dict[str, Any]],
    sectors: Dict[str, str],
    equity: Optional[float],
) -> Dict[str, Any]:
    """Compute gross/net/long/short and sector/ticker concentration."""
    long_val = 0.0
    short_val = 0.0
    by_ticker: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"long": 0.0, "short": 0.0, "qty": 0.0, "rows": 0,
                 "sector": "UNKNOWN", "strategies": set(), "directions": set()}
    )
    by_sector: Dict[str, Dict[str, float]] = defaultdict(
        lambda: {"long": 0.0, "short": 0.0, "abs": 0.0}
    )
    by_strategy: Dict[str, Dict[str, float]] = defaultdict(
        lambda: {"long": 0.0, "short": 0.0, "n": 0}
    )

    for p in positions:
        t = (p.get("ticker") or "?").upper()
        d = (p.get("direction") or "LONG").upper()
        strat = (p.get("strategy") or "?").upper()
        qty = _position_qty(p)
        price = _position_price(p)
        notional = qty * price
        if notional <= 0:
            continue
        sec = sectors.get(t, "UNKNOWN")
        side_key = "short" if d == "SHORT" else "long"
        if d == "SHORT":
            short_val += notional
        else:
            long_val += notional
        by_ticker[t][side_key] += notional
        by_ticker[t]["qty"] += qty
        by_ticker[t]["rows"] += 1
        by_ticker[t]["sector"] = sec
        by_ticker[t]["strategies"].add(strat)
        by_ticker[t]["directions"].add(d)
        by_sector[sec][side_key] += notional
        by_sector[sec]["abs"] += notional
        by_strategy[strat][side_key] += notional
        by_strategy[strat]["n"] += 1

    gross = long_val + short_val
    net = long_val - short_val

    def _pct(x: float) -> Optional[float]:
        if equity is None or equity <= 0:
            return None
        return round(x / equity * 100.0, 2)

    ticker_rows: List[Dict[str, Any]] = []
    for t, b in by_ticker.items():
        notional = b["long"] + b["short"]
        ticker_rows.append({
            "ticker":       t,
            "sector":       b["sector"],
            "strategies":   sorted(b["strategies"]),
            "directions":   sorted(b["directions"]),
            "qty":          round(b["qty"], 4),
            "long_notional": round(b["long"], 2),
            "short_notional": round(b["short"], 2),
            "notional":     round(notional, 2),
            "gross_pct":    round(notional / gross * 100, 2) if gross else None,
            "equity_pct":   _pct(notional),
        })
    ticker_rows.sort(key=lambda r: r["notional"], reverse=True)

    sector_rows: List[Dict[str, Any]] = []
    for sec, b in by_sector.items():
        sector_rows.append({
            "sector":         sec,
            "long_notional":  round(b["long"], 2),
            "short_notional": round(b["short"], 2),
            "abs_notional":   round(b["abs"], 2),
            "gross_pct":      round(b["abs"] / gross * 100, 2) if gross else None,
            "equity_pct":     _pct(b["abs"]),
        })
    sector_rows.sort(key=lambda r: r["abs_notional"], reverse=True)

    strategy_rows: List[Dict[str, Any]] = []
    for strat, b in by_strategy.items():
        notional = b["long"] + b["short"]
        strategy_rows.append({
            "strategy":      strat,
            "n_positions":   b["n"],
            "long_notional": round(b["long"], 2),
            "short_notional": round(b["short"], 2),
            "abs_notional":  round(notional, 2),
            "gross_pct":     round(notional / gross * 100, 2) if gross else None,
            "equity_pct":    _pct(notional),
        })
    strategy_rows.sort(key=lambda r: r["abs_notional"], reverse=True)

    top1_pct = ticker_rows[0]["gross_pct"] if ticker_rows else None
    top3_pct = round(sum((r["gross_pct"] or 0) for r in ticker_rows[:3]), 2) if ticker_rows else None
    top5_pct = round(sum((r["gross_pct"] or 0) for r in ticker_rows[:5]), 2) if ticker_rows else None

    return {
        "n_positions":       len(positions),
        "n_unique_tickers":  len(by_ticker),
        "long_notional":     round(long_val, 2),
        "short_notional":    round(short_val, 2),
        "gross_notional":    round(gross, 2),
        "net_notional":      round(net, 2),
        "long_pct_equity":   _pct(long_val),
        "short_pct_equity":  _pct(short_val),
        "gross_pct_equity":  _pct(gross),
        "net_pct_equity":    _pct(net),
        "top1_gross_pct":    top1_pct,
        "top3_gross_pct":    top3_pct,
        "top5_gross_pct":    top5_pct,
        "by_ticker":         ticker_rows,
        "by_sector":         sector_rows,
        "by_strategy":       strategy_rows,
    }


# ── Correlation math (cache-only, parquet-backed) ────────────────────────────

def _load_returns(ticker: str, prices_dir: Path, window: int) -> Optional[pd.Series]:
    """Read last `window` daily closes from parquet and return log returns.
    Returns None when the cache file is missing or unreadable."""
    path = prices_dir / f"{ticker.upper()}.parquet"
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path)
    except Exception:
        return None
    if "close" not in df.columns or len(df) < 5:
        return None
    closes = df["close"].dropna().tail(window + 1)
    if len(closes) < 5:
        return None
    returns = closes.pct_change().dropna()
    returns.name = ticker.upper()
    return returns


def compute_correlations(
    tickers: List[str],
    prices_dir: Path,
    window: int = CORRELATION_WINDOW_DAYS,
) -> Tuple[Dict[str, Dict[str, float]], Dict[str, float], List[str], List[str]]:
    """Pairwise Pearson correlations of daily returns from cached parquets.

    Returns:
      pairs:   {tA: {tB: corr}} for tA != tB (symmetric, excludes self-corr)
      max_to_book: {ticker: max corr to any other ticker in the book}
      covered: tickers we had cached data for
      skipped: tickers we lacked data for
    """
    series: Dict[str, pd.Series] = {}
    skipped: List[str] = []
    for t in tickers:
        s = _load_returns(t, prices_dir, window)
        if s is None or len(s) < 10:
            skipped.append(t)
            continue
        series[t] = s

    pairs: Dict[str, Dict[str, float]] = {}
    max_to_book: Dict[str, float] = {}
    covered = sorted(series.keys())
    if len(covered) < 2:
        return pairs, max_to_book, covered, skipped

    df = pd.concat(series, axis=1, join="inner")
    if len(df) < 10:
        return pairs, max_to_book, covered, skipped

    corr = df.corr()
    for a in covered:
        pairs[a] = {}
        best = -1.1
        for b in covered:
            if a == b:
                continue
            c = corr.loc[a, b]
            if pd.isna(c):
                continue
            pairs[a][b] = round(float(c), 3)
            if c > best:
                best = float(c)
        max_to_book[a] = round(best, 3) if best > -1.1 else None  # type: ignore[assignment]
    return pairs, max_to_book, covered, skipped


def find_clusters(
    pairs: Dict[str, Dict[str, float]],
    threshold: float = HIGH_CORRELATION_THRESHOLD,
) -> List[List[str]]:
    """Connected components of the high-correlation graph (corr ≥ threshold).
    Returns clusters with 2+ members."""
    nodes = list(pairs.keys())
    parent = {n: n for n in nodes}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for a, neighbors in pairs.items():
        for b, c in neighbors.items():
            if c >= threshold:
                union(a, b)

    groups: Dict[str, List[str]] = defaultdict(list)
    for n in nodes:
        groups[find(n)].append(n)
    return sorted(
        [sorted(g) for g in groups.values() if len(g) >= 2],
        key=lambda g: (-len(g), g[0]),
    )


# ── Equity ───────────────────────────────────────────────────────────────────

def read_equity_hint() -> Optional[float]:
    """Best-effort equity hint from cached heartbeat / status JSON.  Returns
    None if no hint is available — concentration math degrades gracefully
    (notionals still computed; %-of-equity cells become null)."""
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


# ── Report assembly ──────────────────────────────────────────────────────────

def build_report(
    db_path: Path,
    cache_dir: Path,
    equity_override: Optional[float] = None,
    skip_correlation: bool = False,
    since_iso: Optional[str] = None,
) -> Dict[str, Any]:
    positions = load_open_positions(db_path, since_iso=since_iso)
    tickers = sorted({(p.get("ticker") or "?").upper() for p in positions if p.get("ticker")})
    paper_sectors = load_paper_signal_sectors(db_path)
    profile_sectors = load_gatekeeper_profiles(db_path, tickers)
    sectors = resolve_sectors(tickers, paper_sectors, profile_sectors)
    sector_source_counts = {
        "paper_signals":  sum(1 for t in tickers if t in paper_sectors),
        "fmp_profile":    sum(1 for t in tickers if t not in paper_sectors and t in profile_sectors),
        "unknown":        sum(1 for t in tickers if t not in paper_sectors and t not in profile_sectors),
    }

    equity = equity_override if equity_override is not None else read_equity_hint()
    exposure = compute_exposure(positions, sectors, equity)

    correlation: Dict[str, Any] = {
        "enabled":  not skip_correlation,
        "window":   CORRELATION_WINDOW_DAYS,
        "threshold": HIGH_CORRELATION_THRESHOLD,
    }
    if skip_correlation or len(tickers) < 2:
        correlation.update({
            "covered":     [],
            "skipped":     tickers,
            "max_to_book": {},
            "clusters":    [],
            "pairs":       {},
        })
    else:
        prices_dir = cache_dir / "prices"
        pairs, max_to_book, covered, skipped = compute_correlations(
            tickers, prices_dir, CORRELATION_WINDOW_DAYS
        )
        clusters = find_clusters(pairs, HIGH_CORRELATION_THRESHOLD)
        correlation.update({
            "covered":     covered,
            "skipped":     skipped,
            "max_to_book": max_to_book,
            "clusters":    clusters,
            "pairs":       pairs,
        })

    # Phase 1G.1 calibration: separate diagnostic INFO (small-book
    # mechanical concentrations) from actionable WARNINGS. The
    # numerical thresholds are unchanged; we only add a sample-size
    # gate so a single-position paper book does not generate a
    # "100% of gross" panic that obviously cannot be acted on.
    warnings: List[str] = []
    info: List[str] = []
    n_pos = int(exposure["n_positions"] or 0)
    enough_for_warning = n_pos >= CONCENTRATION_MIN_POSITIONS_FOR_WARNING
    if n_pos > 0:
        top1 = exposure.get("top1_gross_pct") or 0
        top3 = exposure.get("top3_gross_pct") or 0
        if top1 > 25:
            (warnings if enough_for_warning else info).append(
                f"top single name is {top1:.1f}% of gross "
                f"(n_positions={n_pos})"
            )
        if top3 > 60:
            (warnings if enough_for_warning else info).append(
                f"top 3 names are {top3:.1f}% of gross "
                f"(n_positions={n_pos})"
            )
        for sec_row in exposure["by_sector"]:
            gp = sec_row.get("gross_pct") or 0
            if gp > 40 and sec_row["sector"] != "UNKNOWN":
                (warnings if enough_for_warning else info).append(
                    f"sector {sec_row['sector']} is {gp:.1f}% of gross "
                    f"(n_positions={n_pos})"
                )
        if not enough_for_warning:
            info.append(
                f"SINGLE_POSITION_BOOK: n_positions={n_pos} < "
                f"{CONCENTRATION_MIN_POSITIONS_FOR_WARNING}; "
                f"concentration shares are mechanical, not actionable"
            )
        ep = exposure.get("gross_pct_equity")
        if ep is not None and ep > 200:
            # Leverage is a real risk at any n; keep it as a warning.
            warnings.append(f"gross exposure is {ep:.0f}% of equity hint")
        if correlation.get("clusters"):
            # A correlation cluster requires >=2 covered tickers, so
            # by construction this only fires when n_pos >= 2.
            for cluster in correlation["clusters"]:
                warnings.append(
                    f"correlation cluster (≥{HIGH_CORRELATION_THRESHOLD:.2f}): "
                    + ", ".join(cluster)
                )
    if sector_source_counts["unknown"] > 0:
        # Data-quality note, not a portfolio risk.
        info.append(
            f"{sector_source_counts['unknown']} ticker(s) lack sector classification"
        )

    return {
        "generated_at":   datetime.now(timezone.utc).isoformat(),
        "db_path":        str(db_path),
        "cache_dir":      str(cache_dir),
        "equity_hint":    equity,
        "equity_source":  "override" if equity_override is not None
                          else ("heartbeat" if equity is not None else None),
        "sector_source_counts": sector_source_counts,
        "summary": {
            "n_positions":      exposure["n_positions"],
            "n_unique_tickers": exposure["n_unique_tickers"],
            "gross_notional":   exposure["gross_notional"],
            "net_notional":     exposure["net_notional"],
            "long_notional":    exposure["long_notional"],
            "short_notional":   exposure["short_notional"],
            "gross_pct_equity": exposure["gross_pct_equity"],
            "net_pct_equity":   exposure["net_pct_equity"],
            "top1_gross_pct":   exposure["top1_gross_pct"],
            "top3_gross_pct":   exposure["top3_gross_pct"],
            "top5_gross_pct":   exposure["top5_gross_pct"],
        },
        "by_ticker":      exposure["by_ticker"],
        "by_sector":      exposure["by_sector"],
        "by_strategy":    exposure["by_strategy"],
        "correlation":    correlation,
        "warnings":       warnings,
        # Phase 1G.1: small-book / data-quality notes that previously
        # masqueraded as warnings. Same JSON root for back-compat;
        # consumers can choose to render INFO entries differently.
        "info":           info,
        "sample_size_thresholds": {
            "min_positions_for_warning": CONCENTRATION_MIN_POSITIONS_FOR_WARNING,
        },
    }


# ── Rendering ────────────────────────────────────────────────────────────────

def _fmt_pct(v: Optional[float]) -> str:
    return "  n/a" if v is None else f"{v:5.1f}%"


def _fmt_money(v: float) -> str:
    if abs(v) >= 1_000_000:
        return f"${v/1_000_000:.2f}M"
    if abs(v) >= 1_000:
        return f"${v/1_000:.1f}k"
    return f"${v:.0f}"


def render_text(report: Dict[str, Any]) -> str:
    bar = "─" * 80
    lines: List[str] = []
    s = report["summary"]
    lines.append(bar)
    lines.append("PORTFOLIO CONCENTRATION & CORRELATION — paper-only")
    lines.append(f"generated_at={report['generated_at']}   db={report['db_path']}")
    eq = report.get("equity_hint")
    eq_src = report.get("equity_source")
    lines.append(
        f"equity_hint={_fmt_money(eq) if eq else 'n/a'}  source={eq_src or 'none'}"
    )
    lines.append(bar)
    lines.append(
        f"positions={s['n_positions']}  tickers={s['n_unique_tickers']}  "
        f"gross={_fmt_money(s['gross_notional'])}  net={_fmt_money(s['net_notional'])}  "
        f"long={_fmt_money(s['long_notional'])}  short={_fmt_money(s['short_notional'])}"
    )
    lines.append(
        f"%equity:  gross={_fmt_pct(s['gross_pct_equity'])}  "
        f"net={_fmt_pct(s['net_pct_equity'])}  "
        f"top1={_fmt_pct(s['top1_gross_pct'])}  "
        f"top3={_fmt_pct(s['top3_gross_pct'])}  "
        f"top5={_fmt_pct(s['top5_gross_pct'])}"
    )
    lines.append("")

    if report["by_sector"]:
        lines.append("BY SECTOR (gross %):")
        lines.append(f"  {'sector':<28} {'gross$':>12} {'gross%':>8} {'eq%':>8}")
        for r in report["by_sector"]:
            lines.append(
                f"  {r['sector'][:28]:<28} "
                f"{_fmt_money(r['abs_notional']):>12} "
                f"{_fmt_pct(r['gross_pct'])} "
                f"{_fmt_pct(r['equity_pct'])}"
            )
        lines.append("")

    if report["by_strategy"]:
        lines.append("BY STRATEGY:")
        lines.append(f"  {'strategy':<12} {'n':>4} {'gross$':>12} {'gross%':>8} {'eq%':>8}")
        for r in report["by_strategy"]:
            lines.append(
                f"  {r['strategy']:<12} {r['n_positions']:>4} "
                f"{_fmt_money(r['abs_notional']):>12} "
                f"{_fmt_pct(r['gross_pct'])} "
                f"{_fmt_pct(r['equity_pct'])}"
            )
        lines.append("")

    if report["by_ticker"]:
        lines.append("BY TICKER (top 25 by notional):")
        lines.append(f"  {'ticker':<8} {'sector':<22} {'dir':<6} "
                     f"{'gross$':>12} {'gross%':>8} {'eq%':>8} {'maxC':>6}")
        max_to_book = (report.get("correlation") or {}).get("max_to_book") or {}
        for r in report["by_ticker"][:25]:
            sec = (r.get("sector") or "?")[:22]
            d = ",".join(r.get("directions") or []) or "?"
            mc = max_to_book.get(r["ticker"])
            mc_s = "  n/a" if mc is None else f"{mc:+.2f}"
            lines.append(
                f"  {r['ticker']:<8} {sec:<22} {d:<6} "
                f"{_fmt_money(r['notional']):>12} "
                f"{_fmt_pct(r['gross_pct'])} "
                f"{_fmt_pct(r['equity_pct'])} "
                f"{mc_s:>6}"
            )
        lines.append("")

    corr = report["correlation"]
    if corr.get("enabled"):
        lines.append(
            f"CORRELATIONS (last {corr['window']}d, threshold ≥ {corr['threshold']:.2f}):"
        )
        if corr["clusters"]:
            for cl in corr["clusters"]:
                lines.append(f"  cluster: {', '.join(cl)}")
        else:
            lines.append("  no high-correlation clusters")
        if corr["skipped"]:
            lines.append(f"  skipped (no cached bars): {', '.join(corr['skipped'])}")
        lines.append("")

    if report["warnings"]:
        lines.append("WARNINGS:")
        for w in report["warnings"]:
            lines.append(f"  ⚠ {w}")
    else:
        lines.append("WARNINGS: none")
    info_rows = report.get("info") or []
    if info_rows:
        lines.append("INFO:")
        for i in info_rows:
            lines.append(f"  · {i}")
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
    db_path: Path,
    cache_dir: Path,
    equity_override: Optional[float] = None,
    skip_correlation: bool = False,
    clean_epoch_iso: str = CLEAN_PAPER_EVIDENCE_START,
) -> Dict[str, Any]:
    """Phase 1D wrapper. Returns a single dict with both full-ledger and
    clean-epoch concentration views.  Each section preserves the
    pre-Phase-1D ``build_report`` shape verbatim.
    """
    full = build_report(
        db_path, cache_dir,
        equity_override=equity_override,
        skip_correlation=skip_correlation,
        since_iso=None,
    )
    clean = build_report(
        db_path, cache_dir,
        equity_override=equity_override,
        skip_correlation=skip_correlation,
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
    parts.append("PORTFOLIO CONCENTRATION — Phase 1D dual-scope")
    parts.append(f"generated_at={dual['generated_at']}   db={dual['db_path']}")
    parts.append(f"clean_epoch_start={dual['clean_epoch_start']}")
    parts.append(bar)
    parts.append("─── FULL LEDGER ───")
    parts.append(render_text(dual["full_ledger"]))
    parts.append("─── CLEAN EPOCH (positions opened on/after clean_epoch_start) ───")
    parts.append(render_text(dual["clean_epoch"]))
    return "\n".join(parts) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Portfolio concentration / correlation diagnostic")
    parser.add_argument("--db-path", default=str(cfg.DB_PATH))
    parser.add_argument("--cache-dir", default=str(cfg.CACHE_DIR))
    parser.add_argument("--equity", type=float, default=None,
                        help="Override equity hint (else read from heartbeat)")
    parser.add_argument("--no-correlation", action="store_true",
                        help="Skip correlation matrix computation")
    parser.add_argument("--since", default=None,
                        help=("Override the clean-epoch start (ISO ts). "
                              f"Default: {CLEAN_PAPER_EVIDENCE_START}"))
    parser.add_argument("--json-out", default=None)
    parser.add_argument("--txt-out", default=None)
    parser.add_argument("--print", action="store_true")
    args = parser.parse_args(argv)

    clean_epoch_iso = args.since or CLEAN_PAPER_EVIDENCE_START
    dual = build_dual_scope_report(
        Path(args.db_path),
        Path(args.cache_dir),
        equity_override=args.equity,
        skip_correlation=args.no_correlation,
        clean_epoch_iso=clean_epoch_iso,
    )

    json_path = Path(args.json_out) if args.json_out else (
        cfg.CACHE_DIR / "research" / "portfolio_concentration_latest.json"
    )
    txt_path = Path(args.txt_out) if args.txt_out else (
        cfg.LOG_DIR / "portfolio_concentration_latest.txt"
    )
    json_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = json_path.with_suffix(json_path.suffix + ".tmp")
    tmp.write_text(json.dumps(dual, indent=2, default=str), encoding="utf-8")
    tmp.replace(json_path)
    txt_path.write_text(render_dual_text(dual), encoding="utf-8")

    if args.print or dual["full_ledger"]["summary"]["n_positions"] == 0:
        print(render_dual_text(dual))
    print(f"wrote {json_path}")
    print(f"wrote {txt_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

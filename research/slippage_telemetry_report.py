"""
research/slippage_telemetry_report.py — Paper-only slippage telemetry.

Reads the persisted fill telemetry from the ``decisions`` table and reports
the gap between intended entry (signal price) and broker fill price.

This is a Phase 1B paper-only diagnostic report.  It does **not** mutate
data, does not promote strategies, does not change governance, and does not
talk to providers.

Decisions schema columns used:
  ts, ticker, strategy, direction, entry_price, fill_price, fill_qty,
  shares, slippage_bps, fill_status, position_opened.

Definitions used here:
  * ``slippage_bps`` is the signed gap (fill_price - entry_price) / entry_price * 1e4
    persisted by execution/order_manager.py.  Positive means fill_price > entry.
  * ``adverse_bps`` rebases the sign per direction so positive always means
    "cost to us":  LONG ->  slippage_bps;  SHORT -> -slippage_bps.
  * A row has fill data iff ``slippage_bps IS NOT NULL`` (filled or partial).
  * No-fill = rows where the broker reported zero fill_qty (position_opened=0
    and fill_qty IS NULL or 0).  Partial fill = position_opened=1 and
    fill_qty < shares.

Backtest friction assumption:
  research/paper_trades/resolve_tactical_outcomes.py defines
  ROUND_TRIP_FRICTION_PCT = 0.30 (0.05% commission + 0.10% slippage each way).
  The slippage-only one-way component is 10 bps.  We warn when realized
  median |adverse_bps| exceeds the imported constant's one-way slippage
  assumption, or when p90 exceeds the full one-way friction allowance.

Usage:
  cd /home/gem/trading-production
  GEM_TRADER_SKIP_DOTENV=true .venv/bin/python \\
      research/slippage_telemetry_report.py
  SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python \\
      research/slippage_telemetry_report.py --since 2026-04-01

Outputs:
  cache/research/slippage_telemetry_latest.json
  logs/slippage_telemetry_latest.txt
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from zoneinfo import ZoneInfo

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
logger = logging.getLogger("slippage_telemetry")

_ET = ZoneInfo("America/New_York")

# Backtest friction benchmarks (bps, one-way).  Sourced from
# research/paper_trades/resolve_tactical_outcomes.py ROUND_TRIP_FRICTION_PCT.
BACKTEST_ONEWAY_SLIPPAGE_BPS = 10.0   # the 0.10% each-way "slippage" component
BACKTEST_ONEWAY_FRICTION_BPS = 15.0   # full one-way (0.05% comm + 0.10% slip)

# Phase 1G.1: sample-size threshold below which a budget breach is
# *informational* rather than an actionable warning. With n=2 fills,
# a median above the 10 bps backtest assumption tells us essentially
# nothing about live friction — one outlier flips the median. The
# warning text remains in the JSON sidecar so the audit MCP can still
# see the picture, but operators are not told to investigate.
SLIPPAGE_MIN_SAMPLE_FOR_WARNING = 10


# ── Data loading ─────────────────────────────────────────────────────────────

_DECISION_COLS = (
    "id", "run_id", "ts", "ticker", "strategy", "direction",
    "shares", "entry_price", "stop_loss", "target_price",
    "fill_price", "fill_qty", "slippage_bps", "fill_status",
    "position_opened", "position_closed",
)


def load_decisions(db_path: Path, since: Optional[str]) -> List[Dict[str, Any]]:
    """Return decisions rows as dicts.  Empty list if the table does not
    exist yet (fresh DB)."""
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(str(db_path))
        cols = [d[1] for d in conn.execute("PRAGMA table_info(decisions)").fetchall()]
        if not cols:
            conn.close()
            return []
        select_cols = [c for c in _DECISION_COLS if c in cols]
        sql = f"SELECT {', '.join(select_cols)} FROM decisions"
        params: Tuple = ()
        if since:
            sql += " WHERE ts >= ?"
            params = (since,)
        sql += " ORDER BY ts ASC"
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return [dict(zip(select_cols, r)) for r in rows]
    except sqlite3.OperationalError:
        return []


# ── Stats helpers ────────────────────────────────────────────────────────────

def _adverse_bps(row: Dict[str, Any]) -> Optional[float]:
    """Sign-correct slippage in bps.  Positive = cost-to-us."""
    s = row.get("slippage_bps")
    if s is None:
        return None
    direction = (row.get("direction") or "").upper()
    if direction == "SHORT":
        return -float(s)
    return float(s)


def _percentile(values: List[float], pct: float) -> Optional[float]:
    """Linear-interpolated percentile; pct in [0, 100].  None if empty."""
    if not values:
        return None
    if len(values) == 1:
        return float(values[0])
    s = sorted(values)
    k = (len(s) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return float(s[f])
    return float(s[f] + (s[c] - s[f]) * (k - f))


def _stats_block(adv_bps: List[float]) -> Dict[str, Optional[float]]:
    """Return median / p90 / worst / mean / n for a sequence of adverse bps."""
    if not adv_bps:
        return {"n": 0, "median_bps": None, "p90_bps": None,
                "worst_bps": None, "mean_bps": None, "abs_median_bps": None,
                "abs_p90_bps": None}
    abs_vals = [abs(v) for v in adv_bps]
    return {
        "n":              len(adv_bps),
        "median_bps":     round(statistics.median(adv_bps), 2),
        "p90_bps":        round(_percentile(adv_bps, 90), 2),
        "worst_bps":      round(max(adv_bps), 2),
        "mean_bps":       round(statistics.mean(adv_bps), 2),
        "abs_median_bps": round(statistics.median(abs_vals), 2),
        "abs_p90_bps":    round(_percentile(abs_vals, 90), 2),
    }


def _hour_et(ts_iso: str) -> Optional[int]:
    """Convert ISO-UTC timestamp string to America/New_York hour (0–23)."""
    if not ts_iso:
        return None
    try:
        dt = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(_ET).hour
    except Exception:
        return None


def _session_bucket(hour_et: Optional[int]) -> str:
    if hour_et is None:
        return "unknown"
    if hour_et < 9 or (hour_et == 9 and False):
        return "premarket"   # before 09:30 ET
    if 9 <= hour_et < 10:
        return "open_first_hour"   # 09:30–10:30 approximation
    if 10 <= hour_et < 15:
        return "mid"
    if 15 <= hour_et < 16:
        return "close_last_hour"
    if 16 <= hour_et < 20:
        return "postmarket"
    return "closed"


# ── Core report builder ──────────────────────────────────────────────────────

def build_report(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute the full slippage telemetry report from decisions rows."""
    submitted = 0
    filled_rows: List[Dict[str, Any]] = []
    partial_rows: List[Dict[str, Any]] = []
    nofill_rows: List[Dict[str, Any]] = []
    status_counts: Dict[str, int] = defaultdict(int)

    for r in rows:
        submitted += 1
        status = (r.get("fill_status") or "unknown").lower()
        status_counts[status] += 1
        fill_qty = r.get("fill_qty")
        shares = r.get("shares") or 0
        opened = bool(r.get("position_opened"))
        if opened and fill_qty and shares and fill_qty < shares:
            partial_rows.append(r)
        if r.get("slippage_bps") is not None:
            filled_rows.append(r)
        else:
            nofill_rows.append(r)

    overall_adv = [_adverse_bps(r) for r in filled_rows]
    overall_adv = [v for v in overall_adv if v is not None]
    overall = _stats_block(overall_adv)

    # Group by strategy
    by_strategy: Dict[str, Dict[str, Any]] = {}
    strat_buckets: Dict[str, List[float]] = defaultdict(list)
    for r in filled_rows:
        v = _adverse_bps(r)
        if v is None:
            continue
        strat_buckets[(r.get("strategy") or "UNKNOWN").upper()].append(v)
    for k, vs in strat_buckets.items():
        by_strategy[k] = _stats_block(vs)

    # Group by ticker — only report top 25 by n
    ticker_buckets: Dict[str, List[float]] = defaultdict(list)
    for r in filled_rows:
        v = _adverse_bps(r)
        if v is None:
            continue
        ticker_buckets[(r.get("ticker") or "?").upper()].append(v)
    by_ticker_full = {t: _stats_block(vs) for t, vs in ticker_buckets.items()}
    by_ticker_top = dict(sorted(
        by_ticker_full.items(),
        key=lambda kv: (kv[1].get("n") or 0),
        reverse=True,
    )[:25])

    # Group by ET session bucket
    session_buckets: Dict[str, List[float]] = defaultdict(list)
    for r in filled_rows:
        v = _adverse_bps(r)
        if v is None:
            continue
        bucket = _session_bucket(_hour_et(str(r.get("ts") or "")))
        session_buckets[bucket].append(v)
    by_session = {k: _stats_block(vs) for k, vs in session_buckets.items()}

    # Group by ET hour (24 buckets) — only those with rows
    hour_buckets: Dict[int, List[float]] = defaultdict(list)
    for r in filled_rows:
        v = _adverse_bps(r)
        if v is None:
            continue
        h = _hour_et(str(r.get("ts") or ""))
        if h is not None:
            hour_buckets[h].append(v)
    by_hour = {str(h): _stats_block(vs) for h, vs in sorted(hour_buckets.items())}

    # Phase 1G.1 calibration: separate actionable warnings from
    # informational small-sample notes. Same budget thresholds; the
    # severity gating is sample-size driven so a budget breach on
    # n=2 doesn't sound the same alarm as the same breach on n=50.
    warnings: List[str] = []
    info: List[str] = []
    abs_med = overall.get("abs_median_bps") or 0.0
    abs_p90 = overall.get("abs_p90_bps") or 0.0
    n_overall = int(overall.get("n") or 0)
    if filled_rows:
        med_breach = abs_med > BACKTEST_ONEWAY_SLIPPAGE_BPS
        p90_breach = abs_p90 > BACKTEST_ONEWAY_FRICTION_BPS
        if med_breach and n_overall >= SLIPPAGE_MIN_SAMPLE_FOR_WARNING:
            warnings.append(
                f"median |adverse| {abs_med:.1f} bps exceeds backtest one-way "
                f"slippage assumption {BACKTEST_ONEWAY_SLIPPAGE_BPS:.0f} bps "
                f"(n={n_overall})"
            )
        elif med_breach:
            info.append(
                f"INSUFFICIENT_SAMPLE: median |adverse| {abs_med:.1f} bps "
                f"exceeds {BACKTEST_ONEWAY_SLIPPAGE_BPS:.0f} bps benchmark "
                f"but n={n_overall} < {SLIPPAGE_MIN_SAMPLE_FOR_WARNING}; "
                f"informational only"
            )
        if p90_breach and n_overall >= SLIPPAGE_MIN_SAMPLE_FOR_WARNING:
            warnings.append(
                f"p90 |adverse| {abs_p90:.1f} bps exceeds backtest one-way "
                f"friction allowance {BACKTEST_ONEWAY_FRICTION_BPS:.0f} bps "
                f"(n={n_overall})"
            )
        elif p90_breach:
            info.append(
                f"INSUFFICIENT_SAMPLE: p90 |adverse| {abs_p90:.1f} bps "
                f"exceeds {BACKTEST_ONEWAY_FRICTION_BPS:.0f} bps benchmark "
                f"but n={n_overall} < {SLIPPAGE_MIN_SAMPLE_FOR_WARNING}; "
                f"informational only"
            )
    for strat, block in by_strategy.items():
        b_p90 = block.get("abs_p90_bps") or 0.0
        # Per-strategy gate was already n>=5; we keep that bar but make
        # the message consistent with the overall calibration.
        if (block.get("n") or 0) >= 5 and b_p90 > BACKTEST_ONEWAY_FRICTION_BPS * 2:
            warnings.append(
                f"{strat}: p90 |adverse| {b_p90:.1f} bps is >2x backtest "
                f"friction allowance (n={block.get('n')})"
            )

    fill_rate = None
    if submitted:
        fill_rate = round((submitted - len(nofill_rows)) / submitted, 4)

    return {
        "generated_at":    datetime.now(timezone.utc).isoformat(),
        "db_path":         str(cfg.DB_PATH),
        "benchmarks": {
            "oneway_slippage_bps": BACKTEST_ONEWAY_SLIPPAGE_BPS,
            "oneway_friction_bps": BACKTEST_ONEWAY_FRICTION_BPS,
            "source": "research/paper_trades/resolve_tactical_outcomes.py "
                      "ROUND_TRIP_FRICTION_PCT=0.30",
        },
        "summary": {
            "submitted_rows":   submitted,
            "filled_rows":      len(filled_rows),
            "partial_fills":    len(partial_rows),
            "nofill_rows":      len(nofill_rows),
            "fill_rate":        fill_rate,
            "status_counts":    dict(status_counts),
        },
        "overall_adverse_bps": overall,
        "by_strategy":         by_strategy,
        "by_ticker_top25":     by_ticker_top,
        "by_session":          by_session,
        "by_hour_et":          by_hour,
        "warnings":            warnings,
        # Phase 1G.1: sample-size-aware informational notes. Same
        # benchmarks as ``warnings``, but emitted when n is below the
        # actionable threshold so consumers can render them differently.
        "info":                info,
        "sample_size_thresholds": {
            "min_sample_for_warning": SLIPPAGE_MIN_SAMPLE_FOR_WARNING,
        },
    }


# ── Rendering ────────────────────────────────────────────────────────────────

def _fmt_bps(v: Optional[float]) -> str:
    return "    n/a" if v is None else f"{v:+7.2f}"


def render_text(report: Dict[str, Any]) -> str:
    s = report["summary"]
    o = report["overall_adverse_bps"]
    lines: List[str] = []
    bar = "─" * 80
    lines.append(bar)
    lines.append("SLIPPAGE TELEMETRY — paper-only")
    lines.append(f"generated_at={report['generated_at']}   db={report['db_path']}")
    lines.append(bar)
    lines.append(
        f"submitted={s['submitted_rows']}  filled={s['filled_rows']}  "
        f"partial={s['partial_fills']}  nofill={s['nofill_rows']}  "
        f"fill_rate={s['fill_rate'] if s['fill_rate'] is not None else 'n/a'}"
    )
    status_parts = ", ".join(f"{k}={v}" for k, v in sorted(s["status_counts"].items()))
    lines.append(f"status_counts: {status_parts or '(none)'}")
    lines.append("")
    lines.append(
        f"OVERALL adverse bps (positive = cost):  n={o['n']}  "
        f"median={_fmt_bps(o['median_bps'])}  p90={_fmt_bps(o['p90_bps'])}  "
        f"worst={_fmt_bps(o['worst_bps'])}  mean={_fmt_bps(o['mean_bps'])}"
    )
    lines.append(
        f"  |adverse|:  median={_fmt_bps(o['abs_median_bps'])}  "
        f"p90={_fmt_bps(o['abs_p90_bps'])}    "
        f"benchmark: one-way slip {BACKTEST_ONEWAY_SLIPPAGE_BPS:.0f} / "
        f"friction {BACKTEST_ONEWAY_FRICTION_BPS:.0f} bps"
    )
    lines.append("")

    if report["by_strategy"]:
        lines.append("BY STRATEGY:")
        lines.append(f"  {'strategy':<12} {'n':>4} {'median':>8} {'p90':>8} "
                     f"{'worst':>8} {'|med|':>8} {'|p90|':>8}")
        for k, b in sorted(report["by_strategy"].items()):
            lines.append(
                f"  {k:<12} {b['n']:>4} {_fmt_bps(b['median_bps'])} "
                f"{_fmt_bps(b['p90_bps'])} {_fmt_bps(b['worst_bps'])} "
                f"{_fmt_bps(b['abs_median_bps'])} {_fmt_bps(b['abs_p90_bps'])}"
            )
        lines.append("")

    if report["by_session"]:
        lines.append("BY ET SESSION:")
        lines.append(f"  {'bucket':<18} {'n':>4} {'median':>8} {'p90':>8} {'worst':>8}")
        for k, b in sorted(report["by_session"].items()):
            lines.append(
                f"  {k:<18} {b['n']:>4} {_fmt_bps(b['median_bps'])} "
                f"{_fmt_bps(b['p90_bps'])} {_fmt_bps(b['worst_bps'])}"
            )
        lines.append("")

    if report["by_ticker_top25"]:
        lines.append("TOP TICKERS BY FILL COUNT:")
        lines.append(f"  {'ticker':<8} {'n':>4} {'median':>8} {'p90':>8} {'worst':>8}")
        for k, b in report["by_ticker_top25"].items():
            lines.append(
                f"  {k:<8} {b['n']:>4} {_fmt_bps(b['median_bps'])} "
                f"{_fmt_bps(b['p90_bps'])} {_fmt_bps(b['worst_bps'])}"
            )
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
    clean_epoch_iso: str = CLEAN_PAPER_EVIDENCE_START,
) -> Dict[str, Any]:
    """Phase 1D wrapper. Returns the full-ledger ``build_report`` shape at
    the top level (so existing readers — notably the dashboard's RISK
    TELEMETRY panel — keep working unchanged) PLUS a ``clean_epoch``
    sub-block containing the clean-epoch view.

    Back-compat doctrine: pre-Phase-1D readers index ``summary``,
    ``overall_adverse_bps``, ``warnings`` etc. directly on the JSON root.
    Spreading the full-ledger view at the root preserves that contract.
    """
    full_rows  = load_decisions(db_path, None)
    clean_rows = load_decisions(db_path, clean_epoch_iso)
    full  = build_report(full_rows)
    clean = build_report(clean_rows)
    dual: Dict[str, Any] = dict(full)  # spread full-ledger at top level
    dual["clean_epoch_start"] = clean_epoch_iso
    dual["full_ledger"]       = full
    dual["clean_epoch"]       = clean
    return dual


def render_dual_text(dual: Dict[str, Any]) -> str:
    bar = "═" * 80
    parts: List[str] = []
    parts.append(bar)
    parts.append("SLIPPAGE TELEMETRY — Phase 1D dual-scope")
    parts.append(f"generated_at={dual['generated_at']}   db={dual['db_path']}")
    parts.append(f"clean_epoch_start={dual['clean_epoch_start']}")
    parts.append(bar)
    parts.append("─── FULL LEDGER ───")
    parts.append(render_text(dual["full_ledger"]))
    parts.append("─── CLEAN EPOCH (rows logged on/after clean_epoch_start) ───")
    parts.append(render_text(dual["clean_epoch"]))
    return "\n".join(parts) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Paper slippage telemetry report")
    parser.add_argument("--db-path", default=str(cfg.DB_PATH),
                        help="SQLite DB path (default: core.config.DB_PATH)")
    parser.add_argument("--since", default=None,
                        help=("Override the clean-epoch start (ISO ts). "
                              f"Default: {CLEAN_PAPER_EVIDENCE_START}"))
    parser.add_argument("--json-out", default=None,
                        help="Override JSON output path")
    parser.add_argument("--txt-out", default=None,
                        help="Override text output path")
    parser.add_argument("--print", action="store_true",
                        help="Also print the rendered report to stdout")
    args = parser.parse_args(argv)

    db_path = Path(args.db_path)
    clean_epoch_iso = args.since or CLEAN_PAPER_EVIDENCE_START
    dual = build_dual_scope_report(db_path, clean_epoch_iso=clean_epoch_iso)

    json_path = Path(args.json_out) if args.json_out else (
        cfg.CACHE_DIR / "research" / "slippage_telemetry_latest.json"
    )
    txt_path = Path(args.txt_out) if args.txt_out else (
        cfg.LOG_DIR / "slippage_telemetry_latest.txt"
    )
    # Write dual-scope JSON; text output mirrors that shape.
    json_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = json_path.with_suffix(json_path.suffix + ".tmp")
    tmp.write_text(json.dumps(dual, indent=2, default=str), encoding="utf-8")
    tmp.replace(json_path)
    txt_path.write_text(render_dual_text(dual), encoding="utf-8")

    if args.print or dual["full_ledger"]["summary"]["submitted_rows"] == 0:
        print(render_dual_text(dual))
    print(f"wrote {json_path}")
    print(f"wrote {txt_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

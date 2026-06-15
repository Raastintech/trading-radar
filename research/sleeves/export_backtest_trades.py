"""
research/sleeves/export_backtest_trades.py — standardized trade-level CSV
exports for the active sleeves (Phase 9B).

Outputs (one CSV per sleeve, written to research/sleeves/trades/):

  SNIPER_V6.csv      — historical sniper backtest, v6 large-cap + SPY 200d gate
  VOYAGER_PAPER.csv  — historical voyager backtest, mode A (base, no 13F)
  SHORT_A.csv        — live paper_signals + outcomes (transformed)

Standard schema (any unavailable field is left blank):

  strategy, baseline_tag, ticker, side, entry_date, exit_date, horizon,
  entry_price, exit_price, raw_return_pct, adjusted_return_pct,
  stop_hit, target_hit, sector, source_backtest, friction_model, notes

Design
------
- Cache-only: sets BACKTEST_CACHE_ONLY=true so backtests never call Alpaca.
- Universe is intersected with cache/backtest_prices/*.parquet so a missing
  ticker can never raise.
- Backtests are imported and instrumented (monkey-patched) — their strategy
  logic is NOT modified.
- SHORT_A export pulls live paper_signals + paper_signal_outcomes; running
  the heavyweight short_backtester separately is the path for historical depth.

Usage
-----
  cd /home/gem/trading-production
  .venv/bin/python research/sleeves/export_backtest_trades.py [--quick]
  .venv/bin/python research/sleeves/export_backtest_trades.py --sleeves SNIPER_V6
"""
from __future__ import annotations

import argparse
import csv
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

REPO = Path(__file__).resolve().parent.parent.parent
TRADES_DIR = REPO / "research" / "sleeves" / "trades"
PRICE_CACHE = REPO / "cache" / "backtest_prices"
DB_PATH = REPO / "db" / "trading.db"

STD_FIELDS = [
    "strategy", "baseline_tag", "ticker", "side",
    "entry_date", "exit_date", "horizon",
    "entry_price", "exit_price",
    "raw_return_pct", "adjusted_return_pct",
    "stop_hit", "target_hit",
    "sector", "source_backtest", "friction_model", "notes",
]

ACTIVE_SLEEVES = ["SNIPER_V6", "VOYAGER_PAPER", "SHORT_A"]


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _cached_tickers() -> set:
    if not PRICE_CACHE.exists():
        return set()
    return {p.stem for p in PRICE_CACHE.glob("*.parquet")}


def _fmt_bool(v) -> str:
    if v is None:
        return ""
    return "1" if v else "0"


def _fmt_date(d) -> str:
    if d is None:
        return ""
    if isinstance(d, str):
        return d
    if isinstance(d, datetime):
        return d.date().isoformat()
    return d.isoformat()


def _fmt_num(v, places: int = 4) -> str:
    if v is None:
        return ""
    try:
        return f"{float(v):.{places}f}"
    except Exception:
        return ""


def _write_csv(path: Path, rows: Sequence[Dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=STD_FIELDS)
        w.writeheader()
        for r in rows:
            # Coerce missing fields to blank, drop unknown keys.
            out = {k: ("" if r.get(k) is None else r.get(k)) for k in STD_FIELDS}
            w.writerow(out)
    return len(rows)


# ──────────────────────────────────────────────────────────────────────────────
# SNIPER_V6 export
# ──────────────────────────────────────────────────────────────────────────────

def export_sniper_v6(quick: bool = False) -> List[Dict[str, Any]]:
    """Run sniper_backtest with cache-only enforcement and capture v6 signals."""
    os.environ.setdefault("BACKTEST_CACHE_ONLY", "true")
    sys.path.insert(0, str(REPO / "research" / "backtests"))
    import sniper_backtest as sb  # type: ignore

    cached = _cached_tickers()
    sb.FULL_UNIVERSE      = [t for t in sb.FULL_UNIVERSE      if t in cached]
    sb.EX_SAAS_UNIVERSE   = [t for t in sb.EX_SAAS_UNIVERSE   if t in cached]
    sb.LARGE_CAP_UNIVERSE = [t for t in sb.LARGE_CAP_UNIVERSE if t in cached]
    sb.QUICK_UNIVERSE     = [t for t in sb.QUICK_UNIVERSE     if t in cached]

    captured_v6: List[Dict[str, Any]] = []
    captured_v4: List[Dict[str, Any]] = []
    orig_add = sb.ResultSet.add

    def capturing_add(self, s):
        # Tag by the result-set label for downstream filtering.
        rec = dict(s)
        rec["_label"] = self.label
        if "v6" in self.label:
            captured_v6.append(rec)
        elif "v4 baseline" in self.label:
            captured_v4.append(rec)
        return orig_add(self, s)

    sb.ResultSet.add = capturing_add  # type: ignore[assignment]

    if quick:
        sb.run_backtest(
            sb.QUICK_START.year, sb.QUICK_START.month, sb.QUICK_START.day,
            sb.QUICK_END.year,   sb.QUICK_END.month,   sb.QUICK_END.day,
            quick=True,
        )
    else:
        sb.run_backtest(
            sb.FULL_START.year, sb.FULL_START.month, sb.FULL_START.day,
            sb.FULL_END.year,   sb.FULL_END.month,   sb.FULL_END.day,
            quick=False,
        )

    sb.ResultSet.add = orig_add  # type: ignore[assignment]

    rows: List[Dict[str, Any]] = []
    horizons = sb.HOLD_HORIZONS
    baseline_tag = "v6_quick" if quick else "v6_full"
    for sig in captured_v6:
        ticker = sig["ticker"]
        entry_date = sig["date"]
        entry_price = sig["close"]
        stop_price = sig.get("stop_1_5x")
        target_price = sig.get("target_1_5x")
        for h in horizons:
            o = sig["outcomes_1_5x"][h]
            score = sig.get("score", "")
            vol_ratio = sig.get("vol_ratio", "")
            try:
                vol_str = f"{float(vol_ratio):.2f}"
            except Exception:
                vol_str = str(vol_ratio)
            note_parts = [
                f"label={sig.get('_label','')}",
                "stop=1.5xATR",
                "target=R:R 2.5",
                f"score={score}",
                f"vol_ratio={vol_str}",
                f"stop_price={_fmt_num(stop_price)}",
                f"target_price={_fmt_num(target_price)}",
            ]
            rows.append({
                "strategy":           "SNIPER",
                "baseline_tag":       baseline_tag,
                "ticker":             ticker,
                "side":               "LONG",
                "entry_date":         _fmt_date(entry_date),
                "exit_date":          "",
                "horizon":            h,
                "entry_price":        _fmt_num(entry_price),
                "exit_price":         _fmt_num(entry_price * (1.0 + (o["raw"] or 0.0))),
                "raw_return_pct":     _fmt_num((o["raw"] or 0.0) * 100, places=6),
                "adjusted_return_pct":_fmt_num((o["adj"] or 0.0) * 100, places=6),
                "stop_hit":           _fmt_bool(o["stop_hit"]),
                "target_hit":         _fmt_bool(o["target_hit"]),
                "sector":             "",
                "source_backtest":    "research/backtests/sniper_backtest.py",
                "friction_model":     "commission_5bps_each_side+slippage_10bps_each_side (RT 30bps)",
                "notes":              "; ".join(note_parts),
            })
    print(f"  [sniper] captured v6 signals: {len(captured_v6)} (×{len(horizons)} horizons → {len(rows)} rows); "
          f"v4 baseline: {len(captured_v4)}")
    return rows


# ──────────────────────────────────────────────────────────────────────────────
# VOYAGER_PAPER export
# ──────────────────────────────────────────────────────────────────────────────

# Voyager: conservative round-trip friction. Voyager's underlying backtest
# reports raw forward returns with no friction modelled. We apply a flat RT
# cost at export time so downstream evidence reads honestly. 0.30% mirrors
# sniper's commission+slippage budget for liquid US large-caps.
VOYAGER_DEFAULT_FRICTION_RT_PCT = 0.30


def export_voyager_paper(quick: bool = False, friction_rt_pct: float = VOYAGER_DEFAULT_FRICTION_RT_PCT) -> List[Dict[str, Any]]:
    """Run voyager_v2_backtest in skip-13f mode with cache-only enforcement,
    capture mode A signals."""
    os.environ.setdefault("BACKTEST_CACHE_ONLY", "true")
    sys.path.insert(0, str(REPO / "research" / "backtests"))
    import voyager_v2_backtest as vb  # type: ignore

    cached = _cached_tickers()
    full_filtered = [t for t in vb.FULL_UNIVERSE if t in cached]
    quick_filtered = [t for t in vb.QUICK_UNIVERSE if t in cached]

    if quick:
        universe = quick_filtered
        scan_dates = vb.QUICK_SCAN_DATES
    else:
        universe = full_filtered
        scan_dates = vb.FULL_SCAN_DATES

    # Bound the price-fetch window
    start_date = min(scan_dates) - timedelta(days=400)
    end_date   = max(scan_dates) + timedelta(days=400)

    print(f"  [voyager] universe={len(universe)} tickers, scan_dates={len(scan_dates)}")
    result = vb.run_backtest(
        universe=universe,
        scan_dates=scan_dates,
        include_13f=False,
    )
    signals_a = result.get("signals_a") or []
    print(f"  [voyager] mode-A signals: {len(signals_a)}")

    rows: List[Dict[str, Any]] = []
    baseline_tag = "voyager_v2_quick_modeA" if quick else "voyager_v2_full_modeA"
    horizon_map = {"30d": 30, "90d": 90, "180d": 130, "365d": 252}  # mirror FWD_WINDOWS
    for sig in signals_a:
        ticker = sig.get("ticker")
        scan_date = sig.get("scan_date")  # ISO string per backtest code
        entry_price = sig.get("entry_price") or sig.get("close")
        stop_price = sig.get("stop")
        target_price = sig.get("target")
        note_extra = (f"score={sig.get('final_score', sig.get('score',''))}; "
                      f"size_bucket={sig.get('size_bucket','')}; "
                      f"stop_price={_fmt_num(stop_price)}; target_price={_fmt_num(target_price)}")
        for label, h_days in horizon_map.items():
            ret = sig.get(label)
            if ret is None:
                # Forward window unavailable (signal too close to data end)
                rows.append({
                    "strategy":           "VOYAGER",
                    "baseline_tag":       baseline_tag,
                    "ticker":             ticker,
                    "side":               "LONG",
                    "entry_date":         scan_date or "",
                    "exit_date":          "",
                    "horizon":            h_days,
                    "entry_price":        _fmt_num(entry_price),
                    "exit_price":         "",
                    "raw_return_pct":     "",
                    "adjusted_return_pct":"",
                    "stop_hit":           "",
                    "target_hit":         "",
                    "sector":             "",
                    "source_backtest":    "research/backtests/voyager_v2_backtest.py (mode A)",
                    "friction_model":     "RAW_FORWARD_RETURN — no friction modelled",
                    "notes":              note_extra + "; outcome unavailable",
                })
                continue
            ret_pct = float(ret)
            adj_pct = ret_pct - friction_rt_pct
            exit_price = entry_price * (1.0 + ret_pct / 100.0) if entry_price else None
            if friction_rt_pct > 0:
                friction_label = (f"RT {friction_rt_pct:.2f}% applied (commission+slippage estimate, "
                                  "voyager backtest does not natively model friction)")
            else:
                friction_label = "RAW_FORWARD_RETURN — no friction modelled"
            rows.append({
                "strategy":           "VOYAGER",
                "baseline_tag":       baseline_tag,
                "ticker":             ticker,
                "side":               "LONG",
                "entry_date":         scan_date or "",
                "exit_date":          "",  # voyager backtest reports return only
                "horizon":            h_days,
                "entry_price":        _fmt_num(entry_price),
                "exit_price":         _fmt_num(exit_price),
                "raw_return_pct":     _fmt_num(ret_pct, places=6),
                "adjusted_return_pct":_fmt_num(adj_pct, places=6),
                "stop_hit":           "",   # voyager backtest does not test stops
                "target_hit":         "",
                "sector":             "",
                "source_backtest":    "research/backtests/voyager_v2_backtest.py (mode A)",
                "friction_model":     friction_label,
                "notes":              note_extra,
            })
    print(f"  [voyager] rows: {len(rows)}")
    return rows


# ──────────────────────────────────────────────────────────────────────────────
# SHORT_A export
# ──────────────────────────────────────────────────────────────────────────────

def export_short_a() -> List[Dict[str, Any]]:
    """Pull live paper_signals + paper_signal_outcomes; transform to standard
    schema. For historical depth, run
    `research/sleeves/export_short_a_history.py` separately — it merges with
    this output, so live-paper rows are not lost.

    Hygiene: if SHORT_A.csv already contains historical rows
    (`baseline_tag` starting with `short_history_`), those rows are preserved
    in the merge done by the historical exporter; this function only emits
    the live-paper subset."""
    if not DB_PATH.exists():
        print("  [short] db not found")
        return []
    rows: List[Dict[str, Any]] = []
    sql = """
      SELECT ps.id, ps.ticker, ps.side, ps.sector, ps.logged_at,
             ps.entry_price, ps.stop_loss, ps.target_price,
             pso.horizon_days, pso.outcome_date,
             pso.return_pct, pso.adjusted_return_pct,
             pso.stop_hit, pso.target_hit, pso.still_open,
             pso.hold_complete
      FROM paper_signals ps
      JOIN paper_signal_outcomes pso ON pso.signal_id = ps.id
      WHERE ps.sleeve = 'SHORT_A'
    """
    with sqlite3.connect(str(DB_PATH)) as con:
        con.row_factory = sqlite3.Row
        for r in con.execute(sql):
            entry_iso = (r["logged_at"] or "")[:10] or ""
            still_open = bool(r["still_open"]) if r["still_open"] is not None else False
            ret = r["return_pct"]
            adj = r["adjusted_return_pct"]
            entry_price = r["entry_price"]
            exit_price = ""
            if not still_open and entry_price and ret is not None:
                # Approximate exit from entry × (1 - return%/100) for SHORT
                # NB: paper_signal_outcomes already tracks adjusted_return_pct
                # net of friction declared by execution_policy.
                if (r["side"] or "").upper() == "SHORT":
                    exit_price = _fmt_num(entry_price * (1.0 - float(ret) / 100.0))
                else:
                    exit_price = _fmt_num(entry_price * (1.0 + float(ret) / 100.0))
            rows.append({
                "strategy":           "SHORT_SLEEVE_A",
                "baseline_tag":       "live_paper_db",
                "ticker":             r["ticker"] or "",
                "side":               r["side"] or "SHORT",
                "entry_date":         entry_iso,
                "exit_date":          (r["outcome_date"] or "")[:10] if r["outcome_date"] else "",
                "horizon":            r["horizon_days"],
                "entry_price":        _fmt_num(entry_price),
                "exit_price":         exit_price,
                "raw_return_pct":     _fmt_num(ret, places=6) if ret is not None else "",
                "adjusted_return_pct":_fmt_num(adj, places=6) if adj is not None else "",
                "stop_hit":           _fmt_bool(bool(r["stop_hit"]) if r["stop_hit"] is not None else None),
                "target_hit":         _fmt_bool(bool(r["target_hit"]) if r["target_hit"] is not None else None),
                "sector":             r["sector"] or "",
                "source_backtest":    "db/trading.db (paper_signals + paper_signal_outcomes)",
                "friction_model":     "execution_policy_adjusted (live paper)",
                "notes":              ("still_open" if still_open else
                                       ("hold_complete" if r["hold_complete"] else "interim_outcome"))
                                      + f"; stop_price={_fmt_num(r['stop_loss'])}"
                                      + f"; target_price={_fmt_num(r['target_price'])}",
            })
    print(f"  [short] live paper rows: {len(rows)}")
    return rows


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

EXPORTERS = {
    "SNIPER_V6":     export_sniper_v6,
    "VOYAGER_PAPER": export_voyager_paper,
    "SHORT_A":       export_short_a,
}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Export backtest trades to standard CSV.")
    parser.add_argument("--sleeves", default=",".join(ACTIVE_SLEEVES),
                        help="Comma-separated sleeves to export.")
    parser.add_argument("--quick", action="store_true",
                        help="Use quick universe/window for sniper and voyager.")
    parser.add_argument("--voyager-friction-rt-pct", type=float, default=VOYAGER_DEFAULT_FRICTION_RT_PCT,
                        help=f"Conservative RT friction (in pct) to subtract from voyager raw forward "
                             f"returns. Default {VOYAGER_DEFAULT_FRICTION_RT_PCT:.2f} (commission+slippage estimate). "
                             f"Pass 0.0 for the raw signal; the audit script also reports a sensitivity table.")
    parser.add_argument("--out-dir", default=str(TRADES_DIR),
                        help=f"Output directory (default {TRADES_DIR}).")
    args = parser.parse_args(argv)

    sleeves = [s.strip() for s in args.sleeves.split(",") if s.strip()]
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    summary: Dict[str, int] = {}
    for s in sleeves:
        if s not in EXPORTERS:
            print(f"⚠ skip {s} — unknown sleeve")
            continue
        print(f"=== Exporting {s} ===")
        try:
            if s == "SNIPER_V6":
                rows = EXPORTERS[s](quick=args.quick)
            elif s == "VOYAGER_PAPER":
                rows = EXPORTERS[s](quick=args.quick, friction_rt_pct=args.voyager_friction_rt_pct)
            else:
                rows = EXPORTERS[s]()
                # Protect any historical SHORT_A export already on disk: if
                # SHORT_A.csv currently contains rows tagged short_history_*,
                # merge our live rows in instead of overwriting.
                if s == "SHORT_A":
                    out_path = Path(args.out_dir) / "SHORT_A.csv"
                    if out_path.exists():
                        with out_path.open() as fh:
                            prev = list(csv.DictReader(fh))
                        has_history = any(
                            (r.get("baseline_tag") or "").startswith("short_history_") for r in prev
                        )
                        if has_history:
                            keyfn = lambda r: f"{r.get('ticker','')}|{r.get('entry_date','')}|{r.get('horizon','')}|{r.get('baseline_tag','')}"
                            merged = {keyfn(r): r for r in prev}
                            for r in rows:
                                merged[keyfn(r)] = r
                            rows = list(merged.values())
                            print(f"  [short] preserved {len(prev)} pre-existing rows; merged to {len(rows)}")
        except Exception as exc:
            print(f"✗ {s} export failed: {exc!r}")
            rows = []
        out_path = out_dir / f"{s}.csv"
        n = _write_csv(out_path, rows)
        summary[s] = n
        print(f"→ {out_path} ({n} rows)")

    print()
    print("=" * 50)
    print("Export summary:")
    for s, n in summary.items():
        print(f"  {s:<16} {n:>6} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())

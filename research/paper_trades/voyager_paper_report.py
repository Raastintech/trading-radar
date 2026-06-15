"""
research/paper_trades/voyager_paper_report.py — Voyager paper-validation report.

Reads voyager_paper_signals from db/trading.db and produces a structured
paper-validation status report. Optionally fetches current prices from Alpaca
to fill in outcomes for signals that have passed their measurement window.

Usage:
  cd /home/gem/trading-production
  SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python3 \
      research/paper_trades/voyager_paper_report.py [--update-outcomes]

  --update-outcomes  Fetch current prices from Alpaca and fill in any
                     outcome_30d / outcome_90d / outcome_180d columns that
                     are NULL and whose measurement window has elapsed.
                     Also checks whether ticker is still above MA200.

Paper-valid gate criteria (from voyager_scorecard.md):
  1. ≥ 30 signals collected
  2. ≥ 30 signals with 30+ days since logged (measurement window closed)
  3. 30d avg return > 0%
  4. above_ma200_at_30d rate ≥ 70% (structural stop intact — first-cycle check)
  5. 90d win rate ≥ 50% (where n ≥ 10 outcomes available)
  6. No archetype with 90d win rate < 30% (no archetype systematically failing)
  7. 13F avg pts contribution ≥ -2 (overlay not actively harmful)

Doctrine: VOYAGER = LONG only. direction is always 'LONG'.
SHORT logic belongs in strategies/short_sleeve.py.
"""
from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

# ── Load credentials (SNIPER_ENV_PATH pattern) ────────────────────────────────
_env_path = os.environ.get("SNIPER_ENV_PATH", "")
if _env_path:
    try:
        from dotenv import load_dotenv as _load
        _load(Path(_env_path), override=True)
    except ImportError:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── Paper-valid gate thresholds ───────────────────────────────────────────────
GATE_MIN_SIGNALS        = 30
GATE_MIN_MEASURED_30D   = 30      # signals with ≥30 day hold window closed
GATE_30D_AVG_RETURN     = 0.0     # 30d avg return must be > 0%
GATE_MA200_HOLD_RATE    = 70.0    # % of signals still above MA200 at 30d
GATE_90D_WIN_RATE       = 50.0    # overall 90d win rate
GATE_ARCH_MIN_WIN_RATE  = 30.0    # minimum 90d win rate per archetype
GATE_13F_MIN_AVG_PTS    = -2.0    # 13F overlay average pts must be ≥ -2


def _pct(x: Optional[float]) -> str:
    return "N/A" if x is None else f"{x:+.1f}%"


def _avg(vals: List[Optional[float]]) -> Optional[float]:
    clean = [v for v in vals if v is not None]
    return round(statistics.mean(clean), 1) if clean else None


def _win_rate(vals: List[Optional[float]]) -> Optional[float]:
    clean = [v for v in vals if v is not None]
    if not clean:
        return None
    return round(sum(1 for v in clean if v > 0) / len(clean) * 100, 1)


def _days_since(logged_at: str) -> int:
    try:
        dt = datetime.fromisoformat(logged_at.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).days
    except Exception:
        return 0


def _gate(label: str, value: float, threshold: float, direction: str = "ge") -> str:
    """Return a pass/fail indicator."""
    if direction == "ge":
        ok = value >= threshold
    elif direction == "gt":
        ok = value > threshold
    else:
        ok = value <= threshold
    status = "PASS" if ok else "FAIL"
    return f"  [{status}] {label}: {value:.1f} (threshold {direction} {threshold})"


def load_signals(db_path: Path) -> List[Dict]:
    """Load all paper signals from the DB. Returns [] if table doesn't exist yet."""
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            "SELECT * FROM voyager_paper_signals ORDER BY logged_at DESC"
        ).fetchall()
        desc = conn.execute("PRAGMA table_info(voyager_paper_signals)").fetchall()
        cols = [d[1] for d in desc]
        conn.close()
        return [dict(zip(cols, r)) for r in rows]
    except sqlite3.OperationalError:
        return []  # table doesn't exist yet — no signals logged


def update_outcomes(signals: List[Dict], db_path: Path) -> int:
    """
    For any signal where an outcome window has elapsed but the column is NULL,
    fetch current price from Alpaca and fill it in.

    Returns count of outcomes updated.
    """
    try:
        from core.alpaca_client import get_alpaca
        alpaca = get_alpaca()
    except Exception as exc:
        print(f"  Cannot connect to Alpaca for outcome updates: {exc}")
        return 0

    HORIZONS = {"30d": 30, "90d": 65, "180d": 130}  # calendar days (approx trading days)
    updated = 0
    conn = sqlite3.connect(str(db_path))

    for sig in signals:
        if sig.get("signal_status") == "stopped_out":
            continue

        days_old = _days_since(sig["logged_at"])
        entry = sig.get("entry_price") or 0
        if entry <= 0:
            continue

        for label, min_days in HORIZONS.items():
            col = f"outcome_{label}"
            if sig.get(col) is not None:
                continue          # already measured
            if days_old < min_days:
                continue          # window not elapsed yet

            try:
                bars = alpaca.get_daily_bars(sig["ticker"], days=5)
                if not bars:
                    continue
                current_price = bars[-1]["close"]
                ret_pct = round((current_price - entry) / entry * 100, 2)

                # For 30d measurement, also check MA200 hold
                above_ma200 = None
                if label == "30d":
                    try:
                        all_bars = alpaca.get_daily_bars(sig["ticker"], days=260)
                        if len(all_bars) >= 200:
                            closes = [b["close"] for b in all_bars]
                            ma200  = statistics.mean(closes[-200:])
                            above_ma200 = current_price > ma200
                    except Exception:
                        pass

                mdate = date.today().isoformat()
                dcol  = f"outcome_{label}_date"
                with conn:
                    if label == "30d" and above_ma200 is not None:
                        conn.execute(
                            f"UPDATE voyager_paper_signals SET {col}=?, {dcol}=?, above_ma200_at_30d=? WHERE id=?",
                            (ret_pct, mdate, int(above_ma200), sig["id"]),
                        )
                    else:
                        conn.execute(
                            f"UPDATE voyager_paper_signals SET {col}=?, {dcol}=? WHERE id=?",
                            (ret_pct, mdate, sig["id"]),
                        )
                print(f"  Outcome updated: {sig['ticker']} ({label}) = {ret_pct:+.1f}%")
                updated += 1

            except Exception as exc:
                logger.debug("Outcome update failed %s %s: %s", sig["ticker"], label, exc)

    conn.close()
    return updated


def print_report(signals: List[Dict]) -> None:
    def section(title: str):
        print(f"\n{'═'*70}")
        print(f"  {title}")
        print(f"{'═'*70}")

    # ── Header ────────────────────────────────────────────────────────────────
    section("VOYAGER PAPER VALIDATION REPORT")
    print(f"\n  Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}")
    print(f"  Doctrine:  VOYAGER = LONG only. SHORT = strategies/short_sleeve.py")
    print(f"  Goal:      30 signals × ≥30-day hold → paper-valid gate")

    if not signals:
        section("1. COLLECTION STATUS")
        print(f"\n  Total signals logged: 0")
        print(f"  Target minimum:      {GATE_MIN_SIGNALS}")
        print(f"\n  Paper-valid gate: NOT MET (need {GATE_MIN_SIGNALS} signals)")
        print(f"\n  To start collecting: set VOYAGER_PAPER_LOG=true in your .env")
        print(f"  Signals are logged automatically by VoyagerScanner.scan().")
        return

    # ── 1. Collection status ──────────────────────────────────────────────────
    section("1. COLLECTION STATUS")
    n_total   = len(signals)
    n_open    = sum(1 for s in signals if s.get("signal_status") == "open")
    n_stopped = sum(1 for s in signals if s.get("signal_status") == "stopped_out")
    n_meas_30d = sum(1 for s in signals if s.get("outcome_30d") is not None)

    # Signals with ≥30 days elapsed (window closed, whether measured or not)
    n_window_closed = sum(1 for s in signals if _days_since(s["logged_at"]) >= 30)

    if signals:
        date_range_start = min(s["logged_at"][:10] for s in signals)
        date_range_end   = max(s["logged_at"][:10] for s in signals)
    else:
        date_range_start = date_range_end = "—"

    print(f"\n  Total signals logged:     {n_total}")
    print(f"  Target minimum:           {GATE_MIN_SIGNALS}")
    print(f"  Progress:                 {n_total}/{GATE_MIN_SIGNALS} ({n_total/GATE_MIN_SIGNALS*100:.0f}%)")
    print(f"\n  Status breakdown:")
    print(f"    Open (monitoring):       {n_open}")
    print(f"    Stopped out:             {n_stopped}")
    print(f"    30d window closed:       {n_window_closed}")
    print(f"    30d outcome measured:    {n_meas_30d}")
    print(f"\n  Date range: {date_range_start} → {date_range_end}")

    # ── 2. Signals by archetype ───────────────────────────────────────────────
    section("2. SIGNALS BY ARCHETYPE")
    by_arch: Dict[str, List[Dict]] = defaultdict(list)
    for s in signals:
        by_arch[s.get("archetype", "unknown")].append(s)

    print(f"\n  {'Archetype':<24} {'Count':>6} {'AvgScore':>9} {'Avg13F':>7} "
          f"{'Ret30d':>8} {'Ret90d':>8} {'WR90d':>7}")
    print(f"  {'-'*73}")
    for arch in ["BASE_ACCUMULATION", "TREND_PULLBACK", "EARLY_ACCUMULATION", "unknown"]:
        sigs = by_arch.get(arch, [])
        if not sigs:
            continue
        scores = [s["final_score"] for s in sigs]
        pts13f = [s.get("thirteen_f_pts") or 0 for s in sigs]
        r30  = [s.get("outcome_30d")  for s in sigs]
        r90  = [s.get("outcome_90d")  for s in sigs]
        print(f"  {arch:<24} {len(sigs):>6} {statistics.mean(scores):>9.1f} "
              f"{statistics.mean(pts13f):>7.1f} "
              f"{_pct(_avg(r30)):>8} {_pct(_avg(r90)):>8} "
              f"{str(_win_rate(r90) or '—'):>7}")

    # ── 3. Signals by size bucket ─────────────────────────────────────────────
    section("3. SIGNALS BY SIZE BUCKET")
    by_bucket: Dict[str, List[Dict]] = defaultdict(list)
    for s in signals:
        by_bucket[s.get("size_bucket") or "unknown"].append(s)

    print(f"\n  {'Bucket':<12} {'Count':>6} {'AvgScore':>9} {'Ret30d':>8} {'Ret90d':>8} {'WR90d':>7}")
    print(f"  {'-'*56}")
    for bucket in ["large", "mid", "emerging", "unknown"]:
        sigs = by_bucket.get(bucket, [])
        if not sigs:
            continue
        scores = [s["final_score"] for s in sigs]
        r30  = [s.get("outcome_30d") for s in sigs]
        r90  = [s.get("outcome_90d") for s in sigs]
        print(f"  {bucket:<12} {len(sigs):>6} {statistics.mean(scores):>9.1f} "
              f"{_pct(_avg(r30)):>8} {_pct(_avg(r90)):>8} "
              f"{str(_win_rate(r90) or '—'):>7}")

    # ── 4. 13F analysis ───────────────────────────────────────────────────────
    section("4. 13F OVERLAY ANALYSIS")
    pts_vals = [s.get("thirteen_f_pts") or 0 for s in signals]
    avg_pts  = statistics.mean(pts_vals) if pts_vals else 0

    print(f"\n  Average 13F pts across all signals: {avg_pts:.2f}")
    print(f"  (Healthy: close to 0 or slightly positive. <-2 = overlay actively harmful)")

    # 13F flow distribution
    flow_counts: Dict[str, int] = defaultdict(int)
    for s in signals:
        flow_counts[s.get("thirteen_f_flow") or "N/A"] += 1
    print(f"\n  13F flow distribution:")
    for flow, cnt in sorted(flow_counts.items(), key=lambda x: -x[1]):
        print(f"    {flow:<12} {cnt:>4}  ({cnt/n_total*100:.0f}%)")

    # 13F pts distribution
    pts_dist: Dict[int, int] = defaultdict(int)
    for p in pts_vals:
        pts_dist[int(p)] += 1
    print(f"\n  13F pts distribution:")
    for pt in sorted(pts_dist.keys(), reverse=True):
        n = pts_dist[pt]
        print(f"    {pt:>+3}  {n:>4}  ({n/n_total*100:.0f}%)")

    # Correlation: 13F pts vs 90d return (rough)
    paired = [(s.get("thirteen_f_pts") or 0, s.get("outcome_90d"))
              for s in signals if s.get("outcome_90d") is not None]
    if len(paired) >= 5:
        pos_13f_sigs = [r for p, r in paired if p > 0]
        neg_13f_sigs = [r for p, r in paired if p < 0]
        neu_13f_sigs = [r for p, r in paired if p == 0]
        print(f"\n  13F pts → 90d return (where outcomes available, n={len(paired)}):")
        if pos_13f_sigs:
            print(f"    Positive 13F pts (n={len(pos_13f_sigs)}): avg 90d = {_pct(_avg(pos_13f_sigs))}")
        if neg_13f_sigs:
            print(f"    Negative 13F pts (n={len(neg_13f_sigs)}): avg 90d = {_pct(_avg(neg_13f_sigs))}")
        if neu_13f_sigs:
            print(f"    Zero 13F pts     (n={len(neu_13f_sigs)}): avg 90d = {_pct(_avg(neu_13f_sigs))}")
    else:
        print(f"\n  Insufficient 90d outcomes for 13F correlation (need ≥5, have {len(paired)})")

    # ── 5. Forward return summary ─────────────────────────────────────────────
    section("5. FORWARD RETURNS")
    print(f"\n  {'Horizon':<8} {'N':>4} {'AvgRet':>9} {'WinRate':>9} {'Median':>9} {'Best':>9} {'Worst':>9}")
    print(f"  {'-'*63}")
    for lbl in ["outcome_30d", "outcome_90d", "outcome_180d"]:
        vals = [s.get(lbl) for s in signals]
        clean = [v for v in vals if v is not None]
        n = len(clean)
        if n == 0:
            print(f"  {lbl.replace('outcome_',''):<8} {'0':>4}  (no outcomes yet)")
            continue
        clean_sorted = sorted(clean)
        median = clean_sorted[n // 2]
        print(f"  {lbl.replace('outcome_',''):<8} {n:>4} {_pct(_avg(clean)):>9} "
              f"{str(_win_rate(clean)):>9} {_pct(median):>9} "
              f"{_pct(max(clean)):>9} {_pct(min(clean)):>9}")

    # MA200 hold rate at 30d
    ma200_vals = [s.get("above_ma200_at_30d") for s in signals if s.get("above_ma200_at_30d") is not None]
    if ma200_vals:
        hold_rate = sum(ma200_vals) / len(ma200_vals) * 100
        print(f"\n  MA200 hold rate at 30d: {hold_rate:.1f}% ({sum(ma200_vals)}/{len(ma200_vals)} still above structural stop)")

    # ── 6. Signal detail table (most recent 20) ───────────────────────────────
    section("6. SIGNAL LOG (most recent 20)")
    print(f"\n  {'Date':<12} {'Ticker':<7} {'Arch':<7} {'Score':>6} {'13F':>5} {'Size':<9} "
          f"{'Entry':>8} {'30d':>8} {'90d':>8} {'Status':<12}")
    print(f"  {'-'*88}")
    ARCH_ABBR = {
        "BASE_ACCUMULATION":  "BASE",
        "TREND_PULLBACK":     "PULL",
        "EARLY_ACCUMULATION": "EARL",
    }
    for s in signals[:20]:
        arch_short = ARCH_ABBR.get(s.get("archetype", ""), s.get("archetype", "")[:4])
        print(f"  {s['logged_at'][:10]:<12} {s['ticker']:<7} {arch_short:<7} "
              f"{s['final_score']:>6} {(s.get('thirteen_f_pts') or 0):>+5} "
              f"{(s.get('size_bucket') or '?'):<9} "
              f"{s['entry_price']:>8.2f} "
              f"{_pct(s.get('outcome_30d')):>8} {_pct(s.get('outcome_90d')):>8} "
              f"{(s.get('signal_status') or 'open'):<12}")

    # ── 7. Paper-valid gate checklist ─────────────────────────────────────────
    section("7. PAPER-VALID GATE CHECKLIST")
    print(f"\n  Pass criteria for paper → live promotion:")

    r30_vals = [s.get("outcome_30d") for s in signals if s.get("outcome_30d") is not None]
    r90_vals = [s.get("outcome_90d") for s in signals if s.get("outcome_90d") is not None]
    avg_30d = _avg(r30_vals) or 0.0
    wr90    = _win_rate(r90_vals) or 0.0
    avg_13f = avg_pts
    ma200_rate = (sum(ma200_vals) / len(ma200_vals) * 100) if ma200_vals else 0.0

    gates_passed = 0
    gates_total  = 7

    def gate_line(label: str, val: float, threshold: float, direction: str = "ge", fmt: str = ".1f") -> bool:
        nonlocal gates_passed
        ok = (val >= threshold) if direction == "ge" else (val > threshold)
        status = "PASS" if ok else "FAIL"
        if ok:
            gates_passed += 1
        print(f"  [{status}] {label}")
        return ok

    # Gate 1
    ok1 = n_total >= GATE_MIN_SIGNALS
    if ok1: gates_passed += 1
    print(f"  [{'PASS' if ok1 else 'FAIL'}] ≥{GATE_MIN_SIGNALS} signals collected:       {n_total}/{GATE_MIN_SIGNALS}")

    # Gate 2
    ok2 = n_window_closed >= GATE_MIN_MEASURED_30D
    if ok2: gates_passed += 1
    print(f"  [{'PASS' if ok2 else 'FAIL'}] ≥{GATE_MIN_MEASURED_30D} signals with 30d window closed:  {n_window_closed}/{GATE_MIN_MEASURED_30D}")

    # Gate 3 (needs data)
    if r30_vals:
        ok3 = avg_30d > GATE_30D_AVG_RETURN
        if ok3: gates_passed += 1
        print(f"  [{'PASS' if ok3 else 'FAIL'}] 30d avg return > 0%:             {avg_30d:+.1f}% (n={len(r30_vals)})")
    else:
        gates_total -= 1
        print(f"  [----] 30d avg return > 0%:             no data yet")

    # Gate 4 (MA200 hold rate)
    if ma200_vals:
        ok4 = ma200_rate >= GATE_MA200_HOLD_RATE
        if ok4: gates_passed += 1
        print(f"  [{'PASS' if ok4 else 'FAIL'}] MA200 hold rate ≥ {GATE_MA200_HOLD_RATE:.0f}% at 30d:    {ma200_rate:.1f}% (n={len(ma200_vals)})")
    else:
        gates_total -= 1
        print(f"  [----] MA200 hold rate ≥ {GATE_MA200_HOLD_RATE:.0f}% at 30d:    no data yet")

    # Gate 5 (90d win rate)
    if len(r90_vals) >= 10:
        ok5 = wr90 >= GATE_90D_WIN_RATE
        if ok5: gates_passed += 1
        print(f"  [{'PASS' if ok5 else 'FAIL'}] 90d win rate ≥ {GATE_90D_WIN_RATE:.0f}%:              {wr90:.1f}% (n={len(r90_vals)})")
    else:
        gates_total -= 1
        print(f"  [----] 90d win rate ≥ {GATE_90D_WIN_RATE:.0f}%:              need ≥10 outcomes (have {len(r90_vals)})")

    # Gate 6 (no archetype catastrophically failing)
    arch_90d: Dict[str, List[float]] = defaultdict(list)
    for s in signals:
        if s.get("outcome_90d") is not None:
            arch_90d[s.get("archetype", "unknown")].append(s["outcome_90d"])
    arch_failing = [(a, _win_rate(v)) for a, v in arch_90d.items()
                    if v and len(v) >= 3 and (_win_rate(v) or 100) < GATE_ARCH_MIN_WIN_RATE]
    if arch_90d:
        ok6 = len(arch_failing) == 0
        if ok6: gates_passed += 1
        status6 = "PASS" if ok6 else "FAIL"
        failing_str = ", ".join(f"{a}={r:.0f}%" for a, r in arch_failing) if arch_failing else "none"
        print(f"  [{status6}] No archetype with 90d WR < {GATE_ARCH_MIN_WIN_RATE:.0f}%:  failing={failing_str}")
    else:
        gates_total -= 1
        print(f"  [----] No archetype with 90d WR < {GATE_ARCH_MIN_WIN_RATE:.0f}%:  no outcomes yet")

    # Gate 7 (13F avg pts)
    ok7 = avg_pts >= GATE_13F_MIN_AVG_PTS
    if ok7: gates_passed += 1
    print(f"  [{'PASS' if ok7 else 'FAIL'}] 13F avg pts ≥ {GATE_13F_MIN_AVG_PTS:.0f}:                {avg_pts:.2f}")

    # Overall verdict
    print(f"\n  {'─'*50}")
    print(f"  Gates passed: {gates_passed}/{gates_total}")
    if gates_total > 0 and gates_passed == gates_total:
        print(f"\n  PAPER-VALID GATE: PASS")
        print(f"  Voyager is ready for capital-deployment review.")
    elif n_total < GATE_MIN_SIGNALS:
        remaining = GATE_MIN_SIGNALS - n_total
        print(f"\n  PAPER-VALID GATE: NOT MET")
        print(f"  Need {remaining} more signals to close the collection gate.")
        print(f"  Run paper cycle until 30 signals are logged.")
    else:
        print(f"\n  PAPER-VALID GATE: NOT MET  ({gates_passed}/{gates_total} passed)")
        print(f"  Continue monitoring. Recheck once more 30d windows close.")


def main():
    parser = argparse.ArgumentParser(description="Voyager paper validation report")
    parser.add_argument(
        "--update-outcomes", action="store_true",
        help="Fetch current prices from Alpaca and fill in elapsed outcome windows",
    )
    args = parser.parse_args()

    try:
        import core.config as cfg
        db_path = cfg.DB_PATH
    except Exception:
        db_path = Path(__file__).resolve().parents[2] / "db" / "trading.db"

    signals = load_signals(db_path)

    if args.update_outcomes:
        print(f"Fetching outcome updates for {len(signals)} signals...")
        n_updated = update_outcomes(signals, db_path)
        print(f"Updated {n_updated} outcomes.")
        # Reload after updates
        signals = load_signals(db_path)

    print_report(signals)


if __name__ == "__main__":
    main()

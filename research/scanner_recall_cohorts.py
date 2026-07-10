#!/usr/bin/env python3
"""Scanner recall cohorts — prospective forward accrual (P0 completion).

The recall diagnostics (research/scanner_recall_diagnostics.py) replay
strict vs simple-RS vs loose cohorts at HISTORICAL as-of dates, which
inherits the shallow-price-cache fidelity caveat (old as-of dates
exclude most of the universe as stale).  This module closes the P0's
remaining gap: it registers the same cohorts PROSPECTIVELY each night —
at full data fidelity, selection strictly from bars ≤ today — and lets
their 5/10/20d forward returns accrue with time, exactly like the
watchlist forward tracker.

Cohorts registered per date (same definitions as the diagnostics):
  strict_mirror     — liquidity gate AND (production_structural OR
                      production_breakout) mirror gates.
  scanner_watchlist — what the REAL production scanner actually surfaced
                      (research_scanner_latest.json watchlist).
  rs_baseline       — top-N liquid names by 20d return vs SPY with
                      positive 40d momentum (simple control).
  loose_scanner     — research-loosened gate variant (superset of
                      strict_mirror by construction).
  random_control    — size-matched random liquid sample, seeded by date.

Doctrine:
  - RESEARCH-ONLY / CACHE-ONLY.  No provider calls, no DB writes, no
    signals.  NOTHING here changes a production threshold, filter, gate,
    ranking, or score.  A looser configuration may only be PROPOSED for
    human review after the pre-registered gates below pass — and even
    then this module only prints the proposal condition, it never
    applies anything.
  - PRE-REGISTERED GATES (fixed before accrual — do not tune):
      coverage: ≥ MIN_MATURED_DATES distinct matured registration dates
                AND ≥ MIN_TICKER_DAYS matured loose ticker-days at 20d.
      proposal: loose 20d winner-recall ≥ RECALL_FACTOR × strict AND
                loose mean 20d excess vs SPY > 0 AND ≥ BEAT_RANDOM_PCT
                of matured dates where loose beats the random control's
                mean 20d excess AND loose mean 20d excess ≥ strict's.
    Verdicts: NEED_MORE_DATA / LOOSE_NOT_BETTER /
              LOOSE_PROMISING_PROPOSAL_ALLOWED.
  - Ledger is append-only and idempotent per date
    (data/research/scanner_recall_cohorts_history.jsonl).

Usage:
  ./scripts/run_research_cycle.sh scanner-recall-cohorts            # register + report
  ./scripts/run_research_cycle.sh scanner-recall-cohorts --report-only
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

LEDGER_REL = (Path("data") / "research"
              / "scanner_recall_cohorts_history.jsonl")
SIDECAR_REL = (Path("cache") / "research"
               / "scanner_recall_cohorts_latest.json")
LOG_REL = Path("logs") / "scanner_recall_cohorts_latest.txt"
SCANNER_REL = Path("cache") / "research" / "research_scanner_latest.json"

HORIZONS = (5, 10, 20)
WINNER_FWD20 = 0.10        # +10% at 20d = forward winner (matches diagnostics)
RS_CAP = 25
RANDOM_SEED_BASE = 42

# ── pre-registered decision gates (do not tune post-hoc) ─────────────────────
MIN_MATURED_DATES = 15
MIN_TICKER_DAYS = 300      # matured loose ticker-days at 20d
RECALL_FACTOR = 2.0        # loose recall must be ≥ 2x strict recall
BEAT_RANDOM_PCT = 60.0     # % of matured dates loose must beat random

COHORT_NAMES = ("strict_mirror", "scanner_watchlist", "rs_baseline",
                "loose_scanner", "random_control")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── registration (selection uses bars ≤ as-of only) ──────────────────────────


def build_registration_row(world: Dict[str, Dict[str, Any]],
                           scanner_watchlist: List[str],
                           asof_date: str) -> Dict[str, Any]:
    """One ledger row from a scanned world snapshot.  ``world`` maps
    ticker -> {strict_pass, loose_pass, rs20, r40} for every liquid,
    fresh, sane ticker as of ``asof_date`` (no forward data involved)."""
    tickers = sorted(t for t in world if not t.startswith("__"))
    strict = sorted(t for t in tickers if world[t].get("strict_pass"))
    loose = sorted(t for t in tickers if world[t].get("loose_pass"))
    rs_rows = [(world[t].get("rs20"), t) for t in tickers
               if world[t].get("rs20") is not None
               and (world[t].get("r40") or 0) > 0]
    rs_rows.sort(key=lambda x: -x[0])
    rs_base = sorted(t for _, t in rs_rows[:RS_CAP])
    watch = sorted(set(scanner_watchlist) & set(tickers))
    rng = random.Random(f"{RANDOM_SEED_BASE}|{asof_date}")
    sample_n = min(len(tickers), max(len(loose), RS_CAP))
    rand = sorted(rng.sample(tickers, sample_n)) if tickers else []
    meta = world.get("__meta__") or {}
    return {
        "date": asof_date,
        "registered_at": _utcnow(),
        "n_liquid": len(tickers),
        "liquid": tickers,
        "cohorts": {
            "strict_mirror": strict,
            "scanner_watchlist": watch,
            "rs_baseline": rs_base,
            "loose_scanner": loose,
            "random_control": rand,
        },
        "meta": {
            "excluded_stale": meta.get("excluded_stale"),
            "excluded_data_suspect": meta.get("excluded_data_suspect"),
            "research_only": True,
        },
    }


def _scan_today() -> Optional[Dict[str, Any]]:
    """Gate + RS snapshot of the liquid universe as of the latest SPY bar.
    Selection-only: no forward returns exist yet by construction."""
    import pandas as pd
    from research.scanner_truth import dataio
    from research.scanner_truth.filters import (
        liquidity_gate, sniper_breakout, voyager_structural)
    from research.scanner_recall_diagnostics import (
        MAX_SANE_R20, loose_sniper, loose_voyager)

    spy = dataio.load_prices("SPY")
    if spy is None or spy.empty:
        return None
    asof = spy.index.max()
    spy_c = spy["close"]
    spy20 = (float(spy_c.iloc[-1] / spy_c.iloc[-21] - 1.0)
             if len(spy_c) >= 21 else None)

    world: Dict[str, Any] = {"__meta__": {"excluded_stale": 0,
                                          "excluded_data_suspect": 0}}
    for t in dataio.all_price_tickers():
        if t in dataio.BENCHMARKS:
            continue
        df = dataio.load_prices(t)
        if df is None or df.empty or len(df) < 60:
            continue
        if not liquidity_gate(df, asof).passed:
            continue
        if df.index.max() < asof:
            world["__meta__"]["excluded_stale"] += 1
            continue
        c = df["close"]
        r20 = (float(c.iloc[-1] / c.iloc[-21] - 1.0)
               if len(c) >= 21 and c.iloc[-21] > 0 else None)
        if r20 is not None and r20 > MAX_SANE_R20:
            world["__meta__"]["excluded_data_suspect"] += 1
            continue
        r40 = (float(c.iloc[-1] / c.iloc[-41] - 1.0)
               if len(c) >= 41 and c.iloc[-41] > 0 else None)
        world[t] = {
            "strict_pass": (voyager_structural(df, asof).passed
                            or sniper_breakout(df, asof).passed),
            "loose_pass": (loose_voyager(df, asof).passed
                           or loose_sniper(df, asof).passed),
            "rs20": ((r20 - spy20)
                     if (r20 is not None and spy20 is not None) else None),
            "r40": r40,
        }
    world["__asof__"] = str(asof)[:10]
    return world


# ── ledger IO ────────────────────────────────────────────────────────────────


def load_ledger(root: Optional[Path] = None) -> List[Dict[str, Any]]:
    root = Path(root) if root else REPO_ROOT
    path = root / LEDGER_REL
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict) and row.get("date"):
            rows.append(row)
    return rows


def append_registration(row: Dict[str, Any],
                        root: Optional[Path] = None) -> bool:
    """Append one registration row; idempotent per date (re-runs no-op)."""
    root = Path(root) if root else REPO_ROOT
    existing = {r.get("date") for r in load_ledger(root)}
    if row["date"] in existing:
        return False
    path = root / LEDGER_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, separators=(",", ":")) + "\n")
    return True


# ── forward resolution + report (cache-only) ─────────────────────────────────


def default_price_loader(root: Path) -> Callable[[str], Optional[Any]]:
    import pandas as pd

    def _load(ticker: str):
        path = root / "cache" / "prices" / f"{ticker}.parquet"
        if not path.exists():
            return None
        try:
            return pd.read_parquet(path)["close"]
        except Exception:
            return None
    return _load


def _fwd(closes, date: str, horizon: int) -> Optional[float]:
    if closes is None or not len(closes):
        return None
    import pandas as pd
    idx = closes.index.searchsorted(pd.Timestamp(date))
    # registration is AT the as-of bar, so idx must land on it exactly
    if idx >= len(closes) or str(closes.index[idx])[:10] != date:
        return None
    if idx + horizon >= len(closes):
        return None
    try:
        return float((closes.iloc[idx + horizon]
                      / closes.iloc[idx] - 1.0) * 100.0)
    except Exception:
        return None


def build_report(rows: List[Dict[str, Any]],
                 price_loader: Callable[[str], Optional[Any]],
                 ) -> Dict[str, Any]:
    """Aggregate matured forward evidence per cohort across all
    registered dates, plus the pre-registered verdict."""
    cache: Dict[str, Any] = {}

    def closes(t: str):
        if t not in cache:
            cache[t] = price_loader(t)
        return cache[t]

    per_date: List[Dict[str, Any]] = []
    agg: Dict[str, Dict[str, Any]] = {
        name: {h: {"rets": [], "ex": []} for h in HORIZONS}
        for name in COHORT_NAMES}
    recall: Dict[str, Dict[str, int]] = {
        name: {"winners_caught": 0, "n": 0} for name in COHORT_NAMES}
    total_winners = 0

    for row in rows:
        date = row["date"]
        spy = closes("SPY")
        spy_fwd = {h: _fwd(spy, date, h) for h in HORIZONS}
        if spy_fwd[20] is None:
            continue  # date not 20d-matured yet — accruing
        liquid = row.get("liquid") or []
        fwd20 = {t: _fwd(closes(t), date, 20) for t in liquid}
        winners = {t for t, v in fwd20.items()
                   if v is not None and v >= WINNER_FWD20 * 100}
        total_winners += len(winners)
        drec: Dict[str, Any] = {"date": date, "n_winners": len(winners)}
        for name in COHORT_NAMES:
            members = row.get("cohorts", {}).get(name) or []
            recall[name]["winners_caught"] += len(set(members) & winners)
            recall[name]["n"] += len(members)
            ex20 = []
            for t in members:
                for h in HORIZONS:
                    r = _fwd(closes(t), date, h)
                    if r is None or spy_fwd[h] is None:
                        continue
                    agg[name][h]["rets"].append(r)
                    agg[name][h]["ex"].append(r - spy_fwd[h])
                    if h == 20:
                        ex20.append(r - spy_fwd[h])
            drec[name] = {
                "n": len(members),
                "mean_ex20": round(sum(ex20) / len(ex20), 2)
                if ex20 else None}
        per_date.append(drec)

    cohorts_out: Dict[str, Any] = {}
    for name in COHORT_NAMES:
        horizons = {}
        for h in HORIZONS:
            rets, ex = agg[name][h]["rets"], agg[name][h]["ex"]
            if not rets:
                continue
            horizons[f"{h}d"] = {
                "n": len(rets),
                "mean_pct": round(sum(rets) / len(rets), 2),
                "median_pct": round(statistics.median(rets), 2),
                "win_rate_pct":
                    round(sum(1 for r in rets if r > 0) / len(rets) * 100, 1),
                "mean_excess_vs_spy_pct":
                    round(sum(ex) / len(ex), 2) if ex else None,
                "median_excess_vs_spy_pct":
                    round(statistics.median(ex), 2) if ex else None,
            }
        rc = recall[name]
        cohorts_out[name] = {
            "matured_ticker_days_20d":
                len(agg[name][20]["rets"]),
            "winner_recall_pct":
                round(rc["winners_caught"] / total_winners * 100, 1)
                if total_winners else None,
            "winner_precision_pct":
                round(rc["winners_caught"] / rc["n"] * 100, 1)
                if rc["n"] else None,
            "horizons": horizons,
        }

    # dates where loose beats random on mean 20d excess
    both = [d for d in per_date
            if (d.get("loose_scanner") or {}).get("mean_ex20") is not None
            and (d.get("random_control") or {}).get("mean_ex20") is not None]
    beat_random = sum(
        1 for d in both
        if d["loose_scanner"]["mean_ex20"]
        > d["random_control"]["mean_ex20"])
    beat_random_pct = round(beat_random / len(both) * 100, 1) if both else None

    verdict, reason = _verdict(cohorts_out, len(per_date), beat_random_pct)
    return {
        "kind": "scanner_recall_cohorts",
        "version": 1,
        "generated_at": _utcnow(),
        "research_only": True,
        "guardrails": {"no_gate_change": True,
                       "proposal_requires_human_review": True,
                       "pre_registered_gates": True},
        "registered_dates": len(rows),
        "matured_dates": len(per_date),
        "gates": {
            "min_matured_dates": MIN_MATURED_DATES,
            "min_ticker_days": MIN_TICKER_DAYS,
            "recall_factor": RECALL_FACTOR,
            "beat_random_pct": BEAT_RANDOM_PCT,
        },
        "loose_beats_random_pct_of_dates": beat_random_pct,
        "cohorts": cohorts_out,
        "per_date": per_date,
        "verdict": verdict,
        "verdict_reason": reason,
    }


def _verdict(cohorts: Dict[str, Any], matured_dates: int,
             beat_random_pct: Optional[float]):
    loose = cohorts.get("loose_scanner") or {}
    strict = cohorts.get("strict_mirror") or {}
    n_days = loose.get("matured_ticker_days_20d") or 0
    if matured_dates < MIN_MATURED_DATES or n_days < MIN_TICKER_DAYS:
        return ("NEED_MORE_DATA",
                f"matured dates {matured_dates}/{MIN_MATURED_DATES}, "
                f"loose 20d ticker-days {n_days}/{MIN_TICKER_DAYS} — "
                "keep accruing; no proposal possible yet")
    l_recall = loose.get("winner_recall_pct") or 0.0
    s_recall = strict.get("winner_recall_pct") or 0.0
    l_ex = ((loose.get("horizons") or {}).get("20d")
            or {}).get("mean_excess_vs_spy_pct")
    s_ex = ((strict.get("horizons") or {}).get("20d")
            or {}).get("mean_excess_vs_spy_pct")
    checks = [
        ("recall", l_recall >= RECALL_FACTOR * max(s_recall, 0.1)),
        ("excess_positive", l_ex is not None and l_ex > 0),
        ("beats_random", (beat_random_pct or 0) >= BEAT_RANDOM_PCT),
        ("excess_ge_strict", l_ex is not None
         and (s_ex is None or l_ex >= s_ex)),
    ]
    failed = [name for name, ok in checks if not ok]
    if not failed:
        return ("LOOSE_PROMISING_PROPOSAL_ALLOWED",
                "all pre-registered gates pass — a loosening PROPOSAL may "
                "be written for human review (nothing is auto-applied)")
    return ("LOOSE_NOT_BETTER",
            f"coverage met but gates failed: {', '.join(failed)} — "
            "production gates stay unchanged")


# ── rendering + CLI ──────────────────────────────────────────────────────────


def render_text(report: Dict[str, Any]) -> str:
    lines = ["SCANNER RECALL COHORTS (prospective accrual — research-only, "
             "no gate changes)",
             f"  generated {report['generated_at'][:16]} | registered dates "
             f"{report['registered_dates']} | matured (20d) "
             f"{report['matured_dates']}",
             f"  VERDICT: {report['verdict']} — {report['verdict_reason']}"]
    for name, blk in report["cohorts"].items():
        lines.append(
            f"  {name:<18} recall={blk['winner_recall_pct']}% "
            f"precision={blk['winner_precision_pct']}% "
            f"ticker-days(20d)={blk['matured_ticker_days_20d']}")
        for h, st in (blk.get("horizons") or {}).items():
            lines.append(
                f"      {h:>3}: n={st['n']:4d} mean {st['mean_pct']:+.2f}% "
                f"med {st['median_pct']:+.2f}% win {st['win_rate_pct']}% "
                f"exSPY {st['mean_excess_vs_spy_pct']:+.2f}%")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Prospective scanner-recall cohort accrual "
                    "(research-only; cache-only; no gate changes).")
    parser.add_argument("--report-only", action="store_true",
                        help="skip registration; aggregate what exists")
    parser.add_argument("--dry-run", action="store_true",
                        help="print only; write nothing")
    parser.add_argument("--root", default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    root = Path(args.root) if args.root else REPO_ROOT

    if not args.report_only:
        world = _scan_today()
        if world is None:
            print("no SPY parquet — cannot register cohorts")
            return 1
        asof = world.pop("__asof__")
        watchlist: List[str] = []
        scanner_path = root / SCANNER_REL
        if scanner_path.exists():
            try:
                scanner = json.loads(scanner_path.read_text(encoding="utf-8"))
                watchlist = [i.get("ticker") for i in
                             scanner.get("watchlist") or [] if i.get("ticker")]
            except Exception:
                watchlist = []
        row = build_registration_row(world, watchlist, asof)
        if args.dry_run:
            print(f"[dry-run] would register {row['date']}: "
                  + ", ".join(f"{k}={len(v)}"
                              for k, v in row["cohorts"].items()))
        else:
            appended = append_registration(row, root)
            print(f"registration {row['date']}: "
                  + ("appended" if appended else "already present (no-op)")
                  + " — " + ", ".join(f"{k}={len(v)}"
                                      for k, v in row["cohorts"].items()))

    rows = load_ledger(root)
    report = build_report(rows, default_price_loader(root))
    print(render_text(report))
    if not args.dry_run:
        sidecar = root / SIDECAR_REL
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(json.dumps(report, ensure_ascii=False, indent=2)
                           + "\n", encoding="utf-8")
        log = root / LOG_REL
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(render_text(report) + "\n", encoding="utf-8")
        print(f"\nwritten: {sidecar}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""High-Conviction Alpha Shortlist — separate forward-validation tracking.

The shortlist is tracked as its OWN research hypothesis, never merged into
the general program-candidate episode ledger.  This module:

  1. historizes each day's shortlist members (append-only, idempotent by
     ticker+date+version) to data/research/high_conviction_history.jsonl;
  2. resolves matured forward returns from the cached price parquets at
     pre-registered horizons and computes excess vs SPY / QQQ / IWM / the
     sector ETF;
  3. aggregates by classification (HIGH_CONVICTION / PROMISING /
     WATCH_FOR_ENTRY) and by rank tier (top 3 / top 5 / full), and compares
     the shortlist against the broader program-candidate baseline;
  4. emits a PRE-REGISTERED verdict — the module must PROVE that stronger
     quality-growth-value-momentum filtering improves outcomes; it does not
     assume it does.  Immature evidence stays NEED_MORE_DATA.

DOCTRINE: RESEARCH-ONLY / CACHE-ONLY / CRED-FREE.  The only write is the
append-only history ledger + the validation sidecar/log.  No scanner,
routing, score, gate, or program-verdict change.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.high_conviction_alpha import VERSION, SHORTLIST_CLASSES  # noqa: E402

HISTORY_REL = (Path("data") / "research"
               / "high_conviction_history.jsonl")
SIDECAR_REL = (Path("cache") / "research"
               / "high_conviction_forward_latest.json")
LOG_REL = Path("logs") / "high_conviction_forward_latest.txt"
PRICE_DIR = Path("cache") / "prices"

# Pre-registered evaluation horizons (trading days).  6m≈126td, 12m≈252td.
HORIZONS = (5, 10, 15, 30, 45, 60, 90, 126, 252)
BENCHMARKS = ("SPY", "QQQ", "IWM")

# Sector ETF map mirrors the forward tracker (single source would be ideal
# but that module pulls pandas at import; this copy keeps us cred/dep-light).
SECTOR_ETF_MAP: Dict[str, str] = {
    "Technology": "XLK", "Communication Services": "XLC",
    "Consumer Discretionary": "XLY", "Consumer Staples": "XLP",
    "Health Care": "XLV", "Healthcare": "XLV", "Financials": "XLF",
    "Financial Services": "XLF", "Industrials": "XLI", "Materials": "XLB",
    "Energy": "XLE", "Utilities": "XLU", "Real Estate": "XLRE",
}

# Verdict ladder (pre-registered).
V_NEED_MORE_DATA = "NEED_MORE_DATA"
V_IMPROVES = "SHORTLIST_IMPROVES_OUTCOMES"
V_NO_IMPROVEMENT = "NO_IMPROVEMENT_OVER_PROGRAM_CANDIDATES"
MIN_MATURED_FOR_VERDICT = 10   # matured shortlist episodes at the 10d anchor


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _sector_etf(sector: Optional[str], industry: Optional[str]) -> Optional[str]:
    if industry and "semiconductor" in str(industry).lower():
        return "SMH"
    return SECTOR_ETF_MAP.get(sector or "")


def _load_closes(sym: str, root: Path) -> List[Tuple[str, float]]:
    path = root / PRICE_DIR / f"{sym.upper()}.parquet"
    if not path.exists():
        return []
    try:
        import pandas as pd
        df = pd.read_parquet(path)
        close_col = next((c for c in ("close", "Close") if c in df.columns),
                         None)
        if not close_col:
            return []
        date_col = next((c for c in ("date", "Date", "timestamp",
                                     "Timestamp") if c in df.columns), None)
        dates = (df[date_col].astype(str).str[:10] if date_col
                 else df.index.astype(str).str[:10])
        pairs = list(zip(dates, df[close_col].astype(float)))
        return sorted(pairs, key=lambda x: x[0])
    except Exception:
        return []


def _forward_return(closes: List[Tuple[str, float]], date: str,
                    horizon: int) -> Optional[float]:
    if not closes:
        return None
    idx = next((i for i, (d, _) in enumerate(closes) if d >= date), None)
    if idx is None or idx + horizon >= len(closes):
        return None
    entry = closes[idx][1]
    if not entry:
        return None
    return round((closes[idx + horizon][1] / entry - 1.0) * 100.0, 2)


# ── historization (append-only, idempotent) ─────────────────────────────────


def historize_shortlist(payload: Dict[str, Any],
                        root: Optional[Path] = None,
                        date_override: Optional[str] = None) -> int:
    """Append today's shortlist members to the separate history ledger.
    Idempotent by (ticker, date, version): re-runs never duplicate.  Returns
    the number of rows newly written."""
    root = Path(root) if root else REPO_ROOT
    if not payload or not payload.get("present"):
        return 0
    date = date_override or str(payload.get("generated_at") or
                                _utcnow().isoformat())[:10]
    path = root / HISTORY_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
                existing.add((rec.get("ticker"), rec.get("appearance_date"),
                              rec.get("version")))
            except Exception:
                continue
    rows = []
    for rank, s in enumerate(payload.get("shortlist") or [], 1):
        key = (s.get("ticker"), date, VERSION)
        if key in existing:
            continue
        rows.append({
            "ticker": s.get("ticker"),
            "appearance_date": date,
            "version": VERSION,
            "program": s.get("program"),
            "classification": s.get("classification"),
            "high_conviction_score": s.get("high_conviction_score"),
            "shortlist_rank": rank,
            "sector": s.get("sector"),
            "industry": s.get("industry"),
            "research_only": True,
        })
    if rows:
        with path.open("a", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(rows)


def _load_history(root: Path) -> List[Dict[str, Any]]:
    path = root / HISTORY_REL
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


# ── resolution + aggregation ────────────────────────────────────────────────


def _resolve_entry(entry: Dict[str, Any], root: Path,
                   bench_cache: Dict[str, List[Tuple[str, float]]]
                   ) -> Dict[str, Any]:
    ticker = str(entry.get("ticker") or "")
    date = str(entry.get("appearance_date") or "")
    closes = _load_closes(ticker, root)
    resolved = dict(entry)
    sec_etf = _sector_etf(entry.get("sector"), entry.get("industry"))
    for h in HORIZONS:
        r = _forward_return(closes, date, h)
        resolved[f"ret_{h}d"] = r
        for bench in BENCHMARKS:
            if bench not in bench_cache:
                bench_cache[bench] = _load_closes(bench, root)
            br = _forward_return(bench_cache[bench], date, h)
            resolved[f"ret_{h}d_vs_{bench.lower()}"] = (
                round(r - br, 2) if r is not None and br is not None else None)
        if sec_etf:
            if sec_etf not in bench_cache:
                bench_cache[sec_etf] = _load_closes(sec_etf, root)
            sr = _forward_return(bench_cache[sec_etf], date, h)
            resolved[f"ret_{h}d_vs_sector"] = (
                round(r - sr, 2) if r is not None and sr is not None else None)
        else:
            resolved[f"ret_{h}d_vs_sector"] = None
    return resolved


def _stats(entries: List[Dict[str, Any]], field: str) -> Dict[str, Any]:
    vals = [e[field] for e in entries if e.get(field) is not None]
    if not vals:
        return {"n": 0, "mean": None, "median": None, "win_rate": None}
    return {
        "n": len(vals),
        "mean": round(sum(vals) / len(vals), 2),
        "median": round(statistics.median(vals), 2),
        "win_rate": round(100.0 * sum(1 for v in vals if v > 0) / len(vals), 1),
    }


def _cohort_summary(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"n_episodes": len(entries)}
    for h in HORIZONS:
        out[f"{h}d"] = {
            "raw": _stats(entries, f"ret_{h}d"),
            "vs_spy": _stats(entries, f"ret_{h}d_vs_spy"),
            "vs_qqq": _stats(entries, f"ret_{h}d_vs_qqq"),
            "vs_iwm": _stats(entries, f"ret_{h}d_vs_iwm"),
            "vs_sector": _stats(entries, f"ret_{h}d_vs_sector"),
        }
    return out


def _program_baseline(root: Path) -> Optional[Dict[str, Any]]:
    """Matured stats for the broader program-candidate ledger, used only as
    a comparison baseline (never merged into the shortlist cohort)."""
    path = root / "data" / "research" / "research_watchlist_history.jsonl"
    if not path.exists():
        return None
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    if not rows:
        return None
    return {
        "n_episodes": len(rows),
        "10d": {"raw": _stats(rows, "ret_10d"),
                "vs_spy": _stats(rows, "ret_10d_vs_spy")},
    }


def build_forward(root: Optional[Path] = None,
                  now: Optional[datetime] = None) -> Dict[str, Any]:
    root = Path(root) if root else REPO_ROOT
    now = now or _utcnow()
    history = _load_history(root)
    base = {
        "kind": "high_conviction_forward",
        "version": VERSION,
        "generated_at": now.isoformat(),
        "research_only": True,
        "horizons_td": list(HORIZONS),
        "benchmarks": list(BENCHMARKS) + ["SECTOR_ETF"],
        "separate_hypothesis": True,
        "note": "Shortlist tracked as its own hypothesis — never merged "
                "into the general program-candidate episode ledger.",
    }
    if not history:
        return {**base, "present": False, "verdict": V_NEED_MORE_DATA,
                "verdict_reason": "no shortlist history recorded yet"}

    bench_cache: Dict[str, List[Tuple[str, float]]] = {}
    resolved = [_resolve_entry(e, root, bench_cache) for e in history]

    # rank tiers (per appearance date)
    by_date: Dict[str, List[Dict[str, Any]]] = {}
    for e in resolved:
        by_date.setdefault(str(e.get("appearance_date")), []).append(e)
    top3, top5 = [], []
    for date, entries in by_date.items():
        ranked = sorted(entries,
                        key=lambda x: x.get("shortlist_rank") or 999)
        top3.extend(ranked[:3])
        top5.extend(ranked[:5])

    cohorts = {
        "full_shortlist": _cohort_summary(resolved),
        "top3": _cohort_summary(top3),
        "top5": _cohort_summary(top5),
    }
    for cls in SHORTLIST_CLASSES:
        cohorts[cls.lower()] = _cohort_summary(
            [e for e in resolved if e.get("classification") == cls])

    baseline = _program_baseline(root)

    # verdict: needs enough matured 10d shortlist episodes AND a positive
    # excess vs SPY that beats the program-candidate baseline.
    n_matured = cohorts["full_shortlist"]["10d"]["vs_spy"]["n"]
    verdict, reason = V_NEED_MORE_DATA, (
        f"only {n_matured} matured 10d shortlist episodes "
        f"(need ≥{MIN_MATURED_FOR_VERDICT})")
    if n_matured >= MIN_MATURED_FOR_VERDICT:
        sl_excess = cohorts["full_shortlist"]["10d"]["vs_spy"]["mean"]
        base_excess = (baseline or {}).get("10d", {}).get(
            "vs_spy", {}).get("mean") if baseline else None
        if sl_excess is not None and sl_excess > 0 and (
                base_excess is None or sl_excess > base_excess):
            verdict, reason = V_IMPROVES, (
                f"shortlist 10d excess vs SPY {sl_excess:+.2f}% beats "
                f"program baseline {base_excess}")
        else:
            verdict, reason = V_NO_IMPROVEMENT, (
                f"shortlist 10d excess vs SPY {sl_excess} does not beat "
                f"baseline {base_excess}")

    return {
        **base, "present": True,
        "n_history_rows": len(history),
        "n_distinct_dates": len(by_date),
        "cohorts": cohorts,
        "program_candidate_baseline": baseline,
        "verdict": verdict,
        "verdict_reason": reason,
    }


def render_terminal(payload: Dict[str, Any]) -> str:
    lines = ["=== HIGH-CONVICTION SHORTLIST — FORWARD VALIDATION ===",
             f"  {VERSION} · separate hypothesis · research-only"]
    if not payload.get("present"):
        lines.append(f"  {payload.get('verdict')}: "
                     f"{payload.get('verdict_reason')}")
        return "\n".join(lines)
    lines.append(f"  history rows {payload['n_history_rows']} across "
                 f"{payload['n_distinct_dates']} dates")
    full = payload["cohorts"]["full_shortlist"]
    for h in (5, 10, 30, 60, 126, 252):
        st = full.get(f"{h}d", {})
        raw = st.get("raw", {})
        spy = st.get("vs_spy", {})
        if raw.get("n"):
            lines.append(f"  {h}d: n={raw['n']} mean={raw['mean']}% "
                         f"win={raw['win_rate']}% vs_spy={spy.get('mean')}%")
    lines.append(f"  VERDICT: {payload['verdict']} — "
                 f"{payload['verdict_reason']}")
    return "\n".join(lines)


def write_outputs(payload: Dict[str, Any],
                  root: Optional[Path] = None) -> Path:
    root = Path(root) if root else REPO_ROOT
    sidecar = root / SIDECAR_REL
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(json.dumps(payload, ensure_ascii=False, indent=2)
                       + "\n", encoding="utf-8")
    log = root / LOG_REL
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(render_terminal(payload) + "\n", encoding="utf-8")
    return sidecar


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="High-Conviction Shortlist forward validation "
                    "(separate hypothesis; cache-only, research-only).")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--root", default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    root = Path(args.root) if args.root else REPO_ROOT
    payload = build_forward(root)
    print(render_terminal(payload))
    if not args.dry_run:
        print(f"\nwritten: {write_outputs(payload, root)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

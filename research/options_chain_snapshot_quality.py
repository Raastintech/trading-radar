#!/usr/bin/env python3
"""
research/options_chain_snapshot_quality.py - Phase 1J.1 snapshot quality audit.

Cache-only audit of the persisted options chain snapshots written by
research/options_chain_snapshot_collector.py. Reads parquet files only; never
calls providers; produces per-symbol usability verdicts for FUTURE
backtesting (once enough history accumulates — this module never pretends
today's snapshots are history).

Outputs:
  - cache/research/options_chain_snapshot_quality_latest.json
  - docs/research/OPTIONS_CHAIN_SNAPSHOT_QUALITY.md
  - logs/options_chain_snapshot_quality_latest.txt
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_ROOT = ROOT / "data" / "options_snapshots"
OUT_JSON = ROOT / "cache" / "research" / "options_chain_snapshot_quality_latest.json"
OUT_DOC = ROOT / "docs" / "research" / "OPTIONS_CHAIN_SNAPSHOT_QUALITY.md"
OUT_TXT = ROOT / "logs" / "options_chain_snapshot_quality_latest.txt"

VERSION = "OPTIONS_CHAIN_SNAPSHOT_QUALITY_V1"

# Pre-registered usability floors for FUTURE backtesting (per symbol/day).
MIN_BID_ASK_COVERAGE = 0.90
MIN_IV_COVERAGE = 0.80
MIN_OI_COVERAGE = 0.80
MAX_MEDIAN_SPREAD_PCT = 0.12
MIN_CONTRACTS = 40


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coverage(frame: pd.DataFrame, col: str) -> Optional[float]:
    if col not in frame.columns or frame.empty:
        return None
    s = pd.to_numeric(frame[col], errors="coerce")
    return round(float((s.notna() & (s != 0)).mean()), 4)


def audit_file(path: Path) -> Dict[str, Any]:
    frame = pd.read_parquet(path)
    n = len(frame)
    bid = pd.to_numeric(frame.get("bid"), errors="coerce")
    ask = pd.to_numeric(frame.get("ask"), errors="coerce")
    mid = pd.to_numeric(frame.get("mid"), errors="coerce")
    has_quote = bid.notna() & ask.notna() & (bid > 0) & (ask > 0)
    spread_pct = ((ask - bid) / mid)[has_quote & mid.notna() & (mid > 0)]
    flags = frame.get("data_quality_flags")
    stale = 0
    if flags is not None:
        stale = int(flags.astype(str).str.contains("stale_quote").sum())
    dtes = sorted(pd.to_numeric(frame.get("dte"), errors="coerce").dropna().unique().tolist())
    return {
        "contracts": n,
        "expirations": sorted(frame.get("expiration").astype(str).unique().tolist()) if n else [],
        "dte_values": [int(d) for d in dtes],
        "bid_ask_coverage": round(float(has_quote.mean()), 4) if n else None,
        "iv_coverage": _coverage(frame, "implied_volatility"),
        "greeks_coverage": _coverage(frame, "delta"),
        "oi_coverage": round(float(pd.to_numeric(frame.get("open_interest"), errors="coerce").notna().mean()), 4) if n else None,
        "median_spread_pct": round(float(spread_pct.median()), 4) if len(spread_pct) else None,
        "p90_spread_pct": round(float(spread_pct.quantile(0.9)), 4) if len(spread_pct) else None,
        "stale_quote_rate": round(stale / n, 4) if n else None,
        "providers": sorted(frame.get("provider").astype(str).unique().tolist()) if n else [],
    }


def usability_verdict(stats: Dict[str, Any]) -> Dict[str, Any]:
    reasons = []
    if (stats.get("contracts") or 0) < MIN_CONTRACTS:
        reasons.append(f"contracts {stats.get('contracts')} < {MIN_CONTRACTS}")
    if (stats.get("bid_ask_coverage") or 0) < MIN_BID_ASK_COVERAGE:
        reasons.append(f"bid/ask coverage {stats.get('bid_ask_coverage')} < {MIN_BID_ASK_COVERAGE}")
    if (stats.get("iv_coverage") or 0) < MIN_IV_COVERAGE:
        reasons.append(f"IV coverage {stats.get('iv_coverage')} < {MIN_IV_COVERAGE}")
    if (stats.get("oi_coverage") or 0) < MIN_OI_COVERAGE:
        reasons.append(f"OI coverage {stats.get('oi_coverage')} < {MIN_OI_COVERAGE}")
    med = stats.get("median_spread_pct")
    if med is not None and med > MAX_MEDIAN_SPREAD_PCT:
        reasons.append(f"median spread {med} > {MAX_MEDIAN_SPREAD_PCT}")
    return {
        "usable_for_future_backtesting": not reasons,
        "reasons": reasons or ["all pre-registered floors met (usable once enough HISTORY accumulates)"],
    }


def build_report() -> Dict[str, Any]:
    days = sorted(p.name for p in SNAPSHOT_ROOT.glob("*") if p.is_dir()) if SNAPSHOT_ROOT.exists() else []
    per_symbol: Dict[str, Dict[str, Any]] = {}
    per_day_counts: Dict[str, int] = {}
    provider_missing: Dict[str, Dict[str, List[float]]] = defaultdict(lambda: defaultdict(list))
    for day in days:
        files = sorted((SNAPSHOT_ROOT / day).glob("*.parquet"))
        per_day_counts[day] = len(files)
        for f in files:
            sym = f.stem.upper()
            stats = audit_file(f)
            row = per_symbol.setdefault(sym, {"days": [], "latest": None})
            row["days"].append(day)
            row["latest"] = stats
            for prov in stats.get("providers") or ["unknown"]:
                for field_name in ("iv_coverage", "greeks_coverage", "oi_coverage", "bid_ask_coverage"):
                    value = stats.get(field_name)
                    if value is not None:
                        provider_missing[prov][field_name].append(value)
    for sym, row in per_symbol.items():
        row["snapshot_days"] = len(row.pop("days"))
        row["verdict"] = usability_verdict(row["latest"] or {})
    missingness = {
        prov: {field_name: round(statistics.mean(vals), 4) for field_name, vals in fields.items()}
        for prov, fields in provider_missing.items()
    }
    total_contracts = sum((row["latest"] or {}).get("contracts") or 0 for row in per_symbol.values())
    return {
        "kind": "options_chain_snapshot_quality",
        "version": VERSION,
        "generated_at": _utc_now(),
        "research_only": True,
        "strategy_status": "DATA_COLLECTION_ONLY",
        "snapshot_days": days,
        "files_per_day": per_day_counts,
        "symbols_collected": sorted(per_symbol),
        "total_contracts_latest_day": total_contracts,
        "floors": {
            "min_bid_ask_coverage": MIN_BID_ASK_COVERAGE,
            "min_iv_coverage": MIN_IV_COVERAGE,
            "min_oi_coverage": MIN_OI_COVERAGE,
            "max_median_spread_pct": MAX_MEDIAN_SPREAD_PCT,
            "min_contracts": MIN_CONTRACTS,
        },
        "per_symbol": per_symbol,
        "avg_coverage_by_provider": missingness,
        "history_note": (
            f"{len(days)} snapshot day(s) retained. Usability verdicts describe per-day data quality only; "
            "backtest feasibility additionally requires the history gates in OPTIONS_CHAIN_COLLECTION_CADENCE.md"
        ),
    }


def render_doc(res: Dict[str, Any]) -> str:
    lines = [
        "# Options Chain Snapshot Quality (Phase 1J.1)",
        "",
        f"Generated: {res['generated_at']}",
        "",
        f"Status: **{res['strategy_status']}** — quality audit of persisted snapshots; no strategy, no signals.",
        "",
        f"Snapshot days retained: {len(res['snapshot_days'])} ({', '.join(res['snapshot_days']) or 'none'}). "
        f"Symbols: {len(res['symbols_collected'])}. Contracts (latest day): {res['total_contracts_latest_day']}.",
        "",
        "| Symbol | Days | Contracts | Expirations | Bid/Ask | IV | Greeks | OI | Med Spread | Stale | Usable (per-day quality) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for sym, row in sorted(res["per_symbol"].items()):
        s = row["latest"] or {}
        v = row["verdict"]
        lines.append(
            f"| {sym} | {row['snapshot_days']} | {s.get('contracts')} | {len(s.get('expirations') or [])} | "
            f"{s.get('bid_ask_coverage')} | {s.get('iv_coverage')} | {s.get('greeks_coverage')} | {s.get('oi_coverage')} | "
            f"{s.get('median_spread_pct')} | {s.get('stale_quote_rate')} | "
            f"{'YES' if v['usable_for_future_backtesting'] else 'NO: ' + '; '.join(v['reasons'])} |"
        )
    lines += [
        "",
        "## Coverage by provider",
        "",
        "```json",
        json.dumps(res["avg_coverage_by_provider"], indent=2),
        "```",
        "",
        res["history_note"] + ".",
        "",
    ]
    return "\n".join(lines)


def render_text(res: Dict[str, Any]) -> List[str]:
    lines = [
        f"OPTIONS SNAPSHOT QUALITY (PHASE 1J.1) - {res['generated_at']}",
        f"days={len(res['snapshot_days'])} symbols={len(res['symbols_collected'])} contracts_latest={res['total_contracts_latest_day']}",
    ]
    for sym, row in sorted(res["per_symbol"].items()):
        s = row["latest"] or {}
        ok = row["verdict"]["usable_for_future_backtesting"]
        lines.append(
            f"  {sym}: n={s.get('contracts')} bidask={s.get('bid_ask_coverage')} iv={s.get('iv_coverage')} "
            f"oi={s.get('oi_coverage')} spread={s.get('median_spread_pct')} usable={'YES' if ok else 'NO'}"
        )
    return lines


def write_outputs(res: Dict[str, Any]) -> None:
    for p in (OUT_JSON, OUT_DOC, OUT_TXT):
        p.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    OUT_DOC.write_text(render_doc(res) + "\n", encoding="utf-8")
    OUT_TXT.write_text("\n".join(render_text(res)) + "\n", encoding="utf-8")


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Phase 1J.1 options snapshot quality audit (cache-only)")
    ap.parse_args(argv)
    res = build_report()
    write_outputs(res)
    print("\n".join(render_text(res)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

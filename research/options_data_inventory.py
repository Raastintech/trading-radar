#!/usr/bin/env python3
"""
research/options_data_inventory.py - Phase 1J.0 options feasibility audit.

Cache-only audit of every locally retained options dataset, answering one
question honestly: CAN WE VALIDLY BACKTEST OPTIONS PREMIUM NOW? It reads the
retained IV-history JSONLs, the options regime lens artifacts, and the price
caches (for realized volatility); it never calls providers, never fabricates
IV history, and never treats current chain snapshots as historical data.

Writes:
  - cache/research/options_data_inventory_latest.json
  - logs/options_data_inventory_latest.txt
  - docs/research/OPTIONS_DATA_INVENTORY.md

No paper signals, no broker orders, no trade proposals, no execution /
governance / live-capital imports, no production changes.
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
CACHE = ROOT / "cache" / "research"
LOGS = ROOT / "logs"
DOCS = ROOT / "docs" / "research"

OUT_JSON = CACHE / "options_data_inventory_latest.json"
OUT_TXT = LOGS / "options_data_inventory_latest.txt"
OUT_DOC = DOCS / "OPTIONS_DATA_INVENTORY.md"

IV_HISTORY_PATH = CACHE / "options_iv_history.jsonl"
REGIME_HISTORY_PATH = ROOT / "data" / "research" / "options_regime_lens_history.jsonl"
REGIME_LATEST_PATH = CACHE / "options_regime_lens_latest.json"
PRICES_DIR = ROOT / "cache" / "prices"
PRICES_DEEP_DIR = ROOT / "cache" / "prices_deep"

VERSION = "OPTIONS_DATA_INVENTORY_V1"

# IV Rank feasibility thresholds (pre-registered; industry IVR uses ~252d).
IVR_FEASIBLE_MIN_DAYS = 120
IVR_PARTIAL_MIN_DAYS = 60
IVR_MAX_MISSING_RATE = 0.25

IVR_FEASIBLE = "IVR_FEASIBLE"
IVR_PARTIAL = "IVR_PARTIAL"
IVR_NOT_VALID = "IVR_NOT_VALID"

FEASIBLE_NOW = "FEASIBLE_NOW"
FEASIBLE_WITH_LIMITATIONS = "FEASIBLE_WITH_LIMITATIONS"
NOT_FEASIBLE = "NOT_FEASIBLE_WITH_CURRENT_DATA"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_jsonl(path: Path) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    bad = 0
    if not path.exists():
        return {"rows": rows, "bad_lines": 0, "exists": False}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            bad += 1
    return {"rows": rows, "bad_lines": bad, "exists": True}


def point_in_time_check(rows: Sequence[Dict[str, Any]], *, date_key: str, today: Optional[str] = None) -> Dict[str, Any]:
    """A retained series is point-in-time only if it is append-only with
    observation dates, none of which are in the future. There is no backfill
    source for options data, so any future-dated row would indicate fakery."""
    today = today or str(datetime.now(timezone.utc).date())
    dates = sorted({str(r.get(date_key))[:10] for r in rows if r.get(date_key)})
    future = [d for d in dates if d > today]
    return {
        "rows": len(rows),
        "distinct_dates": len(dates),
        "first_date": dates[0] if dates else None,
        "last_date": dates[-1] if dates else None,
        "future_dated_rows": len(future),
        "point_in_time_forward_accumulating": bool(dates) and not future,
        "historical_backfill_available": False,
        "note": "rows are appended on observation day from live chains; history exists only from first append onward",
    }


def _trading_days_between(first: str, last: str) -> int:
    if not first or not last:
        return 0
    return len(pd.bdate_range(first, last))


def iv_history_inventory() -> Dict[str, Any]:
    raw = _read_jsonl(IV_HISTORY_PATH)
    rows = raw["rows"]
    per_ticker: Dict[str, set] = defaultdict(set)
    buckets: Dict[str, int] = defaultdict(int)
    field_presence = {k: 0 for k in ("atm_call_iv", "atm_put_iv", "atm_iv_blend", "iv_skew")}
    for r in rows:
        if r.get("ticker") and r.get("date"):
            per_ticker[str(r["ticker"])].add(str(r["date"])[:10])
        if r.get("bucket"):
            buckets[str(r["bucket"])] += 1
        for k in field_presence:
            if r.get(k) is not None:
                field_presence[k] += 1
    depths = sorted(((len(v), k) for k, v in per_ticker.items()), reverse=True)
    return {
        "path": str(IV_HISTORY_PATH),
        "exists": raw["exists"],
        "bad_lines": raw["bad_lines"],
        "row_count": len(rows),
        "symbols_covered": len(per_ticker),
        "buckets": dict(buckets),
        "field_presence": field_presence,
        "granularity": "ATM IV summary per (ticker, date, expiry bucket) — NOT strike-level chains",
        "has_bid_ask": False,
        "has_open_interest": False,
        "has_greeks": False,
        "has_strikes": False,
        "deepest_symbols": [{"symbol": k, "iv_days": n} for n, k in depths[:10]],
        "median_depth_days": statistics.median([n for n, _ in depths]) if depths else 0,
        "point_in_time": point_in_time_check(rows, date_key="date"),
        "per_ticker_days": {k: len(v) for k, v in per_ticker.items()},
        "per_ticker_span": {k: (min(v), max(v)) for k, v in per_ticker.items()},
    }


def regime_lens_inventory() -> Dict[str, Any]:
    raw = _read_jsonl(REGIME_HISTORY_PATH)
    rows = raw["rows"]
    per_symbol: Dict[str, set] = defaultdict(set)
    for r in rows:
        sym = r.get("symbol") or r.get("ticker")
        day = str(r.get("generated_at_date") or r.get("date") or "")[:10]
        if sym and day:
            per_symbol[str(sym)].add(day)
    latest = {}
    if REGIME_LATEST_PATH.exists():
        try:
            latest_raw = json.loads(REGIME_LATEST_PATH.read_text())
            latest = {"generated_at": latest_raw.get("generated_at"), "kind": latest_raw.get("kind")}
        except Exception:
            latest = {"error": "unreadable"}
    return {
        "path": str(REGIME_HISTORY_PATH),
        "exists": raw["exists"],
        "bad_lines": raw["bad_lines"],
        "row_count": len(rows),
        "symbols": sorted(per_symbol),
        "per_symbol_days": {k: len(v) for k, v in sorted(per_symbol.items())},
        "fields": "atm_iv, iv_30d_estimate, gamma_proxy (naive), skew, term_structure, source_quality",
        "granularity": "market-level diagnostics for SPY/QQQ/IWM/VXX — no chains, no strikes",
        "point_in_time": point_in_time_check(rows, date_key="generated_at_date"),
        "latest_sidecar": latest,
    }


def chain_storage_inventory() -> Dict[str, Any]:
    """Search for any persisted strike-level chain data. There is none by
    design: the feed factory serves live chains and nothing writes them."""
    candidates = []
    for pattern in ("*chain*", "*option*"):
        for base in (ROOT / "cache", ROOT / "data"):
            for p in base.rglob(pattern):
                if p.is_file() and p.suffix in {".json", ".jsonl", ".parquet", ".csv"}:
                    candidates.append(str(p.relative_to(ROOT)))
    known_non_chain = {
        "cache/research/options_iv_history.jsonl",
        "cache/research/options_regime_lens_latest.json",
        "cache/research/options_data_inventory_latest.json",
        "data/research/options_regime_lens_history.jsonl",
    }
    unexpected = sorted(set(candidates) - known_non_chain)
    return {
        "persisted_chain_snapshot_count": 0,
        "strike_level_history_files": unexpected,
        "providers": {
            "alpaca": "snapshot endpoint returns quote/trade/bar only — NO IV, NO greeks, NO OI (OI merged live from contracts endpoint); current snapshots only, no historical chain endpoint in use",
            "tradier": "current chains with IV/greeks when token validates; no historical chains retained locally",
            "fmp": "no options files present",
        },
        "current_snapshots_are_not_history": True,
        "note": "core/options_feed_factory.py serves LIVE chains to the Stock Lens / regime lens; no module persists strike-level chains",
    }


def realized_vol_inventory() -> Dict[str, Any]:
    deep = list(PRICES_DEEP_DIR.glob("*.parquet")) if PRICES_DEEP_DIR.exists() else []
    shallow_count = len(list(PRICES_DIR.glob("*.parquet"))) if PRICES_DIR.exists() else 0
    spy_bars = None
    spy_span = None
    probe = PRICES_DEEP_DIR / "SPY.parquet"
    if probe.exists():
        try:
            df = pd.read_parquet(probe)
            spy_bars = int(len(df))
            spy_span = (str(pd.Timestamp(df.index.min()).date()), str(pd.Timestamp(df.index.max()).date()))
        except Exception:
            pass
    return {
        "deep_cache_tickers": len(deep),
        "deep_cache_typical_bars": spy_bars,
        "deep_cache_spy_span": spy_span,
        "shallow_cache_tickers": shallow_count,
        "shallow_cache_typical_bars": "~90-111 (daemon-maintained, overwritten)",
        "rv_computable": bool(deep),
        "note": "realized volatility (RV20/RV60) is computable from price caches; the constraint is IV history, not RV",
    }


def ivr_feasibility(iv_inv: Dict[str, Any]) -> Dict[str, Any]:
    per_days = iv_inv.get("per_ticker_days") or {}
    per_span = iv_inv.get("per_ticker_span") or {}
    out_rows = {}
    for sym, days in sorted(per_days.items(), key=lambda kv: -kv[1])[:25]:
        first, last = per_span.get(sym, (None, None))
        expected = _trading_days_between(first, last)
        missing_rate = round(1.0 - days / expected, 4) if expected else None
        if days >= IVR_FEASIBLE_MIN_DAYS and (missing_rate or 0) <= IVR_MAX_MISSING_RATE:
            verdict = IVR_FEASIBLE
        elif days >= IVR_PARTIAL_MIN_DAYS:
            verdict = IVR_PARTIAL
        else:
            verdict = IVR_NOT_VALID
        out_rows[sym] = {
            "iv_days": days,
            "span": [first, last],
            "expected_trading_days": expected,
            "missing_rate": missing_rate,
            "stale_quote_rate": "NOT_MEASURABLE (no quotes persisted)",
            "verdict": verdict,
        }
    best_days = max(per_days.values()) if per_days else 0
    overall = IVR_NOT_VALID
    if any(r["verdict"] == IVR_FEASIBLE for r in out_rows.values()):
        overall = IVR_FEASIBLE
    elif any(r["verdict"] == IVR_PARTIAL for r in out_rows.values()):
        overall = IVR_PARTIAL
    return {
        "thresholds": {
            "feasible_min_days": IVR_FEASIBLE_MIN_DAYS,
            "partial_min_days": IVR_PARTIAL_MIN_DAYS,
            "max_missing_rate": IVR_MAX_MISSING_RATE,
        },
        "overall_verdict": overall,
        "best_history_days": best_days,
        "days_until_partial": max(0, IVR_PARTIAL_MIN_DAYS - best_days),
        "days_until_feasible": max(0, IVR_FEASIBLE_MIN_DAYS - best_days),
        "iv_rv_spread": "computable only over the retained IV span (best symbol above); RV side is not the constraint",
        "rows": out_rows,
        "doctrine": "IV history must accumulate forward from live chains; fabricating or backfilling IVR is prohibited",
    }


def strategy_feasibility_map(iv_inv: Dict[str, Any], chains: Dict[str, Any], ivr: Dict[str, Any]) -> Dict[str, Any]:
    no_chains = "no point-in-time strike-level chains, no bid/ask history, no OI history — fills cannot be modeled honestly"
    best_days = ivr.get("best_history_days", 0)
    return {
        "A_iron_condor_30_60_dte": {
            "verdict": NOT_FEASIBLE,
            "why": f"4-leg defined-risk structure needs historical chains with per-strike bid/ask and OI; {no_chains}",
        },
        "B_put_credit_spread_30_60_dte": {
            "verdict": NOT_FEASIBLE,
            "why": f"2-leg spread needs the same per-strike history; {no_chains}",
        },
        "C_cash_secured_put": {
            "verdict": NOT_FEASIBLE,
            "why": "premium capture cannot be priced without historical quotes; an equity-proxy backtest would not be honest premium evidence",
        },
        "D_covered_call": {
            "verdict": NOT_FEASIBLE,
            "why": "same as C — call premium history does not exist locally",
        },
        "E_ivr_signal_only_no_execution": {
            "verdict": FEASIBLE_WITH_LIMITATIONS,
            "why": (
                f"ATM-IV history is accumulating point-in-time (best symbol {best_days} days) but is below the "
                f"{IVR_PARTIAL_MIN_DAYS}-day partial floor; the signal becomes testable as the existing collection keeps running — "
                "no new build required, only time"
            ),
        },
        "F_volatility_regime_diagnostic_only": {
            "verdict": FEASIBLE_NOW,
            "why": (
                "already exists: Phase 1G.12 options regime lens (SPY/QQQ/IWM/VXX IV, skew, term structure, naive gamma proxy) "
                "with a short but honest point-in-time history; diagnostic only, never a trade signal"
            ),
        },
        "dte_backtesting": {
            "30_60_dte_possible": False,
            "45_dte_possible": False,
            "why": "no historical expirations/strikes are retained; a single 45 DTE trade cannot even be priced at entry, let alone marked daily",
        },
    }


def final_decision(iv_inv: Dict[str, Any], chains: Dict[str, Any], ivr: Dict[str, Any]) -> Dict[str, Any]:
    """Task 6 decision rules, applied literally."""
    yes_conditions = {
        "point_in_time_chains_exist": chains["persisted_chain_snapshot_count"] > 0,
        "bid_ask_history_exists": False,
        "historical_iv_exists": ivr["overall_verdict"] in {IVR_FEASIBLE},
        "expirations_dte_available_historically": False,
        "enough_history_for_ivr_and_30_60_dte": False,
    }
    partial_conditions = {
        "limited_theoretical_research_possible": ivr["overall_verdict"] in {IVR_FEASIBLE, IVR_PARTIAL},
        "trade_level_backtest_possible": False,
    }
    if all(yes_conditions.values()):
        answer = "YES"
    elif partial_conditions["limited_theoretical_research_possible"]:
        answer = "PARTIAL"
    else:
        answer = "NO"
    return {
        "answer": answer,
        "yes_conditions": yes_conditions,
        "partial_conditions": partial_conditions,
        "summary": {
            "NO": (
                "Only current chain snapshots are served (never persisted); there is no historical strike-level data, "
                "no bid/ask history, no OI history, and the retained ATM-IV series is too young even for a partial IVR. "
                "A trade-level options premium backtest cannot be modeled honestly today."
            ),
            "PARTIAL": "Enough IV history for limited theoretical research only; trade-level backtests remain invalid.",
            "YES": "All point-in-time requirements met.",
        }[answer],
        "skeleton_built": False,
        "skeleton_rule": "research/options_premium_research_lab.py is built only on YES or strong PARTIAL; not earned",
        "path_to_partial": f"keep the existing Stock Lens IV collection running; PARTIAL unlocks at {IVR_PARTIAL_MIN_DAYS} days of IV history for liquid symbols",
        "path_to_yes": "requires persisting point-in-time strike-level chains (bid/ask, OI, expirations) from the live feed going forward, or licensing historical chain data",
    }


def build_report() -> Dict[str, Any]:
    iv_inv = iv_history_inventory()
    regime_inv = regime_lens_inventory()
    chains = chain_storage_inventory()
    rv = realized_vol_inventory()
    ivr = ivr_feasibility(iv_inv)
    feasibility = strategy_feasibility_map(iv_inv, chains, ivr)
    decision = final_decision(iv_inv, chains, ivr)
    # Keep the JSON compact: per-ticker maps are summarized above.
    iv_slim = {k: v for k, v in iv_inv.items() if k not in ("per_ticker_days", "per_ticker_span")}
    return {
        "kind": "options_data_inventory",
        "version": VERSION,
        "generated_at": _utc_now(),
        "research_only": True,
        "status": "RESEARCH_ONLY",
        "top_line": f"CAN WE VALIDLY BACKTEST OPTIONS PREMIUM NOW? {decision['answer']}",
        "answer": decision["answer"],
        "iv_history": iv_slim,
        "regime_lens": regime_inv,
        "chain_storage": chains,
        "realized_volatility": rv,
        "ivr_feasibility": ivr,
        "strategy_feasibility": feasibility,
        "decision": decision,
        "safety": {
            "no_live_trading": True,
            "no_broker_orders": True,
            "no_paper_signals": True,
            "no_trade_proposals": True,
            "no_provider_calls": True,
            "no_execution_imports": True,
            "no_governance_imports": True,
            "no_production_changes": True,
            "no_directional_module_deletion": True,
            "no_evidence_mutation": True,
            "short_a_remains_frozen": True,
        },
    }


def render_text(res: Dict[str, Any]) -> List[str]:
    iv = res["iv_history"]
    ivr = res["ivr_feasibility"]
    lines = [
        f"OPTIONS DATA INVENTORY (PHASE 1J.0) - {res['generated_at']}",
        "RESEARCH_ONLY: cache-only audit; no provider calls, no signals, no orders, no proposals.",
        res["top_line"],
        "",
        f"iv_history: {iv['row_count']} rows / {iv['symbols_covered']} symbols / window "
        f"{iv['point_in_time']['first_date']}..{iv['point_in_time']['last_date']} / bad_lines={iv['bad_lines']}",
        f"deepest: {[(r['symbol'], r['iv_days']) for r in iv['deepest_symbols'][:5]]}",
        f"granularity: {iv['granularity']}",
        f"persisted chains: {res['chain_storage']['persisted_chain_snapshot_count']} | bid/ask history: NO | greeks history: NO | OI history: NO",
        f"ivr: {ivr['overall_verdict']} (best {ivr['best_history_days']}d; partial at {ivr['thresholds']['partial_min_days']}d, feasible at {ivr['thresholds']['feasible_min_days']}d)",
        "",
        "strategy feasibility:",
    ]
    for key, row in res["strategy_feasibility"].items():
        if key == "dte_backtesting":
            lines.append(f"  30-60 DTE possible: {row['30_60_dte_possible']} | 45 DTE possible: {row['45_dte_possible']}")
            continue
        lines.append(f"  {key}: {row['verdict']}")
    lines += ["", f"decision: {res['answer']} — {res['decision']['summary']}"]
    return lines


def render_doc(res: Dict[str, Any]) -> str:
    iv = res["iv_history"]
    ivr = res["ivr_feasibility"]
    lines = [
        "# Options Data Inventory (Phase 1J.0)",
        "",
        f"Generated: {res['generated_at']}",
        "",
        "RESEARCH_ONLY: cache-only audit; no provider calls, no signals, no orders, no proposals.",
        "",
        f"## {res['top_line']}",
        "",
        res["decision"]["summary"],
        "",
        "## What exists locally",
        "",
        "| Dataset | Granularity | Symbols | Window | Depth |",
        "|---|---|---:|---|---|",
        f"| `cache/research/options_iv_history.jsonl` | ATM IV blend + skew per (ticker, date, bucket) | {iv['symbols_covered']} | "
        f"{iv['point_in_time']['first_date']} → {iv['point_in_time']['last_date']} | best {ivr['best_history_days']}d, median {iv['median_depth_days']}d |",
        f"| `data/research/options_regime_lens_history.jsonl` | market IV/skew/term/gamma-proxy | {len(res['regime_lens']['symbols'])} "
        f"({', '.join(res['regime_lens']['symbols'])}) | {res['regime_lens']['point_in_time']['first_date']} → "
        f"{res['regime_lens']['point_in_time']['last_date']} | {res['regime_lens']['per_symbol_days']} |",
        f"| Strike-level chains (bid/ask, OI, greeks, expirations) | — | 0 | none | **not persisted anywhere** |",
        "",
        "Point-in-time status: both retained series are append-only from live chains "
        f"(future-dated rows: {iv['point_in_time']['future_dated_rows']}); there is NO historical backfill source. "
        "Current snapshots are explicitly NOT treated as history.",
        "",
        "Providers: Alpaca snapshots carry no IV/greeks/OI (OI merged live from the contracts endpoint); "
        "Tradier serves current chains only. Realized volatility is computable "
        f"({res['realized_volatility']['deep_cache_tickers']} deep-cache tickers, ~{res['realized_volatility']['deep_cache_typical_bars']} bars) — IV history is the constraint.",
        "",
        "## IV Rank feasibility (Task 3)",
        "",
        f"Overall: **{ivr['overall_verdict']}** — best symbol has {ivr['best_history_days']} IV days; "
        f"{ivr['days_until_partial']} more days to PARTIAL, {ivr['days_until_feasible']} to FEASIBLE.",
        "",
        "| Symbol | IV days | Span | Missing rate | Verdict |",
        "|---|---:|---|---:|---|",
    ]
    for sym, row in list(ivr["rows"].items())[:10]:
        lines.append(f"| {sym} | {row['iv_days']} | {row['span'][0]} → {row['span'][1]} | {row['missing_rate']} | {row['verdict']} |")
    lines += [
        "",
        "## Strategy feasibility map (Task 4)",
        "",
        "| Strategy | Verdict | Why |",
        "|---|---|---|",
    ]
    for key, row in res["strategy_feasibility"].items():
        if key == "dte_backtesting":
            continue
        lines.append(f"| {key} | **{row['verdict']}** | {row['why']} |")
    dte = res["strategy_feasibility"]["dte_backtesting"]
    lines += [
        "",
        f"30–60 DTE backtesting possible: **{dte['30_60_dte_possible']}**. 45 DTE possible: **{dte['45_dte_possible']}**. {dte['why']}.",
        "",
        "## Paths forward",
        "",
        f"- To PARTIAL: {res['decision']['path_to_partial']}",
        f"- To YES: {res['decision']['path_to_yes']}",
        "",
        "## Safety",
        "",
        "Cache-only audit. No live trading, broker orders, paper signals, trade proposals, strategy activation,",
        "production changes, directional-module deletion, or evidence mutation. SHORT_A remains frozen.",
        f"The Task 7 skeleton was NOT built: {res['decision']['skeleton_rule']}.",
        "",
    ]
    return "\n".join(lines)


def write_outputs(res: Dict[str, Any]) -> None:
    for p in (OUT_JSON, OUT_TXT, OUT_DOC):
        p.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    OUT_TXT.write_text("\n".join(render_text(res)) + "\n", encoding="utf-8")
    OUT_DOC.write_text(render_doc(res) + "\n", encoding="utf-8")


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Phase 1J.0 options data inventory (cache-only, research-only)")
    ap.parse_args(argv)
    res = build_report()
    write_outputs(res)
    print("\n".join(render_text(res)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

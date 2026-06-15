#!/usr/bin/env python3
"""
research/leader_reset_reclaim_strategy.py - Phase 1I LEADER_RESET_RECLAIM test.

Research-only standalone alpha test for the LEADER_RESET_RECLAIM variant
(prior leader -> controlled reset -> EMA reclaim with close strength). It is
the formal Strategy Lab successor to the Phase 1G.3 leader-reset event study
and is designed around the 1H.4 finding that pullback-band / extension gates
sole-block benchmark-beating flow which patched gates failed to monetize.

Writes cache/log/doc artifacts only; never imports broker, execution,
governance, paper-signal, or live-capital modules. A paper-shadow proposal
DOC is written only if every strict eligibility gate passes.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from research import strategy_failure_reason_miner as miner  # noqa: E402
from research import strategy_lab_correction_strategy as correction  # noqa: E402
from research import strategy_lab_portfolio as portfolio  # noqa: E402
from research import strategy_research_lab as lab  # noqa: E402
from research import strategy_walk_forward as wf  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "cache" / "research"
LOGS = ROOT / "logs"
DOCS = ROOT / "docs" / "research"

OUT_JSON = CACHE / "leader_reset_reclaim_latest.json"
OUT_TXT = LOGS / "leader_reset_reclaim_latest.txt"
OUT_DOC = DOCS / "LEADER_RESET_RECLAIM_RESULTS.md"
PROPOSAL_DOC = DOCS / "LEADER_RESET_RECLAIM_PAPER_SHADOW_PROPOSAL.md"

VERSION = "LEADER_RESET_RECLAIM_V1"
VARIANT = "LEADER_RESET_RECLAIM"

COMPARISON_VARIANTS = (
    VARIANT,
    "PROD_SNIPER_CURRENT",
    "POWER_TREND_EXTENSION",
    "RECALL_SHADOW_PULLBACK",
    "RANDOM_LIQUID",
)

YES = "YES"
MAYBE = "MAYBE"
NO = "NO"

MIN_INDEPENDENT_TRADES = 40
MIN_ACCEPTED_TRADES = 20
MIN_TEST_TRADES = 20
MAX_REALISTIC_DD = -0.15
MAX_INDEPENDENT_DD = -0.30
MAX_TICKER_CONC = 0.35
MAX_SECTOR_CONC = 0.70

_f = lab._f
_pct = miner._pct


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mean(vals: Sequence[float]) -> Optional[float]:
    return round(statistics.mean(vals), 6) if vals else None


def _regime_breakdown(trades: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    by: Dict[str, List[float]] = defaultdict(list)
    for t in trades:
        by[str(t.get("market_regime") or "UNKNOWN")].append(float(t.get("net_return") or 0.0))
    return {
        label: {
            "trade_count": len(vals),
            "expectancy": _mean(vals),
            "win_rate": round(sum(1 for v in vals if v > 0) / len(vals), 4) if vals else None,
        }
        for label, vals in sorted(by.items())
    }


def _year_breakdown(trades: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    by: Dict[str, List[float]] = defaultdict(list)
    for t in trades:
        by[str(t.get("exit_date") or "")[:4]].append(float(t.get("net_return") or 0.0))
    out = {}
    for year, vals in sorted(by.items()):
        equity = 1.0
        for v in vals:
            equity *= 1.0 + v
        out[year] = {
            "trade_count": len(vals),
            "expectancy": _mean(vals),
            "win_rate": round(sum(1 for v in vals if v > 0) / len(vals), 4) if vals else None,
            "compounded_return": round(equity - 1.0, 6),
        }
    return out


def _split_metrics(trades: Sequence[Dict[str, Any]], splits: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for name in ("train", "validation", "test"):
        block = splits.get(name) or {}
        s, e = block.get("start"), block.get("end")
        if not s or not e:
            out[name] = {"trade_count": 0}
            continue
        subset = [t for t in trades if s <= str(t.get("signal_date"))[:10] <= e]
        m = lab.summarize_trades(subset, start=s, end=e)
        out[name] = {
            "start": s,
            "end": e,
            "trade_count": m.get("trade_count"),
            "expectancy": m.get("expectancy"),
            "win_rate": m.get("win_rate"),
            "rel_spy": m.get("rel_spy"),
            "rel_qqq": m.get("rel_qqq"),
            "max_drawdown": m.get("max_drawdown"),
            "profit_factor": m.get("profit_factor"),
        }
    return out


def _slim_exact(m: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(m)
    for key in ("worst_10_trades", "best_10_trades"):
        rows = out.get(key) or []
        out[key] = [
            {k: r.get(k) for k in ("ticker", "signal_date", "net_return", "exit_reason", "market_regime")}
            for r in rows[:5]
        ]
    return out


def _eligibility(
    *,
    independent: Dict[str, Any],
    realistic: Dict[str, Any],
    same_spy: Dict[str, Any],
    same_qqq: Dict[str, Any],
    exact: Dict[str, Any],
    splits: Dict[str, Any],
    month_check: Dict[str, Any],
    concentration: Dict[str, Any],
) -> Dict[str, Any]:
    """Strict promotion ladder. Every gate must pass for a YES."""
    blockers: List[str] = []
    soft: List[str] = []

    n = int(independent.get("trade_count") or 0)
    accepted = int(realistic.get("accepted_trade_count") or 0)
    if n < MIN_INDEPENDENT_TRADES:
        blockers.append(f"independent trade count {n} < {MIN_INDEPENDENT_TRADES}")
    if accepted < MIN_ACCEPTED_TRADES:
        blockers.append(f"realistic accepted trades {accepted} < {MIN_ACCEPTED_TRADES}")
    if _f(independent.get("expectancy"), -1.0) <= 0:
        blockers.append("independent expectancy is not positive")
    if _f(realistic.get("total_return")) <= 0 or _f(realistic.get("cagr")) <= 0:
        blockers.append("realistic portfolio return/CAGR is not positive after cash drag")
    if _f(realistic.get("max_drawdown")) < MAX_REALISTIC_DD:
        blockers.append(f"realistic maxDD {_pct(realistic.get('max_drawdown'))} breaches {_pct(MAX_REALISTIC_DD)}")
    if _f(independent.get("max_drawdown")) < MAX_INDEPENDENT_DD:
        blockers.append(
            f"independent-trade maxDD {_pct(independent.get('max_drawdown'))} breaches {_pct(MAX_INDEPENDENT_DD)} "
            "(portfolio caps would be doing the work, the 1H.2 CLR failure mode)"
        )

    same_best_ret = max(_f(same_spy.get("total_return")), _f(same_qqq.get("total_return")))
    same_best_sharpe = max(_f(same_spy.get("sharpe"), -9.0), _f(same_qqq.get("sharpe"), -9.0))
    if _f(realistic.get("total_return")) <= same_best_ret:
        blockers.append(
            f"does not beat same-exposure SPY/QQQ total return ({_pct(realistic.get('total_return'))} vs {_pct(same_best_ret)})"
        )
    if _f(realistic.get("sharpe"), -9.0) <= same_best_sharpe:
        blockers.append(
            f"does not beat same-exposure SPY/QQQ Sharpe ({realistic.get('sharpe')} vs {round(same_best_sharpe, 4)})"
        )

    if not month_check.get("passes"):
        blockers.append("edge depends on one month or lacks multiple positive months")
    if _f(concentration.get("top_ticker_pct")) > MAX_TICKER_CONC:
        blockers.append(f"ticker concentration {concentration.get('top_ticker_pct')} > {MAX_TICKER_CONC}")
    if _f(concentration.get("top_sector_pct")) > MAX_SECTOR_CONC:
        blockers.append(f"sector concentration {concentration.get('top_sector_pct')} > {MAX_SECTOR_CONC}")

    test = splits.get("test") or {}
    positive_splits = sum(
        1 for blk in ("train", "validation", "test")
        if _f((splits.get(blk) or {}).get("expectancy"), -1.0) > 0
    )
    if int(test.get("trade_count") or 0) < MIN_TEST_TRADES:
        blockers.append(f"walk-forward test split has {test.get('trade_count')} trades (<{MIN_TEST_TRADES})")
    elif _f(test.get("expectancy"), -1.0) <= 0:
        blockers.append("walk-forward test expectancy is not positive")
    if positive_splits < 2:
        blockers.append(f"expectancy positive in only {positive_splits}/3 walk-forward splits")
    if _f(test.get("rel_spy"), -1.0) <= 0 and _f(test.get("rel_qqq"), -1.0) <= 0:
        soft.append("walk-forward test split does not beat SPY/QQQ per-trade (decay watch)")

    flow_or_maturity_only = blockers and all(
        ("trade count" in b or "accepted trades" in b or "test split has" in b) for b in blockers
    )
    if not blockers:
        answer = YES
    elif flow_or_maturity_only:
        answer = MAYBE
    else:
        answer = NO
    return {
        "answer": answer,
        "blockers": blockers,
        "soft_warnings": soft,
        "rules": {
            "min_independent_trades": MIN_INDEPENDENT_TRADES,
            "min_accepted_trades": MIN_ACCEPTED_TRADES,
            "min_test_trades": MIN_TEST_TRADES,
            "max_realistic_dd": MAX_REALISTIC_DD,
            "max_independent_dd": MAX_INDEPENDENT_DD,
            "max_ticker_concentration": MAX_TICKER_CONC,
            "max_sector_concentration": MAX_SECTOR_CONC,
        },
    }


def build_report(
    *,
    start: Optional[str] = None,
    end: Optional[str] = None,
    stride: int = 1,
    max_dates: Optional[int] = None,
    skip_miner: bool = False,
) -> Dict[str, Any]:
    if start is None or end is None:
        span_start, span_end, _, _ = portfolio._primary_window_span()
        start = start or span_start
        end = end or span_end
    params = lab.StrategyParams()
    config = lab.BacktestConfig(universe_cap=140, date_stride=stride)

    # Primary collection (hold=10, fixed a priori) for LRR + comparisons.
    collected = portfolio.collect_trades_for_span(
        start, end, variants=COMPARISON_VARIANTS, params=params, config=config, cost_model=lab.BASE_COST,
    )
    if max_dates is not None:
        pass  # max_dates only applies to the miner pass below; span collection is exact.

    splits = wf.make_walk_forward_splits(start=start, end=end).get("splits") or {}
    benchmarks = {
        sym: correction._augment_metrics(portfolio.benchmark_metrics(sym, start=start, end=end, cost_model=lab.BASE_COST))
        for sym in ("SPY", "QQQ")
    }

    comparison: Dict[str, Any] = {}
    lrr_block: Dict[str, Any] = {}
    for variant in COMPARISON_VARIANTS:
        trades = collected["trades_by_variant"].get(variant, [])
        exact = _slim_exact(lab.summarize_trades(trades, start=start, end=end))
        independent = portfolio.independent_trade_metrics(trades, start=start, end=end)
        realistic = portfolio.realistic_portfolio_metrics(trades, start=start, end=end, config=portfolio.PortfolioConfig())
        rows = realistic.get("daily_rows") or []
        same_spy = correction._same_exposure_benchmark("SPY", rows, start=start, end=end)
        same_qqq = correction._same_exposure_benchmark("QQQ", rows, start=start, end=end)
        block = {
            "exact": exact,
            "independent": correction._augment_metrics(independent),
            "realistic": correction._augment_metrics(realistic),
            "same_exposure_spy": same_spy,
            "same_exposure_qqq": same_qqq,
            "walk_forward": _split_metrics(trades, splits),
            "regime_breakdown": _regime_breakdown(trades),
            "year_breakdown": _year_breakdown(trades),
        }
        comparison[variant] = {
            "trade_count": exact.get("trade_count"),
            "expectancy": exact.get("expectancy"),
            "win_rate": exact.get("win_rate"),
            "rel_spy": exact.get("rel_spy"),
            "rel_qqq": exact.get("rel_qqq"),
            "independent_max_dd": block["independent"].get("max_drawdown"),
            "realistic_return": block["realistic"].get("total_return"),
            "realistic_cagr": block["realistic"].get("cagr"),
            "realistic_max_dd": block["realistic"].get("max_drawdown"),
            "realistic_sharpe": block["realistic"].get("sharpe"),
            "exposure": block["realistic"].get("exposure_pct"),
            "same_exposure_spy_return": same_spy.get("total_return"),
            "same_exposure_qqq_return": same_qqq.get("total_return"),
        }
        if variant == VARIANT:
            lrr_block = block

    # Hold-period sensitivity (5/10/20d) — signals identical, exits differ.
    # 10d is the a-priori primary; 5/20 are sensitivity only, not selection.
    hold_sensitivity = {}
    lrr_signals_trades: Dict[int, List[Dict[str, Any]]] = {}
    for hold in (5, 10, 20):
        p_h = dataclasses.replace(params, max_hold_days=hold)
        if hold == 10:
            trades_h = collected["trades_by_variant"].get(VARIANT, [])
        else:
            c_h = portfolio.collect_trades_for_span(
                start, end, variants=(VARIANT,), params=p_h, config=config, cost_model=lab.BASE_COST,
            )
            trades_h = c_h["trades_by_variant"].get(VARIANT, [])
        lrr_signals_trades[hold] = trades_h
        m = lab.summarize_trades(trades_h, start=start, end=end)
        hold_sensitivity[f"hold_{hold}d"] = {
            "trade_count": m.get("trade_count"),
            "expectancy": m.get("expectancy"),
            "win_rate": m.get("win_rate"),
            "rel_spy": m.get("rel_spy"),
            "rel_qqq": m.get("rel_qqq"),
            "max_drawdown": m.get("max_drawdown"),
            "stop_hit_rate": m.get("stop_hit_rate"),
            "target_hit_rate": m.get("target_hit_rate"),
        }

    # Accepted-loser / rejected-winner autopsy via the 1H.4 miner (LRR only).
    miner_block: Dict[str, Any] = {"skipped": True}
    if not skip_miner:
        mined = miner.mine(start, end, variants=(VARIANT,), params=params, config=config, max_dates=max_dates)
        agg = mined["aggs"][VARIANT]
        miner_block = {
            "skipped": False,
            "fidelity_mismatches": agg.fidelity_mismatches,
            "class_counts": dict(agg.class_counts),
            "accepted_outcomes": miner._final_outcome(agg.accepted_out),
            "gate_table": miner._gate_table(agg),
            "accepted_loser_patterns": miner._loser_pattern_table(agg),
            "rejected_winner_patterns": miner._winner_pattern_table(agg),
        }

    lrr_trades = collected["trades_by_variant"].get(VARIANT, [])
    month_check = correction._works_outside_one_month(lrr_trades)
    concentration = correction._concentration(lrr_trades)
    eligibility = _eligibility(
        independent=lrr_block.get("independent") or {},
        realistic=lrr_block.get("realistic") or {},
        same_spy=lrr_block.get("same_exposure_spy") or {},
        same_qqq=lrr_block.get("same_exposure_qqq") or {},
        exact=lrr_block.get("exact") or {},
        splits=lrr_block.get("walk_forward") or {},
        month_check=month_check,
        concentration=concentration,
    )

    return {
        "kind": "leader_reset_reclaim",
        "version": VERSION,
        "generated_at": _utc_now(),
        "research_only": True,
        "status": "RESEARCH_ONLY",
        "top_line": f"DID WE FIND A PAPER-SHADOW CANDIDATE? {eligibility['answer']}",
        "answer": eligibility["answer"],
        "signal_window": {"start": start, "end": end},
        "strategy_rules": {
            "prior_leadership": f"max(rs60_spy, rs60_qqq) >= {lab.LRR_RS60_MIN} and r60 >= {lab.LRR_R60_MIN}",
            "reset": (
                f"pullback from 20d high in [-{lab.LRR_RESET_MAX}, -{lab.LRR_RESET_MIN}], above MA200, "
                f"price >= MA50*0.92, r10 >= {lab.LRR_CRASH_R10} (no climax failure), r20 <= {lab.LRR_PARABOLIC_R20} (no parabolic)"
            ),
            "reclaim": "10 EMA reclaim preferred (20 EMA fallback) with an up close; entry next open",
            "risk": "stop = max(2x ATR, distance below reclaim low + 1%) clamped to [4%, 10%]; target +10%; max hold 10d primary (5/20 sensitivity)",
            "fitting": "all thresholds fixed a priori; not exposed to any parameter sweep",
            "lineage": "Phase 1G.3 leader_reset_event_study -> Phase 1H.4 overblocking evidence -> this standalone test",
        },
        "params": params.as_dict(),
        "config": config.as_dict(),
        "collection": {k: v for k, v in collected.items() if k != "trades_by_variant"},
        "walk_forward_splits": splits,
        "walk_forward_note": "Rules are fixed a priori; splits are a decay diagnostic and the test block is binding evidence.",
        "benchmarks": benchmarks,
        "comparison_table": comparison,
        "leader_reset_reclaim": lrr_block,
        "hold_sensitivity": hold_sensitivity,
        "month_check": month_check,
        "concentration": concentration,
        "failure_miner": miner_block,
        "eligibility": eligibility,
        "paper_shadow": {
            "proposal_created": eligibility["answer"] == YES,
            "proposal_doc": str(PROPOSAL_DOC) if eligibility["answer"] == YES else None,
            "note": "Proposal doc only; nothing is activated.",
        },
        "safety": lab.safety_confirmations(),
    }


def render_text(res: Dict[str, Any]) -> List[str]:
    lines = [
        f"LEADER RESET RECLAIM (PHASE 1I) - {res['generated_at']}",
        lab.RESEARCH_DISCLAIMER,
        res["top_line"],
        f"window={res['signal_window']['start']}..{res['signal_window']['end']}",
        "",
        f"{'variant':28s} {'n':>5s} {'exp':>8s} {'win':>6s} {'indDD':>8s} {'realRet':>8s} {'realDD':>8s} {'sharpe':>7s} {'sameSPY':>8s}",
    ]
    for variant, row in res["comparison_table"].items():
        lines.append(
            f"{variant:28s} {row['trade_count'] or 0:>5d} {_pct(row['expectancy']):>8s} "
            f"{row['win_rate'] if row['win_rate'] is not None else 'n/a':>6} {_pct(row['independent_max_dd']):>8s} "
            f"{_pct(row['realistic_return']):>8s} {_pct(row['realistic_max_dd']):>8s} "
            f"{str(row['realistic_sharpe']):>7s} {_pct(row['same_exposure_spy_return']):>8s}"
        )
    lines += ["", "eligibility blockers:"]
    lines += [f"  - {b}" for b in res["eligibility"]["blockers"]] or ["  none"]
    return lines


def render_doc(res: Dict[str, Any]) -> str:
    e = res["eligibility"]
    lrr = res["leader_reset_reclaim"]
    lines = [
        "# LEADER_RESET_RECLAIM Results (Phase 1I)",
        "",
        f"Generated: {res['generated_at']}",
        "",
        lab.RESEARCH_DISCLAIMER,
        "",
        f"## {res['top_line']}",
        "",
        f"Signal window: `{res['signal_window']['start']}` to `{res['signal_window']['end']}`.",
        "",
        "## Strategy Rules (fixed a priori)",
        "",
        *[f"- **{k}**: {v}" for k, v in res["strategy_rules"].items()],
        "",
        "## Comparison Table (exact backtest + realistic portfolio)",
        "",
        "| Variant | Trades | Expectancy | Win | Rel SPY | Ind MaxDD | Real Return | Real MaxDD | Sharpe | Same-Exp SPY |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for variant, row in res["comparison_table"].items():
        lines.append(
            f"| {variant} | {row['trade_count']} | {_pct(row['expectancy'])} | {row['win_rate']} | {_pct(row['rel_spy'])} | "
            f"{_pct(row['independent_max_dd'])} | {_pct(row['realistic_return'])} | {_pct(row['realistic_max_dd'])} | "
            f"{row['realistic_sharpe']} | {_pct(row['same_exposure_spy_return'])} |"
        )
    lines += [
        "",
        "## Benchmarks (full exposure)",
        "",
        f"- SPY: {_pct(res['benchmarks']['SPY'].get('total_return'))} (maxDD {_pct(res['benchmarks']['SPY'].get('max_drawdown'))})",
        f"- QQQ: {_pct(res['benchmarks']['QQQ'].get('total_return'))} (maxDD {_pct(res['benchmarks']['QQQ'].get('max_drawdown'))})",
        "",
        "## Walk-Forward (a-priori rules; decay diagnostic)",
        "",
        "| Split | Trades | Expectancy | Win | Rel SPY | MaxDD |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, row in (lrr.get("walk_forward") or {}).items():
        lines.append(
            f"| {name} | {row.get('trade_count')} | {_pct(row.get('expectancy'))} | {row.get('win_rate')} | "
            f"{_pct(row.get('rel_spy'))} | {_pct(row.get('max_drawdown'))} |"
        )
    lines += ["", "## Hold Sensitivity (10d is primary, fixed a priori)", "", "| Hold | Trades | Expectancy | Win | Rel SPY | MaxDD | Stop% | Target% |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for name, row in res["hold_sensitivity"].items():
        lines.append(
            f"| {name} | {row['trade_count']} | {_pct(row['expectancy'])} | {row['win_rate']} | {_pct(row['rel_spy'])} | "
            f"{_pct(row['max_drawdown'])} | {row['stop_hit_rate']} | {row['target_hit_rate']} |"
        )
    lines += ["", "## Regime Breakdown", "", "| Regime | Trades | Expectancy | Win |", "|---|---:|---:|---:|"]
    for label, row in (lrr.get("regime_breakdown") or {}).items():
        lines.append(f"| {label} | {row['trade_count']} | {_pct(row['expectancy'])} | {row['win_rate']} |")
    lines += ["", "## Year Breakdown", "", "| Year | Trades | Expectancy | Win | Compounded |", "|---|---:|---:|---:|---:|"]
    for year, row in (lrr.get("year_breakdown") or {}).items():
        lines.append(f"| {year} | {row['trade_count']} | {_pct(row['expectancy'])} | {row['win_rate']} | {_pct(row['compounded_return'])} |")

    fm = res.get("failure_miner") or {}
    if not fm.get("skipped"):
        cc = fm.get("class_counts") or {}
        lines += [
            "",
            "## Accepted Losers / Rejected Winners (1H.4 miner, exit-free 10d labels)",
            "",
            f"- Accepted winners/losers: {cc.get('ACCEPTED_WINNER', 0)} / {cc.get('ACCEPTED_LOSER', 0)}",
            f"- Rejected winners/losers: {cc.get('REJECTED_WINNER', 0)} / {cc.get('REJECTED_LOSER', 0)}",
            f"- Trace fidelity mismatches: {fm.get('fidelity_mismatches')}",
            "",
            "| Gate | Fired | Sole-blocked (matured) | Blocked avg fwd10 | Verdict |",
            "|---|---:|---:|---:|---|",
        ]
        for gate, g in (fm.get("gate_table") or {}).items():
            s = g["sole_blocker"]
            lines.append(f"| {gate} | {g['fired_count']} | {s['matured']} | {_pct(s['avg_fwd_10d'])} | {g['verdict']} |")
    lines += [
        "",
        "## Eligibility Verdict",
        "",
        f"**{res['top_line']}**",
        "",
        "Blockers:" if e["blockers"] else "All gates passed.",
        *[f"- {b}" for b in e["blockers"]],
        *(["", "Soft warnings:"] + [f"- {s}" for s in e["soft_warnings"]] if e["soft_warnings"] else []),
        "",
        "## Safety",
        "",
        "No paper signals, broker orders, trade proposals (doc-only), production thresholds, Gatekeeper/Veto logic,",
        "execution/governance/live-capital modules, historical evidence, or SHORT_A frozen status were changed.",
        "Production gates were not loosened; LEADER_RESET_RECLAIM is registered only in the research lab's variant map",
        "and is not part of the lab's default variant list.",
        "",
    ]
    return "\n".join(lines)


def render_proposal_doc(res: Dict[str, Any]) -> str:
    lrr = res["leader_reset_reclaim"]
    real = lrr.get("realistic") or {}
    test = (lrr.get("walk_forward") or {}).get("test") or {}
    return "\n".join([
        "# LEADER_RESET_RECLAIM Paper-Shadow Proposal (Phase 1I)",
        "",
        "Manual review required. This document does not activate anything.",
        "",
        "## Strategy",
        "",
        *[f"- **{k}**: {v}" for k, v in res["strategy_rules"].items()],
        "",
        "## Evidence",
        "",
        f"- Exact backtest: {lrr['exact'].get('trade_count')} trades, expectancy {_pct(lrr['exact'].get('expectancy'))}, rel-SPY {_pct(lrr['exact'].get('rel_spy'))}",
        f"- Walk-forward test split: n={test.get('trade_count')}, expectancy {_pct(test.get('expectancy'))}",
        f"- Realistic portfolio: return {_pct(real.get('total_return'))}, maxDD {_pct(real.get('max_drawdown'))}, Sharpe {real.get('sharpe')}",
        f"- Same-exposure SPY/QQQ: {_pct(lrr['same_exposure_spy'].get('total_return'))} / {_pct(lrr['same_exposure_qqq'].get('total_return'))}",
        "",
        "## Risk limits and kill criteria (paper-only)",
        "",
        "- Lab portfolio caps: 5 concurrent, 10% position, 30% sector, 50% gross exposure; no margin.",
        "- Kill if paper-shadow expectancy after 30 trades <= 0, drawdown-equivalent exceeds -10%,",
        "  or any month contributes more than 65% of cumulative gains.",
        "- Paper-only: signals logged for evidence; no orders of any kind.",
        "",
    ])


def write_outputs(res: Dict[str, Any]) -> None:
    for p in (OUT_JSON, OUT_TXT, OUT_DOC):
        p.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    OUT_TXT.write_text("\n".join(render_text(res)) + "\n", encoding="utf-8")
    OUT_DOC.write_text(render_doc(res) + "\n", encoding="utf-8")
    if res["paper_shadow"]["proposal_created"]:
        PROPOSAL_DOC.write_text(render_proposal_doc(res) + "\n", encoding="utf-8")


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Phase 1I LEADER_RESET_RECLAIM standalone alpha test (research-only)")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--stride", type=int, default=1)
    ap.add_argument("--max-dates", type=int, default=None)
    ap.add_argument("--skip-miner", action="store_true")
    args = ap.parse_args(argv)
    res = build_report(start=args.start, end=args.end, stride=args.stride, max_dates=args.max_dates, skip_miner=args.skip_miner)
    write_outputs(res)
    print("\n".join(render_text(res)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

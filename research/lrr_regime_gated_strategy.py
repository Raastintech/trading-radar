#!/usr/bin/env python3
"""
research/lrr_regime_gated_strategy.py - Phase 1I.1 LRR_REGIME_GATED test.

Research-only, strict a-priori test of the regime-gated Leader Reset Reclaim
variant. The allowed-regime set was fixed BEFORE this variant was ever run,
from the Phase 1I regime breakdown of unrestricted LRR; because the hypothesis
came from in-sample analysis, this report applies extra anti-overfit checks
and treats the walk-forward test block as the binding evidence.

Writes cache/log/doc artifacts only; never imports broker, execution,
governance, paper-signal, or live-capital modules. A paper-shadow proposal
DOC is written only if every gate passes.
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
from research import leader_reset_reclaim_strategy as lrr_report  # noqa: E402
from research import strategy_failure_reason_miner as miner  # noqa: E402
from research import strategy_lab_correction_strategy as correction  # noqa: E402
from research import strategy_lab_portfolio as portfolio  # noqa: E402
from research import strategy_lab_regime as regime  # noqa: E402
from research import strategy_research_lab as lab  # noqa: E402
from research import strategy_walk_forward as wf  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "cache" / "research"
LOGS = ROOT / "logs"
DOCS = ROOT / "docs" / "research"

OUT_JSON = CACHE / "lrr_regime_gated_latest.json"
OUT_TXT = LOGS / "lrr_regime_gated_latest.txt"
OUT_DOC = DOCS / "LRR_REGIME_GATED_RESULTS.md"
GATE_SPEC_JSON = CACHE / "lrr_regime_gate_spec_latest.json"
GATE_SPEC_DOC = DOCS / "LRR_REGIME_GATE_SPEC.md"
PROPOSAL_DOC = DOCS / "PAPER_SHADOW_PROMOTION_PROPOSAL.md"

VERSION = "LRR_REGIME_GATED_V1"
VARIANT = "LRR_REGIME_GATED"
BASE_VARIANT = "LEADER_RESET_RECLAIM"

COMPARISON_VARIANTS = (
    VARIANT,
    BASE_VARIANT,
    "POWER_TREND_EXTENSION",
    "RECALL_SHADOW_PULLBACK",
    "PROD_SNIPER_CURRENT",
    "SNIPER_NO_ATR_CONTRACTION",
    "RANDOM_LIQUID",
)

# Decision ladder labels (Task 5).
REJECT = "REJECT"
NEED_MORE = "NEED_MORE_DATA"
OVERFIT = "PROMISING_BUT_OVERFIT_RISK"
PORTFOLIO_RISK = "PROMISING_BUT_PORTFOLIO_RISK"
LOW_FLOW = "LOW_FLOW_PAPER_WATCH"
CANDIDATE = "PAPER_SHADOW_CANDIDATE"
NO_VARIANT_READY = "NO_VARIANT_READY_FOR_PAPER_SHADOW"

YES, MAYBE, NO = "YES", "MAYBE", "NO"

MIN_INDEPENDENT_TRADES = 40
MIN_ACCEPTED_TRADES = 20
MIN_TEST_TRADES = 20
MAX_REALISTIC_DD = -0.15
MAX_INDEPENDENT_DD = -0.30
MAX_TICKER_CONC = 0.35
MAX_MONTH_SHARE = 0.65
MAX_NOV_2024_SHARE = 0.50

_f = lab._f
_pct = miner._pct


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def gate_spec() -> Dict[str, Any]:
    """Task 1: the fixed a-priori regime gate. Pure data, no computation."""
    return {
        "kind": "lrr_regime_gate_spec",
        "version": VERSION,
        "fixed_a_priori": True,
        "fixed_on": "2026-06-12",
        "derived_from": "Phase 1I regime breakdown of unrestricted LEADER_RESET_RECLAIM (cache/research/leader_reset_reclaim_latest.json)",
        "allowed_regimes": sorted(lab.LRR_ALLOWED_REGIMES),
        "blocked_regimes": sorted(
            {regime.RISK_OFF, regime.MARKET_CORRECTION, regime.BULL_TREND, regime.CHOP}
        ) + ["<any unknown/None label>"],
        "bull_trend_note": (
            "BULL_TREND is blocked. The classifier emits RECOVERY_RECLAIM as its own label when the "
            "recovery/reclaim condition is present, so 'bull unless recovery condition' reduces to the allowed set."
        ),
        "classifier": {
            "module": "research/strategy_lab_regime.py:classify_regime",
            "as_of_inputs": [
                "SPY/QQQ drawdown from 20d/60d highs",
                "QQQ vs SPY 20d relative strength; SMH/XLK vs SPY 20d relative strength",
                "SPY/QQQ 5d/10d/20d returns",
                "SPY/QQQ above EMA20 / MA50 / MA200 trend state",
                "SPY/QQQ ATR%% and VXX (VIXY fallback) 10d return as volatility proxy",
            ],
            "no_future_data": "classify_regime reads compute_features_asof only; no future bars, no outcome labels, no manual overrides",
        },
        "anti_overfit_doctrine": (
            "The allowed set came from in-sample analysis of Phase 1I, so this variant is a NEW hypothesis. "
            "It must pass walk-forward, month/ticker concentration, same-exposure Sharpe, and 2026-decay checks; "
            "the regime set itself is frozen and MUST NOT be re-tuned on this sample."
        ),
    }


def _year_expectancy(trades: Sequence[Dict[str, Any]], year: str) -> Optional[float]:
    vals = [float(t.get("net_return") or 0.0) for t in trades if str(t.get("exit_date") or "").startswith(year)]
    return round(statistics.mean(vals), 6) if vals else None


def _month_share(month_check: Dict[str, Any], month: str) -> Optional[float]:
    best = month_check.get("best_month") or {}
    if best.get("month") == month:
        return month_check.get("best_positive_month_share")
    return None


def _nov_2024_share(trades: Sequence[Dict[str, Any]]) -> Optional[float]:
    """Share of total compounded (independent) gain contributed by 2024-11."""
    by_month: Dict[str, float] = defaultdict(lambda: 1.0)
    for t in trades:
        m = str(t.get("exit_date") or t.get("signal_date") or "")[:7]
        by_month[m] *= 1.0 + float(t.get("net_return") or 0.0)
    total = 1.0
    for v in by_month.values():
        total *= v
    total_ret = total - 1.0
    nov = by_month.get("2024-11", 1.0) - 1.0
    if total_ret <= 0 or nov <= 0:
        return 0.0 if nov <= 0 else None
    return round(nov / total_ret, 4)


def anti_overfit_checks(
    *,
    gated: Dict[str, Any],
    unrestricted: Dict[str, Any],
    random_block: Dict[str, Any],
    gated_trades: Sequence[Dict[str, Any]],
    unrestricted_trades: Sequence[Dict[str, Any]],
    month_check: Dict[str, Any],
    concentration: Dict[str, Any],
) -> Dict[str, Any]:
    """Task 4: every check is explicit, named, and pass/fail."""
    real = gated.get("realistic") or {}
    ind = gated.get("independent") or {}
    base_real = unrestricted.get("realistic") or {}
    base_ind = unrestricted.get("independent") or {}
    same_spy = gated.get("same_exposure_spy") or {}
    same_qqq = gated.get("same_exposure_qqq") or {}
    splits = gated.get("walk_forward") or {}
    test = splits.get("test") or {}

    same_best_sharpe = max(_f(same_spy.get("sharpe"), -9.0), _f(same_qqq.get("sharpe"), -9.0))
    same_best_ret = max(_f(same_spy.get("total_return")), _f(same_qqq.get("total_return")))
    nov_share = _nov_2024_share(gated_trades)
    ytd_gated = _year_expectancy(gated_trades, "2026")
    ytd_base = _year_expectancy(unrestricted_trades, "2026")
    positive_splits = sum(
        1 for blk in ("train", "validation", "test")
        if _f((splits.get(blk) or {}).get("expectancy"), -1.0) > 0
    )
    top_theme = concentration.get("top_theme") or (None, 0)

    checks = {
        "improves_sharpe_vs_same_exposure": {
            "passed": _f(real.get("sharpe"), -9.0) > same_best_sharpe,
            "detail": f"gated Sharpe {real.get('sharpe')} vs same-exposure best {round(same_best_sharpe, 4)}",
        },
        "improves_maxdd_vs_unrestricted_lrr": {
            "passed": (
                _f(real.get("max_drawdown"), -1.0) > _f(base_real.get("max_drawdown"), -1.0)
                and _f(ind.get("max_drawdown"), -1.0) > _f(base_ind.get("max_drawdown"), -1.0)
            ),
            "detail": (
                f"realistic {_pct(real.get('max_drawdown'))} vs {_pct(base_real.get('max_drawdown'))}; "
                f"independent {_pct(ind.get('max_drawdown'))} vs {_pct(base_ind.get('max_drawdown'))}"
            ),
        },
        "avoids_nov_2024_concentration": {
            "passed": nov_share is not None and nov_share <= MAX_NOV_2024_SHARE,
            "detail": f"2024-11 share of compounded gain = {nov_share} (max {MAX_NOV_2024_SHARE})",
        },
        "improves_2026_ytd_decay": {
            "passed": ytd_gated is not None and ytd_gated > 0 and (ytd_base is None or ytd_gated > ytd_base),
            "detail": f"2026 expectancy gated {_pct(ytd_gated)} vs unrestricted {_pct(ytd_base)}",
        },
        "walk_forward_test_positive": {
            "passed": int(test.get("trade_count") or 0) >= MIN_TEST_TRADES and _f(test.get("expectancy"), -1.0) > 0,
            "detail": f"test n={test.get('trade_count')}, expectancy {_pct(test.get('expectancy'))}",
        },
        "walk_forward_majority_positive": {
            "passed": positive_splits >= 2,
            "detail": f"expectancy positive in {positive_splits}/3 splits",
        },
        "works_in_multiple_months": {
            "passed": bool(month_check.get("passes")),
            "detail": f"positive months {month_check.get('positive_months')}/{month_check.get('month_count')}, best share {month_check.get('best_positive_month_share')}",
        },
        "works_across_tickers_and_themes": {
            "passed": _f(concentration.get("top_ticker_pct")) <= MAX_TICKER_CONC and _f(concentration.get("top_theme_pct")) <= 0.60,
            "detail": f"top ticker {concentration.get('top_ticker_pct')}, top theme {top_theme} ({concentration.get('top_theme_pct')})",
        },
        "beats_random_after_costs": {
            "passed": _f((gated.get("exact") or {}).get("expectancy"), -1.0) > _f((random_block.get("exact") or {}).get("expectancy"), -1.0),
            "detail": f"gated expectancy {_pct((gated.get('exact') or {}).get('expectancy'))} vs random {_pct((random_block.get('exact') or {}).get('expectancy'))}",
        },
        "beats_same_exposure_return_after_costs": {
            "passed": _f(real.get("total_return")) > same_best_ret,
            "detail": f"gated realistic {_pct(real.get('total_return'))} vs same-exposure best {_pct(same_best_ret)}",
        },
    }
    checks["all_passed"] = all(v["passed"] for k, v in checks.items() if isinstance(v, dict))
    return checks


def decision_ladder(
    *,
    gated: Dict[str, Any],
    checks: Dict[str, Any],
    concentration: Dict[str, Any],
) -> Dict[str, Any]:
    """Task 5 ladder. Promotion requires every gate; failures are named."""
    real = gated.get("realistic") or {}
    ind = gated.get("independent") or {}
    exact = gated.get("exact") or {}
    test = (gated.get("walk_forward") or {}).get("test") or {}
    reasons: List[str] = []

    n = int(ind.get("trade_count") or 0)
    accepted = int(real.get("accepted_trade_count") or 0)
    if _f(exact.get("expectancy"), -1.0) <= 0 or _f(real.get("total_return")) <= 0:
        return {"label": REJECT, "answer": NO, "reasons": ["expectancy or realistic return is not positive after costs"]}
    if n < MIN_INDEPENDENT_TRADES or accepted < MIN_ACCEPTED_TRADES:
        return {
            "label": LOW_FLOW, "answer": MAYBE,
            "reasons": [f"flow below paper-shadow floor (independent {n}<{MIN_INDEPENDENT_TRADES} or accepted {accepted}<{MIN_ACCEPTED_TRADES}) with positive quality"],
        }
    if int(test.get("trade_count") or 0) < MIN_TEST_TRADES:
        return {
            "label": NEED_MORE, "answer": MAYBE,
            "reasons": [f"walk-forward test split has {test.get('trade_count')} trades (<{MIN_TEST_TRADES}); cannot confirm out-of-sample"],
        }

    if _f(ind.get("max_drawdown"), -1.0) < MAX_INDEPENDENT_DD:
        reasons.append(f"independent-trade maxDD {_pct(ind.get('max_drawdown'))} breaches {_pct(MAX_INDEPENDENT_DD)} (caps would be doing the work)")
    if _f(real.get("max_drawdown"), -1.0) < MAX_REALISTIC_DD:
        reasons.append(f"realistic maxDD {_pct(real.get('max_drawdown'))} breaches {_pct(MAX_REALISTIC_DD)}")
    if _f(concentration.get("top_ticker_pct")) > MAX_TICKER_CONC:
        reasons.append(f"ticker concentration {concentration.get('top_ticker_pct')} > {MAX_TICKER_CONC}")
    if reasons:
        return {"label": PORTFOLIO_RISK, "answer": NO, "reasons": reasons}

    failed_checks = [k for k, v in checks.items() if isinstance(v, dict) and not v["passed"]]
    if _f(test.get("expectancy"), -1.0) <= 0:
        return {"label": REJECT, "answer": NO, "reasons": ["walk-forward test expectancy is not positive; in-sample regime story does not survive out-of-sample"]}
    if failed_checks:
        return {
            "label": OVERFIT, "answer": NO,
            "reasons": [f"anti-overfit checks failed: {', '.join(failed_checks)}"],
        }
    return {"label": CANDIDATE, "answer": YES, "reasons": ["all flow, portfolio, anti-overfit, and walk-forward gates passed"]}


def _variant_block(trades, start, end, splits):
    exact = lrr_report._slim_exact(lab.summarize_trades(trades, start=start, end=end))
    independent = correction._augment_metrics(portfolio.independent_trade_metrics(trades, start=start, end=end))
    realistic_raw = portfolio.realistic_portfolio_metrics(trades, start=start, end=end, config=portfolio.PortfolioConfig())
    rows = realistic_raw.get("daily_rows") or []
    same_spy = correction._same_exposure_benchmark("SPY", rows, start=start, end=end)
    same_qqq = correction._same_exposure_benchmark("QQQ", rows, start=start, end=end)
    return {
        "exact": exact,
        "independent": independent,
        "realistic": correction._augment_metrics(realistic_raw),
        "same_exposure_spy": same_spy,
        "same_exposure_qqq": same_qqq,
        "walk_forward": lrr_report._split_metrics(trades, splits),
        "regime_breakdown": lrr_report._regime_breakdown(trades),
        "year_breakdown": lrr_report._year_breakdown(trades),
    }


def build_report(
    *,
    start: Optional[str] = None,
    end: Optional[str] = None,
    stride: int = 1,
    skip_exit_sweep: bool = False,
) -> Dict[str, Any]:
    if start is None or end is None:
        span_start, span_end, _, _ = portfolio._primary_window_span()
        start = start or span_start
        end = end or span_end
    params = lab.StrategyParams()
    config = lab.BacktestConfig(universe_cap=140, date_stride=stride)
    splits = wf.make_walk_forward_splits(start=start, end=end).get("splits") or {}

    collected = portfolio.collect_trades_for_span(
        start, end, variants=COMPARISON_VARIANTS, params=params, config=config, cost_model=lab.BASE_COST,
    )
    benchmarks = {
        sym: correction._augment_metrics(portfolio.benchmark_metrics(sym, start=start, end=end, cost_model=lab.BASE_COST))
        for sym in ("SPY", "QQQ", "cash")
    }

    blocks: Dict[str, Any] = {}
    comparison: Dict[str, Any] = {}
    for variant in COMPARISON_VARIANTS:
        trades = collected["trades_by_variant"].get(variant, [])
        block = _variant_block(trades, start, end, splits)
        blocks[variant] = block
        comparison[variant] = {
            "trade_count": block["exact"].get("trade_count"),
            "expectancy": block["exact"].get("expectancy"),
            "win_rate": block["exact"].get("win_rate"),
            "rel_spy": block["exact"].get("rel_spy"),
            "independent_max_dd": block["independent"].get("max_drawdown"),
            "realistic_return": block["realistic"].get("total_return"),
            "realistic_max_dd": block["realistic"].get("max_drawdown"),
            "realistic_sharpe": block["realistic"].get("sharpe"),
            "exposure": block["realistic"].get("exposure_pct"),
            "same_exposure_spy_return": block["same_exposure_spy"].get("total_return"),
            "same_exposure_spy_sharpe": block["same_exposure_spy"].get("sharpe"),
            "same_exposure_qqq_return": block["same_exposure_qqq"].get("total_return"),
            "same_exposure_qqq_sharpe": block["same_exposure_qqq"].get("sharpe"),
        }

    gated_trades = collected["trades_by_variant"].get(VARIANT, [])
    base_trades = collected["trades_by_variant"].get(BASE_VARIANT, [])
    month_check = correction._works_outside_one_month(gated_trades)
    concentration = correction._concentration(gated_trades)

    # Exit-threshold sweep (Task 3): EXITS ONLY, regime set is frozen.
    # Diagnostic sensitivity; the a-priori default (hold 10, target 10%, no
    # trailing) remains the binding configuration for the verdict.
    exit_sweep: Dict[str, Any] = {"skipped": skip_exit_sweep}
    if not skip_exit_sweep:
        exit_sweep = {"skipped": False, "binding_config": "hold=10, target=0.10, trailing=None", "rows": {}}
        for hold, target, trailing in (
            (5, 0.10, None), (10, 0.10, None), (20, 0.10, None),
            (10, 0.08, None), (10, 0.15, None), (10, 0.10, 0.10), (20, 0.15, 0.10),
        ):
            key = f"hold{hold}_tgt{int(target * 100)}_trail{'none' if trailing is None else int(trailing * 100)}"
            if (hold, target, trailing) == (10, 0.10, None):
                trades_cfg = gated_trades
            else:
                p_cfg = dataclasses.replace(params, max_hold_days=hold, profit_target_pct=target, trailing_stop_pct=trailing)
                c_cfg = portfolio.collect_trades_for_span(
                    start, end, variants=(VARIANT,), params=p_cfg, config=config, cost_model=lab.BASE_COST,
                )
                trades_cfg = c_cfg["trades_by_variant"].get(VARIANT, [])
            m = lab.summarize_trades(trades_cfg, start=start, end=end)
            exit_sweep["rows"][key] = {
                "trade_count": m.get("trade_count"),
                "expectancy": m.get("expectancy"),
                "win_rate": m.get("win_rate"),
                "rel_spy": m.get("rel_spy"),
                "max_drawdown": m.get("max_drawdown"),
                "stop_hit_rate": m.get("stop_hit_rate"),
                "target_hit_rate": m.get("target_hit_rate"),
            }

    checks = anti_overfit_checks(
        gated=blocks[VARIANT],
        unrestricted=blocks[BASE_VARIANT],
        random_block=blocks["RANDOM_LIQUID"],
        gated_trades=gated_trades,
        unrestricted_trades=base_trades,
        month_check=month_check,
        concentration=concentration,
    )
    verdict = decision_ladder(gated=blocks[VARIANT], checks=checks, concentration=concentration)

    return {
        "kind": "lrr_regime_gated",
        "version": VERSION,
        "generated_at": _utc_now(),
        "research_only": True,
        "status": "RESEARCH_ONLY",
        "top_line": f"DID LRR_REGIME_GATED EARN PAPER-SHADOW? {verdict['answer']}",
        "answer": verdict["answer"],
        "verdict": verdict["label"] if verdict["label"] == CANDIDATE else NO_VARIANT_READY,
        "variant_verdict": verdict,
        "signal_window": {"start": start, "end": end},
        "regime_gate_spec": gate_spec(),
        "params": params.as_dict(),
        "config": config.as_dict(),
        "collection": {k: v for k, v in collected.items() if k != "trades_by_variant"},
        "walk_forward_splits": splits,
        "benchmarks": benchmarks,
        "comparison_table": comparison,
        "lrr_regime_gated": blocks[VARIANT],
        "unrestricted_lrr": {
            k: blocks[BASE_VARIANT][k] for k in ("exact", "independent", "realistic", "walk_forward", "year_breakdown")
        },
        "exit_sweep": exit_sweep,
        "month_check": month_check,
        "concentration": concentration,
        "anti_overfit_checks": checks,
        "paper_shadow": {
            "proposal_created": verdict["answer"] == YES,
            "proposal_doc": str(PROPOSAL_DOC) if verdict["answer"] == YES else None,
            "status": CANDIDATE if verdict["answer"] == YES else NO_VARIANT_READY,
            "note": "Proposal doc only; nothing is activated.",
        },
        "safety": lab.safety_confirmations(),
    }


def render_text(res: Dict[str, Any]) -> List[str]:
    lines = [
        f"LRR REGIME GATED (PHASE 1I.1) - {res['generated_at']}",
        lab.RESEARCH_DISCLAIMER,
        res["top_line"],
        f"verdict={res['variant_verdict']['label']} window={res['signal_window']['start']}..{res['signal_window']['end']}",
        f"allowed_regimes={res['regime_gate_spec']['allowed_regimes']}",
        "",
        f"{'variant':28s} {'n':>5s} {'exp':>8s} {'indDD':>8s} {'realRet':>8s} {'realDD':>8s} {'sharpe':>7s} {'sameSh':>7s}",
    ]
    for variant, row in res["comparison_table"].items():
        same_sh = max(_f(row.get("same_exposure_spy_sharpe"), -9.0), _f(row.get("same_exposure_qqq_sharpe"), -9.0))
        lines.append(
            f"{variant:28s} {row['trade_count'] or 0:>5d} {_pct(row['expectancy']):>8s} {_pct(row['independent_max_dd']):>8s} "
            f"{_pct(row['realistic_return']):>8s} {_pct(row['realistic_max_dd']):>8s} {str(row['realistic_sharpe']):>7s} {round(same_sh, 3):>7}"
        )
    lines += ["", "anti-overfit checks:"]
    for name, c in res["anti_overfit_checks"].items():
        if isinstance(c, dict):
            lines.append(f"  [{'PASS' if c['passed'] else 'FAIL'}] {name}: {c['detail']}")
    lines += ["", f"ladder reasons: {'; '.join(res['variant_verdict']['reasons'])}"]
    return lines


def render_gate_spec_doc(spec: Dict[str, Any]) -> str:
    return "\n".join([
        "# LRR Regime Gate Specification (Phase 1I.1, fixed a priori)",
        "",
        f"Fixed on: {spec['fixed_on']} — BEFORE the gated variant was ever run.",
        "",
        f"Derived from: {spec['derived_from']}",
        "",
        "## Allowed regimes",
        "",
        *[f"- `{r}`" for r in spec["allowed_regimes"]],
        "",
        "## Blocked regimes",
        "",
        *[f"- `{r}`" for r in spec["blocked_regimes"]],
        "",
        spec["bull_trend_note"],
        "",
        "## Classifier (as-of only)",
        "",
        f"Module: `{spec['classifier']['module']}`",
        "",
        *[f"- {x}" for x in spec["classifier"]["as_of_inputs"]],
        "",
        spec["classifier"]["no_future_data"] + ".",
        "",
        "## Anti-overfit doctrine",
        "",
        spec["anti_overfit_doctrine"],
        "",
    ])


def render_doc(res: Dict[str, Any]) -> str:
    v = res["variant_verdict"]
    gated = res["lrr_regime_gated"]
    lines = [
        "# LRR_REGIME_GATED Results (Phase 1I.1)",
        "",
        f"Generated: {res['generated_at']}",
        "",
        lab.RESEARCH_DISCLAIMER,
        "",
        f"## {res['top_line']}",
        "",
        f"Ladder verdict: **{v['label']}** — {'; '.join(v['reasons'])}",
        "",
        f"Signal window: `{res['signal_window']['start']}` to `{res['signal_window']['end']}`. "
        f"Allowed regimes (fixed a priori): `{', '.join(res['regime_gate_spec']['allowed_regimes'])}`.",
        "",
        "## Comparison Table",
        "",
        "| Variant | Trades | Expectancy | Ind MaxDD | Real Return | Real MaxDD | Sharpe | Same-Exp SPY Sharpe | Same-Exp QQQ Sharpe |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for variant, row in res["comparison_table"].items():
        lines.append(
            f"| {variant} | {row['trade_count']} | {_pct(row['expectancy'])} | {_pct(row['independent_max_dd'])} | "
            f"{_pct(row['realistic_return'])} | {_pct(row['realistic_max_dd'])} | {row['realistic_sharpe']} | "
            f"{row['same_exposure_spy_sharpe']} | {row['same_exposure_qqq_sharpe']} |"
        )
    lines += [
        "",
        f"Benchmarks: SPY {_pct(res['benchmarks']['SPY'].get('total_return'))} (Sharpe {res['benchmarks']['SPY'].get('sharpe')}), "
        f"QQQ {_pct(res['benchmarks']['QQQ'].get('total_return'))} (Sharpe {res['benchmarks']['QQQ'].get('sharpe')}), cash +0.00%.",
        "",
        "## Anti-Overfit Checks (Task 4)",
        "",
        "| Check | Result | Detail |",
        "|---|---|---|",
    ]
    for name, c in res["anti_overfit_checks"].items():
        if isinstance(c, dict):
            lines.append(f"| {name} | {'PASS' if c['passed'] else 'FAIL'} | {c['detail']} |")
    lines += ["", "## Walk-Forward (a-priori rules; decay diagnostic)", "", "| Split | Trades | Expectancy | Rel SPY | MaxDD |", "|---|---:|---:|---:|---:|"]
    for name, row in (gated.get("walk_forward") or {}).items():
        lines.append(f"| {name} | {row.get('trade_count')} | {_pct(row.get('expectancy'))} | {_pct(row.get('rel_spy'))} | {_pct(row.get('max_drawdown'))} |")
    lines += ["", "## Exit Sweep (exits only; regime set frozen; diagnostic)", ""]
    sweep = res.get("exit_sweep") or {}
    if sweep.get("skipped"):
        lines.append("Skipped.")
    else:
        lines += [f"Binding config: `{sweep['binding_config']}`.", "", "| Config | Trades | Expectancy | Win | MaxDD | Stop% | Target% |", "|---|---:|---:|---:|---:|---:|---:|"]
        for key, row in sweep["rows"].items():
            lines.append(
                f"| {key} | {row['trade_count']} | {_pct(row['expectancy'])} | {row['win_rate']} | "
                f"{_pct(row['max_drawdown'])} | {row['stop_hit_rate']} | {row['target_hit_rate']} |"
            )
    lines += ["", "## Regime / Year Breakdown (gated variant)", "", "| Regime | Trades | Expectancy | Win |", "|---|---:|---:|---:|"]
    for label, row in (gated.get("regime_breakdown") or {}).items():
        lines.append(f"| {label} | {row['trade_count']} | {_pct(row['expectancy'])} | {row['win_rate']} |")
    lines += ["", "| Year | Trades | Expectancy | Win | Compounded |", "|---|---:|---:|---:|---:|"]
    for year, row in (gated.get("year_breakdown") or {}).items():
        lines.append(f"| {year} | {row['trade_count']} | {_pct(row['expectancy'])} | {row['win_rate']} | {_pct(row['compounded_return'])} |")
    lines += [
        "",
        "## Paper-Shadow Status",
        "",
        f"**{res['paper_shadow']['status']}** (proposal created: `{res['paper_shadow']['proposal_created']}`)",
        "",
        "## Safety",
        "",
        "No paper signals, broker orders, trade proposals (doc-only), production thresholds, Gatekeeper/Veto logic,",
        "execution/governance/live-capital modules, historical evidence, or SHORT_A frozen status were changed.",
        "LRR_REGIME_GATED exists only in the research lab's variant map (not in lab defaults, not in the production",
        "strategy registry). Regime labels are strictly as-of.",
        "",
    ]
    return "\n".join(lines)


def render_proposal_doc(res: Dict[str, Any]) -> str:
    gated = res["lrr_regime_gated"]
    real = gated.get("realistic") or {}
    test = (gated.get("walk_forward") or {}).get("test") or {}
    return "\n".join([
        "# Paper-Shadow Promotion Proposal: LRR_REGIME_GATED (Phase 1I.1)",
        "",
        "Manual review required. This document does not activate anything.",
        "",
        "## Strategy",
        "",
        f"- Regime gate (fixed a priori): `{', '.join(res['regime_gate_spec']['allowed_regimes'])}`",
        "- Base rules: LEADER_RESET_RECLAIM (prior leader -> controlled reset -> 10/20 EMA reclaim, close strength,",
        "  reclaim-low/ATR stop 4-10%, +10% target, 10d max hold, next-open entry).",
        "",
        "## Evidence",
        "",
        f"- Exact: {gated['exact'].get('trade_count')} trades, expectancy {_pct(gated['exact'].get('expectancy'))}, rel-SPY {_pct(gated['exact'].get('rel_spy'))}",
        f"- Walk-forward test: n={test.get('trade_count')}, expectancy {_pct(test.get('expectancy'))}",
        f"- Realistic: return {_pct(real.get('total_return'))}, maxDD {_pct(real.get('max_drawdown'))}, Sharpe {real.get('sharpe')}",
        f"- Same-exposure SPY/QQQ Sharpe: {gated['same_exposure_spy'].get('sharpe')} / {gated['same_exposure_qqq'].get('sharpe')}",
        "",
        "## Risk limits and kill criteria (paper-only)",
        "",
        "- Lab portfolio caps: 5 concurrent, 10% position, 30% sector, 50% gross; no margin.",
        "- Kill if paper-shadow expectancy after 30 trades <= 0, drawdown-equivalent exceeds -10%,",
        "  any month contributes >65% of gains, or the regime classifier inputs change.",
        "- Paper-only: signals logged for evidence; no orders of any kind.",
        "",
    ])


def write_outputs(res: Dict[str, Any]) -> None:
    for p in (OUT_JSON, OUT_TXT, OUT_DOC, GATE_SPEC_JSON, GATE_SPEC_DOC):
        p.parent.mkdir(parents=True, exist_ok=True)
    spec = res["regime_gate_spec"]
    GATE_SPEC_JSON.write_text(json.dumps({**spec, "generated_at": res["generated_at"]}, indent=2, default=str), encoding="utf-8")
    GATE_SPEC_DOC.write_text(render_gate_spec_doc(spec) + "\n", encoding="utf-8")
    OUT_JSON.write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    OUT_TXT.write_text("\n".join(render_text(res)) + "\n", encoding="utf-8")
    OUT_DOC.write_text(render_doc(res) + "\n", encoding="utf-8")
    if res["paper_shadow"]["proposal_created"]:
        PROPOSAL_DOC.write_text(render_proposal_doc(res) + "\n", encoding="utf-8")


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Phase 1I.1 LRR_REGIME_GATED strict a-priori test (research-only)")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--stride", type=int, default=1)
    ap.add_argument("--skip-exit-sweep", action="store_true")
    args = ap.parse_args(argv)
    res = build_report(start=args.start, end=args.end, stride=args.stride, skip_exit_sweep=args.skip_exit_sweep)
    write_outputs(res)
    print("\n".join(render_text(res)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

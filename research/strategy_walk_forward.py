#!/usr/bin/env python3
"""
research/strategy_walk_forward.py - Phase 1H walk-forward validation.

Research-only wrapper around strategy_research_lab. Parameter candidates are
scored on train, selected on validation, and then evaluated once on untouched
test data.
"""
from __future__ import annotations

import argparse
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from research import strategy_lab_data as d
from research import strategy_research_lab as lab

ROOT = Path(__file__).resolve().parents[1]
OUT_JSON = ROOT / "cache" / "research" / "strategy_walk_forward_latest.json"
OUT_TXT = ROOT / "logs" / "strategy_walk_forward_latest.txt"
OUT_DOC = ROOT / "docs" / "research" / "STRATEGY_WALK_FORWARD_RESULTS.md"
EXACT_OUT_JSON = ROOT / "cache" / "research" / "strategy_walk_forward_exact_latest.json"
EXACT_OUT_TXT = ROOT / "logs" / "strategy_walk_forward_exact_latest.txt"
EXACT_OUT_DOC = ROOT / "docs" / "research" / "STRATEGY_WALK_FORWARD_EXACT_RESULTS.md"

VERSION = "STRATEGY_WALK_FORWARD_V2"


def make_walk_forward_splits(
    *,
    start: str = "2024-01-01",
    end: Optional[str] = None,
    min_block_days: int = 60,
) -> Dict[str, Any]:
    cal = d.benchmark_calendar()
    latest = pd.Timestamp(end).normalize() if end else pd.Timestamp(cal.max()).normalize()
    dates = [pd.Timestamp(x).normalize() for x in cal[(cal >= pd.Timestamp(start)) & (cal <= latest)]]
    if len(dates) < min_block_days * 3:
        # Short history fallback: rolling thirds of whatever is retained.
        dates = [pd.Timestamp(x).normalize() for x in cal[-max(len(cal), min_block_days * 3):]]
    if len(dates) < 90:
        return {
            "mode": "insufficient_history",
            "reason": f"only {len(dates)} benchmark dates retained",
            "splits": {},
        }
    n = len(dates)
    train_end = max(min_block_days, int(n * 0.50))
    val_end = max(train_end + min_block_days, int(n * 0.75))
    val_end = min(val_end, n - min_block_days)
    splits = {
        "train": {"start": str(dates[0].date()), "end": str(dates[train_end - 1].date())},
        "validation": {"start": str(dates[train_end].date()), "end": str(dates[val_end - 1].date())},
        "test": {"start": str(dates[val_end].date()), "end": str(dates[-1].date())},
    }
    mode = "train_validation_test" if all(
        len(d.trading_dates_between(s["start"], s["end"])) >= min_block_days
        for s in splits.values()
    ) else "rolling_blocks_limited_history"
    return {"mode": mode, "splits": splits, "n_dates": n}


def candidate_params() -> List[lab.StrategyParams]:
    return [
        lab.StrategyParams(),
        lab.StrategyParams(sector_rs_threshold=0.05, momentum_20_threshold=0.06, momentum_60_threshold=0.12, extension_cap=0.30),
        lab.StrategyParams(sector_rs_threshold=0.10, momentum_20_threshold=0.10, momentum_60_threshold=0.20, extension_cap=0.22),
        lab.StrategyParams(pullback_depth=0.08, stop_loss_pct=0.05, profit_target_pct=0.08, max_hold_days=5),
        lab.StrategyParams(volume_expansion_threshold=1.2, stop_loss_pct=0.08, profit_target_pct=0.15, max_hold_days=20),
        lab.StrategyParams(correction_rs_lookback=40, correction_ema_reclaim=10, max_hold_days=5, stop_loss_pct=0.05, profit_target_pct=0.08),
        lab.StrategyParams(correction_rs_lookback=60, correction_max_pullback=0.18, correction_market_dd_threshold=0.04),
        lab.StrategyParams(correction_volume_expansion_threshold=1.20, correction_atr_stop_multiple=1.5, max_hold_days=20, stop_loss_pct=0.08, profit_target_pct=0.15),
    ]


def _score_metric(m: Dict[str, Any]) -> float:
    if not m or not m.get("trade_count"):
        return -999.0
    exp = float(m.get("expectancy") or 0.0)
    rel = float(m.get("rel_spy") or 0.0) + float(m.get("rel_qqq") or 0.0)
    dd = abs(float(m.get("max_drawdown") or 0.0))
    n = min(float(m.get("trade_count") or 0.0), 80.0)
    return exp * 120.0 + rel * 30.0 + n * 0.01 - dd * 2.0


def _run_split(split_name: str, split: Dict[str, str], params: lab.StrategyParams, config: lab.BacktestConfig) -> Dict[str, Any]:
    return lab.run_backtest_window(
        split_name,
        split["start"],
        split["end"],
        variants=lab.DEFAULT_VARIANTS,
        params=params,
        config=config,
        cost_models=(lab.BASE_COST,),
    )


def build_walk_forward(
    *,
    config: lab.BacktestConfig = lab.BacktestConfig(universe_cap=110, date_stride=3),
) -> Dict[str, Any]:
    sampled = config.date_stride > 1
    split_info = make_walk_forward_splits()
    if not split_info.get("splits"):
        return {
            "kind": "strategy_walk_forward",
            "version": VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "research_only": True,
            "mode": split_info.get("mode"),
            "sampled": sampled,
            "exact": not sampled,
            "verdict": lab.VERDICT_NEED_MORE,
            "reason": split_info.get("reason"),
        }
    splits = split_info["splits"]
    tried: List[Dict[str, Any]] = []
    params_list = candidate_params()

    # Train: rank each parameter set for each variant.
    train_runs = []
    for idx, params in enumerate(params_list):
        run = _run_split("train", splits["train"], params, config)
        train_runs.append((idx, params, run))
        for variant in lab.DEFAULT_VARIANTS:
            m = run["by_cost"]["base_cost"].get(variant, {})
            tried.append({
                "phase": "train",
                "param_id": idx,
                "variant": variant,
                "score": round(_score_metric(m), 6),
                "metrics": {k: m.get(k) for k in ("trade_count", "expectancy", "rel_spy", "rel_qqq", "max_drawdown")},
            })

    top_candidates: Dict[str, List[int]] = {}
    for variant in lab.DEFAULT_VARIANTS:
        ranked = sorted(
            ((idx, _score_metric(run["by_cost"]["base_cost"].get(variant, {}))) for idx, _, run in train_runs),
            key=lambda x: x[1],
            reverse=True,
        )
        top_candidates[variant] = [idx for idx, _ in ranked[:2]]

    # Validation: choose parameters per variant without looking at test.
    validation_runs: Dict[int, Dict[str, Any]] = {}
    selected: Dict[str, Dict[str, Any]] = {}
    for variant, ids in top_candidates.items():
        best = None
        for idx in ids:
            if idx not in validation_runs:
                validation_runs[idx] = _run_split("validation", splits["validation"], params_list[idx], config)
            m = validation_runs[idx]["by_cost"]["base_cost"].get(variant, {})
            score = _score_metric(m)
            tried.append({
                "phase": "validation",
                "param_id": idx,
                "variant": variant,
                "score": round(score, 6),
                "metrics": {k: m.get(k) for k in ("trade_count", "expectancy", "rel_spy", "rel_qqq", "max_drawdown")},
            })
            if best is None or score > best["validation_score"]:
                best = {"param_id": idx, "validation_score": score, "validation_metrics": m}
        if best is not None:
            selected[variant] = best

    # Test: untouched until after selection.
    test_runs: Dict[int, Dict[str, Any]] = {}
    final: Dict[str, Dict[str, Any]] = {}
    for variant, sel in selected.items():
        idx = sel["param_id"]
        if idx not in test_runs:
            test_runs[idx] = _run_split("test", splits["test"], params_list[idx], config)
        test_m = test_runs[idx]["by_cost"]["base_cost"].get(variant, {})
        train_m = train_runs[idx][2]["by_cost"]["base_cost"].get(variant, {})
        val_m = sel["validation_metrics"]
        verdict, blockers = _wf_verdict(train_m, val_m, test_m)
        train_exp = float(train_m.get("expectancy") or 0.0)
        test_exp = float(test_m.get("expectancy") or 0.0)
        train_scores = {
            t_idx: round(_score_metric(run["by_cost"]["base_cost"].get(variant, {})), 6)
            for t_idx, _, run in train_runs
        }
        final[variant] = {
            "param_id": idx,
            "params": params_list[idx].as_dict(),
            "train": {k: train_m.get(k) for k in ("trade_count", "expectancy", "rel_spy", "rel_qqq", "max_drawdown")},
            "validation": {k: val_m.get(k) for k in ("trade_count", "expectancy", "rel_spy", "rel_qqq", "max_drawdown")},
            "test": {k: test_m.get(k) for k in ("trade_count", "expectancy", "rel_spy", "rel_qqq", "max_drawdown")},
            "test_decay": round(train_exp - test_exp, 6),
            "decay_ratio": round((train_exp - test_exp) / abs(train_exp), 4) if train_exp else None,
            "overfit_risk": _wf_overfit_risk(train_m, val_m, test_m),
            "parameter_stability": {
                "selected_param_id": idx,
                "selected_is_default": idx == 0,
                "train_score_by_param": train_scores,
                "train_top2_param_ids": top_candidates.get(variant, []),
                "stable": idx in (top_candidates.get(variant) or [])[:1],
            },
            "verdict": verdict,
            "blockers": blockers,
        }

    ranked = sorted(final.items(), key=lambda kv: _score_metric(kv[1]["test"]), reverse=True)
    best_variant = ranked[0][0] if ranked else None
    best = final.get(best_variant) if best_variant else None
    overall = lab.VERDICT_NEED_MORE
    if best and best["verdict"] == lab.VERDICT_EDGE:
        overall = lab.VERDICT_EDGE
    elif best and best["verdict"] == lab.VERDICT_OVERFIT_RISK:
        overall = lab.VERDICT_OVERFIT_RISK
    if sampled and overall == lab.VERDICT_EDGE:
        overall = lab.VERDICT_OVERFIT_RISK
    return {
        "kind": "strategy_walk_forward",
        "version": VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "research_only": True,
        "status": "RESEARCH_ONLY",
        "mode": split_info.get("mode"),
        "sampled": sampled,
        "exact": not sampled,
        "date_stride": config.date_stride,
        "splits": splits,
        "config": config.as_dict(),
        "selection_rule": "train ranks candidates; validation selects; test is evaluated only after selection",
        "test_used_for_selection": False,
        "tried_parameter_sets": [p.as_dict() for p in params_list],
        "tried_rows": tried,
        "selected": selected,
        "final_by_variant": final,
        "best_variant": best_variant,
        "best_variant_verdict": None if best is None else best["verdict"],
        "verdict": overall,
        "paper_shadow": {
            "proposal_created": False,
            "status": "NO_VARIANT_READY_FOR_PAPER_SHADOW",
        },
    }


def _wf_overfit_risk(train: Dict[str, Any], val: Dict[str, Any], test: Dict[str, Any]) -> str:
    train_exp = float(train.get("expectancy") or 0.0)
    val_exp = float(val.get("expectancy") or 0.0)
    test_exp = float(test.get("expectancy") or 0.0)
    if train_exp > 0 and val_exp > 0 and test_exp <= 0:
        return "HIGH_TEST_DECAY"
    if train_exp > 0 and val_exp <= 0:
        return "HIGH_TRAIN_ONLY"
    if int(test.get("trade_count") or 0) < 20:
        return "HIGH_LOW_TEST_SAMPLE"
    if test_exp > 0 and val_exp > 0:
        return "MODERATE_NEEDS_PAPER_SHADOW_GATE"
    return "ELEVATED"


def _wf_verdict(train: Dict[str, Any], val: Dict[str, Any], test: Dict[str, Any]) -> tuple[str, List[str]]:
    blockers: List[str] = []
    if int(test.get("trade_count") or 0) < 20:
        blockers.append("test_trade_count_below_20")
    if float(train.get("expectancy") or 0.0) <= 0:
        blockers.append("train_expectancy_not_positive")
    if float(val.get("expectancy") or 0.0) <= 0:
        blockers.append("validation_expectancy_not_positive")
    if float(test.get("expectancy") or 0.0) <= 0:
        blockers.append("test_expectancy_not_positive")
    if float(test.get("rel_spy") or 0.0) <= 0 or float(test.get("rel_qqq") or 0.0) <= 0:
        blockers.append("test_does_not_beat_spy_qqq")
    if float(test.get("max_drawdown") or 0.0) < -0.20:
        blockers.append("test_drawdown_too_high")
    if not blockers:
        return lab.VERDICT_EDGE, []
    if "test_trade_count_below_20" in blockers:
        return lab.VERDICT_NEED_MORE, blockers
    if float(train.get("expectancy") or 0.0) > 0 and float(val.get("expectancy") or 0.0) > 0:
        return lab.VERDICT_OVERFIT_RISK, blockers
    return lab.VERDICT_REJECT, blockers


def render_text(res: Dict[str, Any]) -> List[str]:
    lines = [
        f"STRATEGY WALK-FORWARD - {res.get('generated_at')}",
        lab.RESEARCH_DISCLAIMER,
        f"mode={res.get('mode')} exact={res.get('exact', False)} sampled={res.get('sampled', False)} "
        f"verdict={res.get('verdict')} best={res.get('best_variant')} best_verdict={res.get('best_variant_verdict')}",
        "",
    ]
    if res.get("reason"):
        lines.append(f"reason={res['reason']}")
        return lines
    lines.append("SPLITS:")
    for name, split in (res.get("splits") or {}).items():
        lines.append(f"  {name}: {split['start']} -> {split['end']}")
    lines += ["", f"{'variant':34s} {'verdict':30s} {'param':>5s} {'test_n':>7s} {'test_exp':>10s}"]
    for variant, row in (res.get("final_by_variant") or {}).items():
        test = row.get("test") or {}
        lines.append(f"{variant:34s} {row.get('verdict'):30s} {row.get('param_id'):5d} {int(test.get('trade_count') or 0):7d} {float(test.get('expectancy') or 0.0):10.4f}")
    return lines


def render_doc(res: Dict[str, Any]) -> str:
    lines = [
        "# Strategy Walk-Forward Results",
        "",
        f"Generated: {res.get('generated_at')}",
        "",
        lab.RESEARCH_DISCLAIMER,
        "",
        f"Mode: `{res.get('mode')}` · exact: `{res.get('exact', False)}` · sampled: `{res.get('sampled', False)}`",
        "",
        "Selection rule: train ranks candidates; validation selects; test is evaluated only after selection.",
        "",
        f"Test used for selection: `{res.get('test_used_for_selection', False)}`",
        "",
    ]
    if res.get("reason"):
        lines += [f"Verdict: `{res.get('verdict')}`", "", res["reason"], ""]
        return "\n".join(lines)
    lines += [
        "## Splits",
        "",
    ]
    for name, split in (res.get("splits") or {}).items():
        lines.append(f"- {name}: {split['start']} to {split['end']}")
    lines += [
        "",
        "## Final Test Results",
        "",
        "| Variant | Verdict | Param | Train Exp | Val Exp | Test Exp | Test Trades | Test Decay | Overfit Risk | Stable | Blockers |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|---|---|",
    ]
    for variant, row in (res.get("final_by_variant") or {}).items():
        stability = row.get("parameter_stability") or {}
        lines.append(
            f"| {variant} | {row['verdict']} | {row['param_id']} | "
            f"{float((row.get('train') or {}).get('expectancy') or 0.0):+.4f} | "
            f"{float((row.get('validation') or {}).get('expectancy') or 0.0):+.4f} | "
            f"{float((row.get('test') or {}).get('expectancy') or 0.0):+.4f} | "
            f"{int((row.get('test') or {}).get('trade_count') or 0)} | "
            f"{float(row.get('test_decay') or 0.0):+.4f} | "
            f"{row.get('overfit_risk', 'n/a')} | "
            f"{stability.get('stable', 'n/a')} | "
            f"{', '.join(row.get('blockers') or []) or 'none'} |"
        )
    lines += [
        "",
        "## Paper-Shadow Decision",
        "",
        res.get("paper_shadow", {}).get("status", "NO_VARIANT_READY_FOR_PAPER_SHADOW"),
        "",
    ]
    return "\n".join(lines) + "\n"


def write_outputs(res: Dict[str, Any], *, exact: bool = False) -> None:
    json_path, txt_path, doc_path = (
        (EXACT_OUT_JSON, EXACT_OUT_TXT, EXACT_OUT_DOC) if exact else (OUT_JSON, OUT_TXT, OUT_DOC)
    )
    json_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    txt_path.write_text("\n".join(render_text(res)) + "\n", encoding="utf-8")
    doc_path.write_text(render_doc(res), encoding="utf-8")


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Strategy walk-forward validation (research-only)")
    ap.add_argument("--universe-mode", default="research_core")
    ap.add_argument("--universe-cap", type=int, default=None)
    ap.add_argument("--date-stride", type=int, default=None)
    ap.add_argument("--exact", action="store_true",
                    help="evaluate every trading date (stride 1) and write the *_exact_* artifacts")
    args = ap.parse_args(argv)
    if args.exact:
        cap = args.universe_cap or 140
        stride = 1
    else:
        cap = args.universe_cap or 110
        stride = max(1, args.date_stride if args.date_stride is not None else 3)
    config = lab.BacktestConfig(universe_mode=args.universe_mode, universe_cap=cap, date_stride=stride)
    res = build_walk_forward(config=config)
    write_outputs(res, exact=args.exact)
    print("\n".join(render_text(res)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

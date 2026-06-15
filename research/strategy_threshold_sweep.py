#!/usr/bin/env python3
"""
research/strategy_threshold_sweep.py - Phase 1H controlled threshold sweep.

Research-only. Limited grid, train/validation/test split, complexity penalty,
all tried parameter sets preserved, no production threshold mutation.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from research import strategy_research_lab as lab
from research import strategy_walk_forward as wf

ROOT = Path(__file__).resolve().parents[1]
OUT_JSON = ROOT / "cache" / "research" / "strategy_threshold_sweep_latest.json"
OUT_TXT = ROOT / "logs" / "strategy_threshold_sweep_latest.txt"
OUT_DOC = ROOT / "docs" / "research" / "STRATEGY_THRESHOLD_SWEEP_RESULTS.md"
EXACT_OUT_JSON = ROOT / "cache" / "research" / "strategy_threshold_sweep_exact_latest.json"
EXACT_OUT_TXT = ROOT / "logs" / "strategy_threshold_sweep_exact_latest.txt"
EXACT_OUT_DOC = ROOT / "docs" / "research" / "STRATEGY_THRESHOLD_SWEEP_EXACT_RESULTS.md"

VERSION = "STRATEGY_THRESHOLD_SWEEP_V2"
SWEEP_VARIANTS = (
    "CORRECTION_LEADER_RECLAIM",
    "SNIPER_NO_ATR_CONTRACTION",
    "RECALL_SHADOW_RS_MOMENTUM",
    "RECALL_SHADOW_PULLBACK",
    "POWER_TREND_EXTENSION",
    "QQQ_TECH_TACTICAL_SHORT",
    "SIMPLE_SECTOR_RS",
    "SIMPLE_MOM_20_60",
    "RANDOM_LIQUID",
)


def limited_grid() -> List[lab.StrategyParams]:
    """Hand-bounded grid over allowed knobs only.

    This deliberately avoids a cartesian explosion. Each row is a predeclared
    hypothesis, not an optimizer fishing expedition.
    """
    return [
        lab.StrategyParams(),
        lab.StrategyParams(sector_rs_threshold=0.05),
        lab.StrategyParams(sector_rs_threshold=0.12),
        lab.StrategyParams(momentum_20_threshold=0.06, momentum_60_threshold=0.12),
        lab.StrategyParams(momentum_20_threshold=0.12, momentum_60_threshold=0.25),
        lab.StrategyParams(extension_cap=0.20),
        lab.StrategyParams(extension_cap=0.30),
        lab.StrategyParams(pullback_depth=0.08),
        lab.StrategyParams(pullback_depth=0.16),
        lab.StrategyParams(volume_expansion_threshold=1.2),
        lab.StrategyParams(stop_loss_pct=0.05, profit_target_pct=0.08, max_hold_days=5),
        lab.StrategyParams(stop_loss_pct=0.08, profit_target_pct=0.15, max_hold_days=20),
        lab.StrategyParams(min_avg_dvol=10_000_000.0),
        lab.StrategyParams(correction_rs_lookback=40),
        lab.StrategyParams(correction_rs_lookback=60),
        lab.StrategyParams(correction_max_pullback=0.18),
        lab.StrategyParams(correction_max_pullback=0.30),
        lab.StrategyParams(correction_ema_reclaim=10),
        lab.StrategyParams(correction_volume_dryup_threshold=0.75),
        lab.StrategyParams(correction_volume_expansion_threshold=1.20),
        lab.StrategyParams(max_hold_days=5, stop_loss_pct=0.05, profit_target_pct=0.08),
        lab.StrategyParams(max_hold_days=20, stop_loss_pct=0.08, profit_target_pct=0.15, trailing_stop_pct=0.08),
        lab.StrategyParams(correction_atr_stop_multiple=1.5),
        lab.StrategyParams(correction_atr_stop_multiple=2.5),
        lab.StrategyParams(correction_market_dd_threshold=0.04),
        lab.StrategyParams(correction_market_dd_threshold=0.07),
    ]


def complexity_penalty(params: lab.StrategyParams) -> float:
    base = lab.StrategyParams().as_dict()
    changed = sum(1 for k, v in params.as_dict().items() if v != base[k])
    return round(changed * 0.0025, 6)


def _score(m: Dict[str, Any], penalty: float = 0.0) -> float:
    if not m or not m.get("trade_count"):
        return -999.0 - penalty
    exp = float(m.get("expectancy") or 0.0)
    rel = float(m.get("rel_spy") or 0.0) + float(m.get("rel_qqq") or 0.0)
    dd = abs(float(m.get("max_drawdown") or 0.0))
    n = min(float(m.get("trade_count") or 0.0), 100.0)
    return round(exp * 130.0 + rel * 25.0 + n * 0.006 - dd * 2.0 - penalty, 6)


def _run(split_name: str, split: Dict[str, str], params: lab.StrategyParams, config: lab.BacktestConfig) -> Dict[str, Any]:
    return lab.run_backtest_window(
        split_name,
        split["start"],
        split["end"],
        variants=SWEEP_VARIANTS,
        params=params,
        config=config,
        cost_models=(lab.BASE_COST,),
    )


def build_threshold_sweep(
    *,
    config: lab.BacktestConfig = lab.BacktestConfig(universe_cap=100, date_stride=4),
) -> Dict[str, Any]:
    sampled = config.date_stride > 1
    split_info = wf.make_walk_forward_splits()
    if not split_info.get("splits"):
        return {
            "kind": "strategy_threshold_sweep",
            "version": VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "research_only": True,
            "sampled": sampled,
            "exact": not sampled,
            "verdict": lab.VERDICT_NEED_MORE,
            "reason": split_info.get("reason"),
        }
    splits = split_info["splits"]
    grid = limited_grid()
    tried: List[Dict[str, Any]] = []
    runs: Dict[tuple[str, int], Dict[str, Any]] = {}

    for idx, params in enumerate(grid):
        penalty = complexity_penalty(params)
        for split_name in ("train", "validation", "test"):
            run = _run(split_name, splits[split_name], params, config)
            runs[(split_name, idx)] = run
            for variant in SWEEP_VARIANTS:
                m = run["by_cost"]["base_cost"].get(variant, {})
                tried.append({
                    "split": split_name,
                    "param_id": idx,
                    "variant": variant,
                    "params": params.as_dict(),
                    "complexity_penalty": penalty,
                    "score": _score(m, penalty if split_name != "test" else 0.0),
                    "metrics": {
                        "trade_count": m.get("trade_count"),
                        "expectancy": m.get("expectancy"),
                        "rel_spy": m.get("rel_spy"),
                        "rel_qqq": m.get("rel_qqq"),
                        "max_drawdown": m.get("max_drawdown"),
                    },
                })

    selected: Dict[str, Dict[str, Any]] = {}
    for variant in SWEEP_VARIANTS:
        # Selection uses validation only. Test rows are reported after the fact.
        candidates = [
            row for row in tried
            if row["split"] == "validation" and row["variant"] == variant
        ]
        candidates.sort(key=lambda r: r["score"], reverse=True)
        if not candidates:
            continue
        best = candidates[0]
        pid = best["param_id"]
        test_m = runs[("test", pid)]["by_cost"]["base_cost"].get(variant, {})
        train_m = runs[("train", pid)]["by_cost"]["base_cost"].get(variant, {})
        selected[variant] = {
            "param_id": pid,
            "params": best["params"],
            "selected_on": "validation_score_after_complexity_penalty",
            "test_used_for_selection": False,
            "validation_score": best["score"],
            "train_metrics": {k: train_m.get(k) for k in ("trade_count", "expectancy", "rel_spy", "rel_qqq", "max_drawdown")},
            "validation_metrics": best["metrics"],
            "test_metrics": {k: test_m.get(k) for k in ("trade_count", "expectancy", "rel_spy", "rel_qqq", "max_drawdown")},
            "overfit_risk": _overfit_risk(train_m, best["metrics"], test_m),
        }

    ranked = sorted(
        selected.items(),
        key=lambda kv: _score(kv[1]["test_metrics"]),
        reverse=True,
    )
    best_variant = ranked[0][0] if ranked else None
    return {
        "kind": "strategy_threshold_sweep",
        "version": VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "research_only": True,
        "status": "RESEARCH_ONLY",
        "sampled": sampled,
        "exact": not sampled,
        "date_stride": config.date_stride,
        "config": config.as_dict(),
        "splits": splits,
        "allowed_sweep_knobs": [
            "sector_rs_threshold",
            "momentum_20_threshold",
            "momentum_60_threshold",
            "extension_cap",
            "pullback_depth",
            "atr_contraction_on_off_as_variant",
            "volume_expansion_threshold",
            "stop_loss_pct",
            "profit_target_pct",
            "max_hold_days",
            "min_avg_dvol",
            "correction_rs_lookback",
            "correction_max_pullback",
            "correction_ema_reclaim",
            "correction_volume_dryup_threshold",
            "correction_volume_expansion_threshold",
            "correction_atr_stop_multiple",
            "correction_market_dd_threshold",
        ],
        "grid_size": len(grid),
        "parameter_sets": [p.as_dict() for p in grid],
        "all_tried_rows": tried,
        "selected_by_variant": selected,
        "best_variant_by_test_after_validation_selection": best_variant,
        "verdict": lab.VERDICT_NEED_MORE if not best_variant else lab.VERDICT_OVERFIT_RISK,
        "paper_shadow": {
            "proposal_created": False,
            "status": "NO_VARIANT_READY_FOR_PAPER_SHADOW",
        },
        "production_thresholds_mutated": False,
    }


def _overfit_risk(train: Dict[str, Any], val: Dict[str, Any], test: Dict[str, Any]) -> str:
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


def render_text(res: Dict[str, Any]) -> List[str]:
    lines = [
        f"STRATEGY THRESHOLD SWEEP - {res.get('generated_at')}",
        lab.RESEARCH_DISCLAIMER,
        f"grid_size={res.get('grid_size')} exact={res.get('exact', False)} sampled={res.get('sampled', False)} "
        f"verdict={res.get('verdict')} best={res.get('best_variant_by_test_after_validation_selection')}",
        f"production_thresholds_mutated={res.get('production_thresholds_mutated')}",
        "",
        f"{'variant':34s} {'param':>5s} {'risk':24s} {'test_n':>7s} {'test_exp':>10s}",
    ]
    for variant, row in (res.get("selected_by_variant") or {}).items():
        test = row.get("test_metrics") or {}
        lines.append(
            f"{variant:34s} {row['param_id']:5d} {row['overfit_risk']:24s} "
            f"{int(test.get('trade_count') or 0):7d} {float(test.get('expectancy') or 0.0):10.4f}"
        )
    return lines


def render_doc(res: Dict[str, Any]) -> str:
    lines = [
        "# Strategy Threshold Sweep Results",
        "",
        f"Generated: {res.get('generated_at')}",
        "",
        lab.RESEARCH_DISCLAIMER,
        "",
        f"Grid size: `{res.get('grid_size')}`",
        "",
        "The grid is intentionally limited and predeclared. Selection is by validation score after complexity penalty; test results are reported after selection and are not used to choose thresholds.",
        "",
        f"Production thresholds mutated: `{res.get('production_thresholds_mutated')}`",
        "",
        "## Selected Rows",
        "",
        "| Variant | Param | Validation Score | Test Trades | Test Exp | Overfit Risk |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for variant, row in (res.get("selected_by_variant") or {}).items():
        test = row.get("test_metrics") or {}
        lines.append(
            f"| {variant} | {row['param_id']} | {row['validation_score']:+.4f} | "
            f"{int(test.get('trade_count') or 0)} | "
            f"{float(test.get('expectancy') or 0.0):+.4f} | {row['overfit_risk']} |"
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
    ap = argparse.ArgumentParser(description="Controlled strategy threshold sweep (research-only)")
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
        cap = args.universe_cap or 100
        stride = max(1, args.date_stride if args.date_stride is not None else 4)
    config = lab.BacktestConfig(universe_mode=args.universe_mode, universe_cap=cap, date_stride=stride)
    res = build_threshold_sweep(config=config)
    write_outputs(res, exact=args.exact)
    print("\n".join(render_text(res)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

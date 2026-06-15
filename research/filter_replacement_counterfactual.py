#!/usr/bin/env python3
"""
research/filter_replacement_counterfactual.py - Phase 1H.4 replacement tests.

Research-only counterfactual backtests for filter replacements identified by
the failure-reason miner. Each replacement is applied ONLY where the original
gate fired (SOFTEN: admit sole-blocked candidates that meet the new rule;
TIGHTEN: drop accepted candidates that trip the new protection). All other
strategy logic — scoring, daily flow cap, exits, costs — stays unchanged.

Writes cache/log/doc artifacts only. A paper-shadow proposal DOC (no signals,
no activation) is written only when a replacement passes every strict gate.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
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

OUT_JSON = CACHE / "filter_replacement_counterfactual_latest.json"
OUT_TXT = LOGS / "filter_replacement_counterfactual_latest.txt"
OUT_DOC = DOCS / "FILTER_REPLACEMENT_COUNTERFACTUAL_RESULTS.md"
PROPOSAL_DOC = DOCS / "PAPER_SHADOW_PROMOTION_PROPOSAL.md"

VERSION = "FILTER_REPLACEMENT_COUNTERFACTUAL_V1"

REPLACEMENT_REJECT = "REPLACEMENT_REJECT"
REPLACEMENT_NEED_MORE_DATA = "REPLACEMENT_NEED_MORE_DATA"
REPLACEMENT_IMPROVES_FLOW_ONLY = "REPLACEMENT_IMPROVES_FLOW_ONLY"
REPLACEMENT_IMPROVES_EDGE = "REPLACEMENT_IMPROVES_EDGE"
REPLACEMENT_READY = "REPLACEMENT_READY_FOR_PAPER_SHADOW_PROPOSAL"

NO_READY_STATUS = "NO_FILTER_REPLACEMENT_READY_FOR_PAPER_SHADOW"

MIN_CHANGED_TRADES = 15
MIN_TEST_TRADES = 20

_f = lab._f
_opt = lab._opt
_pct = miner._pct

CondFn = Callable[[Dict[str, Any], lab.StrategyParams, Dict[str, Any], Dict[str, Any]], bool]


@dataclass(frozen=True)
class ReplacementSpec:
    spec_id: str
    variant: str
    target_gate: str
    mode: str  # SOFTEN | TIGHTEN
    old_rule: str
    new_rule: str
    rationale: str
    condition: CondFn


def _vol_ratio(f: Dict[str, Any]) -> float:
    return _f(f.get("volume")) / max(_f(f.get("avg_vol20"), 1.0), 1.0)


def _c_sniper_rs_accel(f, p, c, s):
    return _f(f.get("rs10_spy")) > 0.02 and _f(f.get("r5")) > 0


def _c_sniper_vol_expansion(f, p, c, s):
    return _vol_ratio(f) >= 1.8


def _c_sniper_close_strength(f, p, c, s):
    high20 = _f(f.get("high20"))
    return high20 > 0 and _f(f.get("price")) >= high20 * 0.98 and f.get("above_ema20") is True


def _c_sniper_near_high_reclaim(f, p, c, s):
    return (
        _f(f.get("drawdown_from_high20")) >= -0.05
        and f.get("above_ema20") is True
        and _f(f.get("rs10_spy")) > 0
    )


def _c_voy_ma50_reclaim(f, p, c, s):
    ma50 = _opt(f.get("ma50"))
    return bool(ma50) and _f(f.get("price")) >= ma50 and _f(f.get("rs50_spy")) > 0.03


def _c_voy_declining_only(f, p, c, s):
    return f.get("ma50_rising") is True or _f(f.get("rs50_spy")) > 0


def _c_rsmom_power_leader(f, p, c, s):
    rs = max(_f(f.get("rs20_spy"), -9.0), _f(f.get("sector_rs20"), -9.0))
    return (
        lab._is_power_theme(f)
        and rs >= 1.5 * p.sector_rs_threshold
        and _f(f.get("drawdown_from_high20")) >= -0.05
    )


def _c_rsmom_correction_turn(f, p, c, s):
    label = (c.get("REGIME") or {}).get("label")
    rs = max(_f(f.get("rs20_spy"), -9.0), _f(f.get("sector_rs20"), -9.0))
    return label in regime.CORRECTION_FAMILY and f.get("above_ema20") is True and rs >= 0.02


def _c_pwr_climax_only(f, p, c, s):
    return _f(f.get("vol_expansion"), 1.0) <= 2.0 and _f(f.get("r5")) >= 0


def _c_block_risk_regime(f, p, c, s):
    return (c.get("REGIME") or {}).get("label") in miner.RISK_REGIMES


def _c_clr_block_weak_dryup(f, p, c, s):
    volume_ratio = s.get("volume_ratio")
    if volume_ratio is None:
        volume_ratio = _vol_ratio(f)
    rs = s.get("rs")
    if rs is None:
        rs = lab._correction_rs(f, c, int(p.correction_rs_lookback))
    return volume_ratio < p.correction_volume_expansion_threshold and _f(rs) < 0.03


REPLACEMENTS: Tuple[ReplacementSpec, ...] = (
    ReplacementSpec(
        "SNIPER_ATR_TO_RS_ACCELERATION", "PROD_SNIPER_CURRENT", "atr_contraction_lt_0_85", "SOFTEN",
        "reject unless ATR(5)/ATR(15) < 0.85",
        "where the ATR gate alone fired: admit if rs10_spy > +2% and r5 > 0 (RS acceleration)",
        "1H showed SNIPER is starved; miner tests whether ATR contraction blocks working breakouts.",
        _c_sniper_rs_accel,
    ),
    ReplacementSpec(
        "SNIPER_ATR_TO_VOL_EXPANSION_1_8", "PROD_SNIPER_CURRENT", "atr_contraction_lt_0_85", "SOFTEN",
        "reject unless ATR(5)/ATR(15) < 0.85",
        "where the ATR gate alone fired: admit if breakout volume ratio >= 1.8x",
        "Replaces volatility-contraction evidence with stronger volume evidence.",
        _c_sniper_vol_expansion,
    ),
    ReplacementSpec(
        "SNIPER_ATR_TO_BREAKOUT_CLOSE_STRENGTH", "PROD_SNIPER_CURRENT", "atr_contraction_lt_0_85", "SOFTEN",
        "reject unless ATR(5)/ATR(15) < 0.85",
        "where the ATR gate alone fired: admit if close within 2% of 20d high and above EMA20",
        "Replaces contraction with breakout close strength.",
        _c_sniper_close_strength,
    ),
    ReplacementSpec(
        "SNIPER_BREAKOUT_TO_NEAR_HIGH_RECLAIM", "PROD_SNIPER_CURRENT", "first_breakout", "SOFTEN",
        "reject unless a fresh 20d-high breakout happened today",
        "where the breakout gate alone fired: admit tight near-high consolidation (dd20 >= -5%, above EMA20, rs10 > 0)",
        "Tests whether near-breakout reclaims work as well as fresh breakouts.",
        _c_sniper_near_high_reclaim,
    ),
    ReplacementSpec(
        "VOY_MA200_FLOOR_TO_MA50_RECLAIM", "PROD_VOYAGER_CURRENT", "ma200_floor_0_92", "SOFTEN",
        "reject if price < MA200 * 0.92",
        "where the MA200 floor alone fired: admit if price reclaimed MA50 and rs50_spy > +3%",
        "Tests early-recovery admission below the MA200 floor.",
        _c_voy_ma50_reclaim,
    ),
    ReplacementSpec(
        "VOY_MA200_FLOOR_DECLINING_ONLY", "PROD_VOYAGER_CURRENT", "ma200_floor_0_92", "SOFTEN",
        "reject if price < MA200 * 0.92",
        "where the MA200 floor alone fired: admit unless MA50 is also falling and rs50 <= 0",
        "Rejects only structurally broken names instead of every deep base.",
        _c_voy_declining_only,
    ),
    ReplacementSpec(
        "RSMOM_EXT_ALLOW_POWER_LEADER", "RECALL_SHADOW_RS_MOMENTUM", "extension_cap", "SOFTEN",
        "reject if extension above EMA20 > 25%",
        "where the extension cap alone fired: admit power-theme RS leaders with controlled pullback (dd20 >= -5%)",
        "1G.14 showed power-theme extension is rewarded; this tests it inside the lab funnel.",
        _c_rsmom_power_leader,
    ),
    ReplacementSpec(
        "RSMOM_WEAK_RS_CORRECTION_TURN", "RECALL_SHADOW_RS_MOMENTUM", "rs_floor", "SOFTEN",
        "reject if max(rs20_spy, sector_rs20) < 8%",
        "where the RS floor alone fired: admit early RS turns (rs >= 2%) during correction-family regimes if above EMA20",
        "Tests whether the RS floor is too slow coming out of corrections.",
        _c_rsmom_correction_turn,
    ),
    ReplacementSpec(
        "PWR_PARABOLIC_BLOCK_ONLY_CLIMAX", "POWER_TREND_EXTENSION", "not_parabolic", "SOFTEN",
        "reject if r5 > 30% or r10 > 55%",
        "where the parabolic gate alone fired: admit unless volume climax (vol_expansion > 2x) or bearish reversal (r5 < 0)",
        "Blocks only climax behavior instead of all fast moves.",
        _c_pwr_climax_only,
    ),
    ReplacementSpec(
        "RSMOM_RISK_REGIME_PROTECTION", "RECALL_SHADOW_RS_MOMENTUM", "risk_regime_protection_new", "TIGHTEN",
        "no market-regime protection",
        "block accepted signals when the market regime is RISK_OFF / correction / high-volatility",
        "Accepted-loser mining flags regime risk; tests a protective gate.",
        _c_block_risk_regime,
    ),
    ReplacementSpec(
        "PULLBACK_RISK_REGIME_PROTECTION", "RECALL_SHADOW_PULLBACK", "risk_regime_protection_new", "TIGHTEN",
        "no market-regime protection",
        "block accepted signals when the market regime is RISK_OFF / correction / high-volatility",
        "RECALL_SHADOW_PULLBACK carries -14% realistic maxDD; tests regime protection.",
        _c_block_risk_regime,
    ),
    ReplacementSpec(
        "CLR_REQUIRE_REAL_VOLUME_CONFIRM", "CORRECTION_LEADER_RECLAIM", "volume_confirm_or_dryup", "TIGHTEN",
        "volume expansion OR dryup counts as confirmation",
        "block accepted signals confirmed only by volume dryup when correction RS < +3%",
        "Tests whether weak dryup-only confirmations drive CLR's -97% independent drawdown.",
        _c_clr_block_weak_dryup,
    ),
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _signal_from_trace(spec: ReplacementSpec, trace: Dict[str, Any], f: Dict[str, Any]) -> lab.Signal:
    score = trace.get("score")
    if score is None:
        score = miner.score_for(spec.variant, f, trace.get("state") or {}) or 0.0
    return lab.Signal(
        spec.variant, trace["ticker"], trace["asof"], trace["side"], float(score),
        [f"replacement:{spec.spec_id}"], f,
    )


def collect_counterfactual_signals(
    start: str,
    end: str,
    *,
    specs: Sequence[ReplacementSpec] = REPLACEMENTS,
    params: lab.StrategyParams = lab.StrategyParams(),
    config: lab.BacktestConfig = lab.BacktestConfig(universe_cap=140, date_stride=1),
    max_dates: Optional[int] = None,
) -> Dict[str, Any]:
    """One evaluation pass: baseline signals per variant + modified signals per spec.

    SOFTEN admits only candidates whose sole blocker is the target gate AND
    that satisfy the new rule. TIGHTEN removes accepted candidates that trip
    the protection. The daily top-N flow cap is re-applied after modification,
    exactly as the lab applies it.
    """
    variants = sorted({s.variant for s in specs})
    baseline: Dict[str, List[lab.Signal]] = defaultdict(list)
    modified: Dict[str, List[lab.Signal]] = defaultdict(list)
    changed_keys: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
    cap = config.max_signals_per_variant_day

    for idx, asof, features, context, traces_by_variant in miner.iter_evaluation(
        start, end, variants=variants, params=params, config=config, max_dates=max_dates
    ):
        fmap = {f["ticker"]: f for f in features}
        for variant in variants:
            traces = traces_by_variant[variant]
            accepted = [t for t in traces if t["accepted"]]
            accepted_sorted = sorted(accepted, key=lambda t: _f(t.get("score")), reverse=True)
            baseline[variant].extend(t["signal"] for t in accepted_sorted[:cap])
            for spec in specs:
                if spec.variant != variant:
                    continue
                if spec.mode == "SOFTEN":
                    admitted = []
                    for t in traces:
                        if t["sole_blocker"] != spec.target_gate:
                            continue
                        f = fmap[t["ticker"]]
                        try:
                            if spec.condition(f, params, context, t.get("state") or {}):
                                admitted.append(_signal_from_trace(spec, t, f))
                                changed_keys[spec.spec_id].append((t["ticker"], t["asof"]))
                        except Exception:
                            continue
                    pool = [t["signal"] for t in accepted] + admitted
                else:
                    pool = []
                    for t in accepted:
                        f = fmap[t["ticker"]]
                        try:
                            blocked = spec.condition(f, params, context, t.get("state") or {})
                        except Exception:
                            blocked = False
                        if blocked:
                            changed_keys[spec.spec_id].append((t["ticker"], t["asof"]))
                        else:
                            pool.append(t["signal"])
                pool_sorted = sorted(pool, key=lambda s: _f(s.score), reverse=True)
                modified[spec.spec_id].extend(pool_sorted[:cap])
    return {"baseline": dict(baseline), "modified": dict(modified), "changed_keys": dict(changed_keys)}


def _simulate(signals: Sequence[lab.Signal], params: lab.StrategyParams, config: lab.BacktestConfig) -> List[Dict[str, Any]]:
    trades = []
    for sig in signals:
        trade = lab.simulate_trade(sig, params=params, cost_model=lab.BASE_COST, entry_timing=config.entry_timing)
        if trade is not None:
            trades.append(trade)
    return trades


def _slim_exact(m: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(m)
    for key in ("worst_10_trades", "best_10_trades", "monthly_return_distribution", "monthly_trade_counts",
                "sector_concentration", "theme_concentration", "ticker_concentration", "regime_breakdown"):
        out.pop(key, None)
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
        out[name] = _slim_exact(lab.summarize_trades(subset, start=s, end=e))
    return out


def _portfolio_block(trades: Sequence[Dict[str, Any]], start: str, end: str) -> Dict[str, Any]:
    real = portfolio.realistic_portfolio_metrics(trades, start=start, end=end, config=portfolio.PortfolioConfig())
    rows = real.get("daily_rows") or []
    same_spy = correction._same_exposure_benchmark("SPY", rows, start=start, end=end)
    same_qqq = correction._same_exposure_benchmark("QQQ", rows, start=start, end=end)
    slim = correction._augment_metrics(real)
    return {"realistic": slim, "same_exposure_spy": same_spy, "same_exposure_qqq": same_qqq}


def _changed_stats(trades: Sequence[Dict[str, Any]], keys: Sequence[Tuple[str, str]], mode: str) -> Dict[str, Any]:
    keyset = {(str(k[0]), str(k[1])) for k in keys}
    rows = [t for t in trades if (str(t["ticker"]), str(t["signal_date"])) in keyset]
    rets = [float(t["net_return"]) for t in rows]
    wins = sum(1 for r in rets if r > 0)
    return {
        "mode": mode,
        "changed_candidates": len(keyset),
        "changed_trades_simulated": len(rows),
        "mean_net_return": miner._mean(rets),
        "win_rate": round(wins / len(rets), 4) if rets else None,
        "winners": wins,
        "losers": sum(1 for r in rets if r < 0),
    }


def _delta(new: Optional[float], old: Optional[float]) -> Optional[float]:
    if new is None or old is None:
        return None
    return round(float(new) - float(old), 6)


def spec_verdict(row: Dict[str, Any]) -> Tuple[str, List[str]]:
    """Strict ladder. Flow without edge is rejected by design."""
    reasons: List[str] = []
    changed = int(row["changed"].get("changed_trades_simulated") or 0)
    old_exact = row["baseline_exact"]
    new_exact = row["exact"]
    d_exp = _delta(new_exact.get("expectancy"), old_exact.get("expectancy"))
    new_exp = _f(new_exact.get("expectancy"), -1.0)
    test_new = row["walk_forward"]["test"]
    test_old = row["baseline_walk_forward"]["test"]
    real_new = row["portfolio"]["realistic"]
    real_old = row["baseline_portfolio"]["realistic"]
    same_best_ret = max(_f(row["portfolio"]["same_exposure_spy"].get("total_return")), _f(row["portfolio"]["same_exposure_qqq"].get("total_return")))
    same_best_sharpe = max(_f(row["portfolio"]["same_exposure_spy"].get("sharpe"), -9.0), _f(row["portfolio"]["same_exposure_qqq"].get("sharpe"), -9.0))

    if changed < MIN_CHANGED_TRADES:
        return REPLACEMENT_NEED_MORE_DATA, [f"only {changed} changed trades (<{MIN_CHANGED_TRADES}); cannot judge the replacement"]

    if new_exp <= 0:
        return REPLACEMENT_REJECT, [f"modified expectancy is not positive ({_pct(new_exact.get('expectancy'))})"]
    if _f(real_new.get("max_drawdown")) < -0.15:
        return REPLACEMENT_REJECT, [f"modified realistic maxDD {_pct(real_new.get('max_drawdown'))} breaches -15% risk limit"]
    if _f(real_new.get("max_drawdown")) < _f(real_old.get("max_drawdown")) - 0.03:
        return REPLACEMENT_REJECT, [
            f"replacement worsens realistic maxDD ({_pct(real_old.get('max_drawdown'))} -> {_pct(real_new.get('max_drawdown'))})"
        ]

    flow_delta = int(new_exact.get("trade_count") or 0) - int(old_exact.get("trade_count") or 0)
    if d_exp is None or d_exp < 0.002:
        if d_exp is not None and d_exp >= -0.001 and abs(flow_delta) >= max(5, 0.15 * max(1, int(old_exact.get("trade_count") or 0))):
            return REPLACEMENT_IMPROVES_FLOW_ONLY, [
                f"flow changes by {flow_delta:+d} trades but expectancy delta is only {_pct(d_exp)}; flow without edge is not promotable"
            ]
        return REPLACEMENT_REJECT, [f"expectancy delta {_pct(d_exp)} does not clear the +0.20% bar"]

    reasons.append(f"expectancy improves {_pct(old_exact.get('expectancy'))} -> {_pct(new_exact.get('expectancy'))}")
    if int(test_new.get("trade_count") or 0) < MIN_TEST_TRADES:
        return REPLACEMENT_NEED_MORE_DATA, reasons + [f"walk-forward test split has only {test_new.get('trade_count')} trades (<{MIN_TEST_TRADES})"]
    if _f(test_new.get("expectancy"), -1.0) <= 0:
        return REPLACEMENT_REJECT, reasons + ["walk-forward test expectancy is not positive; full-window gain does not survive the test split"]
    if _f(test_new.get("expectancy")) < _f(test_old.get("expectancy")) - 0.001:
        return REPLACEMENT_REJECT, reasons + ["replacement degrades the walk-forward test split versus the original gate"]

    reasons.append(f"test-split expectancy {_pct(test_new.get('expectancy'))} on {test_new.get('trade_count')} trades")

    ready = (
        _f(real_new.get("total_return")) > same_best_ret
        and _f(real_new.get("sharpe"), -9.0) > same_best_sharpe
        and _f(real_new.get("max_drawdown")) >= -0.15
        and _f(real_new.get("sharpe"), -9.0) >= _f(real_old.get("sharpe"), -9.0)
        and sum(1 for blk in ("train", "validation", "test") if _f(row["walk_forward"][blk].get("expectancy"), -1.0) > 0) >= 2
    )
    if ready:
        return REPLACEMENT_READY, reasons + [
            "realistic portfolio beats same-exposure SPY/QQQ on return and Sharpe within risk limits; positive in >=2 walk-forward splits"
        ]
    return REPLACEMENT_IMPROVES_EDGE, reasons + [
        "edge improves but realistic portfolio does not yet beat same-exposure benchmarks on both return and Sharpe"
    ]


def build_counterfactual(
    *,
    start: Optional[str] = None,
    end: Optional[str] = None,
    specs: Sequence[ReplacementSpec] = REPLACEMENTS,
    stride: int = 1,
    max_dates: Optional[int] = None,
) -> Dict[str, Any]:
    if start is None or end is None:
        span_start, span_end, _, _ = portfolio._primary_window_span()
        start = start or span_start
        end = end or span_end
    params = lab.StrategyParams()
    config = lab.BacktestConfig(universe_cap=140, date_stride=stride)
    splits = wf.make_walk_forward_splits(start=start, end=end).get("splits") or {}

    collected = collect_counterfactual_signals(
        start, end, specs=specs, params=params, config=config, max_dates=max_dates
    )

    baseline_trades: Dict[str, List[Dict[str, Any]]] = {}
    baseline_blocks: Dict[str, Dict[str, Any]] = {}
    for variant, signals in collected["baseline"].items():
        trades = _simulate(signals, params, config)
        baseline_trades[variant] = trades
        baseline_blocks[variant] = {
            "exact": _slim_exact(lab.summarize_trades(trades, start=start, end=end)),
            "walk_forward": _split_metrics(trades, splits),
            "portfolio": _portfolio_block(trades, start, end),
        }

    spec_rows: Dict[str, Any] = {}
    for spec in specs:
        signals = collected["modified"].get(spec.spec_id, [])
        trades = _simulate(signals, params, config)
        base = baseline_blocks[spec.variant]
        base_trades = baseline_trades[spec.variant]
        exact = _slim_exact(lab.summarize_trades(trades, start=start, end=end))
        split_m = _split_metrics(trades, splits)
        port = _portfolio_block(trades, start, end)
        changed_pool = trades if spec.mode == "SOFTEN" else base_trades
        changed = _changed_stats(changed_pool, collected["changed_keys"].get(spec.spec_id, []), spec.mode)
        row = {
            "spec_id": spec.spec_id,
            "variant": spec.variant,
            "target_gate": spec.target_gate,
            "mode": spec.mode,
            "old_rule": spec.old_rule,
            "new_rule": spec.new_rule,
            "rationale": spec.rationale,
            "exact": exact,
            "baseline_exact": base["exact"],
            "walk_forward": split_m,
            "baseline_walk_forward": base["walk_forward"],
            "portfolio": port,
            "baseline_portfolio": base["portfolio"],
            "changed": changed,
            "deltas": {
                "trade_count": int(exact.get("trade_count") or 0) - int(base["exact"].get("trade_count") or 0),
                "expectancy": _delta(exact.get("expectancy"), base["exact"].get("expectancy")),
                "rel_spy": _delta(exact.get("rel_spy"), base["exact"].get("rel_spy")),
                "rel_qqq": _delta(exact.get("rel_qqq"), base["exact"].get("rel_qqq")),
                "win_rate": _delta(exact.get("win_rate"), base["exact"].get("win_rate")),
                "max_drawdown_exact": _delta(exact.get("max_drawdown"), base["exact"].get("max_drawdown")),
                "max_drawdown_realistic": _delta(port["realistic"].get("max_drawdown"), base["portfolio"]["realistic"].get("max_drawdown")),
                "mfe": _delta(exact.get("mfe"), base["exact"].get("mfe")),
                "mae": _delta(exact.get("mae"), base["exact"].get("mae")),
                "realistic_total_return": _delta(port["realistic"].get("total_return"), base["portfolio"]["realistic"].get("total_return")),
                "realistic_sharpe": _delta(port["realistic"].get("sharpe"), base["portfolio"]["realistic"].get("sharpe")),
            },
        }
        verdict, reasons = spec_verdict(row)
        if spec.mode == "SOFTEN":
            row["rejected_winner_recovery"] = changed.get("winners")
            row["false_positive_increase"] = changed.get("losers")
        else:
            row["accepted_loser_reduction"] = changed.get("losers")
            row["accepted_winner_cost"] = changed.get("winners")
        row["verdict"] = verdict
        row["verdict_reasons"] = reasons
        spec_rows[spec.spec_id] = row

    ready = [sid for sid, row in spec_rows.items() if row["verdict"] == REPLACEMENT_READY]
    improves = [sid for sid, row in spec_rows.items() if row["verdict"] == REPLACEMENT_IMPROVES_EDGE]
    ranked = sorted(
        spec_rows.values(),
        key=lambda r: (
            {REPLACEMENT_READY: 0, REPLACEMENT_IMPROVES_EDGE: 1, REPLACEMENT_IMPROVES_FLOW_ONLY: 2,
             REPLACEMENT_NEED_MORE_DATA: 3, REPLACEMENT_REJECT: 4}[r["verdict"]],
            -_f(r["deltas"].get("expectancy")),
        ),
    )
    best = ranked[0] if ranked else None

    return {
        "kind": "filter_replacement_counterfactual",
        "version": VERSION,
        "generated_at": _utc_now(),
        "research_only": True,
        "status": "RESEARCH_ONLY",
        "signal_window": {"start": start, "end": end},
        "walk_forward_splits": splits,
        "walk_forward_note": (
            "Replacement rules are fixed a priori (not fitted), so the train/validation/test split is a decay "
            "diagnostic; the test block is the binding evidence."
        ),
        "params": params.as_dict(),
        "config": config.as_dict(),
        "cost_model": lab.BASE_COST,
        "replacements": spec_rows,
        "baselines": baseline_blocks,
        "best_replacement": (
            {"spec_id": best["spec_id"], "verdict": best["verdict"], "delta_expectancy": best["deltas"].get("expectancy")}
            if best else None
        ),
        "paper_shadow": {
            "status": REPLACEMENT_READY if ready else NO_READY_STATUS,
            "ready_specs": ready,
            "improves_edge_specs": improves,
            "proposal_created": bool(ready),
            "note": "Proposal doc only; nothing is activated. Flow-only improvements are rejected by design.",
        },
        "safety": lab.safety_confirmations(),
    }


def render_proposal_doc(res: Dict[str, Any]) -> str:
    ready = res["paper_shadow"]["ready_specs"]
    lines = [
        "# Paper-Shadow Promotion Proposal (Phase 1H.4 filter replacement)",
        "",
        "Manual review required. This document does not activate anything: no paper signals, no broker orders,",
        "no production threshold changes. It exists because a replacement filter passed every strict gate.",
        "",
    ]
    for sid in ready:
        row = res["replacements"][sid]
        real = row["portfolio"]["realistic"]
        lines += [
            f"## {sid}",
            "",
            f"- Strategy: `{row['variant']}`",
            f"- Old gate: {row['old_rule']}",
            f"- Replacement gate: {row['new_rule']}",
            f"- Mode: {row['mode']} (applies only where the original gate fired)",
            "",
            "### Evidence",
            "",
            f"- Exact backtest: {row['exact'].get('trade_count')} trades, expectancy {_pct(row['exact'].get('expectancy'))} "
            f"(baseline {_pct(row['baseline_exact'].get('expectancy'))}), rel-SPY {_pct(row['exact'].get('rel_spy'))}",
            f"- Walk-forward test: {row['walk_forward']['test'].get('trade_count')} trades, "
            f"expectancy {_pct(row['walk_forward']['test'].get('expectancy'))}",
            f"- Realistic portfolio: return {_pct(real.get('total_return'))}, maxDD {_pct(real.get('max_drawdown'))}, "
            f"Sharpe {real.get('sharpe')}",
            f"- Same-exposure SPY/QQQ: {_pct(row['portfolio']['same_exposure_spy'].get('total_return'))} / "
            f"{_pct(row['portfolio']['same_exposure_qqq'].get('total_return'))}",
            "",
            "### Risk limits and kill criteria (paper-only)",
            "",
            "- Max 5 concurrent positions, 10% position cap, 30% sector cap, 50% gross exposure (lab portfolio config).",
            "- Kill if paper-shadow expectancy after 30 trades is <= 0, or realistic-equivalent drawdown exceeds -10%,",
            "  or the replacement's changed-trade win rate drops below 40%.",
            "- Paper-only: signals are logged for evidence; no orders of any kind.",
            "",
        ]
    return "\n".join(lines)


def render_text(res: Dict[str, Any]) -> List[str]:
    lines = [
        f"FILTER REPLACEMENT COUNTERFACTUAL - {res['generated_at']}",
        lab.RESEARCH_DISCLAIMER,
        f"window={res['signal_window']['start']}..{res['signal_window']['end']}",
        f"paper_shadow={res['paper_shadow']['status']} ready={res['paper_shadow']['ready_specs']}",
        "",
        f"{'spec':40s} {'verdict':38s} {'dN':>5s} {'dExp':>8s} {'dDD':>8s}",
    ]
    for sid, row in res["replacements"].items():
        dd = row["deltas"]
        lines.append(
            f"{sid:40s} {row['verdict']:38s} {dd['trade_count']:>+5d} {_pct(dd['expectancy']):>8s} {_pct(dd['max_drawdown_realistic']):>8s}"
        )
    return lines


def render_doc(res: Dict[str, Any]) -> str:
    lines = [
        "# Filter Replacement Counterfactual Results (Phase 1H.4)",
        "",
        f"Generated: {res['generated_at']}",
        "",
        lab.RESEARCH_DISCLAIMER,
        "",
        f"Signal window: `{res['signal_window']['start']}` to `{res['signal_window']['end']}`. "
        f"Cost model: base (10bps slippage + 5bps spread). {res['walk_forward_note']}",
        "",
        f"## Top line: **{res['paper_shadow']['status']}**",
        "",
        "| Spec | Variant | Mode | Verdict | dTrades | dExpectancy | dRelSPY | dWinRate | dMaxDD(real) | Changed trades |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for sid, row in res["replacements"].items():
        dd = row["deltas"]
        lines.append(
            f"| {sid} | {row['variant']} | {row['mode']} | {row['verdict']} | {dd['trade_count']:+d} | "
            f"{_pct(dd['expectancy'])} | {_pct(dd['rel_spy'])} | {_pct(dd['win_rate'])} | "
            f"{_pct(dd['max_drawdown_realistic'])} | {row['changed'].get('changed_trades_simulated')} |"
        )
    lines += ["", "## Per-Replacement Detail", ""]
    for sid, row in res["replacements"].items():
        test = row["walk_forward"]["test"]
        real = row["portfolio"]["realistic"]
        lines += [
            f"### {sid}",
            "",
            f"- Old rule: {row['old_rule']}",
            f"- New rule: {row['new_rule']}",
            f"- Rationale: {row['rationale']}",
            f"- Verdict: **{row['verdict']}** — {'; '.join(row['verdict_reasons'])}",
            f"- Exact: {row['exact'].get('trade_count')} trades (baseline {row['baseline_exact'].get('trade_count')}), "
            f"expectancy {_pct(row['exact'].get('expectancy'))} (baseline {_pct(row['baseline_exact'].get('expectancy'))})",
            f"- Walk-forward test: n={test.get('trade_count')}, expectancy {_pct(test.get('expectancy'))} "
            f"(baseline {_pct(row['baseline_walk_forward']['test'].get('expectancy'))})",
            f"- Realistic: return {_pct(real.get('total_return'))} (baseline "
            f"{_pct(row['baseline_portfolio']['realistic'].get('total_return'))}), "
            f"maxDD {_pct(real.get('max_drawdown'))}, Sharpe {real.get('sharpe')}",
            f"- Same-exposure SPY/QQQ return: {_pct(row['portfolio']['same_exposure_spy'].get('total_return'))} / "
            f"{_pct(row['portfolio']['same_exposure_qqq'].get('total_return'))}",
            f"- Changed-trade stats: {row['changed']}",
            "",
        ]
    lines += [
        "## Safety",
        "",
        "No paper signals, broker orders, trade proposals (doc-only), production thresholds, Gatekeeper/Veto logic,",
        "execution/governance/live-capital modules, historical evidence, or SHORT_A frozen status were changed.",
        "",
    ]
    return "\n".join(lines)


def write_outputs(res: Dict[str, Any]) -> None:
    for p in (OUT_JSON, OUT_TXT, OUT_DOC):
        p.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    OUT_TXT.write_text("\n".join(render_text(res)) + "\n", encoding="utf-8")
    OUT_DOC.write_text(render_doc(res) + "\n", encoding="utf-8")
    if res["paper_shadow"]["proposal_created"]:
        PROPOSAL_DOC.write_text(render_proposal_doc(res) + "\n", encoding="utf-8")

    # Refresh the kill/repair list with replacement outcomes when the miner
    # artifact exists (Task 8 doc stays consistent across both runners).
    if miner.OUT_JSON.exists():
        try:
            miner_res = json.loads(miner.OUT_JSON.read_text())
            miner.OUT_KILL_REPAIR_DOC.write_text(
                miner.render_kill_repair_doc(miner_res, res) + "\n", encoding="utf-8"
            )
        except Exception:
            pass


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Phase 1H.4 filter replacement counterfactual (research-only)")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--stride", type=int, default=1)
    ap.add_argument("--max-dates", type=int, default=None)
    ap.add_argument("--specs", default=None, help="comma-separated spec ids (default: all)")
    args = ap.parse_args(argv)
    specs: Sequence[ReplacementSpec] = REPLACEMENTS
    if args.specs:
        wanted = {s.strip() for s in args.specs.split(",") if s.strip()}
        specs = tuple(s for s in REPLACEMENTS if s.spec_id in wanted)
    res = build_counterfactual(start=args.start, end=args.end, specs=specs, stride=args.stride, max_dates=args.max_dates)
    write_outputs(res)
    print("\n".join(render_text(res)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

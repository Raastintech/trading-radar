#!/usr/bin/env python3
"""
research/strategy_lab_correction_strategy.py - Phase 1H.3 correction strategy report.

Research-only orchestration for CORRECTION_LEADER_RECLAIM and existing Strategy
Lab variants. It writes cache/log/doc artifacts only and never imports broker,
execution, governance, paper-signal, or live-capital modules.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from research import strategy_lab_portfolio as portfolio  # noqa: E402
from research import strategy_lab_regime as regime  # noqa: E402
from research import strategy_research_lab as lab  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "cache" / "research"
LOGS = ROOT / "logs"
DOCS = ROOT / "docs" / "research"

OUT_JSON = CACHE / "strategy_lab_correction_strategy_latest.json"
OUT_TXT = LOGS / "strategy_lab_correction_strategy_latest.txt"
OUT_DOC = DOCS / "STRATEGY_LAB_CORRECTION_STRATEGY_RESULTS.md"
PROPOSAL_DOC = DOCS / "CORRECTION_LEADER_RECLAIM_PAPER_SHADOW_PROPOSAL.md"

VERSION = "STRATEGY_LAB_CORRECTION_STRATEGY_V1"

EVAL_VARIANTS = (
    "CORRECTION_LEADER_RECLAIM",
    "PROD_SNIPER_CURRENT",
    "SNIPER_NO_ATR_CONTRACTION",
    "PROD_VOYAGER_CURRENT",
    "RECALL_SHADOW_RS_MOMENTUM",
    "RECALL_SHADOW_PULLBACK",
    "POWER_TREND_EXTENSION",
    "SIMPLE_SECTOR_RS",
    "SIMPLE_MOM_20_60",
    "RANDOM_LIQUID",
    "QQQ_TECH_TACTICAL_SHORT",
)

PRIMARY_CANDIDATES = tuple(v for v in EVAL_VARIANTS if v != "QQQ_TECH_TACTICAL_SHORT")
BENCHMARK_ROWS = ("SPY", "QQQ", "cash", "50% SPY / 50% cash", "50% QQQ / 50% cash")
CORRECTION_SLICES = (
    "march_2026",
    regime.MARKET_CORRECTION,
    regime.TECH_LED_CORRECTION,
    regime.RECOVERY_RECLAIM,
    "CORRECTION_FAMILY",
)

CANDIDATE_LABEL = "PAPER_SHADOW_CANDIDATE"
REJECT = "REJECT"
NEED_MORE = "NEED_MORE_DATA"
OVERFIT = "PROMISING_BUT_OVERFIT_RISK"
PORTFOLIO_RISK = "PROMISING_BUT_PORTFOLIO_RISK"
LOW_FLOW = "LOW_FLOW_PAPER_WATCH"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        value = float(value)
        return value if math.isfinite(value) else default
    except Exception:
        return default


def _round(value: Any, digits: int = 6) -> Optional[float]:
    try:
        value = float(value)
        return round(value, digits) if math.isfinite(value) else None
    except Exception:
        return None


def _pct(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value) * 100:+.2f}%"


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _calmar(cagr: Any, max_drawdown: Any) -> Optional[float]:
    c = _f(cagr)
    dd = abs(_f(max_drawdown))
    if dd <= 0:
        return None if c == 0 else round(c / 1e-9, 4)
    return round(c / dd, 4)


def _return_per_unit_exposure(total_return: Any, exposure: Any) -> Optional[float]:
    exp = _f(exposure)
    if exp <= 0:
        return None
    return round(_f(total_return) / exp, 6)


def _augment_metrics(metrics: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(metrics)
    out["calmar"] = _calmar(out.get("cagr"), out.get("max_drawdown"))
    out["return_per_unit_exposure"] = _return_per_unit_exposure(
        out.get("total_return"), out.get("exposure_pct")
    )
    out.pop("daily_rows", None)
    out.pop("accepted_sample", None)
    out.pop("rejected_sample", None)
    return out


def _trade_expectancy_stats(trades: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    returns = [float(t.get("net_return") or 0.0) for t in trades]
    rel_spy = [float(t["rel_spy"]) for t in trades if t.get("rel_spy") is not None]
    rel_qqq = [float(t["rel_qqq"]) for t in trades if t.get("rel_qqq") is not None]
    mae = [float(t["mae"]) for t in trades if t.get("mae") is not None]
    if not returns:
        return {
            "trade_count": 0,
            "win_rate": None,
            "expectancy": None,
            "rel_spy": None,
            "rel_qqq": None,
            "max_drawdown": 0.0,
            "average_mae": None,
            "total_return_independent": 0.0,
        }
    equity = 1.0
    path = [equity]
    for value in returns:
        equity *= 1.0 + value
        path.append(equity)
    return {
        "trade_count": len(returns),
        "win_rate": round(sum(1 for r in returns if r > 0) / len(returns), 4),
        "expectancy": round(statistics.mean(returns), 6),
        "rel_spy": round(statistics.mean(rel_spy), 6) if rel_spy else None,
        "rel_qqq": round(statistics.mean(rel_qqq), 6) if rel_qqq else None,
        "max_drawdown": round(portfolio._max_drawdown_from_equity(path), 6),
        "average_mae": round(statistics.mean(mae), 6) if mae else None,
        "total_return_independent": round(equity - 1.0, 6),
    }


def _date_in_month(date_value: Any, month: str) -> bool:
    return str(date_value or "")[:7] == month


def _filter_slice(trades: Sequence[Dict[str, Any]], slice_name: str) -> List[Dict[str, Any]]:
    if slice_name == "march_2026":
        return [t for t in trades if _date_in_month(t.get("signal_date"), "2026-03")]
    if slice_name == "CORRECTION_FAMILY":
        labels = {regime.MARKET_CORRECTION, regime.TECH_LED_CORRECTION, regime.RECOVERY_RECLAIM}
        return [t for t in trades if t.get("market_regime") in labels]
    return [t for t in trades if t.get("market_regime") == slice_name]


def _correction_slice_metrics(trades: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    out = {}
    for name in CORRECTION_SLICES:
        subset = _filter_slice(trades, name)
        out[name] = _trade_expectancy_stats(subset)
    return out


def _concentration(trades: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(trades)
    ticker = Counter(str(t.get("ticker") or "UNKNOWN") for t in trades)
    sector = Counter(str(t.get("sector") or "UNKNOWN") for t in trades)
    theme = Counter(str(t.get("theme") or "unknown") for t in trades)
    return {
        "trade_count": n,
        "top_ticker": ticker.most_common(1)[0] if ticker else None,
        "top_sector": sector.most_common(1)[0] if sector else None,
        "top_theme": theme.most_common(1)[0] if theme else None,
        "top_ticker_pct": round(ticker.most_common(1)[0][1] / n, 4) if ticker and n else 0.0,
        "top_sector_pct": round(sector.most_common(1)[0][1] / n, 4) if sector and n else 0.0,
        "top_theme_pct": round(theme.most_common(1)[0][1] / n, 4) if theme and n else 0.0,
    }


def _works_outside_one_month(trades: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    by_month: Dict[str, List[float]] = defaultdict(list)
    for trade in trades:
        by_month[str(trade.get("exit_date") or trade.get("signal_date") or "")[:7]].append(
            float(trade.get("net_return") or 0.0)
        )
    month_returns: Dict[str, float] = {}
    for month, vals in by_month.items():
        equity = 1.0
        for value in vals:
            equity *= 1.0 + value
        month_returns[month] = equity - 1.0
    positive_months = sum(1 for v in month_returns.values() if v > 0)
    total = 1.0
    for value in month_returns.values():
        total *= 1.0 + value
    total_return = total - 1.0
    best_month = max(month_returns, key=month_returns.get) if month_returns else None
    best_value = month_returns[best_month] if best_month else 0.0
    best_share = best_value / total_return if total_return > 0 and best_value > 0 else None
    return {
        "month_count": len(month_returns),
        "positive_months": positive_months,
        "total_return": round(total_return, 6),
        "best_month": {"month": best_month, "return": round(best_value, 6)} if best_month else None,
        "best_positive_month_share": round(best_share, 4) if best_share is not None else None,
        "passes": bool(len(month_returns) >= 2 and positive_months >= 2 and (best_share is None or best_share <= 0.65)),
    }


def _daily_metrics_from_rows(
    rows: Sequence[Dict[str, Any]],
    *,
    start: str,
    end: str,
    mode: str,
    symbol: Optional[str] = None,
    exposure_pct: Optional[float] = None,
) -> Dict[str, Any]:
    if not rows:
        return {
            "mode": mode,
            "symbol": symbol,
            "total_return": 0.0,
            "cagr": 0.0,
            "max_drawdown": 0.0,
            "sharpe": None,
            "sortino": None,
            "exposure_pct": exposure_pct or 0.0,
            "daily_rows": [],
        }
    equity = [float(row.get("equity") or 1.0) for row in rows]
    daily_returns = [float(row.get("daily_return") or 0.0) for row in rows[1:]]
    month_returns = portfolio._month_returns_from_equity(rows)
    worst_month, best_month = portfolio._best_worst_month(month_returns)
    total = equity[-1] - 1.0
    return {
        "mode": mode,
        "symbol": symbol,
        "start": start,
        "end": end,
        "total_return": round(total, 6),
        "cagr": _round(portfolio._annualized_return(total, start, end)),
        "max_drawdown": round(portfolio._max_drawdown_from_equity(equity), 6),
        "sharpe": _round(portfolio._sharpe(daily_returns), 4),
        "sortino": _round(portfolio._sortino(daily_returns), 4),
        "exposure_pct": round(exposure_pct if exposure_pct is not None else 1.0, 6),
        "worst_month": worst_month,
        "best_month": best_month,
        "daily_rows": list(rows),
        "calmar": _calmar(portfolio._annualized_return(total, start, end), portfolio._max_drawdown_from_equity(equity)),
        "return_per_unit_exposure": _return_per_unit_exposure(total, exposure_pct if exposure_pct is not None else 1.0),
    }


def _scaled_benchmark(symbol: str, scale: float, *, start: str, end: str) -> Dict[str, Any]:
    base = portfolio.benchmark_metrics(symbol, start=start, end=end, cost_model=lab.BASE_COST)
    equity = 1.0
    rows = []
    for row in base.get("daily_rows") or []:
        daily_return = float(row.get("daily_return") or 0.0) * scale
        equity *= 1.0 + daily_return
        rows.append({
            "date": row["date"],
            "daily_return": round(daily_return, 8),
            "equity": round(equity, 8),
            "gross_exposure": scale,
        })
    metrics = _daily_metrics_from_rows(
        rows,
        start=start,
        end=end,
        mode="scaled_benchmark",
        symbol=f"{int(scale * 100)}% {symbol} / {int((1 - scale) * 100)}% cash",
        exposure_pct=scale,
    )
    metrics.pop("daily_rows", None)
    return metrics


def _same_exposure_benchmark(
    symbol: str,
    strategy_rows: Sequence[Dict[str, Any]],
    *,
    start: str,
    end: str,
) -> Dict[str, Any]:
    base = portfolio.benchmark_metrics(symbol, start=start, end=end, cost_model=lab.BASE_COST)
    ret_by_date = {row["date"]: float(row.get("daily_return") or 0.0) for row in base.get("daily_rows") or []}
    equity = 1.0
    rows = []
    exposures = []
    for srow in strategy_rows:
        exposure = max(0.0, min(1.0, float(srow.get("gross_exposure") or 0.0)))
        exposures.append(exposure)
        daily_return = ret_by_date.get(str(srow.get("date")), 0.0) * exposure
        equity *= 1.0 + daily_return
        rows.append({
            "date": srow.get("date"),
            "daily_return": round(daily_return, 8),
            "equity": round(equity, 8),
            "gross_exposure": round(exposure, 6),
        })
    exposure_pct = statistics.mean(exposures) if exposures else 0.0
    metrics = _daily_metrics_from_rows(
        rows,
        start=start,
        end=end,
        mode="same_exposure_benchmark",
        symbol=symbol,
        exposure_pct=exposure_pct,
    )
    metrics.pop("daily_rows", None)
    return metrics


def _benchmark_table(start: str, end: str) -> Dict[str, Any]:
    out = {}
    for symbol in ("SPY", "QQQ", "cash"):
        m = portfolio.benchmark_metrics(symbol, start=start, end=end, cost_model=lab.BASE_COST)
        out[symbol] = _augment_metrics(m)
    out["50% SPY / 50% cash"] = _scaled_benchmark("SPY", 0.50, start=start, end=end)
    out["50% QQQ / 50% cash"] = _scaled_benchmark("QQQ", 0.50, start=start, end=end)
    return out


def _exact_backtest_summary() -> Dict[str, Any]:
    artifact = _load_json(CACHE / "strategy_lab_full_windows_latest.json")
    windows = artifact.get("windows") or {}
    rows: Dict[str, Any] = {}
    for variant in EVAL_VARIANTS:
        weighted = []
        rel_spy = []
        rel_qqq = []
        n_total = 0
        worst_dd = 0.0
        for window in windows.values():
            m = ((window.get("by_cost") or {}).get("base_cost") or {}).get(variant) or {}
            n = int(m.get("trade_count") or 0)
            if n:
                n_total += n
                if m.get("expectancy") is not None:
                    weighted.append(float(m["expectancy"]) * n)
                if m.get("rel_spy") is not None:
                    rel_spy.append(float(m["rel_spy"]))
                if m.get("rel_qqq") is not None:
                    rel_qqq.append(float(m["rel_qqq"]))
                worst_dd = min(worst_dd, float(m.get("max_drawdown") or 0.0))
        verdict = ((artifact.get("variant_verdicts") or {}).get(variant) or {}).get("verdict")
        rows[variant] = {
            "trade_count": n_total,
            "expectancy": round(sum(weighted) / n_total, 6) if n_total and weighted else None,
            "rel_spy": round(statistics.mean(rel_spy), 6) if rel_spy else None,
            "rel_qqq": round(statistics.mean(rel_qqq), 6) if rel_qqq else None,
            "max_drawdown": round(worst_dd, 6),
            "strategy_lab_verdict": verdict,
        }
    return {
        "artifact": str(CACHE / "strategy_lab_full_windows_latest.json"),
        "generated_at": artifact.get("generated_at"),
        "mode": artifact.get("mode"),
        "rows": rows,
    }


def _walk_forward_summary() -> Dict[str, Any]:
    artifact = _load_json(CACHE / "strategy_walk_forward_exact_latest.json")
    out: Dict[str, Any] = {
        "artifact": str(CACHE / "strategy_walk_forward_exact_latest.json"),
        "generated_at": artifact.get("generated_at"),
        "available": bool(artifact),
        "rows": {},
    }
    for variant in EVAL_VARIANTS:
        row = ((artifact.get("final_by_variant") or {}).get(variant) or {})
        test = row.get("test") or {}
        out["rows"][variant] = {
            "verdict": row.get("verdict"),
            "param_id": row.get("param_id"),
            "test_trade_count": test.get("trade_count"),
            "test_expectancy": test.get("expectancy"),
            "test_rel_spy": test.get("rel_spy"),
            "test_rel_qqq": test.get("rel_qqq"),
            "test_max_drawdown": test.get("max_drawdown"),
            "overfit_risk": row.get("overfit_risk"),
            "blockers": row.get("blockers"),
        }
    return out


def _threshold_sweep_summary() -> Dict[str, Any]:
    artifact = _load_json(CACHE / "strategy_threshold_sweep_exact_latest.json")
    out: Dict[str, Any] = {
        "artifact": str(CACHE / "strategy_threshold_sweep_exact_latest.json"),
        "generated_at": artifact.get("generated_at"),
        "available": bool(artifact),
        "grid_size": artifact.get("grid_size"),
        "rows": {},
    }
    for variant in EVAL_VARIANTS:
        row = ((artifact.get("selected_by_variant") or {}).get(variant) or {})
        test = row.get("test_metrics") or {}
        out["rows"][variant] = {
            "param_id": row.get("param_id"),
            "validation_score": row.get("validation_score"),
            "test_trade_count": test.get("trade_count"),
            "test_expectancy": test.get("expectancy"),
            "test_rel_spy": test.get("rel_spy"),
            "test_rel_qqq": test.get("rel_qqq"),
            "test_max_drawdown": test.get("max_drawdown"),
            "overfit_risk": row.get("overfit_risk"),
        }
    return out


def _evaluate_behavior(correction: Dict[str, Any]) -> Dict[str, Any]:
    march = correction.get("march_2026") or {}
    family = correction.get("CORRECTION_FAMILY") or {}
    recovery = correction.get(regime.RECOVERY_RECLAIM) or {}
    return {
        "protects_capital_during_correction": bool(
            int(family.get("trade_count") or 0) > 0
            and _f(family.get("expectancy"), -1.0) >= -0.005
            and _f(family.get("max_drawdown")) >= -0.12
        ),
        "march_2026_behavior": "NO_TRADES" if not march.get("trade_count") else (
            "PROTECTIVE_OR_POSITIVE" if _f(march.get("expectancy"), -1.0) >= -0.005 else "LOSING"
        ),
        "participates_in_recovery": bool(
            int(recovery.get("trade_count") or 0) > 0 and _f(recovery.get("expectancy"), -1.0) > 0
        ),
        "beats_same_exposure_during_trade_windows": bool(
            _f(family.get("rel_spy"), -1.0) > 0 or _f(family.get("rel_qqq"), -1.0) > 0
        ),
    }


def _eligibility(
    variant: str,
    *,
    trades: Sequence[Dict[str, Any]],
    independent: Dict[str, Any],
    realistic: Dict[str, Any],
    same_spy: Dict[str, Any],
    same_qqq: Dict[str, Any],
    correction: Dict[str, Any],
    concentration: Dict[str, Any],
    month_check: Dict[str, Any],
    walk_forward: Dict[str, Any],
    threshold: Dict[str, Any],
) -> Dict[str, Any]:
    reasons: List[str] = []
    n = int(independent.get("trade_count") or 0)
    accepted = int(realistic.get("accepted_trade_count") or 0)
    ind_exp = _f(independent.get("expectancy"))
    ind_dd = _f(independent.get("max_drawdown"))
    real_total = _f(realistic.get("total_return"))
    real_cagr = _f(realistic.get("cagr"))
    real_dd = _f(realistic.get("max_drawdown"))
    same_best = max(_f(same_spy.get("total_return")), _f(same_qqq.get("total_return")))
    same_best_sharpe = max(_f(same_spy.get("sharpe"), -999.0), _f(same_qqq.get("sharpe"), -999.0))
    family = correction.get("CORRECTION_FAMILY") or {}
    wf_row = (walk_forward.get("rows") or {}).get(variant) or {}
    sweep_row = (threshold.get("rows") or {}).get(variant) or {}

    if variant == "QQQ_TECH_TACTICAL_SHORT":
        return {"label": REJECT, "reasons": ["kept only as rejected harmful baseline; not optimized"]}
    if variant == "RANDOM_LIQUID":
        return {"label": REJECT, "reasons": ["random-liquid control is not a strategy edge"]}
    if n == 0 or accepted == 0:
        return {"label": REJECT, "reasons": ["no usable trades under realistic portfolio constraints"]}
    if ind_exp <= 0:
        return {"label": REJECT, "reasons": ["independent-trade expectancy is not positive"]}
    if real_cagr <= 0 or real_total <= 0:
        return {"label": REJECT, "reasons": ["realistic portfolio CAGR/return is not positive after cash drag"]}

    if n < 40 or accepted < 20:
        reasons.append("minimum flow below paper-shadow threshold")
        if variant == "PROD_SNIPER_CURRENT":
            reasons.append("HIGH_QUALITY_LOW_FLOW")
            return {"label": LOW_FLOW, "reasons": reasons}
        return {"label": NEED_MORE, "reasons": reasons}

    if ind_dd < -0.30:
        reasons.append("independent-trade drawdown remains too high")
    if real_dd < -0.15:
        reasons.append("realistic max drawdown is not acceptable")
    if real_total <= same_best:
        reasons.append("does not beat same-exposure SPY/QQQ total return")
    if _f(realistic.get("sharpe"), -999.0) <= same_best_sharpe:
        reasons.append("does not improve same-exposure risk-adjusted return")
    if not month_check.get("passes"):
        reasons.append("edge is too month-dependent or lacks multiple positive months")
    if _f(concentration.get("top_ticker_pct")) > 0.35:
        reasons.append("ticker concentration above 35%")
    if _f(concentration.get("top_sector_pct")) > 0.70:
        reasons.append("sector concentration above 70%")
    if int(family.get("trade_count") or 0) == 0:
        reasons.append("no correction-family trades to validate correction behavior")
    elif _f(family.get("expectancy"), -1.0) < -0.005:
        reasons.append("correction-family expectancy is not protective")
    wf_verdict = wf_row.get("verdict")
    if wf_verdict == lab.VERDICT_NEED_MORE:
        reasons.append("walk-forward test sample too small; edge unconfirmed")
    elif wf_verdict not in {lab.VERDICT_EDGE, lab.VERDICT_READY}:
        reasons.append("walk-forward does not confirm the edge")
    if wf_row.get("overfit_risk") in {"HIGH_TEST_DECAY", "HIGH_TRAIN_ONLY"}:
        reasons.append(f"walk-forward overfit risk: {wf_row.get('overfit_risk')}")
    if sweep_row.get("overfit_risk") in {"HIGH_TEST_DECAY", "HIGH_TRAIN_ONLY"}:
        reasons.append(f"threshold sweep overfit risk: {sweep_row.get('overfit_risk')}")

    if not reasons:
        return {"label": CANDIDATE_LABEL, "reasons": ["all independent, portfolio, correction, benchmark, concentration, and validation gates passed"]}
    if any("drawdown" in r or "concentration" in r for r in reasons):
        return {"label": PORTFOLIO_RISK, "reasons": reasons}
    if any("overfit" in r or "does not confirm" in r or "month-dependent" in r or "same-exposure" in r for r in reasons):
        return {"label": OVERFIT, "reasons": reasons}
    return {"label": NEED_MORE, "reasons": reasons}


def _correction_rules_summary() -> Dict[str, Any]:
    return {
        "variant": "CORRECTION_LEADER_RECLAIM",
        "research_only": True,
        "entry": "next open after reclaim signal; close fallback if open unavailable",
        "exit": "Strategy Lab max-hold 5/10/20 capable; default max hold 10 with reclaim/ATR-derived stop metadata and generic target/trailing parameters",
        "allowed_regimes": sorted(lab.CORRECTION_RECLAIM_REGIMES),
        "long_requirements": [
            "market regime is MARKET_CORRECTION, TECH_LED_CORRECTION, CHOP, or RECOVERY_RECLAIM",
            "positive relative strength versus SPY/QQQ or sector over configured 20/40/60 lookback",
            "ticker avoids new-low behavior and has controlled pullback versus recent high and market drawdown",
            "price reclaims configured 10/20 EMA using bars available as of the signal date",
            "volume expands on reclaim or sell-volume dries up",
            "price remains above major trend support where computable",
            "earnings/fundamental future labels are not used because dated history is not retained",
            "illiquid names are filtered by price and average dollar-volume floor",
            "parabolic exhaustion is rejected unless there was a reset",
        ],
        "data_contract": "price-derived as-of features only; no Stock Lens, Gatekeeper, social, options, or future labels",
    }


def build_correction_strategy_report() -> Dict[str, Any]:
    start, end, primary_windows, unavailable = portfolio._primary_window_span()
    params = lab.StrategyParams()
    config = lab.BacktestConfig(universe_cap=140, date_stride=1)
    pconfig = portfolio.PortfolioConfig()

    regime_labels = regime.build_regime_labels(start=start, end=end)
    regime.write_outputs(regime_labels)

    collected_base = portfolio.collect_trades_for_span(
        start,
        end,
        variants=EVAL_VARIANTS,
        params=params,
        config=config,
        cost_model=lab.BASE_COST,
    )
    collected_high = portfolio.collect_trades_for_span(
        start,
        end,
        variants=EVAL_VARIANTS,
        params=params,
        config=config,
        cost_model=lab.HIGH_COST,
    )

    benchmarks = _benchmark_table(start, end)
    exact = _exact_backtest_summary()
    walk_forward = _walk_forward_summary()
    threshold = _threshold_sweep_summary()

    results: Dict[str, Any] = {}
    correction_comparison: Dict[str, Any] = {}
    same_exposure_table: Dict[str, Any] = {}

    for variant in EVAL_VARIANTS:
        trades = collected_base["trades_by_variant"].get(variant, [])
        high_trades = collected_high["trades_by_variant"].get(variant, [])
        independent = portfolio.independent_trade_metrics(trades, start=start, end=end)
        basket = portfolio.equal_weight_basket_metrics(trades, start=start, end=end)
        realistic = portfolio.realistic_portfolio_metrics(trades, start=start, end=end, config=pconfig)
        realistic_high = portfolio.realistic_portfolio_metrics(high_trades, start=start, end=end, config=pconfig)
        same_spy = _same_exposure_benchmark("SPY", realistic.get("daily_rows") or [], start=start, end=end)
        same_qqq = _same_exposure_benchmark("QQQ", realistic.get("daily_rows") or [], start=start, end=end)
        correction = _correction_slice_metrics(trades)
        concentration = _concentration(trades)
        month_check = _works_outside_one_month(trades)
        behavior = _evaluate_behavior(correction)
        eligibility = _eligibility(
            variant,
            trades=trades,
            independent=independent,
            realistic=realistic,
            same_spy=same_spy,
            same_qqq=same_qqq,
            correction=correction,
            concentration=concentration,
            month_check=month_check,
            walk_forward=walk_forward,
            threshold=threshold,
        )
        results[variant] = {
            "independent_trade": _augment_metrics(independent),
            "equal_weight_basket": _augment_metrics(basket),
            "realistic_portfolio_base_cost": _augment_metrics(realistic),
            "realistic_portfolio_high_cost": _augment_metrics(realistic_high),
            "same_exposure_spy": same_spy,
            "same_exposure_qqq": same_qqq,
            "correction_only": correction,
            "correction_behavior": behavior,
            "concentration": concentration,
            "outside_one_month": month_check,
            "paper_shadow_eligibility": eligibility,
        }
        correction_comparison[variant] = {
            name: {
                key: (correction.get(name) or {}).get(key)
                for key in ("trade_count", "expectancy", "rel_spy", "rel_qqq", "max_drawdown", "win_rate")
            }
            for name in CORRECTION_SLICES
        }
        same_exposure_table[variant] = {
            "realistic_total_return": realistic.get("total_return"),
            "realistic_cagr": realistic.get("cagr"),
            "realistic_max_drawdown": realistic.get("max_drawdown"),
            "realistic_sharpe": realistic.get("sharpe"),
            "same_exposure_spy_total_return": same_spy.get("total_return"),
            "same_exposure_spy_sharpe": same_spy.get("sharpe"),
            "same_exposure_qqq_total_return": same_qqq.get("total_return"),
            "same_exposure_qqq_sharpe": same_qqq.get("sharpe"),
        }

    candidates = [
        variant for variant, row in results.items()
        if row["paper_shadow_eligibility"]["label"] == CANDIDATE_LABEL
    ]
    maybe = [
        variant for variant, row in results.items()
        if row["paper_shadow_eligibility"]["label"] in {LOW_FLOW, NEED_MORE}
        and variant in {"CORRECTION_LEADER_RECLAIM", "PROD_SNIPER_CURRENT", "RECALL_SHADOW_PULLBACK", "POWER_TREND_EXTENSION"}
    ]
    if candidates:
        top_line = "YES: PAPER_SHADOW_CANDIDATE FOUND"
    elif maybe:
        top_line = "MAYBE: NEED_MORE_DATA"
    else:
        top_line = "NO: NO_VARIANT_READY"

    proposal_created = False
    if candidates and candidates == ["CORRECTION_LEADER_RECLAIM"]:
        PROPOSAL_DOC.write_text(render_proposal_doc(results["CORRECTION_LEADER_RECLAIM"]), encoding="utf-8")
        proposal_created = True

    verdict_rows = {
        variant: {
            "label": row["paper_shadow_eligibility"]["label"],
            "reasons": row["paper_shadow_eligibility"].get("reasons"),
            "realistic_total_return": row["realistic_portfolio_base_cost"].get("total_return"),
            "realistic_cagr": row["realistic_portfolio_base_cost"].get("cagr"),
            "realistic_max_drawdown": row["realistic_portfolio_base_cost"].get("max_drawdown"),
            "realistic_sharpe": row["realistic_portfolio_base_cost"].get("sharpe"),
            "accepted_trades": row["realistic_portfolio_base_cost"].get("accepted_trade_count"),
            "correction_family_expectancy": row["correction_only"].get("CORRECTION_FAMILY", {}).get("expectancy"),
        }
        for variant, row in results.items()
    }

    return {
        "kind": "strategy_lab_correction_strategy",
        "version": VERSION,
        "generated_at": _utc_now(),
        "research_only": True,
        "status": "RESEARCH_ONLY",
        "top_line_result": top_line,
        "did_we_find_profitable_candidate": top_line,
        "signal_window": {"start": start, "end": end},
        "primary_non_overlapping_windows": primary_windows,
        "unavailable_windows": unavailable,
        "config": config.as_dict(),
        "params": params.as_dict(),
        "portfolio_config": pconfig.as_dict(),
        "cost_models": {"base": lab.BASE_COST, "high": lab.HIGH_COST},
        "regime_labels": {
            "artifact": str(regime.OUT_JSON),
            "label_counts": regime_labels.get("label_counts"),
            "march_2026": regime_labels.get("march_2026"),
            "rules": regime_labels.get("rules"),
        },
        "correction_leader_reclaim_rules": _correction_rules_summary(),
        "collection": {
            "base_cost": {k: v for k, v in collected_base.items() if k != "trades_by_variant"},
            "high_cost": {k: v for k, v in collected_high.items() if k != "trades_by_variant"},
        },
        "exact_backtest_results": exact,
        "walk_forward_results": walk_forward,
        "threshold_sweep_results": threshold,
        "benchmarks": benchmarks,
        "same_exposure_benchmark_results": same_exposure_table,
        "results": results,
        "correction_only_comparison": correction_comparison,
        "updated_variant_verdicts": verdict_rows,
        "paper_shadow": {
            "proposal_created": proposal_created,
            "proposal_doc": str(PROPOSAL_DOC) if proposal_created else None,
            "status": CANDIDATE_LABEL if candidates else "NO_VARIANT_READY_FOR_PAPER_SHADOW",
            "candidates": candidates,
            "reason": "No strategy is activated; proposal doc is written only if all gates pass.",
        },
        "operator_decision": (
            "Do not activate paper-shadow. Continue research/data collection."
            if not candidates else (
                "Review proposal doc manually; do not activate automatically."
                if proposal_created else
                "Candidate gates passed but no proposal doc applies; manual operator review required; do not activate automatically."
            )
        ),
        "safety": lab.safety_confirmations(),
    }


def render_proposal_doc(row: Dict[str, Any]) -> str:
    real = row.get("realistic_portfolio_base_cost") or {}
    return "\n".join([
        "# CORRECTION_LEADER_RECLAIM Paper-Shadow Proposal",
        "",
        "Manual review required. This document does not activate paper-shadow.",
        "",
        f"Realistic total return: {_pct(real.get('total_return'))}",
        f"Realistic max drawdown: {_pct(real.get('max_drawdown'))}",
        f"Realistic Sharpe: {real.get('sharpe')}",
        "",
    ])


def render_text(res: Dict[str, Any]) -> List[str]:
    lines = [
        f"STRATEGY LAB CORRECTION STRATEGY - {res['generated_at']}",
        lab.RESEARCH_DISCLAIMER,
        f"top_line={res['top_line_result']}",
        f"window={res['signal_window']['start']}..{res['signal_window']['end']}",
        f"march_2026_labels={res['regime_labels']['march_2026']['label_counts']}",
        f"paper_shadow={res['paper_shadow']['status']} proposal_created={res['paper_shadow']['proposal_created']}",
        "",
        f"{'variant':32s} {'verdict':30s} {'ret':>9s} {'cagr':>9s} {'maxDD':>9s} {'sharpe':>8s} {'corr_exp':>9s}",
    ]
    for variant, row in res.get("updated_variant_verdicts", {}).items():
        lines.append(
            f"{variant:32s} {row['label']:30s} "
            f"{_pct(row.get('realistic_total_return')):>9s} "
            f"{_pct(row.get('realistic_cagr')):>9s} "
            f"{_pct(row.get('realistic_max_drawdown')):>9s} "
            f"{str(row.get('realistic_sharpe')):>8s} "
            f"{_pct(row.get('correction_family_expectancy')):>9s}"
        )
    return lines


def render_doc(res: Dict[str, Any]) -> str:
    lines = [
        "# Strategy Lab Correction Strategy Results",
        "",
        f"Generated: {res['generated_at']}",
        "",
        lab.RESEARCH_DISCLAIMER,
        "",
        "## Top-Line Result",
        "",
        f"Did we find a profitable trading candidate? **{res['top_line_result']}**",
        "",
        f"Signal window: `{res['signal_window']['start']}` to `{res['signal_window']['end']}`.",
        "",
        "## March 2026 Regime Label",
        "",
        f"March 2026 label counts: `{res['regime_labels']['march_2026']['label_counts']}`.",
        f"Manual override used: `{res['regime_labels']['march_2026'].get('manual_override_used')}`.",
        "",
        "## CORRECTION_LEADER_RECLAIM Rules",
        "",
    ]
    rules = res.get("correction_leader_reclaim_rules") or {}
    lines.append(f"Entry: {rules.get('entry')}")
    lines.append("")
    lines.append(f"Exit: {rules.get('exit')}")
    lines.append("")
    lines.extend(f"- {x}" for x in rules.get("long_requirements") or [])
    lines += [
        "",
        "## Portfolio-Mode Results",
        "",
        "| Variant | Verdict | Accepted | Return | CAGR | MaxDD | Sharpe | Exposure | Correction Exp |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for variant, row in res.get("results", {}).items():
        real = row.get("realistic_portfolio_base_cost") or {}
        corr = (row.get("correction_only") or {}).get("CORRECTION_FAMILY") or {}
        verdict = (row.get("paper_shadow_eligibility") or {}).get("label")
        lines.append(
            f"| {variant} | {verdict} | {int(real.get('accepted_trade_count') or 0)} | "
            f"{_pct(real.get('total_return'))} | {_pct(real.get('cagr'))} | {_pct(real.get('max_drawdown'))} | "
            f"{real.get('sharpe')} | {_pct(real.get('exposure_pct'))} | {_pct(corr.get('expectancy'))} |"
        )
    lines += [
        "",
        "## Exact Backtest Results",
        "",
        "| Variant | Trades | Expectancy | Rel SPY | Rel QQQ | MaxDD | Lab Verdict |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for variant, row in (res.get("exact_backtest_results") or {}).get("rows", {}).items():
        lines.append(
            f"| {variant} | {int(row.get('trade_count') or 0)} | {_pct(row.get('expectancy'))} | "
            f"{_pct(row.get('rel_spy'))} | {_pct(row.get('rel_qqq'))} | {_pct(row.get('max_drawdown'))} | "
            f"{row.get('strategy_lab_verdict')} |"
        )
    lines += [
        "",
        "## Correction-Only Results",
        "",
        "| Variant | March N/Exp | Market Corr N/Exp | Tech Corr N/Exp | Recovery N/Exp | Family N/Exp |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for variant, slices in res.get("correction_only_comparison", {}).items():
        def cell(name: str) -> str:
            row = slices.get(name) or {}
            return f"{int(row.get('trade_count') or 0)} / {_pct(row.get('expectancy'))}"
        lines.append(
            f"| {variant} | {cell('march_2026')} | {cell(regime.MARKET_CORRECTION)} | "
            f"{cell(regime.TECH_LED_CORRECTION)} | {cell(regime.RECOVERY_RECLAIM)} | {cell('CORRECTION_FAMILY')} |"
        )
    lines += [
        "",
        "## Same-Exposure Benchmarks",
        "",
        "| Variant | Strategy Return | Same-Exp SPY | Same-Exp QQQ | Strategy Sharpe | Same-Exp SPY Sharpe | Same-Exp QQQ Sharpe |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for variant, row in res.get("same_exposure_benchmark_results", {}).items():
        lines.append(
            f"| {variant} | {_pct(row.get('realistic_total_return'))} | "
            f"{_pct(row.get('same_exposure_spy_total_return'))} | {_pct(row.get('same_exposure_qqq_total_return'))} | "
            f"{row.get('realistic_sharpe')} | {row.get('same_exposure_spy_sharpe')} | {row.get('same_exposure_qqq_sharpe')} |"
        )
    lines += [
        "",
        "## Walk-Forward Results",
        "",
        "| Variant | Verdict | Test N | Test Exp | Overfit Risk | Blockers |",
        "|---|---|---:|---:|---|---|",
    ]
    for variant, row in (res.get("walk_forward_results") or {}).get("rows", {}).items():
        lines.append(
            f"| {variant} | {row.get('verdict')} | {int(row.get('test_trade_count') or 0)} | "
            f"{_pct(row.get('test_expectancy'))} | {row.get('overfit_risk')} | {', '.join(row.get('blockers') or [])} |"
        )
    lines += [
        "",
        "## Threshold Sweep Results",
        "",
        "| Variant | Param | Test N | Test Exp | Overfit Risk |",
        "|---|---:|---:|---:|---|",
    ]
    for variant, row in (res.get("threshold_sweep_results") or {}).get("rows", {}).items():
        lines.append(
            f"| {variant} | {row.get('param_id')} | {int(row.get('test_trade_count') or 0)} | "
            f"{_pct(row.get('test_expectancy'))} | {row.get('overfit_risk')} |"
        )
    lines += [
        "",
        "## Benchmark Table",
        "",
        "| Benchmark | Return | CAGR | MaxDD | Sharpe | Exposure |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, row in res.get("benchmarks", {}).items():
        lines.append(
            f"| {name} | {_pct(row.get('total_return'))} | {_pct(row.get('cagr'))} | "
            f"{_pct(row.get('max_drawdown'))} | {row.get('sharpe')} | {_pct(row.get('exposure_pct'))} |"
        )
    lines += [
        "",
        "## Updated Variant Verdicts",
        "",
        "| Variant | Verdict | Reasons |",
        "|---|---|---|",
    ]
    for variant, row in res.get("updated_variant_verdicts", {}).items():
        lines.append(f"| {variant} | {row.get('label')} | {'; '.join(row.get('reasons') or [])} |")
    lines += [
        "",
        "## Paper-Shadow Eligibility",
        "",
        f"Status: `{res['paper_shadow']['status']}`",
        f"Proposal created: `{res['paper_shadow']['proposal_created']}`",
        "",
        "No paper-shadow was activated. No paper signals, broker orders, production thresholds, Gatekeeper/Veto logic, execution/governance/live-capital modules, historical evidence, or SHORT_A frozen status were changed.",
        "",
        "## Recommended Operator Decision",
        "",
        res.get("operator_decision", "Do not activate paper-shadow."),
        "",
    ]
    return "\n".join(lines)


def write_outputs(res: Dict[str, Any]) -> None:
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_TXT.parent.mkdir(parents=True, exist_ok=True)
    OUT_DOC.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    OUT_TXT.write_text("\n".join(render_text(res)) + "\n", encoding="utf-8")
    OUT_DOC.write_text(render_doc(res) + "\n", encoding="utf-8")


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Phase 1H.3 correction strategy report (research-only)")
    ap.parse_args(argv)
    res = build_correction_strategy_report()
    write_outputs(res)
    print("\n".join(render_text(res)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

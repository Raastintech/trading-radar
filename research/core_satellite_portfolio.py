#!/usr/bin/env python3
"""
research/core_satellite_portfolio.py - Phase 2A core-satellite engine test.

Research-only feasibility test of a top-level portfolio engine: can
regime-throttled index exposure be the compounding core, with the existing
SNIPER / frozen LRR_C1 sleeves as small satellites? Daily-resolution exact
simulation over the retained price history with strictly as-of regime labels
(exposure decided at close t applies to the t->t+1 return; day 0 is cash).

Never imports broker, execution, governance, paper-signal, or live-capital
modules. Emits no signals; writes cache/log/doc artifacts only. A paper
proposal DOC is written only if a core variant passes every pre-registered
gate; otherwise the verdict is NO_CORE_ENGINE_READY.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from research import lrr_clustered_portfolio as lrr_clus  # noqa: E402
from research import strategy_lab_portfolio as portfolio  # noqa: E402
from research import strategy_lab_regime as regime  # noqa: E402
from research import strategy_research_lab as lab  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "cache" / "research"
LOGS = ROOT / "logs"
DOCS = ROOT / "docs" / "research"

OUT_JSON = CACHE / "core_satellite_portfolio_latest.json"
OUT_TXT = LOGS / "core_satellite_portfolio_latest.txt"
OUT_DOC = DOCS / "CORE_SATELLITE_PORTFOLIO_RESULTS.md"
SCENARIOS_DOC = DOCS / "PORTFOLIO_COMPOUNDING_SCENARIOS.md"
PROPOSAL_DOC = DOCS / "CORE_SATELLITE_PAPER_PROPOSAL.md"

VERSION = "CORE_SATELLITE_PORTFOLIO_V1"

# Verdicts (Task 7).
REJECT = "REJECT"
NEED_MORE = "NEED_MORE_DATA"
CORE_CANDIDATE = "CORE_ENGINE_CANDIDATE"
SATELLITE_VALUE_ADD = "SATELLITE_VALUE_ADD"
OBSERVATION_ONLY = "OBSERVATION_ONLY"
NOT_READY = "NOT_READY_FOR_PAPER"
BENCHMARK = "BENCHMARK"
NO_CORE_READY = "NO_CORE_ENGINE_READY"

# Pre-registered exposure ladder (see CORE_SATELLITE_REGIME_SPEC.md).
REGIME_EXPOSURE: Dict[Optional[str], float] = {
    regime.BULL_TREND: 1.00,
    regime.RECOVERY_RECLAIM: 1.00,
    regime.CHOP: 0.60,
    regime.HIGH_VOLATILITY: 0.60,
    regime.MARKET_CORRECTION: 0.40,
    regime.TECH_LED_CORRECTION: 0.40,
    regime.RISK_OFF: 0.10,
}

# Pre-registered hard gates for CORE_ENGINE_CANDIDATE.
GATE_MAXDD_RATIO_VS_QQQ = 0.70      # maxDD must be <= 70% of QQQ's, OR
GATE_RETURN_MUST_BEAT_QQQ = True    # ... total return must beat QQQ
GATE_MAX_MONTH_SHARE = 0.65
GATE_MIN_POSITIVE_MONTHS = 2
GATE_MAX_CHANGE_DAYS_PCT = 0.25
GATE_MAX_TURNOVER_PER_YEAR = 25.0

SAT_SNIPER = "SNIPER_SATELLITE"
SAT_LRR = "LRR_C1_SATELLITE"


@dataclass(frozen=True)
class VariantSpec:
    variant_id: str
    kind: str                      # buy_hold | static | throttled | throttled_blend
    asset: str = "QQQ"             # for buy_hold/static/throttled
    static_weight: float = 1.0
    satellites: Tuple[Tuple[str, float], ...] = ()


VARIANTS: Sequence[VariantSpec] = (
    VariantSpec("BENCHMARK_SPY_BUY_HOLD", "buy_hold", asset="SPY"),
    VariantSpec("BENCHMARK_QQQ_BUY_HOLD", "buy_hold", asset="QQQ"),
    VariantSpec("STATIC_60_QQQ_40_CASH", "static", asset="QQQ", static_weight=0.60),
    VariantSpec("STATIC_80_QQQ_20_CASH", "static", asset="QQQ", static_weight=0.80),
    VariantSpec("REGIME_THROTTLED_QQQ", "throttled", asset="QQQ"),
    VariantSpec("REGIME_THROTTLED_SPY_QQQ_BLEND", "throttled_blend"),
    VariantSpec("CORE_PLUS_SNIPER_OBSERVATION", "throttled", asset="QQQ", satellites=((SAT_SNIPER, 0.10),)),
    VariantSpec("CORE_PLUS_LRR_C1_WATCH", "throttled", asset="QQQ", satellites=((SAT_LRR, 0.10),)),
    VariantSpec("CORE_PLUS_BOTH_SATELLITES", "throttled", asset="QQQ", satellites=((SAT_SNIPER, 0.075), (SAT_LRR, 0.075))),
)

CORE_VARIANT_IDS = {"REGIME_THROTTLED_QQQ", "REGIME_THROTTLED_SPY_QQQ_BLEND"}
SATELLITE_VARIANT_IDS = {"CORE_PLUS_SNIPER_OBSERVATION", "CORE_PLUS_LRR_C1_WATCH", "CORE_PLUS_BOTH_SATELLITES"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


_f = lab._f


def _pct(value: Any) -> str:
    return "n/a" if value is None else f"{float(value) * 100:+.2f}%"


# ---------------------------------------------------------------------------
# Data: daily closes + as-of regimes
# ---------------------------------------------------------------------------

def load_daily_returns(symbol: str, start: str, end: str) -> pd.Series:
    """Close-to-close daily returns from retained cached bars only."""
    frame = lab.d._full_frame(symbol)
    if frame is None or frame.empty:
        return pd.Series(dtype=float)
    closes = frame["close"].astype(float)
    closes = closes[(closes.index >= pd.Timestamp(start)) & (closes.index <= pd.Timestamp(end))]
    return closes.pct_change().fillna(0.0)


def build_regime_series(dates: Sequence[pd.Timestamp]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for ts in dates:
        row = regime.classify_regime(ts)
        out[str(ts.date())] = {
            "label": row.get("label"),
            "qqq_vs_spy_20": (row.get("inputs") or {}).get("qqq_vs_spy_20"),
        }
    return out


def target_exposure(label: Optional[str]) -> float:
    return REGIME_EXPOSURE.get(label, 0.0)


def blend_asset(qqq_vs_spy_20: Any) -> str:
    return "QQQ" if _f(qqq_vs_spy_20) >= 0 else "SPY"


# ---------------------------------------------------------------------------
# Daily simulation (no lookahead: weight from prior close's regime)
# ---------------------------------------------------------------------------

def simulate_variant(
    spec: VariantSpec,
    *,
    dates: Sequence[str],
    returns: Dict[str, pd.Series],
    regimes: Dict[str, Dict[str, Any]],
    satellite_returns: Dict[str, Dict[str, float]],
) -> List[Dict[str, Any]]:
    """Returns daily rows: date, daily_return, equity, gross_exposure, weights.

    The exposure applied to day i's return is decided from day i-1's regime
    (as-of close). Day 0 is cash for regime-driven variants. Satellite weights
    are fixed-capital, daily-rebalanced; core weight scales by (1 - sat_total).
    Cash earns 0%.
    """
    sat_total = sum(cap for _, cap in spec.satellites)
    equity = 1.0
    rows: List[Dict[str, Any]] = []
    for i, day in enumerate(dates):
        if spec.kind == "buy_hold":
            core_w, asset = 1.0, spec.asset
        elif spec.kind == "static":
            core_w, asset = spec.static_weight, spec.asset
        else:
            prior = regimes.get(dates[i - 1]) if i > 0 else None
            if prior is None:
                core_w, asset = 0.0, spec.asset if spec.kind != "throttled_blend" else "QQQ"
            else:
                core_w = target_exposure(prior.get("label"))
                asset = blend_asset(prior.get("qqq_vs_spy_20")) if spec.kind == "throttled_blend" else spec.asset
        core_w = core_w * (1.0 - sat_total)
        r_core = float(returns[asset].get(pd.Timestamp(day), 0.0))
        daily = core_w * r_core
        sat_exposure = 0.0
        for sat_name, cap in spec.satellites:
            r_sat = float((satellite_returns.get(sat_name) or {}).get(day, 0.0))
            daily += cap * r_sat
            sat_exposure += cap
        equity *= 1.0 + daily
        rows.append({
            "date": day,
            "daily_return": round(daily, 8),
            "equity": round(equity, 8),
            "gross_exposure": round(core_w + sat_exposure, 6),
            "core_weight": round(core_w, 6),
            "satellite_weight": round(sat_exposure, 6),
            "cash_weight": round(max(0.0, 1.0 - core_w - sat_exposure), 6),
            "asset": asset,
        })
    return rows


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def month_returns(rows: Sequence[Dict[str, Any]]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    by_month: Dict[str, List[float]] = {}
    for row in rows:
        by_month.setdefault(str(row["date"])[:7], []).append(float(row["daily_return"]))
    for month, vals in sorted(by_month.items()):
        eq = 1.0
        for v in vals:
            eq *= 1.0 + v
        out[month] = round(eq - 1.0, 6)
    return out


def metrics_from_rows(rows: Sequence[Dict[str, Any]], *, start: str, end: str) -> Dict[str, Any]:
    if not rows:
        return {"total_return": 0.0}
    equity = [float(r["equity"]) for r in rows]
    daily = [float(r["daily_return"]) for r in rows[1:]]
    months = month_returns(rows)
    pos_months = [m for m, v in months.items() if v > 0]
    best_m = max(months, key=months.get) if months else None
    worst_m = min(months, key=months.get) if months else None
    total = equity[-1] - 1.0
    best_share = None
    if best_m and total > 0 and months[best_m] > 0:
        best_share = round(months[best_m] / total, 4)
    weights = [float(r["gross_exposure"]) for r in rows]
    changes = sum(1 for i in range(1, len(weights)) if abs(weights[i] - weights[i - 1]) > 1e-9)
    turnover = sum(abs(weights[i] - weights[i - 1]) for i in range(1, len(weights)))
    years = max((pd.Timestamp(end) - pd.Timestamp(start)).days / 365.25, 1e-9)
    vol = statistics.pstdev(daily) * math.sqrt(252) if len(daily) > 1 else None
    cagr = portfolio._annualized_return(total, start, end)
    max_dd = portfolio._max_drawdown_from_equity(equity)
    exposure = statistics.mean(weights) if weights else 0.0
    return {
        "total_return": round(total, 6),
        "cagr": round(cagr, 6) if cagr is not None else None,
        "max_drawdown": round(max_dd, 6),
        "sharpe": portfolio._round(portfolio._sharpe(daily), 4),
        "sortino": portfolio._round(portfolio._sortino(daily), 4),
        "calmar": round(cagr / abs(max_dd), 4) if cagr is not None and max_dd < 0 else None,
        "volatility_annualized": round(vol, 6) if vol is not None else None,
        "monthly_hit_rate": round(len(pos_months) / len(months), 4) if months else None,
        "positive_months": len(pos_months),
        "month_count": len(months),
        "worst_month": {"month": worst_m, "return": months.get(worst_m)} if worst_m else None,
        "best_month": {"month": best_m, "return": months.get(best_m)} if best_m else None,
        "best_month_share_of_total": best_share,
        "exposure_pct": round(exposure, 6),
        "time_in_market_pct": round(sum(1 for w in weights if w > 0.05) / len(weights), 4),
        "turnover_per_year": round(turnover / years, 4),
        "exposure_change_days_pct": round(changes / max(1, len(weights) - 1), 4),
        "return_per_unit_exposure": round(total / exposure, 6) if exposure > 0 else None,
        "month_returns": months,
    }


def year_slices(rows: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out = {}
    for year in ("2024", "2025", "2026"):
        subset = [r for r in rows if str(r["date"]).startswith(year)]
        if not subset:
            continue
        eq0 = 1.0
        sub_rows = []
        for r in subset:
            eq0 *= 1.0 + float(r["daily_return"])
            sub_rows.append({**r, "equity": eq0})
        out[year] = {
            "total_return": round(eq0 - 1.0, 6),
            "max_drawdown": round(portfolio._max_drawdown_from_equity([x["equity"] for x in sub_rows]), 6),
        }
    return out


def rolling_3m(rows: Sequence[Dict[str, Any]], window: int = 63) -> Dict[str, Any]:
    daily = [float(r["daily_return"]) for r in rows]
    if len(daily) < window + 1:
        return {"windows": 0}
    rets = []
    for i in range(window, len(daily) + 1):
        eq = 1.0
        for v in daily[i - window:i]:
            eq *= 1.0 + v
        rets.append(eq - 1.0)
    return {
        "windows": len(rets),
        "min": round(min(rets), 6),
        "median": round(statistics.median(rets), 6),
        "max": round(max(rets), 6),
        "pct_positive": round(sum(1 for r in rets if r > 0) / len(rets), 4),
    }


# ---------------------------------------------------------------------------
# Satellites (realistic sleeve daily returns; includes their cash drag)
# ---------------------------------------------------------------------------

def satellite_daily_returns(start: str, end: str) -> Dict[str, Dict[str, float]]:
    params = lab.StrategyParams()
    config = lab.BacktestConfig(universe_cap=140, date_stride=1)
    collected = portfolio.collect_trades_for_span(
        start, end, variants=("PROD_SNIPER_CURRENT", "LRR_REGIME_GATED"),
        params=params, config=config, cost_model=lab.BASE_COST,
    )
    out: Dict[str, Dict[str, float]] = {}
    sniper_trades = collected["trades_by_variant"].get("PROD_SNIPER_CURRENT", [])
    real = portfolio.realistic_portfolio_metrics(sniper_trades, start=start, end=end, config=portfolio.PortfolioConfig())
    out[SAT_SNIPER] = {str(r["date"]): float(r["daily_return"]) for r in real.get("daily_rows") or []}

    # LRR_C1: frozen 1I.2 spec — LRR_REGIME_GATED stream + max-1-entry-per-day.
    gated = collected["trades_by_variant"].get("LRR_REGIME_GATED", [])
    cal = [str(pd.Timestamp(d).date()) for d in lab.d.trading_dates_between(start, end)]
    c1 = next(c for c in lrr_clus.CONFIGS if c.config_id == "C1_MAX_1_PER_DAY")
    filtered = lrr_clus.apply_entry_filters(gated, c1, cal)
    real_lrr = portfolio.realistic_portfolio_metrics(filtered, start=start, end=end, config=portfolio.PortfolioConfig())
    out[SAT_LRR] = {str(r["date"]): float(r["daily_return"]) for r in real_lrr.get("daily_rows") or []}
    return out


# ---------------------------------------------------------------------------
# Gates and verdicts
# ---------------------------------------------------------------------------

def core_gates(m: Dict[str, Any], years: Dict[str, Any], qqq: Dict[str, Any], spy: Dict[str, Any]) -> Dict[str, Any]:
    ytd = (years.get("2026") or {}).get("total_return")
    dd_ok = _f(m.get("max_drawdown"), -1.0) >= GATE_MAXDD_RATIO_VS_QQQ * _f(qqq.get("max_drawdown"), -1.0)
    ret_ok = _f(m.get("total_return")) > _f(qqq.get("total_return"))
    sharpe_ok = _f(m.get("sharpe"), -9.0) > max(_f(qqq.get("sharpe"), -9.0), _f(spy.get("sharpe"), -9.0))
    calmar_ok = _f(m.get("calmar"), -9.0) > max(_f(qqq.get("calmar"), -9.0), _f(spy.get("calmar"), -9.0))
    gates = {
        "no_lookahead": {"passed": True, "detail": "weight uses prior-close regime by construction (test-pinned)"},
        "full_span_positive": {"passed": _f(m.get("total_return")) > 0, "detail": _pct(m.get("total_return"))},
        "ytd_2026_positive": {"passed": ytd is not None and ytd > 0, "detail": _pct(ytd)},
        "risk_or_return_vs_qqq": {
            "passed": dd_ok or ret_ok,
            "detail": f"maxDD {_pct(m.get('max_drawdown'))} vs QQQ {_pct(qqq.get('max_drawdown'))} (need <=70%), return {_pct(m.get('total_return'))} vs QQQ {_pct(qqq.get('total_return'))}",
        },
        "sharpe_or_calmar_beats_index": {
            "passed": sharpe_ok or calmar_ok,
            "detail": f"sharpe {m.get('sharpe')} vs max(SPY {spy.get('sharpe')}, QQQ {qqq.get('sharpe')}); calmar {m.get('calmar')} vs max({spy.get('calmar')}, {qqq.get('calmar')})",
        },
        "not_one_month_dependent": {
            "passed": (m.get("positive_months") or 0) >= GATE_MIN_POSITIVE_MONTHS
            and (m.get("best_month_share_of_total") is None or m.get("best_month_share_of_total") <= GATE_MAX_MONTH_SHARE),
            "detail": f"positive months {m.get('positive_months')}/{m.get('month_count')}, best share {m.get('best_month_share_of_total')}",
        },
        "churn_acceptable": {
            "passed": _f(m.get("exposure_change_days_pct")) <= GATE_MAX_CHANGE_DAYS_PCT
            and _f(m.get("turnover_per_year")) <= GATE_MAX_TURNOVER_PER_YEAR,
            "detail": f"change days {m.get('exposure_change_days_pct')} (max {GATE_MAX_CHANGE_DAYS_PCT}), turnover/yr {m.get('turnover_per_year')} (max {GATE_MAX_TURNOVER_PER_YEAR})",
        },
        "simple_to_operate": {"passed": True, "detail": "7-label ladder, one decision per close, daily-or-slower rebalance"},
    }
    gates["all_passed"] = all(v["passed"] for k, v in gates.items() if isinstance(v, dict))
    return gates


def satellite_assessment(sat_m: Dict[str, Any], core_m: Dict[str, Any]) -> Dict[str, Any]:
    d_cagr = _f(sat_m.get("cagr")) - _f(core_m.get("cagr"))
    d_sharpe = _f(sat_m.get("sharpe"), -9.0) - _f(core_m.get("sharpe"), -9.0)
    d_dd = _f(sat_m.get("max_drawdown"), -1.0) - _f(core_m.get("max_drawdown"), -1.0)
    improves = d_sharpe > 0 and d_cagr > -0.001 and d_dd > -0.01
    return {
        "delta_cagr": round(d_cagr, 6),
        "delta_sharpe": round(d_sharpe, 4),
        "delta_max_drawdown": round(d_dd, 6),
        "verdict": SATELLITE_VALUE_ADD if improves else OBSERVATION_ONLY,
        "detail": (
            "satellite improves risk-adjusted performance" if improves
            else "satellite adds complexity without risk-adjusted improvement; keep observation-only"
        ),
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def build_report(*, start: str = "2024-01-02", end: str = "2026-06-11", skip_satellites: bool = False) -> Dict[str, Any]:
    spy_ret = load_daily_returns("SPY", start, end)
    qqq_ret = load_daily_returns("QQQ", start, end)
    common = sorted(set(spy_ret.index) & set(qqq_ret.index))
    dates = [str(pd.Timestamp(d).date()) for d in common]
    returns = {"SPY": spy_ret, "QQQ": qqq_ret}
    regimes = build_regime_series(common)
    sat_streams: Dict[str, Dict[str, float]] = {}
    if not skip_satellites:
        sat_streams = satellite_daily_returns(start, end)

    results: Dict[str, Any] = {}
    rows_by_variant: Dict[str, List[Dict[str, Any]]] = {}
    for spec in VARIANTS:
        if spec.satellites and skip_satellites:
            continue
        rows = simulate_variant(spec, dates=dates, returns=returns, regimes=regimes, satellite_returns=sat_streams)
        rows_by_variant[spec.variant_id] = rows
        m = metrics_from_rows(rows, start=start, end=end)
        results[spec.variant_id] = {
            "kind": spec.kind,
            "metrics": {k: v for k, v in m.items() if k != "month_returns"},
            "years": year_slices(rows),
            "rolling_3m": rolling_3m(rows),
        }

    spy_m = results["BENCHMARK_SPY_BUY_HOLD"]["metrics"]
    qqq_m = results["BENCHMARK_QQQ_BUY_HOLD"]["metrics"]

    # Same-exposure comparisons for throttled variants: scale index daily
    # returns by the variant's own exposure path.
    for vid in CORE_VARIANT_IDS | (SATELLITE_VARIANT_IDS if not skip_satellites else set()):
        rows = rows_by_variant.get(vid)
        if not rows:
            continue
        same = {}
        for sym in ("SPY", "QQQ"):
            eq = 1.0
            srows = []
            for r in rows:
                dr = float(returns[sym].get(pd.Timestamp(r["date"]), 0.0)) * float(r["gross_exposure"])
                eq *= 1.0 + dr
                srows.append({"date": r["date"], "daily_return": dr, "equity": eq, "gross_exposure": r["gross_exposure"]})
            sm = metrics_from_rows(srows, start=start, end=end)
            same[sym] = {k: sm.get(k) for k in ("total_return", "cagr", "max_drawdown", "sharpe", "calmar")}
        results[vid]["same_exposure"] = same

    # Gates for core variants; satellite assessment vs their core.
    for vid in CORE_VARIANT_IDS:
        results[vid]["gates"] = core_gates(results[vid]["metrics"], results[vid]["years"], qqq_m, spy_m)
    core_m = results["REGIME_THROTTLED_QQQ"]["metrics"]
    if not skip_satellites:
        for vid in SATELLITE_VARIANT_IDS:
            results[vid]["satellite_assessment"] = satellite_assessment(results[vid]["metrics"], core_m)

    candidates = [vid for vid in CORE_VARIANT_IDS if results[vid]["gates"]["all_passed"]]
    for vid, row in results.items():
        if vid.startswith("BENCHMARK"):
            row["verdict"] = BENCHMARK
        elif vid in CORE_VARIANT_IDS:
            row["verdict"] = CORE_CANDIDATE if vid in candidates else NOT_READY
        elif vid in SATELLITE_VARIANT_IDS:
            row["verdict"] = row.get("satellite_assessment", {}).get("verdict", OBSERVATION_ONLY)
        else:
            row["verdict"] = BENCHMARK if vid.startswith("STATIC") else NOT_READY

    if candidates:
        answer = "YES"
    elif any(
        not results[vid]["gates"]["all_passed"]
        and sum(1 for g in results[vid]["gates"].values() if isinstance(g, dict) and g["passed"]) >= 6
        for vid in CORE_VARIANT_IDS
    ):
        answer = "MAYBE"
    else:
        answer = "NO"

    regime_days = {}
    for row in regimes.values():
        regime_days[row["label"]] = regime_days.get(row["label"], 0) + 1

    return {
        "kind": "core_satellite_portfolio",
        "version": VERSION,
        "generated_at": _utc_now(),
        "research_only": True,
        "status": "RESEARCH_ONLY",
        "top_line": f"DID WE FIND A CORE PORTFOLIO ENGINE? {answer}",
        "answer": answer,
        "verdict": CORE_CANDIDATE if candidates else NO_CORE_READY,
        "candidates": candidates,
        "signal_window": {"start": start, "end": end, "trading_days": len(dates)},
        "regime_day_counts": regime_days,
        "exposure_ladder": {k or "unknown": v for k, v in REGIME_EXPOSURE.items()},
        "pre_registered_gates": {
            "maxdd_ratio_vs_qqq": GATE_MAXDD_RATIO_VS_QQQ,
            "or_return_beats_qqq": GATE_RETURN_MUST_BEAT_QQQ,
            "max_month_share": GATE_MAX_MONTH_SHARE,
            "max_change_days_pct": GATE_MAX_CHANGE_DAYS_PCT,
            "max_turnover_per_year": GATE_MAX_TURNOVER_PER_YEAR,
        },
        "cash_note": "cash earns 0% (no T-bill history retained); throttled/static returns are understated by roughly the cash yield",
        "results": results,
        "paper_shadow": {
            "proposal_created": bool(candidates),
            "proposal_doc": str(PROPOSAL_DOC) if candidates else None,
            "status": CORE_CANDIDATE if candidates else NO_CORE_READY,
            "note": "Proposal doc only; nothing is activated.",
        },
        "safety": lab.safety_confirmations(),
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_text(res: Dict[str, Any]) -> List[str]:
    lines = [
        f"CORE-SATELLITE PORTFOLIO (PHASE 2A) - {res['generated_at']}",
        lab.RESEARCH_DISCLAIMER,
        res["top_line"],
        f"verdict={res['verdict']} candidates={res['candidates']}",
        f"regime days: {res['regime_day_counts']}",
        "",
        f"{'variant':34s} {'ret':>8s} {'cagr':>8s} {'maxDD':>8s} {'sharpe':>7s} {'calmar':>7s} {'expo':>6s} {'turn/y':>7s} {'verdict':>22s}",
    ]
    for vid, row in res["results"].items():
        m = row["metrics"]
        lines.append(
            f"{vid:34s} {_pct(m.get('total_return')):>8s} {_pct(m.get('cagr')):>8s} {_pct(m.get('max_drawdown')):>8s} "
            f"{str(m.get('sharpe')):>7s} {str(m.get('calmar')):>7s} {m.get('exposure_pct'):>6.2f} "
            f"{m.get('turnover_per_year'):>7.2f} {row['verdict']:>22s}"
        )
    for vid in sorted(res["candidates"] or []):
        lines.append(f"CANDIDATE: {vid}")
    for vid in CORE_VARIANT_IDS:
        gates = res["results"][vid]["gates"]
        failed = [k for k, v in gates.items() if isinstance(v, dict) and not v["passed"]]
        lines.append(f"{vid} failed gates: {failed or 'NONE'}")
    return lines


def render_doc(res: Dict[str, Any]) -> str:
    lines = [
        "# Core-Satellite Portfolio Results (Phase 2A)",
        "",
        f"Generated: {res['generated_at']}",
        "",
        lab.RESEARCH_DISCLAIMER,
        "",
        f"## {res['top_line']}",
        "",
        f"Verdict: **{res['verdict']}**. Window `{res['signal_window']['start']}`..`{res['signal_window']['end']}` "
        f"({res['signal_window']['trading_days']} trading days). Regime-day counts: `{res['regime_day_counts']}`.",
        "",
        res["cash_note"] + ".",
        "",
        "## Variant Table",
        "",
        "| Variant | Return | CAGR | MaxDD | Sharpe | Sortino | Calmar | Vol | Hit | Worst M | Exposure | TimeInMkt | Turn/yr | R/Exp | Verdict |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for vid, row in res["results"].items():
        m = row["metrics"]
        worst = (m.get("worst_month") or {})
        lines.append(
            f"| {vid} | {_pct(m.get('total_return'))} | {_pct(m.get('cagr'))} | {_pct(m.get('max_drawdown'))} | "
            f"{m.get('sharpe')} | {m.get('sortino')} | {m.get('calmar')} | {_pct(m.get('volatility_annualized'))} | "
            f"{m.get('monthly_hit_rate')} | {worst.get('month')} {_pct(worst.get('return'))} | {_pct(m.get('exposure_pct'))} | "
            f"{m.get('time_in_market_pct')} | {m.get('turnover_per_year')} | {_pct(m.get('return_per_unit_exposure'))} | {row['verdict']} |"
        )
    lines += ["", "## Year Slices", "", "| Variant | 2024 | 2025 | 2026 YTD | 2026 maxDD | Rolling-3m %pos |", "|---|---:|---:|---:|---:|---:|"]
    for vid, row in res["results"].items():
        y = row["years"]
        lines.append(
            f"| {vid} | {_pct((y.get('2024') or {}).get('total_return'))} | {_pct((y.get('2025') or {}).get('total_return'))} | "
            f"{_pct((y.get('2026') or {}).get('total_return'))} | {_pct((y.get('2026') or {}).get('max_drawdown'))} | "
            f"{row['rolling_3m'].get('pct_positive')} |"
        )
    lines += ["", "## Core Gates", ""]
    for vid in sorted(CORE_VARIANT_IDS):
        lines += [f"### {vid}", ""]
        for name, g in res["results"][vid]["gates"].items():
            if isinstance(g, dict):
                lines.append(f"- [{'PASS' if g['passed'] else 'FAIL'}] {name}: {g['detail']}")
        same = res["results"][vid].get("same_exposure") or {}
        for sym, sm in same.items():
            lines.append(f"- same-exposure {sym}: return {_pct(sm.get('total_return'))}, sharpe {sm.get('sharpe')}, calmar {sm.get('calmar')}")
        lines.append("")
    lines += ["## Satellite Value-Add", ""]
    for vid in sorted(SATELLITE_VARIANT_IDS):
        row = res["results"].get(vid)
        if not row or "satellite_assessment" not in row:
            continue
        sa = row["satellite_assessment"]
        lines.append(
            f"- **{vid}**: {sa['verdict']} — dCAGR {_pct(sa['delta_cagr'])}, dSharpe {sa['delta_sharpe']}, "
            f"dMaxDD {_pct(sa['delta_max_drawdown'])} vs REGIME_THROTTLED_QQQ. {sa['detail']}."
        )
    lines += [
        "",
        "## Safety",
        "",
        "No paper signals, broker orders, trade proposals (doc-only), production thresholds, Gatekeeper/Veto logic,",
        "execution/governance/live-capital modules, historical evidence, or SHORT_A frozen status were changed.",
        "Regime labels are strictly as-of; exposure uses the prior close's label (test-pinned).",
        "",
    ]
    return "\n".join(lines)


def render_scenarios_doc() -> str:
    """Task 6 — planning math only; explicitly NOT strategy evidence."""
    lines = [
        "# Portfolio Compounding Scenarios (Phase 2A — planning only)",
        "",
        "**THIS IS NOT STRATEGY EVIDENCE.** The table below is arithmetic on ASSUMED return",
        "rates and contribution schedules. No backtest supports any specific rate here; see",
        "CORE_SATELLITE_PORTFOLIO_RESULTS.md for what has actually been measured historically.",
        "",
        "Start: $100,000. Monthly contributions invested at month-end. Values rounded to $1k.",
        "",
        "## Years to reach $1,000,000",
        "",
        "| Assumed CAGR | $0/mo | $1,000/mo | $2,000/mo | $3,000/mo |",
        "|---:|---:|---:|---:|---:|",
    ]
    for rate in (0.10, 0.15, 0.20, 0.25):
        cells = []
        for contrib in (0, 1000, 2000, 3000):
            balance = 100_000.0
            months = 0
            r_m = (1 + rate) ** (1 / 12) - 1
            while balance < 1_000_000 and months < 12 * 60:
                balance = balance * (1 + r_m) + contrib
                months += 1
            cells.append(f"{months / 12:.1f}y" if balance >= 1_000_000 else ">60y")
        lines.append(f"| {rate:.0%} | {cells[0]} | {cells[1]} | {cells[2]} | {cells[3]} |")
    lines += [
        "",
        "## 10-year balance",
        "",
        "| Assumed CAGR | $0/mo | $1,000/mo | $2,000/mo | $3,000/mo |",
        "|---:|---:|---:|---:|---:|",
    ]
    for rate in (0.10, 0.15, 0.20, 0.25):
        cells = []
        for contrib in (0, 1000, 2000, 3000):
            balance = 100_000.0
            r_m = (1 + rate) ** (1 / 12) - 1
            for _ in range(120):
                balance = balance * (1 + r_m) + contrib
            cells.append(f"${balance / 1000:,.0f}k")
        lines.append(f"| {rate:.0%} | {cells[0]} | {cells[1]} | {cells[2]} | {cells[3]} |")
    lines += [
        "",
        "Reading guide: contributions dominate outcomes at this account size. At 15% CAGR,",
        "$3k/mo reaches $1M roughly six years sooner than $0/mo. Sustained 25%+ CAGR has no",
        "evidential support in this repository and should not be planned on.",
        "",
    ]
    return "\n".join(lines)


def render_proposal_doc(res: Dict[str, Any]) -> str:
    vid = sorted(res["candidates"])[0]
    row = res["results"][vid]
    m = row["metrics"]
    return "\n".join([
        "# Core-Satellite Paper Proposal (Phase 2A)",
        "",
        "Manual review required. This document does not activate anything — paper-only plan.",
        "",
        f"## Candidate: {vid}",
        "",
        "### Allocation rules",
        "",
        "- Exposure ladder (fixed a priori, see CORE_SATELLITE_REGIME_SPEC.md):",
        *[f"  - {k}: {v:.0%}" for k, v in res["exposure_ladder"].items()],
        "- Decision once per close from the as-of regime label; applied next session.",
        "- Satellites stay observation-only unless SATELLITE_VALUE_ADD was earned.",
        "",
        "### Evidence",
        "",
        f"- Full span: {_pct(m.get('total_return'))} (CAGR {_pct(m.get('cagr'))}), maxDD {_pct(m.get('max_drawdown'))}, "
        f"Sharpe {m.get('sharpe')}, Calmar {m.get('calmar')}",
        f"- 2026 YTD: {_pct((row['years'].get('2026') or {}).get('total_return'))}",
        f"- Exposure {_pct(m.get('exposure_pct'))}, turnover/yr {m.get('turnover_per_year')}",
        "",
        "### Risk controls (paper)",
        "",
        "- Hard drawdown limit: de-risk to RISK_OFF ladder if paper equity drawdown exceeds -12%.",
        "- Rebalance cadence: daily check, trade only on ladder change (no intraday).",
        "- Kill criteria: 6-month paper Sharpe < same-exposure QQQ, or churn > pre-registered caps,",
        "  or any lookahead defect found.",
        "",
        "### Why not live",
        "",
        "- Paper validation must run through the existing holdout window (closes 2026-12-01);",
        "  the live-capital gate (three env keys) remains untouched.",
        "",
    ])


def write_outputs(res: Dict[str, Any]) -> None:
    for p in (OUT_JSON, OUT_TXT, OUT_DOC, SCENARIOS_DOC):
        p.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    OUT_TXT.write_text("\n".join(render_text(res)) + "\n", encoding="utf-8")
    OUT_DOC.write_text(render_doc(res) + "\n", encoding="utf-8")
    SCENARIOS_DOC.write_text(render_scenarios_doc() + "\n", encoding="utf-8")
    if res["paper_shadow"]["proposal_created"]:
        PROPOSAL_DOC.write_text(render_proposal_doc(res) + "\n", encoding="utf-8")


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Phase 2A core-satellite portfolio engine (research-only)")
    ap.add_argument("--start", default="2024-01-02")
    ap.add_argument("--end", default="2026-06-11")
    ap.add_argument("--skip-satellites", action="store_true")
    args = ap.parse_args(argv)
    res = build_report(start=args.start, end=args.end, skip_satellites=args.skip_satellites)
    write_outputs(res)
    print("\n".join(render_text(res)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

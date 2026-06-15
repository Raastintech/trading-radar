#!/usr/bin/env python3
"""
research/core_satellite_leveraged.py - Phase 2A.1 leveraged variant test.

Tests whether adding 1.25x or 1.5x leverage to the Phase 2A CORE_ENGINE_CANDIDATE
variants (REGIME_THROTTLED_QQQ, REGIME_THROTTLED_BLEND) improves risk-adjusted
outcomes after explicit margin/borrowing costs, while keeping maxDD at or below
QQQ's maxDD of -22.77%.

Pre-registered gates (fixed a priori — not tuned on results):
  1. Beats QQQ CAGR after all costs (gate: >27.34% per Phase 2A measured value)
  2. maxDD >= QQQ maxDD (-22.77%) — absolute floor; not just "better than 1.0x base"
  3. Sharpe > QQQ Sharpe (1.2738) AND Calmar > QQQ Calmar (1.2004)
  4. Not one-year dependent (positive months >= 2, best-month share <= 65%)
  5. All year slices positive (2024, 2025, 2026 YTD)
  6. No lookahead — weight from prior-close regime by construction (test-pinned)

Margin/borrow cost model (stated assumption, not tuned):
  daily_net_return = leverage × r_core - max(0, leverage-1) × BORROW_RATE_ANNUAL / 252
  Borrow cost accrues only on the leveraged portion above 1.0x exposure.
  Fixed annualized rate: 6.5% (Fed funds avg 2024-2026 ≈ 5% + 1.5% broker spread).

Gap risk stress: empirical QQQ daily-return distribution × leverage multiplier,
  plus Monte Carlo 1-week simulation (seed=42 for reproducibility).

Tax estimate: rough approximation; clearly labeled NOT financial advice.

Research-only. No paper signals, broker orders, trade proposals, production
changes, governance, execution, or historical evidence modifications.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from research import core_satellite_portfolio as cs  # noqa: E402
from research import strategy_lab_regime as regime  # noqa: E402
from research import strategy_research_lab as lab  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "cache" / "research"
LOGS = ROOT / "logs"
DOCS = ROOT / "docs" / "research"

OUT_JSON = CACHE / "core_satellite_leveraged_latest.json"
OUT_TXT = LOGS / "core_satellite_leveraged_latest.txt"
OUT_DOC = DOCS / "CORE_SATELLITE_LEVERAGED_RESULTS.md"

VERSION = "CORE_SATELLITE_LEVERAGED_V1"

# ---------------------------------------------------------------------------
# Pre-registered benchmark constants (Phase 2A measured values, 2024-01-02..2026-06-11)
# Fixed before any leveraged run — gates may NOT be adjusted on results.
# ---------------------------------------------------------------------------

QQQ_CAGR_BENCHMARK: float = 0.2734       # +27.34% CAGR
QQQ_MAXDD_BENCHMARK: float = -0.2277     # -22.77% maxDD (absolute floor)
QQQ_SHARPE_BENCHMARK: float = 1.2738
QQQ_CALMAR_BENCHMARK: float = 1.2004

# Margin/borrow cost: Fed funds avg 2024-2026 ≈ 5.0% + 1.5% broker spread = 6.5%.
# Fixed-rate approximation; actual rate floats with EFFR + broker terms.
# 6.5% is moderately conservative for the 2024-2026 window.
BORROW_RATE_ANNUAL: float = 0.065

# Gap risk: empirical quantile levels applied to QQQ daily-return distribution.
GAP_RISK_QUANTILES: Tuple[float, ...] = (0.05, 0.02, 0.01, 0.005, 0.001)

# Tax estimate assumptions (pre-registered; NOT advice):
# Daily-rebalanced regime throttling triggers ETF switches at each ladder change
# (~19.5 changes/yr per Phase 2A). Assuming short-term rates for all realized gains.
STCG_RATE: float = 0.35   # marginal STCG (federal + state approximation)
LTCG_RATE: float = 0.20   # long-term rate (informational; not applied at 1.0x variant)

# Gate thresholds (inherited from Phase 2A, not re-tuned):
GATE_MAX_MONTH_SHARE: float = 0.65
GATE_MIN_POSITIVE_MONTHS: int = 2

# Leverage multipliers to test:
LEVERAGE_LEVELS: Tuple[float, ...] = (1.0, 1.25, 1.5)

# Verdicts:
LEVERED_CANDIDATE = "LEVERED_CANDIDATE"
LEVERED_REJECT = "LEVERED_REJECT"
BENCHMARK = "BENCHMARK"

_f = lab._f


def _pct(v: Any) -> str:
    return "n/a" if v is None else f"{float(v) * 100:+.2f}%"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Variant definitions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LeveredSpec:
    variant_id: str
    base_kind: str    # buy_hold | throttled | throttled_blend
    asset: str        # primary risk asset (QQQ unless throttled_blend overrides)
    leverage: float
    borrow_rate_annual: float = BORROW_RATE_ANNUAL


def _build_variants() -> Tuple[LeveredSpec, ...]:
    vs: List[LeveredSpec] = [
        LeveredSpec("BENCHMARK_QQQ_BUY_HOLD", "buy_hold", "QQQ", 1.0),
        LeveredSpec("BENCHMARK_SPY_BUY_HOLD", "buy_hold", "SPY", 1.0),
    ]
    for lev in LEVERAGE_LEVELS:
        tag = f"{lev:.2f}x".replace(".", "_")
        vs.append(LeveredSpec(f"REGIME_THROTTLED_QQQ_{tag}", "throttled", "QQQ", lev))
        vs.append(LeveredSpec(f"REGIME_THROTTLED_BLEND_{tag}", "throttled_blend", "QQQ", lev))
    return tuple(vs)


VARIANTS: Tuple[LeveredSpec, ...] = _build_variants()

# IDs that receive gate evaluation:
_THROTTLED_IDS = frozenset(
    s.variant_id for s in VARIANTS if s.base_kind in ("throttled", "throttled_blend")
)


# ---------------------------------------------------------------------------
# Daily simulation (no-lookahead: weight from prior-close regime)
# ---------------------------------------------------------------------------

def simulate_levered(
    spec: LeveredSpec,
    *,
    dates: Sequence[str],
    returns: Dict[str, pd.Series],
    regimes: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Returns daily rows: date, daily_return (net), equity, gross_exposure, borrow_cost_daily.

    Borrow cost formula:
      borrowed_fraction = max(0, core_w - 1.0)   # only above 1.0x equity exposure
      daily_borrow = borrowed_fraction × borrow_rate_annual / 252

    For regime-throttled variants: core_w = target_exposure(prior_label) × leverage.
    Day 0 is cash (exposure 0). No lookahead: weight from dates[i-1] regime.
    For buy-hold 1.0x: core_w = 1.0, no borrowing needed.
    """
    equity = 1.0
    rows: List[Dict[str, Any]] = []
    for i, day in enumerate(dates):
        if spec.base_kind == "buy_hold":
            core_w = spec.leverage  # at 1.0x: fully invested, no borrow
            asset = spec.asset
        else:
            prior = regimes.get(dates[i - 1]) if i > 0 else None
            if prior is None:
                core_w, asset = 0.0, spec.asset
            else:
                base_expo = cs.target_exposure(prior.get("label"))
                core_w = base_expo * spec.leverage
                asset = (
                    cs.blend_asset(prior.get("qqq_vs_spy_20"))
                    if spec.base_kind == "throttled_blend"
                    else spec.asset
                )

        # Borrow cost applies only to the portion above 1.0x equity exposure.
        borrowed_fraction = max(0.0, core_w - 1.0)
        daily_borrow = borrowed_fraction * spec.borrow_rate_annual / 252

        r_core = float(returns[asset].get(pd.Timestamp(day), 0.0))
        daily_net = core_w * r_core - daily_borrow
        equity *= 1.0 + daily_net

        rows.append({
            "date": day,
            "daily_return": round(daily_net, 8),
            "equity": round(equity, 8),
            "gross_exposure": round(core_w, 6),
            "borrow_cost_daily": round(daily_borrow, 8),
            "asset": asset,
        })
    return rows


# ---------------------------------------------------------------------------
# Gap risk stress analysis
# ---------------------------------------------------------------------------

def gap_risk_analysis(
    qqq_daily_returns: List[float],
    leverage_levels: Tuple[float, ...] = LEVERAGE_LEVELS,
    borrow_rate_annual: float = BORROW_RATE_ANNUAL,
    *,
    n_mc_sims: int = 10_000,
    mc_week_len: int = 5,
    rng_seed: int = 42,
) -> Dict[str, Any]:
    """
    Gap/tail-risk analysis using empirical QQQ daily-return distribution.

    Produces:
      1. quantile_table: for each percentile Q, the underlying return and
         the resulting leveraged portfolio loss at each leverage level.
      2. monte_carlo_1week: bootstrap 1-week simulations (seed=42);
         shows p5/p1 worst weeks at each leverage level.
      3. worst_historical_5d: actual worst consecutive 5-day outcome.
    """
    if not qqq_daily_returns:
        return {"error": "no_data"}
    n = len(qqq_daily_returns)
    sorted_r = sorted(qqq_daily_returns)

    # 1. Quantile table
    quantile_table: Dict[str, Any] = {}
    for q in GAP_RISK_QUANTILES:
        idx = max(0, int(q * n) - 1)
        underlying = sorted_r[idx]
        by_lev: Dict[str, float] = {}
        for lev in leverage_levels:
            # Single-day leveraged loss (borrow cost negligible for a single day shock):
            by_lev[f"{lev:.2f}x"] = round(lev * underlying, 6)
        quantile_table[f"p{q * 100:.1f}"] = {
            "underlying_return": round(underlying, 6),
            "leveraged_loss": by_lev,
        }

    # 2. Monte Carlo 1-week bootstrap (fixed seed for reproducibility)
    rng = random.Random(rng_seed)
    sim_by_lev: Dict[float, List[float]] = {lev: [] for lev in leverage_levels}
    for _ in range(n_mc_sims):
        week = [rng.choice(qqq_daily_returns) for _ in range(mc_week_len)]
        for lev in leverage_levels:
            eq = 1.0
            for r in week:
                borrowed = max(0.0, lev - 1.0)
                eq *= 1.0 + lev * r - borrowed * borrow_rate_annual / 252
            sim_by_lev[lev].append(eq - 1.0)

    monte_carlo: Dict[str, Any] = {}
    for lev in leverage_levels:
        sims = sorted(sim_by_lev[lev])
        monte_carlo[f"{lev:.2f}x"] = {
            "p5_1week": round(sims[int(0.05 * n_mc_sims)], 6),
            "p1_1week": round(sims[int(0.01 * n_mc_sims)], 6),
            "median_1week": round(statistics.median(sims), 6),
        }

    # 3. Worst historical 5-day consecutive window
    worst_5d: Dict[str, float] = {}
    for lev in leverage_levels:
        worst = 0.0
        for start_i in range(max(1, len(qqq_daily_returns) - mc_week_len + 1)):
            eq = 1.0
            for r in qqq_daily_returns[start_i: start_i + mc_week_len]:
                borrowed = max(0.0, lev - 1.0)
                eq *= 1.0 + lev * r - borrowed * borrow_rate_annual / 252
            worst = min(worst, eq - 1.0)
        worst_5d[f"{lev:.2f}x"] = round(worst, 6)

    return {
        "quantile_table": quantile_table,
        "note": "Borrow cost on single-day shock is negligible (<0.001%); omitted from quantile table.",
        "monte_carlo_1week": {
            "n_simulations": n_mc_sims,
            "rng_seed": rng_seed,
            "results": monte_carlo,
        },
        "worst_historical_5d": worst_5d,
        "n_history_days": n,
    }


# ---------------------------------------------------------------------------
# Tax estimate (rough approximation — NOT financial advice)
# ---------------------------------------------------------------------------

def tax_estimate(cagr: float, turnover_per_year: float) -> Dict[str, Any]:
    """
    Rough after-tax CAGR approximation. Clearly labeled as estimate only.

    Assumptions (pre-registered):
    - Regime-throttled ETF switches at each ladder change (~19.5 events/yr per Phase 2A).
    - All ladder changes are taxable events on position changes.
    - Worst-case: 100% of annual gains realized as STCG (daily-rebalanced, < 1yr holds).
    - Mid-case: 50% of gains realized as STCG each year (partial position holds persist).
    - Tax-deferred accounts (IRA, 401k, Roth) avoid this entirely.
    """
    worst_after_tax = cagr * (1.0 - STCG_RATE)
    mid_after_tax = cagr * (1.0 - 0.5 * STCG_RATE)
    return {
        "pre_tax_cagr": round(cagr, 6),
        "worst_case_after_tax_cagr": round(worst_after_tax, 6),
        "mid_case_after_tax_cagr": round(mid_after_tax, 6),
        "stcg_rate_assumed": STCG_RATE,
        "ltcg_rate_assumed": LTCG_RATE,
        "turnover_events_per_year": round(turnover_per_year, 2),
        "note": (
            "Approximation only. Actual taxes depend on individual marginal rate, "
            "state taxes, tax-loss harvesting, and account type. "
            "IRA/401k/Roth: $0 tax impact. Taxable brokerage: worst-case applies. "
            "ETF rebalancing cost may also include small bid-ask spreads (not modeled here)."
        ),
    }


# ---------------------------------------------------------------------------
# Pre-registered gates for levered variants
# ---------------------------------------------------------------------------

def levered_gates(m: Dict[str, Any], years: Dict[str, Any]) -> Dict[str, Any]:
    """
    7 pre-registered gates for levered variants.
    All thresholds fixed before any run; not adjusted on results.
    """
    cagr = _f(m.get("cagr"))
    dd = _f(m.get("max_drawdown"), -1.0)
    sharpe = _f(m.get("sharpe"), -9.0)
    calmar = _f(m.get("calmar"), -9.0)
    pos_months = m.get("positive_months") or 0
    best_share = m.get("best_month_share_of_total")
    ytd = (years.get("2026") or {}).get("total_return")
    y2024 = (years.get("2024") or {}).get("total_return")
    y2025 = (years.get("2025") or {}).get("total_return")

    gates: Dict[str, Any] = {
        "no_lookahead": {
            "passed": True,
            "detail": "weight = prior-close regime × leverage, by construction (test-pinned)",
        },
        "cagr_beats_qqq_after_cost": {
            "passed": cagr is not None and cagr > QQQ_CAGR_BENCHMARK,
            "detail": f"variant CAGR {_pct(cagr)} vs gate {_pct(QQQ_CAGR_BENCHMARK)} (Phase 2A QQQ)",
        },
        "maxdd_lte_qqq_absolute": {
            "passed": dd >= QQQ_MAXDD_BENCHMARK,
            "detail": (
                f"maxDD {_pct(dd)} vs absolute floor {_pct(QQQ_MAXDD_BENCHMARK)} "
                f"(must not exceed QQQ; not just vs unlevered base)"
            ),
        },
        "sharpe_beats_qqq": {
            "passed": sharpe > QQQ_SHARPE_BENCHMARK,
            "detail": f"Sharpe {sharpe} vs gate {QQQ_SHARPE_BENCHMARK}",
        },
        "calmar_beats_qqq": {
            "passed": calmar > QQQ_CALMAR_BENCHMARK,
            "detail": f"Calmar {calmar} vs gate {QQQ_CALMAR_BENCHMARK}",
        },
        "not_one_year_dependent": {
            "passed": (
                pos_months >= GATE_MIN_POSITIVE_MONTHS
                and (best_share is None or best_share <= GATE_MAX_MONTH_SHARE)
            ),
            "detail": f"positive months {pos_months}/{m.get('month_count')}, best share {best_share}",
        },
        "all_years_positive": {
            "passed": bool(
                ytd is not None and ytd > 0
                and y2024 is not None and y2024 > 0
                and y2025 is not None and y2025 > 0
            ),
            "detail": f"2024 {_pct(y2024)}, 2025 {_pct(y2025)}, 2026 YTD {_pct(ytd)}",
        },
    }
    gates["all_passed"] = all(v["passed"] for k, v in gates.items() if isinstance(v, dict))
    return gates


# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------

def build_report(
    *,
    start: str = "2024-01-02",
    end: str = "2026-06-11",
    borrow_rate_annual: float = BORROW_RATE_ANNUAL,
) -> Dict[str, Any]:
    spy_ret = cs.load_daily_returns("SPY", start, end)
    qqq_ret = cs.load_daily_returns("QQQ", start, end)
    common = sorted(set(spy_ret.index) & set(qqq_ret.index))
    dates = [str(pd.Timestamp(d).date()) for d in common]
    returns = {"SPY": spy_ret, "QQQ": qqq_ret}
    regimes = cs.build_regime_series(common)

    # Precompute gap risk once from QQQ daily returns (shared across leverage levels).
    qqq_daily = [float(qqq_ret.get(pd.Timestamp(d), 0.0)) for d in dates]
    shared_gap_risk = gap_risk_analysis(qqq_daily, LEVERAGE_LEVELS, borrow_rate_annual)

    results: Dict[str, Any] = {}
    rows_by_variant: Dict[str, List[Dict[str, Any]]] = {}

    for spec in VARIANTS:
        rows = simulate_levered(spec, dates=dates, returns=returns, regimes=regimes)
        rows_by_variant[spec.variant_id] = rows
        m = cs.metrics_from_rows(rows, start=start, end=end)
        years = cs.year_slices(rows)
        r3m = cs.rolling_3m(rows)
        tax = tax_estimate(_f(m.get("cagr"), 0.0), _f(m.get("turnover_per_year"), 0.0))

        entry: Dict[str, Any] = {
            "kind": spec.base_kind,
            "leverage": spec.leverage,
            "borrow_rate_annual": borrow_rate_annual,
            "metrics": {k: v for k, v in m.items() if k != "month_returns"},
            "years": years,
            "rolling_3m": r3m,
            "tax_estimate": tax,
        }
        if spec.variant_id in _THROTTLED_IDS:
            entry["gates"] = levered_gates(m, years)

        results[spec.variant_id] = entry

    # Gap risk is attached at the top level (shared across all variants — same
    # underlying QQQ distribution, different leverage levels displayed together).
    results["__gap_risk_qqq__"] = shared_gap_risk

    candidates = [
        vid for vid in _THROTTLED_IDS
        if results[vid].get("gates", {}).get("all_passed") is True
    ]
    for vid, row in results.items():
        if vid.startswith("__"):
            continue
        spec = next((s for s in VARIANTS if s.variant_id == vid), None)
        if spec is None:
            continue
        if spec.base_kind == "buy_hold":
            row["verdict"] = BENCHMARK
        elif row.get("gates", {}).get("all_passed"):
            row["verdict"] = LEVERED_CANDIDATE
        else:
            row["verdict"] = LEVERED_REJECT

    return {
        "kind": "core_satellite_leveraged",
        "version": VERSION,
        "generated_at": _utc_now(),
        "research_only": True,
        "status": "RESEARCH_ONLY",
        "pre_registered_benchmarks": {
            "qqq_cagr": QQQ_CAGR_BENCHMARK,
            "qqq_maxdd": QQQ_MAXDD_BENCHMARK,
            "qqq_sharpe": QQQ_SHARPE_BENCHMARK,
            "qqq_calmar": QQQ_CALMAR_BENCHMARK,
            "source": "Phase 2A full run 2024-01-02..2026-06-11; fixed before this run",
        },
        "borrow_model": {
            "rate_annual": borrow_rate_annual,
            "formula": "daily_net = core_w*r_core - max(0, core_w-1.0)*rate/252",
            "note": (
                "Borrow cost accrues only on the leveraged portion above 1.0x exposure. "
                "At CHOP (60%) × 1.25x = 75% gross → no borrowing. "
                "At BULL (100%) × 1.25x = 125% gross → borrow 25% at rate."
            ),
        },
        "signal_window": {"start": start, "end": end, "trading_days": len(dates)},
        "candidates": candidates,
        "results": results,
        "safety": lab.safety_confirmations(),
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_text(res: Dict[str, Any]) -> List[str]:
    lines = [
        f"CORE-SATELLITE LEVERAGED (PHASE 2A.1) - {res['generated_at']}",
        lab.RESEARCH_DISCLAIMER,
        f"Borrow rate: {res['borrow_model']['rate_annual']:.1%}/yr  "
        f"(accrues only on exposure > 1.0x)",
        f"Pre-registered QQQ gates: CAGR>{_pct(res['pre_registered_benchmarks']['qqq_cagr'])}  "
        f"maxDD>={_pct(res['pre_registered_benchmarks']['qqq_maxdd'])}  "
        f"Sharpe>{res['pre_registered_benchmarks']['qqq_sharpe']}  "
        f"Calmar>{res['pre_registered_benchmarks']['qqq_calmar']}",
        "",
        f"{'variant':42s} {'lev':>5s} {'cagr':>8s} {'maxDD':>8s} {'sharpe':>7s} "
        f"{'calmar':>7s} {'wst-mo':>7s} {'turn/y':>7s} {'verdict':>18s}",
    ]

    for spec in VARIANTS:
        vid = spec.variant_id
        row = res["results"].get(vid)
        if not row:
            continue
        m = row["metrics"]
        worst_m = (m.get("worst_month") or {}).get("return")
        lines.append(
            f"{vid:42s} {spec.leverage:>5.2f} {_pct(m.get('cagr')):>8s} "
            f"{_pct(m.get('max_drawdown')):>8s} {str(m.get('sharpe')):>7s} "
            f"{str(m.get('calmar')):>7s} {_pct(worst_m):>7s} "
            f"{m.get('turnover_per_year', 0.0):>7.2f} {row.get('verdict', ''):>18s}"
        )

    lines += ["", "--- Gate Summary ---"]
    for spec in VARIANTS:
        if spec.base_kind == "buy_hold":
            continue
        vid = spec.variant_id
        row = res["results"].get(vid, {})
        gates = row.get("gates", {})
        failed = [k for k, v in gates.items() if isinstance(v, dict) and not v["passed"]]
        lines.append(f"  {vid}: {'PASS ALL' if not failed else 'FAIL ' + str(failed)}")

    lines += ["", "--- Gap Risk (QQQ underlying) ---"]
    gr = res["results"].get("__gap_risk_qqq__", {})
    qt = gr.get("quantile_table", {})
    if qt:
        header = f"{'Quantile':>12s} {'QQQ%':>8s}" + "".join(
            f" {f'{lev:.2f}x':>8s}" for lev in LEVERAGE_LEVELS
        )
        lines.append(header)
        for qname, qdata in qt.items():
            row_str = f"{qname:>12s} {_pct(qdata['underlying_return']):>8s}" + "".join(
                f" {_pct(qdata['leveraged_loss'].get(f'{lev:.2f}x')):>8s}"
                for lev in LEVERAGE_LEVELS
            )
            lines.append(row_str)
    mc = gr.get("monte_carlo_1week", {}).get("results", {})
    if mc:
        lines.append("Monte Carlo 1-week (10k sims, seed=42):")
        lines.append(f"  {'':12s} {'p5':>8s} {'p1':>8s} {'median':>8s}")
        for lev in LEVERAGE_LEVELS:
            tag = f"{lev:.2f}x"
            d = mc.get(tag, {})
            lines.append(
                f"  {tag:12s} {_pct(d.get('p5_1week')):>8s} "
                f"{_pct(d.get('p1_1week')):>8s} {_pct(d.get('median_1week')):>8s}"
            )

    lines += ["", "--- Tax Estimate (STCG 35% worst / mid-case 17.5%) ---"]
    lines.append(f"{'variant':42s} {'pre-tax':>9s} {'worst-tax':>10s} {'mid-tax':>9s}")
    for spec in VARIANTS:
        vid = spec.variant_id
        row = res["results"].get(vid)
        if not row:
            continue
        tax = row.get("tax_estimate", {})
        lines.append(
            f"{vid:42s} {_pct(tax.get('pre_tax_cagr')):>9s} "
            f"{_pct(tax.get('worst_case_after_tax_cagr')):>10s} "
            f"{_pct(tax.get('mid_case_after_tax_cagr')):>9s}"
        )
    lines.append("NOTE: Tax-deferred accounts (IRA/Roth/401k) avoid all tax drag.")

    lines += ["", "--- Year Slices + Rolling 3m ---"]
    lines.append(f"{'variant':42s} {'lev':>5s} {'2024':>8s} {'2025':>8s} {'YTD26':>8s} {'R3m%+':>7s}")
    for spec in VARIANTS:
        vid = spec.variant_id
        row = res["results"].get(vid)
        if not row:
            continue
        y = row["years"]
        r3 = row.get("rolling_3m", {})
        lines.append(
            f"{vid:42s} {spec.leverage:>5.2f} "
            f"{_pct((y.get('2024') or {}).get('total_return')):>8s} "
            f"{_pct((y.get('2025') or {}).get('total_return')):>8s} "
            f"{_pct((y.get('2026') or {}).get('total_return')):>8s} "
            f"{str(r3.get('pct_positive', 'n/a')):>7s}"
        )

    lines += ["", f"Candidates: {res['candidates'] or 'NONE'}"]
    return lines


def render_doc(res: Dict[str, Any]) -> str:
    lines = [
        "# Core-Satellite Leveraged Results (Phase 2A.1)",
        "",
        f"Generated: {res['generated_at']}",
        "",
        lab.RESEARCH_DISCLAIMER,
        "",
        "## Setup",
        "",
        f"- Window: `{res['signal_window']['start']}`..`{res['signal_window']['end']}` "
        f"({res['signal_window']['trading_days']} trading days).",
        f"- Borrow rate: **{res['borrow_model']['rate_annual']:.1%}/yr** "
        f"(Fed funds avg 2024-2026 ≈5% + 1.5% broker spread; fixed assumption).",
        f"- Formula: `{res['borrow_model']['formula']}`",
        f"- {res['borrow_model']['note']}",
        "",
        "## Pre-Registered Gates (fixed before run)",
        "",
        f"| Gate | Threshold |",
        "|---|---|",
        f"| CAGR after cost | >{_pct(res['pre_registered_benchmarks']['qqq_cagr'])} (QQQ Phase 2A) |",
        f"| maxDD absolute floor | >={_pct(res['pre_registered_benchmarks']['qqq_maxdd'])} (QQQ; not just vs 1.0x base) |",
        f"| Sharpe | >{res['pre_registered_benchmarks']['qqq_sharpe']} (QQQ) |",
        f"| Calmar | >{res['pre_registered_benchmarks']['qqq_calmar']} (QQQ) |",
        f"| Year-dependent | positive months ≥{GATE_MIN_POSITIVE_MONTHS}, best share ≤{GATE_MAX_MONTH_SHARE:.0%} |",
        f"| All years positive | 2024 + 2025 + 2026 YTD all positive |",
        f"| No lookahead | prior-close regime only (test-pinned) |",
        "",
        "## Results Table",
        "",
        "| Variant | Lev | CAGR | maxDD | Sharpe | Calmar | Worst M | Turn/yr | Verdict |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for spec in VARIANTS:
        vid = spec.variant_id
        row = res["results"].get(vid)
        if not row:
            continue
        m = row["metrics"]
        worst_m = (m.get("worst_month") or {}).get("return")
        lines.append(
            f"| {vid} | {spec.leverage:.2f}x | {_pct(m.get('cagr'))} | "
            f"{_pct(m.get('max_drawdown'))} | {m.get('sharpe')} | {m.get('calmar')} | "
            f"{_pct(worst_m)} | {m.get('turnover_per_year')} | {row.get('verdict', '')} |"
        )

    lines += ["", "## Year Slices", "",
              "| Variant | Lev | 2024 | 2025 | 2026 YTD | 2026 maxDD | Roll-3m %+ |",
              "|---|---:|---:|---:|---:|---:|---:|"]
    for spec in VARIANTS:
        vid = spec.variant_id
        row = res["results"].get(vid)
        if not row:
            continue
        y = row["years"]
        r3 = row.get("rolling_3m", {})
        lines.append(
            f"| {vid} | {spec.leverage:.2f}x | "
            f"{_pct((y.get('2024') or {}).get('total_return'))} | "
            f"{_pct((y.get('2025') or {}).get('total_return'))} | "
            f"{_pct((y.get('2026') or {}).get('total_return'))} | "
            f"{_pct((y.get('2026') or {}).get('max_drawdown'))} | "
            f"{r3.get('pct_positive', 'n/a')} |"
        )

    lines += ["", "## Gap Risk (QQQ Underlying × Leverage)", "",
              "Tail events in empirical QQQ daily-return distribution:", ""]
    gr = res["results"].get("__gap_risk_qqq__", {})
    qt = gr.get("quantile_table", {})
    if qt:
        lev_headers = " | ".join(f"{lev:.2f}x" for lev in LEVERAGE_LEVELS)
        lines += [f"| Quantile | QQQ | {lev_headers} |",
                  "|---|" + "---:|" * (1 + len(LEVERAGE_LEVELS))]
        for qname, qdata in qt.items():
            lev_cells = " | ".join(
                _pct(qdata["leveraged_loss"].get(f"{lev:.2f}x")) for lev in LEVERAGE_LEVELS
            )
            lines.append(
                f"| {qname} | {_pct(qdata['underlying_return'])} | {lev_cells} |"
            )
    mc = gr.get("monte_carlo_1week", {})
    mc_res = mc.get("results", {})
    if mc_res:
        lines += ["", f"Monte Carlo 1-week (n={mc.get('n_simulations', 0)}, seed={mc.get('rng_seed')}):"]
        lines += ["| Leverage | p5 worst week | p1 worst week | median week |",
                  "|---|---:|---:|---:|"]
        for lev in LEVERAGE_LEVELS:
            tag = f"{lev:.2f}x"
            d = mc_res.get(tag, {})
            lines.append(
                f"| {tag} | {_pct(d.get('p5_1week'))} | "
                f"{_pct(d.get('p1_1week'))} | {_pct(d.get('median_1week'))} |"
            )
    w5 = gr.get("worst_historical_5d", {})
    if w5:
        lines += ["", "Worst historical consecutive 5-day outcome:"]
        lines += ["| Leverage | Worst 5-day |", "|---|---:|"]
        for lev in LEVERAGE_LEVELS:
            lines.append(f"| {lev:.2f}x | {_pct(w5.get(f'{lev:.2f}x'))} |")

    lines += ["", "## Tax Estimate (Approximation — NOT Financial Advice)", "",
              "Assumes daily-rebalanced ETF regime throttling → all realized gains short-term.",
              "IRA/Roth/401k: $0 tax impact. Taxable brokerage worst-case below.",
              "",
              "| Variant | Lev | Pre-tax CAGR | Worst-case (35% STCG) | Mid-case (17.5%) |",
              "|---|---:|---:|---:|---:|"]
    for spec in VARIANTS:
        vid = spec.variant_id
        row = res["results"].get(vid)
        if not row:
            continue
        tax = row.get("tax_estimate", {})
        lines.append(
            f"| {vid} | {spec.leverage:.2f}x | {_pct(tax.get('pre_tax_cagr'))} | "
            f"{_pct(tax.get('worst_case_after_tax_cagr'))} | "
            f"{_pct(tax.get('mid_case_after_tax_cagr'))} |"
        )

    lines += ["", "## Gate Details", ""]
    for spec in VARIANTS:
        if spec.base_kind == "buy_hold":
            continue
        vid = spec.variant_id
        row = res["results"].get(vid, {})
        gates = row.get("gates", {})
        lines += [f"### {vid}", ""]
        for gname, g in gates.items():
            if isinstance(g, dict):
                lines.append(f"- [{'PASS' if g['passed'] else 'FAIL'}] **{gname}**: {g['detail']}")
        lines.append("")

    lines += [
        "## Safety",
        "",
        "No paper signals, broker orders, trade proposals, production thresholds,",
        "Gatekeeper/Veto/execution/governance/live-capital changes, historical evidence",
        "mutations, or SHORT_A frozen-status changes. Regime labels strictly as-of;",
        "exposure uses prior-close label (test-pinned). Borrow-rate model is a fixed",
        "stated assumption, not a tuned parameter.",
        "",
        f"Candidates: **{res['candidates'] or 'NONE'}**",
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
    ap = argparse.ArgumentParser(
        description="Phase 2A.1 leveraged variant test (research-only)"
    )
    ap.add_argument("--start", default="2024-01-02")
    ap.add_argument("--end", default="2026-06-11")
    ap.add_argument("--borrow-rate", type=float, default=BORROW_RATE_ANNUAL,
                    help=f"Annualized margin borrow rate (default {BORROW_RATE_ANNUAL:.1%})")
    args = ap.parse_args(argv)
    res = build_report(start=args.start, end=args.end, borrow_rate_annual=args.borrow_rate)
    write_outputs(res)
    print("\n".join(render_text(res)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

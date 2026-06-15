#!/usr/bin/env python3
"""
research/lrr_clustered_portfolio.py - Phase 1I.2 entry clustering / sizing test.

Research-only test of pre-registered PORTFOLIO CONSTRUCTION rules on the
frozen LRR_REGIME_GATED signal stream. The signal rules, regime gate, exits,
and costs are untouched; only entry selection (day caps, ranking, cooldown,
duplicate-sector skip), open-position caps, and position sizing vary. This
targets the two Phase 1I.1 blockers: independent-trade drawdown (entry
clustering) and same-exposure Sharpe.

Writes cache/log/doc artifacts only; never imports broker, execution,
governance, paper-signal, or live-capital modules. A paper-shadow proposal
DOC is written only if a config passes every pre-registered gate; otherwise
the verdict is LRR_FAMILY_ARCHIVE_RECOMMENDED.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from research import leader_reset_reclaim_strategy as lrr_report  # noqa: E402
from research import strategy_failure_reason_miner as miner  # noqa: E402
from research import strategy_lab_correction_strategy as correction  # noqa: E402
from research import strategy_lab_portfolio as portfolio  # noqa: E402
from research import strategy_research_lab as lab  # noqa: E402
from research import strategy_walk_forward as wf  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "cache" / "research"
LOGS = ROOT / "logs"
DOCS = ROOT / "docs" / "research"

OUT_JSON = CACHE / "lrr_clustered_portfolio_latest.json"
OUT_TXT = LOGS / "lrr_clustered_portfolio_latest.txt"
OUT_DOC = DOCS / "LRR_CLUSTERED_PORTFOLIO_RESULTS.md"
PROPOSAL_DOC = DOCS / "PAPER_SHADOW_PROMOTION_PROPOSAL.md"

VERSION = "LRR_CLUSTERED_PORTFOLIO_V1"
VARIANT = "LRR_REGIME_GATED"

ARCHIVE_RECOMMENDED = "LRR_FAMILY_ARCHIVE_RECOMMENDED"
CANDIDATE = "PAPER_SHADOW_CANDIDATE"

# Pre-registered gate thresholds (fixed before the run).
MAX_REALISTIC_DD = -0.15
MIN_INDEPENDENT_DD = -0.35          # "materially improved" absolute bar
MIN_INDEPENDENT_DD_GAIN = 0.25      # and >= 25 points better than baseline
MIN_TEST_TRADES = 20
MAX_MONTH_SHARE = 0.65
MAX_TICKER_CONC = 0.35

# Vol-scaled sizing (rule 6): weight = clamp(TARGET_ATR / atr_pct, 0.25, 1.0).
SIZING_TARGET_ATR = 0.02
SIZING_MIN_WEIGHT = 0.25

COOLDOWN_DAYS = 3

_f = lab._f
_pct = miner._pct


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def composite_rank(trade: Dict[str, Any]) -> float:
    """Rule 10: rank by RS + reclaim strength + lower ATR risk (pre-registered)."""
    rs60 = _f(trade.get("rank_rs60"))
    vol_ratio = min(_f(trade.get("rank_volume_ratio"), 1.0), 3.0)
    stop = _f(trade.get("rank_stop_pct"), 0.10)
    return rs60 * 100.0 + vol_ratio * 10.0 - stop * 200.0


def vol_size_weight(trade: Dict[str, Any]) -> float:
    atr = _f(trade.get("rank_atr_pct"))
    if atr <= 0:
        return 1.0
    return min(1.0, max(SIZING_MIN_WEIGHT, SIZING_TARGET_ATR / atr))


@dataclass(frozen=True)
class ClusterConfig:
    config_id: str
    description: str
    max_entries_per_day: Optional[int] = None
    rank_by: str = "score"              # "score" | "composite"
    max_open_positions: int = 5
    max_sector_pct: float = 0.30
    vol_scaled_sizing: bool = False
    cooldown_after_loss: bool = False
    skip_duplicate_sector_open: bool = False
    skip_duplicate_theme_open: bool = False


CONFIGS: Sequence[ClusterConfig] = (
    ClusterConfig("BASELINE_GATED", "LRR_REGIME_GATED with default lab caps (reference)"),
    ClusterConfig("C1_MAX_1_PER_DAY", "max 1 entry per day (by lab score)", max_entries_per_day=1),
    ClusterConfig("C2_MAX_2_PER_DAY", "max 2 entries per day (by lab score)", max_entries_per_day=2),
    ClusterConfig("C3_TOP_RANKED_ONLY", "top composite-ranked signal only per day", max_entries_per_day=1, rank_by="composite"),
    ClusterConfig("C4_MAX_3_OPEN", "max 3 open positions", max_open_positions=3),
    ClusterConfig("C5_MAX_5_OPEN", "max 5 open positions (engine default, explicit)", max_open_positions=5),
    ClusterConfig("C6_VOL_SCALED", "volatility-scaled sizing (2% ATR target, floor 0.25x)", vol_scaled_sizing=True),
    ClusterConfig("C7_SECTOR_THEME_CAP", "sector cap 20% + skip duplicate open theme", max_sector_pct=0.20, skip_duplicate_theme_open=True),
    ClusterConfig("C8_COOLDOWN_3D_AFTER_LOSS", "3-trading-day entry cooldown after a losing exit", cooldown_after_loss=True),
    ClusterConfig("C9_SKIP_DUP_SECTOR_OPEN", "skip entry if same sector already open", skip_duplicate_sector_open=True),
    ClusterConfig(
        "C10_COMBO_DEFENSIVE",
        "pre-registered combo: 2/day composite-ranked + max 3 open + vol-scaled + cooldown + dup-sector skip",
        max_entries_per_day=2, rank_by="composite", max_open_positions=3,
        vol_scaled_sizing=True, cooldown_after_loss=True, skip_duplicate_sector_open=True,
    ),
)


def collect_gated_trades(
    start: str,
    end: str,
    *,
    params: lab.StrategyParams,
    config: lab.BacktestConfig,
) -> Dict[str, List[Dict[str, Any]]]:
    """Collect frozen LRR_REGIME_GATED + RANDOM_LIQUID trades, attaching the
    as-of ranking fields (rs60, volume ratio, stop, ATR) the engine drops."""
    out: Dict[str, List[Dict[str, Any]]] = {VARIANT: [], "RANDOM_LIQUID": []}
    for asof in lab.d.trading_dates_between(start, end):
        daily = lab.generate_signals_for_date(
            asof, variants=(VARIANT, "RANDOM_LIQUID"), params=params, config=config,
        )
        for variant, signals in daily.items():
            for sig in signals:
                trade = lab.simulate_trade(sig, params=params, cost_model=lab.BASE_COST, entry_timing=config.entry_timing)
                if trade is None:
                    continue
                f = sig.features
                trade["rank_rs60"] = max(_f(f.get("rs60_spy"), -9.0), _f(f.get("rs60_qqq"), -9.0))
                trade["rank_volume_ratio"] = _f(f.get("lrr_volume_ratio"), 1.0)
                trade["rank_stop_pct"] = _f(f.get("lrr_stop_loss_pct"), params.stop_loss_pct)
                trade["rank_atr_pct"] = _f(f.get("atr_pct"))
                trade["theme"] = f.get("theme")
                out[variant].append(trade)
    return out


def apply_entry_filters(
    trades: Sequence[Dict[str, Any]],
    cfg: ClusterConfig,
    trading_dates: Sequence[str],
) -> List[Dict[str, Any]]:
    """Sequential, no-lookahead entry selection.

    Filters use only information available at each signal date: the candidate's
    own as-of fields, previously selected trades' entry dates, and exits that
    have already happened (exit_date <= signal_date). Open-position state is
    tracked against the filtered stream itself; the realistic engine then
    applies its own static caps on top.
    """
    date_idx = {d: i for i, d in enumerate(trading_dates)}
    by_day: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for t in trades:
        by_day[str(t.get("signal_date"))[:10]].append(t)

    selected: List[Dict[str, Any]] = []
    for day in sorted(by_day):
        idx = date_idx.get(day)
        candidates = sorted(
            by_day[day],
            key=(lambda t: -composite_rank(t)) if cfg.rank_by == "composite" else (lambda t: -_f(t.get("score"))),
        )
        open_now = [
            s for s in selected
            if str(s.get("signal_date"))[:10] < day and str(s.get("exit_date"))[:10] > day
        ]
        cooldown_active = False
        if cfg.cooldown_after_loss and idx is not None:
            for s in selected:
                exit_day = str(s.get("exit_date"))[:10]
                e_idx = date_idx.get(exit_day)
                if e_idx is not None and 0 <= idx - e_idx <= COOLDOWN_DAYS and _f(s.get("net_return")) < 0:
                    cooldown_active = True
                    break
        taken_today = 0
        for t in candidates:
            if cfg.max_entries_per_day is not None and taken_today >= cfg.max_entries_per_day:
                break
            if cooldown_active:
                continue
            sector = str(t.get("sector") or "UNKNOWN")
            theme = str(t.get("theme") or "unknown")
            if cfg.skip_duplicate_sector_open and any(str(s.get("sector") or "UNKNOWN") == sector for s in open_now):
                continue
            if cfg.skip_duplicate_theme_open and theme != "unknown" and any(str(s.get("theme") or "unknown") == theme for s in open_now):
                continue
            row = dict(t)
            if cfg.vol_scaled_sizing:
                row["size_weight"] = vol_size_weight(t)
            selected.append(row)
            open_now.append(row)
            taken_today += 1
    return selected


def _year_expectancy(trades: Sequence[Dict[str, Any]], year: str) -> Optional[float]:
    vals = [float(t.get("net_return") or 0.0) for t in trades if str(t.get("exit_date") or "").startswith(year)]
    return round(statistics.mean(vals), 6) if vals else None


def evaluate_config(
    cfg: ClusterConfig,
    trades: Sequence[Dict[str, Any]],
    *,
    start: str,
    end: str,
    splits: Dict[str, Any],
    trading_dates: Sequence[str],
    baseline_ind_dd: float,
    random_expectancy: Optional[float],
) -> Dict[str, Any]:
    filtered = apply_entry_filters(trades, cfg, trading_dates)
    pconfig = portfolio.PortfolioConfig(max_open_positions=cfg.max_open_positions, max_sector_pct=cfg.max_sector_pct)
    independent = correction._augment_metrics(portfolio.independent_trade_metrics(filtered, start=start, end=end))
    realistic_raw = portfolio.realistic_portfolio_metrics(filtered, start=start, end=end, config=pconfig)
    rows = realistic_raw.get("daily_rows") or []
    same_spy = correction._same_exposure_benchmark("SPY", rows, start=start, end=end)
    same_qqq = correction._same_exposure_benchmark("QQQ", rows, start=start, end=end)
    realistic = correction._augment_metrics(realistic_raw)
    split_m = lrr_report._split_metrics(filtered, splits)
    month_check = correction._works_outside_one_month(filtered)
    concentration = correction._concentration(filtered)
    exact = lab.summarize_trades(filtered, start=start, end=end)
    ytd_2026 = _year_expectancy(filtered, "2026")
    test = split_m.get("test") or {}

    ind_dd = _f(independent.get("max_drawdown"), -1.0)
    gates = {
        "sharpe_beats_same_exposure_spy": {
            "passed": _f(realistic.get("sharpe"), -9.0) > _f(same_spy.get("sharpe"), -9.0),
            "detail": f"{realistic.get('sharpe')} vs same-exp SPY {same_spy.get('sharpe')}",
        },
        "realistic_maxdd_acceptable": {
            "passed": _f(realistic.get("max_drawdown"), -1.0) >= MAX_REALISTIC_DD,
            "detail": f"{_pct(realistic.get('max_drawdown'))} (floor {_pct(MAX_REALISTIC_DD)})",
        },
        "independent_dd_materially_improved": {
            "passed": ind_dd >= MIN_INDEPENDENT_DD and ind_dd >= baseline_ind_dd + MIN_INDEPENDENT_DD_GAIN,
            "detail": f"{_pct(ind_dd)} (floor {_pct(MIN_INDEPENDENT_DD)}, baseline {_pct(baseline_ind_dd)} + {MIN_INDEPENDENT_DD_GAIN * 100:.0f}pts)",
        },
        "walk_forward_test_positive": {
            "passed": int(test.get("trade_count") or 0) >= MIN_TEST_TRADES and _f(test.get("expectancy"), -1.0) > 0,
            "detail": f"test n={test.get('trade_count')}, expectancy {_pct(test.get('expectancy'))}",
        },
        "no_one_month_dependency": {
            "passed": bool(month_check.get("passes")),
            "detail": f"positive months {month_check.get('positive_months')}/{month_check.get('month_count')}, best share {month_check.get('best_positive_month_share')}",
        },
        "no_one_ticker_dependency": {
            "passed": _f(concentration.get("top_ticker_pct")) <= MAX_TICKER_CONC,
            "detail": f"top ticker {concentration.get('top_ticker')} ({concentration.get('top_ticker_pct')})",
        },
        "ytd_2026_positive": {
            "passed": ytd_2026 is not None and ytd_2026 > 0,
            "detail": f"2026 expectancy {_pct(ytd_2026)}",
        },
        "positive_edge_after_costs": {
            "passed": _f(exact.get("expectancy"), -1.0) > 0 and _f(realistic.get("total_return")) > 0
            and (random_expectancy is None or _f(exact.get("expectancy"), -1.0) > random_expectancy),
            "detail": f"expectancy {_pct(exact.get('expectancy'))} vs random {_pct(random_expectancy)}; realistic {_pct(realistic.get('total_return'))}",
        },
    }
    passed_all = all(g["passed"] for g in gates.values())
    return {
        "config": cfg.__dict__,
        "trade_count": exact.get("trade_count"),
        "expectancy": exact.get("expectancy"),
        "win_rate": exact.get("win_rate"),
        "independent_max_dd": independent.get("max_drawdown"),
        "realistic": {k: realistic.get(k) for k in ("total_return", "cagr", "max_drawdown", "sharpe", "sortino", "exposure_pct", "accepted_trade_count", "expectancy")},
        "same_exposure_spy": same_spy,
        "same_exposure_qqq": same_qqq,
        "walk_forward": split_m,
        "month_check": month_check,
        "concentration": {k: concentration.get(k) for k in ("top_ticker", "top_ticker_pct", "top_sector_pct", "top_theme_pct")},
        "ytd_2026_expectancy": ytd_2026,
        "gates": gates,
        "passed_all_gates": passed_all,
    }


def build_report(
    *,
    start: Optional[str] = None,
    end: Optional[str] = None,
    stride: int = 1,
) -> Dict[str, Any]:
    if start is None or end is None:
        span_start, span_end, _, _ = portfolio._primary_window_span()
        start = start or span_start
        end = end or span_end
    params = lab.StrategyParams()
    config = lab.BacktestConfig(universe_cap=140, date_stride=stride)
    splits = wf.make_walk_forward_splits(start=start, end=end).get("splits") or {}
    trading_dates = [str(pd.Timestamp(d).date()) for d in lab.d.trading_dates_between(start, end)]

    collected = collect_gated_trades(start, end, params=params, config=config)
    gated_trades = collected[VARIANT]
    random_trades = collected["RANDOM_LIQUID"]

    baseline_ind = portfolio.independent_trade_metrics(gated_trades, start=start, end=end)
    baseline_ind_dd = _f(baseline_ind.get("max_drawdown"), -1.0)
    random_exact = lab.summarize_trades(random_trades, start=start, end=end)
    random_real = correction._augment_metrics(
        portfolio.realistic_portfolio_metrics(random_trades, start=start, end=end, config=portfolio.PortfolioConfig())
    )

    results: Dict[str, Any] = {}
    for cfg in CONFIGS:
        results[cfg.config_id] = evaluate_config(
            cfg, gated_trades, start=start, end=end, splits=splits,
            trading_dates=trading_dates, baseline_ind_dd=baseline_ind_dd,
            random_expectancy=random_exact.get("expectancy"),
        )

    passing = [cid for cid, row in results.items() if row["passed_all_gates"] and cid != "BASELINE_GATED"]
    best = None
    if passing:
        best = max(passing, key=lambda cid: _f(results[cid]["realistic"].get("sharpe"), -9.0))
    verdict = CANDIDATE if passing else ARCHIVE_RECOMMENDED
    answer = "YES" if passing else "NO"

    return {
        "kind": "lrr_clustered_portfolio",
        "version": VERSION,
        "generated_at": _utc_now(),
        "research_only": True,
        "status": "RESEARCH_ONLY",
        "top_line": f"DID ANY PORTFOLIO-CONSTRUCTION CONFIG EARN PAPER-SHADOW? {answer}",
        "answer": answer,
        "verdict": verdict,
        "passing_configs": passing,
        "best_config": best,
        "signal_window": {"start": start, "end": end},
        "frozen_inputs": {
            "signal_rules": "LRR_REGIME_GATED unchanged (LRR_* constants + LRR_ALLOWED_REGIMES)",
            "allowed_regimes": sorted(lab.LRR_ALLOWED_REGIMES),
            "exits": "hold 10d, +10% target, reclaim-low/ATR stop 4-10%, next-open entry, base costs",
            "note": "only entry selection, open-position caps, and sizing vary; all configs pre-registered",
        },
        "pre_registered_gates": {
            "sharpe_beats_same_exposure_spy": True,
            "max_realistic_dd": MAX_REALISTIC_DD,
            "min_independent_dd": MIN_INDEPENDENT_DD,
            "min_independent_dd_gain_vs_baseline": MIN_INDEPENDENT_DD_GAIN,
            "min_test_trades": MIN_TEST_TRADES,
            "max_month_share": MAX_MONTH_SHARE,
            "max_ticker_concentration": MAX_TICKER_CONC,
            "ytd_2026_positive": True,
        },
        "baseline_independent_max_dd": round(baseline_ind_dd, 6),
        "params": params.as_dict(),
        "config": config.as_dict(),
        "walk_forward_splits": splits,
        "comparison_baselines": {
            "RANDOM_LIQUID": {
                "trade_count": random_exact.get("trade_count"),
                "expectancy": random_exact.get("expectancy"),
                "realistic_return": random_real.get("total_return"),
                "realistic_sharpe": random_real.get("sharpe"),
                "realistic_max_dd": random_real.get("max_drawdown"),
            },
            "cash": {"total_return": 0.0, "sharpe": None, "max_drawdown": 0.0},
        },
        "results": results,
        "paper_shadow": {
            "proposal_created": bool(passing),
            "proposal_doc": str(PROPOSAL_DOC) if passing else None,
            "status": verdict,
            "note": "Proposal doc only; nothing is activated.",
        },
        "safety": lab.safety_confirmations(),
    }


def render_text(res: Dict[str, Any]) -> List[str]:
    lines = [
        f"LRR CLUSTERED PORTFOLIO (PHASE 1I.2) - {res['generated_at']}",
        lab.RESEARCH_DISCLAIMER,
        res["top_line"],
        f"verdict={res['verdict']} passing={res['passing_configs']} best={res['best_config']}",
        f"baseline independent maxDD={_pct(res['baseline_independent_max_dd'])}",
        "",
        f"{'config':28s} {'n':>5s} {'exp':>8s} {'indDD':>8s} {'realRet':>8s} {'realDD':>8s} {'sharpe':>7s} {'sameSh':>7s} {'gates':>6s}",
    ]
    for cid, row in res["results"].items():
        real = row["realistic"]
        n_pass = sum(1 for g in row["gates"].values() if g["passed"])
        lines.append(
            f"{cid:28s} {row['trade_count'] or 0:>5d} {_pct(row['expectancy']):>8s} {_pct(row['independent_max_dd']):>8s} "
            f"{_pct(real.get('total_return')):>8s} {_pct(real.get('max_drawdown')):>8s} {str(real.get('sharpe')):>7s} "
            f"{str(row['same_exposure_spy'].get('sharpe')):>7s} {n_pass:>3d}/8"
        )
    lines += ["", "failed gates per config:"]
    for cid, row in res["results"].items():
        failed = [name for name, g in row["gates"].items() if not g["passed"]]
        lines.append(f"  {cid}: {', '.join(failed) if failed else 'ALL PASS'}")
    return lines


def render_doc(res: Dict[str, Any]) -> str:
    lines = [
        "# LRR Clustered Portfolio Results (Phase 1I.2)",
        "",
        f"Generated: {res['generated_at']}",
        "",
        lab.RESEARCH_DISCLAIMER,
        "",
        f"## {res['top_line']}",
        "",
        f"Verdict: **{res['verdict']}**",
        "",
        f"Signal window: `{res['signal_window']['start']}` to `{res['signal_window']['end']}`. "
        f"Signal rules, regime gate, and exits are FROZEN; only pre-registered portfolio construction varies. "
        f"Baseline independent maxDD: {_pct(res['baseline_independent_max_dd'])}.",
        "",
        "## Config Table",
        "",
        "| Config | Trades | Expectancy | Ind MaxDD | Real Return | Real MaxDD | Sharpe | Same-Exp SPY Sharpe | Gates |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for cid, row in res["results"].items():
        real = row["realistic"]
        n_pass = sum(1 for g in row["gates"].values() if g["passed"])
        lines.append(
            f"| {cid} | {row['trade_count']} | {_pct(row['expectancy'])} | {_pct(row['independent_max_dd'])} | "
            f"{_pct(real.get('total_return'))} | {_pct(real.get('max_drawdown'))} | {real.get('sharpe')} | "
            f"{row['same_exposure_spy'].get('sharpe')} | {n_pass}/8 |"
        )
    lines += [
        "",
        f"Baselines: RANDOM_LIQUID realistic {_pct(res['comparison_baselines']['RANDOM_LIQUID']['realistic_return'])} "
        f"(Sharpe {res['comparison_baselines']['RANDOM_LIQUID']['realistic_sharpe']}); cash +0.00%.",
        "",
        "## Gate Detail Per Config",
        "",
    ]
    for cid, row in res["results"].items():
        lines += [f"### {cid} — {row['config']['description']}", ""]
        for name, g in row["gates"].items():
            lines.append(f"- [{'PASS' if g['passed'] else 'FAIL'}] {name}: {g['detail']}")
        test = row["walk_forward"].get("test") or {}
        lines += [
            f"- walk-forward test: n={test.get('trade_count')}, expectancy {_pct(test.get('expectancy'))}",
            f"- 2026 YTD expectancy: {_pct(row['ytd_2026_expectancy'])}",
            "",
        ]
    lines += [
        "## Safety",
        "",
        "No paper signals, broker orders, trade proposals (doc-only), production thresholds, Gatekeeper/Veto logic,",
        "execution/governance/live-capital modules, historical evidence, or SHORT_A frozen status were changed.",
        "LRR entry rules, allowed regimes, and regime labels were not modified.",
        "",
    ]
    return "\n".join(lines)


def render_proposal_doc(res: Dict[str, Any]) -> str:
    best = res["best_config"]
    row = res["results"][best]
    real = row["realistic"]
    test = row["walk_forward"].get("test") or {}
    return "\n".join([
        "# Paper-Shadow Promotion Proposal: LRR_REGIME_GATED + portfolio construction (Phase 1I.2)",
        "",
        "Manual review required. This document does not activate anything.",
        "",
        f"## Winning config: {best}",
        "",
        f"- {row['config']['description']}",
        f"- All passing configs: {', '.join(res['passing_configs'])}",
        "",
        "## Evidence",
        "",
        f"- Trades: {row['trade_count']}, expectancy {_pct(row['expectancy'])}, win {row['win_rate']}",
        f"- Independent maxDD: {_pct(row['independent_max_dd'])} (baseline {_pct(res['baseline_independent_max_dd'])})",
        f"- Realistic: return {_pct(real.get('total_return'))}, maxDD {_pct(real.get('max_drawdown'))}, Sharpe {real.get('sharpe')}",
        f"- Same-exposure SPY Sharpe: {row['same_exposure_spy'].get('sharpe')}",
        f"- Walk-forward test: n={test.get('trade_count')}, expectancy {_pct(test.get('expectancy'))}",
        f"- 2026 YTD expectancy: {_pct(row['ytd_2026_expectancy'])}",
        "",
        "## Risk limits and kill criteria (paper-only)",
        "",
        f"- Portfolio rules exactly as config {best}; no discretionary overrides.",
        "- Kill if paper-shadow expectancy after 30 trades <= 0, drawdown-equivalent exceeds -10%,",
        "  any month contributes >65% of gains, or the regime classifier inputs change.",
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
    ap = argparse.ArgumentParser(description="Phase 1I.2 LRR entry clustering / sizing test (research-only)")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--stride", type=int, default=1)
    args = ap.parse_args(argv)
    res = build_report(start=args.start, end=args.end, stride=args.stride)
    write_outputs(res)
    print("\n".join(render_text(res)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

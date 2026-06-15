#!/usr/bin/env python3
"""
research/strategy_lab_decision.py - Phase 1H.1 decision-grade tournament table.

Research-only aggregator. Reads the exact-mode Strategy Lab artifacts
(exact recent-60, exact 2026 YTD, exact full windows), the exact walk-forward
artifact, and the exact threshold-sweep artifact, and produces one final
comparison table with a verdict per variant.

Promotion rule (hard): no variant may receive READY_FOR_PAPER_SHADOW_PROPOSAL
unless it passes BOTH the exact full-window backtest (BACKTEST_EDGE_DETECTED)
and the exact walk-forward (BACKTEST_EDGE_DETECTED on untouched test data).
Sampled inputs are refused outright. If no variant passes, the published
status is NO_VARIANT_READY_FOR_PAPER_SHADOW and no proposal document is
created. If one passes, only a proposal DOCUMENT is created - nothing is
activated, registered, or emitted.
"""
from __future__ import annotations

import argparse
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from research import strategy_research_lab as lab

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "cache" / "research"
OUT_JSON = CACHE / "strategy_lab_decision_latest.json"
OUT_TXT = ROOT / "logs" / "strategy_lab_decision_latest.txt"
OUT_DOC = ROOT / "docs" / "research" / "STRATEGY_LAB_DECISION_TOURNAMENT.md"
PROPOSAL_DOC = ROOT / "docs" / "research" / "PAPER_SHADOW_PROMOTION_PROPOSAL.md"

VERSION = "STRATEGY_LAB_DECISION_V1"

SOURCES = {
    "exact_recent60": CACHE / "strategy_lab_exact_recent60_latest.json",
    "exact_2026ytd": CACHE / "strategy_lab_exact_2026ytd_latest.json",
    "exact_full": CACHE / "strategy_lab_full_windows_latest.json",
    "walk_forward_exact": CACHE / "strategy_walk_forward_exact_latest.json",
    "threshold_sweep_exact": CACHE / "strategy_threshold_sweep_exact_latest.json",
}

TABLE_VARIANTS = list(lab.DEFAULT_VARIANTS) + ["SPY_BUY_HOLD", "QQQ_BUY_HOLD", "CASH"]
BENCHES = {"SPY_BUY_HOLD", "QQQ_BUY_HOLD", "CASH"}


def _load(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _full_metrics(full: Optional[Dict[str, Any]], variant: str) -> Dict[str, Any]:
    """Aggregate base-cost metrics for one variant across exact full windows."""
    out: Dict[str, Any] = {
        "trade_count": 0, "expectancy": None, "rel_spy": None, "rel_qqq": None,
        "max_drawdown": None, "positive_windows": 0, "windows_with_trades": 0,
    }
    if not full:
        return out
    windows = full.get("windows") or {}
    rel_spy: List[float] = []
    rel_qqq: List[float] = []
    dds: List[float] = []
    for w in windows.values():
        m = ((w.get("by_cost") or {}).get("base_cost") or {}).get(variant) or {}
        n = int(m.get("trade_count") or 0)
        out["trade_count"] += n
        if n:
            out["windows_with_trades"] += 1
            if float(m.get("expectancy") or 0.0) > 0:
                out["positive_windows"] += 1
            if m.get("rel_spy") is not None:
                rel_spy.append(float(m["rel_spy"]))
            if m.get("rel_qqq") is not None:
                rel_qqq.append(float(m["rel_qqq"]))
            if m.get("max_drawdown") is not None:
                dds.append(float(m["max_drawdown"]))
    out["expectancy"] = lab._aggregate_expectancy(windows, variant) if windows else None
    out["rel_spy"] = round(statistics.mean(rel_spy), 6) if rel_spy else None
    out["rel_qqq"] = round(statistics.mean(rel_qqq), 6) if rel_qqq else None
    out["max_drawdown"] = round(min(dds), 6) if dds else None
    return out


def _lab_verdict(artifact: Optional[Dict[str, Any]], variant: str) -> Optional[str]:
    if not artifact:
        return None
    return ((artifact.get("variant_verdicts") or {}).get(variant) or {}).get("verdict")


def _stability(metrics: Dict[str, Any]) -> str:
    n = metrics.get("windows_with_trades") or 0
    if not n:
        return "NO_TRADES"
    frac = (metrics.get("positive_windows") or 0) / n
    if n >= 3 and frac >= 0.7:
        return "STABLE_POSITIVE"
    if frac >= 0.5:
        return "MIXED"
    return "UNSTABLE"


def _final_verdict(
    variant: str,
    *,
    full_verdict: Optional[str],
    wf_verdict: Optional[str],
    wf_overfit: Optional[str],
    sweep_risk: Optional[str],
    metrics: Dict[str, Any],
    any_sampled: bool,
    missing_sources: List[str],
) -> tuple[str, List[str]]:
    reasons: List[str] = []
    if variant in BENCHES:
        return "BENCHMARK", ["benchmark row, not a candidate"]
    if any_sampled:
        reasons.append("one or more input artifacts were sampled; decision-grade promotion refused")
        return lab.VERDICT_NEED_MORE, reasons
    if missing_sources:
        reasons.append(f"missing exact artifacts: {', '.join(missing_sources)}")
        return lab.VERDICT_NEED_MORE, reasons
    trade_count = int(metrics.get("trade_count") or 0)
    if trade_count == 0:
        return lab.VERDICT_REJECT, ["zero trades across exact full windows"]
    # Hard promotion rule.
    if full_verdict == lab.VERDICT_EDGE and wf_verdict == lab.VERDICT_EDGE:
        return lab.VERDICT_READY, ["passed exact full-window backtest AND exact walk-forward test"]
    if full_verdict == lab.VERDICT_REJECT or wf_verdict == lab.VERDICT_REJECT:
        reasons.append(f"rejected by {'backtest' if full_verdict == lab.VERDICT_REJECT else 'walk-forward'}")
        return lab.VERDICT_REJECT, reasons
    if trade_count < 40 or full_verdict == lab.VERDICT_NEED_MORE or wf_verdict == lab.VERDICT_NEED_MORE:
        reasons.append("insufficient exact-sample evidence (trades or window/test coverage)")
        return lab.VERDICT_NEED_MORE, reasons
    if full_verdict == lab.VERDICT_EDGE:
        reasons.append("exact backtest edge but walk-forward did not confirm")
        if wf_overfit in ("HIGH_TEST_DECAY", "HIGH_TRAIN_ONLY") or (sweep_risk or "").startswith("HIGH"):
            return lab.VERDICT_OVERFIT_RISK, reasons + [f"overfit flags: wf={wf_overfit} sweep={sweep_risk}"]
        return lab.VERDICT_EDGE, reasons
    reasons.append(f"backtest verdict {full_verdict}, walk-forward {wf_verdict}")
    if wf_overfit in ("HIGH_TEST_DECAY", "HIGH_TRAIN_ONLY"):
        return lab.VERDICT_OVERFIT_RISK, reasons
    return lab.VERDICT_OVERFIT_RISK if full_verdict == lab.VERDICT_OVERFIT_RISK else lab.VERDICT_NEED_MORE, reasons


def build_decision() -> Dict[str, Any]:
    artifacts = {name: _load(path) for name, path in SOURCES.items()}
    missing = [name for name, a in artifacts.items() if a is None]
    sampled_sources = [
        name for name, a in artifacts.items()
        if a is not None and bool(a.get("sampled", False))
    ]
    wf = artifacts.get("walk_forward_exact") or {}
    sweep = artifacts.get("threshold_sweep_exact") or {}
    wf_final = wf.get("final_by_variant") or {}
    sweep_sel = sweep.get("selected_by_variant") or {}

    rows: List[Dict[str, Any]] = []
    for variant in TABLE_VARIANTS:
        metrics = _full_metrics(artifacts.get("exact_full"), variant)
        wf_row = wf_final.get(variant) or {}
        sweep_row = sweep_sel.get(variant) or {}
        verdict, reasons = _final_verdict(
            variant,
            full_verdict=_lab_verdict(artifacts.get("exact_full"), variant),
            wf_verdict=wf_row.get("verdict"),
            wf_overfit=wf_row.get("overfit_risk"),
            sweep_risk=sweep_row.get("overfit_risk"),
            metrics=metrics,
            any_sampled=bool(sampled_sources),
            missing_sources=missing,
        )
        rows.append({
            "variant": variant,
            "exact_recent60_verdict": _lab_verdict(artifacts.get("exact_recent60"), variant),
            "exact_2026ytd_verdict": _lab_verdict(artifacts.get("exact_2026ytd"), variant),
            "exact_full_verdict": _lab_verdict(artifacts.get("exact_full"), variant),
            "walk_forward_exact_verdict": wf_row.get("verdict"),
            "walk_forward_overfit_risk": wf_row.get("overfit_risk"),
            "threshold_sweep_verdict": sweep_row.get("overfit_risk"),
            "trade_count": metrics["trade_count"],
            "expectancy_base_cost": metrics["expectancy"],
            "rel_spy": metrics["rel_spy"],
            "rel_qqq": metrics["rel_qqq"],
            "max_drawdown": metrics["max_drawdown"],
            "stability": _stability(metrics),
            "overfit_risk": wf_row.get("overfit_risk") or sweep_row.get("overfit_risk"),
            "final_verdict": verdict,
            "reasons": reasons,
        })

    ready = [r for r in rows if r["final_verdict"] == lab.VERDICT_READY]
    paper_status = (
        f"READY_FOR_PAPER_SHADOW_PROPOSAL:{ready[0]['variant']}" if len(ready) == 1
        else "NO_VARIANT_READY_FOR_PAPER_SHADOW"
    )
    if len(ready) > 1:
        # One strategy in deep validation at a time - never propose several.
        paper_status = "NO_VARIANT_READY_FOR_PAPER_SHADOW"
        for r in ready:
            r["final_verdict"] = lab.VERDICT_EDGE
            r["reasons"].append("multiple variants passed; one-at-a-time doctrine defers proposal to operator")
        ready = []
    return {
        "kind": "strategy_lab_decision",
        "version": VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "research_only": True,
        "status": "RESEARCH_ONLY",
        "decision_grade": not sampled_sources and not missing,
        "sampled_sources_refused": sampled_sources,
        "missing_sources": missing,
        "source_generated_at": {
            name: (a or {}).get("generated_at") for name, a in artifacts.items()
        },
        "promotion_rule": (
            "READY_FOR_PAPER_SHADOW_PROPOSAL requires BACKTEST_EDGE_DETECTED on the exact "
            "full-window backtest AND on the exact walk-forward untouched test split; sampled "
            "inputs are refused"
        ),
        "table": rows,
        "ready_variants": [r["variant"] for r in ready],
        "paper_shadow": {
            "proposal_created": bool(ready),
            "status": paper_status,
        },
        "safety": lab.safety_confirmations(),
    }


def write_proposal_doc(decision: Dict[str, Any]) -> Optional[Path]:
    """Create the proposal DOCUMENT only - nothing is activated."""
    ready = decision.get("ready_variants") or []
    if len(ready) != 1:
        return None
    variant = ready[0]
    row = next(r for r in decision["table"] if r["variant"] == variant)
    wf = _load(SOURCES["walk_forward_exact"]) or {}
    wf_row = (wf.get("final_by_variant") or {}).get(variant) or {}
    lines = [
        "# Paper-Shadow Promotion Proposal (PROPOSAL ONLY - NOTHING ACTIVATED)",
        "",
        f"Generated: {decision['generated_at']}",
        "",
        lab.RESEARCH_DISCLAIMER,
        "",
        f"## Variant: {variant}",
        "",
        "## Evidence",
        "",
        f"- Exact full-window backtest verdict: {row['exact_full_verdict']}",
        f"- Exact walk-forward verdict: {row['walk_forward_exact_verdict']}",
        f"- Trades (exact full windows, base cost): {row['trade_count']}",
        f"- Expectancy after base costs: {row['expectancy_base_cost']}",
        f"- Rel SPY: {row['rel_spy']} · Rel QQQ: {row['rel_qqq']} · Max DD: {row['max_drawdown']}",
        f"- Walk-forward train/validation/test: {json.dumps({k: wf_row.get(k) for k in ('train', 'validation', 'test')}, default=str)}",
        "",
        "## Risk rules (paper-shadow, unchanged from lab simulation)",
        "",
        "- Stop loss, profit target, and max-hold exactly as in the winning parameter set.",
        "- Max 5 signals/day, liquidity floor $5M avg dollar volume, price >= $5.",
        "",
        "## Expected emission rate",
        "",
        f"- {row['trade_count']} trades over the exact full windows (see artifact for per-window counts).",
        "",
        "## Kill criteria",
        "",
        "- Paper expectancy after costs <= 0 over the first 40 resolved paper signals.",
        "- Max drawdown of the paper ledger worse than the backtest max drawdown by 1.5x.",
        "- Any reconciliation/hygiene violation in the paper ledger.",
        "",
        "## Required paper sample",
        "",
        "- Minimum 40 resolved paper-shadow signals across at least 2 calendar months before any further promotion discussion.",
        "",
        "## Why it remains paper-only",
        "",
        "- Backtest features are RECONSTRUCTED_FROM_PRICE_ONLY with CURRENT_METADATA_APPROXIMATION;",
        "  fundamentals/Gatekeeper/Lens were not point-in-time inputs.",
        "- The promotion ladder requires paper -> shadow -> limited live; this proposal only enters the paper stage.",
        "- The holdout (2026-12-01) is untouched; ALLOW_LIVE_CAPITAL stays false.",
        "",
        "NOTHING in this document activates a sleeve, registers a scanner, emits a paper signal, or changes governance.",
        "",
    ]
    PROPOSAL_DOC.parent.mkdir(parents=True, exist_ok=True)
    PROPOSAL_DOC.write_text("\n".join(lines), encoding="utf-8")
    return PROPOSAL_DOC


def render_text(res: Dict[str, Any]) -> List[str]:
    lines = [
        f"STRATEGY LAB DECISION TOURNAMENT - {res['generated_at']}",
        lab.RESEARCH_DISCLAIMER,
        f"decision_grade={res['decision_grade']} paper_shadow={res['paper_shadow']['status']}",
    ]
    if res.get("sampled_sources_refused"):
        lines.append(f"REFUSED sampled sources: {res['sampled_sources_refused']}")
    if res.get("missing_sources"):
        lines.append(f"missing sources: {res['missing_sources']}")
    lines += [
        "",
        f"{'variant':28s} {'final':32s} {'n':>5s} {'exp':>8s} {'relSPY':>8s} {'relQQQ':>8s} {'maxDD':>8s} {'stability':16s}",
    ]
    for r in res["table"]:
        def fmt(x: Any) -> str:
            return "n/a" if x is None else f"{float(x):+.4f}"
        lines.append(
            f"{r['variant']:28s} {r['final_verdict']:32s} {r['trade_count']:5d} "
            f"{fmt(r['expectancy_base_cost']):>8s} {fmt(r['rel_spy']):>8s} "
            f"{fmt(r['rel_qqq']):>8s} {fmt(r['max_drawdown']):>8s} {r['stability']:16s}"
        )
    return lines


def render_doc(res: Dict[str, Any]) -> str:
    lines = [
        "# Strategy Lab Decision Tournament (Phase 1H.1)",
        "",
        f"Generated: {res['generated_at']}",
        "",
        lab.RESEARCH_DISCLAIMER,
        "",
        f"Decision grade: `{res['decision_grade']}`",
        "",
        f"Promotion rule: {res['promotion_rule']}.",
        "",
    ]
    if res.get("sampled_sources_refused"):
        lines += [f"**Refused sampled sources:** {res['sampled_sources_refused']}", ""]
    if res.get("missing_sources"):
        lines += [f"**Missing sources:** {res['missing_sources']}", ""]
    lines += [
        "| Variant | recent60 | 2026 YTD | full | walk-fwd | sweep | Trades | Exp (base) | Rel SPY | Rel QQQ | Max DD | Stability | Overfit | FINAL |",
        "|---|---|---|---|---|---|---:|---:|---:|---:|---:|---|---|---|",
    ]
    def pct(x: Any) -> str:
        return "n/a" if x is None else f"{float(x) * 100:+.2f}%"
    for r in res["table"]:
        lines.append(
            f"| {r['variant']} | {r['exact_recent60_verdict'] or '-'} | {r['exact_2026ytd_verdict'] or '-'} | "
            f"{r['exact_full_verdict'] or '-'} | {r['walk_forward_exact_verdict'] or '-'} | "
            f"{r['threshold_sweep_verdict'] or '-'} | {r['trade_count']} | {pct(r['expectancy_base_cost'])} | "
            f"{pct(r['rel_spy'])} | {pct(r['rel_qqq'])} | {pct(r['max_drawdown'])} | {r['stability']} | "
            f"{r['overfit_risk'] or '-'} | **{r['final_verdict']}** |"
        )
    lines += [
        "",
        "## Per-variant reasons",
        "",
    ]
    for r in res["table"]:
        lines.append(f"- **{r['variant']}** -> {r['final_verdict']}: {'; '.join(r['reasons'])}")
    lines += [
        "",
        "## Paper-Shadow Decision",
        "",
        res["paper_shadow"]["status"],
        "",
        "No paper signals, broker orders, registry/governance/execution/live-capital changes, or historical evidence mutation.",
        "",
    ]
    return "\n".join(lines)


def write_outputs(res: Dict[str, Any]) -> None:
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_TXT.parent.mkdir(parents=True, exist_ok=True)
    OUT_DOC.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    OUT_TXT.write_text("\n".join(render_text(res)) + "\n", encoding="utf-8")
    OUT_DOC.write_text(render_doc(res), encoding="utf-8")


def main(argv: Optional[Sequence[str]] = None) -> int:
    argparse.ArgumentParser(description="Strategy Lab decision tournament (research-only)").parse_args(argv)
    res = build_decision()
    write_outputs(res)
    proposal = write_proposal_doc(res)
    if proposal is not None:
        print(f"proposal document written: {proposal}")
    print("\n".join(render_text(res)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

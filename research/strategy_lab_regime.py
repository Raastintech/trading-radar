#!/usr/bin/env python3
"""
research/strategy_lab_regime.py - point-in-time market regime labels.

Research-only helper for the Strategy Lab. Labels use only price-derived
features available as of the label date.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from research import strategy_lab_data as d  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT_JSON = ROOT / "cache" / "research" / "strategy_lab_regime_labels_latest.json"
OUT_DOC = ROOT / "docs" / "research" / "STRATEGY_LAB_REGIME_LABELS.md"

VERSION = "STRATEGY_LAB_REGIME_LABELS_V1"

BULL_TREND = "BULL_TREND"
CHOP = "CHOP"
MARKET_CORRECTION = "MARKET_CORRECTION"
TECH_LED_CORRECTION = "TECH_LED_CORRECTION"
RISK_OFF = "RISK_OFF"
RECOVERY_RECLAIM = "RECOVERY_RECLAIM"
HIGH_VOLATILITY = "HIGH_VOLATILITY"

CORRECTION_FAMILY = {MARKET_CORRECTION, TECH_LED_CORRECTION, CHOP, RECOVERY_RECLAIM}


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        value = float(value)
        return value if math.isfinite(value) else default
    except Exception:
        return default


def _feature(symbol: str, asof: Any) -> Dict[str, Any]:
    return d.compute_features_asof(symbol, asof) or {}


def classify_regime(asof: Any, *, correction_threshold: float = 0.05) -> Dict[str, Any]:
    ts = pd.Timestamp(asof).normalize()
    spy = _feature("SPY", ts)
    qqq = _feature("QQQ", ts)
    smh = _feature("SMH", ts)
    xlk = _feature("XLK", ts)
    vxx = _feature("VXX", ts) or _feature("VIXY", ts)

    spy_dd = min(_f(spy.get("drawdown_from_high20")), _f(spy.get("drawdown_from_high60")))
    qqq_dd = min(_f(qqq.get("drawdown_from_high20")), _f(qqq.get("drawdown_from_high60")))
    smh_dd = min(_f(smh.get("drawdown_from_high20")), _f(smh.get("drawdown_from_high60")))
    xlk_dd = min(_f(xlk.get("drawdown_from_high20")), _f(xlk.get("drawdown_from_high60")))

    spy_r20 = _f(spy.get("r20"))
    qqq_r20 = _f(qqq.get("r20"))
    qqq_vs_spy_20 = qqq_r20 - spy_r20
    smh_vs_spy_20 = _f(smh.get("r20")) - spy_r20
    xlk_vs_spy_20 = _f(xlk.get("r20")) - spy_r20
    tech_weak = (
        qqq_dd <= spy_dd - 0.015
        or qqq_vs_spy_20 <= -0.015
        or smh_vs_spy_20 <= -0.025
        or xlk_vs_spy_20 <= -0.025
    )
    below_short = (
        spy.get("above_ema20") is False
        and qqq.get("above_ema20") is False
        and spy.get("above_ma50") is False
        and qqq.get("above_ma50") is False
    )
    below_major = spy.get("above_ma200") is False or qqq.get("above_ma200") is False
    high_vol = (
        _f(spy.get("atr_pct")) >= 0.018
        or _f(qqq.get("atr_pct")) >= 0.022
        or _f(vxx.get("r10")) >= 0.12
    )
    correction = (
        spy_dd <= -correction_threshold
        or qqq_dd <= -correction_threshold
        or (below_short and min(spy_r20, qqq_r20) <= -0.025)
    )
    recovery = (
        correction
        and spy.get("above_ema20") is True
        and qqq.get("above_ema20") is True
        and _f(spy.get("r5")) > 0.025
        and _f(qqq.get("r5")) > 0.025
    )
    if below_major and correction and min(spy_r20, qqq_r20) <= -0.035:
        label = RISK_OFF
    elif recovery:
        label = RECOVERY_RECLAIM
    elif correction and tech_weak:
        label = TECH_LED_CORRECTION
    elif correction:
        label = MARKET_CORRECTION
    elif high_vol:
        label = HIGH_VOLATILITY
    elif below_short or abs(spy_r20) < 0.015 or abs(qqq_r20) < 0.015:
        label = CHOP
    else:
        label = BULL_TREND

    return {
        "date": str(ts.date()),
        "label": label,
        "inputs": {
            "spy_drawdown": round(spy_dd, 6),
            "qqq_drawdown": round(qqq_dd, 6),
            "smh_drawdown": round(smh_dd, 6),
            "xlk_drawdown": round(xlk_dd, 6),
            "spy_r5": spy.get("r5"),
            "spy_r10": spy.get("r10"),
            "spy_r20": spy.get("r20"),
            "qqq_r5": qqq.get("r5"),
            "qqq_r10": qqq.get("r10"),
            "qqq_r20": qqq.get("r20"),
            "qqq_vs_spy_20": round(qqq_vs_spy_20, 6),
            "smh_vs_spy_20": round(smh_vs_spy_20, 6),
            "xlk_vs_spy_20": round(xlk_vs_spy_20, 6),
            "spy_above_ema20": spy.get("above_ema20"),
            "qqq_above_ema20": qqq.get("above_ema20"),
            "spy_above_ma50": spy.get("above_ma50"),
            "qqq_above_ma50": qqq.get("above_ma50"),
            "spy_above_ma200": spy.get("above_ma200"),
            "qqq_above_ma200": qqq.get("above_ma200"),
            "spy_atr_pct": spy.get("atr_pct"),
            "qqq_atr_pct": qqq.get("atr_pct"),
            "vxx_r10": vxx.get("r10"),
            "correction_threshold": correction_threshold,
        },
        "flags": {
            "below_short_trend": bool(below_short),
            "below_major_trend": bool(below_major),
            "tech_weak": bool(tech_weak),
            "correction": bool(correction),
            "recovery_reclaim": bool(recovery),
            "high_volatility": bool(high_vol),
        },
        "data_reliability": {
            "price": d.TRUE_POINT_IN_TIME,
            "features": d.RECONSTRUCTED_FROM_PRICE_ONLY,
            "metadata": d.NOT_RETAINED,
            "manual_override": False,
        },
    }


def build_regime_labels(start: str = "2024-01-01", end: Optional[str] = None) -> Dict[str, Any]:
    cal = d.benchmark_calendar()
    latest = pd.Timestamp(end).normalize() if end else pd.Timestamp(cal.max()).normalize()
    dates = [pd.Timestamp(x).normalize() for x in cal[(cal >= pd.Timestamp(start)) & (cal <= latest)]]
    rows = [classify_regime(x) for x in dates]
    counts = Counter(row["label"] for row in rows)
    march_2026 = [row for row in rows if str(row["date"]).startswith("2026-03")]
    return {
        "kind": "strategy_lab_regime_labels",
        "version": VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "research_only": True,
        "status": "RESEARCH_ONLY",
        "start": str(pd.Timestamp(dates[0]).date()) if dates else None,
        "end": str(pd.Timestamp(dates[-1]).date()) if dates else None,
        "label_counts": dict(sorted(counts.items())),
        "rows": rows,
        "march_2026": {
            "label_counts": dict(sorted(Counter(row["label"] for row in march_2026).items())),
            "rows": march_2026,
            "manual_override_used": False,
        },
        "rules": [
            "RISK_OFF: major index below 200d support during a correction with weak 20d returns.",
            "RECOVERY_RECLAIM: correction context with SPY and QQQ reclaiming 20 EMA and strong 5d returns.",
            "TECH_LED_CORRECTION: correction context plus QQQ/SMH/XLK weakness versus SPY.",
            "MARKET_CORRECTION: SPY or QQQ drawdown exceeds threshold, or both are below 20 EMA/50 MA with weak 20d returns.",
            "HIGH_VOLATILITY: ATR or VXX/VIXY proxy stress elevated without correction classification.",
            "CHOP: below short trend or flat 20d returns without correction classification.",
            "BULL_TREND: default positive/non-stressed state.",
        ],
    }


def render_doc(res: Dict[str, Any]) -> str:
    lines = [
        "# Strategy Lab Regime Labels",
        "",
        f"Generated: {res['generated_at']}",
        "",
        "Research-only. Regime labels use only as-of price-derived market features.",
        "",
        f"Window: `{res.get('start')}` to `{res.get('end')}`",
        "",
        "## Label Counts",
        "",
        "| Label | Dates |",
        "|---|---:|",
    ]
    for label, count in (res.get("label_counts") or {}).items():
        lines.append(f"| {label} | {count} |")
    lines += [
        "",
        "## March 2026",
        "",
        f"Manual override used: `{res.get('march_2026', {}).get('manual_override_used')}`",
        "",
        "| Date | Label | SPY DD | QQQ DD | QQQ vs SPY 20d |",
        "|---|---|---:|---:|---:|",
    ]
    for row in (res.get("march_2026") or {}).get("rows") or []:
        inp = row.get("inputs") or {}
        lines.append(
            f"| {row['date']} | {row['label']} | "
            f"{inp.get('spy_drawdown', 0) * 100:+.2f}% | "
            f"{inp.get('qqq_drawdown', 0) * 100:+.2f}% | "
            f"{inp.get('qqq_vs_spy_20', 0) * 100:+.2f}% |"
        )
    lines += [
        "",
        "## Rules",
        "",
    ]
    lines.extend(f"- {x}" for x in res.get("rules") or [])
    return "\n".join(lines) + "\n"


def write_outputs(res: Dict[str, Any]) -> None:
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_DOC.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    OUT_DOC.write_text(render_doc(res), encoding="utf-8")


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Strategy Lab regime labels (research-only)")
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end")
    args = ap.parse_args(argv)
    res = build_regime_labels(start=args.start, end=args.end)
    write_outputs(res)
    print(f"wrote {OUT_JSON}")
    print(f"wrote {OUT_DOC}")
    print(f"March 2026 labels: {res['march_2026']['label_counts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

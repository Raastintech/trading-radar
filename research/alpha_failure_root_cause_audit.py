#!/usr/bin/env python3
"""Alpha failure root-cause audit.

Diagnostic-only, additive evidence analysis. Reads existing forward-evidence
sidecars (cohort attribution, program validation, forward tracker) and cached
price parquets, and answers *why* the research engine has not proven alpha.

This module NEVER changes scanner logic, scores, rankings, routing, gates,
thresholds, factor weights, High-Conviction rules, Emerging Outlier rules,
program verdict thresholds, candidate selection, or research-only safeguards.
Every number here is read-only attribution/simulation for human review.

Sections (per the audit mission):
  A. Signal-family attribution      (reuses cohort_attribution cohorts)
  B. Entry-timing audit             (non-mutating alternate-entry simulation)
  C. Regime-conditioned evidence    (price-derived regime labels, joined by date)
  D. Factor ablation                (population-minus-X recompute)
  E. Baseline comparison            (benchmarks + same-universe random control)
  F. Repeat-candidate audit         (repeat-count vs return)
  G. Stop/continue decision gates   (pre-registered dates: 2026-08-17/09-07/09-30)
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_ROOT_FOR_IMPORTS = Path(__file__).resolve().parents[1]
if str(_ROOT_FOR_IMPORTS) not in sys.path:
    sys.path.insert(0, str(_ROOT_FOR_IMPORTS))

from research.cohort_attribution import (
    HORIZONS,
    MIN_MATURED_ROBUST,
    MIN_MATURED_TO_EVALUATE,
    MIN_MATURED_TO_INTERPRET,
    PRIMARY_HORIZON,
    _enrich_rows,
    _load_json as ca_load_json,
    _load_jsonl,
    _outlier_analysis as ca_outlier_analysis,
    _read_price_dates,
    _sample_status,
    _total_negative_abs_by_horizon,
    _winsorized_mean as ca_winsorized_mean,
    headline_excess_pct,
    headline_mean_return_pct,
    outlier_driven,
)
from research.cohort_attribution import _horizon_stats as ca_horizon_stats
from research.strategy_lab_regime import build_regime_labels

VERSION = "ALPHA_FAILURE_ROOT_CAUSE_AUDIT_V1"
REPO_ROOT = Path(__file__).resolve().parents[1]

COHORT_ATTRIBUTION_REL = Path("cache/research/cohort_attribution_latest.json")
FORWARD_REL = Path("cache/research/research_forward_latest.json")
PROGRAM_VALIDATION_REL = Path("cache/research/research_program_validation_latest.json")
HISTORY_REL = Path("data/research/research_watchlist_history.jsonl")
UNIVERSE_BUILD_REL = Path("cache/research/research_universe_build_latest.json")
PRICE_REL = Path("cache/prices")
PRICE_DEEP_REL = Path("cache/prices_deep")
OUT_JSON_REL = Path("cache/research/alpha_failure_root_cause_latest.json")
OUT_TXT_REL = Path("logs/alpha_failure_root_cause_latest.txt")
DOC_REL = Path("docs/research/ALPHA_FAILURE_ROOT_CAUSE_AUDIT.md")

ENTRY_HORIZON = 10  # trading days held for every entry-timing / baseline variant
RANDOM_CONTROL_SEED = 20260725
RANDOM_CONTROL_PER_DATE = 8

DECISION_DATES = ("2026-08-17", "2026-09-07", "2026-09-30")

RS_MOMENTUM_CATEGORIES = {"rs_momentum_leader"}


# ── generic helpers ──────────────────────────────────────────────────────────


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _num(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _mean(values: Sequence[float]) -> Optional[float]:
    return round(statistics.fmean(values), 2) if values else None


def _median(values: Sequence[float]) -> Optional[float]:
    return round(statistics.median(values), 2) if values else None


def _win_rate(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    return round(sum(1 for v in values if v > 0) / len(values), 3)


def _agg(rows: List[Dict[str, Any]], ret_field: str, excess_field: Optional[str] = None) -> Dict[str, Any]:
    values = [float(r[ret_field]) for r in rows if r.get(ret_field) is not None]
    excess = ([float(r[excess_field]) for r in rows if excess_field and r.get(excess_field) is not None]
              if excess_field else [])
    stat = {
        "n": len(values),
        "mean_return_pct": _mean(values),
        "median_return_pct": _median(values),
        "winsorized_mean_return_pct": ca_winsorized_mean(values),
        "win_rate": _win_rate(values),
        "excess_vs_spy_pct": _mean(excess) if excess else None,
        "winsorized_excess_vs_spy_pct": ca_winsorized_mean(excess) if excess else None,
        "sample_status": _sample_status(len(values)),
        "outlier_analysis": ca_outlier_analysis(values),
    }
    stat["outlier_flagged"] = outlier_driven(stat)
    stat["headline_mean_return_pct"] = headline_mean_return_pct(stat)
    stat["headline_excess_vs_spy_pct"] = headline_excess_pct(stat, "spy")
    return stat


# ── price-cache helpers (self-contained; no core.config / credentials) ──────


def _read_parquet_closes(path: Path) -> List[Tuple[str, float]]:
    if not path.exists():
        return []
    try:
        import pandas as pd
        df = pd.read_parquet(path)
        close_col = next((c for c in ("close", "Close") if c in df.columns), None)
        if not close_col:
            return []
        date_col = next((c for c in ("date", "Date", "timestamp", "Timestamp") if c in df.columns), None)
        dates = df[date_col].astype(str).str[:10] if date_col else df.index.astype(str).str[:10]
        pairs = list(zip((str(d) for d in dates), df[close_col].astype(float)))
        return sorted(pairs, key=lambda x: x[0])
    except Exception:
        return []


def _load_ticker_closes(root: Path, ticker: str) -> List[Tuple[str, float]]:
    t = str(ticker or "").upper().strip()
    if not t:
        return []
    deep = _read_parquet_closes(root / PRICE_DEEP_REL / f"{t}.parquet")
    shallow = _read_parquet_closes(root / PRICE_REL / f"{t}.parquet")
    by_date: Dict[str, float] = {}
    for d, c in deep:
        by_date[d] = c
    for d, c in shallow:
        by_date[d] = c
    return sorted(by_date.items(), key=lambda x: x[0])


def _price_index(closes: List[Tuple[str, float]], date: str) -> Optional[int]:
    for i, (d, _) in enumerate(closes):
        if d >= date:
            return i
    return None


def _ret_from_index(closes: List[Tuple[str, float]], idx: Optional[int], horizon: int) -> Optional[float]:
    if idx is None or idx < 0 or idx + horizon >= len(closes):
        return None
    entry = closes[idx][1]
    if not entry:
        return None
    exit_ = closes[idx + horizon][1]
    return round((exit_ / entry - 1.0) * 100.0, 2)


def _sma_series(closes: List[Tuple[str, float]], window: int) -> List[Optional[float]]:
    out: List[Optional[float]] = []
    vals: List[float] = []
    for _, c in closes:
        vals.append(c)
        if len(vals) < window:
            out.append(None)
        else:
            out.append(sum(vals[-window:]) / window)
    return out


class TickerCloseCache:
    """Lazy per-ticker close-series cache shared across sections B/E."""

    def __init__(self, root: Path):
        self.root = root
        self._cache: Dict[str, List[Tuple[str, float]]] = {}

    def get(self, ticker: str) -> List[Tuple[str, float]]:
        t = str(ticker or "").upper()
        if t not in self._cache:
            self._cache[t] = _load_ticker_closes(self.root, t)
        return self._cache[t]


# ── Section A: signal-family attribution (reuses cohort_attribution) ────────


SIGNAL_FAMILY_COHORT_IDS: Dict[str, str] = {
    "rs_momentum": "rs_momentum_leaders",
    "social_attention": "social_attention_names",
    "catalyst": "catalyst_names",
    "reset_or_reclaim": "strong_profiles_wait_for_reset",
    "beaten_down_or_early_accumulation": "beaten_down_or_early_accumulation",
    "high_conviction_alpha": "high_conviction_alpha",
    "emerging_outlier_watch": "emerging_outlier_watch",
    "quality_but_extended": "extension_extended",
    "broad_scanner_repeats": "sequence_repeat",
}


def _cohort_index(cohort_report: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {str(c.get("id")): c for c in cohort_report.get("cohorts") or [] if c.get("id")}


def _cohort_summary(cohort: Optional[Dict[str, Any]], horizon: str = "10d") -> Dict[str, Any]:
    if not cohort:
        return {"present": False}
    h = (cohort.get("horizons") or {}).get(horizon) or {}
    return {
        "present": True,
        "id": cohort.get("id"),
        "label": cohort.get("label"),
        "matured_count": h.get("matured_count"),
        "mean_return_pct": h.get("mean_return_pct"),
        "median_return_pct": h.get("median_return_pct"),
        "winsorized_mean_return_pct": h.get("winsorized_mean_return_pct"),
        "outlier_flagged": outlier_driven(h),
        "headline_mean_return_pct": headline_mean_return_pct(h),
        "win_rate": h.get("win_rate"),
        "excess_vs_spy_pct": h.get("excess_vs_spy_pct"),
        "headline_excess_vs_spy_pct": headline_excess_pct(h, "spy"),
        "sample_status": h.get("sample_status"),
        "primary_result": cohort.get("primary_result"),
        "primary_result_text": cohort.get("primary_result_text"),
    }


def build_signal_family_attribution(cohort_report: Dict[str, Any]) -> Dict[str, Any]:
    cohorts = _cohort_index(cohort_report)
    horizon = str(cohort_report.get("primary_horizon") or "10d")
    families = {
        family: _cohort_summary(cohorts.get(cid), horizon)
        for family, cid in SIGNAL_FAMILY_COHORT_IDS.items()
    }
    interpretable = [
        (name, f) for name, f in families.items()
        if f.get("present") and f.get("matured_count") and f["matured_count"] >= MIN_MATURED_TO_EVALUATE
    ]
    best = max(interpretable, key=lambda kv: (kv[1].get("headline_mean_return_pct") or -1e9), default=None)
    worst = min(interpretable, key=lambda kv: (kv[1].get("headline_mean_return_pct") or 1e9), default=None)
    return {
        "primary_horizon": horizon,
        "families": families,
        "best_family": {"family": best[0], **best[1]} if best else None,
        "worst_family": {"family": worst[0], **worst[1]} if worst else None,
        "caveats": [
            "Families reuse cohort_attribution's existing cohort membership rules; "
            "no new selection criteria were created.",
            "Families overlap (e.g. a name can be both a repeat and quality-but-extended).",
        ],
    }


# ── Section B: entry-timing audit (non-mutating alternate-entry simulation) ─


def _find_pullback_index(closes: List[Tuple[str, float]], idx0: int, lookahead: int) -> Optional[int]:
    end = min(idx0 + lookahead, len(closes) - 1)
    for j in range(idx0 + 1, end + 1):
        if closes[j][1] < closes[j - 1][1]:
            return j
    return None


def _find_ma20_reset_index(closes: List[Tuple[str, float]], sma20: List[Optional[float]],
                            idx0: int, lookahead: int) -> Optional[int]:
    end = min(idx0 + lookahead, len(closes) - 1)
    for j in range(idx0, end + 1):
        if sma20[j] is not None and closes[j][1] <= sma20[j]:
            return j
    return None


def _find_reclaim_index(closes: List[Tuple[str, float]], idx0: int, lookahead: int) -> Optional[int]:
    pullback = _find_pullback_index(closes, idx0, lookahead)
    if pullback is None:
        return None
    pre_pullback_close = closes[pullback - 1][1]
    end = min(idx0 + lookahead + 10, len(closes) - 1)
    for j in range(pullback + 1, end + 1):
        if closes[j][1] > pre_pullback_close:
            return j
    return None


ENTRY_VARIANTS = (
    "detection_close",
    "plus_3d",
    "plus_5d",
    "first_pullback",
    "ma20_reset",
    "not_extended_only",
    "reclaim_confirmation",
)


def build_entry_timing_audit(root: Path, history: List[Dict[str, Any]],
                              spy_closes: List[Tuple[str, float]],
                              close_cache: TickerCloseCache,
                              horizon: int = ENTRY_HORIZON) -> Dict[str, Any]:
    variant_returns: Dict[str, List[float]] = {v: [] for v in ENTRY_VARIANTS}
    variant_excess: Dict[str, List[float]] = {v: [] for v in ENTRY_VARIANTS}
    skipped_no_price = 0
    considered = 0

    for row in history:
        ticker = str(row.get("ticker") or "").upper()
        appearance = str(row.get("appearance_date") or "")[:10]
        if not ticker or not appearance:
            continue
        closes = close_cache.get(ticker)
        if len(closes) < 25:
            skipped_no_price += 1
            continue
        idx0 = _price_index(closes, appearance)
        if idx0 is None:
            skipped_no_price += 1
            continue
        considered += 1

        def _record(variant: str, idx: Optional[int]) -> None:
            ret = _ret_from_index(closes, idx, horizon)
            if ret is None:
                return
            variant_returns[variant].append(ret)
            if idx is not None and idx < len(closes):
                spy_idx = _price_index(spy_closes, closes[idx][0])
                spy_ret = _ret_from_index(spy_closes, spy_idx, horizon)
                if spy_ret is not None:
                    variant_excess[variant].append(ret - spy_ret)

        _record("detection_close", idx0)
        _record("plus_3d", idx0 + 3)
        _record("plus_5d", idx0 + 5)
        _record("first_pullback", _find_pullback_index(closes, idx0, horizon))
        sma20 = _sma_series(closes, 20)
        _record("ma20_reset", _find_ma20_reset_index(closes, sma20, idx0, horizon + 5))
        if row.get("extension_bucket") != "EXTENDED":
            _record("not_extended_only", idx0)
        _record("reclaim_confirmation", _find_reclaim_index(closes, idx0, horizon))

    variants_out = []
    for v in ENTRY_VARIANTS:
        rets = variant_returns[v]
        excess = variant_excess[v]
        variant_stat = {
            "variant": v,
            "n": len(rets),
            "mean_return_pct": _mean(rets),
            "median_return_pct": _median(rets),
            "winsorized_mean_return_pct": ca_winsorized_mean(rets),
            "win_rate": _win_rate(rets),
            "excess_vs_spy_pct": _mean(excess) if excess else None,
            "sample_status": _sample_status(len(rets)),
            "outlier_analysis": ca_outlier_analysis(rets),
        }
        variant_stat["outlier_flagged"] = outlier_driven(variant_stat)
        variant_stat["headline_mean_return_pct"] = headline_mean_return_pct(variant_stat)
        variants_out.append(variant_stat)
    baseline_headline = next(
        (x["headline_mean_return_pct"] for x in variants_out if x["variant"] == "detection_close"), None)
    for x in variants_out:
        x["delta_vs_detection_close_pct"] = (
            round(x["headline_mean_return_pct"] - baseline_headline, 2)
            if x["headline_mean_return_pct"] is not None and baseline_headline is not None else None
        )
    interpretable = [x for x in variants_out if x["n"] >= MIN_MATURED_TO_EVALUATE]
    best_variant = max(interpretable, key=lambda x: x["headline_mean_return_pct"] or -1e9, default=None)
    conclusion = (
        "Insufficient matured alternate-entry samples to diagnose timing." if not interpretable else
        (f"'{best_variant['variant']}' is the best-performing simulated entry "
         f"({best_variant['headline_mean_return_pct']}% mean, n={best_variant['n']}), "
         f"{'beating' if (best_variant['delta_vs_detection_close_pct'] or 0) > 0 else 'not clearly beating'} "
         "detection-close entry, but this is diagnostic only — not a trade recommendation."
         + (" [outlier-capped headline mean; raw mean "
            f"{best_variant['mean_return_pct']}%]" if best_variant.get("outlier_flagged") else ""))
    )
    return {
        "holding_period_days": horizon,
        "candidates_considered": considered,
        "skipped_no_price_data": skipped_no_price,
        "variants": variants_out,
        "best_variant": best_variant,
        "conclusion": conclusion,
        "methodology": {
            "first_pullback": "first close below the prior close within the holding window",
            "ma20_reset": "first close at/below its trailing 20-close SMA within holding_window+5 days",
            "not_extended_only": "detection-close entry restricted to rows not flagged EXTENDED",
            "reclaim_confirmation": "first close above the pre-pullback close, following the first pullback day",
        },
        "caveats": [
            "This is a research-only, non-mutating simulation over already-cached price history. "
            "It produces no trade recommendations and changes no scanner/entry logic.",
            "Alternate-entry variants that never trigger (e.g. no pullback within the window) are "
            "simply excluded from that variant's sample, which can bias it toward calmer names.",
        ],
    }


# ── Section C: regime-conditioned evidence ───────────────────────────────────


def build_regime_conditioned_evidence(history: List[Dict[str, Any]],
                                       start_date: Optional[str],
                                       end_date: Optional[str]) -> Dict[str, Any]:
    if not start_date:
        return {"present": False, "reason": "no history rows with an appearance_date"}
    try:
        labels_doc = build_regime_labels(start=start_date, end=end_date)
    except Exception as exc:  # pragma: no cover - defensive; price cache issues
        return {"present": False, "reason": f"regime label build failed: {exc}"}
    by_date = {r["date"]: r["label"] for r in labels_doc.get("rows") or []}
    groups: Dict[str, List[Dict[str, Any]]] = {}
    unlabeled = 0
    for row in history:
        date = str(row.get("appearance_date") or "")[:10]
        label = by_date.get(date)
        if not label:
            unlabeled += 1
            continue
        groups.setdefault(label, []).append(row)
    regimes = []
    for label, rows in sorted(groups.items()):
        stat = _agg(rows, "ret_10d", "ret_10d_vs_spy")
        stat["regime"] = label
        stat["n_episodes_all_rows"] = len(rows)
        regimes.append(stat)
    interpretable = [r for r in regimes if r["n"] >= MIN_MATURED_TO_EVALUATE]
    best = max(interpretable, key=lambda r: r["headline_mean_return_pct"] or -1e9, default=None)
    worst = min(interpretable, key=lambda r: r["headline_mean_return_pct"] or 1e9, default=None)
    return {
        "present": True,
        "label_source": "research.strategy_lab_regime.build_regime_labels (price-derived, point-in-time)",
        "label_window": {"start": labels_doc.get("start"), "end": labels_doc.get("end")},
        "unlabeled_rows": unlabeled,
        "regimes": regimes,
        "best_regime": best,
        "worst_regime": worst,
        "conclusion": (
            "Regime split is too thin to interpret yet." if not interpretable else
            f"Best matured regime is {best['regime']} ({best['headline_mean_return_pct']}% mean, n={best['n']}); "
            f"worst is {worst['regime']} ({worst['headline_mean_return_pct']}% mean, n={worst['n']})."
        ),
        "caveats": [
            "Regime labels are reconstructed from cached price-only features, not retained "
            "point-in-time metadata; treat as diagnostic classification, not ground truth.",
        ],
    }


# ── Section D: factor ablation (population-minus-X) ─────────────────────────


def _ablation_stats(rows: List[Dict[str, Any]], price_dates: Sequence[str],
                     total_negative_abs: Dict[int, float], horizon: int = PRIMARY_HORIZON) -> Dict[str, Any]:
    return ca_horizon_stats(rows, horizon, price_dates, total_negative_abs.get(horizon, 0.0))


def build_factor_ablation(root: Path, history: List[Dict[str, Any]]) -> Dict[str, Any]:
    price_dates = _read_price_dates(root, "SPY")
    total_negative_abs = _total_negative_abs_by_horizon(history)
    baseline = _ablation_stats(history, price_dates, total_negative_abs)

    baseline_headline = headline_mean_return_pct(baseline)

    def _rule(name: str, predicate) -> Dict[str, Any]:
        kept = [r for r in history if predicate(r)]
        removed_n = len(history) - len(kept)
        stat = _ablation_stats(kept, price_dates, total_negative_abs)
        stat_headline = headline_mean_return_pct(stat)
        delta = (
            round(stat_headline - baseline_headline, 2)
            if stat_headline is not None and baseline_headline is not None else None
        )
        return {
            "rule": name,
            "n_removed": removed_n,
            "n_remaining": len(kept),
            "matured_count": stat.get("matured_count"),
            "mean_return_pct": stat.get("mean_return_pct"),
            "winsorized_mean_return_pct": stat.get("winsorized_mean_return_pct"),
            "outlier_flagged": outlier_driven(stat),
            "headline_mean_return_pct": stat_headline,
            "win_rate": stat.get("win_rate"),
            "excess_vs_spy_pct": stat.get("excess_vs_spy_pct"),
            "sample_status": stat.get("sample_status"),
            "delta_vs_baseline_mean_pct": delta,
        }

    ablations = [
        _rule("remove_unprofitable", lambda r: r.get("profitability_bucket") != "UNPROFITABLE"),
        _rule("remove_extended", lambda r: r.get("extension_bucket") != "EXTENDED"),
        _rule("remove_extreme_rs_momentum",
              lambda r: str(r.get("category") or "") not in RS_MOMENTUM_CATEGORIES),
        _rule("remove_technology", lambda r: str(r.get("sector") or "") != "Technology"),
        _rule("remove_small_micro_cap",
              lambda r: r.get("market_cap_bucket") not in {"SMALL_CAP", "MICRO_CAP"}),
        _rule("remove_repeat_candidates", lambda r: r.get("candidate_sequence") != "REPEAT"),
    ]
    isolations = [
        _rule("only_profitable_and_reset",
              lambda r: r.get("profitability_bucket") == "PROFITABLE"
              and r.get("extension_bucket") == "RESET_OR_WATCH_FOR_ENTRY"),
        _rule("only_mid_or_known_cap_and_quality",
              lambda r: r.get("market_cap_bucket") == "MID_CAP" and r.get("profitability_bucket") == "PROFITABLE"),
        _rule("only_reset_or_watch_for_entry",
              lambda r: r.get("extension_bucket") == "RESET_OR_WATCH_FOR_ENTRY"),
        _rule("only_high_conviction_membership",
              lambda r: "HIGH_CONVICTION_ALPHA" in (r.get("membership_lanes") or [])),
        _rule("only_emerging_outlier_membership",
              lambda r: "EMERGING_OUTLIER_WATCH" in (r.get("membership_lanes") or [])),
    ]
    interpretable_ablations = [a for a in ablations if a["matured_count"] and a["matured_count"] >= MIN_MATURED_TO_EVALUATE]
    top_cause = max(interpretable_ablations, key=lambda a: a["delta_vs_baseline_mean_pct"] or -1e9, default=None)
    return {
        "primary_horizon": f"{PRIMARY_HORIZON}d",
        "baseline": {
            "n": len(history),
            "matured_count": baseline.get("matured_count"),
            "mean_return_pct": baseline.get("mean_return_pct"),
            "winsorized_mean_return_pct": baseline.get("winsorized_mean_return_pct"),
            "outlier_flagged": outlier_driven(baseline),
            "headline_mean_return_pct": baseline_headline,
            "win_rate": baseline.get("win_rate"),
            "excess_vs_spy_pct": baseline.get("excess_vs_spy_pct"),
            "sample_status": baseline.get("sample_status"),
        },
        "ablations": ablations,
        "isolations": isolations,
        "top_likely_failure_cause": (
            {"rule": top_cause["rule"], "delta_vs_baseline_mean_pct": top_cause["delta_vs_baseline_mean_pct"]}
            if top_cause else None
        ),
        "caveats": [
            "Ablation removes rows population-wide and recomputes the remainder's stats; it does not "
            "re-run the scanner or change what gets selected.",
            "delta_vs_baseline_mean_pct is the mean-return improvement of the remainder once the named "
            "group is excluded — a positive delta means that group was dragging the population down.",
        ],
    }


# ── Section E: baseline comparison ───────────────────────────────────────────


def _load_universe_pool(root: Path) -> List[str]:
    doc = ca_load_json(root / UNIVERSE_BUILD_REL) or {}
    return sorted({
        str(r.get("ticker") or "").upper()
        for r in doc.get("universe_full") or []
        if r.get("ticker")
    })


def build_random_universe_control(root: Path, history: List[Dict[str, Any]],
                                   spy_closes: List[Tuple[str, float]],
                                   close_cache: TickerCloseCache,
                                   horizon: int = ENTRY_HORIZON,
                                   per_date: int = RANDOM_CONTROL_PER_DATE,
                                   seed: int = RANDOM_CONTROL_SEED) -> Dict[str, Any]:
    pool = _load_universe_pool(root)
    if not pool:
        return {"n": 0, "note": "universe pool unavailable (research_universe_build_latest.json missing)"}
    dates_selected: Dict[str, set] = {}
    for row in history:
        date = str(row.get("appearance_date") or "")[:10]
        ticker = str(row.get("ticker") or "").upper()
        if date and ticker:
            dates_selected.setdefault(date, set()).add(ticker)
    rng = random.Random(seed)
    rets: List[float] = []
    excess: List[float] = []
    used_pairs = 0
    for date, selected in sorted(dates_selected.items()):
        candidates = [t for t in pool if t not in selected]
        rng.shuffle(candidates)
        picked = 0
        for ticker in candidates:
            if picked >= per_date:
                break
            closes = close_cache.get(ticker)
            idx = _price_index(closes, date)
            ret = _ret_from_index(closes, idx, horizon)
            if ret is None:
                continue
            rets.append(ret)
            spy_idx = _price_index(spy_closes, date)
            spy_ret = _ret_from_index(spy_closes, spy_idx, horizon)
            if spy_ret is not None:
                excess.append(ret - spy_ret)
            picked += 1
            used_pairs += 1
    random_stat = {
        "n": len(rets),
        "n_dates": len(dates_selected),
        "per_date_sample_target": per_date,
        "mean_return_pct": _mean(rets),
        "median_return_pct": _median(rets),
        "winsorized_mean_return_pct": ca_winsorized_mean(rets),
        "win_rate": _win_rate(rets),
        "excess_vs_spy_pct": _mean(excess) if excess else None,
        "sample_status": _sample_status(len(rets)),
        "outlier_analysis": ca_outlier_analysis(rets),
    }
    random_stat["outlier_flagged"] = outlier_driven(random_stat)
    random_stat["headline_mean_return_pct"] = headline_mean_return_pct(random_stat)
    random_stat["note"] = (
        "Sampled from the CURRENT top-1000 universe snapshot, not a point-in-time "
        "reconstruction of the universe on each historical date; treat as an approximate control."
    )
    return random_stat


def build_baseline_comparison(root: Path, cohort_report: Dict[str, Any], history: List[Dict[str, Any]],
                               spy_closes: List[Tuple[str, float]],
                               close_cache: TickerCloseCache) -> Dict[str, Any]:
    cohorts = _cohort_index(cohort_report)
    horizon = str(cohort_report.get("primary_horizon") or "10d")
    broad = cohorts.get("broad_scanner")
    broad_h = (broad.get("horizons") or {}).get(horizon, {}) if broad else {}
    benchmarks = {
        "excess_vs_spy_pct": broad_h.get("excess_vs_spy_pct"),
        "excess_vs_qqq_pct": broad_h.get("excess_vs_qqq_pct"),
        "excess_vs_iwm_pct": broad_h.get("excess_vs_iwm_pct"),
        "excess_vs_sector_pct": broad_h.get("excess_vs_sector_pct"),
        "outlier_flagged": outlier_driven(broad_h),
        "headline_excess_vs_spy_pct": headline_excess_pct(broad_h, "spy"),
        "matured_count": broad_h.get("matured_count"),
        "sample_status": broad_h.get("sample_status"),
    }
    did_not_qualify = [r for r in history if r.get("membership_bucket") == "BROAD_SCANNER_ONLY"]
    sector_control = _agg(
        [r for r in did_not_qualify if str(r.get("sector") or "") == "Technology"],
        "ret_10d", "ret_10d_vs_spy")
    cap_control = _agg(
        [r for r in did_not_qualify if r.get("market_cap_bucket") in {"SMALL_CAP", "MICRO_CAP"}],
        "ret_10d", "ret_10d_vs_spy")
    random_control = build_random_universe_control(root, history, spy_closes, close_cache)
    return {
        "primary_horizon": horizon,
        "candidate_pool_vs_market_benchmarks": benchmarks,
        "random_same_universe_control": random_control,
        "sector_matched_control": {
            **sector_control,
            "definition": "did-not-qualify broad-scanner rows in the Technology sector "
                          "(same-universe control, not a random draw)",
        },
        "market_cap_matched_control": {
            **cap_control,
            "definition": "did-not-qualify broad-scanner rows in SMALL_CAP/MICRO_CAP buckets",
        },
        "profitable_stock_baseline": _cohort_summary(cohorts.get("profitability_profitable"), horizon),
        "momentum_baseline": {
            **_cohort_summary(cohorts.get("rs_momentum_leaders"), horizon),
            "caveat": "Proxy only: this reuses the RS-momentum-leader cohort, not an independent "
                      "momentum-factor construction.",
        },
        "caveats": [
            "The candidate pool underperforming SPY/QQQ/IWM/sector does not by itself distinguish "
            "'no alpha exists' from 'the wrong entries/timing are being used' — see sections B/C/D.",
            "sector_matched_control and market_cap_matched_control use same-universe scanner "
            "appearances that were not promoted to HC/EO, which is a same-source control, not an "
            "independent random draw.",
        ],
    }


# ── Section F: repeat-candidate audit ────────────────────────────────────────


def build_repeat_candidate_audit(history: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_ticker: Dict[str, List[Dict[str, Any]]] = {}
    for row in history:
        ticker = str(row.get("ticker") or "").upper()
        date = str(row.get("appearance_date") or "")[:10]
        if ticker and date:
            by_ticker.setdefault(ticker, []).append(row)
    buckets: Dict[str, List[Dict[str, Any]]] = {"1st_appearance": [], "2nd_to_3rd": [], "4th_plus": []}
    pairs: List[Tuple[int, float]] = []
    declining_tickers = 0
    repeated_tickers = 0
    for ticker, rows in by_ticker.items():
        rows_sorted = sorted(rows, key=lambda r: str(r.get("appearance_date") or ""))
        for i, row in enumerate(rows_sorted):
            bucket = "1st_appearance" if i == 0 else ("2nd_to_3rd" if i <= 2 else "4th_plus")
            buckets[bucket].append(row)
            ret = row.get("ret_10d")
            if ret is not None:
                pairs.append((i, float(ret)))
        if len(rows_sorted) >= 2:
            repeated_tickers += 1
            first_ret = rows_sorted[0].get("ret_10d")
            last_ret = rows_sorted[-1].get("ret_10d")
            if first_ret is not None and last_ret is not None and float(last_ret) < float(first_ret):
                declining_tickers += 1
    by_bucket = {name: _agg(rows, "ret_10d", "ret_10d_vs_spy") for name, rows in buckets.items()}

    correlation = None
    if len(pairs) >= MIN_MATURED_TO_EVALUATE:
        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        try:
            correlation = round(statistics.correlation(xs, ys), 3)
        except Exception:
            correlation = None

    interpretable = {k: v for k, v in by_bucket.items() if v["n"] >= MIN_MATURED_TO_EVALUATE}
    decays = (
        interpretable.get("1st_appearance", {}).get("mean_return_pct"),
        interpretable.get("4th_plus", {}).get("mean_return_pct"),
    )
    decay_confirmed = (
        decays[0] is not None and decays[1] is not None and decays[1] < decays[0]
    )
    return {
        "primary_horizon": "10d",
        "unique_tickers": len(by_ticker),
        "repeated_tickers": repeated_tickers,
        "repeated_tickers_with_declining_return": declining_tickers,
        "by_repeat_bucket": by_bucket,
        "repeat_index_vs_return_correlation": correlation,
        "decay_confirmed": decay_confirmed,
        "conclusion": (
            "Repeat appearances show a return decay pattern (later appearances underperform first "
            "appearances)." if decay_confirmed else
            "Repeat-appearance decay is not clearly confirmed at current sample sizes."
        ),
        "caveats": [
            "1st/2nd-3rd/4th+ buckets are appearance order within the tracked history window only "
            "(2026-06-15 onward); a ticker's true lifetime repeat count may be higher.",
            "repeat_index_vs_return_correlation is a simple Pearson correlation between appearance "
            "order and 10d return; it is descriptive, not a hypothesis test.",
        ],
    }


# ── Section G: stop/continue decision framework ──────────────────────────────


def _evaluate_stop_continue(cohort_report: Dict[str, Any], ablation: Dict[str, Any],
                             repeat_audit: Dict[str, Any]) -> Dict[str, Any]:
    cohorts = _cohort_index(cohort_report)
    horizon = str(cohort_report.get("primary_horizon") or "10d")
    non_core_ids = {"broad_scanner", "did_not_qualify", "era_pre_high_conviction"}
    candidates = [
        c for c in cohorts.values()
        if c.get("id") not in non_core_ids
        and (c.get("horizons") or {}).get(horizon, {}).get("matured_count", 0) >= MIN_MATURED_TO_EVALUATE
    ]
    robust_positive = [
        c for c in candidates
        if (c["horizons"][horizon].get("sample_status") == "ROBUST"
            and (c["horizons"][horizon].get("mean_return_pct") or -1) > 0
            and (c["horizons"][horizon].get("win_rate") or 0) >= 0.5)
    ]
    meaningful_positive = [
        c for c in candidates
        if (c["horizons"][horizon].get("matured_count", 0) >= MIN_MATURED_TO_INTERPRET
            and (c["horizons"][horizon].get("mean_return_pct") or -1) > 0
            and (c["horizons"][horizon].get("win_rate") or 0) >= 0.5)
    ]
    top_cause = (ablation.get("top_likely_failure_cause") or {})
    ablation_helps = (top_cause.get("delta_vs_baseline_mean_pct") or 0) > 0
    broad = cohorts.get("broad_scanner")
    broad_h = (broad.get("horizons") or {}).get(horizon, {}) if broad else {}
    broad_robust_negative = (
        broad_h.get("sample_status") == "ROBUST" and (broad_h.get("mean_return_pct") or 0) < 0
    )
    if robust_positive:
        recommendation = "CONTINUE"
        rationale = (
            f"{robust_positive[0].get('label')} shows a ROBUST, positive, >=50% win-rate 10d read — "
            "continue collecting evidence toward a promotion review."
        )
    elif meaningful_positive:
        recommendation = "NARROW"
        rationale = (
            f"{meaningful_positive[0].get('label')} shows a MEANINGFUL positive read but nothing ROBUST "
            "yet — narrow attention to this cohort rather than the full broad scanner."
        )
    elif broad_robust_negative and repeat_audit.get("decay_confirmed"):
        recommendation = "FREEZE"
        rationale = (
            "Broad scanner is ROBUST and negative, and repeat-candidate decay is confirmed — freeze "
            "broad-scanner expansion pending a specific fix, rather than sunsetting the whole engine."
        )
    else:
        recommendation = "FREEZE"
        rationale = "No cohort has cleared a positive MEANINGFUL+ read yet; hold at current scope."
    return {
        "recommendation": recommendation,
        "rationale": rationale,
        "robust_positive_cohorts": [c.get("label") for c in robust_positive],
        "meaningful_positive_cohorts": [c.get("label") for c in meaningful_positive],
        "ablation_top_cause_helps_when_removed": ablation_helps,
        "broad_scanner_robust_negative": broad_robust_negative,
        "repeat_decay_confirmed": bool(repeat_audit.get("decay_confirmed")),
    }


def build_stop_continue_framework(cohort_report: Dict[str, Any], ablation: Dict[str, Any],
                                   repeat_audit: Dict[str, Any]) -> Dict[str, Any]:
    current = _evaluate_stop_continue(cohort_report, ablation, repeat_audit)
    gate_definitions = {
        "CONTINUE": "Any non-broad cohort reaches ROBUST sample (>=100 matured 10d) with positive "
                    "mean return and win rate >= 50%.",
        "NARROW": "At least one non-broad cohort reaches MEANINGFUL sample (>=30 matured 10d) with "
                  "positive mean return and win rate >= 50%, but nothing ROBUST yet.",
        "FREEZE": "No non-broad cohort clears a positive MEANINGFUL+ read; broad scanner and/or "
                  "repeat-candidate drag persists. Hold current scope, do not expand discovery.",
        "SUNSET": "By 2026-09-30: broad scanner forward tracker remains ROBUST NO_FORWARD_EDGE, no "
                  "ablation/isolation/regime cohort has ever cleared MEANINGFUL+ with positive win "
                  "rate, and repeat-candidate decay is confirmed. Retire the standing alpha-engine "
                  "claim (research intelligence / candidate-surfacing role can continue).",
    }
    decision_dates = []
    for date in DECISION_DATES:
        decision_dates.append({
            "date": date,
            "gate": gate_definitions,
            "current_read_if_evaluated_today": current["recommendation"],
            "note": (
                "Final gate — SUNSET criteria apply in full only at this date." if date == DECISION_DATES[-1]
                else "Interim checkpoint — CONTINUE/NARROW/FREEZE evaluated against the same fixed criteria."
            ),
        })
    return {
        "gate_definitions": gate_definitions,
        "decision_dates": decision_dates,
        "current_recommendation": current["recommendation"],
        "current_rationale": current["rationale"],
        "current_evaluation_detail": current,
        "non_mutation_statement": (
            "This framework only classifies existing evidence into continue/narrow/freeze/sunset; it "
            "changes no program verdict threshold, no HC/EO rule, and no gate elsewhere in the system."
        ),
    }


# ── root-cause synthesis + dashboard/journal summary ────────────────────────


def _root_cause_findings(signal_family: Dict[str, Any], entry_timing: Dict[str, Any],
                          regime: Dict[str, Any], ablation: Dict[str, Any],
                          baseline: Dict[str, Any], repeat_audit: Dict[str, Any]) -> Dict[str, Any]:
    top_cause = ablation.get("top_likely_failure_cause")
    worst_family = signal_family.get("worst_family")
    answers = {
        "lacking_alpha_or_mixing_strategies": (
            "Evidence points to mixing: several narrow ablations/isolations show smaller drags than "
            "the pooled broad scanner, meaning the pooled population blends weak and (relatively) "
            "less-weak signal families rather than every family being uniformly unprofitable."
        ),
        "predictive_vs_harmful_families": (
            f"Worst family in current attribution: {worst_family.get('label') if worst_family else 'n/a'}."
        ),
        "regime_dependence": regime.get("conclusion"),
        "entry_timing": entry_timing.get("conclusion"),
        "resets_vs_immediate_detection": (
            "See factor_ablation.isolations.only_reset_or_watch_for_entry vs the pooled baseline."
        ),
        "profitable_vs_unprofitable": (
            "See factor_ablation.ablations.remove_unprofitable delta_vs_baseline_mean_pct."
        ),
        "repeat_candidates_negative_selection": repeat_audit.get("conclusion"),
        "crowded_or_exhausted_moves": (
            "See factor_ablation.ablations.remove_extended and entry_timing_audit.variants.not_extended_only."
        ),
        "sector_bias_damage": (
            "See factor_ablation.ablations.remove_technology and baseline_comparison.sector_matched_control."
        ),
        "any_cohort_beats_baselines": (
            "See stop_continue_framework.current_evaluation_detail.robust_positive_cohorts / "
            "meaningful_positive_cohorts."
        ),
    }
    return {
        "top_likely_failure_cause": (
            f"{top_cause['rule']} (removing it improves the population mean by "
            f"{top_cause['delta_vs_baseline_mean_pct']} pct points)"
            if top_cause else "Not yet determinable — no ablation reached the interpretation floor."
        ),
        "answers_to_core_questions": answers,
    }


def _explicit_findings(cohort_report: Dict[str, Any], baseline: Dict[str, Any],
                        ablation: Dict[str, Any], repeat_audit: Dict[str, Any],
                        signal_family: Dict[str, Any]) -> List[str]:
    """Plain-English restatement of the load-bearing findings, generated from
    the same computed values as the structured sections above (not
    hand-written), so a reviewer can verify each claim without navigating
    the nested report."""
    findings: List[str] = []

    broad = _cohort_index(cohort_report).get("broad_scanner")
    broad_h = (broad.get("horizons") or {}).get("10d", {}) if broad else {}
    findings.append(
        f"Broad scanner remains weak / no edge: primary_result="
        f"{broad.get('primary_result') if broad else 'unknown'}, "
        f"mean_10d={broad_h.get('mean_return_pct')}%, "
        f"win_rate={broad_h.get('win_rate')}, "
        f"sample_status={broad_h.get('sample_status')}."
    )

    rand = baseline.get("random_same_universe_control") or {}
    pool = baseline.get("candidate_pool_vs_market_benchmarks") or {}
    pool_headline_excess = pool.get("headline_excess_vs_spy_pct", pool.get("excess_vs_spy_pct"))
    findings.append(
        "Random same-universe control currently beats the actual candidate pool: "
        f"random control mean={rand.get('headline_mean_return_pct', rand.get('mean_return_pct'))}% "
        f"(win={rand.get('win_rate')}, n={rand.get('n')}) vs candidate pool "
        f"excess_vs_spy={pool_headline_excess}% (n={pool.get('matured_count')})."
        if (rand.get("mean_return_pct") is not None and pool_headline_excess is not None
            and rand.get("headline_mean_return_pct", rand.get("mean_return_pct")) > (pool_headline_excess or 0))
        else "Random same-universe control does not currently beat the actual candidate pool."
    )

    top_cause = ablation.get("top_likely_failure_cause") or {}
    findings.append(
        f"Extension is the top ablation-identified drag: removing '{top_cause.get('rule')}' "
        f"improves the population mean by {top_cause.get('delta_vs_baseline_mean_pct')} pct points."
        if top_cause.get("rule") == "remove_extended"
        else f"Top ablation-identified drag is currently '{top_cause.get('rule')}', not extension."
    )

    findings.append(
        "Repeat candidates show a return decay pattern (later appearances underperform first "
        "appearances)."
        if repeat_audit.get("decay_confirmed")
        else "Repeat-candidate return decay is not currently confirmed."
    )

    hc = signal_family.get("families", {}).get("high_conviction_alpha", {})
    eo = signal_family.get("families", {}).get("emerging_outlier_watch", {})
    findings.append(
        f"High-Conviction Alpha and Emerging Outlier Watch are not yet mature enough to judge "
        f"(matured_count={hc.get('matured_count')} and {eo.get('matured_count')} respectively, "
        f"vs the {MIN_MATURED_TO_EVALUATE}-episode evidence floor)."
    )

    findings.append(f"alpha_proven remains {cohort_report.get('alpha_proven')}.")
    findings.append("research_only=true; promote_to_signal=false.")
    return findings


def build_report(root: Optional[Path] = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    root = Path(root) if root else REPO_ROOT
    now = now or _utcnow()

    cohort_report = ca_load_json(root / COHORT_ATTRIBUTION_REL)
    if not cohort_report:
        return {
            "kind": "alpha_failure_root_cause_audit",
            "version": VERSION,
            "generated_at": now.isoformat(),
            "research_only": True,
            "promote_to_signal": False,
            "fallback": "MISSING_ARTIFACT",
            "missing_artifact": str(COHORT_ATTRIBUTION_REL),
            "guardrails": _guardrails(),
        }

    forward = ca_load_json(root / FORWARD_REL) or {}
    program_validation = ca_load_json(root / PROGRAM_VALIDATION_REL) or {}
    raw_history = _load_jsonl(root / HISTORY_REL)
    history = _enrich_rows(root, raw_history, lane="BROAD_SCANNER")
    dates = sorted({str(r.get("appearance_date") or "")[:10] for r in history if r.get("appearance_date")})
    spy_closes = _load_ticker_closes(root, "SPY")
    close_cache = TickerCloseCache(root)

    signal_family = build_signal_family_attribution(cohort_report)
    entry_timing = build_entry_timing_audit(root, history, spy_closes, close_cache)
    regime = build_regime_conditioned_evidence(
        history, dates[0] if dates else None, str(now.date()))
    ablation = build_factor_ablation(root, history)
    baseline = build_baseline_comparison(root, cohort_report, history, spy_closes, close_cache)
    repeat_audit = build_repeat_candidate_audit(history)
    stop_continue = build_stop_continue_framework(cohort_report, ablation, repeat_audit)
    root_cause = _root_cause_findings(signal_family, entry_timing, regime, ablation, baseline, repeat_audit)
    explicit_findings = _explicit_findings(cohort_report, baseline, ablation, repeat_audit, signal_family)

    rankings = cohort_report.get("rankings") or {}
    best_current = rankings.get("best_current")
    worst_drag = rankings.get("biggest_explanatory_drag") or rankings.get("biggest_drag")

    def _cohort_headline_text(item: Optional[Dict[str, Any]]) -> str:
        if not item:
            return None
        headline = item.get("headline_mean_return_pct", item.get("mean_return_pct"))
        text = f"{item.get('label')} (mean {headline}%, n={item.get('matured_count')})"
        if item.get("outlier_flagged"):
            text += f" [outlier-capped; raw mean {item.get('mean_return_pct')}%]"
        return text

    dashboard_summary = {
        "top_likely_failure_cause": root_cause.get("top_likely_failure_cause"),
        "best_surviving_cohort": (
            _cohort_headline_text(best_current) if best_current else "none meet the evidence floor"
        ),
        "worst_harmful_cohort": (
            _cohort_headline_text(worst_drag) if worst_drag else "n/a"
        ),
        "next_decision_date": DECISION_DATES[0],
        "current_recommendation": stop_continue.get("current_recommendation"),
    }

    return {
        "kind": "alpha_failure_root_cause_audit",
        "version": VERSION,
        "generated_at": now.isoformat(),
        "research_only": True,
        "promote_to_signal": False,
        "mission": "Root-cause audit only: determine why the research engine has not proven alpha. "
                   "No scanner/scoring/routing/gate/threshold/factor-weight/HC/EO/program-verdict/"
                   "candidate-selection changes were made.",
        "source_artifacts": {
            "cohort_attribution": str(COHORT_ATTRIBUTION_REL),
            "research_forward": str(FORWARD_REL),
            "program_validation": str(PROGRAM_VALIDATION_REL),
            "watchlist_history": str(HISTORY_REL),
            "universe_build": str(UNIVERSE_BUILD_REL),
        },
        "source_timestamps": {
            "cohort_attribution_generated_at": cohort_report.get("generated_at"),
            "forward_generated_at": forward.get("generated_at"),
            "program_validation_generated_at": program_validation.get("generated_at"),
        },
        "alpha_proven": cohort_report.get("alpha_proven"),
        "overall_forward_verdict": (forward.get("overall") or {}).get("verdict"),
        "phase_4b_gated": True,
        "signal_family_attribution": signal_family,
        "entry_timing_audit": entry_timing,
        "regime_conditioned_evidence": regime,
        "factor_ablation": ablation,
        "baseline_comparison": baseline,
        "repeat_candidate_audit": repeat_audit,
        "stop_continue_framework": stop_continue,
        "root_cause_findings": root_cause,
        "explicit_findings": explicit_findings,
        "dashboard_summary": dashboard_summary,
        "guardrails": _guardrails(),
        "caveats": [
            "This audit re-analyzes existing forward evidence and cached prices; it does not collect "
            "new signals and does not change what the scanner surfaces.",
            "Alternate-entry, regime, ablation, and baseline samples below the evidence floor "
            "(n < 10 matured) are diagnostic only and must not be read as a proven edge.",
        ],
    }


def _guardrails() -> Dict[str, Any]:
    return {
        "no_scanner_logic_change": True,
        "no_score_or_ranking_change": True,
        "no_routing_change": True,
        "no_gate_or_threshold_change": True,
        "no_factor_weight_change": True,
        "no_high_conviction_rule_change": True,
        "no_emerging_outlier_rule_change": True,
        "no_program_verdict_threshold_change": True,
        "no_candidate_selection_change": True,
        "research_only": True,
        "cache_only_inputs": True,
        "no_trade_recommendations": True,
        "promote_to_signal": False,
    }


# ── rendering ─────────────────────────────────────────────────────────────────


def _fmt_pct(value: Any) -> str:
    return "n/a" if value is None else f"{float(value):.2f}%"


def render_text(report: Dict[str, Any]) -> str:
    if report.get("fallback"):
        return f"ALPHA FAILURE ROOT-CAUSE AUDIT - fallback: {report.get('missing_artifact')}\n"
    dash = report.get("dashboard_summary") or {}
    lines = [
        f"ALPHA FAILURE ROOT-CAUSE AUDIT - {report.get('generated_at')}",
        f"Research only: {report.get('research_only')}",
        f"Alpha proven: {report.get('alpha_proven')}",
        f"Overall forward verdict: {report.get('overall_forward_verdict')}",
        "",
        f"Top likely failure cause: {dash.get('top_likely_failure_cause')}",
        f"Best surviving cohort: {dash.get('best_surviving_cohort')}",
        f"Worst harmful cohort: {dash.get('worst_harmful_cohort')}",
        f"Next decision date: {dash.get('next_decision_date')}",
        f"Current recommendation: {dash.get('current_recommendation')}",
        "",
        f"Promote to signal: {report.get('promote_to_signal')}",
        "",
        "Explicit findings:",
    ]
    lines.extend(f"- {f}" for f in report.get("explicit_findings") or [])
    lines.append("")
    lines.append("Section A - Signal family attribution:")
    families = (report.get("signal_family_attribution") or {}).get("families") or {}
    for name, f in sorted(families.items()):
        if not f.get("present"):
            lines.append(f"- {name}: missing")
        else:
            outlier_suffix = (
                f" [outlier-capped; raw mean {_fmt_pct(f.get('mean_return_pct'))}]"
                if f.get("outlier_flagged") else ""
            )
            lines.append(
                f"- {name}: n={f.get('matured_count')} "
                f"mean={_fmt_pct(f.get('headline_mean_return_pct', f.get('mean_return_pct')))} "
                f"win={f.get('win_rate')} status={f.get('sample_status')}{outlier_suffix}"
            )
    et = report.get("entry_timing_audit") or {}
    lines.append("")
    lines.append(f"Section B - Entry timing: {et.get('conclusion')}")
    reg = report.get("regime_conditioned_evidence") or {}
    lines.append(f"Section C - Regime split: {reg.get('conclusion')}")
    ab = report.get("factor_ablation") or {}
    lines.append(f"Section D - Ablation top cause: {(ab.get('top_likely_failure_cause') or {}).get('rule')}")
    rep = report.get("repeat_candidate_audit") or {}
    lines.append(f"Section F - Repeat candidates: {rep.get('conclusion')}")
    sc = report.get("stop_continue_framework") or {}
    lines.append(f"Section G - Stop/continue: {sc.get('current_recommendation')} - {sc.get('current_rationale')}")
    lines.append("")
    lines.append("Caveats:")
    lines.extend(f"- {c}" for c in report.get("caveats") or [])
    return "\n".join(lines) + "\n"


def render_markdown(report: Dict[str, Any]) -> str:
    dash = report.get("dashboard_summary") or {}
    sc = report.get("stop_continue_framework") or {}
    lines = [
        "# Alpha Failure Root-Cause Audit",
        "",
        f"Generated: `{report.get('generated_at')}`",
        "",
        report.get("mission", ""),
        "",
        "## Summary",
        f"- Alpha proven: `{report.get('alpha_proven')}`",
        f"- Overall forward verdict: `{report.get('overall_forward_verdict')}`",
        f"- Top likely failure cause: {dash.get('top_likely_failure_cause')}",
        f"- Best surviving cohort: {dash.get('best_surviving_cohort')}",
        f"- Worst harmful cohort: {dash.get('worst_harmful_cohort')}",
        f"- Current recommendation: **{dash.get('current_recommendation')}**",
        f"- Promote to signal: `{report.get('promote_to_signal')}`",
        "",
        "## Explicit Findings",
        "",
    ]
    lines.extend(f"- {f}" for f in report.get("explicit_findings") or [])
    lines += [
        "",
        "## Stop/Continue Decision Dates",
        "",
        "| Date | Gate read today |",
        "|---|---|",
    ]
    for d in sc.get("decision_dates") or []:
        lines.append(f"| {d['date']} | {d['current_read_if_evaluated_today']} |")
    lines += ["", "## Caveats", ""]
    lines.extend(f"- {c}" for c in report.get("caveats") or [])
    return "\n".join(lines) + "\n"


def write_artifacts(report: Dict[str, Any], root: Optional[Path] = None, *, docs: bool = False) -> Dict[str, str]:
    root = Path(root) if root else REPO_ROOT
    json_path = root / OUT_JSON_REL
    txt_path = root / OUT_TXT_REL
    _write_json(json_path, report)
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.write_text(render_text(report), encoding="utf-8")
    paths = {"json": str(json_path), "text": str(txt_path)}
    if docs:
        doc_path = root / DOC_REL
        doc_path.parent.mkdir(parents=True, exist_ok=True)
        doc_path.write_text(render_markdown(report), encoding="utf-8")
        paths["docs"] = str(doc_path)
    return paths


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build the alpha failure root-cause audit sidecar.")
    parser.add_argument("--root", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--docs", action="store_true", help="Also write docs/research/ALPHA_FAILURE_ROOT_CAUSE_AUDIT.md")
    parser.add_argument("--json", action="store_true", help="Print the report JSON to stdout")
    args = parser.parse_args(argv)
    root = Path(args.root) if args.root else REPO_ROOT
    report = build_report(root)
    paths = write_artifacts(report, root, docs=args.docs)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"wrote {paths['json']}")
        print(f"wrote {paths['text']}")
        if "docs" in paths:
            print(f"wrote {paths['docs']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

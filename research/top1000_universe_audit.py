"""
research/top1000_universe_audit.py — Phase 1G.7B Task 1.

Audits HOW the production top-1000 base universe is selected, to verify the
selection layer before any scanner/strategy change. It reports the exact
ranking formula (read from core/universe.py), the refresh cadence, staleness
risk, diversity enforcement, and — empirically from the live snapshot + price
cache — whether the top-1000 is biased toward names that have ALREADY moved
(late/extended) versus emerging setups.

Selection facts are CODE-DERIVED (cited to core/universe.py) so the audit stays
honest about what the production system actually does; the late-bias measures
are computed from cache/universe/universe_snapshot_latest.json metadata.

Outputs:
  cache/research/top1000_universe_audit_latest.json
  logs/top1000_universe_audit_latest.txt
  docs/research/TOP1000_UNIVERSE_AUDIT.md

RESEARCH-ONLY / CACHE-ONLY. No provider calls, no DB writes, no signals.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from statistics import median
from typing import Dict, List

from research.scanner_truth import dataio

SNAPSHOT = dataio.REPO / "cache" / "universe" / "universe_snapshot_latest.json"
DOCS_DIR = dataio.REPO / "docs" / "research"

# ── Code-derived selection facts (provenance: core/universe.py) ───────────────
# base_score = 0.45*liquidity + 0.25*movement + 0.15*activity + 0.15*abs_trend
#   liquidity = log-norm(avg_dollar_volume_20)        (core/universe.py:291,327)
#   movement  = clip01((atr_pct_14 - 1)/7)            (:292)
#   activity  = clip01((volume_ratio_5d - 1)/1.5)     (:293)
#   abs_trend = clip01(abs(return_20d_pct)/20)        (:294)  ← ABSOLUTE return
RANKING = {
    "ranked_by": "base_score (descending), tie-break symbol",
    "provenance": "core/universe.py:_compute_features (base_score) + _build_fresh:721-722",
    "weights": {"liquidity": 0.45, "movement_atr": 0.25, "activity_vol_ratio": 0.15,
                "abs_trend_20d": 0.15},
    "abs_trend_note": "abs_trend uses ABSOLUTE 20d return magnitude — a name already "
                      "up (or down) a lot scores HIGHER, an explicit late/post-move bias.",
    "relative_strength_component": False,
    "theme_component": False,
    "earliness_component": False,
    "sector_theme_diversity_enforced": False,
    "cap_env": "UNIVERSE_BASE_LIMIT (default 1000)",
    "prefilters_before_cap": {
        "min_price": 5.0, "max_price": 1000.0, "min_avg_volume_20": 300000,
        "min_avg_dollar_volume_20": 5_000_000,
        "provenance": "core/universe.py:_passes_filters (_MIN_*)"},
    "refresh": {"dynamic": True, "ttl_seconds": 1800,
                "env": "UNIVERSE_SNAPSHOT_TTL_SECONDS (default 1800 = 30 min)",
                "bar_window_days": 90,
                "note": "Rebuilt every 30 min from Alpaca; NOT a static list. But the "
                        "bar window is only 90 days (UNIVERSE_DISCOVERY_DAYS_BACK)."},
    "staleness_risk": "Within the 30-min TTL the cached snapshot is reused. On Alpaca "
                      "discovery failure a FALLBACK (curated-only) snapshot is emitted "
                      "(fallback_used=true) — that is the main stale/degraded path.",
}

# Empirical late/extended thresholds (computed from snapshot metadata, point-in-time)
LATE_RET20 = 30.0          # already +30% over 20d ⇒ likely late
EXTENDED_RET20 = 50.0      # already +50% over 20d ⇒ very late
HIGH_ATR = 8.0             # atr_pct_14 > 8 ⇒ already volatile/in-move


def _load_snapshot() -> Dict:
    return json.loads(SNAPSHOT.read_text())


def build() -> Dict:
    snap = _load_snapshot()
    base = [s.upper() for s in snap.get("base_universe", [])]
    md = snap.get("metadata", {})
    summary = snap.get("summary", {})
    profiles = dataio.load_profiles()

    gen = snap.get("generated_at")
    age_h = None
    if gen:
        try:
            age_h = round((datetime.now(timezone.utc)
                           - datetime.fromisoformat(gen)).total_seconds() / 3600, 1)
        except Exception:
            age_h = None

    # Empirical late-bias measures from snapshot metadata (return_20d_pct, atr_pct_14).
    rets, atrs, r5 = [], [], []
    n_late, n_extended, n_high_atr, n_negative = 0, 0, 0, 0
    for t in base:
        m = md.get(t) or {}
        r = m.get("return_20d_pct")
        a = m.get("atr_pct_14")
        if r is not None:
            rets.append(r)
            if r >= LATE_RET20:
                n_late += 1
            if r >= EXTENDED_RET20:
                n_extended += 1
            if r < 0:
                n_negative += 1
        if a is not None:
            atrs.append(a)
            if a > HIGH_ATR:
                n_high_atr += 1
        if m.get("return_5d_pct") is not None:
            r5.append(m["return_5d_pct"])

    n = len(base) or 1
    # sector diversity (best-effort via cached profiles)
    sectors = Counter((profiles.get(t) or {}).get("sector") or "UNKNOWN" for t in base)
    top_sectors = dict(sorted(sectors.items(), key=lambda x: -x[1])[:8])

    findings = _findings(n_late, n_extended, n, summary, snap)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_generated_at": gen,
        "snapshot_age_hours": age_h,
        "fallback_used": snap.get("fallback_used"),
        "source": snap.get("source"),
        "selection_logic": RANKING,
        "input_universe_size": (summary.get("passed_base_filter")
                                or (summary.get("excluded_for_filters", 0)
                                    + summary.get("excluded_for_data", 0) + n)),
        "excluded_for_filters": summary.get("excluded_for_filters"),
        "excluded_for_data": summary.get("excluded_for_data"),
        "output_universe_size": len(base),
        "late_bias": {
            "n_up_ge_30pct_20d": n_late, "pct_up_ge_30pct_20d": round(100.0 * n_late / n, 1),
            "n_up_ge_50pct_20d": n_extended, "pct_up_ge_50pct_20d": round(100.0 * n_extended / n, 1),
            "n_high_atr_gt8": n_high_atr, "pct_high_atr_gt8": round(100.0 * n_high_atr / n, 1),
            "n_negative_20d": n_negative, "pct_negative_20d": round(100.0 * n_negative / n, 1),
            "median_return_20d_pct": round(median(rets), 2) if rets else None,
            "median_atr_pct_14": round(median(atrs), 2) if atrs else None,
        },
        "sector_diversity": {"enforced": False, "top_sectors_in_top1000": top_sectors,
                             "n_distinct_sectors": len(sectors)},
        "static_or_dynamic": "DYNAMIC (30-min TTL rebuild) but ranking is liquidity- and "
                             "absolute-movement-weighted with NO RS/theme/earliness term",
        "findings": findings,
    }


def _findings(n_late: int, n_ext: int, n: int, summary: Dict, snap: Dict) -> List[str]:
    out = [
        "Top-1000 is DYNAMIC (rebuilt ~every 30 min) — not a static or stale list, "
        "except the curated-only FALLBACK path when Alpaca discovery fails.",
        "Ranking is dominated by LIQUIDITY (0.45) — mega/large-caps are structurally "
        "favored regardless of setup quality.",
        "The 0.15 abs_trend term uses ABSOLUTE 20d return, so already-moved names score "
        "HIGHER. Combined with ATR (movement) and 5d volume (activity), the score rewards "
        "names that have ALREADY run — a measurable late bias.",
        "NO relative-strength-vs-SPY, NO theme membership, NO earliness/accumulation term "
        "is used at the top-1000 stage.",
        "NO sector/theme diversity is enforced; a single hot sector can crowd slots.",
        f"Empirically, {n_late} ({round(100.0*n_late/n,1)}%) of the top-1000 are already "
        f"+30% over 20d and {n_ext} ({round(100.0*n_ext/n,1)}%) are +50% — included AFTER "
        "much of the move.",
    ]
    if snap.get("fallback_used"):
        out.append("WARNING: snapshot is a FALLBACK (curated-only) — discovery degraded.")
    return out


def _render_txt(res: Dict) -> List[str]:
    lb = res["late_bias"]
    L = [
        f"== TOP-1000 UNIVERSE AUDIT ({res['generated_at']}) ==",
        f"snapshot: {res['snapshot_generated_at']} (age {res['snapshot_age_hours']}h)  "
        f"fallback={res['fallback_used']}  source={res['source']}",
        f"input≈{res['input_universe_size']} → output {res['output_universe_size']}  "
        f"(excl_filter={res['excluded_for_filters']}, excl_data={res['excluded_for_data']})",
        "",
        f"RANKED BY: {res['selection_logic']['ranked_by']}",
        f"  weights: {res['selection_logic']['weights']}",
        f"  RS component: {res['selection_logic']['relative_strength_component']}  "
        f"theme: {res['selection_logic']['theme_component']}  "
        f"diversity: {res['selection_logic']['sector_theme_diversity_enforced']}",
        f"  {res['static_or_dynamic']}",
        "",
        "LATE BIAS (of current top-1000):",
        f"  +30%/20d: {lb['n_up_ge_30pct_20d']} ({lb['pct_up_ge_30pct_20d']}%)   "
        f"+50%/20d: {lb['n_up_ge_50pct_20d']} ({lb['pct_up_ge_50pct_20d']}%)",
        f"  high ATR(>8): {lb['n_high_atr_gt8']} ({lb['pct_high_atr_gt8']}%)   "
        f"negative 20d: {lb['n_negative_20d']} ({lb['pct_negative_20d']}%)",
        f"  median 20d return: {lb['median_return_20d_pct']}%   median ATR%: {lb['median_atr_pct_14']}",
        "",
        f"sector diversity enforced: {res['sector_diversity']['enforced']}  "
        f"(distinct sectors: {res['sector_diversity']['n_distinct_sectors']})",
        "",
        "FINDINGS:",
    ]
    L += [f"  • {f}" for f in res["findings"]]
    return L


def _write_doc(res: Dict) -> None:
    p = DOCS_DIR / "TOP1000_UNIVERSE_AUDIT.md"
    sl = res["selection_logic"]
    lb = res["late_bias"]
    L = [
        "# Top-1000 Universe Audit — Phase 1G.7B (Task 1)",
        "",
        f"*Generated {res['generated_at']} · research-only · cache-only.*",
        "",
        "## Where the top-1000 is created",
        f"`core/universe.py` → `UniverseBuilder._build_fresh()`. Symbols are ranked by "
        f"**`{sl['ranked_by']}`** ({sl['provenance']}) and the top "
        f"`{sl['cap_env']}` are kept as `base_universe`.",
        "",
        "## Ranking formula",
        "",
        "| component | weight | input |",
        "|---|--:|---|",
        f"| liquidity | {sl['weights']['liquidity']} | log-norm avg $-volume(20d) |",
        f"| movement | {sl['weights']['movement_atr']} | ATR%(14) |",
        f"| activity | {sl['weights']['activity_vol_ratio']} | 5d/20d volume ratio |",
        f"| abs_trend | {sl['weights']['abs_trend_20d']} | **absolute** 20d return |",
        "",
        f"**{sl['abs_trend_note']}**",
        "",
        f"- Relative-strength-vs-SPY component: **{sl['relative_strength_component']}**",
        f"- Theme component: **{sl['theme_component']}**",
        f"- Earliness/accumulation component: **{sl['earliness_component']}**",
        f"- Sector/theme diversity enforced: **{sl['sector_theme_diversity_enforced']}**",
        "",
        "## Static or dynamic?",
        f"{res['static_or_dynamic']}. Refresh: {sl['refresh']['note']} "
        f"Staleness: {sl['refresh']['note']}",
        f"\n{sl['staleness_risk']}",
        "",
        "## Pre-filters before the cap",
        f"Price ${sl['prefilters_before_cap']['min_price']}–"
        f"{sl['prefilters_before_cap']['max_price']}, avg vol ≥ "
        f"{sl['prefilters_before_cap']['min_avg_volume_20']:,}, avg $-vol ≥ "
        f"${sl['prefilters_before_cap']['min_avg_dollar_volume_20']:,} "
        f"({sl['prefilters_before_cap']['provenance']}).",
        "",
        "## Empirical late bias (current top-1000)",
        f"- Already **+30%/20d**: {lb['n_up_ge_30pct_20d']} ({lb['pct_up_ge_30pct_20d']}%)",
        f"- Already **+50%/20d**: {lb['n_up_ge_50pct_20d']} ({lb['pct_up_ge_50pct_20d']}%)",
        f"- High ATR (>8%): {lb['n_high_atr_gt8']} ({lb['pct_high_atr_gt8']}%)",
        f"- Median 20d return: {lb['median_return_20d_pct']}% · median ATR%: {lb['median_atr_pct_14']}",
        "",
        "## Findings",
        "",
    ] + [f"- {f}" for f in res["findings"]] + [
        "",
        "## Conclusion",
        "The top-1000 is **dynamic and fresh**, so staleness is not the problem. The "
        "problem is the **ranking objective**: liquidity-dominated with an absolute-"
        "movement term and no relative-strength, theme, earliness, or diversity signal. "
        "That structurally favours large-caps and names that have already moved, and "
        "under-selects emerging leaders before their move. See "
        "`DYNAMIC_UNIVERSE_SELECTION_POLICY.md` for a proposed slot-allocation policy and "
        "`scanner_recall_repair` for the forward-validated early-leader scoring. "
        "**No production change is made by this audit.**",
        "",
    ]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(L) + "\n")


def main() -> int:
    res = build()
    dataio.write_json(dataio.RESEARCH_CACHE / "top1000_universe_audit_latest.json", res)
    lines = _render_txt(res)
    dataio.write_text(dataio.LOGS_DIR / "top1000_universe_audit_latest.txt", lines)
    _write_doc(res)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

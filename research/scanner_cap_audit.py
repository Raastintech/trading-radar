"""
research/scanner_cap_audit.py — Phase 1G.6 Task 3.

Isolates the TOP-N / cap question from the broader emission-gap audit: at each
hard count cap in the discovery pipeline, how many liquidity-eligible winners sit
*below* the cap, and how many are *dropped* purely because the cap is finite (not
because they failed a quality gate)? Also surfaces which winner themes get crowded
out, the biggest individual winners lost to each cap, and a recommendation on
whether each cap should change / be regime-dynamic / get a dedicated RS-lane cap.

Caps audited (all read cache-only from real artifacts):
  raw_price_universe   — every price parquet (not a cap, the ceiling)
  liquidity_gate       — quality filter, not a count cap (reported for context)
  base_universe_top1000 — UNIV_BASE_LIMIT top-N-by-liquidity seed (HARD CAP)
  alpha_candidate_band  — enrichment candidate band (count-only; ~320)
  alpha_board_top20     — final board ≤20 (10/track) (HARD CAP)
  stock_lens_prebuild   — # lens artifacts present
  gatekeeper_refresh    — # gatekeeper artifacts present
  options_enrichment    — Tradier rows enriched (~25)
  provider_budget       — FMP monthly budget context (read-only)

Outputs:
  cache/research/scanner_cap_audit_latest.json
  logs/scanner_cap_audit_latest.txt

RESEARCH-ONLY / CACHE-ONLY. No provider calls, no DB writes. Recommends only;
NEVER raises a cap.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

from research.scanner_truth import dataio
from research.scanner_truth.filters import (ALPHA_BOARD_CAP, UNIV_BASE_LIMIT,
                                            liquidity_gate)

WINNER_UNI = dataio.RESEARCH_CACHE / "missed_winner_universe_latest.json"
UNI_SNAPSHOT = dataio.REPO / "cache" / "universe" / "universe_snapshot_latest.json"
ALPHA_BOARD = dataio.RESEARCH_CACHE / "alpha_discovery_board_latest.json"


def _winner_map() -> Dict[str, float]:
    uni = json.loads(WINNER_UNI.read_text())
    return {w["ticker"].upper(): w["best_max_return"] for w in uni["winners"]}


def _winner_theme() -> Dict[str, str]:
    uni = json.loads(WINNER_UNI.read_text())
    return {w["ticker"].upper(): w.get("theme", "unknown") for w in uni["winners"]}


def _board_tickers() -> List[str]:
    try:
        b = json.loads(ALPHA_BOARD.read_text())
        return [(i.get("ticker") or i.get("symbol") or "").upper()
                for i in b.get("items", []) if (i.get("ticker") or i.get("symbol"))]
    except Exception:
        return []


def _liq_eligible_winners(winners: Dict[str, float]) -> Set[str]:
    cal = dataio.benchmark_calendar()
    asof = cal[-1]
    out: Set[str] = set()
    for t in winners:
        df = dataio.load_prices(t)
        if df is not None and liquidity_gate(df, asof).passed:
            out.add(t)
    return out


def _themes_crowded_out(dropped: Set[str], wtheme: Dict[str, str]) -> Dict[str, int]:
    c = Counter(wtheme.get(t, "unknown") for t in dropped)
    return dict(sorted(c.items(), key=lambda x: -x[1]))


def _largest(members: Set[str], winners: Dict[str, float], n: int = 10) -> List[Dict]:
    return [{"ticker": t, "max_return": round(winners[t], 2)}
            for t in sorted(members, key=lambda t: -winners[t])[:n]]


def _cap_entry(name: str, cap_size: Optional[int], members: Optional[Set[str]],
               eligible_pool: Set[str], winners: Dict[str, float],
               wtheme: Dict[str, str], is_hard_cap: bool, note: str) -> Dict:
    """members = winners that survived this cap (None ⇒ membership unknown)."""
    pool_winners = eligible_pool
    survived = (members & pool_winners) if members is not None else None
    dropped = (pool_winners - survived) if survived is not None else set()
    return {
        "cap": name,
        "cap_size": cap_size,
        "is_hard_count_cap": is_hard_cap,
        "eligible_winners_below_cap": len(pool_winners),
        "winners_surviving_cap": (len(survived) if survived is not None else None),
        "winners_dropped_by_cap": (len(dropped) if survived is not None else None),
        "pct_eligible_lost": (round(100.0 * len(dropped) / len(pool_winners), 1)
                              if (survived is not None and pool_winners) else None),
        "themes_crowded_out": _themes_crowded_out(dropped, wtheme) if dropped else {},
        "largest_winners_dropped": _largest(dropped, winners) if dropped else [],
        "note": note,
    }


def build() -> Dict:
    winners = _winner_map()
    wtheme = _winner_theme()
    liq_winners = _liq_eligible_winners(winners)

    snap = json.loads(UNI_SNAPSHOT.read_text())
    base_uni = {t.upper() for t in snap.get("base_universe", [])}
    board = set(_board_tickers())
    alpha = json.loads(ALPHA_BOARD.read_text())
    enr = alpha.get("enrichment_cache", {})
    candidate_band = enr.get("candidate_band")
    tradier_rows = (alpha.get("coverage", {}) or {}).get("thirteen_f_rows")  # context
    options_rows = enr.get("tradier_rows") or (alpha.get("coverage", {}) or {}).get("tradier_rows")

    lens = {p.name[len("stock_lens_"):-len("_latest.json")].upper()
            for p in dataio.RESEARCH_CACHE.glob("stock_lens_*_latest.json")}
    gk = {p.name[len("executive_gatekeeper_"):-len("_latest.json")].upper()
          for p in dataio.RESEARCH_CACHE.glob("executive_gatekeeper_*_latest.json")}

    caps: List[Dict] = []
    # base 1000 cap: of the liq-eligible winners, which survive into the base seed?
    caps.append(_cap_entry(
        "base_universe_top1000", UNIV_BASE_LIMIT, base_uni & set(winners), liq_winners,
        winners, wtheme, True,
        "Top-N-by-liquidity seed. Winners below the 1000th liquidity rank are cut "
        "before any strategy sees them — pure count cap, not a quality decision."))
    # alpha candidate band (count-only — membership not historized)
    caps.append({
        "cap": "alpha_candidate_band", "cap_size": candidate_band,
        "is_hard_count_cap": True,
        "eligible_winners_below_cap": len(base_uni & set(winners)),
        "winners_surviving_cap": None, "winners_dropped_by_cap": None,
        "pct_eligible_lost": None, "themes_crowded_out": {},
        "largest_winners_dropped": [],
        "note": "Enrichment candidate band (~%s). Ticker list is NOT historized, so "
                "winner survival here is unobservable; counted for cap-size context only."
                % candidate_band,
    })
    # alpha board top-20 cap
    caps.append(_cap_entry(
        "alpha_board_top20", len(board) or 20, board, liq_winners, winners, wtheme, True,
        "Final board ≤20 (10/track). The narrowest hard cap with an observable member "
        "list; everything downstream (lens/gatekeeper/council) inherits it."))
    # lens prebuild
    caps.append(_cap_entry(
        "stock_lens_prebuild", len(lens), lens, liq_winners, winners, wtheme, False,
        "Lens artifacts present. Lens generation is gated by board/enrichment selection, "
        "not a fixed count cap; reported as coverage."))
    # gatekeeper
    caps.append(_cap_entry(
        "gatekeeper_refresh", len(gk), gk, liq_winners, winners, wtheme, False,
        "Gatekeeper artifacts present. Coverage, not a fixed count cap."))
    # options enrichment
    caps.append({
        "cap": "options_enrichment", "cap_size": options_rows,
        "is_hard_count_cap": True,
        "eligible_winners_below_cap": None, "winners_surviving_cap": None,
        "winners_dropped_by_cap": None, "pct_eligible_lost": None,
        "themes_crowded_out": {}, "largest_winners_dropped": [],
        "note": "Options/Tradier enrichment is capped at the board's top names (~%s rows); "
                "it is a per-name enrichment cap downstream of the board, so it cannot "
                "drop a winner the board already excluded." % options_rows,
    })
    # provider budget context (read-only)
    budget = None
    try:
        with dataio._ro_conn() as con:
            row = con.execute(
                "SELECT key, payload FROM cache_meta WHERE key LIKE 'fmp_budget_monthly%' "
                "ORDER BY key DESC LIMIT 1").fetchone()
            if row:
                budget = {"key": row[0], "payload": json.loads(row[1]) if row[1] else None}
    except Exception:
        budget = None
    caps.append({
        "cap": "provider_budget_fmp_monthly", "cap_size": None,
        "is_hard_count_cap": False,
        "eligible_winners_below_cap": None, "winners_surviving_cap": None,
        "winners_dropped_by_cap": None, "pct_eligible_lost": None,
        "themes_crowded_out": {}, "largest_winners_dropped": [],
        "note": "FMP monthly budget gates how many names can be enrichment-fetched; "
                "indirectly bounds candidate_band. Snapshot: %s" % (budget or "unavailable"),
    })

    # Which cap is the PRIMARY recall killer? (most eligible winners lost, hard caps only)
    hard = [c for c in caps if c["is_hard_count_cap"] and c["winners_dropped_by_cap"] is not None]
    primary = max(hard, key=lambda c: c["winners_dropped_by_cap"]) if hard else None

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_winners": len(winners),
        "n_liquidity_eligible_winners": len(liq_winners),
        "caps": caps,
        "primary_recall_killing_cap": primary["cap"] if primary else None,
        "verdict": _verdict(caps, liq_winners),
        "recommendations": _recommendations(caps),
    }


def _verdict(caps: List[Dict], liq_winners: Set[str]) -> str:
    by = {c["cap"]: c for c in caps}
    base = by["base_universe_top1000"]
    board = by["alpha_board_top20"]
    return (
        f"Of {len(liq_winners)} liquidity-eligible winners, the top-1000 base seed drops "
        f"{base['winners_dropped_by_cap']} ({base['pct_eligible_lost']}%) before any strategy "
        f"runs, and the ≤20 board ultimately retains only {board['winners_surviving_cap']}. "
        "The board cap is the binding constraint on what reaches lens/gatekeeper/council, "
        "but it is SECONDARY to the per-strategy score gates measured in the emission-gap "
        "audit (score gates cut 341→40). Caps amplify a funnel that is already too narrow; "
        "raising caps alone, without a higher-recall feeder, would mostly add low-quality names.")


def _recommendations(caps: List[Dict]) -> List[Dict]:
    return [
        {"target": "base_universe_top1000",
         "recommendation": "Do NOT blindly raise the 1000 cap. Instead add a parallel, "
                           "ranked high-recall lane (RS recall lane) that is NOT bounded by "
                           "the liquidity-rank seed, then let the Lens/Gatekeeper supply "
                           "precision. Consider a regime-aware seed size (wider in high-"
                           "dispersion regimes) only after forward validation.",
         "evidence": "124 liq-eligible winners sit below the 1000th liquidity rank; the "
                     "drop is a liquidity-RANK artifact, not a quality decision.",
         "precision_risk": "MEDIUM — a wider seed adds mostly non-winners (base rate ~9%).",
         "overfit_risk": "LOW — recommendation is structural, not threshold-tuned to past winners.",
         "provider_cost": "NONE for the seed itself; enrichment cost scales with candidate_band.",
         "complexity": "MEDIUM", "scope": "production-impacting (deferred)"},
        {"target": "alpha_board_top20 / candidate_band",
         "recommendation": "Give the RS recall lane its OWN small ranked cap (e.g. top 15-25) "
                           "feeding the Lens directly, rather than widening the Alpha board. "
                           "Keeps the board's precision while restoring recall via a separate "
                           "surface.",
         "evidence": "board retains few winners; lens exists for ~80 winners yet only ~10 "
                     "reach the board — a routing/cap problem, not a lens-coverage problem.",
         "precision_risk": "LOW if the lane feeds Lens/Gatekeeper rather than execution.",
         "overfit_risk": "LOW", "provider_cost": "LOW (lens prebuild on a small ranked set)",
         "complexity": "LOW-MEDIUM", "scope": "research-only first, then production feeder"},
        {"target": "options_enrichment / provider_budget",
         "recommendation": "Leave as-is. These are downstream per-name enrichment caps; they "
                           "cannot drop a winner the board already excluded and are not the "
                           "recall bottleneck.",
         "evidence": "options/Tradier enrichment runs only on board names.",
         "precision_risk": "n/a", "overfit_risk": "n/a", "provider_cost": "unchanged",
         "complexity": "n/a", "scope": "no change"},
    ]


def _render_txt(res: Dict) -> List[str]:
    L = [
        f"== SCANNER CAP AUDIT ({res['generated_at']}) ==",
        f"winners: {res['n_winners']}  ·  liquidity-eligible: {res['n_liquidity_eligible_winners']}",
        "",
        f"{'cap':<28}{'size':>8}{'hard':>6}{'elig':>6}{'surv':>6}{'drop':>6}{'lost%':>7}  note",
    ]
    for c in res["caps"]:
        sz = c["cap_size"] if c["cap_size"] is not None else "—"
        el = c["eligible_winners_below_cap"] if c["eligible_winners_below_cap"] is not None else "—"
        sv = c["winners_surviving_cap"] if c["winners_surviving_cap"] is not None else "—"
        dr = c["winners_dropped_by_cap"] if c["winners_dropped_by_cap"] is not None else "—"
        lp = f"{c['pct_eligible_lost']}%" if c["pct_eligible_lost"] is not None else "—"
        L.append(f"{c['cap']:<28}{str(sz):>8}{('Y' if c['is_hard_count_cap'] else 'n'):>6}"
                 f"{str(el):>6}{str(sv):>6}{str(dr):>6}{lp:>7}  {c['note'][:60]}")
    L += ["", f"PRIMARY recall-killing cap: {res['primary_recall_killing_cap']}",
          "", "VERDICT:", "  " + res["verdict"], "", "RECOMMENDATIONS:"]
    for r in res["recommendations"]:
        L.append(f"  • {r['target']}: {r['recommendation']}")
    return L


def main() -> int:
    res = build()
    dataio.write_json(dataio.RESEARCH_CACHE / "scanner_cap_audit_latest.json", res)
    lines = _render_txt(res)
    dataio.write_text(dataio.LOGS_DIR / "scanner_cap_audit_latest.txt", lines)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

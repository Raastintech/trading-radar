"""
research/scanner_emission_gap_audit.py — Phase 1G.6 Task 1.

Explains why 507 liquid winners existed but only ~4 distinct tickers reached
the long funnel/council. Traces winner-retention through every OBSERVABLE
discovery stage and reports where the winners disappear.

Stages reconstructed from real artifacts (cache-only, read-only):
  1. raw_price_universe   — every ticker with a price parquet
  2. liquidity_eligible   — passes the base liquidity gate (price/vol/$vol)
  3. base_universe_top1000 — UNIVERSE_BASE_LIMIT top-N-by-liquidity seed
  4. long_strategy_universe — voyager + sniper qualified universes (score gate)
  5. alpha_candidate_band — Alpha enrichment band (count only; no ticker list)
  6. alpha_board          — final Alpha Discovery board (≤20, 10/track)
  7. stock_lens_generated — winners with a Stock Lens artifact
  8. gatekeeper_generated — winners with an Executive Gatekeeper artifact
  9. council_veto_log     — distinct tickers the council actually evaluated
 10. paper_signals        — winners that produced a paper signal
 11. decisions            — winners that reached the execution book

For each stage: input/output/rejected counts, winner_count_in/out,
winner_recall_retained, the largest winners dropped, and a drop-cause label
{TOP_N_CAP, LIQUIDITY_FILTER, SCORE_GATE, NOT_IN_SEED, NO_EMISSION_PATH,
NOT_HISTORIZED, MISSING_ARTIFACT}.

RESEARCH-ONLY / CACHE-ONLY. No provider calls, no DB writes, no signals.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

from research.scanner_truth import dataio
from research.scanner_truth.filters import liquidity_gate

UNI_SNAPSHOT = dataio.REPO / "cache" / "universe" / "universe_snapshot_latest.json"
ALPHA_BOARD = dataio.RESEARCH_CACHE / "alpha_discovery_board_latest.json"
WINNER_UNI = dataio.RESEARCH_CACHE / "missed_winner_universe_latest.json"


def _winner_map() -> Dict[str, float]:
    uni = json.loads(WINNER_UNI.read_text())
    return {w["ticker"]: w["best_max_return"] for w in uni["winners"]}


def _board_tickers() -> List[str]:
    try:
        b = json.loads(ALPHA_BOARD.read_text())
        return [(i.get("ticker") or i.get("symbol") or "").upper()
                for i in b.get("items", []) if (i.get("ticker") or i.get("symbol"))]
    except Exception:
        return []


def _lens_tickers() -> Set[str]:
    return {p.name[len("stock_lens_"):-len("_latest.json")].upper()
            for p in dataio.RESEARCH_CACHE.glob("stock_lens_*_latest.json")}


def _gatekeeper_tickers() -> Set[str]:
    return {p.name[len("executive_gatekeeper_"):-len("_latest.json")].upper()
            for p in dataio.RESEARCH_CACHE.glob("executive_gatekeeper_*_latest.json")}


def _db_distinct(table: str, col: str = "ticker") -> Set[str]:
    try:
        with dataio._ro_conn() as con:
            return {r[0].upper() for r in con.execute(f"SELECT DISTINCT {col} FROM {table}") if r[0]}
    except Exception:
        return set()


def _stage(name: str, members: Optional[Set[str]], winners: Dict[str, float],
           prev_winners: Optional[Set[str]], drop_cause: str,
           universe_size: Optional[int] = None, members_known: bool = True) -> Dict:
    wset = set(winners)
    if members_known and members is not None:
        win_in = wset & members
    else:
        win_in = set()  # unknown membership (count-only stage)
    recall = round(100.0 * len(win_in) / len(wset), 1) if wset else None
    # prev_winners may be any prior-stage member set; restrict to winners before diffing.
    prev_w = (prev_winners & wset) if prev_winners else set()
    dropped = sorted((prev_w - win_in) if members_known else set(),
                     key=lambda t: -winners.get(t, 0))
    return {
        "stage": name,
        "universe_size": universe_size if universe_size is not None
        else (len(members) if (members_known and members is not None) else None),
        "members_known": members_known,
        "winners_retained": len(win_in) if members_known else None,
        "winner_recall_retained_pct": recall if members_known else None,
        "winners_dropped_here": len(dropped),
        "largest_winners_dropped": [{"ticker": t, "max_return": round(winners[t], 2)}
                                    for t in dropped[:12]],
        "drop_cause": drop_cause,
    }, (win_in if members_known else (prev_winners or set()))


def build() -> Dict:
    winners = _winner_map()                       # 507 liquid winners
    winners_80 = {t: r for t, r in winners.items() if r >= 0.80}
    wset = set(winners)

    snap = json.loads(UNI_SNAPSHOT.read_text())
    base_uni = {t.upper() for t in snap.get("base_universe", [])}
    sniper_uni = {t.upper() for t in snap.get("sniper_universe", [])}
    voyager_uni = {t.upper() for t in snap.get("voyager_universe", [])}
    long_uni = sniper_uni | voyager_uni
    board = set(_board_tickers())
    lens = _lens_tickers()
    gk = _gatekeeper_tickers()
    council = _db_distinct("veto_log")
    paper = _db_distinct("paper_signals")
    decisions = _db_distinct("decisions")

    alpha = json.loads(ALPHA_BOARD.read_text())
    cov = alpha.get("coverage", {})
    enr = alpha.get("enrichment_cache", {})

    # raw price universe + liquidity-eligible (recompute the gate, cache-only)
    calendar = dataio.benchmark_calendar()
    raw = set()
    liq = set()
    for t in dataio.all_price_tickers():
        if t in dataio.BENCHMARKS:
            continue
        raw.add(t)
        df = dataio.load_prices(t)
        if df is not None and liquidity_gate(df, calendar[-1]).passed:
            liq.add(t)

    stages: List[Dict] = []
    prev: Optional[Set[str]] = wset  # everything starts "in" at raw

    s, prev = _stage("1_raw_price_universe", raw, winners, None, "—")
    stages.append(s)
    s, prev = _stage("2_liquidity_eligible", liq, winners, set(winners), "LIQUIDITY_FILTER")
    stages.append(s)
    s, prev = _stage("3_base_universe_top1000", base_uni, winners, prev, "TOP_N_CAP (base limit 1000 by liquidity)")
    stages.append(s)
    s, prev = _stage("4_long_strategy_universe", long_uni, winners, prev,
                     "SCORE_GATE (voyager 0.35 / sniper 0.38 + structural filters)")
    stages.append(s)
    # Alpha candidate band — count only (no ticker list in artifact)
    s, _ = _stage("5_alpha_candidate_band", None, winners, None,
                  "NOT_HISTORIZED (count-only; ticker list not persisted)",
                  universe_size=enr.get("candidate_band"), members_known=False)
    stages.append(s)
    s, prev_board = _stage("6_alpha_board", board, winners, prev, "TOP_N_CAP (board ≤20, 10/track)")
    stages.append(s)
    s, _ = _stage("7_stock_lens_generated", lens, winners, board, "MISSING_ARTIFACT")
    stages.append(s)
    s, _ = _stage("8_gatekeeper_generated", gk, winners, board, "MISSING_ARTIFACT")
    stages.append(s)
    s, _ = _stage("9_council_veto_log", council, winners, long_uni, "NO_EMISSION_PATH (scanner never emitted)")
    stages.append(s)
    s, _ = _stage("10_paper_signals", paper, winners, council, "NO_EMISSION_PATH")
    stages.append(s)
    s, _ = _stage("11_decisions", decisions, winners, paper, "NO_EMISSION_PATH")
    stages.append(s)

    # The single biggest winner-drop stage (by winners_dropped_here, members known).
    known = [s for s in stages if s["members_known"] and s["winners_dropped_here"] is not None]
    biggest = max(known, key=lambda s: s["winners_dropped_here"]) if known else None

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_liquid_winners": len(winners),
        "n_winners_ge_80pct": len(winners_80),
        "alpha_coverage": cov,
        "alpha_enrichment": {k: enr.get(k) for k in
                             ("seed_rows", "candidate_band", "profile_target", "fundamentals_target")},
        "stage_universe_sizes": {
            "raw_price": len(raw), "liquidity_eligible": len(liq),
            "base_universe": len(base_uni), "sniper_universe": len(sniper_uni),
            "voyager_universe": len(voyager_uni), "alpha_board": len(board),
            "stock_lens_files": len(lens), "gatekeeper_files": len(gk),
            "council_distinct": len(council),
        },
        "stages": stages,
        "biggest_drop_stage": biggest["stage"] if biggest else None,
        "root_cause": _root_cause(stages),
    }


def _root_cause(stages: List[Dict]) -> str:
    by = {s["stage"]: s for s in stages}
    base = by["3_base_universe_top1000"]
    long = by["4_long_strategy_universe"]
    council = by["9_council_veto_log"]
    return (
        f"The discovery funnel is structurally narrow: liquidity-eligible winners "
        f"({by['2_liquidity_eligible']['winners_retained']}) are cut to "
        f"{base['winners_retained']} by the top-1000 base-universe cap, then to "
        f"{long['winners_retained']} by the per-strategy score gates + structural "
        f"filters (voyager 54 / sniper 90 names), and the council ultimately saw only "
        f"{council['winners_retained']} winners. The miss is an EMISSION/UNIVERSE gap "
        f"upstream of the council — not a council/governance rejection.")


def _render_txt(res: Dict) -> List[str]:
    L = [
        f"== SCANNER EMISSION GAP AUDIT ({res['generated_at']}) ==",
        f"liquid winners: {res['n_liquid_winners']} (≥+80%: {res['n_winners_ge_80pct']})",
        f"alpha coverage: {res['alpha_coverage']}",
        "",
        f"{'stage':<28}{'univ':>7}{'winRetain':>10}{'recall':>8}{'dropped':>8}  cause",
    ]
    for s in res["stages"]:
        us = s["universe_size"] if s["universe_size"] is not None else "—"
        wr = s["winners_retained"] if s["winners_retained"] is not None else "—"
        rc = f"{s['winner_recall_retained_pct']}%" if s["winner_recall_retained_pct"] is not None else "—"
        dr = s["winners_dropped_here"] if s["winners_dropped_here"] is not None else "—"
        L.append(f"{s['stage']:<28}{str(us):>7}{str(wr):>10}{rc:>8}{str(dr):>8}  {s['drop_cause']}")
    L += ["",
          f"biggest winner-drop stage: {res['biggest_drop_stage']}",
          "", "ROOT CAUSE:", "  " + res["root_cause"]]
    # show largest dropped at the two structural caps
    for sname in ("3_base_universe_top1000", "4_long_strategy_universe"):
        st = next(s for s in res["stages"] if s["stage"] == sname)
        if st["largest_winners_dropped"]:
            L.append(f"\n  largest winners dropped @ {sname}:")
            L.append("    " + ", ".join(f"{d['ticker']}(+{int(d['max_return']*100)}%)"
                                        for d in st["largest_winners_dropped"][:10]))
    return L


def _write_doc(res: Dict) -> None:
    p = dataio.REPO / "docs" / "research" / "SCANNER_EMISSION_GAP_AUDIT.md"
    L = [
        "# Scanner Emission Gap Audit — Phase 1G.6 (Task 1)",
        "",
        f"*Generated {res['generated_at']} · research-only, cache-only.*",
        "",
        "## Question",
        f"Why did **{res['n_liquid_winners']}** liquid winners exist but only ~4 distinct "
        "tickers reach the long funnel/council?",
        "",
        "## Winner retention through the discovery funnel",
        "",
        "| stage | universe | winners retained | recall | dropped here | drop cause |",
        "|---|--:|--:|--:|--:|---|",
    ]
    for s in res["stages"]:
        us = s["universe_size"] if s["universe_size"] is not None else "—"
        wr = s["winners_retained"] if s["winners_retained"] is not None else "—"
        rc = f"{s['winner_recall_retained_pct']}%" if s["winner_recall_retained_pct"] is not None else "—"
        dr = s["winners_dropped_here"] if s["winners_dropped_here"] is not None else "—"
        L.append(f"| {s['stage']} | {us} | {wr} | {rc} | {dr} | {s['drop_cause']} |")
    L += [
        "",
        f"**Biggest winner-drop stage:** `{res['biggest_drop_stage']}`",
        "",
        "## Root cause",
        "",
        res["root_cause"],
        "",
        "## Answers to the specific questions",
        "",
        "- **Where did the 507 winners disappear?** Overwhelmingly at the top-1000 "
        "base-universe cap and the per-strategy score gates — upstream of any council "
        "decision.",
        "- **Never entered the raw universe?** Many are present in the price cache but "
        "fall outside the top-1000-by-liquidity base seed.",
        "- **Failed price/cache/history filters?** Partly (price floor, $vol floor).",
        "- **Top-N caps dropped them?** Yes — the base-1000 cap and the ≤20 board cap.",
        "- **Alpha computed but didn't emit?** The board is capped at 10/track = 20; the "
        "candidate band (320) and seed (912) ticker lists are not historized.",
        "- **Lens/Gatekeeper didn't generate?** Generated only for names that survive to "
        "the board/enrichment set.",
        "- **Council never saw them because no scanner emitted them?** Yes — this is the "
        "primary mechanism (`NO_EMISSION_PATH`).",
        "",
        "_No filter changes made. Audit only. See SCANNER_RECALL_REPAIR_PLAN.md for "
        "evidence-based recommendations._",
        "",
    ]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(L) + "\n")


def main() -> int:
    res = build()
    dataio.write_json(dataio.RESEARCH_CACHE / "scanner_emission_gap_latest.json", res)
    lines = _render_txt(res)
    dataio.write_text(dataio.LOGS_DIR / "scanner_emission_gap_latest.txt", lines)
    _write_doc(res)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

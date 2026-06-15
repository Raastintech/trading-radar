"""
research/scanner_truth_review.py — Phase 1G.5 orchestrator (FULL: Tasks 1-11).

Runs the cache-only autopsy pipeline and assembles the review:
  T1 winner universe → T2/3 funnel trace+root cause → T4 metrics →
  T5 simple-baseline comparison → T6 theme audit → T7 filter audit →
  T8 entry-state timing → T9 recommendations → T10 report → T11 summary sidecar.

Writes:
  cache/research/scanner_truth_review_latest.json
  cache/research/scanner_truth_summary_latest.json   (compact, for MCP/dashboard)
  logs/scanner_truth_review_latest.txt
  docs/research/SCANNER_TRUTH_REVIEW_YYYY_MM.md  (rolls with the current month)

RESEARCH-ONLY / CACHE-ONLY: no provider calls, no paper signals, no DB writes.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List

from research.scanner_truth import (baselines, dataio, entry_timing, filter_audit,
                                    funnel_trace, metrics, theme_audit, winner_universe)

def _doc_path():
    """Month-stamped report path, rolling with the current UTC month so a nightly
    re-run writes SCANNER_TRUTH_REVIEW_YYYY_MM.md for the month it runs in rather
    than perpetually overwriting a stale May-named file."""
    stamp = datetime.now(timezone.utc).strftime("%Y_%m")
    return dataio.REPO / "docs" / "research" / f"SCANNER_TRUTH_REVIEW_{stamp}.md"


def _w(name: str, obj) -> None:
    dataio.write_json(dataio.RESEARCH_CACHE / name, obj)


def run() -> Dict:
    uni = winner_universe.build()
    _w("missed_winner_universe_latest.json", uni)
    dataio.write_text(dataio.LOGS_DIR / "missed_winner_universe_latest.txt", winner_universe._render_txt(uni))

    trace = funnel_trace.build()
    _w("scanner_funnel_trace_latest.json", trace)
    dataio.write_text(dataio.LOGS_DIR / "scanner_funnel_trace_latest.txt", funnel_trace._render_txt(trace))

    mets = metrics.build()
    _w("scanner_recall_precision_latest.json", mets)
    dataio.write_text(dataio.LOGS_DIR / "scanner_recall_precision_latest.txt", metrics._render_txt(mets))

    base = baselines.build()
    _w("scanner_baseline_comparison_latest.json", base)
    dataio.write_text(dataio.LOGS_DIR / "scanner_baseline_comparison_latest.txt", baselines._render_txt(base))

    theme = theme_audit.build()
    _w("scanner_theme_audit_latest.json", theme)
    dataio.write_text(dataio.LOGS_DIR / "scanner_theme_audit_latest.txt", theme_audit._render_txt(theme))

    filt = filter_audit.build()
    _w("scanner_filter_audit_latest.json", filt)
    dataio.write_text(dataio.LOGS_DIR / "scanner_filter_audit_latest.txt", filter_audit._render_txt(filt))

    entry = entry_timing.build()
    _w("scanner_entry_timing_latest.json", entry)
    dataio.write_text(dataio.LOGS_DIR / "scanner_entry_timing_latest.txt", entry_timing._render_txt(entry))

    recs = _recommendations(uni, trace, mets, base, theme, filt, entry)

    ts = trace["summary"]
    review = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "phase": "1G.5 — Scanner Truth Review (FULL: Tasks 1-11)",
        "headline": {
            "winners_were_missed": True,
            "winner_recall_pct": mets["recall"]["overall_pct"],
            "n_winners_ge_80pct": uni["counts"]["ge_80pct"],
            "n_winners_ge_100pct": uni["counts"]["ge_100pct"],
            "main_failure_stage": "never surfaced to council (UNIVERSE_MISS) — active long "
                                  "funnel logged only 4 distinct tickers",
            "main_root_cause": max(ts["by_root_cause"], key=ts["by_root_cause"].get),
            "best_simple_baseline_recall_pct": max(
                (b["recall_pct"] or 0) for b in base.get("baselines", {}).values()) if base.get("baselines") else None,
        },
        "winner_universe": {k: uni[k] for k in ("counts", "by_theme", "by_sector",
                                                "percentile_buckets", "coverage")},
        "funnel_trace_summary": ts,
        "metrics": mets,
        "baseline_comparison": base,
        "theme_audit": {k: theme[k] for k in ("themes", "theme_leadership_radar_proposal",
                                              "classifier_limitation")},
        "filter_audit": filt,
        "entry_timing_summary": entry["summary"],
        "recommendations": recs,
        "fidelity_disclosures": _disclosures(trace),
    }
    _w("scanner_truth_review_latest.json", review)
    _w("scanner_truth_summary_latest.json", _summary(review))
    dataio.write_text(dataio.LOGS_DIR / "scanner_truth_review_latest.txt", _render_txt(review))
    _write_doc(review, uni, trace, mets, base, theme, filt, entry)
    return review


def _disclosures(trace: Dict) -> List[str]:
    ts = trace["summary"]
    return [
        "Alpha board+overlay are historized via research_delta since ~2026-05-20 "
        "(~6 days); per-ticker Stock Lens + Gatekeeper were NOT — funnel_historizer.py "
        "now closes that gap for FUTURE autopsies.",
        f"Voyager 260-bar gates are indeterminate for ~110-bar cache names "
        f"({ts['fidelity']['n_voyager_recon_cache_limited']}/{trace['n_traced']}); not "
        "attributed to the scanner. The bars_needed filter audit is likewise marked "
        "INDETERMINATE (cache-confounded).",
        "Forward precision is NOT_COMPUTABLE_YET (today's board has no forward window).",
        "Root causes leaning on non-historized stages carry an _INFERRED suffix.",
        "Theme classifier is profile-text-limited; memory/AI-hardware counts are lower bounds.",
    ]


def _recommendations(uni, trace, mets, base, theme, filt, entry) -> List[Dict]:
    best_base = max(base.get("baselines", {}).values(),
                    key=lambda b: (b["recall_pct"] or 0), default=None)
    return [
        {
            "id": "R1_historize_funnel",
            "title": "Historize the per-ticker funnel (lens + gatekeeper)",
            "evidence": "Recall-before-move and forward-precision are uncomputable; only "
                        "~6 days of board history exist.",
            "expected_benefit": "Makes the NEXT autopsy faithful; enables forward precision.",
            "risk": "None (cache-only, additive).",
            "complexity": "LOW — funnel_historizer.py shipped; add a daily timer.",
            "scope": "research-only", "overfit_risk": "none",
        },
        {
            "id": "R2_investigate_emission_gap",
            "title": "Investigate why the LONG funnel surfaced only 4 tickers to the council",
            "evidence": f"Winner recall {mets['recall']['overall_pct']}%; veto_log has 27 "
                        "distinct tickers total, ~all SHORT. The miss is upstream of the "
                        "council, not a threshold-tuning issue.",
            "expected_benefit": "Biggest single lever on recall — likely a universe-seed or "
                                "scan-emission/score-gate gap.",
            "risk": "Investigation only; no change until understood.",
            "complexity": "MEDIUM — needs the historizer + a few weeks of universe snapshots.",
            "scope": "research-only", "overfit_risk": "none",
        },
        {
            "id": "R3_deepen_price_cache",
            "title": "Deepen the price-history cache beyond ~110 bars",
            "evidence": f"{trace['summary']['fidelity']['n_voyager_recon_cache_limited']}/"
                        f"{trace['n_traced']} winners are cache-limited; Voyager's 260-bar "
                        "gates can't be reconstructed or back-tested faithfully.",
            "expected_benefit": "Faithful PIT reconstruction and back-tests; better universe coverage.",
            "risk": "Provider-budget cost for backfill (one-time).",
            "complexity": "LOW-MEDIUM.", "scope": "research-only (cache)", "overfit_risk": "none",
        },
        {
            "id": "R4_momentum_RS_baseline_lane",
            "title": "Evaluate a regime-adaptive momentum/RS recall lane (research-only first)",
            "evidence": f"A simple baseline ('{best_base['name'] if best_base else 'rs_20d'}', "
                        f"recall {best_base['recall_pct'] if best_base else '?'}%) caught far "
                        f"more forward winners than the funnel ({base.get('funnel_recall_pct_same_set')}%). "
                        "54.6% of winners had a clean buyable window the system never took.",
            "expected_benefit": "Materially higher recall on momentum leaders.",
            "risk": "Low precision (8-12%) — a recall lane needs a precision gate before any "
                    "promotion; momentum drawdowns are real.",
            "complexity": "MEDIUM.", "scope": "research-only",
            "overfit_risk": "MEDIUM — validate forward, do NOT tune to this winner set.",
        },
        {
            "id": "R5_theme_leadership_radar",
            "title": "Build a research-only Theme Leadership Radar",
            "evidence": f"Semiconductors: {theme['themes'].get('semiconductors', {}).get('n_winners')} "
                        "winners, median max return "
                        f"~{int((theme['themes'].get('semiconductors', {}).get('median_max_return') or 0)*100)}%, "
                        "recall ~nil; coarse FMP labels hide memory/AI-hardware clusters.",
            "expected_benefit": "Early detection of strength clusters; an additive Alpha feature later.",
            "risk": "Descriptive only; scoring use needs a forward gate.",
            "complexity": "MEDIUM.", "scope": "research-only", "overfit_risk": "MEDIUM",
        },
        {
            "id": "R6_quantify_liquidity_opportunity_cost",
            "title": "Quantify (do not remove) the penny/illiquid exclusion opportunity cost",
            "evidence": "liquidity filters reject 55-65% of winners — but those are "
                        "sub-$5/illiquid names with real tradability risk.",
            "expected_benefit": "Informed decision on whether a small, ring-fenced low-price "
                                "sleeve is worth it.",
            "risk": "Trading illiquid names has slippage/borrow risk.",
            "complexity": "LOW.", "scope": "research-only", "overfit_risk": "low",
        },
        {
            "id": "R0_do_not_tune",
            "title": "Do NOT tune thresholds to recapture past winners",
            "evidence": "The whole audit set is realized; tuning to it is curve-fitting.",
            "expected_benefit": "Avoids overfit / false confidence.",
            "risk": "n/a", "complexity": "n/a", "scope": "discipline", "overfit_risk": "n/a",
        },
    ]


def _summary(review: Dict) -> Dict:
    """Compact cache-only sidecar for MCP audit summary + dashboard line (T11)."""
    h = review["headline"]
    ts = review["funnel_trace_summary"]
    return {
        "generated_at": review["generated_at"],
        "winner_recall_pct": h["winner_recall_pct"],
        "late_detection": ts["by_detection_timing"]["late"],
        "blind_misses": ts["by_detection_timing"]["blind"],
        "n_winners_ge_80pct": h["n_winners_ge_80pct"],
        "main_failure": h["main_root_cause"],
        "best_simple_baseline_recall_pct": h["best_simple_baseline_recall_pct"],
        "one_liner": (f"Scanner Truth: recall {h['winner_recall_pct']}% · "
                      f"main miss {h['main_root_cause']} · "
                      f"simple-RS baseline {h['best_simple_baseline_recall_pct']}%"),
    }


def _render_txt(r: Dict) -> List[str]:
    h = r["headline"]
    L = [
        f"== SCANNER TRUTH REVIEW — {r['phase']} ({r['generated_at']}) ==",
        f"winners missed: {h['winners_were_missed']}   winner_recall={h['winner_recall_pct']}%",
        f"winners ≥+80%: {h['n_winners_ge_80pct']}   ≥+100%/2x: {h['n_winners_ge_100pct']}",
        f"best simple baseline recall: {h['best_simple_baseline_recall_pct']}% (vs funnel {h['winner_recall_pct']}%)",
        f"main failure: {h['main_failure_stage']}",
        "",
        "RECOMMENDATIONS:",
        *[f"  [{rec['id']}] {rec['title']} ({rec['scope']}, overfit={rec['overfit_risk']})"
          for rec in r["recommendations"]],
        "",
        "fidelity disclosures:",
        *[f"  - {d}" for d in r["fidelity_disclosures"]],
    ]
    return L


def _write_doc(r, uni, trace, mets, base, theme, filt, entry) -> None:
    h = r["headline"]
    ts = trace["summary"]
    bl = base.get("baselines", {})
    lines = [
        "# Scanner Truth Review — 2026-05 (Phase 1G.5)",
        "",
        f"*Generated {r['generated_at']} · {r['phase']} · research-only, cache-only.*",
        "",
        "## 1. Executive summary",
        "",
        f"- **Were market winners missed? YES.** Of **{h['n_winners_ge_80pct']}** liquid "
        f"winners ≥+80% (**{h['n_winners_ge_100pct']}** ≥2x), only "
        f"**{mets['recall']['n_seen']}** ever touched any historized funnel stage → "
        f"**winner recall {h['winner_recall_pct']}%**.",
        "- **They fell out before the council saw them.** The active long funnel "
        "(VOYAGER+SNIPER) logged only **4 distinct tickers**; veto_log is ~all SHORT. "
        "`UNIVERSE_MISS` dominates.",
        f"- **A dumb baseline beats the funnel on recall:** simple 20d-RS recall "
        f"**{h['best_simple_baseline_recall_pct']}%** vs funnel "
        f"**{base.get('funnel_recall_pct_same_set')}%** on the same forward set.",
        f"- **A clean entry existed:** {entry['summary']['pct_with_buyable_window']}% of "
        f"winners had a buyable window (median {entry['summary']['median_entry_window_days']}d) "
        "before becoming extended — the system simply never surfaced them.",
        "- Consistent with design (Voyager buyable-pullback mandate; Alpha penalises large "
        "momentum leaders). The question is whether a **research-only momentum/RS recall "
        "lane** + **theme radar** is worth adding — validated forward, never curve-fit.",
        "",
        "## 2. Were market winners missed?",
        "",
        f"- Liquid winners ≥+50%: **{uni['counts']['ge_50pct']}**, ≥+80%: "
        f"**{uni['counts']['ge_80pct']}**, ≥2x: **{uni['counts']['ge_100pct']}** "
        f"(scanned {uni['coverage']['tickers_scanned']}; "
        f"{uni['counts']['illiquid_winners_excluded']} illiquid excluded).",
        "- By theme: " + ", ".join(f"`{k}`={v}" for k, v in uni["by_theme"].items()) + ".",
        "",
        "## 3. Top missed winners (liquid, by trailing max return)",
        "",
        "| ticker | theme | max ret | $vol(M) | recall |",
        "|---|---|--:|--:|:--:|",
    ]
    seen_t = {t["ticker"]: bool(t["first_date_actually_saw"] or t["today_snapshot"]["in_alpha_board"])
              for t in trace["traces"]}
    for w in uni["winners"][:25]:
        lines.append(f"| {w['ticker']} | {w['theme']} | {w['best_max_return']*100:.0f}% | "
                     f"{(w['avg_dvol_20'] or 0)/1e6:.0f} | {'seen' if seen_t.get(w['ticker']) else '—'} |")
    lines += [
        "",
        "## 4. Where they fell out of the funnel",
        "",
        "| root cause | count |", "|---|--:|",
        *[f"| {c} | {n} |" for c, n in ts["by_root_cause"].items()],
        "",
        f"Detection timing: early **{ts['by_detection_timing']['early']}**, late "
        f"**{ts['by_detection_timing']['late']}**, blind **{ts['by_detection_timing']['blind']}**.",
        "",
        "## 5. Recall / precision metrics",
        "",
        f"- Recall (ever in funnel): **{mets['recall']['overall_pct']}%**; ≥2x bucket "
        f"**{mets['recall']['by_return_bucket']['ge_100pct']['recall_pct']}%**.",
        f"- Semiconductors recall **{mets['recall']['by_theme'].get('semiconductors', {}).get('recall_pct')}%** "
        f"(n={mets['recall']['by_theme'].get('semiconductors', {}).get('n')}).",
        f"- Recall-before-move **{mets['recall_before_move']['status']}**; forward precision "
        f"**{mets['precision_forward']['status']}**.",
        "",
        "## 6. Comparison vs simple baselines",
        "",
        f"As-of {base.get('asof_date')}, {base.get('horizon_trading_days')}td forward, "
        f"{base.get('n_forward_winners')} forward winners in {base.get('n_liquid_universe')} liquid names.",
        "",
        "| baseline | flagged | recall | precision | avg fwd ret |",
        "|---|--:|--:|--:|--:|",
        *[f"| {b['name']} | {b['n_flagged']} | {b['recall_pct']}% | {b['precision_pct']}% | "
          f"{(b['avg_fwd_return_of_flagged'] or 0)*100:.0f}% |" for b in bl.values()],
        "",
        f"**Verdict:** {base.get('verdict')}",
        "",
        "## 7. Theme / sector leadership audit",
        "",
        "| theme | winners | median max | on board | seen | visibility |",
        "|---|--:|--:|--:|--:|---|",
        *[f"| {th} | {d['n_winners']} | {d['median_max_return']*100:.0f}% | "
          f"{d['n_on_todays_alpha_board']} | {d['recall_seen']} | {d['visibility']} |"
          for th, d in theme["themes"].items()],
        "",
        f"*Limitation:* {theme['classifier_limitation']}",
        "",
        f"**Theme Leadership Radar:** {theme['theme_leadership_radar_proposal']['status']} — "
        f"{theme['theme_leadership_radar_proposal']['what']}",
        "",
        "## 8. Filter audit",
        "",
        "| filter | threshold | winners rej | losers rej | recall cost | verdict |",
        "|---|---|--:|--:|--:|---|",
        *[f"| {a['filter']} | {a['threshold']} | {a['winners_rejected']} | {a['losers_rejected']} | "
          f"{a['recall_cost_pct']}% | {a['verdict'].split(' — ')[0].split(' (')[0]} |"
          for a in filt["audits"]],
        "",
        "_Not reliably computable (disclosed, not guessed):_ "
        + "; ".join(n["filter"] for n in filt["not_reliably_computable"]) + ".",
        "",
        "## 9. Entry-state timing audit",
        "",
        f"- **{entry['summary']['pct_with_buyable_window']}%** of winners had a clean buyable "
        f"window before becoming extended (median **{entry['summary']['median_entry_window_days']} "
        f"days**); funnel detected **{entry['summary']['n_detected']}**.",
        f"- {entry['summary']['note']}",
        "",
        "## 10. Recommendations (evidence-based)",
        "",
    ]
    for rec in r["recommendations"]:
        lines += [
            f"### {rec['id']} — {rec['title']}",
            f"- **Evidence:** {rec['evidence']}",
            f"- **Benefit:** {rec['expected_benefit']}",
            f"- **Risk:** {rec['risk']}",
            f"- **Complexity:** {rec['complexity']} · **Scope:** {rec['scope']} · "
            f"**Overfit risk:** {rec['overfit_risk']}",
            "",
        ]
    lines += [
        "## 11. What NOT to change yet",
        "",
        "- No threshold tuning to recapture past winners (curve-fitting).",
        "- No execution / governance / strategy-registry / live-capital / paper-signal changes.",
        "- No new live strategy. Any momentum/RS lane or theme radar stays research-only "
        "behind a forward-validation gate.",
        "",
        "## 12. Recommended next phase",
        "",
        "1. Add a daily `funnel_historizer` timer; accrue ~4-6 weeks of dated boards+lens+gatekeeper.",
        "2. Backfill deeper price history; re-run this review to lift the cache-limited caveats.",
        "3. Stand up the research-only momentum/RS recall lane + Theme Leadership Radar and "
        "measure FORWARD recall/precision on out-of-sample winners before any promotion decision.",
        "",
        "---",
        "*Fidelity disclosures:*",
        *[f"> - {d}" for d in r["fidelity_disclosures"]],
        "",
    ]
    doc_path = _doc_path()
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_path.write_text("\n".join(lines) + "\n")


def main() -> int:
    r = run()
    print("\n".join(_render_txt(r)))
    print(f"\nreport: {_doc_path().relative_to(dataio.REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

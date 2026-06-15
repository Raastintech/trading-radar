"""
research/scanner_recall_repair.py — Phase 1G.6/1G.7 orchestrator.

One cache-only command that runs the full Scanner Recall Repair research package
in order and writes a single aggregated summary sidecar:

  1. scanner_emission_gap_audit     — where winners drop through the funnel
  2. rs_recall_lane (--historize)   — high-recall lane + appends forward evidence
  3. theme_leadership_radar (--historize) — live theme leadership + forward hist
  4. rs_recall_forward_validation   — out-of-sample gate for the lane (1G.7)
  5. scanner_cap_audit              — which count caps drop winners
  6. price_cache_coverage_audit     — is cache deep enough for the gates (1G.7 buckets)
  7. price_cache_deepening_plan     — cache-only deepening plan (no provider calls)

Outputs:
  cache/research/scanner_recall_repair_latest.json
  logs/scanner_recall_repair_latest.txt

RESEARCH-ONLY / CACHE-ONLY. No provider calls, no DB writes, no signals, no
timer registration. Re-scans the price cache a few times (each sub-report is
independent), so it is an operator-invoked command, not a hot-path job.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Dict, List

from research import (price_cache_coverage_audit, price_cache_deepening_plan,
                      rs_recall_forward_validation, rs_recall_lane,
                      scanner_cap_audit, scanner_emission_gap_audit,
                      theme_leadership_radar, top1000_universe_audit,
                      universe_dynamic_selection, universe_forward_replay)
from research.scanner_truth import dataio

RC = dataio.RESEARCH_CACHE


def _load(name: str) -> Dict:
    try:
        return json.loads((RC / name).read_text())
    except Exception as e:  # pragma: no cover - defensive
        return {"error": f"{name}: {e}"}


def build() -> Dict:
    # Run each sub-report (each writes its own sidecar + txt). Order matters:
    # rs_recall_lane (with --historize) before theme_leadership_radar so the radar
    # reads the lane's coverage; both before forward-validation so it sees the
    # freshest history. The lane/theme historize idempotently (per as-of date).
    steps = [
        ("emission_gap", scanner_emission_gap_audit.main),
        ("rs_recall_lane", lambda: rs_recall_lane.main(["--historize"])),
        ("theme_leadership", lambda: theme_leadership_radar.main(["--historize"])),
        ("rs_forward_validation", rs_recall_forward_validation.main),
        ("cap_audit", scanner_cap_audit.main),
        ("price_cache_coverage", price_cache_coverage_audit.main),
        ("deepening_plan", price_cache_deepening_plan.main),
        ("top1000_audit", top1000_universe_audit.main),
        ("universe_dynamic", lambda: universe_dynamic_selection.main(["--historize"])),
        ("universe_forward_replay", universe_forward_replay.main),
    ]
    run_status: Dict[str, str] = {}
    for name, fn in steps:
        try:
            fn()
            run_status[name] = "ok"
        except Exception as e:  # pragma: no cover - defensive
            run_status[name] = f"error: {e}"

    egap = _load("scanner_emission_gap_latest.json")
    rsl = _load("rs_recall_lane_latest.json")
    theme = _load("theme_leadership_latest.json")
    cap = _load("scanner_cap_audit_latest.json")
    cov = _load("price_cache_coverage_latest.json")
    fwd = _load("rs_recall_forward_validation_latest.json")
    plan = _load("price_cache_deepening_plan_latest.json")
    uaudit = _load("top1000_universe_audit_latest.json")
    udyn = _load("universe_dynamic_selection_latest.json")
    uab = _load("universe_forward_replay_latest.json")

    bt = rsl.get("backtest", {})
    leading = theme.get("leading_themes", [])
    fwd_5d = (fwd.get("by_horizon", {}) or {}).get("5d", {})
    summary = {
        "system_recall_pct": _truth_recall(),
        "rs_lane_recall_pct": bt.get("recall_pct"),
        "rs_lane_random_control_pct": bt.get("random_control_recall_pct"),
        "rs_lane_precision_pct": bt.get("precision_pct"),
        "rs_lane_has_edge_over_random": (
            bt.get("recall_pct") is not None
            and bt.get("random_control_recall_pct") is not None
            and bt["recall_pct"] > bt["random_control_recall_pct"]),
        "emission_gap_biggest_drop_stage": egap.get("biggest_drop_stage"),
        "primary_recall_killing_cap": cap.get("primary_recall_killing_cap"),
        "leading_themes": leading,
        "leading_themes_on_alpha_board": [
            t for t in leading
            if theme.get("themes", {}).get(t, {}).get("covered_by_alpha_board")],
        "cache_uniformly_shallow": cov.get("cache_uniformly_shallow"),
        "median_cache_bars": cov.get("median_bars_universe"),
        "rs_forward_verdict": fwd.get("verdict"),
        "rs_forward_history_rows": fwd.get("history_rows"),
        "rs_forward_history_dates": len(fwd.get("history_dates", [])),
        "deepening_needed": plan.get("n_needing_deepen"),
        "unreliable_filters": cov.get("unreliable_filters_due_to_depth"),
        "universe_static_or_dynamic": uaudit.get("static_or_dynamic"),
        "universe_late_pct": (uaudit.get("late_bias") or {}).get("pct_up_ge_30pct_20d"),
        "universe_current_late_stage_pct": (udyn.get("current_top1000") or {}).get("late_pct"),
        "universe_current_early_pct": (udyn.get("current_top1000") or {}).get("early_pct"),
        "universe_proposed_early_pct": (udyn.get("proposed_universe") or {}).get("early_pct"),
        "universe_new_entrants": (udyn.get("comparison") or {}).get("added"),
        "universe_ab_verdict": uab.get("verdict"),
        "universe_ab_promotion": (uab.get("promotion_gates") or {}).get("status"),
        "universe_ab_shadow_dates": uab.get("n_shadow_dates"),
    }
    summary["action_needed"] = _action(summary)

    # Pre-rendered surfacing hooks (Task 7).  Doctrine: prefer CLI + JSON sidecars
    # and wire the dashboard/MCP only after the report stabilizes.  These strings
    # let a future MCP-audit summary or a single dashboard line read this sidecar
    # with ZERO computation and remain cache-only.
    lt = ",".join(leading) or "none"
    gap = (summary["emission_gap_biggest_drop_stage"] or "?").split("_", 1)[-1]
    summary["mcp_summary_block"] = {
        "system_recall": f"{summary['system_recall_pct']}%",
        "rs_recall": f"{summary['rs_lane_recall_pct']}%",
        "emission_gap_stage": summary["emission_gap_biggest_drop_stage"],
        "leading_theme": lt,
        "action_needed": summary["action_needed"],
    }
    # Phase 1G.7: RS FORWARD block for a future MCP "RS FORWARD" panel.
    summary["rs_forward_block"] = {
        "state": summary["rs_forward_verdict"],
        "top15_n": (fwd_5d.get("rs_top15") or {}).get("matured_groups"),
        "5d_net": (fwd_5d.get("rs_top15") or {}).get("mean_rel_spy"),
        "vs_random": (fwd_5d.get("random_control") or {}).get("mean_rel_spy"),
        "vs_alpha": (fwd_5d.get("alpha_board") or {}).get("mean_rel_spy"),
        "history_rows": summary["rs_forward_history_rows"],
    }
    theme_lead = (leading[0] if leading else "none")
    summary["dashboard_line"] = (
        f"Scanner Recall: system {summary['system_recall_pct']}% · "
        f"RS {summary['rs_lane_recall_pct']}% · gap={gap} · leading={lt}")
    # Phase 1G.7 compact RS-lane line requested for the dashboard hook.
    summary["dashboard_line_rs"] = (
        f"RS Lane: hist={summary['rs_forward_history_rows']} · "
        f"verdict={summary['rs_forward_verdict']} · theme={theme_lead} Leading")

    # Phase 1G.7B: UNIVERSE SELECTION block (Task 8). main_issue is code-derived:
    # the ranking has no RS/theme/earliness term (theme_blind) and a liquidity +
    # absolute-movement bias; secondary = late_bias if many names already ran.
    sl = uaudit.get("selection_logic", {}) or {}
    no_rs_theme = not (sl.get("relative_strength_component") or sl.get("theme_component")
                       or sl.get("earliness_component"))
    late_now = summary.get("universe_current_late_stage_pct") or 0
    main_issue = ("theme_blind" if no_rs_theme
                  else ("late_bias" if late_now and late_now >= 20 else "score_gate"))
    semis = (udyn.get("theme_coverage", {}) or {}).get("semiconductors", {})
    summary["universe_selection_block"] = {
        "current_top1000_late_pct": summary["universe_current_late_stage_pct"],
        "early_leader_count": (udyn.get("proposed_universe") or {}).get("early_accumulation_emerging"),
        "new_entrants": summary["universe_new_entrants"],
        "main_issue": main_issue,
    }
    summary["dashboard_line_universe"] = (
        f"Universe: early={summary.get('universe_proposed_early_pct')}% · "
        f"late={summary.get('universe_current_late_stage_pct')}% · "
        f"semis covered={semis.get('proposed', '?')} · "
        f"new entrants={summary.get('universe_new_entrants')}")

    # Phase 1G.8: UNIVERSE A/B block (Task 6). Forward A/B of production vs proposed.
    rlf = uab.get("recall_late_fp", {}) or {}
    prod_recall = (rlf.get("production") or {}).get("recall_before_20")
    prop_recall = (rlf.get("proposed_dynamic") or {}).get("recall_before_20")
    summary["universe_ab_block"] = {
        "production_recall": prod_recall,
        "proposed_recall": prop_recall,
        "verdict": summary["universe_ab_verdict"],
        "promotion": summary["universe_ab_promotion"],
        "shadow_dates": summary["universe_ab_shadow_dates"],
    }
    summary["dashboard_line_universe_ab"] = (
        f"Universe A/B: prod={prod_recall if prod_recall is not None else '—'} · "
        f"proposed={prop_recall if prop_recall is not None else '—'} · "
        f"verdict={summary['universe_ab_verdict']}")

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "disclaimer": "RESEARCH-ONLY scanner recall repair package. No signals, no trades.",
        "run_status": run_status,
        "summary": summary,
        "emission_gap": {k: egap.get(k) for k in
                         ("n_liquid_winners", "biggest_drop_stage", "root_cause",
                          "stage_universe_sizes")},
        "rs_recall_lane_backtest": bt,
        "rs_forward_validation": {
            "verdict": fwd.get("verdict"), "verdict_reason": fwd.get("verdict_reason"),
            "history_rows": fwd.get("history_rows"),
            "history_dates": fwd.get("history_dates")},
        "cap_audit_verdict": cap.get("verdict"),
        "price_cache_verdict": cov.get("verdict"),
        "price_cache_deepening": {
            "n_needing_deepen": plan.get("n_needing_deepen"),
            "estimated_provider_calls": plan.get("estimated_provider_calls"),
            "unreliable_filters": cov.get("unreliable_filters_due_to_depth")},
        "universe_selection": {
            "static_or_dynamic": uaudit.get("static_or_dynamic"),
            "audit_late_bias": uaudit.get("late_bias"),
            "current_vs_proposed": udyn.get("comparison"),
            "current_stage_distribution": (udyn.get("current_top1000") or {}).get("stage_distribution"),
            "proposed_stage_distribution": (udyn.get("proposed_universe") or {}).get("stage_distribution"),
            "theme_coverage": udyn.get("theme_coverage"),
            "missed_winner_replay": udyn.get("missed_winner_replay")},
        "universe_forward_ab": {
            "verdict": uab.get("verdict"),
            "promotion_gates": uab.get("promotion_gates"),
            "n_shadow_dates": uab.get("n_shadow_dates"),
            "ticker_days": uab.get("ticker_days"),
            "recall_late_fp": uab.get("recall_late_fp"),
            "score_gate_audit": uab.get("score_gate_audit")},
        "theme_leadership_states": {
            t: d.get("theme_state") for t, d in (theme.get("themes") or {}).items()},
    }


def _truth_recall() -> float:
    s = _load("scanner_truth_summary_latest.json")
    return s.get("winner_recall_pct")


def _action(s: Dict) -> str:
    bits: List[str] = []
    if s.get("leading_themes") and not s.get("leading_themes_on_alpha_board"):
        bits.append(f"leading theme(s) {','.join(s['leading_themes'])} absent from Alpha board")
    if s.get("rs_lane_recall_pct") and s.get("system_recall_pct") \
            and s["rs_lane_recall_pct"] > 3 * s["system_recall_pct"]:
        bits.append("simple RS recall >> system recall — funnel is structurally too narrow")
    if s.get("rs_lane_has_edge_over_random") is False:
        bits.append("RS lane has NO precision edge over random at current width — needs a ranked cap, not a wide net")
    if s.get("cache_uniformly_shallow"):
        bits.append("price cache too shallow for 200d/260-bar gates — deepen before trusting those gates")
    ub = s.get("universe_selection_block") or {}
    if ub.get("main_issue") == "theme_blind":
        bits.append("top-1000 ranking has NO relative-strength/theme/earliness term "
                    "(liquidity + absolute-movement biased) — proposed dynamic policy "
                    "raises early-stage coverage (research-only)")
    return "; ".join(bits) if bits else "no structural action flagged"


def _render_txt(res: Dict) -> List[str]:
    s = res["summary"]
    L = [
        f"== SCANNER RECALL REPAIR — AGGREGATE ({res['generated_at']}) ==",
        res["disclaimer"],
        "",
        "run status: " + ", ".join(f"{k}={v}" for k, v in res["run_status"].items()),
        "",
        f"system recall:        {s['system_recall_pct']}%",
        f"RS lane recall:       {s['rs_lane_recall_pct']}%  "
        f"(random control {s['rs_lane_random_control_pct']}%, precision {s['rs_lane_precision_pct']}%)",
        f"RS lane > random?     {s['rs_lane_has_edge_over_random']}",
        f"biggest emission drop: {s['emission_gap_biggest_drop_stage']}",
        f"primary cap:          {s['primary_recall_killing_cap']}",
        f"leading themes:       {', '.join(s['leading_themes']) or '(none)'}",
        f"  on alpha board:     {', '.join(s['leading_themes_on_alpha_board']) or '(none)'}",
        f"cache shallow?        {s['cache_uniformly_shallow']} (median {s['median_cache_bars']} bars)",
        f"unreliable filters:   {', '.join(s.get('unreliable_filters') or []) or '(none)'}",
        f"deepening needed:     {s.get('deepening_needed')} priority tickers",
        f"RS forward verdict:   {s.get('rs_forward_verdict')} "
        f"(history {s.get('rs_forward_history_rows')} rows / {s.get('rs_forward_history_dates')} date(s))",
        f"universe:             {s.get('universe_static_or_dynamic')}",
        f"  current late={s.get('universe_current_late_stage_pct')}% "
        f"early={s.get('universe_current_early_pct')}% → proposed early="
        f"{s.get('universe_proposed_early_pct')}%; new entrants={s.get('universe_new_entrants')}; "
        f"issue={(s.get('universe_selection_block') or {}).get('main_issue')}",
        f"universe A/B:          verdict={s.get('universe_ab_verdict')} "
        f"promotion={s.get('universe_ab_promotion')} (shadow dates={s.get('universe_ab_shadow_dates')})",
        "",
        "ACTION NEEDED:",
        "  " + s["action_needed"],
        "",
        "surfacing hooks:",
        "  " + s["dashboard_line"],
        "  " + s["dashboard_line_rs"],
        "  " + s["dashboard_line_universe"],
        "  " + s["dashboard_line_universe_ab"],
    ]
    return L


def main() -> int:
    res = build()
    dataio.write_json(RC / "scanner_recall_repair_latest.json", res)
    lines = _render_txt(res)
    dataio.write_text(dataio.LOGS_DIR / "scanner_recall_repair_latest.txt", lines)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

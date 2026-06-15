"""
research/universe_forward_replay.py — Phase 1G.8 Tasks 2-5.

Forward A/B test of the PRODUCTION top-1000 vs the PROPOSED dynamic universe,
using the dual-version shadow ledger (data/research/universe_selection_history
.jsonl). Each shadow snapshot is scored only once enough forward bars mature
(bars strictly AFTER the snapshot's as-of date — no look-ahead).

It emits:
  T2. forward A/B metrics per window (1/3/5/10/20d) + a gated verdict
      {PRODUCTION_BETTER, PROPOSED_BETTER, NO_DIFFERENCE, NEED_MORE_DATA}
  T3. a universe-quality report (composition, theme/early/late coverage)
  T4. strict promotion gates that must pass before core/universe.py is touched
  T5. a score-gate interaction audit (do current structural gates kill the
      proposed early leaders?)

Outputs:
  cache/research/universe_forward_replay_latest.json + logs/...txt
  cache/research/universe_quality_latest.json        + logs/...txt
  docs/research/UNIVERSE_FORWARD_REPLAY_RESULTS.md

RESEARCH-ONLY / CACHE-ONLY. No provider calls, no DB writes, no signals, and it
NEVER modifies core/universe.py or the production universe.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from statistics import mean
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from research.scanner_truth import dataio
from research.scanner_truth.filters import sniper_breakout, voyager_structural
from research.universe_dynamic_selection import (HISTORY_PATH, PROPOSED_VERSION,
                                                 PRODUCTION_VERSION, _aligned)

WINNER_UNI = dataio.RESEARCH_CACHE / "missed_winner_universe_latest.json"
DOCS_DIR = dataio.REPO / "docs" / "research"
HORIZONS = (1, 3, 5, 10, 20)
RECALL_LEVELS = (0.20, 0.30, 0.50)
LATE_STAGES = {"LATE_EXTENDED", "PARABOLIC"}
EARLY_STAGES = {"EARLY_ACCUMULATION", "EMERGING_MOMENTUM"}

# Promotion-gate thresholds (Task 4)
GATE_MIN_DAYS = 10
GATE_MIN_TICKER_DAYS = 3000
GATE_RECALL_MARGIN = 0.03          # +3pp early-winner recall to count as meaningful
GATE_FP_TOLERANCE = 0.02           # proposed FP may not exceed production by >2pp
GATE_MAX_SECTOR_SHARE = 0.35       # one-sector cap unless theme leadership confirmed


def _winners() -> Dict[str, float]:
    try:
        uni = json.loads(WINNER_UNI.read_text())
        return {w["ticker"].upper(): w["best_max_return"] for w in uni.get("winners", [])}
    except Exception:
        return {}


def _cal_index(cal, asof: str) -> Optional[int]:
    locs = np.where(cal <= pd.Timestamp(asof))[0]
    return int(locs[-1]) if len(locs) else None


def _fwd(t: str, cal, i: int, h: int, spy: pd.Series) -> Optional[Dict]:
    if i + h >= len(cal):
        return None
    df = dataio.load_prices(t)
    if df is None:
        return None
    c = _aligned(df, cal)
    p0 = c.iloc[i]
    if pd.isna(p0) or p0 <= 0 or pd.isna(c.iloc[i + h]):
        return None
    fwd = float(c.iloc[i + h] / p0 - 1.0)
    fwd_max = float(np.nanmax(c.iloc[i:i + h + 1].values) / p0 - 1.0)
    spy_ret = float(spy.iloc[i + h] / spy.iloc[i] - 1.0) if spy.iloc[i] else 0.0
    return {"fwd": fwd, "rel_spy": fwd - spy_ret, "fwd_max": fwd_max}


def _load_ledger() -> Dict[str, Dict[str, List[Dict]]]:
    """{asof_date: {version: [rows...]}} for versioned rows only."""
    out: Dict[str, Dict[str, List[Dict]]] = defaultdict(lambda: defaultdict(list))
    for r in dataio.read_jsonl(HISTORY_PATH):
        v = r.get("universe_version")
        if v and r.get("included"):
            out[r["asof_date"]][v].append(r)
    return out


# ── Task 2: forward A/B ──────────────────────────────────────────────────────

def _ab(ledger, cal, spy, winners) -> Dict:
    per_h: Dict[str, Dict] = {}
    ticker_days = {PRODUCTION_VERSION: 0, PROPOSED_VERSION: 0}
    matured_days = 0
    for h in HORIZONS:
        agg = {PRODUCTION_VERSION: defaultdict(list), PROPOSED_VERSION: defaultdict(list)}
        n_dates = 0
        for asof in sorted(ledger):
            i = _cal_index(cal, asof)
            if i is None or i + h >= len(cal):
                continue
            n_dates += 1
            # union forward returns at this date (for top-decile percentile)
            for ver in (PRODUCTION_VERSION, PROPOSED_VERSION):
                rels, fwds, maxes, stages, secs = [], [], [], [], []
                w_incl = 0
                for row in ledger[asof][ver]:
                    m = _fwd(row["ticker"], cal, i, h, spy)
                    if m is None:
                        continue
                    rels.append(m["rel_spy"]); fwds.append(m["fwd"])
                    maxes.append((row["ticker"], m["fwd_max"]))
                    stages.append(row.get("stage_label"))
                    secs.append(row.get("sector") or "UNKNOWN")
                    if row["ticker"] in winners:
                        w_incl += 1
                if not fwds:
                    continue
                thr90 = float(np.percentile(fwds, 90))
                thr95 = float(np.percentile(fwds, 95))
                a = agg[ver]
                a["mean_rel_spy"].append(mean(rels))
                a["mean_fwd"].append(mean(fwds))
                a["win_rate"].append(100.0 * sum(1 for r in rels if r > 0) / len(rels))
                a["top_decile_hit"].append(100.0 * sum(1 for f in fwds if f >= thr90) / len(fwds))
                a["top5pct_hit"].append(100.0 * sum(1 for f in fwds if f >= thr95) / len(fwds))
                a["late_repr"].append(100.0 * sum(1 for s in stages if s in LATE_STAGES) / len(stages))
                a["early_repr"].append(100.0 * sum(1 for s in stages if s in EARLY_STAGES) / len(stages))
                a["max_sector_share"].append(100.0 * max(Counter(secs).values()) / len(secs))
                if h == HORIZONS[0]:
                    ticker_days[ver] += len(fwds)

        def _m(ver, key):
            v = agg[ver][key]
            return round(mean(v), 3) if v else None
        keys = ("mean_rel_spy", "mean_fwd", "win_rate", "top_decile_hit",
                "top5pct_hit", "late_repr", "early_repr", "max_sector_share")
        entry = {"matured_dates": n_dates}
        for ver in (PRODUCTION_VERSION, PROPOSED_VERSION):
            entry[ver] = {k: _m(ver, k) for k in keys}
        per_h[f"{h}d"] = entry
        matured_days = max(matured_days, n_dates)
    return {"by_horizon": per_h, "ticker_days": ticker_days, "matured_days": matured_days}


def _recall(ledger, cal, spy, winners) -> Dict:
    """Per version: winner recall before +20/+30/+50, late-detection, blind-miss,
    false-positive — averaged over matured dates (20d window)."""
    h = 20
    out = {PRODUCTION_VERSION: defaultdict(list), PROPOSED_VERSION: defaultdict(list)}
    for asof in sorted(ledger):
        i = _cal_index(cal, asof)
        if i is None or i + h >= len(cal):
            continue
        # forward-max for every winner from this asof
        wmax = {}
        for t in winners:
            m = _fwd(t, cal, i, h, spy)
            if m is not None:
                wmax[t] = m["fwd_max"]
        for ver in (PRODUCTION_VERSION, PROPOSED_VERSION):
            incl = {r["ticker"] for r in ledger[asof][ver]}
            stage = {r["ticker"]: r.get("stage_label") for r in ledger[asof][ver]}
            for lvl in RECALL_LEVELS:
                reached = {t for t, mx in wmax.items() if mx >= lvl}
                caught = reached & incl
                if reached:
                    out[ver][f"recall_before_{int(lvl*100)}"].append(100.0 * len(caught) / len(reached))
            # late-detection: of caught winners, share already LATE/PARABOLIC at asof
            caught_w = set(wmax) & incl
            if caught_w:
                out[ver]["late_detection_rate"].append(
                    100.0 * sum(1 for t in caught_w if stage.get(t) in LATE_STAGES) / len(caught_w))
            # blind-miss: winners reaching +20% not included
            reached20 = {t for t, mx in wmax.items() if mx >= 0.20}
            if reached20:
                out[ver]["blind_miss_rate"].append(100.0 * len(reached20 - incl) / len(reached20))
            # false-positive cost: included names with negative 20d forward
            fps, n = 0, 0
            for t in incl:
                m = _fwd(t, cal, i, h, spy)
                if m is not None:
                    n += 1
                    if m["fwd"] < 0:
                        fps += 1
            if n:
                out[ver]["false_positive_pct"].append(100.0 * fps / n)

    def _agg(ver):
        return {k: round(mean(v), 1) for k, v in out[ver].items() if v}
    return {ver: _agg(ver) for ver in (PRODUCTION_VERSION, PROPOSED_VERSION)}


def _turnover(ledger) -> Dict:
    out = {}
    for ver in (PRODUCTION_VERSION, PROPOSED_VERSION):
        dates = sorted(ledger)
        tos = []
        for a, b in zip(dates, dates[1:]):
            sa = {r["ticker"] for r in ledger[a][ver]}
            sb = {r["ticker"] for r in ledger[b][ver]}
            if sb:
                tos.append(100.0 * len(sb - sa) / len(sb))
        out[ver] = round(mean(tos), 1) if tos else None
    return out


# ── Task 4: promotion gates ──────────────────────────────────────────────────

def _gates(ab, recall, quality) -> Dict:
    days = ab["matured_days"]
    tdays = min(ab["ticker_days"].values()) if ab["ticker_days"] else 0
    prod_r = recall.get(PRODUCTION_VERSION, {})
    prop_r = recall.get(PROPOSED_VERSION, {})

    def g(cond):
        return bool(cond)
    enough_days = g(days >= GATE_MIN_DAYS)
    enough_td = g(tdays >= GATE_MIN_TICKER_DAYS)
    insufficient = not (enough_days and enough_td)

    def margin(key):
        a, b = prop_r.get(key), prod_r.get(key)
        return (a - b) if (a is not None and b is not None) else None
    recall_margin = margin("recall_before_20")
    fp_margin = margin("false_positive_pct")
    h20 = (ab["by_horizon"].get("20d", {}) or {})
    prod20 = h20.get(PRODUCTION_VERSION, {}) or {}
    prop20 = h20.get(PROPOSED_VERSION, {}) or {}
    fwd_better = (prop20.get("mean_rel_spy") is not None and prod20.get("mean_rel_spy") is not None
                  and prop20["mean_rel_spy"] >= prod20["mean_rel_spy"])
    decile_better = (prop20.get("top_decile_hit") is not None and prod20.get("top_decile_hit") is not None
                     and prop20["top_decile_hit"] >= prod20["top_decile_hit"])
    theme_better = (quality.get("proposed", {}).get("leading_theme_members", 0)
                    >= quality.get("production", {}).get("leading_theme_members", 0))
    sector_ok = ((prop20.get("max_sector_share") or 0) <= GATE_MAX_SECTOR_SHARE * 100)

    gates = {
        "min_10_trading_days": enough_days,
        "min_3000_ticker_days": enough_td,
        "early_recall_improved": (recall_margin is not None and recall_margin >= GATE_RECALL_MARGIN * 100),
        "fp_not_worse": (fp_margin is not None and fp_margin <= GATE_FP_TOLERANCE * 100),
        "theme_coverage_improved": theme_better,
        "forward_or_decile_improved": bool(fwd_better or decile_better),
        "no_sector_overconcentration": sector_ok,
    }
    overall = (not insufficient) and all(v for k, v in gates.items())
    return {"gates": gates, "all_pass": overall,
            "status": "NEED_MORE_DATA" if insufficient else ("READY" if overall else "NOT_READY"),
            "matured_days": days, "min_ticker_days_observed": tdays,
            "recall_margin_pp": recall_margin, "fp_margin_pp": fp_margin}


def _verdict(ab, recall, gates) -> str:
    if gates["status"] == "NEED_MORE_DATA":
        return "NEED_MORE_DATA"
    rm = gates.get("recall_margin_pp")
    h20 = ab["by_horizon"].get("20d", {})
    prop = (h20.get(PROPOSED_VERSION) or {}).get("mean_rel_spy")
    prod = (h20.get(PRODUCTION_VERSION) or {}).get("mean_rel_spy")
    if rm is None or prop is None or prod is None:
        return "NO_DIFFERENCE"
    if rm >= GATE_RECALL_MARGIN * 100 and prop >= prod and gates["gates"]["fp_not_worse"]:
        return "PROPOSED_BETTER"
    if rm <= -GATE_RECALL_MARGIN * 100 or prod > prop + 0.02:
        return "PRODUCTION_BETTER"
    return "NO_DIFFERENCE"


# ── Task 3: universe quality (composition) ───────────────────────────────────

def _quality(ledger, theme_states) -> Dict:
    latest = sorted(ledger)[-1] if ledger else None
    out = {"asof_date": latest}
    watch_themes = ("semiconductors", "memory_storage", "space_aerospace",
                    "nuclear_energy", "hardware")
    for ver in (PRODUCTION_VERSION, PROPOSED_VERSION):
        rows = ledger.get(latest, {}).get(ver, []) if latest else []
        stages = Counter(r.get("stage_label") for r in rows)
        themes = Counter(r.get("theme") for r in rows)
        secs = Counter((r.get("sector") or "UNKNOWN") for r in rows)
        n = len(rows) or 1
        out["production" if ver == PRODUCTION_VERSION else "proposed"] = {
            "size": len(rows),
            "stage_distribution": dict(stages),
            "early_pct": round(100.0 * sum(stages[s] for s in EARLY_STAGES) / n, 1),
            "late_pct": round(100.0 * sum(stages[s] for s in LATE_STAGES) / n, 1),
            "leading_theme_members": sum(1 for r in rows
                                         if theme_states.get(r.get("theme")) == "LEADING"),
            "theme_coverage": {th: themes.get(th, 0) for th in watch_themes},
            "max_sector_share_pct": round(100.0 * max(secs.values()) / n, 1) if secs else None,
            "distinct_sectors": len(secs),
        }
    return out


# ── Task 5: score-gate interaction audit ─────────────────────────────────────

def _score_gate_audit(ledger, cal) -> Dict:
    """Do the current structural score-gates (mirrored voyager/sniper) kill the
    proposed early leaders? Cache-only; uses research.scanner_truth.filters."""
    latest = sorted(ledger)[-1] if ledger else None
    if not latest:
        return {"status": "no_ledger"}
    asof = cal[-1]
    rows = ledger[latest].get(PROPOSED_VERSION, [])
    early = [r for r in rows if r.get("stage_label") in EARLY_STAGES | {"BREAKOUT_CONFIRMED"}]
    pass_voy = pass_sni = pass_either = 0
    killed_early = 0
    voy_reasons: Counter = Counter()
    sni_reasons: Counter = Counter()
    n_eval = 0
    for r in early:
        df = dataio.load_prices(r["ticker"])
        if df is None:
            continue
        n_eval += 1
        v = voyager_structural(df, asof)
        s = sniper_breakout(df, asof)
        pass_voy += int(v.passed); pass_sni += int(s.passed)
        passed_either = v.passed or s.passed
        pass_either += int(passed_either)
        if not passed_either:
            killed_early += 1
        for code in v.reasons:
            voy_reasons[code] += 1
        for code in s.reasons:
            sni_reasons[code] += 1
    return {
        "status": "ok",
        "n_proposed_early_leaders_evaluated": n_eval,
        "pass_voyager_structural": pass_voy,
        "pass_sniper_breakout": pass_sni,
        "pass_either_gate": pass_either,
        "killed_by_both_gates": killed_early,
        "killed_pct": round(100.0 * killed_early / n_eval, 1) if n_eval else None,
        "top_voyager_reject_reasons": dict(voy_reasons.most_common(6)),
        "top_sniper_reject_reasons": dict(sni_reasons.most_common(6)),
        "recommendation": (
            "Many early leaders are rejected by the structural gates — note that "
            "insufficient_history_* rejections are CACHE-DEPTH artifacts (shallow cache), "
            "not real structure failures. Recommendation (DESIGN ONLY): route forward-"
            "validated RS/theme early leaders to the Stock Lens/Gatekeeper as a research-"
            "only second surface that BYPASSES the voyager/sniper score gates, rather than "
            "loosening those gates. No gate change is made here."),
        "caveat": "voyager_structural needs 260 bars; on the shallow cache most names fail "
                  "on insufficient_history_260, so this audit is fidelity-limited until the "
                  "deepening refresh completes.",
    }


# ── build ────────────────────────────────────────────────────────────────────

def _theme_states() -> Dict[str, str]:
    try:
        th = json.loads((dataio.RESEARCH_CACHE / "theme_leadership_latest.json").read_text())
        return {n: d.get("theme_state") for n, d in (th.get("themes") or {}).items()}
    except Exception:
        return {}


def build() -> Dict:
    ledger = _load_ledger()
    cal = dataio.benchmark_calendar()
    spy = _aligned(dataio.load_prices("SPY"), cal)
    winners = _winners()
    theme_states = _theme_states()

    ab = _ab(ledger, cal, spy, winners)
    recall = _recall(ledger, cal, spy, winners)
    turnover = _turnover(ledger)
    quality = _quality(ledger, theme_states)
    gates = _gates(ab, recall, quality)
    verdict = _verdict(ab, recall, gates)
    sga = _score_gate_audit(ledger, cal)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "disclaimer": "RESEARCH-ONLY forward A/B. Does NOT modify core/universe.py or the "
                      "production universe; no signals, no trades.",
        "history_path": dataio.rel_to_repo(HISTORY_PATH),
        "shadow_dates": sorted(ledger),
        "n_shadow_dates": len(ledger),
        "ticker_days": ab["ticker_days"],
        "forward_ab": ab["by_horizon"],
        "recall_late_fp": recall,
        "turnover_pct": turnover,
        "promotion_gates": gates,
        "verdict": verdict,
        "score_gate_audit": sga,
        "quality": quality,
    }


def _render_txt(res: Dict) -> List[str]:
    L = [
        f"== UNIVERSE FORWARD REPLAY A/B ({res['generated_at']}) ==",
        res["disclaimer"],
        f"shadow dates: {res['n_shadow_dates']}  ticker-days: {res['ticker_days']}",
        f"VERDICT: {res['verdict']}   promotion: {res['promotion_gates']['status']}",
        "",
        f"{'horizon':<8}{'prod_rel':>10}{'prop_rel':>10}{'prod_dec':>10}{'prop_dec':>10}{'matured':>9}",
    ]
    for h, d in res["forward_ab"].items():
        pr = d.get(PRODUCTION_VERSION, {}); pp = d.get(PROPOSED_VERSION, {})

        def f(x):
            return f"{x:+.3f}" if isinstance(x, (int, float)) else "—"
        L.append(f"{h:<8}{f(pr.get('mean_rel_spy')):>10}{f(pp.get('mean_rel_spy')):>10}"
                 f"{f(pr.get('top_decile_hit')):>10}{f(pp.get('top_decile_hit')):>10}"
                 f"{d.get('matured_dates', 0):>9}")
    g = res["promotion_gates"]
    L += ["", "PROMOTION GATES:"]
    for k, v in g["gates"].items():
        L.append(f"  [{'PASS' if v else 'fail'}] {k}")
    L += [f"  → status={g['status']} all_pass={g['all_pass']} "
          f"(days={g['matured_days']}, ticker_days={g['min_ticker_days_observed']})"]
    sga = res["score_gate_audit"]
    if sga.get("status") == "ok":
        L += ["", "SCORE-GATE INTERACTION (proposed early leaders):",
              f"  evaluated={sga['n_proposed_early_leaders_evaluated']}  "
              f"pass_either={sga['pass_either_gate']}  killed={sga['killed_by_both_gates']} "
              f"({sga['killed_pct']}%)",
              f"  voyager rejects: {sga['top_voyager_reject_reasons']}",
              f"  sniper rejects: {sga['top_sniper_reject_reasons']}"]
    q = res["quality"]
    if q.get("production") and q.get("proposed"):
        L += ["",
              f"QUALITY: production early={q['production']['early_pct']}% late={q['production']['late_pct']}% "
              f"lead-theme={q['production']['leading_theme_members']}",
              f"         proposed   early={q['proposed']['early_pct']}% late={q['proposed']['late_pct']}% "
              f"lead-theme={q['proposed']['leading_theme_members']}"]
    return L


def _write_results_doc(res: Dict) -> None:
    p = DOCS_DIR / "UNIVERSE_FORWARD_REPLAY_RESULTS.md"
    q = res["quality"]
    g = res["promotion_gates"]
    sga = res["score_gate_audit"]
    L = [
        "# Universe Forward Replay (A/B) Results — Phase 1G.8",
        "",
        f"*Generated {res['generated_at']} · research-only · cache-only. Does NOT modify "
        "the production universe.*",
        "",
        f"**Verdict:** `{res['verdict']}` · **Promotion:** `{g['status']}` "
        f"(shadow dates: {res['n_shadow_dates']}, ticker-days: {res['ticker_days']}).",
        "",
        "## Why NEED_MORE_DATA (today)",
        "The shadow ledger has just begun; nothing has matured to a forward horizon yet, "
        "so the A/B metrics are empty and the promotion gates cannot pass. The framework "
        "accrues both universes nightly and will populate as bars mature.",
        "",
        "## Promotion gates (Task 4)",
        "",
        "| gate | threshold | status |",
        "|---|---|---|",
        f"| ≥10 trading days | {GATE_MIN_DAYS} | {'PASS' if g['gates']['min_10_trading_days'] else 'fail'} |",
        f"| ≥3000 ticker-days | {GATE_MIN_TICKER_DAYS} | {'PASS' if g['gates']['min_3000_ticker_days'] else 'fail'} |",
        f"| early-winner recall ↑ ≥{int(GATE_RECALL_MARGIN*100)}pp | margin | {'PASS' if g['gates']['early_recall_improved'] else 'fail'} |",
        f"| FP not worse (≤+{int(GATE_FP_TOLERANCE*100)}pp) | margin | {'PASS' if g['gates']['fp_not_worse'] else 'fail'} |",
        f"| theme coverage ↑ | — | {'PASS' if g['gates']['theme_coverage_improved'] else 'fail'} |",
        f"| forward/top-decile ↑ | — | {'PASS' if g['gates']['forward_or_decile_improved'] else 'fail'} |",
        f"| no sector overconcentration (≤{int(GATE_MAX_SECTOR_SHARE*100)}%) | — | {'PASS' if g['gates']['no_sector_overconcentration'] else 'fail'} |",
        "",
        "**If any gate fails, production stays unchanged and shadow research continues.**",
        "",
        "## Universe quality (latest snapshot)",
    ]
    if q.get("production") and q.get("proposed"):
        L += [
            "",
            "| metric | production | proposed |",
            "|---|--:|--:|",
            f"| size | {q['production']['size']} | {q['proposed']['size']} |",
            f"| early-stage % | {q['production']['early_pct']} | {q['proposed']['early_pct']} |",
            f"| late-stage % | {q['production']['late_pct']} | {q['proposed']['late_pct']} |",
            f"| leading-theme members | {q['production']['leading_theme_members']} | {q['proposed']['leading_theme_members']} |",
            f"| max sector share % | {q['production']['max_sector_share_pct']} | {q['proposed']['max_sector_share_pct']} |",
            "",
            "### Theme coverage (semis / memory / space / nuclear / hardware)",
            f"- production: {q['production']['theme_coverage']}",
            f"- proposed: {q['proposed']['theme_coverage']}",
        ]
    L += ["", "## Score-gate interaction (Task 5)"]
    if sga.get("status") == "ok":
        L += [
            f"- Proposed early leaders evaluated: **{sga['n_proposed_early_leaders_evaluated']}**; "
            f"pass either structural gate: **{sga['pass_either_gate']}**; "
            f"killed by both: **{sga['killed_by_both_gates']}** ({sga['killed_pct']}%).",
            f"- Top voyager reject reasons: {sga['top_voyager_reject_reasons']}",
            f"- Top sniper reject reasons: {sga['top_sniper_reject_reasons']}",
            "",
            f"{sga['recommendation']}",
            "",
            f"*Caveat: {sga['caveat']}*",
        ]
    else:
        L.append("- No ledger available yet.")
    L += ["",
          "## Does proposed find candidates earlier / just add noise?",
          "Undetermined until maturity. The dual-version ledger + this replay are the "
          "instrument that will answer it point-in-time. **No production change is made.**",
          ""]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(L) + "\n")


def _write_quality(res: Dict) -> None:
    q = dict(res["quality"])
    q["generated_at"] = res["generated_at"]
    q["verdict"] = res["verdict"]
    dataio.write_json(dataio.RESEARCH_CACHE / "universe_quality_latest.json", q)
    lines = [f"== UNIVERSE QUALITY ({res['generated_at']}) ==", f"verdict={res['verdict']}"]
    for ver in ("production", "proposed"):
        d = q.get(ver)
        if d:
            lines.append(f"{ver}: size={d['size']} early={d['early_pct']}% late={d['late_pct']}% "
                         f"lead-theme={d['leading_theme_members']} themes={d['theme_coverage']}")
    dataio.write_text(dataio.LOGS_DIR / "universe_quality_latest.txt", lines)


def main() -> int:
    res = build()
    dataio.write_json(dataio.RESEARCH_CACHE / "universe_forward_replay_latest.json", res)
    lines = _render_txt(res)
    dataio.write_text(dataio.LOGS_DIR / "universe_forward_replay_latest.txt", lines)
    _write_quality(res)
    _write_results_doc(res)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
research/scanner_truth/theme_audit.py — TASK 6 (theme / sector leadership audit).

For each strong theme cluster: theme breadth + return, top tickers, recall,
and whether the theme is visible on today's Alpha board (the only board snapshot
we can read directly). Concludes with a research-only Theme Leadership Radar
proposal — clustering by realized strength, no trades, no signals.

Output:
  cache/research/scanner_theme_audit_latest.json
  logs/scanner_theme_audit_latest.txt
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from statistics import median
from typing import Dict, List

from . import dataio


def _board() -> List[str]:
    try:
        b = json.loads((dataio.RESEARCH_CACHE / "alpha_discovery_board_latest.json").read_text())
        return [(i.get("symbol") or i.get("ticker") or "").upper()
                for i in b.get("items", []) if (i.get("symbol") or i.get("ticker"))]
    except Exception:
        return []


def build(min_return: float = 0.50) -> Dict:
    uni = json.loads((dataio.RESEARCH_CACHE / "missed_winner_universe_latest.json").read_text())
    winners = [w for w in uni["winners"] if w["best_max_return"] >= min_return]
    profiles = dataio.load_profiles()
    board = _board()
    board_theme = {}
    for t in board:
        board_theme[t] = dataio.classify_theme(profiles.get(t))

    recall_seen = {}
    try:
        tr = json.loads((dataio.RESEARCH_CACHE / "scanner_funnel_trace_latest.json").read_text())
        recall_seen = {t["ticker"]: bool(t["first_date_actually_saw"] or t["today_snapshot"]["in_alpha_board"])
                       for t in tr["traces"]}
    except Exception:
        pass

    themes: Dict[str, Dict] = {}
    for w in winners:
        th = w["theme"]
        d = themes.setdefault(th, {"tickers": [], "returns": []})
        d["tickers"].append(w["ticker"])
        d["returns"].append(w["best_max_return"])

    out = {}
    for th, d in themes.items():
        rets = sorted(d["returns"], reverse=True)
        top = sorted(d["tickers"], key=lambda t: -dict(zip(d["tickers"], d["returns"]))[t])[:6]
        on_board = [t for t in board if board_theme.get(t) == th]
        seen = [t for t in d["tickers"] if recall_seen.get(t)]
        out[th] = {
            "n_winners": len(d["tickers"]),
            "median_max_return": round(median(rets), 3),
            "top_max_return": round(rets[0], 3),
            "top_tickers": top,
            "n_on_todays_alpha_board": len(on_board),
            "board_tickers": on_board,
            "recall_seen": len(seen),
            "visibility": ("visible_on_board" if on_board else "absent_from_board"),
        }
    out = dict(sorted(out.items(), key=lambda x: -x[1]["n_winners"]))

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_winners": len(winners),
        "alpha_board_size": len(board),
        "themes": out,
        "classifier_limitation": (
            "FMP industry taxonomy is coarse: memory & AI-hardware mostly read "
            "'Semiconductors'/'Hardware, Equipment & Parts' and cannot be cleanly "
            "separated by profile. Theme counts are lower bounds for those clusters."),
        "theme_leadership_radar_proposal": {
            "status": "PROPOSED — research-only, not built this phase",
            "what": "A nightly cache-only report that clusters the liquid universe by "
                    "trailing relative-strength co-movement (correlation of 20/60d "
                    "returns) to detect emerging strength CLUSTERS independent of the "
                    "coarse FMP industry labels, and surfaces the top leaders per cluster.",
            "does_not": ["emit signals", "create paper trades", "trade", "tune live filters"],
            "feeds": "If validated, its cluster-strength score could later inform Alpha "
                     "Discovery scoring as an additive feature — only after a forward "
                     "validation gate, never retrofit to past winners.",
            "evidence_for": "semiconductors recall ~2% despite being the dominant winner "
                            "theme; today's board holds only a few semis. A theme lane "
                            "would have flagged the cluster early.",
            "overfit_risk": "MEDIUM — must validate forward (historizer enables it); "
                            "clustering is descriptive, scoring use requires a gate.",
        },
    }


def _render_txt(res: Dict) -> List[str]:
    L = [
        f"== THEME / SECTOR LEADERSHIP AUDIT ({res['generated_at']}) ==",
        f"winners: {res['n_winners']}   alpha board size: {res['alpha_board_size']}",
        "",
        f"{'theme':<20}{'wins':>5}{'medRet':>8}{'topRet':>8}{'onBoard':>8}{'seen':>6}  visibility",
    ]
    for th, d in res["themes"].items():
        L.append(f"{th:<20}{d['n_winners']:>5}{d['median_max_return']*100:>7.0f}%"
                 f"{d['top_max_return']*100:>7.0f}%{d['n_on_todays_alpha_board']:>8}"
                 f"{d['recall_seen']:>6}  {d['visibility']}")
    L += ["",
          "classifier limitation: " + res["classifier_limitation"],
          "",
          "THEME LEADERSHIP RADAR: " + res["theme_leadership_radar_proposal"]["status"],
          "  " + res["theme_leadership_radar_proposal"]["what"]]
    return L


def main() -> int:
    res = build()
    dataio.write_json(dataio.RESEARCH_CACHE / "scanner_theme_audit_latest.json", res)
    lines = _render_txt(res)
    dataio.write_text(dataio.LOGS_DIR / "scanner_theme_audit_latest.txt", lines)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

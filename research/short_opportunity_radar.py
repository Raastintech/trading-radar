#!/usr/bin/env python3
"""
research/short_opportunity_radar.py — Phase 1G.3 T2

Research-only short-side awareness. SHORT_A was frozen on 2026-05-24 (it was
net-negative, noisy, and fighting a bull tape). Freezing it must not blind the
system to short opportunity, so this radar keeps a continuous read on whether the
short side is even worth researching — WITHOUT emitting trades.

It is cache-only: it reads existing research artifacts and never calls providers,
never writes paper_signals, never registers a strategy, and never routes to
governance or execution.

Reads (best-effort; missing inputs degrade gracefully toward SHORTS_OFF):
  - cache/research/regime_forecast_latest.json  (SPY vs 50d/200d, VIX, regime,
    sector rotation, strategy favorability)
  - cache/research/alpha_discovery_board_latest.json  (leaders, extension,
    options_quality, bucket, sector)

Writes:
  - cache/research/short_opportunity_radar_latest.json
  - logs/short_opportunity_radar_latest.txt

SHORT_REGIME_SCORE (0–100) → state band:
  0–30  SHORTS_OFF
  31–50 WATCH
  51–70 RESEARCH_ACTIVE
  71+   SHORT_SLEEVE_TEST_CANDIDATE

Allowed candidate labels: Monitor, Research Candidate, Needs Backtest, Not Active,
Avoid Shorting In Bull Tape. NO "SHORT NOW / SELL NOW / Execute / Trade Approved".
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CACHE = ROOT / "cache" / "research"
FORECAST_PATH = CACHE / "regime_forecast_latest.json"
ALPHA_PATH = CACHE / "alpha_discovery_board_latest.json"
JSON_OUT = CACHE / "short_opportunity_radar_latest.json"
TXT_OUT = ROOT / "logs" / "short_opportunity_radar_latest.txt"

# State bands.
STATE_SHORTS_OFF = "SHORTS_OFF"
STATE_WATCH = "WATCH"
STATE_RESEARCH_ACTIVE = "RESEARCH_ACTIVE"
STATE_TEST_CANDIDATE = "SHORT_SLEEVE_TEST_CANDIDATE"

# Allowed candidate labels (no trade language permitted).
LABEL_MONITOR = "Monitor"
LABEL_RESEARCH = "Research Candidate"
LABEL_BACKTEST = "Needs Backtest"
LABEL_NOT_ACTIVE = "Not Active"
LABEL_AVOID_BULL = "Avoid Shorting In Bull Tape"

ARCHETYPES = (
    "FAILED_LEADER",
    "POST_EARNINGS_FAILED_REACTION",
    "RELATIVE_WEAKNESS_BREAKDOWN",
    "OVERCROWDED_UNWIND",
)


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _state_for_score(score: int) -> str:
    if score <= 30:
        return STATE_SHORTS_OFF
    if score <= 50:
        return STATE_WATCH
    if score <= 70:
        return STATE_RESEARCH_ACTIVE
    return STATE_TEST_CANDIDATE


def compute_short_regime_score(forecast: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute SHORT_REGIME_SCORE 0–100 from the regime forecast. Missing forecast
    → SHORTS_OFF with score 0 (we cannot prove the short side is worth researching)."""
    components: List[Dict[str, Any]] = []
    reasons: List[str] = []

    def add(points: int, why: str):
        components.append({"points": points, "reason": why})
        reasons.append(why)

    if not forecast:
        return {
            "score": 0,
            "state": STATE_SHORTS_OFF,
            "components": [],
            "reasons": ["regime forecast unavailable — short side not researchable"],
            "suppressed_bull_tape": False,
            "inputs": {},
        }

    headline = forecast.get("headline") or {}
    mt = forecast.get("market_trend") or {}
    spy = mt.get("SPY") or {}
    vol = forecast.get("volatility") or {}
    sectors = (forecast.get("sector_rotation") or {}).get("rows") or []

    spy_above_50 = spy.get("above_ma50")
    spy_above_200 = spy.get("above_ma200")
    try:
        vix = float(vol.get("vix")) if vol.get("vix") is not None else None
    except (TypeError, ValueError):
        vix = None
    current_regime = headline.get("current_regime")

    score = 0
    if spy_above_50 is False:
        score += 20; add(20, "SPY below 50d MA")
    if spy_above_200 is False:
        score += 30; add(30, "SPY below 200d MA")
    if vix is not None:
        if vix > 25:
            score += 25; add(25, f"VIX {vix:.1f} > 25 (stress)")
        elif vix > 20:
            score += 15; add(15, f"VIX {vix:.1f} > 20 (elevated)")

    # Defensive sectors leading.
    defensive_leading = [
        s for s in sectors
        if s.get("is_defensive") and str(s.get("state", "")).lower() in ("leading", "improving")
    ]
    if len(defensive_leading) >= 2:
        score += 10
        add(10, f"{len(defensive_leading)} defensive sectors leading/improving")

    # Cyclicals weakening (non-defensive sectors lagging / negative 20d RS).
    cyclicals_weak = [
        s for s in sectors
        if not s.get("is_defensive")
        and (str(s.get("state", "")).lower() == "lagging"
             or (s.get("rs_20d_pct") is not None and s.get("rs_20d_pct") < 0))
    ]
    if len(cyclicals_weak) >= 3:
        score += 10
        add(10, f"{len(cyclicals_weak)} cyclical sectors weakening")

    # Multiple former leaders breaking 50d (sectors that were strong now below 50d MA).
    leaders_breaking = [s for s in sectors if s.get("above_ma50") is False and not s.get("is_defensive")]
    if len(leaders_breaking) >= 2:
        score += 15
        add(15, f"{len(leaders_breaking)} cyclical sectors below 50d MA")

    score = min(score, 100)

    # Bull-tape suppression: if SPY above both MAs and VIX benign, the short side
    # is not worth standing up regardless of minor sector noise.
    suppressed = False
    if spy_above_50 is True and spy_above_200 is True and (vix is not None and vix < 20):
        suppressed = True
        score = min(score, 25)
        reasons.append(
            f"bull tape (SPY>50d & >200d, VIX={vix:.1f}<20) — short regime suppressed"
        )

    return {
        "score": int(score),
        "state": _state_for_score(int(score)),
        "components": components,
        "reasons": reasons,
        "suppressed_bull_tape": suppressed,
        "inputs": {
            "spy_above_ma50": spy_above_50,
            "spy_above_ma200": spy_above_200,
            "vix": vix,
            "current_regime": current_regime,
            "n_sectors": len(sectors),
        },
    }


def _candidate_label(state: str, default: str) -> str:
    return LABEL_AVOID_BULL if state == STATE_SHORTS_OFF else default


def build_candidates(
    alpha: Optional[Dict[str, Any]],
    forecast: Optional[Dict[str, Any]],
    state: str,
) -> Dict[str, Any]:
    """Classify a handful of research-only short candidates by archetype from the
    Alpha Discovery board + sector rotation. No trade language; labels only."""
    by_archetype: Dict[str, List[Dict[str, Any]]] = {a: [] for a in ARCHETYPES}

    items = (alpha or {}).get("items") or []
    sectors = (forecast or {}).get("sector_rotation", {}).get("rows") or []
    weak_sectors = {
        str(s.get("sector")) for s in sectors
        if str(s.get("state", "")).lower() == "lagging"
        or (s.get("rs_20d_pct") is not None and s.get("rs_20d_pct") < 0)
    }

    for it in items:
        tk = it.get("ticker")
        bucket = str(it.get("bucket") or "")
        oq = str(it.get("options_quality") or "")
        r5 = it.get("return_5d_pct")
        sector = str(it.get("sector") or "")
        crowded = bucket == "Too Late / Crowded"

        # OVERCROWDED_UNWIND: crowded leader with bearish options positioning.
        if crowded and oq == "BEARISH_HEDGE":
            by_archetype["OVERCROWDED_UNWIND"].append({
                "ticker": tk, "sector": sector, "bucket": bucket,
                "options_quality": oq, "return_5d_pct": r5,
                "label": _candidate_label(state, LABEL_RESEARCH),
                "why": "crowded leader with bearish options hedge",
            })
        # FAILED_LEADER: crowded/extended leader now showing short-term weakness.
        elif crowded and (r5 is not None and r5 < 0):
            by_archetype["FAILED_LEADER"].append({
                "ticker": tk, "sector": sector, "bucket": bucket,
                "options_quality": oq, "return_5d_pct": r5,
                "label": _candidate_label(state, LABEL_MONITOR),
                "why": "crowded leader cooling (negative 5d)",
            })
        # RELATIVE_WEAKNESS_BREAKDOWN: name in a weak sector with negative momentum.
        elif sector in weak_sectors and (r5 is not None and r5 < 0):
            by_archetype["RELATIVE_WEAKNESS_BREAKDOWN"].append({
                "ticker": tk, "sector": sector, "bucket": bucket,
                "options_quality": oq, "return_5d_pct": r5,
                "label": _candidate_label(state, LABEL_MONITOR),
                "why": "weak name in a lagging sector",
            })

    # POST_EARNINGS_FAILED_REACTION needs gap/earnings-reaction data not present in
    # the cached board; report it as an explicit data gap rather than fabricating.
    post_earn_note = (
        "no post-earnings gap-failure dataset in current cache artifacts; "
        "this archetype needs an event study before it can be populated"
    )

    counts = {a: len(by_archetype[a]) for a in ARCHETYPES}
    # In bull tape, even classified names carry the avoid label.
    return {
        "by_archetype": by_archetype,
        "counts": counts,
        "post_earnings_note": post_earn_note,
        "total": sum(counts.values()),
    }


def _recommendation(state: str) -> List[str]:
    if state == STATE_SHORTS_OFF:
        return ["No short sleeve needed now", "Continue monitoring only"]
    if state == STATE_WATCH:
        return ["Continue monitoring only"]
    if state == STATE_RESEARCH_ACTIVE:
        return ["Start short event study", "Short sleeve design should be reviewed"]
    return ["Paper short sleeve may be considered only after validation"]


def build_radar(*, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    forecast = _load_json(FORECAST_PATH)
    alpha = _load_json(ALPHA_PATH)

    regime = compute_short_regime_score(forecast)
    state = regime["state"]
    candidates = build_candidates(alpha, forecast, state)

    return {
        "kind": "short_opportunity_radar",
        "version": "SHORT_OPPORTUNITY_RADAR_V1",
        "generated_at": now.isoformat(),
        "research_only": True,
        "short_a_status": "FROZEN / RESEARCH ONLY (2026-05-24)",
        "short_regime_score": regime["score"],
        "state": state,
        "suppressed_bull_tape": regime["suppressed_bull_tape"],
        "score_components": regime["components"],
        "reasons": regime["reasons"],
        "inputs": regime["inputs"],
        "candidates": candidates,
        "recommendation": _recommendation(state),
        "sources": {
            "regime_forecast": str(FORECAST_PATH) if forecast else None,
            "alpha_board": str(ALPHA_PATH) if alpha else None,
        },
    }


def render_text(r: Dict[str, Any]) -> str:
    L: List[str] = []
    L.append("=" * 60)
    L.append(f"SHORT OPPORTUNITY RADAR — {r['generated_at'][:19]}  (research-only)")
    L.append("=" * 60)
    L.append(f"SHORT_A: {r['short_a_status']}")
    L.append(f"SHORT_REGIME_SCORE: {r['short_regime_score']}/100  →  STATE: {r['state']}")
    if r["suppressed_bull_tape"]:
        L.append("  (suppressed: bull tape)")
    inp = r["inputs"]
    L.append(f"  SPY>50d={inp.get('spy_above_ma50')} SPY>200d={inp.get('spy_above_ma200')} "
             f"VIX={inp.get('vix')} regime={inp.get('current_regime')!r}")
    L.append("  reasons:")
    for why in r["reasons"]:
        L.append(f"    - {why}")
    L.append("")
    L.append(f"CANDIDATES (research-only, {r['candidates']['total']} total):")
    for arch, rows in r["candidates"]["by_archetype"].items():
        L.append(f"  {arch}: {len(rows)}")
        for c in rows[:5]:
            L.append(f"    - {c['ticker']:<6} [{c['label']}] {c['why']} "
                     f"(5d={c.get('return_5d_pct')}, opts={c.get('options_quality')})")
    L.append(f"  POST_EARNINGS_FAILED_REACTION note: {r['candidates']['post_earnings_note']}")
    L.append("")
    L.append("RECOMMENDATION:")
    for rec in r["recommendation"]:
        L.append(f"  - {rec}")
    L.append("=" * 60)
    return "\n".join(L)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Short Opportunity Radar (research-only)")
    p.add_argument("--print", dest="do_print", action="store_true")
    args = p.parse_args(argv)

    radar = build_radar()
    JSON_OUT.parent.mkdir(parents=True, exist_ok=True)
    TXT_OUT.parent.mkdir(parents=True, exist_ok=True)
    JSON_OUT.write_text(json.dumps(radar, indent=2, default=str))
    text = render_text(radar)
    TXT_OUT.write_text(text + "\n")

    if args.do_print:
        print(text)
    else:
        print(f"short_opportunity_radar: score {radar['short_regime_score']}/100 "
              f"→ {radar['state']} ({radar['candidates']['total']} candidates)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

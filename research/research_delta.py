#!/usr/bin/env python3
"""
research/research_delta.py — "What Changed Today" research delta report.

Reads the latest cached research artifacts and compares them against the
previous snapshot stored under cache/research/history/.  Writes:

  cache/research/research_delta_latest.json
  logs/research_delta_latest.txt

After computing the delta, captures the current state into history/ so the
next run has a baseline to compare against.  Keeps the last N snapshots
(default 20) to bound disk growth.

This script does NOT change any scoring / sleeve / paper / governance /
execution logic.  It only reads cached JSON artifacts and writes a delta
summary.  It makes no provider calls.

Inputs (all cache/research/*_latest.json):
  - regime_forecast_latest.json          (Market Forecast)
  - alpha_discovery_board_latest.json    (Alpha Discovery — nightly)
  - alpha_discovery_overlay_latest.json  (Alpha Discovery — premarket overlay)
  - social_arb_latest.json               (Social Arb radar)
  - universe_snapshot.json (optional)    (structural candidates / readiness)

Optional inputs (best-effort, may be missing):
  - scan_results via SQLite (skipped here — scanner has its own cadence;
    delta reports note "scanner cache not consulted in delta v1")
  - open positions via Alpaca (best-effort, network-fenced; baseline empty
    if Alpaca client unreachable)

Output JSON shape (research-only · not trade approval):
  {
    "built_at": ISO,
    "previous_at": ISO | None,
    "baseline": bool,
    "headline": [ "regime: …", "5d bias: …", … ],
    "market_forecast": { … },
    "alpha_discovery": { "new":[], "removed":[], "upgrades":[], "downgrades":[],
                         "validator_state_changes":[] },
    "alpha_overlay":   { same shape },
    "market_posture":  { … } (cache-only via universe snapshot),
    "social_arb":      { "new_leads":[], "removed_leads":[], "high_quality":[] },
    "scanner":         { "note": …, "new_approved":[] },
    "positions":       { "new":[], "removed":[] },
    "needs_action":    [ … ],
    "research_only":   true
  }
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── env load before core imports ──────────────────────────────────────────────
_CRED = os.environ.get("SNIPER_ENV_PATH")
if _CRED and Path(_CRED).exists():
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv(_CRED, override=True)
    except ImportError:
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Lazy / soft imports — research_delta must run even when some modules are
# unavailable (e.g. positions skipped if Alpaca client missing).
try:
    import core.config as cfg  # type: ignore
    CACHE_DIR = Path(cfg.CACHE_DIR) if hasattr(cfg, "CACHE_DIR") else (ROOT / "cache")
    LOG_DIR   = Path(cfg.LOG_DIR)   if hasattr(cfg, "LOG_DIR")   else (ROOT / "logs")
except Exception:
    CACHE_DIR = ROOT / "cache"
    LOG_DIR   = ROOT / "logs"

RESEARCH_DIR = CACHE_DIR / "research"
HISTORY_DIR  = RESEARCH_DIR / "history"

DEFAULT_HISTORY_KEEP = 20

# Artifact paths.
PATH_FORECAST       = RESEARCH_DIR / "regime_forecast_latest.json"
PATH_ALPHA_BOARD    = RESEARCH_DIR / "alpha_discovery_board_latest.json"
PATH_ALPHA_OVERLAY  = RESEARCH_DIR / "alpha_discovery_overlay_latest.json"
PATH_SOCIAL_ARB     = RESEARCH_DIR / "social_arb_latest.json"
PATH_UNIVERSE_SNAP  = CACHE_DIR / "universe_snapshot.json"

DELTA_JSON = RESEARCH_DIR / "research_delta_latest.json"
DELTA_TXT  = LOG_DIR     / "research_delta_latest.txt"


# ══════════════════════════════════════════════════════════════════════════════
# IO
# ══════════════════════════════════════════════════════════════════════════════

def _read_json(p: Path) -> Dict[str, Any]:
    """Read a JSON file or return {} if missing/unreadable."""
    try:
        if not p.exists():
            return {}
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def _maybe_positions() -> List[Dict[str, Any]]:
    """Best-effort open-positions snapshot.  Cache-only fallbacks: empty list
    when Alpaca client is unavailable / errors.  Never raises."""
    try:
        from core.alpaca_client import get_alpaca  # type: ignore
        client = get_alpaca()
        pos = client.get_positions() or []
        return [{"ticker": str(p.get("ticker") or "").upper(),
                 "side":   str(p.get("side") or "long")}
                for p in pos if p.get("ticker")]
    except Exception:
        return []


# ══════════════════════════════════════════════════════════════════════════════
# SNAPSHOTS — slim representations used both for diffing and history
# ══════════════════════════════════════════════════════════════════════════════

def _slim_forecast(art: Dict[str, Any]) -> Dict[str, Any]:
    head = art.get("headline") or {}
    sec  = art.get("sector_rotation") or {}
    sf   = art.get("strategy_favorability") or {}
    sf_slim: Dict[str, str] = {}
    for k, v in sf.items():
        if isinstance(v, dict):
            sf_slim[k] = str(v.get("stance") or "—")
    return {
        "regime":     str(head.get("current_regime") or "—"),
        "bias_5d":    str(head.get("bias_5d") or "—"),
        "bias_10d":   str(head.get("bias_10d") or "—"),
        "confidence": str(head.get("confidence") or "—"),
        "leading":    list(sec.get("leading") or []),
        "improving":  list(sec.get("improving") or []),
        "weakening":  list(sec.get("weakening") or []),
        "defensive":  list(sec.get("defensive") or []),
        "strategy":   sf_slim,
        "anchor":     str(art.get("anchor_date") or "—"),
        "built_at":   str(art.get("built_at") or art.get("_mtime_iso") or ""),
    }


def _slim_alpha(art: Dict[str, Any]) -> Dict[str, Any]:
    items = art.get("items") or []
    rows: List[Dict[str, Any]] = []
    for it in items:
        sym = str(it.get("ticker") or "").upper()
        if not sym:
            continue
        rows.append({
            "ticker":          sym,
            "tier":            str(it.get("data_tier") or "C"),
            "bucket":          str(it.get("bucket") or "—"),
            "validator_state": str(it.get("validator_state") or "—"),
            "alpha_score":     float(it.get("alpha_score") or 0.0),
            "track":           str(it.get("track") or ""),
            "actionable_now":  bool(it.get("actionable_now")),
        })
    return {
        "items":   rows,
        "tiers":   art.get("tier_counts") or {},
        "tracks":  art.get("track_counts") or {},
        "buckets": art.get("bucket_counts") or {},
        "built_at": str(art.get("built_at") or art.get("_mtime_iso") or ""),
    }


def _slim_social(art: Dict[str, Any]) -> Dict[str, Any]:
    items = art.get("items") or []
    rows: List[Dict[str, Any]] = []
    for it in items:
        sym = str(it.get("ticker") or "").upper()
        if not sym:
            continue
        rows.append({
            "ticker":     sym,
            "bucket":     str(it.get("bucket") or "—"),
            "confidence": str(it.get("confidence") or "—"),
            "noise_risk": str(it.get("noise_risk") or "—"),
            "label":      str(it.get("news_label") or it.get("theme") or "—"),
        })
    return {"items": rows,
            "built_at": str(art.get("built_at") or art.get("_mtime_iso") or "")}


def _slim_posture(snap: Dict[str, Any]) -> Dict[str, Any]:
    """Market Posture is derived from the universe snapshot.  We invoke
    research_assist_bte directly; if it errors (missing fields, etc.), we
    return an empty posture and the delta marks it unavailable."""
    try:
        from core.research_assist_bte import build_research_bte  # type: ignore
        out = build_research_bte(universe_snapshot=snap or {}, regime=None, vix=None)
        focus_names = []
        for r in (out.focus_names or [])[:8]:
            sym = str(r.get("symbol") or "").upper()
            if sym:
                focus_names.append({
                    "symbol": sym,
                    "status": str(r.get("status") or ""),
                    "tag":    str(r.get("compliance_tag") or ""),
                })
        return {
            "bias":        str(out.bias or "—"),
            "confidence":  str(out.confidence or "—"),
            "factors":     list(out.factors or [])[:5],
            "playbook":    list(out.playbook or [])[:5],
            "risk_flag":   str(out.risk_flag or "none"),
            "focus_names": focus_names,
        }
    except Exception:
        return {"bias": "—", "confidence": "—", "factors": [],
                "playbook": [], "risk_flag": "none", "focus_names": []}


def _slim_structural(snap: Dict[str, Any]) -> Dict[str, Any]:
    cands = (snap or {}).get("strategy_candidates") or []
    rows: List[Dict[str, Any]] = []
    for c in cands:
        sym = str(c.get("symbol") or "").upper()
        if not sym:
            continue
        rdns = str(c.get("readiness") or "")
        if rdns not in {"READY_NOW", "WATCH", "DEVELOPING"}:
            continue
        rows.append({
            "symbol":    sym,
            "readiness": rdns,
            "strategy":  str(c.get("strategy") or "—"),
            "direction": str(c.get("direction") or "—"),
            "score":     float(c.get("final_score") or 0.0),
        })
    return {"items": rows}


def capture_snapshot() -> Dict[str, Any]:
    """Read all current artifacts and return a slim snapshot.  This is the
    object stored under history/ and used both as the "current" side of the
    diff and as the next "previous"."""
    snap = _read_json(PATH_UNIVERSE_SNAP)
    out = {
        "captured_at":     _now_iso(),
        "market_forecast": _slim_forecast(_read_json(PATH_FORECAST)),
        "alpha_board":     _slim_alpha(_read_json(PATH_ALPHA_BOARD)),
        "alpha_overlay":   _slim_alpha(_read_json(PATH_ALPHA_OVERLAY)),
        "social_arb":      _slim_social(_read_json(PATH_SOCIAL_ARB)),
        "market_posture":  _slim_posture(snap),
        "structural":      _slim_structural(snap),
        "positions":       _maybe_positions(),
    }
    return out


# ══════════════════════════════════════════════════════════════════════════════
# HISTORY — store/list/load slim snapshots under cache/research/history/
# ══════════════════════════════════════════════════════════════════════════════

def _history_files() -> List[Path]:
    if not HISTORY_DIR.exists():
        return []
    files = sorted(HISTORY_DIR.glob("snapshot_*.json"))
    return files


def _trim_history(keep: int) -> None:
    files = _history_files()
    if len(files) <= keep:
        return
    for old in files[:len(files) - keep]:
        try:
            old.unlink()
        except Exception:
            pass


def _save_snapshot(snap: Dict[str, Any]) -> Path:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    # Use microsecond precision so two runs inside the same second produce
    # distinct history files; the captured_at field on the snapshot itself
    # remains second-resolution for human readability.
    ts = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y%m%dT%H%M%S_%f")
    out = HISTORY_DIR / f"snapshot_{ts}.json"
    out.write_text(json.dumps(snap, indent=2, default=str), encoding="utf-8")
    return out


def _load_previous() -> Optional[Dict[str, Any]]:
    files = _history_files()
    if not files:
        return None
    try:
        return json.loads(files[-1].read_text(encoding="utf-8"))
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════════════════
# DIFF — pure functions on slim snapshots
# ══════════════════════════════════════════════════════════════════════════════

TIER_ORDER = {"A": 0, "B": 1, "C": 2}
BUCKET_ORDER = {
    "Buyable Now": 0, "Top Discovery Now": 0,
    "Buyable Pullback": 1, "Sponsor Confirmation": 2, "Pullback Watch": 2,
    "Early Discovery": 3, "Watch / Stalk": 3, "Too Late / Crowded": 4,
}


def _diff_forecast(prev: Dict[str, Any], curr: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k in ("regime", "bias_5d", "bias_10d", "confidence", "anchor"):
        if (prev.get(k) or "—") != (curr.get(k) or "—"):
            out[k] = {"from": prev.get(k), "to": curr.get(k)}
    # Sector rotation: which buckets gained/lost which sector tickers.
    for bucket in ("leading", "improving", "weakening", "defensive"):
        prev_set = set(prev.get(bucket) or [])
        curr_set = set(curr.get(bucket) or [])
        added = sorted(curr_set - prev_set)
        removed = sorted(prev_set - curr_set)
        if added or removed:
            out.setdefault("sectors", {})[bucket] = {"added": added, "removed": removed}
    # Strategy favorability changes.
    sf_prev = prev.get("strategy") or {}
    sf_curr = curr.get("strategy") or {}
    sf_changes = []
    for k in sorted(set(sf_prev) | set(sf_curr)):
        a = sf_prev.get(k) or "—"
        b = sf_curr.get(k) or "—"
        if a != b:
            sf_changes.append({"strategy": k, "from": a, "to": b})
    if sf_changes:
        out["strategy_favorability"] = sf_changes
    return out


def _diff_alpha(prev: Dict[str, Any], curr: Dict[str, Any]) -> Dict[str, Any]:
    prev_items = {row["ticker"]: row for row in (prev.get("items") or [])}
    curr_items = {row["ticker"]: row for row in (curr.get("items") or [])}
    new = [curr_items[t] for t in sorted(curr_items.keys() - prev_items.keys())]
    removed = [prev_items[t] for t in sorted(prev_items.keys() - curr_items.keys())]
    upgrades: List[Dict[str, Any]] = []
    downgrades: List[Dict[str, Any]] = []
    validator_changes: List[Dict[str, Any]] = []
    for t in sorted(prev_items.keys() & curr_items.keys()):
        a, b = prev_items[t], curr_items[t]
        # Tier change.
        ta, tb = a.get("tier") or "C", b.get("tier") or "C"
        if ta != tb:
            (upgrades if TIER_ORDER.get(tb, 9) < TIER_ORDER.get(ta, 9) else downgrades).append({
                "ticker": t, "from_tier": ta, "to_tier": tb,
            })
            continue
        # Bucket change (lower order = more actionable).
        ba, bb = a.get("bucket") or "—", b.get("bucket") or "—"
        if ba != bb:
            ra, rb = BUCKET_ORDER.get(ba, 9), BUCKET_ORDER.get(bb, 9)
            if rb < ra:
                upgrades.append({"ticker": t, "from_bucket": ba, "to_bucket": bb})
            elif rb > ra:
                downgrades.append({"ticker": t, "from_bucket": ba, "to_bucket": bb})
            else:
                # same rank, different label — record as neutral validator-style change
                validator_changes.append({"ticker": t, "field": "bucket",
                                          "from": ba, "to": bb})
            continue
        # Validator state change.
        va, vb = a.get("validator_state") or "—", b.get("validator_state") or "—"
        if va != vb:
            validator_changes.append({"ticker": t, "field": "validator_state",
                                      "from": va, "to": vb})
    return {
        "new":                      new,
        "removed":                  removed,
        "upgrades":                 upgrades,
        "downgrades":               downgrades,
        "validator_state_changes":  validator_changes,
    }


def _diff_social(prev: Dict[str, Any], curr: Dict[str, Any]) -> Dict[str, Any]:
    prev_items = {row["ticker"]: row for row in (prev.get("items") or [])}
    curr_items = {row["ticker"]: row for row in (curr.get("items") or [])}
    new = [curr_items[t] for t in sorted(curr_items.keys() - prev_items.keys())]
    removed = [prev_items[t] for t in sorted(prev_items.keys() - curr_items.keys())]
    high_quality = []
    for row in curr_items.values():
        bucket = row.get("bucket") or ""
        conf = (row.get("confidence") or "").lower()
        if bucket in {"Cross-Confirmed Lead", "Options/Tape Confirmed"} or conf == "high":
            high_quality.append(row)
    return {"new_leads": new, "removed_leads": removed,
            "high_quality": high_quality}


def _diff_posture(prev: Dict[str, Any], curr: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k in ("bias", "confidence", "risk_flag"):
        if (prev.get(k) or "—") != (curr.get(k) or "—"):
            out[k] = {"from": prev.get(k), "to": curr.get(k)}
    pa = list(prev.get("playbook") or [])
    pb = list(curr.get("playbook") or [])
    if pa != pb:
        out["playbook"] = {
            "added":   sorted(set(pb) - set(pa)),
            "removed": sorted(set(pa) - set(pb)),
        }
    fa = {f["symbol"] for f in (prev.get("focus_names") or [])}
    fb = {f["symbol"] for f in (curr.get("focus_names") or [])}
    if fa != fb:
        out["focus_added"]   = sorted(fb - fa)
        out["focus_removed"] = sorted(fa - fb)
    return out


def _diff_structural(prev: Dict[str, Any], curr: Dict[str, Any]) -> Dict[str, Any]:
    prev_items = {(row["symbol"], row["readiness"]): row
                  for row in (prev.get("items") or [])}
    curr_items = {(row["symbol"], row["readiness"]): row
                  for row in (curr.get("items") or [])}
    # Symbol-level "newly READY_NOW" is the most useful signal.
    prev_ready = {sym for (sym, rdns) in prev_items if rdns == "READY_NOW"}
    curr_ready = {sym for (sym, rdns) in curr_items if rdns == "READY_NOW"}
    newly_ready = sorted(curr_ready - prev_ready)
    fell_off    = sorted(prev_ready - curr_ready)
    return {"newly_ready": newly_ready, "fell_off": fell_off}


def _diff_positions(prev: List[Dict[str, Any]],
                    curr: List[Dict[str, Any]]) -> Dict[str, Any]:
    p = {(r["ticker"], r.get("side") or "long") for r in (prev or [])}
    c = {(r["ticker"], r.get("side") or "long") for r in (curr or [])}
    return {
        "new":     sorted([t for (t, _) in c - p]),
        "removed": sorted([t for (t, _) in p - c]),
    }


# ══════════════════════════════════════════════════════════════════════════════
# COMPOSE the delta payload
# ══════════════════════════════════════════════════════════════════════════════

def _stock_lens_missing(tickers: List[str]) -> List[str]:
    """Which of the given tickers do not yet have a stock lens artifact on
    disk?  Cache-only check, no provider calls."""
    out = []
    for t in tickers:
        p = RESEARCH_DIR / f"stock_lens_{t.upper()}_latest.json"
        if not p.exists():
            out.append(t.upper())
    return out


def _compose_headline(curr: Dict[str, Any], delta: Dict[str, Any]) -> List[str]:
    h: List[str] = []
    f = delta.get("market_forecast") or {}
    if f.get("regime"):
        h.append(f"regime: {f['regime']['from']} → {f['regime']['to']}")
    if f.get("bias_5d"):
        h.append(f"5d bias: {f['bias_5d']['from']} → {f['bias_5d']['to']}")
    if f.get("bias_10d"):
        h.append(f"10d bias: {f['bias_10d']['from']} → {f['bias_10d']['to']}")
    if f.get("confidence"):
        h.append(f"confidence: {f['confidence']['from']} → {f['confidence']['to']}")
    if f.get("strategy_favorability"):
        for ch in f["strategy_favorability"][:3]:
            h.append(f"strategy {ch['strategy']}: {ch['from']} → {ch['to']}")
    a = delta.get("alpha_discovery") or {}
    if a.get("new"):
        h.append(f"alpha new names: {len(a['new'])}")
    if a.get("upgrades"):
        h.append(f"alpha upgrades: {len(a['upgrades'])}")
    if a.get("downgrades"):
        h.append(f"alpha downgrades: {len(a['downgrades'])}")
    s = delta.get("social_arb") or {}
    if s.get("new_leads"):
        h.append(f"social leads new: {len(s['new_leads'])}")
    return h[:8]


def _compose_needs_action(delta: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    a = delta.get("alpha_discovery") or {}
    new_alpha = [r["ticker"] for r in (a.get("new") or [])]
    if new_alpha:
        miss = _stock_lens_missing(new_alpha)
        if miss:
            out.append(f"Stock Lens missing for new Alpha names: {', '.join(miss[:8])}")
    posture = delta.get("market_posture") or {}
    new_focus = posture.get("focus_added") or []
    if new_focus:
        miss_f = _stock_lens_missing(new_focus)
        if miss_f:
            out.append(f"Stock Lens missing for new Posture focus: {', '.join(miss_f[:8])}")
    structural = delta.get("structural") or {}
    nready = structural.get("newly_ready") or []
    if nready:
        out.append(f"Newly READY_NOW structural: {', '.join(nready[:8])}")
    sf = (delta.get("market_forecast") or {}).get("strategy_favorability") or []
    if sf:
        out.append("Strategy favorability changed — review playbook gates")
    return out


def build_delta(curr: Dict[str, Any],
                prev: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if prev is None:
        return {
            "built_at":    _now_iso(),
            "previous_at": None,
            "baseline":    True,
            "headline":    ["baseline created; no prior comparison."],
            "market_forecast": {},
            "alpha_discovery": {"new": [], "removed": [], "upgrades": [],
                                "downgrades": [], "validator_state_changes": []},
            "alpha_overlay":   {"new": [], "removed": [], "upgrades": [],
                                "downgrades": [], "validator_state_changes": []},
            "market_posture":  {},
            "social_arb":      {"new_leads": [], "removed_leads": [],
                                "high_quality": []},
            "structural":      {"newly_ready": [], "fell_off": []},
            "scanner":         {"note": "scanner cache not consulted in delta v1"},
            "positions":       {"new": [], "removed": []},
            "needs_action":    [],
            "research_only":   True,
        }

    delta: Dict[str, Any] = {
        "built_at":    _now_iso(),
        "previous_at": str(prev.get("captured_at") or ""),
        "baseline":    False,
        "market_forecast": _diff_forecast(prev.get("market_forecast") or {},
                                          curr.get("market_forecast") or {}),
        "alpha_discovery": _diff_alpha(prev.get("alpha_board") or {},
                                       curr.get("alpha_board") or {}),
        "alpha_overlay":   _diff_alpha(prev.get("alpha_overlay") or {},
                                       curr.get("alpha_overlay") or {}),
        "market_posture":  _diff_posture(prev.get("market_posture") or {},
                                         curr.get("market_posture") or {}),
        "social_arb":      _diff_social(prev.get("social_arb") or {},
                                        curr.get("social_arb") or {}),
        "structural":      _diff_structural(prev.get("structural") or {},
                                            curr.get("structural") or {}),
        "scanner":         {"note": "scanner cache not consulted in delta v1"},
        "positions":       _diff_positions(prev.get("positions") or [],
                                           curr.get("positions") or []),
        "research_only":   True,
    }
    delta["headline"]     = _compose_headline(curr, delta)
    delta["needs_action"] = _compose_needs_action(delta)
    return delta


# ══════════════════════════════════════════════════════════════════════════════
# TEXT RENDERER
# ══════════════════════════════════════════════════════════════════════════════

def render_text(delta: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("=" * 72)
    lines.append("WHAT CHANGED — research delta · cache-only · not trade approval")
    lines.append("=" * 72)
    lines.append(f"built_at:    {delta.get('built_at','—')}")
    lines.append(f"previous_at: {delta.get('previous_at','—')}")
    if delta.get("baseline"):
        lines.append("")
        lines.append("baseline created; no prior comparison.")
        return "\n".join(lines) + "\n"

    if delta.get("headline"):
        lines.append("")
        lines.append("-- HEADLINE --")
        for h in delta["headline"]:
            lines.append(f"  · {h}")

    f = delta.get("market_forecast") or {}
    if f:
        lines.append("")
        lines.append("-- MARKET FORECAST --")
        for k in ("regime", "bias_5d", "bias_10d", "confidence", "anchor"):
            if k in f:
                lines.append(f"  {k}: {f[k]['from']} → {f[k]['to']}")
        sectors = f.get("sectors") or {}
        for bucket, ch in sectors.items():
            added = ", ".join(ch.get("added") or []) or "—"
            removed = ", ".join(ch.get("removed") or []) or "—"
            lines.append(f"  sectors {bucket}: +{added}  -{removed}")
        for ch in (f.get("strategy_favorability") or []):
            lines.append(f"  strategy {ch['strategy']}: {ch['from']} → {ch['to']}")

    def _alpha_block(title: str, blk: Dict[str, Any]) -> None:
        if not (blk.get("new") or blk.get("removed") or blk.get("upgrades")
                or blk.get("downgrades") or blk.get("validator_state_changes")):
            return
        lines.append("")
        lines.append(f"-- {title} --")
        if blk.get("new"):
            names = ", ".join(f"{r['ticker']}({r['tier']}/{r['bucket']})"
                              for r in blk["new"][:8])
            lines.append(f"  NEW ({len(blk['new'])}): {names}")
        if blk.get("upgrades"):
            for u in blk["upgrades"][:8]:
                if "from_tier" in u:
                    lines.append(f"  UPGRADE {u['ticker']}: tier {u['from_tier']} → {u['to_tier']}")
                else:
                    lines.append(f"  UPGRADE {u['ticker']}: bucket {u['from_bucket']} → {u['to_bucket']}")
        if blk.get("downgrades"):
            for u in blk["downgrades"][:8]:
                if "from_tier" in u:
                    lines.append(f"  DOWNGRADE {u['ticker']}: tier {u['from_tier']} → {u['to_tier']}")
                else:
                    lines.append(f"  DOWNGRADE {u['ticker']}: bucket {u['from_bucket']} → {u['to_bucket']}")
        if blk.get("removed"):
            names = ", ".join(r["ticker"] for r in blk["removed"][:8])
            lines.append(f"  REMOVED ({len(blk['removed'])}): {names}")
        if blk.get("validator_state_changes"):
            for v in blk["validator_state_changes"][:6]:
                lines.append(f"  STATE {v['ticker']} {v.get('field','validator_state')}: {v['from']} → {v['to']}")

    _alpha_block("ALPHA DISCOVERY (nightly)", delta.get("alpha_discovery") or {})
    _alpha_block("ALPHA DISCOVERY (overlay)", delta.get("alpha_overlay") or {})

    p = delta.get("market_posture") or {}
    if p:
        lines.append("")
        lines.append("-- MARKET POSTURE --")
        for k in ("bias", "confidence", "risk_flag"):
            if k in p:
                lines.append(f"  {k}: {p[k]['from']} → {p[k]['to']}")
        if p.get("playbook"):
            lines.append(f"  playbook +{p['playbook'].get('added') or '—'}  "
                         f"-{p['playbook'].get('removed') or '—'}")
        if p.get("focus_added"):
            lines.append(f"  focus added: {', '.join(p['focus_added'])}")
        if p.get("focus_removed"):
            lines.append(f"  focus removed: {', '.join(p['focus_removed'])}")

    s = delta.get("social_arb") or {}
    if s.get("new_leads") or s.get("removed_leads") or s.get("high_quality"):
        lines.append("")
        lines.append("-- SOCIAL ARB --")
        if s.get("new_leads"):
            names = ", ".join(r["ticker"] for r in s["new_leads"][:8])
            lines.append(f"  new leads: {names}")
        if s.get("removed_leads"):
            names = ", ".join(r["ticker"] for r in s["removed_leads"][:8])
            lines.append(f"  expired:   {names}")
        if s.get("high_quality"):
            names = ", ".join(r["ticker"] for r in s["high_quality"][:8])
            lines.append(f"  high-quality this run: {names}")

    st = delta.get("structural") or {}
    if st.get("newly_ready") or st.get("fell_off"):
        lines.append("")
        lines.append("-- STRUCTURAL CANDIDATES --")
        if st.get("newly_ready"):
            lines.append(f"  newly READY_NOW: {', '.join(st['newly_ready'][:8])}")
        if st.get("fell_off"):
            lines.append(f"  fell off READY: {', '.join(st['fell_off'][:8])}")

    pos = delta.get("positions") or {}
    if pos.get("new") or pos.get("removed"):
        lines.append("")
        lines.append("-- POSITIONS --")
        if pos.get("new"):
            lines.append(f"  opened: {', '.join(pos['new'][:8])}")
        if pos.get("removed"):
            lines.append(f"  closed: {', '.join(pos['removed'][:8])}")

    needs = delta.get("needs_action") or []
    if needs:
        lines.append("")
        lines.append("-- NEEDS ACTION --")
        for n in needs:
            lines.append(f"  · {n}")

    lines.append("")
    lines.append("research-only · this report does not approve any trade.")
    return "\n".join(lines) + "\n"


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Research delta — what changed today.")
    parser.add_argument("--keep", type=int, default=DEFAULT_HISTORY_KEEP,
                        help="how many history snapshots to retain (default 20)")
    parser.add_argument("--print-text", action="store_true",
                        help="print the text report to stdout in addition to writing it")
    args = parser.parse_args(argv)

    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    curr = capture_snapshot()
    prev = _load_previous()
    delta = build_delta(curr, prev)

    DELTA_JSON.write_text(json.dumps(delta, indent=2, default=str), encoding="utf-8")
    text = render_text(delta)
    DELTA_TXT.write_text(text, encoding="utf-8")

    # Snapshot the current state for the next run AFTER computing the delta.
    _save_snapshot(curr)
    _trim_history(args.keep)

    print(f"research_delta: wrote {DELTA_JSON}")
    print(f"research_delta: wrote {DELTA_TXT}")
    if args.print_text:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

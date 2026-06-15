#!/usr/bin/env python3
"""
research/research_change_detector.py — Daily scanner watchlist change detection.

Compares the current research_scanner_latest.json watchlist against the
previous run's snapshot to surface what changed: new entries, dropped names,
score movements, and label reclassifications.

Changes detected:
  NEW_ENTRY    — ticker not in previous run
  DROPPED      — ticker in previous but not current
  SCORE_UP     — research_score increased by ≥ 5 points
  SCORE_DOWN   — research_score decreased by ≥ 5 points
  LABEL_CHANGE — watchlist_label changed

On each run, the current scanner snapshot is atomically rotated into
cache/research/research_scanner_prev.json after computing the delta.

Outputs:
  cache/research/research_changes_latest.json
  logs/research_changes_latest.txt

Usage:
  GEM_TRADER_SKIP_DOTENV=true .venv/bin/python research/research_change_detector.py
  SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python research/research_change_detector.py
  ./scripts/run_research_cycle.sh research-changes
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None  # type: ignore[assignment]

if load_dotenv is not None and os.getenv("GEM_TRADER_SKIP_DOTENV", "").lower() not in {"1", "true", "yes"}:
    env_path = os.getenv("SNIPER_ENV_PATH", "").strip()
    if env_path:
        load_dotenv(env_path, override=False)
    load_dotenv(ROOT / ".env", override=False)

os.environ.setdefault("ALPACA_API_KEY", "offline")
os.environ.setdefault("ALPACA_SECRET_KEY", "offline")
os.environ.setdefault("FMP_API_KEY", "offline")
os.environ.setdefault("FMP_BASE_URL", "https://financialmodelingprep.com/stable")
os.environ.setdefault("ALPACA_PAPER", "true")
os.environ.setdefault("PAPER_TRADING", "true")
os.environ.setdefault("DB_PATH", str(ROOT / "db" / "trading.db"))
os.environ.setdefault("CACHE_DIR", str(ROOT / "cache"))
os.environ.setdefault("LOG_DIR", str(ROOT / "logs"))

import core.config as cfg
from core.research_mode import SYSTEM_MODE, RESEARCH_ONLY_BANNER

VERSION = "RESEARCH_CHANGE_DETECTOR_V1"
RESEARCH_DIR = cfg.CACHE_DIR / "research"
SCANNER_LATEST = RESEARCH_DIR / "research_scanner_latest.json"
SCANNER_PREV = RESEARCH_DIR / "research_scanner_prev.json"
OUT_JSON = RESEARCH_DIR / "research_changes_latest.json"
OUT_TXT = cfg.LOG_DIR / "research_changes_latest.txt"

SCORE_CHANGE_THRESHOLD = 5.0

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("research_change_detector")


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Failed to load %s: %s", path, e)
        return None


def _watchlist_by_ticker(scanner: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {item["ticker"]: item for item in scanner.get("watchlist", [])}


def detect_changes(
    current: Dict[str, Any],
    previous: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()

    if previous is None:
        return {
            "version": VERSION,
            "generated_at": now,
            "system_mode": SYSTEM_MODE,
            "research_only": True,
            "current_generated_at": current.get("generated_at"),
            "previous_generated_at": None,
            "first_run": True,
            "changes": [],
            "change_counts": {"NEW_ENTRY": 0, "DROPPED": 0, "SCORE_UP": 0, "SCORE_DOWN": 0, "LABEL_CHANGE": 0},
            "summary": "No previous snapshot — first run baseline established.",
            "guardrails": {"no_trade_recommendation": True, "diagnostic_only": True},
        }

    cur_by_ticker = _watchlist_by_ticker(current)
    prev_by_ticker = _watchlist_by_ticker(previous)

    cur_set = set(cur_by_ticker)
    prev_set = set(prev_by_ticker)

    changes: List[Dict[str, Any]] = []

    for ticker in sorted(cur_set - prev_set):
        item = cur_by_ticker[ticker]
        changes.append({
            "change_type": "NEW_ENTRY",
            "ticker": ticker,
            "current_score": item.get("research_score"),
            "current_label": item.get("watchlist_label"),
            "category": item.get("category"),
        })

    for ticker in sorted(prev_set - cur_set):
        item = prev_by_ticker[ticker]
        changes.append({
            "change_type": "DROPPED",
            "ticker": ticker,
            "previous_score": item.get("research_score"),
            "previous_label": item.get("watchlist_label"),
            "category": item.get("category"),
        })

    for ticker in sorted(cur_set & prev_set):
        cur = cur_by_ticker[ticker]
        prv = prev_by_ticker[ticker]
        cur_score = cur.get("research_score") or 0
        prv_score = prv.get("research_score") or 0
        cur_label = cur.get("watchlist_label")
        prv_label = prv.get("watchlist_label")

        delta = cur_score - prv_score
        label_changed = cur_label != prv_label

        base: Dict[str, Any] = {
            "ticker": ticker,
            "current_score": cur_score,
            "previous_score": prv_score,
            "score_delta": round(delta, 1),
            "current_label": cur_label,
            "previous_label": prv_label,
            "category": cur.get("category"),
        }

        if delta >= SCORE_CHANGE_THRESHOLD:
            changes.append({**base, "change_type": "SCORE_UP"})
        elif delta <= -SCORE_CHANGE_THRESHOLD:
            changes.append({**base, "change_type": "SCORE_DOWN"})
        elif label_changed:
            changes.append({**base, "change_type": "LABEL_CHANGE"})

    counts: Dict[str, int] = {"NEW_ENTRY": 0, "DROPPED": 0, "SCORE_UP": 0, "SCORE_DOWN": 0, "LABEL_CHANGE": 0}
    for c in changes:
        counts[c["change_type"]] = counts.get(c["change_type"], 0) + 1

    summary_parts = []
    if counts["NEW_ENTRY"]:
        summary_parts.append(f"{counts['NEW_ENTRY']} new")
    if counts["DROPPED"]:
        summary_parts.append(f"{counts['DROPPED']} dropped")
    if counts["SCORE_UP"]:
        summary_parts.append(f"{counts['SCORE_UP']} score↑")
    if counts["SCORE_DOWN"]:
        summary_parts.append(f"{counts['SCORE_DOWN']} score↓")
    if counts["LABEL_CHANGE"]:
        summary_parts.append(f"{counts['LABEL_CHANGE']} relabeled")
    summary = ", ".join(summary_parts) if summary_parts else "no significant changes"

    return {
        "version": VERSION,
        "generated_at": now,
        "system_mode": SYSTEM_MODE,
        "research_only": True,
        "current_generated_at": current.get("generated_at"),
        "previous_generated_at": previous.get("generated_at"),
        "first_run": False,
        "changes": changes,
        "change_counts": counts,
        "summary": summary,
        "guardrails": {"no_trade_recommendation": True, "diagnostic_only": True},
    }


def _format_text(result: Dict[str, Any]) -> str:
    lines = [
        RESEARCH_ONLY_BANNER,
        "",
        f"RESEARCH CHANGE DETECTOR  [{result['version']}]",
        f"Generated:  {result['generated_at']}",
        f"Current:    {result.get('current_generated_at', 'unknown')}",
        f"Previous:   {result.get('previous_generated_at', 'none')}",
        "",
        f"Summary: {result['summary']}",
        "",
    ]

    counts = result.get("change_counts", {})
    for ct in ("NEW_ENTRY", "DROPPED", "SCORE_UP", "SCORE_DOWN", "LABEL_CHANGE"):
        n = counts.get(ct, 0)
        if n:
            lines.append(f"  {ct:<16}  {n}")

    for change_type in ("NEW_ENTRY", "DROPPED", "SCORE_UP", "SCORE_DOWN", "LABEL_CHANGE"):
        items = [c for c in result.get("changes", []) if c["change_type"] == change_type]
        if not items:
            continue
        lines += ["", f"=== {change_type} ({len(items)}) ==="]
        for c in items[:20]:
            t = c["ticker"]
            if change_type == "NEW_ENTRY":
                lines.append(f"  {t:<6}  score={c.get('current_score', '?'):>5}  [{c.get('current_label', '?')}]  cat={c.get('category','?')}")
            elif change_type == "DROPPED":
                lines.append(f"  {t:<6}  was score={c.get('previous_score', '?'):>5}  [{c.get('previous_label', '?')}]")
            else:
                delta_str = f"{c.get('score_delta', 0):+.1f}"
                lines.append(
                    f"  {t:<6}  {c.get('previous_score', '?'):>5} → {c.get('current_score', '?'):>5}  ({delta_str})"
                    + (f"  label: {c.get('previous_label')} → {c.get('current_label')}" if c.get("previous_label") != c.get("current_label") else "")
                )

    lines += ["", "--- RESEARCH ONLY — NO TRADE RECOMMENDATIONS ---"]
    return "\n".join(lines)


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Research Change Detector (research-only)")
    parser.add_argument("--no-rotate", action="store_true",
                        help="Detect changes but do not rotate current→prev")
    args = parser.parse_args()

    print(RESEARCH_ONLY_BANNER)

    current = _load_json(SCANNER_LATEST)
    if current is None:
        print("ERROR: research_scanner_latest.json not found. Run research-scanner first.")
        sys.exit(1)

    previous = _load_json(SCANNER_PREV)
    result = detect_changes(current, previous)

    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    cfg.LOG_DIR.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(result, indent=2), encoding="utf-8")
    OUT_TXT.write_text(_format_text(result), encoding="utf-8")

    if not args.no_rotate:
        shutil.copy2(str(SCANNER_LATEST), str(SCANNER_PREV))
        logger.info("Rotated current → prev: %s", SCANNER_PREV)

    logger.info("wrote %s", OUT_JSON)

    print(f"\nChange detection complete.")
    print(f"Summary: {result['summary']}")
    counts = result["change_counts"]
    print(f"NEW={counts['NEW_ENTRY']}  DROPPED={counts['DROPPED']}  SCORE_UP={counts['SCORE_UP']}  SCORE_DOWN={counts['SCORE_DOWN']}  RELABELED={counts['LABEL_CHANGE']}")
    print(f"Artifact: {OUT_JSON}")


if __name__ == "__main__":
    main()

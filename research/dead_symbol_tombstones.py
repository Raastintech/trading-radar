#!/usr/bin/env python3
"""
research/dead_symbol_tombstones.py — append-only dead-symbol ledger (P2 hygiene).

Problem: delisted / renamed / invalid tickers linger in the scan universe,
fail every FMP refresh (``fmp_empty``), and burn a provider call + a scanner
skip each run.  This module keeps an *append-only* event ledger and derives
per-ticker state from it, so persistently unrefreshable symbols can be
excluded from refresh planning and scanner universe construction — but only
after repeated, dated evidence.  One transient failure never tombstones.

Ledger:  data/research/dead_symbol_tombstones.jsonl   (append-only events)
Events:  {"ticker","event","reason","at","provider_status"}
         event ∈ {failure, success, review}

Derived state per ticker:
  first_failure / last_failure / failure_count / distinct_failure_days
  provider_status  (last recorded, e.g. fmp_empty / transport_error)
  status           WATCH        — has failures, below threshold
                   CONFIRMED_DEAD — ≥ DEAD_THRESHOLD_DAYS distinct days of
                                    hard failures with no success since
  review_status    none | reinstated   ("review" events with
                   {"decision": "reinstate"} force-revive a ticker)

Doctrine:
  * Append-only — nothing here mutates or deletes history.
  * Hard failures = provider returned no data (fmp_empty / not_found).
    Transport/exception failures are recorded but never count toward death.
  * A success (bars returned) after failures revives the ticker: state
    resets from the success forward.
  * RESEARCH-ONLY: no DB writes, no signals, no execution.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

logger = logging.getLogger("dead_symbol_tombstones")

_REPO = Path(__file__).resolve().parent.parent
LEDGER_PATH = _REPO / "data" / "research" / "dead_symbol_tombstones.jsonl"

# Confirmed dead only after hard failures on this many DISTINCT days.
DEAD_THRESHOLD_DAYS = 3

# Provider statuses that count as hard "no such data" evidence.
# "no_bar_for_session": the provider answered but its latest bar remains
# behind the required session after a refresh — the delisted pattern (FMP
# keeps serving the stale tail forever, so fmp_empty never fires).  A
# multi-day trading halt can accumulate this too; the operator
# record_review(..., "reinstate") path covers resumptions.
HARD_FAILURE_STATUSES = {"fmp_empty", "not_found", "no_date_column",
                         "no_bar_for_session"}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append(record: Dict[str, Any], ledger_path: Optional[Path] = None) -> None:
    path = ledger_path or LEDGER_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


def record_failure(ticker: str, provider_status: str, reason: str = "",
                   ledger_path: Optional[Path] = None) -> None:
    """Record one refresh failure. ``provider_status`` should be a stable
    slug (e.g. ``fmp_empty`` for no-data, ``transport_error`` for exceptions
    — only HARD_FAILURE_STATUSES count toward tombstoning)."""
    _append({"ticker": ticker.upper(), "event": "failure",
             "provider_status": provider_status, "reason": reason,
             "at": _utc_now_iso()}, ledger_path)


def record_success(ticker: str, ledger_path: Optional[Path] = None) -> None:
    """Record a successful refresh — revives any prior failure streak."""
    _append({"ticker": ticker.upper(), "event": "success",
             "at": _utc_now_iso()}, ledger_path)


def record_review(ticker: str, decision: str, note: str = "",
                  ledger_path: Optional[Path] = None) -> None:
    """Operator review event. ``decision='reinstate'`` force-revives."""
    _append({"ticker": ticker.upper(), "event": "review",
             "decision": decision, "note": note,
             "at": _utc_now_iso()}, ledger_path)


def _read_events(ledger_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    path = ledger_path or LEDGER_PATH
    if not path.exists():
        return []
    events: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except Exception:
            continue  # never let one bad line poison the ledger read
    return events


def derive_states(ledger_path: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """Fold the event ledger into per-ticker state (see module docstring)."""
    states: Dict[str, Dict[str, Any]] = {}
    for ev in _read_events(ledger_path):
        t = str(ev.get("ticker") or "").upper()
        if not t:
            continue
        st = states.setdefault(t, {
            "ticker": t, "first_failure": None, "last_failure": None,
            "failure_count": 0, "hard_failure_days": [],
            "provider_status": None, "review_status": "none",
        })
        kind = ev.get("event")
        at = str(ev.get("at") or "")
        if kind == "failure":
            st["failure_count"] += 1
            st["last_failure"] = at
            if st["first_failure"] is None:
                st["first_failure"] = at
            st["provider_status"] = ev.get("provider_status")
            if ev.get("provider_status") in HARD_FAILURE_STATUSES:
                day = at[:10]
                if day and day not in st["hard_failure_days"]:
                    st["hard_failure_days"].append(day)
        elif kind == "success":
            # Revive: reset the streak from here forward.
            st.update({"first_failure": None, "last_failure": None,
                       "failure_count": 0, "hard_failure_days": [],
                       "provider_status": "ok"})
        elif kind == "review" and ev.get("decision") == "reinstate":
            st["review_status"] = "reinstated"
            st["hard_failure_days"] = []
            st["failure_count"] = 0

    for st in states.values():
        st["distinct_failure_days"] = len(st["hard_failure_days"])
        dead = (st["distinct_failure_days"] >= DEAD_THRESHOLD_DAYS
                and st["review_status"] != "reinstated")
        st["status"] = "CONFIRMED_DEAD" if dead else (
            "WATCH" if st["failure_count"] > 0 else "OK")
    return states


def confirmed_dead(ledger_path: Optional[Path] = None) -> Set[str]:
    """Tickers with CONFIRMED_DEAD status — safe to exclude from refresh
    planning and scanner universe construction."""
    return {t for t, st in derive_states(ledger_path).items()
            if st["status"] == "CONFIRMED_DEAD"}


def summary(ledger_path: Optional[Path] = None) -> Dict[str, Any]:
    """Compact summary for sidecars / dashboards."""
    states = derive_states(ledger_path)
    dead = sorted(t for t, s in states.items() if s["status"] == "CONFIRMED_DEAD")
    watch = sorted(t for t, s in states.items() if s["status"] == "WATCH")
    return {
        "ledger_path": str((ledger_path or LEDGER_PATH)),
        "confirmed_dead_count": len(dead),
        "confirmed_dead": dead[:100],
        "watch_count": len(watch),
        "watch_sample": watch[:25],
        "dead_threshold_days": DEAD_THRESHOLD_DAYS,
    }

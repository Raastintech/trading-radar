#!/usr/bin/env python3
"""
scripts/run_paper_evidence.py — Historical paper-evidence resolver (ARCHIVED).

Phase 3B (2026-06-14): Paper trading and holdout validation are permanently
archived.  Alpaca is NOT retained for paper trading.  No new paper signals
are generated (PAPER_TRADING_ENABLED=False in core.research_mode).

This script continues to run as a HISTORICAL RESOLVER ONLY:
  - Resolves existing paper_signals and voyager_paper_signals outcome rows
    from cached price parquets (AlpacaClient stub — no network calls).
  - Writes a scoreboard for historical records.
  - Does NOT generate new paper signals.
  - Does NOT require Alpaca credentials.
  - Does NOT place broker orders.

Run order:
  1. Resolve tactical SNIPER_V6 / SHORT_A outcomes from existing rows.
  2. Resolve VOYAGER 30d / 90d / 180d outcomes from the legacy ledger.
  3. Refresh the unified paper scoreboard report.
  4. Write one ops-friendly status JSON.

To confirm no new signals are possible:
  from core.research_mode import PAPER_TRADING_ENABLED
  assert PAPER_TRADING_ENABLED is False
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

cred = os.environ.get("SNIPER_ENV_PATH", "/home/gem/secure/trading.env")
if Path(cred).exists():
    try:
        from dotenv import load_dotenv

        load_dotenv(cred, override=True)
    except ImportError:
        pass

import core.config as cfg
from research.paper_trades.paper_scoreboard import print_scoreboard
from research.paper_trades.resolve_tactical_outcomes import resolve_tactical_outcomes
from research.paper_trades.voyager_paper_report import load_signals as load_voyager_signals
from research.paper_trades.voyager_paper_report import update_outcomes as update_voyager_outcomes

LOG_DIR = cfg.LOG_DIR
STATUS_PATH = LOG_DIR / "paper_evidence_status.json"
SCOREBOARD_PATH = LOG_DIR / "paper_scoreboard_latest.txt"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("paper_evidence")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_status(status: Dict[str, Any]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(status, indent=2, sort_keys=True) + "\n")


def _previous_status() -> Dict[str, Any]:
    if not STATUS_PATH.exists():
        return {}
    try:
        return json.loads(STATUS_PATH.read_text())
    except Exception:
        return {}


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    started_at = _utc_now()
    previous = _previous_status()
    status: Dict[str, Any] = {
        "job": "paper_evidence",
        "phase": "ARCHIVED_FOR_HISTORY_ONLY",
        "paper_trading_enabled": False,
        "new_signals_possible": False,
        "alpaca_required": False,
        "started_at": started_at,
        "finished_at": None,
        "last_success_at": previous.get("last_success_at"),
        "last_failure_at": previous.get("last_failure_at"),
        "ok": False,
        "resolver_ok": False,
        "voyager_resolver_ok": False,
        "scoreboard_ok": False,
        "resolver_result": {},
        "voyager_outcomes_updated": 0,
        "scoreboard_path": str(SCOREBOARD_PATH),
        "db_path": str(cfg.DB_PATH),
        "error": "",
    }

    logger.info("Paper evidence job starting. db=%s", cfg.DB_PATH)

    try:
        resolver_result = resolve_tactical_outcomes()
        status["resolver_result"] = resolver_result
        status["resolver_ok"] = True
        logger.info("Tactical resolver complete: %s", resolver_result)

        voyager_signals = load_voyager_signals(cfg.DB_PATH)
        with contextlib.redirect_stdout(open(os.devnull, "w")):
            voyager_updated = update_voyager_outcomes(voyager_signals, cfg.DB_PATH)
        status["voyager_outcomes_updated"] = voyager_updated
        status["voyager_resolver_ok"] = True
        logger.info(
            "Voyager resolver complete: %d signal(s) seen, %d outcome(s) updated",
            len(voyager_signals), voyager_updated,
        )

        with SCOREBOARD_PATH.open("w") as fh, contextlib.redirect_stdout(fh):
            print_scoreboard(cfg.DB_PATH)
        status["scoreboard_ok"] = True
        logger.info("Scoreboard refreshed: %s", SCOREBOARD_PATH)

        status["ok"] = True
        return_code = 0
    except Exception as exc:
        status["error"] = f"{type(exc).__name__}: {exc}"
        logger.error("Paper evidence job failed: %s", exc)
        logger.debug(traceback.format_exc())
        return_code = 1
    finally:
        finished_at = _utc_now()
        status["finished_at"] = finished_at
        if status["ok"]:
            status["last_success_at"] = finished_at
        else:
            status["last_failure_at"] = finished_at
        _write_status(status)
        logger.info("Paper evidence status written: %s", STATUS_PATH)

    return return_code


if __name__ == "__main__":
    raise SystemExit(main())

"""
scripts/snapshot_broker_positions.py — Phase 1E broker-snapshot diagnostic.

Read-only, operator-invoked tool. Pulls the current open positions from
Alpaca and writes a JSON sidecar that downstream cache-only reports
(notably ``research/paper_state_hygiene_report.py``) can use to fill the
``broker_position_match`` field on legacy quarantine entries.

Why a separate script:
  - The risk-telemetry workflow is doctrine cache-only. We do NOT add a
    provider call there. This script is run explicitly when the operator
    wants a fresh broker view.
  - Read-only on Alpaca (``get_positions``). No DB mutation. No order
    submission. No live-capital gate involvement.

Output:
  cache/state/broker_positions_snapshot.json

Atomic write via tmp-file + rename so concurrent dashboard reads are
safe.

Usage:
  SNIPER_ENV_PATH=/home/gem/secure/trading.env \
    .venv/bin/python scripts/snapshot_broker_positions.py [--print]

Exit codes:
  0   snapshot written (even if zero positions)
  1   provider error during fetch (sidecar NOT updated)
  2   environment / config error (no creds)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Standard env-load pattern used by every credential-requiring tool.
_env_path = os.environ.get("SNIPER_ENV_PATH", "")
if _env_path:
    try:
        from dotenv import load_dotenv as _load
        _load(Path(_env_path), override=True)
    except ImportError:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("snapshot_broker_positions")


from core.broker_snapshot import (  # noqa: E402 — env load above must run first
    SIDECAR_REL_PATH,
    default_sidecar_path as _default_sidecar_path,
    write_snapshot as _write_snapshot,
)


def fetch_snapshot() -> List[Dict[str, Any]]:
    """Call Alpaca once. Raises on import / instantiation errors so the
    caller can map to exit-code 2; provider exceptions propagate so the
    caller can map to exit-code 1."""
    from core.alpaca_client import get_alpaca
    alpaca = get_alpaca()
    return alpaca.get_positions() or []


def write_sidecar(
    positions: List[Dict[str, Any]],
    sidecar_path: Path,
) -> Dict[str, Any]:
    """Thin wrapper kept for back-compat with any external callers."""
    return _write_snapshot(positions, sidecar_path)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Snapshot broker positions to a cache sidecar (read-only)"
    )
    parser.add_argument("--out", default=None,
                        help=f"Override sidecar path (default: {SIDECAR_REL_PATH})")
    parser.add_argument("--print", action="store_true",
                        help="Print one ticker / qty line per position to stdout")
    args = parser.parse_args(argv)

    sidecar_path = Path(args.out) if args.out else _default_sidecar_path()

    try:
        positions = fetch_snapshot()
    except ImportError as exc:
        logger.error("environment not configured: %s", exc)
        return 2
    except RuntimeError as exc:
        # core.config raises RuntimeError on missing required env vars.
        logger.error("config error: %s", exc)
        return 2
    except Exception as exc:
        logger.error("alpaca fetch failed: %s", exc)
        return 1

    payload = write_sidecar(positions, sidecar_path)
    logger.info("wrote %s  (count=%d)", sidecar_path, payload["count"])

    if args.print:
        for p in positions:
            print(f"  {p['ticker']:<8} qty={p['qty']}  side={p['side']}  "
                  f"px={p['entry_price']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

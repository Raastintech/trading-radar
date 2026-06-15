"""
core/broker_snapshot.py — atomic write of cache/state/broker_positions_snapshot.json.

Both ``scripts/snapshot_broker_positions.py`` (operator-invoked) and the
daemon (Fix 7, 2026-05-15 audit) use this module so the snapshot stays
fresh during market hours without violating the doctrine that
``run_research_cycle.sh risk-telemetry`` is cache-only.

The daemon already calls ``alpaca.get_positions()`` every 5 min for
position reconciliation; teeing the result to the sidecar costs nothing
extra and keeps Phase 1E broker-match enrichment off stale data.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import core.config as cfg


SIDECAR_REL_PATH = Path("cache") / "state" / "broker_positions_snapshot.json"


def normalize_position(p: Dict[str, Any]) -> Dict[str, Any]:
    """Match the Phase 1E shape that the hygiene report's
    broker-match enricher expects. Trimmed to the auditable fields —
    market_value / unrealized_pnl are visibility only and never feed
    an enforcement decision."""
    qty = p.get("qty")
    try:
        qty_f = float(qty) if qty is not None else None
    except (TypeError, ValueError):
        qty_f = None
    return {
        "ticker":         str(p.get("ticker") or "").upper(),
        "qty":            qty_f,
        "side":           str(p.get("side") or "").lower(),
        "entry_price":    p.get("entry_price"),
        "current_price":  p.get("current_price"),
        "market_value":   p.get("market_value"),
        "unrealized_pnl": p.get("unrealized_pnl"),
    }


def default_sidecar_path() -> Path:
    """Repo-root-relative default. Honors cfg.PROJECT_ROOT if present
    (falls back to the parent of this file)."""
    root = getattr(cfg, "PROJECT_ROOT", None)
    if root is None:
        root = Path(__file__).resolve().parents[1]
    return Path(root) / SIDECAR_REL_PATH


def write_snapshot(
    positions: List[Dict[str, Any]],
    sidecar_path: Path | None = None,
) -> Dict[str, Any]:
    """Atomic tmp-file + rename. Returns the payload that was written."""
    sidecar_path = sidecar_path or default_sidecar_path()
    payload: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source":       "alpaca.get_positions",
        "count":        len(positions),
        "positions":    [normalize_position(p) for p in positions],
    }
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = sidecar_path.with_suffix(sidecar_path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    tmp.replace(sidecar_path)
    return payload

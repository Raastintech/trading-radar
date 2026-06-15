"""
core/paper_evidence_epoch.py — Phase 1D clean-paper-evidence epoch.

Single source of truth for the timestamp at which paper-evidence rows
became reliable enough to feed Phase 1B risk telemetry. Rows logged
before this cutoff are *legacy* — they predate Phase 0 fill telemetry
and lack ``fill_price`` / ``fill_qty`` instrumentation.

Doctrine: legacy rows are NEVER deleted, mutated, or backfilled.
They are preserved as historical evidence and quarantined out of the
clean-epoch ready_to_gate verdict so a future promotion of any Phase 1B
warning to a hard gate is not blocked by pre-instrumentation debt.

Empirically chosen from the live ``decisions`` table:
  - Latest ``position_opened=1 AND fill_price IS NULL`` row: 2026-05-07
  - Earliest row WITH fill_price populated:                    2026-05-08
  - Cutoff is set to start-of-day 2026-05-08 UTC.

If the cutoff is ever revised, update this module and re-run Phase 1C
tests + the Phase 1D quarantine sidecar so coverage stays explicit.

This module intentionally has no external imports — every Phase 1B/1C/1D
report can import it freely without dragging in credentials.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


CLEAN_PAPER_EVIDENCE_START: str = "2026-05-08T00:00:00+00:00"
"""ISO-UTC timestamp at which the clean-paper-evidence epoch begins.

Rows with ``ts`` (decisions) or ``logged_at`` (paper_signals,
voyager_paper_signals) strictly less than this value are *legacy*.
"""

QUARANTINE_REASON_PRE_FILL_TELEMETRY: str = "pre_fill_telemetry_legacy"
"""Canonical quarantine reason for rows that predate Phase 0 fill
telemetry instrumentation."""


def is_legacy(ts_iso: Optional[str],
              cutoff_iso: str = CLEAN_PAPER_EVIDENCE_START) -> bool:
    """Return True if ``ts_iso`` predates the clean-epoch cutoff.

    A ``None`` / empty / unparseable timestamp is treated as legacy
    (conservative — we cannot prove the row belongs to the clean
    epoch, so we exclude it from the clean verdict).
    """
    if not ts_iso:
        return True
    try:
        ts = datetime.fromisoformat(str(ts_iso).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        cutoff = datetime.fromisoformat(cutoff_iso.replace("Z", "+00:00"))
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=timezone.utc)
        return ts < cutoff
    except (TypeError, ValueError):
        return True


__all__ = (
    "CLEAN_PAPER_EVIDENCE_START",
    "QUARANTINE_REASON_PRE_FILL_TELEMETRY",
    "is_legacy",
)

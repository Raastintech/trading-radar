#!/usr/bin/env python3
"""Artifact dependency audit helpers for cache-only research surfaces.

These helpers compare a downstream artifact's timestamp/source identity with
the latest scanner and latest-scan routing artifacts.  They do not run any
scanner, scoring, routing, qualification, or provider code.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

CURRENT_FOR_LATEST_SCAN = "CURRENT_FOR_LATEST_SCAN"
STALE_FOR_LATEST_SCAN = "STALE_FOR_LATEST_SCAN"
SOURCE_UNKNOWN = "SOURCE_UNKNOWN"


def parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _scan_hash(scanner: Dict[str, Any], routed: Dict[str, Any]) -> Optional[str]:
    integ = scanner.get("scan_integrity") or {}
    return (
        scanner.get("scan_universe_manifest_hash")
        or integ.get("manifest_hash")
        or routed.get("scanner_manifest_hash")
        or routed.get("manifest_universe_hash")
    )


def dependency_audit(
    *,
    artifact_kind: str,
    artifact_timestamp: Optional[str],
    scanner_doc: Optional[Dict[str, Any]] = None,
    routed_doc: Optional[Dict[str, Any]] = None,
    recorded_dependencies: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return a deterministic source-dependency verdict.

    ``recorded_dependencies`` is the source block written by an older
    downstream artifact.  When the current scanner/routing source no longer
    matches that recorded source, the artifact is stale even if a clock skew
    made timestamp ordering ambiguous.
    """
    scanner = scanner_doc or {}
    routed = routed_doc or {}
    recorded = recorded_dependencies or {}

    source_scan_ts = scanner.get("generated_at") or routed.get("scan_timestamp")
    source_routing_ts = routed.get("generated_at")
    source_scan_hash = _scan_hash(scanner, routed)
    source_routing_hash = (
        routed.get("scanner_manifest_hash")
        or routed.get("manifest_universe_hash")
    )

    artifact_dt = parse_ts(artifact_timestamp)
    scan_dt = parse_ts(source_scan_ts)
    routing_dt = parse_ts(source_routing_ts)
    stale_reasons: List[str] = []

    if artifact_dt and scan_dt and artifact_dt < scan_dt:
        stale_reasons.append(
            f"artifact {artifact_timestamp} older than source scan {source_scan_ts}"
        )
    if artifact_dt and routing_dt and artifact_dt < routing_dt:
        stale_reasons.append(
            "artifact "
            f"{artifact_timestamp} older than program routing {source_routing_ts}"
        )

    rec_scan_ts = recorded.get("source_scan_timestamp")
    if rec_scan_ts and source_scan_ts and rec_scan_ts != source_scan_ts:
        stale_reasons.append(
            f"recorded source scan {rec_scan_ts} differs from latest {source_scan_ts}"
        )
    rec_route_ts = recorded.get("source_program_routing_timestamp")
    if rec_route_ts and source_routing_ts and rec_route_ts != source_routing_ts:
        stale_reasons.append(
            "recorded program routing "
            f"{rec_route_ts} differs from latest {source_routing_ts}"
        )
    rec_scan_hash = recorded.get("source_scan_hash")
    if rec_scan_hash and source_scan_hash and rec_scan_hash != source_scan_hash:
        stale_reasons.append(
            f"recorded source scan hash {rec_scan_hash} differs from latest "
            f"{source_scan_hash}"
        )

    if stale_reasons:
        status = STALE_FOR_LATEST_SCAN
    elif artifact_dt and (scan_dt or routing_dt):
        status = CURRENT_FOR_LATEST_SCAN
    else:
        status = SOURCE_UNKNOWN

    return {
        "artifact_kind": artifact_kind,
        "artifact_timestamp": artifact_timestamp,
        "source_scan_timestamp": source_scan_ts,
        "source_scan_hash": source_scan_hash,
        "source_program_routing_timestamp": source_routing_ts,
        "source_program_routing_hash": source_routing_hash,
        "status": status,
        "stale_for_latest_scan": status == STALE_FOR_LATEST_SCAN,
        "stale_reasons": stale_reasons,
    }

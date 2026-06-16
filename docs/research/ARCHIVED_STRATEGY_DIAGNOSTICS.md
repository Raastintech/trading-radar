# Archived Strategy Diagnostics

**Phase 4A.2 — 2026-06-16**

This document lists legacy strategy diagnostic tools, reports, and references that
were intentionally excluded from the Daily Alpha Radar (DAILY_ALPHA_RADAR_REPORT.md)
as part of the Phase 4A.2 quality-hardening pass. They remain available as standalone
CLI commands and JSON sidecars; they are just no longer surfaced in the daily radar.

## Legacy Strategy Diagnostics (still available, not in daily radar)

| Tool / Report | CLI command | Sidecar |
|---|---|---|
| VOYAGER conversion audit | `voyager-audit` | `cache/research/voyager_conversion_audit_latest.json` |
| SNIPER emission gap audit | `emission-calibration` | `cache/research/scanner_emission_gap_latest.json` |
| SHORT_A opportunity radar | `short-radar` | `cache/research/short_opportunity_radar_latest.json` |
| SHORT_A detection audit | `short-detection-audit` | `cache/research/short_detection_audit_latest.json` |
| Strategy tournament | `strategy-tournament` | `cache/research/strategy_tournament_latest.json` |
| Holdout scoreboard | `holdout` | (via paper-evidence resolver) |
| Risk telemetry (slippage/concentration/shadow sizing) | `risk-telemetry` | `cache/research/slippage_telemetry_latest.json` etc. |
| Paper state hygiene | `risk-telemetry` | `cache/research/paper_state_hygiene_latest.json` |
| Broker snapshot | `scripts/snapshot_broker_positions.py` | `cache/state/broker_positions_snapshot.json` |
| Leader Reset event study | `leader-reset-study` | `cache/research/leader_reset_event_study_latest.json` |
| LRR (Leader Reset Reclaim) strategy suite | archived | preserved in research/ |

## Rationale

The Daily Alpha Radar is a RESEARCH_ONLY market intelligence surface for identifying
early-stage research candidates. The tools above are diagnostic artifacts for
specific (often frozen) strategy sleeves — they do not belong in a forward-looking
research radar and would create misleading signals if surfaced there.

They remain fully operational via their existing CLI commands and JSON sidecars.
The dashboard (Modes 1-3), MCP audit tools, and risk-telemetry readers continue
to consume them directly.

## Reference: Frozen Sleeves

- `SHORT_A` — frozen 2026-05-24 (Phase 1G.3), net-negative in bull tape
- `REMORA`, `CONTRARIAN`, `SHORT_B`, `PATHFINDER` — frozen, in registry
- `LRR` / Leader Reset Reclaim — research-only, did not pass promotion gates

Active sleeves (SNIPER, VOYAGER) are validated via their own scorcards in
`docs/strategy/` and `docs/scorecards/`, not via the daily radar.

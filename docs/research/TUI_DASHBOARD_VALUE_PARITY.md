# TUI ↔ Command Center Value Parity

Both the TUI (`dashboards/gem_trader_hq.py`) and the web Command Center read the **same
canonical cache artifacts**. Display wording may differ; the underlying value and its
timestamp must match. This report records the parity for every newly-wired metric.

## Same-source guarantee

| Metric | Canonical artifact | TUI reader | Dashboard reader |
|---|---|---|---|
| Regime / Forecast / Bias / Breadth / Volatility / Leadership | `cache/research/regime_forecast_latest.json` | `_market_forecast_*`, `_market_bias_badge` | `build_market_context` |
| Market Heartbeat | `cache/research/market_heartbeat_latest.json` | `_market_heartbeat_panel` | `build_market_context` |
| Moment of Truth | `cache/research/moment_of_truth_2026_06_27.json` | docs/panel | `build_research_confidence` |
| Program verdicts | `cache/research/research_program_validation_latest.json` | RESEARCH STATE | `build_research_programs` |
| Forward resolution | `cache/research/forward_resolution_health_latest.json` | — | `build_research_confidence` |
| Scan integrity | `research_scanner_latest.json.scan_integrity` | TRADE READINESS | `build_scan_integrity` |
| Options overlay | `cache/research/options_coverage_report_latest.json` | SCANNER SIGNALS | `build_system_trust` |
| Provider health | `cache/research/fmp_provider_health_latest.json` | — | `build_system_trust` |
| MCP audit state | `cache/research/mcp_analysis_latest.json` | MCP AUDIT SUMMARY | `build_system_trust` |

## Verified side-by-side (anchor 2026-07-10)

| Metric | Artifact value | Dashboard value | Match |
|---|---|---|---|
| Regime | `Bull Continuation` | Bull Continuation | ✓ |
| Forecast 5d | `constructive` | constructive | ✓ |
| Forecast confidence | `low` | low | ✓ |
| Heartbeat | `DEFENSIVE_ROTATION` | Defensive Rotation (title-cased display) | ✓ (value) |
| VIX | `15.03` | 15.03 | ✓ |
| Moment of Truth | `INCONCLUSIVE` | INCONCLUSIVE | ✓ |
| MCP state | `FRAGILE` | FRAGILE | ✓ |
| Forecast timestamp | `2026-07-11T00:30:04` | `2026-07-11T00:30:04` | ✓ |

## Intentional display differences (value preserved)

- **Heartbeat / regime labels** are title-cased for display (`DEFENSIVE_ROTATION` →
  "Defensive Rotation"); the canonical enum is unchanged in the payload.
- **Program verdicts / forward status** use clean display labels
  (`INSUFFICIENT_MATURE_EVIDENCE` → "Insufficient Evidence", `NEED_MORE_DATA` →
  "Forward Validation: In Progress") via the `termLabel` map; canonical enums preserved.
- **MCP state** is shown under System Trust → operations detail with an explicit
  "operational-only; not market data" qualifier — a placement difference, not a value
  difference.

## Stale behavior parity
Each metric exposes `freshness {generated_at, age_hours, stale, available}`. A metric older
than its threshold (30h for market artifacts; 30d for the slow-cadence Moment of Truth)
renders a `Stale` chip and is not presented as current — matching the TUI's staleness
guards. No market states from different sessions are silently blended.

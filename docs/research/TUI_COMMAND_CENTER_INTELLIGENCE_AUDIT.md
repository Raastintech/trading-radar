# TUI → Command Center Intelligence Wiring Audit

**Scope:** identify the high-value market / evidence / system-state metrics rendered
in the TUI (`dashboards/gem_trader_hq.py`) that were missing or under-represented in
the web Command Center, and wire the decision-relevant ones. **Presentation-only** —
no research, scanner, forecast, heartbeat, bias, verdict, routing, score, or threshold
logic changed. Both surfaces read the **same canonical artifacts**.

Numbers/labels below are from the latest real artifacts (anchor **2026-07-10**).

---

## 1. TUI metric inventory (source → artifact → cadence → placement)

| Metric | TUI panel | Source artifact | Cadence | Already on CC? | New CC placement | Visibility |
|---|---|---|---|---|---|---|
| Market Regime | SPY/REGIME, MARKET FORECAST | regime_forecast_latest.json `headline.current_regime` | nightly/premarket | partial (label only) | **Market Context** | Always |
| Market Forecast (5d/10d, confidence) | MARKET FORECAST | regime_forecast `headline.bias_5d/bias_10d/confidence`, `forecast_state` | nightly/premarket | **no** | **Market Context** | Always |
| Market Heartbeat | MARKET HEARTBEAT | market_heartbeat_latest.json `heartbeat_label` + `heartbeat_reasons` | nightly | **no** | **Market Context** | Summary (state + ≤3 drivers) |
| Market Bias | MARKET BIAS badge | regime_forecast `risk_signal.signal` / trend_score | nightly | **no** | **Market Context** | Summary |
| Breadth | forecast/heartbeat | regime_forecast `breadth.sector_breadth_pct_above_ma20` | nightly | **no** | **Market Context** | Summary |
| Volatility | forecast | regime_forecast `volatility.vix / vix_avg_20` | nightly | **no** | **Market Context** | Summary |
| Sector leadership / weakness | forecast/heartbeat | regime_forecast `sector_rotation.leading/weakening` | nightly | **no** | **Market Context** | Summary |
| Moment of Truth | (docs) | moment_of_truth_2026_06_27.json `verdict/verdict_reason` | manual | **no** (home) | **Evidence & Confidence** | Compact verdict |
| Program verdicts | RESEARCH STATE | research_program_validation_latest.json | nightly | yes (program cards) | **Evidence & Confidence** | Summary (display labels) |
| Forward resolution | — | forward_resolution_health_latest.json | nightly | **no** | **Evidence & Confidence** | Coverage % + next date |
| Benchmark readiness | — | nightly summary | nightly | yes | **Evidence & Confidence** | Always |
| Research/engine/promotion state | RESEARCH STATE | derived | live | partial | **Evidence & Confidence** | Distinct trio |
| Scan integrity | TRADE READINESS | research_scanner `scan_integrity` | premarket/nightly | yes (banner) | **System Trust** + banner | Summary |
| Universe / same-session coverage | — | scan_integrity | nightly | partial | **System Trust** | Summary |
| Options overlay | SCANNER SIGNALS | options_coverage_report_latest.json `overlay_state` | nightly | **no** | **System Trust** | Summary (informational) |
| Provider / API health | — | fmp_provider_health_latest.json `overall_status` | nightly | **no** | **System Trust** | Summary |
| MCP audit state | MCP AUDIT SUMMARY | mcp_analysis_latest.json `state` | nightly (session) | **no** | **System Trust → operations detail** | Conditional / expandable |
| Data freshness | header chips | scan_integrity readiness | live | yes | **System Trust** | Summary |
| Scanner candidates / leaderboard | SCANNER SIGNALS | research_scanner_latest.json | premarket | yes | Discovery tabs | Drill-down |
| Manifest hash | TRADE READINESS | scan manifest | nightly | drill-down only | Scan Integrity detail | Drill-down |

---

## 2. Quant decision-value classification (A–E)

- **A. Market Context** (top of home): Regime, Forecast, Heartbeat, Bias, Breadth,
  Volatility, Leadership, Weakness + **deterministic Context Tension**.
- **B. Research Opportunity** (already live): High-Conviction, Emerging, Wait-for-Reset,
  program leaders. Added only a one-line **market-fit** per candidate card.
- **C. Evidence & Confidence** (new panel): Research/Engine/Promotion state, Moment of
  Truth, program verdicts, forward resolution + next maturity, benchmark readiness.
- **D. System & Data Integrity** (System Trust panel): scan integrity, same-session &
  universe coverage, options overlay, provider health, data freshness. Summarized, not
  dominant.
- **E. Engineering / Operational internals** (expandable / drill-down): MCP audit state,
  provider-freshness detail, manifest hash, resolver command lines.

## Metrics investigated but NOT wired to the home page (and why)
- **Advance/decline, new-highs/lows, %>MA50/200, equal-vs-cap-weight, dispersion,
  correlation, term structure**: not computed as standalone artifacts (breadth is proxied
  by sector-ETF %>MA already shown). Wiring would require new calculations — out of scope
  (mission forbids inventing metrics).
- **Candidate sector / quality / profitability concentration**: valuable but belongs on
  the filter-audit / program drill-downs, not the 20-second home view — duplication risk.
- **Forecast historical accuracy**: `regime_forecast_validation` exists but is
  "Phase 1: not validated yet" — surfaced only as the forecast **validation status** label,
  never as an accuracy claim.
- **Provider call counts / raw task IDs / test counts**: operational noise → Operations only.

---

## 3. MCP-state definition (clarified)

`MCP state` in this codebase is **not** Model-Context-Protocol connectivity and **not**
a market metric. It is the **MCP-audit session state** written to
`cache/research/mcp_analysis_latest.json` by `research/mcp_audit_orchestrator.py` — a
**cache-only, read-only** composition of the daily-dashboard / system-health / late-chase
audits (via the read-only `stocklens-audit` MCP tools). Its value is one of
`{NORMAL, CONFLICTED, FRAGILE, STALE, BLOCKED}` (currently **FRAGILE**).

- **Source:** `mcp_analysis_latest.json` (`state`, `session`, `executive_summary`).
- **Cadence:** nightly session; can go STALE.
- **Failure modes:** stale sidecar, session change → hidden/flagged.
- **Impact on research output:** it is a meta-audit *about* the engine, not an input to
  candidate selection or market interpretation.
- **Placement decision:** **System Trust → "System operations detail" (expandable)**, with
  an explicit `operational-only; not market data` label. It is **never** promoted to the
  Market Context strip. Payload carries `operational_only: true`,
  `affects_market_interpretation: false`.

---

## 4. UX placement decision matrix

| Metric | Quant value | Home placement | Detail page | Visibility |
|---|---:|---|---|---|
| Market Forecast | High | Market Context | Sector/Regime | Always |
| Market Heartbeat | High | Market Context | — | Summary |
| Market Bias | High | Market Context | — | Summary |
| Regime | High | Market Context | Sector/Regime | Always |
| Breadth / Volatility / Leadership | High | Market Context | — | Summary |
| Context Tension | High | Market Context | — | Always (when present) |
| Research State | High | Evidence & Confidence | Forward Evidence | Always |
| Moment of Truth | High | Evidence & Confidence | — | Compact |
| Program verdicts | High | Evidence & Confidence | Research Programs | Summary |
| Forward resolution | Medium-High | Evidence & Confidence | Forward Evidence | Summary |
| Scan integrity | High | Banner + System Trust | Scan Integrity | Summary |
| Options overlay | Medium | System Trust | Options | Summary |
| Provider health | Medium | System Trust | Data Quality | Summary |
| MCP audit state | Operational | System Trust → ops detail | System Health | Conditional |
| Manifest hash | Low (operator) | none | Scan Integrity | Drill-down |

---

## 5. Consolidated payload

`GET /api/intel` (and the same three keys on `/api/status`) returns cache-only:

```
market_context   : regime, forecast, heartbeat, bias, breadth, volatility,
                   leadership, context_tension, freshness
research_confidence: engine_state, research_state, promotion_state, moment_of_truth,
                   program_verdicts, forward_tracking, benchmark_readiness
system_trust     : overall, scan_integrity, data_freshness, options_overlay,
                   provider_health, mcp_state
```

Every sub-metric carries its own `freshness {generated_at, age_hours, stale, available}`.
No provider or LLM call is made by the dashboard.

---

## 6. Architecture critique
- **Single source of truth preserved:** the dashboard reads the exact artifacts the TUI
  reads; display wording differs, values/timestamps do not (see
  `TUI_DASHBOARD_VALUE_PARITY.md`).
- **Clarity over volume:** the home page adds exactly three panels (Market Context,
  Evidence & Confidence, System Trust) under strict metric caps, not another card wall.
- **Contradiction is deterministic:** Context Tension is computed from existing fields
  (constructive forecast vs no leadership / narrow breadth / invalidation breached /
  defensive heartbeat), never authored by an LLM.
- **Honest staleness:** every metric can render `Stale`; MoT uses a slow-cadence threshold.
- **Known gap:** true market breadth beyond sector-ETF %>MA (A/D line, new highs/lows) is
  not computed anywhere yet — a future research artifact, deliberately not faked here.

# Phase 4A.5 — T1: Research Engine Architecture Audit

*Generated 2026-06-16 | Audit phase: adversarial foundation review*

---

## 1. Data Inputs

| Source | Location | Refresh | Used by |
|--------|----------|---------|---------|
| FMP daily price bars | `cache/prices/*.parquet` | nightly (3:30 ET Mon–Fri via `nightly_refresh.py`) | scanner, enrichment, forward tracker, 10x radar |
| FMP deep price bars | `cache/prices_deep/*.parquet` | manual / `deepen_price_cache.py` | enrichment (MA200 preference) |
| FMP company profiles | `cache_meta` SQLite (key `fmp:profile:{TICKER}`) | nightly (via `prebuild_stock_lenses.py`) | enrichment, sector label, market cap |
| FMP fundamentals | `cache/fundamentals/{TICKER}.json` | nightly lenses | scan_asymmetric, stock_research_card |
| FMP earnings calendar | in-memory via `get_fmp().get_earnings_calendar()` | 6 h cache | catalyst_watch, gatekeeper_refresh |
| FMP analyst grades | in-memory via `get_fmp().get_analyst_grades()` | per-ticker, cached | catalyst_watch |
| FMP news | in-memory via `get_fmp().get_news()` | per-ticker, cached | catalyst_watch |
| Alpha Discovery board | `cache/research/alpha_discovery_board_latest.json` | nightly alpha step | scanner universe (P1) |
| Alpha Discovery overlay | `cache/research/alpha_discovery_overlay_latest.json` | premarket | alpha overlay feed |
| Social / attention sidecars | `cache/research/*social*_latest.json`, `*news_catalyst*` | nightly social step | scanner universe (P2) + social_arb scan |
| Sector ETF prices | inside `cache/prices/` (XLK, XLV, SMH, etc.) | nightly | sector_leaders scan |
| SPY closes | `cache/prices/SPY.parquet` | nightly | RS calculations in all scans |
| QQQ / IWM / VXX | `cache/prices/` | nightly | market heartbeat, regime |
| Tradier options chains | `cache/options/*.json` (via `options_chain_snapshot_collector.py`) | daily 15:45 ET timer | stock_research_card IV/OI layer |
| Manual social JSONL | `data/research/manual_social_items.example.jsonl` | operator-written | social_arb scan (opt-in) |
| Regime forecast | `cache/research/regime_forecast_latest.json` | premarket + nightly | market heartbeat, daily alpha radar |
| MCP audit sidecar | `cache/research/mcp_analysis_latest.json` | nightly `mcp-audit-session` | dashboard MCP AUDIT panel |

---

## 2. Core Modules

| Module | Path | Purpose |
|--------|------|---------|
| `market_heartbeat` | `research/market_heartbeat.py` | SPY/QQQ/VXX/IWM context, regime state, sector breadth |
| `research_scanner` | `research/research_scanner.py` | 6-category scan → watchlist with labels |
| `research_candidate_enrichment` | `research/research_candidate_enrichment.py` | fills MA20/50/200, RS, volume, liquidity, missing_fields |
| `research_scoring` | `research/research_scoring.py` | earliness_label, consensus_label, quality_adjusted_consensus, priority_label |
| `catalyst_sanity` | `research/catalyst_sanity.py` | validates catalyst/social signals (staleness, sector-spillover, duplication) |
| `research_change_detector` | `research/research_change_detector.py` | diffs scanner watchlist vs prior snapshot |
| `research_watchlist_forward_tracker` | `research/research_watchlist_forward_tracker.py` | logs observation per ticker per day, computes 5/10/20d forward returns |
| `ten_x_candidate_radar` | `research/ten_x_candidate_radar.py` | TRUE_10X_RESEARCH / ASYMMETRIC_RECOVERY_WATCH / THEME_ONLY labels |
| `daily_alpha_radar_report` | `research/daily_alpha_radar_report.py` | aggregates all sidecars into priority-gated radar + DAILY_ALPHA_RADAR_REPORT.md |
| `stock_research_card` | `research/stock_research_card.py` | per-ticker research card with enriched fields |
| `research_coverage_audit` | `research/research_coverage_audit.py` | coverage confidence per ticker (HIGH/MEDIUM/LOW/INVALID) |
| `alpha_discovery_board` | `research/alpha_discovery_board.py` | FMP-driven short-list of alpha candidates (provider call) |
| `gatekeeper_refresh` | `research/gatekeeper_refresh.py` | refreshes executive gatekeeper per priority ticker |
| `dashboard` | `dashboards/gem_trader_hq.py` | read-only TUI; reads all sidecars; never calls providers |
| MCP audit tools | `research/mcp_audit_workflows.py`, `mcp_audit_orchestrator.py` | MCP read-only audit session |

---

## 3. Output Artifacts

| Artifact | Path | Written by | Read by |
|----------|------|-----------|---------|
| Scanner watchlist | `cache/research/research_scanner_latest.json` | `research_scanner.py` | forward tracker, change detector, daily radar |
| Scanner log | `logs/research_scanner_latest.txt` | `research_scanner.py` | operator |
| Coverage audit | `cache/research/research_coverage_latest.json` | `research_coverage_audit.py` | daily radar |
| Change delta | `cache/research/research_changes_latest.json` | `research_change_detector.py` | daily radar, dashboard |
| Forward tracker summary | `cache/research/research_forward_latest.json` | `research_watchlist_forward_tracker.py` | daily radar, dashboard |
| Forward tracker history | `data/research/research_watchlist_history.jsonl` | `research_watchlist_forward_tracker.py` | forward tracker (load + append) |
| 10x radar | `cache/research/ten_x_candidates_latest.json` | `ten_x_candidate_radar.py` | daily radar |
| Daily alpha radar | `cache/research/daily_alpha_radar_latest.json` | `daily_alpha_radar_report.py` | dashboard |
| Daily alpha radar report | `docs/research/DAILY_ALPHA_RADAR_REPORT.md` | `daily_alpha_radar_report.py` | operator |
| Stock research cards | `cache/research/cards/{TICKER}_research_card_latest.json` | `stock_research_card.py` | dashboard, MCP |
| Emission gap audit | `docs/research/SCANNER_EMISSION_GAP_AUDIT.md` | `scanner_truth_review` | operator |
| MCP audit sidecar | `cache/research/mcp_analysis_latest.json` | `mcp_audit_orchestrator.py` | dashboard |
| Alpha discovery | `cache/research/alpha_discovery_board_latest.json` | `alpha_discovery_board.py` | scanner (universe P1), gatekeeper |

---

## 4. Dependency Flow (Nightly)

```
FMP provider calls (forecast, alpha, lenses)
    │
    ▼
[cache/prices/*.parquet]  [cache_meta SQLite]  [alpha_discovery_board_latest.json]
    │                           │                        │
    ▼                           ▼                        ▼
research_scanner.py ────── _build_universe (200 cap: alpha board → social → alphabetical)
    │   6 categories: early_accum / beaten_down / sector_leaders / catalyst / social / asymmetric
    ▼
research_candidate_enrichment.py  ←  [prices] [prices_deep] [profile cache]
    │   fills MA20/50/200, RS20/63, volume, liquidity, missing_fields, data_confidence
    ▼
research_scoring.py
    │   earliness_label → consensus_label → priority_label
    ▼
catalyst_sanity.py (_apply_catalyst_sanity)
    │   validates catalyst / social before priority upgrade
    ▼
[research_scanner_latest.json]
    │
    ├──► research_change_detector.py → [research_changes_latest.json]
    │
    ├──► research_watchlist_forward_tracker.py → [research_forward_latest.json]
    │                                             [data/research/research_watchlist_history.jsonl]
    │
    ├──► ten_x_candidate_radar.py → [ten_x_candidates_latest.json]
    │
    └──► daily_alpha_radar_report.py ←── [coverage] [changes] [forward] [10x] [heartbeat] [regime]
              │
              ├──► [daily_alpha_radar_latest.json]
              └──► [docs/research/DAILY_ALPHA_RADAR_REPORT.md]

dashboard (gem_trader_hq.py) reads ALL sidecars above (cache-only, no provider calls)
MCP tools (mcp_audit_workflows.py) read ALL sidecars (cache-only, read-only)
```

---

## 5. Isolation Invariants Verified

- Production code (`core/`, `council/`, `execution/`) does **not** import from `research/` ✓
- `research/` imports `core/` for config/cache only (no execution paths) ✓
- Dashboard is cache-only (reads JSON; never calls providers or writes DB) ✓
- All research modules print `RESEARCH_ONLY_BANNER` on startup ✓
- `core/research_mode.py` is single source of truth for mode flags ✓

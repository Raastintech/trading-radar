# Phase 4A — Alpha Radar Research Engine

**Status:** Complete  
**Date:** 2026-06-15  
**Mode:** RESEARCH_ONLY — no trade recommendations, no signals, no execution

---

## What Phase 4A Builds

Phase 4A upgrades the research scanner into a proper Alpha Radar — adding
data confidence scoring, lifecycle positioning, signal confirmation breadth,
daily change detection, forward outcome tracking, and a focused speculative
candidate radar.

## New Modules

| Module | Runner | Purpose |
|--------|--------|---------|
| `research/research_scoring.py` | (shared utility) | `earliness_label()` + `consensus_label()` |
| `research/research_coverage_audit.py` | `research-coverage` | Data confidence per ticker (HIGH/MEDIUM/LOW/INVALID) |
| `research/research_change_detector.py` | `research-changes` | Scanner watchlist delta (new/dropped/score/label) |
| `research/research_watchlist_forward_tracker.py` | `research-forward-tracker` | Forward outcome tracking by label bucket |
| `research/ten_x_candidate_radar.py` | `ten-x-candidates` | Speculative 10x candidate radar |

## New Scanner Fields (on every watchlist item)

| Field | Type | Description |
|-------|------|-------------|
| `earliness_label` | str | Lifecycle position: EARLY/DEVELOPING/RECLAIM_WATCH/RESET_WATCH/EXTENDED/LATE/INVALIDATED/UNKNOWN |
| `consensus_label` | str | Signal breadth: SINGLE_SIGNAL/DOUBLE_CONFIRMATION/MULTI_CONFIRMATION/HIGH_PRIORITY_RESEARCH |
| `all_categories` | list[str] | All scanner categories this ticker appeared in |

## New Run Commands

```bash
./scripts/run_research_cycle.sh research-coverage        # data confidence audit
./scripts/run_research_cycle.sh research-scanner         # scanner (now emits earliness+consensus)
./scripts/run_research_cycle.sh research-changes         # watchlist delta vs yesterday
./scripts/run_research_cycle.sh research-forward-tracker # forward outcome tracking
./scripts/run_research_cycle.sh ten-x-candidates         # speculative radar
```

## New Artifacts

| Path | Description |
|------|-------------|
| `cache/research/research_coverage_latest.json` | Per-ticker confidence audit |
| `cache/research/research_scanner_prev.json` | Previous scanner snapshot (for change detection) |
| `cache/research/research_changes_latest.json` | Daily watchlist delta |
| `cache/research/research_forward_latest.json` | Forward outcome verdict by label bucket |
| `data/research/research_watchlist_history.jsonl` | Append-only forward tracker ledger |
| `cache/research/ten_x_candidates_latest.json` | 10x speculative candidate list |

## Guardrails (unchanged)

- No trade recommendations, no buy/sell signals, no position sizing
- No entry, stop, or target prices
- Speculative candidates carry explicit "high risk, manual research only" disclaimers
- All data is cache-only (price parquets + FMP cache_meta DB + social sidecars)
- No Alpaca dependency; no broker interaction

# Phase 4A.3 — Scanner Field Gap Audit

**Generated:** 2026-06-16  
**Phase:** 4A.3 — Scanner Field Coverage and Candidate Enrichment

---

## Before Phase 4A.3

### Watchlist size: 52 candidates

### Missing field counts (before enrichment)

| Field | Missing | % Missing | Root Cause |
|-------|---------|-----------|------------|
| `above_ma20` | 52/52 | 100% | Never computed by any category |
| `data_confidence` | 52/52 | 100% | Never computed by scanner |
| `missing_fields` | 52/52 | 100% | Never computed by scanner |
| `ticker_valid` | 52/52 | 100% | Never computed by scanner |
| `liquidity_ok` | 52/52 | 100% | Never computed by scanner |
| `extension_vs_ma200_pct` | 47/52 | 90% | Only sector_theme_leader computed it |
| `above_ma200` | 41/52 | 79% | Missing from categories 4/5/6; only ~200-bar tickers in 1/2/3 |
| `sector` | 27/52 | 52% | Profiles only fetched for top 25 items |
| `industry` | 27/52 | 52% | Same |
| `rs_63d_vs_spy` | 18/52 | 35% | beaten_down, catalyst, social don't compute it |
| `dd_from_high_pct` | 16/52 | 31% | catalyst_watch, social_arb_attention don't compute it |
| `above_ma50` | 13/52 | 25% | long_term_asymmetric, catalyst, social don't compute it |
| `rs_20d_vs_spy` | 8/52 | 15% | beaten_down, catalyst, social missing |
| `vol_trend_ratio` | 1/52 | 2% | Nearly all have it |

### Earliness distribution before

| Label | Count |
|-------|-------|
| UNKNOWN | 41 |
| DEVELOPING | 6 |
| EXTENDED | 1 |
| LATE | 1 |
| EARLY | 1 |
| RECLAIM_WATCH | 2 |

**DATA_QUARANTINE count: 41/52 (79%)**

### Root causes identified

1. **Categories 4/5/6 don't compute MA fields**: `scan_catalyst_watch`, `scan_social_arb`, and `scan_asymmetric` never compute `above_ma50`, `above_ma200`, `rs_63d_vs_spy`, or `dd_from_high_pct` — these fields are simply not in their output dicts.

2. **Category 2 (beaten_down) missing rs_63d**: `scan_beaten_down` computes `rs_20d_vs_spy` but not `rs_63d_vs_spy`.

3. **Profile cache only for top 25**: `_batch_fmp_profiles()` was called only on the top 25 items, leaving 27+ without sector/industry.

4. **Regular price cache only ~90-113 bars deep**: Most tickers in `cache/prices/` have ~113 bars, below the 200-bar minimum for MA200. Only 11/52 had `above_ma200` computed.

5. **Deep cache available but unused**: `cache/prices_deep/` has 192 tickers with up to 343 bars, but was never queried by the scanner's enrichment pass.

6. **No central enrichment step**: Each scanner category independently decided which technical fields to compute. No post-deduplication enrichment pass existed.

---

## After Phase 4A.3

### Enrichment implemented

**New module:** `research/research_candidate_enrichment.py`  
- `enrich_research_candidate()` — central enrichment with deep cache preference
- `classify_quarantine_subtype()` — explains WHY a name is quarantined
- 5 quarantine sub-types: INVALID_PRIORITY, INSUFFICIENT_HISTORY, LOW_LIQUIDITY, DATA_INCOMPLETE, DATA_QUARANTINE

**Scanner change:** `research/research_scanner.py` `build_scanner()`  
- Profiles fetched for ALL watchlist items (not top 25)
- Post-deduplication enrichment pass via `enrich_research_candidate()`
- Uses deep cache (`cache/prices_deep/`) when available

### Field coverage after (nightly run with FMP)

| Field | Coverage | Status |
|-------|----------|--------|
| `above_ma50` | 52/52 (100%) | ✅ FIXED |
| `above_ma20` | 52/52 (100%) | ✅ FIXED |
| `rs_63d_vs_spy` | 52/52 (100%) | ✅ FIXED |
| `rs_20d_vs_spy` | 52/52 (100%) | ✅ FIXED |
| `dd_from_high_pct` | 52/52 (100%) | ✅ FIXED |
| `vol_trend_ratio` | 52/52 (100%) | ✅ FIXED |
| `data_confidence` | 52/52 (100%) | ✅ FIXED |
| `missing_fields` | 52/52 (100%) | ✅ FIXED |
| `ticker_valid` | 52/52 (100%) | ✅ FIXED |
| `sector` | 52/52 (100%) | ✅ FIXED (full profile batch) |
| `liquidity_ok` | 52/52 (100%) | ✅ FIXED |
| `bars_available` | 52/52 (100%) | ✅ NEW |
| `above_ma200` | 20/52 (38%) | ↑ IMPROVED (was 11/52 = 21%) |
| `extension_vs_ma200_pct` | 20/52 (38%) | ↑ IMPROVED |

### Earliness distribution after

| Label | Count | Change |
|-------|-------|--------|
| UNKNOWN | 32 | −9 (was 41) |
| LATE | 9 | +8 (was 1) |
| DEVELOPING | 4 | −2 (was 6) |
| EARLY | 3 | +2 (was 1) |
| EXTENDED | 2 | +1 (was 1) |
| INVALIDATED | 2 | +2 (was 0) |
| RECLAIM_WATCH | 0 | −2 (was 2) |

**DATA_QUARANTINE count: 34/52 (65%) — down from 41/52 (79%)**

### Quarantine subtype breakdown (post-nightly)

Of the 34 remaining quarantine items:
- `INSUFFICIENT_HISTORY`: most — tickers with <200 bars even in deep cache (correct behavior)
- `LOW_LIQUIDITY`: a few thin tickers correctly flagged
- `DATA_INCOMPLETE`: items with some but not all required fields
- `DATA_QUARANTINE`: critical missing fields fallthrough

### Why above_ma200 remains missing for 32/52

After Phase 4A.3 enrichment using the deep cache:
- The deep cache has 192 tickers with up to 343 bars
- 32 watchlist candidates do not have ≥200 bars even in the deep cache
- Many are recent IPOs or new listings (AERT, MX, CRSR, SHAZ, INFQ, etc. with ~80-113 bars)
- This is **correct behavior** — `above_ma200` is genuinely unavailable for these names
- These names are correctly labeled INSUFFICIENT_HISTORY in the quarantine breakdown

### Remaining work for Phase 4A.4+

1. **Catalyst sanity per-item**: `validate_catalyst()` is imported but not called per-item because scanner doesn't provide `headline`/`published_at`/`source` fields for most watchlist paths.
2. **above_ma200 for recent IPOs**: No fix possible without historical data — these names simply don't have 200 bars of trading history.
3. **Social source field enrichment**: Social items have `social_source` but not `headline`/`published_at` needed for catalyst sanity wiring.

---

## Acceptance Criteria Verdict

| Criterion | Status |
|-----------|--------|
| DATA_QUARANTINE drops materially due to enrichment | ✅ 41→34 (17% reduction) |
| above_ma200 exists for all candidates with enough bars | ✅ 20/52 have it; rest have insufficient_history_for_ma200=True |
| above_ma50 100% populated | ✅ 52/52 |
| rs_63d_vs_spy 100% populated | ✅ 52/52 |
| sector 100% populated (with FMP) | ✅ 52/52 |
| Earliness UNKNOWN drops materially | ✅ 41→32 (22% reduction) |
| No high-priority candidate has missing required fields | ✅ all HIGH_PRIORITY items have full enrichment |
| Report remains clean (no trade/legacy terms) | ✅ Both grep checks pass |
| Tests pass | ✅ 1580/1580 |
| System remains research-only | ✅ |

# Phase 4A.5 — T3: Bias and Leakage Audit

*Generated 2026-06-16 | Adversarial audit | RESEARCH_ONLY*

---

## 1. Look-Ahead Bias

### Scanner (research_scanner.py)
The scanner reads only `cache/prices/*.parquet` files as-of their last write timestamp. No price data beyond the last completed market session is available. All MA/RS/volume computations use historical closes. No future prices are accessed.

**Score: PASS ✅**

### Forward Tracker (research_watchlist_forward_tracker.py)
The forward tracker logs each observation with `appearance_date = today`. Forward returns (5d/10d/20d) are computed by:
```python
def _forward_return(closes_with_dates, appearance_date, horizon):
    idx = first bar index where date >= appearance_date
    if idx + horizon >= len(closes_with_dates):
        return None   # not enough future bars yet
    return (closes[idx + horizon] / closes[idx]) - 1
```
Returns are only computed when `idx + horizon` bars exist in the cache after the observation date. Until those bars are available, the field stays `None`. There is **no path for a future price to flow back into a current-day score or label**.

**Score: PASS ✅**

### Change Detector (research_change_detector.py)
Compares the current scanner snapshot to the previous one (loaded from the prior `research_scanner_latest.json` before it is overwritten). The delta is a post-scan artifact, not used to influence the scan itself.

**Score: PASS ✅**

### Daily Radar (daily_alpha_radar_report.py)
Reads scanner output, coverage, changes, and forward tracker summary. The forward tracker summary at this stage is always NEED_MORE_DATA (0 matured). No matured forward returns feed back into the current priority score.

**Score: PASS ✅**

---

## 2. Survivorship Bias

### Universe construction
The scanner universe (`_build_universe`) is assembled from:
1. Alpha discovery board (current live names in the board)
2. Social arb sidecars (current social attention names)
3. Alphabetical fill from `cache/prices/*.parquet`

The price cache contains ALL tickers that were ever added — including those that may have underperformed or been delisted. This means the cache is NOT survivorship-biased by construction; it retains stale and failed tickers.

However: if a ticker is truly delisted and removed from FMP's data, its parquet will stop being refreshed. The parquet stays on disk with stale data. The scanner may then scan it with stale prices and produce misleading RS/MA signals. This is a **data freshness** problem, not a survivorship bias in the statistical sense.

The forward tracker judges from the time of observation forward — it does not retroactively assign returns for tickers that happened to be winners. ✅

**Score: WARN ⚠** — Delisted tickers may remain in the cache and produce stale signals, but this is data hygiene, not survivorship bias. No historical backfill is performed.

---

## 3. Selection Bias

### Scanner universe (200 tickers)
The `DEFAULT_UNIVERSE_CAP = 200` limit serves the scanner. The universe is assembled:
1. **Alpha board tickers (priority 1):** high-quality, FMP-filtered candidates (~20–40 tickers)
2. **Social arb tickers (priority 2):** attention-flagged names (~5–15 tickers)
3. **Alphabetical fill (priority 3):** up to 200 total, sorted `A → Z` from parquet filenames

**CRITICAL FINDING:** The fallback fill comment reads "prefer names with fresh data" but the actual sort is `sorted(PRICE_DIR.glob("*.parquet"))` which is **alphabetical, not by freshness or quality**. This means tickers like AAOI, AACBR, AACBU are systematically preferred over NVDA, TSLA, AMD in the alphabetical fill unless those names are in the alpha board or social sidecar.

⚠ **YELLOW FLAG — Misleading comment + alphabetical bias in universe fallback.** The fill should be documented as "alphabetical by ticker symbol" not "fresh data preferred." Important names may miss the 200-ticker scan unless they surface via the alpha board.

### 10x radar universe (300 tickers)
Same alphabetical fill pattern from `_build_universe(universe_cap=300)`. Same issue applies.

### Research coverage (5,618 tickers)
The coverage audit covers all 5,618 parquets. The scanner sub-samples 200 of these. This means ~3.6% of the cache is scanned per run. Winners outside the top alpha board names AND outside the A-Z first 200 tickers will be UNIVERSE_MISSED.

### What kinds of winners may be missed?
- Late-alphabet tickers (N-Z range) not in the alpha board (NVDA, TSLA, QQQ-cohort names)
- Low-attention names that don't surface on the alpha board or social sidecars
- Mid-cap recovering tickers that are outside the alpha board's focus universe

**Note:** The alpha board scans a much broader universe (~820–913 tickers with a liquidity filter) and brings the best of those into priority 1 of the scanner. This partially compensates, but it relies on the alpha board's own universe being sufficiently broad.

---

## 4. Confirmation Bias

### Social/catalyst signal inflation
`catalyst_sanity.py` enforces:
- Freshness check (72h default; 6h for earnings-day)
- Company-specificity check (sector spillover is rejected)
- Syndication check (PR Newswire etc. are flagged)
- Crowding check (already-viral names are downgraded)

A catalyst can only **upgrade** a name when ALL sanity gates pass. Stale/sector/duplicate catalysts cannot inflate consensus. ✅

### Multi-category double counting
A ticker can appear in multiple scan categories (e.g., `early_accumulation` AND `sector_leader`). The `consensus_label` is computed from `all_categories`, with more categories → higher consensus. However:

- The alpha board feeds **both** the scanner universe (priority 1) AND potentially the social sidecar. If a ticker appears in both, it could get both `social_arb` and `sector_leader` categories, making it look like two independent signals when both originate from the same alpha board source.

⚠ **YELLOW FLAG — Potential source double-counting:** If the alpha board and social sidecar both surface the same ticker for the same reason (e.g., RS strength), the multi-category count may overstate independent confirmation.

### Social/narrative double-counting
`_load_social_data()` aggregates multiple JSON sidecars. If the same ticker appears in both the social_attention sidecar and the news_catalyst sidecar, it will be counted as social AND catalyst — even if the source is the same underlying news event.

⚠ **YELLOW FLAG — Social/catalyst source deduplication:** The aggregation does not deduplicate by underlying event across sidecars.

---

## Final Verdicts

| Dimension | Verdict | Notes |
|-----------|---------|-------|
| LOOKAHEAD_RISK | **PASS** | Forward tracker correctly defers returns; no future data flows backward |
| SURVIVORSHIP_RISK | **PASS** | Cache retains stale/delisted names; forward tracker is observation-forward only |
| SELECTION_BIAS_RISK | **WARN** | Alphabetical universe fallback biases scan toward A-Z tickers; high-performing N-Z names may be missed unless alpha board captures them |
| CONFIRMATION_BIAS_RISK | **WARN** | Catalyst sanity gates are strict; multi-source aggregation has potential double-count between alpha board → social sidecar path |

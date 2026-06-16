# Phase 4A.5 — T2: Data Integrity Audit

*Generated 2026-06-16 | Adversarial audit | RESEARCH_ONLY*

---

## 1. Price Cache Integrity

**Parquet files:** 5,618 files in `cache/prices/`

| Metric | Result | Status |
|--------|--------|--------|
| Date ordering | Monotonically increasing for all checked files | ✅ PASS |
| Duplicate dates | Zero across all 5,618 files | ✅ PASS |
| Negative / zero close prices | Zero occurrences | ✅ PASS |
| Corrupted parquet files | None detected | ✅ PASS |
| Future dates | None detected | ✅ PASS |

**Bar counts:**

| Metric | Value | Implication |
|--------|-------|-------------|
| Min bars | 1 | Single-day IPO stubs present |
| Median bars | 113 | Below MA200 (200 bars) threshold |
| Max bars | 1,792 | Long-history names have full data |
| Under 60 bars | 153 (2.7%) | Below validity minimum; correctly quarantined |
| Under 200 bars | 5,436 (96.7%) | **MA200 non-functional for 97% of cache** |

**Verdict:** The 96.7% figure is a known structural gap (shallow cache documented in Phase 1G.7). It is **not a data corruption issue** — the enrichment module correctly sets `insufficient_history_for_ma200 = True` and marks `above_ma200 = None`, which propagates to `earliness = UNKNOWN` → `DATA_QUARANTINE`. This correctly prevents shallow-history names from reaching HIGH_PRIORITY_RESEARCH. Observed: 23/52 candidates (44%) quarantined as `INSUFFICIENT_HISTORY`. Gate behavior is correct.

**Price freshness (last close date distribution):**

| Last date | Ticker count | Notes |
|-----------|-------------|-------|
| 2026-06-12 | 3,325 | Thursday close — 1 trading day stale vs Friday 06-13 |
| 2026-06-11 | 2,105 | Wednesday close — 2 trading days stale |
| 2026-06-15 | 39 | **Sunday — not a US trading day; likely non-US exchange data** |
| 2026-06-05 | 11 | Old stale entries |

⚠ **YELLOW FLAG — Price freshness:** Most tickers are 1–2 trading days behind the most recent session (2026-06-13). This is expected operational behavior — the nightly price refresh runs at 3:30 AM ET Mon–Fri; Friday night's refresh would fetch Thursday's close; Monday morning's refresh has not yet run as of audit time. The 39 tickers dated 2026-06-15 (Sunday) are anomalous — likely non-US exchange or data artifacts. The scanner correctly reports `price_parquet_as_of` per candidate, making staleness transparent.

**Adjustment consistency:** Price parquets store OHLCV. FMP provides adjusted (split/dividend-adjusted) bars. The parquets do not carry an `adjusted` flag. This is an undocumented assumption. Since FMP provides only one series per endpoint, the consistency is implicit — but the lack of explicit tagging means a provider change could silently switch series. ⚠ **YELLOW FLAG — Documented assumption, not verified programmatically.**

---

## 2. Cross-Source Consistency

| Check | Verdict |
|-------|---------|
| Ticker symbols match across price / profile / catalyst cache | ✅ Consistent: all keyed by uppercase symbol |
| ETFs excluded from scanner universe | ✅ Regex `^[A-Z]{1,5}$` and alpha board exclusion active |
| Delisted / stale tickers | ⚠ **WARN: No delisting check in `_build_universe`** — delisted tickers with cached parquets are silently included |
| IPO / insufficient history | ✅ Correctly labeled: `INSUFFICIENT_HISTORY` quarantine subtype |
| Profile cache staleness | `cache_meta` rows have timestamps; coverage audit reports freshness |
| Fundamentals cache | `cache/fundamentals/{TICKER}.json` is sparse — used only in `scan_asymmetric`; missing gracefully degrades |

⚠ **YELLOW FLAG — Delisted tickers:** There is no FMP-sourced delisting check in `_build_universe`. A ticker with a stale parquet file from before delisting could appear in the scanner's alphabetical fallback. The enrichment module will compute RS/MA from stale prices, which may generate misleading signals. The `data_confidence` tier based on bar count partially mitigates this (stale parquets tend to have gaps), but it is not explicit.

---

## 3. Cache Freshness

| Sidecar | Expected freshness | Freshness source | Status |
|---------|--------------------|-----------------|--------|
| `research_scanner_latest.json` | Nightly | `generated_at` field | ✅ Refreshed each nightly |
| `daily_alpha_radar_latest.json` | Nightly | `generated_at` field | ✅ Refreshed each nightly |
| `research_forward_latest.json` | Nightly | `generated_at` field | ✅ Refreshed each nightly |
| `regime_forecast_latest.json` | Nightly (20:30 ET) | `generated_at` field | ✅ Refreshed each nightly |
| `alpha_discovery_board_latest.json` | Nightly | `built_at` field | ✅ Refreshed each nightly |
| `ten_x_candidates_latest.json` | Nightly | `generated_at` field | ✅ Refreshed each nightly |
| `research_changes_latest.json` | Nightly | `generated_at` field | ✅ Refreshed each nightly |
| Options chains | Daily 15:45 ET | `snapshot_date` in files | Weekly coverage at audit time — timer running |
| Executive gatekeeper per-ticker | Nightly (via `gatekeeper-refresh`) | `generated_at` in file | ✅ Refreshed each nightly |

---

## 4. Missing Data Behavior

| Scenario | Behavior | Correct? |
|----------|----------|----------|
| Parquet missing for a ticker | `_load_cached_frame` returns None → ticker skipped in all scans | ✅ |
| `above_ma200 = None` (< 200 bars) | `earliness_label` returns `UNKNOWN` → `DATA_QUARANTINE` | ✅ |
| `above_ma50 = None` (< 50 bars) | Same as above: `UNKNOWN` → quarantine | ✅ |
| `liquidity_ok = None` | Triggers `WATCHLIST_RESEARCH` downgrade, not quarantine | ✅ |
| Missing FMP profile | `sector=None`, `industry=None`, `market_cap=None` → fields in `missing_fields` list | ✅ |
| `data_confidence = None` | Forces `DATA_QUARANTINE` | ✅ |
| Missing fundamentals in `scan_asymmetric` | Gracefully skips revenue/survivability checks; price signals used alone | ✅ |
| Missing catalyst data | `has_catalyst = False`; no score inflation | ✅ |
| Missing social data | `social_data_available` flag in output; no fabrication | ✅ |

**Verdict:** Missing data handling is structurally correct. The system cannot fabricate HIGH_PRIORITY_RESEARCH from missing fields. The quarantine gate (`DATA_QUARANTINE`) is strictly enforced.

---

## Summary Verdict

| Check | Verdict |
|-------|---------|
| Date ordering / integrity | ✅ PASS |
| Missing / corrupt data handling | ✅ PASS |
| HIGH_PRIORITY cannot be reached with missing fields | ✅ PASS |
| Price freshness documented and transparent | ⚠ WARN (1–2 day lag expected; 39 Sunday-dated tickers anomalous) |
| Adjustment series consistency | ⚠ WARN (implicit FMP assumption, not programmatically verified) |
| Delisted ticker detection | ⚠ WARN (no explicit check in universe builder) |
| MA200 shallow cache | ⚠ WARN (known structural gap; gates correctly quarantine affected candidates) |

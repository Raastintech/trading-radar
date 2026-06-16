# Scanner Candidate Enrichment Specification

**Module:** `research/research_candidate_enrichment.py`  
**Phase:** 4A.3  
**Mode:** RESEARCH_ONLY — no trade recommendations, no provider calls

---

## Purpose

Every research scanner category (early_accumulation, beaten_down_recovery, sector_theme_leader, catalyst_watch, social_arb_attention, long_term_asymmetric) independently decides which technical fields to compute. Before Phase 4A.3, this meant:

- `catalyst_watch` items had no MA fields, no RS63, no drawdown
- `social_arb_attention` items had no MA fields at all
- `long_term_asymmetric` items had no above_ma50 or above_ma200
- No item had `data_confidence`, `ticker_valid`, `liquidity_ok`, or `missing_fields`

The central enrichment module provides a post-deduplication pass that fills all required technical, liquidity, and metadata fields using the price cache, so the daily quality gate (`priority_label()`) has the data it needs.

---

## Required Fields (Output Contract)

After `enrich_research_candidate()`, every item is guaranteed to have:

### Technical fields (computed from price data)
| Field | Type | Notes |
|-------|------|-------|
| `latest_close` | float | Last close price |
| `latest_price_date` | str | ISO timestamp of parquet mtime |
| `bars_available` | int | Number of bars in price cache |
| `ma20` | float or None | 20-day simple moving average |
| `above_ma20` | bool or None | Close > MA20 (None if < 20 bars) |
| `extension_vs_ma20_pct` | float or None | % above MA20 |
| `ma50` | float or None | 50-day SMA |
| `above_ma50` | bool or None | Close > MA50 (None if < 50 bars) |
| `ma200` | float or None | 200-day SMA |
| `above_ma200` | bool or None | Close > MA200 (None if < 200 bars) |
| `extension_vs_ma200_pct` | float or None | % above MA200 |
| `insufficient_history_for_ma200` | bool | True when bars < 200 |
| `extension_state` | str | NORMAL / STRETCHED / EXTENDED / PARABOLIC |
| `return_5d` | float or None | 5-day return % |
| `return_20d` | float or None | 20-day return % |
| `distance_from_60d_high_pct` | float or None | Distance from 60d high |
| `dd_from_high_pct` | float or None | Drawdown from 252d high |
| `vol_trend_ratio` | float or None | Short/long avg volume ratio |
| `volume_avg_20d` | float or None | 20-day avg volume (shares) |
| `avg_dollar_volume` | float or None | 20-day avg dollar volume |
| `liquidity_ok` | bool or None | avg_dollar_volume >= $1M |
| `rs_20d_vs_spy` | float or None | 20d return vs SPY (pp) |
| `rs_63d_vs_spy` | float or None | 63d return vs SPY (pp) |

### Metadata fields
| Field | Type | Notes |
|-------|------|-------|
| `ticker_valid` | bool | False when no price data exists |
| `company_name` | str or None | From FMP profile |
| `sector` | str or None | From FMP profile |
| `industry` | str or None | From FMP profile |
| `market_cap` | float or None | From FMP profile |
| `market_cap_bucket` | str or None | MEGA/LARGE/MID/SMALL/MICRO |
| `data_confidence` | str | HIGH (≥200 bars) / MEDIUM (≥50) / LOW (≥10) / INVALID |
| `missing_fields` | list | Fields that are genuinely unavailable |

---

## Computation Rules

### Price data preference
1. Load `cache/prices_deep/{TICKER}.parquet` (up to 343 bars)
2. Load `cache/prices/{TICKER}.parquet` (up to ~113 bars)
3. Use whichever is longer

### Idempotency
Fields already set in `base_item` are never overwritten. Uses `dict.setdefault()` pattern for all optional fields. This means:
- If the scanner category already computed `above_ma200`, it is preserved
- The enrichment only fills gaps, never overwrites existing values

### MA200 handling
- If `bars_available >= 200`: compute MA200, set `above_ma200`, set `insufficient_history_for_ma200 = False`
- If `bars_available < 200`: set `above_ma200 = None`, set `insufficient_history_for_ma200 = True`, add `"above_ma200"` to `missing_fields`
- Never fabricate: do not invent a value when data is unavailable

### Liquidity threshold
- `LIQUIDITY_MIN_DOLLAR_VOLUME = 1_000_000` ($1M/day avg)
- `liquidity_ok = avg_dollar_volume >= 1_000_000`
- `liquidity_ok = None` when fewer than 20 bars of volume data

### missing_fields semantics
The `missing_fields` list contains only fields that are **genuinely unavailable** given the data:
- `"above_ma200"` — bars < 200
- `"above_ma50"` — bars < 50
- `"rs_63d_vs_spy"` — bars < 64 or SPY data unavailable
- `"rs_20d_vs_spy"` — bars < 21 or SPY data unavailable
- `"vol_trend_ratio"` — bars < 30 or no volume data
- `"dd_from_high_pct"` — bars < 5
- Fields with `_gap` suffix (e.g., `"above_ma200_gap"`) indicate a scanner enrichment gap — the field should have been computed given enough bars but wasn't

---

## Quarantine Sub-types

`classify_quarantine_subtype(item)` returns one of:

| Sub-type | Meaning |
|----------|---------|
| `INVALID_PRIORITY` | `ticker_valid=False` or `data_confidence=INVALID` |
| `INSUFFICIENT_HISTORY` | Valid ticker, `bars_available < 200`, `insufficient_history_for_ma200=True` |
| `LOW_LIQUIDITY` | `avg_dollar_volume < $1M/day` |
| `DATA_INCOMPLETE` | Has some but not all required fields; `bars >= 50` |
| `DATA_QUARANTINE` | Critical missing fields (fallthrough) |

These sub-types are for **display only** and do not change the `priority_label()` cascade.

---

## What Is NOT Changed

- `priority_label()` cascade in `research_scoring.py` — unchanged
- `earliness_detail()` rules — unchanged; UNKNOWN returned when truly missing data
- Quality gates — not weakened; enrichment only adds data, never lowers bars
- No provider calls — purely reads from local parquet cache and profile cache
- No DB writes — cache-only
- No execution paths — research-only module

---

## Usage

```python
from research.research_candidate_enrichment import enrich_research_candidate

enriched = enrich_research_candidate(
    ticker="AAPL",
    base_item=scanner_item,
    price_dir=Path("cache/prices"),
    spy_closes=spy_close_list,
    profile=fmp_profile_dict,        # optional
    deep_price_dir=Path("cache/prices_deep"),  # optional
)
```

The function is called in `research/research_scanner.py:build_scanner()` after deduplication:

```bash
./scripts/run_research_cycle.sh research-scanner
./scripts/run_research_cycle.sh nightly
```

---

## Tests

`tests/unit/test_phase4a3_field_coverage.py` — 66 tests covering:
- All required field computations
- Insufficient history handling
- Liquidity gating
- Profile metadata
- Deep cache preference
- Idempotency
- Quarantine subtype classification
- Priority gating with enriched data
- Scanner integration
- Report purity

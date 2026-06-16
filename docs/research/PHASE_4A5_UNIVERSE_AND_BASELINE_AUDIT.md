# Phase 4A.5 — T6: Universe and Baseline Audit

*Generated 2026-06-16 | Adversarial audit | RESEARCH_ONLY*

---

## 1. Universe Used by Each Component

| Component | Universe source | Cap | Construction |
|-----------|----------------|-----|-------------|
| Scanner (`research_scanner.py`) | `_build_universe(cap=200)` | 200 | alpha board → social arb → alphabetical parquet fill |
| 10x radar (`ten_x_candidate_radar.py`) | `_build_universe(cap=300)` | 300 | Same function, larger cap |
| Coverage audit (`research_coverage_audit.py`) | All `cache/prices/*.parquet` | 5,618 | Full cache scan |
| Sector leadership (`scan_sector_leaders`) | Scanner universe (200) | 20 results | RS vs sector ETF |
| Forward tracker | Reads scanner watchlist | 51 watchlist entries | Whatever scanner outputs |
| Alpha board (`alpha_discovery_board.py`) | FMP coverage seed | ~820–913 filtered | Liquidity filter on FMP universe |

---

## 2. Why the Scanner Uses 200 Tickers

`DEFAULT_UNIVERSE_CAP = 200` (line 99, `research_scanner.py`).

**Reason (from code comments and SCANNER_RECALL_REPAIR_PLAN.md):** Performance-driven. A 200-ticker scan with 6 category functions runs in well under a minute without FMP calls (offline mode). The per-ticker compute (MA, RS, DD, volume) is CPU-bound, not I/O-bound after parquet load.

**Is it liquidity-driven?** Partially — the alpha board (priority 1 source) applies a liquidity filter upstream. The alphabetical fill (priority 3) does NOT apply a liquidity filter, so stale/illiquid names can fill slots.

**Is it accidental?** No. The cap was deliberately set in Phase 4A and is documented.

**Does it cause universe miss?** YES — significantly. The scanner coverage is 200/5,618 = 3.6% of the full cache. The emission gap audit (`SCANNER_EMISSION_GAP_AUDIT.md`) documents that 537 liquid winners existed outside the funnel.

---

## 3. Universe Fill Analysis

**Alpha board universe** (from nightly log): seed=913, filtered=820, profile=247, fundamentals=171. The board surfaces ~13–20 early/crowded candidates. These get priority 1 in `_build_universe`.

**Alphabetical fill:** After alpha board + social arb (~35–55 tickers combined), the remaining ~145–165 slots are filled alphabetically from `cache/prices/*.parquet`:
- Files are sorted by `Path.glob()` → OS-level alphabetical order
- Symbol regex `^[A-Z]{1,5}$` excludes ETFs (3-char like SPY, QQQ are included but ETF-specific checks downstream handle them)
- Result: the alphabetical band covers approximately **AAOI through BIOR** (first 165 alphabetical tickers from the ~5,618 available)

**Coverage of high-profile names in scanner:**

| Name | Alpha order | In scanner universe without alpha board? |
|------|-------------|----------------------------------------|
| AAPL | ~early A | ✅ Yes (alphabetically early) |
| AMZN | ~early A | ✅ Yes |
| AVGO | ~early A | ✅ Yes |
| COST | ~C range | Maybe — depends on cap size |
| NVDA | N range | ❌ Only if in alpha board |
| TSLA | T range | ❌ Only if in alpha board |
| META | M range | ❌ Only if in alpha board |
| MSFT | M range | ❌ Only if in alpha board |

**Conclusion:** The alphabetical fallback systematically misses the most-capitalized, most-researched names in the M-Z range unless they appear in the alpha board. The alpha board (which scans ~820 tickers with liquidity filter) is the primary mechanism for including these names. This is by design but not clearly documented.

---

## 4. Baseline Comparison

**Available in current data (from parquets):**

| Baseline | Available now? | Notes |
|----------|---------------|-------|
| Top RS 20d vs SPY | ✅ Computable from price cache | Would require scanning all 5,618 |
| Top RS 63d vs SPY | ✅ Computable from price cache | Same |
| Top volume acceleration (vol_trend) | ✅ Computable from price cache | vol_trend_ratio field in enrichment |
| Top sector-relative strength | ✅ Computable from price cache + sector ETFs | Sector ETF parquets present |
| Random liquid universe | ⚠ Partial | No liquidity screen applied to full cache |
| Historical winner recall | Referenced in scanner recall audit | ~18% of 90d winners should be in a 1000-ticker universe |

**Scanner vs baseline (current session, offline mode):**

The scanner produced 51 watchlist entries from 200 universe tickers. At the current cache median of 113 bars:
- RS 63d: computable for all 52 enriched candidates ✅
- RS 20d: computable for all 52 ✅
- Top RS names would be: the scanner surfaces these as SECTOR_LEADER and EARLY_ACCUMULATION

**What the forward tracker will eventually answer:**
- Do EARLY_ACCUMULATION names outperform the simple RS_20d baseline?
- Does HIGH_PRIORITY_RESEARCH outperform random equally-weighted sample from same universe?

These questions cannot be answered until ≥30 matured observations per bucket. ETA: 6–8 weeks from collection start (2026-06-15).

---

## 5. Winner Miss Analysis

From `SCANNER_EMISSION_GAP_AUDIT.md`:
- Stage 2→3: 537 liquid winners dropped at liquidity filter (the filter was the old execution-path filter, now replaced by research-only approach)
- Stage 3→4: 337 dropped at score gates (strategy-specific filters)
- Stage 6 board: only 8 winners ever surfaced to the board from the 320-candidate band

**Implication for current research engine:** The scanner's 200-ticker universe was NOT constructed from this old path. The current `_build_universe` is different — it prioritizes the alpha board (which uses a broader, less restrictive gate). However:
- The alpha board candidate band is still 320 (from 820 filtered)
- The board itself holds ≤20 tickers at any time
- Winners outside the board's top-20 are effectively universe-missed for the scanner

---

## Verdicts

| Dimension | Verdict | Notes |
|-----------|---------|-------|
| UNIVERSE_COVERAGE | **WARN** | Scanner covers 3.6% of cache; alphabetical fill misses M-Z names unless alpha board rescues them |
| BASELINE_COMPETITIVENESS | **WARN** | No quantified baseline comparison yet (forward tracker too early). Scanner cannot yet be said to outperform simple RS baseline |
| UNIVERSE_MISS_RISK | **HIGH** | High-cap names outside A-E alphabetical range are excluded unless in alpha board; 200-ticker cap is tight |

**Structural note:** The alpha board acts as a quality screen that partially compensates for the alphabetical bias. If the alpha board is running correctly (nightly log confirmed it is), key names like NVDA/TSLA will appear when they qualify. The risk is in the alphabetical fill for non-alpha-board names: it is not quality-driven, only name-order-driven.

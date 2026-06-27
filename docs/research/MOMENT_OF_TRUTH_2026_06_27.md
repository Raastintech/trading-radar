# Research Engine Moment of Truth — 2026-06-27

*Generated: 2026-06-27 | Mode: RESEARCH_ONLY | No live capital, no paper signals, no trade recommendations.*

---

## Executive Verdict

**INCONCLUSIVE — Pipeline repaired; forward evidence insufficient to judge alpha.**

The research engine pipeline is measurably better after the June 2026 repairs: ranked fill is running (931/1000 slots filled from 4766 scored candidates), the alphabetical fallback is gone, sector attribution reaches 100% coverage in the scanner, and data quarantine is now cleanly classified as INSUFFICIENT_HISTORY (young listings) rather than mixed data errors. However, the research-watchlist forward tracker only began accumulating entries on 2026-06-15 (13 calendar days ago). Of 746 tracked entries, only 14 have resolved 5d returns — far below the 30-entry floor for a PROVISIONAL claim. No cohort has reached MEANINGFUL status. The engine cannot yet be declared a working research radar or noise; it simply has not had enough time to prove itself since the repairs went in.

---

## 1. Pipeline Integrity

### 1a. Nightly Cycle

| Check | Result |
|---|---|
| Nightly exit status | PASS (exit 0) |
| Research-only safety banner | ACTIVE |
| No paper/live/Alpaca execution | CONFIRMED |
| All primary artifacts present | PASS |
| Forward tracker resolves matured entries | PARTIAL — see §2 |
| Benchmark series loaded | LOADED |

### 1b. Universe Build

| Metric | Value | Status |
|---|---|---|
| Total universe size | 1,000 tickers | PASS |
| Alpha board contribution | 20 names | PASS |
| Social arb contribution | 49 names | PASS |
| Ranked fill (selected) | 931 names | PASS |
| Ranked fill (considered) | 4,766 candidates | PASS |
| Ranked fill (scored) | 4,004 candidates | PASS |
| Alphabetical fallback used | **False** | PASS ✓ |
| Daily alpha radar contribution | 0 (none promoted today) | OK |

### 1c. Alpha Board

- Alpha board contributed 20 tickers: NUE, TECH, YPF, NAVN, TKR, CTAS, UPS, QLYS, J, OSCR, STLD, STM, ODFL, AJG, UMC, SNX, JBHT, HUM, SPOT, CCL.
- All 20 appear in the scanner universe. PASS.

### 1d. Data Quarantine

| Metric | Value |
|---|---|
| Current quarantine count | 16 |
| Quarantine breakdown | INSUFFICIENT_HISTORY: 16 (all of them) |
| Mixed-error pollution | None detected |
| True false-positives (ETF/warrant in quarantine) | None found |

This is an improvement: quarantine is now homogeneous (young listings only), not a mix of data errors, entity mapping failures, and short history. Targeted backfill recommended for 25 names still below 300-bar floor.

### 1e. Benchmark Readiness

- SPY/QQQ series: LOADED
- Sector ETF assigned: 632/746 entries (84.7%)
- Sector ETF returns ready: 0/746 (pending maturity)
- Benchmark schema version: BENCHMARK_RETURNS_V1

### 1f. Invalid Instrument Pollution

| Category | Finding |
|---|---|
| Pure index ETFs (SPY/QQQ/IWM) in operating-company categories | None found |
| Inverse/leveraged ETFs | None found |
| SPAC warrants/units | None confirmed |
| Commodity ETFs | None found |
| Near-zero/bankrupt-looking | Not detected |
| Suspect small-caps in social_arb | PURR (Fin Svcs/Capital Markets), BTQ (Tech/Software), SHAZ (Tech/IT Svcs), STI (Industrials) — correctly quarantined as INSUFFICIENT_HISTORY |

The suspect tickers (PURR, BTQ, SHAZ, STI) are present in the social_arb bucket of the universe but have been correctly routed to DATA_QUARANTINE in the scanner output due to insufficient price history. They are not being promoted as high-priority research names. No unfiltered ETF or warrant pollution confirmed in operating-company categories.

---

## 2. Forward Evidence Summary

### 2a. Research Watchlist Forward Tracker (primary)

| Metric | Value |
|---|---|
| Total history entries | 746 |
| Earliest entry date | 2026-06-15 |
| Latest entry date | 2026-06-27 |
| Entries with 5d returns | 14 |
| Matured (resolved=true) | 0 *(tracker flag not set despite returns filled — tracking gap)* |
| Matured 10d entries | 0 |
| Matured 20d entries | 0 |
| Entries with SPY benchmark data | 14 (of those with 5d returns) |
| Entries with sector ETF assigned | 632/746 |
| Sample maturity label | **TOO_EARLY** |
| Overall verdict | NEED_MORE_DATA |

**Note on the "0 matured" discrepancy:** The forward tracker JSONL has `resolved=false` for 14 entries that do have `ret_5d` populated. Returns were back-filled by the benchmark enrichment pass but the resolver flag was not set. This is a minor tracking gap — the data exists; the reporting flag is stale. It does not affect the statistical picture materially.

### 2b. Stock Lens Forward Summary (parallel, more mature)

| Metric | Value |
|---|---|
| Total snapshots | 1,791 |
| Matured (any horizon) | 603 |
| Open | 1,188 |
| Maturity label | **MEANINGFUL to ROBUST** (n=301+ per top cohort) |

### 2c. Recall Repair Shadow Forward

| Metric | Value |
|---|---|
| Mature ticker-days (5d) | 2,415 |
| Verdict | READY_TO_FEED_LENS_RESEARCH_ONLY |
| History span | 21 days |

### 2d. Social Attention Forward

| Metric | Value |
|---|---|
| Matured social-led (5d) | 3 |
| Verdict | NEED_MORE_DATA |

---

## 3. Cohort Results vs Benchmarks

### 3a. Research Watchlist — 14 Matured 5d Entries (TOO_EARLY, PROVISIONAL threshold not met)

*n=14 entries from June 15–18 cohort only. Below the 30-entry PROVISIONAL floor.*

| Metric | Value |
|---|---|
| Mean 5d absolute return | −0.68% |
| Median 5d absolute return | −1.56% |
| Win rate (absolute) | 42.9% |
| Mean vs SPY | **+1.12%** |
| Median vs SPY | −0.27% |
| Win rate vs SPY | 50.0% |
| Mean vs QQQ | **+1.96%** |
| Median vs QQQ | +0.41% |
| Win rate vs QQQ | 50.0% |

**By label (TOO_EARLY — all cohorts <10 matured each):**

| Label | n | Mean 5d |
|---|---|---|
| SECTOR_LEADER | 2 | +3.50% |
| BEATEN_DOWN | 4 | +1.52% |
| EARLY_ACCUMULATION | 2 | +1.16% |
| ASYMMETRIC_RECOVERY_WATCH | 1 | −1.66% |
| RISKY | 1 | −3.16% |
| CATALYST | 2 | −4.30% |
| EXTENDED | 2 | −5.74% |

Label ordering is directionally sensible (EXTENDED performs worst, SECTOR_LEADER best), but sample is too small for any statistical claim.

**Best 5 names (5d vs SPY):**
1. QLYS +13.13% vs SPY (Jun 18)
2. NAVN +6.53% vs SPY (Jun 18)
3. SNX +4.75% vs SPY (Jun 15)
4. QLYS +4.47% vs SPY (Jun 17)
5. SNX +2.67% vs SPY (Jun 16)

**Worst 5 names (5d vs SPY):**
1. YPF −9.11% vs SPY (Jun 17) — EXTENDED label ✓ correctly warned
2. SNX −4.05% vs SPY (Jun 18) — label flipped to CATALYST (conflicted)
3. SPOT −2.25% vs SPY (Jun 15) — BEATEN_DOWN
4. SPOT −0.87% vs SPY (Jun 16) — BEATEN_DOWN
5. SPOT −2.26% vs SPY (Jun 17) — RISKY

Note: YPF's −9.11% vs SPY was labeled EXTENDED — the warning label correctly identified the risk. This is the directional validity we want to see.

### 3b. Stock Lens Forward (MEANINGFUL / ROBUST)

| Label | n | 5d avg | 5d vs SPY | 5d hit% | 10d avg | 10d vs SPY | 10d hit% |
|---|---|---|---|---|---|---|---|
| Bullish but not buyable yet | 301 | +2.49% | +1.38% | 63.5% | +5.65% | +3.65% | 68.1% |
| Bullish but extended | 4 | +3.67% | +2.15% | 75.0% | +20.70% | +22.17% | 100.0% |
| Neutral | 210 | +0.13% | −0.76% | — | +0.58% | −1.33% | — |
| Bearish but oversold | 17 | −0.89% | −1.89% | 52.9% | −0.51% | −2.63% | 35.3% |
| Bearish | 54 | −0.07% | −0.83% | 48.1% | −0.97% | −3.04% | 46.3% |
| Avoid / no edge | 17 | −1.30% | −2.32% | — | −0.94% | −2.96% | — |

**Key finding:** Directional ordering is valid. "Bullish" > "Neutral" > "Bearish" > "Avoid" across all horizons. Bullish names beat SPY. Bearish names lag SPY. Labels are not backwards.

**By confidence (Stock Lens):**

| Confidence | n | 5d avg | 5d vs SPY | 10d avg | 10d vs SPY |
|---|---|---|---|---|---|
| High | 384 | +1.19% | +0.21% | +2.57% | +0.53% |
| Medium | 56 | +0.73% | −0.16% | +2.42% | +0.45% |
| Low | 163 | +1.54% | +0.48% | +4.33% | +2.28% |

### 3c. Recall Repair Shadow Forward (ROBUST — n=2,415 at 5d)

| Horizon | Shadow avg rel SPY | Random avg rel SPY | RS-top rel SPY | Alpha rel SPY |
|---|---|---|---|---|
| 1d | +0.39% | +0.11% | +0.38% | +0.50% |
| 3d | +0.61% | +0.22% | +0.36% | +0.53% |
| 5d | **+0.91%** | +0.32% | +0.60% | +0.73% |
| 10d | **+1.68%** | +0.33% | +1.61% | +2.17% |
| 20d | **+4.27%** | −0.79% | +4.92% | +9.65% |

Shadow lane consistently beats random at every horizon from 1d through 20d. This is the most mature signal and earned its READY_TO_FEED_LENS_RESEARCH_ONLY verdict.

### 3d. Pre-Repair vs Post-Repair Cohort

| Period | Universe source | Alphabetical fallback | Sector attribution | Notes |
|---|---|---|---|---|
| Pre-repair (before ~Jun 15) | Partial — some runs used alphabetical fill | True on some runs | Incomplete (ETF pollution possible) | Not in forward tracker history |
| Post-repair (Jun 15+) | Ranked fill (931 names, 0 alphabetical) | False (all runs) | 100% in scanner | All 746 forward entries are post-repair |

There is no pre-repair forward evidence in the tracker. All 746 entries are from the post-repair period. A direct pre/post comparison is not possible — another reason the verdict is INCONCLUSIVE rather than PASS or FAIL.

### 3e. Universe Source Cohort

| Source | Count in universe | In scanner output? |
|---|---|---|
| alpha_board | 20 | 20 contribute to scanner |
| social_arb | 49 | 49 in universe, subset qualify for scanner labels |
| ranked_fill | 931 | Majority of scanner candidates |
| alphabetical_fallback | 0 | None (PASS) |
| daily_alpha_radar | 0 | None promoted today |

### 3f. Scanner Category Distribution (today)

| Category | Count |
|---|---|
| CATALYST | 18 |
| EARLY_ACCUMULATION | 15 |
| BEATEN_DOWN | 13 |
| WATCH | 11 |
| ASYMMETRIC_RECOVERY_WATCH | 11 |
| EXTENDED | 5 |
| SECTOR_LEADER | 2 |
| RISKY | 2 |
| NO_SOCIAL_DATA | 1 |

### 3g. Priority Label Distribution (Daily Alpha Radar)

| Priority Label | Count | Status |
|---|---|---|
| HIGH_PRIORITY_RESEARCH | 1 (HOOD) | Active |
| RESET_WATCH | 2 (NUE, TECH) | Parabolic extension, watchlisted |
| RECLAIM_WATCH | 6 (WGS, NKE, PAYX, RCAT, ONON, FLUT) | Recovery monitoring |
| WATCHLIST_RESEARCH | 17 | Secondary |
| CONFLICTED_SIGNAL | 14 | Blocked/mixed signals |
| EXTENDED_CROWDED | 22 | Warning label — avoid chasing |
| DATA_QUARANTINE | 16 (INSUFFICIENT_HISTORY: 16) | Young listings |

Total radar: 78 candidates.

---

## 4. Current High-Priority Names (Research Review Queue)

*No buy/sell signals, no entry/stop/target. Research-only.*

### HOOD — HIGH_PRIORITY_RESEARCH
- Category: EARLY_ACCUMULATION
- Sector: Financial Services
- Why appeared: Rising volume + improving RS or higher lows; not extended
- RS 63d vs SPY: +32.8pp; RS 20d vs SPY: +46.2pp
- Above MA50: Yes; Above MA200: Yes
- Earliness: DEVELOPING; Consensus: DOUBLE_CONFIRMATION
- Quality score: 89; Extension: NORMAL
- Data maturity: MEDIUM trust (price cache)
- Universe source: social_arb (also alpha board)
- Forward evidence: None yet (appeared June 27)
- Manual review required: YES — confirm RS trend, volume character

### NUE — RESET_WATCH (downgraded from EARLY_ACCUMULATION)
- Category: EARLY_ACCUMULATION (downgraded: extension_high_consensus)
- Sector: Basic Materials
- Earliness: LATE; Extension: PARABOLIC
- Data history: 274 bars (below 300-bar preferred floor)
- Forward evidence: Jun 17 appearance, ret_5d = −1.47% vs SPY −0.57%

### TECH — RESET_WATCH (downgraded)
- Category: EARLY_ACCUMULATION (downgraded: extension_high_consensus)
- Sector: Healthcare
- Earliness: LATE; Extension: PARABOLIC
- Data history: 274 bars
- Forward evidence: None yet in tracker

### NKE — RECLAIM_WATCH
- Category: CATALYST
- Sector: Consumer Cyclical
- Catalyst: Upcoming earnings 2026-06-30
- Earliness: RECLAIM_WATCH; Extension: NORMAL
- Forward evidence: None yet

### WGS — RECLAIM_WATCH
- Category: WATCH (social attention)
- Sector: Healthcare
- Source: social_attention_radar
- Forward evidence: None yet

### PAYX — RECLAIM_WATCH
- Category: BEATEN_DOWN
- Sector: Industrials
- Why: Large drawdown (10%/3m) with stabilization
- Earliness: RECLAIM_WATCH; Extension: NORMAL
- Data history: 300 bars (at floor)
- Forward evidence: None yet

---

## 5. What Improved Since Repairs

1. **Ranked fill operational.** 931/1000 universe slots filled from 4766 scored candidates. Alphabetical fallback = False. Universe quality improved dramatically.
2. **Alpha board fully wired.** 20 alpha-board names present and correctly sourced in the universe.
3. **Sector attribution: 100%.** Scanner reports 78/78 sector fields populated. Before fix: ETF pollution contaminated sector counts.
4. **Social arb injection capped and mapped.** 49 social_arb tickers with valid sector/industry attribution. Previously mapping failures produced junk entries.
5. **Data quarantine homogeneous.** All 16 quarantine entries = INSUFFICIENT_HISTORY (young listings). Previously mixed with entity-mapping errors and RS misalignment failures.
6. **RS date alignment fixed.** Price cache / RS date alignment corrected; RS percentiles now reflect the correct window.
7. **Benchmark readiness: LOADED.** SPY/QQQ/sector ETF baseline attached to forward entries. Previously no benchmarks existed.
8. **Forward tracker operational.** 746 entries recorded with ticker, date, label, sector ETF, and benchmark comparison fields. Infrastructure is in place.
9. **Stock Lens directional validity confirmed.** 603 matured lens snapshots show bullish labels outperforming bearish labels vs SPY across 5d and 10d. Labels are not backwards.
10. **Recall repair shadow: READY_TO_FEED_LENS.** Shadow lane beats random at every horizon (1d–20d) on 2,415+ matured ticker-days.

---

## 6. What Still Fails

1. **Forward evidence TOO_EARLY.** Only 14 entries have 5d returns in the primary tracker. Cannot make any provisional claim about the research radar's alpha on 14 observations.
2. **Scanner recall: 2.0% vs 19.1% RS baseline.** The engine still misses the vast majority of actual winners. Root cause = UNIVERSE_MISS (115/255 liquid winners never entered the universe). Ranked fill helps but the 1000-ticker cap combined with score weighting still misses many RS leaders.
3. **resolved=false tracking gap.** 14 entries have ret_5d populated but resolved=false in the JSONL. The forward tracker summary reports "0 matured" — misleading. Minor bug but creates confusion in the operator summary.
4. **351 lens snapshots missing price bars.** `forward_resolution_health` reports WARN: 351 snapshots old enough for their final horizon but missing price data (gaps in cache/prices/*.parquet). Forward edge may be understated.
5. **Options overlay: DISABLED.** Coverage 5% (4/78 names). Will remain disabled until IV accumulation reaches the 50% threshold (~Sep 2026 per 1J.1 projection).
6. **Suspect tickers in social_arb still entering universe.** PURR (meme-adjacent), BTQ (crypto-adjacent), SHAZ, STI (recycled ticker) appear in the social_arb slot. They are being quarantined correctly as INSUFFICIENT_HISTORY, but they consume universe slots that could hold better candidates.
7. **Targeted backfill not yet executed.** 25 tickers identified below 300-bar floor; dry-run only in nightly tail. Until executed, MA200 and deep RS calculations remain unreliable for those names.
8. **Social attention forward: 3 matured.** Far too early for any claim. NEED_MORE_DATA.
9. **Daily alpha radar forward tracker: daily_alpha_radar source = 0.** No names promoted from the alpha radar back into the universe today, suggesting the pipeline integration is incomplete or the radar's output wasn't fed forward.
10. **Scanner recall UNIVERSE_MISS remains the dominant bottleneck.** 115 winners never entered the 1000-ticker universe. Simple RS 20d baseline recalls 19.1%; the full scanner recalls 2.0%. This gap has not been closed by the current repairs.

---

## 7. Decision

**Continue nightly evidence collection only.**

The pipeline is structurally sound after repairs. The forward infrastructure is in place. But there is not enough post-repair forward evidence to judge whether the repaired engine produces alpha. The 14 matured entries are directionally neutral vs SPY (50% win rate, +1.12% mean). This is neither a pass nor a fail — it is simply too early.

Additionally:
- Fix the `resolved=false` tracking gap (minor, but the "0 matured" summary is misleading operators).
- Run `targeted-backfill --execute` to fill the 25 names below the 300-bar floor.
- Do not tune, relabel, or score-adjust anything until at least 30 entries mature.

**Phase 4B: BLOCKED** — No benchmarked forward evidence meets the maturity criteria (≥10d/3000 ticker-days/recall+FP/theme/sector). Evidence must accumulate organically.

---

## 8. Next 7-Day Plan (2026-06-27 to 2026-07-04)

| Day | Action |
|---|---|
| June 27 (today) | Fix `resolved=false` tracking gap in research_watchlist_forward_tracker.py so the operator summary correctly reports matured entries |
| June 27–28 | Run `targeted-backfill --execute --limit 25 --max-provider-calls 15` to close the 25 names below 300-bar floor |
| Daily June 28–July 4 | Run nightly; do not change scoring, thresholds, or labels |
| July 1–2 | Expect ~50–80 entries to reach 5d maturity (all June 15–20 entries); check cohort returns at that point |
| July 3–4 | If ≥30 entries have matured 5d returns with SPY/QQQ benchmarks, compute PROVISIONAL verdict for EARLY_ACCUMULATION and SECTOR_LEADER cohorts |
| July 4+ | If PROVISIONAL verdict is positive, plan Phase 4B proposal document. Do not proceed to Phase 4B until evidence meets criteria. |

**What NOT to do this week:**
- Do not change scoring or thresholds based on the 14-entry read.
- Do not promote any strategy or route to paper trading.
- Do not tune social arb or RS thresholds.
- Do not filter PURR/BTQ/SHAZ/STI manually — let the 300-bar quarantine handle them.

---

## Appendix: Pipeline Integrity Checklist

| Gate | Status |
|---|---|
| Nightly exit 0 | PASS |
| Research-only safety banner | PASS |
| No paper/live execution behavior | PASS |
| Alphabetical fallback = False | PASS |
| Ranked fill considered > 0 | PASS (4,766) |
| Ranked fill selected > 0 | PASS (931) |
| Alpha board contributes > 0 | PASS (20) |
| Social arb contributes > 0 | PASS (49) |
| Sector coverage ≥ 90% | PASS (100%) |
| Data quarantine = INSUFFICIENT_HISTORY only | PASS |
| No ETF/warrant in operating categories | PASS |
| Benchmark series loaded | PASS |
| Forward tracker recording new entries | PASS (1 new today) |
| Scanner recall > 2× random | FAIL (2.0% vs 19.1%) |
| Phase 4B allowed | BLOCKED |

---

*RESEARCH_ONLY — Not a signal. Not a recommendation. No live capital. Human review required before any action.*

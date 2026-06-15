---
strategy: SHORT Family — Sleeve A (Event Continuation) + Sleeve B (Broken Leader, design)
book: Short Tactical (Book C)
code_path: strategies/short_sleeve.py (Sleeve A) | strategies/short_broken_leader.py (Sleeve B, TBD)
owner: TBD
last_updated: 2026-05-04
validation_order: 1 (first in queue)
---

# SHORT Family Scorecard

**SHORT is the sole short-direction strategy family in this platform.**
All other strategies (Voyager, Sniper, Remora, Contrarian) are LONG-only.
Full doctrine: `docs/strategy/SHORT_DOCTRINE.md`

> **Phase 10B evidence note (2026-05-04, post historical export).** The
> heavyweight historical export (`research/sleeves/export_short_a_history.py`)
> ran with the doctrine friction stack (1%/yr borrow + 5 bps slippage each
> side + 5 bps spread each side + 0.5% halt-gap penalty). It returned
> **9 historical trades** over a 504-day lookback against a 200-name
> discovery universe; merged with 6 live-paper rows → 13 closed.
>
> **The aggregate evidence is negative.** Across all 13 closed trades:
> - Avg adj return: **−4.85% [−8.04%, −1.68%]** (95% CI fully below zero)
> - Win rate: **30.8% [7.7%, 61.5%]**
> - Stop-hit rate: **46.2%** — most historical trades hit the configured −10% stop
> - Universe-matched random control (n=65 synthetic entries at each trade's
>   actual hold_days): WR 49.2%, adj +0.86% — strategy underperforms random
>   by **−5.71pp adj, −18.5pp WR**.
>
> Phase 9B verdict from `docs/scorecards/evidence_rigor_report.md`:
> **`WEAK_AND_THIN`** (downgraded from `PROMISING_BUT_THIN` once the
> historical depth was added). This is honest negative evidence. Do not
> retune in this phase. Keep collecting live paper; optionally re-run the
> historical exporter with stricter friction assumptions to see whether
> the signal is robust to stop-loss geometry.

---

# Sleeve A — Event Continuation Short

## Mandate

Enter after a verified earnings disappointment produces a gap-down ≥ 3% on institutional
volume, confirmed by continuation of the sell-off within 3 sessions. Captures the early
post-event repricing window before broader market adjustment.

## Thesis

Negative earnings reactions with gap-down ≥ 3%, confirmed by above-average volume and continuation (close still pressing below event bar low within 3 sessions), offer a structured short window. The event trigger is dated, the confirmation is behavioral, and the exit geometry enforces R:R ≥ 2.0 from entry.

## Signal Conditions

1. FMP earnings date confirmed via `/stable/earnings-calendar`
2. Gap reaction ≤ -3.0% on earnings session open (event bar open vs prior close)
3. Volume ≥ 1.5× 20-day average (institutional selling, not retail noise)
4. Continuation: today's close < event bar low (still pressing, not rebounding)
5. Lag ≤ 3 sessions between event bar and signal bar
6. R:R ≥ 2.0 enforced. Stop: entry + 1.5× ATR. Target: entry − 2× stop-distance

## Required Data

- Alpaca: daily OHLCV bars (30+ days lookback)
- FMP: `/stable/earnings-calendar` historical date ranges

---

## Validation Status

| Gate | Status | Notes |
|------|--------|-------|
| data-valid | **complete** | FMP endpoint fixed (was calling legacy `/v3/` path — now uses `/stable/earnings-calendar`); `get_past_earnings()` verified returning 2,630+ events/month |
| backtest-valid | **complete** | `research/backtests/short_v1_backtest.py` run 2026-04-18: 23 signals, 65-ticker universe, 2022–2024 |
| paper-valid | pending | not started |

---

## Code Audit — 2026-04-18

### Bugs Found and Fixed

**Critical: FMP endpoint broken in production**
`short_sleeve.py._recent_earnings()` was calling `self._fmp._get("/v3/earning_calendar", ...)` directly. Because `FMP_BASE_URL = https://financialmodelingprep.com/stable`, this resolved to `…/stable/v3/earning_calendar` — a 404. The Starter plan does not have `/v3/` access. Production was generating **zero signals** since the FMP_BASE_URL migration.

Fix: Added `get_past_earnings(lookback_days)` to `FMPClient` (uses `/earnings-calendar` with `from`/`to` date params). `short_sleeve.py` now calls `self._fmp.get_past_earnings(lookback_days)`.

**Minor: `eps_actual` field name**
`event.get("eps")` → `event.get("epsActual")`. FMP stable API returns `epsActual` (not `eps` as in legacy v3). Field was silently returning `None` in all signals.

### AMC/BMO Timing — Known Limitation (Not a Lookahead Bug)

For AMC (after-market-close) earnings, FMP records `event_date` as the announcement day but the gap appears the following morning. The production scanner measures the gap on bar[event_date] → measures pre-announcement bar → near-zero gap → rejected at the gap gate. This causes **false negatives for roughly 50% of earnings events** (AMC reporters). BMO events are handled correctly.

This is **not a lookahead issue**. The scanner is conservative; it simply misses half the opportunity set. Fix (future): detect AMC vs BMO from time-of-day in FMP data and advance `ed_idx` by 1 for AMC events. Implementing this fix would approximately double signal count.

### Council Profile Gap

The VetoCouncil (`core/veto_council.py`) uses a single generic weight set for all strategies. No SHORT-specific profile exists. Known interactions:
- **RegimeAgent**: blocks if VIX > 40. 2022 bear (VIX 25–35) passes; crisis (VIX > 40) blocks.
- **EarningsAgent**: scores 80 if no upcoming earnings (correct for SHORT — trigger is on past earnings).
- **MomentumAgent**: negative 20d momentum → higher score. Post-gap-down tickers have this naturally.
- **FlowAgent**: intraday volume deceleration. Meaningful for momentum strategies; less meaningful for event shorts.

The missing piece: a SHORT-specific council profile weighting MomentumAgent higher and FlowAgent lower.

---

## Backtest Results — 2026-04-18

**Script:** `research/backtests/short_v1_backtest.py`
**Universe:** 65 tickers (high-beta growth, consumer, semis, media, biotech, airlines, fintech, China ADRs, quality controls)
**Event window:** 2022-01-01 → 2024-09-30 (33 monthly FMP API calls)
**Friction model:** 0.05% commission + 0.05% slippage each way + 1.0% annual borrow
**No lookahead:** price bars sliced strictly to signal date; continuation checked before any forward price access

### Signal Funnel

| Stage | Count | % of events |
|-------|-------|-------------|
| Events evaluated | 357 | — |
| Gap too small (> −3%) | 295 | 82.6% |
| Volume insufficient (< 1.5×) | 4 | 1.1% |
| No continuation within 3 sessions | 33 | 9.2% |
| Out-of-range / no events found | 5 | 1.4% |
| **Signals generated** | **23** | **6.4%** |

Gap qualifiers (gap ≤ −3%): 60 events (16.8%)
Continuation filter eliminates 33/60 gap qualifiers (55%). This is the most discriminating gate after gap size.

### Performance Summary

| Horizon | N | Avg Raw | Avg Adj (with friction) | Win Rate | Expectancy |
|---------|---|---------|------------------------|----------|------------|
| 5d | 23 | +1.3% | +1.1% | **56.5%** | +1.3% |
| 10d | 23 | −0.3% | −0.6% | 43.5% | −0.3% |
| 20d | 23 | +1.2% | +0.9% | 43.5% | +1.2% |

**10d is the weakest horizon.** Stop-hit rate at 10d is 26.1% (6/23). The stop at 1.5× ATR above entry may be too tight for 10-day holds in high-volatility names. Short positions that were held to 10d without stop had mixed outcomes.

### Return by Gap Severity (10-day raw)

| Gap bucket | N | Avg 10d | Win Rate |
|------------|---|---------|----------|
| gap < −10% | 9 | +0.8% | 44.4% |
| −10% to −7% | 6 | +2.3% | **66.7%** |
| −7% to −5% | 3 | +1.2% | 33.3% |
| −5% to −3% | 5 | −6.5% | 20.0% |

**Key insight:** Signals with gap in the −7% to −10% range show the best win rate (66.7%). The marginal-gap bucket (−5% to −3%) has 20% win rate — the continuation gate is insufficient to rescue borderline gap setups.

### Signals by Ticker

| Ticker | Signals | Avg Gap | Avg Vol | Avg 10d Raw |
|--------|---------|---------|---------|-------------|
| BBWI | 2 | −4.3% | 1.90× | −1.7% |
| M | 2 | −9.0% | 6.22× | +4.0% |
| KSS | 2 | −6.5% | 4.50× | −3.8% |
| DG | 2 | −22.0% | 13.93× | +3.1% |
| DLTR | 2 | −9.1% | 6.44× | +0.4% |
| NYCB | 1 | −42.6% | 17.17× | +12.2% |
| PLTR | 1 | −11.3% | 3.07× | −13.1% |
| SHOP | 1 | −10.1% | 4.22× | +9.0% |
| ANF | 1 | −13.8% | 5.98× | −1.6% |
| PTON | 1 | −13.3% | 3.54× | −4.2% |
| (8 more single-signal tickers) | | | | |

Concentration: 18 of 65 tickers produced at least one signal over 3 years. Consumer/retail (M, KSS, DG, DLTR, BBWI) generates more signals than high-growth tech. The largest gap event was NYCB (−42.6%, Jan 2024 bank stress).

### Signal Characteristics

| Metric | Min | Avg | Max |
|--------|-----|-----|-----|
| Gap % | −42.6% | −10.5% | −3.1% |
| Volume ratio | 1.74× | 5.42× | 18.53× |
| Lag sessions | 1 | 1.2 | 3 |
| Score | 74 | 95.4 | 100 |

Lag=1 dominates (83% of signals). The continuation fires the day after the event in most cases. Scores are uniformly high (avg 95.4) because the combination of large gaps + high volume saturates the score formula.

### Outcome Breakdown (10d primary horizon)

| Outcome | Count | % |
|---------|-------|---|
| Held to 10d | 17 | 73.9% |
| Stop hit | 6 | 26.1% |
| Target hit | 0 | 0.0% |

The target (entry − 2× stop-distance) was never reached at 10d. This suggests the 10-day window is too short for full repricing. Shorts that went to plan needed more time to play out.

---

## Council Simulation (Simplified)

Full council simulation requires live API calls; not run in backtest. Structural assessment:

- **RegimeAgent**: no blocking expected for 2022 signals (VIX 25–35, bear market). Would have blocked any signals coinciding with VIX > 40 spikes.
- **EarningsAgent**: all short signals trigger on past earnings. Upcoming earnings unlikely within 5 days → scores 80 (favorable for signal passage).
- **MomentumAgent**: post-gap-down tickers have negative 20d momentum → directionally aligned with SHORT signal.
- **Structural gap**: no SHORT-specific council weights. Council was designed with LONG strategies in mind.

---

## Event Timing Integrity

**No lookahead contamination.** Verified:
- Gap measured: `event_bar.open vs prev_bar.close` — uses open price of first bar ≥ event_date
- Volume measured: 20-bar average strictly before the event bar
- Continuation measured: close of bar at (ed_idx + lag) vs event_bar.low
- Forward returns: start from signal_date bar, never reference prices before signal

**Known false-negative source (not lookahead):** AMC earnings events. FMP records event_date as announcement day; gap appears next morning. Scanner measures gap on announcement day bar → near-zero → rejected. Approximately 50% of earnings events are AMC. Backtest results reflect this coverage gap — true signal frequency after AMC fix would be approximately 2×.

---

## Verdict

**Signal count: 23** (target ≥30 for statistical validity — below threshold)

**10-day primary horizon: edge does not clearly survive friction**
- Avg adj return: −0.6%
- Win rate: 43.5%
- Stop hit 26% of the time at this horizon

**5-day horizon shows marginal positive expectancy** (+1.1% adj, 56.5% WR) but the sample is too small to assert statistical significance.

**Structural observations:**
1. The scanner fires on real events with real gap/volume characteristics — logic is sound
2. The FMP endpoint fix was critical; production was generating zero signals prior
3. The −7% to −10% gap bucket outperforms (+2.3% 10d, 67% WR) — a potential future filter
4. Consumer/retail sector generates more signals than high-growth tech in this universe
5. AMC coverage gap means this is a lower-bound sample — true signal count is higher
6. No signals ever hit the 2× target at 10d — hold period may be too short for full repricing

**Paper Readiness: CONDITIONAL**

The strategy is structurally sound and the production bug is fixed. The backtest does not show compelling positive expectancy at the 10-day horizon, but the sample is borderline (n=23 vs. ≥30 threshold) and the AMC gap materially suppresses signal count. Proceeding to paper phase is appropriate to build real sample size with the fixed scanner.

**Not ready for capital until:**
1. ≥30 paper signals collected and tracked
2. AMC/BMO timing fix evaluated (would ~double signal count)
3. Short-specific council profile assessed
4. Borrow availability verified for each signal ticker
5. 10d win rate ≥ 50% sustained in paper phase OR evidence that a different hold period (5d or longer) improves expectancy

---

## Paper-Cycle Success Criteria

| Gate | Threshold | Status |
|------|-----------|--------|
| Signal count | ≥30 paper signals logged | pending |
| Raw win rate (10d) | ≥50% | pending |
| Avg adj return (10d) | > 0% | pending |
| Stop hit rate | < 35% | pending |
| No single ticker > 30% of signals | concentration check | pending |
| Gap > −7% signals: WR ≥ 40% | marginal-gap viability | pending |
| AMC fix evaluated | detect BMO/AMC, retest | pending |

---

## Promotion Blockers

- [ ] Paper phase not started (waiting for ALLOW_SHORTS=true in config)
- [ ] Sample below statistical threshold (n=23 backtest; need ≥30 paper)
- [ ] 10d edge does not clearly survive friction in backtest
- [ ] AMC timing coverage gap unresolved (fix documented, not implemented)
- [ ] No SHORT-specific council profile

---

## Resolved

- [x] FMP endpoint fixed (critical — was returning zero signals in production)
- [x] `epsActual` field name corrected
- [x] AMC/BMO limitation documented with clear fix path
- [x] Direction mandate locked in `STRATEGY_DOCTRINE.md`
- [x] Historical backtest run: `short_v1_backtest.py` 2022–2024, 65-ticker universe
- [x] Event timing integrity verified (no lookahead contamination)

---

## Next Experiment

**Option A (recommended):** Implement AMC/BMO detection using FMP time-of-day data and advance reaction bar by 1 for AMC events. Re-run backtest. Expected: ~2× signal count, clearer read on expectancy.

**Option B:** Enable `ALLOW_SHORTS=true` and begin paper phase with current scanner. Track signals in DB. After ≥30 paper signals, assess paper-valid gates.

**Option C:** Add gap severity filter (gap < −7% required instead of −3%). Reduces signal count but improves win rate based on backtest gap-severity breakdown.

The scorecard will be updated after each paper signal batch and after the AMC fix is evaluated.

---
---

# Sleeve B — Broken Leader / Structural Deterioration Short

**Status:** Design phase. Not yet coded or backtested.
**Code:** `strategies/short_broken_leader.py` (to be created)
**Doctrine reference:** `docs/strategy/SHORT_DOCTRINE.md §Sleeve B`

---

## Identity

Sleeve B captures the gradual repricing of former market leaders that have broken
structural support and exhibit institutional distribution signatures. It does not require
an earnings event. It requires behavioral evidence that smart money has been exiting,
confirmed by a specific failed rally attempt.

**The edge:** Former leaders are "known names" with active retail buyer bases. When
institutional holders distribute, retail "buy the dip" behavior creates a series of
failed rally attempts into supply overhead. Entering after the first confirmed failed
reclaim gives a setup with specific invalidation (if price recovers) and directional
confirmation (if supply continues to hold it down).

---

## What It Is Not

This is NOT:

- a valuation short ("PE is too high")
- a momentum exhaustion short ("RSI is elevated, it must fall")
- a generic "overbought fade"
- a short triggered simply by a prior strong run
- anything that would require calling the exact top of a trend

All of the above have weak edges and high false-positive rates for retail traders. The
Broken Leader setup requires that the break has already happened and been confirmed
by behavioral evidence. It does not predict tops — it observes failed recoveries.

---

## Proposed Signal Conditions

### Screening Gate (required prior to any timing logic)

1. **Prior leadership**: stock achieved ≥ +40% total return within any trailing 18-month
   window in the last 3 years. This confirms the name was institutionally held and
   meaningful, not just a random weak stock.
2. **Structural break confirmed**: current price < 50-day MA AND a lower high has been
   established after the prior peak (price bounced but did not exceed the last significant
   high). The trend has already reversed.
3. **Relative weakness**: 20-day return vs SPY ≤ −8%. The name is underperforming the
   market, not just falling in a falling market.

### Entry Timing Gate (specific signal trigger)

4. **Failed rally attempt**: price bounced toward the 50-day MA or prior resistance (within
   3–5%), then closed back below the prior bounce low. This is the specific entry event —
   not the structural breakdown, but the confirmed failure to recover from it.
5. **Distribution confirmation**: ≥ 2 of the last 10 sessions had volume > 1.3× 20-day
   average on a down close. Supply is active, not passively drifting.
6. **Exhaustion guard**: RSI at entry between 35 and 58. Below 35 = already oversold,
   too late. Above 58 = not broken enough, entry is premature.
7. **Drawdown range**: current decline from recent high between −20% and −50%. Inside
   this range the repricing is likely incomplete. Beyond −50%, mean reversion risk rises.

### Safety Gates (hard filters, no exceptions)

8. ADV ≥ $50M (avoid illiquid names where exit is difficult)
9. Price ≥ $15 (avoid penny-stock dynamics)
10. No earnings within 5 trading days (Sleeve A handles event-driven shorts; Sleeve B
    should not be in the earnings window — conflation reduces quality of both signals)

### Trade Geometry

- Entry: close below failed bounce low (the break trigger)
- Stop: above the failed bounce high OR above 50-day MA, whichever produces tighter risk
- Target: 2× risk distance OR nearest major structural support level
- R:R enforced ≥ 2.0; if geometry doesn't work, no trade
- Hold horizon: 15–30 trading days

---

## Difference from Sleeve A

| | Sleeve A | Sleeve B |
|-|----------|----------|
| Trigger | Verified earnings event | Failed rally after structural break |
| Requires prior leadership | No | Yes (≥ +40% prior) |
| Requires RS deterioration | No | Yes (underperforming SPY) |
| Volume requirement | Single-day event spike | Multi-session distribution pattern |
| Typical hold | 5–10 days | 15–30 days |
| Near-earnings behavior | Earnings is the trigger | Block near earnings |
| Edge source | Speed of post-event repricing | Lag between distribution and retail recognition |

---

## Difference from Voyager

Voyager finds quality businesses being accumulated by institutions.
Sleeve B finds former leaders being distributed by institutions.
These are near-perfect opposites on every observable dimension:

| Voyager (LONG) | Sleeve B (SHORT) |
|----------------|-----------------|
| Volume increasing on up days | Volume increasing on down days |
| Price holding above key MAs | Price below 50-day MA, failed to reclaim |
| RS improving vs market | RS deteriorating vs market |
| Lower highs have NOT formed | Lower highs confirmed |
| Accumulation signal | Distribution signal |

If a ticker would qualify for Voyager's accumulation screen AND Sleeve B's deterioration
screen simultaneously, that is a data or logic error — not a signal conflict.

---

## Council Profile (When Implemented)

Sleeve B requires a different council weight profile than Sleeve A:

- **RegimeAgent**: HIGH weight. Sleeve B works best in declining or bear regimes.
  In strong bull markets, even broken leaders can be lifted. Regime context is the
  most important filter for Sleeve B viability.
- **RelativeStrengthAgent** (to be built or approximated): HIGH weight.
  RS deterioration vs SPY is a core gate, not just confirmation.
- **EarningsAgent**: MODERATE weight — but as a BLOCK (near upcoming earnings
  means this is not a clean Sleeve B setup), not a positive scoring input.
- **FlowAgent**: MODERATE weight. Distribution day counting aligns with this agent's
  intraday volume analysis.
- **MomentumAgent**: LOW weight. Negative momentum IS the signal — it's already in the
  screening gates. The council shouldn't double-count it.

---

## Backtest Plan (Before Coding)

**Target sample size:** ≥ 40 signals for statistical validity

**Universe (80–90 tickers):**

Category | Examples
--- | ---
Prior SaaS/growth leaders (2020–2021 peaks) | NFLX, SNAP, PYPL, DOCU, ZM, SHOP, COIN, ROKU, AFRM, UPST, TWLO, HOOD, OKTA, SQ
Former hyped consumer | PTON, W, ETSY, CHWY, BBWI, ANF
Former large-cap that broke (2022) | META, DIS, PARA, WBD
Consumer discretionary breaks | NKE (2023–2024), LULU, DG, DLTR
China ADRs that broke | BABA, JD
Airlines/travel post-COVID normalization | AAL, CCL, RCL
**Controls — should rarely signal** | NVDA, MSFT, AAPL, LLY (strong leaders throughout)

**Backtest window:** 2021-01-01 → 2024-09-30
- 2021 peak: validates leadership prerequisite logic
- 2022 bear: primary testing regime for structural breaks
- 2023–2024: tests what happens after the initial break (some recover, some keep falling)

**Friction model:**
- Commission: 0.05% each way
- Slippage: 0.10% each way (higher than Sleeve A — broken leaders are volatile)
- Borrow: 2.0% annualized (broken leaders are harder to borrow than easy-to-borrow names)

**Hold horizons to test:** 15d, 30d, 45d

**Stop/target variants to test:**
- Stop above failed bounce high (specific)
- Stop 5% above entry (absolute max, avoids wide stops on high-ATR names)
- Target: 2× risk OR prior support (whichever is closer)

**Key validation questions:**
1. Does the leadership prerequisite (≥ +40% prior) meaningfully reduce false positives
   on random weak names?
2. Do controls (NVDA, MSFT) produce ≤ 2 signals over the full period?
3. Does the failed rally trigger outperform "just broke MA" as a timing method?
4. What is the win rate by hold horizon? Is 30d meaningfully better than 15d?
5. Which sector produces the most signals? Is this a consumer/tech split?
6. Does distribution day count (≥2 of 10) add incremental filtering value?

**Sample size assessment:** If full backtest produces fewer than 20 signals, the
universe or screening criteria may be too tight. If it produces more than 80, the
criteria may be too loose. Target 30–60 signals for a calibrated evaluation.

---

## Validation Status

| Gate | Status | Notes |
|------|--------|-------|
| data-valid | **complete** | Manual validation on META/SNAP/PYPL confirmed logic fires at correct points |
| backtest-valid | **FAILED** | Three runs: v1 n=99 (pre-calibration, negative edge), v2 n=14, v3 n=16 (calibrated, 2018-2024). Negative edge persists. Two structural blockers identified. |
| paper-valid | **not started** | Not pursued until stop geometry and leadership filter are fixed |

**Current status: RESEARCH-ONLY. Not paper-ready.**

---

## Backtest Results — v1 (2026-04-18, pre-calibration)

**Script:** `research/backtests/short_b_backtest.py`
**Universe:** 52 tickers (SaaS leaders, consumer, big-cap breaks, China ADRs, controls)
**Signal window:** 2021-01-01 → 2024-12-31
**Note:** FMP daily budget exhausted during run — earnings proximity block inactive.
Signals near earnings events were not filtered. Re-run with full FMP budget to
activate this gate; expect slight reduction in signal count.

### Manual Validation (META, SNAP, PYPL)

Before running the full backtest, conditions were validated manually on three known
2022 breakdown names. Key finding: the failed rally trigger fires at logical entry
points when the bounce-peak stop is used. The MA-approach condition was removed from
the failed rally definition — it was too tight for violent single-event breakdowns
(e.g. META Feb 2022 gap) where the 50d MA stays far above price for months.

### Signal Funnel

| Rejection gate | Count | % of bars |
|---------------|-------|-----------|
| Price above 50d MA | 23,878 | 49.0% |
| Drawdown too deep (> −55%) | 6,409 | 13.1% |
| RSI out of range (33–55) | 4,105 | 8.4% |
| RS vs SPY too strong | 3,661 | 7.5% |
| Insufficient distribution | 3,412 | 7.0% |
| No lower high established | 3,314 | 6.8% |
| Drawdown too shallow (< −20%) | 2,376 | 4.9% |
| Geometry / earnings block | 720 | 1.5% |
| No failed rally found | 362 | 0.7% |
| Price too low | 205 | 0.4% |
| Dedup cooldown | 107 | 0.2% |
| No prior leadership | 96 | 0.2% |
| **Signals** | **99** | **0.20%** |

### Performance Summary

| Horizon | N | Avg Raw | Avg Adj | Win Rate | Stop Rate |
|---------|---|---------|---------|----------|-----------|
| 20d | 99 | −0.6% | −1.1% | 44.4% | 38.4% |
| 30d | 99 | +0.1% | −0.3% | 41.4% | — |

Edge does not survive friction at either horizon at current conditions.

### Critical Finding: Drawdown Bucket Split

| Drawdown at entry | N | Avg 20d | Win Rate |
|------------------|---|---------|----------|
| −20% to −30% | 41 | **+0.6%** | **56.1%** |
| −30% to −40% | 30 | −0.9% | 40.0% |
| −40% to −55% | 28 | −2.3% | 32.1% |

**The early-break signals (−20% to −30%) show marginal positive expectancy (56% WR,
+0.6% raw ≈ +0.1% after friction). The deep-breakdown signals (−40%+) are clearly
negative and are dragging down the overall average.**

This is the primary calibration insight: Sleeve B works early in the breakdown,
not in the middle or late stages. The −40%+ region has significant mean-reversion
risk (violent bear market rallies easily hit stops).

### Control Analysis

9 control signals out of 99 total (9%). Controls: NVDA, MSFT, AAPL, AMZN, LLY, V,
GOOGL. GOOGL fired 3 times — highest among controls. All at drawdown −20% to −37%.
Control avg 20d: −3.5%, WR 44% — modestly worse than target names, but not
dramatically different. The leadership prerequisite filters for institutional quality
but doesn't fully exclude mega-caps that had temporary drawdowns.

### Best Individual Names (20d)

| Ticker | Signals | Avg 20d | WR |
|--------|---------|---------|-----|
| SQ | 5 | +8.0% | 80% |
| CHWY | 5 | +12.1% | 80% |
| DOCU | 3 | +12.3% | 100% |
| NFLX | 1 | +27.7% | 100% |
| DIS | 4 | +0.5% | 75% |

### Problem Names

| Ticker | Signals | Avg 20d | WR | Issue |
|--------|---------|---------|-----|-------|
| BABA | 7 | −5.1% | 29% | China political risk creates unpredictable behavior |
| JD | 3 | −11.4% | 0% | Same — China ADRs should be excluded or treated separately |
| ETSY | 5 | −7.2% | 20% | Multiple very deep DD signals (−54%), stop-hit pattern |
| INTC | 2 | −10.0% | 0% | Structural decline with violent bear rallies |

### Regime Context

| Year | Signals | Avg 20d | WR |
|------|---------|---------|-----|
| 2021 | 25 | +1.7% | 48% |
| 2022 | 27 | −0.2% | 48% |
| 2023 | 22 | +0.6% | 46% |
| 2024 | 25 | −4.5% | 36% |

2024 was the worst year — strong bull market made structural shorts difficult.
This confirms the RegimeAgent should have HIGH weight for Sleeve B: the strategy
is regime-dependent and should reduce activity in strong bull environments.

---

## Backtest Results — v2 Calibrated (2026-04-19)

**Changes applied vs v1:**
1. Drawdown range tightened: −20% to −35% (from −55%)
2. SPY regime gate added: SPY must be below its 50d MA on signal date
3. China ADRs (BABA, JD) removed from universe

**Results:**

| Universe | Signals | Avg 20d Raw | WR (20d) | Stop Rate |
|----------|---------|-------------|----------|-----------|
| 50 tickers, 2021–2024 | **14** | −5.1% | 28.6% | 50.0% |

**Note:** FMP daily budget was exhausted again during this run — earnings-proximity block
loaded 0 tickers. Results include signals that may fall within 5 days of earnings events.

**Verdict:** n=14 is statistically inert. No conclusions can be drawn. The calibration
reduced signal count from 99 to 14 — a dramatic over-constraint for a 2021-2024 window.

**Why 14?** The SPY regime gate eliminated 1,466 bar-days. Combined with the −35% drawdown
cap and universe shrink, the 4-year window simply doesn't contain enough qualifying
bear-regime periods with this universe size. The regime gate is correct in principle; the
issue is that 2021-2024 contained only one meaningful bear market window (2022).

**Control problem:** 6 of 14 signals (43%) came from control tickers (GOOGL, NVDA, MSFT,
AAPL, AMZN). This rate is too high and reflects the small sample rather than a logic
failure — but it means the calibrated conditions are not yet selective enough at n=14.

**Rejection funnel (v2):**

| Rejection bucket | Count |
|-----------------|-------|
| Price above 50d MA | 23,098 |
| Drawdown too deep (> −35%) | 12,497 |
| No lower high | 3,298 |
| Drawdown too shallow (< −20%) | 2,375 |
| RS too strong | 1,813 |
| RSI out of range | 1,653 |
| SPY regime too strong | 1,466 |
| Insufficient distribution | 396 |
| No failed rally | 53 |
| Geometry / earnings | 50 |
| Dedup cooldown | 11 |
| Price too low | 9 |
| No prior leadership | 3 |

---

## Backtest Results — v3 Final Calibrated (2026-04-20, 2018–2024)

**Script:** `research/backtests/short_b_backtest.py`
**Universe:** 50 tickers (BABA/JD excluded), 2018-01-01 → 2024-12-31
**Earnings block:** Active for 2021-2024 (FMP returns 402 for pre-2021 historical range)
**Friction:** 0.10% slippage each way, 0.05% commission each way, 2.0% annual borrow

### Signal Funnel

| Rejection bucket | Count |
|-----------------|-------|
| Price above 50d MA | 39,308 |
| Drawdown too deep (> −35%) | 15,008 |
| No lower high | 6,318 |
| Drawdown too shallow (< −20%) | 3,934 |
| RS too strong | 2,645 |
| RSI out of range | 2,466 |
| SPY regime too strong | 1,999 |
| Insufficient distribution | 571 |
| Geometry / earnings | 94 |
| No failed rally | 74 |
| Dedup cooldown | 11 |
| No prior leadership | 10 |
| Price too low | 9 |
| **Signals** | **16** |

### Performance Summary

| Horizon | N | Avg Raw | Avg Adj | Win Rate | Stop Rate |
|---------|---|---------|---------|----------|-----------|
| 20d | 16 | **−5.9%** | **−6.3%** | **25.0%** | **56.2%** |
| 30d | 16 | −4.6% | −5.1% | 25.0% | — |

### Drawdown-Bucket Results

| Drawdown at entry | N | Avg 20d | Win Rate |
|------------------|---|---------|----------|
| −20% to −30% | 10 | −5.9% | **30.0%** |
| −30% to −35% | 6 | −5.8% | 16.7% |
| −40% to −55% | 0 | N/A | — |

**Critical finding:** The −20% to −30% bucket that showed 56% WR / +0.6% avg in v1 (n=41)
produced 30% WR / −5.9% avg in the calibrated run (n=10). The v1 positive edge in that
bucket was driven by taking signals during SPY uptrend periods (2021, 2023, 2024) where
"broken leaders" recovered with the market. The regime gate correctly removed those
signals. What remains — bear-regime-only signals — does not show positive edge.

### Control Ticker Analysis

**6 of 16 signals (37.5%) are from control tickers** (NVDA, MSFT, AAPL, AMZN, GOOGL).
Target was < 10%. The leadership prerequisite (≥40% in any 18-month window) passes all
mega-caps that ran hard in 2020–2021 but dipped temporarily in 2022. These are not
structurally broken leaders — they are high-quality names that fell with the 2022 bear.

### Regime Analysis

| Year | Signals | Avg 20d | WR |
|------|---------|---------|-----|
| 2018 | 2 | −11.2% | 0% |
| 2021 | 1 | −11.2% | 0% |
| 2022 | 8 | −3.1% | 37.5% |
| 2023 | 3 | −4.5% | 33.3% |
| 2024 | 2 | −11.1% | 0% |

SPY regime gate rejected 1,999 bars — approximately 1 qualifying bear window per year.
2022 remains the primary signal source (8 of 16). Even in the best year (2022), WR is
37.5% — below the 50% threshold. The regime gate is conceptually correct; the
strategy doesn't have an edge within qualifying bear periods on this universe.

### Pre-Backtest Checklist Assessment (2026-04-20)

Two checklist stop conditions triggered:

1. **n < 20 after 7-year window** → "investigate whether conditions are too tight or
   universe is too small" — confirmed: SPY gate + drawdown gate + 50-ticker universe
   produces only ~2 signals/year in qualifying bear periods

2. **Controls > 20% of signals (37.5%)** → "leadership/quality filter insufficient" —
   confirmed: ≥40% in 18-month window admits all 2020-2021 mega-cap leaders temporarily

Both stop conditions correctly identify real structural problems with the current design.

---

## Honest Verdict (2026-04-20)

**REMAIN RESEARCH-ONLY.**

The 2018-2024 calibrated run did not produce a positive expectancy. The specific
problems are:

**Blocker 1 — Stop geometry too tight (56% stop-hit rate):**
`failed_bounce_peak × 1.015` (1.5% buffer) is getting hit on the next session's high
before the short thesis plays out. After a failed rally, stocks commonly probe 1-3%
above the prior bounce peak before resuming downside. The 1.5% buffer is too narrow
for this volatility class. Fix: widen to `× 1.04` or `× 1.05`, accept lower R:R (1.5×).

**Blocker 2 — Leadership filter not discriminating (37.5% control rate):**
AAPL, AMZN, NVDA, MSFT, GOOGL all satisfy "≥40% in any 18-month window" because they
ran hard in 2020-2021. These are not broken leaders — they are temporarily-dipped
mega-caps that recovered cleanly. Fix: require the leadership peak to be ≥ 2 years
old (ruling out names still near all-time highs), AND require that the 52-week high
is NOT in the last 6 months (the name should have failed a full recovery attempt).

Neither fix requires a fundamental redesign. But they represent a v4 calibration pass,
not a cosmetic tweak — both change which signals fire and need a clean re-run.

**What the positive v1 bucket was actually measuring:**
The v1 −20%/−30% bucket at 56% WR / +0.6% raw (n=41) was largely driven by signals
taken during SPY uptrend periods where structurally short positions got bailed out by
rising markets. This is not an edge — it is noise from taking the wrong-direction trade
in the wrong regime and still occasionally winning. The regime gate was the right fix;
it exposed the absence of real edge underneath.

---

## Blockers Before Next Research Pass

- [ ] Stop buffer: change to `failed_bounce_peak × 1.04–1.05`, reduce R:R minimum to 1.5×
- [ ] Leadership filter: require peak age ≥ 2 years AND 52-week high not within last 6 months
- [ ] These are v4 calibration changes — require a clean re-run before any paper consideration
- [ ] FMP historical earnings coverage: returns 402 for pre-2021 dates (higher plan required)

---

## Paper-Cycle Success Criteria (when research pass is complete)

| Gate | Threshold | Status |
|------|-----------|--------|
| Calibrated backtest signals | ≥ 40 | **not met** (n=16, 7-year window) |
| Win rate (20d) | ≥ 50% | **not met** (25%) |
| Avg adj return (20d) | > 0% | **not met** (−6.3%) |
| Stop hit rate | < 30% | **not met** (56.2%) |
| Control signals | < 10% of total | **not met** (37.5%) |

---

## Resolved

- [x] Manual validation: conditions fire at logical entry points on META/SNAP/PYPL
- [x] Script written: `research/backtests/short_b_backtest.py`
- [x] v1 backtest run: 99 signals, 52 tickers, 2021-2024 (pre-calibration)
- [x] Failed rally definition: simplified to bounce-magnitude only (no MA-approach requirement)
- [x] Stop geometry: failed bounce peak × 1.015 (not 50d MA reference — but too tight, see blocker 1)
- [x] Leadership prerequisite: implemented (≥40% in 18-month window — but too loose, see blocker 2)
- [x] Drawdown range tightened to −20% to −35% (calibration v2, applied in code)
- [x] SPY regime gate implemented: SPY close < SPY 50d MA (applied in code)
- [x] China ADR exclusion: BABA/JD removed from universe (applied in code)
- [x] v3 calibrated run: 2018-2024 window, n=16, honest verdict rendered (2026-04-20)
- [x] Pre-backtest checklist applied: two stop conditions triggered, both confirmed real problems

---

## Hype / Exhaustion Short — Explicitly Deferred

A third short category (Sleeve C) — shorting parabolic names at apparent exhaustion —
is not part of the current plan. It is not added until Sleeve A and Sleeve B are
both paper-validated. See `docs/strategy/SHORT_DOCTRINE.md §5` for the explicit
deferral rationale and the conditions required before Sleeve C can be considered.

<!-- RIGOR_AUDIT_BEGIN -->

## Evidence rigor strip (auto-generated)

_Last audit: 2026-05-04 02:01 UTC · see `docs/scorecards/evidence_rigor_report.md`._

- **Verdict:** **WEAK_AND_THIN**
- **Source:** backtest_csv
- **Sample (closed):** n = 13 (open = 2)
- **Primary horizon (5d):** n=2  ·  avg adj +1.69% [+1.20%, +2.18%]  ·  WR 100.0% [100.0%, 100.0%]
- **All horizons aggregate:** n=13  ·  avg adj -4.85% [-8.04%, -1.68%]  ·  WR 30.8% [7.7%, 61.5%]
- **Random control:** WR 40.0%  ·  avg adj -0.76%  ·  n=10
- **Walk-forward:** not run (insufficient data span)

<!-- RIGOR_AUDIT_END -->

<!-- AUTOPSY_BEGIN -->

## Autopsy summary (2026-05-04)

_Source: `docs/research/SLEEVE_FAILURE_AUTOPSY.md` · `docs/scorecards/sleeve_failure_autopsy.json` · evidence-only language._

**Cohort split (closed trades):**

| Cohort | n closed | WR % | Stop-hit % | Avg adj % |
|---|---:|---:|---:|---:|
| `short_history_v1` (heavyweight backtest) | 9 | 11.1 | 66.7 | −7.76 |
| `live_paper_db` (since 2026-04-22) | 4 | 75.0 | 0.0 | +1.69 |

The historical cohort lost during a **+24.28% SPY tape (2024-07-25 → 2026-03-23)**. Six of nine historical trades hit the −10% stop.

**Stop-hit cluster (6 of 9 historical trades):** VST (×2), MSTR, GS, HON, ZTS. Two trades opened on 2025-04-07 with VIX = 46.98 (post-flush) and were stopped at lows; SPY then rallied +12% and +16.5% over those windows. VST 2025-03-03 had an intraday range of 17.8% against a 10% stop — geometry alone made the trade unsurvivable.

**Winners vs losers:**

| Metric | Winners (n=4) | Losers (n=9) |
|---|---:|---:|
| Avg gap-risk % | 0.41 | **1.81 (4.4×)** |
| Avg intraday range % | 2.35 | **8.67 (3.7×)** |
| Avg SPY 20d ret at entry | +6.6% | −3.7% |
| Avg VIX at entry | 19.0 | 28.3 |
| SPY > 200dma at entry | 4 / 4 | 4 / 9 |

**Diagnosis (priority-ordered).** (1) Wrong-regime entries (VIX 47 post-flush). (2) Stop/target geometry incompatible with underlying volatility. (3) Shorting leadership names mid-rally. (4) Permissive score thresholds (historical cohort scores cluster at 61–68).

**Disposition.** Stays **paper-only**. **Historical setup needs redesign** — see hypothesis H1 in `docs/research/SLEEVE_FAILURE_AUTOPSY.md`. No threshold or scanner change in this phase.

<!-- AUTOPSY_END -->

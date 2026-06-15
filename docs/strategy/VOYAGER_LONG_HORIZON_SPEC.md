# VOYAGER Long-Horizon Strategy Specification

**Version:** 2.0 — doctrine-aligned rebuild  
**Date:** 2026-04-17  
**Status:** Production scanner rebuilt; paper validation active  
**Replaces:** voyager.py mean-reversion SHORT (archived to research/sleeves/voyager_mean_reversion_archive.py)

---

## Mandate

Capture long-duration accumulation before the major run is widely recognized.
Piggyback on real institutional accumulation using retail flexibility to enter
earlier and size more precisely.

**Direction: LONG only. Never SHORT.**  
**Target holding period: 6–18 months.**  
**Book: A (Long Trend / Leadership)**

This is the flagship long-duration sleeve of the system. It represents the
"align with institutions when they are creating durable opportunity" half of
the core doctrine.

---

## What VOYAGER is NOT

VOYAGER is not a tactical short strategy.  
VOYAGER is not a mean-reversion short.  
VOYAGER does not fade extensions or short overbought stocks.  
VOYAGER does not trade on the same day as earnings.  
SHORT-direction signals belong exclusively in `strategies/short_sleeve.py`.

The previous production implementation of VOYAGER (archived at
`research/sleeves/voyager_mean_reversion_archive.py`) incorrectly implemented
this as a SHORT mean-reversion scanner. That implementation contradicted the
doctrine and has been replaced.

---

## Doctrine Reference

From `docs/strategy/STRATEGY_DOCTRINE.md`:

> **VOYAGER Mandate:** capture long-duration accumulation before the major run
> is widely recognized.
>
> **Retail edge claim:** piggyback on real institutional accumulation while
> using retail flexibility to enter earlier and size more precisely.
>
> **Failure mode to avoid:** buying strong names after they already ran.
> Confusing "good company" with "good entry."
>
> **What success looks like:** lower-frequency, higher-conviction names.
> Strong thesis support. Asymmetric long-duration upside.

---

## Three Entry Archetypes

VOYAGER detects three valid long-horizon entry patterns. The scanner confirms
which archetype applies and includes it in the signal output. Archetypes are
ordered from earliest (least confirmed) to most confirmed entry timing.

### Archetype C: EARLY_ACCUMULATION (earliest entry)

**Pattern:** Pre-golden-cross convergence zone. MA50 is below MA200 but within
3% and actively rising toward it. Stock is already outperforming SPY, and
institutional dollar volume is building (dvol_ratio ≥ 1.15). The golden cross
has not yet formed, but the conditions that create it are underway.

**Technical signature:**
- MA50 ≤ MA200 but within 3% below it (converging, pre-golden-cross)
- MA50 today > MA50 20 days ago (MA50 actively rising toward MA200)
- Dollar volume trend ratio ≥ 1.15 (stronger accumulation required)
- RS vs SPY positive over 50 trading days (stock already outperforming)
- Fundamental score ≥ 55 (higher bar — less structural confirmation means
  fundamentals must carry more weight)

**Why this entry:** The widely-watched golden cross is a lagging indicator. By
the time MA50 crosses MA200, the smartest institutional money has typically been
building the position for weeks. EARLY_ACCUMULATION captures stocks where
the underlying accumulation dynamics are already clearly present — RS outperformance,
rising MA50, building dollar volume — before the "official" signal forms. This
is the highest-conviction expression of the VOYAGER doctrine: entering before
the move is recognizable.

**Scoring bonus:** +4 points (lowest of three archetypes — less structural
confirmation requires other scoring dimensions to carry more weight).

### Archetype A: BASE_ACCUMULATION

**Pattern:** Stock building a multi-week constructive base while institutional
dollar volume rises quietly. Price stays tight (standard deviation of 20-day
closes < 3% of mean). Entry before the breakout is visible to most participants.
Requires golden cross (MA50 > MA200).

**Technical signature:**
- Price within ±5% of MA50
- Price tightness < 3% (close-to-close std dev / mean)
- MA50 > MA200 (trend structure intact — golden cross confirmed)
- Dollar volume rising over 6-week window
- Up-day volume > down-day volume

**Why this entry:** Institutions cannot build large positions without leaving
a footprint. Quiet price + rising dollar volume is the classic pre-breakout
accumulation signature. Retail account can enter at this stage with precision
that larger accounts cannot.

**Scoring bonus:** +8 points (highest — tightest setup, best entry timing conviction).

### Archetype B: TREND_PULLBACK

**Pattern:** Established long-term uptrend (MA50 > MA200), stock pulling back
constructively 2–10% below MA50. MA50 itself is still rising. Better
multi-month entry than chasing the extension.

**Technical signature:**
- Price 2–10% below MA50
- MA50 > MA200 (long-term trend intact — golden cross confirmed)
- MA50 today > MA50 30 days ago (MA50 still ascending)
- Relative strength vs SPY positive over 10 weeks

**Why this entry:** Even in strong uptrends, stocks periodically pull back to
their moving averages. These pullbacks are the correct institutional-quality
entry — not chasing the extension. The 6–18 month thesis remains intact; only
the entry timing improves.

**Scoring bonus:** +5 points.

---

## Scanner Metrics

| Metric | Window | Source | Purpose |
|---|---|---|---|
| Daily OHLCV | 260 bars | Alpaca | All price/volume calculations |
| MA50 | 50-day simple avg | derived | Trend structure, entry zone |
| MA200 | 200-day simple avg | derived | Long-term trend confirmation |
| RS vs SPY | 50 and 130 trading days | derived (+ Alpaca SPY) | Relative leadership |
| Dollar volume (20-day avg) | 20 days | derived | Institutional liquidity |
| Dollar volume trend | 20d avg / 60d avg ratio | derived | Accumulation building |
| Up-day volume ratio | last 20 bars | derived | Accumulation direction |
| Price tightness | std dev / mean, last 20 | derived | BASE archetype |
| MA50 slope | MA50 now vs 30d ago | derived | PULLBACK archetype |
| Live bid-ask spread | live | Alpaca quote | Liquidity quality |
| Earnings calendar | 15 days ahead | FMP | Entry timing protection |
| Income statement | 4 quarters | FMP | Fundamental quality |
| Balance sheet | 4 quarters | FMP | Leverage / health |
| Cash flow statement | 4 quarters | FMP | Operating cash flow |

---

## Hard Entry Conditions

All must pass. Any single failure rejects the ticker.

| # | Condition | Rejection reason |
|---|---|---|
| 1 | Price ≥ $5 | `price_too_low` |
| 2 | 20-day avg dollar volume ≥ $5M | `low_dollar_vol` |
| 3 | Price ≥ MA200 × 0.92 (not broken) | `below_ma200_floor` |
| 4 | Price ≤ MA50 × 1.12 (not extended) | `too_extended` |
| 5 | RS vs SPY > 0 over 50 trading days | `weak_rs_50d` |
| 6 | 20d avg dollar volume ≥ 85% of 60d avg | `dvol_fading` |
| 7 | Up-day avg volume ≥ down-day avg volume | `selling_dominates` |
| 8 | Fundamental score ≥ 40 / 100 (≥ 55 for EARLY_ACCUMULATION) | `low_fundamental_quality` |
| 9 | No earnings within 15 days | `earnings_soon` |
| 10 | At least one archetype confirmed | `no_archetype` |
| 11 | Score ≥ 65 | `low_score` |

**Note on MA50 > MA200:** This is no longer a universal hard gate. BASE_ACCUMULATION
and TREND_PULLBACK require golden cross (MA50 > MA200). EARLY_ACCUMULATION allows
pre-golden-cross entries when MA50 is within 3% of MA200 and rising. The `no_archetype`
rejection handles any ticker that does not qualify for at least one archetype.

---

## Scoring (0–100)

| Component | Max pts | Notes |
|---|---|---|
| Base | 30 | Starting floor |
| RS 50-day vs SPY | +15 | >10% outperformance = +15, >5% = +10, >2% = +5 |
| RS 130-day vs SPY | +8 | Multi-month leadership confirmation |
| Dollar volume trend | +10 | 20d/60d ratio > 1.20 = +10, > 1.05 = +7 |
| Up-day volume dominance | +8 | Ratio > 1.5 = +8, > 1.2 = +5 |
| Fundamental score | +14 | fund_score / 100 × 14 |
| Archetype bonus | +5–8 | BASE_ACCUMULATION = +8, TREND_PULLBACK = +5 |
| Entry timing | +5 | Closer to MA50 = better |
| **Maximum** | **100** | |

Minimum score to pass: **65**

---

## Trade Geometry

```
Stop  = min(entry − 1.5 × ATR,  MA200 × 0.97)
      = the lower of the two (gives more room for a long-horizon hold)
      Note: MA200-anchored stop reflects structural thesis invalidation.
            If the stock breaks below MA200, the long-duration thesis is wrong.

Target = entry + (entry − stop) × 2.5
       = minimum tactical R:R target
       Note: The real 6–18 month price target is typically much higher.
             The 2.5× R:R is a floor for risk management, not a sell signal.

MIN_RRR = 2.5
```

---

## Fundamental Quality Score

FMP data used: income statement, balance sheet, cash flow statement (last 4 quarters).

| Dimension | Max pts | Signal |
|---|---|---|
| Revenue trend (YoY) | 30 | >10% growth = 30, >3% = 20, flat = 10, declining = 0 |
| Profitability | 25 | Net income positive = 25, operating positive = 15, neither = 0 |
| Balance sheet (D/E) | 25 + 5 cash | D/E < 0.5 = 25, < 1.5 = 18, < 3.0 = 10, ≥ 3.0 = 0. Cash positive = +5 |
| Gross margin | 15 | > 40% = 15, > 20% = 9, > 10% = 4 |
| Operating cash flow | 5 | Positive OCF = +5 |
| **Total** | **105 → capped at 100** | |

**Hard minimum: 40/100.** Signals with fundamental score below 40 are rejected.
If FMP returns no data, score defaults to 50 (neutral, not blocking).

Rejection triggers:
- Declining revenue without profitability: typically 0–15 pts → rejected
- Negative equity + unprofitable: typically < 10 pts → rejected
- D/E > 3.0 + unprofitable: typically 15–25 pts → rejected

---

## Veto Council Profile

```
momentum:  0.30  ← dominant (20-day trend quality proxy)
earnings:  0.25  ← high (entry timing protection — 15-day buffer guaranteed)
spread:    0.20  ← elevated (institutional-grade liquidity required)
sector:    0.10
flow:      0.10  ← low (intraday flow irrelevant for 6–18 month hold)
sentiment: 0.05  ← minimal (short-term news noise irrelevant)
Threshold: 52.0
```

**Why this profile fits VOYAGER LONG:**

- `momentum (0.30)`: The best available proxy for multi-week trend quality.
  For a stock in an established uptrend or constructive pullback, 20-day
  momentum is positive → council LONG score is high. This is directionally
  correct. The scanner's MA50 > MA200 gate already ensures the longer-term
  trend is intact.

- `earnings (0.25)`: The VOYAGER scanner requires earnings > 15 days away.
  The council's 5-day look-ahead always returns 80 for VOYAGER signals.
  Combined: these two gates together reliably contribute 20 pts toward
  the 52-pt threshold.

- `spread (0.20)`: Elevated because VOYAGER targets institutional-grade names
  with sufficient liquidity for 6–18 month holding. Tight spread = quality name.

- `flow (0.10) and sentiment (0.05)`: De-weighted because intraday 5-minute
  flow bursts and short-term news headlines are irrelevant to a holding period
  measured in months.

**Verified soft score under typical VOYAGER LONG conditions:**
- Strong uptrend (mom=+6%, safe earnings, tight spread): 72.2 → PASS
- Constructive pullback (mom=+2%): 67.0 → PASS
- Base consolidation (mom=+1%): 66.4 → PASS
- All comfortably above the 52.0 threshold with no structural tension.

---

## What This Scanner Cannot Measure (Honest Limitations)

| Ideal Metric | Why Unavailable | Proxy Used |
|---|---|---|
| Real-time institutional positioning | 13F filings are quarterly, 45-day lag | Dollar volume trend (20d/60d ratio) |
| Dark pool flow confirmation | No API access in current stack | Up/down day volume ratio |
| Analyst estimate revisions | Not in FMP stack | Revenue trend from quarterly income |
| Options flow (puts/calls) | Not in current stack | None |
| Short interest and short squeeze risk | Not in current stack | D/E ratio as leverage proxy |

These are acknowledged gaps. The proxies are honest substitutes, not equivalents.
The system should be extended with real options flow and short interest data when
those data sources are added (see ARCHITECTURE_PHASE1.md §5.3 Unusual Whales).

---

---

## 13F Institutional Confirmation Layer

**Added:** 2026-04-18  
**Source:** SEC EDGAR 13F-HR filings via `edgartools` — `core/whale_tracker.py`

### What it provides

For each VOYAGER candidate, the scanner fetches the most recent two 13F quarters
and computes Q-over-Q position changes across 16 tracked institutional investors:

| Field | Description |
|---|---|
| `thirteen_f_flow` | BUYING / SELLING / MIXED / NEUTRAL / UNKNOWN |
| `thirteen_f_confidence` | HIGH (≥5 tracked) / MODERATE (3–4) / LOW (1–2) / UNKNOWN |
| `thirteen_f_buying` | Count of institutions increasing positions |
| `thirteen_f_selling` | Count of institutions decreasing positions |
| `thirteen_f_quarter` | Most recent quarter in the data (e.g. 2025-12-31) |
| `thirteen_f_pts` | Score adjustment applied (-5 to +8) |

### Scoring overlay (soft layer only)

| Condition | Score adjustment |
|---|---|
| BUYING + HIGH confidence | +8 |
| BUYING + MODERATE | +5 |
| BUYING + LOW | +2 |
| MIXED (buy > sell) | +1 |
| MIXED (sell ≥ buy) or NEUTRAL | 0 |
| UNKNOWN or unavailable | 0 |
| SELLING + LOW | -2 |
| SELLING + MODERATE | -3 |
| SELLING + HIGH | -5 |

### Critical design constraints

- **NOT a hard gate.** A SELLING signal from 13F does not reject the ticker. It applies
  a modest score penalty. The thesis is: institutions may have sold 6 weeks ago while
  the stock was building a new base — the current setup is what matters most.
- **Does not replace live proxies.** Dollar volume trend, RS, and up/down volume ratio
  capture current-quarter activity that 13F cannot see yet (45-day lag).
- **Safe fallback.** If edgartools is not installed, SEC is unreachable, or any error
  occurs, the scanner returns `score_adj = 0` and continues normally.
- **Cached 24 hours** via `core/data_gatekeeper.py` (SQLite + JSON cache).

### Currently tracked institutions (16 active as of 2026-04-18)

| Institution | Status | Notes |
|---|---|---|
| Vanguard | Active | |
| State Street | Active | |
| Fidelity (FMR LLC) | Active | |
| Berkshire Hathaway | Active | |
| ARK Investment Management | Active | CIK fixed: 0001697748 |
| Renaissance Technologies | Active | |
| Citadel Advisors | Active | |
| DE Shaw | Active | |
| Bridgewater Associates | Active | |
| Tiger Global | Active | |
| Point72 Asset Management | Active | |
| Viking Global | Active | |
| Third Point | Active | |
| Lone Pine Capital | Active | CIK fixed: 0001061165 |
| Soros Fund Management | Active | |
| BlackRock | Excluded | CIK 0001086364 = BLACKROCK ADVISORS LLC (stale 2016); staleness guard excludes it |
| Two Sigma | Excluded | No usable 13F CIK found |
| Millennium Management | Excluded | No usable 13F CIK found |
| Coatue Management | Excluded | No usable 13F CIK found |
| D1 Capital | Excluded | No usable 13F CIK found |

---

## What Happened to the Old Voyager

The production file `strategies/voyager.py` previously implemented a SHORT
mean-reversion scanner (fade stocks 15%+ above MA200 with RSI ≥ 70). That
implementation:
- Was in **Book C (Short Tactical)** instead of Book A
- Used **SHORT direction** instead of LONG
- Required the stock to have **already extended** — the exact failure mode the
  doctrine warns against
- Had a target holding period of **5–20 days** instead of 6–18 months
- Had **severe scanner↔council mismatch** because the council's SHORT scoring
  penalizes exactly the conditions (positive momentum, positive sentiment) that
  made those setups attractive

That file has been archived to `research/sleeves/voyager_mean_reversion_archive.py`
with a clear header marking it as doctrine drift.

---

## Drift Prevention

To prevent future drift from this doctrine:

1. **The file docstring** opens with the mandate, direction, and holding period.
   Any future PR that adds SHORT logic to voyager.py violates the explicit docstring.

2. **`STRATEGY_DOCTRINE.md`** has been updated with the explicit drift history
   and a clear statement that VOYAGER is LONG only.

3. **The veto council comment** in `council/veto_council.py` explicitly notes
   that SHORT signals belong in SHORT, not VOYAGER.

4. **The voyager_scorecard.md** has been updated with the new mandate, direction,
   and validation requirements.

---

## Validation Requirements (Paper → Live)

| Gate | Description | Status |
|---|---|---|
| data-valid | FMP fundamentals + Alpaca bars wired and tested | pass |
| backtest-valid | Historical backtest on production LONG scanner with real fundamentals and 13F anti-lookahead | pass |
| doctrine-valid | Quant Research Doctrine audit completed | pass, with council-profile measurement note |
| paper-valid | 30 paper-trade signals with hold duration ≥ 30 days | in progress |

Minimum paper sample before promotion: **30 signals** with tracked outcomes.
Minimum required for backtest: 12 months of daily scans, out-of-sample last 3 months.

Given the 6–18 month holding period, outcome data will take time to accumulate.
The first paper cycle should focus on entry quality (was the setup constructive?
was the entry timing correct? did the stock hold above MA200?) rather than
exit-based P&L, which will not be available within the validation window.

Doctrine audit reference: see `docs/scorecards/voyager_scorecard.md`
`Quant Research Doctrine Audit — 2026-04-21`.

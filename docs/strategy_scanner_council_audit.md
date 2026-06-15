# ARCHIVED / HISTORICAL DOCUMENT

> OBSOLETE — DO NOT USE FOR CURRENT OPERATIONS.
>
> This pre-doctrine audit contains superseded strategy assumptions, including an
> obsolete VOYAGER interpretation that no longer matches the live platform.
>
> Current operating truth now lives in:
> - `docs/strategy/CURRENT_DOCTRINE_MAP.md`
> - `docs/strategy/CURRENT_READINESS.md`
> - `docs/strategy/STRATEGY_DOCTRINE.md`
> - current active sleeve specs / scorecards
>
> Keep this file only as historical context for how the platform was audited
> before the doctrine cleanup and sleeve rebuilds.

# Strategy Scanner + Veto Council Audit

**Generated:** 2026-04-17  
**Purpose:** Pre-adjustment review of scanner logic, council profiles, and scanner↔council fit  
**Scope:** SNIPER, VOYAGER, REMORA, CONTRARIAN, SHORT  
**Status of code:** Post-calendar-window fix and REMORA `import statistics` fix. No threshold changes.

---

## How to read this document

Each strategy section covers:
1. Scanner metrics — what data the scanner actually reads
2. Entry / pass conditions — hard requirements vs soft preferences
3. Rejection buckets — what gets logged and why
4. Veto Council profile — hard veto conditions, Tier 2 agent weights, minimum threshold
5. Fit / mismatch analysis — whether the scanner logic and council logic are aligned
6. Horizon / identity check — what the code actually behaves like

Summary tables are at the end.

---

## Soft Score Math Reference

For all strategies, the Tier 2 council computes:

```
soft_score = Σ ( weight[agent] × agent_score )
```

Agent scores run 0–100 with direction-aware formulas:
- `flow`:      LONG → `50 + (vol_accel − 1.0) × 50`; SHORT → `50 + (1.0 − vol_accel) × 50`
- `sentiment`: LONG → `sent × 100`; SHORT → `(1 − sent) × 100`   (sent is 0.0–1.0)
- `momentum`:  LONG → `50 + mom_20d × 300`; SHORT → `50 − mom_20d × 300`  (clamped 0–100)
- `earnings`:  80 if no event within 5 days; 20 if earnings imminent
- `spread`:    `max(30, 100 − spread_pct × 10000)`;  >0.5% spread → 20
- `sector`:    hardcoded 55 (neutral) — no ticker→sector mapping is active

One code note: `main.py` line 416 uses a hardcoded threshold of 50 to assign the `REJECTED`
vs `GATED` status label in the scan_results DB. The actual veto uses the strategy-specific
threshold. This creates a display labeling gap for SNIPER (threshold 52) and SHORT (threshold 55):
signals vetoed with soft_score in [50, threshold) are logged as `GATED` instead of `REJECTED`.
This does not affect execution — the veto already happened — but distorts dashboard reporting.

---

---

# STRATEGY 1: SNIPER

## 1. Scanner Metrics

| Metric | Window | Source |
|---|---|---|
| OHLCV bars | 55 trading days | Alpaca daily |
| 10-day consolidation range | max(high[−10:]) − min(low[−10:]) | derived |
| ATR | last 14 bars (calc_atr) | derived |
| 20-day high | max(high[−20:]) | derived |
| 20-day average volume | mean(vol[−20:]) | derived |
| MA50 | mean(close[−50:]) | derived |
| VIX | live | FMP |
| 10-day SPY return | (spy_close[−1] / spy_close[−11]) − 1 | Alpaca SPY bars |
| RS vs SPY | ticker_10d > spy_10d | derived |

## 2. Scanner Entry / Pass Conditions

**Hard requirements (all must pass):**
1. VIX < 28 — regime gate enforced at scan entry, entire scan aborted if failed
2. Today's close > 20-day high **and** prior close ≤ 20-day high — must be the first breakout bar
3. Volume ≥ 1.4× 20-day average — institutional participation
4. Consolidation range (10-day high minus 10-day low) ≤ ATR × 1.5 — price was coiled, not churning
5. Close ≥ MA50 — basic trend alignment
6. Score ≥ 70

**Soft scoring (affect score only, not pass/fail):**
- RS positive vs SPY over 10 days: +15 pts
- Consolidation tightness < 0.8× ATR: +10 pts
- VIX < 18: +5 pts; VIX > 22: −10 pts

**Trade geometry:**  
Stop = entry − 1.0× ATR. Target = entry + 2.5× ATR. MIN_RRR = 2.5.

**Lookback / timeframe:** Daily bars only. No intraday confirmation in scanner.

**Intended horizon match:** Swing breakout (typically 3–15 trading days). Implementation matches intent.

## 3. Main Rejection Reasons

| Reason | Plain English |
|---|---|
| `stale_bars` | Fewer than 55 bars available — data window issue or very new listing |
| `no_breakout` | Today didn't close above the 20-day high, OR already above it (not the first day) |
| `low_volume` | Volume ratio < 1.4 — move is not institutionally confirmed |
| `wide_consolidation` | 10-day range > 1.5× ATR — base is too loose, coil is not set |
| `below_ma50` | Price below 50 MA — no trend alignment |
| `low_score` | All structural checks passed but weighted score < 70 |
| `poor_geometry` | R:R < 2.5 (rare — formula-based target almost always satisfies this) |

## 4. Veto Council Profile

**Tier 1 (hard veto):** Regime (VIX > 40), Macro (high-impact event ±30 min), Portfolio (daily loss cap, position limits).

**Tier 2 soft score weights:**
```
flow:      0.28  ← dominant
earnings:  0.22
momentum:  0.18
sector:    0.12
spread:    0.12
sentiment: 0.08  ← lowest
Threshold: 52.0
```

## 5. Fit / Mismatch Analysis

**FIT: GOOD**

The council profile is well-aligned with the scanner's logic:

- **flow (0.28):** Council measures intraday 5-min vol acceleration. On a genuine breakout day with institutional participation, afternoon volume tends to sustain or build on the morning open → vol_accel ≥ 1.0 → high LONG score. This directly confirms the breakout participation thesis.

- **earnings (0.22):** A breakout that occurs immediately before an earnings announcement is genuinely high-risk — the base could unwind on a miss. High weight is appropriate.

- **momentum (0.18):** Breakouts happen from stocks already in uptrends. LONG scoring rewards positive 20-day momentum, which is typically present for SNIPER candidates.

- **sentiment (0.08):** SNIPER is a technical pattern strategy, not news-driven. Low weight is appropriate.

**Verified soft score under typical conditions:** ~75 vs threshold 52 — comfortable margin.

**Minor note:** SNIPER already gates on VIX < 28 in the scanner. The council's Tier 1 regime agent hard-blocks at VIX > 40. These are non-conflicting but redundant at different levels.

## 6. Horizon / Identity Check

**Current behavior:** Tactical swing breakout, LONG only, 3–15 day holding implied by ATR-based stop/target. **Matches intended doctrine.** This is the most straightforward strategy in the system.

---

---

# STRATEGY 2: VOYAGER

## 1. Scanner Metrics

| Metric | Window | Source |
|---|---|---|
| OHLCV bars | 220 trading days | Alpaca daily |
| MA200 | mean(close[−200:]) | derived |
| Extension | (close − MA200) / MA200 | derived |
| RSI(14) | closes[−30:] | derived |
| Volume declining flag | all(vol[i] ≥ vol[i+1] for i in 0..3) — last 5 sessions | derived |
| Cluster selling count | sum(close < open) in last 10 bars | derived |
| ATR | last 14 bars | derived |
| Earnings calendar | 10 days ahead | FMP |

## 2. Scanner Entry / Pass Conditions

**Hard requirements (all must pass):**
1. `ALLOW_SHORTS = True`
2. Extension ≥ 15% above MA200
3. RSI(14) ≥ 70
4. At least one distribution signal: volume strictly declining over last 5 sessions **OR** ≥ 3 down-closes in last 10 bars
5. Earnings > 10 days away
6. Score ≥ 60

**Soft scoring:**
- Extension above MA200 (up to +25 pts, e.g. 20% extension → +20)
- RSI overbought severity (up to +20 pts above 70)
- Volume declining: +10 pts
- Down-close count: +2 pts per bar, up to +10

**Trade geometry:**  
Stop = entry + 1.5× ATR (SHORT — stop is above entry). Target = entry − (stop − entry) × 2.0. MIN_RRR = 2.0.

**Lookback:** Requires 220 daily bars. Signal is TODAY's close. All structural context is daily.

**Intended horizon claim:** Docstring calls this a "long-horizon trading/investing strategy."

## 3. Main Rejection Reasons

| Reason | Plain English |
|---|---|
| `stale_bars` | Fewer than 220 bars (was the dominant rejection before calendar-window fix) |
| `not_extended` | Price < 15% above MA200 — stock isn't overextended |
| `rsi_not_overbought` | RSI < 70 — not technically overbought |
| `no_distribution` | Neither volume declining nor cluster selling — no distribution pattern |
| `poor_geometry` | R:R < 2.0 or target would be ≤ 0 |
| `low_score` | Structure passes but score < 60 |
| `earnings_soon` | Earnings within 10 days — event risk too high |

## 4. Veto Council Profile

**Tier 1 (hard veto):** Same as all strategies.

**Tier 2 soft score weights:**
```
sentiment: 0.22  ← highest (tied)
earnings:  0.22  ← highest (tied)
flow:      0.20
sector:    0.18
spread:    0.10
momentum:  0.08  ← lowest
Threshold: 50.0
```

## 5. Fit / Mismatch Analysis

**FIT: SEVERE MISMATCH**

This is the most important finding in this audit.

Voyager's scanner entry criteria are:
- Price ≥ 15% above MA200 → implies strong positive 20-day momentum
- RSI ≥ 70 → sustained buying pressure → stock has recently had positive news coverage
- Some distribution signal (volume or cluster), but the stock is still extended

**The council's SHORT scoring treats these conditions as disqualifying:**

| Agent | Voyager's Condition | Council SHORT Formula | Result |
|---|---|---|---|
| sentiment | Extended stock with RSI 70+ → positive news (sent ≈ 0.70–0.90) | `(1 − sent) × 100` → 10–30 | **LOW** |
| flow | Extended trending stock → intraday buying (vol_accel ≈ 1.2–2.0) | `50 + (1 − vol_accel) × 50` → 0–40 | **LOW** |
| momentum | 15%+ above MA200 → 20-day mom ≈ +8–15% | `50 − mom × 300` → 5–26 | **LOW** |
| earnings | 10-day buffer guaranteed by scanner | 80 (safe) | **HIGH — only reliable positive** |
| spread | Liquid extended stocks | ~75–90 | **HIGH** |
| sector | Hardcoded 55 | 55 | neutral |

The two reliably positive agents (earnings, spread) together contribute at most **17.6 + 8.0 = 25.6 pts** toward the 50.0 threshold. The remaining 24.4 pts must come from sentiment, flow, and momentum — which are structurally penalized by Voyager's own entry requirements.

**Verified soft score under typical Voyager conditions:**

| Scenario | Soft Score | Result |
|---|---|---|
| Moderate extension (sent=0.70, vol_accel=1.4, mom=+6%) | 50.7 | PASS (barely) |
| Strong extension (sent=0.80, vol_accel=1.8, mom=+10%) | 43.3 | **FAIL** |
| Very extended (sent=0.90, vol_accel=2.2, mom=+15%) | 37.4 | **FAIL** |
| Best case (mixed news, decelerating vol, modest mom) | 63.1 | PASS |

The practical conclusion: **the better a Voyager signal is (more extended, more overbought), the more likely it is to fail the council.** This is not a calibration issue — it is a category error. The council's SHORT scoring assumes trend-following (short because the trend is down, sentiment is negative, flow is bearish). Voyager is the opposite — it is a contrarian mean-reversion short (short because the trend is too far up).

## 6. Horizon / Identity Check

**Claimed doctrine:** "long-horizon trading/investing strategy"  
**Actual implementation:** Same-day entry on today's close. ATR-based stop and target. No fundamental or macro context. No position sizing difference. 2.0× R:R swing trade.

**This is a 5–20 day tactical mean-reversion swing short.** The MA200 extension and RSI≥70 criteria are classic swing mean-reversion triggers, not strategic positioning criteria.

The "long-horizon" label likely refers to the strategy's **regime independence** (it runs in all conditions, unlike Contrarian which requires VIX≥22), not the holding period of individual trades. The implementation and the naming create ambiguity that should be resolved.

**Honest assessment:** VOYAGER is the "fade the extension" strategy. It should be classified alongside SHORT (Book C) as tactical mean-reversion, not as a long-horizon investment sleeve.

---

---

# STRATEGY 3: REMORA

## 1. Scanner Metrics

| Metric | Window | Source |
|---|---|---|
| OHLCV bars | 252 trading days | Alpaca daily |
| Price change % | abs(close[−1] − close[−2]) / close[−2] | derived |
| 20-day average volume | mean(vol[−20:]) | derived |
| Volume ratio | vol[−1] / avg_vol | derived |
| Dollar volume | close[−1] × vol[−1] | derived |
| 52-week high | max(close[−min(252,n):]) | derived |
| % from 52-week high | (high_52w − close) / high_52w | derived |
| Live bid-ask spread | (ask − bid) / mid | Alpaca quote API |
| ATR | last 14 bars | derived |
| Earnings calendar | 5 days ahead | FMP |

**Updated 2026-04-21:** Stale bars now require the full 252 bars before evaluation.
This keeps the 52-week-high gate honest and avoids false positives in newer listings.

## 2. Scanner Entry / Pass Conditions

**Hard requirements (all must pass):**
1. Price change < 0.5% — no visible headline move; stealth signal only
2. Volume ratio in [1.20, 1.60] — above average but not explosive (breakout would be > 1.60)
3. Dollar volume ≥ $25M — institutional-grade liquidity
4. Within 2% of 52-week high — strength, not distress buying
5. Bid-ask spread < 0.15% — dark pool eligible
6. No earnings within 5 days
7. Score ≥ 55

**Soft scoring:**
- Vol ratio proximity to 1.4 (the stealth sweet spot): up to +15 pts
- Near 52-week high: up to +15 pts
- Tight spread: +15 pts (< 0.05%) or +8 pts (0.05–0.1%)
- Dollar volume ≥ $50M: +10 pts; ≥ $25M: +5 pts

**Trade geometry:**  
Stop = 1.2× ATR below entry. Target = 3.0× ATR above entry. 1.5× normal position size.

**Intended horizon:** Short-horizon LONG, intraday to ~5 trading days, following
quiet institutional accumulation to near-term continuation.

## 3. Main Rejection Reasons

| Reason | Plain English |
|---|---|
| `stale_bars` | Fewer than 252 bars — full lookback required for honest 52-week-high gate |
| `price_moved` | Price moved ≥ 0.5% — either news-driven or a real price move, not stealth |
| `vol_too_low` | Volume ratio < 1.20 — no unusual activity |
| `vol_too_high` | Volume ratio > 1.60 — this is an outright breakout/news event, not stealth accumulation |
| `low_dollar_vol` | Dollar volume < $25M — too small for institutional participation |
| `not_near_52w_high` | Price > 2% below 52-week high — accumulation near lows, not near highs |
| `no_quote` | Live quote unavailable — scanner fails closed instead of assuming acceptable spread |
| `wide_spread` | Spread ≥ 0.15% — not dark-pool eligible |
| `poor_geometry` | R:R < 2.0 |
| `low_score` | Structural filters pass but score < 55 |
| `earnings_soon` | Earnings within 5 days |

## 4. Veto Council Profile

**Tier 1 (hard veto):** Same as all strategies.

**Tier 2 soft score weights:**
```
flow:      0.35  ← dominant
spread:    0.18
earnings:  0.15
sector:    0.12
momentum:  0.10
sentiment: 0.10  ← lowest
Threshold: 48.0
```

## 5. Fit / Mismatch Analysis

**FIT: MOSTLY GOOD — with one structural timing gap**

The council profile is thoughtfully constructed for this strategy:

- **spread (0.18):** REMORA already filters live spread < 0.15%. The council's spread agent will consistently score 80–95 for these signals, contributing 14–17 pts. Appropriate.

- **earnings (0.15):** REMORA uses a 5-day filter; council also checks 5 days forward. These are aligned — signals consistently get the 80-point safe score.

- **momentum (0.10):** Stocks within 2% of their 52-week high have positive 20-day momentum by definition. LONG scoring rewards this. Consistent positive contributor (~6–8 pts).

- **sentiment (0.10):** Low weight is correct. REMORA is explicitly not news-driven (price change < 0.5% is the signal). Sentiment is near-neutral for stealth setups.

**The timing gap — flow (0.35):**

The council's flow agent measures **intraday** 5-min volume acceleration (last 5 bars vs first 10 bars). REMORA's signal is a **daily observation** (daily volume 20–60% above average). These are different time windows.

If institutional accumulation happens steadily throughout the day (typical dark pool behavior), vol_accel is likely ≈ 1.0 → flow score = 50 → 17.5 pts. Adequate.

If accumulation is concentrated in the afternoon (common — institutions use the afternoon to build size without moving the price), vol_accel > 1.0 → score > 50 → good.

But if the 20-60% daily volume anomaly is entirely morning activity that has since dissipated by the time the council evaluates (market close), vol_accel might be < 1.0 → flow score < 50. The council's dominant agent would then drag the soft score down for a valid REMORA signal.

**This timing gap affects real-time evaluation but not end-of-day scans run after market close.** If the daemon scans near or after 16:00 ET, the full day's intraday bars are present and the accumulation pattern is visible. If the scanner runs mid-day, the flow signal is partial.

**Verified soft score under typical conditions:** ~65.5 vs threshold 48 — comfortable margin. REMORA has the largest safety margin of all strategies, which is appropriate given its strict stealth criteria.

## 6. Horizon / Identity Check

**Actual behavior:** Short-horizon LONG (intraday to ~5 trading days), accumulation-following momentum. Near-52-week-high requirement means the scanner never buys distressed stocks — only those being quietly accumulated at the top of their range.

**Matches intended doctrine:** Yes. Stealth accumulation detection is correctly implemented.

**Resolved code note:** The old stale-bars check (`< 60`) was too lenient for a scanner
whose identity depends on the 52-week-high gate. It now requires 252 bars.

**Resolved quote note:** The scanner now fails closed on missing quote data with
`no_quote` instead of assuming a usable spread.

---

---

# STRATEGY 4: CONTRARIAN

## 1. Scanner Metrics

| Metric | Window | Source |
|---|---|---|
| VIX | live | FMP |
| SPY daily bars | 30 bars | Alpaca |
| SPY 10-day high | max(close[−10:]) | derived |
| SPY RSI(14) | closes[−20:] | derived |
| Stock OHLCV | 55 bars | Alpaca daily |
| Stock RSI(14) | closes[−20:] | derived |
| MA50 | mean(close[−50:]) | derived |
| Strong close flag | (close − low) / (high − low) ≥ 0.70 on today's bar | derived |
| Reversal candle flag | hammer OR bullish engulf vs prior bar | derived |
| Higher low flag | today.low > yesterday.low | derived |
| ATR | last 14 bars | derived |

**Gate sequence at scan entry:**
1. VIX mode computed first (active/watch/None); entire scan skipped if VIX < 22
2. SPY washout context computed; entire scan skipped if both gates fail

## 2. Scanner Entry / Pass Conditions

**Hard requirements:**
1. VIX ≥ 28 (active mode) OR VIX ≥ 22 (watch mode)
2. SPY washout: SPY ≥ 3% below 10-day high **OR** SPY RSI < 38 (prevents Day-1 entry before the real flush)
3. Stock RSI ≤ dynamic gate: 42 (VIX < 30), 38 (VIX 30–35), 35 (VIX ≥ 35)
4. Price ≥ MA50 × 0.80 (not in total freefall — max 20% below 50 MA)
5. At least 1 of 3 reversal signals: strong close, reversal candle, higher low
6. Score ≥ 60

**Soft scoring:**
- Deeper oversold (RSI below gate): up to +20 pts
- Reversal signal count × 10 pts (each of the 3 signals)
- Within 10% of MA50: +10 pts
- VIX ≥ 28 (active panic regime): +10 pts

**Trade geometry:**  
Stop = entry − 1.5× ATR. Target = entry + ATR × 1.5 × 1.5 = +2.25× ATR. MIN_RRR = 1.5. Position: 50% of normal size.

**Horizon:** Short-term panic reversal trade, 3–10 days.

## 3. Main Rejection Reasons

| Reason | Plain English |
|---|---|
| `stale_bars` | Fewer than 55 bars |
| `rsi_not_oversold` | Stock RSI above the VIX-adjusted gate (42/38/35) |
| `in_freefall` | Price < MA50 × 0.80 — the stock is in structural breakdown, not a reversal candidate |
| `no_reversal` | None of the 3 reversal signals are present — no sign of bottoming |
| `low_score` | Structural checks passed but score < 60 |
| `poor_geometry` | R:R < 1.5 |

Note: VIX and SPY washout failures cause the entire scan to return `[]` without per-ticker rejection logging. These are scan-level gates, not per-ticker rejection reasons.

## 4. Veto Council Profile

**Tier 1 (hard veto):** Same as all strategies.

**Tier 2 soft score weights:**
```
sentiment: 0.32  ← dominant (highest across all strategies)
momentum:  0.22  ← high
flow:      0.16
spread:    0.12
earnings:  0.10
sector:    0.08  ← lowest
Threshold: 45.0  ← lowest across all strategies
```

## 5. Fit / Mismatch Analysis

**FIT: MODERATE MISMATCH — stable in pure market panic; fragile in mixed scenarios**

CONTRARIAN is explicitly designed to trade against the current market direction. But the council's LONG scoring, driven primarily by sentiment (0.32) and momentum (0.22), rewards trend continuation.

**For stocks in a pure market-driven panic (no company-specific negative news):**
- Sentiment may be ~0.35–0.45 (somewhat negative but not extreme) → LONG score ~35–45 → contribution ~11–14 pts
- Momentum is negative but moderate → score ~20–35 → contribution ~4–8 pts
- Flow: panic selling creates vol_accel > 1.0; the LONG formula rewards this (accidentally helpful)
- Result: soft score ~45–52. **Passes threshold of 45.0, but only barely.**

**For stocks with company-specific negative news in a panic:**
- Sentiment drops to 0.10–0.25 → score ~10–25 → contribution ~3–8 pts
- Momentum is strongly negative → score ~5–20 → contribution ~1–4 pts
- Result: soft score ~30–40. **Fails threshold of 45.0.**

**Stress test results (verified):**

| Scenario | Soft Score | Result |
|---|---|---|
| Market panic, neutral news, vol_accel=1.4, mom=−8%, sent=0.40 | 49.6 | PASS |
| Market panic, bad news, vol_accel=1.2, mom=−12%, sent=0.20 | 38.7 | **FAIL** |
| Deep panic VIX=38, capitulation, vol_accel=2.0, mom=−15%, sent=0.25 | 44.1 | **FAIL (1 pt short)** |
| Mild fear VIX=23, mild dip, vol_accel=1.0, mom=−3%, sent=0.45 | 52.8 | PASS |

**The deep panic scenario (VIX=38, the strongest Contrarian regime) is the one most likely to fail.** This is the same structural tension as Voyager: the conditions that make a signal MOST interesting to the strategy are precisely the conditions that score worst in the council.

**The low threshold (45.0) partially compensates** — it allows marginal passes in market-wide panic that would otherwise be blocked. But company-specific bad news during a panic is blocked, which may be appropriate (avoid catching falling knives with specific bad news).

**Flow agent behavior:** The flow agent accidentally helps CONTRARIAN. In heavy panic selling, vol_accel > 1.0 (lots of late-day selling), and the LONG formula `50 + (vol_accel − 1.0) × 50` rewards this. This is logically backward (selling volume is not bullish confirmation) but incidentally produces a positive score contribution. It is the main reason the deep panic scenario at 44.1 stays close to passing.

## 6. Horizon / Identity Check

**Actual behavior:** Tactical panic reversal swing (3–10 days). Only active when VIX ≥ 22 with SPY in washout. Half-size position. Lower R:R target (1.5 vs 2.5 for SNIPER).

**Matches intended doctrine:** Largely yes. The regime gating, reduced sizing, and relaxed R:R requirement all reflect a genuine understanding of contrarian mean-reversion risk.

The council mismatch doesn't reflect an implementation bug in the scanner — it reflects the council not being designed to evaluate contrarian strategies.

---

---

# STRATEGY 5: SHORT (ShortSleeve)

## 1. Scanner Metrics

| Metric | Window | Source |
|---|---|---|
| OHLCV bars | 30 trading days | Alpaca daily |
| FMP earnings calendar | 14-day lookback | FMP historical earnings |
| Event bar identification | first bar where date ≥ earnings_date | derived |
| Earnings gap % | (event_bar.open − prev_bar.close) / prev_bar.close × 100 | derived |
| Volume on event bar | event_bar.volume / mean(vol[prior 20 bars]) | derived |
| Continuation flag | today.close < event_bar.low | derived |
| Lag (sessions since event) | len(bars) − 1 − event_bar_idx | derived |
| ATR | last 14 bars | derived |
| EPS estimate / actual | from FMP earnings record | FMP |

## 2. Scanner Entry / Pass Conditions

**Hard requirements (all must pass):**
1. `ALLOW_SHORTS = True`
2. Ticker has a recent earnings event in FMP calendar (within 14 days lookback)
3. Earnings gap ≤ −3.0% on the event session open
4. Volume on event bar ≥ 1.5× 20-day average volume
5. Continuation: today's close < event bar's low (still pressing lower, not bouncing)
6. Lag ≤ 3 sessions (entry must be within 3 trading days of the event)
7. Score ≥ 55

**Soft scoring:**
- Larger gap: up to +30 pts (e.g. −10% gap → +30)
- Higher volume ratio: up to +20 pts
- Freshness (lag 0 vs lag 3): +5–15 pts
- Continuation present: +15 pts (always true if the check passed)

**Trade geometry:**  
Stop = entry + 1.5× ATR (SHORT stop is above entry). Target = entry − (stop − entry) × 2.0. MIN_RRR = 2.0.

**Horizon:** Very tactical, 0–3 session holding implied by the lag constraint and post-event continuation thesis.

## 3. Main Rejection Reasons

| Reason | Plain English |
|---|---|
| `no_recent_event` | Ticker not in FMP earnings calendar for the last 14 days (expected for most tickers) |
| `stale_bars` | Fewer than 5 bars available |
| `no_event_date` | FMP earnings record present but date field is missing |
| `event_bar_not_found` | Can't locate the reaction bar in the fetched daily bars |
| `lag_too_large` | Event was > 3 sessions ago — too late for continuation entry |
| `gap_not_large_enough` | Gap > −3.0% on earnings day — not enough initial institutional damage |
| `low_volume` | Event-bar volume < 1.5× average — no institutional selling confirmation |
| `no_continuation` | Today's close ≥ event bar low — the stock is recovering, not continuing lower |
| `poor_geometry` | R:R < 2.0 |
| `low_score` | Structure passes but score < 55 |

The `no_recent_event` bucket is the dominant rejection for most tickers — most stocks don't have recent earnings. This is expected and correct. The scanner routes only ~10–15% of the universe (tickers with recent earnings) through meaningful evaluation.

## 4. Veto Council Profile

**Tier 1 (hard veto):** Same as all strategies.

**Tier 2 soft score weights:**
```
earnings:  0.30  ← dominant
sentiment: 0.22  ← high
flow:      0.18
spread:    0.14
momentum:  0.08
sector:    0.08
Threshold: 55.0  ← highest (SHORT is held to the highest bar)
```

## 5. Fit / Mismatch Analysis

**FIT: GOOD — strongest alignment in the system for SHORT direction**

This is why: the scanner's entry conditions create a specific market context that the council's agents correctly identify as favorable for a SHORT entry.

| Agent | SHORT Scanner's Condition | Council SHORT Score | Result |
|---|---|---|---|
| earnings (0.30) | Event ALREADY happened (0–3 days ago) → forward calendar clear | Score 80 (no event in 5 days ahead) → 24 pts | **STRONG** |
| sentiment (0.22) | Post-earnings miss → "miss", "drop", "loss" headlines → sent ≈ 0.15–0.35 | `(1−0.25) × 100 = 75` → 16.5 pts | **STRONG** |
| flow (0.18) | Stock still selling 1–3 days post-event; intraday direction ambiguous | ~50 (neutral to moderate) → 9 pts | **NEUTRAL** |
| momentum (0.08) | Post-earnings gap down → 20-day mom slightly negative | SHORT direction rewards this → ~65 → 5 pts | **POSITIVE** |
| spread (0.14) | Liquid post-earnings stock | ~75 → 10.5 pts | **POSITIVE** |

**Verified soft score under typical conditions:** ~71.5 vs threshold 55 — generous margin.

The key insight: the SHORT scanner's events **already happened**, so the council's forward-looking earnings agent consistently scores 80. The post-earnings negative news creates genuine negative sentiment that the sentiment agent correctly identifies as favorable for a SHORT. These two agents together (0.52 combined weight) provide a reliable 40+ point floor.

**The higher threshold (55.0) is justified** — the system correctly recognizes that directional shorts carry higher risk and demands more council conviction.

## 6. Horizon / Identity Check

**Actual behavior:** Event-driven post-earnings continuation short. Very tactical: 0–3 session entry window, continuation required. This is the tightest time-constrained strategy in the system.

**Matches intended doctrine:** Yes. SHORT is correctly identified as an event-driven tactical short, distinct from Voyager's structural/technical mean-reversion short.

---

---

# SUMMARY

## A. Scanner ↔ Council Alignment

| Strategy | Scanner Type | Direction | Alignment | Typical Soft Score | Threshold | Margin |
|---|---|---|---|---|---|---|
| SNIPER | Breakout momentum | LONG | **GOOD** | ~75 | 52 | +23 |
| SHORT | Event-driven continuation | SHORT | **GOOD** | ~72 | 55 | +17 |
| REMORA | Stealth accumulation | LONG | **MOSTLY GOOD** | ~66 | 48 | +18 |
| CONTRARIAN | Panic reversal | LONG | **MODERATE MISMATCH** | ~45–52 | 45 | +0–7 |
| VOYAGER | Mean-reversion SHORT | SHORT | **SEVERE MISMATCH** | ~37–51 | 50 | −13 to +1 |

## B. Strategies with Obvious Mismatches

**VOYAGER — Severe:**
The council's SHORT scoring treats positive sentiment, positive momentum, and intraday buying as negative signals. Voyager requires all three as entry preconditions. The strategies fight each other by design. A Voyager signal is most actionable when the stock is most extended — and most extended stocks score worst in the council. The 20 signals in the live log failing on soft_score is a structural, predictable outcome of this mismatch.

**CONTRARIAN — Moderate:**
The council's LONG scoring rewards positive sentiment and momentum, but Contrarian enters during fear regimes with negative sentiment and negative momentum. The low threshold (45.0) and the flow agent's accidental helpfulness (panic selling creates vol_accel > 1.0 which the LONG formula rewards) provide a thin buffer. The strategy works in pure market-driven panics but fails in mixed scenarios (company-specific bad news + market selloff).

## C. Strategies Ready for Validation

**Ready now — scanner and council aligned:**
1. **SNIPER** — clean breakout logic, well-profiled council, large soft score margin. Primary risk is the 70-point scanner score threshold (high bar). Ready for live cycle validation.
2. **SHORT** — event-driven with excellent council alignment. Primary constraint is event coverage (most tickers have no recent earnings). Ready for validation when events occur.

**Ready with monitoring:**
3. **REMORA** — good alignment with minor flow-timing gap. If end-of-day scans are used consistently, timing gap is minimal. Validate with close attention to the flow agent's contribution in scan_results.

## D. Strategies Needing Redesign or Recalibration

**VOYAGER — Council profile needs redesign:**
The council profile must be rebuilt for a contrarian SHORT. The fix is not threshold adjustment — it is agent reweighting. The `sentiment`, `flow`, and `momentum` agents are structurally hostile to contrarian mean-reversion signals. The redesigned VOYAGER profile should:
- Heavily upweight `earnings` (event risk is the real gate for an extended stock) and `spread` (liquidity is paramount for a short)
- Downweight or remove `sentiment`, `flow`, and `momentum` from the VOYAGER profile (they measure trend confirmation, not reversal quality)
- Add or proxy an "extension" or "deviation from mean" signal that actually confirms the reversal setup

**CONTRARIAN — Council profile improvement needed:**
The council profile is better than Voyager's but still imprecise. The `sentiment` and `momentum` agents work against contrarian entries in deep panic. Consider:
- Routing CONTRARIAN to an alternative scoring path that rewards negative sentiment (same as SHORT does) — because CONTRARIAN is also a contrarian signal
- Or redesigning a `contrarian_flow` metric that rewards volume deceleration (selling exhaustion) rather than volume acceleration

## E. Code Bug Found

**`main.py` line 416 — status label uses hardcoded threshold of 50:**
```python
# current (wrong for SNIPER and SHORT):
scan_status = "REJECTED" if (soft is not None and soft < 50) else "GATED"
```
For SNIPER (threshold=52) and SHORT (threshold=55), signals vetoed with soft_score in [50, threshold) are labeled `GATED` in the DB instead of `REJECTED`. The veto is correct — only the label is wrong. Fix: use the strategy-specific threshold from `MIN_SOFT_SCORE_BY_STRATEGY`.

---

## Honest Recommendation

| Strategy | Disposition |
|---|---|
| SNIPER | **Keep as-is** — scanner and council aligned, ready for live validation |
| SHORT | **Keep scanner as-is** — council aligned; fix status label bug; validate when events appear |
| REMORA | **Keep as-is** — small flow-timing gap doesn't warrant changes yet; validate first |
| CONTRARIAN | **Monitor** — thin margin in deep panic (the exact regime it targets); redesign council profile if signal quality validates |
| VOYAGER | **Redesign council profile only** — the scanner logic is sound; the council profile must be rebuilt for contrarian SHORT; the status label bug affects SHORT range for this strategy too |

The scanner logic for all five strategies is conceptually sound and correctly implemented. The mismatch is entirely in the council's Tier 2 agent weighting, which was built with a trend-confirmation bias that doesn't translate to the two contrarian strategies (VOYAGER and CONTRARIAN).

---

*End of audit. Generated from code review of strategies/sniper.py, strategies/voyager.py,
strategies/remora.py, strategies/contrarian.py, strategies/short_sleeve.py,
council/veto_council.py, execution/portfolio_allocator.py, execution/portfolio_risk.py,
core/decision_logger.py, main.py.*

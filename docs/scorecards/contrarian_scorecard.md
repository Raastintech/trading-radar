---
strategy: CON — Contrarian
book: Long Mean-Reversion / Panic-Rebound (Book B)
code_path: strategies/contrarian.py
owner: TBD
last_updated: 2026-04-22
---

# Contrarian Scorecard

## Mandate

Panic / dislocation rebound, LONG only. Enter quality names that have been indiscriminately force-sold during broad panic events, not ordinary dips.

**Holding horizon: days to weeks (typically 2–10 sessions for the snap-back).**

## Thesis

During elevated VIX regimes, large institutional holders are forced to sell regardless of individual stock quality — redemptions, risk limits, margin calls. Quality stocks are sold alongside weak names with no differentiation. When broad washout is confirmed (SPY context), a stock-level oversold signal plus early reversal signature indicates the forced-selling pressure is exhausting. Retail accounts can enter where institutional forced-sellers are exiting.

## Identity and Anti-Drift Notes

- **CONTRARIAN exploits institutional constraints** — forced selling creates asymmetric entries that don't exist in normal regimes.
- **PANIC/DISLOCATION ONLY — NOT generic dip-buying.** The VIX gate and SPY washout gate are load-bearing. A stock down 3% in a calm market is NOT a Contrarian setup. Weakening these gates destroys the edge.
- **Distinct from VOYAGER:** VOYAGER may find high-quality names being accumulated during fear for a 6–18 month hold. CONTRARIAN targets the 2–5 session snap-back from the panic low. Both can fire on the same ticker simultaneously with different theses and different hold horizons.
- **Distinct from SNIPER:** SNIPER confirms breakouts in constructive regimes. CONTRARIAN buys oversold reversal attempts during fear/washout regimes.
- **Distinct from REMORA:** REMORA looks for quiet-flow near highs. CONTRARIAN looks for panic/dislocation near washed-out lows.
- **Distinct from SHORT:** SHORT profits from continued downside after a negative event. CONTRARIAN is LONG only and requires evidence that forced selling is exhausting.
- **No short logic belongs here.** SHORT is the sole short-direction strategy.

## Known Gap — Company-Specific vs Macro Selling

The scanner cannot distinguish broad macro/indiscriminate selling from company-specific bad news (guidance cut, structural deterioration). The MA50 × 0.80 freefall gate is a partial structural proxy, but a stock down on company-specific news can pass all gates. This is a validation blocker that requires a news-freshness or catalyst-type filter before live promotion.

## Signal Conditions

- VIX ≥ 28.0 (panic) or ≥ 22.0 (watch mode after recent peak)
- SPY ≥ 3% below 10-day high, OR SPY RSI < 38
- Ticker RSI(14) ≤ dynamic gate: 42 below VIX 30, 38 at VIX 30-35, 35 at VIX ≥ 35
- Price ≥ 80% of MA50, avoiding total freefall / structural breakdown
- At least one reversal signal: strong close, reversal candle, or higher low
- Score ≥ 60, R:R ≥ 1.5
- Position sizing: 50% of normal (scale-in under uncertainty)

## Required Data

- Alpaca: daily OHLCV bars
- FMP: VIX level, SPY bars

## Doctrine Audit — 2026-04-22

**Identity decision:** locked as panic / dislocation / forced-selling rebound,
LONG only. No generic dip-buying, no breakout confirmation, no stealth-flow, no
long-horizon accumulation, and no short continuation logic.

**Edge source:** broad fear regimes create indiscriminate forced selling. Quality
or liquid names can become temporarily oversold when institutions de-risk,
redeem, margin-reduce, or volatility-target down. CONTRARIAN tries to enter only
after broad washout plus stock-level oversold/reversal evidence suggests selling
pressure is exhausting.

**Trigger:** scan-level VIX mode plus SPY washout must pass, then ticker-level
RSI gate, non-freefall MA50 guard, at least one reversal-quality signal, score
≥ 60, and R:R ≥ 1.5.

**Invalidation:** the trade is wrong if the panic low/reversal attempt fails.
Production scanner defines baseline invalidation through `stop_loss = entry -
1.5x ATR`. Backtest design should also track time failure if no rebound occurs
within the selected tactical horizon.

**Regime fit:**
- Active: VIX ≥ 28 with SPY washout, or VIX ≥ 22 watch mode after a recent panic
  peak and SPY washout still present.
- Selective: VIX 22-28 watch mode, mixed market stress, or shallow washout.
- Silent: VIX < 22, no SPY washout, calm market dips, or company-specific
  breakdown without broad panic context.

**Scanner audit from code:**
- Metrics/features: live VIX, 30 SPY daily bars, SPY 10-day high extension,
  SPY RSI, 55 ticker daily bars, ticker RSI, MA50, strong close, hammer/bullish
  engulf, higher low, ATR, score, R:R, sector-diversity cap.
- Hard entry conditions: VIX mode not `None`; SPY washout passed; at least 55
  ticker bars; ticker RSI below the VIX-adjusted threshold; price not below
  80% of MA50; at least one reversal signal; score ≥ 60; R:R ≥ 1.5.
- Rejection visibility: current implementation returns `None` silently inside
  `_evaluate`; scan-level VIX and SPY washout are logged, but per-ticker
  rejection buckets are not persisted. This is a validation gap, not a doctrine
  identity failure.
- Stop/target: stop = entry - 1.5x ATR; target = entry + 2.25x ATR; baseline
  R:R = 1.5; position size is half normal.
- Implementation match: PASS for panic/dislocation identity. The load-bearing
  VIX, SPY washout, oversold, and reversal-quality gates are all present.

**Scanner / council fit:** PARTIAL. Production `main.py` routes CONTRARIAN
through the same generic `VetoCouncil` as other strategies. Tier 1 regime logic
does not block CONTRARIAN, but Tier 2 LONG scoring can penalize the exact
negative sentiment and negative momentum that make a panic-rebound setup
interesting. This is tolerable for scanner-only baseline validation, but
scanner+council results must be reported separately. Do not redesign the council
before the first scanner-only baseline; a minimal future clarification would be
a CONTRARIAN council profile that treats panic evidence as context rather than
ordinary LONG trend confirmation.

## Pre-Backtest Checklist — 2026-04-22

| Section | Status | Notes | Blocks deep backtest? |
|---|---|---|---|
| A. Doctrine / mandate clarity | PASS | Panic/dislocation forced-selling rebound, LONG only, tactical horizon | No |
| B. Scanner / council fit | PARTIAL | Scanner identity is clean; generic council can under-score fear setups | No for scanner-only baseline; yes for treating council results as decisive |
| C. Data integrity | PARTIAL | Daily OHLCV/VIX/SPY path is defined; event timing must avoid lookahead; historical live council inputs are not guaranteed | No for scanner-only baseline |
| D. Execution realism | PASS | ATR stop/target, half-size risk, and tactical horizon are explicit | No |
| E. Output expectations | PASS | Scanner-only baseline first; scanner+council only if practical without fabricating inputs | No |

## Baseline Validation Design

- Universe: routed CONTRARIAN universe plus broad liquid single-stock controls;
  exclude ETFs from tradable signals unless explicitly used as controls.
- Window: include stress regimes across 2020-2024 minimum, with specific
  reporting for high-VIX windows.
- Entry: signal-date close.
- Horizons: 1d, 3d, 5d, 10d, with 3-5d as the primary snap-back window and 10d
  as the outer tactical hold.
- Friction: at least 0.05% commission each way plus at least 0.10% slippage each
  way.
- Stop/target: current baseline stop = 1.5x ATR, target = 2.25x ATR; also
  record time exits by horizon.
- Reporting: signal count, win rate, raw/adjusted return, stop-hit rate,
  target-hit rate, year/regime breakdown, VIX bucket breakdown, ticker/sector
  concentration, and examples that distinguish market panic from idiosyncratic
  bad-news breakdowns.
- Paper-readiness threshold: positive adjusted expectancy at the primary 3-5d
  horizons, acceptable stop-hit rate, enough signals in true fear regimes,
  stability across stress windows, and no evidence that results depend on one
  ticker, sector, or one year.

## Validation Status

| Gate | Status | Notes |
|------|--------|-------|
| data-valid | partial | Daily OHLCV path is clean; historical VIX is proxied because no local FMP VIX history is cached |
| backtest-valid | partial | scanner-only baseline run; result is not paper-ready |
| paper-valid | pending | not started |

## Validation Blockers

- [x] Identity locked as panic/dislocation forced-selling rebound
- [x] Scanner implementation matches core identity
- [x] Scanner/council fit reviewed; partial but tolerable for scanner-only baseline
- [x] Per-ticker rejection attribution for baseline diagnostics
- [ ] VIX gating confirmed to fire at correct threshold in historical/live data
- [ ] "No company-specific bad news" verification layer (must distinguish macro vs idiosyncratic selling)
- [ ] Rebound timing study (confirm 2–5 session window is realistic)
- [ ] Crisis-regime veto hardening

## Last Backtest Result

**Run: 2026-04-22 — scanner-only baseline**

Script: `research/backtests/contrarian_backtest.py`

Configuration:
- Universe: 82 liquid single stocks; ETF instruments retained as controls only
- Window: 2020-01-01 to 2024-12-31
- Entry: signal-date close
- Horizons: 1d / 3d / 5d / 10d
- Primary horizons: 3d and 5d
- Friction: 0.30% round trip
- Stop: entry - 1.5x ATR
- Target: entry + 2.25x ATR
- Position model: half-size documented; returns shown unlevered per signal
- Scanner-only baseline; no council replay, no threshold tuning, no news filter
- Historical VIX limitation: no local FMP historical VIX cache exists, so the
  backtest uses the same local 20-day annualized SPY realized-volatility proxy
  used elsewhere in backtests. Treat VIX buckets as proxy-regime diagnostics, not
  exact historical VIX replay.

Checklist confirmation:
- Doctrine / mandate clarity: PASS
- Scanner / council fit: PARTIAL
- Data integrity: PARTIAL because VIX is proxied; daily OHLCV path is clean
- Execution realism: PASS for baseline ATR stop/target and friction
- Output expectations: PASS

Results:

| Horizon | n | Win rate | Avg raw | Avg adjusted | Stop-hit | Target-hit | Expectancy R |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1d | 715 | 46.3% | -0.06% | -0.36% | 6.7% | 1.0% | +0.02 |
| 3d | 715 | 49.2% | -0.03% | -0.33% | 24.5% | 6.7% | +0.03 |
| 5d | 715 | 42.8% | -0.57% | -0.87% | 37.2% | 14.5% | -0.05 |
| 10d | 715 | 40.7% | -1.00% | -1.30% | 54.3% | 22.7% | -0.11 |

Year-by-year, primary horizons:

| Year | n | 3d WR | 3d avg adjusted | 5d WR | 5d avg adjusted | 3d stop-hit |
|---|---:|---:|---:|---:|---:|---:|
| 2020 | 181 | 39.2% | -2.65% | 26.0% | -4.17% | 44.8% |
| 2022 | 534 | 52.6% | +0.46% | 48.5% | +0.24% | 17.6% |

VIX proxy bucket behavior, 3d adjusted:

| Bucket | n | WR | Avg adjusted | Stop-hit |
|---|---:|---:|---:|---:|
| Watch 22-28 | 348 | 54.6% | +0.67% | 17.5% |
| Active 28-35 | 304 | 51.0% | +0.02% | 20.1% |
| Extreme 35+ | 63 | 11.1% | -7.54% | 84.1% |

Controls:
- ETF controls produced 191 non-tradable comparison signals:
  - 3d WR 42.4%, avg adjusted -0.73%, stop-hit 34.6%
  - 5d WR 37.7%, avg adjusted -0.98%, stop-hit 48.2%
- Single-stock controls were 40 / 715 signals:
  - 3d WR 55.0%, avg adjusted -0.01%, stop-hit 25.0%
  - 5d WR 47.5%, avg adjusted -0.16%, stop-hit 32.5%

Concentration:
- Top tickers: MA 21, MS 20, AXP 19, NKE 19, BAC 16, COST 16, JPM 16, GE 16
- No single ticker exceeded 3% of signals
- Top sectors: Consumer 18.9%, Financials 16.8%, Software 12.6%, Semis 9.9%,
  Healthcare 9.2%

Rejection attribution:
- Dominant exclusions were `vix_inactive`, `spy_washout_failed`,
  `rsi_not_oversold`, `no_reversal_quality`, and `freefall_guard`.
- Per-ticker rejection attribution now exists in the backtest path.

Quality / identity check:
- Signals matched the locked identity: fear regime, SPY washout, stock oversold,
  and reversal-quality evidence. No breakout, catalyst/news, quiet-flow, or
  short-continuation trigger was used.
- Company-specific bad-news cannot be proven from OHLCV. The diagnostic proxy
  flagged 82 / 715 signals (11.5%) where the stock's 20d return trailed SPY's
  20d return by at least 10 percentage points.
- The biggest weakness is not idiosyncratic bad-news frequency; it is crisis
  timing. Extreme panic conditions were too early/falling-knife-like under the
  current trigger, with 84.1% 3d stop-hit and -7.54% 3d adjusted return.

Diagnosis:
- CONTRARIAN has a coherent scanner identity but the first baseline is not
  paper-ready.
- The strategy worked modestly in 2022 watch/active fear regimes.
- It failed badly in 2020 extreme panic, which is supposed to be one of the
  sleeve's best regimes. That is a structural timing problem, not a cosmetic
  win-rate problem.
- 5d and 10d results decay materially, so the current trigger is not reliably
  capturing durable rebound follow-through.
- ETF controls were weak, which confirms the scanner is not simply buying broad
  index rebound well enough to justify paper.

Verdict:
**Research-only. One narrow research pass may be justified, but only on crisis
timing / extreme-panic confirmation.**

Do not tune thresholds broadly. Do not add catalyst/news filters yet. Do not
redesign the council yet. If research continues later, the clean question is:
does CONTRARIAN need a separate extreme-panic rule that waits for stabilization
after VIX-proxy 35+ conditions instead of buying the first reversal-looking bar?

**Run: 2026-04-22 — extreme-panic structural variants**

Script: `research/backtests/contrarian_backtest.py --variants`

Research question:
Does CONTRARIAN fail mainly because it enters extreme panic too early, and can a
simple stabilization rule improve results without breaking the panic/dislocation
identity?

Variants:
- Canonical baseline: current scanner-only logic.
- Variant A: current logic, but exclude trades where VIX proxy >= 35.
- Variant B: current logic for VIX proxy < 35; for VIX proxy >= 35, enter next
  day at the close only if next-day close > signal-day close and next-day low
  does not undercut signal-day low. This was chosen over `next-day close >
  signal-day high` because it tests stabilization directly without turning the
  sleeve into delayed breakout confirmation.

Comparison:

| Version | Signals | 1d WR / Adj | 3d WR / Adj | 5d WR / Adj | 10d WR / Adj | 3d stop-hit | 3d target-hit | 3d Exp R |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Baseline | 715 | 46.3% / -0.36% | 49.2% / -0.33% | 42.8% / -0.87% | 40.7% / -1.30% | 24.5% | 6.7% | +0.03 |
| Variant A: exclude 35+ | 652 | 48.9% / +0.05% | 52.9% / +0.37% | 45.9% / -0.21% | 43.7% / -0.66% | 18.7% | 7.1% | +0.11 |
| Variant B: stabilize 35+ | 657 | 48.7% / +0.03% | 52.8% / +0.34% | 46.0% / -0.21% | 43.7% / -0.66% | 18.9% | 7.0% | +0.10 |

Year-by-year, primary horizons:

| Version | Year | n | 3d WR | 3d adjusted | 5d WR | 5d adjusted | 3d stop-hit |
|---|---:|---:|---:|---:|---:|---:|---:|
| Baseline | 2020 | 181 | 39.2% | -2.65% | 26.0% | -4.17% | 44.8% |
| Baseline | 2022 | 534 | 52.6% | +0.46% | 48.5% | +0.24% | 17.6% |
| Variant A | 2020 | 122 | 55.7% | +0.17% | 36.1% | -1.99% | 23.0% |
| Variant A | 2022 | 530 | 52.3% | +0.41% | 48.1% | +0.20% | 17.7% |
| Variant B | 2020 | 125 | 55.2% | +0.02% | 36.0% | -2.05% | 24.0% |
| Variant B | 2022 | 532 | 52.3% | +0.41% | 48.3% | +0.22% | 17.7% |

VIX bucket behavior:
- Baseline extreme 35+: 63 signals, 3d WR 11.1%, 3d adjusted -7.54%, stop-hit 84.1%.
- Variant A removes the extreme bucket entirely.
- Variant B keeps only 5 stabilized extreme-panic signals, but they remain weak:
  3d WR 40.0%, 3d adjusted -3.66%, stop-hit 40.0%.

Concentration:
- Variant A top tickers: MS 19, MA 17, NKE 17, BAC 16, COST 16, JPM 16, GE 16,
  AXP 15.
- Variant A top sectors: Consumer 17.6%, Financials 16.9%, Software 12.4%,
  Semis 9.8%, Healthcare 9.4%, Industrials 8.1%.
- Variant B concentration was similar: no single ticker near dependency levels.

Diagnosis:
- The extreme-panic bucket is the true drag.
- Excluding VIX-proxy 35+ materially improves 3d expectancy and stop-hit rate,
  but 5d and 10d remain negative after friction.
- The simple stabilization rule does not rescue extreme-panic entries. It keeps
  only 5 extreme signals and those still lose badly.
- Therefore CONTRARIAN appears viable only in watch/active fear regimes below
  extreme panic. It does not currently have a robust extreme-crisis playbook.

Variant verdict:
**Variant A is the only structural improvement worth keeping for future
research: CONTRARIAN should stand down above VIX-proxy 35 until a separate
extreme-crisis doctrine exists.**

Even with Variant A, the sleeve is not paper-ready because 5d/10d adjusted
returns remain negative and validation relies on only 2020/2022 stress windows.
No further threshold tuning is recommended now.

## Last Paper-Trade Result

Not started.

## Promotion Blockers

None cleared. All three gates pending. Contrarian is 5th in validation order.

## Next Experiment

If continuing CONTRARIAN research, run one narrow timing pass only: isolate
extreme-panic (`VIX proxy >= 35`) failures and test whether a stabilization
requirement after the panic impulse improves results without mutating the sleeve
into generic dip-buying. Do not redesign the council or add news/catalyst filters
before that question is answered.

After the 2026-04-22 structural variant pass, the next decision is to stop
CONTRARIAN here for this phase or explicitly redefine the sleeve as active only
in VIX-proxy 22-35 conditions. Do not attempt another extreme-panic timing rule
without a new doctrine-level hypothesis.

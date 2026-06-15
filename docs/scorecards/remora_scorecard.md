---
strategy: REM — Remora
book: Short-Horizon Stealth (distinct from Book A long-trend sleeves)
code_path: strategies/remora.py
owner: TBD
last_updated: 2026-04-21
---

# Remora Scorecard

## Mandate

Stealth institutional accumulation / quiet-flow detection, LONG only.

REMORA detects unusual quiet buying activity before it becomes visible momentum.
It is a short-horizon tactical sleeve, not a catalyst/news burst sleeve.

**Holding horizon: intraday to ~5 trading days.**

**Official identity decision, 2026-04-21:** Commit REMORA to
**A. Stealth Accumulation / Quiet Flow**.

## Thesis

Institutions cannot accumulate shares without leaving a volume footprint, even when trying to minimize market impact. A stock near its 52-week high with flat price action but elevated dollar volume suggests systematic buying behind the scenes. Retail accounts can enter the same session and hold while the footprint resolves into visible momentum.

## Identity Decision

REMORA had two competing candidate identities:

### A. Stealth Accumulation / Quiet Flow — Chosen

Edge source:
- institutions need liquidity but often avoid visible price impact
- quiet price action with moderately abnormal volume near highs can reveal
  low-footprint accumulation before momentum becomes obvious
- retail accounts can enter the same session and exit over a short horizon
  before the signal becomes a crowded breakout

Trigger:
- price change < 0.5%
- volume 20-60% above 20-day average
- dollar volume >= $25M
- price within 2% of 52-week high
- spread < 0.15%
- no earnings within 5 days
- score >= 55

Invalidation:
- the stealth thesis is wrong if price breaks below the ATR-based stop,
  if the quiet-volume condition becomes a visible breakout/chase, or if the
  stock loses the near-high strength context that made the flow meaningful

Regime fit:
- active in normal to constructive markets where quiet institutional footprints
  are readable
- selective in elevated volatility because broad-market volume can contaminate
  stock-specific signals
- silent in panic/chaos regimes where volume anomalies are market-wide and
  better handled by CONTRARIAN

Overlap risk:
- with SNIPER: controlled by requiring no price breakout and only moderate
  volume, not explosive breakout participation
- with VOYAGER: controlled by short holding horizon and single-day flow trigger,
  not multi-week accumulation and 6-18 month thesis
- with CONTRARIAN: controlled by near-high strength; REMORA does not buy washouts

### B. Catalyst / News / Volume Burst — Rejected For REMORA

Edge source:
- fresh catalyst creates a fast repricing window that slower capital may not
  digest immediately
- abnormal volume shock plus fast momentum can continue for intraday to a few
  days

Trigger:
- verified fresh catalyst or news event
- abnormal volume shock, likely > 1.6x to 3.0x normal
- directional price expansion or gap/continuation behavior
- event timing integrity and headline availability must be point-in-time

Invalidation:
- catalyst follow-through fails, price reverts below the event reference level,
  or liquidity/spread deteriorates after the initial burst

Regime fit:
- active when catalyst reactions are being rewarded and liquidity is healthy
- selective around macro-event days and broad selloffs
- silent when news reactions are noisy, one-bar fades, or panic-driven

Overlap risk:
- with SNIPER: high. Catalyst continuation can look like a breakout bar unless
  the catalyst/event trigger is the primary edge source
- with CONTRARIAN: medium. Negative catalyst overreaction can become a mean
  reversion setup, but that belongs to CONTRARIAN if the edge is panic/overshoot
- with VOYAGER: low to medium. A catalyst burst is not a long-horizon
  accumulation entry, but it can occur in the same leadership names

Decision:
- REMORA will not mix catalyst/news burst logic with quiet-flow logic.
- If the platform later needs a catalyst/news/volume-burst strategy, create a
  separate sleeve or rename/rebuild explicitly. Do not smuggle that logic into
  REMORA.

## Identity and Anti-Drift Notes

- **REMORA exploits the institutional footprint** — the edge is reading unusual quiet activity, not chasing visible breakouts.
- **Distinct from SNIPER:** SNIPER requires a structural price breakout (explosive volume). REMORA requires no price breakout — only quiet unusual volume (20–60% above average).
- **Distinct from VOYAGER:** VOYAGER detects multi-week accumulation trends over a 6–18 month hold. REMORA detects a single day's unusual signature and holds for days.
- **Not a catalyst scanner:** Fresh news, event shocks, and visible volume bursts
  are not REMORA's official mandate. Those require a separate doctrine if pursued.
- **No short logic belongs here.** SHORT is the sole short-direction strategy.

## Signal Conditions

- Price change < 0.5% (stealth — no headline move)
- Volume 20–60% above 20-day average
- Dollar volume ≥ $25M (institutional grade)
- Price within 2% of 52-week high
- Bid-ask spread < 0.15%
- No earnings within 5 days

## Required Data

- Alpaca: daily OHLCV bars, bid-ask spread
- FMP: earnings calendar

## Validation Status

| Gate | Status | Notes |
|------|--------|-------|
| doctrine-valid | pass | identity locked as stealth accumulation / quiet flow |
| scanner-audit-valid | pass | implementation matches quiet-flow identity; rejection attribution added 2026-04-21 |
| data-valid | partial | price/volume/quote/earnings paths are clear; accumulation proxy still needs historical validation |
| backtest-design-valid | pass | validation design defined below; do not tune before baseline run |
| backtest-valid | research-only | first baseline run completed 2026-04-22; not paper-ready |
| paper-valid | pending | not started |

## Quant Research Doctrine Audit — 2026-04-21

### Scanner Audit

Current implementation: `strategies/remora.py`.

Scanner metrics/features:
- daily OHLCV bars: 252 trading days
- absolute 1-day price change
- 20-day average volume
- current volume / 20-day average volume ratio
- current dollar volume
- 52-week high and percent from 52-week high
- live bid/ask spread
- 14-bar ATR
- FMP earnings calendar, 5-day lookahead

Hard entry conditions:
- LONG only
- price change < 0.5%
- volume ratio between 1.20 and 1.60
- dollar volume >= $25M
- price within 2% of 52-week high
- spread < 0.15%
- no earnings within 5 days
- score >= 55
- R:R >= 2.0 from ATR stop/target geometry

Stop / target / invalidation:
- stop = entry - 1.2x ATR
- target = entry + 3.0x ATR
- nominal R:R = 2.5
- invalidation is break below the ATR stop, or loss of the quiet-flow near-high
  context that justified the entry

Identity fit:
- quiet price: PASS
- moderate abnormal volume: PASS
- near highs: PASS
- stealth institutional footprint: PASS as a hypothesis; still needs historical validation
- short horizon: PASS in doctrine, but the backtest must use 1d/3d/5d horizons
- breakout confirmation: NOT PRESENT
- catalyst/news burst: NOT PRESENT
- panic reversal: NOT PRESENT

Rejection attribution:
- before this audit, the scanner returned `None` silently and only logged the final
  setup count
- 2026-04-21 patch added top rejection logging with reasons:
  `earnings_soon`, `stale_bars`, `price_moved`, `vol_too_low`, `vol_too_high`,
  `low_dollar_vol`, `not_near_52w_high`, `no_quote`, `wide_spread`, `poor_geometry`,
  `low_score`, `exception`
- 2026-04-21 patch also removed the prior silent quote fallback. Missing quotes now
  reject as `no_quote` instead of assuming acceptable spread.

52-week-high / stale-bars decision:
- This was a real validation issue because the scanner used `BARS_NEEDED = 252`
  but accepted as few as 60 bars, which could understate the true 52-week high
  for newer listings.
- Decision: fix before backtesting.
- 2026-04-21 patch changed the stale-bars gate to require the full 252 bars.
  This is conservative and avoids false positives in a sleeve whose identity
  depends on "near 52-week high."

Scanner/council alignment:
- Current live council uses a generic weight table, not REMORA-specific weights.
- The live FlowAgent is intraday-oriented: it compares late 5-minute volume to
  early 5-minute volume. REMORA's scanner thesis is daily moderate volume.
- This mismatch is acceptable for validation if results report scanner-only and
  scanner+council outcomes separately. It is not a blocker for baseline backtest
  design because the scanner edge can be tested first.
- Minimal clarification needed before paper/capital promotion: document or add a
  REMORA council profile where flow remains important but does not overrule the
  daily quiet-volume setup by itself.

### Pre-Backtest Checklist

| Section | Status | Notes |
|---|---|---|
| A. Doctrine / mandate clarity | PASS | REMORA identity is now locked as stealth accumulation / quiet flow. Edge source, trigger, invalidation, regime fit, horizon, and separation are explicit. |
| B. Scanner / council fit | PARTIAL | Scanner matches mandate. Council is workable for validation if scanner-only and scanner+council results are separated; live council still lacks a REMORA-specific profile. Does not block baseline scanner backtest. |
| C. Data integrity | PASS | Daily OHLCV, quote, and earnings paths are clear. Stale-bars issue fixed by requiring 252 bars; missing quotes now fail closed as `no_quote`. |
| D. Execution realism | PASS | Baseline stop/target and friction assumptions are defined for validation. No tuning before baseline run. |
| E. Output expectations | PASS | Required signal count, expectancy, stop-hit, control, concentration, and scanner/council outputs are defined below. |

### Backtest Readiness Verdict

**Ready for baseline deep backtest design and implementation.**

The identity is clean and the scanner audit issues that would contaminate a
baseline backtest have been resolved or scoped. Do not tune thresholds before the
baseline run. The first validation should test the current quiet-flow scanner as
implemented.

## Validation Plan

### Scanner Audit

- Confirm each rejection reason is logged and attributable.
- Confirm price-change gate prevents visible breakout/chase entries.
- Confirm volume ratio [1.20, 1.60] excludes SNIPER-style breakout volume.
- Confirm 52-week-high calculation is point-in-time and not distorted by short
  history for newer listings.
- Confirm earnings blackout uses point-in-time calendar availability.
- Confirm `no_quote` and `wide_spread` behavior in the backtest/live scanner bridge.

### Backtest Design

- Universe: liquid US equities routed to the REMORA universe, plus controls that
  should rarely pass:
  - defensive ETFs / low-beta names
  - low-volume names below institutional liquidity
  - obvious breakout names that should fail `price_moved` or `vol_too_high`
  - panic/washout names that should fail near-high context
- Window: at least 2020-2024 to cover bull, bear, recovery, and high-volatility
  environments.
- Entry: signal-date close for EOD version; intraday variant requires separate
  doctrine and data timing rules.
- Horizon: 1d, 3d, and 5d primary; no 6-18 month analysis.
- Friction: commission 0.05% each way; slippage at least 0.10% each way, higher
  if spread or intraday liquidity is worse.
- Stop/target: current scanner baseline is 1.2x ATR stop and 3.0x ATR target
  (R:R 2.5). Test this as baseline, not as a tuned afterthought.
- Regime handling: active below chaos/panic conditions; analyze elevated VIX
  separately to test whether broad-market volume contaminates the signal.
- Scanner/council comparison:
  - required if practical
  - report scanner-only first
  - then apply current generic council and report pass/fail deltas
  - do not change scanner thresholds to compensate for council behavior

### Paper-Readiness Requirements

Minimum thresholds before paper:
- n >= 40 signals in historical validation
- average adjusted return > 0% at the primary horizon
- win rate >= 50% at the primary horizon, unless expectancy is clearly positive
  with asymmetric winners
- stop-hit rate < 45% for the baseline ATR stop
- controls < 20% of signals
- no single ticker > 30% of signals
- performance not dependent on one year or one sector
- scanner-only and scanner+council results both reported

## Validation Blockers

- [x] Identity locked as stealth accumulation / quiet flow
- [x] Rejection attribution added
- [x] Stale 52-week-high issue resolved by requiring 252 bars
- [x] Missing quote fallback removed; scanner now rejects `no_quote`
- [x] Scanner-only vs scanner+council reporting plan defined
- [ ] Accumulation proxy validation (confirm volume signature is not noise)
- [ ] Macro contamination audit (ensure signals are stock-specific, not market-wide)
- [ ] Sector and liquidity controls confirmed by baseline backtest

## Last Backtest Result

**Run: 2026-04-22 — baseline quiet-flow scanner-only plus single-stock structural rerun**

Script: `research/backtests/remora_backtest.py`

Baseline configuration:
- Universe: 88 liquid equities/ETFs, including REMORA-style liquid names and controls
- Window: 2020-01-01 to 2024-12-31
- Entry: signal-date close
- Horizons: 1d / 3d / 5d
- Friction: 0.30% round trip
- Stop: entry - 1.2x ATR
- Target: entry + 3.0x ATR
- Scanner-only baseline; no council replay
- No threshold tuning, catalyst/news logic, breakout logic, sector bans, ticker bans,
  or council redesign
- Historical spread limitation: live bid/ask spread is not available in the OHLCV
  cache, so the backtest did not simulate the production spread gate. Production
  scanner still fails closed on missing quote via `no_quote`.

Checklist confirmation:
- Doctrine / mandate clarity: PASS
- Scanner / council fit: PARTIAL
- Data integrity: PARTIAL for full production equivalence because historical
  spread is unavailable; OHLCV path is clean
- Execution realism: PASS for baseline ATR stop/target and friction
- Output expectations: PASS

Structural rerun:
- Research question: does REMORA have a real single-stock quiet-flow edge once
  ETF/control instruments are excluded from the tradable universe?
- Tradable universe: 74 single stocks
- Controls retained for reporting only: 14 ETFs/control instruments
- Scanner logic unchanged
- Volume ratio band, price-change cap, 52-week-high requirement, stop/target,
  horizons, and friction unchanged

Baseline results:

| Horizon | n | Win rate | Avg raw | Avg adjusted | Stop-hit | Target-hit |
|---|---:|---:|---:|---:|---:|---:|
| 1d | 594 | 39.4% | +0.04% | -0.26% | 14.8% | 1.3% |
| 3d | 594 | 41.8% | +0.15% | -0.15% | 34.7% | 4.7% |
| 5d | 594 | 42.8% | +0.27% | -0.03% | 42.9% | 9.3% |

Single-stock-only results:

| Horizon | n | Win rate | Avg raw | Avg adjusted | Stop-hit | Target-hit |
|---|---:|---:|---:|---:|---:|---:|
| 1d | 301 | 46.2% | +0.07% | -0.23% | 15.6% | 2.0% |
| 3d | 301 | 45.2% | +0.27% | -0.03% | 33.6% | 6.3% |
| 5d | 301 | 48.5% | +0.51% | +0.21% | 41.2% | 11.6% |

Year-by-year, 3d adjusted:

| Year | n | WR | Avg adjusted | Stop-hit |
|---|---:|---:|---:|---:|
| 2020 | 80 | 53.8% | +0.58% | 18.8% |
| 2021 | 179 | 38.0% | -0.26% | 36.3% |
| 2022 | 23 | 26.1% | -1.04% | 52.2% |
| 2023 | 95 | 38.9% | -0.36% | 42.1% |
| 2024 | 217 | 43.3% | -0.13% | 34.1% |

Single-stock-only year-by-year, 3d adjusted:

| Year | n | WR | Avg adjusted | Stop-hit |
|---|---:|---:|---:|---:|
| 2020 | 35 | 65.7% | +1.11% | 17.1% |
| 2021 | 69 | 44.9% | -0.16% | 29.0% |
| 2022 | 11 | 18.2% | -1.37% | 54.5% |
| 2023 | 67 | 41.8% | -0.25% | 40.3% |
| 2024 | 119 | 43.7% | -0.04% | 35.3% |

Controls:
- 293 / 594 signals = 49.3%
- top tickers: AGG 75, XLP 46, XLV 32, SPY 25, XLI 20
- in the single-stock rerun, controls were excluded from tradable signals and
  produced 293 comparison signals:
  - 1d WR 32.4%, avg adjusted -0.29%, stop-hit 14.0%, target-hit 0.7%
  - 3d WR 38.2%, avg adjusted -0.27%, stop-hit 35.8%, target-hit 3.1%
  - 5d WR 36.9%, avg adjusted -0.28%, stop-hit 44.7%, target-hit 6.8%

Single-stock concentration:
- top tickers: V 14, AXP 12, COST 12, MA 12, ISRG 12, ABBV 11, HD 10
- no single ticker exceeded 5% of the single-stock signal set
- sector concentration: Financials 20.3%, Healthcare 15.0%, Software 14.0%,
  Consumer 13.0%, Semis 10.0%, Industrials 7.6%

Diagnosis:
- REMORA identity stayed clean: selected signals are quiet price, moderate abnormal
  volume, and near highs. No catalyst/news fields and no breakout trigger were used.
- Excluding ETF/control instruments removed the universe contamination and
  improved signal quality, especially at 5d, where adjusted return turned positive.
- The structural rerun does not prove a stable paper-ready edge. The primary 3d
  tactical horizon remains slightly negative after friction, 2021-2024 remain
  weak, and 2022 is sharply negative with a small sample.
- Baseline weakness was partly a control-universe contamination problem, but not
  only that. The single-stock sleeve still lacks enough stable adjusted expectancy
  across years to justify paper.
- Scanner+council comparison was not run because the live council requires
  historical intraday bars, quote snapshots, sentiment, macro state, and portfolio
  state. Replaying it from daily OHLCV would fabricate inputs.

Verdict:
**REMORA remains research-only. Stop here for this phase.**

The single-stock-only rerun answered the narrow structural question. Removing
controls revealed a better single-stock profile, but not a durable enough edge
for paper. Do not tune thresholds, add catalyst/news logic, add breakout logic,
or redesign the council for REMORA in this phase.

## Last Paper-Trade Result

Not started.

## Promotion Blockers

None cleared. All three gates pending. Remora is 4th in validation order.

## Next Experiment

No further REMORA experiment is recommended in this phase. Move the doctrine
sequence to CONTRARIAN after recording REMORA as research-only.

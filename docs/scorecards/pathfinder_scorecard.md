---
strategy: PATHFINDER
book: Future Long Research
code_path: strategies/pathfinder.py
baseline_tag: PATHFINDER_V1
last_updated: 2026-04-22
status: future research-only
---

# PATHFINDER Scorecard

Subtitle: **Early Sponsorship / Emerging Leader**

PATHFINDER is a future LONG-only sleeve. It is not in the current paper book and
must not mutate VOYAGER, SNIPER, REMORA, CONTRARIAN, or SHORT.

Doctrine spec: `docs/strategy/PATHFINDER_EARLY_SPONSORSHIP_SPEC.md`

## Mandate

Capture smaller but liquid companies showing real business and tape improvement
before broad institutional sponsorship is obvious.

## Edge Source

PATHFINDER exploits institutional slowness and delayed recognition in emerging
leaders. Large institutions are often constrained by liquidity, market cap,
coverage, committee process, and proof requirements. Retail-sized accounts can
enter earlier, but only when business and price evidence show the opportunity is
becoming real.

## Identity

PATHFINDER is:

- LONG-only
- early sponsorship / emerging leader
- smaller-cap but tradeable
- business-inflection aware
- before confirmed breakout chase
- before broad crowding is obvious

PATHFINDER is not:

- microcap speculation
- meme/hype trading
- generic dip buying
- VOYAGER-style mature accumulation
- SNIPER-style breakout confirmation
- REMORA-style quiet near-high flow
- CONTRARIAN panic rebound

## Baseline Universe

Live baseline:

- market cap: `$500M-$15B`
- price: `>= $8`
- 20d average dollar volume: `$5M-$150M`
- US-listed common stocks
- ETFs are controls only

This keeps the sleeve early enough to differ from VOYAGER/SNIPER while avoiding
microcap and illiquid execution traps.

## PATHFINDER_V1 Trigger

All gates must pass:

1. business inflection score `>= 55`
2. price above MA50
3. MA50 not materially below MA150
4. higher-low structure: recent 20d low above prior 60d low
5. 20d dollar volume improving versus prior 60d dollar volume
6. 20d and 60d relative strength versus SPY positive enough to show early
   leadership
7. not extended: max `12%` above MA50 and max `25%` above 60d base low
8. not a chase: current volume not above `2.5x` 20d average
9. precise trigger: close above prior 10d high while previous close was not
   already above that prior 10d high
10. geometry: stop/target support minimum `2.5` R:R

## Invalidation

Baseline stop:

- `max(entry - 1.8 * ATR, MA50 * 0.96)`

Baseline target:

- `entry + 4.5 * ATR`

The thesis is wrong if the higher-low base fails, MA50 support is lost in a way
that breaks the emerging-leader structure, or subsequent business data no longer
supports the inflection.

## Holding Horizon

Initial validation:

- primary: `20d`
- secondary: `5d / 10d / 60d`

PATHFINDER may naturally have lower win rate and higher upside skew than
VOYAGER/SNIPER. Judge it on adjusted expectancy, loss profile, year/regime
stability, and sample quality.

## Pre-Backtest Checklist

| Section | Status | Notes |
|---|---|---|
| A. Doctrine / mandate clarity | PASS | Edge source, trigger, invalidation, regime, horizon, and separation are explicit. |
| B. Scanner / council fit | PARTIAL | Scanner-only baseline first. A PATHFINDER council profile does not exist yet. |
| C. Data integrity | PARTIAL | Production can use cached FMP fundamentals; historical paper-readiness requires point-in-time fundamentals. |
| D. Execution realism | PASS | Liquidity, stop/target, R:R, and friction assumptions are defined. |
| E. Output expectations | PASS | Baseline report should include signal count, adjusted return, stop/target rates, concentration, and blocker summary. |

## Baseline Backtest Design

Script:

- `research/backtests/pathfinder_backtest.py`

Universe:

- curated emerging-leader single stocks plus SPY/QQQ/IWM controls

Window:

- full: `2020-2024`
- quick smoke: `2022-2023`

Data path:

- price: `BacktestDataLoader` reading local `cache/backtest_prices`
- fundamentals: optional local point-in-time JSON only
- no current FMP fundamentals in historical loops

Friction:

- `0.05%` commission each way
- `0.10%` slippage each way
- `0.30%` total round trip

Pass criteria for future paper consideration:

- enough signals, initially `>= 50`
- positive 20d adjusted expectancy, preferably `> +1.0%`
- stop-hit rate below `45%`
- no unacceptable single-name/sector concentration
- stability across years and market regimes
- point-in-time fundamentals integrated without lookahead

## Current Verdict

Paused / research-only. Doctrine remains distinct, but the final verification
pass did not produce enough point-in-time business-inflection evidence to
justify continued work in this phase. PATHFINDER is not paper-ready and is not
wired into live or paper execution.

## Baseline Validation — 2026-04-22

Command:

```bash
SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python research/backtests/pathfinder_backtest.py
```

Baseline rules:

- `PATHFINDER_V1` logic unchanged
- scanner-only
- no threshold tuning
- no live/paper wiring
- OHLCV-only historical baseline; current FMP fundamentals were not used as
  historical truth

### Main Results

Universe/window:

- universe: 57 tickers including controls
- tradable signals exclude controls
- window: `2020-01-01` to `2024-12-31`
- friction: `0.30%` round trip

Signals:

- `n = 12`

Horizon results:

| Horizon | Win rate | Avg raw | Avg adjusted | Stop-hit | Target-hit |
|---:|---:|---:|---:|---:|---:|
| 5d | 41.7% | +0.06% | -0.24% | 25.0% | 0.0% |
| 10d | 58.3% | +2.06% | +1.76% | 33.3% | 0.0% |
| 20d | 41.7% | -1.14% | -1.44% | 50.0% | 8.3% |
| 60d | 33.3% | +1.43% | +1.13% | 66.7% | 33.3% |

Year-by-year at 20d adjusted:

| Year | Signals | Win rate | Avg adjusted | Stop-hit |
|---:|---:|---:|---:|---:|
| 2020 | 1 | 100.0% | +0.03% | 0.0% |
| 2021 | 2 | 0.0% | -5.62% | 100.0% |
| 2022 | 1 | 100.0% | +19.71% | 0.0% |
| 2023 | 2 | 100.0% | +2.28% | 0.0% |
| 2024 | 6 | 16.7% | -5.06% | 66.7% |

Concentration:

- sectors: Software 33.3%, Healthcare 25.0%, Consumer 16.7%,
  Industrials 16.7%, Biotech 8.3%
- top tickers: ESTC, AXON, and PEN each 2 signals / 16.7%

Representative signals:

- `2020-02-05 HALO`: 20d adjusted +0.03%
- `2021-09-22 ESTC`: 20d adjusted -6.22%, stop
- `2021-10-07 ELF`: 20d adjusted -5.03%, stop
- `2022-10-21 AXON`: 20d adjusted +19.71%, target
- `2023-03-16 PEN`: 20d adjusted +3.31%
- `2023-03-21 PEN`: 20d adjusted +1.25%
- `2024-03-08 DOCN`: 20d adjusted -4.98%
- `2024-05-02 AXON`: 20d adjusted -4.32%, stop

### Overlap Diagnostic

PATHFINDER signal count checked:

- `12`

SNIPER overlap:

- full Sniper-like structural matches: `0 / 12`
- 20-day breakout only: `1 / 12`
- volume spike >= 1.4x only: `2 / 12`
- both 20-day breakout and >= 1.4x volume: `0 / 12`

Conclusion: baseline PATHFINDER is not simply re-expressing SNIPER. It mostly
selects earlier or quieter structure, not confirmed breakout bars.

VOYAGER overlap:

- Voyager-like technical structure: `2 / 12`

Conclusion: there is limited technical overlap with VOYAGER-style constructive
long setups, but the horizon, universe, edge claim, and trigger remain distinct.

### Baseline Verdict

`RESEARCH-ONLY`.

The baseline is distinct enough from SNIPER to continue studying, but it does
not yet show a strong enough edge:

- primary 20d adjusted expectancy is negative
- stop-hit rate is high at the primary horizon
- sample size is too small
- 2024 behavior is weak
- historical fundamentals remain a data blocker for a true business-inflection
  validation

One narrow next research pass is justified only if it addresses the data/identity
question directly: add a point-in-time local fundamentals proxy or remove the
business-inflection claim from the historical test. Do not tune price thresholds
first.

## Final Verification Pass — 2026-04-22

Research question:

Does PATHFINDER have a real distinct edge when tested with a business-inflection
layer that is available as of the historical signal date, or was the weak
tape-only baseline telling us the sleeve/data/universe is not strong enough?

### Data Integrity Audit

Current production scanner fields:

- Alpaca daily OHLCV
- FMP current company profile for market-cap/sector gates
- FMP current latest-quarter fundamentals for live-only business-inflection
  scoring
- SPY relative-strength context

Historical backtest fields before this pass:

- local cached OHLCV only
- no current fundamentals reused through history

Conceptual fields not fully proven historically before this pass:

- true point-in-time business inflection
- early sponsorship / under-owned status
- point-in-time market cap and ownership/crowding

Lookahead finding:

- the original tape-only backtest did not use fundamental lookahead because it
  skipped fundamentals entirely
- current production fundamentals are suitable for live scanning but cannot be
  applied retroactively as historical truth

### PIT Fundamentals Proxy

Implemented in:

- `research/backtests/pathfinder_backtest.py`

Local cache:

- raw statements: `cache/pathfinder_fundamentals/{TICKER}.json`
- derived score map: `cache/pathfinder_fundamentals/pathfinder_v1_pit_scores.json`

Method:

- fetch quarterly FMP income statement and cash-flow history once per ticker
- assign each quarter an availability date from `acceptedDate` / `filingDate`
  where present
- if no filing/accepted date is available, use fiscal quarter date + 60 calendar
  days
- compute the same `PATHFINDER_V1` fundamental inflection score using only the
  latest four quarters available as of each historical signal date
- backtest reads the local score map; FMP is not called inside the historical
  evaluation loop

Limitations:

- this is a point-in-time proxy, not a full SEC point-in-time database
- current curated universe creates survivorship/current-availability bias
- point-in-time market cap and ownership/crowding are still not reconstructed

### Tape-Only Baseline Vs PIT Business-Inflection Baseline

Commands:

```bash
SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python research/backtests/pathfinder_backtest.py
SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python research/backtests/pathfinder_backtest.py --pit-fundamentals
```

Window/universe:

- `2020-01-01` to `2024-12-31`
- 57-ticker PATHFINDER universe including SPY/QQQ/IWM controls
- tradable signals exclude controls
- friction: `0.30%` round trip

| Version | Signals | 5d WR / Avg Adj | 10d WR / Avg Adj | 20d WR / Avg Adj | 60d WR / Avg Adj | 20d Stop | 20d Target |
|---|---:|---:|---:|---:|---:|---:|---:|
| Tape-only | 12 | 41.7% / -0.24% | 58.3% / +1.76% | 41.7% / -1.44% | 33.3% / +1.13% | 50.0% | 8.3% |
| PIT business layer | 3 | 66.7% / +1.84% | 66.7% / +5.26% | 66.7% / +7.32% | 66.7% / +7.32% | 33.3% | 66.7% |

Year-by-year at 20d adjusted:

| Version | 2020 | 2021 | 2022 | 2023 | 2024 |
|---|---:|---:|---:|---:|---:|
| Tape-only | n=1, +0.03% | n=2, -5.62% | n=1, +19.71% | n=2, +2.28% | n=6, -5.06% |
| PIT business layer | n=0 | n=1, -5.03% | n=1, +19.71% | n=1, +7.27% | n=0 |

PIT concentration:

- sectors: Industrials 66.7%, Consumer 33.3%
- tickers: AXON 2 signals / 66.7%, ELF 1 signal / 33.3%

PIT representative trades:

- `2021-10-07 ELF`: fund score 60, 20d adjusted -5.03%, stop
- `2022-10-21 AXON`: fund score 85, 20d adjusted +19.71%, target
- `2023-11-30 AXON`: fund score 85, 20d adjusted +7.27%, target

PIT overlap check:

- Sniper-like full structural overlap: `0 / 3`
- 20d breakout only: `2 / 3`
- volume spike >= 1.4x: `0 / 3`
- Voyager-like technical structure: `3 / 3`

### Diagnosis

The PIT fundamentals layer adds separation versus the weak tape-only baseline:
it removes many losing/noisy 2024 tape-only signals and improves the 20d
outcome profile. However, it does so by collapsing the test to only three
trades over five years, two of which are AXON and all of which look
Voyager-like on technical structure.

Best-supported root cause:

- the available data/scanner/universe combination is too sparse to validate a
  distinct PATHFINDER edge

Secondary issues:

- point-in-time ownership/crowding and point-in-time market-cap history remain
  incomplete
- the business-inflection gate appears useful but not broad enough in this
  universe to support a standalone sleeve

### Final Verdict

`PAUSE / SHELVE PATHFINDER FOR NOW`.

Do not tune price thresholds or add more filters. PATHFINDER remains a coherent
future sleeve concept, but this final honest pass did not show enough distinct,
repeatable evidence to justify continued work in the current phase.

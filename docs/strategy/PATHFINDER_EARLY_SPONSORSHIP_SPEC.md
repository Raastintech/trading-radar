# PATHFINDER Early Sponsorship / Emerging Leader Spec

**Status:** Future research sleeve, paused after final verification  
**Baseline tag:** `PATHFINDER_V1`  
**Last updated:** 2026-04-22

PATHFINDER is a future LONG-only sleeve. It is not part of the current active
paper set and must not mutate VOYAGER, SNIPER, REMORA, CONTRARIAN, or SHORT.

## Mandate

PATHFINDER seeks emerging leaders before broad institutional sponsorship is
obvious.

The edge is not random early guessing. The edge is exploiting the interval where
smaller but liquid companies show enough business improvement and technical
repair to become investable, while many large institutions remain slow,
constrained by mandate size, liquidity requirements, committee process, or the
need for more quarters of proof.

## Edge Source

PATHFINDER exploits three linked inefficiencies:

1. **Institutional slowness:** many funds cannot build meaningful positions
   early in smaller names until market cap, liquidity, coverage, and business
   proof improve.
2. **Delayed recognition:** real business inflections often become visible in
   fundamentals and tape before broad sponsorship, index inclusion, or consensus
   ownership arrives.
3. **Retail agility:** a smaller account can enter a liquid emerging leader
   before it becomes a crowded large-cap institutional consensus name.

The edge should exist because large pools of capital are structurally slower
than the information diffusion path in improving smaller public companies.

## Identity

PATHFINDER is:

- LONG-only
- early sponsorship / emerging leader
- smaller-cap but still tradeable
- before obvious institutional crowding
- before confirmed breakout-chase behavior
- before broad sponsorship is obvious

PATHFINDER is not:

- microcap speculation
- meme or hype trading
- generic dip buying
- long-horizon mature institutional accumulation
- breakout confirmation
- quiet-flow near-highs clone
- panic rebound

## Holding Horizon

Initial validation horizon family:

- primary: `20 trading days`
- secondary: `5d / 10d / 60d`

Rationale: PATHFINDER is earlier than SNIPER but not as slow as VOYAGER. A
1-12 week family is appropriate, but the first honest baseline should judge the
20d path first because business/tape inflections usually need more than a few
days to express and should not be judged like a tactical breakout.

## Regime Fit

Active:

- constructive or neutral equity regimes
- SPY not in a confirmed panic/breakdown state
- VIX proxy below `28`
- liquidity available for smaller growth/inflection names

Selective:

- VIX proxy `28-35`
- SPY below MA200 but stabilizing
- only strongest balance of business inflection, RS turn, and non-extended base
  should survive

Silent:

- VIX proxy `>= 35`
- broad market liquidation
- macro lockout regimes
- periods where small/mid growth liquidity is impaired

## Baseline Universe

Baseline live universe:

- US-listed common stocks only
- market cap: `$500M-$15B`
- price: `>= $8`
- 20d average dollar volume: `$5M-$150M`
- exclude ETFs, microcaps, illiquid names, OTC names, and obvious mega-cap
  consensus leaders

Tradeoff:

- The lower cap bound avoids microcap trash and spread traps.
- The upper cap bound keeps the sleeve away from VOYAGER/SNIPER mega-cap
  crowding.
- The dollar-volume floor supports retail execution realism.
- The dollar-volume ceiling avoids names where sponsorship is already obvious.

## Baseline Signal Logic

### Business / Structural Improvement

Production scanner uses cached FMP fundamentals:

- latest quarterly revenue growth positive and meaningful
- latest growth accelerating versus prior quarter
- two-quarter growth positive
- operating margin improving
- gross margin improving
- operating cash flow positive where available
- operating income improving

Baseline hard gate:

- fundamental inflection score `>= 55`

Historical warning:

- Current FMP fundamentals must not be used for historical backtests because
  that would introduce lookahead.
- Historical tests now use a local point-in-time proxy only when explicitly run
  with `--pit-fundamentals`.

Historical PIT proxy:

- raw quarterly FMP statements are cached under
  `cache/pathfinder_fundamentals/{TICKER}.json`
- derived dated scores are cached in
  `cache/pathfinder_fundamentals/pathfinder_v1_pit_scores.json`
- statement availability uses `acceptedDate` / `filingDate` where available
- if availability fields are missing, fiscal quarter date + 60 calendar days is
  used as a conservative approximation
- the proxy computes the same `PATHFINDER_V1` business-inflection score using
  only quarters available as of the historical signal date

Limitations:

- this is an honest point-in-time proxy, not a full SEC-grade PIT database
- point-in-time ownership/crowding and market-cap history are not reconstructed
- current curated-universe survivorship bias remains

### Tape / Price Improvement

Baseline tape gates:

- price above MA50
- MA50 not materially below MA150
- 20d low is above prior 60d low, confirming early higher-low structure
- 20d dollar volume improving versus prior 60d dollar volume
- 20d and 60d relative strength versus SPY positive enough to show early
  leadership

### Not Already Crowded

Baseline anti-crowding gates:

- market cap below `$15B`
- 20d dollar volume below `$150M`
- not more than `12%` above MA50
- not more than `25%` above the 60d base low
- no single-day chase volume above `2.5x` 20d average volume

### Trigger

Entry trigger:

- close above the prior 10-day high
- previous close was not already above the prior 10-day high
- all business, tape, crowding, regime, and geometry gates pass

This is not a SNIPER breakout trigger. The resistance window is shorter, volume
is capped to avoid chase behavior, and the setup requires business inflection
plus emerging-leader base structure.

### Invalidation

Trade is wrong if:

- price violates the initial structural stop
- the higher-low/base structure fails
- price loses MA50 support in a way that invalidates emerging sponsorship
- later data shows the business inflection did not persist

Baseline stop:

- `max(entry - 1.8 * ATR, MA50 * 0.96)`

Baseline target:

- `entry + 4.5 * ATR`

Minimum R:R:

- `2.5`

## Overlap Check

### VOYAGER

- Timing: PATHFINDER is earlier/smaller and 1-12 weeks; VOYAGER is mature
  institutional accumulation over 6-18 months.
- Edge: PATHFINDER exploits institutional slowness before broad sponsorship;
  VOYAGER follows durable institutional accumulation.
- Trigger: PATHFINDER uses business inflection plus early RS/base turn; VOYAGER
  uses long-horizon accumulation archetypes.
- Regime: PATHFINDER is more sensitive to small/mid liquidity.

### SNIPER

- Timing: PATHFINDER enters before obvious breakout-chase behavior; SNIPER enters
  on confirmed breakout.
- Edge: PATHFINDER exploits delayed recognition; SNIPER exploits institutional
  participation visible on the breakout bar.
- Trigger: PATHFINDER uses a short higher-high trigger with capped volume;
  SNIPER requires a close above prior 20d high with volume spike.
- Regime: both need constructive regimes, but PATHFINDER is more selective when
  small-cap liquidity deteriorates.

### REMORA

- Timing: PATHFINDER holds weeks; REMORA is short-horizon quiet-flow.
- Edge: PATHFINDER requires business inflection; REMORA detects stealth daily
  flow near highs.
- Trigger: PATHFINDER requires structural improvement and early leadership;
  REMORA requires quiet price with moderate abnormal volume near highs.
- Regime: REMORA can be tactical; PATHFINDER needs multi-week risk appetite.

### CONTRARIAN

- Timing: PATHFINDER buys emerging strength; CONTRARIAN buys panic/dislocation.
- Edge: delayed sponsorship versus forced-selling rebound.
- Trigger: constructive base/RS turn versus fear/washout.
- Regime: PATHFINDER is silent in extreme panic; CONTRARIAN only exists in fear
  regimes and has learned to stand down in extreme panic.

## Pre-Backtest Checklist

| Section | Status | Notes |
|---|---|---|
| A. Doctrine / mandate clarity | PASS | Edge, trigger, invalidation, regime, horizon, and sleeve separation are explicit. |
| B. Scanner / council fit | PARTIAL | Baseline scanner is clear. No PATHFINDER-specific council profile exists yet. Use scanner-only baseline first. |
| C. Data integrity | PARTIAL | Historical backtest now supports a local PIT fundamentals proxy. Ownership/crowding and market-cap history remain incomplete. |
| D. Execution realism | PASS | Liquidity, stop/target, friction, and horizon assumptions are explicit. |
| E. Output expectations | PASS | First result should be scanner-only baseline with honest blocker summary. |

## Final Verification Status

Final verification completed 2026-04-22:

- tape-only `PATHFINDER_V1`: 12 signals, 20d adjusted average `-1.44%`
- PIT business-inflection proxy: 3 signals, 20d adjusted average `+7.32%`
- PIT improvement was real but too sparse for a standalone sleeve decision
- PIT survivors were concentrated in AXON/ELF and technically Voyager-like

Verdict:

- `PAUSE / SHELVE PATHFINDER FOR NOW`
- do not wire into paper or live
- do not tune thresholds or add filters in this phase
- revisit only if a genuinely better point-in-time universe/ownership/business
  inflection dataset becomes available

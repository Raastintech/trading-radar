# Alpha Discovery Board

Version: `ALPHA_DISCOVERY_V2.1`

Purpose:

- research alpha discovery layer for discretionary/manual long idea generation
- early opportunity discovery before names are fully obvious or overcrowded
- separate from sleeve approval, paper evidence, and execution

This board is explicitly not:

- a live sleeve
- a paper-evidence engine
- a governance input
- an auto-trade engine

## Role

Alpha Discovery Board answers four morning questions:

1. What is getting interesting early?
2. What is buyable now?
3. What is only a stalking candidate?
4. What is already too late or crowded?

It is a pre-sleeve discovery layer. A surfaced name may resemble `SNIPER` or
`VOYAGER`, but that resemblance is only descriptive. It is not sleeve approval
and not approved paper output.

## V2 Calibration

V1 surfaced too many already-obvious liquid leaders because:

- one blended top-N ranking let high-liquidity resets dominate the board
- liquidity/practicality rewarded mega-cap consensus names without separating
  useful liquidity from obvious crowding
- crowd penalty measured extension, but not consensus size / dominant-liquidity
  obviousness
- bucket logic labeled many leader pullbacks as buyable before separating them
  from true earlier opportunity

V2 keeps the architecture, layering, and research-only separation. It adds two
explicit tracks so the board can preserve useful liquid resets while also
surfacing earlier under-owned opportunities.

V2.1 keeps the same research architecture but tightens daily-chart truthfulness.
The main issue in V2 was that a strong or interesting name could still be
marked `Buyable Pullback` / actionable even when the daily entry was only
forming. V2.1 makes `Buyable Now` materially stricter and demotes borderline
names into more honest watch states.

## Daily Entry Validator

Alpha Discovery now uses a separate research-only module:

- `core/daily_entry_validator.py`

Role split:

- Alpha Discovery = idea generation / ranking / discovery buckets
- Daily Entry Validator = daily-chart truthfulness and entry-state validation

The validator is not:

- a sleeve
- a live gate
- paper evidence
- a trade engine

## Tracks

### Liquid Leadership Reset

- already-known liquid leaders
- useful for buyable reset / reload research
- still practical for discretionary longs

### Emerging Opportunity

- smaller, earlier, less crowded names
- improving business / tape / participation
- still tradeable, but before broad sponsorship is obvious

V2 uses one shared discovery universe with internal track classification and
balanced output caps so Liquid Leadership Reset does not swallow the board.

## Core Buckets

### Early Discovery

- improving business, tape, or sponsorship signs
- not fully obvious yet
- worth starting to stalk

### Buyable Pullback

- strong enough to matter
- constructive reset / reclaim / reload zone
- best bucket for actual manual entry review
- in V2.1 this is stricter: a name should only land here when the daily chart
  really supports a fresh buyable pullback, not just a strong or interesting
  pause

### Sponsor Confirmation

- sponsorship is becoming more obvious
- stronger confirmation, less early
- may approach sleeve-like quality later

### Too Late / Crowded

- good name, poor current entry
- extension / crowding / hot momentum makes chasing unattractive

Important: `Too Late / Crowded` does not imply a short thesis.

## Daily Entry Truthfulness

V2.1 adds a stricter daily entry-state layer so the board distinguishes:

- `Buyable Now`
- `Pullback Forming`
- `Watch Reclaim`
- `Watch Only`
- `Too Extended`
- `Broken / Avoid`

The key rule is simple:

- `entry still forming` should not receive full actionable `YES`
- strong names are not automatically good daily entries
- honest demotion is preferred over false buyability

Latest calibration note:

- `Broken / Avoid` is intentionally narrow. It should represent actual daily-structure damage, not merely an immature reset.
- Structurally intact names that are not entry-ready should usually land in `Watch Reclaim`, `Pullback Forming`, or `Watch Only`.

Primary validator feature set:

- trend context
  - EMA20
  - EMA50
  - MA200 when available
- pullback geometry
  - pullback depth from recent high
  - bars since recent peak
- swing structure
  - recent higher-low check
- entry location
  - close location in 5-bar range
  - reclaim vs EMA20
- risk / reward geometry
  - upside back to recent peak
  - downside to support / stop anchor
- damage / extension
  - upside extension
  - gap stretch / gap damage
  - too many upside days / late chase

## Data Sources

### Alpaca / local OHLCV

Primary current-state engine for:

- price
- dollar volume
- 5d / 20d returns
- volume ratio
- ATR proxy
- tape / sponsorship / entry / crowd signals

Source path:

- cache-first via `cache/universe/universe_snapshot_latest.json`

### FMP

Primary current-state enrichment for:

- market cap
- sector / industry
- business-inflection scoring inputs from quarterly fundamentals

Used through:

- `core/fmp_client.py`

### 13F

Optional slow overlay only:

- ownership-awareness / sponsorship confirmation
- low weight
- never a timing trigger

Used through:

- `core/whale_tracker.py`

### Tradier

Optional options-participation overlay only:

- call/put participation context
- low weight
- caution/bonus layer only

Current implementation reuses the legacy Tradier feed opportunistically if it
is configured. If not available, the board degrades gracefully.

## Scoring Model

Alpha Discovery V2 uses layered scoring, not one opaque blend.

### Positive blocks

- Business Inflection
- Sponsorship / Participation
- Entry Quality
- Liquidity / Practicality

### Penalty block

- Crowd Penalty

### Optional overlays

- 13F
- Tradier

Overall score is a weighted blend of the available positive blocks, minus a
crowd penalty. Missing overlays do not break the board. They only reduce the
data tier and remove that small contribution.

V2 calibration additions:

- `Liquid Leadership Reset` keeps practical liquidity and reset quality
- `Emerging Opportunity` gets a lower-base participation bonus
- `Emerging Opportunity` also gets an obvious-leader penalty for very large,
  already-dominant names
- output is balanced across the two tracks instead of taking one blended top-N

V2.1 actionability calibration additions:

- explicit daily entry state classification
- stricter `Buyable Pullback` gate
- stricter `actionable_now`
- stronger separation between:
  - strong name
  - promising reset
  - actually buyable daily entry

Volume and sponsorship remain secondary:

- they can help confidence
- they do not override a bad daily entry location

## Data Tiers

### Tier A

- Alpaca
- FMP
- 13F
- Tradier

### Tier B

- Alpaca
- FMP
- optional 13F

### Tier C

- Alpaca-led watchlist
- partial or missing enrichment

The board does not hide missing-layer reality. Each surfaced name keeps its
data-layer list and tier.

## Baseline Universe

Alpha Discovery V2 baseline universe is broad enough for early ideas but
restricted enough to avoid junk.

Current filters:

- price >= `8`
- avg 20d dollar volume >= `10M`
- current dollar volume proxy >= `5M`
- stale bars excluded
- ETF / fund instruments excluded from the surfaced board
- FMP market cap preference:
  - floor `300M`
  - ceiling `80B`
  - unknown market cap allowed only if tape liquidity is already strong

This keeps the board aimed at emerging opportunities rather than only crowded
mega-cap leaders or thin microcap speculation.

## Output Fields

Each surfaced name includes:

- ticker
- track
- bucket
- alpha score
- business inflection score
- sponsorship score
- entry quality score
- entry state
- validator state
- validator reason
- validator flags
- crowd penalty
- liquidity score
- data tier
- contributing data layers
- why now
- main risk
- sleeve resemblance, if any
- actionable now: yes/no
- action label
- if no, why not

## Review Path

Command:

```bash
.venv/bin/python research/alpha_discovery_board.py --limit 20
```

Outputs:

- stdout text report
- `cache/research/alpha_discovery_board_latest.json`
- `logs/alpha_discovery_board_latest.txt`

## Visual Dashboard

Alpha Discovery now also has a dedicated Research-mode dashboard surface.

- Location: `dashboards/gem_trader_hq.py` -> Research mode
- Separation: shown only inside the discretionary/manual research area
- Explicitly not:
  - paper evidence
  - sleeve approval
  - trade approval
  - governance input

The visual board is designed to answer, quickly:

1. What is buyable now?
2. What needs a pullback or reclaim?
3. What is early and worth stalking?
4. What is already too late / crowded?

## Daily Workflow

### Nightly canonical build

Purpose:

- stable daily-bar discovery build
- next-session watchlist preparation
- canonical research artifact for the next morning

Recommended window:

- roughly `4:20 PM` to `6:00 PM ET`

Command:

```bash
SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python research/alpha_discovery_board.py --mode nightly --limit 20
```

Nightly prewarm behavior:

- the nightly command runs an enrichment prewarm first unless `--skip-prewarm` is set
- prewarm broadens FMP profile/fundamentals cache coverage for a larger ranked candidate band
- the board then reads the warmed cache instead of depending only on thin ad hoc enrichment during scoring
- the board also performs a final-target enrichment pass for the names that actually surface, so track-balanced finalists are not left as accidental `Tier C` rows
- this is the main fix for accidental `Tier C` outcomes caused by shallow enrichment coverage

Nightly artifact:

- `cache/research/alpha_discovery_board_latest.json`
- `cache/research/alpha_discovery_enrichment_latest.json`

### Premarket actionability overlay

Purpose:

- refine yesterday's discovery board for today's open
- detect gap/stretch damage or improvement
- reclassify into:
  - `Buyable Now`
  - `Pullback Watch`
  - `Early Discovery`
  - `Too Late / Crowded`

Recommended window:

- roughly `8:15 AM` to `9:20 AM ET`

Command:

```bash
SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python research/alpha_discovery_board.py --mode premarket
```

Premarket artifact:

- `cache/research/alpha_discovery_overlay_latest.json`

## Premarket Overlay Logic

The premarket overlay is intentionally light. It does not rebuild the heavy
discovery stack. It only reevaluates surfaced names from the nightly board.

Current overlay inputs:

- latest quote vs prior close
- overnight gap size
- whether the setup stayed in range, stretched, or degraded

Typical overlay states:

- `still buyable`
- `wait for pullback`
- `stronger than expected`
- `gapped too far`
- `lost setup`
- `too crowded now`

This remains a research-assist overlay, not an intraday trade engine.

## Visual Buckets

### Nightly canonical board

- `Buyable Pullback`
- `Sponsor Confirmation`
- `Early Discovery`
- `Too Late / Crowded`

### Premarket visual board

- `Buyable Now`
- `Pullback Watch`
- `Early Discovery`
- `Too Late / Crowded`

The premarket view is intentionally stricter about current actionability.
Sparse or empty `Buyable Now` output is acceptable and often more honest.

## Tier Visibility

Each surfaced name still carries:

- `Tier A` = full signal
- `Tier B` = strong signal
- `Tier C` = tape-led watchlist

Tier quality is shown visually so lower-information names are not confused with
full-layer discovery names.

## Separation From Sleeves

Alpha Discovery Board stays separate from sleeves in code and intent:

- not in `core/strategy_registry.py`
- not wired into paper governance
- not written into paper evidence tables
- not shown as approved sleeve output

## Remaining Limitation

Alpha Discovery is now materially more usable as a morning research board, but
it is still constrained by enrichment coverage. A board that is heavy in `Tier C`
rows remains more useful for stalking and triage than for strong business-layer
alpha claims.

It is also still a current-state discovery board, not a full pattern-recognition
chart engine. V2.1 materially improves daily entry honesty, but some final
discretionary chart review is still required.

It is a research assist layer only.

## Current Limitation

Version 2 is still a current-state research board. It is not point-in-time
tested and does not make historical edge claims. Business-inflection uses
current cached quarterly fundamentals for present discovery only, and the
Emerging Opportunity track remains constrained by current snapshot coverage and
missing-enrichment reality.

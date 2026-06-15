# Quant Research Doctrine

This document defines the permanent research doctrine for every strategy and
sleeve. It governs what each strategy is for, what edge it is allowed to claim,
how data may be used, and what standard it must meet before it can control live
capital.

If this file conflicts with looser status language elsewhere, this file wins for
strategy intent. `PROJECT_NORTH_STAR.md` still defines mission, and
`CURRENT_READINESS.md` still defines current readiness.

## Core Principle

SniperTradingAI is not trying to become "another institutional trading system."

The product is for independent retail and individual equity traders who did not
previously have access to:

- institutional-quality flow and regime context
- disciplined multi-timeframe strategy selection
- structured trade thesis, invalidation, and execution guidance
- fast feedback on where the real edge is and where it is not

The edge is not "looking institutional." The edge is:

- align with institutions when they are creating durable opportunity
- exploit institutional constraints, crowding, slowness, and forced behavior
- use retail agility where a smaller account can enter, exit, and adapt faster

If a strategy does not improve real retail edge, it is noise.

## Permanent Quant Research Rules

Future strategy work must follow these rules:

1. **Edge source must be explicit.** Every strategy or sleeve must clearly state
   what market inefficiency it exploits, why that edge should exist, and why the
   sleeve exists separately from neighboring sleeves.
2. **Trigger must be explicit.** Every strategy needs a precise entry trigger.
   "Looks good," "feels weak," and generic descriptions like "overbought" are
   not backtest-ready triggers.
3. **Invalidation must be explicit.** Every trade must have a clear reason it is
   wrong and a clear invalidation condition.
4. **Regime fit must be explicit.** Each sleeve must define when it should be
   active, when it should be selective, and when it should be silent.
5. **Scanner and council must align.** Scanner logic and council logic must not
   fight each other. If a sleeve needs a sleeve-specific council profile, state
   that before deep backtesting.
6. **Data must be treated by quality and timing.** No lookahead is allowed. No
   silent fallback contamination is allowed. Delayed data must be treated as
   delayed. Slow-changing data should be cached and prefetched. Backtests should
   read local cached data whenever practical.
7. **More data is not automatically more edge.** Correlated features are
   correlated evidence, not independent confirmation. Do not add filters unless
   evidence shows they separate winners from losers in the sleeve being tested.
8. **Do not optimize for win rate alone.** Stable edge matters more than prettier
   win rate: adjusted expectancy, acceptable stop-hit rate, enough sample size,
   stability across years and regimes, and paper viability.
9. **One structural hypothesis at a time.** When a strategy is weak, test one
   clean idea at a time. Do not stack several new filters into one pass.
10. **Stop tuning when the edge ceiling is likely reached.** If disciplined
    passes fail to improve the edge materially, say so clearly and stop tuning a
    modest edge into a fake one.

## Required Pre-Backtest Gate

Before any deep backtest, the strategy must pass the detailed checklist in
`PRE_BACKTEST_READINESS_CHECKLIST.md`. At minimum, the research owner must
confirm:

### A. Doctrine / Mandate

- explicit edge source
- explicit trigger
- explicit invalidation
- explicit regime fit
- explicit holding horizon
- clear separation from neighboring strategies

### B. Scanner / Council Fit

- scanner logic and council logic do not conflict
- no direction drift
- no timeframe drift
- sleeve-specific council logic is documented if needed

### C. Data Integrity

- no lookahead
- event timing integrity confirmed
- endpoints and paths valid
- no silent missing-field contamination
- no hidden live-provider calls in tight backtest loops

### D. Execution Realism

- friction assumptions defined
- stop, target, and invalidation defined
- hold horizon defined
- sample size standards stated honestly
- backtest and research code aligned with production logic as closely as practical

### E. Output Expectations

- scanner-only result if useful
- scanner plus council result if useful
- blocker summary
- honest go/no-go verdict

If a strategy fails this gate, do not launch a full deep backtest. Fix doctrine,
data integrity, or scanner/council alignment first.

## Data Discipline

The system already has broad data coverage. The default answer is not "add more
indicators."

Before adding a filter or feature, answer:

1. Is this feature actually independent information?
2. Does it separate winners from losers in the current sleeve?
3. Is it an entry-quality feature, a regime feature, or a post-entry management
   feature?
4. Is the failure mode happening at entry, or after entry?
5. Is this a true structural improvement, or just historical fitting?

If evidence does not support the filter, do not add it.

## Readiness Verdicts

Use this hierarchy when judging any strategy or sleeve:

### Capital-Ready

Only if the sleeve has strong adjusted expectancy, a healthy stop-hit rate,
enough signals, stability across years, and paper validation.

### Paper-Ready

Use when the edge is positive but modest, the logic is coherent, and the
backtest is good enough to justify forward observation, but not yet strong
enough for capital.

### Research-Only

Use when the doctrine is coherent but the edge is weak or inconclusive, and one
or two more structural passes may still be justified.

### Stop / Doctrine Review

Use when repeated disciplined passes do not improve the edge, or the strategy no
longer appears to have a real separable edge.

## Required Future Strategy Output

For the next strategy and every strategy after that, structure research output
like this:

1. **Strategy audit**
   - mandate
   - edge source
   - trigger
   - invalidation
   - regime fit
   - council fit
2. **Pre-backtest checklist result**
   - pass/fail by section
3. **Backtest design**
   - universe
   - window
   - friction
   - stop/target
   - horizon
   - data path
4. **Results**
   - signal count
   - win rate
   - average raw / adjusted return
   - stop-hit rate
   - expectancy if available
   - year/regime breakdown if useful
5. **Diagnosis**
   - what is actually working
   - what is actually failing
   - whether the problem is entry, regime, management, or doctrine
6. **One next hypothesis only**
   - if improvement is justified, propose one clean next pass
   - if improvement is not justified, say stop
7. **Verdict**
   - capital-ready
   - paper-ready
   - research-only
   - stop / doctrine review

Before starting the next strategy backtest, first confirm which strategy is next,
whether its doctrine is already clean enough, and whether any unresolved identity
issue remains. If the next strategy's identity is unresolved, stop and fix
doctrine first. If it is doctrinally clean, begin with audit, checklist,
backtest design, then validation.

## Portfolio Shape

This is a multi-sleeve desk. Each sleeve must solve a different market problem.

The goal is not to maximize the number of strategies.

The goal is to maintain a compact set of strategies that together:

- cover different market regimes
- operate on different time horizons
- exploit different market inefficiencies
- fail independently enough to improve total portfolio resilience

Every strategy must have:

1. a clear mandate
2. a distinct edge claim
3. a defined failure mode
4. a strategy-specific readiness standard
5. a reason to exist that is not duplicated better elsewhere in the stack

## Future Sleeve: PATHFINDER

PATHFINDER is a future LONG-only research sleeve, not part of the current active
paper set.

Subtitle: **Early Sponsorship / Emerging Leader**

Baseline tag:

- `PATHFINDER_V1`

Mandate:

- exploit institutional slowness and delayed recognition in smaller but liquid
  companies where business improvement and early tape leadership are becoming
  real before broad sponsorship is obvious

PATHFINDER is separate from:

- VOYAGER: not mature long-horizon institutional accumulation
- SNIPER: not confirmed breakout-chase behavior
- REMORA: not short-horizon quiet-flow near highs
- CONTRARIAN: not panic/dislocation rebound
- SHORT: not short-direction logic

Research status:

- doctrine/spec: `docs/strategy/PATHFINDER_EARLY_SPONSORSHIP_SPEC.md`
- scorecard: `docs/scorecards/pathfinder_scorecard.md`
- scanner baseline: `strategies/pathfinder.py`
- backtest baseline: `research/backtests/pathfinder_backtest.py`

Data integrity warning:

- production scanner may use cached current FMP fundamentals
- historical paper-readiness requires point-in-time local fundamentals
- current fundamentals must not be used as historical truth

## Strategy Mandates

### `VOYAGER`

Mandate:
- capture long-duration accumulation before the major run is widely recognized

Retail edge claim:
- piggyback on real institutional accumulation while using retail flexibility to
  enter earlier and size more precisely

Required properties:
- quality business
- durable accumulation / stabilization
- favorable long-horizon structure
- acceptable entry timing, not late extension
- clear invalidation and staged risk management

Failure mode to avoid:
- buying strong names after they already ran
- confusing "good company" with "good entry"

What success looks like:
- lower-frequency, higher-conviction names
- strong thesis support
- asymmetric long-duration upside

### `SNIPER`

Mandate:
- confirm institutional breakout participation and enter on the breakout bar itself

Retail edge claim:
- act at end-of-day when a breakout is confirmed by volume, before slower capital
  validates the move across multiple sessions

Required properties:
- close above prior 20-day high (first bar above resistance, not continuation)
- volume ≥ 1.4× average on the breakout bar (institutional participation signature)
- tight consolidation base in the prior 10 bars (< 1.5× ATR range)
- above 50-day MA (uptrend context — not a relief bounce)
- positive RS vs SPY over 10 days
- VIX < 28 (panic regimes belong to CONTRARIAN)
- R:R ≥ 2.5, stop = entry − 1× ATR

Hold horizon: 1–30 trading days.

Distinct from VOYAGER:
- VOYAGER enters the accumulation base weeks before the breakout is visible.
  SNIPER enters on the breakout bar. VOYAGER is pre-breakout; SNIPER is the breakout.

Distinct from REMORA:
- REMORA detects quiet institutional volume WITHOUT a price breakout.
  SNIPER requires the breakout to be confirmed. If there is no new 20-day high, it is
  not a SNIPER signal.

Distinct from CONTRARIAN:
- CONTRARIAN fades panic and oversold conditions. SNIPER confirms momentum and strength.
  These are near-opposite setups. VIX ≥ 28 suppresses SNIPER; VIX ≥ 28 is when
  CONTRARIAN activates.

Failure mode to avoid:
- chasing breakouts without volume confirmation (false breakouts)
- entering continuation bars (price already above resistance for multiple days)
- trading in panic regimes (VIX ≥ 28) — those belong to CONTRARIAN

What success looks like:
- selective, fast entries on the breakout bar only
- high confirmation bar (volume + RS + consolidation + MA)
- false breakout rate < 40% (exits via stop within 5 bars)
- limited tolerance for mediocre geometry (R:R < 2.5 rejected)

### `REMORA`

Mandate:
- detect short-horizon stealth institutional accumulation / quiet flow before it
  becomes visible momentum

Retail edge claim:
- identify moderate abnormal volume near highs while price remains quiet, then
  use retail speed to enter before the footprint becomes a crowded breakout

Required properties:
- quiet price action, not a visible breakout
- moderate unusual volume, not explosive catalyst volume
- institutional-grade liquidity and tight spread
- near-high strength context
- enough liquidity to enter and exit cleanly
- short holding horizon, normally intraday to ~5 trading days

Failure mode to avoid:
- treating random volume as institutional accumulation
- drifting into SNIPER-style breakout continuation
- drifting into catalyst/news burst logic without a separate mandate
- overfitting noisy names with weak evidence

What success looks like:
- rare, high-selectivity quiet-flow LONG entries
- measurable short-horizon continuation after stealth volume signatures
- clear separation from VOYAGER's long-horizon accumulation and SNIPER's breakout bar

### `SHORT`

Mandate:
- capture deterioration before the market fully reprices bad fundamentals and
  broken structure

This is a multi-sleeve family. Full SHORT doctrine lives in
`docs/strategy/SHORT_DOCTRINE.md`. That document governs what SHORT is permitted
to do, which sleeves exist, and what is permanently off-limits.

Retail edge claim:
- identify the early part of real downside repricing while avoiding the crowded,
  late, or structurally dangerous short

Current sleeves:
- **Sleeve A — Event Continuation Short** (production, pending paper gate): enter
  after verified earnings gap-down ≥ 3% with institutional volume and continuation
  confirmation. Hold 5–10 days. Code: `strategies/short_sleeve.py`.
- **Sleeve B — Broken Leader / Structural Deterioration Short** (design phase): enter
  after a prior leader breaks structural support and a failed rally confirms supply
  overhead. Hold 15–30 days. Code: to be built.

Failure modes to avoid:
- shorting because something is "expensive" without behavioral deterioration
- shorting already washed-out names (Sleeve A) or names without prior leadership (Sleeve B)
- fighting broad market strength with marginal setups
- short logic leaking into Voyager or any other long strategy

What success looks like:
- lower-frequency but cleaner bearish entries
- clear edge claim per sleeve, not a generic "something is falling"
- deterioration first, repricing second

### `CONTRARIAN`

Mandate:
- exploit panic, forced selling, and volatility overshoot when mean reversion
  and stabilization conditions are present

Retail edge claim:
- step in selectively where larger players are forced, constrained, or unable
  to act quickly enough

Required properties:
- real washout context
- oversold and fear confirmation
- evidence of stabilization, not blind knife-catching

Failure mode to avoid:
- fading normal weakness
- entering before panic is complete

What success looks like:
- rare but high-quality dislocation entries
- strong regime dependence

## Direction Mandate

**SHORT is the sole short-direction strategy family in this platform.**

- All other strategies (VOYAGER, SNIPER, REMORA, CONTRARIAN) are LONG-only or regime-neutral.
- VOYAGER must never contain short logic. Its mandate is LONG-only, and this must be
  preserved unconditionally through every rebuild, extension, or backtest pass.
- All short-direction logic lives under SHORT's namespace — not distributed across other strategies.
- If a future research pass identifies a short opportunity outside SHORT's current sleeves,
  the correct response is to add a sleeve to SHORT, not to add short logic to a long strategy.
- SHORT is a multi-sleeve family. See `docs/strategy/SHORT_DOCTRINE.md` for the complete
  sleeve specifications, council profile requirements, and anti-drift rules.

## Cross-Strategy Rules

No strategy earns live capital merely by existing in the stack.

Every strategy must prove:

- enough forward sample size
- positive expectancy
- acceptable drawdown
- coherent fill quality
- auditability from decision to outcome

No strategy should be promoted because:

- it sounds sophisticated
- it increases activity
- it gives the dashboard more to display
- it resembles what institutions use

## Retail-Edge Test

A strategy or feature belongs only if it improves at least one of these:

1. better alignment with institutional strength
2. better exploitation of institutional inefficiency
3. better use of retail agility
4. better selectivity and timing
5. better trader decision quality and discipline
6. better realized risk-adjusted outcomes

If the improvement is unclear, the change does not ship.

## Operating Implication

The right long-term shape of the platform is:

- one flagship long-duration sleeve executed with excellence
- supporting tactical sleeves that are each sharp, narrow, and proven
- shared infrastructure for risk, attribution, and audit
- trader-facing output that is institutional in discipline and retail in
  usability

This is the niche:

- institutional-grade hedge and context for self-directed retail traders
- plus exploitation of the inefficiencies and constraints that institutions
  themselves create

If the system stops serving that niche, it is off-mission.

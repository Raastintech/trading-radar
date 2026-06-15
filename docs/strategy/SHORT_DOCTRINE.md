# SHORT Family Doctrine

This document defines the complete SHORT strategy family: its mandate, permitted sleeves,
what each sleeve is allowed to do, and what is permanently off-limits.

It supersedes any conflicting language in other documents when the topic is short-direction
logic. `STRATEGY_DOCTRINE.md` still governs platform-wide rules and portfolio shape.
`CURRENT_READINESS.md` still governs live readiness status.

---

## 1. Direction Mandate (Non-Negotiable)

**SHORT is the sole short-direction strategy family in this platform.**

- VOYAGER, SNIPER, REMORA, and CONTRARIAN are LONG-only or regime-neutral. None may
  contain short-direction entry logic under any circumstances — including backtests,
  research branches, or "optional" flags.

- All short-direction logic lives under SHORT's namespace. If a research pass identifies
  a short opportunity outside SHORT's current scope, the correct response is to extend
  SHORT (add a sleeve), not to add short logic to a long strategy.

- VOYAGER in particular: its mandate is long-duration accumulation. Any signal, filter,
  or score that could produce a directional short from VOYAGER represents a doctrine
  violation and must be removed.

---

## 2. What SHORT Is For

SHORT captures downside repricing when there is real, evidence-based edge:

- confirmed bad-news event continuation (Sleeve A)
- broken leadership / smart-money exit / structural distribution (Sleeve B)
- not because something is "high" or "expensive" in isolation
- not because sentiment is negative without behavioral confirmation
- not because a prior bull run has ended without structural evidence of distribution

SHORT is a precision instrument, not a hedge-everything book. Low frequency is acceptable
and expected. A short that does not have a clear, confirmable edge should not fire.

---

## 3. What SHORT Is Not For

Short logic does not belong in this platform when:

- the only basis is valuation (high PE, high PS, or "too expensive")
- the only basis is momentum exhaustion without behavioral confirmation of distribution
- the signal is replicating what retail already knows and is crowded into
- the setup requires calling the exact top of a strong trend
- the name has insufficient liquidity (ADV < $50M) to exit cleanly
- there is no clear invalidation level
- the borrow cost or availability is not considered

A short that merely mirrors what financial Twitter is already positioning for is not
an edge. It is joining the back of a crowded trade at maximum squeeze risk.

---

## 4. Current Sleeve Structure

### Sleeve A — Event Continuation Short

**Status:** Production-ready (pending paper phase gate)
**Code:** `strategies/short_sleeve.py`
**Scorecard:** `docs/scorecards/short_sleeve_scorecard.md`

**Identity:** Tactical short triggered by a verified earnings disappointment that
produces a gap-down ≥ 3% on high volume, confirmed by continuation of the sell-off
within 3 sessions.

**Edge claim:** Post-earnings repricing is incomplete on the event day. When institutional
selling is confirmed (volume ≥ 1.5×) and price has not recovered above the event bar low
within 3 sessions, there is residual downside before the broader market fully adjusts.

**Hold horizon:** 5–10 trading days

**What it requires:**
- Verified FMP earnings event date
- Gap ≤ −3% on event bar open vs prior close
- Volume ≥ 1.5× 20-day average on event bar
- Continuation: close < event bar low within 3 sessions
- R:R ≥ 2.0, stop = entry + 1.5× ATR

**What it does NOT need:** Prior trend context, fundamental quality, relative strength,
or duration beyond the immediate event window.

**Known limitation:** AMC (after-market-close) earnings events are systematically missed
because FMP records the announcement date, not the reaction date. Approximately 50% of
earnings events are AMC. This suppresses signal count but is not a lookahead issue.
Fix documented; not yet implemented.

---

### Sleeve B — Broken Leader / Structural Deterioration Short

**Status:** Backtest phase — conditions calibrated, extended backtest required
**Code:** `strategies/short_broken_leader.py` (to be created after backtest validates)
**Scorecard:** `docs/scorecards/short_sleeve_scorecard.md` (Sleeve B section)

**Identity:** Medium-duration short triggered by evidence that a prior market leader
has broken structural support with institutional distribution signatures, confirmed
by a failed rally attempt.

**Edge claim:** Former leaders that break structural support are repriced gradually,
not instantly. Retail "buy the dip" behavior creates a series of failed rally attempts
into supply. The edge is entering after the first failed reclaim of a key level, when
the pattern of distribution is confirmable and invalidation is specific.

**Hold horizon:** 15–30 trading days

**What it requires (calibrated v2 — as of 2026-04-19):**
1. Prior leadership: stock was a leader (≥ +40% in any trailing 18-month window) —
   this filters for names that were institutionally held, not random losers
2. Structural break: price < 50-day MA AND lower high established after prior peak
3. Failed rally trigger: price bounced ≥ 4% from a local low within 12 bars, then
   today's close is below that bounce start — the specific entry event.
   (Note: the MA-approach requirement was removed; not needed after violent single-event
   gaps where the 50d MA stays far above price for months.)
4. Relative weakness: ticker underperforming SPY by ≥ −6% over 20 days
5. Distribution confirmation: ≥ 2 of last 10 sessions with volume > 1.3× 20d avg
   on a down close
6. Safety gates: price ≥ $15, not within 5 days of earnings
7. Exhaustion guard: RSI at entry 33–55 (not already oversold), current drawdown
   from recent high **−20% to −35%** (tightened from −55%; deep-breakdown signals
   have 32% WR and are negative-edge)
8. SPY regime gate: SPY close must be **below** its 50-day MA on signal date. Sleeve B
   structural shorts fail in confirmed broad uptrends (2024 bull: 36% WR, −4.5% avg).
9. China ADR exclusion: BABA, JD, and similar names excluded from universe. Geopolitical
   and regulatory noise overrides structural deterioration logic.

**Entry geometry:**
- Entry: close on signal date (bar where failed rally is confirmed)
- Stop: failed_bounce_peak × 1.015 (1.5% above the specific level that failed)
  (Not 50d MA — MA-based stops produce absurdly wide risk after violent event gaps)
- Target: entry − 2× risk (R:R = 2.0 enforced)
- Max risk: ≤ 15% of entry price (rejects wide-stop geometry)
- R:R enforced ≥ 2.0

**What it explicitly avoids:**
- Shorting names with positive 20-day RS vs SPY
- Shorting names approaching or making new highs
- Shorting on valuation without behavioral deterioration
- Shorting into oversold conditions (RSI < 33)
- Shorting near upcoming earnings (Sleeve A territory)
- Shorting in SPY uptrends (SPY > 50d MA)
- Shorting China ADRs (geopolitical regime unpredictability)
- Shorting illiquid or hard-to-borrow names without explicit borrow verification
- Shorting any name that Voyager would classify as "quality accumulation" — the two
  should never fire simultaneously on the same ticker

**Backtest history:**
- v1 (pre-calibration, 2021-2024): 99 signals, 44.4% WR, −0.6% raw → negative after friction
- v2 (calibrated, 2021-2024): 14 signals → statistically insufficient
- v3 (calibrated, 2018-2024): 16 signals, 25.0% WR, −5.9% raw → **not paper-ready**

**Current verdict (2026-04-20): RESEARCH-ONLY.**

Two structural blockers identified:

1. **Stop geometry too tight** — `failed_bounce_peak × 1.015` produces 56% stop-hit rate.
   After a failed rally, stocks commonly probe 1–3% above the prior bounce peak before
   resuming downside. The 1.5% buffer is insufficient for this volatility class. Next
   pass: widen to `× 1.04–1.05`, reduce R:R minimum to 1.5×.

2. **Leadership filter too loose** — ≥40% in any 18-month window admits all 2020-2021
   mega-caps (AAPL, AMZN, NVDA, MSFT, GOOGL), producing 37.5% control-ticker rate (target
   < 10%). These names are not structurally broken leaders — they had temporary drawdowns
   and recovered. Next pass: require leadership peak age ≥ 2 years AND 52-week high not
   within last 6 months.

Neither fix requires a fundamental redesign of the strategy identity. Both require a clean
v4 calibration pass before the strategy can be reconsidered for paper phase.

See `docs/scorecards/short_sleeve_scorecard.md §Sleeve B` for full results and analysis.

---

## 5. Hype / Exhaustion Short — Explicitly Deferred

A third short category — shorting parabolic names at apparent exhaustion without
a structural break or event confirmation — is **not part of the current SHORT family.**

Why deferred:
- Entry is ambiguous: "when is something too high?" has no clean answer
- Short squeeze risk is highest in this category (high short interest, retail crowding)
- Retail timing is consistently poor at calling exhaustion tops
- This category requires borrow-availability monitoring that does not yet exist here

If this sleeve is ever added (Sleeve C), it requires:
- Confirmed borrow availability at entry (not assumed)
- Short interest monitoring (avoid already-crowded shorts)
- A squeeze risk score as a hard gate
- Demonstrated positive expectancy in Sleeve A and B first

Until Sleeve A and Sleeve B are both paper-validated, Sleeve C does not exist in this
platform. Calling something "a hype short" is not a doctrine category. It is not added
to this document until the above conditions are met.

---

## 6. How Sleeves Differ

| Dimension | Sleeve A (Event Continuation) | Sleeve B (Broken Leader) |
|-----------|-------------------------------|--------------------------|
| Trigger | Earnings event (FMP verified) | Failed rally after structural break |
| Prior condition | Any stock with earnings | Must have been a leader (≥ +40% prior) |
| Hold horizon | 5–10 days | 15–30 days |
| Volume requirement | High vol on event bar | Distribution pattern over ≥ 2 sessions |
| Relative strength | Not required | Required: underperforming SPY |
| Earnings timing | Event is the trigger | Block near upcoming earnings |
| Council profile | Event-heavy, short timing | Regime-heavy, RS-focused |
| Failure mode | AMC timing gap; mean reversion | Failed breakdown; strong market lifts all |
| Primary edge | Speed of post-event repricing | Lag between distribution and recognition |

The two sleeves fail independently. Sleeve A needs bad earnings events. Sleeve B needs
prior leaders to exist and break. In a market with no meaningful earnings disappointments
(post-recovery bull), Sleeve A produces nothing while Sleeve B may still fire on names
rolling over from prior leadership. In a regime with sudden idiosyncratic crashes (single
stocks), Sleeve A fires while Sleeve B may not.

---

## 7. Council Profile Requirements

The VetoCouncil (`core/veto_council.py`) currently uses one generic weight set for all
strategies. This is insufficient for the SHORT family, where the two sleeves require
different scoring emphasis.

**Sleeve A council profile (should be configured when council profiles are implemented):**
- EarningsAgent weight: HIGH — the earnings event is the entire trigger
- RegimeAgent: MODERATE — VIX gate is relevant but not the differentiator
- MomentumAgent: MODERATE — post-gap negative momentum expected; confirms, doesn't discover
- FlowAgent: LOW — intraday volume deceleration is noise in an event-driven context

**Sleeve B council profile (should be configured when Sleeve B is implemented):**
- RelativeStrengthAgent: HIGH — RS deterioration vs SPY is a core gate (agent may need building)
- RegimeAgent: HIGH — Sleeve B works in declining/bear regimes; dangerous in strong bull
- EarningsAgent: MODERATE (as a BLOCK near upcoming earnings, not a positive trigger)
- FlowAgent: MODERATE — distribution day count is genuinely relevant for Sleeve B
- MomentumAgent: LOW (the negative momentum IS the signal, not the validator)

Using Sleeve A's profile to evaluate Sleeve B signals would over-weight the earnings
gate and under-weight the regime gate, potentially filtering good Sleeve B setups.
Using Sleeve B's profile for Sleeve A would require RS deterioration that Sleeve A
signals don't need. **Both sleeves need separate council profiles when profiles are built.**

Until sleeve-specific council profiles exist, any council scoring for SHORT signals
should be treated as approximate, not definitive.

---

## 8. Short Anti-Drift Rules

The following are permanently prohibited and should be treated as doctrine violations:

1. **Voyager short logic**: Any signal, score adjustment, filter, or flag in any VOYAGER
   file that could result in a short-direction position is prohibited. VOYAGER scans for
   accumulation. Distribution is Sleeve B territory and lives in SHORT only.

2. **Cross-strategy short leakage**: No long strategy may contain a "direction" field
   that ever returns "SHORT". Direction is set at strategy namespace level, not signal level.

3. **Implicit shorts via score suppression**: A long strategy that scores a name at 0
   to "avoid" it is not the same as a short. If a name belongs to SHORT, it belongs to
   SHORT's scanner, not to a long scanner's suppression logic.

4. **Hype framing without structure**: Describing a signal as "this name is overvalued
   and likely to fall" without structural evidence of distribution is not a valid SHORT
   signal. Valuation is not a timing tool.

---

## 9. Promotion Standards

### Sleeve A
Current paper-cycle success criteria (from `short_sleeve_scorecard.md`):
- ≥ 30 paper signals collected
- 10d win rate ≥ 50%
- Avg adjusted return (10d) > 0%
- Stop hit rate < 35%
- No single ticker > 30% of all signals

### Sleeve B (current status: research-only, two specific blockers)

Completed:
- [x] Manual validation on META/SNAP/PYPL confirmed conditions fire at logical points
- [x] Backtest script written: `research/backtests/short_b_backtest.py`
- [x] v1 backtest run (2021-2024, 99 signals, pre-calibration)
- [x] Calibration applied: drawdown −20%/−35%, SPY regime gate, China ADR exclusion
- [x] v2 calibrated run (2021-2024): n=14, inconclusive
- [x] v3 calibrated run (2018-2024): n=16, 25% WR, −5.9% raw → research-only verdict
- [x] Pre-backtest checklist applied: both stop conditions triggered, blockers documented
- [x] Council profile documented (Sleeve B section of this file)

Before Sleeve B can return to active research:
- Stop buffer: change to `failed_bounce_peak × 1.04–1.05`, R:R minimum to 1.5×
- Leadership filter: require peak age ≥ 2 years AND 52-week high not within 6 months
- Run v4 calibration with these two changes only — no other redesign
- v4 must achieve: n ≥ 40, WR ≥ 50%, stop hit rate < 30%, control rate < 10%

Before Sleeve B paper phase (after v4 passes):
- Win rate ≥ 50% at primary hold horizon (20d)
- Avg adj return (20d) > 0%
- At least 3 different sectors represented
- Code in `strategies/short_broken_leader.py` not written until the above are met

---

## 10. Change Control

Any modification to short-direction logic — in any file — requires updating this document
if the change affects:
- which sleeve handles which setup
- what conditions are required per sleeve
- council profile weights
- what is explicitly out of scope

Changes that add short logic to non-SHORT strategy files require explicit doctrine
approval and a documented reason. The default answer to "can we add a quick short filter
to [other strategy]?" is: no. The answer is: add a sleeve to SHORT.

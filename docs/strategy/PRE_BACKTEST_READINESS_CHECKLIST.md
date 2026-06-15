# Pre-Backtest Readiness Checklist

This checklist must be completed before writing or running a backtest for any
strategy or sleeve. Its purpose is to force the decisions that make a backtest
interpretable — before data is looked at, not after.

This checklist implements the permanent Quant Research Doctrine in
`STRATEGY_DOCTRINE.md`. If a strategy fails this checklist, do not launch a full
deep backtest yet; fix doctrine, scanner/council alignment, data integrity, or
execution assumptions first.

The worst backtests are ones where the author discovers the edge after seeing
the results and reverse-engineers conditions to match. The second-worst are ones
where critical decisions (universe, stop geometry, friction model) are left
ambiguous and resolved inconsistently across runs.

This checklist is the antidote to both. Fill it out. Get agreement. Then build.

---

## Platform-Level Rules (Non-Negotiable)

Before any strategy-specific work, confirm:

- [ ] **Direction mandate**: Is this strategy family the correct owner of this
  signal direction? (SHORT is the sole short-direction family. A new short
  signal belongs in SHORT, not in a long strategy's filter.)
- [ ] **Doctrine alignment**: Does this sleeve have a clear mandate in
  `STRATEGY_DOCTRINE.md`? If not, does it need one before proceeding?
- [ ] **Not duplicating another sleeve**: Is this sleeve solving a distinct
  market problem, or is it an incremental variation of a sleeve that already
  exists?
- [ ] **Output format accepted**: Will this research pass report strategy audit,
  checklist result, backtest design, results, diagnosis, one next hypothesis, and
  verdict in the format required by `STRATEGY_DOCTRINE.md`?

---

## 1. Mandate Clarity

Define the edge in one sentence. If you cannot do this, the backtest is premature.

> *Example: "Captures post-earnings gap-down repricing when institutional volume
> confirms the sell-off and the name fails to recover within 3 sessions."*

- [ ] One-sentence edge statement written and agreed upon
- [ ] Trigger defined: what specific, observable event or condition starts the clock?
- [ ] Invalidation defined: what specific, observable condition means the thesis is wrong?
- [ ] Hold horizon defined: minimum, target, and maximum hold period

---

## 2. Scanner / Signal Conditions

Define every gate before looking at any output.

- [ ] Each gate is stated as a testable, unambiguous boolean condition
- [ ] Every gate has a documented rationale (why does this filter add value?)
- [ ] The trigger (timing) gate is separated from screening (structural) gates
- [ ] No gate was added after seeing the backtest results ("result-fitted")
- [ ] All threshold values (e.g., RSI 33–55, gap ≤ −3%) are documented with rationale

**Stop and target geometry:**
- [ ] Entry price is defined (close? open? signal-date close?)
- [ ] Stop placement is defined with no ambiguity (which level, what buffer)
- [ ] Target is defined (2× risk? structural support? time-based exit?)
- [ ] R:R minimum is enforced as a hard gate

---

## 3. Council Alignment

Before writing a backtest, the expected council interaction should be stated.

- [ ] Which council agents are expected to confirm this signal type?
- [ ] Which agents are expected to block or reduce score for this signal type?
- [ ] Is a sleeve-specific council profile needed, or does the generic profile work?
- [ ] If a custom profile is needed: document the intended weights (even if approximate)

**Reference:** `docs/strategy/SHORT_DOCTRINE.md §7` for SHORT-specific council
profile guidance. Council profiles are documented per sleeve even before the
VetoCouncil supports per-strategy profiles in code.

---

## 4. Data Integrity

- [ ] Price data source confirmed (Alpaca SIP, adjustment type: "all")
- [ ] Price lookback period sufficient for all indicators used
  - Moving averages: need `period` bars before the first valid signal bar
  - Leadership check: need `lookback_period` bars before the backtest start
  - RS calculation: need the RS lookback period before the first signal bar
- [ ] No forward data leakage: signal computed only from bars up to and including
  signal date — not from any bar after signal date
- [ ] Volume averages computed from bars strictly before the volume event being measured
- [ ] Earnings calendar source confirmed (FMP `/stable/earnings-calendar`)
- [ ] API rate limits considered (FMP: 750 RPM hard limit, no confirmed monthly call cap;
  earnings calendar returns 402 for pre-2021 dates on current plan — historical earnings
  before 2021 require a higher tier; use monthly event blocks, not quarterly)

**Anti-lookahead self-audit questions:**
1. At bar `i`, does any gate use price, volume, or event data from bar `i+1` or later?
2. Does the stop/target computation use the same-bar close as the entry price?
3. Are forward returns measured starting strictly from the bar after signal date?

---

## 5. Universe Definition

- [ ] Universe defined before running any data pulls
- [ ] Rationale for universe inclusion documented (not "added because it worked")
- [ ] Control tickers included: names that should rarely or never signal
- [ ] Universe size is appropriate for the expected signal rate
  - Target: ≥ 40 signals for a statistically meaningful result
  - If expected signal rate is < 0.2%, universe × time window must be large enough
- [ ] Universe survives a "would Voyager like this name?" test for LONG strategies
  and a "would this name ever have been a leader?" test for Sleeve B shorts

**China ADR note:** China ADRs (BABA, JD, etc.) behave differently from US-listed
broken leaders due to geopolitical and regulatory risk. Exclude from Sleeve B
universe or validate separately. See scorecard for documented WR history.

---

## 6. Friction Model

Every backtest must use the same friction model unless there is a documented
reason to deviate.

| Component | Sleeve A | Sleeve B | Default if Unknown |
|-----------|----------|----------|-------------------|
| Commission (each way) | 0.05% | 0.05% | 0.05% |
| Slippage (each way) | 0.05% | 0.10% | 0.10% (conservative) |
| Borrow (annualized) | 1.0% | 2.0% | 2.0% (conservative) |

- [ ] Commission rate documented and applied
- [ ] Slippage rate documented and applied (higher for volatile / small-cap names)
- [ ] Borrow cost documented and applied (short-only strategies)
- [ ] Total friction expressed as % per hold period (for sanity check)

Friction reality check: at 0.10% slippage + 0.05% commission each way + 2% annual
borrow, a 20-day short trade has ~0.46% friction. A strategy with +0.5% raw avg
return has no edge after friction. Adjust hold period or return expectations accordingly.

---

## 7. Regime Context

- [ ] Which market regimes is this strategy expected to outperform in?
- [ ] Which market regimes is this strategy expected to be quiet or inactive in?
- [ ] Is a regime gate required (e.g., SPY below 50d MA for Sleeve B)?
- [ ] If a regime gate is required, it must be tested as a separate variable —
  not added after the fact because results were bad in one year
- [ ] The backtest window includes at least one bear and one bull market period
  (2021-2024 contains one meaningful bear period: 2022)

---

## 8. Output Expectations

Before running the backtest, state what results would make you confident vs. concerned.

- [ ] **Minimum signal count for validity:** ≥ 40 (state in checklist, not discovered after)
- [ ] **Win rate threshold to proceed to paper:** stated (e.g., ≥ 50% at primary horizon)
- [ ] **Expectancy threshold to proceed to paper:** stated (e.g., avg adj return > 0%)
- [ ] **Stop hit rate concern threshold:** stated (e.g., > 35% is a problem)
- [ ] **Control behavior expectation:** stated (e.g., controls should produce < 10% of signals)
- [ ] **Sector concentration concern:** stated (e.g., one sector > 50% of signals = investigate)

---

## 9. Stop Conditions

Define what results would cause you to **not proceed** and rethink the sleeve design:

- [ ] If n < 20 signals: investigate whether conditions are too tight or universe is too small
- [ ] If controls produce > 20% of signals: leadership/quality filter is insufficient
- [ ] If stop hit rate > 50%: stop placement geometry is wrong; don't filter after the fact
- [ ] If single name > 30% of signals: concentration issue; investigate or cap
- [ ] If result dramatically improves when removing a specific year: regime dependency not gated

---

## 10. Change Control

After the backtest runs:

- [ ] Any condition change based on backtest results is documented as "post-backtest calibration"
- [ ] Post-backtest changes are re-run to verify they don't overfit (out-of-sample check if possible)
- [ ] Results written to the strategy scorecard before any paper phase begins
- [ ] Significant design changes (new gates, changed geometry) require updating SHORT_DOCTRINE.md

---

## Checklist Sign-Off

Complete this section when all items above are checked:

```
Strategy / Sleeve:   ___________________________
Backtest script:     ___________________________
Universe size:       ___________________________  tickers
Time window:         ___________________________ to ___________________________
Primary hold period: ___________________________
One-sentence edge:   ___________________________
Min signals target:  ___________________________
Regime assumption:   ___________________________
FMP budget verified: yes / no (750 RPM limit; no daily/monthly call cap enforced)
Checklist completed: ___________________________  (date)
Reviewed by:         ___________________________
```

---

*This file is part of the platform's research discipline layer.
Reference: `docs/strategy/STRATEGY_DOCTRINE.md`, `docs/strategy/SHORT_DOCTRINE.md`*

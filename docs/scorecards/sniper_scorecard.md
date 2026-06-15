---
strategy: SNP — Sniper
book: Long Tactical (Book B)
code_path: strategies/sniper.py
owner: TBD
last_updated: 2026-05-04
validation_order: 2 (after SHORT Sleeve A paper phase begins)
---

# Sniper Scorecard

**SNIPER is LONG-only. No short logic belongs here.**
Full platform doctrine: `docs/strategy/STRATEGY_DOCTRINE.md`

> **Phase 10 evidence note (2026-05-04).** The original v6 backtest reported
> WR 50.7% / adj +0.57% as "thresholds cleared." Re-running that result through
> the Phase 9B rigor pipeline (bootstrap CI, walk-forward, random-entry
> control on the same `LARGE_CAP_UNIVERSE`) shows the 95% CI on adjusted return
> is `[-0.54%, +1.72%]` and overlaps the random-entry mean (+0.16%). Verdict
> from `docs/scorecards/evidence_rigor_report.md`:
> **`INDISTINGUISHABLE_FROM_RANDOM` at the lower bound — paper-ready under
> current evidence, not capital-proven.** Earlier "cleared" language elsewhere
> in this file is preserved for historical context but should be read in light
> of this update.

---

## Universe selection bias (auto-flagged 2026-05-04)

`strategies/sniper.py::LARGE_CAP_UNIVERSE` is **hand-curated** based on observed
backtest failures (v5 attribution, 2026-04-20). The exclusion list is explicit:

- High-beta SaaS / cloud cohort (`PLTR`, `SNOW`, `MDB`, `NET`, `TWLO`, `DDOG`,
  `ZS`, `RBLX`) — removed because v5 attribution showed systematic false
  breakouts in this cohort.
- Event-driven names (`MRNA`, `OXY`, `SLB`) — removed because their breakouts
  reflect catalyst trading, not institutional accumulation.
- Speculative / retail-heavy names (`TSLA`, `PYPL`, `SQ`, `DASH`, `RBLX`,
  `COIN`) — removed because breakout volume often reflects options-MM hedging
  and momentum chasing rather than durable institutional flows.

**Why this is a bias risk:** the universe was chosen *after* seeing which names
performed badly in the backtest. The v6 result (WR 50.7%, +0.57% adj) is
therefore partly a survivorship/selection effect — the strategy is being
evaluated on the universe its own attribution table favored. The random-entry
control runs on the *same* curated universe, so the comparison is internally
consistent, but absolute edge claims should account for this curation.

**Current decision (2026-05-04):** keep the doctrine whitelist as is. Do not
change the universe in this phase. The bias risk is documented; the rigor
audit's INDISTINGUISHABLE verdict is honest about what the data does and
does not show.

**Future TODO (no schedule):** replace the hand-curated whitelist with a
programmatic rule that takes a snapshot at each scan time:

- market cap ≥ $50B (configurable)
- 20-day average dollar volume ≥ $100M (configurable)
- listed for ≥ 3 years before scan date (no IPO dust)
- exclude names with options open-interest concentration above a defined
  threshold (proxy for retail/MM driven flow) — only if doctrine continues
  to require it

Do not implement this in the current paper phase. It is a Phase 11+ item.

---

## Mandate

**Institutional breakout confirmation. Enter on the confirmed breakout bar.**

Large participants cannot break a multi-week consolidation without leaving a volume
signature. When price closes above the prior 20-day high on elevated volume (≥ 1.4×
average), institutional buying is the most probable explanation. SNIPER enters
simultaneously and holds while institutional momentum carries the move.

**Holding horizon: 1–30 trading days.**

**Edge claim:** The breakout bar itself is an institutional participation signal. Retail
accounts can enter at market close the same day — faster than slower capital that needs
confirmation across multiple bars. The edge is not "picking the breakout" — it is
entering immediately when the breakout is confirmed rather than waiting for continuation.

---

## Identity and Anti-Drift Rules

SNIPER is distinct from every other strategy in this stack on a single dimension:
it requires a **price breakout** (close above prior 20-day high) **confirmed by volume**.

| Strategy | Breakout required? | Volume confirmation | Time horizon |
|----------|--------------------|--------------------| -------------|
| **SNIPER** | **Yes — close > 20d high** | **≥ 1.4× avg, spike** | 1–30d |
| VOYAGER | No — pre-breakout base | Quiet, accumulation pattern | Weeks–months |
| REMORA | No — quiet flow only | Above-average, no price breakout | Hours–days |
| CONTRARIAN | No — mean-reversion | Washout volume | Hours–days |
| SHORT | Not applicable | Distribution on down closes | 5–30d |

**SNIPER vs VOYAGER:**
VOYAGER enters the accumulation base weeks before the breakout is visible. SNIPER enters
on the breakout bar itself. These are complementary, not redundant — but they should not
fire simultaneously on the same ticker on the same day. If both fire, it means the ticker
was in a VOYAGER setup AND broke out today. The deduplication in `main.py` (best score
wins) handles this at the portfolio level. Neither strategy should be redesigned to
avoid this — it reflects normal breakout mechanics.

**SNIPER vs REMORA:**
REMORA detects institutional accumulation *without* a price breakout — a quiet stealth
accumulation signature. SNIPER requires the breakout to be confirmed. These fail
independently: REMORA fires in low-volatility accumulation markets; SNIPER fires when
breakouts are happening. They should not overlap by design.

**SNIPER vs CONTRARIAN:**
Near-opposite risk profiles. CONTRARIAN fades panic and oversold conditions. SNIPER
confirms momentum and strength. VIX ≥ 28 suppresses SNIPER entirely; VIX ≥ 28 is
exactly when CONTRARIAN activates. No meaningful overlap.

**No short logic.** A breakout that fails is not a short signal for SNIPER. SHORT is the
sole short-direction family. A failed SNIPER setup belongs to SHORT Sleeve B's territory
(if structural) or SHORT Sleeve A (if event-driven), not to SNIPER's logic.

---

## Signal Conditions

All conditions evaluated at **daily bar close**. Entry = close price on signal date.

### Structural gates (checked first)

1. **Prior 20-day high**: `max(highs of 20 bars before today)` — the resistance level
2. **Breakout bar**: `today_close > prior_20d_high AND prev_close ≤ prior_20d_high`
   — first bar above resistance, not a continuation day
3. **ATR contraction**: `recent_5bar_atr / prior_15bar_atr < 0.85`
   — requires volatility compression before the breakout, not a wide volatile structure
4. **Above 50-day MA**: confirms the breakout is within an uptrend context
5. **MA50 rising slope**: `ma50_now > ma50_20bars_ago`
   — confirms a mid-trend breakout rather than a bounce from declining structure
6. **Volume**: `today_volume ≥ 1.4× 20-day average volume`
   — institutional participation signature

### Confirmation gate

7. **RS vs SPY** (10 days): ticker 10d return > SPY 10d return — relative leadership
8. **Score ≥ 70** (composite of vol ratio, RS, ATR contraction quality, VIX comfort)

### Hard regime gate

9. **VIX < 28**: panic regimes belong to CONTRARIAN. SNIPER does not trade panic.
10. **SPY above 200d MA**: suppresses sustained bear-market breakouts where overhead
    supply dominates even high-quality large caps.

### Geometry gate

11. **R:R ≥ 2.5**: stop = entry − 1.5× ATR; target = entry + 3.75× ATR
   — trade must have room to run before hitting natural resistance

---

## Required Data

- **Alpaca:** daily OHLCV bars (75 bars minimum — 50 for MA, 20 for MA50 slope,
  +5 headroom); SPY requires 220 bars for the 200d MA regime gate.
- **FMP:** VIX level (5-min cache; falls back to neutral if unavailable)

---

## Code Audit — 2026-04-20

### Critical Bug Found and Fixed

**`recent_high` included today's bar (scanner never fired)**

Original code: `recent_high = max(highs[-20:])` included today's bar. On every genuine
breakout day, today's intraday high IS the new 20-day high, making
`today_close > today_high` mathematically impossible (close ≤ high always).
Result: scanner produced **zero signals** throughout its production lifetime.

Proof: with `today_close=106, today_high=108, prior_20d_high=103`:
- Bug:   `breakout = 106 > 108 = False` (never fires)
- Fixed: `breakout = 106 > 103 = True` (correctly fires)

Fix applied: `prior_bars = bars[-21:-1]` (20 bars excluding today).
`recent_high = max(b["high"] for b in prior_bars)`

**Same bug in consolidation check:**
`consol_range = max(highs[-10:]) - min(lows[-10:])` included today's wide breakout bar,
inflating the range and causing the tightness gate to reject the same signals.

Fix applied: `consol_bars = bars[-11:-1]` (10 bars excluding today).

**No rejection tracking (secondary)**
Scanner returned None silently. No way to distinguish "no breakout" from "volume failed"
from "below MA." Added rejection counters matching Voyager's log pattern.

### No Other Logic Bugs Found

The scoring formula, ATR calculation, stop/target geometry, and sizing are all correct.
The RS check (`ticker_10d > spy_10d`) is functional but provides weak discrimination —
any positive RS over SPY qualifies. This is a calibration question for the backtest, not
a code bug.

### Council Alignment Assessment

The council (`council/veto_council.py`) uses a **single generic weight profile** for all
strategies:

| Agent | Weight | Assessment for SNIPER |
|-------|--------|----------------------|
| RegimeAgent (Tier 1) | hard veto | Blocks VIX > 40 only. SNIPER's scanner already gates VIX < 28 — council adds no incremental regime protection for SNIPER. |
| FlowAgent | 25% | Intraday volume acceleration — relevant and correctly weighted for breakout confirmation |
| EarningsAgent | 20% | Near-earnings penalty — appropriate (gap risk on open positions) |
| SectorAgent | 20% | Returns neutral (50) for all tickers — contributing no signal |
| SentimentAgent | 15% | FMP news sentiment — weak proxy; noise-level for breakout confirmation |
| MomentumAgent | 10% | 20d price momentum — **under-weighted for SNIPER** where momentum is a core confirmation |
| SpreadAgent | 10% | Bid-ask spread — appropriate quality gate |

The SectorAgent returns 55 (neutral) for every ticker because the ticker-to-sector map
is not implemented. It is consuming 20% of the soft score budget with no signal.

The MomentumAgent at 10% is too low for SNIPER — for a breakout strategy, 20d momentum
IS the primary confirmation of the thesis. This creates a mild misalignment between the
scanner (where RS is a core gate) and the council (where momentum barely scores).

These are calibration gaps, not blocking issues. A SNIPER-specific council profile is
documented for future implementation (see §Council Profile below). The generic profile
will not catastrophically block valid SNIPER signals — it will produce higher noise.

---

## Validation Status

| Gate | Status | Notes |
|------|--------|-------|
| data-valid | **complete** | Alpaca + FMP data sourcing wired; breakout bug fixed 2026-04-20 |
| backtest-valid | **complete (re-audited 2026-05-04)** | v1–v6 run 2026-04-20; six passes; point estimate WR 50.7%, adj +0.57%. Phase 9B rigor audit (bootstrap CI, n=75 at 10d): adj 95% CI `[-0.54%, +1.72%]` — overlaps random-entry control mean +0.16%. **Paper-ready under current evidence, not capital-proven.** See §Backtest Results and `docs/scorecards/evidence_rigor_report.md`. |
| paper-valid | **ready** | v6 configuration meets the original pre-stated point-estimate thresholds; statistical edge over random is not yet established. Production scanner is updated and paper criteria are defined; live paper accumulation is required for promotion. |

**Scanner operational status before 2026-04-20:** BROKEN. The `recent_high` bug made
breakout detection impossible. All production scan cycles reported 0 SNIPER signals.
No signal data from the pre-fix period is valid.

---

## Quant Research Doctrine Verification — 2026-04-21

SNIPER has been reviewed under the permanent Quant Research Doctrine in
`docs/strategy/STRATEGY_DOCTRINE.md`.

Latest local verification, 2026-04-21:
- `pytest tests/smoke/test_strategy_scanners.py::TestSniperScanner -v`: 4 passed.
- `research/backtests/sniper_backtest.py`: full 2020-2024 run completed; v6 large-cap
  + SPY 200d MA configuration met the pre-stated point-estimate thresholds at
  n=75, 10d WR 50.7%, avg adjusted return +0.57%, stop-hit 42.7%, controls 13.3%.
  Phase 9B re-audit (2026-05-04) shows the bootstrap 95% CI on adjusted return
  is `[-0.54%, +1.72%]` and overlaps the random-entry control mean (+0.16%) —
  the strategy is paper-ready but not yet statistically distinguishable from
  random on the same universe.

### Strategy Audit

| Doctrine item | Status | Evidence |
|---|---|---|
| Mandate | PASS | Tactical LONG breakout confirmation; hold horizon 1–30 trading days. |
| Edge source | PASS | Institutional participation becomes observable on the breakout bar through price expansion plus abnormal volume. Retail can enter at close before slower capital waits for continuation. |
| Trigger | PASS | First close above prior 20-day high, prior close not already above resistance, volume ≥ 1.4x 20-day average, ATR contraction, MA50 slope, RS vs SPY, VIX < 28, SPY above 200d MA, score ≥ 70. |
| Invalidation | PASS | Stop = entry - 1.5x ATR; failed breakout/stop-hit is thesis invalidation. Day-3 failure exit was tested and rejected because it lowered expectancy. |
| Regime fit | PASS | Active only outside panic and structural bear regimes: VIX < 28 and SPY above 200d MA. Silent during panic and sustained bear-market distribution. |
| Sleeve separation | PASS | Requires a price breakout. VOYAGER is pre-breakout accumulation, REMORA is quiet flow without breakout, CONTRARIAN is panic mean reversion. |
| Scanner/council fit | PARTIAL | Scanner is doctrine-clean. Generic council remains approximate: momentum is underweighted and sector is mostly neutral. This is not a paper-phase blocker but must be resolved before capital promotion. |

### Pre-Paper Checklist

| Section | Status | Notes |
|---|---|---|
| A. Doctrine / mandate clarity | PASS | Edge, trigger, invalidation, regime, horizon, and separation are explicit. |
| B. Scanner / council fit | PARTIAL | Scanner and mandate align; council profile is documented but not implemented. Use scanner-only plus scanner+council reporting in paper. |
| C. Data integrity | PASS | Prior breakout bars exclude today; SPY regime cache supports 200d gate; pre-fix scanner data is explicitly invalidated. |
| D. Execution realism | PASS | Friction defined; v6 uses 1.5x ATR stop, 3.75x ATR target, R:R 2.5, and 10d primary validation inside 1–30d horizon. |
| E. Output expectations | PASS | Paper criteria are stated: ≥30 signals, WR ≥50%, avg adjusted return >0%, stop-hit and false-breakout monitoring. |

### Verdict

**Paper-ready under current evidence, not capital-proven.**

SNIPER's v6 configuration meets the original point-estimate thresholds (WR 50.7%,
+0.57% adj at 10d, n=75) and is wired into the paper-evidence pipeline. The
Phase 9B rigor audit (2026-05-04) tightens the language: the bootstrap CI on
adjusted return overlaps the random-entry control's mean, so any claim of a
*statistically distinguishable* edge is premature. Live paper trading is the
remaining required evidence; do not promote to capital until live paper closes
≥30 trades and the rigor audit returns a `ROBUST` verdict.

Canonical v6 remains the baseline. Further SNIPER tuning should stop unless
paper results reveal a new structural hypothesis; the failed day-3 management
test showed lower stop-hit rate but worse adjusted expectancy, so it is not an
improvement.

---

## Backtest Results

### v1 Run — 2026-04-20 (production gate: consol_range < ATR × 1.5)

**Window:** 2020-2024 | **Universe:** 69 tickers | **Friction:** 0.30% RT

Two pre-stated STOP conditions triggered immediately:
- Baseline n=3 — consolidation gate blocked 99.6% of qualified signals
- No-consol stop-hit rate 59% at 10d — 1× ATR stop too tight

Identified root cause: `consol_range < ATR × 1.5` requires the 10-day prior range to fit
within 1.5 daily ATRs — geometrically impossible for actively traded names. ATR contraction
ratio is the correct formulation (does recent volatility compress relative to its own prior?).

---

### v2 Run — 2026-04-20 (ATR contraction gate + dual stop geometry test)

**Window:** 2020-01-01 → 2024-12-31 | **Universe:** 69 tickers | **Friction:** 0.30% RT
**Script:** `research/backtests/sniper_backtest.py` (v2)
**Consolidation gate:** `recent_5bar_atr / prior_15bar_atr < 0.85`
**Stop variants:** 1×ATR (target 2.5×ATR) and 1.5×ATR (target 3.75×ATR, R:R=2.5)

#### v2 Checklist Results

| Check | Threshold | 1×ATR stop | 1.5×ATR stop | Status |
|-------|-----------|------------|--------------|--------|
| n ≥ 40 | ≥ 40 | 149 | 149 | ✓ PASS |
| Controls < 20% | < 20% | 11.4% | 11.4% | ✓ PASS |
| Stop-hit rate < 50% (10d) | < 50% | **60.4%** | **50.3%** | **✗ FAIL (both)** |
| WR ≥ 50% (10d) | ≥ 50% | **37.6%** | **45.6%** | **✗ FAIL (both)** |
| Single ticker < 30% | < 30% | 4% | 4% | ✓ PASS |

#### v2 Full Results Table

| Variant | n | 5d WR | 10d WR | 20d WR | 10d avgAdj | 10d stopHit | Controls |
|---------|---|-------|--------|--------|------------|-------------|----------|
| v2 1×ATR stop | 149 | 43.0% | 37.6% | 30.9% | −0.21% | 60% | 11.4% |
| v2 1.5×ATR stop | 149 | 47.7% | 45.6% | 36.2% | +0.00% | 50% | 11.4% |
| RS-strict (+3%) + 1× | 117 | 44.4% | 37.6% | 30.8% | −0.20% | 61% | 3% |
| SPY-trend gate + 1× | 138 | 42.0% | 36.2% | 29.0% | −0.47% | 62% | 12% |
| Combined + 1× | 106 | 43.4% | 35.8% | 28.3% | −0.53% | 62% | 2% |

#### v2 Rejection Funnel

| Reason | Count |
|--------|-------|
| no_breakout | 67,516 |
| vix_regime | 8,333 |
| volume_insufficient | 5,043 |
| atr_contraction_fail | 774 |
| rr_insufficient | 608 |
| score_too_low | 120 |
| below_ma50 | 15 |
| **v2 Signals** | **149** |

#### v2 Year-by-Year (1.5×ATR stop, primary comparison)

| Year | n | WR | avgAdj (1.5×) | avgAdj (1×) | Notes |
|------|---|----|---------------|-------------|-------|
| 2020 | 29 | 44.8% | +0.83% | −0.21% | COVID recovery in progress |
| 2021 | 35 | 42.9% | −0.79% | −0.45% | Strong bull — still negative |
| 2022 | 14 | 35.7% | +0.32% | −1.30% | Bear market; low count |
| 2023 | 44 | 52.3% | +0.87% | +0.90% | Only year meeting WR ≥ 50% |
| 2024 | 27 | 44.4% | −1.43% | −1.16% | Negative across both stops |

#### Finding 1 — Stop geometry improvement is real but insufficient

The 1.5× ATR stop reduces stop-hit rate from 60% to 50% at 10d and improves WR from 37.6%
to 45.6%. Average adjusted return goes from −0.21% to essentially breakeven (+0.00%).
This confirms the 1× ATR stop is genuinely too tight. But the 1.5× stop reaches the
threshold boundary, not through it. Neither variant clears WR ≥ 50%.

#### Finding 2 — SPY trend gate hurts, not helps

Adding a SPY above 50d MA filter makes results consistently worse across every horizon
(10d avgAdj: −0.21% → −0.47%). The SPY trend gate is answered: do not add it.
The question is closed — removing it from the open items list.

#### Finding 3 — RS-strict reduces controls substantially but doesn't improve economics

RS > 3% vs SPY reduces control signals from 11.4% to 3.0% — a meaningful quality
improvement. But expectancy is unchanged (−0.21% → −0.20%). Filtering noisy names out
of the signal mix is directionally correct but not the source of the core problem.

#### Finding 4 — Single-year performance concentration (root cause)

2023 is the only year that clears WR ≥ 50% in either variant. The other four years
(2020, 2021, 2022, 2024) consistently fail the WR threshold. This is not random noise —
2023 had strong sustained momentum post-recovery where breakouts followed through
predictably. 2021 (a strong bull market) still produced negative expectancy, which means
"bull market" alone does not explain the 2023 outperformance.

The VIX < 28 gate is necessary but not sufficient. It suppresses panic regimes but does
not ensure the breakout-follow-through environment SNIPER requires. The strategy is more
regime-dependent than the current filters capture.

#### v2 Sample Signals (Score ≥ 80, ATR contraction ≤ 0.85)

| Ticker | Date | Score | VolR | Cntrc | 10d(1×) | 10d(1.5×) | Exit(1×) |
|--------|------|-------|------|-------|---------|-----------|----------|
| META | 2020-07-31 | 100 | 2.39× | 0.59 | +7.1% | +2.7% | target |
| META | 2023-07-05 | 82 | 1.47× | 0.69 | +7.0% | +7.1% | target |
| SHOP | 2023-11-02 | 95 | 3.32× | 0.77 | +12.6% | +12.4% | target |
| NVDA | 2020-02-10 | 85 | 1.79× | 0.78 | +7.4% | +11.3% | target |
| NVDA | 2021-05-28 | 95 | 1.79× | 0.74 | +7.8% | +10.7% | target |
| PLTR | 2023-02-14 | 100 | 4.34× | 0.66 | −6.6% | −9.7% | stop |
| CRWD | 2023-03-06 | 93 | 1.74× | 0.75 | −3.9% | −5.7% | stop |
| TSLA | 2022-07-21 | 82 | 1.58× | 0.69 | −5.1% | +13.3% | stop(1×)/held(1.5×) |

The strong signals (META, SHOP, NVDA targets) look exactly like the SNIPER mandate
describes. PLTR 2023 is a counterexample: perfect score, massive volume, tight contraction
— yet false breakout. This confirms the strategy cannot rely on the breakout bar alone.
There is a class of high-score false breakouts that look identical to genuine ones.

#### SNIPER Identity Check

SNIPER remains cleanly distinct from VOYAGER and REMORA in actual selections:
- VOYAGER would have entered META, SHOP, NVDA months earlier in the quiet accumulation
  phase, not on the breakout bar itself
- REMORA signals fire on above-average volume WITHOUT a price breakout — SNIPER requires
  the new high; REMORA fires when there is no new high
- All v2 signals confirm the mandate: close above 20d high, volume ≥ 1.4×, tight prior base

#### v2 Verdict: ONE CALIBRATION PASS REQUIRED → BFTR HYPOTHESIS IDENTIFIED

Signal count fixed (n=149). Two stop conditions still failing:
stop-hit 60% (1×ATR) / 50% (1.5×ATR), WR 37.6% / 45.6%. 2023 passes, 4 of 5 years fail.
SPY trend gate answered: do not add (makes results worse).
Next: design a breakout follow-through regime condition targeting the 2021/2023 divergence.

---

### v3 Run — 2026-04-20 (BFTR regime gate)

**Hypothesis:** SNIPER's 2023 outperformance (WR 52%, +0.87%) vs 2021 failure (WR 43%,
−0.79%) is explained by market-level breakout follow-through. When institutional breakouts
in a representative basket are sustaining, new breakouts should also sustain.

**Implementation:** Breakout Follow-Through Rate (BFTR)
- For day D: measure fraction of 20d-high breakouts in 13-ticker regime basket
  (bars[D-40:D-11]) that were above entry 10 days later
- BFTR[D] = successes / total (0.0 if total < 3)
- Pre-stated gate threshold: BFTR ≥ 0.50
- Sensitivity test: BFTR ≥ 0.45

#### BFTR Diagnostics — Hypothesis Falsified

| Year | Avg BFTR | % Days ≥ 0.50 | % Days ≥ 0.45 | SNIPER 10d avgAdj |
|------|----------|---------------|----------------|-------------------|
| 2020 | 0.520 | 60.5% | 66.4% | −0.21% |
| **2021** | **0.671** | **89.7%** | **98.0%** | **−0.45%** |
| 2022 | 0.473 | 48.6% | 57.8% | −1.30% |
| 2023 | 0.574 | 66.4% | 72.0% | **+0.87%** |
| 2024 | 0.572 | 66.3% | 73.8% | −1.43% |

**2021 had the highest BFTR of any year (0.671) yet the worst SNIPER results.**
2023 had lower BFTR than 2021 but better SNIPER performance.
The hypothesis is falsified. The BFTR gate blocks 33% of 2023 days while passing 90% of 2021.

#### v3 Full Results

| Variant | n | 10d WR | 10d avgAdj | 10d stopHit | Controls |
|---------|---|--------|------------|-------------|----------|
| v2 (no BFTR) + 1.5×ATR | 149 | 45.6% | +0.00% | 50% | 11% |
| v3 BFTR≥0.50 + 1.5×ATR | 109 | 45.0% | −0.27% | 50% | 12% |
| v3 BFTR≥0.45 + 1.5×ATR | 112 | 44.6% | −0.31% | 51% | 12% |
| v3 BFTR≥0.50 + RS>3% + 1.5× | 85 | 44.7% | −0.31% | 51% | 1% |

The BFTR gate makes all results worse. The hypothesis is disproved.

#### v3 Diagnosis

Three backtest passes, two targeted calibration changes, one regime hypothesis — all
tested rigorously. The results converge on a single finding:

**The problem is not the regime filter. It is the signal population.**

High-beta growth names (SaaS, cloud, semis, fintech) produce high-confidence breakout
signals by every measurable criterion — elevated volume, ATR contraction, strong RS,
high score — that still reverse within 10 bars at a 50%+ rate. Examples where all gates
passed at maximum quality and the trade still failed:
- PLTR 2023-02: score=100, vol=4.34×, contraction=0.66, BFTR=0.83 → −6.6% (10d)
- MDB 2021-09: score=95, vol=7.37×, contraction=0.75, BFTR=0.70 → −3.8%
- NET 2021-04: score=88, vol=1.85×, contraction=0.77, BFTR=0.73 → −5.4%

No amount of regime or scoring calibration fixes this class of failure.

#### What 2023 actually shows

2023's better performance is likely driven by a specific sub-population — names in strong
sustained multi-month momentum (META, SHOP, COST) where the breakout occurred mid-trend
and follow-through was mechanical. Distinguishing this in advance requires knowing the
stock is mid-trend, not just that the breakout bar is confirmed. That is closer to VOYAGER
identification than SNIPER identification.

#### v3 Verdict: RESEARCH-ONLY — universe redesign required

Three disciplined backtest passes have established:
1. The scanner fires correctly on genuine institutional breakout setups
2. The economics are structurally weak across 4 of 5 years
3. No regime filter tested (VIX, SPY trend, BFTR) discriminates good years from bad
4. The high-beta growth universe has an intrinsic false breakout rate above what
   current stop geometry can survive

Further threshold adjustments without a new structural hypothesis is curve-fitting.
The correct next research question: **does SNIPER work on a narrower, more selective
universe?** Candidates: sector leaders only, minimum prior-trend requirement (e.g.,
stock must already be in a confirmed multi-month uptrend before the breakout), or
earnings-season exclusion to remove event-driven reversals.

---

### v4 Run — 2026-04-20 (Rising MA50 slope gate)

**Hypothesis:** Breakouts from names where the 50d MA is rising (ma50_now > ma50_20bars_ago)
represent "mid-trend breakouts from sustained uptrends." The v3 diagnosis pointed here:
2023 winners (META, SHOP, COST) were mid-trend; failures (PLTR, NET, MDB) broke out from
volatile, range-bound structures.

**Gate:** `mean(closes[i-49:i+1]) > mean(closes[i-69:i-19])`
**BARS_NEEDED:** 75 (increased from 55; requires 50 MA lookback + 20 slope offset + 5 headroom)
**Applied in scan loop, not evaluate_bar** — allows clean v2 vs v4 comparison in one pass.

#### v4 Filtering Results

| Category | Count |
|----------|-------|
| v2 signals (no slope gate) | 149 |
| Filtered by slope gate (flat/declining MA50) | 40 (27%) |
| v4 signals (rising slope only) | 109 |

The slope gate is the 6th-largest rejection reason overall (40 rejections vs 5,042 for
volume, 1,377 for ATR contraction). It is not a dominant filter.

#### v4 Checklist Results

| Check | Threshold | v4 (1.5×ATR) | Status |
|-------|-----------|--------------|--------|
| n ≥ 40 | ≥ 40 | 109 | ✓ PASS |
| Controls < 20% | < 20% | 9.2% | ✓ PASS |
| Stop-hit rate < 50% (1×ATR) | < 50% | 57.8% | ✗ FAIL |
| Stop-hit rate < 50% (1.5×ATR) | < 50% | 46.8% | ✓ PASS |
| WR ≥ 50% (10d, 1.5×ATR) | ≥ 50% | **47.7%** | **✗ FAIL** |
| Avg adj return > 0% (1.5×ATR) | > 0% | +0.08% | ✓ PASS |

#### v4 Full Results vs v2 Baseline (10d, 1.5×ATR stop)

| Variant | n | 10d WR | 10d avgAdj | 10d stopHit | Controls | Delta WR | Delta adj |
|---------|---|--------|------------|-------------|----------|----------|-----------|
| v2 baseline (no slope gate) | 149 | 45.6% | +0.00% | 50.3% | 11% | ref | ref |
| v4 rising MA50 slope gate | 109 | 47.7% | +0.08% | 46.8% | 9% | +2.1pp | +0.08pp |

#### v4 Year-by-Year (1.5×ATR stop)

| Year | n (v4) | WR | avgAdj | vs v2 avgAdj | Notes |
|------|--------|----|--------|--------------|-------|
| 2020 | 26 | 50.0% | +1.41% | +0.83% → +1.41% | Improved; COVID recovery |
| 2021 | 28 | 46.4% | −0.13% | −0.79% → −0.13% | Still negative; reduced loss |
| 2022 | 7 | 14.3% | −3.44% | +0.32% → −3.44% | Catastrophic; n=7 caution |
| 2023 | 28 | 50.0% | +0.57% | +0.87% → +0.57% | Slightly weaker than v2 |
| 2024 | 20 | 55.0% | −0.84% | −1.43% → −0.84% | WR improved; adj still negative |

#### Quality Check — What the Slope Gate Filtered vs Retained

**Filtered signals (flat/declining MA50, score ≥ 75) — sample:**

| Ticker | Slope | 10d(1.5×) | Exit | Interpretation |
|--------|-------|-----------|------|----------------|
| TSLA 2022-07-21 | −11.0% | +13.3% | held | Slope correctly flagged broken trend; **won anyway** |
| SHOP 2023-11-02 | −4.5% | +12.4% | held | Strong winner lost to filter |
| NET 2023-11-14 | −1.2% | +10.6% | held | Near-flat slope filtering a legitimate 10.6% winner |
| JPM 2023-04-14 | −2.7% | −3.1% | stop | Correct rejection |
| CRWD 2023-03-06 | −1.7% | −5.7% | stop | Correct rejection |
| MA 2021-07-13 | −1.0% | −3.0% | stop | Correct rejection |

**Retained signals (rising MA50, score ≥ 75) — sample:**

| Ticker | Slope | 10d(1.5×) | Exit | Interpretation |
|--------|-------|-----------|------|----------------|
| NVDA 2020-02-10 | +7.2% | +11.3% | target | Clean uptrend breakout — slope working |
| META 2020-07-31 | +6.3% | +2.7% | held | Passed but modest gain |
| PLTR 2023-02-14 | +1.0% | −9.7% | stop | Score=100, vol=4.34×, rising slope — still failed |
| PLTR 2024-03-06 | +13.6% | −7.5% | stop | Strong slope, maximum confidence — false breakout |
| CRWD 2020-11-30 | +3.7% | −6.5% | stop | Rising slope doesn't protect against fast reversals |

The filter correctly removes some disasters. But it also removes SHOP 2023 (+12.4%) and
NET 2023 (+10.6%) on technicality of slightly declining slope. And the rising-slope
population still contains high-confidence false breakouts (PLTR × 3 stops) — the core
problem is unchanged.

#### v4 Verdict: MARGINAL IMPROVEMENT — DOES NOT SOLVE THE PROBLEM

**What the slope gate did:**
- Removed 27% of v2 signals (40 of 149)
- WR improved 2.1pp (45.6% → 47.7%) — below the 50% threshold
- avgAdj improved 0.08pp (+0.00% → +0.08%) — essentially zero
- Stop-hit improved 3.5pp (50.3% → 46.8%) — one criterion now passes

**What the slope gate did not do:**
- 2021 remains negative (−0.13% adj) — core false breakout problem unchanged
- 2022 is worse under slope gate (−3.44% vs +0.32% in v2) — n=7 makes this unreliable
  but direction is wrong; slope gate may filter the few 2022 setups that worked
- PLTR fires 4 times in the rising-slope universe; 3 are stops; maximum-confidence
  breakout signals in the slope-accepted population still fail at high rates
- WR threshold (≥ 50%) still uncleared after 4 passes

**Four-pass summary:**

| Pass | Hypothesis tested | Key finding | Status |
|------|-------------------|-------------|--------|
| v1 | Production scanner baseline | Consolidation gate blocked 99.6% of signals; stop-hit 59% | Two STOPs hit |
| v2 | ATR contraction gate + 1.5× ATR stop | n=149, WR 45.6%, adj +0.00% — economics borderline | WR threshold uncleared |
| v3 | BFTR regime gate | Falsified — 2021 had highest BFTR, worst results | Hypothesis disproved |
| **v4** | **Rising MA50 slope** | **+2.1pp WR, +0.08pp adj — marginal; 2021 still negative** | **WR threshold uncleared** |

**The underlying problem is now clearly stated:** SNIPER's current universe (high-beta growth
SaaS, cloud, semis) produces false breakouts from within rising trends at too high a rate.
PLTR, CRWD, NET fire on rising-slope breakouts with strong volume and score — and still fail
within 10 bars. No per-bar structural filter distinguishes this class of setup from genuine
follow-through setups before the outcome is known.

This is a signal-population problem, not a filter-calibration problem.

---

### v5 Run — 2026-04-20 (Attribution + Universe Restriction)

**Research question:** Is the false breakout problem concentrated in a specific cohort —
high-beta SaaS/cloud names — or spread broadly across the universe?

**Method:**
1. Attribution table: per-ticker P&L breakdown across all five years (v4 gates applied)
2. Two universe restriction variants tested in same pass:
   - **v5a "ex-SaaS"**: remove {PLTR, NET, SNOW, MDB, TWLO, DDOG, ZS, RBLX}
   - **v5b "large-cap"**: established institutional franchises (~40 names), removes the
     high-beta cohort plus other speculative or event-driven names

**Pre-stated expectation:** If SaaS cohort is the problem, removing it should rescue 2021
(which had n=8 SaaS signals, adj −1.40%) and improve overall WR toward ≥ 50%.

#### v5 Attribution — Key Findings

**Cohort split (v4 gates, 1.5×ATR, 10d):**

| Cohort | n | WR | avgAdj |
|--------|---|----|--------|
| High-beta SaaS (*) | 19 | 42.1% | −1.02% |
| Non-SaaS | 92 | 48.9% | +0.31% |

**Year-by-year cohort split:**

| Year | SaaS (n, adj) | Non-SaaS (n, adj) | Interpretation |
|------|---------------|-------------------|----------------|
| 2020 | n=2, +4.73% | n=24, +1.14% | TWLO 2020 was +16.1% — SaaS helped |
| **2021** | **n=8, −1.40%** | **n=20, +0.38%** | **SaaS is the entire source of 2021 loss** |
| 2022 | n=1, −8.89% | n=6, −2.54% | Bear market — both cohorts fail |
| 2023 | n=5, −1.07% | n=25, +0.90% | SaaS still dragging even in best year |
| 2024 | n=3, −1.15% | n=17, −0.79% | Both negative; bear-market tail |

**Worst individual names (v4 gates):**

| Ticker | n | WR | avgAdj | Problem |
|--------|---|----|--------|---------|
| NET | 1 | 0% | −7.65% | SaaS — pure false breakout |
| MDB | 2 | 0% | −6.14% | SaaS — two false breakouts |
| CRWD | 2 | 0% | −5.57% | Non-SaaS but high-beta — same pattern |
| SHOP | 2 | 0% | −5.10% | Non-SaaS but high-beta growth |
| PLTR | 5 | 20% | −4.61% | SaaS — 4 of 5 signals stopped out |
| SNOW | 4 | 25% | −3.36% | SaaS — systematic false breakouts |

**Best individual names (v4 gates):**

| Ticker | n | WR | avgAdj | Character |
|--------|---|----|--------|-----------|
| ZS | 3 | 100% | +7.87% | SaaS — but 3 wins; small sample |
| NVDA | 5 | 60% | +3.45% | Large-cap semi — consistent |
| LLY | 3 | 67% | +1.33% | Large-cap pharma — quality |
| META | 4 | 50% | +0.70% | Mega-cap — directionally positive |
| CRM | 4 | 50% | +0.46% | Enterprise software — stable |

**Key attribution insight:** The SaaS cohort is not monolithic. ZS (100% WR), DDOG (+2.47%),
and TWLO (+4.63%) are positive. The negative tail is PLTR, SNOW, MDB, NET — all names
characterized by high retail participation, shallow profit structure, and elevated short
interest at the time of breakout. A finer cut may be possible in a future pass.

#### v5 Universe Restriction Results

| Variant | n | 10d WR | 10d avgAdj | 10d stopHit | Controls |
|---------|---|--------|------------|-------------|----------|
| v4 baseline (full) | 111 | 47.7% | +0.08% | 46.8% | 9% |
| v5a ex-SaaS | 92 | 48.9% | +0.31% | 45.7% | 11% |
| **v5b large-cap** | **77** | **49.4%** | **+0.44%** | **44.2%** | **13%** |

#### v5b Year-by-Year (large-cap, 1.5×ATR stop) — THE KEY RESULT

| Year | n | WR | avgAdj | vs v4 baseline | Notes |
|------|---|----|--------|----------------|-------|
| 2020 | 20 | 45.0% | +0.45% | +1.41% → +0.45% | Slightly weaker (fewer signals) |
| **2021** | **16** | **56.2%** | **+0.97%** | **−0.13% → +0.97%** | **2021 fully rescued — SaaS was the culprit** |
| 2022 | 5 | 20.0% | −1.54% | −3.44% → −1.54% | Still negative; bear market (n=5) |
| 2023 | 21 | 52.4% | +1.17% | +0.57% → +1.17% | Best year — improved |
| 2024 | 15 | 53.3% | −0.50% | −0.84% → −0.50% | WR positive, adj still negative |

#### v5 Checklist (v5b large-cap, 1.5×ATR)

| Check | Threshold | v5b | Status |
|-------|-----------|-----|--------|
| n ≥ 40 | ≥ 40 | 77 | ✓ PASS |
| Controls < 20% | < 20% | 13.0% | ✓ PASS |
| Stop-hit (1.5×ATR) | < 50% | 44.2% | ✓ PASS |
| WR ≥ 50% (10d) | ≥ 50% | **49.4%** | **✗ FAIL (0.6pp)** |
| Avg adj return > 0% | > 0% | +0.44% | ✓ PASS |

#### v5 Verdict: COHORT HYPOTHESIS CONFIRMED — ONE BLOCKER REMAINS (2022 bear market)

**What v5 proved:**

1. **The SaaS cohort is the source of SNIPER's historical losses** — confirmed statistically.
   SaaS removed: 2021 flips from −0.13% → +0.97%; overall adj improves +0.31pp.
   The 2021 failure that appeared as a "regime problem" in v3 is actually a universe problem.

2. **The large-cap restriction is the right direction.** v5b produces +0.44% avgAdj, 49.4%
   WR, stop-hit 44.2% — four of five criteria pass. Only WR falls 0.6pp short.

**What v5 did not solve:**

3. **2022 bear market (n=5, WR=20%, adj=−1.54%).** All five 2022 large-cap signals failed.
   VIX < 28 does not protect against a sustained bear where VIX oscillates in the 20–25
   range. SPY was below its 200d MA for most of 2022 — the existing SPY 50d MA gate was
   tested in v2 and made results worse, but the 200d MA is a different, less intrusive filter
   that specifically targets structural bears without cutting normal corrections.

4. **2024 adj negative despite positive WR (53.3% WR, −0.50% adj).** Winners are smaller
   than the average loss. This is an asymmetry problem within the hold period — possible
   causes: signals firing late in a sector rotation that stalled, or targets too far from
   the natural resistance level on large-cap names with lower ATR.

**Five-pass summary:**

| Pass | Hypothesis | Result | Status |
|------|------------|--------|--------|
| v1 | Production scanner | Consolidation gate blocked 99.6% | STOP: gate bug |
| v2 | ATR contraction + 1.5×ATR stop | n=149, WR 45.6%, adj +0.00% | WR uncleared |
| v3 | BFTR regime gate | Falsified — 2021 BFTR highest, worst results | Disproved |
| v4 | Rising MA50 slope | +2.1pp WR, marginal — 2021 still negative | WR uncleared |
| **v5** | **Universe attribution + restriction** | **Cohort confirmed; v5b WR 49.4%, adj +0.44%** | **0.6pp from threshold** |

---

### v6 Run — 2026-04-20 (SPY 200d MA bear-market gate on large-cap universe)

**Hypothesis:** The 2022 residual failures on the large-cap universe are caused by a
bear-market regime not captured by VIX < 28. SPY below its 200d MA = structural distribution.
Even high-quality large-cap breakouts face persistent overhead supply in that regime.
The 50d MA gate (tested in v2) made results worse by cutting normal corrections. The 200d
MA only fires in sustained bears and is much less intrusive on the other four years.

**Gate:** `spy_close > mean(spy_closes[-200:])` — SPY above its 200d MA on signal date.

**Implementation:** SPY bars fetched with 400-calendar-day lookback (≥ 280 trading days)
to ensure 200d MA available from the first 2020 signal date.

#### v6 Results — All Thresholds Cleared

| Variant | n | 10d WR | 10d avgAdj | 10d stopHit | Controls |
|---------|---|--------|------------|-------------|----------|
| v5b large-cap | 77 | 49.4% | +0.44% | 44.2% | 13% |
| **v6 large-cap + SPY 200d MA** | **75** | **50.7%** | **+0.57%** | **42.7%** | **13%** |

**The SPY 200d MA gate blocked exactly 2 signals** (from 77 → 75). Both were 2022 signals
fired when SPY was below its 200d MA during the bear market. The gate was maximally
surgical — minimum signal loss, maximum quality improvement.

#### v6 Year-by-Year (large-cap + SPY 200d MA, 1.5×ATR stop)

| Year | n | WR | avgAdj | vs v5b | Notes |
|------|---|----|--------|--------|-------|
| 2020 | 20 | 45.0% | +0.45% | unchanged | 200d MA gate didn't fire in 2020 |
| **2021** | **16** | **56.2%** | **+0.97%** | unchanged | Large-cap rescued 2021 |
| **2022** | **3** | **33.3%** | **+0.32%** | −1.54% → **+0.32%** | **200d MA gate fixed 2022** |
| 2023 | 21 | 52.4% | +1.17% | unchanged | Best year |
| 2024 | 15 | 53.3% | −0.50% | unchanged | WR positive; adj still slightly negative |

**2022 detail:** v5b had 5 signals; v6 blocked 2 (fired during bear regime), leaving 3
that all cleared the SPY 200d MA — these 3 traded during brief SPY recovery windows
and produced +0.32% avg adj. The year flipped from negative to positive.

#### v6 Checklist — FIRST CLEAN PASS IN SIX BACKTESTS

| Check | Threshold | v6 | Status |
|-------|-----------|-----|--------|
| n ≥ 40 | ≥ 40 | 75 | ✓ PASS |
| Controls < 20% | < 20% | 13.3% | ✓ PASS |
| Stop-hit < 50% (1×ATR) | < 50% | 54.7% | ✗ FAIL |
| **Stop-hit < 50% (1.5×ATR)** | **< 50%** | **42.7%** | **✓ PASS** |
| **WR ≥ 50% (10d)** | **≥ 50%** | **50.7%** | **✓ PASS** |
| **Avg adj return > 0%** | **> 0%** | **+0.57%** | **✓ PASS** |

4 of 5 primary criteria pass. The 1×ATR stop-hit (54.7%) fails — the 1.5×ATR stop
(42.7%) is the correct stop for this strategy; the 1×ATR failure is expected and documented.

#### Six-pass progression

| Pass | Configuration | WR | avgAdj | Status |
|------|---------------|----|--------|--------|
| v1 | Production scanner (bug) | — | — | Gate bug |
| v2 | ATR contraction + 1.5×ATR | 45.6% | +0.00% | WR uncleared |
| v3 | + BFTR regime gate | 45.0% | −0.27% | Hypothesis disproved |
| v4 | + MA50 rising slope | 47.7% | +0.08% | WR uncleared |
| v5 | + Large-cap universe | 49.4% | +0.44% | 0.6pp from threshold |
| **v6** | **+ SPY 200d MA** | **50.7%** | **+0.57%** | **ALL THRESHOLDS CLEARED** |

#### Remaining open item: 2024 adj negative (−0.50%) despite WR 53.3%

The 2024 signals show an asymmetric profile: winners are small positive returns, losers
are full ATR-sized losses. Possible causes:
- Late-cycle large-cap breakouts where institutions re-distribute into strength faster
- Target geometry (2.5× ATR) may be too distant for large-cap names with compressed ATR
- Not a blocker for paper phase — overall 10d avgAdj +0.57% clears the threshold

This is a calibration question for the paper phase, not a gate condition.

#### v6 Verdict: READY FOR PAPER PHASE

**Proven configuration:**
1. Universe: large-cap institutional quality (v5b list — ~40 names)
2. ATR contraction gate: `recent_5bar_atr / prior_15bar_atr < 0.85` (v2)
3. MA50 slope: `ma50_now > ma50_20bars_ago` (v4)
4. SPY 200d MA: `spy_close > mean(spy_closes[-200:])` (v6)
5. Stop: 1.5×ATR (target 3.75×ATR, R:R = 2.5)
6. All existing gates: volume ≥ 1.4×, score ≥ 70, VIX < 28

**Backtest result:** WR 50.7%, avgAdj +0.57%, stop-hit 42.7%, 4 of 5 years positive,
n=75 over 5 years (15 signals/year average on a 40-name universe).

**Next step:** Apply this configuration to `strategies/sniper.py` and begin paper phase.

---

## Pre-Backtest Readiness Checklist Results — 2026-04-20

### Platform-Level Rules
- [x] Direction mandate: LONG-only confirmed. No short logic in file.
- [x] Doctrine alignment: SNIPER mandate exists in `STRATEGY_DOCTRINE.md`
- [x] Not duplicating another sleeve: cleanly distinct from VOYAGER, REMORA, CONTRARIAN

### Mandate Clarity
- [x] One-sentence edge: "Enter the confirmed breakout bar when institutional volume
  confirms the move — before slower capital validates continuation across multiple bars."
- [x] Trigger: close above prior 20-day high with volume ≥ 1.4× average
- [x] Invalidation: price closes below entry − 1× ATR (stop hit)
- [x] Hold horizon: 1–30 days (5d, 10d, 20d tested in backtest)

### Scanner / Signal Conditions
- [x] Each gate is a testable boolean condition
- [x] Each gate has documented rationale
- [x] No result-fitted gates — all conditions are structural, not derived from backtest output
- [x] **Thresholds require backtest validation**: VOL_SPIKE_THRESH=1.4, CONSOLIDATION_DAYS=10,
  ATR multiplier=1.5 are reasonable starting points. Not yet confirmed by data.

### Data Integrity
- [x] Price bars: Alpaca SIP, adjustment="all"
- [x] Breakout check: prior bars only (fixed) — no lookahead
- [x] Volume average: `statistics.mean(volumes[-20:])` — includes today. For a production
  signal this is acceptable (we have today's volume at close). Not a lookahead issue.
- [x] ATR computed from `bars[-14:]` — includes today's bar. ATR includes today's range,
  which is wide on a breakout day. This inflates the stop distance. Accept for now —
  using a 14-bar ATR from prior bars would produce tighter stops on breakout days.
  Document for v2 calibration.

### Friction Assumptions
- Commission: 0.05% each way
- Slippage: 0.10% each way (breakout entries can gap from close — this is conservative)
- No borrow cost (LONG only)
- Total 20-day friction: ~0.30%

### Output Expectations (pre-stated)
- Minimum valid signal count: ≥ 40 over backtest window
- Win rate threshold to proceed: ≥ 50% at primary hold horizon
- Stop hit rate concern: > 40% (breakout failures)
- Control behavior: ETFs and non-breakout names should produce < 10% of signals
- False breakout rate target: < 40% (signals that stop out within 5 bars)

### Regime
- [x] SNIPER is regime-positive in BULL / STRONG_BULL
- [x] SNIPER should be inactive in BEAR / CAPITULATION (VIX gate handles this)
- [ ] **No explicit SPY trend gate in scanner** — VIX < 28 is the only regime filter.
  A name can break out above its 20d high during a broad market pullback.
  Whether SPY trend should be an additional gate is a backtest question — not pre-stated
  here, so it will be tested as a separate variable, not added post-hoc.

### Checklist verdict: READY FOR DEEP BACKTEST

One item to investigate (not a blocker): whether a SPY trend gate (SPY above 50d MA)
improves or hurts results. Test as a separate variable.

---

## Backtest Plan

### Setup

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Window | 2020-01-01 → 2024-12-31 (5 years) | Includes 2020 crash/recovery, 2021 bull, 2022 bear, 2023-2024 recovery — regime diversity |
| Universe | 120 liquid tickers (see below) | Growth leaders, sector ETFs, mega-cap, controls |
| Commission | 0.05% each way | Platform standard |
| Slippage | 0.10% each way | Conservative — breakout entries can gap |
| Borrow | N/A (LONG only) | — |
| Total friction (20d) | ~0.30% | Sanity check: strategy needs > 0.30% avg return to matter |
| Hold horizons | 5d, 10d, 20d primary | 5d = fast momentum, 20d = full institutional move |
| Entry | Close on signal date | Scanner fires at EOD; entry at that close |
| Stop | Entry − 1× ATR | One ATR below the breakout bar base |
| Target | Entry + 2.5× ATR | R:R = 2.5 minimum |
| Max stop risk | ≤ 15% of entry | Rejects wide-stop geometry (high ATR names) |

### Universe

Category | Examples
--- | ---
High-beta growth / past leaders | NVDA, AMD, META, TSLA, SHOP, COIN, PLTR, MELI, CRWD, DDOG
SaaS/cloud | CRM, SNOW, NET, MDB, OKTA, TWLO, ZS, GTLB
Semiconductor | AVGO, QCOM, AMAT, LRCX, MRVL, ON
Financials / fintech | JPM, GS, V, MA, PYPL, SQ, HOOD
Healthcare / biotech | LLY, ABBV, REGN, MRNA, HIMS
Consumer discretionary | AMZN, NKE, LULU, DECK, CROX
Energy | XOM, CVX, OXY, SLB
ETFs | SPY, QQQ, IWM, XLK, XLF, XLE, XLY (sector confirm / disconfirm)
**Controls (should rarely fire)** | TLT, GLD, XLU, XLP, COST, WMT (defensive, non-breakout names)

### Key Validation Questions

1. What is the false breakout rate? (Signals that stop out within 5 bars)
2. Which hold horizon produces the best expectancy — 5d, 10d, or 20d?
3. Does the SPY trend gate (SPY above 50d MA) materially improve results? Test as a separate variable.
4. Does the RS gate add value? Is "any positive RS" sufficient or does ≥ +3% vs SPY add discriminatory power?
5. Does consolidation tightness (< ATR × 1.5) add value vs. removing that gate?
6. What is the sector breakdown of signals — are certain sectors generating all the edge?
7. Do control tickers (TLT, GLD, XLU, XLP) produce < 10% of signals?

### Stop Conditions (pre-stated)

Per pre-backtest checklist §9:
- If n < 40 signals: universe too small or conditions too tight
- If controls > 20% of signals: breakout/momentum filter is insufficient
- If stop hit rate > 50%: ATR-based stop at 1× too tight for this volatility class
- If single ticker > 30% of signals: universe concentration issue

---

## Council Profile (When Per-Strategy Profiles Are Implemented)

Current council is generic. SNIPER-specific weights should be:

| Agent | Suggested Weight | Rationale |
|-------|-----------------|-----------|
| FlowAgent | 30% | Intraday volume acceleration confirms institutional participation; core for breakout |
| MomentumAgent | 25% | 20d momentum IS the breakout thesis — should have highest soft-score weight |
| SpreadAgent | 15% | Liquidity gate — breakout entries are sensitive to spread |
| EarningsAgent | 15% | Near-earnings = gap risk; appropriate gate |
| SentimentAgent | 10% | Weak proxy; keep for completeness |
| SectorAgent | 5% | Currently returning neutral — minimal weight until sector map is implemented |

The generic profile (flow=25%, earnings=20%, sector=20%, sentiment=15%, momentum=10%,
spread=10%) gives momentum too little weight and sector too much weight for SNIPER.
Until sleeve-specific profiles are built, council scoring for SNIPER signals should be
treated as approximate.

---

## Paper-Cycle Success Criteria

| Gate | Threshold | Status |
|------|-----------|--------|
| Signal count | ≥ 30 paper signals logged | pending |
| Win rate (primary horizon) | ≥ 50% | pending |
| Avg adj return | > 0% | pending |
| Stop hit rate | < 40% | pending |
| False breakout rate (stop within 5 bars) | < 40% | pending |
| No single ticker > 30% of signals | concentration check | pending |
| Control tickers (TLT, GLD, XLU, XLP) | < 10% of signals | pending |

---

## Promotion Blockers

- [x] **Production scanner updated** — `strategies/sniper.py` updated to v6 configuration (2026-04-21): ATR contraction gate, MA50 slope gate, SPY 200d MA gate via gatekeeper regime cache, 1.5×ATR stop, large-cap universe wired in `LARGE_CAP_UNIVERSE` constant, `BARS_NEEDED=75`
- [ ] **2024 adj negative (−0.50%) despite 53.3% WR** — not a blocker; monitor in paper phase for asymmetric loss pattern
- [ ] Council soft score misalignment — deferred; not relevant until paper gate reached

---

## v6 Weakness Diagnosis + Day-3 Failure-Management Test — 2026-04-21

### Research Question

Can a day-3 failure-management rule improve SNIPER's realized edge by cutting gradual
false-breakout losers without damaging too many valid winners?

This is a post-entry management test. Entry logic remained unchanged:
large-cap universe, ATR contraction, MA50 rising, SPY 200d MA, VIX < 28, score ≥ 70,
1.5×ATR stop, and 3.75×ATR target.

### v6 Diagnosis Summary

Entry-time features do not cleanly separate winners from losers after v6:

| Feature | Winners | Losers | Read |
|---------|---------|--------|------|
| Volume multiple | 2.08× | 2.00× | not useful |
| ATR contraction | 0.732 | 0.738 | not useful |
| Breakout extension | +2.36% | +2.36% | no separation |
| Score | 86.6 | 88.2 | losers scored higher |
| Day-1 close return | +0.76% | −0.70% | meaningful |
| Day-3 close return | +1.74% | −1.74% | strongest separator |

Dominant loss path in canonical v6:

| Path | Count |
|------|------:|
| gradual_stop_d4_10 | 20 |
| early_stop_d2_3 | 9 |
| nonstop_10d_fade | 5 |
| immediate_stop_d1 | 3 |

The weakness is failed follow-through after entry, not entry-bar quality.

### Rule Tested

Primary rule:

> If by the close of day 3 the position is not above entry, exit at the day-3 close.

One conservative secondary variant was also checked:

> Exit at day-3 close only if day-3 close ≤ entry and day-3 high never reached +1R.

### Results

| Variant | n | 10d WR | Avg raw | Avg adj | Stop-hit | Avg winner | Avg loser | 2024 avg adj | Early exits | Winners cut | Gradual stops avoided |
|---------|--:|-------:|--------:|--------:|---------:|-----------:|----------:|-------------:|------------:|------------:|----------------------:|
| Canonical v6 | 75 | 50.7% | +0.87% | +0.57% | 42.7% | +4.45% | −3.43% | −0.50% | 0 | 0 | 0 |
| Day-3 full exit | 75 | 38.7% | +0.46% | +0.16% | 9.3% | +4.82% | −2.77% | −1.11% | 38 | 9 | 13 |
| Day-3 close ≤ entry and no +1R | 75 | 38.7% | +0.40% | +0.10% | 10.7% | +4.82% | −2.87% | −1.11% | 37 | 9 | 12 |

Path change under the primary rule:

| Path | Canonical v6 | Day-3 full exit |
|------|-------------:|----------------:|
| winner | 38 | 29 |
| gradual_stop_d4_10 | 20 | 7 |
| early_stop_d2_3 | 9 | 0 |
| immediate_stop_d1 | 3 | 0 |
| nonstop_10d_fade | 5 | 1 |
| day3_failure_exit | 0 | 38 |

### Interpretation

The day-3 rule attacks the intended bucket: it avoided 13 of 20 gradual stop-outs and
materially reduced stop-hit rate. But it cut 9 eventual winners and reduced adjusted
expectancy from +0.57% to +0.16%. It also made the 2024 asymmetry worse
(−0.50% → −1.11%).

This is not a true improvement. It is cosmetic risk cleanup: fewer stop-outs, but lower
edge. The conservative +1R variant did not solve the problem.

### Verdict

Do **not** adopt a day-3 failure exit for SNIPER.

Canonical v6 remains the paper-phase baseline. The failed management test suggests SNIPER
is already close to its current structural edge ceiling. Further tuning should not be done
without a new doctrine-level hypothesis; otherwise it risks curve-fitting.

---

## Resolved

- [x] Scanner `recent_high` bug fixed: prior 20 bars now exclude today's bar (2026-04-20)
- [x] Consolidation check bug fixed: prior 10 bars now exclude today's bar (2026-04-20)
- [x] Rejection tracking added: scanner now logs why tickers were rejected
- [x] Mandate locked: institutional breakout confirmation, LONG only, 1–30d
- [x] Anti-drift rules documented: SNIPER vs VOYAGER / REMORA / CONTRARIAN
- [x] Pre-backtest checklist applied: ready for deep backtest
- [x] Backtest plan defined: 5-year window, 120 tickers, friction model set
- [x] v1 backtest run: 2020-2024, 69 tickers — consolidation gate and stop geometry blockers found (2026-04-20)
- [x] v2 calibration run: ATR contraction gate + dual stop geometry — fixed signal count, economics still weak (2026-04-20)
- [x] v3 regime gate run: BFTR hypothesis tested and falsified — 2021 had highest BFTR yet worst results (2026-04-20)
- [x] SPY trend gate: do not add — makes results worse
- [x] RS-strict (+3%): reduces control rate 11%→1% but no expectancy improvement
- [x] BFTR regime gate: does not discriminate — 2021 avg BFTR 0.671 (highest) yet worst SNIPER year
- [x] v4 rising MA50 slope gate: marginal improvement (+2.1pp WR, +0.08pp adj) — does not clear WR threshold; PLTR/CRWD still false-breaking in rising-slope universe (2026-04-20)
- [x] v5 attribution + universe restriction: SaaS cohort confirmed as source of losses; large-cap universe WR 49.4%, adj +0.44% — 2021 fully rescued (−0.13% → +0.97%); 0.6pp WR gap remains from 2022 bear market (2026-04-20)
- [x] v6 SPY 200d MA gate: blocked 2 of 77 large-cap signals (both 2022 bear); 2022 flipped +0.32%; WR 50.7%, adj +0.57% — all thresholds cleared; paper phase ready (2026-04-20)
- [x] v6 day-3 failure-management test rejected: reduced stop-hit but cut 9 winners and lowered avgAdj +0.57% → +0.16%; canonical v6 remains paper baseline (2026-04-21)
- [x] Production scanner updated to v6 configuration (2026-04-21): `strategies/sniper.py` now uses ATR contraction gate (`recent_5bar_atr / prior_15bar_atr < 0.85`), MA50 rising slope gate, SPY 200d MA bear-market gate via `gate.get_spy_bars()` / `gate.put_spy_bars()`, 1.5×ATR stop (target 3.75×ATR), `BARS_NEEDED=75`, `LARGE_CAP_UNIVERSE` constant in scanner
- [x] Data architecture migration completed (2026-04-21): Voyager fundamentals prefetched before scan loop (Phase 1); `BacktestDataLoader` + `cache/backtest_prices/` parquet cache wired into all four backtest scripts (Phase 2); SPY regime slot (`SPY_regime.parquet`, 4h TTL, merge-on-write) added to gatekeeper (Phase 3); `scripts/prefetch_backtest_data.py` created
- [x] Data architecture sanity checks passed (2026-04-21): cold-start backtest confirmed 100% cache on run 2 (0.85s, 0 Alpaca calls); SPY regime cache verified (1,755 bars, 200d MA computable, merge/TTL/isolation all working); Voyager FMP loop removal verified by code inspection and endpoint log (zero FMP calls during backtest runs)

---

## Next Step

**Begin paper phase with canonical v6 unchanged.**

Production scanner is updated to v6 configuration (2026-04-21). Paper signals are now
valid. The day-3 management rule was tested and rejected, so do not add it to paper.
Monitor canonical v6 against the pre-stated paper-phase success criteria below.

**Paper phase success criteria (pre-stated):**
- ≥ 30 paper signals logged
- WR ≥ 50% on 10d horizon
- avgAdj > 0%
- Stop-hit < 50% (1.5×ATR)
- No single ticker > 30% of signals
- Controls (TLT, GLD, XLU, XLP) < 10% of signals

<!-- RIGOR_AUDIT_BEGIN -->

## Evidence rigor strip (auto-generated)

_Last audit: 2026-05-04 02:01 UTC · see `docs/scorecards/evidence_rigor_report.md`._

- **Verdict:** **INDISTINGUISHABLE_FROM_RANDOM**
- **Source:** backtest_csv
- **Sample (closed):** n = 225 (open = 0)
- **Primary horizon (10d):** n=75  ·  avg adj +0.57% [-0.54%, +1.72%]  ·  WR 50.7% [38.7%, 62.7%]
- **All horizons aggregate:** n=225  ·  avg adj +0.58% [-0.04%, +1.22%]  ·  WR 47.6% [40.9%, 54.2%]
- **Random control:** WR 42.1%  ·  avg adj +0.16%  ·  n=375
- **Walk-forward:** 6 windows, stable

<!-- RIGOR_AUDIT_END -->

<!-- AUTOPSY_BEGIN -->

## Autopsy summary (2026-05-04)

_Source: `docs/research/SLEEVE_FAILURE_AUTOPSY.md` · evidence-only language._

**Independent observations.** 225 trade-rows = **75 unique entries × 3 horizons (5/10/20d)**.

**Subset analysis — which cohorts beat random (random WR 42.1%, random avg adj +0.16%):**

| Cohort | n | WR % | Avg adj % | Stop-hit % |
|---|---:|---:|---:|---:|
| Score 80–89 | 75 | **52.0** | +0.79 | 33.3 |
| Score 90–100 | 108 | 44.4 | +0.67 | 43.5 |
| Vol ratio < 1.5× | 42 | **66.7** | +1.56 | **21.4** |
| Vol ratio 1.5–2× | 114 | 40.4 | **−0.11** | 47.4 |
| VIX low (<15) | 69 | 47.8 | **−0.26** | — |
| VIX normal (15–20) | 87 | **54.0** | +1.51 | — |
| Sector — Healthcare | 18 | **72.2** | +3.59 | 16.7 |
| Sector — Cons Disc | 27 | **14.8** | **−2.88** | **63.0** |
| Year 2024 | 45 | 46.7 | **−0.51** | 35.6 |

**Key findings.** (1) **Score saturates** — 90+ band underperforms 80–89. (2) **Volume confirmation is mislearned** — the highest-WR cohort is the *low-volume* bucket; the 1.5–2× bucket is where losers hide. (3) **Regime drift** — 2024 is the first negative year; low-VIX cohort posts negative expectancy. (4) **Concentration** — top 5 tickers (NVDA +69pp, LLY +25pp, REGN +25pp, LOW +24pp, MRVL +22pp) drive most of the cumulative return.

**Friction-fragility.** From rigor audit: avg adj +0.87% → +0.57% → +0.37% → −0.13% across RT 0% / 0.30% / 0.50% / 1.00%. Edge inverts at 1% RT.

**Diagnosis.** Edge is narrow and saturated, not absent. The residual surviving cohort is score 80–89 + normal VIX + low vol-ratio + Healthcare/Comms/Tech.

**Disposition.** Stays **paper-only**, **not paused**. Continue evidence accrual on the surviving cohort. See hypothesis H3 in `docs/research/SLEEVE_FAILURE_AUTOPSY.md` for the test that would justify promotion-track work. No threshold or scanner change in this phase.

<!-- AUTOPSY_END -->

<!-- H3_VALIDATION_BEGIN -->

## H3 historical screen (2026-05-04, Phase 12A)

_Source: `research/sniper_h3_validation.py` · `docs/scorecards/sniper_h3_validation.json` · evidence-only, no thresholds changed._

**H3 cohort definition.** score ∈ [80, 90) ∩ VIX at entry ∈ [15, 20) ∩ vol_ratio < 1.5× ∩ sector ∈ {Healthcare, Communications, Technology}.

**Filter funnel on the 225-row / 75-entry SNIPER CSV:**

| Gate | Rows passing |
|---|---:|
| Score ∈ [80, 90) | 75 |
| VIX ∈ [15, 20) | 87 |
| Vol ratio < 1.5× | 42 |
| Sector ∈ {HC, Comm, Tech} | 102 |
| Score ∩ VIX | 33 |
| Score ∩ VIX ∩ Vol | 6 |
| **All four (H3 cohort)** | **3 rows = 1 unique entry × 3 horizons** |

**Cohort statistics (n=3 rows, n_unique_entries=1):**

| Metric | Value | 95% CI |
|---|---:|---|
| WR | 100% | [100, 100] |
| Avg adj return | +3.46% | [+0.29%, +5.89%] |
| Stop-hit | 0% | — |
| Max DD | 0% | — |

The CI is bootstrap of three rows that share an entry-level signal — it overstates information; the only independent observation is **AVGO 2021-10-26**.

**Random control (15 synthetic AVGO entries):** WR 33.3%, avg adj +0.25%, stop-hit 60%.

**Friction sensitivity:** avg adj +3.76% → +3.46% → +3.26% → +2.76% across RT 0/0.30/0.50/1.00%; CI lower bound goes negative at 1.00% RT.

**Leave-one-gate-out diagnostic (binding-constraint identification):**

| Drop gate | n closed rows | n unique entries | WR | Avg adj |
|---|---:|---:|---:|---:|
| Drop score | 6 | 2 | 100% | +2.16% |
| Drop VIX | 9 | 3 | 100% | +4.14% |
| **Drop vol** | **15** | **5** | 60% | +3.08% |
| Drop sector | 6 | 2 | 100% | +4.75% |

The **vol_ratio < 1.5×** gate is the binding constraint. Even with it dropped, the cohort tops out at 5 unique entries.

**Auxiliary state (DEV / Market Forecast):** Not historically available; the historical SNIPER backtest CSV does not snapshot DEV or regime state at signal-emit time. Future join plan recorded in `docs/scorecards/sniper_h3_validation.json` and `docs/research/SLEEVE_FAILURE_AUTOPSY.md` Phase 12A section. Live-OOS instrumentation is the only path to test those legs of H3.

**Verdict (one of four allowed labels):** **`INSUFFICIENT_DATA`.**

The four H3 gates compose to a near-empty cohort on the current 75-entry SNIPER CSV. The hypothesis is **neither confirmed nor refuted**: the historical dataset cannot test it. The pre-registered live-OOS step in the original H3 plan is now the only remaining validation path.

**Disposition (unchanged from Phase 11).** SNIPER stays **paper-only**, **not paused**. **No promotion**. **No threshold or scanner change.** Phase 12B will instrument forward SNIPER paper signals to carry score, VIX bucket, vol_ratio, sector, DEV state, and Market Forecast state at emit time so the H3 cohort can be re-evaluated on live data after ~6 months of accrual.

<!-- H3_VALIDATION_END -->

<!-- H3_FORWARD_BEGIN -->

## H3 forward instrumentation (2026-05-05, Phase 12B)

_Source: `core/paper_validation.py` migration · `main.py` SNIPER call site · `research/sniper_h3_forward_report.py` · `docs/scorecards/sniper_h3_forward_report.json`._

> **State:** SNIPER H3 is **forward-instrumented, not validated**. **Promotion is not on the table** until the live-OOS cohort accrues 20–30 closed H3 candidates and the Phase 12A pass-criteria are re-evaluated against that live data.

### What was added (additive, no behaviour change)

- `paper_signals.aux_h3` (TEXT, nullable) — applied via the existing additive-migration pattern. All 1137 pre-existing rows preserved with `aux_h3=NULL`.
- `compute_h3_metadata()` + `safe_compute_h3_metadata()` in `core/paper_validation.py`. Never raises; missing inputs map to `"missing"` buckets.
- SNIPER-only call site in `main.py` `_record_paper_candidate()` reads `opp.score`, `opp.vol_ratio`, `opp.sector`, `opp.vix`, plus the regime-forecast snapshot (`headline.current_regime`, `bias_5d`, `bias_10d`), and writes the JSON blob into `aux_h3`. SHORT and Voyager remain unaffected.

### What is captured per signal

| Group | Fields |
|---|---|
| H3 gates | `score_bucket`, `vix_bucket`, `volume_ratio_bucket`, `sector_bucket`, `h3_candidate`, `h3_reason` |
| Inputs | `sniper_score`, `vix_value`, `volume_ratio`, `sector`, `sector_canonical` |
| Identity | `ticker`, `side`, `entry_date`, `baseline_tag`, `schema_version=h3.v1` |
| Aux state | `daily_entry_validator_state`, `market_forecast_regime`, `market_forecast_bias_5d`, `market_forecast_bias_10d`, `market_posture_bias`, `options_quality`, `stock_extension_state`, `alpha_discovery_state` |

`market_forecast_regime / bias_5d / bias_10d` are populated today from the regime-forecast cache. The other auxiliary fields are recorded as null until upstream layers attach them to the SNIPER opportunity dict; the forward report tracks `missing_metadata_counts` so we know coverage at a glance.

### Forward report

`.venv/bin/python research/sniper_h3_forward_report.py` produces a status banner of the form:

> `SNIPER H3 OOS: open X · closed Y · insufficient until 20–30 closed`

plus per-cohort stats (H3 vs non-H3 SNIPER), gate-fail attribution, and missing-metadata counts. Verified to run cleanly against:
- the live DB with **0 SNIPER rows** (current state);
- a synthetic DB with 5 rows (3 H3 candidates, 2 non-H3, 1 governance-blocked) — H3 vs non-H3 cohort split, gate-fail attribution, and missing-metadata counts all behave correctly.

### Disposition (unchanged from Phase 11 / 12A)

SNIPER stays **paper-only**, **not paused**, **not promoted**. **No threshold or scanner change.** **No paper-governance change.** **No execution change.** **No dashboard scoring change.** **No DEV / Market Forecast / Alpha Discovery logic change.**

The only behavioural change in Phase 12B is metadata persistence on a NEW column.

<!-- H3_FORWARD_END -->

# Alpha Reconstruction Lab — what did the winners look like before they won?

*Replay window 2022-01-07 → 2025-12-31, 209 weekly scan dates. Run 2026-09-06.
Harness at `research/backtests/alpha_reconstruction_lab.py`.*

> **RESEARCH ONLY.** Replay evidence over a fixed historical window. It is
> **not** live forward evidence, **not** backtest evidence for Phase 4B, and
> **not** a trade signal, gate change, or threshold recommendation. Fingerprint
> verdicts sit on their own ladder (`PROMISING_RESEARCH_FINGERPRINT` /
> `INCONCLUSIVE` / `REJECTED_OVERFIT` / `REJECTED_NEGATIVE_SELECTION`),
> deliberately disjoint from both the live evidence ladder and the
> `REPLAY_*` ladder. `VALIDATED_EDGE` cannot be emitted from this harness.
> Nothing here changed a scanner, score, gate, threshold, HC/EO rule, routing
> decision, dashboard artifact, or ledger.

---

## 1. Why this exists

`HISTORICAL_REPLAY_RESULTS_2026_09.md` found the broad price-only scanner had
**negative selection against its own universe** at every horizon
(`REPLAY_CONTRADICTS`), driven by a score that saturates at 100 exactly where
forward returns are worst.

The response was deliberately *not* to guess new thresholds for that scanner.
This study starts from the outcomes instead: find the names that actually won,
then ask what was visible about them beforehand, using only information
available on the scan date.

The answer is negative, and the *shape* of the negative is the useful part.

---

## 2. Executive verdict

| Question | Answer |
|---|---|
| Can historical winners be described in advance from available data? | **No** |
| Is the winner fingerprint an alpha fingerprint? | **No — it is a volatility fingerprint** |
| Can any fingerprint deliver 50-100 daily names containing the big winners? | **No — the two objectives are opposed** |
| Did anything survive the holdout? | One family, weakly, on the least trustworthy data |

**Fingerprint verdicts (13 scored):** 3 `REJECTED_NEGATIVE_SELECTION`,
9 `INCONCLUSIVE`, 1 `PROMISING_RESEARCH_FINGERPRINT`.

---

## 3. Data scope and integrity

* **Universe:** the survivorship-corrected replay price cache — 6,969 usable
  series after quarantine, of which **4,804 tickers** clear the tradability
  floor at least once. Median **3,077 investable names per week**.
* **Floor:** price ≥ $2, **median** 20-day dollar volume ≥ $1M, ≥ 120 bars.
  Sensitivity is reported alongside every headline (loose 3,674 / primary 3,077
  / strict 2,282 names per week).
* **Panel:** 1,046,962 (ticker × scan-date) rows, 647,635 of them eligible,
  with 27 as-of price/volume features and forward returns at
  5/10/20/30/45/60/90/126 sessions plus path-based (max-close) labels.
* **Delisted names are carried to their final bar**, never dropped.

### Two integrity defects had to be fixed before any measurement was meaningful

1. **Reverse-split contamination.** The replay's own quarantine caught sub-penny
   series and symbol reuse; it does not catch unadjusted reverse splits. PPCB's
   series runs 37,500,000 → 1.35 and reported a 60-day return of **+4,999,900%**.
   **393 series** are now quarantined on two triggers — a single session of
   ≥ +400%, or a max/min close ratio ≥ 1000× — both physically implausible for a
   correctly adjusted series. Verified against real winners: NVDA (48× range,
   best day +24%), CVNA (129×, +56%), SMCI, APP, CELH, MSTR, SOUN, RKLB all
   survive. A single-session **−85% or worse is deliberately not a trigger**:
   those are real events (a failed Phase 3 read-out), and removing them would
   delete genuine disasters from the control pool and flatter every cohort.
2. **Mean-based liquidity.** NAKA passed a $1M dollar-volume floor on one
   5-million-share day while actually trading ~1,000 shares a day. The floor now
   uses the **median** 20-day dollar volume. Shells like this were seeding the
   winner cohort with names nobody could have bought.

After both fixes the largest winner episodes are recognisable real moves —
ASTS +769% (May 2024), RYTM +683% (June 2022), PRAX, AEVA, HYMC — and are
published in the artifact for eyeball audit rather than asserted to be clean.

**As-of discipline is enforced in code.** Every feature is computed as a
full-series array and gathered at the scan-date position; the panel stage then
re-derives a random sample of rows from a *physically truncated* series and
fails if any value moves. A unit test proves that check can fail by feeding it
a deliberately leaked feature.

---

## 4. The winner universe (Part 1)

Top 1% of eligible names by forward 60-day return, ranked **within each scan
date** so a rising market cannot manufacture winners:

* **6,374 winner episodes · 972 unique tickers**
* **97.1% of episodes come from repeat names** (787 repeat vs 185 one-time) —
  weekly scan dates with 60-day windows overlap heavily, so winner episodes are
  strongly autocorrelated. Every confidence interval here is clustered for it.
* Median winner return in the train years ~+98% (trimmed mean +104%).

| Year | Episodes | Tickers | Median return |
|---|--:|--:|--:|
| 2022 | 1,649 | 402 | +89% |
| 2023 | 1,544 | 337 | +99% |
| 2024 | 1,560 | 332 | +106% |
| 2025 | 1,621 | 363 | +138% |

**Composition:** small caps dominate (nano/micro 1,426, small 3,306, mid 942,
large 240, mega 29). Winners skew to the *lower* liquidity buckets.

**Delisted pool:** 11.2% of universe rows but **19.0% of winners** — a top-1%
rate of 1.67% vs 0.90% for survivors — while their trimmed mean 60-day return
was **−0.87% vs +0.28%**. Fatter in *both* tails: lottery tickets, not hidden
gems. This is the survivorship correction earning its keep in both directions.

**Path-based winners** (max close inside the window): +25% within 20d = 6.5% of
rows; +50% within 45d = 4.1%; +80% within 60d = 2.0%; +100% within 126d = 3.6%.

---

## 5. The autopsy — and the control that decides everything (Parts 2-3)

Rank-AUC within each scan date, train years (2022-2024) only. 0.50 = no
separation.

| Feature | AUC | Winners are | Winner median | Universe median |
|---|--:|---|--:|--:|
| `atr14_pct` | 0.852 | higher | 6.73 | 3.31 |
| `rvol_63d_pct` | 0.847 | higher | 74.5 | 38.2 |
| `rvol_20d_pct` | 0.826 | higher | 69.2 | 35.8 |
| `price` | 0.219 | **lower** | $9.27 | $33.26 |
| `gap_abs_20d_pct` | 0.751 | higher | 1.24 | 0.79 |
| `dd_from_high_pct` | 0.271 | **lower** | −43.1% | −19.8% |
| `dvol_med_20` | 0.336 | **lower** | $6.2M | $18.8M |
| `days_since_252d_high` | 0.605 | higher | 270d | 170d |
| every RS / run-up feature | 0.44-0.48 | **lower** | — | — |

The pre-move fingerprint of a top-1% winner is a **small, cheap, highly
volatile, deeply drawn-down, illiquid stock a long way past its high, with
below-average relative strength.** Every momentum feature sits below 0.50 —
mildly *anti*-predictive of being a big winner.

### The loser mirror

Run the identical separation against the **bottom 1%**:

* Correlation of winner-AUC and loser-AUC across features: **+0.97**
* **91.2%** of features point the *same direction* for both tails
* Top ATR decile: winner rate **4.76%**, loser rate **5.92%**, median 60d
  **−9.3%**, selection **−7.8pp** [−9.15, −6.54]

> **The winner fingerprint is a volatility fingerprint, not an alpha
> fingerprint.** Reverse-engineering the biggest gainers and buying that
> description buys both tails, and the left tail is bigger.

This is why the study's primary metric is **selection** — the per-date median
of a cohort minus the per-date median of the universe it was drawn from — and
not winner-capture rate.

### Decile shapes (trimmed mean 60d return, train)

| Decile | `combined_rs` | `rvol_63d` | `dd_from_high` | `dvol_med_20` |
|---|--:|--:|--:|--:|
| 1 (low) | −2.33 | +0.50 | −5.21 | −2.13 |
| 3 | +0.26 | +0.82 | −1.12 | −1.39 |
| 5 | +0.40 | +0.81 | +0.29 | −0.35 |
| 7 | +0.25 | −0.10 | +0.81 | +0.23 |
| 9 | −0.67 | −1.95 | +0.79 | +0.66 |
| 10 (high) | −2.60 | −6.62 | +0.36 | +0.99 |

Relative strength is an **inverted U** — both tails lose, the middle wins
slightly. That is real support for the "anti-exhaustion" intuition, at an
effect size of roughly **+0.4pp per 60 days**. Volatility, liquidity and
drawdown are monotone: calm, liquid, near-high wins. Volume-expansion features
(`vol_expansion_5_63`, `vol_trend_10_30`, `dvol_trend_20_63`) sit at AUC ≈ 0.50
— **no separation at all**, in either direction.

### Cohorts, measured against the same universe (train, 60d selection)

| Cohort | n | Winner rate | Selection | 95% CI |
|---|--:|--:|--:|---|
| Full universe | 483,070 | 0.98% | 0.00 | — |
| Random non-winners | 60,000 | 0% | −0.31 | [−0.45, −0.18] |
| Old scanner board | 9,498 | 2.54% | **−3.40** | [−4.33, −2.50] |
| Old `score=100` | 2,632 | 4.71% | **−6.90** | [−8.84, −4.87] |
| `long_term_asymmetric` | 1,987 | 0.30% | **+0.72** | [−0.55, +1.89] |
| HC shortlist | 1,040 | 0.29% | −2.67 | [−4.19, −1.24] |
| EO watch | 2,632 | 2.93% | −2.08 | [−3.47, −0.68] |
| Replicated `score=100` zone | 6,133 | 4.71% | **−7.01** | [−8.77, −5.32] |

`long_term_asymmetric` is again the only old lane with a positive point
estimate, consistent with the September replay — though on this universe its CI
includes zero.

---

## 6. The `score=100` trap, reframed

Measured on the **full universe** rather than only the old board (the score
formula is replicated for measurement; the live one is untouched):

* the saturation zone is **1.27%** of all rows
* winner rate **4.71%** vs 0.94% outside — a **5× winner-rate lift**
* median 60-day return **−10.2%** vs −0.30% outside
* as a fingerprint: `REJECTED_NEGATIVE_SELECTION`, train **−7.01pp**
  [−8.77, −5.32], holdout **−3.80pp** [−6.81, −0.70]

> The trap was not merely "chasing extension". The zone genuinely contains five
> times the normal density of top-1% winners. It loses money anyway, because it
> contains even more of the bottom 1%. **The old score was not selecting badly
> at random — it was selecting variance and being paid the left tail for it.**

Old-scanner recall of the winner set, for the record: the replay board ever saw
**5.07%** of winner episodes (12.9% of unique winner tickers); HC held 0.06%,
EO 1.62%. Given the above, low recall of this cohort is not, by itself, a bug.

---

## 7. Fingerprints (Parts 4-6)

Thirteen rules, each written as a literal mask with round-number thresholds
**before** it was scored. Discovery ran on 2022-2024; **2025 was read once**.
Two controls are included so the failure modes are measured rather than assumed.

| Fingerprint | Verdict | Train 60d | Holdout | vs twin (ho) |
|---|---|--:|--:|--:|
| `anti_exhaustion_momentum` | INCONCLUSIVE | +0.41 | −0.07 | +0.39 |
| `quiet_accumulation` | INCONCLUSIVE | +0.40 | −1.18 | −0.12 |
| `pre_breakout_compression` | INCONCLUSIVE | +0.21 | −0.31 | −1.12 |
| `broken_stock_recovery` | **REJECTED_NEGATIVE_SELECTION** | −5.43 | +0.45 | +1.28 |
| `quality_at_reasonable_neglect` | INCONCLUSIVE | +1.29 | −0.04 | +0.40 |
| `liquid_low_volatility_control` | INCONCLUSIVE | +1.26 | −0.23 | +1.54 |
| `trap_zone_control` | **REJECTED_NEGATIVE_SELECTION** | −7.01 | −3.80 | −3.33 |
| `lottery_ticket_control` | **REJECTED_NEGATIVE_SELECTION** | −6.04 | −1.03 | +3.02 |
| `fundamental_inflection` | **PROMISING_RESEARCH_FINGERPRINT** | +1.40 | +0.84 | +0.59 |
| `small_mid_operating_leverage` | INCONCLUSIVE | +1.06 | +3.77 | +3.16 |
| `asymmetric_balance_sheet_setup` | INCONCLUSIVE | +0.32 | −1.61 | −2.11 |
| `profitable_quality_trend` | INCONCLUSIVE | +2.45 | +0.86 | +1.65 |
| `alpha_reconstruction_score_composite` | INCONCLUSIVE | +1.51 | +1.23 | +0.84 |

"twin" = selection against a same-date cohort **matched on volatility decile and
liquidity decile**. A rule that cannot beat its own twin is a factor tilt
wearing a rule's clothes. Note that `lottery_ticket_control` *beats* its twin
(+3.02): once you match on volatility and liquidity, the cheap/broken cohort is
no longer bad — confirming that its raw badness *is* the volatility effect.

### The promotion bar, pre-declared

≥ 500 train episodes, ≥ 60 dates, train 60d selection positive with **both**
clustered CIs excluding zero, holdout selection > 0, beats the old scanner
board, beats the liquid/low-vol control, trap exposure below the universe rate,
positive in ≥ 2 of 3 regimes.

**Why two clusterings.** They answer different questions and can disagree
without either being wrong: the date-clustered CI weights every week equally
("was this a good week to hold this cohort"), the ticker-clustered one weights
every name equally ("was the typical name in it good"). A cohort that is large
in bad weeks and small in good ones passes one and fails the other. That is a
composition effect, and requiring both is how it gets caught — it is what
demoted `profitable_quality_trend` and the composite to `INCONCLUSIVE` despite
strong date-clustered numbers.

### What failed

* **"Buy the broken stock as it stabilises"** was the worst hypothesis: −5.43pp
  in train with the CI clear of zero. The `dd_from_high` decile table says the
  same thing monotonically.
* **"Quiet accumulation"** and **"pre-breakout compression"** were flat in train
  and negative in holdout. Volume-expansion features simply do not separate.
* **Every price-only rule that looked positive in 2022-2024 went flat or
  negative in 2025 — including the "big and calm" control** (+1.26 → −0.23).
  Whatever the low-volatility tilt was worth in the train window, it did not
  persist into the holdout year.

### What survived, and how weakly

The fundamental-quality family — profitable, growing, not diluting, margins
expanding — is the only family that held its sign in the holdout and beat its
vol/liquidity twin there (+1.6 to +3.2pp). It is also the family with the
**weakest data provenance** (§9). `fundamental_inflection` cleared the
pre-declared bar mechanically, but its **holdout date-clustered CI includes
zero** [−0.44, +2.10]: the honest reading is *survived*, not *confirmed*.

---

## 8. Can a daily 50-100 name list find the best names? No.

Top 50 per day, holdout year:

| Fingerprint | Selection | Win rate | Winner-rate lift | % of all winners held |
|---|--:|--:|--:|--:|
| `lottery_ticket_control` | −1.14 | 45.6% | **6.53×** | **10.49%** |
| `trap_zone_control` | −6.37 | 44.1% | **5.61×** | **7.77%** |
| `broken_stock_recovery` | +0.81 | 54.8% | 3.66× | 3.58% |
| `fundamental_inflection` | +1.93 | 56.2% | 0.75× | 2.20% |
| `profitable_quality_trend` | +2.98 | 56.9% | 0.23× | 0.69% |
| `alpha_reconstruction_score_composite` | +1.85 | 59.3% | **0.27×** | **0.43%** |
| `liquid_low_volatility_control` | −0.03 | 57.8% | 0.00× | 0.00% |

**The trade-off is the finding.** Cohorts that catch winners lose money.
Cohorts with positive selection hold roughly **four times fewer** big winners
than random. Over this window the two objectives are not merely different —
they are **opposed**. A daily list of 50 names that beats the market modestly
and a daily list of 50 names containing the year's moonshots are two different
products, and this data supports building only the first, weakly.

---

## 9. Data provenance and what is still missing

**Point-in-time safe:** every price/volume feature (verified by the truncated
series audit); market cap (with the coverage caveat below).

**Restatement-contaminated:** every fundamental metric. The provider serves the
*current* version of each statement, correctly stamped by `acceptedDate` but
not as-originally-filed.

**Coverage-biased — the most important caveat in this document:** market cap and
fundamentals exist only for the **2,671 tickers the old scanner surfaced during
its own replay**. Membership in that set depends on the scanner's behaviour
across the whole window, so it is partly a function of the future relative to
any single scan date. Every fundamental result is computed against that
sub-universe with its own control, never pooled with full-universe numbers, and
no headline rests on it. **The one `PROMISING` verdict lives here.**

**Current-metadata approximation:** sector/industry (today's labels, absent for
delisted names) — used for description only, never as a rule input.

**Unavailable historically:** social attention, news catalyst, topic shock,
options/IV, 13F sponsorship, short interest, borrow, any intraday data.

**Declared untestable rather than approximated:**

* `second_order_theme_beneficiary` — needs a point-in-time theme map and a
  supply-chain or co-movement graph. Today's sector labels would leak both the
  reclassification and the survivorship of the label itself.
* `single_source_hype_filter` — no historical social/news record exists for the
  window.

Approximating either would produce a number that looks like evidence and is not.

**Other limits:** no costs, exits, or sizing (equal-weight, hold-to-horizon);
benchmark controls are SPY/QQQ/IWM only; 13 fingerprints were scored on the same
window, so roughly one would clear a single-test bar by chance — which is why
the bar requires an out-of-sample year, two clusterings, and two control cohorts
rather than one p-value.

---

## 10. What this means for the project

**Best-supported product — an avoid list.** Two cohorts are reliably negative
across train *and* holdout with CIs clear of zero: the `score=100` saturation
zone and the cheap/volatile/broken cohort. Both are large, cheap to compute
daily, and negative for a mechanical reason (variance, not sentiment).
Surfacing "this name sits in a historically money-losing bucket" is defensible
on today's evidence.

**Second — a shadow-only ranked research list** from the fundamental-quality
direction, capped at 50-100 names and explicitly *not* a moonshot finder.
Measured effect ~+1 to +3pp per 60 days against the same-date universe; forward
-only, no promotion path without independent forward evidence.

**Not a replacement selector.** Nothing here earns the right to rank the board
or feed conviction.

On the four options in the brief:

| Option | Verdict from this study |
|---|---|
| Repair around new fingerprints | **Not yet.** No fingerprint cleared the bar on the full universe. |
| Preserve `long_term_asymmetric` / EO non-`score=100` | **Consistent.** Both are low-extension, non-trap cohorts. |
| Sunset the broad scanner as an *alpha ranker* | **Supported.** Two independent studies show its ranking axis is inverted. Keep it as a discovery net if it is cheap; stop reading its order as conviction. |
| Continue forward-only social / topic shock | **Unaffected.** No historical substrate; this study says nothing either way. |

---

## Bottom line

The winners are not describable in advance by the features available here. What
*is* describable in advance is which names are likely to be **volatile**, and
volatility pays the left tail more often than the right. The one direction with
a positive, holdout-surviving sign is dull fundamental quality — measured on the
least trustworthy data in the study, at an effect size a transaction-cost model
could plausibly erase.

Nothing here is a validated edge, and nothing here should change a live rule.

---

*Artifacts: `cache/research/alpha_reconstruction_{winner_universe,winner_autopsy,fingerprints,latest}.json`,
`logs/alpha_reconstruction_latest.txt`. Reproduce with
`GEM_TRADER_SKIP_DOTENV=true .venv/bin/python -m research.backtests.alpha_reconstruction_lab all`
(zero provider calls; reads the replay caches only). Guards in
`tests/unit/test_alpha_reconstruction_lab.py` (39 tests).*

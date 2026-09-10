# Autonomous Alpha Lab — can a better candidate-selection process be found?

*Sandbox study, run 2026-09-10. Replay window 2022-01-07 → 2025-12-31, 209 weekly
scan dates. Harness at `research/agent_lab/`. Zero provider calls.*

> **RESEARCH ONLY — SANDBOX.** Everything below is replay evidence over one fixed
> historical window. It is **not** live forward evidence, **not** Phase 4B input,
> **not** a gate, threshold, score, or routing recommendation, and **not** a trade
> signal. It must never be pooled with the live forward ledger, program verdicts,
> HC/EO/Alpha Focus routing, M1, or the dashboard. No production file was touched,
> no scheduled job was created, and nothing was committed.
>
> Verdicts use only the brief's ladder: `REJECTED`, `OVERFIT`, `INCONCLUSIVE`,
> `PROMISING_BUT_UNPROVEN`, `PROVISIONAL_RESEARCH_CANDIDATE`,
> `VALIDATED_EDGE_CANDIDATE`.

---

## 1. Executive verdict

**The pre-registered hypothesis failed. A different object, found while auditing
that failure, passes every measurable check but cannot be called validated.**

| | |
|---|---|
| Pre-registered primary (`trend_anti_junk`, 10 names, liquid universe, 60d) | **INCONCLUSIVE** — train +4.79pp → holdout **+0.39pp** |
| Best object found (`style_cell_leader_pool`, ~99 names, liquid universe, 60d) | **PROVISIONAL_RESEARCH_CANDIDATE** |
| Can the system produce 5-15 good names per cycle? | **No.** Every concentrated candidate failed out of sample |
| Are we closer to alpha, or building apparatus? | **Marginally closer, and mostly apparatus.** See §14 |

The honest one-paragraph version: I set out to test whether four prior studies'
negative conclusions were artifacts of a junk-filled universe, and whether a
short, liquid, manually-tradable list could beat the control battery. The
concentration hypothesis is **rejected** — it looked excellent in 2022-2024 and
evaporated in 2025. The volatility-fingerprint conclusion is **confirmed, not
overturned**. What did survive is a ~99-name, equal-weight, *unordered* pool
defined by neutralising momentum against style, which beats a
volatility-and-liquidity-matched control in both halves with both clustered
confidence intervals clear of zero — the first object in this repo's record to
do that. It is also a wide list held for sixty days, which is the opposite of
the product the brief asked for.

---

## 2. What existing systems failed, and why

Confirmed here, on a new cut of the same data:

**The production scanner's ordering axis is inverted, and concentration makes it
worse.** Ranking the prior-work universe by `combined_rs` and taking the top ten
returns **−7.75pp** selection against its own same-date universe in the training
years. The damage scales with concentration — at 20 days in the holdout, the top
5 by that axis returned a **−6.38% median** with a **38% win rate**. Its 60-day
sign is unstable between splits (train −2.97, holdout +1.39), so the reliable
statement is narrower than "always bad": *read as a short list on a short
horizon it is reliably bad, and as an ordering it carries no information worth
having.* This replicates `HISTORICAL_REPLAY_RESULTS_2026_09.md` §4 by a different
route.

**Fundamental and event overlays subtract.** Adding profitability + FCF-positive
+ low-dilution to a momentum/trend rule cut selection from +4.79 to +2.91 in
train and from +0.39 to +1.33 in holdout — i.e. it moved the number around
without ever establishing an edge. Adding a positive post-earnings reaction did
the same and killed the 20-day result outright (+0.02pp in train). This
independently reproduces the M1 memo's measurement that the EPS-beat screen was
worth **−0.30**.

**Concentration fails.** This was the study's central hypothesis and it is
rejected. `trend_anti_junk` at 5/10/15 names produced train selections of
+6.56/+4.79/+3.60pp with both clustered CIs clear of zero — and holdout
selections of −0.46/+0.39/+0.67 with every CI straddling zero, only 46-62% of
non-overlapping window phases positive, and the leave-top-tickers-out check
turning **negative** (+0.39 → −1.01). It is a textbook overfit that looked
disciplined on the way in.

**The most seductive failure.** Raw 12-1 momentum concentrated to 10 names
returned **+13.76pp** selection in the 2025 holdout — the largest number anywhere
in this study. It is 51.6% Technology, the top ten tickers are 53% of all
episodes, 24.5% of names fell more than 25%, and removing five names
(QUBT, RGTI, QBTS, RKLB, PRCH — a quantum-computing cluster) cuts it to +7.24.
Its 2024 selection was **−0.27**. The sector check caught it; without the
fragility battery this would have been reported as the finding of the study.

---

## 3. What existing systems still have value

* **The replay universe is the repo's best research asset.** 7,770 symbols,
  2020→2026, 1,948 delisted names carried to their final traded bar, quarantined
  for sub-penny series and symbol reuse. Almost nothing in this study would be
  credible without it.
* **The measurement discipline is genuinely ahead of the signal work.** The
  panel's own look-ahead audit, the `acceptedDate` stamping of fundamentals, the
  published coverage caveats, and the two-clustering habit are what let this
  study catch its own errors rather than publish them.
* **The broad scan as a discovery net.** Nothing here contradicts using it to
  *find* names. Only its ordering is worthless.
* **The forward tracker's benchmark map** (SPY/QQQ/IWM + sector ETF at
  5/10/20/60d, append-only, idempotent) is exactly the instrument any forward
  test of this study's output would need, and it already exists.

---

## 4. New candidate methods tested

Eight ranking methods × three universes × six list sizes × two horizons = 288
train cells, then a single frozen holdout read. Universes:

| universe | floor | names/week |
|---|---|--:|
| `full_prior_work` | price ≥ $2, median 20d $vol ≥ $1M | 3,015 |
| `liquid_tradable` | price ≥ $5, median 20d $vol ≥ $20M | 1,481 |
| `strict_tradable` | price ≥ $10, median 20d $vol ≥ $50M | 918 |

Methods: `mom_12_1` (the mandated factor baseline), `old_scanner_axis`
(`combined_rs`, replicated for measurement — the live scanner is untouched),
`mom_12_1_style_neutral`, `trend_anti_junk`, `trend_anti_junk_quality`,
`trend_anti_junk_pead`, `low_vol_control`, `composite_z`, and — added after the
tie-break audit in §6 — `style_cell_leaders`.

Every ranking input passes a forward-column deny-list enforced in code
(`research/agent_lab/lab_stats.py:assert_no_lookahead`) and pinned by tests.
Fundamental and event fields were used only inside explicitly coverage-limited
methods.

### Pre-registration

`research/agent_lab/preregistration.py` was frozen after the training grid and
**before** the holdout was read. It names one primary cell, six secondary cells,
four baselines, and a twelve-item bar taken verbatim from the brief's
`VALIDATED_EDGE_CANDIDATE` requirements, made mechanical so it could not be
softened afterwards. It also records the multiplicity problem up front: 288 train
cells were scored, so several will look good by chance, which is why the verdict
rests on one primary cell judged against an out-of-sample year, two clusterings,
and two control cohorts rather than a p-value.

---

## 5. Rejected methods, and why

| method | train 60d | holdout 60d | verdict | why |
|---|--:|--:|---|---|
| `trend_anti_junk` N=10 | +4.79 | +0.39 | INCONCLUSIVE | 7 of 12 checks failed; non-overlap 46% of phases positive; leave-top-tickers-out negative |
| `trend_anti_junk` N=5 | +6.56 | −0.46 | **OVERFIT** | sign flipped out of sample |
| `trend_anti_junk` N=10, full universe | +6.18 | −0.62 | **OVERFIT** | the strongest train cell in the whole grid; worst reversal |
| `composite_z` N=10 | +5.65 | +2.83 | INCONCLUSIVE | both CIs straddle zero in holdout; fails matched random |
| `trend_anti_junk_quality` | +2.91 | +1.33 | INCONCLUSIVE | overlay subtracts from the rule it wraps |
| `trend_anti_junk_pead` | +2.65 | +2.02 | INCONCLUSIVE | very clean list (0.2% of names down >25% at 20d) with no demonstrable excess |
| `low_vol_control` | −0.93 | −3.50 | **REJECTED** | behaved exactly as a style control should — 75% win rate, negative selection |
| `old_scanner_axis` | −1.67 | +2.06 | INCONCLUSIVE | unstable at 60d, reliably negative at 20d |
| `mom_12_1` N=10 | +4.47 | +13.76 | PROMISING_BUT_UNPROVEN | fails the sector check; a single-theme ride (§2) |
| `mom_12_1_style_neutral` N=10 | +2.26 | +4.27 | PROMISING_BUT_UNPROVEN | only the ticker-clustered CI fails |

Three hypotheses were rejected outright:

**H2 — "the winner fingerprint is only a volatility fingerprint because the
universe is full of junk."** Wrong. The winner/loser feature-AUC correlation is
**+0.925 to +0.991 in every universe and both splits**; `atr14_pct` has an AUC
near 0.85 for *both* tails everywhere. Prior work's central negative survives a
direct attack.

The useful residue is the *gap*: winner-AUC minus loser-AUC for trend and
momentum features rises with liquidity (+0.049 full → +0.099 liquid → +0.124
strict) while the gap for volatility features stays at zero. Volatility buys both
tails; trend buys the right one slightly more than the left, and more so among
liquid names. That, not a new fingerprint, is what the surviving method exploits.

**H3 — concentration to 5-15 names.** Rejected (§2).

**H4 — secondary confirms add value.** Rejected (§2).

---

## 6. Best method found — and the audit that shrank it

While checking why `mom_12_1_style_neutral` was flat in list size, the
leave-top-tickers-out output came back alphabetically clustered: AHR, AEM, APP,
AS, AKRO… Ranking 12-1 momentum *within* a volatility × liquidity cell leaves
**~99 names per date tied at a within-cell percentile of exactly 1.0** — one
leader per cell. Any "top 25" of that score is therefore not a ranking; it is 25
arbitrary names from one pool, and my tie-break was alphabetical.

Re-drawing under five deterministic random seeds:

| list size | holdout, alphabetical | holdout, random tie-break (mean, range) |
|---|--:|--:|
| 10 | +4.27 | +2.62 (+0.56 … +4.80) |
| 15 | +4.97 | +2.94 (+2.26 … +3.71) |
| 25 | +4.49 | +2.94 (+2.51 … +3.12) |

**The alphabetical draw was flattering by roughly 1.5pp.** The correction made
the headline number smaller, which is the direction a post-hoc change should
move if it is a correction rather than a search.

It also identified the real object. The thing with an effect is not any top-N —
it is the pool.

### `style_cell_leader_pool`

> In a universe floored at price ≥ $5 and median 20-day dollar volume ≥ $20M,
> take the single highest 12-1 momentum name in each volatility-decile ×
> liquidity-decile cell. About 99 names per week. **Equal weight, unordered,
> held 60 sessions.**

| | train (2022-24) | holdout (2025) |
|---|--:|--:|
| names / week | 99 | 99 |
| dates · unique tickers | 156 · 1,488 | 53 · 855 |
| mean return | +3.43% | +6.18% |
| median return | +1.43% | +3.23% |
| win rate | 54.8% | 58.9% |
| **selection vs same-date universe** | **+2.27pp** | **+2.45pp** |
| selection vs same-date *median* | +1.58pp | +1.94pp |
| date-clustered CI (portfolio) | **[+1.50, +3.01]** | **[+1.63, +3.30]** |
| ticker-clustered CI (typical name) | **[+1.36, +3.22]** | **[+1.03, +3.83]** |
| vs same-date random of same size | [+1.53, +3.04] | [+1.70, +3.38] |
| vs vol × liquidity matched random | **[+1.48, +3.05]** | **[+1.52, +3.20]** |
| matched draws beaten | 67% | 72% |
| dates won | 71% | 75% |
| excess vs SPY / QQQ / IWM | +0.90 / −0.08 / +2.57 | +2.40 / +1.42 / +1.45 |
| names down > 25% | 5.6% | 7.9% |
| 5th-percentile return | −26.3% | −30.6% |
| worst *date* selection | −15.9pp | −4.4pp |
| median / p10 dollar volume | $74M / $25M | $87M / $27M |
| median price | $73 | $70 |
| largest sector | Technology 21% | Technology 22% |
| top-10 ticker share | 4.3% | 6.8% |
| exposure to the old `score=100` trap | 2.4% | 3.8% |

Fragility, all four checks:

* **Every year positive:** 2022 +1.13, 2023 +3.03, 2024 +2.66, 2025 +2.45.
* **Leave-one-month-out:** worst case +1.96 (train), +2.16 (holdout).
* **Leave-top-5-tickers-out:** +1.87 (train), +2.03 (holdout) — removing GE,
  FTAI, SMCI, FICO, AMR and HWM, QUBT, AHR, DB, VRNA barely moves it.
* **Non-overlapping 60-day windows, all 13 phase offsets:** 100% positive in
  train, 92% in holdout (worst offset −0.56).

**Verdict: `PROVISIONAL_RESEARCH_CANDIDATE`.**

The mechanical bar returns `VALIDATED_EDGE_CANDIDATE` — no check fails. I am not
emitting that label, for four reasons the bar cannot see:

1. **The object was specified after the holdout was read.** The direction was
   pre-registered (`mom_12_1_style_neutral` was a frozen baseline cell) but the
   "take the whole pool" form was not. 2025 is no longer out-of-sample for it.
2. **2025 has now been read by five studies in this repo.** Its value as a
   holdout is largely spent, and every CI on it is narrower than the truth.
3. **It is a rediscovery wearing a wrapper.** Cross-sectional momentum,
   neutralised against volatility and liquidity, is a documented factor. The
   contribution is that neutralising it *inside* a tradable universe produces a
   positive per-name interval where raw momentum does not — useful, not novel.
4. **No costs, exits, or sizing anywhere.** Equal-weight, hold-to-horizon, no
   friction model. At ~+2.4pp per 60 days a transaction-cost model could
   plausibly erase a large fraction of it.

### Where it does not work

* **20-day horizon: `INCONCLUSIVE`.** Holdout selection +0.59pp, date-clustered
  CI [−0.12, +1.25], fails both random controls. The effect needs sixty days.
  This matters more than it looks: the brief asks for a manual *review cycle*
  product, and this is not one.
* **Full prior-work universe: `INCONCLUSIVE`.** Holdout ticker-clustered CI
  [−0.68, +2.84]. The liquidity floor is load-bearing for the per-name claim.
* **Strict universe ($10 / $50M): passes**, at a smaller effect (+1.86 train,
  +1.76 holdout). The result degrades gracefully rather than vanishing, which is
  mild evidence it is not a fitting artifact.

---

## 7. Evidence table

Liquid universe, 60-day horizon, at the pre-registered list size of 10 except
where noted. `date` = date-clustered CI lower bound, `tick` = ticker-clustered,
`match` = vol × liquidity matched-random CI lower bound.

| method | sel train | sel hold | med hold | win hold | dn25 | date (ho) | tick (ho) | match (ho) | verdict |
|---|--:|--:|--:|--:|--:|--:|--:|--:|---|
| **style_cell_leader_pool** (99) | **+2.27** | **+2.45** | +3.23 | 58.9% | 7.9% | **+1.63** | **+1.03** | **+1.52** | PROVISIONAL_RESEARCH_CANDIDATE |
| mom_12_1_style_neutral | +2.26 | +4.27 | +4.27 | 59.6% | 5.8% | +2.03 | −0.25 | +1.19 | PROMISING_BUT_UNPROVEN |
| mom_12_1 | +4.47 | +13.76 | +7.41 | 56.4% | 24.5% | +6.82 | +4.58 | +2.21 | PROMISING_BUT_UNPROVEN (sector-fragile) |
| composite_z | +5.65 | +2.83 | +1.41 | 60.0% | 13.0% | −0.21 | −2.51 | −0.11 | INCONCLUSIVE |
| trend_anti_junk **(primary)** | +4.79 | +0.39 | +1.01 | 54.2% | 10.6% | −1.73 | −3.86 | −1.68 | INCONCLUSIVE |
| trend_anti_junk_pead | +2.65 | +2.02 | +3.08 | 59.5% | 7.1% | −0.09 | −1.37 | +0.22 | INCONCLUSIVE |
| trend_anti_junk_quality | +2.91 | +1.33 | +2.83 | 56.4% | 7.6% | −0.43 | −2.69 | −0.70 | INCONCLUSIVE |
| old_scanner_axis | −1.67 | +2.06 | −4.31 | 44.9% | 28.1% | −3.99 | −8.07 | −7.24 | INCONCLUSIVE |
| low_vol_control | −0.93 | −3.50 | +0.77 | 75.1% | 0.8% | −5.41 | −5.45 | −1.56 | REJECTED |

Full grids, both horizons, all three universes, all six list sizes, with random
and matched-random controls, per-year splits, leave-one-month-out,
leave-top-tickers-out, and all-phase non-overlapping windows are in
`cache/research/agent_lab/intermediates/`.

---

## 8. Cost and API implications

**This study made zero provider calls.** Everything ran on cached replay panels
in about twelve minutes of compute.

Running the pool *forward* is a different matter, and the constraint is data, not
compute:

* 12-1 momentum needs **252 bars per name**.
* Of the 1,949 names in the liquid universe, only **58.9% have that depth in the
  live cache** (`cache/prices` + `cache/prices_deep`).
* The replay cache covers **100%** of them — but it is maintained by a research
  backfill script, not a timer, and **2,347 of its 7,770 symbols are already
  stale** (last bar before 2026-09-01).
* A daily refresh of ~2,000 liquid names is roughly **2,000 calls/day ≈ 42,000
  calls/month**, against recent FMP usage of 56k (Sep to date), 88k (Aug) and
  108k (Jul). That is a material addition, not a rounding error.

This restates the M1 memo's precondition with a sharper number: **a shadow report
run on a 59%-complete cache would not be computing this pool, it would be
computing a look-alike.** The staleness guard has to fail loudly, not degrade.

---

## 9. Recommended daily workflow

No change to the current one is recommended by this study. If a forward
measurement of the pool were authorised later, the workflow that the evidence
supports is:

1. Refresh depth for the ~2,000-name liquid universe; **refuse to run** below a
   declared bar-coverage floor.
2. Compute the liquid universe (price ≥ $5, median 20d $vol ≥ $20M), assign
   within-date volatility and liquidity deciles, take each cell's 12-1 leader.
3. Write the ~99 names to an append-only dated ledger, **equal weight, unordered,
   no score column, no top-N cut**.
4. Publish pool-level statistics daily: name count, median dollar volume, sector
   shares, top-10 ticker share, bar coverage.
5. Measure forward at 5/10/20/60d vs SPY, QQQ, IWM and sector ETF using the
   existing tracker. **60 days is the horizon that carries the effect.**
6. No verdict before a pre-declared maturity floor, written before the first
   observation.

---

## 10. What to kill

* **The scanner's ordering axis read as conviction.** `combined_rs` ranking is
  negative-to-noise in every cut here and worse the more you concentrate it. Two
  prior studies said the same. Killing the *ordering* costs nothing; the net can
  stay.
* **The fundamental-quality and post-earnings overlays as selection filters.**
  Measured three times now, in three studies, always subtracting. Keep them as
  descriptive tags if anyone wants them; stop treating them as screens.
* **Any pursuit of a 5-15 name high-conviction list from this data.** Three
  studies have now attacked it from three directions — hand-written fingerprints,
  a 30-family tournament, and this concentration sweep. It fails every time, and
  the reason is measured rather than guessed: the features that pick winners pick
  losers just as hard (AUC correlation +0.93 to +0.99), so shortening the list
  amplifies variance faster than it amplifies signal.

---

## 11. What to keep

* The replay universe, its delisted harvest, and its quarantine rules.
* The two-clustering habit and the matched-random twin. The twin is what
  demoted `composite_z` and what promoted the pool; without it both would have
  been misread.
* The forward tracker's benchmark map.
* The pre-registration + frozen-bar pattern. It is the only reason this report
  says "my primary failed" instead of quietly promoting `composite_z`.
* The look-ahead deny-list. Cheap, and it makes a whole class of error loud.

---

## 12. What not to build next

* **Do not build a ranking inside the pool.** ~99 names tie at the top of the
  score by construction; the M1 programme already established that 13 overlays,
  the factor's own ordering, and a 65-feature ridge all failed to order such a
  pool usefully. There is no reason to believe a fourteenth overlay will.
* **Do not re-tune the concentration thresholds.** The N-sweep failure was not
  near-miss — the sign flipped and the fragility checks turned negative.
* **Do not add another fundamental overlay.** Three independent measurements, all
  negative.
* **Do not read 2025 again as a holdout.** It has been read five times. The next
  out-of-sample evidence has to come from time passing, not from another split.
* **Do not build a dashboard panel for any of this.** Nothing here has earned
  surfacing.

---

## 13. Exact production recommendation

**None.** No production change is recommended.

If the project later wants to advance this, the smallest defensible step is a
*research-only, forward-only, append-only* shadow ledger of the pool, gated on
the cache precondition in §8 and on a pre-registered maturity floor written
before the first observation. That is a measurement, not an integration, and it
should not touch the scanner, M1, HC/EO, Alpha Focus, routing, the dashboard, or
any timer without a separate decision.

---

## 14. Final answer: closer to alpha, or building apparatus?

**Mostly apparatus, with one real increment.**

The increment is genuine and it is small: this is the first object in the repo's
record whose *typical name* beats its own date's universe with a confidence
interval clear of zero, in both halves, against a style-matched control — the
exact test that M1 failed (`ticker-clustered CI negative at every horizon`) and
that every one of the 30 tournament families failed. The mechanism is legible:
neutralising momentum against volatility and liquidity removes the tilt that was
paying the left tail, and the residual trend signal is the part with an
asymmetric winner-minus-loser AUC gap. It is worth roughly **+2.4pp per 60 days**
before costs, on a 99-name equal-weight basket of liquid stocks.

Everything else is apparatus, and the apparatus is what actually earned its keep
today. The pre-registration caught my primary hypothesis failing. The sector
check caught a +13.76pp result that was a quantum-computing cluster. The
tie-break audit caught my own implementation flattering itself by 1.5pp. The
matched-random twin separated a real residual from a style tilt. Four traps, four
catches — and had any one of them been missing, this report would have contained
a confident, wrong headline.

The uncomfortable part is the shape of the answer. The brief asked for 5-15
liquid names per review cycle with clear reasons, better than random and better
than M1. What the data supports is **99 names, unordered, with no reason field
beyond "leads its style cell on twelve-month momentum", held for sixty days**.
That is better than M1 on the one statistic M1 could never clear, and it is not
the product that was asked for. The gap between those two sentences is the honest
state of this project: the measurement apparatus is now good enough to tell the
difference, and the data still does not contain a short list.

---

*Artifacts: `cache/research/agent_lab/autonomous_alpha_lab_latest.json` and
`cache/research/agent_lab/intermediates/`. Reproduce with
`GEM_TRADER_SKIP_DOTENV=true .venv/bin/python -m research.agent_lab.liquid_concentration_lab all`
(zero provider calls; reads cached replay panels only). Guards in
`tests/unit/test_agent_lab_liquid_concentration.py` (39 tests).*

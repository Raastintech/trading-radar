# Alpha Discovery Programme — four studies, one conclusion

*Replay window 2022-01-07 → 2025-12-31, 209 weekly scan dates. Run 2026-09-06.
Harnesses: `research/backtests/alpha_tournament.py`,
`m1_conviction_layer.py`, `m1_missing_data_audit.py`, `m1_surprise_overlay.py`.*

> **RESEARCH ONLY.** Replay evidence over a fixed historical window. It is
> **not** live forward evidence, **not** backtest evidence for Phase 4B, and
> **not** a trade signal, gate change, or threshold recommendation. Each study
> emits verdicts on its own ladder — `TournamentVerdict`, `ConvictionVerdict` —
> deliberately disjoint from the live evidence ladder. `VALIDATED_EDGE` cannot
> be emitted from any of these harnesses. Nothing here changed a scanner,
> score, gate, threshold, HC/EO rule, routing decision, dashboard artifact,
> cron entry, or ledger.

---

## 1. Where this starts

`HISTORICAL_REPLAY_RESULTS_2026_09.md` found the broad scanner had negative
selection against its own universe. `ALPHA_RECONSTRUCTION_LAB_2026_09.md` then
found that top-1% winners share their pre-move features with bottom-1% losers
(AUC correlation +0.97) and concluded the winner fingerprint was a *volatility*
fingerprint.

That conclusion was drawn under two choices this programme abandoned:

* it judged cohorts on **median** selection, which is the wrong statistic for a
  lane whose thesis is a fat right tail — an equal-weight research list earns
  the **mean**;
* it tested bespoke hand-written fingerprints and never tested the
  best-documented factor in the equity literature.

Both changes mattered. The first overturned a headline; the second produced the
programme's only positive result.

---

## 2. Study 1 — Alpha Discovery Tournament (30 families)

Thirty strategy families across twelve groups, each written as a literal rule
before scoring, none inheriting an old scanner lane, score or threshold.
Discovery 2022-2024; 2025 read once.

| verdict | n |
|---|--:|
| `TRUE_ALPHA_CANDIDATE_POOL` | **1** |
| `PROMISING_BUT_UNPROVEN` | 5 |
| `SPECIAL_SITUATION_ONLY` | 3 |
| `INCONCLUSIVE` | 13 |
| `REJECTED_OVERFIT` | 7 |
| `REJECTED_NEGATIVE_SELECTION` | 1 |

### The one pass: classic 12-1 momentum

The twelve-month return excluding the most recent month — the canonical
cross-sectional momentum factor, absent from every earlier study here, which
tested only 20/63-day relative strength (the short-horizon window contaminated
by reversal).

* mean selection positive in **all four years**: +1.14, +1.76, +0.54, +2.25
* train CI [0.56, 1.74], holdout CI [1.38, 3.14] — both clear of zero
* beats a **volatility/liquidity-matched twin** in both halves: holdout +1.97
  [1.26, 2.65], winning 77% of dates
* winner-capture lift 1.27 against loser lift 1.06; holds **24.9% of all
  top-decile winners**
* largest sector 22.2%, top-10 tickers 1.3% of episodes — not a sector or
  outlier bet
* ~600 names/day; the blended surviving-family pool reaches 763, filling a
  top-50 and top-100 on 100% of dates

### What died

* **Quality compounder went 0 for 3** — all `REJECTED_OVERFIT`, positive
  2022-2024 and negative in 2025. Quality was the most *fragile* group, not the
  safest.
* **Value + quality** repeated the pattern (train +2.46 → holdout −1.86).
* **Breakout / base structure** had negative selection; volume-confirmed
  breakouts failed outright.
* **Institutional accumulation** (up/down volume) was flat — no separation.
* **Controlled volatility** was real but sparse and did not beat its own
  volatility-matched twin.

### A correction to the Reconstruction Lab

Measured on the **mean** rather than the median, volatility deciles 1-9 all
carry positive forward returns and the tail ratio *rises* monotonically with
volatility. Only decile 10 inverts. Nano/micro caps have the highest mean
(+2.23) and highest tail ratio (1.93) in the study despite the worst median.
"Volatility is a trap" was wrong as stated; "the top volatility decile is where
the payoff inverts" is what the data says.

Two ladder gaps were found and fixed mid-run and are disclosed in the artifact:
the commodity family first passed as `TRUE_ALPHA` on 4 names/date and 100%
two-sector concentration. The brief's own "not one year, one sector, one
outlier" requirement was encoded, and daily-list capacity was made a hard
router — a lane that cannot fill a list is not a pool.

---

## 3. Study 2 — M1 Conviction Layer

M1 works as a basket. Its **ticker-clustered CI is negative** while its
date-clustered CI is positive: the basket beats the tape, the typical name does
not. A human reading a shortlist experiences the second. So: can anything rank
*inside* the pool?

Thirteen overlays, judged against a **random same-size draw from the same M1
pool on the same dates** — the only control that isolates an overlay, since
beating the universe is something M1 already does.

| verdict | n |
|---|--:|
| `PROMISING_CONVICTION_OVERLAY` | **0** |
| `USEFUL_AVOID_FILTER` | 1 |
| `INCONCLUSIVE` | 10 |
| `REJECTED_OVERFIT` | 2 |

Three findings:

1. **The variance structure is inside M1 too.** Winner/loser AUC correlation
   within the pool: **+0.947**.
2. **Every risk filter trims both tails proportionally.** Removing the most
   volatile fifth cuts the −25% rate 36% and the +50% rate 38%; prior-crash
   names 37%/41%; real-liquidity 30%/29%. Only `fundamental_quality`
   (profitable + FCF-positive + dilution <5%) tilted favourably at 28%/19%.
3. **M1's own ordering is not a ranking signal.** Ranking by momentum strength
   inside the momentum pool does not beat a random draw at any list size
   (top-25 +1.34, CI [−0.46, 3.08]).

**Concentration makes it worse per name.** Holdout top-10 returned **+12.8%
mean with a −6.3% median**; the share of names down more than 25% rises from
20% at top-200 to 34% at top-10. A shorter list amplifies the basket effect and
degrades the typical name — the opposite of what a conviction list claims.

---

## 4. Study 3 — Missing Conviction Data Audit

Rather than write a fourteenth overlay, measure what is missing. Zero provider
calls.

### The prize is enormous

A perfect ranker inside M1 (oracle, cheats by construction) on a top-25:
**+71.1% train / +103.0% holdout**, against a random M1 draw's +2.1% / +4.9%.
Ranked backwards it loses 46-49%. The dispersion is real and so is the damage
from ranking badly.

### The available data is not quite exhausted

A ridge model over **all 65 available as-of features** (131 terms), fit on train
years against the within-date rank of forward return:

| split | AUC (tail) | top-25 vs random | ticker CI |
|---|--:|--:|---|
| holdout 2025 | 0.436 | +2.65 | **[1.53, 7.48]** |
| walk-forward 2024 | 0.459 | +2.24 | **[1.23, 5.88]** |
| walk-forward 2023 | 0.490 | +0.12 | [−1.33, 6.16] |

This is the **first positive ticker-clustered reading anywhere in the
programme** — 2 of 3 out-of-sample years, stable across ridge alpha 5→500. The
limitation was partly the *rules*, not only the data. But the AUC sits **below
0.5**: the model ranks by avoiding the tail, not finding it, and captures ~3% of
the oracle headroom.

*(An earlier version of this measurement scored 2023 and 2024 with a model fit
on all train years and labelled them walk-forward. They were in the training
data. Corrected: each test year is now scored by a model fit only on years
before it.)*

### Why the rest is missing: the winners are events

| | winners | losers | whole pool |
|---|--:|--:|--:|
| median biggest up day | +14.2% | +9.1% | +7.3% |
| had a +25% day | **20.6%** | 4.6% | 4.4% |
| had a +15% gap up | **21.8%** | 5.1% | 6.0% |
| no day above 10% | 29.4% | 56.7% | 67.7% |

One session is **more than a quarter of the total move for 63.9% of winners**
and more than half for 24.8%. No price history can anticipate an announcement.
That explains the sub-0.5 AUC, the proportional tail-trimming, and why thirteen
structural overlays found nothing.

---

## 5. Study 4 — Earnings Surprise Overlay (52 provider calls)

The audit's recommended experiment, run under explicit approval. FMP's
`/earnings-calendar` is date-ranged rather than per-ticker, so four years of
`epsActual` / `epsEstimated` / `revenueActual` / `revenueEstimated` cost **52
calls** against a 500 cap — 184,777 rows, 0 errors.

Point-in-time rule enforced: an event is usable only from the first trading
session **strictly after** its report date (no time-of-day field exists). 400
sampled rows re-audited against the raw calendar; none used a future or stale
event. Coverage: **81.3%** of M1 rows carry a usable surprise.

| verdict | n |
|---|--:|
| `PROMISING_CONVICTION_OVERLAY` | **0** |
| `USEFUL_EVENT_ANNOTATION` | 2 |
| `INCONCLUSIVE` | 10 |
| `REJECTED_OVERFIT` | 1 |
| `REJECTED_DATA_UNSAFE` | 3 |

**No overlay beat a random M1 draw at top-50 in train with a CI clear of zero.**
The three refused as `REJECTED_DATA_UNSAFE` covered 4-8% of pool rows — the
fresh-event combinations that were, on paper, the most interesting.

### The attribution test

The best-scoring overlay bundles an EPS beat with a profitability screen the
previous study already had:

| cohort | rows | mean | vs pool |
|---|--:|--:|--:|
| fundamental_quality alone | 32,277 | +3.99 | **+1.01** |
| fundamental_quality + EPS beat | 21,224 | +4.60 | **+1.18** |
| **EPS beat alone** | 68,266 | +2.95 | **−0.30** |

An EPS beat on its own is worth slightly *less than nothing*. The screen carries
+1.01 of the bundle's +1.18; the beat's incremental contribution is +0.65pp with
CI [0.04, 1.25] — barely off zero.

Other answers: **revenue** surprise mildly beats EPS (+0.54 [0.19, 0.92] vs
−0.30); both-beat does nothing; post-earnings hold and follow-through made
results *worse* (−1.54 train, −2.01 holdout) and were too sparse; negative
surprise **fails as an avoid filter** — dropping EPS-miss names cuts the +50%
rate 44% but the −25% rate only 24%, removing more winners than losers.

### Provenance risk, disclosed not dismissed

`epsEstimated` is the value the provider serves today; its `lastUpdated` sits a
median ~2 years after the report, so it cannot be *verified* as pre-report
consensus. Two checks argue against silent back-filling: the EPS beat rate is
54.0% (actuals snapped in would show ~100%) and only 4.4% of rows have estimate
exactly equal to actual. Reassurance, not proof.

---

## 6. What the four studies say together

**Three independent data classes produced the same structure.** Volatility and
liquidity filters trim both tails proportionally. So do earnings-surprise
filters. So does everything inside the M1 pool. This is not a coincidence of one
dataset; it is the shape of the problem.

**The one real finding is a well-known factor, not a discovery.** 12-1 momentum
survives an out-of-sample year, a style-matched twin, and every concentration
check, with capacity to fill a daily list. Its edge is roughly +1 to +2pp per 60
days against the same-date universe, and it lives in the **mean**, not the
typical name.

**The tail is unreachable with what we hold.** Two thirds of M1's big winners
make a quarter of their move in one session, and the earnings experiment
established that those sessions are *not* the scheduled ones. That points at
unscheduled events — contracts, clinical read-outs, M&A — which this repo cannot
observe historically at any call count.

**A small per-name signal does exist**, but it is found by fitting rather than
by hand-written rules, it works by dodging the left tail, and it is worth ~3% of
the available headroom.

---

## 7. What should happen

| | |
|---|---|
| **Sunset** | The old scanner's *ranking axis*. `combined_rs` saturates at 100 exactly where forward returns are worst, and the short-window RS it is built from is the contaminated version of the signal that works at 12-1. The fix is a different lookback, not a threshold. |
| **Keep as discovery only** | The broad scan itself. As a net it is fine and cheap; as an ordering it is negative (baseline: −1.83pp against the pool it drew from). |
| **Build** | A wide, equal-weight, momentum-anchored shadow watchlist: top-quintile 12-1, trend filter, exhaustion excluded, 50-100 names, **no conviction ordering**. Carry the fitted rank as a *risk* ordering only, and "profitable, cash-generative, non-diluting, beat last quarter" as a tag on ~17% of the list — labelled so the fundamental half gets the credit. |
| **Do not build** | A 10-25 name high-conviction shortlist drawn from M1. The evidence actively contradicts that framing. |

---

## 8. Limitations

1. **Market cap covers 57% of rows**, and the gap is not random — it is names
   the old scanner never surfaced, including S&P Global, Apollo, CRH, Coterra,
   Cirrus Logic. Every valuation and quality family inherits that bias. ~1,400
   provider calls would close it.
2. **Fundamentals are restatement-contaminated** at source and their coverage is
   the old scanner's episode set, which is partly forward-looking.
3. **Sector labels are current metadata** (43-49% coverage), not point-in-time.
4. **No options, news, social, 13F, short-interest or intraday history** exists
   for this window.
5. **Programme-level researcher degrees of freedom.** This is the fifth pass
   over one window. Every split is honoured mechanically, but no split removes
   the contamination of having looked this many times. The 2025 holdout in
   particular has now been read by four studies.
6. **No costs, exits or sizing.** Equal-weight, hold-to-horizon.

---

## Bottom line

The programme found one thing that works and established, at reasonable cost,
why the rest does not. Momentum ranks a **basket**; nothing yet ranks a **name**.
The winners are made by events this repo cannot see, the cheapest event class
that *is* visible was tested and failed, and the honest product is a wide
equal-weight watchlist measured forward — not a conviction board, and not a
moonshot finder.

Nothing here is a validated edge, and nothing here should change a live rule.

---

*Artifacts: `cache/research/alpha_tournament_*`, `m1_conviction_*`,
`m1_missing_data_*`, `m1_surprise_*` with reports under `logs/`. Reproduce with
`GEM_TRADER_SKIP_DOTENV=true .venv/bin/python -m research.backtests.<module> all`
(zero provider calls; the surprise fetch is separately gated behind
`--execute-fetch`). Guards: 127 tests across
`tests/unit/test_alpha_tournament.py`, `test_m1_conviction_layer.py`,
`test_m1_missing_data_audit.py`, `test_m1_surprise_overlay.py`.*

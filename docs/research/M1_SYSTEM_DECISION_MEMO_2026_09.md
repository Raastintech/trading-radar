# M1 System Decision Memo

*Written 2026-09-06. Decision memo — no code changes. Evidence base:
`ALPHA_RECONSTRUCTION_LAB_2026_09.md`, `ALPHA_DISCOVERY_PROGRAMME_2026_09.md`,
and the four harnesses under `research/backtests/` (`alpha_tournament.py`,
`m1_conviction_layer.py`, `m1_missing_data_audit.py`, `m1_surprise_overlay.py`).*

> **RESEARCH ONLY.** Everything below rests on replay evidence over one fixed
> historical window (2022-01-07 → 2025-12-31, 209 weekly dates). It is **not**
> live forward evidence, **not** backtest evidence for Phase 4B, and **not** a
> gate, threshold, score, or routing recommendation. No conclusion here earns
> `VALIDATED_EDGE` and none of it may be pooled with the live forward ledger.

---

## 0. The one-paragraph version

M1 — the within-date top quintile of 12-1 momentum — is a real but small
basket effect worth roughly **+1 to +2pp per 60 days** against the same-date
universe, and it is the only thing four studies found. Its **ticker-clustered
confidence interval is negative at every horizon** (60d: [−4.60, −3.55]),
which means the basket beats the tape while the typical name in it does not.
Thirteen conviction overlays, an earnings-surprise dataset covering 81.3% of
pool rows, and a 65-feature ridge model all failed to rank names inside it in
any way a human could act on. The reason is measured, not guessed: two thirds
of M1's big winners make over a quarter of their move in a single session, and
the cheapest observable class of scheduled event was tested and returned a
clean negative. **Build a wide, equal-weight, unordered shadow list or build
nothing.** Do not build a shortlist.

---

## 1. What the system can honestly do today

Five things, in descending order of how well they are evidenced.

**1. Draw a wide momentum source pool that beats its own universe on the mean.**
12-1 momentum is positive in all four years (+1.14, +1.76, +0.54, +2.25),
train CI [0.56, 1.74] and holdout CI [1.38, 3.14] both clear of zero, and it
beats a **volatility- and liquidity-matched twin** in both halves (holdout
+1.97 [1.26, 2.65], winning 77% of dates). That last control is what separates
it from every other family tested — the rest were factor tilts wearing a
rule's clothes. It is not a sector bet (largest sector 22.2%) or an outlier bet
(top-10 tickers = 1.3% of episodes), and it fills a list on 100% of dates at
~600 names/day.

**2. State honestly that this is a rediscovery, not a discovery.** 12-1
momentum is the canonical cross-sectional momentum factor. The finding is that
*this repo's earlier work missed it* by testing only 20/63-day relative
strength — the short-horizon window contaminated by reversal. That is a useful
correction to our own record. It is not an edge anyone else lacks.

**3. Name cohorts that reliably lose.** Two are negative across train and
holdout with CIs clear of zero: the old scanner's `score=100` saturation zone
(train −7.01 [−8.77, −5.32], holdout −3.80 [−6.81, −0.70]) and the
cheap/volatile/broken cohort. Both are cheap to compute daily. "This name sits
in a historically money-losing bucket" is the best-supported *output* the
programme produced — better supported than any positive selection claim.

**4. Measure a candidate list forward against the right benchmarks.**
`research_watchlist_forward_tracker.py` already computes SPY/QQQ/IWM and
sector-ETF relative returns at 5/10/20/60d on an append-only, idempotent
ledger. The measurement infrastructure is genuinely ahead of the signal
research — which is the correct order.

**5. Refuse to manufacture evidence.** The surprise study refused three overlay
combinations as `REJECTED_DATA_UNSAFE` — covering 4-8% of pool rows, and on
paper the most interesting ones. Two studies declared hypotheses *untestable*
rather than approximating them. A mislabelled walk-forward was caught, fixed,
and pinned by a test. This discipline is an asset and should not be traded away
for a more exciting headline.

---

## 2. What the system cannot do today

**It cannot rank names inside M1.** This is the central negative and it was
attacked three independent ways:

| attempt | result |
|---|---|
| 13 hand-written overlays vs. random same-size M1 draw | **0** cleared the per-name bar |
| M1's own ordering (rank by momentum strength) | top-25 +1.34, CI **[−0.46, 3.08]** — no better than random |
| 65-feature / 131-term ridge, walk-forward | AUC for tail membership **0.436–0.490 — below 0.5** |

The ridge model is the honest edge case. It produced the programme's *first*
positive ticker-clustered reading (holdout +2.65 [1.53, 7.48]; 2024 +2.24
[1.23, 5.88]; 2023 +0.12, straddling zero), stable across ridge alpha 5→500.
So the limitation was partly the **rules**, not only the data. But its AUC sits
below 0.5: it ranks by **dodging the left tail**, not by finding the right one,
and it captures about **3% of the available headroom**. That is a risk
ordering, not a conviction ordering, and it must never be presented as one.

**It cannot concentrate.** Concentration makes the product worse per name, not
better. Holdout top-10 returns **+12.8% mean against a −6.3% median**, and the
share of names down more than 25% rises from 20% at top-200 to **34% at
top-10**. Shortening the list amplifies the basket effect and degrades the
typical name — the exact opposite of what a conviction list claims to do.

**It cannot separate the tails with any filter it holds.** Three independent
data classes produced the same structure. Removing the most volatile fifth cuts
the −25% rate 36% and the +50% rate 38%. Prior-crash names: 37%/41%. Real
liquidity: 30%/29%. Earnings-miss names: cuts the +50% rate **44%** and the
−25% rate only **24%** — it removes more winners than losers. Winner and loser
fingerprints inside M1 correlate **+0.947**. Every filter is a variance filter
wearing a quality filter's name.

**It cannot see what makes the winners.** 63.9% of M1's top-decile winners make
more than a quarter of their 60-day move in one session; 24.8% make more than
half. 20.6% had a +25% day (pool: 4.4%); 21.8% had a +15% gap up (pool: 6.0%).
Only 29.4% of winners never had a 10% day, against 67.7% of the pool. **No
price history anticipates an announcement.** The earnings experiment then
established that those sessions are largely *not* the scheduled ones — which
points at contracts, clinical read-outs, M&A and index changes, none of which
this repo can observe historically at any call count.

**It cannot compute M1 daily from the live cache.** Measured today: `cache/prices`
has 5,887 parquets at a **median 113 bars** — 6 of 25 sampled reach the 252 bars
that 12-1 momentum requires. `cache/prices_deep` has only **411** files. The
deep, survivorship-corrected `cache/replay_prices` (7,770 tickers, 2020 →
2026-09-04) *can* support it, but it is maintained by a research backfill
script, not by any timer. **There is no daily job that keeps M1's input data
alive.** This is an operational fact, not a modelling one, and it gates
everything in §3.

**It cannot claim any of this is clean.** This is the **fifth pass over one
window**, and the 2025 holdout has now been read by four studies. Market cap
covers 57% of rows and the gap is not random — it is names the old scanner
never surfaced (S&P Global, Apollo, CRH, Coterra, Cirrus Logic), so every
valuation and quality family inherits that bias. Fundamentals are
restatement-contaminated at source. Sector labels are current metadata. No
costs, exits, or sizing anywhere. No split removes any of this.

---

## 3. Should we build a shadow daily M1 report now?

**Yes — with one precondition, and only in the narrow form described in §4.**

The case for is straightforward: M1 is the only research output in the repo's
history with a positive out-of-sample year, a style-matched control it beats,
and daily capacity. Its evidence is entirely in-sample to a single window, so
the only thing that can advance it is **independent forward observation**,
which cannot start until something starts recording. Nothing else in the queue
generates new information; a daily record does. The tracker, the benchmark
map, and the append-only ledger pattern already exist, so the build is small.

The case against is that this is the fifth pass over one window, and a
forward record of a factor that is already public tells us about **our
implementation**, not about the factor. That is worth knowing and it is worth
roughly one afternoon — not a phase.

**The precondition is data, not code.** M1 needs 252 bars per name across a
~3,000-name universe, and the live cache does not carry them. Either the deep
replay cache gets a maintained daily refresh, or the report will silently
degrade to whatever handful of names happens to have depth — producing a list
that looks like M1 and is not. **Do not ship the report before the cache
refresh that feeds it is a scheduled, monitored job with a staleness guard that
fails loudly.** A shadow report running on a stale or thin cache is worse than
no report, because it accumulates a forward ledger of a signal we did not
actually compute.

Two things this decision explicitly is not: it is not a promotion step, and it
is not a commitment to act on the output at any future date. The forward record
either confirms the replay result or it does not, and a null result after
forty-five sessions is a perfectly acceptable outcome that should be published
as such.

---

## 4. What the report should contain

**Shape:** wide, equal-weight, **unordered**, 50-100 names, written daily to an
append-only dated ledger, measured forward, surfaced nowhere that ranks.

| element | why it is in |
|---|---|
| **Membership**: within-date top quintile of `mom_12_1`, top 50-100 by *nothing* — a random or alphabetical draw from the quintile | The pool is the finding; any ordering is not |
| **A trend filter and an exhaustion exclusion** | The `score=100` zone is the only reliably negative cohort we have; excluding it is the best-evidenced action available |
| **Equal weight, hold-to-horizon, stated explicitly** | The +1 to +2pp lives in the mean of an equal-weight basket and nowhere else |
| **Forward returns at 5/10/20/60d vs SPY, QQQ, IWM and sector ETF** | Raw returns of a momentum basket in a rising market are meaningless; the existing tracker already does this |
| **The fitted ridge rank, labelled `risk_order`, presented ascending-risk and never as a top-N** | It is real (2 of 3 walk-forward years positive) and it works by tail-avoidance; the label must say so |
| **A `fundamental_quality` tag on the ~17% of the list that carries it** (profitable, FCF-positive, dilution <5%, beat last quarter) | The only overlay that tilted favourably (28%/19% tail trim). Must be tagged, not ranked, and the attribution must ride with it: the profitability screen carries +1.01 of the bundle's +1.18; the EPS beat alone is worth **−0.30** |
| **Pool-level statistics every day**: n names, median dollar volume, sector concentration, top-10 ticker share | The tournament caught a family passing on 4 names and 100% two-sector concentration. Ship the check, not just the list |
| **A data-integrity header**: cache as-of date, bars available, % of universe with ≥252 bars, and a `DEGRADED`/`BLOCKED` status | Per §3. The report must be able to refuse to run |
| **A standing caveat block**, verbatim from this memo's §2 | So no future reader reconstructs conviction from a list that never claimed it |
| **A pre-registered verdict ladder and maturity floor** — no verdict before 45 sessions of matured 60d outcomes | Written before the first observation, so the bar cannot move afterwards |

---

## 5. What the report must forbid

These are not style preferences. Each one corresponds to a specific measured
failure.

1. **No conviction ordering, no score, no rank column presented as quality, no
   stars, no tiers.** M1's own ordering does not beat a random draw at any list
   size. Any ordering is a claim the evidence contradicts.
2. **No top-10, top-25, or "best ideas" cut.** Holdout top-10: +12.8% mean,
   −6.3% median, a third of names down over 25%. Concentration is measurably
   harmful here.
3. **No merge with the HC shortlist, the EO watch, program routing, the
   Gatekeeper, or the live forward ledger.** M1 is a separate hypothesis with a
   separate ladder. `high_conviction_forward` already reads
   `NO_IMPROVEMENT_OVER_PROGRAM_CANDIDATES`; pooling would launder one negative
   into another.
4. **No promotion path, no gate, no threshold, no routing.** Nothing in the
   programme is `VALIDATED_EDGE` and the harness cannot emit it.
5. **No entry, stop, target, size, or holding-period language.** Research-only
   mode is permanent (2026-06-13), and the six-category scanner's forbidden-output
   contract applies here unchanged.
6. **No re-tuning of the quintile, the trend filter, or the exhaustion rule
   against the forward record.** That converts forward evidence into a sixth
   in-sample pass and destroys the only asset the report is being built to
   create.
7. **No presenting the ridge rank as skill.** Its AUC is below 0.5. If it cannot
   be labelled `risk_order` with the tail-avoidance mechanism stated on the
   same screen, it should be left out.
8. **No dropping names for looking risky.** Every risk filter tested trims both
   tails proportionally; each one removes real return along with real loss.
9. **No verdict before the pre-registered maturity floor**, and no reading of
   the record between now and then as a trend.
10. **No claim of novelty.** The report surfaces a textbook factor. Anything
    that implies proprietary insight is false.

---

## 6. What data is required to attempt true per-name ranking

The oracle bounds the prize precisely: a perfect ranker inside M1 on a top-25
is worth **+71pp per 60 days in train and +103pp in the holdout**, against a
random M1 draw's +2.1 and +4.9. Ranked backwards it loses 46-49%. The
dispersion is enormous and so is the damage from ranking badly. Everything we
hold captures ~3% of it.

Closing the rest requires data in three classes, and the move-anatomy result
says which class matters:

**Class 1 — unscheduled corporate events (the binding constraint).** Contract
wins, clinical read-outs, M&A, index inclusion, partnership announcements.
Timestamped, entity-mapped, and classified by event type. This is where the
single sessions are. Nothing else on this list addresses the actual mechanism.

**Class 2 — forward expectation revisions.** Analyst EPS/revenue estimate
revisions and revision *breadth*, as a point-in-time snapshot series — not a
current pull. Realised surprise was tested and failed; the revision series is a
genuinely different signal with independent literature behind it. It is also
**the single easiest way to manufacture a fake edge in this entire programme**:
a consensus reconstructed from today's data has been revised after the fact,
and the resulting backtest will look excellent and be worthless.

**Class 3 — positioning and constraint.** Short interest, borrow cost, free
float, and options positioning. These are the mechanically asymmetric
quantities — the ones with a structural reason to separate tails rather than
just measure variance. Options history does not exist for the replay window at
any price; the Phase 1J collector accrues forward and reopens the question
around 2027. Short interest and borrow are unverified at our providers.

Two hygiene items are separate from the above and worth naming so they are not
confused with signal: **~1,400 calls** would take market-cap coverage from 57%
to ~100% of liquid rows, removing a known bias without adding information; and
restatement-safe, as-originally-filed fundamentals would need a different
vendor entirely.

---

## 7. Highest expected value missing source

**Point-in-time analyst estimate revisions.**

It wins on the argument, not on comfort. Unscheduled events (Class 1) address
the real mechanism and would matter more if we had them — but there is no
classified event history for 2022-2025 at any call count, headline coverage
thins exactly where the microcap moves are, and any classifier we train today
knows how the stories ended. That is a lookahead hazard that is easy to
introduce and hard to detect. Expected value is high and probability of
obtaining clean evidence is low.

Revisions are the opposite trade: the mechanism is documented independently of
us, the signal is forward-looking rather than a description of the past (unlike
13F, which lags 45 days and describes positions up to 135 days old), and it is
per-ticker retrievable *if* a history endpoint exists — roughly 3,600 calls,
against the 52 the whole surprise study cost.

**Its risk is the reason to rank it first, honestly stated.** The endpoint is
`UNVERIFIED` offline, and reconstructing a point-in-time consensus from a
current pull is the fake-edge machine described in §6. So the expected value is
conditional and the condition is checkable cheaply: **before spending 3,600
calls, spend a handful establishing whether the provider serves a dated
revision history with an as-of stamp, or only a current snapshot.** If it is a
current snapshot, the class is dead here and the correct answer is to say so
and stop — not to approximate it.

Runner-up, for completeness: **short interest + insider buying together** (not
13F). `/insider-trading` is already wired per-ticker. Short interest is the
more interesting of the two because it is explicitly asymmetric, but it is
unverified and settles twice monthly with a ~9-day publication lag.

---

## 8. Cheapest next experiment

**A provider-capability probe, at roughly 5-20 calls: does FMP serve a dated,
as-of-stamped analyst estimate revision history?**

It is cheap, it is decisive, and it gates a 3,600-call decision. The outcome is
binary and both branches are useful — a dated history opens Class 2 for real
testing; a current-only snapshot closes it permanently and the class stops
consuming planning attention.

Two genuinely free alternatives if provider calls are unwelcome right now:

- **Re-target the ridge model at tail membership instead of median rank.** The
  existing model was fit against within-date rank of forward return — a typical-
  outcome target — and its own artifact says so. Its sub-0.5 tail AUC may be a
  consequence of the target choice rather than a property of the data. Refitting
  against a top-decile indicator costs zero calls and directly tests that
  distinction. It is likely to fail, and a clean failure retires the "we didn't
  ask the right question" objection permanently.
- **Publish the daily M1 shadow ledger** (per §3-§5), which starts the clock on
  the only clean evidence any of this can ever receive.

Cheapest of all, and worth stating: **the float proxy is computable at zero
cost** from share counts already held — and should **not** be built, because
liquidity filters already failed and float is a close cousin of liquidity.
Cheap is not the same as worth doing.

---

## 9. Most ambitious next experiment

**Build a point-in-time unscheduled-event history for the replay universe and
test whether event *type* separates M1's tails.**

The move-anatomy result identifies this as the only class that addresses the
actual mechanism. Two thirds of the winners are made in single sessions that
are not the scheduled ones. If anything can convert M1 from a basket into a
ranker, it is here.

It is ambitious because it is genuinely hard, and the honest framing is that it
is **more likely to fail than succeed**:

- ~3,600 calls per news pull and plausibly **10,000+** for usable windowed
  history — a scale that competes with the entire FMP monthly allowance rather
  than fitting inside it.
- The feed is headlines, not classified events. Classification is a modelling
  problem, and every classifier we can build today knows what happened next.
  Preventing that lookahead requires more discipline than any stage of this
  programme has yet needed.
- Headline coverage thins badly for microcaps — precisely where the moves are.
  The data is weakest exactly where the question lives.
- Three studies have now found the same variance structure at three zoom
  levels. **The prior should be that a fourth dataset fails the same way.**

If it is attempted, it should be pre-registered as a kill-gate design: a
feasibility stage that measures headline coverage on the *winner* cohort
specifically before any classifier is written, with a declared coverage floor
below which the project stops. A cheaper substitute worth considering first is
a narrow slice — one event type (FDA calendar or index-change history) on one
sector — testing the method at 1/50th the cost.

The genuinely patient alternative: **the options collector is accruing
point-in-time chain history right now** and reopens Class 3 around 2027 with no
lookahead risk at all, because it was recorded forward. That is the ambitious
experiment that requires waiting rather than spending.

---

## 10. Old scanner components to sunset or demote

Framed by what the evidence supports, not by what is unloved. Note that
`docs/research/ALPHA_DISCOVERY_PROGRAMME_2026_09.md` §7 already carries the
sunset column; this section states it as a decision.

**Sunset — the ranking axis, not the scanner.**

- **`combined_rs` as an ordering** (`research/research_scanner.py:1962-2058`:
  `rs_63d * 0.6 + rs_20d * 0.4`, sorted descending). Two independent studies
  show this axis is inverted. The decile table is an inverted U — both tails
  lose. Worse, it is built from the *contaminated* lookback: 20/63-day relative
  strength is the reversal-polluted window, and the signal that works lives at
  12-1. **The fix is a different lookback, not a threshold.**
- **`score = min(100, 50 + combined_rs * 0.8)`** (line 2031) and the saturation
  zone it creates. The zone is 1.27% of rows, holds a genuine **5× winner-rate
  lift**, and loses money anyway because it holds even more of the bottom 1%:
  train −7.01 [−8.77, −5.32], holdout −3.80 [−6.81, −0.70]. It was not
  selecting badly at random — it was selecting variance and being paid the left
  tail for it. Retain it **inverted, as an avoid flag**; retire it as a score.

**Demote to discovery-only — the broad scan itself.** As a net it is cheap and
fine. As an ordering it is negative (board selection −3.40 [−4.33, −2.50]
against the universe it drew from). Keep it running; stop reading its order as
conviction. Its low recall of the winner cohort (5.07% of episodes) is not, on
this evidence, a defect worth chasing — those winners were disproportionately
the left tail too.

**Demote — the high-conviction shortlist as a conviction product.** Its own
forward tracker reads `NO_IMPROVEMENT_OVER_PROGRAM_CANDIDATES` (shortlist 10d
excess vs SPY 2.53 against a 31.76 baseline, n=514 over 47 dates), and the
replay put the HC cohort at −2.67 [−4.19, −1.24]. Independently, the M1 work
says a 10-25 name shortlist is the **wrong shape** regardless of how the names
are chosen. Keep the multi-factor qualification as a **tag**; retire the
"shortlist" framing and the ranked 10.

**Preserve — `long_term_asymmetric`.** The only old lane with a positive point
estimate in both the September replay and the reconstruction lab (+0.72,
CI [−0.55, +1.89] — includes zero, so *consistent*, not confirmed). It is a
low-extension, non-trap cohort. Leave it alone; do not tune it.

**Preserve unchanged — `emerging_outlier_watch` excluding the `score=100`
overlap.** EO's negative reading (−2.08 [−3.47, −0.68]) is largely the
saturation zone showing through it; the non-saturated remainder is not
separately indicted.

**Unaffected — forward-only social and topic-shock lanes.** No historical
substrate exists for the replay window, so none of this evidence speaks to
them, in either direction. They neither gained nor lost support here.

**Not on this list, deliberately:** the six-category scanner as a *net*, the
forward tracker, the benchmark map, the FMP budget guard, and the session-
integrity manifest. The programme's most consistent lesson is that measurement
infrastructure was right and the signal layer was wrong.

---

## 11. Bottom line

The system can produce a wide, unordered, equal-weight momentum watchlist with
a small honest edge on the mean, an avoid list with better evidence behind it
than anything positive we found, and a clean forward measurement of both. That
is the whole product. It cannot rank, cannot concentrate, cannot separate the
tails, and cannot see the events that make the winners.

Build the shadow report — after the cache that feeds it is a maintained job,
not before. Spend twenty calls finding out whether the revision endpoint is
real. Retire the `combined_rs` ordering and the saturation score. Then wait for
forward evidence, because there is no fifth way to interrogate this window that
has not already been tried four times.

*Nothing in this memo is a validated edge, and nothing in it should change a
live rule.*

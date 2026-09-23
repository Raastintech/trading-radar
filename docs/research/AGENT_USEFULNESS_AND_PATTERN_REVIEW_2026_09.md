# Agent Usefulness and Pattern Review — 2026-09

*Written 2026-09-23. Report only. Cache-only reads plus one run each of the
deterministic governor, the read-only feedback-queue review, and the style-cell
forward shadow's `plan-refresh --dry-run` (it refused before any call). **Zero
provider calls, zero LLM calls.** No code, threshold, registry status, M1
membership, HC/EO/Alpha Focus rule, candidate selection, dashboard or timer was
changed by this review.*

> **RESEARCH ONLY.** This reviews the research system's own tooling and the
> recurring research patterns in its artifacts. It names no stock, ranks
> nothing, and is not a signal. Every pattern below is a **research pattern**:
> provisional, and in most cases dependent on forward evidence that has not
> matured. **No component is VALIDATED_EDGE.**

> **Follow-up, same day.** The four zero-provider fixes in §10 were made
> after this review was written, and the body below is left as it was
> found:
>
> - `0b263ab`: the two re-queued feedback tasks;
> - `f764a8b`: the EO verdict now matures, reading `NO_EXCESS_VS_SPY`;
> - `7bbd214`: the governor checks for label-number mismatches and
>   unregistered stale ledgers, and Core-Satellite is registered `DORMANT`.
>
> The price-cache refresh decision in §10 is still open.

"The agent" means two things in this review. They are judged separately
because they did different jobs:

- **the governance layer**: the 2026-09-09 drift audit, the 2026-09-11 LLM
  overlap review, and the deterministic Research Governor v0 with its component
  registry (`research/research_governor.py`, `data/research/component_registry.json`);
- **the agent-run research studies**: the autonomous alpha lab
  (`docs/research/agent_lab/AUTONOMOUS_ALPHA_LAB_REPORT_2026_09.md`), the M1
  programme memo, and the historical replay.

The DeepSeek journal audit reviewer is a third, older thing. It is assessed in
§8.

---

## 1. Executive verdict

**KEEP_BUT_LIMIT.**

The governance layer has **reduced noise and prevented waste**. It has **not
discovered research patterns**, and it was not built to. The patterns worth
having came from the agent-run studies. The governor's contribution was to
stop us from re-litigating those patterns, and to stop stale surfaces from
speaking as if they were current.

The strongest conclusion across the artifacts is not about any selector. It is
this: **the binding constraint on alpha research is now price-cache freshness
and forward maturity, not ideas.** The one provisional object that passed
every replay check (the style-cell leader pool) cannot accrue forward evidence.
Its first scheduled run refused at 42.4% fresh against an 80% floor. The
re-opened P1 queue task (×57), M1's `M1_DATA_DEGRADED` guard and HC's
`scan_readiness: DEGRADED` are the same problem seen from three other places.

The governor also has **three blind spots**, found by this review and not by
the governor (§2.3):

- it trusts verdict tokens without checking them against the numbers;
- it measures freshness by file age, not by whether evidence is accruing;
- it cannot see lanes that are not in the registry.

These are cheap to fix and are listed as proposals only.

---

## 2. What the agent/governor improved

### 2.1 Measured improvements (dated, from commits and governor runs)

| Improvement | Before | After | Source |
|---|---|---|---|
| Provider-spend enforcement | `budget_consume()` always returned True, and `FMP_MONTHLY_BUDGET=0` read as unlimited. One study spent 20,260 calls in a day. | Monthly and daily caps are refused before the request. Unlimited needs a named opt-in. Any stage planning more than 100 calls needs a separate `--allow-large-run`. | 36da6b7, bec47e0 |
| Active candidate surfaces | 7 surfaces answering "which names should I look at"; the daily loop was 3 lists totalling 28 names | **2** surfaces. One daily source (Daily Alpha Radar); HC/EO/Alpha Focus/M1 pool are labels and inputs. The governor's daily workflow today lists **1** high-priority name, capped at 15. | 34ffda4; governor §7 |
| Stale / dead surfaces speaking as current | Scanner-truth 0.0% recall, which measures a decommissioned funnel; the 10x radar's 30-name list with no hypothesis; the superseded Social V11 | All three quarantined in one place (`core/quarantined_surfaces.py`). Scanner-truth and RS/theme triage are off the nightly cycle. An empty 10x section now says it is quarantined. | 36da6b7, 34ffda4, 7c62142 |
| A hidden live metric | The dead 0% recall number sat in front of the live recall cohort | The live cohort is visible: `LOOSE_NOT_BETTER` (beats random on 8.7% of dates vs a 60% gate) | 36da6b7 |
| Fake precision from one broken bar | One symbol's spliced weekend bars produced a +189,857% 10-day return. It moved a 6,216-row mean from −1.67% to +28.87% and flipped the HC verdict. | Weekend bars are refused and impossible returns are quarantined. Medians and winsorized headlines are reported beside every mean. | 493bd9c |
| Generated-doc noise | Journal digest: 184–187 lines, 18–19 sections | About 95 lines, 13 sections; every shortlist token carries "unproven" inline. The cap is now 100 on purpose (temporary). | dc575d4, a7203f8 |
| Feedback-queue honesty | The queue reported "0 open" while two closed tasks were re-queued nightly (57× and 33×) | Re-opened tasks are surfaced and counted as awaiting a human | 24deb80, 6d93ec2 |
| Passed decision dates shown as "next" | The digest printed 2026-08-17 as next on 2026-09-11 | Picks the first date on or after the ET session and labels the SUNSET gate | 24deb80 |
| Dead manual cadence | The style-cell shadow sat at 1 session for 2 weeks with `cadence: manual` | Weekly zero-provider timer. It records a refusal row when gates fail. | 0327b4d |
| Evidence labels | Verdict tokens travelled out of context | The governor states the asymmetric evidence doctrine: backtest may reject, only mature forward may support a VALIDATED_EDGE review | 07f0f6c |

**Governor trajectory:**

| Date | Verdict | Drift warnings | FIX_NEXT | NEEDS_HUMAN_APPROVAL | Active candidate surfaces |
|---|---|---|---|---|---|
| 2026-09-11 (first run) | DRIFTING | 23 | 12 | 6 | 7 |
| 2026-09-23 | CAUTION | **1** | **1** | **0** | **2** |

**Validated edge count: 0**, unchanged throughout. That is the correct
outcome, not a failure of the agent.

### 2.2 What the governor did *not* do

- It found no research pattern. It is audit-only by design, with no LLM and no
  provider access.
- Its biggest wins (budget enforcement, quarantine, surface collapse) came from
  the drift audit, which preceded it. The governor's job was to keep those
  decisions from regressing, which it has done for 12 days. That is useful but
  modest.

### 2.3 Blind spots found in this review (proposals only, nothing changed)

1. **It trusts verdict tokens.**
   - **EO:** `research/emerging_outlier_watch.py:509-512` hard-codes the forward
     verdict to `NEED_MORE_DATA`. Its reason string reads "only 1177 matured
     10d episodes (need >=10)". No ladder is ever evaluated, so EO can never
     mature, yet the governor lists it as `WAIT_FOR_MATURITY`. The underlying
     numbers are 10d vs SPY mean 0.00%, median −0.41%, win 48.9%, n=1,177.
   - **Social Attention:** the governor shows `PROMISING_BUT_UNPROVEN` /
     AGREES. The numbers underneath are weaker than the token (§3, P6).
2. **Freshness is file age, not evidence accrual.** The style-cell shadow reads
   `FRESH` / `WAIT_FOR_MATURITY` because the refusal rewrote its artifact
   7 hours ago. It has accepted **zero** new sessions since 2026-09-10 and
   cannot mature until the cache is refreshed. "Wait" is the wrong instruction
   for a lane that cannot advance.
3. **Unregistered lanes are invisible.** The Core-Satellite forward-shadow
   ledger holds **one seed row from 2026-08-24**, has been untouched for 29 days,
   and appears nowhere in the registry. It is the same dead-manual-cadence
   failure the style-cell lane had. The M1 review packet (manual, 344h old,
   `M1_DATA_DEGRADED`) shows `NOT_CHECKED` for the same reason.

---

## 3. New patterns and trends detected

These are recurring **research patterns**, each seen in at least two
independent artifacts. None is a finding of edge.

**P1. A wide unordered basket beats per-name ranking; concentration makes things worse.**
- M1: the basket beats the tape (+1 to +2pp per 60d), but the ticker-clustered
  CI is negative at every horizon (60d [−4.60, −3.55]).
- Alpha lab: every 5–15-name list failed out of sample (the primary went
  +4.79 → +0.39). The 99-name unordered style-cell pool passed every check.
- M1 top-10: mean +12.8% against a median of −6.3%; the share of names down
  more than 25% rises from 20% to 34% as the list shortens.
- Live HC: +0.36% vs SPY at row level, but **−0.02% mean / −0.57% median per
  ticker** (96 names, overlap 5.5×).

**P2. Short-horizon RS / momentum-leader labels underperform, in replay and in live forward data.**
- Live forward tracker, `RS_MOMENTUM_LEADER`: 10d vs SPY **−3.96%** mean,
  −3.99% median, win 35.0%, 1,473 episodes / 137 names, `NO_FORWARD_EDGE`.
- `EARLY_ACCUMULATION`: −3.06%, win 36.7%, `NO_FORWARD_EDGE`.
- Replay: the old scanner axis (`combined_rs`) is −7.75pp as a top-10 in the
  training years. Its 20d holdout top-5 has a −6.38% median.
- The six price-only lanes are `REPLAY_CONTRADICTS` at every horizon.
- The 12-1 form (which skips the most recent month) is the one that survives.
  The 20/63-day window is contaminated by reversal (M1 memo §1).

**P3. Every filter tested is a variance filter.**
- The features that pick winners pick losers equally: winner/loser AUC
  correlation +0.925 to +0.991 in every universe (alpha lab).
- The same holds inside M1: +0.947.
- Removing volatility, prior-crash names or earnings-miss names cuts both tails
  about equally. The earnings-miss filter removes more winners than losers.

**P4. Fundamental-quality and event overlays subtract as selectors.**
- Measured in three studies: the alpha lab (quality overlay +4.79 → +2.91),
  the M1 memo (EPS-beat screen −0.30), and M1 analyst actions (upgrades +0.73,
  downgrades +0.50, i.e. attention, not direction).
- Replay: `FUNDAMENTAL_REPLAY_CONTRADICTS`.
- The same layer *does* reject the trap bucket: HC rejects 99.74% of the
  `score=100` zone.

**P5. Named trap buckets reliably lose.**
- The `score=100` saturation zone is negative in both train and holdout with
  CIs clear of zero (−7.01 / −3.80). The cheap/volatile/broken cohort is also
  negative.
- Live `RISKY` label: 10d vs SPY −4.92%, win 35.8%.
- This is the best-supported output the system has, and it is negative in
  form.

**P6. Social attention reads better as late-crowd context than as a lead.**
- The social-led cohort is slightly positive at 1d (+0.24% vs SPY), then
  negative at every horizon from 3d on:
  - 5d −0.61%, win 32%;
  - 10d −2.14%, win 29%;
  - 20d −5.60%, win 25% (n=24–36).
- It loses to random (`social_beats_random: false`) and all leads lose to
  random.
- The `PROMISING_BUT_UNPROVEN` token rests on "social beats news" (−0.61% vs
  −4.54%) and a velocity split of −1.42% vs −1.64%. Neither is a positive
  result.

**P7. Headline means are routinely distorted by one bar, one cluster or one tie-break.**
- One broken bar: +189,857%.
- A quantum-computing cluster: +13.76pp, falling to +7.24 with five names
  removed.
- Alphabetical tie-break: flattered the result by about 1.5pp.
- Ledger duplication: 4.89× (8,749 rows → 1,788 episodes). Per-ticker overlap
  in the forward tracker is 8.56×.
- HC is positive at row level and flat per ticker.

**P8. Cache freshness is blocking forward evidence.**
- Style-cell: 42.4% fresh vs an 80% floor. A refresh needs **1,291 calls**
  (from `plan-refresh`, zero calls made).
- M1 packet guard: `M1_DATA_DEGRADED`.
- HC `scan_readiness: DEGRADED`.
- The re-opened P1 task "run the targeted price-cache backfill" has been queued
  57 times.
- The alpha lab found only 58.9% of liquid names had 252-bar depth in the live
  cache.
- The July finding still stands: the candidate pool closed after the
  decommission and only about 1,000 names are refreshed daily.

**P9. The only surviving effects are 60-day effects, and 60d evidence has barely started.**
- The style-cell pool is `INCONCLUSIVE` at 20d and passes at 60d. M1's effect
  is a 60d effect. The replay's `long_term_asymmetric` lane improved with
  horizon (+0.25 / +0.66 / +1.79 at 5/20/60d).
- In the live tracker, 60d has 358 matured rows against 7,190 at 10d. Programs
  are still being judged mostly at 5–20d.

**P10. Manual cadences with no owner die silently.**
- Style-cell: 2 weeks.
- Core-Satellite forward shadow: 29 days, 1 seed row.
- M1 packet: 14 days.
- Social V11: never wired.

**P11. Verdict tokens drift more optimistic than their numbers.**
- `PROMISING_BUT_UNPROVEN` (social); `SHORTLIST_IMPROVES_OUTCOMES` (HC, flat
  per ticker); a hard-coded EO verdict; `PROMISING` on the `NO_SOCIAL_DATA`
  label (12 names).
- The drift audit flagged this class on 2026-09-09. It recurs.

---

## 4. Patterns useful for future scanner research

| # | Pattern | Class |
|---|---|---|
| P1 | Unordered basket > per-name ranking | **NEEDS_FORWARD_TEST** (framing: USEFUL_NOW) |
| P2 | Short-horizon RS-leader labels underperform | **USEFUL_AS_LABEL_ONLY** |
| P3 | Filters are variance filters | **USEFUL_NOW** (as a stop rule for research) |
| P4 | Fundamental/event overlays as selectors | **REJECTED** as selectors; **USEFUL_AS_LABEL_ONLY** as exclusion tags |
| P5 | Trap buckets reliably lose | **USEFUL_AS_LABEL_ONLY** |
| P6 | Social = late-crowd context | **USEFUL_AS_LABEL_ONLY**; late-crowd warning **NEEDS_FORWARD_TEST** |
| P7 | Means distorted by bars/clusters/ties | **USEFUL_NOW** (reporting standard) |
| P8 | Cache freshness blocks evidence | **USEFUL_NOW** (binding constraint) |
| P9 | 60-day effects only | **NEEDS_FORWARD_TEST** |
| P10 | Manual cadences die | **USEFUL_NOW** (process) |
| P11 | Tokens drift optimistic | **USEFUL_NOW** (governance) |
| — | Concentrated 12-1 momentum top-10 (+13.76pp) | **LIKELY_OVERFIT** |
| — | `trend_anti_junk` 5–15-name lists | **REJECTED** |
| — | `NO_SOCIAL_DATA` label +7.35% (12 names) | **LIKELY_OVERFIT** (small-n artifact) |
| — | `BEATEN_DOWN` label (per-ticker 10d median +1.68%) and replay `long_term_asymmetric` | **NEEDS_FORWARD_TEST** |

**P1: basket over per-name ranking**
- *Supporting artifacts:* M1 memo §0–2; alpha lab §6–7; HC forward per-ticker
  block.
- *Why it may help:* it tells research to evaluate pools, not orderings. It
  explains why every shortlist has failed.
- *What would prove it:* the style-cell forward floor (30 matured 60-session
  snapshots across 4 non-overlapping windows) clearing the vol×liquidity
  matched control. The M1 frozen cohort at 45d/60d (2026-11-06 / 11-27)
  failing to beat its not-picked arm would corroborate it.
- *What would falsify it:* the forward pool fails to beat its matched control,
  or the M1 picks beat the not-picked arm on both mean and median at 45d and
  60d.
- *New data needed:* no new data class. It needs a fresh price cache (P8).
- *Overfitting risk:* **material.** The pool form was specified after the 2025
  holdout was read, and 2025 has now been read five times.

**P2: short-horizon RS-leader labels underperform**
- *Supporting artifacts:* `research_forward_latest.json` label table;
  replay §3–4; alpha lab §2.
- *Why it may help:* it is the one negative that live forward data and replay
  agree on. It argues against reading scanner RS order as priority.
- *What would prove it:* the label stays negative vs SPY and vs its own
  same-date universe median at 20d and 60d as those mature, per ticker, with
  medians.
- *What would falsify it:* 20d/60d turn positive per ticker. One regime window
  (Jul–Sep 2026) is all we have.
- *New data needed:* no.
- *Overfitting risk:* low for the negative claim. It was not tuned, and replay
  and live agree. Any *live change* on this basis still needs approval.

**P3: filters are variance filters**
- *Supporting artifacts:* alpha lab §5 (H2); M1 memo §2.
- *Why it may help:* it stops time spent hunting a 5–15-name filter.
- *What would prove it:* it is already measured in two independent studies.
- *What would falsify it:* a new data class, such as unscheduled-event data
  (contracts, clinical read-outs, M&A), that separates the tails. The memo
  shows most big winners make their move in one session that price history
  cannot anticipate.
- *New data needed:* yes, to *overturn* it.
- *Overfitting risk:* none. It is a negative.

**P4: fundamental/event overlays as selectors**
- *Supporting artifacts:* alpha lab §2; M1 memo; M1 analyst-actions memo;
  replay §6.
- *Why it may help:* it frees effort. Exclusion tags such as the trap bucket
  and red flags still earn their place.
- *What would prove the rejection:* already measured three times.
- *What would falsify it:* a forward cohort where the overlay improves the
  *median* per-ticker outcome against a matched control.
- *New data needed:* no.
- *Overfitting risk:* low.

**P5: trap buckets reliably lose**
- *Supporting artifacts:* M1 memo §1(3); live `RISKY` label.
- *Why it may help:* a "historically money-losing bucket" warning is cheap and
  honest context.
- *What would prove it:* it holds forward at 20d/60d.
- *What would falsify it:* the live `RISKY` / score=100 cohorts turn positive
  per ticker.
- *New data needed:* no.
- *Overfitting risk:* low (negative, replicated).

**P6: social attention as late-crowd context**
- *Supporting artifacts:* `social_attention_forward_latest.json` by-cohort and
  comparisons.
- *Why it may help:* as a label ("attention already arrived") it adds context
  without adding a list.
- *What would prove the warning version:* the social-led cohort keeps
  underperforming random and SPY at 5–20d as n grows past ~100 per horizon.
- *What would falsify it:* it converges to random or flips positive.
- *New data needed:* more history only.
- *Overfitting risk:* **high** at n=24–36. Treat the warning reading as a
  hypothesis, not a finding.

**P7: means distorted by bars, clusters and ties**
- *Supporting artifacts:* 493bd9c; alpha lab §2, §6; program sidecar
  duplication factor.
- *Why it may help:* requiring median, per-ticker, deduped and
  leave-top-5-out beside every mean would have caught every false headline so
  far.
- *What would prove it:* already shown four times.
- *What would falsify it:* not applicable; this is a reporting standard.
- *New data needed:* no.
- *Overfitting risk:* reduces it.

**P8: cache freshness blocks evidence**
- *Supporting artifacts:* style-cell refusal; `plan-refresh` (1,291 calls);
  queue task `afa49350da0a` ×57; M1 guard; HC scan readiness.
- *Why it may help:* it is the single unblocker for the only provisional pool
  and for the M1 packet.
- *What would prove it:* after one broad refresh, the style-cell gates pass
  and M1's guard clears.
- *What would falsify it:* the gates still fail after a refresh, for example
  on depth or merge-integrity rather than freshness.
- *New data needed:* no new class; provider calls. **Human budget decision.**
- *Overfitting risk:* none.

**P9: 60-day effects only**
- *Supporting artifacts:* alpha lab §6 ("where it does not work"); M1 memo;
  replay §8; the tracker's `matured_by_horizon`.
- *Why it may help:* it stops judging swing and long-term hypotheses at
  5–10d.
- *What would prove it:* 60d forward cohorts show the effect while 5–20d stay
  flat.
- *What would falsify it:* 60d forward is flat too.
- *New data needed:* time only.
- *Overfitting risk:* moderate. "The effect is at the horizon we haven't seen"
  is also what a hope looks like.

**P10: manual cadences die**
- *Supporting artifacts:* style-cell ledger; Core-Satellite shadow sidecar;
  M1 packet age.
- *Why it may help:* any lane that is supposed to accrue evidence either gets
  a zero-provider timer or is marked dormant.
- *What would prove it:* not applicable; this is process.
- *New data needed:* no.
- *Overfitting risk:* none.

**P11: tokens drift optimistic**
- *Supporting artifacts:* EO code (§2.3); social comparisons; HC per-ticker
  block.
- *Why it may help:* a governor check that compares the token with the
  per-ticker median and the random control would catch the next one.
- *What would prove it:* not applicable; this is governance.
- *New data needed:* no.
- *Overfitting risk:* none.

---

## 5. What should improve the scanners without changing live logic yet

All of these are **research framing or reporting changes**. None alters a
threshold, a score, membership or routing.

1. **Frame candidate research as pools, not rankings.** Style-cell
   diversification (one name per volatility × liquidity cell) is the framing
   with the best replay support. Use it to *describe* a list: how many cells it
   covers, and its sector and top-10 share. Do not use it to order names.
2. **Keep one daily candidate source.** The Daily Alpha Radar high-priority
   bucket, capped at 15. Everything else stays a label on that list.
3. **Treat social, news catalyst and topic shock as context labels** on names
   already on the list, never as sources. Read social attention as possibly
   late, not early (P6, provisional).
4. **Require median, per-ticker, deduped and leave-top-5-out beside every
   mean** in any new report, and prefer the vol×liquidity matched-random
   control over SPY as the primary comparison.
5. **Avoid top-10 concentration** in any research list, and report the top-10
   ticker share when a list is shown.
6. **Prefer source pools over per-name rankers.** Where a ranker exists (RS
   order), read it as discovery only (P2).
7. **Promote nothing before forward maturity.** The dates already fixed are:
   - M1 cohort: 2026-11-06 (45d) and 2026-11-27 (60d);
   - style-cell: first 60d session on about 2026-12-04, with a floor that is not
     reached before 2027, and only if the cache is fresh.
8. **Judge swing and long-term hypotheses at their own horizons** (≥45–60d), not
   at 5–10d.

---

## 6. What should not be built

| Tempting idea | Why not |
|---|---|
| A new per-name ranker or conviction layer (in M1, in the style-cell pool, anywhere) | Five independent attempts failed. The ticker-clustered CI is negative. About 99 names tie at the top of the pool by construction. |
| A new social / attention alpha scanner | The social-led cohort loses to random at every horizon past 1 day (P6) |
| A new 10x / asymmetric radar | Quarantined for having no hypothesis. Concentrated high-momentum lists are the most seductive overfit in the record. |
| More overlay stacking (quality, PEAD, analyst actions, surprise) | Measured three times, always subtracting (P4) |
| Any 5–15-name "high-conviction" list from this data | Rejected from three directions (P1, P3) |
| More dashboards or panels | Governor DO_NOT_BUILD; nothing new has earned surfacing |
| Daily 100-name reviews | More than about 15 names is browsing, not research |
| Automatic provider refreshes | Price refreshes that no human sized and approved are the 20,260-call-day accident class. Any refresh is a named, sized human decision (§9). |
| Another nightly step | The cycle is about 49 steps and should shrink |
| An LLM alpha research agent / recursive loop | Roadmap: not before the 45d/60d windows mature |
| Routing or selecting on any `PROMISING_*` token | Tokens drift optimistic (P11) |
| Reading 2025 as a holdout again | It has been read five times; new out-of-sample evidence must come from time |
| Re-tuning concentration thresholds | The failure was a sign flip, not a near miss |

---

## 7. Current best alpha-research direction

**Forward-measure the style-cell leader pool as an unordered basket, once the
price cache can support it, and leave everything else alone until the fixed
maturity dates.**

It is the only object in the repo's record whose *typical name* beat a
vol×liquidity matched control, with both clustered CIs clear of zero, in both
halves:
- train +2.27pp, holdout +2.45pp per 60d;
- ticker-clustered holdout CI [+1.03, +3.83].

It is **provisional**:
- the pool form was specified after the holdout was read;
- it is a known factor (style-neutral 12-1 momentum), not a discovery;
- no cost model exists, and roughly +2.4pp per 60 days could be largely erased
  by one;
- it does not work at 20d.

It is a measurement to run, not a product to use.

Secondary, low-cost directions (all research-only, all NEEDS_FORWARD_TEST):
the replay's `long_term_asymmetric` lane and EO's non-`score=100` subset,
isolated.

---

## 8. What still requires forward evidence

| Item | Earliest meaningful read | Blocker |
|---|---|---|
| M1 frozen manual-pick cohort (picks vs 65 not-picked) | 45d ≈ 2026-11-06, 60d ≈ 2026-11-27 | none; weekly timer running |
| Style-cell leader pool | first 60d ≈ 2026-12-04; floor 2027+ | **price-cache freshness** (42.4% vs 80%) |
| Research programs (tactical / swing / long-term) | primary horizons are still below floors | time; SWING at 60d has n=53 over 10 dates |
| HC shortlist | per-ticker read is flat; one regime window | time and more regimes |
| EO watch | **cannot mature as coded** (verdict hard-coded) | code fix, needs approval |
| Social Attention | n=24–36 per horizon | time |
| Recall-repair shadow lane | own ladder | time |
| Core-Satellite forward shadow | **never started** (1 seed row, 29d) | no cadence; not in registry |
| Options IV-rank work | PARTIAL ≈ Sep 2026 | accumulation |

---

## 9. Whether the agent is worth keeping

**KEEP_BUT_LIMIT.**

| Criterion | Answer | Evidence |
|---|---|---|
| Did it reduce noise? | **Yes** | Surfaces 7→2; drift warnings 23→1; digest 187→~95 lines; three dead surfaces quarantined |
| Did it prevent waste? | **Yes** | Budget enforcement and the large-run gate close the 20,260-call accident class. The style-cell job is provably zero-provider. |
| Did it expose real problems? | **Yes, with gaps** | Re-opened queue tasks; a broken bar flipping a verdict; passed dates shown as next. It missed the EO hard-code, the refused-but-FRESH lane and the unregistered Core-Satellite ledger. |
| Did it help us focus? | **Yes** | One daily source, at most 15 names; WAIT and DO_NOT_BUILD lists are explicit |
| Did it find useful research patterns? | **No, and not its job** | The patterns came from the agent-run studies |
| Did it create new apparatus? | **Some, bounded** | One module, one registry and one weekly timer. It added no nightly step and no dashboard, and removed two nightly steps. |

**Limits proposed:**
- keep it deterministic, with no LLM and no provider access;
- run it weekly, not nightly;
- no new checks except the three blind-spot fixes in §2.3;
- honour the existing 2026-10-23 retirement review.

If by then it has not caught a problem a human would otherwise have missed, it
should be retired to on-demand.

**DeepSeek journal audit reviewer (older agent):**
- the least useful part of the stack;
- 147 tasks, of which 80 superseded, 29 dismissed and 36 resolved by audit;
- its two live items are one repeating data-quality condition (57×) and one
  fundamental-display request (33×);
- its one-line summary comments on specific names on the shortlist, which edges
  toward per-name commentary.

It is worth a separate human decision on whether its daily LLM call earns its
place. No change is made here.

---

## 10. Exact next recommendation

**One human decision: approve or decline a single, sized, weekly price-cache
top-up for the liquid universe.**

- `plan-refresh` (zero calls, dry run) sizes it today at **1,291 calls**. Weekly,
  that is roughly 5,000–5,600 calls a month against the 120,000 default cap.
- September is already at **91,480** month-to-date, so the first run should wait
  for October or be explicitly budgeted.
- If approved, it is run by hand the first time. It would clear the style-cell
  refusal and plausibly the M1 guard, and it closes the P1 task queued 57 times.
- If declined, the style-cell lane should be marked dormant in the registry
  rather than left reading `WAIT_FOR_MATURITY`. The same applies to the
  Core-Satellite ledger.

**Zero-cost follow-ups, each needing separate approval:**
1. Triage the two re-opened queue items: `afa49350da0a` (P1) and
   `70884dde3217` (P2).
2. Fix EO's hard-coded forward verdict so its own ladder is evaluated.
3. Governor: treat "no accepted evidence since N days" as stale for
   evidence-accruing lanes, and warn when a verdict token disagrees with the
   per-ticker median or the random control.
4. Register or retire the Core-Satellite forward shadow.

Nothing else until 2026-11-06.

---

*Inputs read: `docs/research/RESEARCH_GOVERNOR_REPORT_latest.md`,
`cache/research/research_governor_latest.json`, `data/research/component_registry.json`,
`research_forward_latest.json`, `research_program_validation_latest.json`,
`high_conviction_forward_latest.json`, `emerging_outlier_forward_latest.json`,
`social_attention_forward_latest.json`, `m1_manual_picks_resolution_latest.json`,
`m1_daily_review_packet_latest.json`, `alpha_failure_root_cause_latest.json`,
`core_satellite_forward_shadow_latest.json`, `journal_audit_latest.json`,
`agent_lab/style_cell_leader_forward_latest.json` and its ledger,
`AUTONOMOUS_ALPHA_LAB_REPORT_2026_09.md`, `M1_SYSTEM_DECISION_MEMO_2026_09.md`,
`HISTORICAL_REPLAY_RESULTS_2026_09.md`, `SYSTEM_DRIFT_AUDIT_2026_09.md`,
`LLM_AGENT_OVERLAP_REVIEW_2026_09.md`, the feedback-queue review, and commits
4a533f6…0327b4d. Research only; not a signal; no validated edge.*

# Daily Operating Note — the smallest workflow that works

*Written 2026-09-09 from `docs/research/SYSTEM_DRIFT_AUDIT_2026_09.md`. This is the
operating contract for daily manual research. It replaces "read everything".*

> **RESEARCH ONLY.** Nothing in this system has a validated forward edge. Every
> board, shortlist and label below is a place to start reading, never a reason to
> act. No rankings, no price targets, no buy/sell language.

---

## The daily loop — about 15 minutes, zero provider calls

The 08:00 ET premarket timer and the 20:30 ET nightly timer have already done the
work. **You run nothing in the morning.**

### 1. Confirm freshness (30 seconds)

```bash
./scripts/run_research_cycle.sh freshness-audit
```

If the session is aligned and nothing is DEGRADED, continue. If it is not, that
is the whole finding for today — fix the data, do not read the board through it.

### 2. Read the board — **5–10 names**

`docs/research/DAILY_ALPHA_RADAR_REPORT.md`

Top priority bucket first. Stop at ten. A list longer than ten is browsing, not
research.

### 3. Skim the summary — **warnings only**

`docs/research/NIGHTLY_OPERATOR_SUMMARY.md`

Act on data-quality warnings. Everything else is context.

### That is the daily loop. Stop here.

---

## Weekly / on demand

**The M1 review packet** — a second, independent pool. Weekly depth, not daily.

```bash
GEM_TRADER_SKIP_DOTENV=true .venv/bin/python -m research.backtests.m1_daily_review_packet
```

Then open `docs/research/M1_DAILY_REVIEW_PACKET.md` §6 (*M1 Manual Review
Candidates*) and fill in `bucket`/`notes` by hand. The strict filter already cut
the pool for you.

**M1 membership is a basket property.** The pool's edge is measured across the
whole basket and its ticker-clustered confidence interval is *negative* at every
horizon. A name being in M1 is not a statement about that name.

**Automatic, no action needed:** the M1 cohort tracker (Sat, ~75 calls), the
weekly liquid lens refresh, the daily options chain snapshot.

---

## The review-count target

| surface | names to review |
|---|---|
| Daily Alpha Radar | 5–10 |
| M1 packet (weekly) | up to ~10 of the filtered candidates |
| **daily total** | **≤ 15** |

If you are consistently over 15, the filters are not doing their job — that is a
filter problem to fix, not a reading list to work through.

---

## What to ignore

Ignore every diagnostic, audit, replay and forward-validation sidecar **unless
you are investigating a specific failure**:

`*_audit_*` · `*_diagnostics_*` · `*_forward_validation_*` · `strategy_lab_*` ·
`lrr_*` · `core_satellite_*` · `scanner_recall_*` · `rs_theme_*` ·
`power_trend_*` · `gatekeeper_precision_*` · `universe_*_replay_*`

These are RESEARCH_ONLY. Most have reached a verdict and been frozen since June.
**A sidecar that has not changed a decision in 30 days is not a daily artifact.**

### Quarantined — not current truth

These are preserved on disk as history and are **not** readings on today's system.
`core/quarantined_surfaces.py` is the single place that decides this, and the
operator surfaces ask it before rendering anything.

| artifact | status | why |
|---|---|---|
| `scanner_truth_summary_latest.json` | `DECOMMISSIONED_AUTOPSY` | Measures the council funnel decommissioned 2026-06-13. Pinned near 0% by construction — it is not live recall. Live-board recall accrues in `scanner_recall_cohorts_latest.json`. |
| `ten_x_candidates_latest.json` | `QUARANTINE_NO_HYPOTHESIS` | No registered forward hypothesis, no validating consumer. Off the schedule; runnable on demand. Not a current candidate list. |
| `social_attention_v11_*`, `top_market_attention_*` | `SUPERSEDED_STALE` | Superseded by the nightly Social Attention radar. No cadence; historical only. |

---

## Social, news and topic surfaces

**One current social/news lane:** the nightly Social Attention radar
(`social_attention_radar_latest.json`) plus the News Catalyst radar
(`social_arb_latest.json`).

- Use them **after** a name is already on your list, to answer *"why is it
  moving?"*.
- **Never** use them as a source of names.
- Social Attention's own verdict is `PROMISING_BUT_UNPROVEN` — that means "not yet
  a reason", not "nearly a reason".
- **Topic Shock** is shadow-mode and nothing reads its output. Ignore it entirely.

---

## Never read as a trade signal

The Daily Alpha Radar board · the HC shortlist · the EO watch · Alpha Focus
ordering · the M1 pool · the M1 review candidates · the 10x radar ·
social/news/topic output · and every `PROMISING_*` / `*_CANDIDATE` /
`*_READY` verdict token.

None of these has a validated forward edge. All three research programs currently
return `INSUFFICIENT_MATURE_EVIDENCE`, and the broad forward tracker — the one
robust dataset, 7,577 entries across 784 tickers — returns **MIXED**.

---

## Reading the metrics honestly

Two traps this system has already walked into. Both produce a number that looks
like a finding and is not.

### Recall is not comparable across cohorts

**Recall scales with how many names a cohort surfaces.** A cohort that lists more
names will catch more winners, whatever it does or does not know. So an "X% vs Y%"
recall comparison is a size difference until proven otherwise.

From `scanner_recall_cohorts_latest.json` (2026-09-09), all five cohorts, same
window:

| cohort | ticker-days | recall % | precision % |
|---|---:|---:|---:|
| random_control | 14,347 | **89.6** | 17.9 |
| loose_scanner | 14,502 | 84.6 | 16.9 |
| strict_mirror | 9,872 | 47.6 | 14.2 |
| scanner_watchlist | 1,454 | 15.0 | 30.3 |
| rs_baseline | 558 | 5.4 | 30.4 |

**The random control has the highest recall of the five.** Reading
"scanner_watchlist 15.0% vs rs_baseline 5.4%" as the watchlist doing better is
reading list length. Precision, and excess return against a same-window control,
are the numbers that carry information — and a control means *random*, not
another cohort of a different size.

### Overlapping windows inflate everything

A forward horizon of N sessions measured from consecutive daily registration
dates gives **~dates ÷ N independent observations**, not one per date.
Consecutive dates share (N−1)/N of their window, so means, win-rates and
t-statistics are all inflated by construction.

Worked example, same artifact: 23 registration dates at a 20-session horizon is
**~1.1 independent windows**. The watchlist appeared to beat random by +2.20pp on
16 of 23 dates (apparent t ≈ 3.7) — but the wins were one contiguous run of 13
dates, four dates in a single week carried 54% of the total, and all three
non-overlapping subsamples came out negative. One late-July episode, counted
thirteen times.

**Before believing any forward comparison, ask:** how many *non-overlapping*
windows is this? If the answer is one, it is an anecdote with a decimal point.

The same failure has now been documented three times here — the M1 ticker-clustered
CI, the LRR result where one month carried 73% of the edge, and this. Assume it is
present until you have checked.

---

## Provider spend

Spend is now **enforced**, not just counted (`core/provider_budget.py`):

- monthly and daily ceilings, refusing **before** a request reaches the wire;
- `FMP_MONTHLY_BUDGET=0` means "use the documented default", **not** unlimited;
- unlimited requires the explicit `ALLOW_UNLIMITED_PROVIDER_CALLS=true`;
- a run planning more than 100 calls must pass an explicit override;
- `m1_price_refresh` refuses above its per-run hard cap without `--allow-huge-run`.

**Before any manual provider run, plan it first.** Every module's `plan` stage
costs zero calls and prints what a fetch would cost. Check `fmp_budget_monthly`
against the run rate monthly — recent months: Jul 107,739 · Aug 88,157.

---

## When to break this note

When you are investigating a specific failure, read whatever you need. The rule
is about the *default* daily path, not about what is permitted. The failure mode
this note exists to prevent is reading fifty artifacts every morning and calling
it research.

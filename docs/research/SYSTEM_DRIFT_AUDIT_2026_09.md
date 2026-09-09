# System Drift Audit — 2026-09

*Generated 2026-09-09. Cache-only review; zero provider calls made for this audit.
No production logic, scanner threshold, M1 membership, frozen cohort, HC/EO/Alpha
Focus routing or dashboard was changed.*

> **RESEARCH ONLY.** This is an audit of the research system's shape, not of any
> stock. It contains no candidate list, no ranking, no score and no trade
> language. Nothing here is evidence of edge in either direction.

---

## 0. The one-paragraph version

**Yes, we are drifting — but not in the direction the question implies.** The
system is not producing bad candidates; it is producing *too much apparatus
around* candidates. The nightly cycle runs **49 sequential steps**; the repo
holds **139 research modules**, **97 runner commands**, **130 research documents**
and **1,846 cached artifacts**, of which **1,514 (92.5%) are more than a week
stale** and roughly **80 system sidecars are 60–130 days stale**. Meanwhile the
one component with genuinely robust data — the forward evidence tracker, 7,577
entries across 784 tickers and 60 dates — returns **MIXED**, and all three
research programs return **INSUFFICIENT_MATURE_EVIDENCE** with 5-day
excess-return confidence intervals straddling zero. The drift is that
**measurement infrastructure has outgrown the thing being measured**, and a
meaningful share of it is now measuring decommissioned components. The single
most urgent finding is not a research finding at all: **the FMP budget is not
enforced anywhere in the codebase** — `budget_consume()` always returns `True`.

---

## 1. Component inventory and classification

| Component | Class | Cadence | Freshness | Basis |
|---|---|---|---|---|
| **Forward evidence tracker** (`research_forward_latest.json`) | **CORE** | nightly + premarket | 0.1d | 7,577 entries, 5,901 matured 10d, 784 tickers, 60 dates, 98% resolution, `sample_status: ROBUST`. The only robust dataset in the system. |
| **Daily Alpha Radar** | **CORE** | nightly + premarket | 0.1d | The one board a human actually opens. Quality-gated. |
| **Research scanner + watchlist** | **CORE** | nightly + premarket | 0.1d | Candidate discovery. Feeds everything downstream. |
| **M1 daily review packet** | **CORE** | manual | 0.0d | Now 49 reviewable names of 75 after the strict filter. Bounded, cheap, honest about what it is. |
| **M1 source pool** | **CORE** (as a pool) | manual | 1.5d | Only strategy family with out-of-sample support; works as a basket, not per name. |
| **M1 frozen manual-pick cohort tracker** | **CORE** | weekly (Sat) | 1.4d | ~75 calls/week. Pre-registered falsification test. Currently `NOT_MATURE`. |
| **M1 strict review filter** | **CORE** | with packet | 0.0d | Cuts review load 75 → 49 at zero call cost. |
| **HC (High-Conviction) shortlist** | SUPPORT | nightly + premarket | 0.1d | `CURRENT_FOR_LATEST_SCAN`. Forward hypothesis still immature. |
| **EO (Emerging Outlier) watch** | SUPPORT | nightly + premarket | 0.1d | Forward verdict `NEED_MORE_DATA`. |
| **Alpha Focus** | SUPPORT | nightly | 0.6d | `promote_to_signal: False`. Prioritisation only; correctly bounded. |
| **Daily Research Digest** | SUPPORT (degrading) | nightly | 0.6d | 187 lines, 18 sections, numbering that skips 2 and forks into 4/4b/4b1/4b2/4c/4d. Has become a dumping ground. |
| **Social Arb / News Catalyst Radar** | SUPPORT | nightly | 0.6d | Genuinely a *news catalyst* radar, not social. Name is misleading. |
| **Social Attention Radar V0** | SUPPORT | cron Mon-Fri 21:30 UTC | 0.7d | Runs, is fresh, verdict `PROMISING_BUT_UNPROVEN`. |
| **Social Attention Radar V11** | **SUNSET_CANDIDATE** | **none** | **12.6d** | Successor built, never wired to any cadence. Duplicate of V0. |
| **Topic Shock Detector** | RESEARCH_ONLY | nightly (shadow) | 0.4d | Explicitly shadow-mode; nothing downstream reads it. Accumulating phrase history only. |
| **Alpha Heat Radar** | SUPPORT (library) | **none** | n/a | No runner command, no dispatch entry, no artifact, no timer. Exists only as an import inside Topic Shock. Not a report. |
| **Historical replay harness** (strategy lab, LRR, core-satellite, failure miner) | RESEARCH_ONLY | none | **85–90d** | ~30 sidecars frozen since mid-June. Verdicts already reached and recorded. |
| **Scanner Truth Review** | **QUARANTINE** | **nightly** | 0.4d | Publishes `winner_recall_pct: 0.0` nightly against `measured_pipeline: "decommissioned_council_funnel_autopsy"`. Still read by the dashboard and the operator summary. |
| **Scanner recall family** (`scanner_recall_*`, `rs_recall_*`, `recall_shadow_*`) | RESEARCH_ONLY | partly nightly | 63–93d for most | Verdicts reached; several still on the nightly tail. |
| **RS/Theme triage + forward** | RESEARCH_ONLY | nightly | 94d (forward) | Matured verdict `NO_VALUE` (2026-06-03). Triage still runs nightly. |
| **MCP audit workflows / session** | SUPPORT | nightly + social cron | 0.4d (session) | Per-ticker audits 113d stale; session composite is fresh. |
| **Options chain snapshot collector** | SUPPORT | user timer 15:45 ET | 0.8d | `DATA_COLLECTION_ONLY`. Accumulating toward IV-rank ≈ Sep 2026. Correct posture. |
| **10x candidate radar** | **SUNSET_CANDIDATE** | nightly + premarket | 0.1d | 30 names emitted nightly with no forward hypothesis attached and no consumer that validates it. Fake-precision risk. |
| **Journal digest + audit + feedback queue** | SUPPORT | nightly | 0.6d | Useful loop, but audits a digest that is itself sprawling. |
| **Dashboards (TUI)** | SUPPORT | on demand | live | Cache-only. Correct. Surfaces the quarantined scanner-truth 0%. |
| **Nightly operator summary** | SUPPORT | nightly | 0.6d | Good idea; inherits the quarantined metric. |

**Counts:** CORE 7 · SUPPORT 12 · RESEARCH_ONLY 5 · QUARANTINE 1 · SUNSET_CANDIDATE 2.

---

## 2. What is duplicated

| # | Duplicate pair/group | Evidence | Recommendation |
|---|---|---|---|
| 1 | **Social Attention V0 vs V11** | V0 writes `social_attention_radar_latest.json` (cron, 0.7d). V11 writes `social_attention_v11_*` (12.6d, no cadence). V11's docstring names V0 as "the production pipeline". | Pick one. V11 is unwired; either wire it and retire V0, or delete V11's cadence ambition and mark it archived. Do not keep both. |
| 2 | **Social Attention Radar vs Social Arb / News Catalyst Radar** | Two "attention" lanes; `social_arb` is actually news-catalyst detection. Already documented as a naming problem in the 1G.15 reality-check. | Rename `social_arb` → News Catalyst Radar at the output boundary; it is not a social lane. |
| 3 | **Topic Shock Detector vs Alpha Heat Radar** | Topic Shock imports `alpha_heat_radar` for `TOPIC_RULES`, `compute_alpha_fit`, `compute_noise_flags`. Alpha Heat has no independent output. | Alpha Heat is a library, not a component. Stop listing it as a radar. |
| 4 | **M1 shadow pool report vs M1 daily review packet** | The packet reproduces the pool's membership, tags and integrity header, and adds the filter and the paste block. | The packet supersedes the pool *report* as an operator surface. Keep the pool *builder*; demote the pool document to audit-only. |
| 5 | **Four "which names should I look at" layers** | Daily Alpha Radar, HC shortlist, EO watch, Alpha Focus, plus the 10x radar — five surfaces answering one question, all regenerated nightly. | Collapse to two: one board (Daily Alpha Radar) and one prioritisation pass (Alpha Focus). HC/EO stay as *labels on the board*, not separate lists. |
| 6 | **`research_programs` vs the forward tracker** | Both compute forward outcomes over the same ledger; programs adds pre-registered gates. Ledger duplication factor **4.63** (7,568 rows → 1,636 episodes). | Keep programs (it has the gates). The 4.63× duplication is a real defect — see §5. |
| 7 | **Scanner recall family** | `scanner_truth_review`, `scanner_recall_repair`, `scanner_recall_diagnostics`, `rs_recall_lane`, `recall_repair_shadow_lane`, `recall_shadow_forward` — six modules on one question, three still nightly. | One recall metric, measured against a *live* pipeline. See §5. |

---

## 3. What is expensive

**Critical finding first: there is no budget enforcement anywhere.**

`core/config.py:70` — `FMP_MONTHLY_BUDGET = int(_opt("FMP_MONTHLY_BUDGET", "0"))`, and
`0 = no cap enforced`. `core/data_gatekeeper.py:166` — `budget_consume()` is
documented **"TELEMETRY ONLY — always returns True (never blocks)"** and ends with
`return True   # always allow — rate bucket is the real gate`. The only real limit
in the system is the 750 RPM token bucket. Every "budget-guarded" and
"budget-capped" claim in the runner refers to a **per-module `--max-calls` flag**,
not to a system budget. CLAUDE.md's "the budget is tracked monthly via
`fmp_budget_monthly`" is true; the implication that it constrains anything is not.

**Observed spend** (`fmp_endpoint_log`): Sept 52,199 · Aug 88,157 · Jul 107,739 ·
Jun 42,304. Daily range over the last 10 days: 52 → **20,260**.

| Component | Default call behaviour | Max possible | Scheduled | Can it run away? | Recommended cap / rule |
|---|---|---|---|---|---|
| `m1_price_refresh` | **plan-only, 0 calls**; `--execute-fetch` required | `DEFAULT_MAX_CALLS = 500`, but `--max-calls` is unbounded (this session passed **3,257**) | no | **Yes** — one flag, no ceiling | Hard ceiling in-module (e.g. 1,200) requiring a second explicit override above it |
| `refresh_universe_prices` | executes by default (`--execute` in runner) | `DEFAULT_MAX_CALLS = 1100` | **nightly + premarket** | Bounded per run; **2,200/day** across both | Keep 1,100; drop the premarket repeat (see §5) |
| `discovery_bootstrap` | executes | `DEFAULT_MAX_CALLS = 150` | nightly | Bounded | Correct as-is |
| `scan_universe_manifest` | executes | `DEFAULT_MAX_CALLS = 400` | nightly + premarket | Bounded; 800/day | Correct as-is |
| `lenses_nightly --max=35` | executes | ~50–100 calls per 10 lenses ⇒ ~175–350 | nightly | Bounded | Correct as-is |
| `lenses_liquid 80` | executes | several hundred | weekly Sat | Bounded | Correct as-is |
| `targeted_backfill` | **dry-run, 0 calls** | `--max-provider-calls` | nightly (dry-run only) | No | Correct as-is — good pattern |
| `research_scanner` | cache-first, degrades gracefully | unbounded in principle | nightly + premarket | Low | Add an explicit cap |
| `gatekeeper_refresh` | earnings calendar only (6h TTL) | ~1–25 | nightly + premarket | No | Correct as-is |
| `social_attention` (V0) | Google Trends best-effort | low | cron Mon-Fri | No | Correct as-is |
| `options_chain_snapshot` | executes | ~20 symbols | user timer | Bounded | Correct as-is |
| `m1_daily_review_packet` | **0 calls**, refuses above 100 planned | 0 | no | No | Correct as-is |
| M1 conviction / analyst-actions studies | executes | **5,612 `/grades` + ~26,700 fundamentals observed** | no (manual) | **Yes** — the 20,260-call day on 2026-09-05 | Require a written pre-registered plan and a hard cap before any study >1,000 calls |

**Worst realistic accident today:** a manual M1 study or an uncapped
`m1_price_refresh` — both are one flag away from five figures, and nothing in the
codebase would stop either.

---

## 4. What actually helps daily manual research

The honest answer: **three artifacts and about fifteen minutes.** Everything else
is either weekly, diagnostic, or accumulating for a decision that is months away.

### Smallest practical daily workflow

```bash
# 1. nothing to run in the morning — the 08:00 ET premarket timer already did it.
#    Confirm it ran and the session is aligned:
GEM_TRADER_SKIP_DOTENV=true .venv/bin/python -m research.evidence_freshness   # or: ./scripts/run_research_cycle.sh freshness-audit
```

**Open, in this order:**

1. `docs/research/DAILY_ALPHA_RADAR_REPORT.md` — the board. **Review the top 5–10
   names only.** More than ten is not research, it is browsing.
2. `docs/research/NIGHTLY_OPERATOR_SUMMARY.md` — what changed and what broke.
   Skim; act only on data-quality warnings.
3. `docs/research/M1_DAILY_REVIEW_PACKET.md` — **only when you want a second,
   independent pool.** 49 candidates today; do not attempt all of them. Pick a
   handful, fill in `bucket`/`notes` by hand.

**How many names:** 5–10 from the board. Up to ~10 from M1 if you use it that
day. A daily review list longer than ~15 names is a sign the filters are not
doing their job.

**What gets ignored, daily:** every `*_audit_*`, `*_diagnostics_*`,
`*_forward_validation_*`, `strategy_lab_*`, `lrr_*`, `core_satellite_*`,
`scanner_recall_*`, `rs_theme_*`, `power_trend_*`, `gatekeeper_precision_*`
sidecar. These are RESEARCH_ONLY. They answer questions already answered.

**What gets tracked:** the forward evidence tracker (automatic), the M1 frozen
cohort (weekly timer), the options chain snapshots (daily timer). Nothing else
needs your attention to keep accumulating.

**What must never be read as a trade signal:** the Daily Alpha Radar board, the
HC shortlist, the EO watch, Alpha Focus ordering, the M1 pool, the M1 review
candidates, the 10x radar, social/news/topic output, and every `PROMISING_*`
verdict token. None of these has a validated forward edge. The M1 pool's edge is
a **basket** property with a **negative** ticker-clustered CI at every horizon —
membership is not a statement about the name.

---

## 5. What should be stopped or demoted

Ranked by cost-to-benefit, worst first.

**1. Stop publishing Scanner Truth Review nightly. (QUARANTINE → off)**
It reports `winner_recall_pct: 0.0` against
`measured_pipeline: "decommissioned_council_funnel_autopsy"`. It is measuring a
funnel that no longer exists, producing a permanent, meaningless 0%, and it is
still read by `dashboards/gem_trader_hq.py:1982` and
`research/nightly_operator_summary.py:92`. A metric that structurally cannot move
off zero is worse than no metric: it trains the operator to ignore a red number.
This is the same class of failure already caught once (the 7-week false HIGH
blocker); it was fixed as a *blocker* but the nightly publication survived.

**2. Fix or stop the 4.63× ledger duplication.**
`research_programs` reads 7,568 ledger rows that dedupe to 1,636 episodes. Every
per-program n is inflated ~4.6× before dedup. The verdicts are safe (all three
say `INSUFFICIENT_MATURE_EVIDENCE`), but any future "we have n=800 episodes"
claim is a fake-precision trap waiting to be sprung.

**3. Retire the 10x candidate radar. (SUNSET)**
Emits 30 names nightly, twice a day, with no forward hypothesis registered and no
consumer that validates it. It is a list that looks like a finding.

**4. Wire or delete Social Attention V11. (SUNSET as-is)**
12.6 days stale, no cadence, duplicates V0. Two implementations of one idea, one
of which silently does nothing, is pure maintenance burden.

**5. Drop the premarket repeat of `refresh_universe_prices`.**
1,100-call ceiling, run twice a day. The nightly run already refreshed the
universe; the premarket comment concedes "only tickers that missed it get a
provider call". That is a genuine but small benefit for up to 1,100 calls of
headroom. Halving this is the cheapest budget win available.

**6. Demote the nightly tail: `rs_theme_triage`, `recall_repair_shadow_lane`,
`recall_repair_shadow_forward`, `scanner_truth_review`.**
Their verdicts are in (`NO_VALUE` for RS/theme forward as of 2026-06-03;
`NEED_MORE_DATA` for the shadow lane). Move to weekly, or to on-demand.

**7. Restructure the Daily Research Digest.**
187 lines, 18 sections, numbering that skips 2 and forks into 4/4b/4b1/4b2/4c/4d,
including a `### Legacy / Decommissioned Recall Diagnostics` section. It has
become the place where every new sidecar gets appended. Cap it at ~40 lines and
require a section to earn its place.

**8. Garbage-collect the artifact directory.**
1,514 of 1,636 per-ticker artifacts are >7d stale; the oldest stock lenses are
134 days old. 110 of 130 research documents are >30 days stale. A reader cannot
distinguish current truth from June's truth by looking at the directory.

---

## 6. What should remain read-only

Preserve for audit and history; never consult for candidate selection.

- **The whole historical replay harness** — `strategy_lab_*`, `strategy_walk_forward*`,
  `strategy_threshold_sweep*`, `leader_reset_*`, `lrr_*`, `core_satellite_*`,
  `strategy_failure_reason_miner`, `filter_replacement_counterfactual`,
  `accepted_loser_pattern`, `rejected_winner_pattern`. All 85–90 days frozen, all
  verdicts recorded. Their value is that they say *no* and are dated.
- **The frozen M1 manual-pick cohort** — write-once, git-tracked. The commit is
  what proves the hypothesis predated the outcome. Read by the tracker only.
- **The M1 shadow source pool document** — audit record of what was published.
- **All `*_forward_validation_latest.json` with matured negative verdicts** —
  RS/theme (`NO_VALUE`), gatekeeper precision, power trend, short detection.
- **The universe/scanner recall studies** — `universe_forward_replay`,
  `scanner_recall_*`, `rs_recall_*`. Answered; keep dated.
- **MCP per-ticker audits** — 113d stale by design; snapshots, not state.
- **`docs/research/AUTO_TRADING_DECOMMISSION_FINAL_FINDINGS.md`** and the
  decommission-era docs.

---

## 7. Current evidence levels

| Path | Level | Basis |
|---|---|---|
| **M1 as an unordered basket** | **provisional pool-level evidence** | ~+1 to +2pp per 60d vs same-date universe; the only positive finding across five passes |
| **M1 per-name selection** | **contradicted by replay** | Ticker-clustered CI negative at every horizon (60d: [−4.60, −3.55]). 13 conviction overlays, an earnings-surprise dataset and a 65-feature ridge all failed |
| **M1 analyst-action overlays** | **contradicted by replay** | 0 of 18 earned `PROMISING_CONVICTION_OVERLAY`; upgrades +0.73pp *and* downgrades +0.50pp ⇒ attention, not direction. Downgrades fail as an avoid filter |
| **M1 human selection (frozen cohort)** | **forward-only / immature** | `NOT_MATURE`; decisive 45d ≈ 2026-11-06, 60d ≈ 2026-11-27 |
| **Broad forward tracker (all labels)** | **not enough evidence** — and this is the robust one | 7,577 entries, 5,901 matured 10d, 784 tickers, `sample_status: ROBUST`, **verdict MIXED**. 60d+ horizons: **0 matured** |
| **TACTICAL program** | **not enough evidence** | n=848 episodes; 5d vs SPY mean −0.37%, win 49.4%, CI [−1.22, +0.45] |
| **SWING program** | **not enough evidence** | n=594; 5d vs SPY mean −0.54%, win 39.6%, CI [−1.95, +1.08] |
| **LONG_TERM program** | **not enough evidence** | n=194; 30d vs SPY mean −2.49%, win 46.5%, CI [−6.93, +2.49] |
| **HC shortlist** | **forward-only / immature** | Forward hypothesis registered separately; not matured |
| **EO watch** | **forward-only / immature** | `NEED_MORE_DATA` |
| **Social Attention (V0)** | **forward-only / immature** | `PROMISING_BUT_UNPROVEN` |
| **News Catalyst Radar** | **not enough evidence** | No registered forward hypothesis |
| **Topic Shock** | **forward-only / immature** | Shadow mode; accumulating only |
| **RS/theme lens routing** | **contradicted by replay** | Matured `NO_VALUE` 2026-06-03: LENS_READY +0.77% vs random +1.21% |
| **Scanner recall (legacy funnel)** | **QUARANTINE — not a live metric** | Measures a decommissioned pipeline; structurally pinned at 0.0% |
| **Core-Satellite** | **provisional / forward-only** | Gate pass in-window; forward shadow ledger seeded, 14.7d stale |
| **Options / IV-rank work** | **not enough evidence (by design)** | Collecting; PARTIAL ≈ Sep 2026 |
| **Anything, as a validated edge** | **none** | **No path in this system has a validated forward edge.** |

---

## 8. Are we still research-only?

**Yes — this is the part of the system that has held.** Verified:

- `alpha_focus_latest.json` → `promote_to_signal: False`, with an explicit
  purpose statement ("does not delete candidates, change scores, change rankings,
  change scanner logic, or create trade signals") and eight `no_*_change`
  guardrails.
- The M1 packet carries `forbidden_by_design` (never ranks/scores/orders, never a
  conviction tier, never per-name momentum, never a trade signal, never a live
  verdict, never a provider call by default) and enforces a language guard at
  render time (`assert_packet_language`) over both the document and the paste block.
- `FORBIDDEN_PHRASES` / `ABSOLUTE_FORBIDDEN` block `validated_edge`, "top pick",
  "conviction tier", "price target", "position size" and similar across replay
  outputs, with a negation window so disclaimers remain sayable.
- Replay modules write only into their declared namespaces; `LiveArtifactTripwire`
  aborts on any live-artifact movement. The M1 run in this session reported
  `TRIPWIRE: CLEAN`.
- All sleeves remain decommissioned; `gem-trader.service` is stopped and disabled;
  no order-routing path is reachable.
- 3,240 tests collected, with explicit guards on terminology
  (`test_alpha_engine_terminology.py`) and on packet language.

**Two soft spots, neither a violation:**

1. **Verdict tokens read as promises.** `PROMISING_BUT_UNPROVEN`,
   `CORE_ENGINE_CANDIDATE`, `READY_TO_FEED_LENS`, `LENS_READY` are honest inside
   their own report and optimistic-sounding out of context. They travel into the
   digest and dashboard where the qualifier is easy to lose.
2. **The 10x candidate radar** emits a 30-name list nightly with no registered
   hypothesis. It does not claim edge, but its shape is a shortlist.

---

## 9. Recommended operating model

### Daily — cache-only, ~15 minutes, 0 extra provider calls

The 08:00 ET premarket and 20:30 ET nightly timers already do the work.

1. `./scripts/run_research_cycle.sh freshness-audit` — confirm the session aligned.
2. Open **Daily Alpha Radar** → review **5–10 names**.
3. Skim **Nightly Operator Summary** → act only on data-quality warnings.
4. Ignore every diagnostic sidecar. If a sidecar has not changed a decision in 30
   days, it is not a daily artifact.

### Weekly

- **Saturday (automatic):** the M1 cohort tracker timer, ~75 calls. No action.
- **Saturday (automatic):** the weekly liquid lens refresh.
- **Manual, once:** run the **M1 daily review packet** and work the **49
  candidates** — this is a weekly-depth exercise, not a daily one. Refresh the M1
  cache only if the guard refuses, and cap it (see §3).
- **Manual, once:** review the journal feedback queue.

### Monthly

- Check `fmp_budget_monthly` against the run rate. Until a real cap exists, this
  is the only budget control that works, and it is a human reading a number.
- Garbage-collect artifacts older than 30 days.

### The cohort tracker process

Leave it alone. It is write-once, git-tracked, on a weekly timer, and its first
decisive read is **≈2026-11-06** (45d). Do not re-run, re-date, re-tune or
re-interpret it before then. Reading it early is the failure mode it was designed
against.

### When to use social / topic / digest — and when to ignore them

- **News Catalyst Radar:** use it *after* a name is already on your list, to
  answer "why is it moving?". Never as a source of names.
- **Social Attention V0:** same. `PROMISING_BUT_UNPROVEN` means "not yet a reason".
- **Topic Shock:** ignore entirely. It is in shadow mode and nothing reads it.
- **Digest:** read sections 1 (Data Quality) and 7 (Final Finding). Ignore the rest
  until it is restructured.

---

## 10. Final verdict

### Are we drifting?

**Yes — toward apparatus, not toward bad decisions.** The research posture is
intact and genuinely well-guarded; what has drifted is the ratio of measurement
to measured. 49 nightly steps, 97 runner commands, 139 modules and 1,846
artifacts exist to serve a daily decision that involves reading about ten names.
Roughly a third of the nightly cycle now refreshes diagnostics whose verdicts
were reached in June, and at least one nightly step measures a pipeline that was
decommissioned in 2026-06.

The strongest evidence of drift is not any single component — it is that the one
dataset that finally became **ROBUST** (7,577 forward entries, 784 tickers, 60
dates) returned **MIXED**, and the system's response has been to add more
measurement rather than to sit with that answer.

### Stop immediately

1. **Publishing Scanner Truth Review's 0.0% recall.** It measures a decommissioned
   funnel and cannot move off zero.
2. **Uncapped provider spending.** `budget_consume()` never blocks and
   `FMP_MONTHLY_BUDGET` defaults to 0. Either set a real cap or stop describing
   anything as "budget-guarded". A 20,260-call day happened four days ago.
3. **The 10x candidate radar**, until it has a registered forward hypothesis.
4. **Maintaining two social attention radars**, one of which has no cadence.

### Keep doing

1. **The forward evidence tracker.** It is the only thing here that will ever
   settle an argument. 98% resolution coverage is genuinely good engineering.
2. **The M1 frozen cohort discipline** — write-once, pre-registered falsification,
   decisive horizons fixed in advance, control arm chosen correctly (the 65
   not-picked names, not SPY). This is the best methodological work in the repo.
3. **The refusal culture** — data guards, language guards, tripwires, dry-run
   defaults, `promote_to_signal: False`. This is why nothing has quietly become a
   trade signal.
4. **Options chain accumulation.** Cheap, bounded, and the only path to a
   genuinely new data class.
5. **The M1 strict review filter.** 75 → 49 at zero call cost is exactly the right
   shape of improvement.

### Measure next

1. **Let the 60d horizon mature.** 5,901 entries are matured at 10d; **zero** at
   60d. Every negative result so far is a short-horizon result, and the M1 pool's
   own edge is a 60-day effect. We are judging swing and long-term hypotheses at
   10 days.
2. **The M1 cohort at 45d/60d** (Nov 6 / Nov 27). Do not read it before.
3. **De-duplicate the episode ledger** (4.63×) before any n-based claim is made.
4. **One honest recall metric** against the *live* scanner, replacing six modules
   measuring a dead funnel.

### Do not build next

1. **No new radar, lens, overlay, detector or shortlist.** Five surfaces already
   answer "which names should I look at".
2. **No per-name M1 ranker.** Five independent attempts have failed; the negative
   is measured, not assumed.
3. **No new nightly step.** The nightly cycle is at 49 and should shrink.
4. **No dashboard work.** Existing operator guidance already says CLI + JSON
   sidecars first, and the dashboard currently surfaces a quarantined metric.
5. **No promotion of any `PROMISING_*` token into a routing or selection rule**
   before its own pre-registered maturity floor.

---

*Nothing in this audit is a validated edge, and nothing in it should change a live
rule. It recommends removals and cadence changes only; no code, threshold,
membership, cohort, routing or dashboard was modified to produce it.*

# LLM Agent Overlap Review — 2026-09

*Generated 2026-09-11. Read-only review of code, docs and cached artifacts. No
provider calls (FMP, Tradier, DeepSeek or otherwise), no code changes, no commit.
The only command executed against runtime state was the read-only
`research/feedback_queue.py review`.*

> **RESEARCH ONLY.** This reviews the research system's own tooling. It contains
> no candidate list, no ranking, no score and no trade language.

**Question:** before building a Research Governor / Alpha Research Agent, does the
existing DeepSeek/LLM daily-digest agent already do that job?

---

## 0. Short answer

**No. The overlap is in infrastructure, not function.** The existing agent is a
nightly **digest auditor**. It reads one generated text note (187 lines, 19
sections) and returns a structured critique of that day's research output, plus
templated repair tasks. It governs nothing, because it cannot see anything the
digest does not print. That rules out:

- provider spend
- the quarantine registry
- artifact staleness across the cache
- duplicate surfaces
- runner cadence
- decision dates

Most of the "governor" functions you asked about now exist somewhere in the repo,
but they are **deterministic, scattered, and mostly not LLM**:

- `core/quarantined_surfaces.py` and `core/provider_budget.py` (both added
  2026-09-09 in 36da6b7)
- the one-off component classification in `SYSTEM_DRIFT_AUDIT_2026_09.md`
- the stop/continue framework in `alpha_failure_root_cause_audit.py`

Nothing ties those together into a durable, dated, per-component decision record.

**Recommendation: `BUILD_SEPARATE_RESEARCH_GOVERNOR`.** It should be a small,
deterministic, on-demand checker plus a git-tracked component registry. It should
not use an LLM in v1 and should not be an autonomous agent. The "Alpha Research
Agent" half of the idea is **not built now**. That half means an LLM proposing
hypotheses or alpha changes, and `docs/ROADMAP_PHASES.md` → *Recursive
Self-Improvement Governor* holds it until the 45d/60d windows mature.

---

## 1. What exists

### 1.1 The digest → audit → queue pipeline (nightly tail)

These are the last two steps of `cmd_nightly` in `scripts/run_research_cycle.sh`,
after `forward-milestones → operator summary → cohort attribution → alpha
root-cause → operating policy → alpha focus → topic shock`:

```
cmd_journal_digest   scripts/generate_research_journal_digest.py --append
    │                  + dashboards/research_command_center/journal_digest.py
    │                  deterministic, cred-free, NO LLM → data/research/journal.jsonl
    ▼
cmd_journal_audit    research/journal_audit_reviewer.py
    │                  regex pre-parse of the digest text (extract_digest_signals)
    │                  → ONE DeepSeek call via core/llm_clients (role "chat")
    │                  → sanitize_audit() re-enforces invariants on both paths
    │                  → deterministic next_system_actions are the contract;
    │                    LLM proposals only fill uncovered areas
    │                  → any failure → rule-based fallback (LLM_FAIL_OPEN)
    ▼ writes:
      cache/research/journal_audit_latest.json          (sidecar, overwritten)
      logs/research_engine_feedback_queue.jsonl         (append, deduped per digest)
      data/research/journal_audit_history.jsonl         (append, trend input)

(human, on demand)
research/feedback_queue.py review | resolve | dismiss
      → data/research/feedback_queue_state.jsonl        (append-only lifecycle)
```

### 1.2 File ownership

| Role | File | Notes |
|---|---|---|
| Digest generator (no LLM) | `scripts/generate_research_journal_digest.py`, `dashboards/research_command_center/journal_digest.py` (1,768 lines) | 19 sections today, including `### Legacy / Decommissioned Recall Diagnostics` |
| LLM auditor | `research/journal_audit_reviewer.py` (2,320 lines) | Prompts at `:1531` (`_SYSTEM_PROMPT`) and `:1555` (`_USER_PROMPT_TEMPLATE`); sanitizer `:1987`; writer `:2164` |
| Queue consumer | `research/feedback_queue.py` (500 lines) | Review, resolve, dismiss. Never executes a task |
| LLM abstraction | `core/llm_clients/{base,deepseek_client,anthropic_client,provider,usage}.py` | Forced safety fields; daily call/token caps; usage ledger |
| LLM doctrine | `docs/ops/LLM_PROVIDER.md` | Service inventory and safety contract |
| Re-audit hook | `research/forward_evidence_milestones.py` | Writes `reaudit_due` on the first 20d cohort / HC 10d≥10 crossing |
| Tests | `test_journal_audit_reviewer.py` (74), `test_llm_clients.py` (37), `test_research_journal_digest.py` (54), `test_feedback_queue.py` (8) | |

### 1.3 Other LLM call sites (none of them govern anything)

| Call site | What the LLM does |
|---|---|
| `research/social_arb_radar.py` `llm_review` | Per-candidate KEEP / DROP / NOISE noise filter over news-catalyst names. This is the only KEEP vocabulary in LLM code, and it is ticker-level |
| `core/executive_gatekeeper.py` `_try_llm_summary` | Plain-English restatement of an already-final verdict |
| `dashboards/gem_trader_hq.py` `LLMAnalyzer` | Operator-triggered per-ticker framing panel |

**Checked and found to have no LLM:**
- `research/nightly_operator_summary.py`: zero LLM references.
- `research/mcp_audit_orchestrator.py`: explicitly "no LLM".
- The `stocklens-audit` MCP tools: cache readers only.
- `.claude/`: contains only `settings.local.json`. There are no agents, commands,
  skills or hooks.
- **"IRS":** nothing by that name exists in `docs/`, `research/`, `core/`,
  `scripts/` or `dashboards/`.

### 1.4 Live state (2026-09-11)

**Latest audit:** 00:41 UTC.
- `audit_source=llm`, provider deepseek
- `RESEARCH_ONLY` / `OPERATIONAL_WITH_BLOCKERS` / `MIXED`
- 10 flaws, 4 actions
- All three programs `INSUFFICIENT_MATURE_EVIDENCE`

**Audit history:** 52 audits (39 LLM, 13 fallback). The last fallback was
2026-08-27 (`stop_reason=max_tokens`); there have been none since the
chat-role switch.

**LLM usage ledger:** 89 calls since 2026-07-17.
- 48 journal audit, 40 social-arb (1 error), 1 probe.
- The last audit call used 9,135 prompt + 10,949 completion tokens and took
  55.8 s.

**Queue:**
- 361 lines: 178 `system_repair_task`, 166 legacy `recommended_task` (no longer
  queued since 2026-08-25), 17 untyped early rows.
- 147 distinct task ids: 4 open, 12 addressed, 27 dismissed, 79 superseded,
  25 resolved-by-audit.
- **The state ledger was last written 2026-08-27**, so nobody has triaged the
  queue for 15 days while the nightly run keeps appending.

---

## 2. The eleven questions

### Q1. What does the existing DeepSeek/LLM agent actually do?

It audits **one day's digest text**. It returns a fixed schema:

- `research_verdict`, `alpha_discovery_quality`, `engine_health`
- `program_verdicts` (restated from the digest, never LLM-decided)
- `candidate_quality_summary` (the LLM may only demote)
- `missing_evidence_or_artifacts`, `unbiased_next_steps`, `what_is_working`
- `flaws_detected` (severity × area)
- `next_system_actions` (P0–P2 × six fixed areas)
- a five-audit `audit_trend`

It is good at **intra-digest** consistency. Today it caught three problems:
- a social-attribution wording conflict;
- a mean-contaminated repeat-cohort figure (+39.1% mean against a −1.02% median);
- BMNR (a severe-dilution name) sitting in "Top research names".

### Q2. What files and scripts own it?

See §1.2. The core is `research/journal_audit_reviewer.py`, called by
`cmd_journal_audit` as the last nightly step. The queue side is owned by
`research/feedback_queue.py`.

### Q3. Does it only summarize daily candidates, or govern the whole system?

**Neither, strictly. It audits the digest, and sees the system only through the
digest.**

The only files it reads are the latest `journal.jsonl` note and its own
`journal_audit_history.jsonl`. Every "system" judgment it makes is a regex hit on
digest prose, for example "Scan readiness DEGRADED", "quarantined: N" or "legacy
universe snapshot STALE". Components the digest does not mention are invisible to
it:

- the 10x radar
- Social Attention V11
- ~30 frozen replay sidecars
- the scanner-recall family
- provider spend
- runner cadence

### Q4. Does it classify components as KEEP / FIX / QUARANTINE / SUNSET / WAIT?

**No.** Its enums describe the day's evidence and engine health, not
components. `ACTION_AREAS` is a fixed six-tuple (`scanner_recall`,
`forward_evidence`, `options_overlay`, `data_quality`, `fundamental_overlay`,
`sector_alignment`). There is no component identity, no status and no date.

Component-like classification exists only outside the agent, in four
incompatible vocabularies:

| Where | Vocabulary | Scope | Recurring? |
|---|---|---|---|
| `SYSTEM_DRIFT_AUDIT_2026_09.md` §1 | CORE / SUPPORT / RESEARCH_ONLY / QUARANTINE / SUNSET_CANDIDATE | 26 components | **No.** A one-off markdown document from 2026-09-09 |
| `core/quarantined_surfaces.py` | CURRENT / DECOMMISSIONED_AUTOPSY / QUARANTINE_NO_HYPOTHESIS / SUPERSEDED_STALE | 4 named artifacts + 1 classified from its own fields | Yes, but it decides **display eligibility** only |
| `research/alpha_failure_root_cause_audit.py` | CONTINUE / NARROW / FREEZE / SUNSET | The alpha-engine claim as a whole | Nightly |
| `research/research_operating_policy.py` | focus_now / use_caution / discovery_only / do_not_conclude_yet | Candidate **cohorts** | Nightly |

None of them has FIX or WAIT, and none covers all components.

### Q5. Does it check API spend and provider-call risk?

**No.**
- **FMP spend.** The agent never sees it. Since 36da6b7, spend is *enforced* in
  `core/provider_budget.py` (120k/month and 12k/day defaults; runs planning
  more than 100 calls need an override). But no recurring surface reports
  month-to-date spend against the cap: the digest has zero references to
  `fmp_budget` / `provider_budget` / spend. `DAILY_OPERATING_NOTE.md` still says
  to check it by hand monthly.
- **LLM spend.** `core/llm_clients/usage.py` enforces 200 calls and 400k tokens
  per UTC day before each call, and logs to `logs/llm_usage.jsonl`. **Nothing
  outside `core/llm_clients` reads `usage_today()` or `get_llm_status()`.**
  `LLM_MONTHLY_BUDGET_USD` is documented as not enforced.
- **Provider-call risk in its own output.** The agent can recommend
  provider-spending work with no cost attached. Today's P1 `data_quality`
  action reads "Run the targeted price-cache backfill plan (dry-run first, then
  `--execute`)…". It has no call estimate and no reference to the planned-call
  gate.

### Q6. Does it detect stale or duplicated surfaces?

- **Stale: partially, and only when the digest says so.**
  - It has CRITICAL rules for mixed-session benchmarks and "stale contextual
    modules" (`_FROZEN_SOURCE_RE`), plus counters for stale-price and
    suspect-feed.
  - It does not inspect artifact ages itself.
  - Its track record here is weak. It raised a **false HIGH blocker for about
    7 weeks** off the decommissioned council-funnel recall (drift audit §5.1).
    It was fooled by a stale surface rather than detecting one.
- **Duplicated: no.** There is no flaw area or logic for duplication. The drift
  audit found 7 duplicate groups (§2 there), including five surfaces answering
  "which names should I look at". The nightly audit has raised none of them.

### Q7. Does it prevent dashboards/digests from reading quarantined outputs?

**No, and neither does anything else, fully.** `core/quarantined_surfaces.py` is
opt-in per reader, and its contract is advisory: "the caller is expected to
render the label".

- **Importers:** `dashboards/gem_trader_hq.py`, `research/scanner_truth_review.py`,
  `research/daily_alpha_radar_report.py`, `research/topic_shock_detector.py`,
  `research/alpha_heat_radar.py`.
- **Not importers:** the journal digest (`journal_digest.py`), the command
  center (`data_adapter.py`), and the audit reviewer.
- The digest uses its own substring heuristic instead
  (`_is_legacy_recall_warning`, `journal_digest.py:410`). It still renders a
  legacy-recall section (`:1583`). It never reads the 10x or V11 artifacts, so
  it is safe by **omission**, not by gate.
- `DAILY_OPERATING_NOTE.md` claims "the operator surfaces ask it before
  rendering anything". That is true for the TUI and false for the digest and
  command center.
- **Cadence:** `scanner_truth_review` is relabelled but still **published
  nightly**. Drift audit §5.1 asked for it to stop.

### Q8. Does it track evidence maturity and failed gates?

**Partially. It tracks forward-tracker maturity, as printed in the digest. It
does not track decision dates or failed gates.**

What exists:
- It parses matured 5d/10d/20d counts, the tracker verdict, moment-of-truth,
  Phase 4B and program verdicts.
- Its invariants pin `RESEARCH_ONLY` whenever there is NO_FORWARD_EDGE or
  Phase 4B is BLOCKED.
- The digest adds 20d/45d/60d maturity ETAs.
- `forward_evidence_milestones.py` fires a one-time RE-AUDIT DUE on the first
  milestone crossings.

What it misses — a concrete defect found in this review:
- `alpha_failure_root_cause_audit.py:1033` sets
  `"next_decision_date": DECISION_DATES[0]`. The digest therefore printed
  **"Next decision date: 2026-08-17 (current recommendation: CONTINUE)"** on
  2026-09-11, 25 days after that date passed.
- The 2026-09-07 checkpoint passed with no recorded decision.
- The **2026-09-30 SUNSET gate, 19 days out**, is not shown anywhere current.
- Today's LLM audit repeated the stale date ("pre-registers its next decision
  date (2026-08-17, CONTINUE)") without flagging that it had passed.

Maturity dates for other components live only in prose and are tracked nowhere:
- M1 frozen cohort at 45d ≈ 2026-11-06 and 60d ≈ 2026-11-27
- options IV-rank PARTIAL ≈ Sep 2026
- the HC shortlist floor
- the recall shadow lane

Pre-registered **failed** gates are not ledgered either, for example RS/theme
`NO_VALUE`, recall `LOOSE_NOT_BETTER`, the LRR family archive and the
directional-options archive.

### Q9. Does it write a durable decision queue?

**It writes a durable _task_ queue, not a decision queue.** The mechanics are
sound:
- append-only queue plus an append-only lifecycle ledger;
- stable task ids (sha256 of kind|priority|area|text);
- commit linkage;
- six statuses;
- a strict "human selects" rule.

But the entries are engineering chores ("build X", "report Y"), not "component C
→ status S by date D on evidence E". There are also two health problems:

1. **Self-re-queueing P0s.** `8d01d462a3bb` (forward_evidence) has been queued
   **49 times** and `2e43d991d887` (scanner_recall) **36 times**. Both are
   marked ADDRESSED, and both were re-emitted again on 2026-09-11. Their
   triggers (`build_next_system_actions`, `:1145–1199`) key on conditions no
   code fix can clear:
   - `forward_immature` / `phase4b_blocked` — a maturity state;
   - any `scanner_recall` flaw, including today's LOW one.

   This is the exact failure class the code already retired for
   candidate-quality (comment at `:1232–1236`, commit 031f9ab).
2. **Triage has stalled** since 2026-08-27 (see §1.4).

### Q10. Does it have authority boundaries and no-build/no-provider-call rules?

**Strong boundaries on what it says about stocks and verdicts. None on system
growth or spend.** In place today:

- **Prompt hard rules:** never promote, never predict direction, never invent
  data, never propose score/gate/watchlist changes, and loosening only as a
  forward-validated experiment.
- **Code, regardless of what the LLM says:**
  - `sanitize_audit` coerces every enum;
  - `promote_to_signal=False` is forced;
  - Phase 4B or a negative forward result pins `RESEARCH_ONLY`;
  - `apply_safety_fields` sets `may_change_{scores,rankings,gates,verdicts}=false`;
  - the LLM cannot promote a tier or drop the deterministic HC flaws;
  - it fails open to a deterministic fallback;
  - daily LLM caps apply.
- **Doctrine:** the queue is never auto-executed
  (`feedback_queue.py`: "HUMAN SELECTS"). `ROADMAP_PHASES.md` says AI may
  review, propose, test and explain, but may not rewrite production alpha logic.

**What is missing:**
- **No no-build rule.** Its standing P0 asks to "Build scanner recall
  diagnostics", which already exist. It has no knowledge of the drift audit's
  *Do not build next* list (no new radar, no new nightly step, no dashboard
  work). A daily auditor that proposes engineering work every night, with no
  view of system size, pushes toward the apparatus drift the 2026-09 audit
  diagnosed.
- **No provider-call rule** on its recommendations (Q5).
- **Doc drift.** `LLM_PROVIDER.md` and the `_llm_audit` docstring say the audit
  uses role `reasoner`. The code has used `role="chat"` since 8352a7e
  (2026-08-25, "reasoner was truncating").

### Q11. Extend it safely, or keep the governor separate?

**Keep the governor separate.** Reuse the infrastructure, not the agent. Reasons:

1. **Wrong input contract.** The agent's whole design, including the regex
   pre-parse, the prompt and the sanitizer, is built around one digest note. A
   governor needs a component inventory, artifact ages, runner cadence, spend
   counters and dated gates. Extending means replacing its input, which makes
   it a different module wearing the same name.
2. **Circularity.** The digest and its audit are themselves components the
   governor must classify. The drift audit rates the digest "SUPPORT
   (degrading)". The auditor cannot also be the judge of whether it should
   exist.
3. **Wrong cadence.** Component status changes weekly or monthly, not nightly.
   The drift audit says the 49-step nightly run must shrink, and that no new
   nightly step should be added. Governance bolted onto the last nightly step
   would run 5× a week for information that changes about once a month.
4. **Wrong tool for the core job.** Component status should be a human
   decision recorded with evidence. Staleness, quarantine consistency, spend
   and date-passed checks are deterministic and cheap. An LLM adds tokens and a
   fallback path, and does not make any of them more correct. The one LLM call
   is already large (≈20k tokens) and has truncated twice (2026-07-16,
   2026-08-27).
5. **Blast radius.** Today one fallback degrades only the audit. If the audit
   and the governance checks shared one call, a single DeepSeek failure would
   take out both.

What to reuse:
- the append-only ledger and task-id pattern (`feedback_queue.py`);
- `apply_safety_fields`;
- the `core/llm_clients` gate, if an LLM is ever added;
- the existing `core/quarantined_surfaces.py` and `core/provider_budget.py` as
  the governor's sources of truth;
- the runner's `[CACHE]` command pattern.

---

## 3. Where governor functions already live

| Governor function | Exists today | Gap |
|---|---|---|
| Component inventory + status | Drift audit §1 (markdown, one-off) | Not machine-readable, not recurring, no FIX/WAIT, no dates |
| "May this speak as current?" | `core/quarantined_surfaces.py` | Opt-in; the digest and command center don't consult it |
| FMP spend enforcement | `core/provider_budget.py` | No recurring month-to-date vs cap readout |
| LLM spend enforcement | `core/llm_clients/usage.py` | No reader; the monthly USD cap is not enforced |
| Engine stop/continue gate | `alpha_failure_root_cause_audit.py` | `next_decision_date` stuck on the first date; 09-30 SUNSET gate not surfaced |
| Cohort-level guidance | `research_operating_policy.py` | Covers cohorts, not components |
| Maturity milestones | `forward_evidence_milestones.py` + digest ETAs | Forward tracker only; other components' dates live in prose |
| Repair-task queue | journal audit + `feedback_queue.py` | Task-shaped; P0 self-re-queue; triage stalled |
| Tracking drift-audit recommendations | **Nothing** | Only visible by reading the runner. #3 10x is off schedule (`# cmd_ten_x_candidates`). #1 scanner truth is relabelled but still nightly. #5 premarket universe refresh and #6 nightly-tail demotions (rs_theme_triage, recall_repair_shadow_lane/forward) were not applied |

---

## 4. Defects found during this review (none fixed — no-code mission)

| # | Defect | Where |
|---|---|---|
| D1 | Two P0 actions re-queue nightly after being addressed (×49, ×36); triggers key on maturity state or any recall flaw | `journal_audit_reviewer.py:1145–1199` |
| D2 | Root-cause "next decision date" is hard-coded to the first date; prints 2026-08-17 on 2026-09-11; 09-30 SUNSET gate not surfaced | `alpha_failure_root_cause_audit.py:1033` |
| D3 | Docs say the journal audit uses role `reasoner`; code uses `chat` | `docs/ops/LLM_PROVIDER.md` (inventory + overrides), `_llm_audit` docstring |
| D4 | Digest and command center don't consult `quarantined_surfaces`, contrary to the operating note | `journal_digest.py`, `data_adapter.py`, `DAILY_OPERATING_NOTE.md` |
| D5 | Feedback-queue triage stalled since 2026-08-27 (4 open, 3 addressed-unconfirmed) | `data/research/feedback_queue_state.jsonl` |
| D6 | LLM usage ledger has no consumer | `logs/llm_usage.jsonl` |
| D7 | Root-cause rationale says "continue collecting evidence toward a **promotion review**" under a permanent research-only posture. It cites a "ROBUST" HC 10d read while the same digest's HC forward validation reads `NO_IMPROVEMENT_OVER_PROGRAM_CANDIDATES`. The two answer different questions (absolute vs relative-to-program), but no layer says so | `alpha_failure_root_cause_latest.json` |

D1–D3 are small fixes to existing code and docs. They should happen **before**
any governor exists; otherwise its first run just re-reports them.

---

## 5. Recommendation

## **`BUILD_SEPARATE_RESEARCH_GOVERNOR`**

This comes with scope limits that are part of the recommendation, not optional:

- **Deterministic. No LLM in v1.**
- **On demand, weekly by hand.** No timer, no nightly step, no digest section
  in v1.
- **Zero provider calls, zero LLM calls.** Enforced by a forbidden-import test.
- **Proposes only.** It never edits the registry, the runner, artifacts or
  verdicts. SUNSET means "a human stops the cadence and quarantines the
  display"; files are preserved.
- **Not the Alpha Research Agent.** An LLM that proposes hypotheses or alpha
  changes stays `DO_NOT_BUILD_AGENT_YET` under `ROADMAP_PHASES.md` → *Recursive
  Self-Improvement Governor*. The earliest review is after the M1 cohort's 45d
  (≈2026-11-06) / 60d (≈2026-11-27) reads, and after the forward tracker has
  matured 60d episodes (0 today). It must also pass that section's
  production-change checklist.

Why build it, when the drift audit says "no new radar/step/surface"? Because
this is the tool for **removing** components. The drift audit made eight
stop/demote recommendations. Two weeks later, only the 10x radar is fully off
schedule, and nothing records which of the eight were accepted, deferred or
rejected. The governor gives SUNSET decisions a home. It must pass the same test
it applies to others (§6, step 5).

Why not extend the DeepSeek agent: see Q11. It is the right tool for its job
(daily digest critique). Fix D1–D3 in it and leave its scope alone.

---

## 6. Smallest implementation path

**Step 0 — fix the existing agent first (separate small approvals; not governor work).**
- D1: suppress the two P0 actions when the deliverable is ADDRESSED in the state
  ledger, or re-key them on a deliverable-missing condition. Keep the underlying
  flaw visible.
- D2: compute `next_decision_date` as the first date ≥ today.
- D3: correct the role in `LLM_PROVIDER.md` and the docstring.

**Step 1 — `data/research/component_registry.json` (git-tracked, human-authored).**
Seed it from drift-audit §1, which gives about 26 rows. Each row holds:

- `id`, `owner_files`, `artifacts` (globs), `runner_cmds`
- `cadence` (nightly / premarket / weekly / manual / none)
- `answers_question` (a short key, used for duplicate detection)
- `status` ∈ {KEEP, FIX, QUARANTINE, SUNSET, WAIT}
- `evidence` (a doc or sidecar reference), `decision_date`
- `est_provider_calls_per_run`, `consumers`

Each status carries a mechanical evidence requirement:

| Status | Must have |
|---|---|
| KEEP | Declared cadence, an artifact fresh for that cadence, at least one named consumer |
| FIX | A linked feedback-queue task id or commit, plus a due date |
| QUARANTINE | Classified non-current by `core/quarantined_surfaces.py` (checked in both directions) |
| SUNSET | QUARANTINE requirements + no longer invoked by the runner; files preserved |
| WAIT | A pre-registered decision/maturity date and the gate that decides it |

Status changes only by human commit. **Git history is the decision ledger**, so
v1 needs no new JSONL ledger.

**Step 2 — `research/research_governor.py`.** Deterministic, cred-free,
read-only, roughly 300–400 lines. Its checks:

1. **Freshness vs cadence.** Artifact `generated_at` / mtime against the
   declared cadence.
2. **Quarantine consistency, both directions.** Also list readers of
   QUARANTINE/SUNSET artifacts that don't import `quarantined_surfaces`
   (static scan). Today this would flag D4.
3. **Cadence consistency.** SUNSET/QUARANTINE rows still invoked from
   `cmd_nightly` / `cmd_premarket` (static parse of the runner). Today this
   would flag `scanner_truth_review`.
4. **Spend.** `provider_budget.describe()` + FMP month-to-date (read-only
   `SELECT` on `fmp_budget_monthly`) + `usage_today()`. Also the registry's
   calls-per-run × cadence, giving projected monthly spend per component.
5. **Dates.** A passed `decision_date` on a WAIT row → `DECISION_DUE`; one
   within 14 days → `UPCOMING`. Today this would surface D2 and the 09-30 gate.
6. **Orphans.** Runner commands and `cache/research/*_latest.json` files that no
   registry row claims.
7. **Duplicates.** More than one KEEP row with the same `answers_question`.

Output goes to `cache/research/research_governor_latest.json` and
`logs/research_governor_latest.md`. Their `decisions_due` list **is the decision
queue**: proposed status, the evidence that triggered it, the date, and the
exact registry edit a human would make.

**Step 3 — runner `governor-review` (`[CACHE]`, on demand).** Run it weekly
alongside `feedback-queue-review`. No timer, no nightly step, no digest line
until it has run clean for four weeks.

**Step 4 — tests (about 12).**
- Registry schema and the per-status evidence rules.
- Two-way quarantine consistency.
- A passed WAIT date → DECISION_DUE.
- Runner-parse fixture.
- Orphan and duplicate detection.
- Writes only its two outputs.
- **Forbidden-import test:** no `core.config`, `core.fmp_client`, network paths
  in `core.data_gatekeeper`, `core.llm_clients`, or broker/execution modules.

**Step 5 — the governor's own row.** It enters the registry as `WAIT`, with a
decision date six weekly runs out (≈2026-10-23). If by then it has not produced
at least one human-accepted SUNSET/QUARANTINE/FIX decision, or caught one drift
item a human acted on, it is itself SUNSET.

**Deferred (not v1):**
- An optional LLM narration of the governor sidecar via `complete_json` (role
  chat, forced safety fields, deterministic markdown as fallback). Consider it
  only if the deterministic report proves hard to read.
- Any LLM that *proposes* research changes (the RSI loop) stays behind the
  ROADMAP maturity gate.

**Authority boundaries to write into the module docstring:**
- read-only on every artifact, database and doc;
- never edits the registry or the runner;
- never deletes;
- never calls a provider or an LLM;
- never touches scanner logic, scoring, gates, thresholds, HC/EO/Alpha Focus
  rules or program verdicts;
- never schedules or executes anything;
- every status it outputs is a *proposal* until a human commits a registry edit.

---

## 7. What not to build

- **Do not turn `journal_audit_reviewer.py` into a system governor.** Wrong
  input, wrong cadence, and circular (Q11).
- **Do not add a governor LLM call to the nightly run.** It is already the
  largest single LLM cost and has truncated twice.
- **Do not auto-execute queue tasks or registry proposals.** This is the
  `feedback_queue.py` doctrine and the ROADMAP rule.
- **Do not add a dashboard panel or digest section for the governor in v1.**
  The drift audit calls the digest "a dumping ground", and operator guidance is
  CLI + JSON first.
- **Do not build the Alpha Research Agent** (LLM hypothesis proposer) before the
  45d/60d gate.

---

---

## Follow-up — 2026-09-11 (same day, uncommitted pending review)

D1–D4 were addressed in a separate change. No governor was built, no LLM
prompt or model was changed, and no provider call was made.

- **D1, fixed.** The P0 forward_evidence task is suppressed when the digest
  declares the tracker stats. The digest now emits that declaration only
  when `research_forward_latest.json` carries them. The P0 scanner_recall
  task treats the digest's live recall-cohorts line as its deliverable, and
  no longer fires on a LOW recall flaw. Immature forward evidence is now
  recorded as `wait_for_maturity` in the audit sidecar, never queued. The
  queue review confirms an ADDRESSED task when it stops being queued, rather
  than when its whole area goes quiet. No ledger entry was written.
- **D2, fixed.** `next_decision_date` is the first date on or after the ET
  session date. Passed dates are marked PASSED. The final 2026-09-30 date is
  labelled as the SUNSET gate. The digest refuses to present a passed date as
  current, even from an older sidecar.
- **D3, fixed.** `LLM_PROVIDER.md` and the reviewer docstring now say role
  `chat`. A test ties the doc to the code constant.
- **D4, corrected.** The digest and command center read **none** of the
  quarantined sidecars, so nothing leaked. The operator summary still loads
  `scanner_truth` but has not used it since 2026-09-09. A guard test now pins
  that the digest and command center do not read quarantined sidecars.
- **D5–D7:** not changed.

*Nothing in this review is evidence of edge, and nothing here changes a live rule.
No code, prompt, threshold, registry, queue entry, artifact or dashboard was
modified to produce it.*

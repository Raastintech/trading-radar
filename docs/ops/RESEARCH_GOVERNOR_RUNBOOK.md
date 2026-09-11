# Research Governor Runbook

*Research Governor v0. Created 2026-09-11. Review for retirement by **2026-10-23**.*

## What it is

An on-demand, deterministic checker for the whole research system. It reads
the hand-edited component registry (`data/research/component_registry.json`)
and whatever local artifacts, ledgers, logs, scheduler unit files and budget
counters exist. For every registered component it recommends exactly one
action — STOP_NOW, KEEP, FIX_NEXT, WAIT_FOR_MATURITY, DO_NOT_BUILD or
NEEDS_HUMAN_APPROVAL. It then writes one report and one JSON summary.

Its job is to catch drift early:
- API waste
- stale surfaces still shown as current
- duplicate candidate lists
- fake precision
- unvalidated alpha claims
- decisions whose date passed unrecorded

The point is to keep attention on finding, validating or rejecting candidates.

## What it is not

- **Not an LLM agent.** It uses no model and makes no LLM call.
- **Not a provider client.** It makes zero FMP / Tradier / DeepSeek / OpenAI /
  Anthropic calls and imports no provider or LLM client. It also imports no
  `core.*` module: `core.config` needs credentials, and `core.provider_budget`
  imports it lazily.
- **Not a scheduler.** It has no timer and runs only when you run it.
- **Not a trading tool or a ranker.** It names no stocks and orders nothing.
- **Not an executor.** It never changes a status, never edits the registry,
  never runs a recommended action, and never touches scanner, M1, HC / EO /
  Alpha Focus routing, dashboards, MCP, cron or systemd.

## Command

```bash
GEM_TRADER_SKIP_DOTENV=true .venv/bin/python -m research.research_governor
# optional flags
#   --registry data/research/component_registry.json
#   --output-md docs/research/RESEARCH_GOVERNOR_REPORT_latest.md
#   --output-json cache/research/research_governor_latest.json
#   --quiet
```

It runs in a few seconds.

## Output files

| File | What |
|---|---|
| `docs/research/RESEARCH_GOVERNOR_REPORT_latest.md` | Human report, 10 sections (verdict → final answer) |
| `cache/research/research_governor_latest.json` | Machine summary: verdict, components, evidence, cost risks, decisions, `provider_calls_made: 0`, `research_only: true` |

These are the only two files it writes. A `.md` output must sit under
`docs/research/` and a `.json` output under `cache/research/`; anything else
is refused with exit code 2.

**Git note.** The markdown report sits in a tracked directory, so each run
leaves it as a new or modified file. Commit it deliberately or leave it
untracked. The registry lives under `data/`, which `.gitignore` excludes, so
it needs `git add -f data/research/component_registry.json` once. After that,
git tracks every edit.

## Inputs (all read-only)

- **Registry:** components, limits, build proposals, known test debt,
  generated docs, reader surfaces, label-scan sources, scheduler and cost
  sources.
- **Cache artifacts:** `cache/research/*_latest.json`, used for freshness,
  candidate-list sizes, verdicts and precision probes.
- **Latest digest note:** the most recent Daily Research Digest in
  `data/research/journal.jsonl`.
- **Reader sources:** the digest builder, command center, TUI, operator
  summary and radar report, scanned for quarantined-artifact reads.
- **Scheduler:** the runner (`scripts/run_research_cycle.sh`) plus enabled
  timers in `/etc/systemd/system` and `~/.config/systemd/user`. The user
  crontab is not read.
- **Spend counters:** FMP budget counters in `db/trading.db`, opened
  read-only (`mode=ro`), and the LLM usage log `logs/llm_usage.jsonl`.
- **Budget facts:** read statically from the source of
  `core/provider_budget.py`, `core/data_gatekeeper.py` and
  `core/llm_clients/usage.py`, never imported.
- **Audit queue:** the feedback queue and its state ledger.
- **Git:** `git --no-optional-locks status`, used only to spot generated docs
  that dirty the working tree.

A missing input becomes a warning. A broken check becomes a `check_error`
warning. Neither crashes the run.

## Approved enums

| Field | Values |
|---|---|
| `status` | CORE, SUPPORT, RESEARCH_ONLY, QUARANTINE, SUNSET_CANDIDATE, DISABLED, DECOMMISSIONED |
| `evidence_level` | VALIDATED_EDGE, PROVISIONAL_POOL_LEVEL_ONLY, FORWARD_IMMATURE, REPLAY_CONTRADICTED, FAILED_GATES, NOT_ENOUGH_EVIDENCE, OPERATIONAL_CONTROL_ONLY |
| `provider_call_risk` | NONE, LOW, MEDIUM, HIGH, UNKNOWN |
| decision `action` | STOP_NOW, KEEP, FIX_NEXT, WAIT_FOR_MATURITY, DO_NOT_BUILD, NEEDS_HUMAN_APPROVAL |
| executive verdict | HEALTHY, CAUTION, DRIFTING, BLOCKED, RESEARCH_ONLY_NO_EDGE |
| build proposal `decision` | DO_NOT_BUILD, APPROVED |

Optional registry fields the checks use:
- `kind`, `cadence`, `artifacts`, `runner_refs`, `reader_refs`
- `candidate_lists`, `evidence_probe`, `precision_probes`,
  `decision_date_probes`, `line_count_probe`
- `display_aliases`, `hard_guard`, `provider_calls_per_run_max`
- `forward_hypothesis`, `purpose`, `consumers`, `evidence_path_label`,
  `evidence_source`, `research_label`

The approved `cadence` and `kind` values are listed at the top of
`research/research_governor.py`.

## Registry rules

A registry that breaks any of these rules makes the verdict **BLOCKED**:
- Every component carries all required fields, uses approved enum values, and
  has `requires_human_approval_to_change: true`.
- A QUARANTINE, DECOMMISSIONED or DISABLED component cannot be allowed to
  surface candidates, appear in the daily digest, or appear on a dashboard.
- A REPLAY_CONTRADICTED or FAILED_GATES component cannot surface candidates.
- VALIDATED_EDGE requires a `validated_edge_approval` block (`approved_by`,
  `approved_on`, `evidence_ref`) plus supporting notes.
- Registry text may not use trade language: buy, sell, hold, top-pick,
  price-target.

## How to read the actions

| Action | Meaning | Who acts |
|---|---|---|
| **STOP_NOW** | A quarantined candidate surface is still shown as live, or a system whose replay contradicted it (or that failed gates) still influences daily decisions. Stop showing or using it. | Human, today |
| **FIX_NEXT** | A concrete defect: a stale surface shown as current, a daily list over 15 names, fake precision, a quarantined job still scheduled, an untriaged queue, HIGH provider risk with no hard guard, a manual run able to spend more than `large_run_calls` (2,000) calls with no planned-call gate, a registry rule broken. | Human, next work session |
| **NEEDS_HUMAN_APPROVAL** | A decision only a human can make: a decision date has passed, the registry is more optimistic than its artifact, a surface has no forward hypothesis, a candidate surface is unregistered, or there are too many surfaces. | Human decides, then edits the registry |
| **WAIT_FOR_MATURITY** | Forward evidence is immature and its maturity date is still ahead. Do not re-run, re-date or re-read it early. | Nobody — wait |
| **DO_NOT_BUILD** | A proposed new radar / lens / overlay / ranker / nightly step / dashboard panel. It is refused unless an APPROVED build proposal exists. | Nobody — do not build |
| **KEEP** | Registered, current, no drift finding. | Nobody |

When a component triggers several findings, the most urgent action wins. The
order is STOP_NOW > FIX_NEXT > NEEDS_HUMAN_APPROVAL > WAIT_FOR_MATURITY >
DO_NOT_BUILD > KEEP. The rest are listed under `other_findings`.

## Verdict ladder

1. **BLOCKED** — the registry is unreadable or breaks a rule.
2. **DRIFTING** — any of:
   - any STOP_NOW item
   - more active candidate surfaces than `max_active_candidate_surfaces`
   - at least `drifting_warning_threshold` drift warnings
3. **CAUTION** — any of:
   - an untriaged queue
   - a stale surface still shown as current
   - uncontrolled provider risk
   - a decision date passed with no decision recorded
   - any FIX_NEXT item
4. **RESEARCH_ONLY_NO_EDGE** — none of the above, and no component carries a
   registry-approved VALIDATED_EDGE. This is not a clean bill of health.
5. **HEALTHY** — none of the above, and at least one validated edge.

## What requires human approval

Every change the governor recommends. That covers any status, evidence level
or flag in the registry, any scheduled job, any scanner / M1 / HC / EO /
Alpha Focus / dashboard / MCP / timer change, and any closure of a queue task.
The governor only writes its own two output files.

## What it can never do

- Call a provider or an LLM, or import a provider / LLM client.
- Change a component status or edit the registry.
- Execute, schedule or queue an action.
- Change scanner logic, M1 membership, the frozen M1 cohort, HC / EO / Alpha
  Focus routing, dashboards, MCP or timers.
- Create a signal, rank a stock, or name a stock.
- Write outside `docs/research/` (report) and `cache/research/` (JSON).

## Editing the registry

Edit `data/research/component_registry.json` by hand, then rerun the governor.
A registry error shows up immediately as BLOCKED with the broken rule. Record
why you changed a status in the component's `notes`, with the date.

## Expiration policy

The governor's own registry entry carries `expiration_date: 2026-10-23`. On
that date, review whether any governor finding drove a human decision. That
means a status changed, a job stopped, a list was capped, or a build was
refused. If none did, retire the governor. After the date passes, the
governor flags itself NEEDS_HUMAN_APPROVAL on every run until someone decides.

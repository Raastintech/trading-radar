# LLM Provider — DeepSeek default (2026-07-17 migration)

DeepSeek replaced Anthropic as the **default** LLM provider for every
runtime LLM service in the research engine. This was a provider
migration only — no scanner logic, candidate selection, scores,
rankings, gates, factor weights, forward tracking, program verdicts, or
artifact schemas changed.

DeepSeek is the default **lower-cost** LLM provider, subject to provider
rate limits, account capacity, API availability, and the configured
budget controls below — never treat it as unmetered.

## Abstraction

`core/llm_clients/` (production-importable — the hard separation rule
forbids `core/` and `dashboards/` importing from `research/`, which is
why the package lives here rather than `research/llm_clients/`):

| File | Purpose |
|------|---------|
| `core/llm_clients/base.py` | `LLMClient` contract, `LLMResponse`, `LLMError`, role constants, forced safety fields, `resolve_secret` (trading.env-over-shell key resolution) |
| `core/llm_clients/deepseek_client.py` | DeepSeek client (OpenAI-compatible `/chat/completions`, lazy `requests` import, output-token guardrail `PROVIDER_MAX_OUTPUT_TOKENS=65536` — an in-repo cap, not the provider's actual ceiling; DeepSeek's docs report up to 384K output tokens for `deepseek-v4-pro`/`deepseek-v4-flash`) |
| `core/llm_clients/anthropic_client.py` | Anthropic fallback — used **only** when explicitly re-enabled |
| `core/llm_clients/provider.py` | `get_llm_client()` factory + `get_llm_status()` key-free snapshot |

All modules are cred-free at import time (no `core.config` dependency).
`get_llm_client()` returns `None` when the active provider is disabled
or has no key — every caller treats `None` as "run the deterministic
path". **The nightly cycle never blocks on an LLM failure.**

## Configuration (env, resolved lazily; trading.env wins over shell)

```
LLM_PROVIDER=deepseek            # default; "anthropic" only for fallback
DEEPSEEK_ENABLED=true
DEEPSEEK_API_KEY=...             # add to /home/gem/secure/trading.env
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_CHAT_MODEL=deepseek-v4-flash    # role "chat"
DEEPSEEK_REASONER_MODEL=deepseek-v4-pro  # role "reasoner"
DEEPSEEK_TIMEOUT_SECONDS=60
DEEPSEEK_MAX_TOKENS=4000
DEEPSEEK_TEMPERATURE=0.1         # chat-role default when caller passes none
```

**Model-name resolution chain** (no model names hard-coded outside
`deepseek_client.py`, so a provider-side rename is an env edit, not a
code edit):

- chat: `DEEPSEEK_CHAT_MODEL` → `DEEPSEEK_MODEL` (legacy) → provider
  default (`deepseek-chat` compatibility alias, one constant in
  `deepseek_client.py`)
- reasoner: `DEEPSEEK_REASONER_MODEL` → chat chain → provider default
  (`deepseek-reasoner` compatibility alias)

The API currently reports the model serving both aliases as
`deepseek-v4-flash`; pin `DEEPSEEK_CHAT_MODEL` / `DEEPSEEK_REASONER_MODEL`
explicitly if the compatibility aliases are retired.

## Budget + usage controls (`core/llm_clients/usage.py`)

```
LLM_DAILY_MAX_CALLS=200          # provider calls per UTC day; <=0 disables
LLM_DAILY_MAX_TOKENS=400000      # prompt+completion tokens per UTC day; <=0 disables
LLM_FAIL_OPEN=true               # default: LLM failure degrades to the
                                 # deterministic path; false = propagate (debug only)
LLM_USAGE_LOG_PATH=...           # optional override; default logs/llm_usage.jsonl
LLM_MONTHLY_BUDGET_USD=...       # reserved, NOT enforced — no per-token cost
                                 # model exists in this repo yet
```

Every provider call is checked against the daily caps first (a blocked
call raises `LLMError` with the cap reason → the caller's deterministic
fallback runs) and appends one entry to the usage ledger
`logs/llm_usage.jsonl`: `ts, provider, model, status (ok|error|
budget_blocked), prompt_tokens, completion_tokens, latency_ms, artifact
(caller label), error_class`. **Never logged:** API keys, auth headers,
raw prompts, raw replies. `get_llm_status()` exposes a key-free
`usage_today` snapshot (calls/tokens vs caps).

The dashboard's own `LLM_DAILY_BUDGET` (default 20, legacy alias
`CLAUDE_DAILY_BUDGET`) is a separate per-dashboard analyzer cap layered
on top of the global daily caps.

Anthropic is **not** used unless BOTH `LLM_PROVIDER=anthropic` and
`ANTHROPIC_ENABLED=true` are set. `ANTHROPIC_API_KEY` is no longer
required for normal operation; a lingering key alone never re-activates
Anthropic.

Per-service model overrides (optional; empty = provider role default):

- `JOURNAL_AUDIT_LLM_MODEL` — journal audit (role: chat)
- `SOCIAL_ARB_LLM_MODEL` — social-arb review (role: chat)
- `LLM_DAILY_BUDGET` — dashboard analyzer call cap (legacy alias `CLAUDE_DAILY_BUDGET`)

## Model routing

| Role | Used for |
|------|----------|
| `chat` | summaries, candidate memos, noise-filter reviews, dashboard analysis, gatekeeper prose, journal digest audit (since 2026-08-25) |
| `reasoner` | reasoning-heavy critique — **no runtime caller since 2026-08-25** (see below) |

**Journal audit role change (2026-08-25, commit 8352a7e).** The journal
digest audit moved from `reasoner` to `chat`. On the reasoner role the
model spent its whole output budget on hidden chain-of-thought, so replies
hit the completion cap and the audit fell back. The call now resolves
through the chat chain (`DEEPSEEK_CHAT_MODEL` → `DEEPSEEK_MODEL` →
provider default). The usage ledger records `deepseek-v4-pro` for
journal-audit calls through 2026-08-25 00:44 UTC and `deepseek-v4-flash` /
`deepseek-flash` since. The role is the constant `LLM_ROLE` in
`research/journal_audit_reviewer.py`, and
`tests/unit/test_journal_audit_reviewer.py` fails if this document names a
different role for the journal audit.

Callers request a **role**, never a hard-coded model name; the role→model
mapping comes from the env chain above.

## Structured output: `complete_json`

For JSON-shaped output, callers use the provider-neutral interface on
`LLMClient`:

```python
client.complete_json(
    system_prompt=..., user_prompt=...,
    schema_name="social_arb_reviews", max_tokens=9000,
    role="chat", temperature=0, artifact="cache/research/social_arb_latest.json")
```

It parses the reply (tolerating ``` fences / stray prose), **forces the
research-only safety fields** onto the object, and attaches a secret-free
`_llm` metadata block (provider, model, tokens, latency). On truncation
or invalid JSON it raises `LLMError` carrying `schema_name` and a safely
truncated `raw_excerpt` (≤2000 chars) so the caller can persist an ERROR
artifact. The social-arb radar uses it; the journal audit keeps its own
specialized parse path (raw-reply dump to
`logs/journal_audit_llm_raw_error.txt`).

## Runtime LLM service inventory (post-migration)

| Call site | Function | Purpose | Input | Output artifact | Nightly? | Dashboard? | Mutates? |
|-----------|----------|---------|-------|-----------------|----------|------------|----------|
| `research/journal_audit_reviewer.py` | `_llm_audit` (role: chat) | audit the daily digest, flag flaws/contradictions/missing evidence | digest text + deterministic signals | `cache/research/journal_audit_latest.json`, feedback queue, history JSONL | yes (nightly tail) | no (queue is CLI-reviewed) | **no** — sanitizer re-enforces invariants; deterministic fallback on any failure |
| `research/social_arb_radar.py` | `llm_review` (role: chat) | conservative KEEP/DROP/NOISE noise filter over deterministically scored candidates | candidate JSON + regime context | `cache/research/social_arb_latest.json` (+ text render) | yes (social nightly cron) | read-only sidecar | **no** — reviews only annotate/drop; scores untouched; empty review dict on failure |
| `core/executive_gatekeeper.py` | `_try_llm_summary` (role: chat) | plain-English restatement of the deterministic gatekeeper verdict | finalized `GatekeeperResult` | display annotation inside gatekeeper artifact | yes (gatekeeper-refresh) | cached panel | **no** — verdict is final before the call; `None` on any failure |
| `dashboards/gem_trader_hq.py` | `LLMAnalyzer.analyze` (role: chat) | per-ticker research framing panel (Mode 2) | cached bars + indicators + calendar | in-memory panel cache only | no | yes (operator-triggered) | **no** — display only; stub card on failure |

## Safety contract

Every JSON artifact embedding LLM output carries forced fields
(published invariants, overwriting anything the model claims):

```json
{"research_only": true, "promote_to_signal": false,
 "may_change_scores": false, "may_change_rankings": false,
 "may_change_gates": false, "may_change_verdicts": false}
```

The LLM may summarize, audit, flag contradictions, identify missing
evidence, write memos, and explain dashboard states. It may not promote
candidates, recommend trades, change scores/rankings/gates/verdicts,
mutate scanner artifacts, call market-data providers, or block the
nightly pipeline on failure (invalid JSON → ERROR artifact with the raw
reply preserved at `logs/journal_audit_llm_raw_error.txt`; timeout/API
error → deterministic fallback).

## Back-compat notes

- The social-arb sidecar keeps its legacy `anthropic` /
  `anthropic_contract` / `anthropic_verdict` key names (readers pin
  them); a `provider` field inside records the real provider.
- `JOURNAL_AUDIT_ANTHROPIC_MODEL` and `SOCIAL_ARB_ANTHROPIC_MODEL` are
  honored only when the Anthropic fallback is active.
- Secrets live only in `/home/gem/secure/trading.env` (git-ignored via
  `.env`, `*.env`, `trading.env`, `secure/` rules). Keys are never
  logged, never embedded in error strings or artifacts.

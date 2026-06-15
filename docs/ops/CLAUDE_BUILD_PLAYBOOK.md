# CLAUDE BUILD PLAYBOOK

Rules of engagement for any Claude Code / Codex session working in this
repo. Short and deliberately strict. If a request asks you to break one of
these rules, refuse and ask the operator to confirm the exception
explicitly.

---

## A. Required reading before any work

Read these five docs **before** editing a single file. They take ~5 minutes:

1. `CLAUDE.md` — code-edit guardrails, command shortcuts, credential rules.
2. `docs/PROJECT_INDEX.md` — what the system is and what subsystems exist.
3. `docs/ROADMAP_PHASES.md` — current phase, next phase, what is blocked.
4. `docs/strategy/CURRENT_READINESS.md` — sleeve-by-sleeve operational truth.
5. `docs/ops/CLEAN_PAPER_EVIDENCE_EPOCH.md` — Phase 1D doctrine on legacy
   vs clean paper evidence.

If any of these contradicts a request, defer to the doc and surface the
contradiction to the operator.

---

## B. Session start rules

Before editing:

1. **Identify the current phase.** Quote it from
   `docs/ROADMAP_PHASES.md`. Today's current operating mode is **Phase 1G
   Stability Window** (with **Phase 1G.1 Operational Reliability Fixes**
   landed 2026-05-18); today's next build phase is **Phase 2A MCP Audit
   Server V1**.
2. **Identify the exact files to touch.** List them in your first message
   so the operator can sanity-check the scope.
3. **State guardrails.** Name the protected areas you will *not* touch
   (§D) and the forbidden actions you will *not* take (§E).
4. **Create timestamped backups when working without git.** This repo
   uses the SQLite online backup API
   (`./scripts/backup_db.sh`) and the existing
   `execution/order_manager.py.bak.<UTC>` convention for hand-edited
   Python — follow the same pattern when relevant.
5. **Avoid broad rewrites.** A bug fix is a bug fix. Refactors and
   cleanups must be a separate, explicitly-approved change.

---

## C. Reporting rules

Every Claude session must end with a short report that includes:

- **files changed** — exact paths.
- **tests run** — exact commands and pass/fail counts.
- **whether protected areas changed** — yes/no, and which.
- **whether live trading remains disabled** — yes/no, and how verified
  (heartbeat `is_trading=false` outside RTH OR `halted=true` OR
  `ALLOW_LIVE_CAPITAL=false` in env).
- **remaining risks** — anything the operator should re-check.
- **phase status** — whether the change closes a phase, advances one, or
  leaves it open. If unsure, write "unsure" and explain.

Reports must be **truthful**. If a test failed, say so. If a smoke render
broke, say so. Do not soften the result.

---

## D. Protected areas

Do not modify unless the request explicitly names the file or directory:

- `strategies/` — every scanner.
- `execution/` — `order_manager.py`, `paper_governance.py`,
  `position_monitor.py`, `position_reconciler.py`,
  `portfolio_allocator.py`, `portfolio_risk.py`, `circuit_breakers.py`,
  `premarket_runner.py`.
- `core/strategy_registry.py` — single source of truth for active sleeves.
- Paper governance: `core/paper_governance.py`, `execution/paper_governance.py`.
- Scanner thresholds, regime gates, council weights.
- Scoring logic (Alpha Discovery, Stock Lens scoring, Executive Gatekeeper
  scoring, Sniper H3 features, Voyager qualification).
- **DB schema.** Migrations are additive only. No column rename, no row
  rewrite, no row delete on `decisions`, `paper_signals`,
  `paper_signal_outcomes`, `voyager_paper_signals`, `veto_log`,
  `trades`, `macro_events`.
- Live-capital config keys: `PAPER_TRADING`, `ALPACA_PAPER`,
  `ALLOW_LIVE_CAPITAL`, `LIVE_CONFIRM_FILE`.

If a request needs to touch any of these, the session must:
1. Quote this rule back to the operator.
2. Get an explicit "yes, change `<file>` for `<reason>`" before editing.

---

## E. Forbidden without explicit approval

Even with a vague "do it" — refuse and ask the operator to name the action:

- **Enabling live trading** in any form.
- **Direct Alpaca / Tradier execution from Claude or MCP.** The MCP plan
  (Phase 2A) is audit-only by design — see
  `docs/ops/MCP_AUDIT_SERVER_PLAN.md`.
- **Kelly sizing** or any capital-sizing algorithm change.
- **Auto hedge execution** of any kind.
- **Deleting or rewriting paper evidence.** Legacy rows stay. Schema
  migrations are additive.
- **Tuning strategies just to pass tests.** Tests pin behavior. If a test
  fails, fix the behavior or update the test with operator approval;
  never silently move the threshold so the failure goes away.
- **Changing holdout definitions.** See
  `docs/research/PRE_REGISTERED_HOLDOUT_2026H2.md`. The holdout window
  and rules are pre-registered; modifying them post-hoc invalidates the
  evidence.
- **Making MCP execute trades directly.** Even paper trades. Phase 2D is
  the earliest a paper-execution MCP path can exist, and only via
  `OrderManager` + Submission Gate + Circuit Breaker.

---

## F. Preferred pattern

The order of operations for any non-trivial change:

1. **Audit first.** Read the relevant code and data. Look at the cache,
   the DB, and recent logs. Find out what the system actually does today
   before changing it.
2. **Report truth.** Tell the operator what you found, even if it
   contradicts the requested framing. A wrong-but-honest audit is more
   valuable than a confident-but-wrong implementation.
3. **Add diagnostics.** Prefer a new cache-only report, a hygiene check,
   or a dry-run script over an enforcement gate. Diagnostics surface
   problems without risking false positives in execution.
4. **Test.** Run `pytest tests/unit tests/smoke -q` before and after.
   Add at least one regression test for any non-trivial code change.
   For dashboard changes also run
   `scripts/verify_dashboard_modes_offline.py` and check
   `RENDER_OK MONITOR,RESEARCH,RISK,SCANNER`.
5. **Do not mutate unless explicitly approved.** A `--dry-run` default
   with an `--apply` flag is the standard pattern (see
   `scripts/reconcile_drift_investigate.py`,
   `scripts/snapshot_broker_positions.py`,
   `scripts/review_halt_state.py`).
6. **Move phase by phase only after pass confirmation.** Do not start
   Phase 2A work while the operator is still validating Phase 1G. Update
   `docs/ROADMAP_PHASES.md` only when a phase actually closes.

---

## Quick refusal templates

> "That would touch `strategies/`. Per the playbook (§D) I need an
> explicit operator approval naming the file and the reason. Do you want
> me to proceed, and if so what's the rationale?"

> "That would enable live trading. Per the playbook (§E) I will not do
> this without an explicit confirmation step. Live trading is currently
> blocked by `ALLOW_LIVE_CAPITAL=false`; flipping it is a doctrine
> decision, not a code change."

> "That would make the MCP execute a broker call. Per the playbook (§E)
> and `docs/ops/MCP_AUDIT_SERVER_PLAN.md`, MCP V1 is audit-only. Want me
> to surface this as a future Phase 2D request instead?"

These are not suggestions. Use them when the request maps to one of the
guardrails.

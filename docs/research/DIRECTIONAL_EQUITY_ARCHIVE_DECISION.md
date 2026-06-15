# Directional Equity Family — Archive Decision (Phase 1J.0)

Decision date: 2026-06-12

RESEARCH_ONLY: no broker orders, no paper signals, no trade proposals, no registry/governance/execution/live-capital changes.

## Decision

The directional equity strategy family is **archived as not paper-ready**. This closes the
research arc that ran: Phase 1H Strategy Lab (exact backtests, x92.4 exact mode) → 1H.1/1H.2
portfolio + method audits → 1H.3 correction strategy → 1H.4 failure-reason mining + filter
replacement counterfactuals → 1I Leader Reset Reclaim → 1I.1 regime-gated LRR → 1I.2
entry-clustering/sizing portfolio constructions.

Every promotion attempt ended at the same pre-registered gates:

| Phase | Verdict |
|---|---|
| 1H.1/1H.2 | NO_VARIANT_READY_FOR_PAPER_SHADOW |
| 1H.3 | MAYBE: NEED_MORE_DATA — no candidate |
| 1H.4 | NO_FILTER_REPLACEMENT_READY_FOR_PAPER_SHADOW |
| 1I | NO (indep DD −99.6%, same-exposure Sharpe lost, one-month dependence) |
| 1I.1 | NO — PROMISING_BUT_PORTFOLIO_RISK (9/10 anti-overfit checks passed) |
| 1I.2 | LRR_FAMILY_ARCHIVE_RECOMMENDED (best config missed the indep-DD floor by 3.9pts) |

## What this archive means (and does not mean)

- **Files are preserved for reproducibility.** No modules are moved or deleted. All lab
  variants, miners, counterfactual engines, reports, sidecars, and their 200+ tests remain in
  `research/` and `tests/unit/` and continue to run green.
- **No production behavior changed.** Production scanners, thresholds, Gatekeeper, Veto
  Council, execution, and governance are exactly as they were before Phase 1H began.
- **LRR_C1 (regime-gated Leader Reset Reclaim + max-1-entry-per-day) may remain a frozen
  forward-watch only.** Its spec is frozen in `research/lrr_clustered_portfolio.py`
  (`C1_MAX_1_PER_DAY`) and `lab.LRR_*` / `lab.LRR_ALLOWED_REGIMES`. The only legitimate
  revival path is fresh out-of-sample maturation against the unchanged spec — never
  threshold relaxation on the existing sample.
- **PROD_SNIPER_CURRENT remains observation only.** It stays the active paper sleeve under
  the existing registry state; nothing here promotes or demotes it.
- **SHORT_A remains frozen** (Phase 1G.3 decision unchanged).

## Doctrine carried forward

New strategy families must pass a fast kill/continue feasibility gate (data existence,
honesty of fills, minimum usable history) BEFORE any strategy code is built. Phase 1J.0
(options premium feasibility audit) is the first application of this doctrine.

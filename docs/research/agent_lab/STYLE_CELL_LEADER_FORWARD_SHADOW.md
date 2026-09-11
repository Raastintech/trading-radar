# `style_cell_leader_pool` — forward shadow ledger

*Generated 2026-09-11T17:27:05Z. Zero provider calls.*

> **FORWARD_SHADOW_RESEARCH_ONLY.** This page records what a basket would
> have contained on past sessions and what happened next. It is **not** live
> forward evidence for any production surface, **not** Phase 4B input, **not**
> a gate/threshold/score/routing change, and **not** a trade signal. The rows
> must never be pooled with the live forward ledger, program verdicts, M1,
> HC/EO/Alpha Focus routing, or the dashboard. Nothing here is an individual
> stock selection: the tracked object is an **unordered, equal-weight basket**,
> and no per-name score is stored, so it cannot be ordered after the fact.

---

## What is tracked

Universe floored at price >= $5 and median 20-day dollar volume >=
$20M, split into volatility-decile x liquidity-decile cells
computed within the session. Each cell contributes its single highest 12-1
momentum name. Horizons 20d, 45d, 60d; primary **60d**.

The replay study labelled this `PROVISIONAL_RESEARCH_CANDIDATE` and said why
it could not be called validated: the object was specified after the 2025
holdout had been read. Only unseen data can advance it. This ledger records
that data and deliberately does not judge it.

## Session status

| | |
|---|---|
| price source | `live_plus_replay_cache` |
| latest session | 2026-09-10 |
| status | **OK** |
| universe size | 1855 |
| pool size | 100 |
| liquid candidates | 2224 |
| fresh share | 84.98% (floor 80%) |
| depth coverage of fresh | 98.15% (floor 95%) |
| sessions in ledger | 1 (1 with a pool, 0 refused) |
| **accepted forward sessions** | **1** |
| retracted rows | 3 |
| unresolved at 60d | 1 |

### Cache merge

Where a ticker exists in more than one cache the series are merged, with the maintained cache winning on any shared date. Preferring the *deepest* file instead would silently drop the newest bar, which is the entry price this ledger records.

* merged from more than one cache: **5,023**
* only one cache held it: **1,808**
* **307** refused a splice — the caches disagreed by more than 2% on overlapping dates, which is an unadjusted corporate action in one of them. Those names fall back to a single cache and drop out of the universe if that leaves them short of 252 bars, rather than being spliced into a price gap that never happened.

## Forward outcomes

No horizon has matured yet. Nothing to report.

## Maturity

Pre-declared floor, frozen before the first observation: **30 matured 60d sessions** and **4 non-overlapping windows**.

Progress: 0 matured, 0 non-overlapping. Floor reached: **no**.

Reaching the floor does not produce a verdict. The published label stays
`FORWARD_SHADOW_RESEARCH_ONLY` either way; promotion is a separate human
decision taken outside this module.

## What this does not support

* reading any member as an individual stock selection
* ordering, ranking, shortlisting or scoring the pool
* any production scanner, M1, HC/EO, Alpha Focus, dashboard or routing change
* pooling these rows with the live forward ledger or program verdicts

---

*Ledger: `data/research/agent_lab/style_cell_leader_forward_ledger.jsonl` (append-only JSONL). Artifact:*
*`cache/research/agent_lab/style_cell_leader_forward_latest.json`.*
*Re-run with* `GEM_TRADER_SKIP_DOTENV=true .venv/bin/python -m research.agent_lab.forward_shadow run`.
*Not scheduled: there is no timer, cron entry, or unit for this module.*

# `style_cell_leader_pool` — forward shadow ledger

*Generated 2026-09-10T19:00:17Z. Zero provider calls.*

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

## No accepted forward sessions yet

**There are zero accepted OK forward observations in this ledger.** The
evidence record has not started.

2026-09-09 was worked as a **debug and validation day**, not as the first
observation. It was used to exercise the cache-merge rules, the coverage
gates, and the append-only invalidation path, and every pool it produced has
been retracted — the last of them discarded by operator decision before any
horizon matured, so nothing was thrown away that had become evidence.

The first clean accepted session is **pending**. Until one is recorded, every
forward statistic on this page is empty by construction rather than by
coincidence.

### Retracted rows, in order

| row | original status | pool | reason |
|---|---|--:|---|
| `POOL_SNAPSHOT\|live_plus_replay_cache` | OK | 100 | cache merge spliced a pre-reverse-split segment from cache/prices_deep onto the post-split series, manufacturing a +1726% session for WOLF and contaminating 12-1 momentum for the affected names; the overlap-only splice check could not see the segment contributed outside the overlap. Superseded by a revision recorded after merged-series integrity validation and a corrected cache priority order. |
| `POOL_SNAPSHOT\|live_plus_replay_cache\|r1` | REFUSED_INSUFFICIENT_LIVE_CACHE_COVERAGE | 0 | recorded while the merge fallback preferred the highest-priority cache over the freshest clean one, which cost 2,017 symbols their most recent bar and pushed freshness below the gate; superseded by revision 2. |
| `POOL_SNAPSHOT\|live_plus_replay_cache\|r2` | OK | 100 | operator_discarded_debug_run_before_first_evidence_maturity |

Nothing above was deleted. Each row is still readable in the ledger with
its original status, and each retraction carries its own reason. Published
statistics skip them.

## Session status

| | |
|---|---|
| price source | `live_cache` |
| latest session | 2026-09-09 |
| status | **REFUSED_INSUFFICIENT_LIVE_CACHE_COVERAGE** |
| universe size | 0 |
| pool size | 0 |
| liquid candidates | 1965 |
| fresh share | 48.35% (floor 80%) |
| depth coverage of fresh | 99.68% (floor 95%) |
| sessions in ledger | 1 (0 with a pool, 1 refused) |
| **accepted forward sessions** | **0** |
| retracted rows | 3 |
| unresolved at 60d | 0 |

### Cache merge

Where a ticker exists in more than one cache the series are merged, with the maintained cache winning on any shared date. Preferring the *deepest* file instead would silently drop the newest bar, which is the entry price this ledger records.

* merged from more than one cache: **404**
* only one cache held it: **5,479**
* **7** refused a splice — the caches disagreed by more than 2% on overlapping dates, which is an unadjusted corporate action in one of them. Those names fall back to a single cache and drop out of the universe if that leaves them short of 252 bars, rather than being spliced into a price gap that never happened.

### Why this session was refused

**REFUSED_INSUFFICIENT_LIVE_CACHE_COVERAGE** — only 48.3% of 1965 liquid candidates have a bar within 5 days of 2026-09-09 (floor 80%) — the session would be computed on a fraction of the universe

A pool computed on a thin or stale cache is a look-alike, and a forward
ledger of a look-alike is worse than no ledger. The refusal is recorded
in the ledger so a gap can never be mistaken for a session that was
simply not run.

**What would unblock it.** The gate that fails is freshness, not depth —
99.68% of the names that *are* current carry the
252 bars the method needs. The live cache simply is not being kept
current across this universe. Two options, neither taken here:

1. A maintained daily refresh of the liquid universe. Size it with
   `forward_shadow.py plan-refresh`; this module refuses above
   100 planned calls and never fetches itself — it hands off to
   the existing `research/refresh_universe_prices.py`.
2. Run against `--source live_plus_replay_cache`, which clears both gates
   today. That cache is maintained by a research backfill script rather
   than a timer, so a ledger built on it inherits whatever staleness the
   script has drifted into. It is a way to start measuring, not a fix.

## Forward outcomes

No accepted session exists yet, so no horizon can mature. Nothing to report.

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

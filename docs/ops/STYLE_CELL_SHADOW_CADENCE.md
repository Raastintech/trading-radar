# Style-cell leader forward shadow — weekly cadence

Research-only. This ledger records what an unordered `style_cell_leader_pool`
basket would have held on past sessions and what happened next. It emits no
ranking, no signal and no trade language, its published label is pinned at
`FORWARD_SHADOW_RESEARCH_ONLY`, and reaching its maturity floor grants no
verdict — promotion is a separate human decision taken outside the module.

## What this cadence does and does not do

* The weekly timer makes **zero provider calls** and does **not** refresh prices.
* It never passes `--allow-backdate`.
* If the freshness or depth gates fail, it records a **refusal** row.
* It records an accepted snapshot only when the cache is **already** fresh
  enough.
* A broad price-cache refresh remains a **separate human budget decision**.
* The first run (2026-09-23) refused at **42.4% fresh against an 80% floor**, so
  today this is a monitoring cadence: it records the stall every week, but it is
  not yet accumulating new accepted snapshots.

## Why there is a cadence

The lane was registered with `cadence: manual` and no runner reference. It
accrued one accepted session (2026-09-10) and then nothing for two weeks,
because a manual cadence with no owner is not a cadence. Its floor needs **30
matured 60-session snapshots across 4 non-overlapping windows**, so a lane that
does not take snapshots cannot reach it. Operator decision 2026-09-23 gave it a
weekly, zero-provider cadence.

## The scheduled command

```bash
./scripts/style_cell_shadow_weekly.sh
```

which runs exactly:

```bash
GEM_TRADER_SKIP_DOTENV=true .venv/bin/python -m research.agent_lab.forward_shadow \
  run --source live_plus_replay_cache
```

Nothing else is passed, and nothing else should be added to the wrapper:
`--allow-backdate` would write a stale session into forward evidence instead of
leaving a visible gap, and `--max-calls` belongs to the `plan-refresh`
subcommand this path never invokes.

## Why a timer is safe here

`gem-trader-style-cell-shadow.timer` is allowed to run unattended because the
job cannot spend a provider call:

* `GEM_TRADER_SKIP_DOTENV=true` — no credential is loaded, in the wrapper and
  again in the unit's `Environment=`;
* the unit sets no `SNIPER_ENV_PATH`, unlike the M1 cohort unit, so there is no
  credential path to pick up;
* `research/agent_lab/forward_shadow.py` imports only stdlib, numpy, pandas and
  `research.backtests.common` (itself stdlib-only) — there is no provider
  client anywhere in the import graph;
* the module calls `offline_env()` before it parses argv;
* `run` reads cached parquet and appends to the ledger. The `plan-refresh`
  subcommand only *names* the symbols a refresh would need and hands off to
  `research/refresh_universe_prices.py`; it never fetches, and the wrapper
  never calls it;
* `tests/unit/test_agent_lab_forward_shadow.py` runs a session with
  `socket.socket` removed and asserts it completes.

Verified 2026-09-23: the wrapper ran end to end with the FMP counter unchanged
at 4,321 calls for the day.

## Timer

| | |
|---|---|
| Unit | `gem-trader-style-cell-shadow.timer` (**user** timer, not system) |
| Schedule | `Sat 10:00 America/New_York` |
| Service | `gem-trader-style-cell-shadow.service` |
| Repo copies | `scripts/systemd/gem-trader-style-cell-shadow.{timer,service}` |
| Installed at | `~/.config/systemd/user/` |
| Log | `logs/gem-trader-style-cell-shadow.log` |

`Persistent=true`: a missed week catches up once. That is safe because a
snapshot is keyed by session date and `append_ledger` skips a duplicate key, so
a late run either records the session it missed or does nothing.

### Check status

```bash
systemctl --user list-timers gem-trader-style-cell-shadow.timer
systemctl --user status gem-trader-style-cell-shadow.service
tail -40 logs/gem-trader-style-cell-shadow.log
```

### Disable

```bash
systemctl --user disable --now gem-trader-style-cell-shadow.timer
```

Re-enable with `enable --now`. Disabling stops collection; it does not change
the ledger, and the governor will start reporting the artifact as stale once it
passes the 216-hour weekly threshold — which is the intended signal, not a
fault.

## Files to inspect

| Path | What it holds |
|---|---|
| `data/research/agent_lab/style_cell_leader_forward_ledger.jsonl` | the append-only ledger: one row per session, plus any invalidation rows |
| `cache/research/agent_lab/style_cell_leader_forward_latest.json` | the summary the governor reads (`verdict`, `latest_status`, `maturity`) |
| `docs/research/agent_lab/STYLE_CELL_LEADER_FORWARD_SHADOW.md` | the rendered report; generated and gitignored |
| `logs/gem-trader-style-cell-shadow.log` | timer output, including refusal reasons |

The ledger is append-only and is never rewritten. A superseded snapshot is
retracted by an explicit invalidation row, not by editing history — three rows
from 2026-09-09 are retracted that way and are excluded from every published
statistic.

## What a refusal means

A refusal is a **correct outcome, not a failure**. The module refuses to compute
a session on a fraction of the universe, records a `REFUSED_*` row so the gap is
explicit, and stops. The unit still exits 0, so a genuine breakage stays
distinguishable from an expected stale-cache week.

The gates: at least **80%** of liquid candidates must have a bar within **5
days** of the session, and at least **95%** of those must carry **252 bars**.

**Known open issue (2026-09-23).** The first scheduled run refused:

```
only 42.4% of 2234 liquid candidates have a bar within 5 days of 2026-09-22
(floor 80%) — the session would be computed on a fraction of the universe
```

The 2026-09-10 session passed at 84.98% because a broad price refresh had run
days earlier as part of the September M1 work. Only about 1,000 universe names
are refreshed by the daily cycle, so the merged `live_plus_replay_cache` decays
below the 80% floor within a couple of weeks. **Until a broad price-cache
refresh runs, this lane will record weekly refusals rather than snapshots, and
its floor will not advance.**

That refresh costs provider calls (`research/refresh_universe_prices.py`, up to
1,100 per run) and is deliberately out of scope for a cache-only cadence. Making
the lane actually accrue is a separate provider-budget decision. The weekly
refusal row is the honest interim state: the stall is now recorded every week
instead of being invisible.

# Clean-Paper-Evidence Epoch (Phase 1D)

**Source of truth:** `core/paper_evidence_epoch.py` — constant
`CLEAN_PAPER_EVIDENCE_START`.

**Current value:** `2026-05-08T00:00:00+00:00`

## What it is

A single timestamp marking when paper-evidence rows in `decisions`
became reliable enough to feed Phase 1B risk telemetry. Rows logged
before this cutoff are *legacy* — they pre-date Phase 0 fill telemetry
and lack `fill_price` / `fill_qty` instrumentation.

## Why it exists

Phase 1C's `paper_state_hygiene_report.py` produced
`ready_to_gate=False` purely because 84 legacy `decisions` rows were
flagged for missing fill data. Those rows are validation history — we
do not delete, backfill, or rewrite them. Instead, Phase 1D quarantines
them out of the *clean* hygiene verdict so a future promotion of a 1B
warning to a hard gate is not blocked by pre-instrumentation debt.

## How it shapes reports

Every Phase 1B/1C risk-telemetry report under `research/` runs twice:

- **Full-ledger view** — all rows. Surfaces legacy debt. Drives
  `ready_to_gate_all`.
- **Clean-epoch view** — rows with `ts >= CLEAN_PAPER_EVIDENCE_START`.
  Drives `ready_to_gate_clean`.

The JSON sidecars (`cache/research/*_latest.json`) keep the
pre-Phase-1D flat shape at the root so the dashboard's `RISK
TELEMETRY` panel continues to work, then attach `full_ledger` and
`clean_epoch` sub-blocks for the dual view.

Reports also accept `--since <ISO-ts>` to override the cutoff at
runtime (useful for sleeve-specific evidence windows). The override
shifts which rows are considered legacy vs clean — it does NOT mutate
any DB state.

## Quarantine sidecar

`data/state/paper_legacy_quarantine.json` lists every pre-epoch
`decisions` row with a hygiene issue (missing `fill_price` /
`fill_qty` / `pnl` / `exit_price`, or extreme `slippage_bps`).
Each entry carries:

- `decision_id`, `ticker`, `strategy`, `timestamp`
- `position_opened`, `position_closed`
- `missing_fields` — the specific columns that failed validation
- `quarantine_reason: pre_fill_telemetry_legacy`
- `broker_position_match` — Phase 1E: filled from
  `cache/state/broker_positions_snapshot.json` when present.
  One of `match` | `no_broker_position` | `closed_by_book` | `null`.
  Stays `null` when no snapshot is on disk.

The sidecar header also publishes (Phase 1E):

- `broker_match_source` — `broker_snapshot_sidecar` or
  `unavailable_no_cached_snapshot`
- `broker_snapshot_path` and `broker_snapshot_generated_at` for
  freshness audit
- `broker_match_counts` — `{match, no_broker_position, closed_by_book, unknown}`

The sidecar is rewritten atomically each `risk-telemetry` run. **DB
rows are never mutated.** The broker snapshot is also read-only — the
hygiene report never calls Alpaca itself.

## Phase 1E — broker snapshot diagnostic

The hygiene report can correlate quarantined `decisions` rows against
current broker state via a cache sidecar that the operator refreshes
on demand:

```bash
# One-shot, read-only Alpaca call. No order submission, no DB write.
SNIPER_ENV_PATH=/home/gem/secure/trading.env \
  .venv/bin/python scripts/snapshot_broker_positions.py --print

# Then re-run risk-telemetry so the hygiene report picks up the new
# snapshot and fills broker_position_match per quarantined entry.
./scripts/run_research_cycle.sh risk-telemetry
```

The snapshot writes to `cache/state/broker_positions_snapshot.json`
with `{generated_at, source: "alpaca.get_positions", count, positions[]}`.
**This script is operator-invoked only — it is NOT part of the
`risk-telemetry` workflow** so that workflow stays cache-only by
doctrine.

Match labels:

| Label                | Meaning                                                                  |
|----------------------|--------------------------------------------------------------------------|
| `match`              | broker has an open position on the same ticker                           |
| `no_broker_position` | snapshot was loaded; ticker is not present (book open / broker absent)   |
| `closed_by_book`     | book has `position_closed=1`; broker absence is consistent                |
| `null`               | snapshot unavailable; cannot say (see `broker_match_source`)              |

Dashboard `RISK TELEMETRY` panel (mode 3) shows:

- `clean epoch  all=Y/N  clean=Y/N  qrt=<count>` — Phase 1D verdicts
  plus the quarantined-row count
- `broker snap  pos=<count> <age>  m/n/c/u=<counts>` — Phase 1E
  snapshot age + match coverage

Both panel rows are cache-only reads. The dashboard never invokes the
broker snapshot CLI or the hygiene report.

## Operator workflow

```bash
# Cache-only run; emits all four sidecars + the quarantine file.
./scripts/run_research_cycle.sh risk-telemetry

# Override the cutoff (e.g. when piloting a tighter evidence window).
./scripts/run_research_cycle.sh risk-telemetry --since 2026-06-01T00:00:00+00:00

# Inspect the legacy debt:
jq '.count, .entries[0]' data/state/paper_legacy_quarantine.json
```

## Doctrine guardrails (unchanged from Phase 1B/1C)

- Diagnostic only. `ready_to_gate_*` are *published verdicts*, never
  wired to enforcement in this phase.
- No DB mutation. No provider calls. No dashboard wiring beyond the
  existing `RISK TELEMETRY` panel (which still reads the flat root
  shape).
- Additive migration. Existing JSON sidecar keys are preserved; the
  clean-epoch view is a new sub-block, not a rewrite.

## Updating the cutoff

If telemetry coverage is found to have started later (or earlier),
edit `CLEAN_PAPER_EVIDENCE_START` in `core/paper_evidence_epoch.py`,
re-run `tests/unit/test_paper_evidence_epoch.py` and
`tests/unit/test_paper_state_hygiene.py`, then re-run
`risk-telemetry`. The quarantine sidecar will be regenerated to
match.

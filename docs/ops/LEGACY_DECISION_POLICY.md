# Legacy Decision Policy

**Phase:** 1G.3 (report tooling landed in 1G.2)
**Report generator:** `research/legacy_decision_policy_report.py` (read-only, SELECT-only)
**Artifacts:** `cache/research/legacy_decision_policy_latest.json`, `logs/legacy_decision_policy_latest.txt`
**Last refreshed for this doc:** 2026-05-24 (counts below are from that run)

## What this covers

A small set of `decisions` rows pre-date the Phase 0 fill telemetry, so they are
marked `position_opened=1` but are missing `fill_price` / `fill_qty`. They pollute
the **full-ledger** verdict (`ready_to_gate_all` in `paper_state_hygiene_latest.json`)
but are intentionally excluded from the **clean-epoch** verdict
(`ready_to_gate_clean`). The clean-epoch start is the single source
`core/paper_evidence_epoch.py` → `CLEAN_PAPER_EVIDENCE_START = 2026-05-08T00:00:00+00:00`.

## Current status (read-only audit)

| Metric | Value |
|---|---|
| Affected legacy rows | **10** |
| Pre-clean-epoch | 10 / 10 |
| Inside clean epoch | 0 |
| Closed | 10 / 10 |
| Broker match = `closed_by_book` | 10 / 10 |
| Broker match = open/mismatch | 0 |
| Broker snapshot present | yes |

All 10 rows are SHORT/VOYAGER entries from 2026-04-23 → 2026-05-04 (e.g. GE, TSCO,
PEGA, CHTR, LMT, W, PH, CRS), every one **closed** and reconciled against the broker
snapshot as `closed_by_book`. Per-row recommendation: **`keep_quarantined`**.

## Why `ready_to_gate_all` remains false (and that's fine)

`ready_to_gate_all` is the full-ledger view and includes these 10 fill-incomplete
legacy rows, so it stays `false` by construction — the legacy debt is deliberately
kept *visible* rather than hidden. The gate that future promotion will actually
reference is **`ready_to_gate_clean`**, which is computed from the clean-epoch view
(rows on/after 2026-05-08). Those 10 rows are pre-epoch, so they do **not** affect
`ready_to_gate_clean`, which remains true while they stay quarantined.

In short: the full-ledger "false" is honest accounting of historical debt; the clean
epoch is unaffected.

## Policy

- **Default: keep quarantined.** Do not backfill, delete, or mutate these rows.
- The report **never** mutates the `decisions` table. Any archival annotation (e.g.
  setting `suspect_state` on these rows) requires a **separate, explicitly
  operator-approved** tool — it is not part of this report and not part of Phase 1G.3.
- Recommendation stands: **keep the quarantine** unless the operator explicitly
  approves an archival annotation. There is no correctness benefit to mutating closed,
  closed-by-book, pre-epoch rows, and doing so would rewrite validation history —
  forbidden by the additive-migrations doctrine.

## How to refresh

```bash
SNIPER_ENV_PATH=/home/gem/secure/trading.env \
  .venv/bin/python research/legacy_decision_policy_report.py --print
```

Related: `data/state/paper_legacy_quarantine.json` (the quarantine list),
`docs/ops/CLEAN_PAPER_EVIDENCE_EPOCH.md`, `cache/research/paper_state_hygiene_latest.json`.

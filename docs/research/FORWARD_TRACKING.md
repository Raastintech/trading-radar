# Forward Tracking — Reference (V2, Phase 5.1)

**Component:** `research/research_watchlist_forward_tracker.py` (`RESEARCH_FORWARD_TRACKER_V2`)
**Ledger:** `data/research/research_watchlist_history.jsonl` (append-only rows keyed `ticker|appearance_date`; the resolver fills fields in place, never deletes rows)
**Summary artifact:** `cache/research/research_forward_latest.json` + `logs/research_forward_latest.txt`
**Runner:** `./scripts/run_research_cycle.sh research-forward-tracker` (nightly chain)

## Horizons (canonical, pre-registered)

Defined once in `research/research_programs.py::TRACKED_HORIZONS` and imported by the tracker so architecture and measurement cannot drift:

```
5, 10, 15, 20, 30, 45, 60, 90, 126, 189, 252, 378   (trading days)
```

| Program | Primary horizons | Diagnostic horizons |
|---|---|---|
| Tactical (hold 5–15td) | 5, 10, 15 | — |
| Swing (hold 45–60td) | 30, 45, 60 | 5, 10, 20 |
| Long-Term (hold ~6–18mo) | 126, 189, 252, 378 | 30, 60, 90 |

Evaluation windows are defined **before** evidence collection. Do not add or move horizons to improve results; a change here requires a new pre-registration note in this file.

## Return methodology

- Entry = close on/after `appearance_date`; exit = close `h` trading bars later, from `cache/prices/{TICKER}.parquet`.
- Known bias (Phase 5 A8): the entry close is the same close the scan consumed (~0.45pp/10d optimistic at tactical horizons). Program-level validation should prefer lagged-entry robustness checks for Tactical verdicts.
- Benchmarks computed identically per horizon: **SPY, QQQ, IWM** (Phase 5.1) + sector ETF (`SECTOR_ETF_MAP`, SMH override for semiconductors). Fields: `{spy,qqq,iwm,sector}_ret_{h}d` and `ret_{h}d_vs_{spy,qqq,iwm,sector}`. Schema stamp `BENCHMARK_RETURNS_V2` (re-stamps force IWM/new-horizon backfill on old rows).

## Candidate metadata (Phase 5.1 routing)

Every ledger row carries: `research_program`, `program_holding_period_td`, `program_confidence` (from `classify_label`), plus `market_cap` when the scanner provides it. Old rows were backfilled by label. No candidate appears without a program.

## Max adverse excursion + priority split (Phase 5.1 follow-up)

- `mae_{h}d` per entry: lowest close inside the horizon window vs the entry close (≤ 0), computed under the same full-window maturity rule as returns.
- `priority_split` in the summary artifact: forward evidence at 5/10/20d split into `high_priority` (radar HIGH_PRIORITY_RESEARCH / TOP_RESEARCH), `watch_only` (WATCHLIST_RESEARCH / RESET_WATCH), `other` (EXTENDED_CROWDED / DATA_QUARANTINE), and `unstamped` — each with n, hit-rate vs SPY, mean/median return and excess, mean/worst MAE.
- **Stamping is next-run backfill:** the alpha radar runs after the tracker nightly, so `priority_label` lands on the following run when the radar sidecar's date matches the entry's `appearance_date`. Rows recorded before 2026-07-10 have no per-date radar snapshot and stay `unstamped` (reported, never dropped); the split becomes meaningful as stamped cohorts mature (~5 trading days after 2026-07-10 for 5d, etc.).

## Sample honesty rules

- `sample_status` is graded on **unique matured tickers** (`sample_basis: unique_tickers`), never raw rows; `matured_unique_tickers`, `matured_distinct_dates`, and `matured_by_horizon` are exported for transparency. (Raw rows overstated the sample ×3–4 via nightly re-appearances.)
- `resolution_coverage` (per 5/10/20d): rows old enough to have matured by the SPY calendar vs rows actually resolved, plus unresolved-ticker samples. **Any verdict quoted without its resolution coverage is incomplete** — unresolved rows are a survivorship hole (~29% at 10d as of 2026-07-10, dominated by warrants/micro-caps with no price parquets).
- Bucket verdicts (`PROMISING`/`EARLY_SIGNAL`/`MIXED`/`NO_FORWARD_EDGE`) remain absolute-return, 10d-based, and are **legacy/diagnostic only**. Program-level truth uses the pre-registered gates in `research/research_programs.py` (deduped episodes, matched benchmarks, date-clustered bootstrap) via `./scripts/run_research_cycle.sh research-programs`.

## Maturation calendar (from 2026-06-15 first entries)

| Horizon | First resolutions |
|---|---|
| 15d | ~2026-07-07 (flowing) |
| 20d | ~2026-07-14 |
| 30d | ~2026-07-28 |
| 45d | ~2026-08-18 |
| 60d | ~2026-09-09 |
| 126d | ~2026-12-14 |
| 189/252/378d | 2027-03 / 2027-06 / 2027-12 |

`resolved` on a ledger row means **all 12 horizons** resolved — it will stay false for ~18 months on healthy rows; use per-horizon fields, not the flag.

*Research only — forward returns are evidence, never trade recommendations.*

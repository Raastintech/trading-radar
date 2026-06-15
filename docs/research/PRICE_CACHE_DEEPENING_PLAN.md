# Price Cache Deepening Plan — Phase 1G.7 (Task 1)

*Generated 2026-06-09T01:08:07.111258+00:00 · research-only · PLAN ONLY (no provider calls made by this report).*

## Why
Phase 1G.6 found the research price cache is uniformly shallow (median ~110 bars). The production daemon overwrites `cache/prices/*.parquet` with ~90-day windows on every scan (see `core/data_gatekeeper.py` and `core/universe.py:_DAYS_BACK=90`), so MA200 and the Voyager 260-bar gate are effectively non-functional on the research cache and the scanner-truth Voyager-structural verdicts are fidelity-limited.

## Strategy: a separate deep cache
Because the daemon clobbers `cache/prices`, the deepening refresh writes **merge-on-write** parquets to `cache/prices_deep` instead. Research `dataio.load_prices` prefers the deep parquet when present and falls back to `cache/prices` otherwise — additive and a no-op until the refresh runs. The live execution path is untouched.

## Current depth distribution

| bars | tickers |
|---|--:|
| ≥60 | 5441 |
| ≥120 | 340 |
| ≥200 | 328 |
| ≥260 | 322 |
| ≥300 | 180 |

Universe size: **5598** · median **110** bars.

## Priority batches

| batch | total | needing deepen | already deep |
|---|--:|--:|--:|
| 1_alpha_board | 20 | 19 | 1 |
| 2_rs_lane | 25 | 7 | 18 |
| 3_theme_leaders | 28 | 18 | 10 |
| 4_top_missed_winners | 109 | 33 | 76 |
| 5_open_positions | 0 | 0 | 0 |

**Priority tickers:** 182 · **needing deepen:** 77.

## Estimated impact
- **Alpaca batch requests:** ~1 (batched at 200/request, SIP daily bars).
- **FMP budget:** none — OHLCV deepening does not touch FMP.
- **Storage:** ~1.6 MB additional parquet.
- **Dashboard:** unaffected; stays cache-only.

## Safe refresh command

Dry-run (default — **no provider calls**, prints the plan the tool would run):
```bash
SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python scripts/deepen_price_cache.py --priority
```
Execute (provider calls; writes to the deep cache, merge-on-write):
```bash
SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python scripts/deepen_price_cache.py --priority --execute
```

**Do not run the `--execute` form until this plan is reviewed.** The tool defaults to dry-run and requires the explicit `--execute` flag. It never writes `cache/prices`, never touches the DB, governance, or live capital.

## After refresh
Re-run `research/price_cache_coverage_audit.py` (Phase 1G.7 Task 2 buckets) to confirm coverage, then re-run the scanner-truth review to confirm or withdraw the Voyager-structural conclusions.


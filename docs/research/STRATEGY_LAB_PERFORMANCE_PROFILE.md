# Strategy Lab Performance Profile (Phase 1H.1)

Generated: 2026-06-12T06:01:36.737051+00:00

RESEARCH_ONLY: no broker orders, no paper signals, no trade proposals, no registry/governance/execution/live-capital changes.

## Baseline bottlenecks (measured 2026-06-12, pre-optimization)

- compute_features_asof recomputed every rolling window per (ticker, date): ~9 ms x 599 tickers per date
- atr()/rsi() recomputed from scratch per (ticker, date) (~2.1 s + 1.3 s per date)
- _bench_ret re-sliced SPY/QQQ frames per ticker per date (~2.7 s per date)
- pandas attrs deepcopy on every slice (~1.7 s per date in copy.deepcopy)
- get_forward_window copied the full merged price frame per simulated trade
- simulate_trade re-simulated the identical price path once per cost model (3x)
- feature lru_cache (250k entries) would thrash on full windows: 599 tickers x 613 dates = 367k entries

## Measurements

| Metric | Baseline (pre-1H.1) | Current |
|---|---:|---:|
| Per-date cost, warm new date | 7.77 s | 0.0841 s |
| Same-date memoized repeat | 0.004 s | 0.0531 s |
| simulate_trade per call | 0.44 ms | 0.1072 ms |
| Peak RSS | 153.3 MB | 293.6 MB |
| Estimated full lab (unsampled, 613 dates) | 79.4 min | 0.86 min |
| Estimated exact walk-forward | 85.0 min | 9.0 min |
| Estimated exact threshold sweep | 95.0 min | 22.02 min |

Per-date speedup vs baseline: **x92.4**

## Optimization targets identified

1. Vectorize feature computation once per ticker (rolling/ewm columns) instead of per (ticker, date).
2. Share SPY/QQQ benchmark return tables instead of re-slicing frames per ticker per date.
3. Stop copying the full merged price frame per forward-window lookup.
4. Simulate each trade path once and apply the three cost models to the same path.
5. Scan the universe from precomputed column arrays without building feature dicts for non-survivors.

## Slowest functions (cumtime over one profiled date)

| cumtime (s) | ncalls | function |
|---:|---:|---|

All measurements are cache-only and research-only; no providers, no DB writes, no execution paths.

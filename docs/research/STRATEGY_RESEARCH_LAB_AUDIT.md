# Strategy Research Lab Audit

Generated: 2026-06-12

## Executive Finding

The repository has strong research diagnostics, forward-validation ledgers, and price-cache utilities, but it does not yet have a unified no-lookahead strategy research lab. Existing tools answer useful questions about cohorts that were already emitted, missed, or frozen. They do not consistently replay candidate generation across historical as-of dates, apply common cost and exit assumptions, compare variants across identical windows, or enforce train/validation/test separation.

The new lab should be research-only, cache-only, price-first, and explicit about data reliability. It must not import execution, governance, live-capital, paper-signal, broker, or provider paths.

## What Already Exists

1. `research/strategy_tournament.py`
   - Research-only, cache-only tournament summary over the `data/state/stock_lens_forward_log.jsonl` event spine.
   - Compares labeled event families against cash and random controls.
   - Uses a fixed friction constant from the tactical resolver when available.
   - Useful for report shape, verdict ladders, random controls, and safety language.
   - Limitation: it is not a true historical signal replay. It grades existing Stock Lens snapshots and their recorded forward outcomes.

2. `research/scanner_truth/`
   - `dataio.py` provides read-only price/profile/cache helpers and prefers `cache/prices_deep` when available.
   - `filters.py` mirrors production price-derived SNIPER, VOYAGER, and universe gates as pure as-of functions.
   - `baselines.py`, `entry_timing.py`, `filter_audit.py`, `funnel_trace.py`, and related modules provide scanner autopsy and baseline comparison utilities.
   - Best reusable component for no-lookahead price-derived gates.

3. Recall-shadow / RS recall infrastructure
   - `research/rs_recall_lane.py` computes RS, momentum, extension, pullback, and sector-relative labels using bars at-or-before an as-of date.
   - `research/rs_recall_forward_validation.py` validates historized RS recall picks out-of-sample.
   - `research/recall_shadow_lens_feeder.py` and `research/recall_shadow_gk_cohort_freeze.py` preserve research-only routing/cohort artifacts.
   - `research/recall_shadow_gk_forward.py` measures the frozen 20-name Gatekeeper cohort without rewriting it.
   - Useful for variant definitions and forward-measurement patterns.
   - Limitation: current validation history is young and mostly post-hoc cohort maturation.

4. Short detection / short-side diagnostics
   - `research/short_detection_truth_audit.py`, `research/short_detection_forward_validation.py`, and `research/short_opportunity_radar.py` are diagnostic-only.
   - Short forward validation signs returns correctly as negative of underlying long returns.
   - Useful for short-return math and safety language.
   - Limitation: history is too young and explicitly does not unfreeze `SHORT_A`.

5. Existing older backtests
   - `research/backtests/sniper_backtest.py` and `research/backtests/voyager_v2_backtest.py` contain historical logic, thresholds, and backtest assumptions.
   - `research/backtests/backtest_data_loader.py` has a cache-first backtest cache under `cache/backtest_prices`.
   - Useful for threshold provenance and older price coverage.
   - Limitation: older scripts have fixed/manual universes, optional provider fallback, and some metadata/fundamental lookahead caveats. They should not be used as Phase 1H truth without stricter cache-only wrappers.

6. Price/cache infrastructure
   - `cache/backtest_prices`: 132 parquet files, coverage observed from 2018-11-27 to 2026-04-20, median about 1510 rows.
   - `cache/prices_deep`: 192 parquet files, coverage observed from 2025-01-13 to 2026-05-26, median about 343 rows.
   - `cache/prices`: 5616 parquet files, broad current coverage, observed from 2019-04-26 to 2026-06-11, but median only about 113 rows.
   - Practical conclusion: 2024 tests are possible only for the limited `cache/backtest_prices` universe. 2025 and 2026 tests can use the deep cache plus safe shallow fallback.

7. Universe, Stock Lens, and Gatekeeper history
   - `data/research/universe_selection_history.jsonl` has dated universe-selection rows beginning in late May 2026.
   - `data/state/stock_lens_forward_log.jsonl` has historized Stock Lens snapshots with forward outcomes.
   - `cache/research/stock_lens_*_latest.json` and `cache/research/executive_gatekeeper_*_latest.json` are current artifacts only unless a dated log exists.
   - `data/research/recall_shadow_gk_cohort_1g17a.json` is immutable and must not be rewritten.

## What Can Be Reused

- `research.scanner_truth.dataio` read/write helpers, profile/theme classifiers, and benchmark calendar behavior.
- `research.scanner_truth.filters` pure price-derived gates and constants.
- `research.rs_recall_lane` feature and label definitions, adapted into lab-local pure functions.
- `research.power_trend_extension_study` classification concepts for extension quality, adapted without importing Gatekeeper.
- `research.short_detection_forward_validation` short-side return convention.
- `research.strategy_tournament` report/verdict style and safety constraints.
- `cache/backtest_prices`, `cache/prices_deep`, and `cache/prices` as local price sources.
- `research/mcp_audit_orchestrator.py` and `dashboards/gem_trader_hq.py` cache-only sidecar surfacing pattern.

## What Is Only Post-Hoc Cohort Validation

- `strategy_tournament.py`: classifies already-historized Stock Lens snapshot rows and grades their forward outcomes.
- `rs_recall_forward_validation.py`: validates already-historized RS recall top-N picks.
- `recall_shadow_gk_forward.py`: measures the frozen 1G.17A cohort after it was selected.
- `short_detection_forward_validation.py`: measures rows already captured by short detection history.
- `stock_lens_forward_report.py`, `forecast_forward_report.py`, and resolver reports: outcome maturation and diagnostics, not historical strategy replay.

These are valid forward-validation tools, but they are not enough to decide whether changing SNIPER, VOYAGER, or Gatekeeper thresholds would have worked historically.

## What Is Missing For Real Strategy Backtests

- A reusable historical as-of data layer with explicit data-quality labels.
- Common entry and exit simulation across variants.
- Shared cost/slippage/borrow assumptions.
- Multi-window backtests on identical universes and calendars.
- Ticker, sector, theme, regime, and concentration breakdowns from generated trades.
- Reproducible random baselines over the same candidate universe and dates.
- Benchmark comparison against SPY, QQQ, and cash over the same dates.
- Walk-forward split discipline: tune on train, choose on validation, report untouched test.
- Controlled threshold sweep that preserves all tried parameter sets and penalizes complexity.
- Tests proving no imports or writes into execution/governance/paper/live-capital paths.
- Cache-only MCP/dashboard summary for the lab result.

## Point-In-Time Reliable Data

- OHLCV bars loaded from local parquet and sliced to `bars <= asof`.
- Price-derived features computed only from historical bars: moving averages, ATR, RSI, 20/60 momentum, volume expansion, high/low proximity, extension, MFE/MAE over forward windows after signal.
- Benchmarks from local SPY/QQQ/SMH/XLK bars, when present.
- Dated RS recall lane history rows for their own as-of dates.
- Dated universe-selection history rows, but only from the date range retained in `data/research/universe_selection_history.jsonl`.
- Dated Stock Lens forward-log rows for snapshot outcomes and labels on the actual snapshot dates.

## Not Fully Point-In-Time Reliable

- Current `stock_lens_*_latest.json` labels for old dates.
- Current `executive_gatekeeper_*_latest.json` labels for old dates.
- Current Alpha board membership for old dates unless a dated history exists.
- Current profile sector/theme/market-cap metadata for old dates.
- Current universe snapshot membership for old dates.
- Fundamental and 13F data from older backtests unless explicitly filtered by filing or availability date.
- Any options, social, short-interest, or lens diagnostics that are current-only.

These may be used as `CURRENT_METADATA_APPROXIMATION` context, never as historical decision truth.

## Must Be Excluded To Avoid Lookahead

- Future bars in feature generation.
- Forward returns, MFE, MAE, stop hits, and target hits in entry decisions.
- Future Stock Lens labels or Gatekeeper labels.
- Current Alpha board, current universe, current sector/theme metadata as if it existed historically.
- Provider calls from backtests, dashboard, MCP, or tests.
- Any import path that can create paper signals, trade proposals, broker orders, governance decisions, or live-capital changes.
- Any mutation of frozen cohorts, historical forward logs, `paper_signals`, `voyager_paper_signals`, `decisions`, `veto_log`, `scan_results`, or strategy registry state.

## Recommended Lab Architecture

1. `research/strategy_lab_data.py`
   - Cache-only loaders.
   - Source priority: `cache/backtest_prices`, `cache/prices_deep`, then safe `cache/prices` fallback.
   - `load_price_frame_asof(ticker, asof)`, `compute_features_asof(ticker, asof)`, `build_universe_asof(asof, mode)`, `get_forward_window(ticker, asof, horizon)`, and `validate_no_future_bars()`.
   - Every output carries one of: `TRUE_POINT_IN_TIME`, `RECONSTRUCTED_FROM_PRICE_ONLY`, `CURRENT_METADATA_APPROXIMATION`, `NOT_RETAINED`.

2. `research/strategy_research_lab.py`
   - Pure research variant functions.
   - Common entry/exit/cost simulator.
   - Variant and window summary with baseline comparisons and verdicts.
   - Writes only cache/log/docs artifacts.

3. `research/strategy_walk_forward.py`
   - Uses the same engine and variants.
   - Performs train/validation/test split or rolling blocks if history is short.
   - Rejects train-only and unstable-parameter variants.

4. `research/strategy_threshold_sweep.py`
   - Limited grids only.
   - Train/validation/test required.
   - Preserves every tried parameter set.
   - Never mutates production thresholds.

5. Cache-only surfacing
   - Add a compact Strategy Lab reader to MCP orchestrator and dashboard.
   - The dashboard must read only `cache/research/strategy_research_lab_latest.json`.

## Audit Verdict

Build Phase 1H as a new research-only lab. Reuse price-derived gates, data readers, and report/safety conventions, but do not depend on current Gatekeeper/Lens/Alpha labels for historical decisions. Older 2024 claims must be labelled limited because only the backtest cache covers that era for a small universe.

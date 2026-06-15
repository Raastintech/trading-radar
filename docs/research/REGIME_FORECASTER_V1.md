# Market & Sector Regime Forecaster V1 — Phase 1

A research-only probabilistic regime and sector-rotation forecaster.

It answers four questions every run:

1. What market regime are we likely in now?
2. What is the likely environment over the next 5–10 trading days?
3. Which sectors are leading, improving, neutral, weakening, or defensive?
4. Which active strategies should be favored, allowed, selective, or avoided?

It is **not** trade approval, **not** a crystal ball, **not** paper evidence,
and **not** a sleeve. It is a strategic research lens.

## Phase 1 scope

This phase delivers the standalone forecasting report only. Out of scope:

- single-stock lookup overlay (Phase 3+)
- dashboard integration (Phase 3+)
- ML / deep-learning models (Phase 4+)
- news / social as a core forecasting input — never planned as core; may be a
  manual context overlay only
- any change to sleeve logic, paper evidence, governance, execution,
  Alpha Discovery, Market Posture, or the Daily Entry Validator

## Files

- `core/regime_forecaster.py` — pure-Python feature builders + heuristic
  regime / sector-state / strategy-favorability mapping. No I/O. No provider
  calls.
- `research/regime_forecast.py` — CLI runner. Loads inputs, calls the core,
  writes artifacts.
- `cache/research/regime_forecast_latest.json` — machine-readable artifact.
- `cache/research/regime_forecast_vix_history.json` — small rolling VIX
  history used by the volatility-change features.
- `logs/regime_forecast_latest.txt` — human-readable summary.
- `docs/research/REGIME_FORECASTER_V1.md` — this document.

Suggested command:

```bash
SNIPER_ENV_PATH=/home/gem/secure/trading.env \
  .venv/bin/python research/regime_forecast.py --mode daily
```

Smoke command (no providers, cache-only):

```bash
GEM_TRADER_SKIP_DOTENV=true \
  .venv/bin/python research/regime_forecast.py --mode daily --offline
```

## Data universe (V1)

Market ETFs:

- SPY, QQQ, IWM, DIA

Sector ETFs:

- XLK, XLF, XLV, XLE, XLY, XLI, XLP, XLU, XLB, XLRE, XLC

Risk / volatility / credit / rate proxies:

- VIX (FMP primary, yfinance `^VIX` fallback)
- VXX (used when VIX is unavailable or to confirm vol-expansion)
- TLT, HYG, LQD
- HYG/LQD ratio
- IWM/SPY ratio (small-cap risk appetite proxy)

Source order, per loader (prices):

1. **Local parquet cache** `cache/prices/{SYM}.parquet`. Trading-day-aware
   freshness check: a frame whose last bar is the most recently completed
   US-equities session is considered fresh regardless of wall-clock age, so
   weekend / pre-open runs do not hit any provider.
2. **Alpaca SIP daily bars** (`AlpacaClient.get_daily_bars_batch`) — single
   batched request (chunk size 200; the V1 ~19-symbol universe is one
   request). Cache-first per ticker via Gatekeeper (TTL_OHLCV = 12 h).
3. **FMP `/historical-price-eod/full`** (`FMPClient.get_ticker_bars`) —
   per-ticker, but each request is wrapped in a 12 h Gatekeeper cache and
   gated by the global token-bucket limiter at `FMP_CALLS_PER_MINUTE`. Used
   only for symbols Alpaca did not supply. Skip with `--no-fmp`.
4. **yfinance daily bars** — single batched `yf.download(...)` for whatever
   is still missing.

Each non-cache fetch (FMP, yfinance) is written back to the project's local
parquet cache so the next run hits step 1 and never spends provider budget.
Alpaca's client already persists its own fetches via Gatekeeper.

VIX source order:

1. **Local rolling history** `cache/research/regime_forecast_vix_history.json`
   provides the 5d / 10d change features when no provider is reachable.
2. **FMP `get_vix()`** for the latest level (5-min Gatekeeper cache).
3. **yfinance `^VIX`** for history backfill when FMP is unavailable or the
   cached history is short.

Provider choices that are **explicitly avoided** in V1:

- FMP / Tradier in tight loops (we only call FMP per-symbol when Alpaca
  cannot supply the bar, and only when the Gatekeeper cache also misses)
- 13F as a timing input
- News / social as a core forecasting input

Rate-limit / API-budget guarantees:

- Local parquet cache is checked first with a trading-day-aware freshness
  rule, so back-to-back runs (or weekend runs) issue zero provider calls.
- Alpaca: one batched SIP request per chunk; cache-first via Gatekeeper.
- FMP: per-ticker fetch, but each is 12 h Gatekeeper-cached and gated by a
  token bucket at `FMP_CALLS_PER_MINUTE`. Use `--no-fmp` to skip the tier
  entirely.
- yfinance: a single batched download for any remaining symbols, persisted
  back to the parquet cache.

## Feature families

### A. Market trend (per market ETF)

- 5d / 10d / 20d returns
- price vs 20d / 50d / 200d MA
- distance from rolling 60d high / low
- QQQ/SPY and IWM/SPY relative strength

### B. Sector rotation (per sector ETF)

- 5d / 10d / 20d relative strength vs SPY
- price vs 20d / 50d MA
- improving / weakening flag (delta of short-vs-long RS)
- numerical rank
- discrete state: `Leading / Improving / Neutral / Weakening / Defensive`

### C. Volatility / risk

- VIX level (when available)
- VIX change over 5d / 10d
- VIX vs trailing 20d average
- annualized realized volatility on SPY (20d log-return basis)
- VXX trend (return + above-MA20)

### D. Credit / rates / risk-appetite proxies

- TLT trend (return + above-MA flags)
- HYG vs LQD level + 5d / 20d ratio change
- IWM/SPY 5d / 20d ratio change
- composite risk-appetite label: `risk-on / leaning risk-on / neutral /
  leaning risk-off / risk-off`

### E. Breadth proxy

Two lenses:

1. Sector-level breadth — % of sector ETFs above 20d / 50d MA.
2. Universe-level breadth — % of `strategy_candidates` above MA20 / MA50 /
   MA200, taken from the dashboard's universe snapshot. This is read-only
   and entirely optional; if the snapshot is missing or lacks the relevant
   fields, the breadth family degrades to "available=False" without
   affecting the rest of the report.

## Regime classes

V1 produces coarse, 5%-snapped probabilities for these regimes:

1. Bull Continuation
2. Bull Pullback / Buy-the-Dip
3. Chop / Range
4. Risk-Off
5. Volatility Expansion / Stress
6. Bear Rally / Unstable Rebound

Classification is a transparent heuristic — no ML, no opaque score. Each
regime accumulates contributions from the feature families; raw scores are
normalized then snapped to a 5% grid so the output never claims more
precision than the underlying signals support.

Headline output also includes:

- current regime (the modal class)
- 5d bias and 10d bias: `constructive / mixed / neutral / chop / defensive`
- confidence: `low / medium / high`, driven by the top-probability margin
  and data-availability
- a single concrete invalidation rule for the current regime

## Strategy favorability

Active strategies considered:

- VOYAGER (trend / accumulation)
- SNIPER_V6 (breakouts)
- SHORT_A (short-side)
- ALPHA_DISCOVERY (research board)

Each strategy gets:

- `stance` ∈ {favored, allowed, selective, avoid}
- `reason` (1 line)
- `invalidation` (1 line)

This mapping is advisory research context only. It does **not**:

- change strategy gates, scoring, or paper logic
- promote / demote sleeves
- affect Alpha Discovery scoring or Market Posture output
- affect the Daily Entry Validator

Stances are recomputed each run from the current regime + market context.
Strategy modules do not import from this module.

## Output schema (artifact JSON)

Top-level fields written to `cache/research/regime_forecast_latest.json`:

- `version`, `phase`, `mode`, `built_at`
- `headline`: `current_regime`, `bias_5d`, `bias_10d`, `confidence`,
  `main_invalidation`
- `regime_probabilities`: ordered list of `{regime, probability}`
- `regime_invalidations`: per-regime invalidation rule
- `trend_score`, `constructive_mass`, `defensive_mass`
- `market_trend`: per-ETF trend features + relative strength block
- `sector_rotation`: ranked rows + Leading / Improving / Weakening /
  Defensive groupings
- `volatility`: VIX, VIX change, realized vol, VXX
- `credit_rates`: TLT/HYG/LQD blocks + risk-appetite composite
- `breadth`: sector breadth + optional universe breadth
- `strategy_favorability`: per-strategy stance + reason + invalidation
- `factor_contributions`: top bullish + bearish factors
- `data_quality`: `spy_bars`, `sector_frames_available`,
  `vix_history_points`, `missing_layers`
- `frames_summary`: per-symbol bar count, last bar, last close
- `data_sources`: which provider tier was used per data layer
- `guardrails`: declarative list (see below)

## Guardrails

- research-only; not trade approval
- not paper evidence; not governance; no execution
- no sleeve mutation
- Alpha Discovery / Market Posture / Daily Entry Validator unchanged
- no news/social as core forecasting input
- no 13F as a timing input
- no ML in Phase 1
- cache-first; degrades gracefully on missing data
- shows uncertainty honestly (5%-snapped probabilities + explicit confidence
  + explicit `missing_layers`)
- no dashboard provider calls; the dashboard, when wired in Phase 3, must
  read only the JSON artifact

## Validation plan (Phase 2 — not built yet)

The Phase 1 artifact carries `validation_status: "Phase 1: not validated yet
(Phase 2)"`. The planned Phase 2 backtest will measure, with strict
no-lookahead rules:

- 5d and 10d SPY forward returns conditioned on regime
- realized volatility expansion vs predicted Volatility Expansion / Stress
  regime
- leader-vs-laggard sector spread realization (top-3 vs bottom-3 by
  10d RS)
- regime hit rate by class
- Brier score / calibration of the probability output
- regime-transition stickiness (how often the current regime survives the
  next 5 trading days)

No paper-evidence or sleeve-promotion outcomes will ever be derived from
the validation harness; it is offline analysis only.

## Limitations

- Heuristic, not learned: probabilities are a coarse aggregation, not a
  trained classifier. They reflect the rules in `core/regime_forecaster.py`
  exactly.
- Cache may be sparse: most sector ETFs in the live cache only retain ~19
  bars from the scanner's normal scan range. The forecaster falls back to
  yfinance to fill the gaps when run online; offline-only runs will have
  shorter sector RS windows.
- VIX coverage depends on FMP / yfinance availability. Realized vol on SPY
  is always present as a backup volatility lens.
- Universe-level breadth depends on the dashboard's universe snapshot
  having been written; otherwise only sector-ETF breadth is reported.
- This V1 surface is intentionally compact. Single-stock context, options
  structure, and intraday signals are out of scope.

## Future phases

- Phase 2: validation harness and calibration report.
- Phase 3: read-only dashboard panel + single-stock lookup that *consumes*
  this artifact (no provider calls on render).
- Phase 4 (only if validation supports it): learned components or richer
  feature families.

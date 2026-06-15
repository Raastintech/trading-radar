# Stock Research Lens V1 — Phase 3

A single-stock research lens that combines all available research layers into
one honest 5d / 10d / 20d view per ticker.

This is **research-only**. It is not trade approval, not paper evidence, not
sleeve approval, and not execution. It does not modify any other module.

## Files

- `core/stock_research_lens.py` — pure logic: technical features, layer
  scorers, transparent composite, hard caps, label resolver.
- `research/stock_lens_runner.py` — runner: loads each layer (cache-first),
  calls the core, writes JSON + text artifacts.
- `research/regime_forecast.py` — extended with `--ticker / --refresh /
  --cache-only / --horizon` to dispatch into the lens runner.
- `cache/research/stock_lens_<TICKER>_latest.json` — machine artifact (per
  ticker, uppercase).
- `logs/stock_lens_<TICKER>_latest.txt` — human-readable summary.
- `docs/research/STOCK_RESEARCH_LENS_V1.md` — this document.

No edits to:

- `core/daily_entry_validator.py` (called as-is)
- `core/research_assist_bte.py` (Market Posture; called as-is)
- `core/alpha_discovery.py` / Alpha Discovery scoring
- `research/social_arb_radar.py`
- `core/regime_forecaster.py` (Phase 1, called as-is when `--refresh`)
- live sleeve, paper, governance, execution, or dashboard code

## Commands

```bash
# Live (provider-backed) — recommended:
SNIPER_ENV_PATH=/home/gem/secure/trading.env \
  .venv/bin/python research/regime_forecast.py --ticker AAPL

# Cache-only (no providers):
GEM_TRADER_SKIP_DOTENV=true \
  .venv/bin/python research/regime_forecast.py --ticker AAPL --cache-only

# With horizon hint (lens always reports 5/10/20):
SNIPER_ENV_PATH=/home/gem/secure/trading.env \
  .venv/bin/python research/regime_forecast.py --ticker NVDA --horizon 20

# Force a fresh market regime computation rather than reading the cached artifact:
SNIPER_ENV_PATH=/home/gem/secure/trading.env \
  .venv/bin/python research/regime_forecast.py --ticker XOM --refresh
```

CLI:

```
--ticker SYMBOL          run the single-stock lens (uppercase enforced in artifact names)
--horizon N              horizon hint in trading days (default 20; lens still reports 5/10/20)
--cache-only             alias for --offline; no provider calls
--refresh                ignore cache/research/regime_forecast_latest.json and recompute
--no-fmp                 skip the FMP price-bars tier
--print-json             print the artifact JSON to stdout after writing
```

## Data layers

Each layer is optional and degrades gracefully. If a layer is unavailable,
it is labelled so in the output and excluded from the composite (the
remaining layer weights are renormalized to sum to 1.0).

| # | Layer | Source | Failure mode |
|---|---|---|---|
| 1 | Market regime | `cache/research/regime_forecast_latest.json` (or recompute if `--refresh`) | layer becomes "Unknown" with score 0 |
| 2 | Sector | FMP `get_company_profile` (24h Gatekeeper cache) → SPDR sector ETF mapping → sector rotation block from the market forecast | layer becomes "Unknown" with score 0 |
| 3 | Stock technicals | cache → Alpaca → FMP → yfinance (same chain as Phase 1) | layer becomes "Unknown" if no usable bars |
| 4 | Daily Entry Validator | `core.daily_entry_validator.validate_daily_entry(bars)` | "Watch Only" when <80 bars, else one of: Buyable Now / Watch Reclaim / Pullback Forming / Watch Only / Too Extended / Broken / Avoid |
| 5 | Alpha Discovery | `cache/research/alpha_discovery_board_latest.json` and `..._overlay_latest.json` | layer becomes "No data" or "Not on Alpha board" |
| 6 | Market Posture | rebuilt via `core.research_assist_bte.build_research_bte` against `cache/universe/universe_snapshot_latest.json` | layer becomes "Unknown" if snapshot missing |
| 7 | Options | not wired in V1 | layer is permanently "No data" until Phase 4 |
| 8 | Social Arb | `cache/research/social_arb_latest.json` | layer becomes "No data" or "No useful signal" |
| 9 | 13F / institutional | not wired in V1 (slow background only) | layer is permanently "No data" until Phase 4 |

## Stock technical features

For each ticker the lens computes:

- price vs EMA20 / EMA50 / MA200
- 5d / 10d / 20d return
- 5d / 10d / 20d relative strength vs SPY
- distance from rolling 60d high / 60d low
- ATR%(14)
- 5d-vs-trailing-20d volume ratio
- discrete state ∈ {trend up, extended, pullback within trend, neutral, weakening, oversold bounce attempt}

## Weighting model

Transparent, fixed weights for the 10–20 day view (sums to 1.00):

```
market_regime           0.15
sector                  0.20
technicals              0.25
entry_validator         0.20
options                 0.10
alpha_posture_overlap   0.07
social                  0.03
```

`alpha_posture_overlap` is the average of the Alpha Discovery and Market
Posture layer scores when both are available. Missing layers are dropped
from the sum and the remaining weights are re-normalized.

`13F / institutional` is **not** weighted in V1; it is background-only and
explicitly excluded from any timing decision.

## Hard caps

The label resolver enforces these in order. The first match wins for the
final label; multiple caps may be recorded in `hard_caps_fired`.

1. **Daily Entry Validator says `Broken / Avoid`** → label is forced to
   `Bearish` (if composite is negative) or `Avoid / no edge` (otherwise).
2. **Stock is technically extended OR Daily Entry Validator says `Too
   Extended`** → any `Bullish` label is rewritten to `Bullish but extended`.
   Cannot be `Buyable Now`.
3. **Sector is `Weakening` and the stock's RS-vs-SPY 10d ≤ +1.5pp** → any
   `Bullish` label drops to `Bullish but not buyable yet`.
4. **Market regime is `Defensive`** → any `Bullish` label drops to `Bullish
   but not buyable yet`.
5. **Daily Entry Validator is not `Buyable Now`** → any remaining `Bullish`
   label drops to `Bullish but not buyable yet` (so we never imply a fresh
   entry without the validator's agreement).

Confidence is independently downgraded:

- `low` if fewer than 2 of {market regime, sector, technicals, entry
  validator} are available, or if the market regime layer's own confidence
  is `low`.
- `high` only when all 4 of those layers are available AND the market
  regime layer is `high`.
- `medium` otherwise.

## Output labels (vocabulary)

Only these labels are ever emitted:

- **Bullish**
- **Bullish but extended**
- **Bullish but not buyable yet**
- **Neutral**
- **Bearish**
- **Bearish but oversold**
- **Avoid / no edge**

The lens never emits "Buy now", "Sell", or any execution-style verb.

## Composite scores (rounded, no fake precision)

- `composite` ∈ [-1.0, +1.0] (weighted layer score)
- `bullish_score` = 100 × max(0, composite)
- `bearish_score` = 100 × max(0, -composite)
- `entry_quality_score` ∈ [0, 100], driven by the Daily Entry Validator's
  state (capped at 30 if technicals say "extended")
- `risk_score` ∈ [0, 100], rises with Defensive market, Weakening sector,
  technical extension, high ATR%, and any negative options score

## Output sections (per ticker)

The text report contains:

1. Headline — one sentence: ticker, label, confidence, top reasons
2. Horizon view — 5d / 10d / 20d
3. Layer agreement table — view + notes per layer
4. Composite scores
5. Hard caps applied (if any)
6. Conclusion — one-sentence honest manual recommendation
7. Invalidation conditions — concrete EMA / VIX / sector triggers
8. Next manual checks — chart, earnings, options, sector ETF, news
9. Technicals raw — full per-feature dump
10. Weights table
11. Data quality notes — every unavailable layer named
12. Validation plan stub (Phase 4)
13. Guardrails

## Sample outputs (current run, 2026-04-27)

**AAPL — Technology / XLK:** label `Bullish but not buyable yet`,
confidence `high`, composite `+0.39`, bullish_score 39, entry_quality 55,
risk 30. Cap fired: `entry_validator!=Buyable Now`. Conclusion: watch for a
clean reclaim of EMA20 with confirmation volume; otherwise skip.

**NVDA — Technology / XLK:** label `Bullish but not buyable yet`, conf
`high`, composite `+0.21`, bullish_score 21, entry_quality 55, risk 30.
Same cap as AAPL.

**XOM — Energy / XLE:** label `Neutral`, conf `high`, composite `-0.09`,
bullish_score 0, bearish_score 9, entry_quality 35, risk 45. No hard cap
fired (composite already in the Neutral band). Sector layer flagged
`Weakening` with RS 10d -5.20pp; technicals `Bearish but oversold`.

## Validation plan stub (Phase 4 — not built yet)

The lens does not yet have a forward-return validation harness. A future
Phase 4 should test:

- forward 5d / 10d / 20d returns by emitted label
- hit rate by `confidence` bucket
- whether `Bullish but extended` actually avoids worse forward draws than
  bare `Bullish`
- whether sector-aligned bullish picks beat sector-conflicted bullish picks
- whether Entry-Validator `Buyable Now` picks improve forward expectancy
  vs Watch / Reclaim states
- false-confidence audit (high-conf labels that misfire)

Phase 2's regime forecaster validation already cast doubt on the upstream
regime probabilities being calibrated — Phase 4 should not assume the
market-regime layer is well-calibrated; it may need to *demote* the
market-regime weight on the basis of validation evidence.

## Limitations

- Heuristic-only V1. No learned weights. The composite is a fixed-weight
  weighted sum.
- Options and 13F layers are not implemented; both are flagged "No data".
- Market regime layer carries forward Phase 2's known calibration weakness
  — treat it as descriptive, not predictive.
- Sector mapping uses a small lookup table; FMP industries that aren't in
  the table will fail to a `None` ETF and the sector layer will be marked
  unavailable.
- The lens emits a single composite label per run; it does not produce a
  position-sizing recommendation or a stop level.
- The `horizon_view` block currently emits the same label across 5d / 10d
  / 20d (with a small re-label at 20d). Per-horizon scoring is a Phase 4
  candidate.

## Guardrails (enforced)

- research-only / not trade approval / not paper evidence
- no execution / no governance / no sleeve mutation
- no Alpha Discovery scoring change, no Market Posture logic change, no
  Daily Entry Validator change, no Social Arb change, no dashboard change
- cache-first; honours `--cache-only / --offline`
- no tight API loops — provider chain reuses the Phase 1 cache layers
  (Alpaca batch, FMP 12h Gatekeeper, yfinance batch fallback)
- degrades gracefully when any layer is missing
- never hallucinates missing layers — every absent layer is named in
  `data_quality_notes`
- coarse, rounded scores; no fake decimals

## Future phases

- Phase 4a: Tradier options layer (call/put tilt, IV expansion, unusual
  participation proxy) — replaces the V1 placeholder.
- Phase 4b: 13F slow-background layer (institutional sponsorship trend) —
  background only, never a timing trigger.
- Phase 4c: forward-return validation harness for the lens labels (mirrors
  Phase 2 for the upstream regime).
- Phase 5: dashboard integration — read-only consumer of the JSON
  artifact, no provider calls on render. Out of scope here.

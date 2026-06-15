# Market & Sector Regime Forecaster — Validation (Phase 2)

Walk-forward, no-lookahead historical validation of the V1 forecaster.

This document is the spec for `research/validate_regime_forecaster.py` and
captures the current verdict from the most recent run.

## Purpose

Answer five honest questions about V1:

1. Did predicted bullish regimes lead to better forward SPY/QQQ returns?
2. Did predicted risk-off / stress regimes show weaker forward returns or
   higher realized volatility?
3. Did predicted sector leaders outperform predicted laggards?
4. Are forecast probabilities even roughly calibrated?
5. Is the forecaster useful enough to keep as-is, recalibrate, or downgrade
   to descriptive-only?

The validator does not change live sleeves, paper evidence, governance,
execution, the dashboard, Alpha Discovery, Market Posture, the Daily Entry
Validator, or Social Arb. It is offline analysis only.

## Files

- `research/validate_regime_forecaster.py` — walk-forward engine + metrics.
- `core/regime_forecaster.py` — the V1 forecaster being validated. **Not
  modified by this phase.**
- `cache/research/regime_validation_prices/` — validation-only deep-history
  parquet store. **Separate from `cache/prices/`** so the live scanner cache
  is never touched by the validator's backfill.
- `cache/research/regime_validation_vix.parquet` — validation-only `^VIX`
  history.
- `cache/research/regime_forecast_validation_latest.json` — full machine
  artifact.
- `cache/research/regime_forecast_walkforward_rows.csv` — per-date row dump
  (one row per evaluation date) for ad-hoc inspection.
- `logs/regime_forecast_validation_latest.txt` — human-readable summary.
- `docs/research/REGIME_FORECASTER_VALIDATION.md` — this document.

## Commands

Recommended (full backfill, 2020-onward):

```bash
SNIPER_ENV_PATH=/home/gem/secure/trading.env \
  .venv/bin/python research/validate_regime_forecaster.py \
    --start 2020-01-01 --end $(date +%F) --backfill
```

Cache-only (no provider calls; uses whatever history is already cached):

```bash
GEM_TRADER_SKIP_DOTENV=true \
  .venv/bin/python research/validate_regime_forecaster.py \
    --start 2020-01-01 --end $(date +%F) --cache-only
```

Smoke (fast, capped rows):

```bash
GEM_TRADER_SKIP_DOTENV=true \
  .venv/bin/python research/validate_regime_forecaster.py \
    --start 2025-04-01 --end 2026-04-24 --cache-only --max-rows 30
```

CLI:

```
--start YYYY-MM-DD          window start (default 2020-01-01)
--end   YYYY-MM-DD          window end   (default today)
--horizons 5,10             forward horizons in trading days
--sample-step 1d|5d|...     evaluate every Nd trading days (default 1d)
--max-rows N                cap on evaluation dates (smoke testing)
--cache-only                never call providers; use cached parquets only
--backfill                  fetch missing history via yfinance to the
                            validation-only parquet store
--no-csv                    skip writing the per-date CSV artifact
--verbose                   DEBUG-level logging
```

## Walk-forward design

For each evaluation date `t`:

1. Truncate every price frame to bars with `date <= t`.
2. Truncate the `^VIX` close series to bars with `date <= t`.
3. Call `core.regime_forecaster.build_forecast(...)` with those truncated
   inputs. The same V1 logic runs that the live forecaster uses today.
4. Capture: dominant regime, confidence, all six regime probabilities, and
   the ranked sector list (top-3 / bottom-3 by 10d RS).
5. Score against forward outcomes (computed from full data but used only as
   labels):
   - SPY 5d / 10d return
   - QQQ 5d / 10d return
   - SPY 5d forward drawdown (min close vs t-close)
   - SPY realized vol 20d before vs 10d after
   - VIX relative change 5d / 10d
   - Top-3 sector basket vs bottom-3 sector basket forward 5d / 10d returns

No future information enters the forecast computation. Forward returns are
used **only** to grade the forecast generated at t.

## Realized-label thresholds

Simple, explicit, documented:

```
bullish_5d_pct                          = +1.0%
bullish_10d_pct                         = +1.5%
riskoff_5d_pct                          = -1.0%
riskoff_realized_vol_expansion_rel_pct  = +30%
stress_5d_drawdown_pct                  = -3.0%
stress_vix_expansion_rel_pct            = +30%
chop_5d_abs_pct                         = 1.0%
chop_vol_change_band_rel_pct            = 20%
```

Coarse class for the confusion matrix:

- **bullish**       — Bull Continuation OR Bull Pullback / Buy-the-Dip
- **chop**          — Chop / Range OR Bear Rally / Unstable Rebound
- **risk-off / stress** — Risk-Off OR Volatility Expansion / Stress

Stress wins ties for the realized class (it is the worst case).

## Metrics

- **Regime return table** — for each predicted dominant regime: n,
  avg 5d/10d SPY return, win rate 5d/10d, avg 5d forward drawdown,
  avg post-event 10d realized vol, avg 5d VIX change.
- **Sector validation** — top-3-minus-bottom-3 basket forward spread at 5d
  and 10d, win rate, stdev.
- **Calibration** — Brier scores for "bullish" and "risk-off" probabilities,
  base-rate comparison, calibration buckets `0–30% / 30–50% / 50–70% / 70%+`
  with `(n, avg predicted p, actual frequency)` per bucket.
- **Confusion matrix** — predicted coarse class vs realized coarse class.
- **False-confidence examples** — top-N high-confidence misses ranked by
  adverse realized move.
- **Verdict** — mechanical mapping from the metrics to one of:
  - `useful as strategic lens`
  - `needs calibration`
  - `descriptive only for now`
  - `insufficient sample (need more history before judging)`

## Current verdict (latest run, 2020-01-01 → 2026-04-24, n=1527)

**Verdict: descriptive only for now.**

Headline numbers from the latest artifact:

```
5d SPY return spread (bullish predictions − risk-off/stress predictions):
  -0.52pp  (bull avg +0.24%, risk-off avg +0.77%)

Top-3 vs bottom-3 sector basket forward spread, 5d:
  +0.02pp  (win rate 50% over n=1522)

Brier (bullish): 0.354 vs base-rate baseline 0.250
  → bullish probabilities are worse than always-predict-base-rate.

Brier (risk-off): 0.231 vs base-rate baseline 0.186
  → risk-off probabilities are worse than always-predict-base-rate.

Bullish calibration buckets:
   0–30%   n= 297  avg_p=14%  actual=69%
  30–50%   n= 351  avg_p=38%  actual=54%
  50–70%   n= 457  avg_p=59%  actual=52%
  70%+     n= 417  avg_p=78%  actual=37%
```

What this is telling us:

- **No directional edge.** Predicted bullish regimes had *lower* forward SPY
  returns than predicted risk-off regimes over 2020–2026. That is the
  signature of a trend-confirming heuristic that fires *after* the move.
- **No sector edge.** The 10d-RS sector ranking does not predict forward
  sector spreads — top-3 minus bottom-3 averages essentially zero.
- **Severe overconfidence on bullish.** The 70%+ bullish bucket realized only
  37% bullish forward outcomes. The 0–30% bucket realized 69%. Probabilities
  are inverted at the extremes — the model gets *less* accurate as it gets
  *more* confident.
- **High-confidence misses cluster on Risk-Off calls right before V-bottoms**
  (Nov 2020, March 2022, Oct 2023, etc.). The forecaster identifies stress
  correctly but cannot anticipate the snap-back.

## Interpretation

V1 is descriptive, not predictive. It accurately summarizes the *current*
market posture (trend score, sector breadth, risk appetite, vol level) but
adds no forward edge versus a base-rate prior on the 5d / 10d horizons
tested.

This is consistent with the design — V1 is an explicit, transparent
heuristic with no learned weights and no forward calibration. The Phase 1
documentation labelled probabilities as heuristic; Phase 2 confirms that
label was warranted.

## Recommended downgrade

Until calibration is fixed, the forecaster's user-facing surface should
treat the output as **descriptive context** rather than a forward forecast:

- Continue surfacing the regime label, sector states, and factor
  contributions — those describe the present and are useful as a research
  lens.
- Consider de-emphasizing or hiding the percentage probabilities in
  user-facing copy until they are recalibrated, or re-label them as
  "heuristic weights" instead of "probabilities".
- Do not let the forecaster influence sleeve gates, paper evidence, Alpha
  Discovery scoring, or Market Posture — which is already the case.

## Calibration guidance (Phase 3 candidate, not done here)

A single, conservative recalibration pass is the natural follow-up:

1. **Trim bullish weights.** The bullish bucket overshoots realized
   frequency at the high end and undershoots at the low end. Shrinking the
   `Bull Continuation` heuristic weight (and/or the `constructive_mass`
   tilt) toward the realized base rate (~52%) would compress predictions
   into a tighter band.
2. **Raise the `confidence='high'` threshold.** The current top-margin rule
   (≥0.20) is too loose given the calibration evidence. Bumping to ≥0.30
   would still occasionally fire `high` but only when the dominant class
   genuinely dominates.
3. **Consider isotonic calibration on the bullish-coarse probability** as a
   one-shot post-hoc fit on a holdout slice (e.g., 2020–2023 fit, 2024+
   evaluate). This is opt-in for Phase 3 and out of scope here.

Phase 2 explicitly does not retune weights inside the validator. The
validator surfaces findings; the calibration pass is a separate change.

## Limitations

- **Heuristic, not learned.** This validation is of the V1 heuristic. A
  learned classifier on the same features may behave very differently and
  is out of scope for V1.
- **2020-onward only.** Pre-COVID regimes (2017–2019 grind, 2018 vol-mage)
  are not in the sample. Backfill there is straightforward (extend
  `--start`) when desired.
- **No regime persistence handling.** The validator scores each evaluation
  date independently. Real strategy use would care about regime stickiness
  — Phase 3 could add a transition-matrix lens.
- **No realized vol expansion confidence interval.** The vol-expansion
  threshold is a fixed +30% relative; a percentile-based threshold may be
  more honest in chronically-low-vol stretches.
- **Coarse confusion classes.** Bull Continuation and Bull Pullback collapse
  into one class; Risk-Off and Vol Expansion / Stress collapse into another.
  This loses some resolution but matches how a discretionary user would
  read the regime label.

## Guardrails

- Research-only. Not trade approval. Not paper evidence.
- No lookahead — forecasts use only data ≤ each evaluation date; VIX
  history truncated identically.
- Forward outcomes are computed from full data but are used **only** to
  grade the forecast.
- No ML and no weight tuning inside the validator.
- No news / social as a core forecasting input.
- No live sleeve, paper, governance, execution, dashboard, Alpha
  Discovery, Market Posture, Daily Entry Validator, or Social Arb changes.
- Validation-only price backfill writes to
  `cache/research/regime_validation_prices/`, never to `cache/prices/`.

## Future work

- Phase 3a: a single conservative calibration pass (trim bullish weights,
  raise high-confidence threshold). Re-run validator. Compare deltas.
- Phase 3b: isotonic / Platt calibration on a holdout split.
- Phase 3c: regime-transition lens (how often does the dominant class
  survive the next 5 / 10 trading days?).
- Phase 3d: a learned baseline (logistic regression over the same features,
  calibrated on a holdout) as a comparison anchor — not a replacement.

# Forecast & Stock-Lens Forward Tracking — Phase 5

Phase 5 closes the research loop:  every canonical forecast and every
single-stock lens call is appended to a JSONL ledger, and a separate
resolver fills in 1d / 5d / 10d / 20d forward outcomes once cached price
bars cover the window.  Reports summarise hit-rates, sector-basket
spread, and false-confidence cases.

This phase is *tracking, attribution, and workflow only*.  No forecast
weights, no lens scoring, no sleeve allocation, and no execution path
were changed.  Outcomes are *recorded*, never fed back into the model.

## Files

```
core/forecast_forward_tracker.py             # core append/resolve/summary API
research/forecast_forward_report.py          # CLI: resolve + summary
research/stock_lens_forward_report.py        # CLI: resolve + summary
scripts/run_research_cycle.sh                # workflow wrapper
docs/research/FORECAST_FORWARD_TRACKING.md   # this file
```

Data artifacts:

```
data/state/regime_forecast_forward_log.jsonl     # append-only forecast ledger
data/state/stock_lens_forward_log.jsonl          # append-only lens ledger
cache/research/forecast_forward_summary_latest.json
cache/research/stock_lens_forward_summary_latest.json
logs/forecast_forward_summary_latest.txt
logs/stock_lens_forward_summary_latest.txt
```

Snapshot logging is wired into the existing runners as a best-effort
side-effect — a logging failure never blocks artifact write.

## Snapshot schema (forecast)

Each forecast snapshot records:

- `snapshot_id` — sha1 of `built_at::anchor_date::version`
- `anchor_date` — last bar reflected in the forecast (SPY frame's last
  trading day; fallback `built_at`).  Forward returns are measured from
  this close.
- `current_regime`, `bias_5d`, `bias_10d`, `confidence`
- top regime probabilities
- `sector_leaders`, `improving`, `weakening`, `defensive`
- `predicted_top_basket` / `predicted_bottom_basket` — top-3 / bottom-3
  sectors (leaders → improving → ranked by `rs_10d_pct`)
- strategy favorability stance
- data quality (`spy_bars`, `sector_frames_available`, missing layers)
- `outcomes` (filled by resolver)
- `status` ∈ {`open`, `matured`}

## Snapshot schema (stock lens)

- `snapshot_id`, `ticker`, `anchor_date`, `built_at`
- `label`, `confidence`, `horizon_view_5d/10d/20d`
- composite / bullish / bearish / entry-quality / risk scores
- `hard_caps_fired`
- per-layer view (market / sector / tech / entry / alpha / posture /
  options / social)
- `outcomes`, `status`

## Resolver

`resolve_forecast_outcomes()` and `resolve_stock_lens_outcomes()` walk
the JSONL ledger, look up each anchor date in cached parquets
(`cache/research/regime_validation_prices/` then `cache/prices/`), and
fill in any forward-return field whose horizon's bars are now
available.  Rows graduate to `matured` only when the *final* horizon's
bar is on disk and enough calendar days have passed (≈ 1.5 × trading
days).

The resolver makes no provider calls — it is safe to run from cron and
will not pollute the live cache.

## Reports

```
.venv/bin/python research/forecast_forward_report.py
.venv/bin/python research/stock_lens_forward_report.py
```

By default each report runs the resolver, then writes the JSON + text
summaries.  Pass `--no-resolve` to summarize what's already on disk.
Pass `--print` to echo the rendered text.

## Workflow

```
./scripts/run_research_cycle.sh nightly      # forecast + alpha + social + resolve + reports
./scripts/run_research_cycle.sh premarket    # forecast + alpha
./scripts/run_research_cycle.sh resolve      # outcomes + reports (no providers)
./scripts/run_research_cycle.sh reports      # summaries only (no resolver)
./scripts/run_research_cycle.sh lens AAPL NVDA XOM
./scripts/run_research_cycle.sh dry-run nightly
```

The dashboard never auto-runs any of these — Phase 4 cache-only loaders
just read the latest artifact.

## Honesty rules

- Hit-rates are computed from recorded forward returns; the forecast
  itself is never tuned on these outcomes.
- High-confidence misses are surfaced in `false_confidence_examples`.
- A 50% hit rate against base-rate is reported as 50%, not 60%.
- Sector basket spread can be negative — the report prints it as-is.

## Dashboard

Risk mode includes a compact `FORWARD TRACKING` panel that reads the
two `*_forward_summary_latest.json` artifacts.  No provider calls; no
resolver runs from the dashboard.

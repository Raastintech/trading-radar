# Research Command Center — Operating Runbook

**Status:** research-only. Nothing on this page approves a trade. The
forecaster, stock-lens, alpha discovery, posture, social-arb, and
forward-tracking outputs are reference material. Paper evidence,
governance, sleeve allocation, and execution are unaffected by this
runbook.

**Dashboard:** read-only. `dashboards/gem_trader_hq.py` consumes the
JSON artifacts under `cache/research/` and never calls a provider. All
provider work happens through `scripts/run_research_cycle.sh`.

---

## After market close (daily)

```bash
./scripts/run_research_cycle.sh nightly
```

What this does:

1. **[PROVIDER]** runs the regime forecast, writes
   `cache/research/regime_forecast_latest.json` and the matching
   `logs/regime_forecast_latest.txt`.
2. **[PROVIDER]** runs the Alpha Discovery board, refreshing
   `cache/research/alpha_discovery_board_latest.json`.
3. **[PROVIDER, cadence-gated]** runs Social Arb on Tue / Thu / Sun.
   On other days the script logs:
   `Social Arb skipped — cadence day not reached. Use --force-social to override.`
4. **[CACHE]** runs the forward-tracking resolver against cached
   parquets, then writes the forecast and stock-lens summary
   artifacts.

After the cycle finishes:

- Open Mode 1 in the dashboard. Confirm the **MARKET FORECAST** strip
  shows the new `built_at` and the same `anchor_date` as the SPY frame.
  An anchor warning means the cache lagged the latest trading day —
  treat the snapshot as approximate.
- Open Mode 2. Spot-check Alpha Discovery / Market Posture context
  against the forecast (e.g., constructive regime + sector leaders that
  are also alpha names = consistent).
- On Social Arb cadence days, glance at the social-arb radar and
  cross-check leads against Alpha + the stock lens.

If you want a Stock Lens for a specific ticker:

```bash
./scripts/run_research_cycle.sh lens AAPL NVDA XOM
```

This calls providers for each ticker, then re-resolves and re-writes
the forward-tracking summaries.

## Premarket (pre-open)

```bash
./scripts/run_research_cycle.sh premarket
```

- **[PROVIDER]** regime forecast + Alpha Discovery board.
- No Social Arb, no resolver.

Then in the dashboard:

1. Mode 1 — confirm the forecast strip and the VIX gates.
2. Mode 2 — open Alpha Discovery; only run a Stock Lens manually for a
   ticker you actually intend to research today.

## During market

The dashboard is reference. There are no live signals here.

- Treat the regime forecast as a probability tilt, not a signal.
- Treat the Stock Lens as research context, not an entry trigger. If a
  ticker is **Watch Reclaim** rather than **Buyable Now**, the lens is
  telling you it is not buyable yet — that statement does not change
  during the session.
- For any discretionary action, you are still required to run the
  Daily Entry Validator and chart review. Those are unchanged by Phase
  4 / 5 / 6.
- Sleeves, paper evidence, governance, and execution are not driven by
  the research center. Do not move them based on a forecast change.

## Weekly (Saturday or Sunday)

```bash
./scripts/run_research_cycle.sh resolve
./scripts/run_research_cycle.sh reports
```

The first command re-resolves any matured forecasts/lens calls and
rewrites the summaries. The second is a no-resolver re-render — useful
when you only want to refresh the text reports.

Then read:

- `logs/forecast_forward_summary_latest.txt`
- `logs/stock_lens_forward_summary_latest.txt`

Look for:

- **Hit rate vs base-rate.** A 50% hit rate is not a signal; calibrate
  your trust in the forecast accordingly.
- **By-confidence bucket.** If "high" confidence is not at least
  meaningfully better than "low/med", treat all confidence labels with
  skepticism.
- **By-regime breakdown.** Some regimes might be useful even if the
  aggregate is not.
- **Sector basket spread.** If `leaders_beat_laggards_5d_rate` < 50%,
  sector calls are not yet edge.
- **False-confidence examples.** Read each one. If the system has been
  confidently wrong in a clearly identifiable pattern, that's the most
  important learning of the week.

The summaries print an honesty note — keep it. The forecast has not
been tuned on these outcomes, so the numbers are honest.

## Honesty rules

- The dashboard reads cache only.
- The forecast labels are probabilistic, not signals.
- Stock-lens labels can be correct directionally and still produce
  losing setups (entry quality matters more on short horizons).
- Forward tracking measures *what happened* after a snapshot, not
  *why*. Do not over-interpret tiny samples.
- If you find yourself wanting to re-tune the forecast on the back of
  forward-tracking outcomes — stop. That is its own project (validator
  Phase 2-style work), not a runbook step.

## Cadence override

Default Social Arb cadence is `Tue, Thu, Sun` (`RESEARCH_SOCIAL_DAYS=2,4,7`).
Override via env or CLI:

```bash
# one-off override
./scripts/run_research_cycle.sh nightly --force-social

# permanent change for this shell
export RESEARCH_SOCIAL_DAYS=2,4   # Tue + Thu only
```

## Troubleshooting

| symptom | likely cause | fix |
|---|---|---|
| `anchor warning` on the dashboard forecast strip | forecast ran on stale frames | re-run forecast after the EOD bars are in the cache |
| `Stock Lens missing for X` in Mode 2 | no cached lens for that ticker | `./scripts/run_research_cycle.sh lens X` |
| `Forward tracking summary missing` in Risk mode | resolver hasn't run | `./scripts/run_research_cycle.sh resolve` |
| dashboard says forecast is `STALE` | last cycle didn't run | run `nightly` or `forecast` |
| nightly skipped Social Arb | not a cadence day | `--force-social` if you intended to run it |

## File map

```
research/regime_forecast.py             — forecast runner (provider)
research/alpha_discovery_board.py       — alpha runner (provider)
research/social_arb_radar.py            — social arb runner (provider, cadence)
research/forecast_forward_report.py     — resolver + summary (cache)
research/stock_lens_forward_report.py   — resolver + summary (cache)
core/forecast_forward_tracker.py        — append/resolve/summary API
scripts/run_research_cycle.sh           — workflow wrapper
cache/research/*_latest.json            — dashboard inputs
data/state/*_forward_log.jsonl          — append-only ledgers
docs/research/REGIME_FORECASTER_V1.md
docs/research/STOCK_RESEARCH_LENS_V1.md
docs/research/FORECAST_FORWARD_TRACKING.md
docs/research/RESEARCH_COMMAND_CENTER_RUNBOOK.md   — this file
```

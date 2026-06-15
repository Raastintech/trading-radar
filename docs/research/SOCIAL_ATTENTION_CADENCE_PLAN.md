# Social Attention — Cadence Plan (Phase 1G.15B)

How to run the Social Attention Radar on a daily cadence so it accumulates
forward-validation history — **without Reddit**, and **not auto-enabled** until
the operator approves. Machine-readable copy:
`cache/research/social_attention_cadence_plan_latest.json`.

## Plan

1. **Collection** — once per market day after close (~16:30 ET, Mon–Fri):
   ```bash
   ./scripts/run_research_cycle.sh social-attention --profile safe-nightly
   ```
   `safe-nightly` = StockTwits-first over the capped watch universe, manual JSONL
   on, Google Trends low-cap, **Reddit DISABLED**.

2. **Forward validation** — immediately after collection:
   ```bash
   ./scripts/run_research_cycle.sh social-attention-forward
   ```

Each run logs: source counts, leads / social_led / news_led / stealth / early /
crowded, per-source `source_health` (incl. 429 / RATE_LIMITED), and `history_days`.

## Estimated calls per run

| Provider | Calls | Notes |
|----------|-------|-------|
| StockTwits | ~75 | 1 per watch-universe symbol; public API, no key |
| Google Trends | ~3 | 12 terms low-cap, 5/chunk; unofficial, often 429 |
| FMP / Alpaca | 0 | price overlays read cached parquets only |
| Anthropic | 0 | — |

No metered FMP/Alpaca budget impact. StockTwits and Google Trends are free /
no-key and may rate-limit; both degrade gracefully (the run never hard-fails).

## Enable (operator approval required — DISABLED by default)

Add to `crontab -e`:
```cron
30 16 * * 1-5 cd /home/gem/trading-production && SNIPER_ENV_PATH=/home/gem/secure/trading.env ./scripts/run_research_cycle.sh social-attention --profile safe-nightly >> logs/social_attention_cron.log 2>&1
35 16 * * 1-5 cd /home/gem/trading-production && ./scripts/run_research_cycle.sh social-attention-forward >> logs/social_attention_cron.log 2>&1
```
(Adjust the hour to your box timezone so it fires after the 16:00 ET close.)

## Disable

```bash
crontab -l | grep -v social-attention | crontab -
```
or `crontab -e` and delete the two lines.

## Manual / dry-run

```bash
# full manual run (real artifacts + history append)
SNIPER_ENV_PATH=/home/gem/secure/trading.env ./scripts/run_research_cycle.sh social-attention --profile safe-nightly && ./scripts/run_research_cycle.sh social-attention-forward

# dry-run (safe-nightly behavior, writes nothing)
./scripts/run_research_cycle.sh social-attention --profile dry-run
```

## Why not a systemd timer yet?

The existing research timers are operator-owned and budgeted around FMP/Alpaca.
This radar touches only free/no-key social endpoints and is **not** wired into
`cmd_nightly`. Keeping it on an explicit, operator-enabled cron (or manual run)
makes the "not auto-enabled until approved" guardrail self-evident. Promote to a
systemd timer later if the forward gate proves an edge.

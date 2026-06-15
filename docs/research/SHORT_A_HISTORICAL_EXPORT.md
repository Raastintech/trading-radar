# SHORT_A Historical Export — Runbook

Last updated: 2026-05-04 (Phase 10B).

## Why this is separate

`research/sleeves/export_backtest_trades.py` runs cache-only and writes
`SHORT_A.csv` from live `paper_signals` + `paper_signal_outcomes` only — that
sample is currently very thin (n=4 closed). For real evidence on Sleeve A you
need the historical path through `research/sleeves/short_backtester.py`,
which is heavyweight: it pulls fundamentals from FMP, daily bars from Alpaca,
and writes a per-trade table after applying the configured friction stack.

## Requirements

| Env var | Source | Purpose |
|---|---|---|
| `FMP_API_KEY` | `/home/gem/secure/trading.env` | Earnings calendar + fundamentals |
| `ALPACA_API_KEY` | same | Daily price bars |
| `ALPACA_SECRET_KEY` | same | Daily price bars |
| `TRADIER_API_KEY` *(optional)* | same | Fallback options/quote data |
| `POLYGON_API_KEY` *(optional)* | same | Fallback price data |

The runner `research/sleeves/export_short_a_history.py` checks
`FMP_API_KEY` / `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` and refuses to start if
any are missing. It does not read `secure/trading.env` itself — you source it
in your shell first.

## Exact command

```bash
cd /home/gem/trading-production
set -a && source /home/gem/secure/trading.env && set +a
.venv/bin/python research/sleeves/export_short_a_history.py
```

Defaults (chosen to match the SHORT_DOCTRINE friction stack):

| Flag | Default | Notes |
|---|---:|---|
| `--lookback` | 504 | ~2 trading years; matches `short_backtester` default |
| `--universe-size` | 200 | top-N candidates from discovery screen |
| `--score-threshold` | 60.0 | matches `SHORT_SCORE_THRESHOLD` |
| `--min-rr` | 2.5 | doctrine R:R floor |
| `--borrow-fee-annual-pct` | 1.0 | conservative borrow assumption |
| `--slippage-bps` | 5.0 | each side (10 bps RT) |
| `--spread-bps` | 5.0 | each side (10 bps RT) |
| `--halt-gap-penalty-pct` | 0.5 | applies only to squeeze-like exits |

Override any of those flags from the CLI if you want to stress-test wider
borrow / spread / slippage assumptions before promotion.

## What it does

1. Imports `research/sleeves/short_backtester.py`.
2. Calls `run_backtest(...)` with the friction stack above.
3. Reads `result["friction_trades"]` (or `sized_trades` as fallback).
4. Maps each trade to the Phase 9B standard schema:
   `strategy, baseline_tag, ticker, side, entry_date, exit_date, horizon,
   entry_price, exit_price, raw_return_pct, adjusted_return_pct, stop_hit,
   target_hit, sector, source_backtest, friction_model, notes`.
5. Merges with the existing `research/sleeves/trades/SHORT_A.csv` so the
   live-paper rows produced by `export_backtest_trades.py` are preserved.
6. Writes the merged file. Historical rows are tagged
   `baseline_tag="short_history_v1"`; live rows keep `baseline_tag="live_paper_db"`.

## After running

```bash
.venv/bin/python research/strategy_evidence_audit.py
```

This will:
- Pick up the bigger SHORT_A.csv automatically (CSV is preferred over the live DB).
- Recompute bootstrap CI, walk-forward, random-entry control, friction sensitivity.
- Update `docs/scorecards/evidence_rigor_report.md` and the SHORT scorecard rigor strip.

## Expected output sizes

`short_backtester` typically produces 50–250 trades over a 2-year lookback on
a 200-name discovery universe, depending on macro/regime. Anything below ~30
closed trades will keep the verdict at `PROMISING_BUT_THIN` — the same gate
the audit applies to all sleeves.

## What this is *not* doing

- Not changing strategy thresholds, scanner gates, or paper governance.
- Not promoting SHORT_A toward capital. The verdict is decided by the audit
  pipeline.
- Not silently inventing data. If `short_backtester` returns zero trades for
  a given configuration, the merged CSV will only contain the existing live
  paper rows.

## Cross-references

- `research/sleeves/short_backtester.py` — the heavyweight path
- `research/sleeves/export_backtest_trades.py` — the cache-only default path
- `research/strategy_evidence_audit.py` — consumes the merged CSV
- `docs/scorecards/short_sleeve_scorecard.md` — current SHORT_A status
- `docs/strategy/SHORT_DOCTRINE.md` — mandate / friction stack rationale

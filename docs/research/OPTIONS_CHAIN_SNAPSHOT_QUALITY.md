# Options Chain Snapshot Quality (Phase 1J.1)

Generated: 2026-06-30T19:46:17.241826+00:00

Status: **DATA_COLLECTION_ONLY** — quality audit of persisted snapshots; no strategy, no signals.

Snapshot days retained: 13 (2026-06-12, 2026-06-15, 2026-06-16, 2026-06-17, 2026-06-18, 2026-06-19, 2026-06-22, 2026-06-23, 2026-06-24, 2026-06-25, 2026-06-26, 2026-06-29, 2026-06-30). Symbols: 24. Contracts (latest day): 9040.

| Symbol | Days | Contracts | Expirations | Bid/Ask | IV | Greeks | OI | Med Spread | Stale | Usable (per-day quality) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| AAPL | 13 | 288 | 4 | 0.9549 | 1.0 | 0.9896 | 1.0 | 0.0628 | 0.0312 | YES |
| AMZN | 13 | 224 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0625 | 0.0 | YES |
| ARW | 1 | 34 | 1 | 0.8235 | 1.0 | 1.0 | 1.0 | 0.1524 | 0.1176 | NO: contracts 34 < 40; bid/ask coverage 0.8235 < 0.9; median spread 0.1524 > 0.12 |
| AVGO | 1 | 330 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0786 | 0.0 | YES |
| DIA | 13 | 594 | 4 | 0.963 | 1.0 | 0.9899 | 1.0 | 0.0473 | 0.037 | YES |
| GOOG | 13 | 336 | 4 | 0.994 | 1.0 | 1.0 | 1.0 | 0.0735 | 0.003 | YES |
| GOOGL | 13 | 320 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0644 | 0.0 | YES |
| HUM | 13 | 328 | 4 | 0.9329 | 1.0 | 1.0 | 1.0 | 0.1511 | 0.0671 | NO: median spread 0.1511 > 0.12 |
| IWM | 13 | 516 | 4 | 1.0 | 1.0 | 0.9981 | 1.0 | 0.0096 | 0.0 | YES |
| LSCC | 13 | 34 | 1 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0665 | 0.0 | NO: contracts 34 < 40 |
| META | 13 | 522 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0605 | 0.0 | YES |
| MSFT | 13 | 328 | 4 | 0.9909 | 1.0 | 1.0 | 1.0 | 0.0607 | 0.0061 | YES |
| NUE | 12 | 36 | 1 | 0.9722 | 1.0 | 1.0 | 1.0 | 0.1091 | 0.0278 | NO: contracts 36 < 40 |
| NVDA | 12 | 200 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0289 | 0.0 | YES |
| NXPI | 13 | 36 | 1 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0509 | 0.0 | NO: contracts 36 < 40 |
| OSCR | 12 | 200 | 4 | 0.985 | 1.0 | 1.0 | 1.0 | 0.3016 | 0.015 | NO: median spread 0.3016 > 0.12 |
| QQQ | 13 | 1228 | 4 | 1.0 | 1.0 | 0.9984 | 1.0 | 0.0149 | 0.0 | YES |
| SBAC | 11 | 72 | 2 | 0.6944 | 1.0 | 0.9861 | 1.0 | 0.1788 | 0.2917 | NO: bid/ask coverage 0.6944 < 0.9; median spread 0.1788 > 0.12 |
| SMH | 13 | 722 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0421 | 0.0 | YES |
| SPOT | 2 | 366 | 4 | 0.9809 | 1.0 | 1.0 | 1.0 | 0.1238 | 0.0164 | NO: median spread 0.1238 > 0.12 |
| SPY | 13 | 1600 | 4 | 1.0 | 0.9856 | 0.9819 | 1.0 | 0.0162 | 0.0 | YES |
| STLD | 13 | 34 | 1 | 1.0 | 1.0 | 1.0 | 1.0 | 0.081 | 0.0 | NO: contracts 34 < 40 |
| STM | 1 | 214 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.1436 | 0.0 | NO: median spread 0.1436 > 0.12 |
| XLK | 13 | 478 | 4 | 0.9686 | 1.0 | 0.9937 | 1.0 | 0.1376 | 0.0272 | NO: median spread 0.1376 > 0.12 |

## Coverage by provider

```json
{
  "alpaca+tradier": {
    "iv_coverage": 0.9996,
    "greeks_coverage": 0.9997,
    "oi_coverage": 1.0,
    "bid_ask_coverage": 0.9533
  },
  "alpaca": {
    "iv_coverage": 1.0,
    "greeks_coverage": 1.0,
    "oi_coverage": 1.0,
    "bid_ask_coverage": 0.9943
  },
  "tradier": {
    "iv_coverage": 0.9994,
    "greeks_coverage": 0.9954,
    "oi_coverage": 1.0,
    "bid_ask_coverage": 0.9724
  }
}
```

13 snapshot day(s) retained. Usability verdicts describe per-day data quality only; backtest feasibility additionally requires the history gates in OPTIONS_CHAIN_COLLECTION_CADENCE.md.


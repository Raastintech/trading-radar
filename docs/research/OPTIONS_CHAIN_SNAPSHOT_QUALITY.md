# Options Chain Snapshot Quality (Phase 1J.1)

Generated: 2026-07-06T19:46:10.002928+00:00

Status: **DATA_COLLECTION_ONLY** — quality audit of persisted snapshots; no strategy, no signals.

Snapshot days retained: 17 (2026-06-12, 2026-06-15, 2026-06-16, 2026-06-17, 2026-06-18, 2026-06-19, 2026-06-22, 2026-06-23, 2026-06-24, 2026-06-25, 2026-06-26, 2026-06-29, 2026-06-30, 2026-07-01, 2026-07-02, 2026-07-03, 2026-07-06). Symbols: 25. Contracts (latest day): 8896.

| Symbol | Days | Contracts | Expirations | Bid/Ask | IV | Greeks | OI | Med Spread | Stale | Usable (per-day quality) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| AAPL | 17 | 296 | 4 | 0.9966 | 1.0 | 0.9966 | 1.0 | 0.0597 | 0.0 | YES |
| AJG | 3 | 34 | 1 | 0.8235 | 1.0 | 1.0 | 1.0 | 0.0966 | 0.1176 | NO: contracts 34 < 40; bid/ask coverage 0.8235 < 0.9 |
| AMZN | 17 | 240 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0573 | 0.0 | YES |
| ARW | 1 | 34 | 1 | 0.8235 | 1.0 | 1.0 | 1.0 | 0.1524 | 0.1176 | NO: contracts 34 < 40; bid/ask coverage 0.8235 < 0.9; median spread 0.1524 > 0.12 |
| AVGO | 1 | 330 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0786 | 0.0 | YES |
| DIA | 17 | 600 | 4 | 0.96 | 1.0 | 0.9883 | 1.0 | 0.0438 | 0.04 | YES |
| GOOG | 17 | 344 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0711 | 0.0 | YES |
| GOOGL | 17 | 344 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0628 | 0.0 | YES |
| HUM | 17 | 352 | 4 | 0.9432 | 1.0 | 1.0 | 1.0 | 0.1882 | 0.054 | NO: median spread 0.1882 > 0.12 |
| IWM | 17 | 530 | 4 | 0.9906 | 1.0 | 0.9943 | 1.0 | 0.0189 | 0.0094 | YES |
| LSCC | 14 | 34 | 1 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0724 | 0.0 | NO: contracts 34 < 40 |
| META | 17 | 560 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0652 | 0.0 | YES |
| MSFT | 17 | 358 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.05 | 0.0 | YES |
| NUE | 16 | 34 | 1 | 0.9706 | 1.0 | 1.0 | 1.0 | 0.1046 | 0.0294 | NO: contracts 34 < 40 |
| NVDA | 12 | 200 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0289 | 0.0 | YES |
| NXPI | 17 | 34 | 1 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0544 | 0.0 | NO: contracts 34 < 40 |
| OSCR | 16 | 194 | 4 | 0.9845 | 1.0 | 1.0 | 1.0 | 0.2367 | 0.0155 | NO: median spread 0.2367 > 0.12 |
| QQQ | 17 | 1092 | 4 | 1.0 | 1.0 | 0.9991 | 1.0 | 0.0134 | 0.0 | YES |
| SBAC | 11 | 72 | 2 | 0.6944 | 1.0 | 0.9861 | 1.0 | 0.1788 | 0.2917 | NO: bid/ask coverage 0.6944 < 0.9; median spread 0.1788 > 0.12 |
| SMH | 17 | 658 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0381 | 0.0 | YES |
| SPOT | 6 | 400 | 4 | 0.965 | 1.0 | 1.0 | 1.0 | 0.1081 | 0.035 | YES |
| SPY | 17 | 1458 | 4 | 1.0 | 0.989 | 0.9712 | 1.0 | 0.0156 | 0.0 | YES |
| STLD | 17 | 36 | 1 | 0.9444 | 1.0 | 1.0 | 1.0 | 0.0876 | 0.0556 | NO: contracts 36 < 40 |
| STM | 5 | 202 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.1522 | 0.0 | NO: median spread 0.1522 > 0.12 |
| XLK | 17 | 460 | 4 | 0.9761 | 1.0 | 1.0 | 1.0 | 0.1538 | 0.0239 | NO: median spread 0.1538 > 0.12 |

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
    "greeks_coverage": 0.996,
    "oi_coverage": 1.0,
    "bid_ask_coverage": 0.9734
  }
}
```

17 snapshot day(s) retained. Usability verdicts describe per-day data quality only; backtest feasibility additionally requires the history gates in OPTIONS_CHAIN_COLLECTION_CADENCE.md.


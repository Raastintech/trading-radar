# Options Chain Snapshot Quality (Phase 1J.1)

Generated: 2026-07-03T19:46:07.557097+00:00

Status: **DATA_COLLECTION_ONLY** — quality audit of persisted snapshots; no strategy, no signals.

Snapshot days retained: 16 (2026-06-12, 2026-06-15, 2026-06-16, 2026-06-17, 2026-06-18, 2026-06-19, 2026-06-22, 2026-06-23, 2026-06-24, 2026-06-25, 2026-06-26, 2026-06-29, 2026-06-30, 2026-07-01, 2026-07-02, 2026-07-03). Symbols: 25. Contracts (latest day): 7874.

| Symbol | Days | Contracts | Expirations | Bid/Ask | IV | Greeks | OI | Med Spread | Stale | Usable (per-day quality) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| AAPL | 16 | 274 | 4 | 0.9818 | 1.0 | 1.0 | 1.0 | 0.1053 | 0.0109 | YES |
| AJG | 2 | 34 | 1 | 0.8235 | 1.0 | 1.0 | 1.0 | 0.11 | 0.1765 | NO: contracts 34 < 40; bid/ask coverage 0.8235 < 0.9 |
| AMZN | 16 | 232 | 4 | 0.9741 | 1.0 | 1.0 | 1.0 | 0.1057 | 0.0129 | YES |
| ARW | 1 | 34 | 1 | 0.8235 | 1.0 | 1.0 | 1.0 | 0.1524 | 0.1176 | NO: contracts 34 < 40; bid/ask coverage 0.8235 < 0.9; median spread 0.1524 > 0.12 |
| AVGO | 1 | 330 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0786 | 0.0 | YES |
| DIA | 16 | 532 | 4 | 0.9699 | 1.0 | 1.0 | 1.0 | 0.05 | 0.0263 | YES |
| GOOG | 16 | 316 | 4 | 0.9589 | 1.0 | 1.0 | 1.0 | 0.0995 | 0.0285 | YES |
| GOOGL | 16 | 316 | 4 | 0.9968 | 1.0 | 1.0 | 1.0 | 0.0989 | 0.0032 | YES |
| HUM | 16 | 322 | 4 | 0.9565 | 1.0 | 1.0 | 1.0 | 0.2269 | 0.0435 | NO: median spread 0.2269 > 0.12 |
| IWM | 16 | 446 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0109 | 0.0 | YES |
| LSCC | 14 | 34 | 1 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0724 | 0.0 | NO: contracts 34 < 40 |
| META | 16 | 484 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0862 | 0.0 | YES |
| MSFT | 16 | 328 | 4 | 0.997 | 1.0 | 1.0 | 1.0 | 0.0809 | 0.0 | YES |
| NUE | 15 | 34 | 1 | 0.9706 | 1.0 | 1.0 | 1.0 | 0.1379 | 0.0294 | NO: contracts 34 < 40; median spread 0.1379 > 0.12 |
| NVDA | 12 | 200 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0289 | 0.0 | YES |
| NXPI | 16 | 34 | 1 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0908 | 0.0 | NO: contracts 34 < 40 |
| OSCR | 15 | 196 | 4 | 0.9898 | 1.0 | 1.0 | 1.0 | 0.31 | 0.0102 | NO: median spread 0.31 > 0.12 |
| QQQ | 16 | 868 | 4 | 0.9896 | 1.0 | 1.0 | 1.0 | 0.0231 | 0.0092 | YES |
| SBAC | 11 | 72 | 2 | 0.6944 | 1.0 | 0.9861 | 1.0 | 0.1788 | 0.2917 | NO: bid/ask coverage 0.6944 < 0.9; median spread 0.1788 > 0.12 |
| SMH | 16 | 542 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0892 | 0.0 | YES |
| SPOT | 5 | 348 | 4 | 0.9799 | 1.0 | 1.0 | 1.0 | 0.1699 | 0.0172 | NO: median spread 0.1699 > 0.12 |
| SPY | 16 | 1238 | 4 | 1.0 | 0.9782 | 0.9798 | 1.0 | 0.0215 | 0.0 | YES |
| STLD | 16 | 36 | 1 | 0.9444 | 1.0 | 1.0 | 1.0 | 0.1199 | 0.0556 | NO: contracts 36 < 40 |
| STM | 4 | 194 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.2251 | 0.0 | NO: median spread 0.2251 > 0.12 |
| XLK | 16 | 430 | 4 | 0.9651 | 1.0 | 1.0 | 1.0 | 0.2308 | 0.0302 | NO: median spread 0.2308 > 0.12 |

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
    "greeks_coverage": 0.9959,
    "oi_coverage": 1.0,
    "bid_ask_coverage": 0.9732
  }
}
```

16 snapshot day(s) retained. Usability verdicts describe per-day data quality only; backtest feasibility additionally requires the history gates in OPTIONS_CHAIN_COLLECTION_CADENCE.md.


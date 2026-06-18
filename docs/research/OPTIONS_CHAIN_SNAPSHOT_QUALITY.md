# Options Chain Snapshot Quality (Phase 1J.1)

Generated: 2026-06-17T19:46:11.017752+00:00

Status: **DATA_COLLECTION_ONLY** — quality audit of persisted snapshots; no strategy, no signals.

Snapshot days retained: 4 (2026-06-12, 2026-06-15, 2026-06-16, 2026-06-17). Symbols: 22. Contracts (latest day): 8676.

| Symbol | Days | Contracts | Expirations | Bid/Ask | IV | Greeks | OI | Med Spread | Stale | Usable (per-day quality) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| AAPL | 4 | 284 | 4 | 0.9789 | 1.0 | 1.0 | 1.0 | 0.0525 | 0.0211 | YES |
| AMZN | 4 | 232 | 4 | 0.9957 | 1.0 | 1.0 | 1.0 | 0.0474 | 0.0043 | YES |
| ARW | 1 | 34 | 1 | 0.8235 | 1.0 | 1.0 | 1.0 | 0.1524 | 0.1176 | NO: contracts 34 < 40; bid/ask coverage 0.8235 < 0.9; median spread 0.1524 > 0.12 |
| AVGO | 1 | 330 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0786 | 0.0 | YES |
| DIA | 4 | 472 | 4 | 0.9831 | 0.9894 | 1.0 | 1.0 | 0.1078 | 0.0148 | YES |
| GOOG | 4 | 352 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0772 | 0.0 | YES |
| GOOGL | 4 | 360 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0727 | 0.0 | YES |
| HUM | 4 | 336 | 4 | 0.9405 | 1.0 | 1.0 | 1.0 | 0.171 | 0.0595 | NO: median spread 0.171 > 0.12 |
| IWM | 4 | 586 | 4 | 0.9966 | 1.0 | 0.9966 | 1.0 | 0.0195 | 0.0034 | YES |
| LSCC | 4 | 68 | 2 | 1.0 | 1.0 | 1.0 | 1.0 | 0.1387 | 0.0 | NO: median spread 0.1387 > 0.12 |
| META | 4 | 566 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0512 | 0.0 | YES |
| MSFT | 4 | 356 | 4 | 0.9944 | 1.0 | 1.0 | 1.0 | 0.0515 | 0.0 | YES |
| NUE | 3 | 32 | 1 | 0.875 | 1.0 | 0.9375 | 1.0 | 0.144 | 0.0938 | NO: contracts 32 < 40; bid/ask coverage 0.875 < 0.9; median spread 0.144 > 0.12 |
| NVDA | 4 | 200 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0362 | 0.0 | YES |
| NXPI | 4 | 36 | 1 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0777 | 0.0 | NO: contracts 36 < 40 |
| OSCR | 3 | 182 | 4 | 0.9725 | 1.0 | 1.0 | 1.0 | 0.279 | 0.0275 | NO: median spread 0.279 > 0.12 |
| QQQ | 4 | 1370 | 4 | 0.9985 | 1.0 | 0.9971 | 1.0 | 0.0218 | 0.0015 | YES |
| SBAC | 4 | 36 | 1 | 0.6944 | 1.0 | 1.0 | 1.0 | 0.1807 | 0.3056 | NO: contracts 36 < 40; bid/ask coverage 0.6944 < 0.9; median spread 0.1807 > 0.12 |
| SMH | 4 | 664 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0471 | 0.0 | YES |
| SPY | 4 | 1660 | 4 | 1.0 | 0.9946 | 0.9753 | 1.0 | 0.0224 | 0.0 | YES |
| STLD | 4 | 68 | 2 | 0.9706 | 1.0 | 1.0 | 1.0 | 0.0928 | 0.0147 | YES |
| XLK | 4 | 452 | 4 | 0.9381 | 1.0 | 1.0 | 1.0 | 0.2059 | 0.0575 | NO: median spread 0.2059 > 0.12 |

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
    "iv_coverage": 0.9987,
    "greeks_coverage": 0.9959,
    "oi_coverage": 1.0,
    "bid_ask_coverage": 0.9709
  }
}
```

4 snapshot day(s) retained. Usability verdicts describe per-day data quality only; backtest feasibility additionally requires the history gates in OPTIONS_CHAIN_COLLECTION_CADENCE.md.


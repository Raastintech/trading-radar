# Options Chain Snapshot Quality (Phase 1J.1)

Generated: 2026-06-18T19:46:13.447786+00:00

Status: **DATA_COLLECTION_ONLY** — quality audit of persisted snapshots; no strategy, no signals.

Snapshot days retained: 5 (2026-06-12, 2026-06-15, 2026-06-16, 2026-06-17, 2026-06-18). Symbols: 22. Contracts (latest day): 8912.

| Symbol | Days | Contracts | Expirations | Bid/Ask | IV | Greeks | OI | Med Spread | Stale | Usable (per-day quality) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| AAPL | 5 | 284 | 4 | 0.9859 | 1.0 | 0.993 | 1.0 | 0.0445 | 0.0141 | YES |
| AMZN | 5 | 232 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0493 | 0.0 | YES |
| ARW | 1 | 34 | 1 | 0.8235 | 1.0 | 1.0 | 1.0 | 0.1524 | 0.1176 | NO: contracts 34 < 40; bid/ask coverage 0.8235 < 0.9; median spread 0.1524 > 0.12 |
| AVGO | 1 | 330 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0786 | 0.0 | YES |
| DIA | 5 | 524 | 4 | 0.9599 | 1.0 | 0.9866 | 1.0 | 0.0524 | 0.0363 | YES |
| GOOG | 5 | 352 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0692 | 0.0 | YES |
| GOOGL | 5 | 360 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0593 | 0.0 | YES |
| HUM | 5 | 336 | 4 | 0.9821 | 1.0 | 1.0 | 1.0 | 0.1899 | 0.0149 | NO: median spread 0.1899 > 0.12 |
| IWM | 5 | 586 | 4 | 0.9966 | 1.0 | 0.9966 | 1.0 | 0.0078 | 0.0034 | YES |
| LSCC | 5 | 68 | 2 | 1.0 | 1.0 | 1.0 | 1.0 | 0.1028 | 0.0 | YES |
| META | 5 | 566 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0523 | 0.0 | YES |
| MSFT | 5 | 356 | 4 | 0.9972 | 1.0 | 1.0 | 1.0 | 0.0444 | 0.0028 | YES |
| NUE | 4 | 66 | 2 | 0.9091 | 1.0 | 1.0 | 1.0 | 0.097 | 0.0909 | YES |
| NVDA | 5 | 200 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0309 | 0.0 | YES |
| NXPI | 5 | 72 | 2 | 0.9722 | 1.0 | 1.0 | 1.0 | 0.0813 | 0.0278 | YES |
| OSCR | 4 | 182 | 4 | 0.978 | 1.0 | 1.0 | 1.0 | 0.22 | 0.022 | NO: median spread 0.22 > 0.12 |
| QQQ | 5 | 1362 | 4 | 1.0 | 1.0 | 0.9993 | 1.0 | 0.016 | 0.0 | YES |
| SBAC | 5 | 72 | 2 | 0.75 | 1.0 | 1.0 | 1.0 | 0.1885 | 0.25 | NO: bid/ask coverage 0.75 < 0.9; median spread 0.1885 > 0.12 |
| SMH | 5 | 690 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0412 | 0.0 | YES |
| SPY | 5 | 1664 | 4 | 1.0 | 0.9964 | 0.9754 | 1.0 | 0.0165 | 0.0 | YES |
| STLD | 5 | 68 | 2 | 0.9706 | 1.0 | 1.0 | 1.0 | 0.1137 | 0.0294 | YES |
| XLK | 5 | 508 | 4 | 0.9744 | 1.0 | 1.0 | 1.0 | 0.1288 | 0.0236 | NO: median spread 0.1288 > 0.12 |

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
    "iv_coverage": 0.999,
    "greeks_coverage": 0.9963,
    "oi_coverage": 1.0,
    "bid_ask_coverage": 0.9716
  }
}
```

5 snapshot day(s) retained. Usability verdicts describe per-day data quality only; backtest feasibility additionally requires the history gates in OPTIONS_CHAIN_COLLECTION_CADENCE.md.


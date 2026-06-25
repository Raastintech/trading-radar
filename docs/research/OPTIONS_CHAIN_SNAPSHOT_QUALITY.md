# Options Chain Snapshot Quality (Phase 1J.1)

Generated: 2026-06-24T19:46:12.815656+00:00

Status: **DATA_COLLECTION_ONLY** — quality audit of persisted snapshots; no strategy, no signals.

Snapshot days retained: 9 (2026-06-12, 2026-06-15, 2026-06-16, 2026-06-17, 2026-06-18, 2026-06-19, 2026-06-22, 2026-06-23, 2026-06-24). Symbols: 22. Contracts (latest day): 9170.

| Symbol | Days | Contracts | Expirations | Bid/Ask | IV | Greeks | OI | Med Spread | Stale | Usable (per-day quality) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| AAPL | 9 | 308 | 4 | 0.974 | 1.0 | 0.9675 | 1.0 | 0.0454 | 0.026 | YES |
| AMZN | 9 | 252 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0528 | 0.0 | YES |
| ARW | 1 | 34 | 1 | 0.8235 | 1.0 | 1.0 | 1.0 | 0.1524 | 0.1176 | NO: contracts 34 < 40; bid/ask coverage 0.8235 < 0.9; median spread 0.1524 > 0.12 |
| AVGO | 1 | 330 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0786 | 0.0 | YES |
| DIA | 9 | 606 | 4 | 0.967 | 1.0 | 0.9835 | 1.0 | 0.0379 | 0.0314 | YES |
| GOOG | 9 | 356 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0767 | 0.0 | YES |
| GOOGL | 9 | 372 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0704 | 0.0 | YES |
| HUM | 9 | 350 | 4 | 0.9686 | 1.0 | 1.0 | 1.0 | 0.1766 | 0.0314 | NO: median spread 0.1766 > 0.12 |
| IWM | 9 | 580 | 4 | 0.9897 | 1.0 | 0.9897 | 1.0 | 0.0085 | 0.0086 | YES |
| LSCC | 9 | 68 | 2 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0899 | 0.0 | YES |
| META | 9 | 574 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0503 | 0.0 | YES |
| MSFT | 9 | 368 | 4 | 0.9918 | 1.0 | 1.0 | 1.0 | 0.0505 | 0.0027 | YES |
| NUE | 8 | 72 | 2 | 0.9028 | 1.0 | 1.0 | 1.0 | 0.1272 | 0.0972 | NO: median spread 0.1272 > 0.12 |
| NVDA | 9 | 220 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.027 | 0.0 | YES |
| NXPI | 9 | 72 | 2 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0889 | 0.0 | YES |
| OSCR | 8 | 202 | 4 | 0.9802 | 1.0 | 1.0 | 1.0 | 0.1792 | 0.0198 | NO: median spread 0.1792 > 0.12 |
| QQQ | 9 | 1362 | 4 | 0.9971 | 1.0 | 0.9949 | 1.0 | 0.0186 | 0.0029 | YES |
| SBAC | 9 | 72 | 2 | 0.7222 | 1.0 | 0.9861 | 1.0 | 0.1921 | 0.25 | NO: bid/ask coverage 0.7222 < 0.9; median spread 0.1921 > 0.12 |
| SMH | 9 | 702 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0453 | 0.0 | YES |
| SPY | 9 | 1664 | 4 | 0.9922 | 0.9994 | 0.9663 | 1.0 | 0.0152 | 0.0066 | YES |
| STLD | 9 | 68 | 2 | 0.9853 | 1.0 | 1.0 | 1.0 | 0.0935 | 0.0147 | YES |
| XLK | 9 | 538 | 4 | 0.9703 | 1.0 | 1.0 | 1.0 | 0.134 | 0.0297 | NO: median spread 0.134 > 0.12 |

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
    "iv_coverage": 0.9992,
    "greeks_coverage": 0.9952,
    "oi_coverage": 1.0,
    "bid_ask_coverage": 0.971
  }
}
```

9 snapshot day(s) retained. Usability verdicts describe per-day data quality only; backtest feasibility additionally requires the history gates in OPTIONS_CHAIN_COLLECTION_CADENCE.md.


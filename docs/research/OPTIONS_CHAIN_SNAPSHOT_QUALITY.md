# Options Chain Snapshot Quality (Phase 1J.1)

Generated: 2026-06-26T19:46:10.161209+00:00

Status: **DATA_COLLECTION_ONLY** — quality audit of persisted snapshots; no strategy, no signals.

Snapshot days retained: 11 (2026-06-12, 2026-06-15, 2026-06-16, 2026-06-17, 2026-06-18, 2026-06-19, 2026-06-22, 2026-06-23, 2026-06-24, 2026-06-25, 2026-06-26). Symbols: 22. Contracts (latest day): 8630.

| Symbol | Days | Contracts | Expirations | Bid/Ask | IV | Greeks | OI | Med Spread | Stale | Usable (per-day quality) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| AAPL | 11 | 288 | 4 | 0.9514 | 1.0 | 0.9965 | 1.0 | 0.0705 | 0.0486 | YES |
| AMZN | 11 | 232 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.08 | 0.0 | YES |
| ARW | 1 | 34 | 1 | 0.8235 | 1.0 | 1.0 | 1.0 | 0.1524 | 0.1176 | NO: contracts 34 < 40; bid/ask coverage 0.8235 < 0.9; median spread 0.1524 > 0.12 |
| AVGO | 1 | 330 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0786 | 0.0 | YES |
| DIA | 11 | 600 | 4 | 0.9883 | 1.0 | 1.0 | 1.0 | 0.0446 | 0.0117 | YES |
| GOOG | 11 | 336 | 4 | 0.9911 | 1.0 | 1.0 | 1.0 | 0.0863 | 0.003 | YES |
| GOOGL | 11 | 352 | 4 | 0.9972 | 1.0 | 1.0 | 1.0 | 0.0819 | 0.0 | YES |
| HUM | 11 | 334 | 4 | 0.9461 | 1.0 | 1.0 | 1.0 | 0.1759 | 0.0539 | NO: median spread 0.1759 > 0.12 |
| IWM | 11 | 516 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0098 | 0.0 | YES |
| LSCC | 11 | 68 | 2 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0964 | 0.0 | YES |
| META | 11 | 560 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0716 | 0.0 | YES |
| MSFT | 11 | 342 | 4 | 0.9942 | 1.0 | 1.0 | 1.0 | 0.0741 | 0.0029 | YES |
| NUE | 10 | 72 | 2 | 0.9028 | 1.0 | 0.9583 | 1.0 | 0.1164 | 0.0972 | YES |
| NVDA | 11 | 200 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0355 | 0.0 | YES |
| NXPI | 11 | 72 | 2 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0682 | 0.0 | YES |
| OSCR | 10 | 196 | 4 | 0.9439 | 1.0 | 1.0 | 1.0 | 0.3771 | 0.0561 | NO: median spread 0.3771 > 0.12 |
| QQQ | 11 | 1198 | 4 | 1.0 | 1.0 | 0.9958 | 1.0 | 0.0172 | 0.0 | YES |
| SBAC | 11 | 72 | 2 | 0.6944 | 1.0 | 0.9861 | 1.0 | 0.1788 | 0.2917 | NO: bid/ask coverage 0.6944 < 0.9; median spread 0.1788 > 0.12 |
| SMH | 11 | 722 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0454 | 0.0 | YES |
| SPY | 11 | 1564 | 4 | 1.0 | 1.0 | 0.9725 | 1.0 | 0.0182 | 0.0 | YES |
| STLD | 11 | 72 | 2 | 0.9028 | 1.0 | 1.0 | 1.0 | 0.1187 | 0.0833 | YES |
| XLK | 11 | 470 | 4 | 0.9617 | 1.0 | 1.0 | 1.0 | 0.1827 | 0.0362 | NO: median spread 0.1827 > 0.12 |

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
    "greeks_coverage": 0.995,
    "oi_coverage": 1.0,
    "bid_ask_coverage": 0.9699
  }
}
```

11 snapshot day(s) retained. Usability verdicts describe per-day data quality only; backtest feasibility additionally requires the history gates in OPTIONS_CHAIN_COLLECTION_CADENCE.md.


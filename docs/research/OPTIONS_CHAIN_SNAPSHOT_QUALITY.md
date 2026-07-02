# Options Chain Snapshot Quality (Phase 1J.1)

Generated: 2026-07-02T19:46:08.538626+00:00

Status: **DATA_COLLECTION_ONLY** — quality audit of persisted snapshots; no strategy, no signals.

Snapshot days retained: 15 (2026-06-12, 2026-06-15, 2026-06-16, 2026-06-17, 2026-06-18, 2026-06-19, 2026-06-22, 2026-06-23, 2026-06-24, 2026-06-25, 2026-06-26, 2026-06-29, 2026-06-30, 2026-07-01, 2026-07-02). Symbols: 25. Contracts (latest day): 7904.

| Symbol | Days | Contracts | Expirations | Bid/Ask | IV | Greeks | OI | Med Spread | Stale | Usable (per-day quality) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| AAPL | 15 | 274 | 4 | 0.9964 | 1.0 | 1.0 | 1.0 | 0.0765 | 0.0036 | YES |
| AJG | 1 | 34 | 1 | 0.8235 | 1.0 | 1.0 | 1.0 | 0.0789 | 0.1765 | NO: contracts 34 < 40; bid/ask coverage 0.8235 < 0.9 |
| AMZN | 15 | 232 | 4 | 0.9828 | 1.0 | 1.0 | 1.0 | 0.0734 | 0.0129 | YES |
| ARW | 1 | 34 | 1 | 0.8235 | 1.0 | 1.0 | 1.0 | 0.1524 | 0.1176 | NO: contracts 34 < 40; bid/ask coverage 0.8235 < 0.9; median spread 0.1524 > 0.12 |
| AVGO | 1 | 330 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0786 | 0.0 | YES |
| DIA | 15 | 532 | 4 | 0.9756 | 1.0 | 0.985 | 1.0 | 0.0433 | 0.0207 | YES |
| GOOG | 15 | 310 | 4 | 0.9677 | 1.0 | 1.0 | 1.0 | 0.0817 | 0.0226 | YES |
| GOOGL | 15 | 310 | 4 | 0.9968 | 1.0 | 1.0 | 1.0 | 0.0685 | 0.0032 | YES |
| HUM | 15 | 322 | 4 | 0.9565 | 1.0 | 1.0 | 1.0 | 0.1502 | 0.0435 | NO: median spread 0.1502 > 0.12 |
| IWM | 15 | 446 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0081 | 0.0 | YES |
| LSCC | 14 | 34 | 1 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0724 | 0.0 | NO: contracts 34 < 40 |
| META | 15 | 484 | 4 | 0.9917 | 1.0 | 1.0 | 1.0 | 0.0742 | 0.0041 | YES |
| MSFT | 15 | 328 | 4 | 0.997 | 1.0 | 1.0 | 1.0 | 0.0606 | 0.0 | YES |
| NUE | 14 | 34 | 1 | 0.9706 | 1.0 | 1.0 | 1.0 | 0.128 | 0.0294 | NO: contracts 34 < 40; median spread 0.128 > 0.12 |
| NVDA | 12 | 200 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0289 | 0.0 | YES |
| NXPI | 15 | 34 | 1 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0574 | 0.0 | NO: contracts 34 < 40 |
| OSCR | 14 | 196 | 4 | 0.9898 | 1.0 | 1.0 | 1.0 | 0.2887 | 0.0102 | NO: median spread 0.2887 > 0.12 |
| QQQ | 15 | 874 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.015 | 0.0 | YES |
| SBAC | 11 | 72 | 2 | 0.6944 | 1.0 | 0.9861 | 1.0 | 0.1788 | 0.2917 | NO: bid/ask coverage 0.6944 < 0.9; median spread 0.1788 > 0.12 |
| SMH | 15 | 566 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0485 | 0.0 | YES |
| SPOT | 4 | 348 | 4 | 0.9799 | 1.0 | 1.0 | 1.0 | 0.1582 | 0.0172 | NO: median spread 0.1582 > 0.12 |
| SPY | 15 | 1238 | 4 | 1.0 | 1.0 | 0.9814 | 1.0 | 0.0142 | 0.0 | YES |
| STLD | 15 | 36 | 1 | 0.9444 | 1.0 | 1.0 | 1.0 | 0.0928 | 0.0556 | NO: contracts 36 < 40 |
| STM | 3 | 194 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.1896 | 0.0 | NO: median spread 0.1896 > 0.12 |
| XLK | 15 | 442 | 4 | 0.9751 | 1.0 | 1.0 | 1.0 | 0.169 | 0.0249 | NO: median spread 0.169 > 0.12 |

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
    "greeks_coverage": 0.9957,
    "oi_coverage": 1.0,
    "bid_ask_coverage": 0.973
  }
}
```

15 snapshot day(s) retained. Usability verdicts describe per-day data quality only; backtest feasibility additionally requires the history gates in OPTIONS_CHAIN_COLLECTION_CADENCE.md.


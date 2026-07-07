# Options Chain Snapshot Quality (Phase 1J.1)

Generated: 2026-07-07T19:46:09.816873+00:00

Status: **DATA_COLLECTION_ONLY** — quality audit of persisted snapshots; no strategy, no signals.

Snapshot days retained: 18 (2026-06-12, 2026-06-15, 2026-06-16, 2026-06-17, 2026-06-18, 2026-06-19, 2026-06-22, 2026-06-23, 2026-06-24, 2026-06-25, 2026-06-26, 2026-06-29, 2026-06-30, 2026-07-01, 2026-07-02, 2026-07-03, 2026-07-06, 2026-07-07). Symbols: 26. Contracts (latest day): 8956.

| Symbol | Days | Contracts | Expirations | Bid/Ask | IV | Greeks | OI | Med Spread | Stale | Usable (per-day quality) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| AAPL | 18 | 296 | 4 | 0.9932 | 1.0 | 0.9932 | 1.0 | 0.0563 | 0.0068 | YES |
| AJG | 4 | 36 | 1 | 0.8333 | 1.0 | 1.0 | 1.0 | 0.0876 | 0.1667 | NO: contracts 36 < 40; bid/ask coverage 0.8333 < 0.9 |
| AMZN | 18 | 232 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0557 | 0.0 | YES |
| ARW | 1 | 34 | 1 | 0.8235 | 1.0 | 1.0 | 1.0 | 0.1524 | 0.1176 | NO: contracts 34 < 40; bid/ask coverage 0.8235 < 0.9; median spread 0.1524 > 0.12 |
| AVGO | 1 | 330 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0786 | 0.0 | YES |
| DIA | 18 | 594 | 4 | 0.9545 | 1.0 | 0.9697 | 1.0 | 0.0478 | 0.0421 | YES |
| GOOG | 18 | 344 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0723 | 0.0 | YES |
| GOOGL | 18 | 352 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0735 | 0.0 | YES |
| HUM | 18 | 352 | 4 | 0.9716 | 1.0 | 1.0 | 1.0 | 0.2162 | 0.0284 | NO: median spread 0.2162 > 0.12 |
| IWM | 18 | 530 | 4 | 0.9906 | 1.0 | 0.9906 | 1.0 | 0.0163 | 0.0094 | YES |
| LSCC | 14 | 34 | 1 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0724 | 0.0 | NO: contracts 34 < 40 |
| META | 18 | 576 | 4 | 0.9983 | 1.0 | 1.0 | 1.0 | 0.0589 | 0.0017 | YES |
| MSFT | 18 | 350 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0529 | 0.0 | YES |
| NUE | 17 | 36 | 1 | 0.9444 | 1.0 | 1.0 | 1.0 | 0.1065 | 0.0556 | NO: contracts 36 < 40 |
| NVDA | 12 | 200 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0289 | 0.0 | YES |
| NXPI | 17 | 34 | 1 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0544 | 0.0 | NO: contracts 34 < 40 |
| OSCR | 17 | 188 | 4 | 0.984 | 1.0 | 1.0 | 1.0 | 0.2278 | 0.016 | NO: median spread 0.2278 > 0.12 |
| QQQ | 18 | 1092 | 4 | 0.9982 | 1.0 | 0.9936 | 1.0 | 0.0156 | 0.0018 | YES |
| SBAC | 11 | 72 | 2 | 0.6944 | 1.0 | 0.9861 | 1.0 | 0.1788 | 0.2917 | NO: bid/ask coverage 0.6944 < 0.9; median spread 0.1788 > 0.12 |
| SMH | 18 | 674 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.043 | 0.0 | YES |
| SPOT | 7 | 400 | 4 | 0.9275 | 1.0 | 1.0 | 1.0 | 0.1085 | 0.05 | YES |
| SPY | 18 | 1454 | 4 | 0.9966 | 0.9993 | 0.9752 | 1.0 | 0.0169 | 0.0034 | YES |
| STLD | 18 | 36 | 1 | 0.9722 | 1.0 | 1.0 | 1.0 | 0.1143 | 0.0278 | NO: contracts 36 < 40 |
| STM | 6 | 206 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.1538 | 0.0 | NO: median spread 0.1538 > 0.12 |
| TKR | 1 | 36 | 1 | 0.9444 | 1.0 | 1.0 | 1.0 | 0.1228 | 0.0556 | NO: contracts 36 < 40; median spread 0.1228 > 0.12 |
| XLK | 18 | 468 | 4 | 0.9722 | 1.0 | 1.0 | 1.0 | 0.1529 | 0.0256 | NO: median spread 0.1529 > 0.12 |

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
    "bid_ask_coverage": 0.9735
  }
}
```

18 snapshot day(s) retained. Usability verdicts describe per-day data quality only; backtest feasibility additionally requires the history gates in OPTIONS_CHAIN_COLLECTION_CADENCE.md.


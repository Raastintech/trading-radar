# Options Chain Snapshot Quality (Phase 1J.1)

Generated: 2026-06-15T19:46:10.323102+00:00

Status: **DATA_COLLECTION_ONLY** — quality audit of persisted snapshots; no strategy, no signals.

Snapshot days retained: 2 (2026-06-12, 2026-06-15). Symbols: 22. Contracts (latest day): 9052.

| Symbol | Days | Contracts | Expirations | Bid/Ask | IV | Greeks | OI | Med Spread | Stale | Usable (per-day quality) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| AAPL | 2 | 278 | 4 | 0.9964 | 1.0 | 0.982 | 1.0 | 0.0741 | 0.0036 | YES |
| AMZN | 2 | 232 | 4 | 1.0 | 1.0 | 0.9957 | 1.0 | 0.0742 | 0.0 | YES |
| ARW | 1 | 34 | 1 | 0.8235 | 1.0 | 1.0 | 1.0 | 0.1524 | 0.1176 | NO: contracts 34 < 40; bid/ask coverage 0.8235 < 0.9; median spread 0.1524 > 0.12 |
| AVGO | 1 | 330 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0786 | 0.0 | YES |
| DIA | 2 | 570 | 4 | 0.9807 | 0.9895 | 1.0 | 1.0 | 0.0672 | 0.0193 | YES |
| GOOG | 2 | 352 | 4 | 1.0 | 1.0 | 0.9915 | 1.0 | 0.0799 | 0.0 | YES |
| GOOGL | 2 | 352 | 4 | 1.0 | 1.0 | 0.9915 | 1.0 | 0.0747 | 0.0 | YES |
| HUM | 2 | 320 | 4 | 0.9125 | 1.0 | 0.9844 | 1.0 | 0.1748 | 0.0875 | NO: median spread 0.1748 > 0.12 |
| IWM | 2 | 672 | 4 | 1.0 | 1.0 | 0.9985 | 1.0 | 0.014 | 0.0 | YES |
| LSCC | 2 | 68 | 2 | 1.0 | 1.0 | 1.0 | 1.0 | 0.1036 | 0.0 | YES |
| META | 2 | 546 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0722 | 0.0 | YES |
| MSFT | 2 | 350 | 4 | 0.9943 | 1.0 | 1.0 | 1.0 | 0.0672 | 0.0057 | YES |
| NUE | 1 | 30 | 1 | 0.9333 | 1.0 | 1.0 | 1.0 | 0.1338 | 0.0 | NO: contracts 30 < 40; median spread 0.1338 > 0.12 |
| NVDA | 2 | 200 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0292 | 0.0 | YES |
| NXPI | 2 | 36 | 1 | 1.0 | 1.0 | 1.0 | 1.0 | 0.077 | 0.0 | NO: contracts 36 < 40 |
| OSCR | 1 | 190 | 4 | 0.9579 | 1.0 | 1.0 | 1.0 | 0.3963 | 0.0421 | NO: median spread 0.3963 > 0.12 |
| QQQ | 2 | 1518 | 4 | 1.0 | 0.998 | 0.9987 | 1.0 | 0.0202 | 0.0 | YES |
| SBAC | 2 | 36 | 1 | 0.7778 | 1.0 | 1.0 | 1.0 | 0.1497 | 0.2222 | NO: contracts 36 < 40; bid/ask coverage 0.7778 < 0.9; median spread 0.1497 > 0.12 |
| SMH | 2 | 718 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0542 | 0.0 | YES |
| SPY | 2 | 1660 | 4 | 1.0 | 0.991 | 0.9777 | 1.0 | 0.02 | 0.0 | YES |
| STLD | 2 | 68 | 2 | 0.9853 | 1.0 | 1.0 | 1.0 | 0.1239 | 0.0147 | NO: median spread 0.1239 > 0.12 |
| XLK | 2 | 492 | 4 | 0.9431 | 1.0 | 0.9898 | 1.0 | 0.1398 | 0.0528 | NO: median spread 0.1398 > 0.12 |

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
    "iv_coverage": 0.9989,
    "greeks_coverage": 0.9955,
    "oi_coverage": 1.0,
    "bid_ask_coverage": 0.9741
  }
}
```

2 snapshot day(s) retained. Usability verdicts describe per-day data quality only; backtest feasibility additionally requires the history gates in OPTIONS_CHAIN_COLLECTION_CADENCE.md.


# Options Chain Snapshot Quality (Phase 1J.1)

Generated: 2026-06-12T23:37:13.320789+00:00

Status: **DATA_COLLECTION_ONLY** — quality audit of persisted snapshots; no strategy, no signals.

Snapshot days retained: 1 (2026-06-12). Symbols: 20. Contracts (latest day): 8832.

| Symbol | Days | Contracts | Expirations | Bid/Ask | IV | Greeks | OI | Med Spread | Stale | Usable (per-day quality) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| AAPL | 1 | 278 | 4 | 0.9173 | 1.0 | 1.0 | 1.0 | 0.0838 | 0.036 | YES |
| AMZN | 1 | 232 | 4 | 0.9698 | 1.0 | 1.0 | 1.0 | 0.076 | 0.0 | YES |
| ARW | 1 | 34 | 1 | 0.8235 | 1.0 | 1.0 | 1.0 | 0.1524 | 0.1176 | NO: contracts 34 < 40; bid/ask coverage 0.8235 < 0.9; median spread 0.1524 > 0.12 |
| AVGO | 1 | 330 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0786 | 0.0 | YES |
| DIA | 1 | 570 | 4 | 0.9825 | 0.9912 | 1.0 | 1.0 | 0.0749 | 0.014 | YES |
| GOOG | 1 | 352 | 4 | 0.9261 | 1.0 | 1.0 | 1.0 | 0.0882 | 0.0227 | YES |
| GOOGL | 1 | 352 | 4 | 0.9233 | 1.0 | 1.0 | 1.0 | 0.0927 | 0.0256 | YES |
| HUM | 1 | 320 | 4 | 0.9125 | 1.0 | 0.9969 | 1.0 | 0.2321 | 0.0531 | NO: median spread 0.2321 > 0.12 |
| IWM | 1 | 672 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0254 | 0.0 | YES |
| LSCC | 1 | 68 | 2 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0922 | 0.0 | YES |
| META | 1 | 546 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0886 | 0.0 | YES |
| MSFT | 1 | 350 | 4 | 0.9943 | 1.0 | 1.0 | 1.0 | 0.0757 | 0.0 | YES |
| NVDA | 1 | 200 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0421 | 0.0 | YES |
| NXPI | 1 | 36 | 1 | 0.9722 | 1.0 | 1.0 | 1.0 | 0.0798 | 0.0 | NO: contracts 36 < 40 |
| QQQ | 1 | 1518 | 4 | 0.9802 | 1.0 | 0.9974 | 1.0 | 0.0431 | 0.0112 | YES |
| SBAC | 1 | 36 | 1 | 0.75 | 1.0 | 1.0 | 1.0 | 0.1499 | 0.1667 | NO: contracts 36 < 40; bid/ask coverage 0.75 < 0.9; median spread 0.1499 > 0.12 |
| SMH | 1 | 718 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0502 | 0.0 | YES |
| SPY | 1 | 1660 | 4 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0233 | 0.0 | YES |
| STLD | 1 | 68 | 2 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0873 | 0.0 | YES |
| XLK | 1 | 492 | 4 | 0.9146 | 1.0 | 1.0 | 1.0 | 0.2475 | 0.0285 | NO: median spread 0.2475 > 0.12 |

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
  }
}
```

1 snapshot day(s) retained. Usability verdicts describe per-day data quality only; backtest feasibility additionally requires the history gates in OPTIONS_CHAIN_COLLECTION_CADENCE.md.


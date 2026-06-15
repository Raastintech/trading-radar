# Options Chain Snapshot Schema (Phase 1J.1)

Status: **DATA_COLLECTION_ONLY** — this schema feeds no strategy, no signals, no orders.

One row = one option contract observed at one collection moment. Snapshots are
**append-only and point-in-time**: a file written for (date, underlying) is never
overwritten, and current chains are never back-dated. History exists only from the first
collection day forward.

Storage: `data/options_snapshots/YYYY-MM-DD/<UNDERLYING>.parquet`

## Fields

| Field | Type | Notes |
|---|---|---|
| `as_of_date` | str (YYYY-MM-DD) | collection trading date |
| `as_of_timestamp_utc` | str ISO-8601 | exact collection moment |
| `provider` | str | serving feed (`alpaca`, enriched flag if Tradier IV/greeks merged) |
| `underlying` | str | e.g. `SPY` |
| `underlying_price` | float/null | spot at collection (cache-first daily close, then quote mid) |
| `expiration` | str (YYYY-MM-DD) | listed expiration |
| `dte` | int | calendar days to expiration at collection |
| `option_type` | str | `call` / `put` |
| `strike` | float | |
| `bid` | float/null | |
| `ask` | float/null | |
| `mid` | float/null | (bid+ask)/2 when both sides exist |
| `last` | float/null | last trade price if provided |
| `volume` | float/null | day volume |
| `open_interest` | float/null | merged from the Alpaca contracts endpoint (snapshot endpoint has none) |
| `implied_volatility` | float/null | Tradier enrichment when available; Alpaca snapshots carry none |
| `delta` | float/null | enrichment-only |
| `gamma` | float/null | enrichment-only |
| `theta` | float/null | enrichment-only |
| `vega` | float/null | enrichment-only |
| `rho` | float/null | enrichment-only |
| `quote_timestamp` | str/null | provider quote timestamp when exposed |
| `earnings_date` | str/null | currently null at collection; joinable point-in-time at read time from the FMP earnings calendar cache |
| `days_to_earnings` | int/null | same as above |
| `raw_provider_fields` | str (JSON) | contractSymbol, bid_iv/ask_iv, inTheMoney, close_price extras |
| `data_quality_flags` | str (JSON list) | see below |

## Data quality flags

| Flag | Condition |
|---|---|
| `missing_bid_ask` | bid or ask absent / non-positive |
| `wide_spread` | (ask − bid) > 25% of mid (mid > 0) |
| `missing_iv` | implied_volatility absent or 0 |
| `missing_greeks` | delta absent |
| `missing_oi` | open_interest absent |
| `stale_quote` | zero volume AND missing/zero bid (no evidence of a live market) |
| `crossed_market` | bid > ask with both positive |
| `zero_volume` | volume == 0 |
| `expiration_out_of_range` | DTE outside the configured collection window (kept for schema completeness; filtered rows are not written) |

## Collection rules

- Default universe: SPY, QQQ, IWM, SMH, XLK + the most options-covered liquid names already
  used by the Stock Lens (ranked by retained IV-history depth).
- Target expirations: 20–70 DTE, prioritized by closeness to 45 DTE, capped per symbol.
- Strikes: configurable band around spot (default ±30%); all strikes in band, both sides.
- Idempotent per (symbol, date): an existing snapshot file is skipped, never overwritten.
- Hard provider budget guards: max symbols/run, max expiries/symbol, max provider calls/run.
- Provider calls only from the collector CLI — never from the dashboard.

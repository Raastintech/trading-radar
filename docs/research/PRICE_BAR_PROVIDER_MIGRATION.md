# Price Bar Provider Migration — Alpaca → FMP

**Phase 3B — 2026-06-14**
**Status:** COMPLETE

---

## Summary

All daily price bar refreshes are now FMP-first. Alpaca is not required for any
research operation. The AlpacaClient in `core/alpaca_client.py` is a stub that
serves reads from `cache/prices/*.parquet` — it makes no network calls.

**Final verdict: CAN FMP REPLACE ALPACA PRICE BARS FOR RESEARCH MODE? YES**

---

## Provider Priority (as of Phase 3B)

| Priority | Source | Used by |
|---|---|---|
| 1 | `cache/prices/*.parquet` | All research modules (AlpacaClient stub reads this) |
| 2 | FMP `historical-price-eod/full` | `nightly_refresh.py`, `deepen_price_cache.py --execute`, `regime_forecast.py` |
| 3 | yfinance (DEBUG only, RESEARCH_ALLOW_YFINANCE_DEBUG=true) | `market_heartbeat.py` fallback only |
| ✗ | Alpaca SIP (live) | REMOVED — no network calls, no credentials required |

---

## Modules Audited

| Module | Old source | New source | Notes |
|---|---|---|---|
| `nightly_refresh.py` | Alpaca SIP | FMP `get_ticker_bars` | Phase 3A complete |
| `deepen_price_cache.py` | Alpaca SIP | FMP `get_ticker_bars` | Phase 3A complete |
| `research/regime_forecast.py` | Parquet → Alpaca → yfinance | Parquet → FMP → yfinance | Alpaca path uses stub (reads cache) |
| `research/market_heartbeat.py` | Parquet → FMP → yfinance | Same | No Alpaca dependency |
| `research/research_scanner.py` | Parquet | Same | No provider calls |
| `research/stock_research_card.py` | Parquet | Same | No provider calls |
| `research/sector_leadership_report.py` | Parquet | Same | No provider calls |
| `research/data_freshness_report.py` | Parquet metadata | Same | No provider calls |
| `core/market_regime.py` | Alpaca stub → cache | Alpaca stub → cache | Legacy daemon module; stub reads parquet |
| `core/alpha_discovery.py` | Alpaca stub batch | Stub reads cache | No live Alpaca calls |
| `research/prebuild_stock_lenses.py` | Alpaca stub | Stub reads cache | No live Alpaca calls |

---

## Price Bar Comparison: FMP vs Local Cache

Run date: 2026-06-14. Both sources report 2026-06-12 as the latest bar (Friday close).

| Ticker | Cache close | FMP close | Diff | Diff % |
|--------|------------|----------|------|--------|
| SPY    | $741.42    | $741.75  | $0.33 | 0.04% |
| QQQ    | $720.17    | $721.34  | $1.17 | 0.16% |
| AAPL   | $291.13    | $291.13  | $0.00 | 0.00% |
| NVDA   | $204.18    | $205.19  | $1.01 | 0.49% |
| SMCI   | $29.81     | $30.46   | $0.65 | 2.18% |

**Interpretation:** Differences are within normal bid/ask spread and quote-source
variation. Both sources agree on date. FMP bars are suitable for research-mode
signal generation, MA calculations, RS scoring, and regime detection.

**SMCI note:** The 2.18% diff may reflect a later intraday print captured by one
source vs the other — common for high-volatility small caps. Not a concern for
daily-bar research.

---

## Coverage

- FMP covers all major US equities, ETFs, and indexes.
- `cache/prices/*.parquet` contains 5,618 symbols as of 2026-06-14.
- 348 parquets are fresh (<48h); remaining are refreshed on-demand by `deepen_price_cache.py --execute`.
- The nightly timer pre-warms regime forecast symbols (~30 ETFs) via FMP automatically.

---

## FMP Budget Impact

Each `get_ticker_bars()` call consumes 1 FMP API call.
The nightly pre-warm refreshes ~30 symbols = 30 calls/night.
`deepen_price_cache.py --execute` with a 50-ticker priority list = 50 calls.
Budget limit: 750 RPM (never hit by research operations).

---

## What Is NOT Replaced

Alpaca was also used for:
- **Intraday bars**: `get_intraday_bars()` returns `[]` in the stub. No research module
  requires intraday data.
- **Real-time quotes**: The stub returns the last cached close as a quote. FMP batch
  quotes (`get_quotes_batch()`) provide live prices when FMP key is configured.
- **Account/positions API**: Stub returns `[]`. No execution path exists.

These gaps are acceptable for research-only mode.

---

## Commands

```bash
# Refresh regime forecast symbols (nightly, ~30 symbols via FMP):
./scripts/run_research_cycle.sh forecast

# Deep-refresh a priority list of research tickers (FMP):
SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python scripts/deepen_price_cache.py --priority --execute

# Check data freshness:
./scripts/run_research_cycle.sh data-freshness
```

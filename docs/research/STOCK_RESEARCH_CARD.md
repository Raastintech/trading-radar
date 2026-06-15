# STOCK RESEARCH CARD

**Module:** `research/stock_research_card.py`
**Phase:** 4D
**Mode:** RESEARCH_ONLY — no trade recommendations.

## Purpose

Generates a per-ticker research card for any name surfaced by the scanner,
the heartbeat, or the human operator. Cards compile trend, RS, volume,
catalyst, fundamentals, options (research-only), social attention, risk flags,
and invalidation conditions into a single human-readable artifact.

## Card Sections

| Section | Data Source | Trust |
|---|---|---|
| Price Trend | Price cache (parquet) | HIGH |
| RS Score | Price cache vs SPY | HIGH |
| Volume / Accumulation | Price cache | HIGH |
| Catalyst Summary | FMP earnings calendar + news + analyst grades | HIGH (when FMP available) |
| Fundamentals | FMP income / balance sheet | HIGH (when FMP available) |
| Valuation Snapshot | FMP ratios | MEDIUM |
| Options Snapshot | Tradier (research-only; no execution) | MEDIUM |
| Social Attention | Social arb sidecars (degraded to NO_SOCIAL_DATA if absent) | MEDIUM |
| Risk Flags | Derived from all above | HIGH |
| Research Conclusion | Rule-based summary; one of allowed outputs only | HIGH |
| Scanner Context | research_scanner_latest.json | HIGH (if scanner ran) |

## Allowed Outputs

- "worth researching"
- "watch candidate"
- "requires manual review"
- "risk flagged — multiple concerns; requires manual review before any action"
- "extended; wait for reset"
- "potential asymmetric candidate — speculative long-term watch; high risk"
- "catalyst candidate — upcoming earnings; requires manual review"
- "beaten-down potential recovery — worth researching further"
- "crowded/viral — late-mover risk; watch candidate only after consolidation"

## Guardrails

- No buy/sell/entry/stop/target outputs
- Social data is never fabricated; degrades to NO_SOCIAL_DATA
- Tradier options are research-only; execution is disabled
- Alpaca is not required
- Manual review is required before any action

## Outputs

- `cache/research/stock_research_card_<TICKER>.json`
- `logs/stock_research_card_latest.txt`

## Usage

```bash
SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python research/stock_research_card.py AAPL NVDA
GEM_TRADER_SKIP_DOTENV=true .venv/bin/python research/stock_research_card.py AAPL --offline
./scripts/run_research_cycle.sh stock-research-card AAPL NVDA
```

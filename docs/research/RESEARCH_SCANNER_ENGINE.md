# RESEARCH SCANNER ENGINE

**Module:** `research/research_scanner.py`
**Phase:** 4B / 4C
**Mode:** RESEARCH_ONLY — no trade recommendations.

## Purpose

The Research Scanner runs six evidence categories against the cached price
universe and FMP data each day. It surfaces names worth researching — not
names worth trading. The human operator decides whether any scanner result
deserves deeper research, and all subsequent steps are manual.

## Scanner Categories

| # | Category | Data Sources | Trust |
|---|---|---|---|
| 1 | Early Accumulation | Price cache (RS + volume trend + higher lows) | MEDIUM |
| 2 | Beaten-Down Recovery | Price cache (drawdown + stabilization) | MEDIUM |
| 3 | Sector / Theme Leaders | Price cache (RS rank within leading sectors) | HIGH |
| 4 | Catalyst Watch | FMP earnings calendar + analyst grades | HIGH |
| 5 | Social Arb / Attention Anomaly | Social arb/attention sidecars | MEDIUM |
| 6 | Long-Term Asymmetric Watch | Price cache + FMP fundamentals (optional) | SPECULATIVE |

## Watchlist Labels (4C)

| Label | Meaning |
|---|---|
| `WATCH` | Worth monitoring; research phase |
| `RESEARCH` | Higher priority; warrants deeper look |
| `EARLY_ACCUMULATION` | Volume + RS improving; base building |
| `BEATEN_DOWN` | Large drawdown + stabilization signal |
| `SECTOR_LEADER` | Outperforming sector ETF and SPY |
| `CATALYST` | Upcoming earnings or analyst upgrade |
| `SOCIAL_ARB` | Social attention anomaly; early-stage |
| `SPECULATIVE_10X` | Long-term asymmetric thesis; high risk |
| `EXTENDED` | Outperforming but stretched vs MA |
| `RISKY` | Signal present but risk flags elevated |
| `AVOID` | No positive signals; multiple flags |
| `CROWDED` | Already viral/widely discussed; risky |
| `NO_SOCIAL_DATA` | Social signal absent; data degraded |

## Guardrails

- No buy/sell/entry/stop/target output
- Social data is never fabricated; degrades to NO_SOCIAL_DATA
- Already-viral names are labeled CROWDED
- Tradier is research-only; no execution
- Alpaca is not required
- Manual review is required before any action

## Outputs

- `cache/research/research_scanner_latest.json`
- `logs/research_scanner_latest.txt`

## Usage

```bash
SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python research/research_scanner.py
GEM_TRADER_SKIP_DOTENV=true .venv/bin/python research/research_scanner.py --offline
./scripts/run_research_cycle.sh research-scanner
```

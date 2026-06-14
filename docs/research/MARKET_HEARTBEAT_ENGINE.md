# MARKET HEARTBEAT ENGINE

**Module:** `research/market_heartbeat.py`
**Phase:** 4A
**Mode:** RESEARCH_ONLY — no trade recommendations.

## Purpose

The Market Heartbeat provides a daily read on the broad market environment.
It is not a trade signal. It does not recommend entries, exits, stops, or
position sizes. Its purpose is to give the human operator a clear, concise
snapshot of what the market is doing so manual research is conducted in the
right context.

## Data Sources

1. `cache/prices/*.parquet` — always tried first (no provider call)
2. FMP historical bars — fallback if parquet missing or stale
3. yfinance — debug fallback only (`RESEARCH_ALLOW_YFINANCE_DEBUG=true`)

VIX: FMP `get_vix()` → None on failure. Degrades gracefully.

**Alpaca is not required.**

## Heartbeat Labels

| Label | Meaning |
|---|---|
| `RISK_ON` | Broad strength, positive breadth, low vol |
| `HEALTHY_PULLBACK` | Short-term weakness inside intact uptrend |
| `CHOP` | No clear direction, mixed signals |
| `CORRECTION` | Meaningful decline (≥7%), elevated vol |
| `RISK_OFF` | Defensive rotation, credit stress, high vol |
| `TECH_LED` | Tech/semis outperforming; narrow leadership |
| `SMALL_CAP_LED` | IWM leading; risk appetite broadening |
| `DEFENSIVE_ROTATION` | Staples/utilities/health-care leading |

## Symbols Tracked

- Market: SPY, QQQ, IWM, SMH
- Vol proxy: VXX
- Sectors: XLK XLF XLV XLE XLY XLI XLP XLU XLB XLRE XLC
- Credit/risk: TLT, HYG

## Outputs

- `cache/research/market_heartbeat_latest.json`
- `logs/market_heartbeat_latest.txt`

## Usage

```bash
# With FMP credentials
SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python research/market_heartbeat.py

# Cache-only (no provider calls)
GEM_TRADER_SKIP_DOTENV=true .venv/bin/python research/market_heartbeat.py --offline

# Via research cycle
./scripts/run_research_cycle.sh market-heartbeat
```

## Guardrails

- No buy/sell/entry/stop/target output
- No paper signals, no governance, no execution
- No Alpaca required
- Tradier not used
- Degrades gracefully when data is missing

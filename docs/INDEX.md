# SniperTradingAI — Documentation Index

## Strategy & Architecture

| File | Purpose |
|------|---------|
| [CURRENT_DOCTRINE_MAP.md](strategy/CURRENT_DOCTRINE_MAP.md) | First stop for current operating truth and doctrine source ordering |
| [PROJECT_NORTH_STAR.md](strategy/PROJECT_NORTH_STAR.md) | Core mission, non-negotiables, long-term vision |
| [MASTER_PLAN.md](strategy/MASTER_PLAN.md) | Long-term roadmap reference; not the live active-sleeve status source |
| [STRATEGY_DOCTRINE.md](strategy/STRATEGY_DOCTRINE.md) | Permanent Quant Research Doctrine, strategy mandates, and required research output format |
| [CURRENT_READINESS.md](strategy/CURRENT_READINESS.md) | Live system readiness and current platform phase source of truth |
| [ARCHITECTURE_PHASE1.md](strategy/ARCHITECTURE_PHASE1.md) | System architecture and data flow |
| [BREAKOUT_TIMING_ENGINE_BLUEPRINT.md](strategy/BREAKOUT_TIMING_ENGINE_BLUEPRINT.md) | Breakout timing engine design |
| [OPTIONS_PHASE1_SPEC.md](strategy/OPTIONS_PHASE1_SPEC.md) | Options strategy spec (pending FMP Ultimate) |

## Research & Backtesting

| File | Purpose |
|------|---------|
| [EXECUTION_PROMPT_SHORT.md](strategy/EXECUTION_PROMPT_SHORT.md) | SHORT strategy backtester run instructions |
| [VALIDATION_OPEN_ITEMS.md](strategy/VALIDATION_OPEN_ITEMS.md) | Open validation issues and known gaps |
| [CODEX_PHASE5_PROMPT.md](strategy/CODEX_PHASE5_PROMPT.md) | Phase 5 build prompts and context |
| [reports/RESEARCH_ARCHIVE.md](reports/RESEARCH_ARCHIVE.md) | Historical backtest results archive |

## Historical / Obsolete Doctrine References

These documents are preserved for history only. They are not current operating
truth and must not override `CURRENT_READINESS.md`, `CURRENT_DOCTRINE_MAP.md`,
or current sleeve specs.

| File | Status |
|------|--------|
| `../SNIPER_TRADING_AI_MASTER_DOC.md` | Historical / obsolete consolidated master doc |
| [../docs/strategy_scanner_council_audit.md](strategy_scanner_council_audit.md) | Historical pre-doctrine audit with obsolete VOYAGER assumptions |
| [system_pipeline_v2.md](system_pipeline_v2.md) | Historical pipeline snapshot with obsolete sleeve assignments |

## Operations

| File | Purpose |
|------|---------|
| [trade_log.md](trade_log.md) | Manual trade log and notes |
| [daily_notes/](daily_notes/) | Day-by-day session notes |

## Live System (Ubuntu /home/gem/trading-production/)

```
production/
├── main.py                    # systemd entry point — runs via gem-trader.service
├── gem-trader.service         # systemd unit file
├── requirements.txt           # pip dependencies
├── core/
│   ├── config.py              # Credential loader (reads /home/gem/secure/trading.env)
│   ├── fmp_client.py          # FMP Starter client + Gatekeeper cache
│   ├── data_gatekeeper.py     # SQLite metadata + Parquet price cache
│   └── alpaca_client.py       # Alpaca Pro Plus execution + OHLCV
├── strategies/
│   ├── sniper.py              # Momentum breakout LONG (FROZEN during backtest phase)
│   ├── voyager.py             # Mean-reversion SHORT (FROZEN)
│   ├── remora.py              # Stealth accumulation LONG (FROZEN)
│   ├── contrarian.py          # Fear-regime LONG (FROZEN)
│   └── short_sleeve.py        # Earnings disappointment SHORT (ACTIVE — backtesting)
├── council/
│   └── veto_council.py        # 3 hard-veto + 6 soft-score agents
├── db/
│   └── trading_performance.db # SQLite — decisions, veto_log, trades, macro_events
└── legacy/                    # 112 migrated files from Mac
    ├── multi_sleeve_short_research.py  # SHORT backtester (3 sleeves)
    ├── short_backtester.py             # Backtester engine
    ├── research_data_provider.py       # FMP/Alpaca/AV routing
    ├── fundamental_data_fetcher.py     # FMP primary fundamentals
    ├── earnings_event_store.py         # Earnings event builder
    └── unified_master_trader_v3.py     # Legacy trader (use systemd main.py instead)
```

## Data Sources (Production)

| Source | Purpose | Plan |
|--------|---------|------|
| FMP | Earnings, fundamentals, economic calendar, VIX, news | Starter Annual (300 calls/min) |
| Alpaca | OHLCV bars (SIP feed), order execution, positions | Pro Plus |
| yfinance | Optional debug fallback only — never primary | None |

## Key Operational Commands (Ubuntu server)

```bash
# Start / stop via systemd (canonical method)
sudo systemctl start gem-trader
sudo systemctl stop gem-trader
sudo systemctl status gem-trader

# Or use shell scripts
./start_trader.sh      # starts unified_master_trader_v3.py
./stop_trader.sh
./check_status.sh

# Run SHORT backtester (Sleeve B)
cd /home/gem/trading-production/legacy
python3 multi_sleeve_short_research.py --universe 300 --mode sleeve_b --period 5y

# Scan specific tickers
./scan.sh AAPL MSFT TSLA

# View live logs
sudo journalctl -u gem-trader -f
```

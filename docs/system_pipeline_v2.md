# ARCHIVED / HISTORICAL DOCUMENT

> OBSOLETE — DO NOT USE FOR CURRENT OPERATIONS.
>
> This pipeline snapshot reflects an earlier sleeve map and obsolete VOYAGER /
> strategy-book assignments. It is preserved as a historical system snapshot,
> not as current doctrine or current platform state.
>
> Current operating truth now lives in:
> - `docs/strategy/CURRENT_DOCTRINE_MAP.md`
> - `docs/strategy/CURRENT_READINESS.md`
> - `docs/strategy/STRATEGY_DOCTRINE.md`
>
> Use this file only for historical context.

# gem-trader — System Pipeline v2

**Date:** 2026-04-16  
**Stack:** Alpaca Pro Plus (SIP data + execution) + FMP Premium (fundamentals, calendar, sentiment)

---

## 14-Stage Pipeline

```
[1] DISCOVERY          Dynamic universe builder — filters all US equities
[2] BASE FILTERS       Liquidity, price, volume minimums
[3] BARS FETCH         Alpaca get_stock_bars() → BarSet (fixed via _bars_from_response)
[4] FEATURES           ATR, momentum, RSI, volume ratio per strategy
[5] HARD FILTERS       Strategy-specific entry conditions (gap, score threshold)
[6] SCORING            Signal score 0–100 per scanner
[7] THRESHOLD GATE     MIN_SCORE cut (≥55 for most strategies)
[8] ROUTING            main.py: top 20 ranked signals per cycle
[9] VETO COUNCIL       9 agents: 3 Tier 1 hard-veto + 6 Tier 2 soft-score
                       Strategy-aware weights via COUNCIL_PROFILES
[10] CIRCUIT BREAKERS  CircuitBreakers.gate() — kill-switch, drawdown stop
[11] PORTFOLIO ALLOC   PortfolioAllocator — wraps PortfolioRisk + sector throttle
[12] ORDER MANAGER     AlpacaClient limit/market order submission
[13] POSITION MONITOR  PositionMonitor — stop/target/time-stop exits (every 60s)
[14] DECISION LOGGER   SQLite scan_results + decisions + veto_log
```

---

## Stage-by-Stage Status

| # | Stage | File | Status |
|---|-------|------|--------|
| 1 | Discovery | `core/universe.py` | Active |
| 2 | Base Filters | `core/universe.py` | Active |
| 3 | Bars Fetch | `core/alpaca_client.py` | **Fixed** — `_bars_from_response()` handles BarSet SDK break |
| 4 | Features | Per-strategy scanner | Active |
| 5 | Hard Filters | Per-strategy scanner | Active |
| 6 | Scoring | Per-strategy scanner | Active |
| 7 | Threshold Gate | Per-strategy scanner | Active |
| 8 | Routing | `main.py` | Active |
| 9 | Veto Council | `council/veto_council.py` | **Enhanced** — strategy-aware `COUNCIL_PROFILES` |
| 10 | Circuit Breakers | `execution/circuit_breakers.py` | Active |
| 11 | Portfolio Alloc | `execution/portfolio_allocator.py` | **New** — explicit stage |
| 12 | Order Manager | `execution/order_manager.py` | Active |
| 13 | Position Monitor | `execution/position_monitor.py` | Active |
| 14 | Decision Logger | `core/decision_logger.py` | Active |

---

## Five Strategies

| Strategy | Book | Direction | Activation Condition |
|----------|------|-----------|---------------------|
| SNIPER | A | LONG | Breakout momentum, volume confirmation |
| REMORA | A | LONG | Follows institutional flow signals |
| CONTRARIAN | B | LONG | VIX ≥ 22 fear regime (otherwise idle) |
| VOYAGER | C | SHORT | Trend breakdown continuation |
| SHORT | C | SHORT | Post-earnings disappointment gap down |

---

## Pipeline State Machine

```
SCANNER_SIGNAL
    │
    ├─ CircuitBreaker gate ──────────────────────────────────→ GATED
    │
    ├─ VetoCouncil hard-vetoed ──────────────────────────────→ REJECTED
    │
    ├─ VetoCouncil soft-vetoed (score borderline) ───────────→ GATED
    │
    └─ VetoCouncil APPROVED
           │
           ├─ [dashboard dry-run / manual scan — no further steps] → SCAN_APPROVED
           │
           └─ [daemon — proceeds to allocation]
                  │
                  ├─ PortfolioAllocator BLOCKED ────────────→ ALLOCATION_BLOCKED
                  │
                  └─ PortfolioAllocator APPROVED
                         │
                         ├─ OrderManager failed/rejected ──→ EXECUTION_FAILED
                         │
                         └─ OrderManager success ──────────→ READY_NOW  ← live position
```

### Status Reference

| Status | Source | Meaning |
|--------|--------|---------|
| `READY_NOW` | daemon | Order placed — live position exists |
| `SCAN_APPROVED` | dashboard scan | Council approved in dry-run; no allocator/execution |
| `EXECUTION_FAILED` | daemon | Council + allocator approved; order rejected/failed |
| `ALLOCATION_BLOCKED` | daemon | Council approved; allocator blocked (book/sector cap) |
| `GATED` | both | Scanner-confirmed; council soft-blocked (borderline) |
| `REJECTED` | both | Scanner-confirmed; council hard-blocked (low score) |
| `APPROVED` *(legacy)* | pre-v2 daemon | Ambiguous — display as SCAN-APPROVED |
| `WATCH` *(legacy)* | pre-v2 daemon | Ambiguous — display as SCAN-APPROVED |

---

## Veto Council Profiles

Each strategy uses strategy-specific Tier 2 soft-score weights:

| Agent | SNIPER | REMORA | VOYAGER | SHORT | CONTRARIAN |
|-------|--------|--------|---------|-------|------------|
| sector | 12% | 12% | 18% | 8% | 8% |
| flow | 28% | 35% | 20% | 18% | 16% |
| sentiment | 8% | 10% | 22% | 22% | 32% |
| earnings | 22% | 15% | 22% | 30% | 10% |
| spread | 12% | 18% | 10% | 14% | 12% |
| momentum | 18% | 10% | 8% | 8% | 22% |
| **Min score** | 52 | 48 | 50 | 55 | 45 |

---

## Portfolio Risk — Book Limits

| Book | Strategies | Max Gross | Notes |
|------|-----------|-----------|-------|
| A | SNIPER, REMORA | 60% long | Trend/momentum |
| B | CONTRARIAN | 20% long | Mean-reversion |
| C | VOYAGER, SHORT | 30% short | Tactical short |

Global: max 10 open positions, max 3 per strategy, max 5% single-name heat.  
Sector throttle: max 2 open positions per GICS sector (PortfolioAllocator).

---

## Data Dependencies

| Data | Source | Cache TTL | Used By |
|------|--------|-----------|---------|
| Daily OHLCV | Alpaca SIP | 12h Parquet | All scanners |
| Intraday bars | Alpaca SIP | Not cached | FlowAgent |
| Live quotes | Alpaca SIP | Not cached | SpreadAgent |
| VIX | FMP `/stable/quote` | 5 min | RegimeAgent |
| SPY bars | FMP `/stable/historical-price-eod` | 12h | RegimeAgent |
| Economic calendar | FMP `/stable/economic-calendar` | 4h | MacroAgent |
| Earnings calendar (future) | FMP `/stable/earnings-calendar` | 6h | EarningsAgent |
| Earnings calendar (past) | FMP `/stable/earnings-calendar` | 6h | ShortSleeve |
| News / sentiment | FMP `/stable/news/stock` | 1h | SentimentAgent |
| Fundamentals | FMP `/stable/income-statement` | 24h | Optional |

**Not available:** Earnings-call transcripts (FMP Premium tier limitation).  
ShortSleeve uses `eps_estimate` / `eps_actual` fields from the earnings calendar — no transcript dependency.

---

## Startup Self-Test

`core/startup_checks.py` runs on boot:

| Check | Critical | Failure Action |
|-------|----------|---------------|
| Timezone (zoneinfo) | Yes | HALT |
| Database writable | Yes | HALT |
| Alpaca auth / account | Yes | HALT |
| Alpaca bar fetch (SPY) | No | DEGRADED |
| FMP auth (VIX quote) | No | DEGRADED |
| FMP economic calendar | No | DEGRADED |
| Cache directory writable | No | DEGRADED |

DEGRADED mode: engine runs, missing checks logged as WARNING.

---

## What Is Deliberately Frozen / Out of Scope

- **Pre-market / AH scanning**: `_is_market_hours()` enforces NYSE 09:30–16:00 ET only
- **Options**: equities only; no options chain access
- **Earnings transcripts**: not available on current FMP tier
- **ML scoring**: pure rule-based scanners only; no model inference
- **Crypto**: Alpaca crypto API not wired; equity-only

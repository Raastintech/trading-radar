# ARCHIVED / HISTORICAL DOCUMENT
> OBSOLETE — DO NOT USE FOR CURRENT OPERATIONS.
>
> This consolidated master doc preserves an earlier platform doctrine and stack
> definition. It contains superseded assumptions, including obsolete VOYAGER and
> active-strategy definitions.
>
> Current operating truth now lives in:
> - `docs/strategy/CURRENT_DOCTRINE_MAP.md`
> - `docs/strategy/CURRENT_READINESS.md`
> - `docs/strategy/STRATEGY_DOCTRINE.md`
> - current active sleeve specs / scorecards
>
> Keep this file only as historical context.

# SniperTradingAI — Master Documentation
> Single consolidated reference for all strategy, architecture, operations, and build context.
> Last updated: 2026-04-14

---

## Table of Contents
1. [Mission & North Star](#1-mission--north-star)
2. [Strategy Mandates (Doctrine)](#2-strategy-mandates-doctrine)
3. [System Architecture](#3-system-architecture)
4. [Readiness & Validation](#4-readiness--validation)
5. [Master Build Roadmap](#5-master-build-roadmap)
6. [Options Strategy Spec](#6-options-strategy-spec)
7. [Breakout Timing Engine Blueprint](#7-breakout-timing-engine-blueprint)
8. [Backtesting — SHORT Strategy](#8-backtesting--short-strategy)
9. [Phase 5 Structural Fixes (CODEX)](#9-phase-5-structural-fixes-codex)
10. [Research Archive](#10-research-archive)
11. [Daily Build Notes](#11-daily-build-notes)
12. [Trade Log Reference](#12-trade-log-reference)
13. [Operations Reference](#13-operations-reference)

---

## 1. Mission & North Star

### Core Mission
Build a fully autonomous, institutionally disciplined AI trading system that generates consistent alpha across multiple market regimes using edge-based strategies — not luck, noise, or overfit.

### Positioning
- **Not a scanner.** Not a signal generator. A complete, self-governing trading system.
- Competes on process discipline, not speed (no HFT).
- Designed for small-account scalability up to $500K without strategy degradation.

### Strategy Stack (5 Strategies)
| ID | Name | Direction | Regime |
|----|------|-----------|--------|
| SNP | Sniper | LONG | Momentum breakout |
| VOY | Voyager | SHORT | Mean reversion |
| REM | Remora | LONG | Stealth accumulation |
| CON | Contrarian | LONG | Fear-regime reversal |
| SLV | Short Sleeve | SHORT | Earnings disappointment |

### Non-Negotiables
1. Every trade must have a defined stop and target before entry.
2. No strategy trades unless its veto council clears it.
3. FMP + Alpaca are the only authoritative data sources in production.
4. No yfinance, Alpha Vantage, or FRED in primary execution paths.
5. Paper mode first; live only after 30-trade validation per strategy.

### Decision Filter (Before Any Code Change)
- Does this generate verifiable alpha?
- Does this reduce risk?
- Does this improve operational reliability?
- If no to all three → defer.

### Long-Term Vision
Phase 3 target: fully autonomous 24/7 system managing $250K–$500K across 5 strategies, self-healing on data failure, self-reporting via dashboard, with human oversight only for risk-limit changes.

---

## 2. Strategy Mandates (Doctrine)

### 2.1 VOYAGER (Mean-Reversion SHORT)
**Mandate:** Short overextended large-caps on earnings miss + analyst downgrade + volume confirmation. Cover at mean reversion target or hard stop.

**Entry conditions:**
- EPS miss ≥ 5% vs consensus
- At least one analyst downgrade same day
- Price extended ≥ 2 SD above 20-day MA
- Volume ≥ 1.5× 20-day avg at signal

**Exit rules:**
- Target: 20-day MA (mean reversion)
- Stop: 3% above entry (hard)
- Time stop: 10 trading days max

**Veto triggers:**
- VIX > 35 (panic regime — shorts squeeze)
- Earnings report pending within 5 days (avoid re-event risk)
- Market-wide LONG bias day (SPY +1.5%+ at open)

### 2.2 SNIPER (Momentum Breakout LONG)
**Mandate:** Enter confirmed breakouts above key resistance on high-volume thrust. Ride trend with trailing stop.

**Entry conditions:**
- Price closes above prior 52-week high OR multi-month consolidation breakout
- Volume ≥ 2× 50-day avg on breakout candle
- RS rank ≥ 85 (price strength vs S&P 500)
- No earnings within 10 days

**Exit rules:**
- Trailing stop: 7% from recent swing high
- Target: measured move (range × 1.5 minimum)
- Time stop: 20 trading days max if target not reached

**Veto triggers:**
- VIX > 28
- Sector ETF negative on breakout day
- Market breadth < 40% stocks above 50-day MA

### 2.3 REMORA (Stealth Accumulation LONG)
**Mandate:** Ride institutional accumulation in neglected mid-caps. Enter on quiet volume ramp with improving fundamentals.

**Entry conditions:**
- Volume trending up 3+ weeks, price flat or slightly positive
- Revenue growth ≥ 10% YoY, profitable
- Float < 50M shares
- No analyst coverage spike (stealth signal)

**Exit rules:**
- Target: 20–30% gain or breakout above prior resistance
- Stop: 8% below entry
- Hold period: up to 60 days

**Veto triggers:**
- Market in confirmed downtrend (SPY below 200-day MA)
- Sector showing distribution pattern

### 2.4 CONTRARIAN (Fear-Regime Reversal LONG)
**Mandate:** Buy quality names sold off in fear spikes (VIX > 25). Capture snap-back within 5–10 days.

**Entry conditions:**
- VIX ≥ 25 and spiking
- Target stock down ≥ 8% on no company-specific news
- Strong balance sheet (debt/equity < 1.0)
- 52-week RS > 60 before selloff

**Exit rules:**
- Target: 50% retracement of selloff move
- Stop: 5% below entry
- Time stop: 10 trading days

**Veto triggers:**
- Company-specific catalyst (earnings, FDA, legal) causing the drop
- VIX > 40 (systemic crisis — no longs)

### 2.5 SHORT SLEEVE (Earnings Disappointment SHORT)
**Mandate:** Short stocks the night before / day of expected earnings miss. Close within 1–3 days.

**Entry conditions:**
- Pre-earnings: consensus estimates degrading last 30 days
- Earnings Surprise Score (ESS) negative 2+ consecutive quarters
- Stock at or above pre-earnings run-up level
- Options IV confirms downside skew (pending FMP Ultimate)

**Exit rules:**
- Target: 5–15% down from entry
- Stop: 4% above entry
- Time stop: 3 trading days (earnings decay)

**Veto triggers:**
- Market-wide gap-up day (SPY +1%+ pre-market)
- Stock already down ≥ 10% from prior high (already sold)
- Analyst upgrade in prior 48 hours

### 2.6 Cross-Strategy Rules
- Max 3 strategies active simultaneously
- Max portfolio risk: 6% total drawdown trigger for all-strategy pause
- No two strategies enter same ticker same day
- Strategy priority if conflict: SLV > VOY > SNP > CON > REM

### 2.7 Retail-Edge Test
Before each entry, the system asks: "Would a retail trader be tempted to do the opposite?" If yes → stronger conviction signal. If no → review thesis.

---

## 3. System Architecture

### 3.1 Canonical Runtime (Production)
```
/home/gem/trading-production/
├── main.py                    # systemd entry point
├── gem-trader.service         # systemd unit file
├── requirements.txt
├── core/
│   ├── config.py              # Reads /home/gem/secure/trading.env
│   ├── fmp_client.py          # FMP Starter client + Gatekeeper cache
│   ├── data_gatekeeper.py     # SQLite metadata + Parquet price cache
│   └── alpaca_client.py       # Alpaca Pro Plus execution + OHLCV
├── strategies/
│   ├── sniper.py              # FROZEN during backtest phase
│   ├── voyager.py             # FROZEN
│   ├── remora.py              # FROZEN
│   ├── contrarian.py          # FROZEN
│   └── short_sleeve.py        # ACTIVE — backtesting
├── council/
│   └── veto_council.py        # 3 hard-veto + 6 soft-score agents
├── db/
│   └── trading_performance.db # decisions, veto_log, trades, macro_events
└── legacy/                    # 112 migrated files from Mac
    ├── multi_sleeve_short_research.py
    ├── short_backtester.py
    ├── research_data_provider.py
    ├── fundamental_data_fetcher.py
    └── earnings_event_store.py
```

### 3.2 Data Sources
| Source | Purpose | Plan |
|--------|---------|------|
| FMP | Earnings, fundamentals, economic calendar, VIX, news | Starter Annual (300 calls/min) |
| Alpaca | OHLCV bars (SIP feed), order execution, positions | Pro Plus |
| yfinance | Debug fallback only — never primary path | None |

**FMP daily call budget:** `FMP_DAILY_BUDGET_TARGET = 3000` (conservative; plan allows far more)

### 3.3 Module Boundaries
- `research_data_provider.py` — all external data routing (FMP primary → AV secondary → yf debug)
- `fundamental_data_fetcher.py` — fundamental data; FMP primary via direct HTTP (no circular import)
- `earnings_event_store.py` — builds earnings event cache using RoutingEarningsProvider
- `short_backtester.py` — price history engine; normalizes Alpaca bar timestamps with `.normalize()`
- `multi_sleeve_short_research.py` — orchestrates 3 SHORT sleeves; Sleeve B uses `_build_fmp_event_store()`

### 3.4 V2 Module Migration Matrix
| Module | Status |
|--------|--------|
| fmp_client.py | MIGRATE |
| data_gatekeeper.py | MIGRATE |
| alpaca_client.py | MIGRATE |
| veto_council.py | MIGRATE |
| decision_logger.py | MIGRATE |
| sniper.py | MIGRATE (FROZEN) |
| voyager.py | MIGRATE (FROZEN) |
| remora.py | MIGRATE (FROZEN) |
| contrarian.py | MIGRATE (FROZEN) |
| short_sleeve.py | MIGRATE (ACTIVE) |
| macro_calendar.py | DEFER |
| live_dashboard.py | DEFER (local dev only) |
| live_dashboard_v3.py | DEFER (local dev only) |
| options overlay | DEFER (needs FMP Ultimate) |
| legacy scanner tools | RETIRE |

### 3.5 Key Design Rules
1. Strategies do NOT call data APIs directly — all data through core/ or research_data_provider.
2. DB writes go through decision_logger.py only (single write path).
3. Alpaca paper mode is default; live mode requires explicit `ALPACA_LIVE_MODE=true` env var.
4. All timestamps normalized to US/Eastern before any comparison.
5. Parquet cache invalidation: 1 day for price data, 90 days for fundamentals.

---

## 4. Readiness & Validation

### 4.1 Layer Status
| Layer | Status | Notes |
|-------|--------|-------|
| Layer 1 — Infrastructure | GREEN | Ubuntu server running, systemd active, credentials secured |
| Layer 2 — Data | AMBER | FMP+Alpaca wired; ETF earnings noise fixed; gap_pct bug fixed |
| Layer 3 — Backtesting | AMBER-RED | SHORT backtester migrated; 0-event bug fixed; needs live run |
| Layer 4 — Live Validation | RED | No strategies yet validated to 30-trade threshold |

### 4.2 Proven (Validated)
- FMP Starter plan successfully replaces yfinance/AV for earnings and fundamentals
- Alpaca Pro Plus delivers reliable 1-min and daily bars
- systemd gem-trader.service starts reliably; paper equity ~$80,492
- Veto council logic (3 hard + 6 soft) running without errors
- ETF detection: TQQQ/SQQQ/UPRO etc. correctly silenced (no ERROR logs)
- gap_pct bug: fixed by `.normalize()` on Alpaca bar DatetimeIndex

### 4.3 Open Validation Items
| Item | Status | Owner |
|------|--------|-------|
| Voyager short exit telemetry | OPEN | Needs 30 paper trades |
| Live SHORT sleeve exit timing | OPEN | Backtester run required first |
| Options overlay (IV skew input) | OPEN | Blocked on FMP Ultimate upgrade |
| Sleeve B 300-symbol backtest | OPEN | Ready to run; needs Ubuntu execution |
| Sniper BTE advisory mode | OPEN | Phase 2 build |
| Contrarian VIX-regime wiring | OPEN | Phase 2 build |

---

## 5. Master Build Roadmap

### Phase 1 — Foundation (COMPLETE)
- Ubuntu server live with systemd
- FMP + Alpaca wired, credentials secured
- All 5 strategies coded (4 FROZEN, 1 ACTIVE)
- Veto council (3 hard-veto + 6 soft-score) running
- 112 legacy files migrated to production/legacy/
- SHORT backtester stack deployed with FMP-primary data

### Phase 2 — Validation (CURRENT)
**Goal:** Generate 30 paper trades per strategy with documented entry/exit rationale.

Milestones:
- [ ] Run Sleeve B 300-symbol SHORT backtest → identify promotable setup
- [ ] Validate SHORT sleeve exit timing (intraday vs close)
- [ ] Run Voyager 6-month paper: 30 trades target
- [ ] Review Sniper breakout filter (add BTE advisory)
- [ ] Contrarian: wire VIX-regime entry gating
- [ ] Remora: validate volume accumulation proxy

### Phase 3 — Live Deployment (FUTURE)
**Trigger:** All 5 strategies pass 30-trade paper validation with Sharpe > 1.0

- Enable Alpaca live mode (ALPACA_LIVE_MODE=true)
- Options overlay (requires FMP Ultimate upgrade)
- Slack alerting for entries/exits/veto blocks
- Daily automated P&L report
- Monthly strategy review cadence

### Operating Rules (All Phases)
1. Never deploy a strategy live that hasn't completed Phase 2 validation.
2. Never modify a FROZEN strategy during backtesting of another.
3. All config changes via environment variables only — no hardcoded keys.
4. Any change to risk parameters requires explicit note in trade_log.

### Change Discipline
- Before any code change: state what it fixes and why now.
- After any code change: run the affected module's test or backtester pass.
- Commits must include the strategy tag: [SNP], [VOY], [REM], [CON], [SLV], [INFRA].

---

## 6. Options Strategy Spec

### 6.1 Phase 1 Scope
- **Defined-risk spreads first** (vertical debit/credit spreads, no naked positions)
- Applies to: SHORT SLEEVE (pre-earnings) and CONTRARIAN (fear spike)
- Requires FMP Ultimate for real-time IV and options chain data

### 6.2 DB Tables (Options)
```sql
CREATE TABLE IF NOT EXISTS options_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    ticker TEXT,
    strategy TEXT,
    spread_type TEXT,          -- 'debit_put_spread', 'credit_call_spread', etc.
    long_strike REAL,
    short_strike REAL,
    expiry TEXT,               -- YYYY-MM-DD
    contracts INTEGER,
    entry_debit REAL,          -- net debit paid (positive = paid, negative = received)
    max_profit REAL,
    max_loss REAL,
    opened_at TEXT,
    closed_at TEXT,
    exit_credit REAL,
    realized_pnl REAL,
    status TEXT                -- 'open', 'closed', 'expired'
);

CREATE TABLE IF NOT EXISTS options_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    ticker TEXT,
    strategy TEXT,
    signal_time TEXT,
    iv_rank REAL,              -- 0–100
    put_call_ratio REAL,
    skew_score REAL,           -- downside skew indicator
    recommended_spread TEXT,
    recommended_expiry TEXT,
    confidence REAL,
    acted_on INTEGER DEFAULT 0
);
```

### 6.3 Integration Rules
- Options module is advisory-only until FMP Ultimate active
- Equity position takes priority if options chain unavailable
- Max options allocation: 15% of portfolio per strategy
- Minimum IV rank: 40 before selling premium; max IV rank: 85 before buying

### 6.4 Build Prompt (For Future Session)
When FMP Ultimate is available:
1. Add `fmp_options_client.py` to core/ — wraps FMP options chain endpoint
2. Add `options_signal_generator.py` to strategies/ — scores IV rank, put/call ratio, skew
3. Wire into short_sleeve.py and contrarian.py as optional signal layer
4. Log all signals to options_signals table regardless of action taken
5. Paper test options vs equity for 30 trades before going live

---

## 7. Breakout Timing Engine Blueprint

### 7.1 Purpose
Advisory-only subsystem that improves SNIPER entry timing within confirmed breakout setups. Does not override veto council. Does not generate new signals.

**BTE is not a signal generator.** It answers: "Given a confirmed breakout, is NOW the optimal entry bar?"

### 7.2 Timing Signals
| Signal | Weight | Source |
|--------|--------|--------|
| Intraday volume surge (vs 10-day avg) | 30% | Alpaca 1-min bars |
| Price above prior day high | 20% | Alpaca daily bars |
| VIX declining on breakout day | 20% | FMP VIX |
| Sector ETF confirming | 15% | Alpaca sector ETF bars |
| Time-of-day (avoid first 15 min, last 30 min) | 15% | System clock |

### 7.3 Output
- BTE score: 0–100
- BTE recommendation: ENTER / WAIT / SKIP
- ENTER threshold: score ≥ 70
- Advisory only: SNIPER still enters if veto council clears regardless of BTE score

### 7.4 Build Phases
1. **Phase 1:** Add BTE score to veto_log for monitoring (no action gating)
2. **Phase 2:** Surface BTE score in live dashboard
3. **Phase 3:** Use BTE to delay entry by up to 30 minutes (soft gate)
4. **Phase 4:** Backtest BTE-gated vs ungated entries over 2-year window

### 7.5 Implementation Notes
- BTE runs after veto council PASS, before order submission
- BTE timeout: 30 seconds max (never blocks execution)
- BTE failure → default ENTER (fail-open)
- Log BTE score to decisions table: add `bte_score` column

---

## 8. Backtesting — SHORT Strategy

### 8.1 Role & Goal
Backtest the SHORT SLEEVE strategy across 300 symbols over 5 years to identify the highest-conviction setup geometry (Sleeve B: ranked portfolio approach).

**Optimize for:** Calmar ratio (CAGR / max drawdown). Not raw return.

### 8.2 Required Inputs
- FMP_API_KEY (Starter Annual)
- Alpaca API credentials
- Universe: 300 symbols (Russell 1000 liquid subset)
- Period: 5 years
- Sleeve: B (ranked portfolio)

### 8.3 Run Command (Ubuntu)
```bash
cd /home/gem/trading-production/legacy
python3 multi_sleeve_short_research.py \
  --universe 300 \
  --mode sleeve_b \
  --period 5y \
  --out sleeve_b_300_fmp.json
```

### 8.4 Known Fixes Applied
| Bug | Fix |
|-----|-----|
| gap_pct=None (0 events verified) | Added `.normalize()` to DatetimeIndex in `_normalize_price_frame()` in short_backtester.py. Alpaca bars arrive as "2024-01-15 05:00:00 UTC"; after tz-strip they don't match midnight-normalized earnings dates. |
| ETF log noise (TQQQ, SQQQ, etc.) | FMPEarningsProvider checks `isEtf` flag via cached profile call when earnings empty. RoutingEarningsProvider short-circuits at DEBUG level for ETFs. |
| yfinance ImportError on Ubuntu | Made import conditional: `try: import yfinance as yf; _HAS_YFINANCE = True; except ImportError: yf = None; _HAS_YFINANCE = False` |
| Circular import risk | fundamental_data_fetcher.py uses direct `requests` HTTP to FMP (no import of research_data_provider) |

### 8.5 Guardrails
- Never modify strategy logic during backtest run
- Run Sleeve B only (Sleeve A = naive, Sleeve C = requires live data)
- If any symbol fails with FMP error, skip symbol and log to failed_symbols.json
- Stop run if FMP daily budget (3000 calls) would be exceeded; resume next day

### 8.6 Output Contract
```json
{
  "sleeve": "B",
  "universe_size": 300,
  "period": "5y",
  "total_trades": "...",
  "win_rate": "...",
  "calmar_ratio": "...",
  "sharpe_ratio": "...",
  "max_drawdown_pct": "...",
  "avg_hold_days": "...",
  "promotable": true/false,
  "promotion_blocker": "...",
  "top_setups": [...]
}
```

### 8.7 Promotion Criteria
Sleeve B is promotable to live paper trading if:
- Calmar ratio ≥ 1.0
- Win rate ≥ 45%
- Max drawdown ≤ 20%
- At least 100 qualifying trades in backtest window
- No sign-flip at universe scale (return doesn't invert when going from 50 → 300 symbols)

---

## 9. Phase 5 Structural Fixes (CODEX)

### 9.1 Fix 1 — Options PCR Signal
**Problem:** SHORT SLEEVE enters without IV/skew confirmation, reducing edge.
**Fix:** Add put/call ratio from FMP options data as soft veto score (not hard block).
**Blocked by:** FMP Ultimate required for real-time options chain.
**Workaround:** Use historical PCR from FMP earnings data as proxy until upgrade.

### 9.2 Fix 2 — SHORT Risk/Reward Geometry
**Problem:** Current SHORT SLEEVE uses flat 4% stop / 10% target regardless of setup volatility.
**Fix:** Scale stop to 1× ATR(14), target to 2.5× ATR(14). Log ATR at entry to decisions table.
**Status:** Ready to implement in short_sleeve.py.

### 9.3 Fix 3 — REMORA Risk/Reward
**Problem:** Remora uses static 8% stop — too wide for slow accumulation plays.
**Fix:** Dynamic stop: 2× ATR(20) below entry, min 4%, max 10%.
**Status:** FROZEN — implement in Phase 2 validation.

### 9.4 Fix 4 — Circuit Breaker
**Problem:** No system-wide halt when portfolio drawdown exceeds threshold.
**Fix:** Add circuit breaker to main.py: if total portfolio down ≥ 6% from session open → pause all new entries for remainder of day.
**Status:** Ready to implement in main.py.

### 9.5 Fix 5 — Contrarian VIX Wiring
**Problem:** Contrarian strategy not actually checking VIX at entry time (VIX check was bypassed).
**Fix:** Wire FMP VIX endpoint into veto_council hard-veto #3: VIX < 25 → hard block for CONTRARIAN.
**Status:** FROZEN — implement in Phase 2.

### 9.6 Fix 6 — R/R Gap Reporting
**Problem:** No post-trade R/R analysis — can't tell if stops/targets are properly sized.
**Fix:** Add to decision_logger: after close, compute actual_rr = realized_pnl / (entry - stop_loss). Log to decisions table.
**Status:** Ready to implement in decision_logger.py.

---

## 10. Research Archive

### 10.1 Completed Backtests
| Test | Result | Promotable |
|------|--------|-----------|
| crowded_loser_unwind (Sleeve A, 50 symbols, 2y) | Sharpe 0.71, Calmar 0.43, win rate 38% | NO |
| short_ranked_portfolio_v2 (Sleeve B, 50 symbols, 2y) | Sharpe 1.12, Calmar 0.89 | CONDITIONAL |
| short_ranked_portfolio_v2 (Sleeve B, 300 symbols, 2y) | Sign-flip: return inverted at scale | NOT PROMOTABLE |

### 10.2 Key Findings
**crowded_loser_unwind:** Short selling based on crowding metrics alone (short interest, borrow cost) does not produce consistent edge. Win rate below 40% is unacceptable for a strategy with 1:1 R/R.

**short_ranked_portfolio_v2 sign-flip at scale:** The ranked portfolio approach showed positive results at 50-symbol universe but inverted returns at 300-symbol scale. Root cause: ranking function weights degrade when universe is large and diverse. Possible fix: add sector neutrality constraint to ranking (rank within sector, not cross-sector).

### 10.3 Next Backtest
Run Sleeve B at 300 symbols with:
- Sector neutrality constraint (rank within GICS sector)
- ATR-scaled R/R (Fix 2 from Phase 5)
- FMP-primary data (not yfinance)
- 5-year window (not 2-year)

---

## 11. Daily Build Notes

### 2026-04-09
**Remora macro-sympathy fix:** Remora was entering on volume ramps that coincided with macro events (FOMC days, CPI releases). Added macro_calendar check: if macro event within 1 day → skip Remora entry.

**Voyager accumulation proxy:** FMP doesn't have short interest data on Starter plan. Using volume/price divergence (price flat + volume rising on up days) as proxy for institutional accumulation until FMP Ultimate.

**Revenue growth fallback:** FMP income statement sometimes missing revenue for micro-caps. Fallback chain: `revenue` → `totalRevenue` → `netRevenue`. If all missing → filter ticker from universe.

### 2026-04-10
**5-minute daemon loop:** main.py now runs on 5-minute loop (was 1-minute). Rationale: FMP Starter at 300 calls/min — 1-minute loop was burning budget on idle scans. 5-minute loop reduces daily FMP calls by ~80% with minimal signal delay for swing strategies.

**Sniper VIX curve:** Added VIX regime curve to Sniper veto: VIX 20–28 → reduce position size 50%; VIX > 28 → hard block. Previously only had VIX > 28 hard block with no size scaling.

**SHORT geometry rework:** Moved from static stops/targets to ATR-scaled geometry (Phase 5 Fix 2). ATR(14) calculated at entry. Stop = 1× ATR, target = 2.5× ATR. Average trade R/R improved from 1:2.5 (static) to 1:2.5 (dynamic) — same ratio but better fit to volatility.

---

## 12. Trade Log Reference

The live trade log is maintained in `trading_performance.db` → `decisions` table.

### Key Fields
| Field | Description |
|-------|-------------|
| run_id | Unique session identifier |
| ticker | Symbol traded |
| strategy | SNIPER / VOYAGER / REMORA / CONTRARIAN / SHORT_SLEEVE |
| direction | LONG / SHORT |
| shares | Quantity |
| stop_loss | Stop price at entry |
| target_price | Target price at entry |
| order_id | Alpaca order ID |
| position_opened | Timestamp |

### Query Examples
```sql
-- All open positions
SELECT ticker, strategy, direction, stop_loss, target_price
FROM decisions
WHERE position_opened IS NOT NULL
ORDER BY position_opened DESC;

-- Strategy win rate (closed trades)
SELECT strategy,
       COUNT(*) as trades,
       SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
       AVG(realized_pnl) as avg_pnl
FROM decisions
WHERE position_closed IS NOT NULL
GROUP BY strategy;
```

---

## 13. Operations Reference

### 13.1 Server
- **Host:** 192.168.0.40
- **User:** gem
- **Credentials:** /home/gem/secure/trading.env
- **Working dir:** /home/gem/trading-production/

### 13.2 Start/Stop
```bash
# Canonical — via systemd
sudo systemctl start gem-trader
sudo systemctl stop gem-trader
sudo systemctl status gem-trader
sudo journalctl -u gem-trader -f

# Or scripts
./start_trader.sh
./stop_trader.sh
./check_status.sh
```

### 13.3 Deploy from Mac
```bash
cd ~/Desktop/SniperTradingAI
rsync -avz --progress production/ gem@192.168.0.40:/home/gem/trading-production/
```

### 13.4 Run SHORT Backtester
```bash
cd /home/gem/trading-production/legacy
python3 multi_sleeve_short_research.py \
  --universe 300 \
  --mode sleeve_b \
  --period 5y \
  --out sleeve_b_300_fmp.json
```

### 13.5 Scan Specific Tickers
```bash
./scan.sh AAPL MSFT TSLA
```

### 13.6 Environment Variables
```bash
# Required in /home/gem/secure/trading.env
FMP_API_KEY=...
ALPACA_API_KEY=...
ALPACA_SECRET_KEY=...
ALPACA_BASE_URL=https://paper-api.alpaca.markets  # paper mode
# ALPACA_BASE_URL=https://api.alpaca.markets      # live mode (explicit)
ALPACA_LIVE_MODE=false                             # must be 'true' for live

# Optional tuning
FMP_DAILY_BUDGET_TARGET=3000
RESEARCH_ALLOW_YFINANCE_DEBUG=false
```

### 13.7 Install / Update Dependencies
```bash
cd /home/gem/trading-production
pip install -r requirements.txt
# If using venv:
.venv/bin/pip install -r requirements.txt
```

### 13.8 Data Source Priority (Non-Negotiable)
1. **FMP** — primary for all fundamentals, earnings, VIX, economic calendar
2. **Alpaca** — primary for OHLCV bars and order execution
3. **yfinance** — debug fallback only; `RESEARCH_ALLOW_YFINANCE_DEBUG=false` by default

Never use yfinance, Alpha Vantage, or FRED in primary execution paths. If FMP doesn't support a data point, mark it `None` / `"pending_fmp_upgrade"`.

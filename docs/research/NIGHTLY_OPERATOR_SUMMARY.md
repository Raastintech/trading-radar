======================================================================
  RESEARCH_ONLY_MODE — NO AUTO TRADING — HUMAN REVIEW ONLY
  Broker execution, paper-trade routing, and Alpaca are disabled.
======================================================================

# Nightly Operator Summary — 2026-07-04

**Generated:** 00:44 UTC  |  **Mode:** RESEARCH_ONLY  |  **Version:** NIGHTLY_OPERATOR_SUMMARY_V1

---

## 1. Overall Status

**✓ PASS** — All primary artifacts present
- Research-only safety: ACTIVE

## 2. Market Context

**Regime:** Chop / Range (conf: LOW, 13m ago)
- 5d: constructive  |  10d: constructive  |  30d: mixed
- Weak sectors: XLB, XLE
- Research posture: stalking mode — do not promote ideas during stress; watchlist only; human review required before any action

## 3. Alpha Radar Snapshot

**Total candidates:** 101
- HIGH_PRIORITY_RESEARCH: 2: SGRY, SDGR
- WATCHLIST_RESEARCH: 18: NVO, HOOD, CNNE, NTNX, ... +14 more
- RESET_WATCH: 7: FCEL, EVH, CUE, PENG, ... +3 more
- RECLAIM_WATCH: 0
- EXTENDED_CROWDED: 51: AGL, RXT, MXL, BLZE, ... +47 more
- DATA_QUARANTINE: 6 (INSUFFICIENT_HISTORY: 4; DATA_QUARANTINE: 2)
- Options overlay: DISABLED

## 4. Best Research Names to Review

*Research candidates only — no trade ideas, no buy/sell signals.*

**High-priority review:**

- **SGRY** | EARLY_ACCUMULATION | sector=Healthcare | confidence=HIGH
  - Why appeared: Rising volume + improving RS or higher lows; not extended
- **SDGR** | EARLY_ACCUMULATION | sector=Technology | confidence=HIGH
  - Why appeared: Rising volume + improving RS or higher lows; not extended

**Secondary reset/reclaim watch:**

- **FCEL** | RS_MOMENTUM_LEADER | sector=Industrials | confidence=HIGH
  - Why appeared: Strong RS vs SPY: 20d=+26.6pp, 63d=+309.5pp | above MA50 + MA200
- **EVH** | RS_MOMENTUM_LEADER | sector=Healthcare | confidence=HIGH
  - Why appeared: Strong RS vs SPY: 20d=+50.7pp, 63d=+140.8pp | above MA50 + MA200
- **CUE** | RS_MOMENTUM_LEADER | sector=Healthcare | confidence=HIGH
  - Why appeared: Strong RS vs SPY: 20d=+4.2pp, 63d=+341.9pp | above MA50 + MA200
- **PENG** | RS_MOMENTUM_LEADER | sector=Technology | confidence=HIGH
  - Why appeared: Strong RS vs SPY: 20d=-12.9pp, 63d=+222.3pp | above MA50 + MA200
- **CLOV** | RS_MOMENTUM_LEADER | sector=Healthcare | confidence=HIGH
  - Why appeared: Strong RS vs SPY: 20d=+47.3pp, 63d=+199.9pp | above MA50 + MA200

**RS Momentum + Watchlist Research:**

- **NVO** | SECTOR_LEADER | sector=Healthcare | confidence=HIGH
  - Why appeared: Outperforming SPY by +21.0pp over 20d; above 50d MA
- **HOOD** | SECTOR_LEADER | sector=Financial Services | confidence=HIGH
  - Why appeared: Outperforming SPY by +36.4pp over 20d; above 50d MA
- **CNNE** | BEATEN_DOWN | sector=Consumer Cyclical | confidence=HIGH
  - Why appeared: Large drawdown (28%/3m) with stabilization pattern
- **NTNX** | BEATEN_DOWN | sector=Technology | confidence=HIGH
  - Why appeared: Large drawdown (35%/3m) with stabilization pattern
- **FUN** | BEATEN_DOWN | sector=Consumer Cyclical | confidence=HIGH
  - Why appeared: Large drawdown (20%/3m) with stabilization pattern
- **ACVA** | BEATEN_DOWN | sector=Consumer Cyclical | confidence=HIGH
  - Why appeared: Large drawdown (70%/3m) with stabilization pattern
- **NMAX** | BEATEN_DOWN | sector=Communication Services | confidence=HIGH
  - Why appeared: Large drawdown (63%/3m) with stabilization pattern
- **XRX** | BEATEN_DOWN | sector=Industrials | confidence=HIGH
  - Why appeared: Large drawdown (130%/3m) with stabilization pattern

## 5. Forward Evidence

**Total entries:** 1361  |  **New today:** 101  |  **Matured 5d:** 494  |  **Matured 10d:** 142
- Sample status: ROBUST
- Benchmark readiness: READY — SPY 142, QQQ 142 entries with 10d return
- Verdict: **MIXED**
- Alpha proven: NO — insufficient evidence

## 6. Biggest Warnings

- ⚠ Scanner recall low at 1.8% — main miss: FILTER_TOO_STRICT (simple-RS baseline: 25.4%)
- ⚠ Options overlay: DISABLED — insufficient coverage
- ⚠ Targeted backfill plan: 8 tickers need >=300 bars — run targeted-backfill --execute to fill

## 7. Next Operator Actions

1. Review 2 high-priority research name(s) manually: SGRY, SDGR
2. Run targeted-backfill --execute --limit 8 --max-provider-calls 15 to fill 8 research names below 300-bar floor.
3. Run nightly again tomorrow.

---

*Research-only engine. Not a signal. Not a recommendation. No live capital.*

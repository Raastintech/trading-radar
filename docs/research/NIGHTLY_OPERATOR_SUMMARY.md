======================================================================
  RESEARCH_ONLY_MODE — NO AUTO TRADING — HUMAN REVIEW ONLY
  Broker execution, paper-trade routing, and Alpaca are disabled.
======================================================================

# Nightly Operator Summary — 2026-07-01

**Generated:** 16:15 UTC  |  **Mode:** RESEARCH_ONLY  |  **Version:** NIGHTLY_OPERATOR_SUMMARY_V1

---

## 1. Overall Status

**✓ PASS** — All primary artifacts present
- Research-only safety: ACTIVE

## 2. Market Context

**Regime:** Chop / Range (conf: HIGH, 4m ago)
- 5d: neutral / chop  |  10d: neutral / chop  |  30d: mixed
- Weak sectors: XLRE, XLB, XLE, XLC
- Research posture: stalking mode — do not promote ideas during stress; watchlist only; human review required before any action

## 3. Alpha Radar Snapshot

**Total candidates:** 98
- HIGH_PRIORITY_RESEARCH: 0
- WATCHLIST_RESEARCH: 15: VCIG, AKTX, CVLT, FLNC, ... +11 more
- RESET_WATCH: 3: STI, UMC, TECH
- RECLAIM_WATCH: 7: PAYX, RCAT, SDGR, ONON, ... +3 more
- EXTENDED_CROWDED: 33: PENG, VPG, AGL, PIII, ... +29 more
- DATA_QUARANTINE: 25 (INSUFFICIENT_HISTORY: 23; DATA_QUARANTINE: 2)
- Options overlay: DISABLED

## 4. Best Research Names to Review

*Research candidates only — no trade ideas, no buy/sell signals.*

**Secondary reset/reclaim watch:**

- **STI** | RS_MOMENTUM_LEADER | sector=Industrials | confidence=HIGH
  - Why appeared: Strong RS vs SPY: 20d=+344.6pp, 63d=+419.4pp | MA position unknown
- **UMC** | RS_MOMENTUM_LEADER | sector=Technology | confidence=HIGH
  - Why appeared: Strong RS vs SPY: 20d=+20.4pp, 63d=+197.9pp | above MA50 + MA200
- **TECH** | EARLY_ACCUMULATION | sector=Healthcare | confidence=HIGH
  - Why appeared: Rising volume + improving RS or higher lows; not extended
- **PAYX** | BEATEN_DOWN | sector=Industrials | confidence=HIGH
  - Why appeared: Large drawdown (10%/3m) with stabilization pattern
- **RCAT** | BEATEN_DOWN | sector=Technology | confidence=HIGH
  - Why appeared: Large drawdown (-25%/3m) with stabilization pattern

**RS Momentum + Watchlist Research:**

- **VCIG** | RS_MOMENTUM_LEADER | sector=Industrials | confidence=HIGH
  - Why appeared: Strong RS vs SPY: 20d=+591.1pp, 63d=+162.6pp | MA position unknown
- **AKTX** | RS_MOMENTUM_LEADER | sector=Healthcare | confidence=HIGH
  - Why appeared: Strong RS vs SPY: 20d=+372.0pp, 63d=+26.1pp | MA position unknown
- **CVLT** | EARLY_ACCUMULATION | sector=Technology | confidence=HIGH
  - Why appeared: Rising volume + improving RS or higher lows; not extended
- **FLNC** | EARLY_ACCUMULATION | sector=Utilities | confidence=HIGH
  - Why appeared: Rising volume + improving RS or higher lows; not extended
- **CPSH** | EARLY_ACCUMULATION | sector=Technology | confidence=HIGH
  - Why appeared: Rising volume + improving RS or higher lows; not extended
- **RGTI** | SECTOR_LEADER | sector=Technology | confidence=HIGH
  - Why appeared: Outperforming SPY by +10.3pp over 20d; above 50d MA
- **QBTS** | SECTOR_LEADER | sector=Technology | confidence=HIGH
  - Why appeared: Outperforming SPY by +9.3pp over 20d; above 50d MA
- **BAX** | BEATEN_DOWN | sector=Healthcare | confidence=HIGH
  - Why appeared: Large drawdown (20%/3m) with stabilization pattern

## 5. Forward Evidence

**Total entries:** 1000  |  **New today:** 22  |  **Matured 5d:** 75  |  **Matured 10d:** 2
- Sample status: TOO_EARLY
- Benchmark readiness: PARTIAL — SPY: 2 entries with 10d return, QQQ: 2
- Verdict: **NEED_MORE_DATA**
- Alpha proven: NO — insufficient evidence

## 6. Biggest Warnings

- ⚠ Scanner recall low at 1.8% — main miss: FILTER_TOO_STRICT (simple-RS baseline: 24.4%)
- ⚠ Forward evidence immature: 75 matured 5d, 2 matured 10d / 1000 total — do not change scoring
- ⚠ Benchmark coverage: PARTIAL (SPY: 2 entries with 10d return, QQQ: 2) — no Phase 4B until benchmarked
- ⚠ Options overlay: DISABLED — insufficient coverage
- ⚠ Targeted backfill plan: 25 tickers need >=300 bars — run targeted-backfill --execute to fill

## 7. Next Operator Actions

1. Do not change scoring until forward outcomes mature.
2. No Phase 4B until enough benchmarked forward evidence exists.
3. Run targeted-backfill --execute --limit 25 --max-provider-calls 15 to fill 25 research names below 300-bar floor.
4. Run nightly again tomorrow.

---

*Research-only engine. Not a signal. Not a recommendation. No live capital.*

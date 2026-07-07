======================================================================
  RESEARCH_ONLY_MODE — NO AUTO TRADING — HUMAN REVIEW ONLY
  Broker execution, paper-trade routing, and Alpaca are disabled.
======================================================================

# Nightly Operator Summary — 2026-07-07

**Generated:** 00:36 UTC  |  **Mode:** RESEARCH_ONLY  |  **Version:** NIGHTLY_OPERATOR_SUMMARY_V1

---

## 1. Overall Status

**✓ PASS** — All primary artifacts present
- Research-only safety: ACTIVE

## 2. Market Context

**Regime:** Bull Continuation (conf: LOW, 6m ago)
- 5d: constructive  |  10d: constructive  |  30d: mixed
- Weak sectors: XLE
- Research posture: selective — avoid extended names; favor reset/reclaim watchlists; human review required before any action

## 3. Alpha Radar Snapshot

**Total candidates:** 104
- HIGH_PRIORITY_RESEARCH: 3: SGRY, SDGR, NVO
- WATCHLIST_RESEARCH: 22: JEF, RH, SLG, HOOD, ... +18 more
- RESET_WATCH: 7: WYY, PENG, UMC, AGYS, ... +3 more
- RECLAIM_WATCH: 1: CTAS
- EXTENDED_CROWDED: 46: AGL, RXT, CUE, MXL, ... +42 more
- DATA_QUARANTINE: 8 (INSUFFICIENT_HISTORY: 5; DATA_QUARANTINE: 3)
- Options overlay: DISABLED

## 4. Best Research Names to Review

*Research candidates only — no trade ideas, no buy/sell signals.*

**High-priority review:**

- **SGRY** | EARLY_ACCUMULATION | sector=Healthcare | confidence=HIGH
  - Why appeared: Rising volume + improving RS or higher lows; not extended
- **SDGR** | EARLY_ACCUMULATION | sector=Technology | confidence=HIGH
  - Why appeared: Rising volume + improving RS or higher lows; not extended
- **NVO** | EARLY_ACCUMULATION | sector=Healthcare | confidence=HIGH
  - Why appeared: Rising volume + improving RS or higher lows; not extended

**Secondary reset/reclaim watch:**

- **WYY** | RS_MOMENTUM_LEADER | sector=Technology | confidence=HIGH
  - Why appeared: Strong RS vs SPY: 20d=+36.0pp, 63d=+198.4pp | above MA50 + MA200
- **PENG** | RS_MOMENTUM_LEADER | sector=Technology | confidence=HIGH
  - Why appeared: Strong RS vs SPY: 20d=-4.1pp, 63d=+212.7pp | above MA50 + MA200
- **UMC** | RS_MOMENTUM_LEADER | sector=Technology | confidence=HIGH
  - Why appeared: Strong RS vs SPY: 20d=+25.0pp, 63d=+183.4pp | above MA50 + MA200
- **AGYS** | EARLY_ACCUMULATION | sector=Technology | confidence=HIGH
  - Why appeared: Rising volume + improving RS or higher lows; not extended
- **BLMN** | EARLY_ACCUMULATION | sector=Consumer Cyclical | confidence=HIGH
  - Why appeared: Rising volume + improving RS or higher lows; not extended

**RS Momentum + Watchlist Research:**

- **JEF** | EARLY_ACCUMULATION | sector=Financial Services | confidence=HIGH
  - Why appeared: Rising volume + improving RS or higher lows; not extended
- **RH** | SECTOR_LEADER | sector=Consumer Cyclical | confidence=HIGH
  - Why appeared: Outperforming SPY by +11.0pp over 20d; above 50d MA
- **SLG** | SECTOR_LEADER | sector=Real Estate | confidence=HIGH
  - Why appeared: Outperforming SPY by +10.1pp over 20d; above 50d MA
- **HOOD** | SECTOR_LEADER | sector=Financial Services | confidence=HIGH
  - Why appeared: Outperforming SPY by +33.8pp over 20d; above 50d MA
- **NTNX** | BEATEN_DOWN | sector=Technology | confidence=HIGH
  - Why appeared: Large drawdown (28%/3m) with stabilization pattern
- **ATRA** | BEATEN_DOWN | sector=Healthcare | confidence=HIGH
  - Why appeared: Large drawdown (118%/3m) with stabilization pattern
- **TTAN** | BEATEN_DOWN | sector=Technology | confidence=HIGH
  - Why appeared: Large drawdown (24%/3m) with stabilization pattern
- **CNNE** | BEATEN_DOWN | sector=Consumer Cyclical | confidence=HIGH
  - Why appeared: Large drawdown (20%/3m) with stabilization pattern

## 5. Forward Evidence

**Total entries:** 1564  |  **New today:** 104  |  **Matured 5d:** 564  |  **Matured 10d:** 179
- Sample status: ROBUST
- Benchmark readiness: READY — SPY 179, QQQ 179 entries with 10d return
- Verdict: **NO_FORWARD_EDGE**
- Alpha proven: NO — insufficient evidence

## 6. Biggest Warnings

- ⚠ Scanner recall low at 2.3% — main miss: FILTER_TOO_STRICT (simple-RS baseline: 34.0%)
- ⚠ Options overlay: DISABLED — insufficient coverage
- ⚠ Targeted backfill plan: 10 tickers need >=300 bars — run targeted-backfill --execute to fill

## 7. Next Operator Actions

1. Review 3 high-priority research name(s) manually: SGRY, SDGR, NVO
2. Run targeted-backfill --execute --limit 10 --max-provider-calls 15 to fill 10 research names below 300-bar floor.
3. Run nightly again tomorrow.

---

*Research-only engine. Not a signal. Not a recommendation. No live capital.*

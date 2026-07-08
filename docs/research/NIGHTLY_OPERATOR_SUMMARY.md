======================================================================
  RESEARCH_ONLY_MODE — NO AUTO TRADING — HUMAN REVIEW ONLY
  Broker execution, paper-trade routing, and Alpaca are disabled.
======================================================================

# Nightly Operator Summary — 2026-07-08

**Generated:** 00:46 UTC  |  **Mode:** RESEARCH_ONLY  |  **Version:** NIGHTLY_OPERATOR_SUMMARY_V1

---

## 1. Overall Status

**✓ PASS** — All primary artifacts present
- Research-only safety: ACTIVE

## 2. Market Context

**Regime:** Bull Continuation (conf: LOW, 16m ago)
- 5d: constructive  |  10d: constructive  |  30d: mixed
- Leading sectors: XLRE
- Weak sectors: XLK
- Research posture: selective — avoid extended names; favor reset/reclaim watchlists; human review required before any action

## 3. Alpha Radar Snapshot

**Total candidates:** 100
- HIGH_PRIORITY_RESEARCH: 5: SGRY, SDGR, ACVA, JPM, ... +1 more
- WATCHLIST_RESEARCH: 18: JEF, NVO, RDDT, TTAN, ... +14 more
- RESET_WATCH: 8: WYY, PENG, UMC, AGYS, ... +4 more
- RECLAIM_WATCH: 2: MSFT, CTAS
- EXTENDED_CROWDED: 45: AGL, RXT, CUE, MXL, ... +41 more
- DATA_QUARANTINE: 4 (INSUFFICIENT_HISTORY: 3; DATA_QUARANTINE: 1)
- Options overlay: DISABLED

## 4. Best Research Names to Review

*Research candidates only — no trade ideas, no buy/sell signals.*

**High-priority review:**

- **SGRY** | EARLY_ACCUMULATION | sector=Healthcare | confidence=HIGH
  - Why appeared: Rising volume + improving RS or higher lows; not extended
- **SDGR** | EARLY_ACCUMULATION | sector=Technology | confidence=HIGH
  - Why appeared: Rising volume + improving RS or higher lows; not extended
- **ACVA** | WATCH | sector=Consumer Cyclical | confidence=HIGH
  - Why appeared: Social attention signal (source: social_attention_radar)
- **JPM** | WATCH | sector=Financial Services | confidence=HIGH
  - Why appeared: Social attention signal (source: social_arb)
- **HOOD** | EARLY_ACCUMULATION | sector=Financial Services | confidence=HIGH
  - Why appeared: Rising volume + improving RS or higher lows; not extended

**Secondary reset/reclaim watch:**

- **WYY** | RS_MOMENTUM_LEADER | sector=Technology | confidence=HIGH
  - Why appeared: Strong RS vs SPY: 20d=+33.8pp, 63d=+199.5pp | above MA50 + MA200
- **PENG** | RS_MOMENTUM_LEADER | sector=Technology | confidence=HIGH
  - Why appeared: Strong RS vs SPY: 20d=-6.2pp, 63d=+213.8pp | above MA50 + MA200
- **UMC** | RS_MOMENTUM_LEADER | sector=Technology | confidence=HIGH
  - Why appeared: Strong RS vs SPY: 20d=+19.6pp, 63d=+160.7pp | above MA50 + MA200
- **AGYS** | EARLY_ACCUMULATION | sector=Technology | confidence=HIGH
  - Why appeared: Rising volume + improving RS or higher lows; not extended
- **BLMN** | EARLY_ACCUMULATION | sector=Consumer Cyclical | confidence=HIGH
  - Why appeared: Rising volume + improving RS or higher lows; not extended

**RS Momentum + Watchlist Research:**

- **JEF** | EARLY_ACCUMULATION | sector=Financial Services | confidence=HIGH
  - Why appeared: Rising volume + improving RS or higher lows; not extended
- **NVO** | SECTOR_LEADER | sector=Healthcare | confidence=HIGH
  - Why appeared: Outperforming SPY by +11.2pp over 20d; above 50d MA
- **RDDT** | SECTOR_LEADER | sector=Communication Services | confidence=HIGH
  - Why appeared: Outperforming SPY by +7.8pp over 20d; above 50d MA
- **TTAN** | BEATEN_DOWN | sector=Technology | confidence=HIGH
  - Why appeared: Large drawdown (24%/3m) with stabilization pattern
- **NTNX** | BEATEN_DOWN | sector=Technology | confidence=HIGH
  - Why appeared: Large drawdown (28%/3m) with stabilization pattern
- **ATRA** | BEATEN_DOWN | sector=Healthcare | confidence=HIGH
  - Why appeared: Large drawdown (118%/3m) with stabilization pattern
- **CNNE** | BEATEN_DOWN | sector=Consumer Cyclical | confidence=HIGH
  - Why appeared: Large drawdown (20%/3m) with stabilization pattern
- **NMAX** | BEATEN_DOWN | sector=Communication Services | confidence=HIGH
  - Why appeared: Large drawdown (59%/3m) with stabilization pattern

## 5. Forward Evidence

**Total entries:** 1668  |  **New today:** 100  |  **Matured 5d:** 589  |  **Matured 10d:** 214
- Sample status: ROBUST
- Benchmark readiness: READY — SPY 214, QQQ 214 entries with 10d return
- Verdict: **MIXED**
- Alpha proven: NO — insufficient evidence

## 6. Biggest Warnings

- ⚠ Scanner recall low at 2.0% — main miss: FILTER_TOO_STRICT (simple-RS baseline: 30.4%)
- ⚠ Options overlay: DISABLED — insufficient coverage
- ⚠ Targeted backfill plan: 8 tickers need >=300 bars — run targeted-backfill --execute to fill

## 7. Next Operator Actions

1. Review 5 high-priority research name(s) manually: SGRY, SDGR, ACVA, JPM, ... +1 more
2. Run targeted-backfill --execute --limit 8 --max-provider-calls 15 to fill 8 research names below 300-bar floor.
3. Run nightly again tomorrow.

---

*Research-only engine. Not a signal. Not a recommendation. No live capital.*

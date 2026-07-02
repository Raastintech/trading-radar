======================================================================
  RESEARCH_ONLY_MODE — NO AUTO TRADING — HUMAN REVIEW ONLY
  Broker execution, paper-trade routing, and Alpaca are disabled.
======================================================================

# Nightly Operator Summary — 2026-07-02

**Generated:** 19:26 UTC  |  **Mode:** RESEARCH_ONLY  |  **Version:** NIGHTLY_OPERATOR_SUMMARY_V1

---

## 1. Overall Status

**✓ PASS** — All primary artifacts present
- Research-only safety: ACTIVE

## 2. Market Context

**Regime:** Chop / Range (conf: HIGH, 31m ago)
- 5d: neutral / chop  |  10d: neutral / chop  |  30d: mixed
- Weak sectors: XLRE, XLC, XLB, XLE
- Research posture: stalking mode — do not promote ideas during stress; watchlist only; human review required before any action

## 3. Alpha Radar Snapshot

**Total candidates:** 103
- HIGH_PRIORITY_RESEARCH: 1: SDGR
- WATCHLIST_RESEARCH: 17: SGRY, CNNE, NTNX, FUN, ... +13 more
- RESET_WATCH: 7: FCEL, EVH, CUE, MXL, ... +3 more
- RECLAIM_WATCH: 1: CCL
- EXTENDED_CROWDED: 51: AGL, RXT, BLZE, APPS, ... +47 more
- DATA_QUARANTINE: 6 (INSUFFICIENT_HISTORY: 3; DATA_QUARANTINE: 3)
- Options overlay: DISABLED

## 4. Best Research Names to Review

*Research candidates only — no trade ideas, no buy/sell signals.*

**High-priority review:**

- **SDGR** | EARLY_ACCUMULATION | sector=Healthcare | confidence=HIGH
  - Why appeared: Rising volume + improving RS or higher lows; not extended

**Secondary reset/reclaim watch:**

- **FCEL** | RS_MOMENTUM_LEADER | sector=Industrials | confidence=HIGH
  - Why appeared: Strong RS vs SPY: 20d=+27.2pp, 63d=+308.5pp | above MA50 + MA200
- **EVH** | RS_MOMENTUM_LEADER | sector=Healthcare | confidence=HIGH
  - Why appeared: Strong RS vs SPY: 20d=+51.3pp, 63d=+139.8pp | above MA50 + MA200
- **CUE** | RS_MOMENTUM_LEADER | sector=Healthcare | confidence=HIGH
  - Why appeared: Strong RS vs SPY: 20d=+4.8pp, 63d=+340.9pp | above MA50 + MA200
- **MXL** | RS_MOMENTUM_LEADER | sector=Technology | confidence=HIGH
  - Why appeared: Strong RS vs SPY: 20d=+6.1pp, 63d=+424.1pp | above MA50 + MA200
- **PENG** | RS_MOMENTUM_LEADER | sector=Technology | confidence=HIGH
  - Why appeared: Strong RS vs SPY: 20d=-12.3pp, 63d=+221.3pp | above MA50 + MA200

**RS Momentum + Watchlist Research:**

- **SGRY** | SECTOR_LEADER | sector=Healthcare | confidence=HIGH
  - Why appeared: Outperforming SPY by +28.7pp over 20d; above 50d MA
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
- **KULR** | BEATEN_DOWN | sector=Industrials | confidence=HIGH
  - Why appeared: Large drawdown (86%/3m) with stabilization pattern

## 5. Forward Evidence

**Total entries:** 1153  |  **New today:** 7  |  **Matured 5d:** 494  |  **Matured 10d:** 142
- Sample status: ROBUST
- Benchmark readiness: READY — SPY 87, QQQ 87 entries with 10d return
- Verdict: **MIXED**
- Alpha proven: NO — insufficient evidence

## 6. Biggest Warnings

- ⚠ Scanner recall low at 1.1% — main miss: FILTER_TOO_STRICT (simple-RS baseline: 32.3%)
- ⚠ Options overlay: DISABLED — insufficient coverage
- ⚠ Targeted backfill plan: 3 tickers need >=300 bars — run targeted-backfill --execute to fill

## 7. Next Operator Actions

1. Review 1 high-priority research name(s) manually: SDGR
2. Run targeted-backfill --execute --limit 3 --max-provider-calls 15 to fill 3 research names below 300-bar floor.
3. Run nightly again tomorrow.

---

*Research-only engine. Not a signal. Not a recommendation. No live capital.*

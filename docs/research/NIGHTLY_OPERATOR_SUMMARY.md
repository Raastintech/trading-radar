======================================================================
  RESEARCH_ONLY_MODE — NO AUTO TRADING — HUMAN REVIEW ONLY
  Broker execution, paper-trade routing, and Alpaca are disabled.
======================================================================

# Nightly Operator Summary — 2026-08-01

**Generated:** 00:40 UTC  |  **Mode:** RESEARCH_ONLY  |  **Version:** NIGHTLY_OPERATOR_SUMMARY_V1

---

## 1. Overall Status

**✓ PASS** — All primary artifacts present
- Research-only safety: ACTIVE

## 2. Market Context

**Regime:** Bull Continuation (conf: LOW, 10m ago)
- 5d: constructive  |  10d: constructive  |  30d: mixed
- Weak sectors: XLRE, XLC
- Research posture: selective — avoid extended names; favor reset/reclaim watchlists; human review required before any action

## 3. Alpha Radar Snapshot

**Total candidates:** 100
- HIGH_PRIORITY_RESEARCH: 15: LW, GTLB, ESTC, TTAN, ... +11 more
- WATCHLIST_RESEARCH: 23: AGYS, FBIN, ALK, ANF, ... +19 more
- RESET_WATCH: 6: REPL, GRPN, DFTX, NTNX, ... +2 more
- RECLAIM_WATCH: 1: ADBE
- EXTENDED_CROWDED: 33: AGL, BLZE, QTTB, EVC, ... +29 more
- DATA_QUARANTINE: 4 (INSUFFICIENT_HISTORY: 1; DATA_QUARANTINE: 3)
- Options overlay: DISABLED

## 4. Best Research Names to Review

*Research candidates only — no trade ideas, no buy/sell signals.*

**High-priority review:**

- **LW** | EARLY_ACCUMULATION | sector=Consumer Defensive | confidence=HIGH
  - Why appeared: Rising volume + improving RS or higher lows; not extended
- **GTLB** | EARLY_ACCUMULATION | sector=Technology | confidence=HIGH
  - Why appeared: Rising volume + improving RS or higher lows; not extended
- **ESTC** | EARLY_ACCUMULATION | sector=Technology | confidence=HIGH
  - Why appeared: Rising volume + improving RS or higher lows; not extended
- **TTAN** | EARLY_ACCUMULATION | sector=Technology | confidence=HIGH
  - Why appeared: Rising volume + improving RS or higher lows; not extended
- **HRB** | EARLY_ACCUMULATION | sector=Consumer Cyclical | confidence=HIGH
  - Why appeared: Rising volume + improving RS or higher lows; not extended
- **SAIL** | EARLY_ACCUMULATION | sector=Technology | confidence=HIGH
  - Why appeared: Rising volume + improving RS or higher lows; not extended
- **KSS** | EARLY_ACCUMULATION | sector=Consumer Cyclical | confidence=HIGH
  - Why appeared: Rising volume + improving RS or higher lows; not extended
- **RYAN** | EARLY_ACCUMULATION | sector=Financial Services | confidence=HIGH
  - Why appeared: Rising volume + improving RS or higher lows; not extended
- **MORN** | BEATEN_DOWN | sector=Financial Services | confidence=HIGH
  - Why appeared: Large drawdown (14%/3m) with stabilization pattern
- **FDS** | BEATEN_DOWN | sector=Financial Services | confidence=HIGH
  - Why appeared: Large drawdown (16%/3m) with stabilization pattern

**Secondary reset/reclaim watch:**

- **REPL** | RS_MOMENTUM_LEADER | sector=Healthcare | confidence=HIGH
  - Why appeared: Strong RS vs SPY: 20d=-3.6pp, 63d=+331.9pp | above MA50 + MA200
- **GRPN** | RS_MOMENTUM_LEADER | sector=Communication Services | confidence=HIGH
  - Why appeared: Strong RS vs SPY: 20d=+7.5pp, 63d=+90.1pp | above MA50 + MA200
- **DFTX** | RS_MOMENTUM_LEADER | sector=Healthcare | confidence=HIGH
  - Why appeared: Strong RS vs SPY: 20d=-1.8pp, 63d=+95.0pp | above MA50 + MA200
- **NTNX** | EARLY_ACCUMULATION | sector=Technology | confidence=HIGH
  - Why appeared: Rising volume + improving RS or higher lows; not extended
- **AAPL** | SOCIAL_ARB | sector=Technology | confidence=HIGH
  - Why appeared: Social attention signal (source: social_arb)

**RS Momentum + Watchlist Research:**

- **AGYS** | EARLY_ACCUMULATION | sector=Technology | confidence=HIGH
  - Why appeared: Rising volume + improving RS or higher lows; not extended
- **FBIN** | EARLY_ACCUMULATION | sector=Basic Materials | confidence=HIGH
  - Why appeared: Rising volume + improving RS or higher lows; not extended
- **ALK** | EARLY_ACCUMULATION | sector=Industrials | confidence=HIGH
  - Why appeared: Rising volume + improving RS or higher lows; not extended
- **ANF** | SECTOR_LEADER | sector=Consumer Cyclical | confidence=HIGH
  - Why appeared: Outperforming SPY by +7.4pp over 20d; above 50d MA
- **BBSI** | SECTOR_LEADER | sector=Industrials | confidence=HIGH
  - Why appeared: Outperforming SPY by +2.7pp over 20d; above 50d MA
- **PTON** | SECTOR_LEADER | sector=Consumer Cyclical | confidence=HIGH
  - Why appeared: Outperforming SPY by +10.8pp over 20d; above 50d MA
- **TFX** | CATALYST | sector=Healthcare | confidence=HIGH
  - Why appeared: Upcoming earnings (2026-08-06); analyst upgrade
- **PINS** | BEATEN_DOWN | sector=Communication Services | confidence=HIGH
  - Why appeared: Large drawdown (22%/3m) with stabilization pattern

## 5. Forward Evidence

**Total entries:** 4017  |  **New today:** 100  |  **Matured 5d:** 3199  |  **Matured 10d:** 2496
- Sample status: ROBUST
- Benchmark readiness: READY — SPY 2496, QQQ 2496 entries with 10d return
- Verdict: **NO_FORWARD_EDGE**
- Alpha proven: NO — insufficient evidence

## 6. Biggest Warnings

- ⚠ Options overlay: DISABLED — insufficient coverage (known structural cause: capped snapshot-collector universe, by design — see options-coverage report; extending it is a provider-budget decision)
- ⚠ Targeted backfill plan: 6 tickers need >=300 bars — run targeted-backfill --execute to fill
- ⚠ Legacy council-funnel recall 0.0% (autopsy of the pipeline decommissioned 2026-06-13, not the live board) — main miss: FILTER_TOO_STRICT (simple-RS baseline: 43.1%) — live research-board recall accruing via scanner-recall cohorts: NEED_MORE_DATA

## 7. Next Operator Actions

1. Review 15 high-priority research name(s) manually: LW, GTLB, ESTC, TTAN, ... +11 more
2. Run targeted-backfill --execute --limit 6 --max-provider-calls 15 to fill 6 research names below 300-bar floor.
3. Run nightly again tomorrow.

---

*Research-only engine. Not a signal. Not a recommendation. No live capital.*

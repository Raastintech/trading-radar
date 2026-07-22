======================================================================
  RESEARCH_ONLY_MODE — NO AUTO TRADING — HUMAN REVIEW ONLY
  Broker execution, paper-trade routing, and Alpaca are disabled.
======================================================================

# Nightly Operator Summary — 2026-07-22

**Generated:** 00:42 UTC  |  **Mode:** RESEARCH_ONLY  |  **Version:** NIGHTLY_OPERATOR_SUMMARY_V1

---

## 1. Overall Status

**✓ PASS** — All primary artifacts present
- Research-only safety: ACTIVE

## 2. Market Context

**Regime:** Bull Pullback / Buy-the-Dip (conf: LOW, 12m ago)
- 5d: constructive  |  10d: constructive  |  30d: mixed
- Weak sectors: XLC, XLP, XLU, XLY
- Research posture: constructive for long-side research; pullback and reset candidates highlighted; human review required before any action

## 3. Alpha Radar Snapshot

**Total candidates:** 102
- HIGH_PRIORITY_RESEARCH: 9: BAX, AVTR, NTNX, ADP, ... +5 more
- WATCHLIST_RESEARCH: 23: FRSH, PTON, PAYX, MANH, ... +19 more
- RESET_WATCH: 5: QTTB, EVH, VRNS, BLZE, ... +1 more
- RECLAIM_WATCH: 2: PLTR, TKO
- EXTENDED_CROWDED: 39: REPL, AGL, BAND, EVC, ... +35 more
- DATA_QUARANTINE: 6 (DATA_QUARANTINE: 6)
- Options overlay: DISABLED

## 4. Best Research Names to Review

*Research candidates only — no trade ideas, no buy/sell signals.*

**High-priority review:**

- **BAX** | EARLY_ACCUMULATION | sector=Healthcare | confidence=HIGH
  - Why appeared: Rising volume + improving RS or higher lows; not extended
- **AVTR** | EARLY_ACCUMULATION | sector=Healthcare | confidence=HIGH
  - Why appeared: Rising volume + improving RS or higher lows; not extended
- **NTNX** | EARLY_ACCUMULATION | sector=Technology | confidence=HIGH
  - Why appeared: Rising volume + improving RS or higher lows; not extended
- **ADP** | EARLY_ACCUMULATION | sector=Technology | confidence=HIGH
  - Why appeared: Rising volume + improving RS or higher lows; not extended
- **RBRK** | EARLY_ACCUMULATION | sector=Technology | confidence=HIGH
  - Why appeared: Rising volume + improving RS or higher lows; not extended
- **RH** | EARLY_ACCUMULATION | sector=Consumer Cyclical | confidence=HIGH
  - Why appeared: Rising volume + improving RS or higher lows; not extended
- **HRB** | EARLY_ACCUMULATION | sector=Consumer Cyclical | confidence=HIGH
  - Why appeared: Rising volume + improving RS or higher lows; not extended
- **AGYS** | EARLY_ACCUMULATION | sector=Technology | confidence=HIGH
  - Why appeared: Rising volume + improving RS or higher lows; not extended
- **RIGL** | EARLY_ACCUMULATION | sector=Healthcare | confidence=HIGH
  - Why appeared: Rising volume + improving RS or higher lows; not extended

**Secondary reset/reclaim watch:**

- **QTTB** | RS_MOMENTUM_LEADER | sector=Healthcare | confidence=HIGH
  - Why appeared: Strong RS vs SPY: 20d=+21.3pp, 63d=+126.1pp | above MA50 + MA200
- **EVH** | RS_MOMENTUM_LEADER | sector=Healthcare | confidence=HIGH
  - Why appeared: Strong RS vs SPY: 20d=+30.1pp, 63d=+94.1pp | above MA50 + MA200
- **VRNS** | RS_MOMENTUM_LEADER | sector=Technology | confidence=HIGH
  - Why appeared: Strong RS vs SPY: 20d=+40.7pp, 63d=+83.6pp | above MA50 + MA200
- **BLZE** | RS_MOMENTUM_LEADER | sector=Technology | confidence=HIGH
  - Why appeared: Strong RS vs SPY: 20d=+65.5pp, 63d=+220.8pp | above MA50 + MA200
- **GOOG** | SOCIAL_ARB | sector=Communication Services | confidence=HIGH
  - Why appeared: Social attention signal (source: social_arb)

**RS Momentum + Watchlist Research:**

- **FRSH** | SECTOR_LEADER | sector=Technology | confidence=HIGH
  - Why appeared: Outperforming SPY by +19.2pp over 20d; above 50d MA
- **PTON** | SECTOR_LEADER | sector=Consumer Cyclical | confidence=HIGH
  - Why appeared: Outperforming SPY by +16.6pp over 20d; above 50d MA
- **PAYX** | SECTOR_LEADER | sector=Technology | confidence=HIGH
  - Why appeared: Outperforming SPY by +16.2pp over 20d; above 50d MA
- **MANH** | SECTOR_LEADER | sector=Technology | confidence=HIGH
  - Why appeared: Outperforming SPY by +23.0pp over 20d; above 50d MA
- **FBIN** | SECTOR_LEADER | sector=Industrials | confidence=HIGH
  - Why appeared: Outperforming SPY by +19.5pp over 20d; above 50d MA
- **CNNE** | BEATEN_DOWN | sector=Consumer Cyclical | confidence=HIGH
  - Why appeared: Large drawdown (12%/3m) with stabilization pattern
- **CBZ** | BEATEN_DOWN | sector=Industrials | confidence=HIGH
  - Why appeared: Large drawdown (34%/3m) with stabilization pattern
- **BULL** | BEATEN_DOWN | sector=Technology | confidence=HIGH
  - Why appeared: Large drawdown (16%/3m) with stabilization pattern

## 5. Forward Evidence

**Total entries:** 3002  |  **New today:** 102  |  **Matured 5d:** 2057  |  **Matured 10d:** 1358
- Sample status: ROBUST
- Benchmark readiness: READY — SPY 1358, QQQ 1358 entries with 10d return
- Verdict: **MIXED**
- Alpha proven: NO — insufficient evidence

## 6. Biggest Warnings

- ⚠ Legacy council-funnel recall 0.0% (autopsy of the pipeline decommissioned 2026-06-13, not the live board) — main miss: FILTER_TOO_STRICT (simple-RS baseline: 35.9%) — live research-board recall accruing via scanner-recall cohorts: NEED_MORE_DATA
- ⚠ Options overlay: DISABLED — insufficient coverage (known structural cause: capped snapshot-collector universe, by design — see options-coverage report; extending it is a provider-budget decision)
- ⚠ Targeted backfill plan: 1 tickers need >=300 bars — run targeted-backfill --execute to fill

## 7. Next Operator Actions

1. Review 9 high-priority research name(s) manually: BAX, AVTR, NTNX, ADP, ... +5 more
2. Run targeted-backfill --execute --limit 1 --max-provider-calls 15 to fill 1 research names below 300-bar floor.
3. Run nightly again tomorrow.

---

*Research-only engine. Not a signal. Not a recommendation. No live capital.*

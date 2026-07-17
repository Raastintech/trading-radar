======================================================================
  RESEARCH_ONLY_MODE — NO AUTO TRADING — HUMAN REVIEW ONLY
  Broker execution, paper-trade routing, and Alpaca are disabled.
======================================================================

# Nightly Operator Summary — 2026-07-17

**Generated:** 00:39 UTC  |  **Mode:** RESEARCH_ONLY  |  **Version:** NIGHTLY_OPERATOR_SUMMARY_V1

---

## 1. Overall Status

**✓ PASS** — All primary artifacts present
- Research-only safety: ACTIVE

## 2. Market Context

**Regime:** Bull Pullback / Buy-the-Dip (conf: MEDIUM, 9m ago)
- 5d: constructive  |  10d: constructive  |  30d: mixed
- Leading sectors: XLE, XLP, XLRE, XLC
- Weak sectors: XLI, XLK
- Research posture: constructive for long-side research; pullback and reset candidates highlighted; human review required before any action

## 3. Alpha Radar Snapshot

**Total candidates:** 97
- HIGH_PRIORITY_RESEARCH: 8: FRMM, MANH, PAYX, HRB, ... +4 more
- WATCHLIST_RESEARCH: 18: KSS, AVTR, RDDT, MNKD, ... +14 more
- RESET_WATCH: 11: QTTB, GRPN, SLS, OKTA, ... +7 more
- RECLAIM_WATCH: 2: MSFT, UBER
- EXTENDED_CROWDED: 37: AGL, REPL, BLZE, BAND, ... +33 more
- DATA_QUARANTINE: 5 (INSUFFICIENT_HISTORY: 3; DATA_QUARANTINE: 2)
- Options overlay: DISABLED

## 4. Best Research Names to Review

*Research candidates only — no trade ideas, no buy/sell signals.*

**High-priority review:**

- **FRMM** | RS_MOMENTUM_LEADER | sector=Technology | confidence=HIGH
  - Why appeared: Strong RS vs SPY: 20d=+10.1pp, 63d=+167.4pp | above MA50, below MA200 (early recovery)
- **MANH** | EARLY_ACCUMULATION | sector=Technology | confidence=HIGH
  - Why appeared: Rising volume + improving RS or higher lows; not extended
- **PAYX** | EARLY_ACCUMULATION | sector=Technology | confidence=HIGH
  - Why appeared: Rising volume + improving RS or higher lows; not extended
- **HRB** | EARLY_ACCUMULATION | sector=Consumer Cyclical | confidence=HIGH
  - Why appeared: Rising volume + improving RS or higher lows; not extended
- **NVO** | EARLY_ACCUMULATION | sector=Healthcare | confidence=HIGH
  - Why appeared: Rising volume + improving RS or higher lows; not extended
- **AJG** | CATALYST | sector=Financial Services | confidence=HIGH
  - Why appeared: Upcoming earnings (2026-07-30); analyst upgrade
- **AGYS** | EARLY_ACCUMULATION | sector=Technology | confidence=HIGH
  - Why appeared: Rising volume + improving RS or higher lows; not extended
- **NTNX** | EARLY_ACCUMULATION | sector=Technology | confidence=HIGH
  - Why appeared: Rising volume + improving RS or higher lows; not extended

**Secondary reset/reclaim watch:**

- **QTTB** | RS_MOMENTUM_LEADER | sector=Healthcare | confidence=HIGH
  - Why appeared: Strong RS vs SPY: 20d=+19.2pp, 63d=+142.5pp | above MA50 + MA200
- **GRPN** | RS_MOMENTUM_LEADER | sector=Communication Services | confidence=HIGH
  - Why appeared: Strong RS vs SPY: 20d=+65.5pp, 63d=+114.0pp | above MA50 + MA200
- **SLS** | RS_MOMENTUM_LEADER | sector=Healthcare | confidence=HIGH
  - Why appeared: Strong RS vs SPY: 20d=+56.0pp, 63d=+132.1pp | above MA50 + MA200
- **OKTA** | RS_MOMENTUM_LEADER | sector=Technology | confidence=HIGH
  - Why appeared: Strong RS vs SPY: 20d=+27.0pp, 63d=+112.1pp | above MA50 + MA200
- **TRIP** | EARLY_ACCUMULATION | sector=Consumer Cyclical | confidence=HIGH
  - Why appeared: Rising volume + improving RS or higher lows; not extended

**RS Momentum + Watchlist Research:**

- **KSS** | SECTOR_LEADER | sector=Consumer Cyclical | confidence=HIGH
  - Why appeared: Outperforming SPY by +5.8pp over 20d; above 50d MA
- **AVTR** | SECTOR_LEADER | sector=Healthcare | confidence=HIGH
  - Why appeared: Outperforming SPY by +17.6pp over 20d; above 50d MA
- **RDDT** | BEATEN_DOWN | sector=Communication Services | confidence=HIGH
  - Why appeared: Large drawdown (17%/3m) with stabilization pattern
- **MNKD** | BEATEN_DOWN | sector=Healthcare | confidence=HIGH
  - Why appeared: Large drawdown (46%/3m) with stabilization pattern
- **FLGT** | BEATEN_DOWN | sector=Healthcare | confidence=HIGH
  - Why appeared: Large drawdown (27%/3m) with stabilization pattern
- **HIMS** | BEATEN_DOWN | sector=Healthcare | confidence=HIGH
  - Why appeared: Large drawdown (39%/3m) with stabilization pattern
- **CBZ** | BEATEN_DOWN | sector=Industrials | confidence=HIGH
  - Why appeared: Large drawdown (42%/3m) with stabilization pattern
- **ACVA** | BEATEN_DOWN | sector=Consumer Cyclical | confidence=HIGH
  - Why appeared: Large drawdown (55%/3m) with stabilization pattern

## 5. Forward Evidence

**Total entries:** 2571  |  **New today:** 97  |  **Matured 5d:** 1609  |  **Matured 10d:** 819
- Sample status: ROBUST
- Benchmark readiness: READY — SPY 819, QQQ 819 entries with 10d return
- Verdict: **MIXED**
- Alpha proven: NO — insufficient evidence

## 6. Biggest Warnings

- ⚠ Legacy council-funnel recall 0.0% (autopsy of the pipeline decommissioned 2026-06-13, not the live board) — main miss: FILTER_TOO_STRICT (simple-RS baseline: 52.5%) — live research-board recall accruing via scanner-recall cohorts: NEED_MORE_DATA
- ⚠ Options overlay: DISABLED — insufficient coverage (known structural cause: capped snapshot-collector universe, by design — see options-coverage report; extending it is a provider-budget decision)
- ⚠ Targeted backfill plan: 2 tickers need >=300 bars — run targeted-backfill --execute to fill

## 7. Next Operator Actions

1. Review 8 high-priority research name(s) manually: FRMM, MANH, PAYX, HRB, ... +4 more
2. Run targeted-backfill --execute --limit 2 --max-provider-calls 15 to fill 2 research names below 300-bar floor.
3. Run nightly again tomorrow.

---

*Research-only engine. Not a signal. Not a recommendation. No live capital.*

======================================================================
  RESEARCH_ONLY_MODE — NO AUTO TRADING — HUMAN REVIEW ONLY
  Broker execution, paper-trade routing, and Alpaca are disabled.
======================================================================

# Nightly Operator Summary — 2026-06-19

**Generated:** 17:02 UTC  |  **Mode:** RESEARCH_ONLY  |  **Version:** NIGHTLY_OPERATOR_SUMMARY_V1

---

## 1. Overall Status

**✓ PASS** — All primary artifacts present
- Research-only safety: ACTIVE

## 2. Market Context

**Regime:** Chop / Range (conf: MEDIUM, 2m ago)
- 5d: constructive  |  10d: constructive  |  30d: mixed
- Weak sectors: XLC, XLE
- Research posture: stalking mode — do not promote ideas during stress; watchlist only; human review required before any action

## 3. Alpha Radar Snapshot

**Total candidates:** 56
- HIGH_PRIORITY_RESEARCH: 0
- RESET_WATCH: 3: SPIR, ASX, XPO
- RECLAIM_WATCH: 1: RCAT
- EXTENDED_CROWDED: 27: AAOI, AERT, CRSR, MX, ... +23 more
- DATA_QUARANTINE: 16 (INSUFFICIENT_HISTORY: 7; DATA_QUARANTINE: 9)
- Options overlay: DISABLED

## 4. Best Research Names to Review

*Research candidates only — no trade ideas, no buy/sell signals.*

**Secondary reset/reclaim watch:**

- **SPIR** | SECTOR_LEADER | sector=Industrials | confidence=HIGH
  - Why appeared: Outperforming SPY by +8.7pp over 20d; above 50d MA
- **ASX** | EARLY_ACCUMULATION | sector=Technology | confidence=HIGH
  - Why appeared: Rising volume + improving RS or higher lows; not extended
- **XPO** | EXTENDED | sector=Industrials | confidence=HIGH
  - Why appeared: Outperforming SPY by +10.3pp over 20d; above 50d MA
- **RCAT** | BEATEN_DOWN | sector=Technology | confidence=HIGH
  - Why appeared: Large drawdown (-25%/3m) with stabilization pattern

## 5. Forward Evidence

**Total entries:** 299  |  **New today:** 0  |  **Matured:** 0
- Sample status: TOO_EARLY
- Benchmark readiness: LOADED — benchmark series loaded; entry outcomes pending maturity
- Verdict: **NEED_MORE_DATA**
- Alpha proven: NO — insufficient evidence

## 6. Biggest Warnings

- ⚠ Scanner recall low at 2.0% — main miss: FILTER_TOO_STRICT (simple-RS baseline: 22.6%)
- ⚠ Forward evidence immature: 0/299 entries matured — do not change scoring
- ⚠ Benchmark series loaded; no entries have matured to 10d yet — no Phase 4B until benchmarked
- ⚠ Options overlay: DISABLED — insufficient coverage
- ⚠ Targeted backfill plan: 2 tickers need >=300 bars — run targeted-backfill --execute to fill

## 7. Next Operator Actions

1. Do not change scoring until forward outcomes mature.
2. Run targeted-backfill --execute --limit 2 --max-provider-calls 15 to fill 2 research names below 300-bar floor.
3. Run nightly again tomorrow.

---

*Research-only engine. Not a signal. Not a recommendation. No live capital.*

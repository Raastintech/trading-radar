======================================================================
  RESEARCH_ONLY_MODE — NO AUTO TRADING — HUMAN REVIEW ONLY
  Broker execution, paper-trade routing, and Alpaca are disabled.
======================================================================

# Nightly Operator Summary — 2026-06-27

**Generated:** 16:00 UTC  |  **Mode:** RESEARCH_ONLY  |  **Version:** NIGHTLY_OPERATOR_SUMMARY_V1

---

## 1. Overall Status

**✓ PASS** — All primary artifacts present
- Research-only safety: ACTIVE

## 2. Market Context

**Regime:** Bull Continuation (conf: LOW, 2m ago)
- 5d: constructive  |  10d: constructive  |  30d: bearish
- Leading sectors: XLV, XLRE
- Weak sectors: XLC, XLE
- Research posture: selective — avoid extended names; favor reset/reclaim watchlists; human review required before any action

## 3. Alpha Radar Snapshot

**Total candidates:** 78
- HIGH_PRIORITY_RESEARCH: 1: HOOD
- RESET_WATCH: 2: NUE, TECH
- RECLAIM_WATCH: 6: JEF, WGS, PAYX, RCAT, ... +2 more
- EXTENDED_CROWDED: 23: BRUN, STI, INDP, AERT, ... +19 more
- DATA_QUARANTINE: 5 (INSUFFICIENT_HISTORY: 4; DATA_QUARANTINE: 1)
- Options overlay: DISABLED

## 4. Best Research Names to Review

*Research candidates only — no trade ideas, no buy/sell signals.*

**High-priority review:**

- **HOOD** | EARLY_ACCUMULATION | sector=Financial Services | confidence=HIGH
  - Why appeared: Rising volume + improving RS or higher lows; not extended

**Secondary reset/reclaim watch:**

- **NUE** | EARLY_ACCUMULATION | sector=Basic Materials | confidence=HIGH
  - Why appeared: Rising volume + improving RS or higher lows; not extended
- **TECH** | EARLY_ACCUMULATION | sector=Healthcare | confidence=HIGH
  - Why appeared: Rising volume + improving RS or higher lows; not extended
- **JEF** | WATCH | sector=Financial Services | confidence=HIGH
  - Why appeared: Social attention signal (source: social_attention_radar)
- **WGS** | WATCH | sector=Healthcare | confidence=HIGH
  - Why appeared: Social attention signal (source: social_attention_radar)
- **PAYX** | BEATEN_DOWN | sector=Industrials | confidence=HIGH
  - Why appeared: Large drawdown (10%/3m) with stabilization pattern

## 5. Forward Evidence

**Total entries:** 746  |  **New today:** 0  |  **Matured 5d:** 14  |  **Matured 10d:** 0
- Sample status: TOO_EARLY
- Benchmark readiness: LOADED — benchmark series loaded; entry outcomes pending maturity
- Verdict: **NEED_MORE_DATA**
- Alpha proven: NO — insufficient evidence

## 6. Biggest Warnings

- ⚠ Scanner recall low at 2.0% — main miss: UNIVERSE_MISS (simple-RS baseline: 19.6%)
- ⚠ Forward evidence immature: 14 matured 5d, 0 matured 10d / 746 total — do not change scoring
- ⚠ Benchmark series loaded; no entries have matured to 10d yet — no Phase 4B until benchmarked
- ⚠ Options overlay: DISABLED — insufficient coverage
- ⚠ Targeted backfill plan: 25 tickers need >=300 bars — run targeted-backfill --execute to fill

## 7. Next Operator Actions

1. Do not change scoring until forward outcomes mature.
2. Review 1 high-priority research name(s) manually: HOOD
3. Run targeted-backfill --execute --limit 25 --max-provider-calls 15 to fill 25 research names below 300-bar floor.
4. Run nightly again tomorrow.

---

*Research-only engine. Not a signal. Not a recommendation. No live capital.*

# Phase 4A.5 — Foundation Audit Final Report

*Generated 2026-06-16 | Adversarial, independent audit of the research engine*

---

## Executive Verdict

| | |
|-|-|
| **FOUNDATION_HEALTH** | **WARN** |
| **ALPHA_RADAR_READY_FOR_FORWARD_EVIDENCE_COLLECTION** | **YES** |
| **STRUCTURAL_REWORK_REQUIRED** | **NO** |

The foundation is structurally sound. The system can continue nightly evidence collection. No finding invalidates the current data or the forward tracker history. Yellow flags exist and are documented; none of them causes a FAIL.

---

## Red Flags

**None found.**

There is no lookahead, no fabricated confidence, no forward leakage, no trade language in outputs, and no path back to execution. The quality gates block 48% of current candidates from reaching HIGH_PRIORITY status, which is correct and expected given the shallow price cache.

---

## Yellow Flags

**1. Alphabetical universe fallback (most important yellow flag)**
The scanner's 200-ticker universe fills positions 35–200 alphabetically from parquet filenames. Tickers in the M-Z range are excluded unless the alpha board surfaces them. The code comment says "prefer names with fresh data" but the implementation sorts alphabetically. This means the scanner may miss important recovery names outside the alpha board's focus.

*Mitigation:* The alpha board scans ~820 tickers with a quality/liquidity filter and feeds priority 1. For now, operators should treat scanner results as conditional on the alpha board's coverage, not as a universal screen.

**2. No baseline comparison in forward tracker** — ✅ FIXED (Phase 4A.6, 2026-06-16)
SPY, QQQ, and sector ETF benchmark returns are now stored at 5d/10d/20d/60d alongside every ticker observation. Fields: `spy_ret_{h}d`, `qqq_ret_{h}d`, `sector_ret_{h}d`, `ret_{h}d_vs_spy`, `ret_{h}d_vs_qqq`, `ret_{h}d_vs_sector`. The `_compute_verdicts()` function now reports win-rate-vs-SPY/QQQ/sector and avg/median relative returns when PROVISIONAL+. Fixed before the first entries matured (0 matured as of fix date). Sector ETF assigned for 80/118 existing entries; 38 without sector info carry `benchmark_missing_reasons: ["no_sector_etf"]`.

**3. Stale constant + stale docstring in forward tracker**
`MIN_MATURED_FOR_VERDICT = 5` is defined but never used. The module header says "NEED_MORE_DATA — fewer than 5 matured entries" but the code threshold is 10. A future reader would be misled.

*Fix:* Remove the dead constant; update the module header to say 10.

**4. Catalyst sanity runs after consensus scoring**
The consensus label is computed before sanity is applied. A stale or sector-spillover catalyst already inflated the consensus label before the sanity gate can block it. The sanity result IS stored in the item, so the priority gate still works correctly (priority requires no conflict_flags). But the consensus label count is slightly inflated.

*Impact:* Low. The priority gate is downstream of consensus and independently gated. This is a display/reporting accuracy issue.

**5. Delisted ticker detection missing in universe builder**
`_build_universe` does not check whether a parquet-present ticker is still listed. Delisted tickers with stale parquets could fill alphabetical slots.

*Impact:* Low in practice — delisted tickers typically have old stale parquets with short bar counts and fall into INVALID/INSUFFICIENT_HISTORY quarantine. But the detection is implicit, not explicit.

**6. No git commit hash in research artifacts**
Outputs do not embed the git SHA that produced them. Cannot retrospectively pin a report to its code version.

*Fix:* `git rev-parse --short HEAD` in the report writer; embed in JSON header and report markdown. Low-cost addition.

**7. Price freshness: most tickers 1–2 trading days stale**
The nightly price refresh runs at 3:30 AM ET Mon–Fri. Most parquets reflect Thursday 06-12 closes at audit time (Monday 06-16 before refresh). This is expected operational behavior, but the scanner's RS/MA computations are always 1+ trading day behind.

*Note:* 39 tickers dated 2026-06-15 (Sunday) are anomalous. Likely non-US exchange data — should be filtered or flagged.

**8. Adjustment series not programmatically tagged**
FMP provides adjusted prices. The parquets do not carry an `adjusted: true` flag. If the provider changes series, the scanner would use different data silently.

---

## Green Flags

**1. Research-only safety: airtight**
All five execution methods raise `ResearchOnlyModeError`. No flag flip re-enables trading. Restoration requires deliberate code recovery from `archive/execution_disabled/`. No forbidden trade language anywhere in outputs. Dashboard renders correctly without any broker connectivity.

**2. Quality gates are strict and working**
25/52 candidates quarantined as DATA_QUARANTINE (48%). Gates correctly block every shallow-cache, missing-field, low-liquidity candidate. No rubber-stamping. TOP_RESEARCH = 0 (correct — all current candidates are in pullback/reclaim states). HIGH_PRIORITY = 5 only (small, hard-won set).

**3. Forward tracker integrity**
118 entries logged, 0 forward-leaked. Labels and scores frozen at observation time. NEED_MORE_DATA correctly enforced. The collection is structurally valid.

**4. Catalyst sanity is genuinely strict**
7-label taxonomy, freshness check, company-specificity check, syndication check, crowding check. Stale/sector/duplicate catalysts cannot inflate priority.

**5. No lookahead in any component**
Scanner: cached-only data from prior sessions. Forward tracker: future returns computed from future parquet bars only, with explicit guard against insufficient future data. Change detector: compares prior snapshot only.

**6. Data integrity is clean**
5,618 parquets: zero date ordering issues, zero duplicates, zero negative closes, zero corrupted files. Missing data propagates as `None` / `missing_fields`, not as fabricated values.

**7. Test coverage: 1618 tests passing**
Includes unit tests for scoring logic, label sets, catalyst sanity, enrichment, forward tracker, 10x radar, dashboard panel rendering. All pass with 0 failures.

**8. Nightly cycle: fully operational**
All 20+ sub-commands completed without error. Bull Continuation regime. Alpha board active (920+ seed universe). Forward tracker collecting evidence.

---

## What Works Now

| Component | Status |
|-----------|--------|
| Market heartbeat | ✅ Operational (Bull Continuation) |
| Research scanner (6 categories) | ✅ Operational (51 candidates, nightly) |
| Candidate enrichment | ✅ Operational (MA/RS/vol/liquidity/profile) |
| Catalyst sanity | ✅ Operational (strict gates active) |
| Coverage audit | ✅ Operational (5618 tickers, 94.9% actionable) |
| Change detector | ✅ Operational (8 relabeled today) |
| Forward tracker | ✅ Operational (collecting; 118 entries, 0 matured) |
| 10x / asymmetric radar | ✅ Operational (30 candidates; TRUE_10X gate strict) |
| Daily alpha radar | ✅ Operational (generates report each nightly) |
| Dashboard / MCP | ✅ Operational (cache-only, offline-safe) |
| Nightly cycle | ✅ Operational (all sub-commands clean) |

---

## What Is Still Unproven

| Claim | Status |
|-------|--------|
| Scanner has alpha over random baseline | UNPROVEN — forward tracker at day 1 |
| EARLY_ACCUMULATION beats SPY at 10d | UNPROVEN — 0 matured observations |
| HIGH_PRIORITY_RESEARCH has edge over WATCHLIST_RESEARCH | UNPROVEN — 0 matured observations |
| 10x radar TRUE_10X_RESEARCH has better outcomes than ASYMMETRIC_RECOVERY_WATCH | UNPROVEN |
| Social/catalyst signals are actually useful | UNPROVEN — forward tracker does not yet segment by catalyst presence |
| Alpha board tickers outperform alphabetical fills | UNPROVEN |

**ETA for first PROVISIONAL verdict (≥10 matured at 10d horizon):** approximately 2026-06-30 (10 trading days from first observation 2026-06-15).

---

## What Should NOT Be Done Yet

1. **Do not start Phase 4B** until forward tracker has ≥30 matured observations per label bucket (ETA: ~6 weeks, early August 2026).
2. **Do not trust any bucket as alpha** until SPY/QQQ-adjusted baselines are beating the market — not just showing positive absolute returns.
3. **Do not promote research candidates into trade signals.** ALPHA_RADAR_READY_FOR_FORWARD_EVIDENCE_COLLECTION = YES does not mean the scanner has proven edge.
4. **Do not loosen quality gates** to increase candidate flow. The current quarantine rate (48%) is structurally correct given the shallow cache.
5. **Do not interpret PROMISING** (once reached) as a trading signal — it is a research classification only.

---

## Required Fixes Before Continuing

### Must-fix before first PROVISIONAL verdict:

| Fix | File | Status |
|-----|------|--------|
| Add `ret_10d_vs_spy` (and 5d/20d/60d) to forward tracker history | `research/research_watchlist_forward_tracker.py` | ✅ Done (Phase 4A.6) |
| Remove dead `MIN_MATURED_FOR_VERDICT = 5` constant; fix module header docstring | `research/research_watchlist_forward_tracker.py` | ✅ Done (Phase 4A.5) |

### Nice-to-have (yellow flags, not blockers):

| Fix | File | Priority |
|-----|------|---------|
| Document alphabetical fill in `_build_universe` comment (remove "prefer fresh data" claim) | `research/research_scanner.py` | Low |
| Add git commit hash to sidecar JSON headers and report markdown | `research/research_scanner.py`, `research/daily_alpha_radar_report.py` | Low |
| Add 39 Sunday-dated tickers investigation | `research/price_cache_coverage_audit.py` | Low |
| Move catalyst sanity before consensus scoring (or apply sanity correction to consensus) | `research/research_scanner.py` | Medium |

---

## Recommended Next Steps

**Continue nightly evidence collection immediately.** No structural flaw requires a pause.

Priority order:
1. **This week:** Add SPY/QQQ baseline to forward tracker observations (before first entries mature)
2. **This week:** Fix the dead constant and stale docstring in forward tracker
3. **Rolling:** Continue nightly cycle; monitor for scan failures or sidecar staleness
4. **At 10 matured entries per bucket (~2026-06-30):** Review first PROVISIONAL verdicts; check if any bucket is beating or losing to SPY by a meaningful margin
5. **At 30 matured entries (~early August 2026):** First MEANINGFUL verdict; decide whether to continue Phase 4A evidence collection or begin Phase 4B design

---

## Final Decision

```
FOUNDATION_AUDIT: WARN

PASS conditions met:
  - No structural flaw found
  - No lookahead, no fabricated confidence, no execution path
  - Data integrity clean
  - Quality gates strict and working
  - Tests passing (1618/1618)
  - Nightly cycle operational

WARN because:
  - Alphabetical universe fallback is not quality-ranked (medium concern)
  - No SPY/QQQ baseline in forward tracker yet (must fix before first verdict)
  - Minor documentation/constant hygiene issues
  - No proven alpha (correct — it's day 1 of collection, not a failure)

FAIL conditions not met:
  - No lookahead found
  - No universe flaw that invalidates current data
  - No data integrity flaw
  - No forward tracker structural flaw

Safe to continue collecting forward evidence.
Fix the baseline comparison before any bucket reaches PROVISIONAL status.
```

---

## References

| Task | Document |
|------|---------|
| T1: Architecture | `docs/research/PHASE_4A5_RESEARCH_ENGINE_ARCHITECTURE_AUDIT.md` |
| T2: Data integrity | `docs/research/PHASE_4A5_DATA_INTEGRITY_AUDIT.md` |
| T3: Bias and leakage | `docs/research/PHASE_4A5_BIAS_AND_LEAKAGE_AUDIT.md` |
| T4: Scoring logic | `docs/research/PHASE_4A5_SCORING_LOGIC_AUDIT.md` |
| T5: Forward tracker | `docs/research/PHASE_4A5_FORWARD_TRACKER_AUDIT.md` |
| T6: Universe and baseline | `docs/research/PHASE_4A5_UNIVERSE_AND_BASELINE_AUDIT.md` |
| T7: Research-only safety | `docs/research/PHASE_4A5_RESEARCH_ONLY_SAFETY_AUDIT.md` |
| T8: Reproducibility | `docs/research/PHASE_4A5_REPRODUCIBILITY_AUDIT.md` |
| T9: Tests and smoke | `docs/research/PHASE_4A5_TEST_AND_SMOKE_RESULTS.md` |

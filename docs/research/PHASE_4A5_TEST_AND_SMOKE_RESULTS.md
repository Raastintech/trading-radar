# Phase 4A.5 — T9: Test and Smoke Check Results

*Generated 2026-06-16 | Adversarial audit | RESEARCH_ONLY*

---

## 1. Pytest — Unit + Smoke Suite

```
.venv/bin/python3 -m pytest tests/unit tests/smoke -q
```

**Result:** ✅ **1618 passed, 4 warnings, 0 failures** in 21.29s

4 deprecation warnings (all from `edgar` library, not from this codebase).

---

## 2. Module-Level Offline Checks

### research_scanner.py --offline
```
Label summary:
  EARLY_ACCUMULATION      15
  BEATEN_DOWN             13
  SECTOR_LEADER           9
  ASYMMETRIC_RECOVERY_WATCH  8
  RISKY                   4
  EXTENDED                2
Artifacts: cache/research/research_scanner_latest.json
```
**Result:** ✅ Exit 0, 51 candidates, no SPECULATIVE_10X labels (cleaned), RESEARCH_ONLY_BANNER printed.

### research_coverage_audit.py
```
Coverage audit complete.
Total: 5618 tickers
HIGH=42  MEDIUM=5290  LOW=227  INVALID=59
Actionable: 94.9%
Artifact: cache/research/research_coverage_latest.json
```
**Result:** ✅ Exit 0.

### research_change_detector.py
```
Change detection complete.
Summary: 8 relabeled
NEW=0  DROPPED=0  SCORE_UP=0  SCORE_DOWN=0  RELABELED=8
Artifact: cache/research/research_changes_latest.json
```
**Result:** ✅ Exit 0. 8 relabeled = SPECULATIVE_10X → ASYMMETRIC_RECOVERY_WATCH from today's label cleanup (expected).

### research_watchlist_forward_tracker.py
```
Forward tracker complete.
Total entries: 118
New today: 0
Overall verdict: NEED_MORE_DATA (n_matured=0)
Artifact: cache/research/research_forward_latest.json
```
**Result:** ✅ Exit 0. Day 1 of collection; all verdicts NEED_MORE_DATA as expected.

### ten_x_candidate_radar.py --offline
```
10x Candidate Radar complete.
Universe: 300 tickers
Candidates: 30
  ASYMMETRIC_RECOVERY_WATCH  30
Artifact: cache/research/ten_x_candidates_latest.json
⚠  Speculative research only. No trade recommendations.
```
**Result:** ✅ Exit 0. 0 TRUE_10X_RESEARCH in offline mode (no market_cap data → cannot meet small_cap criterion). Correct behavior.

---

## 3. Full Nightly Research Cycle

```
SNIPER_ENV_PATH=/home/gem/secure/trading.env ./scripts/run_research_cycle.sh nightly
```

**Result:** ✅ **Completed successfully** at 2026-06-16 17:50:38.

Key outputs from nightly:
- **Regime:** Bull Continuation (high confidence, 5d constructive) ✅
- **Options regime:** MIXED (anchor SPY) ✅
- **Alpha board:** seed=913, filtered=820, profile=247, fundamentals=171; 11 early / 9 crowded ✅
- **Alpha board tiers:** A=13, B=5, C=2; dominant sectors: Technology, Industrials, Healthcare ✅
- **Social/catalyst:** Ran with cadence check ✅
- **Delta, lenses, gatekeeper refresh:** All completed ✅
- **Risk telemetry, scanner truth, regime validation:** All completed ✅
- **Recall-repair shadow lane / forward:** Completed ✅
- **RS/theme triage, MCP audit session:** Completed ✅
- **Research scanner:** 51 candidates, no errors ✅
- **Coverage audit, change detector, forward tracker:** All completed ✅
- **10x radar:** 30 candidates ✅
- **Daily alpha radar report:** Generated ✅

No errors or failures in the nightly cycle.

---

## 4. Dashboard Verification

```
GEM_TRADER_SKIP_DOTENV=true .venv/bin/python scripts/verify_dashboard_modes_offline.py
```

**Result:** ✅ `RENDER_OK MARKET,WATCHLIST,INTEL,RESEARCH`

All 4 dashboard modes render without errors in offline mode.

---

## 5. Forbidden Term Scans

| Target | Trade terms | Legacy terms |
|--------|-------------|-------------|
| `DAILY_ALPHA_RADAR_REPORT.md` | ✅ CLEAN | ✅ CLEAN |
| `research_scanner_latest.json` | ✅ CLEAN | ✅ CLEAN |
| `daily_alpha_radar_latest.json` | ✅ CLEAN | ✅ CLEAN |

No forbidden trade language. No legacy strategy/paper trading references in research artifacts.

---

## 6. Post-Cleanup Label Verification

After Phase 4A.5 label cleanup (SPECULATIVE_10X → ASYMMETRIC_RECOVERY_WATCH / TRUE_10X_RESEARCH):

- `grep -r "SPECULATIVE_10X" research/*.py` → 0 occurrences ✅
- Only remaining occurrence: `test_phase4a2_quality_gates.py:447` — an active assertion that guards against the legacy label reappearing ✅
- `WATCHLIST_LABELS` frozenset updated ✅
- `ALLOWED_LABELS` in test updated ✅
- Dashboard style map updated with both new labels ✅
- `stock_research_card.py` label check updated ✅
- `ten_x_candidate_radar.py` stale docstring updated ✅

---

## Summary

| Check | Result |
|-------|--------|
| 1618 unit + smoke tests | ✅ ALL PASS |
| Scanner offline run | ✅ PASS |
| Coverage audit | ✅ PASS |
| Change detector | ✅ PASS |
| Forward tracker | ✅ PASS (NEED_MORE_DATA, correct) |
| 10x radar offline | ✅ PASS |
| Nightly cycle | ✅ PASS (no errors, complete) |
| Dashboard verification | ✅ PASS |
| Forbidden term scan | ✅ ALL CLEAN |
| Label cleanup verification | ✅ PASS |

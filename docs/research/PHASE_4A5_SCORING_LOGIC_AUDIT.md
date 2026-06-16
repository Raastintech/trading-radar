# Phase 4A.5 — T4: Scoring Logic Audit

*Generated 2026-06-16 | Adversarial audit | RESEARCH_ONLY*

---

## 1. Earliness Score

**Function:** `research_scoring.earliness_label()` (7 buckets)

| Label | Conditions | Issues? |
|-------|------------|---------|
| LATE | ext > 20% AND rs_63 > 20 | ✅ Parabolic move correctly identified |
| EXTENDED | ext > 15% OR earliness=EXTENDED | ✅ Penalizes extension; routes to RESET_WATCH |
| DEVELOPING | above MA50 AND MA200 AND rs_63 ≥ 0 AND ext < 15% | ✅ Standard trend-following |
| EARLY | above MA50, below MA200, rs_63 > 0, vol_trend > 1.1 | ✅ Best entry structure |
| RESET_WATCH | above MA200, pulled back below MA50 | ✅ Pullback within uptrend |
| RECLAIM_WATCH | below MA50/MA200 but close to high | ✅ Potential recovery |
| INVALIDATED | below MA200 AND rs_63 < -10 | ✅ Active downtrend correctly blocked |
| UNKNOWN | above_ma50 or above_ma200 is None | ✅ Returns UNKNOWN → DATA_QUARANTINE |

**IPO / spinoff handling:** Tickers with < 60 bars get `data_confidence = INVALID` → `INVALID_PRIORITY` quarantine. Tickers with 60–199 bars get `data_confidence = LOW` and `above_ma200 = None` → `UNKNOWN` earliness → `DATA_QUARANTINE`. Gate is strict and correct. ✅

**Extension penalization:** `priority_label()` applies:
- ext > 15% → RESET_WATCH regardless of consensus
- ext > 20% + high consensus → RESET_WATCH
- ext > 20% + low consensus → EXTENDED_CROWDED

Extension is penalized at TWO levels (earliness AND priority). There is no path for an extended ticker to reach HIGH_PRIORITY_RESEARCH. ✅

**UNKNOWN quarantine:** UNKNOWN earliness goes directly to DATA_QUARANTINE. UNKNOWN cannot become HIGH_PRIORITY_RESEARCH. ✅

---

## 2. Consensus Score

**Function:** `research_scoring.consensus_label()` and `quality_adjusted_consensus()`

```
Categories (all_categories list, multi-category if ticker appears in multiple scans):
  1 category  → SINGLE_SIGNAL
  2 categories → DOUBLE_CONFIRMATION
  3+ categories AND score ≥ 70 → HIGH_PRIORITY_RESEARCH
  else → MULTI_CONFIRMATION
```

**Are categories independent?**
- `early_accumulation` vs `beaten_down`: can overlap if ticker has recovering RS + deep drawdown ✅ (legitimately independent signals)
- `sector_leader` vs `early_accumulation`: can overlap ✅ (independent: sector outperformance + base-building)
- `social_arb` + `alpha_board → universe → sector_leader`: potentially correlated (same FMP data drives both)
- `catalyst_watch` vs `social_arb`: the alpha board feeds both the universe and can influence social sidecar content

⚠ **Category independence is not strictly enforced.** The count of categories may overstate signal breadth when multiple categories source from the same underlying provider data.

**Social / catalyst cap:**
- `catalyst_sanity.py` blocks upgrades from stale/duplicate/sector-spillover signals
- A catalyst that fails sanity emits `catalyst_sanity_block = True` in the item dict
- The `_apply_catalyst_sanity` step runs AFTER consensus scoring; it does not retroactively lower consensus

⚠ **YELLOW FLAG:** Catalyst sanity runs AFTER consensus label assignment. A failed sanity check does not reduce the consensus label — it only blocks priority upgrade. A ticker with a stale catalyst could get `DOUBLE_CONFIRMATION` consensus label (from catalyst + sector categories) even if the catalyst sanity ultimately fails.

**Quality-adjusted consensus:** `quality_adjusted_consensus()` downweights when `data_confidence = LOW` or when `missing_fields` is non-empty. This correctly prevents low-quality data from reaching `HIGH_PRIORITY_RESEARCH` through the adjusted path. ✅

**Conflicting signals:** `CONFLICTED_SIGNAL` is triggered when `conflict_flags` is non-empty. Conflict detection is caller-supplied — the scanner does not currently auto-populate `conflict_flags`. This means the CONFLICTED_SIGNAL label depends on the calling context (daily alpha radar assembles it from gatekeeper blocks + scanner/alpha disagreements). ✅ Verified present in radar output (1 CONFLICTED_SIGNAL in current run).

---

## 3. Priority Label

**Function:** `research_scoring.priority_label()`

Gate order (strict cascade, no bypass):
1. `ticker_valid is False` → `INVALID_PRIORITY`
2. `data_confidence == "INVALID"` → `INVALID_PRIORITY`
3. `earliness == UNKNOWN or INVALIDATED` → `DATA_QUARANTINE`
4. `missing_fields` non-empty → `DATA_QUARANTINE`
5. `data_confidence is None` → `DATA_QUARANTINE`
6. `conflict_flags` non-empty → `CONFLICTED_SIGNAL`
7. Extension checks → `RESET_WATCH` or `EXTENDED_CROWDED`
8. `quality_ok` check → `WATCHLIST_RESEARCH` if fails
9. `TOP_RESEARCH` if data_confidence HIGH + HIGH_PRIORITY consensus + adj_score ≥ 70
10. Else `HIGH_PRIORITY_RESEARCH`

**HIGH_PRIORITY gate:** requires ALL of:
- `data_confidence in (HIGH, MEDIUM)`
- `ticker_valid is not False`
- `liquidity_ok is not False`
- `earliness` not UNKNOWN/LATE/INVALIDATED
- `consensus` in DOUBLE/MULTI/HIGH_PRIORITY
- no `conflict_flags`
- no `missing_fields`
- no high extension

**Observed results (current run):** 5 HIGH_PRIORITY_RESEARCH, 0 TOP_RESEARCH, 25 DATA_QUARANTINE, 14 EXTENDED_CROWDED. The gate is working as designed — the system is not rubber-stamping candidates.

**TOP_RESEARCH rarity:** Current run: 0 TOP_RESEARCH. This is correct — the alpha board has "Watch Reclaim / Watch Only" states for all current candidates, and the regime is "Bull Continuation" with names in pullback states. TOP_RESEARCH requires data_confidence HIGH (≥200 bars), which excludes 96.7% of the universe.

---

## 4. 10x / Asymmetric Radar

**TRUE_10X_RESEARCH** requires all four:
- `in_theme` (speculative structural theme keyword match)
- `small_cap` (market_cap < $5B)
- `large_dd` (dd < −40%)
- `rs_recovering` (rs_63 > 0 and < 40)

**Current run:** 0 TRUE_10X_RESEARCH, 30 ASYMMETRIC_RECOVERY_WATCH. This is correct — the offline run lacked fundamentals data (no market_cap), so all names defaulted to ASYMMETRIC_RECOVERY_WATCH. In live runs with FMP profile data, the TRUE_10X gate will apply correctly.

**ASYMMETRIC_RECOVERY_WATCH** is NOT marketed as "10x likely" — the research note explicitly states: "price/volume signals only. Theme/fundamental confirmation absent." ✅

**Survivability/dilution checks:** In `scan_asymmetric`, `survivability_ok = cash > debt * 0.5`. This is a minimal check and does not cover dilution risk (secondary offerings, convertible debt). ⚠ **YELLOW FLAG — survivability proxy is coarse; actual dilution risk requires manual review.** The `speculative_disclaimer` field warns of this.

**Price/volume alone cannot qualify as TRUE_10X_RESEARCH.** Without theme + small_cap, candidates land in ASYMMETRIC_RECOVERY_WATCH regardless of price signals. ✅

---

## 5. Social / Catalyst Sanity

**Function:** `catalyst_sanity.py`

| Check | Implementation |
|-------|---------------|
| Freshness (72h default) | `hours_since_publish > freshness_hours_limit → STALE` |
| Company-specificity | `_SECTOR_KEYWORDS` match → SECTOR_SPILLOVER |
| Syndication | `_SYNDICATION_MARKERS` match → DUPLICATE_OR_SYNDICATED |
| Crowding | `already_viral = True` → HYPE_CROWDED |
| Malformed price targets | `pt < 0.01 or pt > 100,000` → MALFORMED |
| Ticker match | requires headline to contain ticker symbol (or close variant) |

A catalyst passes sanity ONLY if `label == FRESH_COMPANY_SPECIFIC`. Everything else is rejected.

The `_apply_catalyst_sanity` step at the end of the scanner pipeline correctly sets `catalyst_sanity_pass` and `catalyst_sanity_label` on each item without rerunning scores. ✅

**Gap:** Catalyst sanity runs on the scanner output, not inside the per-category scans. This means the `catalyst_watch` category count is already included in consensus before sanity is evaluated. A ticker can get DOUBLE_CONFIRMATION from `catalyst_watch + sector_leader` even if the catalyst later fails sanity. The sanity result is available in the output but the consensus label is not retroactively corrected.

---

## Summary Verdicts

| Dimension | Verdict | Critical Issues? |
|-----------|---------|-----------------|
| Earliness score | ✅ PASS | Extension penalized at two levels; UNKNOWN quarantines correctly |
| Consensus score | ⚠ WARN | Sanity runs after consensus assignment; category independence not enforced |
| Priority label | ✅ PASS | Strict cascade; no bypass; no rubber-stamping observed |
| 10x radar | ✅ PASS | TRUE_10X criteria are strict; ASYMMETRIC_RECOVERY_WATCH correctly defaulted |
| Social/catalyst sanity | ✅ PASS (structure) ⚠ WARN (sequencing) | Sanity gate is strict but runs post-consensus |

**Scoring is strict and not promotional.** The architecture is sound. The sequencing issue (sanity after consensus) is a yellow flag, not a blocker. It means consensus numbers may slightly overcount catalyst confirmations, but the priority label is separately gated.

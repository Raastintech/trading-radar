# Phase 4A.5 — T8: Reproducibility Audit

*Generated 2026-06-16 | Adversarial audit | RESEARCH_ONLY*

---

## 1. What Is Reproducible Today

Each `research_scanner_latest.json` contains:

```json
{
  "version": "RESEARCH_SCANNER_V1",
  "generated_at": "2026-06-16T17:37:01.684077+00:00",
  "system_mode": "RESEARCH_ONLY",
  "universe_size": 200,
  "offline_mode": false,
  "fmp_available": true,
  "social_data_available": true,
  "watchlist_size": N,
  "label_summary": { ... },
  "category_counts": { ... }
}
```

Each `daily_alpha_radar_latest.json` contains:

```json
{
  "version": "DAILY_ALPHA_RADAR_V1",
  "generated_at": "...",
  "system_mode": "RESEARCH_ONLY",
  "total_candidates": N,
  "priority_counts": { ... },
  "field_coverage": { ... },
  "options_coverage": { ... }
}
```

`DAILY_ALPHA_RADAR_REPORT.md` header:
```
# Daily Alpha Radar — 2026-06-16
**Version:** DAILY_ALPHA_RADAR_V1 | **Mode:** RESEARCH_ONLY
*Candidates: 52 scanned | TOP_RESEARCH: 0 | HIGH_PRIORITY: 5 | DATA_QUARANTINE: 25*
**Regime:** UNKNOWN | **Trend:** UNKNOWN | *as of 2026-06-15*
**Tickers:** 5618 total | Actionable (HIGH+MEDIUM): 94.9%
```

---

## 2. Reproducibility Gaps

| Field | Present? | Impact |
|-------|----------|--------|
| `generated_at` (UTC ISO timestamp) | ✅ Yes | Date of run known |
| `version` string per module | ✅ Yes | Module version anchored |
| `universe_size` | ✅ Yes | Universe cap known |
| `candidate_count` / `watchlist_size` | ✅ Yes | Output size known |
| `priority_counts` | ✅ Yes | Priority distribution recorded |
| `field_coverage` | ✅ Yes | Data completeness snapshot |
| **Git commit hash** | ❌ Missing | Cannot pin code version to a report |
| **FMP data anchor date** | ❌ Missing | Price data freshness per-ticker is in item `price_parquet_as_of` but not in header |
| **Config parameters** | ❌ Missing | `DEFAULT_UNIVERSE_CAP`, scoring thresholds not embedded in artifacts |
| **Source artifact versions** | ❌ Missing | Scanner sidecar doesn't log which alpha_board, social sidecar it read |
| **Cache freshness summary** | ⚠ Partial | `field_coverage` shows populated fields, but not parquet dates |
| **Forward tracker state** | ✅ Yes | `total_history_entries`, `new_entries_today` |
| **Candidate hashes** | ❌ Missing | Cannot verify individual candidate data without re-running |

---

## 3. Reproducibility Header Addition for DAILY_ALPHA_RADAR_REPORT.md

The `daily_alpha_radar_report.py` already writes a rich header. The following fields should be added to ensure future operators can anchor to a specific run:

**Recommended addition to the report header** (not yet implemented):

```markdown
## Reproducibility Anchor
- **Git commit:** <git hash at run time>
- **Price cache anchor:** 3325 tickers at 2026-06-12, 2105 at 2026-06-11 (most recent complete session)
- **Universe cap:** 200 (research_scanner) / 300 (10x radar)
- **Input sidecars:** alpha_discovery_board_latest.json (built_at: ...), social_attention_latest.json (as_of: ...)
- **Scoring thresholds:** priority top = 70, extension_warn = 15%, extension_block = 20%
```

⚠ **YELLOW FLAG — No git commit hash in any artifact.** A report produced today cannot be pinned to the code that generated it without manual cross-referencing. If scoring logic changes, old reports cannot be compared against the new version's output without knowing which commit produced them.

---

## 4. Current Commit at Audit Time

```
commit 6d82454
Phase 4A.4: label cleanup (SPECULATIVE_10X → ASYMMETRIC_RECOVERY_WATCH / TRUE_10X_RESEARCH)
+ Phase 4A.5 audit documents (this commit, pending)
```

---

## 5. What a Future Operator Needs to Reproduce a Report

Given `DAILY_ALPHA_RADAR_REPORT.md` dated 2026-06-16:

1. **Git checkout:** commit 6d82454 (currently missing from artifacts)
2. **Price cache state:** parquets as-of 2026-06-12 (majority) — freeze or snapshot required
3. **FMP cache state:** `cache_meta` SQLite + `cache/research/` sidecars as of run time
4. **Run command:** `./scripts/run_research_cycle.sh nightly` with `SNIPER_ENV_PATH` set
5. **Verify reproducibility:** `generated_at` timestamp in output should match

**Currently feasible:** An operator with the same cache and code commit can reproduce the structural outputs (which tickers qualify) but not the exact scores (FMP provider data may differ slightly if re-fetched). The report is reproducible in structure but not precisely bit-for-bit due to FMP live API responses.

---

## 6. Forward Tracker Reproducibility

Each entry in `research_watchlist_history.jsonl` is stamped with `appearance_date` and the frozen label/score at observation time. Returns are filled in later as price bars become available. This is structurally reproducible:
- Re-running the tracker on the same JSONL + same parquets → identical output ✅
- The history is append-only (no retroactive edits to existing rows' labels/scores) ✅

---

## Verdict

| Criterion | Status |
|-----------|--------|
| Report date present | ✅ PASS |
| Data anchor timestamp | ✅ PASS (generated_at; per-ticker price_parquet_as_of) |
| Scanner snapshot version | ✅ PASS |
| Universe size documented | ✅ PASS |
| Candidate counts / priority counts | ✅ PASS |
| Git commit hash in artifact | ❌ MISSING — yellow flag |
| Source artifact versions | ❌ MISSING — yellow flag |
| Config parameter snapshot | ❌ MISSING — yellow flag |
| Forward tracker history frozen | ✅ PASS |

**A future operator CAN understand what data and code produced a report** only if they also have access to the git log. The report does not self-identify its code version. This is acceptable for a research phase but should be addressed before results are presented to external parties.

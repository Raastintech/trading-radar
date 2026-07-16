# Scanner Recall 0.0% — Verification (2026-07-16)

**Question (LLM audit queue task `a037dcabc81d`):** is the digest's
"Scanner recall low at 0.0%" a real over-blocking rate or an
instrumentation bug?

**Verdict: INSTRUMENTATION ARTIFACT — the figure measures a decommissioned
pipeline, not the live research board.**

## What the 0.0% actually measures

The number comes from the Phase 1G.5 Scanner Truth Review
(`research/scanner_truth_review.py` → `scanner_truth_summary_latest.json`).
Its funnel trace (`research/scanner_truth/funnel_trace.py`) checks whether
each winner ever appeared in the **council-funnel DB stages**:
`scan_results`, `veto_log`, `paper_signals`, `decisions`.

Those tables are frozen — last writes (verified read-only 2026-07-16):

| table | rows | last write |
|-------|------|-----------|
| `scan_results` | 47 | 2026-05-29 |
| `veto_log` | 5,037 | 2026-06-09 |
| `decisions` | 89 | 2026-05-15 |

All trading sleeves were decommissioned 2026-06-13. The winner universe,
by contrast, rolls forward daily (20/40/60/90-trading-day windows ending
at the latest cached session). As winner windows move past the last DB
writes, recall decays toward 0% **mechanically** — nothing new can ever be
"caught" by a funnel that no longer logs. This also explains the trend:
2.1% at the June review → 0.0% now, with all 237 misses classified
`blind`.

## What the live research board actually catches

Cross-checking the same winner set against the modern pipeline's own
historizer (`data/research/research_watchlist_history.jsonl`, board
appearances 2026-06-15 → 2026-07-16):

- Winners with ≥80% intra-window max return: **237**
- Appeared on the research board at least once: **137 / 237 = 57.8%**

Caveats: "ever appeared" is not timing-aware (a name may have surfaced
after its move started), and the board history window (~1 month) is
shorter than the longest winner window (90 td). The **prospective**
measurement with pre-registered gates is the scanner-recall cohorts
tracker (`scanner_recall_cohorts_latest.json`) — currently
`NEED_MORE_DATA` (0/15 matured dates; first 20d maturities ≈ early
August 2026).

So the true live-board recall is materially above the 43.4% simple-RS
baseline on an ever-appeared basis, and is **not** 0.0%.

## Fixes applied (2026-07-16)

1. `research/scanner_truth_review.py` — summary sidecar now carries
   `measured_pipeline: "decommissioned_council_funnel_autopsy"`, the
   one-liner is prefixed "(legacy-funnel autopsy)", and the fidelity
   disclosures lead with the frozen-funnel explanation.
2. `research/nightly_operator_summary.py` — the digest warning is
   reworded to "Legacy council-funnel recall N% (autopsy of the pipeline
   decommissioned 2026-06-13, not the live board) …" and appends the
   live cohorts-tracker verdict. Regex compatibility with the
   rule-based journal audit parser was preserved (`recall N%`,
   `main miss:`, `baseline:` all still match).
3. Regression test: `test_warnings_scanner_recall_labeled_as_legacy_funnel`
   in `tests/unit/test_nightly_operator_summary.py`.

## What was NOT changed

- No scanner gates, filters, or universe logic — per standing doctrine
  (strict gates beat random 12/12; gate changes require forward
  evidence from the cohorts tracker).
- The 1G.5 truth review still runs as a historical autopsy; its
  numbers are now labeled as such rather than removed.

*Research only — no trading, no signals.*

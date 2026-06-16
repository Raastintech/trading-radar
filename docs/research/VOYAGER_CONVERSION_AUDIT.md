# VOYAGER Conversion Audit (research-only)

**Generated:** 2026-06-16T05:50:04  
**Scope:** read-only (SELECT-only) DB diagnosis. No code/strategy/governance change.

## Headline numbers

- Council approvals: **145** (across 3 distinct tickers)
- Council vetoes: **41**
- Paper signals logged: **6**
- **Approval → signal conversion: 0.0414** (~4.1%)
- Positions opened / closed: **3 / 3**

## Reject reasons (council vetoes)

| n | reason |
|---|---|
| 25 | Tier 2 weighted score 41.5 < 50 |
| 12 | Tier 2 weighted score 49.0 < 50 |
| 1 | WMG single-name heat would reach $16,160 (cap: $4,099, 5.0% × equity) |
| 1 | KURA single-name heat would reach $16,444 (cap: $4,099, 5.0% × equity) |
| 1 | KURA single-name heat would reach $14,172 (cap: $4,099, 5.0% × equity) |
| 1 | JHX single-name heat would reach $16,498 (cap: $4,099, 5.0% × equity) |

## Logged signals

| ticker | archetype | score | 13F flow | ext vs 50d | logged |
|---|---|---|---|---|---|
| SBAC | EARLY_ACCUMULATION | 75 | BUYING | 11.3 | 2026-04-22 |
| CRS | BASE_ACCUMULATION | 67 | SELLING | 4.5 | 2026-05-04 |
| VOYA | EARLY_ACCUMULATION | 65 | SELLING | 11.9 | 2026-05-07 |
| KURA | EARLY_ACCUMULATION | 70 | MIXED | 11.1 | 2026-05-28 |
| WMG | EARLY_ACCUMULATION | 73 | MIXED | 11.0 | 2026-05-29 |
| JHX | EARLY_ACCUMULATION | 79 | BUYING | 7.8 | 2026-06-09 |

## Findings

- Approval→signal conversion is ~4.1% (6 paper signals from 145 council approvals across 3 distinct approved tickers). The council is NOT the bottleneck — approvals are re-emitted on the same few names every cycle, but almost none convert into a logged paper signal.
- 2/6 logged VOYAGER signals carried 13F flow = SELLING — the institutional-sponsorship thesis is being violated at selection (logging accumulation names while 13F shows distribution).
- 4/6 logged signals were >=8% extended above the 50d MA (e.g. SBAC +11.3%, VOYA +11.9%, KURA +11.1%, WMG +11.0%) — VOYAGER enters already-extended names, the same late-entry failure mode LEADER_RESET is designed to fix.
- Not over-gated at the council: only 41 vetoes vs 145 approvals, and the vetoes are borderline Tier-2 scores (~41–49 < 50), not structural. The conversion loss is downstream of the council (signal-logging / paper governance / dedup), not in selection strictness.

## Is it over-gated?

`over_gated = True`. Possibly — council vetoes are a large share of evaluations.

## Recommendation

Fold the long-leadership thesis into LEADER_RESET (research-only) rather than keep VOYAGER as a separate active sleeve. VOYAGER's failure modes — late/extended entries and logging against institutional selling — are exactly what LEADER_RESET's entry-timing + sponsorship filter target. Keep VOYAGER ACTIVE_PAPER for now (it is the favored long sleeve and its few signals are real evidence), but do NOT redesign it independently: preserve the 13F-BUYING sponsorship filter as a LEADER_RESET feature, and revisit a standalone 13F_EMERGING sleeve only after LEADER_RESET passes its event study. No code change is warranted from this audit (no logging bug found).

### Options considered

- **13F_EMERGING** — defer — overlaps LEADER_RESET; revisit after LEADER_RESET event study
- **fold_into_LEADER_RESET** — recommended — preserve 13F-BUYING sponsorship filter as a feature
- **keep_research_only** — VOYAGER stays ACTIVE_PAPER (favored long sleeve); do not redesign independently

LEADER_RESET remains research-only; Phase 2C (Trade Proposal Generator) remains not started. No VOYAGER code was changed by this audit.

# Alpha Focus

Generated: `2026-07-25T06:24:19.529352+00:00`

Alpha Focus is a same-day manual research prioritization layer. It does not delete candidates, change scores, change rankings, change scanner logic, or create trade signals.

Same-day, ticker-level presentation layer over already-scanned/scored candidates. Not a new engine: no new score, no new forward-evidence ledger, no scanner/gate/threshold/HC/EO/program-verdict change. Deprioritized names are not rejected from research — they remain visible, unaffected, in their original High-Conviction / Emerging Outlier sections.

## Summary
- Market as-of date: `2026-07-25`
- Total today: 101 · Review Now: 14 · Higher-Risk EO Review: 10 · Wait for Reset: 44 · Deprioritized: 33
- HC overlap: 10 · EO overlap: 28
- research_only: `True` · promote_to_signal: `False`

## Buckets

- **Review Now** — has >=1 positive research reason, not extended, not EO-only.
- **Higher-Risk EO Review** — has >=1 positive research reason via Emerging Outlier membership (inherently unprofitable/early, kept separate from Review Now).
- **Wait for Reset** — strong profile (HC/EO/profitable) but currently extended.
- **Deprioritized / Broad Noise** — no qualifying positive reason today.

## Rules

1. Positive reasons required to enter Review Now / Higher-Risk EO Review: HC-and-not-extended, EO-and-not-high-risk, reset/watch-for-entry, profitable quality, mid/known-cap with quality-or-reset evidence, or a clear non-extended catalyst reason.
2. Repeat-candidate decay is a caution flag (not exclusion) for HC/EO names; broad-scanner-only repeats are deprioritized.
3. Extended HC/EO/profitable names go to Wait for Reset, not deprioritized.


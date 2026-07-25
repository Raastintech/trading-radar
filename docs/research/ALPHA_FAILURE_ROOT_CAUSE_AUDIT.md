# Alpha Failure Root-Cause Audit

Generated: `2026-07-25T04:00:25.519649+00:00`

Root-cause audit only: determine why the research engine has not proven alpha. No scanner/scoring/routing/gate/threshold/factor-weight/HC/EO/program-verdict/candidate-selection changes were made.

## Summary
- Alpha proven: `False`
- Overall forward verdict: `NO_FORWARD_EDGE`
- Top likely failure cause: remove_extended (removing it improves the population mean by 2.52 pct points)
- Best surviving cohort: Mid Cap (mean 6.74%, n=80)
- Worst harmful cohort: Repeat candidates (mean -4.46%, n=1404)
- Current recommendation: **CONTINUE**
- Promote to signal: `False`

## Explicit Findings

- Broad scanner remains weak / no edge: primary_result=no edge, mean_10d=-4.52%, win_rate=0.378, sample_status=ROBUST.
- Random same-universe control currently beats the actual candidate pool: random control mean=1.52% (win=0.591, n=176) vs candidate pool excess_vs_spy=-4.68% (n=1837).
- Extension is the top ablation-identified drag: removing 'remove_extended' improves the population mean by 2.52 pct points.
- Repeat candidates show a return decay pattern (later appearances underperform first appearances).
- High-Conviction Alpha and Emerging Outlier Watch are not yet mature enough to judge (matured_count=0 and 0 respectively, vs the 10-episode evidence floor).
- alpha_proven remains False.
- research_only=true; promote_to_signal=false.

## Stop/Continue Decision Dates

| Date | Gate read today |
|---|---|
| 2026-08-17 | CONTINUE |
| 2026-09-07 | CONTINUE |
| 2026-09-30 | CONTINUE |

## Caveats

- This audit re-analyzes existing forward evidence and cached prices; it does not collect new signals and does not change what the scanner surfaces.
- Alternate-entry, regime, ablation, and baseline samples below the evidence floor (n < 10 matured) are diagnostic only and must not be read as a proven edge.

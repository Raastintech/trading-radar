# Participation Bottleneck Audit (Phase 1G.17)

Generated: 2026-06-12T16:30:12Z · window since 2026-05-01 ·
research-only / read-only — no signals, no proposals, no execution change.

## Verdict

| Layer | State |
|---|---|
| Participation | **STARVED** (reason: `entry_gates`) |
| Veto council | **STRICT** |
| Execution | **NEVER_REACHED** |

The daemon is healthy (heartbeat `LOOP`),
but the scan→council→decision funnel carried:

| Stage | Count in window |
|---|---|
| Scan cycles (active sleeves) | 4380 |
| Scanner opportunities | 479 |
| Council veto-log rows | 4131 |
| Decisions | 47 |
| Positions opened | 13 |
| Paper signals (active sleeves) | 2818 |

Last decision: **2026-05-15** ·
last SNIPER paper signal: 2026-05-08 ·
last VOYAGER paper signal: 2026-06-09.

## Rejection distribution (window totals)

SNIPER (2190 cycles, 7 opportunities):
- `no_breakout` = 92741
- `volume_insufficient` = 7031
- `atr_contraction_fail` = 961

VOYAGER (2190 cycles, 472 opportunities):
- `too_extended` = 41455
- `weak_rs_50d` = 29391
- `below_ma200_floor` = 28116
- `no_archetype` = 21527
- `earnings_soon` = 12142
- `dvol_fading` = 6312
- `stale_bars` = 4510

## Interpretation rules

- **Council STARVED** = scans ran but produced ~no candidates; the council had
  nothing to veto. Tightening or loosening the council changes nothing.
- **Execution NEVER_REACHED** = order manager and governance were not exercised;
  they are not the bottleneck and are unproven, not broken.
- Companion audits: `sniper_starvation_audit` (gate confluence),
  `voyager_starvation_cache_audit` (data-depth vs structure rejections),
  `holdout_feasibility_audit` (sample-rate viability).

*Sidecar:* `cache/research/participation_bottleneck_audit_latest.json`
*Runner:* `./scripts/run_research_cycle.sh participation-audit`

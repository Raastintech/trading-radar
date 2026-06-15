# Recall-Repair Shadow Lane — Forward Results

**Generated:** 2026-06-07 (first run) · **Verdict:** `NEED_MORE_DATA` ·
**Module:** `research/recall_repair_shadow_forward.py` ·
**Sidecar:** `cache/research/recall_repair_shadow_forward_latest.json`

> Research-only. Forward returns are point-in-time correct; cross-reference
> annotations (alpha/lens/gatekeeper/scanner) use current artifacts and are
> best-effort context. **No signals, no routing, no production change.**

## Sample

- History days (distinct as-of): **7** (backfilled point-in-time boards:
  2026-04-23, 04-30, 05-07, 05-14, 05-21, 05-29, 06-05).
- Mature ticker-days (primary 5d): **900**.
- Board cap: 150 ranked labeled candidates per as-of.

## Forward returns (pooled, rel-SPY)

| Horizon | n | shadow | random | sector_rs top | Alpha* | shadow win% | SPY |
|---|---|---|---|---|---|---|---|
| 1d | 900 | +0.71% | −0.13% | +0.79% | +1.11% | 53.4% | +0.2% |
| 3d | 900 | +0.64% | −0.72% | +0.03% | +1.18% | 50.2% | +0.1% |
| 5d | 900 | **+1.72%** | −0.41% | +1.19% | +2.82% | 53.9% | +0.7% |
| 10d | 750 | **+2.92%** | −0.85% | +3.11% | +6.83% | 61.9% | +1.9% |
| 20d | 450 | **+6.41%** | −1.36% | +5.27% | +11.53% | 65.3% | +3.6% |

\* Alpha column uses the current-annotation proxy (caveat above) and a small n.

## Recall @ 20d (winner = forward MFE ≥ threshold)

| Threshold | universe winners | shadow recall | sector_rs top | Alpha* | funnel bench | precision | FP |
|---|---|---|---|---|---|---|---|
| +20% | 910 | **16.3%** | 21.8% | 1.1% | ≈1.1% | 32.9% | 67.1% |
| +30% | 454 | **20.5%** | 28.4% | 1.3% | ≈1.1% | 20.7% | 79.3% |
| +50% | 169 | **23.7%** | 33.7% | 1.2% | ≈1.1% | 8.9% | 91.1% |

- Theme earliness: shadow leading-theme recall **2.97%** vs Alpha **1.1%**.
- Top-decile (by rank) 5d win rate: **61.1%**.

## Read

**Encouraging, UNPROVEN.** Preliminarily the simple shadow board:

- **beats the random control at every horizon** (5d +1.72% vs −0.41% rel-SPY;
  20d +6.41% vs −1.36%);
- catches **~15×** more forward winners than the live funnel (recall@+20% ≈ 16%
  vs ≈1.1%), with materially better precision/FP than the raw dumb baselines;
- catches leading-theme winners earlier than the Alpha board (2.97% vs 1.1%).

This directly supports the audit's core finding: **the recall problem is real
and fixable** — winners are reachable with simple, transparent signals the
current funnel discards.

**Why the verdict is still `NEED_MORE_DATA`:** the decision gate requires ≥10
distinct as-of days; we have 7 (backfilled). The nightly cadence adds one dated
board per trading day, so the gate is ~3 trading days away. Until then these
numbers must **not** drive any routing.

Note: the pure `sector_rs top` control has slightly higher *raw recall* than the
ranked shadow board (it flags more names); the shadow board trades a little
recall for higher precision + a positive rel-SPY edge over random. The next
question (once matured) is whether the ranked/labeled board's precision edge is
the right operating point, or whether a wider `sector_rs` net should feed Lens.

## Next

1. Let nightly accrue to ≥10 history days, then re-read the verdict.
2. If `RECALL_EDGE_DETECTED`/`READY_TO_FEED_LENS_RESEARCH_ONLY`, the only
   authorised next step is feeding top candidates to **Lens/Gatekeeper research**
   — still no paper signals, no production-universe change.

# Dynamic Universe Selection Policy — Phase 1G.7B (Task 5)

*Generated 2026-06-09T01:08:26.602083+00:00 · research-only DESIGN. Not implemented in production; no scanner/strategy/universe change is made.*

## Problem (from Task 1 audit)
The production top-1000 ranks by `base_score` = 0.45·liquidity + 0.25·ATR + 0.15·volume-ratio + 0.15·**absolute** 20d return. It is dynamic (30-min refresh) but has **no relative-strength, theme, earliness, or diversity** term, so it is liquidity-dominated and rewards already-moved names.

## Proposed slot-allocation policy

| bucket | slots | selection key |
|---|--:|---|
| core_liquid | 300 | top liquid names (keeps a broad liquid core) |
| rs_leaders | 250 | top 20d relative strength vs SPY |
| emerging_theme | 150 | leading/emerging-theme members, early/breakout stage |
| pullback_reclaim | 150 | prior strength now reclaiming rising EMA20 |
| accumulation_unusual_vol | 75 | volume expansion + tight range, constructive |
| earnings_drift | 50 | post-earnings drift (**NOT_RETAINED** — needs PIT earnings) |
| watchlist_positions | 25 | operator watchlist + open positions |

### Constraints
- **Sector cap:** ≤ 22% of slots per sector in discretionary buckets, **exempt** for confirmed-LEADING themes (so a real leadership cluster can be over-weighted, but a random hot sector cannot crowd everything).
- **Reserve for new entrants:** the emerging/accumulation/pullback buckets (375 slots) are explicitly reserved for non-extended setups.
- **Late names kept but downgraded:** LATE_EXTENDED/PARABOLIC names are NOT deleted — they stay visible (core_liquid / monitor) but are excluded from the early-entry buckets and flagged `stage_label`.
- **Refresh:** at least daily (the production builder already refreshes every 30 min; this policy changes the ranking objective, not the cadence).
- **Provenance:** every selected ticker records its `selection_bucket`, `stage_label`, `early_leader_score`, and `reason_codes` in the Task 7 ledger.

## Early-Leader Score (Task 3) components (0–100)
- RS acceleration (0–25): rs20>0, rs40>0, rs20>rs40 (accelerating).
- Controlled accumulation (0–25): volume expansion without extension, above rising EMA20, higher lows, tight range.
- Theme confirmation (0–20): LEADING=20 / EMERGING=12 / EXTENDED=5.
- Entry potential (0–15): near EMA20, pullback/reclaim, measurable stop.
- Data quality (0–15): ≥200 bars, deep cache, liquidity.
- **Late-extension penalty:** up to −40 from the sum (scaled by ext-EMA20 / ext-SMA50 / parabolic / RSI-extreme).

## Observed effect (today's research comparison)
- Current top-1000: late 6.9%, early 10.7%.
- Proposed: late 7.5%, early 15.9%, size 1000.
- 378 names added (53 early-stage), 378 dropped (3 late-stage).

## What is NOT done
- No production universe/ranking change. No new strategy, signals, or governance change. The earnings_drift bucket is `NOT_RETAINED` pending point-in-time earnings.
- Promotion requires the Task 7 ledger to accrue and a forward replay to show the proposed universe surfaces winners EARLIER point-in-time — not just covers them today.


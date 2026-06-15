# Top-1000 Universe Audit — Phase 1G.7B (Task 1)

*Generated 2026-06-09T01:08:07.117504+00:00 · research-only · cache-only.*

## Where the top-1000 is created
`core/universe.py` → `UniverseBuilder._build_fresh()`. Symbols are ranked by **`base_score (descending), tie-break symbol`** (core/universe.py:_compute_features (base_score) + _build_fresh:721-722) and the top `UNIVERSE_BASE_LIMIT (default 1000)` are kept as `base_universe`.

## Ranking formula

| component | weight | input |
|---|--:|---|
| liquidity | 0.45 | log-norm avg $-volume(20d) |
| movement | 0.25 | ATR%(14) |
| activity | 0.15 | 5d/20d volume ratio |
| abs_trend | 0.15 | **absolute** 20d return |

**abs_trend uses ABSOLUTE 20d return magnitude — a name already up (or down) a lot scores HIGHER, an explicit late/post-move bias.**

- Relative-strength-vs-SPY component: **False**
- Theme component: **False**
- Earliness/accumulation component: **False**
- Sector/theme diversity enforced: **False**

## Static or dynamic?
DYNAMIC (30-min TTL rebuild) but ranking is liquidity- and absolute-movement-weighted with NO RS/theme/earliness term. Refresh: Rebuilt every 30 min from Alpaca; NOT a static list. But the bar window is only 90 days (UNIVERSE_DISCOVERY_DAYS_BACK). Staleness: Rebuilt every 30 min from Alpaca; NOT a static list. But the bar window is only 90 days (UNIVERSE_DISCOVERY_DAYS_BACK).

Within the 30-min TTL the cached snapshot is reused. On Alpaca discovery failure a FALLBACK (curated-only) snapshot is emitted (fallback_used=true) — that is the main stale/degraded path.

## Pre-filters before the cap
Price $5.0–1000.0, avg vol ≥ 300,000, avg $-vol ≥ $5,000,000 (core/universe.py:_passes_filters (_MIN_*)).

## Empirical late bias (current top-1000)
- Already **+30%/20d**: 87 (8.7%)
- Already **+50%/20d**: 47 (4.7%)
- High ATR (>8%): 189 (18.9%)
- Median 20d return: -0.32% · median ATR%: 5.28

## Findings

- Top-1000 is DYNAMIC (rebuilt ~every 30 min) — not a static or stale list, except the curated-only FALLBACK path when Alpaca discovery fails.
- Ranking is dominated by LIQUIDITY (0.45) — mega/large-caps are structurally favored regardless of setup quality.
- The 0.15 abs_trend term uses ABSOLUTE 20d return, so already-moved names score HIGHER. Combined with ATR (movement) and 5d volume (activity), the score rewards names that have ALREADY run — a measurable late bias.
- NO relative-strength-vs-SPY, NO theme membership, NO earliness/accumulation term is used at the top-1000 stage.
- NO sector/theme diversity is enforced; a single hot sector can crowd slots.
- Empirically, 87 (8.7%) of the top-1000 are already +30% over 20d and 47 (4.7%) are +50% — included AFTER much of the move.

## Conclusion
The top-1000 is **dynamic and fresh**, so staleness is not the problem. The problem is the **ranking objective**: liquidity-dominated with an absolute-movement term and no relative-strength, theme, earliness, or diversity signal. That structurally favours large-caps and names that have already moved, and under-selects emerging leaders before their move. See `DYNAMIC_UNIVERSE_SELECTION_POLICY.md` for a proposed slot-allocation policy and `scanner_recall_repair` for the forward-validated early-leader scoring. **No production change is made by this audit.**


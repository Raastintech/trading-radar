# Social Attention Forward Results (Phase 1G.15)

Auto-refreshed by `research/social_attention_forward_validation.py`.
Research-only / cache-only. See `SOCIAL_ATTENTION_RADAR_V0.md` and `SOCIAL_ARB_REALITY_CHECK.md`.

- Generated: `2026-08-28T00:23:09.549648+00:00`
- History days: `55`
- Matured social-led (primary): `21`
- **Verdict: `SOCIAL_EDGE_DETECTED`** — social-led beats random plus ≥2 of {beats-news, early>viral, velocity-predictive} at the primary horizon — but this is a RELATIVE edge only (loses less than the alternatives, not necessarily a positive absolute return) on a thin, horizon-decaying sample; check the cohort table's absolute mean_rel_spy before treating this as a standalone signal, and confirm the remaining gate before any lens routing.

## What the gate measures
- social-led vs news-led, early-discovery vs viral-crowding, high vs low
  attention-velocity, and all leads vs seeded random liquid controls.
- Point-in-time forward returns at 1/3/5/10/20d; immature windows excluded.

## Verdict ladder
NEED_MORE_DATA → NO_VALUE → PROMISING_BUT_UNPROVEN → SOCIAL_EDGE_DETECTED →
READY_TO_FEED_LENS_RESEARCH_ONLY. Nothing here emits signals or trades.

**SOCIAL_EDGE_DETECTED / READY_TO_FEED_LENS_RESEARCH_ONLY describe a RELATIVE edge** (social-led loses less than the alternatives) — they do not by themselves mean the absolute return is positive. Always check the cohort table's `mean_rel_spy` / `mean_end` at the relevant horizon before treating either verdict as a standalone signal.

## Caveat
The 'vs News Catalyst Radar' comparison is proxied by the NEWS_LED cohort
because the News Catalyst Radar keeps no forward-outcome history. Treat it
as best-effort context, not an as-of head-to-head.

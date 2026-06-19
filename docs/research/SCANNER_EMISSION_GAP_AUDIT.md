# Scanner Emission Gap Audit — Phase 1G.6 (Task 1)

*Generated 2026-06-19T04:42:39.599518+00:00 · research-only, cache-only.*

## Question
Why did **494** liquid winners exist but only ~4 distinct tickers reach the long funnel/council?

## Winner retention through the discovery funnel

| stage | universe | winners retained | recall | dropped here | drop cause |
|---|--:|--:|--:|--:|---|
| 1_raw_price_universe | 2 | 0 | 0.0% | 0 | — |
| 2_liquidity_eligible | 2 | 0 | 0.0% | 494 | LIQUIDITY_FILTER |
| 3_base_universe_top1000 | 1000 | 341 | 69.0% | 0 | TOP_N_CAP (base limit 1000 by liquidity) |
| 4_long_strategy_universe | 138 | 24 | 4.9% | 317 | SCORE_GATE (voyager 0.35 / sniper 0.38 + structural filters) |
| 5_alpha_candidate_band | 320 | — | — | 0 | NOT_HISTORIZED (count-only; ticker list not persisted) |
| 6_alpha_board | 20 | 7 | 1.4% | 24 | TOP_N_CAP (board ≤20, 10/track) |
| 7_stock_lens_generated | 0 | 0 | 0.0% | 7 | MISSING_ARTIFACT |
| 8_gatekeeper_generated | 0 | 0 | 0.0% | 7 | MISSING_ARTIFACT |
| 9_council_veto_log | 30 | 2 | 0.4% | 24 | NO_EMISSION_PATH (scanner never emitted) |
| 10_paper_signals | 31 | 1 | 0.2% | 1 | NO_EMISSION_PATH |
| 11_decisions | 16 | 2 | 0.4% | 0 | NO_EMISSION_PATH |

**Biggest winner-drop stage:** `2_liquidity_eligible`

## Root cause

The discovery funnel is structurally narrow: liquidity-eligible winners (0) are cut to 341 by the top-1000 base-universe cap, then to 24 by the per-strategy score gates + structural filters (voyager 54 / sniper 90 names), and the council ultimately saw only 2 winners. The miss is an EMISSION/UNIVERSE gap upstream of the council — not a council/governance rejection.

## Answers to the specific questions

- **Where did the 507 winners disappear?** Overwhelmingly at the top-1000 base-universe cap and the per-strategy score gates — upstream of any council decision.
- **Never entered the raw universe?** Many are present in the price cache but fall outside the top-1000-by-liquidity base seed.
- **Failed price/cache/history filters?** Partly (price floor, $vol floor).
- **Top-N caps dropped them?** Yes — the base-1000 cap and the ≤20 board cap.
- **Alpha computed but didn't emit?** The board is capped at 10/track = 20; the candidate band (320) and seed (912) ticker lists are not historized.
- **Lens/Gatekeeper didn't generate?** Generated only for names that survive to the board/enrichment set.
- **Council never saw them because no scanner emitted them?** Yes — this is the primary mechanism (`NO_EMISSION_PATH`).

_No filter changes made. Audit only. See SCANNER_RECALL_REPAIR_PLAN.md for evidence-based recommendations._


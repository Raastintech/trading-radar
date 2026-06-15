# Social Arb Reality Check (Phase 1G.15)

Honest assessment of what the existing `research/social_arb_radar.py` actually
is, why it is not true social arbitrage, and what a real Social Attention Radar
requires. Written so future sessions do not over-claim the current engine.

## What `social_arb_radar.py` actually is

**It is a smart, de-noised News Catalyst Radar — not a social-arbitrage engine.**

Its live sources (`collect_raw_items`) are:

| Source | What it is | Social? |
|--------|-----------|---------|
| FMP stock news (`_collect_fmp_news`) | financial press / wire | No — news |
| NewsAPI (`_collect_news_api`) | keyword news queries | No — news |
| Alpaca news (`_collect_alpaca_news`) | Benzinga newswire | No — news |
| Google Trends (`_fetch_google_trends`) | search-interest z-spike | The only social-ish input — and **corroboration-only** |

Three of four sources are straight newswires. The scoring then layers
deterministic blocks (freshness, source credibility, cross-source confirmation,
mapping confidence, theme novelty, catalyst presence, tape confirmation, options
participation, 13F, internal Alpha/Lens corroboration) and finishes with an
Anthropic KEEP/DROP/NOISE review on the top ≤10 survivors.

So operationally it answers: *"did multiple credible newswires independently
carry a specific catalyst on a liquid name, and does the tape confirm
participation?"* That is **news cross-confirmation + tape confirmation**.

## Why it is not social arbitrage

- **Google Trends is corroboration-only.** It is hard-coded `trend_is_corrob_only=True`,
  given the lowest source credibility (5.0 vs 12.0 for real news), excluded from
  `source_count`, and — in `_bucket_for_candidate` — can only *promote* a lead
  that is **already** a respectable News Catalyst (≥1 real news source + tape +
  named catalyst + score floor). A Trends spike alone produces nothing.
- **No Reddit / X-Twitter / StockTwits / Discord ingestion exists** anywhere in
  the module. Those are listed only under "Future Data Sources To Investigate"
  in `SOCIAL_ARB_RADAR_V1.md`; none are implemented.
- It therefore **cannot detect early crowd attention before mainstream news.**
  By design, the closest thing to crowd attention (Trends) is forbidden from
  initiating a lead.

## What the News Catalyst Radar IS good for (keep it)

It is genuinely useful and **stays in place unchanged**: filtered, cross-confirmed
news leads with tape/options confirmation for manual review. It is the
*news/tape confirmation* surface.

## What true social arbitrage requires (the gap)

A real Social Attention Radar must, at minimum:

1. **Attention velocity** — mention counts and engagement over short windows,
   z-scored against a per-name baseline, with an acceleration ratio. The edge (if
   any) is in the *rate of change* of crowd attention, not the headline.
2. **Crowd-stage classification** — distinguish stealth/early discovery from
   broadening attention from viral crowding / exhaustion. Early ≠ late.
3. **Social-led vs news-led separation** — measure whether attention spiked
   *before* mainstream news (the only thing that can be "arbitrage") vs after it.
4. **Forward validation** — prove out-of-sample that social-led / early-discovery
   names actually beat news-led, viral-crowding, random liquid controls, and the
   News Catalyst Radar before any of it is allowed near a strategy lens.

These live in the new, **separate, research-only** module
`research/social_attention_radar.py` (Phase 1G.15) plus its forward validator
`research/social_attention_forward_validation.py`. See
`SOCIAL_ATTENTION_RADAR_V0.md`.

## Hard boundaries (both radars)

Research-only. No paper signals, no trade proposals, no execution / governance /
gate / live-capital changes, no production-universe changes. Social signal is
**never** trade approval. No private-community scraping, no API-ToS bypass, no
personal/PII collection (authors are hashed, never stored raw).

## File renames

Deferred. Renaming `social_arb_radar.py` → `news_catalyst_radar.py` is correct in
spirit but touches the runner, dashboard, MCP orchestrator, tests, and cached
artifact names — not low-risk. The two modules coexist by clear naming instead:

- `social_arb_radar.py` → **News Catalyst Radar** (news/tape confirmation)
- `social_attention_radar.py` → **Social Attention Radar** (early crowd anomaly)

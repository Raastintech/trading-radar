# Smart Social Arb Radar V1

Smart Social Arb Radar is a low-cost, twice-weekly, research-only side module.
It surfaces a small number of market-relevant news/theme/ticker leads for manual
review.

It is not a trade engine, not paper evidence, not sleeve approval, not Alpha
Discovery scoring, and not an auto-trading feature.

## Cadence

Default mode is `twice_weekly`.

Recommended schedule:

- Tuesday after market close
- Thursday or Sunday after market close

The dashboard must read only `cache/research/social_arb_latest.json`. It must
not fetch providers, refresh social/news data, or call Anthropic while rendering.

## Current Sources

- News API: broad theme discovery and company/story discovery when
  `NEWS_API_KEY` is configured.
- FMP: stock news, company profile, sector/industry context, and cached provider
  telemetry through the existing `core.fmp_client`.
- yfinance: candidate-level price/volume sanity only, after deterministic
  filtering has reduced the universe.
- 13F: slow institutional background only. It is never a timing trigger.
- Tradier: options participation overlay only when configured.
- Anthropic: final review only for top deterministic candidates, capped at 10.
  It must return or be interpreted as `KEEP`, `DROP`, or `NOISE`.

## Pipeline

1. Collect raw items and cache them.
2. Normalize title, source, timestamp, source type, URL, ticker mentions,
   company aliases, theme, and freshness.
3. Deduplicate by ticker plus normalized story key.
4. Map to tickers with confidence labels:
   direct ticker, company-name alias, or lower-confidence theme inference.
5. Apply hard noise filters before any Anthropic call.
6. Score deterministic blocks:
   freshness, source credibility, cross-source confirmation, mapping confidence,
   theme novelty, market relevance, tape confirmation, options confirmation,
   13F background, and noise-risk penalty.
7. Send only top filtered candidates to Anthropic, capped at 10.
8. Save artifacts and show only leads that clear the quality bar.

## Output Buckets

- Cross-Confirmed Lead
- News Catalyst
- Emerging Theme
- Options/Tape Confirmed
- Watch Only / Needs Verification
- Dropped / Noise

Dropped/noise rows are counted and logged but should not appear on the main
dashboard panel.

## Artifacts

- `cache/research/social_arb_latest.json`
- `cache/research/social_arb_raw_latest.json`
- `logs/social_arb_latest.txt`

Suggested command:

```bash
SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python research/social_arb_radar.py --mode twice_weekly --limit 20
```

Smoke command:

```bash
GEM_TRADER_SKIP_DOTENV=true .venv/bin/python research/social_arb_radar.py --mode twice_weekly --limit 8 --offline-sample --skip-anthropic --force
```

## Future Data Sources To Investigate

- Google Trends API / early access
- YouTube Data API metadata
- Reddit API
- TikTok Research API if eligible
- App ranking, product-review, or web-traffic datasets
- Cheap alternative trend APIs

These are future upgrades only. V1 does not assume TikTok, full web-traffic, or
reliable social-firehose access exists.

## Guardrails

- Research-only.
- Twice-weekly default.
- No dashboard-triggered provider calls.
- No Anthropic call on the raw headline universe.
- No sleeve, paper, governance, execution, or Alpha scoring changes.
- Cache-first.
- Hard noise filtering.
- Fewer, better ideas over more noisy ideas.

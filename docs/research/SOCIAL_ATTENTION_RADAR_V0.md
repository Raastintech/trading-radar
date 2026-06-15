# Social Attention Radar V0 (Phase 1G.15)

Research-only early-crowd-attention radar. Separate from the News Catalyst
Radar (`social_arb_radar.py`). See `SOCIAL_ARB_REALITY_CHECK.md`.

## What it does
- Ingests Google Trends (standalone lead), an operator-curated manual JSONL
  feed, and (opt-in) StockTwits / Reddit official APIs.
- Maps text → tickers with explicit/cashtag/alias-context/theme/sympathy
  methods, suppressing ambiguous aliases without context.
- Computes attention-velocity metrics (mention windows, z-score vs its own
  history, acceleration, source/author diversity, novelty) → 0-100 scores.
- Classifies crowd stage: STEALTH / EARLY_DISCOVERY / BROADENING /
  VIRAL_CROWDING / EXHAUSTION_RISK / NO_SIGNAL.
- Separates SOCIAL_LED vs NEWS_LED vs SIMULTANEOUS vs UNKNOWN by comparing
  social first-seen to the News Catalyst Radar artifact timestamps.
- Attaches cache-only tape/RS/options overlays as CONTEXT (never approval).

## Labels (research routing only)
SOCIAL_ATTENTION_LEAD · SOCIAL_THEME_LEAD · SOCIAL_LED_CANDIDATE ·
NEWS_LED_CANDIDATE · CROWDED_ATTENTION · NO_SOCIAL_EDGE · NEEDS_LENS ·
NEEDS_FORWARD_VALIDATION.  Never BUY/SELL/EXECUTE/APPROVED.

## Outputs
- `cache/research/social_attention_radar_latest.json`
- `logs/social_attention_radar_latest.txt`
- `data/research/social_attention_history.jsonl` (append-only, 1 row/ticker/day)

## Manual feed
`data/research/manual_social_items.jsonl` — one JSON object per line. Accepted
keys: text/title/body, ticker/tickers, theme/themes, source, source_type,
timestamp, url, author (hashed on ingest), engagement, comments, reposts.

## Run
```bash
SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python research/social_attention_radar.py
GEM_TRADER_SKIP_DOTENV=true .venv/bin/python research/social_attention_radar.py --offline-sample --skip-google-trends
```

## Guardrails
Research-only. No paper signals, trade proposals, execution/governance/gate/
live-capital/universe changes, no DB writes. Social signal is never trade
approval. No private scraping, no PII (authors hashed). Forward edge must be
proven by `social_attention_forward_validation.py` before any lens routing.

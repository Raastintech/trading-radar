# Social Attention — Source Reliability Plan (Phase 1G.15B)

How the Social Attention Radar accumulates useful history **without depending on
Reddit**. Companion runtime artifacts (refreshed every run):

- `cache/research/social_attention_source_audit_latest.json`
- `logs/social_attention_source_audit_latest.txt`

## Source priority (Reddit-free path)

| # | Source | Role | Configured? |
|---|--------|------|-------------|
| 1 | **Manual JSONL** | always-supported, operator-curated fallback | `data/research/manual_social_items.jsonl` (see `*.example.jsonl`) |
| 2 | **StockTwits** | **primary live-social source** — public API, no key | opt-in / on in `safe-nightly` |
| 3 | **Google Trends** | auxiliary, low-cap, rate-limit prone | optional, low cap in `safe-nightly` |
| 4 | **Reddit** | **DISABLED / NOT_CONFIGURED** until official API creds | intentionally off |

### Explicit statements

- **Reddit is intentionally skipped for now.** Access is difficult; the operator
  may revisit later with official API credentials. We do **not** scrape Reddit.
- **Reddit must never block history accumulation.** The radar runs and historizes
  fine with Reddit DISABLED — it is not on the required path.
- **StockTwits is the primary live-social candidate.** Public streams API, no
  auth, capped by the watch universe. Verified working (returns real messages).
- **Manual JSONL is the operator-curated fallback** and is always supported.
- **Google Trends is auxiliary** and frequently returns HTTP 429; it is low-cap
  and degrades gracefully (never fails the run).

## Source-health labels

Every source is classified each run (`source_health` in the audit artifact):

| Label | Meaning |
|-------|---------|
| `HEALTHY` | returned data this run |
| `DEGRADED` | partial — some symbols/chunks errored but data returned |
| `RATE_LIMITED` | provider throttled (429); expected occasionally for Trends/StockTwits |
| `DISABLED` | intentionally off this run (e.g. Reddit, or a `--skip-*` flag) |
| `NOT_CONFIGURED` | required dependency/creds/file absent (e.g. Reddit creds, missing manual feed) |
| `NO_DATA` | ran cleanly but nothing qualified |

A run is considered productive if **either** StockTwits **or** Manual is
`HEALTHY`. Trends and Reddit are never required for a productive run.

## What to do if a source is down

- StockTwits `RATE_LIMITED`/`NO_DATA` → rely on the manual feed that day; history
  still accrues. Lower `--stocktwits-cap` if throttling persists.
- Google Trends `RATE_LIMITED` → ignore; auxiliary only.
- Reddit `NOT_CONFIGURED` → expected; set `REDDIT_CLIENT_ID`/`REDDIT_CLIENT_SECRET`
  and pass `--enable-reddit` only when official API access is granted.

## Guardrails (unchanged from 1G.15)

Research-only. No paper signals, trade proposals, execution/governance/gate/
live-capital/universe changes, no DB writes. Social signal is never trade
approval. No private-community scraping, no PII (authors hashed). No X/Twitter
unless separately approved.

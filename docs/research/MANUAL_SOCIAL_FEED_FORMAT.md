# Manual Social Feed Format (Phase 1G.15B)

The operator-curated, always-supported social source for the Social Attention
Radar. It is the **fallback** when StockTwits is rate-limited and the way to log
observations from communities the radar does not (and will not) scrape.

- **Live feed:** `data/research/manual_social_items.jsonl`
- **Template:** `data/research/manual_social_items.example.jsonl`

Format is **JSONL** — one JSON object per line. The live feed is read by the
radar; the example file is never read (only the `.jsonl` name without `.example`).

## Fields

| Field | Required | Notes |
|-------|----------|-------|
| `timestamp` | recommended | ISO-8601 (UTC). Defaults to "now" if omitted. Drives social-vs-news lead timing. |
| `ticker` **or** `tickers` | one of these or `theme` | `"RKLB"` or `["OKLO","SMR"]`. Highest-confidence mapping. |
| `theme` **or** `themes` | optional | e.g. `"space"`, `"power_nuclear"` — maps to beneficiaries (sympathy) when no ticker. |
| `text` | yes | the observation. Keep it factual; **no private/PII content**. |
| `source_type` | recommended | free label, e.g. `manual`, `discord_note`, `stocktwits_manual`. Counts toward source diversity. |
| `public_url` / `url` | optional | only if public. |
| `engagement` | optional | int (likes/upvotes). Feeds engagement velocity. |
| `comments`, `reposts` | optional | ints. |
| `confidence` | optional | 0.0–1.0 operator confidence. |
| `note` | optional | free text for your own context. |

## Rules

- **No PII.** Do not paste usernames, real names, or private messages. The radar
  hashes any `author` field on ingest, but the simplest safe path is to omit it.
- **No private-community content.** Log a neutral observation ("chatter rising on
  X theme"), not scraped private text.
- A row with only a `theme`/`themes` (no ticker) is fine — it maps via the
  theme→beneficiary impact graph and is labelled `SOCIAL_THEME_LEAD`.
- Bad/blank lines are skipped safely; the feed never crashes a run.

## Examples

```jsonl
{"timestamp":"2026-06-09T14:30:00+00:00","ticker":"RKLB","text":"launch-cadence chatter rising, no news yet","source_type":"discord_note","confidence":0.6,"note":"early"}
{"timestamp":"2026-06-09T15:05:00+00:00","tickers":["OKLO","SMR"],"text":"SMR names trending on retail boards","source_type":"manual","theme":"power_nuclear","confidence":0.5}
{"theme":"space","text":"satellite/launch theme buzz across communities","source_type":"manual","confidence":0.4}
```

## Start your feed

```bash
cp data/research/manual_social_items.example.jsonl data/research/manual_social_items.jsonl
# then edit data/research/manual_social_items.jsonl
```

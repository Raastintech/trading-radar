# 10x Candidate Radar

**Module:** `research/ten_x_candidate_radar.py`  
**Phase:** 4A — Task 6  
**Mode:** RESEARCH_ONLY — speculative, manual research required

## Purpose

A focused scan for names that could deliver outsized multi-year returns.
Distinct from the general research scanner's `long_term_asymmetric` category —
this radar applies stricter multi-criteria filtering for the most
asymmetric speculative setups.

## Criteria (any 2+ required to appear)

| Signal | Threshold |
|--------|-----------|
| Large ATH drawdown | > 40% down from historical high |
| RS recovering | rs_63 in (0, +40) — positive but not runaway |
| Volume surge | vol_trend_ratio > 1.2 |
| Speculative theme | keyword match: AI, semis, biotech, space, EV, etc. |
| Small-cap | market cap < $5B (when FMP profile cached) |

## Output Labels

| Label | Meaning |
|-------|---------|
| `SPECULATIVE_10X` | Meets 3+ criteria — highest speculative research priority |
| `ASYMMETRIC_WATCH` | Meets 2 criteria — worth monitoring |
| `THEME_ONLY` | Theme exposure confirmed but no price momentum yet |

## Outputs

- `cache/research/ten_x_candidates_latest.json`
- `logs/ten_x_candidates_latest.txt`

## Guardrails

- **No trade recommendations** — these are research ideas only
- Candidates carry explicit speculative disclaimer
- No position sizing, entry, stop, or target implied
- Manual due diligence is mandatory before any consideration

## Usage

```bash
./scripts/run_research_cycle.sh ten-x-candidates
GEM_TRADER_SKIP_DOTENV=true .venv/bin/python research/ten_x_candidate_radar.py --offline
```

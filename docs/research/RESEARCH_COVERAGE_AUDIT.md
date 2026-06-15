# RESEARCH COVERAGE AUDIT

**Module:** `research/research_coverage_audit.py`
**Phase:** 4A — Task 1
**Mode:** RESEARCH_ONLY — diagnostic only, no trade recommendations.

## Purpose

Audits data availability and freshness across the price cache universe.
Assigns a DATA_CONFIDENCE level per ticker so downstream research scripts
can weight their findings appropriately.

## Confidence Levels

| Level   | Criteria |
|---------|----------|
| HIGH    | ≥ 90 price bars, parquet age ≤ 3 days, FMP profile in cache |
| MEDIUM  | ≥ 60 bars, age ≤ 7 days (FMP optional) |
| LOW     | ≥ 20 bars but stale (> 7 days) OR bars < 60 |
| INVALID | < 20 bars or no parquet file |

## Outputs

- `cache/research/research_coverage_latest.json`
- `logs/research_coverage_latest.txt`

## Usage

```bash
GEM_TRADER_SKIP_DOTENV=true .venv/bin/python research/research_coverage_audit.py
./scripts/run_research_cycle.sh research-coverage
```

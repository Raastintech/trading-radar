# Earliness Scoring

**Module:** `research/research_scoring.py` → `earliness_label()`  
**Phase:** 4A  
**Mode:** RESEARCH_ONLY — not a trade signal

## Purpose

Classifies where a ticker sits in its trend lifecycle using price-derived
signals (MA position, RS, extension, volume trend).  Used by the research
scanner and stock research card to help the operator understand whether a
ticker is early-stage, extended, or in a downtrend.

## Labels

| Label | Meaning | Key Criteria |
|-------|---------|--------------|
| `EARLY` | Base breakout in progress, not yet confirmed | above MA50, below MA200, RS > 0 or volume rising |
| `DEVELOPING` | Established uptrend with room to grow | above both MAs, RS ≥ 0, extension < 15% |
| `RECLAIM_WATCH` | Near-reclaim zone, watching for catalyst | below MA50/MA200, not deeply negative RS |
| `RESET_WATCH` | Healthy consolidation within uptrend | above MA200, pulled back below MA50 |
| `EXTENDED` | Stretched above key levels | > 15% above MA200 |
| `LATE` | Parabolic move, high extension + high RS | > 20% above MA200 AND rs_63 > 20 |
| `INVALIDATED` | Active downtrend | below MA200, rs_63 < −10 |
| `UNKNOWN` | Insufficient data to classify | MA200 not computable (< 200 bars) |

## Notes

- `UNKNOWN` is common when price cache depth < 200 bars — not a failure, just insufficient data
- Labels are research classifications, not trade signals
- Does NOT recommend EARLY stocks as buys; the earliness label is one research input among many

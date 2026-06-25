======================================================================
  RESEARCH_ONLY_MODE — NO AUTO TRADING — HUMAN REVIEW ONLY
  Broker execution, paper-trade routing, and Alpaca are disabled.
  FMP + Tradier (research) remain active.
======================================================================

# Daily Alpha Radar — 2026-06-25

**Version:** DAILY_ALPHA_RADAR_V1 | **Mode:** RESEARCH_ONLY | **Research-Only**

*Candidates: 79 scanned | TOP_RESEARCH: 0 | HIGH_PRIORITY: 1 | DATA_QUARANTINE: 14*

---

## Market Context

**Regime:** DEFENSIVE_ROTATION | **Trend:** PULLBACK | *as of 2026-06-25*

## Data Coverage

**Tickers:** 5619 total | Actionable (HIGH+MEDIUM): 1.9%
  HIGH=28 | MEDIUM=76 | LOW=5456 | INVALID=59

**Options Coverage:** 5% | Overlay: DISABLED
  > ⚠ OPTIONS_DATA_UNAVAILABLE: coverage below 50% threshold. No candidate will be promoted based on options data.

## Scanner Field Coverage

| Field | Coverage |
|-------|----------|
| `above_ma200` populated | 66/79 (83%) |
| `above_ma50` populated | 79/79 (100%) |
| `rs_63d_vs_spy` populated | 79/79 (100%) |
| `sector` populated | 79/79 (100%) |
| `liquidity_ok` populated | 79/79 (100%) |
| Earliness non-UNKNOWN | 66/79 (83%) |

**Quarantine breakdown:**
  - INSUFFICIENT_HISTORY: 13
  - DATA_QUARANTINE: 1

## What Changed Today

**Summary:** 6 new, 2 dropped

  - NEW_ENTRY: **CTAS** 
  - NEW_ENTRY: **ELV** 
  - NEW_ENTRY: **GE** 
  - NEW_ENTRY: **ODFL** 
  - NEW_ENTRY: **QLYS** 
  - NEW_ENTRY: **TSM** 
  - DROPPED: **MTB** 
  - DROPPED: **MU** 

## Top Research Candidates — Quality Adjusted

*(none)*

## Early Accumulation — Clean Only

- **HOOD** | priority=HIGH_PRIORITY_RESEARCH | earliness=DEVELOPING | consensus=HIGH_PRIORITY_RESEARCH | qscore=100 | confidence=MEDIUM | ext=NORMAL | escore=75 | sector=Financial Services
  - *Why appeared:* Rising volume + improving RS or higher lows; not extended
  - *Confirms if:* RS continues rising, volume expands on up-days, reclaims 50d MA
  - *Invalidates if:* Volume dries up, RS reverses, undercuts recent lows

## Reclaim / Reset Watch

- **PAYX** | priority=RECLAIM_WATCH | earliness=RECLAIM_WATCH | consensus=SINGLE_SIGNAL | qscore=40 | confidence=LOW | ext=NORMAL | escore=45 | sector=Industrials
  - *Why appeared:* Large drawdown (10%/3m) with stabilization pattern
  - *Confirms if:* Price reclaims 50d MA on volume, RS turns positive, catalytic news
  - *Invalidates if:* New lows, accelerating selling, fundamental deterioration
- **RCAT** | priority=RECLAIM_WATCH | earliness=RECLAIM_WATCH | consensus=SINGLE_SIGNAL | qscore=40 | confidence=LOW | ext=NORMAL | escore=45 | sector=Technology
  - *Why appeared:* Large drawdown (-25%/3m) with stabilization pattern
  - *Confirms if:* Price reclaims 50d MA on volume, RS turns positive, catalytic news
  - *Invalidates if:* New lows, accelerating selling, fundamental deterioration
- **ONON** | priority=RECLAIM_WATCH | earliness=RECLAIM_WATCH | consensus=SINGLE_SIGNAL | qscore=40 | confidence=LOW | ext=NORMAL | escore=45 | sector=Consumer Cyclical
  - *Why appeared:* Large drawdown (3%/3m) with stabilization pattern
  - *Confirms if:* Price reclaims 50d MA on volume, RS turns positive, catalytic news
  - *Invalidates if:* New lows, accelerating selling, fundamental deterioration
- **GLXY** | priority=RESET_WATCH | earliness=RESET_WATCH | consensus=SINGLE_SIGNAL | qscore=37 | confidence=LOW | ext=NORMAL | escore=65 | sector=Financial Services
  - *Why appeared:* Social attention signal (source: social_attention_radar)
  - *Confirms if:* Early attention + price not yet extended + fundamental support
  - *Invalidates if:* Already widely discussed (CROWDED), price fully extended
- **CLF** | priority=RECLAIM_WATCH | earliness=RECLAIM_WATCH | consensus=SINGLE_SIGNAL | qscore=35 | confidence=LOW | ext=NORMAL | escore=45 | sector=Basic Materials
  - *Why appeared:* Social attention signal (source: social_attention_radar)
  - *Confirms if:* Early attention + price not yet extended + fundamental support
  - *Invalidates if:* Already widely discussed (CROWDED), price fully extended

## Conflicted Signals

- **CMC** | priority=CONFLICTED_SIGNAL | earliness=DEVELOPING | consensus=SINGLE_SIGNAL | qscore=20 | confidence=LOW | ext=STRETCHED | escore=65 | sector=Basic Materials
  - *Catalyst sanity:* NEEDS_MANUAL_SOURCE_CHECK (no_imminent_catalyst_event)
  - **CONFLICTS:** catalyst_not_validated
  - *Downgraded:* catalyst_not_validated
  - *Why appeared:* Upcoming earnings (2026-06-25)
  - *Confirms if:* Guidance raised, strong beat, volume expands post-earnings
  - *Invalidates if:* Miss + guide down, volume collapses, extended into print
- **HUBG** | priority=CONFLICTED_SIGNAL | earliness=DEVELOPING | consensus=SINGLE_SIGNAL | qscore=20 | confidence=LOW | ext=STRETCHED | escore=65 | sector=Industrials
  - *Catalyst sanity:* NEEDS_MANUAL_SOURCE_CHECK (no_imminent_catalyst_event)
  - **CONFLICTS:** catalyst_not_validated
  - *Downgraded:* catalyst_not_validated
  - *Why appeared:* Upcoming earnings (2026-06-25)
  - *Confirms if:* Guidance raised, strong beat, volume expands post-earnings
  - *Invalidates if:* Miss + guide down, volume collapses, extended into print
- **MSM** | priority=CONFLICTED_SIGNAL | earliness=EXTENDED | consensus=SINGLE_SIGNAL | qscore=8 | confidence=LOW | ext=PARABOLIC | escore=30 | sector=Industrials
  - **CONFLICTS:** catalyst_not_validated
  - *Downgraded:* catalyst_not_validated
  - *Why appeared:* Upcoming earnings (2026-07-01); analyst upgrade
  - *Confirms if:* Guidance raised, strong beat, volume expands post-earnings
  - *Invalidates if:* Miss + guide down, volume collapses, extended into print
- **PENN** | priority=CONFLICTED_SIGNAL | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=0 | confidence=LOW | ext=PARABOLIC | escore=15 | sector=Consumer Cyclical
  - *Catalyst sanity:* NEEDS_MANUAL_SOURCE_CHECK (tape_extended)
  - **CONFLICTS:** catalyst_not_validated
  - *Downgraded:* catalyst_not_validated
  - *Why appeared:* Social attention signal (source: social_attention_radar)
  - *Confirms if:* Early attention + price not yet extended + fundamental support
  - *Invalidates if:* Already widely discussed (CROWDED), price fully extended
- **FEIM** | priority=CONFLICTED_SIGNAL | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=0 | confidence=LOW | ext=PARABOLIC | escore=15 | sector=Technology
  - **CONFLICTS:** catalyst_not_validated
  - *Downgraded:* catalyst_not_validated
  - *Why appeared:* Upcoming earnings (2026-07-09); analyst upgrade
  - *Confirms if:* Guidance raised, strong beat, volume expands post-earnings
  - *Invalidates if:* Miss + guide down, volume collapses, extended into print
- **FLY** | priority=CONFLICTED_SIGNAL | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=0 | confidence=LOW | ext=PARABOLIC | escore=15 | sector=Industrials
  - **CONFLICTS:** catalyst_not_validated
  - *Downgraded:* catalyst_not_validated
  - *Why appeared:* Upcoming earnings (2026-07-10); analyst upgrade
  - *Confirms if:* Guidance raised, strong beat, volume expands post-earnings
  - *Invalidates if:* Miss + guide down, volume collapses, extended into print
- **AEHR** | priority=CONFLICTED_SIGNAL | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=0 | confidence=LOW | ext=PARABOLIC | escore=15 | sector=Technology
  - **CONFLICTS:** catalyst_not_validated
  - *Downgraded:* catalyst_not_validated
  - *Why appeared:* Upcoming earnings (2026-07-14); analyst upgrade
  - *Confirms if:* Guidance raised, strong beat, volume expands post-earnings
  - *Invalidates if:* Miss + guide down, volume collapses, extended into print
- **CLPT** | priority=CONFLICTED_SIGNAL | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=0 | confidence=LOW | ext=PARABOLIC | escore=15 | sector=Healthcare
  - *Catalyst sanity:* NEEDS_MANUAL_SOURCE_CHECK (tape_extended)
  - **CONFLICTS:** catalyst_not_validated
  - *Downgraded:* catalyst_not_validated
  - *Why appeared:* Social attention signal (source: social_attention_radar)
  - *Confirms if:* Early attention + price not yet extended + fundamental support
  - *Invalidates if:* Already widely discussed (CROWDED), price fully extended
- **FC** | priority=CONFLICTED_SIGNAL | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=0 | confidence=LOW | ext=PARABOLIC | escore=15 | sector=Industrials
  - **CONFLICTS:** catalyst_not_validated
  - *Downgraded:* catalyst_not_validated
  - *Why appeared:* Upcoming earnings (2026-07-01)
  - *Confirms if:* Guidance raised, strong beat, volume expands post-earnings
  - *Invalidates if:* Miss + guide down, volume collapses, extended into print
- **BB** | priority=CONFLICTED_SIGNAL | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=0 | confidence=LOW | ext=PARABOLIC | escore=15 | sector=Technology
  - *Catalyst sanity:* NEEDS_MANUAL_SOURCE_CHECK (tape_extended, no_imminent_catalyst_event)
  - **CONFLICTS:** catalyst_not_validated
  - *Downgraded:* catalyst_not_validated
  - *Why appeared:* Upcoming earnings (2026-06-25)
  - *Confirms if:* Guidance raised, strong beat, volume expands post-earnings
  - *Invalidates if:* Miss + guide down, volume collapses, extended into print
*... and 6 more (see JSON sidecar)*

## Data Quarantine

- **PURR** | priority=DATA_QUARANTINE | earliness=UNKNOWN | consensus=MULTI_CONFIRMATION | qscore=50 | confidence=LOW | ext=NORMAL | escore=0 | sector=Financial Services [INSUFFICIENT_HISTORY]
- **STI** | priority=DATA_QUARANTINE | earliness=UNKNOWN | consensus=SINGLE_SIGNAL | qscore=38 | confidence=LOW | ext=NORMAL | escore=0 | sector=Industrials [INSUFFICIENT_HISTORY]
- **NAVN** | priority=DATA_QUARANTINE | earliness=UNKNOWN | consensus=SINGLE_SIGNAL | qscore=35 | confidence=HIGH | ext=NORMAL | escore=0 | sector=Technology [INSUFFICIENT_HISTORY]
- **PEP** | priority=DATA_QUARANTINE | earliness=INVALIDATED | consensus=SINGLE_SIGNAL | qscore=35 | confidence=MEDIUM | ext=NORMAL | escore=5 | sector=Consumer Defensive [DATA_QUARANTINE]
- **SHAZ** | priority=DATA_QUARANTINE | earliness=UNKNOWN | consensus=SINGLE_SIGNAL | qscore=34 | confidence=LOW | ext=NORMAL | escore=0 | sector=Technology [INSUFFICIENT_HISTORY]
- **BTQ** | priority=DATA_QUARANTINE | earliness=UNKNOWN | consensus=SINGLE_SIGNAL | qscore=34 | confidence=LOW | ext=NORMAL | escore=0 | sector=Technology [INSUFFICIENT_HISTORY]
- **PRGO** | priority=DATA_QUARANTINE | earliness=UNKNOWN | consensus=SINGLE_SIGNAL | qscore=34 | confidence=LOW | ext=NORMAL | escore=0 | sector=Healthcare [INSUFFICIENT_HISTORY]
- **SLP** | priority=DATA_QUARANTINE | earliness=UNKNOWN | consensus=SINGLE_SIGNAL | qscore=10 | confidence=LOW | ext=NORMAL | escore=0 | sector=Healthcare [INSUFFICIENT_HISTORY]
- **ZVRA** | priority=DATA_QUARANTINE | earliness=UNKNOWN | consensus=SINGLE_SIGNAL | qscore=8 | confidence=LOW | ext=NORMAL | escore=0 | sector=Healthcare [INSUFFICIENT_HISTORY]
- **RDDT** | priority=DATA_QUARANTINE | earliness=UNKNOWN | consensus=SINGLE_SIGNAL | qscore=7 | confidence=LOW | ext=NORMAL | escore=0 | sector=Communication Services [INSUFFICIENT_HISTORY]
- **WULF** | priority=DATA_QUARANTINE | earliness=UNKNOWN | consensus=SINGLE_SIGNAL | qscore=7 | confidence=LOW | ext=NORMAL | escore=0 | sector=Technology [INSUFFICIENT_HISTORY]
- **PUSA** | priority=DATA_QUARANTINE | earliness=UNKNOWN | consensus=SINGLE_SIGNAL | qscore=7 | confidence=LOW | ext=NORMAL | escore=0 | sector=Consumer Cyclical [INSUFFICIENT_HISTORY]
- **VCIG** | priority=DATA_QUARANTINE | earliness=UNKNOWN | consensus=SINGLE_SIGNAL | qscore=0 | confidence=LOW | ext=NORMAL | escore=0 | sector=Industrials [INSUFFICIENT_HISTORY]
- **REPL** | priority=DATA_QUARANTINE | earliness=UNKNOWN | consensus=SINGLE_SIGNAL | qscore=0 | confidence=LOW | ext=NORMAL | escore=0 | sector=Healthcare [INSUFFICIENT_HISTORY]

## Social / Catalyst Anomalies

- **ASX** | priority=EXTENDED_CROWDED | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=14 | confidence=LOW | ext=PARABOLIC | escore=15 | sector=Technology
  - *Catalyst sanity:* NEEDS_MANUAL_SOURCE_CHECK (tape_extended)
  - *Downgraded:* too_extended
  - *Why appeared:* Social attention signal (source: social_attention_radar)
  - *Confirms if:* Early attention + price not yet extended + fundamental support
  - *Invalidates if:* Already widely discussed (CROWDED), price fully extended
- **ZVRA** | priority=DATA_QUARANTINE | earliness=UNKNOWN | consensus=SINGLE_SIGNAL | qscore=8 | confidence=LOW | ext=NORMAL | escore=0 | sector=Healthcare
  - *Quarantine reason:* INSUFFICIENT_HISTORY
  - *Downgraded:* earliness_UNKNOWN, missing_fields:above_ma200,extension_vs_ma200_pct,ma20_extension
  - *Missing fields:* above_ma200
  - *Why appeared:* Social attention signal (source: social_attention_radar)
  - *Confirms if:* Early attention + price not yet extended + fundamental support
  - *Invalidates if:* Already widely discussed (CROWDED), price fully extended
- **GLXY** | priority=RESET_WATCH | earliness=RESET_WATCH | consensus=SINGLE_SIGNAL | qscore=37 | confidence=LOW | ext=NORMAL | escore=65 | sector=Financial Services
  - *Why appeared:* Social attention signal (source: social_attention_radar)
  - *Confirms if:* Early attention + price not yet extended + fundamental support
  - *Invalidates if:* Already widely discussed (CROWDED), price fully extended
- **RDDT** | priority=DATA_QUARANTINE | earliness=UNKNOWN | consensus=SINGLE_SIGNAL | qscore=7 | confidence=LOW | ext=NORMAL | escore=0 | sector=Communication Services
  - *Quarantine reason:* INSUFFICIENT_HISTORY
  - *Downgraded:* earliness_UNKNOWN, missing_fields:above_ma200,extension_vs_ma200_pct,ma20_extension
  - *Missing fields:* above_ma200
  - *Why appeared:* Social attention signal (source: social_attention_radar)
  - *Confirms if:* Early attention + price not yet extended + fundamental support
  - *Invalidates if:* Already widely discussed (CROWDED), price fully extended
- **WULF** | priority=DATA_QUARANTINE | earliness=UNKNOWN | consensus=SINGLE_SIGNAL | qscore=7 | confidence=LOW | ext=NORMAL | escore=0 | sector=Technology
  - *Quarantine reason:* INSUFFICIENT_HISTORY
  - *Downgraded:* earliness_UNKNOWN, missing_fields:above_ma200,extension_vs_ma200_pct,ma20_extension
  - *Missing fields:* above_ma200
  - *Why appeared:* Social attention signal (source: social_attention_radar)
  - *Confirms if:* Early attention + price not yet extended + fundamental support
  - *Invalidates if:* Already widely discussed (CROWDED), price fully extended
- **PUSA** | priority=DATA_QUARANTINE | earliness=UNKNOWN | consensus=SINGLE_SIGNAL | qscore=7 | confidence=LOW | ext=NORMAL | escore=0 | sector=Consumer Cyclical
  - *Quarantine reason:* INSUFFICIENT_HISTORY
  - *Downgraded:* earliness_UNKNOWN, missing_fields:above_ma200,extension_vs_ma200_pct,ma20_extension
  - *Missing fields:* above_ma200
  - *Why appeared:* Social attention signal (source: social_attention_radar)
  - *Confirms if:* Early attention + price not yet extended + fundamental support
  - *Invalidates if:* Already widely discussed (CROWDED), price fully extended
- **KSS** | priority=WATCHLIST_RESEARCH | earliness=DEVELOPING | consensus=SINGLE_SIGNAL | qscore=37 | confidence=LOW | ext=STRETCHED | escore=75 | sector=Consumer Cyclical
  - *Downgraded:* confidence_LOW, only_single_signal
  - *Why appeared:* Social attention signal (source: social_attention_radar)
  - *Confirms if:* Early attention + price not yet extended + fundamental support
  - *Invalidates if:* Already widely discussed (CROWDED), price fully extended
- **PENN** | priority=CONFLICTED_SIGNAL | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=0 | confidence=LOW | ext=PARABOLIC | escore=15 | sector=Consumer Cyclical
  - *Catalyst sanity:* NEEDS_MANUAL_SOURCE_CHECK (tape_extended)
  - **CONFLICTS:** catalyst_not_validated
  - *Downgraded:* catalyst_not_validated
  - *Why appeared:* Social attention signal (source: social_attention_radar)
  - *Confirms if:* Early attention + price not yet extended + fundamental support
  - *Invalidates if:* Already widely discussed (CROWDED), price fully extended
- **CLPT** | priority=CONFLICTED_SIGNAL | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=0 | confidence=LOW | ext=PARABOLIC | escore=15 | sector=Healthcare
  - *Catalyst sanity:* NEEDS_MANUAL_SOURCE_CHECK (tape_extended)
  - **CONFLICTS:** catalyst_not_validated
  - *Downgraded:* catalyst_not_validated
  - *Why appeared:* Social attention signal (source: social_attention_radar)
  - *Confirms if:* Early attention + price not yet extended + fundamental support
  - *Invalidates if:* Already widely discussed (CROWDED), price fully extended
- **CLF** | priority=RECLAIM_WATCH | earliness=RECLAIM_WATCH | consensus=SINGLE_SIGNAL | qscore=35 | confidence=LOW | ext=NORMAL | escore=45 | sector=Basic Materials
  - *Why appeared:* Social attention signal (source: social_attention_radar)
  - *Confirms if:* Early attention + price not yet extended + fundamental support
  - *Invalidates if:* Already widely discussed (CROWDED), price fully extended

## True 10x Research Candidates

> ⚠ TRUE_10X_RESEARCH requires: confirmed speculative/structural theme + small-cap base + price recovery evidence. High risk. Manual deep research required.

*(none — stricter criteria require theme + small-cap + confirmed price recovery)*

## Asymmetric Recovery Watch

> ASYMMETRIC_RECOVERY_WATCH: price/volume recovery signals only. Theme/fundamental thesis unconfirmed. Not the same as TRUE_10X_RESEARCH.

- **AI** | dd=-61.35% | rs63=14.85 | vol_trend=1.609 | score=86 | [price/volume only — no confirmed thesis]
- **ALOY** | dd=-45.67% | rs63=5.68 | vol_trend=1.455 | score=86 | [price/volume only — no confirmed thesis]
- **AACBR** | dd=-55.26% | rs63=-66.89 | vol_trend=2.776 | score=69 | [price/volume only — no confirmed thesis]
- **AACIW** | dd=-73.21% | rs63=-84.85 | vol_trend=1.842 | score=69 | [price/volume only — no confirmed thesis]
- **AAME** | dd=-41.45% | rs63=-47.37 | vol_trend=1.969 | score=69 | [price/volume only — no confirmed thesis]
- **ABLVW** | dd=-44.74% | rs63=-18.23 | vol_trend=1.281 | score=69 | [price/volume only — no confirmed thesis]
- **ABM** | dd=-2.2% | rs63=8.32 | vol_trend=1.551 | score=69 | [price/volume only — no confirmed thesis]
- **AD** | dd=0.0% | rs63=21.71 | vol_trend=1.809 | score=69 | [price/volume only — no confirmed thesis]
- **ADBE** | dd=-53.61% | rs63=-32.99 | vol_trend=1.562 | score=69 | [price/volume only — no confirmed thesis]
- **ADCT** | dd=-78.28% | rs63=-85.97 | vol_trend=2.048 | score=69 | [price/volume only — no confirmed thesis]

## Extended / Crowded / Avoid

- **AAL** | priority=EXTENDED_CROWDED | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=43 | confidence=MEDIUM | ext=PARABOLIC | escore=15 | sector=Industrials
- **LRCX** | priority=EXTENDED_CROWDED | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=43 | confidence=MEDIUM | ext=PARABOLIC | escore=15 | sector=Technology
- **SNX** | priority=EXTENDED_CROWDED | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=40 | confidence=HIGH | ext=PARABOLIC | escore=15 | sector=Technology
- **NUE** | priority=EXTENDED_CROWDED | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=40 | confidence=HIGH | ext=PARABOLIC | escore=15 | sector=Basic Materials
- **ODFL** | priority=EXTENDED_CROWDED | earliness=EXTENDED | consensus=SINGLE_SIGNAL | qscore=35 | confidence=HIGH | ext=PARABOLIC | escore=30 | sector=Industrials
- **JBHT** | priority=EXTENDED_CROWDED | earliness=EXTENDED | consensus=SINGLE_SIGNAL | qscore=35 | confidence=HIGH | ext=PARABOLIC | escore=30 | sector=Industrials
- **UMAC** | priority=EXTENDED_CROWDED | earliness=EXTENDED | consensus=SINGLE_SIGNAL | qscore=30 | confidence=LOW | ext=PARABOLIC | escore=30 | sector=Technology
- **BRUN** | priority=EXTENDED_CROWDED | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=28 | confidence=LOW | ext=PARABOLIC | escore=15 | sector=Technology
- **INDP** | priority=EXTENDED_CROWDED | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=28 | confidence=LOW | ext=PARABOLIC | escore=15 | sector=Healthcare
- **AERT** | priority=EXTENDED_CROWDED | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=28 | confidence=LOW | ext=PARABOLIC | escore=15 | sector=Industrials
*... and 14 more (see JSON sidecar)*

## Forward Tracker Status

**Overall:** n=0 matured | sample_status=TOO_EARLY | verdict=NEED_MORE_DATA

> ⚠ TOO_EARLY: 0 matured observations. Need ≥10 for provisional read, ≥30 for meaningful, ≥100 for robust. No bucket has interpretable evidence yet.

**Benchmarks:** SPY/QQQ ready for 0/588 entries | sector ETF assigned for 499/588

| Bucket | n_total | n_matured | sample_status | verdict |
|--------|---------|-----------|---------------|---------|
| ASYMMETRIC_RECOVERY_WATCH |      88 |         0 | TOO_EARLY     | NEED_MORE_DATA |
| BEATEN_DOWN            |     115 |         0 | TOO_EARLY     | NEED_MORE_DATA |
| CATALYST               |      55 |         0 | TOO_EARLY     | NEED_MORE_DATA |
| EARLY_ACCUMULATION     |     152 |         0 | TOO_EARLY     | NEED_MORE_DATA |
| EXTENDED               |      20 |         0 | TOO_EARLY     | NEED_MORE_DATA |
| NO_SOCIAL_DATA         |      12 |         0 | TOO_EARLY     | NEED_MORE_DATA |
| RISKY                  |      15 |         0 | TOO_EARLY     | NEED_MORE_DATA |
| SECTOR_LEADER          |      69 |         0 | TOO_EARLY     | NEED_MORE_DATA |
| SOCIAL_ARB             |       1 |         0 | TOO_EARLY     | NEED_MORE_DATA |
| SPECULATIVE_10X        |      21 |         0 | TOO_EARLY     | NEED_MORE_DATA |
| WATCH                  |      40 |         0 | TOO_EARLY     | NEED_MORE_DATA |

## Safety Confirmations

- **RESEARCH_ONLY_MODE:** All execution flags are False
- **NO TRADE-ACTION GUIDANCE** generated in this report
- **NO DIRECTIONAL CALLS** — all candidates require manual research
- **NO EXECUTION PARAMETERS** of any kind in any output
- **NO PAPER-LEDGER WRITES** emitted
- **NO ALPACA INTERACTION** — all data from local cache
- **NO BROKER EXECUTION** — system remains fully decommissioned from auto-trading
- All research candidates require independent human validation before any action

---
*Generated: 2026-06-25T16:40:55.718639+00:00 | DAILY_ALPHA_RADAR_V1*

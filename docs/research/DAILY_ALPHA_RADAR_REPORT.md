======================================================================
  RESEARCH_ONLY_MODE — NO AUTO TRADING — HUMAN REVIEW ONLY
  Broker execution, paper-trade routing, and Alpaca are disabled.
  FMP + Tradier (research) remain active.
======================================================================

# Daily Alpha Radar — 2026-06-27

**Version:** DAILY_ALPHA_RADAR_V1 | **Mode:** RESEARCH_ONLY | **Research-Only**

*Candidates: 78 scanned | TOP_RESEARCH: 0 | HIGH_PRIORITY: 1 | DATA_QUARANTINE: 5*

---

## Market Context

**Regime:** DEFENSIVE_ROTATION | **Trend:** CHOP | *as of 2026-06-27*

## Data Coverage

**Tickers:** 5619 total | Actionable (HIGH+MEDIUM): 1.8%
  HIGH=26 | MEDIUM=76 | LOW=5458 | INVALID=59

**Options Coverage:** 5% | Overlay: DISABLED
  > ⚠ OPTIONS_DATA_UNAVAILABLE: coverage below 50% threshold. No candidate will be promoted based on options data.

## Scanner Field Coverage

| Field | Coverage |
|-------|----------|
| `above_ma200` populated | 74/78 (94%) |
| `above_ma50` populated | 78/78 (100%) |
| `rs_63d_vs_spy` populated | 78/78 (100%) |
| `sector` populated | 78/78 (100%) |
| `liquidity_ok` populated | 78/78 (100%) |
| Earliness non-UNKNOWN | 74/78 (94%) |

**Quarantine breakdown:**
  - INSUFFICIENT_HISTORY: 4
  - DATA_QUARANTINE: 1

## What Changed Today

**Summary:** no significant changes

## Top Research Candidates — Quality Adjusted

*(none)*

## Early Accumulation — Clean Only

- **HOOD** | priority=HIGH_PRIORITY_RESEARCH | earliness=DEVELOPING | consensus=DOUBLE_CONFIRMATION | qscore=89 | confidence=MEDIUM | ext=NORMAL | escore=75 | sector=Financial Services
  - *Why appeared:* Rising volume + improving RS or higher lows; not extended
  - *Confirms if:* RS continues rising, volume expands on up-days, reclaims 50d MA
  - *Invalidates if:* Volume dries up, RS reverses, undercuts recent lows

## Reclaim / Reset Watch

- **NUE** | priority=RESET_WATCH | earliness=LATE | consensus=DOUBLE_CONFIRMATION | qscore=60 | confidence=HIGH | ext=PARABOLIC | escore=15 | sector=Basic Materials
  - *Downgraded:* extension_high_consensus
  - *Why appeared:* Rising volume + improving RS or higher lows; not extended
  - *Confirms if:* RS continues rising, volume expands on up-days, reclaims 50d MA
  - *Invalidates if:* Volume dries up, RS reverses, undercuts recent lows
- **TECH** | priority=RESET_WATCH | earliness=LATE | consensus=DOUBLE_CONFIRMATION | qscore=60 | confidence=HIGH | ext=PARABOLIC | escore=15 | sector=Healthcare
  - *Downgraded:* extension_high_consensus
  - *Why appeared:* Rising volume + improving RS or higher lows; not extended
  - *Confirms if:* RS continues rising, volume expands on up-days, reclaims 50d MA
  - *Invalidates if:* Volume dries up, RS reverses, undercuts recent lows
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
- **FLUT** | priority=RECLAIM_WATCH | earliness=RECLAIM_WATCH | consensus=SINGLE_SIGNAL | qscore=40 | confidence=LOW | ext=NORMAL | escore=45 | sector=Consumer Cyclical
  - *Why appeared:* Large drawdown (1%/3m) with stabilization pattern
  - *Confirms if:* Price reclaims 50d MA on volume, RS turns positive, catalytic news
  - *Invalidates if:* New lows, accelerating selling, fundamental deterioration
- **JEF** | priority=RECLAIM_WATCH | earliness=RECLAIM_WATCH | consensus=SINGLE_SIGNAL | qscore=37 | confidence=LOW | ext=NORMAL | escore=45 | sector=Financial Services
  - *Why appeared:* Social attention signal (source: social_attention_radar)
  - *Confirms if:* Early attention + price not yet extended + fundamental support
  - *Invalidates if:* Already widely discussed (CROWDED), price fully extended
- **WGS** | priority=RECLAIM_WATCH | earliness=RECLAIM_WATCH | consensus=SINGLE_SIGNAL | qscore=35 | confidence=LOW | ext=NORMAL | escore=45 | sector=Healthcare
  - *Why appeared:* Social attention signal (source: social_attention_radar)
  - *Confirms if:* Early attention + price not yet extended + fundamental support
  - *Invalidates if:* Already widely discussed (CROWDED), price fully extended

## Conflicted Signals

- **C** | priority=CONFLICTED_SIGNAL | earliness=EXTENDED | consensus=SINGLE_SIGNAL | qscore=10 | confidence=MEDIUM | ext=PARABOLIC | escore=30 | sector=Financial Services
  - **CONFLICTS:** catalyst_not_validated
  - *Downgraded:* catalyst_not_validated
  - *Why appeared:* Upcoming earnings (2026-07-14)
  - *Confirms if:* Guidance raised, strong beat, volume expands post-earnings
  - *Invalidates if:* Miss + guide down, volume collapses, extended into print
- **MSM** | priority=CONFLICTED_SIGNAL | earliness=EXTENDED | consensus=SINGLE_SIGNAL | qscore=8 | confidence=LOW | ext=PARABOLIC | escore=30 | sector=Industrials
  - **CONFLICTS:** catalyst_not_validated
  - *Downgraded:* catalyst_not_validated
  - *Why appeared:* Upcoming earnings (2026-07-01); analyst upgrade
  - *Confirms if:* Guidance raised, strong beat, volume expands post-earnings
  - *Invalidates if:* Miss + guide down, volume collapses, extended into print
- **BFLY** | priority=CONFLICTED_SIGNAL | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=0 | confidence=LOW | ext=PARABOLIC | escore=15 | sector=Healthcare
  - *Catalyst sanity:* NEEDS_MANUAL_SOURCE_CHECK (tape_extended)
  - **CONFLICTS:** catalyst_not_validated
  - *Downgraded:* catalyst_not_validated
  - *Why appeared:* Social attention signal (source: social_attention_radar)
  - *Confirms if:* Early attention + price not yet extended + fundamental support
  - *Invalidates if:* Already widely discussed (CROWDED), price fully extended
- **TBBB** | priority=CONFLICTED_SIGNAL | earliness=EXTENDED | consensus=SINGLE_SIGNAL | qscore=0 | confidence=LOW | ext=PARABOLIC | escore=30 | sector=Consumer Defensive
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
- **TGTX** | priority=CONFLICTED_SIGNAL | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=0 | confidence=LOW | ext=PARABOLIC | escore=15 | sector=Healthcare
  - *Catalyst sanity:* NEEDS_MANUAL_SOURCE_CHECK (tape_extended)
  - **CONFLICTS:** catalyst_not_validated
  - *Downgraded:* catalyst_not_validated
  - *Why appeared:* Social attention signal (source: social_attention_radar)
  - *Confirms if:* Early attention + price not yet extended + fundamental support
  - *Invalidates if:* Already widely discussed (CROWDED), price fully extended
- **NUAI** | priority=CONFLICTED_SIGNAL | earliness=EXTENDED | consensus=SINGLE_SIGNAL | qscore=0 | confidence=LOW | ext=PARABOLIC | escore=30 | sector=Energy
  - *Catalyst sanity:* NEEDS_MANUAL_SOURCE_CHECK (tape_extended)
  - **CONFLICTS:** catalyst_not_validated
  - *Downgraded:* catalyst_not_validated
  - *Why appeared:* Social attention signal (source: social_attention_radar)
  - *Confirms if:* Early attention + price not yet extended + fundamental support
  - *Invalidates if:* Already widely discussed (CROWDED), price fully extended
- **BNED** | priority=CONFLICTED_SIGNAL | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=0 | confidence=LOW | ext=PARABOLIC | escore=15 | sector=Consumer Cyclical
  - *Catalyst sanity:* HYPE_CROWDED (tape_extended, extended_into_earnings)
  - **CONFLICTS:** risky_with_catalyst, catalyst_not_validated
  - *Downgraded:* risky_with_catalyst, catalyst_not_validated
  - *Why appeared:* Upcoming earnings (2026-07-01); analyst upgrade
  - *Confirms if:* Guidance raised, strong beat, volume expands post-earnings
  - *Invalidates if:* Miss + guide down, volume collapses, extended into print
*... and 12 more (see JSON sidecar)*

## Data Quarantine

- **BTQ** | priority=DATA_QUARANTINE | earliness=UNKNOWN | consensus=MULTI_CONFIRMATION | qscore=50 | confidence=LOW | ext=NORMAL | escore=0 | sector=Technology [INSUFFICIENT_HISTORY]
- **NAVN** | priority=DATA_QUARANTINE | earliness=UNKNOWN | consensus=SINGLE_SIGNAL | qscore=35 | confidence=HIGH | ext=NORMAL | escore=0 | sector=Technology [INSUFFICIENT_HISTORY]
- **PEP** | priority=DATA_QUARANTINE | earliness=INVALIDATED | consensus=SINGLE_SIGNAL | qscore=35 | confidence=MEDIUM | ext=NORMAL | escore=5 | sector=Consumer Defensive [DATA_QUARANTINE]
- **SHAZ** | priority=DATA_QUARANTINE | earliness=UNKNOWN | consensus=SINGLE_SIGNAL | qscore=34 | confidence=LOW | ext=NORMAL | escore=0 | sector=Technology [INSUFFICIENT_HISTORY]
- **PURR** | priority=DATA_QUARANTINE | earliness=UNKNOWN | consensus=SINGLE_SIGNAL | qscore=34 | confidence=LOW | ext=NORMAL | escore=0 | sector=Financial Services [INSUFFICIENT_HISTORY]

## Social / Catalyst Anomalies

- **HNGE** | priority=EXTENDED_CROWDED | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=14 | confidence=LOW | ext=PARABOLIC | escore=15 | sector=Healthcare
  - *Catalyst sanity:* NEEDS_MANUAL_SOURCE_CHECK (tape_extended)
  - *Downgraded:* too_extended
  - *Why appeared:* Social attention signal (source: social_attention_radar)
  - *Confirms if:* Early attention + price not yet extended + fundamental support
  - *Invalidates if:* Already widely discussed (CROWDED), price fully extended
- **BFLY** | priority=CONFLICTED_SIGNAL | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=0 | confidence=LOW | ext=PARABOLIC | escore=15 | sector=Healthcare
  - *Catalyst sanity:* NEEDS_MANUAL_SOURCE_CHECK (tape_extended)
  - **CONFLICTS:** catalyst_not_validated
  - *Downgraded:* catalyst_not_validated
  - *Why appeared:* Social attention signal (source: social_attention_radar)
  - *Confirms if:* Early attention + price not yet extended + fundamental support
  - *Invalidates if:* Already widely discussed (CROWDED), price fully extended
- **ALHC** | priority=WATCHLIST_RESEARCH | earliness=DEVELOPING | consensus=SINGLE_SIGNAL | qscore=37 | confidence=LOW | ext=STRETCHED | escore=65 | sector=Healthcare
  - *Downgraded:* confidence_LOW, only_single_signal
  - *Why appeared:* Social attention signal (source: social_attention_radar)
  - *Confirms if:* Early attention + price not yet extended + fundamental support
  - *Invalidates if:* Already widely discussed (CROWDED), price fully extended
- **JEF** | priority=RECLAIM_WATCH | earliness=RECLAIM_WATCH | consensus=SINGLE_SIGNAL | qscore=37 | confidence=LOW | ext=NORMAL | escore=45 | sector=Financial Services
  - *Why appeared:* Social attention signal (source: social_attention_radar)
  - *Confirms if:* Early attention + price not yet extended + fundamental support
  - *Invalidates if:* Already widely discussed (CROWDED), price fully extended
- **PD** | priority=WATCHLIST_RESEARCH | earliness=EARLY | consensus=SINGLE_SIGNAL | qscore=37 | confidence=LOW | ext=NORMAL | escore=90 | sector=Technology
  - *Downgraded:* confidence_LOW, only_single_signal
  - *Why appeared:* Social attention signal (source: social_attention_radar)
  - *Confirms if:* Early attention + price not yet extended + fundamental support
  - *Invalidates if:* Already widely discussed (CROWDED), price fully extended
- **TBBB** | priority=CONFLICTED_SIGNAL | earliness=EXTENDED | consensus=SINGLE_SIGNAL | qscore=0 | confidence=LOW | ext=PARABOLIC | escore=30 | sector=Consumer Defensive
  - *Catalyst sanity:* NEEDS_MANUAL_SOURCE_CHECK (tape_extended)
  - **CONFLICTS:** catalyst_not_validated
  - *Downgraded:* catalyst_not_validated
  - *Why appeared:* Social attention signal (source: social_attention_radar)
  - *Confirms if:* Early attention + price not yet extended + fundamental support
  - *Invalidates if:* Already widely discussed (CROWDED), price fully extended
- **TGTX** | priority=CONFLICTED_SIGNAL | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=0 | confidence=LOW | ext=PARABOLIC | escore=15 | sector=Healthcare
  - *Catalyst sanity:* NEEDS_MANUAL_SOURCE_CHECK (tape_extended)
  - **CONFLICTS:** catalyst_not_validated
  - *Downgraded:* catalyst_not_validated
  - *Why appeared:* Social attention signal (source: social_attention_radar)
  - *Confirms if:* Early attention + price not yet extended + fundamental support
  - *Invalidates if:* Already widely discussed (CROWDED), price fully extended
- **NUAI** | priority=CONFLICTED_SIGNAL | earliness=EXTENDED | consensus=SINGLE_SIGNAL | qscore=0 | confidence=LOW | ext=PARABOLIC | escore=30 | sector=Energy
  - *Catalyst sanity:* NEEDS_MANUAL_SOURCE_CHECK (tape_extended)
  - **CONFLICTS:** catalyst_not_validated
  - *Downgraded:* catalyst_not_validated
  - *Why appeared:* Social attention signal (source: social_attention_radar)
  - *Confirms if:* Early attention + price not yet extended + fundamental support
  - *Invalidates if:* Already widely discussed (CROWDED), price fully extended
- **WGS** | priority=RECLAIM_WATCH | earliness=RECLAIM_WATCH | consensus=SINGLE_SIGNAL | qscore=35 | confidence=LOW | ext=NORMAL | escore=45 | sector=Healthcare
  - *Why appeared:* Social attention signal (source: social_attention_radar)
  - *Confirms if:* Early attention + price not yet extended + fundamental support
  - *Invalidates if:* Already widely discussed (CROWDED), price fully extended
- **CASY** | priority=CONFLICTED_SIGNAL | earliness=EXTENDED | consensus=SINGLE_SIGNAL | qscore=0 | confidence=LOW | ext=EXTENDED | escore=30 | sector=Consumer Cyclical
  - *Catalyst sanity:* NEEDS_MANUAL_SOURCE_CHECK (tape_extended)
  - **CONFLICTS:** catalyst_not_validated
  - *Downgraded:* catalyst_not_validated
  - *Why appeared:* Social attention signal (source: social_attention_radar)
  - *Confirms if:* Early attention + price not yet extended + fundamental support
  - *Invalidates if:* Already widely discussed (CROWDED), price fully extended
*... and 1 more (see JSON sidecar)*

## True 10x Research Candidates

> ⚠ TRUE_10X_RESEARCH requires: confirmed speculative/structural theme + small-cap base + price recovery evidence. High risk. Manual deep research required.

*(none — stricter criteria require theme + small-cap + confirmed price recovery)*

## Asymmetric Recovery Watch

> ASYMMETRIC_RECOVERY_WATCH: price/volume recovery signals only. Theme/fundamental thesis unconfirmed. Not the same as TRUE_10X_RESEARCH.

- **ADIL** | dd=-53.27% | rs63=37.07 | vol_trend=2.489 | score=86 | [price/volume only — no confirmed thesis]
- **AI** | dd=-61.35% | rs63=11.52 | vol_trend=1.609 | score=86 | [price/volume only — no confirmed thesis]
- **ALOY** | dd=-45.67% | rs63=2.35 | vol_trend=1.455 | score=86 | [price/volume only — no confirmed thesis]
- **AACBR** | dd=-55.26% | rs63=-70.22 | vol_trend=2.776 | score=69 | [price/volume only — no confirmed thesis]
- **AACIW** | dd=-73.21% | rs63=-88.18 | vol_trend=1.842 | score=69 | [price/volume only — no confirmed thesis]
- **AAL** | dd=-1.66% | rs63=38.34 | vol_trend=1.502 | score=69 | [price/volume only — no confirmed thesis]
- **AAME** | dd=-41.45% | rs63=-50.71 | vol_trend=1.969 | score=69 | [price/volume only — no confirmed thesis]
- **ABLVW** | dd=-44.74% | rs63=-21.57 | vol_trend=1.281 | score=69 | [price/volume only — no confirmed thesis]
- **ABM** | dd=-2.2% | rs63=4.99 | vol_trend=1.551 | score=69 | [price/volume only — no confirmed thesis]
- **AD** | dd=0.0% | rs63=18.38 | vol_trend=1.809 | score=69 | [price/volume only — no confirmed thesis]

## Extended / Crowded / Avoid

- **SNX** | priority=EXTENDED_CROWDED | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=43 | confidence=HIGH | ext=PARABOLIC | escore=15 | sector=Technology
- **LRCX** | priority=EXTENDED_CROWDED | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=43 | confidence=MEDIUM | ext=PARABOLIC | escore=15 | sector=Technology
- **AAL** | priority=EXTENDED_CROWDED | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=43 | confidence=MEDIUM | ext=PARABOLIC | escore=15 | sector=Industrials
- **UMAC** | priority=EXTENDED_CROWDED | earliness=EXTENDED | consensus=SINGLE_SIGNAL | qscore=30 | confidence=LOW | ext=PARABOLIC | escore=30 | sector=Technology
- **BRUN** | priority=EXTENDED_CROWDED | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=28 | confidence=LOW | ext=PARABOLIC | escore=15 | sector=Technology
- **STI** | priority=EXTENDED_CROWDED | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=28 | confidence=LOW | ext=PARABOLIC | escore=15 | sector=Industrials
- **INDP** | priority=EXTENDED_CROWDED | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=28 | confidence=LOW | ext=PARABOLIC | escore=15 | sector=Healthcare
- **AERT** | priority=EXTENDED_CROWDED | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=28 | confidence=LOW | ext=PARABOLIC | escore=15 | sector=Industrials
- **TKR** | priority=EXTENDED_CROWDED | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=25 | confidence=HIGH | ext=PARABOLIC | escore=15 | sector=Industrials
- **OSCR** | priority=EXTENDED_CROWDED | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=25 | confidence=HIGH | ext=PARABOLIC | escore=15 | sector=Healthcare
*... and 13 more (see JSON sidecar)*

## Forward Tracker Status

**Overall:** n=0 matured | sample_status=TOO_EARLY | verdict=NEED_MORE_DATA

> ⚠ TOO_EARLY: 0 matured observations. Need ≥10 for provisional read, ≥30 for meaningful, ≥100 for robust. No bucket has interpretable evidence yet.

**Benchmarks:** SPY/QQQ ready for 0/746 entries | sector ETF assigned for 632/746

| Bucket | n_total | n_matured | sample_status | verdict |
|--------|---------|-----------|---------------|---------|
| ASYMMETRIC_RECOVERY_WATCH |     112 |         0 | TOO_EARLY     | NEED_MORE_DATA |
| BEATEN_DOWN            |     140 |         0 | TOO_EARLY     | NEED_MORE_DATA |
| CATALYST               |      92 |         0 | TOO_EARLY     | NEED_MORE_DATA |
| EARLY_ACCUMULATION     |     182 |         0 | TOO_EARLY     | NEED_MORE_DATA |
| EXTENDED               |      28 |         0 | TOO_EARLY     | NEED_MORE_DATA |
| NO_SOCIAL_DATA         |      15 |         0 | TOO_EARLY     | NEED_MORE_DATA |
| RISKY                  |      19 |         0 | TOO_EARLY     | NEED_MORE_DATA |
| SECTOR_LEADER          |      74 |         0 | TOO_EARLY     | NEED_MORE_DATA |
| SOCIAL_ARB             |       2 |         0 | TOO_EARLY     | NEED_MORE_DATA |
| SPECULATIVE_10X        |      21 |         0 | TOO_EARLY     | NEED_MORE_DATA |
| WATCH                  |      61 |         0 | TOO_EARLY     | NEED_MORE_DATA |

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
*Generated: 2026-06-27T16:00:18.006475+00:00 | DAILY_ALPHA_RADAR_V1*

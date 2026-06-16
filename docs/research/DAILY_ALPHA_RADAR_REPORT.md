======================================================================
  RESEARCH_ONLY_MODE — NO AUTO TRADING — HUMAN REVIEW ONLY
  Broker execution, paper-trade routing, and Alpaca are disabled.
  FMP + Tradier (research) remain active.
======================================================================

# Daily Alpha Radar — 2026-06-16

**Version:** DAILY_ALPHA_RADAR_V1 | **Mode:** RESEARCH_ONLY | **Research-Only**

*Candidates: 52 scanned | TOP_RESEARCH: 0 | HIGH_PRIORITY: 5 | DATA_QUARANTINE: 25*

---

## Market Context

**Regime:** UNKNOWN | **Trend:** UNKNOWN | *as of 2026-06-15*

## Data Coverage

**Tickers:** 5618 total | Actionable (HIGH+MEDIUM): 94.9%
  HIGH=42 | MEDIUM=5290 | LOW=227 | INVALID=59

**Options Coverage:** 0% | Overlay: DISABLED
  > ⚠ OPTIONS_DATA_UNAVAILABLE: coverage below 50% threshold. No candidate will be promoted based on options data.

## Scanner Field Coverage

| Field | Coverage |
|-------|----------|
| `above_ma200` populated | 29/52 (55%) |
| `above_ma50` populated | 52/52 (100%) |
| `rs_63d_vs_spy` populated | 52/52 (100%) |
| `sector` populated | 52/52 (100%) |
| `liquidity_ok` populated | 52/52 (100%) |
| Earliness non-UNKNOWN | 29/52 (55%) |

**Quarantine breakdown:**
  - INSUFFICIENT_HISTORY: 23
  - DATA_QUARANTINE: 2

## What Changed Today

**Summary:** no significant changes

## Top Research Candidates — Quality Adjusted

- **HPQ** | priority=HIGH_PRIORITY_RESEARCH | earliness=DEVELOPING | consensus=HIGH_PRIORITY_RESEARCH | qscore=100 | confidence=MEDIUM | ext=NORMAL | escore=75 | sector=Technology
  - *Why appeared:* Outperforming SPY by +15.7pp over 20d; above 50d MA
  - *Confirms if:* RS sustains, sector ETF stays in leadership, volume confirms
  - *Invalidates if:* RS reverses, sector rotates out, undercuts 50d MA
- **AI** | priority=HIGH_PRIORITY_RESEARCH | earliness=EARLY | consensus=DOUBLE_CONFIRMATION | qscore=80 | confidence=MEDIUM | ext=NORMAL | escore=95 | sector=Technology
  - *Why appeared:* Large drawdown (27%/3m) with stabilization pattern
  - *Confirms if:* Price reclaims 50d MA on volume, RS turns positive, catalytic news
  - *Invalidates if:* New lows, accelerating selling, fundamental deterioration
- **BHVN** | priority=HIGH_PRIORITY_RESEARCH | earliness=EARLY | consensus=DOUBLE_CONFIRMATION | qscore=80 | confidence=MEDIUM | ext=NORMAL | escore=90 | sector=Healthcare
  - *Why appeared:* Speculative growth theme + price momentum; requires manual research
  - *Confirms if:* Revenue growth accelerates, expanding gross margin, theme tailwind
  - *Invalidates if:* Revenue decelerates, balance sheet stress, theme fades

## Early Accumulation — Clean Only

- **AAL** | priority=HIGH_PRIORITY_RESEARCH | earliness=DEVELOPING | consensus=DOUBLE_CONFIRMATION | qscore=83 | confidence=HIGH | ext=STRETCHED | escore=75 | sector=Industrials
  - *Why appeared:* Rising volume + improving RS or higher lows; not extended
  - *Confirms if:* RS continues rising, volume expands on up-days, reclaims 50d MA
  - *Invalidates if:* Volume dries up, RS reverses, undercuts recent lows
- **RGTI** | priority=HIGH_PRIORITY_RESEARCH | earliness=DEVELOPING | consensus=DOUBLE_CONFIRMATION | qscore=80 | confidence=MEDIUM | ext=STRETCHED | escore=75 | sector=Technology
  - *Why appeared:* Rising volume + improving RS or higher lows; not extended
  - *Confirms if:* RS continues rising, volume expands on up-days, reclaims 50d MA
  - *Invalidates if:* Volume dries up, RS reverses, undercuts recent lows

## Reclaim / Reset Watch

- **XPO** | priority=RESET_WATCH | earliness=EXTENDED | consensus=HIGH_PRIORITY_RESEARCH | qscore=70 | confidence=MEDIUM | ext=PARABOLIC | escore=30 | sector=Industrials
  - *Downgraded:* extension_high_consensus
  - *Why appeared:* Outperforming SPY by +9.0pp over 20d; above 50d MA
  - *Confirms if:* RS sustains, sector ETF stays in leadership, volume confirms
  - *Invalidates if:* RS reverses, sector rotates out, undercuts 50d MA
- **RKLB** | priority=RESET_WATCH | earliness=LATE | consensus=DOUBLE_CONFIRMATION | qscore=60 | confidence=MEDIUM | ext=PARABOLIC | escore=15 | sector=Industrials
  - *Downgraded:* extension_high_consensus
  - *Why appeared:* Rising volume + improving RS or higher lows; not extended
  - *Confirms if:* RS continues rising, volume expands on up-days, reclaims 50d MA
  - *Invalidates if:* Volume dries up, RS reverses, undercuts recent lows
- **OUST** | priority=RESET_WATCH | earliness=LATE | consensus=DOUBLE_CONFIRMATION | qscore=60 | confidence=MEDIUM | ext=PARABOLIC | escore=15 | sector=Technology
  - *Downgraded:* extension_high_consensus
  - *Why appeared:* Rising volume + improving RS or higher lows; not extended
  - *Confirms if:* RS continues rising, volume expands on up-days, reclaims 50d MA
  - *Invalidates if:* Volume dries up, RS reverses, undercuts recent lows
- **BB** | priority=RESET_WATCH | earliness=LATE | consensus=DOUBLE_CONFIRMATION | qscore=60 | confidence=MEDIUM | ext=PARABOLIC | escore=15 | sector=Technology
  - *Downgraded:* extension_high_consensus
  - *Why appeared:* Outperforming SPY by +50.2pp over 20d; above 50d MA
  - *Confirms if:* RS sustains, sector ETF stays in leadership, volume confirms
  - *Invalidates if:* RS reverses, sector rotates out, undercuts 50d MA

## Conflicted Signals

- **ASTS** | priority=CONFLICTED_SIGNAL | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=0 | confidence=MEDIUM | ext=PARABOLIC | escore=15 | sector=Technology
  - *Catalyst sanity:* NEEDS_MANUAL_SOURCE_CHECK (tape_extended)
  - **CONFLICTS:** catalyst_not_validated
  - *Downgraded:* catalyst_not_validated
  - *Why appeared:* Social attention signal (source: social_attention_radar)
  - *Confirms if:* Early attention + price not yet extended + fundamental support
  - *Invalidates if:* Already widely discussed (CROWDED), price fully extended

## Data Quarantine

- **MX** | priority=DATA_QUARANTINE | earliness=UNKNOWN | consensus=HIGH_PRIORITY_RESEARCH | qscore=70 | confidence=MEDIUM | ext=NORMAL | escore=0 | sector=Technology [INSUFFICIENT_HISTORY]
- **AERT** | priority=DATA_QUARANTINE | earliness=UNKNOWN | consensus=SINGLE_SIGNAL | qscore=58 | confidence=MEDIUM | ext=NORMAL | escore=0 | sector=Industrials [INSUFFICIENT_HISTORY]
- **SPOT** | priority=DATA_QUARANTINE | earliness=INVALIDATED | consensus=DOUBLE_CONFIRMATION | qscore=55 | confidence=HIGH | ext=NORMAL | escore=5 | sector=Communication Services [DATA_QUARANTINE]
- **CRSR** | priority=DATA_QUARANTINE | earliness=UNKNOWN | consensus=SINGLE_SIGNAL | qscore=54 | confidence=MEDIUM | ext=NORMAL | escore=0 | sector=Technology [INSUFFICIENT_HISTORY]
- **SHAZ** | priority=DATA_QUARANTINE | earliness=UNKNOWN | consensus=SINGLE_SIGNAL | qscore=54 | confidence=MEDIUM | ext=NORMAL | escore=0 | sector=Technology [INSUFFICIENT_HISTORY]
- **INFQ** | priority=DATA_QUARANTINE | earliness=UNKNOWN | consensus=SINGLE_SIGNAL | qscore=50 | confidence=MEDIUM | ext=NORMAL | escore=0 | sector=Technology [INSUFFICIENT_HISTORY]
- **ACVA** | priority=DATA_QUARANTINE | earliness=UNKNOWN | consensus=SINGLE_SIGNAL | qscore=50 | confidence=MEDIUM | ext=NORMAL | escore=0 | sector=Consumer Cyclical [INSUFFICIENT_HISTORY]
- **VELO** | priority=DATA_QUARANTINE | earliness=UNKNOWN | consensus=SINGLE_SIGNAL | qscore=50 | confidence=MEDIUM | ext=NORMAL | escore=0 | sector=Technology [INSUFFICIENT_HISTORY]
- **MGNI** | priority=DATA_QUARANTINE | earliness=UNKNOWN | consensus=SINGLE_SIGNAL | qscore=50 | confidence=MEDIUM | ext=NORMAL | escore=0 | sector=Communication Services [INSUFFICIENT_HISTORY]
- **BTU** | priority=DATA_QUARANTINE | earliness=UNKNOWN | consensus=SINGLE_SIGNAL | qscore=50 | confidence=MEDIUM | ext=NORMAL | escore=0 | sector=Energy [INSUFFICIENT_HISTORY]
- **AD** | priority=DATA_QUARANTINE | earliness=UNKNOWN | consensus=SINGLE_SIGNAL | qscore=48 | confidence=MEDIUM | ext=NORMAL | escore=0 | sector=Communication Services [INSUFFICIENT_HISTORY]
- **ADUR** | priority=DATA_QUARANTINE | earliness=UNKNOWN | consensus=SINGLE_SIGNAL | qscore=48 | confidence=MEDIUM | ext=NORMAL | escore=0 | sector=Industrials [INSUFFICIENT_HISTORY]
- **ACN** | priority=DATA_QUARANTINE | earliness=UNKNOWN | consensus=SINGLE_SIGNAL | qscore=38 | confidence=MEDIUM | ext=NORMAL | escore=0 | sector=Technology [INSUFFICIENT_HISTORY]
- **ABT** | priority=DATA_QUARANTINE | earliness=INVALIDATED | consensus=SINGLE_SIGNAL | qscore=35 | confidence=MEDIUM | ext=NORMAL | escore=5 | sector=Healthcare [DATA_QUARANTINE]
- **AACIW** | priority=DATA_QUARANTINE | earliness=UNKNOWN | consensus=SINGLE_SIGNAL | qscore=30 | confidence=MEDIUM | ext=NORMAL | escore=0 | sector=Financial Services [INSUFFICIENT_HISTORY]
*... and 10 more (see JSON sidecar)*

## Social / Catalyst Anomalies

- **BTU** | priority=DATA_QUARANTINE | earliness=UNKNOWN | consensus=SINGLE_SIGNAL | qscore=50 | confidence=MEDIUM | ext=NORMAL | escore=0 | sector=Energy
  - *Quarantine reason:* INSUFFICIENT_HISTORY
  - *Downgraded:* earliness_UNKNOWN, missing_fields:above_ma200,extension_vs_ma200_pct,ma20_extension
  - *Missing fields:* above_ma200
  - *Why appeared:* Social attention signal (source: social_attention_radar)
  - *Confirms if:* Early attention + price not yet extended + fundamental support
  - *Invalidates if:* Already widely discussed (CROWDED), price fully extended
- **SPOT** | priority=DATA_QUARANTINE | earliness=INVALIDATED | consensus=DOUBLE_CONFIRMATION | qscore=55 | confidence=HIGH | ext=NORMAL | escore=5 | sector=Communication Services
  - *Quarantine reason:* DATA_QUARANTINE
  - *Downgraded:* ticker_INVALIDATED
  - *Why appeared:* Social attention signal (source: social_attention_radar)
  - *Confirms if:* Early attention + price not yet extended + fundamental support
  - *Invalidates if:* Already widely discussed (CROWDED), price fully extended
- **FJET** | priority=DATA_QUARANTINE | earliness=UNKNOWN | consensus=SINGLE_SIGNAL | qscore=15 | confidence=MEDIUM | ext=NORMAL | escore=0 | sector=Industrials
  - *Quarantine reason:* INSUFFICIENT_HISTORY
  - *Downgraded:* earliness_UNKNOWN, missing_fields:above_ma200,extension_vs_ma200_pct,ma20_extension
  - *Missing fields:* above_ma200
  - *Why appeared:* Social attention signal (source: social_attention_radar)
  - *Confirms if:* Early attention + price not yet extended + fundamental support
  - *Invalidates if:* Already widely discussed (CROWDED), price fully extended
- **ASTS** | priority=CONFLICTED_SIGNAL | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=0 | confidence=MEDIUM | ext=PARABOLIC | escore=15 | sector=Technology
  - *Catalyst sanity:* NEEDS_MANUAL_SOURCE_CHECK (tape_extended)
  - **CONFLICTS:** catalyst_not_validated
  - *Downgraded:* catalyst_not_validated
  - *Why appeared:* Social attention signal (source: social_attention_radar)
  - *Confirms if:* Early attention + price not yet extended + fundamental support
  - *Invalidates if:* Already widely discussed (CROWDED), price fully extended

## True 10x Research Candidates

> ⚠ TRUE_10X_RESEARCH requires: confirmed speculative/structural theme + small-cap base + price recovery evidence. High risk. Manual deep research required.

*(none — stricter criteria require theme + small-cap + confirmed price recovery)*

## Asymmetric Recovery Watch

> ASYMMETRIC_RECOVERY_WATCH: price/volume recovery signals only. Theme/fundamental thesis unconfirmed. Not the same as TRUE_10X_RESEARCH.

- **ADIL** | dd=-53.27% | rs63=39.51 | vol_trend=2.489 | score=86 | [price/volume only — no confirmed thesis]
- **AI** | dd=-61.35% | rs63=13.96 | vol_trend=1.609 | score=86 | [price/volume only — no confirmed thesis]
- **ALOY** | dd=-45.67% | rs63=4.78 | vol_trend=1.455 | score=86 | [price/volume only — no confirmed thesis]
- **AACBR** | dd=-55.26% | rs63=-67.78 | vol_trend=2.776 | score=69 | [price/volume only — no confirmed thesis]
- **AACIW** | dd=-73.21% | rs63=-85.74 | vol_trend=1.842 | score=69 | [price/volume only — no confirmed thesis]
- **AAL** | dd=-7.87% | rs63=30.27 | vol_trend=1.351 | score=69 | [price/volume only — no confirmed thesis]
- **AAME** | dd=-41.45% | rs63=-48.27 | vol_trend=1.969 | score=69 | [price/volume only — no confirmed thesis]
- **ABLVW** | dd=-44.74% | rs63=-19.13 | vol_trend=1.281 | score=69 | [price/volume only — no confirmed thesis]
- **ABM** | dd=-2.2% | rs63=7.43 | vol_trend=1.551 | score=69 | [price/volume only — no confirmed thesis]
- **AD** | dd=0.0% | rs63=20.82 | vol_trend=1.809 | score=69 | [price/volume only — no confirmed thesis]

## Extended / Crowded / Avoid

- **ENPH** | priority=EXTENDED_CROWDED | earliness=EXTENDED | consensus=SINGLE_SIGNAL | qscore=50 | confidence=MEDIUM | ext=PARABOLIC | escore=30 | sector=Energy
- **PL** | priority=EXTENDED_CROWDED | earliness=EXTENDED | consensus=SINGLE_SIGNAL | qscore=50 | confidence=MEDIUM | ext=PARABOLIC | escore=30 | sector=Industrials
- **AAOI** | priority=EXTENDED_CROWDED | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=49 | confidence=HIGH | ext=PARABOLIC | escore=15 | sector=Technology
- **SIDU** | priority=EXTENDED_CROWDED | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=40 | confidence=MEDIUM | ext=PARABOLIC | escore=15 | sector=Industrials
- **NVTS** | priority=EXTENDED_CROWDED | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=40 | confidence=MEDIUM | ext=PARABOLIC | escore=15 | sector=Technology
- **SPIR** | priority=EXTENDED_CROWDED | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=40 | confidence=MEDIUM | ext=PARABOLIC | escore=15 | sector=Industrials
- **VOYG** | priority=EXTENDED_CROWDED | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=40 | confidence=MEDIUM | ext=PARABOLIC | escore=15 | sector=Industrials
- **AMBQ** | priority=EXTENDED_CROWDED | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=40 | confidence=MEDIUM | ext=PARABOLIC | escore=15 | sector=Technology
- **FLY** | priority=EXTENDED_CROWDED | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=40 | confidence=MEDIUM | ext=PARABOLIC | escore=15 | sector=Industrials
- **VSH** | priority=EXTENDED_CROWDED | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=38 | confidence=MEDIUM | ext=PARABOLIC | escore=15 | sector=Technology
*... and 4 more (see JSON sidecar)*

## Forward Tracker Status

**Overall:** n=0 matured | sample_status=TOO_EARLY | verdict=NEED_MORE_DATA

> ⚠ TOO_EARLY: 0 matured observations. Need ≥10 for provisional read, ≥30 for meaningful, ≥100 for robust. No bucket has interpretable evidence yet.

| Bucket | n_total | n_matured | sample_status | verdict |
|--------|---------|-----------|---------------|---------|
| BEATEN_DOWN            |      33 |         0 | TOO_EARLY     | NEED_MORE_DATA |
| CATALYST               |       2 |         0 | TOO_EARLY     | NEED_MORE_DATA |
| EARLY_ACCUMULATION     |      33 |         0 | TOO_EARLY     | NEED_MORE_DATA |
| EXTENDED               |       4 |         0 | TOO_EARLY     | NEED_MORE_DATA |
| RISKY                  |       6 |         0 | TOO_EARLY     | NEED_MORE_DATA |
| SECTOR_LEADER          |      19 |         0 | TOO_EARLY     | NEED_MORE_DATA |
| SPECULATIVE_10X        |      21 |         0 | TOO_EARLY     | NEED_MORE_DATA |

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
*Generated: 2026-06-16T17:50:38.611542+00:00 | DAILY_ALPHA_RADAR_V1*

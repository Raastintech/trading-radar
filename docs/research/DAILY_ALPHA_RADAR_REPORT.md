======================================================================
  RESEARCH_ONLY_MODE — NO AUTO TRADING — HUMAN REVIEW ONLY
  Broker execution, paper-trade routing, and Alpaca are disabled.
  FMP + Tradier (research) remain active.
======================================================================

# Daily Alpha Radar — 2026-06-19

**Version:** DAILY_ALPHA_RADAR_V1 | **Mode:** RESEARCH_ONLY | **Research-Only**

*Candidates: 56 scanned | TOP_RESEARCH: 0 | HIGH_PRIORITY: 0 | DATA_QUARANTINE: 16*

---

## Market Context

**Regime:** SMALL_CAP_LED | **Trend:** PULLBACK | *as of 2026-06-19*

## Data Coverage

**Tickers:** 5619 total | Actionable (HIGH+MEDIUM): 3.9%
  HIGH=40 | MEDIUM=177 | LOW=5343 | INVALID=59

**Options Coverage:** 4% | Overlay: DISABLED
  > ⚠ OPTIONS_DATA_UNAVAILABLE: coverage below 50% threshold. No candidate will be promoted based on options data.

## Scanner Field Coverage

| Field | Coverage |
|-------|----------|
| `above_ma200` populated | 49/56 (87%) |
| `above_ma50` populated | 56/56 (100%) |
| `rs_63d_vs_spy` populated | 56/56 (100%) |
| `sector` populated | 56/56 (100%) |
| `liquidity_ok` populated | 56/56 (100%) |
| Earliness non-UNKNOWN | 49/56 (87%) |

**Quarantine breakdown:**
  - DATA_QUARANTINE: 9
  - INSUFFICIENT_HISTORY: 7

## What Changed Today

**Summary:** no significant changes

## Top Research Candidates — Quality Adjusted

*(none)*

## Early Accumulation — Clean Only

*(none)*

## Reclaim / Reset Watch

- **XPO** | priority=RESET_WATCH | earliness=EXTENDED | consensus=HIGH_PRIORITY_RESEARCH | qscore=70 | confidence=MEDIUM | ext=PARABOLIC | escore=30 | sector=Industrials
  - *Downgraded:* extension_high_consensus
  - *Why appeared:* Outperforming SPY by +10.3pp over 20d; above 50d MA
  - *Confirms if:* RS sustains, sector ETF stays in leadership, volume confirms
  - *Invalidates if:* RS reverses, sector rotates out, undercuts 50d MA
- **ASX** | priority=RESET_WATCH | earliness=LATE | consensus=DOUBLE_CONFIRMATION | qscore=60 | confidence=HIGH | ext=PARABOLIC | escore=15 | sector=Technology
  - *Downgraded:* extension_high_consensus
  - *Why appeared:* Rising volume + improving RS or higher lows; not extended
  - *Confirms if:* RS continues rising, volume expands on up-days, reclaims 50d MA
  - *Invalidates if:* Volume dries up, RS reverses, undercuts recent lows
- **RCAT** | priority=RECLAIM_WATCH | earliness=RECLAIM_WATCH | consensus=SINGLE_SIGNAL | qscore=40 | confidence=LOW | ext=NORMAL | escore=45 | sector=Technology
  - *Why appeared:* Large drawdown (-25%/3m) with stabilization pattern
  - *Confirms if:* Price reclaims 50d MA on volume, RS turns positive, catalytic news
  - *Invalidates if:* New lows, accelerating selling, fundamental deterioration
- **SPIR** | priority=RESET_WATCH | earliness=LATE | consensus=MULTI_CONFIRMATION | qscore=40 | confidence=LOW | ext=PARABOLIC | escore=15 | sector=Industrials
  - *Downgraded:* extension_high_consensus
  - *Why appeared:* Outperforming SPY by +8.7pp over 20d; above 50d MA
  - *Confirms if:* RS sustains, sector ETF stays in leadership, volume confirms
  - *Invalidates if:* RS reverses, sector rotates out, undercuts 50d MA

## Conflicted Signals

- **MU** | priority=CONFLICTED_SIGNAL | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=8 | confidence=HIGH | ext=PARABOLIC | escore=15 | sector=Technology
  - **CONFLICTS:** catalyst_not_validated
  - *Downgraded:* catalyst_not_validated
  - *Why appeared:* Upcoming earnings (2026-06-24)
  - *Confirms if:* Guidance raised, strong beat, volume expands post-earnings
  - *Invalidates if:* Miss + guide down, volume collapses, extended into print
- **SNX** | priority=CONFLICTED_SIGNAL | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=8 | confidence=HIGH | ext=PARABOLIC | escore=15 | sector=Technology
  - **CONFLICTS:** catalyst_not_validated
  - *Downgraded:* catalyst_not_validated
  - *Why appeared:* Upcoming earnings (2026-06-25)
  - *Confirms if:* Guidance raised, strong beat, volume expands post-earnings
  - *Invalidates if:* Miss + guide down, volume collapses, extended into print
- **FLY** | priority=CONFLICTED_SIGNAL | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=0 | confidence=LOW | ext=PARABOLIC | escore=15 | sector=Industrials
  - **CONFLICTS:** catalyst_not_validated
  - *Downgraded:* catalyst_not_validated
  - *Why appeared:* Upcoming earnings (2026-07-10); analyst upgrade
  - *Confirms if:* Guidance raised, strong beat, volume expands post-earnings
  - *Invalidates if:* Miss + guide down, volume collapses, extended into print
- **GSIT** | priority=CONFLICTED_SIGNAL | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=0 | confidence=LOW | ext=PARABOLIC | escore=15 | sector=Technology
  - *Catalyst sanity:* NEEDS_MANUAL_SOURCE_CHECK (tape_extended)
  - **CONFLICTS:** catalyst_not_validated
  - *Downgraded:* catalyst_not_validated
  - *Why appeared:* Social attention signal (source: social_attention_radar)
  - *Confirms if:* Early attention + price not yet extended + fundamental support
  - *Invalidates if:* Already widely discussed (CROWDED), price fully extended

## Data Quarantine

- **NAVN** | priority=DATA_QUARANTINE | earliness=UNKNOWN | consensus=SINGLE_SIGNAL | qscore=48 | confidence=HIGH | ext=NORMAL | escore=0 | sector=Technology [INSUFFICIENT_HISTORY]
- **ABT** | priority=DATA_QUARANTINE | earliness=INVALIDATED | consensus=SINGLE_SIGNAL | qscore=35 | confidence=MEDIUM | ext=NORMAL | escore=5 | sector=Healthcare [DATA_QUARANTINE]
- **INFQ** | priority=DATA_QUARANTINE | earliness=UNKNOWN | consensus=SINGLE_SIGNAL | qscore=30 | confidence=LOW | ext=NORMAL | escore=0 | sector=Technology [INSUFFICIENT_HISTORY]
- **ACIU** | priority=DATA_QUARANTINE | earliness=INVALIDATED | consensus=SINGLE_SIGNAL | qscore=15 | confidence=LOW | ext=NORMAL | escore=5 | sector=Healthcare [DATA_QUARANTINE]
- **AEM** | priority=DATA_QUARANTINE | earliness=INVALIDATED | consensus=SINGLE_SIGNAL | qscore=15 | confidence=LOW | ext=NORMAL | escore=5 | sector=Basic Materials [DATA_QUARANTINE]
- **ADTX** | priority=DATA_QUARANTINE | earliness=INVALIDATED | consensus=SINGLE_SIGNAL | qscore=15 | confidence=LOW | ext=NORMAL | escore=5 | sector=Healthcare [DATA_QUARANTINE]
- **AENT** | priority=DATA_QUARANTINE | earliness=INVALIDATED | consensus=SINGLE_SIGNAL | qscore=15 | confidence=LOW | ext=NORMAL | escore=5 | sector=Communication Services [DATA_QUARANTINE]
- **AARD** | priority=DATA_QUARANTINE | earliness=INVALIDATED | consensus=SINGLE_SIGNAL | qscore=15 | confidence=LOW | ext=NORMAL | escore=5 | sector=Healthcare [DATA_QUARANTINE]
- **ACTU** | priority=DATA_QUARANTINE | earliness=INVALIDATED | consensus=SINGLE_SIGNAL | qscore=15 | confidence=LOW | ext=NORMAL | escore=5 | sector=Healthcare [DATA_QUARANTINE]
- **ADGM** | priority=DATA_QUARANTINE | earliness=INVALIDATED | consensus=SINGLE_SIGNAL | qscore=15 | confidence=LOW | ext=NORMAL | escore=5 | sector=Healthcare [DATA_QUARANTINE]
- **ABVE** | priority=DATA_QUARANTINE | earliness=INVALIDATED | consensus=SINGLE_SIGNAL | qscore=15 | confidence=LOW | ext=NORMAL | escore=5 | sector=Consumer Defensive [DATA_QUARANTINE]
- **AACIW** | priority=DATA_QUARANTINE | earliness=UNKNOWN | consensus=SINGLE_SIGNAL | qscore=10 | confidence=LOW | ext=NORMAL | escore=0 | sector=Financial Services [INSUFFICIENT_HISTORY]
- **ABVEW** | priority=DATA_QUARANTINE | earliness=UNKNOWN | consensus=SINGLE_SIGNAL | qscore=10 | confidence=LOW | ext=NORMAL | escore=0 | sector=Consumer Defensive [INSUFFICIENT_HISTORY]
- **ACONW** | priority=DATA_QUARANTINE | earliness=UNKNOWN | consensus=SINGLE_SIGNAL | qscore=10 | confidence=LOW | ext=NORMAL | escore=0 | sector=Healthcare [INSUFFICIENT_HISTORY]
- **ADSEW** | priority=DATA_QUARANTINE | earliness=UNKNOWN | consensus=SINGLE_SIGNAL | qscore=10 | confidence=LOW | ext=NORMAL | escore=0 | sector=Industrials [INSUFFICIENT_HISTORY]
*... and 1 more (see JSON sidecar)*

## Social / Catalyst Anomalies

- **GSIT** | priority=CONFLICTED_SIGNAL | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=0 | confidence=LOW | ext=PARABOLIC | escore=15 | sector=Technology
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

- **ADIL** | dd=-53.27% | rs63=36.9 | vol_trend=2.489 | score=86 | [price/volume only — no confirmed thesis]
- **AI** | dd=-61.35% | rs63=11.35 | vol_trend=1.609 | score=86 | [price/volume only — no confirmed thesis]
- **ALOY** | dd=-45.67% | rs63=2.18 | vol_trend=1.455 | score=86 | [price/volume only — no confirmed thesis]
- **AACBR** | dd=-55.26% | rs63=-70.39 | vol_trend=2.776 | score=69 | [price/volume only — no confirmed thesis]
- **AACIW** | dd=-73.21% | rs63=-88.35 | vol_trend=1.842 | score=69 | [price/volume only — no confirmed thesis]
- **AAL** | dd=-7.87% | rs63=27.67 | vol_trend=1.351 | score=69 | [price/volume only — no confirmed thesis]
- **AAME** | dd=-41.45% | rs63=-50.88 | vol_trend=1.969 | score=69 | [price/volume only — no confirmed thesis]
- **ABLVW** | dd=-44.74% | rs63=-21.74 | vol_trend=1.281 | score=69 | [price/volume only — no confirmed thesis]
- **ABM** | dd=-2.2% | rs63=4.82 | vol_trend=1.551 | score=69 | [price/volume only — no confirmed thesis]
- **AD** | dd=0.0% | rs63=18.21 | vol_trend=1.809 | score=69 | [price/volume only — no confirmed thesis]

## Extended / Crowded / Avoid

- **AAOI** | priority=EXTENDED_CROWDED | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=49 | confidence=MEDIUM | ext=PARABOLIC | escore=15 | sector=Technology
- **AAL** | priority=EXTENDED_CROWDED | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=43 | confidence=MEDIUM | ext=PARABOLIC | escore=15 | sector=Industrials
- **NUE** | priority=EXTENDED_CROWDED | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=39 | confidence=HIGH | ext=PARABOLIC | escore=15 | sector=Basic Materials
- **ENPH** | priority=EXTENDED_CROWDED | earliness=EXTENDED | consensus=SINGLE_SIGNAL | qscore=30 | confidence=LOW | ext=PARABOLIC | escore=30 | sector=Energy
- **AERT** | priority=EXTENDED_CROWDED | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=28 | confidence=LOW | ext=PARABOLIC | escore=15 | sector=Industrials
- **ADUR** | priority=EXTENDED_CROWDED | earliness=EXTENDED | consensus=SINGLE_SIGNAL | qscore=28 | confidence=LOW | ext=EXTENDED | escore=30 | sector=Industrials
- **CRSR** | priority=EXTENDED_CROWDED | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=24 | confidence=LOW | ext=PARABOLIC | escore=15 | sector=Technology
- **MX** | priority=EXTENDED_CROWDED | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=24 | confidence=LOW | ext=PARABOLIC | escore=15 | sector=Technology
- **APLS** | priority=EXTENDED_CROWDED | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=23 | confidence=LOW | ext=PARABOLIC | escore=15 | sector=Healthcare
- **OUST** | priority=EXTENDED_CROWDED | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=20 | confidence=LOW | ext=PARABOLIC | escore=15 | sector=Technology
*... and 17 more (see JSON sidecar)*

## Forward Tracker Status

**Overall:** n=0 matured | sample_status=TOO_EARLY | verdict=NEED_MORE_DATA

> ⚠ TOO_EARLY: 0 matured observations. Need ≥10 for provisional read, ≥30 for meaningful, ≥100 for robust. No bucket has interpretable evidence yet.

**Benchmarks:** SPY/QQQ ready for 0/299 entries | sector ETF assigned for 241/299

| Bucket | n_total | n_matured | sample_status | verdict |
|--------|---------|-----------|---------------|---------|
| ASYMMETRIC_RECOVERY_WATCH |      38 |         0 | TOO_EARLY     | NEED_MORE_DATA |
| BEATEN_DOWN            |      80 |         0 | TOO_EARLY     | NEED_MORE_DATA |
| CATALYST               |      11 |         0 | TOO_EARLY     | NEED_MORE_DATA |
| EARLY_ACCUMULATION     |      81 |         0 | TOO_EARLY     | NEED_MORE_DATA |
| EXTENDED               |      10 |         0 | TOO_EARLY     | NEED_MORE_DATA |
| RISKY                  |       9 |         0 | TOO_EARLY     | NEED_MORE_DATA |
| SECTOR_LEADER          |      49 |         0 | TOO_EARLY     | NEED_MORE_DATA |
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
*Generated: 2026-06-19T17:02:37.360775+00:00 | DAILY_ALPHA_RADAR_V1*

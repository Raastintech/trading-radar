======================================================================
  RESEARCH_ONLY_MODE — NO AUTO TRADING — HUMAN REVIEW ONLY
  Broker execution, paper-trade routing, and Alpaca are disabled.
  FMP + Tradier (research) remain active.
======================================================================

# Daily Alpha Radar — 2026-07-08

**Version:** DAILY_ALPHA_RADAR_V1 | **Mode:** RESEARCH_ONLY | **Research-Only**

*Candidates: 100 scanned | TOP_RESEARCH: 1 | HIGH_PRIORITY: 4 | WATCHLIST: 18 | DATA_QUARANTINE: 4*

---

## Market Context

**Regime:** SMALL_CAP_LED | **Trend:** UPTREND_MILD | *as of 2026-07-08*

## Data Coverage

**Tickers:** 5620 total | Actionable (HIGH+MEDIUM): 20.0%
  HIGH=756 | MEDIUM=367 | LOW=4438 | INVALID=59

**Options Coverage:** 9% | Overlay: DISABLED
  > ⚠ OPTIONS_DATA_UNAVAILABLE: coverage below 50% threshold. No candidate will be promoted based on options data.

## Scanner Field Coverage

| Field | Coverage |
|-------|----------|
| `above_ma200` populated | 97/100 (97%) |
| `above_ma50` populated | 100/100 (100%) |
| `rs_63d_vs_spy` populated | 100/100 (100%) |
| `sector` populated | 100/100 (100%) |
| `liquidity_ok` populated | 100/100 (100%) |
| Earliness non-UNKNOWN | 97/100 (97%) |

**Quarantine breakdown:**
  - INSUFFICIENT_HISTORY: 3
  - DATA_QUARANTINE: 1

## What Changed Today

**Summary:** 12 new, 15 dropped, 2 score↑, 3 relabeled

  - NEW_ENTRY: **ARWR** 
  - NEW_ENTRY: **AZZ** 
  - NEW_ENTRY: **CBRL** 
  - NEW_ENTRY: **CVLT** 
  - NEW_ENTRY: **GOOGL** 
  - NEW_ENTRY: **HELE** 
  - NEW_ENTRY: **KLIC** 
  - NEW_ENTRY: **LEVI** 
  - NEW_ENTRY: **PSMT** 
  - NEW_ENTRY: **TREX** 
  - NEW_ENTRY: **TRVI** 
  - NEW_ENTRY: **VSAT** 
  - DROPPED: **AAPL** 
  - DROPPED: **BNY** 
  - DROPPED: **DAL** 
  - DROPPED: **DIN** 
  - DROPPED: **FEIM** 
  - DROPPED: **HNGE** 
  - DROPPED: **LC** 
  - DROPPED: **LCID** 

## Top Research Candidates — Quality Adjusted

- **HOOD** | priority=TOP_RESEARCH | earliness=DEVELOPING | consensus=HIGH_PRIORITY_RESEARCH | qscore=100 | confidence=HIGH | ext=STRETCHED | escore=75 | sector=Financial Services
  - *Why appeared:* Rising volume + improving RS or higher lows; not extended
  - *Confirms if:* RS continues rising, volume expands on up-days, reclaims 50d MA
  - *Invalidates if:* Volume dries up, RS reverses, undercuts recent lows
- **ACVA** | priority=HIGH_PRIORITY_RESEARCH | earliness=DEVELOPING | consensus=DOUBLE_CONFIRMATION | qscore=80 | confidence=HIGH | ext=NORMAL | escore=75 | sector=Consumer Cyclical
  - *Why appeared:* Social attention signal (source: social_attention_radar)
  - *Confirms if:* Early attention + price not yet extended + fundamental support
  - *Invalidates if:* Already widely discussed (CROWDED), price fully extended
- **JPM** | priority=HIGH_PRIORITY_RESEARCH | earliness=DEVELOPING | consensus=DOUBLE_CONFIRMATION | qscore=80 | confidence=HIGH | ext=STRETCHED | escore=65 | sector=Financial Services
  - *Why appeared:* Social attention signal (source: social_arb)
  - *Confirms if:* Early attention + price not yet extended + fundamental support
  - *Invalidates if:* Already widely discussed (CROWDED), price fully extended

## Early Accumulation — Clean Only

- **SGRY** | priority=HIGH_PRIORITY_RESEARCH | earliness=DEVELOPING | consensus=DOUBLE_CONFIRMATION | qscore=89 | confidence=HIGH | ext=NORMAL | escore=75 | sector=Healthcare
  - *Why appeared:* Rising volume + improving RS or higher lows; not extended
  - *Confirms if:* RS continues rising, volume expands on up-days, reclaims 50d MA
  - *Invalidates if:* Volume dries up, RS reverses, undercuts recent lows
- **SDGR** | priority=HIGH_PRIORITY_RESEARCH | earliness=DEVELOPING | consensus=DOUBLE_CONFIRMATION | qscore=89 | confidence=HIGH | ext=STRETCHED | escore=75 | sector=Technology
  - *Why appeared:* Rising volume + improving RS or higher lows; not extended
  - *Confirms if:* RS continues rising, volume expands on up-days, reclaims 50d MA
  - *Invalidates if:* Volume dries up, RS reverses, undercuts recent lows

## Reclaim / Reset Watch

- **GOOG** | priority=RESET_WATCH | earliness=RESET_WATCH | consensus=DOUBLE_CONFIRMATION | qscore=80 | confidence=HIGH | ext=STRETCHED | escore=65 | sector=Communication Services
  - *Why appeared:* Social attention signal (source: social_arb)
  - *Confirms if:* Early attention + price not yet extended + fundamental support
  - *Invalidates if:* Already widely discussed (CROWDED), price fully extended
- **CTAS** | priority=RECLAIM_WATCH | earliness=RECLAIM_WATCH | consensus=SINGLE_SIGNAL | qscore=68 | confidence=HIGH | ext=NORMAL | escore=45 | sector=Industrials
  - *Why appeared:* Upcoming earnings (2026-07-15)
  - *Confirms if:* Guidance raised, strong beat, volume expands post-earnings
  - *Invalidates if:* Miss + guide down, volume collapses, extended into print
- **WYY** | priority=RESET_WATCH | earliness=LATE | consensus=DOUBLE_CONFIRMATION | qscore=60 | confidence=HIGH | ext=PARABOLIC | escore=15 | sector=Technology
  - *Downgraded:* extension_high_consensus
  - *Why appeared:* Strong RS vs SPY: 20d=+33.8pp, 63d=+199.5pp | above MA50 + MA200
  - *Confirms if:* RS continues expanding, volume confirms, price holds above recent pivot
  - *Invalidates if:* RS rolls over, volume dries on up-days, price undercuts pivot low
- **PENG** | priority=RESET_WATCH | earliness=LATE | consensus=DOUBLE_CONFIRMATION | qscore=60 | confidence=HIGH | ext=PARABOLIC | escore=15 | sector=Technology
  - *Downgraded:* extension_high_consensus
  - *Why appeared:* Strong RS vs SPY: 20d=-6.2pp, 63d=+213.8pp | above MA50 + MA200
  - *Confirms if:* RS continues expanding, volume confirms, price holds above recent pivot
  - *Invalidates if:* RS rolls over, volume dries on up-days, price undercuts pivot low
- **UMC** | priority=RESET_WATCH | earliness=LATE | consensus=DOUBLE_CONFIRMATION | qscore=60 | confidence=HIGH | ext=PARABOLIC | escore=15 | sector=Technology
  - *Downgraded:* extension_high_consensus
  - *Why appeared:* Strong RS vs SPY: 20d=+19.6pp, 63d=+160.7pp | above MA50 + MA200
  - *Confirms if:* RS continues expanding, volume confirms, price holds above recent pivot
  - *Invalidates if:* RS rolls over, volume dries on up-days, price undercuts pivot low
- **MSFT** | priority=RECLAIM_WATCH | earliness=RECLAIM_WATCH | consensus=SINGLE_SIGNAL | qscore=60 | confidence=HIGH | ext=NORMAL | escore=45 | sector=Technology
  - *Why appeared:* Social attention signal (source: social_arb)
  - *Confirms if:* Early attention + price not yet extended + fundamental support
  - *Invalidates if:* Already widely discussed (CROWDED), price fully extended
- **AMZN** | priority=RESET_WATCH | earliness=RESET_WATCH | consensus=SINGLE_SIGNAL | qscore=59 | confidence=HIGH | ext=NORMAL | escore=65 | sector=Consumer Cyclical
  - *Why appeared:* Social attention signal (source: social_arb)
  - *Confirms if:* Early attention + price not yet extended + fundamental support
  - *Invalidates if:* Already widely discussed (CROWDED), price fully extended
- **NVDA** | priority=RESET_WATCH | earliness=RESET_WATCH | consensus=SINGLE_SIGNAL | qscore=59 | confidence=HIGH | ext=NORMAL | escore=60 | sector=Technology
  - *Why appeared:* Social attention signal (source: social_arb)
  - *Confirms if:* Early attention + price not yet extended + fundamental support
  - *Invalidates if:* Already widely discussed (CROWDED), price fully extended
- **AGYS** | priority=RESET_WATCH | earliness=EXTENDED | consensus=DOUBLE_CONFIRMATION | qscore=59 | confidence=HIGH | ext=EXTENDED | escore=30 | sector=Technology
  - *Downgraded:* extension_high_consensus
  - *Why appeared:* Rising volume + improving RS or higher lows; not extended
  - *Confirms if:* RS continues rising, volume expands on up-days, reclaims 50d MA
  - *Invalidates if:* Volume dries up, RS reverses, undercuts recent lows
- **BLMN** | priority=RESET_WATCH | earliness=EXTENDED | consensus=DOUBLE_CONFIRMATION | qscore=59 | confidence=HIGH | ext=EXTENDED | escore=30 | sector=Consumer Cyclical
  - *Downgraded:* extension_high_consensus
  - *Why appeared:* Rising volume + improving RS or higher lows; not extended
  - *Confirms if:* RS continues rising, volume expands on up-days, reclaims 50d MA
  - *Invalidates if:* Volume dries up, RS reverses, undercuts recent lows

## Research Watchlist — RS Momentum + Multi-Signal

- **JEF** | priority=WATCHLIST_RESEARCH | earliness=DEVELOPING | consensus=SINGLE_SIGNAL | qscore=93 | confidence=HIGH | ext=NORMAL | escore=75 | sector=Financial Services
  - *Downgraded:* only_single_signal
  - *Why appeared:* Rising volume + improving RS or higher lows; not extended
  - *Confirms if:* RS continues rising, volume expands on up-days, reclaims 50d MA
  - *Invalidates if:* Volume dries up, RS reverses, undercuts recent lows
- **NVO** | priority=WATCHLIST_RESEARCH | earliness=DEVELOPING | consensus=SINGLE_SIGNAL | qscore=85 | confidence=HIGH | ext=NORMAL | escore=75 | sector=Healthcare
  - *Downgraded:* only_single_signal
  - *Why appeared:* Outperforming SPY by +11.2pp over 20d; above 50d MA
  - *Confirms if:* RS sustains, sector ETF stays in leadership, volume confirms
  - *Invalidates if:* RS reverses, sector rotates out, loses leading sector membership
- **RDDT** | priority=WATCHLIST_RESEARCH | earliness=DEVELOPING | consensus=SINGLE_SIGNAL | qscore=85 | confidence=HIGH | ext=STRETCHED | escore=75 | sector=Communication Services
  - *Downgraded:* only_single_signal
  - *Why appeared:* Outperforming SPY by +7.8pp over 20d; above 50d MA
  - *Confirms if:* RS sustains, sector ETF stays in leadership, volume confirms
  - *Invalidates if:* RS reverses, sector rotates out, loses leading sector membership
- **TTAN** | priority=WATCHLIST_RESEARCH | earliness=EARLY | consensus=SINGLE_SIGNAL | qscore=79 | confidence=HIGH | ext=NORMAL | escore=95 | sector=Technology
  - *Downgraded:* only_single_signal
  - *Why appeared:* Large drawdown (24%/3m) with stabilization pattern
  - *Confirms if:* Price reclaims 50d MA on volume, RS turns positive, catalytic news
  - *Invalidates if:* New lows, accelerating selling, fundamental deterioration
- **NTNX** | priority=WATCHLIST_RESEARCH | earliness=DEVELOPING | consensus=SINGLE_SIGNAL | qscore=75 | confidence=HIGH | ext=NORMAL | escore=75 | sector=Technology
  - *Downgraded:* only_single_signal
  - *Why appeared:* Large drawdown (28%/3m) with stabilization pattern
  - *Confirms if:* Price reclaims 50d MA on volume, RS turns positive, catalytic news
  - *Invalidates if:* New lows, accelerating selling, fundamental deterioration
- **ATRA** | priority=WATCHLIST_RESEARCH | earliness=DEVELOPING | consensus=SINGLE_SIGNAL | qscore=75 | confidence=HIGH | ext=STRETCHED | escore=75 | sector=Healthcare
  - *Downgraded:* only_single_signal
  - *Why appeared:* Large drawdown (118%/3m) with stabilization pattern
  - *Confirms if:* Price reclaims 50d MA on volume, RS turns positive, catalytic news
  - *Invalidates if:* New lows, accelerating selling, fundamental deterioration
- **CNNE** | priority=WATCHLIST_RESEARCH | earliness=EARLY | consensus=SINGLE_SIGNAL | qscore=72 | confidence=HIGH | ext=NORMAL | escore=95 | sector=Consumer Cyclical
  - *Downgraded:* only_single_signal
  - *Why appeared:* Large drawdown (20%/3m) with stabilization pattern
  - *Confirms if:* Price reclaims 50d MA on volume, RS turns positive, catalytic news
  - *Invalidates if:* New lows, accelerating selling, fundamental deterioration
- **NMAX** | priority=WATCHLIST_RESEARCH | earliness=DEVELOPING | consensus=SINGLE_SIGNAL | qscore=70 | confidence=HIGH | ext=STRETCHED | escore=75 | sector=Communication Services
  - *Downgraded:* only_single_signal
  - *Why appeared:* Large drawdown (59%/3m) with stabilization pattern
  - *Confirms if:* Price reclaims 50d MA on volume, RS turns positive, catalytic news
  - *Invalidates if:* New lows, accelerating selling, fundamental deterioration
- **XRX** | priority=WATCHLIST_RESEARCH | earliness=DEVELOPING | consensus=SINGLE_SIGNAL | qscore=70 | confidence=HIGH | ext=STRETCHED | escore=75 | sector=Industrials
  - *Downgraded:* only_single_signal
  - *Why appeared:* Large drawdown (129%/3m) with stabilization pattern
  - *Confirms if:* Price reclaims 50d MA on volume, RS turns positive, catalytic news
  - *Invalidates if:* New lows, accelerating selling, fundamental deterioration
- **KULR** | priority=WATCHLIST_RESEARCH | earliness=DEVELOPING | consensus=SINGLE_SIGNAL | qscore=70 | confidence=HIGH | ext=STRETCHED | escore=75 | sector=Industrials
  - *Downgraded:* only_single_signal
  - *Why appeared:* Large drawdown (86%/3m) with stabilization pattern
  - *Confirms if:* Price reclaims 50d MA on volume, RS turns positive, catalytic news
  - *Invalidates if:* New lows, accelerating selling, fundamental deterioration
- **CBZ** | priority=WATCHLIST_RESEARCH | earliness=EARLY | consensus=SINGLE_SIGNAL | qscore=68 | confidence=HIGH | ext=NORMAL | escore=95 | sector=Industrials
  - *Downgraded:* only_single_signal
  - *Why appeared:* Large drawdown (28%/3m) with stabilization pattern
  - *Confirms if:* Price reclaims 50d MA on volume, RS turns positive, catalytic news
  - *Invalidates if:* New lows, accelerating selling, fundamental deterioration
- **BAC** | priority=WATCHLIST_RESEARCH | earliness=DEVELOPING | consensus=SINGLE_SIGNAL | qscore=68 | confidence=HIGH | ext=STRETCHED | escore=65 | sector=Financial Services
  - *Downgraded:* only_single_signal
  - *Why appeared:* Upcoming earnings (2026-07-14)
  - *Confirms if:* Guidance raised, strong beat, volume expands post-earnings
  - *Invalidates if:* Miss + guide down, volume collapses, extended into print
- **WFC** | priority=WATCHLIST_RESEARCH | earliness=DEVELOPING | consensus=SINGLE_SIGNAL | qscore=68 | confidence=HIGH | ext=NORMAL | escore=65 | sector=Financial Services
  - *Downgraded:* only_single_signal
  - *Why appeared:* Upcoming earnings (2026-07-14)
  - *Confirms if:* Guidance raised, strong beat, volume expands post-earnings
  - *Invalidates if:* Miss + guide down, volume collapses, extended into print
- **INDP** | priority=WATCHLIST_RESEARCH | earliness=DEVELOPING | consensus=SINGLE_SIGNAL | qscore=65 | confidence=HIGH | ext=STRETCHED | escore=75 | sector=Healthcare
  - *Downgraded:* only_single_signal
  - *Why appeared:* Large drawdown (78%/3m) with stabilization pattern
  - *Confirms if:* Price reclaims 50d MA on volume, RS turns positive, catalytic news
  - *Invalidates if:* New lows, accelerating selling, fundamental deterioration
- **ABBV** | priority=WATCHLIST_RESEARCH | earliness=DEVELOPING | consensus=SINGLE_SIGNAL | qscore=65 | confidence=HIGH | ext=STRETCHED | escore=65 | sector=Healthcare
  - *Downgraded:* only_single_signal
  - *Why appeared:* Speculative growth theme + price momentum; requires manual research
  - *Confirms if:* Revenue growth accelerates, expanding gross margin, theme tailwind
  - *Invalidates if:* Revenue decelerates, balance sheet stress, theme fades
*... and 3 more (see JSON sidecar)*

## Conflicted Signals

- **LEVI** | priority=CONFLICTED_SIGNAL | earliness=DEVELOPING | consensus=SINGLE_SIGNAL | qscore=40 | confidence=HIGH | ext=STRETCHED | escore=75 | sector=Consumer Cyclical
  - *Catalyst sanity:* NEEDS_MANUAL_SOURCE_CHECK (no_imminent_catalyst_event)
  - **CONFLICTS:** catalyst_not_validated
  - *Downgraded:* catalyst_not_validated
  - *Why appeared:* Upcoming earnings (2026-07-08)
  - *Confirms if:* Guidance raised, strong beat, volume expands post-earnings
  - *Invalidates if:* Miss + guide down, volume collapses, extended into print
- **TFIN** | priority=CONFLICTED_SIGNAL | earliness=EXTENDED | consensus=SINGLE_SIGNAL | qscore=28 | confidence=HIGH | ext=PARABOLIC | escore=30 | sector=Financial Services
  - **CONFLICTS:** catalyst_not_validated
  - *Downgraded:* catalyst_not_validated
  - *Why appeared:* Upcoming earnings (2026-07-15); analyst upgrade
  - *Confirms if:* Guidance raised, strong beat, volume expands post-earnings
  - *Invalidates if:* Miss + guide down, volume collapses, extended into print
- **AEHR** | priority=CONFLICTED_SIGNAL | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=18 | confidence=HIGH | ext=PARABOLIC | escore=15 | sector=Technology
  - **CONFLICTS:** catalyst_not_validated
  - *Downgraded:* catalyst_not_validated
  - *Why appeared:* Upcoming earnings (2026-07-14); analyst upgrade
  - *Confirms if:* Guidance raised, strong beat, volume expands post-earnings
  - *Invalidates if:* Miss + guide down, volume collapses, extended into print
- **C** | priority=CONFLICTED_SIGNAL | earliness=EXTENDED | consensus=SINGLE_SIGNAL | qscore=18 | confidence=HIGH | ext=PARABOLIC | escore=30 | sector=Financial Services
  - **CONFLICTS:** catalyst_not_validated
  - *Downgraded:* catalyst_not_validated
  - *Why appeared:* Upcoming earnings (2026-07-14)
  - *Confirms if:* Guidance raised, strong beat, volume expands post-earnings
  - *Invalidates if:* Miss + guide down, volume collapses, extended into print
- **GS** | priority=CONFLICTED_SIGNAL | earliness=EXTENDED | consensus=SINGLE_SIGNAL | qscore=18 | confidence=HIGH | ext=EXTENDED | escore=30 | sector=Financial Services
  - **CONFLICTS:** catalyst_not_validated
  - *Downgraded:* catalyst_not_validated
  - *Why appeared:* Upcoming earnings (2026-07-14)
  - *Confirms if:* Guidance raised, strong beat, volume expands post-earnings
  - *Invalidates if:* Miss + guide down, volume collapses, extended into print
- **AIR** | priority=CONFLICTED_SIGNAL | earliness=EXTENDED | consensus=SINGLE_SIGNAL | qscore=18 | confidence=HIGH | ext=PARABOLIC | escore=30 | sector=Industrials
  - **CONFLICTS:** catalyst_not_validated
  - *Downgraded:* catalyst_not_validated
  - *Why appeared:* Upcoming earnings (2026-07-15)
  - *Confirms if:* Guidance raised, strong beat, volume expands post-earnings
  - *Invalidates if:* Miss + guide down, volume collapses, extended into print
- **PNC** | priority=CONFLICTED_SIGNAL | earliness=EXTENDED | consensus=SINGLE_SIGNAL | qscore=18 | confidence=HIGH | ext=EXTENDED | escore=30 | sector=Financial Services
  - **CONFLICTS:** catalyst_not_validated
  - *Downgraded:* catalyst_not_validated
  - *Why appeared:* Upcoming earnings (2026-07-15)
  - *Confirms if:* Guidance raised, strong beat, volume expands post-earnings
  - *Invalidates if:* Miss + guide down, volume collapses, extended into print
- **AZZ** | priority=CONFLICTED_SIGNAL | earliness=EXTENDED | consensus=SINGLE_SIGNAL | qscore=10 | confidence=HIGH | ext=PARABOLIC | escore=30 | sector=Industrials
  - *Catalyst sanity:* NEEDS_MANUAL_SOURCE_CHECK (tape_extended, no_imminent_catalyst_event)
  - **CONFLICTS:** catalyst_not_validated
  - *Downgraded:* catalyst_not_validated
  - *Why appeared:* Upcoming earnings (2026-07-08)
  - *Confirms if:* Guidance raised, strong beat, volume expands post-earnings
  - *Invalidates if:* Miss + guide down, volume collapses, extended into print
- **PSMT** | priority=CONFLICTED_SIGNAL | earliness=EXTENDED | consensus=SINGLE_SIGNAL | qscore=10 | confidence=HIGH | ext=PARABOLIC | escore=30 | sector=Consumer Defensive
  - *Catalyst sanity:* NEEDS_MANUAL_SOURCE_CHECK (tape_extended, no_imminent_catalyst_event)
  - **CONFLICTS:** catalyst_not_validated
  - *Downgraded:* catalyst_not_validated
  - *Why appeared:* Upcoming earnings (2026-07-08)
  - *Confirms if:* Guidance raised, strong beat, volume expands post-earnings
  - *Invalidates if:* Miss + guide down, volume collapses, extended into print
- **PKE** | priority=CONFLICTED_SIGNAL | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=8 | confidence=HIGH | ext=PARABOLIC | escore=15 | sector=Industrials
  - *Catalyst sanity:* HYPE_CROWDED (tape_extended, extended_into_earnings)
  - **CONFLICTS:** risky_with_catalyst, catalyst_not_validated
  - *Downgraded:* risky_with_catalyst, catalyst_not_validated
  - *Why appeared:* Upcoming earnings (2026-07-14); analyst upgrade
  - *Confirms if:* Guidance raised, strong beat, volume expands post-earnings
  - *Invalidates if:* Miss + guide down, volume collapses, extended into print
*... and 8 more (see JSON sidecar)*

## Data Quarantine

- **SHAZ** | priority=DATA_QUARANTINE | earliness=UNKNOWN | consensus=SINGLE_SIGNAL | qscore=70 | confidence=HIGH | ext=NORMAL | escore=0 | sector=Technology [INSUFFICIENT_HISTORY]
- **OFRM** | priority=DATA_QUARANTINE | earliness=UNKNOWN | consensus=DOUBLE_CONFIRMATION | qscore=58 | confidence=HIGH | ext=NORMAL | escore=0 | sector=Consumer Defensive [INSUFFICIENT_HISTORY]
- **FLY** | priority=DATA_QUARANTINE | earliness=INVALIDATED | consensus=SINGLE_SIGNAL | qscore=53 | confidence=HIGH | ext=NORMAL | escore=5 | sector=Industrials [DATA_QUARANTINE]
- **NAVN** | priority=DATA_QUARANTINE | earliness=UNKNOWN | consensus=SINGLE_SIGNAL | qscore=50 | confidence=HIGH | ext=NORMAL | escore=0 | sector=Technology [INSUFFICIENT_HISTORY]

## Social / Catalyst Anomalies

- **KLIC** | priority=CONFLICTED_SIGNAL | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=1 | confidence=HIGH | ext=PARABOLIC | escore=15 | sector=Technology
  - *Catalyst sanity:* NEEDS_MANUAL_SOURCE_CHECK (tape_extended)
  - **CONFLICTS:** catalyst_not_validated
  - *Downgraded:* catalyst_not_validated
  - *Why appeared:* Social attention signal (source: social_attention_radar)
  - *Confirms if:* Early attention + price not yet extended + fundamental support
  - *Invalidates if:* Already widely discussed (CROWDED), price fully extended
- **GOOG** | priority=RESET_WATCH | earliness=RESET_WATCH | consensus=DOUBLE_CONFIRMATION | qscore=80 | confidence=HIGH | ext=STRETCHED | escore=65 | sector=Communication Services
  - *Why appeared:* Social attention signal (source: social_arb)
  - *Confirms if:* Early attention + price not yet extended + fundamental support
  - *Invalidates if:* Already widely discussed (CROWDED), price fully extended
- **GOOGL** | priority=EXTENDED_CROWDED | earliness=EXTENDED | consensus=SINGLE_SIGNAL | qscore=45 | confidence=HIGH | ext=EXTENDED | escore=30 | sector=Communication Services
  - *Catalyst sanity:* NEEDS_MANUAL_SOURCE_CHECK (tape_extended)
  - *Downgraded:* too_extended
  - *Why appeared:* Social attention signal (source: social_arb)
  - *Confirms if:* Early attention + price not yet extended + fundamental support
  - *Invalidates if:* Already widely discussed (CROWDED), price fully extended
- **MSFT** | priority=RECLAIM_WATCH | earliness=RECLAIM_WATCH | consensus=SINGLE_SIGNAL | qscore=60 | confidence=HIGH | ext=NORMAL | escore=45 | sector=Technology
  - *Why appeared:* Social attention signal (source: social_arb)
  - *Confirms if:* Early attention + price not yet extended + fundamental support
  - *Invalidates if:* Already widely discussed (CROWDED), price fully extended
- **AMZN** | priority=RESET_WATCH | earliness=RESET_WATCH | consensus=SINGLE_SIGNAL | qscore=59 | confidence=HIGH | ext=NORMAL | escore=65 | sector=Consumer Cyclical
  - *Why appeared:* Social attention signal (source: social_arb)
  - *Confirms if:* Early attention + price not yet extended + fundamental support
  - *Invalidates if:* Already widely discussed (CROWDED), price fully extended
- **NVDA** | priority=RESET_WATCH | earliness=RESET_WATCH | consensus=SINGLE_SIGNAL | qscore=59 | confidence=HIGH | ext=NORMAL | escore=60 | sector=Technology
  - *Why appeared:* Social attention signal (source: social_arb)
  - *Confirms if:* Early attention + price not yet extended + fundamental support
  - *Invalidates if:* Already widely discussed (CROWDED), price fully extended
- **ACVA** | priority=HIGH_PRIORITY_RESEARCH | earliness=DEVELOPING | consensus=DOUBLE_CONFIRMATION | qscore=80 | confidence=HIGH | ext=NORMAL | escore=75 | sector=Consumer Cyclical
  - *Why appeared:* Social attention signal (source: social_attention_radar)
  - *Confirms if:* Early attention + price not yet extended + fundamental support
  - *Invalidates if:* Already widely discussed (CROWDED), price fully extended
- **TRVI** | priority=CONFLICTED_SIGNAL | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=0 | confidence=HIGH | ext=PARABOLIC | escore=15 | sector=Healthcare
  - *Catalyst sanity:* NEEDS_MANUAL_SOURCE_CHECK (tape_extended)
  - **CONFLICTS:** catalyst_not_validated
  - *Downgraded:* catalyst_not_validated
  - *Why appeared:* Social attention signal (source: social_attention_radar)
  - *Confirms if:* Early attention + price not yet extended + fundamental support
  - *Invalidates if:* Already widely discussed (CROWDED), price fully extended
- **VSAT** | priority=CONFLICTED_SIGNAL | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=0 | confidence=HIGH | ext=PARABOLIC | escore=15 | sector=Technology
  - *Catalyst sanity:* NEEDS_MANUAL_SOURCE_CHECK (tape_extended)
  - **CONFLICTS:** catalyst_not_validated
  - *Downgraded:* catalyst_not_validated
  - *Why appeared:* Social attention signal (source: social_attention_radar)
  - *Confirms if:* Early attention + price not yet extended + fundamental support
  - *Invalidates if:* Already widely discussed (CROWDED), price fully extended
- **ARWR** | priority=CONFLICTED_SIGNAL | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=0 | confidence=HIGH | ext=PARABOLIC | escore=15 | sector=Healthcare
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

- **ADIL** | dd=-53.27% | rs63=38.62 | vol_trend=2.489 | score=86 | [price/volume only — no confirmed thesis]
- **AACBR** | dd=-55.26% | rs63=-68.68 | vol_trend=2.776 | score=69 | [price/volume only — no confirmed thesis]
- **AACIW** | dd=-73.21% | rs63=-86.64 | vol_trend=1.842 | score=69 | [price/volume only — no confirmed thesis]
- **AAME** | dd=-41.45% | rs63=-49.16 | vol_trend=1.969 | score=69 | [price/volume only — no confirmed thesis]
- **AAOI** | dd=-44.71% | rs63=1.38 | vol_trend=0.909 | score=69 | [price/volume only — no confirmed thesis]
- **AAPL** | dd=-0.81% | rs63=7.36 | vol_trend=1.321 | score=69 | [price/volume only — no confirmed thesis]
- **ABBV** | dd=-2.42% | rs63=9.83 | vol_trend=1.602 | score=69 | [price/volume only — no confirmed thesis]
- **ABLVW** | dd=-44.74% | rs63=-20.02 | vol_trend=1.281 | score=69 | [price/volume only — no confirmed thesis]
- **ACHC** | dd=-2.57% | rs63=11.39 | vol_trend=1.388 | score=69 | [price/volume only — no confirmed thesis]
- **ADCT** | dd=-78.28% | rs63=-87.76 | vol_trend=2.048 | score=69 | [price/volume only — no confirmed thesis]

## Extended / Crowded / Avoid

- **AGL** | priority=EXTENDED_CROWDED | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=60 | confidence=HIGH | ext=PARABOLIC | escore=15 | sector=Healthcare
- **RXT** | priority=EXTENDED_CROWDED | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=60 | confidence=HIGH | ext=PARABOLIC | escore=15 | sector=Technology
- **CUE** | priority=EXTENDED_CROWDED | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=60 | confidence=HIGH | ext=PARABOLIC | escore=15 | sector=Healthcare
- **MXL** | priority=EXTENDED_CROWDED | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=60 | confidence=HIGH | ext=PARABOLIC | escore=15 | sector=Technology
- **FCEL** | priority=EXTENDED_CROWDED | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=60 | confidence=HIGH | ext=PARABOLIC | escore=15 | sector=Industrials
- **BLZE** | priority=EXTENDED_CROWDED | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=60 | confidence=HIGH | ext=PARABOLIC | escore=15 | sector=Technology
- **EVC** | priority=EXTENDED_CROWDED | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=60 | confidence=HIGH | ext=PARABOLIC | escore=15 | sector=Communication Services
- **ABSI** | priority=EXTENDED_CROWDED | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=60 | confidence=HIGH | ext=PARABOLIC | escore=15 | sector=Healthcare
- **APPS** | priority=EXTENDED_CROWDED | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=60 | confidence=HIGH | ext=PARABOLIC | escore=15 | sector=Technology
- **ALAB** | priority=EXTENDED_CROWDED | earliness=LATE | consensus=SINGLE_SIGNAL | qscore=60 | confidence=HIGH | ext=PARABOLIC | escore=15 | sector=Technology
*... and 35 more (see JSON sidecar)*

## Forward Tracker Status

**Overall:** n=214 matured | sample_status=ROBUST | verdict=MIXED

**Benchmarks:** SPY/QQQ ready for 214/1668 entries | sector ETF assigned for 1428/1668

| Bucket | n_total | n_matured | sample_status | verdict | avg_vs_SPY |
|--------|---------|-----------|---------------|---------|------------|
| ASYMMETRIC_RECOVERY_WATCH |     213 |        39 | MEANINGFUL    | NO_FORWARD_EDGE        |     -4.42% |
| BEATEN_DOWN            |     278 |        23 | PROVISIONAL   | EARLY_SIGNAL           |     +6.31% |
| CATALYST               |     250 |         8 | TOO_EARLY     | NEED_MORE_DATA         |        n/a |
| EARLY_ACCUMULATION     |     322 |        63 | MEANINGFUL    | NO_FORWARD_EDGE        |     -2.87% |
| EXTENDED               |      53 |         9 | TOO_EARLY     | NEED_MORE_DATA         |        n/a |
| NO_SOCIAL_DATA         |      24 |         4 | TOO_EARLY     | NEED_MORE_DATA         |        n/a |
| RISKY                  |      50 |         5 | TOO_EARLY     | NEED_MORE_DATA         |        n/a |
| RS_MOMENTUM_LEADER     |     185 |         0 | TOO_EARLY     | NEED_MORE_DATA         |        n/a |
| SECTOR_LEADER          |     124 |        41 | MEANINGFUL    | NO_FORWARD_EDGE        |     -2.63% |
| SOCIAL_ARB             |      16 |         0 | TOO_EARLY     | NEED_MORE_DATA         |        n/a |
| SPECULATIVE_10X        |      21 |        21 | PROVISIONAL   | MIXED                  |     +2.56% |
| WATCH                  |     132 |         1 | TOO_EARLY     | NEED_MORE_DATA         |        n/a |

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
*Generated: 2026-07-08T00:46:44.938127+00:00 | DAILY_ALPHA_RADAR_V1*

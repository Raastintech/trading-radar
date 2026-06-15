# Filter Replacement Counterfactual Results (Phase 1H.4)

Generated: 2026-06-12T16:44:47.637454+00:00

RESEARCH_ONLY: no broker orders, no paper signals, no trade proposals, no registry/governance/execution/live-capital changes.

Signal window: `2024-01-02` to `2026-06-11`. Cost model: base (10bps slippage + 5bps spread). Replacement rules are fixed a priori (not fitted), so the train/validation/test split is a decay diagnostic; the test block is the binding evidence.

## Top line: **NO_FILTER_REPLACEMENT_READY_FOR_PAPER_SHADOW**

| Spec | Variant | Mode | Verdict | dTrades | dExpectancy | dRelSPY | dWinRate | dMaxDD(real) | Changed trades |
|---|---|---|---|---:|---:|---:|---:|---:|---:|
| SNIPER_ATR_TO_RS_ACCELERATION | PROD_SNIPER_CURRENT | SOFTEN | REPLACEMENT_REJECT | +245 | -0.99% | -0.84% | -10.28% | -3.03% | 245 |
| SNIPER_ATR_TO_VOL_EXPANSION_1_8 | PROD_SNIPER_CURRENT | SOFTEN | REPLACEMENT_REJECT | +159 | -1.18% | -1.12% | -11.48% | -3.18% | 159 |
| SNIPER_ATR_TO_BREAKOUT_CLOSE_STRENGTH | PROD_SNIPER_CURRENT | SOFTEN | REPLACEMENT_REJECT | +224 | -1.11% | -0.94% | -11.55% | -3.51% | 224 |
| SNIPER_BREAKOUT_TO_NEAR_HIGH_RECLAIM | PROD_SNIPER_CURRENT | SOFTEN | REPLACEMENT_IMPROVES_FLOW_ONLY | +45 | -0.02% | -0.40% | -0.51% | -0.89% | 45 |
| VOY_MA200_FLOOR_TO_MA50_RECLAIM | PROD_VOYAGER_CURRENT | SOFTEN | REPLACEMENT_NEED_MORE_DATA | +0 | +0.00% | +0.00% | +0.00% | +0.00% | 0 |
| VOY_MA200_FLOOR_DECLINING_ONLY | PROD_VOYAGER_CURRENT | SOFTEN | REPLACEMENT_NEED_MORE_DATA | +2 | +0.00% | +0.00% | +0.07% | +0.00% | 4 |
| RSMOM_EXT_ALLOW_POWER_LEADER | RECALL_SHADOW_RS_MOMENTUM | SOFTEN | REPLACEMENT_REJECT | +0 | -0.02% | -0.00% | -0.32% | +0.20% | 205 |
| RSMOM_WEAK_RS_CORRECTION_TURN | RECALL_SHADOW_RS_MOMENTUM | SOFTEN | REPLACEMENT_NEED_MORE_DATA | +5 | -0.01% | -0.01% | -0.05% | +0.00% | 5 |
| PWR_PARABOLIC_BLOCK_ONLY_CLIMAX | POWER_TREND_EXTENSION | SOFTEN | REPLACEMENT_REJECT | +124 | +0.00% | +0.05% | +0.06% | -3.34% | 165 |
| RSMOM_RISK_REGIME_PROTECTION | RECALL_SHADOW_RS_MOMENTUM | TIGHTEN | REPLACEMENT_REJECT | -509 | +0.13% | +0.16% | +0.78% | +1.51% | 509 |
| PULLBACK_RISK_REGIME_PROTECTION | RECALL_SHADOW_PULLBACK | TIGHTEN | REPLACEMENT_IMPROVES_FLOW_ONLY | -378 | -0.02% | -0.08% | -0.22% | +1.35% | 378 |
| CLR_REQUIRE_REAL_VOLUME_CONFIRM | CORRECTION_LEADER_RECLAIM | TIGHTEN | REPLACEMENT_NEED_MORE_DATA | -11 | +0.03% | +0.02% | +0.26% | +0.00% | 11 |

## Per-Replacement Detail

### SNIPER_ATR_TO_RS_ACCELERATION

- Old rule: reject unless ATR(5)/ATR(15) < 0.85
- New rule: where the ATR gate alone fired: admit if rs10_spy > +2% and r5 > 0 (RS acceleration)
- Rationale: 1H showed SNIPER is starved; miner tests whether ATR contraction blocks working breakouts.
- Verdict: **REPLACEMENT_REJECT** — replacement worsens realistic maxDD (-1.37% -> -4.40%)
- Exact: 286 trades (baseline 41), expectancy +0.59% (baseline +1.58%)
- Walk-forward test: n=66, expectancy +1.87% (baseline +1.85%)
- Realistic: return +12.74% (baseline +5.12%), maxDD -4.40%, Sharpe 0.8706
- Same-exposure SPY/QQQ return: +10.45% / +13.32%
- Changed-trade stats: {'mode': 'SOFTEN', 'changed_candidates': 257, 'changed_trades_simulated': 245, 'mean_net_return': 0.00422, 'win_rate': 0.4898, 'winners': 120, 'losers': 125}

### SNIPER_ATR_TO_VOL_EXPANSION_1_8

- Old rule: reject unless ATR(5)/ATR(15) < 0.85
- New rule: where the ATR gate alone fired: admit if breakout volume ratio >= 1.8x
- Rationale: Replaces volatility-contraction evidence with stronger volume evidence.
- Verdict: **REPLACEMENT_REJECT** — replacement worsens realistic maxDD (-1.37% -> -4.54%)
- Exact: 200 trades (baseline 41), expectancy +0.40% (baseline +1.58%)
- Walk-forward test: n=38, expectancy +1.32% (baseline +1.85%)
- Realistic: return +8.82% (baseline +5.12%), maxDD -4.54%, Sharpe 0.7231
- Same-exposure SPY/QQQ return: +9.33% / +12.15%
- Changed-trade stats: {'mode': 'SOFTEN', 'changed_candidates': 166, 'changed_trades_simulated': 159, 'mean_net_return': 0.000956, 'win_rate': 0.4654, 'winners': 74, 'losers': 85}

### SNIPER_ATR_TO_BREAKOUT_CLOSE_STRENGTH

- Old rule: reject unless ATR(5)/ATR(15) < 0.85
- New rule: where the ATR gate alone fired: admit if close within 2% of 20d high and above EMA20
- Rationale: Replaces contraction with breakout close strength.
- Verdict: **REPLACEMENT_REJECT** — replacement worsens realistic maxDD (-1.37% -> -4.88%)
- Exact: 265 trades (baseline 41), expectancy +0.47% (baseline +1.58%)
- Walk-forward test: n=56, expectancy +1.42% (baseline +1.85%)
- Realistic: return +8.01% (baseline +5.12%), maxDD -4.88%, Sharpe 0.6317
- Same-exposure SPY/QQQ return: +9.23% / +11.56%
- Changed-trade stats: {'mode': 'SOFTEN', 'changed_candidates': 240, 'changed_trades_simulated': 224, 'mean_net_return': 0.002665, 'win_rate': 0.4732, 'winners': 106, 'losers': 118}

### SNIPER_BREAKOUT_TO_NEAR_HIGH_RECLAIM

- Old rule: reject unless a fresh 20d-high breakout happened today
- New rule: where the breakout gate alone fired: admit tight near-high consolidation (dd20 >= -5%, above EMA20, rs10 > 0)
- Rationale: Tests whether near-breakout reclaims work as well as fresh breakouts.
- Verdict: **REPLACEMENT_IMPROVES_FLOW_ONLY** — flow changes by +45 trades but expectancy delta is only -0.02%; flow without edge is not promotable
- Exact: 86 trades (baseline 41), expectancy +1.55% (baseline +1.58%)
- Walk-forward test: n=18, expectancy +1.94% (baseline +1.85%)
- Realistic: return +9.11% (baseline +5.12%), maxDD -2.25%, Sharpe 1.5074
- Same-exposure SPY/QQQ return: +4.41% / +6.51%
- Changed-trade stats: {'mode': 'SOFTEN', 'changed_candidates': 45, 'changed_trades_simulated': 45, 'mean_net_return': 0.015334, 'win_rate': 0.6, 'winners': 27, 'losers': 18}

### VOY_MA200_FLOOR_TO_MA50_RECLAIM

- Old rule: reject if price < MA200 * 0.92
- New rule: where the MA200 floor alone fired: admit if price reclaimed MA50 and rs50_spy > +3%
- Rationale: Tests early-recovery admission below the MA200 floor.
- Verdict: **REPLACEMENT_NEED_MORE_DATA** — only 0 changed trades (<15); cannot judge the replacement
- Exact: 1471 trades (baseline 1471), expectancy +0.52% (baseline +0.52%)
- Walk-forward test: n=349, expectancy +1.85% (baseline +1.85%)
- Realistic: return +14.55% (baseline +14.55%), maxDD -9.52%, Sharpe 0.7701
- Same-exposure SPY/QQQ return: +20.10% / +22.56%
- Changed-trade stats: {'mode': 'SOFTEN', 'changed_candidates': 0, 'changed_trades_simulated': 0, 'mean_net_return': None, 'win_rate': None, 'winners': 0, 'losers': 0}

### VOY_MA200_FLOOR_DECLINING_ONLY

- Old rule: reject if price < MA200 * 0.92
- New rule: where the MA200 floor alone fired: admit unless MA50 is also falling and rs50 <= 0
- Rationale: Rejects only structurally broken names instead of every deep base.
- Verdict: **REPLACEMENT_NEED_MORE_DATA** — only 4 changed trades (<15); cannot judge the replacement
- Exact: 1473 trades (baseline 1471), expectancy +0.53% (baseline +0.52%)
- Walk-forward test: n=349, expectancy +1.85% (baseline +1.85%)
- Realistic: return +14.55% (baseline +14.55%), maxDD -9.52%, Sharpe 0.7701
- Same-exposure SPY/QQQ return: +20.10% / +22.56%
- Changed-trade stats: {'mode': 'SOFTEN', 'changed_candidates': 6, 'changed_trades_simulated': 4, 'mean_net_return': 0.041617, 'win_rate': 1.0, 'winners': 4, 'losers': 0}

### RSMOM_EXT_ALLOW_POWER_LEADER

- Old rule: reject if extension above EMA20 > 25%
- New rule: where the extension cap alone fired: admit power-theme RS leaders with controlled pullback (dd20 >= -5%)
- Rationale: 1G.14 showed power-theme extension is rewarded; this tests it inside the lab funnel.
- Verdict: **REPLACEMENT_REJECT** — modified realistic maxDD -17.36% breaches -15% risk limit
- Exact: 2800 trades (baseline 2800), expectancy +0.35% (baseline +0.37%)
- Walk-forward test: n=723, expectancy -0.02% (baseline +0.18%)
- Realistic: return +4.36% (baseline +0.65%), maxDD -17.36%, Sharpe 0.1899
- Same-exposure SPY/QQQ return: +23.55% / +31.50%
- Changed-trade stats: {'mode': 'SOFTEN', 'changed_candidates': 248, 'changed_trades_simulated': 205, 'mean_net_return': 0.005493, 'win_rate': 0.4293, 'winners': 88, 'losers': 117}

### RSMOM_WEAK_RS_CORRECTION_TURN

- Old rule: reject if max(rs20_spy, sector_rs20) < 8%
- New rule: where the RS floor alone fired: admit early RS turns (rs >= 2%) during correction-family regimes if above EMA20
- Rationale: Tests whether the RS floor is too slow coming out of corrections.
- Verdict: **REPLACEMENT_NEED_MORE_DATA** — only 5 changed trades (<15); cannot judge the replacement
- Exact: 2805 trades (baseline 2800), expectancy +0.37% (baseline +0.37%)
- Walk-forward test: n=723, expectancy +0.18% (baseline +0.18%)
- Realistic: return +0.65% (baseline +0.65%), maxDD -17.56%, Sharpe 0.0979
- Same-exposure SPY/QQQ return: +21.87% / +28.44%
- Changed-trade stats: {'mode': 'SOFTEN', 'changed_candidates': 188, 'changed_trades_simulated': 5, 'mean_net_return': -0.031067, 'win_rate': 0.2, 'winners': 1, 'losers': 4}

### PWR_PARABOLIC_BLOCK_ONLY_CLIMAX

- Old rule: reject if r5 > 30% or r10 > 55%
- New rule: where the parabolic gate alone fired: admit unless volume climax (vol_expansion > 2x) or bearish reversal (r5 < 0)
- Rationale: Blocks only climax behavior instead of all fast moves.
- Verdict: **REPLACEMENT_REJECT** — replacement worsens realistic maxDD (-5.22% -> -8.56%)
- Exact: 917 trades (baseline 793), expectancy +0.95% (baseline +0.95%)
- Walk-forward test: n=412, expectancy +0.88% (baseline +0.96%)
- Realistic: return +26.78% (baseline +32.19%), maxDD -8.56%, Sharpe 0.9551
- Same-exposure SPY/QQQ return: +9.65% / +13.26%
- Changed-trade stats: {'mode': 'SOFTEN', 'changed_candidates': 205, 'changed_trades_simulated': 165, 'mean_net_return': 0.00627, 'win_rate': 0.4545, 'winners': 75, 'losers': 90}

### RSMOM_RISK_REGIME_PROTECTION

- Old rule: no market-regime protection
- New rule: block accepted signals when the market regime is RISK_OFF / correction / high-volatility
- Rationale: Accepted-loser mining flags regime risk; tests a protective gate.
- Verdict: **REPLACEMENT_REJECT** — modified realistic maxDD -16.05% breaches -15% risk limit
- Exact: 2291 trades (baseline 2800), expectancy +0.50% (baseline +0.37%)
- Walk-forward test: n=498, expectancy +0.32% (baseline +0.18%)
- Realistic: return +7.35% (baseline +0.65%), maxDD -16.05%, Sharpe 0.268
- Same-exposure SPY/QQQ return: +19.00% / +24.44%
- Changed-trade stats: {'mode': 'TIGHTEN', 'changed_candidates': 1293, 'changed_trades_simulated': 509, 'mean_net_return': -0.00212, 'win_rate': 0.4165, 'winners': 212, 'losers': 297}

### PULLBACK_RISK_REGIME_PROTECTION

- Old rule: no market-regime protection
- New rule: block accepted signals when the market regime is RISK_OFF / correction / high-volatility
- Rationale: RECALL_SHADOW_PULLBACK carries -14% realistic maxDD; tests regime protection.
- Verdict: **REPLACEMENT_IMPROVES_FLOW_ONLY** — flow changes by -378 trades but expectancy delta is only -0.02%; flow without edge is not promotable
- Exact: 1536 trades (baseline 1914), expectancy +0.57% (baseline +0.59%)
- Walk-forward test: n=289, expectancy -0.24% (baseline +0.24%)
- Realistic: return +21.49% (baseline +28.13%), maxDD -12.99%, Sharpe 0.8918
- Same-exposure SPY/QQQ return: +20.18% / +24.96%
- Changed-trade stats: {'mode': 'TIGHTEN', 'changed_candidates': 484, 'changed_trades_simulated': 378, 'mean_net_return': 0.006791, 'win_rate': 0.5159, 'winners': 195, 'losers': 183}

### CLR_REQUIRE_REAL_VOLUME_CONFIRM

- Old rule: volume expansion OR dryup counts as confirmation
- New rule: block accepted signals confirmed only by volume dryup when correction RS < +3%
- Rationale: Tests whether weak dryup-only confirmations drive CLR's -97% independent drawdown.
- Verdict: **REPLACEMENT_NEED_MORE_DATA** — only 11 changed trades (<15); cannot judge the replacement
- Exact: 1094 trades (baseline 1105), expectancy +0.87% (baseline +0.84%)
- Walk-forward test: n=320, expectancy -0.48% (baseline -0.55%)
- Realistic: return +22.35% (baseline +22.35%), maxDD -7.04%, Sharpe 1.1187
- Same-exposure SPY/QQQ return: +15.77% / +18.82%
- Changed-trade stats: {'mode': 'TIGHTEN', 'changed_candidates': 190, 'changed_trades_simulated': 11, 'mean_net_return': -0.016883, 'win_rate': 0.2727, 'winners': 3, 'losers': 8}

## Safety

No paper signals, broker orders, trade proposals (doc-only), production thresholds, Gatekeeper/Veto logic,
execution/governance/live-capital modules, historical evidence, or SHORT_A frozen status were changed.


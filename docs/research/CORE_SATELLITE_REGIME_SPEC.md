# Core-Satellite Regime Specification (Phase 2A)

Status: RESEARCH_ONLY. No live trading, no paper signals, no production changes.

## Classifier

The engine reuses `research/strategy_lab_regime.py:classify_regime` — the same as-of
classifier validated in-loop by CORRECTION_LEADER_RECLAIM (1H.3) and LRR_REGIME_GATED
(1I.1). It is **not assumed correct**: Phase 2A tests whether throttling on it beats
buy-and-hold; the classifier earns or loses its core-engine role on those results.

### Exact as-of features (computed from cached bars at each date, no future data)

- SPY/QQQ trend state: above/below EMA20, MA50, MA200
- SPY/QQQ drawdown from 20d and 60d highs
- QQQ vs SPY 20d relative strength; SMH vs SPY and XLK vs SPY 20d relative strength
- SPY/QQQ 5d/10d/20d returns
- Volatility proxy: SPY/QQQ ATR% and VXX (VIXY fallback) 10d return
- No manual overrides (`manual_override: false` is asserted in the artifact)

### Labels

`BULL_TREND, CHOP, MARKET_CORRECTION, TECH_LED_CORRECTION, RISK_OFF, RECOVERY_RECLAIM, HIGH_VOLATILITY`

## No-lookahead protocol

- The regime label for day *t* is computed from bars up to and including day *t*'s close.
- The exposure derived from day *t*'s label is applied to the **next day's** return
  (*t → t+1*). Day 0 starts in cash.
- No future drawdown, no future labels, no backfilled regimes. This shift is enforced by
  construction in the engine and pinned by a unit test.

## Pre-registered exposure ladders (fixed before the backtest was run)

### E. REGIME_THROTTLED_QQQ

| Regime | QQQ exposure |
|---|---:|
| BULL_TREND, RECOVERY_RECLAIM | 100% |
| CHOP, HIGH_VOLATILITY | 60% |
| MARKET_CORRECTION, TECH_LED_CORRECTION | 40% |
| RISK_OFF | 10% |
| unknown/None | 0% |

### F. REGIME_THROTTLED_SPY_QQQ_BLEND

Same ladder; the risk asset is QQQ when `qqq_vs_spy_20 >= 0` at the decision close
(tech risk-on), otherwise SPY. Cash remainder earns 0% (no T-bill proxy is retained
locally; this understates blended returns slightly and is stated in the results).

### Satellites (G/H/I)

- Core weight = regime exposure × (1 − satellite capital).
- G: + PROD_SNIPER_CURRENT realistic sleeve at fixed 10% capital.
- H: + LRR_C1 (frozen 1I.2 spec: LRR_REGIME_GATED + max-1-entry-per-day) at fixed 10%.
- I: + both satellites at 7.5% each (15% total).
- Satellite streams are the realistic-portfolio daily equity paths of those sleeves
  (including their own internal cash drag — idle satellite capital drags, honestly).
- Daily rebalancing to fixed weights is assumed and stated.

All ladder values, caps, and the blend rule were fixed a priori in this spec and are
not tuned on results.

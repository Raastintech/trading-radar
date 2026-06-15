# LRR Regime Gate Specification (Phase 1I.1, fixed a priori)

Fixed on: 2026-06-12 — BEFORE the gated variant was ever run.

Derived from: Phase 1I regime breakdown of unrestricted LEADER_RESET_RECLAIM (cache/research/leader_reset_reclaim_latest.json)

## Allowed regimes

- `HIGH_VOLATILITY`
- `RECOVERY_RECLAIM`
- `TECH_LED_CORRECTION`

## Blocked regimes

- `BULL_TREND`
- `CHOP`
- `MARKET_CORRECTION`
- `RISK_OFF`
- `<any unknown/None label>`

BULL_TREND is blocked. The classifier emits RECOVERY_RECLAIM as its own label when the recovery/reclaim condition is present, so 'bull unless recovery condition' reduces to the allowed set.

## Classifier (as-of only)

Module: `research/strategy_lab_regime.py:classify_regime`

- SPY/QQQ drawdown from 20d/60d highs
- QQQ vs SPY 20d relative strength; SMH/XLK vs SPY 20d relative strength
- SPY/QQQ 5d/10d/20d returns
- SPY/QQQ above EMA20 / MA50 / MA200 trend state
- SPY/QQQ ATR%% and VXX (VIXY fallback) 10d return as volatility proxy

classify_regime reads compute_features_asof only; no future bars, no outcome labels, no manual overrides.

## Anti-overfit doctrine

The allowed set came from in-sample analysis of Phase 1I, so this variant is a NEW hypothesis. It must pass walk-forward, month/ticker concentration, same-exposure Sharpe, and 2026-decay checks; the regime set itself is frozen and MUST NOT be re-tuned on this sample.


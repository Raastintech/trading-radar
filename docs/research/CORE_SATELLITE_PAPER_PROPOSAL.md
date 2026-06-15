# Core-Satellite Paper Proposal (Phase 2A)

Manual review required. This document does not activate anything — paper-only plan.

## Candidate: REGIME_THROTTLED_QQQ

### Allocation rules

- Exposure ladder (fixed a priori, see CORE_SATELLITE_REGIME_SPEC.md):
  - BULL_TREND: 100%
  - RECOVERY_RECLAIM: 100%
  - CHOP: 60%
  - HIGH_VOLATILITY: 60%
  - MARKET_CORRECTION: 40%
  - TECH_LED_CORRECTION: 40%
  - RISK_OFF: 10%
- Decision once per close from the as-of regime label; applied next session.
- Satellites stay observation-only unless SATELLITE_VALUE_ADD was earned.

### Evidence

- Full span: +52.69% (CAGR +18.95%), maxDD -9.99%, Sharpe 1.3857, Calmar 1.8963
- 2026 YTD: +13.81%
- Exposure +74.73%, turnover/yr 19.5128

### Risk controls (paper)

- Hard drawdown limit: de-risk to RISK_OFF ladder if paper equity drawdown exceeds -12%.
- Rebalance cadence: daily check, trade only on ladder change (no intraday).
- Kill criteria: 6-month paper Sharpe < same-exposure QQQ, or churn > pre-registered caps,
  or any lookahead defect found.

### Why not live

- Paper validation must run through the existing holdout window (closes 2026-12-01);
  the live-capital gate (three env keys) remains untouched.


# Gatekeeper Precision Audit — Phase 1G.13

> RESEARCH-ONLY Gatekeeper precision audit. Forward MEASUREMENT only — NOT buy/sell/trade signals, NOT paper signals, NOT trade proposals, and it does NOT make any blocked name tradeable. Does NOT promote any sleeve, register a strategy, or modify the Gatekeeper, Veto Council, core/universe.py, strategy gates, execution, governance, or live capital, and makes no provider calls.

_Generated 2026-06-25T13:00:23.838440+00:00._

## Blocked cohort

- Total blocked names: **198** by source `{'rs_theme_1g10': 8, 'gatekeeper_snapshot': 190}`
- Block as-of range: `['2026-05-05', '2026-06-25']`
- Matured at 5d: **98** (verdict floor 20)
- Not-blocked WATCH control: **236**
- Blocked names that were missed winners: **93** `['AAL', 'ACMR', 'ALMU', 'AMAT', 'AMD', 'APLD', 'ARM', 'ASTS', 'ASX', 'AXTI', 'BB', 'BE', 'CDNS', 'CECO', 'CLF', 'COHR', 'CPSH', 'CRCL', 'CROX', 'CRS']`

## Forward returns by horizon (mean excess vs SPY)

| Horizon | A BLOCKED | B WATCH (not blocked) | C Alpha | D Random | E RS-top |
|---|---|---|---|---|---|
| 1d | +0.0010 | -0.0017 | -0.0050 | -0.0074 | -0.0027 |
| 3d | +0.0092 | -0.0044 | +0.0026 | +0.0103 | +0.0150 |
| 5d | +0.0016 | -0.0044 | — | — | — |
| 10d | +0.0121 | -0.0055 | — | — | — |
| 20d | +0.0335 | -0.0123 | — | — | — |

## Block reason performance (5d)

| Reason | n | matured | rel-SPY 5d | win% | MFE | MAE | Label |
|---|---|---|---|---|---|---|---|
| too_extended | 139 | 66 | +0.0026 | 39.4% | +0.0355 | -0.0238 | **OVER_BLOCKING** |
| unknown | 89 | 55 | -0.0057 | 41.8% | +0.0325 | -0.0290 | **GOOD_BLOCK** |
| volume_insufficient | 11 | 10 | +0.0157 | 30.0% | +0.0624 | -0.0266 | **OVER_BLOCKING** |
| no_atr_contraction | 7 | 7 | +0.0354 | 42.9% | +0.0891 | -0.0226 | **OVER_BLOCKING** |
| no_breakout | 6 | 6 | +0.0179 | 16.7% | +0.0699 | -0.0240 | **OVER_BLOCKING** |
| below_ma200_floor | 2 | 2 | +0.1289 | 50.0% | +0.2010 | -0.0084 | **DATA_ARTIFACT** |
| insufficient_history_260 | 2 | 2 | +0.0257 | 50.0% | +0.0636 | +0.0000 | **DATA_ARTIFACT** |
| ma50_not_rising | 2 | 2 | +0.0243 | 50.0% | +0.0819 | -0.0084 | **NEED_MORE_DATA** |

## Cache-depth artifact isolation

- Cache-depth-reason blocks: **4**
- Data-depth artifacts (shallow cache): **3** `['OWL', 'RAL', 'TEAM']`
- Trustworthy cache-depth blocks (deep enough): **1** `['HEI']`
- 3 of 4 cache-depth-reason blocks fired on tickers with < 260 usable bars (shallow on both caches) — those below_ma200_floor / insufficient_history_260 blocks are DATA-DEPTH ARTIFACTS, not trustworthy structure rejections. Deepen the cache before judging them.

## Rescue simulation (research-only — no trade / signal / proposal)

_RESEARCH-ONLY counterfactual: forward returns tracked AS IF the names had not been blocked — NO trade, NO paper signal, NO proposal. Does not approve anything. root_cause is only populated for the RS/theme 1G.10 subset; gatekeeper-snapshot blocks have root_cause=None._

| Subset | n | matured | rel-SPY 5d | win% |
|---|---|---|---|---|
| blocked_all | 198 | 98 | +0.0016 | 43.9% |
| blocked_ex_real_weak_structure | 192 | 92 | -0.0003 | 44.6% |
| blocked_ex_data_artifact | 195 | 95 | -0.0009 | 44.2% |
| blocked_only_gate_design_mismatch | 2 | 2 | +0.0257 | 50.0% |
| blocked_only_too_extended | 139 | 66 | +0.0026 | 39.4% |

## Verdict

### OVER_BLOCKING → recommendation E

**Split Gatekeeper blocks into HARD blocks vs SOFT warnings (research redesign).**

BLOCK basket +0.0016 rel-SPY > 0 and ≥ the not-blocked WATCH set (-0.0044) / random (None) at 5d — the gate appears to reject future winners short-horizon. Over-blocking reasons=['too_extended', 'no_breakout', 'no_atr_contraction', 'volume_insufficient']; good-block reasons=['unknown']. Split hard blocks vs soft warnings (E).

### Proposed hard/soft split (not implemented)

- **HARD_BLOCK**: real weak structure, broken trend, severe liquidity / spread risk.
- **SOFT_WARNING**: insufficient history, no ATR contraction, no breakout yet, early leader not mature.
- **DATA_WARNING**: MA200 / 260-bar unavailable or unreliable on a shallow cache.

> caveat: Random / RS-top / Alpha-board controls anchor at the MEDIAN block date (single as-of), while BLOCK/WATCH baskets use each name's own block date. Alpha-board membership is the CURRENT artifact, not a point-in-time snapshot. Treat control comparisons as best-effort context, not as-of truth.

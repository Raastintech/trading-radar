# Gatekeeper Precision Audit — Phase 1G.13

> RESEARCH-ONLY Gatekeeper precision audit. Forward MEASUREMENT only — NOT buy/sell/trade signals, NOT paper signals, NOT trade proposals, and it does NOT make any blocked name tradeable. Does NOT promote any sleeve, register a strategy, or modify the Gatekeeper, Veto Council, core/universe.py, strategy gates, execution, governance, or live capital, and makes no provider calls.

_Generated 2026-06-07T03:48:29.219239+00:00._

## Blocked cohort

- Total blocked names: **159** by source `{'rs_theme_1g10': 8, 'gatekeeper_snapshot': 151}`
- Block as-of range: `['2026-05-05', '2026-06-07']`
- Matured at 5d: **112** (verdict floor 20)
- Not-blocked WATCH control: **193**
- Blocked names that were missed winners: **64** `['ACMR', 'ALMU', 'AMAT', 'AMD', 'APLD', 'ARM', 'ARW', 'ASTS', 'ASX', 'BB', 'CECO', 'COHR', 'CRWV', 'CSCO', 'DDOG', 'DELL', 'FFIV', 'FSLR', 'FTNT', 'GEN']`

## Forward returns by horizon (mean excess vs SPY)

| Horizon | A BLOCKED | B WATCH (not blocked) | C Alpha | D Random | E RS-top |
|---|---|---|---|---|---|
| 1d | +0.0075 | -0.0003 | +0.0024 | +0.0015 | +0.0020 |
| 3d | +0.0209 | -0.0013 | +0.0312 | +0.0083 | -0.0018 |
| 5d | +0.0139 | -0.0019 | +0.0487 | +0.0059 | +0.0034 |
| 10d | +0.0340 | +0.0353 | — | — | — |
| 20d | +0.0077 | +0.0507 | — | — | — |

## Block reason performance (5d)

| Reason | n | matured | rel-SPY 5d | win% | MFE | MAE | Label |
|---|---|---|---|---|---|---|---|
| too_extended | 112 | 76 | +0.0176 | 46.1% | +0.0586 | -0.0199 | **OVER_BLOCKING** |
| unknown | 73 | 55 | +0.0207 | 56.4% | +0.0715 | -0.0225 | **OVER_BLOCKING** |
| volume_insufficient | 13 | 11 | +0.0482 | 45.5% | +0.0942 | -0.0153 | **OVER_BLOCKING** |
| no_atr_contraction | 7 | 7 | +0.0353 | 42.9% | +0.0901 | -0.0240 | **OVER_BLOCKING** |
| no_breakout | 6 | 6 | +0.0177 | 16.7% | +0.0711 | -0.0256 | **OVER_BLOCKING** |
| below_ma200_floor | 2 | 2 | +0.1289 | 50.0% | +0.2010 | -0.0084 | **DATA_ARTIFACT** |
| insufficient_history_260 | 2 | 2 | +0.0251 | 50.0% | +0.0672 | -0.0049 | **DATA_ARTIFACT** |
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
| blocked_all | 159 | 112 | +0.0139 | 49.1% |
| blocked_ex_real_weak_structure | 153 | 106 | +0.0129 | 50.0% |
| blocked_ex_data_artifact | 156 | 109 | +0.0120 | 49.5% |
| blocked_only_gate_design_mismatch | 2 | 2 | +0.0251 | 50.0% |
| blocked_only_too_extended | 112 | 76 | +0.0176 | 46.1% |

## Verdict

### OVER_BLOCKING → recommendation B

**Need more data — keep nightly cache-only measurement; production unchanged.**

BLOCK basket +0.0139 rel-SPY > 0 and ≥ the not-blocked WATCH set (-0.0019) / random (0.0059) at 5d — the gate appears to reject future winners short-horizon. Over-blocking reasons=['too_extended', 'no_breakout', 'no_atr_contraction', 'volume_insufficient', 'unknown']; good-block reasons=[]. Keep measuring nightly (B) before any redesign.

> caveat: Random / RS-top / Alpha-board controls anchor at the MEDIAN block date (single as-of), while BLOCK/WATCH baskets use each name's own block date. Alpha-board membership is the CURRENT artifact, not a point-in-time snapshot. Treat control comparisons as best-effort context, not as-of truth.

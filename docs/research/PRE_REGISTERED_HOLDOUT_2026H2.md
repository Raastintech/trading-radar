# Pre-Registered Out-of-Sample Holdout — 2026 H2

**Pre-registered on:** 2026-05-07
**Holdout window:** **2026-06-01** (inclusive) → **2026-12-01** (exclusive)
**Purpose:** Defeat the multiple-testing / look-ahead concerns flagged by
the Phase 0 system audit. Every strategy parameter, gate, and promotion
rule listed below is committed *before* any observations from the
holdout window exist. Inside the window, **no retuning is permitted**.

This document is a covenant, not configuration. Code remains the source
of truth; this file pins what the code says today.

---

## Why a holdout

Across six sleeves and the H3 cohort, the audit identified:

- The H3 gate was extracted post-hoc from SNIPER_V6 closed-trade CSVs
  (`research/sniper_h3_validation.py:84-91`) and then "validated" on the
  same history that produced it.
- Five+ versions of SNIPER (v1 → v6) were tried before settling on the
  current parameters; no Bonferroni / FDR correction was applied across
  versions and parameter sweeps.
- Forward cohorts (~n=20-30) imply 95% CI ≈ ±10pp on a 50% win rate —
  too wide to claim edge.

A pre-registered holdout — parameters frozen now, outcomes scored only
after 2026-12-01 — gives one clean OOS pass against rules nothing in the
holdout window could have influenced.

---

## Active sleeves at registration (frozen for the holdout)

Status as of 2026-05-07 from `core/strategy_registry.py`:

| Sleeve | Status | Baseline tag |
|---|---|---|
| SNIPER | `active_paper` | `SNIPER_V6` |
| VOYAGER | `active_paper` | `VOYAGER_PAPER` |
| SHORT | `active_paper` | `SHORT_A` |
| REMORA | `frozen` | research-only — not in holdout |
| CONTRARIAN | `frozen` | research-only — not in holdout |
| SHORT_B | `frozen` | research-only — not in holdout |
| PATHFINDER | `future_research` | not in holdout |

Only the three `active_paper` sleeves participate in the holdout
evaluation. Frozen sleeves are out of scope: changing their parameters
inside the window is permitted (they are not being judged).

---

## Frozen parameters (snapshot from code, 2026-05-07)

### Governance (`core/config.py`)

```
MAX_POSITION_PCT   = 0.02   # 2 % of equity per single position
MAX_DAILY_LOSS_PCT = 0.05   # 5 % daily loss circuit-breaker threshold
ALLOW_SHORTS       = true   # short side enabled (paper)
PAPER_TRADING      = (env)  # set true in production env at registration
```

### SNIPER (`strategies/sniper.py`)

```
VOL_SPIKE_THRESH       = 1.4
MIN_SCORE              = 70
MIN_RRR                = 2.5
BARS_NEEDED            = 75
MA50_SLOPE_BARS        = 20
ATR_CONTRACTION_THRESH = 0.85
STOP_ATR_MULT          = 1.5
TARGET_ATR_MULT        = 3.75
VIX_REGIME_CEILING     = 28.0
SPY_BARS_NEEDED        = 220
LARGE_CAP_UNIVERSE     = (82-name whitelist as of 2026-05-07; SaaS cohort
                          excluded per v5 autopsy — see file head comment)
```

### VOYAGER (`strategies/voyager.py`)

```
MIN_PRICE                  = 5.0
MIN_AVG_DOLLAR_VOL         = 5_000_000
MAX_EXTENSION_MA50         = 0.12
MA200_FLOOR                = 0.92
RS_50_WINDOW               = 50
RS_130_WINDOW              = 130
DVOL_TREND_RATIO           = 0.85
EARNINGS_SAFE_DAYS         = 15
MIN_FUNDAMENTAL_SCORE      = 40
MIN_FUNDAMENTAL_SCORE_EARLY= 55
MIN_SCORE                  = 65
MIN_RRR                    = 2.5
BARS_NEEDED                = 260
BASE_MAX_PRICE_TIGHT       = 0.03
BASE_MAX_DIST_MA50         = 0.05
```

> Phase 0 fix (2026-05-07): the dollar-volume baseline window in
> `voyager.py` was renamed from `avg_dvol_60` (misleading) to
> `avg_dvol_baseline_40` and the slicing replaced with an explicit
> `range(-60, -20)`. Numerically identical to the prior form (regression
> test: `tests/unit/test_voyager_dvol_window.py`). This is a code-clarity
> fix, not a parameter change, and does not invalidate the holdout.

### SHORT_A (`strategies/short_sleeve.py`)

```
REACTION_MIN_PCT  = -3.0
VOL_SPIKE         = 1.5
MAX_LAG_SESSIONS  = 3
MIN_SCORE         = 55
MIN_RRR           = 2.0
BARS_NEEDED       = 30
```

### H3 cohort gate (`research/sniper_h3_validation.py`)

```
H3_SCORE_LO, H3_SCORE_HI = 80.0, 90.0
H3_VIX_LO,   H3_VIX_HI   = 15.0, 20.0
H3_VOL_RATIO_MAX         = 1.5
H3_SECTORS               = {"Healthcare", "Communications", "Technology"}
```

### Submission-time gates (`core/submission_gate.py`, Phase 0)

```
- session.is_execution_allowed         (hard)
- council verdict == APPROVED          (hard)
- circuit_breakers.gate                (hard, when wired in)
- regime favorability ≠ "avoid"        (hard, fail-open on missing artefact)
- portfolio_risk.check                 (hard, when wired in)
```

The five gates above are added by Phase 0 and apply uniformly during the
holdout window. Adding a new gate inside the window WOULD invalidate the
pre-registration; tightening or relaxing a threshold above WOULD
invalidate it. New gates and threshold changes go into a follow-up
holdout (`PRE_REGISTERED_HOLDOUT_2027H1.md` etc.).

---

## Evidence verdicts at registration

From the most recent forward / autopsy reports as of 2026-05-07:

| Sleeve | Latest verdict | Source |
|---|---|---|
| SNIPER_V6 (full) | active_paper, evidence accumulating | `research/sniper_h3_forward_report.py` |
| SNIPER H3 cohort | `H3_PROMISING_BUT_THIN` (n below 30) | `research/sniper_h3_validation.py:306` |
| VOYAGER_PAPER | active_paper, evidence accumulating | forward ledgers under `data/state/` |
| SHORT_A | active_paper, no promotion proposed | autopsy + forward |

No sleeve has been promoted to live. The holdout is judged in November
2026 against these baselines.

---

## Pre-committed promotion criteria (read once, scored later)

A sleeve passes the holdout **only if all four** of the following hold
when measured on closed trades inside `[2026-06-01, 2026-12-01)`:

1. **Sample size:** ≥ 30 closed trades during the window. Below this we
   declare `INSUFFICIENT_DATA` and do not promote — *no exceptions*.
2. **Lower-CI win rate > 50 %:** the lower bound of the 95 % bootstrap
   CI on win rate (frictioned at 0.30 % round-trip) must exceed 0.50.
   Same `RNG_SEED=20260503` as `research/strategy_evidence_audit.py`.
3. **Lift over random control:** observed WR must beat the random-
   entry control (same dates, same friction) by ≥ 5 pp *and* the lower
   CI bound on the delta must exceed 0.
4. **Regime-conditioned consistency:** WR in
   `Bull Continuation ∪ Bull Pullback / Buy-the-Dip` must not be lower
   than ungated WR by more than 5 pp. (This is the test the audit asked
   for: does the regime gate add lift, or is it dead weight?)

For the **H3 cohort** specifically: the gate must additionally satisfy
WR ≥ 55 % and ≥ 5 pp lift over the same-period SNIPER_V6 cohort with
the H3 filter inverted (anti-cohort). This pre-empts the "discovery
sample = test sample" concern the audit raised.

If any criterion fails, the sleeve is **demoted**: research-only or
parameter re-evaluation in a fresh out-of-sample window.

---

## Operational rules during the window

1. **No parameter retuning** for the active sleeves (`SNIPER`,
   `VOYAGER`, `SHORT`). PRs that touch any constant in the snapshot
   above must be rejected unless they include `holdout-invalidating`
   in the PR description and an explicit decision to abandon the
   holdout.
2. **No silent universe edits.** The SNIPER `LARGE_CAP_UNIVERSE`
   whitelist as of 2026-05-07 is the holdout universe. Adding or
   removing tickers inside the window invalidates the holdout for
   SNIPER.
3. **Bug fixes are allowed** if they do not change scoring or signal
   selection: the Voyager dollar-volume rename is the canonical
   example. Each such fix must include a regression test that pins the
   numerical equivalence (see `tests/unit/test_voyager_dvol_window.py`).
4. **New gates / new sleeves** are out of scope for the holdout. They
   start with their own pre-registration on a future window.
5. **The forward-tracking ledgers stay running** —
   `data/state/regime_forecast_forward_log.jsonl` and
   `data/state/stock_lens_forward_log.jsonl` are written normally;
   nothing about the holdout changes the data-collection path.

---

## Scoring procedure (run once, after 2026-12-01)

```bash
# 1. Resolve any unresolved forward outcomes inside the window.
./scripts/run_research_cycle.sh resolve

# 2. Run the per-sleeve forward reports restricted to the window.
.venv/bin/python research/sniper_h3_forward_report.py --window 2026-06-01:2026-12-01
.venv/bin/python research/forecast_forward_report.py
.venv/bin/python research/stock_lens_forward_report.py

# 3. Strategy-level evidence audit.
.venv/bin/python research/strategy_evidence_audit.py --window 2026-06-01:2026-12-01

# 4. Render the verdict table.  Manually check each promotion criterion.
```

The output of step 4 is appended to a sibling document
`PRE_REGISTERED_HOLDOUT_2026H2_VERDICT.md` (created at scoring time, not
now). The verdict document is the only place the four pass/fail
decisions are recorded. It is committed to git so the decision is
auditable.

---

## What this is not

- This is not a backtest. The values in the snapshot above were chosen
  before 2026-05-07 from in-sample work; the holdout *re-tests* them on
  data that did not exist at choice time.
- This is not a guarantee. A sleeve passing the holdout still has
  finite n; institutional standards typically demand a second
  out-of-sample window before live capital.
- This is not a substitute for the autopsy / failure-mode review
  process (`research/sleeve_failure_autopsy.py`,
  `research/review_misses.py`) — those continue to run during the
  window for diagnostic value, but they do not gate the holdout
  verdict.

---

*Registered: 2026-05-07. Author: gem.*
*Sealed against retuning until: 2026-12-01.*

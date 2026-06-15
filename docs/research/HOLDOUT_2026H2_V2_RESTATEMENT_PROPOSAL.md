# DRAFT — Holdout 2026H2 V2 Restatement Proposal (NOT ACTIVE)

**Status: PROPOSAL ONLY.** This document has no force until the operator
ratifies it with a new pre-registration date. The V1 covenant
(`docs/research/PRE_REGISTERED_HOLDOUT_2026H2.md`, designated
STARVED_HOLDOUT_V1) remains the active covenant until then, and its
evidence is preserved untouched.

## Why restate

V1's ">=30 closed trades by 2026-12-01" is statistically unreachable at
observed emission (see `docs/research/HOLDOUT_FEASIBILITY_AUDIT.md`,
generated 2026-06-11). Waiting out the window collects
~zero information while paying ~6 months of calendar time.

## Pre-conditions before ratifying V2 (in order)

1. Cache-layer data-correctness fix landed and verified (depth preserved
   across refreshes; no scanner behavior change).
2. Emission-calibration study (`research/emission_calibration_study.py`)
   has a variant per sleeve at 1–3 candidates/week with non-negative
   forward quality vs random; the chosen variant is documented BEFORE the
   new window opens.
3. Recalibrated gates run in paper for >=5 trading days to confirm
   realized emission matches the study estimate (no tuning to force flow).

## Proposed V2 shape (to be finalized at ratification)

- Window: 6 months from ratification date.
- Sample bar: derived from the verified emission rate so that
  P(reach bar | study rate) >= 80%, floor n >= 20 closes.
- All other V1 acceptance criteria (bootstrap lower-CI WR > 50% at 0.30%
  RT friction, beat random-entry control by >=5pp, regime-conditioned WR
  within 5pp) carry over unchanged.
- VOYAGER closes counted at its paper time-stop horizon, not 6-18 month
  thesis horizon (V1 design flag).

## What does NOT change

- No retuning inside the new window once ratified.
- SHORT_A stays frozen; no new sleeve enters via this proposal.
- The live-capital env gate stays OFF regardless (see core/config.py
  three-key gate; this proposal never touches it).

# Dashboard Truth Audit — June 2026

**Date:** 2026-06-07 · **Scope:** cache-only audit (Mode 1/2/3 sources) ·
**Mutations:** none (no provider/DB/governance/execution/gate/universe/live-capital changes)
**Artifacts:** `cache/research/dashboard_truth_audit_latest.json` · `logs/dashboard_truth_audit_latest.txt`

> Question asked: *Are we on the right track, or collecting a lot of data that
> doesn't answer the one question that matters — can the system find high-quality
> trades early and filter them correctly?*

## TL;DR

The dashboard plumbing is **solid and honest**. The **trading edge is unproven**.
The scanner funnel catches **1.1%** of forward winners; a dumb `sector_rs`
baseline catches **18%** and `mom_20_60` hits **39% precision / +32.8%** average
forward return. We are operationally clean but **digging sideways** around a
front-of-funnel that is nearly empty of winners (`UNIVERSE_MISS`).

**Primary path:** prove & fix **scanner recall** (Path B). **Backup:** mature
**Gatekeeper precision** 10d/20d (Path C). **Stop** treating immature/
non-reproducible micro-cohorts (rs-theme-forward, universe A/B, power-trend,
forecast hit-rate) as decision inputs.

## Task 1 — Source refresh & freshness

All Mode 1/3 sources were refreshed by tonight's nightly cycle (02:35 UTC).
Three audits requested by the operator were **stale (Jun 4, ~2.4 days)** because
they are operator-invoked and wired into **no timer**; this audit re-ran them
(all cache-only, exit 0):

| Audit | Before | After (03:48 UTC) |
|---|---|---|
| gatekeeper-precision | Jun 4 | `OVER_BLOCKING_SHORT_HORIZON_ONLY` → `OVER_BLOCKING` (rec B) |
| power-trend | Jun 4 | `NEED_MORE_DATA` (matured 13 → **12**, < 15 floor) |
| rs-theme-forward | Jun 4 | `NO_VALUE` → **`PROMISING_BUT_UNPROVEN`** |

`options-regime` (the only provider-touching command in the list) was **read,
not re-run**, to avoid burning FMP budget (June: 7,403 calls used). Its artifact
is ~1.3h old — acceptable.

**Finding (freshness):** gatekeeper-precision / power-trend / rs-theme-forward
have no scheduled cadence, so the dashboard/MCP can surface multi-day-stale
verdicts as if current.

## Task 2 — Contradictions (8)

| # | Panel | Root cause | Risk |
|---|---|---|---|
| C1 | Mode 2 MCP `STALE — rerun required` | age is ~1.2h; staleness is forced by **`session_changed`** (sidecar `regular` vs live `CLOSED`). A weekend rerun re-stales immediately. | LOW (wording) |
| C2 | Research-state "entries permitted within sleeve rules" vs STANDBY | two independent computations; phrasing ignores `session==CLOSED`/STANDBY. Live state is actually **CONFLICTED**, not NORMAL — the label is itself unstable. | MEDIUM |
| C3 | Mode 1 "no signals" vs Mode 2 Alpha WATCH names | tonight's `alpha_discovery_board` is **empty (0)**; the WATCH list is the **2-day-stale (Jun 5)** premarket overlay, shown without a stale banner. | MEDIUM |
| C4 | Mode 1 `SHORT_A 0/30 no flow` | registry marks SHORT_A **FROZEN (2026-05-24)**; panel renders an active-style `/30` target. | LOW (clarity) |
| C5 | Paper open-count > 0 while live positions = 0 | 10 legacy `decisions` rows `position_opened=1` with no `fill_price` (quarantine), not live positions. `ready_to_gate_clean=True`, `all=False`. | LOW (documented) |
| C6 | Mode 3 scanner recall 1.1% vs "system OK" | operational health ≠ selection edge; nothing persistently signals "no edge". | **HIGH** |
| C7 | Mode 2 ticker `QTSLA` accepted | shape-only validation (`isalpha`, ≤6 chars); degrades to "insufficient bars", no fabricated data. | LOW |
| C8 | rs-theme-forward / power-trend verdicts | flip on a **frozen** cohort (`NO_VALUE`↔`PROMISING`; matured 13→12) because the random control is re-drawn and the forward window slides at n≈20. | MEDIUM |

## Task 3 — Data usefulness

See `dashboard_truth_audit_latest.json:task3_data_usefulness` for the full table.

- **HIGH:** scanner_truth_review, scanner_baseline_comparison, paper-evidence/holdout.
- **MEDIUM:** scanner_recall_precision, gatekeeper_precision, strategy_tournament, stock_lens_forward, risk-telemetry, weekly_review.
- **LOW:** options_regime, regime_forecast_validation, forecast_forward_summary (bull-tape beta, not skill), social_arb, rs_theme_lens_triage.
- **NOT_READY:** universe A/B, rs_recall_forward, power_trend_extension.
- **MISLEADING_IF_USED:** rs_theme_forward_validation (verdict flips on noise), alpha_discovery board/overlay (empty board / stale overlay).

## Task 4 — Are we waiting on the right data?

**Useful waits:** holdout closes (≥30 by 2026-12-01; **day 6 of 183**) — the only
gate that can promote a sleeve; gatekeeper precision 10d/20d (to confirm/deny
over-blocking).

**Low-value waits:** rs-theme-forward (non-reproducible; matured 1G.11 already
found no edge), universe A/B (`NOT_RETAINED`; measures a surface the gates
discard anyway), power-trend 10/20d (narrow theme exception), forecast hit-rate
(beta, not skill).

**Missing now (more valuable than any wait):**
1. A **dated, point-in-time historized board** — forward *precision* is
   `NOT_COMPUTABLE_YET` because only a latest snapshot exists.
2. **Deep price cache** (200+ bars) — production overwrites `cache/prices` with
   ~90 bars, so MA200/260 gates are non-functional and many "misses" are
   shallow-cache artifacts.
3. A **baseline-vs-funnel** measurement inside the production funnel.

## Task 5 — Scanner/filter sharpness

| Area | Status |
|---|---|
| Universe selection | WEAK (UNIVERSE_MISS #1; theme-blind) |
| Top-1000 ranking | WEAK (no RS/theme/earliness/diversity) |
| Scanner recall | **BROKEN** (1.1% vs 18% baseline) |
| RS/theme feeder | NEED_MORE_DATA |
| Alpha board | WEAK (empty/stale; no historization) |
| Stock Lens | NEED_MORE_DATA |
| Gatekeeper | NEED_MORE_DATA (over-block @5d, 49% win, no 10/20d) |
| Power-trend exception | NEED_MORE_DATA (n=12<15) |
| Options regime | GOOD (diagnostic only) |
| Strategy sleeves | NEED_MORE_DATA (holdout day 6) |
| Dashboard | GOOD operationally / WEAK at signaling "no edge" |

**Direct answers:** scanners sharp? **No.** Gates sharp? **Unproven (coin-flip).**
Too strict? **Likely upstream** (gates kill ~93% of early leaders). Too loose?
**Not the binding problem.** Missing themes early? **Yes** (semis/hardware/space
LEADING but off-board). Over-blocking winners? **Probably, short-horizon,
unconfirmed.** Under-filtering noise? **Secondary.** Dashboard indicators enough?
**Operationally yes; for edge, no.**

## Task 6 — Minimum viable profitable-system path

- **One core bottleneck:** the funnel doesn't find winners early — they never
  enter the universe (UNIVERSE_MISS) and survivors are dropped by score gates.
  Recall 1.1% vs 18% baseline.
- **Proof:** `scanner_truth_summary` + `scanner_baseline_comparison`.
- **Next one action:** a **research-only shadow lane** running `sector_rs` +
  `mom_20_60` on a deep-cache, theme-aware universe, historizing a dated board,
  measuring recall + forward precision vs the live funnel.
- **Stop:** spinning up more verdict-flipping micro-cohorts/labels and dashboard
  indicators.
- **Keep:** holdout closes, gatekeeper precision, price-cache deepening.
- **Remove from decisions until proven:** rs-theme-forward label, universe A/B,
  power-trend exception, forecast hit-rate, Alpha overlay watchlist.
- **Primary: Path B (scanner recall). Backup: Path C (Gatekeeper precision).**

## Task 7 — Decision-grade criteria

See `docs/research/DASHBOARD_DECISION_GRADE_CRITERIA.md`.

## Task 8 — Proposed fixes (none applied)

| ID | Item | Type | Risk | When |
|---|---|---|---|---|
| F1 | MCP "rerun required" on `session_changed` | display bug | low | later |
| F2 | Alpha overlay missing stale banner | data-quality | medium | soon |
| F3 | "entries permitted" wording vs STANDBY/CLOSED | wording | medium | soon |
| F4 | SHORT_A `/30` → `FROZEN` | label | low | later |
| F5 | Persistent "SELECTION EDGE: UNPROVEN" banner | improvement | medium+ | **soon (highest value)** |
| F6 | Verdict reproducibility (seed control, freeze as-of) | research-correctness | medium | soon (research code) |
| F7 | Timer/staleness for operator-only audits | freshness | low | later |
| F8 | Unknown-symbol → "UNKNOWN SYMBOL" | polish | low | optional |

## Task 9 — Code changes

**None.** Per Task 8 ("propose first"), no change was applied to the 7,381-line
production dashboard or to research-correctness logic without explicit operator
go-ahead. No tests run (no code changed).

## Final answer

- **On the right track?** Operationally yes, **strategically no.**
- **Digging the wrong hole?** **Partly yes** — accumulating immature, sometimes
  non-reproducible micro-cohorts and labels around a funnel that catches ~1% of
  winners. The good news: the audit data already names the hole (UNIVERSE_MISS)
  and a baseline that beats the funnel 16×.
- **Next:** freeze new indicator/label work; build the **recall-repair shadow
  lane**; keep collecting holdout closes + gatekeeper precision; demote the
  noisy cohorts from decision-making until reproducible and matured.

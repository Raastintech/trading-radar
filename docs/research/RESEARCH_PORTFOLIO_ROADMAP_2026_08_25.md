# Research Portfolio Roadmap — 2026-08-25

*Generated 2026-08-25T21:08:08+00:00 · updated 2026-08-25T21:43:44+00:00 (same day — §6/§7/§9 revised after building the two approved measurement-infrastructure pieces) · research-only, cache-only.*

This note records the outcome of a read-only portfolio review of the research
engine's strategy families, forward evidence, and infrastructure gaps as of
2026-08-25. It is documentation only — no code, scanner logic, scores,
rankings, filters, gates, thresholds, factor weights, HC/EO rules, Alpha Focus
rules, program verdicts, candidate selection, evidence files, runtime cache,
logs, ledgers, generated artifacts, env files, or secrets were changed to
produce it or as a result of it.

## 1. Executive Decision

The research program should no longer be reviewed or discussed as one
monolithic scanner. It is a portfolio of research lanes at different
maturities, and decisions about it must be split two ways:

- **By horizon** — 5d/10d/20d evidence is old enough to draw conclusions from;
  45d/60d evidence is not, for purely mechanical (sample-size) reasons.
- **By research lane** — some lanes have cleared their evidence bar and
  should continue untouched, some have failed cleanly and should be frozen or
  archived, and some are still accumulating sample and must wait. Treating
  all of it as a single "is the system working" question hides all three of
  these answers behind one blended, perpetually-MIXED verdict.

## 2. Horizon Split

| Horizon | Matured sample | Resolution | Status |
|---|---|---|---|
| 5d | 5,359 (general tracker) / 8,460 ticker-days (alpha-vs-baseline study) | ~100% | **Mature** |
| 10d | 4,620 / 7,714 | ~100% | **Mature** |
| 20d | 3,046 / 6,216 | 88.0% | **Mature enough to trust** |
| 45d | 134 | 55.1% | **Still immature and partially unresolved** |
| 60d | 0 | — | **Zero information; first episode ≈2026-09-09** |

- 5d/10d/20d are mature enough to support short-horizon conclusions now.
  Waiting for more data at these horizons will not change the answer — at
  3,000–8,000+ matured episodes, more of the same methodology produces a
  more precisely measured version of the same result, not a different one.
- 45d is thin and half-unresolved; conclusions drawn from it today would be
  premature.
- 60d has zero matured episodes. This is not a judgment call — there is
  nothing to read yet, and the first data point is roughly two weeks out.
- **"Wait for more data" is not a blanket answer.** It is the correct
  response only for 45d/60d. For 5d/10d/20d, the evidence has already
  spoken, and the correct response is a decision (continue / freeze /
  archive), not further waiting.

## 3. Correction to Prior Read

An earlier pass characterized the short-horizon forward evidence as flatly
"no proven edge." That was incomplete and is corrected here.

The **combined forward tracker** (n=6,209 across the whole board — HC, EO,
social, tactical, everything blended together) remains **MIXED**, and that
part of the read stands.

However, a separate, more controlled artifact
(`cache/research/recall_repair_shadow_forward_latest.json`, 61 dated as-of
snapshots from 2026-04-23 to today, 6,216–9,056 mature ticker-days per
horizon) runs a 4-way comparison of the **production Alpha Discovery
board** against a random-200 control, a simple 20d-RS-top-200 baseline, and
the recall-repair shadow lane. The Alpha Discovery board's excess return
over SPY is **positive and grows monotonically with horizon**, and beats
all three other cohorts at every horizon measured:

| Horizon | Alpha excess vs SPY | Random | RS-top | Shadow |
|---|---|---|---|---|
| 1d | +0.20% | +0.01% | −0.04% | +0.04% |
| 5d | +0.48% | −0.24% | −0.77% | −0.33% |
| 10d | +0.81% | −0.59% | −1.08% | −0.34% |
| 20d | +0.95% | −1.20% | −1.92% | −1.16% |

At the same maturity, the same artifact's `recall_at_recall_horizon` block
shows Alpha Discovery's winner-recall (share of the eventual +20%/+30%/+50%
winner set that ever appeared on the board) is **0.3%–0.9%**, while the
broader shadow and RS-top boards recall **15%–31%** of the same winners, at
the cost of negative average excess return (shadow precision 2%–16%,
false-positive rate 84%–98% at those same thresholds).

State clearly:

- **This does not prove a complete trading system.** It is one artifact,
  one methodology, over one ~4-month window, and is explicitly labeled
  `PROMISING_BUT_UNPROVEN` by its own decision gates.
- **It suggests Alpha Discovery is a precision board with low recall** —
  what it holds performs modestly better than SPY on average, but it almost
  never holds the largest winners.
- **The problem is not simply "no edge."** The problem is a small, real
  edge on a narrow board, paired with poor winner recall — two separate
  and already-quantified issues, not one vague "unproven" verdict.

## 4. Continue

These lanes have either cleared their own evidence bar or are correctly
still accumulating sample, and should continue unchanged:

- **Alpha Discovery board** — mature, small, positive average-return edge
  vs. SPY and vs. random/RS-top/shadow controls (§3). Protect as-is.
- **Same Session Clean cohort** — best-performing existing cohort (10d mean
  +5.72%, win rate 63.1%).
- **High-Conviction Alpha** — 10d sample now adequate (n=258, "promising"
  per the cohort tracker); let it clear its own pre-registered floor rather
  than force a verdict early.
- **Alpha Focus** — existing operator-facing review-priority mechanism;
  no change indicated.
- **Emerging Outlier tracking, in limited form** — 10d sample (n=552) still
  immature ("mixed"); keep tracking, do not expand its role yet.
- **scanner-recall-cohorts historizer** (`scanner_recall_cohorts_history.jsonl`)
  — the live-board recall accrual mechanism (§7); ~7 weeks into accrual,
  let it keep running.
- **Existing 45d/60d maturation** on the general forward tracker — passive
  and correct to wait on (§2).

## 5. Freeze / Archive

These lanes have failed cleanly at adequate sample size, or represent
repeated negative results in the same family, and should be frozen or
archived rather than re-tested:

- **LRR family** (standalone, regime-gated, clustered variants) — archive,
  per the family's own prior recommendation.
- **Filter-loosening / raw over-blocking experiments** (the Failure Miner
  filter-replacement track) — closed; raw over-blocking does not survive
  flow-cap-and-exits at adequate sample.
- **Gatekeeper-loosening track** — closed at current evidence; blocks lead
  short-horizon but win rate is coin-flip and longer horizons are immature.
  Do not loosen the gate on this evidence.
- **Strategy Tournament / Strategy Lab variant search** — no further
  variant search on the existing pre-registered bar; twelve independent
  strategy families tried, one passed a full gate (§6). The next unit of
  research effort is better spent deepening that one than searching for a
  thirteenth family.
- **New strategy-family launches, for now** — paused in favor of (a) the
  45d/60d maturation already in flight, and (b) resolving the two concrete
  gaps in §6 and §7.
- **Options directional strategy** — already archived pre-build (feasibility
  gate failed for insufficient persisted chain history).

## 6. Core-Satellite Gap

- **Core-Satellite 2A (regime-throttled QQQ/BLEND) is the only strategy
  family that has passed a full historical/backtest gate**: +52.7% CAGR,
  max drawdown −10% vs. QQQ's −22.8%, Calmar 1.90 vs. 1.20, positive in
  every year tested. (The leveraged variant, 2A.1, missed its CAGR gate by
  64bps and does not count as a pass.)
- **It currently lacks a forward-validation venue.** `docs/research/
  CORE_SATELLITE_PAPER_PROPOSAL.md` was written for a paper-trading venue
  that no longer exists — paper-trading and broker execution were
  permanently decommissioned 2026-06-13, and `scripts/run_paper_evidence.py`
  does not run. The one strategy that earned the next rung of the
  promotion ladder currently has nowhere to go.
- **Built and seeded 2026-08-25** (measurement infrastructure — approved
  separately from the alpha-system freeze; see §9): `research/
  core_satellite_forward_shadow.py`, a narrow, cache-only forward shadow
  ledger. Reuses the exact backtested rules rather than re-deriving them
  (`research.strategy_lab_regime.classify_regime` for the as-of regime,
  `core_satellite_portfolio.target_exposure`/`blend_asset` for the
  exposure ladder), with the same no-lookahead rule as the original
  backtest (exposure decided from day i-1's regime applies to day i's
  return). Tracks both gate-passing variants (QQQ-only and SPY/QQQ
  blend). Append-only ledger at `data/research/
  core_satellite_forward_shadow.jsonl`, metrics sidecar at
  `cache/research/core_satellite_forward_shadow_latest.json`
  (CAGR/maxDD/Calmar, suppressed until >=20 realized days to avoid
  noisy early annualization). No fills, no slippage modeling, no
  broker/execution imports — pure arithmetic over already-cached bars.
  Seeded on 2026-08-24 (NAV=1.0 for both variants); the first realized
  daily return lands the next time it runs. 14 unit tests
  (`tests/unit/test_core_satellite_forward_shadow.py`).
- **This does not backfill history.** Per instruction, the ledger starts
  from whatever session was current when it first ran — it does not
  reconstruct what the shadow would have shown between the backtest's
  cutoff and today. That is real forward-validation days lost by not
  having built this sooner, not something this task recovers.

## 7. Live-Board Recall Measurement

- **The legacy "scanner recall 0.0%" figure is a decommissioned-funnel
  autopsy, not a measurement of the live repaired board.**
  `cache/research/scanner_recall_precision_latest.json` defines its
  `overall_pct` as "share of winner set EVER in any historized funnel
  stage," traces exactly the 172-winner set from the pre-decommission
  VOYAGER+SNIPER funnel autopsy, and its own `precision_forward.status`
  field reads `NOT_COMPUTABLE_YET` — "today's board has no forward window
  yet."
- **`data/research/scanner_recall_cohorts_history.jsonl` is the live-board
  recall accrual mechanism.** It has been registering dated snapshots of
  the current live board (33 snapshots, 2026-07-09 → 2026-08-24, ~771
  tickers on the latest one) so that a genuine forward recall check
  becomes possible once enough snapshots have matured bars behind them.
  This is what the digest's "live research-board recall accruing via
  scanner-recall cohorts: NEED_MORE_DATA" refers to — it is ~7 weeks into
  accrual, not stalled at zero.
- **Correction (2026-08-25, same day): this measurement already existed
  and did not need to be built.** A closer read of `research/
  scanner_recall_cohorts.py` found it already runs a full forward
  comparison (`build_report`/`_verdict`) of the live board against
  strict/loose/RS-baseline/random cohorts, at 13/15 matured dates as of
  today (`cache/research/scanner_recall_cohorts_latest.json`: verdict
  `NEED_MORE_DATA`, scanner-watchlist 20d winner-recall 13.5% vs.
  RS-baseline 5.5%, gates need 15 matured dates and haven't yet been
  met). The earlier framing in this section (re-point the recall
  methodology at the cohort snapshots) was based on an incomplete read —
  the methodology was already pointed there. The actual gap was
  narrower: the digest only surfaced the bare verdict string, and the
  fallback (non-LLM) audit path was still misreading the labeled legacy
  figure as a live blocker.
- **Fixed 2026-08-25** (measurement infrastructure — approved separately
  from the alpha-system freeze; see §9):
  - `research/nightly_operator_summary.py` — the live-board recall note
    now surfaces the real progress (matured-date count vs. the 15-date
    floor, scanner-watchlist vs. RS-baseline recall at 20d) instead of a
    bare "NEED_MORE_DATA" string with no way to judge how close it is.
  - `research/journal_audit_reviewer.py` — `_RECALL_RE` now excludes the
    labeled "Legacy council-funnel recall" figure via a negative
    lookbehind, so `scanner_recall_pct` is only ever populated from a
    genuinely live-board recall mention. Before this fix, the fallback
    audit path (which ran on 8 of the last 9 nights before the
    role="chat" fix landed, and will still run whenever the LLM call
    fails) raised a false HIGH `scanner_recall` flaw, a false P0
    "build scanner recall diagnostics" action, and a false "low recall"
    line in the one-line summary — every single night, off a
    decommissioned number, for at least seven weeks. Verified against
    today's real digest: zero recall flaws/actions post-fix, and the
    diagnostics-building action (queued 27 times since 2026-07-08,
    already ADDRESSED once) will not re-fire off this dead metric again.
    5 new/updated unit tests.
- **No new recall-measurement script was written** — there was nothing
  left to build once the surfacing and parsing were fixed. The
  scanner-recall-cohorts historizer (§4) continues to accrue toward its
  own 15-matured-date gate on its own schedule.

## 8. Guardrails

- AI may review, propose, test, and explain.
- AI may not independently rewrite production alpha logic — scanner
  logic, scores, rankings, filters, gates, thresholds, factor weights,
  HC/EO rules, Alpha Focus rules, or program verdicts.
- Any production change requires: an explicit hypothesis, a baseline
  comparison, out-of-sample forward evidence, a random/control cohort
  comparison, no-lookahead validation, human approval, passing tests, and
  preserved research-only safeguards.

This is the same governing rule already recorded in `docs/ROADMAP_PHASES.md`
→ "Future Direction — Recursive Self-Improvement Governor" and applies
identically here.

## 9. Current Status

**Operating principle (set 2026-08-25): wait for 45d/60d before changing
the alpha system; do not wait for 45d/60d to fix missing measurement
infrastructure.** These are governed by different constraints — the
alpha-system verdict is genuinely sample-limited at 45d/60d (§2), while
measurement infrastructure (Core-Satellite's forward-validation venue,
§6; live-board recall surfacing, §7) was blocked only on being built, not
on any horizon maturing. Conflating the two would have used the 45d/60d
gate as cover for delaying work it never actually governed.

- No production alpha logic changes approved. Scanner logic, scores,
  rankings, filters, gates, thresholds, factor weights, HC/EO rules,
  Alpha Focus rules, program verdicts, and candidate selection remain
  untouched.
- No new strategy family launches.
- Waiting for more 45d/60d evidence (§2) on the alpha-system verdict —
  the only place waiting is the correct response.
- **Measurement infrastructure built 2026-08-25, same day as this
  principle was set:** the Core-Satellite forward-shadow ledger (§6) and
  the live-board recall digest surfacing + false-flaw fix (§7). Neither
  touched scanner logic, scores, gates, candidate selection, or existing
  evidence files — both are additive diagnostic/bookkeeping layers.
- Research only — not a signal or recommendation.

# M1 Analyst-Action Overlay — do dated upgrades rank inside the momentum pool?

*Written 2026-09-07. Research memo — no production change. Harness:
`research/backtests/m1_analyst_actions.py`. Machine-readable artifacts (all
gitignored): `cache/research/m1_analyst_actions_*`,
`logs/m1_analyst_actions_latest.txt`.*

> **RESEARCH ONLY.** Replay evidence over one fixed historical window
> (2022-01-07 → 2025-12-31, 209 weekly scan dates, 124,996 pool rows). It is
> **not** live forward evidence, **not** backtest evidence for Phase 4B, and
> **not** a gate, threshold, score, or routing recommendation. No conclusion
> here earns `VALIDATED_EDGE` and none of it may be pooled with the live
> forward ledger.

---

## 0. The one-paragraph version

FMP's `/stable/analyst-estimates` is snapshot-only, so point-in-time EPS and
revenue **estimate revision breadth cannot be reconstructed from FMP at all**.
`/stable/grades` is the usable substitute: one row per analyst action, each
event-dated, with a `previousGrade → newGrade` pair. Tested inside the frozen
M1 12-1 momentum pool on complete coverage (3,640 of 3,640 tickers, 5,189
provider calls across two passes), analyst-action momentum is **an annotation,
not a ranker**. An upgrade in the trailing quarter is worth **+0.73pp** against
the pool at 60 days [0.41, 1.06] — but a *downgrade* is worth **+0.50pp**, and
both sit above the cohort no analyst touched (+0.03pp). What the feed marks is
**analyst attention, not analyst direction**: the informative event is that
somebody re-rated the name at all, and the sign of the re-rating adds little on
top of that. Three overlays
earned `USEFUL_EVENT_ANNOTATION`; **none** earned `PROMISING_CONVICTION_OVERLAY`;
every ticker-clustered CI straddles zero. Downgrades **fail** as an avoid
filter. M1 stays a wide, equal-weight, unordered source pool.

---

## 1. Why this endpoint, and only this endpoint

| Endpoint | Dated history? | Verdict |
|---|---|---|
| `/stable/analyst-estimates` | **No** — rows keyed by fiscal period, no as-of date | Cannot be replayed point-in-time. Numeric estimate revisions are unavailable from FMP. |
| `/stable/grades` | **Yes** — one row per action, own event date, `previousGrade → newGrade` | **Used here.** |
| `/stable/grades-historical` | Monthly aggregated rating counts | Not tested — coarser aggregation of the same events, with restatement risk. |
| `/stable/price-target-news` | Yes — event-dated, carries `priceWhenPosted` | Not tested — the recommended next step (§7). |
| `/stable/earnings-surprises-bulk` | — | HTTP 402, not in the subscription. |

Provider containment is in code, not intent: `APPROVED_ENDPOINT = "/grades"`,
`FORBIDDEN_ENDPOINTS` names the other four plus options paths, and
`assert_endpoint_allowed()` raises before any call. The fetch refuses to run
without `--execute-fetch` and aborts if the plan exceeds the operator's cap.

## 2. Point-in-time rules (enforced, not asserted)

* An action is usable only from the **first trading session strictly after**
  its event date — `/stable/grades` has no time-of-day field, so an action
  dated D is unknown on D.
* Trailing windows (7 / 30 / 63 / 126) are counted in **trading sessions** off
  SPY's own calendar; the search bound *is* the scan session, so nothing after
  the scan date can enter a feature by construction.
* `_assert_no_future_actions()` re-counts 400 sampled rows directly from the
  raw feed and fails the stage on any mismatch or any not-yet-usable action.
* Forward returns are labels only. No rule reads one — pinned by a test.
* Upgrades are derived from the **rating pair**, never the provider's `action`
  string, so the maintain/reiterate noise that is 86% of the feed cannot be
  counted as conviction. Provider-label agreement: 100%; unmapped grade
  strings: 0%.

**Unclosable risk, stated rather than assumed away:** the feed is served as it
stands today. Each row is an event with its own date, which is why this
endpoint was chosen, but a provider that back-fills or re-labels historical
grade rows would inject contamination no current pull can detect.

## 3. The coverage failure, and why it is in this memo

The first pass (2026-09-07, 6 workers) lost **1,546 of 3,640 tickers to HTTP
429** — the client's 750-call token burst was exhausted and FMP rejected the
rest. That left 56.3% coverage. A second, operator-approved pass
(`--only-failed --workers 1`, 1,546 calls, zero errors) completed it. The
outcome ledger after that pass reads **3,640 ok / 0 failed**, and the two passes
together cost **5,189 provider calls** on `/stable/grades` and nothing else.

**Completing the coverage roughly halved every effect in the study**, and two
readings from the partial run are formally withdrawn:

| Measure | 56% coverage | Complete |
|---|---|---|
| `positive_action_momentum` subset vs pool | +1.089pp | **+0.721pp** |
| `positive_action_momentum` top-50 vs random (train) | +0.833 | **+0.382** |
| `repeated_upgrades` top-50 vs random (train) | +1.845 | **+0.938** |
| `upgrade_into_strong_momentum` subset vs pool | +7.463pp | **+4.125pp** |
| `no_action_126` vs pool | −0.737pp | **+0.026pp** (withdrawn) |
| `upgrade_into_strong_momentum` left tail | −12.4% | **+13.2%** (withdrawn) |
| Verdicts | 4 annotations | **3 annotations** |

The partial pool was not a random half — it was whatever the rate limiter let
through — and it flattered every cohort. **A partial provider pass must be
labelled and re-measured, never read.** The pass-1 numbers are frozen in
`PARTIAL_PASS_REFERENCE` so the comparison is reproducible from the artifact
rather than from anyone's memory.

## 4. What the data says

Cohorts at 60d, complete coverage, against the whole M1 pool:

| Cohort | Rows | vs pool | CI |
|---|---|---|---|
| upgrade in 63 sessions | 21,313 | **+0.725pp** | [0.412, 1.055] |
| downgrade in 63 sessions | 17,755 | **+0.498pp** | [0.136, 0.818] |
| maintain-only in 63 sessions | 58,122 | −0.188pp | [−0.395, 0.009] |
| no action in 126 sessions | 23,284 | +0.026pp | [−0.459, 0.530] |
| EPS beat (last report) | 68,266 | −0.298pp | [−0.521, −0.082] |
| upgrade without a beat | 7,584 | +1.356pp | [0.622, 2.119] |
| upgrade *and* a beat | 13,729 | +0.156pp | [−0.250, 0.572] |
| top-momentum quintile, no upgrade | 21,481 | +0.176pp | [−0.545, 0.864] |
| top-momentum quintile **and** upgrade | 3,642 | **+4.001pp** | [2.803, 5.232] |

**The reading:** both rating-change cohorts — upgrades *and* downgrades —
outperform the cohort with no action at all, whose own interval covers zero.
Direction separates them barely and on overlapping intervals; the presence of a
change separates them from silence. That is what disqualifies net upgrades as an
ordering signal, and it is why `rank_by_net_upgrades` is +0.15 in train and
negative in both walk-forward steps.

The upgrade tag's value is in the **left tail**: −25% rate down 19.0%, +50%
rate down 9.8%, catastrophic-loser lift 0.67× at top-50.

## 5. Verdicts

`INCONCLUSIVE` × 10 · `USEFUL_EVENT_ANNOTATION` × 3 · `REJECTED_DATA_UNSAFE` × 5
· **`PROMISING_CONVICTION_OVERLAY` × 0**

Annotations: `positive_action_momentum`, `large_rating_improvement`,
`new_bullish_coverage`. The five refusals are coverage-floor refusals (< 10% of
pool rows), pre-registered before any number was seen.

`repeated_upgrades` is the closest miss and deserves its own line: +1.34pp vs
pool [0.62, 2.02], the **only** overlay whose top-50 train CI clears zero
(+0.938 [0.105, 1.719]), +1.524 in the spent 2025 holdout, positive in both
walk-forward steps, catastrophic-loser lift 0.4×. It covers **4.2%** of pool
rows — and completing the fetch moved that only from 4.4%, because the share is
a property of analyst behaviour, not of coverage. It now fills 24 names on the
median date (bar: 25). The floor refuses it, and lowering the floor after
seeing which cohorts it excludes would be the error this programme keeps
refusing to make.

`upgrade_into_strong_momentum` posts the largest numbers in the study
(+2.91 train, +6.37 holdout vs random) but on complete coverage it raises the
+50% rate 113% **and** the −25% rate 13% — it selects liveness, which is the
exact failure the tail test exists to catch.

## 6. Direct answers

1. **Explains missing M1 dispersion?** A little, non-directionally — a rating
   *change* is what separates cohorts, not its sign. A rounding error against
   the ~69–98pp of oracle headroom the missing-data audit measured.
2. **Upgrades vs earnings surprise?** Upgrades win (+0.73pp vs −0.30pp), and
   stacking them is worth less than either alone.
3. **Downgrades as an avoid filter?** **No — the reverse.** Downgraded names
   beat the pool, so dropping them leaves it 0.55pp worse and raises both tails.
4. **Repeated upgrades?** Best analyst-only cohort; refused on coverage.
5. **Improves top-25/top-50 ranking?** No on complete coverage. The whole-pool
   ranker is +0.15 in train and negative in both walk-forward steps.
6. **Improves per-name confidence?** No — every ticker-clustered CI straddles
   zero. The partial run's single positive (one horizon of five) did not survive.
7. **Overlay or annotation?** Annotation.

## 7. What this does and does not support

**Supported:** tagging an M1 name with "analysts have been raising their
ratings here" as a *risk* annotation on ~14% of the pool.

**Not supported:** using analyst actions to order, rank, score, tier or filter
an M1 list; deriving any trade signal from them; using a downgrade as an avoid
filter; reading the sub-floor cohorts (`repeated_upgrades`,
`upgrade_into_strong_momentum`) as validated; any production, gate, score,
routing, HC/EO, Alpha Focus, dashboard, MCP or cron change. Nothing in this
study produces an ordering, and no number in it should be turned into one.

**Next lead:** `/stable/price-target-news` — 86.0% of the grades feed is
maintains carrying no rating change, invisible to every feature built here, and
that cohort is flat. A dated price target with `priceWhenPosted` is the numeric
revision this feed cannot express and the closest available substitute for the
estimate-revision breadth FMP does not serve. Go in with a sub-1pp prior and
pre-registered bars. **Not** `grades-historical`.

**Standing decision unchanged:** M1 stays a wide, equal-weight, unordered
source pool (`M1_SYSTEM_DECISION_MEMO_2026_09.md`). Analyst actions annotate
it; they do not order it.

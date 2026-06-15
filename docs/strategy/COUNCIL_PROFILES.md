---
component: VetoCouncil — Per-Strategy Weight Profiles
code_path: council/veto_council.py
flag: COUNCIL_PROFILES_ENABLED (default false)
last_updated: 2026-04-25
---

# Council Profiles — Per-Strategy Tier-2 Weights

The VetoCouncil currently uses a single Tier-2 weight set (`_WEIGHTS`) for all
strategies. Both active scorecards flag this as the wrong default:

- `voyager_scorecard.md §Council Profile Gap` — Voyager needs higher
  RelativeStrength / FlowAgent / MomentumAgent weights (long-horizon
  accumulation thesis).
- `short_sleeve_scorecard.md §Council Profile Gap` — SHORT_A needs higher
  MomentumAgent (post-gap downtrend is the signal) and lower FlowAgent
  (intraday vol deceleration is noise around an event print).

This document is the **pre-declared profile spec**. The weights below bind
once the activation gate is met, so future tuning is evidence-based, not
retroactive.

References that take precedence:
- `STRATEGY_DOCTRINE.md §Direction Mandate` — direction rules per sleeve
- `MASTER_PLAN.md §Change Discipline` — pre-declared reason / rollback
- `voyager_scorecard.md` / `sniper_scorecard.md` / `short_sleeve_scorecard.md`

---

## Agents at a Glance

Tier 1 (hard veto — same for all strategies, not profile-tunable):
- **RegimeAgent** — VIX > 40 hard block; SPY 20d MA context
- **MacroAgent** — high-impact macro event blackout (±15/30 min)
- **PortfolioAgent** — daily loss cap, position limits, circuit breaker

Tier 2 (soft score — these are the profile-tunable weights):
- **SectorAgent** — sector PE vs market PE (currently neutral pass-through)
- **FlowAgent** — intraday 5m volume acceleration as institutional flow proxy
- **SentimentAgent** — FMP news sentiment 0.0–1.0, direction-flipped for SHORT
- **EarningsAgent** — 5-day forward earnings blackout (low score if conflict)
- **SpreadAgent** — bid-ask % from live Alpaca quote
- **MomentumAgent** — 20d return, direction-flipped for SHORT

All Tier 2 agents are already direction-aware. The profile change is purely
in the **weights** applied to their scores. Same agent code, different mix.

---

## Profile Definitions

### Default (status quo — unchanged)

Used when `COUNCIL_PROFILES_ENABLED=false` OR when `signal["strategy"]` does
not match a defined profile (e.g. legacy / unknown strategies).

| Agent | Weight |
|-------|--------|
| sector | 0.20 |
| flow | 0.25 |
| sentiment | 0.15 |
| earnings | 0.20 |
| spread | 0.10 |
| momentum | 0.10 |

Rationale: this is the historical mix. Preserved verbatim so default-off is a
true no-op for existing baseline tags.

---

### VOYAGER profile (long-horizon LONG, 6–18 month hold, accumulation)

| Agent | Weight | Δ vs default | Rationale |
|-------|--------|--------------|-----------|
| sector | 0.15 | −0.05 | Sector rotation matters at 6–18mo horizons but agent is currently a neutral pass-through, so weight has limited effect |
| flow | 0.25 | 0 | FlowAgent's intraday volume acceleration aligns with accumulation thesis — keep weight |
| sentiment | 0.10 | −0.05 | News sentiment dilutes over multi-month holds; less signal than at short horizon |
| earnings | 0.10 | −0.10 | 5-day forward earnings blackout is too tight a gate for a multi-month thesis — earnings noise is absorbed by the hold horizon |
| spread | 0.05 | −0.05 | Entry-cost sensitivity is low for multi-month holds; tight spreads matter less than for SNIPER |
| momentum | 0.35 | +0.25 | Voyager fires on positive 20d momentum + accumulation; MomentumAgent's positive-momentum bias is directionally core to the thesis |

**Sum: 1.00**. Tier-2 score still `≥ 50/100` to pass. Direction = LONG only.

**What the new profile changes operationally:**
- A Voyager candidate with weak intraday flow but strong 20d momentum and
  sector tailwind passes more easily under the new profile.
- A Voyager candidate with earnings within 5 days but otherwise strong
  multi-month accumulation passes more easily (lower earnings weight).
- A Voyager candidate with wide spread but strong momentum passes more easily
  (lower spread weight).

**Acknowledged blind spot:** Voyager would benefit most from a
RelativeStrengthAgent (RS vs SPY) with HIGH weight. That agent is not yet
built. Until built, MomentumAgent's 20d return serves as a partial proxy.

---

### SNIPER profile (short-horizon LONG, 1–10 day hold, momentum continuation)

| Agent | Weight | Δ vs default | Rationale |
|-------|--------|--------------|-----------|
| sector | 0.10 | −0.10 | Short hold horizon — sector rotation is too slow to matter |
| flow | 0.20 | −0.05 | Intraday flow confirmation matters but momentum dominates at this horizon |
| sentiment | 0.10 | −0.05 | Sentiment moves on news cycle; SNIPER is technical, not narrative |
| earnings | 0.20 | 0 | 5-day earnings blackout is tight enough to be material at 1–10d holds — keep |
| spread | 0.15 | +0.05 | Entry cost is meaningful at short hold; tight spreads protect 1.5×ATR stop geometry |
| momentum | 0.25 | +0.15 | Continuation is the thesis; positive 20d momentum is the core signal |

**Sum: 1.00**. Tier-2 score still `≥ 50/100` to pass. Direction = LONG only.

**What the new profile changes operationally:**
- A SNIPER candidate with poor sector context but strong momentum and tight
  spread passes more easily under the new profile.
- A SNIPER candidate with high spread is penalized harder (matches the
  geometry sensitivity at short hold).

**Note on activation timing:** SNIPER currently produces zero signals due to
the universe-overlap mismatch (90 universe candidates vs 46-name
LARGE_CAP_UNIVERSE whitelist — see Task 1 diagnostic in `CURRENT_READINESS.md`).
The SNIPER profile cannot be paper-validated until SNIPER signal flow is
restored. Profile activation should follow the SNIPER signal-flow fix, not
precede it.

---

### SHORT profile (event continuation SHORT, 5–10 day hold, post-earnings)

| Agent | Weight | Δ vs default | Rationale |
|-------|--------|--------------|-----------|
| sector | 0.05 | −0.15 | Event is idiosyncratic; sector context is noise around an earnings print |
| flow | 0.10 | −0.15 | Per scorecard: intraday flow deceleration is "less meaningful for event shorts" — the event itself is the flow shock |
| sentiment | 0.10 | −0.05 | Post-event sentiment is high-variance (analyst downgrades, retail panic) and adds noise |
| earnings | 0.20 | 0 | EarningsAgent already scores 80 if no upcoming earnings, which is correct for SHORT (we trigger on past, not upcoming) — keep |
| spread | 0.15 | +0.05 | Short execution is cost-sensitive (locate fees, borrow, slippage) — entry spread matters more than for default |
| momentum | 0.40 | +0.30 | Per scorecard: "negative 20d momentum → higher score. Post-gap-down tickers have this naturally." MomentumAgent's direction-aware logic gives SHORT signals high momentum scores when downtrend is confirmed — this is the core thesis |

**Sum: 1.00**. Tier-2 score still `≥ 50/100` to pass. Direction = SHORT only.

**What the new profile changes operationally:**
- A SHORT candidate with strong negative momentum and acceptable spread
  passes even when intraday volume looks ambiguous.
- A SHORT candidate where intraday vol shows accumulation (FlowAgent score
  drops for SHORT direction) is penalized less harshly — the agent score is
  still right, but its weight in the mix is reduced.

**Acknowledged blind spot:** SHORT_A would benefit from a BorrowAgent that
checks locate availability and borrow rate. Not yet built. Until built,
borrow-availability gate lives in execution layer (per
`SHORT_A_PROMOTION_CRITERIA.md §Stage 2 Capital geometry`).

---

## Activation Gate

Profile activation **changes paper sample behavior** and therefore requires
a baseline tag bump and sample reset for the affected sleeve, mirroring the
discipline used for the SHORT_A AMC fix (2026-04-25).

| Sleeve | Activation gate | Baseline tag bump |
|--------|----------------|-------------------|
| SHORT_A | Activate alongside the AMC-fix baseline reset (already in motion) | `SHORT_A_PAPER` → `SHORT_A_PAPER_v2_council` |
| VOYAGER | Defer activation until current Stage-1 paper sample either passes (≥30 with 30d closed) OR fails. Don't contaminate the in-flight sample | `VOYAGER_PAPER` → `VOYAGER_PAPER_v2_council` after current sample resolves |
| SNIPER | Defer activation until SNIPER signal flow is restored (Task 1 fix) and ≥10 candidates have been observed under the restored generator | `SNIPER_PAPER` → `SNIPER_PAPER_v2_council` after signal-flow restoration |

Each activation is a separate Change Discipline event. Each must be recorded
in the corresponding scorecard's Validation Status table with:
- date
- pre-declared metric expected to improve
- rollback condition (if metric regresses by N% over M days, fall back to
  default profile)

---

## Implementation Notes

The activation flag is `COUNCIL_PROFILES_ENABLED` (env-driven, default
`false`). When `false`, every signal — including VOYAGER / SNIPER / SHORT —
uses the historical `_WEIGHTS` mix. Behavior is byte-identical to the
pre-profile council. This makes the profile system safe to ship dormant.

When `true`, the council inspects `signal["strategy"]` and selects the
matching profile from the registry. Strategies without a defined profile
(e.g. frozen sleeves like REMORA / CONTRARIAN, if they ever produced live
signals again) fall back to the default mix.

The `MIN_SOFT_SCORE` threshold (50/100) is unchanged across all profiles.
Only the per-agent weights are profile-tuned.

**No agent code changes.** The direction-aware logic (LONG vs SHORT) inside
each agent's `_evaluate` is already correct for the profiles above. Adding
new agents (RelativeStrengthAgent for VOYAGER, BorrowAgent for SHORT_A) is
explicitly out of scope for this profile pass — those are separate Change
Discipline tickets.

---

## Out of Scope

- Building RelativeStrengthAgent (VOYAGER blind spot — separate ticket).
- Building BorrowAgent / locate validation (SHORT_A blind spot — for now,
  borrow check happens at pilot-entry per SHORT_A_PROMOTION_CRITERIA Stage 2).
- Profiles for frozen sleeves (REMORA, CONTRARIAN, SHORT_B, PATHFINDER) —
  these sleeves do not produce paper signals in the current phase.
- Tier-1 agent tuning (RegimeAgent, MacroAgent, PortfolioAgent thresholds).
  These remain platform-wide defaults.
- Council profile A/B framework. The activation pattern is sequential
  (default → profile, gated by paper sample maturity), not concurrent A/B.

---

## Rollback

If a profile activation produces a regression (per-strategy Stage-1
thresholds in the corresponding promotion criteria doc start trending the
wrong way after activation), rollback is a single-flag flip:

```
COUNCIL_PROFILES_ENABLED=false
```

This restores the default mix for all strategies in one cycle. Per
`MASTER_PLAN.md §Change Discipline`, rollback events are recorded with the
specific metric that regressed and the post-rollback baseline tag (which
reverts to the prior `_v1` tag).

# Short Detection Truth Audit — QQQ Breakdown / Tactical Short Regime Review

_Phase 1G.16 · research-only · generated 2026-06-16T05:50:03Z · as-of bar 2026-06-15_

> **Scope guard.** This audits the *short-detection layer* only. `SHORT_A` stays **FROZEN** (it failed structurally). No paper signals, no trade proposals, no execution/governance/live-capital/gate/Veto-Council changes, no new active short strategy. Proposed states are research labels.

## 1. Recent downside tape

| ETF | 1d | 3d | 5d | 10d |
|-----|----|----|----|-----|
| SPY | +1.76% | +4.05% | +2.11% | -0.49% |
| QQQ | +3.14% | +7.25% | +3.90% | +0.17% |
| IWM | +0.58% | +4.46% | +3.71% | +1.96% |
| SMH | +4.38% | +13.35% | +8.18% | +6.46% |
| XLK | +3.78% | +8.58% | +4.13% | -2.03% |
| VXX | -6.78% | -15.19% | -8.89% | -8.96% |

- **Move classification:** `ISOLATED` — indices roughly flat; any weakness is name-specific
- QQQ−SPY 5d relative: **1.789%**; tech−SPY 5d: **6.07%**; VXX 3d: **-15.18796992481204%**

## 2. What the Short Opportunity Radar said
- state **SHORTS_OFF**, score 25/100, suppressed_bull_tape=True, candidates=4
- suppressed because SPY>50d&200d: **True**; VIX just under 20.0: False; score uses QQQ/sector relative weakness: **False**
- The radar persists only a single _latest.json snapshot; there is no per-day archive, so 'what it said on each day of the drawdown' cannot be reconstructed historically. Phase 1G.16 begins a forward history spine (data/research/short_detection_history.jsonl) to close this gap.

## 3. Simple baseline detectors

| # | detector | triggered |
|---|----------|-----------|
| A | QQQ downside velocity | False |
| B | QQQ relative weakness vs SPY | False |
| C | Tech/semis breakdown | False |
| D | Momentum leader unwind | False |
| E | VXX stress confirmation | False |
| F | Tech breadth deterioration | True |

_1/6 simple baselines fired. These are research signals only — no trades. Compare against radar state._

## 4. Missed-short autopsy
- examined **192**, sharp 5d drops **14**, missed by radar **13**; by archetype: `{'RELATIVE_WEAKNESS_BREAKDOWN': 12, 'NO_SHORT_EDGE': 1, 'FAILED_LEADER': 1}`

| ticker | 5d | archetype | theme | radar saw |
|--------|----|-----------|-------|-----------|
| SMCI | -32.2% | RELATIVE_WEAKNESS_BREAKDOWN | hardware | False |
| ADBE | -16.7% | RELATIVE_WEAKNESS_BREAKDOWN | other | False |
| FJET | -13.9% | RELATIVE_WEAKNESS_BREAKDOWN | space_aerospace | False |
| ORCL | -12.7% | RELATIVE_WEAKNESS_BREAKDOWN | other | False |
| NOW | -10.4% | RELATIVE_WEAKNESS_BREAKDOWN | other | False |
| MX | -8.9% | RELATIVE_WEAKNESS_BREAKDOWN | semiconductors | False |
| CRM | -8.6% | RELATIVE_WEAKNESS_BREAKDOWN | other | False |
| QBTS | -7.7% | RELATIVE_WEAKNESS_BREAKDOWN | quantum | False |
| QCOM | -7.4% | RELATIVE_WEAKNESS_BREAKDOWN | semiconductors | False |
| TE | -7.1% | RELATIVE_WEAKNESS_BREAKDOWN | other | False |
| NTAP | -7.0% | NO_SHORT_EDGE | hardware | False |
| NAVN | -6.2% | FAILED_LEADER | other | False |
| MSFT | -5.7% | RELATIVE_WEAKNESS_BREAKDOWN | other | False |
| PLTR | -5.0% | RELATIVE_WEAKNESS_BREAKDOWN | other | False |

## 5. Suppression-rule diagnosis
- **Current rule:** `bull tape: SPY>50d & >200d, VIX<20 → short regime suppressed` → {'suppressed': True, 'state': 'SHORTS_OFF'}
- **Alternative (QQQ-aware):** QQQ-aware: SPY bull is the broad gate, but QQQ/tech tactical breakdown (rel-SPY, EMA20, velocity, tech breadth) or VXX stress escalates to HEDGE_WATCH / FAILED_LEADER_WATCH / TACTICAL_SHORT_RESEARCH → {'state': 'SHORTS_OFF', 'flags': {'bull_tape': True, 'qqq_breakdown': False, 'tech_breakdown': False, 'vxx_stress': False, 'failed_leaders': False}}
- **Rule too broad:** False · missed=13 · false-positive risk: n/a (proposed also says SHORTS_OFF)
- **Recommended (not implemented):**
  - keep rule — proposed QQQ-aware logic agrees with current state today

## 6. Proposed short-side research states
- **SHORTS_OFF** — Broad tape constructive; no short research except alerts.
- **HEDGE_WATCH** — SPY still okay but QQQ/tech/semis weakening; index-hedge research only, no stock shorts.
- **FAILED_LEADER_WATCH** — Prior winners breaking; research-only short candidates allowed.
- **TACTICAL_SHORT_RESEARCH** — QQQ/sector breakdown confirmed; generate research candidates only.
- **SHORT_REGIME_ACTIVE** — Only after backtested validation; NOT active now and never auto-set.

**State today:** `SHORTS_OFF` — broad tape constructive; QQQ/tech healthy

## 7–8. Redesign verdict / surfacing
- **Verdict:** `NO_GAP_TODAY` — Current radar state agrees with the QQQ-aware proposal today.
- Concerns DETECTION only. SHORT_A remains frozen; no active short sleeve proposed.
- Forward validation: `./scripts/run_research_cycle.sh short-detection-forward` (verdict ladder NEED_MORE_DATA → NO_VALUE → SHORT_DETECTION_EDGE → READY_FOR_SHORT_REDESIGN_RESEARCH).


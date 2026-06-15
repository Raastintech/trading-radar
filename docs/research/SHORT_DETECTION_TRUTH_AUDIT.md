# Short Detection Truth Audit — QQQ Breakdown / Tactical Short Regime Review

_Phase 1G.16 · research-only · generated 2026-06-13T00:38:03Z · as-of bar 2026-06-12_

> **Scope guard.** This audits the *short-detection layer* only. `SHORT_A` stays **FROZEN** (it failed structurally). No paper signals, no trade proposals, no execution/governance/live-capital/gate/Veto-Council changes, no new active short strategy. Proposed states are research labels.

## 1. Recent downside tape

| ETF | 1d | 3d | 5d | 10d |
|-----|----|----|----|-----|
| SPY | +0.50% | +0.59% | +0.52% | -1.99% |
| QQQ | +0.43% | +1.74% | +2.14% | -2.46% |
| IWM | +0.87% | +2.78% | +4.01% | +0.87% |
| SMH | +1.72% | +4.90% | +8.82% | +3.51% |
| XLK | +0.86% | +2.22% | +2.48% | -3.27% |
| VXX | -4.42% | -3.85% | -4.01% | +0.25% |

- **Move classification:** `ISOLATED` — indices roughly flat; any weakness is name-specific
- QQQ−SPY 5d relative: **1.618%**; tech−SPY 5d: **8.299%**; VXX 3d: **-3.853794199443794%**

## 2. What the Short Opportunity Radar said
- state **SHORTS_OFF**, score 15/100, suppressed_bull_tape=True, candidates=5
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
- examined **190**, sharp 5d drops **19**, missed by radar **17**; by archetype: `{'RELATIVE_WEAKNESS_BREAKDOWN': 16, 'NO_SHORT_EDGE': 2, 'FAILED_LEADER': 1}`

| ticker | 5d | archetype | theme | radar saw |
|--------|----|-----------|-------|-----------|
| SMCI | -28.4% | RELATIVE_WEAKNESS_BREAKDOWN | hardware | False |
| SAIL | -21.5% | RELATIVE_WEAKNESS_BREAKDOWN | other | False |
| FJET | -15.5% | RELATIVE_WEAKNESS_BREAKDOWN | space_aerospace | False |
| OLOX | -15.2% | RELATIVE_WEAKNESS_BREAKDOWN | other | False |
| ORCL | -13.5% | RELATIVE_WEAKNESS_BREAKDOWN | other | False |
| ADBE | -13.0% | RELATIVE_WEAKNESS_BREAKDOWN | other | False |
| TTAN | -11.8% | RELATIVE_WEAKNESS_BREAKDOWN | other | False |
| CRM | -10.1% | RELATIVE_WEAKNESS_BREAKDOWN | other | False |
| TE | -10.0% | RELATIVE_WEAKNESS_BREAKDOWN | other | False |
| GTLB | -10.0% | RELATIVE_WEAKNESS_BREAKDOWN | other | False |
| MX | -9.4% | RELATIVE_WEAKNESS_BREAKDOWN | semiconductors | False |
| NOW | -9.0% | RELATIVE_WEAKNESS_BREAKDOWN | other | False |
| SEDG | -8.2% | RELATIVE_WEAKNESS_BREAKDOWN | other | False |
| MSFT | -6.9% | RELATIVE_WEAKNESS_BREAKDOWN | other | False |
| QCOM | -6.7% | RELATIVE_WEAKNESS_BREAKDOWN | semiconductors | False |
| PD | -6.4% | NO_SHORT_EDGE | other | False |
| NAVN | -6.0% | FAILED_LEADER | other | False |
| NTAP | -5.2% | NO_SHORT_EDGE | hardware | False |
| IBM | -5.0% | RELATIVE_WEAKNESS_BREAKDOWN | other | False |

## 5. Suppression-rule diagnosis
- **Current rule:** `bull tape: SPY>50d & >200d, VIX<20 → short regime suppressed` → {'suppressed': True, 'state': 'SHORTS_OFF'}
- **Alternative (QQQ-aware):** QQQ-aware: SPY bull is the broad gate, but QQQ/tech tactical breakdown (rel-SPY, EMA20, velocity, tech breadth) or VXX stress escalates to HEDGE_WATCH / FAILED_LEADER_WATCH / TACTICAL_SHORT_RESEARCH → {'state': 'SHORTS_OFF', 'flags': {'bull_tape': True, 'qqq_breakdown': False, 'tech_breakdown': False, 'vxx_stress': False, 'failed_leaders': False}}
- **Rule too broad:** False · missed=17 · false-positive risk: n/a (proposed also says SHORTS_OFF)
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


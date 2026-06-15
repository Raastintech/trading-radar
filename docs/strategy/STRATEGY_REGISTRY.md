# Strategy Registry

**Status:** Operational source of truth  
**Last updated:** 2026-04-22

The current platform phase is paper validation and portfolio-governance
evidence collection. Strategy research is closed for this phase unless a new
doctrine-level hypothesis is explicitly approved.

Runtime source of truth:

- `core/strategy_registry.py`

## Active Paper Sleeves

| Sleeve | Status | Baseline tag | Runtime scanner | Paper ledger | Outcomes |
|---|---|---|---|---|---|
| VOYAGER | active_paper | `VOYAGER_PAPER` | `voyager` | Voyager legacy paper table | 30d / 90d / 180d via Voyager paper path |
| SNIPER v6 | active_paper | `SNIPER_V6` | `sniper` | unified `paper_signals` | 1d / 3d / 5d / 10d |
| SHORT Sleeve A | active_paper | `SHORT_A` | `short` | unified `paper_signals` | 3d / 5d / 10d |

Only these sleeves should appear as active/tradable opportunities in the
dashboard during this phase.

## Frozen / Research-Only Sleeves

| Sleeve | Status | Baseline tag | Operational state |
|---|---|---|---|
| REMORA | frozen | `REMORA_RESEARCH_ONLY` | hidden from active opportunity views |
| CONTRARIAN | frozen | `CONTRARIAN_RESEARCH_ONLY` | hidden from active opportunity views |
| SHORT Sleeve B | frozen | `SHORT_B_RESEARCH_ONLY` | not part of the paper engine |
| PATHFINDER | future_research | `PATHFINDER_V1` | paused; not operational |

Frozen sleeves may be shown only in a separate frozen/research-only status
section. They must not appear as active opportunities, active scanner panels, or
paper-governance candidates.

## Enforcement Points

- `main.py` instantiates and scans active paper scanners only.
- `dashboards/gem_trader_hq.py` manual scanner runs active paper scanners only
  and filters active opportunity/candidate views through the registry.
- `core/paper_validation.py` rejects paper ledger writes for non-active sleeves.
- `execution/paper_governance.py` accepts only active paper sleeves.
- `research/paper_trades/resolve_tactical_outcomes.py` resolves only active
  tactical paper tags.
- `research/paper_trades/paper_scoreboard.py` reports active paper sleeves
  separately from frozen/research-only sleeves.

## Guardrail

Do not change this registry to revive a frozen sleeve unless the platform phase
changes and the sleeve has passed the Quant Research Doctrine gates for that
new phase.

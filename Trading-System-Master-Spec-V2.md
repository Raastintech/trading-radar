# Trading System Master Spec v2

## 1. Executive Summary

### Mission

Build a disciplined, research-first trading platform that can eventually manage capital across multiple market regimes using only validated strategies, clean data, explicit risk controls, and auditable decision paths.

### Current Truth

* Infrastructure is real and usable.
* Data stack is much stronger after moving to Ubuntu + cache-first design.
* Research process is materially better than before.
* Long-side strategy concepts are defined but not yet fully validated.
* Short-side research has improved, but no short strategy is yet promotable.
* The system is not ready for unattended live trading.

### Production Data Policy

* **Alpaca**: primary market data + execution
* **FMP**: primary fundamentals, earnings/events, VIX, macro calendar, news context
* **yfinance / Alpha Vantage / FRED**: never in primary execution path; debug or research fallback only when explicitly allowed

### Deployment Doctrine

* Paper first
* Manual oversight before live automation
* No live promotion without strategy-specific validation
* Research and production code paths must remain separate

---

## 2. North Star and Design Principles

### North Star

Create a robust multi-strategy trading system that survives bad regimes, bad data, and operator mistakes.

### Core Principles

1. Alpha must be validated, not assumed.
2. Risk controls must exist before optimization.
3. Data lineage must be explicit for every decision.
4. Backtests must avoid lookahead, silent fallback, and hidden survivorship bias.
5. Execution automation must come after paper and shadow validation.

### Non-Negotiables

* Every trade has a stop, target, and invalidation thesis.
* Every strategy can be paused independently.
* Every strategy has its own validation report card.
* Every external data request is routed through a provider layer.
* Every production decision is logged in one canonical database path.

---

## 3. System State Snapshot

### What Is Green

* Ubuntu server migration
* systemd runtime
* Alpaca integration
* FMP integration
* cache-first architecture direction
* veto council framework
* production/research split becoming clearer

### What Is Amber

* multi-strategy validation status
* short-strategy edge quality
* event labeling quality
* point-in-time fundamentals for some research paths
* portfolio-level coordination across all strategies

### What Is Red

* full live readiness
* unattended auto-trading
* validated short book
* options overlay readiness

---

## 4. Strategy Book (Organized)

### 4.1 Long Book

#### SNP — Sniper

**Role:** momentum breakout long

**Thesis:** strong breakouts with volume, RS, and sector confirmation outperform when market conditions are supportive.

**Needed before promotion:**

* breakout quality score
* BTE validation
* false-breakout study
* post-entry slippage / gap audit

#### REM — Remora

**Role:** stealth accumulation long

**Thesis:** quiet institutional accumulation in underfollowed names can precede large directional moves.

**Needed before promotion:**

* accumulation proxy validation
* macro contamination audit
* stop-width optimization
* sector and liquidity controls

#### CON — Contrarian

**Role:** fear-regime snap-back long

**Thesis:** quality names sold indiscriminately during fear spikes often mean revert quickly.

**Needed before promotion:**

* VIX gating fully wired
* “no company-specific bad news” verification layer
* rebound timing study
* crisis-regime veto hardening

### 4.2 Short Book

#### VOY — Voyager

**Role:** event-confirmed mean-reversion short

**Thesis:** overextended names with earnings disappointment and sell-side deterioration can revert back toward mean.

**Needed before promotion:**

* verified downgrade/event plumbing
* short borrow realism
* drawdown / squeeze containment
* 30-paper-trade telemetry

#### SLV — Short Sleeve

**Role:** earnings disappointment short / event short

**Thesis:** negative earnings or bad-news continuation can offer fast short windows with tight holding periods.

**Current truth:**

* previous event research had data and labeling flaws
* event-validity is now stronger than before
* still not validated as deployable alpha

**Needed before promotion:**

* verified event coverage at scale
* session alignment certainty
* enough trade count
* robust expectancy after friction

---

## 5. Strategic Recommendation: Convert From 5 Isolated Strategies to 3 Books

### Book A — Long Trend / Momentum Book

Contains:

* SNP
  n- REM

### Book B — Long Mean-Reversion / Panic-Rebound Book

Contains:

* CON

### Book C — Short Tactical Book

Contains:

* VOY
* SLV
* future price-structure short sleeves

### Why this is better

* cleaner portfolio-level governance
* easier conflict resolution
* simpler risk budgeting
* more honest validation by book and by sleeve

---

## 6. Architecture (Target Production Form)

## Runtime Layout

* `main.py`: orchestration and scheduling
* `core/config.py`: environment and feature flags
* `core/fmp_client.py`: fundamentals/events/macro/news provider
* `core/alpaca_client.py`: market data + order execution
* `core/data_gatekeeper.py`: cache policy, freshness, lineage, budgeting
* `council/veto_council.py`: hard/soft vetoes
* `strategies/`: production strategy implementations only
* `research/`: all experimental and backtest-only logic
* `db/`: canonical operational + decision + trade database
* `dashboards/`: operator UI, risk monitor, scanner terminal

### Hard Separation Rule

Production strategy code must not import legacy research modules directly.

---

## 7. Validation Framework

### Promotion Ladder

1. Research hypothesis
2. Historical backtest
3. Out-of-sample backtest
4. Paper trading
5. Shadow mode / dashboard-only recommendation
6. Limited live capital
7. Full live capital

### Promotion Minimums

A strategy or sleeve should not move forward unless it has:

* meaningful sample size
* positive expectancy after friction
* acceptable max drawdown
* stable behavior across universe sizes and windows
* no unresolved data-quality contamination
* clear invalidation conditions

### Book-Level Validation

Do not validate only at strategy level.
Validate:

* each sleeve
* each book
* combined portfolio

### Strategy Activation Policy

The system maintains five strategies intentionally to cover different market regimes and holding periods:

| Code | Strategy | Regime Focus |
|------|----------|--------------|
| SHT | SHORT | short-term event-driven / fast tactical short |
| SNP | SNIPER | momentum breakout long |
| VOY | VOYAGER | mean-reversion short |
| REM | REMORA | stealth accumulation long |
| CON | CONTRARIAN | fear-regime snap-back long |

**Existence is not activation.**
The five-strategy framework is the doctrinal market-coverage model. Only validated strategies may become active. All others remain frozen.

**One strategy family enters deep validation at a time.**
While one strategy is in active validation, all remaining strategies are frozen. No parallel promotion paths.

**A strategy must clear three gates before receiving capital:**

1. `data-valid` — data sourcing, lineage, and point-in-time integrity confirmed
2. `backtest-valid` — positive expectancy after friction, sufficient sample, no lookahead
3. `paper-valid` — live paper results match backtest characteristics within acceptable tolerance

**Failure is not optional to ignore.**
A strategy that fails any gate must be archived, replaced, or redesigned. It may not remain nominally active because it exists in the doctrine.

**Options are secondary execution vehicles.**
An options long or short overlay may only be activated after the corresponding equity strategy (long or short) is proven profitable in paper trading. Options are not primary signal engines.

**Current Validation Order:**

1. SHORT (SHT)
2. SNIPER (SNP)
3. VOYAGER (VOY)
4. REMORA (REM)
5. CONTRARIAN (CON)

This order is operational. It may be revised if a later strategy demonstrates clear edge sooner than expected. However, no strategy may skip the validation ladder — every strategy must clear all three gates regardless of position in the queue.

---

## 8. Major Flaws / Risks To Fix

### 8.1 Biggest Design Flaw

The document mixes:

* mission
* production architecture
* research notes
* historical findings
* operational commands
* future ideas

This makes it harder to know what is:

* live
* frozen
* research-only
* archived
* deprecated

### 8.2 Strategy-Level Flaw

The short side is still under-validated and too dependent on event precision.

### 8.3 Data-Level Flaw

Some research paths still risk point-in-time contamination or weak event alignment unless explicitly hardened.

### 8.4 Portfolio-Level Flaw

Global portfolio rules are still too thin relative to the ambition of a 5-strategy autonomous system.
You need:

* book-level exposure caps
* long/short gross and net caps
* sector caps by book
* single-name heat limits
* strategy kill switches

### 8.5 Research Governance Flaw

The document still allows too much “idea creep.”
Research should be governed by:

* one active hypothesis queue
* one archive
* one scorecard per strategy

---

## 9. Simplification Recommendations

### Keep

* Alpaca + FMP core stack
* Ubuntu cache-first setup
* veto council
* paper-first discipline
* BTE as advisory
* options as deferred

### Simplify

* remove legacy scanner emphasis
* stop mixing archived short theses with active doctrine
* reduce current active focus to:

  * SNP
  * CON
  * one short event sleeve
  * one short price-structure sleeve

### Defer

* full options overlay
* Claude narrative layer for every name by default
* too many dashboard variants

---

## 10. Dashboard / Terminal Recommendation

### Operator Dashboard v1

Must answer in one screen:

* current regime
* active books and sleeves
* candidate queue
* veto reasons
* open positions
* daily risk usage
* FMP / Alpaca health
* cache freshness
* strategy validation status

### Research Terminal v1

Must support:

* quick ticker search
* event / earnings view
* fundamentals snapshot
* price structure panel
* trade replay / annotation
* backtest result comparison
* strategy sleeve attribution

### Claude Layer

Use Claude only for:

* post-scan narrative synthesis
* earnings-call / filing summary
* anomaly explanation
* red-flag memo

Do not make Claude part of the core signal path.

---

## 11. What Should Happen Next

### Immediate

1. Freeze the document structure in this v2 format.
2. Move archived short research out of active doctrine.
3. Run sleeve-by-sleeve validation, not only combined runs.
4. Build a proper cache-only validation mode.
5. Add book-level exposure constraints.

### Next 2 Weeks

1. Validate SNP and CON independently.
2. Validate VOY and SLV independently.
3. Build one honest price-structure short sleeve separate from event short.
4. Create operator dashboard v1.
5. Add data lineage tags to every decision.

### Before Any Live Automation

1. strategy-specific paper validation complete
2. combined portfolio paper validation complete
3. daily and weekly kill switches tested
4. recovery procedures documented
5. data outage behavior tested

---

## 12. Final Executive Verdict

### Does the system make sense?

Yes — as a framework.

### Is it flawless?

No.

### Main strength

The architecture is becoming professional: good data sources, server migration, cache-first thinking, strategy separation, and explicit risk discipline.

### Main weakness

Research quality and validation discipline still lag behind the ambition of full autonomy.

### Best path

Do not chase complexity. Tighten the system around:

* fewer active hypotheses
* cleaner validation
* cleaner data lineage
* better dashboard visibility
* book-level risk control

### Chief-strategist conclusion

The system should become a **multi-book, research-first, paper-validated platform** before it becomes an autonomous live trading machine.

---

## 13. Production-Ready File Split

### A. Master Spec

**File:** `docs/master_spec.md`

**Purpose:**
Single source of truth for mission, architecture, books, validation ladder, and non-negotiables.

**Should contain:**

* mission and north star
* design principles
* data-source doctrine
* book/strategy map
* promotion ladder
* global risk principles
* current system-state snapshot

**Should NOT contain:**

* shell commands
* research experiment logs
* archived backtest details
* server IPs / secrets / operational snippets

### B. Production Runbook

**File:** `docs/production_runbook.md`

**Purpose:**
How to operate the live or paper system safely.

**Should contain:**

* server layout
* systemd commands
* deployment steps
* environment variable reference
* backup / restore steps
* incident response checklist
* provider outage behavior
* paper vs live mode rules

### C. Research Archive

**File:** `docs/research_archive.md`

**Purpose:**
Historical memory of tested ideas, failures, fixes, and archived theses.

**Should contain:**

* archived short theses
* backtest findings
* sign-flip notes
* failed assumptions
* data-quality incidents
* retired modules / deprecated logic

**Rule:**
Anything not currently active in production or active validation goes here.

### D. Strategy Scorecards

**Folder:** `docs/scorecards/`

Create one file per strategy:

* `sniper_scorecard.md`
* `remora_scorecard.md`
* `contrarian_scorecard.md`
* `voyager_scorecard.md`
* `short_sleeve_scorecard.md`

**Each scorecard should contain:**

* mandate
* current thesis
* required data
* active code path
* validation status
* last backtest result
* last paper-trade result
* promotion blockers
* next experiment
* owner

### E. Dashboard Spec

**File:** `docs/dashboard_spec.md`

**Purpose:**
Define what must appear in the operator dashboard and research terminal before UI work expands.

### F. Database Spec

**File:** `docs/database_schema.md`

**Purpose:**
Canonical description of DB tables, write paths, lineage fields, and retention policy.

---

## 14. Recommended Repository Layout

```text
/home/gem/trading-production/
├── main.py
├── requirements.txt
├── gem-trader.service
├── docs/
│   ├── master_spec.md
│   ├── production_runbook.md
│   ├── research_archive.md
│   ├── dashboard_spec.md
│   ├── database_schema.md
│   └── scorecards/
│       ├── sniper_scorecard.md
│       ├── remora_scorecard.md
│       ├── contrarian_scorecard.md
│       ├── voyager_scorecard.md
│       └── short_sleeve_scorecard.md
├── core/
│   ├── config.py
│   ├── alpaca_client.py
│   ├── fmp_client.py
│   ├── data_gatekeeper.py
│   ├── provider_router.py
│   ├── market_regime.py
│   ├── macro_calendar.py
│   └── health_monitor.py
├── council/
│   ├── veto_council.py
│   ├── hard_vetoes.py
│   └── soft_scores.py
├── strategies/
│   ├── sniper.py
│   ├── remora.py
│   ├── contrarian.py
│   ├── voyager.py
│   ├── short_sleeve.py
│   └── shared/
│       ├── risk_geometry.py
│       ├── position_sizing.py
│       ├── signal_types.py
│       └── execution_rules.py
├── execution/
│   ├── order_router.py
│   ├── order_guard.py
│   ├── portfolio_risk.py
│   ├── circuit_breakers.py
│   └── trade_manager.py
├── research/
│   ├── backtests/
│   ├── event_studies/
│   ├── sleeves/
│   ├── notebooks/
│   └── archived/
├── dashboards/
│   ├── operator_dashboard/
│   └── research_terminal/
├── db/
│   ├── trading_performance.db
│   ├── migrations/
│   └── seeds/
├── cache/
│   ├── prices/
│   ├── fundamentals/
│   ├── earnings_events/
│   ├── macro/
│   └── metadata/
├── logs/
├── scripts/
│   ├── deploy.sh
│   ├── run_backtest.sh
│   ├── refresh_cache.sh
│   └── validate_strategy.sh
└── tests/
    ├── unit/
    ├── integration/
    └── smoke/
```

---

## 15. Exact Separation Rules

### Production Code

Lives in:

* `core/`
* `council/`
* `strategies/`
* `execution/`

**Rule:** must be import-safe, deterministic, test-covered, and free of experimental notebooks or archived code.

### Research Code

Lives in:

* `research/`

**Rule:** may experiment, but cannot be called directly from `main.py` or production strategy modules.

### Legacy Code

Current `legacy/` folder should be split into:

* active research modules moved into `research/`
* deprecated modules moved into `research/archived/`
* shared stable code moved into `core/` or `strategies/shared/`

### Database Writes

All writes must go through one canonical layer, such as:

* `execution/decision_logger.py`

No direct ad hoc writes from strategy modules.

---

## 16. Production-Ready Table of Contents

### 1. Executive Summary

### 2. Mission, Principles, and Non-Negotiables

### 3. Current System State

### 4. Book Structure and Strategy Mandates

### 5. Data Architecture and Source-of-Truth Policy

### 6. Runtime Architecture

### 7. Portfolio Risk Framework

### 8. Validation Ladder and Promotion Rules

### 9. Strategy Scorecard Index

### 10. Dashboard and Monitoring Spec

### 11. Operations Runbook

### 12. Research Archive Policy

### 13. Change Control and Release Discipline

### 14. Appendices

---

## 17. Recommended Immediate File Moves

### Move to `docs/`

* mission / doctrine / roadmap / operations text from the current markdown
* trade-log reference
* server commands
* environment variable reference

### Move to `research/archived/`

* crowded_loser_unwind research files
* short_ranked_portfolio_v2 research files
* obsolete yfinance-driven event experiments

### Move to `research/sleeves/`

* active short sleeve experiments
* multi-sleeve short research runner
* verified event study scripts

### Move to `core/`

Only stable provider and cache code:

* `research_data_provider.py` pieces that are now production-safe
* FMP / Alpaca routing logic
* cache budgeting and freshness logic

### Move to `strategies/shared/`

* ATR / risk-geometry helpers
* shared ranking / veto utilities that are stable and strategy-agnostic

---

## 18. Immediate Cleanup Checklist

1. Remove dependence on `legacy/` from any production runtime path.
2. Create `docs/scorecards/` and write one page per strategy.
3. Create `research/archived/` and move non-promotable short work there.
4. Create `execution/` and centralize order/risk/circuit-breaker code.
5. Split operational commands and environment notes out of the master spec.
6. Add data-lineage fields to every decision record.
7. Add a formal `BOOK_STATUS` view to the dashboard.
8. Make `main.py` run only production-safe modules.

---

## 19. Final Structural Recommendation

The system should be run as:

* **one master spec**
* **one production runbook**
* **one research archive**
* **five strategy scorecards**
* **one dashboard spec**
* **one database spec**

That structure is much easier to operate, validate, and grow than one giant project review document.

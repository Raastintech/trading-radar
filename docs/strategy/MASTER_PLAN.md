# Master Plan

> Mission / roadmap document
>
> Not current active-sleeve operational truth.
>
> For current active-phase status and doctrine/source ordering, see:
> - `docs/strategy/CURRENT_DOCTRINE_MAP.md`
> - `docs/strategy/CURRENT_READINESS.md`

This roadmap is the execution layer for the North Star.
If this file conflicts with `PROJECT_NORTH_STAR.md`, North Star wins.
If this file conflicts with `STRATEGY_DOCTRINE.md` on strategy mandate or edge
claim, the doctrine wins.

## Objective

Build the best retail-first trading intelligence platform in our category by:

- aligning with institutional flow when it is valid
- exploiting institutional constraints and inefficiencies when edge appears
- giving retail traders institutional-grade context without institutional complexity

## Strategic Pillars

1. Edge Engine
- Combine price action, market structure, options flow, whale activity, and macro regime.
- Score setups by edge quality, not signal quantity.
- Maintain multi-strategy coverage (VOYAGER, SNIPER, REMORA, SHORT, CONTRARIAN).
- Keep each strategy role distinct; do not allow one sleeve to become a weak
  copy of another.

2. Decision Assist
- Convert model output into actionable trade guidance: thesis, invalidation, target logic, and confidence.
- Surface "why now" and "what breaks this trade" on every signal.
- Keep the interface fast, direct, and explainable for retail users.

3. Execution And Risk Guidance
- Build for real execution constraints: slippage, liquidity, spreads, halts, and gap risk.
- Enforce portfolio-level controls (concentration, beta clustering, correlation stress).
- Prioritize downside containment before upside optimization.

4. Retail Empowerment
- Focus on making users better traders, not passive copy-traders.
- Add guided workflows, post-trade review, and feedback loops.
- Keep language practical: assist decisions, never promise outcomes.

5. Learning Loop
- Track signal quality, execution quality, and outcome quality separately.
- Feed diagnostics back into threshold tuning, ranking logic, and playbook updates.
- Ship small measurable improvements weekly.

## Phased Roadmap

## Phase 1: Foundation Stabilization

- Canonical runtime and scanner reliability.
- Diagnostic coverage for reject funnels, RR gaps, and execution drift.
- Logging and schema quality for full traceability.

Exit criteria:
- Stable daily runtime.
- Full decision-to-trade attribution path.
- No blind spots in strategy-level reporting.

## Phase 2: Edge Expansion

- Deeper institutional flow and whale-tracking integration.
- Better inefficiency detection around crowding, forced moves, and regime transitions.
- Higher quality calibration of stop/target and strategy-specific RR distributions.

Exit criteria:
- Improved opportunity quality and reduced false positives.
- Clear evidence that signals adapt across regimes.

## Phase 3: Outlier Product Experience

- Best-in-class retail signal presentation and trade guidance UX.
- Personalized trader coaching loop from historical behavior and mistakes.
- Productized workflow that feels institutional in capability but retail in usability.

Exit criteria:
- Users can execute faster and with better discipline.
- Measurable improvement in risk-adjusted user outcomes over baseline behavior.

## Operating Rules

- Do not add complexity without measurable edge.
- Do not ship black-box outputs without explainability.
- Do not market certainty; market probabilistic edge and process quality.
- Keep all strategy and product decisions aligned to retail trader advantage.
- Do not add "institutional-style" features unless they improve actual retail
  decision quality, selectivity, or execution outcomes.
- Do not keep weak strategies live for portfolio aesthetics; every sleeve must
  justify its place with forward evidence.

## Production Readiness Standard

Use `CURRENT_READINESS.md` as the current-state readiness source of truth.

Interpret completion in layers:

1. runtime and safety
2. telemetry and attribution
3. strategy quality
4. capital-promotion readiness

Do not use blanket language such as "complete" or "A+" unless all four layers
are green.

## Change Discipline

For production-affecting work:

- runtime and security fixes may proceed immediately when they remove a real
  defect
- telemetry fixes may proceed immediately when they remove a real blind spot
- strategy threshold or gate changes require:
  - a pre-declared reason
  - the metric expected to improve
  - a rollback condition
  - a documentation update

Do not base strategy changes on legacy imported rows, synthetic trades, or
analytics-excluded test trades.

## Messaging And Compliance Guardrails

- Positioning: "retail intelligence and execution-assist platform."
- Avoid regulated-advisor framing or language implying managed accounts.
- Explicitly state users control execution and remain responsible for decisions.
- Do not claim guaranteed returns or guaranteed profitability.

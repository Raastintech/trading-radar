# SniperTradingAI — Phase 5 Implementation Prompt

## Project Context

SniperTradingAI is a multi-strategy equity trading platform for independent retail traders.
Working directory: `/Users/hraastin/Desktop/SniperTradingAI`
Primary DB: `trading_performance.db` (SQLite)

**Core strategies:**
- `SNIPER` — tactical momentum / breakout. Scanner: `sniper_scanner_v2.py`. Live RR threshold: **2.5**
- `SHORT` — deterioration before institutional repricing. Scanner: `short_scanner_v1.py`. Live RR threshold: **4.0**
- `VOYAGER` — institutional accumulation / growth leadership. RR threshold: **2.0**
- `REMORA` — short-horizon stealth accumulation / quiet flow. RR threshold: **2.0** (score threshold: **70**)
- `CONTRARIAN` — panic / forced-selling / volatility overshoot. Scanner: `contrarian_scanner.py`

**Key files:**
- `unified_master_trader_v3.py` — main execution engine, `_log_scanner_rejects()` at line ~519
- `decision_logger.py` — writes to `decisions` table, `log_decision()` accepts `options_pcr`, `options_gamma` kwargs
- `options_intelligence.py` — `get_options_score_adj(ticker)` returns `{adj, pcr, gamma, source, note}`
- `tradier_options_feed.py` — real-time Tradier feed, already wired into `options_intelligence._get_chain()`
- `scan_diagnostics.py` — `ScanDiagnosticsEngine`, `run_diagnostics()`
- `pilot_policy_controller.py` — `PilotPolicyController`, pilot framework (Phase 4.3)
- `report_scan_quality.py` — CLI diagnostics report
- `live_dashboard_v3.py` — V3 operator dashboard, class `V3DashboardPro`
- `command_center_terminal_v3.py` — retail terminal

**What has been built (Phases 4–4.3):**
- Full scan quality diagnostics loop (funnel, gate attribution, near-miss, stability, suggestions)
- RR shadow calibration (threshold ladders, distribution percentiles, paper_shadow_policy)
- Shadow outcome tracker (seeds paper candidates, computes MFE/MAE/hit_target/hit_stop via yfinance)
- Pilot framework (PilotPolicyController, pilot_events table, PILOT_ENABLE env flag, exposure caps)
- All tests pass (87 tests total across test_scan_diagnostics.py, test_rr_shadow_calibration.py, test_phase42.py, test_phase43.py)

---

## Phase 5: Fix Six Confirmed Structural Issues

These are not hypothetical improvements. Each issue is confirmed by DB data or code inspection.
All changes must be **additive and safe**. No live threshold values may be changed.
All schema changes must be idempotent. All imports must use try/except guards.

---

### Fix 1 — options_pcr / options_gamma not reaching the decisions table

**Root cause (confirmed):**
`_log_scanner_rejects()` in `unified_master_trader_v3.py` calls `self.decision_logger.log_decision()`
but does NOT pass `options_pcr` or `options_gamma`, even though:
- `sniper_scanner_v2.py` puts `options_pcr` and `options_gamma` into every reject dict (lines 238–239)
- `decision_logger.log_decision()` accepts `options_pcr` and `options_gamma` kwargs (lines 253–254)
- The column exists in the `decisions` table

Result: 8,329 decisions in 7 days, **zero with options_pcr populated**.

**Fix required:**
In `unified_master_trader_v3.py`, `_log_scanner_rejects()`, extract `options_pcr` and `options_gamma`
from each reject row `r` and pass them to `log_decision()`:

```python
self.decision_logger.log_decision(
    ...
    options_pcr=r.get('options_pcr'),
    options_gamma=r.get('options_gamma'),
    notes=json.dumps({...}),
)
```

Also ensure `short_scanner_v1.py` and `remora_scanner_v2.py` include `options_pcr`/`options_gamma`
in their reject dicts (sniper already does; add it to short and remora reject dicts using the same
`_get_options_score_adj` call pattern that sniper uses — already imported in sniper).

---

### Fix 2 — SHORT RR threshold (4.0) structurally unreachable

**Root cause (confirmed by DB):**
```
Max SHORT RR in last 7 days: 3.77
Live threshold: 4.0
SHORT approvals in last 14 days: 0
```

Investigate `short_scanner_v1.py` stop/target calculation. The RR formula is:
```python
calculated_rr = ((entry - target) / (stop - entry))  # line 58
```
The target for SHORT positions is too close to entry OR the stop is too far, resulting in RR that
can never reach 4.0 for any real candidate.

**Fix required:**
1. Read `short_scanner_v1.py` fully. Find where `target` and `stop` are calculated for SHORT setups.
2. Add a diagnostic comment block showing the current logic and why max RR = 3.77.
3. Add a configurable `SHORT_TARGET_MULTIPLIER` env var (default: current behavior) and
   `SHORT_STOP_MULTIPLIER` env var (default: current behavior). These must NOT change live behavior
   unless explicitly set. Add them only as levers, not changes.
4. In `scan_diagnostics.py`, add to `_generate_suggestions()`: if SHORT max_rr < live_threshold
   and SHORT reject count > 100, surface suggestion:
   `"SHORT RR ceiling ({max_rr:.2f}) is below live threshold ({threshold:.1f}) — stop/target calibration needed"`

---

### Fix 3 — REMORA RR hardcoded at 3.0

**Root cause (confirmed by DB):**
```sql
SELECT rr, COUNT(*) FROM decisions
WHERE strategy='REMORA' AND position_opened=0
GROUP BY rr ORDER BY COUNT(*) DESC;
-- Returns: 3.0 | 470+ rows
```
Every single REMORA reject has `rr = 3.0` exactly. This is a constant, not a distribution.

**Fix required:**
1. Read `remora_scanner_v2.py` fully. Find where `rr` is assigned to the reject dict.
2. If RR is hardcoded (e.g., `'rr': 3.0`) or uses a fixed formula that always produces 3.0,
   document it in a comment and add dynamic calculation based on actual entry/stop/target:
   ```python
   _r_rr = round((target - entry) / (entry - stop), 2) if (entry and stop and target and entry > stop) else 3.0
   reject_dict['rr'] = _r_rr
   ```
3. Also add `options_pcr` and `options_gamma` to REMORA reject dicts (same pattern as Fix 1).
4. REMORA score threshold is 70 vs 60 for all other strategies. Add a note in `scan_diagnostics.py`
   `_generate_suggestions()`: if REMORA rejection rate is 100% and dominant reason is
   `score_below_threshold`, surface:
   `"REMORA score threshold (70) is tighter than all other strategies (60) — review for quiet-flow calibration"`

---

### Fix 4 — Bracket order gap protection (EOSE −49% failure)

**Root cause (confirmed by DB):**
```
EOSE: entry=11.64, stop=10.48, exit=5.90, reason=MARKET_EXIT, pnl=−49.3%
```
The position dropped from $11.64 to $5.90 without the stop triggering. Stop was at $10.48.
Exit was `MARKET_EXIT` not `STOP_HIT`. The stock gapped or halted through the stop level.
The system monitors for stop and sends a market order when breached — but gaps bypass monitoring.

**Fix required:**
In `smart_order_executor.py` (or wherever bracket/OCO orders are submitted), add a
`MAX_LOSS_CIRCUIT_BREAKER` safety:

1. Add env var `CIRCUIT_BREAKER_MAX_LOSS_PCT` (default: `0.25` = 25%). Any open position
   losing more than this triggers an immediate market exit regardless of stop status.

2. In the position monitor loop (wherever live positions are polled), add:
   ```python
   if current_loss_pct > MAX_LOSS_PCT:
       logger.warning(f"[CIRCUIT_BREAKER] {ticker} loss {current_loss_pct:.1%} exceeds max. Force exit.")
       self._submit_market_exit(ticker, reason="CIRCUIT_BREAKER")
   ```

3. Add `circuit_breaker_triggered` column to trades table (idempotent ALTER):
   ```sql
   ALTER TABLE trades ADD COLUMN circuit_breaker_triggered INTEGER DEFAULT 0;
   ```

4. Add a new exit_reason value `"CIRCUIT_BREAKER"` so it's distinguishable in analytics.

5. Write tests in a new `test_phase5.py`:
   - circuit breaker fires at default 25% loss
   - circuit breaker does NOT fire at 24% loss
   - circuit breaker env override works

---

### Fix 5 — Contrarian scanner completely absent from scan data

**Root cause (confirmed by DB):**
```sql
SELECT strategy, COUNT(*) FROM decisions
WHERE date(timestamp) >= date('now', '-14 days')
GROUP BY strategy;
-- Returns: SNIPER, SHORT, VOYAGER, REMORA, NULL — no CONTRARIAN rows
```

**Fix required:**
1. Read `contrarian_scanner.py` and `unified_master_trader_v3.py` to understand whether
   Contrarian is instantiated but silently failing, or simply not being called.

2. If Contrarian scanner exists but is not wired into `unified_master_trader_v3.py`'s scan loop,
   wire it in — same pattern as SNIPER/REMORA scanners. Use the same `_log_scanner_rejects()`
   and `_extract_*` pattern. Import-guard it: if import fails, log once and skip.

3. Add a regime-aware activation condition: Contrarian should only scan when:
   ```python
   regime_vix in ('HIGH', 'EXTREME') OR regime_overall in ('BEAR', 'CORRECTION')
   ```
   When regime doesn't qualify, log: `"[CONTRARIAN] Regime not eligible — skipping scan (regime={regime_overall}, vix={regime_vix})"`

4. Add `contrarian_eligible_runs` and `contrarian_scanned_runs` to `scan_diagnostics.py`
   `_get_reject_funnel()` so it's visible in reports.

5. In `pilot_policy_controller.py` `PILOT_POLICIES`, add a placeholder for future Contrarian pilot:
   ```python
   # Future: "CONTRARIAN_1_5": {"strategy": "CONTRARIAN", "shadow_threshold": 1.5, ...}
   ```
   (comment only, no active policy yet)

---

### Fix 6 — Execution RR (1.5:1) vs scanner threshold (2.5:1) disconnect

**Root cause (confirmed by DB):**
```sql
SELECT ticker, ROUND((target_price-entry_price)/(entry_price-stop_loss),2) as actual_rr
FROM trades WHERE exit_date IS NOT NULL;
-- ALL rows return: 1.5
```
Every executed trade has a realized planned RR of exactly 1.5, despite the SNIPER scanner
filtering for candidates with RR ≥ 2.5. The scanner's RR is calculated at scan time on
technical structure. The execution engine calculates entry/stop/target using a different
formula (likely ATR or %-based) that consistently produces 1.5.

**Fix required:**
1. Read the execution path: find where `entry_price`, `stop_loss`, `target_price` are set
   at execution time in `unified_master_trader_v3.py` or `smart_order_executor.py`.

2. Add a `scanner_rr` field to the trades table (idempotent ALTER):
   ```sql
   ALTER TABLE trades ADD COLUMN scanner_rr REAL;
   ```
   Populate this with the scanner-computed RR from the decision/signal that triggered the trade.

3. Add a `rr_alignment_warning` log line when executed trades have `planned_rr < scanner_rr * 0.7`:
   ```
   [RR_ALIGNMENT] SNIPER NVDA: scanner_rr=2.8 but execution_rr=1.5 — stop/target calculation divergence
   ```

4. Add to `scan_diagnostics.py` a new `_get_rr_execution_gap()` method:
   - Queries `decisions JOIN trades ON decisions.ticker = trades.ticker`
   - Returns avg scanner_rr vs avg execution_rr for each strategy
   - Surfaced in `run_diagnostics()` report as `rr_execution_gap` key

5. Add to `report_scan_quality.py` `--with-rr-gap` flag that shows this section.

---

## Additional Enhancement: Portfolio Correlation Guard

**Context:** The Feb 2026 batch held HIMS, EOSE, COIN, ASTS, RKLB, OKLO simultaneously —
all high-beta speculative names. A macro shock would hit all simultaneously.

**Fix required:**
Create `portfolio_correlation_guard.py` (new file):

```python
# Checks whether a proposed new trade is too correlated with existing open positions.
# Uses sector + beta overlap as proxy (no matrix required).

SECTOR_CONCENTRATION_LIMIT = 0.40  # max 40% of open positions in same sector
HIGH_BETA_LIMIT = 3                # max 3 simultaneous high-beta (beta > 1.5) positions

class PortfolioCorrelationGuard:
    def check(self, new_ticker: str, new_sector: str, new_beta: float,
              open_positions: list) -> tuple[bool, str]:
        """
        Returns (allowed: bool, reason: str).
        reason values: "OK", "SECTOR_CONCENTRATION", "HIGH_BETA_OVERLOAD"
        """
```

Wire into `unified_master_trader_v3.py` as an optional pre-execution check (import-guarded).
Add `correlation_blocked` column to decisions table (idempotent ALTER).
Add tests in `test_phase5.py`.

---

## Tests

Create `test_phase5.py` with the following test classes (minimum 25 tests, all must pass):

```
TestOptionsPcrLogging         — options_pcr/gamma now flows into decisions for scanner rejects
TestShortRrDiagnostic         — scan_diagnostics surfaces SHORT RR ceiling warning
TestRemораRrDynamic           — REMORA rr is no longer constant (unit test with mock entry/stop/target)
TestCircuitBreaker            — fires at 25%, doesn't fire at 24%, env override works
TestContrарianRegimeGate      — Contrarian scan only runs in HIGH_VIX / BEAR regime
TestRrExecutionGap            — scanner_rr column exists after migration, warning fires on divergence
TestCorrelationGuard          — sector concentration and high-beta blocks work correctly
TestMigrationIdempotency      — all new ALTER TABLE statements safe to run twice
TestBaselineUnchanged         — all existing strategy thresholds unchanged, pilot still OFF by default
```

---

## Validation Commands (run these and include output in response)

```bash
# 1. Compile check all touched files
python3 -m py_compile unified_master_trader_v3.py sniper_scanner_v2.py \
  short_scanner_v1.py remora_scanner_v2.py scan_diagnostics.py \
  report_scan_quality.py portfolio_correlation_guard.py test_phase5.py

# 2. Full test suite (all phases)
python3 -m pytest test_phase5.py test_phase43.py test_phase42.py \
  test_scan_diagnostics.py test_rr_shadow_calibration.py -v

# 3. Diagnostics report with all new sections
python3 report_scan_quality.py --days 3 --mode all --exclude-unknown-regime \
  --with-rr-shadow --with-pilot --with-rr-gap

# 4. Verify options_pcr is now populating (should show non-zero after next scan run)
python3 -c "
import sqlite3
db = sqlite3.connect('trading_performance.db')
rows = db.execute('''
  SELECT strategy,
    COUNT(*) total,
    SUM(CASE WHEN options_pcr IS NOT NULL THEN 1 ELSE 0 END) has_pcr
  FROM decisions WHERE date(timestamp) >= date(\"now\",\"-1 days\")
  GROUP BY strategy
''').fetchall()
for r in rows: print(r)
"
```

---

## Hard Constraints (Non-Negotiable)

1. Live thresholds NEVER change: SNIPER=2.5, SHORT=4.0, VOYAGER=2.0, REMORA=2.0
2. All schema changes additive and idempotent (try/except on ALTER TABLE)
3. All new module imports wrapped in try/except with `_AVAILABLE` bool fallback
4. Pilot remains OFF by default (PILOT_ENABLE=0)
5. No change to pilot execution caps (max 1/scan, max 2 concurrent, max 0.25% risk)
6. Existing 87 tests must continue to pass
7. `decision_logger.py` behavior must not change except for passing through the new kwargs
   it already accepts

---

## Definition of Done

- [ ] `options_pcr` populates in decisions table for scanner rejects (verify in DB after next scan)
- [ ] SHORT RR ceiling warning appears in scan diagnostics when max < threshold
- [ ] REMORA RR is dynamic, not hardcoded
- [ ] Circuit breaker fires on simulated 25%+ gap-down loss
- [ ] Contrarian scanner wired in with regime gate
- [ ] `scanner_rr` column in trades, RR alignment log fires on divergence
- [ ] `portfolio_correlation_guard.py` blocks sector concentration and beta overload
- [ ] All new ALTER TABLE migrations are idempotent
- [ ] `test_phase5.py`: 25+ tests, all pass
- [ ] All existing tests still pass (87 baseline)
- [ ] `python3 -m py_compile` clean on all touched files

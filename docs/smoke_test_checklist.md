# gem-trader — Operational Smoke-Test Checklist

Run this before every backtest cycle, paper-trading session, or live session restart.  
Each check is a one-liner you can run in the activated venv.

```
cd /home/gem/trading-production
source .venv/bin/activate
export $(grep -v '^#' /home/gem/secure/trading.env | xargs)
```

---

## 1. Alpaca bars fetch

Confirms `_bars_from_response` resolves BarSet correctly and SIP data is flowing.

```python
python3 -c "
from core.alpaca_client import get_alpaca
bars = get_alpaca().get_daily_bars('SPY', days=5, use_cache=False)
assert len(bars) >= 3, f'Expected ≥3 bars, got {len(bars)}'
print(f'  PASS — {len(bars)} SPY bars, last close: {bars[-1][\"close\"]}')
"
```

Expected: `PASS — N SPY bars, last close: ...`  
Failure indicator: `'BarSet' object has no attribute 'get'` → `_bars_from_response` not applied

---

## 2. FMP event / fundamental fetch

Confirms FMP stable API is reachable and key endpoints work.

```python
python3 -c "
from core.fmp_client import get_fmp
fmp = get_fmp()
vix = fmp.get_vix()
assert vix and vix > 0, f'VIX returned {vix!r}'
cal = fmp.get_economic_calendar(days_ahead=7)
assert isinstance(cal, list), f'Calendar returned {type(cal)}'
hist = fmp.get_historical_earnings(lookback_days=14)
assert isinstance(hist, list), f'Historical earnings returned {type(hist)}'
print(f'  PASS — VIX={vix:.1f}  calendar={len(cal)} events  hist_earnings={len(hist)} events')
"
```

Expected: `PASS — VIX=XX.X  calendar=N events  hist_earnings=N events`  
Failure indicator: `FMP HTTP 404` → wrong endpoint path; `FMP daily budget exhausted` → reset cache

---

## 3. Startup checks report correct state

```python
python3 -c "
from core.startup_checks import run_startup_checks
state = run_startup_checks()
print()
if state.halted:
    print('  FAIL — HALTED:', [r.name for r in state.results if not r.passed and r.critical])
elif state.degraded:
    print('  WARN — DEGRADED:', state.degraded_reasons)
else:
    print('  PASS — all checks green')
"
```

Expected: `PASS — all checks green`  
Degraded is acceptable if only FMP/cache checks fail (engine still runs).  
Halt = do not proceed until fixed.

---

## 4. Scanner emits candidates

Runs the SNIPER scanner against a small subset and confirms it produces at least one signal (or explains why not).

```python
python3 -c "
from strategies.sniper import SniperScanner
scanner = SniperScanner(account_equity=100_000)
opps = scanner.scan(['AAPL','MSFT','NVDA','GOOGL','AMZN','META','TSLA','AMD','SPY','QQQ'])
print(f'  INFO — SNIPER found {len(opps)} signal(s)')
for o in opps[:3]:
    print(f'    {o[\"ticker\"]}  score={o[\"score\"]}  entry={o[\"entry_price\"]}')
if not opps:
    print('  NOTE — 0 signals is valid if market conditions do not meet strategy criteria')
"
```

Expected: 0–3 signals. Zero is fine — it means conditions are not met.  
Failure indicator: `'BarSet' object has no attribute 'get'` → BarSet fix not applied

---

## 5. Veto council evaluates a candidate

```python
python3 -c "
from council.veto_council import VetoCouncil
council = VetoCouncil()
fake_signal = {
    'ticker': 'AAPL', 'strategy': 'SNIPER', 'direction': 'LONG',
    'entry_price': 180.0, 'stop_loss': 175.0, 'target_price': 192.0,
    'score': 72, 'shares': 10,
}
portfolio = {'open_positions': 0, 'max_positions': 10,
             'gross_long_pct': 0.0, 'gross_short_pct': 0.0, 'daily_pnl_pct': 0.0}
result = council.evaluate(fake_signal, portfolio)
print(f'  PASS — verdict={result[\"verdict\"]}  soft_score={result.get(\"soft_score\")}')
print(f'         agent={result.get(\"agent\")}  reason={result.get(\"reason\",\"\")[:60]}')
"
```

Expected: `verdict=APPROVED` or `verdict=VETOED` with a specific agent/reason.  
Failure indicator: exception → missing import or config not loaded

---

## 6. Allocator can pass and block

```python
python3 -c "
from execution.portfolio_allocator import PortfolioAllocator
alloc = PortfolioAllocator()

# Should pass (empty book)
ok, reason, status = alloc.evaluate(
    {'ticker':'AAPL','strategy':'SNIPER','direction':'LONG',
     'entry_price':180,'shares':10,'stop_loss':175,'target_price':192},
    [], 100_000
)
assert ok, f'Expected pass, got: {reason}'
print(f'  PASS — empty book: {status}')

# Should block (position limit)
fake_pos = [{'ticker':f'X{i}','market_value':5001,'side':'long','strategy':'SNIPER',
             'sector':''} for i in range(10)]
ok2, reason2, status2 = alloc.evaluate(
    {'ticker':'NVDA','strategy':'SNIPER','direction':'LONG',
     'entry_price':500,'shares':5,'stop_loss':480,'target_price':550},
    fake_pos, 100_000
)
assert not ok2, 'Expected block at max positions'
print(f'  PASS — full book blocked: {reason2}')
"
```

Expected: two PASS lines.

---

## 7. Execution path can succeed (paper only)

Only run against paper account. Confirms order submission works end-to-end.

```python
python3 -c "
import core.config as cfg
assert cfg.ALPACA_PAPER, 'SAFETY: only run this on paper account'
from core.alpaca_client import get_alpaca
result = get_alpaca().submit_limit_order(
    ticker='SPY', qty=1, side='buy', limit_price=1.00, time_in_force='day'
)
if result:
    print(f'  PASS — order submitted: {result}')
    # Cancel immediately
    get_alpaca().cancel_all_orders()
    print('  PASS — order cancelled')
else:
    print('  FAIL — order returned None')
"
```

**Only run on paper account.** Expected: order submitted + cancelled.

---

## 8. Dashboard reflects pipeline state

1. Start the dashboard:
   ```
   SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python dashboards/gem_trader_hq.py --mode 4
   ```
2. Verify header shows `[SYSTEM OK]` (green) or explains degraded state
3. Press `4` → Scanner mode. Verify UNIVERSE READINESS panel loads (or shows "snapshot not loaded" with explanation)
4. Press `S` → Manual scan starts. Watch status update: `scanning SNIPER (1/3)…`
5. After scan completes, verify SCANNER SIGNALS shows results or `No scanner-confirmed setups in last 24h`
   - Dashboard scan results appear as `SCAN-APPROVED` (yellow)
   - Daemon results appear as `EXEC-CONFIRMED` (green = live), `EXEC-FAILED` (red), or `SCAN-APPROVED`
6. Check GATED / BLOCKED panel — GATED items show the veto agent; ALLOC-BLK items show "ALLOC-BLK"
7. Press `1` → Monitor mode. Top-3 strip shows `EXEC` (live), `FAIL` (exec failed), or `APR` (approved, no fill)
8. In Monitor mode, verify Paper Evidence, Paper Readiness, and Governance
   Blocks show only active paper sleeves (`VOYAGER`, `SNIPER_V6`, `SHORT_A`).
9. Press `3` → Risk mode. Verify Evidence Freshness shows paper resolver and
   scoreboard timestamps.
10. Press `2` → Research mode. Verify Research Assist is labeled
    discretionary/manual research only and is not shown as paper evidence.

---

## Quick status — run all non-interactive checks in one shot

```bash
python3 -c "
import subprocess, sys
checks = [
    ('Alpaca bars',     'from core.alpaca_client import get_alpaca; bars=get_alpaca().get_daily_bars(\"SPY\",days=5,use_cache=False); assert len(bars)>=3'),
    ('FMP VIX',         'from core.fmp_client import get_fmp; v=get_fmp().get_vix(); assert v and v>0'),
    ('FMP hist_earn',   'from core.fmp_client import get_fmp; h=get_fmp().get_historical_earnings(14); assert isinstance(h,list)'),
    ('Startup checks',  'from core.startup_checks import run_startup_checks; s=run_startup_checks(); assert not s.halted'),
    ('Veto council',    'from council.veto_council import VetoCouncil; VetoCouncil()'),
    ('Allocator',       'from execution.portfolio_allocator import PortfolioAllocator; PortfolioAllocator()'),
]
all_ok = True
for name, code in checks:
    try:
        exec(code)
        print(f'  PASS  {name}')
    except Exception as e:
        print(f'  FAIL  {name}: {e}')
        all_ok = False
sys.exit(0 if all_ok else 1)
"
```

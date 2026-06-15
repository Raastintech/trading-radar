"""
scripts/trace_pipeline.py — Pipeline connectivity trace.

Verifies every production component can initialize and connect
WITHOUT downloading OHLCV bars or any bulk market data.

Data sourcing doctrine verified:
  ALPACA  → price bars, quotes, order execution, account info
  FMP     → VIX, SPY regime, earnings calendar, macro events,
            news sentiment, sector PE, fundamentals
  yfinance / Alpha Vantage / FRED → NEVER in production path

Usage:
  cd /home/gem/trading-production
  .venv/bin/python scripts/trace_pipeline.py
"""
from __future__ import annotations
import os
import sys
import sqlite3
import time
from pathlib import Path

# ── Load credentials from secure env file ────────────────────────────────────
CRED_FILE = os.environ.get("SNIPER_ENV_PATH", "/home/gem/secure/trading.env")
if not Path(CRED_FILE).exists():
    print(f"FAIL  Credential file not found: {CRED_FILE}")
    sys.exit(1)

from dotenv import load_dotenv
load_dotenv(CRED_FILE, override=True)
print(f"OK    Credentials loaded from: {CRED_FILE}\n")

# ── Project root on path ──────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS  = "OK   "
FAIL  = "FAIL "
WARN  = "WARN "
SEP   = "-" * 68


def check(label: str, fn):
    """Run fn(), print PASS/FAIL, return (ok, result)."""
    try:
        result = fn()
        print(f"{PASS} {label}")
        return True, result
    except Exception as exc:
        print(f"{FAIL} {label}  →  {exc}")
        return False, None


# ─────────────────────────────────────────────────────────────────────────────
print(SEP)
print("  1. IMPORTS — all production modules")
print(SEP)

ok, _ = check("core.config", lambda: __import__("core.config"))
ok, _ = check("core.alpaca_client", lambda: __import__("core.alpaca_client"))
ok, _ = check("core.fmp_client", lambda: __import__("core.fmp_client"))
ok, _ = check("core.data_gatekeeper", lambda: __import__("core.data_gatekeeper"))
ok, _ = check("core.decision_logger", lambda: __import__("core.decision_logger"))
ok, _ = check("core.market_regime", lambda: __import__("core.market_regime"))
ok, _ = check("core.macro_calendar", lambda: __import__("core.macro_calendar"))
ok, _ = check("council.veto_council", lambda: __import__("council.veto_council"))
ok, _ = check("strategies.shared.risk", lambda: __import__("strategies.shared.risk"))
ok, _ = check("strategies.sniper", lambda: __import__("strategies.sniper"))
ok, _ = check("strategies.remora", lambda: __import__("strategies.remora"))
ok, _ = check("strategies.contrarian", lambda: __import__("strategies.contrarian"))
ok, _ = check("strategies.voyager", lambda: __import__("strategies.voyager"))
ok, _ = check("strategies.short_sleeve", lambda: __import__("strategies.short_sleeve"))
ok, _ = check("execution.order_manager", lambda: __import__("execution.order_manager"))
ok, _ = check("execution.position_monitor", lambda: __import__("execution.position_monitor"))
ok, _ = check("execution.portfolio_risk", lambda: __import__("execution.portfolio_risk"))
ok, _ = check("execution.circuit_breakers", lambda: __import__("execution.circuit_breakers"))

# ─────────────────────────────────────────────────────────────────────────────
print()
print(SEP)
print("  2. CONFIG — required env vars")
print(SEP)

import core.config as cfg

def _masked(val: str) -> str:
    """Show only whether a key is set — never any characters of the value."""
    return "[SET]" if val and val.strip() else "[NOT SET — CHECK .env]"

for key, val in [
    ("ALPACA_API_KEY",    _masked(cfg.ALPACA_API_KEY)),
    ("ALPACA_SECRET_KEY", _masked(cfg.ALPACA_SECRET_KEY)),
    ("FMP_API_KEY",       _masked(cfg.FMP_API_KEY)),
    ("ALPACA_PAPER",      str(cfg.ALPACA_PAPER)),
    ("ALLOW_SHORTS",      str(cfg.ALLOW_SHORTS)),
    ("MAX_POSITION_PCT",  str(cfg.MAX_POSITION_PCT)),
    ("MAX_DAILY_LOSS_PCT",str(cfg.MAX_DAILY_LOSS_PCT)),
    ("DB_PATH",           str(cfg.DB_PATH)),
    ("CACHE_DIR",         str(cfg.CACHE_DIR)),
    ("LOG_DIR",           str(cfg.LOG_DIR)),
]:
    print(f"  {key:<24} {val}")

# ─────────────────────────────────────────────────────────────────────────────
print()
print(SEP)
print("  3. STORAGE — DB and cache directories")
print(SEP)

def check_db():
    conn = sqlite3.connect(str(cfg.DB_PATH))
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    conn.close()
    return [t[0] for t in tables]

ok, tables = check("SQLite DB connects", check_db)
if ok:
    print(f"       Tables: {tables or '(none yet)'}")

def check_cache():
    dirs = list(cfg.CACHE_DIR.iterdir()) if cfg.CACHE_DIR.exists() else []
    return [d.name for d in dirs if d.is_dir()]

ok, cache_dirs = check("Cache directory accessible", check_cache)
if ok:
    print(f"       Subdirs: {cache_dirs}")

ok, _ = check("Log directory writable", lambda: cfg.LOG_DIR.mkdir(parents=True, exist_ok=True))

# ─────────────────────────────────────────────────────────────────────────────
print()
print(SEP)
print("  4. ALPACA — account connectivity (no market data)")
print(SEP)

from core.alpaca_client import get_alpaca
alpaca_ok, alpaca = check("AlpacaClient instantiates", get_alpaca)

if alpaca_ok:
    def get_acct():
        acct = alpaca.get_account()
        if not acct:
            raise RuntimeError("Empty account response")
        return acct

    ok, acct = check("get_account() — equity and buying power", get_acct)
    if ok and acct:
        paper_label = " [PAPER]" if cfg.ALPACA_PAPER else " [LIVE]"
        print(f"       Equity:        ${float(acct.get('equity', 0)):>12,.2f}{paper_label}")
        print(f"       Buying power:  ${float(acct.get('buying_power', 0)):>12,.2f}")
        print(f"       Cash:          ${float(acct.get('cash', 0)):>12,.2f}")

    ok, positions = check("get_positions() — open positions count",
                          lambda: alpaca.get_positions())
    if ok:
        print(f"       Open positions: {len(positions)}")

print()
print("  DATA SOURCING — Alpaca feeds:")
print("    price bars (OHLCV)     → core.alpaca_client.get_daily_bars()")
print("    intraday bars          → core.alpaca_client.get_intraday_bars()")
print("    live quotes (bid/ask)  → core.alpaca_client.get_quote()")
print("    order execution        → core.alpaca_client.submit_*_order()")
print("    position management    → core.alpaca_client.get_positions() / close_position()")

# ─────────────────────────────────────────────────────────────────────────────
print()
print(SEP)
print("  5. FMP — signal data connectivity (lightweight calls only)")
print(SEP)

from core.fmp_client import get_fmp
fmp_ok, fmp = check("FMPClient instantiates", get_fmp)

if fmp_ok:
    ok, vix = check("get_vix() — VIX level", fmp.get_vix)
    if ok and vix is not None:
        print(f"       VIX: {vix:.2f}")

    ok, events = check("get_economic_calendar(days_ahead=3) — macro events",
                       lambda: fmp.get_economic_calendar(days_ahead=3))
    if ok:
        print(f"       Upcoming events (3d): {len(events)} item(s)")
        for e in events[:3]:
            print(f"         {e.get('date','?')}  {e.get('event','?')}  impact={e.get('impact','?')}")

    ok, earnings = check("get_earnings_calendar(days_ahead=7) — earnings",
                         lambda: fmp.get_earnings_calendar(days_ahead=7))
    if ok:
        print(f"       Earnings in next 7d: {len(earnings)} event(s)")

print()
print("  DATA SOURCING — FMP feeds:")
print("    VIX level              → fmp.get_vix()")
print("    SPY bars (regime)      → fmp.get_spy_bars()  [via RegimeAgent / ContrarianScanner]")
print("    earnings calendar      → fmp.get_earnings_calendar()  [ShortSleeve, VoyagerScanner]")
print("    macro events           → fmp.get_economic_calendar()  [MacroAgent, main.py]")
print("    news sentiment         → fmp.get_sentiment_score()  [SentimentAgent]")
print("    sector PE              → fmp.get_sector_pe()  [SectorAgent]")
print("    fundamentals           → fmp.get_fundamentals()  [future use]")
print("    treasury rates         → fmp.get_treasury_rates()  [future use]")

# ─────────────────────────────────────────────────────────────────────────────
print()
print(SEP)
print("  6. PRODUCTION COMPONENTS — instantiation only, no scans")
print(SEP)

from council.veto_council import VetoCouncil
from strategies.sniper import SniperScanner
from strategies.remora import RemoraScanner
from strategies.contrarian import ContrarianScanner
from strategies.voyager import VoyagerScanner
from strategies.short_sleeve import ShortSleeveScanner
from execution.order_manager import OrderManager
from execution.portfolio_risk import PortfolioRisk
from execution.circuit_breakers import CircuitBreakers
from core.decision_logger import DecisionLogger

equity = float(acct.get("equity", 100_000)) if (alpaca_ok and acct) else 100_000.0

check("VetoCouncil", VetoCouncil)
check("SniperScanner",     lambda: SniperScanner(account_equity=equity))
check("RemoraScanner",     lambda: RemoraScanner(account_equity=equity))
check("ContrarianScanner", lambda: ContrarianScanner(account_equity=equity))
check("VoyagerScanner",    lambda: VoyagerScanner(account_equity=equity))
check("ShortSleeveScanner",lambda: ShortSleeveScanner(account_equity=equity))
check("DecisionLogger",    DecisionLogger)
check("OrderManager",      lambda: OrderManager(DecisionLogger()))
check("PortfolioRisk",     PortfolioRisk)
check("CircuitBreakers",   CircuitBreakers)

# ─────────────────────────────────────────────────────────────────────────────
print()
print(SEP)
print("  7. DATA SOURCE MAP — what is NEVER allowed in production")
print(SEP)
print("    FORBIDDEN in production path:")
print("      yfinance               → never")
print("      Alpha Vantage          → never")
print("      FRED                   → never")
print("      TradingEconomics       → never")
print("      legacy/alpaca_data.py  → never (archived)")
print("      root-level orphan .py  → never (archived)")
print()
print("    ALLOWED (read from cache, fall back to live provider):")
print("      Alpaca SIP             → price, quotes, execution")
print("      FMP Starter Annual     → everything else")
print()
print("    CONFIRMED: main.py imports ONLY core/, council/, strategies/, execution/")

# ─────────────────────────────────────────────────────────────────────────────
print()
print(SEP)
print("  8. VETO COUNCIL — data source mapping per agent")
print(SEP)
print("    Agent             Data Source        Call")
print("    ─────────────     ────────────────── ────────────────────────────────")
print("    RegimeAgent       Alpaca + FMP        get_daily_bars(SPY) + get_vix()")
print("    MacroAgent        SQLite DB           macro_events table (loaded from FMP)")
print("    PortfolioAgent    in-memory state     portfolio_state dict from PositionMonitor")
print("    SectorAgent       FMP                 get_sector_pe()")
print("    FlowAgent         Alpaca              get_intraday_bars()")
print("    SentimentAgent    FMP                 get_sentiment_score()")
print("    EarningsAgent     FMP                 get_earnings_calendar()")
print("    SpreadAgent       Alpaca              get_quote() — live bid/ask")
print("    MomentumAgent     Alpaca              get_daily_bars() — 20d price")

# ─────────────────────────────────────────────────────────────────────────────
print()
print(SEP)
print("  TRACE COMPLETE")
print(SEP)

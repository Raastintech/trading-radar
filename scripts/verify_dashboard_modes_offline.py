#!/usr/bin/env python3
"""
Credential-free dashboard render smoke test.

This script deliberately does not load .env or SNIPER_ENV_PATH. It injects
dummy environment values before importing dashboard modules, then renders all
four dashboard modes against in-memory sample data.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["GEM_TRADER_SKIP_DOTENV"] = "true"
os.environ.setdefault("ALPACA_API_KEY", "offline")
os.environ.setdefault("ALPACA_SECRET_KEY", "offline")
os.environ.setdefault("FMP_API_KEY", "offline")
os.environ.setdefault("FMP_BASE_URL", "https://financialmodelingprep.com/stable")
os.environ.setdefault("ALPACA_PAPER", "true")
os.environ.setdefault("PAPER_TRADING", "true")
os.environ.setdefault("DB_PATH", str(ROOT / "db" / "trading.db"))
os.environ.setdefault("CACHE_DIR", str(ROOT / "cache"))
os.environ.setdefault("LOG_DIR", str(ROOT / "logs"))

from rich.console import Console

import dashboards.gem_trader_hq as hq


class FakeData:
    def __init__(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        bars = [
            {"date": f"2026-04-{day:02d}", "open": 690 + day, "high": 694 + day,
             "low": 686 + day, "close": 690 + day, "volume": 75_000_000}
            for day in range(1, 21)
        ]
        self._data: Dict[str, Any] = {
            "vix": 18.87,
            "spy_bars": bars,
            "regime": {"regime": "BULL", "trend_strength": "moderate"},
            "etf_quotes": {
                "QQQ": {"change_pct": -0.3},
                "IWM": {"change_pct": 0.6},
                "VXX": {"change_pct": 1.2},
            },
            "treasury": {"date": "2026-04-20", "year10": 4.2, "year2": 3.8},
            "econ_cal": [{"date": now, "event": "CPI YoY", "impact": "High"}],
            "earnings": [{"date": "2026-04-22", "symbol": "AAPL", "epsEstimated": 1.24}],
            "sector_pe": [],
            "positions": [
                {"ticker": "BJ", "strategy": "MANUAL", "side": "short", "qty": 55,
                 "entry_price": 92.94, "current_price": 95.91,
                 "unrealized_pnl": -163.0, "market_value": -5275.0,
                 "pnl_pct": -3.2, "sl_source": "MANUAL", "r_mult": None},
            ],
            "account": {
                "equity": 80382.29,
                "cash": 90923.79,
                "buying_power": 310014.28,
                "daily_pnl": 0,
            },
            "paper_summary": {
                "sleeves": {
                    "VOYAGER": {"raw": 1, "effective": 1, "open": 1, "closed": 0, "blocked": 0, "observe": 0},
                    "SNIPER": {"raw": 0, "effective": 0, "open": 0, "closed": 0, "blocked": 0, "observe": 0},
                    "SHORT": {"raw": 3, "effective": 3, "open": 1, "closed": 0, "blocked": 2, "observe": 0},
                },
                "readiness": {
                    "SNIPER": {"signals": 0, "completed": 0},
                    "SHORT": {"signals": 1, "completed": 0},
                    "VOYAGER": {"signals": 1, "completed": 0},
                },
                "governance": {
                    "same_ticker": 2, "sector": 0, "regime": 0, "max_position": 0,
                    "duplicate": 2, "frozen": 0, "other": 0,
                },
            },
            "evidence_status": {
                "ok": True,
                "last_success_at": now,
                "finished_at": now,
                "scoreboard_mtime": now,
            },
            "scan_results": {
                "opportunities": [{
                    "ticker": "AAPL", "strategy": "SNIPER", "direction": "LONG",
                    "score": 82, "entry_price": 190, "stop_loss": 184,
                    "target_price": 204, "risk_reward": 2.3,
                    "status": "SCAN_APPROVED", "ts": now,
                }],
                "vetoed": [],
                "last_cycle_ts": now,
            },
            "db_decisions": [],
            "news_market": [{"site": "offline", "title": "Market rally continues"}],
            "social_arb": {
                "version": "SMART_SOCIAL_ARB_RADAR_V1",
                "mode": "twice_weekly",
                "built_at": now,
                "_age_short": "0m",
                "raw_item_count": 4,
                "dropped_noise": {"total": 2, "reasons": {"generic_or_non_actionable": 1}},
                "items": [{
                    "ticker": "AAPL",
                    "bucket": "News Catalyst",
                    "confidence": "Medium",
                    "confidence_score": 66.4,
                    "noise_risk": "Low",
                    "source_type": "offline_sample",
                    "theme": "Consumer/App Demand",
                    "news_label": "Apple services demand catalyst needs manual verification",
                    "why_it_matters": "Company-specific catalyst may change investor expectations.",
                    "manual_check_needed": "verify the original article and timestamp; check latest tape",
                    "cross_refs": ["SCANNER+"],
                }],
            },
            "alpha_discovery": {
                "version": "ALPHA_DISCOVERY_V2.1",
                "mode": "nightly",
                "built_at": now,
                "_age_short": "0m",
                "track_counts": {"Emerging Opportunity": 1, "Liquid Leadership Reset": 0},
                "bucket_counts": {"Early Discovery": 1, "Too Late / Crowded": 0},
                "tier_counts": {"A": 0, "B": 1, "C": 0},
                "dominant_sectors": ["Technology"],
                "items": [{
                    "ticker": "AAPL",
                    "track": "Emerging Opportunity",
                    "bucket": "Early Discovery",
                    "alpha_score": 71.2,
                    "data_tier": "B",
                    "validator_state": "Watch Reclaim",
                    "action_label": "Watch Reclaim",
                    "why_now": "services demand and tape confirmation improving",
                    "main_risk": "needs stronger follow-through before a clean entry",
                    "sleeve_resemblance": "Sniper v6 resemblance",
                    "actionable_now": False,
                    "block_details": {
                        "business": "profile stable",
                        "sponsorship": "5d +2.4% | 20d +5.2% | vol 1.4x",
                    },
                    "validator_flags": ["needs reclaim"],
                }],
            },
            "alpha_discovery_overlay": {},
            "universe_snap": {
                "_file_age_seconds": 30,
                "sniper_universe": ["AAPL"],
                "voyager_universe": ["MSFT"],
                "short_universe": ["TSLA"],
                "remora_universe": ["NVDA"],
                "contrarian_universe": ["AMD"],
                "strategy_candidates": [
                    {"symbol": "AAPL", "strategy": "SNIPER", "direction": "LONG",
                     "readiness": "READY_NOW", "final_score": 0.61,
                     "base_score": 0.71, "price": 190.0,
                     "avg_dollar_volume_20": 850_000_000,
                     "current_dollar_volume": 1_050_000_000,
                     "return_5d_pct": 2.4, "return_20d_pct": 5.2, "volume_ratio_5d": 1.4,
                     "freshness_ts": "2026-04-20", "key_reason": "breakout structure"},
                    {"symbol": "MSFT", "strategy": "VOYAGER", "direction": "LONG",
                     "readiness": "DEVELOPING", "final_score": 0.29,
                     "base_score": 0.64, "price": 420.0,
                     "avg_dollar_volume_20": 620_000_000,
                     "current_dollar_volume": 575_000_000,
                     "return_5d_pct": 0.8, "return_20d_pct": 2.1, "volume_ratio_5d": 0.9,
                     "freshness_ts": "2026-04-20", "key_reason": "trend improving"},
                ],
                "summary": {
                    "built_at": now,
                    "fallback_used": False,
                    "pipeline_version": "offline",
                    "score_thresholds": {
                        "sniper": 0.38, "voyager": 0.35, "short": 0.35,
                        "remora": 0.32, "contrarian": 0.30,
                    },
                    "strategy_sizes": {
                        "sniper": 1, "voyager": 1, "short": 1,
                        "remora": 1, "contrarian": 1,
                    },
                },
            },
        }

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def provider_status(self) -> tuple[bool, bool]:
        return True, True

    def system_health(self) -> tuple[str, str, str]:
        return "OK", "bold green", ""

    def scanner_status(self) -> Dict[str, Any]:
        return {"running": False, "status": "offline verification", "last_run": None}

    def get_stock_lens(self, ticker: str) -> Dict[str, Any]:
        return {"_missing": True, "ticker": (ticker or "").upper()}

    def get_research_note(self, ticker: str) -> Dict[str, Any]:
        return {"_missing": True, "ticker": (ticker or "").upper()}

    def get_executive_gatekeeper(self, ticker: str) -> Dict[str, Any]:
        return {"_missing": True, "ticker": (ticker or "").upper()}

    def get_weekly_review(self) -> Dict[str, Any]:
        return {"_missing": True}


def main() -> int:
    console = Console(width=132, height=40, record=True)
    data = FakeData()
    state = hq.State()
    claude = hq.ClaudeAnalyzer()

    rendered = []
    for mode, name in hq.MODE_NAMES.items():
        state.mode = mode
        layout = hq._BUILDERS[mode](state, data, claude)
        console.print(layout)
        rendered.append(name)

    print("RENDER_OK " + ",".join(rendered))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

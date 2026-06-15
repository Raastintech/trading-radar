"""Macro standby is scoped to U.S. high-impact (3-star) events only.

Regression for the BoC/ECB false-standby defect: the FMP economic calendar
carries every country's events, but the U.S.-equities engine must only go into
defensive standby for U.S. high-impact prints. A foreign high-impact event
(Bank of Canada / ECB rate decision, UK GDP, China CPI) must NOT veto, and a
U.S. *medium*-impact event must NOT veto either.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone, timedelta

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from council import veto_council as vc_mod  # noqa: E402
import core.config as cfg  # noqa: E402
from core.decision_logger import DecisionLogger  # noqa: E402


@pytest.fixture()
def macro_db(tmp_path, monkeypatch):
    """Point cfg.DB_PATH at a throwaway DB seeded with macro_events rows."""
    db_path = tmp_path / "macro_test.db"
    monkeypatch.setattr(cfg, "DB_PATH", db_path, raising=False)
    log = DecisionLogger()  # creates schema (incl. country col) via _DDL/_migrate

    now = datetime.now(timezone.utc)
    soon = (now + timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
    events = [
        {"date": soon, "event": "BoC Interest Rate Decision", "country": "CA", "impact": "High"},
        {"date": soon, "event": "ECB Interest Rate Decision", "country": "EU", "impact": "High"},
        {"date": soon, "event": "Michigan Consumer Sentiment", "country": "US", "impact": "Medium"},
    ]
    log.refresh_macro_events(events)
    return log


def _macro_verdict():
    return vc_mod.VetoCouncil()._macro_agent("AAPL")["verdict"]


def test_foreign_high_impact_does_not_veto(macro_db):
    # Only CA/EU High + US Medium are queued -> no U.S. 3-star -> APPROVE.
    assert _macro_verdict() == "APPROVE"


def test_us_high_impact_vetoes(macro_db):
    now = datetime.now(timezone.utc)
    soon = (now + timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
    macro_db.refresh_macro_events([
        {"date": soon, "event": "CPI YoY", "country": "US", "impact": "High"},
        {"date": soon, "event": "BoC Interest Rate Decision", "country": "CA", "impact": "High"},
    ])
    assert _macro_verdict() == "VETO"


def test_null_country_does_not_veto(macro_db):
    # Legacy rows pre-migration have NULL country; they must fail safe to APPROVE.
    now = datetime.now(timezone.utc)
    soon = (now + timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
    macro_db.refresh_macro_events([
        {"date": soon, "event": "Unknown High Event", "impact": "High"},  # no country
    ])
    assert _macro_verdict() == "APPROVE"

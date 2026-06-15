"""
tests/unit/test_scanner_truth.py — Phase 1G.5 Scanner Truth Review foundation.

Covers: mirrored-constant drift guard vs the live scanners, point-in-time gate
correctness, no-look-ahead, root-cause classification, winner-universe
construction on synthetic data, read-only DB access, and the research-only
invariants (no execution/governance imports, cache-only).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from research.scanner_truth import filters as F  # noqa: E402
from research.scanner_truth import funnel_trace as FT  # noqa: E402


# ── 1. Drift guard: mirrored constants must match the live modules ───────────
# conftest.py stubs broker creds so the live modules import.

def test_mirrored_constants_match_live():
    import core.alpha_discovery as alpha
    import core.universe as uni
    import strategies.sniper as sniper
    import strategies.voyager as voy

    assert F.UNIV_MIN_PRICE == uni._MIN_PRICE
    assert F.UNIV_MAX_PRICE == uni._MAX_PRICE
    assert F.UNIV_MIN_AVG_VOL == uni._MIN_AVG_VOL
    assert F.UNIV_MIN_AVG_DVOL == uni._MIN_AVG_DVOL
    assert F.UNIV_BASE_LIMIT == uni._BASE_LIMIT

    assert F.VOY_MAX_EXTENSION_MA50 == voy.MAX_EXTENSION_MA50
    assert F.VOY_MA200_FLOOR == voy.MA200_FLOOR
    assert F.VOY_BARS_NEEDED == voy.BARS_NEEDED
    assert F.VOY_DVOL_TREND_RATIO == voy.DVOL_TREND_RATIO

    assert F.SNI_VOL_SPIKE_THRESH == sniper.VOL_SPIKE_THRESH
    assert F.SNI_BARS_NEEDED == sniper.BARS_NEEDED
    assert F.SNI_ATR_CONTRACTION_THRESH == sniper.ATR_CONTRACTION_THRESH

    assert F.ALPHA_MCAP_FLOOR == alpha.UNIVERSE_DEFINITION["market_cap_floor"]
    assert F.ALPHA_MCAP_CEILING == alpha.UNIVERSE_DEFINITION["market_cap_ceiling"]


# ── helpers ──────────────────────────────────────────────────────────────────

def _frame(closes, vols=None, n=None):
    n = n or len(closes)
    idx = pd.date_range("2025-01-01", periods=n, freq="B")
    closes = np.asarray(closes, dtype=float)
    vols = np.asarray(vols if vols is not None else [1_000_000] * n, dtype=float)
    return pd.DataFrame({
        "open": closes, "high": closes * 1.01, "low": closes * 0.99,
        "close": closes, "volume": vols,
    }, index=idx)


# ── 2. liquidity gate ────────────────────────────────────────────────────────

def test_liquidity_gate_pass_and_fail():
    df = _frame([50.0] * 30, vols=[1_000_000] * 30)  # $50 × 1M = $50M/day
    r = F.liquidity_gate(df, df.index[-1])
    assert r.passed and not r.reasons

    penny = _frame([1.0] * 30, vols=[1000] * 30)
    r2 = F.liquidity_gate(penny, penny.index[-1])
    assert not r2.passed
    assert "price_below_min" in r2.reasons and "avg_dvol_below_min" in r2.reasons


# ── 3. voyager extension + history gates ─────────────────────────────────────

def test_voyager_too_extended_detected():
    # 260 flat bars then a spike → price far above MA50 ⇒ too_extended.
    closes = [50.0] * 260
    closes[-1] = 80.0
    df = _frame(closes)
    r = F.voyager_structural(df, df.index[-1])
    assert "too_extended" in r.reasons
    assert not r.passed


def test_voyager_insufficient_history():
    df = _frame([50.0] * 100)  # < 260
    r = F.voyager_structural(df, df.index[-1])
    assert r.reasons == ["insufficient_history_260"]


# ── 4. sniper breakout ───────────────────────────────────────────────────────

def test_sniper_breakout_detected():
    rng = np.random.default_rng(0)
    base = list(50 + np.cumsum(rng.normal(0, 0.2, 99)))
    base.append(max(base[-21:-1]) + 5.0)         # decisive breakout close
    vols = [1_000_000] * 99 + [3_000_000]        # volume spike
    df = _frame(base, vols=vols)
    r = F.sniper_breakout(df, df.index[-1])
    assert "no_breakout" not in r.reasons
    assert "volume_insufficient" not in r.reasons


# ── 5. NO LOOK-AHEAD: future bars must not change an as-of verdict ────────────

def test_no_lookahead_in_gates():
    closes = [50.0] * 260
    df_full = _frame(closes + [200.0] * 5)        # big future spike appended
    asof = df_full.index[259]                     # as-of before the spike
    r_full = F.voyager_structural(df_full, asof)
    df_trunc = _frame(closes)
    r_trunc = F.voyager_structural(df_trunc, df_trunc.index[-1])
    # Same as-of date ⇒ identical metrics; the appended future is ignored.
    assert r_full.metrics["price"] == r_trunc.metrics["price"]
    assert r_full.reasons == r_trunc.reasons


# ── 6. root-cause classification ─────────────────────────────────────────────

def _base_trace(**over):
    t = {
        "market_cap": 5e9,
        "voyager_reconstruction": "reliable",
        "first_date_actually_saw": None,
        "first_date_too_extended": None,
        "price_gates": {
            "liquidity": {"passed": True, "reasons": [], "metrics": {}},
            "voyager": {"passed": True, "reasons": [], "metrics": {}},
            "sniper": {"passed": False, "reasons": ["no_breakout"], "metrics": {}},
            "alpha_mcap": {"passed": True, "reasons": [], "metrics": {}},
        },
        "db_presence": {"veto": None, "ever_approved": False, "ever_opened": False},
        "today_snapshot": {"in_alpha_board": False, "has_gatekeeper": False, "has_lens": False},
    }
    t.update(over)
    return t


def test_root_cause_universe_miss():
    cause, _ = FT.classify_root_cause(_base_trace())
    assert cause == "UNIVERSE_MISS"


def test_root_cause_valid_when_opened():
    t = _base_trace(db_presence={"veto": None, "ever_approved": True, "ever_opened": True})
    cause, _ = FT.classify_root_cause(t)
    assert cause == "VALID_NO_TRADE"


def test_root_cause_governance_veto():
    t = _base_trace(
        first_date_actually_saw="2026-05-01T00:00:00",
        db_presence={"veto": {"verdict": "VETOED", "agent": "liquidity",
                              "reason": "too thin"}, "ever_approved": False, "ever_opened": False})
    cause, _ = FT.classify_root_cause(t)
    assert cause == "GOVERNANCE_OR_SIZE_BLOCK"


def test_cache_limited_voyager_not_blamed_on_scanner():
    # too_extended present but reconstruction cache-limited ⇒ must NOT be FILTER_TOO_STRICT.
    t = _base_trace(
        voyager_reconstruction="cache_limited",
        price_gates={
            "liquidity": {"passed": True, "reasons": [], "metrics": {}},
            "voyager": {"passed": False, "reasons": ["too_extended"], "metrics": {}},
            "sniper": {"passed": False, "reasons": ["no_breakout"], "metrics": {}},
            "alpha_mcap": {"passed": True, "reasons": [], "metrics": {}},
        })
    cause, _ = FT.classify_root_cause(t)
    assert cause == "UNIVERSE_MISS"  # not FILTER_TOO_STRICT


# ── 7. winner universe construction on synthetic data ────────────────────────

def test_winner_universe_detects_known_winner(monkeypatch):
    from research.scanner_truth import dataio, winner_universe as wu
    cal = pd.date_range("2025-06-01", periods=120, freq="B")

    def mk(series):
        return pd.DataFrame({"open": series, "high": np.array(series) * 1.01,
                             "low": np.array(series) * 0.99, "close": series,
                             "volume": [2_000_000] * len(series)},
                            index=cal[-len(series):])

    spy = mk(list(np.linspace(100, 105, 120)))           # +5% benchmark
    winner = mk(list(np.linspace(10, 30, 120)))          # +200% liquid winner
    flat = mk([20.0] * 120)                              # non-winner

    prices = {"SPY": spy, "QQQ": spy, "WIN": winner, "FLAT": flat}
    monkeypatch.setattr(dataio, "load_prices", lambda t: prices.get(t.upper()))
    monkeypatch.setattr(dataio, "all_price_tickers", lambda: ["SPY", "QQQ", "WIN", "FLAT"])
    monkeypatch.setattr(dataio, "benchmark_calendar", lambda: cal)
    monkeypatch.setattr(dataio, "load_profiles", lambda: {
        "WIN": {"sector": "Technology", "industry": "Semiconductors",
                "market_cap": 2e9, "company_name": "Win Semi"}})
    # winner_universe imports these names at call time
    monkeypatch.setattr(wu.dataio, "load_prices", prices.get)
    monkeypatch.setattr(wu.dataio, "all_price_tickers", lambda: ["SPY", "QQQ", "WIN", "FLAT"])
    monkeypatch.setattr(wu.dataio, "benchmark_calendar", lambda: cal)
    monkeypatch.setattr(wu.dataio, "load_profiles", lambda: {
        "WIN": {"sector": "Technology", "industry": "Semiconductors",
                "market_cap": 2e9, "company_name": "Win Semi"}})

    res = wu.build()
    tickers = [w["ticker"] for w in res["winners"]]
    assert "WIN" in tickers and "FLAT" not in tickers
    win = next(w for w in res["winners"] if w["ticker"] == "WIN")
    assert win["best_max_return"] >= 1.0
    assert win["theme"] == "semiconductors"


# ── 8. research-only invariants ──────────────────────────────────────────────

def test_no_execution_or_governance_imports():
    pkg = REPO / "research" / "scanner_truth"
    files = list(pkg.glob("*.py")) + [
        REPO / "research" / "scanner_truth_review.py",
        REPO / "research" / "funnel_historizer.py",
    ]
    forbidden = ("from execution", "import execution", "from council", "import council",
                 "paper_governance", "order_manager", "submit_order")
    for f in files:
        src = f.read_text()
        for tok in forbidden:
            assert tok not in src, f"{f.name} must not reference {tok!r}"


def test_db_access_is_readonly():
    from research.scanner_truth import dataio
    con = dataio._ro_conn()
    with pytest.raises(Exception):
        con.execute("CREATE TABLE _should_fail (x INT)")
    con.close()


def test_classify_theme_keyword_map():
    from research.scanner_truth import dataio
    assert dataio.classify_theme({"sector": "Technology", "industry": "Semiconductors",
                                  "company_name": "X"}) == "semiconductors"
    assert dataio.classify_theme({"sector": "Industrials", "industry": "Aerospace & Defense",
                                  "company_name": "Rocket Co"}) == "space_aerospace"
    assert dataio.classify_theme({"sector": "Technology", "industry": "Hardware, Equipment & Parts",
                                  "company_name": "SanDisk Corp"}) == "memory_storage"
    assert dataio.classify_theme(None) == "unknown"


# ── Task 7: filter-audit verdict honesty ─────────────────────────────────────

def test_filter_audit_cache_limited_marked_indeterminate():
    from research.scanner_truth import filter_audit as FA
    rows = [{"ticker": "A", "is_winner": True, "bars": 50},
            {"ticker": "B", "is_winner": False, "bars": 50}]
    a = FA._audit_filter(rows, "voyager_bars_needed_260", "x", "<260",
                         lambda r: r["bars"] < 260, reliable=False)
    assert a["verdict"].startswith("INDETERMINATE")


def test_filter_audit_design_exclusion_verdict():
    from research.scanner_truth import filter_audit as FA
    # 8/10 winners rejected (high recall cost) but flagged design_exclusion.
    rows = [{"ticker": f"W{i}", "is_winner": True, "price": 1.0} for i in range(8)] \
        + [{"ticker": f"X{i}", "is_winner": True, "price": 50.0} for i in range(2)] \
        + [{"ticker": f"L{i}", "is_winner": False, "price": 1.0} for i in range(5)] \
        + [{"ticker": f"M{i}", "is_winner": False, "price": 50.0} for i in range(5)]
    a = FA._audit_filter(rows, "liquidity_price", "x", "≥$5",
                         lambda r: r["price"] < 5.0, design_exclusion=True)
    assert "BY-DESIGN" in a["verdict"]
    assert a["recall_cost_pct"] == 80.0


# ── Task 8: entry-timing buyable window ──────────────────────────────────────

def test_entry_timing_detects_buyable_then_extended():
    from research.scanner_truth import entry_timing as ET
    cal = pd.date_range("2025-01-01", periods=260, freq="B")
    # 250 bars hugging a slow uptrend (buyable near MA50), then a 10-bar blow-off.
    base = list(np.linspace(40, 50, 250)) + list(np.linspace(50, 90, 10))
    df = pd.DataFrame({"open": base, "high": np.array(base) * 1.01,
                       "low": np.array(base) * 0.99, "close": base,
                       "volume": [1_000_000] * 260}, index=cal)
    tl = ET._timeline(df, cal)
    assert tl["first_buyable_date"] is not None
    assert tl["first_too_extended_date"] is not None
    assert tl["first_buyable_date"] <= tl["first_too_extended_date"]
    assert tl["buyable_before_extended"] is True


# ── Task 5: baseline comparison structure on synthetic data ──────────────────

def test_baselines_structure_and_no_lookahead(monkeypatch):
    from research.scanner_truth import baselines as B, dataio
    cal = pd.date_range("2025-01-01", periods=140, freq="B")

    def mk(series):
        return pd.DataFrame({"open": series, "high": np.array(series) * 1.01,
                             "low": np.array(series) * 0.99, "close": series,
                             "volume": [3_000_000] * len(series)}, index=cal)

    flat = mk([50.0] * 140)
    # winner: flat until as-of (index 79), then doubles → only forward bars move.
    win = mk([50.0] * 80 + list(np.linspace(50, 110, 60)))
    prices = {"SPY": flat, "QQQ": flat, "WIN": win, "DUD": flat}
    for mod in (B.dataio, dataio):
        monkeypatch.setattr(mod, "load_prices", prices.get)
        monkeypatch.setattr(mod, "all_price_tickers", lambda: ["SPY", "QQQ", "WIN", "DUD"])
        monkeypatch.setattr(mod, "benchmark_calendar", lambda: cal)
        monkeypatch.setattr(mod, "load_profiles", lambda: {})
    res = B.build()
    assert set(res["baselines"]) == {"rs_20d", "high_50d_breakout", "vol_strength",
                                     "sector_rs", "mom_20_60"}
    assert res["n_forward_winners"] >= 1
    # WIN was flat through as-of, so a momentum-at-asof baseline must NOT have
    # flagged it on pre-asof data (no look-ahead): rs_20d at asof sees 0% return.
    assert res["baselines"]["rs_20d"]["winners_caught"] == 0


# ── Task 11: dashboard reads the summary sidecar cache-only ──────────────────

def test_dashboard_scanner_truth_line_cache_only():
    from rich.console import Console
    import dashboards.gem_trader_hq as dash

    class _StubData:
        _d = {"scanner_truth_summary": {
            "winner_recall_pct": 2.1, "main_failure": "UNIVERSE_MISS",
            "best_simple_baseline_recall_pct": 26.5}}

        def get(self, k):
            return self._d.get(k, {})

    panel = dash.PB.risk_telemetry(_StubData())
    console = Console(width=120)
    with console.capture() as cap:
        console.print(panel)
    text = cap.get()
    assert "scanner truth" in text and "2.1" in text


# ── research-only: autopsy never writes the DB / paper_signals ───────────────

def test_autopsy_modules_no_db_or_paper_writes():
    pkg = REPO / "research" / "scanner_truth"
    files = list(pkg.glob("*.py")) + [REPO / "research" / "scanner_truth_review.py"]
    for f in files:
        src = f.read_text().upper()
        for tok in ("INSERT INTO", "DELETE FROM", "DROP TABLE", "CREATE TABLE",
                    "ALTER TABLE", "UPDATE DECISIONS", "UPDATE PAPER_SIGNALS"):
            assert tok not in src, f"{f.name} must not write the DB ({tok})"

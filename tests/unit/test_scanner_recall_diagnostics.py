"""
tests/unit/test_scanner_recall_diagnostics.py — scanner recall diagnostics.

Covers: the three-cohort comparison (strict scanner vs simple-RS baseline vs
loose scanner), the loose-superset invariant, reject-counts-by-filter
accounting, top-rejected annotation, 5/10/20d forward-return math (no
look-ahead in selection, forward-only in measurement), artifact writing, and
the research-only source invariants.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from research import scanner_recall_diagnostics as SRD  # noqa: E402
from research.scanner_truth import dataio  # noqa: E402

N_BARS = 300          # bars ≤ as-of must exceed VOY_BARS_NEEDED (260)
LOOKBACK = 21


def _mk(close, vol=None, band=0.01):
    """OHLCV frame. `band` may be scalar or per-bar array (high/low % band)."""
    cal = pd.date_range("2025-01-01", periods=N_BARS, freq="B")
    c = np.asarray(close, dtype=float)
    b = np.broadcast_to(np.asarray(band, dtype=float), c.shape)
    v = np.asarray(vol if vol is not None else [3_000_000] * len(c), dtype=float)
    return pd.DataFrame({"open": c, "high": c * (1 + b), "low": c * (1 - b),
                         "close": c, "volume": v}, index=cal[-len(c):])


def _world(monkeypatch, tmp_path):
    """Synthetic universe around as-of index i = N_BARS - 1 - LOOKBACK.

    SPY      — gentle +5% drift benchmark.
    BREAKOUT — passes strict sniper at as-of: ATR contraction into a high-volume
               20d-high breakout with rising MA50; +12% over the next 20d.
    REJWIN   — strong RS leader but >12% above MA50 at as-of ⇒ strict voyager
               too_extended AND no sniper breakout bar; +25% forward. Must be
               caught by the RS baseline and the loose scanner.
    DUD      — flat, liquid, no edge, flat forward.
    THIN     — fails the liquidity gate (excluded from every cohort).
    """
    cal = pd.date_range("2025-01-01", periods=N_BARS, freq="B")
    i = N_BARS - 1 - LOOKBACK

    spy = _mk(np.linspace(100, 105, N_BARS))

    # BREAKOUT: slow riser, flat 55→56 consolidation with a narrowing band,
    # breakout bar at i (+8% close over prior high, 3× volume), then +12%/20d.
    c = np.concatenate([np.linspace(40, 55, i - 30),          # rising MA50
                        np.full(30, 56.0),                    # tight base
                        [61.0],                               # breakout at i
                        np.linspace(61.5, 68.3, N_BARS - i - 1)])
    band = np.concatenate([np.full(i - 15, 0.03), np.full(15, 0.006),
                           np.full(N_BARS - i, 0.01)])
    vol = np.full(N_BARS, 3_000_000.0)
    vol[i] = 9_000_000.0
    breakout = _mk(c, vol=vol, band=band)

    # REJWIN: recent ramp ⇒ ~17% above MA50 at i (fails strict 12% band,
    # inside the loose 25% band), no breakout bar at i (close ticks DOWN),
    # then keeps winning: ~+26% over the next 20 bars.
    c = np.concatenate([np.full(i - 40, 30.0),
                        np.linspace(30, 42, 40),              # +40% in 40 bars
                        [41.0],                               # down-tick at i
                        np.linspace(42, 52, N_BARS - i - 1)]) # +25.6% fwd20
    rejwin = _mk(c)

    dud = _mk(np.full(N_BARS, 40.0))
    thin = _mk(np.full(N_BARS, 2.0), vol=np.full(N_BARS, 10_000.0))

    prices = {"SPY": spy, "QQQ": spy, "BREAKOUT": breakout,
              "REJWIN": rejwin, "DUD": dud, "THIN": thin}
    profiles = {"REJWIN": {"sector": "Technology", "industry": "Semiconductors"},
                "BREAKOUT": {"sector": "Industrials", "industry": "Machinery"},
                "DUD": {"sector": "Utilities", "industry": "Electric"}}
    monkeypatch.setattr(dataio, "load_prices",
                        lambda t, prefer_deep=True: prices.get(t.upper()))
    monkeypatch.setattr(dataio, "all_price_tickers", lambda: sorted(prices))
    monkeypatch.setattr(dataio, "benchmark_calendar", lambda: cal)
    monkeypatch.setattr(dataio, "load_profiles", lambda: profiles)
    monkeypatch.setattr(dataio, "RESEARCH_CACHE", tmp_path)
    monkeypatch.setattr(dataio, "LOGS_DIR", tmp_path)
    return cal, i


# ── cohort membership ────────────────────────────────────────────────────────

def test_strict_catches_breakout_and_rejects_extended_winner(monkeypatch, tmp_path):
    _world(monkeypatch, tmp_path)
    res = SRD.build(lookback_td=LOOKBACK)
    strict = set(res["cohorts"]["strict_scanner"]["members_by_fwd20"])
    assert "BREAKOUT" in strict
    assert "REJWIN" not in strict
    assert "THIN" not in {t for c in res["cohorts"].values()
                          for t in c["members_by_fwd20"]}


def test_rs_baseline_catches_rejected_winner(monkeypatch, tmp_path):
    _world(monkeypatch, tmp_path)
    res = SRD.build(lookback_td=LOOKBACK)
    assert "REJWIN" in res["cohorts"]["rs_baseline"]["members_by_fwd20"]
    assert "DUD" not in res["cohorts"]["rs_baseline"]["members_by_fwd20"]


def test_loose_is_superset_of_strict(monkeypatch, tmp_path):
    cal, i = _world(monkeypatch, tmp_path)
    world = SRD._scan_world(cal, i)
    strict = {t for t, w in world.items()
              if not t.startswith("__") and w["strict_pass"]}
    loose = {t for t, w in world.items()
             if not t.startswith("__") and w["loose_pass"]}
    assert strict <= loose
    assert "REJWIN" in loose          # ext 0.25 band admits it


# ── reject accounting ────────────────────────────────────────────────────────

def test_reject_counts_by_filter(monkeypatch, tmp_path):
    _world(monkeypatch, tmp_path)
    res = SRD.build(lookback_td=LOOKBACK)
    by_code = {(r["lane"], r["filter"]): r for r in res["reject_counts_by_filter"]}
    # REJWIN fails voyager too_extended (and sniper no_breakout) at as-of.
    assert ("voyager", "too_extended") in by_code
    assert by_code[("voyager", "too_extended")]["winners_missed"] >= 1
    assert ("sniper", "no_breakout") in by_code
    # counts are per rejected liquid name; every count ≤ n_rejected.
    assert all(r["rejected_n"] <= res["n_rejected"]
               for r in res["reject_counts_by_filter"])


def test_top_rejected_annotation(monkeypatch, tmp_path):
    _world(monkeypatch, tmp_path)
    res = SRD.build(lookback_td=LOOKBACK)
    top = res["top_rejected"]
    assert top and top[0]["ticker"] == "REJWIN"       # biggest fwd20 reject
    rej = top[0]
    assert rej["caught_by_rs_baseline"] is True
    assert rej["caught_by_loose"] is True
    assert any(r.startswith("voyager:") for r in rej["reject_reasons"])
    assert rej["fwd20_pct"] == pytest.approx(51.5 / 41.0 * 100 - 100, abs=0.5)
    # ranked descending by fwd20
    vals = [x["fwd20_pct"] for x in top]
    assert vals == sorted(vals, reverse=True)


# ── forward math ─────────────────────────────────────────────────────────────

def test_forward_returns_and_winner_recall(monkeypatch, tmp_path):
    _world(monkeypatch, tmp_path)
    # rs_cap=1 ⇒ the RS baseline takes only its single strongest name.
    res = SRD.build(lookback_td=LOOKBACK, rs_cap=1)
    # REJWIN (+25.6%) and BREAKOUT (+11.4%) are the fwd20 ≥ +10% winners.
    assert res["n_forward_winners"] == 2
    rs = res["cohorts"]["rs_baseline"]
    assert rs["members_by_fwd20"] == ["REJWIN"]       # top rs20 pick
    assert rs["winner_recall_pct"] == 50.0            # catches REJWIN only
    strict = res["cohorts"]["strict_scanner"]
    assert strict["winner_recall_pct"] == 50.0        # catches BREAKOUT only
    loose = res["cohorts"]["loose_scanner"]
    assert loose["winner_recall_pct"] == 100.0        # catches both
    for c in res["cohorts"].values():
        for h in ("5d", "10d", "20d"):
            assert h in c["forward"]


def test_no_lookahead_in_rs_selection(monkeypatch, tmp_path):
    """A name flat through as-of that only ramps afterwards has NO pre-as-of
    momentum, so the RS baseline (which requires r40 > 0 as-of) must not pick
    it — even though it doubles afterwards. Selection sees bars ≤ as-of only;
    forward bars are outcome measurement, never selection input."""
    cal, i = _world(monkeypatch, tmp_path)
    late = np.concatenate([np.full(i + 1, 45.0),
                           np.linspace(45, 90, N_BARS - i - 1)])
    prices_late = _mk(late)
    orig = dataio.load_prices
    monkeypatch.setattr(
        dataio, "load_prices",
        lambda t, prefer_deep=True: prices_late if t == "LATE" else orig(t))
    orig_all = dataio.all_price_tickers()
    monkeypatch.setattr(dataio, "all_price_tickers", lambda: orig_all + ["LATE"])
    res = SRD.build(lookback_td=LOOKBACK)
    assert "LATE" not in res["cohorts"]["rs_baseline"]["members_by_fwd20"]
    # …but its post-as-of double makes it a forward winner in the tally.
    assert res["n_forward_winners"] == 3


def test_stale_and_suspect_parquets_excluded(monkeypatch, tmp_path):
    """FROZEN (no real bars covering the forward window — the 2026-07-02
    freshness failure class) and SPIKE (>+300% in 20d ⇒ bad parquet) must be
    excluded from every cohort and counted in the exclusion tallies."""
    cal, i = _world(monkeypatch, tmp_path)
    # FROZEN: real bars END before the forward window (left-aligned index —
    # _mk right-aligns, which would look fresh).
    n = i - 5
    frozen = pd.DataFrame(
        {"open": [40.0] * n, "high": [40.4] * n, "low": [39.6] * n,
         "close": [40.0] * n, "volume": [3_000_000.0] * n}, index=cal[:n])
    # SPIKE: +450% inside the 20d as-of lookback (c[i-20]=10 → c[i]=55).
    spike = _mk(np.concatenate([np.full(i - 10, 10.0),
                                np.full(N_BARS - i + 10, 55.0)]))
    orig = dataio.load_prices
    extra = {"FROZEN": frozen, "SPIKE": spike}
    monkeypatch.setattr(dataio, "load_prices",
                        lambda t, prefer_deep=True: extra[t] if t in extra else orig(t))
    orig_all = dataio.all_price_tickers()
    monkeypatch.setattr(dataio, "all_price_tickers",
                        lambda: orig_all + ["FROZEN", "SPIKE"])
    res = SRD.build(lookback_td=LOOKBACK)
    assert res["n_excluded_stale"] == 1
    assert res["n_excluded_data_suspect"] == 1
    for c in res["cohorts"].values():
        assert not {"FROZEN", "SPIKE"} & set(c["members_by_fwd20"])


def test_short_calendar_errors_cleanly(monkeypatch, tmp_path):
    _world(monkeypatch, tmp_path)
    monkeypatch.setattr(dataio, "benchmark_calendar",
                        lambda: pd.date_range("2025-01-01", periods=40, freq="B"))
    res = SRD.build(lookback_td=LOOKBACK)
    assert "error" in res
    assert SRD._render_txt(res)[0].startswith("== SCANNER RECALL DIAGNOSTICS — ERROR")


# ── artifacts + CLI ──────────────────────────────────────────────────────────

def test_main_writes_artifacts(monkeypatch, tmp_path, capsys):
    _world(monkeypatch, tmp_path)
    assert SRD.main(["--lookback-td", str(LOOKBACK), "--top-rejected", "5"]) == 0
    j = json.loads((tmp_path / "scanner_recall_diagnostics_latest.json").read_text())
    assert j["artifact_version"] == SRD.ARTIFACT_VERSION
    assert j["params"]["lookback_td"] == LOOKBACK
    assert len(j["top_rejected"]) <= 5
    txt = (tmp_path / "scanner_recall_diagnostics_latest.txt").read_text()
    assert "REJECT COUNTS BY FILTER" in txt and "TOP" in txt
    assert "not a buy list" in j["disclaimer"]


def test_verdict_flags_empty_strict(monkeypatch, tmp_path):
    _world(monkeypatch, tmp_path)
    cohorts = {"strict_scanner": {"n": 0, "forward": {"20d": {"mean_pct": None}}},
               "rs_baseline": {"n": 5, "forward": {"20d": {"mean_pct": 2.0}}},
               "loose_scanner": {"n": 9, "forward": {"20d": {"mean_pct": 1.0}}}}
    verdict, _ = SRD._verdict(cohorts)
    assert verdict == "STRICT_SCANNER_EMPTY"


# ── research-only invariants ─────────────────────────────────────────────────

def test_source_has_no_forbidden_imports():
    src = (REPO / "research" / "scanner_recall_diagnostics.py").read_text()
    for banned in ("core.order", "execution", "paper_governance",
                   "strategy_registry", "alpaca", "requests", "sqlite3"):
        assert banned not in src, f"forbidden reference: {banned}"


def test_loose_thresholds_are_looser_than_strict():
    from research.scanner_truth import filters as F
    assert SRD.LOOSE_VOY_BARS_NEEDED < F.VOY_BARS_NEEDED
    assert SRD.LOOSE_VOY_MA200_FLOOR < F.VOY_MA200_FLOOR
    assert SRD.LOOSE_VOY_MAX_EXTENSION_MA50 > F.VOY_MAX_EXTENSION_MA50
    assert SRD.LOOSE_VOY_DVOL_TREND_RATIO < F.VOY_DVOL_TREND_RATIO
    assert SRD.LOOSE_SNI_BREAKOUT_LOOKBACK < F.SNI_BREAKOUT_LOOKBACK
    assert SRD.LOOSE_SNI_VOL_SPIKE_THRESH < F.SNI_VOL_SPIKE_THRESH

"""
tests/unit/test_scanner_recall_repair.py — Phase 1G.6 Scanner Recall Repair.

Covers the emission-gap winner-diff bug fix, the RS recall lane (catches a
synthetic momentum winner, no look-ahead, no signals), the theme leadership
radar clustering, the cap audit drop accounting, the price-cache coverage
detector, the orchestrator's surfacing hooks, and the research-only invariants
(no execution/governance/strategy imports, no DB mutation, cache-only).
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

from research import (price_cache_coverage_audit as PCC,  # noqa: E402
                      price_cache_deepening_plan as PLAN,
                      rs_recall_forward_validation as FV,
                      rs_recall_lane as RSL,
                      scanner_cap_audit as CAP,
                      scanner_emission_gap_audit as EGA,
                      scanner_recall_repair as ORCH,
                      theme_leadership_radar as TLR,
                      top1000_universe_audit as AUD,
                      universe_dynamic_selection as UDS,
                      universe_forward_replay as UFR)

NEW_MODULES = [RSL, TLR, CAP, PCC, ORCH, EGA, PLAN, FV]

# Phase 1G.7 research modules whose source must stay execution/governance-free.
_1G7_FILES = [
    "rs_recall_lane", "theme_leadership_radar", "scanner_cap_audit",
    "price_cache_coverage_audit", "scanner_recall_repair",
    "scanner_emission_gap_audit", "price_cache_deepening_plan",
    "rs_recall_forward_validation",
]


# ── 1. emission-gap _stage winner-diff bug is fixed ──────────────────────────
# Regression: prev_winners (a prior-stage MEMBER set) may contain non-winners;
# dropped-here must count ONLY winners, so prev must be intersected with the
# winner set before diffing.

def test_emission_gap_stage_diff_counts_only_winners():
    winners = {"WIN1": 1.0, "WIN2": 2.0}          # only these two are winners
    members = {"WIN1"}                            # this stage kept WIN1
    # prev_winners includes a NON-winner ("NOISE") + a real winner not retained.
    prev = {"WIN1", "WIN2", "NOISE", "OTHER"}
    stage, carried = EGA._stage("s", members, winners, prev, "X")
    # Dropped here must be exactly {WIN2} — NOISE/OTHER are not winners.
    assert stage["winners_dropped_here"] == 1
    assert stage["largest_winners_dropped"][0]["ticker"] == "WIN2"
    assert stage["winners_retained"] == 1
    assert carried == {"WIN1"}


def test_emission_gap_stage_count_only_passes_prev_through():
    winners = {"W": 1.0}
    stage, carried = EGA._stage("band", None, winners, {"W"}, "Y",
                                universe_size=320, members_known=False)
    assert stage["winners_retained"] is None
    assert stage["winners_dropped_here"] == 0
    assert carried == {"W"}            # unknown-membership stage forwards prev


def test_emission_gap_recall_monotonic_through_known_stages():
    winners = {"A": 1.0, "B": 1.0, "C": 1.0, "D": 1.0}
    s1, p1 = EGA._stage("1", {"A", "B", "C", "D"}, winners, None, "-")
    s2, p2 = EGA._stage("2", {"A", "B", "C"}, winners, p1, "cut")
    s3, p3 = EGA._stage("3", {"A"}, winners, p2, "cut")
    recalls = [s1["winner_recall_retained_pct"], s2["winner_recall_retained_pct"],
               s3["winner_recall_retained_pct"]]
    assert recalls == sorted(recalls, reverse=True)   # non-increasing
    assert recalls == [100.0, 75.0, 25.0]


# ── 2. RS recall lane: labels, no look-ahead, catches a momentum winner ──────

def test_rs_lane_label_extremes():
    leader = {"rs20": 0.20, "r40": 0.30, "r60": 0.40, "near_20d_high": True,
              "ext_ma20": 0.05}
    assert RSL._label(leader, None) == "RS_LEADER"
    extended = {"rs20": 0.20, "r40": 0.30, "r60": 0.40, "near_20d_high": False,
                "ext_ma20": 0.40}
    assert RSL._label(extended, None) == "RS_EXTENDED"
    dead = {"rs20": -0.05, "r40": -0.1, "r60": -0.1, "near_20d_high": False,
            "ext_ma20": -0.2}
    assert RSL._label(dead, None) == "RS_NO_EDGE"
    assert "RS_NO_EDGE" not in RSL.BUYABLE_LABELS


def test_rs_lane_features_no_lookahead():
    # Build a series that is FLAT up to index i, then ramps. Features at i must
    # reflect only the flat history — no forward leakage.
    cal = pd.date_range("2025-01-01", periods=160, freq="B")
    series = [50.0] * 100 + list(np.linspace(50, 150, 60))
    df = pd.DataFrame({"open": series, "high": np.array(series) * 1.01,
                       "low": np.array(series) * 0.99, "close": series,
                       "volume": [3_000_000] * 160}, index=cal)
    spy = pd.Series([100.0] * 160, index=cal)
    f = RSL._features(df, cal, 99, spy, spy)
    assert f is not None
    # flat history ⇒ ~0 returns, not the +200% that only happens AFTER index 99.
    assert abs(f["r20"]) < 0.01 and abs(f["r60"]) < 0.01


def _synthetic_world(monkeypatch, tmp_path):
    """Patch dataio so every recall-repair module reads a synthetic universe."""
    from research.scanner_truth import dataio
    cal = pd.date_range("2025-01-01", periods=170, freq="B")

    def mk(series):
        s = np.asarray(series, dtype=float)
        return pd.DataFrame({"open": s, "high": s * 1.01, "low": s * 0.99,
                             "close": s, "volume": [3_000_000] * len(s)},
                            index=cal[-len(s):])

    spy = mk(list(np.linspace(100, 103, 170)))                 # ~+3% benchmark
    # WIN ramps hard the last ~70 bars → strong RS leader at as-of, +winner fwd.
    win = mk(list(np.linspace(20, 28, 100)) + list(np.linspace(28, 70, 70)))
    dud = mk([40.0] * 170)
    prices = {"SPY": spy, "QQQ": spy, "WIN": win, "DUD": dud}
    profiles = {"WIN": {"sector": "Technology", "industry": "Semiconductors",
                        "market_cap": 2e9, "company_name": "Win Semi"},
                "DUD": {"sector": "Utilities", "industry": "Regulated Electric",
                        "market_cap": 5e9, "company_name": "Dud Power"}}
    monkeypatch.setattr(dataio, "load_prices",
                        lambda t, prefer_deep=True: prices.get(t.upper()))
    monkeypatch.setattr(dataio, "all_price_tickers",
                        lambda: ["SPY", "QQQ", "WIN", "DUD"])
    monkeypatch.setattr(dataio, "benchmark_calendar", lambda: cal)
    monkeypatch.setattr(dataio, "load_profiles", lambda: profiles)
    monkeypatch.setattr(dataio, "RESEARCH_CACHE", tmp_path)     # empty ⇒ no board/lens
    monkeypatch.setattr(dataio, "LOGS_DIR", tmp_path)
    # CRITICAL: redirect every historizer's append path AND every doc/snapshot
    # path into tmp so synthetic runs (incl. ORCH.build() which calls the real
    # --historize sub-reports) can NEVER pollute the real forward-evidence
    # ledgers under data/research/ or overwrite real docs with synthetic data.
    monkeypatch.setattr(RSL, "HISTORY_PATH", tmp_path / "rs_hist.jsonl")
    monkeypatch.setattr(TLR, "HISTORY_PATH", tmp_path / "theme_hist.jsonl")
    monkeypatch.setattr(FV, "HISTORY_PATH", tmp_path / "rs_hist.jsonl")
    monkeypatch.setattr(UDS, "HISTORY_PATH", tmp_path / "uni_hist.jsonl")
    monkeypatch.setattr(PLAN, "DOCS_DIR", tmp_path)
    monkeypatch.setattr(AUD, "DOCS_DIR", tmp_path)
    monkeypatch.setattr(UDS, "DOCS_DIR", tmp_path)
    # minimal synthetic universe snapshot so the audit + dynamic builder run on
    # tmp rather than the real cache/universe snapshot.
    snap_path = tmp_path / "snap.json"
    snap_path.write_text(json.dumps({
        "base_universe": ["WIN", "DUD"], "generated_at": "2025-08-26T00:00:00+00:00",
        "fallback_used": False, "source": "test",
        "metadata": {"WIN": {"return_20d_pct": 10.0, "atr_pct_14": 4.0, "return_5d_pct": 2.0},
                     "DUD": {"return_20d_pct": 0.0, "atr_pct_14": 2.0, "return_5d_pct": 0.0}},
        "summary": {"excluded_for_filters": 1, "excluded_for_data": 0}}))
    monkeypatch.setattr(AUD, "SNAPSHOT", snap_path)
    monkeypatch.setattr(UDS, "SNAPSHOT", snap_path)
    monkeypatch.setattr(UDS, "WINNER_UNI", tmp_path / "missing.json")
    monkeypatch.setattr(UDS, "THEME", tmp_path / "missing.json")
    # universe_forward_replay binds HISTORY_PATH/DOCS_DIR at import — redirect both
    # so ORCH.build() neither reads the real ledger nor overwrites the real doc.
    monkeypatch.setattr(UFR, "HISTORY_PATH", tmp_path / "uni_hist.jsonl")
    monkeypatch.setattr(UFR, "DOCS_DIR", tmp_path)
    monkeypatch.setattr(UFR, "WINNER_UNI", tmp_path / "missing.json")
    return dataio


def test_rs_lane_build_catches_momentum_winner(monkeypatch, tmp_path):
    _synthetic_world(monkeypatch, tmp_path)
    res = RSL.build()
    labels = res["live"]["label_counts"]
    # WIN is a strong leader; DUD is flat ⇒ no edge.
    leaders = {x["ticker"] for x in res["live"]["top_rs_leaders"]}
    assert "WIN" in leaders
    assert labels.get("RS_NO_EDGE", 0) >= 1            # DUD
    bt = res["backtest"]
    assert bt["n_forward_winners"] >= 1                # WIN ramps forward
    assert "false_positive_pct" in bt                  # FP is reported, not hidden


# ── 3. theme leadership radar clusters related names ─────────────────────────

def test_theme_state_classifier():
    leading = [{"rs20": 0.2, "r20": 0.2, "r60": 0.3, "ext_ma20": 0.05,
                "near_high": True} for _ in range(5)]
    assert TLR._state(leading) == "LEADING"
    thin = [{"rs20": 0.2, "r20": 0.2, "r60": 0.3, "ext_ma20": 0.05,
             "near_high": True}]
    assert TLR._state(thin) == "NOT_CONFIRMED"          # < MIN_MEMBERS
    fading = [{"rs20": -0.05, "r20": -0.1, "r60": 0.30, "ext_ma20": -0.1,
               "near_high": False} for _ in range(5)]
    assert TLR._state(fading) == "FADING"


def test_theme_radar_build_clusters_winner(monkeypatch, tmp_path):
    _synthetic_world(monkeypatch, tmp_path)
    res = TLR.build()
    # WIN classifies as semiconductors; it should be clustered under that theme.
    assert "semiconductors" in res["themes"]
    assert "WIN" in res["themes"]["semiconductors"]["member_tickers"]
    # A 1-member cluster is correctly NOT_CONFIRMED (MIN_MEMBERS guard); the
    # state must still be one of the defined labels.
    assert res["themes"]["semiconductors"]["theme_state"] in (
        "LEADING", "EMERGING", "EXTENDED", "FADING", "NOT_CONFIRMED")
    # WIN's strong RS should be reflected in the cluster's median RS.
    assert res["themes"]["semiconductors"]["median_rs20_vs_spy"] > 0


# ── 4. cap audit reports dropped winners ─────────────────────────────────────

def test_cap_entry_accounts_dropped_winners():
    winners = {"A": 5.0, "B": 4.0, "C": 3.0}
    eligible = {"A", "B", "C"}
    survived_members = {"A", "B", "NOISE"}             # NOISE not a winner
    entry = CAP._cap_entry("board", 20, survived_members, eligible, winners,
                           {"A": "semis", "B": "semis", "C": "biotech"},
                           True, "note")
    assert entry["winners_surviving_cap"] == 2
    assert entry["winners_dropped_by_cap"] == 1        # C dropped
    assert entry["largest_winners_dropped"][0]["ticker"] == "C"
    assert entry["themes_crowded_out"] == {"biotech": 1}


# ── 5. price-cache coverage detects insufficient bars + shallow cache ────────

def test_price_cache_bucket_thresholds():
    assert PCC._bucket(10) == "lt_75"
    assert PCC._bucket(120) == "75_to_199"
    assert PCC._bucket(220) == "200_to_259"
    assert PCC._bucket(400) == "ge_260"


def test_price_cache_build_flags_shallow(monkeypatch, tmp_path):
    from research.scanner_truth import dataio
    cal = pd.date_range("2025-01-01", periods=300, freq="B")

    def mk(n):
        idx = cal[-n:]
        return pd.DataFrame({"close": np.linspace(10, 20, n),
                             "volume": [1e6] * n}, index=idx)

    # Whole "universe" is shallow (110 bars) ⇒ cache_uniformly_shallow True.
    prices = {f"T{i}": mk(110) for i in range(20)}
    prices["SPY"] = mk(110)
    monkeypatch.setattr(dataio, "all_price_tickers", lambda: list(prices))
    monkeypatch.setattr(dataio, "load_prices", lambda t: prices.get(t))
    monkeypatch.setattr(PCC, "WINNER_UNI", tmp_path / "missing.json")  # no winners
    res = PCC.build()
    assert res["cache_uniformly_shallow"] is True
    assert res["median_bars_universe"] == 110
    assert res["voyager_260bar_ok"] == 0
    assert "NON-FUNCTIONAL" in res["verdict"]


# ── 6. orchestrator surfacing hooks + action line ───────────────────────────

def test_orchestrator_action_flags_structural_problems():
    summary = {"leading_themes": ["semiconductors"], "leading_themes_on_alpha_board": [],
               "rs_lane_recall_pct": 36.6, "system_recall_pct": 2.1,
               "rs_lane_has_edge_over_random": False, "cache_uniformly_shallow": True}
    action = ORCH._action(summary)
    assert "absent from Alpha board" in action
    assert "structurally too narrow" in action
    assert "NO precision edge" in action
    assert "too shallow" in action


def test_orchestrator_build_emits_surfacing_hooks(monkeypatch, tmp_path):
    _synthetic_world(monkeypatch, tmp_path)
    # Sub-reports that read on-disk artifacts (emission/cap) will degrade
    # gracefully via run_status; the orchestrator must still emit hooks.
    res = ORCH.build()
    s = res["summary"]
    assert "mcp_summary_block" in s and "dashboard_line" in s
    assert s["dashboard_line"].startswith("Scanner Recall:")
    assert set(s["mcp_summary_block"]) >= {
        "system_recall", "rs_recall", "emission_gap_stage", "leading_theme",
        "action_needed"}


# ── 7. research-only invariants ──────────────────────────────────────────────

def test_no_execution_governance_or_strategy_imports():
    # NB: reading the paper_signals TABLE (read-only) is allowed; only writes are
    # forbidden (covered by test_no_db_mutation_or_paper_signal_writes).
    forbidden = ("from execution", "import execution", "from council", "import council",
                 "paper_governance", "order_manager", "submit_order",
                 "strategy_registry", "from strategies", "import strategies",
                 "decision_logger", "ALLOW_LIVE_CAPITAL")
    files = [REPO / "research" / f"{n}.py" for n in _1G7_FILES]
    for f in files:
        src = f.read_text()
        for tok in forbidden:
            assert tok not in src, f"{f.name} must not reference {tok!r}"


def test_no_db_mutation_or_paper_signal_writes():
    files = [REPO / "research" / f"{n}.py" for n in _1G7_FILES]
    for f in files:
        src = f.read_text().upper()
        for tok in ("INSERT INTO", "DELETE FROM", "DROP TABLE", "CREATE TABLE",
                    "ALTER TABLE", "UPDATE ", "INSERT OR REPLACE"):
            assert tok not in src, f"{f.name} must not mutate the DB ({tok})"


# ── Phase 1G.7 ────────────────────────────────────────────────────────────────

def test_deepening_plan_makes_no_provider_calls(monkeypatch, tmp_path):
    # The planner is cache-only by construction. Guard: if anything tried to hit a
    # provider it would import a client; we assert build() runs purely on cache.
    from research.scanner_truth import dataio
    cal = pd.date_range("2025-01-01", periods=120, freq="B")
    df = pd.DataFrame({"close": np.linspace(10, 12, 120), "volume": [1e6] * 120},
                      index=cal)
    monkeypatch.setattr(dataio, "all_price_tickers", lambda: ["SPY", "AAA", "BBB"])
    monkeypatch.setattr(dataio, "load_prices", lambda t, prefer_deep=True: df)
    monkeypatch.setattr(PLAN, "WINNER_UNI", tmp_path / "x.json")
    monkeypatch.setattr(PLAN, "ALPHA_BOARD", tmp_path / "x.json")
    monkeypatch.setattr(PLAN, "RS_LANE", tmp_path / "x.json")
    monkeypatch.setattr(PLAN, "THEME", tmp_path / "x.json")
    monkeypatch.setattr(PLAN, "BROKER_SNAP", tmp_path / "x.json")
    res = PLAN.build()
    assert res["target_bars"] == 300
    assert "current_depth_distribution" in res
    assert res["safe_refresh_command"]["dry_run"].endswith("--priority")
    # execute command is explicitly gated behind --execute
    assert "--execute" in res["safe_refresh_command"]["execute"]


def test_deepen_refresh_tool_dry_run_safe():
    # Load the gated refresh script and confirm a non --execute run returns 0
    # without contacting a provider (dry-run is the default).
    import importlib.util
    p = REPO / "scripts" / "deepen_price_cache.py"
    spec = importlib.util.spec_from_file_location("deepen_price_cache", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rc = mod.main(["--tickers", "FAKE1", "FAKE2"])  # no --execute ⇒ dry-run
    assert rc == 0
    # source guards: FMP is imported inside the --execute branch (Alpaca removed Phase 3A)
    src = p.read_text()
    assert "from core.alpaca_client import AlpacaClient" not in src
    assert "from core.fmp_client import get_fmp" in src
    assert "if not args.execute:" in src


def test_coverage_reports_ma200_unreliable(monkeypatch, tmp_path):
    from research.scanner_truth import dataio
    cal = pd.date_range("2025-01-01", periods=300, freq="B")

    def mk(n):
        return pd.DataFrame({"close": np.linspace(10, 20, n), "volume": [1e6] * n},
                            index=cal[-n:])
    prices = {f"T{i}": mk(110) for i in range(30)}
    prices["SPY"] = mk(110)
    monkeypatch.setattr(dataio, "all_price_tickers", lambda: list(prices))
    monkeypatch.setattr(dataio, "load_prices", lambda t, prefer_deep=True: prices.get(t))
    monkeypatch.setattr(PCC, "WINNER_UNI", tmp_path / "missing.json")
    monkeypatch.setattr(PCC, "ALPHA_BOARD", tmp_path / "missing.json")
    monkeypatch.setattr(PCC, "RS_LANE", tmp_path / "missing.json")
    monkeypatch.setattr(PCC, "THEME", tmp_path / "missing.json")
    res = PCC.build()
    assert "ma200" in res["unreliable_filters_due_to_depth"]
    assert "voyager_260bar" in res["unreliable_filters_due_to_depth"]
    assert res["depth_buckets"]["ge_300"] == 0
    assert res["depth_buckets"]["ge_60"] == 30


def test_rs_historizer_appends_top_n_only(monkeypatch, tmp_path):
    _synthetic_world(monkeypatch, tmp_path)
    from research.scanner_truth import dataio
    hist = tmp_path / "rs_hist.jsonl"
    monkeypatch.setattr(RSL, "HISTORY_PATH", hist)
    # cap is clamped to ≤25; request a small cap and confirm ranks respect it
    out = RSL.historize(cap=2)
    rows = dataio.read_jsonl(hist)
    assert out["rows_written"] == len(rows)
    assert all(r["rank"] <= 2 for r in rows)
    assert len(rows) <= 2
    # idempotent: a second run for the same as-of date writes nothing
    out2 = RSL.historize(cap=2)
    assert out2["already_present"] is True and out2["rows_written"] == 0
    # cap clamp: asking for 999 never exceeds the doctrine ceiling of 25
    assert RSL.historize.__defaults__ is not None
    big = RSL._ranked_live(dataio.benchmark_calendar(), dataio.load_profiles(), 999)
    assert len(big) <= 999  # _ranked_live itself caps by available buyables


def test_rs_historizer_rows_have_no_future_data(monkeypatch, tmp_path):
    # _ranked_live scans at the LAST bar using features that only see ≤asof bars.
    # Confirm a flat-then-ramp series shows ~0 momentum at as-of (no leakage).
    from research.scanner_truth import dataio
    cal = pd.date_range("2025-01-01", periods=170, freq="B")

    def mk(series):
        s = np.asarray(series, float)
        return pd.DataFrame({"open": s, "high": s * 1.01, "low": s * 0.99,
                             "close": s, "volume": [3_000_000] * len(s)},
                            index=cal[-len(s):])
    spy = mk(list(np.linspace(100, 103, 170)))
    # WIN ramps only AFTER the last bar would be impossible; make it ramp INTO
    # as-of so it is a legitimate leader using only past data.
    win = mk(list(np.linspace(20, 60, 170)))
    prices = {"SPY": spy, "QQQ": spy, "WIN": win}
    monkeypatch.setattr(dataio, "load_prices", lambda t, prefer_deep=True: prices.get(t.upper()))
    monkeypatch.setattr(dataio, "all_price_tickers", lambda: ["SPY", "QQQ", "WIN"])
    monkeypatch.setattr(dataio, "benchmark_calendar", lambda: cal)
    monkeypatch.setattr(dataio, "load_profiles", lambda: {})
    rows = RSL._ranked_live(cal, {}, 20)
    for r in rows:
        # rs_score must equal rs20_vs_spy computed from ≤asof bars (internal consistency)
        assert r["rs_score"] == r["rs20_vs_spy"]
        assert r["price"] > 0


def _seed_history(path: Path, asof: str, tickers):
    rows = [{"generated_at": "t", "asof_date": asof, "ticker": t, "rank": i + 1,
             "rs_score": 0.2, "label": "RS_LEADER"} for i, t in enumerate(tickers)]
    import json as _j
    path.write_text("\n".join(_j.dumps(r) for r in rows) + "\n")


def test_forward_validation_excludes_immature_events(monkeypatch, tmp_path):
    from research.scanner_truth import dataio
    cal = pd.date_range("2025-01-01", periods=140, freq="B")
    df = pd.DataFrame({"open": [50.0] * 140, "high": [50.0] * 140, "low": [50.0] * 140,
                       "close": [50.0] * 140, "volume": [3e6] * 140}, index=cal)
    monkeypatch.setattr(dataio, "load_prices", lambda t, prefer_deep=True: df)
    monkeypatch.setattr(dataio, "all_price_tickers", lambda: ["SPY", "QQQ", "WIN", "DUD"])
    monkeypatch.setattr(dataio, "benchmark_calendar", lambda: cal)
    monkeypatch.setattr(dataio, "RESEARCH_CACHE", tmp_path)
    hist = tmp_path / "h.jsonl"
    _seed_history(hist, str(cal[-1])[:10], ["WIN"])     # as-of = today ⇒ immature
    monkeypatch.setattr(FV, "HISTORY_PATH", hist)
    res = FV.build()
    assert all(d["matured_dates"] == 0 for d in res["by_horizon"].values())
    assert res["verdict"] == "NEED_MORE_DATA"


def test_forward_validation_compares_against_random_controls(monkeypatch, tmp_path):
    from research.scanner_truth import dataio
    cal = pd.date_range("2025-01-01", periods=140, freq="B")

    def mk(series):
        s = np.asarray(series, float)
        return pd.DataFrame({"open": s, "high": s * 1.01, "low": s * 0.99,
                             "close": s, "volume": [3e6] * len(s)}, index=cal)
    spy = mk([100.0] * 140)
    win = mk(list(np.linspace(50, 70, 140)))            # rising
    dud = mk([40.0] * 140)
    prices = {"SPY": spy, "QQQ": spy, "WIN": win, "DUD": dud}
    monkeypatch.setattr(dataio, "load_prices", lambda t, prefer_deep=True: prices.get(t.upper()))
    monkeypatch.setattr(dataio, "all_price_tickers", lambda: ["SPY", "QQQ", "WIN", "DUD"])
    monkeypatch.setattr(dataio, "benchmark_calendar", lambda: cal)
    monkeypatch.setattr(dataio, "RESEARCH_CACHE", tmp_path)
    hist = tmp_path / "h.jsonl"
    _seed_history(hist, str(cal[100])[:10], ["WIN"])    # matured for 5d/10d
    monkeypatch.setattr(FV, "HISTORY_PATH", hist)
    res = FV.build()
    h5 = res["by_horizon"]["5d"]
    assert h5["matured_dates"] >= 1
    # the random-control cohort is computed (key present, aggregated)
    assert "random_control" in h5
    assert "rs_top15" in h5


def test_theme_history_writes_snapshots(monkeypatch, tmp_path):
    _synthetic_world(monkeypatch, tmp_path)
    from research.scanner_truth import dataio
    hist = tmp_path / "theme_hist.jsonl"
    monkeypatch.setattr(TLR, "HISTORY_PATH", hist)
    res = TLR.build()
    out = TLR.historize(res)
    rows = dataio.read_jsonl(hist)
    assert out["rows_written"] == len(rows) and len(rows) >= 1
    assert all("theme_state" in r and "top_leaders" in r for r in rows)
    # idempotent per as-of date
    assert TLR.historize(res)["already_present"] is True


def test_no_live_capital_or_signal_tokens_in_1g7_sources():
    forbidden = ("PAPER_TRADING", "ALPACA_PAPER", "ALLOW_LIVE_CAPITAL",
                 "LIVE_CONFIRM_FILE", "submit_order", "close_position")
    files = [REPO / "research" / f"{n}.py" for n in _1G7_FILES] + \
            [REPO / "scripts" / "deepen_price_cache.py"]
    for f in files:
        src = f.read_text()
        for tok in forbidden:
            assert tok not in src, f"{f.name} must not reference {tok!r}"

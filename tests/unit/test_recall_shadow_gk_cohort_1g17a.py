"""tests/unit/test_recall_shadow_gk_cohort_1g17a.py — Phase 1G.17A.

Frozen recall-shadow × Gatekeeper cohort + forward validation:
  - cohort row assembly (GK status, blocking gates, too_extended_block flag,
    lens state, lane extension join, anchored price with bar date)
  - write-once freeze refusal (immutable evidence)
  - forward outcomes: trading-day offsets, immature exclusion, rel-SPY/QQQ,
    MFE/MAE, WATCH vs BLOCK grouping, pre-registered Q1-Q3 verdicts
  - history append idempotent per as-of date
  - research-only: no paper signals, no proposals, no forbidden imports
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

REPO = Path(__file__).resolve().parents[2]

from research import recall_shadow_gk_cohort_freeze as frz  # noqa: E402
from research import recall_shadow_gk_forward as fwd  # noqa: E402
from research.scanner_truth import dataio  # noqa: E402


def _bars(n, start="2026-01-01", price=10.0, step=0.0):
    idx = pd.bdate_range(start, periods=n)
    close = pd.Series([price * (1 + step) ** i for i in range(n)], index=idx)
    return pd.DataFrame({"open": close, "high": close * 1.02,
                         "low": close * 0.98, "close": close,
                         "volume": 1_000_000}, index=idx).rename_axis("date")


# ── freeze: row assembly ──────────────────────────────────────────────────────

def test_build_row_block_with_too_extended(monkeypatch, tmp_path):
    monkeypatch.setattr(frz, "_gk", lambda t: {
        "final_status": "BLOCK", "confidence": "medium",
        "generated_at": "2026-06-11T23:52:59+00:00",
        "blocking_reasons": ["[entry_quality → BLOCK] Daily Entry Validator "
                             "state is 'Too Extended' — fresh entry is "
                             "disqualified"],
        "gates": [{"name": "entry_quality", "verdict": "BLOCK"},
                  {"name": "regime_sector", "verdict": "DOWNGRADE"}],
    })
    monkeypatch.setattr(frz, "_lens", lambda t: {
        "label": "Neutral", "confidence": "medium",
        "generated_at": "2026-06-11T23:50:00+00:00"})
    monkeypatch.setattr(frz.dataio, "load_prices",
                        lambda t, **k: _bars(120, start="2026-01-01"))
    cand = {"ticker": "CPSH", "rank": 1, "label": "SHADOW_THEME_LEADER",
            "theme": "hardware", "sector": "Technology"}
    lane = {"CPSH": {"extension_label": "NORMAL", "ext_pct": 0.187}}
    row = frz.build_row(cand, lane, anchor="2026-06-11")
    assert row["gk_status"] == "BLOCK"
    assert row["gk_blocking_gates"] == ["entry_quality"]
    assert row["too_extended_block"] is True
    assert row["ext_pct"] == 0.187
    assert row["lens_state"] == "Neutral"
    assert row["price_at_refresh"] is not None
    assert row["price_bar_date"] <= "2026-06-11"
    assert "NOT a trade proposal" in row["note"]


def test_build_row_watch_not_too_extended(monkeypatch):
    monkeypatch.setattr(frz, "_gk", lambda t: {
        "final_status": "WATCH", "confidence": "medium",
        "blocking_reasons": [], "gates": []})
    monkeypatch.setattr(frz, "_lens", lambda t: {"label": "Bullish but not buyable yet"})
    monkeypatch.setattr(frz.dataio, "load_prices", lambda t, **k: _bars(60))
    row = frz.build_row({"ticker": "FJET", "rank": 2}, {}, anchor="2026-06-11")
    assert row["gk_status"] == "WATCH"
    assert row["too_extended_block"] is False
    assert row["gk_blocking_gates"] == []


def test_freeze_anchor_no_lookahead(monkeypatch):
    """Anchor price must come from a bar at-or-before the anchor date."""
    monkeypatch.setattr(frz, "_gk", lambda t: {"final_status": "WATCH",
                                               "blocking_reasons": [], "gates": []})
    monkeypatch.setattr(frz, "_lens", lambda t: None)
    df = _bars(120, start="2026-01-01")
    monkeypatch.setattr(frz.dataio, "load_prices", lambda t, **k: df)
    anchor = str(df.index[50].date())
    row = frz.build_row({"ticker": "X"}, {}, anchor=anchor)
    assert row["price_bar_date"] == anchor
    assert row["price_at_refresh"] == round(float(df["close"].iloc[50]), 4)


def test_freeze_write_once_refusal(tmp_path, monkeypatch, capsys):
    frozen = tmp_path / "frozen.json"
    frozen.write_text(json.dumps({"frozen_at": "2026-06-12T00:01:33Z",
                                  "rows": [], "version": "1G.17A",
                                  "anchor_date": "2026-06-11", "n_total": 0,
                                  "n_watch": 0, "n_block": 0,
                                  "n_too_extended_block": 0}))
    monkeypatch.setattr(frz, "FROZEN", frozen)
    monkeypatch.setattr(frz, "OUT_TXT", tmp_path / "out.txt")
    rc = frz.main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "refusing to overwrite" in out
    # file untouched
    assert json.loads(frozen.read_text())["frozen_at"] == "2026-06-12T00:01:33Z"


# ── forward: outcomes + grouping + verdicts ───────────────────────────────────

def _row(t, status, anchor, price, teb=False):
    return {"ticker": t, "gk_status": status, "too_extended_block": teb,
            "price_bar_date": anchor, "price_at_refresh": price}


def test_forward_outcomes_and_immature_exclusion(monkeypatch):
    df = _bars(60, step=0.01)           # rises 1%/day
    spy = _bars(60)["close"]            # flat
    monkeypatch.setattr(fwd.dataio, "load_prices", lambda t, **k: df)
    anchor = str(df.index[-6].date())   # 5 forward bars available
    row = _row("UP", "WATCH", anchor, float(df["close"].iloc[-6]))
    out = fwd.ticker_outcomes(row, spy, spy)
    assert out["h5"] is not None and out["h5"]["fwd"] > 0.04
    assert out["h5"]["rel_spy"] == pytest.approx(out["h5"]["fwd"], abs=1e-6)
    assert out["h10"] is None and out["h20"] is None   # immature, excluded
    assert out["h5"]["mfe"] >= out["h5"]["fwd"] >= out["h5"]["mae"]


def test_forward_groups_watch_vs_block(monkeypatch):
    up, dn = _bars(60, step=0.01), _bars(60, step=-0.01)
    spy = _bars(60)["close"]
    frames = {"W1": up, "W2": up, "B1": dn, "B2": dn}
    monkeypatch.setattr(fwd.dataio, "load_prices",
                        lambda t, **k: frames[t])
    anchor = str(up.index[-11].date())
    rows = [_row("W1", "WATCH", anchor, float(up["close"].iloc[-11])),
            _row("W2", "WATCH", anchor, float(up["close"].iloc[-11])),
            _row("B1", "BLOCK", anchor, float(dn["close"].iloc[-11]), teb=True),
            _row("B2", "BLOCK", anchor, float(dn["close"].iloc[-11]), teb=True)]
    outcomes = [fwd.ticker_outcomes(r, spy, None) for r in rows]
    agg = fwd.aggregate(outcomes)
    assert agg["WATCH"]["by_horizon"]["10"]["n"] == 2
    assert agg["BLOCK"]["by_horizon"]["10"]["n"] == 2
    assert agg["TOO_EXTENDED_BLOCK"]["by_horizon"]["10"]["n"] == 2
    v = fwd.verdicts(agg)
    q2 = v["q2_watch_vs_block"]["10"]
    assert q2["watch_outperformed_block"] is True
    q1 = v["q1_too_extended_blocks_continued_higher"]["10"]
    assert q1["continued_higher_rel_spy"] is False   # blocks fell — block saved capital
    q3 = v["q3_gk_precision_over_shadow_board"]["10"]
    assert q3["gk_adds_precision"] is True
    assert v["need_more_data"] is True               # n=2 < 15 floor


def test_forward_history_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(fwd, "HISTORY", tmp_path / "h.jsonl")
    res = {"asof_date": "2026-06-12", "generated_at": "x",
           "verdicts": {}, "groups": {g: {"by_horizon": {}} for g in fwd.GROUPS}}
    assert fwd.historize(res) == 1
    assert fwd.historize(res) == 0


def test_forward_handles_missing_anchor():
    out = fwd.ticker_outcomes(
        {"ticker": "GONE", "gk_status": "BLOCK", "too_extended_block": False,
         "price_bar_date": None, "price_at_refresh": None},
        _bars(60)["close"], None)
    assert out["error"] == "no_anchor_or_bars"


# ── research-only guarantees ──────────────────────────────────────────────────

def test_no_forbidden_imports_or_signal_paths():
    for mod in ("recall_shadow_gk_cohort_freeze.py",
                "recall_shadow_gk_forward.py"):
        src = (REPO / "research" / mod).read_text()
        for token in ("execution.order_manager", "execution.paper_governance",
                      "paper_governance", "submit_market_order",
                      "submit_limit_order", "council.veto_council",
                      "ALLOW_LIVE_CAPITAL", "close_position(",
                      "DecisionLogger", "log_voyager_paper_signal",
                      "INSERT INTO", "sqlite3"):
            assert token not in src, f"{mod}: forbidden {token!r}"
        assert "research-only" in src.lower() or "research only" in src.lower()


def test_frozen_cohort_file_is_never_written_by_forward():
    src = (REPO / "research" / "recall_shadow_gk_forward.py").read_text()
    for line in src.splitlines():
        if "FROZEN" in line:
            assert "write" not in line.lower().replace("never rewritten", ""), line
    assert "write_json(FROZEN" not in src

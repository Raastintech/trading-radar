"""Tests for the Core-Satellite 2A forward-shadow ledger.

Covers: pure row-building (no lookahead, seed row, realized-return
arithmetic for both variants), ledger idempotency per date, the metrics
report (total return / max drawdown / annualization suppression below
the day floor), and import hygiene. All synthetic -- no providers, no
production price cache, no real regime classifier.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

from research import core_satellite_forward_shadow as src


def _texp(label):
    return {"BULL_TREND": 1.0, "CHOP": 0.6, "RISK_OFF": 0.1}.get(label, 0.0)


def _basset(qqq_vs_spy_20):
    return "QQQ" if (qqq_vs_spy_20 or 0.0) >= 0 else "SPY"


# ── build_next_row ───────────────────────────────────────────────────────────


def test_seed_row_has_no_realized_return():
    row = src.build_next_row(
        today="2026-08-01", qqq_close=500.0, spy_close=600.0, prev=None,
        regime_label="BULL_TREND", qqq_vs_spy_20=0.02,
        target_exposure_fn=_texp, blend_asset_fn=_basset)
    assert row["seed"] is True
    assert row["realized_return_qqq_variant"] is None
    assert row["realized_return_blend_variant"] is None
    assert row["shadow_nav_qqq_variant"] == 1.0
    assert row["shadow_nav_blend_variant"] == 1.0
    # decides tomorrow's exposure from TODAY's regime
    assert row["decided_target_exposure"] == 1.0
    assert row["decided_blend_asset"] == "QQQ"
    assert row["research_only"] is True
    assert "SHADOW" in row["watermark"]


def test_realizes_prior_days_decided_exposure_qqq_variant():
    prev = {
        "date": "2026-08-01", "qqq_close": 500.0, "spy_close": 600.0,
        "decided_target_exposure": 0.6, "decided_blend_asset": "QQQ",
        "shadow_nav_qqq_variant": 1.0, "shadow_nav_blend_variant": 1.0,
    }
    row = src.build_next_row(
        today="2026-08-02", qqq_close=510.0, spy_close=606.0, prev=prev,
        regime_label="CHOP", qqq_vs_spy_20=-0.01,
        target_exposure_fn=_texp, blend_asset_fn=_basset)
    qqq_ret = 510.0 / 500.0 - 1.0  # 2%
    assert row["realized_return_qqq_variant"] == round(0.6 * qqq_ret, 8)
    # blend variant also used QQQ (that's what prev decided)
    assert row["realized_return_blend_variant"] == round(0.6 * qqq_ret, 8)
    assert row["shadow_nav_qqq_variant"] == round(1.0 * (1 + 0.6 * qqq_ret), 8)
    # today's own regime (CHOP) decides TOMORROW's exposure
    assert row["decided_target_exposure"] == 0.6
    assert row["decided_blend_asset"] == "SPY"  # qqq_vs_spy_20 < 0


def test_blend_variant_realizes_spy_when_prior_decided_spy():
    prev = {
        "date": "2026-08-01", "qqq_close": 500.0, "spy_close": 600.0,
        "decided_target_exposure": 0.5, "decided_blend_asset": "SPY",
        "shadow_nav_qqq_variant": 1.0, "shadow_nav_blend_variant": 1.0,
    }
    row = src.build_next_row(
        today="2026-08-02", qqq_close=520.0, spy_close=606.0, prev=prev,
        regime_label="RISK_OFF", qqq_vs_spy_20=0.0,
        target_exposure_fn=_texp, blend_asset_fn=_basset)
    qqq_ret = 520.0 / 500.0 - 1.0   # 4%
    spy_ret = 606.0 / 600.0 - 1.0   # 1%
    # qqq_variant ALWAYS uses qqq_ret regardless of blend choice
    assert row["realized_return_qqq_variant"] == round(0.5 * qqq_ret, 8)
    # blend_variant uses spy_ret because prev decided SPY
    assert row["realized_return_blend_variant"] == round(0.5 * spy_ret, 8)


def test_no_lookahead_decision_uses_prior_row_not_todays_regime():
    """The return realized THIS row must come from PREV's decided exposure,
    never from today's freshly-classified regime -- that regime only
    decides the NEXT row's exposure."""
    prev = {
        "date": "2026-08-01", "qqq_close": 100.0, "spy_close": 100.0,
        "decided_target_exposure": 0.0,  # prev decided ZERO exposure
        "decided_blend_asset": "QQQ",
        "shadow_nav_qqq_variant": 1.0, "shadow_nav_blend_variant": 1.0,
    }
    row = src.build_next_row(
        # even though today's move is huge, prior exposure was 0 -> flat
        today="2026-08-02", qqq_close=200.0, spy_close=50.0, prev=prev,
        regime_label="BULL_TREND", qqq_vs_spy_20=0.5,  # today's regime is bullish
        target_exposure_fn=_texp, blend_asset_fn=_basset)
    assert row["realized_return_qqq_variant"] == 0.0
    assert row["realized_return_blend_variant"] == 0.0
    assert row["shadow_nav_qqq_variant"] == 1.0
    # but the NEXT day's exposure now reflects today's bullish regime
    assert row["decided_target_exposure"] == 1.0


# ── ledger IO ────────────────────────────────────────────────────────────────


def test_append_row_idempotent_per_date(tmp_path):
    row1 = {"date": "2026-08-01", "shadow_nav_qqq_variant": 1.0,
            "shadow_nav_blend_variant": 1.0}
    assert src.append_row(row1, root=tmp_path) is True
    assert src.append_row(row1, root=tmp_path) is False  # same date, no-op
    ledger = src.load_ledger(root=tmp_path)
    assert len(ledger) == 1


def test_load_ledger_sorts_by_date_and_skips_malformed(tmp_path):
    path = tmp_path / "data" / "research" / "core_satellite_forward_shadow.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"date": "2026-08-03", "v": 3}) + "\n"
        + "not json\n"
        + json.dumps({"date": "2026-08-01", "v": 1}) + "\n"
        + json.dumps({"no_date": True}) + "\n"
        + json.dumps({"date": "2026-08-02", "v": 2}) + "\n",
        encoding="utf-8")
    ledger = src.load_ledger(root=tmp_path)
    assert [r["date"] for r in ledger] == \
        ["2026-08-01", "2026-08-02", "2026-08-03"]


def test_load_ledger_missing_file_returns_empty(tmp_path):
    assert src.load_ledger(root=tmp_path) == []


# ── metrics report ───────────────────────────────────────────────────────────


def test_metrics_report_empty_ledger():
    report = src.build_metrics_report([])
    assert report["n_rows"] == 0
    assert report["variants"] == {}


def test_metrics_report_seed_only_notes_no_realized_return():
    report = src.build_metrics_report([
        {"date": "2026-08-01", "shadow_nav_qqq_variant": 1.0,
         "shadow_nav_blend_variant": 1.0},
    ])
    assert "no realized return" in report["note"]


def test_metrics_report_total_return_and_max_drawdown():
    ledger = [
        {"date": "2026-08-01", "shadow_nav_qqq_variant": 1.00,
         "shadow_nav_blend_variant": 1.00},
        {"date": "2026-08-02", "shadow_nav_qqq_variant": 1.10,
         "shadow_nav_blend_variant": 1.05},
        {"date": "2026-08-03", "shadow_nav_qqq_variant": 0.99,
         "shadow_nav_blend_variant": 1.02},  # drawdown from the 1.10 peak
    ]
    report = src.build_metrics_report(ledger)
    qqq = report["variants"]["qqq_variant"]
    assert qqq["n_realized_days"] == 2
    assert qqq["total_return_pct"] == round((0.99 / 1.00 - 1.0) * 100, 3)
    # drawdown measured from the interim peak (1.10), not from day 1
    assert qqq["max_drawdown_pct"] == round((0.99 / 1.10 - 1.0) * 100, 3)
    assert qqq["current_nav"] == 0.99


def test_metrics_report_suppresses_annualized_below_day_floor():
    ledger = [{"date": f"2026-08-{d:02d}",
               "shadow_nav_qqq_variant": 1.0 + d * 0.001,
               "shadow_nav_blend_variant": 1.0 + d * 0.001}
              for d in range(1, 11)]  # 10 rows -> 9 realized days (< 20 floor)
    report = src.build_metrics_report(ledger)
    qqq = report["variants"]["qqq_variant"]
    assert qqq["n_realized_days"] == 9
    assert qqq["cagr_pct"] is None
    assert qqq["calmar"] is None
    assert "suppressed" in qqq["annualized_metrics_note"]


def test_metrics_report_computes_annualized_above_day_floor():
    # 25 rows (2026-08-01 .. 2026-08-25) -> 24 realized days, clears the
    # 20-day floor.
    ledger = []
    nav = 1.0
    for d in range(1, 26):
        nav *= 1.001
        ledger.append({"date": f"2026-08-{d:02d}",
                       "shadow_nav_qqq_variant": nav,
                       "shadow_nav_blend_variant": nav})
    report = src.build_metrics_report(ledger)
    qqq = report["variants"]["qqq_variant"]
    assert qqq["n_realized_days"] == 24
    assert qqq["cagr_pct"] is not None
    assert qqq["cagr_pct"] > 0  # steadily compounding positive series


# ── import hygiene ───────────────────────────────────────────────────────────


def test_no_broker_or_execution_imports():
    source = Path(src.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    forbidden = ("alpaca", "tradier", "execution", "broker", "core.config",
                "core.alpaca", "order", "strategies", "council",
                "requests", "anthropic")
    for mod in imported:
        assert not any(mod.startswith(f) or f in mod for f in forbidden), \
            f"forbidden import in core_satellite_forward_shadow: {mod}"


def test_never_writes_outside_its_two_paths(tmp_path):
    """append_row/write_report must only ever touch the ledger + sidecar
    paths under the given root -- no other files should appear."""
    row = {"date": "2026-08-01", "shadow_nav_qqq_variant": 1.0,
           "shadow_nav_blend_variant": 1.0}
    src.append_row(row, root=tmp_path)
    src.write_report(root=tmp_path)
    written = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")
                     if p.is_file())
    assert set(str(p) for p in written) == {
        str(src.LEDGER_REL), str(src.SIDECAR_REL)}

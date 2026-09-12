"""Statistical safety rails for the forward-evidence reports.

The regression these tests exist for: cache/prices/PI.parquet carried four
weekend bars priced near $0.09 — a 24/7 symbol's rows spliced into Impinj's
daily equity series. The forward tracker took a Saturday close as an entry
price and resolved a +189,857% 10d return. That single row moved the
6,216-row mean from -1.7% to +28.9%, and through the program-candidate
baseline it decided a published High-Conviction verdict.

Two independent guards must hold: the bar never enters (session filter), and
a return that large is quarantined from every statistic if it is already in
the ledger (data-error net). Neither is an outlier filter — genuine fat
tails stay in the sample and are handled by reporting the median and the
winsorized mean.
"""
from __future__ import annotations

import pytest

from research import forward_stat_safety as fss


# ── session hygiene ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("value,expected", [
    ("2026-01-02", True),    # Friday
    ("2026-01-03", False),   # Saturday
    ("2026-01-04", False),   # Sunday
    ("2026-01-05", True),    # Monday
    ("2026-08-15", False),   # the PI bar
    ("2026-01-02T00:00:00", True),
    ("not-a-date", False),
    (None, False),
])
def test_session_dates_are_weekdays(value, expected):
    assert fss.is_session_date(value) is expected


def test_weekend_bars_are_separated_not_silently_dropped():
    pairs = [("2026-01-02", 101.0), ("2026-01-03", 0.09),
             ("2026-01-04", 0.09), ("2026-01-05", 102.0)]
    kept, dropped = fss.drop_non_session_bars(pairs)
    assert kept == [("2026-01-02", 101.0), ("2026-01-05", 102.0)]
    assert [d for d, _ in dropped] == ["2026-01-03", "2026-01-04"]


def test_dropping_a_spliced_bar_fixes_the_return_and_the_horizon():
    """The wrong bar corrupts twice: the entry price and the bar count."""
    pairs = [("2026-01-02", 100.0), ("2026-01-03", 0.09), ("2026-01-05", 110.0)]
    kept, _ = fss.drop_non_session_bars(pairs)
    naive = (pairs[2][1] / pairs[1][1] - 1) * 100     # entry on the junk bar
    guarded = (kept[1][1] / kept[0][1] - 1) * 100
    assert naive > 100_000
    assert guarded == pytest.approx(10.0)


# ── data-error net ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("ret,horizon,expected", [
    (189857.03, 10, True),
    (184717.69, 20, True),
    (155.1, 10, False),      # a real 10d double stays in the sample
    (-100.0, 10, False),     # a real wipeout stays in the sample
    (999.0, 10, False),
    (1000.0, 10, True),
    (1500.0, 252, False),    # multi-year multiples happen
    (2000.0, 252, True),
    (None, 10, False),
    ("n/a", 10, False),
])
def test_artifact_net_catches_broken_bars_not_big_moves(ret, horizon, expected):
    assert fss.is_price_artifact(ret, horizon) is expected


def test_a_broken_entry_price_quarantines_the_whole_row():
    """One impossible horizon means the entry price is wrong, so the row's
    other horizons are not evidence either."""
    rows = [
        {"ticker": "PI", "appearance_date": "2026-08-15",
         "ret_5d": -6.23, "ret_10d": 189857.03, "ret_20d": 188119.97},
        {"ticker": "AEM", "appearance_date": "2026-08-15",
         "ret_5d": 1.2, "ret_10d": 3.4, "ret_20d": 5.6},
    ]
    clean, artifacts = fss.split_price_artifacts(rows, (5, 10, 20))
    assert [r["ticker"] for r in clean] == ["AEM"]
    assert len(artifacts) == 1
    assert artifacts[0]["ticker"] == "PI"
    assert artifacts[0]["fields"] == ["ret_10d", "ret_20d"]
    assert "broken price bar" in artifacts[0]["reason"]


def test_quarantined_rows_are_returned_for_reporting():
    rows = [{"ticker": "PI", "appearance_date": "2026-08-08",
             "ret_20d": 184717.69}]
    clean, artifacts = fss.split_price_artifacts(rows, (10, 20))
    assert clean == []
    assert artifacts[0]["values"] == {"ret_20d": 184717.69}


# ── robust statistics ────────────────────────────────────────────────────────


def test_headline_stays_the_mean_on_a_well_behaved_sample():
    stats = fss.robust_stats([1.0, 2.0, 3.0, 4.0, 5.0])
    assert stats["mean"] == 3.0
    assert stats["median"] == 3.0
    assert stats["outlier_driven"] is False
    assert stats["headline_mean"] == 3.0


def test_headline_falls_back_to_winsorized_when_mean_and_median_disagree():
    values = [-2.0, -1.0, 0.0, 1.0, 2.0] * 10 + [5000.0]
    stats = fss.robust_stats(values)
    assert stats["mean"] > 90
    assert stats["median"] == 0.0
    assert stats["outlier_driven"] is True
    assert stats["headline_mean"] == stats["winsorized_mean"]
    assert abs(stats["headline_mean"]) < 5


def test_empty_sample_reports_nothing_rather_than_zero():
    stats = fss.robust_stats([])
    assert stats["n"] == 0
    assert stats["mean"] is None and stats["headline_mean"] is None
    assert stats["outlier_driven"] is False


def test_the_pi_shape_is_neutralised_end_to_end():
    """The real failure, in miniature: a clean sample plus one broken bar."""
    rows = [{"ticker": f"T{i:03d}", "ret_10d": v}
            for i, v in enumerate([-2.0, -1.0, 0.5, 1.0, -0.5] * 40)]
    rows.append({"ticker": "PI", "appearance_date": "2026-08-15",
                 "ret_10d": 189857.03})
    contaminated = fss.robust_stats([r["ret_10d"] for r in rows])
    assert contaminated["mean"] > 900          # the headline nobody can read

    clean_rows, artifacts = fss.split_price_artifacts(rows, (10,))
    clean = fss.robust_stats([r["ret_10d"] for r in clean_rows])
    assert len(artifacts) == 1
    assert clean["mean"] == pytest.approx(clean["median"], abs=1.0)
    assert abs(clean["mean"] - clean["median"]) < fss.MEAN_MEDIAN_GAP_PP


# ── overlapping observations ─────────────────────────────────────────────────


def test_each_name_counts_once_however_often_it_reappears():
    rows = ([{"ticker": "AAA", "v": 10.0}] * 30
            + [{"ticker": "BBB", "v": -10.0}, {"ticker": "CCC", "v": 0.0}])
    stats = fss.per_entity_stats(rows, "v")
    assert stats["n"] == 3                      # names, not rows
    assert stats["rows"] == 32
    assert stats["overlap_factor"] == pytest.approx(10.67, abs=0.01)
    assert stats["mean"] == 0.0                 # row-weighted it would be ~9.4
    assert stats["basis"] == "one observation per ticker"


def test_a_name_contributes_the_mean_of_its_own_appearances():
    rows = [{"ticker": "AAA", "v": 0.0}, {"ticker": "AAA", "v": 10.0},
            {"ticker": "BBB", "v": 4.0}]
    stats = fss.per_entity_stats(rows, "v")
    assert stats["n"] == 2
    assert stats["mean"] == 4.5                 # (5.0 + 4.0) / 2


def test_rows_missing_the_value_are_skipped_not_zeroed():
    rows = [{"ticker": "AAA", "v": 4.0}, {"ticker": "BBB"},
            {"ticker": None, "v": 9.0}]
    stats = fss.per_entity_stats(rows, "v")
    assert stats["n"] == 1 and stats["mean"] == 4.0


# ── the two consumers ────────────────────────────────────────────────────────


def test_tracker_quarantines_artifacts_and_publishes_both_views():
    """The tracker's bucket stats must drop a broken row, say so, and carry
    the per-name view beside the row-weighted one."""
    import research.research_watchlist_forward_tracker as rwft

    entries = [{"ticker": f"T{i:03d}", "appearance_date": "2026-08-01",
                "ret_10d": 1.0, "ret_10d_vs_spy": 0.5} for i in range(60)]
    entries += [{"ticker": "AAA", "appearance_date": f"2026-08-{d:02d}",
                 "ret_10d": 2.0, "ret_10d_vs_spy": 1.5} for d in range(1, 21)]
    entries.append({"ticker": "PI", "appearance_date": "2026-08-15",
                    "ret_10d": 189857.03, "ret_10d_vs_spy": 189857.76})

    out = rwft._compute_verdicts(entries, "ALL")

    assert out["price_artifacts_excluded"] == 1
    assert out["price_artifacts"][0]["ticker"] == "PI"
    assert out["matured_entries"] == 80          # the broken row is not evidence
    assert out["mean_ret_10d"] == pytest.approx(out["median_ret_10d"], abs=1.0)
    assert out["robust"]["vs_spy"]["outlier_driven"] is False
    # 61 names behind 80 rows: the per-name view says so
    assert out["per_ticker"]["vs_spy"]["n"] == 61
    assert out["per_ticker"]["vs_spy"]["overlap_factor"] > 1.0


def test_shortlist_verdict_compares_like_for_like(tmp_path, monkeypatch):
    """The published verdict must rest on the outlier-guarded headline of
    both sides — the bug was a +1.7% shortlist losing to a baseline of
    +28.87% that one broken bar had produced."""
    import research.high_conviction_forward as hcf

    baseline_rows = [{"ticker": f"B{i:03d}", "appearance_date": "2026-08-01",
                      "ret_10d": -1.0, "ret_10d_vs_spy": -1.7}
                     for i in range(200)]
    baseline_rows.append({"ticker": "PI", "appearance_date": "2026-08-15",
                          "ret_10d": 189857.03, "ret_10d_vs_spy": 189857.76})
    path = tmp_path / "data" / "research" / "research_watchlist_history.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(__import__("json").dumps(r)
                              for r in baseline_rows), encoding="utf-8")

    baseline = hcf._program_baseline(tmp_path)

    assert baseline["price_artifacts_excluded"] == 1
    vs_spy = baseline["10d"]["vs_spy"]
    assert vs_spy["headline_mean"] == pytest.approx(-1.7, abs=0.01)
    assert vs_spy["n"] == 200
    # and the per-name view is published beside it
    assert baseline["10d"]["vs_spy_per_ticker"]["n"] == 200

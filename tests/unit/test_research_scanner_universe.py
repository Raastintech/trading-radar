"""
tests/unit/test_research_scanner_universe.py

Tests for the research scanner universe construction pipeline (Tasks 1-6).

Verifies:
  - _alpha_board_tickers() reads the real alpha_discovery_board_latest.json
  - Missing old filenames do not zero-out the alpha board
  - Overlay with 0 candidates does not erase real board candidates
  - _is_valid_equity_symbol() rejects warrants / units / rights / foreign
  - _ranked_cache_fill() selects strong RS / liquid names over alphabetical noise
  - _build_universe() no longer dominated by A-range alphabetical symbols
  - M-Z tickers appear through ranked fill when they have better RS / liquidity
  - Alpha board seed contributes nonzero names when file exists
  - Invalid warrant/unit symbols are excluded from universe
  - Universe build artifact (build_info) is structurally correct
  - Miss diagnostic reports expected keys
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch, MagicMock

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import os
os.environ.setdefault("ALPACA_API_KEY", "offline")
os.environ.setdefault("ALPACA_SECRET_KEY", "offline")
os.environ.setdefault("FMP_API_KEY", "offline")
os.environ.setdefault("ALPACA_PAPER", "true")
os.environ.setdefault("PAPER_TRADING", "true")
os.environ.setdefault("DB_PATH", str(REPO / "db" / "trading.db"))
os.environ.setdefault("CACHE_DIR", str(REPO / "cache"))
os.environ.setdefault("LOG_DIR", str(REPO / "logs"))
os.environ.setdefault("FMP_BASE_URL", "https://financialmodelingprep.com/stable")

from research.research_scanner import (
    _alpha_board_tickers,
    _daily_radar_tickers,
    _is_valid_equity_symbol,
    _ranked_cache_fill,
    _build_universe,
    _load_social_data,
    _social_score_from_item,
    _enrich_item,
    _FMP_SECTOR_TO_ETF,
    RESEARCH_DIR,
    PRICE_DIR,
    DEFAULT_UNIVERSE_CAP,
)


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_parquet(tmp: Path, sym: str, bars: int = 100, price: float = 50.0,
                  vol: float = 1_000_000.0, rs_drift: float = 0.0) -> Path:
    """Write a synthetic parquet for `sym` with `bars` rows."""
    idx = pd.date_range("2024-01-01", periods=bars, freq="B")
    prices = price * (1 + rs_drift / bars) ** np.arange(bars)
    vols = np.full(bars, vol)
    df = pd.DataFrame({
        "open": prices, "high": prices * 1.01, "low": prices * 0.99,
        "close": prices, "volume": vols,
    }, index=idx)
    path = tmp / f"{sym}.parquet"
    df.to_parquet(path)
    return path


def _make_alpha_board(tmp: Path, tickers: List[str], key: str = "items") -> Path:
    """Write a synthetic alpha discovery board sidecar."""
    data: Dict[str, Any] = {
        key: [{"ticker": t, "alpha_score": 80.0, "track": "Test"} for t in tickers]
    }
    path = tmp / "alpha_discovery_board_latest.json"
    path.write_text(json.dumps(data))
    return path


def _make_radar(tmp: Path, tickers: List[str], label: str = "RESET_WATCH") -> Path:
    data = {
        "candidates": [{"ticker": t, "priority_label": label} for t in tickers]
    }
    path = tmp / "daily_alpha_radar_latest.json"
    path.write_text(json.dumps(data))
    return path


def _make_social(tmp: Path, tickers: List[str]) -> Path:
    data = {
        "leads": [{"ticker": t, "score": 0.7, "label": "SOCIAL_ATTENTION_LEAD"} for t in tickers]
    }
    path = tmp / "social_attention_radar_latest.json"
    path.write_text(json.dumps(data))
    return path


# ── 1. Symbol validation ──────────────────────────────────────────────────────


def test_valid_equity_symbols():
    valid = ["AAPL", "NVDA", "AMD", "SPY", "QQQ", "A", "AA", "T", "META", "MSFT"]
    for s in valid:
        assert _is_valid_equity_symbol(s), f"{s} should be valid"


def test_invalid_warrant_symbols():
    warrants = ["AACIW", "ABVEW", "ACONW", "ADSEW", "AENTW", "ACAQW", "XYZWW"]
    for s in warrants:
        assert not _is_valid_equity_symbol(s), f"{s} should be rejected (warrant)"


def test_invalid_unit_symbols():
    units = ["BLNKU", "INPXU", "CHEAU"]
    for s in units:
        assert not _is_valid_equity_symbol(s), f"{s} should be rejected (unit)"


def test_rights_suffix_not_filtered():
    # R-suffix is NOT filtered: real companies like SPIR (Spire Global) end in R.
    # SPAC rights (BLNKR) are rare enough that false-positive cost exceeds benefit.
    # Operators must manually exclude any SPAC rights that slip through.
    real_companies = ["SPIR", "VIPR", "BLNKR"]
    for s in real_companies:
        assert _is_valid_equity_symbol(s), f"{s} should pass (R not filtered)"


def test_invalid_foreign_symbols():
    foreign = ["BRK.A", "BRK.B", "A.US", "X-Y"]
    for s in foreign:
        assert not _is_valid_equity_symbol(s), f"{s} should be rejected (foreign)"


# ── 2. Alpha board file discovery ─────────────────────────────────────────────


def test_alpha_board_reads_board_file(tmp_path):
    board_tickers = ["STLD", "NUE", "ODFL", "AJG", "SPOT"]
    _make_alpha_board(tmp_path, board_tickers, key="items")

    with patch("research.research_scanner.RESEARCH_DIR", tmp_path):
        result = _alpha_board_tickers()

    assert set(board_tickers).issubset(set(result)), (
        f"Alpha board tickers not loaded. got={result}"
    )
    assert len(result) >= len(board_tickers)


def test_alpha_board_missing_old_files_does_not_zero_board(tmp_path):
    """alpha_discovery_latest.json and overlay missing → board still loads from board file."""
    board_tickers = ["STLD", "NUE", "KLAC"]
    _make_alpha_board(tmp_path, board_tickers, key="items")
    # Do NOT create alpha_discovery_latest.json or alpha_discovery_overlay_latest.json

    with patch("research.research_scanner.RESEARCH_DIR", tmp_path):
        result = _alpha_board_tickers()

    assert set(board_tickers).issubset(set(result))


def test_alpha_board_overlay_zero_candidates_does_not_erase_board(tmp_path):
    """Overlay file with 0 candidates must not overwrite real board results."""
    board_tickers = ["MRVL", "VSH", "SANM"]
    _make_alpha_board(tmp_path, board_tickers, key="items")
    # Write overlay with 0 candidates
    overlay = tmp_path / "alpha_discovery_overlay_latest.json"
    overlay.write_text(json.dumps({"candidates": []}))

    with patch("research.research_scanner.RESEARCH_DIR", tmp_path):
        result = _alpha_board_tickers()

    assert set(board_tickers).issubset(set(result))


def test_alpha_board_deduplicates(tmp_path):
    """Same ticker in board + overlay should appear once."""
    board_tickers = ["NUE", "STLD"]
    _make_alpha_board(tmp_path, board_tickers, key="items")
    overlay = tmp_path / "alpha_discovery_overlay_latest.json"
    overlay.write_text(json.dumps({"candidates": [{"ticker": "NUE"}]}))

    with patch("research.research_scanner.RESEARCH_DIR", tmp_path):
        result = _alpha_board_tickers()

    assert result.count("NUE") == 1


def test_alpha_board_excludes_warrants(tmp_path):
    """Warrants in the board file must be stripped."""
    tickers = ["STLD", "AACIW", "ABVEW", "NUE"]  # two warrants
    _make_alpha_board(tmp_path, tickers, key="items")

    with patch("research.research_scanner.RESEARCH_DIR", tmp_path):
        result = _alpha_board_tickers()

    assert "AACIW" not in result
    assert "ABVEW" not in result
    assert "STLD" in result and "NUE" in result


# ── 3. Daily radar tickers ─────────────────────────────────────────────────────


def test_daily_radar_seeds_reset_watch(tmp_path):
    reset_tickers = ["SPIR", "ASX", "XPO"]
    _make_radar(tmp_path, reset_tickers, label="RESET_WATCH")

    with patch("research.research_scanner.RESEARCH_DIR", tmp_path):
        result = _daily_radar_tickers()

    assert set(reset_tickers).issubset(set(result))


def test_daily_radar_skips_extended_crowded(tmp_path):
    """EXTENDED_CROWDED label should NOT seed the universe from radar."""
    data = {
        "candidates": [
            {"ticker": "AAOI", "priority_label": "EXTENDED_CROWDED"},
            {"ticker": "SPIR", "priority_label": "RESET_WATCH"},
        ]
    }
    path = tmp_path / "daily_alpha_radar_latest.json"
    path.write_text(json.dumps(data))

    with patch("research.research_scanner.RESEARCH_DIR", tmp_path):
        result = _daily_radar_tickers()

    assert "AAOI" not in result
    assert "SPIR" in result


def test_daily_radar_missing_file_returns_empty(tmp_path):
    with patch("research.research_scanner.RESEARCH_DIR", tmp_path):
        result = _daily_radar_tickers()
    assert result == []


# ── 4. Ranked fill ────────────────────────────────────────────────────────────


def test_ranked_fill_selects_high_rs_over_low_rs(tmp_path):
    """High-RS / liquid name should rank above alphabetically-early weak name."""
    spy_closes = [100.0 + i * 0.1 for i in range(300)]
    # Write SPY parquet
    _make_parquet(tmp_path, "SPY", bars=300, price=100.0)

    # Strong name: ZVEC — alphabetically last, but high RS + liquid
    _make_parquet(tmp_path, "ZVEC", bars=200, price=50.0, vol=5_000_000, rs_drift=0.3)
    # Weak name: AAAA — alphabetically first, but low RS + illiquid
    _make_parquet(tmp_path, "AAAA", bars=200, price=5.0, vol=100, rs_drift=-0.1)

    with (
        patch("research.research_scanner.PRICE_DIR", tmp_path),
        patch("research.research_scanner._load_cached_frame") as mock_load,
    ):
        def _fake_load(sym):
            p = tmp_path / f"{sym}.parquet"
            if p.exists():
                return pd.read_parquet(p)
            return None
        mock_load.side_effect = _fake_load

        seen: set = set()
        result, meta = _ranked_cache_fill(needed=5, seen=seen)

    selected = [sym for sym, src in result]
    assert "ZVEC" in selected, f"Strong RS name ZVEC should be selected: {selected}"


def test_ranked_fill_excludes_warrant_symbols(tmp_path):
    """Warrant symbols must not appear in ranked fill output."""
    _make_parquet(tmp_path, "AACIW", bars=200, price=5.0, vol=1_000_000)
    _make_parquet(tmp_path, "NVDA", bars=200, price=200.0, vol=10_000_000)

    with (
        patch("research.research_scanner.PRICE_DIR", tmp_path),
        patch("research.research_scanner._load_cached_frame") as mock_load,
    ):
        def _fake_load(sym):
            p = tmp_path / f"{sym}.parquet"
            return pd.read_parquet(p) if p.exists() else None
        mock_load.side_effect = _fake_load

        result, _ = _ranked_cache_fill(needed=10, seen=set())

    selected = [sym for sym, _ in result]
    assert "AACIW" not in selected, "Warrant AACIW should not enter ranked fill"


def test_ranked_fill_excludes_penny_stocks(tmp_path):
    """Stocks with last price < $2 should be excluded."""
    _make_parquet(tmp_path, "JUNK", bars=100, price=0.50, vol=5_000_000)
    _make_parquet(tmp_path, "GOOD", bars=100, price=15.0, vol=1_000_000)

    with (
        patch("research.research_scanner.PRICE_DIR", tmp_path),
        patch("research.research_scanner._load_cached_frame") as mock_load,
    ):
        def _fake_load(sym):
            p = tmp_path / f"{sym}.parquet"
            return pd.read_parquet(p) if p.exists() else None
        mock_load.side_effect = _fake_load

        result, _ = _ranked_cache_fill(needed=10, seen=set())

    selected = [sym for sym, _ in result]
    assert "JUNK" not in selected, "Penny stock JUNK should be excluded"
    assert "GOOD" in selected, "Valid stock GOOD should be included"


def test_ranked_fill_returns_metadata(tmp_path):
    """Ranked fill metadata should report candidates_considered, scored, selected."""
    _make_parquet(tmp_path, "XTEST", bars=100, price=10.0, vol=1_000_000)

    with (
        patch("research.research_scanner.PRICE_DIR", tmp_path),
        patch("research.research_scanner._load_cached_frame") as mock_load,
    ):
        def _fake_load(sym):
            p = tmp_path / f"{sym}.parquet"
            return pd.read_parquet(p) if p.exists() else None
        mock_load.side_effect = _fake_load

        _, meta = _ranked_cache_fill(needed=5, seen=set())

    assert "candidates_considered" in meta
    assert "candidates_scored" in meta
    assert "selected" in meta


# ── 5. Full universe build ─────────────────────────────────────────────────────


def test_build_universe_alpha_board_contributes(tmp_path):
    """Alpha board tickers must appear in the universe."""
    board_tickers = ["STLD", "NUE", "ODFL"]
    _make_alpha_board(tmp_path, board_tickers, key="items")

    with (
        patch("research.research_scanner.RESEARCH_DIR", tmp_path),
        patch("research.research_scanner._social_arb_tickers", return_value=[]),
        patch("research.research_scanner._ranked_cache_fill", return_value=([], {})),
    ):
        universe, build_info = _build_universe(cap=50)

    assert set(board_tickers).issubset(set(universe))
    assert build_info["source_counts"]["alpha_board"] == len(board_tickers)


def test_build_universe_no_alphabetical_fallback_when_ranked_fills(tmp_path):
    """When ranked fill covers the gap, alphabetical fallback must not be used."""
    _make_alpha_board(tmp_path, ["STLD"], key="items")

    # Return enough ranked fill candidates
    ranked = [(f"S{i:03d}", "ranked_fill") for i in range(200)]
    with (
        patch("research.research_scanner.RESEARCH_DIR", tmp_path),
        patch("research.research_scanner._social_arb_tickers", return_value=[]),
        patch("research.research_scanner._ranked_cache_fill", return_value=(ranked, {})),
    ):
        universe, build_info = _build_universe(cap=100)

    assert not build_info["used_alphabetical_fallback"]
    assert build_info["source_counts"]["alphabetical_fallback"] == 0


def test_build_universe_not_dominated_by_alphabetical_early_tickers(tmp_path):
    """Universe with ranked fill should NOT be dominated by A-range tickers."""
    # Simulate 60 alpha/social names from M-Z range, 140 ranked fill from various letters
    alpha_names = [f"M{i:02d}" for i in range(10)] + [f"N{i:02d}" for i in range(10)]
    ranked = (
        [(f"Z{i:03d}", "ranked_fill") for i in range(40)] +
        [(f"S{i:03d}", "ranked_fill") for i in range(40)] +
        [(f"A{i:03d}", "ranked_fill") for i in range(20)]  # some A-range too
    )

    with (
        patch("research.research_scanner.RESEARCH_DIR", tmp_path),
        patch("research.research_scanner._alpha_board_tickers", return_value=alpha_names),
        patch("research.research_scanner._daily_radar_tickers", return_value=[]),
        patch("research.research_scanner._social_arb_tickers", return_value=[]),
        patch("research.research_scanner._ranked_cache_fill", return_value=(ranked, {})),
    ):
        # Empty directory so alpha board file lookup returns no extra files
        (tmp_path / "alpha_discovery_board_latest.json").write_text("{}")
        universe, build_info = _build_universe(cap=100)

    # Count how many start with 'A' prefix at positions 0-1
    a_prefix_count = sum(1 for t in universe if t.startswith("A"))
    total = len(universe)
    assert total > 0
    a_fraction = a_prefix_count / total
    # With ranked fill from Z and S ranges, A should not dominate
    assert a_fraction < 0.5, (
        f"A-range tickers dominate ({a_prefix_count}/{total} = {a_fraction:.1%})"
    )


def test_build_universe_mz_tickers_can_be_selected(tmp_path):
    """Strong M-Z names should enter the universe when ranked fill is enabled."""
    mz_names = ["MRVL", "NVDA", "SMCI", "TSM", "LRCX"]
    ranked = [(t, "ranked_fill") for t in mz_names]

    with (
        patch("research.research_scanner.RESEARCH_DIR", tmp_path),
        patch("research.research_scanner._alpha_board_tickers", return_value=[]),
        patch("research.research_scanner._daily_radar_tickers", return_value=[]),
        patch("research.research_scanner._social_arb_tickers", return_value=[]),
        patch("research.research_scanner._ranked_cache_fill", return_value=(ranked, {})),
    ):
        (tmp_path / "alpha_discovery_board_latest.json").write_text("{}")
        universe, build_info = _build_universe(cap=20)

    for t in mz_names:
        assert t in universe, f"{t} should be in universe via ranked fill"


def test_build_universe_source_counts_in_build_info(tmp_path):
    """build_info must include per-source counts and top_50 list."""
    board_tickers = ["STLD", "NUE"]
    radar_tickers = ["SPIR"]
    social_tickers = ["MRVL", "MTSI"]
    ranked = [("KLAC", "ranked_fill"), ("LRCX", "ranked_fill")]

    _make_alpha_board(tmp_path, board_tickers, key="items")
    _make_radar(tmp_path, radar_tickers)
    _make_social(tmp_path, social_tickers)

    with (
        patch("research.research_scanner.RESEARCH_DIR", tmp_path),
        patch("research.research_scanner._ranked_cache_fill", return_value=(ranked, {})),
    ):
        universe, build_info = _build_universe(cap=50)

    assert "source_counts" in build_info
    sc = build_info["source_counts"]
    assert sc["alpha_board"] == len(board_tickers)
    assert sc["daily_alpha_radar"] == len(radar_tickers)
    assert sc["social_arb"] == len(social_tickers)
    assert sc["ranked_fill"] == len(ranked)
    assert "top_50" in build_info
    assert "used_alphabetical_fallback" in build_info
    assert "generated_at" in build_info


def test_build_universe_warrants_excluded(tmp_path):
    """SPAC warrants should never appear in the final universe."""
    board_tickers = ["STLD", "AACIW", "ABVEW", "NUE"]  # two warrants

    _make_alpha_board(tmp_path, board_tickers, key="items")

    with (
        patch("research.research_scanner.RESEARCH_DIR", tmp_path),
        patch("research.research_scanner._social_arb_tickers", return_value=["AENTW"]),
        patch("research.research_scanner._ranked_cache_fill", return_value=([], {})),
    ):
        universe, build_info = _build_universe(cap=50)

    for warrant in ["AACIW", "ABVEW", "AENTW"]:
        assert warrant not in universe, f"Warrant {warrant} leaked into universe"


def test_build_universe_miss_diagnostic_contains_expected_keys(tmp_path):
    """Miss diagnostic must report in_price_cache, in_universe, source for tracked tickers."""
    _make_alpha_board(tmp_path, ["STLD"], key="items")

    with (
        patch("research.research_scanner.RESEARCH_DIR", tmp_path),
        patch("research.research_scanner._social_arb_tickers", return_value=[]),
        patch("research.research_scanner._ranked_cache_fill", return_value=([], {})),
    ):
        _, build_info = _build_universe(cap=50)

    diag = build_info.get("miss_diagnostic", {})
    # Must exist as a dict
    assert isinstance(diag, dict)
    # Check structural keys for at least one entry
    for sym, entry in diag.items():
        assert "in_price_cache" in entry
        assert "in_universe" in entry
        assert "absent_reason" in entry
        break  # only need to verify one


# ── 6. Alphabetical fallback sanity ──────────────────────────────────────────


def test_alphabetical_fallback_triggers_only_when_ranked_fill_empty(tmp_path):
    """Alphabetical fallback should only fire when ranked fill returns nothing."""
    # Write one parquet in the price dir
    _make_parquet(tmp_path, "AAAA", bars=80, price=5.0)

    with (
        patch("research.research_scanner.RESEARCH_DIR", tmp_path),
        patch("research.research_scanner.PRICE_DIR", tmp_path),
        patch("research.research_scanner._alpha_board_tickers", return_value=[]),
        patch("research.research_scanner._daily_radar_tickers", return_value=[]),
        patch("research.research_scanner._social_arb_tickers", return_value=[]),
        patch("research.research_scanner._ranked_cache_fill", return_value=([], {})),
    ):
        (tmp_path / "alpha_discovery_board_latest.json").write_text("{}")
        universe, build_info = _build_universe(cap=5)

    # Alphabetical fallback should have fired since ranked fill returned nothing
    assert build_info["used_alphabetical_fallback"] is True


# ── 7. Social score computation ───────────────────────────────────────────────


def test_social_score_from_velocity_and_novelty():
    """New radar items with attention_velocity_score should produce a nonzero score."""
    item = {
        "attention_velocity_score": 72.2,
        "attention_novelty_score": 46.0,
        "mention_z_score": 1.225,
        "best_confidence": 0.85,
    }
    score = _social_score_from_item(item)
    assert 0.0 < score <= 1.0, f"Score should be in (0, 1], got {score}"
    assert score > 0.3, f"Score should be > 0.3 for moderate attention, got {score}"


def test_social_score_high_novelty_high_velocity():
    """High velocity + high novelty → score near top of range."""
    item = {
        "attention_velocity_score": 90.0,
        "attention_novelty_score": 100.0,
        "mention_z_score": 2.0,
        "best_confidence": 0.9,
    }
    score = _social_score_from_item(item)
    assert score > 0.5, f"High-attention item should score > 0.5, got {score}"


def test_social_score_zero_for_no_attention():
    """Item with no attention metrics should score 0."""
    item = {
        "attention_velocity_score": 0.0,
        "attention_novelty_score": 0.0,
        "mention_z_score": 0.0,
        "best_confidence": 0.85,
    }
    score = _social_score_from_item(item)
    assert score == 0.0, f"Zero-attention item should score 0.0, got {score}"


def test_social_score_low_confidence_discounts():
    """Low-confidence ticker mapping should reduce the score."""
    item_high_conf = {
        "attention_velocity_score": 70.0,
        "attention_novelty_score": 50.0,
        "mention_z_score": 1.0,
        "best_confidence": 0.9,
    }
    item_low_conf = dict(item_high_conf, best_confidence=0.3)
    high = _social_score_from_item(item_high_conf)
    low = _social_score_from_item(item_low_conf)
    assert low < high, f"Low-confidence score ({low}) should be less than high ({high})"


def test_social_score_legacy_field_takes_priority():
    """Legacy 'score' field on old sidecars should override new computation."""
    item = {
        "score": 0.75,
        "attention_velocity_score": 10.0,  # would give much lower score
        "attention_novelty_score": 5.0,
    }
    score = _social_score_from_item(item)
    assert score == 0.75, f"Legacy score field should take priority, got {score}"


def test_social_score_differentiates_tickers():
    """Different attention levels should produce different scores (no flat 0.0)."""
    high_item = {
        "attention_velocity_score": 80.0, "attention_novelty_score": 90.0,
        "best_confidence": 0.85,
    }
    low_item = {
        "attention_velocity_score": 20.0, "attention_novelty_score": 10.0,
        "best_confidence": 0.85,
    }
    high_score = _social_score_from_item(high_item)
    low_score = _social_score_from_item(low_item)
    assert high_score > low_score, "High-attention item should score higher"
    assert low_score > 0.0, "Even low-attention item should have nonzero score"


def test_load_social_data_uses_radar_scores(tmp_path):
    """_load_social_data should produce nonzero scores from the radar format."""
    radar_data = {
        "leads": [
            {
                "ticker": "MRVL",
                "attention_velocity_score": 72.0,
                "attention_novelty_score": 60.0,
                "mention_z_score": 1.5,
                "best_confidence": 0.85,
                "crowd_stage": "STEALTH_ATTENTION",
                "label": "SOCIAL_ATTENTION_LEAD",
            },
            {
                "ticker": "XPO",
                "attention_velocity_score": 40.0,
                "attention_novelty_score": 20.0,
                "mention_z_score": 0.5,
                "best_confidence": 0.85,
                "crowd_stage": "STEALTH_ATTENTION",
                "label": "SOCIAL_ATTENTION_LEAD",
            },
        ]
    }
    radar_file = tmp_path / "social_attention_radar_latest.json"
    radar_file.write_text(json.dumps(radar_data))

    with patch("research.research_scanner.RESEARCH_DIR", tmp_path):
        social = _load_social_data()

    assert "MRVL" in social
    assert "XPO" in social
    assert social["MRVL"]["score"] > 0.0, "MRVL should have nonzero social score"
    assert social["XPO"]["score"] > 0.0, "XPO should have nonzero social score"
    assert social["MRVL"]["score"] > social["XPO"]["score"], (
        "Higher-attention MRVL should score more than lower-attention XPO"
    )


def test_load_social_data_crowded_stage(tmp_path):
    """crowd_stage TRENDING should set crowded=True."""
    radar_data = {
        "leads": [
            {
                "ticker": "AAPL",
                "attention_velocity_score": 90.0,
                "attention_novelty_score": 80.0,
                "best_confidence": 0.9,
                "crowd_stage": "TRENDING",
                "label": "SOCIAL_ATTENTION_LEAD",
            },
            {
                "ticker": "MRVL",
                "attention_velocity_score": 60.0,
                "attention_novelty_score": 50.0,
                "best_confidence": 0.85,
                "crowd_stage": "STEALTH_ATTENTION",
                "label": "SOCIAL_ATTENTION_LEAD",
            },
        ]
    }
    radar_file = tmp_path / "social_attention_radar_latest.json"
    radar_file.write_text(json.dumps(radar_data))

    with patch("research.research_scanner.RESEARCH_DIR", tmp_path):
        social = _load_social_data()

    assert social["AAPL"]["crowded"] is True, "TRENDING stage → crowded=True"
    assert social["MRVL"]["crowded"] is False, "STEALTH_ATTENTION → crowded=False"


# ── 8. Sector attribution ─────────────────────────────────────────────────────


def test_fmp_sector_to_etf_mapping_common_sectors():
    """Key FMP sector names must map to the correct GICS ETFs."""
    assert _FMP_SECTOR_TO_ETF["technology"] == "XLK"
    assert _FMP_SECTOR_TO_ETF["financial services"] == "XLF"
    assert _FMP_SECTOR_TO_ETF["basic materials"] == "XLB"
    assert _FMP_SECTOR_TO_ETF["healthcare"] == "XLV"
    assert _FMP_SECTOR_TO_ETF["industrials"] == "XLI"
    assert _FMP_SECTOR_TO_ETF["consumer cyclical"] == "XLY"
    assert _FMP_SECTOR_TO_ETF["energy"] == "XLE"
    assert _FMP_SECTOR_TO_ETF["communication services"] == "XLC"


def test_enrich_item_adds_company_sector_etf():
    """_enrich_item should add company_sector_etf from FMP profile sector."""
    item = {
        "ticker": "MRVL",
        "category": "sector_theme_leader",
        "leading_sector_etfs": ["XLK", "XLI"],
    }
    profile = {"sector": "Technology", "companyName": "Marvell Technology"}
    enriched = _enrich_item(item, profile)

    assert enriched["company_sector_etf"] == "XLK", (
        f"Technology should map to XLK, got {enriched['company_sector_etf']}"
    )


def test_enrich_item_company_in_leading_sector_true():
    """Tech company should show company_in_leading_sector=True when XLK is leading."""
    item = {
        "ticker": "MRVL",
        "category": "sector_theme_leader",
        "leading_sector_etfs": ["XLK", "XLB", "XLI", "XLF"],
    }
    profile = {"sector": "Technology"}
    enriched = _enrich_item(item, profile)

    assert enriched["company_in_leading_sector"] is True


def test_enrich_item_company_not_in_leading_sector():
    """Financial company should show company_in_leading_sector=False when XLF not leading."""
    item = {
        "ticker": "AJG",
        "category": "sector_theme_leader",
        "leading_sector_etfs": ["XLK", "XLI"],  # XLF not in list
    }
    profile = {"sector": "Financial Services"}
    enriched = _enrich_item(item, profile)

    assert enriched["company_sector_etf"] == "XLF"
    assert enriched["company_in_leading_sector"] is False


def test_enrich_item_unknown_sector_gives_null_etf():
    """Unknown sector string should give company_sector_etf=None."""
    item = {
        "ticker": "XYZ",
        "category": "early_accumulation",
        "leading_sector_etfs": [],
    }
    profile = {"sector": "Cryptocurrency"}  # not in mapping
    enriched = _enrich_item(item, profile)

    assert enriched["company_sector_etf"] is None


def test_enrich_item_sector_attribution_case_insensitive():
    """Sector mapping should be case-insensitive."""
    item = {"ticker": "AAPL", "category": "sector_theme_leader", "leading_sector_etfs": ["XLK"]}
    profile_lower = {"sector": "technology"}
    profile_upper = {"sector": "TECHNOLOGY"}
    profile_mixed = {"sector": "Technology"}

    for p in [profile_lower, profile_upper, profile_mixed]:
        enriched = _enrich_item(item, p)
        assert enriched["company_sector_etf"] == "XLK", (
            f"Sector '{p['sector']}' should map to XLK"
        )


def test_enrich_item_materials_company_correctly_attributed():
    """Basic Materials company (NUE, STLD) should map to XLB."""
    item = {
        "ticker": "NUE",
        "category": "sector_theme_leader",
        "leading_sector_etfs": ["XLB", "XLF", "XLI", "XLK"],
    }
    profile = {"sector": "Basic Materials"}
    enriched = _enrich_item(item, profile)

    assert enriched["company_sector_etf"] == "XLB"
    assert enriched["company_in_leading_sector"] is True


def test_enrich_item_no_profile_sector_etf_none():
    """When profile is None, company_sector_etf should be None."""
    item = {"ticker": "UNKN", "category": "sector_theme_leader", "leading_sector_etfs": ["XLK"]}
    enriched = _enrich_item(item, None)
    assert enriched["company_sector_etf"] is None

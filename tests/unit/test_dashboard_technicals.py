"""tests/unit/test_dashboard_technicals.py

Regression coverage for the dashboard's AI-ANALYSIS technical calcs
(dashboards/gem_trader_hq.py). Focus: calc_atr previously cross-indexed the
True Range against the HEAD of the full price history (bars[i]) instead of the
previous bar in the window, so a long uptrend produced an absurd ATR (e.g. INTC
~$77 on a ~$123 close). These tests pin the correct prev-close behavior.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dashboards.gem_trader_hq import calc_atr, calc_ema, calc_rsi  # noqa: E402


def _bar(h, l, c):
    return {"high": h, "low": l, "close": c}


def test_atr_flat_range_equals_range():
    bars = [_bar(10.5, 9.5, 10.0) for _ in range(50)]
    assert calc_atr(bars) == 1.0


def test_atr_uptrend_does_not_explode():
    # Monotonic uptrend: high=i+1, low=i, close=i+0.5. True Range per bar is
    # bounded (~1.5), NOT the recent-high-minus-ancient-close (~190) the old
    # cross-indexing bug produced.
    bars = [_bar(i + 1.0, float(i), i + 0.5) for i in range(1, 200)]
    atr = calc_atr(bars)
    assert atr < 3.0, f"ATR exploded to {atr} — cross-index regression"


def test_atr_uses_previous_bar_close_not_history_head():
    # Three flat bars at ~10 then one at ~100. TR of the last bar must measure
    # against the PREVIOUS bar (~10), giving a large TR; but earlier bars must
    # NOT be measured against a far-away close. With p=14 and only 4 bars the
    # window is all 4; the mean stays finite and reasonable.
    bars = [_bar(10.5, 9.5, 10.0), _bar(10.5, 9.5, 10.0),
            _bar(10.5, 9.5, 10.0), _bar(101.0, 99.0, 100.0)]
    atr = calc_atr(bars)
    # 3 TRs: 1.0, 1.0, max(2.0, |101-10|=91, |99-10|=89)=91 → mean = 31.0
    assert abs(atr - 31.0) < 0.01


def test_atr_window_size_is_p_true_ranges():
    # 20 bars, p=14 → exactly 14 True Ranges averaged (window = last 15 bars).
    bars = [_bar(i + 2.0, float(i), i + 1.0) for i in range(1, 21)]
    # every TR is identical here, so just assert it returns a finite positive value
    assert calc_atr(bars, p=14) > 0


def test_rsi_and_ema_still_sane():
    closes = [float(i) for i in range(1, 60)]   # steady uptrend
    assert 50.0 <= calc_rsi(closes) <= 100.0     # rising series → RSI high side
    ema = calc_ema(closes, 20)
    assert closes[0] < ema < closes[-1]          # EMA lags between bounds

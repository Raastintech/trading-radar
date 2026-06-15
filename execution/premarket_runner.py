"""
execution/premarket_runner.py — Lightweight read-only research runs.

Called by main.py at session-state transitions:
  run_premarket_brief()  — on entry to PREMARKET (04:00 ET)
  run_postmarket_brief() — on entry to POSTMARKET (16:00 ET)

Neither function places orders.  Both refresh FMP data into the Gatekeeper
cache so the REGULAR session begins with fresh context.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
_ET = ZoneInfo("America/New_York")


def run_premarket_brief(fmp, dec_log=None) -> None:
    """
    Pre-market research pass (read-only).

    Refreshes:
      • FMP economic calendar  (upcoming high-impact events)
      • FMP earnings calendar  (next 7 days)
      • FMP treasury rates     (yield curve snapshot)
      • FMP VIX quote          (overnight reading)

    Logs a structured pre-market briefing to the INFO stream.
    """
    now = datetime.now(_ET)
    logger.info("=" * 60)
    logger.info("PRE-MARKET BRIEF  %s ET", now.strftime("%Y-%m-%d %H:%M"))
    logger.info("=" * 60)

    # ── VIX overnight reading ─────────────────────────────────────────────────
    try:
        vix = fmp.get_vix()
        if vix is not None:
            lbl = _vix_label(vix)
            logger.info("VIX overnight: %.1f  (%s)", vix, lbl)
        else:
            logger.warning("VIX unavailable pre-market")
    except Exception as exc:
        logger.warning("Pre-market VIX fetch failed: %s", exc)

    # ── Treasury yield curve ──────────────────────────────────────────────────
    try:
        rates = fmp.get_treasury_rates()
        if rates:
            y2  = rates.get("year2",  rates.get("twoYear",  0)) or 0
            y10 = rates.get("year10", rates.get("tenYear",  0)) or 0
            y30 = rates.get("year30", rates.get("thirtyYear",0)) or 0
            spread = float(y10) - float(y2)
            curve_lbl = "normal" if spread >= 0 else "inverted"
            logger.info(
                "Yields: 2y=%.2f%%  10y=%.2f%%  30y=%.2f%%  "
                "2s10s_spread=%.0fbps (%s)",
                float(y2), float(y10), float(y30),
                spread * 100, curve_lbl,
            )
    except Exception as exc:
        logger.warning("Pre-market treasury rates failed: %s", exc)

    # ── Economic calendar ─────────────────────────────────────────────────────
    try:
        cal = fmp.get_economic_calendar(days_ahead=3)
        high = [e for e in cal if str(e.get("impact", "")).lower() == "high"]
        if high:
            logger.info("High-impact events next 3 days: %d", len(high))
            for e in high[:5]:
                logger.info(
                    "  %s  %s  %s  forecast=%s  prev=%s",
                    e.get("date", "?")[:16],
                    e.get("country", ""),
                    e.get("event", "?")[:40],
                    e.get("estimate", "?"),
                    e.get("previous", "?"),
                )
        else:
            logger.info("Economic calendar: no high-impact events in next 3 days")
        # Refresh macro events into SQLite if dec_log provided
        if dec_log is not None:
            dec_log.refresh_macro_events(cal)
    except Exception as exc:
        logger.warning("Pre-market econ calendar failed: %s", exc)

    # ── Earnings look-ahead ───────────────────────────────────────────────────
    try:
        earnings = fmp.get_earnings_calendar(days_ahead=7)
        if earnings:
            logger.info("Earnings next 7 days: %d companies", len(earnings))
            for e in earnings[:5]:
                logger.info(
                    "  %s  %s  est=%.2f  time=%s",
                    e.get("date", "?"),
                    e.get("symbol", "?"),
                    float(e.get("epsEstimated") or e.get("eps_estimate") or 0),
                    e.get("time", "?"),
                )
        else:
            logger.info("Earnings calendar: no events in next 7 days")
    except Exception as exc:
        logger.warning("Pre-market earnings calendar failed: %s", exc)

    logger.info("Pre-market brief complete.")
    logger.info("-" * 60)


def run_postmarket_brief(fmp, alpaca, dec_log=None) -> None:
    """
    Post-market research pass (read-only).

    Refreshes:
      • FMP VIX closing reading
      • FMP SPY EOD bars (regime snapshot for overnight/tomorrow)
      • Recent earnings surprises (past 2 days — ShortSleeve feed)
      • News sentiment refresh for positions (if dec_log provided)

    Logs a structured end-of-day briefing to the INFO stream.
    """
    now = datetime.now(_ET)
    logger.info("=" * 60)
    logger.info("POST-MARKET BRIEF  %s ET", now.strftime("%Y-%m-%d %H:%M"))
    logger.info("=" * 60)

    # ── VIX close ─────────────────────────────────────────────────────────────
    try:
        vix = fmp.get_vix()
        if vix is not None:
            logger.info("VIX close: %.1f  (%s)", vix, _vix_label(vix))
    except Exception as exc:
        logger.warning("Post-market VIX failed: %s", exc)

    # ── SPY EOD bar ───────────────────────────────────────────────────────────
    try:
        spy_bars = fmp.get_spy_bars(days=10)
        if spy_bars:
            latest = spy_bars[-1]
            prev   = spy_bars[-2] if len(spy_bars) >= 2 else latest
            chg_pct = (float(latest["close"]) - float(prev["close"])) / float(prev["close"]) * 100
            logger.info(
                "SPY close: %.2f  (%+.2f%%)  vol=%s",
                float(latest["close"]), chg_pct,
                _fmt_vol(int(latest.get("volume", 0))),
            )
    except Exception as exc:
        logger.warning("Post-market SPY bars failed: %s", exc)

    # ── Recent earnings surprises ─────────────────────────────────────────────
    try:
        hist = fmp.get_past_earnings(lookback_days=2)
        surprises = []
        for e in hist:
            est = e.get("epsEstimated") or e.get("eps_estimate")
            act = e.get("eps") or e.get("eps_actual")
            if est is not None and act is not None:
                try:
                    miss = float(act) < float(est) * 0.90  # >10% miss
                    beat = float(act) > float(est) * 1.10  # >10% beat
                    if miss or beat:
                        surprises.append((e.get("symbol","?"), "MISS" if miss else "BEAT",
                                          float(est), float(act)))
                except (ValueError, TypeError):
                    pass
        if surprises:
            logger.info("Earnings surprises (past 2 days): %d", len(surprises))
            for sym, verdict, est, act in surprises[:10]:
                logger.info("  %-8s  %-4s  est=%.2f  act=%.2f", sym, verdict, est, act)
        else:
            logger.info("Earnings surprises: none in past 2 days")
    except Exception as exc:
        logger.warning("Post-market historical earnings failed: %s", exc)

    # ── Open positions summary ────────────────────────────────────────────────
    try:
        positions = alpaca.get_positions()
        if positions:
            total_pnl = sum(p.get("unrealized_pnl", 0) for p in positions)
            logger.info(
                "Open positions: %d  total_unrealized_pnl=$%.2f",
                len(positions), total_pnl,
            )
            for p in positions:
                logger.info(
                    "  %-8s  %s  qty=%.0f  entry=%.2f  now=%.2f  pnl=$%.2f",
                    p["ticker"], p["side"].upper(),
                    p["qty"], p["entry_price"], p["current_price"],
                    p["unrealized_pnl"],
                )
        else:
            logger.info("Open positions: 0 (flat)")
    except Exception as exc:
        logger.warning("Post-market positions summary failed: %s", exc)

    logger.info("Post-market brief complete.")
    logger.info("-" * 60)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _vix_label(v: float) -> str:
    if v < 15:  return "CALM"
    if v < 20:  return "LOW"
    if v < 25:  return "MODERATE"
    if v < 30:  return "ELEVATED"
    if v < 40:  return "HIGH"
    return "EXTREME"


def _fmt_vol(v: int) -> str:
    if v >= 1_000_000: return f"{v/1_000_000:.1f}M"
    if v >= 1_000:     return f"{v/1_000:.0f}K"
    return str(v)

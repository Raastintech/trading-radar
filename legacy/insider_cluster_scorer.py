"""
Insider Cluster Scorer — Form 4 Open-Market Purchase Signal

Source: yfinance insider_transactions (free, no paid API required)

Logic:
  - Filter to open-market PURCHASES only (exclude sales, option exercises)
  - Apply role weight: CEO=1.0, CFO=0.9, President=0.85, Director=0.6, Other=0.4
  - Apply recency decay: exp(-k * days_ago), half-life = 30 days
  - Cluster bonus: +20% for 2+ insiders, +40% for 3+ insiders
  - Normalize to 0-100

Score interpretation:
  0-30  = No meaningful insider activity
  30-60 = Some buying, moderate signal
  60-80 = Notable cluster buying
  80+   = Strong multi-insider cluster

Usage:
    from insider_cluster_scorer import InsiderClusterScorer
    ics = InsiderClusterScorer()
    result = ics.score("AAPL")
    # result = {"insider_cluster_score": 72.0, "buy_count": 3, ...}

    # Also optionally caches to DB
    ics.score("NVDA", save_to_db=True)
"""

import math
import time
import sqlite3
import os
import json
from datetime import datetime, timezone
from typing import Optional

_TXN_CACHE: dict = {}
_CACHE_TTL = 3600  # 1 hr

DB_PATH = os.environ.get("TRADING_DB_PATH", "trading_performance.db")

# Role weight table (substring match, case-insensitive)
_ROLE_WEIGHTS = [
    ("chief executive",   1.00),
    ("ceo",               1.00),
    ("chief financial",   0.90),
    ("cfo",               0.90),
    ("president",         0.85),
    ("chief operating",   0.80),
    ("coo",               0.80),
    ("chief technology",  0.75),
    ("cto",               0.75),
    ("evp",               0.70),
    ("executive vice",    0.70),
    ("svp",               0.65),
    ("senior vice",       0.65),
    ("director",          0.60),
    ("vp ",               0.55),
    ("vice president",    0.55),
    ("officer",           0.50),
    ("general counsel",   0.50),
    ("10%",               0.45),  # large shareholder
]
_DEFAULT_WEIGHT = 0.40

_DECAY_HALF_LIFE_DAYS = 30.0
_DECAY_K = math.log(2) / _DECAY_HALF_LIFE_DAYS

# Raw score normalization ceiling — anything above this maps to 100
_SCORE_CEILING = 8.0


def _role_weight(title: str) -> float:
    t = (title or "").lower()
    for key, w in _ROLE_WEIGHTS:
        if key in t:
            return w
    return _DEFAULT_WEIGHT


def _recency_decay(days_ago: float) -> float:
    """Exponential decay; 1.0 today, 0.5 at 30 days, 0.25 at 60 days."""
    return math.exp(-_DECAY_K * max(0.0, days_ago))


def _cache_get(key: str) -> Optional[object]:
    entry = _TXN_CACHE.get(key)
    if entry and (time.time() - entry["_ts"]) < _CACHE_TTL:
        return entry["data"]
    return None


def _cache_set(key: str, data) -> None:
    _TXN_CACHE[key] = {"data": data, "_ts": time.time()}


class InsiderClusterScorer:
    """Score insider cluster buying from yfinance Form 4 data."""

    def score(self, ticker: str, save_to_db: bool = False) -> dict:
        try:
            return self._score_impl(ticker, save_to_db)
        except Exception as exc:
            return self._neutral(ticker, note=f"Error: {exc}")

    # -----------------------------------------------------------------------
    # implementation
    # -----------------------------------------------------------------------

    def _score_impl(self, ticker: str, save_to_db: bool) -> dict:
        import yfinance as yf

        t = ticker.upper()
        cached = _cache_get(t)
        if cached is not None:
            return cached

        yfobj = yf.Ticker(t)

        try:
            txns = yfobj.insider_transactions
        except Exception:
            txns = None

        if txns is None or (hasattr(txns, "empty") and txns.empty):
            result = self._neutral(t, note="No insider transaction data")
            _cache_set(t, result)
            return result

        now = datetime.now(timezone.utc)

        buy_events = []

        for _, row in txns.iterrows():
            # Filter: open-market purchases only
            text = str(row.get("Text", "") or row.get("Transaction", "") or "").lower()
            if "purchase" not in text and "buy" not in text:
                continue

            shares = row.get("Shares") or row.get("Value")
            price  = row.get("Value") if row.get("Shares") else None
            if isinstance(shares, str):
                try:
                    shares = float(shares.replace(",", ""))
                except Exception:
                    shares = None

            # Parse date
            date_val = row.get("Date") or row.get("Start Date")
            if date_val is None:
                continue
            try:
                if hasattr(date_val, "to_pydatetime"):
                    dt = date_val.to_pydatetime()
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                else:
                    dt = datetime.fromisoformat(str(date_val)).replace(tzinfo=timezone.utc)
            except Exception:
                continue

            days_ago = max(0.0, (now - dt).total_seconds() / 86400.0)
            if days_ago > 180:
                continue  # ignore very stale filings

            insider_name  = str(row.get("Insider", "") or row.get("Name", "") or "")
            insider_title = str(row.get("Title", "") or row.get("Position", "") or "")

            weight  = _role_weight(insider_title)
            decay   = _recency_decay(days_ago)
            notional = None

            if shares and price and price > 0:
                notional = float(shares) * float(price)
            elif shares:
                try:
                    notional = float(shares)
                except Exception:
                    pass

            buy_events.append({
                "name":     insider_name,
                "title":    insider_title,
                "days_ago": round(days_ago, 1),
                "weight":   weight,
                "decay":    round(decay, 4),
                "notional": round(notional, 0) if notional else None,
            })

        if not buy_events:
            result = self._neutral(t, note="No open-market purchases found")
            _cache_set(t, result)
            return result

        # ── Raw score: sum of weight × decay per event ────────────────────
        raw = sum(e["weight"] * e["decay"] for e in buy_events)

        # ── Cluster bonus ─────────────────────────────────────────────────
        unique_names = len({e["name"] for e in buy_events if e["name"]})
        if unique_names >= 3:
            raw *= 1.40
        elif unique_names >= 2:
            raw *= 1.20

        # ── Normalize to 0-100 ────────────────────────────────────────────
        score = round(min(100.0, (raw / _SCORE_CEILING) * 100.0), 1)

        # ── Optional DB save ──────────────────────────────────────────────
        if save_to_db:
            self._save_events(t, buy_events)

        note = (
            f"{len(buy_events)} purchases by {unique_names} insider(s) "
            f"in last 180d; raw={raw:.2f}"
        )

        result = {
            "insider_cluster_score": score,
            "buy_count":    len(buy_events),
            "unique_buyers": unique_names,
            "raw_score":    round(raw, 4),
            "cluster_bonus": unique_names >= 2,
            "events":       buy_events,
            "data_quality": "FULL",
            "note":         note,
        }
        _cache_set(t, result)
        return result

    # -----------------------------------------------------------------------
    # DB persistence
    # -----------------------------------------------------------------------

    @staticmethod
    def _save_events(ticker: str, events: list[dict], db_path: str = DB_PATH) -> None:
        try:
            conn = sqlite3.connect(db_path)
            cur  = conn.cursor()
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='insider_events'"
            )
            if not cur.fetchone():
                conn.close()
                return
            cur.execute("PRAGMA table_info(insider_events)")
            cols = {row[1] for row in cur.fetchall()}

            ts = datetime.now(timezone.utc).isoformat()
            for e in events:
                payload = {}
                if "fetched_at" in cols:
                    payload["fetched_at"] = ts
                if "timestamp_utc" in cols:
                    payload["timestamp_utc"] = ts
                if "ticker" in cols:
                    payload["ticker"] = ticker
                if "insider_name" in cols:
                    payload["insider_name"] = e.get("name")
                if "insider_title" in cols:
                    payload["insider_title"] = e.get("title")
                if "transaction_type" in cols:
                    payload["transaction_type"] = "Purchase"
                if "action" in cols:
                    payload["action"] = "BUY"
                if "shares" in cols:
                    payload["shares"] = None
                if "price" in cols:
                    payload["price"] = None
                if "notional" in cols:
                    payload["notional"] = e.get("notional")
                if "value_usd" in cols:
                    payload["value_usd"] = e.get("notional")
                if "filing_date" in cols:
                    payload["filing_date"] = None
                if "start_date" in cols:
                    payload["start_date"] = None
                if "role_weight" in cols:
                    payload["role_weight"] = e.get("weight")
                if "recency_score" in cols:
                    payload["recency_score"] = e.get("decay")
                if "raw_json" in cols:
                    payload["raw_json"] = json.dumps(e, default=str)

                if not payload:
                    continue
                col_list = list(payload.keys())
                placeholders = ", ".join(["?"] * len(col_list))
                cur.execute(
                    f"INSERT INTO insider_events ({', '.join(col_list)}) VALUES ({placeholders})",
                    [payload[c] for c in col_list],
                )
            conn.commit()
            conn.close()
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def _neutral(ticker: str, note: str = "") -> dict:
        return {
            "insider_cluster_score": 0.0,
            "buy_count":    0,
            "unique_buyers": 0,
            "raw_score":    0.0,
            "cluster_bonus": False,
            "events":       [],
            "data_quality": "NONE",
            "note":         note or "No data",
        }


# ---------------------------------------------------------------------------
# standalone test
# ---------------------------------------------------------------------------

def _test():
    print("🧪 InsiderClusterScorer — live test with yfinance\n")
    ics = InsiderClusterScorer()
    for t in ["NVDA", "AAPL", "META"]:
        r = ics.score(t)
        print(f"  {t:6} score={r['insider_cluster_score']:5.1f}  buyers={r['unique_buyers']}  {r['note']}")
    print("\n✅ InsiderClusterScorer test complete.")


if __name__ == "__main__":
    _test()

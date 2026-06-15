#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sqlite3
from collections import defaultdict


def main() -> None:
    parser = argparse.ArgumentParser(description="Print options overlay summary")
    parser.add_argument("--db", default="trading_performance.db")
    parser.add_argument("--open-only", action="store_true")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    where = "WHERE UPPER(COALESCE(state, 'OPEN')) = 'OPEN'" if args.open_only else ""
    rows = conn.execute(
        f"""
        SELECT ticker, underlying_strategy, structure_type, state, broker, paper_mode,
               opened_at, closed_at, total_pnl_usd, total_pnl_pct, max_risk_usd
        FROM option_positions
        {where}
        ORDER BY opened_at DESC, id DESC
        """
    ).fetchall()
    conn.close()

    print("=" * 72)
    print("OPTIONS OVERLAY REPORT")
    print("=" * 72)
    if not rows:
        print("No option positions found")
        return

    totals = defaultdict(float)
    for row in rows:
        totals["count"] += 1
        totals["pnl"] += float(row["total_pnl_usd"] or 0.0)
        totals["risk"] += float(row["max_risk_usd"] or 0.0)
        print(
            f"{row['ticker']:>6}  {row['underlying_strategy']:<11}  {row['structure_type']:<18}  "
            f"{row['state']:<6}  pnl=${float(row['total_pnl_usd'] or 0.0):>7.2f}  "
            f"risk=${float(row['max_risk_usd'] or 0.0):>7.2f}  broker={row['broker']}"
        )

    print("-" * 72)
    print(f"Positions: {int(totals['count'])}")
    print(f"Total PnL: ${totals['pnl']:.2f}")
    print(f"Risk at Entry: ${totals['risk']:.2f}")


if __name__ == "__main__":
    main()

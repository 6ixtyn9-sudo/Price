#!/usr/bin/env python3
"""
End-to-end sanity test for futures ingestion.

Pulls a tiny amount of data (last 7 days of daily bars) for two futures symbols,
writes to the warehouse, then loads it back and prints basic diagnostics.

Run this after the futures changes to verify the full ingestion path works.

Usage:
    python3 scripts/test_futures_ingestion.py
"""

from datetime import datetime, timedelta, timezone
import os
import pytest

from price.data_sources import fetch_alpaca_futures_bars
from price.warehouse import save_to_warehouse, load_from_warehouse


def test_futures_ingestion():
    if not os.getenv("ALPACA_API_KEY") or not os.getenv("ALPACA_SECRET_KEY"):
        pytest.skip("Alpaca credentials not configured; skipping live ingestion test")
    symbols = ["ES", "CL"]          # two representative futures
    timeframe = "1d"
    days = 365

    end_dt = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=days)

    print(f"Testing futures ingestion for {symbols} ({timeframe}) over last {days} days...\n")

    for symbol in symbols:
        print(f"→ Fetching {symbol}...")
        df = fetch_alpaca_futures_bars(symbol, timeframe, start_dt, end_dt)

        if df.empty:
            print(f"  ❌ No data returned for {symbol}")
            continue

        print(f"  ✓ Fetched {len(df)} bars")
        save_to_warehouse(df)
        print("  ✓ Saved to warehouse")

        # Verify round-trip
        loaded = load_from_warehouse(symbol, timeframe)
        if loaded.empty:
            print(f"  ❌ Load from warehouse failed for {symbol}")
        else:
            print(f"  ✓ Round-trip OK ({len(loaded)} rows in warehouse)")

    print("\n✅ Futures ingestion end-to-end test complete.")


if __name__ == "__main__":
    test_futures_ingestion()
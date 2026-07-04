#!/usr/bin/env python3
"""
Prune under-populated symbol/timeframe partitions from localdata/warehouse.

Usage:
    python3 scripts/prune_warehouse.py                    # dry-run, min 200 bars
    python3 scripts/prune_warehouse.py --min-bars 1000   # dry-run, stricter
    python3 scripts/prune_warehouse.py --delete          # actually delete
    python3 scripts/prune_warehouse.py --delete --timeframe 15m
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import shutil
import argparse
import pandas as pd
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Prune under-populated warehouse partitions")
    parser.add_argument("--min-bars", type=int, default=200,
                        help="Delete partitions with fewer bars (default: 200)")
    parser.add_argument("--delete", action="store_true",
                        help="Actually delete; without this flag it's a dry-run")
    parser.add_argument("--timeframe", default="1d",
                        help="Timeframe to check (default: 1d)")
    args = parser.parse_args()

    warehouse_dir = Path("localdata/warehouse")
    killed = 0
    kept = 0

    for symbol_dir in sorted(warehouse_dir.glob("symbol=*")):
        # Handle both Timeframe= and timeframe= subdirs
        tf_dir = symbol_dir / f"Timeframe={args.timeframe}"
        if not tf_dir.exists():
            tf_dir = symbol_dir / f"timeframe={args.timeframe}"
        data_file = tf_dir / "data.parquet"

        if not data_file.exists():
            continue

        try:
            df = pd.read_parquet(data_file)
            bar_count = len(df)
        except Exception:
            bar_count = 0

        if bar_count < args.min_bars:
            print(f"DELETE {symbol_dir.name}: {bar_count} bars")
            killed += 1
            if args.delete:
                shutil.rmtree(symbol_dir)
        else:
            kept += 1

    print(f"\nSummary: kept {kept}, would delete {killed}")
    if args.delete:
        print("Deletion complete.")
    else:
        print("(dry-run - add --delete to actually remove)")


if __name__ == "__main__":
    main()
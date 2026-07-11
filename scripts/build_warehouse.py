import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import argparse
from price.config import SYMBOLS, is_futures
from price.warehouse import resample_15m_to_1h, propagate_adjustment_factors, load_from_warehouse


def build_warehouse(target_symbols=None):
    """Post-process warehouse partitions.

    This script is now a thin wrapper that handles edge cases where capture_bars
    was interrupted before the post-processing phase completed. The primary
    post-processing (resample + propagate) has been moved into capture_bars.py
    Phase 2, so this script is only needed for:
      - Manual recovery runs
      - Backfilling after partial captures
      - Ensuring 1h partitions exist for symbols that only have Alpaca 15m data

    It skips symbols that already have yfinance 1h data (which arrives with
    adj columns already filled), avoiding the old double-save cascade.
    """
    symbols = target_symbols or SYMBOLS

    for symbol in symbols:
        symbol = symbol.upper()
        print(f"\n🔧 Post-processing warehouse partitions for {symbol}...")

        # Resample 15m→1h only when no direct 1h source exists.
        # yfinance 1h data doesn't need resampling — it arrives as complete 1h bars.
        df_1h = load_from_warehouse(symbol, "1h")
        needs_resample = df_1h.empty or (
            "source" in df_1h.columns
            and df_1h["source"].astype(str).str.contains("alpaca").any()
        )
        if needs_resample:
            print(f"Resampling 15m -> 1h bars for {symbol} (Alpaca 1h path)...")
            try:
                resample_15m_to_1h(symbol)
            except Exception as e:
                print(f"❌ Failed to resample: {e}")
        else:
            print(f"1h data already present from yfinance for {symbol}; skipping resample.")

        # Futures do not have corporate actions — skip adjustment propagation
        if not is_futures(symbol):
            print(f"Propagating adjustments for {symbol} (15m, 1h)...")
            try:
                propagate_adjustment_factors(symbol)
            except Exception as e:
                print(f"❌ Failed to propagate: {e}")
        else:
            print(f"Skipping adjustment propagation for futures symbol: {symbol}")

    print("\n✅ Warehouse post-processing complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Post-process warehouse data.")
    parser.add_argument("--symbols", nargs="+", help="Symbols to build")
    args = parser.parse_args()

    build_warehouse(target_symbols=args.symbols)

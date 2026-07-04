import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import argparse
from price.config import SYMBOLS, is_futures
from price.warehouse import resample_15m_to_1h, propagate_adjustment_factors


def build_warehouse(target_symbols=None):
    symbols = target_symbols or SYMBOLS

    for symbol in symbols:
        symbol = symbol.upper()
        print(f"\n🔧 Post-processing warehouse partitions for {symbol}...")

        print(f"Resampling 15m -> 1h bars for {symbol}...")
        try:
            resample_15m_to_1h(symbol)
        except Exception as e:
            print(f"❌ Failed to resample: {e}")

        # Futures do not have corporate actions — skip adjustment propagation
        if not is_futures(symbol):
            print(f"Propagating adjustments backwards to {symbol} (15m, 1h)...")
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
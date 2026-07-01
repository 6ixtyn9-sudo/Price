import argparse
from price.config import SYMBOLS
from price.warehouse import resample_15m_to_1h, propagate_adjustment_factors

def build_warehouse(target_symbols=None):
    symbols = target_symbols or SYMBOLS
    
    for symbol in symbols:
        symbol = symbol.upper()
        print(f"\n�� Post-processing warehouse partitions for {symbol}...")
        
        print(f"Resampling 15m -> 1h bars for {symbol}...")
        try:
            resample_15m_to_1h(symbol)
        except Exception as e:
            print(f"❌ Failed to resample: {e}")
            
        print(f"Propagating adjustments backwards to {symbol} (15m, 1h)...")
        try:
            propagate_adjustment_factors(symbol)
        except Exception as e:
            print(f"❌ Failed to propagate: {e}")
            
    print("\n✅ Warehouse post-processing complete.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Post-process warehouse data.")
    parser.add_argument("--symbols", nargs="+", help="Symbols to build")
    args = parser.parse_args()
    
    build_warehouse(target_symbols=args.symbols)

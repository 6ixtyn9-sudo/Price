import argparse
import pandas as pd
from price.config import SYMBOLS
from price.discovery import discover_market_slices

def run_discovery(target_symbols=None, timeframe="1d", min_samples=15):
    symbols = target_symbols or SYMBOLS
    
    combinations = [
        ["state_ext", "state_slope"],
        ["state_ext", "state_vol"],
    ]
    
    if timeframe in ["15m", "1h"]:
        combinations.append(["state_session", "state_ext"])
        combinations.append(["state_session", "state_ext", "state_slope"])
        
    all_slices = []
    
    for symbol in symbols:
        symbol = symbol.upper()
        print(f"\n🔍 Exploring state slices for {symbol} ({timeframe})...")
        
        for fields in combinations:
            print(f"Testing state-space combination: {fields}")
            try:
                slices = discover_market_slices(symbol, timeframe, fields, min_samples=min_samples)
                if not slices.empty:
                    print(f"  -> Discovered {len(slices)} slices satisfying sample floor.")
                    all_slices.append(slices)
                else:
                    print("  -> No slices met the sample size threshold.")
            except Exception as e:
                print(f"  ❌ Error exploring combination {fields}: {e}")
                
    if not all_slices:
        print("\nNo market-state slices were discovered matching the sample floor.")
        return
        
    final_slices = pd.concat(all_slices).sort_values("mean_fwd_ret_5", ascending=False).reset_index(drop=True)
    
    output_file = "localdata/discovered_slices.csv"
    final_slices.to_csv(output_file, index=False)
    print(f"\n💾 Saved all discovered slices to {output_file}")
    
    print("\n🏆 Top 10 Discovered Market Slices (by 5-bar Forward Return):")
    print(final_slices.head(10).to_string(index=False))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Discover high-stability 3D-5D market slices.")
    parser.add_argument("--symbols", nargs="+", help="Specific symbols to explore")
    parser.add_argument("--timeframe", default="1d", choices=["15m", "1h", "1d"], help="Timeframe to explore")
    parser.add_argument("--min-samples", type=int, default=15, help="Minimum sample floor per slice")
    args = parser.parse_args()
    
    run_discovery(
        target_symbols=args.symbols,
        timeframe=args.timeframe,
        min_samples=args.min_samples
    )

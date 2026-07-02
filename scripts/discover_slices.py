import argparse
from pathlib import Path

import pandas as pd
from price.config import SYMBOLS
from price.discovery import discover_market_slices

DISCOVERED_SLICES_PATH = "localdata/discovered_slices.csv"

def run_discovery(target_symbols=None, timeframe="1d", min_samples=15, append=False, cond_symbol=None):
    symbols = target_symbols or SYMBOLS
    
    combinations = [
        ["state_ext", "state_slope"],
        ["state_ext", "state_vol"],
    ]
    
    if timeframe in ["15m", "1h"]:
        combinations.append(["state_session", "state_ext"])
        combinations.append(["state_session", "state_slope"])
        combinations.append(["state_session", "state_ext", "state_slope"])
        
    if cond_symbol:
        cs = cond_symbol.upper()
        combinations = combinations + [
            [f"cross_{cs}_state_slope", "state_ext"],
            [f"cross_{cs}_state_vol", "state_ext"],
            [f"cross_{cs}_state_ext", "state_ext"],
            [f"cross_{cs}_state_slope", "state_slope"],
        ]

    all_slices = []
    
    for symbol in symbols:
        symbol = symbol.upper()
        if cond_symbol and symbol == cond_symbol.upper():
            print(f"Skipping {symbol}: cannot condition a symbol on itself.")
            continue
        print(f"\n🔍 Exploring state slices for {symbol} ({timeframe})...")
        
        for fields in combinations:
            print(f"Testing state-space combination: {fields}")
            try:
                slices = discover_market_slices(symbol, timeframe, fields, min_samples=min_samples, cond_symbol=cond_symbol)
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

    output_path = Path(DISCOVERED_SLICES_PATH)

    if append and output_path.exists():
        existing = pd.read_csv(output_path)
        # Replace any prior rows for the same (symbol, timeframe) pairs covered by
        # this run, then append the fresh results, so running discovery for
        # multiple timeframes/symbols accumulates instead of clobbering.
        covered = {(s.upper(), timeframe) for s in symbols}
        existing_keep = existing[
            ~existing.apply(lambda r: (r["symbol"], r["timeframe"]) in covered, axis=1)
        ]
        final_slices = pd.concat([existing_keep, final_slices], ignore_index=True)
        final_slices = final_slices.sort_values("mean_fwd_ret_5", ascending=False).reset_index(drop=True)

    final_slices.to_csv(output_path, index=False)
    action = "Appended to" if append else "Saved all discovered slices to"
    print(f"\n💾 {action} {output_path}")
    
    print("\n🏆 Top 10 Discovered Market Slices (by 5-bar Forward Return):")
    print(final_slices.head(10).to_string(index=False))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Discover high-stability 3D-5D market slices.")
    parser.add_argument("--symbols", nargs="+", help="Specific symbols to explore")
    parser.add_argument("--timeframe", default="1d", choices=["15m", "1h", "1d"], help="Timeframe to explore")
    parser.add_argument("--min-samples", type=int, default=15, help="Minimum sample floor per slice")
    parser.add_argument(
        "--append",
        action="store_true",
        help="Merge into the existing discovered_slices.csv instead of overwriting it "
             "(replaces only rows for the same symbol/timeframe pairs covered by this run). "
             "Use this when running discovery across multiple timeframes so earlier runs "
             "are not lost.",
    )
    parser.add_argument(
        "--condition-on",
        default=None,
        help="Optional conditioning symbol. When set, adds cross-asset slice "
             "combinations that condition each primary symbol on this symbol's "
             "most-recent-completed state (backward as-of, no look-ahead).",
    )

    args = parser.parse_args()
    
    run_discovery(
        target_symbols=args.symbols,
        timeframe=args.timeframe,
        min_samples=args.min_samples,
        append=args.append,
        cond_symbol=args.condition_on,
    )

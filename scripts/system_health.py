#!/usr/bin/env python3
import sys
import pandas as pd
from price.trading import get_open_positions
from price.position_manager import recover_entry_context_for_symbol

def main():
    print("====================================")
    print(" SYSTEM HEALTH & RECONCILIATION     ")
    print("====================================")
    print()

    positions = get_open_positions()
    if positions is None or positions.empty:
        print("No open positions found at the broker.")
        print("System is idle.")
        return 0

    print(f"Total open broker positions: {len(positions)}")
    print("-" * 50)
    
    orphans = 0
    missing_slices = 0

    for _, p in positions.iterrows():
        symbol = p["symbol"]
        qty = p["qty"]
        upnl = p.get("unrealized_pl", 0.0)
        
        print(f"[{symbol}] Qty: {qty} | uPnL: ${upnl:,.2f}")
        
        ctx = recover_entry_context_for_symbol(symbol)
        if ctx is None:
            print("  ❌ STATUS: ORPHAN (No context recovered)")
            orphans += 1
        else:
            source = ctx.get("context_source", "unknown")
            slice_combo = ctx.get("slice_combination", "")
            
            if not slice_combo:
                print(f"  ❌ STATUS: MISSING SLICE (Recovered from {source} but no slice data)")
                missing_slices += 1
            else:
                print(f"  ✅ STATUS: HEALTHY (Source: {source})")
                print(f"     Slice: {slice_combo}")
                print(f"     TF: {ctx.get('timeframe')} | Mode: {ctx.get('bin_mode')}")
        
        print("-" * 50)
    
    print("\n====================================")
    print(" SUMMARY")
    print("====================================")
    print(f"Total Positions : {len(positions)}")
    print(f"Healthy         : {len(positions) - orphans - missing_slices}")
    print(f"Orphans         : {orphans}")
    print(f"Missing Slices  : {missing_slices}")

    if orphans > 0 or missing_slices > 0:
        print("\n⚠️  WARNING: Orphaned or degraded positions detected.")
        print("These positions may not exit according to system logic and")
        print("are relying purely on the broker-side protective stop.")
        return 1
    else:
        print("\n✅ All active positions are fully reconciled and healthy.")
        return 0

if __name__ == "__main__":
    sys.exit(main())

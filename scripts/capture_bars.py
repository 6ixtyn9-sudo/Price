import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import argparse
import pandas as pd
from datetime import datetime, timedelta, timezone
from price.config import SYMBOLS, is_futures, is_crypto, is_equity, ETF_SYMBOLS, UNIVERSE_TIER, UNIVERSE_MAX_SYMBOLS
from price.data_sources import (
    fetch_universal_bars,
    fetch_alpaca_bars,
    fetch_tiingo_daily_bars,
    fetch_alpaca_futures_bars,
    fetch_crypto_bars,
    resolve_universal_source,
)
from price.warehouse import save_to_warehouse, load_from_warehouse, resample_15m_to_1h, propagate_adjustment_factors

def capture_bars(target_symbols=None, target_timeframes=None, days_lookback=365, use_universal_router=True):
    symbols = target_symbols or SYMBOLS
    timeframes = target_timeframes or ["1d", "15m", "1h"]
    
    end_dt = datetime.now(timezone.utc)
    
    print(f"🌐 Universe tier: {UNIVERSE_TIER} | symbols in batch: {len(symbols)} | max_cap: {UNIVERSE_MAX_SYMBOLS}")
    print(f"   Sample: {symbols[:10]}")
    
    for symbol in symbols:
        symbol = symbol.upper() if "/" not in symbol else symbol  # keep BTC/USD case
        for tf in timeframes:
            if tf == "1h":
                # 1h is resampled locally from 15m – skip direct fetch
                continue
            if tf not in ["1d", "15m", "1h"]:
                continue
                
            # Determine source for logging using the same routing rules as
            # fetch_universal_bars. This is a first-attempt label: Tiingo daily
            # equities may still fall back to Alpaca if Tiingo raises.
            if use_universal_router:
                source = resolve_universal_source(symbol, tf)
            elif is_crypto(symbol):
                source = "alpaca_crypto"
            elif is_futures(symbol):
                source = "alpaca_futures"
            elif tf == "1d" and symbol in ETF_SYMBOLS:
                source = "tiingo"
            else:
                source = "alpaca"

            print(f"\n🚀 Ingesting {symbol} ({tf}) from {source.upper()}...")
            
            existing_df = load_from_warehouse(symbol, tf)
            if not existing_df.empty:
                latest_ts = pd.to_datetime(existing_df['bar_ts_utc'].max())
                start_dt = latest_ts - timedelta(days=1)
                print(f"Incremental update: Existing data found. Querying starting at {start_dt}.")
            else:
                start_dt = end_dt - timedelta(days=days_lookback)
                print(f"No existing data. Querying history of {days_lookback} days (starting at {start_dt}).")
                
            if start_dt >= end_dt:
                print("Warehouse is already up to date.")
                continue
                
            try:
                if use_universal_router:
                    df = fetch_universal_bars(symbol, tf, start_dt, end_dt)
                else:
                    # legacy path
                    if is_futures(symbol):
                        df = fetch_alpaca_futures_bars(symbol, tf, start_dt, end_dt)
                    elif is_crypto(symbol):
                        df = fetch_crypto_bars(symbol, tf, start_dt, end_dt)
                    elif tf == "1d" and source == "tiingo":
                        df = fetch_tiingo_daily_bars(symbol, start_dt, end_dt)
                    else:
                        df = fetch_alpaca_bars(symbol, tf, start_dt, end_dt)
                    
                if df is not None and not df.empty:
                    print(f"Successfully fetched {len(df)} bars.")
                    save_to_warehouse(df)
                    # resample 1h if we just ingested 15m
                    if tf == "15m":
                        try:
                            resample_15m_to_1h(symbol)
                        except Exception as re:
                            print(f"  1h resample warning: {re}")
                        # propagate adjustment factors for equities only
                        if is_equity(symbol) and not is_futures(symbol) and "/" not in symbol:
                            try:
                                propagate_adjustment_factors(symbol)
                            except Exception as pe:
                                print(f"  adj propagate warning: {pe}")
                else:
                    print("No new bars returned.")
            except Exception as e:
                print(f"❌ Error: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest historical OHLCV bar data - universal Alpaca free-tier")
    parser.add_argument("--symbols", nargs="+", help="Symbols to ingest (overrides universe)")
    parser.add_argument("--timeframes", nargs="+", choices=["15m", "1d", "1h"], help="Timeframes to ingest")
    parser.add_argument("--days", type=int, default=365, help="Days of lookback")
    parser.add_argument("--tier", choices=["etf", "etf_plus", "sp500", "allowlist", "full", "crypto", "all"], help="Override UNIVERSE_TIER")
    parser.add_argument("--max-symbols", type=int, help="Cap universe size")
    parser.add_argument("--universe", action="store_true", help="Print resolved universe and exit")
    parser.add_argument("--no-router", action="store_true", help="Disable universal router (legacy)")
    
    args = parser.parse_args()

    # tier override
    if args.tier:
        import os
        os.environ["UNIVERSE_TIER"] = args.tier
        # re-import symbols dynamically
        from price.universe import get_universe
        target_symbols = get_universe(args.tier, max_symbols=args.max_symbols)
    elif args.symbols:
        target_symbols = args.symbols
    else:
        target_symbols = None

    if args.universe:
        from price.config import SYMBOLS as CFG_SYMBOLS
        syms = target_symbols or CFG_SYMBOLS
        print(f"Resolved universe ({len(syms)} symbols):")
        for s in syms[:50]:
            print(f"  {s}")
        if len(syms) > 50:
            print(f"  ... +{len(syms)-50} more")
        import sys
        sys.exit(0)
    
    capture_bars(
        target_symbols=target_symbols,
        target_timeframes=args.timeframes,
        days_lookback=args.days,
        use_universal_router=not args.no_router
    )

import argparse
import pandas as pd
from datetime import datetime, timedelta, timezone
from price.config import SYMBOLS, PRIMARY_SOURCES, is_futures
from price.data_sources import fetch_alpaca_bars, fetch_tiingo_daily_bars, fetch_alpaca_futures_bars
from price.warehouse import save_to_warehouse, load_from_warehouse

def capture_bars(target_symbols=None, target_timeframes=None, days_lookback=365):
    symbols = target_symbols or SYMBOLS
    timeframes = target_timeframes or ["1d", "15m"]
    
    end_dt = datetime.now(timezone.utc)
    
    for symbol in symbols:
        symbol = symbol.upper()
        for tf in timeframes:
            if tf not in ["1d", "15m"]:
                continue
                
            source = PRIMARY_SOURCES[tf]
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
                if is_futures(symbol):
                    # Futures always come from Alpaca
                    if tf in ("1d", "15m"):
                        df = fetch_alpaca_futures_bars(symbol, tf, start_dt, end_dt)
                    else:
                        continue
                elif tf == "1d" and source == "tiingo":
                    df = fetch_tiingo_daily_bars(symbol, start_dt, end_dt)
                elif tf == "15m" and source == "alpaca":
                    df = fetch_alpaca_bars(symbol, "15m", start_dt, end_dt)
                else:
                    continue
                    
                if df is not None and not df.empty:
                    print(f"Successfully fetched {len(df)} bars.")
                    save_to_warehouse(df)
                else:
                    print("No new bars returned.")
            except Exception as e:
                print(f"❌ Error: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest historical OHLCV bar data.")
    parser.add_argument("--symbols", nargs="+", help="Symbols to ingest")
    parser.add_argument("--timeframes", nargs="+", choices=["15m", "1d"], help="Timeframes to ingest")
    parser.add_argument("--days", type=int, default=365, help="Days of lookback")
    
    args = parser.parse_args()
    
    capture_bars(
        target_symbols=args.symbols,
        target_timeframes=args.timeframes,
        days_lookback=args.days
    )

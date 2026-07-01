import pandas as pd
from datetime import datetime, timezone

from price.config import WAREHOUSE_DIR

def load_from_warehouse(symbol: str, timeframe: str) -> pd.DataFrame:
    partition_dir = WAREHOUSE_DIR / f"symbol={symbol.upper()}" / f"timeframe={timeframe}"
    if not partition_dir.exists():
        return pd.DataFrame()
    
    files = list(partition_dir.glob("*.parquet"))
    if not files:
        return pd.DataFrame()
        
    dfs = [pd.read_parquet(f) for f in files]
    df = pd.concat(dfs).sort_values("bar_ts_utc").reset_index(drop=True)
    df['bar_ts_utc'] = pd.to_datetime(df['bar_ts_utc']).dt.tz_convert('UTC')
    return df

def save_to_warehouse(df: pd.DataFrame):
    if df.empty:
        return
        
    groups = df.groupby(["symbol", "timeframe"])
    for (symbol, timeframe), group in groups:
        symbol = symbol.upper()
        partition_dir = WAREHOUSE_DIR / f"symbol={symbol}" / f"timeframe={timeframe}"
        partition_dir.mkdir(parents=True, exist_ok=True)
        
        existing_df = load_from_warehouse(symbol, timeframe)
        
        if not existing_df.empty:
            combined = pd.concat([existing_df, group]).reset_index(drop=True)
            combined = combined.sort_values("ingested_at_utc")
            combined = combined.drop_duplicates(subset=["bar_ts_utc"], keep="last")
            final_df = combined.sort_values("bar_ts_utc").reset_index(drop=True)
        else:
            final_df = group.sort_values("bar_ts_utc").reset_index(drop=True)
            
        for old_file in partition_dir.glob("*.parquet"):
            old_file.unlink()
            
        output_file = partition_dir / "data.parquet"
        
        save_df = final_df.copy()
        if "symbol" in save_df.columns:
            save_df = save_df.drop(columns=["symbol"])
        if "timeframe" in save_df.columns:
            save_df = save_df.drop(columns=["timeframe"])
            
        save_df.to_parquet(output_file, index=False)
        print(f"Warehouse saved: {symbol} | {timeframe} | {len(final_df)} total rows.")

def resample_15m_to_1h(symbol: str):
    symbol = symbol.upper()
    df_15m = load_from_warehouse(symbol, "15m")
    if df_15m.empty:
        print(f"No 15m bars found to resample for {symbol}.")
        return
        
    df_15m = df_15m.sort_values("bar_ts_utc")
    
    agg_rules = {
        'open_raw': 'first',
        'high_raw': 'max',
        'low_raw': 'min',
        'close_raw': 'last',
        'volume_raw': 'sum',
        'source': 'first',
    }
    
    resampled = df_15m.resample('1h', on='bar_ts_utc').agg(agg_rules).dropna().reset_index()
    
    resampled['symbol'] = symbol
    resampled['timeframe'] = "1h"
    resampled['ingested_at_utc'] = datetime.now(timezone.utc)
    
    save_to_warehouse(resampled)

def propagate_adjustment_factors(symbol: str):
    symbol = symbol.upper()
    df_1d = load_from_warehouse(symbol, "1d")
    if df_1d.empty:
        print(f"No daily bars found to extract adjustments for {symbol}.")
        return
        
    df_1d['ny_date'] = df_1d['bar_ts_utc'].dt.tz_convert('America/New_York').dt.date
    
    adj_map = df_1d.set_index('ny_date')[['adj_factor', 'split_factor', 'dividend_cash']].to_dict('index')
    
    for tf in ["15m", "1h"]:
        df_tf = load_from_warehouse(symbol, tf)
        if df_tf.empty:
            continue
            
        df_tf['ny_date'] = df_tf['bar_ts_utc'].dt.tz_convert('America/New_York').dt.date
        
        def apply_adjustments(row):
            ny_d = row['ny_date']
            factor = adj_map.get(ny_d, {'adj_factor': 1.0, 'split_factor': 1.0, 'dividend_cash': 0.0})
            
            open_adj = row['open_raw'] * factor['adj_factor']
            high_adj = row['high_raw'] * factor['adj_factor']
            low_adj = row['low_raw'] * factor['adj_factor']
            close_adj = row['close_raw'] * factor['adj_factor']
            
            return pd.Series([
                open_adj, high_adj, low_adj, close_adj,
                factor['adj_factor'], factor['split_factor'], factor['dividend_cash']
            ])
            
        adjusted_cols = df_tf.apply(apply_adjustments, axis=1)
        adjusted_cols.columns = [
            'open_adj', 'high_adj', 'low_adj', 'close_adj',
            'adj_factor', 'split_factor', 'dividend_cash'
        ]
        
        for col in adjusted_cols.columns:
            df_tf[col] = adjusted_cols[col]
            
        df_tf = df_tf.drop(columns=['ny_date'])
        
        df_tf['symbol'] = symbol
        df_tf['timeframe'] = tf
        df_tf['ingested_at_utc'] = datetime.now(timezone.utc)  # Force update to current time to ensure overwrite
        
        save_to_warehouse(df_tf)

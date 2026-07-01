import time
import requests
import pandas as pd
from datetime import datetime, timedelta, timezone
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.data.enums import DataFeed

from price.config import ALPACA_API_KEY, ALPACA_SECRET_KEY, TIINGO_API_KEY

def get_date_chunks(start_dt: datetime, end_dt: datetime, chunk_days: int):
    current_start = start_dt
    while current_start < end_dt:
        current_end = min(current_start + timedelta(days=chunk_days), end_dt)
        yield current_start, current_end
        current_start = current_end + timedelta(seconds=1)

def fetch_alpaca_bars(symbol: str, timeframe_str: str, start_dt: datetime, end_dt: datetime) -> pd.DataFrame:
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        raise ValueError("Alpaca API credentials missing in environment.")
    
    client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
    
    if timeframe_str == "15m":
        tf = TimeFrame(15, TimeFrameUnit.Minute)
    elif timeframe_str == "1h":
        tf = TimeFrame(1, TimeFrameUnit.Hour)
    elif timeframe_str == "1d":
        tf = TimeFrame.Day
    else:
        raise ValueError(f"Unsupported Alpaca timeframe: {timeframe_str}")
        
    all_dfs = []
    
    for chunk_start, chunk_end in get_date_chunks(start_dt, end_dt, 90):
        time.sleep(0.35)
        
        request_params = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=tf,
            start=chunk_start,
            end=chunk_end,
            feed=DataFeed.IEX
        )
        
        retries = 3
        while retries > 0:
            try:
                bars = client.get_stock_bars(request_params)
                df = bars.df
                if df is not None and not df.empty:
                    all_dfs.append(df)
                break
            except Exception as e:
                retries -= 1
                if "429" in str(e) or "Rate Limit" in str(e):
                    time.sleep(10)
                else:
                    time.sleep(2)
                if retries == 0:
                    raise e

    if not all_dfs:
        return pd.DataFrame()
        
    merged_df = pd.concat(all_dfs).sort_index()
    merged_df = merged_df[~merged_df.index.duplicated(keep='first')]
    
    df_clean = merged_df.reset_index()
    df_clean['bar_ts_utc'] = pd.to_datetime(df_clean['timestamp']).dt.tz_convert('UTC')
    df_clean['symbol'] = symbol.upper()
    df_clean['timeframe'] = timeframe_str
    df_clean['source'] = "alpaca"
    df_clean['ingested_at_utc'] = datetime.now(timezone.utc)
    
    df_clean = df_clean.rename(columns={
        'open': 'open_raw',
        'high': 'high_raw',
        'low': 'low_raw',
        'close': 'close_raw',
        'volume': 'volume_raw'
    })
    
    canonical_cols = [
        'symbol', 'timeframe', 'bar_ts_utc', 'source', 'ingested_at_utc',
        'open_raw', 'high_raw', 'low_raw', 'close_raw', 'volume_raw'
    ]
    
    return df_clean[canonical_cols]

def fetch_tiingo_daily_bars(symbol: str, start_dt: datetime, end_dt: datetime) -> pd.DataFrame:
    if not TIINGO_API_KEY:
        raise ValueError("Tiingo API credentials missing in environment.")
        
    start_str = start_dt.strftime('%Y-%m-%d')
    end_str = end_dt.strftime('%Y-%m-%d')
    
    url = f"https://api.tiingo.com/tiingo/daily/{symbol}/prices"
    params = {
        "startDate": start_str,
        "endDate": end_str,
        "token": TIINGO_API_KEY
    }
    
    retries = 3
    data = None
    while retries > 0:
        try:
            response = requests.get(url, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()
            break
        except Exception as e:
            retries -= 1
            time.sleep(2)
            if retries == 0:
                raise e
                
    if not data:
        return pd.DataFrame()
        
    df = pd.DataFrame(data)
    
    if df['date'].dt.tz is None:
        df['bar_ts_utc'] = pd.to_datetime(df['date']).dt.tz_localize('UTC')
    else:
        df['bar_ts_utc'] = pd.to_datetime(df['date']).dt.tz_convert('UTC')
        
    df['symbol'] = symbol.upper()
    df['timeframe'] = "1d"
    df['source'] = "tiingo"
    df['ingested_at_utc'] = datetime.now(timezone.utc)
    
    df = df.rename(columns={
        'open': 'open_raw',
        'high': 'high_raw',
        'low': 'low_raw',
        'close': 'close_raw',
        'volume': 'volume_raw',
        'adjOpen': 'open_adj',
        'adjHigh': 'high_adj',
        'adjLow': 'low_adj',
        'adjClose': 'close_adj',
        'splitFactor': 'split_factor',
        'divCash': 'dividend_cash'
    })
    
    df['adj_factor'] = df.apply(
        lambda r: r['close_adj'] / r['close_raw'] if r['close_raw'] > 0 else 1.0,
        axis=1
    )
    
    canonical_cols = [
        'symbol', 'timeframe', 'bar_ts_utc', 'source', 'ingested_at_utc',
        'open_raw', 'high_raw', 'low_raw', 'close_raw', 'volume_raw',
        'open_adj', 'high_adj', 'low_adj', 'close_adj', 'adj_factor',
        'split_factor', 'dividend_cash'
    ]
    
    return df[canonical_cols]

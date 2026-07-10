import time
from urllib.parse import quote
import requests
import pandas as pd
from datetime import datetime, timedelta, timezone
from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, CryptoBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.data.enums import DataFeed

from price.config import ALPACA_API_KEY, ALPACA_SECRET_KEY, TIINGO_API_KEY, is_crypto, is_futures, is_equity

def resolve_universal_source(symbol: str, timeframe_str: str) -> str:
    """Return the source label fetch_universal_bars will try first.

    This is intentionally kept next to the router so operator/logging code
    cannot drift from the actual data path. It is a first-attempt source: the
    Tiingo daily route may still fall back to Alpaca if Tiingo raises.
    """
    sym = symbol.upper()
    if is_crypto(sym):
        return "alpaca_crypto"
    if is_futures(sym):
        return "alpaca_futures"
    if timeframe_str == "1d" and is_equity(sym) and TIINGO_API_KEY:
        return "tiingo"
    return "alpaca"

def get_date_chunks(start_dt: datetime, end_dt: datetime, chunk_days: int):
    """
    Slices a date range into smaller chunks to politely query APIs.
    """
    current_start = start_dt
    while current_start < end_dt:
        current_end = min(current_start + timedelta(days=chunk_days), end_dt)
        yield current_start, current_end
        current_start = current_end + timedelta(seconds=1)

def _normalize_alpaca_df(merged_df: pd.DataFrame, symbol: str, timeframe_str: str, source: str = "alpaca") -> pd.DataFrame:
    """Common normalizer: adds adj columns = raw (no corp actions for crypto/futures / intraday)."""
    df_clean = merged_df.reset_index()
    # timestamp column name varies: 'timestamp' for stocks, also for crypto
    ts_col = 'timestamp' if 'timestamp' in df_clean.columns else df_clean.columns[0]
    df_clean['bar_ts_utc'] = pd.to_datetime(df_clean[ts_col]).dt.tz_convert('UTC')
    df_clean['symbol'] = symbol.upper()
    df_clean['timeframe'] = timeframe_str
    df_clean['source'] = source
    df_clean['ingested_at_utc'] = datetime.now(timezone.utc)

    # Rename columns to raw OHLVC canonical schema
    df_clean = df_clean.rename(columns={
        'open': 'open_raw',
        'high': 'high_raw',
        'low': 'low_raw',
        'close': 'close_raw',
        'volume': 'volume_raw',
        'trade_count': 'trade_count',
        'vwap': 'vwap'
    })

    # Add adjusted columns = raw (no splits/dividends for crypto/futures/intraday)
    for col in ['open', 'high', 'low', 'close']:
        raw_col = f'{col}_raw'
        adj_col = f'{col}_adj'
        if raw_col in df_clean.columns:
            df_clean[adj_col] = df_clean[raw_col]

    df_clean['adj_factor'] = 1.0
    df_clean['split_factor'] = 1.0
    df_clean['dividend_cash'] = 0.0

    # Select canonical columns (allow extra columns to be ignored downstream)
    canonical_cols = [
        'symbol', 'timeframe', 'bar_ts_utc', 'source', 'ingested_at_utc',
        'open_raw', 'high_raw', 'low_raw', 'close_raw', 'volume_raw',
        'open_adj', 'high_adj', 'low_adj', 'close_adj',
        'adj_factor', 'split_factor', 'dividend_cash'
    ]
    # ensure all cols exist
    for c in canonical_cols:
        if c not in df_clean.columns:
            if c in ['open_adj','high_adj','low_adj','close_adj']:
                # fallback to raw
                raw = c.replace('_adj','_raw')
                df_clean[c] = df_clean.get(raw, 0.0)
            elif c in ['adj_factor','split_factor']:
                df_clean[c] = 1.0
            elif c == 'dividend_cash':
                df_clean[c] = 0.0
            else:
                df_clean[c] = pd.NA

    return df_clean[canonical_cols]

def _filter_equity_regular_hours(df: pd.DataFrame, timeframe_str: str) -> pd.DataFrame:
    """Keep regular trading hours for equity intraday bars.

    Alpaca/IEX can return sparse premarket/after-hours bars. Those bars
    contaminate intraday rolling features and session labels, especially around
    08:00 ET and 16:00 ET. Crypto is 24/7 and is handled by fetch_crypto_bars,
    so this helper is only used inside the equity stock-bar path.
    """
    if df.empty or timeframe_str not in ("15m", "1h"):
        return df
    if "bar_ts_utc" not in df.columns:
        return df

    ny = pd.to_datetime(df["bar_ts_utc"], utc=True).dt.tz_convert("America/New_York")
    minutes = ny.dt.hour * 60 + ny.dt.minute
    rth = (minutes >= 9 * 60 + 30) & (minutes < 16 * 60)
    return df.loc[rth].reset_index(drop=True)


def fetch_alpaca_bars(symbol: str, timeframe_str: str, start_dt: datetime, end_dt: datetime) -> pd.DataFrame:
    """
    Fetches raw bar data from Alpaca for a single symbol, with rate limit handling and chunking.
    Works for US Equities / ETFs.
    """
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        raise ValueError("Alpaca API credentials missing in environment.")
    
    client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
    
    # Map timeframe string to alpaca-py TimeFrame
    if timeframe_str == "15m":
        tf = TimeFrame(15, TimeFrameUnit.Minute)
    elif timeframe_str == "1h":
        tf = TimeFrame(1, TimeFrameUnit.Hour)
    elif timeframe_str == "1d":
        tf = TimeFrame.Day
    else:
        raise ValueError(f"Unsupported Alpaca timeframe: {timeframe_str}")
        
    all_dfs = []
    
    # Use 90-day chunks to prevent huge queries and easy retry caching
    for chunk_start, chunk_end in get_date_chunks(start_dt, end_dt, 90):
        # Respect basic rate limit (roughly 3 calls per second is very safe for 200/min cap)
        time.sleep(0.35)
        
        request_params = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=tf,
            start=chunk_start,
            end=chunk_end,
            feed=DataFeed.IEX  # Essential for free/basic keys
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
                    # Hit rate limit, back off heavily
                    time.sleep(10)
                else:
                    time.sleep(2)
                if retries == 0:
                    print(f"Failed to fetch Alpaca bars for {symbol} ({chunk_start} to {chunk_end}): {e}")
                    raise e

    if not all_dfs:
        return pd.DataFrame()
        
    merged_df = pd.concat(all_dfs).sort_index()
    # Remove duplicate index rows if any chunks overlapped slightly
    merged_df = merged_df[~merged_df.index.duplicated(keep='first')]
    
    df_norm = _normalize_alpaca_df(merged_df, symbol, timeframe_str, source="alpaca")
    return _filter_equity_regular_hours(df_norm, timeframe_str)

def fetch_tiingo_daily_bars(symbol: str, start_dt: datetime, end_dt: datetime) -> pd.DataFrame:
    """
    Fetches adjusted and raw daily bars from Tiingo.
    """
    if not TIINGO_API_KEY:
        raise ValueError("Tiingo API credentials missing in environment.")
        
    start_str = start_dt.strftime('%Y-%m-%d')
    end_str = end_dt.strftime('%Y-%m-%d')
    
    url = f"https://api.tiingo.com/tiingo/daily/{quote(symbol, safe='')}/prices"
    params = {
        "startDate": start_str,
        "endDate": end_str,
    }
    headers = {"Authorization": f"Token {TIINGO_API_KEY}"}
    
    retries = 3
    data = None
    while retries > 0:
        try:
            response = requests.get(url, params=params, headers=headers, timeout=15)
            response.raise_for_status()
            data = response.json()
            break
        except Exception as e:
            retries -= 1
            time.sleep(2)
            if retries == 0:
                print(f"Failed to fetch Tiingo daily bars for {symbol}: {e}")
                raise e
                
    if not data:
        return pd.DataFrame()
        
    df = pd.DataFrame(data)
    
    # Normalize DataFrame (Safe conversion fix)
    parsed_dates = pd.to_datetime(df['date'])
    if parsed_dates.dt.tz is None:
        df['bar_ts_utc'] = parsed_dates.dt.tz_localize('UTC')
    else:
        df['bar_ts_utc'] = parsed_dates.dt.tz_convert('UTC')
        
    df['symbol'] = symbol.upper()
    df['timeframe'] = "1d"
    df['source'] = "tiingo"
    df['ingested_at_utc'] = datetime.now(timezone.utc)
    
    # Rename raw close and adjusted close
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
    
    # Compute adj_factor: close_adj / close_raw (ensure no divide by zero)
    df['adj_factor'] = df.apply(
        lambda r: r['close_adj'] / r['close_raw'] if r['close_raw'] > 0 else 1.0,
        axis=1
    )
    
    # Select canonical columns for daily bar schema
    canonical_cols = [
        'symbol', 'timeframe', 'bar_ts_utc', 'source', 'ingested_at_utc',
        'open_raw', 'high_raw', 'low_raw', 'close_raw', 'volume_raw',
        'open_adj', 'high_adj', 'low_adj', 'close_adj', 'adj_factor',
        'split_factor', 'dividend_cash'
    ]
    
    return df[canonical_cols]


def fetch_alpaca_futures_bars(symbol: str, timeframe_str: str, start_dt: datetime, end_dt: datetime) -> pd.DataFrame:
    """
    Fetches raw bar data from Alpaca for futures symbols.
    Uses the same StockHistoricalDataClient (Alpaca supports futures via the same endpoint).
    NOTE: Alpaca free tier officially covers US Stocks/ETFs, Options, Crypto.
    Futures data may return empty on free tier – caller should handle gracefully.
    """
    # Re-use equity path – output schema is identical
    try:
        return fetch_alpaca_bars(symbol, timeframe_str, start_dt, end_dt)
    except Exception as e:
        print(f"Futures fetch failed for {symbol}: {e}")
        return pd.DataFrame()


def fetch_crypto_bars(symbol: str, timeframe_str: str, start_dt: datetime, end_dt: datetime) -> pd.DataFrame:
    """
    Fetch crypto OHLCV bars from Alpaca Crypto Data API (free tier included).
    symbol format: 'BTC/USD', 'ETH/USD', etc.
    """
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        raise ValueError("Alpaca API credentials missing in environment.")

    client = CryptoHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)

    if timeframe_str == "15m":
        tf = TimeFrame(15, TimeFrameUnit.Minute)
    elif timeframe_str == "1h":
        tf = TimeFrame(1, TimeFrameUnit.Hour)
    elif timeframe_str == "1d":
        tf = TimeFrame.Day
    else:
        raise ValueError(f"Unsupported crypto timeframe: {timeframe_str}")

    all_dfs = []
    for chunk_start, chunk_end in get_date_chunks(start_dt, end_dt, 90):
        time.sleep(0.35)
        request_params = CryptoBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=tf,
            start=chunk_start,
            end=chunk_end
        )
        retries = 3
        while retries > 0:
            try:
                bars = client.get_crypto_bars(request_params)
                df = bars.df
                if df is not None and not df.empty:
                    all_dfs.append(df)
                break
            except Exception as e:
                retries -= 1
                if "429" in str(e) or "rate" in str(e).lower():
                    time.sleep(10)
                else:
                    time.sleep(2)
                if retries == 0:
                    print(f"Failed to fetch crypto bars for {symbol}: {e}")
                    raise e

    if not all_dfs:
        return pd.DataFrame()

    merged_df = pd.concat(all_dfs).sort_index()
    merged_df = merged_df[~merged_df.index.duplicated(keep='first')]

    return _normalize_alpaca_df(merged_df, symbol, timeframe_str, source="alpaca_crypto")


def fetch_universal_bars(symbol: str, timeframe_str: str, start_dt: datetime, end_dt: datetime) -> pd.DataFrame:
    """
    Router that picks the correct data source based on asset class:
      - Crypto: Alpaca CryptoHistoricalDataClient
      - Futures: Alpaca (may be empty on free tier)
      - ETF (core 10): Tiingo 1d, Alpaca 15m/1h
      - All other equities: Alpaca (Tiingo fallback optional)
    """
    sym = symbol.upper()
    # Crypto first
    if is_crypto(sym):
        return fetch_crypto_bars(sym, timeframe_str, start_dt, end_dt)

    # Futures
    if is_futures(sym):
        return fetch_alpaca_futures_bars(sym, timeframe_str, start_dt, end_dt)

    # Prefer Tiingo daily for ALL equities when available.
    #
    # Rationale:
    # - Tiingo returns adjusted daily OHLCV with split/dividend fields.
    # - Alpaca daily bars for non-core equities can be raw/unadjusted.
    # - Bad daily adjustment factors contaminate feature states and intraday
    #   adjustment propagation. This showed up as impossible one-day jumps in
    #   non-core symbols during the liquid236 baseline audit.
    if timeframe_str == "1d" and is_equity(sym) and TIINGO_API_KEY:
        try:
            return fetch_tiingo_daily_bars(sym, start_dt, end_dt)
        except Exception as e:
            print(f"Tiingo failed for {sym}, falling back to Alpaca: {e}")
            return fetch_alpaca_bars(sym, timeframe_str, start_dt, end_dt)

    # Default: Alpaca for everything else (free-tier universal)
    return fetch_alpaca_bars(sym, timeframe_str, start_dt, end_dt)

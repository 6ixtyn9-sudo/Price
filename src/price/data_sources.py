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
from price.futures_metadata import provider_symbol_for, yahoo_symbol_for

# ---------------------------------------------------------------------------
# Rate-limit pacing for external APIs
# ---------------------------------------------------------------------------
_TIINGO_MIN_INTERVAL = 2.0   # seconds between Tiingo requests (≈30/min, conservatively under free tier)
_ALPACA_MIN_INTERVAL = 0.35  # seconds between Alpaca requests (≈170/min, under 200/min cap)
_tiingo_last_request = 0.0
_alpaca_last_request = 0.0


def _tiingo_pace():
    """Sleep just long enough to keep Tiingo requests under the free-tier rate limit."""
    global _tiingo_last_request
    elapsed = time.monotonic() - _tiingo_last_request
    if elapsed < _TIINGO_MIN_INTERVAL:
        time.sleep(_TIINGO_MIN_INTERVAL - elapsed)
    _tiingo_last_request = time.monotonic()


def _alpaca_pace():
    """Sleep just long enough to keep Alpaca requests under the 200/min cap."""
    global _alpaca_last_request
    elapsed = time.monotonic() - _alpaca_last_request
    if elapsed < _ALPACA_MIN_INTERVAL:
        time.sleep(_ALPACA_MIN_INTERVAL - elapsed)
    _alpaca_last_request = time.monotonic()


def resolve_universal_source(symbol: str, timeframe_str: str) -> str:
    """Return the source label fetch_universal_bars will try first.

    This is intentionally kept next to the router so operator/logging code
    cannot drift from the actual data path. It is a first-attempt source: the
    yfinance daily/hourly route may still fall back to Tiingo or Alpaca if
    yfinance fails.
    """
    sym = symbol.upper()
    if is_crypto(sym):
        return "alpaca_crypto"
    if is_futures(sym):
        return "yfinance_futures"
    if timeframe_str in ("1d", "1h") and is_equity(sym):
        return "yfinance"
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
        _alpaca_pace()
        
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
    
    retries = 1  # One retry on 429, then fall back to Alpaca quickly
    data = None
    while retries >= 0:
        try:
            # Tiingo free tier: ~50 requests/min. 2s floor keeps us under
            # that even in tight loops (236 symbols back-to-back).
            _tiingo_pace()
            response = requests.get(url, params=params, headers=headers, timeout=15)
            response.raise_for_status()
            data = response.json()
            break
        except requests.exceptions.HTTPError as e:
            if response.status_code == 429:
                retries -= 1
                if retries >= 0:
                    print(f"Tiingo 429 for {symbol}, waiting 30s before retry...")
                    time.sleep(30)
                else:
                    print(f"Tiingo 429 for {symbol}, giving up (will use Alpaca fallback)")
                    raise e
            else:
                retries -= 1
                if retries >= 0:
                    time.sleep(2)
                else:
                    print(f"Failed to fetch Tiingo daily bars for {symbol}: {e}")
                    raise e
        except Exception as e:
            retries -= 1
            if retries >= 0:
                time.sleep(2)
            else:
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
    """Fetch raw bar data for futures symbols.

    The public research namespace is canonical FUT/* (e.g. FUT/ES). Alpaca's
    request path still needs the provider/root symbol (ES), while the warehouse
    should retain the canonical FUT/* symbol to avoid collisions with equities
    or crypto-like names.

    NOTE: futures remain research-only in this repo. The free-tier/provider
    data path may return empty for some symbols/timeframes; callers must handle
    empty frames gracefully.
    """
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
        raise ValueError(f"Unsupported futures timeframe: {timeframe_str}")

    provider_symbol = provider_symbol_for(symbol)
    all_dfs = []

    for chunk_start, chunk_end in get_date_chunks(start_dt, end_dt, 90):
        _alpaca_pace()
        request_params = StockBarsRequest(
            symbol_or_symbols=provider_symbol,
            timeframe=tf,
            start=chunk_start,
            end=chunk_end,
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
                    print(
                        f"Failed to fetch Alpaca futures bars for {symbol} "
                        f"({chunk_start} to {chunk_end}): {e}"
                    )
                    return pd.DataFrame()

    if not all_dfs:
        return pd.DataFrame()

    merged_df = pd.concat(all_dfs).sort_index()
    merged_df = merged_df[~merged_df.index.duplicated(keep='first')]
    return _normalize_alpaca_df(merged_df, symbol, timeframe_str, source="alpaca_futures")


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
        _alpaca_pace()
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


def _build_yfinance_canonical(df: pd.DataFrame, symbol: str, timeframe_str: str) -> pd.DataFrame:
    """Shared normalizer for yfinance daily and hourly equity bars.

    Handles the common column rename, adj_factor computation, and canonical
    reordering.  Both daily and hourly yfinance output share the same schema
    when auto_adjust=False: raw OHLCV + Adj Close + Dividends + Stock Splits.
    """
    df = df.reset_index()
    df = df.rename(columns={
        "Date": "bar_ts_utc",
        "Datetime": "bar_ts_utc",
        "Open": "open_raw",
        "High": "high_raw",
        "Low": "low_raw",
        "Close": "close_raw",
        "Adj Close": "close_adj",
        "Volume": "volume_raw",
        "Dividends": "dividend_cash",
        "Stock Splits": "split_factor",
    })

    if "close_adj" not in df.columns and "close_raw" in df.columns:
        df["close_adj"] = df["close_raw"]
    if "dividend_cash" not in df.columns:
        df["dividend_cash"] = 0.0
    if "split_factor" not in df.columns:
        df["split_factor"] = 1.0

    # Drop columns yfinance may include but we don't use (e.g. Capital Gains)
    drop_cols = [c for c in df.columns if c not in {
        "bar_ts_utc", "open_raw", "high_raw", "low_raw", "close_raw",
        "volume_raw", "close_adj", "dividend_cash", "split_factor",
    }]
    if drop_cols:
        df = df.drop(columns=drop_cols, errors="ignore")

    # Compute adj_factor and derive adjusted OHLCV from it
    df["adj_factor"] = df["close_adj"] / df["close_raw"]
    df["adj_factor"] = df["adj_factor"].replace([float("inf"), float("-inf")], 1.0).fillna(1.0)
    df["open_adj"] = df["open_raw"] * df["adj_factor"]
    df["high_adj"] = df["high_raw"] * df["adj_factor"]
    df["low_adj"] = df["low_raw"] * df["adj_factor"]
    df["symbol"] = symbol.upper()
    df["timeframe"] = timeframe_str
    df["source"] = "yfinance"
    df["ingested_at_utc"] = datetime.now(timezone.utc)

    # Ensure bar_ts_utc is timezone-aware UTC
    if "bar_ts_utc" in df.columns:
        df["bar_ts_utc"] = pd.to_datetime(df["bar_ts_utc"], utc=True)

    # Reorder to canonical columns
    canonical = [
        "symbol", "timeframe", "bar_ts_utc", "source", "ingested_at_utc",
        "open_raw", "high_raw", "low_raw", "close_raw", "volume_raw",
        "open_adj", "high_adj", "low_adj", "close_adj",
        "adj_factor", "split_factor", "dividend_cash",
    ]
    for col in canonical:
        if col not in df.columns:
            df[col] = pd.NA
    df = df[[c for c in canonical if c in df.columns]]

    return df


def fetch_yfinance_daily_bars(symbol: str, start_dt: datetime, end_dt: datetime) -> pd.DataFrame:
    """Fetch adjusted + raw daily bars from Yahoo Finance via yfinance.

    Uses auto_adjust=False so we get both raw (Close) and adjusted (Adj Close)
    prices, plus Dividends and Stock Splits — the same schema as Tiingo.
    No API key required. Rate limit ~2000 req/hr (effectively unlimited for
    a 236-symbol daily capture).
    """
    try:
        import yfinance as yf
    except ImportError:
        print("yfinance not installed, skipping Yahoo Finance fallback.")
        return pd.DataFrame()

    ticker_symbol = symbol.upper().replace("/", "-")
    start_str = start_dt.strftime("%Y-%m-%d")
    end_str = end_dt.strftime("%Y-%m-%d")

    try:
        ticker = yf.Ticker(ticker_symbol)
        df = ticker.history(start=start_str, end=end_str, auto_adjust=False)
        if df is None or df.empty:
            return pd.DataFrame()
    except Exception as e:
        print(f"yfinance failed for {symbol}: {e}")
        return pd.DataFrame()

    result = _build_yfinance_canonical(df, symbol, "1d")
    print(f"yfinance: fetched {len(result)} adjusted daily bars for {symbol}")
    return result


def fetch_yfinance_hourly_bars(symbol: str, start_dt: datetime, end_dt: datetime) -> pd.DataFrame:
    """Fetch adjusted + raw hourly bars from Yahoo Finance via yfinance.

    Uses auto_adjust=False to get raw + Adj Close (same schema as daily).
    yfinance provides up to 730 days of 1h history — 2× the current Alpaca
    15m→1h resample window (365 days), with no API key, no rate limits, and
    no resample step needed. Bars are already in RTH (yfinance only returns
    regular trading hours for equities).

    If start_dt is more than 730 days in the past, the request is clamped
    to 730 days so yfinance returns what it can instead of failing entirely.
    The caller (Alpaca fallback) will cover the older gap.
    """
    try:
        import yfinance as yf
    except ImportError:
        print("yfinance not installed, skipping Yahoo Finance hourly fetch.")
        return pd.DataFrame()

    # yfinance 1h only covers the last 730 days, but the boundary is strict —
    # requesting exactly 730 days sometimes gets rejected. Use 725 as a safe
    # margin so the request always succeeds.
    _YFINANCE_1H_MAX_DAYS = 725
    min_start = end_dt - timedelta(days=_YFINANCE_1H_MAX_DAYS)
    if start_dt < min_start:
        start_dt = min_start

    ticker_symbol = symbol.upper().replace("/", "-")
    start_str = start_dt.strftime("%Y-%m-%d")
    end_str = end_dt.strftime("%Y-%m-%d")

    try:
        ticker = yf.Ticker(ticker_symbol)
        df = ticker.history(start=start_str, end=end_str, interval="1h", auto_adjust=False)
        if df is None or df.empty:
            return pd.DataFrame()
    except Exception as e:
        print(f"yfinance 1h failed for {symbol}: {e}")
        return pd.DataFrame()

    result = _build_yfinance_canonical(df, symbol, "1h")
    # yfinance hourly bars are already RTH-only for equities; no need to filter.
    print(f"yfinance: fetched {len(result)} adjusted hourly bars for {symbol}")
    return result


def fetch_yfinance_futures_bars(symbol: str, timeframe_str: str, start_dt: datetime, end_dt: datetime) -> pd.DataFrame:
    """Fetch continuous futures bars from Yahoo Finance when available.

    Yahoo uses symbols like ES=F, CL=F, GC=F. This is the primary research
    path for canonical FUT/* symbols because it is free and gives a second
    provider family beyond Alpaca. Tiingo currently has no wired futures path
    in this repo, so the robust provider chain for futures is:

      yfinance -> Alpaca fallback

    Timeframe support mirrors Yahoo's practical constraints:
      1d  : long history
      1h  : up to ~725 days
      15m : up to ~60 days
    """
    try:
        import yfinance as yf
    except ImportError:
        print("yfinance not installed, skipping Yahoo Finance futures fetch.")
        return pd.DataFrame()

    yahoo_symbol = yahoo_symbol_for(symbol)
    if not yahoo_symbol:
        return pd.DataFrame()

    interval = None
    if timeframe_str == "1d":
        interval = None
    elif timeframe_str == "1h":
        interval = "1h"
        min_start = end_dt - timedelta(days=725)
        if start_dt < min_start:
            start_dt = min_start
    elif timeframe_str == "15m":
        interval = "15m"
        min_start = end_dt - timedelta(days=59)
        if start_dt < min_start:
            start_dt = min_start
    else:
        raise ValueError(f"Unsupported futures timeframe: {timeframe_str}")

    start_str = start_dt.strftime("%Y-%m-%d")
    end_str = end_dt.strftime("%Y-%m-%d")

    try:
        ticker = yf.Ticker(yahoo_symbol)
        if interval is None:
            df = ticker.history(start=start_str, end=end_str, auto_adjust=False)
        else:
            df = ticker.history(start=start_str, end=end_str, interval=interval, auto_adjust=False)
        if df is None or df.empty:
            return pd.DataFrame()
    except Exception as e:
        print(f"yfinance futures failed for {symbol}: {e}")
        return pd.DataFrame()

    result = _build_yfinance_canonical(df, symbol, timeframe_str)
    print(f"yfinance: fetched {len(result)} futures bars for {symbol} ({timeframe_str})")
    return result


def fetch_universal_bars(symbol: str, timeframe_str: str, start_dt: datetime, end_dt: datetime) -> pd.DataFrame:
    """
    Router that picks the correct data source based on asset class:
      - Crypto: Alpaca CryptoHistoricalDataClient
      - Futures: yfinance -> Alpaca fallback
      - Equity daily: yfinance → Tiingo → Alpaca raw (last resort)
      - Equity 1h:   yfinance → Alpaca 15m resample (fallback)
      - Equity 15m:  Alpaca (yfinance 15m only covers 60 days)
    """
    sym = symbol.upper()
    # Crypto first
    if is_crypto(sym):
        return fetch_crypto_bars(sym, timeframe_str, start_dt, end_dt)

    # Futures: Yahoo Finance first (continuous futures symbols like ES=F,
    # CL=F), then Alpaca fallback. Keep the canonical FUT/* symbol in the
    # returned frame so warehouse/research identities stay unambiguous.
    if is_futures(sym):
        yf_df = fetch_yfinance_futures_bars(sym, timeframe_str, start_dt, end_dt)
        if not yf_df.empty:
            return yf_df
        return fetch_alpaca_futures_bars(sym, timeframe_str, start_dt, end_dt)

    # Equity daily: yfinance primary (no API key, no rate limits, raw+adjusted).
    # Tiingo as fallback (requires key, rate-limited at ~50/min).
    # Alpaca daily is last resort (raw/unadjusted, produces impossible jumps).
    if timeframe_str == "1d" and is_equity(sym):
        yf_df = fetch_yfinance_daily_bars(sym, start_dt, end_dt)
        if not yf_df.empty:
            return yf_df
        if TIINGO_API_KEY:
            try:
                return fetch_tiingo_daily_bars(sym, start_dt, end_dt)
            except Exception as e:
                print(f"yfinance + Tiingo both failed for {sym}, falling back to raw Alpaca daily (unadjusted!): {e}")
        else:
            print(f"yfinance failed for {sym}, no Tiingo key, falling back to raw Alpaca daily (unadjusted!)")
        return fetch_alpaca_bars(sym, timeframe_str, start_dt, end_dt)

    # Equity 1h: yfinance primary (up to 730 days, no resample needed).
    # Alpaca 1h as fallback (IEX feed, resampled from 15m in capture_bars path).
    if timeframe_str == "1h" and is_equity(sym):
        yf_df = fetch_yfinance_hourly_bars(sym, start_dt, end_dt)
        if not yf_df.empty:
            return yf_df
        print(f"yfinance 1h failed for {sym}, falling back to Alpaca 1h.")
        return fetch_alpaca_bars(sym, timeframe_str, start_dt, end_dt)

    # Default: Alpaca for everything else (equity 15m, no-key daily)
    return fetch_alpaca_bars(sym, timeframe_str, start_dt, end_dt)

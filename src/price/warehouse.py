import pandas as pd
from datetime import datetime, timezone

from price.config import WAREHOUSE_DIR, SYMBOL_PATTERN, is_crypto

def _sanitize_symbol(symbol: str) -> str:
    """
    Filesystem-safe symbol encoding.
    - 'BTC/USD' -> 'BTC-USD'
    - Keeps uppercase
    - Rejects anything outside the market-symbol grammar before path use.
    """
    s = str(symbol).strip().upper()
    if not SYMBOL_PATTERN.fullmatch(s):
        raise ValueError(f"Invalid symbol for warehouse path: {symbol!r}")
    return s.replace("/", "-")

def _desanitize_symbol(safe: str) -> str:
    # best-effort reverse - mainly for display
    # Note: ambiguous if original contained '-', but we store true symbol in data
    return safe.replace("-", "/")

VALID_TIMEFRAMES = {"1d", "1h", "15m"}


def _assert_within_warehouse(path):
    """Belt-and-suspenders containment check for warehouse paths.

    Symbol/timeframe validation should make traversal impossible. This check
    still verifies the resolved path is under the configured WAREHOUSE_DIR so a
    future sanitizer regression or symlink/path trick fails closed before any
    read, write, or unlink.
    """
    root = WAREHOUSE_DIR.resolve()
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Warehouse path escapes root: {path}") from exc
    return path


def _validate_timeframe(timeframe: str) -> str:
    tf = str(timeframe).strip()
    if tf not in VALID_TIMEFRAMES:
        raise ValueError(f"Invalid warehouse timeframe: {timeframe!r}")
    return tf


def load_from_warehouse(symbol: str, timeframe: str) -> pd.DataFrame:
    safe_sym = _sanitize_symbol(symbol)
    timeframe = _validate_timeframe(timeframe)
    partition_dir = _assert_within_warehouse(
        WAREHOUSE_DIR / f"symbol={safe_sym}" / f"timeframe={timeframe}"
    )
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
        symbol = str(symbol).strip().upper()
        safe_sym = _sanitize_symbol(symbol)
        timeframe = _validate_timeframe(timeframe)
        partition_dir = _assert_within_warehouse(
            WAREHOUSE_DIR / f"symbol={safe_sym}" / f"timeframe={timeframe}"
        )
        partition_dir.mkdir(parents=True, exist_ok=True)
        
        existing_df = load_from_warehouse(symbol, timeframe)
        
        if not existing_df.empty:
            combined = pd.concat([existing_df, group]).reset_index(drop=True)
            # ingested_at_utc may be missing from old yfinance partitions;
            # fill with a sentinel so sort doesn't KeyError.
            if "ingested_at_utc" not in combined.columns:
                combined["ingested_at_utc"] = pd.NaT
            else:
                combined["ingested_at_utc"] = combined["ingested_at_utc"].fillna(pd.NaT)
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

def _filter_regular_hours_for_equity_intraday(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Filter equity intraday warehouse rows to regular market hours.

    Crypto remains 24/7. Futures are excluded from the current liquid236
    universe and are not filtered here.
    """
    if df.empty or is_crypto(symbol):
        return df
    if "bar_ts_utc" not in df.columns:
        return df
    ny = pd.to_datetime(df["bar_ts_utc"], utc=True).dt.tz_convert("America/New_York")
    minutes = ny.dt.hour * 60 + ny.dt.minute
    rth = (minutes >= 9 * 60 + 30) & (minutes < 16 * 60)
    return df.loc[rth].reset_index(drop=True)


def resample_15m_to_1h(symbol: str):
    # symbol may be 'BTC/USD' – load_from_warehouse handles sanitizing
    df_15m = load_from_warehouse(symbol, "15m")
    df_15m = _filter_regular_hours_for_equity_intraday(df_15m, symbol)
    if df_15m.empty:
        print(f"No 15m bars found to resample for {symbol}.")
        return
        
    df_15m = df_15m.sort_values("bar_ts_utc")
    
    # Build agg dict dynamically – crypto may lack 'source' column in some paths
    agg_rules = {
        'open_raw': 'first',
        'high_raw': 'max',
        'low_raw': 'min',
        'close_raw': 'last',
        'volume_raw': 'sum',
    }
    # optional cols
    for opt in ['open_adj','high_adj','low_adj','close_adj','adj_factor','split_factor','dividend_cash','vwap','trade_count','source']:
        if opt in df_15m.columns:
            if opt in ['high_adj','high_raw','volume_raw','vwap']:
                agg_rules[opt] = 'max' if 'high' in opt else 'sum' if 'volume' in opt else 'last'
            elif opt in ['low_adj','low_raw']:
                agg_rules[opt] = 'min'
            elif opt in ['open_adj','open_raw']:
                agg_rules[opt] = 'first'
            else:
                agg_rules[opt] = 'last'
    
    # ensure source aggregation exists
    if 'source' in df_15m.columns and 'source' not in agg_rules:
        agg_rules['source'] = 'first'
    
    resampled = df_15m.resample('1h', on='bar_ts_utc').agg(agg_rules).dropna(subset=['open_raw','close_raw']).reset_index()
    
    resampled['symbol'] = symbol.upper()
    resampled['timeframe'] = "1h"
    resampled['ingested_at_utc'] = datetime.now(timezone.utc)
    
    # fill adj = raw if missing
    for col in ['open_adj','high_adj','low_adj','close_adj']:
        raw = col.replace('_adj','_raw')
        if col not in resampled.columns and raw in resampled.columns:
            resampled[col] = resampled[raw]
    for fcol, default in [('adj_factor',1.0),('split_factor',1.0),('dividend_cash',0.0)]:
        if fcol not in resampled.columns:
            resampled[fcol] = default
    
    save_to_warehouse(resampled)

def propagate_adjustment_factors(symbol: str):
    """Propagate daily adjustment factors into intraday partitions.

    Uses a vectorized merge instead of row-wise apply, making it 100-1000×
    faster for large intraday partitions (thousands of bars per symbol).
    """
    # Skip crypto – no corporate actions, adj = raw already
    if is_crypto(symbol):
        return
    df_1d = load_from_warehouse(symbol, "1d")
    if df_1d.empty:
        print(f"No daily bars found to extract adjustments for {symbol}.")
        return
    if 'adj_factor' not in df_1d.columns:
        # nothing to propagate – assume 1.0
        return
        
    # Daily bars are stored at midnight UTC, but semantically represent
    # the market session date. Converting midnight UTC to America/New_York would
    # shift the date to the prior evening and apply each daily adjustment factor
    # to the wrong intraday session. Keep daily bars keyed by their UTC date,
    # while intraday bars below are keyed by their New York market date.
    df_1d['market_date'] = df_1d['bar_ts_utc'].dt.tz_convert('UTC').dt.date

    # Mixed historical daily sources can leave duplicate rows for the same
    # market date (e.g. Tiingo daily at 00:00 UTC and Alpaca daily at 04:00 UTC).
    # Keep the most recently ingested row per market date before building the
    # adjustment map; otherwise to_dict(orient='index') raises on duplicate index.
    if 'ingested_at_utc' in df_1d.columns:
        df_1d['_ingested_sort'] = pd.to_datetime(df_1d['ingested_at_utc'], errors='coerce', utc=True)
        df_1d = df_1d.sort_values(['market_date', '_ingested_sort', 'bar_ts_utc'])
        df_1d = df_1d.drop(columns=['_ingested_sort'])
    else:
        df_1d = df_1d.sort_values(['market_date', 'bar_ts_utc'])
    df_1d = df_1d.drop_duplicates(subset=['market_date'], keep='last')

    # Build a DataFrame of adjustment factors keyed by market_date for vectorized merge
    adj_df = df_1d.set_index('market_date')[['adj_factor', 'split_factor', 'dividend_cash']].copy()
    # Ensure defaults for dates not in the daily data
    adj_df['adj_factor'] = adj_df['adj_factor'].fillna(1.0)
    adj_df['split_factor'] = adj_df['split_factor'].fillna(1.0)
    adj_df['dividend_cash'] = adj_df['dividend_cash'].fillna(0.0)
    
    for tf in ["15m", "1h"]:
        df_tf = load_from_warehouse(symbol, tf)
        if df_tf.empty:
            continue
        
        # crypto runs 24/7 – use UTC date; equities use NY date
        if is_crypto(symbol):
            df_tf['market_date'] = df_tf['bar_ts_utc'].dt.tz_convert('UTC').dt.date
        else:
            df_tf['market_date'] = df_tf['bar_ts_utc'].dt.tz_convert('America/New_York').dt.date

        # Drop stale adj columns from intraday before merge to avoid
        # pandas suffix collision (_x / _y).  The merge replaces them.
        for drop_col in ('adj_factor', 'split_factor', 'dividend_cash'):
            if drop_col in df_tf.columns:
                df_tf = df_tf.drop(columns=[drop_col])

        # Vectorized merge: join adjustment factors by market_date instead of
        # row-wise Python apply. This is the critical performance fix — the
        # old apply() path called a Python function per row (thousands of
        # calls per symbol), while merge+vectorized multiply is a single
        # C-level operation.
        df_tf = df_tf.merge(adj_df, on='market_date', how='left')
        df_tf['adj_factor'] = df_tf['adj_factor'].fillna(1.0)
        df_tf['split_factor'] = df_tf['split_factor'].fillna(1.0)
        df_tf['dividend_cash'] = df_tf['dividend_cash'].fillna(0.0)

        for col in ['open', 'high', 'low', 'close']:
            raw_col = f'{col}_raw'
            adj_col = f'{col}_adj'
            if raw_col in df_tf.columns:
                df_tf[adj_col] = df_tf[raw_col] * df_tf['adj_factor']
            
        df_tf = df_tf.drop(columns=['market_date'])
        
        df_tf['symbol'] = symbol.upper()
        df_tf['timeframe'] = tf
        df_tf['ingested_at_utc'] = datetime.now(timezone.utc)  # Force update to current time to ensure overwrite
        
        save_to_warehouse(df_tf)

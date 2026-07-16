import numpy as np
import pandas as pd

def compute_price_features(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
        
    df = df.sort_values("bar_ts_utc").reset_index(drop=True)
    
    close = df['close_adj']
    high = df['high_adj']
    low = df['low_adj']
    
    for period in [10, 20, 50]:
        sma = close.rolling(period).mean()
        df[f'feat_ext_vs_ma_{period}'] = (close / sma) - 1.0
        
    high_low = high - low
    high_close_prev = (high - close.shift(1)).abs()
    low_close_prev = (low - close.shift(1)).abs()
    
    tr = pd.concat([high_low, high_close_prev, low_close_prev], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    
    sma_20 = close.rolling(20).mean()
    df['feat_atr_norm_ext'] = (close - sma_20) / atr
    
    for ret_period in [1, 3, 5, 10, 20]:
        df[f'feat_ret_{ret_period}'] = close.pct_change(ret_period)
        
    ret_1 = df['feat_ret_1']
    df['feat_realized_vol_20'] = ret_1.rolling(20).std()
    
    vol_60 = ret_1.rolling(60).std()
    df['feat_vol_regime'] = df['feat_realized_vol_20'] / vol_60
    
    def compute_slope(series):
        x = np.arange(len(series))
        y = series.values
        if len(y) < 20 or np.isnan(y).any():
            return np.nan
        slope, _ = np.polyfit(x, y, 1)
        return slope / y[-1]
        
    df['feat_trend_slope_20'] = close.rolling(20).apply(compute_slope, raw=False)
    
    def compute_trend_strength(series):
        x = np.arange(len(series))
        y = series.values
        if len(y) < 20 or np.isnan(y).any():
            return np.nan
        slope, intercept = np.polyfit(x, y, 1)
        y_pred = slope * x + intercept
        ss_res = np.sum((y - y_pred) ** 2)
        ss_tot = np.sum((y - np.mean(y)) ** 2)
        return 1 - (ss_res / ss_tot) if ss_tot > 0 else np.nan
        
    df['feat_trend_strength_20'] = close.rolling(20).apply(compute_trend_strength, raw=False)
    
    # Daily bars are stamped at midnight UTC (Tiingo) or early-UTC (Alpaca).
    # Converting those stamps to America/New_York shifts them to the PRIOR
    # evening, mislabeling every 1d bar's day-of-week/month (e.g. Monday's
    # bar tagged 'Sun'). The semantic market date for a daily bar is its UTC
    # date, so daily partitions take dow/month from UTC directly. Intraday
    # bars have real clock times and keep the NY conversion.
    ts = df['bar_ts_utc']
    is_daily = False
    if len(ts) >= 2:
        median_gap = ts.diff().dropna().median()
        is_daily = pd.notna(median_gap) and median_gap >= pd.Timedelta(hours=23)

    if is_daily:
        df['feat_dow'] = ts.dt.dayofweek
        df['feat_month'] = ts.dt.month
        # Session buckets are meaningless on daily bars; pin to the close
        # bucket (2) for every row so the label is constant instead of an
        # artifact of each source's timestamp convention.
        df['feat_session_bucket'] = 2
    else:
        ny_time = ts.dt.tz_convert('America/New_York')
        df['feat_dow'] = ny_time.dt.dayofweek
        df['feat_month'] = ny_time.dt.month

        def get_session_bucket(hour, minute):
            time_val = hour + minute / 60.0
            if time_val < 11.5:
                return 0
            elif time_val < 13.5:
                return 1
            else:
                return 2

        df['feat_session_bucket'] = np.vectorize(get_session_bucket)(ny_time.dt.hour, ny_time.dt.minute)

    # Additive substrate-agnostic timing features used by the isolated crypto
    # lane. These do NOT change the meaning of the existing equity-native
    # feat_session_bucket/state_session contract.
    utc_time = ts.dt.tz_convert('UTC')
    df['feat_utc_hour'] = utc_time.dt.hour

    def get_utc_session_bucket(hour):
        if hour < 8:
            return 0
        elif hour < 16:
            return 1
        return 2

    df['feat_utc_session_bucket'] = np.vectorize(get_utc_session_bucket)(utc_time.dt.hour)
    df['feat_weekpart'] = (utc_time.dt.dayofweek >= 5).astype(int)

    bars_per_day = 1
    if not is_daily and len(ts) >= 2:
        if median_gap <= pd.Timedelta(minutes=20):
            bars_per_day = 96
        elif median_gap <= pd.Timedelta(minutes=70):
            bars_per_day = 24
        else:
            approx = pd.Timedelta(days=1) / median_gap if median_gap > pd.Timedelta(0) else 1
            bars_per_day = max(1, int(round(float(approx))))

    if bars_per_day <= 1:
        df['feat_ret_day_equiv'] = df['feat_ret_1']
        df['feat_realized_vol_day_equiv'] = df['feat_realized_vol_20']
    else:
        df['feat_ret_day_equiv'] = close.pct_change(bars_per_day)
        df['feat_realized_vol_day_equiv'] = ret_1.rolling(bars_per_day).std()

    # Traded-volume participation relative to the symbol's own recent
    # baseline ("who showed up on this bar"). Intraday volume has a strong
    # deterministic time-of-day shape (open/close heavy, lunch light), so
    # intraday bars are normalised against the same UTC hour over the
    # trailing ~30 occurrences; daily bars use a trailing 20-bar median.
    # ~1.0 is ordinary flow; large values mark the uncommonly heavy
    # participation that institutional-size prints leave behind. Volume is
    # landed in the warehouse for every substrate but was previously never
    # used by any feature.
    if 'volume_raw' in df.columns:
        vol_series = pd.to_numeric(df['volume_raw'], errors='coerce').astype(float)
    else:
        vol_series = pd.Series(np.nan, index=df.index, dtype=float)
    if bars_per_day > 1 and 'feat_utc_hour' in df.columns:
        vol_baseline = vol_series.groupby(df['feat_utc_hour']).transform(
            lambda s: s.rolling(30, min_periods=5).median()
        )
    else:
        vol_baseline = vol_series.rolling(20, min_periods=5).median()
    df['feat_volume_rel'] = vol_series / vol_baseline
    df['feat_volume_rel'] = df['feat_volume_rel'].replace([np.inf, -np.inf], np.nan)

    df['feat_gap'] = (close / close.shift(1)) - 1.0
    df['feat_range_position'] = (close - low) / (high - low + 1e-8)

    # ── T5 macro-event blackout flag (pure risk control, no lookahead) ──
    # Date set is hardcoded static calendar; no external fetcher needed.
    try:
        from price.external_data import attach_blackout_flag
        df = attach_blackout_flag(df)
    except Exception:
        df['feat_event_blackout'] = 0
    
    df['fwd_ret_3'] = close.shift(-3) / close - 1.0
    df['fwd_ret_5'] = close.shift(-5) / close - 1.0
    
    fwd_high_5 = high.rolling(5).max().shift(-5)
    df['fwd_mfe_5'] = (fwd_high_5 / close) - 1.0
    
    fwd_low_5 = low.rolling(5).min().shift(-5)
    df['fwd_mae_5'] = (fwd_low_5 / close) - 1.0
    
    cost = 0.0002
    df['target_positive_5bar'] = (df['fwd_ret_5'] > cost).astype(int)
    
    df['label_eligible'] = ~df['fwd_ret_5'].isna()
    
    return df

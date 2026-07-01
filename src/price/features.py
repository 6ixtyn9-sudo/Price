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
    
    for ret_period in [1, 3, 5, 10]:
        df[f'feat_ret_{ret_period}'] = close.pct_change(ret_period)
        
    ret_1 = df['feat_ret_1']
    df['feat_realized_vol_20'] = ret_1.rolling(20).std()
    
    def compute_slope(series):
        x = np.arange(len(series))
        y = series.values
        if len(y) < 20 or np.isnan(y).any():
            return np.nan
        slope, _ = np.polyfit(x, y, 1)
        return slope / y[-1]
        
    df['feat_trend_slope_20'] = close.rolling(20).apply(compute_slope, raw=False)
    
    ny_time = df['bar_ts_utc'].dt.tz_convert('America/New_York')
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
    
    df['fwd_ret_3'] = close.shift(-3) / close - 1.0
    df['fwd_ret_5'] = close.shift(-5) / close - 1.0
    
    fwd_high_5 = high.rolling(5).max().shift(-5)
    df['fwd_mfe_5'] = (fwd_high_5 / close) - 1.0
    
    fwd_low_5 = low.rolling(5).min().shift(-5)
    df['fwd_mae_5'] = (fwd_low_5 / close) - 1.0
    
    df['label_eligible'] = ~df['fwd_ret_5'].isna()
    
    return df

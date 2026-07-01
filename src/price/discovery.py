import pandas as pd
import numpy as np
from typing import List

from price.warehouse import load_from_warehouse
from price.features import compute_price_features

def bin_features(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
        
    df_binned = df.copy()
    
    def bin_ext(val):
        if pd.isna(val):
            return np.nan
        if val < -0.015:
            return "stretched_down"
        elif val > 0.015:
            return "stretched_up"
        else:
            return "neutral"
            
    df_binned['state_ext'] = df_binned['feat_ext_vs_ma_20'].apply(bin_ext)
    
    if 'feat_trend_slope_20' in df_binned.columns and not df_binned['feat_trend_slope_20'].dropna().empty:
        try:
            df_binned['state_slope'] = pd.qcut(
                df_binned['feat_trend_slope_20'], 
                q=3, 
                labels=["downtrend", "flat", "uptrend"]
            )
        except Exception:
            df_binned['state_slope'] = "flat"
    else:
        df_binned['state_slope'] = "flat"
        
    if 'feat_realized_vol_20' in df_binned.columns and not df_binned['feat_realized_vol_20'].dropna().empty:
        try:
            df_binned['state_vol'] = pd.qcut(
                df_binned['feat_realized_vol_20'], 
                q=3, 
                labels=["low_vol", "mid_vol", "high_vol"]
            )
        except Exception:
            df_binned['state_vol'] = "mid_vol"
    else:
        df_binned['state_vol'] = "mid_vol"
        
    session_map = {0: "morning", 1: "lunch", 2: "afternoon"}
    df_binned['state_session'] = df_binned['feat_session_bucket'].map(session_map)
    
    dow_map = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}
    df_binned['state_dow'] = df_binned['feat_dow'].map(dow_map)
    
    return df_binned

def discover_market_slices(
    symbol: str, 
    timeframe: str, 
    slice_fields: List[str], 
    min_samples: int = 15
) -> pd.DataFrame:
    df_raw = load_from_warehouse(symbol, timeframe)
    if df_raw.empty:
        print(f"No warehouse data found for {symbol} ({timeframe}).")
        return pd.DataFrame()
        
    df_feat = compute_price_features(df_raw)
    df_binned = bin_features(df_feat)
    
    eval_df = df_binned[df_binned['label_eligible']]
    if eval_df.empty:
        print(f"No eligible forward-looking evaluation rows for {symbol} ({timeframe}).")
        return pd.DataFrame()
        
    for f in slice_fields:
        if f not in eval_df.columns:
            raise ValueError(f"Slice feature field '{f}' is not available in the state DataFrame.")
            
    grouped = eval_df.groupby(slice_fields)
    
    slice_metrics = []
    for keys, group in grouped:
        n = len(group)
        if n < min_samples:
            continue
            
        slice_key = " + ".join([f"{f}={k}" for f, k in zip(slice_fields, keys if isinstance(keys, tuple) else [keys])])
        
        mean_ret = group['fwd_ret_5'].mean()
        std_ret = group['fwd_ret_5'].std()
        win_rate = (group['fwd_ret_5'] > 0).sum() / n
        
        mean_mfe = group['fwd_mfe_5'].mean()
        mean_mae = group['fwd_mae_5'].mean()
        
        ratio = mean_ret / std_ret if std_ret > 0 else 0.0
        
        slice_metrics.append({
            'symbol': symbol,
            'timeframe': timeframe,
            'slice_combination': slice_key,
            'sample_count': n,
            'mean_fwd_ret_5': mean_ret,
            'win_rate': win_rate,
            'mean_mfe_5': mean_mfe,
            'mean_mae_5': mean_mae,
            'fwd_ret_ratio': ratio
        })
        
    df_slices = pd.DataFrame(slice_metrics)
    if not df_slices.empty:
        df_slices = df_slices.sort_values("mean_fwd_ret_5", ascending=False).reset_index(drop=True)
        
    return df_slices

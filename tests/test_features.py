import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta

from price.features import compute_price_features

def test_compute_price_features_basic():
    base_time = datetime(2026, 6, 1, 13, 30, tzinfo=timezone.utc)
    timestamps = [base_time + timedelta(minutes=15 * i) for i in range(60)]

    df = pd.DataFrame({
        'symbol': ['SPY'] * 60,
        'timeframe': ['15m'] * 60,
        'bar_ts_utc': timestamps,
        'source': ['alpaca'] * 60,
        'ingested_at_utc': [datetime.now(timezone.utc)] * 60,
        'open_raw': np.linspace(100, 110, 60),
        'high_raw': np.linspace(101, 111, 60),
        'low_raw': np.linspace(99, 109, 60),
        'close_raw': np.linspace(100.5, 110.5, 60),
        'volume_raw': [1000] * 60,
        'open_adj': np.linspace(100, 110, 60),
        'high_adj': np.linspace(101, 111, 60),
        'low_adj': np.linspace(99, 109, 60),
        'close_adj': np.linspace(100.5, 110.5, 60),
        'adj_factor': [1.0] * 60,
        'split_factor': [1.0] * 60,
        'dividend_cash': [0.0] * 60
    })
    
    featured = compute_price_features(df)
    
    assert 'feat_ext_vs_ma_10' in featured.columns
    assert 'feat_ext_vs_ma_20' in featured.columns
    assert 'feat_ext_vs_ma_50' in featured.columns
    assert 'feat_atr_norm_ext' in featured.columns
    assert 'feat_ret_1' in featured.columns
    assert 'feat_realized_vol_20' in featured.columns
    assert 'feat_trend_slope_20' in featured.columns
    assert 'feat_dow' in featured.columns
    assert 'feat_session_bucket' in featured.columns
    assert 'feat_utc_hour' in featured.columns
    assert 'feat_utc_session_bucket' in featured.columns
    assert 'feat_weekpart' in featured.columns
    assert 'feat_ret_day_equiv' in featured.columns
    assert 'feat_realized_vol_day_equiv' in featured.columns
    
    assert 'fwd_ret_3' in featured.columns
    assert 'fwd_ret_5' in featured.columns
    assert 'fwd_mfe_5' in featured.columns
    assert 'fwd_mae_5' in featured.columns
    assert 'label_eligible' in featured.columns
    
    assert not pd.isna(featured.loc[59, 'feat_ext_vs_ma_50'])
    assert not featured.loc[59, 'label_eligible']
    assert featured.loc[50, 'label_eligible']

def test_no_look_ahead_bias():
    base_time = datetime(2026, 6, 1, 13, 30, tzinfo=timezone.utc)
    timestamps = [base_time + timedelta(minutes=15 * i) for i in range(60)]
    
    df_base = pd.DataFrame({
        'symbol': ['SPY'] * 60,
        'timeframe': ['15m'] * 60,
        'bar_ts_utc': timestamps,
        'source': ['alpaca'] * 60,
        'ingested_at_utc': [datetime.now(timezone.utc)] * 60,
        'open_raw': np.linspace(100, 110, 60),
        'high_raw': np.linspace(101, 111, 60),
        'low_raw': np.linspace(99, 109, 60),
        'close_raw': np.linspace(100.5, 110.5, 60),
        'volume_raw': [1000] * 60,
        'open_adj': np.linspace(100, 110, 60),
        'high_adj': np.linspace(101, 111, 60),
        'low_adj': np.linspace(99, 109, 60),
        'close_adj': np.linspace(100.5, 110.5, 60),
        'adj_factor': [1.0] * 60,
        'split_factor': [1.0] * 60,
        'dividend_cash': [0.0] * 60
    })
    
    df_diverged = df_base.copy()
    df_diverged.loc[41:, 'close_adj'] = df_diverged.loc[41:, 'close_adj'] * 2.0
    df_diverged.loc[41:, 'close_raw'] = df_diverged.loc[41:, 'close_raw'] * 2.0
    
    feat_base = compute_price_features(df_base)
    feat_div = compute_price_features(df_diverged)
    
    feature_cols = [col for col in feat_base.columns if col.startswith('feat_')]
    
    for col in feature_cols:
        pd.testing.assert_series_equal(
            feat_base.loc[:40, col],
            feat_div.loc[:40, col],
            obj=f"Look-ahead bias detected in feature column: {col}"
        )

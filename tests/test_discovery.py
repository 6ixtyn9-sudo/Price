import pandas as pd
from datetime import datetime, timezone, timedelta

import price.warehouse
from price.discovery import bin_features, discover_market_slices

def test_bin_features():
    df = pd.DataFrame({
        'feat_ext_vs_ma_20': [-0.02, 0.0, 0.03],
        'feat_trend_slope_20': [0.1, 0.2, 0.3],
        'feat_realized_vol_20': [0.01, 0.02, 0.03],
        'feat_session_bucket': [0, 1, 2],
        'feat_dow': [0, 2, 4]
    })
    
    binned = bin_features(df)
    assert binned.loc[0, 'state_ext'] == "stretched_down"
    assert binned.loc[1, 'state_ext'] == "neutral"
    assert binned.loc[2, 'state_ext'] == "stretched_up"
    assert binned.loc[0, 'state_session'] == "morning"
    assert binned.loc[1, 'state_session'] == "lunch"
    assert binned.loc[2, 'state_session'] == "afternoon"
    assert binned.loc[0, 'state_dow'] == "Mon"
    assert binned.loc[2, 'state_dow'] == "Fri"

def test_discover_market_slices(tmp_path):
    old_dir = price.warehouse.WAREHOUSE_DIR
    price.warehouse.WAREHOUSE_DIR = tmp_path
    
    base_time = datetime(2026, 6, 1, tzinfo=timezone.utc)
    timestamps = [base_time + timedelta(days=i) for i in range(60)]
    
    close_vals = []
    current = 100.0
    for i in range(60):
        if i < 40:
            current += 1.0
        else:
            current -= 1.0
        close_vals.append(current)
        
    df = pd.DataFrame({
        'symbol': ['SPY'] * 60,
        'timeframe': ['1d'] * 60,
        'bar_ts_utc': timestamps,
        'source': ['tiingo'] * 60,
        'ingested_at_utc': [datetime.now(timezone.utc)] * 60,
        'open_raw': close_vals,
        'high_raw': [c + 1.0 for c in close_vals],
        'low_raw': [c - 1.0 for c in close_vals],
        'close_raw': close_vals,
        'volume_raw': [1000] * 60,
        'open_adj': close_vals,
        'high_adj': [c + 1.0 for c in close_vals],
        'low_adj': [c - 1.0 for c in close_vals],
        'close_adj': close_vals,
        'adj_factor': [1.0] * 60,
        'split_factor': [1.0] * 60,
        'dividend_cash': [0.0] * 60
    })
    
    price.warehouse.save_to_warehouse(df)
    
    slices = discover_market_slices('SPY', '1d', ['state_slope'], min_samples=2)
    
    price.warehouse.WAREHOUSE_DIR = old_dir
    
    assert not slices.empty
    assert 'slice_combination' in slices.columns
    assert 'sample_count' in slices.columns
    assert 'mean_fwd_ret_5' in slices.columns
    assert slices.loc[0, 'sample_count'] >= 2

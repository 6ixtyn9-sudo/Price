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


# ---------------------------------------------------------------------------
# Demand-side states (2026-08, momentum doctrine): fixed-prior thresholds
# sourced from the Warrior Trading 5-pillar selection guide — state_relvol
# (2x/5x/20x relative-volume demand bands) and state_gap_open (OPEN vs prior
# CLOSE, +/-2%/+/-5%). Fixed priors -> identical in rolling mode.
# ---------------------------------------------------------------------------

def test_demand_state_bins_fixed_thresholds():
    from price.discovery import STATE_LABELS, ML_FEATURE_TO_STATE

    df = pd.DataFrame({
        'feat_ext_vs_ma_20': [0.0] * 6,
        'feat_volume_rel': [0.5, 1.0, 3.0, 7.0, 25.0, float("nan")],
        'feat_gap_open': [0.01, -0.01, 0.03, 0.08, -0.06, float("nan")],
    })
    binned = bin_features(df)
    assert binned['state_relvol'].tolist()[:5] == [
        "relvol_quiet", "relvol_normal", "relvol_elevated",
        "relvol_high", "relvol_extreme",
    ]
    assert binned['state_gap_open'].tolist()[:5] == [
        "gap_open_flat", "gap_open_flat", "gap_open_up",
        "gap_open_big_up", "gap_open_big_down",
    ]
    # NaN source -> NaN state (repo convention: same as state_volume with a
    # NaN feature; the neutral fallback applies only when the whole column
    # is absent, asserted below on `bare`)
    assert pd.isna(binned.loc[5, 'state_relvol'])
    assert pd.isna(binned.loc[5, 'state_gap_open'])

    # Missing source feature entirely -> neutral fallback
    bare = bin_features(pd.DataFrame({'feat_ext_vs_ma_20': [0.0, 0.0]}))
    assert bare['state_relvol'].iloc[0] == "relvol_normal"
    assert bare['state_gap_open'].iloc[0] == "gap_open_flat"

    # Vocab is registered and ordered low -> high (drift guard)
    assert ML_FEATURE_TO_STATE['feat_gap_open'] == 'state_gap_open'
    assert STATE_LABELS['state_relvol'][-1] == 'relvol_extreme'
    assert STATE_LABELS['state_gap_open'][0] == 'gap_open_big_down'


def test_demand_state_bins_identical_in_rolling_mode():
    """Fixed-prior states must label identically in rolling mode (the
    deployment/research alignment invariant: the live monitor bins over the
    same fixed thresholds as discovery)."""
    from price.discovery import bin_features_rolling

    df = pd.DataFrame({
        'feat_ext_vs_ma_20': [0.0] * 6,
        'feat_trend_slope_20': [0.1] * 6,
        'feat_realized_vol_20': [0.02] * 6,
        'feat_volume_rel': [0.5, 1.0, 3.0, 7.0, 25.0, float("nan")],
        'feat_gap_open': [0.01, -0.01, 0.03, 0.08, -0.06, float("nan")],
    })
    ins = bin_features(df)
    rol = bin_features_rolling(df, min_periods=2)
    for col in ('state_relvol', 'state_gap_open'):
        assert ins[col].tolist() == rol[col].tolist(), col


def test_demand_state_bins_boundary_symmetry():
    """Exact thresholds are symmetric: <= -5% / >= +5% = big buckets,
    -5%<v<-2% (down) and +2%<v<+5% (up) = plain buckets, |v|<=2% = flat.
    Relvol: 2/5/20 land in their high band, just-below stays lower."""
    df = pd.DataFrame({
        "feat_ext_vs_ma_20": [0.0] * 9,
        "feat_volume_rel": [0.7, 1.99, 2.0, 4.99, 5.0, 19.99, 20.0, 0.69, 1.0],
        "feat_gap_open": [-0.05, -0.049, -0.02, -0.019, 0.0, 0.02, 0.021, 0.05, 0.049],
    })
    b = bin_features(df)
    assert b["state_relvol"].tolist() == [
        "relvol_normal", "relvol_normal", "relvol_elevated", "relvol_elevated",
        "relvol_high", "relvol_high", "relvol_extreme", "relvol_quiet", "relvol_normal",
    ]
    assert b["state_gap_open"].tolist() == [
        "gap_open_big_down", "gap_open_down", "gap_open_flat", "gap_open_flat",
        "gap_open_flat", "gap_open_flat", "gap_open_up", "gap_open_big_up", "gap_open_up",
    ]

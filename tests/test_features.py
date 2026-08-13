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


def test_feat_gap_open_is_open_vs_prior_close():
    """feat_gap_open must be the true OPEN-to-prior-CLOSE gap (the demand
    signal), not the close-to-close return feat_gap measures."""
    from price.features import compute_price_features
    import numpy as np

    n = 30
    ts = pd.date_range("2025-03-01", periods=n, freq="1d", tz="UTC")
    close = np.linspace(100.0, 110.0, n)
    open_px = close.copy()
    open_px[10] = 104.0   # +~0.55% vs prior close 103.45 -> small up gap
    open_px[15] = 96.0    # -~7.4% vs prior close 103.68 -> big down gap
    df = pd.DataFrame({
        "bar_ts_utc": ts,
        "open_raw": open_px,
        "high_raw": np.maximum(open_px, close) + 0.1,
        "low_raw": np.minimum(open_px, close) - 0.1,
        "close_raw": close,
        "volume_raw": [1000] * n,
        "open_adj": open_px,
        "high_adj": np.maximum(open_px, close) + 0.1,
        "low_adj": np.minimum(open_px, close) - 0.1,
        "close_adj": close,
        "adj_factor": [1.0] * n,
        "split_factor": [1.0] * n,
        "dividend_cash": [0.0] * n,
    })
    feat = compute_price_features(df)
    assert "feat_gap_open" in feat.columns
    assert abs(feat["feat_gap_open"].iloc[10] - (104.0 / close[9] - 1.0)) < 1e-9
    assert feat["feat_gap_open"].iloc[15] < -0.05


def test_feat_gap_open_missing_open_adj_degrades_gracefully():
    """Frames without open_adj (legacy synthetic fixtures) must not crash;
    the column is simply absent and the state bins to its neutral fallback."""
    from price.features import compute_price_features
    import numpy as np

    n = 30
    ts = pd.date_range("2025-03-01", periods=n, freq="1d", tz="UTC")
    close = np.linspace(100.0, 110.0, n)
    df = pd.DataFrame({
        "bar_ts_utc": ts,
        "open_raw": close, "high_raw": close + 0.1,
        "low_raw": close - 0.1, "close_raw": close,
        "volume_raw": [1000] * n,
        "high_adj": close + 0.1, "low_adj": close - 0.1, "close_adj": close,
        "adj_factor": [1.0] * n, "split_factor": [1.0] * n,
        "dividend_cash": [0.0] * n,
    })
    feat = compute_price_features(df)
    assert "feat_gap_open" not in feat.columns


def test_feat_gap_open_degenerate_denominator_is_nan():
    """A zero/NaN prior close must yield NaN (neutral state downstream),
    never inf -> a spurious extreme gap bucket."""
    from price.features import compute_price_features
    import numpy as np

    n = 30
    ts = pd.date_range("2025-03-01", periods=n, freq="1d", tz="UTC")
    close = np.linspace(100.0, 110.0, n)
    close[9] = 0.0  # poison: bar 10's prior close is zero
    open_px = close.copy()
    df = pd.DataFrame({
        "bar_ts_utc": ts,
        "open_raw": open_px, "high_raw": np.maximum(open_px, close) + 0.1,
        "low_raw": np.minimum(open_px, close) - 0.1, "close_raw": close,
        "volume_raw": [1000] * n,
        "open_adj": open_px, "high_adj": np.maximum(open_px, close) + 0.1,
        "low_adj": np.minimum(open_px, close) - 0.1, "close_adj": close,
        "adj_factor": [1.0] * n, "split_factor": [1.0] * n,
        "dividend_cash": [0.0] * n,
    })
    feat = compute_price_features(df)
    assert "feat_gap_open" in feat.columns
    assert pd.isna(feat["feat_gap_open"].iloc[10])

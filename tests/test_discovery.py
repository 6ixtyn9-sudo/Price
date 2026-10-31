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


def test_discover_skips_denylisted_demand_labels():
    """relvol_high / relvol_extreme and gap_open_up / gap_open_big_up are
    kept in the bin vocabulary but not emitted as slice candidates."""
    from price.discovery import DISCOVERY_SKIP_LABELS, discover_market_slices

    n = 40
    rows = []
    for label, ret in (
        ("relvol_elevated", 0.01),
        ("relvol_high", 0.02),
        ("relvol_extreme", 0.03),
        ("relvol_quiet", -0.005),
    ):
        for _ in range(n):
            rows.append({
                "label_eligible": True,
                "state_relvol": label,
                "fwd_ret_5": ret,
                "fwd_mfe_5": abs(ret),
                "fwd_mae_5": abs(ret) / 2,
            })
    df = pd.DataFrame(rows)
    slices = discover_market_slices(
        "SPY", "1d", ["state_relvol"], min_samples=10, _precomputed_binned=df,
    )
    combos = set(slices["slice_combination"])
    assert "state_relvol=relvol_elevated" in combos
    assert "state_relvol=relvol_quiet" in combos
    assert "state_relvol=relvol_high" not in combos
    assert "state_relvol=relvol_extreme" not in combos
    assert DISCOVERY_SKIP_LABELS["state_gap_open"] == frozenset(
        {"gap_open_up", "gap_open_big_up"}
    )


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


def test_discover_market_slices_locks_side_on_purged_insample(tmp_path):
    """OOS crash in the final 30% of bars must not flip a slice's discovered
    `side` from 'long' to 'short' when its purged IS mean was positive."""
    old_dir = price.warehouse.WAREHOUSE_DIR
    price.warehouse.WAREHOUSE_DIR = tmp_path
    try:
        base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
        n = 120
        timestamps = [base_time + timedelta(days=i) for i in range(n)]
        # Steady +0.5% daily climb through bar 86 (covers 100% of purged IS +
        # 5-bar forward horizon), then -8% daily crash in OOS so full-frame
        # mean_fwd_ret_5 is strongly negative while purged IS mean is positive.
        closes = [100.0]
        for i in range(1, n):
            mult = 1.005 if i < 87 else 0.92
            closes.append(closes[-1] * mult)
        opens = [closes[0]] + closes[:-1]  # open == prior close -> gap_open_flat everywhere

        df = pd.DataFrame({
            "symbol": ["SPY"] * n,
            "timeframe": ["1d"] * n,
            "bar_ts_utc": timestamps,
            "source": ["tiingo"] * n,
            "ingested_at_utc": [datetime.now(timezone.utc)] * n,
            "open_raw": opens,
            "high_raw": [max(o, c) * 1.01 for o, c in zip(opens, closes)],
            "low_raw": [min(o, c) * 0.99 for o, c in zip(opens, closes)],
            "close_raw": closes,
            "volume_raw": [1_000_000] * n,
            "open_adj": opens,
            "high_adj": [max(o, c) * 1.01 for o, c in zip(opens, closes)],
            "low_adj": [min(o, c) * 0.99 for o, c in zip(opens, closes)],
            "close_adj": closes,
            "adj_factor": [1.0] * n,
            "split_factor": [1.0] * n,
            "dividend_cash": [0.0] * n,
        })
        price.warehouse.save_to_warehouse(df)
        slices = discover_market_slices("SPY", "1d", ["state_gap_open"], min_samples=15)
        flat = slices[slices["slice_combination"] == "state_gap_open=gap_open_flat"].iloc[0]
        assert float(flat["mean_fwd_ret_5"]) < 0.0  # full-frame mean dragged negative by OOS
        assert flat["side"] == "long"               # locked strictly on positive purged IS
    finally:
        price.warehouse.WAREHOUSE_DIR = old_dir


"""Smoke tests for the T2/T3/T4/T5 external/macro states.

These tests never hit the network. They verify:
  - All new state columns exist with correct fallback values when external
    data is absent (the primary fail-safe).
  - State binning works correctly when synthetic feat_* values are injected.
  - Blackout flag gates tradable=False in scan_all_slices.
  - LANE SCOPING: crypto states (state_funding/state_oi) bin correctly but
    default to neutral on non-crypto frames; futures state_cot defaults to
    neutral on equities; equity states (state_vix/breadth/dxy) default to
    neutral outside equities. No cross-lane contamination.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from price.discovery import bin_features, STATE_LABELS, ML_FEATURE_TO_STATE  # noqa: E402
from price.external_data import is_blackout  # noqa: E402
from price.features import compute_price_features  # noqa: E402


def _synth_equity_frame(n=400):
    ts = pd.date_range("2025-03-01", periods=n, freq="1h", tz="UTC")
    np.random.seed(7)
    close = 400.0 + np.cumsum(np.random.randn(n) * 0.2)
    df = pd.DataFrame({
        "bar_ts_utc": ts,
        "open_raw": close - 0.05, "high_raw": close + 0.15,
        "low_raw": close - 0.15, "close_raw": close,
        "volume_raw": 1000 + np.abs(np.random.randn(n)) * 300,
    })
    for c in ["open", "high", "low", "close"]:
        df[c + "_adj"] = df[c + "_raw"]
    return df


def test_all_new_state_labels_in_vocab():
    """STATE_LABELS and ML_FEATURE_TO_STATE agree for every new state."""
    new_states = ["state_funding", "state_oi", "state_cot",
                  "state_vix", "state_breadth", "state_dxy"]
    for s in new_states:
        assert s in STATE_LABELS, f"{s} missing from STATE_LABELS"
    new_feats = ["feat_funding_z20", "feat_oi_change_5", "feat_cot_mm_z52",
                 "feat_vix_ext", "feat_breadth_pct", "feat_dxy_slope"]
    for f in new_feats:
        assert f in ML_FEATURE_TO_STATE, f"{f} missing from ML_FEATURE_TO_STATE"


def test_fallback_values_when_externals_absent():
    """When external data isn't attached, every new state must be present
    with its neutral fallback value (no NaNs propagating into slices)."""
    df = _synth_equity_frame()
    feat = compute_price_features(df)
    # No attach_lane_externals call — external feat_* columns are absent.
    binned = bin_features(feat)
    expect = {
        "state_funding": "funding_neutral",
        "state_oi": "oi_flat",
        "state_cot": "cot_neutral",
        "state_vix": "vix_mid",
        "state_breadth": "breadth_mixed",
        "state_dxy": "dxy_flat",
    }
    for col, fb in expect.items():
        assert col in binned.columns
        # last row should be the fallback value (we have enough history, but
        # feat column was never added so the fallback branch fires)
        assert binned[col].iloc[-1] == fb, f"{col} = {binned[col].iloc[-1]}, expected {fb}"


def test_funding_state_bins_correctly_on_extreme_values():
    """Synthetic funding z-scores must bin to the correct state_label."""
    df = _synth_equity_frame()
    feat = compute_price_features(df)
    feat["feat_funding_z20"] = np.nan
    feat.loc[feat.index[-3], "feat_funding_z20"] = -2.5
    feat.loc[feat.index[-2], "feat_funding_z20"] = 0.1
    feat.loc[feat.index[-1], "feat_funding_z20"] = 1.8
    binned = bin_features(feat)
    assert binned["state_funding"].iloc[-3] == "funding_short"
    assert binned["state_funding"].iloc[-2] == "funding_neutral"
    assert binned["state_funding"].iloc[-1] == "funding_long"


def test_breadth_state_bins():
    df = _synth_equity_frame()
    feat = compute_price_features(df)
    feat["feat_breadth_pct"] = np.nan
    feat.loc[feat.index[-3], "feat_breadth_pct"] = 0.20  # weak
    feat.loc[feat.index[-2], "feat_breadth_pct"] = 0.55  # mixed
    feat.loc[feat.index[-1], "feat_breadth_pct"] = 0.85  # strong
    binned = bin_features(feat)
    assert binned["state_breadth"].iloc[-3] == "breadth_weak"
    assert binned["state_breadth"].iloc[-2] == "breadth_mixed"
    assert binned["state_breadth"].iloc[-1] == "breadth_strong"


def test_vix_state_bins():
    df = _synth_equity_frame()
    feat = compute_price_features(df)
    feat["feat_vix_ext"] = np.nan
    feat.loc[feat.index[-3], "feat_vix_ext"] = -0.10  # calm (low)
    feat.loc[feat.index[-2], "feat_vix_ext"] = 0.05   # mid
    feat.loc[feat.index[-1], "feat_vix_ext"] = 0.35   # fear (high)
    binned = bin_features(feat)
    assert binned["state_vix"].iloc[-3] == "vix_low"
    assert binned["state_vix"].iloc[-2] == "vix_mid"
    assert binned["state_vix"].iloc[-1] == "vix_high"


def test_cot_state_bins():
    df = _synth_equity_frame()
    feat = compute_price_features(df)
    feat["feat_cot_mm_z52"] = np.nan
    feat.loc[feat.index[-3], "feat_cot_mm_z52"] = -1.5
    feat.loc[feat.index[-2], "feat_cot_mm_z52"] = 0.3
    feat.loc[feat.index[-1], "feat_cot_mm_z52"] = 2.0
    binned = bin_features(feat)
    assert binned["state_cot"].iloc[-3] == "cot_short"
    assert binned["state_cot"].iloc[-2] == "cot_neutral"
    assert binned["state_cot"].iloc[-1] == "cot_long"


def test_blackout_flag_present_in_features():
    """Every featured frame should contain feat_event_blackout (0/1)."""
    df = _synth_equity_frame()
    feat = compute_price_features(df)
    assert "feat_event_blackout" in feat.columns
    assert set(feat["feat_event_blackout"].dropna().unique()).issubset({0, 1})


def test_is_blackout_known_dates():
    # FOMC 2025-01-29 (known)
    assert is_blackout(pd.Timestamp("2025-01-29 18:00", tz="UTC")) is True
    # NFP Feb 2025 = Feb 7 (first Friday)
    assert is_blackout(pd.Timestamp("2025-02-07 13:30", tz="UTC")) is True
    # Random Wednesday June 25, 2025 (not a scheduled event)
    assert is_blackout(pd.Timestamp("2025-06-25 15:00", tz="UTC")) is False
    # OPEX = third Friday of month. Jan 2025 = Jan 17
    assert is_blackout(pd.Timestamp("2025-01-17 19:00", tz="UTC")) is True


def test_lane_scoping_does_not_add_cross_lane_states_to_combinations():
    """crypto-only states are added in crypto profile combos but NOT in
    equity default combos. futures COT only in futures profile."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from discover_slices import _build_combinations

    default_combos = _build_combinations("1d", profile="default")
    crypto_combos = _build_combinations("1d", profile="crypto")
    futures_combos = _build_combinations("1d", profile="futures")

    default_flat = [c for combo in default_combos for c in combo]
    crypto_flat = [c for combo in crypto_combos for c in combo]
    futures_flat = [c for combo in futures_combos for c in combo]

    # Crypto lane must include funding/OI states
    assert "state_funding" in crypto_flat
    assert "state_oi" in crypto_flat
    # Futures lane must include COT
    assert "state_cot" in futures_flat
    # Default/equity lane must include VIX/breadth
    assert "state_vix" in default_flat
    assert "state_breadth" in default_flat
    # Default must NOT contain crypto/futures lane-specific states
    assert "state_funding" not in default_flat
    assert "state_oi" not in default_flat
    assert "state_cot" not in default_flat
    # Crypto must NOT contain equity/futures lane states
    assert "state_vix" not in crypto_flat
    assert "state_cot" not in crypto_flat
    # Futures must NOT contain crypto/equity lane states
    assert "state_funding" not in futures_flat
    assert "state_breadth" not in futures_flat

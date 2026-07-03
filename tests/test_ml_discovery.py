"""Tests for the ML discovery -> validation bridge (Phase V5).

These tests use small deterministic synthetic frames (the repo's established
style) rather than warehouse data or network calls. They verify that raw ML
feature interactions are correctly translated into the state_*=value slice
format that validate_slices.py consumes.
"""

import numpy as np
import pandas as pd

from price.discovery import ML_FEATURE_TO_STATE, STATE_LABELS, bin_features
from price.ml_discovery import (
    evaluate_interactions,
    interactions_to_state_slices,
    ml_interaction_to_state_slice,
)
from price.validation import apply_slice_filter, parse_slice_combination


def _synthetic_ml_frame(n=240, seed=7):
    """Build a frame shaped like prepare_ml_frame() output: feat_* columns
    plus fwd_ret_5, where the joint 'high extension + high recent return'
    region carries a positive forward return (so it would surface as a
    promising ML interaction)."""
    rng = np.random.default_rng(seed)
    feat_ext = rng.normal(0.0, 0.015, n)
    feat_ret3 = rng.normal(0.0, 0.01, n)
    feat_vol = rng.uniform(0.005, 0.03, n)

    df = pd.DataFrame(
        {
            "feat_ext_vs_ma_20": feat_ext,
            "feat_ret_3": feat_ret3,
            "feat_realized_vol_20": feat_vol,
            "feat_trend_slope_20": rng.normal(0.0, 0.001, n),
            "feat_ret_1": rng.normal(0.0, 0.005, n),
            "feat_ret_5": rng.normal(0.0, 0.008, n),
            "feat_ret_10": rng.normal(0.0, 0.01, n),
            "feat_ret_20": rng.normal(0.0, 0.012, n),
            "feat_atr_norm_ext": rng.normal(0.0, 1.0, n),
            "feat_vol_regime": rng.uniform(0.5, 1.5, n),
            "feat_trend_strength_20": rng.uniform(0.0, 1.0, n),
            "feat_gap": rng.normal(0.0, 0.005, n),
            "feat_range_position": rng.uniform(0.0, 1.0, n),
            # Calendar/session columns that compute_price_features always emits.
            "feat_session_bucket": rng.integers(0, 3, n),
            "feat_dow": rng.integers(0, 5, n),
            # Forward return is positive when ext and ret_3 are both high.
            "fwd_ret_5": 0.004 * feat_ext / 0.015 + 0.004 * feat_ret3 / 0.01
            + rng.normal(0.0, 0.002, n),
        }
    )
    return df


def test_bin_features_exposes_ml_state_columns():
    df = _synthetic_ml_frame()
    binned = bin_features(df)

    # Every state field promised by the ML feature -> state map must exist.
    for state_field in ML_FEATURE_TO_STATE.values():
        assert state_field in binned.columns, f"missing state column {state_field}"

    # Return features must be binned into the ternary ret vocabulary.
    for period in [1, 3, 5, 10, 20]:
        assert set(binned[f"state_ret_{period}"].dropna().astype(str).unique()) <= {
            "ret_down",
            "ret_flat",
            "ret_up",
        }


def test_state_labels_top_bucket_is_the_high_regime():
    # STATE_LABELS orders each field low -> high, so the last label is the
    # "high" regime the ML high-quantile region maps to.
    assert STATE_LABELS["state_ext"][-1] == "stretched_up"
    assert STATE_LABELS["state_ret_3"][-1] == "ret_up"
    assert STATE_LABELS["state_vol"][-1] == "high_vol"
    assert STATE_LABELS["state_slope"][-1] == "uptrend"


def test_ml_interaction_to_state_slice_maps_to_top_bucket():
    binned = bin_features(_synthetic_ml_frame())
    # evaluate_interactions tests the high-quantile side of each feature, so
    # each feature maps to its highest state bucket.
    slice_filter, mapped = ml_interaction_to_state_slice(
        binned, ["feat_ext_vs_ma_20", "feat_ret_3"]
    )

    assert mapped == ["feat_ext_vs_ma_20", "feat_ret_3"]
    assert slice_filter["state_ext"] == "stretched_up"
    assert slice_filter["state_ret_3"] == "ret_up"


def test_ml_interaction_to_state_slice_skips_unmapped_features():
    binned = bin_features(_synthetic_ml_frame())
    slice_filter, mapped = ml_interaction_to_state_slice(
        binned, ["feat_ext_vs_ma_20", "feat_does_not_exist"]
    )

    assert mapped == ["feat_ext_vs_ma_20"]
    assert "state_ext" in slice_filter
    # Unknown feature is silently dropped, not raised on.
    assert all(not k.startswith("does_not_exist") for k in slice_filter)


def test_interactions_to_state_slices_emits_validatable_schema():
    df = _synthetic_ml_frame()
    scored = pd.DataFrame(
        [
            {
                "slice_key": "feat_ext_vs_ma_20 + feat_ret_3",
                "interaction_size": 2,
                "n_samples": 60,
                "mean_return": 0.003,
                "hit_rate": 0.7,
                "sharpe_proxy": 0.4,
            }
        ]
    )

    candidates = interactions_to_state_slices(df, scored, "SPY", "1d")

    assert len(candidates) == 1
    row = candidates.iloc[0]

    # The three columns validate_slices.run_validation reads must be present.
    for col in ["symbol", "timeframe", "slice_combination"]:
        assert col in candidates.columns
    assert row["symbol"] == "SPY"
    assert row["timeframe"] == "1d"
    assert row["source"] == "ml_interaction"

    # The slice must be parseable by the validation pipeline.
    slice_filter = parse_slice_combination(row["slice_combination"])
    assert slice_filter["state_ext"] == "stretched_up"
    assert slice_filter["state_ret_3"] == "ret_up"


def test_ml_candidate_slice_is_applyable_to_a_binned_frame():
    """Round-trip: the emitted slice filter must actually select rows from a
    binned eligible frame (the exact thing validate_slices does)."""
    df = _synthetic_ml_frame()
    scored = pd.DataFrame(
        [
            {
                "slice_key": "feat_ext_vs_ma_20 + feat_ret_3",
                "interaction_size": 2,
                "n_samples": 60,
                "mean_return": 0.003,
                "hit_rate": 0.7,
                "sharpe_proxy": 0.4,
            }
        ]
    )

    candidates = interactions_to_state_slices(df, scored, "SPY", "1d")
    slice_filter = parse_slice_combination(candidates.iloc[0]["slice_combination"])

    binned = bin_features(df)
    selected = apply_slice_filter(binned, slice_filter)

    assert not selected.empty
    assert (selected["state_ext"].astype(str) == "stretched_up").all()
    assert (selected["state_ret_3"].astype(str) == "ret_up").all()


def test_interactions_to_state_slices_empty_inputs():
    empty = pd.DataFrame()
    assert interactions_to_state_slices(empty, empty, "SPY", "1d").empty


def test_evaluate_interactions_accepts_both_schemas():
    """evaluate_interactions must accept both the {features} schema (from
    extract_feature_interactions) and the {slice_key} schema (from a
    run_ml_discovery record), so the documented V5 workflow works."""
    df = _synthetic_ml_frame(n=200)

    via_features = [{"features": ["feat_ext_vs_ma_20", "feat_ret_3"], "size": 2}]
    via_slice_key = [{"slice_key": "feat_ext_vs_ma_20 + feat_ret_3", "interaction_size": 2}]

    scored_a = evaluate_interactions(df, via_features, min_samples=5)
    scored_b = evaluate_interactions(df, via_slice_key, min_samples=5)

    assert not scored_a.empty
    assert not scored_b.empty
    # Both schemas describe the same interaction, so the scores must match.
    assert scored_a.iloc[0]["n_samples"] == scored_b.iloc[0]["n_samples"]
    assert scored_a.iloc[0]["mean_return"] == scored_b.iloc[0]["mean_return"]

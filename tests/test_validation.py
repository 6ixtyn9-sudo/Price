import math

import numpy as np
import pandas as pd
import pytest
from datetime import datetime, timedelta, timezone

from price.validation import (
    apply_slice_filter,
    apply_transaction_cost,
    chronological_train_valid_split,
    evaluate_slice_train_valid,
    newey_west_tstat,
    parse_slice_combination,
    summarize_returns,
    walk_forward_folds,
    walk_forward_validate_slice,
)


def _synthetic_frame(n=100, seed=7):
    rng = np.random.default_rng(seed)
    base_time = datetime(2025, 1, 1, tzinfo=timezone.utc)
    timestamps = [base_time + timedelta(hours=i) for i in range(n)]
    close = 100.0 + np.cumsum(rng.normal(0, 0.05, n))
    fwd_ret_5 = rng.normal(0.001, 0.005, n)
    state_ext = np.where(np.arange(n) % 2 == 0, "stretched_down", "neutral")
    return pd.DataFrame(
        {
            "bar_ts_utc": timestamps,
            "close_adj": close,
            "fwd_ret_5": fwd_ret_5,
            "state_ext": state_ext,
        }
    )


def test_chronological_split_fraction_preserves_order_and_size():
    df = _synthetic_frame(n=100)
    shuffled = df.sample(frac=1.0, random_state=1).reset_index(drop=True)

    train, valid = chronological_train_valid_split(shuffled, split=0.7)

    assert len(train) == 70
    assert len(valid) == 30
    assert train["bar_ts_utc"].max() <= valid["bar_ts_utc"].min()
    assert train["bar_ts_utc"].is_monotonic_increasing
    assert valid["bar_ts_utc"].is_monotonic_increasing


def test_chronological_split_invalid_fraction_raises():
    df = _synthetic_frame(n=10)
    with pytest.raises(ValueError):
        chronological_train_valid_split(df, split=1.5)


def test_chronological_split_by_explicit_date():
    df = _synthetic_frame(n=48)
    cutoff = df.loc[24, "bar_ts_utc"]

    train, valid = chronological_train_valid_split(df, split=cutoff)

    assert (train["bar_ts_utc"] < cutoff).all()
    assert (valid["bar_ts_utc"] >= cutoff).all()
    assert len(train) + len(valid) == len(df)


def test_apply_transaction_cost_bps_round_trip():
    returns = pd.Series([0.02, -0.01, 0.05])
    adjusted = apply_transaction_cost(returns, cost_bps=10, round_trip=True)

    # 10 bps round trip -> 2 * 0.0010 = 0.0020 drag on every row
    expected = returns - 0.0020
    pd.testing.assert_series_equal(adjusted, expected, check_names=False)


def test_apply_transaction_cost_per_share_uses_price():
    returns = pd.Series([0.01, 0.01])
    price = pd.Series([100.0, 50.0])

    adjusted = apply_transaction_cost(
        returns, cost_per_share=0.01, price=price, round_trip=True
    )

    expected = pd.Series([0.01 - (0.02 / 100.0), 0.01 - (0.02 / 50.0)])
    pd.testing.assert_series_equal(adjusted, expected, check_names=False)


def test_apply_transaction_cost_requires_price_for_per_share():
    returns = pd.Series([0.01, 0.02])
    with pytest.raises(ValueError):
        apply_transaction_cost(returns, cost_per_share=0.01, price=None)


def test_newey_west_tstat_matches_ols_when_no_autocorrelation():
    # With lags=0, HAC t-stat must reduce to the plain iid t-stat.
    rng = np.random.default_rng(0)
    returns = rng.normal(0.002, 0.01, 500)

    t_nw, p_nw = newey_west_tstat(returns, lags=0)

    mean = returns.mean()
    se_iid = returns.std(ddof=0) / math.sqrt(len(returns))
    t_iid = mean / se_iid

    assert t_nw == pytest.approx(t_iid, rel=1e-9)
    assert 0.0 <= p_nw <= 1.0


def test_newey_west_tstat_widens_interval_under_positive_autocorrelation():
    # Constructing a strongly positively-autocorrelated series (as in
    # overlapping-window forward returns) should shrink the HAC t-stat
    # relative to the naive iid t-stat, since true information content is
    # lower than the raw sample size implies.
    n = 300
    innovations = np.zeros(n)
    rng = np.random.default_rng(3)
    shocks = rng.normal(0, 1.0, n)
    for i in range(1, n):
        innovations[i] = 0.9 * innovations[i - 1] + shocks[i]
    returns = 0.001 + 0.002 * innovations

    t_nw, _ = newey_west_tstat(returns, lags=5)

    mean = returns.mean()
    se_iid = returns.std(ddof=0) / math.sqrt(n)
    t_iid = mean / se_iid

    assert abs(t_nw) < abs(t_iid)


def test_newey_west_tstat_handles_degenerate_input():
    t_stat, p_value = newey_west_tstat(np.array([0.01]))
    assert math.isnan(t_stat)
    assert math.isnan(p_value)

    t_stat_zero_var, p_value_zero_var = newey_west_tstat(np.array([0.01, 0.01, 0.01]))
    assert math.isnan(t_stat_zero_var)
    assert math.isnan(p_value_zero_var)


def test_walk_forward_folds_are_chronological_and_expanding():
    df = _synthetic_frame(n=100)
    folds = list(walk_forward_folds(df, n_folds=4))

    assert len(folds) == 4
    prev_train_len = 0
    for fold_idx, train_df, valid_df in folds:
        assert len(train_df) > prev_train_len
        prev_train_len = len(train_df)
        # No look-ahead: every validation timestamp must be strictly after
        # every training timestamp in that fold.
        assert train_df["bar_ts_utc"].max() < valid_df["bar_ts_utc"].min()


def test_walk_forward_folds_raises_when_too_few_rows():
    df = _synthetic_frame(n=3)
    with pytest.raises(ValueError):
        list(walk_forward_folds(df, n_folds=4))


def test_parse_slice_combination():
    parsed = parse_slice_combination(
        "state_session=afternoon + state_ext=stretched_down + state_slope=downtrend"
    )
    assert parsed == {
        "state_session": "afternoon",
        "state_ext": "stretched_down",
        "state_slope": "downtrend",
    }


def test_parse_slice_combination_malformed_raises():
    with pytest.raises(ValueError):
        parse_slice_combination("state_ext-stretched_down")


def test_apply_slice_filter():
    df = _synthetic_frame(n=10)
    filtered = apply_slice_filter(df, {"state_ext": "stretched_down"})
    assert (filtered["state_ext"] == "stretched_down").all()
    assert len(filtered) == 5


def test_summarize_returns_reports_min_sample_floor():
    returns = pd.Series([0.01, 0.02, -0.01, 0.03, 0.015])
    summary = summarize_returns(returns, min_samples=10)
    assert summary["sample_count"] == 5
    assert summary["meets_min_samples"] is False

    summary_ok = summarize_returns(returns, min_samples=5)
    assert summary_ok["meets_min_samples"] is True
    assert summary_ok["win_rate"] == pytest.approx(0.8)


def test_evaluate_slice_train_valid_end_to_end_deterministic():
    n = 140
    base_time = datetime(2025, 1, 1, tzinfo=timezone.utc)
    timestamps = [base_time + timedelta(hours=i) for i in range(n)]
    close = 100.0 + np.arange(n) * 0.01
    fwd_ret_5 = np.full(n, 0.01)  # deterministic positive edge, no noise
    state_ext = np.full(n, "stretched_down")

    df = pd.DataFrame(
        {
            "bar_ts_utc": timestamps,
            "close_adj": close,
            "fwd_ret_5": fwd_ret_5,
            "state_ext": state_ext,
        }
    )

    result = evaluate_slice_train_valid(
        df,
        {"state_ext": "stretched_down"},
        split=0.7,
        cost_bps=0.0,
        min_samples=10,
    )

    assert result["train"]["sample_count"] == 98
    assert result["valid"]["sample_count"] == 42
    assert result["train"]["mean_return"] == pytest.approx(0.01)
    assert result["valid"]["mean_return"] == pytest.approx(0.01)
    # A constant positive return series has zero variance -> HAC t-stat
    # is undefined (nan) by construction; this documents that edge case.
    assert math.isnan(result["train"]["t_stat"])


def test_evaluate_slice_train_valid_cost_can_flip_edge_to_negative():
    n = 60
    base_time = datetime(2025, 1, 1, tzinfo=timezone.utc)
    timestamps = [base_time + timedelta(hours=i) for i in range(n)]
    close = np.full(n, 100.0)
    fwd_ret_5 = np.full(n, 0.0005)  # tiny 5bp edge
    state_ext = np.full(n, "stretched_down")

    df = pd.DataFrame(
        {
            "bar_ts_utc": timestamps,
            "close_adj": close,
            "fwd_ret_5": fwd_ret_5,
            "state_ext": state_ext,
        }
    )

    result = evaluate_slice_train_valid(
        df,
        {"state_ext": "stretched_down"},
        split=0.7,
        cost_bps=10,  # 10 bps round trip = 20 bps drag, bigger than the edge
        min_samples=5,
    )

    assert result["train"]["mean_return"] < 0
    assert result["valid"]["mean_return"] < 0


def test_walk_forward_validate_slice_returns_one_entry_per_fold():
    df = _synthetic_frame(n=100)
    results = walk_forward_validate_slice(
        df, {"state_ext": "stretched_down"}, n_folds=3, min_samples=3
    )
    assert len(results) == 3
    for i, entry in enumerate(results):
        assert entry["fold"] == i
        assert "train" in entry and "valid" in entry
        assert entry["train"]["sample_count"] >= 0
        assert entry["valid"]["sample_count"] >= 0

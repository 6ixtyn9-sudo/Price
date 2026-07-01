"""Validation discipline for discovered market-state slices (Phase V4).

This module is intentionally decoupled from discovery.py / features.py /
warehouse.py internals. It operates on any DataFrame that has:
  - a chronological timestamp column (default 'bar_ts_utc')
  - a forward-return target column (default 'fwd_ret_5')
  - zero or more 'state_*' / slice feature columns to filter on

That keeps this module easy to unit-test with small synthetic frames and
reusable for any slice, any symbol, any timeframe.
"""

import math
from typing import Dict, Iterator, Optional, Tuple, Union

import numpy as np
import pandas as pd

DEFAULT_MIN_SAMPLES = 15


def chronological_train_valid_split(
    df: pd.DataFrame,
    ts_col: str = "bar_ts_utc",
    split: Union[float, str, pd.Timestamp] = 0.7,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Split a DataFrame into chronological train/validation windows.

    `split` can be:
      - a float in (0, 1): fraction of rows (by time order) used for training
      - a string or pd.Timestamp: an explicit cutoff date/time. Rows strictly
        before the cutoff go to train, rows at/after go to validation.
    """
    if df.empty:
        return df.copy(), df.copy()

    sorted_df = df.sort_values(ts_col).reset_index(drop=True)

    if isinstance(split, (int, float)):
        if not 0.0 < float(split) < 1.0:
            raise ValueError("split fraction must be strictly between 0 and 1")
        n = len(sorted_df)
        n_train = max(1, min(n - 1, round(n * float(split))))
        train_df = sorted_df.iloc[:n_train].reset_index(drop=True)
        valid_df = sorted_df.iloc[n_train:].reset_index(drop=True)
        return train_df, valid_df

    cutoff = pd.Timestamp(split)
    ts_series = sorted_df[ts_col]
    if getattr(ts_series.dtype, "tz", None) is not None and cutoff.tzinfo is None:
        cutoff = cutoff.tz_localize(ts_series.dtype.tz)
    train_df = sorted_df[sorted_df[ts_col] < cutoff].reset_index(drop=True)
    valid_df = sorted_df[sorted_df[ts_col] >= cutoff].reset_index(drop=True)
    return train_df, valid_df


def apply_transaction_cost(
    returns: pd.Series,
    cost_bps: float = 0.0,
    cost_per_share: float = 0.0,
    price: Optional[Union[pd.Series, float]] = None,
    round_trip: bool = True,
) -> pd.Series:
    """Subtract commission + spread drag from a return series.

    - cost_bps: basis points of drag per leg (1 bp = 0.0001).
    - cost_per_share: flat dollar cost per share per leg, converted to a
      percentage drag using `price` (required if cost_per_share != 0).
    - round_trip: if True (default), both entry and exit legs pay the cost.
    """
    returns = pd.Series(returns).astype(float)
    leg_multiplier = 2.0 if round_trip else 1.0

    drag = pd.Series(0.0, index=returns.index)

    if cost_bps:
        drag = drag + (cost_bps / 10000.0) * leg_multiplier

    if cost_per_share:
        if price is None:
            raise ValueError("price is required when cost_per_share is non-zero")
        if np.isscalar(price):
            price_series = pd.Series(float(price), index=returns.index)
        else:
            price_series = pd.Series(price).astype(float)
            price_series = price_series.reindex(returns.index)
        with np.errstate(divide="ignore", invalid="ignore"):
            share_drag = (cost_per_share / price_series) * leg_multiplier
        drag = drag + share_drag.fillna(0.0)

    return returns - drag


def newey_west_tstat(
    returns: np.ndarray,
    lags: Optional[int] = None,
) -> Tuple[float, float]:
    """Newey-West (HAC, Bartlett kernel) t-stat and two-sided p-value for the
    sample mean of a (possibly serially correlated / overlapping) return
    series.

    Uses the standard HAC variance-of-the-mean estimator:
        Var(mean) = (1/n) * [gamma_0 + 2 * sum_{l=1}^{L} w_l * gamma_l]
        w_l = 1 - l / (L + 1)   (Bartlett kernel)

    If `lags` is None, the Newey-West (1994) automatic bandwidth rule
    L = floor(4 * (n / 100) ** (2/9)) is used.

    The p-value uses the asymptotic normal approximation (no scipy
    dependency), which is standard practice for HAC t-stats.

    Returns (nan, nan) if there are fewer than 2 observations.
    """
    r = np.asarray(returns, dtype=float)
    r = r[~np.isnan(r)]
    n = len(r)
    if n < 2:
        return float("nan"), float("nan")

    mean = r.mean()
    demeaned = r - mean
    gamma0 = float(np.dot(demeaned, demeaned) / n)

    if lags is None:
        lags = int(math.floor(4.0 * (n / 100.0) ** (2.0 / 9.0)))
    lags = max(0, min(lags, n - 1))

    variance = gamma0
    for lag in range(1, lags + 1):
        weight = 1.0 - lag / (lags + 1.0)
        gamma_l = float(np.dot(demeaned[lag:], demeaned[:-lag]) / n)
        variance += 2.0 * weight * gamma_l

    # Guard against floating-point noise around a true zero-variance series
    # (e.g. a constant return column) producing a spuriously huge t-stat.
    scale = max(abs(mean), 1.0)
    if variance <= 0 or math.isclose(variance, 0.0, abs_tol=1e-12 * scale**2):
        return float("nan"), float("nan")

    se = math.sqrt(variance / n)
    if se == 0:
        return float("nan"), float("nan")

    t_stat = mean / se
    p_value = 2.0 * (1.0 - _standard_normal_cdf(abs(t_stat)))
    return t_stat, p_value


def _standard_normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def walk_forward_folds(
    df: pd.DataFrame,
    ts_col: str = "bar_ts_utc",
    n_folds: int = 4,
) -> Iterator[Tuple[int, pd.DataFrame, pd.DataFrame]]:
    """Chronological, non-overlapping, expanding-window walk-forward folds.

    The data is sorted by time and cut into (n_folds + 1) contiguous blocks.
    Fold i trains on blocks [0..i] (expanding window) and validates strictly
    out-of-sample on block i+1.

    Yields (fold_index, train_df, valid_df).
    """
    if n_folds < 1:
        raise ValueError("n_folds must be >= 1")

    sorted_df = df.sort_values(ts_col).reset_index(drop=True)
    n = len(sorted_df)
    n_blocks = n_folds + 1
    if n < n_blocks:
        raise ValueError(
            f"Not enough rows ({n}) to build {n_folds} walk-forward folds "
            f"(need at least {n_blocks})."
        )

    edges = np.linspace(0, n, n_blocks + 1).astype(int)
    blocks = [sorted_df.iloc[edges[i] : edges[i + 1]] for i in range(n_blocks)]

    for i in range(n_folds):
        train_df = pd.concat(blocks[: i + 1]).reset_index(drop=True)
        valid_df = blocks[i + 1].reset_index(drop=True)
        yield i, train_df, valid_df


def parse_slice_combination(slice_combination: str) -> Dict[str, str]:
    """Parse a discovered_slices.csv 'slice_combination' string, e.g.
    'state_ext=stretched_down + state_slope=downtrend', into a filter dict.
    """
    filters: Dict[str, str] = {}
    for part in slice_combination.split("+"):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"Malformed slice_combination segment: '{part}'")
        field, value = part.split("=", 1)
        filters[field.strip()] = value.strip()
    return filters


def apply_slice_filter(df: pd.DataFrame, slice_filter: Dict[str, str]) -> pd.DataFrame:
    """Filter a DataFrame down to rows matching every field=value pair."""
    if df.empty or not slice_filter:
        return df
    mask = pd.Series(True, index=df.index)
    for field, value in slice_filter.items():
        if field not in df.columns:
            raise ValueError(f"Slice field '{field}' not present in DataFrame.")
        mask &= df[field].astype(str) == str(value)
    return df[mask]


def summarize_returns(
    returns: pd.Series,
    min_samples: int = DEFAULT_MIN_SAMPLES,
    nw_lags: Optional[int] = None,
) -> Dict[str, Union[int, float, bool]]:
    """Summary stats for a (cost-adjusted) return series, with a
    Newey-West-adjusted significance test."""
    r = pd.Series(returns).dropna()
    n = len(r)
    if n == 0:
        return {
            "sample_count": 0,
            "mean_return": float("nan"),
            "win_rate": float("nan"),
            "t_stat": float("nan"),
            "p_value": float("nan"),
            "meets_min_samples": False,
        }

    t_stat, p_value = newey_west_tstat(r.values, lags=nw_lags)
    return {
        "sample_count": n,
        "mean_return": float(r.mean()),
        "win_rate": float((r > 0).mean()),
        "t_stat": t_stat,
        "p_value": p_value,
        "meets_min_samples": n >= min_samples,
    }


def evaluate_slice_train_valid(
    df: pd.DataFrame,
    slice_filter: Dict[str, str],
    ts_col: str = "bar_ts_utc",
    target_col: str = "fwd_ret_5",
    split: Union[float, str, pd.Timestamp] = 0.7,
    cost_bps: float = 0.0,
    cost_per_share: float = 0.0,
    price_col: Optional[str] = "close_adj",
    min_samples: int = DEFAULT_MIN_SAMPLES,
    nw_lags: Optional[int] = None,
) -> Dict[str, Dict[str, Union[int, float, bool]]]:
    """Chronological train/validation check for a single slice definition.

    Confirms the slice's edge on the training window and re-measures it,
    cost-adjusted, on the untouched out-of-sample validation window.
    """
    train_df, valid_df = chronological_train_valid_split(df, ts_col=ts_col, split=split)

    train_slice = apply_slice_filter(train_df, slice_filter)
    valid_slice = apply_slice_filter(valid_df, slice_filter)

    train_price = train_slice[price_col] if price_col and price_col in train_slice else None
    valid_price = valid_slice[price_col] if price_col and price_col in valid_slice else None

    train_returns = apply_transaction_cost(
        train_slice[target_col], cost_bps=cost_bps, cost_per_share=cost_per_share, price=train_price
    )
    valid_returns = apply_transaction_cost(
        valid_slice[target_col], cost_bps=cost_bps, cost_per_share=cost_per_share, price=valid_price
    )

    return {
        "train": summarize_returns(train_returns, min_samples=min_samples, nw_lags=nw_lags),
        "valid": summarize_returns(valid_returns, min_samples=min_samples, nw_lags=nw_lags),
    }


def walk_forward_validate_slice(
    df: pd.DataFrame,
    slice_filter: Dict[str, str],
    ts_col: str = "bar_ts_utc",
    target_col: str = "fwd_ret_5",
    n_folds: int = 4,
    cost_bps: float = 0.0,
    cost_per_share: float = 0.0,
    price_col: Optional[str] = "close_adj",
    min_samples: int = DEFAULT_MIN_SAMPLES,
    nw_lags: Optional[int] = None,
) -> list:
    """Walk-forward validation of a single slice definition.

    Rolls forward chronologically: each fold trains on an expanding past
    window and validates strictly out-of-sample on the next forward block.
    Returns a list of per-fold {'fold', 'train': {...}, 'valid': {...}}.
    """
    results = []
    for fold_idx, train_df, valid_df in walk_forward_folds(df, ts_col=ts_col, n_folds=n_folds):
        train_slice = apply_slice_filter(train_df, slice_filter)
        valid_slice = apply_slice_filter(valid_df, slice_filter)

        train_price = train_slice[price_col] if price_col and price_col in train_slice else None
        valid_price = valid_slice[price_col] if price_col and price_col in valid_slice else None

        train_returns = apply_transaction_cost(
            train_slice[target_col], cost_bps=cost_bps, cost_per_share=cost_per_share, price=train_price
        )
        valid_returns = apply_transaction_cost(
            valid_slice[target_col], cost_bps=cost_bps, cost_per_share=cost_per_share, price=valid_price
        )

        results.append(
            {
                "fold": fold_idx,
                "train": summarize_returns(train_returns, min_samples=min_samples, nw_lags=nw_lags),
                "valid": summarize_returns(valid_returns, min_samples=min_samples, nw_lags=nw_lags),
            }
        )
    return results

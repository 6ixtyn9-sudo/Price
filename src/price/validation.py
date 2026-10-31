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
import re
from statistics import NormalDist
from typing import Dict, Iterator, Optional, Sequence, Tuple, Union

import numpy as np
import pandas as pd

DEFAULT_MIN_SAMPLES = 15

# Euler–Mascheroni constant used in Bailey & López de Prado (2014) E[max SR].
_EULER_GAMMA = 0.5772156649015329
_STD_NORMAL = NormalDist(0.0, 1.0)
_HORIZON_RE = re.compile(r"(?:^|_)(\d+)b?$")


def infer_target_horizon_bars(target_col: str) -> int:
    """Infer the forward holding horizon (in bars) from a target column name.

    Examples:
      'fwd_ret_5'           -> 5
      'fwd_ret_5b'          -> 5
      'tradeable_fwd_ret_20'-> 20
      'custom_return'       -> 0
    """
    if not target_col:
        return 0
    match = _HORIZON_RE.search(str(target_col).strip())
    return int(match.group(1)) if match else 0


def _purge_tail(train_df: pd.DataFrame, purge_bars: int) -> pd.DataFrame:
    """Drop the trailing `purge_bars` rows from an in-sample split so multi-bar
    forward targets cannot leak out-of-sample price action across the boundary."""
    if purge_bars < 0:
        raise ValueError("purge_bars must be >= 0")
    if purge_bars == 0 or train_df.empty:
        return train_df
    keep = max(0, len(train_df) - int(purge_bars))
    return train_df.iloc[:keep].reset_index(drop=True)


def chronological_train_valid_split(
    df: pd.DataFrame,
    ts_col: str = "bar_ts_utc",
    split: Union[float, str, pd.Timestamp] = 0.7,
    purge_bars: int = 0,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Split a DataFrame into chronological train/validation windows.

    `split` can be:
      - a float in (0, 1): fraction of rows (by time order) used for training
      - a string or pd.Timestamp: an explicit cutoff date/time. Rows strictly
        before the cutoff go to train, rows at/after go to validation.

    `purge_bars` (>= 0) removes the trailing N rows from `train_df` immediately
    before `valid_df` begins to prevent H-bar forward-return label leakage.
    """
    if purge_bars < 0:
        raise ValueError("purge_bars must be >= 0")

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
        return _purge_tail(train_df, purge_bars), valid_df

    cutoff = pd.Timestamp(split)
    ts_series = sorted_df[ts_col]
    if getattr(ts_series.dtype, "tz", None) is not None and cutoff.tzinfo is None:
        cutoff = cutoff.tz_localize(ts_series.dtype.tz)
    train_df = sorted_df[sorted_df[ts_col] < cutoff].reset_index(drop=True)
    valid_df = sorted_df[sorted_df[ts_col] >= cutoff].reset_index(drop=True)
    return _purge_tail(train_df, purge_bars), valid_df


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


def direction_adjusted_returns(
    returns: pd.Series,
    side: str = "long",
    cost_bps: float = 0.0,
    cost_per_share: float = 0.0,
    price: Optional[Union[pd.Series, float]] = None,
    short_cost_bps: float = 0.0,
    round_trip: bool = True,
) -> pd.Series:
    """Direction-adjusted, cost-adjusted tradeable P&L for a slice.

      Long : P&L = fwd_ret - spread_cost
      Short: P&L = -fwd_ret - spread_cost - borrow_drag

    A short profits when fwd_ret < 0 (price falls), so its P&L is the negated
    forward return, minus the same round-trip spread cost a long pays, minus an
    extra short_cost_bps drag modelling stock borrow + dividend pass-through
    over the hold. With this, the existing gate `mean_return > 0` correctly
    tests tradeable expected P&L for BOTH directions: a promotable short is one
    whose negated, borrow-adjusted mean is still positive.

    `side` defaults to "long" so every caller that does not pass it -- and all
    existing discovered_slices.csv rows that carry no `side` column -- behave
    exactly as before. This is what makes the change backward compatible.
    """
    returns = pd.Series(returns).astype(float)
    eff_cost_bps = cost_bps
    if side == "short":
        returns = -returns
        eff_cost_bps = cost_bps + short_cost_bps
    return apply_transaction_cost(
        returns,
        cost_bps=eff_cost_bps,
        cost_per_share=cost_per_share,
        price=price,
        round_trip=round_trip,
    )


def _hac_moments(
    returns: np.ndarray,
    lags: Optional[int] = None,
    min_lags: int = 0,
) -> Tuple[np.ndarray, float, float, float]:
    """Clean returns and compute (clean_r, mean, gamma0, hac_long_run_var)."""
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    n = len(r)
    if n < 2:
        return r, float("nan"), float("nan"), float("nan")

    mean = float(r.mean())
    demeaned = r - mean
    gamma0 = float(np.dot(demeaned, demeaned) / n)

    if lags is None:
        auto_lags = int(math.floor(4.0 * (n / 100.0) ** (2.0 / 9.0)))
        lags = max(auto_lags, int(min_lags))
    lags = max(0, min(lags, n - 1))

    long_run_var = gamma0
    for lag in range(1, lags + 1):
        weight = 1.0 - lag / (lags + 1.0)
        gamma_l = float(np.dot(demeaned[lag:], demeaned[:-lag]) / n)
        long_run_var += 2.0 * weight * gamma_l

    return r, mean, gamma0, long_run_var


def newey_west_tstat(
    returns: np.ndarray,
    lags: Optional[int] = None,
    min_lags: int = 0,
) -> Tuple[float, float]:
    """Newey-West (HAC, Bartlett kernel) t-stat and two-sided p-value for the
    sample mean of a (possibly serially correlated / overlapping) return
    series.

    Uses the standard HAC variance-of-the-mean estimator:
        Var(mean) = (1/n) * [gamma_0 + 2 * sum_{l=1}^{L} w_l * gamma_l]
        w_l = 1 - l / (L + 1)   (Bartlett kernel)

    If `lags` is None, the Newey-West (1994) automatic bandwidth rule
    L = floor(4 * (n / 100) ** (2/9)) is used (floored at `min_lags`).

    The p-value uses the asymptotic normal approximation (no scipy
    dependency), which is standard practice for HAC t-stats.

    Returns (nan, nan) if there are fewer than 2 observations.
    """
    r, mean, _gamma0, variance = _hac_moments(returns, lags=lags, min_lags=min_lags)
    n = len(r)
    if n < 2:
        return float("nan"), float("nan")

    # Guard against floating-point noise around a true zero-variance series
    # (e.g. a constant return column) producing a spuriously huge t-stat.
    scale = max(abs(mean), 1.0)
    if not math.isfinite(variance) or variance <= 0 or math.isclose(variance, 0.0, abs_tol=1e-12 * scale**2):
        return float("nan"), float("nan")

    se = math.sqrt(variance / n)
    if se == 0:
        return float("nan"), float("nan")

    t_stat = mean / se
    p_value = 2.0 * (1.0 - _standard_normal_cdf(abs(t_stat)))
    return t_stat, p_value


def _standard_normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def expected_max_sharpe(sr_std: float, n_trials: float) -> float:
    """Expected maximum Sharpe under the zero-alpha null across `n_trials`.

    Bailey & López de Prado (2014) Euler–Mascheroni approximation:
        E[max SR] ≈ sqrt(V[SR]) * ((1 - gamma) * Phi^-1(1 - 1/N)
                                   + gamma * Phi^-1(1 - 1/(N * e)))
    """
    if not math.isfinite(n_trials) or n_trials <= 1.0:
        return 0.0
    if not math.isfinite(sr_std) or sr_std <= 0.0:
        return 0.0
    z1 = _STD_NORMAL.inv_cdf(1.0 - 1.0 / n_trials)
    z2 = _STD_NORMAL.inv_cdf(1.0 - 1.0 / (n_trials * math.e))
    return float(sr_std * ((1.0 - _EULER_GAMMA) * z1 + _EULER_GAMMA * z2))


def probabilistic_sharpe(
    observed_sr: float,
    *,
    benchmark_sr: float = 0.0,
    n_obs: Union[int, float],
    skewness: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """Probabilistic Sharpe Ratio P[true SR > benchmark_sr] in [0, 1].

    Bailey & López de Prado (2012/2014):
        PSR = Phi((SR - SR0) * sqrt(T - 1) / sqrt(1 - g3 * SR + ((g4 - 1)/4) * SR^2))

    `kurtosis` is RAW kurtosis (3.0 = Gaussian). Negative skewness and fat
    tails widen the estimator denominator and penalize fragile edges. `n_obs`
    may be an integer count or a continuous HAC overlap-adjusted effective N.
    """
    if n_obs is None or not math.isfinite(float(n_obs)) or float(n_obs) <= 1.0:
        return 0.0
    if not math.isfinite(observed_sr) or not math.isfinite(benchmark_sr):
        return 0.0

    g3 = float(skewness) if math.isfinite(skewness) else 0.0
    g4 = float(kurtosis) if math.isfinite(kurtosis) else 3.0

    denom_sq = 1.0 - g3 * observed_sr + ((g4 - 1.0) / 4.0) * (observed_sr**2)
    if denom_sq <= 0.0:
        # Degenerate sample moments: fall back to Gaussian SR variance rather
        # than fabricating certainty.
        denom_sq = 1.0 + 0.5 * (observed_sr**2)

    z = (observed_sr - benchmark_sr) * math.sqrt(float(n_obs) - 1.0) / math.sqrt(denom_sq)
    return _standard_normal_cdf(z)


def deflated_sharpe(
    trial_sharpes: Sequence[float],
    n_trials: Optional[int] = None,
    *,
    effective_n_trials: Optional[float] = None,
    observed_sr: Optional[float] = None,
    n_obs: Union[int, float],
    skewness: float = 0.0,
    kurtosis: float = 3.0,
    benchmark_sr: float = 0.0,
) -> float:
    """Deflated Sharpe Ratio (Bailey & López de Prado, 2014) in [0, 1].

    Corrects `observed_sr` simultaneously for:
      1. Multiple-testing selection bias across `n_trials` (via `expected_max_sharpe`
         scaled by cross-trial Sharpe dispersion), and
      2. Non-normal higher moments (`skewness`, raw `kurtosis`) over `n_obs`.

    With N <= 1 or zero variance across trials, reduces cleanly to `probabilistic_sharpe`
    against `benchmark_sr`.
    """
    arr = np.asarray(list(trial_sharpes), dtype=float)
    arr = arr[np.isfinite(arr)]

    if observed_sr is None:
        if arr.size == 0:
            return 0.0
        observed_sr = float(arr.max())

    raw_n = int(n_trials) if n_trials is not None else int(arr.size)
    if raw_n < 0:
        raise ValueError("n_trials must be >= 0")

    if effective_n_trials is None:
        effective_n = float(raw_n)
    else:
        effective_n = float(effective_n_trials)
        if not math.isfinite(effective_n) or effective_n < 1.0:
            raise ValueError("effective_n_trials must be finite and >= 1")
        if raw_n >= 1 and effective_n > raw_n:
            raise ValueError("effective_n_trials must be <= n_trials")

    sr_std = float(arr.std(ddof=0)) if arr.size > 1 else 0.0
    sr0 = benchmark_sr + (
        expected_max_sharpe(sr_std, effective_n)
        if effective_n > 1.0 and sr_std > 0.0
        else 0.0
    )

    return probabilistic_sharpe(
        float(observed_sr),
        benchmark_sr=sr0,
        n_obs=n_obs,
        skewness=skewness,
        kurtosis=kurtosis,
    )


def overfit_ratio(is_sharpe: float, oos_sharpe: float) -> float:
    """IS Sharpe / OOS Sharpe diagnostic (>1.5 caution, >2.5 likely overfit).

    Returns nan if either Sharpe is non-finite, inf if IS > 0 and OOS <= 0,
    and 0.0 if both are <= 0.
    """
    if not math.isfinite(is_sharpe) or not math.isfinite(oos_sharpe):
        return float("nan")
    if oos_sharpe <= 0.0:
        return float("inf") if is_sharpe > 0.0 else 0.0
    return float(is_sharpe / oos_sharpe)


def sharpe_decay(oos_sharpes: Sequence[float]) -> float:
    """OLS slope of OOS Sharpe across chronological walk-forward folds.

    Preserves fold positions when intermediate folds are NaN (e.g. sample-empty
    fold). Negative slope indicates decaying out-of-sample edge. Returns nan if
    fewer than 2 folds carry finite Sharpe estimates.
    """
    pairs = [
        (float(idx), float(val))
        for idx, val in enumerate(oos_sharpes)
        if val is not None and math.isfinite(float(val))
    ]
    if len(pairs) < 2:
        return float("nan")

    x = np.array([p[0] for p in pairs], dtype=float)
    y = np.array([p[1] for p in pairs], dtype=float)
    x_mean = float(x.mean())
    y_mean = float(y.mean())
    denom = float(((x - x_mean) ** 2).sum())
    if denom == 0.0:
        return float("nan")
    return float(((x - x_mean) * (y - y_mean)).sum() / denom)


def walk_forward_folds(
    df: pd.DataFrame,
    ts_col: str = "bar_ts_utc",
    n_folds: int = 4,
    purge_bars: int = 0,
) -> Iterator[Tuple[int, pd.DataFrame, pd.DataFrame]]:
    """Chronological, non-overlapping, expanding-window walk-forward folds.

    The data is sorted by time and cut into (n_folds + 1) contiguous blocks.
    Fold i trains on blocks [0..i] (expanding window, optionally purged by
    dropping the trailing `purge_bars` rows before block i+1) and validates
    strictly out-of-sample on block i+1.

    Yields (fold_index, train_df, valid_df).
    """
    if n_folds < 1:
        raise ValueError("n_folds must be >= 1")
    if purge_bars < 0:
        raise ValueError("purge_bars must be >= 0")

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
        yield i, _purge_tail(train_df, purge_bars), valid_df


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
    min_nw_lags: int = 0,
) -> Dict[str, Union[int, float, bool]]:
    """Summary stats for a (cost-adjusted) return series, with a
    Newey-West-adjusted significance test and higher-moment PSR inputs."""
    arr = pd.Series(returns).to_numpy(dtype=float)
    r = arr[np.isfinite(arr)]
    n = len(r)
    if n == 0:
        return {
            "sample_count": 0,
            "effective_n": 0.0,
            "mean_return": float("nan"),
            "sharpe": float("nan"),
            "skewness": float("nan"),
            "kurtosis": float("nan"),
            "psr": float("nan"),
            "win_rate": float("nan"),
            "t_stat": float("nan"),
            "p_value": float("nan"),
            "meets_min_samples": False,
        }

    _, mean, gamma0, long_run_var = _hac_moments(r, lags=nw_lags, min_lags=min_nw_lags)
    t_stat, p_value = newey_west_tstat(r, lags=nw_lags, min_lags=min_nw_lags)

    scale = max(abs(mean) if math.isfinite(mean) else 0.0, 1.0)
    if n < 2 or not math.isfinite(gamma0) or gamma0 <= 0 or math.isclose(gamma0, 0.0, abs_tol=1e-12 * scale**2):
        sharpe = float("nan")
        skewness = float("nan")
        kurtosis = float("nan")
        effective_n = float(n)
        psr = float("nan")
    else:
        std = math.sqrt(gamma0)
        sharpe = float(mean / std)
        z = (r - mean) / std
        skewness = float(np.mean(z**3))
        kurtosis = float(np.mean(z**4))
        # Overlap-aware effective sample size: positive serial correlation
        # (long_run_var > gamma0) shrinks effective_n to n * (gamma0 / long_run_var)
        # so overlapping H-bar forward returns cannot inflate PSR/DSR.
        if math.isfinite(long_run_var) and long_run_var > 0:
            effective_n = float(n) * min(1.0, gamma0 / long_run_var)
        else:
            effective_n = float(n)
        psr = probabilistic_sharpe(
            sharpe,
            benchmark_sr=0.0,
            n_obs=effective_n,
            skewness=skewness,
            kurtosis=kurtosis,
        )

    return {
        "sample_count": n,
        "effective_n": effective_n,
        "mean_return": float(r.mean()),
        "sharpe": sharpe,
        "skewness": skewness,
        "kurtosis": kurtosis,
        "psr": psr,
        "win_rate": float((r > 0).mean()),
        "t_stat": t_stat,
        "p_value": p_value,
        "meets_min_samples": n >= min_samples,
    }


def _resolve_purge_bars(purge_bars: Optional[int], target_col: str) -> int:
    if purge_bars is None:
        return infer_target_horizon_bars(target_col)
    if purge_bars < 0:
        raise ValueError("purge_bars must be >= 0")
    return int(purge_bars)


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
    side: str = "long",
    short_cost_bps: float = 0.0,
    purge_bars: Optional[int] = 0,
) -> Dict[str, Dict[str, Union[int, float, bool]]]:
    """Chronological train/validation check for a single slice definition.

    Confirms the slice's edge on the training window and re-measures it,
    cost-adjusted, on the untouched out-of-sample validation window.

    `side` ("long" | "short") direction-adjusts the return series before the
    cost gate so the same `mean_return > 0` test works for both directions.
    `short_cost_bps` adds extra borrow/dividend drag to short slices only.
    `purge_bars`: pass None to auto-infer the holding horizon from `target_col`
    (e.g. 'fwd_ret_5' -> 5); defaults to 0 for low-level compatibility.
    """
    eff_purge = _resolve_purge_bars(purge_bars, target_col)
    train_df, valid_df = chronological_train_valid_split(
        df, ts_col=ts_col, split=split, purge_bars=eff_purge
    )

    train_slice = apply_slice_filter(train_df, slice_filter)
    valid_slice = apply_slice_filter(valid_df, slice_filter)

    train_price = train_slice[price_col] if price_col and price_col in train_slice else None
    valid_price = valid_slice[price_col] if price_col and price_col in valid_slice else None

    train_returns = direction_adjusted_returns(
        train_slice[target_col], side=side, cost_bps=cost_bps,
        cost_per_share=cost_per_share, price=train_price, short_cost_bps=short_cost_bps,
    )
    valid_returns = direction_adjusted_returns(
        valid_slice[target_col], side=side, cost_bps=cost_bps,
        cost_per_share=cost_per_share, price=valid_price, short_cost_bps=short_cost_bps,
    )

    overlap_lags = max(0, infer_target_horizon_bars(target_col) - 1)
    return {
        "train": summarize_returns(
            train_returns, min_samples=min_samples, nw_lags=nw_lags, min_nw_lags=overlap_lags
        ),
        "valid": summarize_returns(
            valid_returns, min_samples=min_samples, nw_lags=nw_lags, min_nw_lags=overlap_lags
        ),
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
    side: str = "long",
    short_cost_bps: float = 0.0,
    purge_bars: Optional[int] = 0,
) -> list:
    """Walk-forward validation of a single slice definition.

    Rolls forward chronologically: each fold trains on an expanding past
    window and validates strictly out-of-sample on the next forward block.
    Returns a list of per-fold {'fold', 'train': {...}, 'valid': {...}}.

    `side` direction-adjusts returns so the gate is sign-aware; see
    direction_adjusted_returns.
    `purge_bars`: pass None to auto-infer from `target_col`; defaults to 0.
    """
    eff_purge = _resolve_purge_bars(purge_bars, target_col)
    overlap_lags = max(0, infer_target_horizon_bars(target_col) - 1)
    results = []
    for fold_idx, train_df, valid_df in walk_forward_folds(
        df, ts_col=ts_col, n_folds=n_folds, purge_bars=eff_purge
    ):
        train_slice = apply_slice_filter(train_df, slice_filter)
        valid_slice = apply_slice_filter(valid_df, slice_filter)

        train_price = train_slice[price_col] if price_col and price_col in train_slice else None
        valid_price = valid_slice[price_col] if price_col and price_col in valid_slice else None

        train_returns = direction_adjusted_returns(
            train_slice[target_col], side=side, cost_bps=cost_bps,
            cost_per_share=cost_per_share, price=train_price, short_cost_bps=short_cost_bps,
        )
        valid_returns = direction_adjusted_returns(
            valid_slice[target_col], side=side, cost_bps=cost_bps,
            cost_per_share=cost_per_share, price=valid_price, short_cost_bps=short_cost_bps,
        )

        results.append(
            {
                "fold": fold_idx,
                "train": summarize_returns(
                    train_returns, min_samples=min_samples, nw_lags=nw_lags, min_nw_lags=overlap_lags
                ),
                "valid": summarize_returns(
                    valid_returns, min_samples=min_samples, nw_lags=nw_lags, min_nw_lags=overlap_lags
                ),
            }
        )
    return results

import pandas as pd
import numpy as np
from typing import Dict, List

from price.warehouse import load_from_warehouse
from price.features import compute_price_features

# Canonical mapping from a raw ML feature name to its binned state_* field.
# This is the vocabulary the ML discovery path (ml_discovery.py) uses to
# translate raw feature interactions (e.g. "feat_ext_vs_ma_20 + feat_ret_3")
# into the state_*=value slice format the validation pipeline consumes.
# Every value here MUST be a column produced by bin_features().
ML_FEATURE_TO_STATE: Dict[str, str] = {
    "feat_ext_vs_ma_20": "state_ext",
    "feat_trend_slope_20": "state_slope",
    "feat_realized_vol_20": "state_vol",
    "feat_session_bucket": "state_session",
    "feat_dow": "state_dow",
    "feat_ret_1": "state_ret_1",
    "feat_ret_3": "state_ret_3",
    "feat_ret_5": "state_ret_5",
    "feat_ret_10": "state_ret_10",
    "feat_ret_20": "state_ret_20",
    "feat_atr_norm_ext": "state_atr_ext",
    "feat_vol_regime": "state_vol_regime",
    "feat_trend_strength_20": "state_trend_strength",
    "feat_gap": "state_gap",
    "feat_range_position": "state_range_pos",
}

# Ordered state labels (low -> high) for the ML feature -> state mapping.
# evaluate_interactions defines the promising region as the high-quantile
# (>= q75) side of each feature, so each ML feature maps to its HIGHEST state
# bucket here. All fields are ordinal, so "highest" is well defined. This dict
# MUST stay in sync with the label lists in bin_features(); test_state_labels
# guards against drift.
STATE_LABELS: Dict[str, tuple] = {
    "state_ext": ("stretched_down", "neutral", "stretched_up"),
    "state_slope": ("downtrend", "flat", "uptrend"),
    "state_vol": ("low_vol", "mid_vol", "high_vol"),
    "state_session": ("morning", "lunch", "afternoon"),
    "state_dow": ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
    "state_ret_1": ("ret_down", "ret_flat", "ret_up"),
    "state_ret_3": ("ret_down", "ret_flat", "ret_up"),
    "state_ret_5": ("ret_down", "ret_flat", "ret_up"),
    "state_ret_10": ("ret_down", "ret_flat", "ret_up"),
    "state_ret_20": ("ret_down", "ret_flat", "ret_up"),
    "state_atr_ext": ("atr_down", "atr_neutral", "atr_up"),
    "state_vol_regime": ("vol_regime_low", "vol_regime_mid", "vol_regime_high"),
    "state_trend_strength": ("weak_trend", "mod_trend", "strong_trend"),
    "state_gap": ("gap_down", "gap_flat", "gap_up"),
    "state_range_pos": ("range_low", "range_mid", "range_high"),
}


def _qcut_state(series: pd.Series, labels: List[str], fallback: str):
    """Bin a numeric feature into len(labels) equal-frequency state buckets.

    Mirrors the existing state_slope / state_vol binning style: tries a clean
    quantile cut and falls back to a constant bucket when the data is too
    sparse / degenerate to produce non-duplicate edges (e.g. tiny fixtures).
    """
    try:
        return pd.qcut(series, q=len(labels), labels=labels)
    except Exception:
        return fallback


def _expanding_qcut(series: pd.Series, labels: List[str], min_periods: int, fallback: str):
    """Time-respecting equal-frequency bins (look-ahead-free).

    For bar T, the quantile boundaries are computed using ONLY bars strictly
    before T (bars [0..T-1]), via an expanding window over the shift(1)'d
    series. This removes the two failure modes of the full-history pd.qcut
    used by bin_features():
      1. look-ahead bias -- a bar at T is no longer binned using future bars.
      2. in-sample fit -- the boundary at T is not fit on T's own value
         (T is a test point against its forward return; the boundary must
         exclude it).

    Bars before min_periods get NaN (dropped from evaluation -- they are early
    history). Falls back to `fallback` when the series is too short or all-NaN
    to produce stable boundaries. This is the single highest-value overfit-kill
    flagged by the HANDOVER's V5 methodological note.
    """
    n = len(labels)
    s = pd.Series(series).astype(float)
    if s.dropna().shape[0] < max(min_periods, n):
        return fallback
    # Shift by 1 so bar T's boundary uses only [0..T-1] (strictly excludes T).
    prior = s.shift(1)
    qs = [prior.expanding(min_periods=min_periods).quantile((i + 1) / n) for i in range(n - 1)]
    valid = s.notna()
    for q in qs:
        valid = valid & q.notna()
    result = pd.Series(np.nan, index=s.index, dtype=object)
    for i in range(n):
        lower = qs[i - 1] if i > 0 else pd.Series(-np.inf, index=s.index)
        upper = qs[i] if i < n - 1 else pd.Series(np.inf, index=s.index)
        mask = valid & (s >= lower) & (s < upper)
        result[mask] = labels[i]
    return result


# Default min_periods for rolling-bin state fields. 200 matches the monitor's
# lookback and is large enough that the expanding quantiles are stable on both
# daily (~1250 bars) and intraday (thousands) histories while only dropping
# early history that contributes little to validation anyway.
DEFAULT_ROLLING_MIN_PERIODS = 200


def bin_features(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    df_binned = df.copy()

    def bin_ext(val):
        if pd.isna(val):
            return np.nan
        if val < -0.015:
            return "stretched_down"
        elif val > 0.015:
            return "stretched_up"
        else:
            return "neutral"

    if 'feat_ext_vs_ma_20' in df_binned.columns:
        df_binned['state_ext'] = df_binned['feat_ext_vs_ma_20'].apply(bin_ext)
    else:
        df_binned['state_ext'] = np.nan

    if 'feat_trend_slope_20' in df_binned.columns and not df_binned['feat_trend_slope_20'].dropna().empty:
        try:
            df_binned['state_slope'] = pd.qcut(
                df_binned['feat_trend_slope_20'],
                q=3,
                labels=["downtrend", "flat", "uptrend"]
            )
        except Exception:
            df_binned['state_slope'] = "flat"
    else:
        df_binned['state_slope'] = "flat"

    if 'feat_realized_vol_20' in df_binned.columns and not df_binned['feat_realized_vol_20'].dropna().empty:
        try:
            df_binned['state_vol'] = pd.qcut(
                df_binned['feat_realized_vol_20'],
                q=3,
                labels=["low_vol", "mid_vol", "high_vol"]
            )
        except Exception:
            df_binned['state_vol'] = "mid_vol"
    else:
        df_binned['state_vol'] = "mid_vol"

    session_map = {0: "morning", 1: "lunch", 2: "afternoon"}
    if 'feat_session_bucket' in df_binned.columns:
        df_binned['state_session'] = df_binned['feat_session_bucket'].map(session_map)
    else:
        df_binned['state_session'] = np.nan

    dow_map = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}
    if 'feat_dow' in df_binned.columns:
        df_binned['state_dow'] = df_binned['feat_dow'].map(dow_map)
    else:
        df_binned['state_dow'] = np.nan

    # Additional state bins used by the ML discovery path. These translate
    # the raw feat_* features LightGBM ranks highly (notably the return
    # features that dominate ML candidate interactions) into the same
    # state_*=value slice vocabulary the combinatorial discovery and
    # validation pipeline already speak. Purely additive: the combinatorial
    # discovery combinations never reference these columns, so existing
    # discovery/leaderboard behaviour is unchanged.
    for _period in [1, 3, 5, 10, 20]:
        _feat = f"feat_ret_{_period}"
        if _feat in df_binned.columns:
            df_binned[f"state_ret_{_period}"] = _qcut_state(
                df_binned[_feat], ["ret_down", "ret_flat", "ret_up"], "ret_flat"
            )
    if "feat_atr_norm_ext" in df_binned.columns:
        df_binned["state_atr_ext"] = _qcut_state(
            df_binned["feat_atr_norm_ext"], ["atr_down", "atr_neutral", "atr_up"], "atr_neutral"
        )
    if "feat_vol_regime" in df_binned.columns:
        df_binned["state_vol_regime"] = _qcut_state(
            df_binned["feat_vol_regime"], ["vol_regime_low", "vol_regime_mid", "vol_regime_high"],
            "vol_regime_mid",
        )
    if "feat_trend_strength_20" in df_binned.columns:
        df_binned["state_trend_strength"] = _qcut_state(
            df_binned["feat_trend_strength_20"], ["weak_trend", "mod_trend", "strong_trend"],
            "mod_trend",
        )
    if "feat_gap" in df_binned.columns:
        df_binned["state_gap"] = _qcut_state(
            df_binned["feat_gap"], ["gap_down", "gap_flat", "gap_up"], "gap_flat"
        )
    if "feat_range_position" in df_binned.columns:
        df_binned["state_range_pos"] = _qcut_state(
            df_binned["feat_range_position"], ["range_low", "range_mid", "range_high"], "range_mid"
        )

    return df_binned


def bin_features_rolling(
    df: pd.DataFrame,
    min_periods: int = DEFAULT_ROLLING_MIN_PERIODS,
) -> pd.DataFrame:
    """Look-ahead-free variant of bin_features.

    Produces the SAME state_* columns and label vocabularies as bin_features,
    but every quantile-based field uses _expanding_qcut (boundary at bar T
    computed from bars strictly before T only) instead of full-history pd.qcut.

    What changes vs bin_features:
      - state_slope, state_vol, state_ret_{1,3,5,10,20}, state_atr_ext,
        state_vol_regime, state_trend_strength, state_gap, state_range_pos:
        now binned with a time-respecting expanding quantile.
      - state_ext: UNCHANGED. It already uses fixed +-0.015 thresholds (a
        fixed prior), so it has no look-ahead to remove. This is deliberate:
        state_ext is the one state field that combinatorial survivors
        repeatedly clear BH/Bonferroni on, and that is BECAUSE it is a fixed
        prior, not an in-sample quantile. Keeping it fixed preserves that
        property.
      - state_session, state_dow: UNCHANGED (categorical maps, not quantile).
      - The first ~min_periods rows get NaN state (dropped by label_eligible
        selection downstream).

    Rationale: the HANDOVER's V5 methodological note names this as the
    highest-value next improvement. The ML path's in-sample 75th-percentile
    cut and bin_features' full-history qcut are the two overfit sources that
    keep ML slices (and quantile-based combinatorial slices) failing the
    search-wide gate while fixed-prior state_ext slices sometimes pass.
    """
    if df.empty:
        return df

    df_binned = df.copy()

    # state_ext: fixed prior (unchanged from bin_features -- no look-ahead).
    def bin_ext(val):
        if pd.isna(val):
            return np.nan
        if val < -0.015:
            return "stretched_down"
        elif val > 0.015:
            return "stretched_up"
        else:
            return "neutral"

    if "feat_ext_vs_ma_20" in df_binned.columns:
        df_binned["state_ext"] = df_binned["feat_ext_vs_ma_20"].apply(bin_ext)
    else:
        df_binned["state_ext"] = np.nan

    if "feat_trend_slope_20" in df_binned.columns and not df_binned["feat_trend_slope_20"].dropna().empty:
        df_binned["state_slope"] = _expanding_qcut(
            df_binned["feat_trend_slope_20"],
            ["downtrend", "flat", "uptrend"], min_periods, "flat",
        )
    else:
        df_binned["state_slope"] = "flat"

    if "feat_realized_vol_20" in df_binned.columns and not df_binned["feat_realized_vol_20"].dropna().empty:
        df_binned["state_vol"] = _expanding_qcut(
            df_binned["feat_realized_vol_20"],
            ["low_vol", "mid_vol", "high_vol"], min_periods, "mid_vol",
        )
    else:
        df_binned["state_vol"] = "mid_vol"

    session_map = {0: "morning", 1: "lunch", 2: "afternoon"}
    if "feat_session_bucket" in df_binned.columns:
        df_binned["state_session"] = df_binned["feat_session_bucket"].map(session_map)
    else:
        df_binned["state_session"] = np.nan

    dow_map = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}
    if "feat_dow" in df_binned.columns:
        df_binned["state_dow"] = df_binned["feat_dow"].map(dow_map)
    else:
        df_binned["state_dow"] = np.nan

    for _period in [1, 3, 5, 10, 20]:
        _feat = f"feat_ret_{_period}"
        if _feat in df_binned.columns:
            df_binned[f"state_ret_{_period}"] = _expanding_qcut(
                df_binned[_feat],
                ["ret_down", "ret_flat", "ret_up"], min_periods, "ret_flat",
            )
    if "feat_atr_norm_ext" in df_binned.columns:
        df_binned["state_atr_ext"] = _expanding_qcut(
            df_binned["feat_atr_norm_ext"],
            ["atr_down", "atr_neutral", "atr_up"], min_periods, "atr_neutral",
        )
    if "feat_vol_regime" in df_binned.columns:
        df_binned["state_vol_regime"] = _expanding_qcut(
            df_binned["feat_vol_regime"],
            ["vol_regime_low", "vol_regime_mid", "vol_regime_high"], min_periods, "vol_regime_mid",
        )
    if "feat_trend_strength_20" in df_binned.columns:
        df_binned["state_trend_strength"] = _expanding_qcut(
            df_binned["feat_trend_strength_20"],
            ["weak_trend", "mod_trend", "strong_trend"], min_periods, "mod_trend",
        )
    if "feat_gap" in df_binned.columns:
        df_binned["state_gap"] = _expanding_qcut(
            df_binned["feat_gap"],
            ["gap_down", "gap_flat", "gap_up"], min_periods, "gap_flat",
        )
    if "feat_range_position" in df_binned.columns:
        df_binned["state_range_pos"] = _expanding_qcut(
            df_binned["feat_range_position"],
            ["range_low", "range_mid", "range_high"], min_periods, "range_mid",
        )

    return df_binned


def apply_state_bins(
    df: pd.DataFrame,
    bin_mode: str = "insample",
    rolling_min_periods: int = DEFAULT_ROLLING_MIN_PERIODS,
) -> pd.DataFrame:
    """Dispatcher between full-history (insample) and look-ahead-free (rolling)
    state binning. `bin_mode="insample"` reproduces the original bin_features
    exactly (backward compatible). `bin_mode="rolling"` uses bin_features_rolling.
    """
    if bin_mode == "rolling":
        return bin_features_rolling(df, min_periods=rolling_min_periods)
    return bin_features(df)


def discover_market_slices(
    symbol: str,
    timeframe: str,
    slice_fields: List[str],
    min_samples: int = 15,
    cond_symbols: List[str] = None,
    bin_mode: str = "insample",
) -> pd.DataFrame:
    df_raw = load_from_warehouse(symbol, timeframe)
    if df_raw.empty:
        print(f"No warehouse data found for {symbol} ({timeframe}).")
        return pd.DataFrame()

    df_feat = compute_price_features(df_raw)
    df_binned = apply_state_bins(df_feat, bin_mode=bin_mode)

    # Optional cross-asset conditioning: attach each conditioning symbol's
    # most-recent-completed state (backward as-of, no look-ahead) as
    # cross_<SYM>_state_* columns so slice_fields can reference them.
    if cond_symbols:
        for cond_sym in cond_symbols:
            df_binned = attach_cross_asset_states(
                df_binned,
                cond_sym,
                timeframe,
                ["state_ext", "state_slope", "state_vol"],
                bin_mode=bin_mode,
            )

    eval_df = df_binned[df_binned['label_eligible']]
    if eval_df.empty:
        print(f"No eligible forward-looking evaluation rows for {symbol} ({timeframe}).")
        return pd.DataFrame()

    for f in slice_fields:
        if f not in eval_df.columns:
            raise ValueError(f"Slice feature field '{f}' is not available in the state DataFrame.")

    grouped = eval_df.groupby(slice_fields)

    slice_metrics = []
    for keys, group in grouped:
        n = len(group)
        if n < min_samples:
            continue

        slice_key = " + ".join([f"{f}={k}" for f, k in zip(slice_fields, keys if isinstance(keys, tuple) else [keys])])

        mean_ret = group['fwd_ret_5'].mean()
        std_ret = group['fwd_ret_5'].std()
        win_rate = (group['fwd_ret_5'] > 0).sum() / n

        mean_mfe = group['fwd_mfe_5'].mean()
        mean_mae = group['fwd_mae_5'].mean()

        ratio = mean_ret / std_ret if std_ret > 0 else 0.0

        # Direction tag: a slice whose mean forward return is negative is a
        # SHORT (it profits when price falls). The validation gate then
        # direction-adjusts so the same mean_return > 0 test works for both.
        # tradeable_mean_fwd_ret_5 is the sign-adjusted expected P&L, used to
        # rank long and short edges on a single scale (best edge of either
        # direction floats to the top).
        side = "short" if mean_ret < 0 else "long"

        slice_metrics.append({
            'symbol': symbol,
            'timeframe': timeframe,
            'slice_combination': slice_key,
            'sample_count': n,
            'mean_fwd_ret_5': mean_ret,
            'side': side,
            'tradeable_mean_fwd_ret_5': -mean_ret if mean_ret < 0 else mean_ret,
            'win_rate': win_rate,
            'mean_mfe_5': mean_mfe,
            'mean_mae_5': mean_mae,
            'fwd_ret_ratio': ratio
        })

    df_slices = pd.DataFrame(slice_metrics)
    if not df_slices.empty:
        # Rank by tradeable (direction-adjusted) expected P&L so the strongest
        # edge of EITHER direction surfaces first, not just the strongest long.
        df_slices = df_slices.sort_values(
            "tradeable_mean_fwd_ret_5", ascending=False
        ).reset_index(drop=True)

    return df_slices


def align_cross_asset_states(primary_df, cond_state_df, cond_symbol, fields):
    """Backward as-of merge of a conditioning symbol's binned state onto
    primary_df without look-ahead. For each primary bar at time t, the
    conditioning state is its most recent bar with bar_ts_utc <= t. New
    columns are named cross_<COND>_<field>. Row order is preserved.
    """
    if primary_df.empty:
        return primary_df.copy()
    if "bar_ts_utc" not in primary_df.columns:
        raise ValueError("primary_df must contain a bar_ts_utc column.")
    if cond_state_df.empty or "bar_ts_utc" not in cond_state_df.columns:
        raise ValueError("cond_state_df must contain a non-empty bar_ts_utc column.")

    prefix = f"cross_{cond_symbol.upper()}_"
    rename = {f: f"{prefix}{f}" for f in fields}

    cond = cond_state_df[["bar_ts_utc", *fields]].rename(columns=rename)
    cond = cond.sort_values("bar_ts_utc").reset_index(drop=True)

    primary = primary_df.copy()
    primary["_orig_order"] = range(len(primary))
    primary_sorted = primary.sort_values("bar_ts_utc").reset_index(drop=True)

    merged = pd.merge_asof(
        primary_sorted,
        cond,
        on="bar_ts_utc",
        direction="backward",
    )

    merged = merged.sort_values("_orig_order").reset_index(drop=True)
    merged = merged.drop(columns=["_orig_order"])
    return merged


def attach_cross_asset_states(primary_df, cond_symbol, timeframe, fields, bin_mode="insample"):
    """Load the conditioning symbol from the warehouse, rebuild its binned
    state frame the same way as the primary (compute_price_features +
    state binning over full history), and backward as-of merge the requested
    state fields onto primary_df. Returns primary_df unchanged if the
    conditioning symbol has no warehouse data.

    bin_mode is threaded to apply_state_bins so the conditioning symbol is
    binned consistently with the primary (a cross-asset slice must not mix
    in-sample primary state with rolling conditioning state, or vice versa).
    """
    cond_raw = load_from_warehouse(cond_symbol, timeframe)
    if cond_raw.empty:
        return primary_df.copy()

    cond_feat = compute_price_features(cond_raw)
    cond_binned = apply_state_bins(cond_feat, bin_mode=bin_mode)
    return align_cross_asset_states(primary_df, cond_binned, cond_symbol, fields)

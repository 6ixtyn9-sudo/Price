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

def discover_market_slices(
    symbol: str,
    timeframe: str,
    slice_fields: List[str],
    min_samples: int = 15,
    cond_symbols: List[str] = None,
) -> pd.DataFrame:
    df_raw = load_from_warehouse(symbol, timeframe)
    if df_raw.empty:
        print(f"No warehouse data found for {symbol} ({timeframe}).")
        return pd.DataFrame()

    df_feat = compute_price_features(df_raw)
    df_binned = bin_features(df_feat)

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

        slice_metrics.append({
            'symbol': symbol,
            'timeframe': timeframe,
            'slice_combination': slice_key,
            'sample_count': n,
            'mean_fwd_ret_5': mean_ret,
            'win_rate': win_rate,
            'mean_mfe_5': mean_mfe,
            'mean_mae_5': mean_mae,
            'fwd_ret_ratio': ratio
        })

    df_slices = pd.DataFrame(slice_metrics)
    if not df_slices.empty:
        df_slices = df_slices.sort_values("mean_fwd_ret_5", ascending=False).reset_index(drop=True)

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


def attach_cross_asset_states(primary_df, cond_symbol, timeframe, fields):
    """Load the conditioning symbol from the warehouse, rebuild its binned
    state frame the same way as the primary (compute_price_features +
    bin_features over full history), and backward as-of merge the requested
    state fields onto primary_df. Returns primary_df unchanged if the
    conditioning symbol has no warehouse data.
    """
    cond_raw = load_from_warehouse(cond_symbol, timeframe)
    if cond_raw.empty:
        return primary_df.copy()

    cond_feat = compute_price_features(cond_raw)
    cond_binned = bin_features(cond_feat)
    return align_cross_asset_states(primary_df, cond_binned, cond_symbol, fields)

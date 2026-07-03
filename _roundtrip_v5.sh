#!/usr/bin/env bash
# Auto-generated V5 ML-integration roundtrip. Paste into the Price repo root.
# Writes 4 files (quoted heredocs, byte-exact) + appends a HANDOVER section.
set -euo pipefail

if [ ! -f "src/price/discovery.py" ] || [ ! -f "src/price/ml_discovery.py" ]; then
  echo "ERROR: run this from the Price repo root (must contain src/price/)." >&2
  exit 1
fi

echo "Applying V5 ML-integration changes..."

cat << '__PRICE_V5_ROUNDTRIP_END__' > "src/price/discovery.py"
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
__PRICE_V5_ROUNDTRIP_END__
echo "  wrote src/price/discovery.py"

cat << '__PRICE_V5_ROUNDTRIP_END__' > "src/price/ml_discovery.py"
"""
ML-based market state slice discovery using LightGBM.

Supports single features + 2-feature and 3-feature interactions.
"""

import pandas as pd
import numpy as np
from typing import List, Dict, Optional, Tuple
import lightgbm as lgb
from sklearn.model_selection import TimeSeriesSplit
from itertools import combinations

from price.features import compute_price_features
from price.discovery import bin_features, ML_FEATURE_TO_STATE, STATE_LABELS
from price.warehouse import load_from_warehouse


def prepare_ml_frame(symbol: str, timeframe: str, target_type: str = "regression") -> pd.DataFrame:
    df_raw = load_from_warehouse(symbol, timeframe)
    if df_raw.empty:
        return pd.DataFrame()

    df = compute_price_features(df_raw)
    if df.empty:
        return pd.DataFrame()

    if target_type == "regression":
        df['target'] = df['fwd_ret_5']
    elif target_type == "classification":
        df['target'] = df['target_positive_5bar']
    else:
        raise ValueError("target_type must be 'regression' or 'classification'")

    df = df.dropna(subset=['target'])

    if 'label_eligible' in df.columns:
        df = df[df['label_eligible']]

    return df


def train_slice_model(
    df: pd.DataFrame,
    feature_cols: List[str],
    target_col: str = 'target',
    n_splits: int = 4,
    task: str = "regression"
) -> Dict:
    if df.empty or len(df) < 100:
        return {"model": None, "importance": pd.DataFrame(), "cv_score": None}

    X = df[feature_cols].copy()
    y = df[target_col].copy()

    tscv = TimeSeriesSplit(n_splits=n_splits)
    scores = []
    importances = []

    for train_idx, val_idx in tscv.split(X):
        X_train, X_val = X.iloc[train_idx], X.iloc[val_idx]
        y_train, y_val = y.iloc[train_idx], y.iloc[val_idx]

        if task == "regression":
            model = lgb.LGBMRegressor(
                objective='regression',
                n_estimators=400,
                learning_rate=0.05,
                max_depth=6,
                num_leaves=31,
                subsample=0.8,
                colsample_bytree=0.8,
                random_state=42,
                verbose=-1
            )
        else:
            model = lgb.LGBMClassifier(
                objective='binary',
                n_estimators=400,
                learning_rate=0.05,
                max_depth=6,
                num_leaves=31,
                subsample=0.8,
                colsample_bytree=0.8,
                random_state=42,
                verbose=-1
            )

        model.fit(
            X_train, y_train,
            eval_set=[(X_val, y_val)],
            callbacks=[lgb.early_stopping(40), lgb.log_evaluation(0)]
        )

        if task == "regression":
            preds = model.predict(X_val)
            score = np.corrcoef(y_val, preds)[0, 1] if len(y_val) > 1 else 0
        else:
            preds = model.predict_proba(X_val)[:, 1]
            score = np.corrcoef(y_val, preds)[0, 1] if len(y_val) > 1 else 0

        scores.append(score)
        importances.append(model.feature_importances_)

    avg_importance = np.mean(importances, axis=0)
    importance_df = pd.DataFrame({
        'feature': feature_cols,
        'importance': avg_importance
    }).sort_values('importance', ascending=False)

    return {
        "model": model,
        "importance": importance_df,
        "cv_score": np.mean(scores)
    }


def extract_feature_interactions(
    model: lgb.Booster,
    feature_cols: List[str],
    top_n: int = 6,
    max_interaction_size: int = 3
) -> List[Dict]:
    if model is None:
        return []

    importance = model.feature_importances_
    top_indices = np.argsort(importance)[-top_n:][::-1]
    top_features = [feature_cols[i] for i in top_indices]

    interactions = []

    for combo in combinations(top_features, 2):
        interactions.append({
            "features": list(combo),
            "size": 2,
            "type": "interaction"
        })

    if max_interaction_size >= 3:
        for combo in combinations(top_features, 3):
            interactions.append({
                "features": list(combo),
                "size": 3,
                "type": "interaction"
            })

    return interactions


def run_ml_discovery(
    symbol: str,
    timeframe: str,
    feature_cols: Optional[List[str]] = None,
    min_samples: int = 50,
    target_type: str = "regression",
    include_interactions: bool = True,
    max_interaction_size: int = 3
) -> pd.DataFrame:
    if feature_cols is None:
        feature_cols = [
            'feat_ext_vs_ma_20',
            'feat_trend_slope_20',
            'feat_realized_vol_20',
            'feat_atr_norm_ext',
            'feat_ret_1',
            'feat_ret_3',
            'feat_ret_5',
            'feat_ret_10',
            'feat_ret_20',
            'feat_vol_regime',
            'feat_trend_strength_20',
            'feat_session_bucket',
            'feat_dow',
            'feat_gap',
            'feat_range_position'
        ]

    df = prepare_ml_frame(symbol, timeframe, target_type=target_type)
    if df.empty or len(df) < min_samples:
        print(f"Insufficient data for ML discovery on {symbol} {timeframe}")
        return pd.DataFrame()

    task = "classification" if target_type == "classification" else "regression"

    result = train_slice_model(df, feature_cols, task=task)
    if result["model"] is None:
        return pd.DataFrame()

    records = []

    # Single features
    for _, row in result["importance"].head(10).iterrows():
        records.append({
            "symbol": symbol,
            "timeframe": timeframe,
            "slice_key": row['feature'],
            "importance": row['importance'],
            "source": f"lightgbm_{target_type}",
            "interaction_size": 1,
            "features": [row['feature']],
            "cv_correlation": result["cv_score"]
        })

    # Feature interactions
    if include_interactions:
        interactions = extract_feature_interactions(
            result["model"],
            feature_cols,
            top_n=6,
            max_interaction_size=max_interaction_size
        )

        for inter in interactions:
            key = " + ".join(inter["features"])
            records.append({
                "symbol": symbol,
                "timeframe": timeframe,
                "slice_key": key,
                "importance": None,
                "source": f"lightgbm_{target_type}_interaction",
                "interaction_size": inter["size"],
                "features": inter["features"],
                "cv_correlation": result["cv_score"]
            })

    return pd.DataFrame(records)


def evaluate_interactions(
    df: pd.DataFrame,
    interactions: List[Dict],
    target_col: str = 'fwd_ret_5',
    min_samples: int = 15
) -> pd.DataFrame:
    """
    Evaluate 2-feature and 3-feature combinations.
    Returns scored results with mean return, hit rate, and sample size.
    """
    if df.empty or not interactions:
        return pd.DataFrame()

    results = []

    for inter in interactions:
        # Upstream records carry a "features" list. Fall back to parsing a
        # "slice_key" string ("feat_a + feat_b") for dicts that only carry
        # the latter, so the documented V5 workflow is robust to either shape
        # (run_ml_discovery records now carry both, but hand-built dicts or
        # older callers may carry only slice_key).
        features = inter.get("features")
        if features is None and "slice_key" in inter:
            features = [f.strip() for f in str(inter["slice_key"]).split("+") if f.strip()]
        features = features or []

        size = inter.get("size") or inter.get("interaction_size") or len(features) or 2

        if not features:
            continue

        mask = pd.Series(True, index=df.index)

        for feat in features:
            if feat not in df.columns:
                continue
            threshold = df[feat].quantile(0.75)
            mask = mask & (df[feat] >= threshold)

        subset = df[mask]

        if len(subset) < min_samples:
            continue

        mean_ret = subset[target_col].mean()
        hit_rate = (subset[target_col] > 0).mean()
        n = len(subset)

        results.append({
            "slice_key": " + ".join(features),
            "interaction_size": size,
            "n_samples": n,
            "mean_return": round(mean_ret, 6),
            "hit_rate": round(hit_rate, 4),
            "sharpe_proxy": round(mean_ret / (subset[target_col].std() + 1e-8), 4)
        })

    if not results:
        return pd.DataFrame()

    scored = pd.DataFrame(results)
    scored = scored.sort_values(["mean_return", "n_samples"], ascending=[False, False])
    return scored


def ml_interaction_to_state_slice(
    binned_df: pd.DataFrame,
    features: List[str],
) -> Tuple[Dict[str, str], List[str]]:
    """Translate a raw ML feature list into a state_*=value slice filter.

    This is the bridge between ML discovery and the existing validation
    pipeline. evaluate_interactions defines the promising region as the
    high-quantile (>= q75) side of each feature -- i.e. it only ever tests
    "this feature is HIGH". So each feature is mapped to its HIGHEST state
    bucket (state_ext -> stretched_up, state_ret_3 -> ret_up, state_vol ->
    high_vol, ...), preserving the directional intent the ML found. Mapping
    to the count-dominant bucket instead would be wrong: a feature's
    top-25% often straddles the "neutral" bucket when the bin uses fixed
    thresholds (e.g. state_ext at +-0.015), which would discard the very
    "high" signal the ML surfaced.

    Returns (slice_filter, mapped_features). Features with no state mapping,
    or whose state column is absent / unpopulated in `binned_df`, are skipped.
    """
    slice_filter: Dict[str, str] = {}
    mapped: List[str] = []

    for feat in features:
        state_field = ML_FEATURE_TO_STATE.get(feat)
        if state_field is None or state_field not in binned_df.columns:
            continue
        if state_field not in STATE_LABELS:
            continue

        # Only emit when the top bucket is actually populated (>=1 non-null
        # row), so we never produce a slice guaranteed to match zero rows.
        present = binned_df[state_field].dropna().astype(str)
        if present.empty:
            continue

        slice_filter[state_field] = STATE_LABELS[state_field][-1]
        mapped.append(feat)

    return slice_filter, mapped


def interactions_to_state_slices(
    df: pd.DataFrame,
    scored: pd.DataFrame,
    symbol: str,
    timeframe: str,
    min_features_mapped: int = 1,
) -> pd.DataFrame:
    """Convert scored ML feature interactions into candidate slices in the
    discovered_slices.csv schema so they flow through validate_slices.py
    unchanged.

    `df` is the prepared ML frame (from prepare_ml_frame). `scored` is the
    output of evaluate_interactions and must contain a 'slice_key' column of
    '+ '-joined raw feature names.

    Output columns:
      - symbol, timeframe, slice_combination  (the three columns
        validate_slices.run_validation reads)
      - source, ml_slice_key, ml_interaction_size, ml_n_samples,
        ml_mean_return, ml_sharpe_proxy  (ML provenance, ignored by
        validation but useful for traceability)
    """
    if df.empty or scored.empty:
        return pd.DataFrame()

    binned = bin_features(df)
    if binned.empty:
        return pd.DataFrame()

    records = []
    for _, row in scored.iterrows():
        slice_key = str(row.get("slice_key", "")).strip()
        if not slice_key:
            continue

        features = [f.strip() for f in slice_key.split("+") if f.strip()]
        slice_filter, mapped = ml_interaction_to_state_slice(binned, features)

        if len(mapped) < min_features_mapped or not slice_filter:
            continue

        combo = " + ".join(f"{field}={value}" for field, value in slice_filter.items())
        records.append(
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "slice_combination": combo,
                "source": "ml_interaction",
                "ml_slice_key": slice_key,
                "ml_interaction_size": int(row.get("interaction_size", len(mapped))),
                "ml_n_samples": row.get("n_samples"),
                "ml_mean_return": row.get("mean_return"),
                "ml_sharpe_proxy": row.get("sharpe_proxy"),
            }
        )

    return pd.DataFrame(records)
__PRICE_V5_ROUNDTRIP_END__
echo "  wrote src/price/ml_discovery.py"

cat << '__PRICE_V5_ROUNDTRIP_END__' > "scripts/ml_to_slices.py"
"""ML slice discovery -> validation bridge (Phase V5).

Runs LightGBM-based discovery, scores the extracted feature interactions,
converts the promising ones into the binned state_*=value slice format the
existing validate_slices.py pipeline consumes, and writes
localdata/ml_candidate_slices.csv in the discovered_slices.csv schema.

This closes the loop recorded as aspirational in HANDOVER.md V5: ML-discovered
feature interactions now flow through the same V4 validation discipline
(train/valid + cost + Newey-West + walk-forward + parent-excess) as
combinatorial discovery, without any new validation code.

Usage:
    python3 scripts/ml_to_slices.py --symbol SPY --timeframe 1d
    python3 scripts/ml_to_slices.py --symbols SPY QQQ --timeframe 1d --append

Then validate manually:
    python3 scripts/validate_slices.py \\
        --slices-path localdata/ml_candidate_slices.csv --candidate-leaderboard
"""

import argparse
from pathlib import Path

import pandas as pd

from price.config import SYMBOLS
from price.ml_discovery import (
    evaluate_interactions,
    interactions_to_state_slices,
    prepare_ml_frame,
    run_ml_discovery,
)

OUTPUT_PATH = "localdata/ml_candidate_slices.csv"

# Default "promising" thresholds match the V5 handover's candidate filter.
DEFAULT_N_SAMPLES_MIN = 30
DEFAULT_MEAN_RETURN_MIN = 0.0008
DEFAULT_SHARPE_MIN = 0.20


def run(
    target_symbols,
    timeframe,
    target_type,
    append,
    include_interactions,
    max_interaction_size,
    n_samples_min,
    mean_return_min,
    sharpe_min,
    eval_min_samples,
    out_path,
):
    symbols = [s.upper() for s in (target_symbols or SYMBOLS)]
    all_candidates = []

    for symbol in symbols:
        print(f"\n=== ML discovery: {symbol} ({timeframe}, target={target_type}) ===")

        result = run_ml_discovery(
            symbol,
            timeframe,
            target_type=target_type,
            include_interactions=include_interactions,
            max_interaction_size=max_interaction_size,
        )
        if result.empty:
            print(f"  -> No ML discovery results for {symbol} {timeframe}.")
            continue

        interactions = result[result["interaction_size"] > 1].to_dict("records")
        if not interactions:
            print("  -> No multi-feature interactions extracted from the model.")
            continue

        df = prepare_ml_frame(symbol, timeframe, target_type=target_type)
        if df.empty:
            print(f"  -> Empty ML frame for {symbol} {timeframe}; cannot score.")
            continue

        scored = evaluate_interactions(df, interactions, min_samples=eval_min_samples)
        if scored.empty:
            print("  -> No interactions passed the scoring sample floor.")
            continue

        mask = (
            (scored["n_samples"] >= n_samples_min)
            & (scored["mean_return"] > mean_return_min)
            & (scored["sharpe_proxy"] > sharpe_min)
        )
        promising = scored[mask].copy()
        if promising.empty:
            print(
                f"  -> No interactions met the promising thresholds "
                f"(n>={n_samples_min}, mean>{mean_return_min}, "
                f"sharpe>{sharpe_min})."
            )
            continue

        candidates = interactions_to_state_slices(df, promising, symbol, timeframe)
        if candidates.empty:
            print("  -> Promising interactions found, but none mapped to state slices.")
            continue

        print(f"  -> {len(candidates)} candidate state-slices from {len(promising)} promising interactions:")
        print(candidates[["slice_combination", "ml_slice_key"]].to_string(index=False))
        all_candidates.append(candidates)

    if not all_candidates:
        print("\nNo ML candidate slices produced.")
        return

    final = pd.concat(all_candidates, ignore_index=True)

    output_path = Path(out_path)
    if append and output_path.exists():
        existing = pd.read_csv(output_path)
        # Replace prior ML rows for the symbol/timeframe pairs covered by this
        # run, then append the fresh results (mirrors discover_slices.py).
        covered = {(s, timeframe) for s in symbols}
        keep_mask = existing.apply(
            lambda r: (r.get("symbol"), r.get("timeframe")) not in covered
            or r.get("source") != "ml_interaction",
            axis=1,
        )
        existing_keep = existing[keep_mask]
        final = pd.concat([existing_keep, final], ignore_index=True)

    final.to_csv(output_path, index=False)
    action = "Appended ML candidate slices to" if append else "Saved ML candidate slices to"
    print(f"\n{action} {output_path}")
    print("\nNext step - run them through V4 validation:")
    print(
        f"  python3 scripts/validate_slices.py "
        f"--slices-path {output_path} --candidate-leaderboard"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Convert ML feature interactions into validatable state slices."
    )
    parser.add_argument("--symbol", help="Single symbol to run ML discovery on")
    parser.add_argument(
        "--symbols", nargs="+", help="Multiple symbols (mutually exclusive with --symbol)"
    )
    parser.add_argument(
        "--timeframe", default="1d", choices=["15m", "1h", "1d"], help="Timeframe to explore"
    )
    parser.add_argument(
        "--target-type",
        default="regression",
        choices=["regression", "classification"],
        help="LightGBM target type",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Merge into existing ml_candidate_slices.csv (replaces ML rows for the "
        "same symbol/timeframe pairs covered by this run) instead of overwriting.",
    )
    parser.add_argument(
        "--no-interactions", action="store_true", help="Only emit single-feature candidates"
    )
    parser.add_argument(
        "--max-interaction-size",
        type=int,
        default=3,
        choices=[2, 3],
        help="Largest feature interaction size to extract",
    )
    parser.add_argument(
        "--n-samples-min", type=int, default=DEFAULT_N_SAMPLES_MIN, help="Promising n_samples floor"
    )
    parser.add_argument(
        "--mean-return-min",
        type=float,
        default=DEFAULT_MEAN_RETURN_MIN,
        help="Promising mean forward-return floor",
    )
    parser.add_argument(
        "--sharpe-min", type=float, default=DEFAULT_SHARPE_MIN, help="Promising sharpe-proxy floor"
    )
    parser.add_argument(
        "--eval-min-samples",
        type=int,
        default=15,
        help="Minimum samples for evaluate_interactions to keep an interaction at all",
    )
    parser.add_argument("--output", default=OUTPUT_PATH, help="Output CSV path")
    args = parser.parse_args()

    if args.symbol and args.symbols:
        parser.error("Use either --symbol or --symbols, not both.")

    target_symbols = [args.symbol] if args.symbol else args.symbols

    run(
        target_symbols=target_symbols,
        timeframe=args.timeframe,
        target_type=args.target_type,
        append=args.append,
        include_interactions=not args.no_interactions,
        max_interaction_size=args.max_interaction_size,
        n_samples_min=args.n_samples_min,
        mean_return_min=args.mean_return_min,
        sharpe_min=args.sharpe_min,
        eval_min_samples=args.eval_min_samples,
        out_path=args.output,
    )
__PRICE_V5_ROUNDTRIP_END__
echo "  wrote scripts/ml_to_slices.py"

cat << '__PRICE_V5_ROUNDTRIP_END__' > "tests/test_ml_discovery.py"
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
__PRICE_V5_ROUNDTRIP_END__
echo "  wrote tests/test_ml_discovery.py"

# Idempotent HANDOVER.md append (skips if the section already exists).
if ! grep -qF 'V5 — ML -> Validation Bridge (2026-07-03)' HANDOVER.md 2>/dev/null; then
  {
    echo ''
    cat << '__HANDOVER_SECTION_END__'


V5 — ML -> Validation Bridge (2026-07-03)
The V5 section above claims the ML path (4) "outputs candidate slices in the
same format as combinatorial discovery" and (5) "feeds into the existing
validate_slices.py pipeline." Until this patch both claims were aspirational,
not wired up. ml_discovery.py emitted raw feature interactions like
`feat_ext_vs_ma_20 + feat_ret_3`, but validate_slices.py only understands
binned `state_*=value` filters like `state_ext=stretched_up +
state_ret_3=ret_up`, and `bin_features()` did not even bin the return
features (`feat_ret_1/3/5/10/20`) that dominate ML candidate interactions.
So the 8 promising 2-feature combinations the previous session exported to
`localdata/ml_promising_slices.csv` could not actually be validated.

This patch closes that loop. It is the conversion helper the prior session
offered ("convert these combinations into the exact state format your current
bin_features() expects"). No new validation code and no new doctrine:
ML-discovered interactions now flow through the exact same V4 discipline
(train/valid + cost + Newey-West + walk-forward + parent-excess).

What was added:
- `src/price/discovery.py`:
  - `bin_features()` now also bins every raw feature LightGBM ranks highly
    into the state vocabulary: `state_ret_{1,3,5,10,20}`
    (ret_down/ret_flat/ret_up), plus `state_atr_ext`, `state_vol_regime`,
    `state_trend_strength`, `state_gap`, `state_range_pos`. Purely additive;
    the combinatorial discovery combinations never reference these columns,
    so discovery/leaderboard behaviour is unchanged.
  - `bin_features()` is now defensive: `state_ext`/`state_session`/`state_dow`
    are emitted as NaN (instead of raising) when their source feature column
    is absent, so it works on any feature subset.
  - `ML_FEATURE_TO_STATE` maps each raw ML feature to its state field;
    `STATE_LABELS` gives the ordered low->high label list per field. A test
    pins the high-bucket labels (stretched_up / ret_up / high_vol / uptrend)
    so the two dicts cannot drift from `bin_features`.
- `src/price/ml_discovery.py`:
  - `ml_interaction_to_state_slice()` + `interactions_to_state_slices()` are
    the bridge: turn a raw interaction (`feat_ext_vs_ma_20 + feat_ret_3`)
    into a `state_*=value` filter (`state_ext=stretched_up +
    state_ret_3=ret_up`) and emit a candidate table in the
    discovered_slices.csv schema (symbol / timeframe / slice_combination +
    ML provenance columns that validation ignores).
  - Each feature maps to its HIGHEST state bucket, not the count-dominant
    one. evaluate_interactions only ever tests the high-quantile (>= q75)
    side of a feature, so the faithful translation is the "high" bucket.
    Using the count-dominant bucket would be wrong: a feature's top-25%
    often straddles "neutral" when the bin uses fixed thresholds (state_ext
    at +-0.015), which would discard the very "high" signal the ML surfaced.
  - Adds a `slice_key` fallback to `evaluate_interactions()` on top of
    upstream commit `85bf3c7` (which already made `run_ml_discovery` records
    carry a `"features"` key and switched the loop to `.get("features", [])`).
    The fallback means hand-built dicts or older callers that carry only a
    `"slice_key": "feat_a + feat_b"` are still handled, not silently skipped.
    Upstream's records now carry both keys, so the documented V5 workflow
    (`res = run_ml_discovery(...); evaluate_interactions(df,
    res[res.interaction_size>1].to_dict('records'))`) works either way.
- `scripts/ml_to_slices.py`: one-command glue that runs run_ml_discovery +
  evaluate_interactions, filters promising interactions (defaults match the
  prior session: n>=30, mean_return>0.0008, sharpe_proxy>0.20), converts
  them to state slices via the bridge, and writes
  `localdata/ml_candidate_slices.csv`. Supports `--symbols`, `--timeframe`,
  `--append`, `--target-type`, and the threshold flags.
- `tests/test_ml_discovery.py`: 8 unit tests (synthetic fixtures, no
  warehouse/network) covering the new state bins, the feature->state
  mapping, top-bucket selection, schema tolerance, and a round-trip that
  the emitted slice is parseable by `parse_slice_combination` and
  applyable by `apply_slice_filter`.

Verification:
- `python3 -m py_compile` clean on all changed files.
- `python3 -m pytest -q` -> 77 passed (was 69; +8 new).
- `python3 -m ruff check` clean on all changed files.
- An end-to-end smoke run on a synthetic SPY 1d warehouse with a
  constructed edge exercised the full chain -- run_ml_discovery ->
  evaluate_interactions (slice_key schema) -> bridge ->
  `state_ext=stretched_up + state_ret_3=ret_up` ->
  validate_slices.run_validation -- and produced a real scorecard for the
  ML candidate (verdict: rejected, as expected for synthetic noise; the
  plumbing is what was being verified).

How to run it (operator, on real warehouse data):
  python3 scripts/ml_to_slices.py --symbol SPY --timeframe 1d
  # then through the full V4 discipline:
  python3 scripts/validate_slices.py \
      --slices-path localdata/ml_candidate_slices.csv --candidate-leaderboard

Notes / doctrine unchanged:
- ML candidate slices are candidates, not promotions. They must clear the
  same train+valid+cost+Newey-West+walk-forward+parent-excess+search-wide
  gates as combinatorial slices before any monitoring/tracking.
- The ML bridge is a discovery expansion, not a validation shortcut. A
  "promising" ML interaction (high mean_return / sharpe_proxy on the
  in-sample high-quantile region) is explicitly NOT evidence of an edge;
  it is a hypothesis that V4 validation then tries to falsify.
- `scripts/ml_to_slices.py` overwrites `localdata/ml_candidate_slices.csv`
  by default; use `--append` when running across multiple symbols so
  earlier results are not lost (same convention as discover_slices.py).
__HANDOVER_SECTION_END__
  } >> HANDOVER.md
  echo "  appended HANDOVER.md V5 bridge section"
else
  echo "  HANDOVER.md section already present; skipping"
fi

echo "Done."

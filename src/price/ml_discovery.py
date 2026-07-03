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

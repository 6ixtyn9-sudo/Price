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
    """
    Extract promising 2-feature and 3-feature combinations.
    Uses the trained model's feature importance to guide combinations.
    """
    if model is None:
        return []

    # Get top features
    importance = model.feature_importances_
    top_indices = np.argsort(importance)[-top_n:][::-1]
    top_features = [feature_cols[i] for i in top_indices]

    interactions = []

    # 2-feature combinations
    for combo in combinations(top_features, 2):
        interactions.append({
            "features": list(combo),
            "size": 2,
            "type": "interaction"
        })

    # 3-feature combinations (if requested)
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

    # Single features
    records = []
    for _, row in result["importance"].head(10).iterrows():
        records.append({
            "symbol": symbol,
            "timeframe": timeframe,
            "slice_key": row['feature'],
            "importance": row['importance'],
            "source": f"lightgbm_{target_type}",
            "interaction_size": 1,
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
                "cv_correlation": result["cv_score"]
            })

    return pd.DataFrame(records)

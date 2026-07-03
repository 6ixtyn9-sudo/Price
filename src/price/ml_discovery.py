"""
ML-based market state slice discovery using LightGBM.

Supports both regression and classification targets.
"""

import pandas as pd
import numpy as np
from typing import List, Dict, Optional
import lightgbm as lgb
from sklearn.model_selection import TimeSeriesSplit

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


def extract_important_slices(importance_df: pd.DataFrame, top_n: int = 10) -> List[Dict]:
    slices = []
    for _, row in importance_df.head(top_n).iterrows():
        slices.append({
            "feature": row['feature'],
            "importance": float(row['importance']),
            "type": "ml_derived"
        })
    return slices


def run_ml_discovery(
    symbol: str,
    timeframe: str,
    feature_cols: Optional[List[str]] = None,
    min_samples: int = 50,
    target_type: str = "regression"
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

    slices = extract_important_slices(result["importance"])

    records = []
    for s in slices:
        records.append({
            "symbol": symbol,
            "timeframe": timeframe,
            "slice_key": s["feature"],
            "importance": s["importance"],
            "source": f"lightgbm_{target_type}",
            "cv_correlation": result["cv_score"]
        })

    return pd.DataFrame(records)

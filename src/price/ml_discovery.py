"""
ML-based market state slice discovery using LightGBM.

Replaces or augments the combinatorial discovery in discovery.py.
Keeps the same validation pipeline downstream.
"""

import pandas as pd
import numpy as np
from typing import List, Dict, Optional
import lightgbm as lgb
from sklearn.model_selection import TimeSeriesSplit

from price.features import compute_price_features
from price.warehouse import load_from_warehouse


def prepare_ml_frame(symbol: str, timeframe: str) -> pd.DataFrame:
    """Load warehouse data and compute features + forward returns for ML."""
    df_raw = load_from_warehouse(symbol, timeframe)
    if df_raw.empty:
        return pd.DataFrame()

    df = compute_price_features(df_raw)
    if df.empty:
        return pd.DataFrame()

    # Target: 5-bar forward return (consistent with current validation)
    df['target_5bar_ret'] = df['close_adj'].shift(-5) / df['close_adj'] - 1
    df = df.dropna(subset=['target_5bar_ret'])

    # Use only rows that would be eligible in the normal pipeline
    if 'label_eligible' in df.columns:
        df = df[df['label_eligible']]

    return df


def train_slice_model(
    df: pd.DataFrame,
    feature_cols: List[str],
    target_col: str = 'target_5bar_ret',
    n_splits: int = 4
) -> Dict:
    """Train LightGBM model with time-series cross-validation."""
    if df.empty or len(df) < 100:
        return {"model": None, "importance": pd.DataFrame(), "cv_score": None}

    X = df[feature_cols].copy()
    y = df[target_col].copy()

    # Time-series split
    tscv = TimeSeriesSplit(n_splits=n_splits)
    scores = []
    importances = []

    for train_idx, val_idx in tscv.split(X):
        X_train, X_val = X.iloc[train_idx], X.iloc[val_idx]
        y_train, y_val = y.iloc[train_idx], y.iloc[val_idx]

        model = lgb.LGBMRegressor(
            objective='regression',
            n_estimators=300,
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
            callbacks=[lgb.early_stopping(30), lgb.log_evaluation(0)]
        )

        preds = model.predict(X_val)
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


def extract_important_slices(
    importance_df: pd.DataFrame,
    top_n: int = 8
) -> List[Dict]:
    """Convert top features into candidate slice definitions."""
    slices = []
    for _, row in importance_df.head(top_n).iterrows():
        feat = row['feature']
        slices.append({
            "feature": feat,
            "importance": float(row['importance']),
            "type": "ml_derived"
        })
    return slices


def run_ml_discovery(
    symbol: str,
    timeframe: str,
    feature_cols: Optional[List[str]] = None,
    min_samples: int = 50
) -> pd.DataFrame:
    """
    Main entry point for ML slice discovery.
    Returns a DataFrame compatible with the existing discovered_slices format.
    """
    if feature_cols is None:
        feature_cols = [
            'feat_ext_vs_ma_20',
            'feat_trend_slope_20',
            'feat_realized_vol_20',
            'feat_ret_1',
            'feat_ret_3',
            'feat_ret_5',
            'feat_session_bucket',
            'feat_dow'
        ]

    df = prepare_ml_frame(symbol, timeframe)
    if df.empty or len(df) < min_samples:
        print(f"Insufficient data for ML discovery on {symbol} {timeframe}")
        return pd.DataFrame()

    result = train_slice_model(df, feature_cols)
    if result["model"] is None:
        return pd.DataFrame()

    slices = extract_important_slices(result["importance"])

    # Convert to the same format as combinatorial discovery
    records = []
    for s in slices:
        records.append({
            "symbol": symbol,
            "timeframe": timeframe,
            "slice_key": s["feature"],
            "importance": s["importance"],
            "source": "lightgbm",
            "cv_correlation": result["cv_score"]
        })

    return pd.DataFrame(records)

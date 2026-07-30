"""Tests for per-slice stop calibration (best_stop_atr_mult).

The stop multiplier is a high PERCENTILE of the slice's own max-adverse-
excursion (in ATR) over the hold, clamped to a protective range -- non-overfit
by construction (a percentile of the adverse distribution, NOT an in-sample
stop chosen to maximize historical P&L).
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from validate_slices import _calibrate_stop_atr_mult  # noqa: E402
from price.features import compute_price_features  # noqa: E402


# --- _calibrate_stop_atr_mult -------------------------------------------

def test_clamps_to_hi():
    # 90th pct of 1..10 is ~9.1 -> clamped to hi=5.0
    assert _calibrate_stop_atr_mult([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]) == 5.0


def test_clamps_to_lo():
    # 90th pct of 0.1..1.0 is ~0.91 -> clamped to lo=1.5
    assert _calibrate_stop_atr_mult([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]) == 1.5


def test_unclamped_within_range():
    v = _calibrate_stop_atr_mult([2.0, 2.5, 3.0, 3.5, 4.0, 2.2, 2.7, 3.2, 3.7, 4.2])
    assert v is not None and 1.5 < v < 5.0


def test_too_few_samples_returns_none():
    assert _calibrate_stop_atr_mult([1, 2, 3]) is None
    assert _calibrate_stop_atr_mult([]) is None


def test_negatives_count_as_zero_no_adverse():
    # Negative/zero adverse (the trade never went against entry) must be
    # clipped to 0, not crash or blow up the percentile.
    v = _calibrate_stop_atr_mult([-1.0, -0.5, 0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0])
    assert v is not None and 1.5 <= v <= 5.0


def test_accepts_pandas_series():
    # The caller passes a window column (a pandas Series).
    v = _calibrate_stop_atr_mult(pd.Series([2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 2.2, 2.7, 3.2]))
    assert v is not None and 1.5 <= v <= 5.0


# --- features.py emits the calibration inputs ---------------------------

def test_features_emits_adverse_atr_columns():
    n = 40
    base = 100.0
    rng = np.arange(n)
    df = pd.DataFrame({
        "bar_ts_utc": pd.date_range("2026-01-01", periods=n, freq="D", tz="UTC"),
        "close_adj": base + rng,
        "high_adj": base + rng + 1.0,
        "low_adj": base + rng - 1.0,
    })
    out = compute_price_features(df)
    for col in ("feat_atr", "fwd_mae_atr_5_long", "fwd_mae_atr_5_short"):
        assert col in out.columns, f"compute_price_features missing {col}"
    # adverse-atr columns are non-negative where finite (clipped at 0)
    assert (out["fwd_mae_atr_5_long"].dropna() >= 0).all()
    assert (out["fwd_mae_atr_5_short"].dropna() >= 0).all()
    # ATR is positive where finite
    assert (out["feat_atr"].dropna() > 0).all()
    # In a monotonically rising series a LONG never sees adverse (0), but a
    # SHORT does (price keeps rising above entry) -> confirms direction logic.
    assert (out["fwd_mae_atr_5_short"].dropna() > 0).any()

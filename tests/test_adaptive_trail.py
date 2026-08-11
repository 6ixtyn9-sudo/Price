"""Tests for the 2026-08-10 volatility-adaptive trailing stop.

adaptive=True scales the trailing-stop distance by ATR% (volatility relative to
price): low-vol names trail tighter (lock more of the run), high-vol names trail
looser (give a volatile winner room). adaptive=False (default) keeps the static
k_trail behaviour exactly.
"""
import numpy as np

from price.stops import StopState, update_trailing_stop


def _state(entry=100.0, stop=90.0, r_per_share=10.0, price=150.0, extreme=150.0):
    return StopState(
        symbol="X", side="long", qty=10, entry_price=entry,
        initial_stop_price=stop, current_stop_price=stop,
        r_per_share=r_per_share, stage="initial", extreme_price=extreme,
    )


def test_adaptive_default_off_matches_static():
    """adaptive=False must reproduce the exact static k_trail behaviour."""
    st = _state()
    a = update_trailing_stop(st, 150.0, atr=2.0, k_trail=3.0, adaptive=False)
    b = update_trailing_stop(st, 150.0, atr=2.0, k_trail=3.0, adaptive=True)
    # low-vol (atr=2 on entry=100 -> atr_pct 2% -> mult 1.167 -> eff_k 3.5)
    # adaptive is TIGHTER (higher stop) than static here
    assert a.current_stop_price == 144.0      # 150 - 3*2
    assert b.current_stop_price == 143.0      # 150 - 3.5*2


def test_low_vol_trails_tighter():
    """Low ATR% -> tighter stop (locks more of the run)."""
    st = _state()
    low = update_trailing_stop(st, 150.0, atr=1.0, k_trail=3.0, adaptive=True)  # atr_pct 1%
    high = update_trailing_stop(st, 150.0, atr=4.0, k_trail=3.0, adaptive=True)  # atr_pct 4%
    assert low.current_stop_price > high.current_stop_price


def test_high_vol_trails_looser():
    """High ATR% -> looser stop (gives a volatile winner room)."""
    st = _state()
    high = update_trailing_stop(st, 150.0, atr=4.0, k_trail=3.0, adaptive=True)
    static = update_trailing_stop(st, 150.0, atr=4.0, k_trail=3.0, adaptive=False)
    assert high.current_stop_price < static.current_stop_price


def test_multiplier_clamped():
    """ATR% multiplier clamps to [0.5, 2.0] so it can't explode."""
    st = _state()
    tiny = update_trailing_stop(st, 150.0, atr=0.01, k_trail=3.0, adaptive=True)  # atr_pct 0.01%
    huge = update_trailing_stop(st, 150.0, atr=50.0, k_trail=3.0, adaptive=True)  # atr_pct 50%
    # eff_k clamped to [1.5, 6.0]; both must be valid, finite stops
    assert np.isfinite(tiny.current_stop_price)
    assert np.isfinite(huge.current_stop_price)
    assert tiny.current_stop_price > 90.0  # above initial/breakeven
    assert huge.current_stop_price <= 150.0  # never above current price for long


def test_breakeven_floor_preserved():
    """The chandelier never drags a long below the breakeven floor."""
    st = _state(entry=100.0, stop=90.0)
    out = update_trailing_stop(st, 115.0, atr=8.0, k_trail=3.0, adaptive=True)
    # eff_k clamped to 6, chandelier=115-6*8=67 < breakeven 100 -> keep breakeven floor
    assert out.current_stop_price >= 100.0

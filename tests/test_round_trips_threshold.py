"""Tests for the 2026-08-10 timeframe-aware round-trip evidence threshold.

A flat MIN_ROUND_TRIPS_FOR_STATS=5 reaches 'trust' too quickly for intraday
slices (which fire multiple times per day and are noisy), and too slowly for
daily (which fire a few times a month). The floor is now timeframe-aware:
1d=5, 1h=15, 15m=20.
"""
import pandas as pd

from price.attribution import _round_trips_floor


def test_daily_floor_is_5():
    assert _round_trips_floor("1d") == 5


def test_hourly_floor_is_15():
    assert _round_trips_floor("1h") == 15


def test_15m_floor_is_20():
    assert _round_trips_floor("15m") == 20


def test_unknown_timeframe_defaults_to_5():
    assert _round_trips_floor("") == 5
    assert _round_trips_floor(None) == 5
    assert _round_trips_floor("weird") == 5


def test_round_trips_floor_matches_daily_for_legacy():
    """Legacy rows without an explicit timeframe fall back to the daily-ish bar."""
    assert _round_trips_floor(None) == _round_trips_floor("1d")

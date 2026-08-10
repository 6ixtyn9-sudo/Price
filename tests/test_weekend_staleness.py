"""Tests for the 2026-08-10 weekend-aware daily staleness gate.

Root cause fixed: the 1d staleness gate used a flat 72h hour count, so Friday's
daily bar (~86h old) was rejected on Monday morning, silently killing every daily
slice (no_state_data on all 1d scans). The gate is now calendar-aware: a daily bar
is fresh if it is the most recent COMPLETED NYSE session (weekend/holiday gaps are
expected, not staleness).
"""
import datetime

import pandas as pd

from price.monitor import _is_latest_completed_trading_session


def _dt(y, m, d, h=0, minute=0):
    return datetime.datetime(y, m, d, h, minute, tzinfo=datetime.timezone.utc)


def test_friday_bar_fresh_on_monday_morning():
    """Friday's bar is the latest completed session pre-Monday-close."""
    assert _is_latest_completed_trading_session(
        _dt(2026, 8, 7), _dt(2026, 8, 10, 14, 21)
    ) is True


def test_friday_bar_stale_after_monday_close():
    """Once Monday's session closes, Friday's bar is no longer the latest."""
    assert _is_latest_completed_trading_session(
        _dt(2026, 8, 7), _dt(2026, 8, 10, 21)
    ) is False


def test_old_wednesday_bar_stale_on_monday():
    """A genuinely old bar (multiple sessions since) is still stale."""
    assert _is_latest_completed_trading_session(
        _dt(2026, 8, 5), _dt(2026, 8, 10, 14, 21)
    ) is False


def test_thursday_bar_fresh_on_friday_morning():
    """Same-day/intraday case: prior session is still the latest completed."""
    assert _is_latest_completed_trading_session(
        _dt(2026, 8, 6), _dt(2026, 8, 7, 14)
    ) is True


def test_same_day_bar_fresh():
    """Today's session not yet closed -> today's open-date bar is fresh."""
    assert _is_latest_completed_trading_session(
        _dt(2026, 8, 7), _dt(2026, 8, 7, 14)
    ) is True


def test_friday_bar_stale_once_monday_closes():
    """Edge: exactly when Monday's close passes, staleness flips."""
    assert _is_latest_completed_trading_session(
        _dt(2026, 8, 7), _dt(2026, 8, 10, 20, 30)
    ) is False


def test_stale_warehouse_reason_intraday_unchanged(monkeypatch):
    """Intraday timeframes must keep the strict hour gate (not calendar-aware)."""
    from price.monitor import _stale_warehouse_reason
    df = pd.DataFrame({
        "bar_ts_utc": [_dt(2026, 8, 10, 9)],  # 5h old
        "close_adj": [100.0],
    })
    monkeypatch.setattr(
        "price.monitor._is_latest_completed_trading_session", lambda *a, **k: True
    )
    # 1h gate = 8h; a 5h-old 1h bar is fresh, but we only care that intraday
    # does NOT get the calendar bypass for a very old bar.
    old = pd.DataFrame({
        "bar_ts_utc": [_dt(2026, 8, 8, 9)],  # >8h old for 1h
        "close_adj": [100.0],
    })
    r = _stale_warehouse_reason(old, "1h")
    assert r is not None and "stale" in r

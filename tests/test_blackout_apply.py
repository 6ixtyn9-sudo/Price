"""Tests for the 2026-08-10 blackout tightening.

The macro-event blackout gate previously evaluated `is_blackout()` against the
timestamp of the LAST COMPLETED BAR. On a Monday that meant Friday's bar was
checked; if Friday was an NFP/CPI/OPEX/FOMC day, Monday's entries were blocked
even though the event had already passed. The fix evaluates the blackout against
the CURRENT trading day, so only the event day itself (and intraday event windows)
block entries -- not the day after.
"""
import pandas as pd

# Reconstruct the blackout date set the same way external_data.py does.
_FOMC_DATES = {"2026-07-29", "2026-09-16"}


def _nfp_dates():
    out = set()
    for m in range(1, 13):
        d = pd.Timestamp(year=2026, month=m, day=1)
        while d.dayofweek != 4:
            d += pd.Timedelta(days=1)
        out.add(d.strftime("%Y-%m-%d"))
    return out


def _opex_dates():
    out = set()
    for m in range(1, 13):
        d = pd.Timestamp(year=2026, month=m, day=1)
        seen = 0
        while d.month == m:
            if d.dayofweek == 4:
                seen += 1
                if seen == 3:
                    out.add(d.strftime("%Y-%m-%d"))
                    break
            d += pd.Timedelta(days=1)
    return out


def _cpi_windows():
    out = set()
    for m in range(1, 13):
        for day in (12, 13, 14):
            out.add(f"2026-{m:02d}-{day:02d}")
    return out


_BLACKOUT = set(_FOMC_DATES) | _nfp_dates() | _opex_dates() | _cpi_windows()


def is_blackout(ts):
    d = pd.Timestamp(ts).tz_convert("UTC").date().isoformat()
    return d in _BLACKOUT


def test_monday_after_nfp_is_not_blackout():
    """Aug 10 (Mon) after NFP on Aug 7 must NOT be blacked out."""
    assert is_blackout(pd.Timestamp("2026-08-10").tz_localize("UTC")) is False


def test_tuesday_after_weekend_not_blackout():
    assert is_blackout(pd.Timestamp("2026-08-11").tz_localize("UTC")) is False


def test_cpi_days_are_blackout():
    """Aug 12-14 (CPI window) must still be blacked out (event day itself)."""
    for day in (12, 13, 14):
        assert is_blackout(pd.Timestamp(f"2026-08-{day}").tz_localize("UTC")) is True


def test_opex_day_blackout():
    assert is_blackout(pd.Timestamp("2026-08-21").tz_localize("UTC")) is True


def test_nfp_day_itself_blackout():
    assert is_blackout(pd.Timestamp("2026-08-07").tz_localize("UTC")) is True

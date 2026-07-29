"""Tests for the falling-knife / stale-entry guard in price.trading.

These cover the PURE gap logic only (no network, no Alpaca). The guard skips
an entry when the live price has moved against the signal close by more than
a threshold -- the daily-signal-fills-next-day-into-a-decline failure mode.
"""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from price.trading import signal_fill_gap_bps, is_stale_entry  # noqa: E402


# --- signal_fill_gap_bps -------------------------------------------------

def test_gap_zero_and_sign():
    assert signal_fill_gap_bps(100.0, 100.0) == 0.0
    # live below signal -> negative (falling knife for a long)
    assert signal_fill_gap_bps(100.0, 98.0) == -200.0
    # live above signal -> positive
    assert signal_fill_gap_bps(100.0, 101.0) == 100.0


def test_gap_uncomputable_returns_none():
    assert signal_fill_gap_bps(None, 100.0) is None
    assert signal_fill_gap_bps(100.0, None) is None
    assert signal_fill_gap_bps(0.0, 100.0) is None
    assert signal_fill_gap_bps(float("nan"), 100.0) is None


# --- is_stale_entry ------------------------------------------------------

def test_long_blocks_when_price_fell_past_threshold():
    # LRCX case: signal 291.61, live 265.79 (~-885 bps)
    stale, gap = is_stale_entry("buy", 291.61, 265.79, 200.0)
    assert stale is True
    assert gap is not None and gap < -800


def test_long_allows_small_adverse_drift():
    # only -50 bps below signal -> within tolerance, take it
    stale, gap = is_stale_entry("long", 100.0, 99.5, 200.0)
    assert stale is False
    assert gap == -50.0


def test_short_blocks_when_price_rose_past_threshold():
    # shorting after a +300 bps rally is adverse
    stale, gap = is_stale_entry("sell", 100.0, 103.0, 200.0)
    assert stale is True
    assert gap is not None and gap > 200


def test_short_allows_small_favorable_drift():
    # short signal, price slipped DOWN 50 bps -> favourable, allow
    stale, gap = is_stale_entry("short", 100.0, 99.5, 200.0)
    assert stale is False


def test_disabled_when_threshold_zero():
    # even a 50% adverse gap must not block when the guard is off
    stale, _ = is_stale_entry("buy", 100.0, 50.0, 0.0)
    assert stale is False


def test_fail_open_when_no_live_price():
    # missing live quote -> never block (a quote outage must not halt trading)
    stale, gap = is_stale_entry("buy", 100.0, None, 200.0)
    assert stale is False
    assert gap is None

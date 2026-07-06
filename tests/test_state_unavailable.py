"""Tests for the state_unavailable audit row emission in monitor.scan_all_slices.

When paper_trade.py --dry-run scans a monitored slice and can't compute
a valid state (typically because today's bar is partial mid-session),
monitor.scan_all_slices now emits a kind=state_unavailable row in
addition to the per-slice entry_signal rows. This test pins that
behavior so future refactors don't silently drop the new audit kind.
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import price.warehouse as wh  # noqa: E402
from price.monitor import scan_all_slices  # noqa: E402


@pytest.fixture
def temp_warehouse(tmp_path, monkeypatch):
    """Set up a synthetic 1d warehouse with one valid symbol (XLF)
    and one with a NaN close on the most recent bar (SPY).
    """
    wh.WAREHOUSE_DIR = tmp_path / "wh"
    (tmp_path / "wh" / "symbol=XLF" / "timeframe=1d").mkdir(parents=True, exist_ok=True)
    (tmp_path / "wh" / "symbol=SPY" / "timeframe=1d").mkdir(parents=True, exist_ok=True)

    n = 80
    # XLF: clean data, all close_adj values present.
    # features.py reads high_adj/low_adj/open_adj, so we include
    # the *adj columns directly rather than relying on
    # propagate_adjustment_factors to derive them.
    xlf = pd.DataFrame({
        "bar_ts_utc": pd.date_range("2026-04-01", periods=n, freq="D", tz="UTC"),
        "open_raw": [50.0 + i * 0.1 for i in range(n)],
        "high_raw": [50.5 + i * 0.1 for i in range(n)],
        "low_raw": [49.5 + i * 0.1 for i in range(n)],
        "close_raw": [50.2 + i * 0.1 for i in range(n)],
        "volume_raw": [1000] * n,
        "open_adj": [50.0 + i * 0.1 for i in range(n)],
        "high_adj": [50.5 + i * 0.1 for i in range(n)],
        "low_adj": [49.5 + i * 0.1 for i in range(n)],
        "close_adj": [50.2 + i * 0.1 for i in range(n)],
    })
    xlf.to_parquet(tmp_path / "wh" / "symbol=XLF" / "timeframe=1d" / "data.parquet", index=False)

    # SPY: most recent close_adj is NaN (simulating partial bar).
    # open_adj / high_adj / low_adj are still present and non-NaN
    # so compute_price_features can run -- the NaN on close_adj
    # is what should trigger state_unavailable in monitor.
    spy = pd.DataFrame({
        "bar_ts_utc": pd.date_range("2026-04-01", periods=n, freq="D", tz="UTC"),
        "open_raw": [400.0 + i * 0.1 for i in range(n)],
        "high_raw": [401.0 + i * 0.1 for i in range(n)],
        "low_raw": [399.0 + i * 0.1 for i in range(n)],
        "close_raw": [400.5 + i * 0.1 for i in range(n)],
        "volume_raw": [1000] * n,
    })
    spy["open_adj"] = [400.0 + i * 0.1 for i in range(n)]
    spy["high_adj"] = [401.0 + i * 0.1 for i in range(n)]
    spy["low_adj"] = [399.0 + i * 0.1 for i in range(n)]
    spy["close_adj"] = [400.5 + i * 0.1 for i in range(n)]
    spy.loc[spy.index[-1], "close_adj"] = float("nan")
    spy.to_parquet(tmp_path / "wh" / "symbol=SPY" / "timeframe=1d" / "data.parquet", index=False)

    yield tmp_path


def test_state_unavailable_emitted_when_close_adj_nan(temp_warehouse, monkeypatch):
    """When close_adj is NaN on the most recent bar, scan_all_slices
    should emit a kind=state_unavailable row in addition to the
    per-slice entry_signal rows."""
    monkeypatch.setattr("price.monitor.get_open_positions", lambda: pd.DataFrame())
    monkeypatch.setattr("price.monitor.get_open_orders", lambda: pd.DataFrame())
    monkeypatch.setattr("price.monitor.get_today_realized_pnl", lambda: 0.0)
    monkeypatch.setattr("price.monitor.fetch_alpaca_bars", lambda *a, **k: pd.DataFrame())

    # Monitor only the SPY 1d slice (the one with NaN close_adj)
    slices = [
        {"symbol": "SPY", "timeframe": "1d", "slice_combination": "state_ext=neutral + state_slope=uptrend"},
    ]
    signals = scan_all_slices(slices=slices, dry_run=True)

    # We expect: 1 entry_signal (matched or not) + 1 state_unavailable
    kinds = [s.get("kind") for s in signals]
    assert "state_unavailable" in kinds, f"no state_unavailable row in {kinds}"
    su = next(s for s in signals if s.get("kind") == "state_unavailable")
    assert su["symbol"] == "SPY"
    assert su["timeframe"] == "1d"
    assert su["reason"] == "nan_state_features"
    assert "bar_ts_utc" in su
    assert "close_adj" in su


def test_no_state_unavailable_when_state_is_clean(temp_warehouse, monkeypatch):
    """When the warehouse has clean data and the state computes
    successfully, scan_all_slices should NOT emit any
    state_unavailable row."""
    monkeypatch.setattr("price.monitor.get_open_positions", lambda: pd.DataFrame())
    monkeypatch.setattr("price.monitor.get_open_orders", lambda: pd.DataFrame())
    monkeypatch.setattr("price.monitor.get_today_realized_pnl", lambda: 0.0)
    monkeypatch.setattr("price.monitor.fetch_alpaca_bars", lambda *a, **k: pd.DataFrame())

    # Monitor only the XLF 1d slice (the one with clean data)
    slices = [
        {"symbol": "XLF", "timeframe": "1d", "slice_combination": "state_ext=stretched_up + state_slope=flat"},
    ]
    signals = scan_all_slices(slices=slices, dry_run=True)

    # We expect entry_signal rows but no state_unavailable
    kinds = [s.get("kind") for s in signals]
    assert "state_unavailable" not in kinds, f"unexpected state_unavailable row in {kinds}"


def test_state_unavailable_when_warehouse_empty(temp_warehouse, monkeypatch):
    """When the warehouse has no data for a symbol, get_current_state
    returns None and the no_warehouse_data branch should emit
    state_unavailable with reason='no_warehouse_data'."""
    monkeypatch.setattr("price.monitor.get_open_positions", lambda: pd.DataFrame())
    monkeypatch.setattr("price.monitor.get_open_orders", lambda: pd.DataFrame())
    monkeypatch.setattr("price.monitor.get_today_realized_pnl", lambda: 0.0)
    monkeypatch.setattr("price.monitor.fetch_alpaca_bars", lambda *a, **k: pd.DataFrame())

    # Monitor a symbol that has no warehouse data at all
    slices = [
        {"symbol": "QQQ", "timeframe": "1d", "slice_combination": "state_ext=neutral + state_slope=uptrend"},
    ]
    signals = scan_all_slices(slices=slices, dry_run=True)

    # We expect: 1 entry_signal (no_state_data) + 1 state_unavailable (no_warehouse_data)
    su = [s for s in signals if s.get("kind") == "state_unavailable"]
    assert len(su) == 1
    assert su[0]["reason"] == "no_warehouse_data"
    assert su[0]["symbol"] == "QQQ"

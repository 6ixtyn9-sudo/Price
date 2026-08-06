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
        "bar_ts_utc": pd.date_range(pd.Timestamp.now(tz="UTC").normalize() - pd.Timedelta(days=n - 1), periods=n, freq="D", tz="UTC"),
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
        "bar_ts_utc": pd.date_range(pd.Timestamp.now(tz="UTC").normalize() - pd.Timedelta(days=n - 1), periods=n, freq="D", tz="UTC"),
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


def test_explicit_monitored_slices_preserve_bin_mode_and_regime_symbol(tmp_path, monkeypatch):
    """Deployment metadata is functional: bin_mode controls live state
    binning and regime_symbol controls the macro gate."""
    import price.monitor as monitor

    path = tmp_path / "monitored_slices.csv"
    path.write_text(
        "symbol,timeframe,slice_combination,side,bin_mode,regime_symbol,source_note\n"
        "XOP,1d,state_ext=stretched_down + state_slope=downtrend,long,rolling,SPY,test\n"
    )
    monkeypatch.setattr(monitor, "MONITORED_SLICES_PATH", path)

    rows = monitor._load_explicit_monitored_slices()

    assert rows == [{
        "symbol": "XOP",
        "timeframe": "1d",
        "slice_combination": "state_ext=stretched_down + state_slope=downtrend",
        "side": "long",
        "regime_symbol": "SPY",
        "source_note": "test",
        "bin_mode": "rolling",
    }]


def test_scan_threads_bin_mode_into_state_computation(monkeypatch):
    """Two slices on the same symbol/timeframe but different bin modes must
    not share one live state frame."""
    import price.monitor as monitor

    monkeypatch.setattr(monitor, "get_open_positions", lambda: pd.DataFrame())
    monkeypatch.setattr(monitor, "get_open_orders", lambda: pd.DataFrame())
    monkeypatch.setattr(monitor, "get_today_realized_pnl", lambda: 0.0)
    monkeypatch.setattr(monitor, "_load_open_position_slice_labels", lambda: {})
    monkeypatch.setattr(monitor, "load_stop_states", lambda: {})

    seen_modes = []

    def fake_state(symbol, timeframe, **kwargs):
        seen_modes.append(kwargs.get("bin_mode"))
        return pd.DataFrame([{
            "bar_ts_utc": pd.Timestamp("2026-07-06", tz="UTC"),
            "close_adj": 100.0,
            "state_ext": "neutral",
        }])

    monkeypatch.setattr(monitor, "get_current_state", fake_state)

    signals = monitor.scan_all_slices(
        slices=[
            {"symbol": "XLF", "timeframe": "1d", "slice_combination": "state_ext=neutral", "bin_mode": "insample"},
            {"symbol": "XLF", "timeframe": "1d", "slice_combination": "state_ext=neutral", "bin_mode": "rolling"},
        ],
        dry_run=True,
    )

    assert seen_modes == ["insample", "rolling"]
    matched = [s for s in signals if s.get("matched")]
    assert [s["bin_mode"] for s in matched] == ["insample", "rolling"]


def test_state_unavailable_stale_warehouse_bar_too_old(monkeypatch):
    import pandas as pd
    from price import monitor

    df_old = pd.DataFrame([{
        "bar_ts_utc": pd.Timestamp("2026-07-01 12:00:00", tz="UTC"),
        "close_raw": 100.0,
        "close_adj": 100.0,
    }])
    monkeypatch.setattr(monitor, "load_from_warehouse", lambda sym, tf: df_old)
    ctx = monitor._state_unavailable_context("SPY", "1d")
    assert "stale_warehouse_bar_too_old" in ctx["reason"]



# ---------------------------------------------------------------------------
# 2026-08-06 hardening tests (standalone imports kept deliberately so this
# block is portable across lineages; duplicate imports are harmless).
# ---------------------------------------------------------------------------

from pathlib import Path as _RTPath  # noqa: E402
import sys as _RTsys  # noqa: E402

for _sub in ("src", "scripts"):
    _p = str(_RTPath(__file__).resolve().parent.parent / _sub)
    if _p not in _RTsys.path:
        _RTsys.path.insert(0, _p)

from datetime import datetime, timedelta, timezone  # noqa: E402
_ROOT = _RTPath(__file__).resolve().parent.parent  # repo root for data pins

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

import price.monitor as mon  # noqa: E402

def _frame(raw, age_last=20, extra=None):
    last = datetime.now(timezone.utc) - timedelta(hours=age_last)
    n = len(raw)
    rows = []
    for i, c in enumerate(raw):
        row = {"bar_ts_utc": last - timedelta(hours=24 * (n - 1 - i)),
               "open_raw": float(c), "high_raw": float(c) * 1.003,
               "low_raw": float(c) * 0.997, "close_raw": float(c),
               "volume_raw": 1_000_000.0}
        if extra:
            row.update({k: v[i] for k, v in extra.items()})
        rows.append(row)
    return pd.DataFrame(rows)


def _phantom():
    return [100.0] * 69 + [67.0, 22.0]


def _features_must_not_run(*a, **k):
    raise RuntimeError("features must not run on a quarantined frame")


def test_phantom_split_flagged():
    c = _phantom()
    df = _frame(c, extra={"close_adj": list(c), "split_factor": [1.0] * len(c),
                          "adj_factor": [1.0] * len(c)})
    reason = mon._corporate_action_anomaly(df, "1d")
    assert reason and reason.startswith("corporate_action_unadjusted_move")


def test_gate_quarantined_and_audited(monkeypatch):
    if not hasattr(mon, "_state_unavailable_context"):
        pytest.skip("lineage has no Workstream-4 audit ctx (P5 skipped per APPLY_NOTES)")
    c = _phantom()
    df = _frame(c, extra={"close_adj": list(c), "split_factor": [1.0] * len(c),
                          "adj_factor": [1.0] * len(c)})
    monkeypatch.setattr(mon, "load_from_warehouse", lambda s, t: df)
    monkeypatch.setattr(mon, "compute_price_features", _features_must_not_run)
    assert mon.get_current_state("SMCI", "1d") is None
    ctx = mon._state_unavailable_context("SMCI", "1d")
    assert ctx["reason"].startswith("corporate_action_unadjusted_move")


def test_71_5h_bar_inside_stale_window_still_quarantined(monkeypatch):
    df = _frame(_phantom(), age_last=71.5,
                extra={"split_factor": [1.0] * 71, "adj_factor": [1.0] * 71})
    monkeypatch.setattr(mon, "load_from_warehouse", lambda s, t: df)
    monkeypatch.setattr(mon, "compute_price_features", _features_must_not_run)
    assert mon._stale_warehouse_reason(df, "1d") is None  # 71.5h < 72h window
    assert mon.get_current_state("SMCI", "1d") is None


def test_earnings_gap_passes():
    c = [100.0] * 69 + [100.0, 88.0]
    df = _frame(c, extra={"close_adj": list(c), "split_factor": [1.0] * len(c),
                          "adj_factor": [1.0] * len(c)})
    assert mon._corporate_action_anomaly(df, "1d") is None


def test_booked_split_passes():
    c = [67.0] * 69 + [67.0, 22.0]
    df = _frame(c, extra={"close_adj": list(c),
                          "split_factor": [1.0] * 70 + [3.0],
                          "adj_factor": [1.0] * len(c)})
    assert mon._corporate_action_anomaly(df, "1d") is None


def test_properly_adjusted_split_passes():
    raw = [300.0] * 69 + [300.0, 100.0]
    adj = [100.0] * 69 + [100.0, 100.0]
    df = _frame(raw, extra={"close_adj": adj,
                            "split_factor": [1.0] * 70 + [3.0],
                            "adj_factor": [a / r for a, r in zip(adj, raw)]})
    assert mon._corporate_action_anomaly(df, "1d") is None


def test_stale_precedence_preserved(monkeypatch):
    if not hasattr(mon, "_state_unavailable_context"):
        pytest.skip("lineage has no Workstream-4 audit ctx (P5 skipped per APPLY_NOTES)")
    c = _phantom()
    df = _frame(c, age_last=100, extra={"close_adj": list(c),
                                        "split_factor": [1.0] * len(c),
                                        "adj_factor": [1.0] * len(c)})
    monkeypatch.setattr(mon, "load_from_warehouse", lambda s, t: df)
    monkeypatch.setattr(mon, "compute_price_features", _features_must_not_run)
    assert mon.get_current_state("SMCI", "1d") is None
    assert mon._state_unavailable_context("SMCI", "1d")["reason"].startswith(
        "stale_warehouse_bar_too_old")


def test_degenerate_frames_clean():
    assert mon._corporate_action_anomaly(pd.DataFrame(), "1d") is None
    assert mon._corporate_action_anomaly(None, "1d") is None
    assert mon._corporate_action_anomaly(_frame([100.0]), "1d") is None

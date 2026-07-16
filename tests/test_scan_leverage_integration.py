"""End-to-end integration test: monitor.scan_all_slices actually wires the
leverage budgets (gross notional cap + margin cushion) into the live entry
gate, on top of the stop-reconciliation wiring proven in
test_scan_stop_integration.py.

Uses monitor.DEFAULT_MONITORED_SLICES-style manual slices against a
synthetic warehouse so a real entry SIGNAL is generated, then asserts the
leverage checks show up in the resulting risk_check payload correctly
under three regimes: leverage off (1.0x), leverage on with room, and
leverage on but budget exhausted.

All broker calls, the warehouse, and the stop-state files are isolated
(tmp_path / monkeypatch), so this never touches a real Alpaca account or
the real localdata/ files.
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import price.warehouse as wh  # noqa: E402
import price.stops as stops_mod  # noqa: E402
import price.trading as trading  # noqa: E402
from price.monitor import scan_all_slices  # noqa: E402
from price.risk_limits import RiskLimits  # noqa: E402


@pytest.fixture
def isolated_stop_files(tmp_path, monkeypatch):
    state_path = tmp_path / "stop_state.json"
    journal_path = tmp_path / "stopout_journal.json"
    monkeypatch.setattr(stops_mod, "STOP_STATE_PATH", state_path)
    monkeypatch.setattr(stops_mod, "STOPOUT_JOURNAL_PATH", journal_path)
    return state_path, journal_path


@pytest.fixture
def synthetic_warehouse(tmp_path, monkeypatch):
    """Minimal warehouse so downstream (exit-check / stop-reconciliation)
    warehouse reads don't explode; the ENTRY match itself is driven by
    monkeypatching get_current_state directly (see _patch_common), since
    reliably engineering raw OHLCV that bins to an exact state combination
    is brittle and not what this test is trying to prove."""
    wh.WAREHOUSE_DIR = tmp_path / "wh"
    part = tmp_path / "wh" / "symbol=XLF" / "timeframe=1d"
    part.mkdir(parents=True, exist_ok=True)
    n = 80
    closes = [50.0 + i * 0.1 for i in range(n)]
    df = pd.DataFrame({
        "bar_ts_utc": pd.date_range("2026-04-01", periods=n, freq="D", tz="UTC"),
        "open_raw": closes, "high_raw": [c + 0.5 for c in closes],
        "low_raw": [c - 0.5 for c in closes], "close_raw": closes,
        "volume_raw": [1000] * n,
        "open_adj": closes, "high_adj": [c + 0.5 for c in closes],
        "low_adj": [c - 0.5 for c in closes], "close_adj": closes,
    })
    df.to_parquet(part / "data.parquet", index=False)
    return tmp_path


def _fake_current_state():
    row = pd.DataFrame([{
        "bar_ts_utc": pd.Timestamp("2026-06-23", tz="UTC"),
        "close_adj": 71.0,
        "state_ext": "stretched_up",
        "state_slope": "flat",
    }])
    return row


def _patch_common(monkeypatch, buying_power=None):
    monkeypatch.setattr("price.monitor.get_open_positions", lambda: pd.DataFrame())
    monkeypatch.setattr("price.monitor.get_open_orders", lambda: pd.DataFrame())
    monkeypatch.setattr("price.monitor.get_today_realized_pnl", lambda: 0.0)
    monkeypatch.setattr("price.monitor.fetch_alpaca_bars", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr("price.monitor.get_current_state", lambda *a, **k: _fake_current_state())
    monkeypatch.setattr("price.trading.load_trade_journal", lambda: pd.DataFrame())
    monkeypatch.setattr(
        trading, "get_account_info",
        lambda: {"equity": 1000.0, "buying_power": buying_power, "cash": 1000.0,
                  "status": "ACTIVE", "pattern_day_trader": False},
    )


SLICE = [{
    "symbol": "XLF", "timeframe": "1d",
    "slice_combination": "state_ext=stretched_up + state_slope=flat", "side": "long",
}]


def _entry_signal(signals):
    matches = [s for s in signals if s.get("kind") == "entry_signal" and s.get("matched")]
    assert len(matches) == 1, f"expected exactly one matched entry signal, got: {signals}"
    return matches[0]


def test_leverage_off_by_default_no_notional_check_in_reasons(
    isolated_stop_files, synthetic_warehouse, monkeypatch
):
    _patch_common(monkeypatch, buying_power=None)
    limits = RiskLimits(
        max_notional_per_position=100000.0, account_equity_for_sizing=1000.0,
        conviction_sizing_enabled=False,
    )  # target_leverage_multiple defaults to 1.0

    signals = scan_all_slices(slices=SLICE, limits=limits, dry_run=False)
    sig = _entry_signal(signals)
    reasons = sig["risk_check"]["reasons"]
    assert not any("gross notional" in r for r in reasons)
    assert not any("margin cushion" in r for r in reasons)


def test_leverage_on_with_room_allows_entry(
    isolated_stop_files, synthetic_warehouse, monkeypatch
):
    _patch_common(monkeypatch, buying_power=1800.0)  # ceiling = 2*1000=2000; 1800/2000=0.9 >= 0.20
    limits = RiskLimits(
        max_notional_per_position=100000.0, account_equity_for_sizing=1000.0,
        conviction_sizing_enabled=False,
        target_leverage_multiple=2.0, margin_cushion_pct=0.20,
    )

    signals = scan_all_slices(slices=SLICE, limits=limits, dry_run=False)
    sig = _entry_signal(signals)
    assert sig["tradable"] is True
    assert "gross_notional" in sig["risk_check"]["details"]
    assert "margin_cushion" in sig["risk_check"]["details"]


def test_leverage_on_margin_cushion_breached_blocks_entry(
    isolated_stop_files, synthetic_warehouse, monkeypatch
):
    _patch_common(monkeypatch, buying_power=100.0)  # ceiling=2000; 100/2000=0.05 < 0.20
    limits = RiskLimits(
        max_notional_per_position=100000.0, account_equity_for_sizing=1000.0,
        conviction_sizing_enabled=False,
        target_leverage_multiple=2.0, margin_cushion_pct=0.20,
    )

    signals = scan_all_slices(slices=SLICE, limits=limits, dry_run=False)
    sig = _entry_signal(signals)
    assert sig["tradable"] is False
    assert any("margin cushion" in r for r in sig["risk_check"]["reasons"])


def test_incomplete_broker_reconciliation_blocks_new_entry(
    isolated_stop_files, synthetic_warehouse, monkeypatch
):
    """A stale/unresolved order ledger must fail closed for new entries."""
    _patch_common(monkeypatch, buying_power=None)
    limits = RiskLimits(
        max_notional_per_position=100000.0,
        account_equity_for_sizing=1000.0,
        conviction_sizing_enabled=False,
    )

    health = {
        "ok": False,
        "total_order_ids": 1,
        "resolved_order_ids": 0,
        "unresolved_order_ids": ["order-timeout"],
        "errors": ["broker timeout"],
    }
    signals = scan_all_slices(
        slices=SLICE,
        limits=limits,
        dry_run=False,
        entry_sync_blocked=True,
        reconciliation_health=health,
    )
    sig = _entry_signal(signals)
    assert sig["tradable"] is False
    assert sig["risk_check"]["allowed"] is False
    assert any("reconciliation incomplete" in r for r in sig["risk_check"]["reasons"])
    assert sig["risk_check"]["details"]["reconciliation_health"] == health

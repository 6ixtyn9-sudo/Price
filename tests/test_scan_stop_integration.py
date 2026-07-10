"""End-to-end integration test: monitor.scan_all_slices actually wires
together the exit check, the protective-stop reconciliation (stop_manager),
and the aggregate open-risk budget in the entry gate (risk_limits).

This exists because unit tests on stops.py / stop_manager.py / risk_limits.py
in isolation don't prove the pieces are actually CALLED from a real scan --
only that they behave correctly when called directly. This test proves the
wiring, not just the parts.

All broker calls, the warehouse, and the stop-state/stopout-journal files
are isolated (tmp_path / monkeypatch), so this never touches a real Alpaca
account or the real localdata/ files.
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
from price.stops import load_stop_states  # noqa: E402


@pytest.fixture
def isolated_stop_files(tmp_path, monkeypatch):
    state_path = tmp_path / "stop_state.json"
    journal_path = tmp_path / "stopout_journal.json"
    monkeypatch.setattr(stops_mod, "STOP_STATE_PATH", state_path)
    monkeypatch.setattr(stops_mod, "STOPOUT_JOURNAL_PATH", journal_path)
    return state_path, journal_path


@pytest.fixture
def synthetic_warehouse(tmp_path, monkeypatch):
    """One clean symbol (XLF) with enough history for compute_atr_14 and
    compute_price_features (needs >= 60 rows, and ATR needs >= 15)."""
    wh.WAREHOUSE_DIR = tmp_path / "wh"
    part = tmp_path / "wh" / "symbol=XLF" / "timeframe=1d"
    part.mkdir(parents=True, exist_ok=True)

    n = 80
    df = pd.DataFrame({
        "bar_ts_utc": pd.date_range("2026-04-01", periods=n, freq="D", tz="UTC"),
        "open_raw": [50.0 + i * 0.1 for i in range(n)],
        "high_raw": [51.0 + i * 0.1 for i in range(n)],
        "low_raw": [49.0 + i * 0.1 for i in range(n)],
        "close_raw": [50.2 + i * 0.1 for i in range(n)],
        "volume_raw": [1000] * n,
        "open_adj": [50.0 + i * 0.1 for i in range(n)],
        "high_adj": [51.0 + i * 0.1 for i in range(n)],
        "low_adj": [49.0 + i * 0.1 for i in range(n)],
        "close_adj": [50.2 + i * 0.1 for i in range(n)],
    })
    df.to_parquet(part / "data.parquet", index=False)
    return tmp_path


def _patch_common(monkeypatch, open_positions_df, submit_calls, replace_calls):
    monkeypatch.setattr("price.monitor.get_open_positions", lambda: open_positions_df)
    monkeypatch.setattr("price.monitor.get_open_orders", lambda: pd.DataFrame())
    monkeypatch.setattr("price.monitor.get_today_realized_pnl", lambda: 0.0)
    monkeypatch.setattr("price.monitor.fetch_alpaca_bars", lambda *a, **k: pd.DataFrame())

    def _fake_submit(symbol, qty, stop_price, side):
        submit_calls.append((symbol, qty, stop_price, side))
        return {"order_id": "order-int-1", "status": "accepted"}

    def _fake_replace(order_id, new_stop_price):
        replace_calls.append((order_id, new_stop_price))
        return {"order_id": order_id, "status": "replaced"}

    monkeypatch.setattr(trading, "submit_protective_stop", _fake_submit)
    monkeypatch.setattr(trading, "replace_protective_stop", _fake_replace)
    # No resting broker stop exists yet in this scenario -- explicit, so the
    # real (credentials-requiring) default is never silently relied upon via
    # an exception-swallowing fallback.
    monkeypatch.setattr(trading, "get_orders_for_symbol", lambda symbol, status="open": pd.DataFrame())
    # scan_all_slices doesn't call close_position/load_trade_journal in this
    # scenario (no exit fires), but stub defensively so a surprise call
    # never reaches a real broker.
    monkeypatch.setattr("price.trading.load_trade_journal", lambda: pd.DataFrame())


def test_fresh_position_gets_a_real_stop_attached_via_full_scan(
    isolated_stop_files, synthetic_warehouse, monkeypatch
):
    state_path, _ = isolated_stop_files
    open_positions = pd.DataFrame([{
        "symbol": "XLF", "qty": 10.0, "side": "long",
        "avg_entry_price": 58.0, "current_price": 58.0,
        "unrealized_pl": 0.0, "unrealized_plpc": 0.0, "market_value": 580.0,
    }])
    submit_calls, replace_calls = [], []
    _patch_common(monkeypatch, open_positions, submit_calls, replace_calls)

    signals = scan_all_slices(slices=[], limits=RiskLimits(), dry_run=False)

    stop_signals = [s for s in signals if s.get("kind") == "stop_intent"]
    assert len(stop_signals) == 1
    assert stop_signals[0]["action"] == "stop_attached"
    assert stop_signals[0]["symbol"] == "XLF"
    assert len(submit_calls) == 1
    assert submit_calls[0][0] == "XLF"
    assert submit_calls[0][3] == "long"

    saved = load_stop_states(path=state_path)
    assert "XLF" in saved
    assert saved["XLF"].stop_order_id == "order-int-1"


def test_winning_position_gets_ratcheted_via_full_scan(
    isolated_stop_files, synthetic_warehouse, monkeypatch
):
    """A position already tracked at +1R+ should have its resting stop
    REPLACED (ratcheted), not re-attached, on the next full scan."""
    from price.stops import new_stop_state, save_stop_states
    state_path, _ = isolated_stop_files

    # ATR on this synthetic series is ~2.0 (range = high-low = 2.0 every bar).
    existing = new_stop_state("XLF", "long", qty=10, entry_price=58.0, atr=2.0,
                               stop_order_id="order-orig-1")  # R=4, stop=54
    save_stop_states({"XLF": existing}, path=state_path)

    open_positions = pd.DataFrame([{
        "symbol": "XLF", "qty": 10.0, "side": "long",
        "avg_entry_price": 58.0, "current_price": 63.0,  # +1.25R
        "unrealized_pl": 50.0, "unrealized_plpc": 0.086, "market_value": 630.0,
    }])
    submit_calls, replace_calls = [], []
    _patch_common(monkeypatch, open_positions, submit_calls, replace_calls)

    signals = scan_all_slices(slices=[], limits=RiskLimits(), dry_run=False)

    stop_signals = [s for s in signals if s.get("kind") == "stop_intent"]
    assert len(stop_signals) == 1
    assert stop_signals[0]["action"] == "stop_ratcheted"
    assert len(replace_calls) == 1
    assert replace_calls[0][0] == "order-orig-1"
    assert replace_calls[0][1] == pytest.approx(58.0)  # ratcheted to breakeven

    saved = load_stop_states(path=state_path)
    assert saved["XLF"].current_stop_price == pytest.approx(58.0)
    assert saved["XLF"].stage == "breakeven"


def test_aggregate_open_risk_state_available_to_entry_gate(
    isolated_stop_files, synthetic_warehouse, monkeypatch
):
    """After a scan attaches a stop, the resulting StopState is loadable
    (as monitor.scan_all_slices does for the NEXT scan's entry-gate check)
    and correctly reports nonzero open risk for a fresh, unratcheted stop."""
    from price.stops import aggregate_open_risk_dollars, load_stop_states

    state_path, _ = isolated_stop_files
    open_positions = pd.DataFrame([{
        "symbol": "XLF", "qty": 10.0, "side": "long",
        "avg_entry_price": 58.0, "current_price": 58.0,
        "unrealized_pl": 0.0, "unrealized_plpc": 0.0, "market_value": 580.0,
    }])
    submit_calls, replace_calls = [], []
    _patch_common(monkeypatch, open_positions, submit_calls, replace_calls)

    scan_all_slices(slices=[], limits=RiskLimits(), dry_run=False)

    states = load_stop_states(path=state_path)
    total_risk = aggregate_open_risk_dollars(states)
    assert total_risk > 0  # fresh stop -> genuinely at risk


def test_scan_reconciles_stale_stop_state_even_when_account_flat(isolated_stop_files, monkeypatch):
    from price.stops import StopState, save_stop_states, load_stop_states

    state_path, _ = isolated_stop_files
    save_stop_states({
        "XOP": StopState(
            symbol="XOP", side="long", qty=1, entry_price=100,
            initial_stop_price=95, current_stop_price=95, r_per_share=5,
            stop_order_id="stop-1",
        )
    }, path=state_path)

    monkeypatch.setattr("price.monitor.get_open_positions", lambda: pd.DataFrame())
    monkeypatch.setattr("price.monitor.get_open_orders", lambda: pd.DataFrame())
    monkeypatch.setattr("price.monitor.get_today_realized_pnl", lambda: 0.0)
    monkeypatch.setattr("price.trading.load_trade_journal", lambda: pd.DataFrame())
    monkeypatch.setattr("price.trading.get_order_fill_info", lambda order_id: {"status": "canceled"})

    signals = scan_all_slices(slices=[], limits=RiskLimits(), dry_run=False)

    assert any(s.get("kind") == "stop_intent" and s.get("action") == "stop_state_cleared" for s in signals)
    assert load_stop_states(path=state_path) == {}

"""Tests for the broker-truth reconciliation fix: before treating a
position with no LOCAL tracked StopState as "fresh" and submitting a
brand-new stop order, reconcile_stops now checks whether the broker
already has a resting stop order for that symbol and adopts it instead.

Without this, a lost/corrupted stop_state.json (or a race between two
concurrent workflow runs) would cause reconcile_stops to submit a SECOND,
duplicate stop order on a position that already has one live at Alpaca --
an ambiguous, ill-defined protective state.
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import price.stop_manager as stop_manager  # noqa: E402
from price.stop_manager import _adopt_existing_broker_stop  # noqa: E402
from price.stops import load_stop_states  # noqa: E402


class _Limits:
    stop_atr_multiple = 2.0
    trail_atr_multiple = 3.0
    breakeven_trigger_r = 1.0
    target_leverage_multiple = 1.0


def _positions_df(rows):
    return pd.DataFrame(rows)


def _fake_submit_factory():
    calls = []

    def _fn(symbol, qty, stop_price, side):
        calls.append((symbol, qty, stop_price, side))
        return {"order_id": "should-not-be-called", "status": "accepted"}
    _fn.calls = calls
    return _fn


def _fake_replace_factory():
    return lambda order_id, new_stop_price: {"order_id": order_id, "status": "replaced"}


# ---------------------------------------------------------------------------
# _adopt_existing_broker_stop (unit-level)
# ---------------------------------------------------------------------------

def test_adopt_reconstructs_r_from_broker_stop_price():
    broker_order = pd.Series({"order_id": "order-99", "stop_price": 148.5, "type": "stop"})
    state = _adopt_existing_broker_stop("XOP", "long", qty=16, entry_price=154.47,
                                         broker_order=broker_order)
    assert state.stop_order_id == "order-99"
    assert state.current_stop_price == pytest.approx(148.5)
    assert state.initial_stop_price == pytest.approx(148.5)
    assert state.r_per_share == pytest.approx(154.47 - 148.5)
    assert state.stage == "initial"
    assert state.extreme_price == pytest.approx(154.47)


def test_adopt_handles_short_position_stop_above_entry():
    broker_order = pd.Series({"order_id": "order-100", "stop_price": 104.0, "type": "stop"})
    state = _adopt_existing_broker_stop("TLT", "short", qty=10, entry_price=100.0,
                                         broker_order=broker_order)
    assert state.r_per_share == pytest.approx(4.0)  # abs distance either direction


def test_adopt_handles_missing_stop_price_defensively():
    broker_order = pd.Series({"order_id": "order-101", "stop_price": None, "type": "stop"})
    state = _adopt_existing_broker_stop("XOP", "long", qty=16, entry_price=154.47,
                                         broker_order=broker_order)
    assert state.r_per_share == pytest.approx(0.0)  # degenerate but never raises
    assert state.current_risk_dollars() == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# reconcile_stops: full adoption flow
# ---------------------------------------------------------------------------

def test_adopts_broker_stop_instead_of_submitting_duplicate(tmp_path):
    """The headline case: local state.json is empty/lost, but the broker
    already has a resting stop for this symbol -> adopt it, do NOT submit
    a second stop order."""
    positions = _positions_df([
        {"symbol": "XOP", "side": "long", "qty": 16, "avg_entry_price": 154.47, "current_price": 156.0},
    ])
    submit_fn = _fake_submit_factory()
    state_path = tmp_path / "stop_state.json"

    def _broker_orders(symbol, status="open"):
        assert symbol == "XOP"
        return pd.DataFrame([
            {"order_id": "order-existing-1", "symbol": "XOP", "type": "stop",
             "side": "sell", "status": "open", "stop_price": 148.5, "qty": 16.0},
        ])

    intents = stop_manager.reconcile_stops(
        positions, _Limits(), entry_context={},
        submit_protective_stop_fn=submit_fn,
        replace_protective_stop_fn=_fake_replace_factory(),
        get_orders_for_symbol_fn=_broker_orders,
        stop_state_path=state_path,
    )

    assert intents[0]["action"] == "stop_adopted"
    assert intents[0]["order_id"] == "order-existing-1"
    assert submit_fn.calls == []  # NO duplicate stop submitted

    saved = load_stop_states(path=state_path)
    assert saved["XOP"].stop_order_id == "order-existing-1"
    assert saved["XOP"].current_stop_price == pytest.approx(148.5)


def test_no_broker_stop_falls_through_to_normal_attach(tmp_path, monkeypatch):
    """The common case: no local state AND no broker stop -> genuinely
    fresh position, proceeds to the normal attach path."""
    monkeypatch.setattr(stop_manager, "_resolve_atr_for_symbol", lambda sym, tf: 3.0)
    positions = _positions_df([
        {"symbol": "XOP", "side": "long", "qty": 16, "avg_entry_price": 154.47, "current_price": 154.47},
    ])
    submit_fn = _fake_submit_factory()
    state_path = tmp_path / "stop_state.json"

    def _no_broker_orders(symbol, status="open"):
        return pd.DataFrame()

    intents = stop_manager.reconcile_stops(
        positions, _Limits(), entry_context={},
        submit_protective_stop_fn=submit_fn,
        replace_protective_stop_fn=_fake_replace_factory(),
        get_orders_for_symbol_fn=_no_broker_orders,
        stop_state_path=state_path,
    )

    assert intents[0]["action"] == "stop_attached"
    assert len(submit_fn.calls) == 1


def test_broker_orders_of_wrong_type_are_ignored(tmp_path, monkeypatch):
    """A resting LIMIT or MARKET order (not a stop) on the symbol must not
    be mistaken for an existing protective stop."""
    monkeypatch.setattr(stop_manager, "_resolve_atr_for_symbol", lambda sym, tf: 3.0)
    positions = _positions_df([
        {"symbol": "XOP", "side": "long", "qty": 16, "avg_entry_price": 154.47, "current_price": 154.47},
    ])
    submit_fn = _fake_submit_factory()
    state_path = tmp_path / "stop_state.json"

    def _non_stop_orders(symbol, status="open"):
        return pd.DataFrame([
            {"order_id": "order-limit-1", "symbol": "XOP", "type": "limit",
             "side": "sell", "status": "open", "stop_price": None, "qty": 16.0},
        ])

    intents = stop_manager.reconcile_stops(
        positions, _Limits(), entry_context={},
        submit_protective_stop_fn=submit_fn,
        replace_protective_stop_fn=_fake_replace_factory(),
        get_orders_for_symbol_fn=_non_stop_orders,
        stop_state_path=state_path,
    )

    assert intents[0]["action"] == "stop_attached"  # proceeded normally
    assert len(submit_fn.calls) == 1


def test_broker_check_failure_falls_through_gracefully(tmp_path, monkeypatch):
    """If the broker-orders check itself fails (network blip), the scan
    must not crash -- it falls through to the normal attach path, same as
    if no broker stop existed."""
    monkeypatch.setattr(stop_manager, "_resolve_atr_for_symbol", lambda sym, tf: 3.0)
    positions = _positions_df([
        {"symbol": "XOP", "side": "long", "qty": 16, "avg_entry_price": 154.47, "current_price": 154.47},
    ])
    submit_fn = _fake_submit_factory()
    state_path = tmp_path / "stop_state.json"

    def _raising_check(symbol, status="open"):
        raise RuntimeError("network blip")

    intents = stop_manager.reconcile_stops(
        positions, _Limits(), entry_context={},
        submit_protective_stop_fn=submit_fn,
        replace_protective_stop_fn=_fake_replace_factory(),
        get_orders_for_symbol_fn=_raising_check,
        stop_state_path=state_path,
    )

    assert intents[0]["action"] == "stop_attached"
    assert len(submit_fn.calls) == 1


def test_dry_run_adopts_without_persisting(tmp_path):
    positions = _positions_df([
        {"symbol": "XOP", "side": "long", "qty": 16, "avg_entry_price": 154.47, "current_price": 156.0},
    ])

    def _broker_orders(symbol, status="open"):
        return pd.DataFrame([
            {"order_id": "order-existing-1", "symbol": "XOP", "type": "stop",
             "side": "sell", "status": "open", "stop_price": 148.5, "qty": 16.0},
        ])

    state_path = tmp_path / "stop_state.json"
    intents = stop_manager.reconcile_stops(
        positions, _Limits(), dry_run=True, entry_context={},
        submit_protective_stop_fn=_fake_submit_factory(),
        replace_protective_stop_fn=_fake_replace_factory(),
        get_orders_for_symbol_fn=_broker_orders,
        stop_state_path=state_path,
    )

    assert intents[0]["action"] == "stop_adopted"
    assert load_stop_states(path=state_path) == {}  # nothing persisted in dry-run

"""Tests for price.stop_manager.reconcile_stops: the orchestration layer
that attaches, ratchets, and tears down REAL broker-side protective stops.

ATR resolution and the broker calls (submit/replace) are all injected or
monkeypatched, so these tests run with no network, no warehouse, no
broker credentials.
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import price.stop_manager as stop_manager  # noqa: E402
from price.stops import load_stop_states, new_stop_state, save_stop_states  # noqa: E402


class _Limits:
    stop_atr_multiple = 2.0
    trail_atr_multiple = 3.0
    breakeven_trigger_r = 1.0
    target_leverage_multiple = 1.0


class _LeveredLimits:
    stop_atr_multiple = 2.0
    trail_atr_multiple = 3.0
    breakeven_trigger_r = 1.0
    target_leverage_multiple = 2.0


def _positions_df(rows):
    return pd.DataFrame(rows)


def _fake_submit_factory(order_id="order-1", status="accepted"):
    calls = []

    def _fn(symbol, qty, stop_price, side):
        calls.append((symbol, qty, stop_price, side))
        return {"order_id": order_id, "status": status}
    _fn.calls = calls
    return _fn


def _fake_replace_factory(status="replaced"):
    calls = []

    def _fn(order_id, new_stop_price):
        calls.append((order_id, new_stop_price))
        return {"order_id": order_id, "status": status}
    _fn.calls = calls
    return _fn


def _fake_no_broker_orders(symbol, status="open"):
    """Explicit 'the broker has no resting orders for this symbol' stand-in
    for get_orders_for_symbol_fn, used by every 'fresh position' test so
    they exercise the real stop_adopted-vs-fresh-attach branch instead of
    incidentally relying on the real (credentials-requiring) default
    raising and being silently caught."""
    return pd.DataFrame()


# ---------------------------------------------------------------------------
# Fresh position -> attach initial stop
# ---------------------------------------------------------------------------

def test_attaches_initial_stop_for_fresh_position(tmp_path, monkeypatch):
    monkeypatch.setattr(stop_manager, "_resolve_atr_for_symbol", lambda sym, tf: 3.0)
    submit_fn = _fake_submit_factory(order_id="order-42")
    replace_fn = _fake_replace_factory()

    positions = _positions_df([
        {"symbol": "XOP", "side": "long", "qty": 16, "avg_entry_price": 154.47, "current_price": 154.47},
    ])
    state_path = tmp_path / "stop_state.json"
    journal_path = tmp_path / "stopout_journal.json"

    intents = stop_manager.reconcile_stops(
        positions, _Limits(), entry_context={}, submit_protective_stop_fn=submit_fn,
        replace_protective_stop_fn=replace_fn,
        stop_state_path=state_path, stopout_journal_path=journal_path,
        get_orders_for_symbol_fn=_fake_no_broker_orders,
    )

    assert len(intents) == 1
    assert intents[0]["action"] == "stop_attached"
    assert submit_fn.calls == [("XOP", 16.0, pytest.approx(154.47 - 2 * 3.0), "long")]

    saved = load_stop_states(path=state_path)
    assert "XOP" in saved
    assert saved["XOP"].stop_order_id == "order-42"
    assert saved["XOP"].current_stop_price == pytest.approx(154.47 - 6.0)


def test_dry_run_computes_but_does_not_place_orders_or_persist(tmp_path, monkeypatch):
    monkeypatch.setattr(stop_manager, "_resolve_atr_for_symbol", lambda sym, tf: 3.0)
    submit_fn = _fake_submit_factory()
    positions = _positions_df([
        {"symbol": "XOP", "side": "long", "qty": 16, "avg_entry_price": 154.47, "current_price": 154.47},
    ])
    state_path = tmp_path / "stop_state.json"

    intents = stop_manager.reconcile_stops(
        positions, _Limits(), entry_context={}, dry_run=True, submit_protective_stop_fn=submit_fn,
        replace_protective_stop_fn=_fake_replace_factory(),
        stop_state_path=state_path,
        get_orders_for_symbol_fn=_fake_no_broker_orders,
    )

    assert intents[0]["action"] == "would_attach_stop"
    assert submit_fn.calls == []
    assert load_stop_states(path=state_path) == {}


def test_no_atr_defers_stop_attach_without_error(tmp_path, monkeypatch):
    monkeypatch.setattr(stop_manager, "_resolve_atr_for_symbol", lambda sym, tf: None)
    positions = _positions_df([
        {"symbol": "THIN", "side": "long", "qty": 5, "avg_entry_price": 10.0, "current_price": 10.0},
    ])
    intents = stop_manager.reconcile_stops(
        positions, _Limits(), entry_context={},
        submit_protective_stop_fn=_fake_submit_factory(),
        replace_protective_stop_fn=_fake_replace_factory(),
        stop_state_path=tmp_path / "stop_state.json",
        get_orders_for_symbol_fn=_fake_no_broker_orders,
    )
    assert intents[0]["action"] == "stop_pending"


def test_submit_failure_is_reported_not_raised(tmp_path, monkeypatch):
    monkeypatch.setattr(stop_manager, "_resolve_atr_for_symbol", lambda sym, tf: 3.0)

    def _fn(symbol, qty, stop_price, side):
        return {"order_id": None, "status": "rejected", "error": "insufficient buying power"}

    positions = _positions_df([
        {"symbol": "XOP", "side": "long", "qty": 16, "avg_entry_price": 154.47, "current_price": 154.47},
    ])
    intents = stop_manager.reconcile_stops(
        positions, _Limits(), entry_context={}, submit_protective_stop_fn=_fn,
        replace_protective_stop_fn=_fake_replace_factory(),
        stop_state_path=tmp_path / "stop_state.json",
        get_orders_for_symbol_fn=_fake_no_broker_orders,
    )
    assert intents[0]["action"] == "stop_attach_failed"
    assert "insufficient buying power" in intents[0]["reason"]


# ---------------------------------------------------------------------------
# Existing tracked stop -> ratchet
# ---------------------------------------------------------------------------

def test_ratchets_to_breakeven_and_replaces_broker_order(tmp_path, monkeypatch):
    state_path = tmp_path / "stop_state.json"
    existing = new_stop_state("XOP", "long", qty=16, entry_price=100.0, atr=3.0,
                               stop_order_id="order-1")  # R=6, stop=94
    save_stop_states({"XOP": existing}, path=state_path)

    monkeypatch.setattr(stop_manager, "_resolve_atr_for_symbol", lambda sym, tf: None)
    replace_fn = _fake_replace_factory()

    positions = _positions_df([
        {"symbol": "XOP", "side": "long", "qty": 16, "avg_entry_price": 100.0, "current_price": 106.5},  # +1.08R
    ])
    intents = stop_manager.reconcile_stops(
        positions, _Limits(), entry_context={},
        submit_protective_stop_fn=_fake_submit_factory(),
        replace_protective_stop_fn=replace_fn,
        stop_state_path=state_path,
    )

    assert intents[0]["action"] == "stop_ratcheted"
    assert intents[0]["new_stop_price"] == pytest.approx(100.0)
    assert replace_fn.calls == [("order-1", pytest.approx(100.0))]

    saved = load_stop_states(path=state_path)
    assert saved["XOP"].current_stop_price == pytest.approx(100.0)
    assert saved["XOP"].stage == "breakeven"


def test_unchanged_stop_below_breakeven_makes_no_broker_call(tmp_path, monkeypatch):
    state_path = tmp_path / "stop_state.json"
    existing = new_stop_state("XOP", "long", qty=16, entry_price=100.0, atr=3.0,
                               stop_order_id="order-1")  # R=6, stop=94
    save_stop_states({"XOP": existing}, path=state_path)
    monkeypatch.setattr(stop_manager, "_resolve_atr_for_symbol", lambda sym, tf: 3.0)
    replace_fn = _fake_replace_factory()

    positions = _positions_df([
        {"symbol": "XOP", "side": "long", "qty": 16, "avg_entry_price": 100.0, "current_price": 102.0},  # +0.33R
    ])
    intents = stop_manager.reconcile_stops(
        positions, _Limits(), entry_context={},
        submit_protective_stop_fn=_fake_submit_factory(),
        replace_protective_stop_fn=replace_fn,
        stop_state_path=state_path,
    )

    assert intents[0]["action"] == "stop_unchanged"
    assert replace_fn.calls == []


def test_ratchet_failure_is_reported_not_raised(tmp_path, monkeypatch):
    state_path = tmp_path / "stop_state.json"
    existing = new_stop_state("XOP", "long", qty=16, entry_price=100.0, atr=3.0,
                               stop_order_id="order-1")
    save_stop_states({"XOP": existing}, path=state_path)
    monkeypatch.setattr(stop_manager, "_resolve_atr_for_symbol", lambda sym, tf: None)

    def _failing_replace(order_id, new_stop_price):
        return {"order_id": None, "status": "rejected", "error": "order already filled"}

    positions = _positions_df([
        {"symbol": "XOP", "side": "long", "qty": 16, "avg_entry_price": 100.0, "current_price": 106.5},
    ])
    intents = stop_manager.reconcile_stops(
        positions, _Limits(), entry_context={},
        submit_protective_stop_fn=_fake_submit_factory(),
        replace_protective_stop_fn=_failing_replace,
        stop_state_path=state_path,
    )
    assert intents[0]["action"] == "stop_ratchet_failed"


def test_missing_order_id_resubmits_instead_of_replacing(tmp_path, monkeypatch):
    """Legacy StopState with no stop_order_id (e.g. pre-feature data) ->
    a fresh stop order is submitted rather than crashing on replace."""
    state_path = tmp_path / "stop_state.json"
    existing = new_stop_state("XOP", "long", qty=16, entry_price=100.0, atr=3.0,
                               stop_order_id=None)
    save_stop_states({"XOP": existing}, path=state_path)
    monkeypatch.setattr(stop_manager, "_resolve_atr_for_symbol", lambda sym, tf: None)
    submit_fn = _fake_submit_factory(order_id="order-fresh")

    positions = _positions_df([
        {"symbol": "XOP", "side": "long", "qty": 16, "avg_entry_price": 100.0, "current_price": 106.5},
    ])
    intents = stop_manager.reconcile_stops(
        positions, _Limits(), entry_context={}, submit_protective_stop_fn=submit_fn,
        replace_protective_stop_fn=_fake_replace_factory(),
        stop_state_path=state_path,
    )
    assert intents[0]["action"] == "stop_ratcheted"
    assert intents[0]["order_id"] == "order-fresh"
    assert submit_fn.calls == [("XOP", 16.0, pytest.approx(100.0), "long")]


# ---------------------------------------------------------------------------
# Position closed elsewhere -> reconcile bookkeeping
# ---------------------------------------------------------------------------

def test_tracked_stop_cleared_when_position_no_longer_open(tmp_path):
    state_path = tmp_path / "stop_state.json"
    journal_path = tmp_path / "stopout_journal.json"
    existing = new_stop_state("XOP", "long", qty=16, entry_price=100.0, atr=3.0,
                               stop_order_id="order-1")
    save_stop_states({"XOP": existing}, path=state_path)

    intents = stop_manager.reconcile_stops(
        pd.DataFrame(), _Limits(), entry_context={},
        submit_protective_stop_fn=_fake_submit_factory(),
        replace_protective_stop_fn=_fake_replace_factory(),
        get_order_fill_info_fn=lambda order_id: {"status": "canceled"},
        append_synthetic_exit_fn=lambda row: None,
        stop_state_path=state_path, stopout_journal_path=journal_path,
    )

    assert intents[0]["action"] == "stop_state_cleared"
    assert load_stop_states(path=state_path) == {}

    from price.stops import stopout_count_today
    assert stopout_count_today("XOP", path=journal_path) == 1


def test_dry_run_does_not_clear_stop_state(tmp_path):
    state_path = tmp_path / "stop_state.json"
    existing = new_stop_state("XOP", "long", qty=16, entry_price=100.0, atr=3.0,
                               stop_order_id="order-1")
    save_stop_states({"XOP": existing}, path=state_path)

    intents = stop_manager.reconcile_stops(
        pd.DataFrame(), _Limits(), entry_context={}, dry_run=True,
        submit_protective_stop_fn=_fake_submit_factory(),
        replace_protective_stop_fn=_fake_replace_factory(),
        stop_state_path=state_path,
    )
    assert intents[0]["action"] == "stop_state_cleared"
    assert "XOP" in load_stop_states(path=state_path)  # untouched in dry-run


# ---------------------------------------------------------------------------
# Malformed position rows never crash the scan
# ---------------------------------------------------------------------------

def test_malformed_position_row_reported_not_raised(tmp_path):
    positions = _positions_df([
        {"symbol": "BAD", "side": "long", "qty": "not-a-number", "avg_entry_price": 10.0, "current_price": 10.0},
    ])
    intents = stop_manager.reconcile_stops(
        positions, _Limits(), entry_context={},
        submit_protective_stop_fn=_fake_submit_factory(),
        replace_protective_stop_fn=_fake_replace_factory(),
        stop_state_path=tmp_path / "stop_state.json",
        get_orders_for_symbol_fn=_fake_no_broker_orders,
    )
    assert intents[0]["action"] == "error"


# ---------------------------------------------------------------------------
# Leverage safety rule: force-close unprotected positions when
# target_leverage_multiple > 1.0, instead of the default retry-next-scan.
# ---------------------------------------------------------------------------

def _fake_close_factory(order_id="close-order-1", status="accepted"):
    calls = []

    def _fn(symbol):
        calls.append(symbol)
        return {"order_id": order_id, "status": status}
    _fn.calls = calls
    return _fn


def test_no_atr_force_closes_under_leverage_instead_of_retrying(tmp_path):
    positions = _positions_df([
        {"symbol": "THIN", "side": "long", "qty": 5, "avg_entry_price": 10.0, "current_price": 10.0},
    ])
    close_fn = _fake_close_factory()
    intents = stop_manager.reconcile_stops(
        positions, _LeveredLimits(), entry_context={},
        submit_protective_stop_fn=_fake_submit_factory(),
        replace_protective_stop_fn=_fake_replace_factory(),
        close_position_fn=close_fn,
        stop_state_path=tmp_path / "stop_state.json",
        get_orders_for_symbol_fn=_fake_no_broker_orders,
    )
    # No ATR resolver monkeypatched here -> _resolve_atr_for_symbol returns
    # None for a nonexistent warehouse symbol, exercising the real no-ATR path.
    assert intents[0]["action"] == "force_closed_unprotected"
    assert close_fn.calls == ["THIN"]


def test_no_atr_still_retries_at_default_1x_leverage(tmp_path, monkeypatch):
    monkeypatch.setattr(stop_manager, "_resolve_atr_for_symbol", lambda sym, tf: None)
    positions = _positions_df([
        {"symbol": "THIN", "side": "long", "qty": 5, "avg_entry_price": 10.0, "current_price": 10.0},
    ])
    close_fn = _fake_close_factory()
    intents = stop_manager.reconcile_stops(
        positions, _Limits(), entry_context={},  # target_leverage_multiple = 1.0
        submit_protective_stop_fn=_fake_submit_factory(),
        replace_protective_stop_fn=_fake_replace_factory(),
        close_position_fn=close_fn,
        stop_state_path=tmp_path / "stop_state.json",
        get_orders_for_symbol_fn=_fake_no_broker_orders,
    )
    assert intents[0]["action"] == "stop_pending"
    assert close_fn.calls == []


def test_broker_rejection_force_closes_under_leverage(tmp_path, monkeypatch):
    monkeypatch.setattr(stop_manager, "_resolve_atr_for_symbol", lambda sym, tf: 3.0)
    close_fn = _fake_close_factory()

    def _rejecting_submit(symbol, qty, stop_price, side):
        return {"order_id": None, "status": "rejected", "error": "asset not shortable"}

    positions = _positions_df([
        {"symbol": "XOP", "side": "long", "qty": 16, "avg_entry_price": 154.47, "current_price": 154.47},
    ])
    intents = stop_manager.reconcile_stops(
        positions, _LeveredLimits(), entry_context={},
        submit_protective_stop_fn=_rejecting_submit,
        replace_protective_stop_fn=_fake_replace_factory(),
        close_position_fn=close_fn,
        stop_state_path=tmp_path / "stop_state.json",
        get_orders_for_symbol_fn=_fake_no_broker_orders,
    )
    assert intents[0]["action"] == "force_closed_unprotected"
    assert "asset not shortable" in intents[0]["reason"]
    assert close_fn.calls == ["XOP"]


def test_broker_rejection_still_just_reports_at_default_1x_leverage(tmp_path, monkeypatch):
    monkeypatch.setattr(stop_manager, "_resolve_atr_for_symbol", lambda sym, tf: 3.0)
    close_fn = _fake_close_factory()

    def _rejecting_submit(symbol, qty, stop_price, side):
        return {"order_id": None, "status": "rejected", "error": "insufficient buying power"}

    positions = _positions_df([
        {"symbol": "XOP", "side": "long", "qty": 16, "avg_entry_price": 154.47, "current_price": 154.47},
    ])
    intents = stop_manager.reconcile_stops(
        positions, _Limits(), entry_context={},  # 1.0x
        submit_protective_stop_fn=_rejecting_submit,
        replace_protective_stop_fn=_fake_replace_factory(),
        close_position_fn=close_fn,
        stop_state_path=tmp_path / "stop_state.json",
        get_orders_for_symbol_fn=_fake_no_broker_orders,
    )
    assert intents[0]["action"] == "stop_attach_failed"
    assert close_fn.calls == []


def test_dry_run_never_force_closes_even_under_leverage(tmp_path, monkeypatch):
    monkeypatch.setattr(stop_manager, "_resolve_atr_for_symbol", lambda sym, tf: None)
    close_fn = _fake_close_factory()
    positions = _positions_df([
        {"symbol": "THIN", "side": "long", "qty": 5, "avg_entry_price": 10.0, "current_price": 10.0},
    ])
    intents = stop_manager.reconcile_stops(
        positions, _LeveredLimits(), dry_run=True, entry_context={},
        submit_protective_stop_fn=_fake_submit_factory(),
        replace_protective_stop_fn=_fake_replace_factory(),
        close_position_fn=close_fn,
        stop_state_path=tmp_path / "stop_state.json",
        get_orders_for_symbol_fn=_fake_no_broker_orders,
    )
    assert intents[0]["action"] == "stop_pending"
    assert "dry run" in intents[0]["reason"]
    assert close_fn.calls == []

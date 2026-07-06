"""Tests for the CRITICAL P&L kill-switch fix: when a resting protective
stop fires AUTONOMOUSLY at the broker (no submit_exit/close_position call
happens for it -- Alpaca executes the closing trade on its own), the fill
must be journaled with real qty/entry/exit price so:

  - position_manager.get_today_realized_pnl (the account-level daily-loss
    kill switch) actually sees the loss.
  - attribution.reconstruct_round_trips can build a correct round-trip.

Before this fix, NEITHER consumer ever saw a real stop-out: the kill
switch computed exactly $0.00 of realized P&L for every close driven by
an autonomous broker-side fill, forever, regardless of the actual loss.

Also covers the disambiguation logic: a position closed via
close_position (state-break/horizon exit, which cancels the stop order
first) must NOT be double-journaled here -- close_position already wrote
the correct exit row.
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import price.stop_manager as stop_manager  # noqa: E402
from price.stop_manager import _journal_autonomous_stopout  # noqa: E402
from price.stops import new_stop_state, save_stop_states  # noqa: E402


class _Limits:
    stop_atr_multiple = 2.0
    trail_atr_multiple = 3.0
    breakeven_trigger_r = 1.0
    target_leverage_multiple = 1.0


def _fake_submit_factory(order_id="order-1", status="accepted"):
    def _fn(symbol, qty, stop_price, side):
        return {"order_id": order_id, "status": status}
    return _fn


def _fake_replace_factory(status="replaced"):
    def _fn(order_id, new_stop_price):
        return {"order_id": order_id, "status": status}
    return _fn


# ---------------------------------------------------------------------------
# _journal_autonomous_stopout (unit-level, isolated)
# ---------------------------------------------------------------------------

def test_journals_when_stop_order_actually_filled():
    state = new_stop_state("XOP", "long", qty=16, entry_price=100.0, atr=2.0,
                            stop_order_id="order-1")
    journaled = []

    def _fill_info(order_id):
        assert order_id == "order-1"
        return {"status": "filled", "filled_qty": 16.0, "filled_avg_price": 92.5,
                "filled_at": "2026-07-06T12:00:00Z"}

    def _append(row):
        journaled.append(row)

    result = _journal_autonomous_stopout("XOP", state, _fill_info, _append)

    assert result is not None
    assert len(journaled) == 1
    row = journaled[0]
    assert row["symbol"] == "XOP"
    assert row["qty"] == 16.0
    assert row["avg_entry_price"] == pytest.approx(100.0)
    assert row["current_price"] == pytest.approx(92.5)  # the REAL fill price
    assert row["status"] == "filled"


def test_does_not_journal_when_order_was_canceled_not_filled():
    """A canceled stop order (e.g. close_position canceled it before doing
    its own state-break/horizon exit) must NOT be double-journaled here."""
    state = new_stop_state("XLF", "long", qty=10, entry_price=58.0, atr=1.0,
                            stop_order_id="order-2")
    journaled = []

    def _fill_info(order_id):
        return {"status": "canceled"}

    def _append(row):
        journaled.append(row)

    result = _journal_autonomous_stopout("XLF", state, _fill_info, _append)

    assert result is None
    assert journaled == []


def test_does_not_journal_when_no_stop_order_id_tracked():
    state = new_stop_state("XLF", "long", qty=10, entry_price=58.0, atr=1.0,
                            stop_order_id=None)
    journaled = []
    calls = []

    def _fill_info(order_id):
        calls.append(order_id)
        return {"status": "filled", "filled_avg_price": 55.0}

    result = _journal_autonomous_stopout("XLF", state, _fill_info, lambda row: journaled.append(row))

    assert result is None
    assert calls == []  # never even attempted the fetch
    assert journaled == []


def test_does_not_journal_on_fetch_error():
    state = new_stop_state("XLF", "long", qty=10, entry_price=58.0, atr=1.0,
                            stop_order_id="order-3")
    journaled = []

    def _fill_info(order_id):
        return {"error": "network timeout"}

    result = _journal_autonomous_stopout("XLF", state, _fill_info, lambda row: journaled.append(row))

    assert result is None
    assert journaled == []


def test_does_not_journal_when_filled_but_no_price_available():
    """Defensive: 'filled' status but a missing filled_avg_price (should
    not happen per the Alpaca model, but must not crash or journal junk)."""
    state = new_stop_state("XLF", "long", qty=10, entry_price=58.0, atr=1.0,
                            stop_order_id="order-4")
    journaled = []

    def _fill_info(order_id):
        return {"status": "filled", "filled_avg_price": None}

    result = _journal_autonomous_stopout("XLF", state, _fill_info, lambda row: journaled.append(row))

    assert result is None
    assert journaled == []


def test_falls_back_to_tracked_qty_when_filled_qty_missing():
    state = new_stop_state("XOP", "short", qty=8, entry_price=50.0, atr=1.0,
                            stop_order_id="order-5")
    journaled = []

    def _fill_info(order_id):
        return {"status": "filled", "filled_avg_price": 53.0}  # no filled_qty

    _journal_autonomous_stopout("XOP", state, _fill_info, lambda row: journaled.append(row))

    assert journaled[0]["qty"] == pytest.approx(8.0)  # abs(state.qty) fallback


# ---------------------------------------------------------------------------
# Full reconcile_stops integration: this is what actually fixes the kill
# switch / attribution blindness end to end.
# ---------------------------------------------------------------------------

def test_reconcile_stops_journals_real_pnl_for_autonomous_stopout(tmp_path):
    state_path = tmp_path / "stop_state.json"
    journal_path = tmp_path / "stopout_journal.json"
    existing = new_stop_state("XOP", "long", qty=16, entry_price=100.0, atr=2.0,
                               stop_order_id="order-1")  # R=4, stop=96
    save_stop_states({"XOP": existing}, path=state_path)

    journaled = []

    def _fill_info(order_id):
        return {"status": "filled", "filled_qty": 16.0, "filled_avg_price": 95.90,
                "filled_at": "2026-07-06T14:00:00Z"}

    # Position no longer appears in open_positions -> vanished (stopped out).
    intents = stop_manager.reconcile_stops(
        pd.DataFrame(), _Limits(), entry_context={},
        submit_protective_stop_fn=_fake_submit_factory(),
        replace_protective_stop_fn=_fake_replace_factory(),
        get_order_fill_info_fn=_fill_info,
        append_synthetic_exit_fn=lambda row: journaled.append(row),
        stop_state_path=state_path, stopout_journal_path=journal_path,
    )

    assert intents[0]["action"] == "stop_state_cleared"
    assert intents[0]["autonomous_fill_journaled"] is True
    assert intents[0]["fill_price"] == pytest.approx(95.90)
    assert len(journaled) == 1
    assert journaled[0]["current_price"] == pytest.approx(95.90)
    assert journaled[0]["avg_entry_price"] == pytest.approx(100.0)
    assert journaled[0]["qty"] == 16.0

    # This is the actual bug fix, made concrete: get_today_realized_pnl-style
    # arithmetic on this row now correctly computes the REAL loss.
    implied_pnl = (journaled[0]["current_price"] - journaled[0]["avg_entry_price"]) * journaled[0]["qty"]
    assert implied_pnl == pytest.approx((95.90 - 100.0) * 16.0)
    assert implied_pnl < 0  # a real, nonzero loss -- not the previous silent $0.00

    from price.stops import stopout_count_today
    assert stopout_count_today("XOP", path=journal_path) == 1


def test_reconcile_stops_does_not_journal_when_stop_was_canceled_by_other_exit(tmp_path):
    """The position closed via a DIFFERENT exit policy (state-break/
    horizon), which cancels the resting stop first. reconcile_stops must
    NOT also journal a synthetic exit here -- close_position already did."""
    state_path = tmp_path / "stop_state.json"
    journal_path = tmp_path / "stopout_journal.json"
    existing = new_stop_state("XLF", "long", qty=10, entry_price=58.0, atr=1.0,
                               stop_order_id="order-2")
    save_stop_states({"XLF": existing}, path=state_path)

    journaled = []

    def _fill_info(order_id):
        return {"status": "canceled"}  # close_position canceled it, never filled

    intents = stop_manager.reconcile_stops(
        pd.DataFrame(), _Limits(), entry_context={},
        submit_protective_stop_fn=_fake_submit_factory(),
        replace_protective_stop_fn=_fake_replace_factory(),
        get_order_fill_info_fn=_fill_info,
        append_synthetic_exit_fn=lambda row: journaled.append(row),
        stop_state_path=state_path, stopout_journal_path=journal_path,
    )

    assert intents[0]["autonomous_fill_journaled"] is False
    assert journaled == []


def test_reconcile_stops_dry_run_never_journals(tmp_path):
    state_path = tmp_path / "stop_state.json"
    existing = new_stop_state("XOP", "long", qty=16, entry_price=100.0, atr=2.0,
                               stop_order_id="order-1")
    save_stop_states({"XOP": existing}, path=state_path)

    journaled = []
    calls = []

    def _fill_info(order_id):
        calls.append(order_id)
        return {"status": "filled", "filled_avg_price": 95.0}

    intents = stop_manager.reconcile_stops(
        pd.DataFrame(), _Limits(), dry_run=True, entry_context={},
        submit_protective_stop_fn=_fake_submit_factory(),
        replace_protective_stop_fn=_fake_replace_factory(),
        get_order_fill_info_fn=_fill_info,
        append_synthetic_exit_fn=lambda row: journaled.append(row),
        stop_state_path=state_path,
    )

    assert intents[0]["autonomous_fill_journaled"] is False
    assert calls == []  # dry-run never even checks fill status
    assert journaled == []

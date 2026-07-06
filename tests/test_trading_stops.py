"""Tests for the broker-side protective-stop plumbing in price.trading.

trading.py always calls get_trading_client() internally, so these tests
monkeypatch price.trading.get_trading_client to return a small fake
client object -- no network, no real API credentials, no Alpaca
account touched.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import price.trading as trading  # noqa: E402


class _FakeOrder(SimpleNamespace):
    pass


class _FakeClient:
    """Records calls; returns canned responses; can be told to raise."""

    def __init__(self):
        self.submitted = []
        self.replaced = []
        self.canceled = []
        self.raise_on_submit = False
        self.raise_on_replace = False
        self._orders_by_symbol = {}

    def submit_order(self, order_data):
        if self.raise_on_submit:
            raise RuntimeError("submit failed")
        self.submitted.append(order_data)
        return _FakeOrder(
            id="order-123",
            status="accepted",
            submitted_at="2026-07-06T00:00:00Z",
        )

    def replace_order_by_id(self, order_id, order_data):
        if self.raise_on_replace:
            raise RuntimeError("replace failed")
        self.replaced.append((order_id, order_data))
        return _FakeOrder(id=str(order_id), status="replaced")

    def cancel_order_by_id(self, order_id):
        self.canceled.append(order_id)
        # Realistically reflect the cancellation in subsequent get_orders()
        # calls, so close_position's settle-wait loop (which polls until a
        # just-canceled order id no longer appears as open) resolves
        # immediately in these tests instead of exhausting real sleeps.
        for sym, orders in self._orders_by_symbol.items():
            self._orders_by_symbol[sym] = [o for o in orders if o.id != order_id]

    def get_orders(self, filter=None):
        sym = None
        if filter is not None and getattr(filter, "symbols", None):
            sym = filter.symbols[0]
        return self._orders_by_symbol.get(sym, [])


@pytest.fixture(autouse=True)
def _isolated_journal(tmp_path, monkeypatch):
    """Redirect the trade journal so these tests never touch localdata/."""
    monkeypatch.setattr(trading, "TRADE_JOURNAL_PATH", tmp_path / "trade_journal.csv")


def _patch_client(monkeypatch, fake_client):
    monkeypatch.setattr(trading, "get_trading_client", lambda: fake_client)


# ---------------------------------------------------------------------------
# submit_protective_stop
# ---------------------------------------------------------------------------

def test_submit_protective_stop_long_uses_sell_side(monkeypatch):
    fake = _FakeClient()
    _patch_client(monkeypatch, fake)

    result = trading.submit_protective_stop("XOP", qty=16, stop_price=148.5, position_side="long")

    assert result["status"] == "accepted"
    assert result["order_id"] == "order-123"
    assert result["side"] == "sell"
    assert result["stop_price"] == 148.5
    assert len(fake.submitted) == 1
    assert fake.submitted[0].side.value == "sell"
    assert fake.submitted[0].time_in_force.value == "gtc"


def test_submit_protective_stop_short_uses_buy_side(monkeypatch):
    fake = _FakeClient()
    _patch_client(monkeypatch, fake)

    result = trading.submit_protective_stop("TLT", qty=5, stop_price=95.0, position_side="short")

    assert result["side"] == "buy"
    assert fake.submitted[0].side.value == "buy"


def test_submit_protective_stop_uses_4_decimals_under_a_dollar(monkeypatch):
    """Alpaca requires <= 2 decimals when price >= $1.00, but allows/requires
    up to 4 decimals when price < $1.00 (sub-penny rejection is enforced at
    the $1 threshold, not universally at 2 decimals)."""
    fake = _FakeClient()
    _patch_client(monkeypatch, fake)

    result = trading.submit_protective_stop("PENNY", qty=100, stop_price=0.12345, position_side="long")

    assert result["stop_price"] == pytest.approx(0.1235)  # rounded to 4dp, not 2dp


def test_submit_protective_stop_uses_2_decimals_at_or_above_a_dollar(monkeypatch):
    fake = _FakeClient()
    _patch_client(monkeypatch, fake)

    result = trading.submit_protective_stop("XOP", qty=16, stop_price=148.5678, position_side="long")

    assert result["stop_price"] == pytest.approx(148.57)  # rounded to 2dp


def test_submit_protective_stop_never_raises_on_broker_error(monkeypatch):
    fake = _FakeClient()
    fake.raise_on_submit = True
    _patch_client(monkeypatch, fake)

    result = trading.submit_protective_stop("XOP", qty=16, stop_price=148.5, position_side="long")

    assert result["status"] == "rejected"
    assert result["order_id"] is None
    assert "error" in result


def test_submit_protective_stop_journals_the_action(monkeypatch, tmp_path):
    fake = _FakeClient()
    _patch_client(monkeypatch, fake)

    trading.submit_protective_stop("XOP", qty=16, stop_price=148.5, position_side="long")

    journal = pd.read_csv(trading.TRADE_JOURNAL_PATH)
    assert (journal["action"] == "protective_stop_submit").any()


# ---------------------------------------------------------------------------
# replace_protective_stop
# ---------------------------------------------------------------------------

def test_replace_protective_stop_keeps_same_order_id(monkeypatch):
    fake = _FakeClient()
    _patch_client(monkeypatch, fake)

    result = trading.replace_protective_stop("order-123", new_stop_price=151.0)

    assert result["status"] == "replaced"
    assert result["order_id"] == "order-123"
    assert result["stop_price"] == 151.0
    assert fake.replaced[0][0] == "order-123"


def test_replace_protective_stop_never_raises_on_broker_error(monkeypatch):
    fake = _FakeClient()
    fake.raise_on_replace = True
    _patch_client(monkeypatch, fake)

    result = trading.replace_protective_stop("order-123", new_stop_price=151.0)

    assert result["status"] == "rejected"
    assert "error" in result


# ---------------------------------------------------------------------------
# cancel_order
# ---------------------------------------------------------------------------

def test_cancel_order_success(monkeypatch):
    fake = _FakeClient()
    _patch_client(monkeypatch, fake)

    result = trading.cancel_order("order-123")

    assert result["status"] == "cancel_requested"
    assert fake.canceled == ["order-123"]


def test_cancel_order_never_raises(monkeypatch):
    fake = _FakeClient()

    def _raise(order_id):
        raise RuntimeError("nope")
    fake.cancel_order_by_id = _raise
    _patch_client(monkeypatch, fake)

    result = trading.cancel_order("order-123")
    assert result["status"] == "cancel_failed"


# ---------------------------------------------------------------------------
# get_orders_for_symbol
# ---------------------------------------------------------------------------

def test_get_orders_for_symbol_empty(monkeypatch):
    fake = _FakeClient()
    _patch_client(monkeypatch, fake)
    df = trading.get_orders_for_symbol("XOP")
    assert df.empty


def test_get_orders_for_symbol_returns_rows(monkeypatch):
    fake = _FakeClient()
    fake._orders_by_symbol["XOP"] = [
        _FakeOrder(
            id="order-1", symbol="XOP", qty="16", side="sell", type="stop",
            status="open", stop_price="148.5", submitted_at="2026-07-06T00:00:00Z",
        )
    ]
    _patch_client(monkeypatch, fake)

    df = trading.get_orders_for_symbol("XOP")
    assert len(df) == 1
    assert df.iloc[0]["order_id"] == "order-1"
    assert df.iloc[0]["stop_price"] == 148.5


# ---------------------------------------------------------------------------
# close_position cancels resting orders first (no naked stop survives)
# ---------------------------------------------------------------------------

def test_close_position_cancels_open_orders_first(monkeypatch):
    fake = _FakeClient()
    fake._orders_by_symbol["XOP"] = [
        _FakeOrder(
            id="stop-order-1", symbol="XOP", qty="16", side="sell", type="stop",
            status="open", stop_price="148.5", submitted_at="2026-07-06T00:00:00Z",
        )
    ]
    fake.close_position = lambda sym: _FakeOrder(
        id="close-order-1", status="accepted", submitted_at="2026-07-06T00:00:00Z"
    )
    _patch_client(monkeypatch, fake)

    result = trading.close_position("XOP")

    assert fake.canceled == ["stop-order-1"]
    assert result["order_id"] == "close-order-1"


def test_close_position_skips_cancel_when_disabled(monkeypatch):
    fake = _FakeClient()
    fake._orders_by_symbol["XOP"] = [
        _FakeOrder(id="stop-order-1", symbol="XOP", qty="16", side="sell", type="stop",
                   status="open", stop_price="148.5", submitted_at="x")
    ]
    fake.close_position = lambda sym: _FakeOrder(
        id="close-order-1", status="accepted", submitted_at="2026-07-06T00:00:00Z"
    )
    _patch_client(monkeypatch, fake)

    trading.close_position("XOP", cancel_open_orders=False)

    assert fake.canceled == []


def test_close_position_cancel_failure_does_not_block_close(monkeypatch):
    fake = _FakeClient()

    def _raise_get_orders(filter=None):
        raise RuntimeError("network blip")
    fake.get_orders = _raise_get_orders
    fake.close_position = lambda sym: _FakeOrder(
        id="close-order-1", status="accepted", submitted_at="2026-07-06T00:00:00Z"
    )
    _patch_client(monkeypatch, fake)

    result = trading.close_position("XOP")
    assert result["order_id"] == "close-order-1"

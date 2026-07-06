"""Tests for the cancel-then-close race-condition fix in
price.trading.close_position.

Alpaca documents that cancelling a resting order and immediately closing
the position can fail with an insufficient-qty error, because shares can
still be considered reserved by the order pending cancellation. Before
this fix, close_position fired cancel_order and client.close_position
back-to-back with no wait, so hitting this race meant: the stop is
already gone, the close FAILS, and the position sits fully unprotected
until the next scan.

The fix polls get_orders_for_symbol(status='open') after requesting each
cancellation until the canceled order id(s) no longer appear there (or a
max-checks budget is exhausted), narrowing the race window before
attempting the close.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import price.trading as trading  # noqa: E402


class _FakeOrder(SimpleNamespace):
    pass


class _SettlingFakeClient:
    """Simulates an order that stays 'open' for N get_orders() polls after
    cancellation before finally disappearing (settling)."""

    def __init__(self, settle_after_polls=2):
        self.settle_after_polls = settle_after_polls
        self.poll_count = 0
        self.canceled = []
        self.close_called = False
        self.close_should_fail_if_not_settled = True

    def cancel_order_by_id(self, order_id):
        self.canceled.append(order_id)

    def get_orders(self, filter=None):
        sym = filter.symbols[0] if filter and getattr(filter, "symbols", None) else None
        if sym != "XOP":
            return []
        if self.poll_count < self.settle_after_polls:
            self.poll_count += 1
            return [_FakeOrder(
                id="stop-order-1", symbol="XOP", qty="16", side="sell", type="stop",
                status="open", stop_price="148.5", submitted_at="x",
            )]
        return []  # settled: no longer open

    def get_all_positions(self):
        return []

    def close_position(self, sym):
        self.close_called = True
        if self.close_should_fail_if_not_settled and self.poll_count < self.settle_after_polls:
            raise RuntimeError("insufficient qty available (shares reserved)")
        return _FakeOrder(id="close-order-1", status="accepted", submitted_at="x",
                           filled_avg_price="95.0")


@pytest.fixture(autouse=True)
def _isolated_journal(tmp_path, monkeypatch):
    monkeypatch.setattr(trading, "TRADE_JOURNAL_PATH", tmp_path / "trade_journal.csv")


def _patch_client(monkeypatch, fake_client):
    monkeypatch.setattr(trading, "get_trading_client", lambda: fake_client)


def test_close_waits_for_cancellation_to_settle_before_closing(monkeypatch):
    fake = _SettlingFakeClient(settle_after_polls=2)
    _patch_client(monkeypatch, fake)
    sleeps = []

    result = trading.close_position(
        "XOP", cancel_settle_max_checks=5, cancel_settle_sleep_seconds=0.01,
        sleep_fn=lambda s: sleeps.append(s),
    )

    assert fake.canceled == ["stop-order-1"]
    assert fake.close_called is True
    assert result["status"] == "accepted"  # the close succeeded because we waited
    assert len(sleeps) >= 1  # actually polled/waited before closing


def test_close_gives_up_after_max_checks_and_still_attempts_close(monkeypatch):
    """If the order never settles within the budget, close_position must
    still ATTEMPT the close (best-effort) rather than hanging forever or
    silently refusing to close."""
    fake = _SettlingFakeClient(settle_after_polls=100)  # never settles in time
    fake.close_should_fail_if_not_settled = False  # let the close succeed anyway
    _patch_client(monkeypatch, fake)
    sleeps = []

    result = trading.close_position(
        "XOP", cancel_settle_max_checks=3, cancel_settle_sleep_seconds=0.01,
        sleep_fn=lambda s: sleeps.append(s),
    )

    assert fake.close_called is True
    assert len(sleeps) == 3  # exhausted the budget, then proceeded anyway
    assert result["status"] == "accepted"


def test_close_no_open_orders_never_sleeps(monkeypatch):
    """The common case: nothing to cancel -> zero polling, zero sleeping,
    close proceeds immediately."""
    class _NoOrdersClient:
        def get_orders(self, filter=None):
            return []

        def get_all_positions(self):
            return []

        def close_position(self, sym):
            return _FakeOrder(id="close-order-1", status="accepted", submitted_at="x")

    fake = _NoOrdersClient()
    _patch_client(monkeypatch, fake)
    sleeps = []

    trading.close_position("XOP", sleep_fn=lambda s: sleeps.append(s))

    assert sleeps == []


def test_close_race_failure_is_reported_not_silently_swallowed(monkeypatch):
    """If the close STILL fails even after the settle-wait (the race can
    still theoretically happen), the failure must surface as
    status='rejected' with an error -- never silently treated as success."""
    class _AlwaysFailsClient:
        def get_orders(self, filter=None):
            return []

        def get_all_positions(self):
            return []

        def close_position(self, sym):
            raise RuntimeError("insufficient qty available")

    fake = _AlwaysFailsClient()
    _patch_client(monkeypatch, fake)

    result = trading.close_position("XOP")
    assert result["status"] == "rejected"
    assert "insufficient qty" in result["error"]


def test_settle_wait_disabled_when_cancel_open_orders_false(monkeypatch):
    fake = _SettlingFakeClient(settle_after_polls=2)
    fake.close_should_fail_if_not_settled = False
    _patch_client(monkeypatch, fake)
    sleeps = []

    trading.close_position("XOP", cancel_open_orders=False, sleep_fn=lambda s: sleeps.append(s))

    assert fake.canceled == []  # cancellation skipped entirely
    assert sleeps == []

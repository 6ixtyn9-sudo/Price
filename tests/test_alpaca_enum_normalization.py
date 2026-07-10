"""Tests for the CRITICAL enum-normalization bug found during red-team
review: Alpaca's SDK enums (PositionSide, OrderSide, OrderStatus, OrderType)
are `str`-mixin enums where `str(x)` returns `"ClassName.MEMBER"`, NOT the
actual value (`"long"`, `"rejected"`, etc.) -- even though the object
itself equality-compares correctly against the plain string.

Every prior test in this codebase used hand-built plain Python strings
(`{"side": "long"}`) for position/order fixtures, which validated the
CODE'S OWN mental model instead of the real SDK's behaviour, and let two
real bugs ship silently:

  1. get_open_orders / get_orders_for_symbol stored str(o.side) ->
     "OrderSide.BUY" instead of "buy" -- any code comparing against the
     literal "buy"/"sell"/"long"/"short" would silently fail its match.
  2. submit_protective_stop / replace_protective_stop / close_position
     stored str(order.status) -> "OrderStatus.REJECTED" instead of
     "rejected" -- a genuinely REJECTED stop order (Alpaca returns HTTP
     200 with an ASYNC status="rejected" from the execution venue; this
     is documented Alpaca behaviour) was treated as SUCCESSFULLY
     SUBMITTED by every `result.get("status") == "rejected"` check in the
     codebase, silently leaving positions completely unprotected.

These tests use a FakeStrEnum that exactly reproduces the real SDK's
str-mixin quirk, so the bug class is provably caught by test fixtures
going forward, not just fixed by code review.
"""

import enum
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import price.trading as trading  # noqa: E402


class FakeStrEnum(str, enum.Enum):
    """Reproduces Alpaca's real str-mixin enum behaviour exactly:
    str(FakeStrEnum.LONG) == "FakeStrEnum.LONG" (NOT "long"), but
    FakeStrEnum.LONG == "long" and FakeStrEnum.LONG.value == "long"."""
    LONG = "long"
    SHORT = "short"
    BUY = "buy"
    SELL = "sell"
    REJECTED = "rejected"
    ACCEPTED = "accepted"
    FILLED = "filled"
    CANCELED = "canceled"
    STOP = "stop"


def test_fixture_actually_reproduces_the_real_sdk_quirk():
    """Sanity check on the test fixture itself: if this fails, the fixture
    no longer reproduces the bug class and every test below is worthless."""
    assert str(FakeStrEnum.LONG) == "FakeStrEnum.LONG"
    assert str(FakeStrEnum.LONG) != "long"
    assert FakeStrEnum.LONG == "long"
    assert FakeStrEnum.LONG.value == "long"


def test_enum_value_helper_extracts_correctly():
    assert trading._enum_value(FakeStrEnum.LONG) == "long"
    assert trading._enum_value(FakeStrEnum.REJECTED) == "rejected"
    assert trading._enum_value("plain_string") == "plain_string"
    assert trading._enum_value(None) is None


@pytest.fixture(autouse=True)
def _isolated_journal(tmp_path, monkeypatch):
    monkeypatch.setattr(trading, "TRADE_JOURNAL_PATH", tmp_path / "trade_journal.csv")


def _patch_client(monkeypatch, fake_client):
    monkeypatch.setattr(trading, "get_trading_client", lambda: fake_client)


# ---------------------------------------------------------------------------
# get_open_positions: side must normalize correctly
# ---------------------------------------------------------------------------

def test_get_open_positions_normalizes_side_enum(monkeypatch):
    class FakePosition(SimpleNamespace):
        pass

    fake_client = SimpleNamespace(get_all_positions=lambda: [
        FakePosition(symbol="XOP", qty="16", side=FakeStrEnum.LONG,
                     avg_entry_price="100.0", current_price="105.0",
                     unrealized_pl="80.0", unrealized_plpc="0.05", market_value="1680.0"),
    ])
    _patch_client(monkeypatch, fake_client)

    df = trading.get_open_positions()
    assert df.iloc[0]["side"] == "long"
    assert df.iloc[0]["side"] != "FakeStrEnum.LONG"


def test_get_open_positions_normalizes_short_side_enum(monkeypatch):
    """The specific real-world failure mode: a SHORT position's side must
    resolve to the literal string 'short', not silently become 'long'."""
    class FakePosition(SimpleNamespace):
        pass

    fake_client = SimpleNamespace(get_all_positions=lambda: [
        FakePosition(symbol="TLT", qty="10", side=FakeStrEnum.SHORT,
                     avg_entry_price="95.0", current_price="93.0",
                     unrealized_pl="20.0", unrealized_plpc="0.02", market_value="930.0"),
    ])
    _patch_client(monkeypatch, fake_client)

    df = trading.get_open_positions()
    side = df.iloc[0]["side"]
    assert side == "short"
    # The actual downstream check this bug broke:
    normalized = str(side or "long").lower()
    assert normalized == "short"
    assert normalized != "long"  # this WAS the silent-fallback bug


# ---------------------------------------------------------------------------
# get_open_orders / get_orders_for_symbol: side/type/status must normalize
# ---------------------------------------------------------------------------

def test_get_open_orders_normalizes_enums(monkeypatch):
    class FakeOrder(SimpleNamespace):
        pass

    fake_client = SimpleNamespace(get_orders=lambda: [
        FakeOrder(id="o1", symbol="XOP", qty="16", side=FakeStrEnum.BUY,
                  type=FakeStrEnum.STOP, status=FakeStrEnum.ACCEPTED,
                  submitted_at="2026-07-06", expires_at=None),
    ])
    _patch_client(monkeypatch, fake_client)

    df = trading.get_open_orders()
    assert df.iloc[0]["side"] == "buy"
    assert df.iloc[0]["type"] == "stop"
    assert df.iloc[0]["status"] == "accepted"


def test_get_orders_for_symbol_normalizes_enums(monkeypatch):
    class FakeOrder(SimpleNamespace):
        pass

    fake_client = SimpleNamespace(get_orders=lambda filter=None: [
        FakeOrder(id="o1", symbol="XOP", qty="16", side=FakeStrEnum.SELL,
                  type=FakeStrEnum.STOP, status=FakeStrEnum.ACCEPTED,
                  stop_price="148.5", submitted_at="2026-07-06"),
    ])
    _patch_client(monkeypatch, fake_client)

    df = trading.get_orders_for_symbol("XOP")
    assert df.iloc[0]["side"] == "sell"
    assert df.iloc[0]["status"] == "accepted"


# ---------------------------------------------------------------------------
# submit_protective_stop: the CRITICAL bug -- a REJECTED order (async,
# HTTP 200) must be correctly detected as rejected, not silently accepted.
# ---------------------------------------------------------------------------

def test_submit_protective_stop_detects_async_rejection(monkeypatch):
    """THE critical finding: Alpaca can return HTTP 200 with an order
    whose status is asynchronously 'rejected' by the execution venue.
    Before the fix, str(order.status) rendered this as the literal string
    "OrderStatus.REJECTED", which never equals "rejected", so a rejected
    stop order was treated as successfully attached -- the position was
    silently left completely unprotected."""
    class FakeOrder(SimpleNamespace):
        pass

    fake_client = SimpleNamespace(
        submit_order=lambda order_data: FakeOrder(
            id="o1", status=FakeStrEnum.REJECTED, submitted_at="2026-07-06",
        )
    )
    _patch_client(monkeypatch, fake_client)

    result = trading.submit_protective_stop("XOP", qty=16, stop_price=148.5, position_side="long")

    assert result["status"] == "rejected"
    assert "error" in result  # the new explicit error explanation


def test_submit_protective_stop_accepted_is_not_misdetected_as_rejected(monkeypatch):
    class FakeOrder(SimpleNamespace):
        pass

    fake_client = SimpleNamespace(
        submit_order=lambda order_data: FakeOrder(
            id="o1", status=FakeStrEnum.ACCEPTED, submitted_at="2026-07-06",
        )
    )
    _patch_client(monkeypatch, fake_client)

    result = trading.submit_protective_stop("XOP", qty=16, stop_price=148.5, position_side="long")

    assert result["status"] == "accepted"
    assert "error" not in result


def test_submit_protective_stop_side_normalizes_for_short_position(monkeypatch):
    class FakeOrder(SimpleNamespace):
        pass

    fake_client = SimpleNamespace(
        submit_order=lambda order_data: FakeOrder(
            id="o1", status=FakeStrEnum.ACCEPTED, submitted_at="2026-07-06",
        )
    )
    _patch_client(monkeypatch, fake_client)

    result = trading.submit_protective_stop("TLT", qty=10, stop_price=98.0, position_side="short")
    assert result["side"] == "buy"  # a short is protected by a BUY stop
    assert result["side"] != "OrderSide.BUY"


# ---------------------------------------------------------------------------
# replace_protective_stop: same async-rejection detection
# ---------------------------------------------------------------------------

def test_replace_protective_stop_detects_async_rejection(monkeypatch):
    class FakeOrder(SimpleNamespace):
        pass

    fake_client = SimpleNamespace(
        replace_order_by_id=lambda order_id, order_data: FakeOrder(
            id=order_id, status=FakeStrEnum.REJECTED,
        )
    )
    _patch_client(monkeypatch, fake_client)

    result = trading.replace_protective_stop("order-1", new_stop_price=151.0)

    assert result["status"] == "rejected"
    assert "error" in result
    assert "may still be resting" in result["error"]


# ---------------------------------------------------------------------------
# close_position: same async-rejection detection + qty/price snapshot fix
# ---------------------------------------------------------------------------

def test_close_position_detects_async_rejection(monkeypatch):
    class FakeOrder(SimpleNamespace):
        pass

    fake_client = SimpleNamespace(
        get_all_positions=lambda: [],
        get_orders=lambda filter=None: [],
        close_position=lambda sym: FakeOrder(
            id="o1", status=FakeStrEnum.REJECTED, submitted_at="2026-07-06",
        ),
    )
    _patch_client(monkeypatch, fake_client)

    result = trading.close_position("XOP", cancel_open_orders=False)

    assert result["status"] == "rejected"
    assert "STILL OPEN" in result["error"]


def test_close_position_snapshots_qty_and_entry_price_before_closing(monkeypatch):
    """THE OTHER critical finding: close_position used to journal an exit
    row with NO qty/avg_entry_price/current_price at all, making the
    account-level daily-loss kill switch permanently blind to every
    close_position-driven exit (P&L always computed as $0.00)."""
    class FakePosition(SimpleNamespace):
        pass

    class FakeOrder(SimpleNamespace):
        pass

    fake_client = SimpleNamespace(
        get_all_positions=lambda: [
            FakePosition(symbol="XOP", qty="16", side=FakeStrEnum.LONG,
                         avg_entry_price="100.0", current_price="103.5",
                         unrealized_pl="56.0", unrealized_plpc="0.035", market_value="1656.0"),
        ],
        get_orders=lambda filter=None: [],
        close_position=lambda sym: FakeOrder(
            id="o1", status=FakeStrEnum.ACCEPTED, submitted_at="2026-07-06",
            filled_avg_price="103.4",
        ),
    )
    _patch_client(monkeypatch, fake_client)

    result = trading.close_position("XOP", cancel_open_orders=False)

    assert result["qty"] == pytest.approx(16.0)
    assert result["avg_entry_price"] == pytest.approx(100.0)
    assert result["current_price"] == pytest.approx(103.4)  # prefers the real fill price

    # This is the fix, made concrete: the kill switch's arithmetic now
    # computes a REAL, correct P&L instead of always $0.00.
    implied_pnl = (result["current_price"] - result["avg_entry_price"]) * result["qty"]
    assert implied_pnl == pytest.approx((103.4 - 100.0) * 16.0)
    assert implied_pnl != 0.0


def test_close_position_falls_back_to_current_price_when_no_fill_price(monkeypatch):
    class FakePosition(SimpleNamespace):
        pass

    class FakeOrder(SimpleNamespace):
        pass

    fake_client = SimpleNamespace(
        get_all_positions=lambda: [
            FakePosition(symbol="XOP", qty="16", side=FakeStrEnum.LONG,
                         avg_entry_price="100.0", current_price="103.5",
                         unrealized_pl="56.0", unrealized_plpc="0.035", market_value="1656.0"),
        ],
        get_orders=lambda filter=None: [],
        close_position=lambda sym: FakeOrder(
            id="o1", status=FakeStrEnum.ACCEPTED, submitted_at="2026-07-06",
            filled_avg_price=None,
        ),
    )
    _patch_client(monkeypatch, fake_client)

    result = trading.close_position("XOP", cancel_open_orders=False)
    assert result["current_price"] == pytest.approx(103.5)  # pre-close snapshot fallback


def test_close_position_snapshot_failure_does_not_block_close(monkeypatch):
    class FakeOrder(SimpleNamespace):
        pass

    def _raise_positions():
        raise RuntimeError("network blip")

    fake_client = SimpleNamespace(
        get_all_positions=_raise_positions,
        get_orders=lambda filter=None: [],
        close_position=lambda sym: FakeOrder(
            id="o1", status=FakeStrEnum.ACCEPTED, submitted_at="2026-07-06",
            filled_avg_price="103.4",
        ),
    )
    _patch_client(monkeypatch, fake_client)

    result = trading.close_position("XOP", cancel_open_orders=False)
    assert result["status"] == "accepted"
    assert result["qty"] is None  # snapshot unavailable, but close still succeeded


# ---------------------------------------------------------------------------
# get_order_fill_info: the new helper backing autonomous stop-out journaling
# ---------------------------------------------------------------------------

def test_get_order_fill_info_normalizes_and_extracts_fill_data(monkeypatch):
    class FakeOrder(SimpleNamespace):
        pass

    fake_client = SimpleNamespace(
        get_order_by_id=lambda order_id: FakeOrder(
            id=order_id, symbol="xop", status=FakeStrEnum.FILLED, side=FakeStrEnum.SELL,
            filled_qty="16", filled_avg_price="95.9", filled_at="2026-07-06T14:00:00Z",
        )
    )
    _patch_client(monkeypatch, fake_client)

    info = trading.get_order_fill_info("order-1")
    assert info["status"] == "filled"
    assert info["side"] == "sell"
    assert info["symbol"] == "XOP"
    assert info["filled_qty"] == pytest.approx(16.0)
    assert info["filled_avg_price"] == pytest.approx(95.9)


def test_get_order_fill_info_never_raises(monkeypatch):
    def _raise(order_id):
        raise RuntimeError("order not found")
    fake_client = SimpleNamespace(get_order_by_id=_raise)
    _patch_client(monkeypatch, fake_client)

    info = trading.get_order_fill_info("nonexistent")
    assert "error" in info


def test_reconcile_trade_journal_is_idempotent_for_unchanged_state(tmp_path):
    """A repeated identical broker reconciliation must not rewrite the CSV."""
    path = tmp_path / "trade_journal.csv"
    pd.DataFrame([{
        "order_id": "order-1",
        "symbol": "XLE",
        "qty": 9.0,
        "side": "buy",
        "status": "accepted",
        "action": "entry",
        "timestamp_utc": "2026-07-10T00:00:00Z",
    }]).to_csv(path, index=False)

    def _fill_info(order_id):
        assert order_id == "order-1"
        return {
            "status": "canceled",
            "filled_qty": 0.0,
            "filled_avg_price": None,
            "filled_at": None,
        }

    trading.reconcile_trade_journal(path=path, get_order_fill_info_fn=_fill_info)
    first_bytes = path.read_bytes()
    first_timestamp = pd.read_csv(path).loc[0, "reconciled_at_utc"]

    trading.reconcile_trade_journal(path=path, get_order_fill_info_fn=_fill_info)
    second_bytes = path.read_bytes()
    second_timestamp = pd.read_csv(path).loc[0, "reconciled_at_utc"]

    assert second_bytes == first_bytes
    assert second_timestamp == first_timestamp


def test_reconcile_trade_journal_backfills_legacy_entry_metadata(tmp_path, monkeypatch):
    """Legacy fills recover timeframe/signal context by exact order_id."""
    journal_path = tmp_path / "trade_journal.csv"
    pd.DataFrame([{
        "order_id": "order-legacy",
        "symbol": "XOP",
        "qty": 16.0,
        "side": "buy",
        "status": "accepted",
        "action": "entry",
        "slice_label": "state_ext=stretched_down + state_slope=downtrend",
        "timestamp_utc": "2026-07-10T00:00:00Z",
    }]).to_csv(journal_path, index=False)
    pd.DataFrame([{
        "order_id": "order-legacy",
        "action": "enter",
        "symbol": "XOP",
        "slice_combination": "state_ext=stretched_down + state_slope=downtrend",
        "bar_ts_utc": "2026-07-02 04:00:00+00:00",
        "timeframe": "1d",
        "bin_mode": "insample",
    }]).to_csv(tmp_path / "paper_trade_log.csv", index=False)
    monkeypatch.setattr(trading, "DATA_DIR", tmp_path)

    trading.reconcile_trade_journal(
        path=journal_path,
        get_order_fill_info_fn=lambda order_id: {
            "status": "filled",
            "filled_qty": 16.0,
            "filled_avg_price": 154.47,
            "filled_at": "2026-07-06T13:30:57Z",
        },
    )
    row = pd.read_csv(journal_path).iloc[0]
    assert row["timeframe"] == "1d"
    assert row["entry_bar_ts"] == "2026-07-02 04:00:00+00:00"
    assert row["bin_mode"] == "insample"
    assert row["broker_status"] == "filled"


def test_reconcile_trade_journal_reports_unresolved_orders(tmp_path):
    """Broker lookup failures are observable so callers can fail closed."""
    path = tmp_path / "trade_journal.csv"
    pd.DataFrame([{
        "order_id": "order-timeout",
        "symbol": "XOP",
        "qty": 1.0,
        "side": "buy",
        "status": "accepted",
        "action": "entry",
        "timestamp_utc": "2026-07-10T00:00:00Z",
    }]).to_csv(path, index=False)
    health = {}

    trading.reconcile_trade_journal(
        path=path,
        get_order_fill_info_fn=lambda order_id: {"error": "broker timeout"},
        health_out=health,
    )

    assert health["ok"] is False
    assert health["total_order_ids"] == 1
    assert health["resolved_order_ids"] == 0
    assert health["unresolved_order_ids"] == ["order-timeout"]
    assert "broker timeout" in health["errors"][0]

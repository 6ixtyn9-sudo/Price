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


# ---------------------------------------------------------------------------
# Lane confinement at the broker choke point (2026-08-07): submit_entry must
# refuse cross-lane symbols BEFORE any broker client is constructed, and the
# refusal must be journaled as evidence. Pure shape rules are pinned in
# tests/test_substrate_isolation.py.
# ---------------------------------------------------------------------------


def test_submit_entry_lane_guard_rejects_futures_lane_equity_before_broker(tmp_path, monkeypatch):
    """The exact ghost path that placed real XLF orders on 2026-07-30/
    2026-08-04: lane=fut, symbol=XLF, otherwise valid order."""
    def _no_client():  # pragma: no cover - must never run
        raise AssertionError("broker client constructed despite lane guard")

    monkeypatch.setattr(trading, "get_trading_client", _no_client)
    monkeypatch.setattr(trading, "TRADE_JOURNAL_PATH", tmp_path / "trade_journal_futures.csv")

    result = trading.submit_entry(
        symbol="XLF",
        qty=17,
        slice_label="state_ext=stretched_up + state_slope=flat",
        side="buy",
        limit_price=57.0,
        entry_bar_ts="2026-08-04 00:00:00+00:00",
        timeframe="1d",
        bin_mode="rolling",
        exit_horizon=5,
        stop_atr_mult=2.0,
        lane="fut",
    )
    assert result["status"] == "rejected"
    assert result["order_id"] is None
    assert result["error"].startswith("lane_guard: futures lane submits nothing")

    journal = pd.read_csv(tmp_path / "trade_journal_futures.csv")
    assert len(journal) == 1
    assert journal.iloc[0]["status"] == "rejected"
    assert "lane_guard" in journal.iloc[0]["error"]
    assert journal.iloc[0]["action"] == "entry"


def test_submit_entry_lane_guard_blocks_crypto_lane_equity_before_broker(tmp_path, monkeypatch):
    """Mirror guard: the crypto lane must never touch equities (its ops
    files accumulated KLAC/AMAT rows in July via shared-account activity)."""
    def _no_client():  # pragma: no cover - must never run
        raise AssertionError("broker client constructed despite lane guard")

    monkeypatch.setattr(trading, "get_trading_client", _no_client)
    monkeypatch.setattr(trading, "TRADE_JOURNAL_PATH", tmp_path / "trade_journal_crypto.csv")

    result = trading.submit_entry(
        symbol="KLAC", qty=2, slice_label="s", side="buy", limit_price=900.0,
        timeframe="1d", bin_mode="rolling", exit_horizon=5, stop_atr_mult=2.0, lane="crypto",
    )
    assert result["status"] == "rejected"
    assert "crypto lane may only submit" in result["error"]


def test_submit_entry_lane_guard_allows_equity_lane_equity_symbol(tmp_path, monkeypatch):
    """Control: an ordinary equity order on the equity lane sails past the
    guard and reaches the (fake) broker client."""
    client = _FakeClient()
    monkeypatch.setattr(trading, "get_trading_client", lambda: client)
    monkeypatch.setattr(trading, "TRADE_JOURNAL_PATH", tmp_path / "trade_journal.csv")
    monkeypatch.setattr(trading, "_write_open_position_context", lambda **k: None)

    result = trading.submit_entry(
        symbol="XLF", qty=1, slice_label="s", side="buy", limit_price=57.0,
        timeframe="1d", bin_mode="rolling", exit_horizon=5, stop_atr_mult=2.0, lane="eq",
    )
    assert result["status"] == "accepted"
    assert len(client.submitted) == 1
    journal = pd.read_csv(tmp_path / "trade_journal.csv")
    assert len(journal) == 1 and "lane_guard" not in str(journal.iloc[0].get("error", ""))


# ---------------------------------------------------------------------------
# Duplicate client_order_id protection (2026-08-13): the id is deterministic
# per signal bar, so a static daily signal re-derives the same id every scan.
# Observed 2026-08-11 on WMT: 10 broker rejections in ~90 minutes
# (40010001 "client_order_id must be unique"). submit_entry must (a) give a
# re-issued attempt a fresh suffixed id and (b) never re-submit while an
# order for the same signal is already resting at the broker.
# ---------------------------------------------------------------------------

def _base_client_order_id(symbol="XLF", timeframe="1d", side="buy",
                          bin_mode="rolling", slice_label="s",
                          entry_bar_ts="2026-08-11 04:00:00+00:00", lane="eq"):
    import hashlib
    safe = symbol.upper().replace("/", "-").replace(".", "-").replace(":", "-").replace(" ", "-")
    hash_str = f"{symbol}|{timeframe}|{side}|{bin_mode}|{slice_label}|{entry_bar_ts}"
    hash8 = hashlib.sha256(hash_str.encode()).hexdigest()[:8]
    return f"price-{lane}-{safe}-{timeframe}-{side}-{hash8}"


def test_submit_entry_reissue_gets_fresh_suffixed_id(tmp_path, monkeypatch):
    """A re-issued attempt for the same signal must NOT reuse the burned id:
    the second submission carries a monotonic -r2 suffix and reaches the
    broker instead of a guaranteed 400."""
    client = _FakeClient()
    monkeypatch.setattr(trading, "get_trading_client", lambda: client)
    monkeypatch.setattr(trading, "TRADE_JOURNAL_PATH", tmp_path / "trade_journal.csv")
    monkeypatch.setattr(trading, "_write_open_position_context", lambda **k: None)

    kwargs = dict(
        symbol="XLF", qty=1, slice_label="s", side="buy", limit_price=57.0,
        entry_bar_ts="2026-08-11 04:00:00+00:00", timeframe="1d",
        bin_mode="rolling", exit_horizon=5, stop_atr_mult=2.0, lane="eq",
    )
    base = _base_client_order_id(**{k: v for k, v in kwargs.items() if k in (
        "symbol", "timeframe", "side", "bin_mode", "slice_label", "entry_bar_ts", "lane")})

    first = trading.submit_entry(**kwargs)
    assert first["status"] == "accepted"
    assert first["client_order_id"] == base
    assert client.submitted[0].client_order_id == base

    second = trading.submit_entry(**kwargs)
    assert second["status"] == "accepted"
    assert second["client_order_id"] == f"{base}-r2"
    assert client.submitted[1].client_order_id == f"{base}-r2"


def test_submit_entry_blocks_when_same_signal_order_resting(tmp_path, monkeypatch):
    """While an entry order for this exact signal is already resting at the
    broker, submit_entry returns a blocked result and never calls the
    broker (no duplicate orders, no 400 noise)."""
    client = _FakeClient()
    monkeypatch.setattr(trading, "get_trading_client", lambda: client)
    monkeypatch.setattr(trading, "TRADE_JOURNAL_PATH", tmp_path / "trade_journal.csv")
    monkeypatch.setattr(trading, "_write_open_position_context", lambda **k: None)

    kwargs = dict(
        symbol="WMT", qty=8, slice_label="cross_USO_state_ext=neutral + state_ext=neutral",
        side="buy", limit_price=108.85,
        entry_bar_ts="2026-08-11 04:00:00+00:00", timeframe="1d",
        bin_mode="rolling", exit_horizon=20, stop_atr_mult=5.0, lane="eq",
    )
    base = _base_client_order_id(symbol="WMT", timeframe="1d", side="buy",
                                 bin_mode="rolling",
                                 slice_label="cross_USO_state_ext=neutral + state_ext=neutral",
                                 entry_bar_ts="2026-08-11 04:00:00+00:00", lane="eq")
    resting = pd.DataFrame([{
        "order_id": "ord-resting-1", "client_order_id": base,
        "symbol": "WMT", "qty": 8.0, "side": "buy", "type": "limit",
        "status": "new", "limit_price": 108.85, "stop_price": None,
        "submitted_at": "2026-08-11T12:00:00Z", "expires_at": "",
    }])
    monkeypatch.setattr(trading, "get_open_orders", lambda: resting)

    result = trading.submit_entry(**kwargs)
    assert result["status"] == "rejected"
    assert result["order_id"] is None
    assert result["error"].startswith("duplicate_pending_entry")
    assert client.submitted == []  # broker never called


def test_submit_entry_blocks_only_its_own_signal(tmp_path, monkeypatch):
    """A resting order for a DIFFERENT signal (different slice/bar) must not
    block this submission — the pending check is exact to the base id."""
    client = _FakeClient()
    monkeypatch.setattr(trading, "get_trading_client", lambda: client)
    monkeypatch.setattr(trading, "TRADE_JOURNAL_PATH", tmp_path / "trade_journal.csv")
    monkeypatch.setattr(trading, "_write_open_position_context", lambda **k: None)

    other_base = _base_client_order_id(
        symbol="WMT", slice_label="state_ext=stretched_up",
        entry_bar_ts="2026-08-10 04:00:00+00:00")
    resting = pd.DataFrame([{
        "order_id": "ord-other-1", "client_order_id": other_base,
        "symbol": "WMT", "qty": 8.0, "side": "buy", "type": "limit",
        "status": "new", "limit_price": 109.0, "stop_price": None,
        "submitted_at": "2026-08-10T12:00:00Z", "expires_at": "",
    }])
    monkeypatch.setattr(trading, "get_open_orders", lambda: resting)

    result = trading.submit_entry(
        symbol="WMT", qty=8, slice_label="cross_USO_state_ext=neutral + state_ext=neutral",
        side="buy", limit_price=108.85,
        entry_bar_ts="2026-08-11 04:00:00+00:00", timeframe="1d",
        bin_mode="rolling", exit_horizon=20, stop_atr_mult=5.0, lane="eq",
    )
    assert result["status"] == "accepted"
    assert len(client.submitted) == 1


def test_submit_entry_recovers_from_duplicate_id_400(tmp_path, monkeypatch):
    """If the broker refuses a journal-unknown burned id (ops-cache loss),
    submit_entry re-issues ONCE with a fresh suffix instead of losing the
    signal, and the audit trail keeps BOTH attempts."""
    class _DupClient:
        def __init__(self):
            self.submitted = []
            self.calls = 0

        def submit_order(self, order_data):
            self.calls += 1
            self.submitted.append(order_data)
            if self.calls == 1:
                raise RuntimeError(
                    '{"code":40010001,"message":"client_order_id must be unique"}'
                )
            return _FakeOrder(
                id="order-retry", status="accepted",
                submitted_at="2026-08-11T12:00:00Z",
            )

    client = _DupClient()
    monkeypatch.setattr(trading, "get_trading_client", lambda: client)
    monkeypatch.setattr(trading, "TRADE_JOURNAL_PATH", tmp_path / "trade_journal.csv")
    monkeypatch.setattr(trading, "_write_open_position_context", lambda **k: None)

    kwargs = dict(
        symbol="WMT", qty=8,
        slice_label="cross_USO_state_ext=neutral + state_ext=neutral",
        side="buy", limit_price=108.85,
        entry_bar_ts="2026-08-11 04:00:00+00:00", timeframe="1d",
        bin_mode="rolling", exit_horizon=20, stop_atr_mult=5.0, lane="eq",
    )
    base = _base_client_order_id(
        symbol="WMT", slice_label="cross_USO_state_ext=neutral + state_ext=neutral",
        entry_bar_ts="2026-08-11 04:00:00+00:00")

    result = trading.submit_entry(**kwargs)
    assert result["status"] == "accepted"
    assert result["client_order_id"] == f"{base}-r1"
    assert client.calls == 2
    assert client.submitted[0].client_order_id == base
    assert client.submitted[1].client_order_id == f"{base}-r1"

    journal = pd.read_csv(tmp_path / "trade_journal.csv")
    assert len(journal) == 2
    assert journal.iloc[0]["status"] == "rejected"
    assert "client_order_id must be unique" in str(journal.iloc[0]["error"])
    assert journal.iloc[1]["status"] == "accepted"
    assert journal.iloc[1]["client_order_id"] == f"{base}-r1"

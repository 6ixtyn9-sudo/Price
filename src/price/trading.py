"""Minimal Alpaca paper-trading execution layer.

This module handles:
  - connecting to Alpaca's paper-trading API
  - submitting market/limit orders (entry + exit)
  - tracking open positions and their originating slice signals
  - logging every action to a CSV trade journal

It does NOT:
  - decide when to trade (that's monitor.py)
  - compute features or slice conditions (that's features.py + discovery.py)
  - claim any edge or guarantee any outcome
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    GetOrdersRequest,
    MarketOrderRequest,
    LimitOrderRequest,
    ReplaceOrderRequest,
    StopOrderRequest,
)
from alpaca.trading.enums import OrderSide, QueryOrderStatus, TimeInForce

from price.config import ALPACA_API_KEY, ALPACA_SECRET_KEY, DATA_DIR


TRADE_JOURNAL_PATH = DATA_DIR / "trade_journal.csv"


def _enum_value(x):
    """Extract the plain string value from an Alpaca SDK enum, safely.

    CRITICAL: Alpaca's enums (PositionSide, OrderSide, OrderStatus,
    OrderType, ...) are `str`-mixin enums. `str(PositionSide.LONG)`
    returns `"PositionSide.LONG"` (the __str__ override), NOT `"long"`
    (the actual value) -- even though the object itself equality-compares
    equal to the plain string `"long"`. Wrapping one of these enums in
    `str(...)` before storing it in a dict/DataFrame silently corrupts it
    for every later `== "long"` / `.lower()` / CSV round-trip comparison.

    This bug was real and live in this codebase: `get_open_positions()`
    stored raw `p.side` (safe), but `get_open_orders()` /
    `get_orders_for_symbol()` stored `str(o.side)`, and
    `submit_protective_stop()` / `replace_protective_stop()` stored
    `str(order.status)` -- meaning a genuinely REJECTED stop order (Alpaca
    returns HTTP 200 with an async `status="rejected"` from the execution
    venue; this is documented Alpaca behaviour, not a client-side error) was
    stored as the string `"OrderStatus.REJECTED"`, which never equals the
    literal `"rejected"` the caller checks for. The position was then
    treated as successfully protected when it was not protected at all.

    Fix: extract `.value` when present (every Alpaca enum has one and no
    plain string/UUID/datetime does), else pass through unchanged. Safe
    for enums, plain strings, None, UUIDs, and datetimes alike.
    """
    return getattr(x, "value", x)


def get_trading_client() -> TradingClient:
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        raise ValueError("Alpaca API credentials missing. Set ALPACA_API_KEY and ALPACA_SECRET_KEY in .env")
    return TradingClient(
        api_key=ALPACA_API_KEY,
        secret_key=ALPACA_SECRET_KEY,
        paper=True,
    )


def get_account_info() -> dict:
    client = get_trading_client()
    acct = client.get_account()
    return {
        "cash": float(acct.cash),
        "equity": float(acct.equity),
        "buying_power": float(acct.buying_power),
        "status": acct.status,
        "pattern_day_trader": acct.pattern_day_trader,
    }


def get_open_positions() -> pd.DataFrame:
    client = get_trading_client()
    positions = client.get_all_positions()
    if not positions:
        return pd.DataFrame()
    rows = []
    for p in positions:
        rows.append({
            "symbol": p.symbol,
            "qty": float(p.qty),
            "side": _enum_value(p.side),
            "avg_entry_price": float(p.avg_entry_price),
            "current_price": float(p.current_price),
            "unrealized_pl": float(p.unrealized_pl),
            "unrealized_plpc": float(p.unrealized_plpc),
            "market_value": float(p.market_value),
        })
    return pd.DataFrame(rows)



def get_open_orders() -> pd.DataFrame:
    """Return currently open/pending Alpaca paper orders.

    Used by the paper-trading risk gate to prevent duplicate queued entries
    when market orders are submitted outside regular market hours and remain
    accepted until the next session.
    """
    client = get_trading_client()
    orders = client.get_orders()

    if not orders:
        return pd.DataFrame()

    rows = []
    for o in orders:
        rows.append({
            "order_id": str(o.id),
            "symbol": str(o.symbol).upper(),
            "qty": float(o.qty) if o.qty is not None else 0.0,
            "side": _enum_value(o.side),
            "type": _enum_value(o.type),
            "status": _enum_value(o.status),
            "submitted_at": str(o.submitted_at),
            "expires_at": str(getattr(o, "expires_at", "")),
        })

    return pd.DataFrame(rows)

def submit_entry(
    symbol: str,
    qty: int,
    slice_label: str,
    side: str = "buy",
    limit_price: Optional[float] = None,
    entry_bar_ts: Optional[str] = None,
    timeframe: Optional[str] = None,
) -> dict:
    """Submit a market or limit entry order and journal it.

    If limit_price is provided, submits a LIMIT order (recommended to control
    slippage). If None, falls back to a MARKET order (legacy/risky).

    entry_bar_ts / timeframe are recorded so the exit policy can count bars
    held in the position's own timeframe (faithful to the fwd_ret_5 horizon).
    Both optional for backward compatibility; older callers still work.
    """
    client = get_trading_client()
    order_side = OrderSide.BUY if side == "buy" else OrderSide.SELL

    if limit_price is not None:
        # Alpaca requires limit_price to have at most 2 decimals when >= $1.00,
        # and at most 4 decimals when < $1.00.
        rounded_limit = round(float(limit_price), 2 if limit_price >= 1.0 else 4)
        order_data = LimitOrderRequest(
            symbol=symbol.upper(),
            qty=qty,
            side=order_side,
            time_in_force=TimeInForce.DAY,
            limit_price=rounded_limit,
        )
        order_type = "limit"
    else:
        order_data = MarketOrderRequest(
            symbol=symbol.upper(),
            qty=qty,
            side=order_side,
            time_in_force=TimeInForce.DAY,
        )
        order_type = "market"

    try:
        order = client.submit_order(order_data)
        result = {
            "order_id": str(order.id),
            "symbol": symbol.upper(),
            "qty": qty,
            "side": side,
            "order_type": order_type,
            "limit_price": limit_price,
            "time_in_force": "day",
            "status": _enum_value(order.status),
            "submitted_at": str(order.submitted_at),
            "slice_label": slice_label,
            "entry_bar_ts": entry_bar_ts,
            "timeframe": timeframe,
        }
    except Exception as e:
        result = {
            "order_id": None,
            "symbol": symbol.upper(),
            "qty": qty,
            "side": side,
            "order_type": order_type,
            "limit_price": limit_price,
            "status": "rejected",
            "error": str(e),
            "slice_label": slice_label,
            "entry_bar_ts": entry_bar_ts,
            "timeframe": timeframe,
        }

    _append_journal(result, action="entry")
    return result


def submit_protective_stop(
    symbol: str,
    qty: float,
    stop_price: float,
    position_side: str = "long",
) -> dict:
    """Submit a REAL resting stop order at the broker to protect a position.

    This is what makes the R-based stop (price.stops) continuously
    enforced -- Alpaca watches this order tick-by-tick, independent of
    whether/when paper_trade.py next runs. `position_side` is the
    POSITION's side (a long position is protected by a SELL stop; a
    short position is protected by a BUY stop). GTC so it survives
    across sessions until replaced or canceled.

    Never raises: a failed submission is returned with status='rejected'
    and an 'error' field so the caller can decide how to handle a
    genuinely unprotected position (this must never be silently ignored).

    IMPORTANT: `status` in the returned dict is normalized via
    _enum_value(order.status), NOT str(order.status) -- Alpaca can return
    HTTP 200 with an order whose status is asynchronously set to
    'rejected' by the execution venue (e.g. a wash-trade guard, a
    shortable-asset restriction). str(order.status) would render this as
    the literal string "OrderStatus.REJECTED", which never equals the
    "rejected" every caller checks for, silently treating a rejected stop
    as successfully attached. See _enum_value's docstring.
    """
    client = get_trading_client()
    order_side = OrderSide.SELL if position_side == "long" else OrderSide.BUY

    # Alpaca requires stop_price to have at most 2 decimals when >= $1.00,
    # and at most 4 decimals when < $1.00 (sub-penny rejection otherwise).
    rounded_stop_price = round(float(stop_price), 2 if stop_price >= 1.0 else 4)

    order_data = StopOrderRequest(
        symbol=symbol.upper(),
        qty=qty,
        side=order_side,
        time_in_force=TimeInForce.GTC,
        stop_price=rounded_stop_price,
    )

    try:
        order = client.submit_order(order_data)
        result = {
            "order_id": str(order.id),
            "symbol": symbol.upper(),
            "qty": qty,
            "side": _enum_value(order_side),
            "order_type": "stop",
            "stop_price": rounded_stop_price,
            "time_in_force": "gtc",
            "status": _enum_value(order.status),
            "submitted_at": str(order.submitted_at),
        }
        if result["status"] == "rejected":
            result["error"] = (
                "order accepted by Alpaca (HTTP 200) but asynchronously "
                "REJECTED by the execution venue (e.g. wash-trade guard, "
                "shortable-asset restriction); no stop is actually resting"
            )
    except Exception as e:
        result = {
            "order_id": None,
            "symbol": symbol.upper(),
            "qty": qty,
            "side": _enum_value(order_side),
            "order_type": "stop",
            "stop_price": rounded_stop_price,
            "status": "rejected",
            "error": str(e),
        }

    _append_journal(result, action="protective_stop_submit")
    return result


def replace_protective_stop(order_id: str, new_stop_price: float) -> dict:
    """Move an existing resting stop order to `new_stop_price` (ratchet).

    Uses Alpaca's replace-order endpoint so the SAME order_id persists
    (no cancel/resubmit race where the position would be briefly
    unprotected). Never raises; a failure is returned with
    status='rejected' so the caller can fall back to cancel+resubmit.

    IMPORTANT: `status` is normalized via _enum_value(order.status), NOT
    str(order.status) -- see submit_protective_stop's docstring for why
    an unwrapped str() here would silently mask a genuine async rejection
    (e.g. the replace price crossing the current market and getting
    rejected) as a successful ratchet.
    """
    client = get_trading_client()
    rounded_stop_price = round(float(new_stop_price), 2 if new_stop_price >= 1.0 else 4)
    try:
        order = client.replace_order_by_id(
            order_id,
            ReplaceOrderRequest(stop_price=rounded_stop_price),
        )
        result = {
            "order_id": str(order.id),
            "prior_order_id": str(order_id),
            "stop_price": rounded_stop_price,
            "status": _enum_value(order.status),
        }
        if result["status"] == "rejected":
            result["error"] = (
                "replace accepted by Alpaca (HTTP 200) but asynchronously "
                "REJECTED by the execution venue; the ORIGINAL stop order "
                "may still be resting at its prior (unratcheted) price -- "
                "verify via get_orders_for_symbol before assuming no protection"
            )
    except Exception as e:
        result = {
            "order_id": None,
            "prior_order_id": str(order_id),
            "stop_price": rounded_stop_price,
            "status": "rejected",
            "error": str(e),
        }
    _append_journal(result, action="protective_stop_replace")
    return result


def cancel_order(order_id: str) -> dict:
    """Cancel a resting order (e.g. a protective stop, when the position
    is being closed for another reason). Never raises."""
    client = get_trading_client()
    try:
        client.cancel_order_by_id(order_id)
        result = {"order_id": str(order_id), "status": "cancel_requested"}
    except Exception as e:
        result = {"order_id": str(order_id), "status": "cancel_failed", "error": str(e)}
    _append_journal(result, action="order_cancel")
    return result


def get_order_fill_info(order_id: str) -> dict:
    """Fetch a single order's fill details by id. Used to recover the real
    fill price/qty when a resting protective stop fires AUTONOMOUSLY at
    the broker (i.e. no code in this repo called close_position/submit_exit
    for it -- Alpaca executed the closing trade on its own). Never raises;
    returns an empty dict with an 'error' key on any failure so the caller
    can decide whether to fall back to an approximation.
    """
    client = get_trading_client()
    try:
        order = client.get_order_by_id(order_id)
        filled_qty = getattr(order, "filled_qty", None)
        filled_avg_price = getattr(order, "filled_avg_price", None)
        filled_at = getattr(order, "filled_at", None)

        def _optional_float(value):
            try:
                value = float(value)
            except (TypeError, ValueError):
                return None
            return value if value == value else None

        def _optional_text(value):
            if value is None:
                return None
            text = str(value).strip()
            return None if text.lower() in ("", "none", "nan") else text

        return {
            "order_id": str(order.id),
            "symbol": str(order.symbol).upper(),
            "status": _enum_value(order.status),
            "side": _enum_value(order.side),
            "filled_qty": _optional_float(filled_qty),
            "filled_avg_price": _optional_float(filled_avg_price),
            "filled_at": _optional_text(filled_at),
        }
    except Exception as e:  # noqa: BLE001 - callers must degrade gracefully
        return {"error": str(e)}


def reconcile_trade_journal(path: Optional[Path] = None, get_order_fill_info_fn=None) -> pd.DataFrame:
    """Reconcile journaled orders with Alpaca's authoritative order state.

    Submission-time journal rows are intentionally retained for audit history,
    but their ``status``/fill fields can be stale because Alpaca resolves DAY
    orders asynchronously. This read-only reconciliation updates rows with
    broker-confirmed status, filled quantity, average fill price, and fill
    time. It never submits, cancels, or replaces an order.

    Rows are written only when broker state changes, so calling this at the
    start of every scheduled scan does not create needless git diffs.
    """
    journal_path = Path(path) if path else Path(TRADE_JOURNAL_PATH)
    if not journal_path.exists():
        return pd.DataFrame()
    try:
        journal = pd.read_csv(journal_path)
    except (pd.errors.EmptyDataError, pd.errors.ParserError):
        return pd.DataFrame()
    if journal.empty or "order_id" not in journal.columns:
        return journal

    if get_order_fill_info_fn is None:
        get_order_fill_info_fn = get_order_fill_info

    now = datetime.now(timezone.utc).isoformat()
    changed = False
    cache = {}

    # Legacy entries may predate timeframe/entry-bar journaling. The paper
    # audit log carries those fields keyed by the exact submitted order_id;
    # use it only to fill missing metadata, never to overwrite operator data.
    signal_metadata = {}
    paper_log_path = Path(DATA_DIR) / "paper_trade_log.csv"
    if paper_log_path.exists():
        try:
            paper_log = pd.read_csv(paper_log_path)
            if "order_id" in paper_log.columns:
                for _, signal in paper_log.iterrows():
                    signal_id_value = signal.get("order_id")
                    signal_id = None if pd.isna(signal_id_value) else str(signal_id_value).strip()
                    if signal_id.lower() in ("", "nan", "none"):
                        signal_id = None
                    action = str(signal.get("action", "")).lower()
                    if signal_id and action == "enter":
                        signal_metadata[signal_id] = signal.to_dict()
        except (pd.errors.EmptyDataError, pd.errors.ParserError):
            signal_metadata = {}

    def _clean_id(value):
        if value is None:
            return None
        text = str(value).strip()
        return None if not text or text.lower() in ("nan", "none") else text

    def _normalised(value):
        if value is None:
            return None
        try:
            if pd.isna(value):
                return None
        except (TypeError, ValueError):
            pass
        text = str(value).strip()
        if text.lower() in ("", "none", "nan"):
            return None
        try:
            number = float(value)
            if number == number and number not in (float("inf"), float("-inf")):
                return ("number", round(number, 12))
        except (TypeError, ValueError):
            pass
        return ("text", text)

    def _same(a, b):
        """Compare broker/CSV scalar values without None/NaN churn."""
        return _normalised(a) == _normalised(b)

    for idx, row in journal.iterrows():
        order_id = _clean_id(row.get("order_id"))
        if order_id is None:
            continue
        if order_id not in cache:
            try:
                cache[order_id] = get_order_fill_info_fn(order_id) or {}
            except Exception as exc:  # noqa: BLE001 - reconciliation is best effort
                cache[order_id] = {"error": str(exc)}
        info = cache[order_id]
        updates = {}
        if info and not info.get("error") and info.get("status"):
            updates.update({
                "status": info.get("status"),
                "broker_status": info.get("status"),
                "filled_qty": info.get("filled_qty"),
                "filled_avg_price": info.get("filled_avg_price"),
                "filled_at": info.get("filled_at"),
            })

        metadata = signal_metadata.get(order_id) if str(row.get("action", "")).lower() == "entry" else None
        if metadata:
            metadata_fields = {
                "slice_label": metadata.get("slice_combination"),
                "entry_bar_ts": metadata.get("bar_ts_utc"),
                "timeframe": metadata.get("timeframe"),
                "bin_mode": metadata.get("bin_mode", "insample"),
            }
            for col, value in metadata_fields.items():
                if _normalised(row.get(col)) is None and _normalised(value) is not None:
                    updates[col] = value

        if not updates:
            continue
        row_changed = any(not _same(row.get(col), value) for col, value in updates.items())
        if not row_changed:
            continue
        for col, value in updates.items():
            journal.loc[idx, col] = value
        journal.loc[idx, "reconciled_at_utc"] = now
        changed = True

    if changed:
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        journal.to_csv(journal_path, index=False)
    return journal


def get_orders_for_symbol(symbol: str, status: str = "open") -> pd.DataFrame:
    """Open (or all) orders for one symbol, including stop orders, so the
    caller can find a position's resting protective stop by symbol."""
    client = get_trading_client()
    query_status = QueryOrderStatus.ALL if status == "all" else QueryOrderStatus.OPEN
    orders = client.get_orders(
        filter=GetOrdersRequest(status=query_status, symbols=[symbol.upper()])
    )
    if not orders:
        return pd.DataFrame()
    rows = []
    for o in orders:
        rows.append({
            "order_id": str(o.id),
            "symbol": str(o.symbol).upper(),
            "qty": float(o.qty) if o.qty is not None else 0.0,
            "side": _enum_value(o.side),
            "type": _enum_value(o.type),
            "status": _enum_value(o.status),
            "stop_price": float(o.stop_price) if getattr(o, "stop_price", None) is not None else None,
            "submitted_at": str(o.submitted_at),
        })
    return pd.DataFrame(rows)


def submit_exit(symbol: str, qty: int, side: str = "sell") -> dict:
    client = get_trading_client()
    order_side = OrderSide.SELL if side == "sell" else OrderSide.BUY

    order_data = MarketOrderRequest(
        symbol=symbol.upper(),
        qty=qty,
        side=order_side,
        time_in_force=TimeInForce.DAY,
    )

    try:
        order = client.submit_order(order_data)
        result = {
            "order_id": str(order.id),
            "symbol": symbol.upper(),
            "qty": qty,
            "side": side,
            "order_type": "market",
            "time_in_force": "day",
            "status": _enum_value(order.status),
            "submitted_at": str(order.submitted_at),
        }
    except Exception as e:
        result = {
            "order_id": None,
            "symbol": symbol.upper(),
            "qty": qty,
            "side": side,
            "order_type": "market",
            "status": "rejected",
            "error": str(e),
        }

    _append_journal(result, action="exit")
    return result


def close_position(symbol: str, cancel_open_orders: bool = True,
                    cancel_settle_max_checks: int = 5,
                    cancel_settle_sleep_seconds: float = 0.5,
                    sleep_fn=None) -> dict:
    """Close a position. By default first cancels any resting orders on
    the symbol (e.g. its protective stop) so a naked stop order cannot
    survive an exit triggered by a different policy (state-break/horizon).
    Cancellation failures are best-effort and never block the close.

    RACE-CONDITION FIX: Alpaca documents that cancelling an order and then
    IMMEDIATELY closing the position can fail with an insufficient-qty
    error, because the shares can still be considered reserved by the
    order pending cancellation. Before this fix, this function fired the
    cancel and the close back-to-back with no wait, so a close failure
    from this exact race would leave the stop already canceled AND the
    position still open -- i.e. completely unprotected until the next
    scan (up to the full scan interval). Fix: after requesting each
    cancellation, POLL get_orders_for_symbol(status='open') until none of
    the just-canceled order ids remain (or cancel_settle_max_checks is
    exhausted), before calling the broker's close endpoint. This is
    best-effort settling, not a guarantee -- the close's own result
    status must still be checked by the caller (see the 'rejected'
    handling below); it substantially narrows the race window rather
    than eliminating a theoretical retry need entirely.

    CRITICAL P&L FIX: this function used to journal an 'exit' row with NO
    qty / avg_entry_price / current_price at all. That meant TWO downstream
    consumers were silently blind to every close_position-driven exit,
    including every real broker-side stop-loss fill (this is the primary
    exit path once a stop is attached -- reconcile_stops detects the
    position vanished, but the actual CLOSING transaction, when triggered
    manually via this function, went through here):
      - position_manager.get_today_realized_pnl (the account-level daily-
        loss KILL SWITCH) sums (current_price - avg_entry_price) * qty
        from 'exit' journal rows -- with none of those fields present, it
        computed exactly $0.00 of realized P&L for every close_position
        exit, forever, regardless of the actual loss.
      - attribution.reconstruct_round_trips also reads qty/avg_entry_price
        from the same rows to build round-trips -- it was equally blind.
    Fix: snapshot the position's qty/avg_entry_price from get_open_positions
    BEFORE calling the broker's close endpoint (the last moment those
    fields are still readable), and use the ORDER's own filled_avg_price
    (the true fill/exit price) when available, falling back to the
    pre-close current_price snapshot otherwise. Best-effort: a failure to
    snapshot must never block the close itself.
    """
    if sleep_fn is None:
        import time
        sleep_fn = time.sleep

    pre_close_snapshot = {}
    try:
        positions = get_open_positions()
        if positions is not None and not positions.empty:
            match = positions[positions["symbol"].astype(str).str.upper() == symbol.upper()]
            if not match.empty:
                row = match.iloc[0]
                pre_close_snapshot = {
                    "qty": float(row.get("qty")) if row.get("qty") is not None else None,
                    "avg_entry_price": float(row.get("avg_entry_price")) if row.get("avg_entry_price") is not None else None,
                    "current_price": float(row.get("current_price")) if row.get("current_price") is not None else None,
                }
    except Exception:  # noqa: BLE001 - closing the position must not be blocked
        pre_close_snapshot = {}

    if cancel_open_orders:
        try:
            open_orders = get_orders_for_symbol(symbol, status="open")
            canceled_ids = set()
            for _, row in open_orders.iterrows():
                cancel_order(row["order_id"])
                canceled_ids.add(row["order_id"])

            # Poll until the canceled orders no longer show up as OPEN, so
            # the immediately-following close_position call does not race
            # against shares still reserved by a pending cancellation.
            for _ in range(max(0, cancel_settle_max_checks)):
                if not canceled_ids:
                    break
                still_open = get_orders_for_symbol(symbol, status="open")
                still_open_ids = set(still_open["order_id"]) if not still_open.empty else set()
                if not (canceled_ids & still_open_ids):
                    break
                sleep_fn(cancel_settle_sleep_seconds)
        except Exception:  # noqa: BLE001 - closing the position must not be blocked
            pass

    client = get_trading_client()
    try:
        order = client.close_position(symbol.upper())
        filled_avg_price = getattr(order, "filled_avg_price", None)
        exit_price = (
            float(filled_avg_price) if filled_avg_price is not None
            else pre_close_snapshot.get("current_price")
        )
        result = {
            "order_id": str(order.id),
            "symbol": symbol.upper(),
            "side": "close",
            "status": _enum_value(order.status),
            "submitted_at": str(order.submitted_at),
            "qty": pre_close_snapshot.get("qty"),
            "avg_entry_price": pre_close_snapshot.get("avg_entry_price"),
            "current_price": exit_price,
        }
        if result["status"] == "rejected":
            result["error"] = (
                "close accepted by Alpaca (HTTP 200) but asynchronously "
                "REJECTED by the execution venue; the position is likely "
                "STILL OPEN -- callers relying on this close succeeding "
                "(e.g. the leverage force-close safety net) must check "
                "result['status'] explicitly, not just the absence of an "
                "exception"
            )
    except Exception as e:
        result = {
            "order_id": None,
            "symbol": symbol.upper(),
            "side": "close",
            "status": "rejected",
            "error": str(e),
            "qty": pre_close_snapshot.get("qty"),
            "avg_entry_price": pre_close_snapshot.get("avg_entry_price"),
            "current_price": pre_close_snapshot.get("current_price"),
        }

    _append_journal(result, action="exit")
    return result


def _append_journal(row: dict, action: str):
    row["action"] = action
    row["timestamp_utc"] = datetime.now(timezone.utc).isoformat()
    journal_path = Path(TRADE_JOURNAL_PATH)

    if journal_path.exists():
        existing = pd.read_csv(journal_path)
        updated = pd.concat([existing, pd.DataFrame([row])], ignore_index=True)
    else:
        updated = pd.DataFrame([row])

    updated.to_csv(journal_path, index=False)


def append_synthetic_exit(row: dict) -> None:
    """Public entry point for a caller OUTSIDE this module to journal an
    'exit' row this module didn't itself execute (e.g. stop_manager
    detecting that a resting protective stop fired AUTONOMOUSLY at the
    broker -- no submit_exit/close_position call happened for it, so
    without this the fill would never reach trade_journal.csv at all, and
    the daily-loss kill switch / P&L attribution would stay blind to it).
    `row` should already carry qty/avg_entry_price/current_price/symbol.
    """
    _append_journal(dict(row), action="exit")


def load_trade_journal() -> pd.DataFrame:
    journal_path = Path(TRADE_JOURNAL_PATH)
    if not journal_path.exists():
        return pd.DataFrame()
    return pd.read_csv(journal_path)

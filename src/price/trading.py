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

import hashlib
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
import os


def _resolve_data_path(env_name: str, default_name: str):
    from pathlib import Path
    custom = os.getenv(env_name)
    if custom:
        return Path(custom)
    return DATA_DIR / default_name


TRADE_JOURNAL_PATH = _resolve_data_path("TRADE_JOURNAL_PATH", "trade_journal.csv")


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



def _remove_position_from_ledger(symbol: str) -> None:
    import glob
    import os
    import pandas as pd
    from price.config import DATA_DIR
    
    paths = glob.glob(str(DATA_DIR / "open_position_context_*.csv"))
    for path in paths:
        if not os.path.exists(path):
            continue
        try:
            df = pd.read_csv(path, low_memory=False, dtype=str)
            if not df.empty and "symbol" in df.columns:
                # Remove rows matching the symbol
                filtered = df[df["symbol"].astype(str).str.upper() != symbol.upper()]
                if len(filtered) < len(df):
                    filtered.to_csv(path, index=False)
        except Exception:
            pass


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

def get_recent_orders(symbol: str, limit: int = 50) -> list[dict]:
    """Return recent orders for a symbol, including filled/canceled/rejected.
    
    Used for context recovery from client_order_id.
    """
    client = get_trading_client()
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import QueryOrderStatus
    
    req = GetOrdersRequest(
        status=QueryOrderStatus.ALL,
        symbols=[symbol],
        limit=limit,
    )
    orders = client.get_orders(req)
    if not orders:
        return []
        
    return [
        {
            "order_id": str(o.id),
            "client_order_id": str(o.client_order_id) if o.client_order_id else "",
            "symbol": str(o.symbol).upper(),
            "qty": float(o.qty) if o.qty is not None else 0.0,
            "side": _enum_value(o.side),
            "status": _enum_value(o.status),
            "submitted_at": str(o.submitted_at),
        }
        for o in orders
    ]

def submit_entry(
    symbol: str,
    qty: int,
    slice_label: str,
    side: str = "buy",
    limit_price: Optional[float] = None,
    entry_bar_ts: Optional[str] = None,
    timeframe: Optional[str] = None,
    bin_mode: Optional[str] = None,
    exit_horizon: Optional[int] = None,
    lane: str = "eq",
    workflow_run_id: str = "",
    source_note: str = "",
) -> dict:
    """Submit a market or limit entry order and journal it.

    If limit_price is provided, submits a LIMIT order (recommended to control
    slippage). If None, falls back to a MARKET order (legacy/risky).

    entry_bar_ts / timeframe are recorded so the exit policy can count bars
    held in the position's own timeframe (faithful to the fwd_ret_5 horizon).
    All metadata arguments are optional for backward compatibility; older
    callers still work. bin_mode is persisted so exits can evaluate the same
    state-binning contract that authorized the entry.
    """
    client = get_trading_client()
    order_side = OrderSide.BUY if side == "buy" else OrderSide.SELL

    # 1. Generate client_order_id
    symbolsafe = symbol.upper().replace("/", "-").replace(".", "-").replace(":", "-").replace(" ", "-")
    hash_str = f"{symbol}|{timeframe}|{side}|{bin_mode}|{slice_label}|{entry_bar_ts}"
    hash8 = hashlib.sha256(hash_str.encode()).hexdigest()[:8]
    client_order_id = f"price-{lane}-{symbolsafe}-{timeframe}-{side}-{hash8}"
    
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
            client_order_id=client_order_id,
        )
        order_type = "limit"
    else:
        order_data = MarketOrderRequest(
            symbol=symbol.upper(),
            qty=qty,
            side=order_side,
            time_in_force=TimeInForce.DAY,
            client_order_id=client_order_id,
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
            "bin_mode": bin_mode,
            "exit_horizon": exit_horizon if exit_horizon is not None else 5,
            "client_order_id": client_order_id,
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
            "bin_mode": bin_mode,
            "exit_horizon": exit_horizon if exit_horizon is not None else 5,
            "client_order_id": client_order_id,
        }

    _append_journal(result, action="entry")
    if result["status"] not in ("rejected", "canceled"):
        # Write to operational context ledger
        _write_open_position_context(
            lane=lane,
            symbol=symbol.upper(),
            side=side,
            qty=qty,
            entry_order_id=result.get("order_id"),
            client_order_id=client_order_id,
            hash_str=hash_str,
            slice_combination=slice_label,
            timeframe=timeframe,
            bin_mode=bin_mode,
            entry_bar_ts=entry_bar_ts,
            status="open" if result["status"] == "filled" else "pending",
            workflow_run_id=workflow_run_id,
            source_note=source_note,
        )
    return result

def _write_open_position_context(
    lane: str, symbol: str, side: str, qty: int, entry_order_id: Optional[str],
    client_order_id: str, hash_str: str, slice_combination: str, timeframe: Optional[str],
    bin_mode: Optional[str], entry_bar_ts: Optional[str], status: str,
    workflow_run_id: str, source_note: str,
):
    import os
    now_utc = datetime.now(timezone.utc).isoformat()
    # Schema: lane,symbol,side,qty,entry_order_id,client_order_id,context_key,slice_combination,timeframe,bin_mode,entry_bar_ts,submitted_at_utc,filled_at_utc,status,workflow_run_id,source_note,updated_at_utc
    row = {
        "lane": lane,
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "entry_order_id": entry_order_id or "",
        "client_order_id": client_order_id,
        "context_key": hash_str,
        "slice_combination": slice_combination or "",
        "timeframe": timeframe or "",
        "bin_mode": bin_mode or "",
        "entry_bar_ts": entry_bar_ts or "",
        "submitted_at_utc": now_utc,
        "filled_at_utc": now_utc if status == "open" else "",
        "status": status,
        "workflow_run_id": workflow_run_id,
        "source_note": source_note,
        "updated_at_utc": now_utc,
    }
    
    ctx_path = DATA_DIR / f"open_position_context_{lane}.csv"
    if ctx_path.exists():
        df = pd.read_csv(ctx_path, dtype=str)
    else:
        df = pd.DataFrame(columns=row.keys())
        
    # Upsert by client_order_id or entry_order_id
    idx = None
    if "client_order_id" in df.columns and (df["client_order_id"] == client_order_id).any():
        idx = df[df["client_order_id"] == client_order_id].index[0]
    elif entry_order_id and "entry_order_id" in df.columns and (df["entry_order_id"] == entry_order_id).any():
        idx = df[df["entry_order_id"] == entry_order_id].index[0]
        
    new_df = pd.DataFrame([row])
    if idx is not None:
        # Update existing
        for col in row:
            if col not in df.columns:
                df[col] = ""
            # Don't overwrite submitted_at_utc if it exists
            if col == "submitted_at_utc" and pd.notna(df.at[idx, col]) and df.at[idx, col]:
                continue
            df.at[idx, col] = row[col]
    else:
        df = pd.concat([df, new_df], ignore_index=True)
        
    df.to_csv(ctx_path, index=False)


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


def replace_protective_stop(
    order_id: str,
    new_stop_price: float,
    qty: Optional[float] = None,
) -> dict:
    """Move an existing resting stop order to `new_stop_price` (ratchet).

    When ``qty`` is supplied, reconcile the broker stop quantity at the same
    time as its price. This covers partial limit fills that occur after the
    initial protective stop was attached.

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
        replace_kwargs = {"stop_price": rounded_stop_price}
        if qty is not None:
            replace_kwargs["qty"] = abs(float(qty))
        order = client.replace_order_by_id(
            order_id,
            ReplaceOrderRequest(**replace_kwargs),
        )
        result = {
            "order_id": str(order.id),
            "prior_order_id": str(order_id),
            "stop_price": rounded_stop_price,
            "qty": abs(float(qty)) if qty is not None else None,
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


def reconcile_trade_journal(
    path: Optional[Path] = None,
    get_order_fill_info_fn=None,
    health_out: Optional[dict] = None,
) -> pd.DataFrame:
    """Reconcile journaled orders with Alpaca's authoritative order state.

    Submission-time journal rows are intentionally retained for audit history,
    but their ``status``/fill fields can be stale because Alpaca resolves DAY
    orders asynchronously. This read-only reconciliation updates rows with
    broker-confirmed status, filled quantity, average fill price, and fill
    time. It never submits, cancels, or replaces an order.

    Rows are written only when broker state changes, so calling this at the
    start of every scheduled scan does not create needless git diffs.
    """
    def _set_health(ok, total=0, resolved=0, unresolved=None, errors=None, reason=None):
        if health_out is None:
            return
        health_out.clear()
        health_out.update({
            "ok": bool(ok),
            "total_order_ids": int(total),
            "resolved_order_ids": int(resolved),
            "unresolved_order_ids": list(unresolved or []),
            "errors": list(errors or []),
        })
        if reason:
            health_out["reason"] = reason

    journal_path = Path(path) if path else Path(TRADE_JOURNAL_PATH)
    if not journal_path.exists():
        _set_health(True, reason="journal_missing")
        return pd.DataFrame()
    try:
        journal = pd.read_csv(journal_path)
    except (pd.errors.EmptyDataError, pd.errors.ParserError) as exc:
        _set_health(False, errors=[str(exc)], reason="journal_unreadable")
        return pd.DataFrame()
    if journal.empty or "order_id" not in journal.columns:
        _set_health(True, reason="no_orders_to_reconcile")
        return journal

    if get_order_fill_info_fn is None:
        get_order_fill_info_fn = get_order_fill_info

    now = datetime.now(timezone.utc).isoformat()
    changed = False
    cache = {}
    order_ids_seen = set()
    unresolved_order_ids = set()
    reconciliation_errors = []

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
                    if signal_id is None or signal_id.lower() in ("", "nan", "none"):
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
        order_ids_seen.add(order_id)
        if order_id not in cache:
            try:
                cache[order_id] = get_order_fill_info_fn(order_id) or {}
            except Exception as exc:  # noqa: BLE001 - reconciliation is best effort
                cache[order_id] = {"error": str(exc)}
        info = cache[order_id]
        if not info or info.get("error") or not info.get("status"):
            unresolved_order_ids.add(order_id)
            error = (info or {}).get("error") if isinstance(info, dict) else None
            reconciliation_errors.append(
                f"{order_id}: {error or 'missing broker status'}"
            )
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

    unresolved = sorted(unresolved_order_ids)
    _set_health(
        not unresolved,
        total=len(order_ids_seen),
        resolved=len(order_ids_seen) - len(unresolved),
        unresolved=unresolved,
        errors=reconciliation_errors,
        reason="ok" if not unresolved else "unresolved_orders",
    )
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

    # ── Guard: if a non-stop close order is already pending for this
    # symbol, do NOT cancel and re-submit it. That is the cancel-resubmit
    # cycle: every hourly scan's close_position() cancels the previous
    # scan's still-open market sell and replaces it with an identical one,
    # while reconcile_stops() re-creates the protective stop that
    # close_position() just cancelled.  Returning the existing pending
    # order here breaks the cycle: the market sell fills when it fills,
    # and reconcile_stops() (patched separately) skips stop creation when
    # it sees the pending close.
    try:
        pending_df = get_orders_for_symbol(symbol, status="open")
        if pending_df is not None and not pending_df.empty:
            non_stop = pending_df[pending_df["type"] != "stop"]
            if not non_stop.empty:
                existing = non_stop.iloc[0]
                result = {
                    "order_id": str(existing["order_id"]),
                    "symbol": symbol.upper(),
                    "side": "close",
                    "status": "close_already_pending",
                    "submitted_at": str(existing.get("submitted_at", "")),
                    "reason": (
                        "non-stop close order already pending for "
                        f"{symbol}; skipping cancel+resubmit to avoid "
                        "the hourly cancel-resubmit cycle"
                    ),
                    "qty": pre_close_snapshot.get("qty"),
                    "avg_entry_price": pre_close_snapshot.get("avg_entry_price"),
                    "current_price": pre_close_snapshot.get("current_price"),
                }
                # Still journal it so the audit trail shows we considered
                # and explicitly skipped this close.
                _append_journal(result, action="exit")
                return result
    except Exception:  # noqa: BLE001 - must never block a close
        pass

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
    
    if result.get("status") != "rejected":
        _remove_position_from_ledger(symbol)
        
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
    _remove_position_from_ledger(row.get("symbol", ""))


def load_trade_journal() -> pd.DataFrame:
    journal_path = Path(TRADE_JOURNAL_PATH)
    if not journal_path.exists():
        return pd.DataFrame()
    return pd.read_csv(journal_path)


# ---------------------------------------------------------------------------
# Broker backfill: fetch filled orders + idempotent journal append
# ---------------------------------------------------------------------------

def get_recent_filled_orders(lookback_days: int = 60) -> pd.DataFrame:
    """Fetch recent filled orders from the Alpaca paper account.

    Read-only: never places, cancels, or replaces orders.

    Returns a DataFrame with columns:
        order_id, client_order_id, symbol, side, order_type, status,
        qty, filled_qty, filled_avg_price, filled_at, submitted_at, created_at

    Returns an empty DataFrame when there are no filled orders in the
    lookback window. Raises on genuine API / auth errors so the caller
    (backfill_trade_journal_from_broker_orders) can propagate a clear
    message rather than silently returning zero rows on a credentials
    failure.
    """
    from datetime import timedelta

    after_dt = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    client = get_trading_client()
    req = GetOrdersRequest(
        status=QueryOrderStatus.ALL,
        after=after_dt,
        limit=500,  # Alpaca SDK max per page; pagination handled below
    )

    all_orders = []
    # Alpaca SDK get_orders returns a list directly; repeat with offset-by-time
    # for pagination if needed (SDK may silently truncate to limit).
    orders = client.get_orders(filter=req)
    if orders:
        all_orders.extend(orders)

    # If the first page is full (hit the limit), page back in time.
    while orders and len(orders) >= 500:
        earliest = None
        for o in orders:
            ts = getattr(o, "submitted_at", None)
            if ts is not None:
                try:
                    ts = pd.Timestamp(ts).tz_convert("UTC")
                    if earliest is None or ts < earliest:
                        earliest = ts
                except Exception:
                    pass
        if earliest is None or earliest <= pd.Timestamp(after_dt):
            break
        page_req = GetOrdersRequest(
            status=QueryOrderStatus.ALL,
            after=after_dt,
            until=earliest,
            limit=500,
        )
        orders = client.get_orders(filter=page_req)
        if orders:
            all_orders.extend(orders)
        else:
            break

    if not all_orders:
        return pd.DataFrame()

    def _optional_float(v):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f if f == f else None

    def _optional_str(v):
        if v is None:
            return ""
        s = str(v).strip()
        return "" if s.lower() in ("none", "nan") else s

    rows = []
    for o in all_orders:
        status_val = _enum_value(getattr(o, "status", None))
        filled_qty = _optional_float(getattr(o, "filled_qty", None))
        filled_avg_price = _optional_float(getattr(o, "filled_avg_price", None))

        # Only keep genuinely filled orders.
        if str(status_val).lower() != "filled":
            continue
        if not filled_qty or filled_qty <= 0:
            continue
        if not filled_avg_price or filled_avg_price <= 0:
            continue

        rows.append({
            "order_id": str(o.id),
            "client_order_id": _optional_str(getattr(o, "client_order_id", None)),
            "symbol": str(o.symbol).upper(),
            "side": _enum_value(o.side),
            "order_type": _enum_value(getattr(o, "type", None)),
            "status": status_val,
            "qty": _optional_float(getattr(o, "qty", None)),
            "filled_qty": filled_qty,
            "filled_avg_price": filled_avg_price,
            "filled_at": _optional_str(getattr(o, "filled_at", None)),
            "submitted_at": _optional_str(getattr(o, "submitted_at", None)),
            "created_at": _optional_str(getattr(o, "created_at", None)),
        })

    if not rows:
        return pd.DataFrame()

    return pd.DataFrame(rows)


def backfill_trade_journal_from_broker_orders(
    journal_path: Optional[Path] = None,
    lookback_days: int = 60,
    dry_run: bool = False,
    _get_filled_orders_fn=None,  # injectable for tests
) -> dict:
    """Idempotent backfill of broker-filled orders into the trade journal.

    Accounting repair only. This function:
      - Fetches recent filled orders from the Alpaca paper account.
      - Skips any order whose order_id already exists in the journal
        (idempotency guarantee: safe to call multiple times).
      - Appends minimal journal rows for missing fills, tagged with
        sentinel context (UNATTRIBUTED_BROKER_FILL) so they surface in
        attribution without polluting strategy slice statistics.
      - Never places, cancels, or modifies orders.
      - Never reads or infers context from would_enter, stop_adopted,
        dry-run rows, or any non-entry audit rows.

    action convention (matches trade_journal.csv):
        buy  -> action = "enter"
        sell -> action = "exit"

    Returns a summary dict:
        {
            "broker_filled_orders": int,
            "existing_orders_skipped": int,
            "rows_to_add": int,
            "enter_rows_added": int,
            "exit_rows_added": int,
            "unattributed_rows_added": int,
            "dry_run": bool,
        }
    """
    now_utc = datetime.now(timezone.utc).isoformat()

    # --- Load current journal ---
    jpath = Path(journal_path) if journal_path else Path(TRADE_JOURNAL_PATH)
    if jpath.exists():
        try:
            journal = pd.read_csv(jpath, dtype=str)
        except (pd.errors.EmptyDataError, pd.errors.ParserError):
            journal = pd.DataFrame()
    else:
        journal = pd.DataFrame()

    existing_order_ids: set = set()
    if not journal.empty and "order_id" in journal.columns:
        existing_order_ids = {
            str(v).strip()
            for v in journal["order_id"].dropna()
            if str(v).strip().lower() not in ("", "nan", "none")
        }

    # --- Fetch broker filled orders ---
    fetch_fn = _get_filled_orders_fn or get_recent_filled_orders
    broker_orders = fetch_fn(lookback_days=lookback_days)

    n_broker = len(broker_orders) if not broker_orders.empty else 0

    if broker_orders.empty:
        return {
            "broker_filled_orders": 0,
            "existing_orders_skipped": 0,
            "rows_to_add": 0,
            "enter_rows_added": 0,
            "exit_rows_added": 0,
            "unattributed_rows_added": 0,
            "dry_run": dry_run,
        }

    # --- Determine which orders are new ---
    new_rows = []
    n_skipped = 0

    for _, o in broker_orders.iterrows():
        oid = str(o.get("order_id", "")).strip()
        if not oid or oid.lower() in ("nan", "none"):
            n_skipped += 1
            continue
        if oid in existing_order_ids:
            n_skipped += 1
            continue

        side = str(o.get("side", "")).lower()
        if side in ("buy",):
            action = "enter"
        elif side in ("sell",):
            action = "exit"
        else:
            # Ambiguous side (e.g. "short_cover", "short_entry") — still
            # record as broker fill but flag clearly.
            action = "exit" if "sell" in side else "enter"

        filled_at = str(o.get("filled_at", "")).strip()
        submitted_at = str(o.get("submitted_at", "")).strip()
        timestamp_utc = filled_at if filled_at and filled_at.lower() not in ("", "nan", "none") else submitted_at

        client_oid = str(o.get("client_order_id", "")).strip()

        row = {
            "order_id": oid,
            "client_order_id": client_oid,
            "symbol": str(o.get("symbol", "")).upper(),
            "side": side,
            "order_type": str(o.get("order_type", "")).lower(),
            "qty": o.get("filled_qty"),
            "filled_qty": o.get("filled_qty"),
            "filled_avg_price": o.get("filled_avg_price"),
            "filled_at": filled_at,
            "submitted_at": submitted_at,
            "timestamp_utc": timestamp_utc,
            "action": action,
            "status": "filled",
            "broker_status": "filled",
            # Sentinel context — never inferred from audit rows
            "slice_label": "UNATTRIBUTED_BROKER_FILL",
            "slice_combination": "UNATTRIBUTED_BROKER_FILL",
            "timeframe": "unknown",
            "bin_mode": "unknown",
            "context_source": "unattributed_broker_fill",
            # Provenance
            "broker_backfilled": True,
            "backfilled_at_utc": now_utc,
        }
        new_rows.append(row)

    # Sort chronologically so FIFO attribution pairs correctly.
    def _ts_sort_key(r):
        ts = r.get("timestamp_utc", "") or ""
        try:
            return pd.Timestamp(ts, utc=True)
        except Exception:
            return pd.Timestamp.min.tz_localize("UTC")

    new_rows.sort(key=_ts_sort_key)

    n_to_add = len(new_rows)
    n_enter = sum(1 for r in new_rows if r["action"] == "enter")
    n_exit = sum(1 for r in new_rows if r["action"] == "exit")
    n_unattributed = n_to_add  # all v1 backfill rows are unattributed

    summary = {
        "broker_filled_orders": n_broker,
        "existing_orders_skipped": n_skipped,
        "rows_to_add": n_to_add,
        "enter_rows_added": n_enter,
        "exit_rows_added": n_exit,
        "unattributed_rows_added": n_unattributed,
        "dry_run": dry_run,
    }

    if dry_run or n_to_add == 0:
        return summary

    # --- Append rows to journal ---
    new_df = pd.DataFrame(new_rows)
    if journal.empty:
        combined = new_df
    else:
        # sort=False preserves existing column order; new columns are appended
        # at the right without reordering or dropping any existing column.
        combined = pd.concat([journal, new_df], ignore_index=True, sort=False)

    jpath.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(jpath, index=False)

    return summary

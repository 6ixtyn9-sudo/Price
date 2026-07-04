"""Minimal Alpaca paper-trading execution layer.

This module handles:
  - connecting to Alpaca's paper-trading API
  - submitting market orders (entry + exit)
  - tracking open positions and their originating slice signals
  - logging every action to a CSV trade journal

It does NOT:
  - decide when to trade (that's monitor.py)
  - compute features or slice conditions (that's features.py + discovery.py)
  - claim any edge or guarantee any outcome
"""

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

from price.config import ALPACA_API_KEY, ALPACA_SECRET_KEY, DATA_DIR


TRADE_JOURNAL_PATH = DATA_DIR / "trade_journal.csv"


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
            "side": p.side,
            "avg_entry_price": float(p.avg_entry_price),
            "current_price": float(p.current_price),
            "unrealized_pl": float(p.unrealized_pl),
            "unrealized_plpc": float(p.unrealized_plpc),
            "market_value": float(p.market_value),
        })
    return pd.DataFrame(rows)


def submit_entry(symbol: str, qty: int, slice_label: str, side: str = "buy") -> dict:
    client = get_trading_client()
    order_side = OrderSide.BUY if side == "buy" else OrderSide.SELL

    order_data = MarketOrderRequest(
        symbol=symbol.upper(),
        qty=qty,
        side=order_side,
        time_in_force=TimeInForce.DAY,
    )

    try:
        order = client.submit_order(order_data)
        result = {
            "order_id": order.id,
            "symbol": symbol.upper(),
            "qty": qty,
            "side": side,
            "order_type": "market",
            "time_in_force": "day",
            "status": order.status,
            "submitted_at": str(order.submitted_at),
            "slice_label": slice_label,
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
            "slice_label": slice_label,
        }

    _append_journal(result, action="entry")
    return result


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
            "order_id": order.id,
            "symbol": symbol.upper(),
            "qty": qty,
            "side": side,
            "order_type": "market",
            "time_in_force": "day",
            "status": order.status,
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


def close_position(symbol: str) -> dict:
    client = get_trading_client()
    try:
        order = client.close_position(symbol.upper())
        result = {
            "order_id": order.id,
            "symbol": symbol.upper(),
            "side": "close",
            "status": order.status,
            "submitted_at": str(order.submitted_at),
        }
    except Exception as e:
        result = {
            "order_id": None,
            "symbol": symbol.upper(),
            "side": "close",
            "status": "rejected",
            "error": str(e),
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


def load_trade_journal() -> pd.DataFrame:
    journal_path = Path(TRADE_JOURNAL_PATH)
    if not journal_path.exists():
        return pd.DataFrame()
    return pd.read_csv(journal_path)

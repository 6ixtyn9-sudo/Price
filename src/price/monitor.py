"""Live state monitor: polls Alpaca for recent bars, computes features,
bins state, checks slice matches, and gates every signal through the
risk-limits guard before emitting it.

This is the bridge between the research pipeline (warehouse, features,
discovery, validation) and the paper-trading execution layer (trading.py).

It does NOT place orders. It only produces signals (and a list of
exit intents for any open positions whose originating slice's stable
state no longer matches). The paper_trade.py script consumes these
and decides whether to act on them.

Coupling note: it does READ account state (open positions, trade
journal, today's realized P&L) to enforce the risk gate. This is a
deliberate deviation from a pure 'signal-only' monitor and exists
specifically so that no signal that breaches the risk limits can
ever reach trading.submit_entry(). See HANDOVER.md,
"Paper-Trading Exploration Layer (2026-07-02)".
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from price.config import SYMBOLS
from price.data_sources import fetch_alpaca_bars
from price.discovery import bin_features, attach_cross_asset_states
from price.features import compute_price_features
from price.position_manager import check_exits, get_today_realized_pnl
from price.risk_limits import RiskLimits, check_entry
from price.trading import get_open_positions
from price.validation import parse_slice_combination
from price.warehouse import load_from_warehouse


DEFAULT_MONITORED_SLICES: List[dict] = [
    {"symbol": "XLF", "timeframe": "1d", "slice_combination": "state_ext=stretched_up + state_slope=flat"},
    {"symbol": "XLK", "timeframe": "1d", "slice_combination": "cross_TLT_state_slope=uptrend + state_ext=neutral"},
    {"symbol": "XLK", "timeframe": "1h", "slice_combination": "cross_USO_state_vol=mid_vol + state_ext=stretched_down"},
    {"symbol": "SPY", "timeframe": "1h", "slice_combination": "state_session=afternoon + state_slope=downtrend"},
]


def get_current_state(
    symbol: str,
    timeframe: str,
    lookback_bars: int = 200,
    cross_symbols: Optional[Dict[str, List[str]]] = None,
) -> Optional[pd.DataFrame]:
    """Compute the current (most recent) binned state row for `symbol` on
    `timeframe`, with optional cross-asset state fields attached.
    """
    df_warehouse = load_from_warehouse(symbol, timeframe)

    if timeframe in ("15m", "1h"):
        fetch_tf = "15m"
        end_dt = datetime.now(timezone.utc)
        start_dt = end_dt - timedelta(days=10)

        try:
            df_fresh = fetch_alpaca_bars(symbol, fetch_tf, start_dt, end_dt)
            if df_fresh is not None and not df_fresh.empty:
                if not df_warehouse.empty:
                    df_combined = pd.concat([df_warehouse, df_fresh], ignore_index=True)
                    df_combined = df_combined.sort_values("bar_ts_utc")
                    df_combined = df_combined.drop_duplicates(subset=["bar_ts_utc"], keep="last")
                    df_warehouse = df_combined.reset_index(drop=True)
                else:
                    df_warehouse = df_fresh
        except Exception as e:
            print(f"  Fresh Alpaca pull for {symbol} ({timeframe}) failed: {e}")

    elif timeframe == "1d":
        end_dt = datetime.now(timezone.utc)
        start_dt = end_dt - timedelta(days=14)

        try:
            df_fresh = fetch_alpaca_bars(symbol, "1d", start_dt, end_dt)
            if df_fresh is not None and not df_fresh.empty:
                if not df_warehouse.empty:
                    df_combined = pd.concat([df_warehouse, df_fresh], ignore_index=True)
                    df_combined = df_combined.sort_values("bar_ts_utc")
                    df_combined = df_combined.drop_duplicates(subset=["bar_ts_utc"], keep="last")
                    df_warehouse = df_combined.reset_index(drop=True)
                else:
                    df_warehouse = df_fresh
        except Exception as e:
            print(f"  Fresh Alpaca pull for {symbol} (1d) failed: {e}")

    if df_warehouse.empty:
        return None

    if "close_adj" not in df_warehouse.columns:
        df_warehouse["open_adj"] = df_warehouse.get("open_raw", df_warehouse.get("open", np.nan))
        df_warehouse["high_adj"] = df_warehouse.get("high_raw", df_warehouse.get("high", np.nan))
        df_warehouse["low_adj"] = df_warehouse.get("low_raw", df_warehouse.get("low", np.nan))
        df_warehouse["close_adj"] = df_warehouse.get("close_raw", df_warehouse.get("close", np.nan))
        df_warehouse["adj_factor"] = 1.0
        df_warehouse["split_factor"] = 1.0
        df_warehouse["dividend_cash"] = 0.0

    df_tail = df_warehouse.tail(lookback_bars).copy()
    df_tail = df_tail.sort_values("bar_ts_utc").reset_index(drop=True)

    if len(df_tail) < 60:
        print(f"  Only {len(df_tail)} bars for {symbol} ({timeframe}); need ~60 for features.")
        return None

    df_feat = compute_price_features(df_tail)
    df_binned = bin_features(df_feat)

    if cross_symbols:
        for cond_sym, fields in cross_symbols.items():
            df_binned = attach_cross_asset_states(df_binned, cond_sym, timeframe, fields)

    return df_binned.iloc[-1:]


def check_slice_match(current_state: pd.DataFrame, slice_combination: str) -> bool:
    """Return True iff every field=value in the slice matches the current
    state row. False on parse error or any mismatch.
    """
    try:
        slice_filter = parse_slice_combination(slice_combination)
    except ValueError:
        return False
    for field, value in slice_filter.items():
        if field not in current_state.columns:
            print(f"  Field '{field}' not in current state columns; skipping match.")
            return False
        actual = str(current_state[field].iloc[0])
        if actual != value:
            return False
    return True


def extract_cross_symbols(slice_combination: str) -> Dict[str, List[str]]:
    """{cond_symbol: [state_field]} extracted from a slice_combination
    string's cross_* fields. Empty dict if none.
    """
    try:
        filt = parse_slice_combination(slice_combination)
    except ValueError:
        return {}
    cross_syms: Dict[str, List[str]] = {}
    for field in filt:
        if not field.startswith("cross_"):
            continue
        rest = field[len("cross_"):]
        marker = "_state_"
        idx = rest.find(marker)
        if idx == -1:
            continue
        sym = rest[:idx]
        state_field = rest[idx + 1:]
        cross_syms.setdefault(sym, [])
        if state_field not in cross_syms[sym]:
            cross_syms[sym].append(state_field)
    return cross_syms


def _default_qty(close_adj: float, limits: RiskLimits) -> int:
    """Floor(notional_cap / price), at least 1 share if price allows.
    Returns 0 if price is missing or non-positive.
    """
    if close_adj is None or close_adj != close_adj or close_adj <= 0:
        return 0
    return max(0, int(limits.max_notional_per_position // close_adj))


def _load_open_position_slice_labels() -> Dict[str, str]:
    """{symbol: slice_combination} for current open positions by scanning
    the trade journal for the most recent 'entry' per symbol.

    v1 heuristic: one slice per symbol, which matches the current
    4-monitored-slice set. If a symbol has multiple open positions from
    different slices, this collapses them.
    """
    from price.trading import load_trade_journal
    journal = load_trade_journal()
    if journal is None or journal.empty:
        return {}
    if "action" not in journal.columns or "symbol" not in journal.columns:
        return {}

    entries = journal[journal["action"] == "entry"].copy()
    if entries.empty:
        return {}

    entries["ts"] = pd.to_datetime(entries["timestamp_utc"], errors="coerce", utc=True)
    entries = entries.sort_values("ts").dropna(subset=["ts"])
    if entries.empty:
        return {}
    last_per_symbol = entries.groupby("symbol").tail(1)

    return {str(r["symbol"]).upper(): str(r.get("slice_label", ""))
            for _, r in last_per_symbol.iterrows()}


def scan_all_slices(
    slices: Optional[List[dict]] = None,
    limits: Optional[RiskLimits] = None,
    dry_run: bool = False,
) -> List[dict]:
    """Scan all monitored slices; emit tradable signals + exit intents.

    For each slice that matches the current state, compute a default
    position size, then call risk_limits.check_entry. If the guard
    says no, the signal is still emitted (with risk_check.allowed=False
    and the reasons) but is NOT marked tradable=True.

    For each open position, also run position_manager.check_exits
    and return any 'exit' intents.

    Parameters
    ----------
    slices : list of dict, optional
        Override DEFAULT_MONITORED_SLICES.
    limits : RiskLimits, optional
        Override the default risk limits. Used by tests.
    dry_run : bool, default False
        If True, the risk gate is *skipped* and every matched signal
        is emitted as tradable=True. Use only for debugging.
    """
    slices = slices or DEFAULT_MONITORED_SLICES
    limits = limits or RiskLimits()
    signals: List[dict] = []

    open_positions_df = get_open_positions()
    open_positions_list = (
        open_positions_df.to_dict("records")
        if open_positions_df is not None and not open_positions_df.empty
        else []
    )
    today_pnl = get_today_realized_pnl()
    open_position_slice_labels = _load_open_position_slice_labels()

    if open_positions_list:
        print(f"\nChecking exits for {len(open_positions_list)} open position(s)...")
        try:
            exit_intents = check_exits(open_positions_df, open_position_slice_labels)
        except Exception as e:
            print(f"  exit-check failed: {e}")
            exit_intents = []
        for intent in exit_intents:
            print(f"  [{intent.get('action', '?').upper()}] {intent.get('symbol', '')}: {intent.get('reason', '')}")
            signals.append({
                "kind": "exit_intent",
                **intent,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            })

    groups: Dict[tuple, List[dict]] = {}
    for s in slices:
        groups.setdefault((s["symbol"], s["timeframe"]), []).append(s)

    for (symbol, timeframe), group_slices in groups.items():
        print(f"\nScanning {symbol} ({timeframe})...")

        all_cross_symbols: Dict[str, List[str]] = {}
        for s in group_slices:
            for sym, fields in extract_cross_symbols(s["slice_combination"]).items():
                all_cross_symbols.setdefault(sym, [])
                for f in fields:
                    if f not in all_cross_symbols[sym]:
                        all_cross_symbols[sym].append(f)

        current_state = get_current_state(
            symbol,
            timeframe,
            cross_symbols=all_cross_symbols if all_cross_symbols else None,
        )

        if current_state is None:
            print(f"  Could not compute state for {symbol} ({timeframe})")
            for s in group_slices:
                signals.append({
                    "kind": "entry_signal",
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "slice_combination": s["slice_combination"],
                    "matched": False,
                    "tradable": False,
                    "error": "no_state_data",
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                })
            continue

        state_cols = [c for c in current_state.columns if c.startswith("state_") or c.startswith("cross_")]
        state_dict = {c: current_state[c].iloc[0] for c in state_cols}
        print(f"  Current state: {state_dict}")

        try:
            close_adj = float(current_state["close_adj"].iloc[0])
        except (KeyError, ValueError, TypeError):
            close_adj = float("nan")

        for s in group_slices:
            matched = check_slice_match(current_state, s["slice_combination"])
            if not matched:
                signals.append({
                    "kind": "entry_signal",
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "slice_combination": s["slice_combination"],
                    "matched": False,
                    "tradable": False,
                    "current_state": state_dict,
                    "bar_ts_utc": str(current_state["bar_ts_utc"].iloc[0]),
                    "close_adj": close_adj,
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                })
                print(f"  -   {s['slice_combination']}")
                continue

            qty = _default_qty(close_adj, limits) if not dry_run else 0
            if not dry_run:
                risk_result = check_entry(
                    symbol=symbol,
                    qty=qty,
                    price=close_adj,
                    limits=limits,
                    open_positions=open_positions_list,
                    today_realized_pnl=today_pnl,
                )
                tradable = risk_result.allowed
                status_label = "MATCH  " if tradable else "BLOCKED"
                reasons_str = ", ".join(risk_result.reasons) if risk_result.reasons else "risk gate passed"
                risk_payload = {
                    "allowed": risk_result.allowed,
                    "reasons": risk_result.reasons,
                    "details": risk_result.details,
                }
            else:
                tradable = True
                status_label = "MATCH  "
                reasons_str = "dry_run"
                risk_payload = {"allowed": True, "reasons": ["dry_run"], "details": {}}

            signal = {
                "kind": "entry_signal",
                "symbol": symbol,
                "timeframe": timeframe,
                "slice_combination": s["slice_combination"],
                "matched": True,
                "tradable": tradable,
                "current_state": state_dict,
                "bar_ts_utc": str(current_state["bar_ts_utc"].iloc[0]),
                "close_adj": close_adj,
                "suggested_qty": qty,
                "suggested_side": limits.default_side,
                "suggested_notional": (qty * close_adj) if close_adj == close_adj else None,
                "risk_check": risk_payload,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            }
            signals.append(signal)
            verb = "tradable" if tradable else "blocked"
            print(f"  {status_label} {s['slice_combination']}  ({verb}: {reasons_str})")

    return signals

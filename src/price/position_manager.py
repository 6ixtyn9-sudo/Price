"""Position manager for the paper-trading exploration layer.

Decides whether to EXIT an open position. Entry is decided by monitor.py.
Exit policy: state-change (on the *stable* portion of the slice filter).

A position is exited when the non-session, non-DOW, non-month fields
of its originating slice filter no longer match the current state.
For SPY 1h afternoon+downtrend, the exit-relevant field is
state_slope=downtrend; if state_slope flips to flat or uptrend, exit.
The session bucket changing is NOT itself an exit trigger.

This module is read-only with respect to the Alpaca account (it
queries open positions + the trade journal). It returns a list of
exit intents; the actual trading.close_position call is made by
the caller.

It does NOT decide when to enter (that's monitor.py).
It does NOT place orders (that's trading.py).
"""

from datetime import datetime, timezone
from typing import Dict, List, Optional

import pandas as pd

from price.discovery import bin_features
from price.features import compute_price_features
from price.trading import load_trade_journal
from price.validation import parse_slice_combination
from price.warehouse import load_from_warehouse


# Slice filter fields considered "transient" -- they flip every bar and
# therefore do NOT count as a state-change exit trigger.
TRANSIENT_FIELDS: set = {"state_session", "state_dow", "state_month"}


def split_filter(slice_filter: Dict[str, str]) -> tuple:
    """Return (stable_filter, transient_filter)."""
    stable = {k: v for k, v in slice_filter.items() if k not in TRANSIENT_FIELDS}
    transient = {k: v for k, v in slice_filter.items() if k in TRANSIENT_FIELDS}
    return stable, transient


def get_today_realized_pnl(journal: Optional[pd.DataFrame] = None) -> float:
    """Sum of realized P&L for the current UTC day, from the trade journal.

    Approximated as sum of (current_price - avg_entry_price) * qty for
    each 'exit' row in the journal where the timestamp is today (UTC).
    Coarse but enough to drive the daily-loss kill switch.
    """
    if journal is None:
        journal = load_trade_journal()
    if journal is None or journal.empty:
        return 0.0
    if "action" not in journal.columns or "timestamp_utc" not in journal.columns:
        return 0.0
    today = datetime.now(timezone.utc).date()
    exits = journal[journal["action"] == "exit"].copy()
    if exits.empty:
        return 0.0

    def is_today(ts: str) -> bool:
        try:
            t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            return t.date() == today
        except (ValueError, TypeError):
            return False

    exits = exits[exits["timestamp_utc"].apply(is_today)]
    if exits.empty:
        return 0.0

    pnl: float = 0.0
    for _, r in exits.iterrows():
        cur = r.get("current_price")
        entry = r.get("avg_entry_price")
        qty = r.get("qty")
        if cur is None or entry is None or qty is None:
            continue
        try:
            pnl += (float(cur) - float(entry)) * float(qty)
        except (ValueError, TypeError):
            continue
    return pnl


def current_state_to_dict(row: pd.DataFrame) -> Dict[str, str]:
    """Take a 1-row DataFrame (from monitor.get_current_state) and return
    the slice-relevant state fields as a {field: string_value} dict.
    NaN values become empty strings so equality checks work cleanly.
    """
    cols = [c for c in row.columns if c.startswith("state_") or c.startswith("cross_")]
    out: Dict[str, str] = {}
    for c in cols:
        v = row[c].iloc[0]
        out[c] = "" if pd.isna(v) else str(v)
    return out


def _extract_cross_symbols_from_stable(stable: Dict[str, str]) -> Dict[str, List[str]]:
    """{cond_symbol: [state_field]} for any field in `stable` that starts with cross_."""
    cross: Dict[str, List[str]] = {}
    for f in stable:
        if not f.startswith("cross_"):
            continue
        rest = f[len("cross_"):]
        marker = "_state_"
        idx = rest.find(marker)
        if idx == -1:
            continue
        sym = rest[:idx]
        state_field = rest[idx + 1:]
        cross.setdefault(sym, [])
        if state_field not in cross[sym]:
            cross[sym].append(state_field)
    return cross


def check_exits(
    open_positions: pd.DataFrame,
    open_position_slice_labels: Dict[str, str],
) -> List[dict]:
    """For each open position, decide whether to exit on stable-state change.

    Parameters
    ----------
    open_positions : pd.DataFrame
        From `trading.get_open_positions()`. Must have a 'symbol' column.
    open_position_slice_labels : dict
        {symbol: slice_combination_string} -- which slice entered it.

    Returns
    -------
    list of dicts, each with: symbol, slice_combination, action, reason,
    and (when applicable) stable_filter + current_stable_state.
    action is "exit" if the stable filter no longer matches, else "hold".
    """
    if open_positions is None or open_positions.empty:
        return []

    intents: List[dict] = []
    for _, pos in open_positions.iterrows():
        symbol = str(pos["symbol"]).upper()
        slice_combo = open_position_slice_labels.get(symbol)
        if not slice_combo:
            intents.append({
                "symbol": symbol,
                "action": "hold",
                "reason": "no slice label recorded for this position",
            })
            continue
        try:
            slice_filter = parse_slice_combination(slice_combo)
        except ValueError as exc:
            intents.append({
                "symbol": symbol,
                "slice_combination": slice_combo,
                "action": "hold",
                "reason": f"could not parse slice_combination: {exc}",
            })
            continue

        stable, _ = split_filter(slice_filter)
        timeframe = "1h" if "state_session" in slice_filter else "1d"

        df = load_from_warehouse(symbol, timeframe)
        if df.empty or len(df) < 60:
            intents.append({
                "symbol": symbol,
                "slice_combination": slice_combo,
                "action": "hold",
                "reason": f"insufficient warehouse data for {symbol} ({timeframe})",
            })
            continue

        df_feat = compute_price_features(df)
        df_binned = bin_features(df_feat)

        cross_fields = _extract_cross_symbols_from_stable(stable)
        if cross_fields:
            from price.discovery import attach_cross_asset_states
            for cs, fields in cross_fields.items():
                df_binned = attach_cross_asset_states(df_binned, cs, timeframe, fields)

        current = df_binned.iloc[-1:]
        current_state = current_state_to_dict(current)

        mismatches = [
            f"{f}={current_state.get(f, '')} (expected {expected})"
            for f, expected in stable.items()
            if str(current_state.get(f, "")) != expected
        ]

        stable_str = " + ".join(f"{k}={v}" for k, v in stable.items())
        current_stable = {k: current_state.get(k, "") for k in stable}

        if not mismatches:
            intents.append({
                "symbol": symbol,
                "slice_combination": slice_combo,
                "action": "hold",
                "stable_filter": stable_str,
                "current_stable_state": current_stable,
                "reason": "stable filter still matches",
            })
        else:
            intents.append({
                "symbol": symbol,
                "slice_combination": slice_combo,
                "action": "exit",
                "stable_filter": stable_str,
                "current_stable_state": current_stable,
                "reason": "stable filter broken: " + "; ".join(mismatches),
            })

    return intents

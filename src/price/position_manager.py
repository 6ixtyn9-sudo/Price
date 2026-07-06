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

from dataclasses import dataclass
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


@dataclass
class ExitPolicy:
    """Hybrid exit policy configuration.

    A position is exited when ANY condition fires:
      - stable_state_break: the slice's stable (non-transient) filter no
        longer matches the current bar (the original exit logic).
      - horizon_reached: bars held (in the position's own timeframe) >=
        horizon_bars. The validation horizon is fwd_ret_5, so the default 5
        is faithful to the measured edge; holding longer is unvalidated and
        lets winners/losers run past the edge window.

    horizon_bars=0 disables the horizon exit (state-break only = legacy).
    """

    horizon_bars: int = 5


def _parse_ts(ts) -> Optional[datetime]:
    """Best-effort parse of an arbitrary timestamp to tz-aware UTC.

    Returns None on anything unparseable. Used by bar-counting so the exit
    logic never crashes on a malformed journal/warehouse timestamp.
    """
    if ts is None:
        return None
    try:
        t = pd.to_datetime(ts, errors="coerce", utc=True)
    except (ValueError, TypeError):
        return None
    if t is None or pd.isna(t):
        return None
    if getattr(t, "tzinfo", None) is None:
        t = t.replace(tzinfo=timezone.utc)
    return t


def _count_bars_after(entry_ts, df: pd.DataFrame) -> Optional[int]:
    """Number of warehouse bars strictly after ``entry_ts``.

    Counts bars in the position's own timeframe (the warehouse partition the
    caller loaded), so 5 bars on 1d ~= one trading week and 5 bars on 1h ~=
    one session. This is the timeframe-aware bar counting the HANDOVER's
    exit-policy target calls for. Returns None if unknowable (missing entry
    bar, missing bar_ts_utc column, etc.); callers treat None as 'do not
    force a horizon exit on missing data'.
    """
    if entry_ts is None or df is None or df.empty:
        return None
    if "bar_ts_utc" not in df.columns:
        return None
    parsed_entry = _parse_ts(entry_ts)
    if parsed_entry is None:
        return None
    bar_ts = pd.to_datetime(df["bar_ts_utc"], errors="coerce", utc=True)
    return int((bar_ts > parsed_entry).sum())


def _load_entry_context() -> Dict[str, dict]:
    """Per-symbol most-recent accepted entry context from the trade journal.

    Returns {symbol: {slice_combination, timeframe, entry_bar_ts, submitted_at}}.
    Resolves each open position's timeframe and entry bar for the horizon
    exit. Never raises; returns {} if no journal / no entries. Rows written
    before the entry_bar_ts/timeframe columns existed fall back to
    submitted_at as the entry-time proxy, so legacy journal entries still
    get a (slightly approximate) horizon exit.
    """
    try:
        from price.trading import load_trade_journal
        journal = load_trade_journal()
    except Exception:  # noqa: BLE001 - exit logic must never crash the scan
        return {}
    if journal is None or journal.empty:
        return {}
    if "action" not in journal.columns or "symbol" not in journal.columns:
        return {}

    entries = journal[journal["action"] == "entry"].copy()
    if entries.empty:
        return {}
    if "status" in entries.columns:
        entries = entries[entries["status"].astype(str).str.lower() != "rejected"]
    if entries.empty:
        return {}

    sort_col = "submitted_at" if "submitted_at" in entries.columns else "timestamp_utc"
    entries["_sort_ts"] = pd.to_datetime(entries.get(sort_col), errors="coerce", utc=True)
    entries = entries.dropna(subset=["_sort_ts"]).sort_values("_sort_ts")
    if entries.empty:
        return {}
    last = entries.groupby("symbol").tail(1)

    out: Dict[str, dict] = {}
    for _, r in last.iterrows():
        sym = str(r["symbol"]).upper()
        tf = r.get("timeframe")
        ebt = r.get("entry_bar_ts")

        def _clean(v):
            if v is None:
                return None
            try:
                if pd.isna(v):
                    return None
            except (TypeError, ValueError):
                pass
            s = str(v)
            return None if s.lower() in ("nan", "none", "") else s

        out[sym] = {
            "slice_combination": str(r.get("slice_label", "") or ""),
            "timeframe": _clean(tf),
            "entry_bar_ts": _clean(ebt),
            "submitted_at": _clean(r.get("submitted_at")),
        }
    return out


def check_exits(
    open_positions: pd.DataFrame,
    open_position_slice_labels: Dict[str, str],
    exit_policy: Optional[ExitPolicy] = None,
) -> List[dict]:
    """For each open position, decide whether to exit (hybrid policy).

    A position is exited when ANY of:
      - stable_state_break: the slice's stable (non-transient) filter no
        longer matches the current bar.
      - horizon_reached: bars held in the position's own timeframe >=
        exit_policy.horizon_bars (faithful to fwd_ret_5).

    Parameters
    ----------
    open_positions : pd.DataFrame
        From `trading.get_open_positions()`. Must have a 'symbol' column.
    open_position_slice_labels : dict
        {symbol: slice_combination_string} -- which slice entered it.
    exit_policy : ExitPolicy, optional
        Defaults to ExitPolicy() (horizon_bars=5).

    Returns
    -------
    list of dicts, each with: symbol, slice_combination, action, reason,
    bars_held, horizon_bars, timeframe, and (when applicable) stable_filter
    + current_stable_state. action is "exit" if any exit condition fires,
    else "hold".
    """
    if open_positions is None or open_positions.empty:
        return []
    if exit_policy is None:
        exit_policy = ExitPolicy()

    entry_context = _load_entry_context()
    intents: List[dict] = []

    for _, pos in open_positions.iterrows():
        symbol = str(pos["symbol"]).upper()
        ctx = entry_context.get(symbol, {})
        slice_combo = (
            open_position_slice_labels.get(symbol)
            or ctx.get("slice_combination")
        )
        if not slice_combo:
            intents.append({
                "symbol": symbol,
                "action": "hold",
                "reason": "no slice label recorded for this position",
                "bars_held": None,
                "horizon_bars": exit_policy.horizon_bars,
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
                "bars_held": None,
                "horizon_bars": exit_policy.horizon_bars,
            })
            continue

        stable, _ = split_filter(slice_filter)

        # Resolve timeframe from the entry journal when available; fall back
        # to the session-presence heuristic for older journal rows that lack
        # an explicit timeframe column.
        timeframe = ctx.get("timeframe") or (
            "1h" if "state_session" in slice_filter else "1d"
        )

        df = load_from_warehouse(symbol, timeframe)
        if df.empty or len(df) < 60:
            intents.append({
                "symbol": symbol,
                "slice_combination": slice_combo,
                "action": "hold",
                "reason": f"insufficient warehouse data for {symbol} ({timeframe})",
                "bars_held": None,
                "horizon_bars": exit_policy.horizon_bars,
            })
            continue

        # Bars held in the position's own timeframe, counted from the entry
        # signal bar (faithful to the fwd_ret_5 edge horizon). Falls back to
        # order submission time when the signal bar wasn't recorded. None if
        # neither is available -> horizon exit cannot fire.
        entry_bar_ts = ctx.get("entry_bar_ts") or ctx.get("submitted_at")
        bars_held = _count_bars_after(entry_bar_ts, df)

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

        exit_reasons: List[str] = []
        if mismatches:
            exit_reasons.append("stable filter broken: " + "; ".join(mismatches))
        if (
            exit_policy.horizon_bars > 0
            and bars_held is not None
            and bars_held >= exit_policy.horizon_bars
        ):
            exit_reasons.append(
                f"horizon reached: held {bars_held} bars "
                f">= {exit_policy.horizon_bars} ({timeframe})"
            )

        action = "exit" if exit_reasons else "hold"
        if exit_reasons:
            reason = "; ".join(exit_reasons)
        elif bars_held is not None:
            reason = (
                f"stable filter matches; held {bars_held}/"
                f"{exit_policy.horizon_bars} bars ({timeframe})"
            )
        else:
            reason = "stable filter matches; bars held unknown (no entry bar)"

        intents.append({
            "symbol": symbol,
            "slice_combination": slice_combo,
            "action": action,
            "stable_filter": stable_str,
            "current_stable_state": current_stable,
            "bars_held": bars_held,
            "horizon_bars": exit_policy.horizon_bars,
            "timeframe": timeframe,
            "reason": reason,
        })

    return intents

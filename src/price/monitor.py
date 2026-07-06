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
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from price.config import DATA_DIR
# Backward-compatible hook for tests/older monitor workflows that monkeypatch
# a live-refresh fetcher. get_current_state intentionally uses warehouse only.
from price.data_sources import fetch_alpaca_bars  # noqa: F401
from price.discovery import bin_features, attach_cross_asset_states
from price.features import compute_price_features
from price.position_manager import ExitPolicy, check_exits, get_today_realized_pnl
from price.risk_limits import RiskLimits, check_entry, risk_group_key
from price.sizing import compute_position_size
from price.trading import get_open_positions, get_open_orders
from price.validation import parse_slice_combination
from price.warehouse import load_from_warehouse


DEFAULT_MONITORED_SLICES: List[dict] = [
    {"symbol": "XLF", "timeframe": "1d", "slice_combination": "state_ext=stretched_up + state_slope=flat", "side": "long"},
    {"symbol": "XLK", "timeframe": "1d", "slice_combination": "cross_TLT_state_slope=uptrend + state_ext=neutral", "side": "long"},
    {"symbol": "XLK", "timeframe": "1h", "slice_combination": "cross_USO_state_vol=mid_vol + state_ext=stretched_down", "side": "long"},
    {"symbol": "SPY", "timeframe": "1h", "slice_combination": "state_session=afternoon + state_slope=downtrend", "side": "long"},
]
CANDIDATE_LEADERBOARD_PATH = DATA_DIR / "candidate_leaderboard.csv"
MONITORED_SLICES_PATH = DATA_DIR / "monitored_slices.csv"


def _load_clean_survivor_monitored_slices() -> Optional[List[dict]]:
    """Prefer the current clean-survivor leaderboard set when available.

    This keeps the monitor aligned with live_forward_returns.py, which tracks
    the dynamic clean_survivor* universe rather than the older hardcoded V4
    slice list.
    """
    path = Path(CANDIDATE_LEADERBOARD_PATH)
    if not path.exists():
        return None
    try:
        lb = pd.read_csv(path)
    except (pd.errors.EmptyDataError, pd.errors.ParserError):
        return None
    if lb.empty or "triage_bucket" not in lb.columns:
        return None

    clean = lb[lb["triage_bucket"].astype(str).str.startswith("clean_survivor")].copy()
    if clean.empty:
        return None

    out = []
    for _, row in clean.iterrows():
        side = str(row.get("side", "long") or "long").lower()
        if side not in ("long", "short"):
            side = "long"
        out.append(
            {
                "symbol": str(row["symbol"]),
                "timeframe": str(row["timeframe"]),
                "slice_combination": str(row["slice_combination"]),
                "side": side,
            }
        )
    return out or None


def _load_explicit_monitored_slices() -> Optional[List[dict]]:
    """Load an operator-curated monitored_slices.csv when present.

    This is the deployment/watch-list source. It prevents the monitor from
    accidentally scanning every clean_survivor* row in whichever research
    candidate_leaderboard.csv happens to be current.
    """
    path = Path(MONITORED_SLICES_PATH)
    if not path.exists():
        return None

    try:
        rows = pd.read_csv(path)
    except (pd.errors.EmptyDataError, pd.errors.ParserError):
        return None

    if rows.empty:
        return None

    required = {"symbol", "timeframe", "slice_combination"}
    missing = required - set(rows.columns)
    if missing:
        print(f"monitored_slices.csv missing required columns: {sorted(missing)}")
        return None

    out = []
    for _, row in rows.iterrows():
        side = str(row.get("side", "long") or "long").lower()
        if side not in ("long", "short"):
            side = "long"

        out.append(
            {
                "symbol": str(row["symbol"]).upper(),
                "timeframe": str(row["timeframe"]),
                "slice_combination": str(row["slice_combination"]),
                "side": side,
            }
        )

    return out or None


def get_default_monitored_slices() -> List[dict]:
    return (
        _load_explicit_monitored_slices()
        or _load_clean_survivor_monitored_slices()
        or DEFAULT_MONITORED_SLICES
    )


def _drop_incomplete_intraday_rows(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Keep only completed intraday bars.

    `build_warehouse.py` may already have the current partial 1h bar because it
    resamples the latest 15m data. The monitor should reason about the latest
    completed bar, not a still-forming bar.
    """
    if df.empty or timeframe not in ("15m", "1h"):
        return df

    bar_delta = timedelta(minutes=15) if timeframe == "15m" else timedelta(hours=1)
    cutoff = datetime.now(timezone.utc)
    complete = df[df["bar_ts_utc"] + bar_delta <= cutoff].copy()
    return complete.reset_index(drop=True)


def _state_unavailable_context(symbol: str, timeframe: str) -> dict:
    """Best-effort reason payload for an unavailable monitor state."""
    ctx = {
        "reason": "no_completed_state",
        "bar_ts_utc": None,
        "close_adj": None,
        "current_state": {},
    }

    df = load_from_warehouse(symbol, timeframe)
    if df.empty:
        ctx["reason"] = "no_warehouse_data"
        return ctx

    if "close_adj" not in df.columns:
        df = df.copy()
        df["close_adj"] = df.get("close_raw", df.get("close", np.nan))

    df = df.sort_values("bar_ts_utc").reset_index(drop=True)
    df = _drop_incomplete_intraday_rows(df, timeframe)
    if df.empty:
        return ctx

    latest = df.iloc[-1]
    ctx["bar_ts_utc"] = str(latest.get("bar_ts_utc"))
    close_adj = latest.get("close_adj", np.nan)
    try:
        close_adj_float = float(close_adj)
    except (TypeError, ValueError):
        close_adj_float = float("nan")
    ctx["close_adj"] = close_adj_float if close_adj_float == close_adj_float else None

    if pd.isna(close_adj):
        ctx["reason"] = "nan_state_features"
    return ctx


def get_current_state(
    symbol: str,
    timeframe: str,
    lookback_bars: int = 200,
    cross_symbols: Optional[Dict[str, List[str]]] = None,
    required_fields: Optional[List[str]] = None,
) -> Optional[pd.DataFrame]:
    """Compute the most recent completed binned state row.

    Important invariant: monitor state is rebuilt from the already-refreshed
    local warehouse only. Do NOT overlay fresh Alpaca rows here.

    Why:
    - the workflow refreshes the warehouse immediately before paper_trade.py
    - Alpaca's 1d/15m bars are raw-only, while the warehouse carries adjusted
      fields; mixing them here can make the latest row's close_adj/high_adj/
      low_adj NaN and collapse the daily state into NaNs
    - 1h monitoring must use the resampled 1h warehouse partition, not a raw
      concat of 1h bars with fresh 15m bars
    """
    df_warehouse = load_from_warehouse(symbol, timeframe)
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
    df_tail = _drop_incomplete_intraday_rows(df_tail, timeframe)

    if len(df_tail) < 60:
        print(f"  Only {len(df_tail)} completed bars for {symbol} ({timeframe}); need ~60 for features.")
        return None

    latest_close = df_tail["close_adj"].iloc[-1] if "close_adj" in df_tail.columns else np.nan
    if pd.isna(latest_close):
        print(f"  Latest completed bar for {symbol} ({timeframe}) has NaN close_adj.")
        return None

    df_feat = compute_price_features(df_tail)
    df_binned = bin_features(df_feat)

    if cross_symbols:
        for cond_sym, fields in cross_symbols.items():
            df_binned = attach_cross_asset_states(df_binned, cond_sym, timeframe, fields)

    required_fields = required_fields or []
    if required_fields:
        missing = [field for field in required_fields if field not in df_binned.columns]
        if missing:
            print(f"  Missing required state fields for {symbol} ({timeframe}): {missing}")
            return None

        latest = df_binned.iloc[-1:]
        invalid_fields = []
        if latest["close_adj"].isna().iloc[0]:
            invalid_fields.append("close_adj")
        for field in required_fields:
            if latest[field].isna().iloc[0]:
                invalid_fields.append(field)

        if invalid_fields:
            print(
                f"  Latest state row for {symbol} ({timeframe}) has NaN "
                f"required fields: {invalid_fields}"
            )
            return None
        return latest

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
    exit_policy: Optional[ExitPolicy] = None,
) -> List[dict]:
    """Scan all monitored slices; emit tradable signals + exit intents.

    For each slice that matches the current state, compute a default
    position size, then call risk_limits.check_entry. If the guard
    says no, the signal is still emitted (with risk_check.allowed=False
    and the reasons) but is NOT marked tradable=True.

    For each open position, also run position_manager.check_exits
    (hybrid: stable-state-break OR held >= exit_policy.horizon_bars)
    and return any 'exit' intents.

    Parameters
    ----------
    slices : list of dict, optional
        Override the default monitored set. If omitted, prefer the current
        leaderboard clean_survivor* universe and fall back to the older
        hardcoded list only when the leaderboard is unavailable.
    limits : RiskLimits, optional
        Override the default risk limits. Used by tests.
    dry_run : bool, default False
        If True, the risk gate is *skipped* and every matched signal
        is emitted as tradable=True. Use only for debugging.
    exit_policy : ExitPolicy, optional
        Override the default horizon exit (default horizon_bars=5).
    """
    if slices is None:
        slices = get_default_monitored_slices()
    limits = limits or RiskLimits()
    signals: List[dict] = []

    open_positions_df = get_open_positions()
    open_positions_list = (
        open_positions_df.to_dict("records")
        if open_positions_df is not None and not open_positions_df.empty
        else []
    )

    open_orders_df = get_open_orders()
    open_orders_list = (
        open_orders_df.to_dict("records")
        if open_orders_df is not None and not open_orders_df.empty
        else []
    )

    # Treat pending/open orders like open exposure for risk gating so repeated
    # weekend/after-hours scans cannot queue duplicate DAY market orders before
    # the first accepted order has had a chance to fill or expire.
    exposure_for_entry_gate = open_positions_list + open_orders_list

    today_pnl = get_today_realized_pnl()
    open_position_slice_labels = _load_open_position_slice_labels()
    # Risk group per OPEN position (symbol -> stable-condition key). Built from
    # the trade journal's slice labels because broker positions do not carry
    # their originating slice. Used by the correlation-aware allocation cap.
    open_position_risk_groups = {
        sym: risk_group_key(sym, lbl)
        for sym, lbl in open_position_slice_labels.items()
        if lbl
    }

    if open_positions_list:
        print(f"\nChecking exits for {len(open_positions_list)} open position(s)...")
        try:
            exit_intents = check_exits(
                open_positions_df,
                open_position_slice_labels,
                exit_policy=exit_policy,
            )
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
        required_state_fields: List[str] = []
        for s in group_slices:
            try:
                slice_filter = parse_slice_combination(s["slice_combination"])
            except ValueError:
                continue
            for field in slice_filter:
                if field not in required_state_fields:
                    required_state_fields.append(field)
            for sym, fields in extract_cross_symbols(s["slice_combination"]).items():
                all_cross_symbols.setdefault(sym, [])
                for f in fields:
                    if f not in all_cross_symbols[sym]:
                        all_cross_symbols[sym].append(f)

        current_state = get_current_state(
            symbol,
            timeframe,
            cross_symbols=all_cross_symbols if all_cross_symbols else None,
            required_fields=required_state_fields,
        )

        if current_state is None:
            print(f"  Could not compute a completed state for {symbol} ({timeframe})")
            for s in group_slices:
                # Emit a no_state_data entry_signal so paper_trade.py
                # can log it, AND a state_unavailable row for the
                # monitor's audit trail. The two serve different
                # purposes: the entry_signal row is per-slice (each
                # slice in the group gets its own row), while the
                # state_unavailable row is per-(symbol, timeframe).
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
            unavailable_ctx = _state_unavailable_context(symbol, timeframe)
            signals.append({
                "kind": "state_unavailable",
                "symbol": symbol,
                "timeframe": timeframe,
                "slice_combination": group_slices[0]["slice_combination"],
                **unavailable_ctx,
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

            # Edge- and volatility-aware sizing. Falls back to equal-notional
            # when no candidate_leaderboard.csv edge data is available, so the
            # live paper book is unaffected on a fresh/leaderboard-less run.
            size = compute_position_size(
                symbol=symbol,
                timeframe=timeframe,
                slice_combination=s["slice_combination"],
                close_adj=close_adj,
                limits=limits,
            )
            qty = size.qty
            if not dry_run:
                side = str(s.get("side", "long") or "long").lower()
                if side not in ("long", "short"):
                    side = "long"
                suggested_side = "sell" if side == "short" else "buy"
                candidate_group = risk_group_key(symbol, s["slice_combination"])
                risk_result = check_entry(
                    symbol=symbol,
                    qty=qty,
                    price=close_adj,
                    limits=limits,
                    open_positions=exposure_for_entry_gate,
                    today_realized_pnl=today_pnl,
                    side=side,
                    symbol_risk_group=candidate_group,
                    open_position_risk_groups=open_position_risk_groups,
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
                side = str(s.get("side", "long") or "long").lower()
                suggested_side = "sell" if side == "short" else "buy"
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
                "side": side,
                "current_state": state_dict,
                "bar_ts_utc": str(current_state["bar_ts_utc"].iloc[0]),
                "close_adj": close_adj,
                "suggested_qty": qty,
                "suggested_side": suggested_side,
                "suggested_notional": (qty * close_adj) if close_adj == close_adj else None,
                "risk_group": candidate_group,
                **size.to_audit_dict(),
                "risk_check": risk_payload,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            }
            signals.append(signal)
            verb = "tradable" if tradable else "blocked"
            print(f"  {status_label} {s['slice_combination']}  ({verb}: {reasons_str})")

    return signals

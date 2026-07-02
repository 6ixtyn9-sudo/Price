"""Live state monitor: polls Alpaca for recent bars, computes features,
bins state, and checks whether current market conditions match any
validated slice.

This is the bridge between the research pipeline (warehouse, features,
discovery, validation) and the paper-trading execution layer (trading.py).

It does NOT place orders. It only produces signals. The paper_trade.py
script consumes signals and decides whether to act on them.
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from price.config import SYMBOLS
from price.data_sources import fetch_alpaca_bars
from price.features import compute_price_features
from price.discovery import bin_features, attach_cross_asset_states
from price.warehouse import load_from_warehouse
from price.validation import parse_slice_combination


DEFAULT_MONITORED_SLICES = [
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
    df_warehouse = load_from_warehouse(symbol, timeframe)

    if timeframe in ("15m", "1h"):
        fetch_tf = "15m"
        end_dt = datetime.now(timezone.utc)
        start_dt = end_dt - timedelta(days=10)

        try:
            df_fresh = fetch_alpaca_bars(symbol, fetch_tf, start_dt, end_dt)
            if df_fresh is not None and not df_fresh.empty:
                if not df_warehouse.empty and timeframe == "1h":
                    df_combined = pd.concat([df_warehouse, df_fresh], ignore_index=True)
                    df_combined = df_combined.sort_values("bar_ts_utc")
                    df_combined = df_combined.drop_duplicates(subset=["bar_ts_utc"], keep="last")
                    df_warehouse = df_combined.reset_index(drop=True)
                elif not df_warehouse.empty:
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

    current = df_binned.iloc[-1:]
    return current


def check_slice_match(
    current_state: pd.DataFrame,
    slice_combination: str,
) -> bool:
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
    from price.validation import parse_slice_combination as _parse
    try:
        filt = _parse(slice_combination)
    except ValueError:
        return {}

    cross_syms = {}
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
        clean_field = state_field
        cross_syms.setdefault(sym, [])
        if clean_field not in cross_syms[sym]:
            cross_syms[sym].append(clean_field)

    return cross_syms


def scan_all_slices(
    slices: Optional[List[dict]] = None,
) -> List[dict]:
    slices = slices or DEFAULT_MONITORED_SLICES
    signals = []

    groups = {}
    for s in slices:
        key = (s["symbol"], s["timeframe"])
        groups.setdefault(key, []).append(s)

    for (symbol, timeframe), group_slices in groups.items():
        print(f"\nScanning {symbol} ({timeframe})...")

        all_cross_symbols = {}
        for s in group_slices:
            cs = extract_cross_symbols(s["slice_combination"])
            for sym, fields in cs.items():
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
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "slice_combination": s["slice_combination"],
                    "matched": False,
                    "error": "no_state_data",
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                })
            continue

        state_cols = [c for c in current_state.columns if c.startswith("state_") or c.startswith("cross_")]
        state_dict = {c: current_state[c].iloc[0] for c in state_cols}
        print(f"  Current state: {state_dict}")

        for s in group_slices:
            matched = check_slice_match(current_state, s["slice_combination"])
            signal = {
                "symbol": symbol,
                "timeframe": timeframe,
                "slice_combination": s["slice_combination"],
                "matched": matched,
                "current_state": state_dict,
                "bar_ts_utc": str(current_state["bar_ts_utc"].iloc[0]),
                "close_adj": float(current_state["close_adj"].iloc[0]),
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            }
            status = "MATCH" if matched else "  -"
            print(f"  {status} {s['slice_combination']}")
            signals.append(signal)

    return signals

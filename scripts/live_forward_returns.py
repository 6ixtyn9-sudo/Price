"""Live forward-return capture for matched signals.

Reads the paper-trade audit log for matched entry signals, looks up the
exit-time bars (5 and 20 bars after the signal) for each, and writes
the realized forward return to localdata/live_forward_returns.csv.

Design:
  - Idempotent. Re-running appends new rows and updates partial rows.
    No row is ever silently dropped.
  - Data sources: local warehouse first, then on-demand Alpaca fetch
    for any signal whose exit time is not yet in the warehouse.
  - Partial-data rows are kept distinct from completed ones via the
    `partial_data` flag, so a downstream analysis can decide whether
    to include them.
  - Universe: only slices flagged as `clean_survivor*` in the current
    candidate leaderboard. This is the same filter used by
    `--diagnostic-scope clean-survivors`. The watched set evolves with
    the V4 substrate instead of being hardcoded.

This script is read-only against the Alpaca trading API. It only
calls fetch_alpaca_bars for historical bar data. No orders are placed.

See HANDOVER.md "Scheduled Live Capture (2026-07-02)".
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import pandas as pd

from price.config import DATA_DIR
from price.data_sources import fetch_alpaca_bars
from price.warehouse import load_from_warehouse


PAPER_TRADE_LOG_PATH: Path = DATA_DIR / "paper_trade_log.csv"
LEADERBOARD_PATH: Path = DATA_DIR / "candidate_leaderboard.csv"
LIVE_FORWARD_RETURNS_PATH: Path = DATA_DIR / "live_forward_returns.csv"

# Forward-return horizons, in number of bars.
HORIZONS_BARS: List[int] = [5, 20]


def _load_clean_survivor_universe(leaderboard_path: Optional[Path] = None) -> Set[Tuple[str, str, str]]:
    """Return the set of (symbol, timeframe, slice_combination) tuples that
    are clean survivors in the current leaderboard. Used as the watched
    universe for live forward-return capture.
    """
    leaderboard_path = Path(leaderboard_path) if leaderboard_path else LEADERBOARD_PATH
    if not leaderboard_path.exists():
        return set()
    try:
        lb = pd.read_csv(leaderboard_path)
    except (pd.errors.EmptyDataError, pd.errors.ParserError):
        return set()
    if lb.empty or "triage_bucket" not in lb.columns:
        return set()
    clean = lb[lb["triage_bucket"].astype(str).str.startswith("clean_survivor")]
    return {
        (str(r["symbol"]), str(r["timeframe"]), str(r["slice_combination"]))
        for _, r in clean.iterrows()
    }


def _is_matched_signal(row: pd.Series) -> bool:
    """True if this audit row is a matched entry signal we should track."""
    if str(row.get("kind", "")) != "entry_signal":
        return False
    if not bool(row.get("matched", False)):
        return False
    if "slice_combination" not in row or pd.isna(row["slice_combination"]):
        return False
    if "symbol" not in row or pd.isna(row["symbol"]):
        return False
    if "timeframe" not in row or pd.isna(row["timeframe"]):
        return False
    if "bar_ts_utc" not in row or pd.isna(row["bar_ts_utc"]):
        return False
    if "close_adj" not in row or pd.isna(row["close_adj"]):
        return False
    return True


def _resolve_timeframe(timeframe: str) -> str:
    """Map a slice timeframe to the warehouse partition that holds the bars.
    Session-keyed slices are intraday (1h), all others are 1d.
    """
    # Conservative: if a slice has any session reference, it's intraday.
    # The actual call to this function passes the timeframe from the audit
    # log, which is already '15m' / '1h' / '1d'.
    return timeframe


def _get_exit_close(
    symbol: str,
    timeframe: str,
    signal_ts: pd.Timestamp,
    horizon_bars: int,
) -> Tuple[Optional[float], bool]:
    """Look up the close `horizon_bars` after `signal_ts` for `symbol` on
    `timeframe`. Returns (exit_close, partial_data).

    partial_data is True if the exit was unavailable (warehouse too stale
    AND Alpaca fetch failed). The caller records this so a downstream
    analysis can decide whether to include the row.
    """
    partition_tf = _resolve_timeframe(timeframe)
    df = load_from_warehouse(symbol, partition_tf)
    if df is None or df.empty:
        return None, True

    df = df.sort_values("bar_ts_utc").reset_index(drop=True)
    signal_ts = pd.Timestamp(signal_ts)
    if signal_ts.tzinfo is None:
        signal_ts = signal_ts.tz_localize("UTC")
    else:
        signal_ts = signal_ts.tz_convert("UTC")

    # Find the index of the first bar >= signal_ts (i.e. the bar whose open
    # time is at or after the signal timestamp). For a 1d bar at midnight
    # UTC of day D, signal_ts should be that midnight; idx = position of
    # that bar in the sorted frame.
    future = df[df["bar_ts_utc"] >= signal_ts]
    if future.empty:
        # Signal ts is past the warehouse horizon. Try Alpaca backfill.
        return _alpaca_backfill(symbol, partition_tf, signal_ts, horizon_bars)
    start_idx = future.index[0]
    exit_idx = start_idx + horizon_bars
    if exit_idx >= len(df):
        # Warehouse doesn't have enough bars after the signal yet.
        return _alpaca_backfill(symbol, partition_tf, signal_ts, horizon_bars)

    exit_close = float(df.iloc[exit_idx]["close_adj"])
    return exit_close, False


def _alpaca_backfill(
    symbol: str,
    timeframe: str,
    signal_ts: pd.Timestamp,
    horizon_bars: int,
) -> Tuple[Optional[float], bool]:
    """Fetch the most recent bars from Alpaca to backfill an exit that's
    beyond the warehouse horizon. Returns (exit_close, partial_data).
    partial_data is True if the fetch failed.
    """
    try:
        # 1d bars: fetch the last 60 days to be safe. 1h bars: 14 days.
        if timeframe == "1d":
            days = max(60, horizon_bars * 2)
        elif timeframe in ("1h", "15m"):
            days = max(14, horizon_bars)
        else:
            days = 30
        end_dt = datetime.now(timezone.utc)
        start_dt = end_dt - pd.Timedelta(days=days)
        df_fresh = fetch_alpaca_bars(symbol, timeframe, start_dt, end_dt)
        if df_fresh is None or df_fresh.empty:
            return None, True
        df_fresh = df_fresh.sort_values("bar_ts_utc").reset_index(drop=True)
        future = df_fresh[df_fresh["bar_ts_utc"] >= signal_ts]
        if future.empty:
            return None, True
        start_idx = future.index[0]
        exit_idx = start_idx + horizon_bars
        if exit_idx >= len(df_fresh):
            return None, True
        return float(df_fresh.iloc[exit_idx]["close_raw"]), False
    except Exception:
        return None, True


def _load_existing_live_returns(output_path: Optional[Path] = None) -> pd.DataFrame:
    """Load the existing live forward returns CSV. Empty DataFrame if missing."""
    output_path = Path(output_path) if output_path else LIVE_FORWARD_RETURNS_PATH
    if not output_path.exists():
        return pd.DataFrame()
    return pd.read_csv(output_path)


def _row_key(symbol: str, timeframe: str, slice_combo: str, signal_ts: str) -> str:
    """Stable key for one (symbol, timeframe, slice, signal-time) tuple.
    Used to detect updates to existing partial rows."""
    return f"{symbol}|{timeframe}|{slice_combo}|{signal_ts}"


def run_live_capture(
    horizons: Optional[List[int]] = None,
    log_path: Optional[Path] = None,
    leaderboard_path: Optional[Path] = None,
    output_path: Optional[Path] = None,
    universe: Optional[Set[Tuple[str, str, str]]] = None,
) -> pd.DataFrame:
    """Scan the paper-trade audit log and write/append live forward returns.

    Parameters
    ----------
    horizons : list of int, optional
        Override the default [5, 20] bar horizons. Used by tests.
    log_path, leaderboard_path, output_path : Path, optional
        Override the default module-level paths. Used by tests.
    universe : set of tuples, optional
        Override the watched universe. If None, derived from the current
        candidate leaderboard's `clean_survivor*` rows.

    Returns
    -------
    DataFrame : the updated live_forward_returns contents.
    """
    horizons = horizons or HORIZONS_BARS
    log_path = Path(log_path) if log_path else PAPER_TRADE_LOG_PATH
    leaderboard_path = Path(leaderboard_path) if leaderboard_path else LEADERBOARD_PATH
    output_path = Path(output_path) if output_path else LIVE_FORWARD_RETURNS_PATH

    if not log_path.exists():
        print(f"No paper-trade log at {log_path}; nothing to capture.")
        existing = _load_existing_live_returns(output_path)
        return existing

    try:
        log = pd.read_csv(log_path)
    except pd.errors.EmptyDataError:
        print(f"{log_path} is empty; nothing to capture.")
        existing = _load_existing_live_returns(output_path)
        return existing
    if log.empty:
        print(f"{log_path} is empty; nothing to capture.")
        existing = _load_existing_live_returns(output_path)
        return existing

    if universe is None:
        universe = _load_clean_survivor_universe(leaderboard_path)
    if not universe:
        print("No clean_survivor* rows in the current leaderboard; nothing to capture.")
        print("(Re-run scripts/validate_slices.py --candidate-leaderboard to refresh.)")
        existing = _load_existing_live_returns(output_path)
        return existing

    matched = log[log.apply(_is_matched_signal, axis=1)].copy()
    if matched.empty:
        print("No matched entry signals in the paper-trade log.")
        existing = _load_existing_live_returns(output_path)
        return existing

    matched = matched[
        matched.apply(
            lambda r: (str(r["symbol"]), str(r["timeframe"]), str(r["slice_combination"]))
            in universe,
            axis=1,
        )
    ]
    if matched.empty:
        print("No matched signals inside the clean_survivor* universe.")
        existing = _load_existing_live_returns(output_path)
        return existing

    existing = _load_existing_live_returns(output_path)
    existing_keys: Set[str] = set()
    if not existing.empty and "row_key" in existing.columns:
        existing_keys = set(existing["row_key"].astype(str).tolist())

    new_rows: List[dict] = []
    update_rows: List[dict] = []

    for _, sig in matched.iterrows():
        symbol = str(sig["symbol"])
        timeframe = str(sig["timeframe"])
        slice_combo = str(sig["slice_combination"])
        signal_ts = str(sig["bar_ts_utc"])
        signal_close = float(sig["close_adj"])
        key = _row_key(symbol, timeframe, slice_combo, signal_ts)

        row: Dict = {
            "row_key": key,
            "symbol": symbol,
            "timeframe": timeframe,
            "slice_combination": slice_combo,
            "signal_ts_utc": signal_ts,
            "signal_close_adj": signal_close,
            "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        any_partial = False
        for h in horizons:
            exit_close, partial = _get_exit_close(symbol, timeframe, signal_ts, h)
            row[f"exit_close_{h}b"] = exit_close
            if exit_close is not None and not partial and signal_close > 0:
                row[f"fwd_ret_{h}b"] = (exit_close / signal_close) - 1.0
            else:
                row[f"fwd_ret_{h}b"] = None
            if partial:
                any_partial = True
        row["partial_data"] = any_partial

        if key in existing_keys:
            update_rows.append(row)
        else:
            new_rows.append(row)

    # Build the updated DataFrame. Update rows replace existing by key.
    out = existing.copy() if not existing.empty else pd.DataFrame()
    if update_rows:
        update_df = pd.DataFrame(update_rows)
        if out.empty:
            out = update_df
        else:
            keep_mask = ~out["row_key"].astype(str).isin(
                set(r["row_key"] for r in update_rows)
            )
            out = pd.concat([out[keep_mask], update_df], ignore_index=True)
    if new_rows:
        out = pd.concat([out, pd.DataFrame(new_rows)], ignore_index=True)

    if not out.empty:
        out = out.sort_values(["symbol", "timeframe", "slice_combination", "signal_ts_utc"])
        out = out.reset_index(drop=True)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        out.to_csv(output_path, index=False)

    print(
        f"Live capture: {len(new_rows)} new, {len(update_rows)} updated, "
        f"{len(out)} total rows in {output_path}"
    )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture live forward returns for matched paper-trade signals."
    )
    parser.add_argument(
        "--horizons",
        type=int,
        nargs="+",
        default=None,
        help="Forward-return horizons in bars (default: 5 20).",
    )
    args = parser.parse_args()
    run_live_capture(horizons=args.horizons)
    return 0


if __name__ == "__main__":
    sys.exit(main())

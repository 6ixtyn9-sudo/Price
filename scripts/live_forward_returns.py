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
  - Universe source is explicit. Research mode uses only slices flagged
    as `clean_survivor*` in the current candidate leaderboard. Execution
    mode uses the operator-curated `monitored_slices.csv` written by the
    live workflow. There is no silent fallback between the two unless the
    caller explicitly asks for `--universe-source auto`.

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


def _resolve_path(env_name: str, default_name: str) -> Path:
    import os
    custom = os.getenv(env_name)
    if custom:
        return Path(custom)
    return DATA_DIR / default_name

PAPER_TRADE_LOG_PATH: Path = _resolve_path("PAPER_TRADE_LOG_PATH", "paper_trade_log.csv")
LEADERBOARD_PATH: Path = _resolve_path("CANDIDATE_LEADERBOARD_PATH", "candidate_leaderboard.csv")
MONITORED_SLICES_PATH: Path = _resolve_path("MONITORED_SLICES_PATH", "monitored_slices.csv")
LIVE_FORWARD_RETURNS_PATH: Path = _resolve_path("LIVE_FORWARD_RETURNS_PATH", "live_forward_returns.csv")

# Forward-return horizons, in number of bars.
HORIZONS_BARS: List[int] = [5, 20]


def _append_rows(left: pd.DataFrame, right: pd.DataFrame) -> pd.DataFrame:
    """Append CSV-shaped rows without pandas' all-NA concat warning.

    The live ledger is intentionally small, and constructing records here
    preserves the idempotent row-update behavior while remaining stable across
    future pandas dtype changes.
    """
    if left is None or left.empty:
        return right.copy()
    if right is None or right.empty:
        return left.copy()
    return pd.DataFrame.from_records(
        left.to_dict("records") + right.to_dict("records")
    )


# Watched-universe key. bin_mode matters because the same symbol/timeframe/
# slice text can be evaluated under different state-binning semantics.
UniverseKey = Tuple[str, str, str, str]  # symbol, timeframe, slice_combination, bin_mode


def _norm_bin_mode(value) -> str:
    mode = str(value if value is not None and not pd.isna(value) else "insample").lower()
    return mode if mode in ("insample", "rolling") else "insample"


def _load_clean_survivor_universe(leaderboard_path: Optional[Path] = None) -> Set[UniverseKey]:
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
        (
            str(r["symbol"]),
            str(r["timeframe"]),
            str(r["slice_combination"]),
            _norm_bin_mode(r.get("bin_mode", "insample")),
        )
        for _, r in clean.iterrows()
    }


def _load_side_map(
    leaderboard_path: Optional[Path] = None,
    monitored_path: Optional[Path] = None,
    universe_source: str = "leaderboard",
) -> Dict[UniverseKey, str]:
    """Return the execution side for each watched identity.

    The historical universe key remains four fields for backward
    compatibility, while side is carried separately so old callers/tests do
    not break. Forward-return diagnostics and decay logic use this map to
    produce direction-adjusted returns for short candidates.
    """
    path = Path(monitored_path) if universe_source == "monitored" and monitored_path else (
        Path(leaderboard_path) if leaderboard_path else LEADERBOARD_PATH
    )
    if not path.exists():
        return {}
    try:
        rows = pd.read_csv(path)
    except (pd.errors.EmptyDataError, pd.errors.ParserError):
        return {}
    if rows.empty:
        return {}
    out: Dict[UniverseKey, str] = {}
    for _, row in rows.iterrows():
        if universe_source == "leaderboard" and not str(row.get("triage_bucket", "")).startswith("clean_survivor"):
            continue
        key = (
            str(row.get("symbol", "")),
            str(row.get("timeframe", "")),
            str(row.get("slice_combination", "")),
            _norm_bin_mode(row.get("bin_mode", "insample")),
        )
        side = str(row.get("side", "long") or "long").lower()
        out[key] = side if side in {"long", "short"} else "long"
    return out


def _load_monitored_universe(monitored_path: Optional[Path] = None) -> Set[UniverseKey]:
    """Return the explicit deployment/watch universe from monitored_slices.csv.

    The live workflow deliberately stopped refreshing candidate_leaderboard.csv
    on every execution pass. In that execution-only mode, the authoritative
    watched set is localdata/monitored_slices.csv, not a possibly-absent
    leaderboard. Callers must request this source explicitly; it is not a
    silent fallback in normal research mode.
    """
    monitored_path = Path(monitored_path) if monitored_path else MONITORED_SLICES_PATH
    if not monitored_path.exists():
        return set()
    try:
        rows = pd.read_csv(monitored_path)
    except (pd.errors.EmptyDataError, pd.errors.ParserError):
        return set()
    required = {"symbol", "timeframe", "slice_combination"}
    if rows.empty or not required.issubset(rows.columns):
        return set()
    return {
        (
            str(r["symbol"]),
            str(r["timeframe"]),
            str(r["slice_combination"]),
            _norm_bin_mode(r.get("bin_mode", "insample")),
        )
        for _, r in rows.iterrows()
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


def _dedupe_by_row_key(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse duplicate row_key rows, keeping the latest capture.

    Older live_capture versions could create multiple rows for the same
    signal because repeated matched audit rows shared one row_key. The durable
    artifact should be one row per signal key; later runs update that row.

    Migration: existing rows may carry pre-canonicalization keys (full 1d
    equity timestamps such as Tiingo midnight vs Alpaca 04:00 UTC for the
    same session). Re-key them through the current _row_key so old rows merge
    with -- rather than duplicate -- new canonical rows.
    """
    if df is None or df.empty or "row_key" not in df.columns:
        return df
    out = df.copy()
    needed = {"symbol", "timeframe", "slice_combination", "signal_ts_utc"}
    if needed.issubset(out.columns):
        out["row_key"] = out.apply(
            lambda r: _row_key(
                str(r["symbol"]),
                str(r["timeframe"]),
                str(r["slice_combination"]),
                str(r["signal_ts_utc"]),
                _norm_bin_mode(r.get("bin_mode", "insample")),
            ),
            axis=1,
        )
    if "captured_at_utc" in out.columns:
        out["_captured_sort"] = pd.to_datetime(out["captured_at_utc"], errors="coerce", utc=True)
        out = out.sort_values("_captured_sort", na_position="first")
        out = out.drop(columns=["_captured_sort"])
    return out.drop_duplicates(subset=["row_key"], keep="last").reset_index(drop=True)


def _canonical_signal_ts(symbol: str, timeframe: str, signal_ts: str) -> str:
    """Canonicalize a signal timestamp for row-key identity.

    Daily EQUITY bars represent one market session, but different sources
    stamp them differently (Tiingo: midnight UTC; Alpaca: 04:00 UTC). Without
    canonicalization the SAME session signal gets two row keys and the
    forward-return ledger double-counts it. Collapse 1d equity timestamps to
    the UTC calendar date. Crypto trades 24/7 (a 1d bar boundary is a real
    clock time) and intraday bars have real clock times -- both keep the full
    timestamp.
    """
    if str(timeframe) != "1d" or "/" in str(symbol):
        return str(signal_ts)
    try:
        ts = pd.Timestamp(signal_ts)
    except (TypeError, ValueError):
        return str(signal_ts)
    if pd.isna(ts):
        return str(signal_ts)
    return str(ts.date())


def _row_key(symbol: str, timeframe: str, slice_combo: str, signal_ts: str,
             bin_mode: str = "insample") -> str:
    """Stable key for one (symbol, timeframe, bin mode, slice, signal-time).

    Backward compatibility: insample keeps the historical key shape so old
    partial rows update in place. Non-insample modes include the mode to avoid
    collisions when the same slice text is monitored under rolling bins.

    1d equity signal timestamps are canonicalized to the market date so the
    same session recorded by different sources (Tiingo midnight UTC vs Alpaca
    04:00 UTC) cannot create duplicate ledger rows.
    """
    bin_mode = _norm_bin_mode(bin_mode)
    signal_ts = _canonical_signal_ts(symbol, timeframe, signal_ts)
    if bin_mode == "insample":
        return f"{symbol}|{timeframe}|{slice_combo}|{signal_ts}"
    return f"{symbol}|{timeframe}|{bin_mode}|{slice_combo}|{signal_ts}"


def run_live_capture(
    horizons: Optional[List[int]] = None,
    log_path: Optional[Path] = None,
    leaderboard_path: Optional[Path] = None,
    output_path: Optional[Path] = None,
    universe: Optional[Set[UniverseKey]] = None,
    monitored_path: Optional[Path] = None,
    universe_source: str = "leaderboard",
) -> pd.DataFrame:
    """Scan the paper-trade audit log and write/append live forward returns.

    Parameters
    ----------
    horizons : list of int, optional
        Override the default [5, 20] bar horizons. Used by tests.
    log_path, leaderboard_path, output_path : Path, optional
        Override the default module-level paths. Used by tests.
    universe : set of tuples, optional
        Override the watched universe directly.
    monitored_path : Path, optional
        Override the explicit monitored-slices path. Used by tests.
    universe_source : {"leaderboard", "monitored", "auto"}
        Source used when `universe` is not provided. "leaderboard" preserves
        the research default (clean_survivor* only). "monitored" is the live
        execution workflow mode. "auto" tries leaderboard then monitored, but
        should only be used deliberately for diagnostics.

    Returns
    -------
    DataFrame : the updated live_forward_returns contents.
    """
    horizons = horizons or HORIZONS_BARS
    log_path = Path(log_path) if log_path else PAPER_TRADE_LOG_PATH
    leaderboard_path = Path(leaderboard_path) if leaderboard_path else LEADERBOARD_PATH
    output_path = Path(output_path) if output_path else LIVE_FORWARD_RETURNS_PATH
    monitored_path = Path(monitored_path) if monitored_path else MONITORED_SLICES_PATH

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

    side_by_key: Dict[UniverseKey, str] = {}
    if universe is None:
        universe_source = str(universe_source or "leaderboard").lower()
        side_by_key = _load_side_map(
            leaderboard_path=leaderboard_path,
            monitored_path=monitored_path,
            universe_source=universe_source,
        )
        if universe_source == "leaderboard":
            universe = _load_clean_survivor_universe(leaderboard_path)
            if not universe:
                print("No clean_survivor* rows in the current leaderboard; nothing to capture.")
                print("(Use --universe-source monitored for the execution watch list.)")
                existing = _load_existing_live_returns(output_path)
                return existing
        elif universe_source == "monitored":
            universe = _load_monitored_universe(monitored_path)
            if not universe:
                print("No monitored_slices.csv universe; nothing to capture.")
                print("(Write monitored_slices.csv or use --universe-source leaderboard.)")
                existing = _load_existing_live_returns(output_path)
                return existing
        elif universe_source == "auto":
            universe = _load_clean_survivor_universe(leaderboard_path)
            if not universe:
                universe = _load_monitored_universe(monitored_path)
                if universe:
                    print("No clean_survivor* leaderboard rows; using monitored_slices.csv universe (auto mode).")
            if not universe:
                print("No clean_survivor* rows and no monitored_slices.csv universe; nothing to capture.")
                existing = _load_existing_live_returns(output_path)
                return existing
        else:
            raise ValueError("universe_source must be one of: leaderboard, monitored, auto")

    matched = log[log.apply(_is_matched_signal, axis=1)].copy()
    if matched.empty:
        print("No matched entry signals in the paper-trade log.")
        existing = _load_existing_live_returns(output_path)
        return existing

    matched = matched[
        matched.apply(
            lambda r: (
                str(r["symbol"]),
                str(r["timeframe"]),
                str(r["slice_combination"]),
                _norm_bin_mode(r.get("bin_mode", "insample")),
            ) in universe,
            axis=1,
        )
    ]
    if matched.empty:
        print("No matched signals inside the watched universe.")
        existing = _load_existing_live_returns(output_path)
        return existing

    # One forward-return row per unique signal key. paper_trade.py can log the
    # same matched state repeatedly across scheduled scans; those are audit
    # observations of the same bar/slice signal, not distinct forward-return
    # labels. Collapse before scoring so the output remains idempotent.
    matched = matched.copy()
    matched["_bin_mode"] = matched.apply(lambda r: _norm_bin_mode(r.get("bin_mode", "insample")), axis=1)
    matched["_row_key"] = matched.apply(
        lambda r: _row_key(
            str(r["symbol"]),
            str(r["timeframe"]),
            str(r["slice_combination"]),
            str(r["bar_ts_utc"]),
            r["_bin_mode"],
        ),
        axis=1,
    )
    matched = matched.drop_duplicates(subset=["_row_key"], keep="last").reset_index(drop=True)

    existing = _dedupe_by_row_key(_load_existing_live_returns(output_path))
    existing_keys: Set[str] = set()
    if not existing.empty and "row_key" in existing.columns:
        existing_keys = set(existing["row_key"].astype(str).tolist())

    new_rows: List[dict] = []
    update_rows: List[dict] = []

    for _, sig in matched.iterrows():
        symbol = str(sig["symbol"])
        timeframe = str(sig["timeframe"])
        slice_combo = str(sig["slice_combination"])
        bin_mode = _norm_bin_mode(sig.get("_bin_mode", sig.get("bin_mode", "insample")))
        signal_ts = str(sig["bar_ts_utc"])
        signal_close = float(sig["close_adj"])
        identity = (symbol, timeframe, slice_combo, bin_mode)
        side = side_by_key.get(identity, str(sig.get("side", "long") or "long").lower())
        if side not in {"long", "short"}:
            side = "long"
        key = str(sig.get("_row_key") or _row_key(symbol, timeframe, slice_combo, signal_ts, bin_mode))

        row: Dict = {
            "row_key": key,
            "symbol": symbol,
            "timeframe": timeframe,
            "slice_combination": slice_combo,
            "side": side,
            "bin_mode": bin_mode,
            "signal_ts_utc": signal_ts,
            "signal_close_adj": signal_close,
            "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        any_partial = False
        for h in horizons:
            exit_close, partial = _get_exit_close(symbol, timeframe, signal_ts, h)
            row[f"exit_close_{h}b"] = exit_close
            if exit_close is not None and not partial and signal_close > 0:
                raw_return = (exit_close / signal_close) - 1.0
                row[f"fwd_ret_{h}b"] = raw_return
                # Preserve the historical raw return column while adding a
                # direction-adjusted field for lifecycle decay analysis.
                row[f"tradeable_fwd_ret_{h}b"] = -raw_return if side == "short" else raw_return
            else:
                row[f"fwd_ret_{h}b"] = None
                row[f"tradeable_fwd_ret_{h}b"] = None
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
            retained = out.loc[keep_mask].copy()
            out = _append_rows(retained, update_df)
    if new_rows:
        out = _append_rows(out, pd.DataFrame(new_rows))

    if not out.empty:
        sort_cols = [c for c in ["symbol", "timeframe", "bin_mode", "slice_combination", "signal_ts_utc"] if c in out.columns]
        out = out.sort_values(sort_cols)
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
    parser.add_argument(
        "--universe-source",
        choices=["leaderboard", "monitored", "auto"],
        default="auto",
        help=(
            "Watched universe source. leaderboard = clean_survivor* research "
            "default; monitored = explicit monitored_slices.csv deployment set; "
            "auto = try leaderboard then monitored. Default: auto."
        ),
    )
    args = parser.parse_args()
    run_live_capture(horizons=args.horizons, universe_source=args.universe_source)
    return 0


if __name__ == "__main__":
    sys.exit(main())

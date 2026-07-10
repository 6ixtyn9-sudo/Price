"""Build isolated regime/opportunity observations for research refresh.

Read-only with respect to trading and operational logs. This script never
places orders, changes monitored_slices.csv, or runs discovery. It converts
the existing paper-trade audit/journal into a research-only summary keyed by
symbol, timeframe, slice, bin mode, and observed regime.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable

import pandas as pd

from price.attribution import reconstruct_round_trips


DEFAULT_OUTPUT = Path("localdata/research/regime_opportunity_rates.csv")
PAPER_LOG_PATH = Path("localdata/paper_trade_log.csv")
TRADE_JOURNAL_PATH = Path("localdata/trade_journal.csv")
CONFIRMED_STATUSES = {"filled", "partially_filled", "closed"}


def _truthy(value) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def _clean(value, default="") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return default if text.lower() in {"", "nan", "none"} else text


def _order_ids(df: pd.DataFrame, action_values: Iterable[str]) -> set[str]:
    if df is None or df.empty or "action" not in df.columns or "order_id" not in df.columns:
        return set()
    actions = {str(v).lower() for v in action_values}
    out = set()
    for _, row in df.iterrows():
        if str(row.get("action", "")).lower() not in actions:
            continue
        order_id = _clean(row.get("order_id"))
        if order_id:
            out.add(order_id)
    return out


def _confirmed_entry_ids(journal: pd.DataFrame) -> set[str]:
    if journal is None or journal.empty:
        return set()
    out = set()
    for _, row in journal.iterrows():
        if str(row.get("action", "")).lower() != "entry":
            continue
        status = _clean(row.get("broker_status", row.get("status", ""))).lower()
        qty = row.get("filled_qty", row.get("qty", 0))
        try:
            qty = float(qty)
        except (TypeError, ValueError):
            qty = 0.0
        order_id = _clean(row.get("order_id"))
        if order_id and status in CONFIRMED_STATUSES and qty > 0:
            out.add(order_id)
    return out


def _dedupe_signals(log: pd.DataFrame) -> pd.DataFrame:
    """Collapse repeated scans of the same signal bar into one opportunity."""
    if log.empty:
        return log
    out = log.copy()
    for col, default in (("bin_mode", "insample"), ("bar_ts_utc", "")):
        if col not in out.columns:
            out[col] = default
        out[col] = out[col].fillna(default).astype(str)
    key_cols = [
        "symbol", "timeframe", "slice_combination", "bin_mode", "bar_ts_utc",
    ]
    out["_logged"] = pd.to_datetime(
        out.get("logged_at_utc", out.get("timestamp_utc")), errors="coerce", utc=True
    )
    return out.sort_values("_logged").drop_duplicates(key_cols, keep="last")


def build_regime_opportunity_rates(
    paper_log_path: Path = PAPER_LOG_PATH,
    trade_journal_path: Path = TRADE_JOURNAL_PATH,
) -> pd.DataFrame:
    """Return regime/opportunity/fill/completion summary rows."""
    if not Path(paper_log_path).exists():
        return pd.DataFrame()
    try:
        log = pd.read_csv(paper_log_path)
    except (pd.errors.EmptyDataError, pd.errors.ParserError):
        return pd.DataFrame()
    if log.empty:
        return pd.DataFrame()

    log = log[log.get("kind", "").astype(str) == "entry_signal"].copy()
    if log.empty:
        return pd.DataFrame()
    log = _dedupe_signals(log)

    try:
        journal = pd.read_csv(trade_journal_path) if Path(trade_journal_path).exists() else pd.DataFrame()
    except (pd.errors.EmptyDataError, pd.errors.ParserError):
        journal = pd.DataFrame()

    filled_entry_ids = _confirmed_entry_ids(journal)
    completed_round_trips = reconstruct_round_trips(journal)

    # Use exact entry order IDs to attach completed trades to the regime that
    # was observed when the entry signal fired.
    signal_by_order = {}
    for _, row in log.iterrows():
        order_id = _clean(row.get("order_id"))
        if order_id:
            signal_by_order[order_id] = row

    completed_by_group = {}
    for rt in completed_round_trips:
        signal = signal_by_order.get(rt.entry_order_id, {})
        group = (
            rt.symbol,
            rt.timeframe,
            rt.slice_combination,
            rt.bin_mode,
            _clean(signal.get("regime"), "unknown"),
        )
        completed_by_group[group] = completed_by_group.get(group, 0) + 1

    rows = []
    group_cols = [
        "symbol", "timeframe", "slice_combination", "bin_mode", "regime",
    ]
    for group, frame in log.groupby(group_cols, dropna=False):
        symbol, timeframe, slice_combo, bin_mode, regime = [
            _clean(value, "unknown" if index == 4 else "")
            for index, value in enumerate(group)
        ]
        matched = frame[frame["matched"].map(_truthy)] if "matched" in frame else frame.iloc[0:0]
        risk_blocked = matched[
            ~matched.get("tradable", pd.Series(False, index=matched.index)).map(_truthy)
        ] if not matched.empty else matched
        order_ids = _order_ids(frame, {"enter"})
        filled_orders = order_ids & filled_entry_ids
        key = (symbol, timeframe, slice_combo, bin_mode, regime)
        completed = completed_by_group.get(key, 0)
        matched_count = len(matched)
        rows.append({
            "symbol": symbol,
            "timeframe": timeframe,
            "slice_combination": slice_combo,
            "bin_mode": bin_mode,
            "regime": regime,
            "regime_symbol": _clean(frame.iloc[-1].get("regime_symbol")),
            "observed_signal_bars": len(frame),
            "matched_opportunities": matched_count,
            "risk_blocked_opportunities": len(risk_blocked),
            "orders_submitted": len(order_ids),
            "orders_filled": len(filled_orders),
            "completed_round_trips": completed,
            "risk_block_rate": (len(risk_blocked) / matched_count if matched_count else None),
            "order_fill_rate": (len(filled_orders) / len(order_ids) if order_ids else None),
            "completion_rate": (completed / len(filled_orders) if filled_orders else None),
        })

    return pd.DataFrame(rows).sort_values(
        ["symbol", "timeframe", "slice_combination", "bin_mode", "regime"]
    ).reset_index(drop=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build isolated regime/opportunity research observations.")
    parser.add_argument("--paper-log", type=Path, default=PAPER_LOG_PATH)
    parser.add_argument("--trade-journal", type=Path, default=TRADE_JOURNAL_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    output = build_regime_opportunity_rates(args.paper_log, args.trade_journal)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    output.to_csv(args.output, index=False)
    print(f"Saved {len(output)} regime/opportunity rows to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

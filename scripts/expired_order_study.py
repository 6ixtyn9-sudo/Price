"""Expired-order opportunity study (2026-10 ROI pass).

The entry funnel fills fewer than half of submitted limit orders. A
limit at the signal close systematically fills falling knives and
misses follow-through winners (adverse selection). Before tuning the
winner-capture premium, measure what the MISSED orders would have done:

For every expired entry in the trade journal, compute the
direction-adjusted forward return at the slice's own horizon from the
local warehouse, and compare against the realized expectancy of filled
round-trips (attribution). If expired expectancy >> filled expectancy,
the premium is too tight; if similar, the funnel is not the problem.

Read-only. Writes one row per expired order to the output CSV.

Usage:
    python3 scripts/expired_order_study.py
    python3 scripts/expired_order_study.py --json
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import argparse
import json
from pathlib import Path

import pandas as pd

from price.attribution import attribute_pnl, load_trade_journal
from price.config import DATA_DIR
from price.roi_guards import forward_return_from_bars
from price.warehouse import load_from_warehouse


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--journal", type=Path, default=DATA_DIR / "trade_journal.csv")
    parser.add_argument("--output", type=Path,
                        default=DATA_DIR / "expired_order_forward.csv")
    parser.add_argument("--horizon", type=int, default=0,
                        help="Forward horizon in bars; 0 = per-order exit_horizon or 5.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    journal = load_trade_journal(args.journal)
    rows = []
    if journal is not None and not journal.empty and "action" in journal.columns:
        status = journal.get("broker_status", journal.get("status", ""))
        status = status.astype(str).str.lower()
        expired = journal[(journal["action"] == "entry")
                          & (status == "expired")].copy()
        warehouse_cache = {}
        for _, r in expired.iterrows():
            symbol = str(r.get("symbol", "")).upper()
            timeframe = str(r.get("timeframe", "") or "1d")
            side = "short" if str(r.get("side", "")).lower() in ("sell", "short") else "long"
            horizon = args.horizon
            if horizon <= 0:
                try:
                    horizon = int(float(r.get("exit_horizon")))
                except (TypeError, ValueError):
                    horizon = 5
            key = (symbol, timeframe)
            if key not in warehouse_cache:
                warehouse_cache[key] = load_from_warehouse(symbol, timeframe)
            entry_ts = r.get("entry_bar_ts") or r.get("submitted_at")
            fwd = forward_return_from_bars(
                warehouse_cache[key], entry_ts, horizon, side)
            rows.append({
                "symbol": symbol, "timeframe": timeframe, "side": side,
                "slice_combination": str(r.get("slice_label", "") or ""),
                "entry_bar_ts": str(entry_ts or ""),
                "submitted_at": str(r.get("submitted_at", "") or ""),
                "limit_price": r.get("limit_price"),
                "horizon_bars": horizon,
                "fwd_ret_horizon": fwd,
            })

    out = pd.DataFrame(rows)
    measurable = out.dropna(subset=["fwd_ret_horizon"]) if not out.empty else out
    filled_mean = None
    try:
        report = attribute_pnl(journal=journal)
        rts = report.get("round_trips") or []
        if rts:
            filled_mean = sum(x["gross_return"] for x in rts) / len(rts)
    except Exception:  # noqa: BLE001 - study must still report the expired leg
        filled_mean = None

    summary = {
        "n_expired": int(len(out)),
        "n_measurable": int(len(measurable)),
        "expired_mean_fwd_ret": (float(measurable["fwd_ret_horizon"].mean())
                                 if len(measurable) else None),
        "expired_win_rate": (float((measurable["fwd_ret_horizon"] > 0).mean())
                             if len(measurable) else None),
        "filled_realized_mean_ret": filled_mean,
    }
    gap = None
    if summary["expired_mean_fwd_ret"] is not None and filled_mean is not None:
        gap = summary["expired_mean_fwd_ret"] - filled_mean
    summary["expired_minus_filled"] = gap

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        out.to_csv(args.output, index=False)

    if args.json:
        print(json.dumps(summary, indent=2, default=str))
    else:
        print("=" * 72)
        print("EXPIRED-ORDER OPPORTUNITY STUDY")
        print("=" * 72)
        print(f"  Expired entries:        {summary['n_expired']}")
        print(f"  Measurable (warehouse): {summary['n_measurable']}")
        if summary["expired_mean_fwd_ret"] is not None:
            print(f"  Expired fwd expectancy: {summary['expired_mean_fwd_ret']*100:+.3f}% "
                  f"(win {summary['expired_win_rate']*100:.0f}%)")
        if filled_mean is not None:
            print(f"  Filled realized mean:   {filled_mean*100:+.3f}%")
        if gap is not None:
            verdict = ("premium too tight — missed orders outperform fills; "
                       "widen winner-capture in a shadow test"
                       if gap > 0.001 else
                       "no evidence the funnel is adverse-selecting; "
                       "do not widen the premium on this data")
            print(f"  Gap (expired - filled): {gap*100:+.3f}%  -> {verdict}")
        print(f"  Rows written: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

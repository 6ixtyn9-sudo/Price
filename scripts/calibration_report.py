"""Conviction calibration report (2026-10 ROI pass).

Answers one question: does the research expected return predict
realized returns well enough for conviction sizing to move capital?

Joins attribution's per-slice expected-vs-realized pairs and reports
Pearson correlation + OLS slope (roi_guards.calibration_stats). The
verdict is deliberately conservative:

  insufficient                      < 10 slices with both numbers:
                                    keep collecting; sizing unverified.
  calibrated                        corr >= 0.2 and slope > 0:
                                    conviction may differentiate.
  uncalibrated_freeze_conviction    otherwise: run paper_trade with
                                    --equal-notional until recalibrated
                                    (on live-forward returns, not the
                                    backtest that produced the edge).

Read-only.

Usage:
    python3 scripts/calibration_report.py
    python3 scripts/calibration_report.py --json
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import argparse
import json
from pathlib import Path

from price.attribution import attribute_pnl, load_trade_journal
from price.config import DATA_DIR
from price.roi_guards import calibration_stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--journal", type=Path, default=DATA_DIR / "trade_journal.csv")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    journal = load_trade_journal(args.journal)
    report = attribute_pnl(journal=journal)
    pairs = []
    for a in report.get("by_slice") or []:
        if a.get("expected_return") is not None:
            pairs.append((a["expected_return"], a["mean_gross_return"],
                          a["n_round_trips"]))
    cal = calibration_stats(pairs)
    cal["n_round_trips_total"] = report["summary"]["n_round_trips"]
    cal["total_realized_pnl"] = report["summary"]["total_realized_pnl"]
    cal["eras"] = report.get("eras")

    if args.json:
        print(json.dumps(cal, indent=2, default=str))
    else:
        print("=" * 72)
        print("CONVICTION CALIBRATION REPORT")
        print("=" * 72)
        print(f"  Slices with expected+realized: {cal['n_slices']}")
        print(f"  Pearson(expected, realized):   {cal['pearson_expected_realized']}")
        print(f"  Slope realized~expected:       {cal['slope_realized_on_expected']}")
        print(f"  Verdict: {cal['verdict']}")
        if cal["verdict"] == "uncalibrated_freeze_conviction":
            print("  -> Run paper_trade.py with --equal-notional (freeze "
                  "conviction) until live-forward recalibration clears.")
        elif cal["verdict"] == "insufficient":
            print("  -> Too few paired slices to judge sizing; treat "
                  "conviction as unverified, keep notionals small/equal.")
        else:
            print("  -> Conviction has earned differentiation on this sample.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

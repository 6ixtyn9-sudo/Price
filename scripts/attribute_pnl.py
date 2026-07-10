"""One-command P&L attribution report (lever 5).

Reads the trade journal, paper trade log, and candidate leaderboard, then
prints a per-slice realized-P&L attribution report. Read-only: places no
orders, modifies no journals.

Usage:
    python3 scripts/attribute_pnl.py
    python3 scripts/attribute_pnl.py --leaderboard localdata/candidate_leaderboard_1d_tiingo_liquid236.csv
    python3 scripts/attribute_pnl.py --json     # machine-readable

See HANDOVER.md "ROI Refinement — P&L Attribution".
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import argparse
import json
from pathlib import Path

from price.attribution import attribute_pnl, format_report, load_trade_journal
from price.trading import get_open_positions, reconcile_trade_journal


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Print a per-slice realized P&L attribution report."
    )
    parser.add_argument("--journal", type=str, default=None,
                        help="Override the trade journal path.")
    parser.add_argument("--leaderboard", type=str, default=None,
                        help="Override the candidate leaderboard path (for "
                        "expected-vs-realized comparison).")
    parser.add_argument("--paper-log", type=str, default=None,
                        help="Override the paper_trade_log path (for realized "
                        "slippage measurement).")
    parser.add_argument("--sync-broker", action="store_true",
                        help="Reconcile journaled order IDs with Alpaca and use broker positions as the authoritative exposure count. Read-only; no orders are placed.")
    parser.add_argument("--json", action="store_true",
                        help="Emit the report as JSON instead of text.")
    args = parser.parse_args()

    leaderboard_path = Path(args.leaderboard) if args.leaderboard else None
    paper_log_path = Path(args.paper_log) if args.paper_log else None
    journal = None
    broker_positions = None
    journal_path = Path(args.journal) if args.journal else None
    if args.sync_broker:
        reconcile_trade_journal(path=journal_path)
        broker_positions = get_open_positions()
    if args.journal:
        journal = load_trade_journal(journal_path)

    report = attribute_pnl(
        journal=journal,
        leaderboard_path=leaderboard_path,
        paper_log_path=paper_log_path,
        broker_positions=broker_positions,
    )

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(format_report(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())

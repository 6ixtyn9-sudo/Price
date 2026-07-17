"""One-command P&L attribution report (lever 5).

Reads the trade journal, paper trade log, and candidate leaderboard, then
prints a per-slice realized-P&L attribution report. Read-only: places no
orders, modifies no journals.

Usage:
    python3 scripts/attribute_pnl.py
    python3 scripts/attribute_pnl.py --leaderboard localdata/candidate_leaderboard_1d_tiingo_liquid236.csv
    python3 scripts/attribute_pnl.py --json     # machine-readable
    python3 scripts/attribute_pnl.py --sync-broker --backfill-broker-orders --json

See HANDOVER.md "ROI Refinement — P&L Attribution".
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import argparse
import json
from pathlib import Path

from price.attribution import attribute_pnl, format_report, load_trade_journal
from price.trading import (
    get_open_positions,
    reconcile_trade_journal,
    backfill_trade_journal_from_broker_orders,
)


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
    # Broker backfill flags (opt-in; requires --sync-broker)
    parser.add_argument("--backfill-broker-orders", action="store_true",
                        help="Detect broker-filled orders absent from the trade journal and "
                        "append them as UNATTRIBUTED_BROKER_FILL rows. Requires --sync-broker. "
                        "Idempotent: running twice does not duplicate rows.")
    parser.add_argument("--backfill-lookback-days", type=int, default=60,
                        help="How many calendar days back to scan for filled broker orders "
                        "(default: 60).")
    parser.add_argument("--backfill-dry-run", action="store_true",
                        help="When combined with --backfill-broker-orders: show what would be "
                        "added without writing to the journal.")
    args = parser.parse_args()

    # --- Guard: backfill requires --sync-broker ---
    if args.backfill_broker_orders and not args.sync_broker:
        print(
            "ERROR: --backfill-broker-orders requires --sync-broker. "
            "Re-run with both flags so broker reconciliation happens before backfill.",
            file=sys.stderr,
        )
        return 1

    leaderboard_path = Path(args.leaderboard) if args.leaderboard else None
    paper_log_path = Path(args.paper_log) if args.paper_log else None
    broker_positions = None
    journal_path = Path(args.journal) if args.journal else None
    backfill_summary = None

    # --- Step 1: broker reconcile (existing behavior, unchanged) ---
    if args.sync_broker:
        reconcile_trade_journal(path=journal_path)
        broker_positions = get_open_positions()

    # --- Step 2: broker backfill (opt-in) ---
    if args.backfill_broker_orders:
        backfill_summary = backfill_trade_journal_from_broker_orders(
            journal_path=journal_path,
            lookback_days=args.backfill_lookback_days,
            dry_run=args.backfill_dry_run,
        )

    # --- Step 3: reload journal (picks up any newly backfilled rows) ---
    journal = load_trade_journal(journal_path)

    # --- Step 4: run attribution ---
    report = attribute_pnl(
        journal=journal,
        leaderboard_path=leaderboard_path,
        paper_log_path=paper_log_path,
        broker_positions=broker_positions,
    )

    # Attach backfill summary to JSON output when present.
    report["backfill"] = backfill_summary

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(format_report(report))
        if backfill_summary is not None:
            rows_added = backfill_summary.get("rows_to_add", 0)
            dry = backfill_summary.get("dry_run", False)
            if dry:
                print(f"\n[backfill dry-run] would add {rows_added} row(s) to trade journal.")
            else:
                print(f"\n[backfill] added {rows_added} row(s) to trade journal.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

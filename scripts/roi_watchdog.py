"""Tail-risk watchdog audit (2026-10 ROI pass).

Read-only. Two modes:

  Journal mode (default, offline): reconstructs the trade journal and
  lists entry lots with no journaled closing exit. Lots at least
  --max-lot-age-days old (age measured against the newest journal
  timestamp) are VIOLATIONS: each is either a position still open at
  the broker long past its validated horizon, or a position closed by
  a fill nobody journaled. Both demand reconciliation. Exit code 1
  when violations exist so a workflow step can surface them loudly
  (use continue-on-error to keep it audit-only).

  Broker mode (--sync-broker): compares live Alpaca positions against
  stop_state.json and the journal entry context. A position with no
  tracked protective stop, or no recoverable slice context, is a
  violation regardless of age.

This script never places, cancels, or modifies an order. The live
force-exit for aged positions lives in position_manager's watchdog
(--watchdog-* flags on paper_trade.py); this script is the report.

Usage:
    python3 scripts/roi_watchdog.py
    python3 scripts/roi_watchdog.py --max-lot-age-days 30 --json
    python3 scripts/roi_watchdog.py --sync-broker
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import argparse
import json
from pathlib import Path

from price.attribution import load_trade_journal, unmatched_lots
from price.config import DATA_DIR
from price.roi_guards import stale_lots


def _journal_mode(journal_path: Path, max_age_days: float) -> dict:
    journal = load_trade_journal(journal_path)
    lots = unmatched_lots(journal)
    stale = stale_lots(lots, max_age_days=max_age_days)
    return {
        "mode": "journal",
        "journal": str(journal_path),
        "n_unmatched_lots": len(lots),
        "unmatched_notional": round(sum(l["notional"] for l in lots), 2),
        "max_lot_age_days": max_age_days,
        "violations": stale,
        "lots": lots,
    }


def _broker_mode(max_age_days: float) -> dict:
    from price.position_manager import _load_entry_context
    from price.stops import load_stop_states
    from price.trading import get_open_positions

    positions = get_open_positions()
    states = load_stop_states()
    context = _load_entry_context()
    violations = []
    rows = []
    if positions is not None and not positions.empty:
        for _, pos in positions.iterrows():
            sym = str(pos["symbol"]).upper()
            issues = []
            if sym not in states:
                issues.append("no tracked protective stop (stop_state.json)")
            if sym not in context:
                issues.append("no recoverable slice context (journal)")
            row = {
                "symbol": sym,
                "qty": float(pos.get("qty", 0) or 0),
                "avg_entry_price": float(pos.get("avg_entry_price", 0) or 0),
                "unrealized_pl": float(pos.get("unrealized_pl", 0) or 0),
                "issues": issues,
            }
            rows.append(row)
            if issues:
                violations.append(row)
    return {
        "mode": "broker",
        "n_open_positions": len(rows),
        "positions": rows,
        "violations": violations,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--journal", type=Path, default=DATA_DIR / "trade_journal.csv")
    parser.add_argument("--max-lot-age-days", type=float, default=30.0)
    parser.add_argument("--sync-broker", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = (_broker_mode(args.max_lot_age_days) if args.sync_broker
              else _journal_mode(args.journal, args.max_lot_age_days))
    violations = report["violations"]

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print("=" * 72)
        print(f"ROI WATCHDOG ({report['mode']} mode)")
        print("=" * 72)
        if report["mode"] == "journal":
            print(f"  Unmatched lots: {report['n_unmatched_lots']} "
                  f"(${report['unmatched_notional']:.2f} entry notional)")
            print(f"  Stale (>={args.max_lot_age_days:.0f}d): {len(violations)}")
            for lot in violations[:25]:
                print(f"  VIOLATION {lot['symbol']:6} {lot['side']:5} "
                      f"qty={lot['qty']:g} @ {lot['entry_price']:.2f} "
                      f"since {str(lot['entry_ts'])[:10]} ({lot['age_days']}d) "
                      f"{lot['timeframe']} {lot['slice_combination'][:40]}")
        else:
            print(f"  Open positions: {report['n_open_positions']}")
            for row in violations:
                print(f"  VIOLATION {row['symbol']:6} qty={row['qty']:g} "
                      f"uPnL=${row['unrealized_pl']:.2f}: {'; '.join(row['issues'])}")
        if not violations:
            print("  No violations.")
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())

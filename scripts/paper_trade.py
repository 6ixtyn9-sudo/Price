"""One-command paper-trading glue.

Runs monitor.scan_all_slices(), and for each emitted signal:
  - If kind == 'entry_signal' and tradable == True: call trading.submit_entry
  - If kind == 'entry_signal' and tradable == False: log only
  - If kind == 'exit_intent' and action == 'exit': call trading.close_position
  - If kind == 'exit_intent' and action == 'hold': log only

Writes an audit log to localdata/paper_trade_log.csv with one row per
signal-or-action so the operator has a full record of what the script
considered and what it did.

Usage:
    python3 scripts/paper_trade.py                # one scan, exit when done
    python3 scripts/paper_trade.py --loop 60      # scan every 60 seconds
    python3 scripts/paper_trade.py --dry-run      # never call trading, just log
    python3 scripts/paper_trade.py --max-notional 1000
    python3 scripts/paper_trade.py --halt         # touch the kill switch

See HANDOVER.md "Paper-Trading Exploration Layer (2026-07-02)".
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import pandas as pd

from price.config import DATA_DIR
from price.monitor import scan_all_slices
from price.position_manager import ExitPolicy
from price.risk_limits import RiskLimits, record_entry, set_halt_flag
from price.trading import close_position, submit_entry


AUDIT_LOG_PATH: Path = DATA_DIR / "paper_trade_log.csv"


def _append_audit(row: dict) -> None:
    """Append one row to the audit CSV, creating the file if needed."""
    row = dict(row)
    row["logged_at_utc"] = datetime.now(timezone.utc).isoformat()
    df = pd.DataFrame([row])
    if Path(AUDIT_LOG_PATH).exists():
        existing = pd.read_csv(AUDIT_LOG_PATH)
        out = pd.concat([existing, df], ignore_index=True)
    else:
        out = df
    Path(AUDIT_LOG_PATH).parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(AUDIT_LOG_PATH, index=False)


def _strip_known_keys(sig: dict, keys: List[str]) -> dict:
    """Return a copy of `sig` without the listed keys. Used to prevent
    the `**sig` splat from clobbering the audit's own field names."""
    return {k: v for k, v in sig.items() if k not in keys}


def _handle_signals(signals: List[dict], dry_run: bool = False) -> Dict[str, int]:
    """For each signal in the list, either submit a real order, log a
    blocked entry, or close a position. Returns counts for the summary."""
    counts = {
        "entry_submitted": 0,
        "entry_blocked": 0,
        "exit_submitted": 0,
        "exit_hold": 0,
        "no_state_data": 0,
    }

    for sig in signals:
        kind = sig.get("kind")

        if kind == "state_unavailable":
            counts["no_state_data"] += 1
            _append_audit({
                "action": "skip",
                "reason": sig.get("reason", "state_unavailable"),
                **_strip_known_keys(sig, ["action"]),
            })
            continue

        if kind == "entry_signal":
            if sig.get("error") == "no_state_data":
                counts["no_state_data"] += 1
                _append_audit({"action": "skip", "reason": "no_state_data", **_strip_known_keys(sig, ["action"])})
                continue

            if not sig.get("matched"):
                continue  # unmatched slices are noise; don't log them

            if not sig.get("tradable"):
                counts["entry_blocked"] += 1
                _append_audit({
                    "action": "block",
                    "reason": "risk_gate",
                    "blocked_reasons": "; ".join(sig.get("risk_check", {}).get("reasons", [])),
                    **_strip_known_keys(sig, ["action"]),
                })
                continue

            # tradable == True
            symbol = sig["symbol"]
            qty = int(sig.get("suggested_qty", 0))
            slice_label = sig["slice_combination"]

            if qty <= 0:
                counts["entry_blocked"] += 1
                _append_audit({
                    "action": "block",
                    "reason": "qty_zero",
                    **_strip_known_keys(sig, ["action"]),
                })
                continue

            if dry_run:
                _append_audit({
                    "action": "would_enter",
                    "reason": "dry_run",
                    "symbol": symbol,
                    "qty": qty,
                    "slice_label": slice_label,
                    **_strip_known_keys(sig, ["action", "symbol", "qty"]),
                })
                continue

            result = submit_entry(
                symbol=symbol,
                qty=qty,
                slice_label=slice_label,
                side=sig.get("suggested_side", "buy"),
                entry_bar_ts=sig.get("bar_ts_utc"),
                timeframe=sig.get("timeframe"),
            )
            if result.get("status") != "rejected":
                record_entry(symbol)
                counts["entry_submitted"] += 1
            _append_audit({
                "action": "enter",
                "symbol": symbol,
                "qty": qty,
                "slice_label": slice_label,
                "order_id": result.get("order_id"),
                "order_status": result.get("status"),
                "error": result.get("error"),
                **_strip_known_keys(sig, ["action", "symbol", "qty"]),
            })

        elif kind == "exit_intent":
            action = sig.get("action")
            symbol = sig.get("symbol")
            if action == "hold":
                counts["exit_hold"] += 1
                continue
            if action == "exit":
                # sig contains 'action' which would clobber our audit
                # 'action' field via the ** splat. Strip it.
                sig_for_audit = _strip_known_keys(sig, ["action"])
                if dry_run:
                    _append_audit({
                        "action": "would_exit",
                        "reason": "dry_run",
                        **sig_for_audit,
                    })
                    continue
                result = close_position(symbol)
                counts["exit_submitted"] += 1
                _append_audit({
                    "action": "exit",
                    "symbol": symbol,
                    "order_id": result.get("order_id"),
                    "order_status": result.get("status"),
                    "error": result.get("error"),
                    **sig_for_audit,
                })

    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Paper-trade the V4 monitored slices.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Compute signals and write the audit log, but do not call trading.submit_entry / close_position.")
    parser.add_argument("--loop", type=int, default=0,
                        help="If > 0, loop and re-scan every N seconds. 0 = single scan and exit.")
    parser.add_argument("--max-notional", type=float, default=2500.0,
                        help="Max notional per position (USD). Default 2500.")
    parser.add_argument("--max-open", type=int, default=4,
                        help="Max simultaneously open positions. Default 4.")
    parser.add_argument("--max-daily-loss", type=float, default=500.0,
                        help="Daily realized loss kill switch (USD). Default 500.")
    parser.add_argument("--cooldown-seconds", type=int, default=3600,
                        help="Per-symbol entry cooldown in seconds. Default 3600 (1h).")
    parser.add_argument("--equal-notional", action="store_true",
                        help="Disable conviction-weighted sizing and use the legacy equal-notional rule "
                        "(floor(max_notional / price)). By default sizing is edge- and volatility-aware "
                        "and degrades to equal-notional only when no leaderboard edge data exists.")
    parser.add_argument("--risk-fraction", type=float, default=0.005,
                        help="Fraction of account equity risked per trade at full conviction, for the "
                        "volatility rail. Default 0.005 (0.5%%). Only active when --sizing-equity is set.")
    parser.add_argument("--sizing-equity", type=float, default=None,
                        help="Account equity used for the volatility rail (Stage B). When set, sizing "
                        "also caps each position by risk_dollars / ATR so high-vol names cannot "
                        "concentrate more than their risk budget. Toward real capital, set this to "
                        "current account equity.")
    parser.add_argument("--exit-horizon", type=int, default=5,
                        help="Max bars (in the position's own timeframe) to hold before a time-stop "
                        "exit. Default 5 = the fwd_ret_5 validation horizon (faithful to the measured "
                        "edge). 0 disables the horizon exit (state-break only, legacy behaviour).")
    parser.add_argument("--allow-shorts", action="store_true",
                        help="Enable short-side entries on the paper account. Default: short signals "
                        "are computed and logged but BLOCKED at the risk gate (allow_shorts=False).")
    parser.add_argument("--halt", action="store_true",
                        help="Touch the localdata/HALT_TRADING.flag kill switch and exit. No orders will be placed on subsequent runs until --unhalt is used.")
    parser.add_argument("--unhalt", action="store_true",
                        help="Remove the kill switch flag and exit.")
    args = parser.parse_args()

    if args.halt:
        path = set_halt_flag()
        print(f"Halt flag set at: {path}")
        print("All new entries will be blocked until --unhalt is run.")
        return 0
    if args.unhalt:
        from price.risk_limits import clear_halt_flag
        removed = clear_halt_flag()
        print(f"Halt flag removed: {removed}")
        return 0

    limits = RiskLimits(
        max_notional_per_position=args.max_notional,
        max_open_positions=args.max_open,
        max_daily_realized_loss=args.max_daily_loss,
        per_symbol_cooldown_seconds=args.cooldown_seconds,
        allow_shorts=args.allow_shorts,
        conviction_sizing_enabled=not args.equal_notional,
        risk_fraction_per_trade=args.risk_fraction,
        account_equity_for_sizing=args.sizing_equity,
    )

    print(f"Risk limits: {limits.to_dict()}")
    print(f"Dry run: {args.dry_run}")

    exit_policy = ExitPolicy(horizon_bars=args.exit_horizon)
    print(f"Exit policy: horizon_bars={exit_policy.horizon_bars}")

    def _one_pass() -> Dict[str, int]:
        signals = scan_all_slices(
            limits=limits, dry_run=args.dry_run, exit_policy=exit_policy,
        )
        counts = _handle_signals(signals, dry_run=args.dry_run)
        print("\n=== pass summary ===")
        for k, v in counts.items():
            print(f"  {k}: {v}")
        return counts

    if args.loop > 0:
        print(f"Looping every {args.loop}s; Ctrl-C to stop.")
        try:
            while True:
                _one_pass()
                time.sleep(args.loop)
        except KeyboardInterrupt:
            print("Stopped by operator.")
            return 0
    else:
        _one_pass()
        return 0


if __name__ == "__main__":
    sys.exit(main())

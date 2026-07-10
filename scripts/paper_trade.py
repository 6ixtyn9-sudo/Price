"""One-command paper-trading glue.

Runs monitor.scan_all_slices(), and for each emitted signal:
  - If kind == 'entry_signal' and tradable == True: call trading.submit_entry
  - If kind == 'entry_signal' and tradable == False: log only
  - If kind == 'exit_intent' and action == 'exit': call trading.close_position
  - If kind == 'exit_intent' and action == 'hold': log only
  - If kind == 'stop_intent': audit-log only (the broker call already
    happened inside scan_all_slices's reconcile_stops -- see stop_manager.py)

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


def _resolve_sizing_equity(auto: bool, manual: float, get_account_info_fn=None) -> float:
    """Resolve the account-equity value used for the volatility rail and
    the aggregate open-risk budget. When `auto` is True, fetches live
    equity from Alpaca; on any fetch failure, falls back to `manual`
    (never raises, so a transient API hiccup cannot crash the scan).
    """
    if not auto:
        return manual
    if get_account_info_fn is None:
        from price.trading import get_account_info as get_account_info_fn
    try:
        return get_account_info_fn()["equity"]
    except Exception as e:  # noqa: BLE001 - a fetch failure must not crash the scan
        print(f"--auto-sizing-equity fetch failed ({e}); falling back to --sizing-equity={manual}")
        return manual


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
        "stop_actions": 0,
    }

    for sig in signals:
        kind = sig.get("kind")

        if kind == "stop_intent":
            # The actual broker call (attach/ratchet/no-op) already happened
            # inside scan_all_slices's reconcile_stops call -- this is an
            # audit-only row, mirroring the sig's own action label
            # (stop_attached / stop_ratcheted / stop_unchanged / stop_pending
            # / stop_attach_failed / stop_ratchet_failed / stop_state_cleared
            # / would_attach_stop / would_ratchet_stop in --dry-run).
            counts["stop_actions"] += 1
            _append_audit(dict(sig))
            continue

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
            limit_price = sig.get("close_adj")  # Use signal bar close as the limit price

            # Entry orders must be LIMIT-at-signal-close, never market.
            # submit_entry falls back to a market order when limit_price is
            # None/NaN; for scheduled runs (which can fire while the market is
            # closed) a queued market order buys the next open blind -- the
            # exact signal-close-to-fill gap the cost model exists to prevent.
            # Block the entry instead and record why.
            try:
                _lp_ok = limit_price is not None and float(limit_price) > 0 and float(limit_price) == float(limit_price)
            except (TypeError, ValueError):
                _lp_ok = False
            if not _lp_ok:
                counts["entry_blocked"] += 1
                _append_audit({
                    "action": "block",
                    "reason": "no_limit_price",
                    "blocked_reasons": "signal close_adj missing/invalid; refusing market-order fallback",
                    **_strip_known_keys(sig, ["action"]),
                })
                continue

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
                    "limit_price": limit_price,
                    **_strip_known_keys(sig, ["action", "symbol", "qty"]),
                })
                continue

            result = submit_entry(
                symbol=symbol,
                qty=qty,
                slice_label=slice_label,
                side=sig.get("suggested_side", "buy"),
                limit_price=limit_price,
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
                        "current account equity. Ignored if --auto-sizing-equity is also given.")
    parser.add_argument("--auto-sizing-equity", action="store_true",
                        help="Fetch current account equity live from Alpaca (trading.get_account_info) "
                        "and use it for the volatility rail AND the aggregate open-risk budget, instead "
                        "of requiring a manually maintained --sizing-equity value. Recommended for "
                        "unattended/scheduled runs (e.g. the live_capture workflow), since a stale "
                        "hand-set equity number would silently under- or over-state the real risk "
                        "budget as the account's P&L moves. Falls back to --sizing-equity (or Stage "
                        "B / the aggregate cap being skipped) if the account fetch fails.")
    parser.add_argument("--exit-horizon", type=int, default=5,
                        help="Max bars (in the position's own timeframe) to hold before a time-stop "
                        "exit. Default 5 = the fwd_ret_5 validation horizon (faithful to the measured "
                        "edge). 0 disables the horizon exit (state-break only, legacy behaviour). "
                        "Suppressed once a trade is past +1R when --respect-r-gate is enabled "
                        "(default): a confirmed winner is left to the trailing stop, not time-stopped.")
    parser.add_argument("--no-r-gate", action="store_true",
                        help="Disable the R-multiple horizon-suppression gate: restores the original "
                        "unconditional 5-bar time-stop even for a trade that has already confirmed to "
                        "+1R. Off by default (i.e. the R-gate is ON by default) so 'small losses, large "
                        "profits' is the default behaviour once stops are attached.")
    parser.add_argument("--stop-atr-mult", type=float, default=2.0,
                        help="Initial protective-stop distance, in multiples of ATR(14), set the moment "
                        "a position is filled and enforced as a REAL resting broker-side stop order "
                        "(not just checked on the next scan). Default 2.0. This is also the per-share R "
                        "for the trade: R_dollars = stop_atr_mult * ATR * qty.")
    parser.add_argument("--trail-atr-mult", type=float, default=3.0,
                        help="Chandelier trailing-stop distance, in multiples of ATR(14), active only "
                        "once a trade has reached --breakeven-trigger-r. Looser than the initial stop by "
                        "design, so a confirmed trend has room to run. Default 3.0.")
    parser.add_argument("--breakeven-trigger-r", type=float, default=1.0,
                        help="Unrealized R-multiple at which the protective stop ratchets to breakeven "
                        "(the trade can no longer lose money) and the chandelier trail takes over. "
                        "Default 1.0 (+1R).")
    parser.add_argument("--max-aggregate-risk-pct", type=float, default=0.03,
                        help="Max aggregate open risk across the WHOLE book at once (sum of every open "
                        "position's current stop-distance risk; breakeven-or-better positions contribute "
                        "$0), as a fraction of --sizing-equity. This is the leverage prerequisite: with "
                        "every position carrying a real stop and the aggregate capped, leverage changes "
                        "how much notional expresses a given R, not how much can be lost if wrong. "
                        "Default 0.03 (3%%). Requires --sizing-equity to be set; otherwise fails open "
                        "(no cap enforced, consistent with every other equity-dependent lever). "
                        "Set <= 0 to disable explicitly.")
    parser.add_argument("--whipsaw-limit", type=int, default=2,
                        help="Same-day consecutive stop-outs on one symbol before the whipsaw circuit "
                        "breaker benches it for the rest of the trading day. Default 2. Tight ATR stops "
                        "mean more stop-outs; this exists so 'small losses' cannot silently become "
                        "'many small losses in one choppy day.' Set <= 0 to disable.")
    parser.add_argument("--target-leverage", type=float, default=1.0,
                        help="How much of the account's real margin capacity to actually use, as a "
                        "multiple of equity. Default 1.0 (cash-secured, no leverage). 2.0 = standard "
                        "Reg T overnight margin. Deliberately NOT Alpaca's 4x intraday-only rate: that "
                        "rate steps down to 2x for anything held overnight, and this system's exit "
                        "policy holds positions across multiple bars (does not flatten same-day) -- "
                        "using 4.0 here would silently violate the overnight limit every session. "
                        "Requires --auto-sizing-equity or --sizing-equity to actually gate anything "
                        "(the leverage checks fail open without a known equity value). A position that "
                        "cannot get a protective stop attached is FORCE-CLOSED (not retried) whenever "
                        "this is > 1.0 -- see stop_manager.reconcile_stops.")
    parser.add_argument("--margin-cushion-pct", type=float, default=0.20,
                        help="Real-time margin safety cushion: block new entries once the broker's "
                        "actual buying_power falls below this fraction of the self-imposed leverage "
                        "ceiling (equity * --target-leverage). Default 0.20 (stop entries at 80%% "
                        "margin usage). This is the honest backstop against the gross-notional check's "
                        "own approximate math -- it reads Alpaca's real-time account state rather than "
                        "trusting our arithmetic alone. Requires --auto-sizing-equity or --sizing-equity. "
                        "Set <= 0 to disable.")
    parser.add_argument("--max-per-group", type=int, default=2,
                        help="Max concurrent open positions sharing a risk group (the slice's stable "
                        "entry condition). Default 2: allows a confirming second name in a family but "
                        "blocks the book concentrating on one factor (e.g. XOP+XLB+KLAC all on "
                        "stretched_down+downtrend). 0 disables (every symbol = independent slot, "
                        "legacy behaviour).")
    parser.add_argument("--regime-filter", action="store_true",
                        help="Enable the regime deployment gate. When on, a matched slice is blocked "
                        "from entry if its macro regime (SMA-50/200 trend of the slice's own symbol, "
                        "or a configured regime_symbol) is 'bear'. Converts the regime-conditional "
                        "finding into an automatic dismount during hostile macro periods. Default off "
                        "(zero-risk to the live book); fails open on missing data.")
    parser.add_argument("--cost-spread-bps", type=float, default=1.0,
                        help="Per-leg half-spread cost in basis points (crossing a market order). "
                        "Liquid (SPY/XLF) ~0.4-1bp, XOP/KLAC wider. Default 1.0.")
    parser.add_argument("--cost-slippage-bps", type=float, default=3.0,
                        help="Per-leg slippage in basis points, modelling adverse fill + the "
                        "signal-to-fill gap (signal bar closes; order fills next session). The "
                        "dominant uncertain term; recalibrate from realized fills later. Default 3.0.")
    parser.add_argument("--cost-commission-bps", type=float, default=0.0,
                        help="Per-leg commission in basis points. Default 0.0 (zero-commission "
                        "retail / Alpaca paper).")
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

    sizing_equity = _resolve_sizing_equity(args.auto_sizing_equity, args.sizing_equity)
    if args.auto_sizing_equity and sizing_equity is not None:
        print(f"Auto-fetched account equity for sizing/risk-budget: ${sizing_equity:,.2f}")

    limits = RiskLimits(
        max_notional_per_position=args.max_notional,
        max_open_positions=args.max_open,
        max_daily_realized_loss=args.max_daily_loss,
        per_symbol_cooldown_seconds=args.cooldown_seconds,
        allow_shorts=args.allow_shorts,
        conviction_sizing_enabled=not args.equal_notional,
        risk_fraction_per_trade=args.risk_fraction,
        account_equity_for_sizing=sizing_equity,
        max_positions_per_risk_group=args.max_per_group,
        stop_atr_multiple=args.stop_atr_mult,
        trail_atr_multiple=args.trail_atr_mult,
        breakeven_trigger_r=args.breakeven_trigger_r,
        max_aggregate_open_risk_pct=(
            args.max_aggregate_risk_pct if args.max_aggregate_risk_pct > 0 else None
        ),
        whipsaw_stopout_limit=args.whipsaw_limit,
        target_leverage_multiple=args.target_leverage,
        margin_cushion_pct=(args.margin_cushion_pct if args.margin_cushion_pct > 0 else None),
    )

    print(f"Risk limits: {limits.to_dict()}")
    print(f"Dry run: {args.dry_run}")

    from price.cost_model import CostModel
    cost_model = CostModel(
        commission_bps=args.cost_commission_bps,
        spread_bps=args.cost_spread_bps,
        slippage_bps=args.cost_slippage_bps,
    )
    exit_policy = ExitPolicy(
        horizon_bars=args.exit_horizon,
        respect_r_multiple_gate=not args.no_r_gate,
    )
    print(f"Exit policy: horizon_bars={exit_policy.horizon_bars}, "
          f"respect_r_multiple_gate={exit_policy.respect_r_multiple_gate}")
    print(f"Cost model: {cost_model.to_dict()}")

    def _one_pass() -> Dict[str, int]:
        # Reconcile submission-time journal rows with Alpaca before reading
        # exposure, exit context, or risk state. This is read-only and never
        # places/cancels/replaces orders, but it prevents accepted/pending/
        # expired entries from masquerading as fills.
        try:
            from price.trading import reconcile_trade_journal
            reconcile_trade_journal()
        except Exception as exc:  # noqa: BLE001 - the scan remains fail-safe
            print(f"WARNING: broker order reconciliation failed: {exc}")

        signals = scan_all_slices(
            limits=limits, dry_run=args.dry_run, exit_policy=exit_policy,
            cost_model=cost_model, regime_filter_enabled=args.regime_filter,
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

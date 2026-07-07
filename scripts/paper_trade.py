import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import argparse
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
    if not auto: return manual
    if get_account_info_fn is None:
        from price.trading import get_account_info as get_account_info_fn
    try: return get_account_info_fn()["equity"]
    except Exception as e:
        print(f"Equity fetch failed ({e}); falling back to {manual}")
        return manual

def _strip_known_keys(sig: dict, keys: List[str]) -> dict:
    return {k: v for k, v in sig.items() if k not in keys}

def _handle_signals(signals: List[dict], dry_run: bool = False) -> Dict[str, int]:
    counts = {"entry_submitted": 0, "entry_blocked": 0, "exit_submitted": 0, "exit_hold": 0, "no_state_data": 0, "stop_actions": 0}
    for sig in signals:
        kind = sig.get("kind")
        if kind == "stop_intent":
            counts["stop_actions"] += 1
            _append_audit(dict(sig))
            continue
        if kind == "state_unavailable":
            counts["no_state_data"] += 1
            _append_audit({"action": "skip", "reason": sig.get("reason", "state_unavailable"), **_strip_known_keys(sig, ["action"])})
            continue
        if kind == "entry_signal":
            if not sig.get("matched") or sig.get("error") == "no_state_data": continue
            if not sig.get("tradable"):
                counts["entry_blocked"] += 1
                _append_audit({"action": "block", "reason": "risk_gate", "blocked_reasons": "; ".join(sig.get("risk_check", {}).get("reasons", [])), **_strip_known_keys(sig, ["action"])})
                continue
            symbol, qty, slice_label = sig["symbol"], int(sig.get("suggested_qty", 0)), sig["slice_combination"]
            limit_price = sig.get("close_adj")
            if qty <= 0: continue
            if dry_run:
                _append_audit({"action": "would_enter", "reason": "dry_run", "symbol": symbol, "qty": qty, "slice_label": slice_label, "limit_price": limit_price, **_strip_known_keys(sig, ["action", "symbol", "qty"])})
                continue
            result = submit_entry(symbol=symbol, qty=qty, slice_label=slice_label, side=sig.get("suggested_side", "buy"), limit_price=limit_price, entry_bar_ts=sig.get("bar_ts_utc"), timeframe=sig.get("timeframe"))
            if result.get("status") != "rejected":
                record_entry(symbol)
                counts["entry_submitted"] += 1
            _append_audit({"action": "enter", "symbol": symbol, "qty": qty, "slice_label": slice_label, "order_id": result.get("order_id"), "order_status": result.get("status"), "error": result.get("error"), **_strip_known_keys(sig, ["action", "symbol", "qty"])})
        elif kind == "exit_intent":
            action, symbol = sig.get("action"), sig.get("symbol")
            if action == "hold": counts["exit_hold"] += 1
            elif action == "exit":
                sig_for_audit = _strip_known_keys(sig, ["action"])
                if dry_run:
                    _append_audit({"action": "would_exit", "reason": "dry_run", **sig_for_audit})
                    continue
                result = close_position(symbol)
                counts["exit_submitted"] += 1
                _append_audit({"action": "exit", "symbol": symbol, "order_id": result.get("order_id"), "order_status": result.get("status"), "error": result.get("error"), **_strip_known_keys(sig, ["action"])})
    return counts

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--loop", type=int, default=0)
    parser.add_argument("--max-notional", type=float, default=2500.0)
    parser.add_argument("--max-open", type=int, default=4)
    parser.add_argument("--max-daily-loss", type=float, default=500.0)
    parser.add_argument("--cooldown-seconds", type=int, default=3600)
    parser.add_argument("--auto-sizing-equity", action="store_true")
    parser.add_argument("--sizing-equity", type=float, default=None)
    parser.add_argument("--risk-fraction", type=float, default=0.005)
    parser.add_argument("--exit-horizon", type=int, default=5)
    parser.add_argument("--stop-atr-mult", type=float, default=2.0)
    parser.add_argument("--trail-atr-mult", type=float, default=3.0)
    parser.add_argument("--target-leverage", type=float, default=1.0)
    parser.add_argument("--max-per-group", type=int, default=2)
    parser.add_argument("--regime-filter", action="store_true")
    args = parser.parse_args()
    equity = _resolve_sizing_equity(args.auto_sizing_equity, args.sizing_equity)
    limits = RiskLimits(max_notional_per_position=args.max_notional, max_open_positions=args.max_open, max_daily_realized_loss=args.max_daily_loss, per_symbol_cooldown_seconds=args.cooldown_seconds, conviction_sizing_enabled=True, risk_fraction_per_trade=args.risk_fraction, account_equity_for_sizing=equity, max_positions_per_risk_group=args.max_per_group, stop_atr_multiple=args.stop_atr_mult, trail_atr_multiple=args.trail_atr_mult, target_leverage_multiple=args.target_leverage)
    from price.cost_model import CostModel
    cost_model = CostModel()
    exit_policy = ExitPolicy(horizon_bars=args.exit_horizon)
    print(f"Cost model: {cost_model.to_dict()}")
    def _one_pass():
        signals = scan_all_slices(limits=limits, dry_run=args.dry_run, exit_policy=exit_policy, cost_model=cost_model, regime_filter_enabled=args.regime_filter)
        counts = _handle_signals(signals, dry_run=args.dry_run)
        print("\n=== pass summary ===")
        for k, v in counts.items(): print(f"  {k}: {v}")
    if args.loop > 0:
        try:
            while True:
                _one_pass(); time.sleep(args.loop)
        except KeyboardInterrupt: return 0
    else:
        _one_pass(); return 0
if __name__ == "__main__": sys.exit(main())

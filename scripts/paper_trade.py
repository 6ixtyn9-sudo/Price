"""One-command paper-trading glue.

Runs monitor.scan_all_slices(), and for each emitted signal:
  - If kind == 'entry_signal' and tradable == True: call trading.submit_entry
  - If kind == 'entry_signal' and tradable == False: log only
  - If kind == 'exit_intent' and action == 'exit': call trading.close_position
  - If kind == 'exit_intent' and action == 'hold': log only
  - If kind == 'stop_intent': audit-log only (the broker call already
    happened inside scan_all_slices's reconcile_stops -- see stop_manager.py)

Writes an audit log to localdata/paper_trade_log.csv with one row per
signal-or-action so the operator has a full record of what the scrip
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
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set

import pandas as pd

from price.config import DATA_DIR
from price.monitor import scan_all_slices
from price.position_manager import ExitPolicy
from price.risk_limits import RiskLimits, record_entry, set_halt_flag
from price.trading import close_position, submit_entry, entry_limit_with_premium, resolve_adverse_threshold_bps, resolve_entry_premium_bps


import os
AUDIT_LOG_PATH: Path = Path(os.getenv("PAPER_TRADE_LOG_PATH", str(DATA_DIR / "paper_trade_log.csv")))

def _get_lane() -> str:
    path_str = str(AUDIT_LOG_PATH)
    if "crypto" in path_str: return "crypto"
    if "futures" in path_str: return "fut"
    return "eq"


# Lane parking: a committed ops flag that suspends a lane's scan/broker
# work without touching crons or workflows (data ingest in the workflows
# keeps running, so an unpark never cold-starts the warehouse). One audit
# row per parked run keeps the parking visible in the lane's own ledger.
PARK_FLAG_NAMES = {"eq": "EQUITIES", "crypto": "CRYPTO", "fut": "FUTURES"}


def _lane_park_flag_path(lane: str) -> Path:
    return DATA_DIR / f"PARK_LANE_{PARK_FLAG_NAMES.get(lane, lane.upper())}.flag"


def _monitored_book_size() -> int:
    """Row count of the lane's monitored book at scan start; -1 when
    unreadable/absent. Cheap and best-effort — never blocks the scan."""
    try:
        from price.monitor import MONITORED_SLICES_PATH
        book_path = Path(MONITORED_SLICES_PATH)
        if not book_path.exists():
            return -1
        return int(len(pd.read_csv(book_path)))
    except Exception:  # noqa: BLE001 - telemetry must not break trading
        return -1


def _emit_scan_summary(action: str, started_monotonic: float, lane: str, **fields) -> None:
    """Heartbeat: exactly one scan_summary audit row per run, no matter how
    the run ended (complete / parked / failed). Motivation: the crypto lane
    burned days completing 'green' workflow runs in ~1 second with zero
    evaluation rows — green used to be indistinguishable from working.
    Emission itself must never crash a scan."""
    try:
        payload = {
            "kind": "scan_summary",
            "action": action,
            "lane": lane,
            "runtime_s": round(time.monotonic() - started_monotonic, 3),
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }
        payload.update({k: v for k, v in fields.items() if v is not None})
        _append_audit(payload)
    except Exception as exc:  # noqa: BLE001 - heartbeat must be side-effect-safe
        print(f"scan_summary heartbeat emit failed (non-fatal): {exc}")


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
    """Return a copy of `sig` without the listed keys. Used to preven
    the `**sig` splat from clobbering the audit's own field names."""
    return {k: v for k, v in sig.items() if k not in keys}


def _warehouse_adverse_reference(symbol: str, timeframe: Optional[str]) -> Optional[float]:
    """Quote-outage fallback reference price for the falling-knife guard.
    ... Quote-outage hardening (2026-08-06) ..."""
    try:
        import price.monitor as _mon
        from price.warehouse import load_from_warehouse
        tf = timeframe or "1d"
        df = load_from_warehouse(symbol, tf)
        if df is None or df.empty:
            return None
        df = _mon._drop_incomplete_intraday_rows(df, tf)
        if df.empty:
            return None
        if _mon._stale_warehouse_reason(df, tf) is not None:
            return None  # too old to say anything about today's price
        for col in ("close_adj", "close_raw", "close"):
            if col in df.columns:
                px = float(df[col].iloc[-1])
                return px if px == px and px > 0 else None
        return None
    except Exception:  # noqa: BLE001 - guard fallback must never crash the scan
        return None


def _handle_signals(signals: List[dict], dry_run: bool = False, max_adverse_fill_bps: float = 0.0, entry_premium_bps: float = 0.0, adverse_atr_mult: float = 0.0, entry_premium_atr_mult: float = 0.0) -> Dict[str, int]:
    """For each signal in the list, either submit a real order, log a
    blocked entry, or close a position. Returns counts for the summary."""
    counts = {
        "entry_submitted": 0,
        "entry_blocked": 0,
        "exit_submitted": 0,
        "exit_hold": 0,
        "no_state_data": 0,
        "stop_actions": 0,
        "exit_dedup": 0,
    }
    submitted_symbols_this_run: set[str] = set()
    closed_symbols_this_run: set[str] = set()

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
            signal_close = sig.get("close_adj")  # researched entry price (signal bar close)

            # Entry orders must be LIMIT, never market. submit_entry falls back
            # to a market order when limit_price is None/NaN; for scheduled runs
            # (which can fire while the market is closed) a queued market order
            # buys the next open blind -- the exact signal-close-to-fill gap the
            # cost model exists to prevent. Block the entry instead and record why.
            try:
                _sc_ok = signal_close is not None and float(signal_close) > 0 and float(signal_close) == float(signal_close)
            except (TypeError, ValueError):
                _sc_ok = False
            if not _sc_ok:
                counts["entry_blocked"] += 1
                _append_audit({
                    "action": "block",
                    "reason": "no_limit_price",
                    "blocked_reasons": "signal close_adj missing/invalid; refusing market-order fallback",
                    **_strip_known_keys(sig, ["action"]),
                })
                continue

            # Winner-capture: raise the limit a touch ABOVE the signal close so
            # modest post-signal rallies still fill. Without this the limit sat
            # exactly on the signal close, so the engine could ONLY fill when
            # price fell (kitchen-sink fills) and missed every setup that rose
            # -- anti-selecting its own edge. The premium is DYNAMIC: scaled by
            # the signal's own ATR (entry_premium_atr_mult), so a high-beta name
            # gets a wider band and a sleepy one a tight band, automatically.
            # entry_premium_bps (>0) is an optional hard CAP on that. The
            # adverse-fill guard below still measures against `signal_close`.
            _atr = sig.get("sizing_atr")
            _premium_bps = resolve_entry_premium_bps(_atr, signal_close, entry_premium_atr_mult, entry_premium_bps)
            _is_short_entry = sig.get("suggested_side", "buy").lower() in ("sell", "short")
            limit_price = entry_limit_with_premium(signal_close, _premium_bps, is_short=_is_short_entry)
            if limit_price is None:
                limit_price = signal_close

            # Falling-knife guard. Daily-bar signals are acted on the next
            # trading session; a limit then fills at the live price, buying into
            # a decline that already invalidated the setup. Skip the entry when
            # the live price has moved against the SIGNAL CLOSE by more than the
            # (DYNAMIC, ATR-scaled) threshold. Fail OPEN: no live price or no
            # computable threshold -> do not block trading.
            _adverse_bps = resolve_adverse_threshold_bps(_atr, signal_close, adverse_atr_mult, max_adverse_fill_bps)
            adverse_guard_state = "disabled"
            if _adverse_bps is not None and _adverse_bps > 0:
                from price.trading import get_latest_price, is_stale_entry
                _entry_side = sig.get("suggested_side", "buy")
                _live = get_latest_price(symbol)
                _ref_kind = "live"
                if _live is None:
                    _wh_ref = _warehouse_adverse_reference(symbol, sig.get("timeframe"))
                    if _wh_ref is not None:
                        _live = _wh_ref
                        _ref_kind = "warehouse"
                _stale, _gap = is_stale_entry(
                    _entry_side, signal_close, _live, _adverse_bps
                )
                if _gap is None and not dry_run:
                    counts["entry_blocked"] += 1
                    _append_audit({
                        "action": "block",
                        "reason": "no_live_quote_adverse_guard",
                        "blocked_reasons": "live quote missing with active adverse guard; refusing to submit blind order",
                        **_strip_known_keys(sig, ["action"]),
                    })
                    continue
                if _stale:
                    counts["entry_blocked"] += 1
                    _append_audit({
                        "action": "block",
                        "reason": ("stale_signal_adverse_gap" if _ref_kind == "live"
                                   else "stale_signal_adverse_gap_warehouse_ref"),
                        "blocked_reasons": (
                            f"{'live quote' if _ref_kind == 'live' else 'warehouse-ref (no live quote)'} "
                            f"{_live:.2f} is {_gap:.0f} bps vs signal close "
                            f"{float(signal_close):.2f} (adverse beyond dynamic "
                            f"threshold {_adverse_bps:.0f} bps = "
                            f"{adverse_atr_mult:g} ATR); setup likely invalidated "
                            f"by the post-signal move -- skipping"
                        ),
                        "live_price": _live,
                        "adverse_reference": _ref_kind,
                        "signal_to_fill_bps": _gap,
                        "adverse_threshold_bps": _adverse_bps,
                        **_strip_known_keys(sig, ["action"]),
                    })
                    continue
                adverse_guard_state = (
                    "skipped_no_price" if _gap is None
                    else ("passed_warehouse_ref" if _ref_kind == "warehouse" else "passed")
                )

            if qty <= 0:
                counts["entry_blocked"] += 1
                _append_audit({
                    "action": "block",
                    "reason": "qty_zero",
                    **_strip_known_keys(sig, ["action"]),
                })
                continue

            if symbol in submitted_symbols_this_run:
                counts["entry_blocked"] += 1
                _append_audit({
                    "action": "block",
                    "reason": "symbol_already_submitted_this_pass",
                    "blocked_reasons": f"already submitted an entry order for {symbol} on another matching slice in this scan pass",
                    **_strip_known_keys(sig, ["action"]),
                })
                continue

            if dry_run:
                submitted_symbols_this_run.add(symbol)
                _append_audit({
                    "action": "would_enter",
                    "reason": "dry_run",
                    "symbol": symbol,
                    "qty": qty,
                    "slice_label": slice_label,
                    "limit_price": limit_price,
                    "adverse_guard": adverse_guard_state,
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
                bin_mode=sig.get("bin_mode", "insample"),
                exit_horizon=sig.get("exit_horizon"),
                stop_atr_mult=sig.get("stop_atr_mult"),
                lane=_get_lane(),
                workflow_run_id=os.environ.get("GITHUB_RUN_ID", ""),
            )
            if result.get("status") != "rejected":
                submitted_symbols_this_run.add(symbol)
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
                "adverse_guard": adverse_guard_state,
                **_strip_known_keys(sig, ["action", "symbol", "qty"]),
            })

        elif kind == "exit_intent":
            action = sig.get("action")
            symbol = sig.get("symbol")
            if action == "hold":
                counts["exit_hold"] += 1
                continue
            if action == "exit":
                # sig contains 'action' which would clobber our audi
                # 'action' field via the ** splat. Strip it.
                sig_for_audit = _strip_known_keys(sig, ["action"])
                if symbol in closed_symbols_this_run:
                    _append_audit({
                        "action": "exit_dedup",
                        "symbol": symbol,
                        "reason": "another exit already submitted this pass",
                        **sig_for_audit,
                    })
                    counts["exit_dedup"] += 1
                    continue

                if dry_run:
                    closed_symbols_this_run.add(symbol)
                    _append_audit({
                        "action": "would_exit",
                        "reason": "dry_run",
                        **sig_for_audit,
                    })
                    continue

                result = close_position(symbol)
                closed_symbols_this_run.add(symbol)
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
                        help="Enable the side-aware regime deployment gate. When on, a LONG entry is blocked if its "
                             "macro regime (SMA-50/200 trend of the slice's own symbol, or a configured regime_symbol) "
                             "is 'bear' (the dip-buy-into-a-crash failure), and a SHORT entry is blocked in a 'bull' "
                             "regime. neutral/unknown always pass (permissive / fail-open). Converts the regime-conditional "
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
    parser.add_argument("--take-profit-r", type=float, default=0.0,
                        help="Hard R-multiple take profit. Exit if unrealized R >= this value. "
                        "Set to 0 to disable. Example: 3.0")
    parser.add_argument("--eod-profit-lock-r", type=float, default=0.0,
                        help="Exit if unrealized R >= this value AND within the EOD lock window. "
                        "Set to 0 to disable. Example: 0.75")
    parser.add_argument("--eod-lock-minutes", type=int, default=45,
                        help="Minutes before NYSE close to activate the EOD profit lock. Default 45.")
    parser.add_argument("--giveback-trigger-r", type=float, default=0.0,
                        help="Peak R-multiple that arms the profit giveback exit. "
                        "Set to 0 to disable. Example: 2.0")
    parser.add_argument("--max-giveback-r", type=float, default=1.0,
                        help="How much R can be given back from peak before exiting. Default 1.0.")
    parser.add_argument("--eod-crypto", action="store_true",
                        help="Apply EOD profit lock to crypto symbols. Off by default.")
    parser.add_argument("--eod-futures", action="store_true",
                        help="Apply EOD profit lock to futures symbols. Off by default.")
    parser.add_argument("--pure-horizon-exits", action="store_true",
                        help="Ignore stable state-break exits when a position has an active horizon (>0), holding unconditionally for its validated fwd_ret_N horizon.")
    parser.add_argument("--adverse-atr-mult", type=float, default=1.0,
                        help="DYNAMIC falling-knife threshold: skip an entry when the live price has moved more "
                             "than this many ATRs against the signal close (long: fell; short: rose). "
                             "Volatility-normalised, so each symbol gets a band sized to its own typical bar. "
                             "Default 1.0 (one typical bar). 0 disables the dynamic guard.")
    parser.add_argument("--max-adverse-fill-bps", type=float, default=200.0,
                        help="Optional HARD CAP (bps) on the dynamic adverse threshold --adverse-atr-mult. "
                             "0 = uncapped, i.e. purely dynamic. Set >0 to impose a ceiling. Default 200.0.")
    parser.add_argument("--entry-premium-atr-mult", type=float, default=0.25,
                        help="DYNAMIC winner-capture: raise the entry LIMIT this many ATRs above the signal "
                             "close so modest post-signal rallies fill. 0 disables (limit sits on signal close). "
                             "Default 0.25 ATR.")
    parser.add_argument("--entry-premium-bps", type=float, default=100.0,
                        help="Optional HARD CAP (bps) on the dynamic entry premium --entry-premium-atr-mult. "
                             "0 = uncapped, i.e. purely dynamic. Set >0 to impose a ceiling. Default 100.0.")
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

    lane = _get_lane()
    park_flag = _lane_park_flag_path(lane)
    if park_flag.exists():
        run_started = time.monotonic()
        reason_line = park_flag.read_text().strip().splitlines()[0] if park_flag.read_text().strip() else ""
        _emit_scan_summary(
            "lane_parked", run_started, lane,
            book_size=_monitored_book_size(),
            note=f"park flag {park_flag.name} present; scan, broker reconciliation and stop management skipped. {reason_line}",
        )
        print(f"Lane '{lane}' is parked ({park_flag}); nothing to do. Delete the flag to unpark.")
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
    from price.profit_protection import ProfitPolicy
    profit_policy = ProfitPolicy(
        take_profit_r=(args.take_profit_r if args.take_profit_r > 0 else None),
        eod_profit_lock_r=(args.eod_profit_lock_r if args.eod_profit_lock_r > 0 else None),
        eod_lock_minutes_before_close=args.eod_lock_minutes,
        giveback_trigger_r=(args.giveback_trigger_r if args.giveback_trigger_r > 0 else None),
        max_giveback_r=args.max_giveback_r,
        apply_eod_to_crypto=args.eod_crypto,
        apply_eod_to_futures=args.eod_futures,
    )
    exit_policy = ExitPolicy(
        horizon_bars=args.exit_horizon,
        respect_r_multiple_gate=not args.no_r_gate,
        profit_policy=profit_policy,
        pure_horizon_exits=args.pure_horizon_exits,
    )
    print(f"Exit policy: horizon_bars={exit_policy.horizon_bars}, "
          f"respect_r_multiple_gate={exit_policy.respect_r_multiple_gate}, "
          f"pure_horizon_exits={exit_policy.pure_horizon_exits}, "
          f"max_adverse_fill_bps={args.max_adverse_fill_bps}, "
          f"adverse_atr_mult={args.adverse_atr_mult}, "
          f"entry_premium_bps={args.entry_premium_bps}, "
          f"entry_premium_atr_mult={args.entry_premium_atr_mult}, "
          f"profit_policy={profit_policy}")
    print(f"Cost model: {cost_model.to_dict()}")

    
    def _one_pass() -> Dict[str, int]:
        pass_started = time.monotonic()
        book_size = _monitored_book_size()
        # Reconcile submission-time journal rows with Alpaca before reading
        # exposure, exit context, or risk state. This is read-only and never
        # places/cancels/replaces orders, but it prevents accepted/pending/
        # expired entries from masquerading as fills.

        _revalidate_pending_entries(
            dry_run=args.dry_run, 
            max_adverse_fill_bps=args.max_adverse_fill_bps,
            adverse_atr_mult=args.adverse_atr_mult
        )

        reconciliation_health = {"ok": True, "total_order_ids": 0, "unresolved_order_ids": []}
        try:
            from price.trading import reconcile_trade_journal
            reconcile_trade_journal(health_out=reconciliation_health)
        except Exception as exc:  # noqa: BLE001 - entries fail closed below
            reconciliation_health.update({
                "ok": False,
                "errors": [str(exc)],
                "unresolved_order_ids": ["reconciliation_exception"],
            })
        if not reconciliation_health.get("ok", False):
            print(
                "WARNING: broker order reconciliation incomplete; "
                "new entries will be blocked: "
                f"{reconciliation_health}"
            )

        try:
            signals = scan_all_slices(
                limits=limits, dry_run=args.dry_run, exit_policy=exit_policy,
                cost_model=cost_model, regime_filter_enabled=args.regime_filter,
                entry_sync_blocked=not reconciliation_health.get("ok", False),
                reconciliation_health=reconciliation_health,
            )
            counts = _handle_signals(signals, dry_run=args.dry_run, max_adverse_fill_bps=args.max_adverse_fill_bps, entry_premium_bps=args.entry_premium_bps, adverse_atr_mult=args.adverse_atr_mult, entry_premium_atr_mult=args.entry_premium_atr_mult)
        except Exception as scan_exc:
            # Loud failure is the design — but a crashed pass must still
            # leave its heartbeat row so the ledger can tell crashed apart
            # from quiet.
            _emit_scan_summary(
                "scan_failed", pass_started, lane, book_size=book_size,
                note=f"{type(scan_exc).__name__}: {scan_exc}",
            )
            raise
        print("\n=== pass summary ===")
        for k, v in counts.items():
            print(f"  {k}: {v}")
        _emit_scan_summary(
            "scan_complete", pass_started, lane,
            book_size=book_size, signals_total=len(signals),
            **counts,
        )
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


def _revalidate_pending_entries(dry_run: bool, max_adverse_fill_bps: float, adverse_atr_mult: float) -> None:
    """Re-evaluate resting entry limit orders against the latest live quote.
    If the market has gapped down beyond the dynamic threshold since the order
    was placed, cancel it before it fills (e.g. overnight gaps).
    """
    if max_adverse_fill_bps <= 0 and adverse_atr_mult <= 0:
        return
        
    from price.trading import get_open_orders, cancel_order, get_latest_price, is_stale_entry
    open_orders_df = get_open_orders()
    if open_orders_df is None or open_orders_df.empty:
        return

    entry_prefix = f"price-{_get_lane()}-"
    required_cols = {"client_order_id", "order_id", "limit_price", "type"}
    missing_cols = sorted(required_cols - set(open_orders_df.columns))
    if missing_cols:
        # Never let orders-frame schema drift kill the whole scan pass. The
        # first live execution of this path (Actions run 31193346631,
        # 2026-08-07) died here on KeyError('client_order_id'); skipping only
        # THIS guard keeps stop management, exits and entries alive.
        print(f"WARNING: open-orders frame missing columns {missing_cols}; "
              "skipping stale-entry revalidation this pass")
        return

    # Resting ENTRY orders carry submit_entry's id scheme
    # price-{lane}-{symbol}-{timeframe}-{side}-{hash8}; nothing else writes it.
    # Protective stops are submitted with no client id (broker-assigned UUID),
    # and the type guard excludes them even so -- this pass must never cancel
    # a position's only protection, nor another lane's resting entries. (The
    # original c83777f filter matched literal "ext_", a substring no code path
    # ever produced: it would have matched zero rows forever.)
    entry_orders = open_orders_df[
        open_orders_df["client_order_id"].astype(str).str.startswith(entry_prefix)
        & (open_orders_df["type"].astype(str) == "limit")
    ]
    if entry_orders.empty:
        return
        
    for _, row in entry_orders.iterrows():
        symbol = str(row.get("symbol", "")).upper()
        if not symbol: continue
        
        # The broker stores no signal close; the resting limit price is the
        # safe proxy for the staleness math (signal close plus premium).
        # But broker orders don't carry this. We can try to reconstruct from
        # warehouse's latest close, but that's what is_stale_entry does inherently
        # if we supply the signal_close. Wait, how do we get signal_close?
        # The limit price is roughly the signal close (plus premium). 
        # Using limit_price is a safe proxy for signal_close.
        
        # BUT WAIT, the patch says "Every open entry LIMIT order is re-tested against 
        # the live quote at run start with the identical is_stale_entry math; stale 
        # ones are canceled."
        
        order_id = str(row.get("order_id", ""))
        limit_price = pd.to_numeric(row.get("limit_price"), errors="coerce")
        side = str(row.get("side", "buy")).lower()
        if pd.isna(limit_price):
            continue
            
        _live = get_latest_price(symbol)
        if _live is None:
            continue
            
        # Re-resolve the threshold. NOTE: compute_atr_14 lives in price.sizing
        # (imported by monitor.py and stop_manager.py); the original c83777f
        # version of this block imported it from price.features, which has no
        # such attribute -- a fifth latent defect this path carried unseen
        # until its first live execution.
        from price.warehouse import load_from_warehouse
        from price.sizing import compute_atr_14
        df = load_from_warehouse(symbol, "1d")
        _atr = compute_atr_14(df)
        
        # resolve_adverse_threshold_bps is already imported at module top from
        # price.trading (same import list used by the scan path at line ~266).
        # The original c83777f block imported it from price.risk_limits, which
        # has no such attribute -- the sixth latent defect in this path.
        _adverse_bps = resolve_adverse_threshold_bps(_atr, limit_price, adverse_atr_mult, max_adverse_fill_bps)
        if not _adverse_bps or _adverse_bps <= 0:
            continue
            
        _stale, _gap = is_stale_entry(side, limit_price, _live, _adverse_bps)
        if _stale:
            _append_audit({
                "action": "would_cancel_stale_entry_order" if dry_run else "cancel_stale_entry_order",
                "symbol": symbol,
                "reason": "stale_pending_entry_adverse_gap",
                "blocked_reasons": f"resting limit for {symbol} gapped by {_gap:.0f} bps, threshold {_adverse_bps:.0f}",
                "order_id": order_id,
            })
            if not dry_run:
                try:
                    cancel_order(order_id)
                    print(f"  [CANCEL] {symbol} resting entry order {order_id} (gap: {_gap:.0f} bps)")
                except Exception as e:
                    print(f"  [CANCEL FAILED] {symbol} order {order_id}: {e}")



if __name__ == "__main__":
    sys.exit(main())

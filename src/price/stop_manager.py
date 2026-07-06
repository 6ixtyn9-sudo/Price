"""Reconciliation layer: attaches, ratchets, and tears down REAL broker-side
protective stops for every open position.

This is the orchestration glue between the pure R-state logic in stops.py
and the broker calls in trading.py. It is deliberately the ONLY place
that combines them, so paper_trade.py has one call to make per scan:

    reconcile_stops(open_positions_df, limits)

What it does, per open position, every time it runs:
  1. No tracked StopState yet (a freshly-filled entry) -> compute ATR,
     compute the initial k*ATR stop, submit a REAL resting stop order at
     the broker, and persist the StopState. This is what makes "small
     losses" real: the stop exists at the broker from the first scan
     after fill, not only when our code happens to be watching.
  2. Tracked StopState exists -> recompute the ratchet (breakeven at
     +1R, then chandelier trailing) from the current price/ATR. If the
     desired stop improved, REPLACE the resting broker order (same
     order_id, so the position is never briefly unprotected) and persist
     the updated state.
  3. A tracked StopState exists but the position is GONE (broker stopped
     it out, or it was closed by another exit policy) -> reconcile the
     bookkeeping: remove the StopState, and if the price action is
     consistent with a stop-out, record it in the whipsaw journal.

Graceful degradation (safety property, mirrors every other lever):
  - No ATR available (thin warehouse data) -> no stop is created for that
    position this scan; it is retried next scan. Never silently leaves a
    position that COULD be protected unprotected without saying so in the
    audit trail.
  - `dry_run=True` -> computes and returns every intent, places NO broker
    orders, persists NO state. Mirrors paper_trade.py's existing --dry-run
    contract for entries/exits.
  - Any single symbol's reconciliation raising is caught and logged as an
    audit row with action='error'; it never aborts the whole scan.
"""

from typing import Dict, List, Optional

import pandas as pd

from price.sizing import compute_atr_14
from price.stops import (
    StopState,
    load_stop_states,
    new_stop_state,
    record_stopout,
    remove_stop_state,
    save_stop_states,
    update_trailing_stop,
)
from price.warehouse import load_from_warehouse


def _journal_autonomous_stopout(symbol: str, state: StopState, get_order_fill_info_fn,
                                 append_synthetic_exit_fn) -> Optional[dict]:
    """When a tracked stop's position vanished, determine whether the
    RESTING STOP ITSELF actually filled (a real, autonomous broker-side
    stop-out -- the primary reason this whole system exists), and if so,
    journal a synthetic 'exit' row carrying the REAL fill price/qty/entry
    price so the daily-loss kill switch and P&L attribution finally see it.

    CRITICAL FIX: before this, an autonomous stop fill was NEVER journaled
    anywhere -- get_today_realized_pnl (the account-level kill switch) and
    attribution.reconstruct_round_trips are both sourced exclusively from
    trade_journal.csv, and nothing wrote a row for a fill that Alpaca
    executed on its own via a resting order this code placed.

    Deliberately does NOT journal if the stop's own order is NOT 'filled'
    (e.g. it is 'canceled' -- meaning something else, like close_position
    via a state-break/horizon exit, closed the position and canceled the
    stop first). close_position already journals its own correct exit row
    in that case; journaling here too would DOUBLE-COUNT the P&L.

    Returns the fill info dict for audit purposes, or None if nothing was
    journaled (no order id tracked, fetch failed, or the stop did not
    actually fill).
    """
    if not state.stop_order_id:
        return None
    fill_info = get_order_fill_info_fn(state.stop_order_id)
    if fill_info.get("error") or fill_info.get("status") != "filled":
        return None

    filled_qty = fill_info.get("filled_qty") or abs(state.qty)
    filled_price = fill_info.get("filled_avg_price")
    if filled_price is None:
        return None

    append_synthetic_exit_fn({
        "order_id": state.stop_order_id,
        "symbol": symbol,
        "side": "close",
        "status": "filled",
        "qty": filled_qty,
        "avg_entry_price": state.entry_price,
        "current_price": filled_price,
        "submitted_at": fill_info.get("filled_at", ""),
    })
    return fill_info


def _position_timeframe(symbol: str, entry_context: Dict[str, dict]) -> str:
    ctx = entry_context.get(symbol.upper(), {})
    return ctx.get("timeframe") or "1d"


def _resolve_atr_for_symbol(symbol: str, timeframe: str) -> Optional[float]:
    try:
        df = load_from_warehouse(symbol, timeframe)
        return compute_atr_14(df)
    except Exception:  # noqa: BLE001 - stop management must never crash the scan
        return None


def _adopt_existing_broker_stop(symbol: str, side: str, qty: float, entry_price: float,
                                 broker_order) -> StopState:
    """Build a StopState from a resting stop order the broker already has,
    when local tracking (stop_state.json) shows nothing for this symbol.

    r_per_share is reconstructed from the broker's own stop_price relative
    to entry_price (NOT recomputed from current ATR), because the order
    already resting at the broker IS the ground truth for what R this
    trade is actually risking -- recomputing it from today's ATR could
    silently change R after the fact. stage is conservatively set to
    "initial" (assume no ratchet has happened yet); the next scan's normal
    ratchet logic will correctly advance it forward from the current price
    if it has, in fact, already earned a breakeven/trailing stage.
    """
    raw_stop_price = broker_order.get("stop_price")
    try:
        stop_price = float(raw_stop_price) if raw_stop_price is not None else None
    except (TypeError, ValueError):
        stop_price = None
    if stop_price is None or stop_price != stop_price:
        # Defensive: a stop order with no stop_price is malformed. Pandas
        # Series commonly turns None into NaN, so check both None and NaN.
        # Fall back to treating entry_price as if there's zero cushion (R=0),
        # which correctly makes current_risk_dollars()/unrealized_r_
        # multiple() degenerate-safe rather than raising.
        stop_price = entry_price
    r_per_share = abs(entry_price - stop_price)
    return StopState(
        symbol=symbol.upper(),
        side=side,
        qty=qty,
        entry_price=entry_price,
        initial_stop_price=stop_price,
        current_stop_price=stop_price,
        r_per_share=r_per_share,
        stage="initial",
        extreme_price=entry_price,
        stop_order_id=broker_order.get("order_id"),
    )


def _force_close_intent(symbol: str, close_position_fn, reason: str) -> dict:
    """Force-close `symbol` under the leverage safety rule and return the
    correctly-labeled audit intent.

    CRITICAL: close_position_fn can itself fail (broker rejects the close,
    or an exception is raised) -- this must NEVER be silently reported as
    the same 'force_closed_unprotected' label as a genuine success. A
    close failure here means the position is STILL OPEN and STILL
    UNPROTECTED (worse than before: the caller already knows the stop
    could not be attached), so it is surfaced as a distinct
    'force_close_failed' action precisely so monitoring/alerting can
    treat it differently from a routine successful de-risking.
    """
    close_result = close_position_fn(symbol)
    close_status = close_result.get("status")
    if close_status == "rejected" or close_status is None:
        return {
            "action": "force_close_failed",
            "symbol": symbol,
            "reason": f"{reason}; ATTEMPTED to force-close but the close itself "
                      f"FAILED (status={close_status!r}, error="
                      f"{close_result.get('error', 'unknown')!r}) -- position is "
                      "STILL OPEN AND UNPROTECTED. Requires immediate operator "
                      "attention.",
            "close_order_id": close_result.get("order_id"),
            "close_status": close_status,
        }
    return {
        "action": "force_closed_unprotected",
        "symbol": symbol,
        "reason": reason,
        "close_order_id": close_result.get("order_id"),
        "close_status": close_status,
    }


def reconcile_stops(
    open_positions: pd.DataFrame,
    limits,
    entry_context: Optional[Dict[str, dict]] = None,
    dry_run: bool = False,
    submit_protective_stop_fn=None,
    replace_protective_stop_fn=None,
    close_position_fn=None,
    get_order_fill_info_fn=None,
    append_synthetic_exit_fn=None,
    get_orders_for_symbol_fn=None,
    stop_state_path=None,
    stopout_journal_path=None,
) -> List[dict]:
    """Attach/ratchet/tear-down real broker-side stops for every open
    position. Returns a list of audit-log-ready dicts, one per action
    taken (or considered, in dry-run mode).

    `submit_protective_stop_fn` / `replace_protective_stop_fn` /
    `close_position_fn` / `get_order_fill_info_fn` / `append_synthetic_
    exit_fn` are injectable so tests never need real broker credentials;
    they default to price.trading's real implementations.
    `stop_state_path` / `stopout_journal_path` are injectable so tests
    never touch the real localdata/ files.

    Leverage safety rule: when limits.target_leverage_multiple > 1.0, a
    position that CANNOT get a protective stop attached this scan (no ATR
    data, or the broker rejected the stop order) is force-closed
    immediately rather than retried next scan. An unprotected position is
    tolerable at 1x (small, cash-secured, retried quickly); under
    leverage the same gap is materially more dangerous, so the safer
    default is to not hold unprotected leveraged exposure at all.
    """
    if submit_protective_stop_fn is None or replace_protective_stop_fn is None:
        from price.trading import (
            replace_protective_stop as _replace,
            submit_protective_stop as _submit,
        )
        submit_protective_stop_fn = submit_protective_stop_fn or _submit
        replace_protective_stop_fn = replace_protective_stop_fn or _replace
    if close_position_fn is None:
        from price.trading import close_position as close_position_fn
    if get_order_fill_info_fn is None:
        from price.trading import get_order_fill_info as get_order_fill_info_fn
    if append_synthetic_exit_fn is None:
        from price.trading import append_synthetic_exit as append_synthetic_exit_fn
    if get_orders_for_symbol_fn is None:
        from price.trading import get_orders_for_symbol as get_orders_for_symbol_fn

    force_close_unprotected = getattr(limits, "target_leverage_multiple", 1.0) > 1.0

    if entry_context is None:
        # Default: load the same per-symbol {timeframe, entry_bar_ts, ...}
        # context the exit policy uses (price.position_manager), so a
        # position's ATR is resolved in its OWN timeframe rather than
        # defaulting to 1d. Callers that already have this context (e.g.
        # monitor.scan_all_slices, which loads it for the exit check) should
        # pass it explicitly instead of paying for a second journal read.
        try:
            from price.position_manager import _load_entry_context
            entry_context = _load_entry_context()
        except Exception:  # noqa: BLE001 - stop management must never crash the scan
            entry_context = {}

    states = load_stop_states(path=stop_state_path)
    intents: List[dict] = []

    open_symbols = set()
    if open_positions is not None and not open_positions.empty:
        open_symbols = {str(s).upper() for s in open_positions["symbol"]}

    # ---- Reconcile: tracked stop exists but position is gone -> stopped
    # out (or closed by another exit). Clean up bookkeeping either way. ----
    for symbol in list(states.keys()):
        if symbol in open_symbols:
            continue
        fill_info = None
        if not dry_run:
            fill_info = _journal_autonomous_stopout(
                symbol, states[symbol], get_order_fill_info_fn, append_synthetic_exit_fn,
            )
        intents.append({
            "action": "stop_state_cleared",
            "symbol": symbol,
            "reason": "position no longer open (stopped out or closed by another exit policy)",
            "autonomous_fill_journaled": fill_info is not None,
            "fill_price": fill_info.get("filled_avg_price") if fill_info else None,
        })
        if not dry_run:
            # Only a confirmed autonomous broker-side stop fill is a
            # stop-out. A position can also disappear because close_position()
            # exited it for a horizon/state-break and canceled the stop; those
            # must NOT increment the whipsaw circuit breaker.
            if fill_info is not None:
                record_stopout(symbol, path=stopout_journal_path)
            remove_stop_state(symbol, path=stop_state_path)

    if open_positions is None or open_positions.empty:
        return intents

    dirty = False
    for _, pos in open_positions.iterrows():
        symbol = str(pos["symbol"]).upper()
        try:
            # Defensive normalization: extract .value BEFORE str()+.lower() in
            # case a raw Alpaca PositionSide enum ever reaches this function
            # directly (rather than through trading.get_open_positions(),
            # which already normalizes it). str(PositionSide.LONG) is
            # "PositionSide.LONG", NOT "long" -- wrapping first would corrupt
            # it right back into the bug this defends against.
            raw_side = pos.get("side", "long")
            side = str(getattr(raw_side, "value", raw_side) or "long").lower()
            if side not in ("long", "short"):
                side = "long"
            qty = abs(float(pos.get("qty", 0) or 0))
            current_price = float(pos.get("current_price"))
            entry_price = float(pos.get("avg_entry_price"))
        except (TypeError, ValueError):
            intents.append({
                "action": "error", "symbol": symbol,
                "reason": "malformed position row (bad qty/price)",
            })
            continue

        if qty <= 0 or current_price != current_price or entry_price != entry_price:
            intents.append({
                "action": "error", "symbol": symbol,
                "reason": "malformed position row (non-finite price or zero qty)",
            })
            continue

        timeframe = _position_timeframe(symbol, entry_context)
        existing = states.get(symbol)

        if existing is None:
            # CRITICAL: check the BROKER for an already-resting stop order
            # on this symbol before assuming "no tracked stop" means "no
            # stop exists." local_state (stop_state.json) can be lost or
            # racing against a concurrent run while a REAL GTC stop is
            # still live at Alpaca; without this check, this branch would
            # submit a SECOND stop order, leaving the position with two
            # live protective orders (an ambiguous, partially-unprotected
            # state Alpaca's own qty_available field would flag).
            adopted = None
            try:
                broker_orders = get_orders_for_symbol_fn(symbol, status="open")
                if broker_orders is not None and not broker_orders.empty:
                    stop_orders = broker_orders[broker_orders["type"] == "stop"]
                    if not stop_orders.empty:
                        adopted = stop_orders.iloc[0]
            except Exception:  # noqa: BLE001 - reconciliation must never crash the scan
                adopted = None

            if adopted is not None:
                adopted_state = _adopt_existing_broker_stop(symbol, side, qty, entry_price, adopted)
                if not dry_run:
                    states[symbol] = adopted_state
                    dirty = True
                intents.append({
                    "action": "stop_adopted", "symbol": symbol,
                    "reason": f"found an existing resting stop order "
                              f"({adopted['order_id']}) at the broker for {symbol} with no "
                              "local tracked state -- adopting it instead of submitting a "
                              "duplicate (local state was likely lost/reset)",
                    "stop_price": round(adopted_state.current_stop_price, 4),
                    "order_id": adopted_state.stop_order_id,
                })
                continue

            atr = _resolve_atr_for_symbol(symbol, timeframe)
            if atr is None:
                if force_close_unprotected and not dry_run:
                    intents.append(_force_close_intent(
                        symbol, close_position_fn,
                        reason=f"no ATR available for {symbol} ({timeframe}); cannot compute "
                               "initial stop, and leverage is active -- closing rather than "
                               "holding an unprotected leveraged position",
                    ))
                    continue
                intents.append({
                    "action": "stop_pending", "symbol": symbol,
                    "reason": f"no ATR available for {symbol} ({timeframe}); "
                              "cannot compute initial stop this scan, will retry"
                              + (" (dry run; would force-close under leverage)" if force_close_unprotected else ""),
                })
                continue

            k_stop = getattr(limits, "stop_atr_multiple", 2.0)
            new_state = new_stop_state(symbol, side, qty, entry_price, atr, k_stop=k_stop)

            if dry_run:
                intents.append({
                    "action": "would_attach_stop", "symbol": symbol,
                    "stop_price": round(new_state.current_stop_price, 4),
                    "r_per_share": round(new_state.r_per_share, 4),
                    "r_dollars": round(new_state.initial_r_dollars, 2),
                    "atr": round(atr, 4),
                })
                continue

            result = submit_protective_stop_fn(symbol, qty, new_state.current_stop_price, side)
            if result.get("status") == "rejected":
                if force_close_unprotected:
                    intents.append(_force_close_intent(
                        symbol, close_position_fn,
                        reason=f"broker rejected the protective stop order "
                               f"({result.get('error', 'unknown broker error')}), and leverage "
                               "is active -- closing rather than holding an unprotected "
                               "leveraged position",
                    ))
                    continue
                intents.append({
                    "action": "stop_attach_failed", "symbol": symbol,
                    "reason": result.get("error", "unknown broker error"),
                })
                continue

            new_state.stop_order_id = result.get("order_id")
            states[symbol] = new_state
            dirty = True
            intents.append({
                "action": "stop_attached", "symbol": symbol,
                "stop_price": round(new_state.current_stop_price, 4),
                "r_per_share": round(new_state.r_per_share, 4),
                "r_dollars": round(new_state.initial_r_dollars, 2),
                "atr": round(atr, 4),
                "order_id": new_state.stop_order_id,
            })
            continue

        # Existing tracked stop -> ratchet.
        atr = _resolve_atr_for_symbol(symbol, timeframe)
        k_trail = getattr(limits, "trail_atr_multiple", 3.0)
        breakeven_r = getattr(limits, "breakeven_trigger_r", 1.0)
        updated = update_trailing_stop(
            existing, current_price, atr, k_trail=k_trail,
            breakeven_trigger_r=breakeven_r,
        )

        if updated.current_stop_price == existing.current_stop_price and updated.stage == existing.stage:
            intents.append({
                "action": "stop_unchanged", "symbol": symbol,
                "stop_price": round(existing.current_stop_price, 4),
                "stage": existing.stage,
                "unrealized_r": (
                    round(r, 3) if (r := existing.unrealized_r_multiple(current_price)) is not None else None
                ),
            })
            continue

        if dry_run:
            intents.append({
                "action": "would_ratchet_stop", "symbol": symbol,
                "old_stop_price": round(existing.current_stop_price, 4),
                "new_stop_price": round(updated.current_stop_price, 4),
                "stage": updated.stage,
            })
            continue

        if existing.stop_order_id:
            result = replace_protective_stop_fn(existing.stop_order_id, updated.current_stop_price)
            if result.get("status") == "rejected":
                intents.append({
                    "action": "stop_ratchet_failed", "symbol": symbol,
                    "reason": result.get("error", "unknown broker error"),
                })
                continue
            if result.get("order_id"):
                updated.stop_order_id = result["order_id"]
        else:
            # No known order id (e.g. legacy state from before this
            # feature existed) -- (re)submit a fresh resting stop.
            result = submit_protective_stop_fn(symbol, qty, updated.current_stop_price, side)
            if result.get("status") == "rejected":
                intents.append({
                    "action": "stop_ratchet_failed", "symbol": symbol,
                    "reason": result.get("error", "unknown broker error"),
                })
                continue
            updated.stop_order_id = result.get("order_id")

        states[symbol] = updated
        dirty = True
        intents.append({
            "action": "stop_ratcheted", "symbol": symbol,
            "old_stop_price": round(existing.current_stop_price, 4),
            "new_stop_price": round(updated.current_stop_price, 4),
            "stage": updated.stage,
            "order_id": updated.stop_order_id,
        })

    if dirty and not dry_run:
        save_stop_states(states, path=stop_state_path)

    return intents


def get_stop_states_snapshot() -> Dict[str, StopState]:
    """Read-only accessor for the current persisted stop states, e.g. for
    the aggregate open-risk budget check in risk_limits.check_entry."""
    return load_stop_states()
